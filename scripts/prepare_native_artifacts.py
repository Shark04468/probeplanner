import argparse
import os
from pathlib import Path

import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", default=os.environ.get("PROBEPLANNER_ARTIFACT_ROOT", ""))
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--sdf-channel", default="signed_sdf_mm")
    parser.add_argument(
        "--sdf-channels",
        default="",
        help="Comma-separated layered_sdf channels to export as mmap-ready .npy files.",
    )
    args = parser.parse_args()

    if not str(args.artifact_root or "").strip():
        raise SystemExit("PROBEPLANNER_ARTIFACT_ROOT or --artifact-root is required")
    root = Path(args.artifact_root)
    output_dir = Path(args.output_dir) if args.output_dir else root
    output_dir.mkdir(parents=True, exist_ok=True)
    npz_path = root / "layered_sdf.npz"
    if not npz_path.exists():
        raise FileNotFoundError(npz_path)

    with np.load(npz_path) as archive:
        requested = [
            part.strip()
            for part in str(args.sdf_channels or args.sdf_channel).split(",")
            if part.strip()
        ]
        if args.sdf_channel and args.sdf_channel not in requested:
            requested.insert(0, args.sdf_channel)
        for channel in dict.fromkeys(requested):
            npy_path = output_dir / f"{channel}.npy"
            if npy_path.exists() and npy_path.stat().st_mtime >= npz_path.stat().st_mtime:
                print(f"ready {npy_path}")
                continue
            if channel not in archive:
                raise KeyError(f"{channel} not found in {npz_path}")
            values = archive[channel]
            if values.dtype != np.float32:
                values = values.astype(np.float32, copy=False)
            np.save(npy_path, np.ascontiguousarray(values))
            print(f"wrote {npy_path}")


if __name__ == "__main__":
    main()
