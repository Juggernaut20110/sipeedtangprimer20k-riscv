import serial,re,time,json,hashlib,datetime,pathlib
root=pathlib.Path('/home/user/git/fpga/sipeedtangprimer20k-riscv')
d=root/'docs/ddr3/diagnosis'/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-fixed-retention-offset-probe');d.mkdir()
(d/'probe.py').write_bytes(pathlib.Path(__file__).read_bytes())
source=root/'docs/ddr3/diagnosis/20261001T133554.525974Z-dll-off-fixed'
(d/'manifest.json').write_bytes((source/'manifest.json').read_bytes())
results=[]
with (d/'uart.bin').open('wb') as rx,(d/'uart.tx.bin').open('wb') as tx,serial.Serial('/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0',115200,timeout=.05,exclusive=True) as u:
 def command(c,timeout=10):
  tx.write(c.encode());tx.flush();u.write(c.encode());u.flush();b=bytearray();deadline=time.monotonic()+timeout
  while time.monotonic()<deadline:
   chunk=u.read(4096);b.extend(chunk);rx.write(chunk);rx.flush()
   clean=re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]',b'',b)
   if clean.rstrip().endswith(b'litex>'):
    t=clean.decode(errors='replace').replace('\r','');results.append({'command':c,'output':t});print(t,flush=True);return
  raise RuntimeError('No confirmed BIOS prompt; stop without reset')
 command('\n')
 command('mem_write 0xc00b64f4 0xb4de6cd9\n')
 for repeat in range(30):
  command('mem_read 0xc00b64f4 4\n');time.sleep(1)
 command('mem_test 0xc0001000 0x200000\n',120)
 command('mem_read 0xc00b64f4 4\n');command('mem_read 0xc00b74f4 4\n')
(d/'result.json').write_text(json.dumps(results,indent=2)+'\n')
(d/'uart.log').write_text(re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]',b'',(d/'uart.bin').read_bytes()).decode(errors='replace').replace('\r',''))
print(d)
