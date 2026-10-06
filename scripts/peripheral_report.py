#!/usr/bin/env python3
"""Build an identity-checked SD/Ethernet build and hardware evidence matrix."""

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gateware.soc import MEMORY_MODES, PROFILES  # noqa: E402
from scripts.build import source_fingerprint  # noqa: E402
from scripts.peripheral_config import peripheral_build_dir  # noqa: E402
from scripts.peripheral_evidence import validate_session  # noqa: E402

FEATURES = (("spi", "none"), ("none", "rmii"), ("spi", "rmii"))
EVIDENCE_ROOT = ROOT / "docs/peripherals/evidence"


def file_identity(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size,
            "sha256": digest.hexdigest()}


def collect_build(profile, memory, sdcard, ethernet, current_fingerprint):
    directory = peripheral_build_dir(ROOT, memory, profile, sdcard, ethernet)
    metadata_path = directory / "build-metadata.json"
    if not metadata_path.is_file():
        return {"status": "not_built", "path": str(directory.relative_to(ROOT))}
    try:
        metadata = json.loads(metadata_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        return {"status": "invalid_metadata", "error": str(error),
                "path": str(directory.relative_to(ROOT))}
    expected_features = {"sdcard": sdcard, "ethernet": ethernet}
    if (metadata.get("profile") != profile or metadata.get("memory_mode") != memory
            or metadata.get("peripherals") != expected_features):
        return {"status": "identity_mismatch", "path": str(directory.relative_to(ROOT))}
    artifacts = {}
    for label, path in {
        "bitstream": directory / "bitstream.fs",
        "firmware": directory / "firmware/demo.bin",
    }.items():
        if path.is_file():
            artifacts[label] = file_identity(path)
    if metadata.get("status") == "passed" and set(artifacts) != {"bitstream", "firmware"}:
        return {"status": "artifact_missing", "path": str(directory.relative_to(ROOT)),
                "artifacts": artifacts}
    if (metadata.get("status") == "passed"
            and any(artifacts[name]["sha256"] != metadata.get(f"{name}_sha256")
                    for name in ("bitstream", "firmware"))):
        return {"status": "artifact_hash_mismatch", "path": str(directory.relative_to(ROOT)),
                "artifacts": artifacts}
    status = metadata.get("status", "unknown")
    if status in ("passed", "failed") and metadata.get("base_source_fingerprint") != current_fingerprint:
        status = "stale_source"
    gateware_results = metadata.get("gateware_results") or collect_partial_gateware(directory)
    timing_gate = metadata.get("timing_gate")
    if timing_gate is None:
        pnr_report = directory / "gateware/impl/pnr/project.rpt.txt"
        if gateware_results and gateware_results.get("status") == "synthesis_failed":
            timing_gate = "failed_synthesis"
        elif pnr_report.is_file() and "Failed to place" in pnr_report.read_text(errors="replace"):
            timing_gate = "failed_placement"
        else:
            timing_gate = "unavailable"
    if status == "passed" and timing_gate != "passed":
        status = "timing_failed"
    return {
        "status": status,
        "source_fingerprint": metadata.get("source_fingerprint"),
        "current_source_fingerprint": current_fingerprint,
        "cpu_variant": metadata.get("cpu_variant"),
        "cpu_rtl": metadata.get("cpu_rtl"),
        "cpu_configuration": metadata.get("cpu_configuration"),
        "software_capability": metadata.get("software_capability"),
        "memory_map": metadata.get("memory") or metadata.get("address_map"),
        "address_map": metadata.get("address_map"),
        "peripheral_clocks": metadata.get("peripheral_clocks"),
        "gateware_results": gateware_results,
        "timing_gate": timing_gate,
        "artifact_hashes": {name: item["sha256"] for name, item in artifacts.items()},
        "artifacts": artifacts,
        "error": metadata.get("error"),
        "path": str(directory.relative_to(ROOT)),
        "build_record": metadata,
    }


def collect_partial_gateware(directory):
    """Retain utilization from PnR placement failures without faking timing."""
    report = Path(directory) / "gateware/impl/pnr/project.rpt.txt"
    if not report.is_file():
        synthesis_log = Path(directory) / "gateware/impl/gwsynthesis/project.log"
        if not synthesis_log.is_file():
            return None
        text = synthesis_log.read_text(errors="replace")
        overflow = re.search(
            r"ERROR\s*\(RP0006\).*?number\((\d+)\((\d+) LUTs?,\s*(\d+) ALUs?,\s*"
            r"\d+ ROM16s?,\s*(\d+) SSRAMs?\)\).*?resource limit\((\d+)\)",
            text, re.S,
        )
        if not overflow:
            return None
        logic, lut, alu, ssram, limit = (int(value) for value in overflow.groups())
        return {
            "status": "synthesis_failed",
            "resources": {
                "logic": {"used": logic, "capacity": limit},
                "lut": {"used": lut},
                "alu": {"used": alu},
                "ssram_estimate": {"used": ssram},
            },
            "synthesis_error": next(
                (line.strip() for line in text.splitlines() if "RP0006" in line), None
            ),
            "evidence": {"synthesis_log": str(synthesis_log.relative_to(ROOT))},
        }
    text = report.read_text(errors="replace")
    result = {"status": "partial", "resources": {}, "evidence": {
        "pnr_resource_report": str(report.relative_to(ROOT)),
    }}
    logic = re.search(r"^\s*Logic\s+\|\s+(\d+)/(\d+)", text, re.M)
    lut = re.search(r"^\s*--LUT,ALU,ROM16\s+\|\s+\d+\((\d+) LUT,\s*(\d+) ALU", text, re.M)
    registers = re.search(r"^\s*Register\s+\|\s+(\d+)/(\d+)", text, re.M)
    bsram = re.search(r"^\s*BSRAM\s+\|\s+(\d+)/(\d+)", text, re.M)
    if logic:
        result["resources"]["logic"] = {"used": int(logic.group(1)), "capacity": int(logic.group(2))}
    if lut:
        result["resources"]["lut"] = {"used": int(lut.group(1))}
        result["resources"]["alu"] = {"used": int(lut.group(2))}
    if registers:
        result["resources"]["registers"] = {"used": int(registers.group(1)), "capacity": int(registers.group(2))}
    if bsram:
        result["resources"]["bsram"] = {"used": int(bsram.group(1)), "capacity": int(bsram.group(2))}
    result["placement_error"] = next((line.strip() for line in text.splitlines()
                                       if "Failed to place" in line), None)
    return result


def collect_sessions(build, row):
    if build.get("status") != "passed":
        return []
    matches = []
    if not EVIDENCE_ROOT.is_dir():
        return matches
    identity = {
        "profile": row["profile"], "memory": row["memory"],
        "sdcard": row["sdcard"], "ethernet": row["ethernet"],
        "build_record": build["build_record"],
        "artifact_hashes": build["artifact_hashes"],
    }
    for path in sorted(EVIDENCE_ROOT.iterdir()):
        if not path.is_dir():
            continue
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        recorded = manifest.get("build", {})
        if any(recorded.get(key) != identity[key]
               for key in ("profile", "memory", "sdcard", "ethernet")):
            continue
        try:
            checked = validate_session(path, identity, ROOT)
        except (OSError, ValueError, TypeError, KeyError) as error:
            checked = {"status": "invalid", "session": str(path.relative_to(ROOT)),
                       "criteria": {}, "errors": [str(error)]}
        if checked.get("status") != "invalid" or checked.get("errors"):
            matches.append(checked)
    return matches


def hardware_status(sessions, criterion, not_applicable, pending):
    if not_applicable:
        return "not_applicable"
    if any(item.get("criteria", {}).get(criterion) == "passed" for item in sessions):
        return "passed"
    if any(item.get("status") == "invalid" for item in sessions):
        return "invalid_evidence"
    if sessions:
        return "incomplete_evidence"
    return pending


def main():
    current_fingerprint = source_fingerprint()
    matrix = {}
    evidence_sessions = []
    for memory in MEMORY_MODES:
        for profile in PROFILES:
            for sdcard, ethernet in FEATURES:
                row = {"profile": profile, "memory": memory,
                       "sdcard": sdcard, "ethernet": ethernet}
                key = f"{memory}/{profile}/sd-{sdcard}/eth-{ethernet}"
                build = collect_build(profile, memory, sdcard, ethernet, current_fingerprint)
                sessions = collect_sessions(build, row)
                evidence_sessions.extend(sessions)
                row["build"] = {key: value for key, value in build.items() if key != "build_record"}
                row["sessions"] = sessions
                sd_na = sdcard != "spi"
                eth_na = ethernet != "rmii"
                timing = (build.get("gateware_results") or {}).get("timing", {})
                row["hardware"] = {
                    "sd": hardware_status(sessions, "sd", sd_na, "pending_board_and_test_volume"),
                    "static_network": hardware_status(sessions, "static_network", eth_na,
                                                       "pending_board_and_test_host"),
                    "dhcp": hardware_status(sessions, "dhcp", eth_na, "pending_dhcp_service"),
                    "udp_tcp": hardware_status(sessions, "udp_tcp", eth_na,
                                                "pending_board_and_test_host"),
                    "combined_1800s": hardware_status(
                        sessions, "combined_1800s", sd_na or eth_na,
                        "pending_board_media_and_network_peer"),
                    "ddr_qualification": hardware_status(
                        sessions, "ddr_qualification", memory != "ddr3",
                        "historical_only_no_matching_peripheral_image_test"),
                    "final_recovery": hardware_status(
                        sessions, "final_recovery", False, "pending_board_recovery"),
                }
                row["timing"] = {
                    "status": build.get("timing_gate", "unavailable"),
                    "setup_slack_ns": timing.get("worst_setup_slack_ns"),
                    "hold_slack_ns": timing.get("worst_hold_slack_ns"),
                    "recovery_slack_ns": timing.get("worst_recovery_slack_ns"),
                    "removal_slack_ns": timing.get("worst_removal_slack_ns"),
                    "setup_violated_endpoints": timing.get("setup_violated_endpoints"),
                }
                matrix[key] = row

    result = {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "model_configuration": {
            "requested": {"model": "gpt-6-luna", "reasoning_effort": "xhigh"},
            "available_runtime": "current Codex runtime; model selection cannot be changed in-session",
        },
        "current_source_fingerprint": current_fingerprint,
        "coverage_matrix": matrix,
        "hardware_evidence_sessions": evidence_sessions,
    }
    docs = ROOT / "docs/peripherals"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "results.json").write_text(json.dumps(result, indent=2) + "\n")

    lines = [
        "# SD and Ethernet peripheral qualification",
        "",
        "Build results are bound to the current source fingerprint and artifact hashes. Timing slack is reported separately from build completion. A hardware pass requires a validated session manifest, exact image identity, board serial/revision, and hash-verified captures.",
        "",
        "## Build and hardware matrix",
        "",
        "| Memory | Profile | SD | Ethernet | Build | Software level | Timing | LUT / logic | Registers | BSRAM | RMII ref | Setup/hold/recovery/removal (ns) | SD | Static | DHCP | UDP/TCP | Combined 1800 s | DDR qualification | Recovery |",
        "|---|---|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|---|---|---|",
    ]
    for row in matrix.values():
        build = row["build"]
        timing = row["timing"]
        gateware = build.get("gateware_results") or {}
        resources = gateware.get("resources", {})
        logic = resources.get("logic", {})
        registers = resources.get("registers", {})
        bsram = resources.get("bsram", {})
        lut = resources.get("lut", {})
        rmii_frequency = gateware.get("rmii_clock_constraint", {}).get("frequency_mhz")
        slacks = "/".join(str(timing.get(key)) for key in (
            "setup_slack_ns", "hold_slack_ns", "recovery_slack_ns", "removal_slack_ns"))
        hardware = row["hardware"]
        lines.append(
            f"| {row['memory']} | {row['profile']} | {row['sdcard']} | {row['ethernet']} "
            f"| {build['status']} | {build.get('software_capability', 'unavailable')} "
            f"| {timing['status']} | {lut.get('used', '—')}/{logic.get('capacity', '—')} "
            f"| {registers.get('used', '—')}/{registers.get('capacity', '—')} "
            f"| {bsram.get('used', '—')}/{bsram.get('capacity', '—')} "
            f"| {rmii_frequency if rmii_frequency is not None else '—'} MHz | {slacks} "
            f"| {hardware['sd']} | {hardware['static_network']} "
            f"| {hardware['dhcp']} | {hardware['udp_tcp']} | {hardware['combined_1800s']} "
            f"| {hardware['ddr_qualification']} | {hardware['final_recovery']} |"
        )
    lines.extend([
        "",
        "## Model configuration",
        "",
        "The handoff requests Luna (`gpt-6-luna`) with xhigh reasoning. This runtime did not expose a model-switch control, so the request is recorded without claiming it changed the active model.",
        "",
        "## Attached hardware and media",
        "",
        "Core v3961 / Dock v3714, no unique printed serial, LAN connected with DHCP. The user requested exFAT support for the installed 32 GB card. The application now enables FAT16/FAT32/exFAT with UTF-8 names; formatting remains disabled. File and combined passes require actual captured board verification. See [hardware observations](hardware-observations.md).",
        "",
        "## Evidence rules",
        "",
        "The validator in `scripts/peripheral_evidence.py` accepts hardware criteria only when a session matches the current profile, memory mode, features, source fingerprint, bitstream hash, and firmware hash. It rejects missing/changed capture files, evidence outside the session directory, an unknown board revision, missing UART/programmer records, incomplete measurements, and results shorter than the required workloads. Session files belong under `docs/peripherals/evidence/<session>/`.",
        "",
        "## Session details",
        "",
    ])
    if evidence_sessions:
        for session in evidence_sessions:
            diagnostic_note = ("; on-chip controller/UART diagnostic passed (filesystem/network services absent)"
                               if session.get("diagnostic_observation") else "")
            lines.append(f"- `{session.get('session')}`: {session.get('status')}{diagnostic_note}; "
                         f"criteria `{json.dumps(session.get('criteria', {}), sort_keys=True)}`; "
                         f"errors: {'; '.join(session.get('errors', [])) or 'none'}.")
    else:
        lines.append("No peripheral board evidence sessions have been recorded.")
    lines.append("")
    (docs / "report.md").write_text("\n".join(lines))
    print(f"Wrote {docs / 'report.md'} and {docs / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
