#!/usr/bin/env python3
"""Run host firmware checks and generate every profile without vendor tools."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build as build_module  # noqa: E402
from gateware.soc import PROFILES  # noqa: E402


def check_generated_soc(profile):
    output = ROOT / "build/validation" / profile
    soc, builder = build_module.generate_soc(
        profile,
        output,
        run_tools=False,
        compile_software=False,
        compile_gateware=False,
    )
    assert soc.cpu.variant == profile
    assert soc.sys_clk_freq == 48_000_000
    assert soc.integrated_rom_size == 32 * 1024
    assert soc.integrated_main_ram_size == 32 * 1024
    assert soc.integrated_sram_size == 8 * 1024
    assert not hasattr(soc, "ddrphy") and not hasattr(soc, "sdram")
    assert len(soc.leds.out.storage) == 6 and soc.leds.out.storage.reset.value == 0
    assert len(soc.buttons._in.status) == 4
    assert any(
        type(special).__name__ == "MultiReg"
        for special in soc.buttons._fragment.specials
    )
    assert soc.project_led_resource_order == tuple(range(6))
    assert soc.project_led_logical_resource_order == tuple(reversed(range(6)))
    assert soc.project_button_resource_order == tuple(range(4))

    csr_header = (output / "software/include/generated/csr.h").read_text()
    assert "leds_out_write" in csr_header
    assert "buttons_in_read" in csr_header
    assert "timer0_uptime_latch_write" in csr_header
    mem_header = (output / "software/include/generated/mem.h").read_text()
    assert "MAIN_RAM_BASE" in mem_header and "MAIN_RAM_SIZE" in mem_header
    assert "sdram" not in (output / "csr.json").read_text().lower()

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
    assert "-divide_by 9 -multiply_by 16 [get_pins {rPLL/CLKOUT}]" in sdc
    assert "create_clock -name clk27 -period 37.037" in sdc

    return {
        "profile": profile,
        "generated": True,
        "clock_constraint": sdc.strip().splitlines(),
        "gpio_csr": ["leds_out_write", "buttons_in_read"],
        "ddr3_disabled": True,
        "led_count": len(soc.leds.out.storage),
        "led_logical_resource_order": list(soc.project_led_logical_resource_order),
        "button_count": len(soc.buttons._in.status),
        "button_resource_order": list(soc.project_button_resource_order),
        "platform_device": soc.platform.device,
        "checked_pin_constraints": expected_pins,
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
    for profile in PROFILES:
        print(f"Generating and checking {profile} SoC")
        generated.append(check_generated_soc(profile))

    result = {
        "status": "passed",
        "host_firmware_logic": "passed",
        "gpio_simulation": "passed",
        "profiles": generated,
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
