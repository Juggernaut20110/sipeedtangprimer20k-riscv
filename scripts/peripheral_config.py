"""Validated feature selections and isolated paths for peripheral builds."""

from pathlib import Path

from gateware.soc import MEMORY_MODES, PROFILES

SDCARD_MODES = ("none", "spi")
ETHERNET_MODES = ("none", "rmii")
PROFILE_SELECTIONS = (*PROFILES, "ALL")


def validate_features(sdcard="none", ethernet="none"):
    if sdcard not in SDCARD_MODES:
        raise ValueError(f"unknown SDCARD mode {sdcard!r}; choose from {', '.join(SDCARD_MODES)}")
    if ethernet not in ETHERNET_MODES:
        raise ValueError(f"unknown ETHERNET mode {ethernet!r}; choose from {', '.join(ETHERNET_MODES)}")
    return sdcard, ethernet


def feature_slug(sdcard="none", ethernet="none"):
    validate_features(sdcard, ethernet)
    return f"sd-{sdcard}_eth-{ethernet}"


def peripheral_build_dir(root, memory, profile, sdcard="none", ethernet="none"):
    if memory not in MEMORY_MODES:
        raise ValueError(f"unknown memory mode {memory!r}; choose from {', '.join(MEMORY_MODES)}")
    if profile not in PROFILES:
        raise ValueError(f"unknown CPU profile {profile!r}; choose from {', '.join(PROFILES)}")
    return Path(root) / "build" / "peripherals" / memory / profile / feature_slug(sdcard, ethernet)
