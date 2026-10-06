# Luna xhigh handoff: implement the offline system fit study

## Assignment

Use **Luna (`gpt-6-luna`) with extra-high (`xhigh`) reasoning** in `/home/user/git/fpga/sipeedtangprimer20k-riscv` to implement and run [the agreed fit plan](../system-fit-plan.md). Continue through synthesis, place-and-route, evidence collection and the final report; do not return only another plan.

The requested system is a Linux-capable VexRiscv, LiteDRAM DDR3, native four-bit LiteSDCard with read/write DMA, and LiteEthMAC over RMII. The user explicitly removed video from the requirement and approved an optimized, separate target. **Never program, reset, probe or test the FPGA. Do not access JTAG or UART devices.** Linux boot and application-driver implementation are outside scope.

This handoff specifies the future implementing agent's configuration. Writing it did not launch Luna or run the study. Historical handoffs authorize hardware workflows for other tasks; those workflows do not apply to this assignment.

## Starting state and custody

Prepared on 2026-10-06. Rediscover the current revision, instructions and working tree before implementing. At preparation, there were existing edits to five `docs/peripherals/` documents/data files and many untracked peripheral evidence directories. Preserve all of them. Do not reset/clean, overwrite existing evidence or build directories, change CPU selections, commit, or push.

Inspect active host build processes before starting Gowin to avoid competing for shared build outputs or tool resources. Use separate candidate directories under `build/system-fit/` and the pinned `.venv`/`.tools` environment. Do not invoke project load/run/recovery commands or the broader hardware-validation handoffs.

Read these sources before coding:

- `gateware/soc.py`: existing board integration, Hynix geometry override, DDR clock/reset fixes, uncached bridge and 16 KiB diagnostic RAM.
- `gateware/peripherals.py`, `scripts/build.py`, `scripts/compare.py`: Ethernet slot sizes, dependency patches, BIOS build and resource/timing parsing.
- `scripts/cpu_profiles.py`, `scripts/cpu_candidates.py`, `cpu-generator.lock.json`: CPU identity and reproducible generator environment.
- `dependencies.lock.json`, `scripts/setup.py`: pinned dependencies and setup workflow.
- `docs/peripherals/fitting-experiments.md`: prior failed fits and bounded layout trials.
- Pinned `.deps/litex-boards/litex_boards/targets/sipeed_tang_primer_20k.py` and corresponding platform: DDR/RMII integration and native SD pins.
- Pinned LiteX `soc.py` implementations of `add_sdcard` and `add_ethernet`, plus VexRiscv `GenCoreDefault.scala` and its Makefile Linux recipe.

Current project options support only SPI SD and RMII Ethernet. LiteSDCard is absent from both the dependency lock and the local Python environment. Prior Linux/Ethernet placement failures retained diagnostic memory; do not use them as proof that this isolated target cannot fit.

## Implementation instructions

