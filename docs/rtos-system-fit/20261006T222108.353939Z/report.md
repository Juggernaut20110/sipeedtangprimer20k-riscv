# Bare-metal and RTOS system fit study

Complete DDR3 + native SD DMA + RMII hardware fit: **routed timing pass** on `candidate-2-pinned-lite-no-l2`.

Run `20261006T222108.353939Z`. Offline gateware build; no board access. A compile or routed pass does not establish DDR integrity, SD/Ethernet transfers, RTOS scheduling, or OS boot.

## Candidate attempts

| Candidate attempt | Result | BIOS binary bytes | Static stack headroom | Setup / hold / recovery / removal slack (ns) |
|---|---|---:|---:|---|
| candidate-1-pinned-lite (16384 B ROM) | timing failure | 13588 | 3760 | -0.602 / 0.208 / 1.646 / 0.986 |
| candidate-2-pinned-lite-no-l2 (16384 B ROM) | routed timing pass | 13540 | 3760 | 0.648 / 0.079 / 2.05 / 1.532 |

## Selected routed configuration

CPU `candidate-2-pinned-lite-no-l2`; rv32im/ILP32; I-cache 2048 B; D-cache 0 B; L2 0 B; working SRAM 4096 B. DDR is 128 MiB at 96 MHz with the system at 48 MHz.

BIOS ROM reservation 16384 B; auto-sized initialized ROM 13540 B; BIOS binary 13540 B; static BIOS stack headroom 3760 B (minimum 2048 B).

Routed slack (setup / hold / recovery / removal): 0.648 / 0.079 / 2.05 / 1.532 ns; 50894 paths analyzed; 0 setup and 0 hold violations.

| Routed resource | Used / capacity | Headroom | Status |
|---|---:|---:|---|
| Logic | 9408 / 20736 | 11328 | available |
| Registers | 4825 / 16173 | 11348 | available |
| BSRAM | 27 / 46 | 19 | available |
| SSRAM | 80 | unavailable | capacity unavailable in report |
| DSP | unavailable | unavailable | unavailable: resource row not found |
| rPLL | 1 / 4 | 3 | available |
| Global clock pins | 6 / 8 | 2 | available |
| DLL | 1 / 4 | 3 | available |

## Software tracks

Cross-compilation checks source and link integration only. No ELF or binary was run.

- **Bare metal:** passed — baremetal cross-compiled for the selected routed image.
- **Zephyr kernel:** passed — Zephyr kernel ELF cross-compiled for the routed CSR map.
- **Zephyr SD/Ethernet:** passed — Zephyr peripherals ELF cross-compiled for the routed CSR map.
- **FreeRTOS:** passed — freertos cross-compiled for the selected routed image.

## DMA and runtime limits

The selected CPU has no D-cache and the routed design has no L2; the Ethernet packet windows are MMIO. The Zephyr LiteX SD and Ethernet sources receive a target-scoped `fence w,o` before DMA start and `fence i,r` after completion. The bare-metal probe links the real LiteX SD and UDP implementations but exists only for link checking and is never called. No runtime transfer, DDR, scheduling, network, or OS boot result is claimed.


## Reproduction and evidence

Machine report: [`report.json`](report.json). Attempt manifests, BIOS outputs, constraints, Gowin reports, and logs: [`evidence/`](evidence/). Full outputs remain under `build/rtos-system-fit/20261006T222108.353939Z/`.

The design uses the existing patched Hynix DDR3 PHY, 128 MiB DDR, 48 MHz system clock, 96 MHz DDR clock, 50 MHz RMII reference, native four-bit SD read/write DMA, and one 2048-byte RX/TX Ethernet slot. Static timing is gated on setup, hold, recovery, removal, and internal path analysis.
