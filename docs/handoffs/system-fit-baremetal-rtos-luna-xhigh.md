# Luna xhigh handoff: bare-metal and RTOS system fit

## Assignment and boundaries

Use **Luna (`gpt-6-luna`) with extra-high (`xhigh`) reasoning** in `/home/user/git/fpga/sipeedtangprimer20k-riscv` to implement and run a new, isolated offline study of **VexRiscv + LiteDRAM DDR3 + native four-bit LiteSDCard read/write DMA + LiteEthMAC/RMII**, targeting bare metal, Zephyr and FreeRTOS instead of Linux.

Continue through implementation, host checks, synthesis, place-and-route and software cross-compilation. Do not return only another plan. The priority is a complete routed design with a real, compact boot image, followed by three independently reported software tracks. These tracks use the same hardware where compatible; an operating system name is not a separate hardware configuration.

**No video. No Linux/MMU/supervisor requirement. No FPGA programming, reset, probing, JTAG, UART or other board access.** Runtime SD/Ethernet/DDR tests, stress tests and OS boot are excluded. Do not commit or push. This document creates a future assignment; its preparation did not launch Luna or run builds.

## Starting evidence and repository custody

Read the [previous report](../system-fit/20261006T193605.226499Z/report.md), its `report.json` and per-attempt manifests, [original plan](../system-fit-plan.md), and [previous implementation handoff](system-fit-luna-xhigh.md). This handoff supersedes the Linux CPU, full peripheral BIOS and four-candidate requirements for the new study only. Preserve the previous study and its conclusions.

All four complete Linux candidates failed BIOS linking at 48 KiB ROM, overflowing by 1,936–2,088 bytes. None reached synthesis or routing. This is a boot-software integration/budget blocker, not proof that the complete hardware cannot fit.

The three attribution builds routed with passing timing:

| Hardware | Setup / hold / recovery / removal slack (ns) | Routed BSRAM |
|---|---|---|
| DDR only | +0.947 / +0.080 / +2.293 / +0.974 | 45/46 |
| DDR + native SD DMA | +0.228 / +0.316 / +1.585 / +0.963 | 46/46 |
| DDR + one-slot Ethernet | +1.631 / +0.227 / +1.490 / +2.655 | 46/46 |

These figures belong to the prior Linux CPU and BIOS layouts. Do not add utilization figures from different designs or reuse their timing pass for a new image. BSRAM exhaustion makes boot ROM, working SRAM, caches and packet buffers central to this study.

At preparation, the checkout has extensive existing edits and untracked implementation/evidence files. Start by reading applicable instructions and recording revision, status and active host build processes. Preserve existing work, CPU selection records, peripheral evidence and previous build outputs. Do not reset/clean. Use the pinned local environment and avoid competing Gowin jobs in shared paths.

Inspect `gateware/system_fit.py`, `scripts/system_fit.py`, `scripts/system_fit_reports.py`, `tests/test_system_fit.py`, and the reusable options now in `gateware/soc.py`. The current isolated SoC, CPU audits and runner assume Linux; do not merely rename their output. Inspect `scripts/cpu_profiles.py`, `scripts/cpu_candidates.py`, CPU generator sources/lock, dependency lock/setup and BIOS link maps before changing anything.

## Isolated implementation and complete candidates

Add `gateware/rtos_system_fit.py`, `scripts/rtos_system_fit.py`, and commands `make rtos-system-fit` / `make rtos-system-fit-check`. Reuse existing integration and report helpers through narrow refactoring with regression checks. Keep the original `make system-fit` behavior intact. New outputs go under `build/rtos-system-fit/<UTC-run-id>/` and published evidence under `docs/rtos-system-fit/<UTC-run-id>/`.

All candidates retain the fitted 128 MiB Hynix geometry, patched Gowin DDR PHY, DLL-off CL6/CWL6, 48 MHz system and 96 MHz DDR clocks, UART, timer/uptime, interrupts, and the 50 MHz RMII reference constraint. Keep native SD read/write DMA and card detect, and one RX plus one TX Ethernet slot of 2048 bytes each. Omit dedicated DDR diagnostic memory/alias, unused GPIO and optional peripherals from this target. Do not substitute SPI SD, remove a DMA direction, reduce Ethernet frame capacity or raise clocks.

