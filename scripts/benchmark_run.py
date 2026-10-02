#!/usr/bin/env python3
"""Program one profile and capture CoreMark over the Dock UART."""

import datetime
import glob
import hashlib
import json
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

ANSI_ESCAPE = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")

from gateware.soc import MEMORY_MODES, PROFILES, SYS_CLK_FREQ, ProjectSoC  # noqa: E402
from benchmark_results import CaptureValidationError, parse_capture  # noqa: E402
from benchmark_build import build_module, sha256  # noqa: E402
from benchmark_identity import fingerprint_matches  # noqa: E402
from benchmark_evidence import bundle_is_current  # noqa: E402
from scripts.memory import profile_build_dir, validate_memory  # noqa: E402


class HeadlessConsole:
    """LiteXTerm console shim: the benchmark runner has no keyboard writer."""

    def configure(self):
        pass

    def unconfigure(self):
        pass

    def getkey(self):
        raise RuntimeError("interactive input is disabled in the benchmark runner")


class RecordingPort:
    """Serial proxy that records every inbound byte, including SFL replies."""

    _LOCAL_ATTRIBUTES = frozenset({"_port", "_sink", "_transmit_sink"})

    def __init__(self, port, sink, transmit_sink=None):
        object.__setattr__(self, "_port", port)
        object.__setattr__(self, "_sink", sink)
        object.__setattr__(self, "_transmit_sink", transmit_sink)

    def __setattr__(self, name, value):
        if name in self._LOCAL_ATTRIBUTES:
            object.__setattr__(self, name, value)
        else:
            # LiteXTerm adjusts pyserial's timeout around each SFL write/read.
            # Forward setters as well as getters so those protocol timeouts take
            # effect on the actual serial object after it is wrapped here.
            setattr(self._port, name, value)

    def read(self, size=1):
        data = self._port.read(size)
        if data:
            self._sink(data)
        return data

    def write(self, data):
        written = self._port.write(data)
        if self._transmit_sink is not None:
            self._transmit_sink(data if written is None else data[:written])
        return written

    def __getattr__(self, name):
        return getattr(self._port, name)


def clear_uart_input_buffer(port):
    """Discard input queued before the current FPGA image starts booting."""
    reset = getattr(port, "reset_input_buffer", None)
    if reset is None:
        raise RuntimeError("serial port cannot clear stale input before FPGA programming")
    reset()


