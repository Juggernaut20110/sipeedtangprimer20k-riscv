import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.peripheral_evidence import board_identity, validate_session


class PeripheralEvidenceTests(unittest.TestCase):
    def make_session(self, root, complete_sd=True):
        session = root / "docs/peripherals/evidence/session-1"
        session.mkdir(parents=True)
        artifacts = []
        for name in ("uart_rx", "uart_tx", "programmer_log"):
            path = session / f"{name}.bin"
            path.write_bytes((name + " capture\n").encode())
            artifacts.append({"name": name, "path": path.name,
                              "bytes": path.stat().st_size,
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        build_record = {"source_fingerprint": "source-current"}
        identity = {
            "profile": "standard", "memory": "onchip", "sdcard": "spi", "ethernet": "none",
            "build_record": build_record,
            "artifact_hashes": {"bitstream": "a" * 64, "firmware": "b" * 64},
        }
        sd_measurements = {
            "cid": "cid", "csd": "csd", "sector_count": 1000,
            "init_hz": 400000, "transfer_hz": 12000000,
            "verified_file_bytes": [512, 4096, 1048576],
            "integrity_mismatches": 0, "missing_card_bounded_recovery": True,
        }
        if not complete_sd:
            sd_measurements.pop("verified_file_bytes")
        recovery = {
            "sram_programmed": True, "ddr_held_in_reset": True,
            "sd_deselected": True, "sd_clock_stopped": True, "phy_held_in_reset": True,
            "uart_bytes": 0, "uart_quiet_seconds": 5,
        }
        board = {"model": "Sipeed Tang Primer 20K", "serial": "unique-1", "revision": "rev-1"}
        for name, data in (("recovery_bitstream", b"safe idle bitstream"),
                           ("recovery_uart", b""), ("recovery_programmer", b"programmed SRAM")):
            path = session / name; path.write_bytes(data)
            artifacts.append({"name": name, "path": path.name, "bytes": len(data),
                              "sha256": hashlib.sha256(data).hexdigest()})
        recovery["idle_bitstream_sha256"] = artifacts[-3]["sha256"]
        record = {"board": board, "status": "passed", "programmer_exit_code": 0,
                  "idle_bitstream_sha256": recovery["idle_bitstream_sha256"]}
        path = session / "recovery.json"; path.write_text(json.dumps(record))
        artifacts.append({"name": "recovery_record", "path": path.name, "bytes": path.stat().st_size,
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        manifest = {
            "schema_version": 1,
            "board": {"model": "Sipeed Tang Primer 20K", "serial": "unique-1", "revision": "rev-1"},
            "build": {
                "profile": "standard", "memory": "onchip", "sdcard": "spi", "ethernet": "none",
                "source_fingerprint": "source-current",
                "bitstream_sha256": "a" * 64, "firmware_sha256": "b" * 64,
            },
            "artifacts": artifacts,
            "criteria": {
                "sd": {"status": "passed", "measurements": sd_measurements,
                       "evidence": ["uart_rx"]},
                "final_recovery": {"status": "passed", "measurements": recovery,
                                   "evidence": ["uart_rx"]},
            },
        }
        (session / "manifest.json").write_text(json.dumps(manifest))
        return session, identity

    def test_incomplete_sd_workload_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session, identity = self.make_session(root, complete_sd=False)
            result = validate_session(session, identity, root)
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["criteria"]["sd"], "incomplete_or_invalid")
            self.assertEqual(result["criteria"]["final_recovery"], "passed")

    def test_complete_hashed_session_matches_exact_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session, identity = self.make_session(root)
            result = validate_session(session, identity, root)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["criteria"]["sd"], "passed")

    def test_explicitly_unmarked_board_has_honest_revision_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session, identity = self.make_session(root)
            manifest_path = session / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["board"] = board_identity("unavailable", "core v3961; Dock v3714")
            record_path = session / "recovery.json"
            record = json.loads(record_path.read_text()); record["board"] = manifest["board"]
            record_path.write_text(json.dumps(record))
            for artifact in manifest["artifacts"]:
                if artifact["name"] == "recovery_record":
                    artifact.update(bytes=record_path.stat().st_size,
                                    sha256=hashlib.sha256(record_path.read_bytes()).hexdigest())
            manifest_path.write_text(json.dumps(manifest))
            self.assertIsNone(manifest["board"]["serial"])
            self.assertEqual(validate_session(session, identity, root)["status"], "passed")
            manifest["board"].pop("serial_status")
            manifest_path.write_text(json.dumps(manifest))
            self.assertTrue(validate_session(session, identity, root)["errors"])

    def test_generic_debugger_name_does_not_identify_board(self):
        with self.assertRaises(ValueError):
            board_identity("FactoryAIOT Pro", "core v3961; Dock v3714")

    def test_changed_capture_invalidates_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session, identity = self.make_session(root)
            (session / "uart_rx.bin").write_text("changed")
            result = validate_session(session, identity, root)
            self.assertEqual(result["status"], "incomplete")
            self.assertTrue(result["errors"])

    def test_firmware_refresh_reuses_only_exact_hardware_ddr_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session, identity = self.make_session(root)
            manifest_path = session / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            identity["memory"] = manifest["build"]["memory"] = "ddr3"
            manifest["build"].update(source_fingerprint="source-old", firmware_sha256="d" * 64)
            inputs = {"design.v": "unchanged-generated-hardware"}
            identity["build_record"].update(synthesis_inputs=inputs, gateware_reuse_history=[{
                "previous_source_fingerprint": "source-old", "previous_firmware_sha256": "d" * 64,
                "bitstream_sha256": "a" * 64, "synthesis_inputs": inputs}])
            manifest["criteria"]["ddr_qualification"] = {"status": "passed", "evidence": ["uart_rx"],
                "measurements": {"training_passes": 10, "tested_bytes": 134217728,
                    "pattern_errors": 0, "cache_visibility_errors": 0, "stress_seconds": 1800}}
            manifest_path.write_text(json.dumps(manifest))
            result = validate_session(session, identity, root)
            self.assertEqual(result["criteria"]["ddr_qualification"], "passed")
            self.assertEqual(result["criteria"]["sd"], "incomplete_or_invalid")
            identity["artifact_hashes"]["bitstream"] = "e" * 64
            result = validate_session(session, identity, root)
            self.assertEqual(result["criteria"]["ddr_qualification"], "incomplete_or_invalid")

    def test_session_outside_evidence_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            result = validate_session(outside, {}, root)
            self.assertEqual(result["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
