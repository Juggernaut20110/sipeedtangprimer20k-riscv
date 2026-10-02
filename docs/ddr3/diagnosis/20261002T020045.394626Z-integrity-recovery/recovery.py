from pathlib import Path
import datetime, hashlib, json, subprocess, sys, time
import serial
ROOT = Path('/home/user/git/fpga/sipeedtangprimer20k-riscv')
sys.path.insert(0, str(ROOT))
from scripts.project import tool_environment
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
directory = ROOT/'docs/ddr3/diagnosis'/f'{stamp}-integrity-recovery'
directory.mkdir()
(directory/'recovery.py').write_bytes(Path(__file__).read_bytes())
port = '/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0'
owner = subprocess.run(['fuser', '-v', port], capture_output=True)
result = {'reason': 'safe idle after DDR write-disturbance investigation', 'flash_programming': False,
          'uart_requested': port, 'uart_resolved': str(Path(port).resolve()),
          'fuser_exit_code': owner.returncode, 'fuser_output': (owner.stdout+owner.stderr).decode()}
if owner.returncode != 1:
    raise RuntimeError('UART ownership is not exclusive')
def passive(name, seconds):
    data = bytearray()
    with serial.Serial(port, 115200, timeout=.05, exclusive=True) as uart:
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            data.extend(uart.read(4096))
    (directory/f'{name}.uart.bin').write_bytes(data)
    result[name+'_uart_rx_bytes'] = len(data)
    result[name+'_uart_rx_sha256'] = hashlib.sha256(data).hexdigest()
passive('pre_reset', 3)
programmer = str(ROOT/'.tools/bin/openFPGALoader')
for label, args in [('reset', ['--board', 'tangprimer20k', '--detect', '--reset']),
                    ('post_reset_detect', ['--board', 'tangprimer20k', '--detect'])]:
    command = [programmer, *args]
    completed = subprocess.run(command, env=tool_environment(), capture_output=True, timeout=30)
    data = completed.stdout+completed.stderr
    (directory/f'{label}.log').write_bytes(data)
    result[label+'_command'] = command
    result[label+'_exit_code'] = completed.returncode
    result[label+'_sha256'] = hashlib.sha256(data).hexdigest()
    if completed.returncode:
        raise RuntimeError(f'{label} failed')
passive('post_reset', 3)
result['recovery_status'] = 'safe_idle_confirmed_after_explicit_fpga_reset' if result['post_reset_uart_rx_bytes'] == 0 else 'unexpected_post_reset_uart_activity'
(directory/'recovery.json').write_text(json.dumps(result, indent=2)+'\n')
print(directory)
print(json.dumps(result, indent=2))
