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
# 2020-03-11
# Kyuseung Han (han@etri.re.kr)
# ****************************************************************************
# ****************************************************************************

import platform
import os
import sys
import subprocess
import shutil
import distro
import stat

from urllib import request
from filecmp import cmp
from pathlib import Path

is_linux = (platform.system() == 'Linux')
is_windows = not is_linux
is_centos = 'CentOS' in distro.name() or 'Red Hat' in distro.name()
is_ubuntu = 'Ubuntu' in distro.name() or 'Debian' in distro.name()
is_other_os = (not is_windows) and (not is_centos) and (not is_ubuntu)
encoding = 'utf8' if is_linux else 'cp949'


def _get_console_mode():
    if not is_windows:
        return None
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    saved = []
    for std_handle in (-11, -12):
        handle = kernel32.GetStdHandle(std_handle)
        mode = wintypes.DWORD()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            saved.append((handle, mode.value))
    return saved


# keep ENABLE_PROCESSED_OUTPUT and ENABLE_WRAP_AT_EOL_OUTPUT on, otherwise newlines are shown as symbols
def _set_console_mode(saved):
    if saved:
        import ctypes
        for handle, mode in saved:
            ctypes.windll.kernel32.SetConsoleMode(handle, mode | 0x3)


_set_console_mode(_get_console_mode())


# msys programs (sh, rm, ...) mixed with native ones may leave the console output mode cleared on windows
def _run_keeping_console_mode(*args, **kwargs):
    saved = _get_console_mode()
    try:
        return subprocess.run(*args, **kwargs)
    finally:
        _set_console_mode(saved)

def run_shell_cmd(cmd: str, cwd: Path = None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, prints_when_error=True, asserts_when_error=True):
    result = _run_keeping_console_mode(cmd, cwd=cwd, shell=True,
                                       stdout=stdout, stderr=stderr, encoding=encoding)
    if result.returncode != 0:
        if asserts_when_error:
            assert 0, result
        elif prints_when_error:
            print(result)
    return result

def get_version(shell_result: str, seperator: str, keyword: str, iter_diff: int):
    assert iter_diff > 0, iter_diff
    version_info_list = shell_result.split(seperator)
    version = None
    for i, info in enumerate(version_info_list):
        if info == keyword:
            version = version_info_list[i+iter_diff].strip()
            break
    assert version, shell_result
    return version


def get_os_version(os_name: str):
    return get_version(platform.platform(), '-', os_name, 1)


def get_gnome_version():
    return get_version(run_shell_cmd('gnome-terminal --version').stdout, ' ', 'Terminal', 1) if is_linux else None


# os_version = get_os_version('centos' if is_centos else ) if is_linux else None
gnome_terminal_version = get_gnome_version()

python_cmd = 'python3' if is_linux else 'python'


def make_nested_string(text: str, quote: str):
    text = text.replace('\\', '\\\\')
    text = text.replace('\'', '\\\'')
    text = text.replace('\"', '\\\"')
    text = f'{quote}{text}{quote}'
    return text


def run_shell_cmd_with_terminal(cmd: str, cwd: Path):
    if is_linux:
        cmd = 'bash -c ' + make_nested_string(f'{cmd} || read -p "Press Enter to close" _', '"')
        if gnome_terminal_version.split('.')[0] == '2':
            cmd = 'gnome-terminal -e ' + make_nested_string(cmd, '"')
        else:
            cmd = f'gnome-terminal -- {cmd}'
    else:
        cmd = f'start "" cmd /c "{cmd} || pause"'

    return run_shell_cmd(cmd, cwd, stdout=None, stderr=None)


def make_cmd_sudo(cmd: str, passwd: str):
    assert is_linux
    modified_cmd = f'echo \"{passwd}\" | sudo -S -v; sudo {cmd}' if passwd else f'sudo {cmd}'
    return modified_cmd


def _remove_readonly(func, path, exc_info):
    exc = exc_info[1]
    if (
        os.name != "nt"
        or getattr(exc, "winerror", None) != 5
        or func not in (os.unlink, os.rmdir)
    ):
        raise exc.with_traceback(exc_info[2])
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove(element: Path):
    if element.is_dir():
        remove_directory(element)
    else:
        remove_file(element)


def remove_file(file: Path):
    file.unlink(missing_ok=True)


def remove_files(dir: Path, pattern: str):
    if pattern and dir.is_dir():
        if is_linux:
            run_shell_cmd('rm -rf {0}'.format(pattern), dir)
        else:
            run_shell_cmd('del /s /f /q {0}'.format(pattern), dir)


def _is_junction(path: Path):
    return os.name == "nt" and os.path.lexists(path) and bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def remove_directory(path: Path):
    if path.is_symlink():
        path.unlink()
    elif _is_junction(path):
        os.rmdir(path)
    elif path.exists():
        assert path.is_dir(), path
        shutil.rmtree(path, onerror=_remove_readonly)


