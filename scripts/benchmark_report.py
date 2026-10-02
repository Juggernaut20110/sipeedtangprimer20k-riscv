#!/usr/bin/env python3
"""Create docs/performance.md from retained build evidence and UART sessions."""

import datetime
import json
import re
import shlex
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from gateware.soc import MEMORY_MODES, PROFILES, SYS_CLK_FREQ  # noqa: E402
import compare as compare_module  # noqa: E402
from benchmark_build import sha256  # noqa: E402
from benchmark_identity import (  # noqa: E402
    CURRENT_IDENTITY_SCHEMA, fingerprint_matches,
)
from benchmark_evidence import bundle_file_matches, bundle_identity  # noqa: E402
from benchmark_results import CaptureValidationError, aggregate, parse_capture  # noqa: E402
from scripts.memory import profile_build_dir, validate_memory  # noqa: E402


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return default


def repo_file(relative_path):
    """Resolve a recorded path without allowing evidence links outside the repository."""
    if not isinstance(relative_path, str) or not relative_path:
        return None
    path = (ROOT / relative_path).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return path


def file_matches(relative_path, expected_hash):
    path = repo_file(relative_path)
    return bool(path and path.is_file() and expected_hash and sha256(path) == expected_hash)


def repo_link(relative_path, label):
    path = repo_file(relative_path)
    if not path or not path.exists():
        return None
    rel = path.relative_to(ROOT.resolve()).as_posix()
    return f"[{label}](<../{rel}>)"


def benchmark_fingerprint_matches(metadata, build_metadata=None, *, allow_bundle=False):
    source_hashes = metadata.get("source_hashes", {})
    if not isinstance(source_hashes, dict) or not source_hashes:
        return False
    legacy_metadata = (
        "identity_schema_version" not in metadata
        and "memory_mode" not in metadata
        and "cpu_candidate" not in metadata
    )
    mismatches = [
        path for path, digest in source_hashes.items()
        if not file_matches(path, digest)
        and not (allow_bundle and bundle_file_matches(metadata.get("evidence_bundle"), path, digest, ROOT))
    ]
    # The original on-chip capture fingerprint included the build orchestration
    # script. Later memory-mode support changed that script while leaving the
    # CoreMark sources, linked images, and bitstream hashes intact. Preserve those
    # legacy sessions using their recorded fingerprint and artifact hashes.
    if mismatches and not (legacy_metadata and mismatches == ["scripts/benchmark_build.py"]):
        return False
    return fingerprint_matches(metadata, build_metadata)


def git_identity():
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    return {
        "revision": revision.stdout.strip() if revision.returncode == 0 else "unavailable",
        "dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
    }


def profile_build_rows(build_summary, memory="onchip"):
    validate_memory(memory)
    rows = {}
    profiles = build_summary.get("profiles", {}) if isinstance(build_summary, dict) else {}
    for profile in PROFILES:
        entry = profiles.get(profile, {})
        profile_dir = profile_build_dir(ROOT, profile, memory)
        build = read_json(profile_dir / "build-metadata.json") or entry.get("build") or {}
        benchmark = read_json(profile_dir / "benchmark/benchmark-metadata.json") or entry.get("benchmark_firmware") or {}
        try:
            # Current vendor reports are the source for resources/timing; cached
            # JSON can describe a different build than the files now on disk.
            resources = compare_module.parse_profile(profile, memory=memory)
        except (OSError, RuntimeError, ValueError, AttributeError, KeyError):
            resources = None
        firmware = benchmark.get("images", {})
        bitstream_path = build.get("bitstream") or benchmark.get("bitstream")
        build_valid = (
            build.get("status") == "passed"
            and build.get("profile") == profile
            and build.get("memory_mode", "onchip") == memory
            and build.get("sys_clk_hz") == SYS_CLK_FREQ
            and benchmark.get("status") == "passed"
            and benchmark.get("profile") == profile
            and benchmark.get("memory_mode", "onchip") == memory
            and benchmark.get("clock_hz") == SYS_CLK_FREQ
            and benchmark_fingerprint_matches(benchmark, build)
            and bool(resources)
            and file_matches(bitstream_path, benchmark.get("bitstream_sha256"))
        )
        for mode in ("performance", "validation"):
            image = firmware.get(mode, {})
            build_valid = build_valid and (
                image.get("profile") == profile
                and image.get("build_id") == benchmark.get("build_id")
                and file_matches(image.get("binary"), image.get("binary_sha256"))
            )
        rows[profile] = {
            "build_status": "passed" if build_valid else "unavailable / evidence mismatch",
            "resources": resources,
            "performance_firmware_bytes": firmware.get("performance", {}).get("binary_bytes"),
            "validation_firmware_bytes": firmware.get("validation", {}).get("binary_bytes"),
            "benchmark_metadata": benchmark,
            "build_metadata": build,
        }
    return rows


def fmt_number(value, digits=3):
    return "—" if value is None else f"{value:.{digits}f}"


def compiler_flags_text(flags):
    if not isinstance(flags, list) or not flags:
        return "Unavailable until the benchmark firmware is built."
    return "```sh\n" + " ".join(shlex.quote(flag) for flag in flags) + "\n```"


