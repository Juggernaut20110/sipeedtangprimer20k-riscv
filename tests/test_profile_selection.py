import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateware.profile_selection import accepted_maxperf_selection  # noqa: E402


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
    return {"path": relative, "sha256": digest(path)}


def write_benchmark_trial(root, directory, profile, mode, attempt, score, image, build_id):
    ticks = 48_000_000
    iterations = score
    parsed = {
        "status": "passed", "profile": profile, "build_id": build_id,
        "mode": mode, "clock_hz": 48_000_000,
        "elapsed_ticks": ticks, "iterations": iterations,
        "coremark": iterations * 48_000_000 / ticks,
        "image_crc32": image["crc32"], "image_bytes": image["bytes"],
    }
    start = (f"BENCHMARK_START profile={profile} build_id={build_id} mode={mode} "
             "clock_hz=48000000 data_size=2000 contexts=1")
    end = f"BENCHMARK_END status=returned profile={profile} build_id={build_id}"
    capture = root / f"{directory}-{attempt}-{mode}.uart.bin"
    capture.parent.mkdir(parents=True, exist_ok=True)
    capture.write_text(start + "\n" + end + "\n")
    transmitted = root / f"{directory}-{attempt}-{mode}.uart.tx.bin"
    transmitted.write_bytes(b"\nserialboot\n")
    return {
        "mode": mode, "attempt": attempt, "status": "passed",
        "programming_status": "completed", "firmware_sha256": image["sha256"],
        "raw_uart_log": str(capture.relative_to(root)), "raw_uart_sha256": digest(capture),
        "transmitted_uart_log": str(transmitted.relative_to(root)),
        "transmitted_uart_sha256": digest(transmitted), "decode_errors_present": False,
        "parsed": parsed,
    }


def passing_case(root, directory, mean, profile, candidate_id, memory, cpu_configuration=None,
                 cpu_rtl=None, bitstream_sha=None):
    bitstream = root / f"{directory}.fs"
    bitstream.write_bytes((directory + " bitstream").encode())
    images = {}
    for mode in ("validation", "performance"):
        binary = root / f"{directory}-{mode}.bin"
        binary.write_bytes((directory + mode).encode())
        images[mode] = {
            "path": str(binary.relative_to(root)), "sha256": digest(binary),
            "crc32": 1234 if mode == "validation" else 5678,
            "bytes": binary.stat().st_size,
        }
    if bitstream_sha is not None:
        # Allow the DDR qualification test to bind to this exact measured image.
        bitstream_sha = digest(bitstream)
    else:
        bitstream_sha = digest(bitstream)
    identity = {
        "profile": profile, "cpu_candidate": candidate_id,
        "memory_mode": memory, "clock_hz": 48_000_000,
        "cpu_configuration": cpu_configuration or {},
        "cpu_rtl": cpu_rtl,
        "bitstream": {"path": str(bitstream.relative_to(root)), "sha256": bitstream_sha},
        "firmware": images,
    }
    build_id = f"{directory}-build"
    trials = [write_benchmark_trial(root, directory, profile, "validation", 0, 100,
                                    images["validation"], build_id)]
    trials.extend(write_benchmark_trial(root, directory, profile, "performance", index, mean,
                                        images["performance"], build_id)
                  for index in range(1, 4))
    return {
        "status": "passed", "profile": profile, "candidate_id": candidate_id,
        "memory_mode": memory, "clock_hz": 48_000_000, "identity": identity,
        "trials": trials,
        "aggregate": {"count": 3, "coremark_mean": float(mean)},
    }


