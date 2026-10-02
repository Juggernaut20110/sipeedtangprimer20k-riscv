# Issue resolution and validation plan

Prepared 2026-10-01 from [performance.md](performance.md), [coremark-summary.md](coremark-summary.md), and [ddr3/report.md](ddr3/report.md), with read-only checks of the current runners, metadata, and retained captures. Implementation instructions are in [the Luna xhigh handoff](../LUNA_XHIGH_VALIDATION_HANDOFF.md).

This plan continues the existing Linux/maxperf and DDR3 work. It does not restart CPU selection or expand the bounded tuning matrix. No fixes, synthesis, or board qualification were performed while preparing this plan.

## Observed issues

| Priority | Issue and evidence | Consequence |
|---|---|---|
| P0 | On-chip maxperf candidate `icache-4096_dcache-2048_rv32imc` timed out with 36 RX bytes, zero TX bytes, and no application completion/halt or confirmed BIOS console. [Evaluation](performance/maxperf-evaluation/20261001T202219.864127Z-onchip-maxperf/evaluation.json) records `stopped_uncertain_hardware_state`. | The recorded board state is uncertain. Establish current ownership and safe idle before more programming. A partial banner does not identify the cause. |
| P1 | Benchmark fingerprint producer and report verifier disagree: `scripts/benchmark_build.py` includes `cpu_variant` and, when present, `cpu_profile_selection`; `scripts/benchmark_report.py:benchmark_fingerprint_matches` omits both. Recomputing the current Linux payload with `cpu_variant` reproduces its stored fingerprint exactly. | Fresh modern captures can be rejected even after fixing stale inputs. This is a concrete verifier defect, separate from genuine stale artifacts. |
| P1 | Current Linux benchmark source hashes differ for `scripts/benchmark_build.py`, `build/linux/software/libbase/libbase.a`, and generated `soc.h`, `csr.h`, and `mem.h`. The latest Linux session's fingerprint and firmware hashes still match benchmark metadata. | Fixing the verifier alone cannot accept this evidence against the current inputs. Preserve old evidence, isolate artifacts, rebuild, and recapture. Trace what changed the public generated inputs. |
| P1 | DDR3 receive training was restored, but all four earlier public profiles and the latest Linux trial fail BIOS Memtest. Full-range diagnostic repeatedly fails walking zeros at `0xc64011ac`, expected `0xfffff7ff`, actual `0x7ffff7ff`; an isolated rewrite passes afterward. [Training handoff](ddr3/training-handoff.md). | DDR3 integrity is the critical path. No DDR3 CoreMark or maxperf scoring is acceptable before the reference qualifies. Training-window size is insufficient evidence. |
| P2 | Five on-chip maxperf candidates completed measurements, all below the fresh `performance` baseline of 119.3239155434707; candidate six failed startup, seven/eight remain unmeasured. | Finish the bounded experiment after recovery. A winning candidate is possible but not guaranteed. |
| P2 | DDR3 matrix has four viable builds, two timing failures, six placement failures, and six logic-limit failures. No candidate was scored. | Preserve all 18 outcomes; qualify the reference and measure only candidates passing every required resource/timing check. |
| P2 | Thorough DDR3 qualification and final public comparison batches remain missing. The latest DDR report shows Linux only; earlier failed batches remain in results/history. | Finish profile-specific acceptance and report historical versus current results clearly. Neither a runner `passed` field nor host tests establish acceptance. |

## Execution order and gates

| Step | Work | Exit gate |
|---|---|---|
| 1. Preserve and recover | Inventory dirty/untracked work, active processes, evidence, UART ownership, and current board state. Preserve the stopped batch and obtain a documented idle/stop state. | Exclusive board owner; recovery evidence; no uncertain workload continuation. |
| 2. Repair evidence identity | Unify/version modern fingerprint payloads, retain explicit legacy handling, isolate public/candidate/validation artifacts, and save immutable qualification inputs. Audit DDR preflight and promotion for rebuilds changing the qualified bitstream. | Builder-to-verifier round trip passes; mutations and wrong configurations fail; historical records remain verifiable where original artifacts exist. |
| 3. Diagnose DDR corruption | Use on-chip diagnostic execution and the controller-bypassing alias. Trace reproducible first-error writes/reads, masks, burst beats, controller/PHY timing, addressing, and refresh. Convert a demonstrated fix into a setup-applied patch. | Routed timing passes; BIOS Memtest and targeted/full-range integrity pass without masking errors or weakening tests. |
| 4. Close on-chip testing | Diagnose compressed candidate startup; remeasure Linux; run the full eight-candidate maxperf batch against a fresh matched baseline. | Linux has accepted validation plus three scores. All eight candidates have evidence-backed outcomes; winner or exhaustive no-win result. |
| 5. Qualify DDR reference and profiles | Qualify final `performance` artifacts before DDR scoring. Run ten training/smoke cycles, full-range phases, cache visibility/bypass, and >=1800 s measured stress for every affected public profile. | Zero errors and independently checked exact-artifact evidence for all five public profiles; candidate scoring gate opens only after reference acceptance. |
| 6. Complete DDR tuning/promotion | Refresh builds affected by a shared fix, retain the full matrix, score viable candidates, rank and thoroughly qualify any winner, and verify its intended public identity. | Strict gain in both memory modes plus final-identity qualification before registering `maxperf`; otherwise explicit unmet sixth-profile goal. |
| 7. Final audit and reports | Run host/generated checks, final public build/comparison and CoreMark batches, then regenerate reports and independently audit evidence. | Consistent reports, valid hashes/CRC/timers/coverage, recorded profile membership, and a precise completion or blocker statement. |

Steps 3 and 4 can be interleaved within one agent's work, but all JTAG/UART operations must remain sequential. Freeze implementation and run host checks before the final hardware batches; do not change fingerprinted inputs underneath measurements.

## Completion requirements

- Keep 48 MHz system / 96 MHz DDR CK, on-chip defaults, approved memory budgets, READ_FIRST RAM behavior, pinned CoreMark algorithms and compiler policy, and SRAM-only programming. Linux OS boot remains deferred.
- Every final public profile must build/route in both modes and pass all required setup, hold, recovery, removal, clock/map, and linked-memory checks.
- Each final public profile/memory pair needs one accepted validation-seed run and three accepted scored runs. Scores use full-resolution timer ticks and mode-matched configuration identities.
- Each affected final DDR3 public profile needs ten successful fresh reconfiguration/training/smoke cycles, all required 256 MiB integrity phases, uncached bypass and cached visibility checks, and at least 1800 measured stress seconds with zero errors and bandwidth evidence. Reuse qualification only for matching final artifacts.
- `maxperf` stays private unless both modes strictly beat their fresh `performance` baselines and their final identities qualify. If the complete bounded matrix yields no gain, finish the evaluation and document the unmet target; do not broaden tuning or manufacture a selection.
- Preserve failed captures and historical records. Do not pool historical/current scores or on-chip/DDR3 scores. Report the actual final board state.

Allow at least 150 minutes of stress for five public DDR3 profiles, or 180 minutes if a sixth profile qualifies, plus full-range scans, training, builds, and benchmarks. Exact matching prior qualification may avoid duplicate runs; failed/diagnostic-only evidence cannot.

## Deliverables

Reproducible code/patch fixes; regression and generated-design results; complete 26-candidate outcomes; final per-profile build, CoreMark, and DDR3 evidence; qualified selection records if warranted; and updated README, all three source reports, and their JSON results. Any remaining hardware/access/no-win blocker must identify the failed gate, exact evidence, and the next action required.
