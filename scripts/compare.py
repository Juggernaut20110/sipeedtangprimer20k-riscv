#!/usr/bin/env python3
"""Build all CPU profiles and extract measured Gowin resource/timing results."""

import datetime
from html.parser import HTMLParser
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gateware.soc import MEMORY_MODES, PROFILES, SYS_CLK_FREQ  # noqa: E402
from scripts.build import build_profile  # noqa: E402
from scripts.memory import profile_build_dir, validate_memory  # noqa: E402


class TableRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def read_dsp_metric(path):
    parser = TableRows()
    parser.feed(path.read_text(errors="replace"))
    for row_index, row in enumerate(parser.rows[:-1]):
        headers = [cell.upper() for cell in row]
        if "DSP NUMBER" not in headers:
            continue
        column = headers.index("DSP NUMBER")
        values = parser.rows[row_index + 1]
        if len(values) <= column:
            break
        raw = values[column]
        if raw.isdigit():
            return {"used": int(raw), "source": str(path.relative_to(ROOT))}
        return {
            "used": None,
            "status": "unavailable",
            "detail": f"Gowin synthesis resource report lists {raw!r} for DSP NUMBER",
            "source": str(path.relative_to(ROOT)),
        }
    return {
        "used": None,
        "status": "unavailable",
        "detail": "DSP NUMBER was not found in the Gowin synthesis resource report",
        "source": str(path.relative_to(ROOT)),
    }


