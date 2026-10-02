# DDR3 training and integrity report

**Latest batch status: failed.**

The receive patch and current integrity investigation are recorded in [DDR3 training handoff](training-handoff.md), with the original experiments retained in [DDR3 failure diagnosis](diagnosis.md). Diagnostic experiments do not count as acceptance passes.

Batch `20261001T201828.617667Z-ddr3` records its original configured geometry; older 256 MiB captures cannot qualify the fitted 128 MiB part. Current builds use 128 MiB at a 48 MHz system clock and 96 MHz DDR CK. Training success is based on captured BIOS status and all lane bitslip/delay-window records. Each counted training pass required a fresh SRAM reconfiguration and an uncached diagnostic smoke test. The full test executes from the 16 KiB diagnostic RAM. DDR3 CoreMark placement, when run, keeps code/read-only data in DDR and algorithm data/BSS/stack in SRAM.

## Per-profile evidence

### `linux`

- Session status: **failed**; training/smoke runs passed 0/10.
- Recorded DDR configuration: 268435456 bytes; 48 MHz system, 96 MHz DDR CK; 8 KiB L2; 2 trained read lanes per configuration (revalidated from generated PHY header).
- Stress: requested 1800 s; measured not measured s. Uncached alias bandwidth: not measured B/s read and not measured B/s write, not measured-byte transfers, not measured Hz, not measured timer ticks.
- Resources and timing: LUT 8860, ALU 1066, registers 4650, BSRAM 46; operating clock 48.0 MHz, estimated Fmax 48.321 MHz, worst setup slack 0.138 ns.
- Evidence: [training 1 UART](evidence/20261001T201828.617667Z-ddr3-linux/training-01.uart.bin)
- Revalidation errors: session geometry does not match the fitted 128 MiB part, recorded 1 training trials; requested 10, one or more fresh SRAM reconfiguration/training/smoke trials failed revalidation, full destructive memory and sustained stress evidence did not pass revalidation

## Write-disturbance investigation

The [retained investigation](write-disturbance.md) captured correct digital write inputs for the original BIOS error and reproduced bit-20 and bit-31 corruption at untouched victims after writes to another row. Conservative controller row timing and ODT-low did not resolve those failures. These historical probes used the former 256 MiB configuration and remain diagnostic-only; see the geometry correction below for current hardware results.

An independent raw-UART and artifact audit verified 71 snapshots and 136 frames. The [machine-readable investigation](diagnosis/20261002-integrity-investigation.json) links the exact images, captures, attempted fixes, limitations and post-investigation board recovery. DDR integrity remains failed; pin-level observations and comparison on another board are the next hardware discriminators.

## Fitted-part geometry correction

The fitted H5TQ1G63EFR is 128 MiB (13 row bits). Current builds and acceptance bounds have been corrected. The fresh 128 MiB design still fails BIOS Memtest and reproduces the bit-20 and bit-31 neighboring-row write errors. The bounded address probe is diagnostic evidence only. See [the correction and retained hardware results](hynix-geometry-correction.md). The board was recovered using a dedicated SRAM idle image that holds DDR in reset; the earlier combined JTAG detect/reset command does not prove reset occurred.


## Standalone diagnostic probes

These console probes were captured outside the acceptance runner and do not count as training or memory-test passes. They are retained to show the startup failure and subsequent read-only CSR observations.

- `diagnostic_only_not_acceptance_trial` for `minimal`; 1371 UART bytes; capture hash validation: **passed**; [raw UART capture](evidence/20261001T002530.237189Z-startup-probe-minimal/startup.uart.bin). Manifest: `docs/ddr3/evidence/20261001T002530.237189Z-startup-probe-minimal/startup-probe.json`.

```text
  best: m0, b00 SDRAM_READ_LEVELING_LANE module=0 dq=0 bitslip=0 status=failed window_start=-1 window_length=0
SDRAM_TRAINING_RESULT status=failed phase=leveling
Memory initialization failed
litex> 
```
- `diagnostic_only_read_only_csr_probe` for `minimal`; 408 UART bytes; capture hash validation: **passed**; [raw UART capture](evidence/20261001T003147.566569Z-bios-csr-probe/bios-csr-probe.uart.bin). Manifest: `docs/ddr3/evidence/20261001T003147.566569Z-bios-csr-probe/bios-csr-probe.json`.

