import sys
import tempfile
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
        "ddr_bytes=134217728 cached_base=0x40000000 uncached_base=0xc0000000 "
        "l2_bytes=8192 stress_seconds={}".format(stress_seconds),
    ]
    for name in phases:
        lines.extend([
            f"DDR_TEST_PHASE_START name={name} range_start={base:#x} range_end={end:#x} coverage_bytes=134217728",
            f"DDR_TEST_PHASE_END name={name} status=passed bytes=134217728 operations={word_count} elapsed_ticks_hi=0 elapsed_ticks_lo=100 errors=0",
        ])
    subword_operations = (ddr_test_run.DDR_SIZE_BYTES // 4096) * 5
    lines.extend([
        f"DDR_TEST_PHASE_START name=address_bank_row_column_alias range_start={base:#x} range_end={end:#x} coverage_bytes=100 alias_offsets=25 geometry=8x8192x1024",
        "DDR_TEST_PHASE_END name=address_bank_row_column_alias status=passed bytes=100 operations=25 elapsed_ticks_hi=0 elapsed_ticks_lo=100 errors=0",
        f"DDR_TEST_PHASE_START name=byte_halfword_neighbor_preservation range_start={base:#x} range_end={end:#x} coverage_bytes=134217728 sample_stride_bytes=4096 samples_per_page=5",
        f"DDR_TEST_PHASE_END name=byte_halfword_neighbor_preservation status=passed bytes=134217728 operations={subword_operations} elapsed_ticks_hi=0 elapsed_ticks_lo=100 errors=0",
        f"DDR_TEST_PHASE_START name=cached_uncached_visibility range_start={base + 1048576:#x} range_end={end:#x} coverage_bytes=512 sample_stride_bytes=2097152 samples=64 cache_maintenance=fence,dflush,l2flush",
        "DDR_TEST_PHASE_END name=cached_uncached_visibility status=passed bytes=512 operations=128 elapsed_ticks_hi=0 elapsed_ticks_lo=100 errors=0",
        f"DDR_TEST_PHASE_START name=sustained_delayed_readback_stress range_start={base:#x} range_end={end:#x} coverage_bytes=134217728",
        f"DDR_TEST_PHASE_END name=sustained_delayed_readback_stress status=passed bytes=134217728 operations={word_count} elapsed_ticks_hi=0 elapsed_ticks_lo={stress_seconds * 48000000} errors=0",
        "DDR_TEST_BANDWIDTH path=uncached_controller_alias transfer_bytes=1048576 clock_hz=48000000 read_bytes_hi=0 read_bytes_lo=1048576 write_bytes_hi=0 write_bytes_lo=1048576 elapsed_ticks_hi=0 elapsed_ticks_lo=48000000 read_bytes_per_second=1048576 write_bytes_per_second=1048576 sweeps=1",
        f"DDR_TEST_STRESS requested_seconds={stress_seconds} actual_seconds_whole={stress_seconds} elapsed_ticks_hi=0 elapsed_ticks_lo={stress_seconds * 48000000}",
        f"DDR_TEST_END status=passed profile=minimal memory=ddr3 phases=10 errors=0 tested_bytes=134217728 elapsed_ticks_hi=0 elapsed_ticks_lo={stress_seconds * 48000000}",
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
            "SDRAM_PHY_MODULES": 1, "SDRAM_PHY_DQ_DQS_RATIO": 8,
        })
        self.assertEqual(checked["status"], "passed")
        failed = list(lines)
        failed[-1] = "SDRAM_TRAINING_RESULT status=failed"
        self.assertEqual(ddr_test_run.validate_training(failed, 1, {
            "SDRAM_PHY_BITSLIPS": 4, "SDRAM_PHY_DELAYS": 256,
            "SDRAM_PHY_MODULES": 1, "SDRAM_PHY_DQ_DQS_RATIO": 8,
        })["status"], "failed")

    def test_generated_header_selects_byte_or_dq_training(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            header = root / "software/include/generated/sdram_phy.h"
            header.parent.mkdir(parents=True)
            contents = "\n".join((
                "#define SDRAM_PHY_MODULES 2", "#define SDRAM_PHY_DQ_DQS_RATIO 8",
                "#define SDRAM_PHY_DELAYS 256", "#define SDRAM_PHY_BITSLIPS 4",
                "#define SDRAM_PHY_READ_LEVELING_CAPABLE",
            )) + "\n"
            header.write_text(contents)
            self.assertEqual(ddr_test_run.expected_read_lanes(root)[0], 2)
            header.write_text(contents + "#define SDRAM_DELAY_PER_DQ\n")
            self.assertEqual(ddr_test_run.expected_read_lanes(root)[0], 16)

    def test_training_rejects_duplicate_missing_and_unexpected_lanes(self):
        config = {"SDRAM_PHY_MODULES": 2, "SDRAM_PHY_DQ_DQS_RATIO": 8,
                  "SDRAM_PHY_BITSLIPS": 4, "SDRAM_PHY_DELAYS": 256}
        lane = "SDRAM_READ_LEVELING_LANE module={} dq=0 bitslip=2 status=passed window_start=20 window_length=12 delay_center=26 delay_half_window=6"
        lines = ["SDRAM_TRAINING_START phy=GW2DDRPHY", lane.format(0), lane.format(1),
                 "SDRAM_TRAINING_RESULT status=passed"]
        self.assertEqual(ddr_test_run.validate_training(lines, 2, config)["status"], "passed")
        for replacement in (lane.format(0), lane.format(2), lane.format(1).replace("dq=0", "dq=1")):
            invalid = list(lines)
            invalid[2] = replacement
            self.assertEqual(ddr_test_run.validate_training(invalid, 2, config)["status"], "failed")
        self.assertEqual(ddr_test_run.validate_training(lines[:2] + lines[3:], 2, config)["status"], "failed")

    def test_failed_lane_embedded_after_bios_best_prefix_is_preserved(self):
        config = {"SDRAM_PHY_MODULES": 2, "SDRAM_PHY_DQ_DQS_RATIO": 8,
                  "SDRAM_PHY_BITSLIPS": 4, "SDRAM_PHY_DELAYS": 256}
        lines = ["SDRAM_TRAINING_START phy=GW2DDRPHY",
                 "  best: m0, b00 SDRAM_READ_LEVELING_LANE module=0 dq=0 bitslip=0 status=failed window_start=-1 window_length=0",
                 "SDRAM_TRAINING_RESULT status=failed phase=leveling"]
        checked = ddr_test_run.validate_training(lines, 2, config)
        self.assertEqual(checked["status"], "failed")
        self.assertEqual(checked["lane_count"], 1)
        self.assertEqual(checked["lanes"][0]["window_length"], 0)

    def test_full_capture_requires_all_ranges_and_requested_duration(self):
        result = ddr_test_run.validate_full_capture(full_capture(1), "minimal", 1)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["geometry_bytes"], 128 * 1024 * 1024)
        with self.assertRaises(ValueError):
            ddr_test_run.validate_full_capture(full_capture(2), "minimal", 1)
        missing = full_capture(1)
        missing = [line for line in missing if "name=walking_zeros" not in line]
        with self.assertRaises(ValueError):
            ddr_test_run.validate_full_capture(missing, "minimal", 1)

    def test_old_256mib_capture_cannot_qualify_the_fitted_part(self):
        wrong = [line.replace('134217728', '268435456').replace('0xc8000000', '0xd0000000')
                 for line in full_capture(1)]
        with self.assertRaisesRegex(ValueError, 'geometry mismatch'):
            ddr_test_run.validate_full_capture(wrong, 'minimal', 1)

    def test_geometry_and_visibility_sampling_match_the_fitted_hynix_part(self):
        from gateware.ddr3 import H5TQ1G63EFR
        module = H5TQ1G63EFR(48_000_000, '1:2')
        self.assertEqual((module.geom_settings.bankbits, module.geom_settings.rowbits,
                          module.geom_settings.colbits), (3, 13, 10))
        self.assertEqual(ddr_test_run.DDR_SIZE_BYTES, 128 * 1024 * 1024)
        self.assertEqual(ddr_test_run.DDR_ALIAS_OFFSETS, 25)
        self.assertEqual(ddr_test_run.DDR_VISIBILITY_STRIDE, 2 * 1024 * 1024)
        self.assertEqual(ddr_test_run.DDR_VISIBILITY_SAMPLES, 64)


if __name__ == "__main__":
    unittest.main()
