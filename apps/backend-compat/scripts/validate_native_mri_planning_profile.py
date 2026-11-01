#!/usr/bin/env python
"""Validate native MRI planning manifest integrity."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def resolve(base: Path, raw: str) -> Path:
    p = Path(str(raw or "").strip())
    if not p.is_absolute():
        p = (base / p).resolve()
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate native MRI planning manifest.")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--summary-out", default="")
    args = ap.parse_args()

    manifest_path = Path(args.manifest).expanduser().resolve()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = data.get("artifacts") if isinstance(data.get("artifacts"), dict) else {}
    summary = {
        "ok": True,
        "manifest": str(manifest_path),
        "schemaVersion": data.get("schemaVersion"),
        "profileId": data.get("profileId"),
        "planningReadiness": data.get("planningReadiness"),
        "missing": [],
        "hashMismatches": [],
        "checked": [],
    }
    for key, rec in artifacts.items():
        if not isinstance(rec, dict) or not bool(rec.get("required", False)):
            continue
        path_text = str(rec.get("path") or "").strip()
        if not path_text:
            summary["ok"] = False
            summary["missing"].append({"key": key, "reason": "path_empty"})
            continue
        path = resolve(manifest_path.parent, path_text)
        if not path.exists():
            summary["ok"] = False
            summary["missing"].append({"key": key, "reason": "file_missing", "path": str(path)})
            continue
        expected = str(rec.get("sha256") or "").strip().lower()
        actual = sha256_file(path).lower()
        if expected != actual:
            summary["ok"] = False
            summary["hashMismatches"].append({"key": key, "expected": expected, "actual": actual})
        summary["checked"].append({"key": key, "path": str(path), "sha256": actual})

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.summary_out:
        out = Path(args.summary_out).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
