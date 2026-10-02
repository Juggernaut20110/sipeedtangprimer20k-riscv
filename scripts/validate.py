#!/usr/bin/env python3
"""Run host firmware checks and generate every profile without vendor tools."""

import json
import os
import subprocess
import sys
from pathlib import Path
from migen.fhdl.specials import READ_FIRST  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build as build_module  # noqa: E402
from gateware.soc import (  # noqa: E402
    DDR_DIAGNOSTIC_BASE, DDR_DIAGNOSTIC_SIZE, DDR_L2_SIZE, DDR_SIZE_BYTES,
    DDR_UNCACHED_BASE, MEMORY_MODES, PROFILES,
)
from cpu_profiles import configuration_for  # noqa: E402
from memory import profile_build_dir  # noqa: E402


def check_generated_soc(profile, memory="onchip"):
    output = (ROOT / "build/validation" / profile if memory == "onchip"
              else ROOT / "build/validation" / memory / profile)
    cpu_rtl = None
    bios_size = None
    accepted_maxperf = None
    if profile == "performance":
        _, cpu_rtl, _, _ = build_module.selected_performance_cpu()
        bios_size = 24 * 1024 if memory == "onchip" else 32 * 1024
    elif profile == "maxperf":
        selection, selected, manifest, cpu_rtl = build_module.read_maxperf_selection(memory)
        selected_build_path = profile_build_dir(ROOT, "maxperf", memory) / "build-metadata.json"
        if not selected_build_path.is_file():
            raise RuntimeError(
                f"accepted maxperf {memory} build metadata is missing; build the selected public profile before validation"
            )
        accepted_maxperf = json.loads(selected_build_path.read_text())
        if (accepted_maxperf.get("status") != "passed"
                or accepted_maxperf.get("profile") != "maxperf"
                or accepted_maxperf.get("memory_mode") != memory
                or accepted_maxperf.get("cpu_candidate") != selected.get("candidate_id")
                or accepted_maxperf.get("cpu_profile_selection") != selection
                or accepted_maxperf.get("cpu_configuration", {}).get("rtl_sha256") != manifest.get("rtl_sha256")):
            raise RuntimeError(f"accepted maxperf {memory} build does not match its selected identity")
        bios_size = accepted_maxperf.get("bios_size")
        if not isinstance(bios_size, int) or bios_size <= 0:
            raise RuntimeError(f"accepted maxperf {memory} build does not record its BIOS reservation")
    soc, builder = build_module.generate_soc(
        profile,
        output,
        run_tools=False,
        compile_software=False,
        compile_gateware=False,
        memory=memory,
        bios_size=bios_size,
        cpu_rtl=cpu_rtl,
    )
    expected_cpu_variant = (
        accepted_maxperf.get("cpu_variant") if accepted_maxperf is not None else PROFILES[profile]
    )
    assert soc.cpu.variant == expected_cpu_variant
    assert soc.sys_clk_freq == 48_000_000
    expected_rom_size = bios_size or (
        24 * 1024 if profile in ("performance", "linux") and memory == "onchip" else 32 * 1024
    )
    assert soc.integrated_rom_size == expected_rom_size
    assert soc.integrated_sram_size == 8 * 1024
    if memory == "onchip":
        assert soc.integrated_main_ram_size == 32 * 1024
        assert not hasattr(soc, "ddrphy") and not hasattr(soc, "sdram")
        assert all(port.mode == READ_FIRST for port in soc.sram.mem.ports)
        assert all(port.mode == READ_FIRST for port in soc.main_ram.mem.ports)
    else:
        assert soc.integrated_main_ram_size == 0
        assert hasattr(soc, "ddrphy") and hasattr(soc, "sdram")
        geom = soc.sdram.controller.settings.geom
        actual_bytes = (1 << (geom.bankbits + geom.rowbits + geom.colbits)) * (soc.ddrphy.settings.databits // 8)
        assert (1 << geom.bankbits, 1 << geom.rowbits, 1 << geom.colbits) == (8, 16384, 1024)
        assert actual_bytes == DDR_SIZE_BYTES
        assert soc.ddrphy.settings.cl == 6 and soc.ddrphy.settings.cwl == 6
        assert soc.ddrphy.settings.dll_off is True
        assert soc.ddrphy.settings.read_leveling is True
        assert soc.ddrphy.settings.bitslips == 4 and soc.ddrphy.settings.delays == 256
        assert soc.ddrphy.settings.read_latency == 11
        assert soc.sdram.crossbar.read_latency == 12
        from migen import Instance
        dqs_instances = [item for item in soc.ddrphy._fragment.specials
                         if isinstance(item, Instance) and item.of == "DQS"]
        assert len(dqs_instances) == 2
        for instance in dqs_instances:
            inputs = {pin.name: pin.expr for pin in instance.items if isinstance(pin, Instance.Input)}
            assert inputs["RCLKSEL"].value == 3
            assert len(inputs["READ"].l) == 4
            assert inputs["READ"].l[0] is inputs["READ"].l[2]
            assert inputs["READ"].l[1] is inputs["READ"].l[3]
            assert inputs["READ"].l[0] is not inputs["READ"].l[1]
        assert soc.l2_cache is not None
        assert soc.bus.regions["main_ram"].origin == 0x40000000
        assert soc.bus.regions["main_ram"].size == DDR_SIZE_BYTES
        assert soc.bus.regions["ddr_diagnostic_ram"].origin == DDR_DIAGNOSTIC_BASE
        assert soc.bus.regions["ddr_diagnostic_ram"].size == DDR_DIAGNOSTIC_SIZE
        assert soc.bus.regions["ddr_uncached"].origin == DDR_UNCACHED_BASE
        assert soc.bus.regions["ddr_uncached"].size == DDR_SIZE_BYTES
        assert soc.bus.regions["ddr_uncached"].cached is False
        assert all(port.mode == READ_FIRST for port in soc.ddr_diagnostic_ram.mem.ports)
    assert len(soc.leds.out.storage) == 6 and soc.leds.out.storage.reset.value == 0
    assert len(soc.buttons._in.status) == 4
    assert any(
        type(special).__name__ == "MultiReg"
        for special in soc.buttons._fragment.specials
    )
    assert soc.project_led_resource_order == tuple(range(6))
    assert soc.project_led_logical_resource_order == tuple(reversed(range(6)))
    assert soc.project_button_resource_order == tuple(range(4))
    if profile == "linux":
        linux = configuration_for("linux")
        assert soc.cpu.variant == "linux"
        assert linux["isa"] == "rv32i2p0_ma" and linux["compressed"] is False
        assert linux["instruction_cache_bytes"] == linux["data_cache_bytes"] == 4096
        assert linux["mmu"] and linux["supervisor"] and linux["atomics"]
        flags = (output / "software/include/generated/variables.mak").read_text()
        assert "-march=rv32i2p0_ma" in flags and "-mabi=ilp32" in flags
    if accepted_maxperf is not None:
        assert soc.cpu.variant == accepted_maxperf.get("cpu_variant")
        assert accepted_maxperf["cpu_configuration"].get("rtl_sha256") == build_module.read_maxperf_selection(memory)[2].get("rtl_sha256")
        assert accepted_maxperf.get("bios_size") == expected_rom_size
        if memory == "onchip":
            assert accepted_maxperf.get("memory", {}).get("main_ram_bytes") == 32 * 1024
            assert soc.integrated_main_ram_size == accepted_maxperf["memory"]["main_ram_bytes"]
        else:
            selected_memory = accepted_maxperf.get("memory", {})
            assert selected_memory.get("ddr_physical_bytes") == DDR_SIZE_BYTES
            assert selected_memory.get("l2_cache_bytes") == DDR_L2_SIZE
            assert soc.bus.regions["main_ram"].size == selected_memory["ddr_physical_bytes"]
            assert soc.l2_cache is not None

    csr_header = (output / "software/include/generated/csr.h").read_text()
    assert "leds_out_write" in csr_header
    assert "buttons_in_read" in csr_header
    assert "timer0_uptime_latch_write" in csr_header
    mem_header = (output / "software/include/generated/mem.h").read_text()
    assert "MAIN_RAM_BASE" in mem_header and "MAIN_RAM_SIZE" in mem_header
    if memory == "onchip":
        assert "sdram" not in (output / "csr.json").read_text().lower()
    else:
        assert "ddrphy" in (output / "csr.json").read_text().lower()
        assert "sdram_dfii_control" in (output / "csr.json").read_text().lower()

    cst = (output / "gateware" / f"tang20k_{profile}.cst").read_text()
    sdc = (output / "gateware" / f"tang20k_{profile}.sdc").read_text()
    assert 'IO_LOC "btn_n4"' not in cst  # C7/S4 resets this Dock's FPGA
    expected_pins = {
        "led0": ("L16", "LVCMOS33"),
        "led1": ("L14", "LVCMOS33"),
        "led2": ("N14", "LVCMOS33"),
        "led3": ("N16", "LVCMOS33"),
        "led4": ("A13", "LVCMOS33"),
        "led5": ("C13", "LVCMOS33"),
        "btn_n0": ("T10", "LVCMOS33"),
        "btn_n1": ("T3", "LVCMOS15"),
        "btn_n2": ("T2", "LVCMOS15"),
        "btn_n3": ("D7", "LVCMOS15"),
    }
    for resource, (pin, standard) in expected_pins.items():
        assert f'IO_LOC "{resource}" {pin};' in cst
        assert f'IO_PORT "{resource}" IO_TYPE={standard};' in cst
    assert "create_generated_clock -name sys_clk" in sdc
    if memory == "onchip":
        assert "-divide_by 9 -multiply_by 16 [get_pins {rPLL/CLKOUT}]" in sdc
    else:
        assert "-divide_by 9 -multiply_by 32 [get_pins {rPLL/CLKOUT}]" in sdc
        assert "create_generated_clock -name sys2x_clk" in sdc
        assert "-divide_by 2 -multiply_by 1 [get_pins {CLKDIV/CLKOUT}]" in sdc
    assert "create_clock -name clk27 -period 37.037" in sdc

    return {
        "profile": profile,
        "memory_mode": memory,
        "generated": True,
        "clock_constraint": sdc.strip().splitlines(),
        "gpio_csr": ["leds_out_write", "buttons_in_read"],
        "ddr3_enabled": memory == "ddr3",
        "led_count": len(soc.leds.out.storage),
        "led_logical_resource_order": list(soc.project_led_logical_resource_order),
        "button_count": len(soc.buttons._in.status),
        "button_resource_order": list(soc.project_button_resource_order),
        "platform_device": soc.platform.device,
        "cpu_variant": soc.cpu.variant,
        "external_cpu_rtl": soc.project_cpu_rtl,
        "performance_cpu_candidate": (
            build_module.read_performance_selection()["candidate"] if profile == "performance" else None
        ),
        "checked_pin_constraints": expected_pins,
    }


def check_performance_cpu_selection():
    selection = build_module.read_performance_selection()
    candidate, source, generated, _ = build_module.selected_performance_cpu()
    assert candidate == selection["candidate"] == "dynamic_target"
    assert generated["rtl_sha256"] == selection["rtl_sha256"]
    assert PROFILES["performance"] == "standard"
    assert source.is_file()
    return {
        "verified": True,
        "profile_basis": "standard",
        "public_profile_registered": True,
        "candidate": candidate,
        "external_rtl": str(source.relative_to(ROOT)),
        "rtl_sha256": generated["rtl_sha256"],
        "system_clock_hz": selection["clock_hz"],
        "bios_rom_bytes_onchip": selection["memory_budgets"]["onchip_bios_rom_bytes"],
        "main_ram_bytes": selection["memory_budgets"]["main_ram_bytes"],
        "sram_bytes": selection["memory_budgets"]["sram_bytes"],
        "ddr3_l2_bytes": selection["memory_budgets"]["ddr3_l2_bytes"],
        "measured_mean": selection["evaluation"]["winner"]["coremark_mean"],
        "fresh_standard_mean": selection["evaluation"]["winner"]["standard_mean"],
    }


def main():
    build_dir = ROOT / "build/validation"
    build_dir.mkdir(parents=True, exist_ok=True)
    host_binary = build_dir / "test-app-logic"
    host_env = os.environ.copy()
    # The xPack directory contains a generic `ld` alongside its target tools.
    # Keep it out of PATH while invoking the host compiler.
    host_env["PATH"] = "/usr/bin:/bin"
    subprocess.run([
        "/usr/bin/gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
        str(ROOT / "firmware/app_logic.c"), str(ROOT / "tests/test_app_logic.c"),
        "-o", str(host_binary),
    ], check=True, env=host_env)
    subprocess.run([str(host_binary)], check=True)
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], cwd=ROOT, check=True)

    generated = []
    for memory in MEMORY_MODES:
        for profile in PROFILES:
            print(f"Generating and checking {memory}/{profile} SoC")
            generated.append(check_generated_soc(profile, memory))
    performance_cpu_selection = check_performance_cpu_selection()

    result = {
        "status": "passed",
        "host_firmware_logic": "passed",
        "gpio_simulation": "passed",
        "profiles": generated,
        "performance_cpu_profile": performance_cpu_selection,
        "linux_cpu_profile": configuration_for("linux"),
        "vendor_synthesis": "not run by make test; use make build for each profile",
        "hardware": "pending until Dock is connected",
    }
    (build_dir / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError, subprocess.CalledProcessError, RuntimeError, ValueError) as error:
        print(f"validation failed: {error}", file=sys.stderr)
        sys.exit(1)
