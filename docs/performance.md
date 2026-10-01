# VexRiscv CoreMark performance

**Status: INCOMPLETE — Linux CoreMark captures could not be revalidated against current artifacts; DDR3 qualification failed; no maxperf winner was accepted.**

## Measurement identity

- Board: Sipeed Tang Primer 20K with standard Dock; device `GW2A-LV18PG256C8/I7`.
- Measurement: 2026-10-01T20:11:34.365848+00:00; selected profile: `linux`.
- SRAM programming status: **completed**; no flash programming is used.
- Benchmark memory mode: **onchip**.
- Device discovery at session start: 3 `/dev/serial/by-id/` paths and 7 USB device nodes were visible.
- Configured SoC operating clock: **48,000,000 Hz**; UART selected: `/dev/ttyUSB1`; requested device: `/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0` at 115200 baud.
- Repository revision: `bf81c39526abbf61261626d0d6a0767f098e4abd`; dirty at capture: `True`; source fingerprint: `b3be35e7ddbe2eac0f9120430e6826f2ecd0899af7ac52195fb210d8f38ec5c0`.
- CoreMark upstream commit: `1f483d5b8316753a742cbf5590caf5bd0a4e4777`. coremark.md5 reports coremark.h as failed although the checkout is clean at the pinned commit; all five protected algorithm C sources pass the upstream MD5 check.
- Tool versions: CPython 3.12.14; RISC-V GCC `exit 0: riscv-none-elf-gcc (xPack GNU RISC-V Embedded GCC x86_64) 15.2.0`; Gowin `V1.9.12.04`; openFPGALoader `openFPGALoader v0.13.1`.
- Compiler flags for the upstream algorithm sources:
  ```sh
-std=gnu99 -O2 -march=rv32i2p0_ma -mabi=ilp32 -D__vexriscv__ -g3 -no-pie -fomit-frame-pointer -Wall -fno-builtin -fno-stack-protector -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -ffunction-sections -fdata-sections -fno-lto -DITERATIONS=1000 -DTOTAL_DATA_SIZE=2000 -DMEM_METHOD=MEM_STATIC -DMULTITHREAD=1 -DPERFORMANCE_RUN=1 -I/home/user/git/fpga/sipeedtangprimer20k-riscv/firmware/benchmark -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/coremark -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/linux/benchmark -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/linux/software/include -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software/include -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software/libbase -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/cores/cpu/vexriscv -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/linux/software/libc -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/pythondata-software-picolibc/pythondata_software_picolibc/data/libc/include -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/linux/benchmark/performance
```
- Memory placement: Code and read-only data in 32 KiB main RAM at `0x40000000`; CoreMark data/BSS in 8 KiB on-chip SRAM at `0x10000000`; a 2,048-byte stack is reserved. The 2,000-byte CoreMark data area is statically allocated.
- Generated cache configuration: `{'instruction_cache': 'enabled', 'instruction_cache_bytes': 4096, 'data_cache': 'enabled', 'data_cache_bytes': 4096, 'configuration_source': 'build/linux/software/include/generated/soc.h'}`. Build metadata verifies the pinned LiteX `libbase/uart.c` polling backend (`-DUART_POLLING`), which avoids an interrupt-driven TX queue while benchmark interrupts are disabled. Startup, CRC, and timer output follows the timed CoreMark workload.
- Trial startup and recovery: Each trial opens the selected UART before SRAM reprogramming and records received and transmitted bytes. The runner only attempts LiteX serialboot recovery after seeing the BIOS console before any benchmark start marker. Each trial reprograms the design into FPGA SRAM and uploads fresh firmware through the LiteX BIOS serial loader; reconfiguration resets the design, and no reset is issued during a run. Each scored trial first runs a separate 1000-iteration calibration workload and derives the scored iteration count with `ceil(calibration_iterations * target_seconds * clock_hz / calibration_ticks)` for a 20-second target; calibration time is excluded from the score. The scored pass invokes upstream CoreMark again and reinitializes its static algorithm data while retaining the same firmware, profile, and memory placement. Before every upstream invocation, `portable_init` fences and flushes the CPU data and instruction caches outside the timed workload, so the calibration pass does not warm the scored pass. Timer and CRC output follows the scored workload.
- Runtime SRAM and seed preflight: Before timed CoreMark work, firmware records the initial linked-image CRC32 and size, checks a 256-byte, 32-byte-aligned volatile BSS array of 64 union words probe (two 32-bit write/read patterns cached and after cache maintenance; low/high halfword writes checking neighboring-half preservation and unsigned/signed reads; four byte-lane writes checking neighboring-byte preservation and unsigned/signed reads; halfword patterns include 0x0000, 0x7fff, 0x8000, 0xffff; byte patterns include 0x00, 0x7f, 0x80, 0xff; tight volatile back-to-back SH/LH and SB/LB sequences with same-location load-modify-store checks and neighbor preservation; alternating volatile SRAM stores and read-only linked-image loads at matching low-12 address bits (a D-cache index conflict when that cache is enabled), followed by SRAM/code checks) and validates volatile inputs `seed1_volatile, seed2_volatile, seed3_volatile, seed4_volatile` against the selected mode and iteration count. RAM and seed checks must pass before an upstream invocation. After each calibration and scored timed interval, firmware computes the linked-image CRC32 through cached reads, flushes the caches, then checks the CRC32 and size again against the initial image values. These integrity checks are outside timing; a mismatch halts the run. benchmark_main.c is compiled with -Os to fit untimed diagnostics in 32-KiB main RAM; CoreMark sources retain the profile-wide -O2 flag.

