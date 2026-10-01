# Linux and maximum-performance CPU profiles: implementation handoff

## Assignment

Implement and test this handoff through completion using **Luna (`gpt-6-luna`) with extra-high (`xhigh`) reasoning** in `/home/user/git/fpga/sipeedtangprimer20k-riscv`.

The user approved adding **both** a fifth `linux` CPU profile and a sixth `maxperf` CPU profile. Both must support `MEMORY=onchip|ddr3`. The CPU/system clock stays **48,000,000 Hz**. Tune `maxperf` separately for each memory mode and require a measured CoreMark improvement over the fourth `performance` profile in each mode. Deliver implementation, reproducible generation/builds, actual Dock measurements, and updated evidence-backed reports. This is an implementation assignment, not a request to produce another plan.

**Assume the Tang Primer 20K standard Dock is connected and available for tuning and hardware testing.** Discover its actual JTAG and UART interfaces and use them. Historical statements that the board, Java/SBT, or generator source were unavailable are not current facts: inspect the current environment and resolve routine setup or access problems. Do not stop after host tests or synthesis when board measurements are required.

This assignment extends `PERFORMANCE_DDR3_HANDOFF.md`. Preserve its existing implementation and qualification work. This document controls the two new profiles where the earlier handoff only discusses four. Existing clock, memory-test, benchmark-methodology, and evidence requirements still apply.

Run the implementing agent with the specified model and reasoning effort; do not create another user-owned chat automatically. Subagents are optional, not required. If used, use Luna with `xhigh` reasoning and give exactly one agent exclusive ownership of all JTAG/UART operations.

## Approved scope and completion rule

- Final ordered profiles: `minimal`, `lite`, `standard`, `performance`, `linux`, `maxperf`.
- Both additions support on-chip and DDR3 builds. Existing memory-mode defaults and the first four profiles' CPU settings and memory budgets remain unchanged.
- `linux` means the pinned Linux-capable VexRiscv CPU integrated with the existing bare-metal GPIO, DDR diagnostics, and CoreMark flows. Linux kernel, bootloader, root filesystem, device tree, and boot-to-shell work are deferred. Describe the result as a Linux-capable CPU profile, not a demonstrated Linux boot.
- `maxperf` means the best qualified configuration in the explicitly bounded candidate matrix below, not a claim of optimality over all possible CPU designs. Its on-chip and DDR3 CPU RTL may differ; every result must identify the actual configuration.
- All comparisons use the same 48 MHz operating clock and existing CoreMark methodology. DDR CK remains 96 MHz. No frequency sweep, CFU, benchmark-specific accelerator, algorithm edits, SMP, Ethernet, HDMI, or flash programming is included.
- Register `linux` as profile five. Evaluate `maxperf` through a separate candidate path; register profile six only when selected winners meet all qualification requirements in both memory modes. Never disguise a provisional candidate as an accepted `maxperf` profile.
- If no candidate wins in either mode, preserve the implementation and failed/rejected evaluation evidence, deliver the qualified Linux profile if possible, and explicitly report that the sixth-profile goal remains unmet. A gain in one memory mode does not satisfy the two-mode goal.
- Do not commit or push unless separately requested by the user.

## Starting state: inspect before changing it

At preparation, `gateware/soc.py` contains four profiles. `performance` uses LiteX's `standard` integration shell with project-owned generated `dynamic_target` RTL. Its accepted selection is recorded in `cpu-profile-selection.json`, tied to `docs/performance/cpu-evaluation/evaluations.json` and raw UART evidence.

The recorded fourth-profile comparison is at 48 MHz: fresh `standard` mean 109.12748165783437 CoreMark; selected `performance` candidate mean 119.32391343090488, approximately 9.35% higher. These are existing on-chip measurements, not the new `maxperf` baselines or DDR3 scores. Independently validate the recorded hashes and selection before depending on them.

The current DDR report contains a failed `minimal` training attempt with no valid read-leveling window. Some prose reports predate fourth-profile acceptance. Treat this as a snapshot, not a reason to assume the current board must fail. Check newer captures, live process state, and the other agent's latest checkpoint before deciding what remains unfinished.

There are extensive uncommitted changes, untracked generation/selection files, and UART captures. Preserve them. Begin with `git status`; do not reset, clean, overwrite historical captures, or remove another agent's build outputs. Do not rerun fourth-profile selection or modify `cpu-profile-selection.json` merely to add the new profiles.

