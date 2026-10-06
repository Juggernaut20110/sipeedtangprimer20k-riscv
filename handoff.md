# Implement microSD and Ethernet for the Tang Primer 20K VexRiscv profiles

Prepared October 5, 2026 (America/New_York). Repository baseline: `cd7d00a` (`Document replacement board DDR3 qualification`). Recheck HEAD and the working tree before implementation.

## Assignment, model and completion

Use **Luna (`gpt-6-luna`) with extra-high (`xhigh`) reasoning** to implement and qualify microSD file access and Ethernet networking in `/home/user/git/fpga/sipeedtangprimer20k-riscv`. Configure the implementing agent with that model and reasoning effort; if the runtime cannot select them, disclose the limitation and actual execution configuration. Do not claim that this document itself changes the running model.

This is an **implementation assignment to carry through completion**, including source changes, reproducible builds, actual board testing, evidence validation, documentation and final recovery. Do not stop at a plan, controller instantiation, synthesis, or host tests when hardware acceptance remains outstanding. Resume unfinished work after interruptions using saved checkpoints and evidence. Do not shorten acceptance workloads to save time.

The user selected **bare-metal FAT file access and TCP/UDP networking**, with support across **all five registered profiles: minimal, lite, standard, performance and linux**. Start on `standard` with DDR3, then extend shared controllers and drivers to the other profiles. Qualify each feasible profile/memory configuration and report concrete timing, resource or hardware blockers. Reduced on-chip diagnostics are useful but are not a full filesystem/networking pass.

Linux is optional if it becomes necessary to meet those capabilities. The primary completion path is bare-metal firmware. Neither an OS nor a replacement CPU is required merely to use SD storage or Ethernet.

This assignment supersedes earlier exclusions of Ethernet and OS work for this scope. The preceding DDR handoff is preserved unchanged at `docs/handoffs/ddr3-qualification-20261005.md`. Historical handoffs are context, not an instruction to redo completed qualification or use an obsolete memory geometry.

Necessary source changes, dependency setup, builds, host checks and SRAM-only board tests are authorized. Preserve current CPU configurations, peripheral-disabled defaults, clocks, DDR geometry, READ_FIRST RAM fixes and historical evidence. Do not program FPGA flash, increase clocks, promote provisional `maxperf`, add unrelated video/USB work, commit or push without a separate request. Do not create another user-owned chat. Delegation is optional; if used, use Luna/xhigh and assign exactly one owner to all UART/JTAG operations.

## FPGA and software responsibilities

Reuse the physical interfaces already present on the core board and standard Dock. The microSD socket is on the core board; the standard Dock carries the Ethernet PHY and connector. Verify the attached board revision and pin mapping against the pinned platform and Sipeed schematics.

| Layer | microSD | Ethernet |
|---|---|---|
| Existing board hardware | Socket, card connections and 3.3 V signals | RTL8201F PHY, magnetics and Ethernet connector |
| FPGA | SPI controller, programmable clock and CPU-visible registers | LiteEth RMII interface, MAC, frame buffers and CPU-visible registers |
| CPU software | Card initialization, sector access, error handling and FAT filesystem | PHY management, frame access, ARP, IPv4, DHCP, ICMP, UDP and TCP |

Implement SPI SD first, using LiteX's existing SPI controller. Native four-bit SD, DMA and an FPGA TCP/IP offload engine are outside the initial assignment. The Ethernet MAC is FPGA logic; filesystem and network protocol processing belong in software. An Ethernet link rate of 100 Mbit/s does not establish application throughput at that rate.

## Verified starting state and source entrypoints

Inspect these sources before changing their integration:

