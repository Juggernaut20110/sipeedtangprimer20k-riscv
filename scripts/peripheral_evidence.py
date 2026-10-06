#!/usr/bin/env python3
"""Validate peripheral board evidence against its exact current build."""

import hashlib
import json
from pathlib import Path


def board_identity(serial, revision):
    """Record an observed serial or explicitly confirmed absence without inventing one."""
    if not revision or revision.lower() in ("unknown", "unavailable"):
        raise ValueError("verified core-board/Dock revision is required")
    board = {"model": "Sipeed Tang Primer 20K", "revision": revision}
    if serial == "unavailable":
        board.update(serial=None, serial_status="not_printed_user_confirmed",
                     identity_basis="user_confirmed_attached_board_revision")
    elif serial and serial.lower() not in ("unknown", "none", "factoryaiot pro", "factoryaiot_pro"):
        board.update(serial=serial, serial_status="observed")
    else:
        raise ValueError("provide the observed board serial or 'unavailable' only after confirming no serial is printed")
    return board


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _valid_measurements(name, measurement, memory):
    if not isinstance(measurement, dict):
        return False
    try:
        return _valid_measurements_inner(name, measurement, memory)
    except (TypeError, ValueError):
        return False


def _valid_measurements_inner(name, measurement, memory):
    if name == "sd":
        return (
            isinstance(measurement.get("cid"), str) and measurement["cid"]
            and isinstance(measurement.get("csd"), str) and measurement["csd"]
            and measurement.get("sector_count", 0) > 0
            and 0 < measurement.get("init_hz", 0) <= 400_000
            and 0 < measurement.get("transfer_hz", 0) <= 12_000_000
            and set(measurement.get("verified_file_bytes", [])) >= {512, 4096, 1_048_576}
            and measurement.get("integrity_mismatches") == 0
            and measurement.get("missing_card_bounded_recovery") is True
        )
    if name == "static_network":
        return (
            isinstance(measurement.get("phy_id"), str) and measurement["phy_id"]
            and measurement.get("link") is True
            and measurement.get("static_address")
            and measurement.get("arp") is True
            and measurement.get("ping") is True
            and measurement.get("reconnect_recovered") is True
            and measurement.get("payload_mismatches") == 0
        )
    if name == "dhcp":
        return (
            measurement.get("lease_obtained") is True
            and measurement.get("address")
            and measurement.get("netmask")
            and measurement.get("gateway")
        )
    if name == "udp_tcp":
        return (
            set(measurement.get("udp_payload_bytes", [])) >= {1, 64, 512, 1472}
            and set(measurement.get("tcp_payload_bytes", [])) >= {1, 64, 512, 1472}
            and measurement.get("payload_mismatches") == 0
            and measurement.get("reconnect_recovered") is True
            and isinstance(measurement.get("throughput_bytes_per_second"), (int, float))
            and measurement.get("throughput_bytes_per_second", 0) > 0
            and isinstance(measurement.get("observed_drops"), int)
            and isinstance(measurement.get("rx_errors"), int)
            and isinstance(measurement.get("tx_errors"), int)
        )
    if name == "combined_1800s":
        return (
            memory == "ddr3"
            and measurement.get("duration_seconds", 0) >= 1800
            and measurement.get("upload_bytes") == 1_048_576
            and measurement.get("host_crc32") == measurement.get("card_crc32")
            and measurement.get("file_mismatches") == 0
            and measurement.get("roundtrips", 0) > 0
            and measurement.get("simultaneous_network_packets", 0) > 0
            and measurement.get("unrecovered_hangs") == 0
        )
    if name == "ddr_qualification":
        return (
            memory == "ddr3"
            and measurement.get("training_passes") == 10
            and measurement.get("tested_bytes") == 134_217_728
            and measurement.get("pattern_errors") == 0
            and measurement.get("cache_visibility_errors") == 0
            and measurement.get("stress_seconds", 0) >= 1800
        )
    if name == "final_recovery":
        return (
            measurement.get("sram_programmed") is True
            and measurement.get("ddr_held_in_reset") is True
            and measurement.get("sd_deselected") is True
            and measurement.get("sd_clock_stopped") is True
            and measurement.get("phy_held_in_reset") is True
            and measurement.get("uart_bytes") == 0
            and isinstance(measurement.get("idle_bitstream_sha256"), str)
            and len(measurement["idle_bitstream_sha256"]) == 64
            and measurement.get("uart_quiet_seconds", 0) >= 5
        )
    return False


