import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cpu_candidates import generator_arguments  # noqa: E402
from gateware.soc import PROFILES  # noqa: E402
from build import read_performance_selection  # noqa: E402
from cpu_profiles import configuration_for  # noqa: E402


class CpuCandidateTests(unittest.TestCase):
    def test_only_branch_prediction_differs_between_candidates(self):
        dynamic = generator_arguments("dynamic", Path("/tmp/dynamic/VexRiscv.v"))
        dynamic_target = generator_arguments("dynamic_target", Path("/tmp/target/VexRiscv.v"))

        def comparable(arguments):
            return [
                argument for argument in arguments
                if not argument.startswith("--prediction=")
                and not argument.startswith("--outputFile=")
            ]

        self.assertEqual(comparable(dynamic), comparable(dynamic_target))
        self.assertIn("--prediction=dynamic", dynamic)
        self.assertIn("--prediction=dynamic_target", dynamic_target)

    def test_measured_performance_profile_uses_project_owned_standard_shell(self):
        self.assertEqual(list(PROFILES), ["minimal", "lite", "standard", "performance", "linux"])
        self.assertEqual(PROFILES["performance"], "standard")
        selection = read_performance_selection()
        self.assertEqual(selection["candidate"], "dynamic_target")
        self.assertGreater(
            selection["evaluation"]["winner"]["coremark_mean"],
            selection["evaluation"]["winner"]["standard_mean"],
        )

    def test_unknown_candidate_is_rejected(self):
        with self.assertRaises(ValueError):
            generator_arguments("full", Path("/tmp/VexRiscv.v"))

    def test_provisional_maxperf_configuration_is_bound_to_one_memory_mode(self):
        candidate = {
            "candidate_id": "icache-4096_dcache-4096_rv32imc",
            "memory_mode": "ddr3",
            "rtl_sha256": "a" * 64,
            "cpu_configuration": {
                "cpu_variant": "projectimc", "isa": "rv32i2p0_mc",
                "compressed": True, "instruction_cache_bytes": 4096,
                "data_cache_bytes": 4096, "prediction": "dynamic_target",
                "clock_hz": 48_000_000,
            },
        }
        provisional = {
            "status": "provisional",
            "memory_modes": {"ddr3": {"status": "provisional", "candidate_id": candidate["candidate_id"]}},
        }
        configured = configuration_for(
            "maxperf", cpu_candidate=candidate["candidate_id"],
            candidate_manifest=candidate, selection=provisional,
        )
        self.assertEqual(configured["isa"], "rv32i2p0_mc")
        self.assertTrue(configured["compressed"])
        self.assertEqual(configured["rtl_sha256"], "a" * 64)
        provisional["memory_modes"]["ddr3"]["candidate_id"] = "other"
        with self.assertRaises(RuntimeError):
            configuration_for(
                "maxperf", cpu_candidate=candidate["candidate_id"],
                candidate_manifest=candidate, selection=provisional,
            )


if __name__ == "__main__":
    unittest.main()
