from pathlib import Path
import datetime, hashlib, json, subprocess, time
import serial
root=Path('/home/user/git/fpga/sipeedtangprimer20k-riscv')
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
out=root/'docs/ddr3/diagnosis'/(stamp+'-hynix-idle-recovery');out.mkdir()
source=root/'docs/ddr3/sipeed-vendor-test/recovery'
for name in ['idle.v','idle.cst','idle.tcl','idle.vg','pnr.rpt.txt']:
 (out/name).write_bytes((source/name).read_bytes())
image=root/'build/sipeed-ddr-test/idle-build/impl/pnr/ddr_idle.fs'
expected='a7a702e3ff9835ab3ee653f0f9ff7c90165808db514b3230dcc91d0314b93df3'
assert hashlib.sha256(image.read_bytes()).hexdigest()==expected
(out/'idle.fs').write_bytes(image.read_bytes())
(out/'recover_idle.py').write_bytes(Path(__file__).read_bytes())
command=[str(root/'.tools/bin/openFPGALoader'),'--board','tangprimer20k','--write-sram',str(out/'idle.fs')]
process=subprocess.run(command,capture_output=True,timeout=45)
(out/'programmer.log').write_bytes(process.stdout+process.stderr)
assert process.returncode==0
raw=bytearray();port='/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0'
with serial.Serial(port,115200,timeout=.1,exclusive=True) as uart:
 uart.reset_input_buffer();end=time.monotonic()+5
 while time.monotonic()<end: raw.extend(uart.read(4096))
(out/'uart.bin').write_bytes(raw)
result=dict(command=command,exit_code=process.returncode,flash_programming=False,bitstream_sha256=expected,
 ddr_state=dict(reset_n=0,cke=0,cs_n=1,ck_p=0,odt=0),uart_bytes=len(raw),observation_seconds=5,
 status='ddr_held_in_reset_uart_quiet' if not raw else 'unexpected_uart_activity',
 uart_sha256=hashlib.sha256(raw).hexdigest(),recovery_method='dedicated_idle_sram_image')
(out/'results.json').write_text(json.dumps(result,indent=2)+'\n')
print(out)