## Build and profile comparison

| Profile | Build | ISA / ABI | LUT / ALU | Registers | BSRAM | Timing constraint | Worst slack | Estimated Fmax | Perf / validation image | Maximum SRAM data+BSS+padding / stack / free | Largest static frame | Hardware benchmark |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `minimal` | build passed; prior scores retained | `rv32i2p0 / ilp32` | 2247 / 300 | 1382 / 16173 | 38 / 46 | 48 MHz | 5.752 ns | 66.307 MHz | 29928 / 29952 B | 2600+24=2624 / 2048 / 3520 B | 1184 B | prior accepted runs |
| `lite` | build passed; prior scores retained | `rv32i2p0_m / ilp32` | 3127 / 443 | 1639 / 16173 | 40 / 46 | 48 MHz | 2.920 ns | 55.823 MHz | 28360 / 28392 B | 2600+24=2624 / 2048 / 3520 B | 1152 B | prior accepted runs |
| `standard` | build passed; prior scores retained | `rv32i2p0_m / ilp32` | 3452 / 479 | 1889 / 16173 | 46 / 46 | 48 MHz | 6.471 ns | 69.628 MHz | 28376 / 28400 B | 2600+24=2624 / 2048 / 3520 B | 1152 B | prior accepted runs |
| `performance` | build passed; prior scores retained | `rv32i2p0_m / ilp32` | 3656 / 521 | 2060 / 16173 | 46 / 46 | 48 MHz | 5.325 ns | 64.482 MHz | 28360 / 28400 B | 2600+24=2624 / 2048 / 3520 B | 1152 B | prior accepted runs |
| `linux` | build passed; capture identity stale | `rv32i2p0_ma / ilp32` | 4615 / 702 | 2709 / 16173 | 46 / 46 | 48 MHz | 2.448 ns | 54.390 MHz | 28368 / 28400 B | 2600+24=2624 / 2048 / 3520 B | 1152 B | captured; not accepted |

GCC's largest per-function static frame is shown as a stack sizing check, not a runtime high-water mark; firmware reserves 2,048 stack bytes. The SRAM table reports the larger of the validation and performance image extents: linked `_end - _fdata` (data+BSS plus alignment padding), remaining space `_stack_bottom - _end`, and the reserved stack from `_stack_bottom` to `_stack_top`. Synthesis and place-and-route estimated Fmax values are implementation estimates. Any CoreMark score reported here uses the **48 MHz operating clock**; estimated Fmax is not the operating frequency.

## Hardware diagnostics

FPGA SRAM programming and UART firmware upload completed. The firmware-reported image CRC32 `f1146874` and size 28368 bytes match the host-verified firmware binary. The runtime SRAM probe passed. Raw UART evidence: [verified raw UART capture](<../docs/performance/20261001T201134.365368Z-onchip-linux-performance-3.uart.bin>) (SHA256 `6d76bd96cf2b234e83e3ae8de2c0d843eab1035238d9c7cba2945a81da2bd50d`, verified; 4700 bytes).