Try these complete candidates in order; stop hardware exploration at the first routed timing pass:

| Candidate | CPU | I-cache / D-cache | L2 | Working SRAM |
|---|---|---|---|---|
| 1 | pinned Lite VexRiscv, RV32IM | 2 KiB / none | 2 KiB | 4 KiB |
| 2 | same Lite CPU | 2 KiB / none | disabled | 4 KiB |
| 3 | generated small machine-mode RV32IM | 1 KiB / none | disabled | 4 KiB |
| 4 | pinned Minimal VexRiscv, RV32I | none / none | disabled | 4 KiB |

For candidate 3 use the locked `GenCoreDefault` environment, `--csrPluginConfig small`, 1024-byte I-cache, zero D-cache, no MMU/atomics/compressed/debug/CFU/PMP, static prediction, and the pinned Lite CPU generation recipe for other settings. Preserve machine traps, timer/software/external interrupt interfaces and the LiteX Wishbone ABI. Verify the actual pinned Lite recipe before generation; record every explicit argument. Do not use the Linux capability audit for these CPUs. Check machine CSR/trap support and declared ISA against emitted RTL/YAML and software compiler options.

Use 4 KiB working SRAM initially; require at least 2 KiB static boot-stack headroom and retry with 8 KiB only if that check fails. Capture every attempt. If all complete candidates fail, run candidate 2 DDR-only, DDR+native-SD and DDR+Ethernet attribution builds with the identical reduced boot image policy. Limit conclusions to this bounded matrix.

## Resolve boot ROM overhead first

Before full routing, inspect prior BIOS maps and current command/library registration to identify the retained native SD, filesystem, network and diagnostic code. `SDCARD_BOOT_DISABLE` and `NET_BOOT_DISABLE` already existed in the failed study; disabling automatic boot alone did not solve ROM size.

Implement a **study-specific reduced LiteX BIOS** using a repository-owned build recipe/source adaptation. Retain startup, UART serial loader, application handoff, real DDR initialization/training and a bounded DDR smoke check. Keep boot code/data/stack in on-chip memory until DDR initializes. Retain the serial loader's existing integrity checks and length/address checks. Fail visibly and stay in the loader on DDR initialization failure.

Exclude SD/filesystem and network/TFTP/DHCP boot paths, peripheral console commands and unrelated diagnostic commands from boot firmware; these belong to later applications. Keep generated hardware CSRs and peripheral cores intact. Do not fake CSR absence to shrink firmware. Use `-Os`, section garbage collection and BIOS LTO; record the recipe and library selection. Any dependency patch must be repository-owned, scoped to this target and fingerprinted. Prefer a local BIOS source copy/adaptation over globally changing dependency defaults.

Use an initial 16 KiB ROM reservation, retry at 24 then 32 KiB only on demonstrated ROM overflow, and auto-size the implemented ROM from the real binary. Do not increase to 48/64 KiB in this bounded study. Prove map/ELF section placement, requested versus implemented sizes, stack headroom and embedded ROM contents. Do not claim success for stub firmware, an empty ROM or DDR-resident startup that assumes DDR has already initialized.

## Bare-metal, Zephyr and FreeRTOS software tracks

Once a complete candidate routes, compile all three tracks for that exact CPU/CSR map and DDR layout. Pin any additional dependencies locally without upgrading the existing FPGA stack. Record toolchains, source revisions, configuration, ELF/map/binary hashes and exact reproduction commands. Applications load into DDR through the serial loader; never execute loading commands during this assignment.

