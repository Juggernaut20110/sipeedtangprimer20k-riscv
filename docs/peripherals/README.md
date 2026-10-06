# Optional SD and Ethernet builds

SD-over-SPI and RMII Ethernet are opt-in build features. Existing `make build` defaults still leave both disabled. Peripheral artifacts live under `build/peripherals/<memory>/<profile>/sd-<mode>_eth-<mode>/` so they do not replace the normal firmware, DDR qualification images, or CoreMark builds.

Build each configuration with the project’s pinned environment and licensed Gowin flow:

```sh
make peripheral-build PROFILE=standard MEMORY=ddr3 SDCARD=spi ETHERNET=none
make peripheral-build PROFILE=standard MEMORY=ddr3 SDCARD=none ETHERNET=rmii
make peripheral-build PROFILE=standard MEMORY=ddr3 SDCARD=spi ETHERNET=rmii
```

`PROFILE` accepts `minimal`, `lite`, `standard`, `performance`, `linux`, or `ALL`. `MEMORY` accepts `onchip` or `ddr3`. On-chip builds use explicitly identified compact diagnostic firmware by default; use `--full-application` through `scripts/peripheral_build.py` only when testing whether the entire application fits on chip. A diagnostic pass does not mean full filesystem or networking support. DDR3 firmware links its code, data, BSS and 16 KiB stack into the checked 128 MiB region.

The normal application exposes these UART commands when the corresponding feature is built:

```text
sd status
sd ls [/path]
sd read /path
sd roundtrip 512
sd roundtrip 4096
sd roundtrip 1048576
net status
net mac XX:XX:XX:XX:XX:XX
net static IP MASK GATEWAY
net dhcp
```

SD tests create new files in a uniquely named `T20K####` directory. The firmware does not format media. FAT16, FAT32 and exFAT are enabled with UTF-8 long names; formatting remains disabled. Ethernet listens for UDP echo on port 5001, TCP echo on 5002, and (in combined builds) a new-file upload on TCP 5003. The upload begins with an 8-byte big-endian length/CRC32 header, followed by the binary data; firmware syncs, closes and reopens the newly created file before returning `OK`, byte count and CRC32.

Once a fitted image passes routed timing, load it with an explicit UART path:

```sh
make peripheral-run PROFILE=standard MEMORY=ddr3 SDCARD=spi ETHERNET=rmii PORT=/dev/serial/by-id/<verified-Dock-UART> BOARD_SERIAL=<observed-board-serial> BOARD_REVISION=<verified-board-revision>
```

The runner rejects stale builds, a missing artifact/hash, and builds that fail required timing. It loads SRAM images only and creates a per-run evidence directory with raw UART RX/TX bytes, programmer output, event timestamps and a build/board identity manifest. Closing the interactive session does not mark any hardware test as passed. The destructive DDR suite can be run against the exact peripheral SoC/image only after its matching DDR diagnostic build and timing gate pass.

The peripheral DDR qualification flow is separate from the normal DDR diagnostic flow and binds the diagnostic image to the same feature-specific SoC:

```sh
make ddr-test-build PROFILE=standard STRESS_SECONDS=1800 SDCARD=spi ETHERNET=none
make ddr-test-run PROFILE=standard PORT=/dev/serial/by-id/<verified-Dock-UART> TRAINING_RUNS=10 STRESS_SECONDS=1800 SDCARD=spi ETHERNET=none BOARD_SERIAL=<observed-board-serial> BOARD_REVISION=<verified-board-revision>
```

Run the destructive 128 MiB suite only on the identified test board and after all files are closed. The test loads its small control firmware into the dedicated diagnostic SRAM, records each fresh SRAM reconfiguration and training pass, then verifies the entire DDR range, cached/uncached visibility and delayed-readback stress. A qualification manifest is written beside the raw captures under `docs/peripherals/evidence/<session>/`; it remains partial unless it meets the full training, memory and stress criteria. It does not certify the SD/network acceptance criteria for that image.

From a host on the same test network, exercise echo payloads and the 1 MiB SD upload with:

```sh
python3 scripts/peripheral_host_test.py <explicit-board-IP> --ping --output host-test.json
```

Use `--no-upload` for Ethernet-only acceptance or when no supported FAT test volume is available. Throughput fields distinguish echo time from upload time. This runner verifies UDP/TCP binary echo at 1, 64, 512 and 1472 bytes, connection recovery, then streams a deterministic 1 MiB payload to port 5003 and checks the returned byte count/CRC32. The 1472-byte UDP test fills a 1500-byte IPv4 packet. A successful host test alone does not prove card persistence; the firmware’s upload result includes its close/reopen comparison.

`make peripheral-report` regenerates `docs/peripherals/report.md` and `results.json`. A board criterion passes only when a manifest under `docs/peripherals/evidence/<session>/` matches the current source fingerprint, feature set, bitstream and firmware hashes, names the board revision/serial, and references unmodified capture files. The evidence schema and minimum measurements are enforced by `scripts/peripheral_evidence.py`; an incomplete or stale manifest remains incomplete.

