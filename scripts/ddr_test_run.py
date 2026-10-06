#!/usr/bin/env python3
"""Run repeated DDR3 training checks and a destructive full-range stress suite."""

import datetime
import contextlib
import glob
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_run as serial_runner  # noqa: E402
import ddr_test_build as build_runner  # noqa: E402
from scripts.peripheral_evidence import board_identity
from scripts.peripheral_program import program_sram, assert_hardware_available
import compare as compare_module  # noqa: E402
from gateware.soc import DDR_DIAGNOSTIC_BASE, DDR_L2_SIZE, DDR_SIZE_BYTES, MEMORY_MODES, PROFILES, ProjectSoC  # noqa: E402
from gateware.ddr_geometry import DDR_ALIAS_OFFSETS, DDR_VISIBILITY_STRIDE, DDR_VISIBILITY_SAMPLES
from memory import profile_build_dir, validate_memory  # noqa: E402


THOROUGH_TRAINING_RUNS = 10
THOROUGH_STRESS_SECONDS = 1800
DEFAULT_TRIAL_TIMEOUT_SECONDS = 14400.0
SYS_CLK_HZ = 48_000_000
ANSI_ESCAPE = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def split_words(line):
    values = {}
    for item in line.split()[1:]:
        if "=" in item:
            name, value = item.split("=", 1)
            values[name] = value
    return values


def parse_number(value):
    return int(value, 0)


def parse_u64(values, name):
    high = values.get(name + "_hi")
    low = values.get(name + "_lo")
    if high is None or low is None:
        raise ValueError(f"missing {name} 64-bit counter")
    return (int(high, 16) << 32) | int(low, 16)


def expected_read_lanes(memory_dir):
    header = memory_dir / "software/include/generated/sdram_phy.h"
    text = header.read_text()
    definitions = {}
    for name in ("SDRAM_PHY_MODULES", "SDRAM_PHY_DQ_DQS_RATIO", "SDRAM_PHY_DELAYS", "SDRAM_PHY_BITSLIPS"):
        match = re.search(rf"^#define\s+{name}\s+(\d+)", text, re.M)
        if not match:
            raise RuntimeError(f"generated SDRAM PHY header is missing {name}")
        definitions[name] = int(match.group(1))
    if "#define SDRAM_PHY_READ_LEVELING_CAPABLE" not in text:
        raise RuntimeError("generated GW2DDRPHY does not advertise read leveling")
    definitions["SDRAM_DELAY_PER_DQ"] = bool(re.search(r"^#define\s+SDRAM_DELAY_PER_DQ(?:\s|$)", text, re.M))
    dq_count = definitions["SDRAM_PHY_DQ_DQS_RATIO"] if definitions["SDRAM_DELAY_PER_DQ"] else 1
    return definitions["SDRAM_PHY_MODULES"] * dq_count, definitions


def validate_training(lines, expected_lanes, phy_config):
    starts = [line for line in lines if line.startswith("SDRAM_TRAINING_START ")]
    results = [line for line in lines if line.startswith("SDRAM_TRAINING_RESULT ")]
    lane_lines = [line[line.index("SDRAM_READ_LEVELING_LANE "):]
                  for line in lines if "SDRAM_READ_LEVELING_LANE " in line]
    lanes = []
    lane_errors = []
    lane_pattern = re.compile(
        r"SDRAM_READ_LEVELING_LANE module=(\d+) dq=(\d+) bitslip=(\d+) "
        r"status=(\w+) window_start=(-?\d+) window_length=(\d+)"
        r"(?: delay_center=(-?\d+) delay_half_window=(-?\d+))?"
    )
    for line in lane_lines:
        match = lane_pattern.fullmatch(line)
        if not match:
            lane_errors.append(f"unparsed leveling lane record: {line}")
            continue
        module, dq, bitslip, status, start, length, center, half_window = match.groups()
        record = {
            "module": int(module), "dq_line": int(dq), "bitslip": int(bitslip),
            "status": status, "window_start": int(start), "window_length": int(length),
            "delay_center": int(center) if center is not None else -1,
            "delay_half_window": int(half_window) if half_window is not None else -1,
            "raw": line,
        }
        lanes.append(record)
        if status != "passed":
            lane_errors.append(f"leveling lane failed: {line}")
        if not 0 <= record["bitslip"] < phy_config.get("SDRAM_PHY_BITSLIPS", 0):
            lane_errors.append(f"lane has an invalid bitslip selection: {line}")
        if (record["window_length"] < 2
                or record["window_start"] < 0
                or record["window_start"] >= phy_config.get("SDRAM_PHY_DELAYS", 0)
                or record["delay_center"] < 0
                or record["delay_center"] >= phy_config.get("SDRAM_PHY_DELAYS", 0)):
            lane_errors.append(f"lane has an invalid delay window/center: {line}")
    dq_count = phy_config.get("SDRAM_PHY_DQ_DQS_RATIO", 0) if phy_config.get("SDRAM_DELAY_PER_DQ") else 1
    expected_ids = {(module, dq) for module in range(phy_config.get("SDRAM_PHY_MODULES", 0))
                    for dq in range(dq_count)}
    if not expected_ids or len(expected_ids) != expected_lanes:
        lane_errors.append("expected lane count does not match the generated PHY configuration")
    lane_ids = [(lane["module"], lane["dq_line"]) for lane in lanes]
    if len(set(lane_ids)) != len(lane_ids):
        lane_errors.append("duplicate leveling lane identity")
    if set(lane_ids) != expected_ids:
        lane_errors.append(f"leveling lane identities mismatch; missing={sorted(expected_ids - set(lane_ids))}, unexpected={sorted(set(lane_ids) - expected_ids)}")
    result_fields = [split_words(line) for line in results]
    passed = (
        len(starts) == 1
        and len(results) == 1
        and result_fields[0].get("status") == "passed"
        and len(lanes) == expected_lanes
        and not lane_errors
    )
    if not starts:
        lane_errors.append("BIOS did not emit SDRAM_TRAINING_START")
    if not results:
        lane_errors.append("BIOS did not emit SDRAM_TRAINING_RESULT")
    elif result_fields[0].get("status") != "passed":
        lane_errors.append("BIOS reported DDR3 leveling failure")
    if len(lanes) != expected_lanes:
        lane_errors.append(f"recorded {len(lanes)} training lanes; expected {expected_lanes}")
    return {
        "status": "passed" if passed else "failed",
        "start_records": starts,
        "result_records": results,
        "expected_lane_count": expected_lanes,
        "lane_count": len(lanes),
        "phy_configuration": phy_config,
        "lanes": lanes,
        "errors": lane_errors,
    }


