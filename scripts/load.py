#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gateware.soc import PROFILES, ProjectSoC  # noqa: E402


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in PROFILES:
        print(f"usage: make load PROFILE={','.join(PROFILES)}", file=sys.stderr)
        return 2
    profile = sys.argv[1]
    bitstream = ROOT / "build" / profile / "bitstream.fs"
    if not bitstream.is_file():
        print(f"bitstream missing: run make build PROFILE={profile} first", file=sys.stderr)
        return 1
    soc = ProjectSoC(profile=profile)
    programmer = soc.platform.create_programmer(kit="openfpgaloader")
    programmer.load_bitstream(str(bitstream))
    return 0


if __name__ == "__main__":
    sys.exit(main())
