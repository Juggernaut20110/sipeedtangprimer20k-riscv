import copy
import hashlib
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.benchmark_report as report  # noqa: E402


class BenchmarkReportEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.profile = "standard"
        self.build_id = "ab12cd34ef56ab78"
        self.source_fingerprint = "source-fingerprint"
        self.bitstream_hash = "bitstream-hash"
        self.images = {
            "validation": self.image("validation"),
            "performance": self.image("performance"),
        }
        self.benchmark = {
            "status": "passed",
            "profile": self.profile,
            "build_id": self.build_id,
            "clock_hz": report.SYS_CLK_FREQ,
            "source_fingerprint": self.source_fingerprint,
            "bitstream_sha256": self.bitstream_hash,
            "coremark": {"commit": "coremark-commit"},
            "images": self.images,
        }
        self.rows = {
            self.profile: {
                "build_status": "passed",
                "build_metadata": {"status": "passed", "sys_clk_hz": report.SYS_CLK_FREQ},
                "benchmark_metadata": self.benchmark,
            }
        }
        self.parsed = {
            mode: {
                "status": "passed",
                "profile": self.profile,
                "build_id": self.build_id,
                "mode": mode,
                "clock_hz": report.SYS_CLK_FREQ,
                "data_size": 2000,
                "contexts": 1,
                "seeds": [0, 0, 102],
                "iterations": 100,
                "elapsed_ticks": (1 << 32) + 480_000_000,
                "elapsed_seconds": ((1 << 32) + 480_000_000) / report.SYS_CLK_FREQ,
                "image_crc32": 0x1234ABCD,
                "image_bytes": 26672,
                "image_rechecks": [
                    {"phase": "calibration", "cached_crc32": 0x1234ABCD, "flushed_crc32": 0x1234ABCD, "bytes": 26672},
                    {"phase": "scored", "cached_crc32": 0x1234ABCD, "flushed_crc32": 0x1234ABCD, "bytes": 26672},
                ],
                "coremark": 9.6,
                "coremark_per_mhz": 0.2,
                "seedcrc": 0xE9F5,
                "crcs": {"crclist": 0xE714, "crcmatrix": 0x1FD7, "crcstate": 0x8E3A},
                "crcfinal": 0x4321,
                "validation": "passed",
                "upstream_iterations_per_second": 1,
                "upstream_coremark": None,
                "calibration": [],
            }
            for mode in ("validation", "performance")
        }
        self.parsed["validation"]["seeds"] = [0x3415, 0x3415, 0x66]
        self.parsed["validation"]["seedcrc"] = 0x18F2
        self.parsed["validation"]["crcs"] = {"crclist": 0xE3C1, "crcmatrix": 0x0747, "crcstate": 0x8D84}
        self.session = self.session_fixture()

    def tearDown(self):
        self.tempdir.cleanup()

    def image(self, mode):
        return {
            "profile": "standard",
            "mode": mode,
            "build_id": "ab12cd34ef56ab78",
            "binary": f"build/{mode}.bin",
            "binary_sha256": f"{mode}-hash",
        }

    def session_fixture(self):
        identity = {
            "profile": self.profile,
            "clock_hz": report.SYS_CLK_FREQ,
            "source_fingerprint": self.source_fingerprint,
            "coremark": {"commit": "coremark-commit"},
            "bitstream": {"sha256": self.bitstream_hash},
            "firmware": {
                mode: {"sha256": image["binary_sha256"]} for mode, image in self.images.items()
            },
        }
        trials = []
        for mode, attempt in (("validation", 0), ("performance", 1), ("performance", 2), ("performance", 3)):
            stem = f"{mode}-{attempt}"
            raw_rel = f"docs/performance/{stem}.uart.bin"
            text_rel = f"docs/performance/{stem}.uart.log"
            raw = f"capture {stem}\n".encode()
            (self.root / raw_rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / raw_rel).write_bytes(raw)
            (self.root / text_rel).write_text(raw.decode())
            parsed = copy.deepcopy(self.parsed[mode])
            trials.append({
                "mode": mode,
                "attempt": attempt,
                "status": "passed",
                "firmware_sha256": self.images[mode]["binary_sha256"],
                "raw_uart_log": raw_rel,
                "text_uart_log": text_rel,
                "raw_uart_sha256": hashlib.sha256(raw).hexdigest(),
                "raw_uart_bytes": len(raw),
                "parsed": parsed,
            })
        return {
            "session_id": "session-1",
            "status": "passed",
            "profile": self.profile,
            "clock_hz": report.SYS_CLK_FREQ,
            "programming_status": "completed",
            "identity": identity,
            "trials": trials,
        }

    def revalidate(self, session=None, benchmark=None):
        rows = copy.deepcopy(self.rows)
        if benchmark is not None:
            rows[self.profile]["benchmark_metadata"] = benchmark
        with patch.object(report, "ROOT", self.root), patch.object(
            report, "parse_capture", side_effect=lambda _raw, **kwargs: copy.deepcopy(self.parsed[kwargs["mode"]])
        ):
            return report.revalidate_session(session or self.session, rows)

    def test_complete_session_uses_verified_captures_and_conditional_success_text(self):
        checked = self.revalidate()
        self.assertEqual(checked["report_validation"]["status"], "passed")
        self.assertEqual(checked["aggregate"]["count"], 3)
        self.assertTrue(all(trial["reparsed"] for trial in checked["trials"]))
        interpretation = report.interpretation_text(checked, True, checked["aggregate"])
        self.assertIn("three accepted CoreMark repetitions", interpretation)
        self.assertIn("9.600000 CoreMark", interpretation)

    def test_hash_tampering_rejects_capture_and_complete_session(self):
        changed = self.root / self.session["trials"][1]["raw_uart_log"]
        changed.write_bytes(b"tampered\n")
        checked = self.revalidate()
        self.assertEqual(checked["report_validation"]["status"], "failed")
        self.assertIsNone(checked["trials"][1]["reparsed"])
        self.assertIsNone(checked["aggregate"])
        self.assertIn("raw UART capture hash", " ".join(checked["report_validation"]["errors"]))

    def test_cached_parsed_counters_must_match_reparsed_capture(self):
        session = copy.deepcopy(self.session)
        session["trials"][1]["parsed"]["elapsed_ticks"] += 1
        checked = self.revalidate(session)
        self.assertIsNone(checked["trials"][1]["reparsed"])
        self.assertIsNone(checked["aggregate"])
        self.assertIn("saved parsed results disagree", " ".join(checked["report_validation"]["errors"]))

    def test_cached_image_rechecks_must_match_reparsed_capture(self):
        session = copy.deepcopy(self.session)
        session["trials"][1]["parsed"]["image_rechecks"][0]["flushed_crc32"] ^= 1
        checked = self.revalidate(session)
        self.assertIsNone(checked["trials"][1]["reparsed"])
        self.assertIsNone(checked["aggregate"])
        self.assertIn("saved parsed results disagree", " ".join(checked["report_validation"]["errors"]))

    def test_rebuild_with_new_firmware_identity_rejects_old_run(self):
        rebuilt = copy.deepcopy(self.benchmark)
        rebuilt["images"]["performance"]["binary_sha256"] = "new-performance-hash"
        checked = self.revalidate(benchmark=rebuilt)
        self.assertEqual(checked["report_validation"]["status"], "failed")
        self.assertTrue(all(trial["reparsed"] is None for trial in checked["trials"]))
        self.assertIsNone(checked["aggregate"])

    def test_failed_historical_session_never_enters_aggregate(self):
        failed = copy.deepcopy(self.session)
        failed["status"] = "failed"
        failed["trials"][0]["status"] = "failed"
        failed["trials"][0]["error"] = "upload timeout"
        for trial in failed["trials"][1:]:
            trial["status"] = "not_run"
            trial.pop("parsed", None)
        checked = self.revalidate(failed)
        self.assertEqual(checked["report_validation"]["status"], "failed")
        self.assertIsNone(checked["aggregate"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("SRAM programming completed", report.interpretation_text(checked, False, None))

    def test_report_explains_polling_uart_and_recorded_startup_recovery(self):
        benchmark = {
            "images": {
                "performance": {
                    "commands": {"litex_uart_polling.c": {"flags": ["-DUART_POLLING"]}}
                }
            }
        }
        self.assertIn("polling backend", report.uart_method_text(benchmark))
        session = {
            "trials": [{
                "startup_recovery": {
                    "status": "completed",
                    "trigger": "bios_console_after_missed_initial_sfl_ack",
                    "port_reopened": True,
                    "firmware_upload_mode": "LiteXTerm safe mode (64-byte frames, one outstanding)",
                    "console_command": "\\nserialboot\\n",
                    "fpga_reprogrammed": False,
                }
            }]
        }
        recovery = report.startup_recovery_text(session)
        self.assertIn("64-byte frames", recovery)
        self.assertIn("FPGA reprogrammed during recovery: False", recovery)
        failure = {
            "profile": "standard",
            "programming_status": "completed",
            "trials": [{"mode": "validation", "error": "no benchmark start marker"}],
        }
        self.assertIn("UART firmware upload/validation did not reach", report.interpretation_text(failure, False, None))

    def test_fixed_calibration_report_says_scored_pass_reinitializes_coremark(self):
        benchmark = {
            "iteration_calibration": {
                "method": "fixed_iteration_port_calibration_with_fresh_upstream_initialization",
                "calibration_iterations": 1000,
                "target_seconds": 20,
                "formula": "ceil(calibration_iterations * target_seconds * clock_hz / calibration_ticks)",
                "scored_pass_reinitializes_static_algorithm_data": True,
            },
            "cache_maintenance": {
                "function": "portable_init",
                "sequence": [
                    "compiler_and_memory_fence",
                    "flush_cpu_dcache",
                    "compiler_and_memory_fence",
                    "flush_cpu_icache",
                    "compiler_and_memory_fence",
                ],
                "timing": "before_each_upstream_invocation_and_outside_timed_workload",
            }
        }
        text = report.calibration_method_text(benchmark)
        self.assertIn("separate 1000-iteration calibration workload", text)
        self.assertIn("invokes upstream CoreMark again", text)
        self.assertIn("reinitializes its static algorithm data", text)
        self.assertIn("flushes the CPU data and instruction caches", text)
        self.assertIn("calibration pass does not warm the scored pass", text)
        self.assertIn("outside the timed workload", text)
        self.assertNotIn("automatic iteration calibration", text)
        self.assertIn("calibration time is excluded", text)

    def test_runtime_preflight_report_and_dock_uart_reproduction_path(self):
        benchmark = {
            "runtime_preflight": {
                "ram_probe": {
                    "storage": "volatile BSS array of 64 union words",
                    "bytes": 256,
                    "alignment_bytes": 32,
                    "phases": ["32-bit patterns", "halfword and byte lane preservation"],
                },
                "runtime_seed_checks": {
                    "inputs": ["seed1_volatile", "seed2_volatile", "seed3_volatile", "seed4_volatile"],
                    "failure": "emit BENCHMARK_PORT_ERROR and halt before upstream invocation",
                },
                "timing": "all image, SRAM, and seed checks run before timed CoreMark work",
                "image_integrity_rechecks": {
                    "phases": ["calibration", "scored"],
                    "sequence": ["cached image CRC32", "flush caches", "flushed image CRC32"],
                    "comparison": "both values and image sizes must match initial preflight values",
                    "failure": "BENCHMARK_PORT_ERROR and halt",
                    "timing": "after_each_upstream_invocation_and_outside_timed_workload",
                },
                "wrapper_optimization": "benchmark_main.c is compiled with -Os to fit untimed diagnostics in 32-KiB main RAM; CoreMark sources retain the profile-wide -O2 flag",
            }
        }
        preflight = report.runtime_preflight_text(benchmark)
        self.assertIn("256-byte, 32-byte-aligned", preflight)
        self.assertIn("seed1_volatile", preflight)
        self.assertIn("initial linked-image CRC32 and size", preflight)
        self.assertIn("After each calibration and scored timed interval", preflight)
        self.assertIn("outside timing", preflight)
        self.assertIn("-Os to fit untimed diagnostics", preflight)
        self.assertIn("CoreMark sources retain the profile-wide -O2 flag", preflight)

        port = "/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0"
        sessions = [{"profile": "standard", "identity": {"uart_device_requested": port}}]
        self.assertEqual(
            report.reproduction_port(sessions, {"profile": "standard", "identity": {}}, "standard"), port
        )
        self.assertEqual(
            report.reproduction_command("standard", port),
            f"make benchmark-run PROFILE=standard PORT={port}",
        )

    def test_automatic_calibration_report_requires_build_flag_evidence(self):
        benchmark = {
            "images": {
                "validation": {
                    "commands": {"core_main.c": {"flags": ["-DITERATIONS=0"]}}
                }
            }
        }
        self.assertIn("automatic iteration calibration", report.calibration_method_text(benchmark))
        self.assertIn("does not verify the iteration-calibration method", report.calibration_method_text({}))

    def test_compiler_flags_are_a_single_reproducible_shell_command(self):
        rendered = report.compiler_flags_text(["-O2", "-march=rv32i2p0_m", "-DNAME=two words"])
        self.assertTrue(rendered.startswith("```sh\n") and rendered.endswith("\n```"))
        command = rendered.splitlines()[1]
        self.assertEqual(command, "-O2 -march=rv32i2p0_m '-DNAME=two words'")
        self.assertEqual(len(rendered.splitlines()), 3)

    def test_interpretation_reports_crc_failure_without_stale_device_blocker(self):
        session = {
            "profile": "standard",
            "status": "failed",
            "programming_status": "completed",
            "trials": [{
                "mode": "validation",
                "error": "capture validation failed: firmware reported a CoreMark error",
            }],
        }
        rendered = report.interpretation_text(session, False, None)
        self.assertIn("firmware reported a CoreMark error", rendered)
        self.assertIn("FPGA SRAM programming completed", rendered)
        self.assertNotIn("USB", rendered)
        self.assertNotIn("device was not visible", rendered)

    def test_hardware_diagnostic_requires_verified_raw_and_firmware_evidence(self):
        firmware = b"validated firmware image for diagnostic test"
        firmware_path = "build/standard/validation.bin"
        raw_path = "docs/performance/final-validation.uart.bin"
        (self.root / firmware_path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / firmware_path).write_bytes(firmware)
        image_crc = zlib.crc32(firmware) & 0xFFFFFFFF
        raw = (
            f"BENCHMARK_IMAGE_CRC32 build_id={self.build_id} value={image_crc:08x} size={len(firmware)}\n"
            "BENCHMARK_RAM_CHECK status=failed phase=tight_sram_conflict_lh "
            "firstaddr=10000940 expected=00008a01 actual=00000000 errors=256 failmask=00080000 bytes=256\n"
            "BENCHMARK_PORT_ERROR on-chip SRAM preflight failed\n"
        ).encode()
        (self.root / raw_path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / raw_path).write_bytes(raw)
        firmware_sha = hashlib.sha256(firmware).hexdigest()
        raw_sha = hashlib.sha256(raw).hexdigest()
        session = {
            "session_id": "final-session",
            "profile": self.profile,
            "programming_status": "completed",
            "identity": {
                "source_fingerprint": self.build_id,
                "firmware": {
                    "validation": {
                        "path": firmware_path,
                        "sha256": firmware_sha,
                        "crc32": f"{image_crc:08x}",
                        "bytes": len(firmware),
                    }
                },
            },
            "trials": [
                {
                    "mode": "validation",
                    "status": "failed",
                    "programming_status": "completed",
                    "firmware_sha256": firmware_sha,
                    "raw_uart_log": raw_path,
                    "raw_uart_sha256": raw_sha,
                    "raw_uart_bytes": len(raw),
                },
                *[
                    {"mode": "performance", "attempt": attempt, "status": "not_run"}
                    for attempt in (1, 2, 3)
                ],
            ],
        }
        old_raw = b"[0]ERROR! list crc 0x763a - should be 0xe3c1\n"
        old_path = "docs/performance/old-validation.uart.bin"
        (self.root / old_path).write_bytes(old_raw)
        old_session = {
            "session_id": "old-session",
            "profile": self.profile,
            "trials": [{
                "mode": "validation",
                "raw_uart_log": old_path,
                "raw_uart_sha256": hashlib.sha256(old_raw).hexdigest(),
                "raw_uart_bytes": len(old_raw),
            }],
        }

        with patch.object(report, "ROOT", self.root):
            observed = report.latest_observed_diagnostics([session], self.profile)
            history = report.historical_coremark_crc_failures([old_session], self.profile)
            rendered = report.diagnostics_text(observed, history, session)
            self.assertTrue(observed["raw_uart_hash_verified"])
            self.assertTrue(observed["host_image"]["host_artifact_verified"])
            self.assertTrue(observed["host_image"]["matches_host_artifact"])
            self.assertIn("FPGA SRAM programming and UART firmware upload completed", rendered)
            self.assertIn(f"CRC32 `{image_crc:08x}` and size {len(firmware)} bytes match", rendered)
            self.assertIn("tight_sram_conflict_lh", rendered)
            self.assertIn("0x10000940", rendered)
            self.assertIn("expected `0x00008a01` and read `0x00000000`", rendered)
            self.assertIn("exact hardware/cache root cause remains unresolved", rendered)
            self.assertIn("All 3 scored performance repetitions were not run", rendered)
            self.assertIn("Earlier hash-verified CoreMark captures failed list CRC validation", rendered)
            self.assertIn("fence and D-cache flush", rendered)
            self.assertNotIn("CoreMark score:", rendered)

            (self.root / raw_path).write_bytes(raw + b"tampered")
            self.assertIsNone(report.latest_observed_diagnostics([session], self.profile))
            self.assertEqual(len(history), 1)
            (self.root / old_path).write_bytes(old_raw + b"tampered")
            self.assertEqual(
                report.historical_coremark_crc_failures([old_session], self.profile), []
            )

    def test_profile_table_reports_linked_sram_padding_and_bounds(self):
        rows = {}
        for profile in report.PROFILES:
            rows[profile] = {
                "build_status": "passed",
                "benchmark_metadata": {
                    "images": {
                        "performance": {
                            "sram_section_bytes": 2592,
                            "sram_alignment_padding_bytes": 0,
                            "working_sram_bytes": 2592,
                            "reserved_stack_bytes": 2048,
                            "remaining_sram_bytes": 3552,
                            "stack_usage": {"largest_static_frame_bytes": 224},
                        },
                        "validation": {
                            "sram_section_bytes": 2600,
                            "sram_alignment_padding_bytes": 24,
                            "working_sram_bytes": 2624,
                            "reserved_stack_bytes": 2048,
                            "remaining_sram_bytes": 3520,
                        },
                    },
                    "compiler_flags": ["-march=rv32im", "-mabi=ilp32"],
                },
            }
        rendered = report.profile_table(rows, "standard", "not measured")
        self.assertIn("Maximum SRAM data+BSS+padding / stack / free", rendered)
        self.assertIn("2600+24=2624 / 2048 / 3520 B", rendered)


if __name__ == "__main__":
    unittest.main()