```text
BENCHMARK_IMAGE_CRC32 build_id=b3be35e7ddbe2eac value=f1146874 size=28368
BENCHMARK_RAM_CHECK status=passed words=64 bytes=256 alignment=32 phases=word,halfword_u16_s16,byte_u8_s8,tight_sh_lh_rmw,tight_sb_lb_rmw,code_conflict,cache_flushed errors=0 code_conflict_address=40000920
BENCHMARK_IMAGE_RECHECK phase=calibration cached=f1146874 flushed=f1146874 expected=f1146874 size=28368 cached_size=28368 flushed_size=28368
BENCHMARK_IMAGE_RECHECK phase=scored cached=f1146874 flushed=f1146874 expected=f1146874 size=28368 cached_size=28368 flushed_size=28368
```

## CPU candidate and memory-mode comparison

On-chip and DDR3 CoreMark results are revalidated separately. Legacy sessions without a memory field are treated as on-chip.

| CPU profile | On-chip CoreMark mean | On-chip LUT / BSRAM | DDR3 CoreMark mean | DDR3 LUT / BSRAM | DDR3 status |
|---|---:|---:|---:|---:|---|
| `minimal` | 16.588341 | 2247 / 38 | — | 5549 / 46 | not_measured |
| `lite` | 50.427512 | 3127 / 40 | — | 6422 / 46 | not_measured |
| `standard` | 109.127482 | 3452 / 46 | — | 7891 / 46 | not_measured |
| `performance` | 119.323916 | 3656 / 46 | — | 9116 / 46 | not_measured |
| `linux` | — | 4615 / 46 | — | 8860 / 46 | not_measured |

The `linux` run completed one validation and three scored trials at 48 MHz, with a raw timer-derived mean of **109.12748097592832 CoreMark** and zero observed spread. The current report validator rejects those captures because the build/source identities no longer match the stored session; the score is preserved as diagnostic evidence and excluded from accepted aggregates. The CPU is Linux-capable (RV32IMA with MMU, supervisor, and atomics); kernel, bootloader, root filesystem, device tree, and boot-to-shell work remain deferred.

### Standard-derived CPU candidates

| Prediction candidate | Build/synthesis status | LUT / BSRAM | Worst setup slack | Board measurement | Mean CoreMark |
|---|---|---:|---:|---|---:|
| `dynamic` | ready_for_board_measurement | 3540 / 46 | 5.245 ns | passed | 111.487554 |
| `dynamic_target` | ready_for_board_measurement | 3656 / 46 | 5.325 ns | passed | 119.323913 |

The measured winner `dynamic_target` is registered as the public `performance` profile. At 48 MHz it averaged 119.323913 CoreMark versus a fresh `standard` mean of 109.127482, a 10.196432 CoreMark (9.34%) increase. The selection and evaluation identity are recorded in [cpu-profile-selection.json](../cpu-profile-selection.json).

### DDR3 training and integrity

DDR3 acceptance status: **failed**. Training logs, full-range memory coverage, cached/uncached visibility, and sustained stress evidence are summarized in [the DDR3 report](ddr3/report.md).

### Bounded maxperf tuning

The complete matrix contains 8 on-chip and 18 DDR3 candidates, generated through the locked `GenCoreDefault` Wishbone recipe in [cpu-generator.lock.json](../cpu-generator.lock.json). Candidate IDs include I-cache, D-cache, and ISA. The 4 KiB/4 KiB RV32IM DDR candidate is the reproduction control; it was built and timed but not board-scored. Full generator arguments, tool versions, RTL SHA-256 values, bitstreams, firmware, reports, and failure logs are retained in the candidate manifests and build batches.

On-chip builds routed at the fixed 48 MHz clock. The fresh `performance` baseline passed validation and three scored repetitions at **119.3239155434707 CoreMark**. The first five candidates each passed validation and three scored trials, and none beat that baseline. Candidate 6 stopped at a partial BIOS banner, so the runner did not program candidates 7 or 8.

