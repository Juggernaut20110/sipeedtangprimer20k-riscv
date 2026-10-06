from pathlib import Path
import datetime, hashlib, json, subprocess, time
import serial

ROOT = Path('/home/user/git/fpga/sipeedtangprimer20k-riscv')
BUILD = ROOT / 'build/sipeed-ddr-test'
BITSTREAM = BUILD / 'upstream/impl/pnr/sipeed_ddr_test.fs'
PORT = '/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0'
LOADER = str(ROOT / '.tools/bin/openFPGALoader')
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
OUT = ROOT / 'docs/ddr3/sipeed-vendor-test' / (stamp+'-new-board')
OUT.mkdir()
(OUT / 'run_test.py').write_bytes(Path(__file__).read_bytes())
for src, name in [(BUILD/'build.tcl','build.tcl'), (BUILD/'build.log','build.log'),
                  (BUILD/'upstream/impl/pnr/sipeed_ddr_test.tr','timing.tr'),
                  (BUILD/'upstream/impl/pnr/sipeed_ddr_test.rpt.txt','pnr.rpt.txt')]:
    (OUT / name).write_bytes(src.read_bytes())
rev = subprocess.check_output(['git','-C',str(ROOT/'.deps/sipeed-tangprimer20k-example'),'rev-parse','HEAD'],text=True).strip()
source_hashes = {str(p.relative_to(BUILD/'upstream')): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (BUILD/'upstream/src').rglob('*') if p.is_file()}
result = {'source_url':'https://github.com/sipeed/TangPrimer-20K-example/tree/main/DDR-test',
          'source_commit':rev, 'source_modified':False, 'source_sha256':source_hashes,
          'gowin_version':'V1.9.12.04', 'bitstream':str(BITSTREAM.relative_to(ROOT)),
          'bitstream_sha256':hashlib.sha256(BITSTREAM.read_bytes()).hexdigest(),
          'uart_requested':PORT, 'uart_resolved':str(Path(PORT).resolve()),
          'board_identity':'new Tang Primer 20K Dock attached by user on 2026-10-05; chip marking not yet confirmed', 'baud':115200, 'flash_programming':False, 'runs':[]}
expected_hash = '167d04a87c0e0084204a1863f8a999f7c696d0d25ca959a82228c203470b7f50'
if result['bitstream_sha256'] != expected_hash:
    raise RuntimeError('Test bitstream differs from the previous-board comparison image')
def save():
    (OUT/'results.json').write_text(json.dumps(result,indent=2)+'\n')
print('Evidence: '+str(OUT), flush=True)
owner = subprocess.run(['fuser','-v',PORT],capture_output=True)
result['uart_owner_check_exit_code'] = owner.returncode
result['uart_owner_check_output'] = (owner.stdout+owner.stderr).decode(errors='replace')
if owner.returncode != 1:
    save()
    raise RuntimeError('UART is already owned or cannot be checked')
programmed = False
try:
    with serial.Serial(PORT,115200,timeout=.1,exclusive=True) as uart:
        for run_number in range(1,4):
            uart.reset_input_buffer()
            start = time.monotonic()
            print(f'Run {run_number}: loading SRAM',flush=True)
            cmd = [LOADER,'--board','tangprimer20k','--write-sram',str(BITSTREAM)]
            programmed = True
            proc = subprocess.run(cmd,capture_output=True,timeout=45)
            (OUT/f'run-{run_number}.programmer.log').write_bytes(proc.stdout+proc.stderr)
            if proc.returncode:
                raise RuntimeError(f'Programming failed: {proc.returncode}')
            data = bytearray()
            events = []
            pending = bytearray()
            deadline = time.monotonic()+110
            with (OUT/f'run-{run_number}.uart.bin').open('wb') as raw:
                while time.monotonic()<deadline:
                    chunk = uart.read(4096)
                    if not chunk:
                        continue
                    raw.write(chunk); raw.flush(); data.extend(chunk); pending.extend(chunk)
                    while b'\n' in pending:
                        line, _, pending = pending.partition(b'\n')
                        message = line.decode(errors='replace').rstrip('\r')
                        elapsed = round(time.monotonic()-start,3)
                        events.append({'seconds':elapsed,'line':message})
                        print(f'Run {run_number} +{elapsed:.1f}s: {message}',flush=True)
            text = data.decode(errors='replace')
            (OUT/f'run-{run_number}.uart.txt').write_text(text)
            (OUT/f'run-{run_number}.events.json').write_text(json.dumps(events,indent=2)+'\n')
            count = text.count('Test Finished')
            failed = 'Check Failed' in text or 'Error Occured' in text
            passed = (count>=1 and not failed and ('DDR Size: 2G' in text or 'DDR Size: 1G' in text)
                      and text.count('Check Stage 1 Finished without Mismatch')>=count
                      and text.count('Check Stage 2 Finished without Mismatch')>=count)
            result['runs'].append({'run':run_number,'programmer_command':cmd,'programmer_exit_code':proc.returncode,
                'capture_seconds':110,'elapsed_seconds':round(time.monotonic()-start,3),
                'uart_sha256':hashlib.sha256(data).hexdigest(),'uart_bytes':len(data),
                'completed_tests':count,'mismatch_reported':failed,'detected_2gbit':'DDR Size: 2G' in text, 'detected_1gbit':'DDR Size: 1G' in text,
                'status':'passed' if passed else 'failed' if failed else 'incomplete'})
            save()
            print(f'Run {run_number}: '+result['runs'][-1]['status'],flush=True)
except Exception as error:
    result['error']=repr(error)
    raise
finally:
    if programmed:
        idle_path = BUILD/'idle-build/impl/pnr/ddr_idle.fs'
        result['recovery_bitstream_sha256']=hashlib.sha256(idle_path.read_bytes()).hexdigest()
        if result['recovery_bitstream_sha256'] != 'a7a702e3ff9835ab3ee653f0f9ff7c90165808db514b3230dcc91d0314b93df3':
            raise RuntimeError('Idle recovery image hash mismatch')
        reset_cmd = [LOADER,'--board','tangprimer20k','--write-sram',str(idle_path)]
        proc = subprocess.run(reset_cmd,capture_output=True,timeout=30)
        (OUT/'recovery-reset.log').write_bytes(proc.stdout+proc.stderr)
        result['recovery_command']=reset_cmd
        result['recovery_exit_code']=proc.returncode
        if proc.returncode==0:
            data = bytearray()
            with serial.Serial(PORT,115200,timeout=.1,exclusive=True) as uart:
                uart.reset_input_buffer()
                end=time.monotonic()+3
                while time.monotonic()<end:
                    data.extend(uart.read(4096))
            (OUT/'recovery.uart.bin').write_bytes(data)
            result['recovery_uart_bytes']=len(data)
            result['recovery_status']='ddr_held_in_reset_uart_quiet' if not data else 'uart_activity_after_idle_load'
    result['status']='passed' if len(result['runs'])==3 and all(r['status']=='passed' for r in result['runs']) else 'failed' if any(r['status']=='failed' for r in result['runs']) else 'incomplete'
    save()
    print(json.dumps(result,indent=2),flush=True)