The fourth-profile/DDR3 agent may still be working in this checkout. Inspect its status and active build/test processes. Begin shared-file integration only after it reaches a stable checkpoint or releases the checkout. Do not interrupt its board workload, edit shared sources during its measurement batch, or rebuild its selected artifacts underneath a running test. Independent read-only inspection can proceed meanwhile. Use existing session authorization for coordination; otherwise ask before messaging another user-owned chat.

### Environment and entrypoints

- Run project commands through `Makefile`, `scripts/project.py`, `.venv`, and `.tools`. Start with `make doctor`; repair missing pinned prerequisites using project setup rather than silently changing versions.
- CPU integration and metadata: `gateware/soc.py`, `scripts/build.py`, `scripts/memory.py`, `scripts/cpu_candidates.py`, `cpu-generator.lock.json`.
- Existing fourth-profile evaluation: `scripts/cpu_candidate_build.py`, `scripts/cpu_candidate_run.py`, `cpu-profile-selection.json`.
- Command/build/report flows: `scripts/load.py`, `scripts/run.py`, `scripts/compare.py`, `scripts/validate.py`, `scripts/benchmark_*.py`, `scripts/ddr_test_*.py`.
- Firmware: `firmware/benchmark/`, `firmware/ddrtest/`, and the GPIO demo. CoreMark linker layout is generated by benchmark build logic; do not assume a separate benchmark linker script exists.
- Tests: `tests/test_cpu_candidates.py`, `tests/test_ddr3.py`, existing benchmark/GPIO tests, and `make test`.
- Pinned LiteX CPU definitions: `.deps/litex/litex/soc/cores/cpu/vexriscv/core.py`.
- Pinned generator: `.deps/pythondata-cpu-vexriscv/pythondata_cpu_vexriscv/verilog/src/main/scala/vexriscv/GenCoreDefault.scala` and its adjacent `Makefile`.
- Board target: `.deps/litex-boards/litex_boards/targets/sipeed_tang_primer_20k.py`.

The existing generator lock pins pythondata-cpu-vexriscv commit `642ecfed1c84460555d6d803d660cc60cfc1ecb6`, VexRiscv submodule `b6118e5cc2a33323425df6455697139021d50c72`, Temurin Java 8u432-b06, SBT 1.9.7, Scala 2.11.12, and SpinalHDL 1.9.4. Reuse and verify those pins. Any necessary dependency change belongs in a checked repository-owned patch, applied reproducibly by setup and included in fingerprints; do not leave undocumented edits in `.deps`.

## Phase 1: shared profile/configuration integration

1. Extend profile configuration handling so the actual ISA, cache sizes, predictor, RTL identity, BIOS reservation, and memory mode come from one authoritative configuration rather than hard-coded assumptions that all non-minimal profiles are RV32IM with 4 KiB caches. Preserve existing metadata compatibility.
2. Keep on-chip outputs at `build/<profile>/` and DDR3 outputs at `build/ddr3/<profile>/`. Candidate outputs must be separate from public artifacts and separate by memory mode and configuration. Use `build/maxperf-candidates/<memory>/<candidate-id>/` and persistent evidence under `docs/performance/maxperf-evaluation/<batch-id>/`.
3. Candidate IDs must include instruction-cache bytes, data-cache bytes, and compressed-instruction capability; do not use only `dynamic_target` as their identity. Retain complete generator arguments, upstream/tool versions, RTL SHA-256, compiler flags, bitstream/firmware hashes, placement, clock, and training status.
4. Include new configuration/selection records and RTL identity in source fingerprints and stale-artifact checks. Public load/run paths must reject an artifact for the wrong profile, memory mode, or selected configuration. Candidate runners must accept explicit provisional identities without adding them to public `ALL` membership.
5. Support the new profiles across build, load, run, compare, generated validation, benchmark build/run, and DDR diagnostics. Preserve `ALL`/`all` behavior and recorded profile order. Public `maxperf` requests before qualification must fail with a useful message directing users to the candidate workflow.
6. Preserve existing first-four selection semantics and metadata. Use a separate tracked `maxperf-profile-selection.json` containing an accepted selection for each memory mode; validate both selections against generation manifests and independently revalidated board evidence before exposing the public profile.

### Memory, clocks, and cache maintenance

