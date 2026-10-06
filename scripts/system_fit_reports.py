"""Strict parsers and outcome classification for the offline fit study."""

from html.parser import HTMLParser
import re
from pathlib import Path


class _HtmlRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            # Gowin's generated resource HTML omits some closing </tr> tags.
            if self.row is not None:
                self.rows.append(self.row)
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def _metric(used, capacity, source):
    return {
        "used": used,
        "capacity": capacity,
        "headroom": capacity - used if capacity is not None else None,
        "status": "available" if capacity is not None else "capacity unavailable in report",
        "source": str(source),
    }


def _unavailable_metric(source, reason):
    return {
        "used": None, "capacity": None, "headroom": None,
        "status": f"unavailable: {reason}", "source": str(source),
    }


def _synthesis_resource_metrics(synthesis_path):
    """Parse Gowin's synthesis utilization summary when routing is unavailable."""
    synthesis_path = Path(synthesis_path)
    report_path = synthesis_path.with_name("project_syn.rpt.html")
    names = ("logic", "registers", "bsram", "ssram", "dsp", "pll")
    result = {name: _unavailable_metric(report_path, "resource row not found") for name in names}
    if not report_path.is_file():
        return result

    rows = _HtmlRows()
    rows.feed(report_path.read_text(errors="replace"))
    if rows.row is not None:
        rows.rows.append(rows.row)

    summary_rows = {}
    for index, row in enumerate(rows.rows):
        if len(row) < 3 or row[0].strip().lower() != "resource" or row[1].strip().lower() != "usage":
            continue
        for summary_row in rows.rows[index + 1:]:
            if len(summary_row) < 3:
                continue
            resource, usage, _ = summary_row[:3]
            key = resource.strip().lower()
            if key == "logic":
                summary_rows["logic"] = usage
            elif key == "register":
                summary_rows["registers"] = usage
            elif key == "bsram":
                summary_rows["bsram"] = usage
            elif key.startswith("--"):
                continue
            elif key in ("ssram", "dsp", "clock"):
                break

    for name, usage in summary_rows.items():
        match = re.search(r"(\d+).*?/\s*(\d+)", usage)
        if match:
            result[name] = _metric(int(match.group(1)), int(match.group(2)), report_path)

    for index, row in enumerate(rows.rows):
        if not row:
            continue
        key = row[0].strip().upper()
        if key == "SSRAM" and len(row) > 1 and row[1].strip().isdigit():
            result["ssram"] = _metric(int(row[1].strip()), None, report_path)
        if key == "DSP":
            used = 0
            found = False
            for child in rows.rows[index + 1:]:
                child_name = child[0].strip().upper() if child else ""
                if child_name in {"BSRAM", "CLOCK", "LUT", "ALU", "SSRAM", "INV", "IOLOGIC"}:
                    break
                if len(child) > 1 and child[1].strip().isdigit():
                    used += int(child[1].strip())
                    found = True
            if found:
                result["dsp"] = _metric(used, None, report_path)
        if key == "RPLL" and len(row) > 1 and row[1].strip().isdigit():
            result["pll"] = _metric(int(row[1].strip()), None, report_path)
    return result


