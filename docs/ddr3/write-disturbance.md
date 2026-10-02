# DDR3 write-disturbance investigation

> Geometry correction, 2026-10-02 UTC: the fitted H5TQ1G63EFR has 128 MiB and 13 row bits. Earlier 256 MiB assumptions and full-capacity claims in this historical document are superseded by the [correction and hardware retest](hynix-geometry-correction.md). Errors below 128 MiB remain reproducible.

Updated 2026-10-02 UTC. **Integrity remains failed. No experimental setting was promoted to a public build.** All programming used FPGA SRAM; flash was untouched. The system and DDR clocks remained 48 MHz and 96 MHz, with DLL-off CL6/CWL6 and the existing 256 MiB geometry.

## What the recorder established

The diagnostic-only [burst recorder](../../gateware/ddr_trace.py) observes eight system-clock frames of DFI write data/masks and the actual OSER4_MEM data, mask and output-enable inputs. It tracks physical rows across both command phases and freezes on the first matching native WRITE. Three returned-data samples straddle the nominal read-valid cycle. It does not drive the controller or PHY, and does not observe external pin waveforms.

In the [first capture](diagnosis/20261002T013805.615625Z-dll-off-write-trace/trace-probe.json), the boot-time write at offset `0xb64f4` carried the correct full burst:

```text
DFI:                 ed079b34 da4f366f b4de6cd9 69fcd9b5
DFI byte mask:       0000
Serializer chunk 0:  b4de6cd9 69fcd9b5, mask 00
Serializer chunk 1:  ed079b34 da4f366f, mask 00
Nominal read:        ed079b34 da4f366f b4ce6cd9 69fcd9b5
Read-valid samples:  010 (nominal sample only)
```

The expected digital write data reached the serializer inputs, while the nominal read returned the original bit-20 error. An isolated rewrite was correct. This narrows the investigation beyond a wrong software value or DFI byte mask, but cannot establish correct pin timing or identify a faulty physical component.

The recorder freezes on the first WRITE to a native burst, including masked writes to other words in that burst. The uncached PRNG trace at `0xb64f4` therefore observes word lane 0 rather than the failing lane 1; it is not evidence of that lane's uncached write. The boot-time full-burst trace covers all four words. The all-ones target `0x169770` is lane 0 and is covered by its trace.

## Later writes disturb an untouched victim

The [write-order probe](diagnosis/20261002T014157.751746Z-dll-off-write-trace/trace-probe.json) repaired `0xc0169770` to `0xffffffff`, then wrote only offsets `0x169780..0x200000`. The victim changed to `0xffefffff`, although the recorder remained armed with no matching victim WRITE. Writes before the victim, small local writes and five seconds of idle did not reproduce the error. A later complete all-ones sweep passed after the earlier sweeps had changed neighboring contents; this is history-dependent diagnostic behavior, not an integrity pass.

The [bisection](diagnosis/20261002T014610.163681Z-dll-off-write-trace/trace-probe.json) restored the entire BIOS PRNG population before every trial, repaired and checked the victim, then wrote a disjoint all-ones range. It narrowed the reproducible disturbance to offsets `0x16cff0..0x16d4a4` (1,204 bytes). That complete range failed twice, but each half passed separately. Consequently, a single aggressor word has not been isolated; range length, command sequence or data history matters.

The [row and pattern controls](diagnosis/20261002T014855.731212Z-dll-off-write-trace/trace-probe.json) reproduce the bit-20 loss when the narrowed range receives zeros, ones, `0x55555555` or `0xaaaaaaaa` after PRNG restoration. Writing the next physical row in the same bank also fails; writing the previous row, the next row in another bank, the second next row in the same bank, or the same row beyond the victim leaves the victim correct.

## Independent failure address and attempted fixes

The [neighbor-access probe](diagnosis/20261002T015148.226324Z-dll-off-write-trace/trace-probe.json) repairs each victim before each action. It initializes the next row to `0xaaaaaaaa`, reads that row, writes ones, then writes zeros, repairing the victim between actions. All accesses use the `0xc0000000` alias that bypasses CPU caches and LiteDRAM L2, while executing BIOS from on-chip memory. They still pass through the DDR controller and PHY.

| Victim offset / physical mapping | Expected | Next-row read | Next-row ones | Next-row zeros |
|---|---|---|---|---|
| `0x169770`: row 90, bank 2, column 952, word 0 | `ffffffff` | correct | **`ffefffff`** | correct |
| `0xb64f4`: row 45, bank 4, column 632, word 1 | `b4de6cd9` | correct | correct | correct |
| `0x64011ac`: row 6400, bank 2, column 208, word 3 | `fffff7ff` | correct | correct | **`7ffff7ff`** |

For the third victim, only offsets `0x6405000..0x6405800` were written to trigger the exact bit-31 error previously found by the full-range walking-zeros test. Neither failing victim burst received a matching DFI WRITE during these neighbor accesses. Read-only neighbor accesses passed. This demonstrates write-dependent disturbance at two addresses; it does not explain the original `0xb64f4` error under every tested pattern.

Two attempted configuration fixes repeated the entire neighbor-access suite with fresh SRAM programming and fresh two-lane training. Both still failed BIOS Memtest and reproduced the same two neighbor disturbances:

| Diagnostic route | Setup / hold / recovery / removal slack (ns) | Result |
|---|---|---|
| Original trace image | +0.405 / +0.319 / +1.779 / +1.995 | integrity failed |
| [Conservative row timing](diagnosis/20261002T015224.879824Z-dll-off-row-timing/trace-probe.json) | +0.042 / +0.318 / +1.751 / +1.479 | same failures |
| [ODT held low](diagnosis/20261002T015614.337611Z-dll-off-odt-low/trace-probe.json) | +0.016 / +0.318 / +2.022 / +1.038 | same failures |