For the two new on-chip profiles, reserve **24 KiB BIOS ROM, 32 KiB main RAM, and 8 KiB SRAM**. Preserve the 2 KiB application/CoreMark stack reservation. Check BIOS, application, CoreMark, and linked data/stack extents; reject overflow rather than truncate diagnostics or reduce these budgets. A new profile that cannot fit is a measured blocker.

For DDR3 retain the existing 256 MiB geometry, normal base `0x40000000`, controller-bypassing uncached alias `0xc0000000`, 8 KiB SRAM, 16 KiB diagnostic RAM at `0x20000000`, and 8 KiB LiteDRAM L2. Retain the existing BIOS probing order of 32, 40, then 48 KiB and choose the smallest fitting reservation. Record the selected reservation and require the complete resource budget to fit.

Keep READ_FIRST on the integrated writable RAMs affected by the known board corruption. Retain board reset handling, GPIO pin/order/polarity, UART at 115200 baud, timers, and all required 48/96 MHz constraints. Reject negative setup, hold, recovery, or removal checks and unconstrained required paths. Do not expand timing exceptions merely to make a candidate pass.

Cache maintenance and conflict probes must use each configuration's actual sizes and all participating caches. Larger caches must not turn an eviction test into a test of cached data. Verify the pinned CPU flush mechanism for both new variants; account for LiteDRAM L2 separately. Preserve benchmark startup SRAM/image-integrity diagnostics.

## Phase 2: Linux-capable CPU profile

Use the pinned LiteX **`linux`** variant and its shipped `VexRiscv_Linux.v` RTL. Its pinned generation recipe uses `--csrPluginConfig linux-minimal` with 4 KiB instruction/data caches and static prediction. Preserve its MMU, supervisor, and atomic capability, without debug or other optional peripherals.

Derive compiler settings from the actual Linux CPU variant: RV32IMA with ILP32, including the pinned toolchain's required extension-version spelling. Do not reuse standard-profile RV32IM metadata or enable compressed instructions accidentally. Record the shipped RTL hash and upstream identity even though this profile does not require generating custom RTL.

Build and route both memory modes, verify generated memory/CSR maps and compiler settings, and run the existing GPIO demo and CoreMark on the connected board. Both modes require a passing validation run and three scored repetitions. DDR3 additionally requires the thorough memory qualification below. No speed improvement is required for `linux`.

Do not equate bare-metal CoreMark with runtime validation of MMU/supervisor/atomic operations. Record those capabilities from the pinned CPU configuration, and label OS boot as deferred.

## Phase 3: bounded maxperf tuning

Reproducibly generate all candidates through the pinned `GenCoreDefault` Wishbone integration. Use the fourth profile's locked settings, including `dynamic_target` prediction, full bypassing, hardware multiply/divide, single-cycle shifter, small CSR configuration, and its existing exception/IO behavior. Keep atomics, debug, CFU, and PMP disabled.

Vary **only** these parameters:

| Memory mode | Instruction-cache sizes | Data-cache sizes | Compressed instructions | Candidates |
|---|---|---|---|---:|
| `onchip` | 2, 4 KiB | 2, 4 KiB | disabled, enabled | 8 |
| `ddr3` | 4, 8, 16 KiB | 2, 4, 8 KiB | disabled, enabled | 18 |

Generate the full Cartesian product per mode: **26 candidates total**. The 4/4 KiB uncompressed candidate is a reproduction/control of the existing fourth CPU configuration; it must not be promoted merely because it exists. The original upstream `GenFullNoMmuMaxPerf` demo has a different reset/bus/debug setup: do not drop that demo RTL into the LiteX shell or assume the stock `full` variant is faster.

Compressed candidates are **RV32IMC**, uncompressed candidates **RV32IM**, both ILP32. Implement an integration path whose actual generated GCC flags match that ISA. Do not map compressed RTL to an `imac` shell whose compiler flags permit unsupported atomic instructions. Compile BIOS, libraries, demo, DDR diagnostics, and CoreMark consistently for the selected CPU. Record all flags. CoreMark algorithm sources, `-O2`, no LTO, and untimed-wrapper `-Os` remain unchanged.

### Candidate evaluation and promotion