def ddr_qualification(root, directory, candidate_id, bitstream_sha, profile="standard"):
    lane_records = (
        "SDRAM_READ_LEVELING_LANE module=0 dq=0 bitslip=1 status=passed window_start=1 window_length=4 delay_center=2 delay_half_window=1\n"
        "SDRAM_READ_LEVELING_LANE module=0 dq=1 bitslip=1 status=passed window_start=1 window_length=4 delay_center=2 delay_half_window=1\n"
    )
    trials = []
    for index in range(10):
        raw = root / f"{directory}-training-{index}.uart.bin"
        raw.write_text(lane_records + "SDRAM_TRAINING_RESULT status=passed\nMemtest OK\nDDR_TEST_SMOKE status=passed\n")
        tx = root / f"{directory}-training-{index}.uart.tx.bin"
        tx.write_bytes(b"\nserialboot\n")
        trials.append({
            "mode": "training", "status": "passed", "training": {"status": "passed"},
            "bios_memtest": {"status": "passed"}, "smoke_test": {"status": "passed"},
            "decode_errors_present": False,
            "raw_uart_log": str(raw.relative_to(root)), "raw_uart_sha256": digest(raw),
            "transmitted_uart_log": str(tx.relative_to(root)),
            "transmitted_uart_sha256": digest(tx),
        })
    raw = root / f"{directory}-full.uart.bin"
    phase_names = {
        "walking_ones", "walking_zeros", "fixed_pattern", "inverted_pattern",
        "address_pattern", "deterministic_pseudorandom", "address_bank_row_column_alias",
        "byte_halfword_neighbor_preservation", "cached_uncached_visibility",
        "sustained_delayed_readback_stress",
    }
    phase_records = []
    for name in sorted(phase_names):
        coverage = 104 if name == "address_bank_row_column_alias" else 512 if name == "cached_uncached_visibility" else 256 * 1024 * 1024
        start = "0xc0100000" if name == "cached_uncached_visibility" else "0xc0000000"
        phase_records.append(
            f"DDR_TEST_PHASE_START name={name} range_start={start} range_end=0xd0000000 coverage_bytes={coverage}\n"
            f"DDR_TEST_PHASE_END name={name} status=passed bytes={coverage} operations=26 elapsed_ticks_hi=00000000 elapsed_ticks_lo=00000001 errors=0\n"
        )
    raw.write_text(
        lane_records + "SDRAM_TRAINING_RESULT status=passed\nMemtest OK\n"
        f"DDR_TEST_START profile={profile} memory=ddr3 clock_hz=48000000 ddr_bytes=268435456 "
        "l2_bytes=8192 stress_seconds=1800\n"
        + "".join(phase_records)
        + "DDR_TEST_STRESS requested_seconds=1800 elapsed_ticks_hi=00000014 elapsed_ticks_lo=1dd76000\n"
        + f"DDR_TEST_END status=passed profile={profile} memory=ddr3 errors=0 tested_bytes=268435456\n"
    )
    tx = root / f"{directory}-full.uart.tx.bin"
    tx.write_bytes(b"\nserialboot\n")
    trials.append({
        "mode": "full", "status": "passed", "training": {"status": "passed"},
        "bios_memtest": {"status": "passed"}, "decode_errors_present": False,
        "raw_uart_log": str(raw.relative_to(root)), "raw_uart_sha256": digest(raw),
        "transmitted_uart_log": str(tx.relative_to(root)),
        "transmitted_uart_sha256": digest(tx),
        "result": {
            "status": "passed", "geometry_bytes": 256 * 1024 * 1024,
            "stress_seconds_actual": 1800.0,
            "phases": {name: {"status": "passed"} for name in phase_names},
            "bandwidth": {
                "path": "uncached_controller_alias", "clock_hz": 48_000_000,
                "transfer_bytes": 1024 * 1024, "read_bytes": 1024 * 1024,
                "write_bytes": 1024 * 1024,
            },
        },
    })
    session = {
        "status": "passed", "acceptance": "thorough", "profile": profile,
        "memory_mode": "ddr3", "cpu_candidate": candidate_id,
        "expected_read_lanes": 2,
        "stress_seconds": 1800.0, "full_range_bytes": 256 * 1024 * 1024,
        "error_count": 0, "uncached_smoke_passed": True,
        "build_identity": {"bitstream_sha256": bitstream_sha}, "trials": trials,
    }
    return session


