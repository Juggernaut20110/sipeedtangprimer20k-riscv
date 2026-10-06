import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from gateware.system_fit import SystemFitSoC
from scripts.system_fit import (
    CANDIDATES,
    _attempt_summary,
    _pinned_linux_cpu,
    _stack_margin_short,
    board_pin_assignments,
    validate_region_map,
    validate_soc,
)
from scripts.system_fit_reports import classify_outcome, parse_resources, parse_timing
from scripts.system_fit import _error_summary


ROOT = Path(__file__).resolve().parents[1]


class SystemFitTests(unittest.TestCase):
    def test_candidate_order_and_configurations_match_the_fit_plan(self):
        self.assertEqual(
            [candidate["id"] for candidate in CANDIDATES],
            [
                "candidate-1-pinned-linux",
                "candidate-2-compact-memory",
                "candidate-3-linux-2k-caches",
                "candidate-4-linux-1k-caches-no-l2",
            ],
        )
        self.assertEqual(
            [
                (
                    candidate["i_cache_bytes"], candidate["d_cache_bytes"],
                    candidate["l2_bytes"], candidate["working_sram_bytes"],
                    candidate["ethernet_rx_slots"], candidate["ethernet_tx_slots"],
                )
                for candidate in CANDIDATES
            ],
            [(4096, 4096, 8192, 8192, 2, 2), (4096, 4096, 2048, 4096, 1, 1),
             (2048, 2048, 2048, 4096, 1, 1), (1024, 1024, 0, 4096, 1, 1)],
        )

    def test_pinned_linux_cpu_identity_and_required_capabilities_are_audited(self):
        cpu = _pinned_linux_cpu(CANDIDATES[0])
        self.assertEqual(cpu["cache_sizes"], {"i_cache_bytes": 4096, "d_cache_bytes": 4096})
        self.assertTrue(all(cpu["features"].values()))
        self.assertEqual(len(cpu["rtl_sha256"]), 64)
        self.assertEqual(cpu["generator_revisions"]["generator_commit"],
                         json.loads((ROOT / "cpu-generator.lock.json").read_text())
                         ["generator_repository"]["commit"])

    def test_board_pins_and_compact_target_regions_irq_and_ethernet_are_collision_free(self):
        pins = board_pin_assignments(native_sd=True, ethernet="rmii")
        self.assertEqual(pins["native_sd_widths"], {"data": 4, "cmd": 1, "clk": 1, "cd": 1})
        self.assertEqual(pins["collisions"], [])

        candidate = CANDIDATES[1]
        cpu = _pinned_linux_cpu(candidate)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            soc = SystemFitSoC(
                cpu_rtl=cpu["rtl_path"],
                l2_size=candidate["l2_bytes"],
                working_sram_size=candidate["working_sram_bytes"],
                ethernet="rmii",
                ethernet_rx_slots=candidate["ethernet_rx_slots"],
                ethernet_tx_slots=candidate["ethernet_tx_slots"],
                native_sd=True,
            )
            validate_soc(soc, candidate, native_sd=True, ethernet="rmii")
            soc.finalize()
        self.assertEqual(soc.bus.regions["main_ram"].size, 128 * 1024 * 1024)
        self.assertTrue(soc.bus.regions["main_ram"].cached)
        self.assertEqual(soc.bus.regions["ethmac_rx"].size, 2048)
        self.assertEqual(soc.bus.regions["ethmac_tx"].size, 2048)
        self.assertFalse(soc.bus.regions["ethmac_rx"].cached)
        self.assertFalse(soc.bus.regions["ethmac_tx"].cached)
        self.assertTrue({"sdcard_block2mem", "sdcard_mem2block"}.issubset(soc.bus.masters))
        self.assertIn("sdcard", soc.irq.locs)
        self.assertTrue(all(hasattr(soc.sdcard.ev, name) for name in
                            ("card_detect", "block2mem_dma", "mem2block_dma")))
        self.assertIn("main_ram", soc.bus.slaves)
        self.assertNotIn("ddr_diagnostic_ram", soc.bus.regions)
        self.assertNotIn("ddr_uncached", soc.bus.regions)
        self.assertEqual(soc.irq.locs["ethmac"], 2)
        self.assertEqual(validate_region_map(soc)["irq_locations"], soc.irq.locs)
        self.assertIn(
            "set_false_path -to [get_pins {ddrphy/DQS/RESET "
            "ddrphy/DQS_1/RESET ddrphy/OSER4/RESET ddrphy/OSER4_*/RESET "
            "ddrphy/OSER4_MEM/RESET ddrphy/OSER4_MEM_*/RESET}] -setup",
            soc.platform.toolchain.additional_sdc_commands,
        )
        self.assertIn(
            'INS_LOC "ddrphy/gw2ddrphy_dqs_hold_0_s0" R50C12;',
            soc.platform.toolchain.additional_cst_commands,
        )

    def test_resource_parser_keeps_unreported_capacity_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pnr = root / "project.rpt.txt"
            pnr.write_text(
                "  Resource Usage Summary\n"
                "Logic                       | 100/1000\n"
                "Register                    | 200/500\n"
                "BSRAM                       | 3/10\n"
                "--SSRAM(RAM16)              | 12 | -\n"
                "Clock Resource Usage Summary\n"
                "  rPLL          | 1/4           | 25%\n"
                "  DLL           | 1/4           | 25%\n"
                "==========================================\n"
            )
            synthesis = root / "project_syn_resource.html"
            synthesis.write_text(
                "<table><tr><th>MODULE NAME</th><th>DSP NUMBER</th></tr>"
                "<tr><td>top</td><td>-</td></tr></table>"
            )
            result = parse_resources(pnr, synthesis)
        self.assertEqual(result["resources"]["logic"]["headroom"], 900)
        self.assertEqual(result["resources"]["ssram"]["used"], 12)
        self.assertIsNone(result["resources"]["ssram"]["capacity"])
        self.assertIsNone(result["resources"]["synthesis_dsp"]["used"])
        self.assertEqual(result["clock_resources"]["rPLL"]["headroom"], 3)

    def test_synthesis_resources_are_retained_when_routed_report_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pnr = root / "project.rpt.txt"
            synthesis = root / "project_syn_resource.html"
            synthesis.write_text("<table><tr><th>MODULE NAME</th></tr><tr><td>top</td></tr></table>")
            (root / "project_syn.rpt.html").write_text(
                "<table>"
                "<tr><td>Resource</td><td>Usage</td><td>Utilization</td></tr>"
                "<tr><td>Logic</td><td>13218(9593 LUT) / 20736</td><td>64%</td></tr>"
                "<tr><td>Register</td><td>5481 / 16173</td><td>34%</td></tr>"
                "<tr><td>BSRAM</td><td>46 / 46</td><td>100%</td></tr>"
                "<tr><td>SSRAM</td><td>413</td></tr>"
                "<tr><td>DSP</td><td></td></tr>"
                "<tr><td>MULT18X18</td><td>4</td></tr>"
                "<tr><td>ALU54D</td><td>2</td></tr>"
                "<tr><td>BSRAM</td><td>46</td></tr>"
                "<tr><td>rPLL</td><td>1</td></tr>"
                "</table>"
            )
            result = parse_resources(pnr, synthesis)
        self.assertIsNone(result["resources"]["logic"]["used"])
        self.assertEqual(result["synthesis_resources"]["logic"]["used"], 13218)
        self.assertEqual(result["synthesis_resources"]["logic"]["headroom"], 7518)
        self.assertEqual(result["synthesis_resources"]["bsram"]["headroom"], 0)
        self.assertEqual(result["synthesis_resources"]["ssram"]["used"], 413)
        self.assertEqual(result["synthesis_resources"]["dsp"]["used"], 6)
        self.assertEqual(result["synthesis_resources"]["pll"]["used"], 1)
        self.assertIsNone(result["synthesis_resources"]["pll"]["capacity"])

    def test_timing_gates_include_all_clocks_slacks_and_analyzed_paths(self):
        timing = """<Numbers of Paths Analyzed>: 10
<Numbers of Endpoints Analyzed>: 8
<Numbers of Setup Violated Endpoints>: 0
<Numbers of Hold Violated Endpoints>: 0
1 sys_clk Generated 20.833 48.000MHz
2 ddr_ck_96mhz Generated 10.417 96.000MHz
3 sys2x_clk Generated 10.417 96.000MHz
4 eth_rx Base 20.000 50.000MHz
1 sys_clk 48.000(MHz) 52.000(MHz)
1 ddr_ck_96mhz 96.000(MHz) 120.000(MHz)
1 sys2x_clk 96.000(MHz) 150.000(MHz)
1 eth_rx 50.000(MHz) 68.000(MHz)
3.1.1 Setup Paths Table
1 0.200 from to
3.1.2 Hold Paths Table
1 0.100 from to
3.1.3 Recovery Paths Table
1 0.300 from to
3.1.4 Removal Paths Table
1 0.400 from to
3.2 Minimum Pulse Width Table
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.tr"
            path.write_text(timing)
            result = parse_timing(path)
        self.assertEqual(result["status"], "passed", result["errors"])
        self.assertEqual(result["worst_slack_ns"],
                         {"setup": 0.2, "hold": 0.1, "recovery": 0.3, "removal": 0.4})

    def test_timing_parser_accepts_board_rmii_clock_and_enforces_its_fmax(self):
        timing = """<Numbers of Paths Analyzed>: 10
