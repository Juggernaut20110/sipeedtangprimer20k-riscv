# Implementation handoff: Tang Primer 20K VexRiscv/LiteX

## Task for the implementing agent

Implement `PLAN.md` in this repository. Read that document before making changes; it is the authoritative scope and acceptance specification. This handoff supplies execution guidance and implementation defaults. If the documents conflict, follow `PLAN.md` and report the conflict.

The user has selected:

- Sipeed Tang Primer 20K with the **standard Dock**.
- Bare-metal C firmware with GPIO and an interactive UART console.
- Three supplied VexRiscv presets: `minimal`, `lite`, and `standard`.
- Internal block RAM for the initial milestone.
- Temporary FPGA SRAM programming and UART firmware upload.

Carry the implementation through dependency setup, code, automated verification, and real Gowin builds. Do not stop after creating a scaffold or documenting commands that have not been exercised. Resolve routine implementation choices yourself using this handoff and upstream examples. Ask the user only when required information cannot be discovered or an actual approval boundary prevents necessary work.

Do not implement the later DDR3, HDMI, Ethernet, Linux, persistent flash boot, or custom Scala CPU generator milestones. Document their integration path. Do not spawn additional agents unless the user explicitly requests delegation.

## Starting state and environment

Repository: `/home/user/git/fpga/sipeedtangprimer20k-riscv`.

At handoff creation, the repository has no commits or implementation files. It contains `PLAN.md` and this document. Preserve both documents. Check the current working tree and applicable `AGENTS.md` instructions before starting; preserve any work added since this handoff.

Previously inspected environment; recheck it rather than assuming it is unchanged:

| Item | Observed state |
|---|---|
| Host | Ubuntu 26.04, x86-64 |
| System Python | 3.14.4; use the planned local Python 3.12 environment |
| Gowin EDA | 1.9.12.04 installed for this user |
| Gowin CLI wrapper | `/home/user/.local/bin/gw_sh` |
| Gowin installation | `/home/user/.local/opt/Gowin_V1.9.12.04_linux` |
| License | Already configured; node locked to `50:9a:4c:58:c1:df`, expires September 4, 2027 |
| Python FPGA dependencies | LiteX, Migen, LiteX Boards, and LiteDRAM were absent |
| Other missing tools | RISC-V cross compiler and openFPGALoader |
| Existing tools | Git, Make, host GCC, CMake, pkg-config, dpkg-deb, Python venv support |
| Hardware | No USB serial device was visible during planning; connection is not confirmed |

The Gowin wrapper contains library compatibility settings needed on this Ubuntu installation. Invoke the wrapper, not the raw executable. Support `GOWIN_SH` as a path to one executable; do not treat it as an arbitrary shell command. Ensure LiteX's generated build process actually resolves and invokes the selected launcher.

The restricted execution sandbox previously prevented AF_NETLINK access, which caused Gowin to report a host-ID mismatch even though the license matches the physical Ethernet interface. Running the same CLI with approved execution outside that sandbox succeeded. Diagnose this distinction before declaring the license invalid. Never alter the machine MAC or bypass license verification.

Network downloads and execution outside the sandbox may require tool approval. This account previously required interactive sudo authentication. Keep project dependencies local as planned; describe any genuinely required USB access changes separately. Do not copy the license into Git or print its signed contents.

## Suggested project structure

Use this structure unless an upstream build requirement needs a small adjustment:

```text
PLAN.md
HANDOFF.md
README.md
Makefile
dependencies.lock.json
gateware/             # board SoC and profile definitions
firmware/             # C demo, linker script, firmware Makefile
scripts/              # bootstrap, doctor, build/run, report helpers
tests/                # meaningful simulation and host firmware tests
docs/                 # hardware bring-up, verification, future milestones
.venv/                # ignored
.deps/                # ignored upstream clones
.tools/               # ignored portable tools and extracted packages
build/<profile>/      # ignored, isolated generated outputs
```

Use an exact dependency manifest and pinned Python package requirements. Record the selected upstream commits, archive versions, and verified checksums in tracked files. Bootstrap must use those pins on subsequent runs, with no automatic updates to moving branches or `latest` URLs. Keep the setup idempotent and fail clearly on interrupted downloads or checksum failures.