| On-chip candidate | Build | LUT / BSRAM | Setup slack | RTL SHA-256 prefix | Board mean CoreMark | Result |
|---|---|---:|---:|---|---:|---|
| `icache-2048_dcache-2048_rv32im` | passed | 3663 / 46 | 4.205 ns | `4067ada71979` | 114.129016 | below baseline |
| `icache-2048_dcache-2048_rv32imc` | passed | 4007 / 46 | 0.401 ns | `4bed7daba1d2` | 114.513239 | below baseline |
| `icache-2048_dcache-4096_rv32im` | passed | 3584 / 46 | 5.617 ns | `464e12900014` | 117.683525 | below baseline |
| `icache-2048_dcache-4096_rv32imc` | passed | 3996 / 46 | 0.154 ns | `c464329d9cca` | 117.747854 | below baseline |
| `icache-4096_dcache-2048_rv32im` | passed | 3718 / 46 | 4.848 ns | `c9b94796e0fb` | 115.676275 | below baseline |
| `icache-4096_dcache-2048_rv32imc` | passed; board run stopped | 4006 / 46 | 0.218 ns | `77c6c4279e7c` | — | 36-byte partial BIOS banner; no start/end/halt marker |
| `icache-4096_dcache-4096_rv32im` | passed | 3656 / 46 | 5.325 ns | `771f520fcf3f` | — | not measured after safe-stop rule |
| `icache-4096_dcache-4096_rv32imc` | passed | 3995 / 46 | 0.242 ns | `a2f2a92e7ff3` | — | not measured after safe-stop rule |

DDR3 candidate builds have 4 timing/resource-viable candidates. The 4 KiB/2 KiB RV32IM and 4 KiB/4 KiB RV32IMC candidates missed timing; 8 KiB I-cache candidates failed placement; every 16 KiB I-cache candidate exceeded the 20,736-logic device limit. The 8 KiB I-cache candidates left 4,792–8,408 registers unplaced. DDR3 candidate scoring was not started because a fresh `performance` DDR reference has not qualified. Prior `performance` DDR acceptance attempts and the latest Linux DDR attempt trained, then failed BIOS Memtest with a data error; they did not establish the required reference or authorize candidate scoring.

| DDR3 candidate | Build outcome | LUT / BSRAM | Setup slack | Timing estimate | RTL SHA-256 prefix | Board score / reason |
|---|---|---:|---:|---:|---|---|
| `icache-4096_dcache-2048_rv32im` | timing rejected | — | -0.060 ns | 48.870 MHz | `c9b94796e0fb` | not measured |
| `icache-4096_dcache-2048_rv32imc` | ready | 9024 / 46 | 0.385 ns | 48.904 MHz | `77c6c4279e7c` | not measured; DDR preflight failed |
| `icache-4096_dcache-4096_rv32im` | ready; control | 9116 / 46 | 0.530 ns | 49.252 MHz | `771f520fcf3f` | not measured; DDR preflight failed |
| `icache-4096_dcache-4096_rv32imc` | timing rejected | — | -0.223 ns | 47.493 MHz | `a2f2a92e7ff3` | not measured |
| `icache-4096_dcache-8192_rv32im` | ready | 9653 / 46 | 0.211 ns | 48.492 MHz | `720f3fe83dce` | not measured; DDR preflight failed |
| `icache-4096_dcache-8192_rv32imc` | ready | 10038 / 46 | 0.017 ns | 48.038 MHz | `fcc72230818f` | not measured; DDR preflight failed |
| `icache-8192_dcache-2048_rv32im` | placement rejected | — | — | — | `743a25711243` | 4,792 registers unplaced |
| `icache-8192_dcache-2048_rv32imc` | placement rejected | — | — | — | `6544dc15fc32` | 4,794 registers unplaced |
| `icache-8192_dcache-4096_rv32im` | placement rejected | — | — | — | `a30808493279` | 6,470 registers unplaced |
| `icache-8192_dcache-4096_rv32imc` | placement rejected | — | — | — | `60cae7c7e07d` | 6,578 registers unplaced |
| `icache-8192_dcache-8192_rv32im` | placement rejected | — | — | — | `bdcc89c99e69` | 8,400 registers unplaced |
| `icache-8192_dcache-8192_rv32imc` | placement rejected | — | — | — | `103625e57c6f` | 41 LUTs and 8,408 registers unplaced |
| `icache-16384_dcache-2048_rv32im` | logic limit rejected | — | — | — | `d6613084b259` | 30,139 logic elements; limit 20,736 |
| `icache-16384_dcache-2048_rv32imc` | logic limit rejected | — | — | — | `c34cb26fcd5a` | 30,328 logic elements; limit 20,736 |
| `icache-16384_dcache-4096_rv32im` | logic limit rejected | — | — | — | `5740a731a680` | 30,100 logic elements; limit 20,736 |
| `icache-16384_dcache-4096_rv32imc` | logic limit rejected | — | — | — | `de79289f8f67` | 30,322 logic elements; limit 20,736 |
| `icache-16384_dcache-8192_rv32im` | logic limit rejected | — | — | — | `6af169ef75fc` | 31,655 logic elements; limit 20,736 |
| `icache-16384_dcache-8192_rv32imc` | logic limit rejected | — | — | — | `c09cfdca93de` | 32,181 logic elements; limit 20,736 |

