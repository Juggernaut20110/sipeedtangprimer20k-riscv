#!/usr/bin/env python3
"""Offline VexRiscv + DDR3 + native SD + RMII fit study for bare metal/RTOS."""

from __future__ import annotations

import datetime as dt
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.system_fit_reports import classify_outcome, parse_resources, parse_timing  # noqa: E402

from scripts import system_fit as common  # noqa: E402

LOCK_PATH = ROOT / "dependencies.lock.json"
CPU_LOCK_PATH = ROOT / "cpu-generator.lock.json"
CPU_VERILOG = ROOT / ".deps/pythondata-cpu-vexriscv/pythondata_cpu_vexriscv/verilog"
GENERATOR = CPU_VERILOG
LITEX_BIOS = ROOT / ".deps/litex/litex/soc/software/bios"
OUTPUT_ROOT = ROOT / "build/rtos-system-fit"
DOCS_ROOT = ROOT / "docs/rtos-system-fit"
ROM_BUDGETS = (16 * 1024, 24 * 1024, 32 * 1024)
STACK_MARGIN_BYTES = 2 * 1024
ETH_SLOT_BYTES = 2048
DDR_BYTES = 128 * 1024 * 1024

CANDIDATES = (
    {
        "id": "candidate-1-pinned-lite",
        "cpu_source": "pinned-lite-vexriscv",
        "cpu_variant": "lite",
        "isa": "rv32im/ILP32",
        "i_cache_bytes": 2048,
        "d_cache_bytes": 0,
        "l2_bytes": 2048,
        "working_sram_bytes": 4096,
        "ethernet_rx_slots": 1,
        "ethernet_tx_slots": 1,
    },
    {
        "id": "candidate-2-pinned-lite-no-l2",
        "cpu_source": "pinned-lite-vexriscv",
        "cpu_variant": "lite",
        "isa": "rv32im/ILP32",
        "i_cache_bytes": 2048,
        "d_cache_bytes": 0,
        "l2_bytes": 0,
        "working_sram_bytes": 4096,
        "ethernet_rx_slots": 1,
        "ethernet_tx_slots": 1,
    },
    {
        "id": "candidate-3-generated-small-rv32im",
        "cpu_source": "generated-small-machine-mode",
        "cpu_variant": "lite",
        "isa": "rv32im/ILP32",
        "i_cache_bytes": 1024,
        "d_cache_bytes": 0,
        "l2_bytes": 0,
        "working_sram_bytes": 4096,
        "ethernet_rx_slots": 1,
        "ethernet_tx_slots": 1,
    },
    {
        "id": "candidate-4-pinned-minimal-rv32i",
        "cpu_source": "pinned-minimal-vexriscv",
        "cpu_variant": "minimal",
        "isa": "rv32i/ILP32",
        "i_cache_bytes": 0,
        "d_cache_bytes": 0,
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
    "Makefile", "dependencies.lock.json", "cpu-generator.lock.json",
    "gateware/soc.py", "gateware/system_fit.py", "gateware/rtos_system_fit.py",
    "gateware/ddr3.py", "gateware/ddr_geometry.py", "gateware/peripherals.py",
    "scripts/system_fit.py", "scripts/system_fit_reports.py", "scripts/rtos_system_fit.py",
    "scripts/rtos_software.py",
    "scripts/setup_rtos_zephyr.py",
    "scripts/cpu_candidates.py", "firmware/rtos_bios/Makefile",
    "firmware/rtos_bios/rtos_main.c", "patches/litex-rtos-bios-ddr-guard.patch",
    "firmware/rtos_system_fit/rtos-dependencies.lock.json",
    "firmware/rtos_system_fit/host-tools.lock.json",
    "firmware/rtos_system_fit/requirements-zephyr.in",
    "firmware/rtos_system_fit/requirements-zephyr.lock.txt",
    "firmware/rtos_system_fit/baremetal/main.c",
    "firmware/rtos_system_fit/baremetal/peripheral_link_probe.c",
    "firmware/rtos_system_fit/baremetal/linker.ld",
    "firmware/rtos_system_fit/freertos/FreeRTOSConfig.h",
    "firmware/rtos_system_fit/freertos/main.c",
    "firmware/rtos_system_fit/zephyr/app/CMakeLists.txt",
    "firmware/rtos_system_fit/zephyr/app/prj.conf",
    "firmware/rtos_system_fit/zephyr/app/prj-peripherals.conf",
    "firmware/rtos_system_fit/zephyr/app/src/main.c",
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


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def source_fingerprints():
    files = {}
    for relative in SOURCE_FILES:
        path = ROOT / relative
        if path.is_file():
            files[relative] = sha256_file(path)
    patches = {}
    for path in sorted((ROOT / "patches").glob("*.patch")):
        patches[path.relative_to(ROOT).as_posix()] = sha256_file(path)
    return {"files": files, "repository_patches": patches}


def cpu_args(candidate, output_file):
    """Exact generated small-core arguments, using the pinned Lite recipe basis."""
    output_prefix = os.path.relpath(Path(output_file).with_suffix(""), GENERATOR)
    return [
        "--hardwareBreakpointCount=0",
        "--iCacheSize=1024",
        "--dCacheSize=0",
        "--pmpRegions=0",
        "--pmpGranularity=256",
        "--pmpAddressMatchingModes=na4,napot,tor",
        "--mulDiv=true",
        "--cfu=false",
        "--singleCycleMulDiv=false",
        "--singleCycleShift=false",
        "--relaxedPcCalculation=false",
        "--bypass=true",
        "--externalInterruptArray=true",
        "--csrPluginConfig=small",
        "--atomics=false",
        "--compressedGen=false",
        "--dBusCachedRelaxedMemoryTranslationRegister=false",
        "--dBusCachedEarlyWaysHits=true",
        "--prediction=static",
        f"--outputFile={output_prefix}",
    ]


def audit_cpu_artifacts(rtl_path, yaml_path, candidate):
    rtl_path, yaml_path = Path(rtl_path), Path(yaml_path)
    if not rtl_path.is_file() or not yaml_path.is_file():
        raise StudyError(f"CPU RTL or YAML is missing: {rtl_path}, {yaml_path}")
    rtl = rtl_path.read_text(errors="replace")
    yaml_text = yaml_path.read_text(errors="replace")
    module = re.search(r"\bmodule\s+VexRiscv\s*\((.*?)\);", rtl, re.S)
    if not module:
        raise StudyError(f"CPU artifact has no VexRiscv top module: {rtl_path}")
    ports = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*,?\s*$", module.group(1), re.M))
    required = {
        "externalResetVector", "timerInterrupt", "softwareInterrupt", "externalInterruptArray",
        "iBusWishbone_CYC", "iBusWishbone_STB", "iBusWishbone_ACK", "iBusWishbone_ADR",
        "iBusWishbone_DAT_MISO", "dBusWishbone_CYC", "dBusWishbone_STB", "dBusWishbone_ACK",
        "dBusWishbone_ADR", "dBusWishbone_DAT_MISO", "clk", "reset",
    }
    missing = sorted(required - ports)
    if missing:
        raise StudyError(f"CPU lacks LiteX Wishbone or machine interrupt ports: {missing}")
    cache_sizes = common.cache_sizes_from_yaml(yaml_text)
    cache_sizes["i_cache_bytes"] = cache_sizes.get("i_cache_bytes") or 0
    cache_sizes["d_cache_bytes"] = cache_sizes.get("d_cache_bytes") or 0
    expected = {"i_cache_bytes": candidate["i_cache_bytes"], "d_cache_bytes": candidate["d_cache_bytes"]}
    if cache_sizes != expected:
        raise StudyError(f"CPU YAML cache sizes {cache_sizes} do not match {expected}")
    csr_tokens = ("CsrPlugin_mtvec", "CsrPlugin_mepc", "CsrPlugin_mstatus", "CsrPlugin_mcause")
    if not all(token in rtl for token in csr_tokens):
        raise StudyError("CPU RTL does not expose machine trap and CSR state")
    if "MmuPlugin" in rtl or "supervisor" in yaml_text.lower():
        raise StudyError("machine-mode target CPU unexpectedly declares MMU/supervisor support")
    return {
        "rtl_path": str(rtl_path),
        "yaml_path": str(yaml_path),
        "rtl_sha256": sha256_file(rtl_path),
        "yaml_sha256": sha256_file(yaml_path),
        "ports": sorted(ports),
        "cache_sizes": cache_sizes,
        "isa": candidate["isa"],
        "machine_trap_csrs_present": True,
        "isa_compiler_flags": candidate["isa"].split("/")[0],
        "declared_mmu": False,
        "declared_atomics": False,
        "declared_compressed": False,
    }


def cpu_artifacts(candidate, cpu_dir):
    from scripts.cpu_candidates import source_revision_check, java_runtime_check

    try:
        revisions = source_revision_check()
    except Exception as error:
        raise StudyError(f"pinned VexRiscv generator revision check failed: {error}", "tool failure") from error
    if candidate["cpu_source"] == "generated-small-machine-mode":
        common.set_local_tool_path()
        try:
            versions = java_runtime_check()
        except Exception as error:
            raise StudyError(f"pinned VexRiscv generator tool check failed: {error}", "tool failure") from error
        cpu_dir.mkdir(parents=True, exist_ok=True)
        rtl = cpu_dir / "VexRiscv_RtosSmall.v"
        yaml = cpu_dir / "VexRiscv_RtosSmall.yaml"
        args = cpu_args(candidate, rtl)
        run_main = "runMain vexriscv.GenCoreDefault " + " ".join(args)
        command = ["sbt", "-Dsbt.offline=true", "--no-server", "compile", run_main]
        runtime_dir = Path(__import__("tempfile").mkdtemp(prefix="rtosfit-"))
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = str(runtime_dir)
        try:
            result = subprocess.run(command, cwd=GENERATOR, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, timeout=900)
        except subprocess.TimeoutExpired as error:
            output = error.stdout or ""
            (cpu_dir / "generator.log").write_text(output.decode(errors="replace") if isinstance(output, bytes) else output)
            raise StudyError("small machine-mode VexRiscv generation timed out", "tool failure") from error
        finally:
            shutil.rmtree(runtime_dir, ignore_errors=True)
        (cpu_dir / "generator.log").write_text(result.stdout)
        if result.returncode:
            raise StudyError(f"small machine-mode VexRiscv generator failed; see {cpu_dir / 'generator.log'}",
                             "tool failure")
        result_cpu = audit_cpu_artifacts(rtl, yaml, candidate)
        return {
            **result_cpu,
            "source": "generated from the locked GenCoreDefault, based on the pinned Lite recipe",
            "generator_revisions": revisions,
            "tool_versions": versions,
            "generator_command": command,
            "generator_arguments": args,
            "generator_source_sha256": sha256_file(GENERATOR / "src/main/scala/vexriscv/GenCoreDefault.scala"),
            "pinned_lite_recipe_sha256": sha256_file(CPU_VERILOG / "Makefile"),
        }

    variant = "Lite" if candidate["cpu_variant"] == "lite" else "Min"
    rtl = CPU_VERILOG / f"VexRiscv_{variant}.v"
    yaml = CPU_VERILOG / f"VexRiscv_{variant}.yaml"
    makefile = CPU_VERILOG / "Makefile"
    recipe = re.search(rf"(?m)^VexRiscv_{variant}\.v:.*\n\t(sbt compile .*)", makefile.read_text())
    if not recipe:
        raise StudyError(f"locked generator Makefile has no VexRiscv_{variant} recipe")
    expected_fragments = {
        "Lite": ("--iCacheSize 2048", "--dCacheSize 0", "--mulDiv true", "--singleCycleShift false", "--singleCycleMulDiv false"),
        "Min": ("--iCacheSize 0", "--dCacheSize 0", "--mulDiv false", "--singleCycleShift false", "--singleCycleMulDiv false", "--bypass false", "--prediction none"),
    }[variant]
    if any(fragment not in recipe.group(1) for fragment in expected_fragments):
        raise StudyError(f"pinned VexRiscv {variant} recipe has changed: {recipe.group(1)}")
    return {
        **audit_cpu_artifacts(rtl, yaml, candidate),
        "source": f"pinned VexRiscv_{variant}.v in locked pythondata-cpu-vexriscv",
        "generator_revisions": revisions,
        "generator_command": ["Makefile", recipe.group(1)],
        "generator_recipe": recipe.group(1),
        "generator_recipe_sha256": sha256_file(makefile),
    }


def validate_soc(soc, candidate, *, native_sd=True, ethernet="rmii"):
    if soc.memory != "ddr3" or soc.cpu.variant != candidate["cpu_variant"]:
        raise StudyError("fit target lost the requested machine-mode CPU/DDR3 profile")
    if soc.sys_clk_freq != 48_000_000:
        raise StudyError(f"system clock is {soc.sys_clk_freq} Hz, expected 48 MHz")
    if soc.bus.regions["main_ram"].size != DDR_BYTES or not soc.bus.regions["main_ram"].cached:
        raise StudyError("cached 128 MiB DDR main RAM region is missing")
    if "ddr_diagnostic_ram" in soc.bus.regions or "ddr_uncached" in soc.bus.regions:
        raise StudyError("RTOS fit target retained a dedicated diagnostic region or alias")
    if hasattr(soc, "leds") or hasattr(soc, "buttons"):
        raise StudyError("unused GPIO was retained in the RTOS fit target")
    if not hasattr(soc, "timer0") or not hasattr(soc, "uart"):
        raise StudyError("RTOS fit target lost its UART or timer")
    if native_sd:
        masters = {"sdcard_block2mem", "sdcard_mem2block"}
        if not masters.issubset(soc.bus.masters):
            raise StudyError(f"native SD read/write DMA masters are missing: {sorted(masters - set(soc.bus.masters))}")
        if "sdcard" not in soc.irq.locs:
            raise StudyError("native SD interrupt was not allocated")
        if not all(hasattr(soc.sdcard.ev, item) for item in ("card_detect", "block2mem_dma", "mem2block_dma")):
            raise StudyError("native SD card-detect or DMA interrupt source is missing")
    if ethernet == "rmii":
        if not hasattr(soc, "ethmac") or soc.ethmac.slot_size.constant != ETH_SLOT_BYTES:
            raise StudyError("RMII LiteEth MAC or 2048-byte slots are missing")
        if (soc.ethmac.rx_slots.constant, soc.ethmac.tx_slots.constant) != (1, 1):
            raise StudyError("fit target must retain one RX and one TX packet slot")
    if "rom" not in soc.bus.regions or soc.bus.regions["rom"].size != soc.bios_size:
        raise StudyError("requested boot ROM size does not match the SoC ROM region")
    common.validate_region_map(soc)
    common.board_pin_assignments(native_sd=native_sd, ethernet=ethernet, include_gpio=False)


def soc_description(soc, candidate, cpu, *, native_sd=True, ethernet="rmii"):
    regions = {
        name: {"origin": region.origin, "size_bytes": region.size,
               "cached": region.cached, "mode": region.mode}
        for name, region in soc.bus.regions.items()
    }
    irq_locs = dict(getattr(soc.irq, "locs", {}))
    return {
        "candidate": dict(candidate),
        "cpu": cpu,
        "cpu_variant": soc.cpu.variant,
        "cpu_gcc_flags": soc.cpu.gcc_flags,
        "system_clock_hz": soc.sys_clk_freq,
        "ddr_geometry": {
            "part": "H5TQ1G63EFR", "bytes": regions["main_ram"]["size_bytes"],
            "rows": 8192, "banks": 8, "columns": 1024, "data_bits": 16,
            "phy_dll_off": soc.ddrphy.settings.dll_off,
            "cl": soc.ddrphy.settings.cl, "cwl": soc.ddrphy.settings.cwl,
            "ddr_clock_hz": 96_000_000, "l2_bytes": soc.ddr_l2_size,
        },
        "working_sram_bytes": regions["sram"]["size_bytes"],
        "unused_gpio_present": False,
        "diagnostic_memory_present": "ddr_diagnostic_ram" in regions,
        "uncached_ddr_alias_present": "ddr_uncached" in regions,
        "bus_regions": regions,
        "collision_checks": common.validate_region_map(soc),
        "active_pin_assignments": common.board_pin_assignments(
            native_sd=native_sd, ethernet=ethernet, include_gpio=False),
        "sdcard": {
            "native": native_sd, "mode": "read+write" if native_sd else None,
            "dma_masters": sorted(name for name in soc.bus.masters if name.startswith("sdcard_")),
            "irq": irq_locs.get("sdcard"),
        },
        "ethernet": {
            "enabled": hasattr(soc, "ethmac"),
            "rx_slots": soc.ethmac.rx_slots.constant if hasattr(soc, "ethmac") else 0,
            "tx_slots": soc.ethmac.tx_slots.constant if hasattr(soc, "ethmac") else 0,
            "slot_bytes": soc.ethmac.slot_size.constant if hasattr(soc, "ethmac") else None,
            "rmii_reference_hz": 50_000_000 if hasattr(soc, "ethmac") else None,
            "irq": irq_locs.get("ethmac"),
        },
        "timer_present": hasattr(soc, "timer0"),
        "timer_irq": irq_locs.get("timer0"),
        "uart_present": hasattr(soc, "uart"),
        "csr_regions": sorted(soc.csr_regions),
        "irq_locations": irq_locs,
    }


def prepare_bios_source(attempt_dir):
    bios_dir = Path(attempt_dir) / "bios-source"
    if bios_dir.exists():
        shutil.rmtree(bios_dir)
    shutil.copytree(LITEX_BIOS, bios_dir)
    patch = ROOT / "patches/litex-rtos-bios-ddr-guard.patch"
    result = subprocess.run(["patch", "--forward", "-p1", "-i", str(patch)],
                            cwd=bios_dir, capture_output=True, text=True)
    (Path(attempt_dir) / "bios-patch.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise StudyError(f"study BIOS guard patch does not apply to pinned LiteX: {result.stderr.strip()}")
    shutil.copy2(ROOT / "firmware/rtos_bios/Makefile", bios_dir / "Makefile")
    shutil.copy2(ROOT / "firmware/rtos_bios/rtos_main.c", bios_dir / "rtos_main.c")
    return bios_dir


def configure_builder(builder, bios_source):
    default_packages = dict(builder.software_packages)
    # Keep LiteX's bootstrap order so the libc package first generates its
    # target-specific picolibc.h before libbase and LiteDRAM compile.
    keep = ("libc", "libcompiler_rt", "libbase", "liblitedram")
    missing = [name for name in keep if name not in default_packages]
    if missing:
        raise StudyError(f"LiteX Builder lacks required BIOS packages: {missing}")
    builder.software_packages = [(name, default_packages[name]) for name in keep]
    builder.software_packages.append(("bios", str(bios_source)))
    builder.software_libraries = list(keep)
    builder.software_always_link_libraries = []
    return list(keep)


def embedded_rom_matches(bios_bin, init_path):
    image = Path(bios_bin).read_bytes()
    lines = [line.strip() for line in Path(init_path).read_text(errors="replace").splitlines() if line.strip()]
    words = [int(line, 16) for line in lines if re.fullmatch(r"[0-9a-fA-F]{8}", line)]
    expected = [int.from_bytes(image[index:index + 4].ljust(4, b"\0"), "little")
                for index in range(0, len(image), 4)]
    return bool(expected) and words[:len(expected)] == expected, len(words), len(expected)


def audit_bios(builder, soc, requested_rom):
    report = common._bios_report(builder, soc, requested_rom)
    if not report["real_bios_compiled"]:
        raise StudyError("reduced BIOS ELF/binary was not built")
    if not report["stack_margin_passed"]:
        raise StudyError("reduced BIOS does not have the required 2 KiB static stack headroom")
    software = Path(builder.software_dir) / "bios"
    map_path = software / "bios.elf.map"
    if not map_path.is_file():
        raise StudyError("BIOS linker map is missing")
    sections = report["sections"]
    rom = soc.bus.regions["rom"]
    sram = soc.bus.regions["sram"]
    for name in (".text", ".rodata"):
        section = sections.get(name)
        if section and section["size"] and not (rom.origin <= section["address"] < section["address"] + section["size"] <= rom.origin + rom.size):
            raise StudyError(f"BIOS {name} is not contained in on-chip ROM")
    for name in (".data", ".bss", ".boot_sram"):
        section = sections.get(name)
        if section and section["size"] and not (sram.origin <= section["address"] < section["address"] + section["size"] <= sram.origin + sram.size):
            raise StudyError(f"BIOS {name} is not contained in on-chip SRAM")
    init_candidates = list(Path(builder.gateware_dir).glob("*__rom_rom.init"))
    if len(init_candidates) != 1:
        raise StudyError(f"expected one embedded BIOS ROM init image, found {len(init_candidates)}")
    matches, embedded_words, image_words = embedded_rom_matches(software / "bios.bin", init_candidates[0])
    if not matches:
        raise StudyError("embedded FPGA ROM contents do not match the compiled BIOS image")
    report.update({
        "linker_map": str(map_path),
        "linker_map_sha256": sha256_file(map_path),
        "bios_recipe": str(Path(bios_source_for(builder)) / "Makefile"),
        "bios_recipe_sha256": sha256_file(Path(bios_source_for(builder)) / "Makefile"),
        "rom_init": str(init_candidates[0]),
        "rom_init_sha256": sha256_file(init_candidates[0]),
        "rom_init_embedded_image_matches": True,
        "rom_init_embedded_word_count": embedded_words,
        "bios_image_word_count": image_words,
        "stack_margin_required_bytes": STACK_MARGIN_BYTES,
        "stack_headroom_bytes": report["static_stack_headroom_bytes"],
        "implemented_rom_bytes": image_words * (soc.bus.data_width // 8),
        "rom_initialized_bytes": image_words * (soc.bus.data_width // 8),
        "rom_region_reserved_bytes": soc.bus.regions["rom"].size,
    })
    return report


def bios_source_for(builder):
    return next(path for name, path in builder.software_packages if name == "bios")


def _error_class(error, log_text):
    if isinstance(error, StudyError):
        return error.category
    return common._classify_exception(error, log_text)


def _error_summary(error, log_text):
    return common._error_summary(error, log_text)


def build_attempt(candidate, cpu, attempt_dir, *, native_sd=True, ethernet="rmii",
                  l2_bytes=None, sram_bytes=None, rom_bytes=16 * 1024):
    from litex.soc.integration.builder import Builder
    from gateware.rtos_system_fit import RtosSystemFitSoC

    attempt_dir = Path(attempt_dir)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    l2_bytes = candidate["l2_bytes"] if l2_bytes is None else l2_bytes
    sram_bytes = candidate["working_sram_bytes"] if sram_bytes is None else sram_bytes
    rx_slots = candidate["ethernet_rx_slots"] if ethernet == "rmii" else 1
    tx_slots = candidate["ethernet_tx_slots"] if ethernet == "rmii" else 1
    build_name = re.sub(r"[^A-Za-z0-9_]", "_", f"rtos_fit_{candidate['id']}")
    result = {
        "status": "in_progress",
        "attempt_dir": str(attempt_dir),
        "classification": None,
        "config": {
            "candidate": candidate,
            "native_sd": native_sd,
            "ethernet": ethernet,
            "ethernet_rx_slots": rx_slots,
            "ethernet_tx_slots": tx_slots,
            "ethernet_slot_bytes": ETH_SLOT_BYTES,
            "l2_bytes": l2_bytes,
            "working_sram_bytes": sram_bytes,
            "requested_rom_bytes": rom_bytes,
            "cpu": cpu,
            "source_fingerprints": source_fingerprints(),
            "dependency_revisions": common.dependency_revisions(),
            "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        },
        "phases": {"cpu": "passed", "soc": "pending", "bios": "pending",
                   "synthesis": "pending", "pnr": "pending"},
    }
    manifest = attempt_dir / "manifest.json"
    builder = None
    soc = None
    try:
        bios_source = prepare_bios_source(attempt_dir)
        with (attempt_dir / "soc-construction.log").open("w") as soc_log:
            with contextlib.redirect_stdout(soc_log), contextlib.redirect_stderr(soc_log):
                soc = RtosSystemFitSoC(
                    cpu_variant=candidate["cpu_variant"], cpu_rtl=cpu["rtl_path"],
                    l2_size=l2_bytes, working_sram_size=sram_bytes,
                    ethernet=ethernet, ethernet_rx_slots=rx_slots, ethernet_tx_slots=tx_slots,
                    native_sd=native_sd, bios_size=rom_bytes,
                )
                validate_soc(soc, candidate, native_sd=native_sd, ethernet=ethernet)
                result["soc"] = soc_description(soc, candidate, cpu, native_sd=native_sd, ethernet=ethernet)
        result["phases"]["soc"] = "passed"
        soc_dir = attempt_dir / "soc"
        builder = Builder(
            soc, output_dir=str(soc_dir), bios_console="lite", bios_lto=True,
            bios_stack_margin=STACK_MARGIN_BYTES, integrated_rom_auto_size=True,
            hierarchical=True,
        )
        selected_libraries = configure_builder(builder, bios_source)
        result["bios_policy"] = {
            "repository_owned_recipe": str(ROOT / "firmware/rtos_bios/Makefile"),
            "local_main_source": str(ROOT / "firmware/rtos_bios/rtos_main.c"),
            "source_adaptation_patch": str(ROOT / "patches/litex-rtos-bios-ddr-guard.patch"),
            "source_adaptation_patch_sha256": sha256_file(ROOT / "patches/litex-rtos-bios-ddr-guard.patch"),
            "selected_libraries": selected_libraries,
            "bios_lto": builder.bios_lto,
            "optimization": "-Os from the pinned LiteX common.mak",
            "section_gc": "-ffunction-sections/-fdata-sections and linker --gc-sections",
            "stack_headroom_required_bytes": STACK_MARGIN_BYTES,
            "requested_rom_bytes": rom_bytes,
        }
        common._capture_build(builder, build_name)
        result["phases"].update({"bios": "passed", "synthesis": "passed", "pnr": "passed"})
        result["hierarchy"] = common.audit_sd_gateware(builder, soc, build_name) if native_sd else None
        result["bios"] = audit_bios(builder, soc, rom_bytes)
        if result["bios"]["console"] != "lite":
            raise StudyError("reduced BIOS console is not the lite console")
        result["files"] = common._file_artifacts(attempt_dir, builder, cpu, soc=soc, build_name=build_name)
        reports = common._timing_artifacts(soc_dir, require_rmii=(ethernet == "rmii"))
        result["resources"] = reports["resource_summary"]
        result["timing"] = reports["timing"]
        result["evidence_paths"] = reports["paths"]
        resource_failure = common._resource_failure_from_report(result["resources"])
        timing_pass = result["timing"]["status"] == "passed" and not resource_failure
        result["classification"] = classify_outcome(
            timing_pass=timing_pass, resource_failure=resource_failure,
            timing_failure=result["timing"]["status"] != "passed")
        result["phases"]["timing_gate"] = "passed" if timing_pass else "failed"
        result["status"] = "passed" if timing_pass else "failed"
    except Exception as error:
        (attempt_dir / "errors.log").write_text(traceback.format_exc())
        log_text = common._full_error_text(attempt_dir)
        result["error"] = f"{type(error).__name__}: {error}"
        result["error_summary"] = _error_summary(error, log_text)
        if log_text:
            result["error_log_tail"] = log_text[-5000:]
        result["failure_log_paths"] = [str(path) for path in (
            attempt_dir / "errors.log", attempt_dir / "bios-patch.log", attempt_dir / "soc-construction.log",
            attempt_dir / "soc/console.log", attempt_dir / "soc/litex.log") if path.is_file()]
        result["classification"] = _error_class(error, log_text)
        result["status"] = "failed"
        if result["phases"]["soc"] == "pending":
            result["phases"]["soc"] = "failed"
        if builder is not None:
            software = Path(builder.software_dir) / "bios"
            if (software / "bios.elf").is_file() and (software / "bios.bin").is_file():
                try:
                    result["bios"] = common._bios_report(builder, soc, rom_bytes)
                except Exception as audit_error:
                    result["bios_audit_error"] = str(audit_error)
            synth = Path(builder.gateware_dir) / "impl/gwsynthesis/project_syn_resource.html"
            pnr = Path(builder.gateware_dir) / "impl/pnr/project.rpt.txt"
            timing = Path(builder.gateware_dir) / "impl/pnr/project.tr"
            if synth.is_file():
                result["resources"] = parse_resources(pnr, synth)
                result["phases"]["synthesis"] = "passed"
            if timing.is_file():
                result["timing"] = parse_timing(timing, require_rmii=(ethernet == "rmii"))
                result["phases"]["pnr"] = "passed"
            if result["phases"]["bios"] == "pending":
                result["phases"]["bios"] = "passed" if (software / "bios.elf").is_file() else "failed"
        write_json(manifest, result)
        return result
    write_json(manifest, result)
    return result


def rom_overflow(attempt):
    text = (attempt.get("error", "") + "\n" + common._full_error_text(attempt["attempt_dir"])).lower()
    return bool(re.search(r"region\s+[`'\"]?rom[`'\"]?\s+overflowed\s+by\s+\d+|rom usage exceeds budget", text))


def stack_margin_short(attempt):
    if attempt.get("bios", {}).get("static_stack_headroom_bytes", STACK_MARGIN_BYTES) < STACK_MARGIN_BYTES:
        return True
    return common._stack_margin_short(attempt.get("error", "") + common._full_error_text(attempt["attempt_dir"]))


def try_candidate(candidate, cpu, attempt_root, *, native_sd=True, ethernet="rmii",
                  l2_bytes=None, sram_bytes=None):
    results = []
    for rom_bytes in ROM_BUDGETS:
        attempt = build_attempt(
            candidate, cpu, Path(attempt_root) / f"rom{rom_bytes // 1024}k",
            native_sd=native_sd, ethernet=ethernet, l2_bytes=l2_bytes,
            sram_bytes=sram_bytes, rom_bytes=rom_bytes,
        )
        results.append(attempt)
        if attempt["status"] == "passed":
            return attempt, results
        if stack_margin_short(attempt) and sram_bytes != 8192:
            retry = build_attempt(
                candidate, cpu, Path(attempt_root) / f"sram8k-rom{rom_bytes // 1024}k",
                native_sd=native_sd, ethernet=ethernet, l2_bytes=l2_bytes,
                sram_bytes=8192, rom_bytes=rom_bytes,
            )
            results.append(retry)
            if retry["status"] == "passed":
                return retry, results
        if not rom_overflow(attempt):
            return attempt, results
    return results[-1], results


def host_checks():
    expected = (
        (2048, 0, 2048, 4096, 1, 1),
        (2048, 0, 0, 4096, 1, 1),
        (1024, 0, 0, 4096, 1, 1),
        (0, 0, 0, 4096, 1, 1),
    )
    actual = tuple(tuple(item[key] for key in (
        "i_cache_bytes", "d_cache_bytes", "l2_bytes", "working_sram_bytes",
        "ethernet_rx_slots", "ethernet_tx_slots")) for item in CANDIDATES)
    if actual != expected:
        raise StudyError(f"RTOS candidate matrix does not match handoff: {actual}")
    if [item["cpu_source"] for item in CANDIDATES] != [
        "pinned-lite-vexriscv", "pinned-lite-vexriscv",
        "generated-small-machine-mode", "pinned-minimal-vexriscv"]:
        raise StudyError("RTOS candidate CPU source order does not match handoff")
    lite = audit_cpu_artifacts(CPU_VERILOG / "VexRiscv_Lite.v", CPU_VERILOG / "VexRiscv_Lite.yaml", CANDIDATES[0])
    minimal = audit_cpu_artifacts(CPU_VERILOG / "VexRiscv_Min.v", CPU_VERILOG / "VexRiscv_Min.yaml", CANDIDATES[3])
    lite_make = (CPU_VERILOG / "Makefile").read_text()
    if not re.search(r"(?m)^VexRiscv_Lite\.v:.*\n\tsbt compile .*--iCacheSize 2048 --dCacheSize 0", lite_make):
        raise StudyError("pinned Lite VexRiscv recipe no longer matches candidate 1/2")
    if "--csrPluginConfig=small" not in cpu_args(CANDIDATES[2], ROOT / "build/cpu-candidates/rtos/VexRiscv_RtosSmall.v"):
        raise StudyError("generated CPU recipe lost machine-mode small CSR config")

    rtos_lock = json.loads((ROOT / "firmware/rtos_system_fit/rtos-dependencies.lock.json").read_text())
    rtos_revisions = {}
    for key, repo_key in (("zephyr", "zephyr"), ("freertos_kernel", "FreeRTOS-Kernel")):
        entry = rtos_lock[key]
        checkout = ROOT / entry["checkout"]
        if not (checkout / ".git").is_dir():
            raise StudyError(f"locked {entry['name']} checkout is missing: {checkout}")
        actual = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        if actual != entry["commit"]:
            raise StudyError(f"locked {entry['name']} checkout is {actual}, expected {entry['commit']}")
        rtos_revisions[key] = actual
    zephyr_patch = ROOT / rtos_lock["zephyr"]["target_only_patch"]
    zephyr_repo = ROOT / rtos_lock["zephyr"]["checkout"]
    patch_forward = subprocess.run(["git", "-C", str(zephyr_repo), "apply", "--check", str(zephyr_patch)],
                                   text=True, capture_output=True)
    patch_reverse = subprocess.run(["git", "-C", str(zephyr_repo), "apply", "--reverse", "--check", str(zephyr_patch)],
                                   text=True, capture_output=True)
    if patch_forward.returncode and patch_reverse.returncode:
        raise StudyError("target-scoped Zephyr DMA ordering patch does not apply cleanly to the pinned revision")
    freertos_config = (ROOT / "firmware/rtos_system_fit/freertos/FreeRTOSConfig.h").read_text()
    freertos_main = (ROOT / "firmware/rtos_system_fit/freertos/main.c").read_text()
    for token in ("configMTIME_BASE_ADDRESS                 0", "configMTIMECMP_BASE_ADDRESS              0",
                  "configISR_STACK_SIZE_WORDS               512", "configCHECK_FOR_STACK_OVERFLOW           2"):
        if token not in freertos_config:
            raise StudyError(f"FreeRTOS configuration lost required no-CLINT/stack setting: {token.strip()}")
    for token in ("freertos_risc_v_trap_handler", "TIMER0_INTERRUPT", "xTaskIncrementTick()",
                  "timer0_ev_pending_write(1)", "xTaskCreateStatic", "vTaskStartScheduler"):
        if token not in freertos_main:
            raise StudyError(f"FreeRTOS timer/trap application integration is missing {token}")
    baremetal_probe = (ROOT / "firmware/rtos_system_fit/baremetal/peripheral_link_probe.c").read_text()
    for token in ("sdcard_read", "sdcard_write", "udp_service", "udp_send", "aligned(4)"):
        if token not in baremetal_probe:
            raise StudyError(f"bare-metal link probe is missing a required real peripheral API: {token}")
    if "/delete-property/ zephyr,entropy" not in (ROOT / "scripts/rtos_software.py").read_text():
        raise StudyError("Zephyr overlay no longer removes the absent entropy peripheral")
    peripheral_config = (ROOT / "firmware/rtos_system_fit/zephyr/app/prj-peripherals.conf").read_text()
    for token in ("CONFIG_NETWORKING=y", "CONFIG_TEST_RANDOM_GENERATOR=y",
                  "CONFIG_ETH_LITEX_LITEETH=y", "CONFIG_SDHC_LITEX_LITESDCARD=y",
                  "CONFIG_DISK_ACCESS=y", "CONFIG_DISK_DRIVER_SDMMC=y"):
        if token not in peripheral_config:
            raise StudyError(f"Zephyr peripheral profile is missing {token}")
    zephyr_cpu_dtsi = (ROOT / ".deps/rtos/zephyr/dts/riscv/riscv32-litex-vexriscv.dtsi").read_text()
    if 'riscv,isa-extensions = "i", "m", "zicsr", "zifencei";' not in zephyr_cpu_dtsi:
        raise StudyError("pinned Zephyr LiteX DTS CPU ISA no longer matches the routed RV32IM CPU")
    if any(f'"{extension}"' in zephyr_cpu_dtsi for extension in ("a", "c", "f", "d")):
        raise StudyError("pinned Zephyr LiteX DTS advertises unsupported A/C/F/D CPU extensions")

    from gateware.rtos_system_fit import RtosSystemFitSoC
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        soc = RtosSystemFitSoC(cpu_variant="lite", cpu_rtl=str(CPU_VERILOG / "VexRiscv_Lite.v"),
                               native_sd=True, ethernet="rmii", l2_size=2048,
                               working_sram_size=4096, bios_size=16 * 1024)
        validate_soc(soc, CANDIDATES[0])
        soc.finalize()
        if not {"timer0", "sdcard", "ethmac"}.issubset(soc.irq.locs):
            raise StudyError(f"expected unique timer, SD, and Ethernet IRQs, got {soc.irq.locs}")
        if "gpio" in soc.csr_regions:
            raise StudyError("unused project GPIO CSR unexpectedly remains")
        if soc.ddr_l2_size != 2048 or soc.bus.regions["ethmac_rx"].size != ETH_SLOT_BYTES or soc.bus.regions["ethmac_tx"].size != ETH_SLOT_BYTES:
            raise StudyError("DDR L2 or standard Ethernet slot size changed")
        constraint = "set_false_path -to [get_pins {ddrphy/DQS/RESET"
        if not any(constraint in item for item in soc.platform.toolchain.additional_sdc_commands):
            raise StudyError("proven narrow DDR PHY reset exception was not retained")
        if not any("INS_LOC \"ddrphy/gw2ddrphy_dqs_hold_0_s0\"" in item for item in soc.platform.toolchain.additional_cst_commands):
            raise StudyError("proven DDR DQS floorplan constraints were not retained")
        legacy = common.host_checks()
    return {
        "status": "passed",
        "candidate_sequence": [item["id"] for item in CANDIDATES],
        "candidate_configurations": [dict(item) for item in CANDIDATES],
        "pinned_lite_cpu": {"rtl_sha256": lite["rtl_sha256"], "yaml_sha256": lite["yaml_sha256"],
                            "machine_trap_csrs_present": lite["machine_trap_csrs_present"]},
        "pinned_minimal_cpu": {"rtl_sha256": minimal["rtl_sha256"], "yaml_sha256": minimal["yaml_sha256"],
                               "machine_trap_csrs_present": minimal["machine_trap_csrs_present"]},
        "generated_cpu_arguments": cpu_args(CANDIDATES[2], ROOT / "build/cpu-candidates/rtos/VexRiscv_RtosSmall.v"),
        "rtos_dependency_revisions": rtos_revisions,
        "zephyr_dma_patch": {"path": str(zephyr_patch.relative_to(ROOT)),
                              "sha256": sha256_file(zephyr_patch),
                              "state": "already applied" if patch_reverse.returncode == 0 else "cleanly applicable"},
        "full_soc_regions": {name: {"origin": region.origin, "size": region.size, "cached": region.cached}
                             for name, region in soc.bus.regions.items()},
        "irq_locations": dict(soc.irq.locs),
        "csr_regions": sorted(soc.csr_regions),
        "native_sd_masters": sorted(name for name in soc.bus.masters if name.startswith("sdcard_")),
        "pin_check": soc_description(soc, CANDIDATES[0], lite)["active_pin_assignments"],
        "legacy_system_fit_regression_check": legacy["status"],
    }


def _copy_if_exists(source, destination):
    source, destination = Path(source), Path(destination)
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def publish_attempt(attempt, evidence_root):
    source = Path(attempt["attempt_dir"])
    destination = Path(evidence_root) / source.name
    destination.mkdir(parents=True, exist_ok=True)
    _copy_if_exists(source / "manifest.json", destination / "manifest.json")
    _copy_if_exists(source / "errors.log", destination / "errors.log")
    _copy_if_exists(source / "bios-patch.log", destination / "bios-patch.log")
    _copy_if_exists(source / "soc/console.log", destination / "soc/console.log")
    _copy_if_exists(source / "soc/litex.log", destination / "soc/litex.log")
    for relative in (
        "soc/software/bios/bios.elf", "soc/software/bios/bios.elf.map",
        "soc/software/bios/bios.bin", "soc/software/include/generated/regions.ld",
        "soc/csr.json",
    ):
        _copy_if_exists(source / relative, destination / relative)
    gateware = source / "soc/gateware"
    out_gateware = destination / "soc/gateware"
    if gateware.is_dir():
        out_gateware.mkdir(parents=True, exist_ok=True)
        for pattern in ("*.sdc", "*.cst", "impl/gwsynthesis/project_syn_resource.html",
                        "impl/gwsynthesis/project_syn.rpt.html", "impl/pnr/project.rpt.txt",
                        "impl/pnr/project.tr", "impl/pnr/project.fs"):
            for path in gateware.glob(pattern):
                _copy_if_exists(path, out_gateware / path.relative_to(gateware))
    return destination


def render_report(report):
    rows = []
    for item in report["attempts"] + report["attribution_attempts"]:
        bios = item.get("bios", {})
        timing = item.get("timing", {}).get("worst_slack_ns", {})
        rows.append(
            f"| {item.get('id', item.get('config', {}).get('candidate', {}).get('id', 'attempt'))} "
            f"({item.get('config', {}).get('requested_rom_bytes', '?')} B ROM) | "
            f"{item.get('classification', item.get('status'))} | "
            f"{bios.get('bios_bin_bytes', 'unavailable')} | "
            f"{bios.get('static_stack_headroom_bytes', 'unavailable')} | "
            f"{timing.get('setup', 'unavailable')} / {timing.get('hold', 'unavailable')} / "
            f"{timing.get('recovery', 'unavailable')} / {timing.get('removal', 'unavailable')} |"
        )
    selected = report.get("selected_candidate")
    selected_attempt = next((item for item in report.get("attempts", [])
                             if selected and item.get("id") == selected and item.get("status") == "passed"), None)
    if selected:
        intro = f"Complete DDR3 + native SD DMA + RMII hardware fit: **routed timing pass** on `{selected}`."
    else:
        intro = "Complete DDR3 + native SD DMA + RMII hardware fit: **no candidate passed routed timing** in this bounded matrix."
    lines = [
        "# Bare-metal and RTOS system fit study", "", intro, "",
        f"Run `{report['run_id']}`. Offline gateware build; no board access. "
        "A compile or routed pass does not establish DDR integrity, SD/Ethernet transfers, RTOS scheduling, or OS boot.", "",
        "## Candidate attempts", "",
        "| Candidate attempt | Result | BIOS binary bytes | Static stack headroom | Setup / hold / recovery / removal slack (ns) |",
        "|---|---|---:|---:|---|",
        *(rows or ["| no attempts recorded | unavailable | unavailable | unavailable | unavailable |"]), "",
    ]
    if selected_attempt:
        config = selected_attempt.get("config", {})
        candidate = config.get("candidate", {})
        bios = selected_attempt.get("bios", {})
        timing = selected_attempt.get("timing", {})
        slack = timing.get("worst_slack_ns", {})
        routed = selected_attempt.get("resources", {}).get("resources", {})
        lines += [
            "## Selected routed configuration", "",
            f"CPU `{candidate.get('id', selected)}`; {candidate.get('isa', 'ISA unavailable')}; "
            f"I-cache {candidate.get('i_cache_bytes', 'unavailable')} B; D-cache {candidate.get('d_cache_bytes', 'unavailable')} B; "
            f"L2 {config.get('l2_bytes', 'unavailable')} B; working SRAM {config.get('working_sram_bytes', 'unavailable')} B. "
            "DDR is 128 MiB at 96 MHz with the system at 48 MHz.", "",
            f"BIOS ROM reservation {bios.get('rom_reserved_bytes', bios.get('requested_rom_bytes', 'unavailable'))} B; "
            f"auto-sized initialized ROM {bios.get('rom_initialized_bytes', bios.get('bios_bin_bytes', 'unavailable'))} B; "
            f"BIOS binary {bios.get('bios_bin_bytes', 'unavailable')} B; static BIOS stack headroom "
            f"{bios.get('static_stack_headroom_bytes', 'unavailable')} B "
            f"(minimum {bios.get('stack_margin_required_bytes', 'unavailable')} B).", "",
            f"Routed slack (setup / hold / recovery / removal): {slack.get('setup', 'unavailable')} / "
            f"{slack.get('hold', 'unavailable')} / {slack.get('recovery', 'unavailable')} / "
            f"{slack.get('removal', 'unavailable')} ns; {timing.get('analyzed_paths', 'unavailable')} paths analyzed; "
            f"{timing.get('setup_violated_endpoints', 'unavailable')} setup and "
            f"{timing.get('hold_violated_endpoints', 'unavailable')} hold violations.", "",
            "| Routed resource | Used / capacity | Headroom | Status |",
            "|---|---:|---:|---|",
        ]
        for key, label in (("logic", "Logic"), ("registers", "Registers"), ("bsram", "BSRAM"),
                           ("ssram", "SSRAM"), ("dsp", "DSP")):
            item = routed.get(key, {})
            used, capacity = item.get("used"), item.get("capacity")
            use = f"{used} / {capacity}" if used is not None and capacity is not None else str(used or "unavailable")
            headroom = item.get("headroom") if item.get("headroom") is not None else "unavailable"
            lines.append(f"| {label} | {use} | {headroom} | {item.get('status', 'unavailable')} |")
        clock_resources = selected_attempt.get("resources", {}).get("clock_resources", {})
        for key, label in (("rPLL", "rPLL"), ("GCLK_PIN", "Global clock pins"), ("DLL", "DLL")):
            item = clock_resources.get(key, {})
            use = f"{item.get('used')} / {item.get('capacity')}" if item.get("used") is not None else "unavailable"
            headroom = item.get("headroom") if item.get("headroom") is not None else "unavailable"
            lines.append(f"| {label} | {use} | {headroom} | {item.get('status', 'unavailable')} |")
        lines.append("")
    lines += ["## Software tracks", "", "Cross-compilation checks source and link integration only. No ELF or binary was run.", ""]
    labels = {"baremetal": "Bare metal", "zephyr_kernel": "Zephyr kernel",
              "zephyr_peripherals": "Zephyr SD/Ethernet", "freertos": "FreeRTOS"}
    for name, track in report.get("software_tracks", {}).items():
        if name == "software_run":
            continue
        lines.append(f"- **{labels.get(name, name)}:** {track.get('status', 'not run')} — "
                     f"{track.get('summary', 'No result recorded.')}")
        profiles = track.get("profiles", {})
        for profile_name, profile in profiles.items():
            lines.append(f"  - Zephyr {profile_name} profile: {profile.get('status')} — {profile.get('summary')}")
    if selected_attempt:
        lines += [
            "", "## DMA and runtime limits", "",
            "The selected CPU has no D-cache and the routed design has no L2; the Ethernet packet windows are MMIO. "
            "The Zephyr LiteX SD and Ethernet sources receive a target-scoped `fence w,o` before DMA start and "
            "`fence i,r` after completion. The bare-metal probe links the real LiteX SD and UDP implementations "
            "but exists only for link checking and is never called. No runtime transfer, DDR, scheduling, network, "
            "or OS boot result is claimed.", "",
        ]
    lines += [
        "", "## Reproduction and evidence", "",
        f"Machine report: [`report.json`](report.json). Attempt manifests, BIOS outputs, "
        f"constraints, Gowin reports, and logs: [`evidence/`](evidence/). "
        f"Full outputs remain under `{OUTPUT_ROOT.relative_to(ROOT)}/{report['run_id']}/`.", "",
        "The design uses the existing patched Hynix DDR3 PHY, 128 MiB DDR, 48 MHz system clock, "
        "96 MHz DDR clock, 50 MHz RMII reference, native four-bit SD read/write DMA, and one 2048-byte "
        "RX/TX Ethernet slot. Zephyr peripheral configuration uses a static locally administered MAC; its "
        "timer-seeded test random provider only satisfies the upstream LiteEth fallback link dependency. "
        "Static timing is gated on setup, hold, recovery, removal, and internal path analysis.", "",
    ]
    return "\n".join(lines)


def run_study():
    common.set_local_tool_path()
    run_id = utc_run_id()
    run_dir = OUTPUT_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    evidence_root = DOCS_ROOT / run_id / "evidence"
    report = {
        "schema": 1, "run_id": run_id,
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "offline": True, "hardware_access": "none",
        "plan": "docs/handoffs/system-fit-baremetal-rtos-luna-xhigh.md",
        "status": "in_progress", "selected_candidate": None,
        "attempts": [], "attribution_attempts": [], "software_tracks": {},
        "candidate_matrix": [dict(item) for item in CANDIDATES],
        "source_fingerprints": source_fingerprints(),
        "dependency_revisions": common.dependency_revisions(),
        "tool_versions": common.tool_versions(),
    }
    write_json(run_dir / "report.json", report)
    cpu_cache = {}
    for index, candidate in enumerate(CANDIDATES, 1):
        print(f"[{index}/4] {candidate['id']}", flush=True)
        candidate_dir = run_dir / "candidates" / candidate["id"]
        candidate_dir.mkdir(parents=True, exist_ok=True)
        try:
            cpu = cpu_artifacts(candidate, candidate_dir / "cpu")
            cpu_cache[candidate["id"]] = cpu
            selected, attempts = try_candidate(candidate, cpu, candidate_dir / "attempts")
            for item in attempts:
                item["id"] = candidate["id"]
                item["manifest_path"] = str(Path(item["attempt_dir"]) / "manifest.json")
                report["attempts"].append(item)
                publish_attempt(item, evidence_root / "candidates" / candidate["id"])
            if selected["status"] == "passed" and selected["classification"] == "routed timing pass":
                report["selected_candidate"] = candidate["id"]
                report["selected_configuration"] = selected.get("config")
                break
        except Exception as error:
            failure = {
                "id": candidate["id"], "status": "failed",
                "classification": error.category if isinstance(error, StudyError) else "tool failure",
                "error": f"{type(error).__name__}: {error}",
                "config": {"candidate": candidate},
                "attempt_dir": str(candidate_dir),
                "phases": {"cpu": "failed"},
            }
            if (candidate_dir / "cpu/generator.log").is_file():
                failure["generator_log"] = str(candidate_dir / "cpu/generator.log")
                failure["generator_log_sha256"] = sha256_file(candidate_dir / "cpu/generator.log")
            write_json(candidate_dir / "manifest.json", failure)
            report["attempts"].append(failure)
        write_json(run_dir / "report.json", report)

    if report["selected_candidate"] is None:
        candidate = CANDIDATES[1]
        cpu = cpu_cache.get(candidate["id"])
        if cpu:
            for attribution in ATTRIBUTION_BUILDS:
                print(f"{attribution['id']}", flush=True)
                selected, attempts = try_candidate(
                    candidate, cpu, run_dir / "attribution" / attribution["id"],
                    native_sd=attribution["native_sd"], ethernet=attribution["ethernet"],
                    l2_bytes=0, sram_bytes=4096,
                )
                for item in attempts:
                    item["id"] = attribution["id"]
                    item["manifest_path"] = str(Path(item["attempt_dir"]) / "manifest.json")
                    report["attribution_attempts"].append(item)
                    publish_attempt(item, evidence_root / "attribution" / attribution["id"])
                write_json(run_dir / "report.json", report)
        else:
            report["attribution_error"] = "candidate 2 CPU artifact was not generated or audited"

    report["status"] = "passed" if report["selected_candidate"] else "no_complete_candidate_passed"
    report["report_path"] = str(run_dir / "report.json")
    report["published_evidence"] = str(evidence_root.relative_to(ROOT))
    write_json(run_dir / "report.json", report)
    if report["selected_candidate"]:
        try:
            from scripts.rtos_software import run_software_tracks
            report["software_tracks"] = run_software_tracks(run_dir / "report.json")
        except Exception as error:
            blocked = {"status": "blocked_dependency",
                       "summary": f"Software-track runner could not start: {type(error).__name__}: {error}"}
            report["software_tracks"] = {name: dict(blocked) for name in
                                          ("baremetal", "zephyr_kernel", "zephyr_peripherals", "freertos")}
    report["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    write_json(run_dir / "report.json", report)
    destination = DOCS_ROOT / run_id
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(run_dir / "report.json", destination / "report.json")
    (destination / "report.md").write_text(render_report(report))
    write_json(OUTPUT_ROOT / "aggregate.json", report)
    return report


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("run", "check"), default="run")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            print(json.dumps(host_checks(), indent=2))
            return 0
        report = run_study()
        print(f"RTOS fit report: {DOCS_ROOT / report['run_id'] / 'report.md'}")
        print(f"Machine aggregate: {OUTPUT_ROOT / 'aggregate.json'}")
        return 0 if report["selected_candidate"] else 1
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"RTOS system fit study failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
