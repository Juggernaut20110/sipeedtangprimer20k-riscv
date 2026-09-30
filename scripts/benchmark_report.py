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

from gateware.soc import PROFILES, SYS_CLK_FREQ  # noqa: E402
import compare as compare_module  # noqa: E402
from benchmark_build import sha256, stable_hash  # noqa: E402
from benchmark_results import CaptureValidationError, aggregate, parse_capture  # noqa: E402


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


def benchmark_fingerprint_matches(metadata):
    source_hashes = metadata.get("source_hashes", {})
    if not isinstance(source_hashes, dict) or not source_hashes or not all(
        file_matches(path, digest) for path, digest in source_hashes.items()
    ):
        return False
    coremark = metadata.get("coremark", {})
    fingerprint_values = {
        "profile": metadata.get("profile"),
        "coremark_commit": coremark.get("commit"),
        "source_hashes": source_hashes,
        "bitstream_sha256": metadata.get("bitstream_sha256"),
        "compiler": metadata.get("compiler"),
        "compiler_version": metadata.get("compiler_version"),
        "compiler_flags": metadata.get("compiler_flags"),
        "include_flags": metadata.get("include_flags"),
        "system_clock_hz": metadata.get("clock_hz"),
    }
    return stable_hash(fingerprint_values) == metadata.get("source_fingerprint")


def git_identity():
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    return {
        "revision": revision.stdout.strip() if revision.returncode == 0 else "unavailable",
        "dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
    }


