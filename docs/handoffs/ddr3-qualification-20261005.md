# Finish VexRiscv DDR3 testing on the replacement Tang Primer 20K Dock

Prepared October 5, 2026 (America/New_York).

## Assignment and model

Use **Luna (`gpt-6-luna`) with extra-high (`xhigh`) reasoning** to complete actual DDR3 qualification and CoreMark measurement for the registered VexRiscv profiles on the newly attached board. Work in `/home/user/git/fpga/sipeedtangprimer20k-riscv`. Carry out builds, hardware runs, evidence validation and reporting; do not stop at a plan or treat synthesis as a memory-test pass.

The profiles to finish are **minimal, lite, standard, performance and linux**, all at **48 MHz CPU/system clock**. `performance` uses the project's generated `dynamic_target` CPU; `linux` here runs the existing bare-metal diagnostics and benchmarks, not a Linux kernel. Verify the current registry in `gateware/soc.py`. `maxperf` remains provisional and is outside this public-profile qualification; do not promote it or reopen CPU tuning just to complete this assignment.

This handoff authorizes the necessary existing SRAM-only build/test/benchmark workflows and fixes needed to complete them. Preserve on-chip defaults, algorithms, existing CPU configurations, READ_FIRST RAM fixes and historical evidence. Do not program flash, increase clocks, add HDMI/Ethernet/OS work, commit or push without a separate user request. This file does not request creation of another chat. If delegation is used, use Luna/xhigh and give exactly one agent ownership of JTAG and UART; never run two hardware jobs concurrently.

## Critical new evidence: the replacement board passes

The user attached a new Tang Primer 20K Dock on October 5. The reported chip marking is literally `H5TQ1G63ERF PBC`, likely `H5TQ1G63EFR-PBC` (normalization is an inference). Sipeed's vendor test detected **1 Gbit / 128 MiB**.

The exact unchanged vendor bitstream used on the previous board passed both full-range check stages on **three fresh SRAM loads** on the new board: three completed tests, six successful check stages, zero reported mismatches. Each complete test finished about 48.2 seconds after its programming command started. The old board failed the first check on all three loads of this same bitstream. This strongly favors a hardware difference between the assemblies; it does not isolate memory silicon from board power, soldering, Dock connections or signal integrity.

- New-board report: `docs/ddr3/sipeed-vendor-test/new-board-20261005.md`.
- New-board evidence: `docs/ddr3/sipeed-vendor-test/20261005T211409Z-new-board/`.
- Comparison: `comparison.json` in that directory; raw UART, timestamped events and manifest are alongside it.
- Previous-board evidence: `docs/ddr3/sipeed-vendor-test/summary.json`, `20261002T024119Z/` and `20261002T024327Z/`.
- Vendor source commit: `e469df4c0c9c41824f405a8515decf24ef1e8e6f` in `.deps/sipeed-tangprimer20k-example`.
- Vendor test SHA-256: `167d04a87c0e0084204a1863f8a999f7c696d0d25ca959a82228c203470b7f50`.

The vendor test uses Gowin's DDR3 IP independently of LiteDRAM/VexRiscv. Its second-stage `rng_inv` repeats the same generated pattern, rather than an inverted pattern. Its pass is a useful board comparison, **not qualification of the project's PHY/controller, CPU cache visibility or sustained stress**. The corrected LiteX design has **not yet been tested on the replacement board**.

## Current repository and memory configuration

HEAD when this handoff was prepared: `747928c` (`Correct DDR3 geometry for fitted 128 MiB Hynix part`). Check `git status` and HEAD before working. There are uncommitted README edits and untracked vendor-test evidence; preserve all of them and this handoff. Do not run `make clean` or delete evidence/build directories indiscriminately.

The current code already corrects the former 256 MiB assumption:

- `gateware/ddr_geometry.py`: H5TQ1G63EFR, 13 row bits, 3 bank bits, 10 column bits, 16 data bits; **134217728 bytes**.
- `gateware/ddr3.py`: fitted-part module/timings.
- `gateware/soc.py`: 48 MHz system, **96 MHz DDR CK**, existing PHY configuration; 8 KiB LiteDRAM L2; 8 KiB working SRAM.
- Cached DDR range: `0x40000000..0x47ffffff`.
- CPU/L2-bypassed diagnostic alias: `0xc0000000..0xc7ffffff`.
- Diagnostic code/data/stack: 16 KiB on-chip RAM at `0x20000000`, with a reserved 2 KiB stack.

