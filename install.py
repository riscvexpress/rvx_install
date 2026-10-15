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
import filecmp
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import xml.etree.ElementTree as ET

from rvx_module import CONFIG_PATH_FILE, FIXED_NAME_SET, find_module, get_declared_name_list, get_default_branch, get_env_path, get_path_var, get_value, is_branch, is_for_this_os, post_setup_module, read_config_path, read_module_list, remove_path, setup_module, SSL_CERT_FILE, strip_os_suffix, git_output

MIN_PYTHON_VERSION = (3, 8)
MIN_MAKE_VERSION = (3, 81)


def is_windows():
    return platform.system() == 'Windows'


def run(cmd_list):
    try:
        result = subprocess.run(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def parse_version(text):
    match = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', text or '')
    if not match:
        return None
    return tuple(int(x) for x in match.groups() if x is not None)


def version_str(version):
    return '.'.join(str(x) for x in version)


class Checker:
    def __init__(self):
        self.error_count = 0
        self.warning_count = 0

    def ok(self, name, msg):
        print('[ OK ] {0}: {1}'.format(name, msg))

    def warning(self, name, msg):
        self.warning_count += 1
        print('[WARN] {0}: {1}'.format(name, msg))

    def error(self, name, msg):
        self.error_count += 1
        print('[FAIL] {0}: {1}'.format(name, msg))

    def check_tool(self, name, is_required=True):
        path = shutil.which(name)
        if path:
            self.ok(name, path)
        elif is_required:
            self.error(name, 'NOT found in PATH')
        else:
            self.warning(name, 'NOT found in PATH')
        return path

    def summary(self):
        print('')
        print('{0} error(s), {1} warning(s)'.format(
            self.error_count, self.warning_count))
        return 1 if self.error_count else 0

#############
# check_env #
#############


def check_python(checker):
    version = sys.version_info[:3]
    if version >= MIN_PYTHON_VERSION:
        checker.ok('python', '{0} ({1})'.format(
            version_str(version), sys.executable))
    else:
        checker.error('python', '{0} < {1} ({2})'.format(version_str(
            version), version_str(MIN_PYTHON_VERSION), sys.executable))
    if sys.prefix != sys.base_prefix:
        checker.ok('venv', sys.prefix)


def check_pip(checker):
    output = run([sys.executable, '-m', 'pip', '--version'])
    if not output:
        checker.error('pip', "NOT installed, run 'make install_pip'")
        return False
    checker.ok('pip', output.split(' from ')[0])
    return True


def cmd_check_env(checker):
    check_python(checker)
    check_pip(checker)
    checker.check_tool('git')
    if not is_windows():
        if shutil.which('curl') or shutil.which('wget'):
            checker.check_tool('curl' if shutil.which('curl') else 'wget')
        else:
            checker.error('curl or wget', 'NOT found in PATH')
    checker.check_tool('uv', is_required=False)

#######################
# install_pip_package #
#######################

PIP_PACKAGE_LIST = ['distro', 'pyelftools']


def cmd_install_pip_package():
    cmd_list = [sys.executable, '-m', 'pip', 'install', '--upgrade'] + PIP_PACKAGE_LIST
    print(' '.join(cmd_list))
    sys.stdout.flush()
    return subprocess.call(cmd_list)

##############
# check_make #
##############


def find_all_in_path(name):
    name_list = [name]
    if is_windows():
        name_list = [name + ext.lower()
                     for ext in os.environ.get('PATHEXT', '.EXE').split(os.pathsep)]
    found_list = []
    for path_dir in os.environ.get('PATH', '').split(os.pathsep):
        for each_name in name_list:
            path = os.path.join(path_dir, each_name)
            if os.path.isfile(path) and os.path.normcase(os.path.realpath(path)) not in [os.path.normcase(os.path.realpath(x)) for x in found_list]:
                found_list.append(path)
    return found_list


def cmd_check_make(checker):
    make_list = find_all_in_path('make')
    if not make_list:
        checker.error('make', 'NOT found in PATH')
        return
    output = run([make_list[0], '--version'])
    version = parse_version(output.splitlines()[0] if output else None)
    if is_windows() and 'gnuwin32' in make_list[0].lower():
        checker.error(
            'make', 'GnuWin32 make is NOT supported ({0})'.format(make_list[0]))
        if any('scoop' in x.lower() for x in make_list[1:]):
            print('       remove {0} from PATH to use make installed by scoop'.format(
                os.path.dirname(make_list[0])))
        else:
            print("       run 'scoop install make' and remove {0} from PATH".format(
                os.path.dirname(make_list[0])))
    elif not version:
        checker.error('make', 'version is unknown ({0})'.format(make_list[0]))
    elif version < MIN_MAKE_VERSION:
        checker.error('make', '{0} < {1} ({2})'.format(version_str(
            version), version_str(MIN_MAKE_VERSION), make_list[0]))
    else:
        checker.ok('make', '{0} ({1})'.format(
            version_str(version), make_list[0]))
    for path in make_list[1:]:
        checker.warning('make', 'also found but NOT used: {0}'.format(path))

    # make on windows runs recipes with cmd.exe unless sh.exe is in PATH
    # recipes use these tools inside sh; git's sh adds its own usr/bin to PATH
    sh_path = checker.check_tool('sh')
    if not sh_path:
        if is_windows():
            print("       'make setup_python' needs sh, run 'scoop install git'")
        return
    for tool in ('sed', 'grep', 'awk', 'tr'):
        path = run([sh_path, '-c', 'command -v ' + tool])
        if path:
            checker.ok(tool, path)
        else:
            checker.error(tool, 'NOT found in sh')

    # make runs simple recipe lines (e.g. echo) directly without sh,
    # so they must be in the PATH given by make (rvx_init.mh adds git's usr/bin on windows)
    if 'MAKELEVEL' in os.environ:
        for tool in ('echo', 'bash'):
            path = shutil.which(tool)
            if path:
                checker.ok(tool, path)
            else:
                checker.error(tool, 'NOT found in PATH given by make')
                if is_windows():
                    print("       run 'scoop install git' so that make can find sh")

############
# cmd_list #
############


CMD_DICT = {}


def cmd_cmd_list():
    width = max(len(x) for x in CMD_DICT)
    for name, (_, description) in CMD_DICT.items():
        print('make {0}  {1}'.format(name.ljust(width), description))
    return 0

#################
# check_install #
#################

# runs every check_* command


def cmd_check_install(checker):
    for name, (func, _) in CMD_DICT.items():
        if name.startswith('check_') and name != 'check_install':
            print('## {0}'.format(name))
            func(checker)

#########
# setup #
#########

HOME_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__))).replace('\\', '/')


