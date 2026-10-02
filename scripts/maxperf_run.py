#!/usr/bin/env python3
"""Run a fresh baseline and all buildable maxperf candidates on the Dock."""

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_build  # noqa: E402
import benchmark_run  # noqa: E402
import build as build_module  # noqa: E402
import compare  # noqa: E402
from gateware.soc import MEMORY_MODES  # noqa: E402
from memory import profile_build_dir, validate_memory  # noqa: E402


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def run_case(label, profile, build_dir, memory, port, batch_id, evidence_dir,
             candidate_id=None, provisional=False):
    build, firmware = benchmark_run.load_selected_artifacts(
        profile, memory=memory, build_dir=build_dir, cpu_candidate=candidate_id,
        provisional=provisional,
    )
    resources = compare.parse_profile(profile, memory=memory, build_dir=build_dir)
    session = {
        "case": label, "profile": profile,
        "candidate_id": candidate_id if candidate_id is not None else firmware.get("cpu_candidate"),
        "memory_mode": memory, "status": "in_progress",
        "clock_hz": firmware["clock_hz"],
        "identity": benchmark_run.selected_artifact_identity(build, firmware, port),
        "resources_and_timing": resources,
        "trials": [],
    }
    for mode, attempt in (("validation", 0), ("performance", 1),
                          ("performance", 2), ("performance", 3)):
        record = benchmark_run.run_trial(
            profile, build, firmware, mode, attempt, port, f"{batch_id}-{label}",
            45.0, 300.0, evidence_root=evidence_dir / label,
        )
        session["trials"].append(record)
        if record.get("status") != "passed":
            if (record.get("application_end_seen") is not True
                    and record.get("application_halt_seen") is not True
                    and record.get("bios_console_confirmed") is not True):
                session["batch_continuation_safe"] = False
            break
    validation = [item for item in session["trials"]
                  if item["mode"] == "validation" and item["status"] == "passed"]
    scored = [item for item in session["trials"]
              if item["mode"] == "performance" and item["status"] == "passed"]
    session["validation_passes"] = len(validation)
    session["scored_repetitions"] = len(scored)
    session["aggregate"] = None
    if len(validation) == 1 and len(scored) == 3:
        values = [item["parsed"]["coremark"] for item in scored]
        session["aggregate"] = {
            "count": 3, "coremark_mean": sum(values) / 3,
            "coremark_min": min(values), "coremark_max": max(values),
            "coremark_spread": max(values) - min(values),
        }
        session["status"] = "passed"
    else:
        session["status"] = "failed"
    return session


def run_ddr_candidate_check(candidate_id, profile, build_dir, port, batch_id,
                            evidence_dir, *, thorough):
    """Qualify the exact DDR candidate bitstream before accepting its benchmark."""
    import ddr_test_run

    run_id = f"{batch_id}-{candidate_id}-{'qualification' if thorough else 'smoke'}"
    directory = evidence_dir / ("ddr-qualification" if thorough else "ddr-candidate-smoke")
    try:
        session, outcome = ddr_test_run.run_profile(
            profile, port, run_id,
            10 if thorough else 1,
            1800 if thorough else 1,
            45.0,
            ddr_test_run.DEFAULT_TRIAL_TIMEOUT_SECONDS if thorough else 300.0,
            directory, [profile], candidate_id=candidate_id,
            build_dir=build_dir, smoke_only=not thorough,
        )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        session = {
            # Exceptions can happen after the programmer starts. Without a
            # persisted runner session there is no reliable idle-state proof.
            "status": "failed", "batch_continuation_safe": False,
            "error": f"{type(error).__name__}: {error}", "trials": [],
        }
        outcome = 1
    session_path = ROOT / session["evidence"] if session.get("evidence") else None
    result = {
        "status": "passed" if session.get("status") == "passed" and outcome == 0 else "failed",
        "cpu_candidate": candidate_id,
        "acceptance": session.get("acceptance"),
        "training_runs_passed": sum(
            1 for trial in session.get("trials", [])
            if trial.get("mode") == "training" and trial.get("status") == "passed"
        ),
        "stress_seconds": session.get("stress_seconds", 0),
        "full_range_bytes": session.get("full_range_bytes", 0),
        "error_count": session.get("error_count", -1),
        "uncached_smoke_passed": bool(session.get("trials")) and all(
            trial.get("status") == "passed"
            and trial.get("smoke_test", {}).get("status") == "passed"
            for trial in session.get("trials", [])
            if trial.get("mode") == "training"
        ),
        "session_evidence": {
            "path": str(session_path.relative_to(ROOT)),
            "sha256": benchmark_run.sha256(session_path),
        } if session_path is not None else None,
        "session": session,
        "outcome": outcome,
    }
    return result


