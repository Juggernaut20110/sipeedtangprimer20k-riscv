# VexRiscv CoreMark performance

**Status: complete.**

## Measurement identity

- Board: Sipeed Tang Primer 20K with standard Dock; device `GW2A-LV18PG256C8/I7`.
- Measurement: 2026-10-01T01:22:11.920237+00:00; selected profile: `performance`.
- SRAM programming status: **completed**; no flash programming is used.
- Benchmark memory mode: **onchip**.
- Device discovery at session start: 3 `/dev/serial/by-id/` paths and 7 USB device nodes were visible.
- Configured SoC operating clock: **48,000,000 Hz**; UART selected: `/dev/ttyUSB1`; requested device: `/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0` at 115200 baud.
- Repository revision: `bf60a40940fa7a0542b71e21481b474aa2bcec27`; dirty at capture: `True`; source fingerprint: `b2c11c96db08c8ff1c84e0a0319192b553be5c4c4344c9c2ce86bdc35777c983`.
- CoreMark upstream commit: `1f483d5b8316753a742cbf5590caf5bd0a4e4777`. coremark.md5 reports coremark.h as failed although the checkout is clean at the pinned commit; all five protected algorithm C sources pass the upstream MD5 check.
- Tool versions: CPython 3.12.14; RISC-V GCC `exit 0: riscv-none-elf-gcc (xPack GNU RISC-V Embedded GCC x86_64) 15.2.0`; Gowin `V1.9.12.04`; openFPGALoader `openFPGALoader v0.13.1`.
- Compiler flags for the upstream algorithm sources:
  ```sh
-std=gnu99 -O2 -march=rv32i2p0_m -mabi=ilp32 -D__vexriscv__ -g3 -no-pie -fomit-frame-pointer -Wall -fno-builtin -fno-stack-protector -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -ffunction-sections -fdata-sections -fno-lto -DITERATIONS=1000 -DTOTAL_DATA_SIZE=2000 -DMEM_METHOD=MEM_STATIC -DMULTITHREAD=1 -DPERFORMANCE_RUN=1 -I/home/user/git/fpga/sipeedtangprimer20k-riscv/firmware/benchmark -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/coremark -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/performance/benchmark -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/performance/software/include -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software/include -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software/libbase -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/cores/cpu/vexriscv -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/performance/software/libc -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/pythondata-software-picolibc/pythondata_software_picolibc/data/libc/include -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/performance/benchmark/performance
```
- Memory placement: Code and read-only data in 32 KiB main RAM at `0x40000000`; CoreMark data/BSS in 8 KiB on-chip SRAM at `0x10000000`; a 2,048-byte stack is reserved. The 2,000-byte CoreMark data area is statically allocated.
- Generated cache configuration: `{'instruction_cache': 'enabled', 'data_cache': 'enabled', 'configuration_source': 'build/performance/software/include/generated/soc.h'}`. Build metadata verifies the pinned LiteX `libbase/uart.c` polling backend (`-DUART_POLLING`), which avoids an interrupt-driven TX queue while benchmark interrupts are disabled. Startup, CRC, and timer output follows the timed CoreMark workload.
- Trial startup and recovery: Each trial opens the selected UART before SRAM reprogramming and records received and transmitted bytes. The runner only attempts LiteX serialboot recovery after seeing the BIOS console before any benchmark start marker. Each trial reprograms the design into FPGA SRAM and uploads fresh firmware through the LiteX BIOS serial loader; reconfiguration resets the design, and no reset is issued during a run. Each scored trial first runs a separate 1000-iteration calibration workload and derives the scored iteration count with `ceil(calibration_iterations * target_seconds * clock_hz / calibration_ticks)` for a 20-second target; calibration time is excluded from the score. The scored pass invokes upstream CoreMark again and reinitializes its static algorithm data while retaining the same firmware, profile, and memory placement. Before every upstream invocation, `portable_init` fences and flushes the CPU data and instruction caches outside the timed workload, so the calibration pass does not warm the scored pass. Timer and CRC output follows the scored workload.
- Runtime SRAM and seed preflight: Before timed CoreMark work, firmware records the initial linked-image CRC32 and size, checks a 256-byte, 32-byte-aligned volatile BSS array of 64 union words probe (two 32-bit write/read patterns cached and after cache maintenance; low/high halfword writes checking neighboring-half preservation and unsigned/signed reads; four byte-lane writes checking neighboring-byte preservation and unsigned/signed reads; halfword patterns include 0x0000, 0x7fff, 0x8000, 0xffff; byte patterns include 0x00, 0x7f, 0x80, 0xff; tight volatile back-to-back SH/LH and SB/LB sequences with same-location load-modify-store checks and neighbor preservation; alternating volatile SRAM stores and read-only linked-image loads at matching low-12 address bits (a D-cache index conflict when that cache is enabled), followed by SRAM/code checks) and validates volatile inputs `seed1_volatile, seed2_volatile, seed3_volatile, seed4_volatile` against the selected mode and iteration count. RAM and seed checks must pass before an upstream invocation. After each calibration and scored timed interval, firmware computes the linked-image CRC32 through cached reads, flushes the caches, then checks the CRC32 and size again against the initial image values. These integrity checks are outside timing; a mismatch halts the run. benchmark_main.c is compiled with -Os to fit untimed diagnostics in 32-KiB main RAM; CoreMark sources retain the profile-wide -O2 flag.

