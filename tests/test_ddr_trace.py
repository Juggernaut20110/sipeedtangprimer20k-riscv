import unittest
import copy
import json
from pathlib import Path
import tempfile

from migen import Module, Signal
from migen.sim import run_simulation
from litedram.common import TappedDelayLine
from litedram.phy.dfi import Interface
from gateware.ddr_trace import DDRBurstTrace, burst_address, decode_frame
from scripts.ddr_write_trace import csr_integer, prng_burst, validate_manifest
from scripts.ddr_trace_audit import audit


class TraceTests(unittest.TestCase):
    def test_geometry_capture_audit_rejects_false_result_and_source_identity(self):
        root = Path(__file__).resolve().parents[1]
        fixture = root / 'docs/ddr3/diagnosis/20261002T030658.410143Z-dll-off-write-trace'
        self.assertEqual(audit(fixture)['audit'], 'passed')
        with tempfile.TemporaryDirectory(dir=root / 'build') as temporary:
            directory = Path(temporary)
            for source in fixture.iterdir():
                if source.name not in ('trace-probe.json', 'manifest.json'):
                    (directory / source.name).symlink_to(source)
            original = json.loads((fixture / 'trace-probe.json').read_text())
            manifest = json.loads((fixture / 'manifest.json').read_text())
            (directory / 'manifest.json').write_text(json.dumps(manifest))
            forged = copy.deepcopy(original)
            forged['bounded_geometry_probe_passed'] = False
            (directory / 'trace-probe.json').write_text(json.dumps(forged))
            with self.assertRaisesRegex(ValueError, 'false geometry result'):
                audit(directory)
            (directory / 'trace-probe.json').write_text(json.dumps(original))
            manifest['geometry_source_sha256']['gateware/ddr3.py'] = '0' * 64
            (directory / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                audit(directory)

    def test_retained_capture_audit_rejects_false_physical_and_serializer_results(self):
        root = Path(__file__).resolve().parents[1]
        fixture = root/'docs/ddr3/diagnosis/20261002T013805.615625Z-dll-off-write-trace'
        self.assertEqual(audit(fixture)['audit'], 'passed')
        with tempfile.TemporaryDirectory(dir=root/'build') as temporary:
            directory = Path(temporary)
            for source in fixture.iterdir():
                if source.name != 'trace-probe.json':
                    (directory/source.name).symlink_to(source)
            original = json.loads((fixture/'trace-probe.json').read_text())
            for change in ('physical', 'serializer', 'source_identity'):
                changed = copy.deepcopy(original)
                if change == 'physical':
                    changed['traces'][0]['physical_words'][1] = '0xb4de6cd9'
                elif change == 'serializer':
                    changed['traces'][0]['frames'][3]['serializer_data'] = '0x0'
                else:
                    changed['probe_source_sha256'] = '0'*64
                (directory/'trace-probe.json').write_text(json.dumps(changed))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    audit(directory)

    def test_bios_pattern_and_csr_word_order_are_independently_decoded(self):
        self.assertEqual(prng_burst(0xb64f4),
                         [0x69fcd9b5, 0xb4de6cd9, 0xda4f366f, 0xed079b34])
        text = '0xf0001000  78 56 34 12 f0 de bc 9a  |....\n'
        self.assertEqual(csr_integer(text, 0xf0001000, 2), 0x123456789abcdef0)
        with self.assertRaises(ValueError):
            csr_integer(text, 0xf0001004, 2)

    def test_programming_gate_rejects_each_timing_failure_and_wrong_clock(self):
        timing = {f'worst_{kind}_slack_ns': 0.1
                  for kind in ('setup', 'hold', 'recovery', 'removal')}
        timing.update(setup_violated_endpoints=0, generated_clocks={
            name: {'frequency_mhz': frequency} for name, frequency in
            [('sys_clk', 48), ('sys2x_clk', 96), ('ddr_ck_96mhz', 96)]})
        manifest = {'diagnostic_only': True, 'write_trace': {'frames': 8},
                    'csr': {'memories': {name: {'size': 128 * 1024 * 1024}
                                        for name in ('main_ram', 'ddr_uncached')}},
                    'metrics': {'timing': timing}}
        validate_manifest(manifest)
        for kind in ('setup', 'hold', 'recovery', 'removal'):
            bad = copy.deepcopy(manifest)
            bad['metrics']['timing'][f'worst_{kind}_slack_ns'] = -0.01
            with self.assertRaises(ValueError):
                validate_manifest(bad)
        bad = copy.deepcopy(manifest)
        bad['metrics']['timing']['generated_clocks']['sys_clk']['frequency_mhz'] = 50
        with self.assertRaises(ValueError):
            validate_manifest(bad)
        bad = copy.deepcopy(manifest)
        bad['csr']['memories']['main_ram']['size'] = 256 * 1024 * 1024
        with self.assertRaisesRegex(ValueError, 'geometry'):
            validate_manifest(bad)

    def test_failed_address_maps_to_the_correct_burst_and_word(self):
        self.assertEqual(burst_address(0xb64f4),
                         {'row': 45, 'bank': 4, 'column': 632, 'word_lane': 1})
        self.assertEqual(burst_address(0x64011ac)['word_lane'], 3)
        self.assertEqual(burst_address(128 * 1024 * 1024 - 4)['row'], 8191)
        for offset in (-4, 1, 128 * 1024 * 1024, 256 * 1024 * 1024):
            with self.assertRaises(ValueError):
                burst_address(offset)

    def test_command_filter_capture_freeze_rearm_and_read_latency(self):
        dut = Module()
        dfi = Interface(14, 3, 1, 64, 2)
        data, mask, dq_tx, dqs_tx = Signal(64), Signal(8), Signal(2), Signal(2)
        dut.submodules.trace = trace = DDRBurstTrace(dfi, data, mask, dq_tx, dqs_tx, 3)
        dut.submodules.latency = latency = TappedDelayLine(dfi.phases[0].rddata_en, 3)
        dut.comb += dfi.phases[0].rddata_valid.eq(latency.output)
        p = dfi.phases[0]

        def command(ras, cas, we, address, bank=4):
            yield p.cs_n.eq(0)
            yield p.ras_n.eq(ras)
            yield p.cas_n.eq(cas)
            yield p.we_n.eq(we)
            yield p.address.eq(address)
            yield p.bank.eq(bank)
            yield
            yield p.cs_n.eq(1)
            yield p.rddata_en.eq(0)
            yield p.wrdata_en.eq(0)
            yield

        def bench():
            for phase in dfi.phases:
                yield phase.cs_n.eq(1)
            yield
            yield from command(0, 1, 1, 44)  # wrong row
            yield from command(1, 0, 0, 632)
            self.assertEqual((yield trace.status.status) & 7, 0)
            yield from command(0, 1, 1, 45)
            yield from command(1, 0, 0, 640)  # wrong column
            self.assertEqual((yield trace.status.status) & 7, 0)
            yield p.wrdata.eq(0xb4de6cd900000123)
            yield dfi.phases[1].wrdata.eq(0xabcdef)
            yield data.eq(0x89abcdef01234567)
            yield mask.eq(0x21)
            yield dq_tx.eq(0)
            yield dqs_tx.eq(2)
            yield p.wrdata_en.eq(1)
            yield from command(1, 0, 0, 632)
            for _ in range(10):
                yield
            self.assertEqual((yield trace.status.status) & 7, 1)
            yield trace.index.storage.eq(0)
            yield
            captured = (yield trace.frame.status)
            fields = decode_frame(captured)
            self.assertEqual(fields['write_data'], 0xabcdefb4de6cd900000123)
            self.assertEqual(fields['serializer_data'], 0x89abcdef01234567)
            self.assertEqual(fields['serializer_mask'], 0x21)
            yield p.wrdata.eq(0)
            yield from command(1, 0, 0, 632)
            self.assertEqual((yield trace.frame.status), captured)

            # The recorder's three samples straddle the actual PHY valid pulse.
            yield p.rddata_en.eq(1)
            yield p.cs_n.eq(0)
            yield p.ras_n.eq(1)
            yield p.cas_n.eq(0)
            yield p.we_n.eq(1)
            yield p.address.eq(632)
            yield
            yield p.cs_n.eq(1)
            yield p.rddata_en.eq(0)
            for value in range(100, 110):
                yield p.rddata.eq(value)
                yield
            self.assertEqual((yield trace.status.status) & 3, 3)
            self.assertEqual((yield trace.read_valid.status), 2)
            self.assertEqual((yield trace.read_nominal.status), (yield trace.read_early.status) + 1)
            self.assertEqual((yield trace.read_late.status), (yield trace.read_nominal.status) + 1)
            yield trace.arm.wr_stb.eq(1)
            yield
            yield trace.arm.wr_stb.eq(0)
            yield
            self.assertEqual((yield trace.status.status) & 7, 0)
            # A refresh invalidates row tracking; a write cannot match an old row.
            yield from command(0, 0, 1, 0)
            yield from command(1, 0, 0, 632)
            self.assertEqual((yield trace.status.status) & 7, 0)

        run_simulation(dut, bench(), clocks={'sys': 10})

    def test_second_command_phase_and_precharge_all_are_observed(self):
        dut = Module()
        dfi = Interface(14, 3, 1, 64, 2)
        dut.submodules.trace = trace = DDRBurstTrace(
            dfi, Signal(64), Signal(8), Signal(2), Signal(2), 3)
        p = dfi.phases[1]

        def issue(ras, cas, we, address):
            yield p.cs_n.eq(0)
            yield p.ras_n.eq(ras)
            yield p.cas_n.eq(cas)
            yield p.we_n.eq(we)
            yield p.address.eq(address)
            yield p.bank.eq(4)
            yield
            yield p.cs_n.eq(1)
            yield

        def bench():
            for phase in dfi.phases:
                yield phase.cs_n.eq(1)
            yield
            yield from issue(0, 1, 1, 45)
            yield from issue(0, 1, 0, 1 << 10)  # PRECHARGE ALL
            yield from issue(1, 0, 0, 632)
            self.assertEqual((yield trace.status.status) & 7, 0)
            yield from issue(0, 1, 1, 45)
            yield from issue(1, 0, 0, 632)
            for _ in range(10):
                yield
            self.assertEqual((yield trace.status.status) & 7, 1)

        run_simulation(dut, bench(), clocks={'sys': 10})


if __name__ == '__main__':
    unittest.main()
