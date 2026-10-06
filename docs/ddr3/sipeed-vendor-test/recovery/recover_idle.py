from pathlib import Path
import hashlib,json,subprocess,time
import serial
ROOT=Path('/home/user/git/fpga/sipeedtangprimer20k-riscv')
BUILD=ROOT/'build/sipeed-ddr-test/idle-build'
OUT=ROOT/'docs/ddr3/sipeed-vendor-test/recovery'
OUT.mkdir(exist_ok=True)
(OUT/'recover_idle.py').write_bytes(Path(__file__).read_bytes())
for name in ['idle.v','idle.cst','idle.tcl','idle-build.log']:
    (OUT/name).write_bytes((BUILD/name).read_bytes())
(OUT/'pnr.rpt.txt').write_bytes((BUILD/'impl/pnr/ddr_idle.rpt.txt').read_bytes())
bitstream=BUILD/'impl/pnr/ddr_idle.fs'
cmd=[str(ROOT/'.tools/bin/openFPGALoader'),'--board','tangprimer20k','--write-sram',str(bitstream)]
proc=subprocess.run(cmd,capture_output=True,timeout=45)
(OUT/'programmer.log').write_bytes(proc.stdout+proc.stderr)
result={'command':cmd,'exit_code':proc.returncode,'flash_programming':False,
        'bitstream_sha256':hashlib.sha256(bitstream.read_bytes()).hexdigest(),
        'ddr_state':{'reset_n':0,'cke':0,'cs_n':1,'ck_p':0,'odt':0}}
if proc.returncode:
    raise RuntimeError('Idle programming failed')
data=bytearray()
port='/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0'
with serial.Serial(port,115200,timeout=.1,exclusive=True) as uart:
    uart.reset_input_buffer()
    end=time.monotonic()+5
    while time.monotonic()<end:
        data.extend(uart.read(4096))
(OUT/'uart.bin').write_bytes(data)
result.update(uart_requested=port,uart_bytes=len(data),observation_seconds=5,
              status='ddr_held_in_reset_uart_quiet' if not data else 'unexpected_uart_activity')
(OUT/'results.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