- `gateware/soc.py`: project SoC, CPU profiles, GPIO, clock constraints and DDR aliases.
- `scripts/build.py`, `scripts/memory.py`, `scripts/project.py`, `Makefile`: builds, directories, environment and commands.
- `scripts/cpu_profiles.py`, `cpu-profile-selection.json`: CPU identities; `performance` uses the accepted generated `dynamic_target` RTL.
- `scripts/ddr_test_*.py`, `scripts/benchmark_*.py`: artifact checking, timing gate, UART evidence and reporting patterns.
- `.deps/litex-boards/litex_boards/targets/sipeed_tang_primer_20k.py`: existing RMII construction; project integration currently passes `with_ethernet=False`.
- `.deps/litex-boards/litex_boards/platforms/sipeed_tang_primer_20k.py`: `spisdcard`, native `sdcard`, `eth_clocks` and `eth` resources.
- `.deps/litex/litex/soc/integration/soc.py`: `add_spi_sdcard()` and `add_ethernet()`.
- `.deps/litex/litex/soc/software/liblitesdcard/spisdcard.c`: existing initialization and reads; its `DISKOPS` currently omits writes and ioctl.
- `.deps/litex/litex/soc/software/libfatfs/`: bundled FatFs; `FF_FS_READONLY` is currently `1`.
- `.deps/liteeth/liteeth/phy/parallel/rmii.py`: actual implementation behind the compatibility import `liteeth.phy.rmii`.

The following are current facts, not new peripheral acceptance:

- System/CPU clock: **48 MHz**. DDR CK: **96 MHz**. UART: **115200, 8N1**.
- DDR part: `H5TQ1G63EFR`, **134217728 bytes / 128 MiB**; do not restore the former 256 MiB assumption.
- Cached DDR: `0x40000000..0x47ffffff`. CPU/L2-bypassed alias: `0xc0000000..0xc7ffffff`.
- Existing DDR builds reserve 8 KiB working SRAM, 8 KiB LiteDRAM L2 and 16 KiB diagnostic RAM at `0x20000000`.
- Reported current builds use **46/46 BSRAMs**. Ethernet packet buffers cannot simply be added without a resource budget and a fitting memory layout.
- The replacement board attached October 5 passed DDR qualification and DDR CoreMark on `lite`, `standard`, `performance` and `linux`. See `docs/ddr3/replacement-board-qualification-20261005.md` and its JSON manifest.
- `minimal` DDR has a recorded **-0.911 ns** setup failure from `gw2ddrphy_dqs_hold_1_s0/Q` to `DQS_1/HOLD`. It was not programmed or scored. Retain the timing gate and attempt supported remedies within this work; never suppress the path to manufacture acceptance.
- `linux` currently means the pinned MMU/supervisor/atomic-capable CPU running bare-metal tests. No Linux kernel was booted. `maxperf` remains provisional and excluded.

Original SD SPI pins are CLK `N10`, MOSI/CMD `R14`, CS/DAT3 `N11`, MISO/DAT0 `M8`; native card-detect is listed as `D15`. Validate the revision before use. Request SPI and native SD resources exclusively because they share pins. If adding card detect to SPI mode, expose only the additional detect signal without requesting overlapping native bus pins.

Use the existing dependency locks and patches. Pin any new lwIP dependency to an immutable upstream revision with reproducible setup and license attribution. Keep application-specific filesystem configuration separate from the BIOS's read-only configuration. Use project-owned sources/configuration or recorded dependency patches; do not leave unexplained edits in `.deps`.

## Hardware prerequisites and ownership

Assume the replacement standard Dock is available, but rediscover its interfaces and inspect active jobs before programming. The last verified UART was:

```text
/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
```

Generic debugger names and Gowin ID `0x81b` are not unique board identities. Associate every batch with the replacement board and record any newly available serial/revision information. Run `make doctor`; use `.venv`, `.tools`, the existing licensed Gowin launcher and `scripts/project.py` rather than changing tools unnecessarily.

Actual SD writes require an identified FAT16/FAT32 test volume with enough free space. Actual network acceptance requires a cable and a reachable test host; DHCP acceptance also requires a DHCP server. Discover host interface/address information without modifying unrelated network settings. Ask for missing physical prerequisites or a suitable test address only when discovery cannot resolve them; continue independent implementation and builds while waiting.

Use a uniquely named test directory and newly created files. File creation and writes inside that directory are part of the assignment. Do not overwrite existing files, format a card, repartition a host device, or perform unrestricted raw-sector writes. If destructive media preparation becomes necessary, identify the exact disposable media and obtain specific authorization first.

