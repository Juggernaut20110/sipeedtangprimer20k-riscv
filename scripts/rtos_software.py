#!/usr/bin/env python3
"""Compile bare-metal, Zephyr, and FreeRTOS tracks for a routed RTOS fit."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEPS = ROOT / ".deps/rtos"
LITEX_SW = ROOT / ".deps/litex/litex/soc/software"
LITEX_CPU = ROOT / ".deps/litex/litex/soc/cores/cpu/vexriscv"
ZEPHYR_PATCH = ROOT / "patches/zephyr-litex-riscv-dma-ordering.patch"
TOOLCHAIN_BIN = ROOT / ".tools/riscv-gcc/xpack-riscv-none-elf-gcc-15.2.0-1/bin"
PREFIX = TOOLCHAIN_BIN / "riscv-none-elf-"
PYTHON = DEPS / "venv/bin/python"
DTC_LOCAL = DEPS / "tools/dtc-1.7.2/usr/bin/dtc"
DTC_BIN = DTC_LOCAL if DTC_LOCAL.is_file() else Path(shutil.which("dtc") or "")
LINKER = ROOT / "firmware/rtos_system_fit/baremetal/linker.ld"


class TrackError(RuntimeError):
    def __init__(self, message: str, *, status: str = "compile_failed"):
        super().__init__(message)
        self.status = status


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def artifact(path: Path) -> dict:
    if not path.is_file():
        return {"path": relative(path), "present": False}
    return {
        "path": relative(path),
        "present": True,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def run_command(command: list[str], cwd: Path, log: Path, records: list,
                env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as stream:
        stream.write("$ " + " ".join(command) + "\n")
        stream.flush()
        result = subprocess.run(
            command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
            text=True, check=False,
        )
    records.append({
        "command": command,
        "cwd": relative(cwd),
        "log": relative(log),
        "returncode": result.returncode,
        "environment": env_overrides or {},
    })
    if result.returncode:
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-28:])
        raise TrackError(f"command exited {result.returncode}: {command[0]}\n{tail}")
    return result


def capture(command: list[str], cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode:
        raise TrackError(f"inspection command failed ({result.returncode}): {' '.join(command)}")
    return result.stdout


def pinned_revisions() -> dict:
    expected = {
        "zephyr": (DEPS / "zephyr", "1f6485eca25431b5ff27ce9a754218c9e559bbbb"),
        "freertos_kernel": (DEPS / "FreeRTOS-Kernel", "3a22924e0a9ddbbc8b0758881c33b3422a5cc20d"),
    }
    result = {}
    for name, (repo, want) in expected.items():
        if not (repo / ".git").exists():
            raise TrackError(f"pinned {name} checkout is missing: {relative(repo)}", status="blocked_dependency")
        got = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             text=True, capture_output=True, check=False)
        if got.returncode:
            raise TrackError(f"cannot read {name} checkout revision: {got.stderr.strip()}", status="blocked_dependency")
        commit = got.stdout.strip()
        result[name] = {"path": relative(repo), "commit": commit, "expected_commit": want}
        if commit != want:
            raise TrackError(f"{name} revision mismatch: expected {want}, got {commit}", status="blocked_dependency")
    gcc = TOOLCHAIN_BIN / "riscv-none-elf-gcc"
    if not gcc.is_file():
        raise TrackError(f"RISC-V compiler is missing: {relative(gcc)}", status="blocked_toolchain")
    version = capture([str(gcc), "--version"], ROOT).splitlines()[0]
    result["compiler"] = {"path": relative(gcc), "version": version,
                          "target": capture([str(gcc), "-dumpmachine"], ROOT).strip(),
                          "march": "rv32i2p0_m", "mabi": "ilp32"}
    if PYTHON.is_file():
        result["zephyr_host_python"] = {
            "path": relative(PYTHON),
            "version": capture([str(PYTHON), "--version"], ROOT).strip(),
            "packages": capture([str(PYTHON), "-m", "pip", "freeze"], ROOT).splitlines(),
        }
    if DTC_BIN.is_file():
        result["dtc"] = {"path": relative(DTC_BIN),
                         "version": capture([str(DTC_BIN), "--version"], ROOT).strip()}
    return result


def ensure_zephyr_patch(log: Path, records: list) -> dict:
    repo = DEPS / "zephyr"
    patch_rel = os.path.relpath(ZEPHYR_PATCH, repo)
    check = subprocess.run(["git", "-C", str(repo), "apply", "--check", patch_rel],
                           text=True, capture_output=True, check=False)
    if check.returncode == 0:
        run_command(["git", "apply", patch_rel], repo, log, records)
        state = "applied"
    else:
        reverse = subprocess.run(["git", "-C", str(repo), "apply", "--reverse", "--check", patch_rel],
                                 text=True, capture_output=True, check=False)
        if reverse.returncode:
            raise TrackError("pinned Zephyr tree is neither clean for nor already contains the target DMA-ordering patch: "
                             + check.stderr.strip(), status="blocked_dependency")
        state = "already_applied"
    return {"path": relative(ZEPHYR_PATCH), "sha256": sha256(ZEPHYR_PATCH), "state": state}


def csr_static_audit(csr_path: Path, overlay: Path) -> dict:
    csr = json.loads(csr_path.read_text())
    memories = csr["memories"]
    constants = csr["constants"]
    expect_mem = {"base": 0x40000000, "size": 128 * 1024 * 1024}
    actual_mem = memories["main_ram"]
    if actual_mem["base"] != expect_mem["base"] or actual_mem["size"] != expect_mem["size"]:
        raise TrackError(f"unexpected main RAM region: {actual_mem}")
    for name in ("ethmac_rx", "ethmac_tx"):
        if memories[name]["size"] != 2048:
            raise TrackError(f"{name} is {memories[name]['size']} bytes, expected 2048")
    required_regs = {
        "sdcard_block2mem_dma_base", "sdcard_block2mem_dma_enable",
        "sdcard_mem2block_dma_base", "sdcard_mem2block_dma_enable",
        "sdcard_phy_card_detect", "ethmac_sram_writer_ev_pending",
        "ethmac_sram_reader_start", "ethphy_mdio_w", "ethphy_mdio_r",
    }
    missing = sorted(required_regs - set(csr["csr_registers"]))
    if missing:
        raise TrackError(f"CSR map lacks expected native DMA/MDIO registers: {missing}")
    if constants.get("config_cpu_interrupts") != 4 or constants.get("timer0_interrupt") != 1:
        raise TrackError("CSR interrupt count/timer IRQ differs from the routed LiteX map")
    overlay_text = overlay.read_text()
    required_overlay = (
        "&cpu0", "clock-frequency = <48000000>", "&ram0",
        "reg = <0x40000000 0x8000000>", "&sdhc0", "bus-width = <4>",
        "&eth0", "0x80000000 0x800", "0x80000800 0x800",
        "&mdio0", "&phy0", "reg = <0>", "phy-connection-type = \"rmii\"",
        "local-mac-address = [02 00 00 00 00 01]",
        "&gpio_out", "&gpio_in", "/delete-property/ zephyr,entropy",
    )
    absent = [item for item in required_overlay if item not in overlay_text]
    if absent:
        raise TrackError(f"Zephyr overlay omits design-specific settings: {absent}")
    return {
        "main_ram": {"base": actual_mem["base"], "bytes": actual_mem["size"]},
        "isa": "rv32im/ILP32; DTS base advertises rv32i + m + zicsr + zifencei",
        "cpu_icache_bytes": 2048,
        "cpu_dcache_bytes": 0,
        "l2_bytes": constants.get("project_ddr_l2_bytes", 0),
        "interrupts": {name: constants.get(name + "_interrupt") for name in ("uart", "timer0", "ethmac", "sdcard")},
        "sd_bus_width": 4,
        "sd_dma_directions": ["card-to-memory", "memory-to-card"],
        "sd_card_detect_register": "sdcard_phy_card_detect",
        "ethernet": {"interface": "RMII", "phy_address": 0, "rx_slots": constants["ethmac_rx_slots"],
                      "tx_slots": constants["ethmac_tx_slots"], "slot_bytes": constants["ethmac_slot_size"]},
        "optional_gpio_disabled": True,
        "reference_entropy_node_removed": True,
        "required_csr_registers_present": True,
    }


def make_zephyr_overlay(csr_path: Path, out_dir: Path, records: list) -> tuple[Path, Path, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    dts = out_dir / "csr.overlay.dts"
    generated_conf = out_dir / "csr.overlay.conf"
    command = [str(ROOT / ".venv/bin/litex_json2dts_zephyr"), "--dts", str(dts), "--config",
               str(generated_conf), str(csr_path)]
    run_command(command, ROOT, out_dir / "overlay-generator.log", records,
                {"PYTHONPATH": str(ROOT / ".deps/litex")})
    with dts.open("a") as stream:
        stream.write("\n/* Tang Primer 20K routed image compatibility overlay. */\n")
        stream.write("&cpu0 { i-cache-line-size = <32>; };\n")
        stream.write("&sdhc0 { bus-width = <4>; };\n")
        stream.write('&eth0 { phy-connection-type = "rmii"; local-mac-address = [02 00 00 00 00 01]; };\n')
        stream.write("&phy0 { reg = <0>; default-speeds = \"10BASE Half-Duplex\", \"10BASE Full-Duplex\", \"100BASE Half-Duplex\", \"100BASE Full-Duplex\"; };\n")
        stream.write("&gpio_out { status = \"disabled\"; };\n")
        stream.write("&gpio_in { status = \"disabled\"; };\n")
        stream.write("/ { chosen { /delete-property/ zephyr,entropy; }; };\n")
    base_conf = out_dir / "csr.overlay.conf"
    with base_conf.open("a") as stream:
        stream.write("\nCONFIG_LITEX_CSR_DATA_WIDTH=32\nCONFIG_LITEX_CSR_ORDERING_BIG=y\n")
    audit = csr_static_audit(csr_path, dts)
    return dts, base_conf, audit


def write_flags(path: Path, flags: list[str]) -> None:
    path.write_text("\n".join(flags) + "\n")


def section_audit(elf: Path, cwd: Path, records: list, prefix: Path, memory: dict) -> dict:
    readelf = str(PREFIX) + "readelf"
    objdump = str(PREFIX) + "objdump"
    nm = str(PREFIX) + "nm"
    header = capture([readelf, "-W", "-h", str(elf)], cwd)
    attrs = capture([readelf, "-A", str(elf)], cwd)
    sections = capture([objdump, "-h", str(elf)], cwd)
    symbols = capture([nm, "-n", str(elf)], cwd)
    (cwd / f"{prefix.name}.readelf-header.txt").write_text(header)
    (cwd / f"{prefix.name}.attributes.txt").write_text(attrs)
    (cwd / f"{prefix.name}.sections.txt").write_text(sections)
    (cwd / f"{prefix.name}.symbols.txt").write_text(symbols)
    match = re.search(r"Entry point address:\s*(0x[0-9a-fA-F]+)", header)
    if not match:
        raise TrackError(f"cannot parse ELF entry address: {elf}")
    entry = int(match.group(1), 16)
    start, size = memory["base"], memory["bytes"]
    if not (start <= entry < start + size):
        raise TrackError(f"ELF entry {entry:#x} is outside DDR main RAM [{start:#x}, {start+size:#x})")
    found = {}
    pattern = re.compile(r"^\s*\d+\s+(\S+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+", re.M)
    for sec, nbytes, address in pattern.findall(sections):
        found[sec] = {"bytes": int(nbytes, 16), "address": int(address, 16)}
    for sec in (".text", ".rodata", ".data", ".bss"):
        info = found.get(sec)
        if info and info["bytes"]:
            if not (start <= info["address"] < info["address"] + info["bytes"] <= start + size):
                raise TrackError(f"ELF {sec} is outside DDR main RAM: {info}")
    lower_attrs = attrs.lower()
    arch_lines = [line.strip() for line in attrs.splitlines() if "Tag_RISCV_arch" in line]
    forbidden = re.search(r"\b(rv32e|_a\d|_c\d|_f\d|_d\d|_z(?:ba|bb|bc|bs|bkb|bkc|bkx|knd|kne|knh|kr|ks|kt|v)\d)\b", lower_attrs)
    if forbidden:
        raise TrackError(f"unexpected ISA extension in {elf}: {forbidden.group(0)}")
    return {"entry_address": entry, "sections": found, "riscv_attributes": arch_lines,
            "symbol_count": sum(1 for line in symbols.splitlines() if line.strip()),
            "isa_audit": "RV32IM/ILP32; no A/C/F/D extensions declared"}


def run_baremetal(attempt_dir: Path, out_dir: Path, records: list) -> dict:
    generated = attempt_dir / "soc/software/include/generated"
    if not (generated / "csr.h").is_file():
        raise TrackError(f"selected routed CSR headers are missing: {relative(generated)}", status="blocked_hardware_artifact")
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_link = out_dir / "generated"
    if not generated_link.exists():
        generated_link.symlink_to(generated, target_is_directory=True)
    gcc = str(PREFIX) + "gcc"
    sources = ROOT / "firmware/rtos_system_fit/baremetal"
    selected_libc = attempt_dir / "soc/software/libc"
    picolibc = ROOT / ".deps/pythondata-software-picolibc/pythondata_software_picolibc/data"
    flags = [
        "-Os", "-g3", "-march=rv32i2p0_m", "-mabi=ilp32", "-D__vexriscv__",
        "-ffunction-sections", "-fdata-sections", "-fno-common", "-fno-pic",
        "-fno-builtin", "-fno-stack-protector", "-no-pie", "-Wall", "-Wextra",
        "-I", str(picolibc / "libc/include"), "-I", str(LITEX_SW / "libbase"),
        "-I", str(LITEX_SW / "include"), "-I", str(LITEX_SW), "-I", str(generated.parent),
        "-I", str(selected_libc), "-I", str(LITEX_CPU),
    ]
    write_flags(out_dir / "compiler-flags.txt", flags)
    compile_units = [
        (sources / "main.c", out_dir / "main.o"),
        (sources / "peripheral_link_probe.c", out_dir / "peripheral_link_probe.o"),
        (LITEX_SW / "liblitesdcard/sdcard.c", out_dir / "sdcard.o"),
        (LITEX_SW / "libliteeth/udp.c", out_dir / "udp.o"),
        (LITEX_CPU / "crt0.S", out_dir / "crt0.o"),
    ]
    for index, (source, object_path) in enumerate(compile_units, 1):
        if not source.is_file():
            raise TrackError(f"compile source is missing: {relative(source)}", status="blocked_dependency")
        run_command([gcc, *flags, "-c", str(source), "-o", str(object_path)], ROOT,
                    out_dir / f"compile-{index:02d}.log", records)
    software = attempt_dir / "soc/software"
    lib_dirs = [software / name for name in ("libbase", "libc", "libcompiler_rt", "liblitedram")]
    libraries = ["-lbase", "-lc", "-lcompiler_rt", "-llitedram"]
    for folder in lib_dirs:
        if not folder.is_dir():
            raise TrackError(f"selected hardware's LiteX support library is missing: {relative(folder)}",
                             status="blocked_hardware_artifact")
    link = [gcc, "-march=rv32i2p0_m", "-mabi=ilp32", "-nostartfiles", "-Wl,--gc-sections",
            "-Wl,--build-id=none", "-Wl,--undefined=rtos_fit_peripheral_link_probe",
            "-Wl,-u,_start", "-T", str(LINKER), "-Wl,-Map=" + str(out_dir / "application.elf.map"),
            *["-L" + str(folder) for folder in lib_dirs], "-Wl,--start-group",
            *[str(unit[1]) for unit in compile_units], *libraries, "-Wl,--end-group",
            "-o", str(out_dir / "application.elf")]
    run_command(link, out_dir, out_dir / "link.log", records)
    memory = {"base": 0x40000000, "bytes": 128 * 1024 * 1024}
    elf_audit = section_audit(out_dir / "application.elf", out_dir, records,
                              out_dir / "application", memory)
    run_command([str(PREFIX) + "objcopy", "-O", "binary", "application.elf", "application.bin"],
                out_dir, out_dir / "objcopy.log", records)
    probe_symbols = capture([str(PREFIX) + "nm", "-u", str(out_dir / "application.elf")], out_dir)
    # -u against a linked ELF should be empty; function retention is proven by its symbol table.
    syms = capture([str(PREFIX) + "nm", str(out_dir / "application.elf")], out_dir)
    for symbol in ("rtos_fit_peripheral_link_probe", "sdcard_read", "sdcard_write", "udp_start", "udp_send"):
        if not re.search(r"\b" + re.escape(symbol) + r"$", syms, re.M):
            raise TrackError(f"link-only peripheral probe did not retain real implementation {symbol}")
    if probe_symbols.strip():
        raise TrackError(f"bare-metal ELF retains unresolved symbols: {probe_symbols.strip()}")
    elf_audit.update({
        "peripheral_link_symbols": ["sdcard_read", "sdcard_write", "udp_start", "udp_service", "udp_send"],
        "probe_is_link_only": True,
        "runtime_invocation": "none",
        "task_stack_budget_bytes": 0,
        "main_ram_bytes_used": sum(v["bytes"] for k, v in elf_audit["sections"].items()
                                    if k in (".text", ".rodata", ".data", ".bss")),
        "artifacts": {name: artifact(out_dir / name) for name in
                      ("application.elf", "application.elf.map", "application.bin", "compiler-flags.txt")},
        "source_files": {
            relative(path): sha256(path) for path, _ in compile_units
        } | {relative(sources / "main.c"): sha256(sources / "main.c"),
             relative(sources / "peripheral_link_probe.c"): sha256(sources / "peripheral_link_probe.c")},
    })
    return elf_audit


def zephyr_failure_status(log_dir: Path) -> str:
    text = "\n".join(path.read_text(errors="replace") for path in log_dir.glob("*.log") if path.is_file()).lower()
    host_markers = ("dtc not found", "no device tree compiler", "could not find dtc", "west: command not found",
                    "no module named 'west'", "no module named 'pykwalify'", "no module named 'elftools'",
                    "no module named 'jsonschema'", "no module named 'yaml'",
                    "python package 'west' not found", "zephyr sdk not found", "toolchain not found")
    return "blocked_host_dependency" if any(marker in text for marker in host_markers) else "compile_failed"


def run_zephyr(attempt_dir: Path, out_dir: Path, records: list) -> dict:
    zephyr = DEPS / "zephyr"
    app = ROOT / "firmware/rtos_system_fit/zephyr/app"
    if not PYTHON.is_file():
        raise TrackError(f"isolated Zephyr host environment is missing: {relative(PYTHON)}; run make rtos-system-fit-setup",
                         status="blocked_host_dependency")
    if not DTC_BIN.is_file():
        raise TrackError("device-tree-compiler (dtc) is missing; run make rtos-system-fit-setup after acquiring the pinned host tool",
                         status="blocked_host_dependency")
    preflight = subprocess.run([str(PYTHON), "-c", "import jsonschema, yaml, pykwalify, elftools, west"],
                               cwd=ROOT, text=True, capture_output=True, check=False)
    if preflight.returncode:
        raise TrackError("isolated Zephyr host environment lacks required modules: " + preflight.stderr.strip(),
                         status="blocked_host_dependency")
    csr_path = attempt_dir / "soc/csr.json"
    dts, base_overlay, audit = make_zephyr_overlay(csr_path, out_dir / "generated", records)
    patch_info = ensure_zephyr_patch(out_dir / "apply-dma-patch.log", records)
    diff = subprocess.run(["git", "-C", str(zephyr), "diff", "--binary", "HEAD"],
                          capture_output=True, check=True).stdout
    patch_info["source_tree_diff_sha256"] = hashlib.sha256(diff).hexdigest()
    audit["dma_ordering_patch"] = patch_info
    audit["random_provider"] = (
        "CONFIG_TEST_RANDOM_GENERATOR supplies the pinned LiteEth driver's linked fallback; "
        "the overlay sets a static locally administered MAC, runtime is not performed"
    )
    profiles = {
        "kernel": None,
        "peripherals": out_dir / "generated/peripherals.conf",
    }
    peripheral_config = ROOT / "firmware/rtos_system_fit/zephyr/app/prj-peripherals.conf"
    shutil.copy2(peripheral_config, profiles["peripherals"])
    ccache_dir = DEPS / "ccache"
    ccache_dir.mkdir(parents=True, exist_ok=True)
    outcomes = {}
    for profile, extra_config in profiles.items():
        build_dir = out_dir / f"build-{profile}"
        logs = out_dir / f"configure-{profile}.log"
        env = {
            "ZEPHYR_BASE": str(zephyr),
            "ZEPHYR_TOOLCHAIN_VARIANT": "cross-compile",
            "CROSS_COMPILE": str(PREFIX),
            "CROSS_COMPILE_TOOLCHAIN_PATH": str(Path(capture([str(PREFIX) + "gcc", "-print-sysroot"], ROOT).strip())),
            "PYTHON_EXECUTABLE": str(PYTHON),
            "Python3_EXECUTABLE": str(PYTHON),
            "PATH": str(TOOLCHAIN_BIN) + os.pathsep + str(DTC_BIN.parent) + os.pathsep + str(ROOT / ".venv/bin") + os.pathsep + os.environ.get("PATH", ""),
            "CCACHE_DIR": str(ccache_dir),
        }
        command = ["cmake", "-S", str(app), "-B", str(build_dir), "-G", "Unix Makefiles",
                   "-DBOARD=litex_vexriscv", "-DCMAKE_MAKE_PROGRAM=/usr/bin/make",
                   "-DDTC=" + str(DTC_BIN),
                   "-DZEPHYR_BASE=" + str(zephyr), "-DZEPHYR_TOOLCHAIN_VARIANT=cross-compile",
                   "-DCROSS_COMPILE=" + str(PREFIX), "-DSYSROOT_DIR=" + env["CROSS_COMPILE_TOOLCHAIN_PATH"],
                   "-DPython3_EXECUTABLE=" + str(PYTHON), "-DDTC_OVERLAY_FILE=" + str(dts),
                   "-DOVERLAY_CONFIG=" + str(base_overlay)]
        if extra_config:
            command[-1] += ";" + str(extra_config)
        try:
            run_command(command, ROOT, logs, records, env)
            run_command(["cmake", "--build", str(build_dir), "--parallel", "2"], ROOT,
                        out_dir / f"build-{profile}.log", records, env)
            elf = build_dir / "zephyr/zephyr.elf"
            map_path = build_dir / "zephyr/zephyr.map"
            if not elf.is_file():
                raise TrackError(f"Zephyr {profile} build produced no ELF")
            details = section_audit(elf, build_dir, records, build_dir / "zephyr/zephyr", audit["main_ram"])
            details["artifacts"] = {name: artifact(path) for name, path in {
                "elf": elf, "map": map_path, "bin": build_dir / "zephyr/zephyr.bin",
                "config": build_dir / "zephyr/.config", "dts": build_dir / "zephyr/zephyr.dts",
            }.items()}
            details["profile"] = profile
            details["runtime_invocation"] = "none"
            outcomes[profile] = {"status": "passed", "summary": f"Zephyr {profile} ELF cross-compiled for the routed CSR map.",
                                 **details}
        except TrackError as error:
            status = error.status if error.status != "compile_failed" else zephyr_failure_status(out_dir)
            outcomes[profile] = {"status": status, "summary": str(error),
                                 "logs": [relative(path) for path in out_dir.glob(f"*{profile}*.log")]}
    return {"status": "passed" if all(value["status"] == "passed" for value in outcomes.values()) else
            ("partial" if any(value["status"] == "passed" for value in outcomes.values()) else "blocked"),
            "summary": "Zephyr machine-mode kernel and peripheral-enabled builds were attempted against the selected hardware.",
            "source_revision": "1f6485eca25431b5ff27ce9a754218c9e559bbbb",
            "tag": "v4.4.1", "design_audit": audit, "profiles": outcomes,
            "overlay": artifact(dts), "config_overlay": artifact(base_overlay)}


def run_freertos(attempt_dir: Path, out_dir: Path, records: list) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    generated = attempt_dir / "soc/software/include/generated"
    kernel = DEPS / "FreeRTOS-Kernel"
    selected_libc = attempt_dir / "soc/software/libc"
    picolibc = ROOT / ".deps/pythondata-software-picolibc/pythondata_software_picolibc/data"
    includes = [
        kernel / "include", kernel / "portable/GCC/RISC-V",
        kernel / "portable/GCC/RISC-V/chip_specific_extensions/RISCV_no_extensions",
        picolibc / "libc/include", LITEX_SW / "libbase", LITEX_SW / "include", LITEX_SW,
        generated.parent, selected_libc, LITEX_CPU,
    ]
    flags = ["-Os", "-g", "-std=gnu11", "-march=rv32i2p0_m", "-mabi=ilp32", "-D__vexriscv__",
             "-ffunction-sections", "-fdata-sections", "-fno-common", "-fno-pic", "-fno-builtin",
             "-fno-stack-protector", "-no-pie", "-Wall", "-Wextra",
             "-I", str(ROOT / "firmware/rtos_system_fit/freertos"), *sum((["-I", str(path)] for path in includes), [])]
    write_flags(out_dir / "compiler-flags.txt", flags)
    out_dir.mkdir(parents=True, exist_ok=True)
    gen_link = out_dir / "generated"
    if not gen_link.exists():
        gen_link.symlink_to(generated, target_is_directory=True)
    gcc = str(PREFIX) + "gcc"
    source = ROOT / "firmware/rtos_system_fit/freertos/main.c"
    compile_units = [
        (source, out_dir / "main.o", flags),
        (kernel / "tasks.c", out_dir / "tasks.o", flags),
        (kernel / "queue.c", out_dir / "queue.o", flags),
        (kernel / "list.c", out_dir / "list.o", flags),
        (kernel / "portable/GCC/RISC-V/port.c", out_dir / "port.o", flags),
        (kernel / "portable/GCC/RISC-V/portASM.S", out_dir / "portASM.o", flags),
        (LITEX_CPU / "crt0.S", out_dir / "crt0.o", flags),
    ]
    for index, (src, obj, cflags) in enumerate(compile_units, 1):
        run_command([gcc, *cflags, "-c", str(src), "-o", str(obj)], ROOT,
                    out_dir / f"compile-{index:02d}.log", records)
    software = attempt_dir / "soc/software"
    lib_dirs = [software / name for name in ("libbase", "libc", "libcompiler_rt", "liblitedram")]
    link = [gcc, "-march=rv32i2p0_m", "-mabi=ilp32", "-nostartfiles", "-Wl,--gc-sections",
            "-Wl,--build-id=none", "-Wl,-u,_start", "-T", str(LINKER),
            "-Wl,-Map=" + str(out_dir / "freertos.elf.map"), *["-L" + str(path) for path in lib_dirs],
            "-Wl,--start-group", *[str(obj) for _, obj, _ in compile_units],
            "-lbase", "-lc", "-lcompiler_rt", "-llitedram", "-Wl,--end-group",
            "-o", str(out_dir / "freertos.elf")]
    run_command(link, out_dir, out_dir / "link.log", records)
    memory = {"base": 0x40000000, "bytes": 128 * 1024 * 1024}
    details = section_audit(out_dir / "freertos.elf", out_dir, records, out_dir / "freertos", memory)
    run_command([str(PREFIX) + "objcopy", "-O", "binary", "freertos.elf", "freertos.bin"],
                out_dir, out_dir / "objcopy.log", records)
    task_budget = {"counter": 768 * 4, "status": 768 * 4, "idle": 128 * 4, "isr": 512 * 4}
    details.update({
        "kernel_revision": "3a22924e0a9ddbbc8b0758881c33b3422a5cc20d",
        "kernel_tag": "V11.3.1",
        "port": "GCC RISC-V RV32IM, no extensions, no MTIME/CLINT; LiteX timer0 external IRQ 1 -> MEIE trap -> xTaskIncrementTick/vTaskSwitchContext; ecall yield from upstream port",
        "tick_hz": 1000,
        "stack_overflow_check": 2,
        "static_stack_budget_bytes": task_budget,
        "static_stack_total_bytes": sum(task_budget.values()),
        "runtime_invocation": "none",
        "artifacts": {name: artifact(path) for name, path in {
            "elf": out_dir / "freertos.elf", "map": out_dir / "freertos.elf.map",
            "bin": out_dir / "freertos.bin", "flags": out_dir / "compiler-flags.txt",
        }.items()},
        "source_files": {relative(src): sha256(src) for src, _, _ in compile_units},
    })
    return details


def run_software_tracks(report_path: Path) -> dict:
    report = json.loads(report_path.read_text())
    if not report.get("selected_candidate"):
        return {
            "baremetal": {"status": "blocked_no_routed_candidate", "summary": "No complete hardware candidate passed; a deployable CSR map is unavailable."},
            "zephyr_kernel": {"status": "blocked_no_routed_candidate", "summary": "No complete hardware candidate passed; a deployable CSR map is unavailable."},
            "zephyr_peripherals": {"status": "blocked_no_routed_candidate", "summary": "No complete hardware candidate passed; a deployable CSR map is unavailable."},
            "freertos": {"status": "blocked_no_routed_candidate", "summary": "No complete hardware candidate passed; a deployable CSR map is unavailable."},
        }
    selected = report["selected_candidate"]
    attempt = next((item for item in report["attempts"] if item.get("id") == selected and item.get("status") == "passed"), None)
    if attempt is None:
        raise TrackError(f"selected attempt {selected} has no passed manifest", status="blocked_hardware_artifact")
    attempt_dir = Path(attempt["attempt_dir"])
    software_root = Path(report_path).resolve().parent / "software"
    software_root.mkdir(parents=True, exist_ok=True)
    out = software_root / utc_stamp()
    out.mkdir(parents=True)
    dependency = pinned_revisions()
    write_json(out / "dependencies.json", dependency)
    records: list = []
    outcomes = {}

    def attempt_track(name, function):
        track_dir = out / name
        track_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = function(attempt_dir, track_dir, records)
            outcomes[name] = {"status": "passed", "summary": result.pop("summary", f"{name} cross-compiled for the selected routed image."), **result}
        except TrackError as error:
            outcomes[name] = {"status": error.status, "summary": str(error), "output_dir": relative(track_dir)}
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            outcomes[name] = {"status": "compile_failed", "summary": f"{type(error).__name__}: {error}",
                              "output_dir": relative(track_dir)}

    attempt_track("baremetal", run_baremetal)
    zephyr_result: dict = {}
    try:
        zephyr_result = run_zephyr(attempt_dir, out / "zephyr", records)
    except TrackError as error:
        zephyr_result = {"status": "blocked", "summary": str(error), "profiles": {
            "kernel": {"status": error.status, "summary": str(error)},
            "peripherals": {"status": error.status, "summary": str(error)},
        }}
    outcomes["zephyr_kernel"] = dict(zephyr_result.get("profiles", {}).get("kernel", {}))
    outcomes["zephyr_kernel"].setdefault("status", zephyr_result.get("status", "blocked"))
    outcomes["zephyr_kernel"].setdefault("summary", zephyr_result.get("summary", "Zephyr kernel build blocked."))
    outcomes["zephyr_peripherals"] = dict(zephyr_result.get("profiles", {}).get("peripherals", {}))
    outcomes["zephyr_peripherals"].setdefault("status", zephyr_result.get("status", "blocked"))
    outcomes["zephyr_peripherals"].setdefault("summary", zephyr_result.get("summary", "Zephyr peripheral build blocked."))
    if zephyr_result.get("design_audit"):
        outcomes["zephyr_kernel"]["design_audit"] = zephyr_result["design_audit"]
        outcomes["zephyr_peripherals"]["design_audit"] = zephyr_result["design_audit"]
    attempt_track("freertos", run_freertos)
    write_json(out / "commands.json", records)
    write_json(out / "tracks.json", outcomes)

    published = ROOT / "docs/rtos-system-fit" / report["run_id"] / "evidence/software" / out.name
    published.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(out, published, dirs_exist_ok=True)
    for name, outcome in outcomes.items():
        outcome.setdefault("output_dir", relative(out / name))
        outcome.setdefault("evidence_dir", relative(published / name))
    outcomes["software_run"] = {"path": relative(out), "evidence_path": relative(published),
                                "commands": relative(out / "commands.json"),
                                "dependencies": relative(out / "dependencies.json")}
    return outcomes


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", help="run's report.json (defaults to aggregate)")
    args = parser.parse_args(argv)
    report_path = Path(args.report) if args.report else ROOT / "build/rtos-system-fit/aggregate.json"
    try:
        if not report_path.is_file():
            raise TrackError(f"fit report is missing: {relative(report_path)}", status="blocked_hardware_artifact")
        outcomes = run_software_tracks(report_path)
        report = json.loads(report_path.read_text())
        report["software_tracks"] = outcomes
        write_json(report_path, report)
        run_id = report["run_id"]
        docs = ROOT / "docs/rtos-system-fit" / run_id
        docs.mkdir(parents=True, exist_ok=True)
        write_json(docs / "report.json", report)
        from scripts.rtos_system_fit import render_report
        (docs / "report.md").write_text(render_report(report))
        if report_path.name == "aggregate.json":
            write_json(ROOT / "build/rtos-system-fit/aggregate.json", report)
        print(json.dumps(outcomes, indent=2, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"RTOS software tracks failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
