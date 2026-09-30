# VexRiscv CoreMark performance

**Status: complete.**

## Measurement identity

- Board: Sipeed Tang Primer 20K with standard Dock; device `GW2A-LV18PG256C8/I7`.
- Measurement session: 2026-09-30T12:08:13.442358+00:00; selected profile: `standard`.
- SRAM programming status: **completed**; no flash programming is used.
- Device discovery at session start: 3 `/dev/serial/by-id/` paths and 7 USB device nodes were visible.
- Configured SoC operating clock: **48,000,000 Hz**; UART selected: `/dev/ttyUSB1`; requested device: `/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0` at 115200 baud.
- Repository revision: `58a829f6dae8aa488e33cfcb7de1d4d7d567637e`; dirty at capture: `True`; source fingerprint: `7a77d19631667ed71eaffe0330d1d3d11c7d596d736a1b58fe0487f195950419`.
- CoreMark upstream commit: `1f483d5b8316753a742cbf5590caf5bd0a4e4777`. coremark.md5 reports coremark.h as failed although the checkout is clean at the pinned commit; all five protected algorithm C sources pass the upstream MD5 check.
- Tool versions: CPython 3.12.14; RISC-V GCC `exit 0: riscv-none-elf-gcc (xPack GNU RISC-V Embedded GCC x86_64) 15.2.0`; Gowin `V1.9.12.04`; openFPGALoader `openFPGALoader v0.13.1`.
- Compiler flags for the upstream algorithm sources:
  ```sh
-std=gnu99 -O2 -march=rv32i2p0_m -mabi=ilp32 -D__vexriscv__ -g3 -no-pie -fomit-frame-pointer -Wall -fno-builtin -fno-stack-protector -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -ffunction-sections -fdata-sections -fno-lto -DITERATIONS=1000 -DTOTAL_DATA_SIZE=2000 -DMEM_METHOD=MEM_STATIC -DMULTITHREAD=1 -DPERFORMANCE_RUN=1 -I/home/user/git/fpga/sipeedtangprimer20k-riscv/firmware/benchmark -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/coremark -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/standard/benchmark -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/standard/software/include -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software/include -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software/libbase -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/cores/cpu/vexriscv -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/standard/software/libc -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/pythondata-software-picolibc/pythondata_software_picolibc/data/libc/include -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/standard/benchmark/performance
```
- Memory placement: code and read-only data in 32 KiB main RAM at `0x40000000`; static benchmark data and BSS in 8 KiB on-chip SRAM at `0x10000000`; a 2,048-byte stack is reserved. The 2,000-byte CoreMark data area is statically allocated.
- Generated cache configuration: `{'instruction_cache': 'enabled', 'data_cache': 'enabled', 'configuration_source': 'build/standard/software/include/generated/soc.h'}`. Build metadata verifies the pinned LiteX `libbase/uart.c` polling backend (`-DUART_POLLING`), which avoids an interrupt-driven TX queue while benchmark interrupts are disabled. Startup, CRC, and timer output follows the timed CoreMark workload.
- Trial startup and recovery: Each trial opens the selected UART before SRAM reprogramming and records received and transmitted bytes. The runner only attempts LiteX serialboot recovery after seeing the BIOS console before any benchmark start marker. Each trial reprograms the design into FPGA SRAM and uploads fresh firmware through the LiteX BIOS serial loader; reconfiguration resets the design, and no reset is issued during a run. Each scored trial first runs a separate 1000-iteration calibration workload and derives the scored iteration count with `ceil(calibration_iterations * target_seconds * clock_hz / calibration_ticks)` for a 20-second target; calibration time is excluded from the score. The scored pass invokes upstream CoreMark again and reinitializes its static algorithm data while retaining the same firmware, profile, and memory placement. Before every upstream invocation, `portable_init` fences and flushes the CPU data and instruction caches outside the timed workload, so the calibration pass does not warm the scored pass. Timer and CRC output follows the scored workload.
- Runtime SRAM and seed preflight: Before timed CoreMark work, firmware records the initial linked-image CRC32 and size, checks a 256-byte, 32-byte-aligned volatile BSS array of 64 union words probe (two 32-bit write/read patterns cached and after cache maintenance; low/high halfword writes checking neighboring-half preservation and unsigned/signed reads; four byte-lane writes checking neighboring-byte preservation and unsigned/signed reads; halfword patterns include 0x0000, 0x7fff, 0x8000, 0xffff; byte patterns include 0x00, 0x7f, 0x80, 0xff; tight volatile back-to-back SH/LH and SB/LB sequences with same-location load-modify-store checks and neighbor preservation; alternating volatile SRAM stores and read-only linked-image loads at matching low-12 address bits (a D-cache index conflict when that cache is enabled), followed by SRAM/code checks) and validates volatile inputs `seed1_volatile, seed2_volatile, seed3_volatile, seed4_volatile` against the selected mode and iteration count. RAM and seed checks must pass before an upstream invocation. After each calibration and scored timed interval, firmware computes the linked-image CRC32 through cached reads, flushes the caches, then checks the CRC32 and size again against the initial image values. These integrity checks are outside timing; a mismatch halts the run. benchmark_main.c is compiled with -Os to fit untimed diagnostics in 32-KiB main RAM; CoreMark sources retain the profile-wide -O2 flag.

