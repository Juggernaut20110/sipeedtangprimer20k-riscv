#!/usr/bin/env python3
"""Measure a fresh standard baseline and viable prediction candidates."""

import datetime
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_run  # noqa: E402
from cpu_candidates import CANDIDATES  # noqa: E402


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def run_case(label, build_dir, port, batch_id, handshake_timeout, trial_timeout,
             candidate=None):
    session_id = f"{batch_id}-{label}"
    evidence_dir = ROOT / "docs/performance/cpu-evaluation"
    session_dir = ROOT / "build/cpu-candidates/evaluations" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session = {
        "schema_version": 1,
        "session_id": session_id,
        "batch_id": batch_id,
        "case": label,
        "profile": "standard",
        "cpu_candidate": candidate,
        "memory_mode": "onchip",
        "clock_hz": 48_000_000,
        "requested_trials": [
            {"mode": "validation", "attempt": 0},
            {"mode": "performance", "attempt": 1},
            {"mode": "performance", "attempt": 2},
            {"mode": "performance", "attempt": 3},
        ],
        "status": "in_progress",
        "created_utc": utc_now(),
        "trials": [],
    }
    path = session_dir / "session.json"
    path.write_text(json.dumps(session, indent=2) + "\n")
    try:
        build, benchmark = benchmark_run.load_selected_artifacts(
            "standard", memory="onchip", build_dir=build_dir, cpu_candidate=candidate,
        )
        session["identity"] = benchmark_run.selected_artifact_identity(build, benchmark, port)
        for mode, attempt in (("validation", 0), ("performance", 1),
                              ("performance", 2), ("performance", 3)):
            trial = benchmark_run.run_trial(
                "standard", build, benchmark, mode, attempt, port, session_id,
                handshake_timeout, trial_timeout, evidence_root=evidence_dir,
            )
            session["trials"].append(trial)
            path.write_text(json.dumps(session, indent=2) + "\n")
            if trial.get("status") == "interrupted":
                break
            uncertain = (
                trial.get("programming_status") in ("started", "completed")
                and not trial.get("application_end_seen")
                and not trial.get("application_halt_seen")
            )
            if uncertain:
                session["continuation_safe"] = False
                break
            if mode == "validation" and trial.get("status") != "passed":
                break
            if mode == "performance" and trial.get("status") != "passed":
                break
        recorded = {(item.get("mode"), item.get("attempt")) for item in session["trials"]}
        for required in session["requested_trials"]:
            key = (required["mode"], required["attempt"])
            if key not in recorded:
                session["trials"].append({
                    **required, "status": "not_run",
                    "error": "prior trial failed or completion was uncertain",
                })
        performance = [
            trial for trial in session["trials"]
            if trial.get("mode") == "performance" and trial.get("status") == "passed"
        ]
        validation_ok = any(
            trial.get("mode") == "validation" and trial.get("status") == "passed"
            for trial in session["trials"]
        )
        if validation_ok and len(performance) == 3:
            scores = [item["parsed"]["coremark"] for item in performance]
            per_mhz = [item["parsed"]["coremark_per_mhz"] for item in performance]
            session["aggregate"] = {
                "count": 3,
                "coremark_mean": sum(scores) / 3,
                "coremark_min": min(scores),
                "coremark_max": max(scores),
                "coremark_spread": max(scores) - min(scores),
                "coremark_per_mhz_mean": sum(per_mhz) / 3,
                "coremark_per_mhz_spread": max(per_mhz) - min(per_mhz),
            }
            session["status"] = "passed"
        else:
            session["status"] = "failed"
    except Exception as error:
        session["status"] = "failed"
        session["error"] = f"{type(error).__name__}: {error}"
    session["finished_utc"] = utc_now()
    session["evidence"] = str(path.relative_to(ROOT))
    path.write_text(json.dumps(session, indent=2) + "\n")
    return session


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", help="explicit verified Dock UART path")
    parser.add_argument("--handshake-timeout", type=float, default=45.0)
    parser.add_argument("--trial-timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    if args.handshake_timeout <= 0 or args.trial_timeout <= 0:
        parser.error("timeouts must be positive")

    report_path = ROOT / "build/cpu-candidates/evaluations.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    build_report_path = ROOT / "build/cpu-candidates/candidate-build.json"
    if not build_report_path.is_file():
        print("run make cpu-candidate-build before candidate measurements", file=sys.stderr)
        return 1
    build_report = json.loads(build_report_path.read_text())
    batch_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-cpu-eval"
    result = {
        "schema": 1,
        "batch_id": batch_id,
        "status": "in_progress",
        "baseline_clock_hz": 48_000_000,
        "created_utc": utc_now(),
        "cases": {},
        "candidate_build_status": build_report.get("status"),
    }
    report_path.write_text(json.dumps(result, indent=2) + "\n")

    baseline = run_case("fresh-standard", ROOT / "build/standard", args.port, batch_id,
                        args.handshake_timeout, args.trial_timeout)
    result["cases"]["standard"] = baseline
    report_path.write_text(json.dumps(result, indent=2) + "\n")
    if baseline.get("continuation_safe") is False:
        result["status"] = "stopped_completion_uncertain"
    elif baseline.get("status") != "passed":
        result["status"] = "baseline_failed"
    else:
        for candidate in CANDIDATES:
            item = build_report.get("candidates", {}).get(candidate, {})
            if item.get("status") != "ready_for_board_measurement":
                result["cases"][candidate] = {
                    "status": "not_run",
                    "reason": item.get("reason", "candidate did not pass generation, build, resource, timing, and firmware checks"),
                }
                continue
            session = run_case(
                candidate, ROOT / "build/cpu-candidates" / candidate / "soc", args.port,
                batch_id, args.handshake_timeout, args.trial_timeout, candidate=candidate,
            )
            result["cases"][candidate] = session
            report_path.write_text(json.dumps(result, indent=2) + "\n")
            if session.get("continuation_safe") is False:
                result["status"] = "stopped_completion_uncertain"
                break
            if any(trial.get("status") == "interrupted" for trial in session.get("trials", [])):
                result["status"] = "interrupted"
                break

        if result["status"] == "in_progress":
            baseline_mean = baseline.get("aggregate", {}).get("coremark_mean")
            eligible = []
            for candidate in CANDIDATES:
                case = result["cases"].get(candidate, {})
                if case.get("status") != "passed" or case.get("aggregate", {}).get("coremark_mean", 0) <= baseline_mean:
                    continue
                resources = build_report["candidates"][candidate]["resources_and_timing"]["resources"]
                eligible.append((
                    candidate,
                    case["aggregate"]["coremark_mean"],
                    resources["bsram"]["used"],
                    resources["lut"]["used"],
                ))
            if eligible:
                winner = sorted(eligible, key=lambda row: (-row[1], row[2], row[3]))[0]
                result["winner"] = {
                    "candidate": winner[0],
                    "coremark_mean": winner[1],
                    "standard_mean": baseline_mean,
                    "gain": winner[1] - baseline_mean,
                    "bsram_used": winner[2],
                    "lut_used": winner[3],
                    "promotion": "eligible for public profile registration after evidence review",
                }
                result["status"] = "candidate_beats_fresh_standard"
            else:
                result["status"] = "no_candidate_beats_fresh_standard"

    result["finished_utc"] = utc_now()
    report_path.write_text(json.dumps(result, indent=2) + "\n")
    public_report = ROOT / "docs/performance/cpu-evaluation/evaluations.json"
    public_report.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(report_path, public_report)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "candidate_beats_fresh_standard" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