def validate_bios_memtest(lines):
    """A training or firmware pass cannot override a failed startup memtest."""
    records = [line.strip() for line in lines if line.strip() in ("Memtest OK", "Memtest KO")]
    return {
        "status": "passed" if records == ["Memtest OK"] else "failed",
        "records": records,
        "errors": [] if records == ["Memtest OK"] else ["BIOS memory test failed or its result is missing/ambiguous"],
    }


def parse_phase_records(lines):
    starts = {}
    ends = {}
    for line in lines:
        if line.startswith("DDR_TEST_PHASE_START "):
            values = split_words(line)
            name = values.get("name")
            if not name or name in starts:
                raise ValueError(f"missing or duplicate DDR test phase start: {line}")
            starts[name] = values
        elif line.startswith("DDR_TEST_PHASE_END "):
            values = split_words(line)
            name = values.get("name")
            if not name or name in ends:
                raise ValueError(f"missing or duplicate DDR test phase end: {line}")
            ends[name] = values
    if set(starts) != set(ends):
        raise ValueError(f"DDR phase start/end mismatch; starts={sorted(starts)}, ends={sorted(ends)}")
    phases = {}
    for name in starts:
        start, end = starts[name], ends[name]
        if end.get("status") != "passed" or int(end.get("errors", "-1")) != 0:
            raise ValueError(f"DDR test phase {name} did not pass: {end}")
        phases[name] = {
            "range_start": parse_number(start["range_start"]),
            "range_end": parse_number(start["range_end"]),
            "coverage_bytes": int(start["coverage_bytes"]),
            "sample_stride_bytes": int(start["sample_stride_bytes"]) if "sample_stride_bytes" in start else None,
            "samples": int(start["samples"]) if "samples" in start else None,
            "samples_per_page": int(start["samples_per_page"]) if "samples_per_page" in start else None,
            "alias_offsets": int(start["alias_offsets"]) if "alias_offsets" in start else None,
            "cache_maintenance": start.get("cache_maintenance"),
            "bytes": int(end["bytes"]),
            "operations": int(end["operations"]),
            "elapsed_ticks": parse_u64(end, "elapsed_ticks"),
            "errors": 0,
        }
    return phases


