# RISC-V eXpress

**RISC-V eXpress (RVX)** is an integrated hardware–software framework for developing MCU-class RISC-V SoCs with user-defined hardware accelerators.

[https://riscvexpress.github.io](https://riscvexpress.github.io)

## Prerequisites

### Linux

`make`, `curl` (or `wget`), and `git`

### Windows

Install `git` and `make` with [Scoop](https://scoop.sh) in PowerShell.

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
Invoke-RestMethod -Uri https://get.scoop.sh | Invoke-Expression
scoop install git make
```

## Install

Run the following in the top directory of this repository.

```sh
git submodule init    # register the submodules
git submodule update  # fetch the submodules (e.g. rvx_install)
make setup_python     # copy ../rvx_config_python.mh, or install Python 3.9 into ./.rvx_venv
make check_python     # confirm the selected Python
make install          # download and set up the RVX modules
```

## Setup

- Linux: run `source ./rvx_setup.sh` in every new terminal.
- Windows: open a new terminal, since the environment variables are registered with `setx`.

If `make install` fails with `No rule to make target 'install'`, the submodules are missing. Run `git submodule update --init` and try again.

The installation manual is also available on the homepage.

<!-- local -->