def get_sh_bin_dir():
    if not is_windows():
        return ''
    output = run(['sh', '-c', 'cygpath -m /usr/bin'])
    return output if output else ''


def get_submodule_path_set():
    path_set = set()
    gitmodules_path = os.path.join(HOME_PATH, '.gitmodules')
    if not os.path.isfile(gitmodules_path):
        return path_set
    with open(gitmodules_path, encoding='utf8') as f:
        for line in f:
            match = re.match(r'^\s*path\s*=\s*(\S+)\s*$', line)
            if match:
                path_set.add(match.group(1).strip('/'))
    return path_set


def get_name_list():
    name_list = []
    url_name_set = set()
    explicit_path_dict = {}
    root = ET.parse(os.path.join(HOME_PATH, 'rvx_version_info.xml')).getroot()
    for tag_name in get_declared_name_list(root):
        if not is_for_this_os(tag_name):
            continue
        name = strip_os_suffix(tag_name)
        path = get_value(root, '{0}.path'.format(tag_name))
        if path:
            assert name not in FIXED_NAME_SET or path.strip('/') == name, '{0} has to be in {0}, NOT {1}'.format(name, path)
            explicit_path_dict[name] = path
        if get_value(root, '{0}.url'.format(tag_name)):
            url_name_set.add(name)
        if name not in name_list:
            name_list.append(name)
    return name_list, url_name_set, explicit_path_dict


