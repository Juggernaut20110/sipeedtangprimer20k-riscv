# Handoff: measure VexRiscv performance on the Tang Primer 20K Dock

## Instructions for Luna extra high

Implement this document in the existing repository. Complete the builds, board execution, UART capture, and performance report. Proceed with routine implementation decisions without requesting confirmation. If hardware, licensing, or permissions prevent execution, preserve the completed work and report the exact blocker; never substitute estimated benchmark results.

Repository: `/home/user/git/fpga/sipeedtangprimer20k-riscv`.

### Required outcome

1. Build and verify the `minimal`, `lite`, and `standard` configurations, including their benchmark firmware.
2. Program **one selected configuration**, default `standard`, onto the attached Sipeed Tang Primer 20K with Dock using SRAM programming.
3. Execute a validated CPU benchmark and capture its results over the Dock UART.
4. Write `docs/performance.md` using the measured results, with machine-readable results and UART evidence.

Use **CoreMark**. The user permits a benchmark other than DMIPS. Report CoreMark iterations/second and CoreMark/MHz; do not label them DMIPS or convert them to Dhrystone MIPS. Do not automatically benchmark all three configurations: build all three and measure only the selected one. Keep `PROFILE` selectable so additional profiles can be measured later.

## Read first and preserve the existing implementation

Read `README.md`, `docs/verification.md`, `docs/bringup.md`, `Makefile`, `gateware/soc.py`, `scripts/{project,build,run,load,compare,validate}.py`, and `firmware/{Makefile,linker.ld,main.c}`. Read applicable `AGENTS.md` instructions if present. Inspect the pinned LiteX serial loader and timer implementation before extending them.

`PLAN.md` and `HANDOFF.md` describe the earlier GPIO project. Prefer the implemented code and verification evidence when older documents differ. In particular, the Dock has **four usable GPIO buttons, S0–S3**; S4 is FPGA reset. Preserve the corrected LED mapping, input synchronization, and PLL reset fix.

Current configuration:

| Item | Existing implementation |
|---|---|
| CPU profiles | VexRiscv `minimal`, `lite`, `standard` |
| System / CPU clock | 48,000,000 Hz for all profiles |
| UART | 115200 baud |
| Memory | 32 KiB BIOS ROM, 32 KiB main RAM, 8 KiB SRAM |
| Firmware stack | 2,048 bytes reserved in SRAM |
| Timer | LiteX timer0 with a 64-bit hardware uptime counter |
| Programming | openFPGALoader through the LiteX platform, FPGA SRAM only |

The existing report records successful synthesis and place-and-route for all profiles. `standard` uses **46 of 46 BSRAM blocks**. Keep the existing clock, memory map, CPU variants, and peripherals. Reuse the timer; avoid additional hardware. HDMI, Ethernet, DDR, flash programming, and clock tuning are outside this task.

The prior timing estimates were 70.002 MHz (`minimal`), 55.471 MHz (`lite`), and 63.286 MHz (`standard`). These are place-and-route estimates, **not the operating frequencies**. Re-read current reports for the final document; measure the benchmark at 48 MHz.

## 1. Add a reproducible benchmark dependency and firmware

