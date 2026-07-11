#!/usr/bin/env python3
"""Fail-closed checks for the public source and Supplementary Data release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ABSOLUTE_WINDOWS_PATH = re.compile(rb"(?i)(?:[A-Z]:\\(?:Users|mri_ccf|Documents and Settings)\\)")
FORBIDDEN_PARTS = {"node_modules", "target", "build", "dist", ".cache", ".playwright-cli", "__pycache__"}
TEXT_SUFFIXES = {".c", ".cc", ".cff", ".cpp", ".csv", ".css", ".html", ".ini", ".js", ".json", ".jsx", ".md", ".mjs", ".ps1", ".py", ".rs", ".svg", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml"}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--tag", default="v0.2.0-paper")
    args = parser.parse_args()
    errors: list[str] = []

    has_git = (ROOT / ".git").exists()
    if has_git:
        listed = git("ls-files", "--cached", "--others", "--exclude-standard").splitlines()
        paths = [ROOT / item for item in listed if item]
    else:
        paths = [
            path
            for path in ROOT.rglob("*")
            if path.is_file() and not any(part in FORBIDDEN_PARTS for part in path.relative_to(ROOT).parts)
        ]
    for path in paths:
        relative = path.relative_to(ROOT)
        if any(part in FORBIDDEN_PARTS for part in relative.parts):
            errors.append(f"forbidden generated path: {relative.as_posix()}")
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            if ABSOLUTE_WINDOWS_PATH.search(path.read_bytes()):
                errors.append(f"absolute Windows path: {relative.as_posix()}")

    manifest_path = ROOT / "cases" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for case in manifest.get("cases", []):
        request = ROOT / "cases" / "requests" / f"{case['id']}.json"
        if not request.exists():
            errors.append(f"manifest request missing: {request.relative_to(ROOT).as_posix()}")

    supplement = ROOT / "supplementary-data"
    supplement_manifest = json.loads((supplement / "manifest.json").read_text(encoding="utf-8"))
    if supplement_manifest.get("releaseTag") != args.tag:
        errors.append("Supplementary Data release tag does not match requested tag")
    for item in supplement_manifest.get("files", []):
        path = supplement / item["path"]
        if not path.exists() or hash_file(path) != item["sha256"]:
            errors.append(f"Supplementary Data checksum mismatch: {item['path']}")

    expected_versions = {
        "package.json": "0.2.0",
        "apps/ui/package.json": "0.2.0",
    }
    for relative, expected in expected_versions.items():
        actual = json.loads((ROOT / relative).read_text(encoding="utf-8"))["version"]
        if actual != expected:
            errors.append(f"version mismatch in {relative}: {actual} != {expected}")
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    if 'version: "0.2.0-paper"' not in citation:
        errors.append("CITATION.cff version is not 0.2.0-paper")

    if args.require_clean and not has_git:
        errors.append("--require-clean requires a Git checkout")
    if args.require_clean and has_git and git("status", "--porcelain"):
        errors.append("git worktree is not clean")
    tags = git("tag", "--points-at", "HEAD").splitlines() if has_git else []
    if args.require_clean and has_git and args.tag not in tags:
        errors.append(f"HEAD is not tagged {args.tag}")

    if errors:
        print("Release verification FAILED:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Release verification passed for {len(paths)} publishable files and tag policy {args.tag}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