def build_provisional_maxperf(memory, candidate_id):
    """Build the candidate under the future public identity in a private directory."""
    output = ROOT / "build/maxperf-verification" / memory / candidate_id / "soc"
    build = build_module.build_profile(
        "maxperf", memory=memory, force=True, output_dir=output,
        cpu_candidate=candidate_id, provisional=True,
    )
    firmware = benchmark_build.build_profile_firmware(
        "maxperf", build, memory=memory, build_dir=output,
    )
    return output, build, firmware


def candidate_rank(result, rows):
    """Rank passing candidates by score, then the documented resource tie-breaks."""
    baseline = result["fresh_performance_baseline_mean"]
    ranked = []
    for candidate_id, measurement in result.get("candidates", {}).items():
        if measurement.get("status") != "passed" or not measurement.get("aggregate"):
            continue
        mean = measurement["aggregate"]["coremark_mean"]
        measurement["gain_over_fresh_baseline"] = mean - baseline
        if mean <= baseline:
            continue
        resources = measurement["resources_and_timing"]["resources"]
        ranked.append((candidate_id, measurement, resources))
    return sorted(
        ranked,
        key=lambda item: (-item[1]["aggregate"]["coremark_mean"],
                          item[2]["bsram"]["used"], item[2]["lut"]["used"], item[0]),
    )


def accepted_mode_item(memory, candidate_id, result_path):
    manifest_path = ROOT / "build/maxperf-candidates" / memory / candidate_id / "candidate.json"
    manifest = json.loads(manifest_path.read_text())
    return {
        "status": "accepted", "candidate_id": candidate_id, "clock_hz": 48_000_000,
        "candidate_manifest": {
            "path": str(manifest_path.relative_to(ROOT)),
            "sha256": benchmark_run.sha256(manifest_path),
            "rtl": manifest["rtl"], "rtl_sha256": manifest["rtl_sha256"],
        },
        "evaluation_evidence": {
            "path": str(result_path.relative_to(ROOT)),
            "sha256": benchmark_run.sha256(result_path),
        },
    }


def record_mode_acceptance(memory, candidate_id, result_path):
    """Atomically stage a mode result and expose maxperf only after both modes validate."""
    from gateware.profile_selection import validate_mode_acceptance

    pending_path = ROOT / "docs/performance/maxperf-evaluation/maxperf-pending-selection.json"
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    modes = {}
    existing = pending_path
    if existing.is_file():
        try:
            modes = json.loads(existing.read_text()).get("memory_modes", {})
        except (OSError, json.JSONDecodeError):
            modes = {}
    try:
        from gateware.profile_selection import accepted_maxperf_selection
        previous = accepted_maxperf_selection()
        if previous:
            modes = {**previous["memory_modes"], **modes}
    except ImportError:
        pass
    modes[memory] = accepted_mode_item(memory, candidate_id, result_path)
    validate_mode_acceptance(ROOT, memory, modes[memory])
    pending = {"schema_version": 1, "status": "pending", "memory_modes": modes}
    temporary = pending_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(pending, indent=2) + "\n")
    temporary.replace(pending_path)

    if set(modes) == {"onchip", "ddr3"}:
        validate_mode_acceptance(ROOT, "onchip", modes["onchip"])
        validate_mode_acceptance(ROOT, "ddr3", modes["ddr3"])
        selection = {
            "schema_version": 1, "profile": "maxperf", "status": "accepted",
            "clock_hz": 48_000_000, "memory_modes": modes,
            "created_utc": utc_now(),
        }
        selection_path = ROOT / "maxperf-profile-selection.json"
        temporary_selection = selection_path.with_suffix(".tmp")
        temporary_selection.write_text(json.dumps(selection, indent=2) + "\n")
        temporary_selection.replace(selection_path)
        if accepted_maxperf_selection() is None:
            raise RuntimeError("two-mode maxperf selection failed independent acceptance validation")
    return pending


