# CoreMark and DDR3 handoff summary

Updated 2026-10-02. The current-source `PROFILE=ALL MEMORY=onchip` batch passed validation plus three scored runs for all five public profiles. A false post-flush SRAM expectation for the no-D-cache `minimal` and `lite` profiles was corrected before this final batch. DDR3 board acceptance remains incomplete because diagnostic BIOS Memtest reproduces persistent data corruption.

All eight profile/memory builds passed Gowin synthesis, placement, routing, timing, and resource checks. The configured operating clocks are 48 MHz system and, for DDR3, 96 MHz CK. Estimated Fmax values below come from place-and-route and are not the operating clock.

## CoreMark comparison

Each score is a validated mean ± observed spread from three scored repetitions. DDR3 CoreMark remains unmeasured because DDR training did not pass.

| CPU profile | On-chip CoreMark mean ± spread (runs) | DDR3 CoreMark mean ± spread (runs) | DDR3 CoreMark status |
|---|---:|---:|---|
| `minimal` | 16.588341 ± 0.000000 (3) | — | not_measured |
| `lite` | 50.427512 ± 0.000000 (3) | — | not_measured |
| `standard` | 109.127482 ± 0.000000 (3) | — | not_measured |
| `performance` | 119.323916 ± 0.000000 (3) | — | not_measured |
| `linux` | 109.127481 ± 0.000000 (3) | — | not_measured |

## Build resources and timing

| CPU profile | Memory | Build | LUT | ALU | Registers | BSRAM | Operating clock | Worst setup slack | Estimated Fmax |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `minimal` | onchip | passed | 2247 | 300 | 1382 | 38 / 46 | 48.000 MHz | 5.752 ns | 66.307 MHz |
| `minimal` | ddr3 | passed | 5549 | 664 | 3241 | 46 / 46 | 48.000 MHz | 0.502 ns | 49.185 MHz |
| `lite` | onchip | passed | 3127 | 443 | 1639 | 40 / 46 | 48.000 MHz | 2.920 ns | 55.823 MHz |
| `lite` | ddr3 | passed | 6422 | 806 | 3520 | 46 / 46 | 48.000 MHz | 0.277 ns | 49.576 MHz |
| `standard` | onchip | passed | 3452 | 479 | 1889 | 46 / 46 | 48.000 MHz | 6.471 ns | 69.628 MHz |
| `standard` | ddr3 | passed | 7891 | 843 | 3830 | 46 / 46 | 48.000 MHz | 0.548 ns | 50.573 MHz |
| `performance` | onchip | passed | 3656 | 521 | 2060 | 46 / 46 | 48.000 MHz | 5.325 ns | 64.482 MHz |
| `performance` | ddr3 | passed | 9116 | 885 | 5300 | 46 / 46 | 48.000 MHz | 0.530 ns | 49.252 MHz |
| `linux` | onchip | passed | 4615 | 702 | 2709 | 46 / 46 | 48.000 MHz | 2.448 ns | 54.390 MHz |
| `linux` | ddr3 | passed | 8860 | 1066 | 4650 | 46 / 46 | 48.000 MHz | 0.138 ns | 48.321 MHz |

## CPU candidate selection

At 48 MHz, `dynamic_target` is selected as the public `performance` profile. Its candidate-evaluation mean was 119.323913 CoreMark against a fresh standard mean of 109.127482, a 10.196432 CoreMark (9.34%) increase. Both generated candidates passed build, timing, validation, and three scored runs; neither was rejected. The candidate table records both candidates' actual scores and timing results.

| Candidate | Build and timing | Mean CoreMark (3 runs) | Difference vs fresh standard | Selection |
|---|---|---:|---:|---|
| `dynamic` | ready_for_board_measurement; 3540 LUT, 46 BSRAM, 5.245 ns slack | 111.487554 | 2.360072 (2.16%) | viable; lower measured score |
| `dynamic_target` | ready_for_board_measurement; 3656 LUT, 46 BSRAM, 5.325 ns slack | 119.323913 | 10.196432 (9.34%) | selected as public `performance` |

