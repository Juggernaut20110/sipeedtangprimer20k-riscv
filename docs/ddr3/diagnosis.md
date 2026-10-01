# DDR3 hardware acceptance failure diagnosis

## Finding

The acceptance failure is real and happens in BIOS read leveling, before the diagnostic application is uploaded. The attached Dock communicates over JTAG/UART and both FPGA PLL and DLL report locked. The required 48 MHz system / 96 MHz DDR CK, CL6/CWL6, DRAM DLL-off configuration produces an empty read window.

A controlled experiment on the **same FPGA bitstream** isolates the failure to DRAM DLL-off operation:

1. Fresh SRAM load: DLL-off read leveling fails, with zero passing taps.
2. At the confirmed ROM BIOS prompt, write MR1=0x0002 and reset the DRAM DLL with MR0=0x0320. Both byte lanes then train at bitslip 2, with 55-tap windows.
3. Write MR1=0x0003 to disable the DRAM DLL again. Read leveling returns to an empty window.

CL6/CWL6, MR2=0x0008, disabled nominal/dynamic ODT, the FPGA DLL, CPU, PHY logic and placement remain unchanged in this comparison. The MR0 value repeats the value used at boot. No frequency change is made. This is a diagnostic mode probe, not a qualified operating configuration or a full JEDEC frequency-transition sequence.

[Controlled UART capture](diagnosis/20261001T022123.701824Z-current-status-mr1-toggle/uart.log), [raw capture](diagnosis/20261001T022123.701824Z-current-status-mr1-toggle/uart.bin), [transmitted commands](diagnosis/20261001T022123.701824Z-current-status-mr1-toggle/uart.tx.bin), [manifest](diagnosis/20261001T022123.701824Z-current-status-mr1-toggle/manifest.json).

This identifies a DLL-off capture/configuration compatibility problem; it does not establish a defective DDR chip or board. The exact strobe timing still needs a qualified fix and validation.

## Hardware experiments

All images use the minimal CPU and retain the 48/96 MHz clocks. Each loaded image passed the project's setup, hold, recovery and removal checks. Programming was SRAM-only. Status commands and recalibration were issued only at a confirmed BIOS prompt, with no application executing from DDR.

| Experiment | Read leveling | BIOS 2 MiB memory test | Evidence |
| --- | --- | --- | --- |
| Current PHY, DLL-off CL6/CWL6 | Failed; zero window | Not reached | [UART](diagnosis/20261001T021529.730720Z-current-status/uart.log) |
| Restore eight GW2 RCLKSEL positions | Failed; zero window | Not reached | [UART](diagnosis/20261001T021600.689478Z-legacy-rclksel/uart.log) |
| Restore sys reset on PHY primitives | Failed; zero window | Not reached | [UART](diagnosis/20261001T021735.112151Z-legacy-reset/uart.log) |
| Restore both legacy behaviors | Failed; zero window | Not reached | [UART](diagnosis/20261001T022104.806077Z-legacy-both/uart.log) |
| Earlier DQS READ gate, DLL-off CL6/CWL6 | Failed; zero window | Not reached | [UART](diagnosis/20261001T022850.526645Z-read-gate-early/uart.log) |
| DLL-on, matching PHY/DRAM CL8/CWL7 | Both lanes pass; 56–57 taps | **Failed: 2 / 524288 data words** | [UART](diagnosis/20261001T021827.590330Z-latency-8-7/uart.log) |
| DLL-on, matching PHY/DRAM CL6/CWL6 | Both lanes pass; 54 taps | **Failed: 1 / 524288 data words** | [UART](diagnosis/20261001T021904.816554Z-dll-on-6-6/uart.log) |

The independently built DLL-on images also enable the upstream default dynamic write ODT (MR2 bit 9); the controlled MR1 experiment above keeps dynamic ODT disabled and removes that confound. DLL-on at 96 MHz is a diagnostic comparison and does not satisfy the required DLL-off acceptance configuration. A training pass alone is insufficient: both DLL-on startup memory tests still found corruption.

The baseline clock CSR reads 0x037c: FPGA DLL delay code 124, DLL lock=1, PLL lock=1, stop/reset/pause=0. Both RVALID sticky bits are set. The end-of-training burst-detection CSR reads zero; this is an end-state observation, not a complete DQS waveform or proof that no burst occurred earlier. [Status capture](diagnosis/20261001T021529.730720Z-current-status/uart.log).

