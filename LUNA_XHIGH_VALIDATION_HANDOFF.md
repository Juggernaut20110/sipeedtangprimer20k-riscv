# Luna xhigh: repair issues and finish validation

> Geometry correction, 2026-10-02 UTC: the fitted H5TQ1G63EFR has 128 MiB and 13 row bits. Earlier 256 MiB assumptions and full-capacity claims in this historical document are superseded by the [correction and hardware retest](docs/ddr3/hynix-geometry-correction.md). Errors below 128 MiB remain reproducible.

## Assignment and scope

Use **Luna (`gpt-6-luna`) with extra-high (`xhigh`) reasoning** in `/home/user/git/fpga/sipeedtangprimer20k-riscv` to implement fixes and complete testing. Read [the prioritized plan](docs/validation-plan.md) and the three source reports: [performance](docs/performance.md), [CoreMark summary](docs/coremark-summary.md), and [DDR3](docs/ddr3/report.md).

Continue the existing work; do not return only another plan. This handoff updates the starting-state/checkpoint sections of [LINUX_MAXPERF_HANDOFF.md](LINUX_MAXPERF_HANDOFF.md) and [PERFORMANCE_DDR3_HANDOFF.md](PERFORMANCE_DDR3_HANDOFF.md), whose bounded tuning, methodology, and acceptance requirements remain applicable. Read [DDR3 training-handoff.md](docs/ddr3/training-handoff.md) before further PHY experiments. The older handoffs' three/four-profile and zero-window starting states are historical.

At preparation, HEAD is `bf81c39526abbf61261626d0d6a0767f098e4abd` with extensive uncommitted work. Five public profiles exist in order: `minimal`, `lite`, `standard`, `performance`, `linux`. `maxperf` is private and no accepted selection exists. All five have routed builds in both modes at 48 MHz; this does not establish hardware qualification. Linux is a bare-metal-tested Linux-capable RV32IMA CPU; kernel/bootloader/rootfs/device-tree/boot-to-shell work remains deferred.

Preserve existing changes, captures, build outputs, and `cpu-profile-selection.json`. Do not reset/clean the checkout, rerun fourth-profile selection, commit, push, flash-program, raise clocks, or expand the approved 26-candidate matrix. Preserve memory budgets and READ_FIRST fixes. Dependency changes must be repository-owned patches applied by setup and included in identities. Do not edit upstream CoreMark algorithms, reduce integrity coverage, or shorten acceptance stress.

This document specifies the implementing agent's configuration; preparing it did not start another agent/chat or execute hardware tests. When the user invokes this handoff, normal source fixes, builds, and SRAM-only tests are its work scope. Keep one owner of all JTAG/UART operations and respect any active workload in the shared checkout.

## First checkpoint: custody, environment, and recovery

1. Read applicable repository instructions; record `git status --short`, revision, active build/programmer/test processes, and device owners. Preserve a manifest/checkpoint of existing evidence before rebuilding reused paths. Use the pinned `.venv`/`.tools` environment through project commands; start with `make doctor`.
2. Rediscover JTAG and the Dock UART. Previously used: `/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0` at 115200 baud, then `/dev/ttyUSB1`. Verify the current mapping; require explicit `PORT`. Do not assume historical connection/access statements establish today's state.
3. The latest on-chip evaluation, [20261001T202219.864127Z](docs/performance/maxperf-evaluation/20261001T202219.864127Z-onchip-maxperf/evaluation.json), stopped on candidate `icache-4096_dcache-2048_rv32imc`. It received 36 bytes of an incomplete BIOS banner, transmitted zero bytes, and timed out after 45 seconds. Neither end/halt nor idle-console proof was captured. The earlier performance-DDR BIOS restoration is not the latest recorded state.
4. Establish exclusive ownership and inspect passively for a completed/halted workload or confirmed BIOS prompt. If still uncertain, use an explicit documented stop/reset recovery that establishes execution has stopped before subsequent programming; preserve the interrupted trial as failed and record recovery evidence. If such recovery is unavailable, stop board work and report the concrete required action. Never bypass `batch_continuation_safe` or pretend timeout means idle.
5. For actual sandbox/device/tool failures, use the execution environment's supported escalation for this authorized operation; record exact errors if access remains unavailable. Continue independent host work. Do not message another user-owned chat without authorization.

