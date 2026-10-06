# Luna xhigh handoff: tune one core and validate on hardware

## Assignment and authorization

Use **Luna (`gpt-6-luna`) with extra-high (`xhigh`) reasoning** in `/home/user/git/fpga/sipeedtangprimer20k-riscv` to tune the complete bare-metal/RTOS single-core system, measure performance on the attached Tang Primer 20K, and validate DDR, native microSD and Ethernet on the resulting images. Implement and finish the bounded study; do not return only another plan.

The user explicitly authorized expanding to hardware testing and confirmed that a **microSD card and Ethernet cable are attached**. Do not repeat those attachment questions. Attachment does not establish card initialization, filesystem accessibility, PHY link or DHCP success; rediscover those through bounded tests.

This authorization supersedes the previous RTOS handoff's offline-only restriction for this assignment. Allow FPGA **SRAM-only programming**, UART serial loading, DDR tests, application benchmarks, SD test-file writes and local-network test traffic. Do not write onboard flash, format/repartition the SD, overwrite existing files, change host network configuration, commit or push. No second core, Linux, video, or clock-frequency changes. Preserve the one-core DDR/native-SD/Ethernet configuration throughout.

This handoff's preparation did not start another agent, change hardware or implement tuning. The intended future agent must report its actual model configuration if Luna/xhigh cannot be selected.

## Starting state and evidence

Start from the actual current revision/status, preserving user changes and all prior reports. At preparation the RTOS implementation checkpoint is `1bff53c`; do not assume HEAD will remain there. Read:

- [RTOS fit report](../rtos-system-fit/20261006T225330.751092Z/report.md), its manifests and BIOS map.
- [RTOS implementation handoff](system-fit-baremetal-rtos-luna-xhigh.md), plus `gateware/rtos_system_fit.py`, `scripts/rtos_system_fit.py`, `scripts/rtos_software.py`, and `firmware/rtos_bios/`.
- `docs/peripherals/resume.md`, peripheral firmware/host runners, and `docs/ddr3/replacement-board-qualification-20261005.md` for device custody, safe recovery, evidence identity and prior hardware outcomes.
- Pinned CPU generator recipe/lock and existing CoreMark build, identity, timing and DDR cache-maintenance helpers.

Reference hardware: `candidate-2-pinned-lite-no-l2`, RV32IM, 2 KiB I-cache, no D-cache/L2, 4 KiB working SRAM, 128 MiB DDR, 48 MHz CPU/system, 96 MHz DDR, 50 MHz RMII, native four-bit SD read/write DMA and one 2048-byte RX/TX Ethernet slot. BIOS binary 13,540 bytes with 16 KiB reservation, static boot-stack headroom 3,760 bytes. Routed utilization: logic 9,408/20,736; registers 4,825/16,173; BSRAM 27/46. Timing slacks: setup +0.648, hold +0.079, recovery +2.050, removal +1.532 ns.

The reference is **only offline-qualified**. Bare-metal, Zephyr kernel/peripherals and FreeRTOS compile; none has run on this image. Do not import DDR/peripheral runtime acceptance from older bitstreams. The prior 2 KiB L2 candidate missed setup by 0.602 ns. There are already 19 spare BSRAM blocks, so BIOS shrinkage is useful but is not a prerequisite for cache exploration.

Historical board notes record replacement core v3961 / Dock v3714 with no unique printed serial, a 32 GB exFAT card, LAN DHCP, SD initialization failures and absent PHY link. Verify the currently attached assembly and actual filesystem/link state without inventing a unique identifier. Historical failures do not establish today's result after the user's attachment confirmation.

## Isolated build and BIOS work

Add a separate tuning runner and manifests under `build/single-core-tuning/<UTC-run-id>/`; publish reports/evidence under `docs/single-core-tuning/<UTC-run-id>/`. Preserve the original study command and public CPU registry/selection files. Reuse integration, resource/timing parsing and hardware-session utilities without mislabeling candidates as registered profiles.

Keep geometry, peripheral functions, packet slots, buses, DDR PHY/reset corrections and all clocks fixed. Maintain both native SD DMA directions. Require routed timing closure before programming each image: active clocks, nonnegative setup/hold/recovery/removal slack and no unexpected unconstrained internal paths. Never bypass a gate or add broad false paths to accept a candidate.

Inspect BIOS ELF/map/symbol sizes before editing. Try fixed-string/hexadecimal UART output in place of general `printf`, then reduce DDR training verbosity with target-scoped changes. Preserve genuine DDR initialization/training, initialization-failure reporting, bounded smoke checking, loader CRC/address/length checks and instruction synchronization at handoff. Do not hard-code learned DDR calibration values or strip correctness checks to meet a size target.

