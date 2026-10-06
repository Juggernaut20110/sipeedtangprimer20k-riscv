#!/usr/bin/env python3
"""Install the locked, local toolchain without system-wide writes."""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "dependencies.lock.json").read_text())
CPU_LOCK = json.loads((ROOT / "cpu-generator.lock.json").read_text())
DEPS = ROOT / ".deps"
TOOLS = ROOT / ".tools"
DOWNLOADS = TOOLS / "downloads"
PROJECT_PATCHES = {
    "litex": (
        ROOT / "patches/litex-sdram-training-status.patch",
        ROOT / "patches/litex-sdram-read-capture-diagnostic.patch",
        ROOT / "patches/litex-memtest-read-only-diagnostic.patch",
        ROOT / "patches/litex-ddr-diagnostic-boot.patch",
        ROOT / "patches/litex-project-vexriscv-isa-variants.patch",
        ROOT / "patches/litex-gowin-extra-sdc.patch",
        ROOT / "patches/litex-bios-no-ethernet-autoinit.patch",
    ),
    "litedram": (
        ROOT / "patches/litedram-gw2ddrphy-cdc.patch",
        ROOT / "patches/litedram-gw2ddrphy-dll-off-read.patch",
    ),
    "litesdcard": (),
    "lwip": (
        ROOT / "patches/lwip-tcp-option-bounds.patch",
    ),
}


def run(args, cwd=None):
    subprocess.run([str(x) for x in args], cwd=cwd, check=True)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url, target, expected):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and sha256(target) == expected:
        print(f"Verified cached {target.name}")
        return
    partial = target.with_suffix(target.suffix + ".part")
    partial.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "tang20k-litex-setup/1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output)
        actual = sha256(partial)
        if actual != expected:
            raise RuntimeError(f"SHA-256 mismatch for {url}: expected {expected}, got {actual}")
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def safe_extract_tar(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:*") as package:
        for member in package.getmembers():
            path = (destination / member.name).resolve()
            if os.path.commonpath((root, path)) != str(root):
                raise RuntimeError(f"unsafe path in archive: {member.name}")
        package.extractall(destination, filter="data")


def checkout_repositories():
    DEPS.mkdir(parents=True, exist_ok=True)
    # Remove repository-owned edits before checking dependency cleanliness. They
    # are reapplied at the end of setup, so every installed tree remains pinned
    # to the lock file plus an explicit, hashable patch in this repository.
    for name, patches in PROJECT_PATCHES.items():
        checkout = DEPS / name
        if checkout.is_dir():
            for patch in reversed(patches):
                reverse = subprocess.run(
                    ["git", "-C", str(checkout), "apply", "--reverse", "--check", str(patch)],
                    capture_output=True,
                )
                if reverse.returncode == 0:
                    run(["git", "-C", str(checkout), "apply", "--reverse", str(patch)])
    for entry in LOCK["repositories"]:
        path = DEPS / entry["name"]
        if not (path / ".git").exists():
            run(["git", "clone", "--no-checkout", entry["url"], str(path)])
        dirty = subprocess.check_output(["git", "-C", str(path), "status", "--porcelain"], text=True)
        if dirty:
            raise RuntimeError(f"dependency checkout has local changes; inspect {path} before setup can update it")
        current = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
        expected = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--verify", f"{entry['commit']}^{{commit}}"],
            capture_output=True, text=True,
        )
        if expected.returncode:
            run(["git", "-C", str(path), "fetch", "origin", entry["commit"]])
            expected = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--verify", f"{entry['commit']}^{{commit}}"],
                capture_output=True, text=True,
            )
        if expected.returncode:
            raise RuntimeError(
                f"locked revision {entry['commit']} for {entry['name']} did not resolve to a commit"
            )
        expected_commit = expected.stdout.strip()
        if current != expected_commit:
            run(["git", "-C", str(path), "checkout", "--detach", expected_commit])
        if entry.get("submodules"):
            run(["git", "-C", str(path), "submodule", "update", "--init", "--recursive"])
            for subpath, commit in entry["submodules"].items():
                actual = subprocess.check_output(["git", "-C", str(path / subpath), "rev-parse", "HEAD"], text=True).strip()
                if actual != commit:
                    raise RuntimeError(f"submodule {entry['name']}/{subpath} is {actual}, expected {commit}")
        print(f"Locked {entry['name']} at {expected_commit}")

    for name, patches in PROJECT_PATCHES.items():
        checkout = DEPS / name
        for patch in patches:
            forward = subprocess.run(
                ["git", "-C", str(checkout), "apply", "--check", str(patch)],
                capture_output=True,
            )
            if forward.returncode == 0:
                run(["git", "-C", str(checkout), "apply", str(patch)])
            else:
                reverse = subprocess.run(
                    ["git", "-C", str(checkout), "apply", "--reverse", "--check", str(patch)],
                    capture_output=True,
                )
                if reverse.returncode != 0:
                    raise RuntimeError(f"project patch {patch.name} does not apply cleanly to locked {name}")
            print(f"Applied project patch {patch.name} to {name}")