<Numbers of Endpoints Analyzed>: 8
<Numbers of Setup Violated Endpoints>: 0
<Numbers of Hold Violated Endpoints>: 0
1 sys_clk Generated 20.833 48.000MHz
2 ddr_ck_96mhz Generated 10.417 96.000MHz
3 sys2x_clk Generated 10.417 96.000MHz
4 eth_clocks_ref_clk Base 20.000 50.000MHz
1 sys_clk 48.000(MHz) 52.000(MHz)
1 ddr_ck_96mhz 96.000(MHz) 120.000(MHz)
1 sys2x_clk 96.000(MHz) 150.000(MHz)
1 eth_clocks_ref_clk 50.000(MHz) 68.609(MHz)
3.1.1 Setup Paths Table
1 0.200 from to
3.1.2 Hold Paths Table
1 0.100 from to
3.1.3 Recovery Paths Table
1 0.300 from to
3.1.4 Removal Paths Table
1 0.400 from to
3.2 Minimum Pulse Width Table
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.tr"
            path.write_text(timing)
            result = parse_timing(path)
        self.assertEqual(result["status"], "passed", result["errors"])
        self.assertIn("eth_clocks_ref_clk", result["clocks"])

    def test_sram_retry_requires_a_failed_stack_margin_gate(self):
        self.assertFalse(_stack_margin_short("stack margin: 3.67KiB"))
        self.assertTrue(_stack_margin_short("ERROR: SRAM stack margin is below required minimum (1.05KiB < 2.00KiB)"))

    def test_outcome_classification_separates_tool_resource_and_timing_failures(self):
        self.assertEqual(classify_outcome(timing_pass=True), "routed timing pass")
        self.assertEqual(classify_outcome(tool_failure=True), "tool failure")
        self.assertEqual(classify_outcome(resource_failure=True), "resource failure")
        self.assertEqual(classify_outcome(timing_failure=True), "timing failure")
        self.assertEqual(classify_outcome(integration_failure=True), "integration failure")
        self.assertEqual(
            _error_summary(RuntimeError("compile failed"), "region `rom` overflowed by 1936 bytes"),
            "BIOS ROM overflowed by 1,936 bytes",
        )
        self.assertEqual(
            _error_summary(RuntimeError("Can't find object named 'ddrphy/DQS'")),
            "Gowin rejected a generated floorplan instance path",
        )

    def test_routed_pass_summary_reports_all_four_slack_classes(self):
        summary = _attempt_summary({
            "classification": "routed timing pass",
            "timing": {"worst_slack_ns": {
                "setup": 0.947, "hold": 0.080, "recovery": 2.293, "removal": 0.974,
            }},
        })
        self.assertEqual(
            summary,
            "Routed timing pass; worst slack: setup +0.947 ns, hold +0.080 ns, "
            "recovery +2.293 ns, removal +0.974 ns",
        )


if __name__ == "__main__":
    unittest.main()