Do not restore the old 256 MiB module or acceptance bounds from `PERFORMANCE_DDR3_HANDOFF.md`. That document also predates the five-profile registry and the replacement board. `docs/ddr3/hynix-geometry-correction.md`, `docs/ddr3/write-disturbance.md` and the current failed DDR report describe **the previous board**, including failures after the geometry correction. Retain those failures as history; do not report them as failures of the new board.

Generated artifacts use `build/ddr3/<profile>/`; on-chip artifacts use `build/<profile>/`. Check source fingerprints, part/geometry, CPU RTL, bitstream hashes, firmware hashes, linked extents and routed timing before programming. Rebuild stale artifacts from current source rather than loading an old diagnostic experiment or accepting metadata based only on a file's existence. Keep the project's reproducible dependency patches; do not leave unexplained edits in `.deps`.

## Environment and hardware ownership

The pinned tools are already installed: `.venv`, `.tools`, licensed Gowin V1.9.12.04 via `/home/user/.local/bin/gw_sh`, and `.tools/bin/openFPGALoader`. Prefer the Makefile and `scripts/project.py` so PATH and compiler environment are correct. Run `make doctor` before hardware testing. Network/tool/device access may need the normal sandbox escalation; it was approved for the existing board tests. Do not alter the license or install tools unnecessarily.

Verified Dock UART on October 5:

```text
/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
```

It resolved to `/dev/ttyUSB1`; rediscover it and pass the stable name explicitly as `PORT`. Use 115200 baud, 8N1. `if00` is the debugger's other channel. The FTDI `A6005EXR` adapter and `1a86` serial device also present are not the verified Dock UART. The programmer detected Gowin ID `0x81b`, GW2A(R)-18(C). These generic names/IDs do not uniquely identify a board; label every new batch as the **replacement board attached October 5** and associate it with the vendor-test comparison.

Before programming, verify no other job owns the UART (`fuser -v <port>`) or JTAG. Open UART before SRAM programming through the existing runner. Do not reset/retrain/reprogram while a workload is active. Honor the runner's unsafe-continuation result on timeout, disconnect or uncertain completion; preserve evidence and establish an idle state before resuming.

## Execution sequence

First inspect the relevant sources and reports, verify the environment, and run appropriate host/generated-design checks:

```sh
make doctor
make test
make ddr-test-build PROFILE=ALL STRESS_SECONDS=1800
```

Check that all five current-source SoCs and diagnostic images built successfully and meet their required routed timing/resource constraints. Inspect `build/ddr3/ddr-test-build.json` and each profile's metadata. The build command checks fingerprints and rebuilds stale artifacts. Do not weaken timing, suppress integrity checks, reduce diagnostic functionality or substitute an experimental PHY setting to manufacture acceptance.

A short `minimal` diagnostic run can catch startup failures before the long batch:

```sh
make ddr-test-run PROFILE=minimal PORT=/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0 TRAINING_RUNS=3 STRESS_SECONDS=60
```

This still runs the full destructive pattern suite and may take substantially longer than 60 seconds. It is **partial evidence**, never thorough acceptance. This preliminary step is optional if the full batch can start directly. The runner regenerates diagnostic firmware if the requested stress duration differs; final runs must request 1800 seconds.

Finish thorough qualification for every profile:

```sh
make ddr-test-run PROFILE=ALL PORT=/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0 TRAINING_RUNS=10 STRESS_SECONDS=1800
make ddr-test-report
```

Expect a long run: 30 minutes of stress per profile is only the stress portion; the full-range pattern phases and training add time. The runner's default trial timeout is 14400 seconds. Use bounded waits and useful progress updates while leaving a running job intact. If interrupted, retain session/evidence and resume outstanding profiles separately after safe recovery; link the separate batch IDs rather than claiming a single uninterrupted batch. Do not rerun already-qualified, unchanged profiles without a reason.