The selected public profile was subsequently checked as a public build: fresh validation and three Dock runs passed, with mean 119.323916 CoreMark and zero observed spread. Its capture and image identities are in [the detailed report](performance.md).

## Bounded maxperf candidate matrix

The on-chip matrix has outcomes for all eight candidates against the fresh 119.3239155434707 CoreMark `performance` baseline. Candidate 8 measured 119.798647 CoreMark (+0.398%); candidate 7 tied the baseline within 0.000002 CoreMark; candidate 6 timed out before application markers. The candidate captures predate the final no-D-cache SRAM-check correction, so they remain supplemental evidence against their original source identity. DDR3 kept all 18 build outcomes (4 routed, 2 timing failures, 6 placement failures, 6 logic-limit failures), with no scoring because reference integrity failed. No `maxperf` selection was published.

See the [combined matrix record](performance/maxperf-evaluation/20261001-combined-matrix-summary.json) for candidate IDs, per-candidate results, and retained evidence paths.

## DDR3 training, integrity, and stress

DDR3 acceptance status: **failed**. The latest acceptance batch recorded `linux` at 0/10 accepted training/smoke runs and 0/1800 measured stress seconds. A separate diagnostic-only image did train both lanes but failed BIOS Memtest at `0x400b64f4`; six subsequent read-only checks reproduced the same bit-20 mismatch through cached and uncached aliases. Training alone therefore does not clear the integrity gate.

- `linux`: 0/10 accepted training/smoke runs (1 attempted), stress 0/1800 s

 Full-range coverage is not established (0 of 256 MiB accepted), cached/uncached visibility tests did not run, measured stress was 0/1800 seconds, and read/write bandwidth was not measured. Separate console probes are diagnostic evidence only and do not count as acceptance runs. [linux acceptance UART capture](<../docs/ddr3/evidence/20261001T201828.617667Z-ddr3-linux/training-01.uart.bin>)

## Evidence

[Detailed performance report](performance.md), [machine-readable benchmark results](performance/results.json), [DDR3 training and integrity report](ddr3/report.md), [CPU candidate selection](../cpu-profile-selection.json), [candidate build evidence](performance/cpu-evaluation/candidate-build.json), [candidate UART results](performance/cpu-evaluation/evaluations.json), [on-chip `minimal` timing](<../build/minimal/gateware/impl/pnr/project.tr>), [on-chip `lite` timing](<../build/lite/gateware/impl/pnr/project.tr>), [on-chip `standard` timing](<../build/standard/gateware/impl/pnr/project.tr>), [on-chip `performance` timing](<../build/performance/gateware/impl/pnr/project.tr>), [on-chip `linux` timing](<../build/linux/gateware/impl/pnr/project.tr>), [DDR3 `minimal` timing](<../build/ddr3/minimal/gateware/impl/pnr/project.tr>), [DDR3 `lite` timing](<../build/ddr3/lite/gateware/impl/pnr/project.tr>), [DDR3 `standard` timing](<../build/ddr3/standard/gateware/impl/pnr/project.tr>), [DDR3 `performance` timing](<../build/ddr3/performance/gateware/impl/pnr/project.tr>), [DDR3 `linux` timing](<../build/ddr3/linux/gateware/impl/pnr/project.tr>)

The FPGA was left in safe idle after the final on-chip batch by explicit FPGA reset. Pre- and post-reset UART observations were empty, JTAG detection passed, and no flash programming occurred. [Final recovery evidence](performance/maxperf-evaluation/recovery/20261002T004311Z-final-onchip/recovery.json).

Later [DDR write-disturbance probes](ddr3/write-disturbance.md) reproduced bit-20 and bit-31 errors at untouched victims after writes to another row. Conservative row timing and ODT-low did not fix them; public configurations and benchmark scores are unchanged. DDR remains unqualified. The board was reset to safe idle after the probes; [current recovery evidence](ddr3/diagnosis/20261002T020045.394626Z-integrity-recovery/recovery.json).
