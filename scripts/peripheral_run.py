#!/usr/bin/env python3
"""Load and run feature-specific SRAM images while retaining build identity checks."""

import argparse
import contextlib
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gateware.soc import MEMORY_MODES, PROFILES, ProjectSoC  # noqa: E402
from litex.tools.litex_term import LiteXTerm  # noqa: E402
from scripts.build import build_profile, source_fingerprint  # noqa: E402
from scripts.peripheral_evidence import board_identity
from scripts.peripheral_program import program_sram, assert_hardware_available
from scripts.peripheral_config import peripheral_build_dir, validate_features  # noqa: E402


def verify_build(output_dir, profile, memory, sdcard, ethernet):
    metadata_path = output_dir / "build-metadata.json"
    binary = output_dir / "firmware/demo.bin"
    bitstream = output_dir / "bitstream.fs"
    if not all(path.is_file() for path in (metadata_path, binary, bitstream)):
        raise RuntimeError(f"matching peripheral artifacts are missing under {output_dir}")
    metadata = json.loads(metadata_path.read_text())
    if (metadata.get("status") != "passed" or metadata.get("timing_gate") != "passed"
            or metadata.get("base_source_fingerprint") != source_fingerprint()
            or metadata.get("firmware_sha256") != hashlib.sha256(binary.read_bytes()).hexdigest()
            or metadata.get("profile") != profile
            or metadata.get("memory_mode") != memory
            or metadata.get("peripherals") != {"sdcard": sdcard, "ethernet": ethernet}
            or hashlib.sha256(bitstream.read_bytes()).hexdigest() != metadata.get("bitstream_sha256")):
        raise RuntimeError(f"build metadata or bitstream identity does not verify {profile}/{memory}")
    return metadata, binary, bitstream