def validate_full_capture(lines, profile, stress_seconds):
    start_lines = [line for line in lines if line.startswith("DDR_TEST_START ")]
    end_lines = [line for line in lines if line.startswith("DDR_TEST_END ")]
    failures = [line for line in lines if line.startswith("DDR_TEST_FAILURE ")]
    if failures:
        raise ValueError(f"DDR memory corruption was reported: {failures[0]}")
    if len(start_lines) != 1 or len(end_lines) != 1:
        raise ValueError("DDR full-suite start or end marker is missing or duplicated")
    start = split_words(start_lines[0])
    end = split_words(end_lines[0])
    if (start.get("profile") != profile or start.get("memory") != "ddr3"
            or int(start.get("clock_hz", "-1")) != SYS_CLK_HZ
            or int(start.get("ddr_bytes", "-1")) != DDR_SIZE_BYTES
            or parse_number(start.get("cached_base", "0")) != 0x40000000
            or parse_number(start.get("uncached_base", "0")) != 0xC0000000
            or int(start.get("l2_bytes", "-1")) != DDR_L2_SIZE
            or int(start.get("stress_seconds", "-1")) != stress_seconds):
        raise ValueError(f"DDR full-suite identity or geometry mismatch: {start}")
    if end.get("status") != "passed" or end.get("profile") != profile or end.get("memory") != "ddr3":
        raise ValueError(f"DDR full-suite reported failure or a mismatched identity: {end}")
    if int(end.get("errors", "-1")) != 0 or int(end.get("tested_bytes", "-1")) != DDR_SIZE_BYTES:
        raise ValueError(f"DDR full-suite error count or range does not match acceptance: {end}")

    phases = parse_phase_records(lines)
    required = {
        "walking_ones", "walking_zeros", "fixed_pattern", "inverted_pattern",
        "address_pattern", "deterministic_pseudorandom", "address_bank_row_column_alias",
        "byte_halfword_neighbor_preservation", "cached_uncached_visibility",
        "sustained_delayed_readback_stress",
    }
    if set(phases) != required:
        raise ValueError(f"DDR full-suite phase coverage is incomplete: {sorted(set(required) - set(phases))}")
    for name in (
        "walking_ones", "walking_zeros", "fixed_pattern", "inverted_pattern",
        "address_pattern", "deterministic_pseudorandom", "byte_halfword_neighbor_preservation",
        "sustained_delayed_readback_stress",
    ):
        phase = phases[name]
        if (phase["coverage_bytes"] != DDR_SIZE_BYTES
                or phase["range_start"] != 0xC0000000
                or phase["range_end"] != 0xC0000000 + DDR_SIZE_BYTES
                or phase["bytes"] != DDR_SIZE_BYTES):
            raise ValueError(f"DDR phase {name} does not cover the configured physical range: {phase}")
    word_count = DDR_SIZE_BYTES // 4
    for name in ("walking_ones", "walking_zeros", "fixed_pattern", "inverted_pattern",
                 "address_pattern", "deterministic_pseudorandom"):
        if phases[name]["operations"] != word_count:
            raise ValueError(f"DDR phase {name} did not visit every physical word: {phases[name]}")
    subword = phases["byte_halfword_neighbor_preservation"]
    if (subword["sample_stride_bytes"] != 4096
            or subword["samples_per_page"] != 5
            or subword["operations"] != (DDR_SIZE_BYTES // 4096) * 5):
        raise ValueError(f"DDR byte/halfword boundary sampling is incomplete: {subword}")
    alias_phase = phases["address_bank_row_column_alias"]
    cache_phase = phases["cached_uncached_visibility"]
    if (alias_phase["range_start"] != 0xC0000000
            or alias_phase["range_end"] != 0xC0000000 + DDR_SIZE_BYTES
            or alias_phase["alias_offsets"] != DDR_ALIAS_OFFSETS
            or alias_phase["operations"] != DDR_ALIAS_OFFSETS
            or alias_phase["coverage_bytes"] != DDR_ALIAS_OFFSETS * 4
            or alias_phase["bytes"] != DDR_ALIAS_OFFSETS * 4):
        raise ValueError(f"DDR address alias check did not exercise each expected address bit: {alias_phase}")
    if (cache_phase["range_start"] < 0xC0000000
            or cache_phase["range_end"] != 0xC0000000 + DDR_SIZE_BYTES
            or cache_phase["sample_stride_bytes"] != DDR_VISIBILITY_STRIDE
            or cache_phase["samples"] != DDR_VISIBILITY_SAMPLES
            or cache_phase["operations"] != DDR_VISIBILITY_SAMPLES * 2
            or cache_phase["cache_maintenance"] != "fence,dflush,l2flush"):
        raise ValueError(f"DDR cache visibility checks did not cover their documented samples: {cache_phase}")

    stress_lines = [line for line in lines if line.startswith("DDR_TEST_STRESS ")]
    bandwidth_lines = [line for line in lines if line.startswith("DDR_TEST_BANDWIDTH ")]
    if len(stress_lines) != 1 or len(bandwidth_lines) != 1:
        raise ValueError("DDR stress duration or bandwidth marker is missing or duplicated")
    stress = split_words(stress_lines[0])
    bandwidth = split_words(bandwidth_lines[0])
    elapsed_ticks = parse_u64(stress, "elapsed_ticks")
    if (int(stress.get("requested_seconds", "-1")) != stress_seconds
            or elapsed_ticks < stress_seconds * SYS_CLK_HZ):
        raise ValueError(f"DDR stress lasted less than requested: {stress}")
    read_bytes = parse_u64(bandwidth, "read_bytes")
    write_bytes = parse_u64(bandwidth, "write_bytes")
    bandwidth_ticks = parse_u64(bandwidth, "elapsed_ticks")
    if (bandwidth.get("path") != "uncached_controller_alias"
            or int(bandwidth.get("transfer_bytes", "-1")) != 1024 * 1024
            or int(bandwidth.get("clock_hz", "-1")) != SYS_CLK_HZ
            or read_bytes == 0 or write_bytes == 0 or bandwidth_ticks < stress_seconds * SYS_CLK_HZ):
        raise ValueError(f"DDR bandwidth evidence is incomplete: {bandwidth}")
    return {
        "status": "passed",
        "profile": profile,
        "memory_mode": "ddr3",
        "geometry_bytes": DDR_SIZE_BYTES,
        "clock_hz": SYS_CLK_HZ,
        "cache": {"cpu_dcache": "profile-dependent", "l2_bytes": DDR_L2_SIZE},
        "phases": phases,
        "stress_seconds_requested": stress_seconds,
        "stress_elapsed_ticks": elapsed_ticks,
        "stress_seconds_actual": elapsed_ticks / SYS_CLK_HZ,
        "bandwidth": {
            "path": bandwidth["path"], "transfer_bytes": int(bandwidth["transfer_bytes"]),
            "clock_hz": int(bandwidth["clock_hz"]), "read_bytes": read_bytes,
            "write_bytes": write_bytes, "elapsed_ticks": bandwidth_ticks,
            "read_bytes_per_second": int(bandwidth["read_bytes_per_second"]),
            "write_bytes_per_second": int(bandwidth["write_bytes_per_second"]),
            "sweeps": int(bandwidth["sweeps"]),
        },
    }


def read_serial_training(lines, expected_lanes, phy_config):
    return validate_training(lines, expected_lanes, phy_config)


def run_ddr_trial(profile, mode, attempt, image, build, ddr_meta, port,
                  run_id, evidence_dir, handshake_timeout, trial_timeout,
                  expected_lanes, phy_config, *, diagnostic_only=False,
                  sdcard="none", ethernet="none"):
    from litex.tools import litex_term

    label = f"{mode}-{attempt:02d}"
    raw_path = evidence_dir / f"{label}.uart.bin"
    text_path = evidence_dir / f"{label}.uart.log"
    tx_path = evidence_dir / f"{label}.uart.tx.bin"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_file = raw_path.open("wb", buffering=0)
    tx_file = tx_path.open("wb", buffering=0)
    record = {
        "mode": mode,
        "diagnostic_only": diagnostic_only,
        "attempt": attempt,
        "status": "in_progress",
        "programming_status": "not_attempted",
        "image_sha256": image["binary_sha256"],
        "bitstream_sha256": serial_runner.sha256(ROOT / build["bitstream"]),
        "raw_uart_log": str(raw_path.relative_to(ROOT)),
        "text_uart_log": str(text_path.relative_to(ROOT)),
        "transmitted_uart_log": str(tx_path.relative_to(ROOT)),
        "started_utc": utc_now(),
    }
    serial_owner = None
    term = None
    old_console = litex_term.Console
    old_sigint = signal.getsignal(signal.SIGINT)

    def create_term(safe):
        litex_term.Console = serial_runner.HeadlessConsole
        try:
            return litex_term.LiteXTerm(
                True, str(ROOT / image["binary"]),
                f"0x{DDR_DIAGNOSTIC_BASE:08x}", None, safe, None,
            )
        finally:
            litex_term.Console = old_console

    def start_reader(reset_input_buffer=False):
        nonlocal serial_owner
        if reset_input_buffer:
            serial_runner.clear_uart_input_buffer(term.port)
        term.port.timeout = 0.1
        term.port.write_timeout = 2.0
        term.port = serial_runner.RecordingPort(
            term.port,
            lambda data: serial_owner._record(data),
            lambda data: tx_file.write(data),
        )
        serial_owner = serial_runner.SerialOwner(term, raw_file)
        serial_owner.start()

    try:
        if not Path(port).exists():
            raise FileNotFoundError(f"explicit UART path does not exist: {port}")
        if not os.access(port, os.R_OK | os.W_OK):
            raise PermissionError(f"explicit UART path is not readable and writable: {port}")
        assert_hardware_available(port)
        term = create_term(safe=False)
        term.open(port, 115200)
        # Discard bytes queued by the image that was running before this trial.
        # In particular, a stale BIOS prompt must not be treated as this image's
        # post-training recovery state.
        start_reader(reset_input_buffer=True)
        record["uart_input_buffer_cleared_before_programming"] = True

        record["programming_status"] = "started"
        programmer_log = evidence_dir / f"{label}.programmer.log"
        record["programmer_command"] = program_sram(ROOT / build["bitstream"], programmer_log)
        record["programmer_log"] = str(programmer_log.relative_to(ROOT))
        record["programmer_log_sha256"] = serial_runner.sha256(programmer_log)
        record["programming_status"] = "completed"

        startup = serial_owner.wait_for_start_or_console(handshake_timeout)
        if startup == "console":
            record["bios_console_confirmed"] = True
            current_lines = list(serial_owner.lines)
            training = read_serial_training(current_lines, expected_lanes, phy_config)
            if training["status"] != "passed":
                raise RuntimeError("DDR training did not pass before BIOS console; serialboot recovery was refused")
            record["training"] = training
            record["bios_memtest"] = validate_bios_memtest(current_lines)
            if serial_owner.start_marker_started.is_set() or serial_owner.start_seen.is_set():
                raise RuntimeError("DDR BIOS recovery refused after diagnostic firmware startup began")
            if not serial_owner.sfl_handler_idle.wait(timeout=30.0):
                raise TimeoutError("LiteXTerm SFL handler did not finish before DDR BIOS recovery")
            if serial_owner.start_marker_started.is_set() or serial_owner.start_seen.is_set():
                raise RuntimeError("DDR BIOS recovery refused after diagnostic firmware startup began")
            if record["bios_memtest"]["status"] != "passed" and not diagnostic_only:
                record["bios_console_confirmed"] = True
                raise RuntimeError("BIOS memory test failed; acceptance firmware upload was refused")
            previous_error = serial_owner.error
            serial_owner.stop()
            serial_owner = None
            term.close()
            term = create_term(safe=True)
            term.open(port, 115200)
            start_reader(reset_input_buffer=True)
            if previous_error is not None:
                record["startup_recovery_reader_error"] = f"{type(previous_error).__name__}: {previous_error}"
            command = b"\nserialboot\n"
            written = term.port.write(command)
            if written is not None and written != len(command):
                raise RuntimeError(f"short write sending BIOS serialboot command ({written}/{len(command)} bytes)")
            if not serial_owner.wait_for(serial_owner.start_seen, handshake_timeout, "DDR diagnostic firmware startup"):
                raise TimeoutError("DDR diagnostic firmware did not start after BIOS recovery")
            record["startup_recovery"] = "completed without FPGA reconfiguration"
        elif startup == "timeout":
            raise TimeoutError(f"no DDR diagnostic start marker within {handshake_timeout} seconds")

        if not serial_owner.wait_for(serial_owner.end_seen, trial_timeout, "DDR_TEST_END"):
            raise TimeoutError(f"no DDR diagnostic completion marker within {trial_timeout} seconds")
        # BIOS recovery replaces the UART reader; validate the complete raw
        # capture, including the original boot/training records, after the end.
        raw_file.flush()
        lines = ANSI_ESCAPE.sub(b"", raw_path.read_bytes()).decode("utf-8", errors="replace").splitlines()
        training = read_serial_training(lines, expected_lanes, phy_config)
        record["training"] = training
        record["bios_memtest"] = validate_bios_memtest(lines)
        if record["bios_memtest"]["status"] != "passed" and not diagnostic_only:
            raise RuntimeError("BIOS memory test failed or its result was missing")
        if training["status"] != "passed":
            raise RuntimeError("DDR BIOS training records failed or did not cover every read lane")
        if serial_owner.application_failure is not None:
            raise RuntimeError(serial_owner.application_failure)
        if mode == "training":
            smoke_lines = [line for line in lines if line.startswith("DDR_TEST_SMOKE ")]
            if len(smoke_lines) != 1 or split_words(smoke_lines[0]).get("status") != "passed":
                raise RuntimeError("training firmware did not emit a passing DDR smoke-test marker")
            app_ends = [line for line in lines if line.startswith("DDR_TEST_END ")]
            if len(app_ends) != 1 or split_words(app_ends[0]).get("status") != "passed":
                raise RuntimeError("training smoke-test completion marker did not pass")
            record["smoke_test"] = split_words(smoke_lines[0])
        else:
            record["result"] = validate_full_capture(lines, profile, ddr_meta["stress_seconds"])
        record["status"] = "diagnostic_complete" if diagnostic_only else "passed"
    except KeyboardInterrupt:
        record["status"] = "interrupted"
        record["error"] = "KeyboardInterrupt"
    except BaseException as error:
        record["status"] = "failed"
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        if serial_owner is not None:
            record["application_end_seen"] = serial_owner.end_seen.is_set()
            record["application_failure"] = serial_owner.application_failure
            record["bios_training_status_seen"] = any(
                line.startswith("SDRAM_TRAINING_RESULT ") for line in serial_owner.lines
            )
            try:
                serial_owner.stop()
            except BaseException as error:
                record["status"] = "failed"
                record["cleanup_error"] = f"{type(error).__name__}: {error}"
        elif term is not None:
            try:
                term.close()
            except BaseException as error:
                record["cleanup_error"] = f"{type(error).__name__}: {error}"
        litex_term.Console = old_console
        signal.signal(signal.SIGINT, old_sigint)
        raw_file.close()
        tx_file.close()

    if raw_path.is_file():
        raw_bytes = raw_path.read_bytes()
        decoded = ANSI_ESCAPE.sub(b"", raw_bytes).decode("utf-8", errors="replace")
        text_path.write_text(decoded)
        record.update({
            "raw_uart_sha256": serial_runner.sha256(raw_path),
            "raw_uart_bytes": len(raw_bytes),
            "transmitted_uart_sha256": serial_runner.sha256(tx_path),
            "transmitted_uart_bytes": tx_path.stat().st_size,
            "decode_errors_present": _has_decode_errors(raw_bytes),
            "bios_training_status_seen": any(
                line.startswith("SDRAM_TRAINING_RESULT ") for line in decoded.splitlines()
            ),
            "training_status_from_bios": any(
                line.startswith("SDRAM_TRAINING_RESULT status=passed")
                for line in decoded.splitlines()
            ),
        })
    record["finished_utc"] = utc_now()
    return record


def _has_decode_errors(raw_bytes):
    try:
        raw_bytes.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def write_peripheral_ddr_manifest(evidence_dir, session, build, profile,
                                  sdcard, ethernet, board_serial, board_revision):
    """Bind exact peripheral DDR qualification to the same evidence/capture tree."""
    captures = {
        "uart_rx": sorted(evidence_dir.glob("*.uart.bin")),
        "uart_tx": sorted(evidence_dir.glob("*.uart.tx.bin")),
        "programmer_log": sorted(evidence_dir.glob("*.programmer.log")),
    }
    artifact_paths = {}
    for name, paths in captures.items():
        destination = evidence_dir / ({"uart_rx": "uart_rx.bin", "uart_tx": "uart_tx.bin",
                                       "programmer_log": "programmer.log"}[name])
        with destination.open("wb") as output:
            for path in paths:
                output.write(path.read_bytes())
        artifact_paths[name] = destination
    session_copy = evidence_dir / "ddr_session.json"
    session_copy.write_text(json.dumps(session, indent=2) + "\n")
    artifact_paths["ddr_session"] = session_copy
    artifacts = [
        {"name": name, "path": path.name, "bytes": path.stat().st_size,
         "sha256": serial_runner.sha256(path)}
        for name, path in artifact_paths.items()
    ]
    full = next((trial for trial in session.get("trials", []) if trial.get("mode") == "full"), {})
    phases = full.get("result", {}).get("phases", {})
    cache_phase = phases.get("cached_uncached_visibility", {})
    passed = session.get("status") == "passed" and session.get("acceptance") == "thorough"
    measurements = {
        "training_passes": session.get("actual_training_runs", 0),
        "tested_bytes": session.get("full_range_bytes", 0),
        "pattern_errors": session.get("error_count", 0),
        "cache_visibility_errors": cache_phase.get("errors", 0),
        "stress_seconds": full.get("result", {}).get("stress_seconds_actual", 0),
        "stress_seconds_requested": session.get("requested_stress_seconds", 0),
        "board_serial": board_serial,
        "board_revision": board_revision,
    }
    manifest = {
        "schema_version": 1,
        "session_id": evidence_dir.name,
        "status": "passed" if passed else session.get("status", "incomplete"),
        "started_utc": session.get("created_utc"),
        "finished_utc": session.get("finished_utc"),
        "board": board_identity(board_serial, board_revision),
        "build": {
            "profile": profile, "memory": "ddr3", "sdcard": sdcard, "ethernet": ethernet,
            "source_fingerprint": build.get("source_fingerprint"),
            "bitstream_sha256": build.get("bitstream_sha256"),
            "firmware_sha256": build.get("firmware_sha256"),
        },
        "criteria": {
            "ddr_qualification": {
                "status": "passed" if passed else session.get("status", "incomplete"),
                "measurements": measurements,
                "evidence": ["uart_rx", "uart_tx", "programmer_log", "ddr_session"],
            },
        },
        "artifacts": artifacts,
        "session_record": session.get("evidence"),
    }
    manifest_path = evidence_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest_path


def discover_uart_ports():
    return sorted(glob.glob("/dev/serial/by-id/*"))


def discover_usb_nodes():
    return sorted(glob.glob("/dev/bus/usb/*/*"))


def run_profile(profile, port, run_id, training_runs, stress_seconds,
                handshake_timeout, trial_timeout, evidence_root, batch_profiles,
                candidate_id=None, build_dir=None, smoke_only=False,
                sdcard="none", ethernet="none", board_serial=None, board_revision=None):
    from scripts.peripheral_config import peripheral_build_dir, validate_features
    validate_features(sdcard, ethernet)
    peripheral_design = sdcard != "none" or ethernet != "none"
    memory_dir = (Path(build_dir).resolve() if build_dir is not None else
                  peripheral_build_dir(ROOT, "ddr3", profile, sdcard, ethernet)
                  if peripheral_design else profile_build_dir(ROOT, profile, "ddr3"))
    build_path = memory_dir / "build-metadata.json"
    diag_path = memory_dir / "diagnostics/ddr-test-metadata.json"
    if not build_path.is_file():
        raise RuntimeError(f"{profile}/ddr3 SoC build metadata is missing")
    build = (build_runner.build_module.build_profile(
        profile, memory="ddr3", output_dir=memory_dir,
        sdcard=sdcard, ethernet=ethernet,
    ) if peripheral_design else json.loads(build_path.read_text()))
    if peripheral_design and (build.get("timing_gate") != "passed"
            or build.get("peripherals") != {"sdcard": sdcard, "ethernet": ethernet}):
        raise RuntimeError("peripheral DDR testing requires an exact current build with passing routed timing")
    try:
        compare_module.parse_profile(profile, memory="ddr3", build_dir=memory_dir)
    except (OSError, RuntimeError, ValueError, KeyError, AttributeError) as error:
        raise RuntimeError(
            f"{profile}/ddr3 routed timing gate failed; UART and SRAM programming were not started: {error}"
        ) from error
    if peripheral_design:
        ddr_meta = build_runner.build_profile_diagnostics(
            profile, stress_seconds, build_dir=memory_dir,
            sdcard=sdcard, ethernet=ethernet,
        )
    elif candidate_id is not None:
        ddr_meta = build_runner.build_profile_diagnostics(
            profile, stress_seconds, candidate_id=candidate_id, build_dir=memory_dir,
            sdcard=sdcard, ethernet=ethernet,
        )
    elif diag_path.is_file():
        ddr_meta = json.loads(diag_path.read_text())
    else:
        raise RuntimeError(f"{profile}/ddr3 diagnostic images are missing; run make ddr-test-build first")
    build_candidate_matches = build.get("cpu_candidate") == candidate_id
    if candidate_id is None and profile == "performance":
        # The public performance profile is built from its accepted generated
        # CPU candidate, while its diagnostic image metadata is keyed to the
        # public profile (and therefore has no candidate_id). Validate that
        # registered selection and its exact RTL before accepting the build.
        selection = build.get("cpu_profile_selection") or {}
        cpu_configuration = build.get("cpu_configuration") or {}
        rtl_path = ROOT / build.get("cpu_rtl", "")
        rtl_sha256 = selection.get("rtl_sha256")
        build_candidate_matches = (
            selection.get("status") == "accepted"
            and selection.get("candidate") == build.get("cpu_candidate")
            and selection.get("clock_hz") == SYS_CLK_HZ
            and cpu_configuration.get("clock_hz") == SYS_CLK_HZ
            and rtl_sha256 is not None
            and cpu_configuration.get("rtl_sha256") == rtl_sha256
            and rtl_path.is_file()
            and serial_runner.sha256(rtl_path) == rtl_sha256
        )
    if (build.get("status") != "passed" or build.get("memory_mode") != "ddr3"
            or build.get("profile") != profile
            or build.get("peripherals", {"sdcard": "none", "ethernet": "none"})
                != {"sdcard": sdcard, "ethernet": ethernet}
            or ddr_meta.get("status") != "passed" or ddr_meta.get("profile") != profile
            or ddr_meta.get("memory_mode") != "ddr3"
            or ddr_meta.get("peripherals", {"sdcard": "none", "ethernet": "none"})
                != {"sdcard": sdcard, "ethernet": ethernet}
            or ddr_meta.get("cpu_candidate") != candidate_id
            or not build_candidate_matches):
        raise RuntimeError(f"{profile}/ddr3 build metadata failed identity/status checks")
    recorded_cpu_candidate = build.get("cpu_candidate")
    if ddr_meta.get("stress_seconds") != stress_seconds:
        ddr_meta = build_runner.build_profile_diagnostics(
            profile, stress_seconds, candidate_id=candidate_id, build_dir=memory_dir,
            sdcard=sdcard, ethernet=ethernet,
        )
    bitstream = ROOT / build["bitstream"]
    if not bitstream.is_file() or serial_runner.sha256(bitstream) != ddr_meta["source_identity"]["bitstream"]:
        raise RuntimeError(f"{profile}/ddr3 bitstream is missing or has changed")
    for variant, image in ddr_meta["images"].items():
        binary = ROOT / image["binary"]
        if not binary.is_file() or serial_runner.sha256(binary) != image.get("binary_sha256"):
            raise RuntimeError(f"{profile}/{variant} DDR diagnostic image is missing or changed")
    expected_lanes, phy_config = expected_read_lanes(memory_dir)

    session_id = f"{run_id}-{profile}"
    evidence_dir = evidence_root / session_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    session_root = (memory_dir / "diagnostics/sessions" if peripheral_design
                    else ROOT / "build/ddr3/ddr-test-sessions")
    session_path = session_root / session_id / "session.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session = {
        "schema_version": 1,
        "session_id": session_id,
        "batch_id": run_id,
        "profile": profile,
        "cpu_candidate": recorded_cpu_candidate,
        "requested_profiles": list(batch_profiles),
        "memory_mode": "ddr3",
        "status": "in_progress",
        "created_utc": utc_now(),
        "board": (board_identity(board_serial, board_revision) if peripheral_design
                  else "Sipeed Tang Primer 20K with standard Dock"),
        "clock_hz": SYS_CLK_HZ,
        "uart_device_requested": port,
        "uart_device_selected": str(Path(port).resolve()) if Path(port).exists() else None,
        "uart_candidates_at_start": discover_uart_ports(),
        "usb_device_nodes_at_start": discover_usb_nodes(),
        "requested_training_runs": training_runs,
        "actual_training_runs": 0,
        "requested_stress_seconds": stress_seconds,
        "training_criteria_runs": THOROUGH_TRAINING_RUNS,
        "stress_criteria_seconds": THOROUGH_STRESS_SECONDS,
        "build_identity": {
            "build_metadata": str(build_path.relative_to(ROOT)),
            "bitstream": build["bitstream"],
            "bitstream_sha256": serial_runner.sha256(bitstream),
            "build_fingerprint": build["source_fingerprint"],
            "cpu_configuration": build.get("cpu_configuration"),
            "bios_bytes": build.get("bios_size"),
            "l2_cache_bytes": DDR_L2_SIZE,
            "geometry_bytes": DDR_SIZE_BYTES,
            "uncached_alias": "0xc0000000",
            "diagnostic_images": {
                key: {"binary": item["binary"], "sha256": item["binary_sha256"],
                      "stress_seconds": item["stress_seconds"]}
                for key, item in ddr_meta["images"].items()
            },
        },
        "training_phy_configuration": phy_config,
        "expected_read_lanes": expected_lanes,
        "trials": [],
    }
    session_path.write_text(json.dumps(session, indent=2) + "\n")
    failure_result = 1
    try:
        for attempt in range(1, training_runs + 1):
            print(f"DDR3 {profile}: fresh SRAM reconfiguration/training/smoke {attempt}/{training_runs}", flush=True)
            record = run_ddr_trial(
                profile, "training", attempt, ddr_meta["images"]["smoke"], build,
                ddr_meta, port, run_id, evidence_dir, handshake_timeout, trial_timeout,
                expected_lanes, phy_config, sdcard=sdcard, ethernet=ethernet,
            )
            session["trials"].append(record)
            session["actual_training_runs"] = attempt
            session_path.write_text(json.dumps(session, indent=2) + "\n")
            if record["status"] != "passed":
                session["batch_continuation_safe"] = (
                    (record.get("application_end_seen") is True
                     or record.get("bios_console_confirmed") is True)
                    and record.get("programming_status") == "completed"
                )
                if not session["batch_continuation_safe"]:
                    failure_result = 2
                raise RuntimeError(f"DDR training/smoke run {attempt} failed: {record.get('error')}")

        if smoke_only:
            session["acceptance"] = "candidate_smoke"
            session["smoke_runs_passed"] = session["actual_training_runs"]
            session["status"] = "passed"
            failure_result = 0
        else:
            print(f"DDR3 {profile}: running destructive suite and {stress_seconds}s stress", flush=True)
            full = run_ddr_trial(
                profile, "full", 1, ddr_meta["images"]["full"], build,
                ddr_meta, port, run_id, evidence_dir, handshake_timeout, trial_timeout,
                expected_lanes, phy_config, sdcard=sdcard, ethernet=ethernet,
            )
            session["trials"].append(full)
            session_path.write_text(json.dumps(session, indent=2) + "\n")
            if full["status"] != "passed":
                session["batch_continuation_safe"] = (
                    full.get("application_end_seen") is True
                    and full.get("programming_status") == "completed"
                )
                if not session["batch_continuation_safe"]:
                    failure_result = 2
                raise RuntimeError(f"DDR destructive test/stress failed: {full.get('error')}")

            thorough = training_runs >= THOROUGH_TRAINING_RUNS and stress_seconds >= THOROUGH_STRESS_SECONDS
            session["acceptance"] = "thorough" if thorough else "partial"
            session["stress_seconds"] = full.get("result", {}).get("stress_seconds_actual", stress_seconds)
            session["status"] = "passed" if thorough else "partial"
            session["full_range_bytes"] = DDR_SIZE_BYTES
            session["error_count"] = 0
            session["uncached_smoke_passed"] = True
        failure_result = 0
    except BaseException as error:
        session["status"] = "failed"
        session["error"] = f"{type(error).__name__}: {error}"
    finally:
        session["finished_utc"] = utc_now()
        session["uart_evidence_directory"] = str(evidence_dir.relative_to(ROOT))
        session["evidence"] = str(session_path.relative_to(ROOT))
        session_path.write_text(json.dumps(session, indent=2) + "\n")
        if peripheral_design:
            peripheral_manifest = write_peripheral_ddr_manifest(
                evidence_dir, session, build, profile, sdcard, ethernet,
                board_serial, board_revision,
            )
            session["peripheral_manifest"] = str(peripheral_manifest.relative_to(ROOT))
            session_path.write_text(json.dumps(session, indent=2) + "\n")
    return session, failure_result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=str.lower, choices=[*PROFILES, "all"], nargs="?", default="all")
    parser.add_argument("port", nargs="?")
    parser.add_argument("--training-runs", type=int, default=10)
    parser.add_argument("--stress-seconds", type=int, default=1800)
    parser.add_argument("--handshake-timeout", type=float, default=45.0)
    parser.add_argument("--trial-timeout", type=float, default=DEFAULT_TRIAL_TIMEOUT_SECONDS)
    parser.add_argument("--sdcard", choices=("none", "spi"), default="none")
    parser.add_argument("--ethernet", choices=("none", "rmii"), default="none")
    parser.add_argument("--board-serial")
    parser.add_argument("--board-revision")
    args = parser.parse_args(argv)
    if not args.port:
        parser.error("an explicit PORT is required; the runner never selects a serial device automatically")
    if args.training_runs < 1 or args.stress_seconds < 1:
        parser.error("TRAINING_RUNS and STRESS_SECONDS must be positive integers")
    if args.handshake_timeout <= 0 or args.trial_timeout <= 0:
        parser.error("timeouts must be positive")
    validate_memory("ddr3")
    from scripts.peripheral_config import feature_slug, validate_features
    validate_features(args.sdcard, args.ethernet)
    peripheral_design = args.sdcard != "none" or args.ethernet != "none"
    if peripheral_design and (not args.board_serial or not args.board_revision):
        parser.error("peripheral DDR qualification requires observed --board-serial and verified --board-revision")

    profiles = list(PROFILES) if args.profile == "all" else [args.profile]
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-ddr3"
    if peripheral_design:
        run_id += "-" + feature_slug(args.sdcard, args.ethernet)
    evidence_root = (ROOT / "docs/peripherals/evidence" if peripheral_design
                    else ROOT / "docs/ddr3/evidence")
    manifest_path = (ROOT / f"build/peripherals/ddr3/ddr-test-results-{feature_slug(args.sdcard, args.ethernet)}.json"
                     if peripheral_design else ROOT / "build/ddr3/ddr-test-results.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": 1,
        "batch_id": run_id,
        "status": "in_progress",
        "created_utc": utc_now(),
        "memory_mode": "ddr3",
        "sdcard": args.sdcard,
        "ethernet": args.ethernet,
        "peripherals": {"sdcard": args.sdcard, "ethernet": args.ethernet},
        "requested_profiles": profiles,
        "requested_training_runs": args.training_runs,
        "requested_stress_seconds": args.stress_seconds,
        "thorough_criteria": {
            "training_runs_per_profile": THOROUGH_TRAINING_RUNS,
            "stress_seconds_per_profile": THOROUGH_STRESS_SECONDS,
            "full_geometry_bytes": DDR_SIZE_BYTES,
        },
        "port": args.port,
        "profile_results": {},
    }
    manifest_path.write_text(json.dumps(summary, indent=2) + "\n")
    outcome = 0
    try:
        if not Path(args.port).exists() or not os.access(args.port, os.R_OK | os.W_OK):
            raise RuntimeError(f"explicit UART port is unavailable or inaccessible: {args.port}")
        for profile in profiles:
            session, result = run_profile(
                profile, args.port, run_id, args.training_runs, args.stress_seconds,
                args.handshake_timeout, args.trial_timeout, evidence_root, profiles,
                sdcard=args.sdcard, ethernet=args.ethernet,
                board_serial=args.board_serial, board_revision=args.board_revision,
            )
            summary["profile_results"][profile] = {
                "status": session["status"],
                "acceptance": session.get("acceptance"),
                "actual_training_runs": session.get("actual_training_runs", 0),
                "evidence": session.get("evidence"),
                "error": session.get("error"),
            }
            manifest_path.write_text(json.dumps(summary, indent=2) + "\n")
            if session["status"] != "passed":
                outcome = result
                break
        passed = len(summary["profile_results"]) == len(profiles) and all(
            item["status"] == "passed" for item in summary["profile_results"].values()
        )
        partial = len(summary["profile_results"]) == len(profiles) and all(
            item["status"] in ("passed", "partial") for item in summary["profile_results"].values()
        )
        summary["status"] = "passed" if passed else "partial" if partial else "failed"
        summary["acceptance"] = "thorough" if passed else "partial" if partial else "failed"
    except KeyboardInterrupt:
        summary["status"] = "interrupted"
        summary["error"] = "KeyboardInterrupt"
        outcome = 130
    except BaseException as error:
        summary["status"] = "failed"
        summary["error"] = f"{type(error).__name__}: {error}"
        outcome = 1
    finally:
        summary["finished_utc"] = utc_now()
        manifest_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "passed" else 130 if outcome == 130 else 2 if outcome == 2 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