Obtain Python 3.12 and xPack RISC-V GCC from official portable distributions. Extract Ubuntu openFPGALoader packages and missing runtime libraries locally, verifying package hashes against repository metadata. Apply any local library path only to the programmer process. Verify RV32 compilation and tool execution before building gateware.

## Implementation sequence

### 1. Establish and lock a working tool environment

- Inspect current official upstream sources and the dependency definitions used by the selected LiteX revision.
- Install the minimal dependency closure needed to import the board target, build BIOS, generate VexRiscv RTL, and run tests. Disabled hardware may still have Python import dependencies.
- Include the supplied VexRiscv RTL package; do not download an unrelated Verilog core or add Java/Scala for the initial presets.
- Implement `make setup` and `make doctor`, including tool versions, Python imports, a small RV32 compilation check, Gowin CLI/license startup, and programmer runtime checks.
- Separate build prerequisites from connected-hardware checks. Absence of a board must not prevent generation, firmware compilation, or synthesis.

### 2. Generate the smallest working SoC

- Subclass the upstream Tang Primer 20K `BaseSoC`; keep its standard Dock platform, clock/reset implementation, FPGA device settings, and dual-purpose-pin options.
- Explicitly disable the upstream LED chaser, buttons, and LCD backlight before adding project GPIO. Set nonzero integrated main RAM so the upstream target does not instantiate DDR3.
- Use the plan's 48 MHz clock, 115200 baud UART, 32 KiB BIOS ROM, 32 KiB main RAM, and 8 KiB working SRAM. Include LiteX BIOS and timer support.
- Request LEDs individually with indices 0 through 5 and buttons with indices 0 through 4, then concatenate in that order. Never depend on `request_all()` ordering for this board.
- Preserve the upstream pin voltage standards. Confirm LED polarity from Sipeed's schematic and implement logical `1 = illuminated`; normalize active-low buttons to `1 = pressed`.
- Give the GPIO peripherals explicit names `leds` and `buttons` to make CSR generation predictable. Use LiteX `GPIOIn` or an equivalent two-stage synchronizer for asynchronous inputs. Initialize all logical LED outputs to off.
- Generate CSR and linker information before compiling the application. Use generated definitions rather than copying addresses from examples.
- Bring up `minimal` first, then add the other two profiles without changing the GPIO contract or memory layout.

### 3. Build the GPIO firmware

Adapt the pinned LiteX standalone demo's Makefile, linker conventions, and VexRiscv startup code. Reuse LiteX software libraries and generated compiler flags; do not assume a compiler executable prefix or ISA flags independent of the profile.

Implementation defaults that complete the plan's firmware behavior:

- Sample synchronized buttons at 1 ms intervals using the LiteX hardware timer. Accept each button's change after 20 consecutive stable samples. Polling is sufficient; interrupts are not required.
- In demo mode, mirror the five debounced button states onto LEDs 0–4. Toggle LED 5 every 500 ms.
- `buttons` reports both the synchronized input mask and debounced mask.
- `leds <hex-mask>` accepts masks from `0x00` through `0x3f`, with an optional `0x` prefix. Enter manual mode only after successful validation; manual mode controls all six LEDs and suspends heartbeat output.
- `demo` restores button mirroring and heartbeat. `help` lists these commands and their syntax.
- Use a bounded 64-byte command buffer. Reject an overlong line and discard it through the next newline; reject invalid commands or masks without changing the current mode or LED state. Support LF and CRLF terminals.
- Keep button sampling and heartbeat active while waiting for UART input. Avoid blocking reads and long output loops.
- Reserve at least 2 KiB for the stack in working SRAM and enforce a linker assertion against data/BSS overlap. Check both the main-RAM image size and working-SRAM usage.
- Print the board, profile, clock, and help information on startup. Include a stable `GPIO demo ready` line for serial smoke checks.

Extract debounce and command-state logic into small C functions that can be tested on the host while using the same implementation in firmware.

### 4. Complete build, load, run, and reporting commands

Implement every command specified in `PLAN.md`. `minimal` is the default profile; unknown profiles must fail before starting a build.

