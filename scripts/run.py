#!/usr/bin/env python3
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gateware.soc import MEMORY_MODES, PROFILES, ProjectSoC  # noqa: E402
from scripts.memory import profile_build_dir  # noqa: E402
from litex.tools.litex_term import LiteXTerm  # noqa: E402


def generated_main_ram_base(header):
    text = header.read_text()
    for macro in ("MAIN_RAM_BASE", "MAIN_RAM_BASE_VA"):
        found = re.search(rf"^#define\s+{macro}\s+(0x[0-9a-fA-F]+|[0-9]+)", text, re.M)
        if found:
            return int(found.group(1), 0)
    raise RuntimeError("generated mem.h has no main RAM base")


def main():
    if len(sys.argv) not in (3, 4) or sys.argv[1] not in (*PROFILES, "maxperf") or not sys.argv[2]:
        print("usage: make run PROFILE=minimal PORT=/dev/serial/by-id/<device> [MEMORY=onchip|ddr3]", file=sys.stderr)
        return 2
    profile, port = sys.argv[1:3]
    if profile == "maxperf" and profile not in PROFILES:
        print("maxperf is provisional until both memory modes qualify; use make maxperf-run", file=sys.stderr)
        return 1
    memory = sys.argv[3] if len(sys.argv) == 4 else "onchip"
    if memory not in MEMORY_MODES:
        print(f"unknown memory mode {memory!r}; choose from {', '.join(MEMORY_MODES)}", file=sys.stderr)
        return 2
    port_path = Path(port)
    if not port_path.exists():
        print(f"serial port does not exist: {port}; connect the Dock and use its /dev/serial/by-id path", file=sys.stderr)
        return 1
    if not os.access(port, os.R_OK | os.W_OK):
        print(f"serial port is not accessible: {port}; add your user to dialout, log out/in, and reconnect the Dock", file=sys.stderr)
        return 1

    output = profile_build_dir(ROOT, profile, memory)
    metadata_path = output / "build-metadata.json"
    binary = output / "firmware/demo.bin"
    bitstream = output / "bitstream.fs"
    if not metadata_path.is_file() or not binary.is_file() or not bitstream.is_file():
        print(f"matching artifacts are missing; run make build PROFILE={profile} first", file=sys.stderr)
        return 1
    metadata = json.loads(metadata_path.read_text())
    if (metadata.get("status") != "passed" or metadata.get("profile") != profile
            or metadata.get("memory_mode", "onchip") != memory):
        print(f"build metadata does not show a successful {profile} build", file=sys.stderr)
        return 1
    base = generated_main_ram_base(output / "software/include/generated/mem.h")

    # LiteXTerm opens the UART and watches the BIOS serial prompt/magic handshake
    # before the FPGA is programmed. It owns the official serial upload protocol.
    term = LiteXTerm(True, str(binary), hex(base), None, False, None)
    try:
        term.open(port, 115200)
        term.console.configure()
        term.start()
        cpu_rtl = metadata.get("cpu_rtl")
        soc = ProjectSoC(
        profile=profile, memory=memory,
        bios_size=metadata.get("bios_size"),
        cpu_rtl=(ROOT / cpu_rtl) if cpu_rtl else None,
        cpu_variant=metadata.get("cpu_variant"),
    )
        programmer = soc.platform.create_programmer(kit="openfpgaloader")
        programmer.load_bitstream(str(bitstream))
        term.wait()
    finally:
        term.stop()
        try:
            term.console.unconfigure()
        finally:
            term.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as error:
        print(f"run failed: {error}", file=sys.stderr)
        sys.exit(1)
