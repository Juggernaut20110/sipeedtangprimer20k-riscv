#!/usr/bin/env python3
"""Generate, compile, and Gowin-build one isolated CPU profile."""

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
sys.path.insert(0, str(ROOT))
from gateware.soc import (  # noqa: E402
    DDR_BIOS_SIZES, DDR_L2_SIZE, DDR_SIZE_BYTES, PROFILES, ProjectSoC,
    SYS_CLK_FREQ, UART_BAUDRATE,
)
from scripts.memory import profile_build_dir, validate_memory  # noqa: E402
from scripts.cpu_profiles import configuration_for  # noqa: E402
from gateware.ddr_geometry import DDR_PART, DDR_ROW_BITS, DDR_BANK_BITS, DDR_COLUMN_BITS
from cpu_candidates import ensure_generated  # noqa: E402
from litex.soc.integration.builder import Builder  # noqa: E402


def source_fingerprint():
    digest = hashlib.sha256()
    for relative in [
        "gateware/soc.py", "gateware/ddr3.py", "gateware/ddr_geometry.py",
        "firmware/main.c", "firmware/app_logic.c",
        "gateware/profile_selection.py",
        "firmware/app_logic.h", "firmware/linker.ld", "firmware/Makefile",
        "scripts/build.py", "scripts/project.py", "scripts/memory.py",
        "scripts/cpu_candidates.py", "scripts/cpu_profiles.py", "dependencies.lock.json", "cpu-generator.lock.json",
        "scripts/maxperf_candidates.py",
        "cpu-profile-selection.json", "requirements-py312.txt",
        "patches/litex-sdram-training-status.patch",
        "patches/litex-sdram-read-capture-diagnostic.patch",
        "patches/litex-memtest-read-only-diagnostic.patch",
        "patches/litex-ddr-diagnostic-boot.patch",
        "patches/litex-project-vexriscv-isa-variants.patch",
        "patches/litex-gowin-extra-sdc.patch",
        "patches/litedram-gw2ddrphy-cdc.patch",
        "patches/litedram-gw2ddrphy-dll-off-read.patch",
    ]:
        digest.update((ROOT / relative).read_bytes())
    maxperf_selection = ROOT / "maxperf-profile-selection.json"
    if maxperf_selection.is_file():
        digest.update(maxperf_selection.read_bytes())
    return digest.hexdigest()


def builder_for(soc, output_dir, compile_software=True, compile_gateware=True):
    output_dir = Path(output_dir).resolve()
    return Builder(
        soc,
        output_dir=str(output_dir),
        gateware_dir=str(output_dir / "gateware"),
        software_dir=str(output_dir / "software"),
        include_dir=str(output_dir / "software/include"),
        csr_csv=str(output_dir / "csr.csv"),
        csr_json=str(output_dir / "csr.json"),
        compile_software=compile_software,
        compile_gateware=compile_gateware,
        bios_console="lite",
        bios_stack_margin=2048,
        integrated_rom_auto_size=False,
    )


def parse_elf_sections(elf, triple):
    triples = (triple,) if isinstance(triple, str) else tuple(triple)
    readelf = next((shutil.which(candidate + "-readelf") for candidate in triples
        if shutil.which(candidate + "-readelf")), None)
    if not readelf:
        raise RuntimeError(f"cannot find a RISC-V readelf for any supported triple: {triples}")
    output = subprocess.check_output([readelf, "-W", "-S", str(elf)], text=True)
    sections = {}
    pattern = re.compile(r"^\s*\[\s*\d+\]\s+(\S+)\s+(\S+)\s+[0-9a-fA-F]+\s+[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+", re.M)
    for name, section_type, size in pattern.findall(output):
        sections[name] = {"type": section_type, "size": int(size, 16)}
    return sections


def read_main_ram_base(mem_header):
    text = Path(mem_header).read_text()
    for macro in ("MAIN_RAM_BASE", "MAIN_RAM_BASE_VA"):
        match = re.search(rf"^#define\s+{macro}\s+(0x[0-9a-fA-F]+|[0-9]+)", text, re.M)
        if match:
            return int(match.group(1), 0)
    raise RuntimeError("generated mem.h does not define the main RAM base")


