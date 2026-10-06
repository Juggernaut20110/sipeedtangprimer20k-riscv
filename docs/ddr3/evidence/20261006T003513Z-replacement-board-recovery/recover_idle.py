from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

import serial


ROOT = Path(__file__).resolve()
while not (ROOT / ".git").exists() and ROOT != ROOT.parent:
    ROOT = ROOT.parent
if not (ROOT / ".git").exists():
    raise RuntimeError("could not locate the project root")

OUT = Path(__file__).resolve().parent
BITSTREAM = ROOT / "build/sipeed-ddr-test/idle-build/impl/pnr/ddr_idle.fs"
EXPECTED_SHA256 = "a7a702e3ff9835ab3ee653f0f9ff7c90165808db514b3230dcc91d0314b93df3"
PORT = "/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0"


def main() -> int:
    actual_sha256 = hashlib.sha256(BITSTREAM.read_bytes()).hexdigest()
    if actual_sha256 != EXPECTED_SHA256:
        raise RuntimeError(f"idle bitstream hash mismatch: {actual_sha256}")

    command = [
        str(ROOT / ".tools/bin/openFPGALoader"),
        "--board", "tangprimer20k", "--write-sram", str(BITSTREAM),
    ]
    programmed = subprocess.run(command, capture_output=True, timeout=45)
    (OUT / "programmer.log").write_bytes(programmed.stdout + programmed.stderr)
    result = {
        "schema_version": 1,
        "recovery_id": OUT.name,
        "board": "replacement Tang Primer 20K Dock attached 2026-10-05",
        "board_comparison": "docs/ddr3/sipeed-vendor-test/new-board-20261005.md",
        "command": command,
        "programmer_exit_code": programmed.returncode,
        "flash_programming": False,
        "bitstream": str(BITSTREAM.relative_to(ROOT)),
        "bitstream_sha256": actual_sha256,
        "ddr_state": {"reset_n": 0, "cke": 0, "cs_n": 1, "ck_p": 0, "odt": 0},
        "recovery_status": "programming_failed",
    }
    if programmed.returncode:
        (OUT / "results.json").write_text(json.dumps(result, indent=2) + "\n")
        raise RuntimeError("SRAM idle-image programming failed")

    observed = bytearray()
    with serial.Serial(PORT, 115200, timeout=0.1, exclusive=True) as uart:
        uart.reset_input_buffer()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            observed.extend(uart.read(4096))
    (OUT / "uart.bin").write_bytes(observed)
    result.update({
        "uart_requested": PORT,
        "uart_baud": 115200,
        "uart_observation_seconds": 5,
        "uart_bytes": len(observed),
        "recovery_status": "ddr_held_in_reset_uart_quiet" if not observed else "unexpected_uart_activity",
    })
    (OUT / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if not observed else 1


if __name__ == "__main__":
    raise SystemExit(main())