All three routes had zero violated setup endpoints and verified 48/96 MHz clocks. Conservative controller floors were tRP=4, tRCD=4, tRAS=8, tRC=12, tWR=4 and tWTR=4 system cycles, versus the baseline 2/2/3/3/2/2. Mode registers and receive settings were unchanged. ODT-low used baseline controller timing and forced the actual ODT serializer inputs low throughout initialization and operation; MR1=`0x3` and MR2=`0x8` already disable RTT_NOM and RTT_WR. The [ICMAX datasheet, DLL Disable Mode, pages 123–125](https://atta.szlcsc.com/upload/public/pdf/source/20220309/96D421F163B8527A26AFCBB2BF4A8780.pdf) motivates that experiment, although its transition figures also permit static HIGH when both termination settings are disabled. ODT-low is not a demonstrated integrity fix.

## DLL-on images fail at the same word

The earlier DLL-on comparison images ([diagnosis](diagnosis.md)) reported only error counts. Both were rebuilt with BIOS data-error address logging and no other change. Each passed routed timing with zero violated setup endpoints (CL6/CWL6: setup +0.026 ns, hold +0.319 ns; CL8/CWL7: setup +0.319 ns, hold +0.316 ns). Each was then loaded into FPGA SRAM three times.

| Image | DRAM DLL | Two-lane training | BIOS 2 MiB result, every boot |
|---|---|---|---|
| `dll-on-6-6` | on, CL6/CWL6 | 3 / 3; windows 54–55 taps | 1 error: `0x400b64f4` `0xb4ce6cd9` vs `0xb4de6cd9` |
| `latency-8-7` | on, CL8/CWL7 | 3 / 3; windows 56–57 taps | 1 error: `0x400b64f4` `0xb4ce6cd9` vs `0xb4de6cd9` |

Captures: CL6 [1](diagnosis/20261002T022928.120313Z-dll-on-6-6/uart.log), [2](diagnosis/20261002T023001.597561Z-dll-on-6-6/uart.log), [3](diagnosis/20261002T023021.918421Z-dll-on-6-6/uart.log); CL8 [1](diagnosis/20261002T022944.911572Z-latency-8-7/uart.log), [2](diagnosis/20261002T023011.755951Z-latency-8-7/uart.log), [3](diagnosis/20261002T023032.065877Z-latency-8-7/uart.log).

The DLL-off public and diagnostic images fail at the same address, with the same value and the same bit-20 loss. These images change the DRAM DLL mode, CAS latency, PHY receive path (original DLL-on gate, RCLKSEL 0, read latency CL+9), and placement; none of those changes moves the failure. That makes the receive patch, DLL-off operation and the read-capture configuration very unlikely causes. It supports a storage-level fault that the BIOS write sequence reproduces deterministically. The earlier CL8/CWL7 capture with two errors was not reproduced and its second address remains unknown. DLL-on at 96 MHz is outside the JEDEC DLL-on frequency range and remains a diagnostic comparison only.

## Reproduction and evidence audit

```sh
python3 scripts/ddr_diagnose.py build --experiment dll-off-write-trace
python3 scripts/ddr_write_trace.py --suite disturbance-search --port <verified-Dock-UART>
python3 scripts/ddr_write_trace.py --suite disturbance-controls --port <verified-Dock-UART>
python3 scripts/ddr_write_trace.py --suite neighbor-read --port <verified-Dock-UART>

python3 scripts/ddr_diagnose.py build --experiment dll-off-row-timing
python3 scripts/ddr_write_trace.py --experiment dll-off-row-timing --suite neighbor-read --port <verified-Dock-UART>
python3 scripts/ddr_diagnose.py build --experiment dll-off-odt-low
python3 scripts/ddr_write_trace.py --experiment dll-off-odt-low --suite neighbor-read --port <verified-Dock-UART>

python3 scripts/ddr_trace_audit.py docs/ddr3/diagnosis/20261002T01*-dll-off-*/
make test
```

The probe refuses programming when any required routed slack is negative, clocks differ, or the recorder source changed since the build. It stops if training fails or a BIOS prompt is lost. Failed BIOS memory tests are retained only for diagnosis and receive no acceptance credit.

Each of the seven captures retains raw boot/probe RX and TX, decoded frames, probe and recorder sources, BIOS, generated RTL, bitstream, timing report and manifest. The independent audit reconstructed all 71 snapshots and 136 frames from UART bytes and verified artifact hashes. Mutation checks reject fabricated physical readbacks, serializer fields and source identities. `make test` passed 97 host tests and all generated-design checks. The [machine-readable investigation summary](diagnosis/20261002-integrity-investigation.json) links every capture and audit.

## Remaining gate and board state

No public PHY/controller patch is justified by these unsuccessful fixes. The evidence favors stored-data disturbance during later writes, but does not yet distinguish physical DRAM behavior, power/signal integrity, or corruption of commands/data between the FPGA primitives and DRAM pins. It does not prove a defective chip, exclude every receive-path issue, or qualify DDR3.

The next useful hardware discriminator is to run these exact retained images and neighbor sequences on a second board, then compare victim addresses and bit masks. Pin-level command/address, DQS/DQ/DM and DDR supply/VREF observations during the narrowed sequence can distinguish a physical row disturbance from an incorrect external command or write waveform. A board comparison and pin-level observations were not available in this run. Successful BIOS tests, full 256 MiB integrity, ten fresh training/smoke passes and 1,800-second stress remain required before any DDR scoring.

The board was explicitly reset to safe idle after the final probe. JTAG reset/detection succeeded and pre/post-reset UART observations were empty. [Recovery evidence](diagnosis/20261002T020045.394626Z-integrity-recovery/recovery.json).
