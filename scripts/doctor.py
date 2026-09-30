#!/usr/bin/env python3
"""Check local build tools, the Gowin license path, and connected hardware access."""

import glob
import grp
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".tools"


def report(label, ok, detail):
    print(f"{'PASS' if ok else 'FAIL'} {label}: {detail}")
    return ok


def find_python():
    return ROOT / ".venv/bin/python"


def find_gcc():
    preferred = shutil.which("riscv-none-elf-gcc", path=os.pathsep.join([
        str(p) for p in (TOOLS / "riscv-gcc").glob("**/bin")
    ]))
    return preferred or shutil.which("riscv-none-elf-gcc")


def find_gowin():
    configured = os.environ.get("GOWIN_SH")
    candidates = [configured, "/home/user/.local/bin/gw_sh", shutil.which("gw_sh")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    return None


def main():
    failures = 0
    python = find_python()
    python_ok = python.is_file()
    if not report("Python 3.12 environment", python_ok, str(python) if python_ok else "run make setup"):
        failures += 1

    imports = [
        "migen", "litex", "litex_boards", "litedram", "liteeth", "liteiclink",
        "pythondata_cpu_vexriscv", "pythondata_software_picolibc", "pythondata_software_compiler_rt",
    ]
    if python_ok:
        result = subprocess.run([str(python), "-c", "import " + ", ".join(imports)], capture_output=True, text=True)
        if not report("LiteX Python imports", result.returncode == 0,
                      "all pinned packages import" if result.returncode == 0 else result.stderr.strip().splitlines()[-1]):
            failures += 1
    else:
        print("SKIP LiteX Python imports: environment is missing")

    gcc = find_gcc()
    gcc_ok = gcc is not None
    if gcc_ok:
        with tempfile.TemporaryDirectory(prefix="doctor-", dir=TOOLS if TOOLS.exists() else ROOT) as tmp:
            source = Path(tmp) / "probe.c"
            source.write_text("int probe(void) { return 7; }\n")
            for label, march in (("RV32I", "rv32i2p0"), ("RV32IM", "rv32i2p0_m")):
                output = Path(tmp) / f"{label}.o"
                result = subprocess.run([gcc, f"-march={march}", "-mabi=ilp32", "-c", str(source), "-o", str(output)], capture_output=True, text=True)
                if not report(f"RISC-V {label} compile", result.returncode == 0,
                              f"{Path(gcc).name} {march}" if result.returncode == 0 else result.stderr.strip().splitlines()[-1]):
                    failures += 1
    else:
        if not report("RISC-V compiler", False, "run make setup"):
            failures += 1

    gowin = find_gowin()
    if gowin:
        try:
            # gw_sh is a Tcl console; it ignores --version and becomes interactive
            # when it inherits a terminal. Close stdin so the console exits at EOF.
            result = subprocess.run(
                [gowin], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=45
            )
        except subprocess.TimeoutExpired as error:
            captured = []
            for stream in (error.stdout, error.stderr):
                if stream:
                    captured.append(stream.decode(errors="replace") if isinstance(stream, bytes) else stream)
            output = "\n".join(captured).strip()
            detail = f"launcher timed out after {error.timeout}s"
            if output:
                detail += "; partial output: " + " | ".join(output.splitlines())[-500:]
            detail += f"; check with `timeout 60s {gowin} </dev/null`"
            if not report("Gowin CLI startup", False, detail):
                failures += 1
        except OSError as error:
            if not report("Gowin CLI startup", False, str(error)):
                failures += 1
        else:
            output = (result.stdout + result.stderr).strip()
            output_lines = output.splitlines()
            detail = output_lines[0][:180] if output_lines else f"exit {result.returncode}"
            if result.returncode != 0 and ("AF_NETLINK" in output or "hostid" in output.lower()):
                detail = "sandbox denied AF_NETLINK host-ID lookup; run make doctor in a normal user shell"
            if not report("Gowin CLI startup", result.returncode == 0, detail):
                failures += 1
    else:
        if not report("Gowin CLI", False, "set GOWIN_SH to the executable gw_sh launcher"):
            failures += 1

    loader = shutil.which("openFPGALoader", path=os.pathsep.join([str(TOOLS / "bin"), os.environ.get("PATH", "")]))
    if loader:
        result = subprocess.run([loader, "--Version"], capture_output=True, text=True, timeout=20)
        detail = (result.stdout + result.stderr).strip().splitlines()
        if not report("openFPGALoader runtime", result.returncode == 0,
                      detail[0] if detail else "no version output"):
            failures += 1
    else:
        if not report("openFPGALoader", False, "run make setup"):
            failures += 1

    ports = sorted(glob.glob("/dev/serial/by-id/*"))
    usb_nodes = sorted(glob.glob("/dev/bus/usb/*/*"))
    writable_usb = [node for node in usb_nodes if os.access(node, os.R_OK | os.W_OK)]
    if ports:
        print("SERIAL devices: " + ", ".join(ports))
        denied = [port for port in ports if not os.access(port, os.R_OK | os.W_OK)]
        if denied:
            print("HARDWARE access: serial device is not readable/writable; run `sudo usermod -aG dialout \"$USER\"`, log out and back in, then reconnect the Dock.")
        else:
            print("HARDWARE access: serial device permissions look usable")
    else:
        print("HARDWARE pending: no /dev/serial/by-id device is visible; connect the Dock's USB-C JTAG/UART cable and select its stable by-id port.")
    if usb_nodes and not writable_usb:
        rule = TOOLS / "openfpgaloader/usr/lib/udev/rules.d/99-openfpgaloader.rules"
        print("HARDWARE access: USB nodes are present but none are writable by this user.")
        if rule.is_file():
            commands = [
                f"sudo install -m 0644 {rule} /etc/udev/rules.d/99-openfpgaloader.rules",
            ]
            plugdev_gid = grp.getgrnam("plugdev").gr_gid
            if plugdev_gid not in os.getgroups() and os.getegid() != plugdev_gid:
                commands.append('sudo usermod -aG plugdev "$USER"')
            commands.append("sudo udevadm control --reload-rules && sudo udevadm trigger")
            suffix = " Log out/in if plugdev membership was added, then reconnect the Dock."
            print("Run: " + " && ".join(f"`{command}`" for command in commands) + "." + suffix)
        else:
            print("Install openFPGALoader's 99-openfpgaloader.rules under /etc/udev/rules.d, add your account to plugdev, reload udev, log out/in, and reconnect the Dock.")
    elif not usb_nodes:
        print("HARDWARE pending: no USB device nodes are visible; the FPGA programmer check will run after the Dock is connected.")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