Retain `-Os`, LTO, section garbage collection and real ROM initialization. Treat 8 KiB as an aspirational implemented-ROM target, not an acceptance requirement. Keep a 16 KiB link reservation and auto-size actual ROM. Report actual BSRAM savings; reservation changes or byte savings that do not cross mapping boundaries are not recovered blocks. Preserve at least 2 KiB static boot-stack headroom and require observed stack-canary margin during hardware runs. Freeze the final boot policy before candidate comparison; rebuild/rerun the pinned Lite baseline with that policy. Include all validation firmware changes in exact-image identities.

## Bounded single-core candidate matrix

First reproduce the pinned Lite baseline and hardware-qualify it. Then generate nine machine-mode RV32IM cache candidates from the locked Lite recipe:

- I-cache: **2, 4, 8 KiB** crossed with D-cache: **0, 2, 4 KiB**.
- L2 disabled; static prediction; iterative multiply/divide; light shifter; existing bypass/interrupt settings; no compressed ISA, atomics, MMU, supervisor, debug, CFU or PMP.
- Keep 4 KiB working SRAM. If a real static/observed stack budget requires 8 KiB, record the increase and use the same boot-SRAM policy for its matched baseline.

Record every generator argument, tool/source revision, RTL/YAML hash and actual CPU interfaces/capabilities. Include the generated 2 KiB/no-D-cache control to detect differences between pinned and regenerated RTL. Never infer ISA/cache sizes from a filename.

After measuring viable cache candidates, take the fastest validated cache configuration and build three additional compute variants: fast multiplier/divider plus full barrel shifter with static prediction; that same configuration with dynamic prediction; and that same configuration with dynamic-target prediction. Use the locked generator semantics (`singleCycleMulDiv=true`, `singleCycleShift=true`); do not describe division as literally completing in one cycle unless implementation evidence establishes it. This gives nine cache and three compute candidates, plus the pinned baseline. Do not widen the matrix or add L2/SMP/clock tuning.

Choose by measured CoreMark performance after correctness checks, not BIOS size, estimated Fmax or synthesis utilization. Any routing failure is a retained result, not a reason to silently alter the candidate.

## Cache, DMA and boot correctness

Adding D-cache changes the current no-cache assumptions. Before hardware scoring, implement and verify explicit DMA buffer ownership, line alignment, maintenance and ordering for native SD and Ethernet. Existing `fence w,o` / `fence i,r` additions provide ordering; they do **not** flush or invalidate dirty cache lines.

Use actual candidate cache geometry. Verify pinned VexRiscv-supported maintenance operations and CPU I/O-region mapping. Prefer uncached DMA buffers through a verified mapping or supported clean/invalidate primitives. If an uncached DDR alias is necessary, add it only to D-cache candidates, prove the physical mapping and use it consistently in drivers; record its resource/timing cost. Do not put cached and uncached simultaneous writers on the same cache line. Fences and volatile declarations alone are not a coherency solution.

The BIOS DDR smoke check must reach DDR rather than merely reread D-cache. Ensure serial loading and instruction-cache invalidation synchronize freshly loaded code. Validate loader handoff, SD DMA bidirectional transfers, peripheral MMIO treatment and DDR cached/uncached visibility before accepting a cached CPU. Verify timer/interrupt and RTOS port assumptions for each CPU.

## Hardware custody and staged qualification

One owner controls JTAG/UART for the entire batch. Inspect active programmers, serial sessions and existing workloads before device access. Rediscover the current serial-by-id path and programmer target; require an explicit port in runner invocations. Do not assume a timeout means a workload is idle. Capture completed/end/halt records or use the documented SRAM recovery/reset sequence to establish ownership before reprogramming.

Use hash-verified, fresh, timing-passing artifacts only; preserve raw UART RX/TX, logs and manifests per attempt. Adapt existing runners to isolated artifacts and native SD rather than passing `sdcard=spi` or stale public profile identifiers. Keep timeouts bounded and recover safely after failures.

1. **Fresh reference:** run ten SRAM reload/training/boot trials and record DDR lane settings/windows, loader smoke outcome and matching hashes. Establish baseline DDR correctness, SD read/write persistence, PHY link/local-network exchanges and CoreMark before tuning claims.
2. **Each scored candidate:** require three fresh successful reload/training trials, DDR geometry/boundary/alias and byte/halfword-neighbor checks, walking/fixed/inverted/address-derived/PRNG patterns over available test RAM, cache visibility and at least 60 measured seconds of delayed-readback stress. Native SD and Ethernet DMA smoke must pass before scoring.
3. **Final baseline and winner:** require ten reload/training trials and at least 1800 measured seconds of zero-error DDR stress, exhaustive patterns over available test RAM, full SD/network/combined checks and RTOS runtime smoke. Shortlist the top two candidates for this final qualification; accept the fastest fully passing one, otherwise retain the reference.

DDR tests must not destroy running code, stacks, live DMA buffers or result storage. Use small ROM-resident test routines with working-SRAM state where feasible; otherwise reserve live DDR regions explicitly. Record actual addresses and bytes tested and all exclusions. Never claim full 128 MiB destructive coverage while executable/test state remains inside that range. If boot-ROM testing changes the image, re-route and qualify the final image; prior exact-image acceptance is not transferable.

