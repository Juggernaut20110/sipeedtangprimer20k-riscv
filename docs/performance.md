# VexRiscv CoreMark performance

**Status: INCOMPLETE — board measurements are unavailable or failed.**

## Measurement identity

- Board: Sipeed Tang Primer 20K with standard Dock; device `GW2A-LV18PG256C8/I7`.
- Measurement session: 2026-09-30T10:43:44.217695+00:00; selected profile: `standard`.
- SRAM programming status: **completed**; no flash programming is used.
- Device discovery at session start: 3 `/dev/serial/by-id/` paths and 7 USB device nodes were visible.
- Configured SoC operating clock: **48,000,000 Hz**; UART selected: `/dev/ttyUSB1`; requested device: `/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0` at 115200 baud.
- Repository revision: `20c21ca2d9d0e51679e93d1427bafc3bc31f2565`; dirty at capture: `True`; source fingerprint: `3670ebea898d97de64b97936a6fb413294c48787ee091b5a95002108dc8185da`.
- CoreMark upstream commit: `1f483d5b8316753a742cbf5590caf5bd0a4e4777`. coremark.md5 reports coremark.h as failed although the checkout is clean at the pinned commit; all five protected algorithm C sources pass the upstream MD5 check.
- Tool versions: CPython 3.12.14; RISC-V GCC `exit 0: riscv-none-elf-gcc (xPack GNU RISC-V Embedded GCC x86_64) 15.2.0`; Gowin `V1.9.12.04`; openFPGALoader `openFPGALoader v0.13.1`.
- Compiler flags for the upstream algorithm sources:
  ```sh
-std=gnu99 -O2 -march=rv32i2p0_m -mabi=ilp32 -D__vexriscv__ -g3 -no-pie -fomit-frame-pointer -Wall -fno-builtin -fno-stack-protector -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -ffunction-sections -fdata-sections -fno-lto -DITERATIONS=1000 -DTOTAL_DATA_SIZE=2000 -DMEM_METHOD=MEM_STATIC -DMULTITHREAD=1 -DPERFORMANCE_RUN=1 -I/home/user/git/fpga/sipeedtangprimer20k-riscv/firmware/benchmark -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/coremark -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/standard/benchmark -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/standard/software/include -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software/include -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software/libbase -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/cores/cpu/vexriscv -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/standard/software/libc -I/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/pythondata-software-picolibc/pythondata_software_picolibc/data/libc/include -I/home/user/git/fpga/sipeedtangprimer20k-riscv/build/standard/benchmark/performance
```
- Memory placement: code and read-only data in 32 KiB main RAM at `0x40000000`; static benchmark data and BSS in 8 KiB on-chip SRAM at `0x10000000`; a 2,048-byte stack is reserved. The 2,000-byte CoreMark data area is statically allocated.
- Generated cache configuration: `{'instruction_cache': 'enabled', 'data_cache': 'enabled', 'configuration_source': 'build/standard/software/include/generated/soc.h'}`. Build metadata verifies the pinned LiteX `libbase/uart.c` polling backend (`-DUART_POLLING`), which avoids an interrupt-driven TX queue while benchmark interrupts are disabled. Startup, CRC, and timer output follows the timed CoreMark workload.
- Trial startup and recovery: Captured startup recovery: in_progress after `bios_console_after_missed_initial_sfl_ack`; port reopened: True; firmware upload: LiteXTerm safe mode (64-byte frames, one outstanding); console command `"\\nserialboot\\n"`; FPGA reprogrammed during recovery: False. Raw receive and transmitted-byte logs span the initial upload and recovery on the explicit UART port. Each trial reprograms the design into FPGA SRAM and uploads fresh firmware through the LiteX BIOS serial loader; reconfiguration resets the design, and no reset is issued during a run. Each scored trial first runs a separate 1000-iteration calibration workload and derives the scored iteration count with `ceil(calibration_iterations * target_seconds * clock_hz / calibration_ticks)` for a 20-second target; calibration time is excluded from the score. The scored pass invokes upstream CoreMark again and reinitializes its static algorithm data while retaining the same firmware, profile, and memory placement. Before every upstream invocation, `portable_init` fences and flushes the CPU data and instruction caches outside the timed workload, so the calibration pass does not warm the scored pass. Timer and CRC output follows the scored workload.
- Runtime SRAM and seed preflight: Before timed CoreMark work, firmware records the initial linked-image CRC32 and size, checks a 256-byte, 32-byte-aligned volatile BSS array of 64 union words probe (two 32-bit write/read patterns cached and after cache maintenance; low/high halfword writes checking neighboring-half preservation and unsigned/signed reads; four byte-lane writes checking neighboring-byte preservation and unsigned/signed reads; halfword patterns include 0x0000, 0x7fff, 0x8000, 0xffff; byte patterns include 0x00, 0x7f, 0x80, 0xff; tight volatile back-to-back SH/LH and SB/LB sequences with same-location load-modify-store checks and neighbor preservation; alternating volatile SRAM stores and read-only linked-image loads at matching low-12 address bits (a D-cache index conflict when that cache is enabled), followed by SRAM/code checks) and validates volatile inputs `seed1_volatile, seed2_volatile, seed3_volatile, seed4_volatile` against the selected mode and iteration count. RAM and seed checks must pass before an upstream invocation. After each calibration and scored timed interval, firmware computes the linked-image CRC32 through cached reads, flushes the caches, then checks the CRC32 and size again against the initial image values. These integrity checks are outside timing; a mismatch halts the run. benchmark_main.c is compiled with -Os to fit untimed diagnostics in 32-KiB main RAM; CoreMark sources retain the profile-wide -O2 flag.