No candidate selection file was created and `maxperf` is not registered publicly. The current on-chip evaluation is [stopped before the remaining trials](performance/maxperf-evaluation/20261001T202219.864127Z-onchip-maxperf/evaluation.json); its raw RX/TX captures are in that directory. On-chip and DDR3 build matrices, full RTL hashes, and rejected-build logs are linked from [the on-chip build batch](performance/maxperf-evaluation/20261001T201408.429405Z-onchip-maxperf-build/candidate-build.json) and [the DDR3 build batch](performance/maxperf-evaluation/20261001T202139.975865Z-ddr3-maxperf-build/candidate-build.json). The latest DDR3 Linux acceptance attempt is documented in [the DDR3 report](ddr3/report.md).

## Validation and scored runs

**failed (capture evidence rejected)**: captured profile/build artifacts are no longer verified

| Repetition | Iterations | Elapsed ticks | Seconds | CoreMark | CoreMark/MHz | Validation | UART evidence |
|---|---:|---:|---:|---:|---:|---|---|
| Validation seeds | — | — | — | — | — | failed (capture evidence rejected) | [UART log](<../docs/performance/20261001T201134.365368Z-onchip-linux-validation.uart.log>) |
| Performance 1 | — | — | — | — | — | failed (capture evidence rejected) (captured profile/build artifacts are no longer verified) | [UART log](<../docs/performance/20261001T201134.365368Z-onchip-linux-performance-1.uart.log>) |
| Performance 2 | — | — | — | — | — | failed (capture evidence rejected) (captured profile/build artifacts are no longer verified) | [UART log](<../docs/performance/20261001T201134.365368Z-onchip-linux-performance-2.uart.log>) |
| Performance 3 | — | — | — | — | — | failed (capture evidence rejected) (captured profile/build artifacts are no longer verified) | [UART log](<../docs/performance/20261001T201134.365368Z-onchip-linux-performance-3.uart.log>) |

No aggregate is reported until validation and all three scored repetitions pass the capture checks.

Raw elapsed timer counters are preserved as 64-bit values in `results.json`; the UART port emits the high and low 32-bit words separately. Scores are recalculated on the host as `iterations × clock_hz / elapsed_ticks` and `iterations × 1,000,000 / elapsed_ticks`. The compact bare-metal build disables CoreMark's optional floating-point text; upstream integer iterations/second and whole-second duration are cross-checked, while report scores use full-resolution timer ticks. Failed or incomplete captures do not enter aggregates.

## Evidence and reproduction

