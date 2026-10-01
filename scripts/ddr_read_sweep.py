#!/usr/bin/env python3
"""Controlled DLL-off receive sweep. BIOS stays in ROM/SRAM throughout."""
import datetime
import hashlib
import itertools
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLOTS = 6
DEFAULT_PATTERN = 0x00FF00  # READ=1111 at delay-line taps 2 and 3.
LATENCIES = tuple(range(9, 16))
ANSI = re.compile(rb'\x1b\[[0-?]*[ -/]*[@-~]')


def slot_pattern(first, second, width=2):
    """X2 uses READ0 then READ1; duplicate into unused READ2/3 for clarity."""
    if width < 1 or min(first, second) < 0 or max(first, second) + width > SLOTS:
        raise ValueError('READ slot window is outside the six-tap schedule')
    return sum(((5 if first <= tap < first + width else 0)
                | (10 if second <= tap < second + width else 0)) << (4*tap)
               for tap in range(SLOTS))


def candidates(latency_offsets=(0, -1, 1, -2, 2, -3, 3), limit=None):
    # Adjacent slot starts isolate one-CK changes; whole-cycle shifts provide controls.
    starts = [(2, 2), (1, 2), (2, 1), (1, 1), (2, 3), (3, 2), (3, 3),
              (0, 1), (1, 0), (0, 0), (3, 4), (4, 3), (4, 4)]
    number = 0
    for offset, (first, second), select in itertools.product(latency_offsets, starts, range(8)):
        if 12 + offset not in LATENCIES:
            raise ValueError('DFI snapshot latency must be 9..15 cycles')
        if limit is not None and number >= limit:
            return
        number += 1
        yield dict(read_pattern=f'0x{slot_pattern(first, second):06x}',
                   read_slots=[(slot_pattern(first, second) >> (4*i)) & 15 for i in range(SLOTS)],
                   rclksel=select, rdtap=min(first, second),
                   read_latency=12+offset, read_latency_offset=offset,
                   controller_read_latency=12, crossbar_read_latency=13, diagnostic_dfii_only=offset != 0)


def make_sweep_soc():
    """Recreate the original DLL-off receive path after installing the fix."""
    from gateware.soc import ProjectSoC
    from litex_boards.targets import sipeed_tang_primer_20k as target
    original = target.GW2DDRPHY

    class SweepPHY(original):
        def __init__(self, *args, **kwargs):
            # DLL-on CL6 constructs the unchanged original receive path. Switch
            # only the software mode-register flag back to DLL-off afterward.
            kwargs.update(dll_off=False, cl=6, cwl=6)
            super().__init__(*args, **kwargs)
            self.settings.dll_off = True

    target.GW2DDRPHY = SweepPHY
    try:
        return ProjectSoC(profile='minimal', memory='ddr3')
    finally:
        target.GW2DDRPHY = original


