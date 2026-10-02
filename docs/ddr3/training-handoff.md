# DLL-off training handoff implementation

> Geometry correction, 2026-10-02 UTC: the fitted H5TQ1G63EFR has 128 MiB and 13 row bits. Earlier 256 MiB assumptions and full-capacity claims in this historical document are superseded by the [correction and hardware retest](hynix-geometry-correction.md). Errors below 128 MiB remain reproducible.

## Result

The receive patch restores DLL-off training at **48 MHz system / 96 MHz DDR CK, CL6/CWL6**. All twelve fresh public-image captures—three each for minimal, lite, standard and performance—train both byte lanes with bitslip 2.

**DDR acceptance still fails.** Every public capture reports one bad word in the BIOS 2 MiB test. A separate full-range diagnostic completed its 256 MiB walking-ones pass and then failed walking zeros. Training success is not memory-integrity acceptance.

[Machine-readable summary](diagnosis/training-handoff-summary.json) contains experiment identities, raw-capture hashes, profile results and routed timing. [Earlier diagnosis](diagnosis.md) and all failed captures are retained.

## Receive failure and patch

The original configuration uses a replicated READ gate at system taps 2 and 3, RCLKSEL 0, and PHY read latency 12. The controlled MR1 experiment was reproduced before changing the PHY: DLL-off has no passing window, enabling the DRAM DLL allows both lanes to train, and disabling it again removes the window. [Fresh reproduction](diagnosis/20261001T131216.886048Z-baseline-reproduction-mr1-toggle/uart.log).

A diagnostic image exposes READ slots, RCLKSEL and the DFII snapshot latency as CSRs. Its controller latency remains fixed; non-default snapshot settings are **training-only**, with no controller memory test. It records both lanes even after a failed lane, plus sticky RVALID/RBURST and pointer observations.

- At snapshot latency 12, all 104 tested READ/RCLKSEL combinations fail the complete two-lane criterion. Some detect bursts, so burst detection alone is insufficient. [Results](diagnosis/20261001T132255.932700Z-dll-off-read-sweep/results.json).
- At snapshot latency 11, candidate 20 is the first complete pass: slots `[0, A, F, 5, 0, 0]`, RCLKSEL 3 on both lanes. Windows are 217 and 214 taps, bitslip 2, centers 108 and 106. [Results](diagnosis/20261001T132852.112537Z-dll-off-read-sweep/results.json).
- A rebuilt fixed image uses the matching controller latency and also trains both lanes. Its BIOS test finds one corrupted word, establishing the separate integrity problem. [Fixed-image capture](diagnosis/20261001T133554.525974Z-dll-off-fixed/uart.log).

The repository-owned [LiteDRAM patch](../../patches/litedram-gw2ddrphy-dll-off-read.patch) changes only DLL-off receive handling:

| Item | Original DLL-off | Patched DLL-off |
| --- | --- | --- |
| READ[0], READ[2] | taps 2 OR 3 | taps 2 OR 3 |
| READ[1], READ[3] | taps 2 OR 3 | taps 1 OR 2 |
| RCLKSEL, both lanes | 0 | 3 |
| PHY read latency | 12 sys cycles | 11 sys cycles |
| Crossbar read latency | 13 sys cycles | 12 sys cycles |
| CL / CWL / rdphase / wrphase | 6 / 6 / 0 / 0 | 6 / 6 / 0 / 0 |
| Write latency | 2 sys cycles | 2 sys cycles |

For X2, the original logical gate covers CK slots 4–7; the new gate covers slots 3–6 before primitive retiming. Moving the alternating READ slot advances the four-CK window by one CK, rather than shifting all READ bits by a complete system cycle. The FIFO clock selection and earlier assembled-burst consumption are coordinated with this gate change. The controller and DFI use the same patched latency.

DLL-on retains the original replicated gate, RCLKSEL 0 and CL-system-latency + 9. The existing CDC patch remains applied. There is no public write-timing adjustment, frequency change, permanent DLL-on workaround or new functional false-path exception.

**Proven:** the selected combination restores two-lane training, and most words also read correctly through the matching controller.