The report includes all five registered CPU profiles, both memory modes and each SD-only, Ethernet-only and combined feature selection. Build completion and routed timing are separate fields. Rows without current build artifacts or matching board evidence remain explicitly unbuilt, timing-failed or pending.

The attached core is v3961 and Dock is v3714, with no printed unique serial. Use `BOARD_SERIAL=unavailable BOARD_REVISION=core-v3961_Dock-v3714`; this records the user-confirmed observation without treating the generic debugger name as a serial. The Dock is connected to DHCP. The installed 32 GB card is exFAT, and the user requested support for it. The writable application now supports that format. See [hardware observations](hardware-observations.md). Build outcomes, failed routes and pending board tests are tracked separately in the report. Historical DDR qualification is not counted for a new peripheral image.

A firmware-only rebuild can preserve a routed image using:

```sh
.venv/bin/python scripts/peripheral_build.py --profile lite --memory ddr3 --sdcard spi --ethernet rmii --refresh-software
```

Run this in the project tool environment (`scripts.project.tool_environment()`). The rebuild regenerates the synthesis inputs and compares all logic, RAM initialization, external RTL and constraints before retaining the existing bitstream. It ignores only generated timestamps, the hierarchy drawing, and internal SDRIO wire numbering. Mismatches require full routing. Prior metadata and firmware are retained under `software-refresh-history/`. DDR evidence can carry forward only for that identical bitstream and recorded hardware equivalence; application acceptance still requires the new firmware identity.

Recorded application status and Ethernet tests after matching DDR qualification:

```sh
.venv/bin/python scripts/peripheral_accept.py --profile minimal --sdcard none --ethernet rmii --port /dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0 --board-serial unavailable --board-revision core-v3961_Dock-v3714 --ddr-session docs/peripherals/evidence/<matching-DDR-session> --network-tests
```

With SPI enabled, this command creates new 512-byte, 4 KiB and 1 MiB files, syncs/closes/reopens and verifies them. Use `--status-only` for inspection without writes. Combined SPI/RMII runs also perform a 1 MiB TCP upload and at least 1800 measured seconds of repeated SD file tests with concurrent full-size UDP and TCP echo traffic. This command obtains a DHCP lease, verifies binary echo and ping, reuses that same leased address for static-address testing, and retains UART and host captures. It writes only newly created files in its unique test directory. Run it in the project tool environment with device/network access.

Physical removal/reinsertion acceptance is opt-in with `--card-cycle-control-dir /tmp/<new-session-directory>`. After all file operations and network services are closed, the runner creates `removed.ready` and waits up to 600 seconds for `removed.confirmed`, which the operator creates only after physically removing the card. It verifies bounded status/list errors, creates `inserted.ready`, and waits for `inserted.confirmed` after reinsertion. It then verifies the original CID and reads the closed 512-byte test file. A preexisting control directory is refused to prevent stale acknowledgements. Without this physical sequence, the SD criterion remains explicitly partial.

Use `--memory onchip` without `--ddr-session` to record execution of reduced diagnostic images. This verifies the generated controller presence and MAC slot geometry through UART; full filesystem and network tests are refused for these images. The report retains those diagnostic observations without counting them as SD or Ethernet acceptance.

Performance SD-only DDR builds use a 4 KiB BIOS working SRAM to resolve placement exhaustion; peripheral-disabled images retain their approved 8 KiB budget. The accepted performance CPU RTL/caches, 16 KiB diagnostic SRAM, 8 KiB DDR L2, clocks and 128 MiB memory geometry are preserved. Build metadata records BIOS static storage and requires at least 2 KiB of remaining stack space. This static bound does not measure peak stack use; exact-image runtime qualification remains required.

After all workloads and files are closed, load the peripheral-safe idle image into SRAM and observe five quiet UART seconds:

```sh
.venv/bin/python scripts/peripheral_recover.py --port /dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0 --board-serial unavailable --board-revision core-v3961_Dock-v3714 --workload-complete --attach-to docs/peripherals/evidence/<completed-session>
```

Recovery holds DDR in reset, SD deselected with its clock stopped, PHY in reset, and Ethernet TX disabled. Its bitstream, programmer output and quiet UART capture are saved in a new evidence session. No flash persistence is implied.

`net restart` restarts PHY auto-negotiation and clears power-down/isolation. For the identified RTL8201F (`001c:c816`), initialization checks page 7 register 16, selects RMII and PHY clock output, preserves factory TX/RX offsets and restores the original MDIO page. This follows the [Realtek register description](https://www.quick-teck.co.uk/Management/EEUploadFile/1468925005.pdf). UART status includes BMCR, BMSR, advertisements, partner abilities and the checked RMII register. A PHY identification or correct mode does not establish a live LAN link.
