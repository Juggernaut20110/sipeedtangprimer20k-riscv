#!/usr/bin/env python3
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gateware.soc import MEMORY_MODES, PROFILES, ProjectSoC  # noqa: E402
from scripts.memory import profile_build_dir  # noqa: E402
import hashlib  # noqa: E402


def main():
    if len(sys.argv) not in (2, 3) or sys.argv[1] not in (*PROFILES, "maxperf"):
        print(f"usage: make load PROFILE={','.join(PROFILES)} [MEMORY=onchip|ddr3]", file=sys.stderr)
        return 2
    profile = sys.argv[1]
    if profile == "maxperf" and profile not in PROFILES:
        print("maxperf is provisional until both memory modes qualify; use the maxperf candidate workflow", file=sys.stderr)
        return 1
    memory = sys.argv[2] if len(sys.argv) == 3 else "onchip"
    if memory not in MEMORY_MODES:
        print(f"unknown memory mode {memory!r}; choose from {', '.join(MEMORY_MODES)}", file=sys.stderr)
        return 2
    output_dir = profile_build_dir(ROOT, profile, memory)
    bitstream = output_dir / "bitstream.fs"
    if not bitstream.is_file():
        print(f"bitstream missing: run make build PROFILE={profile} MEMORY={memory} first", file=sys.stderr)
        return 1
    metadata_path = output_dir / "build-metadata.json"
    if not metadata_path.is_file():
        print(f"build metadata missing: {metadata_path}", file=sys.stderr)
        return 1
    metadata = json.loads(metadata_path.read_text())
    if (metadata.get("status") != "passed" or metadata.get("profile") != profile
            or metadata.get("memory_mode", "onchip") != memory):
        print(f"build metadata does not verify {memory}/{profile}", file=sys.stderr)
        return 1
    if profile == "maxperf":
        from scripts.build import read_maxperf_selection
        try:
            selection, item, manifest, selected_rtl = read_maxperf_selection(memory)
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            print(f"maxperf selection failed validation: {error}", file=sys.stderr)
            return 1
        if (metadata.get("cpu_profile_selection") != selection
                or metadata.get("cpu_candidate") != item.get("candidate_id")
                or metadata.get("cpu_configuration", {}).get("rtl_sha256") != manifest.get("rtl_sha256")
                or metadata.get("cpu_rtl") != str(selected_rtl.relative_to(ROOT))
                or hashlib.sha256(bitstream.read_bytes()).hexdigest() != metadata.get("bitstream_sha256")):
            print("maxperf build does not match the accepted candidate, RTL, and bitstream identities", file=sys.stderr)
            return 1
    cpu_rtl = metadata.get("cpu_rtl")
    soc = ProjectSoC(
        profile=profile, memory=memory,
        bios_size=metadata.get("bios_size"),
        cpu_rtl=(ROOT / cpu_rtl) if cpu_rtl else None,
        cpu_variant=metadata.get("cpu_variant"),
    )
    programmer = soc.platform.create_programmer(kit="openfpgaloader")
    programmer.load_bitstream(str(bitstream))
    return 0


if __name__ == "__main__":
    sys.exit(main())