def parse_profile(profile, memory="onchip", build_dir=None, require_timing_pass=True):
    validate_memory(memory)
    profile_dir = Path(build_dir).resolve() if build_dir is not None else profile_build_dir(ROOT, profile, memory)
    base = profile_dir / "gateware/impl"
    pnr_report = base / "pnr/project.rpt.txt"
    timing_report = base / "pnr/project.tr"
    synthesis_report = base / "gwsynthesis/project_syn_resource.html"
    for artifact in (pnr_report, timing_report, synthesis_report):
        if not artifact.is_file():
            raise RuntimeError(f"missing Gowin report for {profile}: {artifact}")

    pnr = pnr_report.read_text(errors="replace")
    timing = timing_report.read_text(errors="replace")

    logic = re.search(r"^\s*Logic\s+\|\s+(\d+)/(\d+)", pnr, re.M)
    logic_parts = re.search(
        r"^\s*--LUT,ALU,ROM16\s+\|\s+\d+\((\d+) LUT,\s*(\d+) ALU,\s*(\d+) ROM16\)",
        pnr,
        re.M,
    )
    registers = re.search(r"^\s*Register\s+\|\s+(\d+)/(\d+)", pnr, re.M)
    bsram = re.search(r"^\s*BSRAM\s+\|\s+(\d+)/(\d+)", pnr, re.M)
    if not all((logic, logic_parts, registers, bsram)):
        raise RuntimeError(f"unrecognized Gowin resource report format for {profile}: {pnr_report}")

    clock = re.search(
        r"^\s*\d+\s+sys_clk\s+Generated\s+([\d.]+)\s+([\d.]+)MHz",
        timing,
        re.M,
    )
    fmax = re.search(
        r"^\s*\d+\s+sys_clk\s+([\d.]+)\(MHz\)\s+([\d.]+)\(MHz\)",
        timing,
        re.M,
    )
    violated = re.search(r"<Numbers of Setup Violated Endpoints>:\s*(\d+)", timing)
    analyzed = re.search(r"<Numbers of Paths Analyzed>:\s*(\d+)", timing)
    endpoints = re.search(r"<Numbers of Endpoints Analyzed>:\s*(\d+)", timing)
    def path_slacks(table, next_table):
        start = timing.rfind(table)
        if start < 0:
            return None
        block = timing[start:].split(next_table, 1)[0]
        return [
            float(value)
            for value in re.findall(r"^\s*\d+\s+(-?\d+(?:\.\d+)?)\s+\S", block, re.M)
        ]

    setup_slacks = path_slacks("3.1.1 Setup Paths Table", "3.1.2 Hold Paths Table")
    hold_slacks = path_slacks("3.1.2 Hold Paths Table", "3.1.3 Recovery Paths Table")
    recovery_slacks = path_slacks("3.1.3 Recovery Paths Table", "3.1.4 Removal Paths Table")
    removal_slacks = path_slacks("3.1.4 Removal Paths Table", "3.2 Minimum Pulse Width Table")
    generated_clock_active = re.search(
        r"TC_GENERATED_CLOCK\s+Actived\s+create_generated_clock -name sys_clk",
        timing,
    )
    if not all((clock, fmax, violated, analyzed, endpoints, generated_clock_active)) or any(
        paths is None for paths in (setup_slacks, hold_slacks, recovery_slacks, removal_slacks)
    ):
        raise RuntimeError(f"unrecognized Gowin timing report format for {profile}: {timing_report}")

    constraint_mhz = float(fmax.group(1))
    actual_fmax_mhz = float(fmax.group(2))
    worst_slack_ns = min(setup_slacks) if setup_slacks else None
    worst_hold_slack_ns = min(hold_slacks) if hold_slacks else None
    worst_recovery_slack_ns = min(recovery_slacks) if recovery_slacks else None
    worst_removal_slack_ns = min(removal_slacks) if removal_slacks else None
    setup_violations = int(violated.group(1))
    if abs(constraint_mhz - SYS_CLK_FREQ / 1e6) > 0.001:
        raise RuntimeError(f"{profile} timing report constrains {constraint_mhz} MHz instead of 48 MHz")
    if require_timing_pass and (
        actual_fmax_mhz < constraint_mhz
        or any(value is not None and value < 0 for value in (
            worst_slack_ns, worst_hold_slack_ns, worst_recovery_slack_ns, worst_removal_slack_ns
        ))
        or setup_violations
    ):
        raise RuntimeError(
            f"{profile} misses timing: Fmax={actual_fmax_mhz} MHz, slack={worst_slack_ns} ns, "
            f"hold={worst_hold_slack_ns} ns, recovery={worst_recovery_slack_ns} ns, "
            f"removal={worst_removal_slack_ns} ns, violated endpoints={setup_violations}"
        )

    resource_values = {
        "logic": (int(logic.group(1)), int(logic.group(2))),
        "lut": (int(logic_parts.group(1)), None),
        "alu": (int(logic_parts.group(2)), None),
        "registers": (int(registers.group(1)), int(registers.group(2))),
        "bsram": (int(bsram.group(1)), int(bsram.group(2))),
    }
    for name, (used, capacity) in resource_values.items():
        if capacity is not None and used > capacity:
            raise RuntimeError(f"{profile} exceeds Gowin {name} capacity: {used}/{capacity}")

    clock_rows = re.findall(
        r"^\s*\d+\s+(\S+)\s+Generated\s+([\d.]+)\s+([\d.]+)MHz",
        timing,
        re.M,
    )
    generated_clocks = {
        name: {
            "period_ns": float(period),
            "target_mhz": float(frequency),
            "frequency_mhz": float(frequency),
        }
        for name, period, frequency in clock_rows
    }
    if memory == "ddr3":
        for name, expected in (("sys_clk", 48.0), ("ddr_ck_96mhz", 96.0), ("sys2x_clk", 96.0)):
            if name not in generated_clocks or abs(generated_clocks[name]["frequency_mhz"] - expected) > 0.001:
                raise RuntimeError(f"{profile}/ddr3 is missing its {expected:g} MHz {name} constraint")
        # ddr_ck_96mhz is the PHY's ungated 96 MHz source clock. It clocks
        # DHCEN/CLKIN and has no register endpoint of its own; its generated
        # period and parent are still recorded in the clock summary. Require
        # analyzed sequential paths for the gated PHY and CPU clocks.
        unconstrained = [message for message in re.findall(
            r"^No timing paths to get frequency of (.+)$", timing, re.M
        ) if any(name in message for name in ("sys_clk", "sys2x_clk"))]
        if unconstrained:
            raise RuntimeError(f"{profile}/ddr3 has required clocks without analyzed paths: {unconstrained}")

    return {
        "profile": profile,
        "memory_mode": memory,
        "device": re.search(r"^\s*<Part Number>:\s*(\S+)", pnr, re.M).group(1),
        "resources": {
            "logic": {"used": resource_values["logic"][0], "capacity": resource_values["logic"][1]},
            "lut": {"used": resource_values["lut"][0], "source": str(pnr_report.relative_to(ROOT))},
            "alu": {"used": resource_values["alu"][0], "source": str(pnr_report.relative_to(ROOT))},
            "registers": {"used": resource_values["registers"][0], "capacity": resource_values["registers"][1]},
            "bsram": {"used": resource_values["bsram"][0], "capacity": resource_values["bsram"][1]},
            "dsp": read_dsp_metric(synthesis_report),
        },
        "timing": {
            "constraint_mhz": constraint_mhz,
            "actual_fmax_mhz": actual_fmax_mhz,
            "worst_setup_slack_ns": worst_slack_ns,
            "worst_hold_slack_ns": worst_hold_slack_ns,
            "worst_recovery_slack_ns": worst_recovery_slack_ns,
            "worst_removal_slack_ns": worst_removal_slack_ns,
            "setup_violated_endpoints": setup_violations,
            "analyzed_paths": int(analyzed.group(1)),
            "analyzed_endpoints": int(endpoints.group(1)),
            "no_timing_path_messages": re.findall(r"^No timing paths to get frequency of .+$", timing, re.M),
            "generated_clocks": generated_clocks,
        },
        "evidence": {
            "pnr_resource_report": str(pnr_report.relative_to(ROOT)),
            "timing_report": str(timing_report.relative_to(ROOT)),
            "synthesis_resource_report": str(synthesis_report.relative_to(ROOT)),
        },
    }


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", choices=MEMORY_MODES, default="onchip")
    args = parser.parse_args(argv)
    results = []
    comparison = {
        "status": "in_progress",
        "memory_mode": args.memory,
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "profiles": results,
    }
    destination = ROOT / "build" / ("comparison.json" if args.memory == "onchip" else f"{args.memory}/comparison.json")
    try:
        for profile in PROFILES:
            print(f"Building and measuring {profile}", flush=True)
            build_profile(profile, memory=args.memory)
            results.append(parse_profile(profile, memory=args.memory))
        comparison["status"] = "passed"
    except (OSError, RuntimeError, ValueError, KeyError, AttributeError, subprocess.CalledProcessError) as error:
        comparison["status"] = "failed"
        comparison["error"] = f"{type(error).__name__}: {error}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(comparison, indent=2) + "\n")
    print(json.dumps(comparison, indent=2))
    return 0 if comparison["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
