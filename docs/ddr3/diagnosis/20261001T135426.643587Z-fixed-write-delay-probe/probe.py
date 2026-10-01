import serial,re,time,json,hashlib,datetime,pathlib
root=pathlib.Path('/home/user/git/fpga/sipeedtangprimer20k-riscv')
d=root/'docs/ddr3/diagnosis'/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-fixed-write-delay-probe');d.mkdir()
(d/'probe.py').write_bytes(pathlib.Path(__file__).read_bytes())
source=root/'docs/ddr3/diagnosis/20261001T135345.189228Z-dll-off-write-sweep'
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
 manifest=json.loads((source/'manifest.json').read_text());registers=manifest['csr']['csr_registers']
 def write(name,value):command(f"mem_write 0x{registers[name]['addr']:08x} {value}\n")
 for offset in (0,-24,24,-48,48,-72,72):
  results.append({'write_delay_offset':offset})
  write('ddr_write_select',3);write('ddr_write_enable',1);write('ddr_write_reset',1)
  write('ddr_write_direction',int(offset<0))
  for step in range(abs(offset)):write('ddr_write_move',1)
  command('sdram_cal\n',90)
  command('mem_test 0xc0000000 0x200000\n',120)
  for repeat in range(3):command('mem_verify 0xc0000000 0x200000\n',120)
  command('mem_test 0x40000000 0x200000\n',120)
  command('mem_verify 0xc0000000 0x200000\n',120)
 write('ddr_write_enable',0)
 command('sdram_cal\n',90)
(d/'result.json').write_text(json.dumps(results,indent=2)+'\n')
(d/'uart.log').write_text(re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]',b'',(d/'uart.bin').read_bytes()).decode(errors='replace').replace('\r',''))
print(d)
