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

import argparse
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HOME_PATH = Path(__file__).resolve().parent.parent
DEFAULT_INFO_FILE = HOME_PATH / 'rvx_version_info.xml'


def get_value(root, tag: str):
    value = root.findtext(tag)
    if value is None:
        return None
    value = value.strip()
    return None if value in ('', 'None') else value


def read_module_list(info_file: Path):
    assert info_file.is_file(), info_file
    root = ET.parse(info_file).getroot()
    module_list = []
    for element in root:
        if not element.tag.endswith('.path'):
            continue
        name = element.tag[:-len('.path')]
        path = get_value(root, element.tag)
        if not path or not is_for_this_os(name):
            continue
        url = get_value(root, f'{name}.url')
        commit = get_value(root, f'{name}.commit')
        if url:
            assert commit, f'{name}.commit is NOT found in {info_file}'
        else:
            assert not commit, f'{name}.url is NOT found in {info_file}'
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


def setup_module(name: str, path: str, url: str, commit: str):
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
        if module_path.name == 'rvx_binary':
            make('all', cwd=module_path)
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

    if module_path.name == 'rvx_binary':
        make('all', cwd=module_path)


def make(*arg_list, cwd: Path):
    print('make', ' '.join(arg_list), flush=True)
    result = subprocess.run(['make', '--no-print-directory', *arg_list], cwd=cwd)
    if result.returncode != 0:
        sys.exit(result.returncode)


def get_os_suffix():
    return {'Windows': '_windows', 'Linux': '_linux'}.get(platform.system(), '')


def select_module(module_list: list, name: str):
    module_dict = {module[0]: module for module in module_list}
    for candidate in (f'{name}{get_os_suffix()}', name):
        if candidate in module_dict:
            return module_dict[candidate]
    assert 0, f'{name} is NOT found for this OS'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Set up modules listed in rvx_version_info.xml')
    parser.add_argument('-name', '-n', nargs='+', required=True, help='module name list')
    parser.add_argument('-info', '-i', default=str(DEFAULT_INFO_FILE), help='version info file')
    args = parser.parse_args()

    module_list = read_module_list(Path(args.info).absolute().resolve())
    for name in args.name:
        setup_module(*select_module(module_list, name))