1. Add an isolated SoC implementation and `scripts/system_fit.py`. Avoid changing registered profiles or routing the existing SPI application through the native SD core. Reuse board DDR PHY, geometry, clock/reset corrections and RMII constraint logic through narrowly scoped shared helpers where needed. Include all existing repository-owned patches in build identity.
2. Add LiteSDCard as a reproducibly pinned dependency and integrate it with setup. Resolve and record an exact compatible upstream commit before building; do not upgrade the other locked dependencies. Confirm the generic SD PHY I/O lowers correctly for Gowin. If a compatibility patch is necessary, keep it repository-owned, applied by setup and fingerprinted.
3. Instantiate native `sdcard` pads with all four data lines, command, clock and card detect, and `add_sdcard(mode="read+write")`. Retain both DMA masters and the interrupt. Instantiate LiteEthMAC/RMII with the plan's slot counts and 2048-byte slots. Preserve full standard Ethernet frame capacity.
4. Keep UART, timer, interrupts and 128 MiB DDR at 48/96 MHz. Remove diagnostic RAM and its separate uncached alias only from this target. Do not omit required DDR initialization CSRs, Linux MMU/privilege/atomic support, or peripheral paths to produce an artificial fit.
5. Build the real BIOS with the lite console, serial loading and DDR/peripheral libraries. Set `SDCARD_BOOT_DISABLE`, `NET_BOOT_DISABLE` and `BIOS_NO_ETHERNET_INIT`. Use Builder ROM auto-sizing and enforce the plan's 2 KiB stack margin. Record requested and implemented ROM/SRAM sizes. Retry with 48 KiB ROM only for a demonstrated ROM overflow; retry with 8 KiB SRAM only for inadequate stack headroom. Do not count stub firmware or an uninitialized ROM as a passing result.
6. Run the four complete candidates in the plan's exact order, stopping at the first full pass. Candidates 1–2 use the pinned `VexRiscv_Linux.v`. For 3–4, use the pinned `vexriscv.GenCoreDefault` Linux Makefile recipe (`--csrPluginConfig linux-minimal`) with `--iCacheSize`/`--dCacheSize` set to 2048/2048 and 1024/1024 respectively. Keep remaining Linux recipe options unchanged. The current standard/performance generator defaults are not a Linux CPU recipe. Record generator source/tool hashes, arguments, RTL/YAML and the resulting CPU interface/capabilities.
7. If all four complete candidates fail, run three attribution builds using candidate 2's pinned CPU, 2 KiB L2 and 4 KiB working SRAM (8 KiB only if stack checks require it): DDR alone, DDR with native SD, and DDR with one RX/one TX Ethernet slot. These are diagnostic comparisons, never a substitute for the complete target.

## Checks and evidence

Add meaningful host checks for candidate sequencing, CPU capability/identity, peripheral and DMA presence, address/IRQ/pin collisions, and report classification. Verify the original project configuration still elaborates with its diagnostic memory and unchanged public defaults. Do not run hardware-dependent tests.

Audit generated RTL/CSRs and synthesis hierarchy to ensure SD read/write and Ethernet datapaths survive synthesis. All DMA masters must reach main RAM, and CPU/MMIO regions must retain appropriate cache treatment. For generated CPUs, verify Linux privilege/MMU/atomics and Wishbone/interrupt ports rather than inferring capabilities from a filename.

Use Gowin synthesis and full place-and-route. Adapt report parsing for this target without weakening existing timing gates. Capture used/capacity/headroom for logic, registers, BSRAM, SSRAM, DSPs and PLLs; missing data must be marked unavailable, not zero. Require real 48 MHz system, 96 MHz DDR and 50 MHz RMII clock constraints, active timing analysis, nonnegative setup/hold/recovery/removal slack, and no unexpected unconstrained internal paths. Preserve narrow justified DDR reset/CDC exceptions; do not add broad false paths to manufacture closure.

Keep tool/integration failures separate from resource and timing failures. An unresolved import, BIOS compilation failure, license error or missing report is not evidence that the FPGA is too small. Resolve routine host issues autonomously; if an external blocker remains, preserve exact errors and deliver the completed independent work.

Write per-candidate manifests and a machine-readable aggregate under `build/system-fit/`. Preserve source/configuration/RTL/BIOS/bitstream hashes, dependency revisions and patches, tool versions, logs, constraints, synthesis resources, routed resources and timing. Do not overwrite earlier attempts; use candidate-specific attempt directories.

Publish a compact report and retained report evidence under `docs/system-fit/`. Report every attempted candidate, the selected passing candidate if any, stack/ROM adjustments, resource headroom and limitations. Classify outcomes as routed timing pass, resource failure, timing failure, integration failure or tool failure. State clearly that all builds were offline and hardware functionality/Linux boot remain unverified. If none passes, say that none of the bounded candidates passed, not that all Linux-capable designs are impossible on this FPGA.

## Completion criteria

- A reproducible isolated study command exists and its relevant host checks pass.
- At least one complete candidate passes routing/timing, or all four have preserved outcomes plus the three attribution builds where feasible.
- Real BIOS memory/stack requirements and all required peripherals are verified for any claimed pass.
- The concise report links to configuration, resources and timing evidence and accurately distinguishes blockers from fit failures.
- Existing user changes, public profiles, hardware evidence and FPGA state are preserved; no device access occurred.

The final response should lead with whether a complete candidate achieved routed timing closure, identify its exact configuration and resource headroom, link the report, and mention any remaining host blockers or unverified hardware behavior.
