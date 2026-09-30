import io
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from benchmark_run import RecordingPort, SerialOwner  # noqa: E402
from litex.tools.litex_term import LiteXTerm  # noqa: E402


class FakePort:
    def __init__(self):
        self.closed = False

    def read(self, size=1):
        time.sleep(0.005)
        return b""

    def close(self):
        self.closed = True


class FakeTerm:
    def __init__(self):
        self.port = FakePort()
        self.reader_alive = False
        self.mem_regions = {}
        self.serial_boot = True


class BenchmarkRunnerTests(unittest.TestCase):
    def test_recording_port_preserves_uart_receive_and_transmit_bytes(self):
        class Port:
            def __init__(self):
                self.timeout = 0.1
                self.write_timeout = 2.0

            def read(self, size=1):
                return b"rx"

            def write(self, data):
                return len(data)

        received = bytearray()
        transmitted = bytearray()
        port = RecordingPort(Port(), received.extend, transmitted.extend)
        self.assertEqual(port.read(2), b"rx")
        self.assertEqual(port.write(b"tx"), 2)
        self.assertEqual(received, b"rx")
        self.assertEqual(transmitted, b"tx")

    def test_recording_port_forwards_litex_timeout_updates(self):
        class Port:
            timeout = 0.1
            write_timeout = 2.0

        serial_port = Port()
        port = RecordingPort(serial_port, lambda data: None)
        self.assertEqual(port.timeout, 0.1)
        self.assertEqual(port.write_timeout, 2.0)

        # LiteXTerm sets these around SFL frames and response reads.
        port.write_timeout = 1.5
        port.timeout = 1.0

        self.assertEqual(serial_port.write_timeout, 1.5)
        self.assertEqual(serial_port.timeout, 1.0)

    def test_pinned_litex_sfl_uses_underlying_serial_timeouts(self):
        class Port:
            timeout = 0.1
            write_timeout = 2.0

            def read(self, size=1):
                if self.timeout != 1.0:
                    raise AssertionError(f"LiteX timeout was not applied: {self.timeout}")
                return b"K"

            def write(self, data):
                if self.write_timeout != 1.5:
                    raise AssertionError(f"LiteX write timeout was not applied: {self.write_timeout}")
                return len(data)

        serial_port = Port()
        term = LiteXTerm.__new__(LiteXTerm)
        term.port = RecordingPort(serial_port, lambda data: None)

        self.assertEqual(term.read_sfl_reply(timeout=1.0), b"K")
        self.assertEqual(serial_port.timeout, 0.1)
        self.assertEqual(term.write_sfl_data(b"frame", timeout=1.5), 5)
        self.assertEqual(serial_port.write_timeout, 2.0)

    def test_handshake_timeout_stops_reader_and_closes_uart(self):
        term = FakeTerm()
        owner = SerialOwner(term, io.BytesIO())
        owner.start()
        self.assertFalse(owner.wait_for(owner.start_seen, 0.02, "BENCHMARK_START"))
        owner.stop()
        self.assertFalse(owner.thread.is_alive())
        self.assertTrue(term.port.closed)

    def test_startup_wait_detects_ansi_litex_console_prompt(self):
        owner = SerialOwner(FakeTerm(), io.BytesIO())
        owner._record(b"\x1b[92;1mlitex\x1b[0m> ")

        self.assertTrue(owner.console_prompt_seen.is_set())
        self.assertEqual(owner.wait_for_start_or_console(0.01), "console")

    def test_startup_recovery_is_blocked_after_start_marker_prefix(self):
        owner = SerialOwner(FakeTerm(), io.BytesIO())
        owner._record(b"\rBENCHMARK_START ")
        owner._record(b"profile=standard")
        owner._record(b"\x1b[92;1mlitex\x1b[0m> ")

        self.assertTrue(owner.start_marker_started.is_set())
        self.assertEqual(owner.wait_for_start_or_console(0.01), "timeout")

    def test_console_prompt_allows_recovery_after_reader_error(self):
        owner = SerialOwner(FakeTerm(), io.BytesIO())
        owner._record(b"\x1b[92;1mlitex\x1b[0m> ")
        owner.error = OSError("old UART handle failed")

        self.assertEqual(owner.wait_for_start_or_console(0.01), "console")

    def test_startup_recovery_is_blocked_during_port_calibration(self):
        owner = SerialOwner(FakeTerm(), io.BytesIO())
        owner._record(b"\rBENCHMARK_CALIBRATION_START profile=standard\n")
        owner._record(b"\x1b[92;1mlitex\x1b[0m> ")
        self.assertTrue(owner.start_marker_started.is_set())
        self.assertTrue(owner.start_seen.is_set())
        self.assertEqual(owner.wait_for_start_or_console(0.01), "started")

    def test_port_failure_is_reported_without_waiting_for_timeout(self):
        owner = SerialOwner(FakeTerm(), io.BytesIO())
        owner._record(b"BENCHMARK_PORT_ERROR SRAM preflight failed\n")
        with self.assertRaisesRegex(RuntimeError, "SRAM preflight failed"):
            owner.wait_for_start_or_console(1.0)


if __name__ == "__main__":
    unittest.main()