- Isolate profile outputs, generated headers, firmware objects, and metadata. Changing profile must not reuse another profile's generated includes or firmware.
- `make build` produces BIOS, a real Gowin SRAM-loadable bitstream, and the matching application ELF/binary. Include tool and dependency versions in build metadata.
- `make load` loads FPGA SRAM only through the upstream platform's openFPGALoader programmer support.
- `make run` requires `PORT`, opens UART before programming, catches the BIOS serial boot handshake, uploads the matching application, and presents the interactive console. Reuse LiteX's serial-loader implementation rather than rewriting its protocol. Close the serial connection and stop background activity on error or Ctrl-C.
- Do not introduce flash programming commands or pass persistent-flash options to the programmer.
- `make compare` builds all three profiles and extracts actual LUT, flip-flop, block-RAM, DSP, and timing results from vendor reports. Preserve the raw reports. Missing or unrecognized metrics must appear as unavailable or fail the relevant check, never as zero or a guessed value.
- Treat nonzero tool exits, firmware overflow, device-capacity violations, and failure to meet constrained 48 MHz timing as failures. Report unconstrained paths for review; do not hide timing problems by removing required constraints.

### 5. Verify and document the result

Run the plan's acceptance checks, fixing failures as they arise. Add documentation for a clean setup, each Make command, device selection, board power/cabling/switches, recovery after a failed load, and future peripheral milestones.

Create a verification record with a separate status and evidence for each CPU profile: generation, BIOS/application compilation, synthesis/place-and-route, and physical board testing.

## Validation requirements

- Simulate GPIO reset values, numeric pin ordering, input normalization, and synchronization behavior. Inspect generated constraints for the intended pins and voltage standards.
- Test independent and simultaneous button changes, bounce shorter than 20 ms, stable press/release, and continuous UART activity.
- Host-test valid and invalid LED masks, manual/demo transitions, empty and unknown commands, CRLF handling, and overlong-line recovery using the production firmware logic.
- Generate and compile all three profiles. Check image bounds, stack reservation, and profile isolation.
- Run real Gowin synthesis and place-and-route for all three profiles. Save and summarize the actual resource and timing reports.
- When hardware is connected and accessible, verify all six LEDs, all five buttons, heartbeat, UART commands, and repeated temporary loading for each profile. Verify power cycling restores the preexisting flash boot image.

If hardware is unavailable, complete all work that does not require it and mark physical checks **pending** with exact instructions for running them. If permissions, downloads, or licensing block a build, continue independent work and record the concrete blocker and commands that reproduce it. Do not call an unperformed build or test successful.

## Useful upstream references

- [Tang Primer 20K target](https://github.com/litex-hub/litex-boards/blob/master/litex_boards/targets/sipeed_tang_primer_20k.py)
- [Standard Dock platform and pin constraints](https://github.com/litex-hub/litex-boards/blob/master/litex_boards/platforms/sipeed_tang_primer_20k.py)
- [LiteX VexRiscv integration and presets](https://github.com/enjoy-digital/litex/blob/master/litex/soc/cores/cpu/vexriscv/core.py)
- [Supplied VexRiscv RTL package](https://github.com/litex-hub/pythondata-cpu-vexriscv)
- [LiteX standalone software demo](https://github.com/enjoy-digital/litex/tree/master/litex/soc/software/demo)
- [LiteX serial terminal and boot loader](https://github.com/enjoy-digital/litex/blob/master/litex/tools/litex_term.py)
- [LiteX GPIO implementation](https://github.com/enjoy-digital/litex/blob/master/litex/soc/cores/gpio.py)
- [Sipeed board documentation](https://en.wiki.sipeed.com/hardware/en/tang/tang-primer-20k/primer-20k.html)
- [Sipeed examples and hardware references](https://github.com/sipeed/TangPrimer-20K-example)

Read the corresponding files at the revisions selected for the dependency lock. APIs on the linked development branches may change.

## Final delivery

Leave a reviewable working tree containing the implementation, dependency pins, tests, setup instructions, and verification record. Do not push, publish, or commit unless the user requests it.

In the final response, provide:

1. What was implemented and where the quick-start instructions are.
2. The exact setup/build/run commands for this computer.
3. The measured resource and timing results for each profile, when available.
4. Which automated and hardware checks passed, failed, or remain pending.
5. Any remaining blocker with the smallest concrete next action needed.

Keep claims grounded in captured tool results. A working scaffold is not equivalent to a synthesized and tested SoC.
