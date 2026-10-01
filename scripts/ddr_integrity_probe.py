"""ROM-console probes of a fresh, timing-checked diagnostic DDR image."""
import datetime
import json
import re
import time
from pathlib import Path

from scripts.ddr_diagnose import ROOT, capture, sha

ANSI = re.compile(rb'\x1b\[[0-?]*[ -/]*[@-~]')


def summarize(records):
    rows = []
    for record in records:
        if 'write_delay_offset' in record:
            rows.append({'write_delay_offset': record['write_delay_offset'], 'memtests': [], 'verifications': []})
        if not rows:
            rows.append({'memtests': [], 'verifications': []})
        output = record.get('output', '')
        rows[-1]['memtests'].extend(int(value) for value in re.findall(r'data errors:\s+(\d+)/', output))
        rows[-1]['verifications'].extend(int(value) for value in re.findall(r'SDRAM_VERIFY[^\n]* errors=(\d+)', output))
        if record.get('command') == 'sdram_cal\n':
            lanes = re.findall(r'SDRAM_READ_LEVELING_LANE[^\n]+', output)
            rows[-1]['training'] = lanes
    return rows


def run_probe(device, kind):
    """Capture a fresh BIOS boot, then use one UART owner and bounded commands.

    These are diagnosis records, never acceptance results. No application runs
    from DDR, and every command must finish at the BIOS prompt before proceeding.
    """
    import serial
    experiment = {'read-only': 'dll-off-integrity', 'write-delay': 'dll-off-write-sweep'}[kind]
    source = capture(experiment, device)
    manifest = json.loads((source / 'manifest.json').read_text())
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    evidence = ROOT / 'docs/ddr3/diagnosis' / (stamp + '-' + kind + '-probe')
    evidence.mkdir()
    (evidence / 'manifest.json').write_bytes((source / 'manifest.json').read_bytes())
    (evidence / 'ddr_integrity_probe.py').write_bytes(Path(__file__).read_bytes())
    result = {'diagnostic_only': True, 'source_capture': str(source.relative_to(ROOT)),
              'kind': kind, 'console_confirmed': False, 'records': []}
    records = result['records']
    try:
        with (evidence / 'uart.bin').open('wb') as rx, (evidence / 'uart.tx.bin').open('wb') as tx, \
                serial.Serial(device, 115200, timeout=0.05, exclusive=True) as uart:
            def command(value, timeout=10):
                result['console_confirmed'] = False
                tx.write(value.encode()); tx.flush()
                uart.write(value.encode()); uart.flush()
                data = bytearray()
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    chunk = uart.read(4096)
                    data.extend(chunk)
                    rx.write(chunk); rx.flush()
                    clean = ANSI.sub(b'', data)
                    if clean.rstrip().endswith(b'litex>'):
                        output = clean.decode(errors='replace').replace('\r', '')
                        records.append({'command': value, 'output': output})
                        result['console_confirmed'] = True
                        print(output, flush=True)
                        return output
                raise TimeoutError('No confirmed BIOS prompt; stop without reset or reprogramming')

            command('\n')
            if kind == 'read-only':
                for alias in (0xc0000000, 0x40000000):
                    for _ in range(3):
                        command(f'mem_verify 0x{alias:08x} 0x200000\n', 120)
            else:
                registers = manifest['csr']['csr_registers']

                def write(name, value):
                    command(f"mem_write 0x{registers[name]['addr']:08x} {value}\n")

                for offset in (0, -24, 24, -48, 48, -72, 72):
                    records.append({'write_delay_offset': offset})
                    write('ddr_write_select', 3)
                    write('ddr_write_enable', 1)
                    write('ddr_write_reset', 1)
                    write('ddr_write_direction', int(offset < 0))
                    for _ in range(abs(offset)):
                        write('ddr_write_move', 1)
                    calibration = command('sdram_cal\n', 90)
                    # Leave failed calibrations in the evidence; do not treat
                    # subsequent controller accesses as usable memory testing.
                    if len(re.findall(r'SDRAM_READ_LEVELING_LANE[^\n]* status=passed ', calibration)) != 2:
                        result['stop_reason'] = 'write-delay candidate failed training'
                        break
                    command('mem_test 0xc0000000 0x200000\n', 120)
                    for _ in range(3):
                        command('mem_verify 0xc0000000 0x200000\n', 120)
                    command('mem_test 0x40000000 0x200000\n', 120)
                    command('mem_verify 0xc0000000 0x200000\n', 120)
                write('ddr_write_enable', 0)
                command('sdram_cal\n', 90)
    except BaseException as error:
        result['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        result['summary'] = summarize(records)
        for name in ('uart.bin', 'uart.tx.bin'):
            if (evidence / name).exists():
                result[name + '_sha256'] = sha(evidence / name)
        if (evidence / 'uart.bin').exists():
            (evidence / 'uart.log').write_text(ANSI.sub(b'', (evidence / 'uart.bin').read_bytes())
                                              .decode(errors='replace').replace('\r', ''))
        (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(evidence, flush=True)
    return evidence