def add_read_sweep(soc):
    """Add live controls only to an isolated diagnostic SoC, retaining the PHY."""
    from functools import reduce
    from operator import or_
    from migen import Array, Cat, If, Instance, Replicate, Signal
    from litex.gen import LiteXModule
    from litex.soc.interconnect.csr import CSR, CSRStatus, CSRStorage
    from litedram.common import TappedDelayLine

    phy = soc.ddrphy
    if not phy.settings.dll_off or (phy.settings.cl, phy.settings.cwl) != (6, 6):
        raise ValueError('receive sweep requires DLL-off CL6/CWL6')
    if phy.settings.read_latency != 12 or soc.sdram.crossbar.read_latency != 13:
        raise ValueError('use make_sweep_soc() to retain matching sweep baseline/controller timing')
    read_delay = next(module for _, module in phy._submodules
                      if isinstance(module, TappedDelayLine) and len(module.taps) >= 11)
    lane_instances = sorted((item for item in phy._fragment.specials
                             if isinstance(item, Instance) and item.of == 'DQS'), key=lambda item: item.duid)
    if len(lane_instances) != 2:
        raise ValueError('expected two DQS byte lanes')

    class Controls(LiteXModule):
        def __init__(self):
            self.slots = CSRStorage(24, reset=DEFAULT_PATTERN, name='slots')
            self.rclksel = CSRStorage(6, name='rclksel')  # 3 bits per byte lane.
            self.latency = CSRStorage(3, reset=3, name='latency')  # indexes 9..15.
            self.clear = CSR(name='clear')
            self.valid = CSRStatus(2, name='valid')
            self.burst = CSRStatus(2, name='burst')
            self.pointers = CSRStatus(12, name='pointers')
            read_word = reduce(or_, [Replicate(read_delay.taps[tap], 4)
                                    & self.slots.storage[4*tap:4*tap+4] for tap in range(SLOTS)])
            for lane, instance in enumerate(lane_instances):
                for pin in instance.items:
                    if isinstance(pin, Instance.Input):
                        if pin.name == 'READ':
                            pin.expr = read_word
                        if pin.name == 'RCLKSEL':
                            pin.expr = self.rclksel.storage[3*lane:3*lane+3]
            # This is a DFII snapshot timing sweep. The crossbar remains fixed at
            # 12 cycles. Non-default offsets must never run controller memory tests.
            extended = TappedDelayLine(signal=read_delay.output, ntaps=15-len(read_delay.taps))
            self.submodules += extended
            selections = Array(list(read_delay.taps[8:]) + list(extended.taps))
            for phase in phy.dfi.phases:
                assignment = next(statement for statement in phy._fragment.comb
                                  if getattr(statement, 'l', None) is phase.rddata_valid)
                assignment.r = selections[self.latency.storage]

            def snapshot(value, name):
                meta = Signal(len(value), name_override='ddr_sweep_' + name + '_meta')
                synced = Signal(len(value), name_override='ddr_sweep_' + name + '_synced')
                meta.attr.add('no_retiming')
                synced.attr.add('no_retiming')
                self.sync += [meta.eq(value), synced.eq(meta)]
                return synced

            def outputs(name):
                return [next(pin.expr for pin in instance.items
                             if isinstance(pin, Instance.Output) and pin.name == name)
                        for instance in lane_instances]
            valid = snapshot(Cat(*outputs('RVALID')), 'valid')
            # The existing registered detector is cleared for each training probe.
            # Accumulate it across the whole candidate so the final probe cannot
            # erase earlier evidence. A zero still cannot prove absence of short pulses.
            self.sync += [
                If(self.clear.wr_stb, self.valid.status.eq(0), self.burst.status.eq(0))
                .Else(self.valid.status.eq(self.valid.status | valid),
                      self.burst.status.eq(self.burst.status | phy._burstdet_seen.status))]
            pointers = Cat(*(Cat(r, w) for r, w in zip(outputs('RPOINT'), outputs('WPOINT'))))
            self.comb += self.pointers.status.eq(snapshot(pointers, 'pointers'))

    soc.ddr_sweep = Controls()
    soc.add_constant('SDRAM_READ_CAPTURE_DIAGNOSTIC', 1)
    soc.add_constant('MEMTEST_DATA_DEBUG', 1)
    soc.add_constant('MEMTEST_DEBUG_MAX_ERRORS', 16)
    soc.platform.toolchain.additional_cst_commands.extend([
        'INS_LOC "gw2ddrphy_dqs_hold_0_s0" R50C12;',
        'INS_LOC "gw2ddrphy_dqs_hold_1_s0" R50C45;',
    ])
    soc.platform.toolchain.additional_sdc_commands.append(
        'set_false_path -to [get_pins {ddr_sweep_valid_meta*/D ddr_sweep_pointers_meta*/D}]')
    return dict(read_pattern=f'0x{DEFAULT_PATTERN:06x}', rclksel=[0, 0],
                read_latency=12, read_slots=6, latency_choices=list(LATENCIES),
                dll_off=True, cl=6, cwl=6, sys_clk_hz=48000000, ddr_ck_hz=96000000,
                firmware_all_lanes=True, controller_read_latency=12, crossbar_read_latency=13)


def parse_calibration(text):
    pattern = re.compile(r'SDRAM_READ_LEVELING_LANE module=(\d+) dq=(\d+) bitslip=(\d+) '
                         r'status=(\w+) window_start=(-?\d+) window_length=(\d+)'
                         r'(?: delay_center=(-?\d+) delay_half_window=(-?\d+))?')
    lanes = []
    for match in pattern.finditer(text):
        module, dq, bitslip, status, start, length, center, half = match.groups()
        lanes.append(dict(lane=int(module), dq=int(dq), bitslip=int(bitslip), status=status,
                          window_start=int(start), window_length=int(length),
                          delay_center=int(center) if center is not None else None,
                          delay_half_window=int(half) if half is not None else None))
    markers = re.findall(r'SDRAM_DIAGNOSTIC_CAL_RESULT status=(\w+)', text)
    ids = [(lane['lane'], lane['dq']) for lane in lanes]
    passed = (markers == ['passed'] and sorted(ids) == [(0, 0), (1, 0)]
              and all(lane['status'] == 'passed' and 0 <= lane['bitslip'] < 4
                      and lane['window_length'] >= 2 and 0 <= lane['window_start'] < 256
                      and lane['window_start'] + lane['window_length'] <= 256
                      and lane['delay_center'] is not None
                      and lane['window_start'] <= lane['delay_center'] < lane['window_start'] + lane['window_length']
                      for lane in lanes))
    return dict(training='passed' if passed else 'failed', lanes=lanes, result_markers=markers)


def csr_value(text, address):
    match = re.search(rf'^0x{address:08x}\s+((?:[0-9a-fA-F]{{2}}\s+){{4}})', text, re.M)
    if not match:
        raise RuntimeError('CSR memory dump missing or malformed')
    return int.from_bytes(bytes.fromhex(match[1]), 'little')


