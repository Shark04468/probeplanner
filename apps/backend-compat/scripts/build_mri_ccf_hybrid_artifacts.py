#!/usr/bin/env python
"""
Build MRI+CCF hybrid artifacts for probe-planner.

Pipeline:
1) Reuse build_qiu2018_artifacts.py to generate core artifacts:
   - annotation.npy / annotation.npz
   - stats.json
   - jacobian.npy / jacobian.npz (optional)
   - region_map.json
2) Add MRI-specific artifacts:
   - skull_mask.npy / skull_mask.npz
   - outer_tissue_mask.npy / outer_tissue_mask.npz
   - layered_sdf.npz (v2 multi-channel + legacy signed_sdf_mm)
3) Emit strict contracts:
   - region_map_v2.json
   - artifact_manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import numpy as np
try:
    import SimpleITK as sitk
except Exception:
    sitk = None

try:
    from scipy.ndimage import binary_dilation, binary_erosion, distance_transform_edt
except Exception:
    binary_dilation = None
    binary_erosion = None
    distance_transform_edt = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            blk = fh.read(chunk_size)
            if not blk:
                break
            h.update(blk)
    return h.hexdigest()


def _parse_triplet(text: str, cast=float) -> Tuple[float, float, float]:
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"Expected three comma-separated values, got: {text!r}")
    return (cast(parts[0]), cast(parts[1]), cast(parts[2]))


def _parse_shape(text: str) -> Tuple[int, int, int]:
    z, y, x = _parse_triplet(text, cast=int)
    if z <= 0 or y <= 0 or x <= 0:
        raise ValueError(f"Shape must be positive, got {(z, y, x)}")
    return (z, y, x)


def _load_np(path: Path, preferred_keys: Optional[Sequence[str]] = None) -> np.ndarray:
    if path.suffix.lower() == ".npy":
        return np.load(path, allow_pickle=False)
    if path.suffix.lower() == ".npz":
        pack = np.load(path, allow_pickle=False)
        keys = [str(k) for k in (preferred_keys or ())]
        for key in keys:
            if key in pack.files:
                return pack[key]
        if not pack.files:
            raise RuntimeError(f"NPZ has no arrays: {path}")
        return pack[pack.files[0]]
    raise RuntimeError(f"Unsupported NumPy file: {path}")


def _load_resampled_array(
    path: Path,
    expected_shape_zyx: Tuple[int, int, int],
    reference_img,
    *,
    is_label: bool,
    default_value: float = 0.0,
    preferred_keys: Optional[Sequence[str]] = None,
) -> np.ndarray:
    suffix = path.suffix.lower()
    if suffix in {".npy", ".npz"}:
        arr = np.asarray(_load_np(path, preferred_keys=preferred_keys))
        if arr.ndim != 3:
            raise RuntimeError(f"Expected 3D array from {path}, got shape {tuple(arr.shape)}")
        if tuple(arr.shape) != tuple(expected_shape_zyx):
            raise RuntimeError(
                f"Shape mismatch for {path.name}: got {tuple(arr.shape)}, expected {tuple(expected_shape_zyx)}"
            )
        return arr

    if sitk is None:
        raise RuntimeError(
            "SimpleITK is required for image files (nii/mha/...). "
            "Install with: pip install SimpleITK"
        )
    img = sitk.ReadImage(str(path))
    if reference_img is None:
        arr = np.asarray(sitk.GetArrayFromImage(img))
        if tuple(arr.shape) != tuple(expected_shape_zyx):
            raise RuntimeError(
                f"Image shape mismatch for {path.name}: got {tuple(arr.shape)}, expected {tuple(expected_shape_zyx)}; "
                f"provide --reference-image for resampling."
            )
        return arr

    interp = sitk.sitkNearestNeighbor if is_label else sitk.sitkLinear
    out = sitk.Resample(
        img,
        reference_img,
        sitk.Transform(),
        interp,
        float(default_value),
        img.GetPixelID(),
    )
    return np.asarray(sitk.GetArrayFromImage(out))


def _build_signed_sdf_mm(mask: np.ndarray, voxel_size_um: float = 25.0) -> np.ndarray:
    if distance_transform_edt is None:
        return np.zeros_like(mask, dtype=np.float32)
    risk_mask = np.asarray(mask, dtype=bool)
    voxel_mm = float(voxel_size_um) / 1000.0
    outside_mm = distance_transform_edt(~risk_mask).astype(np.float32) * voxel_mm
    inside_mm = distance_transform_edt(risk_mask).astype(np.float32) * voxel_mm
    signed_mm = outside_mm
    signed_mm[risk_mask] = -inside_mm[risk_mask]
    return signed_mm


def _surface_shell(mask: np.ndarray) -> np.ndarray:
    mask_bool = np.asarray(mask, dtype=bool)
    if not np.any(mask_bool):
        return np.zeros_like(mask_bool, dtype=bool)
    if binary_erosion is None:
        return np.asarray(mask_bool, dtype=bool)
    eroded = binary_erosion(mask_bool, iterations=1, border_value=0)
    return np.logical_and(mask_bool, np.logical_not(eroded))


def _load_region_map_simple(path: Path) -> Dict[str, int]:
    if not Path(path).exists():
        return {}
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: Dict[str, int] = {}
    if not isinstance(loaded, dict):
        return out
    for k, v in loaded.items():
        try:
            out[str(k).strip()] = int(v)
        except Exception:
            continue
    return out


def _mask_from_region_keywords(
    annotation: np.ndarray,
    region_map: Dict[str, int],
    *,
    contains: Sequence[str] = (),
    exact: Sequence[str] = (),
    chunk_depth: int = 32,
) -> np.ndarray:
    labels = set()
    contains_l = tuple(str(x).strip().lower() for x in contains if str(x).strip())
    exact_l = {str(x).strip().lower() for x in exact if str(x).strip()}
    for acronym, rid in (region_map or {}).items():
        key = str(acronym or "").strip().lower()
        if not key:
            continue
        if key in exact_l or any(h in key for h in contains_l):
            labels.add(int(rid))
    if not labels:
        return np.zeros_like(annotation, dtype=bool)
    label_arr = np.asarray(sorted(v for v in labels if v > 0), dtype=np.uint32)
    out = np.zeros(annotation.shape, dtype=bool)
    depth = int(annotation.shape[0])
    chunk_depth = max(4, int(chunk_depth))
    ann_u = np.asarray(annotation, dtype=np.uint32)
    for z0 in range(0, depth, chunk_depth):
        z1 = min(depth, z0 + chunk_depth)
        out[z0:z1] = np.isin(ann_u[z0:z1], label_arr)
    return out


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _save_bool_mask(path_base: Path, mask: np.ndarray) -> None:
    out = np.asarray(mask, dtype=np.uint8)
    np.save(path_base.with_suffix(".npy"), out)
    np.savez_compressed(path_base.with_suffix(".npz"), mask=out)


def _choose_canonical_acronym(keys: Sequence[str]) -> str:
    clean = [str(k).strip() for k in keys if str(k).strip()]
    if not clean:
        return ""

    def _score(k: str):
        lower_count = sum(ch.islower() for ch in k)
        upper_count = sum(ch.isupper() for ch in k)
        # Prefer compact canonical acronyms (typically uppercase Allen style),
        # then lexical tie-break for determinism.
        return (lower_count, -upper_count, len(k), k.lower(), k)

    return sorted(clean, key=_score)[0]


def _build_region_map_v2_payload(region_map: Dict[str, int]) -> Dict[str, object]:
    grouped: Dict[str, Dict[str, object]] = {}
    conflicts = []
    for k, v in (region_map or {}).items():
        alias = str(k).strip()
        if not alias:
            continue
        try:
            rid = int(v)
        except Exception:
            continue
        lk = alias.lower()
        slot = grouped.setdefault(
            lk,
            {
                "ids": set(),
                "aliases": set(),
            },
        )
        slot["ids"].add(rid)
        slot["aliases"].add(alias)
    entries = []
    for _, slot in sorted(grouped.items(), key=lambda kv: kv[0]):
        ids = sorted(int(x) for x in slot["ids"])
        aliases = sorted(str(x) for x in slot["aliases"])
        canonical = _choose_canonical_acronym(aliases)
        rid = int(ids[0]) if ids else None
        if len(ids) > 1:
            conflicts.append(
                {
                    "aliases": aliases,
                    "ids": ids,
                    "resolution": f"use id={rid} for canonical={canonical}",
                }
            )
        if not canonical or rid is None:
            continue
        entries.append(
            {
                "canonicalAcronym": canonical,
                "aliases": aliases,
                "id": rid,
                "systemKey": "",
                "displayNameCn": canonical,
                "displayNameEn": canonical,
            }
        )
    return {
        "schemaVersion": "region_map_v2",
        "createdAt": _utc_now_iso(),
        "entryCount": int(len(entries)),
        "entries": entries,
        "conflicts": conflicts,
    }


def _path_meta(path: Path, kind: str, required: bool, shape: Optional[Tuple[int, int, int]] = None) -> Dict[str, object]:
    path = Path(path).expanduser().resolve()
    exists = bool(path.exists())
    payload = {
        "kind": str(kind),
        "required": bool(required),
        "path": str(path),
        "exists": exists,
        "shape": list(shape) if shape is not None else None,
        "sha256": _sha256_file(path) if exists else "",
        "version": "v1",
    }
    return payload


def _write_manifest(
    out_dir: Path,
    *,
    annotation_path: Path,
    region_map_path: Path,
    stats_path: Path,
    registration_summary_path: Optional[Path],
    jacobian_path: Optional[Path],
    skull_mask_path: Path,
    outer_tissue_mask_path: Path,
    layered_sdf_path: Path,
    annotation_shape: Tuple[int, int, int],
    dataset_profile: str,
) -> Path:
    reg_summary_resolved = (
        registration_summary_path.expanduser().resolve()
        if isinstance(registration_summary_path, Path)
        else (out_dir / "registration_summary.json").resolve()
    )
    jacobian_resolved = (
        jacobian_path.expanduser().resolve() if isinstance(jacobian_path, Path) else (out_dir / "jacobian.npy").resolve()
    )
    region_map_v2_path = region_map_path.with_name("region_map_v2.json")
    manifest = {
        "schemaVersion": "probeplanner.mri_ccf.artifact_manifest.v1",
        "generatedAt": _utc_now_iso(),
        "profileId": str(dataset_profile or "custom"),
        "atlasShape": [int(x) for x in annotation_shape],
        "voxelSizeUm": 25,
        "artifacts": {
            "annotation": _path_meta(annotation_path, "volume", True, annotation_shape),
            "region_map": _path_meta(region_map_path, "json", True, None),
            "region_map_v2": _path_meta(region_map_v2_path, "json", False, None),
            "stats": _path_meta(stats_path, "json", True, None),
            "registration_summary": _path_meta(reg_summary_resolved, "json", True, None),
            "warped_mri": _path_meta(reg_summary_resolved.parent / "ambmc_in_ccf.nii.gz", "image", False, annotation_shape),
            "skull_mask": _path_meta(skull_mask_path, "mask", True, annotation_shape),
            "outer_tissue_mask": _path_meta(outer_tissue_mask_path, "mask", True, annotation_shape),
            "layered_sdf": _path_meta(layered_sdf_path, "sdf", True, annotation_shape),
            "jacobian": _path_meta(jacobian_resolved, "volume", False, annotation_shape),
        },
    }
    manifest["artifacts"]["layered_sdf"]["version"] = "v2"
    manifest["artifacts"]["layered_sdf"]["channels"] = [
        "signed_sdf_mm",
        "skull_sdf_mm",
        "outer_sdf_mm",
        "outer_tissue_sdf_mm",
        "ventricle_sdf_mm",
        "vessel_sdf_mm",
        "sulcus_sdf_mm",
        "cortex_outside_sdf_mm",
        "brain_surface_sdf_mm",
        "drilling_surface_sdf_mm",
    ]
    manifest_path = out_dir / "artifact_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def _load_region_map_csv(path: Path) -> Dict[str, int]:
    import csv

    out: Dict[str, int] = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = {c.lower().strip(): c for c in (reader.fieldnames or [])}
        key_col = cols.get("acronym") or cols.get("key") or cols.get("name")
        id_col = cols.get("id") or cols.get("label") or cols.get("region_id")
        if not key_col or not id_col:
            raise RuntimeError(
                f"Region CSV must contain acronym/key/name and id/label/region_id columns: {path}"
            )
        for row in reader:
            key = str(row.get(key_col, "")).strip()
            if not key:
                continue
            try:
                out[key] = int(row.get(id_col))
            except Exception:
                continue
    return out


def _compute_jacobian_metrics(jacobian_zyx: np.ndarray) -> Dict[str, float]:
    out: Dict[str, float] = {}
    jac = np.asarray(jacobian_zyx, dtype=np.float32)
    if jac.size == 0:
        return out
    finite = np.isfinite(jac)
    if not np.any(finite):
        return out
    vals = jac[finite]
    bad = np.logical_or(vals <= 0.0, np.logical_or(vals < 0.2, vals > 5.0))
    out["jacobianAnomalyRatio"] = float(np.mean(bad))
    out["jacobianStd"] = float(np.std(vals))
    return out


def _build_core_locally(args, out_dir: Path, expected_shape: Tuple[int, int, int]) -> None:
    label_path = Path(args.label_image).expanduser().resolve()
    if label_path.suffix.lower() not in {".npy", ".npz"}:
        raise RuntimeError(
            "Local fallback builder supports only .npy/.npz label inputs. "
            "For image formats install SimpleITK and rerun."
        )
    if args.reference_image:
        raise RuntimeError("Local fallback builder does not support --reference-image. Install SimpleITK.")

    annotation = np.asarray(_load_np(label_path, preferred_keys=("annotation", "label", "labels")), dtype=np.uint32)
    if annotation.ndim != 3:
        raise RuntimeError(f"Label array must be 3D, got shape {tuple(annotation.shape)}")
    if tuple(annotation.shape) != tuple(expected_shape) and not args.allow_shape_mismatch:
        raise RuntimeError(
            f"Annotation shape mismatch: got {tuple(annotation.shape)}, expected {tuple(expected_shape)}"
        )

    np.save(out_dir / "annotation.npy", annotation)
    np.savez_compressed(out_dir / "annotation.npz", annotation=annotation)

    metrics = {}
    if args.jacobian_image:
        jac = np.asarray(
            _load_np(Path(args.jacobian_image).expanduser().resolve(), preferred_keys=("jacobian", "jacobian_det")),
            dtype=np.float32,
        )
        if jac.ndim != 3:
            raise RuntimeError(f"Jacobian array must be 3D, got shape {tuple(jac.shape)}")
        if tuple(jac.shape) != tuple(annotation.shape):
            raise RuntimeError(f"Jacobian shape mismatch: {tuple(jac.shape)} != {tuple(annotation.shape)}")
        np.save(out_dir / "jacobian.npy", jac)
        np.savez_compressed(out_dir / "jacobian.npz", jacobian=jac)
        metrics.update(_compute_jacobian_metrics(jac))

    region_map = {}
    if args.region_map_json:
        with open(Path(args.region_map_json).expanduser().resolve(), "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            for k, v in payload.items():
                try:
                    region_map[str(k).strip()] = int(v)
                except Exception:
                    continue
    if args.region_map_csv:
        region_map.update(_load_region_map_csv(Path(args.region_map_csv).expanduser().resolve()))
    if not region_map and args.emit_rid_region_map:
        region_map = {f"RID_{int(x)}": int(x) for x in np.unique(annotation).tolist() if int(x) > 0}
    if region_map:
        with (out_dir / "region_map.json").open("w", encoding="utf-8") as f:
            json.dump(region_map, f, ensure_ascii=False, indent=2)

    stats_payload = {
        "createdAt": _utc_now_iso(),
        "source": "mri_ccf_hybrid_artifact_builder_local",
        "annotationPath": str(out_dir / "annotation.npy"),
        "annotationShape": list(annotation.shape),
        "annotationDtype": str(annotation.dtype),
        "nonzeroVoxelCount": int(np.count_nonzero(annotation)),
        "nonzeroLabelCount": int(sum(int(x) > 0 for x in np.unique(annotation).tolist())),
    }
    for key, val in metrics.items():
        stats_payload[key] = round(float(val), 6)
    with (out_dir / "stats.json").open("w", encoding="utf-8") as f:
        json.dump(stats_payload, f, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build MRI+CCF hybrid artifacts for probe-planner.")
    ap.add_argument("--label-image", required=True, help="Input MRI-aligned label image/array")
    ap.add_argument("--output-dir", required=True, help="Output artifact directory")
    ap.add_argument("--reference-image", default="", help="Optional reference image used for resampling")
    ap.add_argument("--expected-shape", default="528,320,456", help="Expected array shape z,y,x")
    ap.add_argument("--target-spacing-um", default="25,25,25", help="Target spacing x,y,z in um")
    ap.add_argument("--allow-shape-mismatch", action="store_true")

    ap.add_argument("--jacobian-image", default="", help="Optional jacobian determinant image")
    ap.add_argument("--warp-field", default="", help="Optional displacement warp field for jacobian")
    ap.add_argument("--gt-label-image", default="", help="Optional GT label for Dice/HD95")
    ap.add_argument("--fixed-landmarks-csv", default="", help="Optional landmarks CSV for TRE")
    ap.add_argument("--warped-landmarks-csv", default="", help="Optional warped landmarks CSV for TRE")
    ap.add_argument("--region-map-json", default="", help="Optional acronym->id JSON")
    ap.add_argument("--region-map-csv", default="", help="Optional acronym,id CSV")
    ap.add_argument("--emit-rid-region-map", action="store_true")

    ap.add_argument("--dataset-profile", default="custom", help="Dataset profile tag (e.g. dsurqe, waxholm, custom)")
    ap.add_argument("--skull-mask-image", default="", help="Optional skull mask image/array")
    ap.add_argument("--outer-tissue-mask-image", default="", help="Optional cortex-outside/outer tissue mask")
    ap.add_argument("--layered-sdf-image", default="", help="Optional precomputed signed SDF image/array")
    ap.add_argument("--ventricle-mask-image", default="", help="Optional ventricle mask image/array")
    ap.add_argument("--vessel-mask-image", default="", help="Optional vessel mask image/array")
    ap.add_argument("--sulcus-mask-image", default="", help="Optional sulcus mask image/array")
    ap.add_argument("--registration-summary", default="", help="Optional registration_summary.json")
    ap.add_argument("--voxel-size-um", type=float, default=25.0, help="Voxel size used by fallback SDF builder")
    args = ap.parse_args()

    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    expected_shape = _parse_shape(args.expected_shape)

    # Step 1: delegate core artifact building to existing Qiu script.
    qiu_builder = Path(__file__).resolve().parent / "build_qiu2018_artifacts.py"
    can_run_qiu_builder = qiu_builder.exists() and sitk is not None

    if can_run_qiu_builder:
        cmd = [
            sys.executable,
            str(qiu_builder),
            "--label-image",
            str(Path(args.label_image).expanduser().resolve()),
            "--output-dir",
            str(out_dir),
            "--expected-shape",
            args.expected_shape,
            "--target-spacing-um",
            args.target_spacing_um,
        ]
        if args.reference_image:
            cmd.extend(["--reference-image", str(Path(args.reference_image).expanduser().resolve())])
        if args.allow_shape_mismatch:
            cmd.append("--allow-shape-mismatch")
        if args.jacobian_image:
            cmd.extend(["--jacobian-image", str(Path(args.jacobian_image).expanduser().resolve())])
        if args.warp_field:
            cmd.extend(["--warp-field", str(Path(args.warp_field).expanduser().resolve())])
        if args.gt_label_image:
            cmd.extend(["--gt-label-image", str(Path(args.gt_label_image).expanduser().resolve())])
        if args.fixed_landmarks_csv:
            cmd.extend(["--fixed-landmarks-csv", str(Path(args.fixed_landmarks_csv).expanduser().resolve())])
        if args.warped_landmarks_csv:
            cmd.extend(["--warped-landmarks-csv", str(Path(args.warped_landmarks_csv).expanduser().resolve())])
        if args.region_map_json:
            cmd.extend(["--region-map-json", str(Path(args.region_map_json).expanduser().resolve())])
        if args.region_map_csv:
            cmd.extend(["--region-map-csv", str(Path(args.region_map_csv).expanduser().resolve())])
        if args.emit_rid_region_map:
            cmd.append("--emit-rid-region-map")
        subprocess.run(cmd, check=True)
    else:
        _build_core_locally(args, out_dir, expected_shape)

    annotation_path = out_dir / "annotation.npy"
    if not annotation_path.exists():
        raise RuntimeError(f"annotation.npy not produced in output dir: {out_dir}")
    annotation = np.asarray(np.load(annotation_path, allow_pickle=False), dtype=np.uint32)
    if tuple(annotation.shape) != tuple(expected_shape) and not args.allow_shape_mismatch:
        raise RuntimeError(
            f"Annotation shape mismatch: got {tuple(annotation.shape)}, expected {tuple(expected_shape)}"
        )

    reference_img = None
    if args.reference_image:
        reference_img = sitk.ReadImage(str(Path(args.reference_image).expanduser().resolve()))

    brain_mask = annotation > 0
    region_map_payload_early = _load_region_map_simple(out_dir / "region_map.json")
    warnings = []

    # Step 2: MRI-specific masks.
    if args.skull_mask_image:
        skull_mask = _load_resampled_array(
            Path(args.skull_mask_image).expanduser().resolve(),
            expected_shape,
            reference_img,
            is_label=True,
            default_value=0.0,
            preferred_keys=("mask", "skull_mask", "skull"),
        ) > 0
    else:
        if binary_dilation is not None:
            shell = np.logical_and(binary_dilation(brain_mask, iterations=2), np.logical_not(brain_mask))
            skull_mask = np.asarray(shell, dtype=bool)
            warnings.append("skull_mask estimated from brain boundary shell (no --skull-mask-image provided).")
        else:
            skull_mask = np.zeros_like(brain_mask, dtype=bool)
            warnings.append("scipy unavailable: skull_mask fallback is empty.")

    if args.outer_tissue_mask_image:
        outer_mask = _load_resampled_array(
            Path(args.outer_tissue_mask_image).expanduser().resolve(),
            expected_shape,
            reference_img,
            is_label=True,
            default_value=0.0,
            preferred_keys=("mask", "outer_tissue_mask", "outer_tissue", "cortex_outside"),
        ) > 0
    else:
        outer_mask = np.logical_not(brain_mask)
        warnings.append("outer_tissue_mask estimated as complement of brain mask (no --outer-tissue-mask-image).")

    skull_base = out_dir / "skull_mask"
    outer_base = out_dir / "outer_tissue_mask"
    _save_bool_mask(skull_base, skull_mask)
    _save_bool_mask(outer_base, outer_mask)
    brain_surface_mask = _surface_shell(brain_mask)
    drilling_surface_mask = _surface_shell(skull_mask) if np.any(skull_mask) else brain_surface_mask

    # Optional structure masks for layered SDF v2.
    ventricle_mask = None
    if args.ventricle_mask_image:
        ventricle_mask = _load_resampled_array(
            Path(args.ventricle_mask_image).expanduser().resolve(),
            expected_shape,
            reference_img,
            is_label=True,
            default_value=0.0,
            preferred_keys=("mask", "ventricle_mask", "ventricle"),
        ) > 0

    vessel_mask = None
    if args.vessel_mask_image:
        vessel_mask = _load_resampled_array(
            Path(args.vessel_mask_image).expanduser().resolve(),
            expected_shape,
            reference_img,
            is_label=True,
            default_value=0.0,
            preferred_keys=("mask", "vessel_mask", "vessel", "vasculature"),
        ) > 0

    sulcus_mask = None
    if args.sulcus_mask_image:
        sulcus_mask = _load_resampled_array(
            Path(args.sulcus_mask_image).expanduser().resolve(),
            expected_shape,
            reference_img,
            is_label=True,
            default_value=0.0,
            preferred_keys=("mask", "sulcus_mask", "sulcus", "fissure"),
        ) > 0
    elif region_map_payload_early:
        sulcus_mask = _mask_from_region_keywords(
            annotation,
            region_map_payload_early,
            contains=("sulcus", "fissure"),
            exact=("grv", "hf", "rf", "pfs", "plf", "icf", "sec"),
        )
        if int(np.count_nonzero(sulcus_mask)) > 0:
            warnings.append("sulcus_mask estimated from region_map acronym/name proxy.")

    if vessel_mask is None and region_map_payload_early:
        vessel_mask = _mask_from_region_keywords(
            annotation,
            region_map_payload_early,
            contains=("vessel", "artery", "vein", "vasc"),
            exact=(),
        )
        if int(np.count_nonzero(vessel_mask)) > 0:
            warnings.append("vessel_mask estimated from region_map acronym/name proxy.")
        else:
            warnings.append("vessel_mask proxy is empty; provide --vessel-mask-image for true vascular risk.")

    # Step 3: layered SDF (signed, mm, multi-channel v2 + legacy key).
    layered_sdf_path = out_dir / "layered_sdf.npz"
    layered_channels = {}
    if args.layered_sdf_image:
        layered_src = Path(args.layered_sdf_image).expanduser().resolve()
        if layered_src.suffix.lower() == ".npz":
            pack = np.load(layered_src, allow_pickle=False)
            for key in (
                "signed_sdf_mm",
                "skull_sdf_mm",
                "outer_sdf_mm",
                "outer_tissue_sdf_mm",
                "ventricle_sdf_mm",
                "vessel_sdf_mm",
                "sulcus_sdf_mm",
                "cortex_outside_sdf_mm",
                "brain_surface_sdf_mm",
                "drilling_surface_sdf_mm",
            ):
                if key in pack.files:
                    arr = np.asarray(pack[key], dtype=np.float32)
                    if tuple(arr.shape) == tuple(expected_shape):
                        layered_channels[key] = arr
            if not layered_channels and pack.files:
                arr = np.asarray(pack[pack.files[0]], dtype=np.float32)
                if tuple(arr.shape) != tuple(expected_shape):
                    raise RuntimeError(
                        f"layered_sdf array shape mismatch: got {tuple(arr.shape)} expected {tuple(expected_shape)}"
                    )
                layered_channels["signed_sdf_mm"] = arr
        else:
            sdf_arr = _load_resampled_array(
                layered_src,
                expected_shape,
                reference_img,
                is_label=False,
                default_value=0.0,
                preferred_keys=("signed_sdf_mm", "sdf", "layered_sdf", "risk_sdf"),
            )
            layered_channels["signed_sdf_mm"] = np.asarray(sdf_arr, dtype=np.float32)
    else:
        risk_seed = np.logical_or(skull_mask, outer_mask)
        layered_channels["signed_sdf_mm"] = _build_signed_sdf_mm(risk_seed, voxel_size_um=float(args.voxel_size_um))
        warnings.append("layered_sdf estimated from skull/outer tissue masks.")

    def _ensure_channel(name: str, mask: np.ndarray):
        if name in layered_channels:
            return
        layered_channels[name] = _build_signed_sdf_mm(mask, voxel_size_um=float(args.voxel_size_um))

    _ensure_channel("skull_sdf_mm", np.asarray(skull_mask, dtype=bool))
    _ensure_channel("outer_sdf_mm", np.asarray(outer_mask, dtype=bool))
    _ensure_channel("outer_tissue_sdf_mm", np.asarray(outer_mask, dtype=bool))
    _ensure_channel("cortex_outside_sdf_mm", np.asarray(outer_mask, dtype=bool))
    _ensure_channel("ventricle_sdf_mm", np.asarray(ventricle_mask, dtype=bool) if ventricle_mask is not None else np.zeros_like(brain_mask, dtype=bool))
    _ensure_channel("vessel_sdf_mm", np.asarray(vessel_mask, dtype=bool) if vessel_mask is not None else np.zeros_like(brain_mask, dtype=bool))
    _ensure_channel("sulcus_sdf_mm", np.asarray(sulcus_mask, dtype=bool) if sulcus_mask is not None else np.zeros_like(brain_mask, dtype=bool))
    _ensure_channel("brain_surface_sdf_mm", np.asarray(brain_surface_mask, dtype=bool))
    _ensure_channel("drilling_surface_sdf_mm", np.asarray(drilling_surface_mask, dtype=bool))

    if "signed_sdf_mm" not in layered_channels:
        stacked = np.stack(
            [
                np.asarray(layered_channels.get("skull_sdf_mm"), dtype=np.float32),
                np.asarray(layered_channels.get("outer_sdf_mm"), dtype=np.float32),
                np.asarray(layered_channels.get("ventricle_sdf_mm"), dtype=np.float32),
                np.asarray(layered_channels.get("vessel_sdf_mm"), dtype=np.float32),
                np.asarray(layered_channels.get("sulcus_sdf_mm"), dtype=np.float32),
                np.asarray(layered_channels.get("brain_surface_sdf_mm"), dtype=np.float32),
                np.asarray(layered_channels.get("drilling_surface_sdf_mm"), dtype=np.float32),
            ],
            axis=0,
        )
        layered_channels["signed_sdf_mm"] = np.min(stacked, axis=0).astype(np.float32)
    signed_sdf_mm = np.asarray(layered_channels["signed_sdf_mm"], dtype=np.float32)

    np.savez_compressed(
        layered_sdf_path,
        signed_sdf_mm=signed_sdf_mm,
        skull_sdf_mm=np.asarray(layered_channels["skull_sdf_mm"], dtype=np.float32),
        outer_sdf_mm=np.asarray(layered_channels["outer_sdf_mm"], dtype=np.float32),
        outer_tissue_sdf_mm=np.asarray(layered_channels["outer_tissue_sdf_mm"], dtype=np.float32),
        ventricle_sdf_mm=np.asarray(layered_channels["ventricle_sdf_mm"], dtype=np.float32),
        vessel_sdf_mm=np.asarray(layered_channels["vessel_sdf_mm"], dtype=np.float32),
        sulcus_sdf_mm=np.asarray(layered_channels["sulcus_sdf_mm"], dtype=np.float32),
        cortex_outside_sdf_mm=np.asarray(layered_channels["cortex_outside_sdf_mm"], dtype=np.float32),
        brain_surface_sdf_mm=np.asarray(layered_channels["brain_surface_sdf_mm"], dtype=np.float32),
        drilling_surface_sdf_mm=np.asarray(layered_channels["drilling_surface_sdf_mm"], dtype=np.float32),
    )

    # Step 4: enrich stats payload.
    stats_path = out_dir / "stats.json"
    stats_payload = {}
    if stats_path.exists():
        try:
            with stats_path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
                if isinstance(loaded, dict):
                    stats_payload = loaded
        except Exception:
            stats_payload = {}

    stats_payload["source"] = "mri_ccf_hybrid_artifact_builder"
    stats_payload["updatedAt"] = _utc_now_iso()
    stats_payload["datasetProfile"] = str(args.dataset_profile or "custom").strip() or "custom"
    stats_payload["annotationPath"] = str(annotation_path)
    stats_payload["skullMaskPath"] = str(skull_base.with_suffix(".npy"))
    stats_payload["outerTissueMaskPath"] = str(outer_base.with_suffix(".npy"))
    stats_payload["layeredSdfPath"] = str(layered_sdf_path)
    stats_payload["skullMaskVoxelCount"] = int(np.count_nonzero(skull_mask))
    stats_payload["outerTissueVoxelCount"] = int(np.count_nonzero(outer_mask))
    stats_payload["brainSurfaceVoxelCount"] = int(np.count_nonzero(brain_surface_mask))
    stats_payload["drillingSurfaceVoxelCount"] = int(np.count_nonzero(drilling_surface_mask))
    stats_payload["vesselMaskVoxelCount"] = int(np.count_nonzero(vessel_mask)) if vessel_mask is not None else 0
    stats_payload["sulcusMaskVoxelCount"] = int(np.count_nonzero(sulcus_mask)) if sulcus_mask is not None else 0
    stats_payload["layeredSdfShape"] = list(np.asarray(signed_sdf_mm).shape)
    stats_payload.setdefault("metrics", {})
    if isinstance(stats_payload.get("metrics"), dict):
        metrics = stats_payload["metrics"]
        for key in ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio"):
            if key in stats_payload and metrics.get(key) is None:
                metrics[key] = stats_payload.get(key)
        stats_payload["metrics"] = metrics
    if args.registration_summary:
        reg_path = Path(args.registration_summary).expanduser().resolve()
    else:
        reg_path = out_dir / "registration_summary.json"
    if reg_path.exists():
        stats_payload["registrationSummaryPath"] = str(reg_path)
        try:
            reg_payload = json.loads(reg_path.read_text(encoding="utf-8"))
        except Exception:
            reg_payload = {}
        if isinstance(reg_payload, dict):
            reg_metrics = reg_payload.get("metrics")
            if isinstance(reg_metrics, dict):
                stats_payload.setdefault("metrics", {})
                for k in ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio"):
                    if stats_payload["metrics"].get(k) is None and reg_metrics.get(k) is not None:
                        stats_payload["metrics"][k] = reg_metrics.get(k)

    with stats_path.open("w", encoding="utf-8") as fh:
        json.dump(stats_payload, fh, ensure_ascii=False, indent=2)

    # Step 5: region_map_v2 + artifact_manifest.
    region_map_path = out_dir / "region_map.json"
    region_map_v2_path = out_dir / "region_map_v2.json"
    region_map_payload = {}
    if region_map_path.exists():
        try:
            loaded_map = json.loads(region_map_path.read_text(encoding="utf-8"))
            if isinstance(loaded_map, dict):
                for k, v in loaded_map.items():
                    try:
                        region_map_payload[str(k).strip()] = int(v)
                    except Exception:
                        continue
        except Exception:
            region_map_payload = {}
    region_map_v2_payload = _build_region_map_v2_payload(region_map_payload)
    region_map_v2_path.write_text(json.dumps(region_map_v2_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_path = _write_manifest(
        out_dir,
        annotation_path=annotation_path,
        region_map_path=region_map_path,
        stats_path=stats_path,
        registration_summary_path=(Path(args.registration_summary).expanduser().resolve() if args.registration_summary else None),
        jacobian_path=((out_dir / "jacobian.npy") if (out_dir / "jacobian.npy").exists() else None),
        skull_mask_path=skull_base.with_suffix(".npy"),
        outer_tissue_mask_path=outer_base.with_suffix(".npy"),
        layered_sdf_path=layered_sdf_path,
        annotation_shape=tuple(annotation.shape),
        dataset_profile=stats_payload["datasetProfile"],
    )

    result = {
        "ok": True,
        "outputDir": str(out_dir),
        "annotationPath": str(annotation_path),
        "regionMapPath": str(out_dir / "region_map.json") if (out_dir / "region_map.json").exists() else "",
        "statsPath": str(stats_path),
        "jacobianPath": str(out_dir / "jacobian.npy") if (out_dir / "jacobian.npy").exists() else "",
        "skullMaskPath": str(skull_base.with_suffix(".npy")),
        "outerTissueMaskPath": str(outer_base.with_suffix(".npy")),
        "layeredSdfPath": str(layered_sdf_path),
        "regionMapV2Path": str(region_map_v2_path),
        "manifestPath": str(manifest_path),
        "annotationShape": list(annotation.shape),
        "datasetProfile": stats_payload["datasetProfile"],
        "warnings": warnings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
