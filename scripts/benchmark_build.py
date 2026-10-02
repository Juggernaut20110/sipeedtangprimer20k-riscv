#!/usr/bin/env python3
"""Build and fingerprint CoreMark firmware for all LiteX CPU profiles."""

import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from gateware.soc import MEMORY_MODES, PROFILES, SYS_CLK_FREQ  # noqa: E402
from scripts.memory import profile_build_dir, validate_memory  # noqa: E402
from benchmark_identity import (  # noqa: E402
    CURRENT_IDENTITY_SCHEMA, benchmark_fingerprint_payload, stable_hash,
)
from benchmark_evidence import bundle_is_current, create_bundle  # noqa: E402
import build as build_module  # noqa: E402
import compare as compare_module  # noqa: E402

LOCK = json.loads((ROOT / "dependencies.lock.json").read_text())
COREMARK = LOCK["repositories"][-1]
UPSTREAM = ROOT / ".deps/coremark"
LITEX_UART = ROOT / ".deps/litex/litex/soc/software/libbase/uart.c"
PORT = ROOT / "firmware/benchmark"
CALIBRATION_ITERATIONS = 1000
TARGET_SECONDS = 20
COREMARK_CHECK = None
ALGORITHMS = [
    "core_list_join.c", "core_main.c", "core_matrix.c", "core_state.c", "core_util.c",
]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def require_coremark_checkout():
    global COREMARK_CHECK
    if not (UPSTREAM / ".git").exists():
        raise RuntimeError("CoreMark checkout is missing; run make setup")
    actual = subprocess.check_output(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], text=True).strip()
    if actual != COREMARK["commit"]:
        raise RuntimeError(f"CoreMark checkout is {actual}; expected locked commit {COREMARK['commit']}; run make setup")
    dirty = subprocess.check_output(["git", "-C", str(UPSTREAM), "status", "--porcelain"], text=True)
    if dirty:
        raise RuntimeError("CoreMark checkout has local changes; restore the pinned upstream source before benchmarking")
    check = subprocess.run(["make", "check"], cwd=UPSTREAM, capture_output=True, text=True)
    output = (check.stdout + check.stderr).splitlines()
    algorithm_names = set(ALGORITHMS)
    verified_algorithms = {
        line.split(":", 1)[0] for line in output
        if ": OK" in line and line.split(":", 1)[0] in algorithm_names
    }
    failed_files = {
        line.split(":", 1)[0] for line in output
        if ": FAILED" in line and line.split(":", 1)[0]
    }
    if verified_algorithms != algorithm_names:
        raise RuntimeError(f"CoreMark upstream MD5 check did not verify every algorithm source: {output}")
    if check.returncode and failed_files != {"coremark.h"}:
        raise RuntimeError(f"CoreMark upstream MD5 check found unexpected failures: {output}")
    COREMARK_CHECK = {
        "algorithm_md5": "passed",
        "algorithm_files": sorted(verified_algorithms),
        "checkout_clean_at_locked_commit": True,
        "upstream_make_check_exit_code": check.returncode,
        "upstream_manifest_note": (
            "coremark.md5 reports coremark.h as failed although the checkout is clean at the pinned commit; "
            "all five protected algorithm C sources pass the upstream MD5 check."
            if check.returncode else "all protected sources pass the upstream MD5 check."
        ),
        "upstream_make_check_output": output,
    }
    if check.returncode:
        print("NOTICE: all CoreMark algorithm C files match coremark.md5; upstream coremark.h checksum is stale for the pinned commit.")


def read_make_value(path, name):
    match = re.search(rf"^{re.escape(name)}=(.*)$", Path(path).read_text(), re.M)
    if not match:
        raise RuntimeError(f"generated {Path(path).name} does not define {name}")
    return match.group(1).strip()


