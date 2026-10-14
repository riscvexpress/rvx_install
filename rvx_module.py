# ****************************************************************************
# ****************************************************************************
# Copyright SoC Design Research Group, All rights reserved.
# Electronics and Telecommunications Research Institute (ETRI)
##
# THESE DOCUMENTS CONTAIN CONFIDENTIAL INFORMATION AND KNOWLEDGE
# WHICH IS THE PROPERTY OF ETRI. NO PART OF THIS PUBLICATION IS
# TO BE USED FOR ANY OTHER PURPOSE, AND THESE ARE NOT TO BE
# REPRODUCED, COPIED, DISCLOSED, TRANSMITTED, STORED IN A RETRIEVAL
# SYSTEM OR TRANSLATED INTO ANY OTHER HUMAN OR COMPUTER LANGUAGE,
# IN ANY FORM, BY ANY MEANS, IN WHOLE OR IN PART, WITHOUT THE
# COMPLETE PRIOR WRITTEN PERMISSION OF ETRI.
# ****************************************************************************
# ****************************************************************************

import os
import platform
import re
import shutil
import ssl
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HOME_PATH = Path(__file__).resolve().parent.parent
CONFIG_PATH_FILE = HOME_PATH / 'rvx_config_path.mh'
# Makefile and rvx_init.mh refer to ./rvx_install and ./rvx_util directly, so they can NOT be anywhere else
FIXED_NAME_SET = {'rvx_install', 'rvx_util'}
# a standalone python (e.g. by uv) looks for /etc/ssl/cert.pem, which RHEL does NOT have
SYSTEM_CA_FILE_LIST = ('/etc/pki/tls/certs/ca-bundle.crt', '/etc/ssl/certs/ca-certificates.crt', '/etc/ssl/cert.pem')


def find_ca_file():
    if os.environ.get('SSL_CERT_FILE') or ssl.get_default_verify_paths().cafile:
        return None
    for x in SYSTEM_CA_FILE_LIST:
        if os.path.isfile(x):
            return x
    return None


# applied here, so that downloads by subprocesses (e.g. make all in rvx_binary) verify certificates
SSL_CERT_FILE = find_ca_file()
if SSL_CERT_FILE:
    os.environ['SSL_CERT_FILE'] = SSL_CERT_FILE


def get_value(root, tag: str):
    value = root.findtext(tag)
    if value is None:
        return None
    value = value.strip()
    return None if value in ('', 'None') else value


def strip_os_suffix(name: str):
    return re.sub(r'_(windows|linux)$', '', name)


def get_path_var(name: str):
    return f'{strip_os_suffix(name).upper()}_HOME'


def get_env_path(name: str):
    # rvx_install and rvx_util can NOT be moved by environment variables
    if strip_os_suffix(name) in FIXED_NAME_SET:
        return None
    return os.environ.get(get_path_var(name)) or None


def get_declared_name_list(root):
    # <name/> or <name.key>, in the order of declaration
    name_list = []
    for element in root:
        name = element.tag.split('.', 1)[0]
        if name not in name_list:
            name_list.append(name)
    return name_list


def read_module_list(info_file: Path):
    assert info_file.is_file(), info_file
    root = ET.parse(info_file).getroot()
    module_list = []
    for name in get_declared_name_list(root):
        if not is_for_this_os(name):
            continue
        url = get_value(root, f'{name}.url')
        commit = get_value(root, f'{name}.commit')
        # .path is optional: a module with url is in the directory named without the OS suffix
        path = get_value(root, f'{name}.path')
        if strip_os_suffix(name) in FIXED_NAME_SET:
            assert not path or path.strip('/') == strip_os_suffix(name), f'{name} has to be in {strip_os_suffix(name)}, NOT {path}'
        if url:
            assert commit, f'{name}.commit is NOT found in {info_file}'
            path = path or strip_os_suffix(name)
        else:
            assert not commit, f'{name}.url is NOT found in {info_file}'
        if path:
            module_list.append((name, path, url, commit))
    return module_list


def is_for_this_os(name: str):
    if name.endswith('_windows'):
        return platform.system() == 'Windows'
    if name.endswith('_linux'):
        return platform.system() == 'Linux'
    return True


def git(*arg_list, cwd: Path = None):
    print('git', ' '.join(arg_list), flush=True)
    result = subprocess.run(['git', *arg_list], cwd=cwd)
    if result.returncode != 0:
        sys.exit(result.returncode)


def git_output(*arg_list, cwd: Path):
    result = subprocess.run(['git', *arg_list], cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, universal_newlines=True)
    return result.stdout.strip() if result.returncode == 0 else ''


def normalize_url(url: str):
    url = url.strip().lower().rstrip('/')
    if url.endswith('.git'):
        url = url[:-len('.git')]
    url = re.sub(r'^[a-z+]+://', '', url)
    url = re.sub(r'^[^@/]+@', '', url)
    return url.replace(':', '/', 1)