def to_abs_path(path):
    return os.path.abspath(path).replace('\\', '/')


def find_xml_path(name, submodule_path_set, url_name_set, explicit_path_dict):
    # rvx_install and rvx_util have to be there: Makefile and rvx_init.mh refer to them directly
    if name in FIXED_NAME_SET:
        return to_abs_path(os.path.join(HOME_PATH, name)), 'fixed'
    # a local module has to be declared with .path
    if name in explicit_path_dict:
        return to_abs_path(os.path.join(HOME_PATH, explicit_path_dict[name])), '.path'
    # the directory named as it is, if it is a submodule or cloned from url (even before cloned)
    if name in submodule_path_set:
        return to_abs_path(os.path.join(HOME_PATH, name)), 'submodule'
    if name in url_name_set:
        return to_abs_path(os.path.join(HOME_PATH, name)), 'url'
    return None, None


def find_path(name, submodule_path_set, url_name_set, explicit_path_dict):
    xml_path, source = find_xml_path(name, submodule_path_set, url_name_set, explicit_path_dict)
    # an environment variable comes before rvx_version_info.xml, except for rvx_install and rvx_util
    env_path = get_env_path(name)
    if env_path:
        return to_abs_path(env_path), 'env'
    # a module with url is cloned there, so it is used even before cloned
    if source in ('fixed', '.path'):
        assert name in url_name_set or os.path.isdir(xml_path), '{0} is NOT found at {1} ({2})'.format(name, xml_path, source)
    return xml_path, source


def get_module_list():
    # (name, path, source, path declared in rvx_version_info.xml)
    module_list = []
    submodule_path_set = get_submodule_path_set()
    name_list, url_name_set, explicit_path_dict = get_name_list()
    for name in name_list:
        found_path, source = find_path(name, submodule_path_set, url_name_set, explicit_path_dict)
        xml_path, _ = find_xml_path(name, submodule_path_set, url_name_set, explicit_path_dict)
        module_list.append((name, found_path, source, xml_path))
    return module_list


def generate_config_path(output_file):
    line_list = []
    line_list.append('# make runs simple commands (e.g. echo, rm) without sh, so put git\'s usr/bin in PATH')
    line_list.append('ifeq ($(OS),Windows_NT)')
    line_list.append('  SH_BIN_DIR:={0}'.format(get_sh_bin_dir()))
    line_list.append('  ifneq (${SH_BIN_DIR},)')
    line_list.append('    export PATH:=${SH_BIN_DIR};${PATH}')
    line_list.append('  endif')
    line_list.append('endif')
    line_list.append('')
    var_list = []
    for name, found_path, source, _ in get_module_list():
        if found_path:
            print('{0} = {1} ({2})'.format(get_path_var(name), found_path, source))
        else:
            # a declared module is defined even if NOT found, so that its absence is explicit
            print('{0} is NOT found'.format(get_path_var(name)))
        var_list.append(get_path_var(name))
        assign = '=' if name.startswith('rvx_') else '?='
        line_list.append('{0} {1} {2}'.format(get_path_var(name), assign, found_path or '').rstrip())
    line_list.append('export {0}'.format(' '.join(var_list)))
    line_list.append('')
    if SSL_CERT_FILE:
        line_list.append('# {0} can NOT find CA certificates by itself'.format(sys.executable.replace('\\', '/')))
        line_list.append('export SSL_CERT_FILE ?= {0}'.format(SSL_CERT_FILE))
        line_list.append('')
    line_list.append('-include {0}/rvx_config_python.mh'.format(HOME_PATH))
    with open(output_file, 'w', encoding='utf8', newline='\n') as f:
        f.write('\n'.join(line_list) + '\n')
    print('generated {0}'.format(output_file))


