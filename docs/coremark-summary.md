# CoreMark and DDR3 handoff summary

Updated 2026-10-06T01:23:01.772935+00:00. Replacement board: **replacement Tang Primer 20K Dock attached 2026-10-05**. DDR3 qualification status: **partial (4/5 profiles qualified)**. See the [replacement-board qualification report](ddr3/replacement-board-qualification-20261005.md) for profile outcomes and evidence IDs.

All five on-chip profiles and 4 of five DDR3 profiles meet the recorded routed timing gates. DDR3 `minimal` is blocked: Routed timing misses the 48 MHz sys_clk requirement: worst setup slack -0.911 ns, one violated endpoint, despite estimated Fmax 50.849 MHz. The path is gw2ddrphy_dqs_hold_1_s0/Q to DQS_1/HOLD from sys_clk rising to sys2x_clk falling. No DDR hardware run or DDR CoreMark was started for this profile. The configured operating clocks are 48 MHz system and, for DDR3, 96 MHz CK. Estimated Fmax values below come from place-and-route and are not the operating clock.

## CoreMark comparison

Each score is a validated mean ± observed spread from three scored repetitions. DDR3 CoreMark passed for 4 profiles that passed full DDR qualification; profiles without full DDR acceptance have no DDR score.

| CPU profile | On-chip CoreMark mean ± spread (runs) | DDR3 CoreMark mean ± spread (runs) | DDR3 CoreMark status |
|---|---:|---:|---|
| `minimal` | 16.588341 ± 0.000000 (3) | — | not_measured |
| `lite` | 50.427512 ± 0.000000 (3) | 49.757130 ± 0.000000 (3) | passed |
| `standard` | 109.127482 ± 0.000000 (3) | 106.238365 ± 0.000000 (3) | passed |
| `performance` | 119.323916 ± 0.000000 (3) | 115.798154 ± 0.000000 (3) | passed |
| `linux` | 109.127481 ± 0.000000 (3) | 106.238341 ± 0.000000 (3) | passed |

## Build resources and timing

| CPU profile | Memory | Build | LUT | ALU | Registers | BSRAM | Operating clock | Worst setup slack | Estimated Fmax |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `minimal` | onchip | passed | 2247 | 300 | 1382 | 38 / 46 | 48.000 MHz | 5.752 ns | 66.307 MHz |
| `minimal` | ddr3 | unavailable / evidence mismatch | — | — | — | — / — | — MHz | — ns | — MHz |
| `lite` | onchip | passed | 3127 | 443 | 1639 | 40 / 46 | 48.000 MHz | 2.920 ns | 55.823 MHz |
| `lite` | ddr3 | passed | 6366 | 789 | 3497 | 46 / 46 | 48.000 MHz | 0.183 ns | 48.424 MHz |
| `standard` | onchip | passed | 3452 | 479 | 1889 | 46 / 46 | 48.000 MHz | 6.471 ns | 69.628 MHz |
| `standard` | ddr3 | passed | 7916 | 825 | 3807 | 46 / 46 | 48.000 MHz | 0.742 ns | 53.607 MHz |
| `performance` | onchip | passed | 3656 | 521 | 2060 | 46 / 46 | 48.000 MHz | 5.325 ns | 64.482 MHz |
| `performance` | ddr3 | passed | 9094 | 867 | 5221 | 46 / 46 | 48.000 MHz | 0.006 ns | 55.148 MHz |
| `linux` | onchip | passed | 4615 | 702 | 2709 | 46 / 46 | 48.000 MHz | 2.448 ns | 54.390 MHz |
| `linux` | ddr3 | passed | 8805 | 1048 | 4627 | 46 / 46 | 48.000 MHz | 0.111 ns | 50.321 MHz |

## CPU candidate selection

At 48 MHz, `dynamic_target` is selected as the public `performance` profile. Its candidate-evaluation mean was 119.323913 CoreMark against a fresh standard mean of 109.127482, a 10.196432 CoreMark (9.34%) increase. Both generated candidates passed build, timing, validation, and three scored runs; neither was rejected. The candidate table records both candidates' actual scores and timing results.

