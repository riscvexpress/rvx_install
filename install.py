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

from setup_module import is_branch, is_for_this_os, read_module_list, select_module, setup_module, git_output

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
        checker.check_tool('curl')
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


PREDEFINED_PATH_DICT = {'munoc_hw': ('rvx_hwlib_basic', 'munoc'),
                        'starc_hw': ('rvx_hwlib_special', 'starc'),
                        'dca_hw': ('rvx_hwlib_special', 'dca')}


def get_submodule_path_dict():
    path_dict = {}
    gitmodules_path = os.path.join(HOME_PATH, '.gitmodules')
    if not os.path.isfile(gitmodules_path):
        return path_dict
    with open(gitmodules_path, encoding='utf8') as f:
        for line in f:
            match = re.match(r'^\s*path\s*=\s*(\S+)\s*$', line)
            if match:
                path_dict[os.path.basename(match.group(1))] = match.group(1)
    return path_dict


def get_name_list():
    name_list = []
    xml_path_dict = {}
    root = ET.parse(os.path.join(HOME_PATH, 'rvx_version_info.xml')).getroot()
    for element in root:
        if '.' not in element.tag:
            name = element.tag
        elif element.tag.endswith('.path') and is_for_this_os(element.tag[:-len('.path')]):
            path = (element.text or '').strip()
            if not path or path == 'None':
                continue
            name = os.path.basename(path)
            xml_path_dict[name] = path
        else:
            continue
        if name not in name_list:
            name_list.append(name)
    return name_list, xml_path_dict


def find_path(name, submodule_path_dict, xml_path_dict, path_dict):
    candidate_list = [('env', os.environ.get(get_path_var(name)))]
    if name.startswith('rvx_'):
        folder = os.path.join(HOME_PATH, name[len('rvx_'):])
        candidate_list.append(('folder', folder if os.path.isdir(folder) else None))
        if name in submodule_path_dict:
            candidate_list.append(('submodule', os.path.join(HOME_PATH, submodule_path_dict[name])))
        if name in xml_path_dict:
            candidate_list.append(('rvx_version_info.xml', os.path.join(HOME_PATH, xml_path_dict[name])))
    elif name in PREDEFINED_PATH_DICT:
        parent_name, sub_dir = PREDEFINED_PATH_DICT[name]
        if path_dict.get(parent_name):
            candidate_list.append(('predefined', os.path.join(path_dict[parent_name], sub_dir)))
    for source, candidate in candidate_list:
        if candidate:
            return os.path.abspath(candidate).replace('\\', '/'), source
    return None, None


def get_path_var(name):
    if name.startswith('rvx_'):
        return 'RVX_{0}_HOME'.format(name[len('rvx_'):].upper())
    return '{0}_HOME'.format(name.upper())


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
    path_dict = {}
    var_list = []
    submodule_path_dict = get_submodule_path_dict()
    name_list, xml_path_dict = get_name_list()
    for name in name_list:
        found_path, source = find_path(name, submodule_path_dict, xml_path_dict, path_dict)
        if not found_path:
            print('{0} is NOT found'.format(get_path_var(name)))
            continue
        print('{0} = {1} ({2})'.format(get_path_var(name), found_path, source))
        path_dict[name] = found_path
        var_list.append(get_path_var(name))
        assign = '=' if name.startswith('rvx_') else '?='
        line_list.append('{0} {1} {2}'.format(get_path_var(name), assign, found_path))
    line_list.append('export {0}'.format(' '.join(var_list)))
    line_list.append('')
    line_list.append('-include {0}/rvx_config_python.mh'.format(HOME_PATH))
    with open(output_file, 'w', encoding='utf8', newline='\n') as f:
        f.write('\n'.join(line_list) + '\n')
    print('generated {0}'.format(output_file))