def cmd_path():
    generate_config_path(CONFIG_PATH_FILE)
    return 0


GITIGNORE_LOCAL_MARKER = '# local'


def cmd_setup_gitignore():
    template_file = os.path.join(HOME_PATH, 'rvx_install', 'gitignore.template')
    output_file = os.path.join(HOME_PATH, '.gitignore')
    with open(template_file, encoding='utf8') as f:
        line_list = f.read().rstrip('\n').split('\n')
    if GITIGNORE_LOCAL_MARKER not in line_list:
        line_list.append(GITIGNORE_LOCAL_MARKER)
    if os.path.isfile(output_file):
        with open(output_file, encoding='utf8') as f:
            old_line_list = f.read().rstrip('\n').split('\n')
        if GITIGNORE_LOCAL_MARKER in old_line_list:
            local_line_list = old_line_list[old_line_list.index(GITIGNORE_LOCAL_MARKER) + 1:]
        else:
            local_line_list = [line for line in old_line_list if line.strip() and line not in line_list]
        line_list += local_line_list
    if 'rvx_config_path.mh' not in line_list:
        line_list.append('rvx_config_path.mh')
    for _, path, url, _ in read_module_list(Path(HOME_PATH) / 'rvx_version_info.xml'):
        # a module without url is local: it is left to the local section
        if url and path + '/' not in line_list:
            line_list.append(path + '/')
    with open(output_file, 'r+' if os.path.isfile(output_file) else 'w', encoding='utf8', newline='\n') as f:
        f.write('\n'.join(line_list) + '\n')
        f.truncate()
    print('generated {0}'.format(output_file))
    return 0


README_LOCAL_MARKER = '<!-- local -->'


def cmd_setup_readme():
    template_file = os.path.join(HOME_PATH, 'rvx_install', 'README.md.template')
    output_file = os.path.join(HOME_PATH, 'README.md')
    with open(template_file, encoding='utf8') as f:
        line_list = f.read().rstrip('\n').split('\n')
    if README_LOCAL_MARKER not in line_list:
        line_list += ['', README_LOCAL_MARKER]
    if os.path.isfile(output_file):
        with open(output_file, encoding='utf8') as f:
            old_line_list = f.read().rstrip('\n').split('\n')
        if README_LOCAL_MARKER in old_line_list:
            local_line_list = old_line_list[old_line_list.index(README_LOCAL_MARKER) + 1:]
        elif '\n'.join(old_line_list).strip() != '\n'.join(line_list[:line_list.index(README_LOCAL_MARKER)]).strip():
            local_line_list = [''] + old_line_list
        else:
            local_line_list = []
        line_list = line_list[:line_list.index(README_LOCAL_MARKER) + 1] + local_line_list
    with open(output_file, 'r+' if os.path.isfile(output_file) else 'w', encoding='utf8', newline='\n') as f:
        f.write('\n'.join(line_list) + '\n')
        f.truncate()
    print('generated {0}'.format(output_file))
    return 0


def cmd_setup_init():
    template_file = os.path.join(HOME_PATH, 'rvx_install', 'rvx_init.mh')
    output_file = os.path.join(HOME_PATH, 'rvx_init.mh')
    if os.path.isfile(output_file) and filecmp.cmp(template_file, output_file, shallow=False):
        print('{0} is up to date'.format(output_file))
        return 0
    shutil.copyfile(template_file, output_file)
    print('updated {0}'.format(output_file))
    return 0


