import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from benchmark_results import CaptureValidationError, parse_capture  # noqa: E402


def fixture(mode="performance", *, profile="standard", clock_hz=48_000_000,
            elapsed_ticks=(1 << 32) + 480_000_000):
    build_id = "ab12cd34ef56ab78"
    seeds = ("0", "0", "0x66") if mode == "performance" else ("13333", "13333", "0x66")
    seedcrc, list_crc, matrix_crc, state_crc = (
        ("e9f5", "e714", "1fd7", "8e3a") if mode == "performance"
        else ("18f2", "e3c1", "0747", "8d84")
    )
    iterations = 1000
    elapsed_seconds = elapsed_ticks / clock_hz
    upstream_seconds = int(elapsed_seconds)
    rate = iterations // upstream_seconds
    low = elapsed_ticks & 0xffffffff
    high = elapsed_ticks >> 32
    result = f"""BENCHMARK_START profile={profile} build_id={build_id} mode={mode} clock_hz={clock_hz} data_size=2000 contexts=1 seed1={seeds[0]} seed2={seeds[1]} seed3={seeds[2]}
BENCHMARK_MEMORY code=main_ram data=bss=onchip_sram cache=icache=enabled,dcache=enabled
2K performance run parameters for coremark.
CoreMark Size    : 666
Total ticks      : {low}
Total time (secs): {upstream_seconds}
Iterations/Sec   : {rate}
Iterations       : {iterations}
Compiler version : GCC 15.2.0
Compiler flags   : -O2 -march=rv32i2p0_m -mabi=ilp32
Memory location  : Code in main RAM, static data in on-chip SRAM
seedcrc          : 0x{seedcrc}
[0]crclist       : 0x{list_crc}
[0]crcmatrix     : 0x{matrix_crc}
[0]crcstate      : 0x{state_crc}
[0]crcfinal      : 0x4321
Correct operation validated. See README.md for run and reporting rules.
"""
    result += f"""BENCHMARK_INTERVAL_COUNT count=2 overflow=0
BENCHMARK_CALIBRATION_COUNT count=1
BENCHMARK_CALIBRATION_TICKS index=1 hi=00000000 lo=02dc6c00
BENCHMARK_SCORED_TICKS index=2 hi={high:08x} lo={low:08x}
BENCHMARK_END status=returned profile={profile} build_id={build_id}
"""
    return result, build_id