class MaxPerfSelectionTests(unittest.TestCase):
    def create_selection(self, root, *, ddr_stress=1800):
        modes = {}
        for memory in ("onchip", "ddr3"):
            candidate_id = f"icache-4096_dcache-4096_rv32im-{memory}"
            rtl_relative = f"build/maxperf-candidates/{memory}/{candidate_id}/VexRiscv.v"
            rtl_path = root / rtl_relative
            rtl_path.parent.mkdir(parents=True, exist_ok=True)
            rtl_path.write_text("module VexRiscv; endmodule\n")
            rtl_sha = digest(rtl_path)
            config = {
                "cpu_variant": "projectim", "isa": "rv32i2p0_m",
                "instruction_cache_bytes": 4096, "data_cache_bytes": 4096,
                "compressed": False, "prediction": "dynamic_target", "clock_hz": 48_000_000,
            }
            manifest = {
                "candidate_id": candidate_id, "memory_mode": memory,
                "rtl": rtl_relative, "rtl_sha256": rtl_sha,
                "cpu_configuration": config,
            }
            manifest_ref = write_json(
                root, f"build/maxperf-candidates/{memory}/{candidate_id}/candidate.json", manifest
            )
            manifest_ref.update({"rtl": rtl_relative, "rtl_sha256": rtl_sha})
            measured_config = {**config, "rtl_sha256": rtl_sha}
            baseline = passing_case(
                root, f"{memory}-baseline", 100, "performance", "dynamic_target", memory,
            )
            candidate = passing_case(
                root, f"{memory}-candidate", 110, "standard", candidate_id, memory,
                measured_config, rtl_relative,
            )
            verification = passing_case(
                root, f"{memory}-verification", 109, "maxperf", candidate_id, memory,
                measured_config, rtl_relative,
            )
            qualification = {
                "status": "passed", "training_runs_passed": 10 if memory == "ddr3" else 0,
                "stress_seconds": ddr_stress if memory == "ddr3" else 0,
                "full_range_bytes": 256 * 1024 * 1024 if memory == "ddr3" else 0,
                "error_count": 0, "uncached_smoke_passed": memory == "ddr3",
            }
            if memory == "ddr3":
                session = ddr_qualification(
                    root, f"{memory}-qualification", candidate_id,
                    candidate["identity"]["bitstream"]["sha256"],
                )
                if ddr_stress != 1800:
                    session["stress_seconds"] = ddr_stress
                    session["trials"][-1]["result"]["stress_seconds_actual"] = ddr_stress
                session_ref = write_json(root, "build/ddr3/qualification-session.json", session)
                qualification["stress_seconds"] = ddr_stress
                qualification["session_evidence"] = session_ref
                baseline_qualification = ddr_qualification(
                    root, f"{memory}-baseline-qualification",
                    baseline["identity"]["cpu_candidate"],
                    baseline["identity"]["bitstream"]["sha256"], profile="performance",
                )
                if ddr_stress != 1800:
                    baseline_qualification["stress_seconds"] = ddr_stress
                    baseline_qualification["trials"][-1]["result"]["stress_seconds_actual"] = ddr_stress
                baseline_ref = write_json(
                    root, "build/ddr3/performance-baseline-session.json", baseline_qualification,
                )
            evaluation = {
                "status": "accepted", "memory_mode": memory, "clock_hz": 48_000_000,
                "fresh_performance_baseline_mean": 100.0,
                "baseline": baseline, "candidates": {candidate_id: candidate},
                "selected_candidate": candidate_id, "winner_verification": verification,
                "qualification": qualification,
            }
            if memory == "ddr3":
                evaluation["ddr_preflight"] = {
                    "status": "passed", "acceptance": "thorough", "actual_training_runs": 10,
                    "stress_seconds": ddr_stress, "full_range_bytes": 256 * 1024 * 1024,
                    "error_count": 0, "uncached_smoke_passed": True,
                    "session_evidence": baseline_ref,
                }
            evaluation_ref = write_json(
                root, f"docs/performance/maxperf-evaluation/{memory}.json", evaluation,
            )
            modes[memory] = {
                "status": "accepted", "candidate_id": candidate_id,
                "clock_hz": 48_000_000, "candidate_manifest": manifest_ref,
                "evaluation_evidence": evaluation_ref,
            }
        selection = {
            "schema_version": 1, "profile": "maxperf", "status": "accepted",
            "clock_hz": 48_000_000, "memory_modes": modes,
        }
        write_json(root, "maxperf-profile-selection.json", selection)
        return selection

    def test_public_profile_requires_both_hashed_mode_wins_and_ddr_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = self.create_selection(root)
            accepted = accepted_maxperf_selection(root)
            self.assertEqual(accepted, selection)

    def test_changed_evaluation_bytes_reject_the_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = self.create_selection(root)
            evidence = root / selection["memory_modes"]["onchip"]["evaluation_evidence"]["path"]
            evidence.write_text(evidence.read_text() + " ")
            self.assertIsNone(accepted_maxperf_selection(root))

    def test_incomplete_ddr_stress_cannot_promote(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_selection(root, ddr_stress=1799)
            self.assertIsNone(accepted_maxperf_selection(root))

    def test_changed_raw_uart_capture_rejects_even_a_rehashed_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = self.create_selection(root)
            mode = selection["memory_modes"]["onchip"]
            eval_path = root / mode["evaluation_evidence"]["path"]
            evaluation = json.loads(eval_path.read_text())
            trial = evaluation["candidates"][mode["candidate_id"]]["trials"][1]
            (root / trial["raw_uart_log"]).write_bytes(b"tampered")
            mode["evaluation_evidence"]["sha256"] = digest(eval_path)
            selection_path = root / "maxperf-profile-selection.json"
            selection_path.write_text(json.dumps(selection, indent=2) + "\n")
            self.assertIsNone(accepted_maxperf_selection(root))

    def test_equal_candidate_mean_does_not_beat_the_fresh_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = self.create_selection(root)
            mode = selection["memory_modes"]["onchip"]
            eval_path = root / mode["evaluation_evidence"]["path"]
            evaluation = json.loads(eval_path.read_text())
            candidate = evaluation["candidates"][mode["candidate_id"]]
            for trial in candidate["trials"]:
                if trial["mode"] == "performance":
                    trial["parsed"]["coremark"] = 100.0
                    trial["parsed"]["iterations"] = 100
                    trial["parsed"]["elapsed_ticks"] = 48_000_000
            candidate["aggregate"]["coremark_mean"] = 100.0
            eval_path.write_text(json.dumps(evaluation, indent=2) + "\n")
            mode["evaluation_evidence"]["sha256"] = digest(eval_path)
            (root / "maxperf-profile-selection.json").write_text(json.dumps(selection, indent=2) + "\n")
            self.assertIsNone(accepted_maxperf_selection(root))

    def test_missing_validation_run_cannot_promote(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = self.create_selection(root)
            mode = selection["memory_modes"]["onchip"]
            eval_path = root / mode["evaluation_evidence"]["path"]
            evaluation = json.loads(eval_path.read_text())
            candidate = evaluation["candidates"][mode["candidate_id"]]
            candidate["trials"] = [trial for trial in candidate["trials"] if trial["mode"] != "validation"]
            eval_path.write_text(json.dumps(evaluation, indent=2) + "\n")
            mode["evaluation_evidence"]["sha256"] = digest(eval_path)
            (root / "maxperf-profile-selection.json").write_text(json.dumps(selection, indent=2) + "\n")
            self.assertIsNone(accepted_maxperf_selection(root))


if __name__ == "__main__":
    unittest.main()