def cmd_setup_misc():
    returncode = 0
    if is_windows():
        cmd_list = ['setx', 'RVX_RELEASE_HOME', HOME_PATH]
        print(' '.join(cmd_list))
        sys.stdout.flush()
        returncode = subprocess.call(cmd_list)
        if returncode == 0:
            print('open a new terminal to use RVX_RELEASE_HOME')
    else:
        template_file = os.path.join(HOME_PATH, 'rvx_setup.sh.template')
        if not os.path.isfile(template_file):
            return 0
        setup_file = os.path.join(HOME_PATH, 'rvx_setup.sh')
        cmd_list = [sys.executable, os.path.join(HOME_PATH, 'rvx_install', 'configure_template.py'),
                    '-i', template_file,
                    '-o', setup_file,
                    '-c', 'RVX_RELEASE_HOME={0}'.format(HOME_PATH)]
        print(' '.join(cmd_list))
        sys.stdout.flush()
        returncode = subprocess.call(cmd_list)
        if returncode == 0:
            print('source {0} to use RVX_RELEASE_HOME'.format(setup_file))
    return returncode

##############
# setup_udev #
##############

UDEV_RULES_FILE = os.path.join('rules.d', '99-rvx.rules')
UDEV_RULES_DIR = '/etc/udev/rules.d'


def cmd_setup_udev():
    if is_windows():
        return 0
    # rvx_devkit may be anywhere, as rvx_config_path.mh says
    devkit_path = read_config_path().get('RVX_DEVKIT_HOME')
    if not devkit_path or not os.path.isdir(devkit_path):
        print('[WARNING] RVX_DEVKIT_HOME is NOT found in {0}'.format(CONFIG_PATH_FILE))
        return 0
    rules_path = os.path.join(devkit_path, UDEV_RULES_FILE)
    if not os.path.isfile(rules_path):
        print('[WARNING] {0} is NOT found'.format(rules_path))
        return 0
    target_path = os.path.join(UDEV_RULES_DIR, os.path.basename(rules_path))
    if os.path.isfile(target_path) and filecmp.cmp(rules_path, target_path, shallow=False):
        print('{0} is up to date'.format(target_path))
        return 0
    if not os.path.isdir(UDEV_RULES_DIR) or not shutil.which('udevadm'):
        print('[WARNING] udev is NOT found: {0} is NOT installed'.format(rules_path))
        return 0
    sudo_list = [] if os.geteuid() == 0 else ['sudo']
    if sudo_list and not shutil.which('sudo'):
        print('[WARNING] sudo is NOT found: copy {0} to {1} as root'.format(rules_path, UDEV_RULES_DIR))
        return 0
    for cmd_list in (sudo_list + ['cp', '-f', rules_path, UDEV_RULES_DIR],
                     sudo_list + ['udevadm', 'control', '--reload-rules'],
                     sudo_list + ['udevadm', 'trigger']):
        print(' '.join(cmd_list))
        sys.stdout.flush()
        returncode = subprocess.call(cmd_list)
        if returncode:
            return returncode
    return 0

##################
# install_others #
##################


def cmd_setup_modules():
    info_file = Path(HOME_PATH) / 'rvx_version_info.xml'
    module_list = read_module_list(info_file)
    declared_set = {strip_os_suffix(x) for x in get_declared_name_list(ET.parse(info_file).getroot())}
    for name in os.environ.get('RVX_MODULE_LIST', '').split():
        module = find_module(module_list, name)
        if module:
            setup_module(*module)
        elif strip_os_suffix(name) not in declared_set:
            print(f'[WARNING] {name} in RVX_MODULE_LIST is NOT declared in {info_file.name}', flush=True)
        else:
            # declared without url (e.g. <rvx_synthesizer/>): local, found by make path
            print(f'## {name} (local)', flush=True)
            post_setup_module(name)
    return 0