def _remove_readonly(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_path(path: Path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif os.name == 'nt' and os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        os.rmdir(path)
    else:
        shutil.rmtree(path, onerror=_remove_readonly)


def has_commit(commit: str, cwd: Path):
    return bool(git_output('rev-parse', '--verify', '--quiet', f'{commit}^{{commit}}', cwd=cwd))


def is_ancestor(ancestor: str, descendant: str, cwd: Path):
    return subprocess.run(['git', 'merge-base', '--is-ancestor', ancestor, descendant], cwd=cwd).returncode == 0


def get_default_branch(cwd: Path):
    # origin/HEAD is recorded by git clone; otherwise ask the remote
    branch = git_output('symbolic-ref', '--short', 'refs/remotes/origin/HEAD', cwd=cwd)
    if not branch:
        git_output('remote', 'set-head', 'origin', '--auto', cwd=cwd)
        branch = git_output('symbolic-ref', '--short', 'refs/remotes/origin/HEAD', cwd=cwd)
    return branch[len('origin/'):] if branch.startswith('origin/') else None


def is_branch(commit: str):
    return not re.fullmatch(r'[0-9a-fA-F]{4,40}', commit)


def setup_branch(module_path: Path, url: str, branch: str):
    if not os.path.lexists(module_path):
        git('clone', '-b', branch, url, str(module_path))
        return
    git('fetch', 'origin', cwd=module_path)
    remote_branch = f'origin/{branch}'
    assert has_commit(remote_branch, module_path), f'{branch} is NOT found in {url}'
    if git_output('status', '--porcelain', cwd=module_path):
        print('Skipped: there are uncommitted changes')
        return
    if git_output('branch', '--show-current', cwd=module_path) != branch:
        git('checkout', branch, cwd=module_path)
    head = git_output('rev-parse', 'HEAD', cwd=module_path)
    if is_ancestor(remote_branch, head, module_path):
        print('No update')
    elif is_ancestor(head, remote_branch, module_path):
        git('merge', '--ff-only', remote_branch, cwd=module_path)
    else:
        print(f'Skipped: {branch} has diverged from {remote_branch}')


def read_config_path():
    var_dict = {}
    if CONFIG_PATH_FILE.is_file():
        for line in CONFIG_PATH_FILE.read_text(encoding='utf8').split('\n'):
            match = re.match(r'^([\w-]+_HOME)\s*\??=\s*(\S.*?)\s*$', line)
            if match:
                var_dict[match.group(1)] = match.group(2)
    return var_dict


def make_binary():
    # local or not, rvx_binary has to extract its archives where rvx_config_path.mh says
    binary_path = read_config_path().get('RVX_BINARY_HOME')
    if not binary_path or not os.path.isdir(binary_path):
        print(f'[WARNING] RVX_BINARY_HOME is NOT found in {CONFIG_PATH_FILE}')
        return
    make('all', cwd=Path(binary_path))


def setup_module(name: str, path: str, url: str, commit: str):
    # an environment variable comes first, as make path does: the module there is NOT touched
    env_path = get_env_path(name)
    if env_path:
        print(f'## {name} (env: {get_path_var(name)}={env_path})', flush=True)
    else:
        update_module(name, path, url, commit)
    post_setup_module(name)


def post_setup_module(name: str):
    if strip_os_suffix(name) == 'rvx_binary':
        make_binary()


def update_module(name: str, path: str, url: str, commit: str):
    module_path = HOME_PATH / path
    if not url:
        print(f'## {name} (local)', flush=True)
        if not os.path.lexists(module_path):
            print(f'[WARNING] {module_path} is NOT found')
        return
    print(f'## {name} {commit} ({url})', flush=True)

    if os.path.lexists(module_path):
        if not (module_path / '.git').exists():
            print(f'{module_path} is NOT a git repository. It is removed')
            remove_path(module_path)
        else:
            current_url = git_output('remote', 'get-url', 'origin', cwd=module_path)
            if normalize_url(current_url) != normalize_url(url):
                print(f'{module_path} is cloned from {current_url}. It is removed')
                remove_path(module_path)

    if is_branch(commit):
        setup_branch(module_path, url, commit)
        return

    is_new = not os.path.lexists(module_path)
    if is_new:
        git('clone', url, str(module_path))
    else:
        git('fetch', 'origin', cwd=module_path)
        if not has_commit(commit, module_path) or not git_output('merge-base', 'HEAD', commit, cwd=module_path):
            print(f'{commit} is NOT in the history. The repository may be recreated, so {module_path} is removed')
            remove_path(module_path)
            git('clone', url, str(module_path))
            is_new = True

    head = git_output('rev-parse', 'HEAD', cwd=module_path)
    assert has_commit(commit, module_path), f'{commit} is NOT found in {url}'
    if head.startswith(commit):
        print('No update')
    elif is_new:
        git('reset', '--hard', commit, cwd=module_path)
    elif is_ancestor(commit, head, module_path):
        print('Skipped: HEAD already contains the commit')
    elif git_output('status', '--porcelain', cwd=module_path):
        print('Skipped: there are uncommitted changes')
    elif git_output('branch', '--show-current', cwd=module_path) and is_ancestor(head, commit, module_path):
        git('merge', '--ff-only', commit, cwd=module_path)
    else:
        git('checkout', '--detach', commit, cwd=module_path)


def make(*arg_list, cwd: Path):
    print('make', ' '.join(arg_list), flush=True)
    result = subprocess.run(['make', '--no-print-directory', *arg_list], cwd=cwd)
    if result.returncode != 0:
        sys.exit(result.returncode)


def get_os_suffix():
    return {'Windows': '_windows', 'Linux': '_linux'}.get(platform.system(), '')


def find_module(module_list: list, name: str):
    module_dict = {module[0]: module for module in module_list}
    for candidate in (f'{name}{get_os_suffix()}', name):
        if candidate in module_dict:
            return module_dict[candidate]
    return None
