# VexRiscv/LiteX bring-up for the Tang Primer 20K Dock

## Summary

Create a reproducible project for the **Tang Primer 20K with the standard Dock**, using VexRiscv, LiteX, and the installed Gowin EDA.

The first milestone will run bare-metal C firmware that reads buttons, controls LEDs, and provides an interactive UART console. It will support three CPU presets, use internal block RAM, and load temporarily through JTAG and UART.

Reuse LiteX’s [existing board target](https://github.com/litex-hub/litex-boards/blob/master/litex_boards/targets/sipeed_tang_primer_20k.py) for clocks, reset, device selection, and pin constraints.

## Implementation

### Repository and tools

- Organize the repo into gateware, firmware, setup/build scripts, tests, and documentation. Ignore downloaded dependencies, tool installations, generated HDL, and build outputs.
- Keep upstream dependencies as local clones controlled by a committed lock manifest containing repository URLs and exact commits. Include LiteX, Migen, LiteX Boards, VexRiscv’s generated RTL package, and dependencies imported by the board target.
- Use a local Python 3.12 environment to avoid relying on this computer’s newer Python 3.14. Pin Python packages and record downloaded tool versions and checksums.
- Install portable xPack RISC-V GCC locally. Obtain openFPGALoader from Ubuntu packages, extracting it and any missing runtime dependencies locally without requiring root.
- Use the existing `gw_sh` launcher and license configuration. Allow a `GOWIN_SH` override for other computers.
- Add a dependency check that verifies Python imports, RV32 compilation, Gowin startup/license access, and programmer availability. Report USB or serial permission problems with specific corrective instructions.
- Use supplied VexRiscv RTL presets initially; Scala, Java, and a custom CPU generator are deferred.

### Gateware

Subclass the upstream board SoC with its automatic LED chaser, button peripheral, and LCD backlight disabled. Add explicitly named GPIO peripherals owned by this project.

Use a **48 MHz system clock**, **115200 baud UART**, LiteX BIOS, timer, and these memory allocations:

- BIOS ROM: 32 KiB.
- Firmware main RAM: 32 KiB.
- Working SRAM: 8 KiB.
- DDR3, Ethernet, HDMI, RGB LED, and SD card disabled initially.

Provide these profiles using the existing [LiteX VexRiscv variants](https://github.com/enjoy-digital/litex/blob/master/litex/soc/cores/cpu/vexriscv/core.py):

| Profile | LiteX variant | Purpose |
|---|---|---|
| `minimal` — default | `minimal` | RV32I baseline without caches |
| `lite` | `lite` | Multiply/divide and instruction cache |
| `standard` | `standard` | Multiply/divide and instruction/data caches |

Keep the GPIO interface, clock, and memory layout identical across profiles. Recompile the same firmware source for each profile using LiteX’s generated compiler settings.

Expose:

- `leds`: six output bits, with logical `1` meaning illuminated.
- `buttons`: five synchronized input bits, with logical `1` meaning pressed.

Request pins individually in **numeric resource order**; upstream lists LED resources 2 and 3 out of order. Preserve its mixed button voltage standards, confirm LED polarity against Sipeed’s schematic, and document resource indices alongside physical board labels. All five buttons remain GPIO inputs. [Upstream Dock constraints](https://github.com/litex-hub/litex-boards/blob/master/litex_boards/platforms/sipeed_tang_primer_20k.py)

Export generated CSR headers and memory descriptions for firmware rather than hardcoding peripheral addresses.

### Firmware

Use LiteX’s startup code, generated headers, and firmware build conventions.

- On startup, print the board name, CPU profile, clock, and available commands.
- Default demo: buttons 0–4 control LEDs 0–4; LED 5 provides a heartbeat.
- Sample buttons every millisecond and require 20 ms of stable input before accepting a change.
- Provide `help`, `buttons`, `leds <hex-mask>`, and `demo` commands. Setting an LED mask enters manual mode; `demo` restores button mirroring and heartbeat.
- Keep UART handling nonblocking so typing cannot stop GPIO updates. Handle malformed commands and oversized lines without corrupting memory.
- Check firmware size and reserve stack space at link time.

## Workflow and acceptance

Provide these project commands:

| Command | Behavior |
|---|---|
| `make setup` | Create the local environment and fetch locked dependencies |
| `make doctor` | Check build tools and report hardware access prerequisites |
| `make build PROFILE=minimal` | Build BIOS, bitstream, and matching demo firmware |
| `make load PROFILE=minimal` | Program the selected bitstream into FPGA SRAM |
| `make run PROFILE=minimal PORT=…` | Open UART, load the bitstream, upload firmware, and enter the console |
| `make compare` | Build all profiles and summarize resource and timing reports |
| `make test` | Run automated checks without attached hardware |

`make run` must open UART **before** programming so it catches the BIOS serial boot handshake. Use openFPGALoader through the upstream platform’s programmer support, then LiteX’s serial loader to place firmware in main RAM. Require an explicit serial port, preferably its stable `/dev/serial/by-id/` path.

Store each profile’s outputs separately: bitstream, firmware ELF/binary, CSR descriptions, build metadata, resource utilization, and timing reports.

Acceptance checks:

- Generate all three SoCs and verify GPIO ordering, widths, reset values, synchronized inputs, and disabled DDR3.
- Test debounce behavior and UART command handling, including simultaneous button presses and invalid input.
- Compile BIOS and firmware for every profile; detect memory overflow and stale profile artifacts.
- Complete Gowin synthesis and place-and-route for all profiles, fit within device resources, and meet the constrained 48 MHz timing.
- On hardware, verify every LED and button, heartbeat, console commands, and repeated loading for each profile.
- Power cycling must restore the existing flash image.
- Mark hardware checks pending when the Dock is unavailable; do not report them as passed.

Document setup, cabling, board switches, the loading sequence, expected console output, and troubleshooting. The initial comparison measures resource usage and timing, not application performance.

## Defaults and later milestones

The agreed defaults are the standard Dock, GPIO plus UART, three supplied CPU presets, block RAM, and temporary SRAM loading. License files remain outside Git.

Keep future peripherals within the same SoC and generated CSR workflow. Document this sequence without implementing those peripherals in the first milestone:

1. **DDR3:** enable LiteDRAM and establish initialization plus memory-test reliability.
2. **HDMI:** begin with color bars or a text terminal before a DDR-backed framebuffer.
3. **Ethernet:** enable the Dock’s RMII PHY through LiteEth, then validate link and packet exchange.
4. **Higher CPU performance:** add richer VexRiscv configurations and workload benchmarks.

The upstream target already provides DDR3, HDMI terminal, and Ethernet integration points. Its HDMI DDC pins overlap Ethernet management pins, so later combined designs must explicitly account for that sharing. [Board target](https://github.com/litex-hub/litex-boards/blob/master/litex_boards/targets/sipeed_tang_primer_20k.py), [Dock pin definitions](https://github.com/litex-hub/litex-boards/blob/master/litex_boards/platforms/sipeed_tang_primer_20k.py)