def cmd_update_version_info():
    info_file = os.path.join(HOME_PATH, 'rvx_version_info.xml')
    with open(info_file, encoding='utf8') as f:
        contents = f.read()
    for name, path, url, commit in read_module_list(Path(info_file)):
        module_path = Path(HOME_PATH) / path
        # only a module with url is managed here: NOT a local one nor one given by an environment variable, as setup_modules does
        if not url or get_env_path(name) or not (module_path / '.git').exists():
            continue
        url = git_output('remote', 'get-url', 'origin', cwd=module_path)
        if git_output('status', '--porcelain', cwd=module_path):
            print('[WARNING] {0} has uncommitted changes'.format(name))
        if git_output('log', '--oneline', '@{u}..HEAD', cwd=module_path):
            print('[WARNING] {0} has unpushed commits'.format(name))
        if commit and is_branch(commit):
            print('{0} {1} (branch, skipped)'.format(name, commit))
            continue
        info_list = (('url', url),
                     ('commit', git_output('rev-parse', '--short=7', 'HEAD', cwd=module_path)),
                     ('date', git_output('log', '-1', '--date=format:%Y-%m-%d-%H-%M', '--format=%cd', cwd=module_path)))
        previous_tag = '{0}.url'.format(name)
        for key, value in info_list:
            tag = '{0}.{1}'.format(name, key)
            line = '<{0}>{1}</{0}>'.format(tag, value)
            if re.search(r'<{0}>[^<]*</{0}>'.format(re.escape(tag)), contents):
                contents = re.sub(r'<{0}>[^<]*</{0}>'.format(re.escape(tag)), lambda x: line, contents)
            else:
                contents = re.sub(r'^(\s*)(<{0}>[^<]*</{0}>)$'.format(re.escape(previous_tag)), lambda x: '{0}{1}\n{0}{2}'.format(x.group(1), x.group(2), line), contents, count=1, flags=re.M)
            previous_tag = tag
        new_commit = info_list[1][1]
        # the commit alone does NOT tell which branch it is from
        branch = git_output('branch', '--show-current', cwd=module_path) or 'detached'
        change = '{0} (no update)'.format(new_commit) if commit and new_commit.startswith(commit[:7]) else '{0} -> {1}'.format(commit, new_commit)
        print('{0} [{1}] {2}'.format(name, branch, change))
    with open(info_file, 'w', encoding='utf8', newline='\n') as f:
        f.write(contents)
    return 0


def cmd_pull_modules():
    returncode = 0
    for name, path, url, commit in read_module_list(Path(HOME_PATH) / 'rvx_version_info.xml'):
        module_path = Path(HOME_PATH) / path
        # only a module with url, as update_version_info does
        if not url or get_env_path(name):
            continue
        if not (module_path / '.git').exists():
            print('{0} (NOT cloned, skipped)'.format(name))
            continue
        # a branch given as .commit comes first
        branch = commit if is_branch(commit) else get_default_branch(module_path)
        if not branch:
            print('[FAIL] {0}: the default branch is NOT found ({1})'.format(name, module_path))
            returncode = 1
            continue
        old_commit = git_output('rev-parse', '--short=7', 'HEAD', cwd=module_path)
        for arg_list in (['fetch', '-q', 'origin'],
                         ['checkout', '-q', branch],
                         ['pull', '-q', '--ff-only', 'origin', branch]):
            if git_output('branch', '--show-current', cwd=module_path) == branch and arg_list[0] == 'checkout':
                continue
            result = subprocess.run(['git'] + arg_list, cwd=module_path, stdin=subprocess.DEVNULL)
            if result.returncode:
                print('[FAIL] {0}: git {1} ({2})'.format(name, ' '.join(arg_list), module_path))
                returncode = 1
                break
        else:
            new_commit = git_output('rev-parse', '--short=7', 'HEAD', cwd=module_path)
            print('{0} ({1})'.format(name, 'no update' if new_commit == old_commit else 'updated'))
    return returncode