class BenchmarkResultTests(unittest.TestCase):
    def parse(self, text, **kwargs):
        return parse_capture(text, profile="standard", build_id="ab12cd34ef56ab78", mode="performance", **kwargs)

    def test_valid_capture_and_64_bit_ticks(self):
        capture, _ = fixture()
        parsed = self.parse(capture)
        self.assertEqual(parsed["elapsed_ticks"], (1 << 32) + 480_000_000)
        self.assertGreater(parsed["elapsed_ticks"], 0xffffffff)
        self.assertEqual(parsed["status"], "passed")
        self.assertIsNone(parsed["upstream_coremark"])
        self.assertEqual(parsed["upstream_iterations_per_second"], 10)

    def test_lfcr_bios_prefix_and_fixture_parse_without_changing_raw_evidence(self):
        bios_path = (Path(__file__).resolve().parents[1] / "docs/performance"
                     / "20260930T034854.782554Z-standard-validation.uart.bin")
        bios_capture = bios_path.read_bytes()
        self.assertIn(b"\n\r", bios_capture)

        capture, _ = fixture()
        raw_capture = bios_capture + b"\n\r" + capture.encode("utf-8")
        parsed = self.parse(raw_capture)

        self.assertEqual(parsed["status"], "passed")
        self.assertEqual(parsed["elapsed_ticks"], (1 << 32) + 480_000_000)
        self.assertEqual(bios_path.read_bytes(), bios_capture)

    def test_failed_crc_is_rejected(self):
        capture, _ = fixture()
        capture = capture.replace("[0]crcmatrix     : 0x1fd7", "[0]crcmatrix     : 0x0000")
        with self.assertRaisesRegex(CaptureValidationError, "matrix CRC"):
            self.parse(capture)

    def test_stale_application_failure_before_current_frame_is_not_scored(self):
        capture, _ = fixture()
        previous = capture.replace("build_id=ab12cd34ef56ab78", "build_id=previous")
        previous = previous.replace("Correct operation validated.", "Errors detected")
        parsed = self.parse(previous + "\nBIOS startup\n" + capture)
        self.assertEqual(parsed["build_id"], "ab12cd34ef56ab78")
        self.assertEqual(parsed["status"], "passed")

    def test_incomplete_final_frame_cannot_reuse_an_earlier_valid_frame(self):
        capture, _ = fixture()
        with self.assertRaisesRegex(CaptureValidationError, "BENCHMARK_END"):
            self.parse(capture + "BENCHMARK_START profile=standard\n")

    def test_incomplete_capture_is_rejected(self):
        capture, _ = fixture()
        capture = capture.replace("BENCHMARK_END status=returned profile=standard build_id=ab12cd34ef56ab78\n", "")
        with self.assertRaisesRegex(CaptureValidationError, "BENCHMARK_END"):
            self.parse(capture)

    def test_short_scored_run_is_rejected(self):
        short, _ = fixture(elapsed_ticks=9 * 48_000_000)
        with self.assertRaisesRegex(CaptureValidationError, "shorter than 10 seconds"):
            self.parse(short)

    def test_profile_and_clock_mismatches_are_rejected(self):
        capture, _ = fixture()
        with self.assertRaisesRegex(CaptureValidationError, "profile"):
            parse_capture(capture, profile="lite", build_id="ab12cd34ef56ab78", mode="performance")
        with self.assertRaisesRegex(CaptureValidationError, "clock_hz"):
            self.parse(capture, clock_hz=50_000_000)

    def test_validation_seeds_use_the_upstream_validation_crc(self):
        capture, build_id = fixture("validation")
        parsed = parse_capture(capture, profile="standard", build_id=build_id, mode="validation")
        self.assertEqual(parsed["seedcrc"], 0x18F2)
        self.assertIsNone(parsed["upstream_coremark"])

    def test_fresh_invocation_calibration_is_recalculated_independently(self):
        capture, _ = fixture()
        marker = "BENCHMARK_CALIBRATION_PLAN iterations=50 target_seconds=20 scored_iterations=1000 method=fresh_invocation\n"
        capture = capture.replace("BENCHMARK_MEMORY ", marker + "BENCHMARK_MEMORY ")
        parsed = self.parse(capture)
        self.assertEqual(parsed["calibration"][0]["iterations"], 50)
        self.assertEqual(parsed["calibration_plan"]["scored_iterations"], 1000)
        with self.assertRaisesRegex(CaptureValidationError, "independently calculated calibration"):
            self.parse(capture.replace("iterations=50 target_seconds", "iterations=51 target_seconds"))

    def test_runtime_firmware_checksum_must_match_built_image(self):
        capture, _ = fixture()
        marker = "BENCHMARK_IMAGE_CRC32 build_id=ab12cd34ef56ab78 value=1234abcd size=22000\n"
        parsed = self.parse(marker + capture, image_crc32="1234abcd", image_bytes=22000)
        self.assertEqual(parsed["image_crc32"], 0x1234abcd)
        with self.assertRaisesRegex(CaptureValidationError, "checksum or size"):
            self.parse(marker + capture, image_crc32="1234abce", image_bytes=22000)

    def test_post_workload_image_rechecks_reject_cache_or_memory_corruption(self):
        capture, _ = fixture()
        image = "BENCHMARK_IMAGE_CRC32 build_id=ab12cd34ef56ab78 value=1234abcd size=22000\n"
        template = ("BENCHMARK_IMAGE_RECHECK phase={} cached=1234abcd flushed=1234abcd "
                    "expected=1234abcd size=22000 cached_size=22000 flushed_size=22000\n")
        capture = image + template.format("calibration") + capture.replace(
            "BENCHMARK_END ", template.format("scored") + "BENCHMARK_END ")
        parsed = self.parse(capture, image_crc32="1234abcd", image_bytes=22000)
        self.assertEqual(len(parsed["image_rechecks"]), 2)
        with self.assertRaisesRegex(CaptureValidationError, "image changed"):
            self.parse(capture.replace("cached=1234abcd", "cached=1234abce", 1),
                       image_crc32="1234abcd", image_bytes=22000)


if __name__ == "__main__":
    unittest.main()
