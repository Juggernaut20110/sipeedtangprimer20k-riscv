# Performance CPU and DDR3 implementation handoff

## Assignment

Implement this handoff through completion using **Luna (`gpt-6-luna`) with extra-high (`xhigh`) reasoning**. The user approved the decisions below. This document is an implementation assignment, not a request to replan or merely describe the work.

Work in `/home/user/git/fpga/sipeedtangprimer20k-riscv`. First prove a fourth CPU profile is faster at **48 MHz**, then add selectable DDR3 to all four profiles, including training, thorough board testing, and CoreMark comparisons. Deliver implementation, reproducible builds, actual board evidence, and updated reports. Do not claim a hardware outcome based on generation, simulation, or synthesis alone.

If using subagents, use Luna with `xhigh` reasoning. Delegate independent source inspection, CPU generation, host tests, or report review; designate exactly one agent as owner of all JTAG and UART operations. Join changes and verify integration before measuring hardware. This handoff does not authorize starting another user-owned chat automatically.

## Approved decisions

- New profile name: `performance`. This CPU profile name is distinct from the benchmark's existing `performance` firmware mode.
- CPU/system clock stays **48,000,000 Hz**. Pursue a measured CPU gain, without clock tuning.
- Use a reproducibly generated custom VexRiscv configuration, rather than assuming the stock `full` variant is faster.
- DDR3 is selectable for **all four profiles**. Preserve on-chip builds as the default.
- DDR3 acceptance includes repeated training, full-address-range tests, cache-bypass checks, and sustained stress.
- Run CoreMark with code/read-only data in DDR3 as well as in on-chip main RAM. Keep benchmark data/BSS in SRAM for comparable placement.
- Implement the CPU improvement first, then DDR3. Preserve original benchmark algorithms, GPIO behavior, SRAM fixes, and historical measurements.
- HDMI, Ethernet, flash boot/programming, and CPU frequency increases are outside this assignment.

## Current state and evidence

The three existing CPU profiles are `minimal`, `lite`, and `standard`. All operate at 48 MHz. `make benchmark-run PROFILE=ALL PORT=<explicit UART>` now runs validation and three scored repetitions sequentially for each profile, saves separate evidence under one batch ID, and returns failure if any profile fails. Timeout/disconnect cases with uncertain firmware completion stop before another reconfiguration.

The latest passing batch is `20260930T125117.536122Z-all`, recorded on 2026-09-30. All twelve board executions passed, and the host suite had 47 passing tests. Independent auditing verified raw UART hashes, transmitted captures, firmware CRC/size/SHA, bitstream SHA, validation CRCs, and score calculations.

| Profile | CoreMark mean | CoreMark/MHz | Iterations per scored run | Elapsed ticks per scored run | LUTs | ALUs | Registers | BSRAM |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| minimal | 16.588340660133245 | 0.3455904304194426 | 332 | 960674749 | 2247 | 300 | 1382 | 38/46 |
| lite | 50.42751164106903 | 1.0505731591889382 | 1009 | 960428116 | 3127 | 443 | 1639 | 40/46 |
| standard | 109.12748165783437 | 2.2734892012048826 | 2183 | 960198095 | 3452 | 479 | 1889 | 46/46 |

Each profile's three repetitions had identical ticks and zero observed score spread. These results establish a baseline on this board, not general variability across boards.

Useful evidence:

- `docs/coremark-summary.md`: consolidated scores, individual latest runs, successful session history, resources, methodology, and evidence links.
- `docs/performance.md`: detailed generated performance report.
- `docs/performance/results.json`: all recorded sessions, artifact identities, latest batch summary, and aggregates.
- `BENCHMARK_HANDOFF.md`: original benchmark implementation requirements. Its previous prohibition on DDR/CPU changes was for that earlier task; this newer user-approved scope authorizes those changes.

HEAD at handoff preparation is `bf60a40940fa7a0542b71e21481b474aa2bcec27`, which fixed real board corruption by changing integrated SRAM and main RAM ports from WRITE_FIRST to READ_FIRST. Keep that fix on every integrated writable RAM that uses the same affected implementation. Earlier failed captures remain excluded from score aggregates.

There are **uncommitted changes and untracked UART captures** from the subsequent rerun, ALL-profile runner/report implementation, and summary document. Inspect `git status` before working; preserve them. Do not discard them, overwrite historical captures, or clean `build/` indiscriminately. Do not commit or push unless the user separately requests it. A previous push to `origin master` was rejected by automatic approval review and has not been newly authorized.

## Environment and source entrypoints