## Build and profile comparison

| Profile | Build | ISA / ABI | LUT / ALU | Registers | BSRAM | Timing constraint | Worst slack | Estimated Fmax | Perf / validation image | Maximum SRAM data+BSS+padding / stack / free | Largest static frame | Hardware benchmark |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `minimal` | passed | `rv32i2p0 / ilp32` | 2247 / 300 | 1382 / 16173 | 38 / 46 | 48 MHz | 5.752 ns | 66.307 MHz | 29928 / 29952 B | 2600+24=2624 / 2048 / 3520 B | 1184 B | not measured |
| `lite` | passed | `rv32i2p0_m / ilp32` | 3127 / 443 | 1639 / 16173 | 40 / 46 | 48 MHz | 2.920 ns | 55.823 MHz | 28360 / 28392 B | 2600+24=2624 / 2048 / 3520 B | 1152 B | not measured |
| `standard` | passed | `rv32i2p0_m / ilp32` | 3452 / 479 | 1889 / 16173 | 46 / 46 | 48 MHz | 6.471 ns | 69.628 MHz | 28376 / 28400 B | 2600+24=2624 / 2048 / 3520 B | 1152 B | not measured |
| `performance` | passed | `rv32i2p0_m / ilp32` | 3656 / 521 | 2060 / 16173 | 46 / 46 | 48 MHz | 5.325 ns | 64.482 MHz | 28360 / 28400 B | 2600+24=2624 / 2048 / 3520 B | 1152 B | measured (3 valid repetitions) |

GCC's largest per-function static frame is shown as a stack sizing check, not a runtime high-water mark; firmware reserves 2,048 stack bytes. The SRAM table reports the larger of the validation and performance image extents: linked `_end - _fdata` (data+BSS plus alignment padding), remaining space `_stack_bottom - _end`, and the reserved stack from `_stack_bottom` to `_stack_top`. Synthesis and place-and-route estimated Fmax values are implementation estimates. Any CoreMark score reported here uses the **48 MHz operating clock**; estimated Fmax is not the operating frequency.

## Hardware diagnostics

FPGA SRAM programming and UART firmware upload completed. The firmware-reported image CRC32 `206d3005` and size 28360 bytes match the host-verified firmware binary. The runtime SRAM probe passed. Raw UART evidence: [verified raw UART capture](<../docs/performance/20261001T012211.920014Z-onchip-performance-performance-3.uart.bin>) (SHA256 `78b18e6e5f4bdb7405535f291e45643ed6ba2777d277030525c94a5b3177829f`, verified; 4716 bytes).

```text
BENCHMARK_IMAGE_CRC32 build_id=b2c11c96db08c8ff value=206d3005 size=28360
BENCHMARK_RAM_CHECK status=passed words=64 bytes=256 alignment=32 phases=word,halfword_u16_s16,byte_u8_s8,tight_sh_lh_rmw,tight_sb_lb_rmw,code_conflict,cache_flushed errors=0 code_conflict_address=40000920
BENCHMARK_IMAGE_RECHECK phase=calibration cached=206d3005 flushed=206d3005 expected=206d3005 size=28360 cached_size=28360 flushed_size=28360
BENCHMARK_IMAGE_RECHECK phase=scored cached=206d3005 flushed=206d3005 expected=206d3005 size=28360 cached_size=28360 flushed_size=28360
```

