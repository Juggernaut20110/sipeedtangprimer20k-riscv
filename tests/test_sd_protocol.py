import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SDProtocolHostTests(unittest.TestCase):
    def test_production_protocol_helpers(self):
        with tempfile.TemporaryDirectory(prefix="sd-protocol-") as temp:
            binary = Path(temp) / "sd-protocol-test"
            env = os.environ.copy()
            env["PATH"] = "/usr/bin:/bin"
            subprocess.run([
                "/usr/bin/gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                str(ROOT / "firmware/peripherals/sd_protocol.c"),
                str(ROOT / "tests/test_sd_protocol.c"), "-o", str(binary),
            ], check=True, env=env)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertIn("SD protocol helpers passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
