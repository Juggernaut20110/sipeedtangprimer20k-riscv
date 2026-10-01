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
        print("usage: make build|load|run|compare|test|benchmark-build|benchmark-run|benchmark-report|ddr-test-build|ddr-test-run|ddr-test-report", file=sys.stderr)
        return 2
    command, *values = args
    targets = {
        "build": ("scripts/build.py", [*(values[:1] or ["minimal"]), "--memory", (values[1:2] or ["onchip"])[0]]),
        "load": ("scripts/load.py", [*(values[:1] or ["minimal"]), (values[1:2] or ["onchip"])[0]]),
        "run": ("scripts/run.py", values),
        "compare": ("scripts/compare.py", ["--memory", (values[:1] or ["onchip"])[0]]),
        "test": ("scripts/validate.py", []),
        "benchmark-build": ("scripts/benchmark_build.py", ["--memory", (values[:1] or ["onchip"])[0]]),
        "benchmark-run": ("scripts/benchmark_run.py", values),
        "benchmark-report": ("scripts/benchmark_report.py", []),
        "ddr-test-build": ("scripts/ddr_test_build.py", [values[0].lower() if values else "all", "--stress-seconds", values[1] if len(values) > 1 else "1800"]),
        "ddr-test-run": ("scripts/ddr_test_run.py", [
            values[0].lower() if values else "all",
            values[1] if len(values) > 1 else "",
            "--training-runs", values[2] if len(values) > 2 else "10",
            "--stress-seconds", values[3] if len(values) > 3 else "1800",
        ]),
        "ddr-test-report": ("scripts/ddr_test_report.py", []),
        "cpu-candidate-build": ("scripts/cpu_candidate_build.py", []),
        "cpu-candidate-run": ("scripts/cpu_candidate_run.py", [values[0] if values else ""]),
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
        if len(script_args) not in (2, 3) or not script_args[1]:
            print("make run requires PROFILE and an explicit PORT", file=sys.stderr)
            return 2
        memory = script_args[2] if len(script_args) == 3 else "onchip"
        build_result = subprocess.run(
            [str(VENV_PYTHON), str(ROOT / "scripts/build.py"), script_args[0], "--memory", memory],
            cwd=ROOT,
            env=env,
        )
        if build_result.returncode != 0:
            return build_result.returncode
    if command == "benchmark-run":
        if len(script_args) not in (2, 3) or not script_args[1]:
            print("make benchmark-run requires an explicit PORT; set PROFILE=standard (or another profile)", file=sys.stderr)
            return 2
        if len(script_args) == 3:
            script_args = [script_args[0], script_args[1], "--memory", script_args[2]]
    if command == "ddr-test-run" and not script_args[1]:
        print("make ddr-test-run requires an explicit PORT", file=sys.stderr)
        return 2
    if command == "cpu-candidate-run" and not script_args[0]:
        print("make cpu-candidate-run requires an explicit PORT", file=sys.stderr)
        return 2
    return subprocess.run([str(VENV_PYTHON), str(ROOT / script), *script_args], cwd=ROOT, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
