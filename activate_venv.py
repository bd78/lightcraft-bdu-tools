#!/usr/bin/env python3
"""Create this folder's virtual environment (.venv) when needed, then use it.

A Python script can't change the shell that started it, so "activate" means one of:

  python activate_venv.py                 open a new shell with the venv active (type `exit` to leave)
  python activate_venv.py run CMD [ARGS]   run one command inside the venv, for example
                                    python activate_venv.py run python gap_report.py
  python activate_venv.py path             print the activation script for your shell, to load it yourself:
                                    PowerShell: . (python activate_venv.py path)
                                    bash:       source "$(python activate_venv.py path)"
  python activate_venv.py --recreate ...   delete and rebuild .venv first (works with every form above)

Packages listed in requirements.txt are installed when the venv is created and again whenever
that file changes.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV_DIR / ".requirements.sha256"
WINDOWS = os.name == "nt"
BIN_DIR = VENV_DIR / ("Scripts" if WINDOWS else "bin")
PYTHON = BIN_DIR / ("python.exe" if WINDOWS else "python")
PROMPT = f"({ROOT.name})"


def requirement_lines() -> list[str]:
    if not REQUIREMENTS.is_file():
        return []
    lines = REQUIREMENTS.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def ensure_venv(recreate: bool) -> None:
    if recreate and VENV_DIR.exists():
        print(f"Removing {VENV_DIR}", file=sys.stderr)
        shutil.rmtree(VENV_DIR)
    if not PYTHON.is_file():
        print(f"Creating {VENV_DIR} with Python {sys.version.split()[0]}", file=sys.stderr)
        venv.EnvBuilder(with_pip=True, prompt=ROOT.name).create(VENV_DIR)

    if not requirement_lines():
        return
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    if STAMP.is_file() and STAMP.read_text(encoding="utf-8").strip() == digest:
        return
    print("Installing requirements.txt", file=sys.stderr)
    subprocess.run(
        [str(PYTHON), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS)],
        check=True,
    )
    STAMP.write_text(digest, encoding="utf-8")


def venv_environment() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env["VIRTUAL_ENV"] = str(VENV_DIR)
    env["VIRTUAL_ENV_PROMPT"] = PROMPT
    env["PATH"] = str(BIN_DIR) + os.pathsep + env.get("PATH", "")
    return env


def open_shell(env: dict[str, str]) -> int:
    if WINDOWS:
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell:
            # A -Command string runs even under the Restricted execution policy, unlike
            # dot-sourcing Activate.ps1, so the prompt is set here instead.
            prompt = f'function global:prompt {{ "{PROMPT} PS $($PWD.Path)> " }}'
            command = [powershell, "-NoLogo", "-NoExit", "-Command", prompt]
        else:
            command = [env.get("ComSpec", "cmd.exe"), "/K", f"prompt {PROMPT} $P$G"]
    else:
        command = [env.get("SHELL", "/bin/sh")]
    print(f"venv active in a new shell ({Path(command[0]).name}); type `exit` to leave it.", file=sys.stderr)
    return subprocess.run(command, env=env, cwd=os.getcwd(), check=False).returncode


def run_command(args: list[str], env: dict[str, str]) -> int:
    if not args:
        print("activate_venv.py run: give a command, e.g. `python activate_venv.py run python gap_report.py`", file=sys.stderr)
        return 2
    executable = shutil.which(args[0], path=env["PATH"]) or args[0]
    try:
        return subprocess.run([executable, *args[1:]], env=env, check=False).returncode
    except FileNotFoundError:
        print(f"activate_venv.py run: command not found: {args[0]}", file=sys.stderr)
        return 127


def activation_script() -> Path:
    bash_like = bool(os.environ.get("MSYSTEM")) or "bash" in os.environ.get("SHELL", "")
    if WINDOWS and not bash_like:
        return BIN_DIR / "Activate.ps1"
    return BIN_DIR / "activate"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recreate", action="store_true", help="delete and rebuild .venv first")
    sub = parser.add_subparsers(dest="action")
    run = sub.add_parser("run", help="run one command inside the venv")
    run.add_argument("command", nargs=argparse.REMAINDER)
    sub.add_parser("path", help="print the activation script for your shell")
    sub.add_parser("shell", help="open a new shell with the venv active (the default)")
    args = parser.parse_args()

    try:
        ensure_venv(args.recreate)
    except (OSError, subprocess.CalledProcessError) as err:
        print(f"activate_venv.py: could not prepare {VENV_DIR}: {err}", file=sys.stderr)
        return 1

    if args.action == "path":
        print(activation_script())
        return 0
    env = venv_environment()
    if args.action == "run":
        return run_command(args.command, env)
    return open_shell(env)


if __name__ == "__main__":
    sys.exit(main())
