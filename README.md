# Tang Primer 20K Dock VexRiscv bring-up

This project builds a LiteX VexRiscv SoC for the Sipeed Tang Primer 20K with the standard Dock. The first milestone uses a 48 MHz clock, 115200 baud UART, BIOS, 32 KiB main RAM, 8 KiB working SRAM, six LEDs, and five buttons. The firmware mirrors debounced button inputs to LEDs 0–4 and blinks LED 5; the UART console can switch to a manual LED mask.

Start with `make setup`, then `make doctor`, `make build PROFILE=minimal`, and `make run PROFILE=minimal PORT=/dev/serial/by-id/<device>`. `make run` opens the serial port before loading the SRAM bitstream, then uses LiteX's serial loader to upload the matching firmware. The full wiring and recovery steps are in [docs/bringup.md](docs/bringup.md). `make test` runs the firmware logic tests and generates each SoC without invoking the vendor tools. `make compare` performs vendor builds for all profiles and summarizes report metrics.

The pinned upstream repositories and portable tool archive checksums are in [dependencies.lock.json](dependencies.lock.json). Setup stores downloads and clones under ignored `.tools/` and `.deps/`; Gowin stays installed outside the repository, and its license is never copied here. Set `GOWIN_SH=/path/to/gw_sh` to select another Gowin launcher. The default on the handoff computer is `/home/user/.local/bin/gw_sh`.

## CoreMark benchmark

See the [CoreMark results summary](docs/coremark-summary.md) for scores across all profiles and successful measurement history.

Build the SoC profiles and both firmware variants, then measure one selected profile or all profiles over the Dock UART. On-chip RAM is the default. `MEMORY=ddr3` selects the board's 256 MiB DDR3 path. Benchmark runs program FPGA SRAM only and leave onboard flash untouched:

```sh
make doctor
make benchmark-build
make benchmark-run PROFILE=standard PORT=/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
make benchmark-report
```

`PROFILE` may be `minimal`, `lite`, `standard`, `performance`, `linux`, or `ALL` (`all` also works), and defaults to `standard` for `benchmark-run`. Add `MEMORY=ddr3` to build and measure using DDR3. To validate and measure all registered profiles in one invocation:

```sh
make benchmark-run PROFILE=ALL PORT=/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
make benchmark-report
```

`ALL` runs the registered CPU profiles sequentially on the same Dock UART. Each profile runs validation first, then three scored performance repetitions if validation succeeds. Captures remain separate by profile and memory mode. A timeout or disconnect that leaves firmware completion uncertain stops the batch before programming another profile. Ctrl-C stops the batch and closes the UART.

Confirm the Dock UART path with `make doctor`; the runner does not guess or fall back to another serial port. Build details, captured UART evidence, scores, and any incomplete measurement status are documented in [docs/performance.md](docs/performance.md), with machine-readable results in [docs/performance/results.json](docs/performance/results.json). Scores are CoreMark iterations/second and CoreMark/MHz, calculated from 64-bit timer ticks at the 48 MHz operating clock.

The registered profiles are `minimal` (default), `lite`, `standard`, `performance`, and `linux`. The `performance` profile uses the measured `dynamic_target` VexRiscv configuration at 48 MHz; its selection evidence is in `cpu-profile-selection.json`. The `linux` profile uses LiteX's pinned Linux-capable VexRiscv variant (RV32IMA, MMU, supervisor, atomics) with the existing bare-metal demo, DDR diagnostics, and CoreMark flows. Linux kernel and OS boot are deferred. On-chip outputs remain under `build/<profile>/`; DDR3 outputs use `build/ddr3/<profile>/`.

Maximum-performance tuning is kept outside the public profile list until both memory modes have independently qualified winners. Build and measure the bounded candidate matrix with:

```sh
make maxperf-build MEMORY=onchip
make maxperf-run MEMORY=onchip PORT=<verified-Dock-UART>
make maxperf-build MEMORY=ddr3
make maxperf-run MEMORY=ddr3 PORT=<verified-Dock-UART>
```

The matrix varies cache sizes and RV32IM versus RV32IMC at a fixed 48 MHz clock. A public `maxperf` profile is enabled only when each selected candidate strictly beats a fresh `performance` baseline and its DDR evidence qualifies.

On 2026-10-02, the current-source `PROFILE=ALL MEMORY=onchip` batch passed validation and three scored runs for all five public profiles. The bounded matrix has outcomes for all eight on-chip candidates and all 18 DDR3 build candidates; candidate 8's earlier on-chip capture measured +0.398%, while DDR3 candidates were not scored because BIOS Memtest reproduces persistent memory corruption. Those maxperf captures predate the final no-D-cache SRAM-check correction and remain supplemental; `maxperf` stays private and the public list remains five profiles. The FPGA was left in safe idle after an explicit reset. See the [combined candidate matrix and evidence](docs/performance/maxperf-evaluation/20261001-combined-matrix-summary.json), [CoreMark report](docs/performance.md), [DDR3 report](docs/ddr3/report.md), and [final recovery capture](docs/performance/maxperf-evaluation/recovery/20261002T004311Z-final-onchip/recovery.json).

Build and run the destructive DDR3 checks with `make ddr-test-build PROFILE=ALL`, then `make ddr-test-run PROFILE=ALL PORT=<verified-UART>` and `make ddr-test-report`. The defaults are ten fresh SRAM reconfigurations and 1,800 seconds of stress per profile. See [the DDR3 evidence report](docs/ddr3/report.md).

`make cpu-candidate-build` generates and routes the two standard-derived branch-prediction candidates. `make cpu-candidate-run PORT=<verified-UART>` measures a fresh standard baseline and viable candidates. The public `performance` CPU profile is added only after a candidate beats that baseline at 48 MHz. HDMI, Ethernet, and flash boot remain outside the current scope; see [docs/bringup.md](docs/bringup.md) for existing pin-sharing notes.
