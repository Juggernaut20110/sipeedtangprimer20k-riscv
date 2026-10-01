# Hardware bring-up

## Setup and commands

Use Ubuntu x86-64 with Gowin EDA installed and licensed. Run:

```sh
make setup
make doctor
make build PROFILE=minimal
make run PROFILE=minimal PORT=/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
```

`make setup` checks out the exact repository commits in `dependencies.lock.json`, creates `.venv` with local CPython 3.12, installs the pinned Python packages, downloads xPack RISC-V GCC, and extracts the Ubuntu openFPGALoader package under `.tools/`. It does not install packages system-wide. Gowin stays in its existing installation. `GOWIN_SH=/path/to/gw_sh` selects another executable wrapper.

`make doctor` checks imports, both RV32 compiler settings, the pinned Java/SBT CPU generator, Gowin Tcl console startup, programmer runtime, USB nodes, and serial permissions. A successful Gowin build additionally verifies that the installed license permits synthesis and place-and-route. `make build PROFILE=minimal` creates the BIOS, Gowin SRAM bitstream, and matching firmware. `make load PROFILE=minimal` loads an existing bitstream into FPGA SRAM. `make run PROFILE=minimal PORT=…` builds stale or missing artifacts, opens the named serial port, then loads the bitstream and uploads firmware through LiteX's serial boot protocol. It keeps the terminal attached to the console. `make compare` builds all four profiles and parses their Gowin reports. `make test` runs the host firmware tests, GPIO simulation, and profile generation without Gowin synthesis.

Each profile writes to `build/<profile>/`: `bitstream.fs`, `firmware/demo.elf`, `firmware/demo.bin`, `csr.csv`, `csr.json`, generated CSR and memory headers, BIOS outputs, Gowin reports, and `build-metadata.json`. Generated timing constraints include the 27 MHz board input and the 48 MHz system clock.

## Cable and switches

Seat the Tang Primer 20K core board in the standard Dock. Set DIP switch 1 down to enable the core board; when it is disabled the Dock's first two LEDs can remain lit and the FPGA will not run. Connect a data-capable USB-C cable to the Dock's JTAG/UART connector. The same connection supplies the programmer and serial port. Leave the Dock's other slide switches in their normal USB/JTAG/UART configuration.

Find its stable serial name with:

```sh
ls -l /dev/serial/by-id/
```

Pass the Dock's Sipeed JTAG Debugger UART path as `PORT`; do not guess a changing `/dev/ttyUSB*` number. On the reference computer, the FPGA UART is `/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0`. The other Sipeed debugger channel is `if00-port0`; `/dev/serial/by-id/usb-1a86_USB_Single_Serial_599D114570-if00` is a separate USB serial adapter, not the FPGA UART. The console runs at 115200 baud, 8 data bits, no parity, one stop bit. Run `make run` in an interactive terminal. It opens UART before it programs FPGA SRAM, so the BIOS serial prompt is caught and LiteX's loader can upload the firmware to the generated main RAM base.

The bitstream goes only to FPGA SRAM. The command does not write the core board's flash. Power cycling therefore restores the flash image that was already present before this project ran.

## Expected console

At startup, the serial console prints the board name, selected VexRiscv profile, clock, commands, and `GPIO demo ready`. On the tested Dock, S0–S3 map to LEDs 0–3 after 20 ms of stable input; LED 4 stays off in demo mode and LED 5 toggles every 500 ms. Enter:

```text
buttons
leds 0x21
demo
help
```

`buttons` reports synchronized and debounced masks. `leds` accepts hexadecimal masks from `00` through `3f` and enters manual mode only on valid input. `demo` restores button mirroring and the heartbeat. Empty, malformed, and overlong lines are handled without changing the LED mode or corrupting the next command.

Dock LED outputs are electrically active-low; the hardware GPIO CSR inverts them so firmware uses logical one for illuminated. The CSR uses Sipeed's logical LED0–LED5 order. The platform resource names run in the opposite direction, so the implementation requests resources 0 through 5 individually and maps them explicitly:

| Logical LED bit / silk label | Platform resource | Pin |
|---:|---:|---|
| 0 | 5 | C13 |
| 1 | 4 | A13 |
| 2 | 3 | N16 |
| 3 | 2 | N14 |
| 4 | 1 | L14 |
| 5 | 0 | L16 |

Buttons are also active-low and are inverted before the LiteX two-stage input synchronizer. S0–S3 use platform button resources 0–3 at T10 (LVCMOS33), T3, T2, and D7 (LVCMOS15). The upstream platform also lists C7 as resource 4, but on the tested Dock S4 asserts FPGA reset; the design intentionally does not request that pin as GPIO. The usable button CSR is therefore four bits wide. See [Sipeed's Dock six-LED pin table](https://wiki.sipeed.com/hardware/zh/tang/tang-primer-20k/examples/key_6leds_on.html) for the printed LED labels and board pins.

For Dock revision V3708, Sipeed documents an LED2/LED3 silkscreen error: use the logical pin mapping above rather than the printed LED2/LED3 markings when checking those two lights. See [Sipeed's Dock revision note](https://wiki.sipeed.com/hardware/zh/tang/tang-primer-20k/start.html).

## Troubleshooting

- If Gowin reports a host-ID mismatch together with `Could not create AF_NETLINK socket`, run the command from a normal user shell where the launcher can read the machine's network interface. Do not change the host MAC or the license file.
- If `make doctor` identifies a serial permission problem, add the user to `dialout` with `sudo usermod -aG dialout "$USER"`, log out and back in, reconnect the cable, and rerun the doctor.
- If `make doctor` finds USB devices but cannot access them, install `.tools/openfpgaloader/usr/lib/udev/rules.d/99-openfpgaloader.rules` into `/etc/udev/rules.d/`, reload udev, then reconnect the Dock. Add your account to `plugdev` and log out/in only if the doctor says your account is not already in that group. The doctor prints the exact commands.
- If the serial handshake fails, confirm DIP switch 1 is down, select the Dock's stable `/dev/serial/by-id/` path, close other serial monitors, and retry `make run`.
- If a temporary load fails, stop the run command, power-cycle the Dock, reconnect USB, run `make doctor`, then run `make run` again. The flash image remains untouched.
- If synthesis or timing fails, keep the raw files under `build/<profile>/gateware/` and use `make compare` to identify the failing profile and report. Do not remove the 48 MHz clock constraint to make the report pass.

## Later milestones

Continue with the same SoC and generated CSR workflow. First enable LiteDRAM and prove DDR3 initialization and memory-test reliability. Then add HDMI color bars or a text terminal before attempting a DDR-backed framebuffer. Add the Dock RMII PHY through LiteEth and verify link and packets. Finally, consider richer VexRiscv configurations and workload benchmarks. The upstream platform notes that HDMI DDC shares the Ethernet management pins, so a combined HDMI/Ethernet design must resolve that pin sharing explicitly.
