# Tang Primer 20K Dock VexRiscv bring-up

This project builds a LiteX VexRiscv SoC for the Sipeed Tang Primer 20K with the standard Dock. The first milestone uses a 48 MHz clock, 115200 baud UART, BIOS, 32 KiB main RAM, 8 KiB working SRAM, six LEDs, and five buttons. The firmware mirrors debounced button inputs to LEDs 0–4 and blinks LED 5; the UART console can switch to a manual LED mask.

Start with `make setup`, then `make doctor`, `make build PROFILE=minimal`, and `make run PROFILE=minimal PORT=/dev/serial/by-id/<device>`. `make run` opens the serial port before loading the SRAM bitstream, then uses LiteX's serial loader to upload the matching firmware. The full wiring and recovery steps are in [docs/bringup.md](docs/bringup.md). `make test` runs the firmware logic tests and generates each SoC without invoking the vendor tools. `make compare` performs vendor builds for all profiles and summarizes report metrics.

The pinned upstream repositories and portable tool archive checksums are in [dependencies.lock.json](dependencies.lock.json). Setup stores downloads and clones under ignored `.tools/` and `.deps/`; Gowin stays installed outside the repository, and its license is never copied here. Set `GOWIN_SH=/path/to/gw_sh` to select another Gowin launcher. The default on the handoff computer is `/home/user/.local/bin/gw_sh`.

Profiles are `minimal` (default), `lite`, and `standard`. Each has an isolated output directory under `build/<profile>/`. Build metadata, generated CSR and memory descriptions, firmware ELF and binary, raw Gowin reports, and the verification record are stored with those outputs or in `docs/verification.md`.

DDR3, HDMI, Ethernet, flash boot, and custom Scala CPU generation are later milestones. Their planned integration order and pin-sharing note are in [docs/bringup.md](docs/bringup.md).