## Repair identity and artifact lifecycle first

Entry points: `scripts/benchmark_build.py`, `scripts/benchmark_report.py`, `scripts/benchmark_run.py`, `scripts/build.py`, `scripts/ddr_test_build.py`, `scripts/ddr_test_run.py`, `scripts/ddr_test_report.py`, `scripts/maxperf_run.py`, and `gateware/profile_selection.py`.

Two distinct problems were confirmed by read-only inspection:

- **Fingerprint payload mismatch.** The builder hashes `cpu_variant`, and optionally `cpu_profile_selection`. The report verifier omits both. For current Linux metadata, the verifier-shaped payload hashes to `8ab3462bcad75f7641ecd44c453341b54d3c072e0630fccf9e4a4a95aa415c5e`; adding `cpu_variant` produces stored `b3be35e7ddbe2eac0f9120430e6826f2ecd0899af7ac52195fb210d8f38ec5c0`. Centralize a canonical modern payload or equivalent shared implementation, including actual CPU/memory/selection identities. Version or explicitly recognize historical schemas; adding fields indiscriminately would invalidate older captures.
- **Genuine changed inputs.** Current Linux benchmark metadata records different hashes from the files now present for `scripts/benchmark_build.py`, `build/linux/software/libbase/libbase.a`, and generated `soc.h`, `csr.h`, `mem.h`. The latest session `20261001T201134.365368Z-onchip-linux` still matches the metadata's fingerprint and firmware SHA values; its binaries also match. The earlier Linux session has different firmware/fingerprint values. Do not accept either just by editing metadata or relaxing hash checks. Trace which build/test path rewrote inputs, repair isolation, then rebuild and obtain fresh captures against final sources.

Keep `build/<profile>/`, `build/ddr3/<profile>/`, candidate outputs, `build/validation/`, and provisional verification outputs independent. Inspect shared dependency-generated headers/libraries and mutable CPU integration state as possible contributors; their responsibility has not been established. Final host/generated checks must not silently rewrite qualified public inputs.

Retain a self-contained, immutable evidence bundle for each accepted session/evaluation: the hashed build and firmware manifests, configuration/selection, generated headers, relevant source/patch identities, RTL, firmware/bitstream, routed resource/timing reports, and raw RX/TX captures. Save artifacts before reused build paths are overwritten. Historical evidence should remain independently auditable against its original artifacts; current load/run paths must still reject stale or wrong configurations. Never reconstruct missing historical provenance by relabeling today's files.

Audit these lifecycle risks in the current maxperf flow:

- `ddr_baseline_integrity_gate()` qualifies `performance`, then `run_evaluation()` force-rebuilds the baseline. Qualification must identify the bitstream actually scored; rebuilding after qualification requires equivalence proven by hashes or requalification.
- A DDR candidate gets thorough qualification under `standard`; its provisional `maxperf` rebuild currently gets only smoke testing. Different public-identity placement/bitstream cannot inherit thorough qualification solely from equal CPU RTL. Thoroughly qualify the final bitstream intended for acceptance.
- Final selection publication and later public builds must not change identities underneath accepted evidence. Verify the final registry/load/report paths and retain explicit provisional records until both modes pass.
- DDR candidate/preflight full-trial timeouts are currently 2400 seconds, versus the public runner's 14400-second default. Allow full-range scan time **plus** 1800 stress seconds and startup overhead. Choose an evidence-based bound or progress-aware timeout; do not turn a long valid scan into an uncertain timeout or shorten stress to fit.