def profile_build_rows(build_summary):
    rows = {}
    profiles = build_summary.get("profiles", {}) if isinstance(build_summary, dict) else {}
    for profile in PROFILES:
        entry = profiles.get(profile, {})
        build = read_json(ROOT / "build" / profile / "build-metadata.json") or entry.get("build") or {}
        benchmark = read_json(ROOT / "build" / profile / "benchmark/benchmark-metadata.json") or entry.get("benchmark_firmware") or {}
        try:
            # Current vendor reports are the source for resources/timing; cached
            # JSON can describe a different build than the files now on disk.
            resources = compare_module.parse_profile(profile)
        except (OSError, RuntimeError, ValueError, AttributeError, KeyError):
            resources = None
        firmware = benchmark.get("images", {})
        bitstream_path = build.get("bitstream") or benchmark.get("bitstream")
        build_valid = (
            build.get("status") == "passed"
            and build.get("profile") == profile
            and build.get("sys_clk_hz") == SYS_CLK_FREQ
            and benchmark.get("status") == "passed"
            and benchmark.get("profile") == profile
            and benchmark.get("clock_hz") == SYS_CLK_FREQ
            and benchmark_fingerprint_matches(benchmark)
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


def profile_table(rows, selected_profile, hardware_status):
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
        if profile == selected_profile:
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
    if profile not in PROFILES:
        errors.append(f"unknown session profile {profile!r}")
    if session.get("clock_hz") != SYS_CLK_FREQ or identity.get("clock_hz") != SYS_CLK_FREQ:
        errors.append(f"session clock metadata does not match {SYS_CLK_FREQ} Hz")
    if identity.get("profile") != profile:
        errors.append("session identity profile does not match the selected profile")

    selected = rows.get(profile, {}) if profile in PROFILES else {}
    benchmark = selected.get("benchmark_metadata") or {}
    build = selected.get("build_metadata") or {}
    build_identity_valid = (
        selected.get("build_status") == "passed"
        and identity.get("source_fingerprint") == benchmark.get("source_fingerprint")
        and (identity.get("bitstream") or {}).get("sha256") == benchmark.get("bitstream_sha256")
        and (identity.get("firmware") or {}).get("performance", {}).get("sha256")
        == benchmark.get("images", {}).get("performance", {}).get("binary_sha256")
        and (identity.get("firmware") or {}).get("validation", {}).get("sha256")
        == benchmark.get("images", {}).get("validation", {}).get("binary_sha256")
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


def effective_trial_status(trial):
    if trial.get("reparsed"):
        return "passed"
    if trial.get("status") == "passed":
        return "failed (capture evidence rejected)"
    return trial.get("status", "not_run")


def interpretation_text(session, complete, summary):
    if not session:
        return (
            "No board session is recorded, so hardware performance remains **not measured**. "
            "The benchmark covers this workload in the current on-chip-memory SoC; it does not measure HDMI, Ethernet, or external-memory performance. "
            "Place-and-route estimated Fmax is separate from the configured 48 MHz operating clock."
        )
    if complete:
        return (
            f"The `{session.get('profile')}` profile completed validation and three accepted CoreMark repetitions at 48 MHz "
            f"(mean {summary['coremark_mean']:.6f} CoreMark, {summary['coremark_per_mhz_mean']:.9f} CoreMark/MHz). "
            "This covers CoreMark in the current on-chip-memory SoC; it does not measure HDMI, Ethernet, or external-memory performance. "
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
        "The intended workload covers the current on-chip-memory SoC; it does not measure HDMI, Ethernet, or external-memory performance. "
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


def reproduction_command(profile, port):
    port_text = shlex.quote(port) if port else "/dev/serial/by-id/<verified-Dock-UART>"
    return f"make benchmark-run PROFILE={profile} PORT={port_text}"


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
            f"{opening}{image_text} {ram_text} CoreMark did not start, the exact hardware/cache root cause remains unresolved, "
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
        )

    evidence = "\n".join(observed.get("lines", []))
    raw_link = repo_link(observed.get("raw_uart_log"), "verified raw UART capture")
    raw_note = (
        f"Raw UART evidence: {raw_link or observed.get('raw_uart_log')} (SHA256 "
        f"`{observed.get('raw_uart_sha256')}`, verified; {observed.get('raw_uart_bytes')} bytes)."
    )
    next_step = (
        "Next diagnostic: insert a fence and D-cache flush immediately after the conflicting SRAM store and before the "
        "matching-index linked-image read, then repeat the same probe to isolate write visibility from cache-index conflict."
    )
    return f"{finding} {raw_note}\n\n```text\n{evidence}\n```\n\n{next_step}"


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
    table_profile = selected_profile if selected_profile in PROFILES else "standard"
    build_summary = read_json(ROOT / "build/benchmark-build.json", {}) or {}
    rows = profile_build_rows(build_summary)
    session = revalidate_session(source_session, rows)
    observed_diagnostics = latest_observed_diagnostics(sessions, table_profile)
    historical_crc_failures = historical_coremark_crc_failures(
        sessions, table_profile,
        exclude_session_id=observed_diagnostics.get("session_id") if observed_diagnostics else None,
    )
    perf_trials = [
        trial for trial in (session or {}).get("trials", [])
        if trial.get("mode") == "performance" and trial.get("reparsed")
    ]
    validation_ok = any(
        trial.get("mode") == "validation" and trial.get("reparsed")
        for trial in (session or {}).get("trials", [])
    )
    complete = session is not None and session.get("report_validation", {}).get("status") == "passed"
    validation_text, performance_table = trial_tables(session)
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
    benchmark_run_command = reproduction_command(table_profile, command_port)
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
    diagnostics_note = diagnostics_text(observed_diagnostics, historical_crc_failures, session)
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
    summary = (session or {}).get("aggregate") if complete else None
    data["schema_version"] = 1
    data["latest_session_id"] = session.get("session_id") if session else None
    data["report_generated_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    data["build_profiles"] = {
        profile: {
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
                "measured" if profile == table_profile and complete
                else (session.get("status", "incomplete") if profile == table_profile and session else "not_measured")
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
    data["latest_observed_diagnostics"] = observed_diagnostics
    data["historical_coremark_crc_failures"] = historical_crc_failures
    results_path.write_text(json.dumps(data, indent=2) + "\n")

    version = tools.get("python", sys.version.split()[0])
    report_status = "complete" if complete else "INCOMPLETE — board measurements are unavailable or failed"
    if complete:
        hardware_status = "measured (3 valid repetitions)"
    elif session and session.get("status") == "failed":
        hardware_status = "failed / incomplete"
    elif session:
        hardware_status = "incomplete"
    else:
        hardware_status = "not measured"
    time_rows = profile_table(rows, table_profile, hardware_status)
    if summary:
        aggregate_text = (
            f"Mean CoreMark **{summary['coremark_mean']:.6f}**, minimum **{summary['coremark_min']:.6f}**, "
            f"maximum **{summary['coremark_max']:.6f}**, and spread **{summary['coremark_spread']:.6f}**. "
            f"Mean CoreMark/MHz **{summary['coremark_per_mhz_mean']:.9f}**, range "
            f"**{summary['coremark_per_mhz_min']:.9f}–{summary['coremark_per_mhz_max']:.9f}**, spread "
            f"**{summary['coremark_per_mhz_spread']:.9f}**."
        )
    else:
        aggregate_text = "No aggregate is reported until validation and all three scored repetitions pass the capture checks."

    log_links = []
    for trial in (session or {}).get("trials", []):
        label = f"{trial.get('mode')} {trial.get('attempt', '')}".strip()
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
    report = f"""# VexRiscv CoreMark performance

**Status: {report_status}.**

## Measurement identity

- Board: Sipeed Tang Primer 20K with standard Dock; device `GW2A-LV18PG256C8/I7`.
- Measurement session: {session_date}; selected profile: `{selected_profile}`.
- SRAM programming status: **{programming_status}**; no flash programming is used.
- Device discovery at session start: {len((session or {}).get('uart_candidates_at_start', []))} `/dev/serial/by-id/` paths and {len((session or {}).get('usb_device_nodes_at_start', []))} USB device nodes were visible.
- Configured SoC operating clock: **48,000,000 Hz**; UART selected: `{uart_selected}`; requested device: `{uart_requested}` at 115200 baud.
- Repository revision: `{identity.get('repository_revision', git['revision'])}`; dirty at capture: `{identity.get('repository_dirty', git['dirty'])}`; source fingerprint: `{source_fingerprint}`.
- CoreMark upstream commit: `{coremark_commit}`. {upstream_note}
- Tool versions: CPython {version}; RISC-V GCC `{identity.get('compiler_version', build.get('tool_versions', {}).get('riscv_gcc', 'not available'))}`; Gowin `{tools.get('gowin', 'not available')}`; openFPGALoader `{tools.get('openfpgaloader', 'not available')}`.
- Compiler flags for the upstream algorithm sources:
  {flags_text}
- Memory placement: code and read-only data in 32 KiB main RAM at `0x40000000`; static benchmark data and BSS in 8 KiB on-chip SRAM at `0x10000000`; a 2,048-byte stack is reserved. The 2,000-byte CoreMark data area is statically allocated.
- Generated cache configuration: `{identity.get('cache', benchmark.get('cache', 'not available'))}`. {uart_note}
- Trial startup and recovery: {startup_note} Each trial reprograms the design into FPGA SRAM and uploads fresh firmware through the LiteX BIOS serial loader; reconfiguration resets the design, and no reset is issued during a run. {calibration_note}
- Runtime SRAM and seed preflight: {preflight_note}

## Build and profile comparison

{time_rows}

GCC's largest per-function static frame is shown as a stack sizing check, not a runtime high-water mark; firmware reserves 2,048 stack bytes. The SRAM table reports the larger of the validation and performance image extents: linked `_end - _fdata` (data+BSS plus alignment padding), remaining space `_stack_bottom - _end`, and the reserved stack from `_stack_bottom` to `_stack_top`. Synthesis and place-and-route estimated Fmax values are implementation estimates. Any CoreMark score reported here uses the **48 MHz operating clock**; estimated Fmax is not the operating frequency.

## Hardware diagnostics

{diagnostics_note}

## Validation and scored runs

{validation_text}

{performance_table}

{aggregate_text}

Raw elapsed timer counters are preserved as 64-bit values in `results.json`; the UART port emits the high and low 32-bit words separately. Scores are recalculated on the host as `iterations × clock_hz / elapsed_ticks` and `iterations × 1,000,000 / elapsed_ticks`. The compact bare-metal build disables CoreMark's optional floating-point text; upstream integer iterations/second and whole-second duration are cross-checked, while report scores use full-resolution timer ticks. Failed or incomplete captures do not enter aggregates.

## Evidence and reproduction

- [Machine-readable results and artifact hashes](performance/results.json)
- {links}
- Gowin reports: {', '.join(report_links) if report_links else 'no current build reports are available.'}
- Build/session artifacts: {build_artifacts or 'No benchmark build session is recorded.'}
- Pinned dependency and preserved license: [dependencies.lock.json](../dependencies.lock.json), [CoreMark license](../firmware/benchmark/LICENSE.md).

Rebuild all profiles and both firmware variants:

```sh
make doctor
make benchmark-build
```

Run validation plus three scored repetitions on one selected profile:

```sh
{benchmark_run_command}
make benchmark-report
```

## Interpretation and limits

{interpretation_text(session, complete, summary)} Profiles marked **not measured** have no inferred scores, and this report does not establish a ranking for them.
"""
    (ROOT / "docs/performance.md").write_text(report)
    print(f"Wrote docs/performance.md ({report_status})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(create_report())
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        print(f"benchmark report failed: {error}", file=sys.stderr)
        sys.exit(1)