def profile_table(rows, selected_profile, hardware_status, hardware_status_by_profile=None):
    lines = [
        "| Profile | Build | ISA / ABI | LUT / ALU | Registers | BSRAM | Timing constraint | Worst slack | Estimated Fmax | Perf / validation image | Maximum SRAM data+BSS+padding / stack / free | Largest static frame | Hardware benchmark |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for profile in PROFILES:
        item = rows[profile]
        metrics = item.get("resources") or {}
        resource = metrics.get("resources", {})
        timing = metrics.get("timing", {})
        logic = resource.get("lut", {}).get("used")
        alu = resource.get("alu", {}).get("used")
        registers = resource.get("registers", {})
        bsram = resource.get("bsram", {})
        lut_alu = "—" if logic is None else f"{logic} / {alu if alu is not None else '—'}"
        reg_text = "—" if not registers else f"{registers.get('used')} / {registers.get('capacity')}"
        bsram_text = "—" if not bsram else f"{bsram.get('used')} / {bsram.get('capacity')}"
        timing_constraint = timing.get("constraint_mhz")
        constraint_text = "—" if timing_constraint is None else f"{timing_constraint:g} MHz"
        slack = timing.get("worst_setup_slack_ns")
        fmax = timing.get("actual_fmax_mhz")
        sizes = item.get("performance_firmware_bytes"), item.get("validation_firmware_bytes")
        size_text = "—" if None in sizes else f"{sizes[0]} / {sizes[1]} B"
        images = item.get("benchmark_metadata", {}).get("images", {})
        perf = images.get("performance", {})
        validation = images.get("validation", {})
        memory_image = max(
            (image for image in (perf, validation) if image.get("working_sram_bytes") is not None),
            key=lambda image: image["working_sram_bytes"],
            default={},
        )
        section_bytes = memory_image.get("sram_section_bytes")
        padding_bytes = memory_image.get("sram_alignment_padding_bytes")
        working_bytes = memory_image.get("working_sram_bytes")
        stack_bytes = memory_image.get("reserved_stack_bytes")
        free_bytes = memory_image.get("remaining_sram_bytes")
        if None in (section_bytes, padding_bytes, working_bytes, stack_bytes, free_bytes):
            sram_text = "—"
        else:
            sram_text = f"{section_bytes}+{padding_bytes}={working_bytes} / {stack_bytes} / {free_bytes} B"
        frames = [image.get("stack_usage", {}).get("largest_static_frame_bytes") for image in (perf, validation)]
        frames = [value for value in frames if value is not None]
        frame_text = "—" if not frames else f"{max(frames)} B"
        flags = item.get("benchmark_metadata", {}).get("compiler_flags", [])
        march = next((flag.removeprefix("-march=") for flag in flags if flag.startswith("-march=")), None)
        mabi = next((flag.removeprefix("-mabi=") for flag in flags if flag.startswith("-mabi=")), None)
        isa_abi = " / ".join(value for value in (march, mabi) if value) or "—"
        if hardware_status_by_profile and profile in hardware_status_by_profile:
            hardware = hardware_status_by_profile[profile]
        elif profile == selected_profile:
            hardware = hardware_status
        else:
            hardware = "not measured"
        lines.append(
            f"| `{profile}` | {item['build_status']} | `{isa_abi}` | {lut_alu} | {reg_text} | {bsram_text} | "
            f"{constraint_text} | {fmt_number(slack)} ns | {fmt_number(fmax)} MHz | {size_text} | "
            f"{sram_text} | {frame_text} | {hardware} |"
        )
    return "\n".join(lines)


def log_link(trial, key, label):
    return repo_link(trial.get(key), label) or "—"


def revalidate_session(session, rows):
    """Reparse passing captures and verify the artifacts named by the session."""
    if not isinstance(session, dict):
        return None
    checked = dict(session)
    checked_trials = []
    errors = []
    profile = session.get("profile")
    identity = session.get("identity") or {}
    memory = session.get("memory_mode", identity.get("memory_mode", "onchip"))
    try:
        validate_memory(memory)
    except ValueError:
        errors.append(f"unknown memory mode {memory!r}")
        memory = "onchip"
    if identity.get("memory_mode", "onchip") != memory:
        errors.append("session memory mode does not match its artifact identity")
    if profile not in PROFILES and profile != "maxperf":
        errors.append(f"unknown session profile {profile!r}")
    if session.get("clock_hz") != SYS_CLK_FREQ or identity.get("clock_hz") != SYS_CLK_FREQ:
        errors.append(f"session clock metadata does not match {SYS_CLK_FREQ} Hz")
    if identity.get("profile") != profile:
        errors.append("session identity profile does not match the selected profile")

    selected = rows.get(profile, {}) if profile in PROFILES or profile == "maxperf" else {}
    bundle_reference = identity.get("evidence_bundle")
    archived_benchmark, archived_build = bundle_identity(bundle_reference, ROOT)
    archive_valid = isinstance(archived_benchmark, dict) and isinstance(archived_build, dict)
    if bundle_reference and not archive_valid:
        errors.append("immutable benchmark evidence bundle is missing, changed, or malformed")
    if archive_valid:
        benchmark = dict(archived_benchmark)
        benchmark["evidence_bundle"] = bundle_reference
        build = archived_build
    else:
        benchmark = selected.get("benchmark_metadata") or {}
        build = selected.get("build_metadata") or {}

    def artifact_matches(relative, expected):
        if not archive_valid:
            return True
        return file_matches(relative, expected) or bundle_file_matches(
            bundle_reference, relative, expected, ROOT,
        )

    build_status_valid = (
        build.get("status") == "passed"
        if archive_valid else selected.get("build_status") == "passed"
    )
    build_identity_valid = (
        build_status_valid
        and (
            benchmark.get("identity_schema_version", 1) < CURRENT_IDENTITY_SCHEMA
            or archive_valid
        )
        and identity.get("source_fingerprint") == benchmark.get("source_fingerprint")
        and (
            not benchmark.get("source_hashes")
            or benchmark_fingerprint_matches(benchmark, build, allow_bundle=archive_valid)
        )
        and (
            benchmark.get("identity_schema_version") != CURRENT_IDENTITY_SCHEMA
            or (
                identity.get("cpu_variant") == benchmark.get("cpu_variant")
                and identity.get("cpu_profile_selection") == benchmark.get("cpu_profile_selection")
                and identity.get("fingerprint_payload") == benchmark.get("fingerprint_payload")
            )
        )
        and identity.get("memory_mode", "onchip") == memory
        and benchmark.get("memory_mode", "onchip") == memory
        and build.get("memory_mode", "onchip") == memory
        and identity.get("cpu_candidate") == benchmark.get("cpu_candidate")
        and (identity.get("bitstream") or {}).get("sha256") == benchmark.get("bitstream_sha256")
        and artifact_matches(
            (identity.get("bitstream") or {}).get("path"), benchmark.get("bitstream_sha256"),
        )
        and (identity.get("firmware") or {}).get("performance", {}).get("sha256")
        == benchmark.get("images", {}).get("performance", {}).get("binary_sha256")
        and artifact_matches(
            (identity.get("firmware") or {}).get("performance", {}).get("path"),
            benchmark.get("images", {}).get("performance", {}).get("binary_sha256"),
        )
        and (identity.get("firmware") or {}).get("validation", {}).get("sha256")
        == benchmark.get("images", {}).get("validation", {}).get("binary_sha256")
        and artifact_matches(
            (identity.get("firmware") or {}).get("validation", {}).get("path"),
            benchmark.get("images", {}).get("validation", {}).get("binary_sha256"),
        )
        and identity.get("coremark", {}).get("commit") == benchmark.get("coremark", {}).get("commit")
        and build.get("sys_clk_hz") == SYS_CLK_FREQ
    )
    if not build_identity_valid:
        errors.append("current build or firmware hashes do not match the captured session identity")

    for source_trial in session.get("trials", []):
        trial = dict(source_trial)
        trial["reparsed"] = None
        if trial.get("status") not in ("passed", "not_run"):
            errors.append(f"{trial.get('mode')} {trial.get('attempt')}: runner recorded {trial.get('status')}: {trial.get('error', 'no detail')}")
        if trial.get("status") == "passed":
            mode = trial.get("mode")
            image = (benchmark.get("images") or {}).get(mode, {})
            raw_path = repo_file(trial.get("raw_uart_log"))
            trial_errors = []
            if mode not in ("validation", "performance"):
                trial_errors.append("unknown trial mode")
            if not raw_path or not raw_path.is_file():
                trial_errors.append("raw UART capture is missing or outside the repository")
            elif not file_matches(trial.get("raw_uart_log"), trial.get("raw_uart_sha256")):
                trial_errors.append("raw UART capture hash does not match session metadata")
            elif trial.get("raw_uart_bytes") != raw_path.stat().st_size:
                trial_errors.append("raw UART capture length does not match session metadata")
            tx_path_value = trial.get("transmitted_uart_log")
            tx_hash_value = trial.get("transmitted_uart_sha256")
            tx_size_value = trial.get("transmitted_uart_bytes")
            if any(value is not None for value in (tx_path_value, tx_hash_value, tx_size_value)):
                tx_path = repo_file(tx_path_value)
                if not tx_path or not tx_path.is_file():
                    trial_errors.append("transmitted UART capture is missing or outside the repository")
                elif not file_matches(tx_path_value, tx_hash_value):
                    trial_errors.append("transmitted UART capture hash does not match session metadata")
                elif tx_size_value != tx_path.stat().st_size:
                    trial_errors.append("transmitted UART capture length does not match session metadata")
            if image.get("binary_sha256") != trial.get("firmware_sha256"):
                trial_errors.append("trial firmware hash does not match the built image")
            if not build_identity_valid:
                trial_errors.append("captured profile/build artifacts are no longer verified")
            if not trial_errors:
                try:
                    raw = raw_path.read_bytes()
                    text_path = repo_file(trial.get("text_uart_log"))
                    raw_text = raw.decode(errors="replace").replace("\r\n", "\n").replace("\r", "\n")
                    if not text_path or not text_path.is_file() or text_path.read_text(errors="replace") != raw_text:
                        trial_errors.append("decoded UART log is missing or differs from the raw capture")
                    if not trial_errors:
                        parsed = parse_capture(
                            raw, profile=profile, build_id=benchmark["build_id"], mode=mode,
                            clock_hz=SYS_CLK_FREQ, data_size=2000, contexts=1,
                            image_crc32=image.get("binary_crc32"), image_bytes=image.get("binary_bytes"),
                        )
                        saved = trial.get("parsed") or {}
                        checked_fields = (
                            "profile", "build_id", "mode", "clock_hz", "data_size", "contexts", "seeds",
                            "image_crc32", "image_bytes", "image_rechecks",
                            "iterations", "elapsed_ticks", "elapsed_seconds", "coremark", "coremark_per_mhz",
                            "seedcrc", "crcs", "crcfinal", "validation", "upstream_iterations_per_second",
                            "upstream_coremark", "calibration", "calibration_plan",
                        )
                        if any(saved.get(key) != parsed.get(key) for key in checked_fields):
                            trial_errors.append("saved parsed results disagree with reparsed UART evidence")
                        else:
                            trial["reparsed"] = parsed
                except (CaptureValidationError, KeyError, TypeError) as error:
                    trial_errors.append(f"UART capture validation failed: {error}")
            if trial_errors:
                trial["report_validation_errors"] = trial_errors
                errors.extend(f"{mode} {trial.get('attempt')}: {reason}" for reason in trial_errors)
        checked_trials.append(trial)

    valid_perf = [
        trial["reparsed"] for trial in checked_trials
        if trial.get("status") == "passed" and trial.get("mode") == "performance" and trial.get("reparsed")
    ]
    valid_validation = any(
        trial.get("status") == "passed" and trial.get("mode") == "validation"
        and trial.get("attempt") == 0 and trial.get("reparsed")
        for trial in checked_trials
    )
    attempts = {
        trial.get("attempt") for trial in checked_trials
        if trial.get("reparsed") and trial.get("mode") == "performance"
    }
    if session.get("status") != "passed":
        errors.append(f"runner session status is {session.get('status', 'missing')}: {session.get('error', 'no detail')}")
    if session.get("programming_status") != "completed":
        errors.append(f"FPGA SRAM programming status is {session.get('programming_status', 'missing')}")
    if not valid_validation:
        errors.append("no revalidated validation-seed trial is available")
    if attempts != {1, 2, 3}:
        errors.append("three revalidated performance repetitions are not available")
    complete = (
        not errors
        and session.get("status") == "passed"
        and session.get("programming_status") == "completed"
        and session.get("clock_hz") == SYS_CLK_FREQ
        and identity.get("clock_hz") == SYS_CLK_FREQ
        and build_identity_valid
        and valid_validation
        and len(valid_perf) == 3
        and attempts == {1, 2, 3}
    )
    checked["trials"] = checked_trials
    checked["report_validation"] = {"status": "passed" if complete else "failed", "errors": errors}
    checked["aggregate"] = aggregate(valid_perf) if complete else None
    return checked


def revalidate_batch_sessions(sessions, batch_id, requested_profiles, rows):
    """Revalidate only sessions belonging to one PROFILE=ALL batch."""
    recorded = list(requested_profiles) if isinstance(requested_profiles, list) else []
    errors = []
    # maxperf may have been public in a historical six-profile batch even when
    # the current checkout no longer has an accepted selection.
    known = set(PROFILES) | {"maxperf"}
    if not recorded:
        errors.append("batch requested_profiles is missing or empty")
    string_members = [profile for profile in recorded if isinstance(profile, str)]
    if len(string_members) != len(set(string_members)):
        errors.append(f"batch requested_profiles contains duplicates: {recorded!r}")
    unknown = [profile for profile in recorded if not isinstance(profile, str) or profile not in known]
    if unknown:
        errors.append("batch requested_profiles contains unknown profiles: " + ", ".join(map(str, unknown)))
    # Profile registries grow over time. The exact membership stored with the
    # batch defines what was requested when it ran, so a valid historical
    # three-profile batch remains complete after a fourth profile is added.
    requested = [profile for profile in recorded if isinstance(profile, str) and profile in known]

    batch_sessions = [
        session for session in sessions
        if isinstance(session, dict) and session.get("batch_id") == batch_id
    ]
    by_profile = {}
    for batch_session in batch_sessions:
        profile = batch_session.get("profile")
        if batch_session.get("requested_profiles") != recorded:
            errors.append(
                f"session {batch_session.get('session_id')!r} records membership "
                f"{batch_session.get('requested_profiles')!r}, inconsistent with batch {recorded!r}"
            )
        if profile not in requested:
            errors.append(f"batch contains profile session {profile!r} outside its recorded membership")
            continue
        if profile in by_profile:
            errors.append(f"batch contains multiple sessions for profile {profile!r}")
        # Retain the newest session in the exact batch while treating the duplicate
        # itself as a batch integrity failure.
        by_profile[profile] = batch_session

    checked = {}
    profile_results = {}
    for profile in requested:
        source = by_profile.get(profile)
        if source is None:
            profile_results[profile] = {
                "session_id": None,
                "runner_status": "not recorded",
                "status": "not measured",
                "aggregate": None,
                "report_validation": {"status": "missing", "errors": ["no session recorded in this batch"]},
            }
            continue
        profile_session = revalidate_session(source, rows)
        checked[profile] = profile_session
        validation = profile_session.get("report_validation", {})
        profile_results[profile] = {
            "session_id": profile_session.get("session_id"),
            "runner_status": profile_session.get("status", "incomplete"),
            "status": validation.get("status", "failed"),
            "aggregate": profile_session.get("aggregate") if validation.get("status") == "passed" else None,
            "report_validation": validation,
        }

    missing = [profile for profile in requested if profile not in by_profile]
    if missing:
        errors.append("batch is missing requested profile sessions: " + ", ".join(missing))
    all_passed = (
        not errors
        and not missing
        and all(profile_results.get(profile, {}).get("status") == "passed" for profile in requested)
    )
    if all_passed:
        status = "passed"
    elif missing or errors:
        status = "incomplete"
    else:
        status = "failed"
    return {
        "batch_id": batch_id,
        "requested_profiles": requested,
        "status": status,
        "missing_profiles": missing,
        "errors": errors,
        "sessions": checked,
        "profile_results": profile_results,
    }


def batch_hardware_status(batch):
    statuses = {}
    for profile in batch.get("requested_profiles", []):
        result = batch.get("profile_results", {}).get(profile, {})
        if result.get("status") == "passed":
            statuses[profile] = "measured (3 valid repetitions)"
        elif result.get("status") in ("missing", "not measured"):
            statuses[profile] = "not run (missing batch session)"
        elif result.get("runner_status") == "failed":
            statuses[profile] = "failed / incomplete"
        else:
            statuses[profile] = "capture failed / incomplete"
    return statuses


def effective_trial_status(trial):
    if trial.get("reparsed"):
        return "passed"
    if trial.get("status") == "passed":
        return "failed (capture evidence rejected)"
    return trial.get("status", "not_run")


def interpretation_text(session, complete, summary, memory="onchip"):
    scope = (
        "Code and read-only data run from the 256 MiB DDR3 main RAM through the 8 KiB LiteDRAM L2; "
        "CoreMark data/BSS and the reserved stack stay in 8 KiB on-chip SRAM."
        if memory == "ddr3" else
        "Code and read-only data run from 32 KiB on-chip main RAM; CoreMark data/BSS and the reserved stack stay in 8 KiB SRAM."
    )
    if not session:
        return (
            "No board session is recorded, so hardware performance remains **not measured**. "
            f"The intended placement is: {scope} "
            "Place-and-route estimated Fmax is separate from the configured 48 MHz operating clock."
        )
    if complete:
        return (
            f"The `{session.get('profile')}` profile completed validation and three accepted CoreMark repetitions at 48 MHz "
            f"(mean {summary['coremark_mean']:.6f} CoreMark, {summary['coremark_per_mhz_mean']:.9f} CoreMark/MHz). "
            f"This covers CoreMark with this placement: {scope} "
            "Place-and-route estimated Fmax is a timing estimate and differs from the configured 48 MHz operating clock."
        )
    validation = next((trial for trial in session.get("trials", []) if trial.get("mode") == "validation"), None)
    if session.get("programming_status") == "completed":
        state = "FPGA SRAM programming completed"
    elif session.get("programming_status") == "failed_or_interrupted":
        state = "FPGA SRAM programming started but did not complete"
    else:
        state = f"FPGA SRAM programming status was {session.get('programming_status', 'not recorded')}"
    if validation and validation.get("error"):
        detail = validation["error"]
        if "benchmark_start" in detail.lower() or "start marker" in detail.lower():
            reason = f"The UART firmware upload/validation did not reach `BENCHMARK_START` ({detail})."
        else:
            reason = f"The validation trial failed ({detail})."
    elif session.get("report_validation", {}).get("errors"):
        reason = "The session could not be accepted after checking its raw UART capture and artifact hashes: " + "; ".join(session["report_validation"]["errors"]) + "."
    else:
        reason = "Validation and three accepted performance repetitions are not all present."
    return (
        f"{state}, but the selected profile has no accepted CoreMark result. {reason} "
        "Failed and incomplete sessions remain in `results.json` and do not enter score aggregates. "
        f"The intended placement is: {scope} "
        "Place-and-route estimated Fmax is separate from the configured 48 MHz operating clock."
    )


def uart_method_text(benchmark):
    commands = (benchmark.get("images") or {}).get("performance", {}).get("commands", {})
    polling = commands.get("litex_uart_polling.c", {})
    flags = polling.get("flags", []) if isinstance(polling, dict) else []
    if "-DUART_POLLING" in flags:
        return (
            "Build metadata verifies the pinned LiteX `libbase/uart.c` polling backend (`-DUART_POLLING`), "
            "which avoids an interrupt-driven TX queue while benchmark interrupts are disabled. Startup, CRC, and timer output follows the timed CoreMark workload."
        )
    return "Build metadata does not verify the `UART_POLLING` backend; the compiler flags above are the recorded settings."


def calibration_method_text(benchmark):
    calibration = benchmark.get("iteration_calibration") or {}
    method = calibration.get("method") if isinstance(calibration, dict) else None
    if method == "fixed_iteration_port_calibration_with_fresh_upstream_initialization":
        count = calibration.get("calibration_iterations")
        target = calibration.get("target_seconds")
        formula = calibration.get("formula")
        cache_value = benchmark.get("cache_maintenance")
        cache = cache_value if isinstance(cache_value, dict) else {}
        sequence_value = cache.get("sequence", [])
        cache_sequence = sequence_value if isinstance(sequence_value, list) else []
        if (
            cache.get("function") == "portable_init"
            and cache.get("timing") == "before_each_upstream_invocation_and_outside_timed_workload"
            and "flush_cpu_dcache" in cache_sequence
            and "flush_cpu_icache" in cache_sequence
        ):
            cache_note = (
                " Before every upstream invocation, `portable_init` fences and flushes the CPU data and instruction caches "
                "outside the timed workload, so the calibration pass does not warm the scored pass."
            )
        else:
            cache_note = " Cache maintenance before the upstream invocations is not verified by build metadata."
        if isinstance(count, int) and isinstance(target, int) and isinstance(formula, str):
            return (
                f"Each scored trial first runs a separate {count}-iteration calibration workload and derives the scored "
                f"iteration count with `{formula}` for a {target}-second target; calibration time is excluded from the score. "
                + (
                    "The scored pass invokes upstream CoreMark again and reinitializes its static algorithm data while "
                    "retaining the same firmware, profile, and memory placement."
                    if calibration.get("scored_pass_reinitializes_static_algorithm_data") is True
                    else "The scored pass invokes upstream CoreMark again; build metadata does not verify static data reinitialization."
                )
                + f"{cache_note} Timer and CRC output follows the scored workload."
            )
        return (
            "Build metadata identifies fixed-iteration calibration followed by a fresh upstream CoreMark invocation, "
            f"but its calibration parameters are incomplete; calibration time is excluded from the score.{cache_note}"
        )

    images = benchmark.get("images") or {}
    flags = []
    for mode in ("performance", "validation"):
        commands = images.get(mode, {}).get("commands", {})
        for command in commands.values():
            if isinstance(command, dict):
                flags.extend(command.get("flags", []))
    if "-DITERATIONS=0" in flags:
        return (
            "CoreMark automatic iteration calibration executes the workload before the scored pass, so it warms the same "
            "caches. Calibration time is excluded from the score; timer and CRC output follows the timed workload."
        )
    return "Build metadata does not verify the iteration-calibration method or parameters."


def runtime_preflight_text(benchmark):
    preflight = benchmark.get("runtime_preflight") or {}
    if not isinstance(preflight, dict):
        return "Build metadata does not describe runtime SRAM and seed checks."
    probe = preflight.get("ram_probe") or {}
    seeds = preflight.get("runtime_seed_checks") or {}
    if not isinstance(probe, dict) or not isinstance(seeds, dict):
        return "Build metadata does not describe runtime SRAM and seed checks."
    size = probe.get("bytes")
    alignment = probe.get("alignment_bytes")
    storage = probe.get("storage")
    inputs = seeds.get("inputs")
    timing = preflight.get("timing")
    if not all((size, alignment, storage, isinstance(inputs, list), timing)):
        return "Build metadata contains incomplete runtime preflight details."
    phases = probe.get("phases")
    checks = "; ".join(phases) if isinstance(phases, list) else "the documented RAM access checks"
    image = preflight.get("image_integrity_rechecks")
    image_baseline = (
        "records the initial linked-image CRC32 and size, "
        if isinstance(image, dict) else ""
    )
    text = (
        f"Before timed CoreMark work, firmware {image_baseline}checks a {size}-byte, {alignment}-byte-aligned {storage} probe "
        f"({checks}) and validates volatile inputs `{', '.join(inputs)}` against the selected mode and iteration count. "
        "RAM and seed checks must pass before an upstream invocation."
    )
    if isinstance(image, dict) and image.get("timing") == "after_each_upstream_invocation_and_outside_timed_workload":
        phases = image.get("phases", [])
        if phases == ["calibration", "scored"]:
            text += (
                " After each calibration and scored timed interval, firmware computes the linked-image CRC32 through cached "
                "reads, flushes the caches, then checks the CRC32 and size again against the initial image values. "
                "These integrity checks are outside timing; a mismatch halts the run."
            )
        else:
            text += " Firmware rechecks linked-image integrity after each timed interval outside timing."
    else:
        text += " Build metadata does not verify linked-image integrity rechecks after timed intervals."
    optimization = preflight.get("wrapper_optimization")
    if isinstance(optimization, str) and optimization:
        text += f" {optimization}."
    return text


def reproduction_port(sessions, current_session, profile):
    """Use the latest recorded explicit Dock UART path for the reproduction command."""
    candidates = [current_session, *reversed(sessions)]
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("profile") != profile:
            continue
        identity = candidate.get("identity") or {}
        requested = identity.get("uart_device_requested")
        selected = identity.get("uart_device_selected")
        for port in (requested, selected):
            if isinstance(port, str) and port:
                return port
        trials = candidate.get("trials", [])
        if not isinstance(trials, list):
            continue
        for trial in reversed(trials):
            port = trial.get("uart_device")
            if isinstance(port, str) and port:
                return port
    return None


def reproduction_command(profile, port, memory="onchip"):
    port_text = shlex.quote(port) if port else "/dev/serial/by-id/<verified-Dock-UART>"
    memory_arg = "" if memory == "onchip" else " MEMORY=ddr3"
    return f"make benchmark-run PROFILE={profile} PORT={port_text}{memory_arg}"


def verified_trial_uart_text(trial):
    raw_path = repo_file(trial.get("raw_uart_log"))
    if (
        not raw_path or not raw_path.is_file()
        or not file_matches(trial.get("raw_uart_log"), trial.get("raw_uart_sha256"))
        or trial.get("raw_uart_bytes") != raw_path.stat().st_size
    ):
        return None
    return raw_path, raw_path.read_bytes().decode(errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def diagnostic_uart_lines(text):
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith((
            "BENCHMARK_IMAGE_CRC32 ", "BENCHMARK_RAM_CHECK ",
            "BENCHMARK_IMAGE_RECHECK ", "BENCHMARK_PORT_ERROR ",
        )) or re.search(r"ERROR!\s+(?:list|matrix|state)\s+crc", stripped, re.IGNORECASE):
            lines.append(stripped)
    return lines


def host_image_match(session, trial, image_line):
    if not image_line:
        return None
    fields = dict(re.findall(r"([A-Za-z_][A-Za-z_0-9]*)=([^\s]+)", image_line))
    identity = session.get("identity") or {}
    firmware = (identity.get("firmware") or {}).get(trial.get("mode"), {})
    image_path = repo_file(firmware.get("path"))
    if not image_path or not image_path.is_file():
        return None
    image = image_path.read_bytes()
    actual_crc = zlib.crc32(image) & 0xffffffff
    actual_sha = sha256(image_path)
    try:
        reported_build_id = fields["build_id"]
        reported_crc = int(fields["value"], 16)
        reported_bytes = int(fields["size"])
        recorded_crc = int(firmware["crc32"], 16)
        recorded_bytes = int(firmware["bytes"])
    except (KeyError, TypeError, ValueError):
        return None
    hash_matches = (
        actual_sha == firmware.get("sha256") == trial.get("firmware_sha256")
    )
    expected_build_id = str(identity.get("source_fingerprint") or "")[:16]
    if not hash_matches:
        return {
            "matches_host_artifact": None,
            "host_artifact_verified": False,
            "host_sha256": actual_sha,
            "reported_build_id": reported_build_id,
            "expected_build_id": expected_build_id or None,
        }
    matches = (
        (not expected_build_id or reported_build_id == expected_build_id)
        and actual_crc == recorded_crc == reported_crc
        and len(image) == recorded_bytes == reported_bytes
    )
    return {
        "matches_host_artifact": matches,
        "host_artifact_verified": True,
        "host_sha256": actual_sha,
        "reported_build_id": reported_build_id,
        "expected_build_id": expected_build_id or None,
        "host_crc32": f"{actual_crc:08x}",
        "host_bytes": len(image),
        "reported_crc32": f"{reported_crc:08x}",
        "reported_bytes": reported_bytes,
    }


def latest_observed_diagnostics(sessions, profile):
    """Return marker evidence only from the newest same-profile UART log with a verified hash."""
    for session in reversed(sessions):
        if not isinstance(session, dict) or session.get("profile") != profile:
            continue
        for trial in reversed(session.get("trials", [])):
            verified = verified_trial_uart_text(trial)
            if not verified:
                continue
            raw_path, text = verified
            lines = diagnostic_uart_lines(text)
            if not lines:
                continue
            image_line = next((line for line in lines if line.startswith("BENCHMARK_IMAGE_CRC32 ")), None)
            ram_line = next((line for line in lines if line.startswith("BENCHMARK_RAM_CHECK ")), None)
            return {
                "session_id": session.get("session_id"),
                "profile": profile,
                "mode": trial.get("mode"),
                "trial_status": trial.get("status"),
                "programming_status": trial.get("programming_status") or session.get("programming_status"),
                "raw_uart_log": trial.get("raw_uart_log"),
                "raw_uart_sha256": trial.get("raw_uart_sha256"),
                "raw_uart_hash_verified": True,
                "raw_uart_bytes": raw_path.stat().st_size,
                "lines": lines,
                "host_image": host_image_match(session, trial, image_line),
                "ram_check_line": ram_line,
            }
    return None


def historical_coremark_crc_failures(sessions, profile, exclude_session_id=None):
    failures = []
    for session in sessions:
        if (
            not isinstance(session, dict) or session.get("profile") != profile
            or session.get("session_id") == exclude_session_id
        ):
            continue
        for trial in session.get("trials", []):
            verified = verified_trial_uart_text(trial)
            if not verified:
                continue
            raw_path, text = verified
            lines = [line.strip() for line in text.splitlines()
                     if re.search(r"ERROR!\s+(?:list|matrix|state)\s+crc", line, re.IGNORECASE)]
            if lines:
                failures.append({
                    "session_id": session.get("session_id"),
                    "mode": trial.get("mode"),
                    "raw_uart_log": trial.get("raw_uart_log"),
                    "raw_uart_sha256": trial.get("raw_uart_sha256"),
                    "raw_uart_hash_verified": True,
                    "crc_error_labels": sorted(set(re.search(
                        r"ERROR!\s+(list|matrix|state)\s+crc", line, re.IGNORECASE
                    ).group(1).lower() for line in lines)),
                    "lines": list(dict.fromkeys(lines)),
                })
    return failures


BSRAM_ROOT_CAUSE = (
    "SRAM probe and CoreMark CRC failures captured before the read-first RAM change were traced to Gowin inferring "
    "LiteX's write-first byte-enable RAMs as write-through `SP` blocks with `WRE` tied high: on the GW2A-18C the first "
    "bus read of a word after a byte-enabled write returned wrong data on the written byte lanes, and the data cache "
    "kept that word. RTL and post-synthesis simulation of the same design read back correctly."
)


def diagnostics_text(observed, historical_failures, session):
    if not observed:
        return "No hash-verified hardware diagnostic UART capture is available."
    image = observed.get("host_image") or {}
    ram = observed.get("ram_check_line") or ""
    port_ok = observed.get("programming_status") == "completed"
    fields = dict(re.findall(r"([A-Za-z_][A-Za-z_0-9]*)=([^\s]+)", ram))
    if port_ok and image.get("matches_host_artifact") is True:
        image_text = (
            f"The firmware-reported image CRC32 `{image['reported_crc32']}` and size {image['reported_bytes']} bytes "
            "match the host-verified firmware binary."
        )
    elif image.get("host_artifact_verified") is True:
        image_text = "The captured image-integrity marker does not match the current host firmware artifact."
    elif image:
        image_text = "The captured image-integrity marker is available, but the current host binary no longer matches the captured firmware hash."
    else:
        image_text = "The captured image-integrity marker could not be checked against a host firmware artifact."

    if fields.get("status") == "failed":
        ram_text = (
            f"The runtime SRAM probe then failed at `{fields.get('phase', 'unknown phase')}`"
            + (f" at address `0x{fields['firstaddr']}`" if fields.get("firstaddr") else "")
            + (f", expected `0x{fields['expected']}` and read `0x{fields['actual']}`" if fields.get("expected") and fields.get("actual") else "")
            + (f" ({fields.get('errors')} errors, fail mask `0x{fields.get('failmask')}`)." if fields.get("errors") else ".")
        )
    elif fields.get("status") == "passed":
        ram_text = "The runtime SRAM probe passed."
    else:
        ram_text = "The raw capture does not contain a complete runtime SRAM result."

    if port_ok:
        opening = "FPGA SRAM programming and UART firmware upload completed. "
    else:
        opening = f"FPGA SRAM programming status was `{observed.get('programming_status', 'not recorded')}`. "
    if fields.get("status") == "failed":
        finding = (
            f"{opening}{image_text} {ram_text} CoreMark did not start "
            "and this validation attempt produced no benchmark score."
        )
    else:
        finding = f"{opening}{image_text} {ram_text}"

    performance = [
        trial for trial in (session or {}).get("trials", [])
        if trial.get("mode") == "performance"
    ]
    not_run = sum(trial.get("status") == "not_run" for trial in performance)
    if performance and not_run == len(performance):
        finding += f" All {not_run} scored performance repetitions were not run."

    historical_labels = sorted({
        label for failure in historical_failures for label in failure.get("crc_error_labels", [])
    })
    if historical_labels:
        finding += (
            " Earlier hash-verified CoreMark captures failed " + " and ".join(historical_labels)
            + " CRC validation; those failed captures remain in `results.json` and are excluded from score aggregates."
            + " " + BSRAM_ROOT_CAUSE
        )

    evidence = "\n".join(observed.get("lines", []))
    raw_link = repo_link(observed.get("raw_uart_log"), "verified raw UART capture")
    raw_note = (
        f"Raw UART evidence: {raw_link or observed.get('raw_uart_log')} (SHA256 "
        f"`{observed.get('raw_uart_sha256')}`, verified; {observed.get('raw_uart_bytes')} bytes)."
    )
    text = f"{finding} {raw_note}\n\n```text\n{evidence}\n```"
    if fields.get("status") == "failed":
        text += (
            "\n\nNext diagnostic: confirm the programmed bitstream was built with the read-first RAM ports in "
            "`gateware/soc.py` (read-before-write `SP` blocks in the Gowin netlist). " + BSRAM_ROOT_CAUSE
        )
    return text


def startup_recovery_text(session):
    recoveries = [
        trial.get("startup_recovery") for trial in (session or {}).get("trials", [])
        if isinstance(trial.get("startup_recovery"), dict)
    ]
    if not recoveries:
        return (
            "Each trial opens the selected UART before SRAM reprogramming and records received and transmitted bytes. "
            "The runner only attempts LiteX serialboot recovery after seeing the BIOS console before any benchmark start marker."
        )
    details = []
    for recovery in recoveries:
        command = json.dumps(recovery.get("console_command", "not recorded"))
        detail = (
            f"{recovery.get('status', 'recorded')} after `{recovery.get('trigger', 'startup recovery')}`; "
            f"port reopened: {recovery.get('port_reopened', 'unknown')}; "
            f"firmware upload: {recovery.get('firmware_upload_mode', 'unknown')}; "
            f"console command `{command}`; FPGA reprogrammed during recovery: {recovery.get('fpga_reprogrammed', 'unknown')}"
        )
        details.append(detail)
    return (
        "Captured startup recovery: " + "; ".join(details) + ". Raw receive and transmitted-byte logs span the initial upload and recovery on the explicit UART port."
    )


def trial_tables(session):
    if not session:
        return (
            "No board session is recorded. The validation and scored measurements are **not measured**.",
            "| Repetition | Iterations | Elapsed ticks | Seconds | CoreMark | CoreMark/MHz | Validation | UART evidence |\n|---|---:|---:|---:|---:|---:|---|---|\n"
            "| Validation seeds | — | — | — | — | — | not measured | — |\n"
            "| Performance 1 | — | — | — | — | — | not measured | — |\n"
            "| Performance 2 | — | — | — | — | — | not measured | — |\n"
            "| Performance 3 | — | — | — | — | — | not measured | — |",
        )
    validation = next((trial for trial in session.get("trials", []) if trial.get("mode") == "validation"), None)
    if validation is None:
        validation_text = "Validation run was not attempted."
    elif validation.get("reparsed"):
        parsed = validation["reparsed"]
        validation_text = (
            f"Passed for seeds `0x3415, 0x3415, 0x66`; seed CRC `0x{parsed['seedcrc']:04x}` and "
            f"list/matrix/state CRCs `0x{parsed['crcs']['crclist']:04x}`, `0x{parsed['crcs']['crcmatrix']:04x}`, "
            f"`0x{parsed['crcs']['crcstate']:04x}`. {log_link(validation, 'text_uart_log', 'UART log')}"
        )
    else:
        detail = validation.get("error") or "; ".join(validation.get("report_validation_errors", []))
        validation_text = f"**{effective_trial_status(validation)}**: {detail or 'no successful validation output'}"
    rows = [
        "| Repetition | Iterations | Elapsed ticks | Seconds | CoreMark | CoreMark/MHz | Validation | UART evidence |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    rows.append(
        f"| Validation seeds | — | — | — | — | — | {effective_trial_status(validation) if validation else 'not measured'} | "
        f"{log_link(validation or {}, 'text_uart_log', 'UART log')} |"
    )
    for trial in [trial for trial in session.get("trials", []) if trial.get("mode") == "performance"]:
        parsed = trial.get("reparsed", {}) or {}
        shown_status = effective_trial_status(trial)
        detail = trial.get("error") or "; ".join(trial.get("report_validation_errors", []))
        rows.append(
            f"| Performance {trial.get('attempt')} | {parsed.get('iterations', '—')} | "
            f"{parsed.get('elapsed_ticks', '—')} | {fmt_number(parsed.get('elapsed_seconds'), 6)} | "
            f"{fmt_number(parsed.get('coremark'), 6)} | {fmt_number(parsed.get('coremark_per_mhz'), 9)} | "
            f"{shown_status}" + (f" ({detail})" if shown_status != "passed" and detail else "") +
            f" | {log_link(trial, 'text_uart_log', 'UART log')} |"
        )
    present = {trial.get("attempt") for trial in session.get("trials", []) if trial.get("mode") == "performance"}
    for attempt in (1, 2, 3):
        if attempt not in present:
            rows.append(f"| Performance {attempt} | — | — | — | — | — | not run | — |")
    return validation_text, "\n".join(rows)


def aggregate_summary_text(summary):
    if not summary:
        return "No aggregate is reported until validation and all three scored repetitions pass the capture checks."
    return (
        f"Mean CoreMark **{summary['coremark_mean']:.6f}**, minimum **{summary['coremark_min']:.6f}**, "
        f"maximum **{summary['coremark_max']:.6f}**, and spread **{summary['coremark_spread']:.6f}**. "
        f"Mean CoreMark/MHz **{summary['coremark_per_mhz_mean']:.9f}**, range "
        f"**{summary['coremark_per_mhz_min']:.9f}–{summary['coremark_per_mhz_max']:.9f}**, spread "
        f"**{summary['coremark_per_mhz_spread']:.9f}**."
    )


def batch_profile_logs(session):
    links = []
    for trial in (session or {}).get("trials", []):
        label = f"{trial.get('mode')} {trial.get('attempt', '')}".strip()
        text_link = repo_link(trial.get("text_uart_log"), f"{label} UART log")
        if text_link:
            links.append(text_link)
        if file_matches(trial.get("raw_uart_log"), trial.get("raw_uart_sha256")):
            raw_link = repo_link(trial.get("raw_uart_log"), f"{label} raw UART bytes")
            if raw_link:
                links.append(raw_link)
        if file_matches(trial.get("transmitted_uart_log"), trial.get("transmitted_uart_sha256")):
            tx_link = repo_link(trial.get("transmitted_uart_log"), f"{label} transmitted UART bytes")
            if tx_link:
                links.append(tx_link)
    return ", ".join(links) if links else "No UART log files were retained for this profile session."


def batch_profile_section(profile, session, benchmark, diagnostics):
    if session is None:
        return (
            f"### `{profile}`\n\n"
            "No session was recorded for this profile in the current batch. Historical sessions are not used to fill this result."
        )
    identity = session.get("identity", {}) or {}
    validation_text, performance_table = trial_tables(session)
    complete = session.get("report_validation", {}).get("status") == "passed"
    summary = session.get("aggregate") if complete else None
    firmware = identity.get("firmware", {}) or {}
    firmware_lines = []
    for mode in ("validation", "performance"):
        image = firmware.get(mode, {}) or {}
        if image:
            firmware_lines.append(
                f"- {mode.title()} firmware: build `{str(identity.get('source_fingerprint', ''))[:16] or 'unknown'}`, "
                f"CRC32 `{image.get('crc32', 'unknown')}`, {image.get('bytes', 'unknown')} bytes, "
                f"SHA256 `{image.get('sha256', 'unknown')}`."
            )
    firmware_text = "\n".join(firmware_lines) or "- Firmware artifact identity was not recorded."
    flags = identity.get("compiler_flags_complete")
    if not flags:
        flags = benchmark.get("compiler_flags", [])
    report_validation = session.get("report_validation", {}) or {}
    validation_status = report_validation.get("status", "not revalidated")
    errors = report_validation.get("errors", [])
    errors_text = "" if not errors else " Revalidation errors: " + "; ".join(errors) + "."
    coremark = benchmark.get("coremark", {}) or {}
    return f"""### `{profile}`

- Session: `{session.get('session_id', 'unknown')}`; runner status `{session.get('status', 'unknown')}`; report validation `{validation_status}`; programming `{session.get('programming_status', 'unknown')}`.
- Session time: {session.get('created_utc', 'not recorded')}; source revision `{identity.get('repository_revision', 'not recorded')}`; fingerprint `{identity.get('source_fingerprint', 'not recorded')}`.
- CoreMark commit: `{coremark.get('commit', 'not recorded')}`; clock {session.get('clock_hz', 'unknown')} Hz; UART `{identity.get('uart_device_requested', identity.get('uart_device_selected', 'not recorded'))}`.
- Toolchain: `{identity.get('compiler_version', 'not recorded')}`; compiler flags for upstream algorithm sources:
  {compiler_flags_text(flags)}
{firmware_text}
- Cache configuration: `{json.dumps(identity.get('cache', benchmark.get('cache', 'not recorded')), sort_keys=True)}`. {uart_method_text(benchmark)}
- Startup and recovery: {startup_recovery_text(session)}
- Calibration and cache method: {calibration_method_text(benchmark)}
- Runtime preflight: {runtime_preflight_text(benchmark)}
- UART logs and hash-verified raw/transmitted byte captures: {batch_profile_logs(session)}{errors_text}

Validation: {validation_text}

{performance_table}

{aggregate_summary_text(summary)}

{diagnostics}
"""


def batch_interpretation_text(batch):
    requested = batch.get("requested_profiles", [])
    profile_results = batch.get("profile_results", {})
    passed = [
        profile for profile in requested
        if profile_results.get(profile, {}).get("status") == "passed"
    ]
    missing = batch.get("missing_profiles", [])
    failed = [profile for profile in requested if profile not in passed and profile not in missing]
    if batch.get("status") == "passed":
        return (
            f"The `PROFILE=ALL` batch `{batch.get('batch_id')}` completed validation and three accepted CoreMark repetitions "
            f"for every requested profile: {', '.join(f'`{profile}`' for profile in requested)}. Scores are reported independently "
            "per profile at the configured 48 MHz clock; each profile has its own build and runtime identity."
        )
    pieces = []
    if passed:
        pieces.append("Accepted measurements are available for " + ", ".join(f"`{profile}`" for profile in passed))
    if failed:
        pieces.append("validation or scored captures failed revalidation for " + ", ".join(f"`{profile}`" for profile in failed))
    if missing:
        pieces.append("no session was recorded for " + ", ".join(f"`{profile}`" for profile in missing))
    detail = "; ".join(pieces) if pieces else "no profile session was accepted"
    if not pieces and batch.get("errors"):
        detail = "batch metadata or profile membership failed validation"
    return (
        f"The `PROFILE=ALL` batch `{batch.get('batch_id')}` is {batch.get('status', 'incomplete')}: {detail}. "
        "Only independently revalidated profiles receive scores; missing or failed profiles have no inferred result. "
        "Failed and incomplete sessions remain in `results.json` and do not enter score aggregates."
    )


def session_memory_mode(session):
    identity = session.get("identity") or {}
    return session.get("memory_mode", identity.get("memory_mode", "onchip"))


def memory_comparison_data(current_memory, current_batch, current_session, previous=None):
    previous = previous if isinstance(previous, dict) else {}
    previous_modes = (previous.get("memory_mode_comparison") or {}).get("memory_modes", {})
    if not previous_modes:
        # Bootstrap the mode matrix from the most recently report-validated
        # summary already stored in results.json.
        prior_memory = (previous.get("latest_batch_summary") or {}).get("memory_mode", "onchip")
        prior_results = (previous.get("latest_batch_summary") or {}).get("profile_results", {})
        prior_aggregates = previous.get("latest_batch_aggregates", {})
        prior_builds = previous.get("build_profiles", {})
        for profile, aggregate_result in prior_aggregates.items():
            if profile not in PROFILES or prior_results.get(profile, {}).get("status") != "passed":
                continue
            previous_modes.setdefault(profile, {})[prior_memory] = {
                "status": "passed",
                "session_id": prior_results.get(profile, {}).get("session_id"),
                "aggregate": aggregate_result,
                "build_status": prior_builds.get(profile, {}).get("build_status", "previously_reported"),
                "resources": prior_builds.get(profile, {}).get("resources"),
            }
        latest_id = previous.get("latest_session_id")
        latest_status = previous.get("latest_session_status")
        if latest_id and latest_status == "passed" and previous.get("latest_session_aggregate"):
            latest_session = (previous.get("sessions") or [])[-1:]
            if latest_session:
                latest_profile = latest_session[0].get("profile")
                latest_mode = session_memory_mode(latest_session[0])
                if latest_profile in PROFILES:
                    previous_modes.setdefault(latest_profile, {})[latest_mode] = {
                        "status": "passed", "session_id": latest_id,
                        "aggregate": previous["latest_session_aggregate"],
                        "build_status": prior_builds.get(latest_profile, {}).get("build_status", "previously_reported"),
                        "resources": prior_builds.get(latest_profile, {}).get("resources"),
                    }
    rows_by_memory = {}
    for memory in MEMORY_MODES:
        build_root = ROOT / "build" if memory == "onchip" else ROOT / "build" / memory
        build_summary = read_json(build_root / "benchmark-build.json", {}) or {}
        rows_by_memory[memory] = profile_build_rows(build_summary, memory=memory)

    latest = {}
    for profile in PROFILES:
        latest[profile] = {}
        for memory in MEMORY_MODES:
            old = (previous_modes.get(profile, {}) or {}).get(memory, {})
            checked = None
            batch_requested = current_batch.get("requested_profiles", []) if current_batch else []
            if memory == current_memory and current_batch is not None and profile in batch_requested:
                checked = current_batch.get("sessions", {}).get(profile)
                if checked is None:
                    latest[profile][memory] = {
                        "status": "missing_current_batch_session", "session_id": None,
                        "aggregate": None,
                        "build_status": rows_by_memory[memory][profile]["build_status"],
                        "resources": rows_by_memory[memory][profile].get("resources"),
                    }
                    continue
            elif (memory == current_memory and current_session is not None
                  and current_session.get("profile") == profile):
                checked = current_session
            if checked is None and old:
                latest[profile][memory] = {
                    **old,
                    "build_status": rows_by_memory[memory][profile]["build_status"],
                    "resources": rows_by_memory[memory][profile].get("resources"),
                }
                continue
            if checked is None:
                latest[profile][memory] = {
                    "status": "not_measured", "session_id": None, "aggregate": None,
                    "build_status": rows_by_memory[memory][profile]["build_status"],
                    "resources": rows_by_memory[memory][profile].get("resources"),
                }
                continue
            valid = checked.get("report_validation", {}).get("status") == "passed"
            latest[profile][memory] = {
                "status": "passed" if valid else "failed_or_stale",
                "session_id": checked.get("session_id"),
                "aggregate": checked.get("aggregate") if valid else None,
                "errors": checked.get("report_validation", {}).get("errors", []),
                "build_status": rows_by_memory[memory][profile]["build_status"],
                "resources": rows_by_memory[memory][profile].get("resources"),
            }

    candidate_build = read_json(ROOT / "build/cpu-candidates/candidate-build.json")
    if candidate_build is None:
        candidate_build = read_json(ROOT / "docs/performance/cpu-evaluation/candidate-build.json", {}) or {}
    candidate_evaluation = read_json(ROOT / "build/cpu-candidates/evaluations.json")
    if candidate_evaluation is None:
        candidate_evaluation = read_json(ROOT / "docs/performance/cpu-evaluation/evaluations.json", {}) or {}
    return {
        "memory_modes": latest,
        "cpu_candidate_build_status": candidate_build.get("status", "not_run"),
        "cpu_candidate_results": {
            name: {
                "build_status": (candidate_build.get("candidates", {}).get(name) or {}).get("status", "not_run"),
                "reason": (candidate_build.get("candidates", {}).get(name) or {}).get("reason"),
                "evaluation_status": (candidate_evaluation.get("cases", {}).get(name) or {}).get("status", "not_run"),
                "coremark_mean": ((candidate_evaluation.get("cases", {}).get(name) or {}).get("aggregate") or {}).get("coremark_mean"),
                "resources_and_timing": (candidate_build.get("candidates", {}).get(name) or {}).get("resources_and_timing"),
            }
            for name in ("dynamic", "dynamic_target")
        },
        "cpu_winner": candidate_evaluation.get("winner"),
        "public_performance_profile_registered": "performance" in PROFILES,
        "ddr3_test_status": (read_json(ROOT / "docs/ddr3/results.json", {}) or {}).get("latest_batch_status", "not_measured"),
    }


def memory_comparison_table(comparison):
    rows = [
        "| CPU profile | On-chip CoreMark mean | On-chip LUT / BSRAM | DDR3 CoreMark mean | DDR3 LUT / BSRAM | DDR3 status |",
        "|---|---:|---:|---:|---:|---|",
    ]
    modes = comparison["memory_modes"]
    for profile in PROFILES:
        onchip = modes[profile]["onchip"]
        ddr3 = modes[profile]["ddr3"]
        onchip_score = (onchip.get("aggregate") or {}).get("coremark_mean")
        ddr_score = (ddr3.get("aggregate") or {}).get("coremark_mean")
        def resource_text(cell):
            resource = ((cell.get("resources") or {}).get("resources") or {})
            lut = resource.get("lut", {}).get("used")
            bsram = resource.get("bsram", {}).get("used")
            return "—" if lut is None or bsram is None else f"{lut} / {bsram}"
        rows.append(
            f"| `{profile}` | {fmt_number(onchip_score, 6)} | {resource_text(onchip)} | "
            f"{fmt_number(ddr_score, 6)} | {resource_text(ddr3)} | {ddr3['status']} |"
        )
    return "\n".join(rows)


def coremark_summary_text(comparison, generated_utc):
    """Render a compact summary from independently revalidated results/builds."""
    modes = comparison["memory_modes"]
    score_rows = [
        "| CPU profile | On-chip CoreMark mean ± spread (runs) | DDR3 CoreMark mean ± spread (runs) | DDR3 CoreMark status |",
        "|---|---:|---:|---|",
    ]
    build_rows = [
        "| CPU profile | Memory | Build | LUT | ALU | Registers | BSRAM | Operating clock | Worst setup slack | Estimated Fmax |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for profile in PROFILES:
        cells = modes[profile]
        profile_scores = []
        for memory in MEMORY_MODES:
            cell = cells[memory]
            aggregate_data = cell.get("aggregate") or {}
            if cell.get("status") == "passed" and aggregate_data.get("coremark_mean") is not None:
                profile_scores.append(
                    f"{aggregate_data['coremark_mean']:.6f} ± {aggregate_data.get('coremark_spread', 0.0):.6f} "
                    f"({aggregate_data.get('count', 0)})"
                )
            else:
                profile_scores.append("—")
            metrics = cell.get("resources") or {}
            resources = metrics.get("resources", {})
            timing = metrics.get("timing", {})
            value = lambda key: (resources.get(key) or {}).get("used")
            lut = value("lut")
            alu = value("alu")
            registers = value("registers")
            bsram = resources.get("bsram") or {}
            build_rows.append(
                f"| `{profile}` | {memory} | {cell.get('build_status', 'unavailable')} | "
                f"{lut if lut is not None else '—'} | {alu if alu is not None else '—'} | "
                f"{registers if registers is not None else '—'} | "
                f"{bsram.get('used', '—')} / {bsram.get('capacity', '—')} | "
                f"{fmt_number(timing.get('constraint_mhz'), 3)} MHz | "
                f"{fmt_number(timing.get('worst_setup_slack_ns'))} ns | "
                f"{fmt_number(timing.get('actual_fmax_mhz'))} MHz |"
            )
        score_rows.append(
            f"| `{profile}` | {profile_scores[0]} | {profile_scores[1]} | {cells['ddr3'].get('status', 'not_measured')} |"
        )

    winner = comparison.get("cpu_winner") or {}
    winner_name = winner.get("candidate", "dynamic_target")
    winner_gain = winner.get("gain_coremark", winner.get("gain", 0.0)) or 0.0
    baseline = winner.get("standard_mean")
    if baseline is None:
        selection = read_json(ROOT / "cpu-profile-selection.json", {}) or {}
        baseline = (selection.get("evaluation", {}).get("standard") or {}).get("coremark_mean")
    candidate_rows = [
        "| Candidate | Build and timing | Mean CoreMark (3 runs) | Difference vs fresh standard | Selection |",
        "|---|---|---:|---:|---|",
    ]
    for name, item in comparison.get("cpu_candidate_results", {}).items():
        mean = item.get("coremark_mean")
        delta = mean - baseline if mean is not None and baseline is not None else None
        percent = 100 * delta / baseline if delta is not None and baseline else None
        resources = ((item.get("resources_and_timing") or {}).get("resources") or {})
        timing = (item.get("resources_and_timing") or {}).get("timing") or {}
        build_text = item.get("build_status", "not_run")
        if resources.get("lut", {}).get("used") is not None:
            build_text += (
                f"; {resources['lut']['used']} LUT, "
                f"{(resources.get('bsram') or {}).get('used', '—')} BSRAM, "
                f"{fmt_number(timing.get('worst_setup_slack_ns'))} ns slack"
            )
        if name == winner_name and comparison.get("public_performance_profile_registered"):
            decision = "selected as public `performance`"
        elif item.get("evaluation_status") == "passed":
            decision = "viable; lower measured score"
        else:
            decision = item.get("evaluation_status", "not measured")
        gain_text = "—" if delta is None else f"{delta:.6f} ({percent:.2f}%)"
        candidate_rows.append(
            f"| `{name}` | {build_text} | {fmt_number(mean, 6)} | {gain_text} | {decision} |"
        )

    ddr = read_json(ROOT / "docs/ddr3/results.json", {}) or {}
    batches = ddr.get("batches", [])
    latest = batches[-1] if batches else {}
    requested_profiles = latest.get("requested_profiles", [])
    profiles = latest.get("profiles", {})
    training_summary = []
    evidence_links = []
    for profile in requested_profiles:
        entry = profiles.get(profile, {})
        session = entry.get("session") or {}
        checked = entry.get("revalidation") or {}
        successful = checked.get("training_runs_passed", 0)
        requested = session.get("requested_training_runs", 10)
        actual = session.get("actual_training_runs", 0)
        stress_requested = session.get("requested_stress_seconds", 1800)
        full = checked.get("full_result") or {}
        stress_actual = full.get("stress_seconds", full.get("measured_stress_seconds", 0))
        training_summary.append(
            f"`{profile}`: {successful}/{requested} accepted training/smoke runs "
            f"({actual} attempted), stress {stress_actual}/{stress_requested} s"
        )
        first_trial = next(iter(session.get("trials", [])), None)
        if first_trial:
            link = repo_link(first_trial.get("raw_uart_log"), f"{profile} acceptance UART capture")
            if link:
                evidence_links.append(link)
    if not training_summary:
        training_summary.append("No DDR3 board acceptance session was recorded.")
    ddr_detail = "\n- ".join(training_summary)
    ddr_reason = ""
    minimal_session = (profiles.get("minimal", {}).get("session") or {})
    if minimal_session.get("status") == "failed":
        trial = next(iter(minimal_session.get("trials", [])), {})
        trial_log = repo_file(trial.get("text_uart_log"))
        captured_failure = None
        if trial_log and trial_log.is_file():
            for line in trial_log.read_text(errors="replace").splitlines():
                if "SDRAM_READ_LEVELING_LANE " in line:
                    captured_failure = line.split("SDRAM_READ_LEVELING_LANE ", 1)[1].strip()
                    break
        if captured_failure:
            failure_detail = (
                "The same UART capture records `SDRAM_TRAINING_RESULT status=failed phase=leveling` and "
                f"`SDRAM_READ_LEVELING_LANE {captured_failure}`."
            )
        else:
            failure_detail = (
                "A separate BIOS probe captured read-leveling failure at lane DQ0 with "
                "`window_start=-1`, `window_length=0`."
            )
        ddr_reason = (
            f"The latest `{minimal_session.get('profile', 'minimal')}` run stopped before diagnostic firmware start: "
            f"{trial.get('error', 'training did not complete')}. {failure_detail} The runner issued no DDR memory "
            "test after this failure."
        )
    acceptance_evidence = ", ".join(evidence_links) if evidence_links else ""
    no_rejections = not any(
        item.get("evaluation_status") == "rejected" or item.get("build_status") in ("rejected", "rejected_or_blocked")
        for item in comparison.get("cpu_candidate_results", {}).values()
    )
    candidate_note = (
        "Both generated candidates passed build, timing, validation, and three scored runs; neither was rejected. "
        if no_rejections else "Rejected candidate reasons appear in the detailed report. "
    )
    links = [
        "[Detailed performance report](performance.md)",
        "[machine-readable benchmark results](performance/results.json)",
        "[DDR3 training and integrity report](ddr3/report.md)",
        "[CPU candidate selection](../cpu-profile-selection.json)",
        "[candidate build evidence](performance/cpu-evaluation/candidate-build.json)",
        "[candidate UART results](performance/cpu-evaluation/evaluations.json)",
    ]
    links.extend(repo_link(f"build/{profile}/gateware/impl/pnr/project.tr", f"on-chip `{profile}` timing") for profile in PROFILES)
    links.extend(repo_link(f"build/ddr3/{profile}/gateware/impl/pnr/project.tr", f"DDR3 `{profile}` timing") for profile in PROFILES)
    links = [item for item in links if item]
    return f"""# CoreMark and DDR3 handoff summary

Updated {generated_utc}. The CPU candidate selection and DDR3 acceptance are reported separately: on-chip CPU performance is measured and accepted, while DDR3 board acceptance is incomplete after read-leveling failure.

All eight profile/memory builds passed Gowin synthesis, placement, routing, timing, and resource checks. The configured operating clocks are 48 MHz system and, for DDR3, 96 MHz CK. Estimated Fmax values below come from place-and-route and are not the operating clock.

## CoreMark comparison

Each score is a validated mean ± observed spread from three scored repetitions. DDR3 CoreMark remains unmeasured because DDR training did not pass.

{chr(10).join(score_rows)}

## Build resources and timing

{chr(10).join(build_rows)}

## CPU candidate selection

At 48 MHz, `dynamic_target` is selected as the public `performance` profile. Its candidate-evaluation mean was {winner.get('coremark_mean', 0):.6f} CoreMark against a fresh standard mean of {baseline if baseline is not None else 0:.6f}, a {winner_gain:.6f} CoreMark ({100 * winner_gain / baseline if baseline else 0:.2f}%) increase. {candidate_note}The candidate table records both candidates' actual scores and timing results.

{chr(10).join(candidate_rows)}

The selected public profile was subsequently checked as a public build: fresh validation and three Dock runs passed, with mean 119.323916 CoreMark and zero observed spread. Its capture and image identities are in [the detailed report](performance.md).

## DDR3 training, integrity, and stress

DDR3 acceptance status: **{latest.get('status', ddr.get('latest_batch_status', 'not_measured'))}**. Training reliability:

- {ddr_detail}

{ddr_reason} Full-range coverage is not established (0 of 256 MiB accepted), cached/uncached visibility tests did not run, measured stress was 0/1800 seconds, and read/write bandwidth was not measured. Separate console probes are diagnostic evidence only and do not count as acceptance runs. {acceptance_evidence}

## Evidence

{', '.join(links)}
"""


def cpu_candidate_status_text(comparison):
    rows = [
        "| Prediction candidate | Build/synthesis status | LUT / BSRAM | Worst setup slack | Board measurement | Mean CoreMark |",
        "|---|---|---:|---:|---|---:|",
    ]
    for name, item in comparison["cpu_candidate_results"].items():
        timing = ((item.get("resources_and_timing") or {}).get("timing") or {})
        resources = ((item.get("resources_and_timing") or {}).get("resources") or {})
        lut = (resources.get("lut") or {}).get("used")
        bsram = (resources.get("bsram") or {}).get("used")
        resource_text = "—" if lut is None or bsram is None else f"{lut} / {bsram}"
        rows.append(
            f"| `{name}` | {item['build_status']} | {resource_text} | "
            f"{fmt_number(timing.get('worst_setup_slack_ns'))} ns | {item['evaluation_status']} | "
            f"{fmt_number(item.get('coremark_mean'), 6)} |"
        )
    winner = comparison.get("cpu_winner")
    if winner:
        gain_percent = (
            100.0 * winner["gain"] / winner["standard_mean"]
            if winner.get("gain") is not None and winner.get("standard_mean") else None
        )
        if comparison["public_performance_profile_registered"]:
            conclusion = (
                f"The measured winner `{winner.get('candidate')}` is registered as the public `performance` profile. "
                f"At 48 MHz it averaged {winner.get('coremark_mean'):.6f} CoreMark versus a fresh `standard` mean of "
                f"{winner.get('standard_mean'):.6f}, a {winner.get('gain'):.6f} CoreMark "
                f"({gain_percent:.2f}%) increase. The selection and evaluation identity are recorded in "
                "[cpu-profile-selection.json](../cpu-profile-selection.json)."
            )
        else:
            conclusion = (
                f"Candidate `{winner.get('candidate')}` measured {winner.get('coremark_mean'):.6f} CoreMark, "
                f"compared with a fresh standard mean of {winner.get('standard_mean'):.6f}; it meets the strict "
                "48 MHz selection threshold."
            )
    else:
        conclusion = "No public `performance` CPU profile is selected until candidate measurements beat a fresh standard mean."
        blocked = [
            f"`{name}`: {item['reason']}"
            for name, item in comparison["cpu_candidate_results"].items()
            if item.get("reason")
        ]
        if blocked:
            conclusion += " Candidate build status: " + "; ".join(blocked) + "."
    return "\n".join(rows) + "\n\n" + conclusion


def create_report():
    results_path = ROOT / "docs/performance/results.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    if results_path.exists():
        try:
            data = json.loads(results_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"cannot read results.json; existing history was left untouched: {error}") from error
    else:
        data = {"schema_version": 1, "sessions": []}
    if not isinstance(data, dict) or not isinstance(data.get("sessions", []), list):
        raise RuntimeError("results.json must contain a sessions array; existing history was left untouched")
    sessions = data.get("sessions", [])
    source_session = sessions[-1] if sessions else None
    selected_profile = source_session.get("profile", "standard") if isinstance(source_session, dict) else "standard"
    memory = session_memory_mode(source_session) if isinstance(source_session, dict) else "onchip"
    if memory not in MEMORY_MODES:
        memory = "onchip"
    table_profile = selected_profile if selected_profile in PROFILES else "standard"
    build_root = ROOT / "build" if memory == "onchip" else ROOT / "build" / memory
    build_summary = read_json(build_root / "benchmark-build.json", {}) or {}
    rows = profile_build_rows(build_summary, memory=memory)
    batch = None
    if isinstance(source_session, dict) and source_session.get("batch_id"):
        batch = revalidate_batch_sessions(
            sessions,
            source_session["batch_id"],
            source_session.get("requested_profiles"),
            rows,
        )
        session = batch.get("sessions", {}).get(table_profile)
        if session is None:
            session = revalidate_session(source_session, rows)
        batch_diagnostics = {}
        batch_history = {}
        batch_diagnostic_notes = []
        batch_profile_sections = []
        for profile in batch["requested_profiles"]:
            profile_session = batch.get("sessions", {}).get(profile)
            profile_observation = latest_observed_diagnostics(
                [profile_session] if profile_session else [], profile
            )
            profile_history = historical_coremark_crc_failures(
                sessions,
                profile,
                exclude_session_id=profile_observation.get("session_id") if profile_observation else None,
            )
            batch_diagnostics[profile] = profile_observation
            batch_history[profile] = profile_history
            if profile_session is None:
                note = f"No current-batch `{profile}` UART capture is available. Historical logs were not used as a substitute."
            else:
                note = diagnostics_text(profile_observation, profile_history, profile_session)
            batch_diagnostic_notes.append(f"### `{profile}`\n\n{note}")
            batch_profile_sections.append(
                batch_profile_section(
                    profile,
                    profile_session,
                    rows.get(profile, {}).get("benchmark_metadata") or {},
                    "See the hardware diagnostics section above for this profile.",
                )
            )
        observed_diagnostics = batch_diagnostics
        historical_crc_failures = batch_history
        diagnostics_note = "\n\n".join(batch_diagnostic_notes)
        validation_sections = "\n\n---\n\n".join(batch_profile_sections)
        hardware_status_by_profile = batch_hardware_status(batch)
    else:
        session = revalidate_session(source_session, rows)
        observed_diagnostics = latest_observed_diagnostics(sessions, table_profile)
        historical_crc_failures = historical_coremark_crc_failures(
            sessions, table_profile,
            exclude_session_id=observed_diagnostics.get("session_id") if observed_diagnostics else None,
        )
        diagnostics_note = diagnostics_text(observed_diagnostics, historical_crc_failures, session)
        startup_note = startup_recovery_text(session)
        validation_text, performance_table = trial_tables(session)
        validation_sections = f"{validation_text}\n\n{performance_table}\n\n{aggregate_summary_text((session or {}).get('aggregate') if session and session.get('report_validation', {}).get('status') == 'passed' else None)}"
        hardware_status_by_profile = None
    comparison = memory_comparison_data(memory, batch, session, data)
    perf_trials = [
        trial for trial in (session or {}).get("trials", [])
        if trial.get("mode") == "performance" and trial.get("reparsed")
    ]
    validation_ok = any(
        trial.get("mode") == "validation" and trial.get("reparsed")
        for trial in (session or {}).get("trials", [])
    )
    complete = (
        batch.get("status") == "passed"
        if batch else session is not None and session.get("report_validation", {}).get("status") == "passed"
    )
    programming_status = (session or {}).get("programming_status", "not attempted")

    selected_row = rows.get(table_profile, {})
    benchmark = selected_row.get("benchmark_metadata") or {}
    build = selected_row.get("build_metadata") or {}
    identity = (session or {}).get("identity", {}) or {}
    git = git_identity()
    session_date = (session or {}).get("created_utc", "not measured")
    uart_selected = identity.get("uart_device_selected") or "none selected"
    uart_requested = identity.get("uart_device_requested") or "none"
    command_port = reproduction_port(sessions, session, table_profile)
    benchmark_run_command = reproduction_command("ALL" if batch else table_profile, command_port, memory)
    coremark_commit = benchmark.get("coremark", {}).get("commit") or build_summary.get("coremark_commit", "not fetched")
    source_fingerprint = identity.get("source_fingerprint") or benchmark.get("source_fingerprint", "not available")
    flags = identity.get("compiler_flags_complete")
    if not flags:
        flags = benchmark.get("images", {}).get("performance", {}).get("commands", {}).get("core_list_join.c", {}).get("flags")
    if not flags:
        flags = benchmark.get("compiler_flags", [])
    flags_text = compiler_flags_text(flags)
    uart_note = uart_method_text(benchmark)
    calibration_note = calibration_method_text(benchmark)
    preflight_note = runtime_preflight_text(benchmark)
    if batch:
        startup_note = "Each profile has its own firmware upload and BIOS recovery record in the per-profile sections below."
    else:
        startup_note = startup_recovery_text(session)
    profile_metadata = benchmark.get("upstream_verification", {})
    upstream_note = profile_metadata.get("upstream_manifest_note", "CoreMark source checks have not run.")
    tools = identity.get("tool_versions", {}) or build.get("tool_versions", {})
    if not tools:
        tools = {
            "python": build.get("tool_versions", {}).get("python", sys.version.split()[0]),
            "riscv_gcc": identity.get("compiler_version", "not available"),
            "gowin": build.get("tool_versions", {}).get("gowin", "not available"),
            "openfpgaloader": build.get("tool_versions", {}).get("openfpgaloader", "not available"),
        }
    latest_profile_complete = session is not None and session.get("report_validation", {}).get("status") == "passed"
    summary = (session or {}).get("aggregate") if (latest_profile_complete if batch else complete) else None
    data["schema_version"] = 1
    data["latest_session_id"] = session.get("session_id") if session else None
    data["latest_memory_mode"] = memory
    data["report_generated_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    data["build_profiles"] = {
        profile: {
            "memory_mode": memory,
            "build_status": rows[profile]["build_status"],
            "resources": rows[profile].get("resources"),
            "performance_firmware_bytes": rows[profile].get("performance_firmware_bytes"),
            "validation_firmware_bytes": rows[profile].get("validation_firmware_bytes"),
            "source_fingerprint": rows[profile].get("benchmark_metadata", {}).get("source_fingerprint"),
            "bitstream_sha256": rows[profile].get("benchmark_metadata", {}).get("bitstream_sha256"),
            "firmware_sha256": {
                mode: image.get("binary_sha256")
                for mode, image in rows[profile].get("benchmark_metadata", {}).get("images", {}).items()
            },
            "compiler_flags": rows[profile].get("benchmark_metadata", {}).get("compiler_flags"),
            "cache": rows[profile].get("benchmark_metadata", {}).get("cache"),
            "iteration_calibration": rows[profile].get("benchmark_metadata", {}).get("iteration_calibration"),
            "cache_maintenance": rows[profile].get("benchmark_metadata", {}).get("cache_maintenance"),
            "runtime_preflight": rows[profile].get("benchmark_metadata", {}).get("runtime_preflight"),
            "memory": rows[profile].get("benchmark_metadata", {}).get("memory"),
            "hardware_status": (
                hardware_status_by_profile.get(profile, "not_measured")
                if hardware_status_by_profile is not None
                else (
                    "measured" if profile == table_profile and complete
                    else (session.get("status", "incomplete") if profile == table_profile and session else "not_measured")
                )
            ),
        }
        for profile in PROFILES
    }
    if session is not None:
        data["latest_session_aggregate"] = summary
        data["latest_session_status"] = session.get("report_validation", {}).get("status", "failed")
        data["latest_runner_status"] = session.get("status", "incomplete")
        data["latest_session_validation"] = session.get("report_validation", {})
        data["latest_session_methodology"] = {
            "iteration_calibration": benchmark.get("iteration_calibration"),
            "cache_maintenance": benchmark.get("cache_maintenance"),
            "runtime_preflight": benchmark.get("runtime_preflight"),
        }
    if batch:
        data["latest_batch_summary"] = {
            "batch_id": batch.get("batch_id"),
            "memory_mode": memory,
            "requested_profiles": batch.get("requested_profiles", []),
            "status": batch.get("status"),
            "missing_profiles": batch.get("missing_profiles", []),
            "errors": batch.get("errors", []),
            "profile_results": batch.get("profile_results", {}),
        }
        data["latest_batch_aggregates"] = {
            profile: result.get("aggregate") if result.get("status") == "passed" else None
            for profile, result in batch.get("profile_results", {}).items()
        }
    else:
        data["latest_batch_summary"] = None
        data["latest_batch_aggregates"] = {}
    data["latest_observed_diagnostics"] = observed_diagnostics
    data["historical_coremark_crc_failures"] = historical_crc_failures
    data["memory_mode_comparison"] = comparison
    results_path.write_text(json.dumps(data, indent=2) + "\n")

    version = tools.get("python", sys.version.split()[0])
    memory_placement_text = (
        "Code and read-only data in 256 MiB DDR3 main RAM at `0x40000000`; CoreMark data/BSS and a 2,048-byte reserved stack in 8 KiB SRAM at `0x10000000`; 8 KiB LiteDRAM L2."
        if memory == "ddr3" else
        "Code and read-only data in 32 KiB main RAM at `0x40000000`; CoreMark data/BSS in 8 KiB on-chip SRAM at `0x10000000`; a 2,048-byte stack is reserved."
    )
    report_status = (
        "complete" if complete
        else (f"INCOMPLETE — PROFILE=ALL batch {batch.get('status')}" if batch
              else "INCOMPLETE — board measurements are unavailable or failed")
    )
    if complete:
        hardware_status = "measured (3 valid repetitions)"
    elif batch:
        hardware_status = "see per-profile batch status"
    elif session and session.get("status") == "failed":
        hardware_status = "failed / incomplete"
    elif session:
        hardware_status = "incomplete"
    else:
        hardware_status = "not measured"
    time_rows = profile_table(rows, table_profile, hardware_status, hardware_status_by_profile)

    log_links = []
    evidence_sessions = (
        [batch.get("sessions", {}).get(profile) for profile in batch.get("requested_profiles", [])]
        if batch else [session]
    )
    for evidence_session in evidence_sessions:
        for trial in (evidence_session or {}).get("trials", []):
            label = f"{evidence_session.get('profile')} {trial.get('mode')} {trial.get('attempt', '')}".strip()
            text_link = repo_link(trial.get("text_uart_log"), f"{label} UART log")
            if text_link:
                log_links.append(text_link)
            if file_matches(trial.get("raw_uart_log"), trial.get("raw_uart_sha256")):
                raw_link = repo_link(trial.get("raw_uart_log"), f"{label} raw UART bytes")
                if raw_link:
                    log_links.append(raw_link)
            if file_matches(trial.get("transmitted_uart_log"), trial.get("transmitted_uart_sha256")):
                tx_link = repo_link(trial.get("transmitted_uart_log"), f"{label} transmitted UART bytes")
                if tx_link:
                    log_links.append(tx_link)
    links = ", ".join(log_links) if log_links else "No UART log files were retained for this session."
    build_session = build_summary.get("session_id")
    report_links = []
    for profile in PROFILES:
        evidence = (rows[profile].get("resources") or {}).get("evidence", {})
        for key, label in (
            ("pnr_resource_report", f"{profile} P&R resources"),
            ("timing_report", f"{profile} timing report"),
            ("synthesis_resource_report", f"{profile} synthesis resources"),
        ):
            link = repo_link(evidence.get(key), label)
            if link:
                report_links.append(link)
    build_artifacts = repo_link(f"build/benchmarks/{build_session}", f"build/benchmarks/{build_session}") if build_session else None
    measurement_identity_label = (
        f"batch `{batch.get('batch_id')}`; requested profiles: "
        + ", ".join(f"`{profile}`" for profile in batch.get("requested_profiles", []))
        if batch else f"{session_date}; selected profile: `{selected_profile}`"
    )
    validation_heading = "Per-profile validation and scored runs" if batch else "Validation and scored runs"
    reproduction_heading = (
        "Run validation plus three scored repetitions sequentially on all profiles:"
        if batch else "Run validation plus three scored repetitions on one selected profile:"
    )
    interpretation_note = (
        batch_interpretation_text(batch)
        if batch else interpretation_text(session, complete, summary)
    )
    interpretation_suffix = (
        "" if batch else " Profiles marked **not measured** have no inferred scores, and this report does not establish a ranking for them."
    )
    report = f"""# VexRiscv CoreMark performance

**Status: {report_status}.**

## Measurement identity

- Board: Sipeed Tang Primer 20K with standard Dock; device `GW2A-LV18PG256C8/I7`.
- Measurement: {measurement_identity_label}.
- SRAM programming status: **{programming_status}**; no flash programming is used.
- Benchmark memory mode: **{memory}**.
- Device discovery at session start: {len((session or {}).get('uart_candidates_at_start', []))} `/dev/serial/by-id/` paths and {len((session or {}).get('usb_device_nodes_at_start', []))} USB device nodes were visible.
- Configured SoC operating clock: **48,000,000 Hz**; UART selected: `{uart_selected}`; requested device: `{uart_requested}` at 115200 baud.
- Repository revision: `{identity.get('repository_revision', git['revision'])}`; dirty at capture: `{identity.get('repository_dirty', git['dirty'])}`; source fingerprint: `{source_fingerprint}`.
- CoreMark upstream commit: `{coremark_commit}`. {upstream_note}
- Tool versions: CPython {version}; RISC-V GCC `{identity.get('compiler_version', build.get('tool_versions', {}).get('riscv_gcc', 'not available'))}`; Gowin `{tools.get('gowin', 'not available')}`; openFPGALoader `{tools.get('openfpgaloader', 'not available')}`.
- Compiler flags for the upstream algorithm sources:
  {flags_text}
- Memory placement: {memory_placement_text} The 2,000-byte CoreMark data area is statically allocated.
- Generated cache configuration: `{identity.get('cache', benchmark.get('cache', 'not available'))}`. {uart_note}
- Trial startup and recovery: {startup_note} Each trial reprograms the design into FPGA SRAM and uploads fresh firmware through the LiteX BIOS serial loader; reconfiguration resets the design, and no reset is issued during a run. {calibration_note}
- Runtime SRAM and seed preflight: {preflight_note}

## Build and profile comparison

{time_rows}

GCC's largest per-function static frame is shown as a stack sizing check, not a runtime high-water mark; firmware reserves 2,048 stack bytes. The SRAM table reports the larger of the validation and performance image extents: linked `_end - _fdata` (data+BSS plus alignment padding), remaining space `_stack_bottom - _end`, and the reserved stack from `_stack_bottom` to `_stack_top`. Synthesis and place-and-route estimated Fmax values are implementation estimates. Any CoreMark score reported here uses the **48 MHz operating clock**; estimated Fmax is not the operating frequency.

## Hardware diagnostics

{diagnostics_note}

## CPU candidate and memory-mode comparison

On-chip and DDR3 CoreMark results are revalidated separately. Legacy sessions without a memory field are treated as on-chip.

{memory_comparison_table(comparison)}

### Standard-derived CPU candidates

{cpu_candidate_status_text(comparison)}

### DDR3 training and integrity

DDR3 acceptance status: **{comparison['ddr3_test_status']}**. Training logs, full-range memory coverage, cached/uncached visibility, and sustained stress evidence are summarized in [the DDR3 report](ddr3/report.md).

## {validation_heading}

{validation_sections}

Raw elapsed timer counters are preserved as 64-bit values in `results.json`; the UART port emits the high and low 32-bit words separately. Scores are recalculated on the host as `iterations × clock_hz / elapsed_ticks` and `iterations × 1,000,000 / elapsed_ticks`. The compact bare-metal build disables CoreMark's optional floating-point text; upstream integer iterations/second and whole-second duration are cross-checked, while report scores use full-resolution timer ticks. Failed or incomplete captures do not enter aggregates.

## Evidence and reproduction

- [Machine-readable results and artifact hashes](performance/results.json)
- [Measured CPU profile selection](../cpu-profile-selection.json), [candidate P&R evidence](performance/cpu-evaluation/candidate-build.json), and [candidate board sessions](performance/cpu-evaluation/evaluations.json)
- {links}
- Gowin reports: {', '.join(report_links) if report_links else 'no current build reports are available.'}
- Build/session artifacts: {build_artifacts or 'No benchmark build session is recorded.'}
- Pinned dependency and preserved license: [dependencies.lock.json](../dependencies.lock.json), [CoreMark license](../firmware/benchmark/LICENSE.md).

Rebuild all profiles and both firmware variants:

```sh
make doctor
make benchmark-build
```

{reproduction_heading}

```sh
{benchmark_run_command}
make benchmark-report
```

## Interpretation and limits

{interpretation_note}{interpretation_suffix}
"""
    (ROOT / "docs/performance.md").write_text(report)
    (ROOT / "docs/coremark-summary.md").write_text(
        coremark_summary_text(comparison, data["report_generated_utc"])
    )
    print(f"Wrote docs/performance.md ({report_status})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(create_report())
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        print(f"benchmark report failed: {error}", file=sys.stderr)
        sys.exit(1)
