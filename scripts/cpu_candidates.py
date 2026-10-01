#!/usr/bin/env python3
"""Generate reproducible standard-derived VexRiscv prediction candidates."""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / ".deps/pythondata-cpu-vexriscv/pythondata_cpu_vexriscv/verilog"
LOCK_PATH = ROOT / "cpu-generator.lock.json"
LOCK = json.loads(LOCK_PATH.read_text())
CANDIDATES = tuple(LOCK["standard_configuration"]["prediction_candidates"])


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_revision(path):
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def source_revision_check():
    repo = ROOT / ".deps/pythondata-cpu-vexriscv"
    actual = git_revision(repo)
    expected = LOCK["generator_repository"]["commit"]
    if actual != expected:
        raise RuntimeError(f"VexRiscv generator checkout is {actual}, expected {expected}")
    submodule = repo / LOCK["generator_repository"]["submodule"]
    if not submodule.is_dir() or not (submodule / ".git").exists():
        raise RuntimeError("VexRiscv source submodule is missing; run make setup to initialize pinned dependencies")
    actual_submodule = git_revision(submodule)
    expected_submodule = LOCK["generator_repository"]["submodule_commit"]
    if actual_submodule != expected_submodule:
        raise RuntimeError(f"VexRiscv submodule is {actual_submodule}, expected {expected_submodule}")
    props = (GENERATOR / "project/build.properties").read_text()
    sbt = re.search(r"^sbt.version=(\S+)$", props, re.M)
    build = (GENERATOR / "build.sbt").read_text()
    scala = re.search(r'scalaVersion := "([^"]+)"', build)
    spinal = re.search(r'val spinalVersion = "([^"]+)"', build)
    expected_tools = LOCK["tools"]
    actual_tools = {
        "sbt": sbt.group(1) if sbt else None,
        "scala": scala.group(1) if scala else None,
        "spinalhdl": spinal.group(1) if spinal else None,
    }
    for name, value in actual_tools.items():
        if value != expected_tools[name]:
            raise RuntimeError(f"generator {name} version is {value!r}, expected {expected_tools[name]}")
    return {"generator_commit": actual, "submodule_commit": actual_submodule, **actual_tools}


def java_runtime_check():
    java = shutil.which("java")
    sbt = shutil.which("sbt")
    if java is None or sbt is None:
        missing = ", ".join(name for name, value in (("java", java), ("sbt", sbt)) if value is None)
        raise RuntimeError(f"CPU generation needs the locked Java and SBT tools; missing {missing}")
    result = subprocess.run([java, "-XshowSettings:properties", "-version"], capture_output=True, text=True)
    output = result.stdout + result.stderr
    match = re.search(r"^\s*java\.version\s*=\s*(\S+)", output, re.M)
    artifact_lock = LOCK["artifacts"]
    expected = artifact_lock["java"]["version_output"]
    if result.returncode or not match or match.group(1) != expected:
        actual = match.group(1) if match else "unknown"
        raise RuntimeError(f"Java runtime is {actual}; locked generator runtime is {expected}")
    version = subprocess.run([sbt, "--script-version"], cwd=GENERATOR, capture_output=True, text=True, timeout=60)
    combined = version.stdout + version.stderr
    expected_sbt = artifact_lock["sbt"]["version"]
    if version.returncode or not re.search(rf"(?<![0-9]){re.escape(expected_sbt)}(?![0-9])", combined):
        raise RuntimeError(f"SBT must be version 1.9.7; got {combined.strip()[:300]}")
    try:
        java_executable = Path(java).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        java_executable = Path(java).name
    try:
        sbt_executable = Path(sbt).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        sbt_executable = Path(sbt).name
    return {
        "java": expected,
        "java_executable": java_executable,
        "java_sha256": artifact_lock["java"]["sha256"],
        "sbt": expected_sbt,
        "sbt_executable": sbt_executable,
        "sbt_sha256": artifact_lock["sbt"]["sha256"],
        "scala": LOCK["tools"]["scala"],
        "spinalhdl": LOCK["tools"]["spinalhdl"],
    }


