#!/usr/bin/env python3
"""Publish a matched FPGA image and refresh unified-loader checksums."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "pico_usb" / "prebuilt" / "SHA256SUMS"
ARTIFACTS = {
    REPO_ROOT / "project" / "DAQ_LAUNCH.runs" / "impl_1" / "top.bit":
        REPO_ROOT / "prebuilt" / "top.bit",
    REPO_ROOT / "hw" / "DAQ_LAUNCH.ltx":
        REPO_ROOT / "prebuilt" / "DAQ_LAUNCH.ltx",
    REPO_ROOT / "sw" / "workspace" / "firmware" / "Debug" / "firmware.elf":
        REPO_ROOT / "prebuilt" / "firmware.elf",
}
MANIFEST_ARTIFACTS = ("top.bit", "firmware.elf")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=f".{destination.name}.", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        shutil.copy2(source, temporary_path)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def update_manifest() -> None:
    hashes = {
        name: sha256(REPO_ROOT / "prebuilt" / name)
        for name in MANIFEST_ARTIFACTS
    }
    lines = MANIFEST.read_text(encoding="ascii").splitlines()
    seen: set[str] = set()
    updated: list[str] = []
    for line in lines:
        _digest, filename = line.split(maxsplit=1)
        filename = filename.strip()
        if filename in hashes:
            updated.append(f"{hashes[filename]}  {filename}")
            seen.add(filename)
        else:
            updated.append(line)
    missing = set(hashes) - seen
    if missing:
        raise RuntimeError(
            "checksum manifest is missing entries: " + ", ".join(sorted(missing))
        )
    MANIFEST.write_text("\n".join(updated) + "\n", encoding="ascii")


def validate_release() -> None:
    sys.path.insert(0, str(REPO_ROOT))
    from pico_usb.load_and_test import require_artifacts

    require_artifacts()
    if not (REPO_ROOT / "prebuilt" / "DAQ_LAUNCH.ltx").is_file():
        raise RuntimeError("missing prebuilt/DAQ_LAUNCH.ltx")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish matched board artifacts and loader checksums."
    )
    parser.add_argument(
        "--check", action="store_true",
        help="validate the current prebuilt release without copying build outputs",
    )
    args = parser.parse_args()

    if not args.check:
        missing = [str(source) for source in ARTIFACTS if not source.is_file()]
        if missing:
            raise RuntimeError("missing build artifact(s):\n  " + "\n  ".join(missing))
        for source, destination in ARTIFACTS.items():
            atomic_copy(source, destination)
        update_manifest()

    validate_release()
    for destination in ARTIFACTS.values():
        print(f"{sha256(destination)}  {destination.relative_to(REPO_ROOT)}")
    print("Board artifact release validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())