Add meaningful regression coverage: modern builder/verifier round trips for Linux, selected performance, and provisional/accepted maxperf in both modes; legacy-schema preservation; changed sources/headers/RTL/selection/firmware/bitstream/RX/TX rejection; and qualification tied to the final identity. Test recorded three/four/five/six-profile batch membership independently of today's registry. Retain legacy missing-memory-mode interpretation as `onchip`.

## Fix DDR3 integrity before DDR scoring

Use `scripts/ddr_diagnose.py`, `scripts/ddr_read_sweep.py`, the existing probe helpers, `firmware/ddrtest/ddr_test.c`, `gateware/soc.py`, and repository PHY/BIOS patches. Consult pinned `.deps/litedram/litedram/phy/gw2ddrphy.py`, controller sources, and local Gowin primitive models when tracing actual behavior.

The current DLL-off receive patch changes alternating READ slots, uses RCLKSEL 3, PHY latency 11 and crossbar latency 12, with CL6/CWL6 unchanged. It restored training in twelve earlier captures, but all public BIOS tests still reported corruption. Do not restart the solved zero-window search without new evidence.

Known signatures and useful discriminators:

- PRNG failure at cached `0x400b64f4` / uncached `0xc00b64f4`: expected `0xb4de6cd9`, actual `0xb4ce6cd9`, XOR `0x00100000`.
- Full-range walking-ones phase covered 256 MiB with zero errors; walking zeros repeatedly failed at `0xc64011ac`: expected `0xfffff7ff`, actual `0x7ffff7ff`, XOR `0x80000000`. Later phases/stress were never reached.
- Wrong values persisted through cached/uncached reads and read-delay offsets. Isolated rewrites at restored delay corrected them. This favors corruption stored during a long write sweep or subsequent disturbance, but does not prove a particular faulty component.
- Failures involve physical DQ4 and DQ15 on upper 16-bit beats; a single broken DQ4 wire is not established. Prior write-delay sweeps also changed receive retiming through DQSW270, so they were not independent write-path isolation. Increasing controller tWR alone did not fix the error.

Reproduce targeted failures with diagnostic code/data/stack entirely within the 16 KiB RAM at `0x20000000`; use the physical alias `0xc0000000` that bypasses both CPU D-cache and LiteDRAM L2. Preserve first-error values before rewrites or delay changes. Use controlled pattern/order/burst-boundary/row-bank/refresh-delay experiments to distinguish write data/mask/serialization, command spacing, addressing, retention, and receive assembly. Add focused internal tracing/simulation where it can observe the actual write/read transaction; instrument/resource changes must get their own identities and routed timing. Keep diagnostic-only experiments out of acceptance totals. Another-board comparison is useful if available, but do not assume a second board or scope exists.

Promote a fix only after the experiment discriminates its cause and the public image passes BIOS Memtest and relevant full-range tests. Reproduce the patch from clean pinned dependencies; preserve DLL-on behavior and existing CDC/clock constraints. Check every required setup, hold, recovery, removal, and clock path. Do not add false paths to conceal functional timing failures. Preserve the BIOS boot-range restriction and complete/flushed UART records.

Useful existing diagnostic commands, after recovery and timing checks:

```sh
python3 scripts/ddr_diagnose.py build --experiment dll-off-integrity
python3 scripts/ddr_diagnose.py probe --probe-kind read-only --port "$DOCK_PORT"
make ddr-test-build PROFILE=minimal
python3 scripts/ddr_diagnose.py full-probe --profile minimal --read-delay-probe --isolated-write-probe --port "$DOCK_PORT"
```

Run direct diagnostic scripts with the pinned project environment, including its tool PATH. The diagnosis CLI currently restricts `--profile` to the first four profiles; Linux/maxperf acceptance uses `ddr_test_*`. Extend the diagnostic CLI only if needed, with explicit identity handling.