def generator_arguments(prediction, output_file):
    if prediction not in CANDIDATES:
        raise ValueError(f"unsupported candidate {prediction!r}")
    config = LOCK["standard_configuration"]
    # GenCoreDefault passes this directly to SpinalConfig.netlistFileName.
    # Spinal interprets it relative to the generator checkout and prepends "./",
    # so an absolute path would become a broken ./home/... path.
    output_prefix = os.path.relpath(Path(output_file).with_suffix(""), GENERATOR)
    return [
        "runMain", "vexriscv.GenCoreDefault",
        f"--hardwareBreakpointCount={config['hardware_breakpoints']}",
        f"--iCacheSize={config['instruction_cache_bytes']}",
        f"--dCacheSize={config['data_cache_bytes']}",
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
        f"--atomics={str(config['atomics']).lower()}",
        f"--compressedGen={str(config['compressed']).lower()}",
        f"--dBusCachedRelaxedMemoryTranslationRegister={str(config['dcache_relaxed_memory_translation_register']).lower()}",
        f"--dBusCachedEarlyWaysHits={str(config['dcache_early_way_hits']).lower()}",
        f"--prediction={prediction}",
        f"--outputFile={output_prefix}",
    ]


def generate(prediction):
    revisions = source_revision_check()
    tool_versions = java_runtime_check()
    output_dir = ROOT / "build/cpu-candidates" / prediction
    rtl = output_dir / "VexRiscv.v"
    output_dir.mkdir(parents=True, exist_ok=True)
    args = generator_arguments(prediction, rtl)
    run_main = " ".join(args)
    command = ["sbt", "--no-server", "compile", run_main]
    result = subprocess.run(command, cwd=GENERATOR, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (output_dir / "generator.log").write_text(result.stdout)
    if result.returncode:
        raise RuntimeError(f"SBT failed for {prediction}; see {output_dir / 'generator.log'}")
    if not rtl.is_file() or "module VexRiscv" not in rtl.read_text(errors="replace"):
        raise RuntimeError(f"generator did not produce the expected VexRiscv module at {rtl}")
    metadata = {
        "schema": 1,
        "candidate": prediction,
        "status": "generated",
        "generator_command": command,
        "generator_arguments": args[2:],
        "generator_revisions": revisions,
        "tool_versions": tool_versions,
        "cpu_configuration": {
            **{key: value for key, value in LOCK["standard_configuration"].items()
               if key != "prediction_candidates"},
            "prediction": prediction,
            "clock_hz": 48_000_000,
            "profile_basis": "standard",
        },
        "rtl": str(rtl.relative_to(ROOT)),
        "rtl_sha256": sha256(rtl),
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    (output_dir / "candidate.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def ensure_generated(prediction):
    """Return a candidate only when its RTL and complete generation identity verify."""
    revisions = source_revision_check()
    tool_versions = java_runtime_check()
    output_dir = ROOT / "build/cpu-candidates" / prediction
    rtl = output_dir / "VexRiscv.v"
    manifest_path = output_dir / "candidate.json"
    try:
        metadata = json.loads(manifest_path.read_text())
        valid = (
            rtl.is_file()
            and metadata.get("candidate") == prediction
            and metadata.get("rtl_sha256") == sha256(rtl)
            and metadata.get("generator_revisions") == revisions
            and metadata.get("tool_versions") == tool_versions
        )
    except (OSError, ValueError):
        valid = False
    if not valid:
        metadata = generate(prediction)
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", choices=[*CANDIDATES, "all"])
    args = parser.parse_args(argv)
    try:
        candidates = CANDIDATES if args.candidate == "all" else (args.candidate,)
        results = [generate(candidate) for candidate in candidates]
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
        print(f"CPU candidate generation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
