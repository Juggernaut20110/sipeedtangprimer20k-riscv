# Bare-metal and RTOS system fit study

Complete DDR3 + native SD DMA + RMII hardware fit: **no candidate passed routed timing** in this bounded matrix.

Run `20261006T221650.486565Z`. Offline gateware build; no board access. A compile or routed pass does not establish DDR integrity, SD/Ethernet transfers, RTOS scheduling, or OS boot.

## Candidate attempts

| Candidate attempt | Result | BIOS binary bytes | Static stack headroom | Setup / hold / recovery / removal slack (ns) |
|---|---|---:|---:|---|
| candidate-1-pinned-lite (16384 B ROM) | tool failure | 13588 | 3760 | unavailable / unavailable / unavailable / unavailable |
| candidate-2-pinned-lite-no-l2 (16384 B ROM) | tool failure | 13540 | 3760 | unavailable / unavailable / unavailable / unavailable |
| candidate-3-generated-small-rv32im (? B ROM) | tool failure | unavailable | unavailable | unavailable / unavailable / unavailable / unavailable |
| candidate-4-pinned-minimal-rv32i (16384 B ROM) | tool failure | 14124 | 3760 | unavailable / unavailable / unavailable / unavailable |
| attribution-ddr-only (16384 B ROM) | tool failure | 13548 | 3768 | unavailable / unavailable / unavailable / unavailable |
| attribution-ddr-native-sd (16384 B ROM) | tool failure | 13540 | 3768 | unavailable / unavailable / unavailable / unavailable |
| attribution-ddr-one-slot-ethernet (16384 B ROM) | tool failure | 13564 | 3768 | unavailable / unavailable / unavailable / unavailable |

## Software tracks


## Reproduction and evidence

Machine report: [`report.json`](report.json). Attempt manifests, BIOS outputs, constraints, Gowin reports, and logs: [`evidence/`](evidence/). Full outputs remain under `build/rtos-system-fit/20261006T221650.486565Z/`.

The design uses the existing patched Hynix DDR3 PHY, 128 MiB DDR, 48 MHz system clock, 96 MHz DDR clock, 50 MHz RMII reference, native four-bit SD read/write DMA, and one 2048-byte RX/TX Ethernet slot. Static timing is gated on setup, hold, recovery, removal, and internal path analysis.