If a profile fails, retain the exact BIOS/training/failing-address evidence. Determine whether it is a real memory error, training failure, artifact mismatch, SFL transfer/startup failure, resource/timing issue or capture/parser problem. Fix supported causes, rebuild affected artifacts, run relevant tests and repeat affected qualification. The vendor pass alone does not justify masking a LiteX failure. Stop scored benchmarking for an unqualified profile and report its concrete blocker if it cannot be resolved.

After qualification passes, build matching DDR3 CoreMark artifacts and measure validation plus three scored repetitions for each qualified profile:

```sh
make benchmark-build MEMORY=ddr3
make benchmark-run PROFILE=ALL MEMORY=ddr3 PORT=/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
make benchmark-report
```

Use `PROFILE=ALL` for benchmarking only when all five profiles qualify. Otherwise run qualified profiles individually and clearly identify omissions. Keep the unchanged CoreMark sources/methodology: code/read-only data in DDR3, algorithm data/BSS/stack in SRAM, 48 MHz clock and measured 64-bit timer ticks. Do not assume benchmark validation by itself grants full DDR acceptance. If presenting a DDR-versus-on-chip comparison, obtain a fresh on-chip validation/three-run baseline on this new board with the same source; older-board scores must remain labeled historical.

## Evidence and acceptance

For **each** profile, require all of the following before reporting DDR as thoroughly qualified:

1. Current-source build with verified 128 MiB geometry, artifact identities, required routed clocks and nonnegative required timing slacks.
2. At least **10 fresh SRAM reconfiguration/training/uncached-smoke passes**, with both expected read lanes and recorded leveling windows validated.
3. Passing complete **128 MiB** destructive suite: walking ones/zeros, fixed/inverted patterns, address pattern, deterministic PRNG, bank/row/column alias checks, byte/halfword neighbor preservation and cached/uncached visibility that bypasses or evicts both CPU D-cache and LiteDRAM L2 as required.
4. At least **1800 measured seconds** of delayed-readback stress, zero errors, valid full-suite completion markers and complete UART evidence. Requested time without measured completion is insufficient.
5. DDR3 CoreMark validation and three valid scored repetitions, with reported CoreMark/s, CoreMark/MHz, timer ticks, configuration and build identity. Failed/partial/unqualified evidence must be excluded from accepted aggregates.

The existing parsers in `scripts/ddr_test_run.py`, `scripts/ddr_test_report.py` and benchmark report/evidence helpers perform detailed validation. Revalidate the saved raw evidence and artifact hashes rather than trusting edited summaries. Preserve original binary RX/TX captures, manifests, training records and build reports under the established directories; existing DDR sessions live in `build/ddr3/ddr-test-sessions/` with retained evidence under `docs/ddr3/evidence/`.

The session schemas currently use a generic board name. Ensure a report or supplemental manifest associates every new session/batch ID with the replacement board; do not silently pool results from the two boards. Update `docs/ddr3/report.md`, DDR machine-readable results, benchmark/performance reports, `docs/coremark-summary.md` and README as appropriate. Show a compact profile table containing training passes, tested bytes, stress duration, error count, cache visibility, resource/timing outcome and CoreMark results. Preserve older failed captures and explain which board/configuration produced them.

## Recovery and completion

The replacement board was left in DDR idle after the vendor tests. Recovery loaded `build/sipeed-ddr-test/idle-build/impl/pnr/ddr_idle.fs`, SHA-256 `a7a702e3ff9835ab3ee653f0f9ff7c90165808db514b3230dcc91d0314b93df3`. This image holds DDR RESET# low, CKE low, CS# high, CK stopped and ODT low. The post-load UART capture was empty.

After the final workload completes, load this verified idle image into **SRAM**, capture programmer output and a quiet UART observation, and save new recovery evidence alongside the new testing results. Do not overwrite `docs/ddr3/sipeed-vendor-test/recovery/`, which records the old board. The old `build/sipeed-ddr-test/recover_idle.py` writes that historical directory, so adapt its destination before reuse. **`openFPGALoader --detect --reset` is not proof of recovery**: an earlier successful detect/reset command left the vendor test running.

Completion means all five profiles have current, board-specific DDR acceptance and benchmark evidence, or any unresolved profile has a clearly documented concrete failure and retained diagnostic evidence. Report partial completion honestly. Finish with the outcome, profile results, evidence/report links, material limitations and verified board recovery state. Do not claim cold-boot/power-cycle testing when only SRAM reloads were performed.