DDR tests may destroy otherwise unused RAM. Do not run destructive DDR phases concurrently with filesystem/network workloads using that RAM.

## Benchmark and peripheral acceptance

For CoreMark use the pinned unmodified algorithm, consistent `-O2`/no-LTO algorithm compilation and the existing untimed validation wrapper. Run one validation plus three scored trials, each at least ten measured seconds, with required seed/CRC checks. Score using actual 64-bit timer ticks and the fixed 48 MHz clock. Report mean, range, raw repetitions, CoreMark/s and CoreMark/MHz. Repeat the same-policy baseline at batch end to detect drift; rerun comparisons if baseline means differ by more than 2%. Rank full-precision means; ties prefer fewer BSRAM, then fewer logic units, then lexical candidate ID. Require at least a 3% improvement over the fresh baseline plus final correctness acceptance to select a winner. If no candidate qualifies, report no accepted gain and preserve the baseline.

Add fixed-size DDR read/write/copy bandwidth tests and timer/interrupt/RTOS tick measurements as secondary metrics. Record transfer size, cache state, access path, checksums and elapsed ticks. Include working sets larger than caches. Do not present cached data reuse as physical DDR bandwidth or confuse lower latency with higher throughput.

**microSD:** discover CID/CSD/capacity and filesystem read-only first. The user confirmed a card, not permission to format it. Preserve existing files and support the observed filesystem; prior notes requested exFAT. Use a uniquely named new test directory and new deterministic pattern files only, verify write/close/readback/checksum, then verify persistence after unmount/remount and an SRAM reload. Keep at least one small result file and record its path. Do not raw-write arbitrary sectors. Removal/reinsertion is optional and requires physical user assistance; it must not block ordinary attached-card testing. Mount/driver incompatibilities are software blockers, not a failed hardware fit.

**Ethernet:** identify PHY and observe link/autonegotiation before running DHCP. Use the local LAN/host only, a unique locally administered MAC and bounded UDP/TCP echo/integrity tests. Verify packet sizes up to normal Ethernet frame capacity, packet loss/retries, checksums and throughput. Bind host test servers to the intended interface, record/clean up their processes, and avoid changing host routes/firewall. If DHCP fails, inspect evidence first; request a suitable static IP/subnet/gateway only if it cannot be discovered safely. A connected cable is not proof of link; retain PHY status and ask for a physical cable/port check only on an observed blocker.

Run a ten-minute combined native-SD file transfer plus local network integrity workload with disjoint DDR buffers and checksums on baseline and final winner. Verify errors, timers and interrupts remain correct under contention; report combined throughput separately from standalone tests.

## Bare-metal, Zephyr and FreeRTOS runtime checks

Cross-compile all existing tracks against baseline and accepted candidate identities. Boot and exercise bare-metal UART/timer/interrupt smoke, Zephyr kernel threads/timers, Zephyr native SD and Ethernet, and FreeRTOS two-task scheduling with stack checks. Run final Zephyr/FreeRTOS scheduler smoke for five measured minutes each. No production FreeRTOS network/filesystem port is required; use Zephyr or bare-metal for peripheral acceptance.

Replace the Zephyr test random provider if runtime networking relies on random values: the compile-only fallback from the fit study is not an entropy claim. Preserve a unique explicit MAC. Record exact Zephyr bindings/driver revisions, target-scoped patches and cache maintenance. Label OS/driver compatibility failures separately from hardware timing and benchmark results; do not quietly remove failing tracks from the final report.

## Evidence, recovery and completion

Publish a machine-readable aggregate and concise report with all BIOS experiments, candidate build outcomes, CPU/ROM/cache/working-SRAM/resource identities, raw routing/timing evidence, DDR coverage/exclusions, benchmark repetitions, SD file paths/checksums, network PHY/DHCP/test results and RTOS outcomes. Keep hash-linked final artifacts and commands that reproduce them. Preserve failures and exact external blockers.

At each interrupted batch and final completion, use the documented hash-verified SRAM idle recovery image, with DDR reset, SD deselected/clock stopped and Ethernet TX disabled. Confirm programmer success and five seconds of UART quiet or equivalent explicit idle evidence. Do not write flash. If safe recovery cannot be completed, report actual hardware state and the required user action.

Completion requires the bounded study, a fresh verified reference, all feasible candidate outcomes, a fully hardware-qualified selected configuration or an honest no-winner result, and recorded safe final board state. If a physical/network prerequisite blocks work, complete independent build/benchmark/DDR/software work and report the exact blocker without claiming peripheral acceptance. Do not register or replace existing public CPU profiles automatically. Final response: accepted measured gain or no gain, exact selected configuration, DDR/SD/Ethernet/RTOS status, report link and remaining limitations.