## Close Linux and on-chip maxperf testing

Freeze corrected inputs and run host checks before rebuilding final measurement artifacts. Linux on-chip needs a fresh validation plus three scored repetitions accepted by the repaired verifier. Preserve its 4 KiB I/D caches, RV32IMA/ILP32, MMU/supervisor/atomics configuration and shipped RTL identity; do not claim runtime OS capability tests from bare-metal CoreMark.

For candidate-six startup, inspect the compiled BIOS/library ISA flags, reset/trap behavior, image/ROM fit, UART polling/interrupt assumptions, CSR configuration, actual compressed RTL, and timing. Other compressed candidates completed runs, so the ISA feature alone is not a demonstrated cause. Compare against the 4/4 KiB uncompressed control and a known passing compressed build, changing one factor at a time. Do not force-upload firmware without a verified console/startup state.

Finish all eight on-chip candidates with a fresh mode-matched `performance` baseline. The five measured means were 114.129016–117.747854, below 119.3239155434707. Candidate seven is the 4/4 KiB RV32IM reproduction control; it should not be selected just for matching the baseline. Candidate eight is 4/4 KiB RV32IMC. Preserve the stopped evaluation; create a new complete batch after repairs. Reuse earlier trials only if independently verified against identical final sources/configurations/artifacts and an appropriately matched baseline, never as a mixed-session aggregate.

Use the locked generator and full Cartesian products: on-chip I/D caches 2/4 KiB, DDR I-cache 4/8/16 KiB and D-cache 2/4/8 KiB, each RV32IM/RV32IMC. Keep all other locked parameters unchanged. CoreMark algorithm compilation remains `-O2`, no LTO; untimed wrapper remains `-Os`, with existing calibration, CRC/seed/SRAM/cache checks outside timing. Scores use 48 MHz and raw 64-bit elapsed ticks, not estimated Fmax.

## Finish DDR qualification, tuning, and public verification

After the shared integrity fix, rebuild affected DDR public profiles and candidate artifacts. Retain the original matrix as historical evidence and preserve each new build's outcome. Four DDR candidates were viable previously: 4/2 KiB RV32IMC, 4/4 KiB RV32IM control, 4/8 KiB RV32IM, and 4/8 KiB RV32IMC. The last had only +0.017 ns setup slack. Recheck all required timing after the fix; prior viability is not a guarantee. Keep the two timing, six placement, and six logic-limit failures in the 18-entry table.

Before DDR candidate scoring, thoroughly qualify the exact fresh `performance` reference, then measure its validation plus three scores. Measure every viable candidate with successful per-program training/uncached smoke and the same four CoreMark trials. Rank independently per mode by full-precision mean; ties prefer fewer BSRAM, fewer LUTs, then lexical candidate ID. Require a strict gain over the fresh matched baseline.

For **every affected final public DDR profile** (the five existing profiles, plus maxperf if accepted), require:

- Ten fresh SRAM reconfiguration/training/smoke passes; successful BIOS Memtest; both lane bitslip/window records. These are reconfigurations, not cold power cycles.
- Full 268435456-byte integrity phases: walking bits, fixed/inverted, address-derived, PRNG, geometry/alias/boundary checks, byte/halfword neighbor preservation, and cached/uncached visibility. Record actual coverage per phase rather than counting a single pattern as complete qualification.
- Cache maintenance using actual candidate cache sizes, including the 8 KiB L2; uncached accesses must bypass both cache levels.
- At least 1800 **measured** seconds of delayed-readback/refresh stress with changing seeds, zero errors, and read/write bandwidth with transfer bytes, access path, clock, and elapsed ticks.
- Checked diagnostic linked code/data/stack extent within 16 KiB; complete start/phase/failure/end records; matching firmware/bitstream/manifest and RX/TX hashes.

