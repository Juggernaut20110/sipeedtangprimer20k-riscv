#!/usr/bin/env python3
"""Isolated DDR PHY status and optional BIOS mode probes; no acceptance firmware."""
import argparse
import datetime
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(experiment):
    from migen import Cat, Constant, If, Instance, ResetSignal, Signal
    from litex.gen import LiteXModule
    from litex.soc.interconnect.csr import CSRStatus
    from gateware.soc import ProjectSoC
    from scripts.build import builder_for

    output = ROOT / 'build/ddr3/diagnosis' / experiment
    output.mkdir(parents=True, exist_ok=True)
    if experiment not in ('current-status', 'baseline-reproduction', 'legacy-reset', 'legacy-rclksel', 'legacy-both',
                          'dll-on-6-6', 'latency-8-7', 'read-gate-early', 'dll-off-read-sweep', 'dll-off-fixed', 'dll-off-integrity', 'dll-off-write-sweep'):
        raise ValueError('unsupported experiment')
    if experiment == 'dll-off-read-sweep':
        from scripts.ddr_read_sweep import make_sweep_soc
        soc = make_sweep_soc()
    elif experiment in ('dll-on-6-6', 'latency-8-7'):
        from litex_boards.targets import sipeed_tang_primer_20k as target
        original_phy = target.GW2DDRPHY

        class ModePHY(original_phy):
            def __init__(self, *args, **kwargs):
                kwargs['dll_off'] = False
                kwargs['cl'], kwargs['cwl'] = (8, 7) if experiment == 'latency-8-7' else (6, 6)
                super().__init__(*args, **kwargs)

        target.GW2DDRPHY = ModePHY
        try:
            soc = ProjectSoC(profile='minimal', memory='ddr3')
        finally:
            target.GW2DDRPHY = original_phy
    else:
        soc = ProjectSoC(profile='minimal', memory='ddr3')

    changes = []
    if experiment in ('dll-off-fixed', 'dll-off-integrity', 'dll-off-write-sweep'):
        soc.add_constant('MEMTEST_DATA_DEBUG', 1)
        if experiment in ('dll-off-integrity', 'dll-off-write-sweep'):
            soc.add_constant('MEMTEST_READ_ONLY_DIAGNOSTIC', 1)
        soc.add_constant('MEMTEST_DEBUG_MAX_ERRORS', 16)
        soc.platform.toolchain.additional_cst_commands.extend([
            'INS_LOC "gw2ddrphy_dqs_hold_0_s0" R50C12;',
            'INS_LOC "gw2ddrphy_dqs_hold_1_s0" R50C45;',
        ])
        changes.append('Fixed DLL-off READ slot1 advance/RCLKSEL3/read_latency11; matching controller; error-address logging')
    if experiment in ('dll-on-6-6', 'latency-8-7'):
        changes.append('Diagnostic DRAM DLL-on mode at 96 MHz; outside the required DLL-off acceptance configuration')
        if experiment == 'latency-8-7':
            changes.append('CL8/CWL7 in both PHY/controller and DRAM mode registers')
    if experiment in ('legacy-reset', 'legacy-both'):
        for item in soc.ddrphy._fragment.specials:
            if isinstance(item, Instance):
                for pin in item.items:
                    if isinstance(pin, Instance.Input) and pin.name == 'RESET':
                        pin.expr = ResetSignal('sys')
        changes.append('PHY primitive RESET inputs use sys reset, as before upstream 311a0f7')
    if experiment in ('legacy-rclksel', 'legacy-both'):
        lane_instances = sorted((item for item in soc.ddrphy._fragment.specials
                                 if isinstance(item, Instance) and item.of == 'DQS'), key=lambda item: item.duid)
        for lane, item in enumerate(lane_instances):
            rdly = Signal(3)
            soc.ddrphy.sync += [
                If(soc.ddrphy._dly_sel.storage[lane] & soc.ddrphy._rdly_dq_rst.wr_stb, rdly.eq(0)),
                If(soc.ddrphy._dly_sel.storage[lane] & soc.ddrphy._rdly_dq_inc.wr_stb, rdly.eq(rdly + 1)),
            ]
            values = {'RLOADN': Constant(0), 'RMOVE': Constant(0),
                      'RDIR': Constant(1), 'RCLKSEL': rdly}
            for pin in item.items:
                if isinstance(pin, Instance.Input) and pin.name in values:
                    pin.expr = values[pin.name]
        del soc.ddrphy._rdly_dq_dir
        soc.ddrphy.settings.delays = 8
        changes.append('Restore eight RCLKSEL positions and DLLSTEP-based delay; remove direction CSR')
    if experiment == 'read-gate-early':
        soc.add_constant('MEMTEST_DATA_DEBUG', 1)
        soc.add_constant('MEMTEST_DEBUG_MAX_ERRORS', 16)
        soc.platform.toolchain.additional_cst_commands.extend([
            'INS_LOC "gw2ddrphy_dqs_hold_0_s0" R50C12;',
            'INS_LOC "gw2ddrphy_dqs_hold_1_s0" R50C45;',
        ])
        from migen import Replicate
        from litedram.common import TappedDelayLine
        read_delay = next(module for _, module in soc.ddrphy._submodules
                          if isinstance(module, TappedDelayLine)
                          and len(module.taps) == soc.ddrphy.settings.read_latency)
        for item in soc.ddrphy._fragment.specials:
            if isinstance(item, Instance) and item.of == 'DQS':
                for pin in item.items:
                    if isinstance(pin, Instance.Input) and pin.name == 'READ':
                        pin.expr = Replicate(read_delay.taps[1] | read_delay.taps[2] | read_delay.taps[3], 4)
        changes.append('Extend DQS READ gate one sys cycle earlier: taps 1,2,3 instead of 2,3; DLL-off CL6/CWL6 unchanged')
        changes.append('Place HOLD registers at R50C12/R50C45 to meet setup; enable BIOS data-error addresses')

    if experiment == 'dll-off-write-sweep':
        from litex.soc.interconnect.csr import CSR, CSRStorage

        class WriteControl(LiteXModule):
            def __init__(self):
                self.enable = CSRStorage(name='enable')
                self.select = CSRStorage(2, reset=3, name='select')
                self.reset = CSR(name='reset')
                self.move = CSR(name='move')
                self.direction = CSRStorage(name='direction')
        soc.ddr_write = WriteControl()
        lanes = sorted((item for item in soc.ddrphy._fragment.specials
                        if isinstance(item, Instance) and item.of == 'DQS'), key=lambda item: item.duid)
        for lane, instance in enumerate(lanes):
            active = soc.ddr_write.enable.storage & soc.ddr_write.select.storage[lane]
            values = {'WLOADN': active & ~soc.ddr_write.reset.wr_stb,
                      'WMOVE': active & soc.ddr_write.move.wr_stb,
                      'WDIR': soc.ddr_write.direction.storage}
            for pin in instance.items:
                if isinstance(pin, Instance.Input) and pin.name in values:
                    pin.expr = values[pin.name]
        changes.append('Diagnostic WLOADN/WMOVE/WDIR control: move DQ/DM write clock while keeping DQS/WSTEP unchanged')

    def port(instance, name):
        return next(item.expr for item in instance.items
                    if isinstance(item, Instance.Output) and item.name == name)

    dll = next(item for item in soc.ddrphy.init._fragment.specials
               if isinstance(item, Instance) and item.of == 'DLL')
    dqs = sorted((item for item in soc.ddrphy._fragment.specials
                  if isinstance(item, Instance) and item.of == 'DQS'), key=lambda item: item.duid)

    class Status(LiteXModule):
        def __init__(self):
            def snapshot(value, name):
                meta = Signal(len(value), name_override='ddr_debug_' + name + '_meta')
                synced = Signal(len(value), name_override='ddr_debug_' + name + '_synced')
                meta.attr.add('no_retiming')
                synced.attr.add('no_retiming')
                self.sync += [meta.eq(value), synced.eq(meta)]
                return synced

            self.clock = CSRStatus(16, name='clock')
            self.lanes = CSRStatus(4, name='lanes')
            self.valid = CSRStatus(2, name='valid')
            clock = Cat(soc.ddrphy.init.delay, port(dll, 'LOCK'), soc.crg.pll.locked,
                        soc.ddrphy.init.stop, soc.ddrphy.init.reset, soc.ddrphy.init.pause)
            clock_sys = snapshot(clock, 'clock')
            self.comb += self.clock.status.eq(clock_sys)
            lanes = Cat(*(Cat(port(item, 'RFLAG'), port(item, 'WFLAG')) for item in dqs))
            lane_sys = snapshot(lanes, 'lanes')
            self.comb += self.lanes.status.eq(lane_sys)
            self.sync += self.valid.status.eq(self.valid.status | soc.ddrphy.datavalid)

    soc.ddr_debug = Status()
    sweep_configuration = None
    if experiment == 'dll-off-read-sweep':
        from scripts.build import apply_project_patches
        from scripts.ddr_read_sweep import add_read_sweep
        apply_project_patches()
        sweep_configuration = add_read_sweep(soc)
        changes.append('Diagnostic CSR-controlled READ schedule/RCLKSEL/DFII snapshot latency; DLL-off retained')
    # Only the first flops of the observational synchronizers are asynchronous.
    # These snapshots never feed the functional PHY or memory controller.
    soc.platform.toolchain.additional_sdc_commands.append(
        'set_false_path -to [get_pins {ddr_debug_clock_meta*/D ddr_debug_lanes_meta*/D}]')
    builder_for(soc, output).build(run=True, build_name='ddr_diagnosis')
    finalize(experiment, changes, sweep_configuration)