## Why DLL-off needs separate read capture handling

The current upstream PHY sets CL/CWL and ODT for DLL-off mode, but keeps its DQS READ gate at the same two system-clock taps as DLL-on CL6. The generic read latency is also unchanged. [Pinned PHY source](https://github.com/enjoy-digital/litedram/blob/4c979d195aa0c60adcaf70374a52872028aaca63/litedram/phy/gw2ddrphy.py).

A Micron DDR3 component reference describes DLL-off read strobes relative to CL−1 rather than CL, with a different clock-to-strobe delay, and explicitly requires attention when aligning read data to the controller. This supports testing the read-gate position; it is not a characterization of the installed ICMAX chip. [Micron component data sheet, DLL Disable Mode, pages 123–126](https://www.alliancememory.com/wp-content/uploads/Micron_2Gb_DDR3_SDRAM_PartNo.MT41J128M16JT-107.pdf).

The upstream changes to serializer reset and read-clock selection were plausible suspects, but restoring them separately and together did not recover the required configuration. [Upstream GW2 calibration change](https://github.com/enjoy-digital/litedram/commit/311a0f758a953abb26a41c50e42a9b297d160a0a).

Extending the DQS READ gate from taps 2/3 to taps 1/2/3 also failed. That image retained DLL-off CL6/CWL6 and disabled ODT. Its two HOLD registers were placed near the DQS primitives to resolve setup paths introduced by diagnostic placement; all functional timing checks then passed (worst setup +0.035 ns, hold +0.318 ns). Lane 1's end-state burst flag became set, but lane 0 still had no valid training window. A wider/earlier gate alone is therefore insufficient. [Generated constraints and header](diagnosis/20261001T022850.526645Z-read-gate-early/manifest.json).

The next qualified fix needs to establish DLL-off DQS arrival, FIFO clock selection and data-valid alignment together, using the installed chip's timing specification and measured strobe timing. Enabling the DRAM DLL or increasing CL/CWL is not an accepted workaround: both tested DLL-on images still failed the BIOS data test, and neither meets the required operating mode.

## Acceptance parser defects corrected

- The host multiplied two DQS modules by eight DQ bits and expected 16 independently trained records. GW2DDRPHY trains **two byte lanes**, because its generated header does not define `SDRAM_DELAY_PER_DQ`. The host now follows the BIOS loop count and validates every module/DQ identity, rejecting missing, duplicate and unexpected lanes.
- A failed lane can appear after the BIOS `best:` prefix and omit delay-center fields. The parser now retains that failure record rather than reporting zero records. It still rejects failed or incomplete windows.
- The report uses the revalidated generated lane count while preserving original historical session metadata and raw captures.

These parser fixes cannot turn the captured all-zero BIOS training into a pass. The latest acceptance report remains failed. [Acceptance report](report.md).

## Reproduction

Use the pinned project environment and the verified Dock UART:

```sh
python3 scripts/ddr_diagnose.py build
python3 scripts/ddr_diagnose.py capture --port /dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
python3 scripts/ddr_diagnose.py capture --dll-mode-probe --port /dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
```

Other isolated experiments use `--experiment legacy-reset`, `legacy-rclksel`, `legacy-both`, `dll-on-6-6`, `latency-8-7`, or `read-gate-early`. Build before capture. The harness builds under `build/ddr3/diagnosis/`, checks timing before producing a loadable manifest, validates the bitstream hash, clears UART input before SRAM programming, and records raw RX/TX, programmer output and generated CSR identity. It does not change public profile builds or dependency sources and does not load acceptance firmware.

[Machine-readable experiment summary](diagnosis/summary.json) includes bitstream/capture hashes and routed timing metrics. Each session directory includes its status results and manifest.

## Validation and remaining acceptance

All 58 host tests and all eight generated SoC checks pass after the parser fixes. Full-range integrity, ten fresh training/smoke cycles per profile, 1800-second stress per profile, and DDR CoreMark remain unfulfilled. The short BIOS tests and mode probes are diagnostic evidence only.

The Dock was restored to the standard on-chip SRAM image after the final confirmed diagnostic BIOS prompt. Its fresh BIOS reports CRC `1e8be547`, 32 KiB main RAM, `Memtest OK`, and returns to `litex>`. [Restoration capture](diagnosis/20261001T023018.556065Z-restore-standard/uart.log).