Qualify the final provisional maxperf DDR bitstream thoroughly, then verify validation plus three scores under its intended public identity. Only publish `maxperf-profile-selection.json` and registry entry six when both modes qualify and strictly improve; independently verify the published selection and actual public builds. If the bounded matrix has no winner, preserve five public profiles, deliver a complete no-win result, and mark the sixth-profile objective unmet. Do not widen the matrix or choose a slower/tied candidate to create a sixth profile.

## Final commands and acceptance audit

Set `DOCK_PORT` to the rediscovered stable UART path. The following public commands exist today; execute them at the appropriate gate, not as an unconditional batch across a hardware failure. `PROFILE=ALL` follows the actual registry, so maxperf is included only after accepted promotion.

```sh
make doctor
make test
git diff --check

make benchmark-build MEMORY=onchip PROFILE=linux
make benchmark-run PROFILE=linux MEMORY=onchip PORT="$DOCK_PORT"
make maxperf-build MEMORY=onchip
make maxperf-run MEMORY=onchip PORT="$DOCK_PORT"

# After fixing DDR integrity; stop if reference qualification fails.
make ddr-test-build PROFILE=ALL STRESS_SECONDS=1800
make ddr-test-run PROFILE=ALL PORT="$DOCK_PORT" TRAINING_RUNS=10 STRESS_SECONDS=1800
make maxperf-build MEMORY=ddr3
make maxperf-run MEMORY=ddr3 PORT="$DOCK_PORT"

# After final profile/selection state is fixed.
make benchmark-build MEMORY=onchip PROFILE=ALL
make benchmark-build MEMORY=ddr3 PROFILE=ALL
make compare MEMORY=onchip
make compare MEMORY=ddr3
make benchmark-run PROFILE=ALL MEMORY=onchip PORT="$DOCK_PORT"
make benchmark-run PROFILE=ALL MEMORY=ddr3 PORT="$DOCK_PORT"
make ddr-test-report
make benchmark-report
git diff --check
```

Requalify whenever a final build changes a relevant qualified bitstream/configuration; avoid redundant stress only when exact final evidence matches. Check GPIO demo behavior through the public run path for Linux and accepted maxperf in both modes, then stop it cleanly before the next workload. Extend generated-design validation to accepted maxperf's actual memory budget/configuration; current assertions special-case performance/linux and must not assume a 32 KiB on-chip maxperf BIOS. Update any hard-coded PHY assertion only to match a demonstrated fix.

Independently recalculate raw-capture hashes, firmware image CRC/size/SHA, bitstream and RTL hashes, CoreMark expected CRCs/seeds, `iterations * clock_hz / ticks`, `iterations * 1000000 / ticks` for CoreMark/MHz, repetitions/means/spreads, linked bounds, training lane records, phase coverage, and stress duration. A stored runner `passed` status is insufficient. Do not mutate fingerprinted inputs after final measurements; any later implementation change requires affected retesting.

Update README, `docs/performance.md`, `docs/coremark-summary.md`, `docs/ddr3/report.md`, and both results JSONs. Include all final public profile/memory combinations, complete 26-candidate outcomes, accepted versus diagnostic/stale evidence, actual clocks versus estimated Fmax, exact selections/gains if any, qualification coverage/duration/bandwidth, reproduction commands, and current board state. Keep DDR CoreMark placement explicit: code/read-only data in DDR, algorithm data/BSS/stack in SRAM. Show earlier DDR failures alongside the latest per-profile acceptance rather than implying an absent profile was tested successfully.

Deliver a concise final report linking fixes and evidence and listing every passed or unmet gate. Full success requires repaired identity handling, accepted Linux in both modes, qualified affected DDR profiles, complete bounded evaluations, genuine two-mode maxperf improvement and promotion, and final public-path validation. Exhaustive no-win results or unrecoverable hardware/access faults are valid findings, but leave the corresponding goal explicitly unmet with evidence and a precise next action.