def finalize(experiment, changes=None, sweep_configuration=None):
    from scripts.compare import parse_profile
    output = ROOT / 'build/ddr3/diagnosis' / experiment
    if changes is None:
        changes = {'current-status': [], 'baseline-reproduction': [],
                   'legacy-reset': ['PHY primitive RESET inputs use sys reset'],
                   'legacy-rclksel': ['Restore eight RCLKSEL positions and DLLSTEP-based delay; remove direction CSR'],
                   'legacy-both': ['PHY primitive RESET inputs use sys reset',
                                   'Restore eight RCLKSEL positions and DLLSTEP-based delay; remove direction CSR'],
                   'dll-on-6-6': ['Diagnostic DLL-on CL6/CWL6 at 96 MHz'],
                   'latency-8-7': ['Diagnostic DLL-on CL8/CWL7 at 96 MHz in PHY/controller and mode registers'],
                   'dll-off-fixed': ['DLL-off READ slot1 advance/RCLKSEL3/read_latency11; matching controller'],
                   'dll-off-write-sweep': ['Fixed DLL-off receive; diagnostic DQ/DM write-clock delay controls'],
                   'dll-off-integrity': ['Fixed DLL-off receive configuration; read-only PRNG diagnostic'],
                   'dll-off-read-sweep': ['Diagnostic READ/RCLKSEL/DFII latency sweep; fixed controller latency12'],
                   'read-gate-early': ['DQS READ gate uses taps 1,2,3; DLL-off CL6/CWL6 unchanged']}[experiment]
    metrics = parse_profile('minimal', memory='ddr3', build_dir=output)
    bitstream = output / 'gateware/impl/pnr/project.fs'
    manifest = dict(experiment=experiment, changes=changes, diagnostic_only=True, bitstream=str(bitstream),
                    bitstream_sha256=sha(bitstream), metrics=metrics,
                    csr=json.loads((output / 'csr.json').read_text()),
                    clock_bits={'delay': [0, 7], 'dll_lock': 8, 'pll_lock': 9,
                                'stop': 10, 'reset': 11, 'pause': 12},
                    lane_bits='Each pair: RFLAG bit0 WFLAG bit1; lane0 bits0:1 lane1 bits2:3',
                    script_sha256=sha(Path(__file__)),
                    phy_sha256=sha(ROOT / '.deps/litedram/litedram/phy/gw2ddrphy.py'),
                    generated_header_sha256=sha(output / 'software/include/generated/sdram_phy.h'),
                    bios_sha256=sha(output / 'software/bios/bios.bin'))
    if sweep_configuration is not None:
        manifest['receive_sweep'] = sweep_configuration
        manifest['sweep_source_sha256'] = sha(ROOT / 'scripts/ddr_read_sweep.py')
        manifest['diagnostic_patch_sha256'] = sha(ROOT / 'patches/litex-sdram-read-capture-diagnostic.patch')
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