class SerialOwner:
    """The sole thread that reads UART bytes and services LiteX serial boot."""

    def __init__(self, term, log_file):
        self.term = term
        self.log_file = log_file
        self.lock = threading.Lock()
        self.lines = []
        self.partial_line = bytearray()
        self.start_seen = threading.Event()
        self.start_marker_started = threading.Event()
        self.end_seen = threading.Event()
        self.console_prompt_seen = threading.Event()
        self.sfl_handler_idle = threading.Event()
        self.sfl_handler_idle.set()
        self.stopping = threading.Event()
        self.error = None
        self.application_failure = None
        self.thread = threading.Thread(target=self._read_loop, name="benchmark-uart-reader", daemon=True)

    def _record(self, data):
        with self.lock:
            self.log_file.write(data)
            for byte in data:
                if byte == 10:
                    raw_line = bytes(self.partial_line)
                    normalized_line = ANSI_ESCAPE.sub(b"", raw_line).strip(b"\r")
                    line = normalized_line.decode("utf-8", errors="replace")
                    self.partial_line.clear()
                    self.lines.append(line)
                    if line.startswith(("BENCHMARK_START ", "BENCHMARK_CALIBRATION_START ", "DDR_TEST_START ")):
                        self.start_marker_started.set()
                        self.start_seen.set()
                    elif line.startswith(("BENCHMARK_END ", "DDR_TEST_END ")):
                        self.end_seen.set()
                    elif line.startswith("BENCHMARK_PORT_ERROR "):
                        self.error = RuntimeError(line)
                    elif line.startswith("DDR_TEST_FAILURE "):
                        self.application_failure = line
                else:
                    self.partial_line.append(byte)
                    raw_line = bytes(self.partial_line)
                    normalized_line = ANSI_ESCAPE.sub(b"", raw_line).lstrip(b"\r")
                    if normalized_line.startswith((b"BENCHMARK_START ", b"BENCHMARK_CALIBRATION_START ", b"DDR_TEST_START ")):
                        self.start_marker_started.set()
                    if normalized_line.endswith(b"litex> "):
                        self.console_prompt_seen.set()

    def start(self):
        self.term.reader_alive = True
        self.thread.start()

    def _read_loop(self):
        try:
            while not self.stopping.is_set():
                byte = self.term.port.read(1)
                if not byte:
                    continue
                if self.term.mem_regions and self.term.serial_boot and self.term.detect_prompt(byte):
                    self.term.answer_prompt()
                if self.term.detect_magic(byte):
                    self.sfl_handler_idle.clear()
                    try:
                        self.term.answer_magic()
                    finally:
                        self.sfl_handler_idle.set()
        except BaseException as error:
            if not self.stopping.is_set():
                self.error = error

    def wait_for(self, event, timeout, label):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if event.wait(min(0.1, max(0.0, deadline - time.monotonic()))):
                return True
            if self.error is not None:
                raise RuntimeError(f"UART reader failed while waiting for {label}: {self.error}") from self.error
        return False

    def wait_for_start_or_console(self, timeout):
        """Wait for firmware start or the BIOS console after a missed boot ACK."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.start_seen.is_set():
                return "started"
            # Seeing even the beginning of the firmware marker means execution
            # started; never send a recovery command into an active application.
            if not self.start_marker_started.is_set() and self.console_prompt_seen.is_set():
                return "console"
            if self.error is not None:
                raise RuntimeError(f"UART reader failed during startup: {self.error}") from self.error
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return "timeout"

    def stop(self):
        self.stopping.set()
        self.term.reader_alive = False
        try:
            self.term.port.close()
        except (AttributeError, OSError):
            pass
        if self.thread.ident is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)
            if self.thread.is_alive():
                raise RuntimeError("UART reader did not stop after serial close")


def generated_main_ram_base(header):
    text = Path(header).read_text()
    match = re.search(r"^#define\s+MAIN_RAM_BASE\s+(0x[0-9a-fA-F]+|[0-9]+)", text, re.M)
    if not match:
        raise RuntimeError("generated mem.h has no MAIN_RAM_BASE")
    return int(match.group(1), 0)


def load_selected_artifacts(profile, memory="onchip", build_dir=None, cpu_candidate=None,
                            provisional=False):
    validate_memory(memory)
    output = Path(build_dir).resolve() if build_dir is not None else profile_build_dir(ROOT, profile, memory)
    build_path = output / "build-metadata.json"
    benchmark_path = output / "benchmark/benchmark-metadata.json"
    if not build_path.is_file() or not benchmark_path.is_file():
        raise RuntimeError("selected profile artifacts are missing; run make benchmark-build first")
    build = json.loads(build_path.read_text())
    benchmark = json.loads(benchmark_path.read_text())
    if (build.get("status") != "passed" or build.get("profile") != profile
            or build.get("memory_mode", "onchip") != memory
            or (cpu_candidate is not None and build.get("cpu_candidate") != cpu_candidate)):
        raise RuntimeError(f"SoC build metadata does not verify the {profile} profile")
    if (benchmark.get("status") != "passed" or benchmark.get("profile") != profile
            or benchmark.get("memory_mode", "onchip") != memory
            or (cpu_candidate is not None and benchmark.get("cpu_candidate") != cpu_candidate)):
        raise RuntimeError(f"CoreMark build metadata does not verify the {profile} profile")
    if build.get("cpu_candidate") != benchmark.get("cpu_candidate"):
        raise RuntimeError("SoC and CoreMark metadata select different CPU candidates")
    if build.get("cpu_variant") != benchmark.get("cpu_variant"):
        raise RuntimeError("SoC and CoreMark metadata select different CPU variants")
    if build.get("cpu_profile_selection") != benchmark.get("cpu_profile_selection"):
        raise RuntimeError("SoC and CoreMark metadata select different profile-selection identities")
    if build.get("cpu_configuration") != benchmark.get("cpu_configuration"):
        raise RuntimeError("SoC and CoreMark metadata contain different CPU configurations")
    if not fingerprint_matches(benchmark, build):
        raise RuntimeError("CoreMark fingerprint does not match its CPU, memory, or selection identity")
    if (benchmark.get("identity_schema_version", 1) >= 2
            and not bundle_is_current(benchmark.get("evidence_bundle"), benchmark.get("source_fingerprint"), ROOT)):
        raise RuntimeError("immutable benchmark evidence bundle is missing, changed, or mismatched")
    if profile == "performance":
        selection = build_module.read_performance_selection()
        if (build.get("cpu_candidate") != selection.get("candidate")
                or benchmark.get("cpu_candidate") != selection.get("candidate")
                or build.get("cpu_profile_selection") != selection):
            raise RuntimeError("performance artifacts do not match the measured public CPU selection")
    if profile == "maxperf":
        if provisional:
            if not cpu_candidate:
                raise RuntimeError("provisional maxperf verification requires an explicit CPU candidate")
            manifest_path = ROOT / "build/maxperf-candidates" / memory / cpu_candidate / "candidate.json"
            if not manifest_path.is_file():
                raise RuntimeError("provisional maxperf generation manifest is missing")
            manifest = json.loads(manifest_path.read_text())
            selection = build.get("cpu_profile_selection", {})
            selected_mode = selection.get("memory_modes", {}).get(memory, {})
            verification_root = (ROOT / "build/maxperf-verification" / memory).resolve()
            if (not output.is_relative_to(verification_root)
                    or build.get("cpu_candidate") != cpu_candidate
                    or benchmark.get("cpu_candidate") != cpu_candidate
                    or benchmark.get("cpu_profile_selection") != selection
                    or selection.get("status") != "provisional"
                    or selected_mode.get("status") != "provisional"
                    or selected_mode.get("candidate_id") != cpu_candidate
                    or manifest.get("candidate_id") != cpu_candidate
                    or manifest.get("memory_mode") != memory
                    or build.get("cpu_configuration", {}).get("rtl_sha256") != manifest.get("rtl_sha256")):
                raise RuntimeError("provisional maxperf artifacts do not match the private candidate verification identity")
        else:
            from gateware.profile_selection import accepted_maxperf_selection
            selection = accepted_maxperf_selection()
            if (selection is None or profile not in PROFILES
                    or build.get("cpu_profile_selection") != selection
                    or benchmark.get("cpu_profile_selection") != selection
                    or build.get("cpu_candidate") != selection["memory_modes"][memory]["candidate_id"]):
                raise RuntimeError("maxperf artifacts do not match the accepted two-mode public selection")
    if profile == "linux":
        cpu = build.get("cpu_configuration", {})
        if (build.get("cpu_variant") != "linux" or cpu.get("isa") != "rv32i2p0_ma"
                or cpu.get("instruction_cache_bytes") != 4096 or cpu.get("data_cache_bytes") != 4096
                or cpu.get("compressed") is not False or cpu.get("mmu") is not True
                or cpu.get("supervisor") is not True or cpu.get("atomics") is not True):
            raise RuntimeError("Linux-capable profile artifacts do not match the pinned Linux CPU identity")
    source_hashes = benchmark.get("source_hashes")
    if not isinstance(source_hashes, dict) or not source_hashes:
        raise RuntimeError("benchmark source identity is missing; run make benchmark-build")
    for relative, expected_hash in source_hashes.items():
        source = ROOT / relative
        if not source.is_file() or sha256(source) != expected_hash:
            raise RuntimeError(f"benchmark source or generated configuration changed: {relative}; run make benchmark-build")
    bitstream = ROOT / build["bitstream"]
    if not bitstream.is_file() or sha256(bitstream) != benchmark.get("bitstream_sha256"):
        raise RuntimeError("selected bitstream hash does not match CoreMark firmware metadata")
    for mode in ("performance", "validation"):
        image = benchmark["images"][mode]
        binary = ROOT / image["binary"]
        if not binary.is_file() or sha256(binary) != image.get("binary_sha256"):
            raise RuntimeError(f"selected {mode} firmware is missing or has a changed hash")
        if image.get("profile") != profile or image.get("build_id") != benchmark.get("build_id"):
            raise RuntimeError(f"selected {mode} image profile/build identity does not match its metadata")
    clock = SYS_CLK_FREQ
    soc_header = (output / "software/include/generated/soc.h").read_text()
    clock_match = re.search(r"#define CONFIG_CLOCK_FREQUENCY (0x[0-9a-fA-F]+|[0-9]+)", soc_header)
    if not clock_match or int(clock_match.group(1), 0) != clock:
        raise RuntimeError("generated SoC clock does not match the required 48 MHz benchmark clock")
    csr_header = (output / "software/include/generated/csr.h").read_text()
    if "timer0_uptime_latch_write" not in csr_header or "timer0_uptime_cycles_read" not in csr_header:
        raise RuntimeError("generated CSR header lacks the 64-bit timer uptime accessors")
    benchmark["main_ram_base"] = generated_main_ram_base(output / "software/include/generated/mem.h")
    benchmark["cpu_variant"] = build.get("cpu_variant")
    benchmark["bitstream_path"] = str(bitstream)
    benchmark["build_dir"] = str(output)
    return build, benchmark


def source_state():
    revision = subprocess_text(["git", "rev-parse", "HEAD"])
    dirty = bool(subprocess_text(["git", "status", "--porcelain"]))
    return {"revision": revision, "dirty": dirty}


def subprocess_text(command):
    import subprocess
    return subprocess.check_output(command, cwd=ROOT, text=True).strip()


def append_public_session(session):
    destination = ROOT / "docs/performance/results.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        try:
            data = json.loads(destination.read_text())
        except json.JSONDecodeError:
            data = {"schema_version": 1, "sessions": []}
    else:
        data = {"schema_version": 1, "sessions": []}
    sessions = data.setdefault("sessions", [])
    sessions.append(session)
    data["latest_session_id"] = session["session_id"]
    data["schema_version"] = 1
    destination.write_text(json.dumps(data, indent=2) + "\n")


def discover_candidate_ports():
    return sorted(glob.glob("/dev/serial/by-id/*"))


def discover_usb_nodes():
    return sorted(glob.glob("/dev/bus/usb/*/*"))


def selected_artifact_identity(build, benchmark, port):
    state = source_state()
    return {
        "repository_revision": state["revision"],
        "repository_dirty": state["dirty"],
        "source_fingerprint": benchmark.get("source_fingerprint"),
        "identity_schema_version": benchmark.get("identity_schema_version"),
        "fingerprint_payload": benchmark.get("fingerprint_payload"),
        "evidence_bundle": benchmark.get("evidence_bundle"),
        "profile": benchmark["profile"],
        "cpu_variant": benchmark.get("cpu_variant"),
        "cpu_candidate": benchmark.get("cpu_candidate"),
        "cpu_profile_selection": benchmark.get("cpu_profile_selection"),
        "memory_mode": benchmark.get("memory_mode", "onchip"),
        "memory_placement": benchmark.get("memory", {}),
        "cpu_rtl": benchmark.get("cpu_rtl"),
        "cpu_configuration": benchmark.get("cpu_configuration", build.get("cpu_configuration")),
        "l2_cache_bytes": benchmark.get("memory", {}).get("l2_cache_bytes", 0),
        "ddr_training_status": benchmark.get("ddr_training_status"),
        "clock_hz": benchmark["clock_hz"],
        "coremark": benchmark["coremark"],
        "compiler": benchmark["compiler"],
        "compiler_version": benchmark["compiler_version"],
        "compiler_flags": benchmark["compiler_flags"],
        "compiler_flags_complete": benchmark["images"]["performance"]["commands"]["core_list_join.c"]["flags"],
        "tool_versions": build.get("tool_versions", {}),
        "cache": benchmark["cache"],
        "iteration_calibration": benchmark.get("iteration_calibration"),
        "cache_maintenance": benchmark.get("cache_maintenance"),
        "runtime_preflight": benchmark.get("runtime_preflight"),
        "memory": benchmark["memory"],
        "uart_device_requested": port,
        "uart_device_selected": str(Path(port).resolve()) if Path(port).exists() else None,
        "bitstream": {"path": build["bitstream"], "sha256": benchmark["bitstream_sha256"]},
        "firmware": {
            mode: {"path": item["binary"], "sha256": item["binary_sha256"],
                   "crc32": item.get("binary_crc32"), "bytes": item["binary_bytes"]}
            for mode, item in benchmark["images"].items()
        },
    }


def run_trial(profile, build, benchmark, mode, attempt, port, session_id, handshake_timeout, trial_timeout,
              evidence_root=None):
    from litex.tools import litex_term

    suffix = "validation" if mode == "validation" else f"performance-{attempt}"
    evidence_root = Path(evidence_root) if evidence_root is not None else ROOT / "docs/performance"
    public_raw = evidence_root / f"{session_id}-{suffix}.uart.bin"
    public_text = evidence_root / f"{session_id}-{suffix}.uart.log"
    public_tx = evidence_root / f"{session_id}-{suffix}.uart.tx.bin"
    session_dir = ROOT / "build/benchmarks" / session_id
    session_raw = session_dir / f"{suffix}.uart.bin"
    session_tx = session_dir / f"{suffix}.uart.tx.bin"
    public_raw.parent.mkdir(parents=True, exist_ok=True)
    session_dir.mkdir(parents=True, exist_ok=True)
    image = benchmark["images"][mode]
    record = {
        "mode": mode,
        "attempt": attempt,
        "status": "in_progress",
        "uart_device": port,
        "programming_status": "not_attempted",
        "firmware_sha256": image["binary_sha256"],
        "raw_uart_log": str(public_raw.relative_to(ROOT)),
        "text_uart_log": str(public_text.relative_to(ROOT)),
        "transmitted_uart_log": str(public_tx.relative_to(ROOT)),
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    serial_owner = None
    term = None
    old_console = litex_term.Console
    old_sigint = signal.getsignal(signal.SIGINT)
    raw_file = public_raw.open("wb")
    tx_file = public_tx.open("wb")

    def create_term(safe):
        litex_term.Console = HeadlessConsole
        try:
            return litex_term.LiteXTerm(
                True, str(ROOT / image["binary"]),
                f"0x{benchmark['main_ram_base']:08x}", None, safe, None,
            )
        finally:
            litex_term.Console = old_console

    def start_reader(reset_input_buffer=False):
        nonlocal serial_owner
        if reset_input_buffer:
            clear_uart_input_buffer(term.port)
        term.port.timeout = 0.1
        term.port.write_timeout = 2.0
        term.port = RecordingPort(
            term.port,
            lambda data: serial_owner._record(data),
            lambda data: tx_file.write(data),
        )
        serial_owner = SerialOwner(term, raw_file)
        serial_owner.start()

    try:
        if not Path(port).exists():
            raise FileNotFoundError(f"explicit UART path does not exist: {port}")
        if not os.access(port, os.R_OK | os.W_OK):
            raise PermissionError(f"explicit UART path is not readable and writable: {port}")
        term = create_term(safe=False)
        term.open(port, 115200)
        # Do not let a prompt buffered from the previous bitstream trigger
        # serialboot recovery for the image that is about to be programmed.
        start_reader(reset_input_buffer=True)

        # The UART reader is already watching the BIOS handshakes before SRAM programming.
        cpu_rtl = benchmark.get("cpu_rtl")
        soc_kwargs = {
            "cpu_rtl": ROOT / cpu_rtl if cpu_rtl else None,
            "bios_size": build.get("bios_size"),
        }
        cpu_variant = benchmark.get("cpu_variant")
        if cpu_variant:
            soc_kwargs["cpu_variant"] = cpu_variant
        soc = ProjectSoC(profile=profile, memory=benchmark.get("memory_mode", "onchip"), **soc_kwargs)
        programmer = soc.platform.create_programmer(kit="openfpgaloader")
        record["programming_status"] = "started"
        programmer.load_bitstream(benchmark["bitstream_path"])
        record["programming_status"] = "completed"
        startup = serial_owner.wait_for_start_or_console(handshake_timeout)
        if startup == "console":
            record["bios_console_confirmed"] = True
            # The BIOS's initial 250 ms SFL ACK window can expire while JTAG
            # programming reconfigures the shared FTDI device. Recover only at
            # its confirmed idle console, before any application marker began.
            if serial_owner.start_marker_started.is_set() or serial_owner.start_seen.is_set():
                raise RuntimeError("BIOS recovery refused after benchmark startup began")
            if benchmark.get("memory_mode") == "ddr3":
                import ddr_test_run
                memory_dir = Path(benchmark.get("build_dir", profile_build_dir(ROOT, profile, "ddr3")))
                expected_lanes, phy_config = ddr_test_run.expected_read_lanes(memory_dir)
                lines = list(serial_owner.lines)
                training = ddr_test_run.validate_training(lines, expected_lanes, phy_config)
                bios_memtest = ddr_test_run.validate_bios_memtest(lines)
                record["ddr_startup_qualification"] = {
                    "training": training, "bios_memtest": bios_memtest,
                }
                if training["status"] != "passed" or bios_memtest["status"] != "passed":
                    raise RuntimeError("DDR BIOS training or Memtest did not pass; CoreMark firmware upload was refused")
            if not serial_owner.sfl_handler_idle.wait(timeout=30.0):
                raise TimeoutError("LiteXTerm SFL handler did not finish before BIOS recovery")
            record["startup_recovery"] = {
                "status": "in_progress",
                "trigger": "bios_console_after_missed_initial_sfl_ack",
                "uart_device": port,
                "port_reopened": True,
                "firmware_upload_mode": "LiteXTerm safe mode (64-byte frames, one outstanding)",
                "console_command": "\\nserialboot\\n",
                "fpga_reprogrammed": False,
            }

            previous_error = serial_owner.error
            serial_owner.stop()
            serial_owner = None
            term.close()
            term = create_term(safe=True)
            term.open(port, 115200)
            start_reader(reset_input_buffer=True)
            if previous_error is not None:
                record["startup_recovery"]["initial_reader_error"] = (
                    f"{type(previous_error).__name__}: {previous_error}"
                )
            command = b"\nserialboot\n"
            written = term.port.write(command)
            if written is not None and written != len(command):
                raise RuntimeError(f"short write sending BIOS serialboot command ({written}/{len(command)} bytes)")
            if not serial_owner.wait_for(serial_owner.start_seen, handshake_timeout, "firmware startup after BIOS recovery"):
                raise TimeoutError(f"no benchmark start marker after BIOS serialboot recovery within {handshake_timeout} seconds")
            record["startup_recovery"]["status"] = "completed"
        elif startup == "timeout":
            raise TimeoutError(f"no benchmark start marker within {handshake_timeout} seconds")
        if not serial_owner.wait_for(serial_owner.end_seen, trial_timeout, "BENCHMARK_END"):
            raise TimeoutError(f"no benchmark completion marker within {trial_timeout} seconds")

        record["status"] = "passed"
    except KeyboardInterrupt:
        record["status"] = "interrupted"
        record["error"] = "KeyboardInterrupt"
    except BaseException as error:
        record["status"] = "failed"
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        if serial_owner is not None:
            record["application_end_seen"] = serial_owner.end_seen.is_set()
            record["application_halt_seen"] = (
                isinstance(serial_owner.error, RuntimeError)
                and str(serial_owner.error).startswith("BENCHMARK_PORT_ERROR ")
            )
            try:
                serial_owner.stop()
            except BaseException as error:
                record["status"] = "failed"
                record["cleanup_error"] = f"{type(error).__name__}: {error}"
        elif term is not None:
            try:
                term.close()
            except BaseException as error:
                record["cleanup_error"] = f"{type(error).__name__}: {error}"
        litex_term.Console = old_console
        signal.signal(signal.SIGINT, old_sigint)
        raw_file.close()
        tx_file.close()

    if public_raw.is_file():
        raw_bytes = public_raw.read_bytes()
        decoded = raw_bytes.decode("utf-8", errors="replace")
        public_text.write_text(decoded)
        session_raw.write_bytes(raw_bytes)
        session_text = session_dir / f"{suffix}.uart.log"
        session_text.write_text(decoded)
        session_tx.write_bytes(public_tx.read_bytes())
        record["raw_uart_sha256"] = sha256(public_raw)
        record["raw_uart_bytes"] = len(raw_bytes)
        record["transmitted_uart_sha256"] = sha256(public_tx)
        record["transmitted_uart_bytes"] = public_tx.stat().st_size
        record["decode_errors_present"] = _has_decode_errors(raw_bytes)
        record["session_raw_uart_log"] = str(session_raw.relative_to(ROOT))
        record["session_text_uart_log"] = str(session_text.relative_to(ROOT))
        record["session_transmitted_uart_log"] = str(session_tx.relative_to(ROOT))
        if record["status"] == "passed":
            try:
                if benchmark.get("memory_mode") == "ddr3":
                    import ddr_test_run
                    memory_dir = Path(benchmark.get("build_dir", profile_build_dir(ROOT, profile, "ddr3")))
                    expected_lanes, phy_config = ddr_test_run.expected_read_lanes(memory_dir)
                    lines = ANSI_ESCAPE.sub(b"", raw_bytes).decode("utf-8", errors="replace").splitlines()
                    training = ddr_test_run.validate_training(lines, expected_lanes, phy_config)
                    bios_memtest = ddr_test_run.validate_bios_memtest(lines)
                    record["ddr_startup_qualification"] = {
                        "training": training, "bios_memtest": bios_memtest,
                    }
                    if training["status"] != "passed" or bios_memtest["status"] != "passed":
                        raise RuntimeError("DDR BIOS training or Memtest did not pass before accepting CoreMark")
                record["parsed"] = parse_capture(
                    raw_bytes, profile=profile, build_id=benchmark["build_id"], mode=mode,
                    clock_hz=benchmark["clock_hz"], data_size=2000, contexts=1,
                    image_crc32=image.get("binary_crc32"), image_bytes=image["binary_bytes"],
                )
            except CaptureValidationError as error:
                record["status"] = "failed"
                record["error"] = f"capture validation failed: {error}"
    record["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return record


def _has_decode_errors(raw_bytes):
    try:
        raw_bytes.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def run_profile(profile, port, handshake_timeout, trial_timeout, batch_id=None, memory="onchip"):
    validate_memory(memory)
    session_id = (f"{batch_id}-{profile}" if batch_id else
                  datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + f"-{memory}-{profile}")
    session_dir = ROOT / "build/benchmarks" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    candidates = discover_candidate_ports()
    session = {
        "schema_version": 1,
        "session_id": session_id,
        "status": "in_progress",
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "profile": profile,
        "memory_mode": memory,
        "board": "Sipeed Tang Primer 20K with standard Dock",
        "clock_hz": SYS_CLK_FREQ,
        "uart_candidates_at_start": candidates,
        "usb_device_nodes_at_start": discover_usb_nodes(),
        "programming_status": "not_attempted",
        "trials": [],
    }
    if batch_id is not None:
        session["batch_id"] = batch_id
        session["requested_profiles"] = list(PROFILES)
    (session_dir / "session.json").write_text(json.dumps(session, indent=2) + "\n")
    try:
        build, benchmark = load_selected_artifacts(profile, memory=memory)
        session["identity"] = selected_artifact_identity(build, benchmark, port)
        for mode, attempt in [("validation", 0), ("performance", 1), ("performance", 2), ("performance", 3)]:
            record = run_trial(
                profile, build, benchmark, mode, attempt, port, session_id,
                handshake_timeout, trial_timeout,
            )
            session["trials"].append(record)
            if record.get("programming_status") == "completed":
                session["programming_status"] = "completed"
            elif record.get("programming_status") == "started":
                session["programming_status"] = "failed_or_interrupted"
            (session_dir / "session.json").write_text(json.dumps(session, indent=2) + "\n")
            if record["status"] == "interrupted":
                break
            if (record["status"] != "passed"
                    and record.get("programming_status") in ("started", "completed")
                    and not record.get("application_end_seen")
                    and not record.get("application_halt_seen")):
                # A timed-out or disconnected application may still be running.
                # Preserve its evidence and stop before programming another trial.
                session["batch_continuation_safe"] = False
                break
            if mode == "validation" and record["status"] != "passed":
                break
        # Preserve expected trial slots when validation or interruption stops the session.
        scheduled = [("validation", 0), ("performance", 1), ("performance", 2), ("performance", 3)]
        recorded = {(item["mode"], item["attempt"]) for item in session["trials"]}
        for mode, attempt in scheduled:
            if (mode, attempt) not in recorded:
                session["trials"].append({
                    "mode": mode, "attempt": attempt, "status": "not_run",
                    "error": "prior validation failed or the run was interrupted",
                })
        passed = [item for item in session["trials"] if item["status"] == "passed"]
        validation_ok = any(item["mode"] == "validation" and item["status"] == "passed" for item in passed)
        performance_ok = [item for item in passed if item["mode"] == "performance"]
        session["status"] = "passed" if validation_ok and len(performance_ok) == 3 else "failed"
        session["aggregate"] = None
        if len(performance_ok) == 3:
            values = [entry["parsed"]["coremark"] for entry in performance_ok]
            per_mhz = [entry["parsed"]["coremark_per_mhz"] for entry in performance_ok]
            session["aggregate"] = {
                "count": 3,
                "coremark_mean": sum(values) / 3,
                "coremark_min": min(values), "coremark_max": max(values),
                "coremark_spread": max(values) - min(values),
                "coremark_per_mhz_mean": sum(per_mhz) / 3,
                "coremark_per_mhz_min": min(per_mhz), "coremark_per_mhz_max": max(per_mhz),
                "coremark_per_mhz_spread": max(per_mhz) - min(per_mhz),
            }
    except BaseException as error:
        session["status"] = "failed"
        session["error"] = f"{type(error).__name__}: {error}"
        session["trials"] = session.get("trials", [])
    finally:
        session["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        session["evidence"] = str((session_dir / "session.json").relative_to(ROOT))
        (session_dir / "session.json").write_text(json.dumps(session, indent=2) + "\n")
        append_public_session(session)

    print(json.dumps(session, indent=2))
    interrupted = session.get("error", "").startswith("KeyboardInterrupt:") or any(
        t.get("status") == "interrupted" for t in session.get("trials", []))
    return (0 if session.get("status") == "passed" else 130 if interrupted else
            2 if session.get("batch_continuation_safe") is False else 1)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=str.lower, choices=[*PROFILES, "maxperf", "all"],
                        nargs="?", default="standard",
                        help="CPU profile, or ALL to measure every registered profile sequentially")
    parser.add_argument("port", nargs="?")
    parser.add_argument("--memory", choices=MEMORY_MODES, default="onchip")
    parser.add_argument("--handshake-timeout", type=float, default=45.0)
    parser.add_argument("--trial-timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    if args.profile == "maxperf" and args.profile not in PROFILES:
        parser.error("maxperf is not public until both memory modes qualify; use make maxperf-run")
    if not args.port:
        parser.error("an explicit PORT is required; the runner never selects a serial device automatically")
    if args.handshake_timeout <= 0 or args.trial_timeout <= 0:
        parser.error("timeouts must be positive")
    profiles = list(PROFILES) if args.profile == "all" else [args.profile]
    memory_suffix = "" if args.memory == "onchip" else f"-{args.memory}"
    batch_id = (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + memory_suffix + "-all"
                if args.profile == "all" else None)
    outcomes = {}
    for profile in profiles:
        print(f"CoreMark: validating and measuring {profile} ({profiles.index(profile) + 1}/{len(profiles)})", flush=True)
        call_args = (profile, args.port, args.handshake_timeout, args.trial_timeout, batch_id)
        result = (run_profile(*call_args) if args.memory == "onchip"
                  else run_profile(*call_args, memory=args.memory))
        outcomes[profile] = result
        if result == 130:
            return 130
        if result == 2:
            print("Batch stopped: firmware completion could not be confirmed; remaining profiles were not programmed.", flush=True)
            break
    if batch_id:
        print(json.dumps({"batch_id": batch_id, "profile_exit_codes": outcomes,
                          "status": "passed" if all(code == 0 for code in outcomes.values()) else "failed"}, indent=2))
    return 0 if all(code == 0 for code in outcomes.values()) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
