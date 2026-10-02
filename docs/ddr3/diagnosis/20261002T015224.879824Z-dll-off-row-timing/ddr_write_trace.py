#!/usr/bin/env python3
"""Record diagnostic DDR writes before their persistent readback failure."""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ANSI = re.compile(rb'\x1b\[[0-?]*[ -/]*[@-~]')


def dump_bytes(text, address, length):
    memory = {}
    for line in text.splitlines():
        match = re.match(r'^0x([0-9a-fA-F]{8})\s+((?:[0-9a-fA-F]{2}\s+){1,16})', line)
        if match:
            start = int(match[1], 16)
            memory.update((start + i, byte) for i, byte in enumerate(bytes.fromhex(match[2])))
    try:
        return bytes(memory[address + i] for i in range(length))
    except KeyError as error:
        raise ValueError('memory dump is incomplete or has the wrong address') from error


def csr_integer(text, address, words):
    raw = dump_bytes(text, address, words * 4)
    value = 0
    for i in range(words):
        value = (value << 32) | int.from_bytes(raw[4*i:4*i+4], 'little')
    return value


def prng_burst(offset):
    """The exact pinned BIOS 32-bit Galois sequence, seed 1."""
    first = (offset & ~15) // 4
    seed = 1
    words = []
    for index in range(first + 4):
        seed = (seed >> 1) ^ (0x80200003 if seed & 1 else 0)
        if index >= first:
            words.append(seed)
    return words


def validate_manifest(manifest):
    trace = manifest.get('write_trace', {})
    if manifest.get('diagnostic_only') is not True or trace.get('frames') != 8:
        raise ValueError('manifest must identify the diagnostic-only eight-frame recorder')
    timing = manifest['metrics']['timing']
    for key in ('setup', 'hold', 'recovery', 'removal'):
        if timing.get(f'worst_{key}_slack_ns', -1) < 0:
            raise ValueError(f'diagnostic route fails {key} timing')
    if timing.get('setup_violated_endpoints', -1) != 0:
        raise ValueError('diagnostic route has violated timing endpoints')
    for name, frequency in [('sys_clk', 48), ('sys2x_clk', 96), ('ddr_ck_96mhz', 96)]:
        if abs(timing['generated_clocks'][name]['frequency_mhz'] - frequency) > 0.01:
            raise ValueError(f'wrong diagnostic clock {name}')


