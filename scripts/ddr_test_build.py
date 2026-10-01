#!/usr/bin/env python3
"""Build DDR3 diagnostic firmware for one or all CPU profiles."""

import datetime
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build as build_module  # noqa: E402
from gateware.soc import DDR_DIAGNOSTIC_BASE, DDR_DIAGNOSTIC_SIZE, DDR_SIZE_BYTES, MEMORY_MODES, PROFILES  # noqa: E402
from memory import profile_build_dir, validate_memory  # noqa: E402


def compiler_path():
    found = shutil.which("riscv-none-elf-gcc")
    if found:
        return Path(found).resolve()
    matches = list((ROOT / ".tools/riscv-gcc").glob("**/bin/riscv-none-elf-gcc"))
    if not matches:
        raise RuntimeError("riscv-none-elf-gcc is missing; run make setup")
    return matches[0].resolve()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_make_value(path, name):
    match = re.search(rf"^{re.escape(name)}=(.*)$", Path(path).read_text(), re.M)
    if not match:
        raise RuntimeError(f"generated {Path(path).name} does not define {name}")
    return match.group(1).strip()


def section_layout(readelf, elf):
    output = subprocess.check_output([str(readelf), "-W", "-S", str(elf)], text=True)
    pattern = re.compile(
        r"^\s*\[\s*\d+\]\s+(\S+)\s+\S+\s+([0-9a-fA-F]+)\s+"
        r"[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+",
        re.M,
    )
    return {
        name: {"address": int(address, 16), "size": int(size, 16)}
        for name, address, size in pattern.findall(output)
    }


