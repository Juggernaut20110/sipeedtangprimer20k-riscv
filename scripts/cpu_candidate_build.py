#!/usr/bin/env python3
"""Generate, route, and prepare CoreMark images for both CPU candidates."""

import datetime
import json
import shutil
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_build  # noqa: E402
import build as build_module  # noqa: E402
import compare as compare_module  # noqa: E402
from cpu_candidates import CANDIDATES, generate  # noqa: E402


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def run_candidate(candidate):
    result = {"candidate": candidate, "status": "in_progress"}
    try:
        generated = generate(candidate)
        candidate_root = ROOT / "build/cpu-candidates" / candidate
        soc_dir = candidate_root / "soc"
        build = build_module.build_profile(
            "standard", memory="onchip", force=True, output_dir=soc_dir,
            cpu_rtl=ROOT / generated["rtl"], cpu_candidate=candidate, bios_size=24 * 1024,
        )
        result["build"] = build
        resources = compare_module.parse_profile("standard", memory="onchip", build_dir=soc_dir)
        resources["cpu_candidate"] = candidate
        result["resources_and_timing"] = resources
        firmware = benchmark_build.build_profile_firmware(
            "standard", build, memory="onchip", build_dir=soc_dir,
        )
        result["benchmark_firmware"] = firmware
        result["status"] = "ready_for_board_measurement"
    except Exception as error:
        result["status"] = "rejected_or_blocked"
        result["reason"] = f"{type(error).__name__}: {error}"
        result["traceback"] = traceback.format_exc(limit=8)
    result["finished_utc"] = utc_now()
    path = ROOT / "build/cpu-candidates" / candidate / "candidate-build.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def publish_build_evidence(report):
    """Retain compact candidate manifests and vendor reports outside build/."""
    evidence_root = ROOT / "docs/performance/cpu-evaluation"
    published = {
        "schema": 1,
        "status": report.get("status"),
        "system_clock_hz": report.get("system_clock_hz"),
        "bios_rom_bytes": report.get("bios_rom_bytes"),
        "created_utc": report.get("created_utc"),
        "finished_utc": report.get("finished_utc"),
        "candidates": {},
    }
    for name, row in report.get("candidates", {}).items():
        candidate_root = evidence_root / "candidates" / name
        candidate_root.mkdir(parents=True, exist_ok=True)
        build_root = ROOT / "build/cpu-candidates" / name
        soc_root = build_root / "soc"
        artifacts = {
            "generator_manifest": (build_root / "candidate.json", candidate_root / "candidate.json"),
            "generation_log": (build_root / "generator.log", candidate_root / "generator.log"),
            "build_metadata": (soc_root / "build-metadata.json", candidate_root / "build-metadata.json"),
            "pnr_resource_report": (soc_root / "gateware/impl/pnr/project.rpt.txt", candidate_root / "project.rpt.txt"),
            "timing_report": (soc_root / "gateware/impl/pnr/project.tr", candidate_root / "project.tr"),
            "synthesis_resource_report": (soc_root / "gateware/impl/gwsynthesis/project_syn_resource.html", candidate_root / "project_syn_resource.html"),
        }
        links = {}
        for key, (source, destination) in artifacts.items():
            if source.is_file():
                shutil.copy2(source, destination)
                links[key] = str(destination.relative_to(ROOT))

        metrics = row.get("resources_and_timing")
        if isinstance(metrics, dict):
            metrics = json.loads(json.dumps(metrics))
            metrics["evidence"] = {
                key: links[key]
                for key in ("pnr_resource_report", "timing_report", "synthesis_resource_report")
                if key in links
            }
            for group in ("resources",):
                for item in metrics.get(group, {}).values():
                    if isinstance(item, dict) and item.get("source") in row.get("resources_and_timing", {}).get("evidence", {}).values():
                        original = item["source"]
                        item["source"] = next((links[k] for k, value in row["resources_and_timing"]["evidence"].items() if value == original), original)

        build = row.get("build", {})
        benchmark = row.get("benchmark_firmware", {})
        published["candidates"][name] = {
            "candidate": name,
            "status": row.get("status"),
            "reason": row.get("reason"),
            "rtl_sha256": (json.loads((build_root / "candidate.json").read_text()).get("rtl_sha256")
                           if (build_root / "candidate.json").is_file() else None),
            "generator_manifest": links.get("generator_manifest"),
            "build_metadata": links.get("build_metadata"),
            "benchmark_firmware": {
                key: benchmark.get(key)
                for key in ("status", "build_id", "bitstream_sha256", "clock_hz", "memory", "images")
                if key in benchmark
            },
            "resources_and_timing": metrics,
            "evidence": links,
        }
    destination = evidence_root / "candidate-build.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(published, indent=2) + "\n")
    return destination


def main():
    output = ROOT / "build/cpu-candidates/candidate-build.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": 1,
        "status": "in_progress",
        "cpu_profile": "standard-derived candidates; public profile registry unchanged",
        "system_clock_hz": 48_000_000,
        "bios_rom_bytes": 24 * 1024,
        "created_utc": utc_now(),
        "candidates": {},
    }
    output.write_text(json.dumps(report, indent=2) + "\n")
    for candidate in CANDIDATES:
        print(f"Generating and building CPU candidate {candidate}", flush=True)
        report["candidates"][candidate] = run_candidate(candidate)
        output.write_text(json.dumps(report, indent=2) + "\n")
    report["finished_utc"] = utc_now()
    report["status"] = (
        "ready_for_board_measurement"
        if all(item["status"] == "ready_for_board_measurement" for item in report["candidates"].values())
        else "incomplete"
    )
    output.write_text(json.dumps(report, indent=2) + "\n")
    publish_build_evidence(report)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "ready_for_board_measurement" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