def cmd_remove_modules():
    returncode = 0
    for name, path, url, _ in read_module_list(Path(HOME_PATH) / 'rvx_version_info.xml'):
        module_path = Path(HOME_PATH) / path
        if not url or get_env_path(name) or strip_os_suffix(name) == 'rvx_install' or not os.path.lexists(module_path):
            continue
        if (module_path / '.git').exists():
            if git_output('status', '--porcelain', cwd=module_path):
                print('[FAIL] {0} is NOT removed: it has uncommitted changes'.format(module_path))
                returncode = 1
                continue
            if git_output('log', '--oneline', '@{u}..HEAD', cwd=module_path):
                print('[FAIL] {0} is NOT removed: it has unpushed commits'.format(module_path))
                returncode = 1
                continue
        remove_path(module_path)
        print('removed {0}'.format(module_path))
    return returncode


def cmd_update_modules():
    print('## pull_modules', flush=True)
    returncode = cmd_pull_modules()
    if returncode:
        print('[FAIL] pull_modules failed: rvx_version_info.xml is NOT updated')
        return returncode
    print('## update_version_info', flush=True)
    return cmd_update_version_info()


def cmd_setup_all():
    for name, (func, _) in CMD_DICT.items():
        if name.startswith('setup_') and name != 'setup_all':
            print('## {0}'.format(name))
            sys.stdout.flush()
            returncode = func()
            if returncode:
                return returncode
    return 0


def cmd_install_others():
    for name, (func, _) in CMD_DICT.items():
        if name.startswith('install_') and name != 'install_others':
            print('## {0}'.format(name))
            sys.stdout.flush()
            returncode = func()
            if returncode:
                return returncode
    return 0


CMD_DICT['cmd_list'] = (cmd_cmd_list, 'show this list')
CMD_DICT['install_others'] = (cmd_install_others, 'run every install_* command')
CMD_DICT['install_pip_package'] = (cmd_install_pip_package, 'install pip packages')
CMD_DICT['path'] = (cmd_path, 'generate rvx_config_path.mh from rvx_version_info.xml')
CMD_DICT['setup_all'] = (cmd_setup_all, 'run every setup_* command')
CMD_DICT['pull_modules'] = (cmd_pull_modules, 'check out the default branch of each module with url and pull it')
CMD_DICT['remove_modules'] = (cmd_remove_modules, 'remove the modules with url, except the ones with uncommitted changes or unpushed commits')
CMD_DICT['update_modules'] = (cmd_update_modules, 'pull_modules, then update_version_info')
CMD_DICT['update_version_info'] = (cmd_update_version_info, 'update url, commit and date in rvx_version_info.xml from each module with url')
CMD_DICT['setup_misc'] = (cmd_setup_misc, 'set RVX_RELEASE_HOME (setx on windows, rvx_setup.sh on linux)')
CMD_DICT['setup_gitignore'] = (cmd_setup_gitignore, 'generate .gitignore from rvx_install/gitignore.template and rvx_version_info.xml')
CMD_DICT['setup_readme'] = (cmd_setup_readme, 'generate README.md from rvx_install/README.md.template')
CMD_DICT['setup_init'] = (cmd_setup_init, 'overwrite rvx_init.mh with rvx_install/rvx_init.mh')
CMD_DICT['setup_modules'] = (cmd_setup_modules, 'clone or update the modules in RVX_MODULE_LIST')
CMD_DICT['setup_udev'] = (cmd_setup_udev, 'copy udev rules to /etc/udev/rules.d and reload them (linux only)')
CMD_DICT['check_install'] = (cmd_check_install, 'run every check_* command')
CMD_DICT['check_env'] = (cmd_check_env, 'check python, pip and tools')
CMD_DICT['check_make'] = (
    cmd_check_make, 'check make and the shell used by make')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='rvx install')
    parser.add_argument('-cmd', required=True,
                        choices=list(CMD_DICT), help='command')
    args = parser.parse_args()
    func = CMD_DICT[args.cmd][0]
    if args.cmd.startswith('check_'):
        checker = Checker()
        func(checker)
        sys.exit(checker.summary())
    sys.exit(func())
