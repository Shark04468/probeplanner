"""Regenerate tiny independent NumPy/ZIP inputs for NPZ loader compatibility."""
import hashlib
import io
import json
from pathlib import Path
import zipfile

import numpy as np

HERE = Path(__file__).resolve().parent


def npy(array):
    output = io.BytesIO()
    np.save(output, array, allow_pickle=False)
    return output.getvalue()


def member_archive(name, data):
    info = zipfile.ZipInfo("brain.npy", date_time=(1980, 1, 1, 0, 0, 0))
    with zipfile.ZipFile(HERE / name, "w") as archive:
        archive.writestr(info, data)


def main():
    brain = np.array([0, 0.25, -0.5, 1, 2, 3.5, 10, 20], dtype="<f4").reshape(2, 2, 2)
    auxiliary = np.array([100, 101, 102, 103, 104, 105], dtype="<f4").reshape(2, 1, 3)
    np.savez(HERE / "stored.npz", brain=brain, auxiliary=auxiliary)
    np.savez_compressed(HERE / "deflated.npz", brain=brain, auxiliary=auxiliary)
    member_archive("invalid_npy.npz", b"not a NumPy array\n")
    member_archive("truncated_npy.npz", npy(brain)[:-4])
    np.savez(HERE / "float64.npz", brain=brain.astype("<f8"))
    np.savez(HERE / "fortran.npz", brain=np.asfortranarray(brain))
    np.savez(HERE / "two_dimensional.npz", brain=brain.reshape(2, 4))
    (HERE / "invalid_archive.npz").write_bytes(b"not a ZIP archive\n")
    manifest = {"generator": "NumPy and Python standard-library zipfile, not the Rust ZIP reader",
                "numpyVersion": np.__version__, "expectedBrainShape": [2, 2, 2],
                "expectedBrainValues": brain.ravel(order="C").tolist(),
                "expectedAuxiliaryShape": [2, 1, 3], "expectedAuxiliaryValues": auxiliary.ravel().tolist(),
                "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(HERE.glob("*.npz"))}}
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"fixtures": len(manifest["files"]), "totalBytes": sum(p.stat().st_size for p in HERE.glob("*.npz"))}))


if __name__ == "__main__":
    main()
