#!/usr/bin/env python3
"""Strict parser and score calculator for captured CoreMark UART output."""

import math
import re
import statistics


class CaptureValidationError(ValueError):
    pass


SEED_EXPECTATIONS = {
    "performance": {
        "seeds": (0, 0, 0x66), "seedcrc": 0xE9F5,
        "crclist": 0xE714, "crcmatrix": 0x1FD7, "crcstate": 0x8E3A,
    },
    "validation": {
        "seeds": (0x3415, 0x3415, 0x66), "seedcrc": 0x18F2,
        "crclist": 0xE3C1, "crcmatrix": 0x0747, "crcstate": 0x8D84,
    },
}


def _one(pattern, text, name, convert=str):
    found = re.findall(pattern, text, re.M)
    if len(found) != 1:
        raise CaptureValidationError(f"expected exactly one {name} field; found {len(found)}")
    try:
        return convert(found[0])
    except (TypeError, ValueError) as error:
        raise CaptureValidationError(f"invalid {name} field: {found[0]!r}") from error


def _close_to_printed(value, printed):
    rendered = printed.strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", rendered):
        raise CaptureValidationError(f"invalid printed CoreMark rate {printed!r}")
    decimal_places = len(rendered.partition(".")[2].split("e")[0].split("E")[0]) if "." in rendered else 0
    tolerance = 0.500001 * (10 ** -decimal_places)
    actual = float(rendered)
    if not math.isfinite(actual) or abs(value - actual) > tolerance:
        raise CaptureValidationError(
            f"host rate {value:.12g} disagrees with upstream rate {actual:.12g} at printed precision"
        )
    return actual


