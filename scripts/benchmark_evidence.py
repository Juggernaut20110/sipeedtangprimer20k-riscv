"""Create and verify immutable snapshots for benchmark artifacts."""

import datetime
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = Path("build/validation/benchmark-evidence")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repo_path(root, relative):
    if not isinstance(relative, str) or not relative:
        raise ValueError("benchmark evidence path is empty")
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"benchmark evidence path leaves the repository: {relative}") from error
    return path


def read_bundle(reference, root=ROOT):
    """Read a bundle only when its manifest path and digest are valid."""
    if not isinstance(reference, dict):
        return None
    try:
        manifest_path = _repo_path(root, reference.get("path"))
        if not manifest_path.is_file() or sha256(manifest_path) != reference.get("sha256"):
            return None
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("schema_version") != 1:
            return None
        return manifest
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def bundle_file_matches(reference, relative, expected_sha256, root=ROOT):
    manifest = read_bundle(reference, root)
    if manifest is None:
        return False
    record = manifest.get("files", {}).get(relative)
    if not isinstance(record, dict) or record.get("sha256") != expected_sha256:
        return False
    try:
        snapshot = _repo_path(root, f"{Path(reference['path']).parent}/{record['snapshot']}")
        return snapshot.is_file() and sha256(snapshot) == expected_sha256
    except (KeyError, OSError, ValueError):
        return False


def bundle_identity(reference, root=ROOT):
    manifest = read_bundle(reference, root)
    if manifest is None:
        return None, None
    return manifest.get("benchmark_metadata"), manifest.get("build_metadata")


def bundle_is_current(reference, fingerprint, root=ROOT):
    manifest = read_bundle(reference, root)
    if (not manifest or manifest.get("source_fingerprint") != fingerprint
            or manifest.get("benchmark_metadata", {}).get("source_fingerprint") != fingerprint):
        return False
    return all(
        bundle_file_matches(reference, relative, record.get("sha256"), root)
        for relative, record in manifest.get("files", {}).items()
    )


def create_bundle(build_dir, benchmark_metadata, build_metadata, root=ROOT):
    """Snapshot every fingerprinted input and exact runnable artifact once."""
    profile = benchmark_metadata["profile"]
    memory = benchmark_metadata.get("memory_mode", "onchip")
    fingerprint = benchmark_metadata["source_fingerprint"]
    relative_bundle = BUNDLE_ROOT / memory / profile / fingerprint
    destination = root / relative_bundle
    manifest_path = destination / "manifest.json"
    if manifest_path.is_file():
        reference = {
            "path": str((relative_bundle / "manifest.json").as_posix()),
            "sha256": sha256(manifest_path),
        }
        if bundle_is_current(reference, fingerprint, root):
            return reference
        raise RuntimeError(f"immutable benchmark evidence bundle is inconsistent: {relative_bundle}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{fingerprint}-", dir=destination.parent))
    records = {}

    def preserve(relative, expected=None):
        source = _repo_path(root, relative)
        if not source.is_file():
            raise FileNotFoundError(f"benchmark evidence input is missing: {relative}")
        actual = sha256(source)
        if expected and actual != expected:
            raise RuntimeError(f"benchmark evidence input changed before archival: {relative}")
        snapshot_relative = Path("files") / Path(relative)
        snapshot = temporary / snapshot_relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, snapshot)
        records[relative] = {
            "snapshot": snapshot_relative.as_posix(),
            "sha256": actual,
            "bytes": source.stat().st_size,
        }

    try:
        for relative, expected in sorted(benchmark_metadata.get("source_hashes", {}).items()):
            preserve(relative, expected)

        build_relative = str((Path(build_dir).resolve() / "build-metadata.json").relative_to(root.resolve()))
        benchmark_relative = str((Path(build_dir).resolve() / "benchmark/benchmark-metadata.json").relative_to(root.resolve()))
        preserve(build_relative)
        preserve(benchmark_relative)
        preserve(benchmark_metadata["bitstream"], benchmark_metadata["bitstream_sha256"])
        for image in benchmark_metadata.get("images", {}).values():
            preserve(image["binary"], image["binary_sha256"])

        for selection_name in ("cpu-profile-selection.json", "maxperf-profile-selection.json"):
            selection_path = root / selection_name
            if selection_path.is_file():
                preserve(selection_name)

        cpu_rtl = benchmark_metadata.get("cpu_rtl")
        if cpu_rtl:
            candidate_manifest = Path(cpu_rtl).with_name("candidate.json")
            candidate_relative = candidate_manifest.as_posix()
            if (root / candidate_relative).is_file():
                preserve(candidate_relative)

        pnr_dir = Path(build_dir).resolve() / "gateware/impl/pnr"
        if pnr_dir.is_dir():
            for report_path in sorted(pnr_dir.rglob("*")):
                if report_path.is_file() and report_path.suffix.lower() in (".rpt", ".txt", ".json", ".html"):
                    preserve(str(report_path.relative_to(root.resolve())))

        manifest = {
            "schema_version": 1,
            "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "profile": profile,
            "memory_mode": memory,
            "source_fingerprint": fingerprint,
            "build_metadata": build_metadata,
            "benchmark_metadata": benchmark_metadata,
            "files": records,
        }
        (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.replace(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    return {
        "path": str((relative_bundle / "manifest.json").as_posix()),
        "sha256": sha256(manifest_path),
    }