- [Machine-readable results and artifact hashes](performance/results.json)
- [Measured CPU profile selection](../cpu-profile-selection.json), [candidate P&R evidence](performance/cpu-evaluation/candidate-build.json), and [candidate board sessions](performance/cpu-evaluation/evaluations.json)
- [linux validation 0 UART log](<../docs/performance/20261001T201134.365368Z-onchip-linux-validation.uart.log>), [linux validation 0 raw UART bytes](<../docs/performance/20261001T201134.365368Z-onchip-linux-validation.uart.bin>), [linux validation 0 transmitted UART bytes](<../docs/performance/20261001T201134.365368Z-onchip-linux-validation.uart.tx.bin>), [linux performance 1 UART log](<../docs/performance/20261001T201134.365368Z-onchip-linux-performance-1.uart.log>), [linux performance 1 raw UART bytes](<../docs/performance/20261001T201134.365368Z-onchip-linux-performance-1.uart.bin>), [linux performance 1 transmitted UART bytes](<../docs/performance/20261001T201134.365368Z-onchip-linux-performance-1.uart.tx.bin>), [linux performance 2 UART log](<../docs/performance/20261001T201134.365368Z-onchip-linux-performance-2.uart.log>), [linux performance 2 raw UART bytes](<../docs/performance/20261001T201134.365368Z-onchip-linux-performance-2.uart.bin>), [linux performance 2 transmitted UART bytes](<../docs/performance/20261001T201134.365368Z-onchip-linux-performance-2.uart.tx.bin>), [linux performance 3 UART log](<../docs/performance/20261001T201134.365368Z-onchip-linux-performance-3.uart.log>), [linux performance 3 raw UART bytes](<../docs/performance/20261001T201134.365368Z-onchip-linux-performance-3.uart.bin>), [linux performance 3 transmitted UART bytes](<../docs/performance/20261001T201134.365368Z-onchip-linux-performance-3.uart.tx.bin>)
- Gowin reports: [minimal P&R resources](<../build/minimal/gateware/impl/pnr/project.rpt.txt>), [minimal timing report](<../build/minimal/gateware/impl/pnr/project.tr>), [minimal synthesis resources](<../build/minimal/gateware/impl/gwsynthesis/project_syn_resource.html>), [lite P&R resources](<../build/lite/gateware/impl/pnr/project.rpt.txt>), [lite timing report](<../build/lite/gateware/impl/pnr/project.tr>), [lite synthesis resources](<../build/lite/gateware/impl/gwsynthesis/project_syn_resource.html>), [standard P&R resources](<../build/standard/gateware/impl/pnr/project.rpt.txt>), [standard timing report](<../build/standard/gateware/impl/pnr/project.tr>), [standard synthesis resources](<../build/standard/gateware/impl/gwsynthesis/project_syn_resource.html>), [performance P&R resources](<../build/performance/gateware/impl/pnr/project.rpt.txt>), [performance timing report](<../build/performance/gateware/impl/pnr/project.tr>), [performance synthesis resources](<../build/performance/gateware/impl/gwsynthesis/project_syn_resource.html>), [linux P&R resources](<../build/linux/gateware/impl/pnr/project.rpt.txt>), [linux timing report](<../build/linux/gateware/impl/pnr/project.tr>), [linux synthesis resources](<../build/linux/gateware/impl/gwsynthesis/project_syn_resource.html>)
- Build/session artifacts: [build/benchmarks/20261001T011926Z-onchip-build](<../build/benchmarks/20261001T011926Z-onchip-build>)
- Pinned dependency and preserved license: [dependencies.lock.json](../dependencies.lock.json), [CoreMark license](../firmware/benchmark/LICENSE.md).

Rebuild all profiles and both firmware variants:

```sh
make doctor
make benchmark-build
```

Run validation plus three scored repetitions on one selected profile:

```sh
make benchmark-run PROFILE=linux PORT=/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
make benchmark-report
```

## Interpretation and limits

FPGA SRAM programming completed, but the selected profile has no accepted CoreMark result. The session could not be accepted after checking its raw UART capture and artifact hashes: current build or firmware hashes do not match the captured session identity; validation 0: captured profile/build artifacts are no longer verified; performance 1: captured profile/build artifacts are no longer verified; performance 2: captured profile/build artifacts are no longer verified; performance 3: captured profile/build artifacts are no longer verified; no revalidated validation-seed trial is available; three revalidated performance repetitions are not available. Failed and incomplete sessions remain in `results.json` and do not enter score aggregates. The intended placement is: Code and read-only data run from 32 KiB on-chip main RAM; CoreMark data/BSS and the reserved stack stay in 8 KiB SRAM. Place-and-route estimated Fmax is separate from the configured 48 MHz operating clock. Profiles marked **not measured** have no inferred scores, and this report does not establish a ranking for them.