| Candidate | Build and timing | Mean CoreMark (3 runs) | Difference vs fresh standard | Selection |
|---|---|---:|---:|---|
| `dynamic` | ready_for_board_measurement; 3540 LUT, 46 BSRAM, 5.245 ns slack | 111.487554 | 2.360072 (2.16%) | viable; lower measured score |
| `dynamic_target` | ready_for_board_measurement; 3656 LUT, 46 BSRAM, 5.325 ns slack | 119.323913 | 10.196432 (9.34%) | selected as public `performance` |

The public `performance` profile uses the previously selected `dynamic_target` CPU. Its earlier CPU-selection evaluation remains historical; the replacement-board on-chip and DDR3 scores above use fresh 48 MHz runs.

## DDR3 training, integrity, and stress

DDR3 acceptance status: **partial (4/5 qualified)**. Training and stress by profile:

- `minimal`: not qualified — Routed timing misses the 48 MHz sys_clk requirement: worst setup slack -0.911 ns, one violated endpoint, despite estimated Fmax 50.849 MHz. The path is gw2ddrphy_dqs_hold_1_s0/Q to DQS_1/HOLD from sys_clk rising to sys2x_clk falling. No DDR hardware run or DDR CoreMark was started for this profile.
- `lite`: 10/10 training/smoke runs, 128 MiB tested, stress 1800.250/1800 s, 0 errors, cached/uncached visibility passed
- `standard`: 10/10 training/smoke runs, 128 MiB tested, stress 1800.052/1800 s, 0 errors, cached/uncached visibility passed
- `performance`: 10/10 training/smoke runs, 128 MiB tested, stress 1800.059/1800 s, 0 errors, cached/uncached visibility passed
- `linux`: 10/10 training/smoke runs, 128 MiB tested, stress 1800.052/1800 s, 0 errors, cached/uncached visibility passed

 Separate console probes from the previous board are diagnostic evidence only and do not count as replacement-board acceptance runs. [`lite` DDR session](../build/ddr3/ddr-test-sessions/20261005T215520.872922Z-ddr3-lite/session.json), [`standard` DDR session](../build/ddr3/ddr-test-sessions/20261005T223316.301737Z-ddr3-standard/session.json), [`performance` DDR session](../build/ddr3/ddr-test-sessions/20261005T231023.848982Z-ddr3-performance/session.json), [`linux` DDR session](../build/ddr3/ddr-test-sessions/20261005T234544.924815Z-ddr3-linux/session.json)

## Evidence

[Detailed performance report](performance.md), [machine-readable benchmark results](performance/results.json), [DDR3 training and integrity report](ddr3/report.md), [replacement-board qualification](ddr3/replacement-board-qualification-20261005.md), [CPU candidate selection](../cpu-profile-selection.json), [candidate build evidence](performance/cpu-evaluation/candidate-build.json), [candidate UART results](performance/cpu-evaluation/evaluations.json), [on-chip `minimal` timing](<../build/minimal/gateware/impl/pnr/project.tr>), [on-chip `lite` timing](<../build/lite/gateware/impl/pnr/project.tr>), [on-chip `standard` timing](<../build/standard/gateware/impl/pnr/project.tr>), [on-chip `performance` timing](<../build/performance/gateware/impl/pnr/project.tr>), [on-chip `linux` timing](<../build/linux/gateware/impl/pnr/project.tr>), [DDR3 `minimal` timing](<../build/ddr3/minimal/gateware/impl/pnr/project.tr>), [DDR3 `lite` timing](<../build/ddr3/lite/gateware/impl/pnr/project.tr>), [DDR3 `standard` timing](<../build/ddr3/standard/gateware/impl/pnr/project.tr>), [DDR3 `performance` timing](<../build/ddr3/performance/gateware/impl/pnr/project.tr>), [DDR3 `linux` timing](<../build/ddr3/linux/gateware/impl/pnr/project.tr>)