def first_version_line(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"unavailable: {error}"
    output = (result.stdout + result.stderr).splitlines()
    for line in output:
        if "gowin" in line.lower() or "openfpgaloader" in line.lower() or "version" in line.lower():
            return line.strip()[:200]
    return f"exit {result.returncode}: {output[0][:160] if output else 'no output'}"


def gowin_report_version(report_path):
    match = re.search(r"^\s*<Tool Version>:\s*(\S+)", Path(report_path).read_text(), re.M)
    if not match:
        raise RuntimeError(f"Gowin report did not identify its tool version: {report_path}")
    return match.group(1)


def apply_project_patch(dependency_name, patch_name):
    dependency = ROOT / ".deps" / dependency_name
    patch = ROOT / "patches" / patch_name
    forward = subprocess.run(
        ["git", "-C", str(dependency), "apply", "--check", str(patch)],
        capture_output=True,
        text=True,
    )
    if forward.returncode == 0:
        subprocess.run(["git", "-C", str(dependency), "apply", str(patch)], check=True)
        return
    reverse = subprocess.run(
        ["git", "-C", str(dependency), "apply", "--reverse", "--check", str(patch)],
        capture_output=True,
        text=True,
    )
    if reverse.returncode != 0:
        detail = (forward.stderr or forward.stdout or reverse.stderr or reverse.stdout).strip()
        raise RuntimeError(f"pinned {dependency_name} tree does not accept project patch {patch.name}: {detail}")


def apply_project_patches():
    # The diagnostic patch extends the training-status patch's hunks. Remove
    # only that exact, verified overlay before checking the underlying patch;
    # then reapply it below. Unrelated dependency edits are never discarded.
    overlay = ROOT / "patches/litex-sdram-read-capture-diagnostic.patch"
    dependency = ROOT / ".deps/litex"
    reverse = subprocess.run(
        ["git", "-C", str(dependency), "apply", "--reverse", "--check", str(overlay)],
        capture_output=True, text=True,
    )
    if reverse.returncode == 0:
        subprocess.run(["git", "-C", str(dependency), "apply", "--reverse", str(overlay)], check=True)
    apply_project_patch("litex", "litex-sdram-training-status.patch")
    apply_project_patch("litex", "litex-sdram-read-capture-diagnostic.patch")
    apply_project_patch("litex", "litex-memtest-read-only-diagnostic.patch")
    apply_project_patch("litex", "litex-ddr-diagnostic-boot.patch")
    apply_project_patch("litex", "litex-project-vexriscv-isa-variants.patch")
    apply_project_patch("litex", "litex-gowin-extra-sdc.patch")
    apply_project_patch("litedram", "litedram-gw2ddrphy-cdc.patch")
    apply_project_patch("litedram", "litedram-gw2ddrphy-dll-off-read.patch")
    # This module is imported before dependency patches are installed in the
    # current process. Keep its registry aligned with the patched source.
    from litex.soc.cores.cpu.vexriscv import core as vexriscv_core
    vexriscv_core.CPU_VARIANTS.update({"projectim": "VexRiscv", "projectimc": "VexRiscv"})
    vexriscv_core.GCC_FLAGS.update({
        "projectim": "-march=rv32i2p0_m -mabi=ilp32",
        "projectimc": "-march=rv32i2p0_mc -mabi=ilp32",
    })
    vexriscv_core.VexRiscv.variants = vexriscv_core.CPU_VARIANTS


def read_main_ram_size(mem_header):
    text = Path(mem_header).read_text()
    match = re.search(r"^#define\s+MAIN_RAM_SIZE\s+(0x[0-9a-fA-F]+|[0-9]+)", text, re.M)
    if not match:
        raise RuntimeError("generated mem.h does not define the main RAM size")
    return int(match.group(1), 0)


def compile_firmware(profile, output_dir, builder):
    firmware_dir = Path(output_dir) / "firmware"
    firmware_dir.mkdir(parents=True, exist_ok=True)
    (firmware_dir / "profile.h").write_text(f'#define CPU_PROFILE_NAME "{profile}"\n')
    command = [
        "make", "-f", str(ROOT / "firmware/Makefile"),
        f"ROOT={ROOT}", f"BUILD_DIR={Path(output_dir).resolve()}",
        f"OUTPUT_DIR={firmware_dir.resolve()}", f"PROFILE={profile}", "all",
    ]
    subprocess.run(command, cwd=firmware_dir, check=True)

    elf = firmware_dir / "demo.elf"
    binary = firmware_dir / "demo.bin"
    sections = parse_elf_sections(elf, builder.soc.cpu.gcc_triple)
    main_used = binary.stat().st_size
    sram_used = sum(sections.get(name, {}).get("size", 0) for name in (".data", ".bss"))
    stack_used = sections.get(".stack", {}).get("size", 0)
    main_ram_size = read_main_ram_size(Path(output_dir) / "software/include/generated/mem.h")
    if main_used > main_ram_size:
        raise RuntimeError(f"firmware load image is {main_used} bytes; main RAM is {main_ram_size} bytes")
    if stack_used < 2048:
        raise RuntimeError(f"linker reserved only {stack_used} bytes for stack")
    if sram_used + stack_used > 8 * 1024:
        raise RuntimeError(f"firmware working SRAM use ({sram_used}) plus stack ({stack_used}) exceeds 8192 bytes")
    return {
        "elf": str(elf.relative_to(ROOT)),
        "binary": str(binary.relative_to(ROOT)),
        "binary_bytes": main_used,
        "working_sram_bytes": sram_used,
        "reserved_stack_bytes": stack_used,
        "sections": sections,
        "main_ram_base": read_main_ram_base(Path(output_dir) / "software/include/generated/mem.h"),
        "main_ram_size": main_ram_size,
    }


def generate_soc(profile, output_dir, run_tools=False, compile_software=False, compile_gateware=False,
                 memory="onchip", bios_size=None, cpu_rtl=None, cpu_variant=None):
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; valid profiles: {', '.join(PROFILES)}")
    validate_memory(memory)
    if profile == "performance":
        _, selected_rtl, _, _ = selected_performance_cpu()
        if cpu_rtl is not None and Path(cpu_rtl).resolve() != selected_rtl.resolve():
            raise RuntimeError("performance SoC RTL does not match the measured profile selection")
        cpu_rtl = selected_rtl
        if memory == "onchip" and bios_size is None:
            bios_size = 24 * 1024
    elif profile == "maxperf":
        selection, item, generated, selected_rtl = read_maxperf_selection(memory)
        if cpu_rtl is not None and Path(cpu_rtl).resolve() != selected_rtl.resolve():
            raise RuntimeError("maxperf generated SoC RTL does not match its accepted memory-mode selection")
        cpu_rtl = selected_rtl
        cpu_variant = generated["cpu_configuration"]["cpu_variant"]
        if bios_size is None and memory == "onchip":
            bios_size = 24 * 1024
    output_dir = Path(output_dir).resolve()
    soc = ProjectSoC(profile=profile, memory=memory, bios_size=bios_size, cpu_rtl=cpu_rtl,
                     cpu_variant=cpu_variant)
    builder = builder_for(soc, output_dir, compile_software, compile_gateware)
    builder.build(run=run_tools, build_name=f"tang20k_{profile}")
    return soc, builder


def read_performance_selection():
    selection_path = ROOT / "cpu-profile-selection.json"
    data = json.loads(selection_path.read_text())
    if data.get("profile") != "performance" or data.get("status") != "accepted":
        raise RuntimeError("cpu-profile-selection.json does not select an accepted performance profile")
    evaluation = data.get("evaluation", {})
    candidate = data.get("candidate")
    candidate_result = evaluation.get("candidates", {}).get(candidate, {})
    baseline = evaluation.get("standard", {})
    if (candidate not in ("dynamic", "dynamic_target")
            or evaluation.get("status") != "candidate_beats_fresh_standard"
            or evaluation.get("clock_hz") != SYS_CLK_FREQ
            or baseline.get("clock_hz") != SYS_CLK_FREQ
            or candidate_result.get("clock_hz") != SYS_CLK_FREQ
            or candidate_result.get("status") != "passed"
            or candidate_result.get("repetitions") != 3
            or candidate_result.get("coremark_mean", 0) <= baseline.get("coremark_mean", 0)):
        raise RuntimeError("performance profile selection lacks a strict, validated 48 MHz win")
    evidence_path = ROOT / evaluation.get("result_path", "")
    if not evidence_path.is_file() or hashlib.sha256(evidence_path.read_bytes()).hexdigest() != evaluation.get("result_sha256"):
        raise RuntimeError("performance profile evaluation evidence is missing or has changed")
    evidence = json.loads(evidence_path.read_text())
    if (evidence.get("status") != evaluation.get("status")
            or evidence.get("batch_id") != evaluation.get("batch_id")
            or evidence.get("winner", {}).get("candidate") != candidate):
        raise RuntimeError("performance profile evidence does not match its recorded CPU winner")
    for case_name, expected in (("standard", baseline), (candidate, candidate_result)):
        case = evidence.get("cases", {}).get(case_name, {})
        trials = case.get("trials", [])
        aggregate = case.get("aggregate", {})
        if (case.get("status") != "passed" or case.get("clock_hz") != SYS_CLK_FREQ
                or len(trials) != 4 or any(trial.get("status") != "passed" for trial in trials)
                or aggregate.get("count") != 3
                or aggregate.get("coremark_mean") != expected.get("coremark_mean")):
            raise RuntimeError(f"performance profile {case_name} evidence lacks four passing 48 MHz trials")
    candidate_identity = evidence["cases"][candidate].get("identity", {})
    if (candidate_identity.get("cpu_candidate") != candidate
            or candidate_identity.get("cpu_configuration", {}).get("rtl_sha256") != data.get("rtl_sha256")):
        raise RuntimeError("performance profile evaluation does not identify the selected candidate RTL")
    return data


def selected_performance_cpu():
    selection = read_performance_selection()
    candidate = selection["candidate"]
    generated = ensure_generated(candidate)
    if generated.get("rtl_sha256") != selection.get("rtl_sha256"):
        raise RuntimeError("regenerated performance CPU RTL does not match the measured winner hash")
    return candidate, ROOT / generated["rtl"], generated, selection


def read_maxperf_selection(memory):
    from gateware.profile_selection import accepted_maxperf_selection
    selection = accepted_maxperf_selection()
    if selection is None:
        raise RuntimeError("maxperf is not qualified in both memory modes; run make maxperf-build/maxperf-run")
    item = selection["memory_modes"][memory]
    manifest_path = ROOT / item["candidate_manifest"]["path"]
    rtl_path = ROOT / item["candidate_manifest"]["rtl"]
    if (not manifest_path.is_file()
            or hashlib.sha256(manifest_path.read_bytes()).hexdigest() != item["candidate_manifest"]["sha256"]):
        raise RuntimeError(f"accepted {memory}/maxperf generation manifest is missing or changed")
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get("candidate_id") != item["candidate_id"] or not rtl_path.is_file()
            or hashlib.sha256(rtl_path.read_bytes()).hexdigest() != manifest.get("rtl_sha256")):
        raise RuntimeError(f"accepted {memory}/maxperf RTL identity does not match its selection")
    result_path = ROOT / item["evaluation_evidence"]["path"]
    if (not result_path.is_file()
            or hashlib.sha256(result_path.read_bytes()).hexdigest() != item["evaluation_evidence"]["sha256"]):
        raise RuntimeError(f"accepted {memory}/maxperf board evaluation evidence is missing or changed")
    return selection, item, manifest, rtl_path


