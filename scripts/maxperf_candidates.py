#!/usr/bin/env python3
"""Generate and route the bounded, project-integrated maxperf candidate matrix."""

import datetime
import itertools
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_build  # noqa: E402
import build as build_module  # noqa: E402
import compare as compare_module  # noqa: E402
from cpu_candidates import (GENERATOR, LOCK, java_runtime_check, sha256,
                            source_revision_check)  # noqa: E402
from gateware.soc import MEMORY_MODES  # noqa: E402


def candidate_matrix(memory):
    if memory not in MEMORY_MODES:
        raise ValueError(f"unknown memory mode {memory!r}; choose from {', '.join(MEMORY_MODES)}")
    if memory == "onchip":
        icaches, dcaches = (2048, 4096), (2048, 4096)
    else:
        icaches, dcaches = (4096, 8192, 16384), (2048, 4096, 8192)
    return [
        {
            "candidate_id": f"icache-{icache}_dcache-{dcache}_rv32im{'c' if compressed else ''}",
            "memory_mode": memory,
            "cpu_configuration": {
                "cpu_variant": "projectimc" if compressed else "projectim",
                "isa": "rv32i2p0_mc" if compressed else "rv32i2p0_m",
                "instruction_cache_bytes": icache,
                "data_cache_bytes": dcache,
                "compressed": compressed,
                "prediction": "dynamic_target",
                "mul_div": True,
                "single_cycle_mul_div": True,
                "single_cycle_shift": True,
                "bypass": True,
                "csr_plugin_config": "small",
                "atomics": False,
                "debug": False,
                "cfu": False,
                "pmp_regions": 0,
                "clock_hz": 48_000_000,
            },
        }
        for icache, dcache, compressed in itertools.product(icaches, dcaches, (False, True))
    ]


def generator_arguments(row, output_file):
    config = LOCK["standard_configuration"]
    output_prefix = os.path.relpath(Path(output_file).with_suffix(""), GENERATOR)
    cpu = row["cpu_configuration"]
    args = [
        "runMain", "vexriscv.GenCoreDefault",
        f"--hardwareBreakpointCount={config['hardware_breakpoints']}",
        f"--iCacheSize={cpu['instruction_cache_bytes']}",
        f"--dCacheSize={cpu['data_cache_bytes']}",
        f"--pmpRegions={config['pmp_regions']}",
        f"--pmpGranularity={config['pmp_granularity_bytes']}",
        f"--pmpAddressMatchingModes={config['pmp_address_matching_modes']}",
        f"--mulDiv={str(config['mul_div']).lower()}",
        f"--cfu={str(config['cfu']).lower()}",
        f"--singleCycleMulDiv={str(config['single_cycle_mul_div']).lower()}",
        f"--singleCycleShift={str(config['single_cycle_shift']).lower()}",
        f"--relaxedPcCalculation={str(config['relaxed_pc_calculation']).lower()}",
        f"--bypass={str(config['bypass']).lower()}",
        f"--externalInterruptArray={str(config['external_interrupt_array']).lower()}",
        f"--csrPluginConfig={config['csr_plugin_config']}",
        "--atomics=false", f"--compressedGen={str(cpu['compressed']).lower()}",
        f"--dBusCachedRelaxedMemoryTranslationRegister={str(config['dcache_relaxed_memory_translation_register']).lower()}",
        f"--dBusCachedEarlyWaysHits={str(config['dcache_early_way_hits']).lower()}",
        "--prediction=dynamic_target", f"--outputFile={output_prefix}",
    ]
    return args


