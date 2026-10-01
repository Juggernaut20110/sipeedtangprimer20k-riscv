import pathlib,datetime,serial,time,re,json
root=pathlib.Path('/home/user/git/fpga/sipeedtangprimer20k-riscv')
d=root/'docs/ddr3/diagnosis'/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-full-probe-passive');d.mkdir()
(d/'probe.py').write_bytes(pathlib.Path(__file__).read_bytes())
raw=bytearray()
with serial.Serial('/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0',115200,timeout=.1,exclusive=True) as u,(d/'uart.bin').open('wb') as f:
 deadline=time.monotonic()+45
 while time.monotonic()<deadline:
  chunk=u.read(4096);raw.extend(chunk);f.write(chunk);f.flush()
  if chunk:print(chunk.decode(errors='replace'),flush=True)
clean=re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]',b'',raw).decode(errors='replace')
(d/'uart.log').write_text(clean)
(d/'result.json').write_text(json.dumps({'passive_only':True,'commands_sent':0,'bytes':len(raw),'application_end_seen':'DDR_TEST_END ' in clean,'bios_prompt_seen':clean.rstrip().endswith('litex>')},indent=2)+'\n')
print(d)
