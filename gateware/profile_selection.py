"""Validation for the optional two-mode maxperf profile promotion record."""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELECTION_PATH = ROOT / "maxperf-profile-selection.json"
SYS_CLK_HZ = 48_000_000
DDR_BYTES = 256 * 1024 * 1024
DDR_STRESS_SECONDS = 1800
DDR_TRAINING_RUNS = 10
ANSI_ESCAPE = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_file(root, relative, label):
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{label} path is missing")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"{label} is missing or outside the repository")
    return path


def _hashed_json(root, reference, label):
    if not isinstance(reference, dict):
        raise ValueError(f"{label} reference is missing")
    path = _repo_file(root, reference.get("path"), label)
    if _digest(path) != reference.get("sha256"):
        raise ValueError(f"{label} hash does not match its record")
    try:
        return path, json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON: {error}") from error


def _verify_uart_capture(root, trial, label, profile, mode, parsed):
    for path_key, hash_key in (("raw_uart_log", "raw_uart_sha256"),
                               ("transmitted_uart_log", "transmitted_uart_sha256")):
        path = _repo_file(root, trial.get(path_key), f"{label} {path_key}")
        if _digest(path) != trial.get(hash_key):
            raise ValueError(f"{label} {path_key} hash does not match its trial record")
    raw = _repo_file(root, trial.get("raw_uart_log"), f"{label} raw UART").read_bytes()
    if trial.get("decode_errors_present") is not False or b"BENCHMARK_START " not in raw or b"BENCHMARK_END " not in raw:
        raise ValueError(f"{label} UART capture is incomplete or has decode errors")
    decoded = raw.decode("utf-8", errors="replace")
    start = (f"BENCHMARK_START profile={profile} build_id={parsed.get('build_id')} "
             f"mode={mode} clock_hz={SYS_CLK_HZ}")
    end = (f"BENCHMARK_END status=returned profile={profile} "
           f"build_id={parsed.get('build_id')}")
    if decoded.count(start) != 1 or decoded.count(end) != 1:
        raise ValueError(f"{label} UART start/end identity does not match the parsed trial")