def generated_cache_config(profile_dir, cpu_configuration=None):
    soc = (profile_dir / "software/include/generated/soc.h").read_text()
    cpu_configuration = cpu_configuration or {}
    return {
        "instruction_cache": "enabled" if "#define CONFIG_CPU_HAS_ICACHE" in soc else "disabled",
        "instruction_cache_bytes": cpu_configuration.get("instruction_cache_bytes", 0),
        "data_cache": "enabled" if "#define CONFIG_CPU_HAS_DCACHE" in soc else "disabled",
        "data_cache_bytes": cpu_configuration.get("data_cache_bytes", 0),
        "configuration_source": str((profile_dir / "software/include/generated/soc.h").relative_to(ROOT)),
    }


def c_string(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_sections(readelf, elf):
    return build_module.parse_elf_sections(elf, "riscv-none-elf")


def section_bytes(sections, names):
    return sum(sections.get(name, {}).get("size", 0) for name in names)


def stack_usage_summary(directory):
    frames = []
    for usage_file in sorted(Path(directory).glob("*.su")):
        for line in usage_file.read_text(errors="replace").splitlines():
            fields = line.split("\t")
            if len(fields) >= 3 and fields[1].isdigit():
                frames.append({
                    "function": fields[0].rsplit(":", 1)[-1],
                    "bytes": int(fields[1]),
                    "classification": fields[2],
                    "source": str(usage_file.relative_to(ROOT)),
                })
    if not frames:
        raise RuntimeError(f"compiler produced no static stack reports under {directory}")
    return {
        "largest_static_frame_bytes": max(item["bytes"] for item in frames),
        "reported_function_frames": len(frames),
        "top_frames": sorted(frames, key=lambda item: item["bytes"], reverse=True)[:12],
        "interpretation": "GCC per-function static frames; the firmware reserves 2048 stack bytes. The report is not a runtime stack high-water measurement.",
    }


def compiler_path():
    found = shutil.which("riscv-none-elf-gcc")
    if found:
        return Path(found).resolve()
    matches = list((ROOT / ".tools/riscv-gcc").glob("**/bin/riscv-none-elf-gcc"))
    if not matches:
        raise RuntimeError("riscv-none-elf-gcc is missing; run make setup")
    return matches[0].resolve()


def compile_variant(profile, mode, build_id, compiler_flags, include_flags, build_dir, out_dir,
                    cpu_configuration, memory_configuration):
    mode_dir = out_dir / mode
    mode_dir.mkdir(parents=True, exist_ok=True)
    mode_define = "PERFORMANCE_RUN" if mode == "performance" else "VALIDATION_RUN"
    seeds = (0, 0, 0x66) if mode == "performance" else (0x3415, 0x3415, 0x66)
    mode_defines = [
        f"-DITERATIONS={CALIBRATION_ITERATIONS}", "-DTOTAL_DATA_SIZE=2000", "-DMEM_METHOD=MEM_STATIC",
        "-DMULTITHREAD=1", f"-D{mode_define}=1",
    ]
    profile_h = mode_dir / "benchmark_profile.h"
    cache = generated_cache_config(build_dir, cpu_configuration)
    profile_h.write_text(
        "#ifndef TANG20K_BENCHMARK_PROFILE_H\n#define TANG20K_BENCHMARK_PROFILE_H\n"
        f"#define BENCHMARK_PROFILE {c_string(profile)}\n"
        f"#define BENCHMARK_BUILD_ID {c_string(build_id)}\n"
        f"#define BENCHMARK_MODE {c_string(mode)}\n"
        f"#define BENCHMARK_COMPILER_FLAGS {c_string(' '.join(compiler_flags + mode_defines))}\n"
        f"#define BENCHMARK_CLOCK_HZ {SYS_CLK_FREQ}u\n"
        f"#define BENCHMARK_ICACHE_BYTES {cache['instruction_cache_bytes']}u\n"
        f"#define BENCHMARK_DCACHE_BYTES {cache['data_cache_bytes']}u\n"
        f"#define BENCHMARK_L2_BYTES {memory_configuration.get('l2_cache_bytes', 0)}u\n"
        f"#define BENCHMARK_SEED1 {seeds[0]}\n#define BENCHMARK_SEED2 {seeds[1]}\n"
        f"#define BENCHMARK_SEED3 {seeds[2]}\n"
        f"#define BENCHMARK_CACHE_CONFIG {c_string('icache=' + str(cache['instruction_cache_bytes']) + ',dcache=' + str(cache['data_cache_bytes']) + ',l2=' + str(memory_configuration.get('l2_cache_bytes', 0)))}\n"
        "#endif\n"
    )

    obj_dir = mode_dir / "obj"
    obj_dir.mkdir(parents=True, exist_ok=True)
    sources = [(name, UPSTREAM / name, []) for name in ALGORITHMS]
    sources += [
        ("core_portme.c", PORT / "core_portme.c", []),
        ("benchmark_main.c", PORT / "benchmark_main.c", []),
        ("ee_printf.c", PORT / "ee_printf.c", []),
        # Compile the pinned LiteX UART library's documented polling backend
        # for this interrupt-disabled benchmark. The default buffered backend
        # can stall after its 127-byte TX queue fills when its TX event is not
        # serviced; the polling backend writes directly to the hardware FIFO.
        ("litex_uart_polling.c", LITEX_UART, ["-DUART_POLLING"]),
    ]
    gcc = compiler_path()
    mode_include_flags = include_flags + [f"-I{mode_dir}"]
    objects = []
    object_commands = {}
    for name, source, source_defines in sources:
        object_path = obj_dir / (Path(name).stem + ".o")
        source_flags = list(compiler_flags) + mode_defines + source_defines
        if name == "core_main.c":
            source_flags.append("-Dmain=coremark_main")
        if name == "benchmark_main.c":
            # The integration wrapper contains extra untimed SRAM and image
            # diagnostics. Keep that code compact in the 32-KiB main RAM while
            # leaving every CoreMark source at the measured -O2 setting.
            source_flags.append("-Os")
        command = [
            str(gcc), *source_flags, *mode_include_flags, "-fstack-usage", "-MMD", "-MP",
            "-MF", str(object_path.with_suffix(".d")), "-c", str(source), "-o", str(object_path),
        ]
        subprocess.run(command, cwd=ROOT, check=True)
        objects.append(object_path)
        object_commands[name] = {"source": str(source.relative_to(ROOT)), "flags": source_flags + mode_include_flags}

    cpu = "riscv-none-elf"
    ldflags = [
        "-nostdlib", "-nodefaultlibs", "-Wl,--no-dynamic-linker", "-Wl,--build-id=none",
        *compiler_flags, *mode_include_flags, f"-L{build_dir / 'software/include'}",
        "-T", str(ROOT / "firmware/linker.ld"), "-N",
    ]
    libraries = read_make_value(build_dir / "software/include/generated/variables.mak", "LIBS").split()
    packages = read_make_value(build_dir / "software/include/generated/variables.mak", "PACKAGES").split()
    lib_paths = [f"-L{build_dir / 'software' / package}" for package in packages]
    link_flags = [
        str(gcc), *ldflags, "-o", str(mode_dir / "benchmark.elf"),
        str(build_dir / "firmware/crt0.o") if (build_dir / "firmware/crt0.o").is_file() else "",
    ]
    if not link_flags[-1]:
        # LiteX stores the profile's startup assembly in the generated software directory.
        startup = next(iter(build_dir.glob("**/crt0.o")), None)
        if startup is None:
            raise RuntimeError(f"LiteX startup object crt0.o is missing for {profile}")
        link_flags[-1] = str(startup)
    link_flags += [str(path) for path in objects]
    link_flags += lib_paths + ["-Wl,--start-group"]
    link_flags += [f"-l{name[3:]}" if name.startswith("lib") else f"-l{name}" for name in libraries]
    link_flags += ["-lgcc"]
    link_flags += ["-Wl,--end-group", "-Wl,--gc-sections", f"-Wl,-Map,{mode_dir / 'benchmark.elf.map'}"]
    subprocess.run(link_flags, cwd=ROOT, check=True)

    elf = mode_dir / "benchmark.elf"
    binary = mode_dir / "benchmark.bin"
    objcopy = gcc.with_name(f"{cpu}-objcopy")
    subprocess.run([str(objcopy), "-O", "binary", str(elf), str(binary)], check=True)
    binary.chmod(0o644)
    binary_crc32 = zlib.crc32(binary.read_bytes()) & 0xffffffff
    readelf = gcc.with_name(f"{cpu}-readelf")
    sections = parse_sections(readelf, elf)
    main_used = binary.stat().st_size
    sram_section_bytes = section_bytes(sections, (".data", ".bss"))
    symbol_output = subprocess.check_output([str(gcc.with_name(f"{cpu}-nm")), "-n", str(elf)], text=True)
    symbols = {name: int(address, 16) for address, name in re.findall(
        r"^([0-9a-fA-F]+)\s+\S\s+(_fdata|_end|_stack_bottom|_stack_top)$", symbol_output, re.M)}
    if len(symbols) != 4:
        raise RuntimeError(f"{profile}/{mode} is missing linked SRAM/stack boundary symbols")
    sram_used = symbols["_end"] - symbols["_fdata"]
    stack_reserved = sections.get(".stack", {}).get("size", 0)
    main_capacity = build_module.read_main_ram_size(build_dir / "software/include/generated/mem.h")
    if main_used > main_capacity:
        raise RuntimeError(f"{profile}/{mode} benchmark image is {main_used} bytes; {main_capacity}-byte main RAM exceeded")
    if stack_reserved < 2048:
        raise RuntimeError(f"{profile}/{mode} benchmark reserved stack is only {stack_reserved} bytes")
    if sram_used + stack_reserved > 8 * 1024:
        raise RuntimeError(f"{profile}/{mode} data/BSS ({sram_used}) plus stack ({stack_reserved}) exceeds 8192-byte SRAM")
    if symbols["_end"] > symbols["_stack_bottom"] or symbols["_stack_top"] - symbols["_stack_bottom"] != 2048:
        raise RuntimeError(f"{profile}/{mode} linked SRAM/stack boundaries do not fit")

    stack_usage_reports = sorted(obj_dir.glob("*.su"))
    map_file = mode_dir / "benchmark.elf.map"
    return {
        "profile": profile,
        "mode": mode,
        "build_id": build_id,
        "elf": str(elf.relative_to(ROOT)),
        "elf_sha256": sha256(elf),
        "map": str(map_file.relative_to(ROOT)),
        "binary": str(binary.relative_to(ROOT)),
        "binary_sha256": sha256(binary),
        "binary_crc32": f"{binary_crc32:08x}",
        "binary_bytes": main_used,
        "working_sram_bytes": sram_used,
        "sram_section_bytes": sram_section_bytes,
        "sram_alignment_padding_bytes": sram_used - sram_section_bytes,
        "sram_boundaries": symbols,
        "reserved_stack_bytes": stack_reserved,
        "remaining_sram_bytes": symbols["_stack_bottom"] - symbols["_end"],
        "main_ram_capacity_bytes": main_capacity,
        "sections": sections,
        "stack_usage": stack_usage_summary(obj_dir),
        "stack_usage_reports": [str(path.relative_to(ROOT)) for path in stack_usage_reports],
        "commands": object_commands,
        "link_flags": link_flags[1:],
    }


def build_profile_firmware(profile, build_metadata, memory="onchip", build_dir=None):
    validate_memory(memory)
    build_dir = Path(build_dir).resolve() if build_dir is not None else profile_build_dir(ROOT, profile, memory)
    output_dir = build_dir / "benchmark"
    output_dir.mkdir(parents=True, exist_ok=True)
    variables = build_dir / "software/include/generated/variables.mak"
    cpu_flags = shlex.split(read_make_value(variables, "CPUFLAGS"))
    compiler_flags = [
        "-std=gnu99", "-O2", *cpu_flags, "-g3", "-no-pie", "-fomit-frame-pointer",
        "-Wall", "-fno-builtin", "-fno-stack-protector", "-U_FORTIFY_SOURCE",
        "-D_FORTIFY_SOURCE=0", "-ffunction-sections", "-fdata-sections", "-fno-lto",
    ]
    include_flags = [
        f"-I{PORT}", f"-I{UPSTREAM}", f"-I{output_dir}", f"-I{build_dir / 'software/include'}",
        f"-I{ROOT / '.deps/litex/litex/soc/software'}",
        f"-I{ROOT / '.deps/litex/litex/soc/software/include'}",
        f"-I{ROOT / '.deps/litex/litex/soc/software/libbase'}",
        f"-I{ROOT / '.deps/litex/litex/soc/cores/cpu/vexriscv'}",
        f"-I{build_dir / 'software/libc'}",
        f"-I{ROOT / '.deps/pythondata-software-picolibc/pythondata_software_picolibc/data/libc/include'}",
    ]
    bitstream = ROOT / build_metadata["bitstream"]
    bitstream_hash = sha256(bitstream)
    generated = [
        build_dir / "software/include/generated/variables.mak",
        build_dir / "software/include/generated/soc.h",
        build_dir / "software/include/generated/csr.h",
        build_dir / "software/include/generated/mem.h",
    ]
    startup_object = build_dir / "firmware/crt0.o"
    libbase_archive = build_dir / "software/libbase/libbase.a"
    if not startup_object.is_file() or not libbase_archive.is_file():
        raise RuntimeError(f"{profile} is missing its LiteX startup object or libbase archive")
    source_files = [
        ROOT / "firmware/linker.ld", ROOT / "scripts/benchmark_build.py",
        ROOT / "scripts/benchmark_identity.py", ROOT / "scripts/benchmark_evidence.py",
        startup_object, libbase_archive,
    ]
    if memory == "ddr3":
        source_files += [ROOT / "gateware/ddr3.py", ROOT / "gateware/ddr_geometry.py"]
    if build_metadata.get("cpu_rtl"):
        source_files.append(ROOT / build_metadata["cpu_rtl"])
    elif build_metadata.get("cpu_configuration", {}).get("rtl"):
        source_files.append(ROOT / build_metadata["cpu_configuration"]["rtl"])
    source_files += [PORT / name for name in (
        "core_portme.h", "core_portme.c", "benchmark_main.c", "benchmark_port.h", "ee_printf.c",
    )]
    source_files += [
        LITEX_UART,
        ROOT / ".deps/litex/litex/soc/software/include/system.h",
        ROOT / ".deps/litex/litex/soc/cores/cpu/vexriscv/system.h",
    ]
    source_files += [UPSTREAM / name for name in ALGORITHMS + ["coremark.h", "coremark.md5", "LICENSE.md"]]
    source_identity = {str(path.relative_to(ROOT)): sha256(path) for path in source_files + generated}
    fingerprint_metadata = {
        "profile": profile,
        "cpu_variant": build_metadata.get("cpu_variant"),
        "cpu_candidate": build_metadata.get("cpu_candidate"),
        "cpu_profile_selection": build_metadata.get("cpu_profile_selection"),
        "memory_mode": memory,
        "coremark": {"commit": COREMARK["commit"]},
        "source_hashes": source_identity,
        "bitstream_sha256": bitstream_hash,
        "compiler": str(compiler_path()),
        "compiler_version": build_metadata["tool_versions"]["riscv_gcc"],
        "compiler_flags": compiler_flags,
        "include_flags": include_flags,
        "clock_hz": SYS_CLK_FREQ,
        "cpu_configuration": build_metadata.get("cpu_configuration"),
        "memory_configuration": build_metadata.get("memory"),
    }
    fingerprint_payload = benchmark_fingerprint_payload(
        fingerprint_metadata, build_metadata, schema_version=CURRENT_IDENTITY_SCHEMA,
    )
    fingerprint = stable_hash(fingerprint_payload)
    build_id = fingerprint[:16]
    old_path = output_dir / "benchmark-metadata.json"
    if old_path.is_file():
        try:
            previous = json.loads(old_path.read_text())
            images_current = all(
                (ROOT / entry["binary"]).is_file() and sha256(ROOT / entry["binary"]) == entry["binary_sha256"]
                for entry in previous.get("images", {}).values()
            )
            if (previous.get("status") == "passed"
                    and previous.get("source_fingerprint") == fingerprint
                    and images_current
                    and bundle_is_current(previous.get("evidence_bundle"), fingerprint, ROOT)):
                print(f"Using current {profile} benchmark firmware")
                return previous
        except (OSError, ValueError, KeyError):
            pass

    result = {
        "status": "in_progress",
        "profile": profile,
        "identity_schema_version": CURRENT_IDENTITY_SCHEMA,
        "fingerprint_payload": fingerprint_payload,
        "cpu_variant": build_metadata.get("cpu_variant"),
        "cpu_candidate": build_metadata.get("cpu_candidate"),
        "cpu_profile_selection": build_metadata.get("cpu_profile_selection"),
        "memory_mode": memory,
        "build_id": build_id,
        "source_fingerprint": fingerprint,
        "source_hashes": source_identity,
        "coremark": {"repository": COREMARK["url"], "commit": COREMARK["commit"]},
        "upstream_verification": COREMARK_CHECK,
        "bitstream": build_metadata["bitstream"],
        "bitstream_sha256": bitstream_hash,
        "clock_hz": SYS_CLK_FREQ,
        "compiler": str(compiler_path()),
        "compiler_version": build_metadata["tool_versions"]["riscv_gcc"],
        "compiler_flags": compiler_flags,
        "iteration_calibration": {
            "method": "fixed_iteration_port_calibration_with_fresh_upstream_initialization",
            "calibration_iterations": CALIBRATION_ITERATIONS,
            "target_seconds": TARGET_SECONDS,
            "formula": "ceil(calibration_iterations * target_seconds * clock_hz / calibration_ticks)",
            "scored_pass_reinitializes_static_algorithm_data": True,
            "scored_interval_index": 2,
        },
        "cache_maintenance": {
            "function": "portable_init",
            "sequence": [
                "compiler_and_memory_fence",
                "flush_cpu_dcache",
                "compiler_and_memory_fence",
                "flush_l2_cache_by_configured_eviction_reads_when_present",
                "flush_cpu_dcache_again_to_remove_eviction_probe_lines",
                "compiler_and_memory_fence",
                "flush_cpu_icache",
                "compiler_and_memory_fence",
            ],
            "timing": "before_each_upstream_invocation_and_outside_timed_workload",
            "api_headers": [
                ".deps/litex/litex/soc/software/include/system.h",
                ".deps/litex/litex/soc/cores/cpu/vexriscv/system.h",
            ],
        },
        "runtime_preflight": {
            "ram_probe": {
                "storage": "volatile BSS array of 64 union words",
                "bytes": 256,
                "alignment_bytes": 32,
                "phases": [
                    "two 32-bit write/read patterns cached and after cache maintenance",
                    "low/high halfword writes checking neighboring-half preservation and unsigned/signed reads",
                    "four byte-lane writes checking neighboring-byte preservation and unsigned/signed reads",
                    "halfword patterns include 0x0000, 0x7fff, 0x8000, 0xffff; byte patterns include 0x00, 0x7f, 0x80, 0xff",
                    "tight volatile back-to-back SH/LH and SB/LB sequences with same-location load-modify-store checks and neighbor preservation",
                    "alternating volatile SRAM stores and read-only linked-image loads at matching low-12 address bits (a D-cache index conflict when that cache is enabled), followed by SRAM/code checks",
                ],
                "cache_maintenance_after_patterns": "fence, flush_cpu_dcache, fence, flush_cpu_icache, fence",
                "storage_limit": "existing 64-word array; 256 bytes total",
            },
            "image_integrity_rechecks": {
                "phases": ["calibration", "scored"],
                "sequence": [
                    "CRC32 linked image through cached reads",
                    "fence, flush_cpu_dcache, fence, flush_cpu_icache, fence",
                    "CRC32 linked image after cache maintenance",
                ],
                "comparison": "both values and image sizes must match initial preflight values",
                "failure": "BENCHMARK_PORT_ERROR and halt",
                "timing": "after_each_upstream_invocation_and_outside_timed_workload",
            },
            "runtime_seed_checks": {
                "inputs": ["seed1_volatile", "seed2_volatile", "seed3_volatile", "seed4_volatile"],
                "expected_seed_values": "mode constants and fixed or derived iteration count",
                "emits": "BENCHMARK_RUNTIME_SEEDS",
                "failure": "BENCHMARK_PORT_ERROR and halt before upstream invocation",
            },
            "wrapper_optimization": "benchmark_main.c is compiled with -Os to fit untimed diagnostics in 32-KiB main RAM; CoreMark sources retain the profile-wide -O2 flag",
            "timing": "initial image, SRAM, and seed checks run before timed CoreMark work; image rechecks run after each interval",
        },
        "include_flags": include_flags,
        "cache": generated_cache_config(build_dir, build_metadata.get("cpu_configuration")),
        "link_inputs": {
            "startup_object": str(startup_object.relative_to(ROOT)),
            "startup_object_sha256": sha256(startup_object),
            "litex_libbase_archive": str(libbase_archive.relative_to(ROOT)),
            "litex_libbase_archive_sha256": sha256(libbase_archive),
        },
        "cpu_configuration": build_metadata.get("cpu_configuration"),
        "cpu_rtl": build_metadata.get("cpu_rtl"),
        "memory": {
            **build_metadata.get("memory", {}),
            "main_ram_base": f"0x{build_module.read_main_ram_base(build_dir / 'software/include/generated/mem.h'):08x}",
            "main_ram_bytes": build_module.read_main_ram_size(build_dir / "software/include/generated/mem.h"),
            "code_and_read_only_data": "main_ram",
            "algorithm_data_bss_and_reserved_stack": "sram",
            "sram_base": "0x10000000",
            "sram_bytes": 8192,
        },
        "started_utc": utc_now().isoformat(),
        "images": {},
    }
    old_path.write_text(json.dumps(result, indent=2) + "\n")
    try:
        for mode in ("performance", "validation"):
            result["images"][mode] = compile_variant(
                profile, mode, build_id, compiler_flags, include_flags, build_dir, output_dir,
                build_metadata.get("cpu_configuration", {}), build_metadata.get("memory", {}),
            )
        result["status"] = "passed"
        result["finished_utc"] = utc_now().isoformat()
        old_path.write_text(json.dumps(result, indent=2) + "\n")
        result["evidence_bundle"] = create_bundle(build_dir, result, build_metadata, ROOT)
    except BaseException as error:
        result["status"] = "failed"
        result["error"] = f"{type(error).__name__}: {error}"
        result["finished_utc"] = utc_now().isoformat()
        old_path.write_text(json.dumps(result, indent=2) + "\n")
        raise
    old_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", choices=MEMORY_MODES, default="onchip")
    parser.add_argument("--profile", choices=PROFILES,
                        help="build only this profile; omit to build the full registered profile set")
    args = parser.parse_args(argv)
    memory = args.memory
    require_coremark_checkout()
    session_id = utc_now().strftime("%Y%m%dT%H%M%SZ") + f"-{memory}-build"
    session_dir = ROOT / "build/benchmarks" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    combined = {
        "schema_version": 1,
        "session_id": session_id,
        "memory_mode": memory,
        "requested_profiles": [args.profile] if args.profile else list(PROFILES),
        "status": "in_progress",
        "created_utc": utc_now().isoformat(),
        "coremark_commit": COREMARK["commit"],
        "upstream_verification": COREMARK_CHECK,
        "profiles": {},
    }
    destination = (ROOT / "build" / f"benchmark-build-{memory}-{args.profile}.json"
                  if args.profile else ROOT / "build" / ("benchmark-build.json" if memory == "onchip" else f"{memory}/benchmark-build.json"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(combined, indent=2) + "\n")
    try:
        profiles = [args.profile] if args.profile else list(PROFILES)
        for profile in profiles:
            print(f"Building SoC and CoreMark firmware for {profile}", flush=True)
            profile_dir_root = profile_build_dir(ROOT, profile, memory)
            metadata = build_module.build_profile(profile, memory=memory)
            resources = compare_module.parse_profile(profile, memory=memory)
            firmware = build_profile_firmware(profile, metadata, memory=memory)
            combined["profiles"][profile] = {
                "status": "passed", "build": metadata, "resources": resources, "benchmark_firmware": firmware,
            }
            profile_dir = session_dir / "profiles" / profile
            for key, value in resources["evidence"].items():
                copy_file(ROOT / value, profile_dir / Path(value).name)
            copy_file(ROOT / metadata["bitstream"], profile_dir / "bitstream.fs")
            copy_file(profile_dir_root / "build-metadata.json", profile_dir / "build-metadata.json")
            copy_file(profile_dir_root / "benchmark/benchmark-metadata.json", profile_dir / "benchmark-metadata.json")
            for config_name in ("variables.mak", "soc.h", "csr.h", "mem.h"):
                config_path = profile_dir_root / "software/include/generated" / config_name
                copy_file(config_path, profile_dir / "generated" / config_name)
            for input_name, input_path in (
                ("crt0.o", profile_dir_root / "firmware/crt0.o"),
                ("libbase.a", profile_dir_root / "software/libbase/libbase.a"),
            ):
                copy_file(input_path, profile_dir / "firmware-inputs" / input_name)
            for mode in ("performance", "validation"):
                image = firmware["images"][mode]
                for key in ("binary", "elf", "map"):
                    copy_file(ROOT / image[key], profile_dir / mode / Path(image[key]).name)
                for stack_usage_path in image["stack_usage_reports"]:
                    usage_path = ROOT / stack_usage_path
                    copy_file(usage_path, profile_dir / mode / "stack-usage" / usage_path.name)
            combined["profiles"][profile]["session_artifacts"] = str(profile_dir.relative_to(ROOT))
            destination.write_text(json.dumps(combined, indent=2) + "\n")
        combined["status"] = "passed"
        combined["finished_utc"] = utc_now().isoformat()
    except BaseException as error:
        combined["status"] = "failed"
        combined["error"] = f"{type(error).__name__}: {error}"
        combined["finished_utc"] = utc_now().isoformat()
        destination.write_text(json.dumps(combined, indent=2) + "\n")
        (session_dir / "benchmark-build.json").write_text(json.dumps(combined, indent=2) + "\n")
        raise
    destination.write_text(json.dumps(combined, indent=2) + "\n")
    (session_dir / "benchmark-build.json").write_text(json.dumps(combined, indent=2) + "\n")
    print(json.dumps(combined, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError, RuntimeError, ValueError, KeyError, StopIteration) as error:
        print(f"benchmark build failed: {error}", file=sys.stderr)
        sys.exit(1)