## Build and profile comparison

| Profile | Build | ISA / ABI | LUT / ALU | Registers | BSRAM | Timing constraint | Worst slack | Estimated Fmax | Perf / validation image | Maximum SRAM data+BSS+padding / stack / free | Largest static frame | Hardware benchmark |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `minimal` | passed | `rv32i2p0 / ilp32` | 2247 / 300 | 1382 / 16173 | 38 / 46 | 48 MHz | 5.752 ns | 66.307 MHz | 29928 / 29952 B | 2600+24=2624 / 2048 / 3520 B | 1184 B | not measured |
| `lite` | passed | `rv32i2p0_m / ilp32` | 3127 / 443 | 1639 / 16173 | 40 / 46 | 48 MHz | 2.920 ns | 55.823 MHz | 28360 / 28392 B | 2600+24=2624 / 2048 / 3520 B | 1152 B | not measured |
| `standard` | passed | `rv32i2p0_m / ilp32` | 3452 / 479 | 1889 / 16173 | 46 / 46 | 48 MHz | 6.471 ns | 69.628 MHz | 28376 / 28400 B | 2600+24=2624 / 2048 / 3520 B | 1152 B | measured (3 valid repetitions) |

GCC's largest per-function static frame is shown as a stack sizing check, not a runtime high-water mark; firmware reserves 2,048 stack bytes. The SRAM table reports the larger of the validation and performance image extents: linked `_end - _fdata` (data+BSS plus alignment padding), remaining space `_stack_bottom - _end`, and the reserved stack from `_stack_bottom` to `_stack_top`. Synthesis and place-and-route estimated Fmax values are implementation estimates. Any CoreMark score reported here uses the **48 MHz operating clock**; estimated Fmax is not the operating frequency.

## Hardware diagnostics

FPGA SRAM programming and UART firmware upload completed. The firmware-reported image CRC32 `0091a079` and size 28376 bytes match the host-verified firmware binary. The runtime SRAM probe passed. Earlier hash-verified CoreMark captures failed list and matrix and state CRC validation; those failed captures remain in `results.json` and are excluded from score aggregates. SRAM probe and CoreMark CRC failures captured before the read-first RAM change were traced to Gowin inferring LiteX's write-first byte-enable RAMs as write-through `SP` blocks with `WRE` tied high: on the GW2A-18C the first bus read of a word after a byte-enabled write returned wrong data on the written byte lanes, and the data cache kept that word. RTL and post-synthesis simulation of the same design read back correctly. Raw UART evidence: [verified raw UART capture](<../docs/performance/20260930T120813.442169Z-standard-performance-3.uart.bin>) (SHA256 `b2a168c70290a7892a6bd96f1b4e12be672f661402ed8e82e99324a82a266bf1`, verified; 4705 bytes).

```text
BENCHMARK_IMAGE_CRC32 build_id=7a77d19631667ed7 value=0091a079 size=28376
BENCHMARK_RAM_CHECK status=passed words=64 bytes=256 alignment=32 phases=word,halfword_u16_s16,byte_u8_s8,tight_sh_lh_rmw,tight_sb_lb_rmw,code_conflict,cache_flushed errors=0 code_conflict_address=40000920
BENCHMARK_IMAGE_RECHECK phase=calibration cached=0091a079 flushed=0091a079 expected=0091a079 size=28376 cached_size=28376 flushed_size=28376
BENCHMARK_IMAGE_RECHECK phase=scored cached=0091a079 flushed=0091a079 expected=0091a079 size=28376 cached_size=28376 flushed_size=28376
```

## Validation and scored runs

Passed for seeds `0x3415, 0x3415, 0x66`; seed CRC `0x18f2` and list/matrix/state CRCs `0xe3c1`, `0x0747`, `0x8d84`. [UART log](<../docs/performance/20260930T120813.442169Z-standard-validation.uart.log>)