## Build and profile comparison

| Profile | Build | ISA / ABI | LUT / ALU | Registers | BSRAM | Timing constraint | Worst slack | Estimated Fmax | Perf / validation image | Maximum SRAM data+BSS+padding / stack / free | Largest static frame | Hardware benchmark |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `minimal` | passed | `rv32i2p0 / ilp32` | 2632 / 298 | 1382 / 16173 | 38 / 46 | 48 MHz | 6.548 ns | 70.002 MHz | 29928 / 29952 B | 2600+24=2624 / 2048 / 3520 B | 1184 B | not measured |
| `lite` | passed | `rv32i2p0_m / ilp32` | 3385 / 443 | 1639 / 16173 | 40 / 46 | 48 MHz | 2.806 ns | 55.471 MHz | 28360 / 28392 B | 2600+24=2624 / 2048 / 3520 B | 1152 B | not measured |
| `standard` | passed | `rv32i2p0_m / ilp32` | 3793 / 479 | 1889 / 16173 | 46 / 46 | 48 MHz | 5.032 ns | 63.286 MHz | 28376 / 28400 B | 2600+24=2624 / 2048 / 3520 B | 1152 B | failed / incomplete |

GCC's largest per-function static frame is shown as a stack sizing check, not a runtime high-water mark; firmware reserves 2,048 stack bytes. The SRAM table reports the larger of the validation and performance image extents: linked `_end - _fdata` (data+BSS plus alignment padding), remaining space `_stack_bottom - _end`, and the reserved stack from `_stack_bottom` to `_stack_top`. Synthesis and place-and-route estimated Fmax values are implementation estimates. Any CoreMark score reported here uses the **48 MHz operating clock**; estimated Fmax is not the operating frequency.

## Hardware diagnostics

FPGA SRAM programming and UART firmware upload completed. The firmware-reported image CRC32 `779664db` and size 28400 bytes match the host-verified firmware binary. The runtime SRAM probe then failed at `tight_sram_conflict_lh` at address `0x10000940`, expected `0x00008a01` and read `0x00000000` (256 errors, fail mask `0x00080000`). CoreMark did not start, the exact hardware/cache root cause remains unresolved, and this validation attempt produced no benchmark score. All 3 scored performance repetitions were not run. Earlier hash-verified CoreMark captures failed list and matrix and state CRC validation; those failed captures remain in `results.json` and are excluded from score aggregates. Raw UART evidence: [verified raw UART capture](<../docs/performance/20260930T104344.217514Z-standard-validation.uart.bin>) (SHA256 `bd73c4a3c33863a835832f8ba253510538be34bd5fa8a1155f4013c5f354f477`, verified; 2156 bytes).

