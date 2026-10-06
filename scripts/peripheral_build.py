#!/usr/bin/env python3
"""Build optional SD/Ethernet variants into feature-specific artifact trees."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gateware.soc import MEMORY_MODES, PROFILES  # noqa: E402
from scripts.build import build_profile  # noqa: E402
from scripts.peripheral_config import (  # noqa: E402
    feature_slug, peripheral_build_dir, validate_features,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=(*PROFILES, "ALL"), default="standard")
    parser.add_argument("--memory", choices=MEMORY_MODES, default="ddr3")
    parser.add_argument("--sdcard", choices=("none", "spi"), default="none")
    parser.add_argument("--ethernet", choices=("none", "rmii"), default="none")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--refresh-software", action="store_true",
                        help="recompile firmware only if regenerated synthesis inputs match the routed image")
    parser.add_argument("--full-application", action="store_true",
                        help="attempt the full application in on-chip RAM")
    args = parser.parse_args(argv)
    try:
        validate_features(args.sdcard, args.ethernet)
        profiles = list(PROFILES) if args.profile == "ALL" else [args.profile]
        diagnostic = args.memory == "onchip" and not args.full_application
        outcomes = []
        for profile in profiles:
            output_dir = peripheral_build_dir(
                ROOT, args.memory, profile, args.sdcard, args.ethernet
            )
            try:
                metadata = build_profile(
                    profile,
                    memory=args.memory,
                    force=args.force,
                    output_dir=output_dir,
                    sdcard=args.sdcard,
                    ethernet=args.ethernet,
                    diagnostic=diagnostic,
                    refresh_software=args.refresh_software,
                )
                build_status = metadata.get("status", "failed")
                if build_status == "passed" and metadata.get("timing_gate") != "passed":
                    build_status = "timing_failed"
                outcomes.append({
                    "profile": profile,
                    "memory": args.memory,
                    "feature_slug": feature_slug(args.sdcard, args.ethernet),
                    "status": build_status,
                    "timing_gate": metadata.get("timing_gate", "unavailable"),
                    "gateware_results": metadata.get("gateware_results"),
                    "path": str(output_dir.relative_to(ROOT)),
                    "error": metadata.get("error"),
                })
            except (OSError, RuntimeError, ValueError, KeyError, Exception) as error:
                outcomes.append({
                    "profile": profile,
                    "memory": args.memory,
                    "feature_slug": feature_slug(args.sdcard, args.ethernet),
                    "status": "failed",
                    "path": str(output_dir.relative_to(ROOT)),
                    "error": f"{type(error).__name__}: {error}",
                })
                print(f"{profile}: build failed; continuing remaining profiles: {error}", file=sys.stderr)
        summary = {
            "schema_version": 1,
            "memory": args.memory,
            "sdcard": args.sdcard,
            "ethernet": args.ethernet,
            "software_capability": "diagnostic" if diagnostic else "full_application",
            "outcomes": outcomes,
        }
        print(json.dumps(summary, indent=2))
        return 1 if any(item["status"] != "passed" for item in outcomes) else 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"peripheral build rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