def parse_resources(pnr_path, synthesis_path):
    """Read placed resource totals without inventing missing capacities."""
    pnr_path = Path(pnr_path)
    synthesis_path = Path(synthesis_path)
    pnr = pnr_path.read_text(errors="replace") if pnr_path.is_file() else ""
    metrics = {}
    patterns = {
        "logic": r"^\s*Logic\s+\|\s+(\d+)\s*/\s*(\d+)",
        "registers": r"^\s*Register\s+\|\s+(\d+)\s*/\s*(\d+)",
        "bsram": r"^\s*BSRAM\s+\|\s+(\d+)\s*/\s*(\d+)",
        "dsp": r"^\s*DSP\s+\|\s+(\d+)\s*/\s*(\d+)",
    }
    for name, pattern in patterns.items():
        match = re.search(pattern, pnr, re.M)
        if not match:
            metrics[name] = {
                "used": None, "capacity": None, "headroom": None,
                "status": "unavailable: resource row not found",
                "source": str(pnr_path),
            }
        else:
            metrics[name] = _metric(int(match.group(1)), int(match.group(2)), str(pnr_path))

    ssram = re.search(r"^\s*--SSRAM\(RAM16\)\s+\|\s+(\d+)\s+\|\s*(\S+)", pnr, re.M)
    metrics["ssram"] = _metric(
        int(ssram.group(1)) if ssram else None,
        int(ssram.group(2)) if ssram and ssram.group(2).isdigit() else None,
        str(pnr_path),
    ) if ssram else {
        "used": None, "capacity": None, "headroom": None,
        "status": "unavailable: resource row not found", "source": str(pnr_path),
    }

    clock_resources = {}
    in_clock_table = False
    for line in pnr.splitlines():
        if "Clock Resource Usage Summary" in line:
            in_clock_table = True
            continue
        if in_clock_table and line.strip().startswith("===="):
            if clock_resources:
                break
        if not in_clock_table:
            continue
        match = re.match(r"\s*([A-Za-z0-9_]+)\s+\|\s+(\d+)\s*/\s*(\d+)\s+\|", line)
        if match:
            name, used, capacity = match.groups()
            clock_resources[name] = _metric(int(used), int(capacity), str(pnr_path))

    rows = _HtmlRows()
    if synthesis_path.is_file():
        rows.feed(synthesis_path.read_text(errors="replace"))
    if rows.row is not None:
        rows.rows.append(rows.row)
    synthesis_dsp = None
    for index, row in enumerate(rows.rows[:-1]):
        headers = [cell.upper() for cell in row]
        if "DSP NUMBER" in headers:
            column = headers.index("DSP NUMBER")
            values = rows.rows[index + 1]
            if column < len(values) and values[column].isdigit():
                synthesis_dsp = int(values[column])
            break
    if synthesis_dsp is None:
        metrics["synthesis_dsp"] = {
            "used": None, "capacity": None, "headroom": None,
            "status": "unavailable: DSP NUMBER not found in synthesis report",
            "source": str(synthesis_path),
        }
    else:
        metrics["synthesis_dsp"] = _metric(synthesis_dsp, None, str(synthesis_path))

    return {
        "resources": metrics,
        "clock_resources": clock_resources,
        "synthesis_resources": _synthesis_resource_metrics(synthesis_path),
    }


def _clock_rows(timing):
    clocks = {}
    for match in re.finditer(
        r"^\s*\d+\s+(\S+)\s+(?:Base|Generated|Propagated)\s+([\d.]+)\s+([\d.]+)MHz",
        timing, re.M,
    ):
        name, period, frequency = match.groups()
        clocks[name] = {"period_ns": float(period), "frequency_mhz": float(frequency)}
    return clocks


def _max_fmax_rows(timing):
    return {
        name: {"constraint_mhz": float(required), "actual_mhz": float(actual)}
        for name, required, actual in re.findall(
            r"^\s*\d+\s+(\S+)\s+([\d.]+)\(MHz\)\s+([\d.]+)\(MHz\)",
            timing, re.M,
        )
    }


def _table_slacks(timing, title, following):
    start = timing.rfind(title)
    if start < 0:
        return None
    block = timing[start:].split(following, 1)[0]
    rows = re.findall(r"^\s*\d+\s+(-?\d+(?:\.\d+)?)\s+\S", block, re.M)
    return [float(value) for value in rows]