def _passing_repetitions(root, case, label, expected_profile, expected_candidate, mode):
    if not isinstance(case, dict) or case.get("status") != "passed":
        raise ValueError(f"{label} did not pass")
    identity = case.get("identity", {})
    if (case.get("profile") != expected_profile
            or case.get("candidate_id") != expected_candidate
            or case.get("memory_mode") != mode
            or identity.get("profile") != expected_profile
            or identity.get("cpu_candidate") != expected_candidate
            or identity.get("memory_mode") != mode
            or identity.get("clock_hz") != SYS_CLK_HZ
            or case.get("clock_hz") != SYS_CLK_HZ):
        raise ValueError(f"{label} profile, candidate, memory, or clock identity does not match")
    bitstream = identity.get("bitstream", {})
    bitstream_path = _repo_file(root, bitstream.get("path"), f"{label} bitstream")
    if _digest(bitstream_path) != bitstream.get("sha256"):
        raise ValueError(f"{label} bitstream identity is missing")
    firmware = identity.get("firmware", {})
    if not isinstance(firmware.get("performance", {}).get("sha256"), str):
        raise ValueError(f"{label} firmware identity is missing")
    for image_mode in ("validation", "performance"):
        image = firmware.get(image_mode, {})
        image_path = _repo_file(root, image.get("path"), f"{label} {image_mode} firmware")
        if _digest(image_path) != image.get("sha256") or image_path.stat().st_size != image.get("bytes"):
            raise ValueError(f"{label} {image_mode} firmware hash or size changed")
    trials = case.get("trials", [])
    validation = [item for item in trials if item.get("mode") == "validation"]
    scored = [item for item in trials if item.get("mode") == "performance"]
    if (len(validation) != 1 or validation[0].get("status") != "passed"
            or len(scored) != 3 or any(item.get("status") != "passed" for item in scored)):
        raise ValueError(f"{label} lacks one passing validation and three passing scores")
    for trial in [*validation, *scored]:
        if (trial.get("programming_status") != "completed"
                or trial.get("firmware_sha256") != firmware.get(trial["mode"], {}).get("sha256")):
            raise ValueError(f"{label} contains a trial with a mismatched firmware or incomplete programming")
        parsed = trial.get("parsed", {})
        if (parsed.get("status") != "passed" or parsed.get("profile") != expected_profile
                or parsed.get("mode") != trial["mode"]
                or parsed.get("clock_hz") != SYS_CLK_HZ
                or not isinstance(parsed.get("elapsed_ticks"), int) or parsed["elapsed_ticks"] <= 0
                or not isinstance(parsed.get("iterations"), int) or parsed["iterations"] <= 0):
            raise ValueError(f"{label} contains a malformed parsed benchmark result")
        calculated = parsed["iterations"] * SYS_CLK_HZ / parsed["elapsed_ticks"]
        if parsed.get("coremark") != calculated:
            raise ValueError(f"{label} contains a score that does not match its timer ticks")
        if (parsed.get("image_crc32") != firmware[trial["mode"]].get("crc32")
                or parsed.get("image_bytes") != firmware[trial["mode"]].get("bytes")):
            raise ValueError(f"{label} benchmark image does not match its recorded firmware identity")
        _verify_uart_capture(root, trial, label, expected_profile, trial["mode"], parsed)

    aggregate = case.get("aggregate", {})
    mean = aggregate.get("coremark_mean")
    if aggregate.get("count") != 3 or not isinstance(mean, (int, float)) or mean <= 0:
        raise ValueError(f"{label} has no valid three-run aggregate")
    scored_mean = sum(item["parsed"]["coremark"] for item in scored) / 3
    if mean != scored_mean:
        raise ValueError(f"{label} aggregate mean does not match its full-precision trial scores")
    return float(mean)


def _configuration_matches(actual, expected):
    return isinstance(actual, dict) and all(actual.get(key) == value for key, value in expected.items())


def _record_words(line):
    return dict(part.split("=", 1) for part in line.split()[1:] if "=" in part)