def apt_metadata(package, version):
    output = subprocess.check_output(["apt-cache", "show", f"{package}={version}"], text=True)
    fields = {}
    for line in output.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            fields.setdefault(key, value)
    return fields


def apt_is_installed(package):
    result = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Status}", package], text=True, capture_output=True)
    return result.returncode == 0 and result.stdout.strip() == "installed"


def install_openfpgaloader():
    spec = LOCK["openfpgaloader"]
    if platform.machine() not in ("x86_64", "amd64"):
        raise RuntimeError("The locked openFPGALoader package is for Ubuntu amd64.")
    metadata = apt_metadata(spec["package"], spec["version"])
    if metadata.get("SHA256") != spec["sha256"]:
        raise RuntimeError("Ubuntu apt metadata does not match the locked openFPGALoader package checksum; refresh the lock deliberately.")

    apt_dir = DOWNLOADS / "apt"
    apt_dir.mkdir(parents=True, exist_ok=True)
    deb_name = metadata["Filename"].rsplit("/", 1)[-1]
    deb = apt_dir / deb_name
    if not deb.exists() or sha256(deb) != spec["sha256"]:
        run(["apt-get", "download", f"{spec['package']}={spec['version']}"], cwd=apt_dir)
    if sha256(deb) != spec["sha256"]:
        raise RuntimeError(f"downloaded package hash does not match Ubuntu metadata: {deb}")

    install_root = TOOLS / "openfpgaloader"
    (install_root / "usr").mkdir(parents=True, exist_ok=True)
    run(["dpkg-deb", "-x", str(deb), str(install_root)])
    binary = install_root / "usr/bin/openFPGALoader"
    if not binary.is_file():
        raise RuntimeError("Ubuntu package did not contain /usr/bin/openFPGALoader")

    # Download/extract missing direct libraries reported by the package metadata.
    dependencies = re.findall(r"([a-zA-Z0-9+.-]+)(?:\s*\([^)]*\))?", metadata.get("Depends", ""))
    runtime_receipt = []
    for dependency in dict.fromkeys(dependencies):
        if apt_is_installed(dependency):
            continue
        version_text = subprocess.check_output(["apt-cache", "policy", dependency], text=True)
        match = re.search(r"Candidate:\s*(\S+)", version_text)
        if not match or match.group(1) == "(none)":
            raise RuntimeError(f"missing runtime package {dependency}; enable the Ubuntu universe repository and retry make setup")
        dep_version = match.group(1)
        dep_fields = apt_metadata(dependency, dep_version)
        dep_deb = apt_dir / dep_fields["Filename"].rsplit("/", 1)[-1]
        if not dep_deb.exists() or sha256(dep_deb) != dep_fields.get("SHA256"):
            run(["apt-get", "download", f"{dependency}={dep_version}"], cwd=apt_dir)
        if sha256(dep_deb) != dep_fields.get("SHA256"):
            raise RuntimeError(f"downloaded runtime package hash mismatch: {dependency}={dep_version}")
        run(["dpkg-deb", "-x", str(dep_deb), str(install_root)])
        runtime_receipt.append({"package": dependency, "version": dep_version, "sha256": dep_fields["SHA256"]})

    tool_bin = TOOLS / "bin"
    tool_bin.mkdir(parents=True, exist_ok=True)
    wrapper = tool_bin / "openFPGALoader"
    wrapper.write_text(
        "#!/bin/sh\n"
        'tool_root="$(CDPATH= cd -- "$(dirname -- "$0")/../openfpgaloader" && pwd)"\n'
        'local_libs="$tool_root/usr/lib/x86_64-linux-gnu:$tool_root/lib/x86_64-linux-gnu"\n'
        'export LD_LIBRARY_PATH="$local_libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
        'exec "$tool_root/usr/bin/openFPGALoader" "$@"\n'
    )
    wrapper.chmod(0o755)
    receipt = {
        "openfpgaloader": {"version": spec["version"], "sha256": spec["sha256"]},
        "runtime_packages": runtime_receipt,
    }
    (TOOLS / "toolchain-setup.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Extracted openFPGALoader {spec['version']} into {install_root}")


