import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ddr_test_run  # noqa: E402
from memory import profile_build_dir, validate_memory  # noqa: E402


def words(value):
    return f"{value >> 32:08x}", f"{value & 0xffffffff:08x}"


def full_capture(stress_seconds=1):
    base = 0xC0000000
    end = base + ddr_test_run.DDR_SIZE_BYTES
    word_count = ddr_test_run.DDR_SIZE_BYTES // 4
    phases = (
        "walking_ones", "walking_zeros", "fixed_pattern", "inverted_pattern",
        "address_pattern", "deterministic_pseudorandom",
    )
    lines = [
        "DDR_TEST_START profile=minimal memory=ddr3 clock_hz=48000000 "
        "ddr_bytes=268435456 cached_base=0x40000000 uncached_base=0xc0000000 "
        "l2_bytes=8192 stress_seconds={}".format(stress_seconds),
    ]
    for name in phases:
        lines.extend([
            f"DDR_TEST_PHASE_START name={name} range_start={base:#x} range_end={end:#x} coverage_bytes=268435456",
            f"DDR_TEST_PHASE_END name={name} status=passed bytes=268435456 operations={word_count} elapsed_ticks_hi=0 elapsed_ticks_lo=100 errors=0",
        ])
    subword_operations = (ddr_test_run.DDR_SIZE_BYTES // 4096) * 5
    lines.extend([
        f"DDR_TEST_PHASE_START name=address_bank_row_column_alias range_start={base:#x} range_end={end:#x} coverage_bytes=104 alias_offsets=26 geometry=8x16384x1024",
        "DDR_TEST_PHASE_END name=address_bank_row_column_alias status=passed bytes=104 operations=26 elapsed_ticks_hi=0 elapsed_ticks_lo=100 errors=0",
        f"DDR_TEST_PHASE_START name=byte_halfword_neighbor_preservation range_start={base:#x} range_end={end:#x} coverage_bytes=268435456 sample_stride_bytes=4096 samples_per_page=5",
        f"DDR_TEST_PHASE_END name=byte_halfword_neighbor_preservation status=passed bytes=268435456 operations={subword_operations} elapsed_ticks_hi=0 elapsed_ticks_lo=100 errors=0",
        f"DDR_TEST_PHASE_START name=cached_uncached_visibility range_start={base + 1048576:#x} range_end={end:#x} coverage_bytes=512 sample_stride_bytes=4194304 samples=64 cache_maintenance=fence,dflush,l2flush",
        "DDR_TEST_PHASE_END name=cached_uncached_visibility status=passed bytes=512 operations=128 elapsed_ticks_hi=0 elapsed_ticks_lo=100 errors=0",
        f"DDR_TEST_PHASE_START name=sustained_delayed_readback_stress range_start={base:#x} range_end={end:#x} coverage_bytes=268435456",
        f"DDR_TEST_PHASE_END name=sustained_delayed_readback_stress status=passed bytes=268435456 operations={word_count} elapsed_ticks_hi=0 elapsed_ticks_lo={stress_seconds * 48000000} errors=0",
        "DDR_TEST_BANDWIDTH path=uncached_controller_alias transfer_bytes=1048576 clock_hz=48000000 read_bytes_hi=0 read_bytes_lo=1048576 write_bytes_hi=0 write_bytes_lo=1048576 elapsed_ticks_hi=0 elapsed_ticks_lo=48000000 read_bytes_per_second=1048576 write_bytes_per_second=1048576 sweeps=1",
        f"DDR_TEST_STRESS requested_seconds={stress_seconds} actual_seconds_whole={stress_seconds} elapsed_ticks_hi=0 elapsed_ticks_lo={stress_seconds * 48000000}",
        f"DDR_TEST_END status=passed profile=minimal memory=ddr3 phases=10 errors=0 tested_bytes=268435456 elapsed_ticks_hi=0 elapsed_ticks_lo={stress_seconds * 48000000}",
    ])
    return lines


class Ddr3ParserTests(unittest.TestCase):
    def test_memory_selector_is_strict_and_paths_are_isolated(self):
        self.assertEqual(validate_memory("ddr3"), "ddr3")
        with self.assertRaises(ValueError):
            validate_memory("DDR3")
        self.assertEqual(profile_build_dir(ROOT, "standard", "onchip"), ROOT / "build/standard")
        self.assertEqual(profile_build_dir(ROOT, "standard", "ddr3"), ROOT / "build/ddr3/standard")

    def test_training_success_requires_all_lanes_and_result(self):
        lines = [
            "SDRAM_TRAINING_START phy=GW2DDRPHY read_leveling=1 bitslips=4 delays=256",
            "SDRAM_READ_LEVELING_LANE module=0 dq=0 bitslip=2 status=passed window_start=20 window_length=12 delay_center=26 delay_half_window=6",
            "SDRAM_TRAINING_RESULT status=passed",
        ]
        checked = ddr_test_run.validate_training(lines, 1, {
            "SDRAM_PHY_BITSLIPS": 4, "SDRAM_PHY_DELAYS": 256,
        })
        self.assertEqual(checked["status"], "passed")
        failed = list(lines)
        failed[-1] = "SDRAM_TRAINING_RESULT status=failed"
        self.assertEqual(ddr_test_run.validate_training(failed, 1, {
            "SDRAM_PHY_BITSLIPS": 4, "SDRAM_PHY_DELAYS": 256,
        })["status"], "failed")

    def test_full_capture_requires_all_ranges_and_requested_duration(self):
        result = ddr_test_run.validate_full_capture(full_capture(1), "minimal", 1)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["geometry_bytes"], 256 * 1024 * 1024)
        with self.assertRaises(ValueError):
            ddr_test_run.validate_full_capture(full_capture(2), "minimal", 1)
        missing = full_capture(1)
        missing = [line for line in missing if "name=walking_zeros" not in line]
        with self.assertRaises(ValueError):
            ddr_test_run.validate_full_capture(missing, "minimal", 1)


if __name__ == "__main__":
    unittest.main()