def _validate_ddr_capture(root, qualification_session, trial):
    raw_path = _repo_file(root, trial.get("raw_uart_log"), "DDR3 raw UART capture")
    raw = raw_path.read_bytes()
    text = ANSI_ESCAPE.sub(b"", raw).decode("utf-8", errors="replace")
    if trial.get("decode_errors_present") is not False:
        raise ValueError("DDR3 qualification UART capture has decode errors")
    if (trial.get("training", {}).get("status") != "passed"
            or trial.get("bios_memtest", {}).get("status") != "passed"
            or "SDRAM_TRAINING_RESULT status=passed" not in text
            or "Memtest OK" not in text):
        raise ValueError("DDR3 qualification trial lacks passing BIOS training and Memtest records")
    expected_lanes = qualification_session.get("expected_read_lanes")
    lane_lines = [line for line in text.splitlines() if "SDRAM_READ_LEVELING_LANE " in line]
    lane_pattern = re.compile(
        r"SDRAM_READ_LEVELING_LANE module=\d+ dq=\d+ bitslip=\d+ status=passed "
        r"window_start=\d+ window_length=\d+(?: delay_center=\d+ delay_half_window=\d+)?"
    )
    if (not isinstance(expected_lanes, int) or len(lane_lines) != expected_lanes
            or any(not lane_pattern.search(line) for line in lane_lines)):
        raise ValueError("DDR3 training capture lacks valid delay-window evidence for every read lane")

    if trial.get("mode") == "training":
        if ("DDR_TEST_SMOKE status=passed" not in text
                or trial.get("smoke_test", {}).get("status") != "passed"):
            raise ValueError("DDR3 training trial lacks passing uncached smoke evidence")
        return
    if trial.get("mode") != "full":
        raise ValueError("DDR3 qualification session contains an unknown trial mode")

    starts = [line for line in text.splitlines() if line.startswith("DDR_TEST_START ")]
    ends = [line for line in text.splitlines() if line.startswith("DDR_TEST_END ")]
    stresses = [line for line in text.splitlines() if line.startswith("DDR_TEST_STRESS ")]
    if len(starts) != 1 or len(ends) != 1 or len(stresses) != 1:
        raise ValueError("DDR3 full capture has missing or duplicate identity, end, or stress records")
    start, end, stress = map(_record_words, (starts[0], ends[0], stresses[0]))
    if (start.get("profile") != qualification_session.get("profile")
            or start.get("memory") != "ddr3"
            or int(start.get("clock_hz", "-1")) != SYS_CLK_HZ
            or int(start.get("ddr_bytes", "-1")) != DDR_BYTES
            or int(start.get("l2_bytes", "-1")) != 8192
            or int(start.get("stress_seconds", "-1")) != DDR_STRESS_SECONDS
            or end.get("status") != "passed"
            or end.get("profile") != qualification_session.get("profile")
            or end.get("memory") != "ddr3"
            or int(end.get("errors", "-1")) != 0
            or int(end.get("tested_bytes", "-1")) != DDR_BYTES
            or int(stress.get("requested_seconds", "-1")) != DDR_STRESS_SECONDS):
        raise ValueError("DDR3 full capture identity, geometry, stress request, or end status is invalid")
    elapsed_ticks = (int(stress.get("elapsed_ticks_hi", "0"), 16) << 32)
    elapsed_ticks |= int(stress.get("elapsed_ticks_lo", "0"), 16)
    if elapsed_ticks < DDR_STRESS_SECONDS * SYS_CLK_HZ:
        raise ValueError("DDR3 raw stress timer is shorter than 1800 seconds")

    required_phases = {
        "walking_ones", "walking_zeros", "fixed_pattern", "inverted_pattern",
        "address_pattern", "deterministic_pseudorandom", "address_bank_row_column_alias",
        "byte_halfword_neighbor_preservation", "cached_uncached_visibility",
        "sustained_delayed_readback_stress",
    }
    phase_starts = {}
    phase_ends = {}
    for line in text.splitlines():
        if line.startswith("DDR_TEST_PHASE_START "):
            values = _record_words(line)
            phase_starts[values.get("name")] = values
        elif line.startswith("DDR_TEST_PHASE_END "):
            values = _record_words(line)
            phase_ends[values.get("name")] = values
    if set(phase_starts) != required_phases or set(phase_ends) != required_phases:
        raise ValueError("DDR3 raw capture does not contain every required full-range phase")
    for name in required_phases:
        started, finished = phase_starts[name], phase_ends[name]
        expected_coverage = (104 if name == "address_bank_row_column_alias"
                             else 512 if name == "cached_uncached_visibility" else DDR_BYTES)
        if (finished.get("status") != "passed" or int(finished.get("errors", "-1")) != 0
                or int(started.get("coverage_bytes", "-1")) != expected_coverage
                or int(started.get("range_end", "0"), 0) != 0xD0000000
                or (name != "cached_uncached_visibility"
                    and int(started.get("range_start", "0"), 0) != 0xC0000000)
                or (name == "cached_uncached_visibility"
                    and int(started.get("range_start", "0"), 0) != 0xC0100000)):
            raise ValueError(f"DDR3 phase {name} did not pass full-range coverage")

    result = trial.get("result", {})
    bandwidth = result.get("bandwidth", {})
    phases = result.get("phases", {})
    if (result.get("status") != "passed" or result.get("geometry_bytes") != DDR_BYTES
            or result.get("stress_seconds_actual", 0) < DDR_STRESS_SECONDS
            or set(phases) != required_phases
            or bandwidth.get("path") != "uncached_controller_alias"
            or bandwidth.get("clock_hz") != SYS_CLK_HZ
            or bandwidth.get("transfer_bytes") != 1024 * 1024
            or bandwidth.get("read_bytes", 0) <= 0 or bandwidth.get("write_bytes", 0) <= 0):
        raise ValueError("DDR3 parsed full-suite result lacks full coverage, stress, or bandwidth evidence")