| Repetition | Iterations | Elapsed ticks | Seconds | CoreMark | CoreMark/MHz | Validation | UART evidence |
|---|---:|---:|---:|---:|---:|---|---|
| Validation seeds | — | — | — | — | — | passed | [UART log](<../docs/performance/20260930T120813.442169Z-standard-validation.uart.log>) |
| Performance 1 | 2183 | 960198095 | 20.004127 | 109.127482 | 2.273489201 | passed | [UART log](<../docs/performance/20260930T120813.442169Z-standard-performance-1.uart.log>) |
| Performance 2 | 2183 | 960198095 | 20.004127 | 109.127482 | 2.273489201 | passed | [UART log](<../docs/performance/20260930T120813.442169Z-standard-performance-2.uart.log>) |
| Performance 3 | 2183 | 960198095 | 20.004127 | 109.127482 | 2.273489201 | passed | [UART log](<../docs/performance/20260930T120813.442169Z-standard-performance-3.uart.log>) |

Mean CoreMark **109.127482**, minimum **109.127482**, maximum **109.127482**, and spread **0.000000**. Mean CoreMark/MHz **2.273489201**, range **2.273489201–2.273489201**, spread **0.000000000**.

Raw elapsed timer counters are preserved as 64-bit values in `results.json`; the UART port emits the high and low 32-bit words separately. Scores are recalculated on the host as `iterations × clock_hz / elapsed_ticks` and `iterations × 1,000,000 / elapsed_ticks`. The compact bare-metal build disables CoreMark's optional floating-point text; upstream integer iterations/second and whole-second duration are cross-checked, while report scores use full-resolution timer ticks. Failed or incomplete captures do not enter aggregates.

## Evidence and reproduction

- [Machine-readable results and artifact hashes](performance/results.json)
- [validation 0 UART log](<../docs/performance/20260930T120813.442169Z-standard-validation.uart.log>), [validation 0 raw UART bytes](<../docs/performance/20260930T120813.442169Z-standard-validation.uart.bin>), [validation 0 transmitted UART bytes](<../docs/performance/20260930T120813.442169Z-standard-validation.uart.tx.bin>), [performance 1 UART log](<../docs/performance/20260930T120813.442169Z-standard-performance-1.uart.log>), [performance 1 raw UART bytes](<../docs/performance/20260930T120813.442169Z-standard-performance-1.uart.bin>), [performance 1 transmitted UART bytes](<../docs/performance/20260930T120813.442169Z-standard-performance-1.uart.tx.bin>), [performance 2 UART log](<../docs/performance/20260930T120813.442169Z-standard-performance-2.uart.log>), [performance 2 raw UART bytes](<../docs/performance/20260930T120813.442169Z-standard-performance-2.uart.bin>), [performance 2 transmitted UART bytes](<../docs/performance/20260930T120813.442169Z-standard-performance-2.uart.tx.bin>), [performance 3 UART log](<../docs/performance/20260930T120813.442169Z-standard-performance-3.uart.log>), [performance 3 raw UART bytes](<../docs/performance/20260930T120813.442169Z-standard-performance-3.uart.bin>), [performance 3 transmitted UART bytes](<../docs/performance/20260930T120813.442169Z-standard-performance-3.uart.tx.bin>)
- Gowin reports: [minimal P&R resources](<../build/minimal/gateware/impl/pnr/project.rpt.txt>), [minimal timing report](<../build/minimal/gateware/impl/pnr/project.tr>), [minimal synthesis resources](<../build/minimal/gateware/impl/gwsynthesis/project_syn_resource.html>), [lite P&R resources](<../build/lite/gateware/impl/pnr/project.rpt.txt>), [lite timing report](<../build/lite/gateware/impl/pnr/project.tr>), [lite synthesis resources](<../build/lite/gateware/impl/gwsynthesis/project_syn_resource.html>), [standard P&R resources](<../build/standard/gateware/impl/pnr/project.rpt.txt>), [standard timing report](<../build/standard/gateware/impl/pnr/project.tr>), [standard synthesis resources](<../build/standard/gateware/impl/gwsynthesis/project_syn_resource.html>)
- Build/session artifacts: [build/benchmarks/20260930T120624Z-build](<../build/benchmarks/20260930T120624Z-build>)
- Pinned dependency and preserved license: [dependencies.lock.json](../dependencies.lock.json), [CoreMark license](../firmware/benchmark/LICENSE.md).

Rebuild all profiles and both firmware variants:

```sh
make doctor
make benchmark-build
```

Run validation plus three scored repetitions on one selected profile:

```sh
make benchmark-run PROFILE=standard PORT=/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
make benchmark-report
```

## Interpretation and limits

The `standard` profile completed validation and three accepted CoreMark repetitions at 48 MHz (mean 109.127482 CoreMark, 2.273489201 CoreMark/MHz). This covers CoreMark in the current on-chip-memory SoC; it does not measure HDMI, Ethernet, or external-memory performance. Place-and-route estimated Fmax is a timing estimate and differs from the configured 48 MHz operating clock. Profiles marked **not measured** have no inferred scores, and this report does not establish a ranking for them.