## CPU candidate and memory-mode comparison

On-chip and DDR3 CoreMark results are revalidated separately. Legacy sessions without a memory field are treated as on-chip.

| CPU profile | On-chip CoreMark mean | On-chip LUT / BSRAM | DDR3 CoreMark mean | DDR3 LUT / BSRAM | DDR3 status |
|---|---:|---:|---:|---:|---|
| `minimal` | 16.588341 | 2247 / 38 | — | 5412 / 46 | not_measured |
| `lite` | 50.427512 | 3127 / 40 | — | 6455 / 46 | not_measured |
| `standard` | 109.127482 | 3452 / 46 | — | 7974 / 46 | not_measured |
| `performance` | 119.323916 | 3656 / 46 | — | 9128 / 46 | not_measured |

### Standard-derived CPU candidates

| Prediction candidate | Build/synthesis status | LUT / BSRAM | Worst setup slack | Board measurement | Mean CoreMark |
|---|---|---:|---:|---|---:|
| `dynamic` | ready_for_board_measurement | 3540 / 46 | 5.245 ns | passed | 111.487554 |
| `dynamic_target` | ready_for_board_measurement | 3656 / 46 | 5.325 ns | passed | 119.323913 |

The measured winner `dynamic_target` is registered as the public `performance` profile. At 48 MHz it averaged 119.323913 CoreMark versus a fresh `standard` mean of 109.127482, a 10.196432 CoreMark (9.34%) increase. The selection and evaluation identity are recorded in [cpu-profile-selection.json](../cpu-profile-selection.json).

### DDR3 training and integrity

DDR3 acceptance status: **failed**. Training logs, full-range memory coverage, cached/uncached visibility, and sustained stress evidence are summarized in [the DDR3 report](ddr3/report.md).

## Validation and scored runs

Passed for seeds `0x3415, 0x3415, 0x66`; seed CRC `0x18f2` and list/matrix/state CRCs `0xe3c1`, `0x0747`, `0x8d84`. [UART log](<../docs/performance/20261001T012211.920014Z-onchip-performance-validation.uart.log>)

| Repetition | Iterations | Elapsed ticks | Seconds | CoreMark | CoreMark/MHz | Validation | UART evidence |
|---|---:|---:|---:|---:|---:|---|---|
| Validation seeds | — | — | — | — | — | passed | [UART log](<../docs/performance/20261001T012211.920014Z-onchip-performance-validation.uart.log>) |
| Performance 1 | 2387 | 960209858 | 20.004372 | 119.323916 | 2.485914907 | passed | [UART log](<../docs/performance/20261001T012211.920014Z-onchip-performance-performance-1.uart.log>) |
| Performance 2 | 2387 | 960209858 | 20.004372 | 119.323916 | 2.485914907 | passed | [UART log](<../docs/performance/20261001T012211.920014Z-onchip-performance-performance-2.uart.log>) |
| Performance 3 | 2387 | 960209858 | 20.004372 | 119.323916 | 2.485914907 | passed | [UART log](<../docs/performance/20261001T012211.920014Z-onchip-performance-performance-3.uart.log>) |

Mean CoreMark **119.323916**, minimum **119.323916**, maximum **119.323916**, and spread **0.000000**. Mean CoreMark/MHz **2.485914907**, range **2.485914907–2.485914907**, spread **0.000000000**.

Raw elapsed timer counters are preserved as 64-bit values in `results.json`; the UART port emits the high and low 32-bit words separately. Scores are recalculated on the host as `iterations × clock_hz / elapsed_ticks` and `iterations × 1,000,000 / elapsed_ticks`. The compact bare-metal build disables CoreMark's optional floating-point text; upstream integer iterations/second and whole-second duration are cross-checked, while report scores use full-resolution timer ticks. Failed or incomplete captures do not enter aggregates.

