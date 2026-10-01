"""CPU identities shared by build, benchmark, reporting, and candidate flows."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINUX_RTL = ROOT / ".deps/pythondata-cpu-vexriscv/pythondata_cpu_vexriscv/verilog/VexRiscv_Linux.v"

BASE_CONFIGURATIONS = {
    "minimal": {"liteX_variant": "minimal", "isa": "rv32i2p0", "instruction_cache_bytes": 0,
                "data_cache_bytes": 0, "prediction": "none", "compressed": False},
    "lite": {"liteX_variant": "lite", "isa": "rv32i2p0_m", "instruction_cache_bytes": 2048,
             "data_cache_bytes": 0, "prediction": "static", "compressed": False},
    "standard": {"liteX_variant": "standard", "isa": "rv32i2p0_m", "instruction_cache_bytes": 4096,
                 "data_cache_bytes": 4096, "prediction": "static", "compressed": False},
    "linux": {"liteX_variant": "linux", "isa": "rv32i2p0_ma", "instruction_cache_bytes": 4096,
              "data_cache_bytes": 4096, "prediction": "static", "compressed": False,
              "mmu": True, "supervisor": True, "atomics": True,
              "os_boot": "deferred; shipped Linux-capable RTL used for bare-metal tests"},
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def configuration_for(profile, *, cpu_candidate=None, candidate_manifest=None, selection=None):
    if profile == "performance":
        if not isinstance(selection, dict) or selection.get("status") != "accepted":
            raise RuntimeError("performance profile requires its validated selection record")
        generated = (candidate_manifest or {}).get("cpu_configuration", {})
        return {**BASE_CONFIGURATIONS["standard"], **generated,
                "prediction": selection.get("candidate"),
                "rtl_sha256": selection.get("rtl_sha256"),
                "candidate_generation": candidate_manifest}
    if profile == "maxperf":
        if (not isinstance(selection, dict)
                or selection.get("status") not in ("accepted", "provisional")):
            raise RuntimeError("maxperf requires an accepted selection or an internal provisional build")
        mode = candidate_manifest.get("memory_mode") if candidate_manifest else ""
        mode_selection = selection.get("memory_modes", {}).get(mode, {})
        if (not isinstance(mode_selection, dict)
                or mode_selection.get("candidate_id") != cpu_candidate
                or mode_selection.get("status") not in ("accepted", "provisional")):
            raise RuntimeError("maxperf mode selection does not identify the generated CPU candidate")
        generated = (candidate_manifest or {}).get("cpu_configuration", {})
        return {**BASE_CONFIGURATIONS["standard"], **generated,
                "prediction": generated.get("prediction", "dynamic_target"),
                "rtl_sha256": (candidate_manifest or {}).get("rtl_sha256"),
                "candidate_generation": candidate_manifest,
                "selection": mode_selection}
    if cpu_candidate is not None:
        manifest_id = (candidate_manifest or {}).get("candidate_id", (candidate_manifest or {}).get("candidate"))
        if not isinstance(candidate_manifest, dict) or manifest_id != cpu_candidate:
            raise RuntimeError("candidate CPU identity does not match its generation manifest")
        config = candidate_manifest.get("cpu_configuration", {})
        return {**BASE_CONFIGURATIONS["standard"], **config,
                "prediction": config.get("prediction", candidate_manifest.get("candidate", "dynamic_target")),
                "rtl_sha256": candidate_manifest.get("rtl_sha256"),
                "candidate_generation": candidate_manifest}
    if profile not in BASE_CONFIGURATIONS:
        raise ValueError(f"no base CPU configuration for {profile!r}")
    config = dict(BASE_CONFIGURATIONS[profile])
    if profile == "linux":
        if not LINUX_RTL.is_file():
            raise RuntimeError("pinned Linux VexRiscv RTL is missing; run make setup")
        config["rtl_sha256"] = sha256(LINUX_RTL)
        config["rtl"] = str(LINUX_RTL.relative_to(ROOT))
        config["generator_commit"] = "pythondata-cpu-vexriscv@642ecfed1c84460555d6d803d660cc60cfc1ecb6"
    return config