def run_sweep(device, limit=None, latency_offsets=(0, -1, 1, -2, 2, -3, 3)):
    import serial
    from scripts.ddr_diagnose import sha
    output = ROOT / 'build/ddr3/diagnosis/dll-off-read-sweep'
    manifest = json.loads((output / 'manifest.json').read_text())
    if sha(Path(manifest['bitstream'])) != manifest['bitstream_sha256']:
        raise RuntimeError('bitstream identity changed')
    if manifest.get('receive_sweep', {}).get('dll_off') is not True:
        raise RuntimeError('sweep manifest does not guarantee DLL-off')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    evidence = ROOT / 'docs/ddr3/diagnosis' / (stamp + '-dll-off-read-sweep')
    evidence.mkdir()
    for name, path in [('manifest.json', output/'manifest.json'),
                       ('sdram_phy.h', output/'software/include/generated/sdram_phy.h'),
                       ('timing.tr', output/'gateware/impl/pnr/project.tr'),
                       ('ddr_read_sweep.py', Path(__file__))]:
        (evidence/name).write_bytes(path.read_bytes())
    rows = []
    state = dict(console_confirmed=False, diagnostic_only=True, completed=False)
    registers = manifest['csr']['csr_registers']
    try:
        with serial.Serial(device, 115200, timeout=0.05, exclusive=True) as uart, \
                (evidence/'uart.bin').open('wb') as rx, (evidence/'uart.tx.bin').open('wb') as tx:
            uart.reset_input_buffer()

            def receive(timeout):
                chunk = bytearray()
                deadline = time.monotonic()+timeout
                while time.monotonic() < deadline:
                    data = uart.read(4096)
                    rx.write(data); rx.flush()
                    chunk.extend(data)
                    clean = ANSI.sub(b'', chunk).decode('utf8', errors='replace').replace('\r', '')
                    if clean.rstrip().endswith('litex>'):
                        state['console_confirmed'] = True
                        return clean
                state['console_confirmed'] = False
                raise RuntimeError('BIOS prompt not confirmed; sweep stopped without reset or next command')

            def command(text, timeout=5):
                if not state['console_confirmed']:
                    raise RuntimeError('command requires confirmed BIOS prompt')
                state['console_confirmed'] = False
                data=(text+'\n').encode()
                tx.write(data); tx.flush()
                uart.write(data); uart.flush()
                return receive(timeout)

            def write(name, value):
                command(f"mem_write 0x{registers[name]['addr']:08x} 0x{value:x}")

            def read(name):
                address = registers[name]['addr']
                return csr_value(command(f'mem_read 0x{address:08x} 4'), address)

            with (evidence/'programmer.log').open('wb') as log:
                programmer = subprocess.Popen(['openFPGALoader', '-b', 'tangprimer20k', manifest['bitstream']],
                                              stdout=log, stderr=subprocess.STDOUT)
                boot = receive(120)
                if programmer.wait(timeout=10) != 0 or 'BIOS CRC passed' not in boot:
                    raise RuntimeError('fresh SRAM programming / BIOS identity not confirmed')
            (evidence/'boot.log').write_text(boot)
            for index, candidate in enumerate(candidates(latency_offsets, limit), 1):
                write('ddr_sweep_slots', int(candidate['read_pattern'], 16))
                value = candidate['rclksel']
                write('ddr_sweep_rclksel', value | (value << 3))
                write('ddr_sweep_latency', candidate['read_latency']-9)
                # Reset PHY lane/FIFO state between every candidate without
                # reprogramming the device or touching DRAM DLL mode registers.
                write('ddrphy_dly_sel', 3)
                write('ddrphy_dly_sel', 0)
                write('ddr_sweep_clear', 1)
                text = command('sdram_cal', timeout=90)
                result = dict(candidate, **parse_calibration(text), candidate=index)
                result['rvalid_seen'] = [bool(read('ddr_sweep_valid') & (1 << lane)) for lane in range(2)]
                burst = read('ddr_sweep_burst')
                result['rburst_seen'] = [bool(burst & (1 << lane)) for lane in range(2)]
                result['pointers'] = read('ddr_sweep_pointers')
                result['bios_memtest'] = 'not run'
                (evidence/f'candidate-{index:04d}.log').write_text(text)
                rows.append(result)
                (evidence/'results.json').write_text(json.dumps(rows, indent=2)+'\n')
                print(json.dumps(result), flush=True)
                if result['training'] == 'passed':
                    state['first_passing_candidate'] = index
                    # A fixed image with matching crossbar timing is required next.
                    break
            state['completed'] = True
    finally:
        state['uart_sha256'] = sha(evidence/'uart.bin')
        state['transmitted_uart_sha256'] = sha(evidence/'uart.tx.bin')
        (evidence/'state.json').write_text(json.dumps(state, indent=2)+'\n')
        print(str(evidence), flush=True)
    return evidence