def ddr_baseline_integrity_gate(port, batch_id, evidence_dir, baseline_build):
    """Qualify the already-built baseline image before scoring that same image."""
    import ddr_test_build
    import ddr_test_run

    ddr_test_build.build_profile_diagnostics("performance", 1800, force=False)
    expected_bitstream = baseline_build.get("bitstream_sha256")
    session, _ = ddr_test_run.run_profile(
        "performance", port, batch_id + "-ddr-preflight", 10, 1800,
        45.0, ddr_test_run.DEFAULT_TRIAL_TIMEOUT_SECONDS,
        evidence_dir / "ddr-preflight", ["performance"],
    )
    session_path = ROOT / session["evidence"]
    identity_matches = (
        bool(expected_bitstream)
        and session.get("build_identity", {}).get("bitstream_sha256") == expected_bitstream
    )
    passed = (
        session.get("status") == "passed"
        and session.get("acceptance") == "thorough"
        and identity_matches
    )
    return {
        "status": "passed" if passed else "failed",
        "acceptance": session.get("acceptance", "failed"),
        "actual_training_runs": session.get("actual_training_runs", 0),
        "stress_seconds": session.get("stress_seconds", 0),
        "full_range_bytes": session.get("full_range_bytes", 0),
        "error_count": session.get("error_count", -1),
        "uncached_smoke_passed": session.get("uncached_smoke_passed", False),
        "session_evidence": {
            "path": str(session_path.relative_to(ROOT)),
            "sha256": benchmark_run.sha256(session_path),
        },
        "expected_bitstream_sha256": expected_bitstream,
        "qualified_bitstream_sha256": session.get("build_identity", {}).get("bitstream_sha256"),
        "identity_matches_scored_baseline": identity_matches,
        "error": session.get("error"),
    }


