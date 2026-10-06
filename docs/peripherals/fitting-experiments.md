# Peripheral fitting experiments

These isolated builds preserve registered CPU identities/caches, 48 MHz system and 96 MHz DDR clocks, 128 MiB DDR geometry, standard frame capacity and timing gates. They were not programmed onto the board. The original default builds remain separate. Captured reports and metadata are hash-recorded in [fitting evidence](fitting-evidence/manifest.json).

| Profile/features | Layout trial | Result | Observed blocker |
| --- | --- | --- | --- |
| linux / SD none / Ethernet rmii | one RX and one TX slot, 2048 bytes each | failed | ERROR  (PR0003) : Failed to place with '6 SSRAM(s) unPlaced ' in the device 'GW2A-18C-PBGA256-8' |
| linux / SD none / Ethernet rmii | 24 KiB BIOS ROM reservation | failed | ERROR  (PR0003) : Failed to place with '2 SSRAM(s) unPlaced, 4111 REG(s) unPlaced ' in the device 'GW2A-18C-PBGA256-8' |
| linux / SD none / Ethernet rmii | 4 KiB BIOS working SRAM | failed | ERROR  (PR0003) : Failed to place with '6 SSRAM(s) unPlaced ' in the device 'GW2A-18C-PBGA256-8' |
| linux / SD spi / Ethernet rmii | one RX and one TX slot, 2048 bytes each | failed | ERROR  (PR0003) : Failed to place with '6 SSRAM(s) unPlaced ' in the device 'GW2A-18C-PBGA256-8' |
| linux / SD spi / Ethernet rmii | 24 KiB BIOS ROM reservation | failed | ERROR  (PR0003) : Failed to place with '48 SSRAM(s) unPlaced, 4100 REG(s) unPlaced ' in the device 'GW2A-18C-PBGA256-8' |
| linux / SD spi / Ethernet rmii | 4 KiB BIOS working SRAM | failed | ERROR  (PR0003) : Failed to place with '6 SSRAM(s) unPlaced ' in the device 'GW2A-18C-PBGA256-8' |
| performance / SD spi / Ethernet none | 24 KiB BIOS ROM reservation | failed | ERROR  (PR0003) : Failed to place with '6 SSRAM(s) unPlaced ' in the device 'GW2A-18C-PBGA256-8' |
| performance / SD spi / Ethernet none | 4 KiB BIOS working SRAM | passed | passed |
| standard / SD none / Ethernet rmii | one RX and one TX slot, 2048 bytes each | failed | ERROR  (PR0003) : Failed to place with '6 SSRAM(s) unPlaced ' in the device 'GW2A-18C-PBGA256-8' |
| standard / SD none / Ethernet rmii | 24 KiB BIOS ROM reservation | failed | ERROR  (PR0003) : Failed to place with '2 SSRAM(s) unPlaced, 3356 REG(s) unPlaced ' in the device 'GW2A-18C-PBGA256-8' |
| standard / SD none / Ethernet rmii | 4 KiB BIOS working SRAM | failed | ERROR  (PR0003) : Failed to place with '6 SSRAM(s) unPlaced ' in the device 'GW2A-18C-PBGA256-8' |
| standard / SD spi / Ethernet rmii | one RX and one TX slot, 2048 bytes each | failed | ERROR  (PR0003) : Failed to place with '6 SSRAM(s) unPlaced ' in the device 'GW2A-18C-PBGA256-8' |
| standard / SD spi / Ethernet rmii | 24 KiB BIOS ROM reservation | failed | ERROR  (PR0003) : Failed to place with '2 SSRAM(s) unPlaced, 3549 REG(s) unPlaced ' in the device 'GW2A-18C-PBGA256-8' |
| standard / SD spi / Ethernet rmii | 4 KiB BIOS working SRAM | failed | ERROR  (PR0003) : Failed to place with '6 SSRAM(s) unPlaced ' in the device 'GW2A-18C-PBGA256-8' |

The performance SD-only 4 KiB SRAM trial passed placement and timing. This remedy was implemented and rebuilt in the registered feature-specific output directory; the previous failed default layout is preserved in its `failed-build-history/`. The registered image still requires exact-image DDR and physical SD qualification. No other successful layout was found in these trials.

A smaller ROM reservation did not resolve placement exhaustion. The smaller RX/TX and working-SRAM trials also failed for standard/linux Ethernet layouts. Performance Ethernet-only and combined defaults exceed the FPGA logic capacity; reducing packet buffers or starting Linux does not change the registered CPU logic budget.
