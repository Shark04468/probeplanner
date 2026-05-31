#!/usr/bin/env python
"""
Build CCF-aligned MRI appearance meshes from MRI+CCF profile volumes.

The generated OBJ vertices use the same coordinate contract as Allen CCF
structure meshes: "v AP_um DV_um ML_um". Unity's MeshLoader then applies the
existing CCF->Unity transform, so these surfaces align with CCF labels.

Outputs:
  <output-dir>/surface_manifest.json
  <output-dir>/<layer>.lod0.obj
  <output-dir>/<layer>.lod1.obj
  <output-dir>/<layer>.lod2.obj
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from path_defaults import ants_bin, backend_root, mri_path, workspace_root
from typing import Dict, Iterable, List, Tuple

import numpy as np
from skimage import measure


DEFAULT_PROFILE_DIR = mri_path("planner_artifacts/ambmc_true_outer_full")
DEFAULT_ATLAS_SHAPE = (528, 320, 456)
LAYER_DEFS = {
    "brain_surface": {
        "displayName": "MRI brain surface",
        "source": "annotation",
        "description": "Brain parenchyma surface extracted from annotation > 0.",
    },
    "outer_tissue": {
        "displayName": "Outer tissue boundary",
        "source": "outer_tissue_mask",
        "description": "Outer tissue mask surface.",
    },
    "skull": {
        "displayName": "Skull boundary",
        "source": "skull_mask",
        "description": "Skull mask surface.",
    },
}


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(profile_dir: Path) -> dict:
    path = profile_dir / "artifact_manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_path(profile_dir: Path, manifest: dict, key: str, fallback_name: str) -> Path:
    item = ((manifest.get("artifacts") or {}).get(key) or {})
    raw = item.get("path") or str(profile_dir / fallback_name)
    path = Path(raw)
    if not path.is_absolute():
        path = profile_dir / path
    return path


def manifest_path_text(path: Path, profile_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(profile_dir.resolve())).replace("\\", "/")
    except Exception:
        return str(path.resolve())


def load_volume(path: Path):
    if not path.exists():
        raise FileNotFoundError(str(path))
    if path.suffix.lower() == ".npz":
        data = np.load(path)
        key = "arr_0"
        if key not in data.files:
            key = data.files[0]
        return data[key]
    return np.load(path, mmap_mode="r")


def build_layer_mask(layer: str, paths: Dict[str, Path]) -> np.ndarray:
    if layer == "brain_surface":
        arr = load_volume(paths["annotation"])
        return np.asarray(arr > 0, dtype=np.uint8)
    if layer == "outer_tissue":
        arr = load_volume(paths["outer_tissue_mask"])
        return np.asarray(arr > 0, dtype=np.uint8)
    if layer == "skull":
        arr = load_volume(paths["skull_mask"])
        return np.asarray(arr > 0, dtype=np.uint8)
    raise ValueError(f"Unsupported layer: {layer}")


def write_obj(
    path: Path,
    verts_ijk: np.ndarray,
    faces: np.ndarray,
    normals_ijk: np.ndarray | None,
    voxel_size_um: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# ProbePlanner MRI surface mesh\n")
        fh.write("# Coordinate order: AP_um DV_um ML_um\n")
        for v in verts_ijk:
            ap = float(v[0]) * voxel_size_um
            dv = float(v[1]) * voxel_size_um
            ml = float(v[2]) * voxel_size_um
            fh.write(f"v {ap:.5f} {dv:.5f} {ml:.5f}\n")
        if normals_ijk is not None and len(normals_ijk) == len(verts_ijk):
            for n in normals_ijk:
                fh.write(f"vn {float(n[0]):.7f} {float(n[1]):.7f} {float(n[2]):.7f}\n")
            for tri in faces:
                a, b, c = (int(tri[0]) + 1, int(tri[1]) + 1, int(tri[2]) + 1)
                fh.write(f"f {a}//{a} {b}//{b} {c}//{c}\n")
        else:
            for tri in faces:
                a, b, c = (int(tri[0]) + 1, int(tri[1]) + 1, int(tri[2]) + 1)
                fh.write(f"f {a} {b} {c}\n")


def extract_surface(mask: np.ndarray, step_size: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    if mask.ndim != 3:
        raise ValueError(f"Expected 3D mask, got shape={mask.shape}")
    if int(mask.sum()) <= 0:
        raise ValueError("Mask is empty")
    volume = np.pad(mask.astype(np.uint8, copy=False), 1, mode="constant", constant_values=0)
    verts, faces, normals, _ = measure.marching_cubes(
        volume,
        level=0.5,
        spacing=(1.0, 1.0, 1.0),
        step_size=max(1, int(step_size)),
        allow_degenerate=False,
    )
    # Remove padding offset so coordinates remain in atlas voxel index space.
    verts -= 1.0
    return verts, faces, normals


def parse_lod_steps(raw: str) -> List[int]:
    out = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        value = max(1, int(part))
        if value not in out:
            out.append(value)
    return out or [1, 2, 4]


def update_artifact_manifest(profile_dir: Path, surface_manifest: Path, atlas_shape: Iterable[int]) -> None:
    manifest_path = profile_dir / "artifact_manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = manifest.setdefault("artifacts", {})
    artifacts["mri_surface_meshes"] = {
        "kind": "mesh_bundle",
        "required": False,
        "path": manifest_path_text(surface_manifest, profile_dir),
        "exists": surface_manifest.exists(),
        "shape": list(atlas_shape),
        "sha256": sha256_file(surface_manifest) if surface_manifest.exists() else None,
        "version": "v1",
    }
    manifest["updatedAt"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def build(args: argparse.Namespace) -> dict:
    profile_dir = Path(args.profile_dir).resolve()
    manifest = load_manifest(profile_dir)
    paths = {
        "annotation": Path(args.annotation).resolve() if args.annotation else artifact_path(profile_dir, manifest, "annotation", "annotation.npy"),
        "skull_mask": Path(args.skull_mask).resolve() if args.skull_mask else artifact_path(profile_dir, manifest, "skull_mask", "skull_mask.npy"),
        "outer_tissue_mask": Path(args.outer_tissue_mask).resolve() if args.outer_tissue_mask else artifact_path(profile_dir, manifest, "outer_tissue_mask", "outer_tissue_mask.npy"),
    }
    output_dir = Path(args.output_dir).resolve() if args.output_dir else (profile_dir / "mri_surfaces")
    layers = [x.strip() for x in args.layers.split(",") if x.strip()]
    lod_steps = parse_lod_steps(args.lod_steps)
    voxel_size_um = float(args.voxel_size_um)
    atlas_shape = tuple(manifest.get("atlasShape") or DEFAULT_ATLAS_SHAPE)

    surface_manifest = {
        "schemaVersion": "probeplanner.mri_surface_meshes.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "profileDir": str(profile_dir),
        "voxelSizeUm": voxel_size_um,
        "atlasShape": list(atlas_shape),
        "coordinateOrder": "AP_um,DV_um,ML_um",
        "layers": {},
    }

    for layer in layers:
        if layer not in LAYER_DEFS:
            raise ValueError(f"Unsupported layer '{layer}'. Valid: {', '.join(LAYER_DEFS)}")
        print(f"[surface] building {layer}")
        mask = build_layer_mask(layer, paths)
        if tuple(mask.shape) != tuple(atlas_shape):
            raise ValueError(f"{layer} mask shape {mask.shape} != atlas shape {atlas_shape}")
        layer_info = {
            **LAYER_DEFS[layer],
            "voxelCount": int(mask.sum()),
            "lods": {},
        }
        for lod_idx, step in enumerate(lod_steps):
            out_path = output_dir / f"{layer}.lod{lod_idx}.obj"
            print(f"[surface] {layer} lod{lod_idx} step={step} -> {out_path}")
            verts, faces, normals = extract_surface(mask, step_size=step)
            write_obj(out_path, verts, faces, normals, voxel_size_um=voxel_size_um)
            layer_info["lods"][str(lod_idx)] = {
                "path": manifest_path_text(out_path, profile_dir),
                "stepSize": int(step),
                "vertexCount": int(len(verts)),
                "faceCount": int(len(faces)),
                "sizeBytes": int(out_path.stat().st_size),
                "sha256": sha256_file(out_path),
            }
            if lod_idx == 0:
                canonical = output_dir / f"{layer}.obj"
                try:
                    shutil.copyfile(out_path, canonical)
                    layer_info["canonicalPath"] = manifest_path_text(canonical, profile_dir)
                except Exception:
                    layer_info["canonicalPath"] = manifest_path_text(out_path, profile_dir)
        surface_manifest["layers"][layer] = layer_info

    manifest_path = output_dir / "surface_manifest.json"
    manifest_path.write_text(json.dumps(surface_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.update_artifact_manifest:
        update_artifact_manifest(profile_dir, manifest_path, atlas_shape)
    print(f"[surface] wrote {manifest_path}")
    return surface_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build high-fidelity MRI surface OBJ meshes from profile masks.")
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR))
    parser.add_argument("--annotation", default="")
    parser.add_argument("--skull-mask", default="")
    parser.add_argument("--outer-tissue-mask", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--voxel-size-um", type=float, default=25.0)
    parser.add_argument("--layers", default="brain_surface,outer_tissue,skull")
    parser.add_argument("--lod-steps", default="1,2,4", help="Marching-cubes step sizes for lod0,lod1,lod2")
    parser.add_argument("--update-artifact-manifest", action="store_true")
    args = parser.parse_args()
    build(args)


if __name__ == "__main__":
    main()
