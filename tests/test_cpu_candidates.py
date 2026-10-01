import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cpu_candidates import generator_arguments  # noqa: E402
from gateware.soc import PROFILES  # noqa: E402
from build import read_performance_selection  # noqa: E402


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
        self.assertEqual(list(PROFILES), ["minimal", "lite", "standard", "performance"])
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


if __name__ == "__main__":
    unittest.main()
