import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateware.peripherals import spi_actual_hz, spi_divider_at_most  # noqa: E402
from gateware.soc import ProjectSoC  # noqa: E402
from scripts.peripheral_config import (  # noqa: E402
    feature_slug, peripheral_build_dir, validate_features,
)


class PeripheralConfigurationTests(unittest.TestCase):
    def test_clock_dividers_never_exceed_requested_limits(self):
        init_divider = spi_divider_at_most(48_000_000, 400_000)
        transfer_divider = spi_divider_at_most(48_000_000, 12_000_000)
        self.assertEqual(init_divider, 120)
        self.assertEqual(spi_actual_hz(48_000_000, init_divider), 400_000)
        self.assertEqual(transfer_divider, 4)
        self.assertEqual(spi_actual_hz(48_000_000, transfer_divider), 12_000_000)
        self.assertLessEqual(spi_actual_hz(48_000_000, 65535), 400_000)

    def test_feature_modes_and_artifact_paths_are_isolated(self):
        self.assertEqual(validate_features("spi", "rmii"), ("spi", "rmii"))
        self.assertEqual(feature_slug("spi", "rmii"), "sd-spi_eth-rmii")
        with self.assertRaises(ValueError):
            validate_features("native", "rmii")
        root = Path("/workspace")
        self.assertEqual(
            peripheral_build_dir(root, "ddr3", "standard", "spi", "rmii"),
            root / "build/peripherals/ddr3/standard/sd-spi_eth-rmii",
        )
        self.assertNotEqual(
            peripheral_build_dir(root, "ddr3", "standard", "spi", "none"),
            peripheral_build_dir(root, "ddr3", "standard", "none", "rmii"),
        )

    def test_optional_soc_adds_generated_sd_and_rmii_csrs(self):
        soc = ProjectSoC(profile="standard", memory="onchip", sdcard="spi", ethernet="rmii")
        self.assertEqual(soc.sdcard_mode, "spi")
        self.assertEqual(soc.ethernet_mode, "rmii")
        self.assertEqual(soc.ethmac.rx_slots.constant, 2)
        self.assertEqual(soc.ethmac.tx_slots.constant, 2)
        self.assertEqual(soc.ethmac.slot_size.constant, 2048)
        self.assertEqual(soc.bus.regions["ethmac"].size, 4 * 2048)
        self.assertFalse(soc.bus.regions["ethmac"].cached)
        self.assertEqual(soc.ethphy.rx_clk_freq, 50e6)
        self.assertEqual(soc.ethphy.tx_clk_freq, 50e6)
        self.assertEqual(soc.platform.lookup_request("eth_clocks").ref_clk, soc.ethphy.crg.cd_eth_rx.clk)

    def test_performance_sd_working_sram_remedy_preserves_disabled_budget(self):
        rtl=ROOT/'build/cpu-candidates/dynamic_target/VexRiscv.v'
        if not rtl.exists():self.skipTest('accepted performance RTL must be generated first')
        baseline=ProjectSoC(profile='performance',memory='ddr3',cpu_rtl=rtl)
        optional=ProjectSoC(profile='performance',memory='ddr3',cpu_rtl=rtl,sdcard='spi')
        self.assertEqual(baseline.bus.regions['sram'].size,8192)
        self.assertEqual(optional.bus.regions['sram'].size,4096)
        self.assertEqual(optional.bus.regions['ddr_diagnostic_ram'].size,16384)
        self.assertEqual(optional.bus.regions['main_ram'].size,134217728)
        self.assertEqual(baseline.cpu.external_variant,optional.cpu.external_variant)

    def test_ddr_hold_floorplan_is_limited_to_optional_peripheral_images(self):
        baseline = ProjectSoC(profile="standard", memory="ddr3")
        optional = ProjectSoC(profile="standard", memory="ddr3", sdcard="spi")
        ethernet_only = ProjectSoC(profile="standard", memory="ddr3", ethernet="rmii")
        baseline_commands = baseline.platform.toolchain.additional_cst_commands
        optional_commands = optional.platform.toolchain.additional_cst_commands
        self.assertFalse(any("gw2ddrphy_dqs_hold" in item for item in baseline_commands))
        self.assertTrue(any("gw2ddrphy_dqs_hold_0_s0" in item and "R50C12" in item
                            for item in optional_commands))
        self.assertTrue(any("gw2ddrphy_dqs_hold_1_s0" in item and "R50C45" in item
                            for item in optional_commands))
        self.assertTrue(any('multiregimpl11_s0" R27C16' in item
                            for item in optional_commands))
        ethernet_commands = ethernet_only.platform.toolchain.additional_cst_commands
        self.assertTrue(any('INS_LOC "gw2ddrphy_dqs_hold_0_s0" R50C12;' == item
                            for item in ethernet_commands))
        self.assertTrue(any('INS_LOC "gw2ddrphy_dqs_hold_1_s0" R50C45;' == item
                            for item in ethernet_commands))


if __name__ == "__main__":
    unittest.main()