def select_bios_size(profile, memory, output_dir, cpu_rtl=None, cpu_variant=None):
    if memory == "onchip":
        return 24 * 1024 if profile in ("performance", "linux") or (profile == "standard" and cpu_rtl is not None) else 32 * 1024

    diagnostics = Path(output_dir) / "diagnostics" / "bios-probes"
    failures = []
    for size in DDR_BIOS_SIZES:
        probe_dir = diagnostics / str(size)
        try:
            soc = ProjectSoC(profile=profile, memory=memory, bios_size=size,
                             cpu_rtl=cpu_rtl, cpu_variant=cpu_variant)
            builder = builder_for(soc, probe_dir, compile_software=True, compile_gateware=False)
            builder.build(run=False, build_name=f"bios_probe_{profile}_{size}")
            bios_files = list(probe_dir.glob("**/bios.bin"))
            if not bios_files:
                raise RuntimeError("LiteX software build produced no BIOS image")
            image_size = bios_files[0].stat().st_size
            if image_size <= size:
                return size
            failures.append(f"{size}-byte reservation: BIOS image is {image_size} bytes")
        except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
            failures.append(f"{size}-byte reservation: {type(error).__name__}: {error}")
    raise RuntimeError("DDR-mode BIOS does not fit any approved reservation: " + "; ".join(failures))


