import argparse
import json
import sys
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail closed when active MRI registration QC does not pass.")
    parser.add_argument("--backend-url", default="http://127.0.0.1:5000")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

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