def install_python_and_compiler():
    python_spec = LOCK["python"]
    python_archive = DOWNLOADS / Path(python_spec["url"]).name
    fetch(python_spec["url"], python_archive, python_spec["sha256"])
    python_root = TOOLS / "python-3.12"
    python_bin = next(iter(python_root.glob("**/bin/python3.12")), None)
    if python_bin is None:
        safe_extract_tar(python_archive, python_root)
        python_bin = next(iter(python_root.glob("**/bin/python3.12")), None)
    if python_bin is None:
        raise RuntimeError("Python archive did not contain bin/python3.12")
    if subprocess.check_output([str(python_bin), "--version"], text=True).strip() != f"Python {python_spec['version']}":
        raise RuntimeError("extracted Python version does not match the lock file")

    venv_python = ROOT / ".venv/bin/python"
    if venv_python.exists():
        version = subprocess.check_output([str(venv_python), "--version"], text=True).strip()
        if version != f"Python {python_spec['version']}":
            shutil.rmtree(ROOT / ".venv")
    if not venv_python.exists():
        run([str(python_bin), "-m", "venv", str(ROOT / ".venv")])
    run([str(venv_python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(ROOT / "requirements-py312.txt")])

    # Install only from detached, verified local clones. Requirements above pin all
    # external Python wheels, while source commits are pinned in dependencies.lock.json.
    for name in [
        "migen", "litex", "litedram", "liteiclink", "liteeth", "litex-boards",
        "litesdcard", "pythondata-cpu-vexriscv", "pythondata-software-picolibc",
        "pythondata-software-compiler_rt",
    ]:
        run([str(venv_python), "-m", "pip", "install", "--no-deps", "--no-build-isolation", "--editable", str(DEPS / name)])

    gcc_spec = LOCK["riscv_gcc"]
    gcc_archive = DOWNLOADS / Path(gcc_spec["url"]).name
    fetch(gcc_spec["url"], gcc_archive, gcc_spec["sha256"])
    gcc_root = TOOLS / "riscv-gcc"
    gcc_bin = next(iter(gcc_root.glob(f"**/bin/{gcc_spec['prefix']}-gcc")), None)
    if gcc_bin is None:
        safe_extract_tar(gcc_archive, gcc_root)
        gcc_bin = next(iter(gcc_root.glob(f"**/bin/{gcc_spec['prefix']}-gcc")), None)
    if gcc_bin is None:
        raise RuntimeError("xPack archive did not contain the locked RISC-V GCC prefix")
    print(f"Installed CPython {python_spec['version']} and xPack RISC-V GCC {gcc_spec['version']}")


def install_cpu_generator_toolchain():
    artifacts = CPU_LOCK["artifacts"]
    java_spec = artifacts["java"]
    java_archive = DOWNLOADS / java_spec["file"]
    fetch(java_spec["url"], java_archive, java_spec["sha256"])
    java_root = TOOLS / "java-8u432"
    java_bin = next(iter(java_root.glob("**/bin/java")), None)
    if java_bin is None:
        safe_extract_tar(java_archive, java_root)
        java_bin = next(iter(java_root.glob("**/bin/java")), None)
    if java_bin is None:
        raise RuntimeError("Temurin archive did not contain bin/java")
    java_home = java_bin.parent.parent
    java_output = subprocess.run([str(java_bin), "-XshowSettings:properties", "-version"],
                                 capture_output=True, text=True)
    match = re.search(r"^\s*java\.version\s*=\s*(\S+)",
                      java_output.stdout + java_output.stderr, re.M)
    if java_output.returncode or not match or match.group(1) != java_spec["version_output"]:
        actual = match.group(1) if match else "unknown"
        raise RuntimeError(f"Temurin runtime is {actual}, expected {java_spec['version_output']}")

    sbt_spec = artifacts["sbt"]
    sbt_archive = DOWNLOADS / sbt_spec["file"]
    fetch(sbt_spec["url"], sbt_archive, sbt_spec["sha256"])
    sbt_root = TOOLS / f"sbt-{sbt_spec['version']}"
    sbt_script = sbt_root / "sbt/bin/sbt"
    if not sbt_script.is_file():
        safe_extract_tar(sbt_archive, sbt_root)
    if not sbt_script.is_file():
        raise RuntimeError("SBT archive did not contain sbt/bin/sbt")

    tool_bin = TOOLS / "bin"
    tool_bin.mkdir(parents=True, exist_ok=True)
    java_link = tool_bin / "java"
    if java_link.exists() or java_link.is_symlink():
        java_link.unlink()
    java_link.symlink_to(java_bin.resolve())
    sbt_wrapper = tool_bin / "sbt"
    sbt_wrapper.write_text(
        "#!/bin/sh\n"
        'tool_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"\n'
        f'export JAVA_HOME="{java_home}"\n'
        'export PATH="$JAVA_HOME/bin:$PATH"\n'
        'export HOME="$tool_root/sbt-home"\n'
        'export COURSIER_CACHE="$tool_root/coursier-cache"\n'
        'export SBT_OPTS="${SBT_OPTS:+$SBT_OPTS }-Dsbt.server.autostart=false -Duser.home=$HOME -Dsbt.global.base=$HOME/.sbt -Dsbt.boot.directory=$HOME/.sbt/boot -Dsbt.ivy.home=$HOME/.ivy2"\n'
        f'exec "{sbt_script}" "$@"\n'
    )
    sbt_wrapper.chmod(0o755)
    env = os.environ.copy()
    env["JAVA_HOME"] = str(java_home)
    env["PATH"] = os.pathsep.join((str(tool_bin), str(java_home / "bin"), env.get("PATH", "")))
    env["HOME"] = str(TOOLS / "sbt-home")
    env["COURSIER_CACHE"] = str(TOOLS / "coursier-cache")
    env["SBT_OPTS"] = " ".join((
        env.get("SBT_OPTS", ""),
        "-Dsbt.server.autostart=false",
        f"-Duser.home={env['HOME']}",
        f"-Dsbt.global.base={env['HOME']}/.sbt",
        f"-Dsbt.boot.directory={env['HOME']}/.sbt/boot",
        f"-Dsbt.ivy.home={env['HOME']}/.ivy2",
    )).strip()
    version = subprocess.run([str(sbt_wrapper), "--script-version"], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=60)
    if version.returncode or sbt_spec["version"] not in version.stdout + version.stderr:
        raise RuntimeError(f"installed SBT version check failed: {(version.stdout + version.stderr).strip()[:300]}")
    receipt = {
        "java": {"version": java_spec["version_output"], "sha256": java_spec["sha256"]},
        "sbt": {"version": sbt_spec["version"], "sha256": sbt_spec["sha256"]},
        "scala": CPU_LOCK["tools"]["scala"],
        "spinalhdl": CPU_LOCK["tools"]["spinalhdl"],
    }
    (TOOLS / "cpu-generator-setup.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Installed pinned Temurin {java_spec['version_output']} and SBT {sbt_spec['version']}")


def main():
    TOOLS.mkdir(parents=True, exist_ok=True)
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    checkout_repositories()
    install_openfpgaloader()
    install_python_and_compiler()
    install_cpu_generator_toolchain()
    print("Setup complete. Run make doctor to check build tools and hardware access.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, RuntimeError, tarfile.TarError) as error:
        print(f"setup failed: {error}", file=sys.stderr)
        sys.exit(1)
