#!/usr/bin/env python3
"""Revalidate DDR3 UART captures and render training and memory-test evidence."""

import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_run as serial_runner  # noqa: E402
import compare as compare_module  # noqa: E402
import ddr_test_run as ddr_runner  # noqa: E402
from gateware.soc import DDR_SIZE_BYTES, PROFILES  # noqa: E402
from memory import profile_build_dir  # noqa: E402


def raw_capture_lines(path):
    raw = Path(path).read_bytes()
    decoded = ddr_runner.ANSI_ESCAPE.sub(b"", raw).decode("utf-8", errors="replace")
    return raw, decoded.splitlines()


def validate_trial(profile, trial, session, expected_lanes, phy_config):
    errors = []
    if trial.get("diagnostic_only"):
        errors.append("diagnostic-only trial cannot satisfy DDR acceptance")
    raw_path = ROOT / trial.get("raw_uart_log", "")
    tx_path = ROOT / trial.get("transmitted_uart_log", "")
    if not raw_path.is_file():
        return {"status": "failed", "errors": [f"missing raw UART capture: {trial.get('raw_uart_log')}"]}
    if not tx_path.is_file():
        errors.append(f"missing transmitted UART capture: {trial.get('transmitted_uart_log')}")
    else:
        if serial_runner.sha256(tx_path) != trial.get("transmitted_uart_sha256"):
            errors.append("transmitted UART SHA-256 does not match the session manifest")
    raw, lines = raw_capture_lines(raw_path)
    if serial_runner.sha256(raw_path) != trial.get("raw_uart_sha256"):
        errors.append("raw UART SHA-256 does not match the session manifest")
    build_identity = session.get("build_identity", {})
    image_name = "smoke" if trial.get("mode") == "training" else "full"
    expected_image = (build_identity.get("diagnostic_images", {}) or {}).get(image_name, {})
    if trial.get("image_sha256") != expected_image.get("sha256"):
        errors.append("trial firmware SHA-256 does not match the session build identity")
    if trial.get("bitstream_sha256") != build_identity.get("bitstream_sha256"):
        errors.append("trial bitstream SHA-256 does not match the session build identity")
    image_path = expected_image.get("binary")
    if image_path:
        image_file = ROOT / image_path
        if not image_file.is_file() or serial_runner.sha256(image_file) != expected_image.get("sha256"):
            errors.append("diagnostic firmware artifact is missing or has changed")
    build_path = build_identity.get("bitstream")
    if build_path:
        bitstream_file = ROOT / build_path
        if not bitstream_file.is_file() or serial_runner.sha256(bitstream_file) != build_identity.get("bitstream_sha256"):
            errors.append("DDR3 bitstream artifact is missing or has changed")
    training = ddr_runner.validate_training(lines, expected_lanes, phy_config)
    if training["status"] != "passed":
        errors.extend(training["errors"])
    bios_memtest = ddr_runner.validate_bios_memtest(lines)
    errors.extend(bios_memtest["errors"])
    if not any(line.startswith("DDR_TEST_START ") for line in lines):
        errors.append("diagnostic firmware start marker is missing")
    failure = next((line for line in lines if line.startswith("DDR_TEST_FAILURE ")), None)
    if failure:
        errors.append(failure)

    if trial.get("mode") == "training":
        smoke = [line for line in lines if line.startswith("DDR_TEST_SMOKE ")]
        ends = [line for line in lines if line.startswith("DDR_TEST_END ")]
        if len(smoke) != 1 or ddr_runner.split_words(smoke[0]).get("status") != "passed":
            errors.append("training capture lacks a passing DDR_TEST_SMOKE marker")
        if len(ends) != 1 or ddr_runner.split_words(ends[0]).get("status") != "passed":
            errors.append("training smoke capture lacks a passing DDR_TEST_END marker")
    elif trial.get("mode") == "full":
        try:
            result = ddr_runner.validate_full_capture(
                lines, profile, session.get("requested_stress_seconds", -1)
            )
        except (ValueError, KeyError, TypeError) as error:
            errors.append(f"full memory/stress validation failed: {error}")
            result = None
    else:
        errors.append(f"unknown DDR trial mode {trial.get('mode')!r}")
        result = None

    return {
        "status": "passed" if not errors and trial.get("status") == "passed" else "failed",
        "errors": errors,
        "raw_uart_sha256": serial_runner.sha256(raw_path),
        "raw_uart_bytes": len(raw),
        "training": training,
        "bios_memtest": bios_memtest,
        "full_result": result if trial.get("mode") == "full" else None,
    }


