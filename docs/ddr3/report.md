# DDR3 training and integrity report

**Latest batch status: failed.**

The hardware investigation and controlled DLL-mode experiments are recorded in [DDR3 failure diagnosis](diagnosis.md). Diagnostic experiments do not count as acceptance passes.

Batch `20261001T014933.087634Z-ddr3` uses the configured 256 MiB DDR3 geometry at a 48 MHz system clock and 96 MHz DDR CK. Training success is based on captured BIOS status and all lane bitslip/delay-window records. Each counted training pass required a fresh SRAM reconfiguration and an uncached diagnostic smoke test. The full test executes from the 16 KiB diagnostic RAM. DDR3 CoreMark placement, when run, keeps code/read-only data in DDR and algorithm data/BSS/stack in SRAM.

## Per-profile evidence

### `minimal`

- Session status: **failed**; training/smoke runs passed 0/1.
- DDR configuration: 268435456 bytes; 48 MHz system, 96 MHz DDR CK; 8 KiB L2; 2 trained read lanes per configuration (revalidated from generated PHY header).
- Stress: requested 60 s; measured not measured s. Uncached alias bandwidth: not measured B/s read and not measured B/s write, not measured-byte transfers, not measured Hz, not measured timer ticks.
- Resources and timing: LUT 5412, ALU 664, registers 3244, BSRAM 46; operating clock 48.0 MHz, estimated Fmax 48.36 MHz, worst setup slack 0.155 ns.
- Evidence: [training 1 UART](evidence/20261001T014933.087634Z-ddr3-minimal/training-01.uart.bin)
- Revalidation errors: one or more fresh SRAM reconfiguration/training/smoke trials failed revalidation, full destructive memory and sustained stress evidence did not pass revalidation

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


## Acceptance criteria

Thorough status requires at least 10 successful reconfiguration/training/smoke runs and 1800 seconds of measured stress per profile, full 256 MiB coverage, passing uncached bypass tests, cached visibility checks that evict both CPU and LiteDRAM L2 caches, zero errors, and complete UART captures. Shorter runs are marked partial. These records describe SRAM reconfigurations, not power-cycle or cold-boot tests.

Machine-readable results and capture identities: [results.json](results.json).