Use project commands through `scripts/project.py`, `.venv`, and `.tools`. The pinned environment already includes Python, RISC-V GCC, LiteX/LiteDRAM, Gowin, and openFPGALoader. `make doctor` is the first environment check. In the current sandbox, Gowin and USB/JTAG/UART operations required approved elevated execution; ordinary host checks and source edits did not.

Verified UART on the attached Dock:

```text
/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
```

It resolved to `/dev/ttyUSB1` during the last run. Rediscover and verify it; always require explicit `PORT`. UART is 115200 baud. Use FPGA **SRAM programming only**, without flash operations. Do not reset, retrain, or reprogram while a benchmark or memory workload is running.

Main project entrypoints:

- `gateware/soc.py`: `PROFILES`, `ProjectSoC`, clock/reset workaround, READ_FIRST fix, and GPIO integration.
- `scripts/build.py`, `scripts/compare.py`, `scripts/validate.py`: building, fingerprints/artifact metadata, resources/timing, and generated-design checks.
- `scripts/benchmark_build.py`, `scripts/benchmark_run.py`, `scripts/benchmark_results.py`, `scripts/benchmark_report.py`: firmware generation, board runner, parser, and report/evidence revalidation.
- `scripts/project.py`, `Makefile`, `scripts/load.py`, `scripts/run.py`: command dispatch, environment, and hardware entrypoints.
- `firmware/benchmark/`: CoreMark port, wrapper, diagnostics, and timing. Its linker layout is generated by benchmark build logic; there is no existing `firmware/benchmark/linker.ld` file.
- `scripts/setup.py`, `dependencies.lock.json`: dependency setup and version pins.
- `tests/test_benchmark_*.py`, `tests/test_gpio.py`, `tests/test_app_logic.c`: existing host checks.

Relevant pinned dependency sources:

- `.deps/litex-boards/litex_boards/targets/sipeed_tang_primer_20k.py`
- `.deps/litex-boards/litex_boards/platforms/sipeed_tang_primer_20k.py`
- `.deps/litedram/litedram/phy/gw2ddrphy.py`
- `.deps/litedram/litedram/modules.py`
- `.deps/litex/litex/soc/software/liblitedram/sdram.c`
- `.deps/litex/litex/soc/software/bios/cmds/cmd_litedram.c`
- `.deps/pythondata-cpu-vexriscv/pythondata_cpu_vexriscv/verilog/Makefile`
- `.deps/pythondata-cpu-vexriscv/pythondata_cpu_vexriscv/verilog/src/main/scala/vexriscv/GenCoreDefault.scala`

Keep dependency directories reproducible. Do not leave undocumented edits under `.deps`; any necessary upstream changes must be checked, repository-owned patches applied by setup, with hashes included in build fingerprints.

## Phase 1: custom performance CPU

1. Pin the CPU generation environment, including Java/SBT/Scala/SpinalHDL dependencies and checksums or exact resolved versions. Use the existing pinned VexRiscv generator and its submodule revision `b6118e5cc2a33323425df6455697139021d50c72`. The checked-in generator currently uses Scala 2.11.12 and SpinalHDL 1.9.4; verify compatibility rather than switching versions silently.
2. Start from `standard`: RV32IM/ILP32, 4096-byte instruction cache, 4096-byte data cache, existing hardware multiply/divide and barrel shifter, bypassing, standard CSR configuration, no debug/CFU/atomics/compressed extensions. Evaluate exactly two initial candidates: `--prediction dynamic` and `--prediction dynamic_target`. Keep other CPU generation parameters equal to standard. Stock `full` is chiefly an expanded CSR configuration and is not evidence of a speed gain.
3. Add a project-owned CPU integration path for the generated RTL without pretending that the existing LiteX variant map already contains `performance`. Record the generator arguments, upstream revisions, generation tools, RTL hash, and actual ISA/cache/prediction configuration in build and measurement metadata.
4. Preserve the existing three profiles' on-chip memory budgets. For the new on-chip profile use 24 KiB BIOS ROM, 32 KiB main RAM, and 8 KiB SRAM. Existing standard BIOS is 21036 bytes, making 24 KiB a plausible starting reservation; explicitly reject link/image overflow. Keep the READ_FIRST behavior. Any inability to fit the approved configuration is a measured blocker, not permission to silently reduce benchmark diagnostics or change old profiles.
5. Generate, synthesize, and route both candidates. Reject resource overflow, negative setup slack, unconstrained required clocks, or design errors at 48 MHz. Record rejected candidates and their reasons.
6. Run a fresh standard validation plus three repetitions, then the same measurements for each viable candidate. Use the pinned, unchanged CoreMark algorithm sources and original measurement methodology.
7. Choose the viable candidate with the highest validated mean score. Ties prefer fewer BSRAM blocks, then fewer LUTs. Accept the public `performance` profile only if its mean is strictly greater than the fresh standard baseline. Do not substitute estimated Fmax for a measured gain. If neither candidate passes, retain results and explain the unmet goal; do not invent a passing fourth profile.