## Evidence and reproduction

- [Machine-readable results and artifact hashes](performance/results.json)
- [Measured CPU profile selection](../cpu-profile-selection.json), [candidate P&R evidence](performance/cpu-evaluation/candidate-build.json), and [candidate board sessions](performance/cpu-evaluation/evaluations.json)
- [performance validation 0 UART log](<../docs/performance/20261001T012211.920014Z-onchip-performance-validation.uart.log>), [performance validation 0 raw UART bytes](<../docs/performance/20261001T012211.920014Z-onchip-performance-validation.uart.bin>), [performance validation 0 transmitted UART bytes](<../docs/performance/20261001T012211.920014Z-onchip-performance-validation.uart.tx.bin>), [performance performance 1 UART log](<../docs/performance/20261001T012211.920014Z-onchip-performance-performance-1.uart.log>), [performance performance 1 raw UART bytes](<../docs/performance/20261001T012211.920014Z-onchip-performance-performance-1.uart.bin>), [performance performance 1 transmitted UART bytes](<../docs/performance/20261001T012211.920014Z-onchip-performance-performance-1.uart.tx.bin>), [performance performance 2 UART log](<../docs/performance/20261001T012211.920014Z-onchip-performance-performance-2.uart.log>), [performance performance 2 raw UART bytes](<../docs/performance/20261001T012211.920014Z-onchip-performance-performance-2.uart.bin>), [performance performance 2 transmitted UART bytes](<../docs/performance/20261001T012211.920014Z-onchip-performance-performance-2.uart.tx.bin>), [performance performance 3 UART log](<../docs/performance/20261001T012211.920014Z-onchip-performance-performance-3.uart.log>), [performance performance 3 raw UART bytes](<../docs/performance/20261001T012211.920014Z-onchip-performance-performance-3.uart.bin>), [performance performance 3 transmitted UART bytes](<../docs/performance/20261001T012211.920014Z-onchip-performance-performance-3.uart.tx.bin>)
- Gowin reports: [minimal P&R resources](<../build/minimal/gateware/impl/pnr/project.rpt.txt>), [minimal timing report](<../build/minimal/gateware/impl/pnr/project.tr>), [minimal synthesis resources](<../build/minimal/gateware/impl/gwsynthesis/project_syn_resource.html>), [lite P&R resources](<../build/lite/gateware/impl/pnr/project.rpt.txt>), [lite timing report](<../build/lite/gateware/impl/pnr/project.tr>), [lite synthesis resources](<../build/lite/gateware/impl/gwsynthesis/project_syn_resource.html>), [standard P&R resources](<../build/standard/gateware/impl/pnr/project.rpt.txt>), [standard timing report](<../build/standard/gateware/impl/pnr/project.tr>), [standard synthesis resources](<../build/standard/gateware/impl/gwsynthesis/project_syn_resource.html>), [performance P&R resources](<../build/performance/gateware/impl/pnr/project.rpt.txt>), [performance timing report](<../build/performance/gateware/impl/pnr/project.tr>), [performance synthesis resources](<../build/performance/gateware/impl/gwsynthesis/project_syn_resource.html>)
- Build/session artifacts: [build/benchmarks/20261001T011926Z-onchip-build](<../build/benchmarks/20261001T011926Z-onchip-build>)
- Pinned dependency and preserved license: [dependencies.lock.json](../dependencies.lock.json), [CoreMark license](../firmware/benchmark/LICENSE.md).

Rebuild all profiles and both firmware variants:

```sh
make doctor
make benchmark-build
```

Run validation plus three scored repetitions on one selected profile:

```sh
make benchmark-run PROFILE=performance PORT=/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
make benchmark-report
```

## Interpretation and limits

The `performance` profile completed validation and three accepted CoreMark repetitions at 48 MHz (mean 119.323916 CoreMark, 2.485914907 CoreMark/MHz). This covers CoreMark with this placement: Code and read-only data run from 32 KiB on-chip main RAM; CoreMark data/BSS and the reserved stack stay in 8 KiB SRAM. Place-and-route estimated Fmax is a timing estimate and differs from the configured 48 MHz operating clock. Profiles marked **not measured** have no inferred scores, and this report does not establish a ranking for them.