def parse_timing(timing_path, *, require_rmii=True):
    """Parse timing gates for all required clocks and the four slack classes."""
    timing_path = Path(timing_path)
    timing = timing_path.read_text(errors="replace")
    clocks = _clock_rows(timing)
    fmax = _max_fmax_rows(timing)
    required = {"sys_clk": 48.0, "ddr_ck_96mhz": 96.0, "sys2x_clk": 96.0}

    absent = []
    wrong = {}
    for name, frequency in required.items():
        row = clocks.get(name)
        if row is None:
            absent.append(name)
        elif abs(row["frequency_mhz"] - frequency) > 0.001:
            wrong[name] = {"expected_mhz": frequency, "actual_mhz": row["frequency_mhz"]}
    rmii_clock = None
    if require_rmii:
        # The Tang Primer 20K RMII reference input is named
        # eth_clocks_ref_clk; accept eth_rx as a backwards-compatible alias.
        rmii_clock = next((name for name in ("eth_clocks_ref_clk", "eth_rx") if name in clocks), None)
        if rmii_clock is None:
            absent.append("50 MHz RMII reference clock (eth_clocks_ref_clk)")
        elif abs(clocks[rmii_clock]["frequency_mhz"] - 50.0) > 0.001:
            wrong[rmii_clock] = {"expected_mhz": 50.0, "actual_mhz": clocks[rmii_clock]["frequency_mhz"]}

    slack_tables = {
        "setup": _table_slacks(timing, "3.1.1 Setup Paths Table", "3.1.2 Hold Paths Table"),
        "hold": _table_slacks(timing, "3.1.2 Hold Paths Table", "3.1.3 Recovery Paths Table"),
        "recovery": _table_slacks(timing, "3.1.3 Recovery Paths Table", "3.1.4 Removal Paths Table"),
        "removal": _table_slacks(timing, "3.1.4 Removal Paths Table", "3.2 Minimum Pulse Width Table"),
    }
    worst_slacks = {
        name: min(values) if values else None for name, values in slack_tables.items()
    }
    path_count = re.search(r"<Numbers of Paths Analyzed>:\s*(\d+)", timing)
    endpoint_count = re.search(r"<Numbers of Endpoints Analyzed>:\s*(\d+)", timing)
    setup_violations = re.search(r"<Numbers of Setup Violated Endpoints>:\s*(\d+)", timing)
    hold_violations = re.search(r"<Numbers of Hold Violated Endpoints>:\s*(\d+)", timing)
    unconstrained = re.findall(r"^No timing paths to get frequency of (.+)$", timing, re.M)
    required_clock_names = set(required)
    if rmii_clock:
        required_clock_names.add(rmii_clock)
    required_path_warnings = [
        warning for warning in unconstrained
        if any(name in warning for name in required_clock_names)
    ]
    unexpected_unconstrained = [
        warning for warning in unconstrained
        if warning not in required_path_warnings
        and not re.match(r"rPLL/CLKOUT(?:P|D|D3)\.default_gen_clk!?$", warning)
    ]

    setup_violation_count = int(setup_violations.group(1)) if setup_violations else None
    hold_violation_count = int(hold_violations.group(1)) if hold_violations else None
    analyzed_paths = int(path_count.group(1)) if path_count else 0
    analyzed_endpoints = int(endpoint_count.group(1)) if endpoint_count else 0
    gate_errors = []
    if absent:
        gate_errors.append(f"required timing clocks missing: {', '.join(absent)}")
    if wrong:
        gate_errors.append(f"required timing clocks have wrong frequency: {wrong}")
    if analyzed_paths <= 0 or analyzed_endpoints <= 0:
        gate_errors.append("timing analysis did not report analyzed paths and endpoints")
    if setup_violation_count is None or hold_violation_count is None:
        gate_errors.append("timing report is missing setup/hold violation counts")
    elif setup_violation_count or hold_violation_count:
        gate_errors.append(
            f"timing reports setup/hold violations: {setup_violation_count}/{hold_violation_count}"
        )
    for name, slack in worst_slacks.items():
        if slack is None:
            gate_errors.append(f"{name} slack is unavailable")
        elif slack < 0:
            gate_errors.append(f"negative {name} slack: {slack} ns")
    if required_path_warnings:
        gate_errors.append(f"required clocks have no analyzed paths: {required_path_warnings}")
    if unexpected_unconstrained:
        gate_errors.append(f"unexpected unconstrained clock paths: {unexpected_unconstrained}")
    for name in ("sys_clk", "ddr_ck_96mhz", "sys2x_clk"):
        row = fmax.get(name)
        if not row:
            gate_errors.append(f"{name} is missing from the maximum-frequency report")
        elif row["actual_mhz"] < row["constraint_mhz"]:
            gate_errors.append(
                f"{name} Fmax {row['actual_mhz']} MHz is below its {row['constraint_mhz']} MHz constraint"
            )
    if rmii_clock:
        row = fmax.get(rmii_clock)
        if not row:
            gate_errors.append(f"{rmii_clock} is missing from the maximum-frequency report")
        elif row["actual_mhz"] < row["constraint_mhz"]:
            gate_errors.append(
                f"{rmii_clock} Fmax {row['actual_mhz']} MHz is below its {row['constraint_mhz']} MHz constraint"
            )

    return {
        "status": "passed" if not gate_errors else "failed",
        "errors": gate_errors,
        "clocks": clocks,
        "fmax": fmax,
        "analyzed_paths": analyzed_paths,
        "analyzed_endpoints": analyzed_endpoints,
        "setup_violated_endpoints": setup_violation_count,
        "hold_violated_endpoints": hold_violation_count,
        "worst_slack_ns": worst_slacks,
        "no_timing_path_messages": unconstrained,
        "unexpected_unconstrained": unexpected_unconstrained,
        "source": str(timing_path),
    }


def classify_outcome(*, timing_pass=False, resource_failure=False,
                     integration_failure=False, tool_failure=False,
                     timing_failure=False):
    """Return one report classification, keeping tool/integration blockers distinct."""
    if timing_pass:
        return "routed timing pass"
    if integration_failure:
        return "integration failure"
    if tool_failure:
        return "tool failure"
    if resource_failure:
        return "resource failure"
    if timing_failure:
        return "timing failure"
    return "integration failure"