```text
mem_read 0xf000101c 4
Memory dump:
0xf000101c  00 00 00 00                                      ....            
litex> mem_read 0xf0001000 4
Memory dump:
0xf0001000  00 00 00 00                                      ....            
litex> mem_read 0xf0002800 4
Memory dump:
0xf0002800  01 00 00 00                                      ....            
litex> 
```
- `diagnostic_only_not_acceptance_trial` for `minimal`; 142 UART bytes; capture hash validation: **passed**; [raw UART capture](evidence/20261001T003712.639210Z-latency-diagnostic/latency-diagnostic.uart.bin). Manifest: `docs/ddr3/evidence/20261001T003712.639210Z-latency-diagnostic/latency-diagnostic.json`. Temporary CL8/CWL7 mode-register settings were tried for diagnosis; acceptance remains CL6/CWL6 and this capture does not qualify.

```text
sdram_mr_write 0 2624
Writing 0x0a40 to MR0
litex> 
```
- `diagnostic_only_not_acceptance_trial` for `minimal`; 710 UART bytes; capture hash validation: **passed**; [raw UART capture](evidence/20261001T003751.798780Z-latency-calibration/latency-calibration.uart.bin). Manifest: `docs/ddr3/evidence/20261001T003751.798780Z-latency-calibration/latency-calibration.json`. Temporary CL8/CWL7 mode-register settings were tried for diagnosis; acceptance remains CL6/CWL6 and this capture does not qualify.

```text
sdram_mr_write 1 2054
Writing 0x0806 to MR1
litex> sdram_mr_write 2 528
Writing 0x0210 to MR2
litex> sdram_cal
  best: m0, b02 SDRAM_READ_LEVELING_LANE module=0 dq=0 bitslip=2 status=failed window_start=-1 window_length=0
litex> 
```
- `diagnostic_only_write_disturbance_probe` for `minimal`; 17843 UART bytes; capture hash validation: **passed**; [raw UART capture](diagnosis/20261002T013805.615625Z-dll-off-write-trace/trace.uart.bin). Manifest: `docs/ddr3/diagnosis/20261002T013805.615625Z-dll-off-write-trace/manifest.json`.
- `diagnostic_only_write_disturbance_probe` for `minimal`; 24857 UART bytes; capture hash validation: **passed**; [raw UART capture](diagnosis/20261002T014157.751746Z-dll-off-write-trace/trace.uart.bin). Manifest: `docs/ddr3/diagnosis/20261002T014157.751746Z-dll-off-write-trace/manifest.json`.
- `diagnostic_only_write_disturbance_probe` for `minimal`; 38384 UART bytes; capture hash validation: **passed**; [raw UART capture](diagnosis/20261002T014610.163681Z-dll-off-write-trace/trace.uart.bin). Manifest: `docs/ddr3/diagnosis/20261002T014610.163681Z-dll-off-write-trace/manifest.json`.
- `diagnostic_only_write_disturbance_probe` for `minimal`; 26603 UART bytes; capture hash validation: **passed**; [raw UART capture](diagnosis/20261002T014855.731212Z-dll-off-write-trace/trace.uart.bin). Manifest: `docs/ddr3/diagnosis/20261002T014855.731212Z-dll-off-write-trace/manifest.json`.
- `diagnostic_only_write_disturbance_probe` for `minimal`; 39947 UART bytes; capture hash validation: **passed**; [raw UART capture](diagnosis/20261002T015148.226324Z-dll-off-write-trace/trace.uart.bin). Manifest: `docs/ddr3/diagnosis/20261002T015148.226324Z-dll-off-write-trace/manifest.json`.
- `diagnostic_only_write_disturbance_probe` for `minimal`; 39947 UART bytes; capture hash validation: **passed**; [raw UART capture](diagnosis/20261002T015224.879824Z-dll-off-row-timing/trace.uart.bin). Manifest: `docs/ddr3/diagnosis/20261002T015224.879824Z-dll-off-row-timing/manifest.json`.
- `diagnostic_only_write_disturbance_probe` for `minimal`; 39947 UART bytes; capture hash validation: **passed**; [raw UART capture](diagnosis/20261002T015614.337611Z-dll-off-odt-low/trace.uart.bin). Manifest: `docs/ddr3/diagnosis/20261002T015614.337611Z-dll-off-odt-low/manifest.json`.


## Acceptance criteria

Thorough status requires at least 10 successful reconfiguration/training/smoke runs and 1800 seconds of measured stress per profile, full 128 MiB coverage, passing uncached bypass tests, cached visibility checks that evict both CPU and LiteDRAM L2 caches, zero errors, and complete UART captures. Shorter runs are marked partial. These records describe SRAM reconfigurations, not power-cycle or cold-boot tests.

Machine-readable results and capture identities: [results.json](results.json).
