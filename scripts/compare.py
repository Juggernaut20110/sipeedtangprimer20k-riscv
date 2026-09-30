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
from gateware.soc import PROFILES, SYS_CLK_FREQ  # noqa: E402
from scripts.build import build_profile  # noqa: E402


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


def parse_profile(profile):
    base = ROOT / "build" / profile / "gateware/impl"
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
    setup_start = timing.rfind("3.1.1 Setup Paths Table")
    if setup_start >= 0:
        setup_block = timing[setup_start:].split("3.1.2 Hold Paths Table", 1)[0]
        slacks = [float(value) for value in re.findall(r"^\s*\d+\s+([\d.]+)\s+\S", setup_block, re.M)]
    else:
        slacks = []
    generated_clock_active = re.search(
        r"TC_GENERATED_CLOCK\s+Actived\s+create_generated_clock -name sys_clk",
        timing,
    )
    if not all((clock, fmax, violated, analyzed, endpoints, generated_clock_active)) or not slacks:
        raise RuntimeError(f"unrecognized Gowin timing report format for {profile}: {timing_report}")

    constraint_mhz = float(fmax.group(1))
    actual_fmax_mhz = float(fmax.group(2))
    worst_slack_ns = min(slacks)
    setup_violations = int(violated.group(1))
    if abs(constraint_mhz - SYS_CLK_FREQ / 1e6) > 0.001:
        raise RuntimeError(f"{profile} timing report constrains {constraint_mhz} MHz instead of 48 MHz")
    if actual_fmax_mhz < constraint_mhz or worst_slack_ns < 0 or setup_violations:
        raise RuntimeError(
            f"{profile} misses timing: Fmax={actual_fmax_mhz} MHz, slack={worst_slack_ns} ns, "
            f"violated endpoints={setup_violations}"
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

    return {
        "profile": profile,
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
            "setup_violated_endpoints": setup_violations,
            "analyzed_paths": int(analyzed.group(1)),
            "analyzed_endpoints": int(endpoints.group(1)),
            "no_timing_path_messages": re.findall(r"^No timing paths to get frequency of .+$", timing, re.M),
        },
        "evidence": {
            "pnr_resource_report": str(pnr_report.relative_to(ROOT)),
            "timing_report": str(timing_report.relative_to(ROOT)),
            "synthesis_resource_report": str(synthesis_report.relative_to(ROOT)),
        },
    }


def main():
    results = []
    comparison = {
        "status": "in_progress",
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "profiles": results,
    }
    destination = ROOT / "build/comparison.json"
    try:
        for profile in PROFILES:
            print(f"Building and measuring {profile}", flush=True)
            build_profile(profile)
            results.append(parse_profile(profile))
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
