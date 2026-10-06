# Offline fit study: Linux-capable VexRiscv, DDR3, native SD and Ethernet

## Summary

Determine whether all four components fit together on the Tang Primer 20K and meet timing using Gowin synthesis and place-and-route. **Video is excluded. No FPGA programming or hardware tests will run.**

Existing Linux/Ethernet builds failed placement, but retained substantial project diagnostic memory. Those [results](peripherals/fitting-experiments.md) do not settle whether an optimized configuration can fit.

## Implementation

- Add a separate fit-study target and command, `scripts/system_fit.py`, with outputs under `build/system-fit/`. Preserve existing profile definitions, build outputs and current user changes.
- Reuse the project's patched LiteDRAM/Gowin DDR3 integration: 128 MiB Hynix geometry, 48 MHz system clock and 96 MHz DDR clock.
- Pin and install [LiteSDCard](https://github.com/enjoy-digital/litesdcard), retaining existing dependency versions. Instantiate native four-bit SD with both read and write DMA through `add_sdcard(mode="read+write")`; verify its interrupt and bus connections.
- Instantiate LiteEthMAC with LiteEthPHYRMII, the existing 50 MHz reference constraint, and 2048-byte packet slots supporting standard Ethernet frames.
- Retain UART, timer and interrupt support. Omit the dedicated 16 KiB diagnostic RAM and separate diagnostic DDR alias from this target.
- Compile a real LiteX BIOS with the lite console, serial loading, DDR initialization and peripheral libraries. Disable SD/network automatic boot and Ethernet automatic initialization. Start with a 32 KiB ROM reservation, increase to 48 KiB only if compilation requires it, and let Builder size the implemented ROM from the resulting binary.
- Keep the study separate from the current SPI SD application. Native SD application-driver porting and Linux image construction are outside this fit assessment.

## Candidate sequence

Build each complete configuration in order; stop at the first candidate that passes placement and timing.

| Candidate | CPU instruction/data caches | LiteDRAM L2 | Working SRAM | Ethernet RX/TX slots |
|---|---:|---:|---:|---:|
| 1: Pinned Linux RTL | 4/4 KiB | 8 KiB | 8 KiB | 2/2 |
| 2: Compact memory | 4/4 KiB | 2 KiB | 4 KiB | 1/1 |
| 3: Smaller Linux caches | 2/2 KiB | 2 KiB | 4 KiB | 1/1 |
| 4: Minimum study candidate | 1/1 KiB | Disabled | 4 KiB | 1/1 |

Generate candidates 3–4 using the pinned VexRiscv generator with `linux-minimal` CSR configuration. Preserve MMU, supervisor mode, RV32IMA support and the LiteX CPU interface; change only cache sizes. Require at least 2 KiB of BIOS stack headroom; increase working SRAM to 8 KiB if necessary and record that change.

If every candidate fails, build compact DDR-only, DDR-plus-SD and DDR-plus-Ethernet configurations to identify the resource bottleneck.

## Verification and deliverable

- Verify CPU identity, DDR geometry, SD DMA masters, interrupt allocation, peripheral presence and collision-free pin assignments.
- Run Gowin synthesis and full place-and-route. Record logic, registers, BSRAM, SSRAM, DSPs, PLLs and remaining capacity.
- Require active system, DDR and RMII constraints; passing setup, hold, recovery and removal checks; and no unexpected unconstrained internal paths.
- Retain failed-build logs as well as successful reports. Distinguish integration/tool failures, capacity failures, and timing failures.
- Produce a concise report with exact configurations, dependency/RTL hashes and a result for each candidate.

A passing result establishes **routed fit and static timing closure**. DDR operation, native SD transfers, Ethernet traffic and Linux boot remain unverified. If all candidates fail, report that bounded result without claiming every possible VexRiscv configuration is impossible.
