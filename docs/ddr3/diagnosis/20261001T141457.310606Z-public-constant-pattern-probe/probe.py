import serial,re,time,json,hashlib,datetime,pathlib
root=pathlib.Path('/home/user/git/fpga/sipeedtangprimer20k-riscv')
d=root/'docs/ddr3/diagnosis'/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-public-constant-pattern-probe');d.mkdir()
(d/'probe.py').write_bytes(pathlib.Path(__file__).read_bytes())
source=root/'build/ddr3/diagnosis/public-before-boot-fix/minimal'
(d/'build-metadata.json').write_bytes((source/'build-metadata.json').read_bytes())
(d/'source-session.json').write_bytes((root/'build/ddr3/ddr-test-sessions/20261001T140041.989982Z-ddr3-minimal/session.json').read_bytes())
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
 for value in (0xffffffff,0xaaaaaaaa,0x55555555,0x00100000):
  command(f'mem_write 0xc0000000 0x{value:08x} 524288\n',120)
  for address in (0xc00b64f4,0xc0169770,0xc0000000,0xc01ffffc):
   for repeat in range(3):command(f'mem_read 0x{address:08x} 4\n')
 command('mem_test 0xc0000000 0x200000\n',120)
(d/'result.json').write_text(json.dumps(results,indent=2)+'\n')
(d/'uart.log').write_text(re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]',b'',(d/'uart.bin').read_bytes()).decode(errors='replace').replace('\r',''))
print(d)