def compile_image(profile, memory_dir, output_dir, variant, stress_seconds):
    if variant not in ("smoke", "full"):
        raise ValueError(f"unknown DDR diagnostic image {variant!r}")
    firmware_dir = output_dir / variant
    object_dir = firmware_dir / "obj"
    firmware_dir.mkdir(parents=True, exist_ok=True)
    object_dir.mkdir(parents=True, exist_ok=True)
    (firmware_dir / "profile.h").write_text(f'#define CPU_PROFILE_NAME "{profile}"\n')

    generated = memory_dir / "software/include/generated"
    variables = generated / "variables.mak"
    cpu_flags = shlex.split(read_make_value(variables, "CPUFLAGS"))
    common = [
        "-std=gnu11", "-O2", *cpu_flags, "-g3", "-no-pie", "-fomit-frame-pointer",
        "-Wall", "-Wextra", "-Werror", "-fno-builtin", "-fno-stack-protector",
        "-Wno-unused-parameter", "-Wno-unused-function",
        "-ffunction-sections", "-fdata-sections", "-fno-lto", "-fstack-usage",
        f"-DSTRESS_SECONDS={stress_seconds}",
        f'-DCPU_PROFILE_NAME="{profile}"',
    ]
    if variant == "smoke":
        common.append("-DDDR_TEST_SMOKE_ONLY=1")
    include = [
        f"-I{firmware_dir}", f"-I{generated}",
        f"-I{memory_dir / 'software/include'}",
        f"-I{ROOT / 'firmware/ddrtest'}",
        f"-I{ROOT / '.deps/litex/litex/soc/software'}",
        f"-I{ROOT / '.deps/litex/litex/soc/software/include'}",
        f"-I{ROOT / '.deps/litex/litex/soc/software/libbase'}",
        f"-I{ROOT / '.deps/litex/litex/soc/cores/cpu/vexriscv'}",
        f"-I{memory_dir / 'software/libc'}",
        f"-I{ROOT / '.deps/pythondata-software-picolibc/pythondata_software_picolibc/data/libc/include'}",
    ]
    gcc = compiler_path()
    object_path = object_dir / "ddr_test.o"
    command = [
        str(gcc), *common, *include,
        "-MMD", "-MP", "-MF", str(object_path.with_suffix(".d")),
        "-c", str(ROOT / "firmware/ddrtest/ddr_test.c"), "-o", str(object_path),
    ]
    subprocess.run(command, cwd=ROOT, check=True)

    packages = read_make_value(variables, "PACKAGES").split()
    libraries = read_make_value(variables, "LIBS").split()
    library_paths = [f"-L{memory_dir / 'software' / package}" for package in packages]
    startup = memory_dir / "firmware/crt0.o"
    if not startup.is_file():
        startup = next(iter(memory_dir.glob("**/crt0.o")), None)
    if startup is None:
        raise RuntimeError(f"{profile}/ddr3 is missing LiteX crt0.o")
    elf = firmware_dir / "ddr_test.elf"
    link = [
        str(gcc), "-nostdlib", "-nodefaultlibs", "-Wl,--no-dynamic-linker",
        "-Wl,--build-id=none", *common, *include,
        f"-L{memory_dir / 'software/include'}", "-T", str(ROOT / "firmware/ddrtest/linker.ld"), "-N",
        "-o", str(elf), str(startup), str(object_path), *library_paths,
        "-Wl,--start-group",
        *[f"-l{item[3:]}" if item.startswith("lib") else f"-l{item}" for item in libraries],
        "-lgcc", "-Wl,--end-group", "-Wl,--gc-sections",
        f"-Wl,-Map,{firmware_dir / 'ddr_test.elf.map'}",
    ]
    subprocess.run(link, cwd=ROOT, check=True)

    binary = firmware_dir / "ddr_test.bin"
    objcopy = gcc.with_name("riscv-none-elf-objcopy")
    readelf = gcc.with_name("riscv-none-elf-readelf")
    nm = gcc.with_name("riscv-none-elf-nm")
    subprocess.run([str(objcopy), "-O", "binary", str(elf), str(binary)], check=True)
    layout = section_layout(readelf, elf)
    symbols_text = subprocess.check_output([str(nm), "-n", str(elf)], text=True)
    symbols = {name: int(address, 16) for address, name in re.findall(
        r"^([0-9a-fA-F]+)\s+\S\s+(_start|_ftext|_fdata|_end|_stack_bottom|_stack_top)$",
        symbols_text, re.M,
    )}
    required = {"_start", "_ftext", "_fdata", "_end", "_stack_bottom", "_stack_top"}
    if set(symbols) != required:
        raise RuntimeError(f"{profile}/{variant} is missing diagnostic linker symbols: {sorted(required - set(symbols))}")
    end = DDR_DIAGNOSTIC_BASE + DDR_DIAGNOSTIC_SIZE
    if symbols["_start"] != DDR_DIAGNOSTIC_BASE or symbols["_ftext"] != DDR_DIAGNOSTIC_BASE:
        raise RuntimeError(f"{profile}/{variant} diagnostic entry is not at 0x{DDR_DIAGNOSTIC_BASE:08x}")
    if symbols["_stack_top"] != end or symbols["_stack_top"] - symbols["_stack_bottom"] != 2048:
        raise RuntimeError(f"{profile}/{variant} stack does not fit in the 16 KiB diagnostic RAM")
    if symbols["_end"] > symbols["_stack_bottom"]:
        raise RuntimeError(f"{profile}/{variant} BSS overlaps its 2 KiB diagnostic stack")
    for name in (".text", ".rodata", ".data", ".bss", ".stack"):
        item = layout.get(name)
        if item and (item["address"] < DDR_DIAGNOSTIC_BASE or item["address"] + item["size"] > end):
            raise RuntimeError(f"{profile}/{variant} {name} extent leaves diagnostic RAM: {item}")
    if binary.stat().st_size > DDR_DIAGNOSTIC_SIZE - 2048:
        raise RuntimeError(
            f"{profile}/{variant} upload image is {binary.stat().st_size} bytes; "
            "the 16 KiB diagnostic RAM must retain its 2 KiB stack"
        )

    return {
        "variant": variant,
        "profile": profile,
        "binary": str(binary.relative_to(ROOT)),
        "elf": str(elf.relative_to(ROOT)),
        "map": str((firmware_dir / "ddr_test.elf.map").relative_to(ROOT)),
        "binary_bytes": binary.stat().st_size,
        "binary_sha256": sha256(binary),
        "elf_sha256": sha256(elf),
        "source_sha256": sha256(ROOT / "firmware/ddrtest/ddr_test.c"),
        "linker_sha256": sha256(ROOT / "firmware/ddrtest/linker.ld"),
        "stress_seconds": stress_seconds,
        "diagnostic_ram": {
            "base": DDR_DIAGNOSTIC_BASE,
            "size_bytes": DDR_DIAGNOSTIC_SIZE,
            "code_data_bss_stack_in_ram": True,
            "stack_bytes": symbols["_stack_top"] - symbols["_stack_bottom"],
            "symbols": symbols,
            "sections": layout,
        },
    }


