# Sipeed vendor DDR3 test on the connected Tang Primer 20K

Executed October 1, 2026 (America/New_York); evidence directories use UTC.

The requested [Sipeed DDR-test](https://github.com/sipeed/TangPrimer-20K-example/tree/main/DDR-test) was downloaded at commit `e469df4c0c9c41824f405a8515decf24ef1e8e6f`, built with installed Gowin V1.9.12.04, and loaded into FPGA SRAM. All upstream source files and pin/timing constraints were byte-identical to the download. Only the build driver selected the top module and output name. Flash was never programmed.

The build completed synthesis, placement, routing and bitstream generation. Its retained timing report lists zero setup and hold violated endpoints under the upstream constraints. Those constraints declare the clocks asynchronous; this is a report of the supplied design, not independent verification of every CDC or board timing requirement. The 100 MHz application clock's reported Fmax was 101.094 MHz.

## Result

The vendor controller initializes and prints `DDR Size: 1G`, completes its first fill, then prints `Check Failed. Mismatch Occured` during the first check. No completed successful test was observed. The first mismatch appeared about 17.7 seconds after starting the programming command (about 13.6 seconds after initialization). All three fresh SRAM loads reproduced the first-check failure; none reached the second stage. Repetition evidence is retained below.

| Fresh SRAM load | Capture | Outcome |
|---|---|---|
| 1 | [UART](20261002T024119Z/run-1.uart.txt), [events](20261002T024119Z/run-1.events.json), [manifest](20261002T024119Z/results.json) | First-check mismatch; capture continued through another automatic reset |
| 2 | [UART](20261002T024327Z/run-1.uart.txt), [events](20261002T024327Z/run-1.events.json), [manifest](20261002T024327Z/results.json) | First-check mismatch |
| 3 | [UART](20261002T024327Z/run-2.uart.txt), [events](20261002T024327Z/run-2.events.json), [manifest](20261002T024327Z/results.json) | First-check mismatch |

Each capture retains raw UART bytes, timestamped decoded lines, programmer output, the capture script, build log, routed timing and pin/resource report, source SHA-256 hashes, source commit, UART identity and bitstream SHA-256. The built test bitstream remains at `build/sipeed-ddr-test/upstream/impl/pnr/sipeed_ddr_test.fs`; the sparse upstream checkout is `.deps/sipeed-tangprimer20k-example`.

## Chip identification and interpretation

The user identified the physical chip marking as **SK Hynix H5TQ1G63EFR**. Its [manufacturer datasheet hosted by Sipeed](https://dl.sipeed.com/fileList/TANG/Primer_20K/07_Chip_manual/sk_hynix.pdf) identifies this family as 1,073,741,824 bits: **128 MiB**, consistent with the vendor size-detection result. [Sipeed's board specification](https://en.wiki.sipeed.com/hardware/en/tang/tang-primer-20k/primer-20k.html) also lists 128M DDR3.

The existing LiteX project assumes a different chip, `IMD128M16R39CG8GNF`, and asserts 256 MiB in `gateware/soc.py`. That geometry does not describe this identified board. The earlier full-range qualification claims and the hypothesis of a defective chip require reassessment against the actual Hynix part and its timing requirements. This run records the discrepancy; it does not change or qualify the LiteX design.

The unchanged vendor example also fails within its detected 128 MiB range, using Gowin's DDR3 IP independently of LiteDRAM and VexRiscv. Hardware/chip health therefore **cannot be ruled out**. Reproducing a failure with a second controller strengthens the case for checking the board, power and DDR interface, but does **not** prove that the memory silicon is defective. The vendor design, its older encrypted IP, the newer compiler and exact chip timing configuration remain possible contributors. This test prints no failing address, expected word or actual word, so it cannot establish that its mismatch is the same bit/address as the previous LiteX failure.

An upstream detail limits pattern diversity: `rng_inv` is assigned the same `rng_i` as `rng`, so the second pass is not actually an inverted-pattern test despite its state names. The observed failure occurs before that second pass.

A useful next discriminator is to run this exact hashed bitstream on another board with the same Hynix chip. Separately, the LiteX module geometry and timing should be matched to H5TQ1G63EFR before repeating its memory tests.

## Recovery

The capture scripts attempt `openFPGALoader --detect --reset`. The first attempt returned success but UART still emitted test messages; that attempt did not establish idle. Final recovery uses a separate tiny SRAM image that holds DDR RESET# low, CKE low, CS# high, CK stopped and ODT low. Its source, constraints, build/programming logs and post-load UART observation are retained in `recovery/`. The idle image loaded successfully and the following five-second UART capture was empty. [Recovery verification](recovery/results.json).

## Replacement-board comparison, October 5

The replacement board passed both check stages on three fresh loads of this exact bitstream. See the [replacement-board report](new-board-20261005.md). The earlier failures above refer to the previous board. The LiteX geometry correction has since been committed as `747928c`; corrected LiteX has not been tested on the replacement in this comparison.
