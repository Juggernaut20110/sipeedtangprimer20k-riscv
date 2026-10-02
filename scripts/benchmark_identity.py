"""Canonical fingerprint payloads for CoreMark build and report identities."""

import hashlib
import json


CURRENT_IDENTITY_SCHEMA = 2
LEGACY_IDENTITY_SCHEMA = 1


def stable_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def benchmark_fingerprint_payload(metadata, build_metadata=None, *, schema_version=None):
    """Return the canonical modern fingerprint payload.

    `metadata` is the benchmark metadata being fingerprinted. During a build it
    may be a small identity dict; during verification it is the saved metadata.
    The SoC build manifest is authoritative for CPU and memory configuration.
    Schema 1 reproduces the original modern builder payload exactly. Schema 2
    adds an explicit version marker so future payload changes are intentional.
    """
    build = build_metadata if isinstance(build_metadata, dict) else metadata
    version = schema_version or metadata.get("identity_schema_version", LEGACY_IDENTITY_SCHEMA)
    if version not in (LEGACY_IDENTITY_SCHEMA, CURRENT_IDENTITY_SCHEMA):
        raise ValueError(f"unsupported benchmark identity schema: {version}")

    cpu_variant = metadata.get("cpu_variant")
    if cpu_variant is None:
        cpu_variant = build.get("cpu_variant")
    cpu_candidate = metadata.get("cpu_candidate")
    if cpu_candidate is None:
        cpu_candidate = build.get("cpu_candidate")
    memory_mode = metadata.get("memory_mode")
    if memory_mode is None:
        memory_mode = build.get("memory_mode", "onchip")
    payload = {
        "profile": metadata.get("profile"),
        "cpu_variant": cpu_variant,
        "cpu_candidate": cpu_candidate,
        "memory_mode": memory_mode,
        "coremark_commit": metadata.get("coremark", {}).get("commit"),
        "source_hashes": metadata.get("source_hashes"),
        "bitstream_sha256": metadata.get("bitstream_sha256"),
        "compiler": metadata.get("compiler"),
        "compiler_version": metadata.get("compiler_version"),
        "compiler_flags": metadata.get("compiler_flags"),
        "include_flags": metadata.get("include_flags"),
        "system_clock_hz": metadata.get("clock_hz"),
        "cpu_configuration": build.get(
            "cpu_configuration", metadata.get("cpu_configuration")
        ),
        "memory_configuration": build.get(
            "memory", metadata.get("memory_configuration")
        ),
    }
    selection = metadata.get("cpu_profile_selection")
    if selection is None:
        selection = build.get("cpu_profile_selection")
    if selection is not None:
        payload["cpu_profile_selection"] = selection
    if version >= CURRENT_IDENTITY_SCHEMA:
        payload["identity_schema_version"] = version
    return payload


def legacy_fingerprint_payload(metadata):
    """Return the pre-memory-mode report schema without modern CPU fields."""
    coremark = metadata.get("coremark", {})
    return {
        "profile": metadata.get("profile"),
        "coremark_commit": coremark.get("commit"),
        "source_hashes": metadata.get("source_hashes"),
        "bitstream_sha256": metadata.get("bitstream_sha256"),
        "compiler": metadata.get("compiler"),
        "compiler_version": metadata.get("compiler_version"),
        "compiler_flags": metadata.get("compiler_flags"),
        "include_flags": metadata.get("include_flags"),
        "system_clock_hz": metadata.get("clock_hz"),
    }


def fingerprint_matches(metadata, build_metadata=None):
    """Check that the stored fingerprint describes its metadata and SoC build."""
    legacy = (
        "identity_schema_version" not in metadata
        and "memory_mode" not in metadata
        and "cpu_candidate" not in metadata
    )
    if legacy:
        return stable_hash(legacy_fingerprint_payload(metadata)) == metadata.get("source_fingerprint")

    version = metadata.get("identity_schema_version", LEGACY_IDENTITY_SCHEMA)
    if version not in (LEGACY_IDENTITY_SCHEMA, CURRENT_IDENTITY_SCHEMA):
        return False
    build = build_metadata if isinstance(build_metadata, dict) else None
    if build is not None:
        for field in ("cpu_variant", "cpu_candidate", "cpu_profile_selection"):
            if (field in build and field in metadata
                    and (version >= CURRENT_IDENTITY_SCHEMA or metadata.get(field) is not None)
                    and build.get(field) != metadata.get(field)):
                return False
        if ("memory_mode" in build
                and "memory_mode" in metadata
                and build.get("memory_mode", "onchip") != metadata.get("memory_mode", "onchip")):
            return False

    try:
        payload = benchmark_fingerprint_payload(metadata, build, schema_version=version)
    except (TypeError, ValueError):
        return False
    if version >= CURRENT_IDENTITY_SCHEMA and metadata.get("fingerprint_payload") != payload:
        return False
    return stable_hash(payload) == metadata.get("source_fingerprint")