def generate_candidate(row, output_dir, revisions, tools):
    output_dir.mkdir(parents=True, exist_ok=True)
    rtl = output_dir / "VexRiscv.v"
    args = generator_arguments(row, rtl)
    command = ["sbt", "--no-server", "compile", " ".join(args)]
    result = subprocess.run(command, cwd=GENERATOR, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (output_dir / "generator.log").write_text(result.stdout)
    if result.returncode:
        raise RuntimeError(f"SBT generation failed; see {output_dir / 'generator.log'}")
    if not rtl.is_file() or "module VexRiscv" not in rtl.read_text(errors="replace"):
        raise RuntimeError("generator did not produce the LiteX VexRiscv top module")
    config = row["cpu_configuration"]
    manifest = {
        "schema": 1,
        **row,
        "status": "generated",
        "generator_command": command,
        "generator_arguments": args[2:],
        "generator_repository": revisions,
        "tool_versions": tools,
        "generator_lock_sha256": sha256(ROOT / "cpu-generator.lock.json"),
        "rtl": str(rtl.relative_to(ROOT)),
        "rtl_sha256": sha256(rtl),
        "compiler_flags": ["-march=" + config["isa"], "-mabi=ilp32"],
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    (output_dir / "candidate.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def candidate_output(memory, candidate_id):
    return ROOT / "build/maxperf-candidates" / memory / candidate_id


def build_candidate(row, memory, revisions, tools):
    candidate_id = row["candidate_id"]
    output = candidate_output(memory, candidate_id)
    result = {"candidate_id": candidate_id, "memory_mode": memory, "status": "in_progress"}
    try:
        manifest = generate_candidate(row, output, revisions, tools)
        result["generation"] = {k: manifest.get(k) for k in
                                ("rtl", "rtl_sha256", "generator_command", "generator_arguments",
                                 "generator_repository", "tool_versions", "compiler_flags")}
        soc_dir = output / "soc"
        build = build_module.build_profile(
            "standard", memory=memory, force=True, output_dir=soc_dir,
            cpu_rtl=ROOT / manifest["rtl"], cpu_candidate=candidate_id,
        )
        result["build"] = build
        result["resources_and_timing"] = compare_module.parse_profile(
            "standard", memory=memory, build_dir=soc_dir,
        )
        result["benchmark_firmware"] = benchmark_build.build_profile_firmware(
            "standard", build, memory=memory, build_dir=soc_dir,
        )
        result["status"] = "ready_for_board_measurement"
    except Exception as error:
        result["status"] = "rejected_or_blocked"
        result["reason"] = f"{type(error).__name__}: {error}"
        result["traceback"] = traceback.format_exc(limit=8)
    result["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    output.mkdir(parents=True, exist_ok=True)
    (output / "candidate-build.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def build_matrix(memory):
    rows = candidate_matrix(memory)
    revisions = source_revision_check()
    tools = java_runtime_check()
    batch_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + f"-{memory}-maxperf-build"
    evidence = ROOT / "docs/performance/maxperf-evaluation" / batch_id
    evidence.mkdir(parents=True, exist_ok=False)
    report = {"schema": 1, "batch_id": batch_id, "memory_mode": memory,
              "clock_hz": 48_000_000, "status": "in_progress", "candidates": {},
              "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    path = ROOT / "build/maxperf-candidates" / memory / "candidate-build.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    for index, row in enumerate(rows, 1):
        print(f"Building {memory} candidate {index}/{len(rows)}: {row['candidate_id']}", flush=True)
        result = build_candidate(row, memory, revisions, tools)
        report["candidates"][row["candidate_id"]] = result
        path.write_text(json.dumps(report, indent=2) + "\n")
    report["candidate_count"] = len(rows)
    report["status"] = ("ready_for_board_measurement"
                        if all(item["status"] == "ready_for_board_measurement"
                               for item in report["candidates"].values()) else "incomplete")
    report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report["evidence_directory"] = str(evidence.relative_to(ROOT))
    path.write_text(json.dumps(report, indent=2) + "\n")
    (evidence / "candidate-build.json").write_text(json.dumps(report, indent=2) + "\n")
    for candidate_id, item in report["candidates"].items():
        source = candidate_output(memory, candidate_id)
        destination = evidence / candidate_id
        destination.mkdir()
        for name in ("candidate.json", "candidate-build.json", "generator.log"):
            file = source / name
            if file.is_file():
                (destination / name).write_bytes(file.read_bytes())
        soc = source / "soc"
        for name, target in (("build-metadata.json", "build-metadata.json"),
                             ("project.rpt.txt", "project.rpt.txt"), ("project.tr", "project.tr"),
                             ("project_syn_resource.html", "project_syn_resource.html")):
            candidates = list(soc.glob(f"**/{name}"))
            if candidates:
                (destination / target).write_bytes(candidates[0].read_bytes())
        item["persistent_evidence"] = str(destination.relative_to(ROOT))
    path.write_text(json.dumps(report, indent=2) + "\n")
    (evidence / "candidate-build.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"batch_id": batch_id, "memory_mode": memory,
                      "status": report["status"], "candidate_count": len(rows),
                      "evidence": report["evidence_directory"]}, indent=2))
    return report


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", choices=MEMORY_MODES, default="onchip")
    args = parser.parse_args(argv)
    try:
        report = build_matrix(args.memory)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"maxperf candidate build failed: {error}", file=sys.stderr)
        return 1
    return 0 if report["status"] == "ready_for_board_measurement" else 1


if __name__ == "__main__":
    sys.exit(main())
