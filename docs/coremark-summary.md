# CoreMark and DDR3 handoff summary

Updated 2026-10-01T01:54:37.644735+00:00. The CPU candidate selection and DDR3 acceptance are reported separately: on-chip CPU performance is measured and accepted, while DDR3 board acceptance is incomplete after read-leveling failure.

All eight profile/memory builds passed Gowin synthesis, placement, routing, timing, and resource checks. The configured operating clocks are 48 MHz system and, for DDR3, 96 MHz CK. Estimated Fmax values below come from place-and-route and are not the operating clock.

## CoreMark comparison

Each score is a validated mean ± observed spread from three scored repetitions. DDR3 CoreMark remains unmeasured because DDR training did not pass.

| CPU profile | On-chip CoreMark mean ± spread (runs) | DDR3 CoreMark mean ± spread (runs) | DDR3 CoreMark status |
|---|---:|---:|---|
| `minimal` | 16.588341 ± 0.000000 (3) | — | not_measured |
| `lite` | 50.427512 ± 0.000000 (3) | — | not_measured |
| `standard` | 109.127482 ± 0.000000 (3) | — | not_measured |
| `performance` | 119.323916 ± 0.000000 (3) | — | not_measured |

## Build resources and timing

| CPU profile | Memory | Build | LUT | ALU | Registers | BSRAM | Operating clock | Worst setup slack | Estimated Fmax |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `minimal` | onchip | passed | 2247 | 300 | 1382 | 38 / 46 | 48.000 MHz | 5.752 ns | 66.307 MHz |
| `minimal` | ddr3 | passed | 5412 | 664 | 3244 | 46 / 46 | 48.000 MHz | 0.155 ns | 48.360 MHz |
| `lite` | onchip | passed | 3127 | 443 | 1639 | 40 / 46 | 48.000 MHz | 2.920 ns | 55.823 MHz |
| `lite` | ddr3 | passed | 6455 | 806 | 3523 | 46 / 46 | 48.000 MHz | 0.005 ns | 48.010 MHz |
| `standard` | onchip | passed | 3452 | 479 | 1889 | 46 / 46 | 48.000 MHz | 6.471 ns | 69.628 MHz |
| `standard` | ddr3 | passed | 7974 | 842 | 3833 | 46 / 46 | 48.000 MHz | 0.653 ns | 59.787 MHz |
| `performance` | onchip | passed | 3656 | 521 | 2060 | 46 / 46 | 48.000 MHz | 5.325 ns | 64.482 MHz |
| `performance` | ddr3 | passed | 9128 | 884 | 5303 | 46 / 46 | 48.000 MHz | 0.450 ns | 49.061 MHz |

## CPU candidate selection

At 48 MHz, `dynamic_target` is selected as the public `performance` profile. Its candidate-evaluation mean was 119.323913 CoreMark against a fresh standard mean of 109.127482, a 10.196432 CoreMark (9.34%) increase. Both generated candidates passed build, timing, validation, and three scored runs; neither was rejected. The candidate table records both candidates' actual scores and timing results.

| Candidate | Build and timing | Mean CoreMark (3 runs) | Difference vs fresh standard | Selection |
|---|---|---:|---:|---|
| `dynamic` | ready_for_board_measurement; 3540 LUT, 46 BSRAM, 5.245 ns slack | 111.487554 | 2.360072 (2.16%) | viable; lower measured score |
| `dynamic_target` | ready_for_board_measurement; 3656 LUT, 46 BSRAM, 5.325 ns slack | 119.323913 | 10.196432 (9.34%) | selected as public `performance` |

The selected public profile was subsequently checked as a public build: fresh validation and three Dock runs passed, with mean 119.323916 CoreMark and zero observed spread. Its capture and image identities are in [the detailed report](performance.md).

## DDR3 training, integrity, and stress

DDR3 acceptance status: **failed**. Training reliability:

- `minimal`: 0/1 accepted training/smoke runs (1 attempted), stress 0/60 s

The latest `minimal` run stopped before diagnostic firmware start: RuntimeError: DDR training did not pass before BIOS console; serialboot recovery was refused. The same UART capture records `SDRAM_TRAINING_RESULT status=failed phase=leveling` and `SDRAM_READ_LEVELING_LANE module=0 dq=0 bitslip=0 status=failed window_start=-1 window_length=0`. The runner issued no DDR memory test after this failure. Full-range coverage is not established (0 of 256 MiB accepted), cached/uncached visibility tests did not run, measured stress was 0/1800 seconds, and read/write bandwidth was not measured. Separate console probes are diagnostic evidence only and do not count as acceptance runs. [minimal acceptance UART capture](<../docs/ddr3/evidence/20261001T014933.087634Z-ddr3-minimal/training-01.uart.bin>)

## Evidence

[Detailed performance report](performance.md), [machine-readable benchmark results](performance/results.json), [DDR3 training and integrity report](ddr3/report.md), [CPU candidate selection](../cpu-profile-selection.json), [candidate build evidence](performance/cpu-evaluation/candidate-build.json), [candidate UART results](performance/cpu-evaluation/evaluations.json), [on-chip `minimal` timing](<../build/minimal/gateware/impl/pnr/project.tr>), [on-chip `lite` timing](<../build/lite/gateware/impl/pnr/project.tr>), [on-chip `standard` timing](<../build/standard/gateware/impl/pnr/project.tr>), [on-chip `performance` timing](<../build/performance/gateware/impl/pnr/project.tr>), [DDR3 `minimal` timing](<../build/ddr3/minimal/gateware/impl/pnr/project.tr>), [DDR3 `lite` timing](<../build/ddr3/lite/gateware/impl/pnr/project.tr>), [DDR3 `standard` timing](<../build/ddr3/standard/gateware/impl/pnr/project.tr>), [DDR3 `performance` timing](<../build/ddr3/performance/gateware/impl/pnr/project.tr>)