def validate_session(path):
    session = json.loads(Path(path).read_text())
    profile = session.get("profile")
    errors = []
    if not isinstance(profile, str) or profile not in PROFILES:
        return {"session": session, "status": "failed", "errors": [f"unknown profile {profile!r}"]}
    if session.get("memory_mode") != "ddr3":
        errors.append("session identity does not select DDR3")
    if session.get("build_identity", {}).get("geometry_bytes") != DDR_SIZE_BYTES:
        errors.append("session geometry does not match the fitted 128 MiB part")
    if session.get("batch_id") != session.get("session_id", "").rsplit("-", 1)[0]:
        # Profile names contain no hyphens; this catches an altered profile/batch link.
        errors.append("session ID and batch ID are inconsistent")
    memory_dir = profile_build_dir(ROOT, profile, "ddr3")
    try:
        expected_lanes, phy_config = ddr_runner.expected_read_lanes(memory_dir)
    except (OSError, RuntimeError) as error:
        errors.append(f"cannot load generated PHY training configuration: {error}")
        expected_lanes, phy_config = 0, {}
    checked_trials = []
    for trial in session.get("trials", []):
        checked = validate_trial(profile, trial, session, expected_lanes, phy_config)
        checked_trials.append({
            "mode": trial.get("mode"), "attempt": trial.get("attempt"),
            "status": checked["status"], "errors": checked["errors"],
            "raw_uart_sha256": checked.get("raw_uart_sha256"),
            "training_lane_count": (checked.get("training") or {}).get("lane_count"),
            "full_result": checked.get("full_result"),
        })
    training_trials = [item for item in checked_trials if item["mode"] == "training"]
    full_trials = [item for item in checked_trials if item["mode"] == "full"]
    requested = session.get("requested_training_runs", 0)
    if len(training_trials) != requested:
        errors.append(f"recorded {len(training_trials)} training trials; requested {requested}")
    if len([item for item in training_trials if item["status"] == "passed"]) != requested:
        errors.append("one or more fresh SRAM reconfiguration/training/smoke trials failed revalidation")
    if len(full_trials) != 1 or full_trials[0]["status"] != "passed":
        errors.append("full destructive memory and sustained stress evidence did not pass revalidation")
    if session.get("status") == "passed" and (
        requested < ddr_runner.THOROUGH_TRAINING_RUNS
        or session.get("requested_stress_seconds", 0) < ddr_runner.THOROUGH_STRESS_SECONDS
    ):
        errors.append("session is marked passed despite partial thorough-acceptance parameters")
    complete = not errors and session.get("status") in ("passed", "partial")
    return {
        "session": session,
        "status": "passed" if complete and session.get("status") == "passed" else "partial" if complete else "failed",
        "errors": errors,
        "trials": checked_trials,
        "training_runs_passed": sum(item["status"] == "passed" for item in training_trials),
        "training_runs_requested": requested,
        "expected_read_lanes": expected_lanes,
        "full_result": full_trials[0].get("full_result") if full_trials else None,
    }


def latest_sessions():
    paths = sorted((ROOT / "build/ddr3/ddr-test-sessions").glob("*/session.json"))
    batches = {}
    for path in paths:
        try:
            session = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        batch_id = session.get("batch_id")
        if batch_id:
            batches.setdefault(batch_id, []).append(path)
    return paths, batches