- **Bare metal:** create a small DDR-linked UART hello/counter application with timer and interrupt setup. Add compile/link integration probes for native SD read and write DMA and MAC RX/TX using the generated registers. No filesystem or network stack is required. Probes must reference real implementations, not empty placeholders, but must not execute on hardware.
- **Zephyr:** pin a stable release and adapt the LiteX VexRiscv platform with generated DTS/config overlays matching this design's 128 MiB DDR, ISA, UART, timer, interrupt controller, SD and Ethernet. Start with machine-mode hello/thread/timer support and no userspace/MMU. Then compile a separate peripheral-enabled build with native SD and LiteEth drivers enabled. Inspect the pinned drivers' CSR layouts, DMA expectations, slot counts and PHY/MDIO assumptions; do not reuse the reference board's fixed addresses or Xilinx clock driver. Implement only necessary board/glue compatibility adjustments. Report driver incompatibilities separately if substantial driver work is needed.
- **FreeRTOS:** pin FreeRTOS-Kernel, use the GCC RISC-V port, and compile a DDR-linked two-task UART/counter application with a 1 kHz scheduler tick. Provide LiteX timer and external-interrupt adaptation, including trap entry/exit, event acknowledgement and the port's yield path. Do not assume LiteX has CLINT/MTIME/MTIMECMP or PLIC registers. Verify actual CPU interrupt CSRs and timer semantics. Enable stack-overflow checking and explicit task/ISR stack budgets. Full FreeRTOS+TCP, FAT or production SD/Ethernet drivers are outside this study; document their remaining integration work.

Prefer the first routed CPU for all tracks. If a software port requires a missing hardware capability, report it precisely; do not silently enable unsupported ISA instructions or change the routed design. A subsequent hardware variant requires a separately identified build and fresh routing/timing evidence.

No-D-cache candidates simplify DMA visibility, but verify LiteDRAM L2 behavior, MMIO uncached regions, instruction-cache synchronization before jumping to loaded code, DMA buffer alignment and access ordering. Do not assume all DMA is coherent merely because CPU D-cache is absent.

Primary references to verify against the pinned software revisions:

- [Zephyr LiteX VexRiscv documentation](https://docs.zephyrproject.org/latest/boards/enjoydigital/litex_vexriscv/doc/index.html): documents overlay generation and LiteX UART/timer/interrupt/Ethernet/LiteSDCard support. Its reference board is not this Tang Primer layout.
- [FreeRTOS RISC-V port guidance](https://compatibility.freertos.org/Using-FreeRTOS-on-RISC-V) and [official port assembly](https://github.com/FreeRTOS/FreeRTOS-Kernel/blob/main/portable/GCC/RISC-V/portASM.S): inspect timer and chip-specific interrupt integration rather than assuming reference-platform hardware.

## Verification, evidence and completion

Add meaningful host checks for CPU capabilities/ISA, boot ROM budget and embedded image, stack/section placement, DDR geometry, both native SD DMA masters, peripheral retention, address/IRQ/pin collisions, candidate retries and failure classification. Keep original study checks passing. Inspect generated software/DTS and compiled instruction requirements against the actual CPU.

Run Gowin synthesis and full place-and-route with the existing timing parser and gates. Preserve the proven narrow DDR PHY reset-pin setup/recovery exception and its justification; do not broaden false paths to manufacture closure. Require active 48/96/50 MHz constraints, nonnegative setup/hold/recovery/removal slack and no unexpected unconstrained internal paths. Capture resource capacities/headroom and raw reports for logic, registers, BSRAM, SSRAM, DSPs and PLLs. Missing vendor data remains unavailable, not zero.

Every attempt needs a manifest, actual configuration, revisions/patches/source hashes, CPU generation evidence, boot ELF/map/binary, firmware budgets, constraints, synthesis/routing/timing reports and logs. Preserve failed attempts. Classify ROM/software integration, host tool, resource and timing failures separately. The aggregate command returns nonzero if no complete hardware candidate passes; software-track outcomes are separately recorded and summarized.

Publish a concise report distinguishing **complete hardware fit**, **bare-metal compile readiness**, **Zephyr kernel/peripheral compile readiness**, and **FreeRTOS compile readiness**. State remaining driver/port blockers explicitly. Cross-compilation is not evidence of scheduler operation, DDR integrity, SD transfers, Ethernet traffic or OS boot. Preserve previous Linux evidence and user changes; no device access may occur.

Completion means a bounded, reproducible hardware study with all attempted outcomes retained, and all three software tracks either cross-compiled for the selected routed image or documented with concrete blockers. If no hardware passes, perform independent port/compile checks against a clearly labeled non-routed configuration where feasible, without presenting it as a deployable result. Lead the final response with the complete-design fit result, exact CPU/ROM/cache/memory configuration, resource headroom, software status and report link.
