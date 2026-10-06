# Offline system fit study

No complete candidate achieved routed timing closure. All design builds were offline. FPGA programming, DDR/SD/Ethernet operation, and Linux boot were not performed.

Run: `20261006T185431.663084Z`. Configuration: [`system-fit-plan.md`](../../system-fit-plan.md). Aggregate machine-readable data: [`report.json`](report.json).

## Candidate outcomes

| Candidate | Result | BIOS ROM | BIOS stack headroom | Routed resources | Evidence |
|---|---|---:|---:|---|---|
| candidate-1-pinned-linux | integration failure | unavailable | unavailable | logic: unavailable; registers: unavailable; bsram: unavailable; ssram: unavailable; dsp: unavailable | [manifest](evidence/candidates/candidate-1-pinned-linux/attempts/rom32k/manifest.json) |
| candidate-2-compact-memory | integration failure | unavailable | unavailable | logic: unavailable; registers: unavailable; bsram: unavailable; ssram: unavailable; dsp: unavailable | [manifest](evidence/candidates/candidate-2-compact-memory/attempts/rom32k/manifest.json) |
| candidate-3-linux-2k-caches | integration failure | unavailable | unavailable | logic: unavailable; registers: unavailable; bsram: unavailable; ssram: unavailable; dsp: unavailable | [manifest](evidence/candidates/candidate-3-linux-2k-caches/attempts/rom32k/manifest.json) |
| candidate-4-linux-1k-caches-no-l2 | integration failure | unavailable | unavailable | logic: unavailable; registers: unavailable; bsram: unavailable; ssram: unavailable; dsp: unavailable | [manifest](evidence/candidates/candidate-4-linux-1k-caches-no-l2/attempts/rom32k/manifest.json) |

## Attribution builds

| Build | Result | Error | Evidence |
|---|---|---|---|
| attribution-ddr-only | integration failure | OSError: Error occured during Gowin's script execution. | [manifest](evidence/attribution/attribution-ddr-only/attempts/rom32k/manifest.json) |
| attribution-ddr-only | integration failure | OSError: Error occured during Gowin's script execution. | [manifest](evidence/attribution/attribution-ddr-only/attempts/sram8k/manifest.json) |
| attribution-ddr-native-sd | integration failure | ModuleNotFoundError: No module named 'litesdcard' | [manifest](evidence/attribution/attribution-ddr-native-sd/attempts/rom32k/manifest.json) |
| attribution-ddr-one-slot-ethernet | integration failure | CalledProcessError: Command '['make', '-j8', '-C', '/home/user/git/fpga/sipeedtangprimer20k-riscv/build/system-fit/20261006T185431.663084Z/attribution/attribution-ddr-one-slot-ethernet/attempts/rom32k/soc/software/bios', '-f', '/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software/bios/Makefile']' returned non-zero exit status 2. | [manifest](evidence/attribution/attribution-ddr-one-slot-ethernet/attempts/rom32k/manifest.json) |
| attribution-ddr-one-slot-ethernet | integration failure | CalledProcessError: Command '['make', '-j8', '-C', '/home/user/git/fpga/sipeedtangprimer20k-riscv/build/system-fit/20261006T185431.663084Z/attribution/attribution-ddr-one-slot-ethernet/attempts/rom48k/soc/software/bios', '-f', '/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software/bios/Makefile']' returned non-zero exit status 2. | [manifest](evidence/attribution/attribution-ddr-one-slot-ethernet/attempts/rom48k/manifest.json) |
| attribution-ddr-one-slot-ethernet | integration failure | OSError: Error occured during Gowin's script execution. | [manifest](evidence/attribution/attribution-ddr-one-slot-ethernet/attempts/sram8k/manifest.json) |

## Limitations

- `ModuleNotFoundError: No module named 'litesdcard'` (full trace in the per-attempt manifest).
- `OSError: Error occured during Gowin's script execution.` (full trace in the per-attempt manifest).
- `CalledProcessError: Command '['make', '-j8', '-C', '/home/user/git/fpga/sipeedtangprimer20k-riscv/build/system-fit/20261006T185431.663084Z/attribution/attribution-ddr-one-slot-ethernet/attempts/rom32k/soc/software/bios', '-f', '/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software/bios/Makefile']' returned non-zero exit status 2.` (full trace in the per-attempt manifest).
- `CalledProcessError: Command '['make', '-j8', '-C', '/home/user/git/fpga/sipeedtangprimer20k-riscv/build/system-fit/20261006T185431.663084Z/attribution/attribution-ddr-one-slot-ethernet/attempts/rom48k/soc/software/bios', '-f', '/home/user/git/fpga/sipeedtangprimer20k-riscv/.deps/litex/litex/soc/software/bios/Makefile']' returned non-zero exit status 2.` (full trace in the per-attempt manifest).

Timing closure requires active 48 MHz system, 96 MHz DDR, and 50 MHz RMII clocks, nonnegative setup/hold/recovery/removal slack, and no unexpected unconstrained internal clock paths. Resource values omitted by the vendor report remain marked unavailable.

Per-attempt manifests and retained Gowin evidence are under [`evidence/`](evidence/). Full build outputs remain in `build/system-fit/20261006T185431.663084Z/`.