```text
BENCHMARK_IMAGE_CRC32 build_id=3670ebea898d97de value=779664db size=28400
BENCHMARK_RAM_CHECK status=failed phase=tight_sram_conflict_lh firstaddr=10000940 expected=00008a01 actual=00000000 errors=256 failmask=00080000 bytes=256
BENCHMARK_PORT_ERROR on-chip SRAM preflight failed
```

Next diagnostic: insert a fence and D-cache flush immediately after the conflicting SRAM store and before the matching-index linked-image read, then repeat the same probe to isolate write visibility from cache-index conflict.

## Validation and scored runs

**failed**: RuntimeError: UART reader failed while waiting for firmware startup after BIOS recovery: BENCHMARK_PORT_ERROR on-chip SRAM preflight failed

| Repetition | Iterations | Elapsed ticks | Seconds | CoreMark | CoreMark/MHz | Validation | UART evidence |
|---|---:|---:|---:|---:|---:|---|---|
| Validation seeds | — | — | — | — | — | failed | [UART log](<../docs/performance/20260930T104344.217514Z-standard-validation.uart.log>) |
| Performance 1 | — | — | — | — | — | not_run (prior validation failed or the run was interrupted) | — |
| Performance 2 | — | — | — | — | — | not_run (prior validation failed or the run was interrupted) | — |
| Performance 3 | — | — | — | — | — | not_run (prior validation failed or the run was interrupted) | — |

No aggregate is reported until validation and all three scored repetitions pass the capture checks.

Raw elapsed timer counters are preserved as 64-bit values in `results.json`; the UART port emits the high and low 32-bit words separately. Scores are recalculated on the host as `iterations × clock_hz / elapsed_ticks` and `iterations × 1,000,000 / elapsed_ticks`. The compact bare-metal build disables CoreMark's optional floating-point text; upstream integer iterations/second and whole-second duration are cross-checked, while report scores use full-resolution timer ticks. Failed or incomplete captures do not enter aggregates.

## Evidence and reproduction

- [Machine-readable results and artifact hashes](performance/results.json)
- [validation 0 UART log](<../docs/performance/20260930T104344.217514Z-standard-validation.uart.log>), [validation 0 raw UART bytes](<../docs/performance/20260930T104344.217514Z-standard-validation.uart.bin>), [validation 0 transmitted UART bytes](<../docs/performance/20260930T104344.217514Z-standard-validation.uart.tx.bin>)
- Gowin reports: [minimal P&R resources](<../build/minimal/gateware/impl/pnr/project.rpt.txt>), [minimal timing report](<../build/minimal/gateware/impl/pnr/project.tr>), [minimal synthesis resources](<../build/minimal/gateware/impl/gwsynthesis/project_syn_resource.html>), [lite P&R resources](<../build/lite/gateware/impl/pnr/project.rpt.txt>), [lite timing report](<../build/lite/gateware/impl/pnr/project.tr>), [lite synthesis resources](<../build/lite/gateware/impl/gwsynthesis/project_syn_resource.html>), [standard P&R resources](<../build/standard/gateware/impl/pnr/project.rpt.txt>), [standard timing report](<../build/standard/gateware/impl/pnr/project.tr>), [standard synthesis resources](<../build/standard/gateware/impl/gwsynthesis/project_syn_resource.html>)
- Build/session artifacts: [build/benchmarks/20260930T104254Z-build](<../build/benchmarks/20260930T104254Z-build>)
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

FPGA SRAM programming completed, but the selected profile has no accepted CoreMark result. The validation trial failed (RuntimeError: UART reader failed while waiting for firmware startup after BIOS recovery: BENCHMARK_PORT_ERROR on-chip SRAM preflight failed). Failed and incomplete sessions remain in `results.json` and do not enter score aggregates. The intended workload covers the current on-chip-memory SoC; it does not measure HDMI, Ethernet, or external-memory performance. Place-and-route estimated Fmax is separate from the configured 48 MHz operating clock. Profiles marked **not measured** have no inferred scores, and this report does not establish a ranking for them.