Use the official [EEMBC CoreMark repository](https://github.com/eembc/coremark) and its [run and reporting rules](https://github.com/eembc/coremark/blob/main/README.md). Pin an exact upstream commit in the dependency lock/setup process. Record its hash, preserve its license and notices, and verify the algorithm sources against that commit. Reuse installed tools and pinned LiteX dependencies; avoid unrelated upgrades.

Create a separate benchmark application and portable CoreMark adapter. Preserve the existing GPIO application and its `make build`, `make load`, and `make run` behavior. Suitable locations are `firmware/benchmark/` and `scripts/benchmark.py`, but follow the repository's conventions.

Benchmark requirements:

- Keep the upstream algorithm sources unchanged; implement platform support in the permitted port files and separate integration code.
- Use a single execution context, static allocation, and a 2,000-byte benchmark data size. No operating system is needed.
- Use performance seeds `0, 0, 0x66` and also execute the official validation seeds `0x3415, 0x3415, 0x66`.
- Use the same optimization settings for every algorithm source and profile, initially `-O2`, without LTO or profile-guided optimization. Derive each profile's supported ISA and ABI from the existing generated build settings. Record complete compiler flags, including any differing ISA flags.
- Use the existing startup, linker layout, UART library, and generated headers. Do not hard-code CSR addresses or assume all profiles support the same CPU cycle-counter instruction.
- Prove that each firmware image fits main RAM and that data, BSS, and the reserved stack fit SRAM. Retain linker assertions. Report section sizes and investigate stack requirements; do not silently reduce the benchmark workload to fit.
- Keep benchmark images separate from `demo.bin`. A straightforward implementation uses separate performance-seed and validation-seed binaries for each profile, both built from the same upstream sources.

Extend `scripts/build.py` fingerprinting or add equivalent benchmark metadata covering the upstream commit, port sources, build scripts, linker script, compiler settings, and generated profile configuration. A changed source or flag must invalidate its firmware cache. Associate each image with its matching profile and bitstream using hashes and metadata.

## 2. Measure time correctly

Use the existing generated timer uptime accessors. Confirm their names in the generated CSR header; the current timer exposes `timer0_uptime_latch_write(1)` followed by `timer0_uptime_cycles_read()`. These represent **system-clock ticks**, not the timer0 reload countdown. Read the latched 64-bit value and preserve 64-bit arithmetic throughout subtraction and reporting.

Use the generated system clock constant and verify it equals 48,000,000 Hz for this task. Implement CoreMark's timing port with correct tick and seconds types. Arrange UART output outside the timed section, including measurement metadata. Disable interrupts during the benchmark as appropriate for this bare-metal application; do not service GPIO or print progress inside the timed workload.

Use upstream automatic iteration calibration, or an equivalent permitted port configuration, so **each accepted performance repetition runs for at least 10 seconds**. Preserve upstream CRC verification and required workload settings. Capture calibration separately from scored output; do not include upload time, boot time, calibration time, or UART transfer time in the score.

Capture raw elapsed ticks, iterations, clock frequency, seeds, data size, context count, CRC output, and validation status. Include recognizable start/end markers and profile/build identity through the port or integration layer. Let the host parse the official result output for information not available to port hooks; do not modify algorithms merely to add telemetry.

Compute independently on the host:

```text
elapsed_seconds = elapsed_ticks / clock_hz
CoreMark = iterations * clock_hz / elapsed_ticks
CoreMark_per_MHz = iterations * 1_000_000 / elapsed_ticks
```

Cross-check against upstream output within its printed precision. Reject zero ticks, incomplete output, invalid CRCs, inconsistent metadata, or scored repetitions shorter than 10 seconds. A successful firmware upload alone is not benchmark success.

## 3. Add commands to build, capture, and report

Provide documented commands with these semantics; the target names below are preferred:

```sh
make doctor
make benchmark-build
make benchmark-run PROFILE=standard PORT=/dev/serial/by-id/<verified-Dock-UART>
make benchmark-report
```

- `benchmark-build`: generate/build all three SoCs, complete synthesis and place-and-route, and compile performance and validation firmware for every profile. Correctly reuse an unchanged verified bitstream if available; confirm current artifacts and metadata. Produce a combined build/resource/timing summary.
- `benchmark-run`: verify the selected profile's artifacts, program that configuration, upload benchmark firmware, run one validation-seed execution and **three scored performance repetitions**, and save all UART evidence and parsed results. Only this selected profile is programmed. Reprogramming the same bitstream for separate firmware runs is acceptable; document the reset and calibration procedure.
- `benchmark-report`: generate the performance document from actual captured results and build reports. Validate its inputs and preserve failure records. It must not create plausible-looking scores when results are absent.

Integrate through the existing Python environment and `scripts/project.py` tool-path handling. The existing Gowin wrapper and license setup already work in the verified environment; preserve them. If sandboxing blocks USB access or Gowin's license host identification, use the environment's approval mechanism for the specific authorized operation and report any rejection accurately.

## 4. Use the attached board and capture UART automatically

Discover the currently attached JTAG and serial devices; confirm the Dock UART instead of selecting the first USB serial device. The previously working alias was:

```text
/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
```

The previously present QinHeng adapter (`usb-1a86_USB_Single_Serial_599D114570-if00`) was **not** the FPGA UART. Treat these paths as discovery evidence, not a guarantee they remain available. Support an explicit `PORT` and record the actual selected device. Do not silently fall back to another port.

Adapt the working sequence in `scripts/run.py`: open UART and begin watching the BIOS handshake **before programming**, program through the existing platform programmer, and use LiteX's official serial upload protocol with the generated main-RAM base. Inspect the pinned `LiteXTerm` implementation and reuse its protocol handling. The automated runner must operate without an interactive terminal or keyboard reader and have exactly one owner reading the serial stream.

Capture startup, upload, benchmark, and completion output to a raw log. Handle split lines and serial decode errors without discarding evidence. Use bounded handshake and execution timeouts, configurable for slow calibration; allow at least 300 seconds per trial initially. Always stop reader threads and close serial resources on success, error, timeout, and interruption. Return a failing exit code for invalid or incomplete runs. Do not reset the board while a benchmark is running.

Record reset strategy and whether upstream calibration warms caches. Keep that procedure consistent between repetitions. State the code/data memory locations and relevant cache settings from the generated CPU configuration. SRAM programming may replace the currently running design; leave onboard flash untouched.

## 5. Produce the performance document and evidence

Write `docs/performance.md`. Preserve full build/session artifacts under `build/benchmarks/<UTC-session>/` and also retain compact, reviewable evidence in the repository: for example `docs/performance/results.json` and per-trial UART logs in `docs/performance/`. Include artifact hashes in the JSON. Do not rely solely on ignored build files for evidence readers need to inspect.

The document must contain:

1. Board/device, measurement date, selected profile, actual 48 MHz operating clock, UART device, tool versions, repository revision and dirty-state/source fingerprint, upstream benchmark commit, compiler flags, memory placement, and cache configuration.
2. A table for all three profiles showing successful builds, resource usage, timing constraint/slack and estimated Fmax, firmware sizes, and hardware benchmark status. Mark the two unselected profiles **not measured**; do not infer their scores from LUT count or Fmax.
3. A validation summary and one row per scored repetition: iterations, elapsed ticks/seconds, CoreMark, CoreMark/MHz, and validation result. Present mean, minimum, maximum, and spread across the three performance repetitions. Keep validation-seed output separate from scored performance output.
4. Links to the captured logs, machine-readable results, and relevant build reports, followed by exact reproduction commands.
5. Brief practical interpretation and limitations: this measures this workload in the current on-chip-memory SoC; it does not measure HDMI, Ethernet, external-memory performance, or establish a performance ranking for unmeasured cores. Explain that estimated Fmax differs from the operating clock.

Use a results schema containing session identity, profile, clock, seeds, iterations, elapsed ticks, validation/CRC evidence, tool/source identities, bitstream and firmware hashes, resource/timing data, and explicit success/failure status. Avoid rounding raw counters. Failed captures must remain visibly failed and must not enter score aggregates.

## 6. Verify and finish

- Run existing validation (`make test`) after integration and resolve regressions.
- Add focused host checks for result parsing: a valid capture, a failed CRC, an incomplete capture, a short run, a profile/clock mismatch, and a counter value exceeding 32 bits. Exercise runner cleanup/timeouts without requiring FPGA hardware for those checks.
- Complete all three real builds and inspect fit/timing reports; successful Python generation or compilation alone is insufficient.
- Complete the selected profile's real board validation and three performance measurements. Confirm UART metadata matches its artifact hashes and configuration.
- Generate and review the report against logs and independently calculated scores. Confirm the GPIO commands and documentation remain usable.
- Update `README.md` with the benchmark commands and link to the report. Add dependencies/licenses and usage documentation as required. Keep changes within this task; do not commit or push unless separately requested.

If execution is blocked, provide the executable implementation and document the blocker with observed evidence and the next concrete recovery command. Clearly mark the report incomplete and all unavailable measurements unmeasured. Do not declare the task fully complete until board measurements and the report exist.

### Completion response

Summarize files changed, build status for all three profiles, the configuration actually programmed, measured CoreMark and CoreMark/MHz with repetition spread, validation status, and links to `docs/performance.md` and UART evidence. Identify any incomplete requirements explicitly.
