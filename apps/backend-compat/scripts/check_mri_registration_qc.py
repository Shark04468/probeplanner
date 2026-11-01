import argparse
import json
from pathlib import Path
import sys
import urllib.request


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _artifact_path(manifest: dict, key: str) -> Path | None:
    artifact = manifest.get("artifacts", {}).get(key, {}) if isinstance(manifest, dict) else {}
    if not isinstance(artifact, dict):
        return None
    path = str(artifact.get("path") or "").strip()
    return Path(path) if path else None


def _metric(payload: dict, *keys: str):
    if not isinstance(payload, dict):
        return None
    candidates = [payload]
    if isinstance(payload.get("metrics"), dict):
        candidates.insert(0, payload["metrics"])
    for source in candidates:
        for key in keys:
            value = source.get(key)
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return None


def _evaluate(metrics: dict, thresholds: dict) -> tuple[bool, list[str]]:
    reasons = []
    dice = metrics.get("dice")
    hd95 = metrics.get("hd95Mm")
    tre = metrics.get("treMm")
    if dice is None or dice < thresholds["diceMin"]:
        reasons.append(f"dice {dice} < {thresholds['diceMin']}")
    if hd95 is None or hd95 > thresholds["hd95MaxMm"]:
        reasons.append(f"hd95Mm {hd95} > {thresholds['hd95MaxMm']}")
    if tre is None or tre > thresholds["treMaxMm"]:
        reasons.append(f"treMm {tre} > {thresholds['treMaxMm']}")
    return len(reasons) == 0, reasons


def _from_manifest(manifest_path: Path, thresholds: dict) -> dict:
    manifest = _load_json(manifest_path)
    stats_path = _artifact_path(manifest, "stats")
    registration_path = _artifact_path(manifest, "registration_summary")
    stats = _load_json(stats_path) if stats_path and stats_path.exists() else {}
    registration = _load_json(registration_path) if registration_path and registration_path.exists() else {}
    metrics = {
        "dice": _metric(stats, "dice"),
        "hd95Mm": _metric(stats, "hd95Mm", "hd95"),
        "treMm": _metric(stats, "treMm", "tre"),
    }
    for key, aliases in {
        "dice": ("dice",),
        "hd95Mm": ("hd95Mm", "hd95"),
        "treMm": ("treMm", "tre"),
    }.items():
        if metrics[key] is None:
            metrics[key] = _metric(registration, *aliases)
    passed, reasons = _evaluate(metrics, thresholds)
    return {
        "ok": passed,
        "manifest": str(manifest_path),
        "profileId": manifest.get("profileId"),
        "metrics": metrics,
        "qualityGate": {
            "passed": passed,
            "thresholds": thresholds,
            "reasons": reasons,
            "source": "artifact_manifest",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail closed when active MRI registration QC does not pass.")
    parser.add_argument("--backend-url", default="http://127.0.0.1:5000")
    parser.add_argument("--manifest", default="", help="Validate QC metrics directly from artifact_manifest.json.")
    parser.add_argument("--dice-min", type=float, default=0.8)
    parser.add_argument("--hd95-max-mm", type=float, default=0.45)
    parser.add_argument("--tre-max-mm", type=float, default=0.3)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    thresholds = {
        "diceMin": float(args.dice_min),
        "hd95MaxMm": float(args.hd95_max_mm),
        "treMaxMm": float(args.tre_max_mm),
    }

    if args.manifest:
        payload = _from_manifest(Path(args.manifest).expanduser().resolve(), thresholds)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 2

    url = args.backend_url.rstrip("/") + "/api/mri/registration/qc"
    try:
        with urllib.request.urlopen(url, timeout=args.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(json.dumps({"ok": False, "url": url, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2

    quality_gate = payload.get("qualityGate") if isinstance(payload, dict) else None
    passed = bool(isinstance(quality_gate, dict) and quality_gate.get("passed") is True)
    result = {
        "ok": passed,
        "url": url,
        "metrics": payload.get("metrics") if isinstance(payload, dict) else None,
        "qualityGate": quality_gate,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