1. Generate, compile, synthesize, and route every candidate. Preserve every outcome, including BIOS/image overflow, BSRAM/LUT overflow, timing failure, and generator failure. Do not silently remove a failing matrix entry or change cache/budget values to rescue it.
2. Establish a fully working fourth-profile DDR3 reference before DDR candidate scoring. Resolve shared DDR bring-up failures first, then benchmark candidates; a failed training attempt cannot produce a score.
3. In each memory mode, freshly rebuild/verify and measure public `performance`: one validation run and three scored repetitions. Record exact baseline RTL, firmware, bitstream, clock, and placement. Do not use the older 119.323913 score as the new baseline.
4. Measure every resource/timing-viable candidate using one validation run and three performance repetitions, with fresh programming/firmware initialization and existing untimed calibration. DDR candidates require successful training and uncached smoke verification for each programmed design before their benchmark firmware executes. Preserve all raw RX/TX captures and failure evidence.
5. Choose independently per mode: highest validated mean CoreMark; ties prefer fewer BSRAM blocks, then fewer LUTs; remaining exact ties use lexicographically smallest candidate ID. Require a strict mean-score gain over that mode's fresh `performance` baseline, calculated from full-precision tick-derived values rather than rounded display scores.
6. Run thorough DDR qualification on the selected DDR winner. If it fails definitively, exclude it with evidence and evaluate the next ranked candidate for full qualification. Do not continue hardware work after an uncertain timeout until execution is known to have stopped. Never promote a candidate with failed integrity or incomplete stress coverage.
7. Rebuild and remeasure each selected CPU through its intended public `maxperf` identity before final acceptance, using a provisional verification path until both modes qualify. Confirm the installed RTL/configuration matches the measured selection and both public-path means still beat their fresh baselines. Only then finalize the tracked selection record and public registry membership.

Add reproducible commands:

```sh
make maxperf-build MEMORY=onchip
make maxperf-run MEMORY=onchip PORT=<verified Dock UART>
make maxperf-build MEMORY=ddr3
make maxperf-run MEMORY=ddr3 PORT=<verified Dock UART>
```

`maxperf-build` builds the complete matrix for the selected memory mode and preserves per-candidate outcomes. `maxperf-run` runs the fresh baseline, viable candidate trials, winner verification, and writes that mode's evaluation evidence. After both modes qualify, finalize the accepted selection automatically; no user permission step is required for ordinary SRAM programming/tests. Defaults remain `MEMORY=onchip`; reject unknown modes and require explicit `PORT` for hardware runs. Reuse existing benchmark runner/parser logic rather than create a weaker scoring path.

## Phase 4: connected-Dock hardware qualification

Discover and verify the board; use its actual stable UART path. Previously verified path:

```text
/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
```

It previously resolved to `/dev/ttyUSB1`; do not assume that mapping persists. Use FPGA **SRAM programming only**. Confirm no competing serial monitor, benchmark, DDR runner, or programmer owns the board. If sandbox restrictions block device access or Gowin, use the available approval/escalation mechanism for the authorized build/test operation. Capture exact failures if access still cannot be obtained.

Verify the existing first-four work at its stable checkpoint. Reuse complete, independently verified DDR acceptance evidence only if it identifies the same relevant final artifact/configuration. Resolve any shared training/PHY/controller failure necessary for this task, using a reproducible patch and truthful retesting; do not accept old failed or diagnostic-only captures as passes.

For **each new selected DDR3 profile**, require:

- Ten fresh SRAM reconfigurations, each with BIOS training success, valid lane bitslip/delay-window evidence, and successful uncached smoke verification. These are reconfiguration cycles, not physical cold boots.
- Full 256 MiB destructive integrity coverage from the diagnostic RAM: walking bits, fixed/inverted and address-derived patterns, pseudorandom patterns, geometry/address alias tests, byte/halfword lane preservation, and boundary coverage.
- Physical-memory verification through the alias that bypasses CPU D-cache and LiteDRAM L2; cached/uncached visibility checks with fences and maintenance of both caches.
- At least **1,800 seconds of measured stress**, including delayed readback/refresh exposure, changing seeds, zero errors, and measured read/write bandwidth with access path, transfer bytes, clock, and elapsed ticks recorded.
- Complete machine-parseable identity/start/phase/end/training records, error counters, coverage, and duration. Missing completion, short stress, failed training, or corruption cannot yield a thorough pass.

Diagnostics must execute entirely from the reserved 16 KiB diagnostic RAM, with linked code/data/stack bounds checked. Never retrain while executing from DDR. Stop on corruption and retain evidence. On timeout/disconnect/interruption with uncertain execution, do not issue another command or reprogram merely to keep the batch moving; establish a safe idle state first using the existing runner's handling.

After qualification, run final public batches:

```sh
make test
make compare MEMORY=onchip
make compare MEMORY=ddr3
make benchmark-build MEMORY=onchip
make benchmark-run PROFILE=ALL MEMORY=onchip PORT=<verified Dock UART>
make benchmark-build MEMORY=ddr3
make benchmark-run PROFILE=ALL MEMORY=ddr3 PORT=<verified Dock UART>
make ddr-test-build PROFILE=linux
make ddr-test-run PROFILE=linux PORT=<verified Dock UART> TRAINING_RUNS=10 STRESS_SECONDS=1800
make ddr-test-build PROFILE=maxperf
make ddr-test-run PROFILE=maxperf PORT=<verified Dock UART> TRAINING_RUNS=10 STRESS_SECONDS=1800
make ddr-test-report
make benchmark-report
```

These commands describe the required final coverage, not an instruction to duplicate a completed matching qualification run. Final `ALL` benchmark batches cover six profiles per memory mode, each with validation plus three scored repetitions. Preserve algorithm data/BSS and stack in SRAM; selected main RAM holds code/read-only data. DDR bandwidth is a separate measurement from CoreMark.

## Phase 5: tests, reports, and compatibility

Host verification must cover:

- Ordered profile registration, Linux variant settings, mode-specific maxperf resolution, reproducible candidate arguments, actual ISA/compiler flags, memory bounds, READ_FIRST behavior, and generated clocks/maps/GPIO compatibility.
- Correct CPU/cache/RTL metadata and fingerprints; stale/wrong mode, selection, or provisional candidate artifacts must be rejected.
- Promotion rejects missing or changed evidence, missing validation/repetitions, a non-winning score in either mode, failed DDR integrity, and incomplete qualification. Preserve the first-four selection checks.
- Runner/parser behavior for training failure, corruption, timeout/disconnect/interruption, uncertain continuation, insufficient coverage/duration, and cache sizes that differ from the old 4 KiB assumption.
- Historical three- and four-profile batches, new five-profile intermediate batches, and final six-profile batches are validated against **their recorded membership**, not the current registry size. Missing/duplicate/unknown/inconsistent entries still fail. Legacy missing memory mode means `onchip`.

Run required host checks and `git diff --check`. Perform meaningful integration and board tests; do not substitute implementation-mirroring tests for actual synthesis or measurements.

Update README/command documentation, `docs/performance.md`, `docs/performance/results.json`, `docs/coremark-summary.md`, and the DDR report/results. Include:

- All six profiles across both modes with actual configuration/ISA/cache sizes, resource use, actual operating clocks, timing results, validation, means/spreads, and evidence links.
- Separate on-chip and DDR3 maxperf selection records and gains over their fresh matching baselines. Never pool scores across modes/configurations or average historical and current sessions together.
- A table of all 26 candidates, rejected-build/test reasons, selection/tie-break results, pinned generation details, and artifact hashes.
- DDR training reliability, full-range coverage, cache bypass/visibility outcomes, stress duration, error counts, and bandwidth. Label code-in-DDR/data-in-SRAM CoreMark placement accurately.
- Linux CPU capability distinguished from deferred OS boot. Estimated Fmax must not be presented as the measurement clock or proof of a performance gain.

Independently revalidate raw UART hashes, TX captures, firmware/bitstream identities, expected CoreMark CRCs, timer-derived scores, linked extents, selection records, and DDR coverage/duration before declaring completion. Preserve failed captures and exclude them from passing aggregates.

## Final acceptance and delivery

Completion requires: both new profiles fit and meet timing in both modes; all four new profile/memory combinations pass public-path validation and three repetitions; maxperf strictly beats fresh matching fourth-profile baselines in both modes; both selected DDR additions pass ten training cycles and full-range/30-minute qualification; final ALL batches and historical reporting remain valid; and source/configuration/evidence records reproduce the selected builds.

Budget at least one hour for the two new profiles' DDR stress alone, plus candidate synthesis, 26 candidate evaluations, training/full-range tests, public-path verification, and final comparison batches. Do not shorten acceptance runs to save time. If fixing shared DDR integration invalidates older qualification, retest affected configurations and reflect that in the reports.

Deliver a concise final report linking implementation entrypoints, selection records, candidate results, and UART evidence. State the two selected configurations and measured gains, build/timing and hardware qualification outcomes, exact reproduction commands, and any unmet requirement with its observed blocker. A connected board is the working assumption; if hardware access or functionality actually fails, provide the evidence and completed work rather than claiming success.