def parse_capture(capture, *, profile, build_id, mode, clock_hz=48_000_000,
                  data_size=2000, contexts=1, image_crc32=None, image_bytes=None):
    """Validate one complete firmware UART log and return raw and derived values."""
    if isinstance(capture, bytes):
        try:
            text = capture.decode("utf-8")
            decode_errors = False
        except UnicodeDecodeError:
            text = capture.decode("utf-8", errors="replace")
            decode_errors = True
    else:
        text = str(capture)
        decode_errors = False
    # The Dock BIOS emits LF-CR while the application printer emits LF. Normalize
    # all common UART line endings in this parsing copy; the runner preserves the
    # original byte stream unchanged as the evidence artifact.
    text = re.sub(r"\r\n|\n\r|\r|\n", "\n", text)
    if mode not in SEED_EXPECTATIONS:
        raise CaptureValidationError(f"unknown benchmark mode {mode!r}")
    # Opening UART before FPGA programming can capture the tail of the previous
    # application. Validate the final application frame, retaining the complete
    # original byte stream as evidence. Never treat an earlier frame as a result
    # when the last startup marker is incomplete or has the wrong identity.
    starts = list(re.finditer(r"^BENCHMARK_START .*$", text, re.M))
    if not starts:
        raise CaptureValidationError("BENCHMARK_START marker is missing")
    preamble = text[:starts[-1].start()]
    image_value = image_size = None
    if image_crc32 is not None:
        image_rows = re.findall(
            r"^BENCHMARK_IMAGE_CRC32 build_id=(\S+) value=([0-9a-fA-F]{8}) size=(\d+)\s*$",
            preamble, re.M,
        )
        if not image_rows:
            raise CaptureValidationError("runtime firmware image checksum is missing")
        image_build, value, size = image_rows[-1]
        image_value, image_size = int(value, 16), int(size)
        expected_crc = int(image_crc32, 16) if isinstance(image_crc32, str) else image_crc32
        if image_build != build_id or image_value != expected_crc or image_size != image_bytes:
            raise CaptureValidationError("runtime firmware image checksum or size does not match the built binary")
    text = text[starts[-1].start():]
    ends = list(re.finditer(r"^BENCHMARK_END .*$", text, re.M))
    if len(ends) != 1:
        raise CaptureValidationError(f"expected exactly one BENCHMARK_END field; found {len(ends)}")
    text = text[:ends[0].end()]
    image_rechecks = []
    if image_crc32 is not None and "BENCHMARK_IMAGE_RECHECK " in preamble + text:
        # Ignore stale application output preceding the final image marker.
        application = preamble[preamble.rfind("BENCHMARK_IMAGE_CRC32 "):] + text
        rows = re.findall(
            r"^BENCHMARK_IMAGE_RECHECK phase=(\S+) cached=([0-9a-fA-F]{8}) "
            r"flushed=([0-9a-fA-F]{8}) expected=([0-9a-fA-F]{8}) "
            r"size=(\d+) cached_size=(\d+) flushed_size=(\d+)\s*$", application, re.M,
        )
        if [row[0] for row in rows] != ["calibration", "scored"]:
            raise CaptureValidationError("firmware image integrity rechecks are incomplete")
        for phase, cached, flushed, expected, size, cached_size, flushed_size in rows:
            if (any(int(value, 16) != expected_crc for value in (cached, flushed, expected))
                    or any(int(value) != image_bytes for value in (size, cached_size, flushed_size))):
                raise CaptureValidationError("firmware image changed during the workload")
            image_rechecks.append({"phase": phase, "cached_crc32": int(cached, 16),
                                   "flushed_crc32": int(flushed, 16), "bytes": int(size)})
    if "Errors detected" in text or "BENCHMARK_PORT_ERROR" in text or re.search(r"\bERROR!", text):
        raise CaptureValidationError("firmware reported a CoreMark error")
    if "Correct operation validated." not in text:
        raise CaptureValidationError("upstream CoreMark validation marker is missing")

    start_line = _one(r"^(BENCHMARK_START .+)$", text, "BENCHMARK_START")
    start_fields = dict(re.findall(r"([A-Za-z_][A-Za-z_0-9]*)=([^\s]+)", start_line))
    required = {
        "profile": str(profile), "build_id": str(build_id), "mode": mode,
        "clock_hz": str(clock_hz), "data_size": str(data_size), "contexts": str(contexts),
    }
    expected_seeds = SEED_EXPECTATIONS[mode]["seeds"]
    required.update({"seed1": str(expected_seeds[0]), "seed2": str(expected_seeds[1]),
                     "seed3": f"0x{expected_seeds[2]:02x}"})
    for key, expected in required.items():
        if start_fields.get(key) != expected:
            raise CaptureValidationError(
                f"start marker {key} is {start_fields.get(key)!r}; expected {expected!r}"
            )

    end_line = _one(r"^(BENCHMARK_END .+)$", text, "BENCHMARK_END")
    end_fields = dict(re.findall(r"([A-Za-z_][A-Za-z_0-9]*)=([^\s]+)", end_line))
    if end_fields.get("status") != "returned" or end_fields.get("profile") != profile or end_fields.get("build_id") != build_id:
        raise CaptureValidationError("benchmark completion marker does not match its start marker")

    counters = SEED_EXPECTATIONS[mode]
    seedcrc = _one(r"^seedcrc\s*:\s*0x([0-9a-fA-F]+)\s*$", text, "seedcrc", lambda x: int(x, 16))
    if seedcrc != counters["seedcrc"]:
        raise CaptureValidationError(f"seed CRC 0x{seedcrc:04x} does not match {mode} seeds")
    crcs = {}
    for label in ("crclist", "crcmatrix", "crcstate"):
        value = _one(rf"^\[0\]{label}\s*:\s*0x([0-9a-fA-F]+)\s*$", text, label, lambda x: int(x, 16))
        if value != counters[label]:
            raise CaptureValidationError(f"{label} CRC 0x{value:04x} does not match upstream {mode} validation")
        crcs[label] = value
    final_crc = _one(r"^\[0\]crcfinal\s*:\s*0x([0-9a-fA-F]+)\s*$", text, "crcfinal", lambda x: int(x, 16))

    reported_size = _one(r"^CoreMark Size\s*:\s*(\d+)\s*$", text, "CoreMark Size", int)
    if reported_size != data_size // 3:
        raise CaptureValidationError(f"CoreMark per-algorithm size is {reported_size}; expected {data_size // 3}")
    upstream_ticks = _one(r"^Total ticks\s*:\s*(\d+)\s*$", text, "Total ticks", int)
    upstream_seconds_text = _one(r"^Total time \(secs\):\s*([0-9]+(?:\.[0-9]+)?)\s*$", text, "Total time")
    upstream_seconds = float(upstream_seconds_text)
    iterations = _one(r"^Iterations\s*:\s*(\d+)\s*$", text, "Iterations", int)
    upstream_rate_text = _one(r"^Iterations/Sec\s*:\s*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?\d+)?)\s*$", text, "Iterations/Sec")
    coremark_match = re.search(r"^CoreMark 1\.0\s*:\s*([0-9.eE+-]+)\s*/", text, re.M) if mode == "performance" else None
    coremark_line = coremark_match.group(1) if coremark_match else None

    interval_count = _one(r"^BENCHMARK_INTERVAL_COUNT count=(\d+) overflow=(\d+)\s*$", text,
                          "interval count", lambda value: tuple(map(int, value)))
    count, overflow = interval_count
    calibration_count = _one(r"^BENCHMARK_CALIBRATION_COUNT count=(\d+)\s*$", text, "calibration count", int)
    calibration_rows = re.findall(
        r"^BENCHMARK_CALIBRATION_TICKS index=(\d+) hi=([0-9a-fA-F]+) lo=([0-9a-fA-F]+)\s*$", text, re.M,
    )
    scored_row = _one(
        r"^BENCHMARK_SCORED_TICKS index=(\d+) hi=([0-9a-fA-F]+) lo=([0-9a-fA-F]+)\s*$",
        text, "scored ticks", lambda row: (int(row[0]), int(row[1], 16), int(row[2], 16)),
    )
    if overflow or count != calibration_count + 1 or len(calibration_rows) != calibration_count:
        raise CaptureValidationError("timing interval capture is incomplete or overflowed")
    if any(int(row[0]) != index + 1 for index, row in enumerate(calibration_rows)):
        raise CaptureValidationError("calibration intervals are missing or out of order")
    if any(int(row[1], 16) > 0xffffffff or int(row[2], 16) > 0xffffffff for row in calibration_rows):
        raise CaptureValidationError("calibration timer words exceed their 32-bit widths")
    score_index, high, low = scored_row
    if score_index != count:
        raise CaptureValidationError("scored timer interval is not the final CoreMark interval")
    if high > 0xffffffff or low > 0xffffffff:
        raise CaptureValidationError("timer words exceed their 32-bit widths")
    elapsed_ticks = (high << 32) | low
    if elapsed_ticks == 0:
        raise CaptureValidationError("scored timer interval is zero")
    if elapsed_ticks < clock_hz * 10:
        raise CaptureValidationError(f"scored repetition is shorter than 10 seconds ({elapsed_ticks} ticks)")
    if (elapsed_ticks & 0xffffffff) != upstream_ticks:
        raise CaptureValidationError("upstream 32-bit tick display disagrees with the 64-bit port counter")
    if iterations <= 0 or upstream_seconds <= 0:
        raise CaptureValidationError("CoreMark reported a zero duration or iteration count")

    elapsed_seconds = elapsed_ticks / clock_hz
    score = iterations * clock_hz / elapsed_ticks
    score_per_mhz = iterations * 1_000_000 / elapsed_ticks
    if "." in upstream_rate_text or "e" in upstream_rate_text.lower():
        parsed_upstream_rate = _close_to_printed(score, upstream_rate_text)
    else:
        parsed_upstream_rate = int(upstream_rate_text)
        # HAS_FLOAT=0 keeps this small bare-metal image within main RAM. Upstream
        # CoreMark then prints integer iterations / integer elapsed seconds. Its
        # denominator is time_in_secs(total_time), so compare against that exact
        # truncating calculation; the reported host score still uses raw ticks.
        upstream_whole_seconds = int(upstream_seconds)
        if upstream_whole_seconds <= 0 or parsed_upstream_rate != iterations // upstream_whole_seconds:
            raise CaptureValidationError("upstream integer rate disagrees with iterations and whole-second duration")
    parsed_coremark = _close_to_printed(score, coremark_line) if coremark_line is not None else None
    if "." in upstream_seconds_text:
        places = len(upstream_seconds_text.partition(".")[2])
        if abs(upstream_seconds - elapsed_seconds) > 0.500001 * 10 ** -places:
            raise CaptureValidationError("upstream elapsed seconds disagree with 64-bit timer ticks")
    elif int(upstream_seconds) != int(elapsed_seconds):
        raise CaptureValidationError("upstream whole-second duration disagrees with 64-bit timer ticks")

    calibration = [
        {"index": int(index), "elapsed_ticks": (int(high, 16) << 32) | int(low, 16)}
        for index, high, low in calibration_rows
    ]
    plan = None
    if "BENCHMARK_CALIBRATION_PLAN " in text:
        row = _one(
            r"^BENCHMARK_CALIBRATION_PLAN iterations=(\d+) target_seconds=(\d+) scored_iterations=(\d+) method=(\S+)\s*$",
            text, "calibration plan", lambda values: (int(values[0]), int(values[1]), int(values[2]), values[3]),
        )
        initial_iterations, target_seconds, scored_iterations, method = row
        if (calibration_count != 1 or initial_iterations <= 0 or target_seconds < 10
                or scored_iterations != iterations or method != "fresh_invocation"
                or calibration[0]["elapsed_ticks"] <= 0):
            raise CaptureValidationError("invalid fresh-invocation calibration plan")
        numerator = initial_iterations * target_seconds * clock_hz
        calibration_ticks = calibration[0]["elapsed_ticks"]
        expected_iterations = (numerator + calibration_ticks - 1) // calibration_ticks
        if iterations != expected_iterations:
            raise CaptureValidationError("scored iterations disagree with independently calculated calibration")
        calibration[0]["iterations"] = initial_iterations
        plan = {"method": method, "initial_iterations": initial_iterations,
                "target_seconds": target_seconds, "scored_iterations": scored_iterations}
    return {
        "status": "passed",
        "profile": profile,
        "build_id": build_id,
        "image_crc32": image_value,
        "image_bytes": image_size,
        "image_rechecks": image_rechecks,
        "mode": mode,
        "clock_hz": clock_hz,
        "data_size": data_size,
        "contexts": contexts,
        "seeds": list(expected_seeds),
        "iterations": iterations,
        "elapsed_ticks": elapsed_ticks,
        "elapsed_seconds": elapsed_seconds,
        "coremark": score,
        "coremark_per_mhz": score_per_mhz,
        "seedcrc": seedcrc,
        "crcs": crcs,
        "crcfinal": final_crc,
        "validation": "passed",
        "upstream_iterations_per_second": parsed_upstream_rate,
        "upstream_coremark": parsed_coremark,
        "calibration": calibration,
        "calibration_plan": plan,
        "decode_errors_present": decode_errors,
    }


def aggregate(repetitions):
    """Summarize only successful scored repetitions; raw integer counters stay intact."""
    accepted = [entry for entry in repetitions if entry.get("status") == "passed" and entry.get("mode") == "performance"]
    if not accepted:
        return None
    values = [entry["coremark"] for entry in accepted]
    per_mhz = [entry["coremark_per_mhz"] for entry in accepted]
    return {
        "count": len(accepted),
        "coremark_mean": statistics.mean(values),
        "coremark_min": min(values),
        "coremark_max": max(values),
        "coremark_spread": max(values) - min(values),
        "coremark_per_mhz_mean": statistics.mean(per_mhz),
        "coremark_per_mhz_min": min(per_mhz),
        "coremark_per_mhz_max": max(per_mhz),
        "coremark_per_mhz_spread": max(per_mhz) - min(per_mhz),
    }
