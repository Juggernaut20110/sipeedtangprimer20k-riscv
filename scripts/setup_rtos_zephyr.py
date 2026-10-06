#!/usr/bin/env python3
"""Create the isolated host Python environment for the RTOS Zephyr build."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".deps/rtos/venv"
REQUIREMENTS = ROOT / "firmware/rtos_system_fit/requirements-zephyr.lock.txt"
HOST_TOOLS = ROOT / "firmware/rtos_system_fit/host-tools.lock.json"


def main() -> int:
    tool_lock = json.loads(HOST_TOOLS.read_text())
    dtc = tool_lock["device_tree_compiler"]
    archive = VENV.parent / dtc["archive"]
    if archive.is_file():
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != dtc["sha256"]:
            raise RuntimeError(f"{archive.name} SHA256 mismatch: {digest}")
        dtc_root = VENV.parent / "tools/dtc-1.7.2"
        binary = dtc_root / "usr/bin/dtc"
        if not binary.is_file():
            dtc_root.mkdir(parents=True, exist_ok=True)
            subprocess.run(["dpkg-deb", "-x", str(archive), str(dtc_root)], check=True)
        version = subprocess.run([str(binary), "--version"], check=True, text=True,
                                 capture_output=True).stdout.strip()
        if "1.7.2" not in version:
            raise RuntimeError(f"unexpected pinned dtc version: {version}")
    elif not shutil.which("dtc"):
        raise RuntimeError(f"Pinned dtc archive missing at {archive}; download {dtc['archive']} into {archive.parent}")
    VENV.parent.mkdir(parents=True, exist_ok=True)
    python = VENV / "bin/python"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "--requirement", str(REQUIREMENTS)], check=True)
    zephyr = VENV.parent / "zephyr"
    topdir = subprocess.run([str(python), "-m", "west", "topdir"], cwd=zephyr,
                            text=True, capture_output=True, check=False)
    if topdir.returncode:
        subprocess.run([str(python), "-m", "west", "init", "--local", str(zephyr)],
                       cwd=VENV.parent, check=True)
    print(f"Zephyr host environment: {VENV.relative_to(ROOT)}")
    print(f"Pinned Python packages: {REQUIREMENTS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