def diagnostic_probes():
    probes = []
    evidence_root = (ROOT / "docs/ddr3/evidence").resolve()
    for pattern in (
        "*/startup-probe.json", "*/bios-csr-probe.json",
        "*/latency-diagnostic.json", "*/latency-calibration.json",
    ):
        for path in sorted((ROOT / "docs/ddr3/evidence").glob(pattern)):
            try:
                probe = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            errors = []
            for field, hash_field in (
                ("raw_uart", "raw_uart_sha256"),
                ("transmitted_uart", "transmitted_uart_sha256"),
            ):
                relative = probe.get(field)
                expected_hash = probe.get(hash_field)
                if relative is None:
                    continue
                artifact = (ROOT / relative).resolve()
                if not artifact.is_relative_to(evidence_root) or not artifact.is_file():
                    errors.append(f"{field} is missing or outside the DDR evidence directory")
                elif serial_runner.sha256(artifact) != expected_hash:
                    errors.append(f"{field} SHA-256 does not match the probe manifest")
            probe["capture_validation"] = "passed" if not errors else "failed"
            probe["capture_validation_errors"] = errors
            probe["manifest"] = str(path.relative_to(ROOT))
            probes.append(probe)
    return sorted(probes, key=lambda item: item.get("captured_utc", ""))


def read_resource_data():
    rows = {}
    for profile in PROFILES:
        try:
            rows[profile] = compare_module.parse_profile(profile, memory="ddr3")
        except (OSError, RuntimeError, ValueError, KeyError, AttributeError) as error:
            rows[profile] = {"status": "unavailable", "error": str(error)}
    return rows