def validate_session(session_path, build, root):
    """Return a strict validation record; unproven or stale criteria never pass."""
    session_path = Path(session_path).resolve()
    root = Path(root).resolve()
    errors = []
    try:
        session_path.relative_to((root / "docs/peripherals/evidence").resolve())
    except ValueError:
        return {"status": "invalid", "errors": ["session is outside docs/peripherals/evidence"]}
    manifest_path = session_path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        return {"status": "invalid", "errors": [f"cannot read manifest: {error}"]}

    if manifest.get("schema_version") != 1:
        errors.append("unsupported manifest schema")
    board = manifest.get("board", {})
    no_printed_serial = (
        board.get("serial") is None
        and board.get("serial_status") == "not_printed_user_confirmed"
        and board.get("identity_basis") == "user_confirmed_attached_board_revision"
    )
    if (board.get("model") != "Sipeed Tang Primer 20K" or not board.get("revision")
            or (not board.get("serial") and not no_printed_serial)):
        errors.append("board model, verified revision, or explicit serial observation is missing")

    identity = manifest.get("build", {})
    for key in ("profile", "memory", "sdcard", "ethernet"):
        if identity.get(key) != build.get(key):
            errors.append(f"build {key} does not match the report row")
    build_record = build.get("build_record", {})
    application_identity_matches = (
        identity.get("source_fingerprint") == build_record.get("source_fingerprint")
        and identity.get("firmware_sha256") == build.get("artifact_hashes", {}).get("firmware"))
    ddr_reuse = any(
        record.get("previous_source_fingerprint") == identity.get("source_fingerprint")
        and record.get("previous_firmware_sha256") == identity.get("firmware_sha256")
        and record.get("bitstream_sha256") == identity.get("bitstream_sha256")
        and record.get("bitstream_sha256") == build.get("artifact_hashes", {}).get("bitstream")
        and record.get("synthesis_inputs") == build_record.get("synthesis_inputs")
        and bool(record.get("synthesis_inputs"))
        for record in build_record.get("gateware_reuse_history", []))
    if not application_identity_matches and not ddr_reuse:
        errors.append("source or firmware fingerprint does not match current build")
    expected_hashes = build.get("artifact_hashes", {})
    for key in ("bitstream", "firmware"):
        if identity.get(f"{key}_sha256") != expected_hashes.get(key) and not (key == "firmware" and ddr_reuse):
            errors.append(f"{key} identity does not match current build")

    artifacts = {}
    for record in manifest.get("artifacts", []):
        name = record.get("name")
        relative = record.get("path")
        if not name or not relative or name in artifacts:
            errors.append("capture artifact has a missing or duplicate name/path")
            continue
        path = (session_path / relative).resolve()
        try:
            path.relative_to(session_path)
        except ValueError:
            errors.append(f"capture {name} leaves its evidence session")
            continue
        if (not path.is_file() or path.stat().st_size != record.get("bytes")
                or sha256(path) != record.get("sha256")):
            errors.append(f"capture {name} is missing or its SHA-256 does not match")
            continue
        if name in ("uart_rx", "uart_tx", "programmer_log", "host_log") and path.stat().st_size == 0:
            errors.append(f"required capture {name} is empty")
            continue
        artifacts[name] = record["sha256"]

    criteria = {}
    raw_criteria = manifest.get("criteria", {})
    applicable = []
    if build.get("sdcard") == "spi":
        applicable.append("sd")
    if build.get("ethernet") == "rmii":
        applicable.extend(("static_network", "dhcp", "udp_tcp"))
    if build.get("sdcard") == "spi" and build.get("ethernet") == "rmii":
        applicable.append("combined_1800s")
    if build.get("memory") == "ddr3":
        applicable.append("ddr_qualification")
    applicable.append("final_recovery")
    has_uart = {"uart_rx", "uart_tx"} <= artifacts.keys()
    has_programmer = "programmer_log" in artifacts
    has_host_log = "host_log" in artifacts
    for name in applicable:
        item = raw_criteria.get(name, {})
        passed = (
            not errors
            and (application_identity_matches or (ddr_reuse and name in ("ddr_qualification", "final_recovery")))
            and item.get("status") == "passed"
            and has_uart
            and has_programmer
            and (name in ("sd", "ddr_qualification", "final_recovery") or has_host_log)
            and _valid_measurements(name, item.get("measurements", {}), build.get("memory"))
            and bool(item.get("evidence", []))
            and set(item.get("evidence", [])) <= artifacts.keys()
        )
        if passed and name == "final_recovery":
            measurements = item.get("measurements", {})
            passed = ({"recovery_programmer", "recovery_uart", "recovery_record", "recovery_bitstream"}
                      <= artifacts.keys()
                      and artifacts.get("recovery_bitstream") == measurements.get("idle_bitstream_sha256"))
            if passed:
                records = {r["name"]: r for r in manifest["artifacts"]}
                recovery = json.loads((session_path / records["recovery_record"]["path"]).read_text())
                passed = (records["recovery_uart"]["bytes"] == 0
                          and recovery.get("programmer_exit_code") == 0
                          and recovery.get("status") == "passed"
                          and recovery.get("board") == board
                          and recovery.get("idle_bitstream_sha256") == artifacts["recovery_bitstream"])
        criteria[name] = "passed" if passed else "incomplete_or_invalid"
    diagnostic = None
    if (application_identity_matches and not errors and has_uart and has_programmer
            and build.get("memory") == "onchip"
            and manifest.get("diagnostic_observation", {}).get("status") == "passed"):
        rx_record = next(r for r in manifest["artifacts"] if r["name"] == "uart_rx")
        received = (session_path / rx_record["path"]).read_bytes()
        expected = (f'Peripheral diagnostic: profile={build["profile"]} '
                    f'SD_SPI={int(build.get("sdcard")=="spi")} '
                    f'ETHERNET_RMII={int(build.get("ethernet")=="rmii")}').encode()
        if (expected in received
                and (build.get("sdcard") != "spi" or b"LiteX SPI SD controller: present" in received)
                and (build.get("ethernet") != "rmii" or b"RX slots=2 TX slots=2 slot bytes=2048" in received)):
            diagnostic = manifest["diagnostic_observation"]
    return {
        "status": "passed" if criteria and all(value == "passed" for value in criteria.values()) else "incomplete",
        "session": str(session_path.relative_to(root)),
        "criteria": criteria,
        "measurements": {name: raw_criteria.get(name, {}).get("measurements", {}) for name in applicable},
        "diagnostic_observation": diagnostic,
        "errors": errors,
    }