def validate_mode_acceptance(root, mode, item):
    """Validate the generation, fresh measurements, and qualification for one mode."""
    if mode not in ("onchip", "ddr3") or not isinstance(item, dict):
        raise ValueError("unknown memory mode or malformed selection entry")
    if item.get("status") != "accepted" or item.get("clock_hz") != SYS_CLK_HZ:
        raise ValueError(f"{mode} selection is not accepted at 48 MHz")
    candidate_id = item.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError(f"{mode} candidate identifier is missing")

    _, manifest = _hashed_json(root, item.get("candidate_manifest"), f"{mode} candidate manifest")
    if manifest.get("candidate_id") != candidate_id or manifest.get("memory_mode") != mode:
        raise ValueError(f"{mode} candidate manifest identity does not match its selection")
    config = manifest.get("cpu_configuration", {})
    isa = config.get("isa")
    if (isa not in ("rv32i2p0_m", "rv32i2p0_mc")
            or config.get("prediction") != "dynamic_target"
            or config.get("clock_hz") != SYS_CLK_HZ
            or config.get("compressed") != isa.endswith("_mc")):
        raise ValueError(f"{mode} candidate manifest has an unsupported CPU configuration")
    rtl = _repo_file(root, manifest.get("rtl"), f"{mode} candidate RTL")
    if _digest(rtl) != manifest.get("rtl_sha256"):
        raise ValueError(f"{mode} candidate RTL hash does not match its manifest")

    _, evaluation = _hashed_json(root, item.get("evaluation_evidence"), f"{mode} evaluation")
    if (evaluation.get("status") != "accepted" or evaluation.get("memory_mode") != mode
            or evaluation.get("clock_hz") != SYS_CLK_HZ):
        raise ValueError(f"{mode} evaluation is not an accepted 48 MHz result")
    baseline_mean = evaluation.get("fresh_performance_baseline_mean")
    baseline = evaluation.get("baseline")
    measured_baseline = _passing_repetitions(
        root, baseline, f"{mode} fresh performance baseline", "performance",
        baseline.get("candidate_id") if isinstance(baseline, dict) else None, mode,
    )
    if (not isinstance(baseline_mean, (int, float)) or baseline_mean != measured_baseline
            or baseline.get("clock_hz") != SYS_CLK_HZ):
        raise ValueError(f"{mode} baseline mean is not tied to its four passing trials")
    if mode == "ddr3":
        preflight = evaluation.get("ddr_preflight", {})
        if (preflight.get("status") != "passed" or preflight.get("acceptance") != "thorough"
                or preflight.get("actual_training_runs", 0) < DDR_TRAINING_RUNS
                or preflight.get("stress_seconds", 0) < DDR_STRESS_SECONDS
                or preflight.get("full_range_bytes") != DDR_BYTES
                or preflight.get("error_count") != 0
                or preflight.get("uncached_smoke_passed") is not True):
            raise ValueError("DDR3 fresh performance baseline lacks full hardware qualification")
        _, baseline_session = _hashed_json(
            root, preflight.get("session_evidence"), "DDR3 performance-baseline qualification",
        )
        if (baseline_session.get("status") != "passed"
                or baseline_session.get("acceptance") != "thorough"
                or baseline_session.get("profile") != "performance"
                or baseline_session.get("memory_mode") != "ddr3"
                or baseline_session.get("build_identity", {}).get("bitstream_sha256")
                   != baseline.get("identity", {}).get("bitstream", {}).get("sha256")):
            raise ValueError("DDR3 preflight does not identify the freshly measured performance baseline")
        baseline_trials = baseline_session.get("trials", [])
        if (sum(1 for trial in baseline_trials if trial.get("mode") == "training") < DDR_TRAINING_RUNS
                or sum(1 for trial in baseline_trials if trial.get("mode") == "full") != 1):
            raise ValueError("DDR3 baseline preflight lacks ten training runs and one full-suite run")
        for trial in baseline_trials:
            if trial.get("status") != "passed":
                raise ValueError("DDR3 baseline preflight contains a failed trial")
            _validate_ddr_capture(root, baseline_session, trial)

    candidate_case = evaluation.get("candidates", {}).get(candidate_id)
    measured_candidate = _passing_repetitions(
        root, candidate_case, f"{mode} selected candidate", "standard", candidate_id, mode,
    )
    if measured_candidate <= baseline_mean:
        raise ValueError(f"{mode} selected candidate does not strictly beat the fresh baseline")
    candidate_identity = candidate_case.get("identity", {})
    candidate_rtl = _repo_file(root, candidate_identity.get("cpu_rtl"), f"{mode} measured candidate RTL")
    if (_digest(candidate_rtl) != manifest.get("rtl_sha256")
            or candidate_identity.get("cpu_configuration", {}).get("rtl_sha256") != manifest.get("rtl_sha256")
            or not _configuration_matches(candidate_identity.get("cpu_configuration"), config)):
        raise ValueError(f"{mode} candidate measurement does not use its generated CPU configuration")
    if evaluation.get("selected_candidate") != candidate_id:
        raise ValueError(f"{mode} evaluation selects a different candidate")

    verification = evaluation.get("winner_verification", {})
    verified_mean = _passing_repetitions(
        root, verification, f"{mode} maxperf identity verification", "maxperf", candidate_id, mode,
    )
    identity = verification.get("identity", {})
    if (verification.get("profile") != "maxperf"
            or verification.get("candidate_id") != candidate_id
            or not _configuration_matches(identity.get("cpu_configuration"), config)
            or identity.get("cpu_configuration", {}).get("rtl_sha256") != manifest.get("rtl_sha256")
            or identity.get("cpu_rtl") != manifest.get("rtl")
            or verified_mean <= baseline_mean):
        raise ValueError(f"{mode} public-identity verification does not match its selected CPU")

    qualification = evaluation.get("qualification", {})
    if qualification.get("status") != "passed":
        raise ValueError(f"{mode} hardware qualification did not pass")
    if mode == "ddr3" and (
        qualification.get("training_runs_passed", 0) < DDR_TRAINING_RUNS
        or qualification.get("stress_seconds", 0) < DDR_STRESS_SECONDS
        or qualification.get("full_range_bytes") != DDR_BYTES
        or qualification.get("error_count") != 0
        or qualification.get("uncached_smoke_passed") is not True
    ):
        raise ValueError("DDR3 selection lacks ten training runs, full coverage, clean smoke, and 1800s stress")
    if mode == "ddr3":
        _, qualification_session = _hashed_json(
            root, qualification.get("session_evidence"), "DDR3 qualification session",
        )
        if (qualification_session.get("status") != "passed"
                or qualification_session.get("acceptance") != "thorough"
                or qualification_session.get("memory_mode") != "ddr3"
                or qualification_session.get("cpu_candidate") != candidate_id
                or qualification_session.get("build_identity", {}).get("bitstream_sha256")
                   != candidate_identity.get("bitstream", {}).get("sha256")):
            raise ValueError("DDR3 qualification evidence does not match the measured candidate bitstream")
        trials = qualification_session.get("trials", [])
        smoke_trials = [trial for trial in trials if trial.get("mode") == "training"]
        full_trials = [trial for trial in trials if trial.get("mode") == "full"]
        if (len(smoke_trials) < DDR_TRAINING_RUNS or len(full_trials) != 1
                or qualification_session.get("stress_seconds", 0) < DDR_STRESS_SECONDS
                or qualification_session.get("full_range_bytes") != DDR_BYTES
                or qualification_session.get("error_count") != 0
                or qualification_session.get("uncached_smoke_passed") is not True):
            raise ValueError("DDR3 qualification session lacks ten training runs and full-range stress evidence")
        for trial in trials:
            if trial.get("status") != "passed":
                raise ValueError("DDR3 qualification session contains a failed or incomplete trial")
            if (trial.get("training", {}).get("status") != "passed"
                    or trial.get("bios_memtest", {}).get("status") != "passed"):
                raise ValueError("DDR3 qualification trial does not record passing BIOS training and Memtest")
            raw_path = _repo_file(root, trial.get("raw_uart_log"), "DDR3 raw UART capture")
            raw = raw_path.read_bytes()
            decoded = raw.decode("utf-8", errors="replace")
            if trial.get("decode_errors_present") is not False:
                raise ValueError("DDR3 qualification UART capture has decode errors")
            if "SDRAM_TRAINING_RESULT status=passed" not in decoded or "Memtest OK" not in decoded:
                raise ValueError("DDR3 qualification capture lacks passing BIOS training and Memtest records")
            if (trial.get("mode") == "training"
                    and ("DDR_TEST_SMOKE status=passed" not in decoded
                         or trial.get("smoke_test", {}).get("status") != "passed")):
                raise ValueError("DDR3 qualification training trial lacks passing uncached smoke evidence")
            if trial.get("mode") == "full":
                if "DDR_TEST_END status=passed" not in decoded or "DDR_TEST_STRESS " not in decoded:
                    raise ValueError("DDR3 qualification full trial lacks passing end and stress records")
                if f"DDR_TEST_START profile={qualification_session.get('profile')} memory=ddr3" not in decoded:
                    raise ValueError("DDR3 full-suite UART profile identity does not match its session")
                result = trial.get("result", {})
                phases = result.get("phases", {})
                if (result.get("status") != "passed"
                        or result.get("geometry_bytes") != DDR_BYTES
                        or result.get("stress_seconds_actual", 0) < DDR_STRESS_SECONDS
                        or set(phases) != {
                            "walking_ones", "walking_zeros", "fixed_pattern", "inverted_pattern",
                            "address_pattern", "deterministic_pseudorandom", "address_bank_row_column_alias",
                            "byte_halfword_neighbor_preservation", "cached_uncached_visibility",
                            "sustained_delayed_readback_stress",
                        }):
                    raise ValueError("DDR3 full-suite result lacks its complete range, phases, or measured stress")
            for capture_key in ("raw_uart_log", "transmitted_uart_log"):
                capture = _repo_file(root, trial.get(capture_key), f"DDR3 {capture_key}")
                hash_key = "raw_uart_sha256" if capture_key == "raw_uart_log" else "transmitted_uart_sha256"
                if _digest(capture) != trial.get(hash_key):
                    raise ValueError(f"DDR3 {capture_key} hash does not match its trial record")
    return True


def accepted_maxperf_selection(root=ROOT):
    """Return the selection only when both modes' source and hardware evidence validate."""
    root = Path(root).resolve()
    selection_path = (root / "maxperf-profile-selection.json"
                      if root != ROOT.resolve() else SELECTION_PATH)
    try:
        selection = json.loads(selection_path.read_text())
        if (selection.get("schema_version") != 1 or selection.get("profile") != "maxperf"
                or selection.get("status") != "accepted"
                or selection.get("clock_hz") != SYS_CLK_HZ):
            return None
        modes = selection.get("memory_modes", {})
        if set(modes) != {"onchip", "ddr3"}:
            return None
        for mode in ("onchip", "ddr3"):
            validate_mode_acceptance(root, mode, modes[mode])
        return selection
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
