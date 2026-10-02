import copy
import hashlib
import json
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.benchmark_report as report  # noqa: E402
from scripts.benchmark_identity import (  # noqa: E402
    CURRENT_IDENTITY_SCHEMA, benchmark_fingerprint_payload, legacy_fingerprint_payload, stable_hash,
)
from scripts.benchmark_evidence import bundle_file_matches, bundle_is_current, create_bundle  # noqa: E402


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
            tx_rel = f"docs/performance/{stem}.uart.tx.bin"
            tx = b"\nserialboot\n"
            (self.root / tx_rel).write_bytes(tx)
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
                "transmitted_uart_log": tx_rel,
                "transmitted_uart_sha256": hashlib.sha256(tx).hexdigest(),
                "transmitted_uart_bytes": len(tx),
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

    def test_transmitted_uart_hash_tampering_rejects_complete_session(self):
        changed = self.root / self.session["trials"][1]["transmitted_uart_log"]
        changed.write_bytes(b"changed upload bytes")
        checked = self.revalidate()
        self.assertEqual(checked["report_validation"]["status"], "failed")
        self.assertIsNone(checked["aggregate"])
        self.assertIn("transmitted UART capture hash", " ".join(checked["report_validation"]["errors"]))

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
            self.assertIn("All 3 scored performance repetitions were not run", rendered)
            self.assertIn("Earlier hash-verified CoreMark captures failed list CRC validation", rendered)
            self.assertIn("read-first RAM ports", rendered)
            self.assertIn("write-through `SP` blocks", rendered)
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

    def test_memory_build_fingerprint_uses_exact_soc_configuration(self):
        source_hashes = {
            "firmware/core.c": "source-hash",
            "build/software/include/generated/soc.h": "header-hash",
            ".deps/pythondata-cpu-vexriscv/verilog/VexRiscv.v": "rtl-hash",
        }
        cpu_configuration = {"liteX_variant": "standard", "prediction": "dynamic_target"}
        soc_memory = {"mode": "onchip", "main_ram_base": 0x40000000, "main_ram_bytes": 32768}
        selection = {"schema_version": 1, "profile": "performance", "candidate": "dynamic_target"}
        metadata = {
            "identity_schema_version": CURRENT_IDENTITY_SCHEMA,
            "profile": "performance",
            "coremark": {"commit": "coremark-revision"},
            "source_hashes": source_hashes,
            "bitstream_sha256": "bitstream-hash",
            "compiler": "riscv-none-elf-gcc",
            "compiler_version": "gcc-version",
            "compiler_flags": ["-O2"],
            "include_flags": ["-Iinclude"],
            "clock_hz": report.SYS_CLK_FREQ,
            "cpu_variant": "standard",
            "cpu_candidate": "dynamic_target",
            "cpu_profile_selection": selection,
            "memory_mode": "onchip",
            "cpu_configuration": cpu_configuration,
            # This field describes where benchmark sections are placed; it is
            # not the integer-address memory map used in the build fingerprint.
            "memory": {"mode": "onchip", "main_ram_base": "0x40000000", "code_and_read_only_data": "main_ram"},
        }
        build_metadata = {
            "cpu_variant": "standard", "cpu_candidate": "dynamic_target",
            "cpu_profile_selection": selection, "memory_mode": "onchip",
            "cpu_configuration": cpu_configuration, "memory": soc_memory,
        }
        metadata["fingerprint_payload"] = benchmark_fingerprint_payload(
            metadata, build_metadata,
        )
        fingerprint = stable_hash(metadata["fingerprint_payload"])
        metadata["source_fingerprint"] = fingerprint
        with patch.object(report, "file_matches", return_value=True):
            self.assertTrue(report.benchmark_fingerprint_matches(metadata, build_metadata))
            self.assertFalse(report.benchmark_fingerprint_matches(metadata))

            for field, changed in (
                ("cpu_variant", "linux"),
                ("cpu_candidate", "different-candidate"),
                ("cpu_profile_selection", {"profile": "different-selection"}),
                ("bitstream_sha256", "different-bitstream"),
            ):
                mutated = copy.deepcopy(metadata)
                mutated[field] = changed
                self.assertFalse(report.benchmark_fingerprint_matches(mutated, build_metadata), field)

            for path in source_hashes:
                mutated = copy.deepcopy(metadata)
                mutated["source_hashes"][path] = "changed-input-hash"
                self.assertFalse(report.benchmark_fingerprint_matches(mutated, build_metadata), path)

            changed_build = copy.deepcopy(build_metadata)
            changed_build["memory"] = {**soc_memory, "main_ram_bytes": 65536}
            self.assertFalse(report.benchmark_fingerprint_matches(metadata, changed_build))

    def test_modern_fingerprint_round_trips_linux_performance_and_maxperf_modes(self):
        selection_profiles = [{
            "schema_version": 1, "profile": "performance", "status": "accepted",
            "candidate": "dynamic_target",
        }]
        for state in ("provisional", "accepted"):
            for memory in ("onchip", "ddr3"):
                selection_profiles.append({
                    "schema_version": 1, "profile": "maxperf", "status": state,
                    "memory_modes": {memory: {"status": state, "candidate_id": "candidate-x"}},
                })
        scenarios = [
            ("linux", "linux", None, "onchip", None),
            ("performance", "standard", "dynamic_target", "onchip", selection_profiles[0]),
        ]
        scenarios.extend(
            ("maxperf", "projectim", "candidate-x", memory, selection)
            for selection in selection_profiles[1:]
            for memory in selection["memory_modes"]
        )

        for profile, variant, candidate, memory, selection in scenarios:
            with self.subTest(profile=profile, memory=memory, selection=selection):
                cpu_configuration = {
                    "isa": "rv32i2p0_ma" if profile == "linux" else "rv32i2p0_m",
                    "rtl_sha256": f"rtl-{profile}-{memory}",
                }
                memory_configuration = {
                    "mode": memory,
                    "main_ram_base": 0x40000000,
                    "main_ram_bytes": 32768 if memory == "onchip" else 268435456,
                }
                build_metadata = {
                    "cpu_variant": variant, "cpu_candidate": candidate,
                    "cpu_profile_selection": selection, "memory_mode": memory,
                    "cpu_configuration": cpu_configuration,
                    "memory": memory_configuration,
                }
                metadata = {
                    "identity_schema_version": CURRENT_IDENTITY_SCHEMA,
                    "profile": profile, "cpu_variant": variant,
                    "cpu_candidate": candidate, "cpu_profile_selection": selection,
                    "memory_mode": memory,
                    "coremark": {"commit": "coremark"},
                    "source_hashes": {"build/input.h": f"header-{memory}"},
                    "bitstream_sha256": f"bitstream-{profile}-{memory}",
                    "compiler": "riscv-gcc", "compiler_version": "15.2",
                    "compiler_flags": ["-O2"], "include_flags": ["-Iinclude"],
                    "clock_hz": report.SYS_CLK_FREQ,
                    "cpu_configuration": cpu_configuration,
                }
                metadata["fingerprint_payload"] = benchmark_fingerprint_payload(metadata, build_metadata)
                metadata["source_fingerprint"] = stable_hash(metadata["fingerprint_payload"])
                with patch.object(report, "file_matches", return_value=True):
                    self.assertTrue(report.benchmark_fingerprint_matches(metadata, build_metadata))

    def test_legacy_fingerprint_schema_keeps_onchip_default(self):
        metadata = {
            "profile": "minimal", "coremark": {"commit": "legacy-coremark"},
            "source_hashes": {"firmware/core.c": "old-hash"},
            "bitstream_sha256": "old-bitstream", "compiler": "old-gcc",
            "compiler_version": "old-version", "compiler_flags": ["-O2"],
            "include_flags": ["-Iinclude"], "clock_hz": report.SYS_CLK_FREQ,
        }
        metadata["source_fingerprint"] = stable_hash(legacy_fingerprint_payload(metadata))
        with patch.object(report, "file_matches", return_value=True):
            self.assertTrue(report.benchmark_fingerprint_matches(metadata))
            self.assertEqual(metadata.get("memory_mode", "onchip"), "onchip")

    def test_archived_identity_remains_verifiable_after_source_paths_change(self):
        source_path = self.root / "firmware/core.c"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(b"original source")
        build_dir = self.root / "build/standard"
        benchmark_dir = build_dir / "benchmark"
        benchmark_dir.mkdir(parents=True, exist_ok=True)
        bitstream_path = build_dir / "bitstream.fs"
        bitstream_path.write_bytes(b"bitstream")
        images = {}
        for mode in ("validation", "performance"):
            image_path = benchmark_dir / f"{mode}.bin"
            image_path.write_bytes(mode.encode())
            images[mode] = {
                "binary": str(image_path.relative_to(self.root)),
                "binary_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                "profile": "standard", "mode": mode, "build_id": "build-id",
                "binary_crc32": 1234, "binary_bytes": image_path.stat().st_size,
            }
        cpu_configuration = {"isa": "rv32im", "rtl_sha256": "rtl-hash"}
        memory_configuration = {"mode": "onchip", "main_ram_base": 0x40000000, "main_ram_bytes": 32768}
        build = {
            "status": "passed", "profile": "standard", "cpu_variant": "standard",
            "cpu_candidate": None, "cpu_profile_selection": None, "memory_mode": "onchip",
            "cpu_configuration": cpu_configuration, "memory": memory_configuration,
            "sys_clk_hz": report.SYS_CLK_FREQ,
            "bitstream": str(bitstream_path.relative_to(self.root)),
        }
        metadata = {
            "status": "passed", "identity_schema_version": CURRENT_IDENTITY_SCHEMA,
            "profile": "standard", "cpu_variant": "standard", "cpu_candidate": None,
            "cpu_profile_selection": None, "memory_mode": "onchip",
            "coremark": {"commit": "coremark"},
            "source_hashes": {"firmware/core.c": hashlib.sha256(source_path.read_bytes()).hexdigest()},
            "bitstream": str(bitstream_path.relative_to(self.root)),
            "bitstream_sha256": hashlib.sha256(bitstream_path.read_bytes()).hexdigest(),
            "compiler": "gcc", "compiler_version": "test", "compiler_flags": ["-O2"],
            "include_flags": ["-Iinclude"], "clock_hz": report.SYS_CLK_FREQ,
            "cpu_configuration": cpu_configuration, "memory_configuration": memory_configuration,
            "images": images,
        }
        metadata["fingerprint_payload"] = benchmark_fingerprint_payload(metadata, build)
        metadata["source_fingerprint"] = stable_hash(metadata["fingerprint_payload"])
        (build_dir / "build-metadata.json").write_text(json.dumps(build, indent=2) + "\n")
        (benchmark_dir / "benchmark-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")

        reference = create_bundle(build_dir, metadata, build, self.root)
        metadata["evidence_bundle"] = reference
        source_path.write_bytes(b"replacement source")
        with patch.object(report, "ROOT", self.root):
            self.assertFalse(report.benchmark_fingerprint_matches(metadata, build))
            self.assertTrue(report.benchmark_fingerprint_matches(metadata, build, allow_bundle=True))
        self.assertTrue(bundle_is_current(reference, metadata["source_fingerprint"], self.root))
        self.assertTrue(bundle_file_matches(
            reference, "firmware/core.c", metadata["source_hashes"]["firmware/core.c"], self.root,
        ))

    def test_historical_three_profile_batch_uses_its_recorded_membership(self):
        batch_id = "20260930T120000.000000Z-all"
        historical_profiles = ["minimal", "lite", "standard"]
        prior = {"session_id": "old-standard", "profile": "standard", "status": "passed"}
        sessions = [
            prior,
            *[
                {
                    "session_id": f"{batch_id}-{profile}",
                    "batch_id": batch_id,
                    "requested_profiles": historical_profiles,
                    "profile": profile,
                    "status": "passed",
                    "aggregate": {"coremark_mean": float(index + 1)},
                }
                for index, profile in enumerate(list(report.PROFILES)[:2])
            ],
        ]
        calls = []

        def fake_revalidate(session, _rows):
            calls.append(session["session_id"])
            checked = dict(session)
            checked["report_validation"] = {"status": "passed", "errors": []}
            checked["aggregate"] = session["aggregate"]
            return checked

        with patch.object(report, "revalidate_session", side_effect=fake_revalidate):
            batch = report.revalidate_batch_sessions(
                sessions, batch_id, historical_profiles, {}
            )
        self.assertEqual(batch["status"], "incomplete")
        self.assertEqual(batch["missing_profiles"], ["standard"])
        self.assertNotIn("performance", batch["profile_results"])
        self.assertIsNone(batch["profile_results"]["standard"]["aggregate"])
        self.assertEqual(set(batch["profile_results"]["minimal"]["aggregate"]), {"coremark_mean"})
        self.assertEqual(calls, [f"{batch_id}-minimal", f"{batch_id}-lite"])
        self.assertNotIn("old-standard", calls)

    def test_three_through_six_profile_batches_follow_recorded_membership(self):
        all_profiles = ["minimal", "lite", "standard", "performance", "linux", "maxperf"]
        for count in (3, 4, 5, 6):
            batch_id = f"historical-{count}-profile-batch"
            membership = all_profiles[:count]
            sessions = [{
                "session_id": f"{batch_id}-{profile}", "batch_id": batch_id,
                "requested_profiles": membership, "profile": profile, "status": "passed",
                "aggregate": {"coremark_mean": float(index + 1)},
            } for index, profile in enumerate(membership)]

            def fake_revalidate(session, _rows):
                checked = dict(session)
                checked["report_validation"] = {"status": "passed", "errors": []}
                checked["aggregate"] = session["aggregate"]
                return checked

            with self.subTest(count=count), patch.object(
                report, "PROFILES", {profile: profile for profile in all_profiles[:5]},
            ), patch.object(report, "revalidate_session", side_effect=fake_revalidate):
                batch = report.revalidate_batch_sessions(sessions, batch_id, membership, {})
            self.assertEqual(batch["status"], "passed")
            self.assertEqual(batch["requested_profiles"], membership)
            self.assertEqual(set(batch["profile_results"]), set(membership))

    def test_batch_is_complete_only_when_all_profiles_revalidate(self):
        batch_id = "20260930T120000.000000Z-all"
        sessions = [
            {
                "session_id": f"{batch_id}-{profile}",
                "batch_id": batch_id,
                "requested_profiles": list(report.PROFILES),
                "profile": profile,
                "status": "passed",
                "aggregate": {"coremark_mean": float(index + 1)},
            }
            for index, profile in enumerate(report.PROFILES)
        ]

        def fake_revalidate(session, _rows):
            checked = dict(session)
            checked["report_validation"] = {"status": "passed", "errors": []}
            return checked

        with patch.object(report, "revalidate_session", side_effect=fake_revalidate):
            batch = report.revalidate_batch_sessions(
                sessions, batch_id, list(report.PROFILES), {}
            )
        self.assertEqual(batch["status"], "passed")
        self.assertEqual(batch["missing_profiles"], [])
        self.assertEqual(
            {profile: result["aggregate"] for profile, result in batch["profile_results"].items()},
            {profile: {"coremark_mean": float(index + 1)} for index, profile in enumerate(report.PROFILES)},
        )

    def test_batch_report_keeps_profile_results_separate_when_one_fails(self):
        batch_id = "20260930T120000.000000Z-all"
        sessions = []
        for profile in report.PROFILES:
            session = copy.deepcopy(self.session)
            session["session_id"] = f"{batch_id}-{profile}"
            session["batch_id"] = batch_id
            session["requested_profiles"] = list(report.PROFILES)
            session["profile"] = profile
            session["identity"]["profile"] = profile
            session["identity"]["uart_device_requested"] = "/dev/serial/by-id/test-dock-uart"
            sessions.append(session)

        rows = {}
        for profile in report.PROFILES:
            rows[profile] = {
                "build_status": "passed",
                "resources": None,
                "benchmark_metadata": {
                    "status": "passed",
                    "profile": profile,
                    "coremark": {"commit": "coremark-commit"},
                    "source_fingerprint": f"{profile}-fingerprint",
                    "compiler_flags": ["-O2", f"-march={profile}"],
                    "images": {},
                    "cache": {"data_cache": "enabled", "instruction_cache": "enabled"},
                    "iteration_calibration": {
                        "method": "fixed_iteration_port_calibration_with_fresh_upstream_initialization",
                        "calibration_iterations": 1000,
                        "target_seconds": 20,
                        "formula": "ceil(calibration_iterations * target_seconds * clock_hz / calibration_ticks)",
                        "scored_pass_reinitializes_static_algorithm_data": True,
                    },
                },
                "build_metadata": {"status": "passed"},
            }

        def fake_revalidate(source, _rows):
            checked = copy.deepcopy(source)
            passed = source["profile"] != "lite"
            checked["report_validation"] = {
                "status": "passed" if passed else "failed",
                "errors": [] if passed else ["fixture capture rejected"],
            }
            for trial in checked["trials"]:
                trial["reparsed"] = copy.deepcopy(trial["parsed"]) if passed else None
            checked["aggregate"] = (
                {"count": 3, "coremark_mean": 10.0, "coremark_min": 9.0, "coremark_max": 11.0,
                 "coremark_spread": 2.0, "coremark_per_mhz_mean": 0.2,
                 "coremark_per_mhz_min": 0.18, "coremark_per_mhz_max": 0.22,
                 "coremark_per_mhz_spread": 0.04}
                if passed else None
            )
            return checked

        results_path = self.root / "docs/performance/results.json"
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(json.dumps({"schema_version": 1, "sessions": sessions}))
        with (
            patch.object(report, "ROOT", self.root),
            patch.object(report, "profile_build_rows", return_value=rows),
            patch.object(report, "read_json", return_value={}),
            patch.object(report, "git_identity", return_value={"revision": "test", "dirty": False}),
            patch.object(report, "revalidate_session", side_effect=fake_revalidate),
        ):
            report.create_report()

        results = json.loads(results_path.read_text())
        batch = results["latest_batch_summary"]
        self.assertEqual(batch["batch_id"], batch_id)
        self.assertEqual(batch["status"], "failed")
        self.assertEqual(set(results["latest_batch_aggregates"]), set(report.PROFILES))
        self.assertIsNotNone(results["latest_batch_aggregates"]["minimal"])
        self.assertIsNone(results["latest_batch_aggregates"]["lite"])
        self.assertIsNotNone(results["latest_batch_aggregates"]["standard"])
        rendered = (self.root / "docs/performance.md").read_text()
        self.assertIn("INCOMPLETE — PROFILE=ALL batch failed", rendered)
        self.assertIn("### `minimal`", rendered)
        self.assertIn("### `lite`", rendered)
        self.assertIn("### `standard`", rendered)
        self.assertIn("CoreMark/MHz", rendered)
        self.assertIn("PROFILE=ALL PORT=/dev/serial/by-id/test-dock-uart", rendered)
        self.assertIn("- Cache configuration:", rendered)
        self.assertIn("- Validation firmware:", rendered)

    def test_incomplete_batch_report_does_not_fill_from_prior_profile_session(self):
        batch_id = "20260930T120000.000000Z-all"
        batch_sessions = []
        for profile in list(report.PROFILES)[:2]:
            session = copy.deepcopy(self.session)
            session["session_id"] = f"{batch_id}-{profile}"
            session["batch_id"] = batch_id
            session["requested_profiles"] = list(report.PROFILES)
            session["profile"] = profile
            session["identity"]["profile"] = profile
            batch_sessions.append(session)
        prior_standard = copy.deepcopy(self.session)
        prior_standard["session_id"] = "old-standard-session"
        prior_standard["profile"] = "standard"
        prior_standard.pop("batch_id", None)
        sessions = [prior_standard, *batch_sessions]
        rows = {
            profile: {
                "build_status": "passed",
                "resources": None,
                "benchmark_metadata": {"profile": profile, "images": {}},
                "build_metadata": {},
            }
            for profile in report.PROFILES
        }
        calls = []

        def fake_revalidate(source, _rows):
            calls.append(source["session_id"])
            checked = copy.deepcopy(source)
            checked["report_validation"] = {"status": "passed", "errors": []}
            for trial in checked["trials"]:
                trial["reparsed"] = copy.deepcopy(trial["parsed"])
            checked["aggregate"] = {"count": 3, "coremark_mean": 10.0, "coremark_min": 9.0,
                                     "coremark_max": 11.0, "coremark_spread": 2.0,
                                     "coremark_per_mhz_mean": 0.2, "coremark_per_mhz_min": 0.18,
                                     "coremark_per_mhz_max": 0.22, "coremark_per_mhz_spread": 0.04}
            return checked

        results_path = self.root / "docs/performance/results.json"
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(json.dumps({"schema_version": 1, "sessions": sessions}))
        with (
            patch.object(report, "ROOT", self.root),
            patch.object(report, "profile_build_rows", return_value=rows),
            patch.object(report, "read_json", return_value={}),
            patch.object(report, "git_identity", return_value={"revision": "test", "dirty": False}),
            patch.object(report, "revalidate_session", side_effect=fake_revalidate),
        ):
            report.create_report()

        self.assertEqual(calls, [f"{batch_id}-minimal", f"{batch_id}-lite"])
        results = json.loads(results_path.read_text())
        self.assertEqual(results["latest_batch_summary"]["status"], "incomplete")
        self.assertEqual(results["latest_batch_summary"]["missing_profiles"], ["standard", "performance", "linux"])
        self.assertIsNone(results["latest_batch_aggregates"]["standard"])
        rendered = (self.root / "docs/performance.md").read_text()
        self.assertIn("No session was recorded for this profile in the current batch", rendered)
        self.assertIn("No current-batch `standard` UART capture is available", rendered)
        self.assertIn("Historical logs were not used as a substitute", rendered)
        self.assertIn("not run (missing batch session)", rendered)


if __name__ == "__main__":
    unittest.main()