def copy_directory(src_dir: Path, dst_dir: Path):
    assert src_dir.is_dir()
    shutil.copytree(str(src_dir), str(dst_dir), dirs_exist_ok=True)


# overwrite
def copy_file(src_file: Path, dst_file: Path):
    assert src_file.is_file(), src_file
    shutil.copy(str(src_file), str(dst_file))


def move_files(src_path: Path, dst_path: Path):
    if src_path.is_file():
        if is_linux:
            run_shell_cmd(f'mv {src_path} {dst_path}')
        else:
            run_shell_cmd(f'move /y {src_path} {dst_path}')
    elif src_path.is_dir():
        assert dst_path.is_dir(), dst_path
        for element in src_path.glob('**/*'):
            relative_path = element.relative_to(src_path)
            dst_hier_path = dst_path / relative_path
            if is_linux:
                run_shell_cmd(f'mv {element} {dst_hier_path}')
            else:
                run_shell_cmd(f'move /y {element} {dst_hier_path}')


def move_directory(src_dir: Path, dst_dir: Path):
    copy_directory(src_dir, dst_dir)
    remove_directory(src_dir)


def is_equal_file(a_file: Path, b_file: Path):
    return cmp(a_file, b_file)


def make_executable(path: Path):
    if path.is_file():
        if is_linux:
            run_shell_cmd(f'chmod +x {path.name}', path.parent)


def extract_file(target_file: Path):
    assert target_file.is_file(), target_file
    output_dir = target_file.parent

    target_filename = target_file.name
    if target_filename.endswith('.gz') or target_filename.endswith('.tgz'):
        run_shell_cmd(f'tar -xzf ./{target_filename}', output_dir)
    elif target_filename.endswith('.tar') or target_filename.endswith('.xz'):
        run_shell_cmd(f'tar -xf ./{target_filename}', output_dir)
    else:
        assert 0, target_file


def get_path_from_os_env(os_var_name: str, default_value=None, asserts_when_missing: bool = False):
    x = os.environ.get(os_var_name)
    if x:
        x = Path(x)
    else:
        assert not asserts_when_missing, os_var_name
        x = default_value
    return x


def download_url(url: str, output_dir: Path = None, output_file=None):
    assert (not output_dir) or (not output_file)
    assert not (output_dir and output_file)

    if output_file:
        result_file = output_file
    else:
        result_file = output_dir / url.split('/')[-1]

    assert result_file.parent.is_dir(), result_file
    request.urlretrieve(url, result_file)


def get_dir_list(path: Path):
    dir_list = []
    if path.is_dir():
        for child in path.iterdir():
            if not child.is_dir():
                continue
            if child.name.startswith('.'):
                continue
            dir_list.append(child.name)
    return dir_list


def is_process_running(process_name: str) -> bool:
    assert is_linux
    result = subprocess.run(["pgrep", process_name], stdout=subprocess.DEVNULL)
    return result.returncode == 0


def get_makefile_var(var: str, cwd: Path):
    out = subprocess.check_output(
        ["make", f"print-{var}"], cwd=cwd, text=True).strip()
    return out.split("=", 1)[1]


def check_argument_number(argv: list, num: int):
    assert len(argv) == (num+2), argv


if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'append_text':
        assert len(sys.argv) == 4, sys.argv
        file = sys.argv[2]
        text_to_append = sys.argv[3]
        text_to_append = text_to_append.replace('\\n', '\n')
        path = Path(file).resolve()
        assert path.is_file(), path
        original_text = path.read_text(encoding=encoding)
        extended_text = original_text + text_to_append
        path.write_text(extended_text, encoding=encoding)
    elif cmd == 'dir_list':
        check_argument_number(sys.argv, 1)
        dir_list = get_dir_list(Path(sys.argv[2]))
        print(' '.join(dir_list))
    elif cmd == 'cp_file':
        check_argument_number(sys.argv, 2)
        copy_file(Path(sys.argv[2]), Path(sys.argv[3]))
    elif cmd == 'mkdir':
        check_argument_number(sys.argv, 1)
        path = Path(sys.argv[2])
        path.mkdir(parents=True, exist_ok=True)
    elif cmd == 'rm_file':
        check_argument_number(sys.argv, 1)
        remove_file(Path(sys.argv[2]))
    elif cmd == 'rm_files':
        check_argument_number(sys.argv, 2)
        remove_files(Path(sys.argv[2]), sys.argv[3])
    elif cmd == 'rm_dir':
        check_argument_number(sys.argv, 1)
        remove_directory(Path(sys.argv[2]))
    else:
        assert 0, sys.argv[1]
