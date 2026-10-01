"""Shared memory mode validation and build directory selection."""

from pathlib import Path

from gateware.soc import MEMORY_MODES


def validate_memory(memory):
    if memory not in MEMORY_MODES:
        raise ValueError(f"unknown memory mode {memory!r}; choose from {', '.join(MEMORY_MODES)}")
    return memory


def profile_build_dir(root, profile, memory="onchip"):
    validate_memory(memory)
    root = Path(root)
    if memory == "onchip":
        return root / "build" / profile
    return root / "build" / memory / profile