def cmd_path():
    generate_config_path(os.path.join(HOME_PATH, 'rvx_config_path.mh'))
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
    root = ET.parse(os.path.join(HOME_PATH, 'rvx_version_info.xml')).getroot()
    for element in root:
        if element.tag.endswith('.path'):
            path = (element.text or '').strip()
            if path and path != 'None' and path + '/' not in line_list:
                line_list.append(path + '/')
    with open(output_file, 'w', encoding='utf8', newline='\n') as f:
        f.write('\n'.join(line_list) + '\n')
    print('generated {0}'.format(output_file))
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

UDEV_RULES_PATH = os.path.join(HOME_PATH, 'rvx_devkit', 'rules.d', '99-rvx.rules')
UDEV_RULES_DIR = '/etc/udev/rules.d'


def cmd_setup_udev():
    if is_windows():
        return 0
    target_path = os.path.join(UDEV_RULES_DIR, os.path.basename(UDEV_RULES_PATH))
    if os.path.isfile(target_path) and filecmp.cmp(UDEV_RULES_PATH, target_path, shallow=False):
        print('{0} is up to date'.format(target_path))
        return 0
    for cmd_list in (['sudo', 'cp', '-f', UDEV_RULES_PATH, UDEV_RULES_DIR],
                     ['sudo', 'udevadm', 'control', '--reload-rules'],
                     ['sudo', 'udevadm', 'trigger']):
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
    module_list = read_module_list(Path(HOME_PATH) / 'rvx_version_info.xml')
    for name in os.environ.get('RVX_MODULE_LIST', '').split():
        setup_module(*select_module(module_list, name))
    return 0


def cmd_update_version_info():
    info_file = os.path.join(HOME_PATH, 'rvx_version_info.xml')
    with open(info_file, encoding='utf8') as f:
        contents = f.read()
    root = ET.parse(info_file).getroot()
    for element in root:
        if not element.tag.endswith('.path'):
            continue
        name = element.tag[:-len('.path')]
        path = (element.text or '').strip()
        module_path = Path(HOME_PATH) / path
        if not path or not is_for_this_os(name) or not (module_path / '.git').exists():
            continue
        url = git_output('remote', 'get-url', 'origin', cwd=module_path)
        if git_output('status', '--porcelain', cwd=module_path):
            print('[WARNING] {0} has uncommitted changes'.format(name))
        if git_output('log', '--oneline', '@{u}..HEAD', cwd=module_path):
            print('[WARNING] {0} has unpushed commits'.format(name))
        commit = (root.findtext('{0}.commit'.format(name)) or '').strip()
        if commit and is_branch(commit):
            print('{0} {1} (branch, skipped)'.format(name, commit))
            continue
        info_list = (('url', url),
                     ('commit', git_output('rev-parse', '--short=7', 'HEAD', cwd=module_path)),
                     ('date', git_output('log', '-1', '--date=format:%Y-%m-%d-%H-%M', '--format=%cd', cwd=module_path)))
        previous_tag = element.tag
        for key, value in info_list:
            tag = '{0}.{1}'.format(name, key)
            line = '<{0}>{1}</{0}>'.format(tag, value)
            if re.search(r'<{0}>[^<]*</{0}>'.format(re.escape(tag)), contents):
                contents = re.sub(r'<{0}>[^<]*</{0}>'.format(re.escape(tag)), lambda x: line, contents)
            else:
                contents = re.sub(r'^(\s*)(<{0}>[^<]*</{0}>)$'.format(re.escape(previous_tag)), lambda x: '{0}{1}\n{0}{2}'.format(x.group(1), x.group(2), line), contents, count=1, flags=re.M)
            previous_tag = tag
        print('{0} {1}'.format(name, ' '.join(x for _, x in info_list)))
    with open(info_file, 'w', encoding='utf8', newline='\n') as f:
        f.write(contents)
    return 0


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
CMD_DICT['update_version_info'] = (cmd_update_version_info, 'update url, commit and date in rvx_version_info.xml from each .path directory')
CMD_DICT['setup_misc'] = (cmd_setup_misc, 'set RVX_RELEASE_HOME (setx on windows, rvx_setup.sh on linux)')
CMD_DICT['setup_gitignore'] = (cmd_setup_gitignore, 'generate .gitignore from rvx_install/gitignore.template and rvx_version_info.xml')
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