## Phase 2: selectable DDR3 integration

Public memory selector: `MEMORY=onchip|ddr3`, default `onchip`. Thread it through build, load, run, compare, benchmark build/run, generated validation, and artifact selection. Reject unknown values. Preserve existing on-chip paths `build/<profile>/`; use `build/ddr3/<profile>/` for DDR3 artifacts, with distinct diagnostic subdirectories.

Use the upstream target's board-specific DDR3 PHY/controller integration: `GW2DDRPHY` and `IMD128M16R39CG8GNF`. Its configured geometry is 8 banks × 16384 rows × 1024 columns × 16 bits, or 256 MiB. Validate the generated geometry and address aliasing on this board; do not claim capacity from a small smoke test.

- CPU/system clock: 48 MHz; DDR clock CK: 96 MHz. Retain upstream DLL-off settings at this rate, CL6/CWL6, and disabled nominal ODT.
- Enable upstream DDR creation by replacing integrated main RAM with DDR main RAM at `0x40000000`. Retain 8 KiB SRAM at `0x10000000` and project GPIO/timer/UART.
- Start with 8 KiB LiteDRAM L2. Record its size and include it in resource budgets.
- Add a DDR-mode-only 16 KiB integrated diagnostic RAM at `0x20000000` for destructive test code, data, and stack. Enforce its complete linked extent. Diagnostic execution must not depend on the memory being retrained or overwritten.
- Adapt the current reset-synchronizer workaround: DDR mode's system clock is produced through CLKDIV, unlike the existing direct PLL output. Do not require or remove a PLL synchronizer for the wrong clock domain.
- Add correct constraints for PLL/sys2x/CLKDIV/init paths. Preserve pin constraints and check that required clock paths are analyzed.
- Check BIOS image fit after adding DDR training support. Start with the existing 32 KiB DDR-mode BIOS reservation; if it overflows, select the smallest fitting reservation from 40 or 48 KiB and require the complete FPGA resource budget still to pass. Record the selected size. Do not truncate BIOS functionality or a binary to make a build pass.
- Supply a diagnostic uncached DDR alias at `0xc0000000`, mapping the same physical memory through a dedicated controller port that bypasses LiteDRAM L2 as well as CPU D-cache. Verify integration with the CPU IO region and bus decoding. Simply flushing D-cache does not prove an L2-bypassed physical-memory access.

All eight profile/memory configurations must generate, build, and meet timing. Defaults for existing user commands remain compatible.

## Phase 3: training and thorough DDR3 tests

Add commands:

```sh
make ddr-test-build PROFILE=ALL
make ddr-test-run PROFILE=ALL PORT=<verified Dock UART>
make ddr-test-report
```

The test runner handles DDR3 mode, all four profiles in registry order, explicit UART ownership, and separate session artifacts. Expose `TRAINING_RUNS=10` and `STRESS_SECONDS=1800` as defaults; record requested and actual values. A shorter user-selected run must be labeled partial relative to the thorough acceptance criteria.

Training:

- Use the upstream PHY initialization and BIOS JEDEC/read-leveling flow. GW2DDRPHY advertises read leveling, four bitslips, and 256 delays. Do not promise unsupported write-leveling features.
- Capture training return status, lane delay/bitslip selections, and available valid-window diagnostics. Capture raw BIOS output as evidence.
- Inspect `sdram_init`: the pinned source calls `sdram_leveling()` without checking its return value. Make training failures propagate to initialization failure using a checked repository-owned patch or equivalent project-owned integration. Do not accept successful boot text as proof of successful leveling.
- Perform **ten fresh SRAM reconfigurations per profile**, each requiring training and smoke-test success. These are reconfiguration tests; do not label them physical cold boots or power-cycle tests.

Destructive tests execute entirely from the diagnostic RAM and cover the full configured physical DDR range:

- Walking ones/zeros, fixed/inverted patterns, address-derived patterns, deterministic pseudorandom patterns, and address/bank/row/column alias checks.
- Byte and halfword writes/reads with neighboring-lane preservation and boundary coverage.
- Full-range uncached write/read verification through the controller-bypassing alias.
- Cached access verification and cached/uncached visibility tests with correct fences and maintenance of every participating cache. Account explicitly for LiteDRAM L2; do not test only the CPU cache contents.
- Thirty minutes of stress **per profile**, including delayed readback/refresh exposure, changing pseudorandom seeds, and measured read/write bandwidth. Report the access path, transfer size, clock, and elapsed ticks for bandwidth.

Emit machine-parseable start/end, identity, training, test-phase, and failure markers. Preserve exact counters, duration, tested ranges, error count, and first failing address/expected/actual values. Failure, missing completion, or insufficient coverage/duration cannot produce a passing thorough summary. Stop immediately on corruption and retain the evidence.

Do not retrain while code is executing from DDR3. The host must not issue another command, reset, or reconfiguration when current execution completion is uncertain. Adapt existing single-owner serial handling and cleanup rather than introducing competing UART readers.

## Phase 4: benchmarks, reports, and historical compatibility

Once DDR3 passes, support:

```sh
make benchmark-build MEMORY=onchip
make benchmark-run PROFILE=ALL MEMORY=onchip PORT=<verified Dock UART>
make benchmark-build MEMORY=ddr3
make benchmark-run PROFILE=ALL MEMORY=ddr3 PORT=<verified Dock UART>
make benchmark-report
```

Each batch requires validation then three accepted performance repetitions for each of the four profiles. `ALL` and `all` remain accepted. Default memory mode is on-chip. Keep benchmark algorithm data/BSS and the reserved stack in SRAM; use selected main RAM for code/read-only data. Record the actual linker extents. Keep the calibration, fresh data initialization, cache flushing, CRC/image probes, tick-based scoring, and UART-outside-timing method intact.

Extend session identity with memory mode, memory placement, CPU configuration/RTL identity, L2/cache configuration, and DDR training status. Legacy sessions without a memory field mean `onchip`. Use distinct profile/memory/session identities and prevent selecting a stale artifact from the other memory mode. Include mode and new sources/patches in fingerprints and artifact checks.

Historical compatibility needs deliberate work: current batch report validation assumes recorded membership equals the current global `PROFILES`. Once a fourth profile is added, validate old three-profile batches against their **recorded membership**, without adding a missing fourth result or marking the old valid batch failed solely because the registry grew. Unknown/duplicate/inconsistent membership remains an error. Preserve historical identities and hashes; never overwrite failed captures or combine historical and current scores.

Update the detailed performance report, results JSON, and `docs/coremark-summary.md` to compare all four profiles across both memory modes. Include resource/timing data, operating clocks distinct from estimated Fmax, CPU winner selection, rejected candidates, measured CPU gains, DDR training reliability, memory-test coverage/duration, bandwidth, CoreMark values/spreads, and evidence links. Clearly label that DDR CoreMark uses DDR for code/read-only data while algorithm data remains in SRAM.

## Verification and completion checklist

- Host checks cover profile/variant integration, pinned generation and metadata, ROM/image fit, default command compatibility, memory-mode isolation, generated DDR clocks/CSRs/address maps, READ_FIRST handling, and GPIO behavior.
- Parser/runner checks cover successful and failed training, memory corruption, profile/mode/artifact mismatch, duplicate/missing captures, partial coverage/stress, timeout/disconnect, interruption, and no unsafe continuation.
- Report checks cover historical three-profile batches, new four-profile batches, separate memory-mode aggregates, and exclusion of failed trials.
- Both CPU candidates have documented synthesis/timing outcomes and actual measurements when viable; the selected fourth profile beats the fresh standard baseline at 48 MHz.
- All eight public profile/memory builds pass vendor resource/timing checks. No required clock path is silently left unconstrained.
- All four DDR3 profiles pass ten reconfiguration/training cycles, full-range integrity tests, cache-bypass/coherency checks, and at least 1800 seconds of stress each, with zero errors.
- All eight profile/memory combinations pass validation and three scored CoreMark repetitions. Revalidate captured UART and artifact hashes independently before declaring completion.
- Updated commands and reports are reviewable, reproducible, and backed by captured evidence. No flash writes, unrequested commit, or push occurred.

Budget enough time for at least two hours of DDR stress alone, plus builds, training cycles, full-range tests, and benchmarks. Do not replace long acceptance runs with short smoke tests to save time. If access, synthesis resources, timing, or measured CPU performance prevents a requirement, retain completed work and exact failure evidence, state what remains unmet, and avoid declaring this handoff complete.
