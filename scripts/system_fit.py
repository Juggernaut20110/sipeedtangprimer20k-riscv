#!/usr/bin/env python3
"""Build and report the isolated native-SD Linux system fit study offline.

Running this command creates a new, timestamped study under build/system-fit/.
It only synthesizes and routes local designs; it never programs or communicates
with an FPGA.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.system_fit_reports import classify_outcome, parse_resources, parse_timing  # noqa: E402

LOCK_PATH = ROOT / "dependencies.lock.json"
CPU_LOCK_PATH = ROOT / "cpu-generator.lock.json"
CPU_REPO = ROOT / ".deps/pythondata-cpu-vexriscv"
CPU_VERILOG = CPU_REPO / "pythondata_cpu_vexriscv/verilog"
GENERATOR = CPU_VERILOG
OUTPUT_ROOT = ROOT / "build/system-fit"
DOCS_ROOT = ROOT / "docs/system-fit"
REQUESTED_ROM_BYTES = 32 * 1024
LARGE_ROM_BYTES = 48 * 1024
REQUIRED_STACK_MARGIN_BYTES = 2 * 1024
ETH_SLOT_BYTES = 2048

CANDIDATES = (
    {
        "id": "candidate-1-pinned-linux",
        "cpu_source": "pinned-linux-rtl",
        "i_cache_bytes": 4096,
        "d_cache_bytes": 4096,
        "l2_bytes": 8192,
        "working_sram_bytes": 8192,
        "ethernet_rx_slots": 2,
        "ethernet_tx_slots": 2,
    },
    {
        "id": "candidate-2-compact-memory",
        "cpu_source": "pinned-linux-rtl",
        "i_cache_bytes": 4096,
        "d_cache_bytes": 4096,
        "l2_bytes": 2048,
        "working_sram_bytes": 4096,
        "ethernet_rx_slots": 1,
        "ethernet_tx_slots": 1,
    },
    {
        "id": "candidate-3-linux-2k-caches",
        "cpu_source": "generated-linux-minimal",
        "i_cache_bytes": 2048,
        "d_cache_bytes": 2048,
        "l2_bytes": 2048,
        "working_sram_bytes": 4096,
        "ethernet_rx_slots": 1,
        "ethernet_tx_slots": 1,
    },
    {
        "id": "candidate-4-linux-1k-caches-no-l2",
        "cpu_source": "generated-linux-minimal",
        "i_cache_bytes": 1024,
        "d_cache_bytes": 1024,
        "l2_bytes": 0,
        "working_sram_bytes": 4096,
        "ethernet_rx_slots": 1,
        "ethernet_tx_slots": 1,
    },
)

ATTRIBUTION_BUILDS = (
    {"id": "attribution-ddr-only", "native_sd": False, "ethernet": "none"},
    {"id": "attribution-ddr-native-sd", "native_sd": True, "ethernet": "none"},
    {"id": "attribution-ddr-one-slot-ethernet", "native_sd": False, "ethernet": "rmii"},
)

SOURCE_FILES = (
    "Makefile",
    "gateware/soc.py",
    "gateware/system_fit.py",
    "gateware/ddr3.py",
    "gateware/ddr_geometry.py",
    "gateware/peripherals.py",
    "scripts/system_fit.py",
    "scripts/system_fit_reports.py",
    "scripts/build.py",
    "scripts/cpu_candidates.py",
    "scripts/setup.py",
    "dependencies.lock.json",
    "cpu-generator.lock.json",
)


class StudyError(RuntimeError):
    def __init__(self, message, category="integration failure"):
        super().__init__(message)
        self.category = category


def utc_run_id():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def input_fingerprints():
    files = {}
    for relative in SOURCE_FILES:
        path = ROOT / relative
        if path.is_file():
            files[relative] = sha256_file(path)
    patches = {}
    for path in sorted((ROOT / "patches").glob("*.patch")):
        patches[path.relative_to(ROOT).as_posix()] = sha256_file(path)
    return {"files": files, "repository_patches": patches}


def git_revision(path):
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def dependency_revisions():
    lock = json.loads(LOCK_PATH.read_text())
    revisions = {}
    for entry in lock["repositories"]:
        path = ROOT / ".deps" / entry["name"]
        revisions[entry["name"]] = {
            "requested_commit": entry["commit"],
            "resolved_commit": git_revision(path) if path.is_dir() else None,
            "present": path.is_dir(),
        }
    return revisions


def set_local_tool_path():
    local_bin = ROOT / ".tools/bin"
    compiler_bins = sorted((ROOT / ".tools/riscv-gcc").glob("**/bin"))
    venv_bin = ROOT / ".venv/bin"
    os.environ["PATH"] = os.pathsep.join(
        [*(str(path) for path in compiler_bins), str(local_bin), str(venv_bin), os.environ.get("PATH", "")]
    )


def command_version(command, timeout=30):
    if isinstance(command, str):
        command = [command]
    try:
        result = subprocess.run(command, input="", capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"command": command, "status": "unavailable", "error": str(error)}
    output = (result.stdout + result.stderr).strip()
    line = next((line.strip() for line in output.splitlines() if line.strip()), "")
    return {
        "command": command,
        "returncode": result.returncode,
        "first_line": line[:300],
        "sha256": sha256_file(command[0]) if Path(command[0]).is_file() else None,
    }


def tool_versions():
    set_local_tool_path()
    python = subprocess.check_output([sys.executable, "--version"], text=True).strip()
    gcc = shutil.which("riscv-none-elf-gcc")
    gowin = shutil.which("gw_sh")
    return {
        "python": python,
        "python_executable": str(Path(sys.executable).resolve()),
        "riscv_gcc": command_version([gcc, "--version"]) if gcc else {"status": "unavailable"},
        "meson": command_version([shutil.which("meson"), "--version"])
            if shutil.which("meson") else {"status": "unavailable"},
        "ninja": command_version([shutil.which("ninja"), "--version"])
            if shutil.which("ninja") else {"status": "unavailable"},
        "gowin_shell": command_version([gowin], timeout=45) if gowin else {"status": "unavailable"},
        "java": command_version([shutil.which("java"), "-version"]) if shutil.which("java") else {"status": "unavailable"},
        "sbt": command_version([shutil.which("sbt"), "--script-version"]) if shutil.which("sbt") else {"status": "unavailable"},
    }


def cache_sizes_from_yaml(text):
    sizes = {}
    for bus in ("iBus", "dBus"):
        match = re.search(rf"(?ms)^{bus}:.*?\bsize:\s*(\d+)", text)
        sizes["i_cache_bytes" if bus == "iBus" else "d_cache_bytes"] = int(match.group(1)) if match else None
    return sizes


def audit_cpu_artifacts(rtl_path, yaml_path, expected_i_cache, expected_d_cache):
    rtl_path, yaml_path = Path(rtl_path), Path(yaml_path)
    if not rtl_path.is_file() or not yaml_path.is_file():
        raise StudyError(f"CPU RTL or its generator YAML is missing: {rtl_path}, {yaml_path}")
    rtl = rtl_path.read_text(errors="replace")
    yaml_text = yaml_path.read_text(errors="replace")
    module = re.search(r"\bmodule\s+VexRiscv\s*\((.*?)\);", rtl, re.S)
    if not module:
        raise StudyError(f"CPU artifact does not declare the LiteX VexRiscv module: {rtl_path}")
    ports = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*,?\s*$", module.group(1), re.M))
    required_ports = {
        "externalResetVector", "timerInterrupt", "softwareInterrupt", "externalInterruptArray",
        "iBusWishbone_CYC", "iBusWishbone_STB", "iBusWishbone_ACK", "iBusWishbone_ADR",
        "iBusWishbone_DAT_MISO", "dBusWishbone_CYC", "dBusWishbone_STB", "dBusWishbone_ACK",
        "dBusWishbone_ADR", "dBusWishbone_DAT_MISO", "clk", "reset",
    }
    missing_ports = sorted(required_ports - ports)
    if missing_ports:
        raise StudyError(f"Linux CPU RTL is missing required LiteX Wishbone/interrupt ports: {missing_ports}")

    features = {
        "wishbone_instruction_and_data": all(name in ports for name in (
            "iBusWishbone_CYC", "dBusWishbone_CYC")),
        "interrupt_ports": all(name in ports for name in (
            "timerInterrupt", "softwareInterrupt", "externalInterruptArray")),
        "mmu": bool(re.search(r"MmuPlugin_(?:shared_)?State_", rtl)),
        "supervisor_csrs": bool(re.search(r"SSTATUS|SIE|SIP|sepc|scause|sbadaddr", rtl, re.I)),
        "lr_sc_and_amo": bool(re.search(r"isLrsc", rtl) and re.search(r"amoCtrl_", rtl)),
    }
    missing_features = [name for name, present in features.items() if not present]
    if missing_features:
        raise StudyError(f"Linux CPU RTL is missing audited capabilities: {missing_features}")

    yaml_caches = cache_sizes_from_yaml(yaml_text)
    if yaml_caches["i_cache_bytes"] != expected_i_cache:
        raise StudyError(
            f"generated CPU instruction cache is {yaml_caches['i_cache_bytes']}, expected {expected_i_cache} bytes"
        )
    if yaml_caches["d_cache_bytes"] is not None and yaml_caches["d_cache_bytes"] != expected_d_cache:
        raise StudyError(
            f"generated CPU data cache report is {yaml_caches['d_cache_bytes']}, expected {expected_d_cache} bytes"
        )
    if expected_d_cache and not re.search(r"dataCache_1_io_cpu_execute", rtl):
        raise StudyError("Linux CPU RTL is missing the generated data-cache datapath")
    return {
        "module": "VexRiscv",
        "rtl_path": str(rtl_path),
        "rtl_sha256": sha256_file(rtl_path),
        "yaml_path": str(yaml_path),
        "yaml_sha256": sha256_file(yaml_path),
        "ports": sorted(ports),
        "features": features,
        "cache_sizes": {"i_cache_bytes": expected_i_cache, "d_cache_bytes": expected_d_cache},
        "yaml_reported_cache_sizes": yaml_caches,
    }


def board_pin_assignments(*, native_sd=True, ethernet="rmii", include_gpio=True):
    """Return active board pin assignments and reject duplicate physical pins."""
    from litex.build.generic_platform import Pins
    from litex_boards.platforms.sipeed_tang_primer_20k import Platform

    platform = Platform("standard")
    active = {("clk27", 0), ("serial", 0), ("ddram", 0)}
    if include_gpio:
        active.update(("led", index) for index in range(6))
        active.update(("btn_n", index) for index in range(4))
    if native_sd:
        active.add(("sdcard", 0))
    if ethernet == "rmii":
        active.update({("eth", 0), ("eth_clocks", 0)})

    resources = {}
    sd_widths = {}
    for resource in platform.constraint_manager.available:
        name, number = resource[0], resource[1]
        key = (name, number)
        if key not in active:
            continue
        resource_pins = []
        for constraint in resource[2:]:
            constraints = getattr(constraint, "constraints", [constraint])
            for item in constraints:
                if isinstance(item, Pins):
                    identifiers = list(item.identifiers)
                    resource_pins.extend(identifiers)
                    if name == "sdcard" and hasattr(constraint, "name"):
                        sd_widths[constraint.name] = len(identifiers)
        resources[f"{name}[{number}]"] = resource_pins

    missing = sorted(f"{name}[{number}]" for name, number in active
                     if f"{name}[{number}]" not in resources)
    if missing:
        raise StudyError(f"board pin resources missing from the standard Dock: {missing}")
    if native_sd and sd_widths != {"data": 4, "cmd": 1, "clk": 1, "cd": 1}:
        raise StudyError(f"native SD pin widths are incorrect: {sd_widths}")

    owners = {}
    collisions = []
    for resource, pins in resources.items():
        for pin in pins:
            if pin in owners:
                collisions.append((pin, owners[pin], resource))
            else:
                owners[pin] = resource
    if collisions:
        raise StudyError(f"active board pin assignments collide: {collisions}")
    return {"resources": resources, "native_sd_widths": sd_widths, "collisions": []}


def validate_region_map(soc):
    """Check direct bus regions and IRQ indices for accidental collisions."""
    regions = []
    for name, region in soc.bus.regions.items():
        if region.origin is None or region.linker:
            continue
        start, end = region.origin, region.origin + region.size
        for other_name, other_start, other_end in regions:
            if start < other_end and other_start < end:
                raise StudyError(
                    f"bus address regions {name} and {other_name} overlap: "
                    f"0x{start:08x}..0x{end:08x} / 0x{other_start:08x}..0x{other_end:08x}"
                )
        regions.append((name, start, end))
    locations = getattr(soc.irq, "locs", {})
    values = list(locations.values()) if isinstance(locations, dict) else list(locations)
    if len(values) != len(set(values)):
        raise StudyError(f"IRQ locations collide: {locations}")
    return {"bus_regions_checked": len(regions), "irq_locations": locations}


def audit_sd_gateware(builder, soc, build_name):
    """Check native SD pads, both DMA datapaths, CSRs, and synthesis hierarchy."""
    top = Path(builder.gateware_dir) / f"{build_name}.v"
    if not top.is_file():
        raise StudyError(f"LiteX-generated top-level Verilog is missing: {top}")
    source = top.read_text(errors="replace")
    expected = ["sdcard_data", "sdcard_cmd", "sdcard_clk", "sdcard_cd"]
    ethernet_enabled = hasattr(soc, "ethmac")
    missing = [name for name in expected if name not in source]
    if missing:
        raise StudyError(f"generated RTL lost required SD/Ethernet interface or DMA logic: {missing}")
    if not re.search(r"\binout\s+wire\s+(?:\[3:0\]\s*)?sdcard_data\b", source):
        raise StudyError("four-bit native SD data pads did not lower to a Gowin-compatible inout port")
    if not re.search(r"\binout\s+wire\s+sdcard_cmd\b", source):
        raise StudyError("native SD command pad did not lower to a Gowin-compatible inout port")

    csr_path = Path(builder.output_dir) / "csr.json"
    if not csr_path.is_file():
        raise StudyError("LiteX CSR export is missing")
    csr = json.loads(csr_path.read_text())
    if "sdcard" not in json.dumps(csr).lower():
        raise StudyError("native SD CSRs and interrupt registers are missing from csr.json")

    synthesized = Path(builder.gateware_dir) / "impl/gwsynthesis/project.vg"
    hierarchy = {"generated_rtl": str(top), "csr_json": str(csr_path), "synthesis_netlist": str(synthesized)}
    if synthesized.is_file():
        netlist = synthesized.read_text(errors="replace")
        required_datapaths = ["sdcard", "block2mem", "mem2block"]
        if ethernet_enabled:
            required_datapaths.append("ethmac")
        absent = [name for name in required_datapaths if name not in netlist]
        if absent:
            raise StudyError(f"synthesis hierarchy lost SD/Ethernet datapaths: {absent}")
        hierarchy["synthesis_netlist_sha256"] = sha256_file(synthesized)
        hierarchy["synthesis_datapaths_present"] = True
    else:
        hierarchy["synthesis_datapaths_present"] = False
    hierarchy["ethernet_enabled"] = ethernet_enabled
    return hierarchy


def _pinned_linux_cpu(candidate):
    try:
        from scripts.cpu_candidates import source_revision_check
        revisions = source_revision_check()
    except Exception as error:
        raise StudyError(f"pinned VexRiscv generator source check failed: {error}", "tool failure") from error
    rtl = CPU_VERILOG / "VexRiscv_Linux.v"
    yaml_path = CPU_VERILOG / "VexRiscv_Linux.yaml"
    audit = audit_cpu_artifacts(
        rtl, yaml_path, candidate["i_cache_bytes"], candidate["d_cache_bytes"]
    )
    generator_source = GENERATOR / "src/main/scala/vexriscv/GenCoreDefault.scala"
    linux_recipe = GENERATOR / "Makefile"
    source_text = generator_source.read_text()
    recipe_text = linux_recipe.read_text()
    for option in ("iCacheSize : Int = 4096", "dCacheSize : Int = 4096"):
        if option not in source_text:
            raise StudyError(f"pinned Linux cache default is not the expected 4 KiB: {option}")
    if not re.search(r"(?m)^VexRiscv_Linux\.v:.*\n\s*sbt compile .*--csrPluginConfig linux-minimal", recipe_text):
        raise StudyError("pinned VexRiscv Linux Makefile recipe has changed")
    return {
        **audit,
        "source": "pinned VexRiscv_Linux.v from the locked pythondata-cpu-vexriscv checkout",
        "generator_revisions": revisions,
        "generator_arguments": ["Makefile target VexRiscv_Linux.v", "--csrPluginConfig linux-minimal"],
        "generator_source_sha256": sha256_file(generator_source),
        "linux_recipe_sha256": sha256_file(linux_recipe),
    }


def _generated_linux_cpu(candidate, cpu_dir):
    set_local_tool_path()
    try:
        from scripts.cpu_candidates import source_revision_check, java_runtime_check
        revisions = source_revision_check()
        versions = java_runtime_check()
    except Exception as error:
        raise StudyError(f"pinned VexRiscv generator tool/source check failed: {error}", "tool failure") from error
    cpu_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = Path(tempfile.mkdtemp(prefix="sfit-"))
    rtl_path = cpu_dir / "VexRiscv_Linux.v"
    yaml_path = cpu_dir / "VexRiscv_Linux.yaml"
    output_prefix = os.path.relpath(rtl_path.with_suffix(""), GENERATOR)
    args = [
        "--csrPluginConfig", "linux-minimal",
        "--iCacheSize", str(candidate["i_cache_bytes"]),
        "--dCacheSize", str(candidate["d_cache_bytes"]),
        "--outputFile", output_prefix,
    ]
    run_main = "runMain vexriscv.GenCoreDefault " + " ".join(args)
    command = ["sbt", "-Dsbt.offline=true", "--no-server", "compile", run_main]
    generator_env = os.environ.copy()
    generator_env["XDG_RUNTIME_DIR"] = str(runtime_dir)
    try:
        generator_env = os.environ.copy()
        generator_env["XDG_RUNTIME_DIR"] = str(runtime_dir)
        try:
            result = subprocess.run(
                command, cwd=GENERATOR, env=generator_env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=900,
            )
        except subprocess.TimeoutExpired as error:
            output = error.stdout or ""
            (cpu_dir / "generator.log").write_text(
                output.decode(errors="replace") if isinstance(output, bytes) else output
            )
            raise StudyError(
                f"pinned VexRiscv Linux generator timed out after 900 seconds; see {cpu_dir / 'generator.log'}",
                "tool failure",
            ) from error
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)
    (cpu_dir / "generator.log").write_text(result.stdout)
    if result.returncode:
        raise StudyError(
            f"pinned VexRiscv Linux generator failed with exit {result.returncode}; see {cpu_dir / 'generator.log'}",
            "tool failure",
        )
    audit = audit_cpu_artifacts(
        rtl_path, yaml_path, candidate["i_cache_bytes"], candidate["d_cache_bytes"]
    )
    return {
        **audit,
        "source": "generated from the pinned VexRiscv GenCoreDefault Linux Makefile recipe",
        "generator_revisions": revisions,
        "tool_versions": versions,
        "generator_command": command,
        "generator_arguments": args,
        "generator_source_sha256": sha256_file(GENERATOR / "src/main/scala/vexriscv/GenCoreDefault.scala"),
        "linux_recipe_sha256": sha256_file(GENERATOR / "Makefile"),
    }


def cpu_artifacts(candidate, cpu_dir):
    if candidate["cpu_source"] == "pinned-linux-rtl":
        return _pinned_linux_cpu(candidate)
    return _generated_linux_cpu(candidate, cpu_dir)


def _soc_description(soc, candidate, cpu):
    regions = {}
    for name, region in soc.bus.regions.items():
        regions[name] = {
            "origin": region.origin,
            "size_bytes": region.size,
            "cached": region.cached,
            "mode": region.mode,
        }
    sd_dma = sorted(name for name in soc.bus.masters if name.startswith("sdcard_"))
    irq_locs = getattr(soc.irq, "locs", {})
    return {
        "candidate": candidate,
        "cpu": cpu,
        "profile": soc.profile,
        "cpu_variant": soc.cpu.variant,
        "system_clock_hz": soc.sys_clk_freq,
        "ddr_geometry": {
            "part": "H5TQ1G63EFR",
            "bytes": regions["main_ram"]["size_bytes"],
            "rows": 8192,
            "banks": 8,
            "columns": 1024,
            "data_bits": 16,
            "phy_dll_off": soc.ddrphy.settings.dll_off,
            "cl": soc.ddrphy.settings.cl,
            "cwl": soc.ddrphy.settings.cwl,
            "l2_bytes": soc.ddr_l2_size,
        },
        "working_sram_bytes": soc.bus.regions["sram"].size,
        "diagnostic_memory_present": "ddr_diagnostic_ram" in regions,
        "uncached_ddr_alias_present": "ddr_uncached" in regions,
        "bus_regions": regions,
        "collision_checks": validate_region_map(soc),
        "active_pin_assignments": board_pin_assignments(native_sd=bool(getattr(soc, "sdcard", None)),
                                                        ethernet=soc.ethernet_mode),
        "sdcard": {
            "native": bool(getattr(soc, "sdcard", None)),
            "dma_masters": sd_dma,
            "interrupt_allocated": "sdcard" in irq_locs,
            "mode": "read+write" if getattr(soc, "sdcard", None) else None,
        },
        "ethernet": {
            "enabled": hasattr(soc, "ethmac"),
            "rx_slots": getattr(getattr(soc, "ethmac", None), "rx_slots", None).constant
                if hasattr(soc, "ethmac") else 0,
            "tx_slots": getattr(getattr(soc, "ethmac", None), "tx_slots", None).constant
                if hasattr(soc, "ethmac") else 0,
            "slot_bytes": getattr(getattr(soc, "ethmac", None), "slot_size", None).constant
                if hasattr(soc, "ethmac") else None,
            "rmii_reference_hz": 50_000_000 if hasattr(soc, "ethmac") else None,
        },
        "timer_present": hasattr(soc, "timer0"),
        "uart_present": hasattr(soc, "uart"),
        "constants": sorted(getattr(soc, "constants", {}).keys()),
    }


def validate_soc(soc, candidate, *, native_sd=True, ethernet="rmii"):
    if soc.profile != "linux" or soc.memory != "ddr3" or soc.cpu.variant != "linux":
        raise StudyError("fit target lost the Linux CPU/DDR3 profile")
    if soc.sys_clk_freq != 48_000_000:
        raise StudyError(f"system clock is {soc.sys_clk_freq} Hz, expected 48 MHz")
    if soc.bus.regions["main_ram"].size != 128 * 1024 * 1024 or not soc.bus.regions["main_ram"].cached:
        raise StudyError("cached 128 MiB DDR main RAM region is missing")
    if "ddr_diagnostic_ram" in soc.bus.regions or "ddr_uncached" in soc.bus.regions:
        raise StudyError("fit target retained the diagnostic RAM or uncached alias")
    if not hasattr(soc, "timer0") or not hasattr(soc, "uart"):
        raise StudyError("fit target lost the UART or timer")
    if native_sd:
        expected = {"sdcard_block2mem", "sdcard_mem2block"}
        if not expected.issubset(soc.bus.masters):
            raise StudyError(f"SD read/write DMA masters missing: {sorted(expected - set(soc.bus.masters))}")
        if "sdcard" not in getattr(soc.irq, "locs", {}):
            raise StudyError("native SD interrupt was not allocated")
        sd = soc.sdcard
        if not all(hasattr(sd.ev, event) for event in ("card_detect", "block2mem_dma", "mem2block_dma")):
            raise StudyError("native SD card-detect or DMA interrupt source is missing")
        if "main_ram" not in soc.bus.slaves:
            raise StudyError("SD DMA masters cannot route to the main DDR bus slave")
    if ethernet == "rmii":
        if not hasattr(soc, "ethmac") or soc.ethmac.slot_size.constant != ETH_SLOT_BYTES:
            raise StudyError("RMII Ethernet MAC or 2048-byte slots are missing")
        if candidate["ethernet_rx_slots"] * ETH_SLOT_BYTES < 1518:
            raise StudyError("Ethernet RX slots cannot hold a standard frame")
        if candidate["ethernet_tx_slots"] * ETH_SLOT_BYTES < 1518:
            raise StudyError("Ethernet TX slots cannot hold a standard frame")
    if "rom" not in soc.bus.regions or soc.bus.regions["rom"].size != soc.bios_size:
        raise StudyError("requested BIOS ROM reservation does not match the SoC ROM region")
    validate_region_map(soc)
    board_pin_assignments(native_sd=native_sd, ethernet=ethernet)


def _bios_report(builder, soc, requested_rom):
    software = Path(builder.software_dir)
    elf = software / "bios/bios.elf"
    binary = software / "bios/bios.bin"
    report = {
        "requested_rom_bytes": requested_rom,
        "implemented_rom_bytes": None,
        "rom_reserved_bytes": getattr(soc.bus.regions.get("rom"), "size", None),
        "rom_region_bytes": getattr(soc.bus.regions.get("rom"), "size", None),
        "bios_bin_bytes": binary.stat().st_size if binary.is_file() else None,
        "real_bios_compiled": elf.is_file() and binary.is_file() and binary.stat().st_size > 0,
        "stack_margin_required_bytes": REQUIRED_STACK_MARGIN_BYTES,
        "stack_margin_enforced_by_builder": getattr(builder, "bios_stack_margin", None) == REQUIRED_STACK_MARGIN_BYTES,
        "console": getattr(builder, "bios_console", None),
        "software_libraries": sorted(
            item[0] if isinstance(item, (tuple, list)) else item
            for item in getattr(builder, "software_libraries", [])
        ),
        "stack_peak_usage": "not measured; runtime boot is outside scope",
    }
    if report["real_bios_compiled"]:
        report["implemented_rom_bytes"] = getattr(soc, "integrated_rom_size", None) or binary.stat().st_size
        from scripts.build import parse_elf_sections
        sections = parse_elf_sections(elf, soc.cpu.gcc_triple)
        sram = soc.bus.regions["sram"]
        static_end = sram.origin
        for name in (".data", ".bss", ".boot_sram"):
            section = sections.get(name)
            if section and section["size"]:
                end = section["address"] + section["size"]
                if not sram.origin <= section["address"] <= end <= sram.origin + sram.size:
                    raise StudyError(f"BIOS {name} extends outside working SRAM", "integration failure")
                static_end = max(static_end, end)
        report.update({
            "elf": str(elf),
            "elf_sha256": sha256_file(elf),
            "binary": str(binary),
            "binary_sha256": sha256_file(binary),
            "sections": sections,
            "working_sram_bytes": sram.size,
            "static_bytes": static_end - sram.origin,
            "static_stack_headroom_bytes": sram.origin + sram.size - static_end,
        })
        report["stack_margin_passed"] = (
            report["stack_margin_enforced_by_builder"]
            and report["static_stack_headroom_bytes"] >= REQUIRED_STACK_MARGIN_BYTES
        )
    else:
        report["stack_margin_passed"] = False
    return report


def _capture_build(builder, build_name):
    """Capture Python and subprocess output to the attempt-local console log."""
    log_path = Path(builder.output_dir) / "console.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    old_out, old_err = os.dup(1), os.dup(2)
    try:
        with log_path.open("a") as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            builder.build(run=True, build_name=build_name, hierarchical=True)
    finally:
        os.dup2(old_out, 1)
        os.dup2(old_err, 2)
        os.close(old_out)
        os.close(old_err)
    return log_path


def _full_error_text(attempt_dir):
    parts = []
    for path in (Path(attempt_dir) / "soc/console.log", Path(attempt_dir) / "soc/litex.log",
                 Path(attempt_dir) / "errors.log", Path(attempt_dir) / "cpu/generator.log"):
        if path.is_file():
            parts.append(path.read_text(errors="replace"))
    return "\n".join(parts)


def _classify_exception(error, log_text):
    message = f"{type(error).__name__}: {error}\n{log_text}"
    lower = message.lower()
    if any(token in lower for token in (
        "license verification failed", "license hostid", "af_netlink", "gowin shell",
        "no such file or directory: 'gw_sh'", "toolchain build failed", "process timed out",
        "cross compilation toolchain", "unable to find any of the cross compilation toolchains",
    )):
        return "tool failure"
    if any(token in lower for token in (
        "failed to place", "unplaced", "pr0003", "resources exceed", "resource usage exceeds",
    )):
        return "resource failure"
    if any(token in lower for token in (
        "timing not met", "negative setup slack", "negative hold slack", "recovery slack",
        "removal slack", "violated endpoints", "timing gate failed",
    )):
        return "timing failure"
    if isinstance(error, StudyError):
        return error.category
    if isinstance(error, (OSError, subprocess.SubprocessError)) and "make" in lower:
        return "integration failure"
    return "integration failure"


def _error_summary(error, log_text=""):
    """Make reports readable while retaining the complete failure in each manifest."""
    message = f"{type(error).__name__}: {error}\n{log_text}"
    lower = message.lower()
    overflow = re.search(
        r"region\s+[`'\"]?rom[`'\"]?\s+overflowed\s+by\s+(\d+)\s+bytes",
        message,
        re.I,
    )
    if overflow:
        return f"BIOS ROM overflowed by {int(overflow.group(1)):,} bytes"
    stack = re.search(
        r"stack margin is below required minimum\s*\(([^)]+)\)", message, re.I
    )
    if stack:
        return f"BIOS stack headroom below 2 KiB ({stack.group(1)})"
    if "can't set timing constraint to object" in lower:
        return "Gowin rejected a generated DDR reset-pin timing exception"
    if "can't find object named" in lower:
        return "Gowin rejected a generated floorplan instance path"
    if "license verification failed" in lower or "license hostid" in lower:
        return "Gowin license validation failed"
    if "af_netlink" in lower:
        return "Gowin could not open its local AF_NETLINK socket"
    if "no module named 'litesdcard'" in lower:
        return "LiteSDCard dependency is unavailable"
    if "syntax error near '-'" in lower:
        return "Gowin rejected a generated Verilog identifier"
    text = " ".join(str(error).split())
    return text[:240] if text else "Unspecified build failure"


def _resource_failure_from_report(resources):
    for item in resources.get("resources", {}).values():
        used, capacity = item.get("used"), item.get("capacity")
        if used is not None and capacity is not None and used > capacity:
            return True
    for item in resources.get("clock_resources", {}).values():
        used, capacity = item.get("used"), item.get("capacity")
        if used is not None and capacity is not None and used > capacity:
            return True
    return False


def _timing_artifacts(soc_dir, require_rmii=True):
    base = Path(soc_dir) / "gateware/impl"
    pnr = base / "pnr/project.rpt.txt"
    timing = base / "pnr/project.tr"
    synthesis = base / "gwsynthesis/project_syn_resource.html"
    if not pnr.is_file() or not timing.is_file() or not synthesis.is_file():
        missing = [str(path) for path in (pnr, timing, synthesis) if not path.is_file()]
        raise StudyError(f"Gowin reports missing after build: {missing}", "tool failure")
    return {
        "resource_summary": parse_resources(pnr, synthesis),
        "timing": parse_timing(timing, require_rmii=require_rmii),
        "paths": {
            "pnr": str(pnr), "timing": str(timing), "synthesis": str(synthesis),
            "synthesis_detail": str(synthesis.with_name("project_syn.rpt.html")),
        },
    }


def _file_artifacts(attempt_dir, builder, cpu, soc, build_name):
    attempt_dir = Path(attempt_dir)
    result = {"cpu_rtl": {"path": cpu.get("rtl_path"), "sha256": cpu.get("rtl_sha256")},
              "cpu_yaml": {"path": cpu.get("yaml_path"), "sha256": cpu.get("yaml_sha256")}}
    for key, path in (
        ("bios_elf", Path(builder.software_dir) / "bios/bios.elf"),
        ("bios_bin", Path(builder.software_dir) / "bios/bios.bin"),
        ("soc_verilog", Path(builder.gateware_dir) / f"{build_name}.v"),
        ("csr_json", Path(builder.output_dir) / "csr.json"),
    ):
        result[key] = {"path": str(path), "sha256": sha256_file(path) if path.is_file() else None,
                       "bytes": path.stat().st_size if path.is_file() else None}
    bitstream = Path(builder.get_bitstream_filename(mode="sram"))
    result["bitstream"] = {
        "path": str(bitstream), "sha256": sha256_file(bitstream) if bitstream.is_file() else None,
        "bytes": bitstream.stat().st_size if bitstream.is_file() else None,
    }
    constraints = {}
    constraint_files = {}
    gateware_dir = Path(builder.gateware_dir)
    for path in sorted((*gateware_dir.glob("*.sdc"), *gateware_dir.glob("*.cst"))):
        constraints[path.name] = sha256_file(path)
        constraint_files[path.name] = {"path": str(path), "sha256": constraints[path.name]}
    result["constraint_hashes"] = constraints
    result["constraint_files"] = constraint_files
    if soc is not None:
        result["requested_rom_bytes"] = soc.bios_size
        result["rom_reserved_bytes"] = getattr(soc.bus.regions.get("rom"), "size", None)
        result["implemented_rom_bytes"] = (
            getattr(soc, "integrated_rom_size", None)
            if result["bios_bin"]["bytes"] else None
        )
    return result


def _build_id(candidate, cpu, attempt_dir, *, native_sd=True, ethernet="rmii",
              rx_slots=None, tx_slots=None, l2_bytes=None, sram_bytes=None, rom_bytes=REQUESTED_ROM_BYTES):
    return {
        "candidate": candidate,
        "native_sd": native_sd,
        "ethernet": ethernet,
        "ethernet_rx_slots": candidate.get("ethernet_rx_slots", 1) if rx_slots is None else rx_slots,
        "ethernet_tx_slots": candidate.get("ethernet_tx_slots", 1) if tx_slots is None else tx_slots,
        "ethernet_slot_bytes": ETH_SLOT_BYTES,
        "l2_bytes": candidate.get("l2_bytes", 2048) if l2_bytes is None else l2_bytes,
        "working_sram_bytes": candidate.get("working_sram_bytes", 4096) if sram_bytes is None else sram_bytes,
        "requested_rom_bytes": rom_bytes,
        "cpu": cpu,
        "source_fingerprints": input_fingerprints(),
        "dependency_revisions": dependency_revisions(),
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "attempt_dir": str(Path(attempt_dir)),
    }


def _construct_and_build(candidate, cpu, attempt_dir, *, native_sd=True, ethernet="rmii",
                         rx_slots=None, tx_slots=None, l2_bytes=None, sram_bytes=None,
                         rom_bytes=REQUESTED_ROM_BYTES):
    from litex.soc.integration.builder import Builder
    from gateware.system_fit import SystemFitSoC

    attempt_dir = Path(attempt_dir)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    rtl_path = cpu["rtl_path"]
    actual_rx = candidate.get("ethernet_rx_slots", 1) if rx_slots is None else rx_slots
    actual_tx = candidate.get("ethernet_tx_slots", 1) if tx_slots is None else tx_slots
    actual_l2 = candidate.get("l2_bytes", 2048) if l2_bytes is None else l2_bytes
    actual_sram = candidate.get("working_sram_bytes", 4096) if sram_bytes is None else sram_bytes
    build_name = re.sub(r"[^A-Za-z0-9_]", "_", f"system_fit_{candidate['id']}")
    soc = None
    builder = None
    result = {
        "status": "in_progress",
        "attempt_dir": str(attempt_dir),
        "classification": None,
        "config": _build_id(candidate, cpu, attempt_dir, native_sd=native_sd,
                             ethernet=ethernet, rx_slots=actual_rx, tx_slots=actual_tx,
                             l2_bytes=actual_l2, sram_bytes=actual_sram, rom_bytes=rom_bytes),
        "phases": {"cpu": "passed", "soc": "pending", "bios": "pending", "synthesis": "pending", "pnr": "pending"},
    }
    manifest_path = attempt_dir / "manifest.json"
    try:
        soc = SystemFitSoC(
            cpu_rtl=rtl_path,
            l2_size=actual_l2,
            working_sram_size=actual_sram,
            ethernet=ethernet,
            ethernet_rx_slots=actual_rx,
            ethernet_tx_slots=actual_tx,
            native_sd=native_sd,
            bios_size=rom_bytes,
        )
        validate_soc(soc, candidate, native_sd=native_sd, ethernet=ethernet)
        result["soc"] = _soc_description(soc, candidate, cpu)
        result["phases"]["soc"] = "passed"

        soc_dir = attempt_dir / "soc"
        builder = Builder(
            soc,
            output_dir=str(soc_dir),
            bios_console="lite",
            bios_stack_margin=REQUIRED_STACK_MARGIN_BYTES,
            integrated_rom_auto_size=True,
            hierarchical=True,
        )
        result["phases"]["bios"] = "pending"
        # Gowin uses the build name in the generated Verilog module/file names;
        # hyphens are not legal in those identifiers.
        _capture_build(builder, build_name)
        result["phases"].update({"bios": "passed", "synthesis": "passed", "pnr": "passed"})
        if native_sd:
            result["hierarchy"] = audit_sd_gateware(builder, soc, build_name)
        result["bios"] = _bios_report(builder, soc, rom_bytes)
        if not result["bios"]["real_bios_compiled"] or not result["bios"]["stack_margin_passed"]:
            raise StudyError("real BIOS or required 2 KiB static stack headroom is missing")
        if result["bios"]["console"] != "lite":
            raise StudyError("BIOS was not built with the lite console")
        for required_lib in ("liblitedram", "libliteeth", "liblitesdcard"):
            if required_lib not in result["bios"]["software_libraries"]:
                raise StudyError(f"BIOS library {required_lib} is not in the LiteX Builder")

        result["files"] = _file_artifacts(attempt_dir, builder, cpu, soc=soc, build_name=build_name)
        reports = _timing_artifacts(soc_dir, require_rmii=(ethernet == "rmii"))
        result["resources"] = reports["resource_summary"]
        result["timing"] = reports["timing"]
        result["evidence_paths"] = reports["paths"]
        resource_failure = _resource_failure_from_report(result["resources"])
        timing_pass = result["timing"]["status"] == "passed" and not resource_failure
        result["classification"] = classify_outcome(
            timing_pass=timing_pass,
            resource_failure=resource_failure,
            timing_failure=result["timing"]["status"] != "passed",
        )
        result["phases"]["timing_gate"] = "passed" if timing_pass else "failed"
        result["status"] = "passed" if timing_pass else "failed"
    except Exception as error:  # retain each candidate even when one stage blocks it
        (attempt_dir / "errors.log").write_text(traceback.format_exc())
        log_text = _full_error_text(attempt_dir)
        result["error"] = f"{type(error).__name__}: {error}"
        if log_text:
            result["error_log_tail"] = log_text[-5000:]
        result["failure_log_paths"] = [
            str(path) for path in (
                attempt_dir / "errors.log", attempt_dir / "soc/console.log", attempt_dir / "soc/litex.log"
            ) if path.is_file()
        ]
        result["classification"] = _classify_exception(error, log_text)
        result["error_summary"] = _error_summary(error, log_text)
        result["status"] = "failed"
        if result["phases"].get("soc") == "pending":
            result["phases"]["soc"] = "failed"
        if builder is not None:
            bios_done = (Path(builder.software_dir) / "bios/bios.elf").is_file() and (
                Path(builder.software_dir) / "bios/bios.bin"
            ).is_file()
            synthesis_done = (Path(builder.gateware_dir) / "impl/gwsynthesis/project_syn_resource.html").is_file()
            pnr_done = (Path(builder.gateware_dir) / "impl/pnr/project.tr").is_file()
            result["phases"]["bios"] = "passed" if bios_done else "failed"
            result["phases"]["synthesis"] = "passed" if synthesis_done else "failed"
            result["phases"]["pnr"] = "passed" if pnr_done else "failed"
        else:
            for name in ("bios", "synthesis", "pnr"):
                if result["phases"].get(name) == "pending":
                    result["phases"][name] = "failed"
        if builder is not None and soc is not None:
            synthesis_report = Path(builder.gateware_dir) / "impl/gwsynthesis/project_syn_resource.html"
            pnr_report = Path(builder.gateware_dir) / "impl/pnr/project.rpt.txt"
            if synthesis_report.is_file():
                result["resources"] = parse_resources(pnr_report, synthesis_report)
                detail_report = synthesis_report.with_name("project_syn.rpt.html")
                result["evidence_paths"] = {
                    "synthesis_resources": str(synthesis_report),
                    "synthesis_detail": str(detail_report) if detail_report.is_file() else None,
                }
            if native_sd and (Path(builder.gateware_dir) / f"{build_name}.v").is_file():
                try:
                    result["hierarchy"] = audit_sd_gateware(builder, soc, build_name)
                except Exception as audit_error:
                    result["hierarchy_audit_error"] = f"{type(audit_error).__name__}: {audit_error}"
            try:
                result["bios"] = _bios_report(builder, soc, rom_bytes)
                result["files"] = _file_artifacts(
                    attempt_dir, builder, cpu, soc=soc, build_name=build_name
                )
            except Exception as audit_error:
                result["audit_error"] = f"{type(audit_error).__name__}: {audit_error}"
    write_json(manifest_path, result)
    return result


def _stack_margin_short(text):
    lower = str(text).lower()
    return any(marker in lower for marker in (
        "stack margin is below required minimum",
        "stack headroom below required minimum",
        "insufficient stack",
    ))


def _try_with_memory_retries(candidate, cpu, attempt_root, *, native_sd=True, ethernet="rmii",
                             rx_slots=None, tx_slots=None, l2_bytes=None, sram_bytes=None):
    attempt_root = Path(attempt_root)
    first = _construct_and_build(
        candidate, cpu, attempt_root / "rom32k", native_sd=native_sd, ethernet=ethernet,
        rx_slots=rx_slots, tx_slots=tx_slots, l2_bytes=l2_bytes,
        sram_bytes=sram_bytes, rom_bytes=REQUESTED_ROM_BYTES,
    )
    all_results = [first]
    selected = first
    selected_rom = REQUESTED_ROM_BYTES
    first_log = _full_error_text(first["config"]["attempt_dir"])
    first_failure = (first.get("error", "") + first_log).lower()
    rom_overflow = bool(re.search(
        r"region\s+[`'\"]?rom[`'\"]?\s+overflowed\s+by\s+\d+|"
        r"rom usage exceeds budget\s*\(\s*[^)]*\)",
        first_failure,
    ))
    if first["status"] == "failed" and rom_overflow:
        second = _construct_and_build(
            candidate, cpu, attempt_root / "rom48k", native_sd=native_sd, ethernet=ethernet,
            rx_slots=rx_slots, tx_slots=tx_slots, l2_bytes=l2_bytes,
            sram_bytes=sram_bytes, rom_bytes=LARGE_ROM_BYTES,
        )
        all_results.append(second)
        selected = second
        selected_rom = LARGE_ROM_BYTES
        if second["status"] == "passed":
            return second, all_results
    selected_log = _full_error_text(selected["config"]["attempt_dir"])
    stack_short = _stack_margin_short(selected.get("error", "") + selected_log)
    if selected["status"] == "failed" and stack_short and sram_bytes != 8192:
        second = _construct_and_build(
            candidate, cpu, attempt_root / "sram8k", native_sd=native_sd, ethernet=ethernet,
            rx_slots=rx_slots, tx_slots=tx_slots, l2_bytes=l2_bytes,
            sram_bytes=8192, rom_bytes=selected_rom,
        )
        all_results.append(second)
        if second["status"] == "passed":
            return second, all_results
    return all_results[-1], all_results


def host_checks():
    expected = (
        (4096, 4096, 8192, 8192, 2, 2),
        (4096, 4096, 2048, 4096, 1, 1),
        (2048, 2048, 2048, 4096, 1, 1),
        (1024, 1024, 0, 4096, 1, 1),
    )
    actual = tuple(tuple(item[key] for key in (
        "i_cache_bytes", "d_cache_bytes", "l2_bytes", "working_sram_bytes",
        "ethernet_rx_slots", "ethernet_tx_slots",
    )) for item in CANDIDATES)
    if actual != expected:
        raise StudyError(f"candidate sequence/configuration changed: {actual}")
    if [item["cpu_source"] for item in CANDIDATES] != [
        "pinned-linux-rtl", "pinned-linux-rtl", "generated-linux-minimal", "generated-linux-minimal",
    ]:
        raise StudyError("candidate CPU source sequence does not match the agreed plan")

    cpu_identity = _pinned_linux_cpu(CANDIDATES[0])
    pins = board_pin_assignments(native_sd=True, ethernet="rmii")

    from gateware.soc import ProjectSoC
    defaults = ProjectSoC()
    if defaults.profile != "minimal" or defaults.memory != "onchip" or defaults.bios_size != 32 * 1024:
        raise StudyError("public ProjectSoC defaults changed")
    original = ProjectSoC(profile="linux", memory="ddr3", sdcard="spi", ethernet="rmii")
    original.finalize()
    if "ddr_diagnostic_ram" not in original.bus.regions or "ddr_uncached" not in original.bus.regions:
        raise StudyError("original DDR project configuration lost diagnostic memory or uncached alias")
    if original.bus.regions["ddr_diagnostic_ram"].size != 16 * 1024:
        raise StudyError("original project diagnostic RAM changed size")
    if original.ddr_l2_size != 8 * 1024 or original.ethernet_mode != "rmii" or original.sdcard_mode != "spi":
        raise StudyError("original project configuration defaults were modified")
    public_reset_exception = (
        "set_false_path -to [get_pins {DQS/RESET DQS_1/RESET OSER4_MEM*/RESET}] -setup"
    )
    if public_reset_exception not in original.platform.toolchain.additional_sdc_commands:
        raise StudyError("registered profiles' DDR reset exception changed")

    report = {
        "status": "passed",
        "candidate_sequence": [item["id"] for item in CANDIDATES],
        "candidate_configurations": [dict(item) for item in CANDIDATES],
        "pinned_linux_cpu": {
            "rtl_sha256": cpu_identity["rtl_sha256"],
            "yaml_sha256": cpu_identity["yaml_sha256"],
            "generator_revisions": cpu_identity["generator_revisions"],
            "features": cpu_identity["features"],
            "cache_sizes": cpu_identity["cache_sizes"],
        },
        "board_pin_check": pins,
        "public_defaults": {
            "profile": defaults.profile,
            "memory": defaults.memory,
            "bios_rom_bytes": defaults.bios_size,
            "ddr_l2_bytes": defaults.ddr_l2_size,
            "original_ddr_diagnostic_ram_bytes": original.bus.regions["ddr_diagnostic_ram"].size,
            "uncached_alias_present": "ddr_uncached" in original.bus.regions,
            "sdcard_mode": original.sdcard_mode,
            "ethernet_mode": original.ethernet_mode,
            "ddr_reset_exception_unchanged": public_reset_exception in original.platform.toolchain.additional_sdc_commands,
        },
        "external_checks": {
            "litesdcard_import": importlib_available("litesdcard"),
            "gowin_launcher": shutil.which("gw_sh", path=os.pathsep.join((str(ROOT / ".tools/bin"), os.environ.get("PATH", ""))))
                or "/home/user/.local/bin/gw_sh",
        },
    }
    return report


def importlib_available(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _attempt_dir(item):
    path = item.get("attempt_dir") or item.get("config", {}).get("attempt_dir")
    if not path:
        raise StudyError(f"attempt manifest is missing its directory: {item.get('id', 'unknown')}")
    return Path(path)


def _publish_evidence(run_id, run_report):
    run_docs = DOCS_ROOT / run_id
    evidence_root = run_docs / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    all_attempts = run_report.get("attempts", []) + run_report.get("attribution_attempts", [])
    for item in all_attempts:
        attempt_path = Path(item["manifest_path"])
        attempt_dir = _attempt_dir(item)
        relative = attempt_dir.relative_to(OUTPUT_ROOT / run_id)
        destination = evidence_root / relative
        destination.mkdir(parents=True, exist_ok=True)
        for source in (
            attempt_path,
            attempt_dir / "errors.log",
            attempt_dir / "soc/console.log",
            attempt_dir / "soc/litex.log",
            attempt_dir / "cpu/generator.log",
        ):
            if source.is_file():
                target = destination / ("cpu/generator.log" if source.name == "generator.log" else source.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        cpu_info = item.get("config", {}).get("cpu", {})
        if cpu_info.get("rtl_path"):
            cpu_log = Path(cpu_info["rtl_path"]).parent / "generator.log"
            if cpu_log.is_file():
                (destination / "cpu").mkdir(parents=True, exist_ok=True)
                shutil.copy2(cpu_log, destination / "cpu/generator.log")
        soc_dir = attempt_dir / "soc"
        for name in (
            "gateware/impl/pnr/project.rpt.txt",
            "gateware/impl/pnr/project.tr",
            "gateware/impl/gwsynthesis/project_syn_resource.html",
            "gateware/impl/gwsynthesis/project_syn.rpt.html",
        ):
            source = soc_dir / name
            if source.is_file():
                copied = destination / name
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, copied)
        constraint_dir = soc_dir / "gateware"
        retained_constraints = destination / "constraints"
        for source in sorted((*constraint_dir.glob("*.sdc"), *constraint_dir.glob("*.cst"))):
            retained_constraints.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, retained_constraints / source.name)
    report_path = run_docs / "report.json"
    write_json(report_path, run_report)
    markdown = render_report(run_report, report_path, evidence_root)
    (run_docs / "report.md").write_text(markdown)
    return run_docs / "report.md"


def _resource_markdown(item):
    lines = []
    resource_map = item.get("resources", {}).get("resources", {})
    for name in ("logic", "registers", "bsram", "ssram", "dsp"):
        metric = resource_map.get(name, {})
        used, capacity, headroom = metric.get("used"), metric.get("capacity"), metric.get("headroom")
        if used is None:
            lines.append(f"{name}: unavailable")
        elif capacity is None:
            lines.append(f"{name}: {used} used; capacity/headroom unavailable in Gowin report")
        else:
            lines.append(f"{name}: {used}/{capacity} used; {headroom} headroom")
    clocks = item.get("resources", {}).get("clock_resources", {})
    for name in ("rPLL", "DLL"):
        metric = clocks.get(name)
        if metric:
            lines.append(f"{name}: {metric['used']}/{metric['capacity']} used; {metric['headroom']} headroom")
    return "; ".join(lines)


def _synthesis_resource_markdown(item):
    synthesis = item.get("resources", {}).get("synthesis_resources", {})
    parts = []
    for name in ("logic", "registers", "bsram", "ssram", "dsp", "pll"):
        metric = synthesis.get(name, {})
        used, capacity = metric.get("used"), metric.get("capacity")
        if used is None:
            parts.append(f"{name}: unavailable")
        elif capacity is None:
            parts.append(f"{name}: {used} used; capacity unavailable")
        else:
            parts.append(f"{name}: {used}/{capacity} used; {capacity - used} headroom")
    return "; ".join(parts)


def _attempt_summary(item):
    if item.get("classification") == "routed timing pass":
        slacks = (item.get("timing") or {}).get("worst_slack_ns", {})
        if all(slacks.get(name) is not None for name in ("setup", "hold", "recovery", "removal")):
            values = ", ".join(
                f"{name} {slacks[name]:+.3f} ns"
                for name in ("setup", "hold", "recovery", "removal")
            )
            return f"Routed timing pass; worst slack: {values}"
        return "Routed timing pass"
    summary = item.get("error_summary")
    if summary:
        return summary
    timing_errors = (item.get("timing") or {}).get("errors", [])
    if timing_errors:
        return "; ".join(timing_errors)
    error = item.get("error")
    return _error_summary(error) if error else None


def render_report(run_report, report_path, evidence_root):
    attempts = run_report.get("attempts", [])
    selected = run_report.get("selected_candidate")
    if selected:
        outcome = f"Routed timing closure passed for **{selected}**."
    else:
        outcome = "No complete candidate achieved routed timing closure."
    lines = [
        "# Offline system fit study",
        "",
        f"{outcome} All design builds were offline. FPGA programming, DDR/SD/Ethernet operation, and Linux boot were not performed.",
        "",
        f"Run: `{run_report['run_id']}`. Configuration: [`system-fit-plan.md`](../../system-fit-plan.md). Aggregate machine-readable data: [`report.json`](report.json).",
        "",
        "## Candidate outcomes",
        "",
        "| Candidate attempt | Result | BIOS ROM | Working SRAM / stack headroom | Synthesis resources | Routed resources | Evidence |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in attempts:
        config = item.get("config", {}).get("candidate", {})
        candidate_name = config.get("id", item.get("id", "unknown"))
        bios = item.get("bios", {})
        requested_rom = bios.get("requested_rom_bytes")
        reserved_rom = bios.get("rom_reserved_bytes")
        if bios and bios.get("real_bios_compiled"):
            rom = f"{requested_rom} requested; {bios.get('implemented_rom_bytes')} linked"
        elif bios:
            rom = f"{requested_rom} requested; {reserved_rom} reserved; BIOS link failed"
        else:
            rom = "unavailable"
        stack = bios.get("static_stack_headroom_bytes")
        sram_bytes = item.get("config", {}).get("working_sram_bytes")
        sram_text = f"{sram_bytes} B SRAM" if sram_bytes is not None else "SRAM unavailable"
        stack_text = f"{sram_text}; {stack} B stack headroom" if stack is not None else f"{sram_text}; stack unavailable"
        attempt_dir = _attempt_dir(item)
        attempt_label = f"{candidate_name} ({attempt_dir.name})" if attempt_dir.name in ("rom32k", "rom48k", "sram8k") else candidate_name
        try:
            evidence_rel = attempt_dir.relative_to(OUTPUT_ROOT / run_report["run_id"])
            evidence_base = Path("evidence") / evidence_rel
            evidence_links = [f"[manifest]({(evidence_base / 'manifest.json').as_posix()})"]
            resource_rel = evidence_base / "gateware/impl/pnr/project.rpt.txt"
            timing_rel = evidence_base / "gateware/impl/pnr/project.tr"
            synthesis_rel = evidence_base / "gateware/impl/gwsynthesis/project_syn.rpt.html"
            if (Path(report_path).parent / resource_rel).is_file():
                evidence_links.append(f"[resources]({resource_rel.as_posix()})")
            if (Path(report_path).parent / timing_rel).is_file():
                evidence_links.append(f"[timing]({timing_rel.as_posix()})")
            if (Path(report_path).parent / synthesis_rel).is_file():
                evidence_links.append(f"[synthesis]({synthesis_rel.as_posix()})")
            for suffix, label in ((".sdc", "SDC"), (".cst", "CST")):
                for constraint in sorted((Path(report_path).parent / evidence_base / "constraints").glob(f"*{suffix}")):
                    evidence_links.append(
                        f"[{label}]({(evidence_base / 'constraints' / constraint.name).as_posix()})"
                    )
            evidence_text = ", ".join(evidence_links)
        except ValueError:
            evidence_text = "manifest unavailable"
        lines.append(
            f"| {attempt_label} | {item.get('classification', 'pending')} | {rom} | {stack_text} | "
            f"{_synthesis_resource_markdown(item)} | {_resource_markdown(item)} | {evidence_text} |"
        )
    attribution = run_report.get("attribution_attempts", [])
    if attribution:
        lines += ["", "## Attribution builds", "", "| Build attempt | Result | BIOS ROM | SRAM / stack | Synthesis resources | Routed resources | Finding | Evidence |", "|---|---|---|---|---|---|---|---|"]
        for item in attribution:
            attempt_dir = _attempt_dir(item)
            bios = item.get("bios", {})
            requested_rom = bios.get("requested_rom_bytes")
            reserved_rom = bios.get("rom_reserved_bytes")
            if bios and bios.get("real_bios_compiled"):
                rom_text = f"{requested_rom} requested; {bios.get('implemented_rom_bytes')} linked"
            elif bios:
                rom_text = f"{requested_rom} requested; {reserved_rom} reserved; BIOS link failed"
            else:
                rom_text = "unavailable"
            stack = bios.get("static_stack_headroom_bytes")
            sram_bytes = item.get("config", {}).get("working_sram_bytes")
            sram_text = f"{sram_bytes} B / {stack} B headroom" if stack is not None else f"{sram_bytes} B / unavailable"
            try:
                evidence_rel = attempt_dir.relative_to(OUTPUT_ROOT / run_report["run_id"])
                evidence_base = Path("evidence") / evidence_rel
                evidence_links = [f"[manifest]({(evidence_base / 'manifest.json').as_posix()})"]
                for name, label in (
                    ("gateware/impl/pnr/project.rpt.txt", "resources"),
                    ("gateware/impl/pnr/project.tr", "timing"),
                    ("gateware/impl/gwsynthesis/project_syn.rpt.html", "synthesis"),
                ):
                    path = Path(report_path).parent / evidence_base / name
                    if path.is_file():
                        evidence_links.append(f"[{label}]({(evidence_base / name).as_posix()})")
                for suffix, label in ((".sdc", "SDC"), (".cst", "CST")):
                    for constraint in sorted((Path(report_path).parent / evidence_base / "constraints").glob(f"*{suffix}")):
                        evidence_links.append(
                            f"[{label}]({(evidence_base / 'constraints' / constraint.name).as_posix()})"
                        )
                link = ", ".join(evidence_links)
            except ValueError:
                link = "manifest unavailable"
            compact_error = _attempt_summary(item) or "No compact failure detail recorded"
            lines.append(
                f"| {item.get('id', 'unknown')} ({attempt_dir.name}) | {item.get('classification', 'pending')} | "
                f"{rom_text} | {sram_text} | {_synthesis_resource_markdown(item)} | "
                f"{_resource_markdown(item)} | {compact_error} | {link} |"
            )
    lines += ["", "## Limitations", ""]
    errors = []
    for item in attempts + attribution:
        summary = _attempt_summary(item) if item.get("status") == "failed" else None
        if summary and summary not in errors:
            errors.append(summary)
    for error in errors:
        lines.append(f"- {error} (full trace in the per-attempt manifest).")
    if not errors:
        lines.append("- No host integration or tool blocker was recorded.")
    lines += [
        "",
        "Timing closure requires active 48 MHz system, 96 MHz DDR, and 50 MHz RMII clocks, nonnegative setup/hold/recovery/removal slack, and no unexpected unconstrained internal clock paths. Resource values omitted by the vendor report remain marked unavailable.",
        "",
        f"Per-attempt manifests and retained Gowin evidence are under [`evidence/`](evidence/). Full build outputs remain in `{OUTPUT_ROOT.relative_to(ROOT)}/{run_report['run_id']}/`.",
        "",
    ]
    return "\n".join(lines)


def run_study():
    set_local_tool_path()
    run_id = utc_run_id()
    run_dir = OUTPUT_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    report = {
        "schema": 1,
        "run_id": run_id,
        "created_utc": started,
        "offline": True,
        "hardware_access": "none",
        "plan": "docs/system-fit-plan.md",
        "status": "in_progress",
        "selected_candidate": None,
        "attempts": [],
        "attribution_attempts": [],
        "source_fingerprints": input_fingerprints(),
        "dependency_revisions": dependency_revisions(),
        "tool_versions": tool_versions(),
    }
    write_json(run_dir / "report.json", report)

    cpu_cache = {}
    for index, candidate in enumerate(CANDIDATES):
        print(f"[{index + 1}/4] Building {candidate['id']}", flush=True)
        candidate_dir = run_dir / "candidates" / candidate["id"]
        candidate_dir.mkdir(parents=True, exist_ok=True)
        try:
            cpu = cpu_artifacts(candidate, candidate_dir / "cpu")
            cpu_cache[candidate["id"]] = cpu
            attempt, subattempts = _try_with_memory_retries(candidate, cpu, candidate_dir / "attempts")
            for subattempt in subattempts:
                subattempt["id"] = candidate["id"]
                subattempt["manifest_path"] = str(Path(subattempt["config"]["attempt_dir"]) / "manifest.json")
                report["attempts"].append(subattempt)
            if attempt["status"] == "passed" and attempt["classification"] == "routed timing pass":
                report["selected_candidate"] = candidate["id"]
                break
        except Exception as error:
            failed = {
                "id": candidate["id"],
                "status": "failed",
                "classification": error.category if isinstance(error, StudyError) else "tool failure",
                "error": f"{type(error).__name__}: {error}",
                "error_summary": _error_summary(error, (candidate_dir / "cpu/generator.log").read_text(errors="replace")
                                                   if (candidate_dir / "cpu/generator.log").is_file() else ""),
                "config": {"candidate": candidate},
                "attempt_dir": str(candidate_dir),
                "manifest_path": str(candidate_dir / "manifest.json"),
                "phases": {"cpu": "failed"},
            }
            write_json(candidate_dir / "manifest.json", failed)
            report["attempts"].append(failed)

        write_json(run_dir / "report.json", report)

    if report["selected_candidate"] is None:
        candidate2 = CANDIDATES[1]
        try:
            cpu = cpu_cache.get(candidate2["id"]) or _pinned_linux_cpu(candidate2)
            for attribution in ATTRIBUTION_BUILDS:
                print(f"Attribution build {attribution['id']}", flush=True)
                attr_dir = run_dir / "attribution" / attribution["id"]
                attr_dir.mkdir(parents=True, exist_ok=True)
                result, subattempts = _try_with_memory_retries(
                    candidate2,
                    cpu,
                    attr_dir / "attempts",
                    native_sd=attribution["native_sd"],
                    ethernet=attribution["ethernet"],
                    rx_slots=1,
                    tx_slots=1,
                    l2_bytes=2048,
                    sram_bytes=4096,
                )
                for subattempt in subattempts:
                    subattempt["id"] = attribution["id"]
                    subattempt["manifest_path"] = str(Path(subattempt["config"]["attempt_dir"]) / "manifest.json")
                    report["attribution_attempts"].append(subattempt)
        except Exception as error:
            report["attribution_error"] = f"{type(error).__name__}: {error}"

    report["status"] = "passed" if report["selected_candidate"] else "no_complete_candidate_passed"
    report["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    report["report_path"] = str(run_dir / "report.json")
    write_json(run_dir / "report.json", report)
    write_json(OUTPUT_ROOT / "aggregate.json", report)
    report_path = _publish_evidence(run_id, report)
    report["published_report"] = str(report_path.relative_to(ROOT))
    write_json(run_dir / "report.json", report)
    write_json(OUTPUT_ROOT / "aggregate.json", report)
    print(f"Study report: {report_path}")
    print(f"Machine-readable aggregate: {OUTPUT_ROOT / 'aggregate.json'}")
    return 0 if report["selected_candidate"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("run", "check"), default="run")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            result = host_checks()
            print(json.dumps(result, indent=2))
            return 0
        return run_study()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"system fit study failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
