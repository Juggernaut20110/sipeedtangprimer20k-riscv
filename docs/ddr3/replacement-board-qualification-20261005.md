# Replacement-board DDR3 qualification

Board: **replacement Tang Primer 20K Dock attached 2026-10-05**. Qualification status: **partial** — four public profiles passed; `minimal` is blocked by routed timing.

The board identity follows the October 5 handoff and the independent [Sipeed vendor comparison](sipeed-vendor-test/new-board-20261005.md). That unchanged vendor image passed on three fresh SRAM loads on this assembly after failing repeatedly on the previous assembly. This supports a board/assembly difference but does not isolate the responsible component. No board serial or unique hardware identifier was recorded.

All project runs used SRAM programming, the 128 MiB fitted-part geometry, a 48 MHz system clock, and 96 MHz DDR CK. No flash writes, clock changes, cold boot, or power-cycle tests were performed. `linux` means the Linux-capable VexRiscv configuration running the existing bare-metal diagnostics and CoreMark; no OS kernel was booted. `maxperf` remains provisional and was not included.

## Profile matrix

| Profile | Resources LUT/ALU/registers/BSRAM | DDR3 status / batch | Training | Full range / stress | Errors / cache visibility | Routed timing | DDR3 CoreMark/s / per MHz | Same-board on-chip CoreMark/s |
|---|---|---|---:|---|---|---|---:|---:|
| `minimal` | 5476/646/3218/46 | blocked by timing; no DDR session | — | — | — | setup -0.911 ns; Fmax 50.849 MHz | — | 16.588341 |
| `lite` | 6366/789/3497/46 | passed; `20261005T215520.872922Z-ddr3` | 10/10 | 128 MiB; 1800.250/1800 s | 0 errors; passed | setup 0.183 ns; Fmax 48.424 MHz | 49.757130 / 1.036606871 | 50.427512 |
| `standard` | 7916/825/3807/46 | passed; `20261005T223316.301737Z-ddr3` | 10/10 | 128 MiB; 1800.052/1800 s | 0 errors; passed | setup 0.742 ns; Fmax 53.607 MHz | 106.238365 / 2.213299278 | 109.127482 |
| `performance` | 9094/867/5221/46 | passed; `20261005T231023.848982Z-ddr3` | 10/10 | 128 MiB; 1800.059/1800 s | 0 errors; passed | setup 0.006 ns; Fmax 55.148 MHz | 115.798154 / 2.412461534 | 119.323916 |
| `linux` | 8805/1048/4627/46 | passed; `20261005T234544.924815Z-ddr3` | 10/10 | 128 MiB; 1800.052/1800 s | 0 errors; passed | setup 0.111 ns; Fmax 50.321 MHz | 106.238341 / 2.213298762 | 109.127481 |

The DDR3 CoreMark columns show mean iterations/second and CoreMark/MHz from three scored repetitions. Timer ticks, iteration counts, validation results, CRCs, firmware and bitstream hashes are in the [detailed performance report](../performance.md) and [machine-readable benchmark results](../performance/results.json). The on-chip measurements are a fresh same-board baseline with the same CoreMark sources and 48 MHz clock.

## Session identifiers

- `lite`: DDR acceptance [`20261005T215520.872922Z-ddr3-lite`](../../build/ddr3/ddr-test-sessions/20261005T215520.872922Z-ddr3-lite/session.json); CoreMark [`20261006T002139.632753Z-ddr3-lite`](../../build/benchmarks/20261006T002139.632753Z-ddr3-lite/session.json).
- `standard`: DDR acceptance [`20261005T223316.301737Z-ddr3-standard`](../../build/ddr3/ddr-test-sessions/20261005T223316.301737Z-ddr3-standard/session.json); CoreMark [`20261006T002539.055779Z-ddr3-standard`](../../build/benchmarks/20261006T002539.055779Z-ddr3-standard/session.json).
- `performance`: DDR acceptance [`20261005T231023.848982Z-ddr3-performance`](../../build/ddr3/ddr-test-sessions/20261005T231023.848982Z-ddr3-performance/session.json); CoreMark [`20261006T002841.186923Z-ddr3-performance`](../../build/benchmarks/20261006T002841.186923Z-ddr3-performance/session.json).
- `linux`: DDR acceptance [`20261005T234544.924815Z-ddr3-linux`](../../build/ddr3/ddr-test-sessions/20261005T234544.924815Z-ddr3-linux/session.json); CoreMark [`20261006T003139.289511Z-ddr3-linux`](../../build/benchmarks/20261006T003139.289511Z-ddr3-linux/session.json).

## Qualification details

For each accepted DDR3 profile the runner revalidated ten fresh SRAM reconfiguration/training/uncached-smoke trials, both expected read lanes and recorded leveling windows, all destructive memory phases over 128 MiB, cached/uncached visibility with CPU and LiteDRAM L2 maintenance, and at least 1800 measured seconds of delayed-readback stress. All accepted sessions report zero errors. Full phase names and byte/timer counts, raw RX/TX UART hashes, CoreMark timer ticks, per-profile build configuration, and fingerprints are recorded in the [machine-readable qualification manifest](replacement-board-qualification-20261005.json) and [DDR session data](../../build/ddr3/ddr-test-sessions/).

The `minimal` P&R reports show estimated Fmax of 50.849 MHz but worst setup slack of -0.911 ns and one setup-violated endpoint. The failing path is `gw2ddrphy_dqs_hold_1_s0/Q` to `DQS_1/HOLD` from `sys_clk` rising to `sys2x_clk` falling. `minimal` was not programmed or scored on DDR3. The DDR runner now checks routed timing before UART access or SRAM programming, so this profile fails closed until a supported design change clears the timing gate.

## Evidence and recovery

Four independent DDR batches passed: `20261005T215520.872922Z-ddr3`, `20261005T223316.301737Z-ddr3`, `20261005T231023.848982Z-ddr3`, `20261005T234544.924815Z-ddr3`. The on-chip comparison ran as batch `20261006T004728.288750Z-all`. The initial performance retry blocker is retained in the manifest and did not program the FPGA.

After the final on-chip run, recovery loaded the hash-verified idle image into SRAM: [20261006T010916Z-replacement-board-final-recovery](evidence/20261006T010916Z-replacement-board-final-recovery/results.json). Programmer exit code was 0; the five-second stable-UART observation received 0 bytes. The image holds DDR RESET# low, CKE low, CS# high, CK stopped and ODT low. Flash programming was false.

The previous-board BIOS and write-disturbance failures remain in the [historical diagnosis](diagnosis.md) and [geometry correction](hynix-geometry-correction.md); they are not replacement-board outcomes. The replacement-board results describe SRAM reloads only and do not establish power-cycle or cold-boot behavior.

Machine-readable batches and evidence identities: [DDR results](results.json); benchmark scores, timer ticks and image hashes: [performance results](../performance/results.json).
