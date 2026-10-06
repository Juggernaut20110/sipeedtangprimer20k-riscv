"""SRAM programming with complete child-process output capture."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def program_sram(bitstream, log_path):
    command = [str(ROOT / ".tools/bin/openFPGALoader"), "--board", "tangprimer20k",
               "--write-sram", str(Path(bitstream).resolve())]
    with Path(log_path).open("wb") as log:
        log.write(("command: " + " ".join(command) + "\n").encode())
        log.flush()
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=60)
    if result.returncode:
        raise RuntimeError(f"SRAM programming failed ({result.returncode}); see {log_path}")
    return command

def assert_hardware_available(port):
    """Check both the selected UART and its parent USB device before a batch."""
    uart = Path(port)
    if not uart.exists():
        raise RuntimeError(f"UART is missing: {port}")
    targets = [uart]
    device = (Path('/sys/class/tty') / uart.resolve().name / 'device').resolve()
    for parent in [device, *device.parents]:
        if (parent / 'busnum').is_file() and (parent / 'devnum').is_file():
            bus = int((parent / 'busnum').read_text())
            number = int((parent / 'devnum').read_text())
            targets.append(Path(f'/dev/bus/usb/{bus:03d}/{number:03d}'))
            break
    for target in targets:
        result = subprocess.run(['fuser',str(target)],capture_output=True)
        if result.returncode == 0:
            raise RuntimeError(f"hardware has an active owner: {target}; {result.stdout.decode().strip()}")
        if result.returncode != 1:
            raise RuntimeError(f"hardware ownership check failed for {target}: {result.stderr.decode().strip()}")