def run_probe(device, suite='initial', experiment='dll-off-write-trace'):
    import serial
    from gateware.ddr_trace import burst_address, decode_frame
    from scripts.ddr_diagnose import capture, sha

    output = ROOT / 'build/ddr3/diagnosis' / experiment
    manifest = json.loads((output/'manifest.json').read_text())
    validate_manifest(manifest)
    if manifest['trace_source_sha256'] != sha(ROOT/'gateware/ddr_trace.py'):
        raise ValueError('recorder source changed since build')
    evidence = capture(experiment, device)
    for name, source in [('ddr_trace.py', ROOT/'gateware/ddr_trace.py'),
                         ('ddr_write_trace.py', Path(__file__)),
                         ('bitstream.fs', Path(manifest['bitstream'])),
                         ('bios.bin', output/'software/bios/bios.bin'),
                         ('gateware.v', output/'gateware/ddr_diagnosis.v')]:
        (evidence/name).write_bytes(source.read_bytes())
    result = {'diagnostic_only': True, 'acceptance_credit': False, 'suite': suite,
              'bitstream_sha256': manifest['bitstream_sha256'],
              'probe_source_sha256': sha(Path(__file__)),
              'records': [], 'traces': [], 'console_confirmed': False}
    registers = manifest['csr']['csr_registers']
    try:
        from scripts.ddr_test_run import validate_training
        training = validate_training((evidence/'uart.log').read_text().splitlines(), 2, {
            'SDRAM_PHY_BITSLIPS': 4, 'SDRAM_PHY_DELAYS': 256,
            'SDRAM_PHY_MODULES': 2, 'SDRAM_PHY_DQ_DQS_RATIO': 8})
        result['training'] = training
        if training['status'] != 'passed':
            raise RuntimeError('trace image did not train both lanes; controller probes are unusable')
        with serial.Serial(device, 115200, timeout=0.05, exclusive=True) as uart, \
                (evidence/'trace.uart.bin').open('wb') as rx, \
                (evidence/'trace.uart.tx.bin').open('wb') as tx:
            def command(text, timeout=10):
                result['console_confirmed'] = False
                data = (text+'\n').encode()
                tx.write(data); tx.flush()
                uart.write(data); uart.flush()
                received = bytearray()
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    data = uart.read(4096)
                    rx.write(data); rx.flush(); received.extend(data)
                    clean = ANSI.sub(b'', received).decode(errors='replace').replace('\r', '')
                    if clean.rstrip().endswith('litex>'):
                        result['console_confirmed'] = True
                        result['records'].append({'command': text, 'output': clean})
                        return clean
                raise TimeoutError('no BIOS prompt; stop without subsequent command/programming')

            def read(name):
                register = registers['ddr_trace_'+name]
                address, words = register['addr'], register['size']
                return csr_integer(command(f'mem_read 0x{address:08x} {words*4}'), address, words)

            def write(name, value):
                address = registers['ddr_trace_'+name]['addr']
                command(f'mem_write 0x{address:08x} 0x{value:x}')

            def arm(offset):
                target = burst_address(offset)
                for name in ('row', 'bank', 'column'):
                    write(name, target[name])
                write('arm', 1)

            def snapshot(label, offset, expected=None):
                target = burst_address(offset)
                address = 0xc0000000 + (offset & ~15)
                physical = dump_bytes(command(f'mem_read 0x{address:08x} 16'), address, 16)
                status = read('status')
                trace = {'label': label, 'offset': hex(offset), 'geometry': target, 'status': status,
                         'write_complete': bool(status & 1), 'read_complete': bool(status & 2),
                         'physical_words': [f'0x{int.from_bytes(physical[i:i+4], "little"):08x}'
                                            for i in range(0, 16, 4)], 'frames': []}
                if expected is not None:
                    trace['expected_words'] = [f'0x{word:08x}' for word in expected]
                if status & 1:
                    trace['hit_cycle'] = read('hit_cycle')
                    for slot in range(8):
                        write('index', slot)
                        value = read('frame')
                        fields = decode_frame(value)
                        trace['frames'].append({'slot': slot, 'raw': f'{value:064x}',
                            **{k: hex(v) for k, v in fields.items()}})
                if status & 2:
                    trace['read_valid'] = read('read_valid')
                    for name in ('read_early', 'read_nominal', 'read_late'):
                        value = read(name)
                        trace[name] = f'{value:032x}'
                        trace[name+'_words'] = [f'0x{(value >> (32*i)) & 0xffffffff:08x}' for i in range(4)]
                result['traces'].append(trace)
                (evidence/'trace-probe.json').write_text(json.dumps(result, indent=2)+'\n')
                print(json.dumps({k:v for k,v in trace.items() if k != 'frames'}), flush=True)
                return trace

            command('')
            snapshot('boot_cached_prng', 0xb64f4, prng_burst(0xb64f4))
            if suite == 'initial':
                arm(0xb64f4)
                text = command('mem_test 0xc0000000 0x200000', 120)
                print(text, flush=True)
                snapshot('uncached_prng_sweep', 0xb64f4, prng_burst(0xb64f4))
                arm(0xb64f4)
                command('mem_write 0xc00b64f4 0xb4de6cd9')
                snapshot('isolated_correct_rewrite', 0xb64f4)
                arm(0x169770)
                command('mem_write 0xc0000000 0xffffffff 524288', 120)
                snapshot('uncached_all_ones_2mib', 0x169770, [0xffffffff]*4)
                arm(0x169770)
                command('mem_write 0xc0169700 0xffffffff 64')
                snapshot('uncached_all_ones_256byte_window', 0x169770, [0xffffffff]*4)
            elif suite == 'write-order':
                for label, start, end in [
                    ('prefix_including_target', 0, 0x169774),
                    ('prefix_excluding_target', 0, 0x169770),
                    ('suffix_excluding_target_burst', 0x169780, 0x200000),
                    ('target_bank_row_2kib', 0x169000, 0x169800),
                    ('all_banks_row_16kib', 0x168000, 0x16c000),
                    ('full_2mib_control', 0, 0x200000),
                ]:
                    # Establish a correct target before touching each disjoint range.
                    command('mem_write 0xc0169700 0xffffffff 64')
                    before = command('mem_read 0xc0169770 4')
                    if dump_bytes(before, 0xc0169770, 4) != b'\xff'*4:
                        raise RuntimeError('local repair failed; cannot interpret the next order probe')
                    arm(0x169770)
                    command(f'mem_write 0x{0xc0000000+start:08x} 0xffffffff {(end-start)//4}', 120)
                    snapshot(label, 0x169770, [0xffffffff]*4)
                command('mem_write 0xc0169700 0xffffffff 64')
                arm(0x169770)
                command('mem_write 0xc0169770 0xffffffff')
                snapshot('repaired_before_idle', 0x169770, [0xffffffff]*4)
                time.sleep(5)
                snapshot('repaired_after_5s_idle', 0x169770, [0xffffffff]*4)
            elif suite == 'disturbance-search':
                def trial(start, end):
                    # Restore the same entire PRNG population before every trial;
                    # rewriting ones alone removes the triggering data transition.
                    command('mem_test 0xc0000000 0x200000', 120)
                    command('mem_write 0xc0169700 0xffffffff 64')
                    before = dump_bytes(command('mem_read 0xc0169770 4'), 0xc0169770, 4)
                    if before != b'\xff'*4:
                        raise RuntimeError('victim repair failed before disturbance trial')
                    arm(0x169770)
                    command(f'mem_write 0x{0xc0000000+start:08x} 0xffffffff {(end-start)//4}', 120)
                    trace = snapshot(f'range_{start:08x}_{end:08x}', 0x169770)
                    trace.update({'aggressor_start': hex(start), 'aggressor_end': hex(end),
                                  'before_word': '0xffffffff'})
                    return trace['physical_words'][0] != '0xffffffff'

                start, end = 0x169780, 0x200000
                result['disturbance_reproduced'] = trial(start, end)
                if result['disturbance_reproduced']:
                    while end-start > 4:
                        midpoint = (start + (end-start)//2) & ~3
                        if trial(start, midpoint):
                            end = midpoint
                        elif trial(midpoint, end):
                            start = midpoint
                        else:
                            result['bisection_stopped'] = 'neither half reproduced; range/history dependent'
                            break
                    result['disturbance_range'] = {'start': hex(start), 'end': hex(end)}
                    result['final_range_repeat'] = trial(start, end)
            elif suite == 'disturbance-controls':
                for label, start, end, pattern in [
                    ('bad_window_ones_repeat_1', 0x16cff0, 0x16d4a4, 0xffffffff),
                    ('bad_window_zeros', 0x16cff0, 0x16d4a4, 0),
                    ('bad_window_alternating_5', 0x16cff0, 0x16d4a4, 0x55555555),
                    ('bad_window_alternating_a', 0x16cff0, 0x16d4a4, 0xaaaaaaaa),
                    ('next_row_same_bank', 0x16d000, 0x16d800, 0xffffffff),
                    ('previous_row_same_bank', 0x165000, 0x165800, 0xffffffff),
                    ('next_row_other_bank', 0x16c000, 0x16c800, 0xffffffff),
                    ('second_next_row_same_bank', 0x171000, 0x171800, 0xffffffff),
                    ('same_row_after_victim', 0x169780, 0x169800, 0xffffffff),
                    ('bad_window_ones_repeat_2', 0x16cff0, 0x16d4a4, 0xffffffff),
                ]:
                    command('mem_test 0xc0000000 0x200000', 120)
                    command('mem_write 0xc0169700 0xffffffff 64')
                    before = dump_bytes(command('mem_read 0xc0169770 4'), 0xc0169770, 4)
                    if before != b'\xff'*4:
                        raise RuntimeError('victim repair failed before control trial')
                    arm(0x169770)
                    command(f'mem_write 0x{0xc0000000+start:08x} 0x{pattern:08x} {(end-start)//4}', 120)
                    trace = snapshot(label, 0x169770)
                    trace.update({'aggressor_start': hex(start), 'aggressor_end': hex(end),
                                  'pattern': hex(pattern), 'before_word': '0xffffffff'})
            elif suite == 'neighbor-read':
                for offset, expected in [(0x169770, 0xffffffff), (0xb64f4, 0xb4de6cd9),
                                         (0x64011ac, 0xfffff7ff)]:
                    geometry = burst_address(offset)
                    neighbor = (geometry['row'] + 1) * 0x4000 + geometry['bank'] * 0x800
                    command(f'mem_write 0x{0xc0000000+neighbor:08x} 0xaaaaaaaa 512', 120)
                    for action in ('read', 'write_ones', 'write_zeros'):
                        address = 0xc0000000 + offset
                        command(f'mem_write 0x{address:08x} 0x{expected:08x}')
                        before = dump_bytes(command(f'mem_read 0x{address:08x} 4'), address, 4)
                        if int.from_bytes(before, 'little') != expected:
                            raise RuntimeError('victim repair failed before neighbor trial')
                        arm(offset)
                        if action == 'read':
                            command(f'mem_read 0x{0xc0000000+neighbor:08x} 2048', 30)
                        else:
                            pattern = 0xffffffff if action == 'write_ones' else 0
                            command(f'mem_write 0x{0xc0000000+neighbor:08x} 0x{pattern:08x} 512', 120)
                        trace = snapshot(f'victim_{offset:08x}_neighbor_{action}', offset)
                        trace.update({'neighbor_start': hex(neighbor), 'neighbor_end': hex(neighbor+2048),
                                      'action': action, 'before_word': hex(expected)})
            else:
                raise ValueError('unknown diagnostic suite')
            result['completed'] = True
    except BaseException as error:
        result['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        for name in ('trace.uart.bin', 'trace.uart.tx.bin'):
            if (evidence/name).is_file():
                result[name+'_sha256'] = sha(evidence/name)
        if (evidence/'trace.uart.bin').is_file():
            (evidence/'trace.uart.log').write_text(ANSI.sub(b'', (evidence/'trace.uart.bin').read_bytes())
                                                  .decode(errors='replace').replace('\r', ''))
        (evidence/'trace-probe.json').write_text(json.dumps(result, indent=2)+'\n')
        print(evidence, flush=True)
    return evidence


def main():
    from scripts.project import VENV_PYTHON, tool_environment
    if Path(sys.prefix) != ROOT/'.venv':
        return subprocess.call([str(VENV_PYTHON), __file__, *sys.argv[1:]], env=tool_environment())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--experiment', choices=['dll-off-write-trace', 'dll-off-row-timing'],
                        default='dll-off-write-trace')
    parser.add_argument('--suite', choices=['initial', 'write-order', 'disturbance-search',
                                          'disturbance-controls', 'neighbor-read'], default='initial')
    args = parser.parse_args()
    run_probe(args.port, args.suite, args.experiment)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
