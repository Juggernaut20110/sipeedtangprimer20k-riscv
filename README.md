# Tang Primer 20K Dock VexRiscv bring-up

This project builds a LiteX VexRiscv SoC for the Sipeed Tang Primer 20K with the standard Dock. The first milestone uses a 48 MHz clock, 115200 baud UART, BIOS, 32 KiB main RAM, 8 KiB working SRAM, six LEDs, and five buttons. The firmware mirrors debounced button inputs to LEDs 0–4 and blinks LED 5; the UART console can switch to a manual LED mask.

Start with `make setup`, then `make doctor`, `make build PROFILE=minimal`, and `make run PROFILE=minimal PORT=/dev/serial/by-id/<device>`. `make run` opens the serial port before loading the SRAM bitstream, then uses LiteX's serial loader to upload the matching firmware. The full wiring and recovery steps are in [docs/bringup.md](docs/bringup.md). `make test` runs the firmware logic tests and generates each SoC without invoking the vendor tools. `make compare` performs vendor builds for all profiles and summarizes report metrics.

The pinned upstream repositories and portable tool archive checksums are in [dependencies.lock.json](dependencies.lock.json). Setup stores downloads and clones under ignored `.tools/` and `.deps/`; Gowin stays installed outside the repository, and its license is never copied here. Set `GOWIN_SH=/path/to/gw_sh` to select another Gowin launcher. The default on the handoff computer is `/home/user/.local/bin/gw_sh`.

## CoreMark benchmark

Build all three SoC profiles and both firmware variants, then measure one selected profile over the Dock UART. The benchmark programs FPGA SRAM only and leaves onboard flash untouched:

```sh
make doctor
make benchmark-build
make benchmark-run PROFILE=standard PORT=/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
make benchmark-report
```

`PROFILE` may be `minimal`, `lite`, or `standard` and defaults to `standard` for `benchmark-run`. Confirm the Dock UART path with `make doctor`; the runner does not guess or fall back to another serial port. It runs validation first, then three scored performance repetitions if validation succeeds. Build details, captured UART evidence, scores, and any incomplete measurement status are documented in [docs/performance.md](docs/performance.md), with machine-readable results in [docs/performance/results.json](docs/performance/results.json). Scores are CoreMark iterations/second and CoreMark/MHz, calculated from 64-bit timer ticks at the 48 MHz operating clock.

Profiles are `minimal` (default), `lite`, and `standard`. Each has an isolated output directory under `build/<profile>/`. Build metadata, generated CSR and memory descriptions, firmware ELF and binary, raw Gowin reports, and the verification record are stored with those outputs or in `docs/verification.md`.

DDR3, HDMI, Ethernet, flash boot, and custom Scala CPU generation are later milestones. Their planned integration order and pin-sharing note are in [docs/bringup.md](docs/bringup.md).
