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
from gateware.soc import PROFILES, ProjectSoC, SYS_CLK_FREQ, UART_BAUDRATE  # noqa: E402
from litex.soc.integration.builder import Builder  # noqa: E402


def source_fingerprint():
    digest = hashlib.sha256()
    for relative in [
        "gateware/soc.py", "firmware/main.c", "firmware/app_logic.c",
        "firmware/app_logic.h", "firmware/linker.ld", "firmware/Makefile",
        "scripts/build.py", "scripts/project.py",
        "dependencies.lock.json", "requirements-py312.txt",
    ]:
        digest.update((ROOT / relative).read_bytes())
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
    if main_used > 32 * 1024:
        raise RuntimeError(f"firmware load image is {main_used} bytes; main RAM is 32768 bytes")
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
    }


def generate_soc(profile, output_dir, run_tools=False, compile_software=False, compile_gateware=False):
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; valid profiles: {', '.join(PROFILES)}")
    output_dir = Path(output_dir).resolve()
    soc = ProjectSoC(profile=profile)
    builder = builder_for(soc, output_dir, compile_software, compile_gateware)
    builder.build(run=run_tools, build_name=f"tang20k_{profile}")
    return soc, builder


def build_profile(profile, force=False):
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; valid profiles: {', '.join(PROFILES)}")
    output_dir = ROOT / "build" / profile
    metadata_path = output_dir / "build-metadata.json"
    fingerprint = source_fingerprint()
    if not force and metadata_path.exists():
        try:
            previous = json.loads(metadata_path.read_text())
            bitstream = output_dir / "bitstream.fs"
            binary = output_dir / "firmware/demo.bin"
            if (previous.get("status") == "passed" and previous.get("source_fingerprint") == fingerprint
                    and bitstream.is_file() and binary.is_file()):
                print(f"Using current {profile} build from {output_dir}")
                return previous
        except (OSError, json.JSONDecodeError):
            pass

    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "profile": profile,
        "liteX_variant": PROFILES[profile],
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
        soc = ProjectSoC(profile=profile)
        builder = builder_for(soc, output_dir, compile_software=True, compile_gateware=True)
        builder.build(run=True, build_name=f"tang20k_{profile}")
        metadata["tool_versions"]["gowin"] = gowin_report_version(
            output_dir / "gateware/impl/pnr/project.rpt.txt"
        )
        bitstream = Path(builder.get_bitstream_filename(mode="sram"))
        if not bitstream.is_file() or bitstream.stat().st_size == 0:
            raise RuntimeError(f"Gowin SRAM bitstream was not produced at {bitstream}")
        public_bitstream = output_dir / "bitstream.fs"
        shutil.copy2(bitstream, public_bitstream)

        firmware = compile_firmware(profile, output_dir, builder)
        metadata.update({
            "firmware": firmware,
            "bitstream": str(public_bitstream.relative_to(ROOT)),
            "bitstream_bytes": public_bitstream.stat().st_size,
            "cpu_variant": soc.cpu.variant,
            "device": soc.platform.device,
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
    parser.add_argument("profile", choices=PROFILES)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build_profile(args.profile, force=args.force)
    except (OSError, subprocess.CalledProcessError, RuntimeError, ValueError) as error:
        print(f"build failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