def create_report():
    paths, batches = latest_sessions()
    latest_batch = max(batches, default=None)
    results_path = ROOT / "docs/ddr3/results.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    historical = []
    if results_path.is_file():
        try:
            existing = json.loads(results_path.read_text())
            historical = existing.get("batches", []) if isinstance(existing, dict) else []
        except (OSError, json.JSONDecodeError):
            historical = []

    checked_batches = []
    for batch_id in sorted(batches):
        profiles = {}
        batch_errors = []
        requested_profiles = None
        for path in batches[batch_id]:
            checked = validate_session(path)
            profile = checked["session"].get("profile")
            membership = checked["session"].get("requested_profiles")
            if requested_profiles is None:
                requested_profiles = membership
            elif membership != requested_profiles:
                batch_errors.append(f"session {path} records inconsistent requested profile membership")
            if profile in profiles:
                batch_errors.append(f"duplicate session for profile {profile!r}")
            profiles[profile] = checked
        if not isinstance(requested_profiles, list) or not requested_profiles:
            batch_errors.append("batch has no recorded profile membership")
            requested_profiles = []
        if len(requested_profiles) != len(set(item for item in requested_profiles if isinstance(item, str))):
            batch_errors.append("batch profile membership contains duplicates")
        non_string = [profile for profile in requested_profiles if not isinstance(profile, str)]
        if non_string:
            batch_errors.append("batch profile membership contains non-string values")
        unknown = [profile for profile in requested_profiles if isinstance(profile, str) and profile not in PROFILES]
        if unknown:
            batch_errors.append("batch profile membership contains unknown profiles: " + ", ".join(map(str, unknown)))
        requested_profiles = [profile for profile in requested_profiles if profile in PROFILES]
        unexpected = [profile for profile in profiles if profile not in requested_profiles]
        if unexpected:
            batch_errors.append("sessions exist outside recorded profile membership: " + ", ".join(map(str, unexpected)))
        missing = [profile for profile in requested_profiles if profile not in profiles]
        if missing:
            batch_errors.append("missing profile sessions: " + ", ".join(missing))
        if batch_errors:
            batch_status = "incomplete"
        elif all(profiles[p]["status"] == "passed" for p in requested_profiles):
            batch_status = "passed"
        elif all(profiles[p]["status"] in ("passed", "partial") for p in requested_profiles):
            batch_status = "partial"
        else:
            batch_status = "failed"
        checked_batches.append({
            "batch_id": batch_id,
            "requested_profiles": requested_profiles,
            "status": batch_status,
            "missing_profiles": missing,
            "errors": batch_errors,
            "profiles": profiles,
        })

    if latest_batch is not None:
        latest = next(item for item in checked_batches if item["batch_id"] == latest_batch)
        if not any(item.get("batch_id") == latest_batch for item in historical):
            historical.append(latest)
        else:
            historical = [latest if item.get("batch_id") == latest_batch else item for item in historical]
    else:
        latest = None

    data = {
        "schema_version": 1,
        "report_generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "latest_batch_id": latest_batch,
        "latest_batch_status": latest.get("status") if latest else "not_measured",
        "batches": historical,
        "diagnostic_probes": diagnostic_probes(),
    }
    investigation_section = ""
    investigations = sorted((ROOT / "docs/ddr3/diagnosis").glob("*-integrity-investigation.json"))
    if investigations:
        from scripts.ddr_trace_audit import audit
        investigation_path = investigations[-1]
        investigation = json.loads(investigation_path.read_text())
        if investigation.get("acceptance_credit") is not False:
            raise ValueError("write-disturbance investigation must remain diagnostic-only")
        audited = [audit(ROOT / item["evidence"]) for item in investigation["audit"]]
        data["integrity_investigation"] = str(investigation_path.relative_to(ROOT))
        data["latest_diagnostic_id"] = Path(audited[-1]["evidence"]).name
        data["latest_diagnostic_status"] = investigation["status"]
        data["diagnostic_summary"] = {
            "status": "write_disturbance_reproduced_integrity_still_failed",
            "acceptance_credit": False, "victims": investigation["victims"],
            "report": investigation["report"], "evidence": data["integrity_investigation"],
            "audit": audited,
        }
        for item in audited:
            manifest = json.loads((ROOT / item["evidence"] / "manifest.json").read_text())
            data["diagnostic_probes"].append({
                "status": "diagnostic_only_write_disturbance_probe", "acceptance_credit": False,
                "experiment": manifest["experiment"], "suite": item["suite"],
                "manifest": item["evidence"] + "/manifest.json",
                "result": item["evidence"] + "/trace-probe.json", "audit": item["audit"],
                "bitstream_sha256": item["bitstream_sha256"],
                "profile": "minimal", "raw_uart": item["evidence"] + "/trace.uart.bin",
                "raw_uart_bytes": (ROOT / item["evidence"] / "trace.uart.bin").stat().st_size,
                "capture_validation": "passed",
            })
        recovery_path = ROOT / investigation["current_board_recovery"]
        recovery = json.loads(recovery_path.read_text())
        data["current_board_recovery"] = {
            "status": recovery["recovery_status"],
            "evidence": str(recovery_path.relative_to(ROOT)),
            "pre_reset_uart_rx_bytes": recovery["pre_reset_uart_rx_bytes"],
            "post_reset_uart_rx_bytes": recovery["post_reset_uart_rx_bytes"],
            "jtag_reset_exit_code": recovery["reset_exit_code"],
            "post_reset_jtag_detect_exit_code": recovery["post_reset_detect_exit_code"],
            "flash_programming": recovery["flash_programming"],
        }
        investigation_section = f"""## Write-disturbance investigation

The [retained investigation](write-disturbance.md) captured correct digital write inputs for the original BIOS error and reproduced bit-20 and bit-31 corruption at untouched victims after writes to another row. Conservative controller row timing and ODT-low did not resolve those failures. These historical probes used the former 256 MiB configuration and remain diagnostic-only; see the geometry correction below for current hardware results.

An independent raw-UART and artifact audit verified {sum(item['trace_count'] for item in audited)} snapshots and {sum(item['decoded_frame_count'] for item in audited)} frames. The [machine-readable investigation](diagnosis/{investigation_path.name}) links the exact images, captures, attempted fixes, limitations and post-investigation board recovery. DDR integrity remains failed; pin-level observations and comparison on another board are the next hardware discriminators.

"""
    corrections = sorted((ROOT / "docs/ddr3/diagnosis").glob("*-geometry-correction.json"))
    if corrections:
        from scripts.ddr_trace_audit import audit
        correction_path = corrections[-1]
        correction = json.loads(correction_path.read_text())
        if correction.get("acceptance_credit") is not False:
            raise ValueError("geometry retest must remain diagnostic-only")
        correction["audit"] = [audit(ROOT / item["evidence"]) for item in correction["audit"]]
        data["geometry_correction"] = correction
        data["latest_diagnostic_status"] = correction["status"]
        data["latest_diagnostic_id"] = Path(correction["audit"][-1]["evidence"]).name
        recovery_path = ROOT / correction["current_board_recovery"]
        recovery = json.loads(recovery_path.read_text())
        data["current_board_recovery"] = dict(recovery, evidence=str(recovery_path.relative_to(ROOT)))
        investigation_section += """## Fitted-part geometry correction

The fitted H5TQ1G63EFR is 128 MiB (13 row bits). Current builds and acceptance bounds have been corrected. The fresh 128 MiB design still fails BIOS Memtest and reproduces the bit-20 and bit-31 neighboring-row write errors. The bounded address probe is diagnostic evidence only. See [the correction and retained hardware results](hynix-geometry-correction.md). The board was recovered using a dedicated SRAM idle image that holds DDR in reset; the earlier combined JTAG detect/reset command does not prove reset occurred.

"""
    results_path.write_text(json.dumps(data, indent=2) + "\n")

    resources = read_resource_data()
    if latest is None:
        build_path = ROOT / "build/ddr3/ddr-test-build.json"
        try:
            diagnostic_build = json.loads(build_path.read_text())
        except (OSError, json.JSONDecodeError):
            diagnostic_build = {}
        build_profiles = diagnostic_build.get("profiles", {})
        build_rows = []
        for profile in PROFILES:
            resource = resources.get(profile, {})
            resource_data = resource.get("resources", {})
            timing = resource.get("timing", {})
            build = build_profiles.get(profile, {})
            images = build.get("images", {})
            fits = all(
                images.get(variant, {}).get("diagnostic_ram", {}).get("code_data_bss_stack_in_ram")
                for variant in ("smoke", "full")
            )
            if resource.get("status") == "unavailable":
                pnr_status = "unavailable"
                routed = f"unavailable ({resource.get('error')})"
            else:
                pnr_status = "passed"
                routed = (
                    f"LUT {resource_data.get('lut', {}).get('used')}; "
                    f"ALU {resource_data.get('alu', {}).get('used')}; "
                    f"registers {resource_data.get('registers', {}).get('used')}; "
                    f"BSRAM {resource_data.get('bsram', {}).get('used')}/"
                    f"{resource_data.get('bsram', {}).get('capacity')}"
                )
                routed += (
                    f"; Fmax {timing.get('actual_fmax_mhz')} MHz; "
                    f"setup/hold/recovery/removal slack "
                    f"{timing.get('worst_setup_slack_ns')}/"
                    f"{timing.get('worst_hold_slack_ns')}/"
                    f"{timing.get('worst_recovery_slack_ns')}/"
                    f"{timing.get('worst_removal_slack_ns')} ns"
                )
            image_status = "passed" if build.get("status") == "passed" and fits else "unavailable"
            build_rows.append(
                f"| `{profile}` | {pnr_status} | {routed} | "
                f"{image_status} |"
            )
        build_table = "\n".join(build_rows) if build_rows else "| — | unavailable | — | unavailable |"
        report = """# DDR3 training and integrity report

**Status: not measured.** No DDR3 board sessions are recorded.

The intended configuration is a 48 MHz system clock, 96 MHz DDR CK, and 128 MiB of DDR3 with an 8 KiB LiteDRAM L2. Local synthesis/place-and-route and diagnostic firmware build results are summarized below. They do not establish board training, full-range integrity, cache visibility, or stress performance.

## Local generated-design evidence

| Profile | P&R status | Resources and timing estimate | Diagnostic images |
|---|---|---|---|
""" + build_table + """

Each diagnostic image runs from the 16 KiB on-chip diagnostic RAM and reserves a 2 KiB stack. Routed timing is estimated for the constrained 48 MHz system clock; the DDR CK and system 2x clocks are 96 MHz. Timing and resource results are generated-design evidence only. Board runs still need fresh DDR training, full-range tests, cached/uncached visibility checks, and sustained stress captures.

Run `make ddr-test-build PROFILE=ALL`, then `make ddr-test-run PROFILE=ALL PORT=<verified UART>`, and regenerate this report with `make ddr-test-report`.
"""
        (ROOT / "docs/ddr3/report.md").write_text(report + "\n" + investigation_section)
        return data

    profile_sections = []
    probe_sections = []
    for probe in data["diagnostic_probes"]:
        diagnostic_detail = ""
        if probe.get("temporary_latency"):
            diagnostic_detail = (
                f" Temporary {probe['temporary_latency']} mode-register settings were tried "
                f"for diagnosis; acceptance remains {probe.get('required_acceptance_latency')} and this capture does not qualify."
            )
        raw_path = probe.get("raw_uart")
        raw_link = (
            f"[raw UART capture]({raw_path.removeprefix('docs/ddr3/')})"
            if raw_path else "no raw UART capture"
        )
        log_relative = probe.get("text_uart")
        if not log_relative and raw_path:
            log_relative = raw_path.removesuffix(".bin") + ".log"
        excerpt = ""
        if log_relative:
            log_path = (ROOT / log_relative).resolve()
            evidence_root = (ROOT / "docs/ddr3/evidence").resolve()
            if log_path.is_relative_to(evidence_root) and log_path.is_file():
                text = log_path.read_text(errors="replace")
                selected = [
                    line for line in text.splitlines()
                    if any(token in line for token in (
                        "SDRAM_TRAINING_RESULT", "SDRAM_READ_LEVELING_LANE",
                        "Memory initialization failed", "litex>", "Memory dump:",
                        "mem_read 0xf000", "0xf000101c", "0xf0001000", "0xf0002800",
                        "sdram_mr_write", "sdram_cal", "Writing 0x0a40", "Writing 0x0806",
                        "Writing 0x0210",
                    ))
                ]
                if selected:
                    excerpt = "\n\n```text\n" + "\n".join(selected) + "\n```"
        probe_sections.append(
            f"- `{probe.get('status', 'unknown')}` for `{probe.get('profile', 'unknown')}`; "
            f"{probe.get('raw_uart_bytes', probe.get('received_bytes', 0))} UART bytes; "
            f"capture hash validation: **{probe.get('capture_validation', 'unavailable')}**; "
            f"{raw_link}. Manifest: `{probe.get('manifest')}`.{diagnostic_detail}{excerpt}\n"
        )
    for profile in latest["requested_profiles"]:
        entry = latest["profiles"].get(profile)
        if not entry:
            profile_sections.append(f"### `{profile}`\n\nNo session was recorded for this profile.\n")
            continue
        session = entry["session"]
        full = entry.get("full_result") or {}
        bandwidth = full.get("bandwidth", {})
        trials = entry.get("trials", [])
        errors = entry.get("errors", [])
        evidence_links = []
        for trial in session.get("trials", []):
            raw_path = trial.get("raw_uart_log")
            if raw_path:
                evidence_links.append(f"[{trial.get('mode')} {trial.get('attempt')} UART]({raw_path.removeprefix('docs/ddr3/')})")
        resource = resources.get(profile, {})
        if resource.get("status") == "unavailable":
            resource_text = f"P&R data unavailable: {resource.get('error')}"
        else:
            r = resource.get("resources", {})
            timing = resource.get("timing", {})
            resource_text = (
                f"LUT {r.get('lut', {}).get('used')}, ALU {r.get('alu', {}).get('used')}, "
                f"registers {r.get('registers', {}).get('used')}, BSRAM {r.get('bsram', {}).get('used')}; "
                f"operating clock {timing.get('constraint_mhz')} MHz, estimated Fmax "
                f"{timing.get('actual_fmax_mhz')} MHz, worst setup slack {timing.get('worst_setup_slack_ns')} ns."
            )
        profile_sections.append(
            f"### `{profile}`\n\n"
            f"- Session status: **{entry['status']}**; training/smoke runs passed "
            f"{entry.get('training_runs_passed', 0)}/{entry.get('training_runs_requested', 0)}.\n"
            f"- Recorded DDR configuration: {session.get('build_identity', {}).get('geometry_bytes', 'unrecorded')} bytes; 48 MHz system, 96 MHz DDR CK; 8 KiB L2; {entry.get('expected_read_lanes')} trained read lanes per configuration (revalidated from generated PHY header).\n"
            f"- Stress: requested {session.get('requested_stress_seconds')} s; measured "
            f"{full.get('stress_seconds_actual', 'not measured')} s. Uncached alias bandwidth: "
            f"{bandwidth.get('read_bytes_per_second', 'not measured')} B/s read and "
            f"{bandwidth.get('write_bytes_per_second', 'not measured')} B/s write, "
            f"{bandwidth.get('transfer_bytes', 'not measured')}-byte transfers, {bandwidth.get('clock_hz', 'not measured')} Hz, "
            f"{bandwidth.get('elapsed_ticks', 'not measured')} timer ticks.\n"
            f"- Resources and timing: {resource_text}\n"
            f"- Evidence: {', '.join(evidence_links) if evidence_links else 'No UART evidence links were recorded.'}\n"
            f"- Revalidation errors: {', '.join(errors) if errors else 'none.'}\n"
        )
    report = f"""# DDR3 training and integrity report

**Latest batch status: {latest['status']}.**

The receive patch and current integrity investigation are recorded in [DDR3 training handoff](training-handoff.md), with the original experiments retained in [DDR3 failure diagnosis](diagnosis.md). Diagnostic experiments do not count as acceptance passes.

Batch `{latest_batch}` records its original configured geometry; older 256 MiB captures cannot qualify the fitted 128 MiB part. Current builds use 128 MiB at a 48 MHz system clock and 96 MHz DDR CK. Training success is based on captured BIOS status and all lane bitslip/delay-window records. Each counted training pass required a fresh SRAM reconfiguration and an uncached diagnostic smoke test. The full test executes from the 16 KiB diagnostic RAM. DDR3 CoreMark placement, when run, keeps code/read-only data in DDR and algorithm data/BSS/stack in SRAM.

## Per-profile evidence

{''.join(profile_sections)}
{investigation_section}
## Standalone diagnostic probes

These console probes were captured outside the acceptance runner and do not count as training or memory-test passes. They are retained to show the startup failure and subsequent read-only CSR observations.

{''.join(probe_sections) if probe_sections else 'No standalone diagnostic probes were recorded.'}

## Acceptance criteria

Thorough status requires at least {ddr_runner.THOROUGH_TRAINING_RUNS} successful reconfiguration/training/smoke runs and {ddr_runner.THOROUGH_STRESS_SECONDS} seconds of measured stress per profile, full 128 MiB coverage, passing uncached bypass tests, cached visibility checks that evict both CPU and LiteDRAM L2 caches, zero errors, and complete UART captures. Shorter runs are marked partial. These records describe SRAM reconfigurations, not power-cycle or cold-boot tests.

Machine-readable results and capture identities: [results.json](results.json).
"""
    (ROOT / "docs/ddr3/report.md").write_text(report)
    return data


def main():
    result = create_report()
    print(json.dumps({"latest_batch_id": result.get("latest_batch_id"), "status": result.get("latest_batch_status")}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"DDR report failed: {error}", file=sys.stderr)
        sys.exit(1)