Before each hardware batch, check UART/JTAG ownership. Open UART before SRAM programming. Never run two hardware jobs concurrently or reset a board during an active SD write/network workload. On timeout or disconnect, retain evidence and establish safe completion or idle recovery before retrying. Flush/close files and stop the application before normal reconfiguration; mark interrupted write tests as incomplete.

## Implementation sequence

### 1. Optional integration and fitting builds

Add shared optional `SDCARD=none|spi` and `ETHERNET=none|rmii` configuration. Existing public builds remain peripheral-disabled by default. Add separate **`peripheral-build`, `peripheral-run`, and `peripheral-report`** targets accepting `PROFILE`, `MEMORY`, these feature selections and explicit `PORT` for hardware execution. Support `PROFILE=ALL` for builds and runs; retain per-profile failures and skip programming invalid builds while continuing independent eligible profiles.

Use isolated artifacts under `build/peripherals/<memory>/<profile>/<features>/`; do not overwrite existing DDR, GPIO or CoreMark images. Record the feature configuration, selected CPU RTL, ISA flags, linked extents, memory map, requested/actual clocks, source fingerprint, artifact hashes and routed results. Include new peripheral sources/configuration in freshness checks.

Bring up SD-only, Ethernet-only and combined variants on `standard`/DDR3 in that order. Use generated CSR and memory definitions instead of hard-coded offsets. Retain the project's supported generated CPU selection for `performance`.

Create a dedicated peripheral memory layout that fits the MAC buffers and application. Reclaim optional diagnostic reservations where needed and put the full application's code/data/heap/stack in DDR with checked bounds. Keep BIOS initialization and working SRAM viable. On-chip builds may use a smaller, explicitly recorded application memory reservation and diagnostic firmware; do not silently change existing public budgets or call a reduced diagnostic image full software support.

Retain a way to run destructive DDR qualification on the exact peripheral image before loading the DDR application. Its test code, data and stack must reside outside the DDR range being overwritten. If moving/removing diagnostic RAM, adapt the diagnostic and loader layout; never upload old firmware to a region that no longer exists. If that qualification path cannot fit, record the blocker instead of inheriting an old bitstream's DDR pass.

Default Ethernet buffering to two RX and two TX slots with standard frame capacity, and record actual slot counts/size. Account for packet memory, CDC storage, ROM, CPU caches and L2 in the BSRAM budget. If a smaller supported buffer layout is needed to fit, expose it in configuration and rerun all applicable network acceptance; do not truncate valid standard Ethernet frames.

Verify the board's Ethernet reference-clock source/direction; constrain the expected **50 MHz RMII reference** separately from the 48 MHz system clock. Audit reset synchronization, CDC and PHY I/O timing. The upstream board target disables automatic Ethernet timing constraints, so copying that call is insufficient evidence of a properly constrained build. Use only justified asynchronous exceptions; do not cut synchronous timing failures or weaken DDR constraints.

### 2. Writable SD driver and filesystem

Start initialization at **no more than 400 kHz** and normal transfers at **no more than 12 MHz**. Calculate the actual divider frequency and record it. Reliability comes before clock tuning.

Implement reusable initialization/status/read/write/ioctl support, including `CTRL_SYNC`, sector count/size, valid command/data responses, bounded busy/transfer timeouts, CSD/OCR capacity and CCS addressing, range/overflow checks, and propagation of failures. Single-sector writes are sufficient initially; multi-sector operations may iterate over them. Handle missing/removal events without indefinite loops or a falsely successful filesystem operation.

Build writable FatFs using a project-specific `ffconf.h` and matching headers/source. Set `FF_FS_READONLY=0`; keep formatting disabled. Provide FAT16/FAT32 mount, list, read, create/write, sync/close and reopen/verify operations. exFAT is not required; report unsupported formats clearly. SPI-controller presence or BIOS boot-file reads alone do not establish writable filesystem support.

Expose usable UART commands for card status, directory listing, file reads and an explicitly invoked file round-trip test. Use streamed buffers for large files and report file size, CRC/checksum, elapsed time and errors.

### 3. Ethernet driver, lwIP and combined application

