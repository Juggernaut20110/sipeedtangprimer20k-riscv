#!/usr/bin/env python3
"""Build a peripheral-safe idle image and load it into SRAM after a completed workload."""
import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.project import tool_environment
from scripts.peripheral_evidence import board_identity
from scripts.peripheral_program import assert_hardware_available

SOURCES = [ROOT / 'gateware/peripheral_idle' / name for name in ('idle.v', 'idle.cst')]
BUILD = ROOT / 'build/peripheral-idle'
BITSTREAM = BUILD / 'impl/pnr/peripheral_idle.fs'
SAFE_STATE = {'ddr_reset_n': 0, 'ddr_cke': 0, 'ddr_cs': 1, 'ddr_ck': 0, 'ddr_odt': 0,
              'sd_cs': 1, 'sd_clk': 0, 'sd_mosi': 1, 'phy_reset_n': 0,
              'eth_txen': 0, 'eth_txd': 0, 'eth_mdc': 0, 'uart_tx': 1}

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def build_idle():
    BUILD.mkdir(parents=True, exist_ok=True)
    identities = {str(p.relative_to(ROOT)): sha(p) for p in SOURCES}
    metadata_path = BUILD / 'build-metadata.json'
    if metadata_path.exists() and BITSTREAM.exists():
        previous = json.loads(metadata_path.read_text())
        if previous.get('sources') == identities and previous.get('bitstream_sha256') == sha(BITSTREAM):
            return previous
    script = BUILD / 'build.tcl'
    script.write_text('set_device -name GW2A-18C GW2A-LV18PG256C8/I7\n' +
                      ''.join(f'add_file {{{p}}}\n' for p in SOURCES) +
                      'set_option -top_module peripheral_idle\n'
                      'set_option -output_base_name peripheral_idle\nrun all\n')
    env = tool_environment()
    result = subprocess.run([env['GOWIN_SH'], str(script)], cwd=BUILD, env=env,
                            capture_output=True, timeout=120)
    (BUILD / 'build.log').write_bytes(result.stdout + result.stderr)
    if result.returncode or not BITSTREAM.exists():
        raise RuntimeError('safe idle build failed; see build/peripheral-idle/build.log')
    record = {'sources': identities, 'bitstream': str(BITSTREAM.relative_to(ROOT)),
              'bitstream_sha256': sha(BITSTREAM), 'safe_outputs': SAFE_STATE,
              'unassigned_pins': 'FPGA configuration defaults; no outputs requested',
              'flash_programming': False}
    metadata_path.write_text(json.dumps(record, indent=2) + '\n')
    return record

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-only', action='store_true')
    p.add_argument('--port')
    p.add_argument('--board-serial')
    p.add_argument('--board-revision')
    p.add_argument('--workload-complete', action='store_true',
                   help='assert the prior workload has ended and no SD file is open')
    p.add_argument('--attach-to', action='append', default=[], help='existing evidence session directory')
    a = p.parse_args()
    metadata = build_idle()
    if a.build_only:
        print(json.dumps(metadata, indent=2)); return 0
    if not a.port or not a.workload_complete:
        p.error('an explicit UART port and --workload-complete are required')
    board = board_identity(a.board_serial, a.board_revision)
    assert_hardware_available(a.port)
    import serial
    session = ROOT / 'docs/peripherals/evidence' / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ-final-recovery')
    session.mkdir(parents=True)
    command = [str(ROOT / '.tools/bin/openFPGALoader'), '--board', 'tangprimer20k',
               '--write-sram', str(BITSTREAM)]
    record = {'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(), 'board': board, 'command': command, 'idle_bitstream_sha256': metadata['bitstream_sha256'],
              'safe_outputs': SAFE_STATE, 'flash_programming': False, 'status': 'in_progress'}
    with serial.Serial(a.port, 115200, timeout=0.1, exclusive=True) as uart:
        result = subprocess.run(command, capture_output=True, timeout=60)
        (session / 'programmer.log').write_bytes(result.stdout + result.stderr)
        record['programmer_exit_code'] = result.returncode
        if result.returncode:
            record['status'] = 'programming_failed'
        else:
            uart.reset_input_buffer()
            started = time.monotonic(); data = bytearray()
            while time.monotonic() - started < 5:
                data.extend(uart.read(4096))
            (session / 'uart.bin').write_bytes(data)
            record.update(uart_quiet_seconds=time.monotonic()-started, uart_bytes=len(data),
                          status='passed' if not data else 'unexpected_uart_activity')
    record['finished_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    shutil.copyfile(BITSTREAM, session / 'idle.fs')
    (session / 'recovery.json').write_text(json.dumps(record, indent=2)+'\n')
    if record['status'] == 'passed':
        for value in a.attach_to:
            target = Path(value).resolve(); target.relative_to((ROOT/'docs/peripherals/evidence').resolve())
            manifest_path = target / 'manifest.json'; manifest = json.loads(manifest_path.read_text())
            if manifest.get('board') != board:
                raise RuntimeError('recovery board identity does not match the target session')
            names = {'recovery_programmer': ('programmer.log','recovery-programmer.log'),
                     'recovery_uart': ('uart.bin','recovery-uart.bin'),
                     'recovery_record': ('recovery.json','recovery.json'),
                     'recovery_bitstream': ('idle.fs','recovery-idle.fs')}
            for name,(source,dest) in names.items():
                path=target/dest;shutil.copyfile(session/source,path)
                manifest['artifacts']=[r for r in manifest['artifacts'] if r['name']!=name]
                manifest['artifacts'].append({'name':name,'path':dest,'bytes':path.stat().st_size,'sha256':sha(path)})
            manifest.setdefault('criteria',{})['final_recovery']={
                'status':'passed', 'measurements':{'sram_programmed':True,'ddr_held_in_reset':True,
                'sd_deselected':True,'sd_clock_stopped':True,'phy_held_in_reset':True,
                'idle_bitstream_sha256':metadata['bitstream_sha256'],
                'uart_quiet_seconds':record['uart_quiet_seconds'],'uart_bytes':0},'evidence':list(names)}
            manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(record,indent=2)); return 0 if record['status']=='passed' else 1

if __name__ == '__main__':
    sys.exit(main())
