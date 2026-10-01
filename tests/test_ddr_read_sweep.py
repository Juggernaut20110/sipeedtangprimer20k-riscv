import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import ddr_read_sweep as sweep


class ReceiveSweepTests(unittest.TestCase):
    def test_default_replicates_original_read_gate(self):
        self.assertEqual(sweep.slot_pattern(2, 2), sweep.DEFAULT_PATTERN)
        self.assertEqual(next(sweep.candidates())['read_slots'], [0, 0, 15, 15, 0, 0])

    def test_half_cycle_patterns_move_only_one_x2_slot(self):
        first = sweep.slot_pattern(1, 2)
        self.assertEqual([(first >> (4*i)) & 15 for i in range(6)], [0, 5, 15, 10, 0, 0])
        second = sweep.slot_pattern(2, 1)
        self.assertEqual([(second >> (4*i)) & 15 for i in range(6)], [0, 10, 15, 5, 0, 0])
        with self.assertRaises(ValueError):
            sweep.slot_pattern(5, 5)

    def test_search_records_parameters_and_limits(self):
        rows = list(sweep.candidates((0, -1), 110))
        self.assertEqual(len(rows), 110)
        self.assertEqual({row['rclksel'] for row in rows[:8]}, set(range(8)))
        self.assertEqual(rows[104]['read_latency'], 11)
        self.assertTrue(rows[104]['diagnostic_dfii_only'])
        with self.assertRaises(ValueError):
            list(sweep.candidates((4,)))

    def test_training_requires_both_lane_identities_and_result(self):
        lanes = [f'SDRAM_READ_LEVELING_LANE module={lane} dq=0 bitslip=2 status=passed window_start=100 window_length=50 delay_center=125 delay_half_window=25' for lane in (0, 1)]
        text = '\n'.join(lanes + ['SDRAM_DIAGNOSTIC_CAL_RESULT status=passed'])
        self.assertEqual(sweep.parse_calibration(text)['training'], 'passed')
        for bad in (text.replace('module=1', 'module=0'), text.replace('status=passed', 'status=failed'),
                    '\n'.join(lanes[:1]), text + '\n' + lanes[0]):
            self.assertEqual(sweep.parse_calibration(bad)['training'], 'failed')
        failed = sweep.parse_calibration('best: m0, b00 SDRAM_READ_LEVELING_LANE module=0 dq=0 bitslip=0 status=failed window_start=-1 window_length=0')
        self.assertEqual(failed['lanes'][0]['delay_center'], None)

    def test_csr_dump_is_little_endian_and_address_checked(self):
        self.assertEqual(sweep.csr_value('0xf0001000  7c 03 00 00    |...\n', 0xf0001000), 0x37c)
        with self.assertRaises(RuntimeError):
            sweep.csr_value('0xf0001000  7c 03 00 00\n', 0xf0001004)

    def test_diagnostic_patch_applies_after_status_patch_from_clean_pin(self):
        dependency = ROOT / '.deps/litex'
        names = ['litex/soc/software/liblitedram/sdram.c', 'litex/soc/software/liblitedram/sdram.h',
                 'litex/soc/software/bios/cmds/cmd_litedram.c',
                 'litex/soc/software/bios/cmds/cmd_mem.c']
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in names:
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(subprocess.check_output(['git', '-C', str(dependency), 'show', 'HEAD:'+name]))
            for name in ('litex-sdram-training-status.patch', 'litex-sdram-read-capture-diagnostic.patch',
                         'litex-memtest-read-only-diagnostic.patch'):
                subprocess.run(['git', 'apply', str(ROOT/'patches'/name)], cwd=base, check=True)
            generated = (base/names[0]).read_text()
            self.assertIn('#ifdef SDRAM_READ_CAPTURE_DIAGNOSTIC', generated)
            self.assertIn('return all_lanes_ok;', generated)
            self.assertIn('SDRAM_DIAGNOSTIC_CAL_RESULT', (base/names[2]).read_text())
            self.assertIn('.read_only = 1', (base/names[3]).read_text())
            self.assertIn('#ifdef MEMTEST_READ_ONLY_DIAGNOSTIC', (base/names[3]).read_text())

    def test_diagnostic_uart_records_are_flushed_without_interrupts(self):
        import re
        source = (ROOT/'firmware/ddrtest/ddr_test.c').read_text()
        statements = list(re.finditer(r'    printf\([^;]*\);', source))
        self.assertGreater(len(statements), 10)
        for statement in statements:
            self.assertTrue(source[statement.end():].lstrip().startswith('uart_sync();'),
                            statement.group())
        failure = source[source.index('static void fail('):source.index('static uint32_t address_pattern')]
        self.assertLess(failure.rindex('uart_sync();'), failure.index('asm volatile("wfi")'))

    def test_integrity_probe_summary_keeps_bad_counts(self):
        from scripts.ddr_integrity_probe import summarize
        rows = summarize([{'write_delay_offset': 0},
                          {'command': 'mem_test', 'output': 'data errors: 1/524288'},
                          {'command': 'mem_verify', 'output': 'SDRAM_VERIFY errors=1 status=failed'}])
        self.assertEqual(rows[0]['memtests'], [1])
        self.assertEqual(rows[0]['verifications'], [1])

    def test_bios_memtest_failure_cannot_be_accepted_as_training_smoke(self):
        from scripts.ddr_test_run import validate_bios_memtest
        self.assertEqual(validate_bios_memtest(['Memtest OK'])['status'], 'passed')
        for records in ([], ['Memtest KO'], ['Memtest OK', 'Memtest KO'], ['Memtest OK']*2):
            self.assertEqual(validate_bios_memtest(records)['status'], 'failed')

    def test_diagnostic_boot_patch_and_bounded_region(self):
        dependency = ROOT / '.deps/litex'
        name = 'litex/soc/software/bios/boot.c'
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base/name
            path.parent.mkdir(parents=True)
            path.write_bytes(subprocess.check_output(['git', '-C', str(dependency), 'show', 'HEAD:'+name]))
            subprocess.run(['git', 'apply', str(ROOT/'patches/litex-ddr-diagnostic-boot.patch')], cwd=base, check=True)
            source = path.read_text()
            region = source[source.index('static int boot_region_max_size'):source.index('/* Compare physical addresses')]
            region = region[:region.rfind('#endif')]
            load = source[source.index('/* Compare physical addresses'):source.index('/* ROM Boot')]
            load = load[:load.rfind('/*-----------------------------------------------------------------------*/')]
            harness = """#include <stddef.h>
#include <stdio.h>
#include <assert.h>
#define MAIN_RAM_BASE 0x40000000ul
#define MAIN_RAM_SIZE 0x10000000ul
#define MAIN_RAM_BASE_VA MAIN_RAM_BASE
#define SRAM_BASE 0x10000000ul
#define SRAM_SIZE 8192ul
#define DDR_DIAGNOSTIC_RAM_BASE 0x20000000ul
#define DDR_DIAGNOSTIC_RAM_SIZE 16384ul
""" + region + load + """
int main(void) {
    size_t size = 0;
    assert(boot_load_max_size(0x20000000ul, &size) && size == 16384);
    assert(boot_load_max_size(0x20003ffful, &size) && size == 1);
    assert(!boot_load_max_size(0x1ffffffful, &size));
    assert(!boot_load_max_size(0x20004000ul, &size));
    assert(!boot_load_max_size(0x10000000ul, &size));
    assert(!boot_load_max_size(0ul, &size));
    assert(boot_load_max_size(0x40000000ul, &size) && size == 0x10000000ul);
    return 0;
}
"""
            cfile = base/'boot-region.c'
            cfile.write_text(harness)
            binary = base/'boot-region'
            subprocess.run(['/usr/bin/cc', '-std=c11', '-Wall', '-Werror', str(cfile), '-o', str(binary)],
                           env={**os.environ, 'PATH': '/usr/bin:/bin'}, check=True)
            subprocess.run([str(binary)], stdout=subprocess.DEVNULL, check=True)

    def test_phy_patches_reproduce_current_dependency_from_clean_pin(self):
        dependency = ROOT / '.deps/litedram'
        name = 'litedram/phy/gw2ddrphy.py'
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / name
            path.parent.mkdir(parents=True)
            path.write_bytes(subprocess.check_output(['git', '-C', str(dependency), 'show', 'HEAD:'+name]))
            for patch in ('litedram-gw2ddrphy-cdc.patch', 'litedram-gw2ddrphy-dll-off-read.patch'):
                subprocess.run(['git', 'apply', str(ROOT/'patches'/patch)], cwd=base, check=True)
            self.assertEqual(path.read_bytes(), (dependency/name).read_bytes())

    def test_fixed_receive_schedule_and_dll_on_compatibility(self):
        from gateware.soc import ProjectSoC
        from litex_boards.targets import sipeed_tang_primer_20k as target
        from litedram.common import TappedDelayLine
        from migen import Instance
        original_phy = target.GW2DDRPHY
        for dll_off in (True, False):
            class TestPHY(original_phy):
                def __init__(self, *args, **kwargs):
                    kwargs.update(dll_off=dll_off, cl=6, cwl=6)
                    super().__init__(*args, **kwargs)
            target.GW2DDRPHY = TestPHY
            try:
                soc = ProjectSoC(profile='minimal', memory='ddr3')
            finally:
                target.GW2DDRPHY = original_phy
            phy = soc.ddrphy
            latency = 11 if dll_off else 12
            self.assertEqual(phy.settings.read_latency, latency)
            self.assertEqual(soc.sdram.crossbar.read_latency, latency+1)
            self.assertEqual((phy.settings.rdphase, phy.settings.wrphase), (0, 0))
            self.assertEqual(phy.settings.write_latency, 2)
            delay = next(module for _, module in phy._submodules
                         if isinstance(module, TappedDelayLine) and len(module.taps) == latency)
            lane = next(item for item in phy._fragment.specials
                        if isinstance(item, Instance) and item.of == 'DQS')
            inputs = {pin.name: pin.expr for pin in lane.items if isinstance(pin, Instance.Input)}
            self.assertEqual(inputs['RCLKSEL'].value, 3 if dll_off else 0)
            bits = inputs['READ'].l if dll_off else [inputs['READ'].v]*4
            for bit, taps in zip(bits, ((2, 3), (1, 2), (2, 3), (1, 2)) if dll_off else ((2, 3),)*4):
                assignment = next(item for item in phy._fragment.comb if getattr(item, 'l', None) is bit)
                self.assertEqual(assignment.r.op, '|')
                for operand, index in zip(assignment.r.operands, taps):
                    self.assertIs(operand, delay.taps[index])
            for phase in phy.dfi.phases:
                assignment = next(item for item in phy._fragment.comb
                                  if getattr(item, 'l', None) is phase.rddata_valid)
                self.assertIs(assignment.r, delay.output)

    def test_generated_controls_preserve_clock_and_mode(self):
        from migen import Instance
        soc = sweep.make_sweep_soc()
        original_lanes = [item for item in soc.ddrphy._fragment.specials
                          if isinstance(item, Instance) and item.of == 'DQS']
        for lane in original_lanes:
            inputs = {pin.name: pin.expr for pin in lane.items if isinstance(pin, Instance.Input)}
            self.assertEqual(inputs['RCLKSEL'].value, 0)
            self.assertEqual(inputs['READ'].n, 4)
        self.assertEqual(soc.sdram.crossbar.read_latency, 13)
        configuration = sweep.add_read_sweep(soc)
        self.assertTrue(configuration['dll_off'])
        self.assertEqual((soc.ddrphy.settings.cl, soc.ddrphy.settings.cwl), (6, 6))
        self.assertEqual(soc.sys_clk_freq, 48_000_000)
        self.assertEqual(soc.ddrphy.settings.read_latency, 12)
        lanes = [item for item in soc.ddrphy._fragment.specials if isinstance(item, Instance) and item.of == 'DQS']
        self.assertEqual(len(lanes), 2)
        for lane in lanes:
            inputs = {pin.name: pin.expr for pin in lane.items if isinstance(pin, Instance.Input)}
            self.assertEqual(len(inputs['READ']), 4)
            self.assertEqual(len(inputs['RCLKSEL']), 3)
        exceptions = soc.platform.toolchain.additional_sdc_commands
        self.assertTrue(any('ddr_sweep_valid_meta*/D' in item for item in exceptions))
        self.assertFalse(any('READ' in item or 'RCLKSEL' in item for item in exceptions))


if __name__ == '__main__':
    unittest.main()
