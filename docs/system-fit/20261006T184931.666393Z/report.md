# Offline system fit study

No complete candidate achieved routed timing closure. All design builds were offline. FPGA programming, DDR/SD/Ethernet operation, and Linux boot were not performed.

Run: `20261006T184931.666393Z`. Configuration: [`system-fit-plan.md`](../../system-fit-plan.md). Aggregate machine-readable data: [`report.json`](report.json).

## Candidate outcomes

| Candidate | Result | BIOS ROM | BIOS stack headroom | Routed resources | Evidence |
|---|---|---:|---:|---|---|
| candidate-1-pinned-linux | integration failure | unavailable | unavailable | logic: unavailable; registers: unavailable; bsram: unavailable; ssram: unavailable; dsp: unavailable | [manifest](evidence/candidates/candidate-1-pinned-linux/attempts/rom32k/manifest.json) |
| candidate-2-compact-memory | integration failure | unavailable | unavailable | logic: unavailable; registers: unavailable; bsram: unavailable; ssram: unavailable; dsp: unavailable | [manifest](evidence/candidates/candidate-2-compact-memory/attempts/rom32k/manifest.json) |
| candidate-3-linux-2k-caches | tool failure | unavailable | unavailable | logic: unavailable; registers: unavailable; bsram: unavailable; ssram: unavailable; dsp: unavailable | [manifest](evidence/candidates/candidate-3-linux-2k-caches/manifest.json) |
| candidate-4-linux-1k-caches-no-l2 | tool failure | unavailable | unavailable | logic: unavailable; registers: unavailable; bsram: unavailable; ssram: unavailable; dsp: unavailable | [manifest](evidence/candidates/candidate-4-linux-1k-caches-no-l2/manifest.json) |

## Attribution builds

| Build | Result | Error | Evidence |
|---|---|---|---|
| attribution-ddr-only | integration failure | OSError: Unable to find any of the cross compilation toolchains:
- riscv64-pc-linux-musl
- riscv64-unknown-elf
- riscv64-unknown-linux-gnu
- riscv64-elf
- riscv64-linux
- riscv64-linux-gnu
- riscv-sifive-elf
- riscv64-none-elf
- riscv32-pc-linux-musl
- riscv32-unknown-elf
- riscv32-unknown-linux-gnu
- riscv32-elf
- riscv32-linux
- riscv32-linux-gnu
- riscv32-none-elf
- riscv-none-embed
- riscv-none-elf
 | [manifest](evidence/attribution/attribution-ddr-only/attempts/rom32k/manifest.json) |
| attribution-ddr-native-sd | integration failure | ModuleNotFoundError: No module named 'litesdcard' | [manifest](evidence/attribution/attribution-ddr-native-sd/attempts/rom32k/manifest.json) |
| attribution-ddr-one-slot-ethernet | integration failure | OSError: Unable to find any of the cross compilation toolchains:
- riscv64-pc-linux-musl
- riscv64-unknown-elf
- riscv64-unknown-linux-gnu
- riscv64-elf
- riscv64-linux
- riscv64-linux-gnu
- riscv-sifive-elf
- riscv64-none-elf
- riscv32-pc-linux-musl
- riscv32-unknown-elf
- riscv32-unknown-linux-gnu
- riscv32-elf
- riscv32-linux
- riscv32-linux-gnu
- riscv32-none-elf
- riscv-none-embed
- riscv-none-elf
 | [manifest](evidence/attribution/attribution-ddr-one-slot-ethernet/attempts/rom32k/manifest.json) |

## Limitations

- `ModuleNotFoundError: No module named 'litesdcard'`
- `StudyError: pinned VexRiscv Linux generator failed with exit 1; see /home/user/git/fpga/sipeedtangprimer20k-riscv/build/system-fit/20261006T184931.666393Z/candidates/candidate-3-linux-2k-caches/cpu/generator.log`
- `StudyError: pinned VexRiscv Linux generator failed with exit 1; see /home/user/git/fpga/sipeedtangprimer20k-riscv/build/system-fit/20261006T184931.666393Z/candidates/candidate-4-linux-1k-caches-no-l2/cpu/generator.log`

Timing closure requires active 48 MHz system, 96 MHz DDR, and 50 MHz RMII clocks, nonnegative setup/hold/recovery/removal slack, and no unexpected unconstrained internal clock paths. Resource values omitted by the vendor report remain marked unavailable.

Per-attempt manifests and retained Gowin evidence are under [`evidence/`](evidence/). Full build outputs remain in `build/system-fit/20261006T184931.666393Z/`.