def build_profile_diagnostics(profile, stress_seconds, force=False):
    if profile not in PROFILES:
        raise ValueError(f"unknown CPU profile {profile!r}; choose from {', '.join(PROFILES)}")
    validate_memory("ddr3")
    memory_dir = profile_build_dir(ROOT, profile, "ddr3")
    build = build_module.build_profile(profile, memory="ddr3", force=force)
    if build.get("status") != "passed" or build.get("memory_mode") != "ddr3":
        raise RuntimeError(f"{profile} DDR3 SoC build did not pass")
    output_dir = memory_dir / "diagnostics"
    metadata_path = output_dir / "ddr-test-metadata.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    source_identity = {
        "firmware/ddrtest/ddr_test.c": sha256(ROOT / "firmware/ddrtest/ddr_test.c"),
        "firmware/ddrtest/linker.ld": sha256(ROOT / "firmware/ddrtest/linker.ld"),
        "patches/litex-sdram-training-status.patch": sha256(ROOT / "patches/litex-sdram-training-status.patch"),
        "patches/litex-gowin-extra-sdc.patch": sha256(ROOT / "patches/litex-gowin-extra-sdc.patch"),
        "patches/litedram-gw2ddrphy-cdc.patch": sha256(ROOT / "patches/litedram-gw2ddrphy-cdc.patch"),
        "bitstream": sha256(ROOT / build["bitstream"]),
    }
    if metadata_path.is_file() and not force:
        try:
            previous = json.loads(metadata_path.read_text())
            images_valid = all(
                (ROOT / image["binary"]).is_file()
                and sha256(ROOT / image["binary"]) == image["binary_sha256"]
                for image in previous.get("images", {}).values()
            )
            if (previous.get("status") == "passed" and previous.get("source_identity") == source_identity
                    and previous.get("stress_seconds") == stress_seconds and images_valid):
                return previous
        except (OSError, ValueError, KeyError):
            pass

    metadata = {
        "status": "in_progress",
        "profile": profile,
        "memory_mode": "ddr3",
        "clock_hz": 48_000_000,
        "ddr_geometry_bytes": DDR_SIZE_BYTES,
        "ddr_uncached_base": 0xC0000000,
        "l2_cache_bytes": 8192,
        "training_patch_sha256": source_identity["patches/litex-sdram-training-status.patch"],
        "gowin_sdc_patch_sha256": source_identity["patches/litex-gowin-extra-sdc.patch"],
        "cdc_patch_sha256": source_identity["patches/litedram-gw2ddrphy-cdc.patch"],
        "source_identity": source_identity,
        "stress_seconds": stress_seconds,
        "images": {},
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    try:
        for variant in ("smoke", "full"):
            metadata["images"][variant] = compile_image(
                profile, memory_dir, output_dir, variant, stress_seconds
            )
        metadata["status"] = "passed"
        metadata["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    except BaseException as error:
        metadata["status"] = "failed"
        metadata["error"] = f"{type(error).__name__}: {error}"
        metadata["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        raise
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=str.lower, choices=[*PROFILES, "all"], nargs="?", default="all")
    parser.add_argument("--stress-seconds", type=int, default=1800)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.stress_seconds < 1:
        parser.error("STRESS_SECONDS must be a positive integer")
    profiles = list(PROFILES) if args.profile == "all" else [args.profile]
    result = {"status": "in_progress", "stress_seconds": args.stress_seconds, "profiles": {}}
    destination = ROOT / "build/ddr3/ddr-test-build.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n")
    try:
        for profile in profiles:
            print(f"Building DDR3 diagnostic firmware for {profile}", flush=True)
            result["profiles"][profile] = build_profile_diagnostics(
                profile, args.stress_seconds, force=args.force
            )
        result["status"] = "passed"
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        result["status"] = "failed"
        result["error"] = f"{type(error).__name__}: {error}"
        destination.write_text(json.dumps(result, indent=2) + "\n")
        print(f"DDR diagnostic build failed: {error}", file=sys.stderr)
        return 1
    result["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    destination.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