Implement PHY reset/MDIO identification, negotiated link status, MAC frame access, RX release/TX completion and error counters. Use LiteEth's CPU-visible packet-buffer interface with uncached accesses as required. Start with polling; interrupt optimization and direct DMA into DDR are not required.

Integrate **lwIP with `NO_SYS=1`**, raw callbacks, bounded buffer pools, polling and timer servicing. Implement configurable locally administered unicast MAC addresses, explicit static IPv4 settings, DHCP, ARP, ICMP ping responses, UDP echo and TCP echo. Use lwIP for TCP rather than inventing a new TCP implementation. Do not use the BIOS's limited UDP helpers as evidence of full TCP support.

Provide UART network status/configuration commands. Let the host runner supply test IP/subnet/peer information; do not assume the user's LAN is `192.168.1.0/24` or silently use DHCP when a static test was requested. DHCP failure must be visible and recoverable. Fixed echo-service ports are UDP **5001** and TCP **5002**; a separate test-file upload service uses TCP **5003**, writes only a new file in the test directory, and returns a completion/checksum result after close.

Keep SD operations bounded and service network input/timers during long transfers, including card-busy waits. Handle link loss and TCP connection closure without stuck loops, leaked frame slots or corrupted file results. Close/sync writes before reporting persistence.

Demonstrate the integrated path: receive a deterministic 1 MiB binary payload over TCP, save it to SD, close it, reopen it, and compare the file with the host's expected length/checksum. Test SD and networking concurrently, not just two independent standalone bitstreams.

### 4. Extend coverage, resolve failures and optional Linux

Extend the same interfaces and driver implementation to all five registered profiles. Compile with each CPU's actual ISA/ABI; the `minimal` core must not receive unsupported multiply/atomic instructions. Test SD-only, Ethernet-only and combined builds for both memory modes. Use compact on-chip diagnostics where the full application cannot fit; label the capability level explicitly.

The full acceptance target is both peripherals together with files and networking on each feasible DDR configuration. Attempt supported fixes for profile-specific timing/resource/integration failures and retest affected images. Continue eligible profiles if another profile remains blocked. Report `minimal`'s existing DDR failure separately from any new failure; do not substitute a different CPU while keeping the same profile label.

Use a minimal Linux build only if the bare-metal path cannot satisfy the requested file/network features for a demonstrated software reason. Linux cannot remedy FPGA resource exhaustion or failed timing. If needed, keep Linux-specific artifacts separate and target the existing `linux` profile with DDR3.

Before adopting an upstream OS flow, verify CPU/ISA/interrupt/bootloader/kernel compatibility. Current Linux-on-LiteX sources select `vexriscv_smp`, whereas this repository uses `vexriscv` with `VexRiscv_Linux.v`; do not assume that flow is a drop-in replacement. A different CPU design is outside the default assignment and must be proposed explicitly before changing registered CPU identities.

If a compatible Linux fallback is necessary and feasible, pin Buildroot, kernel, bootloader and toolchain versions; build a minimal BusyBox initramfs; generate the device tree from the exact CSR/memory map with **128 MiB** RAM; use compatible SPI/MMC and LiteEth drivers. Prove boot to a UART shell, SD file read/write/reopen, and network tests. Start by loading images into RAM over UART. SD boot, flash boot, an SD root filesystem and NFS root are not required for initial Linux acceptance. Preserve the bare-metal support for the other cores.

## Validation and evidence

Run existing host/generated-design checks and add focused tests for configuration propagation, isolated artifacts, memory/ISA bounds, SD addressing/timeouts/write completion, filesystem write configuration, frame-slot handling, parser rejection of incomplete evidence and recovery behavior. Verify peripheral-disabled GPIO/build flows remain functional. Do not modify CoreMark algorithms or rerun unrelated CPU tuning.

Before any programming, require current-source identities, fitting resources and nonnegative required setup/hold/recovery timing. Estimated Fmax is not a substitute for checking the actual constrained paths.

For each eligible hardware configuration, record at least:

1. **SD:** successful card initialization with CID/CSD/capacity and actual SPI clocks; directory/file reads; 512-byte, 4 KiB and 1 MiB deterministic file write/close/reopen verification; missing-card handling and bounded recovery after removal between closed-file operations. Keep unrelated files untouched.
2. **Ethernet:** PHY identification and negotiated link; static-address ARP/ping; DHCP lease on an available DHCP network; binary UDP/TCP echo with varied payload lengths, including full-size standard frames through the stack; connect/disconnect/reconnect recovery; measured throughput and observed drops/errors.
3. **Combined:** verified 1 MiB TCP-to-SD transfer, followed by **at least 1800 measured seconds** of repeated file round trips and simultaneous network traffic. Record transfer counts, checksums, file outcomes, network counters and elapsed timer measurements. Require zero file/payload integrity mismatches and no unrecovered hang. Report observed network loss separately from integrity failures; do not advertise unmeasured line-rate throughput.
4. **DDR where affected:** ten fresh SRAM reconfiguration/training/smoke passes, full 128 MiB destructive suite, cache visibility and at least 1800 measured seconds of delayed-readback stress on the matching peripheral configuration. Complete destructive testing before loading a DDR-resident application. Historical qualification is background evidence, not acceptance of a changed image.

Use bounded waits and regular progress updates during long batches. Missing media, a missing network peer/DHCP service, timing failure, parser/capture failure and a real data mismatch are distinct statuses. A simulated or host-only result cannot replace a board pass. Never rewrite failed results into a passing aggregate.

Save UART RX/TX bytes, programmer output, timestamped events, host network logs, file/payload checksums, manifests and hash-verified build reports under `docs/peripherals/evidence/<session>/`. Preserve interrupted and failed sessions. Provide `docs/peripherals/report.md` and `docs/peripherals/results.json`, plus usage/reproduction documentation and README links.

Report a matrix covering each profile/memory/feature combination: build and timing, LUT/BSRAM use, memory layout, software capability level, SD read/write, static/DHCP network results, UDP/TCP tests, combined duration, DDR qualification, artifact/session identities and blockers. Do not pool historical/current boards or different build configurations.

## Recovery and final delivery

The last recorded idle image was `build/sipeed-ddr-test/idle-build/impl/pnr/ddr_idle.fs`, SHA-256 `a7a702e3ff9835ab3ee653f0f9ff7c90165808db514b3230dcc91d0314b93df3`. It holds DDR RESET# low, CKE low, CS# high, CK stopped and ODT low. Verify its hash and inspect its SD/PHY pin behavior before reuse; adapt recovery if those interfaces require explicit safe outputs.

After the final workload, sync/close files, stop network work, load a verified safe idle image into **SRAM**, retain programmer output and a quiet UART observation, and save new recovery evidence. Do not overwrite historical recovery records. JTAG detection/reset alone is not proof of idle recovery. Do not claim flash persistence, cold boot or power-cycle testing from SRAM reloads.

Completion requires implemented reusable controllers/drivers, reproducible commands and artifacts, actual file/network/combined acceptance on feasible configurations, a coverage matrix for all five profiles and both memory modes, retained evidence, documented concrete unresolved blockers, and verified final recovery. An unresolved requirement must be reported as partial, not silently omitted. Stop only for an actual prerequisite that cannot be resolved autonomously; complete independent work and state the exact missing input or observed blocker.

Finish with the implemented capabilities, measured outcomes, links to reports/evidence, exact reproduction commands, remaining limitations, Linux outcome if attempted, and final board state. Do not deliver another implementation plan in place of the requested working result.

## Primary references

- Sipeed hardware description, PHY and schematic links: <https://wiki.sipeed.com/hardware/en/tang/tang-primer-20k/primer-20k.html>.
- Pinned local LiteX/LiteEth/platform sources listed above are the integration source of truth; preserve their locked versions.
- FatFs application notes: <https://elm-chan.org/fsw/ff/doc/appnote.html>.
- lwIP upstream source: <https://git.savannah.nongnu.org/cgit/lwip.git/>.
- Linux compatibility reference, not a ready-made project build: <https://github.com/litex-hub/linux-on-litex-vexriscv>, particularly `soc_linux.py` and the Tang Primer 20K board definition.
