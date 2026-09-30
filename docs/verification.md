# Verification record

Validation completed on September 29, 2026, using CPython 3.12.14, xPack RISC-V GCC 15.2.0, and Gowin EDA V1.9.12.04 for device GW2A-LV18PG256C8/I7.

| Profile | SoC generation | BIOS and application | Gowin synthesis and place-and-route | Physical Dock checks |
|---|---|---|---|---|
| `minimal` | Passed | Passed; 6,872-byte image, 384 bytes data/BSS, 2,048-byte stack | Passed; 48 MHz constraint met | Partial: SRAM load, BIOS, firmware upload, UART console, and command smoke test passed; user confirmed S0–S3 control LEDs0–3 and S4 resets FPGA |
| `lite` | Passed | Passed; 6,312-byte image, 384 bytes data/BSS, 2,048-byte stack | Passed; 48 MHz constraint met | Pending: profile not yet run on Dock |
| `standard` | Passed | Passed; 6,312-byte image, 384 bytes data/BSS, 2,048-byte stack | Passed; 48 MHz constraint met | Pending: profile not yet run on Dock |

The final Gowin place-and-route reports measured:

| Profile | LUTs | ALUs | Registers | BSRAM | DSP | Constrained clock | Actual Fmax | Worst setup slack | Setup violations |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `minimal` | 2,632 | 298 | 1,382 / 16,173 | 38 / 46 | Unavailable in report | 48 MHz | 70.002 MHz | 6.548 ns | 0 |
| `lite` | 3,385 | 443 | 1,639 / 16,173 | 40 / 46 | Unavailable in report | 48 MHz | 55.471 MHz | 2.806 ns | 0 |
| `standard` | 3,793 | 479 | 1,889 / 16,173 | 46 / 46 | Unavailable in report | 48 MHz | 63.286 MHz | 5.032 ns | 0 |

All three profiles fit the reported device limits and pass setup timing. The `standard` profile uses every available BSRAM block, so it leaves no BSRAM capacity for later features. Gowin's synthesis resource report displays `-` for DSP count; this record keeps DSP unavailable instead of treating it as zero. The timing report also lists no timing paths for three unused PLL output clocks (`CLKOUTP`, `CLKOUTD`, and `CLKOUTD3`); the 27 MHz input and generated 48 MHz system clocks are active constraints.

`make test` passes the host debounce/command/parser checks, including simultaneous S0/S2 inputs, bounce shorter than 20 ms, independent inputs, reset-key bit exclusion, valid and invalid LED masks, mode transitions, CRLF, overlong-line recovery, and continuous UART-character traffic. GPIO simulation verifies inactive LED reset polarity, active-low normalization for four GPIO buttons, printed LED-label bit order with the reversed platform-resource mapping, and the two-stage input synchronizer. All three generated SoCs use the four usable Dock button pins, omit the C7/S4 reset input from GPIO, retain the same memory and other GPIO layout, omit DDR3, emit profile-specific CSR definitions, preserve pin locations and mixed LVCMOS33/LVCMOS15 standards, and constrain the PLL output to 48 MHz.

`make doctor` passes the Python imports, RV32I and RV32IM compiler probes, Gowin Tcl console startup, and openFPGALoader runtime checks. The completed Gowin synthesis and place-and-route runs confirm license access. It finds three stable serial aliases and confirms their serial permissions are usable. The QinHeng USB serial adapter at `/dev/serial/by-id/usb-1a86_USB_Single_Serial_599D114570-if00` is not the FPGA UART. `make run PROFILE=minimal PORT=/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0` successfully programmed the corrected four-button FPGA SRAM image, passed the BIOS CRC and 32 KiB main-RAM test, uploaded the 6,872-byte firmware, and reached `GPIO demo ready`. On this image, `help` reports S0–S3 controls and `buttons` reports `synchronized=0x00 debounced=0x00` at idle. The earlier command smoke test also exercised `leds 0x21` and `demo`. The user confirmed S0–S3 control LEDs 0–3 as expected; pressing S4 resets the FPGA, so C7 is excluded from the four-bit GPIO CSR. Visual confirmation of every LED and the heartbeat remains pending.

The remaining hardware checks are visual confirmation of LED4 and LED5 behavior, observing the heartbeat and 20 ms button debounce, repeated runs for `lite` and `standard`, and power cycling to confirm the existing flash image returns. S4 is the FPGA reset control and is intentionally not a GPIO. `make load` and `make run` use FPGA SRAM programming only.

The machine-readable comparison is `build/comparison.json`. Each profile's raw evidence is retained under `build/<profile>/gateware/impl/`: `pnr/project.rpt.txt`, `pnr/project.tr`, and `gwsynthesis/project_syn_resource.html`. Profile build metadata and firmware bounds are in `build/<profile>/build-metadata.json`.