def build_profile(profile, memory="onchip", force=False, output_dir=None,
                  cpu_rtl=None, cpu_candidate=None, bios_size=None, provisional=False):
    if profile not in PROFILES and not (profile == "maxperf" and provisional):
        raise ValueError(f"unknown profile {profile!r}; valid profiles: {', '.join(PROFILES)}")
    validate_memory(memory)
    apply_project_patches()
    candidate_manifest = None
    candidate_rtl_hash = None
    profile_selection = None
    cpu_generation = None
    cpu_variant = None
    is_public_performance = profile == "performance"
    if profile == "maxperf":
        if provisional:
            if not cpu_candidate or output_dir is None:
                raise ValueError("internal provisional maxperf builds require an explicit candidate and private output directory")
            output_dir = Path(output_dir).resolve()
            verification_root = (ROOT / "build/maxperf-verification" / memory).resolve()
            if not output_dir.is_relative_to(verification_root):
                raise ValueError("provisional maxperf artifacts must stay under build/maxperf-verification/<memory>/")
            manifest_path = ROOT / "build/maxperf-candidates" / memory / cpu_candidate / "candidate.json"
            if not manifest_path.is_file():
                raise RuntimeError(f"provisional {memory} candidate manifest is missing: {cpu_candidate}")
            cpu_generation = json.loads(manifest_path.read_text())
            if (cpu_generation.get("candidate_id") != cpu_candidate
                    or cpu_generation.get("memory_mode") != memory):
                raise RuntimeError("provisional maxperf candidate manifest has a mismatched identity")
            cpu_rtl = ROOT / cpu_generation["rtl"]
            if hashlib.sha256(cpu_rtl.read_bytes()).hexdigest() != cpu_generation.get("rtl_sha256"):
                raise RuntimeError("provisional maxperf candidate RTL does not match its generation manifest")
            cpu_variant = cpu_generation["cpu_configuration"]["cpu_variant"]
            profile_selection = {
                "schema_version": 1, "profile": "maxperf", "status": "provisional",
                "clock_hz": SYS_CLK_FREQ,
                "memory_modes": {memory: {
                    "status": "provisional", "candidate_id": cpu_candidate,
                }},
            }
        else:
            profile_selection, maxperf_entry, cpu_generation, cpu_rtl = read_maxperf_selection(memory)
            cpu_candidate = maxperf_entry["candidate_id"]
            cpu_variant = cpu_generation["cpu_configuration"]["cpu_variant"]
        if memory == "onchip":
            bios_size = 24 * 1024 if bios_size is None else bios_size
            if bios_size != 24 * 1024:
                raise ValueError("the on-chip maxperf profile reserves a 24 KiB BIOS ROM")
        output_dir = Path(output_dir) if output_dir is not None else profile_build_dir(ROOT, profile, memory)
    elif is_public_performance:
        selected_candidate, selected_rtl, cpu_generation, profile_selection = selected_performance_cpu()
        if cpu_candidate not in (None, selected_candidate):
            raise ValueError("public performance profile is pinned to the measured CPU winner")
        if cpu_rtl is not None and Path(cpu_rtl).resolve() != selected_rtl.resolve():
            raise ValueError("public performance profile cannot override its measured CPU RTL")
        cpu_candidate = selected_candidate
        cpu_rtl = selected_rtl
        cpu_variant = "standard"
        if memory == "onchip":
            bios_size = 24 * 1024 if bios_size is None else bios_size
            if bios_size != 24 * 1024:
                raise ValueError("the on-chip performance profile reserves a 24 KiB BIOS ROM")
        output_dir = Path(output_dir) if output_dir is not None else profile_build_dir(ROOT, profile, memory)
    elif cpu_candidate is not None:
        if profile != "standard" or cpu_rtl is None:
            raise ValueError("provisional maxperf candidates use the standard integration shell and generated RTL")
        candidate_manifest_path = Path(cpu_rtl).resolve().parent / "candidate.json"
        if not candidate_manifest_path.is_file():
            raise RuntimeError("generate the locked CPU candidate before building it")
        cpu_generation = json.loads(candidate_manifest_path.read_text())
        if cpu_generation.get("candidate_id") is None:
            # Keep the original two prediction candidates and their evidence format valid.
            if memory != "onchip" or cpu_candidate not in ("dynamic", "dynamic_target"):
                raise ValueError("legacy prediction candidates only support on-chip evaluation")
            cpu_variant = "standard"
            bios_size = 24 * 1024 if bios_size is None else bios_size
            if bios_size != 24 * 1024:
                raise ValueError("legacy CPU candidates reserve the approved 24 KiB BIOS ROM")
            output_dir = Path(output_dir or ROOT / "build/cpu-candidates" / cpu_candidate / "soc")
        else:
            if cpu_generation.get("candidate_id") != cpu_candidate:
                raise RuntimeError("candidate identifier does not match its generation manifest")
            cpu_variant = cpu_generation["cpu_configuration"]["cpu_variant"]
            if memory == "onchip":
                bios_size = 24 * 1024 if bios_size is None else bios_size
                if bios_size != 24 * 1024:
                    raise ValueError("on-chip maxperf candidates reserve a 24 KiB BIOS ROM")
            output_dir = Path(output_dir or ROOT / "build/maxperf-candidates" / memory / cpu_candidate / "soc")
    else:
        output_dir = Path(output_dir) if output_dir is not None else profile_build_dir(ROOT, profile, memory)
    if cpu_candidate is not None:
        cpu_rtl = Path(cpu_rtl).resolve()
        candidate_manifest_path = cpu_rtl.parent / "candidate.json"
        if not cpu_rtl.is_file() or not candidate_manifest_path.is_file():
            raise RuntimeError("generate the locked CPU candidate RTL before building it")
        candidate_manifest = json.loads(candidate_manifest_path.read_text())
        candidate_rtl_hash = hashlib.sha256(cpu_rtl.read_bytes()).hexdigest()
        if (candidate_manifest.get("candidate_id", candidate_manifest.get("candidate")) != cpu_candidate
                or candidate_manifest.get("rtl_sha256") != candidate_rtl_hash):
            raise RuntimeError("CPU candidate RTL does not match its generation manifest")
    output_dir = output_dir.resolve()
    metadata_path = output_dir / "build-metadata.json"
    fingerprint = source_fingerprint()
    if candidate_manifest is not None:
        fingerprint = hashlib.sha256(
            (fingerprint + candidate_rtl_hash + json.dumps(candidate_manifest, sort_keys=True)).encode()
        ).hexdigest()
    if profile_selection is not None:
        fingerprint = hashlib.sha256(
            (fingerprint + json.dumps(profile_selection, sort_keys=True)).encode()
        ).hexdigest()
    cpu_configuration = configuration_for(
        profile,
        cpu_candidate=cpu_candidate,
        candidate_manifest=cpu_generation,
        selection=profile_selection,
    )
    fingerprint = hashlib.sha256(
        (fingerprint + json.dumps(cpu_configuration, sort_keys=True)
         + (cpu_variant or PROFILES[profile])).encode()
    ).hexdigest()
    if not force and metadata_path.exists():
        try:
            previous = json.loads(metadata_path.read_text())
            bitstream = output_dir / "bitstream.fs"
            binary = output_dir / "firmware/demo.bin"
            if (previous.get("status") == "passed"
                    and previous.get("memory_mode", "onchip") == memory
                    and previous.get("source_fingerprint") == fingerprint
                    and bitstream.is_file() and binary.is_file()):
                print(f"Using current {memory}/{profile} build from {output_dir}")
                return previous
        except (OSError, json.JSONDecodeError):
            pass

    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "profile": profile,
        "memory_mode": memory,
        "cpu_candidate": cpu_candidate,
        "cpu_profile_selection": profile_selection,
        "liteX_variant": cpu_variant or PROFILES[profile],
        "sys_clk_hz": SYS_CLK_FREQ,
        "uart_baud": UART_BAUDRATE,
        "status": "in_progress",
        "source_fingerprint": fingerprint,
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    lock = json.loads((ROOT / "dependencies.lock.json").read_text())
    metadata["locked_repositories"] = {item["name"]: item["commit"] for item in lock["repositories"]}
    metadata["tool_versions"] = {
        "python": sys.version.split()[0],
        "riscv_gcc": first_version_line(["riscv-none-elf-gcc", "--version"]),
        "gowin": "version recorded from successful Gowin PnR report",
        "openfpgaloader": first_version_line(["openFPGALoader", "--Version"]),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    try:
        selected_bios_size = bios_size if bios_size is not None else select_bios_size(
            profile, memory, output_dir, cpu_rtl=cpu_rtl, cpu_variant=cpu_variant
        )
        soc = ProjectSoC(profile=profile, memory=memory, bios_size=selected_bios_size,
                         cpu_rtl=cpu_rtl, cpu_variant=cpu_variant)
        builder = builder_for(soc, output_dir, compile_software=True, compile_gateware=True)
        # Candidate IDs are evidence paths and intentionally include readable
        # separators. Gowin's Verilog parser rejects those in module names, so
        # sanitize only the synthesized top-level identifier.
        candidate_module_suffix = (
            "_" + re.sub(r"[^A-Za-z0-9_]", "_", cpu_candidate)
            if cpu_candidate else ""
        )
        build_name = f"tang20k_{profile}{candidate_module_suffix}"
        builder.build(run=True, build_name=build_name)
        metadata["tool_versions"]["gowin"] = gowin_report_version(
            output_dir / "gateware/impl/pnr/project.rpt.txt"
        )
        bitstream = Path(builder.get_bitstream_filename(mode="sram"))
        if not bitstream.is_file() or bitstream.stat().st_size == 0:
            raise RuntimeError(f"Gowin SRAM bitstream was not produced at {bitstream}")
        public_bitstream = output_dir / "bitstream.fs"
        shutil.copy2(bitstream, public_bitstream)

        firmware = compile_firmware(profile, output_dir, builder)
        bios_binary = output_dir / "software/bios/bios.bin"
        if bios_binary.is_file() and bios_binary.stat().st_size > selected_bios_size:
            raise RuntimeError(
                f"BIOS image is {bios_binary.stat().st_size} bytes and exceeds the "
                f"{selected_bios_size}-byte integrated ROM"
            )
        metadata.update({
            "firmware": firmware,
            "bios_size": selected_bios_size,
            "bitstream": str(public_bitstream.relative_to(ROOT)),
            "bitstream_bytes": public_bitstream.stat().st_size,
            "bitstream_sha256": hashlib.sha256(public_bitstream.read_bytes()).hexdigest(),
            "cpu_variant": soc.cpu.variant,
            "cpu_configuration": {**cpu_configuration, "liteX_variant": soc.cpu.variant},
            "memory": {
                "mode": memory,
                "main_ram_base": firmware["main_ram_base"],
                "main_ram_bytes": firmware["main_ram_size"],
                "ddr_physical_bytes": DDR_SIZE_BYTES if memory == "ddr3" else 0,
                "ddr_part": DDR_PART if memory == "ddr3" else None,
                "ddr_geometry": {"row_bits": DDR_ROW_BITS, "bank_bits": DDR_BANK_BITS,
                                 "column_bits": DDR_COLUMN_BITS} if memory == "ddr3" else None,
                "l2_cache_bytes": DDR_L2_SIZE if memory == "ddr3" else 0,
                "ddr_ck_hz": 96_000_000 if memory == "ddr3" else None,
                "uncached_alias_base": "0xc0000000" if memory == "ddr3" else None,
                "diagnostic_ram": {
                    "base": "0x20000000", "bytes": 16 * 1024
                } if memory == "ddr3" else None,
            },
            "device": soc.platform.device,
            "cpu_rtl": str(cpu_rtl.relative_to(ROOT)) if cpu_rtl is not None else None,
            "status": "passed",
            "finished_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        (output_dir / "build-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        return metadata
    except BaseException as error:
        metadata["status"] = "failed"
        metadata["error"] = f"{type(error).__name__}: {error}"
        metadata["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        raise


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", choices=[*PROFILES, "maxperf"])
    parser.add_argument("--memory", choices=("onchip", "ddr3"), default="onchip")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.profile == "maxperf" and args.profile not in PROFILES:
        print("maxperf is provisional; run make maxperf-build and make maxperf-run for its candidate workflow", file=sys.stderr)
        return 1
    try:
        result = build_profile(args.profile, memory=args.memory, force=args.force)
    except (OSError, subprocess.CalledProcessError, RuntimeError, ValueError) as error:
        print(f"build failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
