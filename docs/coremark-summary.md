# CoreMark and DDR3 handoff summary

Updated 2026-10-01. The project now has five public CPU profiles: `minimal`, `lite`, `standard`, `performance`, and `linux`. `maxperf` remains private until separate on-chip and DDR3 winners pass measurement, integrity, and public-identity verification. The Linux-capable CPU builds in both memory modes, but Linux OS boot is deferred.

All five public profiles built and routed for on-chip and DDR3 at a 48 MHz system clock; DDR CK is 96 MHz. The Linux profile uses RV32IMA/ILP32 with MMU, supervisor, and atomic capability from pinned LiteX Linux RTL. Its work here is bare-metal GPIO, diagnostic, and CoreMark integration.

## CoreMark comparison

Accepted means the stored captures and artifact identities revalidated. Linux's raw on-chip session completed four trials, but its current build/source identity no longer matches, so that score is shown for diagnosis and excluded from accepted aggregates.

| CPU profile | On-chip CoreMark mean ± spread | DDR3 CoreMark mean ± spread | Measurement status |
|---|---:|---:|---|
| `minimal` | 16.588341 ± 0.000000 (3) | — | on-chip accepted; DDR3 unmeasured |
| `lite` | 50.427512 ± 0.000000 (3) | — | on-chip accepted; DDR3 unmeasured |
| `standard` | 109.127482 ± 0.000000 (3) | — | on-chip accepted; DDR3 unmeasured |
| `performance` | 119.323916 ± 0.000000 (3) | — | on-chip accepted; DDR3 unmeasured |
| `linux` | 109.127481 ± 0.000000 (3), capture not accepted | — | raw session passed; current artifact identity mismatch; DDR3 BIOS Memtest failed |

The Linux on-chip raw mean is 109.12748097592832 CoreMark. Do not compare it as an accepted result until the profile is rebuilt and captured again with matching hashes. The DDR3 Linux run trained both read lanes, then BIOS Memtest reported one data error during its 2 MiB startup check; no diagnostic upload, full-range coverage, stress, or CoreMark followed. See [the detailed performance report](performance.md) and [DDR3 report](ddr3/report.md).

## Build resources and timing

Builds completed for each profile/memory combination. These estimated Fmax values are place-and-route results; they are separate from the configured operating clocks and do not imply a CoreMark gain.

| CPU profile | Memory | LUT | ALU | Registers | BSRAM | Operating clock | Worst setup slack | Estimated Fmax |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `minimal` | onchip | 2247 | 300 | 1382 | 38 / 46 | 48 MHz | 5.752 ns | 66.307 MHz |
| `minimal` | ddr3 | 5549 | 664 | 3241 | 46 / 46 | 48 MHz | 0.502 ns | 49.185 MHz |
| `lite` | onchip | 3127 | 443 | 1639 | 40 / 46 | 48 MHz | 2.920 ns | 55.823 MHz |
| `lite` | ddr3 | 6422 | 806 | 3520 | 46 / 46 | 48 MHz | 0.277 ns | 49.576 MHz |
| `standard` | onchip | 3452 | 479 | 1889 | 46 / 46 | 48 MHz | 6.471 ns | 69.628 MHz |
| `standard` | ddr3 | 7891 | 843 | 3830 | 46 / 46 | 48 MHz | 0.548 ns | 50.573 MHz |
| `performance` | onchip | 3656 | 521 | 2060 | 46 / 46 | 48 MHz | 5.325 ns | 64.482 MHz |
| `performance` | ddr3 | 9116 | 885 | 5300 | 46 / 46 | 48 MHz | 0.530 ns | 49.252 MHz |
| `linux` | onchip | 4615 | 702 | 2709 | 46 / 46 | 48 MHz | 2.448 ns | 54.390 MHz |
| `linux` | ddr3 | 8860 | 1066 | 4650 | 46 / 46 | 48 MHz | 0.138 ns | 48.321 MHz |

## Existing `performance` selection

At 48 MHz, the public `performance` profile uses the measured `dynamic_target` CPU. Its three-run candidate mean was 119.323913 CoreMark against a fresh `standard` mean of 109.127482, a 10.196432 CoreMark (9.34%) increase. Both standard-derived candidates built, met timing, and completed board trials.

| Candidate | LUT / BSRAM | Worst setup slack | Mean CoreMark | Gain over fresh `standard` | Outcome |
|---|---:|---:|---:|---:|---|
| `dynamic` | 3540 / 46 | 5.245 ns | 111.487554 | 2.360072 (2.16%) | viable |
| `dynamic_target` | 3656 / 46 | 5.325 ns | 119.323913 | 10.196432 (9.34%) | selected |

## Maxperf evaluation

The complete bounded matrix has **26 candidates**: 8 on-chip and 18 DDR3. The pinned generator varies only I-cache size, D-cache size, and RV32IM/RV32IMC. Complete per-candidate arguments, tool versions, RTL hashes, reports, firmware, and failure logs are in the [on-chip build matrix](performance/maxperf-evaluation/20261001T201408.429405Z-onchip-maxperf-build/candidate-build.json) and [DDR3 build matrix](performance/maxperf-evaluation/20261001T202139.975865Z-ddr3-maxperf-build/candidate-build.json).

The fresh on-chip `performance` baseline passed validation plus three scores at **119.3239155434707 CoreMark**. Five candidates completed all four trials; their means ranged from 114.129016 to 117.747854, all below baseline. Candidate six, `icache-4096_dcache-2048_rv32imc`, timed out after FPGA programming with a 36-byte partial BIOS banner and no benchmark-start, completion, or halt marker. The runner stopped before the final two candidates; there is no accepted on-chip winner.

For DDR3, four candidates passed timing/resource checks. Two missed timing, six 8 KiB I-cache candidates failed placement, and all six 16 KiB candidates exceeded the 20,736-logic device limit. A fresh performance DDR reference has not qualified: prior `performance` DDR attempts and the latest Linux DDR run trained, then failed BIOS Memtest. Candidate scoring was not started without that required reference. No maxperf profile selection exists, and the public profile registry remains at five entries.

| Mode | Fresh performance baseline | Board-scored candidates | Winners over baseline | Hardware status |
|---|---:|---:|---:|---|
| onchip | 119.3239155434707 | 5 complete; candidate 6 uncertain; 2 unmeasured | 0 measured | stopped for safe-idle reset |
| ddr3 | blocked before CoreMark | 0 | 0 | startup BIOS Memtest error; no full acceptance |

The complete candidate-by-candidate outcome table is in [docs/performance.md](performance.md). The partial on-chip trial set and raw UART captures are in [the on-chip maxperf evaluation](performance/maxperf-evaluation/20261001T202219.864127Z-onchip-maxperf/evaluation.json). Public selection requires a strict gain over a fresh mode-matched baseline, ten DDR training/smoke cycles, full 256 MiB integrity coverage, and at least 1,800 seconds of error-free stress.

## Evidence and reproduction

- [Machine-readable benchmark sessions](performance/results.json)
- [Detailed benchmark, build, and maxperf report](performance.md)
- [DDR3 training and integrity results](ddr3/results.json) and [DDR3 report](ddr3/report.md)
- [Performance candidate selection](../cpu-profile-selection.json)

```sh
make benchmark-build MEMORY=onchip PROFILE=linux
make benchmark-run PROFILE=linux MEMORY=onchip PORT=/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
make maxperf-build MEMORY=onchip
make maxperf-run MEMORY=onchip PORT=/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
make maxperf-build MEMORY=ddr3
make maxperf-run MEMORY=ddr3 PORT=/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
```