class RecordingSerial:
    """Record exact UART bytes while preserving LiteXTerm's serial interface."""
    def __init__(self, port, rx_file, tx_file):
        self._port = port
        self._rx_file = rx_file
        self._tx_file = tx_file

    def __getattr__(self, name):
        return getattr(self._port, name)

    def read(self, size=1):
        data = self._port.read(size)
        if data:
            self._rx_file.write(data)
        return data

    def write(self, data):
        written = self._port.write(data)
        if written is None:
            written = len(data)
        self._tx_file.write(data[:written])
        return written

    def close(self):
        return self._port.close()


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_profile(profile, port, memory, sdcard, ethernet, force,
                board_serial, board_revision, evidence_session=None):
    output_dir = peripheral_build_dir(ROOT, memory, profile, sdcard, ethernet)
    metadata = build_profile(
        profile, memory=memory, force=force, output_dir=output_dir,
        sdcard=sdcard, ethernet=ethernet, diagnostic=(memory == "onchip"),
    )
    metadata, binary, bitstream = verify_build(output_dir, profile, memory, sdcard, ethernet)
    if not Path(port).exists():
        raise RuntimeError(f"serial port does not exist: {port}; connect the Dock and use its stable /dev/serial/by-id path")
    if not os.access(port, os.R_OK | os.W_OK):
        raise RuntimeError(f"serial port is not accessible: {port}; grant dialout access and reconnect the Dock")

    session_id = evidence_session or datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%dT%H%M%S.%fZ-peripheral-" + profile
    )
    if Path(session_id).name != session_id or session_id in ("", ".", ".."):
        raise ValueError("evidence session must be a single directory name")
    evidence_dir = ROOT / "docs/peripherals/evidence" / session_id
    evidence_dir.mkdir(parents=True, exist_ok=False)
    rx_path = evidence_dir / "uart_rx.bin"
    tx_path = evidence_dir / "uart_tx.bin"
    programmer_path = evidence_dir / "programmer.log"
    event_path = evidence_dir / "events.jsonl"
    rx_file = rx_path.open("wb", buffering=0)
    tx_file = tx_path.open("wb", buffering=0)

    manifest = {
        "schema_version": 1,
        "session_id": session_id,
        "status": "in_progress",
        "started_utc": utc_now(),
        "board": board_identity(board_serial, board_revision),
        "build": {
            "profile": profile, "memory": memory, "sdcard": sdcard, "ethernet": ethernet,
            "source_fingerprint": metadata["source_fingerprint"],
            "bitstream_sha256": file_sha256(bitstream),
            "firmware_sha256": file_sha256(binary),
        },
        "criteria": {},
        "artifacts": [],
    }

    def event(name, **details):
        with event_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"utc": utc_now(), "event": name, **details}) + "\n")

    def write_manifest():
        records = []
        for name, path in (("uart_rx", rx_path), ("uart_tx", tx_path),
                           ("programmer_log", programmer_path), ("events", event_path)):
            if path.is_file():
                records.append({"name": name, "path": path.name,
                                "bytes": path.stat().st_size, "sha256": file_sha256(path)})
        manifest["artifacts"] = records
        manifest["updated_utc"] = utc_now()
        (evidence_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    mem_header = output_dir / "software/include/generated/mem.h"
    import re
    text = mem_header.read_text()
    match = re.search(r"^#define\s+(?:MAIN_RAM_BASE|MAIN_RAM_BASE_VA)\s+(0x[0-9a-fA-F]+|[0-9]+)", text, re.M)
    if not match:
        raise RuntimeError("generated mem.h has no main RAM base")
    base = int(match.group(1), 0)

    assert_hardware_available(port)

    # Open UART and arm LiteX's BIOS serial-loader handshake before SRAM programming.
    term = LiteXTerm(True, str(binary), hex(base), None, False, None)
    try:
        term.open(port, 115200)
        term.port.timeout = 0.1
        term.port.write_timeout = 2.0
        term.port = RecordingSerial(term.port, rx_file, tx_file)
        term.console.configure()
        term.start()
        event("uart_opened", port=port, baud=115200)
        event("sram_programming_started", bitstream_sha256=manifest["build"]["bitstream_sha256"])
        program_sram(bitstream, programmer_path)
        event("sram_programming_completed")
        term.wait()
    except (KeyboardInterrupt, SystemExit):
        manifest["status"] = "interrupted"
        manifest["error"] = "operator interrupted the interactive UART session"
        event("operator_interrupted")
        raise
    except BaseException as error:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(error).__name__}: {error}"
        event("session_failed", error=manifest["error"])
        raise
    finally:
        if manifest["status"] == "in_progress":
            manifest["status"] = "interactive_session_closed"
        manifest["finished_utc"] = utc_now()
        try:
            term.stop()
            try:
                term.console.unconfigure()
            finally:
                term.close()
        finally:
            rx_file.close()
            tx_file.close()
            write_manifest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=(*PROFILES, "ALL"), required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--memory", choices=MEMORY_MODES, default="ddr3")
    parser.add_argument("--sdcard", choices=("none", "spi"), default="none")
    parser.add_argument("--ethernet", choices=("none", "rmii"), default="none")
    parser.add_argument("--board-serial", required=True,
                        help="observed board serial, or unavailable when user confirms none is printed")
    parser.add_argument("--board-revision", required=True,
                        help="attached core-board/Dock revision verified against its pin map")
    parser.add_argument("--evidence-session",
                        help="optional unique evidence directory name; otherwise generated")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        validate_features(args.sdcard, args.ethernet)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    profiles = list(PROFILES) if args.profile == "ALL" else [args.profile]
    failures = []
    for profile in profiles:
        try:
            run_profile(profile, args.port, args.memory, args.sdcard, args.ethernet, args.force,
                        args.board_serial, args.board_revision,
                        f"{args.evidence_session}-{profile}" if args.evidence_session and len(profiles) > 1
                        else args.evidence_session)
        except KeyboardInterrupt:
            print(f"{profile}: stopped by operator; remaining profiles were not programmed", file=sys.stderr)
            return 130
        except Exception as error:
            failures.append({"profile": profile, "error": f"{type(error).__name__}: {error}"})
            print(f"{profile}: peripheral run failed; remaining profiles will be considered: {error}", file=sys.stderr)
    if failures:
        print(json.dumps({"status": "failed", "failures": failures}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