**Strong inference:** the original DLL-off failure comes from receive-gate/FIFO/data-valid alignment. It is not corrected by changing delay taps alone. The exact pin-level DQS arrival has not been measured with an oscilloscope, and the remaining corruption prevents qualifying the complete PHY.

## Gowin primitive interpretation

Primary local source: Gowin V1.9.12.04 `IDE/simlib/gw2a/prim_sim.v`, DQS module, lines 13674–14415. Its file hash and installation path are recorded in the summary. The [Gowin memory-interface guide](https://cdn.gowinsemi.com.cn/UG286E.pdf), DQS port table, documents the interface; the installed behavioral model supplies the following detailed interpretation:

- READ is registered by PCLK and retimed into FCLK. X2_DDR3 consumes READ[0] and READ[1]; READ[2:3] belong to X4. The diagnostic duplicates the two X2 bits into the unused upper bits.
- RCLKSEL bit 0 selects DQSW0 or inverted DQSW270 as the FIFO read clock. Bit 1 selects the shifted register edge; bit 2 adds a read-clock register stage. RCLKSEL 3 selects the delayed positive clock and the same positive edge for the shifted register, without the extra bit-2 stage.
- RLOADN loads DLLSTEP. Falling RMOVE steps the read delay; RDIR 0 increments and 1 decrements. RFLAG indicates a boundary. A reset CSR therefore loads the DLL-derived code; software then steps toward its scan origin.
- HOLD resets FIFO pointer state. RPOINT is the pointer supplied to the input serializers. RVALID reports FIFO availability; it does not validate the training data. RBURST is derived from detected strobe edges. Sticky diagnostic observations are useful flags, not sampled waveforms.
- WSTEP affects DQSW0 and the initial write-delay value. WMOVE changes the delayed DQ/DM clock; it is a separate write experiment and is not part of the receive patch.

The [pinned GW5 PHY](https://github.com/enjoy-digital/litedram/blob/4c979d195aa0c60adcaf70374a52872028aaca63/litedram/phy/gw5ddrphy.py) recognizes the earlier DLL-off strobe in its X4 receive handling. That architectural observation guided the experiment. Its X4 gate implementation was not copied into GW2 X2.

## Public-profile measurements

All values below come from fresh routed public builds and three SRAM reconfiguration captures per profile. These are reconfigurations, not power cycles.

| Profile | Two-lane training | Window lengths, lanes 0 / 1 | BIOS data errors | Setup / hold / recovery / removal, ns |
| --- | --- | --- | --- | --- |
| minimal | 3 / 3 | 94 / 92 | 1 / 524288 each boot | +0.502 / +0.319 / +1.274 / +2.006 |
| lite | 3 / 3 | 94–95 / 92–93 | 1 / 524288 each boot | +0.277 / +0.321 / +1.779 / +1.543 |
| standard | 3 / 3 | 214 / 89 | 1 / 524288 each boot | +0.548 / +0.318 / +1.779 / +1.365 |
| performance | 3 / 3 | 212–213 / 210 | 1 / 524288 each boot | +0.530 / +0.077 / +2.022 / +1.070 |

Bitslip remains 2 on every lane. Selected centers vary by at most one tap within each profile's three captures. Placement changes the available window, so the larger diagnostic-image windows are not substituted for public-image measurements. The summary links every capture and preserves the routed timing reports. All required setup, hold, recovery and removal checks pass.

## Remaining integrity failure

The diagnostic minimal image reports:

```text
address  0x400b64f4 / uncached 0xc00b64f4
expected 0xb4de6cd9
actual   0xb4ce6cd9
xor      0x00100000
```

The following tests preserve the failed evidence rather than masking it:

| Probe | Observation | Evidence |
| --- | --- | --- |
| Cached and uncached reads, then uncached 2 MiB PRNG rewrite | Same wrong word through both aliases; rewrite reproduces it | [Capture](diagnosis/20261001T134228.324835Z-fixed-integrity-probe/uart.log) |
| Seven isolated values at six addresses | All 126 repeated uncached reads pass | [Summary](diagnosis/20261001T134327.466031Z-fixed-targeted-write-probe/summary.json) |
| Lane-0 read delay at center, ±40, then restored | All 32 reads retain the same wrong value without rewriting | [Summary](diagnosis/20261001T134512.729998Z-fixed-read-delay-probe/summary.json) |
| Isolated correct write, 30 seconds of reads | Correct value persists; shifted PRNG test instead fails at 0xc0169770, again losing bit 20 | [Capture](diagnosis/20261001T134643.233397Z-fixed-retention-offset-probe/uart.log) |
| Three read-only PRNG passes per alias | Each reports the same one-word error | [Capture](diagnosis/20261001T135054.249726Z-fixed-read-only-probe/uart.log) |
| Separate write-clock delay sweep | No clean candidate; several retain the original error, others break calibration | [Summary](diagnosis/20261001T135426.643587Z-fixed-write-delay-probe/summary.json) |
| Large constant-pattern writes | At 0xc0169770, all-ones and 0x55555555 lose bit 20; isolated patterns and other sampled values can pass | [Summary](diagnosis/20261001T141457.310606Z-public-constant-pattern-probe/summary.json) |

A separate recovery experiment raised controller tWR from two to three system cycles (four to six CK), while MR0 remained at five CK. Both lanes trained, but the same `0x400b64f4` bit-20 error remained. [Recovery capture](diagnosis/20261001T152517.396911Z-dll-off-wr-recovery/uart.log). No public recovery setting was changed. The numerical MR0/controller comparison alone does not prove a command-spacing violation; FSM and precharge timing must also be considered.

The write-delay sweep changes only a diagnostic image. Because RCLKSEL 3 derives the FIFO read clock from DQSW270, moving that clock also affects receive retiming. This sweep is not independent write-path isolation and cannot rule out a write-timing problem. Its early experiment continued recording bounded ROM tests after failed calibration; those later results are unusable as qualified memory tests. The reusable probe now stops at a failed calibration. No write-clock sweep setting was promoted to public builds.

The flushed full-range diagnostic reports:

```text
walking_ones: 268435456 bytes, 67108864 words, errors=0
walking_zeros: failed at 0xc64011ac
expected=0xfffff7ff actual=0x7ffff7ff xor=0x80000000
DDR_TEST_END status=failed
```

[Complete full-range capture](diagnosis/20261001T143340.012035Z-full-probe-minimal/full-01.uart.log). It executes code, data and stack from the dedicated on-chip RAM, and accesses DDR through the alias that bypasses both CPU caches and LiteDRAM L2. This is direct evidence of corruption beyond the cache path and beyond the first 2 MiB. The periodic walking-ones pattern alone does not prove absence of address aliasing or physical capacity.

The failures involve bit 20 (physical DQ4) and bit 31 (physical DQ15), both on an upper 16-bit beat. A single failing DQ4 wire is therefore not established. Stored-data/write integrity is favored by the repeatable reads and isolated-write results; remaining receive capture, write timing, addressing/refresh interactions and memory-device behavior are not yet distinguished conclusively. Neither a defective chip nor a fully correct write path has been proven.

A second full-range run fails at the same walking-zeros address and value. Before rewriting, 96 reads at offsets 0, ±20 and ±40 on lane 1 all retain `0x7ffff7ff`; the original receive delay is restored. [Read-delay failure probe](diagnosis/20261001T144349.005478Z-full-probe-minimal/full-01.uart.log).

A third full-range run repeats that failure. Its 96 reads again retain the wrong value; a **single isolated rewrite** at the restored delay produces the correct value on all 16 subsequent reads. The original failure and failed end status remain recorded. [Isolated rewrite comparison](diagnosis/20261001T145250.552487Z-full-probe-minimal/full-01.uart.log). This favors corruption left in storage by the long write sweep, or subsequent disturbance/retention, over a simple receive-center adjustment. It does not yet identify the faulty component.

## Diagnostic and acceptance fixes

Three hidden issues became observable once read leveling worked:

1. The pinned BIOS allowed loads only into main RAM or explicitly reserved BIOS SRAM. It rejected `0x20000000`, preventing the existing destructive diagnostic from starting. [Boot patch](../../patches/litex-ddr-diagnostic-boot.patch) admits only the generated diagnostic-RAM region, with the same existing bounded-range helper. BIOS SRAM reservations, ROM exclusion and outside-region rejection remain enforced.
2. Booted diagnostic firmware has interrupts disabled. The UART library can leave a message tail in its software ring until another write fills it. This delayed startup markers and stranded the final failure/end record before WFI. `uart_sync()` now follows every diagnostic printf record. The original partial capture and passive continuation remain preserved alongside the complete rerun.
3. The runner/report now require exactly one successful BIOS memory-test result. A training or smoke marker cannot override `Memtest KO`. Reopening the UART during BIOS recovery no longer loses the original boot/training records: validation uses the complete raw capture. A deliberately labeled full-range **diagnostic-only** trial may investigate failed memory, but cannot satisfy acceptance.

[Training capture patch](../../patches/litex-sdram-read-capture-diagnostic.patch) changes all-lane reporting only under its diagnostic macro. [Read-only memtest patch](../../patches/litex-memtest-read-only-diagnostic.patch) exposes bounded PRNG verification only in the diagnostic image. Both preserve normal BIOS behavior when their macros are absent.

## Reproduction and validation

Use the pinned project environment. Every hardware build must pass routed timing before capture. These commands program FPGA SRAM only:

```sh
python3 scripts/ddr_diagnose.py build --experiment baseline-reproduction
python3 scripts/ddr_diagnose.py capture --experiment baseline-reproduction --dll-mode-probe --port /dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0

python3 scripts/ddr_diagnose.py build --experiment dll-off-read-sweep
python3 scripts/ddr_diagnose.py sweep --latency-offsets 0 --limit 104 --port /dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
python3 scripts/ddr_diagnose.py sweep --latency-offsets=-1,1 --limit 208 --port /dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0

python3 scripts/ddr_diagnose.py build --experiment dll-off-integrity
python3 scripts/ddr_diagnose.py probe --probe-kind read-only --port /dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0

make ddr-test-build PROFILE=ALL
python3 scripts/ddr_diagnose.py capture-public --profile minimal --port /dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
python3 scripts/ddr_diagnose.py full-probe --profile minimal --read-delay-probe --isolated-write-probe --port /dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0
make test
```

The original receive path remains reproducible after installing the patch: baseline/sweep construction uses the unchanged DLL-on CL6 receive logic and restores the software DLL-off mode flag before controller/BIOS generation. Historical experiment families use this same original baseline.

All dependency changes are repository-owned patches in setup order and build fingerprints. Clean-pin application tests reconstruct the patched PHY exactly, exercise the diagnostic BIOS patches, and compile/run the bounded boot-region check on the host. Regression checks cover both DLL modes, the actual READ operands, matching DFI/controller latency, lane-record validation, BIOS-failure rejection and flushed UART records. **71 host tests and all eight generated SoC checks pass.** Four public DDR images pass routed timing.

## Final acceptance gate and board state

Each public profile was requested to run ten training/smoke cycles and 1800 seconds of stress. Each **first** attempt correctly stops at `Memtest KO`, refuses acceptance-firmware upload and records a confirmed idle BIOS console. No successful acceptance cycle or stress duration is credited. The machine-readable summary includes the four profile refusal records and the final performance restoration trial; the existing [acceptance report](report.md) remains failed.

After the completed full-range diagnostic, the final [SRAM-only restoration trial](evidence/20261001T153918.134692Z-ddr3-performance/session.json) leaves the board at the **performance DDR BIOS prompt**, with no application running. Flash was not changed.

## Qualification left outstanding

The primary zero-window failure is corrected experimentally, but DDR is **not accepted**. Ten successful training/smoke cycles per profile, a passing BIOS test, the complete address/PRNG/access-width/visibility suite, full-range integrity, 1800-second stress per profile and DDR CoreMark remain unfulfilled. Three development training captures per profile do not replace that acceptance count. The full-range probe stops on corruption, before later phases or stress.

The write-data/mask and returned-burst investigation is now recorded in [DDR3 write disturbance](write-disturbance.md). Correct digital write inputs were observed for the original BIOS error. Writes to the next row in the same bank reproduced bit-20 and bit-31 corruption at two untouched victims; conservative row spacing and ODT-low did not fix it. All seven captures remain diagnostic-only. The current board was explicitly reset to safe idle afterward, as documented in that report. Comparing the exact retained images on another board and observing pin-level DDR signals/supplies are the next hardware discriminators. Continue preserving first-error addresses and bit masks; training-window size does not establish integrity.