def run_evaluation(memory, port):
    validate_memory(memory)
    if not Path(port).exists():
        raise FileNotFoundError(f"explicit Dock UART path is unavailable: {port}")
    if not Path(port).is_char_device():
        raise RuntimeError(f"explicit UART path is not a character device: {port}")
    if not os.access(port, os.R_OK | os.W_OK):
        raise PermissionError(f"explicit UART path is not readable and writable: {port}")

    build_path = ROOT / "build/maxperf-candidates" / memory / "candidate-build.json"
    if not build_path.is_file():
        raise RuntimeError(f"{memory} candidate matrix is missing; run make maxperf-build MEMORY={memory}")
    candidate_build = json.loads(build_path.read_text())
    rows = candidate_build.get("candidates", {})
    if not rows:
        raise RuntimeError("candidate build matrix has no recorded outcomes")

    batch_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + f"-{memory}-maxperf"
    evidence_dir = ROOT / "docs/performance/maxperf-evaluation" / batch_id
    evidence_dir.mkdir(parents=True, exist_ok=False)
    result_path = evidence_dir / "evaluation.json"
    result = {
        "schema": 1, "batch_id": batch_id, "memory_mode": memory,
        "clock_hz": 48_000_000, "candidate_build_batch": candidate_build.get("batch_id"),
        "status": "in_progress", "baseline": None, "candidates": {},
        "created_utc": utc_now(),
    }
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    try:
        build_module.apply_project_patches()
        print(f"Building the fresh {memory} performance baseline", flush=True)
        baseline_build = build_module.build_profile("performance", memory=memory, force=True)
        baseline_dir = profile_build_dir(ROOT, "performance", memory)
        baseline_fw = benchmark_build.build_profile_firmware(
            "performance", baseline_build, memory=memory, build_dir=baseline_dir,
        )
        if memory == "ddr3":
            result["ddr_preflight"] = ddr_baseline_integrity_gate(
                port, batch_id, evidence_dir, baseline_build,
            )
            if result["ddr_preflight"]["status"] != "passed":
                result["status"] = "blocked_by_ddr_baseline_integrity"
                result["reason"] = "fresh performance DDR training, BIOS memtest, full range and stress did not qualify"
                return finalize(result, result_path)

        result["baseline"] = run_case(
            "performance-baseline", "performance", baseline_dir,
            memory, port, batch_id, evidence_dir,
        )
        result_path.write_text(json.dumps(result, indent=2) + "\n")
        if result["baseline"]["status"] != "passed":
            result["status"] = "blocked_by_fresh_baseline_failure"
            result["reason"] = "fresh performance baseline did not complete validation plus three scored repetitions"
            return finalize(result, result_path)

        for candidate_id in sorted(rows):
            row = rows[candidate_id]
            if row.get("status") != "ready_for_board_measurement":
                result["candidates"][candidate_id] = {"status": "excluded_build_or_timing_failure",
                                                     "build_status": row.get("status"),
                                                     "reason": row.get("reason")}
                continue
            candidate_dir = ROOT / "build/maxperf-candidates" / memory / candidate_id / "soc"
            if memory == "ddr3":
                smoke = run_ddr_candidate_check(
                    candidate_id, "standard", candidate_dir, port, batch_id,
                    evidence_dir, thorough=False,
                )
                if smoke["status"] != "passed":
                    result["candidates"][candidate_id] = {
                        "status": "excluded_candidate_ddr_smoke_failure",
                        "build_status": row.get("status"),
                        "ddr_candidate_smoke": smoke,
                    }
                    result_path.write_text(json.dumps(result, indent=2) + "\n")
                    if smoke.get("session", {}).get("batch_continuation_safe") is False:
                        result["status"] = "stopped_uncertain_hardware_state"
                        result["reason"] = "candidate DDR smoke did not establish that the Dock is idle"
                        return finalize(result, result_path)
                    continue
            print(f"Measuring {memory} candidate {candidate_id}", flush=True)
            measured = run_case(candidate_id, "standard", candidate_dir, memory,
                                port, batch_id, evidence_dir, candidate_id)
            measured["status"] = measured.get("status", "failed")
            if memory == "ddr3":
                measured["ddr_candidate_smoke"] = smoke
            result["candidates"][candidate_id] = measured
            result_path.write_text(json.dumps(result, indent=2) + "\n")
            if measured.get("batch_continuation_safe") is False:
                result["status"] = "stopped_uncertain_hardware_state"
                result["reason"] = "runner could not confirm application completion; no further candidates programmed"
                return finalize(result, result_path)

        baseline = result["baseline"]["aggregate"]["coremark_mean"]
        result["fresh_performance_baseline_mean"] = baseline
        ranked = candidate_rank(result, rows)
        if not ranked:
            result["status"] = "no_candidate_beats_fresh_baseline"
            return finalize(result, result_path)

        accepted = False
        for candidate_id, winner, _resources in ranked:
            candidate_dir = ROOT / "build/maxperf-candidates" / memory / candidate_id / "soc"
            qualification = {
                "status": "passed", "training_runs_passed": 0,
                "stress_seconds": 0, "full_range_bytes": 0,
                "error_count": 0, "uncached_smoke_passed": True,
            }
            try:
                verify_dir, _verify_build, _verify_firmware = build_provisional_maxperf(
                    memory, candidate_id,
                )
            except Exception as error:
                winner["status"] = "excluded_public_identity_build_failure"
                winner["public_identity_build_error"] = f"{type(error).__name__}: {error}"
                result_path.write_text(json.dumps(result, indent=2) + "\n")
                continue
            if memory == "ddr3":
                qualification_attempt = run_ddr_candidate_check(
                    candidate_id, "maxperf", verify_dir, port, batch_id,
                    evidence_dir, thorough=True,
                )
                qualification = {key: qualification_attempt[key] for key in (
                    "status", "training_runs_passed", "stress_seconds", "full_range_bytes",
                    "error_count", "uncached_smoke_passed", "session_evidence",
                )}
                winner["qualification_attempt"] = qualification_attempt
                result_path.write_text(json.dumps(result, indent=2) + "\n")
                if qualification["status"] != "passed":
                    winner["status"] = "excluded_ddr_qualification_failure"
                    if qualification_attempt.get("session", {}).get("batch_continuation_safe") is False:
                        result["status"] = "stopped_uncertain_hardware_state"
                        result["reason"] = "final maxperf DDR qualification ended without a confirmed safe idle state"
                        return finalize(result, result_path)
                    continue

            verification = run_case(
                "maxperf-identity-verification", "maxperf", verify_dir,
                memory, port, batch_id, evidence_dir, candidate_id,
                provisional=True,
            )
            winner["winner_verification"] = verification
            result_path.write_text(json.dumps(result, indent=2) + "\n")
            if verification.get("batch_continuation_safe") is False:
                result["status"] = "stopped_uncertain_hardware_state"
                result["reason"] = "maxperf identity verification ended without a confirmed safe idle state"
                return finalize(result, result_path)
            verification_mean = (verification.get("aggregate") or {}).get("coremark_mean", 0)
            if verification.get("status") != "passed" or verification_mean <= baseline:
                winner["status"] = "excluded_public_identity_verification_failure"
                continue

            result["qualification"] = qualification
            result["selected_candidate"] = candidate_id
            result["winner_mean"] = verification_mean
            result["gain_percent"] = 100 * (verification_mean / baseline - 1)
            result["status"] = "accepted"
            accepted = True
            break

        if accepted:
            result["finished_utc"] = utc_now()
            result["evidence"] = str(result_path.relative_to(ROOT))
            result_path.write_text(json.dumps(result, indent=2) + "\n")
            record_mode_acceptance(memory, result["selected_candidate"], result_path)
        elif result.get("status") != "stopped_uncertain_hardware_state":
            result["status"] = "no_candidate_passes_public_and_hardware_qualification"
    except KeyboardInterrupt:
        result["status"] = "interrupted"
        result["error"] = "KeyboardInterrupt"
    except BaseException as error:
        result["status"] = "failed"
        result["error"] = f"{type(error).__name__}: {error}"
    return finalize(result, result_path)


def finalize(result, path):
    result.setdefault("finished_utc", utc_now())
    result.setdefault("evidence", str(path.relative_to(ROOT)))
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result.get(key) for key in
                      ("batch_id", "memory_mode", "status", "provisional_winner",
                       "gain_percent", "reason", "error", "evidence")}, indent=2))
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("memory", choices=MEMORY_MODES, nargs="?", default="onchip")
    parser.add_argument("port", nargs="?")
    args = parser.parse_args(argv)
    if not args.port:
        parser.error("an explicit verified Dock UART PORT is required")
    try:
        result = run_evaluation(args.memory, args.port)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"maxperf run failed: {error}", file=sys.stderr)
        return 1
    return 0 if result["status"] == "accepted" else 1


if __name__ == "__main__":
    sys.exit(main())
