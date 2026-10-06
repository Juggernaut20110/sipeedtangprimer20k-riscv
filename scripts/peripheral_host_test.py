#!/usr/bin/env python3
"""Exercise the firmware UDP/TCP echo services and SD upload from a host."""

import argparse
import hashlib
import json
import socket
import struct
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path


def pattern(length, seed=0x5A):
    return bytes(((index * 37 + (index >> 8) + seed) & 0xFF)
                 for index in range(length))


def recv_exact(connection, length):
    chunks = bytearray()
    while len(chunks) < length:
        block = connection.recv(min(65536, length - len(chunks)))
        if not block:
            raise RuntimeError(f"peer closed after {len(chunks)} of {length} bytes")
        chunks.extend(block)
    return bytes(chunks)


def run(args):
    report = {
        "schema_version": 1,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "target": args.host,
        "status": "in_progress",
        "udp_payload_bytes": [],
        "tcp_payload_bytes": [],
        "payload_mismatches": 0,
        "reconnects": 0,
    }
    args.progress_report = report
    started = time.monotonic()
    echo_started = started
    if args.ping:
        import subprocess
        result = subprocess.run([args.ping_program, "-c", "1", "-W", str(args.timeout), args.host],
                                capture_output=True, text=True, timeout=args.timeout + 2)
        report["ping"] = result.returncode == 0
        report["ping_output"] = (result.stdout + result.stderr)[-4000:]
        if result.returncode:
            raise RuntimeError("ICMP ping failed; ARP/network setup should be checked before payload tests")

    for size in args.sizes:
        payload = pattern(size, args.seed)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(args.timeout)
            client.connect((args.host, args.udp_port))
            if client.send(payload) != len(payload):
                raise RuntimeError(f"short UDP send for {size}-byte payload")
            echoed = client.recv(max(2048, size + 1))
        report["udp_payload_bytes"].append(size)
        if echoed != payload:
            report["payload_mismatches"] += 1
            raise RuntimeError(f"UDP echo mismatch for {size}-byte payload")

        with socket.create_connection((args.host, args.tcp_port), timeout=args.timeout) as client:
            client.settimeout(args.timeout)
            client.sendall(payload)
            echoed = recv_exact(client, size)
        report["tcp_payload_bytes"].append(size)
        report["reconnects"] += 1
        if echoed != payload:
            report["payload_mismatches"] += 1
            raise RuntimeError(f"TCP echo mismatch for {size}-byte payload")

    echo_elapsed = time.monotonic() - echo_started
    report.update(echo_elapsed_seconds=echo_elapsed,
                  echo_payload_bytes=sum(args.sizes)*2,
                  echo_throughput_bytes_per_second=sum(args.sizes)*2/echo_elapsed)
    if not getattr(args, "no_upload", False):
        upload_started = time.monotonic()
        upload = pattern(args.upload_bytes, args.seed)
        expected_crc = zlib.crc32(upload) & 0xFFFFFFFF
        with socket.create_connection((args.host, args.upload_port), timeout=args.timeout) as client:
            client.settimeout(max(args.timeout, 600))
            client.sendall(struct.pack(">II", len(upload), expected_crc))
            for offset in range(0, len(upload), args.chunk_bytes):
                client.sendall(upload[offset:offset + args.chunk_bytes])
            response = recv_exact(client, 10)
        status, actual_bytes, actual_crc = response[:2], *struct.unpack(">II", response[2:])
        if status != b"OK" or actual_bytes != len(upload) or actual_crc != expected_crc:
            raise RuntimeError(
                f"SD upload failed: status={status!r}, bytes={actual_bytes}, crc={actual_crc:08x}, expected={expected_crc:08x}"
            )
        report.update({
            "upload_bytes": len(upload),
            "upload_crc32": f"{expected_crc:08x}",
            "upload_card_crc32": f"{actual_crc:08x}",
            "upload_card_bytes": actual_bytes,
            "upload_sha256": hashlib.sha256(upload).hexdigest(),
            "upload_status": status.decode("ascii"),
            "upload_elapsed_seconds": time.monotonic() - upload_started,
            "upload_throughput_bytes_per_second": len(upload)/(time.monotonic()-upload_started),
        })
    else:
        report["upload_status"] = "not_requested"
    elapsed = time.monotonic() - started
    report.update({
        "elapsed_seconds": elapsed,
        "throughput_bytes_per_second": (sum(args.sizes)*2 + report.get("upload_bytes",0)) / elapsed if elapsed else 0,
        "reconnect_recovered": True,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed",
    })
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="explicit target IPv4 address or hostname")
    parser.add_argument("--udp-port", type=int, default=5001)
    parser.add_argument("--tcp-port", type=int, default=5002)
    parser.add_argument("--upload-port", type=int, default=5003)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1, 64, 512, 1472])
    parser.add_argument("--no-upload", action="store_true", help="test Ethernet echo without SD writes")
    parser.add_argument("--upload-bytes", type=int, default=1_048_576)
    parser.add_argument("--chunk-bytes", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=0x5A)
    parser.add_argument("--ping", action="store_true", help="also require one successful system ping")
    parser.add_argument("--ping-program", default="ping")
    parser.add_argument("--output", type=Path, help="write a JSON result file")
    args = parser.parse_args(argv)
    try:
        if not args.sizes or any(size < 1 or size > 1472 for size in args.sizes):
            raise ValueError("echo sizes must be between 1 and 1472 bytes")
        if args.upload_bytes < 1 or args.chunk_bytes < 1:
            raise ValueError("upload and chunk lengths must be positive")
        result = run(args)
    except (OSError, RuntimeError, ValueError, socket.timeout) as error:
        result = getattr(args, "progress_report", {"target": args.host})
        result.update(status="failed", error=f"{type(error).__name__}: {error}",
                      finished_utc=datetime.now(timezone.utc).isoformat())
        print(json.dumps(result, indent=2))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + "\n")
        return 1
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
