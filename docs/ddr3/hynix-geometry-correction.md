# H5TQ1G63EFR geometry correction

The fitted part reported by the board owner is H5TQ1G63EFR. The [SK hynix datasheet, Rev. 1.0, page 9](https://datasheet4u.com/pdf/732289/H5TQ1G63EFR-xxL.pdf) specifies 64M ×16: eight banks, 8192 rows and 1024 columns, totaling 134,217,728 bytes (128 MiB). It uses row addresses A0–A12. The upstream IMD128M16R39CG8GNF selection instead configured 16384 rows and 256 MiB.

The project now owns the module selection and overrides that upstream default. Both the cached range `[0x40000000, 0x48000000)` and the CPU/L2-bypassing alias `[0xc0000000, 0xc8000000)` map 128 MiB. The alias still passes through LiteDRAM. Generated constants, metadata, diagnostic firmware, full-range test bounds, trace address decoding and acceptance checks use that same geometry. Source identities include the new module and geometry files so older builds cannot silently be reused.

The address-alias test covers byte-address bits 2–26 (25 offsets, 100 bytes); bit 27 is outside physical RAM. Cache visibility retains 64 samples and 128 operations, distributed every 2 MiB across the corrected range. Earlier 256 MiB captures are preserved as historical evidence and cannot qualify this part. Older public DDR builds require rebuilding.

This correction preserves 48 MHz system / 96 MHz DDR CK, DLL-off CL6/CWL6 and the existing conservative controller timing envelope, including 160 ns tRFC. The complete chip speed suffix has not been supplied. This is a geometry correction, not a claim that every timing parameter has been qualified against that suffix.

## Fresh hardware results

The corrected diagnostic image has SHA-256 `74518b3959a8ea91974c923a0a4ad152a6fe20b9fa05a5c380e47133352414f4`. Its routed setup/hold/recovery/removal slacks are +0.105/+0.318/+1.480/+1.635 ns, with zero violated endpoints. Two fresh SRAM configurations trained both lanes and reached the BIOS console.

- [Neighboring-row probe](diagnosis/20261002T030129.340475Z-dll-off-write-trace/trace-probe.json): BIOS still returns `0xb4ce6cd9` instead of `0xb4de6cd9` at offset `0xb64f4`. Writes of ones to the next row change untouched offset `0x169770` from `0xffffffff` to `0xffefffff` (bit 20). Writes of zeros to the next row change untouched offset `0x64011ac` from `0xfffff7ff` to `0x7ffff7ff` (bit 31). Read-only controls retain the expected values. All these addresses are below 128 MiB.
- [Bounded geometry probe](diagnosis/20261002T030658.410143Z-dll-off-write-trace/trace-probe.json): distinct sentinels at offset zero, each address bit 2–26, and the final word `0x07fffffc` survive 378 readbacks without an observed alias. This covers 27 addresses and does not substitute for full-range integrity qualification.
- [Public minimal build](diagnosis/20261002T030430-public-minimal-geometry-build/result.json): smoke and full firmware compile with the generated 128 MiB headers. Routing fails setup by 0.911 ns on the lane-1 DQS HOLD path (`sys_clk` to `sys2x_clk` falling edge). The timing gate blocks programming; no public hardware pass is claimed.

An independent audit reconstructs physical words, frames, sentinel writes and readbacks from retained raw UART, verifies image and source hashes, and checks the memory-map sizes against the retained geometry. Both captures remain diagnostic-only. **DDR integrity remains failed after correcting capacity.** These observations do not isolate a defective chip, board routing, electrical timing or PHY behavior as the root cause.

100 host regression tests and all ten generated designs (five profiles in two memory modes) pass. Current board recovery uses a dedicated SRAM idle image holding RESET# low, CKE low, CS# high, CK stopped and ODT low. Flash was not programmed. The previous combined `--detect --reset` command does not prove that reset happened; an empty UART alone is insufficient proof of an unconfigured FPGA. Exact recovery and audit records are linked by the [machine-readable correction](diagnosis/20261002-geometry-correction.json).
