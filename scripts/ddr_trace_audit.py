#!/usr/bin/env python3
"""Independently reconstruct diagnostic trace records from retained UART bytes."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ANSI = re.compile(rb'\x1b\[[0-?]*[ -/]*[@-~]')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def memory(output, address, length):
    data = {}
    for line in output.splitlines():
        match = re.match(r'^0x([0-9a-fA-F]{8})\s+((?:[0-9a-fA-F]{2}\s+){1,16})', line)
        if match:
            start = int(match[1], 16)
            for index, value in enumerate(bytes.fromhex(match[2])):
                data[start + index] = value
    return bytes(data[address + index] for index in range(length))


def audit(directory):
    probe = json.loads((directory/'trace-probe.json').read_text())
    manifest = json.loads((directory/'manifest.json').read_text())
    require(probe['completed'] and probe['console_confirmed'], 'incomplete probe')
    require(probe['diagnostic_only'] and not probe['acceptance_credit'], 'incorrect acceptance label')
    for filename, expected in [
        ('bitstream.fs', manifest['bitstream_sha256']),
        ('bios.bin', manifest['bios_sha256']),
        ('ddr_trace.py', manifest['trace_source_sha256']),
        ('ddr_write_trace.py', probe['probe_source_sha256']),
        ('trace.uart.bin', probe['trace.uart.bin_sha256']),
        ('trace.uart.tx.bin', probe['trace.uart.tx.bin_sha256']),
        ('uart.bin', json.loads((directory/'result.json').read_text())['uart_sha256']),
    ]:
        require(digest(directory/filename) == expected, f'{filename} hash mismatch')
    require(probe['bitstream_sha256'] == manifest['bitstream_sha256'], 'bitstream identities differ')
    raw = ANSI.sub(b'', (directory/'trace.uart.bin').read_bytes()).decode(errors='replace').replace('\r', '')
    require(''.join(record['output'] for record in probe['records']) == raw, 'RX records differ from capture')
    require(''.join(record['command']+'\n' for record in probe['records']).encode() == (
        directory/'trace.uart.tx.bin').read_bytes(), 'TX records differ from capture')

    fields = next(ast.literal_eval(node.value) for node in ast.parse((directory/'ddr_trace.py').read_text()).body
                  if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                  and target.id == 'FRAME_FIELDS' for target in node.targets))
    registers = manifest['csr']['csr_registers']

    def values(name):
        reg = registers['ddr_trace_'+name]
        command = f"mem_read 0x{reg['addr']:08x} {reg['size']*4}"
        decoded = []
        for record in probe['records']:
            if record['command'] != command:
                continue
            raw_bytes = memory(record['output'], reg['addr'], reg['size']*4)
            words = [int.from_bytes(raw_bytes[index:index+4], 'little')
                     for index in range(0, len(raw_bytes), 4)]
            decoded.append(sum(word << (32*(len(words)-index-1)) for index, word in enumerate(words)))
        return decoded

    physical = []
    for record in probe['records']:
        match = re.fullmatch(r'mem_read 0x(c[0-9a-f]{7}) 16', record['command'])
        if match:
            address = int(match[1], 16)
            raw_bytes = memory(record['output'], address, 16)
            physical.append((address, [f'0x{int.from_bytes(raw_bytes[i:i+4], "little"):08x}'
                                       for i in range(0, 16, 4)]))
    require(len(physical) == len(probe['traces']), 'wrong number of physical readbacks')
    for (address, words), trace in zip(physical, probe['traces']):
        require(address == 0xc0000000 + (int(trace['offset'], 16) & ~15), 'wrong victim burst address')
        require(words == trace['physical_words'], 'physical readback differs from raw UART')
    require(values('status') == [trace['status'] for trace in probe['traces']], 'status differs from raw UART')
    frames = [frame for trace in probe['traces'] for frame in trace['frames']]
    raw_frames = values('frame')
    require(len(frames) == len(raw_frames), 'frame count differs from raw UART')
    for frame, value in zip(frames, raw_frames):
        require(value == int(frame['raw'], 16), 'frame differs from raw UART')
        for name, (offset, width) in fields.items():
            require(int(frame[name], 16) == (value >> offset) & ((1 << width)-1), f'{name} differs from frame bits')
    for name in ('read_early', 'read_nominal', 'read_late'):
        require(values(name) == [int(trace[name], 16) for trace in probe['traces'] if trace['read_complete']],
                f'{name} differs from raw UART')
    require(values('read_valid') == [trace['read_valid'] for trace in probe['traces'] if trace['read_complete']],
            'read-valid samples differ from raw UART')
    return {'evidence': str(directory.relative_to(ROOT)), 'audit': 'passed',
            'suite': probe.get('suite', 'initial'), 'bitstream_sha256': manifest['bitstream_sha256'],
            'trace_count': len(physical), 'decoded_frame_count': len(frames),
            'acceptance_credit': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path, nargs='+')
    args = parser.parse_args()
    print(json.dumps([audit(path.resolve()) for path in args.evidence], indent=2))


if __name__ == '__main__':
    main()
