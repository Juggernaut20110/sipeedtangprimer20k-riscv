import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from maxperf_candidates import candidate_matrix, generator_arguments  # noqa: E402
from maxperf_run import candidate_rank, run_ddr_candidate_check  # noqa: E402


class MaxPerfCandidateTests(unittest.TestCase):
    def test_matrix_is_complete_and_candidate_identity_has_caches_and_isa(self):
        onchip = candidate_matrix("onchip")
        ddr3 = candidate_matrix("ddr3")
        self.assertEqual(len(onchip), 8)
        self.assertEqual(len(ddr3), 18)
        self.assertEqual(len({item["candidate_id"] for item in onchip}), 8)
        self.assertEqual(len({item["candidate_id"] for item in ddr3}), 18)
        self.assertEqual(
            {item["cpu_configuration"]["isa"] for item in onchip + ddr3},
            {"rv32i2p0_m", "rv32i2p0_mc"},
        )
        self.assertTrue(all("icache-" in item["candidate_id"] and "dcache-" in item["candidate_id"]
                            for item in onchip + ddr3))

    def test_generator_arguments_hold_all_nonmatrix_settings_locked(self):
        row = next(item for item in candidate_matrix("onchip")
                   if item["candidate_id"] == "icache-4096_dcache-4096_rv32im")
        args = generator_arguments(row, ROOT / "build/maxperf-candidates/onchip/control/VexRiscv.v")
        self.assertIn("--iCacheSize=4096", args)
        self.assertIn("--dCacheSize=4096", args)
        self.assertIn("--prediction=dynamic_target", args)
        self.assertIn("--compressedGen=false", args)
        self.assertIn("--atomics=false", args)
        self.assertIn("--csrPluginConfig=small", args)
        self.assertIn("--bypass=true", args)
        self.assertIn("--singleCycleMulDiv=true", args)
        self.assertIn("--singleCycleShift=true", args)

    def test_unknown_memory_is_rejected(self):
        with self.assertRaises(ValueError):
            candidate_matrix("sram")

    def test_candidate_ranking_uses_score_then_resource_and_id_tie_breaks(self):
        result = {
            "fresh_performance_baseline_mean": 100.0,
            "candidates": {
                "z_candidate": {
                    "status": "passed", "aggregate": {"coremark_mean": 110.0},
                    "resources_and_timing": {"resources": {
                        "bsram": {"used": 46}, "lut": {"used": 9000},
                    }},
                },
                "b_candidate": {
                    "status": "passed", "aggregate": {"coremark_mean": 110.0},
                    "resources_and_timing": {"resources": {
                        "bsram": {"used": 45}, "lut": {"used": 9500},
                    }},
                },
                "a_candidate": {
                    "status": "passed", "aggregate": {"coremark_mean": 110.0},
                    "resources_and_timing": {"resources": {
                        "bsram": {"used": 45}, "lut": {"used": 9500},
                    }},
                },
                "below_baseline": {
                    "status": "passed", "aggregate": {"coremark_mean": 99.0},
                    "resources_and_timing": {"resources": {
                        "bsram": {"used": 1}, "lut": {"used": 1},
                    }},
                },
            },
        }
        ranked = candidate_rank(result, {})
        self.assertEqual([item[0] for item in ranked], ["a_candidate", "b_candidate", "z_candidate"])

    def test_unrecorded_ddr_runner_exception_stops_candidate_batch(self):
        runner = SimpleNamespace(run_profile=Mock(side_effect=RuntimeError("UART disconnected")))
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"ddr_test_run": runner}):
            result = run_ddr_candidate_check(
                "candidate", "standard", Path(directory), "/dev/ttyUSB-test",
                "batch", Path(directory), thorough=False,
            )

        self.assertEqual(result["status"], "failed")
        self.assertIs(result["session"]["batch_continuation_safe"], False)
        self.assertIn("UART disconnected", result["session"]["error"])


if __name__ == "__main__":
    unittest.main()
