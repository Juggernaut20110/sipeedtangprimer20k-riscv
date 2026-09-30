#!/usr/bin/env python3
"""Project command dispatcher; run builds with the pinned local environment."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".tools"
VENV_PYTHON = ROOT / ".venv/bin/python"


def tool_environment():
    env = os.environ.copy()
    tool_bin = TOOLS / "bin"
    tool_bin.mkdir(parents=True, exist_ok=True)
    selected = env.get("GOWIN_SH")
    if not selected:
        if Path("/home/user/.local/bin/gw_sh").is_file():
            selected = "/home/user/.local/bin/gw_sh"
        else:
            selected = shutil.which("gw_sh")
    if selected:
        launcher = Path(selected).expanduser().resolve()
        if not launcher.is_file() or not os.access(launcher, os.X_OK):
            raise RuntimeError(f"GOWIN_SH must name one executable launcher: {launcher}")
        alias = tool_bin / "gw_sh"
        if alias.exists() or alias.is_symlink():
            alias.unlink()
        alias.symlink_to(launcher)
        env["GOWIN_SH"] = str(launcher)

    extra = [str(tool_bin), str(ROOT / ".venv/bin")]
    extra.extend(str(path) for path in (TOOLS / "riscv-gcc").glob("**/bin"))
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: make build|load|run|compare|test [PROFILE] [PORT]", file=sys.stderr)
        return 2
    command, *values = args
    targets = {
        "build": ("scripts/build.py", values[:1] or ["minimal"]),
        "load": ("scripts/load.py", values[:1] or ["minimal"]),
        "run": ("scripts/run.py", values),
        "compare": ("scripts/compare.py", []),
        "test": ("scripts/validate.py", []),
    }
    if command not in targets:
        print(f"unknown command {command!r}", file=sys.stderr)
        return 2
    if not VENV_PYTHON.is_file():
        print("local Python environment is missing; run make setup first", file=sys.stderr)
        return 1
    script, script_args = targets[command]
    try:
        env = tool_environment()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    if command == "run":
        if len(script_args) != 2 or not script_args[1]:
            print("make run requires PROFILE and an explicit PORT", file=sys.stderr)
            return 2
        build_result = subprocess.run(
            [str(VENV_PYTHON), str(ROOT / "scripts/build.py"), script_args[0]],
            cwd=ROOT,
            env=env,
        )
        if build_result.returncode != 0:
            return build_result.returncode
    return subprocess.run([str(VENV_PYTHON), str(ROOT / script), *script_args], cwd=ROOT, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
