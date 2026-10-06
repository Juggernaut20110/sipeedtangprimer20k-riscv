import socket
import threading
import unittest
from types import SimpleNamespace

from scripts.peripheral_host_test import recv_exact, run


def free_port(sock_type):
    with socket.socket(socket.AF_INET, sock_type) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class PeripheralHostRunnerTests(unittest.TestCase):
    def test_host_runner_protocol_against_local_echo_fixture(self):
        udp_port = free_port(socket.SOCK_DGRAM)
        tcp_port = free_port(socket.SOCK_STREAM)
        upload_port = free_port(socket.SOCK_STREAM)
        stop = threading.Event()
        ready = [threading.Event() for _ in range(3)]
        errors = []

        def serve_udp():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
                    server.bind(("127.0.0.1", udp_port))
                    ready[0].set()
                    server.settimeout(0.1)
                    while not stop.is_set():
                        try:
                            data, peer = server.recvfrom(2048)
                        except socket.timeout:
                            continue
                        server.sendto(data, peer)
            except Exception as error:
                errors.append(error)

        def serve_tcp_echo():
            try:
                sizes = [1, 64, 512, 1472]
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    server.bind(("127.0.0.1", tcp_port))
                    server.listen()
                    ready[1].set()
                    server.settimeout(0.1)
                    for size in sizes:
                        while True:
                            try:
                                connection, _ = server.accept()
                                break
                            except socket.timeout:
                                if stop.is_set():
                                    return
                        with connection:
                            data = recv_exact(connection, size)
                            connection.sendall(data)
            except Exception as error:
                errors.append(error)

        def serve_upload():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    server.bind(("127.0.0.1", upload_port))
                    server.listen(1)
                    ready[2].set()
                    server.settimeout(0.1)
                    while True:
                        try:
                            connection, _ = server.accept()
                            break
                        except socket.timeout:
                            if stop.is_set():
                                return
                    with connection:
                        header = recv_exact(connection, 8)
                        length = int.from_bytes(header[:4], "big")
                        expected_crc = int.from_bytes(header[4:], "big")
                        payload = recv_exact(connection, length)
                        import zlib
                        actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
                        if actual_crc != expected_crc:
                            raise AssertionError("fixture got an invalid CRC")
                        connection.sendall(b"OK" + length.to_bytes(4, "big")
                                           + actual_crc.to_bytes(4, "big"))
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=serve_udp, daemon=True),
                   threading.Thread(target=serve_tcp_echo, daemon=True),
                   threading.Thread(target=serve_upload, daemon=True)]
        for thread in threads:
            thread.start()
        self.assertTrue(all(event.wait(2) for event in ready), "local host-test servers did not start")
        try:
            result = run(SimpleNamespace(
                host="127.0.0.1", udp_port=udp_port, tcp_port=tcp_port,
                upload_port=upload_port, timeout=2.0,
                sizes=[1, 64, 512, 1472], upload_bytes=1_048_576,
                chunk_bytes=4096, seed=0x5A, ping=False,
            ))
        finally:
            stop.set()
        for thread in threads:
            thread.join(timeout=1)
        self.assertFalse(errors, errors)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["udp_payload_bytes"], [1, 64, 512, 1472])
        self.assertEqual(result["tcp_payload_bytes"], [1, 64, 512, 1472])
        self.assertEqual(result["upload_bytes"], 1_048_576)
        self.assertEqual(result["upload_status"], "OK")
        self.assertTrue(result["reconnect_recovered"])


if __name__ == "__main__":
    unittest.main()
