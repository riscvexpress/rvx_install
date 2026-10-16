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
import datetime
import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

from install import HOME_PATH, get_module_list, set_info_value
from rvx_module import FIXED_NAME_SET, get_declared_name_list, get_path_var, get_value, git_output, is_branch, normalize_url, read_config_path, read_module_list, remove_path, strip_os_suffix

INFO_FILE = 'rvx_version_info.xml'
OLD_LOCK_INFO_FILE = 'rvx_version_lock.xml'
SNAPSHOT_INFO_FILE = 'rvx_snapshot_info.xml'
GENERATED_FILE_SET = {'rvx_config_path.mh', 'rvx_config_python.mh', 'rvx_setup.sh', OLD_LOCK_INFO_FILE, SNAPSHOT_INFO_FILE}


def get_vendor_dir(name: str):
    # by convention, an rvx_ module is in the folder without rvx_ (e.g. rvx_binary -> binary)
    if name.startswith('rvx_') and name not in FIXED_NAME_SET:
        return name[len('rvx_'):]
    return name


def is_under(path: Path, parent: Path):
    return path == parent or parent in path.parents


def list_tracked_file(path: Path):
    # None when the path is NOT in a git repository
    result = subprocess.run(['git', 'ls-files', '-z', '--recurse-submodules'], cwd=path,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        return None
    return [Path(x) for x in result.stdout.decode('utf8').split('\0') if x]


def list_all_file(path: Path):
    file_list = []
    for root, dir_list, name_list in os.walk(path):
        dir_list[:] = [x for x in dir_list if x != '.git']
        for name in dir_list + [x for x in name_list if x != '.git']:
            file_path = Path(root, name)
            if file_path.is_symlink() or not file_path.is_dir():
                file_list.append(file_path.relative_to(path))
    return file_list


def copy_file_list(src_dir: Path, dst_dir: Path, file_list: list):
    copied_list = []
    for file in file_list:
        src_file = src_dir / file
        if not os.path.lexists(src_file) or (src_file.is_dir() and not src_file.is_symlink()):
            continue
        dst_file = dst_dir / file
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        if dst_file.is_symlink() or dst_file.is_file():
            dst_file.unlink()
        shutil.copy2(src_file, dst_file, follow_symlinks=False)
        copied_list.append(dst_file)
    return copied_list


def get_gitlink_dict(home_path: Path):
    # {submodule path: commit} recorded in the release repository
    result = subprocess.run(['git', 'ls-files', '-z', '-s'], cwd=home_path,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    gitlink_dict = {}
    for line in result.stdout.decode('utf8').split('\0') if result.returncode == 0 else []:
        if line.startswith('160000 '):
            info, path = line.split('\t', 1)
            gitlink_dict[path] = info.split()[1]
    return gitlink_dict


def clone_submodule(module_path: Path, dst_path: Path, target_path: Path):
    # a local clone, so that the submodule is ready without network
    rel_path = dst_path.relative_to(target_path).as_posix()
    name = rel_path
    for line in git_output('config', '-f', '.gitmodules', '--get-regexp', r'^submodule\..*\.path$', cwd=target_path).split('\n'):
        if line.endswith(f' {rel_path}'):
            name = line.split(' ', 1)[0][len('submodule.'):-len('.path')]
    # the git directory absorbed by the previous snapshot
    if (target_path / '.git' / 'modules' / name).exists():
        remove_path(target_path / '.git' / 'modules' / name)
    commit = git_output('rev-parse', 'HEAD', cwd=module_path)
    branch = git_output('branch', '--show-current', cwd=module_path)
    url = git_output('remote', 'get-url', 'origin', cwd=module_path)
    subprocess.run(['git', 'clone', '-q', '--no-checkout', str(module_path), str(dst_path)], check=True)
    if url:
        subprocess.run(['git', 'remote', 'set-url', 'origin', url], cwd=dst_path, check=True)
    subprocess.run(['git', 'checkout', '-q'] + (['-B', branch, commit] if branch else ['--detach', commit]), cwd=dst_path, check=True)
    return commit


def get_top_file_list(home_path: Path):
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=home_path,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode == 0:
        file_list = [Path(x) for x in result.stdout.decode('utf8').split('\0') if x]
    else:
        file_list = [Path(x.name) for x in home_path.iterdir() if x.is_file()]
    return [x for x in file_list if len(x.parts) == 1 and x.name not in GENERATED_FILE_SET]


def get_git_info(module_path: Path):
    if not (module_path / '.git').exists():
        return []
    is_modified = bool(git_output('status', '--porcelain', cwd=module_path))
    if is_modified:
        print(f'[WARNING] {module_path} has uncommitted changes: they are included and recorded as modified')
    return [('url', git_output('remote', 'get-url', 'origin', cwd=module_path)),
            ('branch', git_output('branch', '--show-current', cwd=module_path)),
            ('commit', git_output('rev-parse', '--short=7', 'HEAD', cwd=module_path)),
            ('date', git_output('log', '-1', '--date=format:%Y-%m-%d-%H-%M', '--format=%cd', cwd=module_path)),
            ('modified', 'yes' if is_modified else 'no')]


def write_info_file(info_file: Path, entry_list: list):
    line_list = ['<?xml version="1.0" ?>', '<version_info>']
    for name, info_list in entry_list:
        if not info_list:
            line_list.append(f'  <{name}/>')
        for key, value in info_list:
            line_list.append(f'  <{name}.{key}>{escape(value)}</{name}.{key}>')
    line_list.append('</version_info>')
    with open(info_file, 'w', encoding='utf8', newline='\n') as f:
        f.write('\n'.join(line_list) + '\n')
    return info_file


def update_info_file(info_file: Path, old_contents: str, entry_list: list):
    root = ET.fromstring(old_contents)
    entry_dict = {strip_os_suffix(name): dict(info_list) for name, info_list in entry_list}
    contents = old_contents
    for name in get_declared_name_list(root):
        info = entry_dict.get(strip_os_suffix(name), {})
        url = get_value(root, f'{name}.url')
        commit = get_value(root, f'{name}.commit')
        if not url or not info.get('url') or normalize_url(url) != normalize_url(info['url']) or (commit and is_branch(commit)):
            continue
        contents = set_info_value(contents, name, [('url', url), ('commit', info['commit']), ('date', info['date'])])
        print(f'{name} is updated to {info["commit"]} in {info_file.name}')
    with open(info_file, 'w', encoding='utf8', newline='\n') as f:
        f.write(contents)
    return info_file


def remove_gitignore_line(gitignore_file: Path, removed_set: set):
    if not gitignore_file.is_file():
        return
    line_list = gitignore_file.read_text(encoding='utf8').rstrip('\n').split('\n')
    line_list = [x for x in line_list if x.strip().strip('/') not in removed_set]
    with open(gitignore_file, 'r+', encoding='utf8', newline='\n') as f:
        f.write('\n'.join(line_list) + '\n')
        f.truncate()


def stage(target_path: Path, file_list: list, removed_list: list, gitlink_dict: dict):
    if not (target_path / '.git').exists():
        subprocess.run(['git', 'init', '-q'], cwd=target_path, check=True)
    if removed_list:
        # unstage the removed directories so that their deleted files are staged as deletions
        pathspec = '\0'.join(x.relative_to(target_path).as_posix() for x in removed_list)
        subprocess.run(['git', 'rm', '-r', '-q', '-f', '--cached', '--ignore-unmatch', '--pathspec-from-file=-', '--pathspec-file-nul'],
                       cwd=target_path, input=pathspec.encode('utf8'), check=True)
    pathspec = '\0'.join(x.relative_to(target_path).as_posix() for x in file_list)
    subprocess.run(['git', '-c', 'core.safecrlf=false', 'add', '-f', '--pathspec-from-file=-', '--pathspec-file-nul'],
                   cwd=target_path, input=pathspec.encode('utf8'), check=True)
    for path, commit in gitlink_dict.items():
        subprocess.run(['git', 'update-index', '--add', '--cacheinfo', f'160000,{commit},{path}'], cwd=target_path, check=True)
    if gitlink_dict:
        # register the cloned submodules and move their .git into .git/modules, as git submodule update does
        subprocess.run(['git', 'submodule', 'init', '-q'] + list(gitlink_dict), cwd=target_path, check=True)
        subprocess.run(['git', 'submodule', 'absorbgitdirs'] + list(gitlink_dict), cwd=target_path, check=True)


def snapshot(target_path: Path, vendor: bool):
    home_path = Path(HOME_PATH)
    module_list = []
    # the paths in rvx_config_path.mh are the current configuration (environment variables come first)
    for var, path in read_config_path().items():
        os.environ.setdefault(var, path)
    for name, path, source, xml_path in get_module_list():
        # a snapshot is taken only as rvx_version_info.xml declares
        if not xml_path:
            print(f'[WARNING] {name} has no path in {INFO_FILE}. It is NOT included')
            continue
        if source == 'env':
            assert os.path.realpath(path) == os.path.realpath(xml_path), \
                f'{name} is in {path} by {get_path_var(name)}, but {xml_path} in {INFO_FILE}'
        if path and Path(path).is_dir():
            module_list.append((name, Path(path)))
        else:
            print(f'[WARNING] {name} is NOT found. It is NOT included')
    assert not is_under(home_path, target_path), f'{target_path} contains {home_path}'
    for name, module_path in module_list:
        assert not is_under(target_path, module_path), f'{target_path} is in {name} ({module_path})'
    assert not target_path.exists() or target_path.is_dir(), f'{target_path} is NOT a directory'
    target_path.mkdir(parents=True, exist_ok=True)
    old_info_contents = None
    if (target_path / SNAPSHOT_INFO_FILE).is_file() and (target_path / INFO_FILE).is_file():
        old_info_contents = (target_path / INFO_FILE).read_text(encoding='utf8')

    staged_list = copy_file_list(home_path, target_path, get_top_file_list(home_path))
    # without --vendor, a module with url or a submodule is NOT a part of the release repository
    url_name_set = {strip_os_suffix(x[0]) for x in read_module_list(home_path / INFO_FILE) if x[2]}
    source_gitlink_dict = {} if vendor else get_gitlink_dict(home_path)
    gitlink_dict = {}
    snapshot_entry_list = [('snapshot', [('taken', datetime.datetime.now().strftime('%Y-%m-%d-%H-%M')), ('source', home_path.as_posix())] + get_git_info(home_path))]
    removed_list = []
    copied_name_list = []
    copied_dir_list = []
    rvx_path_list = [x for name, x in module_list if name.startswith('rvx_')]
    for name, module_path in module_list:
        # an rvx_ module is copied as a whole, so a non-rvx_ module in it is copied with it
        if not name.startswith('rvx_') and any(is_under(module_path, x) for x in rvx_path_list):
            continue
        if vendor:
            dst_path = target_path / get_vendor_dir(name)
        else:
            if any(x != module_path and is_under(module_path, x) for _, x in module_list):
                continue
            if is_under(module_path, home_path):
                dst_path = target_path / module_path.relative_to(home_path)
            else:
                dst_path = target_path / name
                print(f'[WARNING] {module_path} is outside {home_path}. It is copied to {dst_path}')
        print(f'## {name}: {module_path} -> {dst_path}', flush=True)
        if os.path.lexists(dst_path):
            remove_path(dst_path)
            removed_list.append(dst_path)
        rel_path = dst_path.relative_to(target_path).as_posix()
        is_submodule = not vendor and rel_path in source_gitlink_dict
        if is_submodule:
            commit = clone_submodule(module_path, dst_path, target_path)
            if commit != source_gitlink_dict[rel_path]:
                print(f'[WARNING] {name} is at {commit[:7]}, NOT {source_gitlink_dict[rel_path][:7]} recorded in {home_path}')
        # everything is copied (over the clone of a submodule, to keep its uncommitted changes), but only the tracked files are staged
        copied_list = copy_file_list(module_path, dst_path, list_all_file(module_path))
        tracked_list = list_tracked_file(module_path)
        if is_submodule:
            print(f'{name} is a submodule: only its commit is staged')
            gitlink_dict[rel_path] = commit
        elif not vendor and name in url_name_set:
            print(f'{name} has url: it is NOT staged')
        elif tracked_list is None:
            staged_list += copied_list
        else:
            copied_set = set(copied_list)
            staged_list += [dst_path / x for x in tracked_list if dst_path / x in copied_set]
        copied_name_list.append(name)
        copied_dir_list.append(dst_path)
        source = module_path.relative_to(home_path).as_posix() if is_under(module_path, home_path) else module_path.as_posix()
        snapshot_entry_list.append((name, [('source', source)] + get_git_info(module_path)))

    if vendor:
        # a copied module is local, so it is declared with .path (except rvx_install and rvx_util)
        info_entry_list = [(name, [('path', get_vendor_dir(name))] if name in copied_name_list and name not in FIXED_NAME_SET else [])
                           for name, _ in module_list]
        staged_list.append(write_info_file(target_path / INFO_FILE, info_entry_list))
        removed_set = {get_vendor_dir(x) for x in copied_name_list}
        removed_set |= {x.relative_to(home_path).as_posix() for name, x in module_list
                        if name in copied_name_list and is_under(x, home_path)}
        remove_gitignore_line(target_path / '.gitignore', removed_set)
        # every module is a part of the snapshot, so NOT a submodule anymore
        gitmodules_list = [x for x in [target_path / '.gitmodules'] + [Path(root, '.gitmodules') for dir_path in copied_dir_list
                                                                       for root, _, name_list in os.walk(dir_path) if '.gitmodules' in name_list]
                           if os.path.lexists(x)]
        for gitmodules_file in gitmodules_list:
            print(f'{gitmodules_file.relative_to(target_path).as_posix()} is removed')
            gitmodules_file.unlink()
        staged_list = [x for x in staged_list if x not in gitmodules_list]
        removed_list += gitmodules_list
        # the submodules registered by a previous snapshot without --vendor
        if (target_path / '.git' / 'modules').is_dir():
            remove_path(target_path / '.git' / 'modules')
        for section in sorted({x.split(' ', 1)[0].rsplit('.', 1)[0] for x in
                               git_output('config', '--local', '--get-regexp', r'^submodule\.', cwd=target_path).split('\n') if x}):
            subprocess.run(['git', 'config', '--local', '--remove-section', section], cwd=target_path, check=True)
    if os.path.lexists(target_path / OLD_LOCK_INFO_FILE):
        remove_path(target_path / OLD_LOCK_INFO_FILE)
        removed_list.append(target_path / OLD_LOCK_INFO_FILE)
    staged_list.append(write_info_file(target_path / SNAPSHOT_INFO_FILE, snapshot_entry_list))
    if old_info_contents is not None:
        staged_list.append(update_info_file(target_path / INFO_FILE, old_info_contents, snapshot_entry_list))
    stage(target_path, sorted(set(staged_list)), removed_list, gitlink_dict)
    print(f'snapshot is taken in {target_path} (staged, NOT committed)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Take a snapshot of the modules listed in rvx_version_info.xml')
    parser.add_argument('target', help='target directory')
    parser.add_argument('--vendor', action='store_true', help='put every module in target/<name without rvx_> (except rvx_install and rvx_util) ')
    args = parser.parse_args()
    snapshot(Path(args.target).absolute(), args.vendor)