def capture(experiment, device, dll_mode_probe=False):
    import serial
    output = ROOT / 'build/ddr3/diagnosis' / experiment
    manifest = json.loads((output / 'manifest.json').read_text())
    bitstream = Path(manifest['bitstream'])
    if sha(bitstream) != manifest['bitstream_sha256']:
        raise RuntimeError('diagnostic bitstream hash mismatch')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    suffix = '-mr1-toggle' if dll_mode_probe else ''
    evidence = ROOT / 'docs/ddr3/diagnosis' / (stamp + '-' + experiment + suffix)
    evidence.mkdir(parents=True, exist_ok=False)
    (evidence / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for relative in ('software/include/generated/sdram_phy.h', 'gateware/ddr_diagnosis.sdc',
                     'gateware/ddr_diagnosis.cst'):
        path = output / relative
        (evidence / path.name).write_bytes(path.read_bytes())
    (evidence / 'ddr_diagnose.py').write_bytes(Path(__file__).read_bytes())
    raw = bytearray()
    tx = bytearray()
    result = {'experiment': experiment, 'dll_mode_probe': dll_mode_probe, 'console_confirmed': False}
    try:
        with serial.Serial(device, 115200, timeout=0.05, exclusive=True) as uart:
            uart.reset_input_buffer()
            with (evidence / 'programmer.log').open('wb') as log:
                programmer = subprocess.Popen(['openFPGALoader', '-b', 'tangprimer20k', str(bitstream)],
                                              stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    raw.extend(uart.read(4096))
                    if programmer.poll() is not None and programmer.returncode != 0:
                        raise RuntimeError('SRAM programming failed')
                    clean = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', raw)
                    if programmer.poll() == 0 and b'BIOS CRC passed' in clean and clean.rstrip().endswith(b'litex>'):
                        result['console_confirmed'] = True
                        break
                if not result['console_confirmed']:
                    raise RuntimeError('fresh BIOS prompt not confirmed; no commands sent')
                registers = manifest['csr']['csr_registers']
                commands = []
                for name in ('ddr_debug_clock', 'ddr_debug_lanes', 'ddr_debug_valid', 'ddrphy_burstdet_seen'):
                    commands.append((name, f"mem_read 0x{registers[name]['addr']:08x} 4\n", 5))
                if dll_mode_probe:
                    # BIOS runs in ROM/SRAM, with no application active. Toggle only
                    # MR1[0], holding CL/CWL, MR2 and FPGA implementation fixed.
                    commands.extend([
                        ('enable_dll', 'sdram_mr_write 1 0x2\n', 5),
                        ('reset_dll', 'sdram_mr_write 0 0x320\n', 5),
                        ('calibrate_dll_on', 'sdram_cal\n', 90),
                        ('disable_dll', 'sdram_mr_write 1 0x3\n', 5),
                        ('calibrate_dll_off', 'sdram_cal\n', 90),
                    ])
                for name, command_text, timeout in commands:
                    command = command_text.encode()
                    tx.extend(command)
                    uart.write(command)
                    uart.flush()
                    chunk = bytearray()
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        chunk.extend(uart.read(4096))
                        clean = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', chunk)
                        if clean.rstrip().endswith(b'litex>'):
                            break
                    else:
                        result['console_confirmed'] = False
                        raise RuntimeError('diagnostic command did not return to BIOS prompt')
                    raw.extend(chunk)
                    result[name] = clean.decode('utf-8', errors='replace')
    finally:
        (evidence / 'uart.bin').write_bytes(raw)
        (evidence / 'uart.tx.bin').write_bytes(tx)
        text = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', raw).decode('utf-8', errors='replace').replace('\r', '')
        (evidence / 'uart.log').write_text(text)
        result['uart_sha256'] = sha(evidence / 'uart.bin')
        (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(text)
        print(evidence)


def main():
    from scripts.project import VENV_PYTHON, tool_environment
    if Path(sys.prefix) != ROOT / '.venv':
        return subprocess.call([str(VENV_PYTHON), __file__, *sys.argv[1:]], env=tool_environment())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'finalize', 'capture', 'sweep'])
    parser.add_argument('--experiment', default='current-status')
    parser.add_argument('--port')
    parser.add_argument('--limit', type=int, help='Stop the diagnostic sweep after this many candidates')
    parser.add_argument('--latency-offsets', default='0,-1,1,-2,2,-3,3')
    parser.add_argument('--dll-mode-probe', action='store_true',
                        help='At the baseline BIOS console, toggle MR1 DLL mode and recalibrate')
    args = parser.parse_args()
    if args.action == 'build':
        build(args.experiment)
    elif args.action == 'finalize':
        finalize(args.experiment)
    elif args.action == 'sweep':
        if not args.port:
            parser.error('sweep requires --port')
        if args.limit is not None and args.limit < 1:
            parser.error('--limit must be positive')
        from scripts.ddr_read_sweep import run_sweep
        offsets = tuple(int(value) for value in args.latency_offsets.split(','))
        run_sweep(args.port, args.limit, offsets)
    else:
        if not args.port:
            parser.error('capture requires --port')
        if args.dll_mode_probe and args.experiment not in ('current-status', 'baseline-reproduction'):
            parser.error('--dll-mode-probe requires the current-status baseline')
        capture(args.experiment, args.port, args.dll_mode_probe)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
