"""
BCI Probe Trajectory Optimizer  ?Flask Backend
================================================
Provides precise voxel-level brain region coverage using Allen CCFv3 annotation data.

Installation (one-time):
    pip install -r apps/backend-compat/requirements.txt
    # Optional legacy AllenSDK mode: pip install -r apps/backend-compat/requirements-legacy.txt

First run downloads annotation_25.nrrd (~110 MB) automatically.

Usage:
    python optimizer_server.py
    # Server starts at http://localhost:5000

API Endpoints:
    GET  /api/status               ?health check + atlas info
    POST /api/optimize             ?run optimization with voxel coverage
    POST /api/coverage             ?compute voxel coverage for given trajectories
    GET  /api/regions              ?list all supported brain regions with voxel counts
    GET  /api/allen_mesh/<id>      ?[NEW] Allen CCFv3 structure mesh proxy (OBJ)
c    POST /api/register_landmarks   ?coordinate correction from landmarks

CHANGELOG:
    [FIX-1] sample_trajectory_voxels: AP axis direction corrected.
    [FIX-2] load_atlas: removed duplicate stub.
    [FIX-3] B&B reduction: diversity-aware candidate selection (Jaccard).
    [NEW-1] Allen mesh proxy: GET /api/allen_mesh/<structure_id>
                      ?api.brain-map.org  ?CORS     ?                   +                 ?"""

import os
import sys
import json
import math
import re
import time
import platform
import subprocess
import queue
import uuid
import hashlib
import logging
import traceback
import threading
import importlib
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from importlib import metadata as importlib_metadata
from typing import Any

import numpy as np
import requests as http_requests          # [NEW-1] Allen mesh proxy
from flask import Flask, request, jsonify, Response, g, send_file
from flask_cors import CORS
from werkzeug.exceptions import BadRequest, HTTPException
from backend.api import (
    TaskRegistry,
    structured_log,
    normalize_task_status,
    TASK_STATE_QUEUED,
    TASK_STATE_RUNNING,
    TASK_STATE_PARTIAL,
    TASK_STATE_FAILED,
    TASK_STATE_COMPLETED,
)
from backend.candidate import summarize_candidate_pool
from backend.token_cover import bounded_token_cover
from backend.planning_policy import run_density_policy
from backend.registration_evidence import assess_registration_metrics
from backend.atlas_sections import AtlasSectionSource, SectionError, AXES as SECTION_AXES
from backend.contract import validate_optimize_schema, sanitize_risk_thresholds
from backend.risk_model import (
    classify_risk_source,
    resolve_risk_threshold_mm,
    build_top_danger_segments,
)
from backend.security import (
    is_loopback_addr,
    path_is_under,
    resolve_allowed_path_roots,
    truthy_env,
)

cKDTree: Any = None
binary_erosion: Any = None
distance_transform_edt: Any = None
try:
    _scipy_spatial = importlib.import_module("scipy.spatial")
    _scipy_ndimage = importlib.import_module("scipy.ndimage")
    cKDTree = getattr(_scipy_spatial, "cKDTree")
    binary_erosion = getattr(_scipy_ndimage, "binary_erosion")
    distance_transform_edt = getattr(_scipy_ndimage, "distance_transform_edt")
except Exception:  # pragma: no cover - optional runtime acceleration
    pass

_cp: Any = None
_cupy_ndimage: Any = None
try:  # pragma: no cover - optional GPU acceleration
    _cp = importlib.import_module("cupy")
    _cupy_ndimage = importlib.import_module("cupyx.scipy.ndimage")
except Exception:
    pass

cp: Any = None
try:
    cp = importlib.import_module("cvxpy")
except Exception:  # pragma: no cover - optional advanced DRCC solver
    pass

#        Logging                                                                                                                                       
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("optimizer")

def _resolve_cors_origins():
    raw = str(os.getenv("PROBEPLANNER_CORS_ORIGINS", "")).strip()
    if raw:
        items = [part.strip() for part in raw.split(",") if part.strip()]
        if items:
            return items
    # Local dev defaults:
    # - localhost/127.0.0.1 any port (React/Vite/WebGL dev servers)
    # - "null" origin (file:// or embedded WebView contexts)
    return [r"http://localhost(:\d+)?", r"http://127\.0\.0\.1(:\d+)?", "null"]


app = Flask(__name__, static_folder=None)
CORS(app, resources={r"/api/*": {"origins": _resolve_cors_origins()}},
     expose_headers=["X-Lod", "X-Lod-Requested", "X-Lod-Fallback", "X-Cache-Source", "X-Structure-Id"])

#        Atlas constants                                                                                                                         
VOXEL_SIZE_UM = 25
ATLAS_SHAPE   = (528, 320, 456)
BREGMA_UM     = np.array([5400.0, 440.0, 5700.0])
ATLAS_BL_DIST_MM = 4.0
ATLAS_ML_DIST_MM = 2.0

# Anatomical safety limits (aligned with frontend constants/anatomy.js)
LAMBDA_AP_MM = -4.0
ENTRY_AP_MIN_MM = -4.5
ENTRY_AP_MAX_MM = 4.5
TIP_POST_LIMIT_MM = -6.0
MAX_DV_MM = 8.5
MIN_RISK_DISTANCE_MM_DEFAULT = 0.35
RISK_DISTANCE_SIGMA_MM_DEFAULT = 0.08
DRCC_RISK_EPSILON_DEFAULT = 0.08
DRCC_WASSERSTEIN_RADIUS_DEFAULT = 0.03
DRCC_EVAL_TOPK_DEFAULT = 240
DRCC_CVAR_ALPHA_DEFAULT = 0.9
DRCC_SOLVER_DEFAULT = "exact_pool"
DRCC_EXACT_MAX_CANDIDATES_DEFAULT = 42
DRCC_EXACT_TIME_LIMIT_S_DEFAULT = 18.0
DRCC_MISOCP_MAX_CANDIDATES_DEFAULT = 56
DRCC_MISOCP_TIME_LIMIT_S_DEFAULT = 45.0
DRCC_MISOCP_SOC_WEIGHT_DEFAULT = 0.15
SDF_RISK_INTEGRAL_WEIGHT_DEFAULT = 1.0
SDF_RISK_CVAR_WEIGHT_DEFAULT = 0.7
SELECTIVE_CONFORMAL_ALPHA_DEFAULT = 0.1
SELECTIVE_REVIEW_PROB_THRESHOLD_DEFAULT = 0.8
SELECTIVE_REJECT_PROB_THRESHOLD_DEFAULT = 0.35
SELECTIVE_REVIEW_MAX_PROB_CI_WIDTH_DEFAULT = 0.5

ENGINE_VERSION = os.getenv("PROBEPLANNER_ENGINE_VERSION", "ccfv3-single-source-2026.03")
ENGINE_MODE = "backend_single_source"
ATLAS_MODES = ("allen_sdk", "qiu2018", "mri_ccf_hybrid")
QIU2018_ANNOTATION_PATH = str(os.getenv("PROBEPLANNER_QIU2018_ANNOTATION_PATH", "")).strip()
QIU2018_REGION_MAP_PATH = str(os.getenv("PROBEPLANNER_QIU2018_REGION_MAP_PATH", "")).strip()
QIU2018_STATS_PATH = str(os.getenv("PROBEPLANNER_QIU2018_STATS_PATH", "")).strip()
QIU2018_JACOBIAN_PATH = str(os.getenv("PROBEPLANNER_QIU2018_JACOBIAN_PATH", "")).strip()
QIU2018_MEMMAP_ENABLED = str(
    os.getenv("PROBEPLANNER_QIU2018_MEMMAP", "true")
).strip().lower() in {"1", "true", "yes", "on"}
MRI_CCF_ANNOTATION_PATH = str(os.getenv("PROBEPLANNER_MRI_CCF_ANNOTATION_PATH", "")).strip()
MRI_CCF_REGION_MAP_PATH = str(os.getenv("PROBEPLANNER_MRI_CCF_REGION_MAP_PATH", "")).strip()
MRI_CCF_STATS_PATH = str(os.getenv("PROBEPLANNER_MRI_CCF_STATS_PATH", "")).strip()
MRI_CCF_JACOBIAN_PATH = str(os.getenv("PROBEPLANNER_MRI_CCF_JACOBIAN_PATH", "")).strip()
MRI_CCF_SKULL_MASK_PATH = str(os.getenv("PROBEPLANNER_MRI_CCF_SKULL_MASK_PATH", "")).strip()
MRI_CCF_OUTER_TISSUE_MASK_PATH = str(os.getenv("PROBEPLANNER_MRI_CCF_OUTER_TISSUE_MASK_PATH", "")).strip()
MRI_CCF_LAYERED_SDF_PATH = str(os.getenv("PROBEPLANNER_MRI_CCF_LAYERED_SDF_PATH", "")).strip()
MRI_CCF_SURFACE_MESH_MANIFEST_PATH = str(
    os.getenv("PROBEPLANNER_MRI_CCF_SURFACE_MESH_MANIFEST_PATH", "")
).strip()
MRI_CCF_LAYERED_MASK_THRESHOLD_MM = float(
    os.getenv("PROBEPLANNER_MRI_CCF_LAYERED_MASK_THRESHOLD_MM", "0.0")
)
_MRI_CCF_DEFAULT_PROFILE_RAW = str(os.getenv("PROBEPLANNER_MRI_CCF_DEFAULT_PROFILE_DIR", "")).strip()
if not _MRI_CCF_DEFAULT_PROFILE_RAW:
    _MRI_ARTIFACT_ROOT = str(os.getenv("PROBEPLANNER_ARTIFACT_ROOT", "")).strip()
    _MRI_PROFILE_NAME = str(
        os.getenv("PROBEPLANNER_MRI_CCF_PROFILE_NAME", "ambmc_true_outer_full")
    ).strip()
    if _MRI_ARTIFACT_ROOT and _MRI_PROFILE_NAME:
        _MRI_CCF_DEFAULT_PROFILE_RAW = str(Path(_MRI_ARTIFACT_ROOT) / _MRI_PROFILE_NAME)
MRI_CCF_DEFAULT_PROFILE_DIR = Path(_MRI_CCF_DEFAULT_PROFILE_RAW).expanduser() if _MRI_CCF_DEFAULT_PROFILE_RAW else Path("")
MRI_CCF_MANIFEST_PATH = str(
    os.getenv("PROBEPLANNER_MRI_CCF_MANIFEST_PATH", "")
).strip()
MRI_CCF_ACTIVE_PROFILE_PATH = str(
    os.getenv("PROBEPLANNER_MRI_CCF_ACTIVE_PROFILE_PATH", "")
).strip()
MRI_CCF_DATASET_PROFILE = str(os.getenv("PROBEPLANNER_MRI_CCF_DATASET_PROFILE", "custom")).strip() or "custom"
MRI_CCF_USE_APPEARANCE = str(
    os.getenv("PROBEPLANNER_MRI_CCF_USE_APPEARANCE", "true")
).strip().lower() in {"1", "true", "yes", "on"}
MRI_CCF_SHOW_OUTER_BOUNDARY = str(
    os.getenv("PROBEPLANNER_MRI_CCF_SHOW_OUTER_BOUNDARY", "true")
).strip().lower() in {"1", "true", "yes", "on"}
MRI_CCF_SHOW_SKULL = str(
    os.getenv("PROBEPLANNER_MRI_CCF_SHOW_SKULL", "true")
).strip().lower() in {"1", "true", "yes", "on"}
MRI_CCF_SHOW_BRAIN_SURFACE = str(
    os.getenv("PROBEPLANNER_MRI_CCF_SHOW_BRAIN_SURFACE", "true")
).strip().lower() in {"1", "true", "yes", "on"}
MRI_CCF_SHOW_CCF_LABELS = str(
    os.getenv("PROBEPLANNER_MRI_CCF_SHOW_CCF_LABELS", "true")
).strip().lower() in {"1", "true", "yes", "on"}
MRI_CCF_OVERLAY_MODE = str(
    os.getenv("PROBEPLANNER_MRI_CCF_OVERLAY_MODE", "mri_ccf_overlay")
).strip() or "mri_ccf_overlay"
MRI_CCF_MEMMAP_ENABLED = str(
    os.getenv("PROBEPLANNER_MRI_CCF_MEMMAP", "true")
).strip().lower() in {"1", "true", "yes", "on"}
MRI_CCF_REQUIRE_MANIFEST = str(
    os.getenv("PROBEPLANNER_MRI_CCF_REQUIRE_MANIFEST", "true")
).strip().lower() in {"1", "true", "yes", "on"}
QUALITY_GATE_ENABLED = str(
    os.getenv("PROBEPLANNER_QUALITY_GATE_ENABLED", "true")
).strip().lower() in {"1", "true", "yes", "on"}
QUALITY_GATE_DICE_MIN = float(os.getenv("PROBEPLANNER_QUALITY_DICE_MIN", "0.80"))
QUALITY_GATE_HD95_MAX_MM = float(os.getenv("PROBEPLANNER_QUALITY_HD95_MAX_MM", "0.45"))
QUALITY_GATE_TRE_MAX_MM = float(os.getenv("PROBEPLANNER_QUALITY_TRE_MAX_MM", "0.30"))
QUALITY_GATE_JAC_ANOMALY_MAX = float(
    os.getenv("PROBEPLANNER_QUALITY_JAC_ANOMALY_MAX", "0.03")
)
PLANNER_CALIBRATION_PATH = str(
    os.getenv("PROBEPLANNER_PLANNER_CALIBRATION_PATH", "")
).strip()


def _normalize_atlas_mode(raw_mode):
    mode = str(raw_mode or "").strip().lower()
    if mode in {"allen", "allensdk", "allen_sdk", "allen-ccfv3", "allenccfv3"}:
        return "allen_sdk"
    if mode in {"qiu", "qiu2018", "qiu_2018", "qiu-2018"}:
        return "qiu2018"
    if mode in {
        "mri",
        "mri_ccf",
        "mri_ccf_hybrid",
        "mri-ccf-hybrid",
        "hybrid",
        "mri_hybrid",
    }:
        return "mri_ccf_hybrid"
    return "allen_sdk"


ACTIVE_ATLAS_MODE = _normalize_atlas_mode(
    os.getenv("PROBEPLANNER_ATLAS_MODE", "allen_sdk")
)


def _mri_runtime_get():
    with mri_mode_runtime_lock:
        return deepcopy(mri_mode_runtime_config)


def _mri_runtime_update(payload):
    if not isinstance(payload, dict):
        return {}
    clean = {}
    for key in (
        "manifestPath",
        "activeProfilePath",
        "annotationPath",
        "regionMapPath",
        "regionMapV2Path",
        "statsPath",
        "registrationSummaryPath",
        "jacobianPath",
        "skullMaskPath",
        "outerTissueMaskPath",
        "layeredSdfPath",
        "mriSurfaceMeshManifestPath",
        "datasetProfile",
    ):
        if key in payload:
            val = payload.get(key)
            if key == "datasetProfile":
                clean[key] = str(val or "custom").strip() or "custom"
            else:
                clean[key] = str(val or "").strip()
    if "useMriAppearance" in payload:
        val = payload.get("useMriAppearance")
        if isinstance(val, bool):
            clean["useMriAppearance"] = bool(val)
        elif isinstance(val, (int, float)):
            clean["useMriAppearance"] = bool(val)
        else:
            raw = str(val or "").strip().lower()
            clean["useMriAppearance"] = raw in {"1", "true", "yes", "on"}
    for key in (
        "showOuterBoundary",
        "showSkull",
        "showBrainSurface",
        "showCcfLabels",
    ):
        if key not in payload:
            continue
        val = payload.get(key)
        if isinstance(val, bool):
            clean[key] = bool(val)
        elif isinstance(val, (int, float)):
            clean[key] = bool(val)
        else:
            raw = str(val or "").strip().lower()
            clean[key] = raw in {"1", "true", "yes", "on"}
    if "overlayMode" in payload:
        overlay_mode = str(payload.get("overlayMode") or "").strip()
        if overlay_mode not in {"mri_ccf_overlay", "mri_only", "ccf_only"}:
            overlay_mode = "mri_ccf_overlay"
        clean["overlayMode"] = overlay_mode
    with mri_mode_runtime_lock:
        mri_mode_runtime_config.update(clean)
        return deepcopy(mri_mode_runtime_config)


def _qiu_payload():
    return {
        "annotationPath": QIU2018_ANNOTATION_PATH or None,
        "regionMapPath": QIU2018_REGION_MAP_PATH or None,
        "statsPath": QIU2018_STATS_PATH or None,
        "jacobianPath": QIU2018_JACOBIAN_PATH or None,
        "annotationConfigured": bool(QIU2018_ANNOTATION_PATH),
        "regionMapConfigured": bool(QIU2018_REGION_MAP_PATH),
        "statsConfigured": bool(QIU2018_STATS_PATH),
        "jacobianConfigured": bool(QIU2018_JACOBIAN_PATH),
    }


def _project_root_path():
    env_root = str(os.getenv("PROBEPLANNER_PROJECT_ROOT", "")).strip()
    if env_root:
        try:
            p = Path(env_root).expanduser().resolve()
            if p.exists():
                return p
        except Exception:
            pass
    try:
        return Path(__file__).resolve().parents[1]
    except Exception:
        return Path.cwd()


def _mri_seed_artifact_paths():
    seed_candidates = []
    try:
        # Prefer the validated local MRI+CCF profile over the bundled seed copy.
        # The seed directory can lag behind regenerated artifacts and cause
        # manifest/stats/mask path mixing.
        if MRI_CCF_DEFAULT_PROFILE_DIR:
            seed_candidates.append(MRI_CCF_DEFAULT_PROFILE_DIR)
    except Exception:
        pass
    try:
        seed_candidates.append(_project_root_path() / ".probeplanner" / "mri_ccf_hybrid_seed")
    except Exception:
        pass
    try:
        cwd = Path.cwd().resolve()
        seed_candidates.append(cwd / ".probeplanner" / "mri_ccf_hybrid_seed")
        seed_candidates.append(cwd / "probe-planner" / ".probeplanner" / "mri_ccf_hybrid_seed")
    except Exception:
        pass
    try:
        seed_candidates.append(Path.home().resolve() / ".probeplanner" / "mri_ccf_hybrid_seed")
    except Exception:
        pass
    seed_root = None
    seen = set()
    for candidate in seed_candidates:
        try:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.exists():
                seed_root = candidate
                break
        except Exception:
            continue
    if seed_root is None:
        return {}

    def _pick(*names):
        for name in names:
            candidate = seed_root / name
            if candidate.exists():
                return str(candidate.resolve())
        return ""

    return {
        "annotationPath": _pick("annotation.npy", "annotation.npz"),
        "regionMapPath": _pick("region_map.json"),
        "regionMapV2Path": _pick("region_map_v2.json"),
        "statsPath": _pick("stats.json"),
        "registrationSummaryPath": _pick("registration_summary.json", "registration/registration_summary.json"),
        "jacobianPath": _pick("jacobian.npy", "jacobian.npz"),
        "skullMaskPath": _pick("skull_mask.npy", "skull_mask.npz"),
        "outerTissueMaskPath": _pick("outer_tissue_mask.npy", "outer_tissue_mask.npz"),
        "layeredSdfPath": _pick("layered_sdf.npz", "layered_sdf.npy"),
        "manifestPath": _pick("artifact_manifest.json"),
        "activeProfilePath": _pick("active_profile.json"),
        "datasetProfile": "seed_default",
    }


def _safe_read_json_obj(path_text):
    path = Path(path_text).expanduser() if path_text else None
    if not path or not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _resolve_contract_path(base_dir, path_text):
    raw = str(path_text or "").strip()
    if not raw:
        return None
    try:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (Path(base_dir) / p).resolve()
        if p.exists():
            return str(p.resolve())
    except Exception:
        return None
    return None


def _artifact_candidate_dirs(path_text, max_parent_hops=2):
    resolved = _resolve_existing_path(path_text)
    if not resolved:
        return []
    try:
        p = Path(resolved)
    except Exception:
        return []
    seeds = [p if p.is_dir() else p.parent]
    if seeds and str(seeds[0].name or "").strip().lower() in {"registration", "reg"}:
        seeds.append(seeds[0].parent)
    dirs = []
    seen = set()
    for seed in seeds:
        cur = seed
        hops = 0
        while cur and hops <= max_parent_hops:
            key = str(cur)
            if key not in seen and cur.exists():
                seen.add(key)
                dirs.append(cur)
            try:
                parent = cur.parent
            except Exception:
                break
            if not parent or str(parent) == str(cur):
                break
            cur = parent
            hops += 1
    return dirs


def _find_neighbor_artifact(filenames, candidates):
    names = filenames if isinstance(filenames, (list, tuple)) else [filenames]
    names = [str(x).strip() for x in names if str(x or "").strip()]
    if not names:
        return ""
    for candidate in candidates or []:
        for base_dir in _artifact_candidate_dirs(candidate):
            for name in names:
                try:
                    probe = (base_dir / name).resolve()
                except Exception:
                    continue
                if probe.exists():
                    return str(probe)
    return ""


def _file_sha256(path_text):
    path = Path(path_text).expanduser() if path_text else None
    if not path or not path.exists():
        return ""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _load_mri_manifest_contract(manifest_path):
    """
    Parse MRI artifact manifest and resolve absolute artifact paths.
    """
    resolved_manifest = _resolve_existing_path(manifest_path)
    if not resolved_manifest:
        return {
            "manifestPath": None,
            "manifestValid": False,
            "manifestSchemaVersion": None,
            "manifestProfileId": None,
            "manifestHash": "",
            "artifacts": {},
            "atlasShape": None,
            "errors": ["manifest_missing"],
        }

    data = _safe_read_json_obj(resolved_manifest)
    if not data:
        return {
            "manifestPath": resolved_manifest,
            "manifestValid": False,
            "manifestSchemaVersion": None,
            "manifestProfileId": None,
            "manifestHash": _file_sha256(resolved_manifest),
            "artifacts": {},
            "atlasShape": None,
            "errors": ["manifest_invalid_json"],
        }

    schema = str(data.get("schemaVersion") or "").strip()
    schema_supported = (
        schema.startswith("probeplanner.mri_ccf.artifact_manifest.")
        or schema.startswith("probeplanner.mri_allen_ants.strict_manifest.")
    )
    if not schema_supported:
        return {
            "manifestPath": resolved_manifest,
            "manifestValid": False,
            "manifestSchemaVersion": schema or None,
            "manifestProfileId": data.get("profileId"),
            "manifestHash": _file_sha256(resolved_manifest),
            "artifacts": {},
            "atlasShape": data.get("atlasShape"),
            "errors": ["manifest_schema_unsupported"],
        }

    artifacts = data.get("artifacts") if isinstance(data.get("artifacts"), dict) else {}
    base_dir = Path(resolved_manifest).parent
    resolved_artifacts = {}
    for key, entry in artifacts.items():
        if not isinstance(entry, dict):
            continue
        resolved_artifacts[str(key)] = {
            **entry,
            "path": _resolve_contract_path(base_dir, entry.get("path")),
        }

    errors = []
    verified_hashes = {}
    if data.get("valid") is False:
        errors.append("manifest_valid_false")
    for key, entry in artifacts.items():
        if not isinstance(entry, dict) or not bool(entry.get("required", False)):
            continue
        rec = resolved_artifacts.get(str(key)) or {}
        path_text = rec.get("path")
        if not path_text:
            errors.append(f"required_artifact_missing:{key}")
            continue
        expected = str(entry.get("sha256") or "").strip().lower()
        if not expected:
            errors.append(f"required_hash_missing:{key}")
            continue
        actual = str(_file_sha256(path_text) or "").strip().lower()
        if actual != expected:
            errors.append(f"artifact_hash_mismatch:{key}")
        else:
            verified_hashes[str(key)] = actual

    return {
        "manifestPath": resolved_manifest,
        "manifestValid": len(errors) == 0,
        "manifestSchemaVersion": schema,
        "manifestProfileId": str(data.get("profileId") or "").strip() or None,
        "manifestHash": _file_sha256(resolved_manifest),
        "artifacts": resolved_artifacts,
        "verifiedArtifactHashes": verified_hashes if not errors else {},
        "atlasShape": data.get("atlasShape"),
        "errors": errors,
    }


def _canonical_existing_path(path_text):
    if not path_text:
        return ""
    try:
        p = Path(str(path_text)).expanduser()
        if p.exists():
            return str(p.resolve()).lower()
        return str(p).lower()
    except Exception:
        return str(path_text or "").strip().lower()


def _stats_internal_artifact_paths(stats_path):
    stats = _safe_read_json_obj(stats_path)
    if not isinstance(stats, dict):
        return {}
    stats_base_dir = None
    try:
        resolved_stats_path = _resolve_existing_path(stats_path)
        if resolved_stats_path:
            stats_base_dir = Path(resolved_stats_path).parent
    except Exception:
        stats_base_dir = None
    key_map = {
        "annotationPath": "annotation",
        "skullMaskPath": "skull_mask",
        "outerTissueMaskPath": "outer_tissue_mask",
        "layeredSdfPath": "layered_sdf",
        "registrationSummaryPath": "registration_summary",
        "warpedMriPath": "warped_mri",
        "jacobianPath": "jacobian",
    }
    out = {}
    for stats_key, artifact_key in key_map.items():
        raw = str(stats.get(stats_key) or "").strip()
        if raw:
            try:
                raw_path = Path(raw).expanduser()
                if stats_base_dir and not raw_path.is_absolute():
                    raw_path = (stats_base_dir / raw_path).resolve()
                out[artifact_key] = str(raw_path)
            except Exception:
                out[artifact_key] = raw
    return out


def _build_mri_profile_consistency(payload, manifest=None):
    payload = payload if isinstance(payload, dict) else {}
    manifest = manifest if isinstance(manifest, dict) else _load_mri_manifest_contract(payload.get("manifestPath"))
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    stats_internal = _stats_internal_artifact_paths(payload.get("statsPath"))
    payload_key_map = {
        "annotation": "annotationPath",
        "region_map": "regionMapPath",
        "region_map_v2": "regionMapV2Path",
        "stats": "statsPath",
        "registration_summary": "registrationSummaryPath",
        "warped_mri": "warpedMriPath",
        "jacobian": "jacobianPath",
        "skull_mask": "skullMaskPath",
        "outer_tissue_mask": "outerTissueMaskPath",
        "layered_sdf": "layeredSdfPath",
    }
    resolved = {}
    mismatches = []
    for artifact_key, payload_key in payload_key_map.items():
        payload_path = payload.get(payload_key)
        manifest_path = None
        rec = artifacts.get(artifact_key) if isinstance(artifacts, dict) else None
        if isinstance(rec, dict):
            manifest_path = rec.get("path")
        stats_path = stats_internal.get(artifact_key)
        resolved[artifact_key] = {
            "payloadPath": payload_path,
            "manifestPath": manifest_path,
            "statsPath": stats_path,
            "exists": bool(_path_exists(payload_path)),
        }
        if payload_path and manifest_path and _canonical_existing_path(payload_path) != _canonical_existing_path(manifest_path):
            mismatches.append({
                "artifact": artifact_key,
                "kind": "payload_vs_manifest",
                "payloadPath": payload_path,
                "manifestPath": manifest_path,
            })
        if stats_path and manifest_path and _canonical_existing_path(stats_path) != _canonical_existing_path(manifest_path):
            mismatches.append({
                "artifact": artifact_key,
                "kind": "stats_vs_manifest",
                "statsPath": stats_path,
                "manifestPath": manifest_path,
            })
    return {
        "profileId": payload.get("datasetProfile") or manifest.get("manifestProfileId") or MRI_CCF_DATASET_PROFILE,
        "passed": len(mismatches) == 0,
        "mismatches": mismatches,
        "paths": resolved,
        "statsInternalPaths": stats_internal,
        "manifestHash": manifest.get("manifestHash", ""),
    }


def _build_missing_mri_artifacts(payload):
    missing = []
    manifest_ok = bool(str(payload.get("manifestPath") or "").strip() and _path_exists(payload.get("manifestPath")))
    if not manifest_ok:
        missing.append("manifest")

    annotation_ok = bool(str(payload.get("annotationPath") or "").strip() and _path_exists(payload.get("annotationPath")))
    if not annotation_ok:
        missing.append("annotation")

    region_v1_ok = bool(str(payload.get("regionMapPath") or "").strip() and _path_exists(payload.get("regionMapPath")))
    region_v2_ok = bool(str(payload.get("regionMapV2Path") or "").strip() and _path_exists(payload.get("regionMapV2Path")))
    if not (region_v1_ok or region_v2_ok):
        missing.append("region_map")

    stats_ok = bool(str(payload.get("statsPath") or "").strip() and _path_exists(payload.get("statsPath")))
    if not stats_ok:
        missing.append("stats")

    registration_ok = bool(
        str(payload.get("registrationSummaryPath") or "").strip()
        and _path_exists(payload.get("registrationSummaryPath"))
    )
    if not registration_ok:
        missing.append("registration_summary")

    skull_ok = bool(str(payload.get("skullMaskPath") or "").strip() and _path_exists(payload.get("skullMaskPath")))
    if not skull_ok:
        missing.append("skull_mask")

    outer_ok = bool(
        str(payload.get("outerTissueMaskPath") or "").strip()
        and _path_exists(payload.get("outerTissueMaskPath"))
    )
    if not outer_ok:
        missing.append("outer_tissue_mask")

    sdf_ok = bool(str(payload.get("layeredSdfPath") or "").strip() and _path_exists(payload.get("layeredSdfPath")))
    if not sdf_ok:
        missing.append("layered_sdf")
    return missing


def _mri_payload():
    runtime = _mri_runtime_get()
    seed = _mri_seed_artifact_paths()
    manifest_only = bool(MRI_CCF_REQUIRE_MANIFEST)
    active_profile_path = str(
        MRI_CCF_ACTIVE_PROFILE_PATH
        or runtime.get("activeProfilePath")
        or seed.get("activeProfilePath")
        or ""
    ).strip()
    active_profile = _safe_read_json_obj(active_profile_path)
    profile_manifest_path = str(
        active_profile.get("manifestPath") or ""
        if isinstance(active_profile, dict)
        else ""
    ).strip()
    manifest_path = str(
        MRI_CCF_MANIFEST_PATH
        or runtime.get("manifestPath")
        or profile_manifest_path
        or seed.get("manifestPath")
        or ""
    ).strip()
    if not manifest_path:
        manifest_path = _find_neighbor_artifact(
            "artifact_manifest.json",
            [
                active_profile_path,
                runtime.get("annotationPath"),
                MRI_CCF_ANNOTATION_PATH,
                seed.get("annotationPath"),
                runtime.get("regionMapPath"),
                MRI_CCF_REGION_MAP_PATH,
                seed.get("regionMapPath"),
                runtime.get("statsPath"),
                MRI_CCF_STATS_PATH,
                seed.get("statsPath"),
                runtime.get("registrationSummaryPath"),
                seed.get("registrationSummaryPath"),
                runtime.get("layeredSdfPath"),
                MRI_CCF_LAYERED_SDF_PATH,
                seed.get("layeredSdfPath"),
            ],
        )
    manifest = _load_mri_manifest_contract(manifest_path)
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else {}

    def _manifest_pick(name):
        rec = artifacts.get(name) if isinstance(artifacts, dict) else None
        if isinstance(rec, dict):
            return str(rec.get("path") or "").strip()
        return ""

    def _artifact_pick(name, *fallbacks):
        manifest_value = _manifest_pick(name)
        if manifest_only:
            return str(manifest_value or "").strip()
        for value in (manifest_value, *fallbacks):
            if str(value or "").strip():
                return str(value or "").strip()
        return ""

    annotation_path = str(
        _artifact_pick(
            "annotation",
            MRI_CCF_ANNOTATION_PATH,
            runtime.get("annotationPath"),
            seed.get("annotationPath"),
        )
    ).strip()
    region_map_path = str(
        _artifact_pick(
            "region_map",
            MRI_CCF_REGION_MAP_PATH,
            runtime.get("regionMapPath"),
            seed.get("regionMapPath"),
        )
    ).strip()
    region_map_v2_path = str(
        _artifact_pick(
            "region_map_v2",
            runtime.get("regionMapV2Path"),
            seed.get("regionMapV2Path"),
        )
    ).strip()
    stats_path = str(
        _artifact_pick(
            "stats",
            MRI_CCF_STATS_PATH,
            runtime.get("statsPath"),
            seed.get("statsPath"),
        )
    ).strip()
    registration_summary_path = str(
        (
            runtime.get("registrationSummaryPath")
            if (not manifest_only and str(runtime.get("registrationSummaryPath") or "").strip())
            else _artifact_pick(
                "registration_summary",
                seed.get("registrationSummaryPath"),
            )
        )
    ).strip()
    warped_mri_path = str(
        _artifact_pick(
            "warped_mri",
            runtime.get("warpedMriPath"),
            seed.get("warpedMriPath"),
        )
    ).strip()
    if not warped_mri_path and not manifest_only:
        warped_mri_path = _find_neighbor_artifact(
            ["ambmc_in_ccf.nii.gz", "registration/ambmc_in_ccf.nii.gz"],
            [
                registration_summary_path,
                active_profile_path,
                runtime.get("annotationPath"),
                seed.get("annotationPath"),
            ],
        )
    jac_path = str(
        _artifact_pick(
            "jacobian",
            MRI_CCF_JACOBIAN_PATH,
            runtime.get("jacobianPath"),
            seed.get("jacobianPath"),
        )
    ).strip()
    skull_mask_path = str(
        _artifact_pick(
            "skull_mask",
            MRI_CCF_SKULL_MASK_PATH,
            runtime.get("skullMaskPath"),
            seed.get("skullMaskPath"),
        )
    ).strip()
    outer_tissue_path = str(
        _artifact_pick(
            "outer_tissue_mask",
            MRI_CCF_OUTER_TISSUE_MASK_PATH,
            runtime.get("outerTissueMaskPath"),
            seed.get("outerTissueMaskPath"),
        )
    ).strip()
    layered_sdf_path = str(
        _artifact_pick(
            "layered_sdf",
            MRI_CCF_LAYERED_SDF_PATH,
            runtime.get("layeredSdfPath"),
            seed.get("layeredSdfPath"),
        )
    ).strip()
    mri_surface_mesh_manifest_path = str(
        _artifact_pick(
            "mri_surface_meshes",
            MRI_CCF_SURFACE_MESH_MANIFEST_PATH,
            runtime.get("mriSurfaceMeshManifestPath"),
            seed.get("mriSurfaceMeshManifestPath"),
        )
    ).strip()
    if not mri_surface_mesh_manifest_path:
        mri_surface_mesh_manifest_path = _find_neighbor_artifact(
            ["mri_surfaces/surface_manifest.json", "surface_manifest.json"],
            [
                manifest.get("manifestPath"),
                active_profile_path,
                annotation_path,
                skull_mask_path,
                outer_tissue_path,
            ],
        )
    if not registration_summary_path and not manifest_only:
        registration_summary_path = _find_neighbor_artifact(
            ["registration_summary.json", "registration/registration_summary.json"],
            [
                manifest.get("manifestPath"),
                active_profile_path,
                annotation_path,
                region_map_path,
                region_map_v2_path,
                stats_path,
                layered_sdf_path,
                skull_mask_path,
                outer_tissue_path,
            ],
        )
    dataset_profile = str(
        manifest.get("manifestProfileId")
        or MRI_CCF_DATASET_PROFILE
        or runtime.get("datasetProfile")
        or seed.get("datasetProfile")
        or "custom"
    ).strip() or "custom"
    appearance_raw = runtime.get("useMriAppearance", MRI_CCF_USE_APPEARANCE)
    if isinstance(appearance_raw, bool):
        use_mri_appearance = appearance_raw
    elif isinstance(appearance_raw, (int, float)):
        use_mri_appearance = bool(appearance_raw)
    else:
        use_mri_appearance = str(appearance_raw or "").strip().lower() in {"1", "true", "yes", "on"}
    show_outer_boundary = _coerce_bool(
        runtime.get("showOuterBoundary", MRI_CCF_SHOW_OUTER_BOUNDARY),
        default=MRI_CCF_SHOW_OUTER_BOUNDARY,
    )
    show_skull = _coerce_bool(
        runtime.get("showSkull", MRI_CCF_SHOW_SKULL),
        default=MRI_CCF_SHOW_SKULL,
    )
    show_brain_surface = _coerce_bool(
        runtime.get("showBrainSurface", MRI_CCF_SHOW_BRAIN_SURFACE),
        default=MRI_CCF_SHOW_BRAIN_SURFACE,
    )
    show_ccf_labels = _coerce_bool(
        runtime.get("showCcfLabels", MRI_CCF_SHOW_CCF_LABELS),
        default=MRI_CCF_SHOW_CCF_LABELS,
    )
    overlay_mode = str(runtime.get("overlayMode", MRI_CCF_OVERLAY_MODE) or "").strip()
    if overlay_mode not in {"mri_ccf_overlay", "mri_only", "ccf_only"}:
        overlay_mode = "mri_ccf_overlay"
    payload = {
        "annotationPath": annotation_path or None,
        "regionMapPath": region_map_path or None,
        "regionMapV2Path": region_map_v2_path or None,
        "statsPath": stats_path or None,
        "registrationSummaryPath": registration_summary_path or None,
        "warpedMriPath": warped_mri_path or None,
        "jacobianPath": jac_path or None,
        "skullMaskPath": skull_mask_path or None,
        "outerTissueMaskPath": outer_tissue_path or None,
        "layeredSdfPath": layered_sdf_path or None,
        "mriSurfaceMeshManifestPath": mri_surface_mesh_manifest_path or None,
        "manifestPath": manifest.get("manifestPath"),
        "manifestHash": manifest.get("manifestHash", ""),
        "manifestValid": bool(manifest.get("manifestValid")),
        "verifiedArtifactHashes": deepcopy(manifest.get("verifiedArtifactHashes") or {}),
        "manifestSchemaVersion": manifest.get("manifestSchemaVersion"),
        "profileId": manifest.get("manifestProfileId") or dataset_profile,
        "activeProfilePath": _resolve_existing_path(active_profile_path),
        "datasetProfile": dataset_profile,
        "atlasShapeFromManifest": manifest.get("atlasShape"),
        "useMriAppearance": bool(use_mri_appearance),
        "showOuterBoundary": bool(show_outer_boundary),
        "showSkull": bool(show_skull),
        "showBrainSurface": bool(show_brain_surface),
        "showCcfLabels": bool(show_ccf_labels),
        "overlayMode": overlay_mode,
        "manifestOnly": bool(manifest_only),
        "annotationConfigured": bool(_path_exists(annotation_path)),
        "regionMapConfigured": bool(_path_exists(region_map_path)),
        "regionMapV2Configured": bool(_path_exists(region_map_v2_path)),
        "statsConfigured": bool(_path_exists(stats_path)),
        "registrationSummaryConfigured": bool(_path_exists(registration_summary_path)),
        "jacobianConfigured": bool(_path_exists(jac_path)),
        "skullMaskConfigured": bool(_path_exists(skull_mask_path)),
        "outerTissueMaskConfigured": bool(_path_exists(outer_tissue_path)),
        "layeredSdfConfigured": bool(_path_exists(layered_sdf_path)),
        "mriSurfaceMeshManifestConfigured": bool(_path_exists(mri_surface_mesh_manifest_path)),
    }
    consistency = _build_mri_profile_consistency(payload, manifest=manifest)
    payload["pathConsistency"] = consistency
    payload["artifactPaths"] = consistency.get("paths", {})
    missing = _build_missing_mri_artifacts(payload)
    payload["missingArtifacts"] = missing
    warped_mri_ok = bool(str(payload.get("warpedMriPath") or "").strip() and _path_exists(payload.get("warpedMriPath")))
    payload["optionalMissingArtifacts"] = [] if warped_mri_ok else ["warped_mri"]
    contract_violations = []
    if not bool(consistency.get("passed", True)):
        contract_violations.append({
            "code": "profile_path_mismatch",
            "message": "manifest/stats/runtime artifact paths are inconsistent",
            "mismatches": deepcopy(consistency.get("mismatches") or []),
        })
    payload["contractViolations"] = contract_violations
    payload["artifactReady"] = bool(len(missing) == 0 and len(contract_violations) == 0)
    payload["artifactContractMode"] = "manifest_only" if MRI_CCF_REQUIRE_MANIFEST else "manifest_preferred"
    if MRI_CCF_REQUIRE_MANIFEST and not payload.get("manifestValid"):
        payload["artifactReady"] = False
        if "manifest" not in payload["missingArtifacts"]:
            payload["missingArtifacts"] = ["manifest", *payload["missingArtifacts"]]
    return payload


def _build_mri_layer_status(mri_payload=None, risk_snapshot=None, active_mode=None):
    payload = mri_payload if isinstance(mri_payload, dict) else _mri_payload()
    risk = risk_snapshot if isinstance(risk_snapshot, dict) else deepcopy(risk_model_summary)
    mode = str(active_mode or ACTIVE_ATLAS_MODE or "allen_sdk")
    in_hybrid = mode == "mri_ccf_hybrid"
    return {
        "activeAtlasMode": mode,
        "hybridActive": bool(in_hybrid),
        "profileId": payload.get("profileId") or payload.get("datasetProfile"),
        "artifactReady": bool(payload.get("artifactReady", False)),
        "missingArtifacts": list(payload.get("missingArtifacts") or []),
        "contractViolations": deepcopy(payload.get("contractViolations") or []),
        "artifactContractMode": str(payload.get("artifactContractMode") or "manifest_preferred"),
        "manifestValid": bool(payload.get("manifestValid", False)),
        "manifestPath": payload.get("manifestPath"),
        "statsPath": payload.get("statsPath"),
        "registrationSummaryPath": payload.get("registrationSummaryPath"),
        "skullMaskPath": payload.get("skullMaskPath"),
        "outerTissueMaskPath": payload.get("outerTissueMaskPath"),
        "layeredSdfPath": payload.get("layeredSdfPath"),
        "jacobianPath": payload.get("jacobianPath"),
        "pathConsistency": deepcopy(payload.get("pathConsistency") or {}),
        "appearanceConfigured": bool(payload.get("useMriAppearance", False)),
        "appearanceActive": bool(in_hybrid and payload.get("useMriAppearance", False)),
        "appearanceLayers": {
            "showOuterBoundary": bool(payload.get("showOuterBoundary", False)),
            "showSkull": bool(payload.get("showSkull", False)),
            "showBrainSurface": bool(payload.get("showBrainSurface", False)),
            "showCcfLabels": bool(payload.get("showCcfLabels", False)),
            "overlayMode": payload.get("overlayMode") or "mri_ccf_overlay",
        },
        "surfaceMeshes": _build_mri_surface_mesh_status(payload),
        "riskLayerStructuredMaskEnabled": bool(risk.get("structuredMaskEnabled", False)),
        "riskLayerSdfEnabled": bool(risk.get("sdfEnabled", False)),
        "riskLayerDistanceModel": risk.get("distanceModel"),
        "riskLayerSources": sorted(str(x) for x in (risk.get("source") or [])),
        "riskLayerCompleteness": deepcopy(risk.get("riskLayerCompleteness") or {}),
        "missingStructuredRiskClasses": list(risk.get("missingStructuredRiskClasses") or []),
        "requiredLayeredSdfChannels": list(risk.get("requiredLayeredSdfChannels") or []),
        "skullMaskConfigured": bool(payload.get("skullMaskConfigured", False)),
        "outerTissueMaskConfigured": bool(payload.get("outerTissueMaskConfigured", False)),
        "layeredSdfConfigured": bool(payload.get("layeredSdfConfigured", False)),
    }


def _build_active_profile_contract(qiu_payload=None, mri_payload=None, risk_snapshot=None):
    qiu = qiu_payload if isinstance(qiu_payload, dict) else _qiu_payload()
    mri = mri_payload if isinstance(mri_payload, dict) else _mri_payload()
    risk = risk_snapshot if isinstance(risk_snapshot, dict) else deepcopy(risk_model_summary)
    risk_contract = {
        "enabled": bool(risk.get("enabled")),
        "sourceTag": risk.get("sourceTag"),
        "distanceModel": risk.get("distanceModel"),
        "degraded": bool(not risk.get("enabled")),
        "error": risk.get("error"),
    }

    if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid":
        return {
            "mode": "mri_ccf_hybrid",
            "profileId": mri.get("profileId") or mri.get("datasetProfile"),
            "artifactReady": bool(mri.get("artifactReady", False)),
            "manifestPath": mri.get("manifestPath"),
            "activeProfilePath": mri.get("activeProfilePath"),
            "annotationPath": mri.get("annotationPath"),
            "regionMapPath": mri.get("regionMapV2Path") or mri.get("regionMapPath"),
            "statsPath": mri.get("statsPath"),
            "registrationSummaryPath": mri.get("registrationSummaryPath"),
            "jacobianPath": mri.get("jacobianPath"),
            "skullMaskPath": mri.get("skullMaskPath"),
            "outerTissueMaskPath": mri.get("outerTissueMaskPath"),
            "layeredSdfPath": mri.get("layeredSdfPath"),
            "mriSurfaceMeshManifestPath": mri.get("mriSurfaceMeshManifestPath"),
            "missingArtifacts": list(mri.get("missingArtifacts") or []),
            "contractViolations": deepcopy(mri.get("contractViolations") or []),
            "pathConsistency": deepcopy(mri.get("pathConsistency") or {}),
            "riskModel": risk_contract,
        }

    if ACTIVE_ATLAS_MODE == "qiu2018":
        annotation_ready = bool(qiu.get("annotationConfigured"))
        return {
            "mode": "qiu2018",
            "profileId": "qiu2018",
            "artifactReady": annotation_ready,
            "manifestPath": None,
            "activeProfilePath": None,
            "annotationPath": qiu.get("annotationPath"),
            "regionMapPath": qiu.get("regionMapPath"),
            "statsPath": qiu.get("statsPath"),
            "registrationSummaryPath": None,
            "jacobianPath": qiu.get("jacobianPath"),
            "skullMaskPath": None,
            "outerTissueMaskPath": None,
            "layeredSdfPath": None,
            "missingArtifacts": [] if annotation_ready else ["annotation"],
            "contractViolations": [],
            "pathConsistency": {"passed": True, "mismatches": []},
            "riskModel": risk_contract,
        }

    return {
        "mode": "allen_sdk",
        "profileId": "allen_ccfv3",
        "artifactReady": True,
        "manifestPath": None,
        "activeProfilePath": None,
        "annotationPath": "AllenSDK ReferenceSpaceCache annotation",
        "regionMapPath": "AllenSDK structure tree",
        "statsPath": None,
        "registrationSummaryPath": None,
        "jacobianPath": None,
        "skullMaskPath": None,
        "outerTissueMaskPath": None,
        "layeredSdfPath": None,
        "missingArtifacts": [],
        "contractViolations": [],
        "pathConsistency": {"passed": True, "mismatches": []},
        "riskModel": risk_contract,
    }


def _path_exists(path_text):
    try:
        if not path_text:
            return False
        return Path(str(path_text)).expanduser().exists()
    except Exception:
        return False


def _resolve_existing_path(path_text):
    if not path_text:
        return None
    p = Path(str(path_text)).expanduser()
    if not p.exists():
        return None
    return str(p.resolve())


def _normalize_progress_01(raw, default=0.0):
    try:
        value = float(raw)
    except Exception:
        value = float(default)
    if value > 1.0:
        value = value / 100.0
    return float(max(0.0, min(1.0, value)))


def _now_iso():
    return _utc_now_iso()


def _build_workflow_entry(
    *,
    status=TASK_STATE_QUEUED,
    stage="queued",
    progress=0.0,
    message="",
    updated_at=None,
    extra=None,
):
    normalized_status = normalize_task_status(status, TASK_STATE_QUEUED)
    payload = {
        "status": normalized_status,
        "state": normalized_status,
        "stage": str(stage or "queued"),
        "progress": _normalize_progress_01(progress, 0.0),
        "message": str(message or ""),
        "updatedAt": str(updated_at or _now_iso()),
    }
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def _set_registration_runtime_state(*, status=None, stage=None, progress=None, message=None, result=None, error=None):
    with workflow_state_lock:
        if status is not None:
            registration_runtime_state["status"] = normalize_task_status(status, registration_runtime_state.get("status"))
        if stage is not None:
            registration_runtime_state["stage"] = str(stage or "")
        if progress is not None:
            registration_runtime_state["progress"] = _normalize_progress_01(progress, registration_runtime_state.get("progress", 0.0))
        if message is not None:
            registration_runtime_state["message"] = str(message or "")
        if result is not None:
            registration_runtime_state["lastResult"] = deepcopy(result)
        if error is not None:
            registration_runtime_state["lastError"] = str(error)
        registration_runtime_state["updatedAt"] = _now_iso()


def _set_planning_runtime_state(*, status=None, stage=None, progress=None, message=None, task_id=None, result_summary=None, error=None):
    with workflow_state_lock:
        if status is not None:
            planning_runtime_state["status"] = normalize_task_status(status, planning_runtime_state.get("status"))
        if stage is not None:
            planning_runtime_state["stage"] = str(stage or "")
        if progress is not None:
            planning_runtime_state["progress"] = _normalize_progress_01(progress, planning_runtime_state.get("progress", 0.0))
        if message is not None:
            planning_runtime_state["message"] = str(message or "")
        if task_id is not None:
            planning_runtime_state["taskId"] = str(task_id or "")
        if result_summary is not None:
            planning_runtime_state["lastResultSummary"] = deepcopy(result_summary)
        if error is not None:
            planning_runtime_state["lastError"] = str(error)
        planning_runtime_state["updatedAt"] = _now_iso()


def _normalize_risk_threshold_template(name):
    key = str(name or "").strip().lower()
    if key in RISK_THRESHOLD_TEMPLATES:
        return key
    return RISK_THRESHOLD_TEMPLATE_DEFAULT


def _risk_threshold_template_payload(active_template):
    out = {}
    for name, values in RISK_THRESHOLD_TEMPLATES.items():
        out[name] = {k: float(v) for k, v in values.items()}
    return {
        "active": _normalize_risk_threshold_template(active_template),
        "defaultTemplate": RISK_THRESHOLD_TEMPLATE_DEFAULT,
        "templates": out,
    }


def _enforce_boundary_risk_threshold_floors(thresholds):
    out = dict(thresholds if isinstance(thresholds, dict) else {})
    for key, floor in BOUNDARY_RISK_THRESHOLD_FLOORS_MM.items():
        try:
            current = float(out.get(key, 0.0))
        except Exception:
            current = 0.0
        out[key] = max(current, float(floor))
    return out


def _resolve_risk_thresholds_with_template(params_in):
    if not isinstance(params_in, dict):
        params_in = {}
    requested_template = _normalize_risk_threshold_template(params_in.get("riskThresholdTemplate"))
    base_thresholds = deepcopy(RISK_THRESHOLD_TEMPLATES.get(requested_template, RISK_THRESHOLD_TEMPLATES[RISK_THRESHOLD_TEMPLATE_DEFAULT]))
    min_risk_distance_mm = float(params_in.get("minRiskDistanceMm", base_thresholds.get("default", MIN_RISK_DISTANCE_MM_DEFAULT)))
    merged = dict(base_thresholds)
    merged["default"] = min_risk_distance_mm
    user_raw = params_in.get("riskThresholdsMm")
    user_clean = sanitize_risk_thresholds(user_raw, default_mm=min_risk_distance_mm)
    merged.update(user_clean)
    sanitized = sanitize_risk_thresholds(merged, default_mm=min_risk_distance_mm)
    return requested_template, _enforce_boundary_risk_threshold_floors(sanitized)


def _load_planner_calibration_profile(path_override=None):
    global planner_calibration_profile
    path_text = str(path_override or PLANNER_CALIBRATION_PATH or "").strip()
    base_profile = {
        "source": "default",
        "loaded": False,
        "path": path_text or None,
        "objectiveWeights": {},
        "riskThresholdsMm": {},
        "uncertaintyCalibration": {},
        "meta": {},
    }
    if not path_text:
        planner_calibration_profile = base_profile
        return deepcopy(planner_calibration_profile)

    path = Path(path_text)
    if not path.exists():
        base_profile["source"] = "missing_file"
        planner_calibration_profile = base_profile
        return deepcopy(planner_calibration_profile)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        base_profile["source"] = f"load_error:{exc.__class__.__name__}"
        planner_calibration_profile = base_profile
        return deepcopy(planner_calibration_profile)

    if not isinstance(raw, dict):
        base_profile["source"] = "invalid_schema"
        planner_calibration_profile = base_profile
        return deepcopy(planner_calibration_profile)

    unc_in = raw.get("uncertaintyCalibration", {})
    unc = {}
    if isinstance(unc_in, dict):
        for key in (
            "entrySigmaMm",
            "angleSigmaDeg",
            "depthSigmaMm",
            "riskDistanceSigmaMm",
            "riskMarginTreMm",
            "landmarkTreMm",
            "bregmaLambdaTreMm",
            "bregmaTreMm",
            "lambdaTreMm",
        ):
            try:
                if key in unc_in and unc_in.get(key) is not None:
                    unc[key] = float(unc_in.get(key))
            except Exception:
                continue

    w_in = raw.get("objectiveWeights", {})
    weights = {}
    if isinstance(w_in, dict):
        key_map = {
            "coverage": "coverage",
            "safety": "safety",
            "angle": "angle",
            "objectiveCoverageWeight": "coverage",
            "objectiveSafetyWeight": "safety",
            "objectiveAngleWeight": "angle",
        }
        for k, v in w_in.items():
            norm_key = key_map.get(str(k))
            if not norm_key:
                continue
            try:
                weights[norm_key] = float(v)
            except Exception:
                pass
        if {"coverage", "safety", "angle"} <= set(weights.keys()):
            s = sum(max(0.0, float(weights[k])) for k in ("coverage", "safety", "angle"))
            if s > 1e-9:
                weights = {k: float(max(0.0, weights[k]) / s) for k in ("coverage", "safety", "angle")}
            else:
                weights = {}
        else:
            weights = {}

    thr_in = raw.get("riskThresholdsMm", {})
    thresholds = {}
    if isinstance(thr_in, dict):
        try:
            default_mm = float(thr_in.get("default", MIN_RISK_DISTANCE_MM_DEFAULT))
        except Exception:
            default_mm = float(MIN_RISK_DISTANCE_MM_DEFAULT)
        thresholds = sanitize_risk_thresholds(thr_in, default_mm=default_mm)

    profile = {
        "source": "file",
        "loaded": True,
        "path": str(path),
        "objectiveWeights": weights,
        "riskThresholdsMm": thresholds,
        "uncertaintyCalibration": unc,
        "meta": raw.get("meta", {}) if isinstance(raw.get("meta"), dict) else {},
    }
    planner_calibration_profile = profile
    return deepcopy(planner_calibration_profile)


def _suggest_mri_retry_payload(bundle):
    cfg = bundle if isinstance(bundle, dict) else {}
    return {
        "manifestPath": cfg.get("manifestPath") or "",
        "activeProfilePath": cfg.get("activeProfilePath") or "",
        "annotationPath": cfg.get("annotationPath") or "",
        "regionMapPath": cfg.get("regionMapPath") or "",
        "regionMapV2Path": cfg.get("regionMapV2Path") or "",
        "statsPath": cfg.get("statsPath") or "",
        "registrationSummaryPath": cfg.get("registrationSummaryPath") or "",
        "jacobianPath": cfg.get("jacobianPath") or "",
        "skullMaskPath": cfg.get("skullMaskPath") or "",
        "outerTissueMaskPath": cfg.get("outerTissueMaskPath") or "",
        "layeredSdfPath": cfg.get("layeredSdfPath") or "",
        "datasetProfile": str(cfg.get("datasetProfile") or "custom"),
        "activateAfterBuild": True,
        "switchMode": True,
        "autoBuild": False,
    }


def _build_mri_repair_hints(error_text, bundle):
    message = str(error_text or "").strip() or "mri_bundle_validation_failed"
    lower = message.lower()
    suggestions = []
    if "manifest" in lower:
        suggestions.extend(
            [
                "Provide artifact_manifest.json and set manifestPath (or PROBEPLANNER_MRI_CCF_MANIFEST_PATH).",
                "Ensure manifest schemaVersion starts with probeplanner.mri_ccf.artifact_manifest. or probeplanner.mri_allen_ants.strict_manifest.",
                "Run scripts/run_mri_ccf_pipeline.py --activate to generate and lock active profile.",
            ]
        )
    if "annotationpath is required" in lower or "annotation path" in lower:
        suggestions.extend(
            [
                "Provide annotationPath pointing to annotation.npy/.npz.",
                f"Expected atlas shape: {ATLAS_SHAPE[0]}x{ATLAS_SHAPE[1]}x{ATLAS_SHAPE[2]}.",
                "If you only have NIfTI/MHA labels, run scripts/build_mri_ccf_hybrid_artifacts.py first.",
            ]
        )
    if "shape" in lower or "resample" in lower:
        suggestions.extend(
            [
                "Resample MRI annotation to planner atlas grid before retry.",
                f"Required shape: {ATLAS_SHAPE[0]}x{ATLAS_SHAPE[1]}x{ATLAS_SHAPE[2]} @ {VOXEL_SIZE_UM}um.",
                "Use scripts/validate_mri_ccf_inputs.py to confirm shape consistency.",
            ]
        )
    if "regionmap" in lower or "region map" in lower:
        suggestions.append("Provide region_map.json with acronym->id mapping for full catalog labels.")
    if "jacobian" in lower:
        suggestions.append("Provide jacobian.npy/.npz to enable quality gate evidence and uncertainty calibration.")
    if "skull" in lower or "outer" in lower:
        suggestions.append("Provide skull/outer tissue masks to enable structured risk distances for extracortical collision checks.")
    if not suggestions:
        suggestions.extend(
            [
                "Run scripts/validate_mri_ccf_inputs.py with annotation/stats/jacobian paths.",
                "Fix missing files or shape mismatch, then retry with returned payload.",
            ]
        )
    return {
        "code": "mri_artifact_auto_repair",
        "message": message,
        "suggestions": suggestions,
        "retryPayload": _suggest_mri_retry_payload(bundle),
        "validateCommand": "python scripts/validate_mri_ccf_inputs.py --annotation <annotation.npy> --expected-shape 528,320,456",
    }


def _new_mri_task(kind="reconstruct"):
    return {
        "taskId": f"mri_{kind}_{uuid.uuid4().hex[:10]}",
        "kind": str(kind),
        "state": TASK_STATE_QUEUED,
        "status": TASK_STATE_QUEUED,
        "stage": "queued",
        "progress": 0.0,
        "message": "queued",
        "createdAt": _utc_now_iso(),
        "updatedAt": _utc_now_iso(),
        "result": None,
        "error": None,
        "repairHints": None,
    }


def _update_mri_task(task_id, **patch):
    with mri_reconstruct_lock:
        task = mri_reconstruct_tasks.get(task_id)
        if not isinstance(task, dict):
            return None
        task.update(patch)
        status_raw = task.get("status", TASK_STATE_QUEUED)
        if "status" not in patch and "state" in patch:
            status_raw = task.get("state", status_raw)
        task["status"] = normalize_task_status(status_raw, TASK_STATE_QUEUED)
        task["state"] = task["status"]
        if "progress" in task:
            task["progress"] = _normalize_progress_01(task.get("progress", 0.0))
        if not task.get("stage"):
            task["stage"] = "queued"
        task["updatedAt"] = _utc_now_iso()
        mri_reconstruct_tasks[task_id] = task
        return deepcopy(task)


def _public_mri_task_snapshot(task):
    if not isinstance(task, dict):
        return task
    out = deepcopy(task)
    status = normalize_task_status(out.get("status", out.get("state")), TASK_STATE_QUEUED)
    out["status"] = status
    out["state"] = status
    if status not in (TASK_STATE_FAILED, TASK_STATE_PARTIAL):
        return out

    safe_message = "MRI reconstruction failed. Inspect backend logs using the response requestId."
    out["message"] = safe_message
    out["error"] = safe_message
    hints = out.get("repairHints") if isinstance(out.get("repairHints"), dict) else {}
    if hints:
        public_hints = {
            "code": hints.get("code") or "mri_artifact_auto_repair",
            "message": "MRI artifact validation failed.",
            "suggestions": list(hints.get("suggestions") or []),
            "validateCommand": hints.get("validateCommand"),
        }
        if isinstance(hints.get("retryPayload"), dict):
            public_hints["retryPayloadAvailable"] = True
        out["repairHints"] = public_hints
    return out


def _validate_mri_artifact_bundle(bundle):
    bundle = bundle if isinstance(bundle, dict) else {}
    active_profile_path = _resolve_existing_path(bundle.get("activeProfilePath"))
    if active_profile_path and not bundle.get("manifestPath"):
        active_profile = _safe_read_json_obj(active_profile_path)
        if isinstance(active_profile, dict):
            bundle["manifestPath"] = (
                active_profile.get("manifestPath")
                or ((active_profile.get("environment") or {}).get("PROBEPLANNER_MRI_CCF_MANIFEST_PATH"))
            )
    manifest_path = _resolve_existing_path(
        bundle.get("manifestPath")
        or bundle.get("artifactManifestPath")
        or bundle.get("manifest")
    )
    if not manifest_path and MRI_CCF_REQUIRE_MANIFEST:
        raise RuntimeError(
            "mri_ccf_hybrid mode requires artifact_manifest.json "
            "(set PROBEPLANNER_MRI_CCF_MANIFEST_PATH or pass manifestPath)."
        )
    if manifest_path:
        contract = _load_mri_manifest_contract(manifest_path)
        if not contract.get("manifestValid"):
            raise RuntimeError(
                f"Invalid MRI artifact manifest: {manifest_path} "
                f"({'; '.join(contract.get('errors') or ['manifest_parse_failed'])})"
            )
        artifacts = contract.get("artifacts") if isinstance(contract.get("artifacts"), dict) else {}
        if not bundle.get("annotationPath"):
            bundle["annotationPath"] = (artifacts.get("annotation") or {}).get("path")
        if not bundle.get("regionMapPath"):
            bundle["regionMapPath"] = (artifacts.get("region_map") or {}).get("path")
        if not bundle.get("regionMapV2Path"):
            bundle["regionMapV2Path"] = (artifacts.get("region_map_v2") or {}).get("path")
        if not bundle.get("statsPath"):
            bundle["statsPath"] = (artifacts.get("stats") or {}).get("path")
        if not bundle.get("registrationSummaryPath"):
            bundle["registrationSummaryPath"] = (artifacts.get("registration_summary") or {}).get("path")
        if not bundle.get("warpedMriPath"):
            bundle["warpedMriPath"] = (artifacts.get("warped_mri") or {}).get("path")
        if not bundle.get("jacobianPath"):
            bundle["jacobianPath"] = (artifacts.get("jacobian") or {}).get("path")
        if not bundle.get("skullMaskPath"):
            bundle["skullMaskPath"] = (artifacts.get("skull_mask") or {}).get("path")
        if not bundle.get("outerTissueMaskPath"):
            bundle["outerTissueMaskPath"] = (artifacts.get("outer_tissue_mask") or {}).get("path")
        if not bundle.get("layeredSdfPath"):
            bundle["layeredSdfPath"] = (artifacts.get("layered_sdf") or {}).get("path")

    annotation_path = _resolve_existing_path(bundle.get("annotationPath"))
    if not annotation_path:
        raise RuntimeError("annotationPath is required and must exist")
    arr = _load_annotation_array_from_path(
        annotation_path,
        mode_name="mri_ccf_hybrid",
        memmap_enabled=MRI_CCF_MEMMAP_ENABLED,
    )
    if tuple(arr.shape) != tuple(ATLAS_SHAPE):
        raise RuntimeError(
            f"MRI annotation shape {arr.shape} != expected {ATLAS_SHAPE}. "
            "Please resample to planner atlas grid first."
        )

    normalized = {
        "manifestPath": manifest_path,
        "activeProfilePath": active_profile_path,
        "annotationPath": annotation_path,
        "regionMapPath": _resolve_existing_path(bundle.get("regionMapPath")),
        "regionMapV2Path": _resolve_existing_path(bundle.get("regionMapV2Path")),
        "statsPath": _resolve_existing_path(bundle.get("statsPath")),
        "registrationSummaryPath": _resolve_existing_path(bundle.get("registrationSummaryPath")),
        "warpedMriPath": _resolve_existing_path(bundle.get("warpedMriPath")),
        "jacobianPath": _resolve_existing_path(bundle.get("jacobianPath")),
        "skullMaskPath": _resolve_existing_path(bundle.get("skullMaskPath")),
        "outerTissueMaskPath": _resolve_existing_path(bundle.get("outerTissueMaskPath")),
        "layeredSdfPath": _resolve_existing_path(bundle.get("layeredSdfPath")),
        "datasetProfile": str(bundle.get("datasetProfile") or "custom").strip() or "custom",
    }

    region_entries = 0
    region_map_for_count = normalized.get("regionMapV2Path") or normalized.get("regionMapPath")
    if region_map_for_count:
        region_map = _load_region_map_from_path(region_map_for_count)
        region_entries = int(len(region_map))

    if normalized["statsPath"]:
        _ = _load_json_from_path(normalized["statsPath"], warn_label="MRI hybrid stats JSON")
    if normalized["registrationSummaryPath"]:
        _ = _load_json_from_path(
            normalized["registrationSummaryPath"],
            warn_label="MRI registration_summary JSON",
        )
    if normalized["jacobianPath"]:
        _ = _compute_jacobian_anomaly_ratio_from_file(normalized["jacobianPath"])

    for mk in ("skullMaskPath", "outerTissueMaskPath"):
        mp = normalized.get(mk)
        if mp:
            mask = _safe_load_mask(Path(mp))
            if mask is None:
                raise RuntimeError(f"Cannot read structured risk mask: {mp}")
            if tuple(mask.shape) != tuple(ATLAS_SHAPE):
                raise RuntimeError(f"Mask shape mismatch for {mk}: {mask.shape} != {ATLAS_SHAPE}")

    if normalized["layeredSdfPath"]:
        sdf = _load_layered_sdf_mm(normalized["layeredSdfPath"])
        if sdf is None:
            raise RuntimeError("layeredSdfPath provided but cannot load SDF array")
        if tuple(sdf.shape) != tuple(ATLAS_SHAPE):
            raise RuntimeError(f"Layered SDF shape mismatch: {sdf.shape} != {ATLAS_SHAPE}")

    summary = {
        "manifestPath": manifest_path,
        "activeProfilePath": active_profile_path,
        "shape": list(arr.shape),
        "labelCount": int(len(_count_qiu_labels_chunked(arr, chunk_depth=64))),
        "regionMapEntries": int(region_entries),
        "regionMapV2Configured": bool(normalized["regionMapV2Path"]),
        "statsConfigured": bool(normalized["statsPath"]),
        "registrationSummaryConfigured": bool(normalized["registrationSummaryPath"]),
        "jacobianConfigured": bool(normalized["jacobianPath"]),
        "skullMaskConfigured": bool(normalized["skullMaskPath"]),
        "outerTissueMaskConfigured": bool(normalized["outerTissueMaskPath"]),
        "layeredSdfConfigured": bool(normalized["layeredSdfPath"]),
    }
    missing = _build_missing_mri_artifacts(normalized)
    summary["missingArtifacts"] = list(missing)
    manifest_contract = _load_mri_manifest_contract(manifest_path)
    consistency = _build_mri_profile_consistency(normalized, manifest=manifest_contract)
    summary["pathConsistency"] = consistency
    summary["contractViolations"] = []
    if not bool(consistency.get("passed", True)):
        summary["contractViolations"].append({
            "code": "profile_path_mismatch",
            "message": "manifest/stats/runtime artifact paths are inconsistent",
            "mismatches": deepcopy(consistency.get("mismatches") or []),
        })
    summary["artifactReady"] = bool(len(missing) == 0 and len(summary["contractViolations"]) == 0)
    if MRI_CCF_REQUIRE_MANIFEST and not manifest_path:
        summary["artifactReady"] = False
        if "manifest" not in summary["missingArtifacts"]:
            summary["missingArtifacts"] = ["manifest", *summary["missingArtifacts"]]
    if summary["missingArtifacts"]:
        raise RuntimeError(
            "MRI artifact contract incomplete: missing "
            + ", ".join(str(x) for x in summary["missingArtifacts"])
        )
    if summary["contractViolations"]:
        preview = "; ".join(
            f"{m.get('artifact')}:{m.get('kind')}"
            for row in summary["contractViolations"]
            for m in (row.get("mismatches") or [])[:4]
            if isinstance(m, dict)
        )
        raise RuntimeError(
            "MRI artifact contract path mismatch"
            + (f": {preview}" if preview else "")
        )
    return normalized, summary

API_TOKEN = os.getenv("PROBEPLANNER_API_TOKEN", "").strip()
AUTH_ENABLED = bool(API_TOKEN)
BENCHMARK_RUN_ENABLED = truthy_env("PROBEPLANNER_ENABLE_BENCHMARK_RUN", default=False)
BENCHMARK_ALLOW_EXTERNAL_PATHS = truthy_env("PROBEPLANNER_BENCHMARK_ALLOW_EXTERNAL_PATHS", default=False)
SERVE_SOURCE_MAPS = truthy_env("PROBEPLANNER_SERVE_SOURCE_MAPS", default=False)

CONSTRAINT_KEYS = (
    "maxAngle",
    "minEntrySpacing",
    "minClearance",
    "lambdaBoundary",
    "brainBounds",
    "hemisphere",
)
OPTIONAL_CONSTRAINT_KEYS = ("continuousRiskDistance",)
CONSTRAINT_MODES = ("enforce", "warn", "off")
CONSTRAINT_ALIASES = {"lambda": "lambdaBoundary"}

DEFAULT_CONSTRAINT_MODES = {
    "maxAngle": "enforce",
    "minEntrySpacing": "enforce",
    "minClearance": "enforce",
    "lambdaBoundary": "enforce",
    "brainBounds": "enforce",
    "continuousRiskDistance": "enforce",
    "hemisphere": "warn",
}

TARGETING_MODES = ("single_hemisphere", "bilateral_symmetric")

CLINICAL_SPECIES_LOCK = str(
    os.getenv("PROBEPLANNER_CLINICAL_SPECIES", "mouse")
).strip().lower() or "mouse"
CLINICAL_INTENDED_USE_LOCK = str(
    os.getenv("PROBEPLANNER_INTENDED_USE", "automatic_decision")
).strip().lower() or "automatic_decision"
CLINICAL_PRIMARY_ENDPOINTS = (
    "coverageRate",
    "continuousMinSafetyDistanceMm",
    "safetyPassProbability",
    "planningTimeMs",
    "expertAcceptanceRate",
)
CLINICAL_STUDY_ARMS = (
    "unspecified",
    "silent",
    "manual_only",
    "manual_plus_system",
)

DEFAULT_CORRECTION_FACTORS = {
    "ap": 1.0,
    "ml": 1.0,
    "dv": 1.0,
    "apShift": 0.0,
    "mlShift": 0.0,
    "dvShift": 0.0,
}

def _is_writable_directory(path_obj):
    path = Path(path_obj)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe_file = path / ".write_probe"
        with open(probe_file, "w", encoding="utf-8") as handle:
            handle.write("ok")
        probe_file.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _resolve_state_root():
    env_root = str(os.getenv("PROBEPLANNER_STATE_DIR", "")).strip()
    candidates = []
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates.extend(
        [
            Path.home() / ".probeplanner",
            Path(__file__).resolve().parent.parent / ".probeplanner",
            Path.cwd() / ".probeplanner",
        ]
    )
    for candidate in candidates:
        if _is_writable_directory(candidate):
            return candidate
    return Path.cwd()


STATE_ROOT = _resolve_state_root()

AUDIT_LOG_PATH = Path(
    os.getenv(
        "PROBEPLANNER_AUDIT_LOG",
        str(STATE_ROOT / "audit.log"),
    )
).expanduser()
REPLAY_DIR = Path(
    os.getenv(
        "PROBEPLANNER_REPLAY_DIR",
        str(STATE_ROOT / "replay"),
    )
).expanduser()
REPLAY_RETENTION = int(os.getenv("PROBEPLANNER_REPLAY_RETENTION", "1000"))
VERSION_FREEZE_FILE = Path(
    os.getenv(
        "PROBEPLANNER_VERSION_FREEZE_FILE",
        str(STATE_ROOT / "version_freeze.json"),
    )
).expanduser()
BENCHMARK_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "benchmark"
BENCHMARK_SCRIPT = BENCHMARK_SCRIPTS_DIR / "run_benchmark.py"
BENCHMARK_RELIABILITY_SCRIPT = BENCHMARK_SCRIPTS_DIR / "evaluate_reliability.py"
BENCHMARK_PAPER_SCRIPT = BENCHMARK_SCRIPTS_DIR / "export_paper_package.py"
BENCHMARK_GOLDEN_SCRIPT = BENCHMARK_SCRIPTS_DIR / "check_golden_replay.py"
BENCHMARK_SIGNATURE_SCRIPT = BENCHMARK_SCRIPTS_DIR / "export_version_signature.py"
BENCHMARK_CALIBRATION_SCRIPT = BENCHMARK_SCRIPTS_DIR / "calibrate_uncertainty.py"
BENCHMARK_EXTERNAL_VALIDATION_SCRIPT = BENCHMARK_SCRIPTS_DIR / "evaluate_external_validation.py"
CLINICAL_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "clinical"
CLINICAL_SILENT_SCRIPT = CLINICAL_SCRIPTS_DIR / "run_silent_study.py"
CLINICAL_INTERVENTIONAL_SCRIPT = CLINICAL_SCRIPTS_DIR / "analyze_interventional_trial.py"

BENCHMARK_DEFAULT_OUT_DIR = Path(
    os.getenv(
        "PROBEPLANNER_BENCHMARK_OUT_DIR",
        str(STATE_ROOT / "benchmark_out"),
    )
).expanduser()
BENCHMARK_ALLOWED_ROOTS = resolve_allowed_path_roots(
    "PROBEPLANNER_BENCHMARK_ALLOWED_ROOTS",
    [STATE_ROOT, REPLAY_DIR, BENCHMARK_DEFAULT_OUT_DIR],
)
BENCHMARK_DEFAULT_TEMPLATE_DIRNAME = "templates"
BENCHMARK_DEFAULT_EXPERT_BASENAME = "expert_baseline.csv"
BENCHMARK_DEFAULT_MANUAL_OVERRIDE_BASENAME = "manual_override.csv"
BENCHMARK_DEFAULT_TRIAL_BASENAME = "interventional_trial.csv"
BENCHMARK_DEFAULT_RESIDUAL_BASENAME = "uncertainty_residuals.csv"
BENCHMARK_DEFAULT_PARAM_TEMPLATE_BASENAME = "parameter_template.json"
SDF_CACHE_DIR = Path(
    os.getenv(
        "PROBEPLANNER_SDF_CACHE_DIR",
        str(STATE_ROOT / "sdf_cache"),
    )
).expanduser()
SDF_CACHE_ENABLED = str(
    os.getenv("PROBEPLANNER_SDF_CACHE_ENABLED", "true")
).strip().lower() in {"1", "true", "yes", "on"}
SDF_SYNC_BUILD_ENABLED = str(
    os.getenv("PROBEPLANNER_SDF_SYNC_BUILD_ENABLED", "false")
).strip().lower() in {"1", "true", "yes", "on"}
SDF_ASYNC_BUILD_ENABLED = str(
    os.getenv("PROBEPLANNER_SDF_ASYNC_BUILD_ENABLED", "true")
).strip().lower() in {"1", "true", "yes", "on"}
SDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
REGION_INDEX_CACHE_DIR = Path(
    os.getenv(
        "PROBEPLANNER_REGION_INDEX_CACHE_DIR",
        str(STATE_ROOT / "region_index_cache"),
    )
).expanduser()
REGION_INDEX_CACHE_ENABLED = str(
    os.getenv("PROBEPLANNER_REGION_INDEX_CACHE_ENABLED", "true")
).strip().lower() in {"1", "true", "yes", "on"}
REGION_INDEX_CACHE_CHUNK_DEPTH = int(
    os.getenv("PROBEPLANNER_REGION_INDEX_CHUNK_DEPTH", "16")
)
REGION_INDEX_CACHE_MIN_REGIONS = int(
    os.getenv("PROBEPLANNER_REGION_INDEX_MIN_REGIONS", "256")
)
REGION_INDEX_CACHE_DIR.mkdir(parents=True, exist_ok=True)

benchmark_last_replay_dir = REPLAY_DIR
benchmark_last_out_dir = BENCHMARK_DEFAULT_OUT_DIR
benchmark_last_expert_csv = None
benchmark_last_manual_override_csv = None
benchmark_last_trial_csv = None
benchmark_last_residual_csv = None
benchmark_last_param_template = None
benchmark_last_manifest = {}
benchmark_lock = threading.Lock()

#        Global atlas state                                                                                                                   
atlas_annotation = None
atlas_loaded      = False
atlas_error       = None
descendant_ids    = {}
structure_map     = {}
atlas_tree_ref    = None
acronym_record_map = {}
atlas_lock        = threading.Lock()
atlas_loader_lock = threading.Lock()
atlas_loader_thread = None
atlas_load_state  = {
    "state": "idle",
    "phase": "idle",
    "message": "",
    "progress": 0.0,
    "step": 0,
    "totalSteps": 8,
    "startedAt": None,
    "updatedAt": None,
    "finishedAt": None,
}
risk_model_lock   = threading.Lock()
risk_surface_tree = None
risk_surface_points_um = None
risk_surface_source_trees = {}
risk_surface_source_points_um = {}
risk_surface_class_trees = {}
risk_surface_class_points_um = {}
risk_class_volume = None
risk_signed_sdf_mm = None
risk_layered_sdf_channels_mm = {}
risk_sdf_build_thread = None
risk_sdf_build_token = 0
risk_source_mask_map = {}
reconstruction_quality_state = {
    "gateEnabled": QUALITY_GATE_ENABLED,
    "metrics": {},
    "thresholds": {
        "diceMin": QUALITY_GATE_DICE_MIN,
        "hd95MaxMm": QUALITY_GATE_HD95_MAX_MM,
        "treMaxMm": QUALITY_GATE_TRE_MAX_MM,
        "jacobianAnomalyMax": QUALITY_GATE_JAC_ANOMALY_MAX,
    },
    "passed": True,
    "blockingReasons": [],
    "source": "unavailable",
    "updatedAt": None,
}
dynamic_uncertainty_profile = {
    "source": "default",
    "residualMm": None,
    "jacobianStd": None,
    "entrySigmaMm": None,
    "angleSigmaDeg": None,
    "depthSigmaMm": None,
    "riskDistanceSigmaMm": None,
}
planner_calibration_profile = {
    "source": "default",
    "loaded": False,
    "path": None,
    "objectiveWeights": {},
    "riskThresholdsMm": {},
    "uncertaintyCalibration": {},
    "meta": {},
}
risk_model_summary = {
    "enabled": False,
    "source": [],
    "voxelCount": 0,
    "surfacePointCount": 0,
    "sdfBuildState": "idle",
}
mri_mode_runtime_config = {}
mri_mode_runtime_lock = threading.Lock()
mri_reconstruct_tasks = {}
mri_reconstruct_lock = threading.Lock()
registration_runtime_state = {
    "status": TASK_STATE_QUEUED,
    "stage": "idle",
    "progress": 0.0,
    "message": "queued",
    "updatedAt": None,
    "lastResult": None,
    "lastError": None,
}
planning_runtime_state = {
    "status": TASK_STATE_QUEUED,
    "stage": "idle",
    "progress": 0.0,
    "message": "queued",
    "updatedAt": None,
    "taskId": None,
    "lastResultSummary": None,
    "lastError": None,
}
workflow_state_lock = threading.Lock()
precomputed_layered_sdf_mm = None
candidate_objective_cache = {}
candidate_cache_lock = threading.Lock()
contact_profile_cache = {}
contact_profile_cache_lock = threading.Lock()
CONTACT_PROFILE_CACHE_MAX = int(
    os.getenv("PROBEPLANNER_CONTACT_PROFILE_CACHE_MAX", "4096")
)
TRAJECTORY_VOXEL_CACHE_MAX = int(
    os.getenv("PROBEPLANNER_TRAJECTORY_VOXEL_CACHE_MAX", "120000")
)
BRAIN_BOUNDS_OUTSIDE_GAP_UM = float(
    os.getenv("PROBEPLANNER_BRAIN_BOUNDS_OUTSIDE_GAP_UM", "300")
)
RISK_DIAG_CACHE_MAX = int(
    os.getenv("PROBEPLANNER_RISK_DIAG_CACHE_MAX", "80000")
)
ROBUST_PERTURB_CACHE_MAX = int(
    os.getenv("PROBEPLANNER_ROBUST_PERTURB_CACHE_MAX", "100000")
)
trajectory_voxel_cache = {}
trajectory_voxel_cache_lock = threading.Lock()
risk_diag_cache = {}
risk_diag_cache_lock = threading.Lock()
robust_perturb_cache = {}
robust_perturb_cache_lock = threading.Lock()


def _atlas_loader_alive():
    thread_obj = atlas_loader_thread
    return bool(thread_obj and thread_obj.is_alive())

RISK_NAME_HINTS = {
    "ventricle": ("ventricle", "ventricular", "vent"),
    "sulcus": ("sulcus", "sulci", "fissure"),
    "vessel": ("vessel", "vasculature", "artery", "vein"),
    "skull": ("skull", "calvaria", "bone"),
    "cortex_outside": ("cortex_outside", "outer_tissue", "extracortical"),
    "brain_surface": ("brain_surface", "cortical_surface", "pial", "brain boundary"),
    "drilling_surface": ("drilling_surface", "skull_entry", "entry_layer", "drill"),
}

RISK_MASK_ENV = {
    "ventricle": "PROBEPLANNER_RISK_MASK_VENTRICLE",
    "sulcus": "PROBEPLANNER_RISK_MASK_SULCUS",
    "vessel": "PROBEPLANNER_RISK_MASK_VESSEL",
    "skull": "PROBEPLANNER_RISK_MASK_SKULL",
    "cortex_outside": "PROBEPLANNER_RISK_MASK_CORTEX_OUTSIDE",
    "brain_surface": "PROBEPLANNER_RISK_MASK_BRAIN_SURFACE",
    "drilling_surface": "PROBEPLANNER_RISK_MASK_DRILLING_SURFACE",
}
RISK_MASK_FALLBACK_DIR = Path(
    os.getenv(
        "PROBEPLANNER_RISK_MASK_DIR",
        str(STATE_ROOT / "risk_masks"),
    )
).expanduser()
RISK_MASK_PREFER_STRUCTURED = str(
    os.getenv("PROBEPLANNER_RISK_MASK_PREFER_STRUCTURED", "true")
).strip().lower() in {"1", "true", "yes", "on"}
DISABLE_ATLAS_KEYWORD_RISK = str(
    os.getenv("PROBEPLANNER_DISABLE_ATLAS_KEYWORD_RISK", "false")
).strip().lower() in {"1", "true", "yes", "on"}
KEYWORD_MASK_CHUNK_DEPTH = max(
    4,
    int(os.getenv("PROBEPLANNER_KEYWORD_MASK_CHUNK_DEPTH", "16") or 16),
)
RISK_CLASS_CODE = {
    "vessel": 1,
    "ventricle": 2,
    "sulcus": 3,
    "skull": 4,
    "cortex_outside": 5,
    "brain_surface": 6,
    "drilling_surface": 7,
}
RISK_CODE_CLASS = {v: k for k, v in RISK_CLASS_CODE.items()}
RISK_CLASS_PRIORITY = {
    "vessel": 80,
    "ventricle": 70,
    "skull": 60,
    "cortex_outside": 50,
    "drilling_surface": 45,
    "sulcus": 40,
    "brain_surface": 20,
}
RISK_LAYER_REQUIRED_SDF_CHANNELS = (
    "skull_sdf_mm",
    "outer_tissue_sdf_mm",
    "ventricle_sdf_mm",
    "sulcus_sdf_mm",
    "vessel_sdf_mm",
    "cortex_outside_sdf_mm",
    "brain_surface_sdf_mm",
    "drilling_surface_sdf_mm",
)
RISK_CLASS_PRIMARY_SDF_CHANNEL = {
    "vessel": "vessel_sdf_mm",
    "ventricle": "ventricle_sdf_mm",
    "sulcus": "sulcus_sdf_mm",
    "skull": "skull_sdf_mm",
    "cortex_outside": "outer_tissue_sdf_mm",
    "brain_surface": "brain_surface_sdf_mm",
    "drilling_surface": "drilling_surface_sdf_mm",
}
RISK_CLASS_OUTPUT_DISTANCE_FIELD = {
    "vessel": "minDistanceToVessel",
    "ventricle": "minDistanceToVentricle",
    "sulcus": "minDistanceToSulcus",
    "skull": "minDistanceToSkull",
    "cortex_outside": "minDistanceToOuterTissue",
    "brain_surface": "minDistanceToBrainSurface",
    "drilling_surface": "minDistanceToDrillingSurface",
}
RISK_CLASS_DATA_REQUIREMENT = {
    "vessel": "MRA/vascular mask or PROBEPLANNER_RISK_MASK_VESSEL",
    "sulcus": "sulcus proxy mask or PROBEPLANNER_RISK_MASK_SULCUS",
    "ventricle": "ventricle mask, layered SDF, or atlas keyword ventricle bucket",
    "skull": "MRI skull_mask",
    "cortex_outside": "MRI outer_tissue_mask/cortex outside mask",
    "brain_surface": "brain surface SDF/mask",
    "drilling_surface": "drilling/skull entry surface SDF/mask",
}
ALLOW_KEYWORD_ONLY_RISK_ENFORCE = str(
    os.getenv("PROBEPLANNER_ALLOW_KEYWORD_RISK_ENFORCE", "")
).strip().lower() in ("1", "true", "yes", "on")
RISK_DIAG_STEP_UM = 25.0

RISK_THRESHOLD_TEMPLATES = {
    "conservative": {
        "default": 0.42,
        "vessel": 0.80,
        "ventricle": 0.44,
        "sulcus": 0.50,
        "skull": 0.58,
        "cortex_outside": 0.05,
        "brain_surface": 0.05,
        "drilling_surface": 0.05,
    },
    "balanced": {
        "default": 0.35,
        "vessel": 0.60,
        "ventricle": 0.35,
        "sulcus": 0.40,
        "skull": 0.45,
        "cortex_outside": 0.05,
        "brain_surface": 0.05,
        "drilling_surface": 0.05,
    },
    "aggressive": {
        "default": 0.24,
        "vessel": 0.50,
        "ventricle": 0.24,
        "sulcus": 0.20,
        "skull": 0.30,
        "cortex_outside": 0.02,
        "brain_surface": 0.02,
        "drilling_surface": 0.02,
    },
}
RISK_THRESHOLD_TEMPLATE_DEFAULT = "balanced"
BOUNDARY_RISK_THRESHOLD_FLOORS_MM = {
    "default": 0.01,
    "vessel": 0.01,
    "ventricle": 0.01,
    "sulcus": 0.01,
    "skull": 0.01,
    "cortex_outside": 0.05,
    "brain_surface": 0.05,
    "drilling_surface": 0.05,
}
ENTRY_CORRIDOR_RISK_CLASSES = {
    "skull",
    "cortex_outside",
    "brain_surface",
    "drilling_surface",
}
INTERNAL_AVOIDANCE_RISK_CLASSES = {
    "vessel",
    "ventricle",
    "sulcus",
    "user_exclusion",
    "user_defined_exclusion",
    "no_go",
}
ENTRY_CORRIDOR_ALLOWANCE_MM = float(os.getenv("PROBEPLANNER_ENTRY_CORRIDOR_ALLOWANCE_MM", "0.90"))
AUTO_AVOID_ENABLED_DEFAULT = True
AUTO_AVOID_COARSE_STEP_UM_DEFAULT = 50.0
AUTO_AVOID_SAFETY_BUFFER_MM_DEFAULT = 0.05
AUTO_AVOID_NEAR_MARGIN_MM_DEFAULT = 0.08
CONSTRAINT_AWARE_SEARCH_DEFAULT = True
AUTO_REPAIR_ENABLED_DEFAULT = True
AUTO_REPAIR_TOPK_DEFAULT = 40
AUTO_REPAIR_ENTRY_STEP_MM_DEFAULT = 0.15
AUTO_REPAIR_ENTRY_MAX_SHIFT_MM_DEFAULT = 0.9
AUTO_REPAIR_ANGLE_STEP_DEG_DEFAULT = 3.0
AUTO_REPAIR_ANGLE_MAX_ADJUST_DEG_DEFAULT = 12.0
AUTO_REPAIR_DEPTH_STEP_MM_DEFAULT = 0.25
AUTO_REPAIR_DEPTH_MAX_ADJUST_MM_DEFAULT = 1.2
AUTO_REPAIR_MAX_VARIANTS_DEFAULT = 800
STRICT_FALLBACK_DECOMPOSE_DEFAULT = True
OBJECTIVE_RISK_PRIMARY_DEFAULT = True
OBJECTIVE_RISK_PRIMARY_BOOST_DEFAULT = 0.20
OBJECTIVE_RISK_SIGMA_MM_DEFAULT = 0.04
SDF_RISK_PATH_WEIGHT_DEFAULT = 1.0
SDF_RISK_MARGIN_WEIGHT_DEFAULT = 1.0
COMPUTE_WORKERS_DEFAULT = max(
    1,
    int(
        os.getenv(
            "PROBEPLANNER_COMPUTE_WORKERS",
            str(max(1, min(8, (os.cpu_count() or 4) - 1))),
        )
    ),
)
COMPUTE_PREFILTER_WORKERS_DEFAULT = max(
    1,
    int(
        os.getenv(
            "PROBEPLANNER_PREFILTER_WORKERS",
            str(max(1, min(6, (os.cpu_count() or 4) - 1))),
        )
    ),
)
COMPUTE_CHUNK_SIZE_DEFAULT = max(
    4,
    int(os.getenv("PROBEPLANNER_COMPUTE_CHUNK_SIZE", "64") or 64),
)
CANDIDATE_GEN_WORKERS_DEFAULT = max(
    1,
    int(
        os.getenv(
            "PROBEPLANNER_CANDIDATE_GEN_WORKERS",
            str(max(1, min(8, (os.cpu_count() or 4) - 1))),
        )
    ),
)
CANDIDATE_COARSE_STEP_UM_DEFAULT = float(
    os.getenv("PROBEPLANNER_CANDIDATE_COARSE_STEP_UM", "75.0") or 75.0
)
CANDIDATE_REFINE_STEP_UM_DEFAULT = float(
    os.getenv("PROBEPLANNER_CANDIDATE_REFINE_STEP_UM", "25.0") or 25.0
)
CANDIDATE_REFINE_FRACTION_DEFAULT = float(
    os.getenv("PROBEPLANNER_CANDIDATE_REFINE_FRACTION", "0.20") or 0.20
)
CANDIDATE_TARGET_DIVERSITY_SEEDS_DEFAULT = max(
    1,
    int(os.getenv("PROBEPLANNER_CANDIDATE_TARGET_DIVERSITY_SEEDS", "8") or 8),
)
CANDIDATE_TARGET_ENTRY_VARIANTS_DEFAULT = max(
    1,
    int(os.getenv("PROBEPLANNER_CANDIDATE_TARGET_ENTRY_VARIANTS", "3") or 3),
)
SCORE_EARLY_STOP_ENABLED_DEFAULT = str(
    os.getenv("PROBEPLANNER_SCORE_EARLY_STOP_ENABLED", "true")
).strip().lower() in ("1", "true", "yes", "on")
SCORE_EARLY_STOP_TOPK_DEFAULT = int(
    os.getenv("PROBEPLANNER_SCORE_EARLY_STOP_TOPK", "120") or 120
)
SCORE_EARLY_STOP_MARGIN_DEFAULT = float(
    os.getenv("PROBEPLANNER_SCORE_EARLY_STOP_MARGIN", "0.03") or 0.03
)
STREAMING_PIPELINE_ENABLED_DEFAULT = str(
    os.getenv("PROBEPLANNER_STREAMING_PIPELINE_ENABLED", "true")
).strip().lower() in ("1", "true", "yes", "on")
STREAMING_SCORE_BATCH_SIZE_DEFAULT = int(
    os.getenv("PROBEPLANNER_STREAMING_SCORE_BATCH_SIZE", "96") or 96
)
STREAMING_QUEUE_MAXSIZE_DEFAULT = int(
    os.getenv("PROBEPLANNER_STREAMING_QUEUE_MAXSIZE", "16") or 16
)
STREAMING_SCORE_WORKERS_MAX_DEFAULT = int(
    os.getenv("PROBEPLANNER_STREAMING_SCORE_WORKERS_MAX", "2") or 2
)
STREAMING_ALLOW_OVERSUBSCRIBE_DEFAULT = str(
    os.getenv("PROBEPLANNER_STREAMING_ALLOW_OVERSUBSCRIBE", "false")
).strip().lower() in ("1", "true", "yes", "on")
DRCC_ADAPTIVE_BUDGET_DEFAULT = str(
    os.getenv("PROBEPLANNER_DRCC_ADAPTIVE_BUDGET", "true")
).strip().lower() in ("1", "true", "yes", "on")
RUNTIME_NUM_THREADS_DEFAULT = max(
    1,
    int(
        os.getenv(
            "PROBEPLANNER_RUNTIME_THREADS",
            str(max(1, min(16, os.cpu_count() or 8))),
        )
    ),
)
SDF_GPU_ENABLED_DEFAULT = str(
    os.getenv("PROBEPLANNER_SDF_GPU_ENABLED", "false")
).strip().lower() in ("1", "true", "yes", "on")
# Keep math libraries in a stable thread budget to reduce oversubscription jitter.
os.environ.setdefault("OMP_NUM_THREADS", str(RUNTIME_NUM_THREADS_DEFAULT))
os.environ.setdefault("MKL_NUM_THREADS", str(RUNTIME_NUM_THREADS_DEFAULT))
os.environ.setdefault("OPENBLAS_NUM_THREADS", str(RUNTIME_NUM_THREADS_DEFAULT))
os.environ.setdefault("NUMEXPR_MAX_THREADS", str(RUNTIME_NUM_THREADS_DEFAULT))
WORKFLOW_STATUS_VALUES = (
    TASK_STATE_QUEUED,
    TASK_STATE_RUNNING,
    TASK_STATE_PARTIAL,
    TASK_STATE_FAILED,
    TASK_STATE_COMPLETED,
)
WORKFLOW_STAGE_ORDER = ("reconstruction", "registration", "risk", "planning")

audit_lock        = threading.Lock()
replay_lock       = threading.Lock()

replay_index      = {}
task_registry     = TaskRegistry()


def _set_atlas_load_state(
    *,
    state=None,
    phase=None,
    message=None,
    progress=None,
    step=None,
    total_steps=None,
    started=False,
    finished=False,
):
    now_iso = _utc_now_iso()
    with atlas_lock:
        if started:
            atlas_load_state["startedAt"] = now_iso
            atlas_load_state["finishedAt"] = None
        if finished:
            atlas_load_state["finishedAt"] = now_iso
        if state is not None:
            atlas_load_state["state"] = str(state)
        if phase is not None:
            atlas_load_state["phase"] = str(phase)
        if message is not None:
            atlas_load_state["message"] = str(message)
        if total_steps is not None:
            try:
                atlas_load_state["totalSteps"] = int(total_steps)
            except Exception:
                pass
        if step is not None:
            try:
                atlas_load_state["step"] = int(step)
            except Exception:
                pass
        if progress is not None:
            try:
                atlas_load_state["progress"] = float(progress)
            except Exception:
                pass
        atlas_load_state["updatedAt"] = now_iso

AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
REPLAY_DIR.mkdir(parents=True, exist_ok=True)
VERSION_FREEZE_FILE.parent.mkdir(parents=True, exist_ok=True)

version_freeze_state = {
    "frozen": False,
    "engineVersion": ENGINE_VERSION,
    "atlasHash": None,
    "frozenAt": None,
    "frozenBy": None,
    "note": "",
}

atlas_signature = {
    "atlasMode": ACTIVE_ATLAS_MODE,
    "atlasSource": (
        "annotation/ccf_2017"
        if ACTIVE_ATLAS_MODE == "allen_sdk"
        else ("mri_ccf_hybrid_local" if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid" else "qiu2018_local")
    ),
    "voxelSizeUm": VOXEL_SIZE_UM,
    "shape": list(ATLAS_SHAPE),
    "hash": None,
}


def _safe_pkg_version(package_name):
    try:
        return str(importlib_metadata.version(package_name))
    except Exception:
        return None


def _detect_blas_backend():
    try:
        config_str = []
        np_config = getattr(np, "__config__", None)
        if np_config is not None:
            for key in ("blas_opt_info", "openblas_info", "blas_ilp64_opt_info", "lapack_opt_info"):
                try:
                    info = np_config.get_info(key)
                except Exception:
                    info = None
                if info:
                    config_str.append(str(info))
        joined = " ".join(config_str).lower()
        if "mkl" in joined:
            return "mkl"
        if "openblas" in joined:
            return "openblas"
        if "blis" in joined:
            return "blis"
        if "accelerate" in joined:
            return "accelerate"
    except Exception:
        pass
    return "unknown"


def _collect_environment_signature():
    rng_state = np.random.get_state() if hasattr(np.random, "get_state") else None
    numpy_seed_hint = None
    if isinstance(rng_state, tuple) and len(rng_state) >= 2:
        try:
            numpy_seed_hint = int(rng_state[1][0])
        except Exception:
            numpy_seed_hint = None
    payload = {
        "pythonVersion": platform.python_version(),
        "pythonImplementation": platform.python_implementation(),
        "pythonExecutable": sys.executable,
        "platform": platform.platform(),
        "osName": os.name,
        "osRelease": platform.release(),
        "osVersion": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "numpyVersion": str(np.__version__),
        "flaskVersion": _safe_pkg_version("flask"),
        "requestsVersion": _safe_pkg_version("requests"),
        "scipyVersion": _safe_pkg_version("scipy"),
        "allensdkVersion": _safe_pkg_version("allensdk"),
        "cvxpyVersion": _safe_pkg_version("cvxpy"),
        "blasBackend": _detect_blas_backend(),
        "blasThreadEnv": {
            "OMP_NUM_THREADS": os.getenv("OMP_NUM_THREADS"),
            "OPENBLAS_NUM_THREADS": os.getenv("OPENBLAS_NUM_THREADS"),
            "MKL_NUM_THREADS": os.getenv("MKL_NUM_THREADS"),
            "NUMEXPR_MAX_THREADS": os.getenv("NUMEXPR_MAX_THREADS"),
        },
        "condaDefaultEnv": os.getenv("CONDA_DEFAULT_ENV"),
        "randomSeedPolicy": {
            "pythonHashSeed": os.getenv("PYTHONHASHSEED"),
            "numpySeedHint": numpy_seed_hint,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    payload["signatureHash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


environment_signature = _collect_environment_signature()


def _clinical_contract_snapshot():
    return {
        "species": CLINICAL_SPECIES_LOCK,
        "intendedUse": CLINICAL_INTENDED_USE_LOCK,
        "decisionClass": "automatic_decision",
        "atlasSpecies": "mouse",
        "primaryEndpoints": list(CLINICAL_PRIMARY_ENDPOINTS),
        "note": (
            "Clinical translation contract is locked to mouse atlas workflow; "
            "human brain use is out of scope for this build."
        ),
    }

#        Brain regions                                                                                                                             
PLANNER_REGIONS = {
    "VISp":  {"id": 385,  "ap": -2.10, "dv": 2.30, "ml": -0.90, "category": "visual"},
    "VISl":  {"id": 394,  "ap": -2.30, "dv": 2.50, "ml": -1.60, "category": "visual"},
    "VISpm": {"id": 533,  "ap": -2.80, "dv": 2.00, "ml": -0.40, "category": "visual"},
    "VISal": {"id": 402,  "ap": -1.50, "dv": 2.10, "ml": -1.60, "category": "visual"},
    "VISrl": {"id": 417,  "ap": -1.90, "dv": 2.20, "ml": -1.10, "category": "visual"},
    "LGd":   {"id": 170,  "ap": -1.80, "dv": 4.00, "ml": -1.60, "category": "visual"},
    "SCsg":  {"id": 834,  "ap": -3.30, "dv": 4.00, "ml": -0.40, "category": "midbrain"},
    "SCm":   {"id": 294,  "ap": -3.50, "dv": 4.50, "ml": -0.40, "category": "midbrain"},
    "MRN":   {"id": 128,  "ap": -3.40, "dv": 4.80, "ml": -0.40, "category": "midbrain"},
    "SNr":   {"id": 1036, "ap": -3.20, "dv": 4.80, "ml": -1.60, "category": "midbrain"},
    "CA1":   {"id": 382,  "ap": -1.80, "dv": 3.80, "ml": -1.20, "category": "hippocampus"},
    "CA3":   {"id": 463,  "ap": -1.60, "dv": 3.60, "ml": -1.40, "category": "hippocampus"},
    "DG":    {"id": 726,  "ap": -2.00, "dv": 4.20, "ml": -1.10, "category": "hippocampus"},
    "ENTl":  {"id": 28,   "ap": -3.50, "dv": 4.50, "ml": -2.00, "category": "hippocampus"},
    "RSP":   {"id": 254,  "ap": -2.50, "dv": 2.60, "ml": -0.50, "category": "hippocampus"},
    "LP":    {"id": 218,  "ap": -1.50, "dv": 3.80, "ml": -1.30, "category": "thalamus"},
    "VPM":   {"id": 733,  "ap": -0.80, "dv": 4.20, "ml": -1.50, "category": "thalamus"},
    "VPL":   {"id": 718,  "ap": -0.90, "dv": 4.00, "ml": -1.90, "category": "thalamus"},
    "MD":    {"id": 362,  "ap": -0.20, "dv": 3.70, "ml": -0.40, "category": "thalamus"},
    "PO":    {"id": 1020, "ap": -1.80, "dv": 3.90, "ml": -1.40, "category": "thalamus"},
    "MOp":   {"id": 985,  "ap":  0.50, "dv": 1.70, "ml": -1.20, "category": "frontal"},
    "MOs":   {"id": 993,  "ap":  0.70, "dv": 1.50, "ml": -1.10, "category": "frontal"},
    "SSp":   {"id": 322,  "ap": -0.20, "dv": 2.00, "ml": -1.50, "category": "frontal"},
    "PFC":   {"id": 184,  "ap":  1.70, "dv": 2.00, "ml": -0.40, "category": "frontal"},
    "ACA":   {"id": 31,   "ap":  1.50, "dv": 1.60, "ml": -0.20, "category": "frontal"},
    "PL":    {"id": 667,  "ap":  1.20, "dv": 2.40, "ml": -0.40, "category": "frontal"},
    "ILA":   {"id": 44,   "ap":  1.80, "dv": 2.70, "ml": -0.40, "category": "frontal"},
    "CP":    {"id": 672,  "ap":  0.30, "dv": 3.20, "ml": -1.80, "category": "basal_ganglia"},
    "ACB":   {"id": 56,   "ap":  0.90, "dv": 3.60, "ml": -0.60, "category": "basal_ganglia"},
    "GPe":   {"id": 1022, "ap": -0.20, "dv": 4.00, "ml": -1.60, "category": "basal_ganglia"},
    "GPi":   {"id": 1031, "ap": -0.50, "dv": 4.20, "ml": -1.80, "category": "basal_ganglia"},
}
BASE_PLANNER_REGIONS = deepcopy(PLANNER_REGIONS)

_env_true = {"1", "true", "yes", "on"}
DYNAMIC_REGION_PROVISION_ENABLED = str(
    os.getenv("PROBEPLANNER_DYNAMIC_REGION_PROVISION", "true")
).strip().lower() in _env_true
AUTO_PRELOAD_ALL_REGIONS = str(
    os.getenv("PROBEPLANNER_PRELOAD_ALL_REGIONS", "false")
).strip().lower() in _env_true
STARTUP_FULL_REGION_PLANNING = str(
    os.getenv("PROBEPLANNER_STARTUP_FULL_REGION_PLANNING", "true")
).strip().lower() in _env_true
LABEL_DRIVEN_USE_ALLEN_ONTOLOGY = str(
    os.getenv("PROBEPLANNER_LABEL_DRIVEN_USE_ALLEN_ONTOLOGY", "true")
).strip().lower() in _env_true
AUTO_PRELOAD_REGION_KEYS = [
    item.strip()
    for item in str(os.getenv("PROBEPLANNER_PRELOAD_REGION_KEYS", "")).replace(";", ",").split(",")
    if item.strip()
]

REGION_CENTROIDS_UM = {
    reg["id"]: (
        BREGMA_UM[0] - reg["ap"] * 1000,
        BREGMA_UM[1] + reg["dv"] * 1000,
        BREGMA_UM[2] + reg["ml"] * 1000,
    )
    for reg in PLANNER_REGIONS.values()
}
REGION_INDEX_CENTROID_POLICY = "canonical_left_v1"


def _rebuild_region_centroids():
    global REGION_CENTROIDS_UM
    REGION_CENTROIDS_UM = {
        reg["id"]: (
            BREGMA_UM[0] - reg["ap"] * 1000,
            BREGMA_UM[1] + reg["dv"] * 1000,
            BREGMA_UM[2] + reg["ml"] * 1000,
        )
        for reg in PLANNER_REGIONS.values()
    }


def _reset_planner_regions():
    global PLANNER_REGIONS
    PLANNER_REGIONS = deepcopy(BASE_PLANNER_REGIONS)
    _rebuild_region_centroids()


def _region_index_cache_path(atlas_mode, atlas_hash):
    mode = str(atlas_mode or "allen_sdk").strip().lower() or "allen_sdk"
    digest = str(atlas_hash or "unknown").strip().lower() or "unknown"
    return REGION_INDEX_CACHE_DIR / f"{mode}_{digest[:20]}_region_index.json"


def _load_region_index_cache(atlas_mode, atlas_hash):
    if not REGION_INDEX_CACHE_ENABLED:
        return None
    cache_path = _region_index_cache_path(atlas_mode, atlas_hash)
    if not cache_path.exists():
        return None
    try:
        with cache_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return None
        if str(payload.get("atlasHash") or "") != str(atlas_hash or ""):
            return None
        if str(payload.get("atlasMode") or "") != str(atlas_mode or ""):
            return None
        if str(payload.get("centroidPolicy") or "") != REGION_INDEX_CENTROID_POLICY:
            log.info(f"Ignoring region index cache with stale centroid policy: {cache_path}")
            return None
        planner_seed = payload.get("plannerSeed")
        structure_seed = payload.get("structureMap")
        descendant_seed = payload.get("descendantIds")
        if not isinstance(planner_seed, dict) or not isinstance(structure_seed, dict) or not isinstance(descendant_seed, dict):
            return None
        if str(atlas_mode or "").strip().lower() == "allen_sdk":
            min_expected = max(1, int(REGION_INDEX_CACHE_MIN_REGIONS))
            if len(planner_seed) < min_expected:
                log.warning(
                    f"Ignoring stale region index cache ({cache_path}): "
                    f"plannerSeedCount={len(planner_seed)} < minExpected={min_expected}"
                )
                return None
        return {
            "plannerSeed": planner_seed,
            "structureMap": structure_seed,
            "descendantIds": descendant_seed,
            "cachePath": str(cache_path),
            "cacheHit": True,
        }
    except Exception as exc:
        log.warning(f"Failed to read region index cache ({cache_path}): {exc}")
        return None


def _save_region_index_cache(atlas_mode, atlas_hash, planner_seed, structure_seed, descendant_seed):
    if not REGION_INDEX_CACHE_ENABLED:
        return None
    cache_path = _region_index_cache_path(atlas_mode, atlas_hash)
    payload = {
        "atlasMode": str(atlas_mode or ""),
        "atlasHash": str(atlas_hash or ""),
        "voxelSizeUm": VOXEL_SIZE_UM,
        "atlasShape": list(ATLAS_SHAPE),
        "centroidPolicy": REGION_INDEX_CENTROID_POLICY,
        "plannerSeed": planner_seed,
        "structureMap": structure_seed,
        "descendantIds": descendant_seed,
        "createdAt": _utc_now_iso(),
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"), sort_keys=True)
        tmp_path.replace(cache_path)
        return str(cache_path)
    except Exception as exc:
        log.warning(f"Failed to write region index cache ({cache_path}): {exc}")
        return None

#        Coordinate helpers                                                                                                                   

def mm_to_voxel(ap_mm_anterior, dv_mm, ml_mm):
    ap_um = BREGMA_UM[0] - ap_mm_anterior * 1000
    dv_um = BREGMA_UM[1] + dv_mm * 1000
    ml_um = BREGMA_UM[2] + ml_mm * 1000
    i = int(round(ap_um / VOXEL_SIZE_UM))
    j = int(round(dv_um / VOXEL_SIZE_UM))
    k = int(round(ml_um / VOXEL_SIZE_UM))
    return i, j, k


def voxel_in_bounds(i, j, k):
    return (0 <= i < ATLAS_SHAPE[0] and
            0 <= j < ATLAS_SHAPE[1] and
            0 <= k < ATLAS_SHAPE[2])


def _compute_atlas_hash(annotation, descendants_map):
    digest = hashlib.sha256()
    digest.update(str(annotation.shape).encode("utf-8"))
    digest.update(str(annotation.dtype).encode("utf-8"))
    digest.update(str(VOXEL_SIZE_UM).encode("utf-8"))

    # Lightweight, deterministic sample to avoid hashing all 110MB each startup.
    x = min(24, annotation.shape[0])
    y = min(24, annotation.shape[1])
    z = min(24, annotation.shape[2])
    digest.update(annotation[:x, :y, :z].tobytes(order="C"))

    for sid in sorted(descendants_map.keys()):
        digest.update(f"{sid}:".encode("utf-8"))
        sample = sorted(list(descendants_map[sid]))[:64]
        digest.update(",".join(str(v) for v in sample).encode("utf-8"))

    return digest.hexdigest()


def _safe_load_mask(mask_path):
    path = Path(mask_path)
    if not path.exists():
        return None
    try:
        if path.suffix.lower() == ".npz":
            data = np.load(path)
            if "mask" in data:
                arr = data["mask"]
            else:
                key = list(data.keys())[0]
                arr = data[key]
        else:
            arr = np.load(path)
        arr = np.asarray(arr).astype(bool)
        if arr.shape != ATLAS_SHAPE:
            log.warning(f"Risk mask ignored ({path}): shape {arr.shape} != {ATLAS_SHAPE}")
            return None
        return arr
    except Exception as exc:
        log.warning(f"Failed to load risk mask {path}: {exc}")
        return None


def _resolve_risk_mask_path(source, env_key):
    env_path = str(os.getenv(env_key, "")).strip()
    if env_path:
        return env_path, "env"
    if not RISK_MASK_PREFER_STRUCTURED:
        return None, None
    source_name = str(source or "").strip().lower()
    fallback_candidates = [
        RISK_MASK_FALLBACK_DIR / f"{source_name}.npy",
        RISK_MASK_FALLBACK_DIR / f"{source_name}.npz",
        RISK_MASK_FALLBACK_DIR / f"risk_{source_name}.npy",
        RISK_MASK_FALLBACK_DIR / f"risk_{source_name}.npz",
    ]
    for candidate in fallback_candidates:
        if candidate.exists():
            return str(candidate), "fallback_dir"
    return None, None


def _iter_tree_records(tree):
    candidates = []
    try:
        if hasattr(tree, "nodes"):
            nodes = tree.nodes() if callable(tree.nodes) else tree.nodes
            if isinstance(nodes, list):
                candidates.extend([n for n in nodes if isinstance(n, dict)])
    except Exception:
        pass
    try:
        if hasattr(tree, "structures"):
            structures = tree.structures() if callable(tree.structures) else tree.structures
            if isinstance(structures, list):
                candidates.extend([n for n in structures if isinstance(n, dict)])
    except Exception:
        pass
    dedup = {}
    for rec in candidates:
        sid = rec.get("id")
        if sid is None:
            continue
        dedup[int(sid)] = rec
    return list(dedup.values())


def _descendant_set_from_tree(tree, sid):
    try:
        desc = tree.descendants([int(sid)])[0]
        ids = [int(node["id"]) for node in desc if isinstance(node, dict) and "id" in node]
        return set(ids) if ids else {int(sid)}
    except Exception:
        return {int(sid)}


def _voxel_centroid_to_bregma_mm(centroid_ijk):
    scale_mm = VOXEL_SIZE_UM / 1000.0
    ap_mm = (BREGMA_UM[0] / 1000.0) - float(centroid_ijk[0]) * scale_mm
    dv_mm = float(centroid_ijk[1]) * scale_mm - (BREGMA_UM[1] / 1000.0)
    ml_mm = float(centroid_ijk[2]) * scale_mm - (BREGMA_UM[2] / 1000.0)
    return ap_mm, dv_mm, ml_mm


def _compute_descendant_centroid(annotation, desc, hemisphere="left"):
    if annotation is None or not desc:
        return None
    try:
        labels = np.asarray(sorted(set(int(v) for v in desc if int(v) > 0)), dtype=np.uint32)
    except Exception:
        return None
    if labels.size == 0:
        return None
    chunk_depth = min(int(annotation.shape[0]), int(KEYWORD_MASK_CHUNK_DEPTH))
    sum_i = 0.0
    sum_j = 0.0
    sum_k = 0.0
    voxel_count = 0
    hemisphere_key = str(hemisphere or "bilateral").strip().lower()
    midline_k = float(BREGMA_UM[2]) / float(VOXEL_SIZE_UM)
    if hemisphere_key not in {"bilateral", "left", "right"}:
        raise ValueError("hemisphere must be bilateral, left, or right")
    for z0 in range(0, int(annotation.shape[0]), chunk_depth):
        z1 = min(int(annotation.shape[0]), z0 + chunk_depth)
        block = np.asarray(annotation[z0:z1], dtype=np.uint32)
        local_mask = np.isin(block, labels)
        if not np.any(local_mask):
            continue
        local_vox = np.argwhere(local_mask)
        if hemisphere_key == "left":
            local_vox = local_vox[local_vox[:, 2] < midline_k]
        elif hemisphere_key == "right":
            local_vox = local_vox[local_vox[:, 2] > midline_k]
        if local_vox.size == 0:
            continue
        voxel_count += int(local_vox.shape[0])
        sum_i += float(np.sum(local_vox[:, 0] + z0))
        sum_j += float(np.sum(local_vox[:, 1]))
        sum_k += float(np.sum(local_vox[:, 2]))
    if voxel_count <= 0:
        return None
    centroid = np.array(
        [sum_i / voxel_count, sum_j / voxel_count, sum_k / voxel_count],
        dtype=np.float64,
    )
    ap_mm, dv_mm, ml_mm = _voxel_centroid_to_bregma_mm(centroid)
    return {
        "ap_mm": float(ap_mm),
        "dv_mm": float(dv_mm),
        "ml_mm": float(ml_mm),
        "voxel_count": voxel_count,
    }


def _compute_label_centroid_stats_chunked(
    annotation,
    label_ids=None,
    chunk_depth=16,
    hemisphere=None,
):
    if annotation is None:
        return {}
    arr = np.asarray(annotation)
    if arr.ndim != 3 or arr.size == 0:
        return {}

    if label_ids is None:
        label_ids = np.unique(arr.astype(np.uint32))
    label_ids = np.asarray(label_ids, dtype=np.int64)
    label_ids = label_ids[label_ids > 0]
    if label_ids.size == 0:
        return {}
    label_ids = np.unique(label_ids)
    n_labels = int(label_ids.size)

    counts = np.zeros((n_labels,), dtype=np.float64)
    sum_i = np.zeros((n_labels,), dtype=np.float64)
    sum_j = np.zeros((n_labels,), dtype=np.float64)
    sum_k = np.zeros((n_labels,), dtype=np.float64)

    ny = int(arr.shape[1])
    nz = int(arr.shape[2])
    j_template = np.repeat(np.arange(ny, dtype=np.float64), nz)
    k_template = np.tile(np.arange(nz, dtype=np.float64), ny)
    hemisphere_key = str(hemisphere or "bilateral").strip().lower()
    midline_k = float(BREGMA_UM[2]) / float(VOXEL_SIZE_UM)
    if hemisphere_key not in {"bilateral", "left", "right"}:
        raise ValueError("hemisphere must be bilateral, left, or right")
    chunk_depth = max(4, int(chunk_depth))

    for z0 in range(0, int(arr.shape[0]), chunk_depth):
        z1 = min(int(arr.shape[0]), z0 + chunk_depth)
        block = np.asarray(arr[z0:z1], dtype=np.int64)
        for local_i in range(block.shape[0]):
            plane = block[local_i].reshape(-1)
            if plane.size == 0:
                continue
            mapped = np.searchsorted(label_ids, plane)
            valid = (
                (mapped >= 0)
                & (mapped < n_labels)
                & (label_ids[np.clip(mapped, 0, n_labels - 1)] == plane)
            )
            if hemisphere_key == "left":
                valid &= k_template < midline_k
            elif hemisphere_key == "right":
                valid &= k_template > midline_k
            if not np.any(valid):
                continue
            mapped_valid = mapped[valid]
            counts += np.bincount(mapped_valid, minlength=n_labels)
            sum_i += np.bincount(
                mapped_valid,
                weights=np.full(mapped_valid.shape, z0 + local_i, dtype=np.float64),
                minlength=n_labels,
            )
            sum_j += np.bincount(
                mapped_valid,
                weights=j_template[valid],
                minlength=n_labels,
            )
            sum_k += np.bincount(
                mapped_valid,
                weights=k_template[valid],
                minlength=n_labels,
            )

    stats = {}
    for idx, sid in enumerate(label_ids.tolist()):
        count = int(round(float(counts[idx])))
        if count <= 0:
            continue
        stats[int(sid)] = {
            "count": count,
            "sum_i": float(sum_i[idx]),
            "sum_j": float(sum_j[idx]),
            "sum_k": float(sum_k[idx]),
        }
    return stats


def _descendant_centroid_from_label_stats(descendant_ids_set, label_stats):
    if not descendant_ids_set:
        return None
    total_count = 0
    total_i = 0.0
    total_j = 0.0
    total_k = 0.0
    for sid in descendant_ids_set:
        stat = label_stats.get(int(sid))
        if not stat:
            continue
        count = int(stat.get("count", 0) or 0)
        if count <= 0:
            continue
        total_count += count
        total_i += float(stat.get("sum_i", 0.0))
        total_j += float(stat.get("sum_j", 0.0))
        total_k += float(stat.get("sum_k", 0.0))
    if total_count <= 0:
        return None
    centroid = (
        total_i / float(total_count),
        total_j / float(total_count),
        total_k / float(total_count),
    )
    ap_mm, dv_mm, ml_mm = _voxel_centroid_to_bregma_mm(centroid)
    return {
        "ap_mm": float(ap_mm),
        "dv_mm": float(dv_mm),
        "ml_mm": float(ml_mm),
        "voxel_count": int(total_count),
    }


def _build_allen_full_region_seed(annotation, tree, records, descendant_map):
    available_label_ids = np.unique(np.asarray(annotation, dtype=np.uint32))
    label_stats = _compute_label_centroid_stats_chunked(
        annotation,
        label_ids=available_label_ids,
        chunk_depth=REGION_INDEX_CACHE_CHUNK_DEPTH,
    )
    left_label_stats = _compute_label_centroid_stats_chunked(
        annotation,
        label_ids=available_label_ids,
        chunk_depth=REGION_INDEX_CACHE_CHUNK_DEPTH,
        hemisphere="left",
    )
    planner_seed = {}
    structure_seed = {}
    descendant_seed = {}

    for rec in records:
        sid = rec.get("id")
        if sid is None:
            continue
        sid = int(sid)
        acronym = str(rec.get("acronym", "")).strip() or f"RID_{sid}"
        name = str(rec.get("name", acronym)).strip() or acronym
        desc = descendant_map.get(sid)
        if not desc:
            desc = _descendant_set_from_tree(tree, sid)
        desc = frozenset(int(v) for v in desc)
        descendant_seed[sid] = [int(v) for v in sorted(desc)]

        total_centroid_info = _descendant_centroid_from_label_stats(desc, label_stats)
        centroid_info = _descendant_centroid_from_label_stats(desc, left_label_stats)
        if centroid_info is None:
            centroid_info = total_centroid_info
        if centroid_info is None:
            centroid_info = {
                "ap_mm": 0.0,
                "dv_mm": 0.0,
                "ml_mm": 0.0,
                "voxel_count": 0,
            }

        planner_seed[acronym] = {
            "id": sid,
            "ap": round(float(centroid_info["ap_mm"]), 3),
            "dv": round(float(centroid_info["dv_mm"]), 3),
            "ml": round(float(centroid_info["ml_mm"]), 3),
            "category": "atlas",
        }
        vcount = int((total_centroid_info or {}).get("voxel_count", 0) or 0)
        structure_seed[sid] = {
            "acronym": acronym,
            "name": name,
            "voxel_count": vcount,
            "volume_mm3": round(vcount * (VOXEL_SIZE_UM / 1000) ** 3, 4),
            "n_subregions": int(len(desc)),
        }

    return planner_seed, structure_seed, descendant_seed


def _merge_startup_planner_seed(planner_seed):
    if not isinstance(planner_seed, dict) or not planner_seed:
        return
    for key, rec in planner_seed.items():
        if not isinstance(rec, dict):
            continue
        clean_key = str(key or "").strip()
        if not clean_key:
            continue
        canonical_key = next(
            (
                existing_key
                for existing_key in PLANNER_REGIONS
                if str(existing_key).strip().lower() == clean_key.lower()
            ),
            clean_key,
        )
        existing = PLANNER_REGIONS.get(canonical_key, {})
        category = existing.get("category") if isinstance(existing, dict) else None
        if not category:
            category = rec.get("category", "atlas")
        PLANNER_REGIONS[canonical_key] = {
            "id": int(rec.get("id")),
            "ap": float(rec.get("ap", 0.0)),
            "dv": float(rec.get("dv", 0.0)),
            "ml": float(rec.get("ml", 0.0)),
            "category": str(category),
        }


def _rebuild_tree_acronym_cache(tree):
    cache = {}
    for rec in _iter_tree_records(tree):
        acronym = str(rec.get("acronym", "")).strip()
        if not acronym:
            continue
        cache.setdefault(acronym.lower(), rec)
    return cache


def _register_region_from_atlas_key(key):
    """
    Dynamically provision a missing target region by acronym from the loaded atlas tree.
    This keeps planner behavior robust when frontend selects a valid CCF acronym not in
    the curated bootstrap list.
    """
    global PLANNER_REGIONS, REGION_CENTROIDS_UM

    clean_key = str(key or "").strip()
    if not clean_key:
        return False
    if clean_key in PLANNER_REGIONS:
        return True
    if not DYNAMIC_REGION_PROVISION_ENABLED:
        return False
    if atlas_annotation is None:
        return False

    record = acronym_record_map.get(clean_key.lower())
    if record is None and atlas_tree_ref is not None:
        try:
            hit = atlas_tree_ref.get_structures_by_acronym([clean_key])
            if isinstance(hit, list) and hit:
                record = hit[0]
        except Exception:
            record = None
    if record is None:
        return False

    sid = int(record.get("id"))
    if atlas_tree_ref is not None:
        desc = _descendant_set_from_tree(atlas_tree_ref, sid)
    else:
        # Qiu2018 mode: treat label id as flat region (no Allen ontology tree).
        desc = {sid}
    centroid_info = _compute_descendant_centroid(atlas_annotation, desc)
    if centroid_info is None:
        return False

    with atlas_lock:
        if clean_key in PLANNER_REGIONS:
            return True

        existing_category = next(
            (
                reg.get("category")
                for reg in PLANNER_REGIONS.values()
                if int(reg.get("id", -1)) == sid
            ),
            None,
        )
        category = existing_category or "dynamic"

        reg = {
            "id": sid,
            "ap": round(centroid_info["ap_mm"], 3),
            "dv": round(centroid_info["dv_mm"], 3),
            "ml": round(centroid_info["ml_mm"], 3),
            "category": category,
        }
        PLANNER_REGIONS[clean_key] = reg
        descendant_ids[sid] = frozenset(desc)

        vcount = int(centroid_info["voxel_count"])
        structure_map[sid] = {
            **structure_map.get(sid, {}),
            "acronym": str(record.get("acronym", clean_key)),
            "name": str(record.get("name", clean_key)),
            "voxel_count": vcount,
            "volume_mm3": round(vcount * (VOXEL_SIZE_UM / 1000) ** 3, 4),
            "n_subregions": len(desc),
        }
        REGION_CENTROIDS_UM[sid] = (
            BREGMA_UM[0] - reg["ap"] * 1000,
            BREGMA_UM[1] + reg["dv"] * 1000,
            BREGMA_UM[2] + reg["ml"] * 1000,
        )
        acronym_record_map.setdefault(clean_key.lower(), record)

    log.info(
        f"[RegionProvision] added {clean_key} (id={sid}) ap={reg['ap']:.3f} "
        f"dv={reg['dv']:.3f} ml={reg['ml']:.3f}"
    )
    return True


def _auto_register_regions_if_needed(target_keys):
    added = []
    for key in target_keys or []:
        if key in PLANNER_REGIONS:
            continue
        if _register_region_from_atlas_key(key):
            added.append(key)
    return added


ALLEN_GROUP_NAME_CN = {
    "CTX": "大脑皮层",
    "CNU": "大脑核团",
    "IB": "间脑",
    "MB": "中脑",
    "HB": "后脑",
    "CBX": "小脑皮层",
    "CBN": "小脑核团",
    "CH": "端脑",
    "BS": "脑干",
    "CB": "小脑",
    "HY": "下丘脑",
    "TH": "丘脑",
    "STR": "纹状体",
    "PAL": "苍白球",
    "CTXsp": "皮层下板",
    "OLF": "嗅觉相关区",
    "HPF": "海马结构",
    "P": "脑桥",
    "MY": "延髓",
    "fiber tracts": "纤维束",
    "cm": "颅神经",
    "VS": "脑室系统",
    "": "其他结构",
    "apf": "前后旁正中裂",
    "AQ": "大脑导水管",
    "arb": "小脑髓质树",
    "c": "中央管（脊髓/延髓）",
    "cbc": "小脑连合",
    "cbf": "小脑相关纤维束",
    "cbp": "小脑脚",
    "cc": "胼胝体",
    "chfl": "脉络裂",
    "chpl": "脉络丛",
    "cst": "皮质脊髓束",
    "ctb": "中央被盖束",
    "drt": "背根",
    "eg": "内嗅沟",
    "eps": "锥体外系纤维系统",
    "epsc": "大脑核团相关",
    "grey": "基础细胞群与区域",
    "grv": "脑沟",
    "grv of CBX": "小脑皮层沟",
    "grv of CTX": "大脑皮层沟",
    "hf": "海马裂",
    "icf": "脚间裂",
    "IIIn": "动眼神经",
    "IIn": "视神经",
    "In": "嗅神经",
    "ipf": "旁绒球内裂",
    "IVF": "室间孔",
    "IVn": "滑车神经",
    "IXn": "舌咽神经",
    "lfbs": "外侧前脑束系统",
    "lfbst": "丘脑相关",
    "mfbc": "大脑相关",
    "mfbs": "内侧前脑束系统",
    "mfsbshy": "下丘脑相关",
    "nf": "小结裂",
    "pce": "中央前裂",
    "pcf": "峰前裂",
    "pfs": "旁绒球沟",
    "plf": "后外侧裂",
    "pms": "旁正中沟",
    "ppf": "锥体前裂",
    "pri": "主裂",
    "psf": "后上裂",
    "RC": "鼻脑腔",
    "retina": "视网膜",
    "rf": "鼻裂",
    "ri": "鼻切迹",
    "root": "根结构",
    "rst": "网状脊髓束",
    "rust": "红核脊髓束",
    "scwm": "胼胝体上大脑白质",
    "sec": "次裂",
    "SEZ": "室管膜下区",
    "sif": "单裂",
    "sst": "脊髓丘脑束",
    "tn": "终神经",
    "tsp": "顶盖脊髓通路",
    "V3": "第三脑室",
    "V4": "第四脑室",
    "V4r": "侧隐窝",
    "VIIIn": "前庭蜗神经",
    "VIIn": "面神经",
    "VIn": "外展神经",
    "VL": "侧脑室",
    "Vn": "三叉神经",
    "von": "犁鼻器神经",
    "vrt": "腹根",
    "vsp": "前庭脊髓通路",
    "XIIn": "舌下神经",
    "XIn": "副神经",
    "Xn": "迷走神经",
}


def _parse_structure_id_path(rec):
    if not isinstance(rec, dict):
        return []
    raw = rec.get("structure_id_path")
    if isinstance(raw, (list, tuple)):
        vals = []
        for v in raw:
            try:
                vals.append(int(v))
            except Exception:
                continue
        return vals
    if isinstance(raw, str):
        vals = []
        for chunk in raw.replace("\\", "/").split("/"):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                vals.append(int(chunk))
            except Exception:
                continue
        return vals
    return []


def _resolve_allen_group_name_cn(group_key, group_name):
    key = str(group_key or "").strip()
    if key in ALLEN_GROUP_NAME_CN:
        return str(ALLEN_GROUP_NAME_CN.get(key, "")).strip()

    name_l = str(group_name or "").strip().lower()
    if not name_l:
        return "其他结构"

    # Fallback rules for unforeseen ontology buckets.
    if "ventricle" in name_l or "aqueduct" in name_l or "foramen" in name_l:
        return "脑室系统"
    if "nerve" in name_l or "roots" in name_l:
        return "颅神经/神经根"
    if "tract" in name_l or "pathway" in name_l or "bundle" in name_l or "white matter" in name_l:
        return "白质纤维通路"
    if "fissure" in name_l or "sulcus" in name_l or "groove" in name_l or "incisure" in name_l:
        return "脑沟裂隙"
    if "thalamus" in name_l:
        return "丘脑相关"
    if "hypothalamus" in name_l:
        return "下丘脑相关"
    if "cerebell" in name_l:
        return "小脑相关"
    if "cerebr" in name_l or "cortex" in name_l:
        return "大脑相关"
    if "retina" in name_l:
        return "视网膜"
    return "其他结构"


def _derive_allen_group(rec, id_record_map):
    """
    Derive a stable high-level Allen group for UI categorization.
    We use structure_id_path depth-3 (root->grey/cfg->cerebrum/brainstem->group),
    which yields compact, anatomically meaningful buckets (CTX/TH/HY/MB/...).
    """
    path = _parse_structure_id_path(rec)
    if not path:
        return {
            "groupKey": "",
            "groupName": "",
            "groupNameCn": "",
            "groupId": None,
        }

    if len(path) >= 4:
        group_sid = int(path[3])
    elif len(path) >= 3:
        group_sid = int(path[2])
    elif len(path) >= 2:
        group_sid = int(path[1])
    else:
        group_sid = int(path[0])

    group_rec = id_record_map.get(group_sid, {})
    group_key = str(group_rec.get("acronym") or f"RID_{group_sid}").strip()
    group_name = str(group_rec.get("name") or group_key).strip()
    group_name_cn = _resolve_allen_group_name_cn(group_key, group_name)

    return {
        "groupKey": group_key,
        "groupName": group_name,
        "groupNameCn": group_name_cn,
        "groupId": group_sid,
    }


def _build_region_catalog(scope="all"):
    """
    Build a region catalog payload for API responses.

    scope="planner" -> curated/registered planner regions only
    scope="all"     -> full atlas acronyms (planner + indexed atlas ontology)
    """
    scope_norm = str(scope or "all").strip().lower()
    with atlas_lock:
        planner_snapshot = dict(PLANNER_REGIONS)
        structure_snapshot = dict(structure_map)
        acronym_snapshot = dict(acronym_record_map)
        has_tree_snapshot = bool(atlas_tree_ref is not None)
    id_record_snapshot = {}
    for rec in acronym_snapshot.values():
        if not isinstance(rec, dict):
            continue
        sid = rec.get("id")
        try:
            sid = int(sid)
        except Exception:
            continue
        if sid not in id_record_snapshot:
            id_record_snapshot[sid] = rec

    include_planner_seed = not (
        ACTIVE_ATLAS_MODE == "qiu2018"
        and scope_norm not in ("planner", "curated", "selected")
    )
    if (
        ACTIVE_ATLAS_MODE == "mri_ccf_hybrid"
        and not has_tree_snapshot
        and scope_norm not in ("planner", "curated", "selected")
    ):
        include_planner_seed = False
    key_set = set(planner_snapshot.keys()) if include_planner_seed else set()
    if scope_norm not in ("planner", "curated", "selected"):
        sid_seen = set()
        planner_key_by_sid = {
            int(reg.get("id")): key
            for key, reg in planner_snapshot.items()
            if isinstance(reg, dict) and reg.get("id") is not None
        }
        for rec in acronym_snapshot.values():
            if not isinstance(rec, dict):
                continue
            acronym = str(rec.get("acronym", "")).strip()
            if not acronym:
                continue
            if ACTIVE_ATLAS_MODE in ("qiu2018", "mri_ccf_hybrid"):
                sid = rec.get("id")
                try:
                    sid = int(sid)
                except Exception:
                    sid = None
                if sid is not None:
                    if sid in sid_seen:
                        continue
                    sid_seen.add(sid)
            key_set.add(planner_key_by_sid.get(sid, acronym))

    out = {}
    for key in sorted(key_set):
        reg = planner_snapshot.get(key) if isinstance(planner_snapshot.get(key), dict) else None
        rec = acronym_snapshot.get(str(key).lower())
        sid = None
        if isinstance(reg, dict):
            try:
                sid = int(reg.get("id"))
            except Exception:
                sid = None
        if sid is None and isinstance(rec, dict):
            try:
                sid = int(rec.get("id"))
            except Exception:
                sid = None
        group_info = _derive_allen_group(rec, id_record_snapshot) if isinstance(rec, dict) else {
            "groupKey": "",
            "groupName": "",
            "groupNameCn": "",
            "groupId": None,
        }
        color_hex = ""
        color_r = -1.0
        color_g = -1.0
        color_b = -1.0
        if isinstance(rec, dict):
            rgb_triplet = rec.get("rgb_triplet")
            if isinstance(rgb_triplet, (list, tuple)) and len(rgb_triplet) >= 3:
                try:
                    r = max(0, min(255, int(rgb_triplet[0])))
                    g = max(0, min(255, int(rgb_triplet[1])))
                    b = max(0, min(255, int(rgb_triplet[2])))
                    color_hex = f"#{r:02X}{g:02X}{b:02X}"
                    color_r = round(r / 255.0, 6)
                    color_g = round(g / 255.0, 6)
                    color_b = round(b / 255.0, 6)
                except Exception:
                    color_hex = ""
                    color_r = -1.0
                    color_g = -1.0
                    color_b = -1.0

        info = structure_snapshot.get(sid, {}) if sid is not None else {}
        out[key] = {
            "structureId": sid,
            "structureIdPath": _parse_structure_id_path(rec if isinstance(rec, dict) else id_record_snapshot.get(sid, {})),
            "acronym": str(
                (info.get("acronym") if isinstance(info, dict) else None)
                or (rec.get("acronym") if isinstance(rec, dict) else key)
                or key
            ),
            "name": str(
                (info.get("name") if isinstance(info, dict) else None)
                or (rec.get("name") if isinstance(rec, dict) else key)
                or key
            ),
            "voxel_count": int((info.get("voxel_count", 0) if isinstance(info, dict) else 0) or 0),
            "volume_mm3": float((info.get("volume_mm3", 0.0) if isinstance(info, dict) else 0.0) or 0.0),
            "n_subregions": int((info.get("n_subregions", 0) if isinstance(info, dict) else 0) or 0),
            "ap": float(reg.get("ap", 0.0)) if isinstance(reg, dict) else 0.0,
            "dv": float(reg.get("dv", 0.0)) if isinstance(reg, dict) else 0.0,
            "ml": float(reg.get("ml", 0.0)) if isinstance(reg, dict) else 0.0,
            "category": str(reg.get("category", "dynamic")) if isinstance(reg, dict) else "dynamic",
            "plannerReady": bool(isinstance(reg, dict)),
            "presentInVolume": bool(int((info.get("voxel_count", 0) if isinstance(info, dict) else 0) or 0) > 0),
            "unavailableReason": "" if int((info.get("voxel_count", 0) if isinstance(info, dict) else 0) or 0) > 0 else "not_present_in_current_annotation",
            "groupId": group_info.get("groupId"),
            "groupKey": group_info.get("groupKey", ""),
            "groupName": group_info.get("groupName", ""),
            "groupNameCn": group_info.get("groupNameCn", ""),
            "colorHex": color_hex,
            "colorR": color_r,
            "colorG": color_g,
            "colorB": color_b,
        }
    return out


def _compute_available_region_count(region_catalog=None):
    """
    Return the atlas-wide available region count used by status/version APIs.

    For Allen mode we should reflect full ontology availability (not only currently
    registered planner regions); for Qiu mode we rely on reconstruction labels.
    """
    with atlas_lock:
        mode = ACTIVE_ATLAS_MODE
        planner_snapshot = dict(PLANNER_REGIONS)
        acronym_snapshot = dict(acronym_record_map)
        structure_snapshot = dict(structure_map)

    if mode == "allen_sdk":
        if isinstance(region_catalog, dict) and region_catalog:
            return int(len(region_catalog))
        count = int(len(acronym_snapshot))
        if count <= 0:
            return int(
                sum(
                    1
                    for _, info in structure_snapshot.items()
                    if isinstance(info, dict) and int(info.get("voxel_count", 0) or 0) > 0
                )
            )
        # Include planner-only aliases that may not appear in acronym index.
        planner_extras = sum(
            1 for key in planner_snapshot.keys() if str(key).strip().lower() not in acronym_snapshot
        )
        # Keep status metrics aligned with planner-visible full catalog.
        return int(max(count + planner_extras, len(planner_snapshot)))

    # Qiu2018/MRI: label-driven reconstruction availability. If the caller
    # already built the catalog, use the same presentInVolume semantics as the
    # UI/API instead of recounting raw structure_map entries with a slightly
    # different ontology/root handling.
    if isinstance(region_catalog, dict) and region_catalog:
        return int(
            sum(1 for _, rec in region_catalog.items() if isinstance(rec, dict) and bool(rec.get("presentInVolume", False)))
        )

    return int(
        sum(
            1
            for _, info in structure_snapshot.items()
            if isinstance(info, dict) and int(info.get("voxel_count", 0) or 0) > 0
        )
    )


def _build_keyword_risk_mask(annotation, tree):
    records = _iter_tree_records(tree)
    if not records:
        return None, {}, {}

    bucket_to_ids = {k: set() for k in RISK_NAME_HINTS}
    for rec in records:
        name = str(rec.get("name", "")).lower()
        acronym = str(rec.get("acronym", "")).lower()
        sid = rec.get("id")
        if sid is None:
            continue
        text = f"{name} {acronym}"
        for bucket, hints in RISK_NAME_HINTS.items():
            if any(h in text for h in hints):
                bucket_to_ids[bucket].add(int(sid))

    if not any(bucket_to_ids.values()):
        return None, {}, {}

    merged_ids = set()
    source_summary = {}
    bucket_masks = {}
    merged_mask = np.zeros(annotation.shape, dtype=bool)

    def _chunked_isin_mask(label_ids):
        labels = np.asarray(sorted(set(int(v) for v in (label_ids or []) if int(v) > 0)), dtype=np.uint32)
        if labels.size == 0:
            return np.zeros(annotation.shape, dtype=bool)
        depth = int(annotation.shape[0])
        chunk_depth = min(depth, int(KEYWORD_MASK_CHUNK_DEPTH))
        out = np.zeros(annotation.shape, dtype=bool)
        for z0 in range(0, depth, chunk_depth):
            z1 = min(depth, z0 + chunk_depth)
            block = np.asarray(annotation[z0:z1], dtype=np.uint32)
            out[z0:z1] = np.isin(block, labels)
        return out

    for bucket, ids in bucket_to_ids.items():
        expanded = set()
        for sid in ids:
            expanded |= _descendant_set_from_tree(tree, sid)
        if expanded:
            try:
                bucket_mask = _chunked_isin_mask(expanded)
            except MemoryError:
                log.warning(
                    f"Skip keyword risk bucket '{bucket}' due MemoryError "
                    f"(chunkDepth={KEYWORD_MASK_CHUNK_DEPTH})"
                )
                continue
            bucket_voxels = int(np.count_nonzero(bucket_mask))
            if bucket_voxels <= 0:
                continue
            source_summary[bucket] = len(expanded)
            merged_ids |= expanded
            bucket_masks[bucket] = bucket_mask
            np.logical_or(merged_mask, bucket_mask, out=merged_mask)

    if not merged_ids or int(np.count_nonzero(merged_mask)) <= 0:
        return None, {}, {}

    return merged_mask.astype(bool), source_summary, bucket_masks


def _alnum_tokens(text):
    buf = []
    for ch in str(text or "").lower():
        buf.append(ch if ch.isalnum() else " ")
    return [tok for tok in "".join(buf).split() if tok]


def _build_keyword_risk_mask_from_structure_maps(annotation, structure_snapshot, descendants_snapshot):
    if annotation is None or not isinstance(structure_snapshot, dict):
        return None, {}, {}

    short_hints = {
        "ventricle": {"aq", "v3", "v4", "v4r", "ivf", "chpl", "sez"},
        "sulcus": {"grv", "hf", "rf", "pfs", "plf", "icf", "sec"},
    }
    bucket_to_ids = {k: set() for k in RISK_NAME_HINTS}
    for sid, rec in structure_snapshot.items():
        try:
            sid_i = int(sid)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("name", "")).strip().lower()
        acronym = str(rec.get("acronym", "")).strip().lower()
        text = f"{name} {acronym}".strip()
        if not text:
            continue
        tokens = set(_alnum_tokens(text))
        for bucket, hints in RISK_NAME_HINTS.items():
            long_hit = any(str(h or "").strip().lower() in text for h in hints)
            short_hit = any(tok in tokens for tok in short_hints.get(bucket, set()))
            if long_hit or short_hit:
                bucket_to_ids[bucket].add(sid_i)

    if not any(bucket_to_ids.values()):
        return None, {}, {}

    def _chunked_isin_mask(label_ids):
        labels = np.asarray(sorted(set(int(v) for v in (label_ids or []) if int(v) > 0)), dtype=np.uint32)
        if labels.size == 0:
            return np.zeros(annotation.shape, dtype=bool)
        depth = int(annotation.shape[0])
        chunk_depth = min(depth, int(KEYWORD_MASK_CHUNK_DEPTH))
        out = np.zeros(annotation.shape, dtype=bool)
        for z0 in range(0, depth, chunk_depth):
            z1 = min(depth, z0 + chunk_depth)
            block = np.asarray(annotation[z0:z1], dtype=np.uint32)
            out[z0:z1] = np.isin(block, labels)
        return out

    merged_ids = set()
    source_summary = {}
    bucket_masks = {}
    merged_mask = np.zeros(annotation.shape, dtype=bool)
    descendants_snapshot = descendants_snapshot if isinstance(descendants_snapshot, dict) else {}
    for bucket, ids in bucket_to_ids.items():
        expanded = set()
        for sid in ids:
            desc = descendants_snapshot.get(int(sid))
            if isinstance(desc, (set, frozenset, list, tuple)):
                expanded |= set(int(v) for v in desc if int(v) > 0)
            else:
                expanded.add(int(sid))
        if not expanded:
            continue
        try:
            bucket_mask = _chunked_isin_mask(expanded)
        except MemoryError:
            log.warning(
                f"Skip structure-map keyword risk bucket '{bucket}' due MemoryError "
                f"(chunkDepth={KEYWORD_MASK_CHUNK_DEPTH})"
            )
            continue
        bucket_voxels = int(np.count_nonzero(bucket_mask))
        if bucket_voxels <= 0:
            continue
        source_summary[bucket] = len(expanded)
        merged_ids |= expanded
        bucket_masks[bucket] = bucket_mask
        np.logical_or(merged_mask, bucket_mask, out=merged_mask)

    if not merged_ids or int(np.count_nonzero(merged_mask)) <= 0:
        return None, {}, {}
    return merged_mask.astype(bool), source_summary, bucket_masks


def _initialize_risk_model(
    annotation,
    tree=None,
    extra_source_masks=None,
    extra_source_meta=None,
    precomputed_signed_sdf_mm=None,
    precomputed_layered_sdf_channels_mm=None,
):
    global risk_surface_tree, risk_surface_points_um, risk_model_summary
    global risk_surface_source_trees, risk_surface_source_points_um
    global risk_surface_class_trees, risk_surface_class_points_um, risk_class_volume
    global risk_signed_sdf_mm, risk_layered_sdf_channels_mm, risk_source_mask_map

    source_masks = {}
    source_meta = {}
    structured_sources = set()

    for source, env_key in RISK_MASK_ENV.items():
        mask_path, path_source = _resolve_risk_mask_path(source, env_key)
        if not mask_path:
            continue
        mask = _safe_load_mask(mask_path)
        if mask is None:
            continue
        source_masks[source] = mask
        structured_sources.add(str(source))
        source_meta[source] = {
            "kind": "external",
            "path": str(mask_path),
            "pathSource": str(path_source or "unknown"),
            "voxels": int(mask.sum()),
        }

    if tree is not None and not DISABLE_ATLAS_KEYWORD_RISK:
        try:
            keyword_mask, keyword_meta, keyword_bucket_masks = _build_keyword_risk_mask(annotation, tree)
        except MemoryError:
            log.warning(
                "Keyword risk mask build hit MemoryError; continue without atlas keyword risk masks."
            )
            keyword_mask, keyword_meta, keyword_bucket_masks = None, {}, {}
        if keyword_mask is not None and int(keyword_mask.sum()) > 0:
            source_meta["atlas_keywords"] = {
                "kind": "atlas",
                "voxels": int(keyword_mask.sum()),
                "detail": keyword_meta,
            }
            bucket_added = 0
            for bucket, bucket_mask in (keyword_bucket_masks or {}).items():
                if bucket_mask is None:
                    continue
                bucket_voxels = int(np.count_nonzero(bucket_mask))
                if bucket_voxels <= 0:
                    continue
                source_key = f"atlas_keywords:{bucket}"
                source_masks[source_key] = bucket_mask
                bucket_added += 1
                source_meta[source_key] = {
                    "kind": "atlas_keyword_bucket",
                    "bucket": bucket,
                    "voxels": bucket_voxels,
                }
            # Avoid duplicated 3D masks when bucket masks are available.
            if bucket_added <= 0:
                source_masks.setdefault("atlas_keywords", keyword_mask)
    elif tree is not None and DISABLE_ATLAS_KEYWORD_RISK:
        log.warning("Atlas keyword risk mask is disabled by PROBEPLANNER_DISABLE_ATLAS_KEYWORD_RISK=1")

    if isinstance(extra_source_masks, dict):
        for source_key, source_mask in extra_source_masks.items():
            if source_mask is None:
                continue
            try:
                mask_bool = np.asarray(source_mask, dtype=bool)
            except Exception:
                continue
            if mask_bool.shape != annotation.shape:
                log.warning(
                    f"Skip extra risk mask '{source_key}' due shape mismatch {mask_bool.shape} != {annotation.shape}"
                )
                continue
            if int(np.count_nonzero(mask_bool)) <= 0:
                continue
            source_key_norm = str(source_key)
            source_masks[source_key_norm] = mask_bool
            if isinstance(extra_source_meta, dict):
                source_meta[source_key_norm] = deepcopy(extra_source_meta.get(source_key_norm, {}))
            source_class = classify_risk_source(source_key_norm)
            if source_class in RISK_CLASS_CODE and not source_key_norm.startswith("atlas_keywords:"):
                structured_sources.add(source_key_norm)

    if not source_masks and not DISABLE_ATLAS_KEYWORD_RISK:
        try:
            struct_snapshot = dict(structure_map) if isinstance(structure_map, dict) else {}
            desc_snapshot = dict(descendant_ids) if isinstance(descendant_ids, dict) else {}
        except Exception:
            struct_snapshot = {}
            desc_snapshot = {}
        if struct_snapshot:
            try:
                kw_mask, kw_meta, kw_bucket_masks = _build_keyword_risk_mask_from_structure_maps(
                    annotation,
                    struct_snapshot,
                    desc_snapshot,
                )
            except MemoryError:
                log.warning(
                    "Fallback structure-map keyword risk build hit MemoryError; risk model may be disabled."
                )
                kw_mask, kw_meta, kw_bucket_masks = None, {}, {}
            if kw_mask is not None and int(np.count_nonzero(kw_mask)) > 0:
                added_bucket = 0
                for bucket, bucket_mask in (kw_bucket_masks or {}).items():
                    if bucket_mask is None:
                        continue
                    bucket_voxels = int(np.count_nonzero(bucket_mask))
                    if bucket_voxels <= 0:
                        continue
                    source_key = f"atlas_keywords:{bucket}"
                    source_masks[source_key] = bucket_mask
                    source_meta[source_key] = {
                        "kind": "atlas_keyword_bucket_fallback",
                        "bucket": bucket,
                        "voxels": bucket_voxels,
                    }
                    added_bucket += 1
                if added_bucket <= 0:
                    source_masks.setdefault("atlas_keywords", kw_mask)
                    source_meta["atlas_keywords"] = {
                        "kind": "atlas_fallback",
                        "voxels": int(np.count_nonzero(kw_mask)),
                        "detail": kw_meta or {},
                    }

    if not source_masks:
        log.warning(
            "Risk model disabled: no usable risk sources "
            f"(treeProvided={tree is not None}, extraMasks={0 if not isinstance(extra_source_masks, dict) else len(extra_source_masks)}, "
            f"disableKeyword={bool(DISABLE_ATLAS_KEYWORD_RISK)})"
        )
        with risk_model_lock:
            risk_surface_tree = None
            risk_surface_points_um = None
            risk_surface_source_trees = {}
            risk_surface_source_points_um = {}
            risk_surface_class_trees = {}
            risk_surface_class_points_um = {}
            risk_class_volume = None
            risk_signed_sdf_mm = None
            risk_layered_sdf_channels_mm = {}
            risk_source_mask_map = {}
            risk_model_summary = {
                "enabled": False,
                "source": [],
                "sourceTag": "none",
                "voxelCount": 0,
                "surfacePointCount": 0,
                "sdfEnabled": False,
                "sdfBuildState": "idle",
            }
        return

    merged = None
    for mask in source_masks.values():
        merged = mask if merged is None else np.logical_or(merged, mask)

    if merged is None or int(merged.sum()) == 0:
        with risk_model_lock:
            risk_surface_tree = None
            risk_surface_points_um = None
            risk_surface_source_trees = {}
            risk_surface_source_points_um = {}
            risk_surface_class_trees = {}
            risk_surface_class_points_um = {}
            risk_class_volume = None
            risk_signed_sdf_mm = None
            risk_layered_sdf_channels_mm = {}
            risk_source_mask_map = {}
            risk_model_summary = {
                "enabled": False,
                "source": sorted(source_masks.keys()),
                "sourceTag": "none",
                "voxelCount": 0,
                "surfacePointCount": 0,
                "sdfEnabled": False,
                "sdfBuildState": "idle",
            }
        return

    def _surface_points(mask):
        if binary_erosion is not None:
            surface_mask = np.logical_and(
                mask,
                np.logical_not(
                    binary_erosion(mask, structure=np.ones((3, 3, 3), dtype=bool), border_value=0)
                ),
            )
        else:
            surface_mask = mask

        points = np.argwhere(surface_mask).astype(np.float64)
        if points.size == 0:
            points = np.argwhere(mask).astype(np.float64)
        if points.size == 0:
            return None

        max_points = 250000
        if len(points) > max_points:
            stride = int(math.ceil(len(points) / max_points))
            points = points[::stride]
        return points * float(VOXEL_SIZE_UM)

    merged_points_um = _surface_points(merged)
    if merged_points_um is None or len(merged_points_um) == 0:
        with risk_model_lock:
            risk_surface_tree = None
            risk_surface_points_um = None
            risk_surface_source_trees = {}
            risk_surface_source_points_um = {}
            risk_surface_class_trees = {}
            risk_surface_class_points_um = {}
            risk_class_volume = None
            risk_signed_sdf_mm = None
            risk_layered_sdf_channels_mm = {}
            risk_source_mask_map = {}
            risk_model_summary = {
                "enabled": False,
                "source": sorted(source_masks.keys()),
                "sourceTag": "none",
                "voxelCount": int(merged.sum()),
                "surfacePointCount": 0,
                "sdfEnabled": False,
                "sdfBuildState": "idle",
            }
        return

    merged_tree_obj = cKDTree(merged_points_um) if cKDTree is not None else None
    signed_sdf_mm = (
        np.asarray(precomputed_signed_sdf_mm, dtype=np.float32)
        if precomputed_signed_sdf_mm is not None
        else _load_signed_sdf_from_cache_only(source_masks)
    )
    if signed_sdf_mm is not None and tuple(signed_sdf_mm.shape) != tuple(annotation.shape):
        log.warning(
            f"Ignore precomputed SDF due shape mismatch {signed_sdf_mm.shape} != {annotation.shape}"
        )
        signed_sdf_mm = None
    layered_channel_map = {}
    if isinstance(precomputed_layered_sdf_channels_mm, dict):
        for channel_key, channel_arr in precomputed_layered_sdf_channels_mm.items():
            cls = _risk_class_from_sdf_channel(channel_key)
            if cls not in RISK_CLASS_CODE:
                continue
            try:
                arr = np.asarray(channel_arr, dtype=np.float32)
            except Exception:
                continue
            if tuple(arr.shape) != tuple(annotation.shape):
                log.warning(
                    f"Ignore layered SDF channel '{channel_key}' due shape mismatch {arr.shape} != {annotation.shape}"
                )
                continue
            layered_channel_map[str(channel_key)] = arr
    sdf_build_state = "ready" if signed_sdf_mm is not None else "cache_miss"
    sdf_build_message = "Loaded cached SDF" if signed_sdf_mm is not None else "No cached SDF"
    if signed_sdf_mm is None and SDF_SYNC_BUILD_ENABLED:
        signed_sdf_mm = _load_or_build_signed_sdf_mm(merged, source_masks)
        if signed_sdf_mm is not None:
            sdf_build_state = "ready"
            sdf_build_message = "Built SDF synchronously"
        else:
            sdf_build_state = "failed"
            sdf_build_message = "SDF synchronous build failed"
    elif signed_sdf_mm is None:
        # In async mode we avoid blocking atlas loading at 82%.
        sdf_build_state = _start_async_sdf_build(merged, source_masks)
        if sdf_build_state == "building":
            sdf_build_message = "SDF building in background; using KDTree temporarily"
        elif sdf_build_state == "disabled":
            sdf_build_message = "Async SDF build disabled; using KDTree"

    source_trees = {}
    source_points = {}
    class_point_chunks = {}
    source_mask_map = {}
    class_volume = np.zeros(annotation.shape, dtype=np.uint8)
    class_priority_volume = np.zeros(annotation.shape, dtype=np.uint8)
    for source_key, mask in source_masks.items():
        source_class = classify_risk_source(source_key)
        if source_class in RISK_CLASS_CODE:
            prio = int(RISK_CLASS_PRIORITY.get(source_class, 1))
            replace_mask = np.logical_and(mask, prio >= class_priority_volume)
            class_volume[replace_mask] = np.uint8(RISK_CLASS_CODE[source_class])
            class_priority_volume[replace_mask] = np.uint8(max(0, min(255, prio)))
        source_mask_map[source_key] = np.asarray(mask, dtype=bool)
        pts_um = _surface_points(mask)
        if pts_um is None or len(pts_um) == 0:
            continue
        if source_class in RISK_CLASS_CODE:
            class_point_chunks.setdefault(source_class, []).append(pts_um)
        if cKDTree is not None:
            source_trees[source_key] = cKDTree(pts_um)
        else:
            source_points[source_key] = pts_um

    class_trees = {}
    class_points = {}
    for source_class, chunks in class_point_chunks.items():
        if not chunks:
            continue
        merged_cls_pts = np.concatenate(chunks, axis=0)
        if len(merged_cls_pts) > 350000:
            stride = int(math.ceil(len(merged_cls_pts) / 350000))
            merged_cls_pts = merged_cls_pts[::stride]
        if cKDTree is not None:
            class_trees[source_class] = cKDTree(merged_cls_pts)
        else:
            class_points[source_class] = merged_cls_pts

    has_keyword_sources = any(
        src == "atlas_keywords" or str(src).startswith("atlas_keywords:")
        for src in source_masks.keys()
    )
    if bool(structured_sources) and has_keyword_sources:
        source_tag = "hybrid_structured_plus_keywords"
    elif bool(structured_sources):
        source_tag = "layered_structured_masks"
    elif has_keyword_sources:
        source_tag = "atlas_keywords_only"
    else:
        source_tag = "unclassified"

    source_surface_point_count = {
        key: int(len(source_points[key]))
        if key in source_points
        else int(source_trees[key].n)
        for key in sorted(set(source_trees.keys()) | set(source_points.keys()))
    }
    class_surface_point_count = {
        key: int(len(class_points[key]))
        if key in class_points
        else int(class_trees[key].n)
        for key in sorted(set(class_trees.keys()) | set(class_points.keys()))
    }
    class_coverage_voxels = {
        cls: int(np.count_nonzero(class_volume == code))
        for cls, code in sorted(RISK_CLASS_CODE.items())
    }
    risk_layer_completeness = _build_risk_layer_completeness(
        layered_channel_map,
        source_meta,
        class_coverage_voxels,
        class_surface_point_count,
    )
    missing_structured_classes = _missing_structured_risk_classes(risk_layer_completeness)

    with risk_model_lock:
        risk_surface_tree = merged_tree_obj
        risk_surface_points_um = merged_points_um if merged_tree_obj is None else None
        risk_surface_source_trees = source_trees
        risk_surface_source_points_um = source_points
        risk_surface_class_trees = class_trees
        risk_surface_class_points_um = class_points
        risk_class_volume = class_volume
        risk_signed_sdf_mm = signed_sdf_mm
        risk_layered_sdf_channels_mm = layered_channel_map
        risk_source_mask_map = source_mask_map
        risk_model_summary = {
            "enabled": True,
            "source": sorted(source_masks.keys()),
            "sourceTag": source_tag,
            "voxelCount": int(merged.sum()),
            "surfacePointCount": int(len(merged_points_um)),
            "sourceSurfacePointCount": source_surface_point_count,
            "classSurfacePointCount": class_surface_point_count,
            "classCoverageVoxels": class_coverage_voxels,
            "meta": source_meta,
            "structuredMaskSources": sorted(structured_sources),
            "structuredMaskEnabled": bool(structured_sources),
            "requiredLayeredSdfChannels": list(RISK_LAYER_REQUIRED_SDF_CHANNELS),
            "riskLayerCompleteness": risk_layer_completeness,
            "missingStructuredRiskClasses": missing_structured_classes,
            "keywordFallbackUsed": bool(
                any(
                    (src == "atlas_keywords" or str(src).startswith("atlas_keywords:"))
                    for src in source_masks.keys()
                )
                and not bool(structured_sources)
            ),
            "keywordRiskDisabled": bool(DISABLE_ATLAS_KEYWORD_RISK),
            "backend": "kdtree" if merged_tree_obj is not None else "numpy",
            "distanceModel": "trajectory_sdf_edt" if signed_sdf_mm is not None else "trajectory_kdtree",
            "sdfEnabled": bool(signed_sdf_mm is not None),
            "layeredSdfChannels": sorted(layered_channel_map.keys()),
            "sdfCacheEnabled": bool(SDF_CACHE_ENABLED),
            "sdfBuildState": str(sdf_build_state),
            "sdfBuildMessage": str(sdf_build_message),
            "sdfBuildUpdatedAt": _utc_now_iso(),
            "sdfSyncBuildEnabled": bool(SDF_SYNC_BUILD_ENABLED),
            "sdfAsyncBuildEnabled": bool(SDF_ASYNC_BUILD_ENABLED),
        }


def _degrade_risk_model(reason):
    global risk_surface_tree, risk_surface_points_um, risk_model_summary
    global risk_surface_source_trees, risk_surface_source_points_um
    global risk_surface_class_trees, risk_surface_class_points_um, risk_class_volume
    global risk_signed_sdf_mm, risk_layered_sdf_channels_mm, risk_source_mask_map
    with risk_model_lock:
        risk_surface_tree = None
        risk_surface_points_um = None
        risk_surface_source_trees = {}
        risk_surface_source_points_um = {}
        risk_surface_class_trees = {}
        risk_surface_class_points_um = {}
        risk_class_volume = None
        risk_signed_sdf_mm = None
        risk_layered_sdf_channels_mm = {}
        risk_source_mask_map = {}
        risk_model_summary = {
            "enabled": False,
            "source": [],
            "sourceTag": "none",
            "voxelCount": 0,
            "surfacePointCount": 0,
            "sdfEnabled": False,
            "sdfBuildState": "failed",
            "degraded": True,
            "error": str(reason or "risk_model_degraded"),
        }


def _mark_risk_model_initializing(message="Risk model initializing"):
    global risk_surface_tree, risk_surface_points_um, risk_model_summary
    global risk_surface_source_trees, risk_surface_source_points_um
    global risk_surface_class_trees, risk_surface_class_points_um, risk_class_volume
    global risk_signed_sdf_mm, risk_layered_sdf_channels_mm, risk_source_mask_map
    with risk_model_lock:
        risk_surface_tree = None
        risk_surface_points_um = None
        risk_surface_source_trees = {}
        risk_surface_source_points_um = {}
        risk_surface_class_trees = {}
        risk_surface_class_points_um = {}
        risk_class_volume = None
        risk_signed_sdf_mm = None
        risk_layered_sdf_channels_mm = {}
        risk_source_mask_map = {}
        risk_model_summary = {
            "enabled": False,
            "source": [],
            "sourceTag": "none",
            "voxelCount": 0,
            "surfacePointCount": 0,
            "sdfEnabled": False,
            "sdfBuildState": "building",
            "state": "building",
            "progress": 0.6,
            "sdfBuildMessage": str(message or "Risk model initializing"),
            "sdfBuildUpdatedAt": _utc_now_iso(),
        }


def _query_min_distance_um(points_um, tree_obj=None, reference_points_um=None):
    if points_um is None or len(points_um) == 0:
        return None, None, None, None
    if tree_obj is not None:
        dist_um, nearest_idx = tree_obj.query(points_um, k=1)
        if np.isscalar(dist_um):
            dist_arr = np.array([float(dist_um)], dtype=np.float64)
            idx_arr = np.array([int(nearest_idx)], dtype=np.int32)
        else:
            dist_arr = np.asarray(dist_um, dtype=np.float64)
            idx_arr = np.asarray(nearest_idx, dtype=np.int32)
        if dist_arr.size == 0:
            return None, None, None, None
        pt_idx = int(np.argmin(dist_arr))
        return float(dist_arr[pt_idx]), pt_idx, int(idx_arr[pt_idx]), dist_arr
    if reference_points_um is None or len(reference_points_um) == 0:
        return None, None, None, None
    diff = points_um[:, None, :] - reference_points_um[None, :, :]
    dist = np.sqrt(np.sum(diff * diff, axis=2))
    if dist.size == 0:
        return None, None, None, None
    point_idx, nearest_idx = np.unravel_index(np.argmin(dist), dist.shape)
    min_um = float(dist[point_idx, nearest_idx])
    dist_arr = np.min(dist, axis=1)
    return min_um, int(point_idx), int(nearest_idx), np.asarray(dist_arr, dtype=np.float64)


def _build_signed_sdf_mm(mask):
    """
    Build true signed distance field in millimeters.
    Positive values are outside risk mask, negative values are inside.
    """
    if distance_transform_edt is None or mask is None:
        return None
    try:
        risk_mask = np.asarray(mask, dtype=bool)
        if risk_mask.size == 0:
            return None
        voxel_mm = float(VOXEL_SIZE_UM) / 1000.0
        use_gpu = bool(SDF_GPU_ENABLED_DEFAULT and _cp is not None and _cupy_ndimage is not None)
        if use_gpu:
            try:
                risk_gpu = _cp.asarray(risk_mask, dtype=_cp.bool_)
                outside_gpu = _cupy_ndimage.distance_transform_edt(~risk_gpu).astype(_cp.float32) * voxel_mm
                inside_gpu = _cupy_ndimage.distance_transform_edt(risk_gpu).astype(_cp.float32) * voxel_mm
                signed_gpu = outside_gpu
                signed_gpu[risk_gpu] = -inside_gpu[risk_gpu]
                return _cp.asnumpy(signed_gpu).astype(np.float32, copy=False)
            except Exception as exc_gpu:
                log.warning(f"SDF GPU path failed, fallback to CPU: {exc_gpu}")
        outside_mm = distance_transform_edt(~risk_mask).astype(np.float32) * voxel_mm
        inside_mm = distance_transform_edt(risk_mask).astype(np.float32) * voxel_mm
        signed_mm = outside_mm
        signed_mm[risk_mask] = -inside_mm[risk_mask]
        return signed_mm
    except Exception as exc:
        log.warning(f"Failed to build SDF: {exc}")
        return None


def _sdf_cache_key(source_masks):
    atlas_hash = str((atlas_signature or {}).get("hash") or "nohash")
    items = []
    for key in sorted((source_masks or {}).keys()):
        try:
            vox = int(np.count_nonzero(source_masks[key]))
        except Exception:
            vox = 0
        items.append(f"{key}:{vox}")
    raw = f"{atlas_hash}|{','.join(items)}|{VOXEL_SIZE_UM}|{ATLAS_SHAPE}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"sdf_{digest}.npy"


def _load_or_build_signed_sdf_mm(mask, source_masks):
    if not SDF_CACHE_ENABLED:
        return _build_signed_sdf_mm(mask)
    try:
        cache_name = _sdf_cache_key(source_masks)
        cache_path = SDF_CACHE_DIR / cache_name
        if cache_path.exists():
            try:
                cached = np.load(cache_path, mmap_mode="r", allow_pickle=False)
                return np.asarray(cached, dtype=np.float32)
            except Exception:
                pass
        built = _build_signed_sdf_mm(mask)
        if built is not None:
            try:
                np.save(cache_path, np.asarray(built, dtype=np.float32))
            except Exception as exc:
                log.warning(f"Failed to persist SDF cache: {exc}")
        return built
    except Exception as exc:
        log.warning(f"SDF cache fallback to direct build: {exc}")
        return _build_signed_sdf_mm(mask)


def _load_signed_sdf_from_cache_only(source_masks):
    """Fast-path cache lookup used during atlas loading to avoid long synchronous EDT builds."""
    if not SDF_CACHE_ENABLED:
        return None
    try:
        cache_name = _sdf_cache_key(source_masks)
        cache_path = SDF_CACHE_DIR / cache_name
        if not cache_path.exists():
            return None
        cached = np.load(cache_path, mmap_mode="r", allow_pickle=False)
        return np.asarray(cached, dtype=np.float32)
    except Exception as exc:
        log.warning(f"Failed to load cached SDF: {exc}")
        return None


def _start_async_sdf_build(mask, source_masks):
    """
    Build SDF in a background daemon thread so atlas loader can complete.
    The loaded atlas remains usable with KDTree risk distance while SDF warms up.
    """
    global risk_sdf_build_thread, risk_sdf_build_token, risk_signed_sdf_mm, risk_model_summary
    if not SDF_ASYNC_BUILD_ENABLED:
        return "disabled"

    with risk_model_lock:
        risk_sdf_build_token += 1
        token = int(risk_sdf_build_token)
        risk_model_summary["sdfBuildState"] = "building"
        risk_model_summary["sdfBuildMessage"] = "Building SDF in background"
        risk_model_summary["sdfBuildUpdatedAt"] = _utc_now_iso()

    def _worker(local_token, local_mask, local_source_masks):
        global risk_signed_sdf_mm, risk_model_summary
        try:
            built = _load_or_build_signed_sdf_mm(local_mask, local_source_masks)
            with risk_model_lock:
                if int(local_token) != int(risk_sdf_build_token):
                    return
                if built is None:
                    risk_model_summary["sdfBuildState"] = "failed"
                    risk_model_summary["sdfBuildMessage"] = "SDF build returned empty"
                    risk_model_summary["sdfBuildUpdatedAt"] = _utc_now_iso()
                    return
                risk_signed_sdf_mm = built
                risk_model_summary["sdfEnabled"] = True
                risk_model_summary["distanceModel"] = "trajectory_sdf_edt"
                risk_model_summary["sdfBuildState"] = "ready"
                risk_model_summary["sdfBuildMessage"] = "SDF ready"
                risk_model_summary["sdfBuildUpdatedAt"] = _utc_now_iso()
                log.info("Risk SDF async build completed")
        except Exception as exc:
            with risk_model_lock:
                if int(local_token) != int(risk_sdf_build_token):
                    return
                risk_model_summary["sdfBuildState"] = "failed"
                risk_model_summary["sdfBuildMessage"] = f"SDF build failed: {exc}"
                risk_model_summary["sdfBuildUpdatedAt"] = _utc_now_iso()
            log.warning(f"Risk SDF async build failed: {exc}")

    t = threading.Thread(
        target=_worker,
        args=(token, mask, source_masks),
        name=f"risk-sdf-builder-{token}",
        daemon=True,
    )
    risk_sdf_build_thread = t
    t.start()
    return "building"


def _sample_volume_trilinear(volume, points_vox):
    """
    Trilinear interpolation for dense scalar volume sampling.
    points_vox shape: [N, 3] in (i, j, k) voxel index space.
    """
    if volume is None:
        return None
    pts = np.asarray(points_vox, dtype=np.float64)
    if pts.size == 0:
        return np.asarray([], dtype=np.float64)
    shape = volume.shape
    i = np.clip(pts[:, 0], 0, shape[0] - 1.000001)
    j = np.clip(pts[:, 1], 0, shape[1] - 1.000001)
    k = np.clip(pts[:, 2], 0, shape[2] - 1.000001)

    i0 = np.floor(i).astype(np.int64)
    j0 = np.floor(j).astype(np.int64)
    k0 = np.floor(k).astype(np.int64)
    i1 = np.clip(i0 + 1, 0, shape[0] - 1)
    j1 = np.clip(j0 + 1, 0, shape[1] - 1)
    k1 = np.clip(k0 + 1, 0, shape[2] - 1)

    di = i - i0
    dj = j - j0
    dk = k - k0

    c000 = volume[i0, j0, k0]
    c001 = volume[i0, j0, k1]
    c010 = volume[i0, j1, k0]
    c011 = volume[i0, j1, k1]
    c100 = volume[i1, j0, k0]
    c101 = volume[i1, j0, k1]
    c110 = volume[i1, j1, k0]
    c111 = volume[i1, j1, k1]

    c00 = c000 * (1 - dk) + c001 * dk
    c01 = c010 * (1 - dk) + c011 * dk
    c10 = c100 * (1 - dk) + c101 * dk
    c11 = c110 * (1 - dk) + c111 * dk
    c0 = c00 * (1 - dj) + c01 * dj
    c1 = c10 * (1 - dj) + c11 * dj
    out = c0 * (1 - di) + c1 * di
    return np.asarray(out, dtype=np.float64)


def _threshold_segments_from_distances(dist_mm_series, threshold_mm, step_um):
    if dist_mm_series is None or len(dist_mm_series) == 0:
        return []
    segments = []
    start = None
    for i, dist_mm in enumerate(dist_mm_series):
        if float(dist_mm) <= float(threshold_mm):
            if start is None:
                start = i
            continue
        if start is not None:
            end = i - 1
            segments.append((start, end))
            start = None
    if start is not None:
        segments.append((start, len(dist_mm_series) - 1))

    out = []
    step_mm = float(step_um) / 1000.0
    for s_idx, e_idx in segments:
        out.append(
            {
                "startDepthMm": round(s_idx * step_mm, 6),
                "endDepthMm": round(e_idx * step_mm, 6),
                "sampleCount": int(e_idx - s_idx + 1),
            }
        )
    return out


def _build_swept_probe_samples_vox(traj_voxels, probe_radius_um, ring_samples=12):
    center = np.asarray(traj_voxels, dtype=np.float64)
    if center.ndim != 2 or center.shape[0] == 0 or center.shape[1] != 3:
        return center, np.zeros((0,), dtype=np.int32)
    radius_um = float(max(0.0, probe_radius_um))
    if radius_um <= 1e-9:
        owners = np.arange(center.shape[0], dtype=np.int32)
        return center, owners

    radius_vox = float(radius_um) / float(VOXEL_SIZE_UM)
    ring_samples = int(max(6, min(32, int(ring_samples or 12))))
    offsets = []
    owner = []
    eps = 1e-9
    angles = np.linspace(0.0, 2.0 * math.pi, num=ring_samples, endpoint=False, dtype=np.float64)

    for i in range(center.shape[0]):
        p = center[i]
        prev_p = center[max(0, i - 1)]
        next_p = center[min(center.shape[0] - 1, i + 1)]
        tangent = next_p - prev_p
        norm_t = float(np.linalg.norm(tangent))
        if norm_t <= eps:
            tangent = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        else:
            tangent = tangent / norm_t

        ref = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        if abs(float(np.dot(tangent, ref))) > 0.9:
            ref = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        n1 = np.cross(tangent, ref)
        norm_n1 = float(np.linalg.norm(n1))
        if norm_n1 <= eps:
            n1 = np.array([1.0, 0.0, 0.0], dtype=np.float64)
            norm_n1 = 1.0
        n1 = n1 / norm_n1
        n2 = np.cross(tangent, n1)
        norm_n2 = float(np.linalg.norm(n2))
        if norm_n2 <= eps:
            n2 = np.array([0.0, 0.0, 1.0], dtype=np.float64)
            norm_n2 = 1.0
        n2 = n2 / norm_n2

        offsets.append(p)
        owner.append(i)
        for ang in angles:
            circle_vec = math.cos(float(ang)) * n1 + math.sin(float(ang)) * n2
            offsets.append(p + radius_vox * circle_vec)
            owner.append(i)

    return np.asarray(offsets, dtype=np.float64), np.asarray(owner, dtype=np.int32)


def _reduce_series_by_owner(series, owner_idx, sample_count, reducer="min"):
    if series is None:
        return None
    arr = np.asarray(series, dtype=np.float64)
    owner = np.asarray(owner_idx, dtype=np.int64)
    if arr.size == 0 or owner.size == 0:
        return None
    if arr.size != owner.size:
        return None
    n = int(max(1, sample_count))
    if reducer == "max":
        out = np.full((n,), -np.inf, dtype=np.float64)
        np.maximum.at(out, owner, arr)
        out[~np.isfinite(out)] = 0.0
        return out
    out = np.full((n,), np.inf, dtype=np.float64)
    np.minimum.at(out, owner, arr)
    out[~np.isfinite(out)] = np.max(arr) if arr.size else 0.0
    return out


def _estimate_sdf_normal_at_voxel(sdf_arr, voxel_zyx):
    if not isinstance(sdf_arr, np.ndarray) or sdf_arr.ndim != 3:
        return None
    try:
        z, y, x = [int(round(float(v))) for v in voxel_zyx]
    except Exception:
        return None
    shape = sdf_arr.shape
    if not (0 <= z < shape[0] and 0 <= y < shape[1] and 0 <= x < shape[2]):
        return None
    z0, z1 = max(0, z - 1), min(shape[0] - 1, z + 1)
    y0, y1 = max(0, y - 1), min(shape[1] - 1, y + 1)
    x0, x1 = max(0, x - 1), min(shape[2] - 1, x + 1)
    dz = float(sdf_arr[z1, y, x] - sdf_arr[z0, y, x]) / max(1, z1 - z0)
    dy = float(sdf_arr[z, y1, x] - sdf_arr[z, y0, x]) / max(1, y1 - y0)
    dx = float(sdf_arr[z, y, x1] - sdf_arr[z, y, x0]) / max(1, x1 - x0)
    vec = np.asarray([dz, dy, dx], dtype=np.float64)
    norm = float(np.linalg.norm(vec))
    if norm <= 1e-9 or not np.isfinite(norm):
        return None
    return (vec / norm).astype(np.float64)


def _risk_distance_diagnostics_from_voxels(
    traj_voxels,
    step_um=RISK_DIAG_STEP_UM,
    threshold_mm=None,
    threshold_by_class=None,
    cvar_alpha=DRCC_CVAR_ALPHA_DEFAULT,
    probe_radius_um=0.0,
    swept_ring_samples=12,
):
    if not traj_voxels:
        return None
    with risk_model_lock:
        merged_tree = risk_surface_tree
        merged_points = risk_surface_points_um
        source_trees = dict(risk_surface_source_trees)
        source_points = dict(risk_surface_source_points_um)
        class_trees = dict(risk_surface_class_trees)
        class_points = dict(risk_surface_class_points_um)
        class_volume = risk_class_volume.copy() if isinstance(risk_class_volume, np.ndarray) else None
        signed_sdf = risk_signed_sdf_mm if isinstance(risk_signed_sdf_mm, np.ndarray) else None
        layered_sdf_channels = dict(risk_layered_sdf_channels_mm) if isinstance(risk_layered_sdf_channels_mm, dict) else {}
        summary = deepcopy(risk_model_summary)
    if not bool(summary.get("enabled")):
        return None
    class_coverage = summary.get("classCoverageVoxels") if isinstance(summary.get("classCoverageVoxels"), dict) else {}

    center_vox = np.asarray(traj_voxels, dtype=np.float64)
    if center_vox.size == 0:
        return None
    swept_vox, swept_owner = _build_swept_probe_samples_vox(
        center_vox,
        probe_radius_um=probe_radius_um,
        ring_samples=swept_ring_samples,
    )
    if swept_vox.size == 0:
        return None
    traj_points_um = swept_vox * float(VOXEL_SIZE_UM)
    sample_count = int(center_vox.shape[0])
    swept_enabled = float(probe_radius_um) > 1e-9
    nearest_surface_idx = None
    tree_min_idx = None
    tree_min_um = None
    dist_um_series = None
    if merged_tree is not None or merged_points is not None:
        tree_min_um, tree_min_idx, nearest_surface_idx, dist_um_series = _query_min_distance_um(
            traj_points_um,
            tree_obj=merged_tree,
            reference_points_um=merged_points,
        )

    signed_series_mm = None
    if signed_sdf is not None:
        signed_series_mm = _sample_volume_trilinear(signed_sdf, swept_vox)
        if signed_series_mm is not None:
            signed_series_mm = np.asarray(signed_series_mm, dtype=np.float64)

    if signed_series_mm is not None and signed_series_mm.size == swept_vox.shape[0]:
        reduced_signed = _reduce_series_by_owner(
            signed_series_mm,
            swept_owner,
            sample_count=sample_count,
            reducer="min",
        )
        if reduced_signed is None:
            return None
        signed_series_mm = np.asarray(reduced_signed, dtype=np.float64)
        unsigned_series_mm = np.where(signed_series_mm < 0.0, 0.0, signed_series_mm)
        risk_metric_series_mm = np.where(signed_series_mm < 0.0, signed_series_mm, unsigned_series_mm)
        traj_min_idx = int(np.argmin(risk_metric_series_mm))
        min_dist_mm = float(np.min(unsigned_series_mm))
    elif dist_um_series is not None:
        reduced_um = _reduce_series_by_owner(
            dist_um_series,
            swept_owner,
            sample_count=sample_count,
            reducer="min",
        )
        if reduced_um is None:
            return None
        unsigned_series_mm = np.asarray(reduced_um, dtype=np.float64) / 1000.0
        signed_series_mm = np.asarray(unsigned_series_mm, dtype=np.float64)
        traj_min_idx = int(np.argmin(unsigned_series_mm))
        min_dist_mm = float(np.min(unsigned_series_mm))
    elif tree_min_um is not None and tree_min_idx is not None:
        min_dist_mm = float(tree_min_um) / 1000.0
        traj_min_idx = int(tree_min_idx)
        unsigned_series_mm = np.full((len(traj_voxels),), min_dist_mm, dtype=np.float64)
        signed_series_mm = np.asarray(unsigned_series_mm, dtype=np.float64)
    else:
        return None

    depth_step_mm = float(step_um) / 1000.0
    cortical_entry_normal = None
    cortical_entry_angle_deg = None
    brain_surface_sdf = None
    for channel_key, channel_arr in layered_sdf_channels.items():
        if _risk_class_from_sdf_channel(channel_key) == "brain_surface":
            brain_surface_sdf = channel_arr
            break
    if brain_surface_sdf is not None and center_vox.shape[0] >= 2:
        normal_vec = _estimate_sdf_normal_at_voxel(brain_surface_sdf, center_vox[0])
        if normal_vec is not None:
            direction_vec = np.asarray(center_vox[1] - center_vox[0], dtype=np.float64)
            direction_norm = float(np.linalg.norm(direction_vec))
            if direction_norm > 1e-9:
                direction_vec = direction_vec / direction_norm
                dot_abs = float(abs(np.dot(normal_vec, direction_vec)))
                dot_abs = max(0.0, min(1.0, dot_abs))
                cortical_entry_normal = [round(float(v), 6) for v in normal_vec.tolist()]
                cortical_entry_angle_deg = round(float(math.degrees(math.acos(dot_abs))), 6)
    nearest_source = None
    nearest_source_dist_mm = None
    source_min_distance_mm = {}
    source_distance_series_mm = {}
    best_source_um = None

    source_keys = sorted(
        [
            key for key in (set(source_trees.keys()) | set(source_points.keys()))
            if key != "atlas_keywords"
        ]
    )
    for source_key in source_keys:
        s_min_um, _, _, s_dist_series = _query_min_distance_um(
            traj_points_um,
            tree_obj=source_trees.get(source_key),
            reference_points_um=source_points.get(source_key),
        )
        if s_min_um is None:
            continue
        s_mm = float(s_min_um) / 1000.0
        source_min_distance_mm[source_key] = round(s_mm, 6)
        if s_dist_series is not None:
            reduced_series = _reduce_series_by_owner(
                s_dist_series,
                swept_owner,
                sample_count=sample_count,
                reducer="min",
            )
            if reduced_series is not None:
                source_distance_series_mm[source_key] = np.asarray(reduced_series, dtype=np.float64) / 1000.0
        if best_source_um is None or s_min_um < best_source_um:
            best_source_um = float(s_min_um)
            nearest_source = source_key
            nearest_source_dist_mm = s_mm

    class_distance_series_mm = {}
    for channel_key, channel_arr in layered_sdf_channels.items():
        risk_class = _risk_class_from_sdf_channel(channel_key)
        if risk_class not in RISK_CLASS_CODE:
            continue
        try:
            if int(class_coverage.get(risk_class, 0) or 0) <= 0:
                continue
        except Exception:
            continue
        if not isinstance(channel_arr, np.ndarray):
            continue
        channel_series = _sample_volume_trilinear(channel_arr, swept_vox)
        if channel_series is None or channel_series.size != swept_vox.shape[0]:
            continue
        reduced_channel = _reduce_series_by_owner(
            np.asarray(channel_series, dtype=np.float64),
            swept_owner,
            sample_count=sample_count,
            reducer="min",
        )
        if reduced_channel is not None:
            class_distance_series_mm[risk_class] = np.asarray(reduced_channel, dtype=np.float64)

    for risk_class in sorted(set(class_trees.keys()) | set(class_points.keys())):
        if risk_class in class_distance_series_mm:
            continue
        cls_min_um, _, _, cls_dist_series = _query_min_distance_um(
            traj_points_um,
            tree_obj=class_trees.get(risk_class),
            reference_points_um=class_points.get(risk_class),
        )
        if cls_min_um is None or cls_dist_series is None:
            continue
        reduced_cls = _reduce_series_by_owner(
            cls_dist_series,
            swept_owner,
            sample_count=sample_count,
            reducer="min",
        )
        if reduced_cls is not None:
            class_distance_series_mm[risk_class] = np.asarray(reduced_cls, dtype=np.float64) / 1000.0

    if class_distance_series_mm:
        class_names = sorted(class_distance_series_mm.keys())
        class_stack = np.stack([class_distance_series_mm[name] for name in class_names], axis=1)
        nearest_class_idx = np.argmin(class_stack, axis=1)
        nearest_class_series = [class_names[int(i)] for i in nearest_class_idx.tolist()]
        distance_series_mm = np.asarray(signed_series_mm, dtype=np.float64)
    else:
        nearest_class_series = [classify_risk_source(nearest_source) for _ in range(len(traj_voxels))]
        distance_series_mm = np.asarray(signed_series_mm, dtype=np.float64)

    if class_volume is not None and swept_vox.shape[0]:
        vox_i = np.asarray(swept_vox, dtype=np.int64)
        ii = np.clip(vox_i[:, 0], 0, class_volume.shape[0] - 1)
        jj = np.clip(vox_i[:, 1], 0, class_volume.shape[1] - 1)
        kk = np.clip(vox_i[:, 2], 0, class_volume.shape[2] - 1)
        inside_codes = class_volume[ii, jj, kk]
        owner_codes = np.zeros((sample_count,), dtype=np.uint8)
        for idx, code in enumerate(inside_codes.tolist()):
            own = int(swept_owner[idx])
            if code and owner_codes[own] == 0:
                owner_codes[own] = int(code)
        for idx, code in enumerate(owner_codes.tolist()):
            cls_name = RISK_CODE_CLASS.get(int(code))
            if cls_name:
                nearest_class_series[idx] = cls_name

    nearest_bucket = (
        nearest_class_series[int(traj_min_idx)]
        if nearest_class_series
        else classify_risk_source(nearest_source)
    )
    if nearest_bucket is None:
        nearest_bucket = classify_risk_source(nearest_source)

    thresholds = {"default": float(threshold_mm) if threshold_mm is not None else MIN_RISK_DISTANCE_MM_DEFAULT}
    if isinstance(threshold_by_class, dict):
        for cls_name in RISK_CLASS_CODE:
            if cls_name in threshold_by_class:
                thresholds[cls_name] = float(threshold_by_class[cls_name])

    segments = []
    if threshold_mm is not None:
        segments = _threshold_segments_from_distances(distance_series_mm, threshold_mm, step_um=step_um)
    danger_segments = build_top_danger_segments(
        distance_series_mm=distance_series_mm,
        class_series=nearest_class_series,
        thresholds_mm=thresholds,
        step_um=step_um,
        top_k=3,
    )
    threshold_ref_mm = float(thresholds.get("default", MIN_RISK_DISTANCE_MM_DEFAULT))
    if nearest_class_series and len(nearest_class_series) == len(distance_series_mm):
        threshold_series_mm = np.asarray(
            [resolve_risk_threshold_mm(thresholds, cls) for cls in nearest_class_series],
            dtype=np.float64,
        )
    else:
        threshold_series_mm = np.full_like(np.asarray(distance_series_mm, dtype=np.float64), threshold_ref_mm)
    shortfall_series = np.maximum(
        0.0,
        threshold_series_mm - np.asarray(distance_series_mm, dtype=np.float64),
    )
    path_step_mm = float(step_um) / 1000.0
    risk_integral_shortfall_mm = float(np.sum(shortfall_series) * path_step_mm)
    cvar_alpha = float(max(0.5, min(0.99, cvar_alpha)))
    tail_count = max(1, int(math.ceil((1.0 - cvar_alpha) * max(1, shortfall_series.size))))
    sorted_sf = np.sort(shortfall_series)
    tail_vals = sorted_sf[-tail_count:] if sorted_sf.size else np.array([0.0], dtype=np.float64)
    risk_tail_shortfall_mm = float(np.mean(tail_vals)) if tail_vals.size else 0.0
    risk_max_shortfall_mm = float(np.max(shortfall_series)) if shortfall_series.size else 0.0

    return {
        "minDistanceMm": round(min_dist_mm, 6),
        "minSignedDistanceMm": round(float(np.min(signed_series_mm)), 6) if signed_series_mm.size else round(min_dist_mm, 6),
        "closestDepthMm": round(float(traj_min_idx) * depth_step_mm, 6),
        "closestVoxel": [int(v) for v in traj_voxels[int(traj_min_idx)]],
        "nearestSource": nearest_source,
        "nearestSourceClass": nearest_bucket,
        "nearestSourceDistanceMm": round(float(nearest_source_dist_mm), 6)
        if nearest_source_dist_mm is not None
        else None,
        "sourceMinDistanceMm": source_min_distance_mm,
        "classThresholdMm": {k: round(float(v), 6) for k, v in thresholds.items()},
        "classMinDistanceMm": {
            cls: round(float(np.min(series)), 6)
            for cls, series in class_distance_series_mm.items()
        },
        "thresholdSegmentsMm": segments,
        "dangerSegmentsTop3": danger_segments,
        "sampleCount": int(len(traj_voxels)),
        "riskModelSource": summary.get("source", []),
        "riskModelSourceTag": summary.get("sourceTag"),
        "riskModelMeta": summary.get("meta", {}),
        "riskDistanceModel": summary.get("distanceModel", "trajectory_sdf_kdtree"),
        "nearestSurfacePointIndex": int(nearest_surface_idx) if nearest_surface_idx is not None else None,
        "riskIntegralShortfallMm": round(risk_integral_shortfall_mm, 6),
        "riskTailShortfallMm": round(risk_tail_shortfall_mm, 6),
        "riskMaxShortfallMm": round(risk_max_shortfall_mm, 6),
        "pathLengthMm": round(float(path_step_mm * max(0, len(traj_voxels) - 1)), 6),
        "riskCvarAlpha": round(cvar_alpha, 6),
        "corticalEntryNormalZyx": cortical_entry_normal,
        "corticalEntryAngleDeg": cortical_entry_angle_deg,
        "corticalEntryNormalSource": "brain_surface_sdf_mm" if cortical_entry_normal is not None else None,
        "collisionModel": "swept_cylinder" if swept_enabled else "centerline",
        "probeRadiusUm": round(float(max(0.0, probe_radius_um)), 6),
        "probeRadiusMm": round(float(max(0.0, probe_radius_um)) / 1000.0, 6),
        "sweptRingSamples": int(max(1, int(swept_ring_samples))),
        "sweptSampleCount": int(swept_vox.shape[0]),
    }


def _risk_distance_from_voxels_mm(traj_voxels):
    diag = _risk_distance_diagnostics_from_voxels(traj_voxels, step_um=RISK_DIAG_STEP_UM, threshold_mm=None)
    if not diag:
        return None
    return float(diag.get("minDistanceMm", 0.0))


def _compute_probe_min_risk_distance_mm(probe, step_um=25.0):
    diag = _compute_probe_risk_diagnostics(probe, step_um=step_um, threshold_mm=None)
    if not diag:
        return None
    return float(diag.get("minDistanceMm", 0.0))


def _compute_probe_risk_diagnostics(
    probe,
    step_um=RISK_DIAG_STEP_UM,
    threshold_mm=None,
    threshold_by_class=None,
    cvar_alpha=DRCC_CVAR_ALPHA_DEFAULT,
):
    ap = float(probe.get("ap_mm_corrected", probe.get("ap_mm", 0.0)))
    ml = float(probe.get("ml_mm_corrected", probe.get("ml_mm", 0.0)))
    theta = float(probe.get("theta_deg", 0.0))
    phi = float(probe.get("phi_deg", 0.0))
    depth_um = float(probe.get("depthUm_corrected", probe.get("depthUm", 10000.0)))
    skull_tilt = float(probe.get("skullTiltDeg", 0.0))
    entry_dv = _probe_entry_dv_mm(probe)
    probe_radius_um = float(
        probe.get(
            "radiusUm",
            probe.get(
                "probeRadiusUm",
                max(5.0, float(probe.get("shankWidthUm", 70.0)) / 2.0),
            ),
        )
    )
    swept_ring_samples = int(max(3, int(probe.get("sweptRingSamples", 12) or 12)))
    threshold_payload = sanitize_risk_thresholds(
        threshold_by_class,
        default_mm=float(threshold_mm if threshold_mm is not None else MIN_RISK_DISTANCE_MM_DEFAULT),
    )
    cache_key = (
        _trajectory_cache_key(ap, ml, theta, phi, depth_um, skull_tilt, step_um, entry_dv),
        round(float(probe_radius_um), 5),
        swept_ring_samples,
        round(float(cvar_alpha), 6),
        tuple(sorted((str(k), round(float(v), 6)) for k, v in (threshold_payload or {}).items())),
    )
    cached_diag = _bounded_cache_get(risk_diag_cache, risk_diag_cache_lock, cache_key)
    if isinstance(cached_diag, dict):
        return deepcopy(cached_diag)

    voxels = sample_trajectory_voxels_cached(ap, ml, theta, phi, depth_um, skull_tilt,
                                           step_um=step_um, entry_dv_corr_mm=entry_dv)
    diag = _risk_distance_diagnostics_from_voxels(
        voxels,
        step_um=step_um,
        threshold_mm=threshold_mm,
        threshold_by_class=threshold_payload,
        cvar_alpha=cvar_alpha,
        probe_radius_um=probe_radius_um,
        swept_ring_samples=swept_ring_samples,
    )
    if isinstance(diag, dict):
        _bounded_cache_put(
            risk_diag_cache,
            risk_diag_cache_lock,
            cache_key,
            deepcopy(diag),
            RISK_DIAG_CACHE_MAX,
        )
    return diag


def _continuous_risk_policy(constraint_modes):
    explicit_mode = isinstance(constraint_modes, dict) and "continuousRiskDistance" in constraint_modes
    # continuousRiskDistance is a high-sensitivity model constraint. Missing
    # values fail closed by default; legacy/demo clients must explicitly request
    # warn/off if they need non-blocking risk diagnostics.
    requested = constraint_modes.get("continuousRiskDistance", "enforce")
    if requested not in CONSTRAINT_MODES:
        requested = "enforce"
    strict_requested = requested == "enforce"

    with risk_model_lock:
        summary = deepcopy(risk_model_summary)
    with atlas_lock:
        load_snapshot = deepcopy(atlas_load_state)
    loader_alive = _atlas_loader_alive()
    load_state = str(load_snapshot.get("state") or "").strip().lower()
    load_phase = str(load_snapshot.get("phase") or "").strip().lower()
    risk_enabled = bool(summary.get("enabled"))
    sources = set(summary.get("source") or [])
    external_sources = sorted([src for src in sources if src in RISK_MASK_ENV])
    keyword_sources = sorted(
        [
            src for src in sources
            if src == "atlas_keywords" or str(src).startswith("atlas_keywords:")
        ]
    )
    source_tag = str(summary.get("sourceTag") or "").strip().lower()
    keyword_only = (
        source_tag == "atlas_keywords_only"
        or (bool(keyword_sources) and not external_sources and len(keyword_sources) == len(sources))
    )
    risk_initializing = bool(
        not risk_enabled
        and (
            (loader_alive and load_state in ("loading", "running"))
            or load_phase in ("risk_model", "sdf", "sdf_build", "mesh_warmup")
            or str(summary.get("sdfBuildState") or "").strip().lower() in ("building", "initializing", "loading")
        )
    )
    if risk_initializing and not explicit_mode and not strict_requested:
        downgraded = bool(requested == "enforce")
        effective = "warn" if requested == "enforce" else requested
        reason = "risk_model_initializing"
    elif risk_initializing:
        downgraded = False
        effective = requested
        reason = "risk_model_initializing_explicit_mode" if explicit_mode else "risk_model_initializing_default_enforce"
    elif not risk_enabled and not explicit_mode and not strict_requested:
        downgraded = bool(requested != "off")
        effective = "off"
        reason = "risk_model_disabled"
    elif not risk_enabled:
        downgraded = False
        effective = requested
        reason = "risk_model_disabled_explicit_mode" if explicit_mode else "risk_model_disabled_default_enforce"
    else:
        source_limited = bool(
            keyword_only and not explicit_mode and not strict_requested and not ALLOW_KEYWORD_ONLY_RISK_ENFORCE
        )
        downgraded = bool(
            requested == "enforce"
            and source_limited
        )
        effective = "warn" if downgraded else requested
        reason = (
            "atlas_keywords_only"
            if source_limited
            else "strict_override"
            if requested == "enforce" and keyword_only and (explicit_mode or ALLOW_KEYWORD_ONLY_RISK_ENFORCE)
            else "default_enforce"
            if requested == "enforce" and keyword_only
            else "default"
        )
    return {
        "constraint": "continuousRiskDistance",
        "requestedMode": requested,
        "effectiveMode": effective,
        "downgraded": downgraded,
        "explicitMode": bool(explicit_mode),
        "reason": reason,
        "sourceLimited": bool(
            keyword_only and not explicit_mode and not strict_requested and not ALLOW_KEYWORD_ONLY_RISK_ENFORCE
        ),
        "riskModelEnabled": risk_enabled,
        "riskModelSources": sorted(sources),
        "externalRiskSources": external_sources,
        "keywordRiskSources": keyword_sources,
        "riskSourceTag": source_tag or None,
        "riskModelInitializing": bool(risk_initializing),
    }


def _build_degradation_notices(
    *,
    constraint_modes=None,
    atlas_loaded_snapshot=None,
    atlas_load_snapshot=None,
    atlas_error_snapshot=None,
    qiu_payload=None,
    mri_payload=None,
):
    """
    Build user-facing, machine-readable notices so frontend can avoid black-box states.
    """
    with risk_model_lock:
        risk_snapshot = deepcopy(risk_model_summary)
    quality_snapshot = deepcopy(reconstruction_quality_state)
    risk_policy = _continuous_risk_policy(constraint_modes or DEFAULT_CONSTRAINT_MODES)
    risk_initializing = bool(risk_policy.get("riskModelInitializing"))

    if atlas_loaded_snapshot is None:
        with atlas_lock:
            atlas_loaded_snapshot = bool(atlas_loaded)
            atlas_load_snapshot = deepcopy(atlas_load_state)
            atlas_error_snapshot = atlas_error

    notices = []

    def _push(
        code,
        level,
        title_en,
        title_cn,
        msg_en,
        msg_cn,
        action_en="",
        action_cn="",
        reason_en="",
        reason_cn="",
        impact_en="",
        impact_cn="",
        recovery_en="",
        recovery_cn="",
    ):
        notices.append(
            {
                "id": str(code),
                "code": str(code),
                "level": str(level),
                "titleEn": str(title_en),
                "titleCn": str(title_cn),
                "messageEn": str(msg_en),
                "messageCn": str(msg_cn),
                "actionEn": str(action_en or ""),
                "actionCn": str(action_cn or ""),
                "reasonEn": str(reason_en or msg_en or ""),
                "reasonCn": str(reason_cn or msg_cn or ""),
                "impactEn": str(impact_en or ""),
                "impactCn": str(impact_cn or ""),
                "recoveryEn": str(recovery_en or action_en or ""),
                "recoveryCn": str(recovery_cn or action_cn or ""),
                "updatedAt": _utc_now_iso(),
            }
        )

    if not bool(risk_snapshot.get("enabled")) and risk_initializing:
        _push(
            "risk_model_initializing",
            "info",
            "Risk model is initializing",
            "风险模型初始化中",
            "Risk model is still initializing. Planning can proceed, but continuous risk-distance checks are temporarily relaxed.",
            "风险模型仍在初始化。规划可继续，但连续风险距离约束会暂时放宽。",
            action_en="Wait for risk stage to turn ready before interpreting strict safety margins.",
            action_cn="请等待风险阶段变为就绪后，再解读严格安全裕度结果。",
            reason_en="Atlas/risk initialization is still running.",
            reason_cn="图谱/风险初始化尚未完成。",
            impact_en="Temporary downgrade avoids false blocking during model warm-up.",
            impact_cn="初始化期间临时降级可避免误阻断。",
            recovery_en="No manual action required; backend will switch to strict mode when risk model is ready.",
            recovery_cn="通常无需手动操作；风险模型就绪后系统会自动切回严格模式。",
        )
    elif not bool(risk_snapshot.get("enabled")):
        detail = str(risk_snapshot.get("error") or "risk model unavailable")
        _push(
            "risk_model_disabled",
            "warning",
            "Risk model disabled, planner stays available",
            "风险模型已关闭，主规划功能保持可用",
            f"Continuous risk model is disabled ({detail}). Planning can continue without risk-distance enforcement.",
            f"连续风险模型当前不可用（{detail}），系统已自动降级为可继续规划，但不再执行连续风险距离约束。",
            action_en="Check risk masks / memory, then reconnect backend to restore risk constraints.",
            action_cn="建议检查风险掩膜/内存后重连后端，以恢复连续风险约束。",
            reason_en=f"Risk model initialization failed or was degraded ({detail}).",
            reason_cn=f"风险模型初始化失败或已降级（{detail}）。",
            impact_en="Continuous risk-distance contract is disabled; optimization remains available but risk screening is weakened.",
            impact_cn="连续风险距离约束已停用；仍可优化，但风险筛查能力下降。",
            recovery_en="Validate risk masks and memory, then restart/reconnect backend.",
            recovery_cn="核对风险掩膜与内存资源后，重启/重连后端。",
        )

    if bool(risk_policy.get("downgraded")):
        req_mode = str(risk_policy.get("requestedMode") or "warn")
        eff_mode = str(risk_policy.get("effectiveMode") or "warn")
        reason = str(risk_policy.get("reason") or "unknown")
        level = "info" if reason.startswith("risk_model_initializing") else "warning"
        _push(
            "continuous_risk_mode_downgraded",
            level,
            "Continuous-risk constraint downgraded",
            "连续风险约束已降级",
            f"Requested mode '{req_mode}' was adjusted to '{eff_mode}' (reason: {reason}).",
            f"连续风险约束已从“{req_mode}”自动调整为“{eff_mode}”（原因：{reason}）。",
            action_en="Review safety panel and risk-model status before clinical interpretation.",
            action_cn="请先核对安全约束面板和风险模型状态，再进行结果解释。",
            reason_en=f"Requested mode '{req_mode}' is not satisfiable under current risk model state ({reason}).",
            reason_cn=f"当前风险模型状态下无法满足请求模式“{req_mode}”（{reason}）。",
            impact_en=f"Constraint mode is auto-adjusted to '{eff_mode}', changing safety strictness.",
            impact_cn=f"约束模式被自动调整为“{eff_mode}”，会改变安全性严格程度。",
            recovery_en="Restore structured risk model and set desired constraint mode explicitly if needed.",
            recovery_cn="恢复结构化风险模型后，再按需显式设置目标约束模式。",
        )

    if bool(risk_snapshot.get("keywordFallbackUsed")) and not bool(risk_snapshot.get("structuredMaskEnabled")):
        _push(
            "risk_keyword_only",
            "info",
            "Risk source fallback to atlas keywords",
            "风险源回退为 Atlas 关键词",
            "Only atlas keyword-derived risk masks are active; structured vessel/ventricle/sulcus/brain-surface masks are missing.",
            "当前仅启用 Atlas 关键词风险掩膜，未检测到结构化 vessel/ventricle/sulcus/brain-surface 掩膜。",
            action_en="Provide structured risk masks for stronger and more interpretable safety distance.",
            action_cn="建议提供结构化风险掩膜，以获得更可靠、可解释的安全距离评估。",
            reason_en="Structured masks are unavailable, so backend falls back to atlas-keyword masks.",
            reason_cn="结构化掩膜缺失，后端已回退到 atlas 关键词掩膜。",
            impact_en="Safety distance remains available but interpretability and specificity are reduced.",
            impact_cn="仍可计算安全距离，但可解释性与结构特异性降低。",
            recovery_en="Import vessel/ventricle/sulcus/brain-surface masks and rebuild risk model.",
            recovery_cn="导入 vessel/ventricle/sulcus/brain-surface 掩膜并重建风险模型。",
        )

    missing_risk_classes = [
        str(x) for x in (risk_snapshot.get("missingStructuredRiskClasses") or [])
        if str(x) in ("vessel", "sulcus")
    ]
    if missing_risk_classes and bool(risk_snapshot.get("enabled")):
        missing_text = ", ".join(missing_risk_classes)
        _push(
            "risk_structured_layers_partial",
            "info",
            "Structured risk field is partial",
            "结构化风险场不完整",
            f"Structured SDF is active, but these risk layers are not usable yet: {missing_text}.",
            f"结构化 SDF 已启用，但以下风险层尚不可用：{missing_text}。",
            action_en="Provide real vessel/sulcus masks (for example MRA-derived vessel mask) and rebuild layered_sdf.npz.",
            action_cn="提供真实 vessel/sulcus 掩膜（例如 MRA 血管掩膜）并重建 layered_sdf.npz。",
            reason_en=f"Layer channels are missing or empty: {missing_text}.",
            reason_cn=f"风险层通道缺失或为空：{missing_text}。",
            impact_en="Planning still uses available structures, but vessel/sulcus-specific safety evidence is incomplete.",
            impact_cn="规划仍会使用现有结构，但血管/脑沟特异性安全证据不完整。",
            recovery_en="Rebuild MRI profile with vessel and sulcus masks, then restart/reload backend.",
            recovery_cn="使用 vessel 与 sulcus 掩膜重建 MRI profile 后重启/重载后端。",
        )

    if (
        ACTIVE_ATLAS_MODE in ("qiu2018", "mri_ccf_hybrid")
        and bool(quality_snapshot.get("gateEnabled"))
        and str(quality_snapshot.get("source") or "").lower() == "unavailable"
    ):
        _push(
            "reconstruction_quality_unavailable",
            "info",
            "Reconstruction quality metrics unavailable",
            "重建质量指标不可用",
            "Quality gate is enabled but Dice/HD95/TRE/Jacobian metrics are unavailable in current session.",
            "质量门禁已启用，但当前会话未提供 Dice/HD95/TRE/Jacobian 指标。",
            action_en="Import stats/jacobian to enable full quality gate evidence.",
            action_cn="建议提供 stats/jacobian 数据以启用完整质量门禁证据。",
            reason_en="Current reconstruction session lacks stats/jacobian artifacts required by quality gate.",
            reason_cn="当前重建会话缺少质量门禁所需的 stats/jacobian 工件。",
            impact_en="Gate evidence is incomplete and quality conclusions are weaker.",
            impact_cn="门禁证据不完整，质量结论可信度下降。",
            recovery_en="Provide stats.json and jacobian volume, then reload atlas session.",
            recovery_cn="提供 stats.json 与 jacobian 数据后重新加载 atlas 会话。",
        )

    if bool(quality_snapshot.get("gateEnabled")) and not bool(quality_snapshot.get("passed", True)):
        reasons = quality_snapshot.get("blockingReasons") or []
        reason_text = "; ".join(str(r) for r in reasons) if reasons else "quality gate failed"
        _push(
            "reconstruction_quality_blocking",
            "warning",
            "Reconstruction quality gate blocks planning",
            "重建质量门禁阻断规划",
            f"Planning is blocked by quality gate: {reason_text}.",
            f"规划被重建质量门禁阻断：{reason_text}。",
            action_en="Fix registration quality or relax thresholds explicitly.",
            action_cn="请修复配准质量，或在有依据的前提下显式调整阈值。",
            reason_en=f"Quality gate thresholds are violated: {reason_text}.",
            reason_cn=f"质量门禁阈值被触发：{reason_text}。",
            impact_en="Optimization is blocked to prevent low-quality reconstruction from entering clinical planning.",
            impact_cn="为防止低质量重建进入临床规划，优化已被阻断。",
            recovery_en="Improve registration/segmentation quality, then rerun with validated metrics.",
            recovery_cn="先提升配准/分割质量并通过指标验证，再重新规划。",
        )
    elif bool(quality_snapshot.get("gateEnabled")) and bool(quality_snapshot.get("nonBlockingReasons")):
        warnings = quality_snapshot.get("nonBlockingReasons") or []
        warning_text = "; ".join(str(r) for r in warnings[:3]) if warnings else "proxy quality evidence"
        _push(
            "reconstruction_quality_proxy_warning",
            "warning",
            "Reconstruction quality uses proxy evidence",
            "重建质量使用代理证据",
            f"Quality thresholds are exceeded, but current evidence is proxy/incomplete and will not hard-block planning: {warning_text}.",
            f"部分质量指标超过阈值，但当前证据为代理或不完整证据，因此不会硬阻断规划：{warning_text}。",
            action_en="Use the result for engineering validation only until true landmarks/masks/SyN Jacobian are provided.",
            action_cn="在提供真实 landmarks/masks/SyN Jacobian 前，仅将结果用于工程验证。",
            reason_en=f"Non-blocking quality evidence: {warning_text}.",
            reason_cn=f"非阻断质量证据：{warning_text}。",
            impact_en="Planning remains available, but MRI-CCF QC confidence is not sufficient for clinical claims.",
            impact_cn="规划功能保持可用，但 MRI-CCF 质控可信度不足以支撑临床结论。",
            recovery_en="Provide ground-truth masks, paired landmarks, or a real deformable warp-derived Jacobian and rebuild stats.",
            recovery_cn="提供真实掩膜、配对 landmarks 或真实非线性形变场 Jacobian，并重建 stats。",
        )

    if ACTIVE_ATLAS_MODE == "qiu2018":
        qiu = qiu_payload or {}
        if not bool(qiu.get("annotationConfigured")):
            _push(
                "qiu_annotation_missing",
                "warning",
                "Qiu2018 annotation not configured",
                "Qiu2018 annotation 未配置",
                "Qiu2018 mode is active but annotation path is missing.",
                "当前已切换到 Qiu2018 模式，但 annotation 路径未配置。",
                action_en="Configure PROBEPLANNER_QIU2018_ANNOTATION_PATH and restart backend.",
                action_cn="请配置 PROBEPLANNER_QIU2018_ANNOTATION_PATH 后重启后端。",
                reason_en="Required annotation volume is missing for Qiu2018 mode.",
                reason_cn="Qiu2018 模式缺少必需的 annotation 体数据。",
                impact_en="Atlas cannot initialize and region-level planning remains unavailable.",
                impact_cn="Atlas 无法完成初始化，脑区级规划不可用。",
                recovery_en="Set Qiu2018 annotation path and restart backend process.",
                recovery_cn="配置 Qiu2018 annotation 路径后重启后端进程。",
            )
    if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid":
        mri = mri_payload or {}
        if not bool(mri.get("annotationConfigured")):
            _push(
                "mri_annotation_missing",
                "warning",
                "MRI hybrid annotation not configured",
                "MRI 混合模式 annotation 未配置",
                "MRI-CCF hybrid mode is active but annotation path is missing.",
                "当前已切换到 MRI-CCF 混合模式，但 annotation 路径未配置。",
                action_en="Configure PROBEPLANNER_MRI_CCF_ANNOTATION_PATH and restart backend.",
                action_cn="请配置 PROBEPLANNER_MRI_CCF_ANNOTATION_PATH 后重启后端。",
                reason_en="Required annotation volume is missing for MRI+CCF hybrid mode.",
                reason_cn="MRI+CCF 混合模式缺少必需的 annotation 体数据。",
                impact_en="Hybrid atlas cannot activate and MRI-assisted planning/visualization remains limited.",
                impact_cn="混合图谱无法激活，MRI 辅助规划/可视化受限。",
                recovery_en="Set MRI annotation path and restart backend process.",
                recovery_cn="配置 MRI annotation 路径后重启后端进程。",
            )
        missing_artifacts = list(mri.get("missingArtifacts") or [])
        if not bool(mri.get("artifactReady", True)) and missing_artifacts:
            missing_text = ", ".join(str(x) for x in missing_artifacts)
            _push(
                "mri_artifact_incomplete",
                "warning",
                "MRI artifact contract incomplete",
                "MRI 工件契约不完整",
                f"MRI artifact contract is not ready. Missing: {missing_text}.",
                f"MRI 工件契约尚未就绪，缺失项：{missing_text}。",
                action_en="Fill missing files in artifact_manifest.json and retry /api/mri/activate.",
                action_cn="补齐 artifact_manifest.json 缺失文件后，重试 /api/mri/activate。",
                reason_en=f"Current MRI profile is missing required artifacts: {missing_text}.",
                reason_cn=f"当前 MRI profile 缺少必需工件：{missing_text}。",
                impact_en="MRI hybrid atlas may load partially and quality/risk checks can degrade.",
                impact_cn="MRI 混合图谱可能仅部分可用，质量/风险检查会降级。",
                recovery_en="Run scripts/run_mri_ccf_pipeline.py --activate to regenerate and activate profile.",
                recovery_cn="运行 scripts/run_mri_ccf_pipeline.py --activate 重新生成并激活 profile。",
            )
        consistency = mri.get("pathConsistency") if isinstance(mri.get("pathConsistency"), dict) else {}
        mismatches = list(consistency.get("mismatches") or [])
        if mismatches:
            preview = "; ".join(
                f"{m.get('artifact')}:{m.get('kind')}" for m in mismatches[:4] if isinstance(m, dict)
            )
            _push(
                "mri_profile_path_mismatch",
                "warning",
                "MRI profile paths are inconsistent",
                "MRI profile 路径不一致",
                f"Manifest/stats/runtime paths do not agree ({preview}).",
                f"manifest、stats 与运行时路径不一致（{preview}）。",
                action_en="Activate one manifest-based profile and restart/reload atlas.",
                action_cn="请只激活一个基于 manifest 的 profile，然后重启或重载 atlas。",
                reason_en="Mixed artifact paths can silently combine masks/stats from different MRI profiles.",
                reason_cn="路径混用会把不同 MRI profile 的 mask/stats 悄悄混在一起。",
                impact_en="Quality gates, risk fields and planning probabilities may describe different volumes.",
                impact_cn="质量门禁、风险场和规划概率可能对应不同体数据。",
                recovery_en="Set PROBEPLANNER_MRI_CCF_MANIFEST_PATH to the intended artifact_manifest.json.",
                recovery_cn="将 PROBEPLANNER_MRI_CCF_MANIFEST_PATH 指向目标 artifact_manifest.json。",
            )

        jac_summary = (
            quality_snapshot.get("jacobianSummary")
            if isinstance(quality_snapshot.get("jacobianSummary"), dict)
            else {}
        )
        if jac_summary and not bool(jac_summary.get("realJacobianEvidence", True)):
            _push(
                "mri_jacobian_placeholder",
                "warning",
                "Jacobian evidence is constant",
                "Jacobian 证据为恒定占位",
                "Jacobian determinant is constant; local deformation QC is not yet backed by a real ANTs/SyN warp.",
                "Jacobian determinant 为恒定值；局部形变 QC 尚未由真实 ANTs/SyN 形变场支撑。",
                action_en="Generate Jacobian determinant from the deformable warp and rebuild stats.",
                action_cn="请从 deformable warp 生成真实 Jacobian determinant，并重建 stats。",
                reason_en="Constant Jacobian usually means identity/placeholder deformation evidence.",
                reason_cn="恒定 Jacobian 通常表示 identity/占位形变证据。",
                impact_en="Jacobian anomaly ratio cannot identify local over-warping; registration QC is incomplete.",
                impact_cn="Jacobian 异常比例无法识别局部过度形变；配准 QC 不完整。",
                recovery_en="Run scripts/build_real_jacobian_from_warp.py --warp-image <warp.nii.gz> --output-jacobian <jacobian.npy>.",
                recovery_cn="运行 scripts/build_real_jacobian_from_warp.py --warp-image <warp.nii.gz> --output-jacobian <jacobian.npy>。",
            )

    if atlas_error_snapshot:
        _push(
            "atlas_error",
            "error",
            "Atlas loading error",
            "图谱加载错误",
            str(atlas_error_snapshot),
            str(atlas_error_snapshot),
            action_en="Inspect backend logs and retry atlas loading.",
            action_cn="请查看后端日志并重试 atlas 加载。",
            reason_en=str(atlas_error_snapshot),
            reason_cn=str(atlas_error_snapshot),
            impact_en="Atlas-dependent 3D visualization and planning are unavailable.",
            impact_cn="依赖 atlas 的三维显示与规划当前不可用。",
            recovery_en="Inspect backend log stack trace, fix dependency/path issues, then reload atlas.",
            recovery_cn="查看后端堆栈日志，修复依赖或路径问题后重载 atlas。",
        )
    elif not bool(atlas_loaded_snapshot):
        load_phase = ""
        load_msg = ""
        if isinstance(atlas_load_snapshot, dict):
            load_phase = str(atlas_load_snapshot.get("phase") or "")
            load_msg = str(atlas_load_snapshot.get("message") or "")
        _push(
            "atlas_loading",
            "info",
            "Atlas is loading",
            "图谱加载中",
            f"Current phase: {load_phase or 'loading'}; detail: {load_msg or 'starting'}",
            f"当前阶段：{load_phase or '加载中'}；详情：{load_msg or '正在启动'}",
            action_en="Wait until backend status becomes ready.",
            action_cn="请等待后端状态变为 ready。",
            reason_en=f"Backend atlas bootstrap is in progress ({load_phase or 'loading'}).",
            reason_cn=f"后端 atlas 正在启动加载（{load_phase or '加载中'}）。",
            impact_en="Region catalog and planning readiness are not final yet.",
            impact_cn="脑区目录与规划就绪状态尚未完成。",
            recovery_en="Wait for phase completion, or use retry endpoint if loading stalls.",
            recovery_cn="等待阶段完成；若长时间停滞可使用重试接口。",
        )

    return notices


def _latest_mri_task_snapshot():
    with mri_reconstruct_lock:
        if not mri_reconstruct_tasks:
            return None
        latest = max(
            mri_reconstruct_tasks.values(),
            key=lambda x: str(x.get("updatedAt") or x.get("createdAt") or ""),
        )
        return _public_mri_task_snapshot(latest)


def _workflow_status_from_atlas(atlas_loaded_snapshot, atlas_load_snapshot, atlas_error_snapshot):
    load = atlas_load_snapshot if isinstance(atlas_load_snapshot, dict) else {}
    progress = _normalize_progress_01(load.get("progress", 0.0), 0.0)
    state = str(load.get("state") or "").strip().lower()
    stage = str(load.get("phase") or state or "queued")
    msg = str(load.get("message") or "").strip()
    if atlas_error_snapshot:
        return _build_workflow_entry(
            status=TASK_STATE_FAILED,
            stage=stage or "failed",
            progress=1.0,
            message=str(atlas_error_snapshot),
            updated_at=load.get("updatedAt"),
            extra={"error": str(atlas_error_snapshot)},
        )
    if bool(atlas_loaded_snapshot):
        return _build_workflow_entry(
            status=TASK_STATE_COMPLETED,
            stage="completed",
            progress=1.0,
            message=msg or "Atlas ready",
            updated_at=load.get("updatedAt"),
        )
    if state in ("error", "failed"):
        return _build_workflow_entry(
            status=TASK_STATE_FAILED,
            stage=stage or "failed",
            progress=progress,
            message=msg or "Atlas loading failed",
            updated_at=load.get("updatedAt"),
        )
    if state in ("degraded", "partial"):
        return _build_workflow_entry(
            status=TASK_STATE_PARTIAL,
            stage=stage or "loading",
            progress=progress,
            message=msg or "Atlas loading degraded",
            updated_at=load.get("updatedAt"),
        )
    if state in ("idle", "queued", "") and progress <= 0.0:
        return _build_workflow_entry(
            status=TASK_STATE_QUEUED,
            stage=stage or "queued",
            progress=progress,
            message=msg or "Queued",
            updated_at=load.get("updatedAt"),
        )
    return _build_workflow_entry(
        status=TASK_STATE_RUNNING,
        stage=stage or "running",
        progress=progress,
        message=msg or "Atlas loading",
        updated_at=load.get("updatedAt"),
    )


def _workflow_status_from_registration(mri_payload=None):
    with workflow_state_lock:
        row = deepcopy(registration_runtime_state)
    runtime_status = normalize_task_status(row.get("status"), TASK_STATE_QUEUED)
    runtime_stage = str(row.get("stage") or "").strip().lower()
    runtime_progress = _normalize_progress_01(row.get("progress", 0.0), 0.0)
    runtime_has_active_state = (
        runtime_status in (TASK_STATE_RUNNING, TASK_STATE_FAILED, TASK_STATE_PARTIAL)
        or (runtime_status == TASK_STATE_COMPLETED and runtime_progress > 0.0)
    ) and runtime_stage not in ("", "idle", "queued")
    if runtime_has_active_state:
        return _build_workflow_entry(
            status=runtime_status,
            stage=row.get("stage", "queued"),
            progress=runtime_progress,
            message=row.get("message", ""),
            updated_at=row.get("updatedAt"),
            extra={
                "lastError": row.get("lastError"),
                "lastResult": row.get("lastResult"),
            },
        )

    # Most sessions load an already-built MRI+CCF profile. In that case there
    # is no live registration task to run, but a valid registration_summary
    # artifact is still definitive evidence that the registration stage is done.
    if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid":
        payload = mri_payload if isinstance(mri_payload, dict) else _mri_payload()
        registration_path = payload.get("registrationSummaryPath")
        registration_ready = bool(payload.get("registrationSummaryConfigured")) or bool(
            registration_path and _path_exists(registration_path)
        )
        if registration_ready:
            return _build_workflow_entry(
                status=TASK_STATE_COMPLETED,
                stage="completed",
                progress=1.0,
                message="Registration summary ready",
                updated_at=row.get("updatedAt"),
                extra={
                    "offlineArtifact": True,
                    "profileId": payload.get("profileId") or payload.get("datasetProfile"),
                    "registrationSummaryPath": registration_path,
                },
            )
        missing = list(payload.get("missingArtifacts") or [])
        message = "Registration summary missing"
        if missing:
            message = f"Registration summary missing; missing artifacts: {', '.join(str(x) for x in missing)}"
        return _build_workflow_entry(
            status=TASK_STATE_PARTIAL if payload.get("annotationConfigured") else TASK_STATE_QUEUED,
            stage="missing_registration_summary",
            progress=0.0,
            message=message,
            updated_at=row.get("updatedAt"),
            extra={
                "offlineArtifact": True,
                "profileId": payload.get("profileId") or payload.get("datasetProfile"),
                "missingArtifacts": missing,
            },
        )

    # Allen/Qiu modes do not require a separate MRI-to-CCF registration stage.
    return _build_workflow_entry(
        status=TASK_STATE_COMPLETED,
        stage="not_applicable",
        progress=1.0,
        message="Registration not required for current atlas mode",
        updated_at=row.get("updatedAt"),
        extra={
            "notApplicable": True,
            "activeAtlasMode": ACTIVE_ATLAS_MODE,
        },
    )


def _workflow_status_from_risk(risk_snapshot):
    snap = risk_snapshot if isinstance(risk_snapshot, dict) else {}
    state = str(snap.get("state") or snap.get("sdfBuildState") or "").strip().lower()
    message = str(snap.get("sdfBuildMessage") or snap.get("state") or "").strip()
    if bool(snap.get("enabled")) is False and state in ("building", "initializing", "loading", "starting", "pending"):
        return _build_workflow_entry(
            status=TASK_STATE_RUNNING,
            stage=state or "building",
            progress=_normalize_progress_01(snap.get("progress", 0.6), 0.6),
            message=message or "risk model initializing",
            updated_at=snap.get("sdfBuildUpdatedAt"),
            extra={"enabled": False, "initializing": True},
        )
    if bool(snap.get("enabled")) is False:
        return _build_workflow_entry(
            status=TASK_STATE_PARTIAL,
            stage=state or "degraded",
            progress=1.0,
            message=message or "risk model disabled",
            updated_at=snap.get("sdfBuildUpdatedAt"),
            extra={"enabled": False},
        )
    if state in ("ready", "ok", "enabled"):
        status = TASK_STATE_COMPLETED
        progress = 1.0
    elif state in ("failed", "error", "unavailable"):
        status = TASK_STATE_FAILED
        progress = 1.0
    elif state in ("building", "initializing", "loading", "starting", "pending"):
        status = TASK_STATE_RUNNING
        progress = _normalize_progress_01(snap.get("progress", 0.6), 0.6)
    elif Number := snap.get("surfacePointCount"):
        try:
            if int(Number) > 0:
                status = TASK_STATE_COMPLETED
                progress = 1.0
            else:
                status = TASK_STATE_RUNNING
                progress = _normalize_progress_01(snap.get("progress", 0.5), 0.5)
        except Exception:
            status = TASK_STATE_RUNNING
            progress = _normalize_progress_01(snap.get("progress", 0.5), 0.5)
    else:
        status = TASK_STATE_QUEUED
        progress = _normalize_progress_01(snap.get("progress", 0.0), 0.0)
    return _build_workflow_entry(
        status=status,
        stage=state or "queued",
        progress=progress,
        message=message or ("Risk model ready" if status == TASK_STATE_COMPLETED else "Risk model waiting"),
        updated_at=snap.get("sdfBuildUpdatedAt"),
        extra={
            "enabled": bool(snap.get("enabled")),
            "distanceModel": snap.get("distanceModel"),
        },
    )


def _workflow_status_from_planning():
    with workflow_state_lock:
        row = deepcopy(planning_runtime_state)
    return _build_workflow_entry(
        status=row.get("status", TASK_STATE_QUEUED),
        stage=row.get("stage", "queued"),
        progress=row.get("progress", 0.0),
        message=row.get("message", ""),
        updated_at=row.get("updatedAt"),
        extra={
            "taskId": row.get("taskId"),
            "lastError": row.get("lastError"),
            "lastResultSummary": row.get("lastResultSummary"),
        },
    )


def _workflow_status_severity(status):
    raw = normalize_task_status(status, TASK_STATE_QUEUED)
    if raw == TASK_STATE_FAILED:
        return 4
    if raw == TASK_STATE_PARTIAL:
        return 3
    if raw == TASK_STATE_RUNNING:
        return 2
    if raw == TASK_STATE_QUEUED:
        return 1
    return 0


def _build_workflow_summary(stages):
    stage_rows = stages if isinstance(stages, dict) else {}
    if not stage_rows:
        return {
            "status": TASK_STATE_QUEUED,
            "progress": 0.0,
            "blockingStages": [],
            "updatedAt": _now_iso(),
        }
    status_rows = []
    total_progress = 0.0
    updated_ts = []
    blocking = []
    for key, row in stage_rows.items():
        status = normalize_task_status(
            (row or {}).get("status"),
            TASK_STATE_QUEUED,
        )
        progress = _normalize_progress_01((row or {}).get("progress"), 0.0)
        status_rows.append((key, status))
        total_progress += progress
        updated = str((row or {}).get("updatedAt") or "").strip()
        if updated:
            updated_ts.append(updated)
        if status in (TASK_STATE_FAILED, TASK_STATE_PARTIAL, TASK_STATE_RUNNING):
            blocking.append(
                {
                    "stage": key,
                    "status": status,
                    "message": str((row or {}).get("message") or ""),
                }
            )
    status_rows.sort(key=lambda item: _workflow_status_severity(item[1]), reverse=True)
    overall = status_rows[0][1] if status_rows else TASK_STATE_QUEUED
    if overall == TASK_STATE_COMPLETED:
        blocking = []
    return {
        "status": overall,
        "progress": round(total_progress / max(len(stage_rows), 1), 4),
        "blockingStages": blocking,
        "updatedAt": max(updated_ts) if updated_ts else _now_iso(),
    }


def _build_unified_workflow_state(*, atlas_loaded_snapshot, atlas_load_snapshot, atlas_error_snapshot):
    with risk_model_lock:
        risk_snapshot = deepcopy(risk_model_summary)
    mri_payload = _mri_payload() if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid" else None
    stages = {
        "reconstruction": _workflow_status_from_atlas(
            atlas_loaded_snapshot,
            atlas_load_snapshot,
            atlas_error_snapshot,
        ),
        "registration": _workflow_status_from_registration(mri_payload),
        "risk": _workflow_status_from_risk(risk_snapshot),
        "planning": _workflow_status_from_planning(),
    }
    return {
        "stateMachine": list(WORKFLOW_STATUS_VALUES),
        "stages": stages,
        "summary": _build_workflow_summary(stages),
    }


def _build_mri_task_notice():
    task = _latest_mri_task_snapshot()
    if not isinstance(task, dict):
        return None
    status = normalize_task_status(task.get("status", task.get("state")))
    if status not in (TASK_STATE_FAILED, TASK_STATE_PARTIAL):
        return None
    hints = task.get("repairHints") if isinstance(task.get("repairHints"), dict) else {}
    suggestions = hints.get("suggestions") if isinstance(hints.get("suggestions"), list) else []
    retry_payload = hints.get("retryPayload") if isinstance(hints.get("retryPayload"), dict) else {}
    hint_line = " | ".join(str(x) for x in suggestions[:3]) if suggestions else "Review MRI artifact paths and retry."
    retry_preview = json.dumps(retry_payload, ensure_ascii=False)[:240] if retry_payload else ""
    return {
        "id": "mri_reconstruct_auto_repair",
        "code": "mri_reconstruct_auto_repair",
        "level": "warning",
        "titleEn": "MRI reconstruction task failed (auto-repair available)",
        "titleCn": "MRI 重建任务失败（可自动修复）",
        "messageEn": f"{task.get('message') or task.get('error') or 'MRI reconstruct failed'}. {hint_line}",
        "messageCn": f"{task.get('message') or task.get('error') or 'MRI 重建失败'}。{hint_line}",
        "actionEn": f"Retry with suggested payload: {retry_preview}" if retry_preview else "Retry using suggested parameters in status payload.",
        "actionCn": f"可使用建议参数重试：{retry_preview}" if retry_preview else "请使用状态返回的建议参数重试。",
        "reasonEn": str(task.get("error") or task.get("message") or "MRI reconstruction pipeline failed"),
        "reasonCn": str(task.get("error") or task.get("message") or "MRI 重建流程失败"),
        "impactEn": "MRI+CCF hybrid reconstruction remains incomplete; planning and appearance layers may be degraded.",
        "impactCn": "MRI+CCF 融合重建尚未完成；规划与外观层可能降级。",
        "recoveryEn": f"Retry with suggested payload: {retry_preview}" if retry_preview else "Retry using suggested parameters in status payload.",
        "recoveryCn": f"可使用建议参数重试：{retry_preview}" if retry_preview else "请使用状态返回的建议参数重试。",
        "updatedAt": _utc_now_iso(),
    }


#        Atlas loading                                                                                                                             

def _reset_atlas_runtime_state():
    global atlas_annotation, atlas_loaded, atlas_error, structure_map, descendant_ids
    global atlas_tree_ref, acronym_record_map
    global reconstruction_quality_state, dynamic_uncertainty_profile
    with atlas_lock:
        _reset_planner_regions()
        atlas_annotation = None
        atlas_loaded = False
        atlas_error = None
        structure_map = {}
        descendant_ids = {}
        atlas_tree_ref = None
        acronym_record_map = {}
        atlas_signature["atlasMode"] = ACTIVE_ATLAS_MODE
        if ACTIVE_ATLAS_MODE == "allen_sdk":
            atlas_source = "annotation/ccf_2017"
        elif ACTIVE_ATLAS_MODE == "mri_ccf_hybrid":
            atlas_source = "mri_ccf_hybrid_local"
        else:
            atlas_source = "qiu2018_local"
        atlas_signature["atlasSource"] = atlas_source
        atlas_signature["shape"] = list(ATLAS_SHAPE)
        atlas_signature["hash"] = None
    reconstruction_quality_state = {
        "gateEnabled": QUALITY_GATE_ENABLED,
        "metrics": {},
        "thresholds": {
            "diceMin": QUALITY_GATE_DICE_MIN,
            "hd95MaxMm": QUALITY_GATE_HD95_MAX_MM,
            "treMaxMm": QUALITY_GATE_TRE_MAX_MM,
            "jacobianAnomalyMax": QUALITY_GATE_JAC_ANOMALY_MAX,
        },
        "passed": True,
        "blockingReasons": [],
        "source": "unavailable",
        "updatedAt": _utc_now_iso(),
    }
    dynamic_uncertainty_profile = {
        "source": "default",
        "residualMm": None,
        "jacobianStd": None,
        "entrySigmaMm": None,
        "angleSigmaDeg": None,
        "depthSigmaMm": None,
        "riskDistanceSigmaMm": None,
    }
    _set_atlas_load_state(
        started=True,
        state="idle",
        phase="idle",
        message="Atlas mode switched; waiting for reload",
        step=0,
        total_steps=8,
        progress=0.0,
        finished=False,
    )


def _apply_active_atlas_mode(mode):
    global ACTIVE_ATLAS_MODE
    normalized = _normalize_atlas_mode(mode)
    if normalized == ACTIVE_ATLAS_MODE:
        return False
    ACTIVE_ATLAS_MODE = normalized
    _reset_atlas_runtime_state()
    return True


def _load_annotation_array_from_path(path_text, *, mode_name="atlas", memmap_enabled=True):
    path = Path(path_text).expanduser() if path_text else None
    if not path or not path.exists():
        raise RuntimeError(
            f"{mode_name} mode requires annotation path "
            "(npy/npz volume in atlas voxel space)."
        )
    suffix = path.suffix.lower()
    if suffix == ".npy":
        if memmap_enabled:
            arr = np.load(path, mmap_mode="r", allow_pickle=False)
        else:
            arr = np.load(path, allow_pickle=False)
        return np.asarray(arr, dtype=np.uint32)
    if suffix == ".npz":
        data = np.load(path, allow_pickle=False)
        key = "annotation" if "annotation" in data.files else (data.files[0] if data.files else None)
        if not key:
            raise RuntimeError(f"{mode_name} NPZ has no arrays: {path}")
        return np.asarray(data[key], dtype=np.uint32)
    raise RuntimeError(
        f"Unsupported {mode_name} annotation format: {path.suffix}. "
        "Use .npy or .npz for now."
    )


def _load_qiu2018_array():
    return _load_annotation_array_from_path(
        QIU2018_ANNOTATION_PATH,
        mode_name="qiu2018",
        memmap_enabled=QIU2018_MEMMAP_ENABLED,
    )


def _load_region_map_from_path(path_text):
    path = Path(path_text).expanduser() if path_text else None
    if not path or not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    out = {}
    if (
        isinstance(data, dict)
        and str(data.get("schemaVersion") or "").strip().lower() == "region_map_v2"
    ):
        entries = data.get("entries") if isinstance(data.get("entries"), list) else []
        for rec in entries:
            if not isinstance(rec, dict):
                continue
            try:
                rid = int(rec.get("id"))
            except Exception:
                continue
            canonical = str(rec.get("canonicalAcronym") or "").strip()
            aliases = rec.get("aliases") if isinstance(rec.get("aliases"), list) else []
            keys = []
            if canonical:
                keys.append(canonical)
            for alias in aliases:
                ak = str(alias or "").strip()
                if ak:
                    keys.append(ak)
            for key in keys:
                out[key] = rid
                out.setdefault(key.lower(), rid)
                out.setdefault(key.upper(), rid)
    elif isinstance(data, dict):
        for k, v in data.items():
            try:
                out[str(k).strip()] = int(v)
            except Exception:
                continue
    elif isinstance(data, list):
        for rec in data:
            if not isinstance(rec, dict):
                continue
            key = str(rec.get("acronym") or rec.get("key") or "").strip()
            val = rec.get("id")
            if not key:
                continue
            try:
                out[key] = int(val)
            except Exception:
                continue
    return out


def _load_qiu2018_region_map():
    return _load_region_map_from_path(QIU2018_REGION_MAP_PATH)


def _metric_float(v):
    try:
        num = float(v)
    except Exception:
        return None
    return num if math.isfinite(num) else None


def _clip(v, lo, hi):
    return max(lo, min(hi, float(v)))


def _load_json_from_path(path_text, *, warn_label="JSON"):
    path = Path(path_text).expanduser() if path_text else None
    if not path or not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning(f"Failed to read {warn_label}: {exc}")
        return {}


def _load_qiu2018_stats():
    return _load_json_from_path(QIU2018_STATS_PATH, warn_label="Qiu2018 stats JSON")


def _compute_jacobian_anomaly_ratio_from_file(path_text=None):
    summary = _compute_jacobian_summary_from_file(path_text)
    if isinstance(summary, dict):
        return summary.get("anomalyRatio")
    return None


def _compute_jacobian_summary_from_file(path_text=None, max_samples=1000000):
    path = Path(path_text).expanduser() if path_text else (
        Path(QIU2018_JACOBIAN_PATH).expanduser() if QIU2018_JACOBIAN_PATH else None
    )
    if not path or not path.exists():
        return None
    try:
        suffix = path.suffix.lower()
        if suffix == ".npy":
            arr = np.load(path, mmap_mode="r", allow_pickle=False)
        elif suffix == ".npz":
            pack = np.load(path, allow_pickle=False)
            key = "jacobian" if "jacobian" in pack.files else (pack.files[0] if pack.files else None)
            if not key:
                return None
            arr = pack[key]
        else:
            return None
        if getattr(arr, "size", 0) == 0:
            return None
        flat = np.ravel(arr)
        step = max(1, int(math.ceil(float(flat.size) / float(max(1, int(max_samples))))))
        sample = np.asarray(flat[::step], dtype=np.float32)
        finite = np.isfinite(sample)
        if not np.any(finite):
            return None
        j = sample[finite]
        bad = np.logical_or(j <= 0.0, np.logical_or(j < 0.2, j > 5.0))
        j_min = float(np.min(j))
        j_max = float(np.max(j))
        j_mean = float(np.mean(j))
        j_std = float(np.std(j))
        real = bool(j_std > 1e-6 and abs(j_max - j_min) > 1e-6)
        return {
            "path": str(path.resolve()),
            "jacobianValueType": "determinant_not_log",
            "sampleCount": int(j.size),
            "totalVoxels": int(flat.size),
            "mean": j_mean,
            "std": j_std,
            "min": j_min,
            "max": j_max,
            "fractionLt0": float(np.mean(j < 0.0)),
            "fractionLt02": float(np.mean(j < 0.2)),
            "fractionGt5": float(np.mean(j > 5.0)),
            "anomalyRatio": float(np.mean(bad)),
            "realJacobianEvidence": real,
            "evidence": "sampled_volume" if real else "constant_or_placeholder",
            "warning": None if real else "Jacobian determinant is constant; likely identity placeholder, not real ANTs/SyN deformation evidence.",
        }
    except Exception as exc:
        log.warning(f"Failed to compute Jacobian anomaly ratio: {exc}")
        return None


def _load_mri_hybrid_array():
    payload = _mri_payload()
    return _load_annotation_array_from_path(
        payload.get("annotationPath"),
        mode_name="mri_ccf_hybrid",
        memmap_enabled=MRI_CCF_MEMMAP_ENABLED,
    )


def _load_mri_hybrid_region_map():
    payload = _mri_payload()
    primary = payload.get("regionMapV2Path") or payload.get("regionMapPath")
    fallback = payload.get("regionMapPath")
    out = _load_region_map_from_path(primary)
    if not out and fallback and str(fallback) != str(primary):
        out = _load_region_map_from_path(fallback)
    return out


def _load_mri_hybrid_stats():
    payload = _mri_payload()
    return _load_json_from_path(payload.get("statsPath"), warn_label="MRI hybrid stats JSON")


def _load_mri_hybrid_registration_summary():
    payload = _mri_payload()
    return _load_json_from_path(
        payload.get("registrationSummaryPath"),
        warn_label="MRI registration_summary JSON",
    )


def _metric_from_stats(stats, key, *aliases):
    if not isinstance(stats, dict):
        return None
    keys = [key, *aliases]
    key_norms = {re.sub(r"[^a-z0-9]", "", str(k).lower()) for k in keys if str(k).strip()}
    metrics = stats.get("metrics") if isinstance(stats.get("metrics"), dict) else {}
    for k in keys:
        if k in stats:
            v = _metric_float(stats.get(k))
            if v is not None:
                return v
    for k in keys:
        if k in metrics:
            v = _metric_float(metrics.get(k))
            if v is not None:
                return v
    stack = [stats]
    visited = set()
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        node_id = id(node)
        if node_id in visited:
            continue
        visited.add(node_id)
        for nk, nv in node.items():
            nk_norm = re.sub(r"[^a-z0-9]", "", str(nk).lower())
            if nk_norm in key_norms:
                v = _metric_float(nv)
                if v is not None:
                    return v
            if isinstance(nv, dict):
                stack.append(nv)
    return None


def _safe_load_optional_mask(path_text):
    try:
        if not path_text:
            return None
        p = Path(str(path_text)).expanduser()
        if not p.exists():
            return None
        return _safe_load_mask(p)
    except Exception:
        return None


def _load_mri_hybrid_structured_masks():
    payload = _mri_payload()
    masks = {}
    skull = _safe_load_optional_mask(payload.get("skullMaskPath"))
    if skull is not None:
        masks["skull"] = skull
    outer = _safe_load_optional_mask(payload.get("outerTissueMaskPath"))
    if outer is not None:
        masks["cortex_outside"] = outer
    return masks


def _risk_class_from_sdf_channel(channel_key):
    key = str(channel_key or "").strip().lower()
    if not key:
        return None
    mapping = {
        "skull_sdf_mm": "skull",
        "outer_sdf_mm": "cortex_outside",
        "outer_tissue_sdf_mm": "cortex_outside",
        "cortex_outside_sdf_mm": "cortex_outside",
        "ventricle_sdf_mm": "ventricle",
        "vessel_sdf_mm": "vessel",
        "sulcus_sdf_mm": "sulcus",
        "brain_surface_sdf_mm": "brain_surface",
        "drilling_surface_sdf_mm": "drilling_surface",
        "skull_entry_sdf_mm": "drilling_surface",
    }
    if key in mapping:
        return mapping[key]
    return classify_risk_source(key)


def _build_risk_layer_completeness(
    layered_channel_map,
    source_meta,
    class_coverage_voxels,
    class_surface_point_count=None,
):
    channels = set(str(k) for k in (layered_channel_map or {}).keys())
    meta = source_meta if isinstance(source_meta, dict) else {}
    coverage = class_coverage_voxels if isinstance(class_coverage_voxels, dict) else {}
    surface_counts = class_surface_point_count if isinstance(class_surface_point_count, dict) else {}
    out = {}
    for risk_class in RISK_CLASS_CODE:
        expected_channel = RISK_CLASS_PRIMARY_SDF_CHANNEL.get(risk_class)
        class_channels = sorted(
            [
                key for key in channels
                if _risk_class_from_sdf_channel(key) == risk_class
            ]
        )
        related_sources = sorted(
            [
                str(source_key) for source_key in meta.keys()
                if classify_risk_source(source_key) == risk_class
            ]
        )
        try:
            active_voxels = int(coverage.get(risk_class, 0) or 0)
        except Exception:
            active_voxels = 0
        try:
            surface_points = int(surface_counts.get(risk_class, 0) or 0)
        except Exception:
            surface_points = 0
        channel_present = bool(class_channels)
        source_present = bool(related_sources)
        usable = bool((active_voxels > 0 or surface_points > 0) and (channel_present or source_present))
        if usable:
            status = "ready"
        elif channel_present or source_present:
            status = "configured_empty"
        else:
            status = "missing"
        out[risk_class] = {
            "status": status,
            "usable": usable,
            "activeVoxelCount": active_voxels,
            "surfacePointCount": surface_points,
            "expectedSdfChannel": expected_channel,
            "channelPresent": channel_present,
            "channels": class_channels,
            "sourcePresent": source_present,
            "sources": related_sources,
            "requirement": RISK_CLASS_DATA_REQUIREMENT.get(risk_class, ""),
        }
    return out


def _missing_structured_risk_classes(layer_completeness):
    if not isinstance(layer_completeness, dict):
        return []
    required = ("vessel", "sulcus", "brain_surface", "drilling_surface")
    missing = []
    for risk_class in required:
        item = layer_completeness.get(risk_class)
        if not isinstance(item, dict) or not bool(item.get("usable")):
            missing.append(risk_class)
    return missing


def _derive_masks_from_layered_sdf_bundle(bundle, threshold_mm=0.0):
    out_masks = {}
    out_meta = {}
    if not isinstance(bundle, dict):
        return out_masks, out_meta
    channels = bundle.get("channels") if isinstance(bundle.get("channels"), dict) else {}
    mapping = {
        "skull_sdf_mm": "skull",
        "outer_sdf_mm": "cortex_outside",
        "outer_tissue_sdf_mm": "cortex_outside",
        "cortex_outside_sdf_mm": "cortex_outside",
        "ventricle_sdf_mm": "ventricle",
        "vessel_sdf_mm": "vessel",
        "sulcus_sdf_mm": "sulcus",
        "brain_surface_sdf_mm": "brain_surface",
        "drilling_surface_sdf_mm": "drilling_surface",
        "skull_entry_sdf_mm": "drilling_surface",
    }
    for sdf_key, source_key in mapping.items():
        arr = channels.get(sdf_key)
        if arr is None:
            continue
        try:
            arr_f = np.asarray(arr, dtype=np.float32)
        except Exception:
            continue
        if tuple(arr_f.shape) != tuple(ATLAS_SHAPE):
            continue
        if not np.any(np.isfinite(arr_f)):
            continue
        mask = np.asarray(arr_f <= float(threshold_mm), dtype=bool)
        voxels = int(np.count_nonzero(mask))
        if voxels <= 0:
            continue
        out_masks[source_key] = mask
        out_meta[source_key] = {
            "kind": "layered_sdf_channel",
            "sdfKey": sdf_key,
            "thresholdMm": float(threshold_mm),
            "voxels": voxels,
            "path": bundle.get("path"),
        }
    return out_masks, out_meta


def _load_layered_sdf_bundle(path_text):
    if not path_text:
        return {"path": None, "signed_sdf_mm": None, "channels": {}, "keys": []}
    try:
        p = Path(str(path_text)).expanduser()
        if not p.exists():
            return {"path": None, "signed_sdf_mm": None, "channels": {}, "keys": []}
        suffix = p.suffix.lower()
        if suffix == ".npy":
            arr = np.load(p, mmap_mode="r", allow_pickle=False)
            signed = np.asarray(arr, dtype=np.float32)
            return {
                "path": str(p.resolve()),
                "signed_sdf_mm": signed,
                "channels": {"signed_sdf_mm": signed},
                "keys": ["signed_sdf_mm"],
            }
        if suffix == ".npz":
            pack = np.load(p, allow_pickle=False)
            channels = {}
            for key in pack.files:
                try:
                    arr = np.asarray(pack[key], dtype=np.float32)
                except Exception:
                    continue
                if arr.ndim != 3:
                    continue
                channels[str(key)] = arr
            signed = None
            for key in ("signed_sdf_mm", "sdf", "layered_sdf", "risk_sdf"):
                if key in channels:
                    signed = channels[key]
                    break
            if signed is None and channels:
                signed = channels[next(iter(channels.keys()))]
            return {
                "path": str(p.resolve()),
                "signed_sdf_mm": signed,
                "channels": channels,
                "keys": sorted(channels.keys()),
            }
        return {"path": str(p.resolve()), "signed_sdf_mm": None, "channels": {}, "keys": []}
    except Exception as exc:
        log.warning(f"Failed to load layered SDF bundle: {exc}")
        return {"path": None, "signed_sdf_mm": None, "channels": {}, "keys": []}


def _load_layered_sdf_mm(path_text):
    bundle = _load_layered_sdf_bundle(path_text)
    arr = bundle.get("signed_sdf_mm") if isinstance(bundle, dict) else None
    return np.asarray(arr, dtype=np.float32) if arr is not None else None


def _count_qiu_labels_chunked(annotation, chunk_depth=64):
    counts = {}
    if annotation is None or getattr(annotation, "shape", None) is None:
        return counts
    depth = int(annotation.shape[0])
    chunk_depth = max(8, int(chunk_depth))
    for z0 in range(0, depth, chunk_depth):
        z1 = min(depth, z0 + chunk_depth)
        block = np.asarray(annotation[z0:z1], dtype=np.uint32)
        if block.size == 0:
            continue
        ids, cnts = np.unique(block, return_counts=True)
        for sid, cnt in zip(ids.tolist(), cnts.tolist()):
            sid_i = int(sid)
            if sid_i <= 0:
                continue
            counts[sid_i] = counts.get(sid_i, 0) + int(cnt)
    return counts


def _update_reconstruction_quality_state(
    annotation=None,
    *,
    stats_override=None,
    registration_summary_override=None,
    jacobian_path_override=None,
    profile_source=None,
):
    global reconstruction_quality_state, dynamic_uncertainty_profile

    stats = stats_override if isinstance(stats_override, dict) else _load_qiu2018_stats()
    registration_summary = (
        registration_summary_override
        if isinstance(registration_summary_override, dict)
        else {}
    )
    jac_anomaly = _metric_from_stats(
        stats,
        "jacobianAnomalyRatio",
        "jacAnomalyRatio",
        "jacobian_anomaly_ratio",
    )
    if jac_anomaly is None:
        jac_anomaly = _metric_from_stats(
            registration_summary,
            "jacobianAnomalyRatio",
            "jacAnomalyRatio",
            "jacobian_anomaly_ratio",
        )
    jacobian_summary = _compute_jacobian_summary_from_file(jacobian_path_override)
    if jac_anomaly is None:
        jac_anomaly = jacobian_summary.get("anomalyRatio") if isinstance(jacobian_summary, dict) else None

    metric_ci = {}
    metric_ci_payload = (
        stats.get("metricCi95")
        if isinstance(stats.get("metricCi95"), dict)
        else stats.get("ci95")
        if isinstance(stats.get("ci95"), dict)
        else {}
    )
    for key in ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio"):
        raw = metric_ci_payload.get(key)
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            lo = _metric_float(raw[0])
            hi = _metric_float(raw[1])
            if lo is not None and hi is not None:
                if lo > hi:
                    lo, hi = hi, lo
                metric_ci[key] = [round(float(lo), 6), round(float(hi), 6)]

    metric_evidence = {}
    quality_builder = (
        stats.get("qualityBuilder")
        if isinstance(stats.get("qualityBuilder"), dict)
        else {}
    )
    quality_evidence_tokens = {
        str(item).strip().lower()
        for item in (quality_builder.get("evidence") or [])
        if str(item).strip()
    }
    explicit_metric_evidence = (
        stats.get("metricEvidence")
        if isinstance(stats.get("metricEvidence"), dict)
        else {}
    )
    metrics = {
        "dice": _metric_from_stats(stats, "dice"),
        "hd95Mm": _metric_from_stats(stats, "hd95Mm", "hd95"),
        "treMm": _metric_from_stats(stats, "treMm", "tre"),
        "jacobianAnomalyRatio": jac_anomaly,
        "residualMm": _metric_from_stats(stats, "residualMm", "residual"),
        "jacobianStd": _metric_from_stats(stats, "jacobianStd", "jacStd"),
    }
    legacy_tre_enabled = bool(
        stats.get("anatomicalTreAvailable", registration_summary.get("anatomicalTreAvailable", True))
    )

    def _seed_metric_evidence(metric_key):
        if metrics.get(metric_key) is None or metric_evidence.get(metric_key):
            return
        explicit = explicit_metric_evidence.get(metric_key)
        if explicit:
            metric_evidence[metric_key] = str(explicit)
            return
        if metric_key == "dice":
            if "from_masks" in quality_evidence_tokens:
                metric_evidence[metric_key] = "mask_ground_truth"
            elif "proxy_dice_from_registration_metric" in quality_evidence_tokens:
                metric_evidence[metric_key] = "registration_metric_proxy"
            elif any(t.startswith("from_registration_summary:dice") for t in quality_evidence_tokens):
                metric_evidence[metric_key] = "registration_summary"
        elif metric_key == "hd95Mm":
            if "from_masks" in quality_evidence_tokens:
                metric_evidence[metric_key] = "mask_ground_truth"
            elif "proxy_hd95_from_tre" in quality_evidence_tokens:
                metric_evidence[metric_key] = "tre_proxy"
            elif any(t.startswith("from_registration_summary:hd95") for t in quality_evidence_tokens):
                metric_evidence[metric_key] = "registration_summary"
        elif metric_key == "treMm":
            if "from_landmarks" in quality_evidence_tokens:
                metric_evidence[metric_key] = "landmark_ground_truth"
            elif "from_registration_center_offset" in quality_evidence_tokens:
                metric_evidence[metric_key] = "registration_center_offset_proxy"
            elif any(t.startswith("from_registration_summary:tre") for t in quality_evidence_tokens):
                metric_evidence[metric_key] = "registration_summary"
        elif metric_key == "jacobianAnomalyRatio":
            if "from_jacobian" in quality_evidence_tokens:
                if isinstance(jacobian_summary, dict) and jacobian_summary.get("realJacobianEvidence"):
                    metric_evidence[metric_key] = "jacobian_volume_sampled"
                else:
                    metric_evidence[metric_key] = "constant_jacobian_placeholder"
            elif "proxy_jacobian_from_rigid_affine" in quality_evidence_tokens:
                metric_evidence[metric_key] = "rigid_affine_no_deformable"

    for _metric_key in ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio"):
        _seed_metric_evidence(_metric_key)

    if metrics["jacobianStd"] is None and isinstance(jacobian_summary, dict):
        metrics["jacobianStd"] = _metric_float(jacobian_summary.get("std"))
        if metrics["jacobianStd"] is not None:
            metric_evidence["jacobianStd"] = str(jacobian_summary.get("evidence") or "jacobian_volume")
    if isinstance(jacobian_summary, dict):
        if jacobian_summary.get("realJacobianEvidence"):
            metric_evidence.setdefault("jacobianAnomalyRatio", "jacobian_volume_sampled")
        else:
            metric_evidence.setdefault("jacobianAnomalyRatio", "constant_jacobian_placeholder")
    if metrics["dice"] is None:
        metrics["dice"] = _metric_from_stats(registration_summary, "dice")
        if metrics["dice"] is not None:
            metric_evidence["dice"] = "registration_summary"
    if metrics["hd95Mm"] is None:
        metrics["hd95Mm"] = _metric_from_stats(registration_summary, "hd95Mm", "hd95")
        if metrics["hd95Mm"] is not None:
            metric_evidence["hd95Mm"] = "registration_summary"
    if metrics["treMm"] is None and legacy_tre_enabled:
        metrics["treMm"] = _metric_from_stats(registration_summary, "treMm", "tre")
        if metrics["treMm"] is not None:
            metric_evidence["treMm"] = "registration_summary"
    if metrics["residualMm"] is None:
        metrics["residualMm"] = _metric_from_stats(registration_summary, "residualMm", "residual")
        if metrics["residualMm"] is not None:
            metric_evidence["residualMm"] = "registration_summary"

    reg_block = (
        registration_summary.get("registration")
        if isinstance(registration_summary, dict)
        and isinstance(registration_summary.get("registration"), dict)
        else {}
    )
    center_offset = reg_block.get("initialCenterOffset")
    center_offset_norm_mm = None
    if isinstance(center_offset, (list, tuple)) and len(center_offset) >= 3:
        try:
            center_offset_norm_mm = float(np.linalg.norm(np.asarray(center_offset[:3], dtype=np.float64)))
        except Exception:
            center_offset_norm_mm = None
    if metrics["treMm"] is None and center_offset_norm_mm is not None and legacy_tre_enabled:
        metrics["treMm"] = center_offset_norm_mm
        metric_evidence["treMm"] = "registration_center_offset_proxy"

    if metrics["dice"] is None:
        affine_metric = _metric_float(reg_block.get("affineMetric"))
        rigid_metric = _metric_float(reg_block.get("rigidMetric"))
        metric_base = affine_metric if affine_metric is not None else rigid_metric
        if metric_base is not None:
            metrics["dice"] = _clip(1.0 - float(metric_base), 0.0, 1.0)
            metric_evidence["dice"] = "registration_metric_proxy"

    if metrics["hd95Mm"] is None:
        tre_proxy = _metric_float(metrics.get("treMm"))
        if tre_proxy is not None:
            metrics["hd95Mm"] = max(0.0, float(tre_proxy) * 0.6)
            metric_evidence["hd95Mm"] = "tre_proxy"

    if metrics["jacobianAnomalyRatio"] is None and reg_block.get("usedDeformable") is False:
        metrics["jacobianAnomalyRatio"] = 0.0
        metric_evidence["jacobianAnomalyRatio"] = "rigid_affine_no_deformable"
        if metrics.get("jacobianStd") is None:
            metrics["jacobianStd"] = 0.0
            metric_evidence["jacobianStd"] = "rigid_affine_no_deformable"

    for mk in ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio"):
        if metric_ci.get(mk) is not None:
            continue
        mv = _metric_float(metrics.get(mk))
        if mv is None:
            continue
        span = max(0.001, abs(float(mv)) * 0.1)
        metric_ci[mk] = [
            round(float(max(0.0, float(mv) - span)), 6),
            round(float(float(mv) + span), 6),
        ]

    if annotation is not None:
        try:
            nonzero = int(np.count_nonzero(annotation))
            metrics["nonzeroVoxelCount"] = nonzero
            metrics["nonzeroVoxelFraction"] = round(float(nonzero / float(annotation.size)), 8)
        except Exception:
            pass

    thresholds = {
        "diceMin": float(QUALITY_GATE_DICE_MIN),
        "hd95MaxMm": float(QUALITY_GATE_HD95_MAX_MM),
        "treMaxMm": float(QUALITY_GATE_TRE_MAX_MM),
        "jacobianAnomalyMax": float(QUALITY_GATE_JAC_ANOMALY_MAX),
    }
    reasons = []
    nonblocking_reasons = []

    def _metric_is_gateable(metric_key):
        evidence = str(metric_evidence.get(metric_key) or "").lower()
        if not evidence:
            return True
        non_gate_tokens = (
            "proxy",
            "center_offset",
            "constant",
            "placeholder",
            "rigid_affine_no_deformable",
        )
        return not any(token in evidence for token in non_gate_tokens)

    def _record_quality_violation(metric_key, message):
        if _metric_is_gateable(metric_key):
            reasons.append(message)
        else:
            evidence = metric_evidence.get(metric_key) or "proxy_or_incomplete_evidence"
            nonblocking_reasons.append(f"{message} (non-blocking; evidence={evidence})")

    if QUALITY_GATE_ENABLED:
        dice = metrics.get("dice")
        hd95 = metrics.get("hd95Mm")
        tre = metrics.get("treMm")
        jac = metrics.get("jacobianAnomalyRatio")
        if dice is not None and dice < thresholds["diceMin"]:
            _record_quality_violation(
                "dice",
                f"dice {dice:.4f} < min {thresholds['diceMin']:.4f}",
            )
        if hd95 is not None and hd95 > thresholds["hd95MaxMm"]:
            _record_quality_violation(
                "hd95Mm",
                f"hd95Mm {hd95:.4f} > max {thresholds['hd95MaxMm']:.4f}",
            )
        if tre is not None and tre > thresholds["treMaxMm"]:
            _record_quality_violation(
                "treMm",
                f"treMm {tre:.4f} > max {thresholds['treMaxMm']:.4f}",
            )
        if jac is not None and jac > thresholds["jacobianAnomalyMax"]:
            _record_quality_violation(
                "jacobianAnomalyRatio",
                f"jacobianAnomalyRatio {jac:.6f} > max {thresholds['jacobianAnomalyMax']:.6f}",
            )

    has_stats_metrics = any(v is not None for k, v in metrics.items() if k in ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio"))
    if has_stats_metrics:
        source = "stats_json"
    elif isinstance(registration_summary, dict) and registration_summary:
        source = "registration_summary"
    else:
        source = "derived"
    evidence_assessment = assess_registration_metrics(
        metrics, metric_evidence, {key: stats for key in metrics}, jacobian=jacobian_summary,
    )
    reconstruction_quality_state = {
        "gateEnabled": bool(QUALITY_GATE_ENABLED),
        "metrics": metrics,
        "metricCi95": metric_ci,
        "metricEvidence": metric_evidence,
        "qualityBuilderEvidence": sorted(quality_evidence_tokens),
        "jacobianSummary": jacobian_summary or {},
        "thresholds": thresholds,
        "passed": len(reasons) == 0,
        "status": "INCOMPLETE_MEASUREMENT_EVIDENCE" if len(reasons) == 0 else "FAIL",
        "gateScope": "legacy_technical_checks_not_anatomical_validation",
        **evidence_assessment,
        "blockingReasons": reasons,
        "nonBlockingReasons": nonblocking_reasons,
        "proxyEvidenceOnly": bool(nonblocking_reasons) and not bool(reasons),
        "source": source,
        "updatedAt": _utc_now_iso(),
        "registrationSummary": registration_summary if isinstance(registration_summary, dict) else {},
    }

    residual = metrics.get("residualMm")
    jac_std = metrics.get("jacobianStd")
    entry_sigma = _metric_from_stats(stats, "entrySigmaMm")
    angle_sigma = _metric_from_stats(stats, "angleSigmaDeg")
    depth_sigma = _metric_from_stats(stats, "depthSigmaMm")
    risk_sigma = _metric_from_stats(stats, "riskDistanceSigmaMm")
    if entry_sigma is None and residual is not None:
        entry_sigma = _clip(max(0.03, residual * 1.2), 0.01, 2.0)
    if angle_sigma is None:
        base = 2.0
        if residual is not None:
            base += residual * 2.5
        if jac_std is not None:
            base += jac_std * 8.0
        angle_sigma = _clip(base, 0.1, 25.0)
    if depth_sigma is None and residual is not None:
        depth_sigma = _clip(max(0.05, residual * 1.5), 0.01, 5.0)
    if risk_sigma is None:
        base = 0.08
        if residual is not None:
            base += residual * 0.5
        if jac_std is not None:
            base += jac_std * 0.03
        risk_sigma = _clip(base, 0.001, 2.0)

    def _scaled_sigma(scale):
        return {
            "entrySigmaMm": round(float(_clip((entry_sigma if entry_sigma is not None else 0.15) * scale, 0.01, 3.0)), 6),
            "angleSigmaDeg": round(float(_clip((angle_sigma if angle_sigma is not None else 2.0) * scale, 0.1, 30.0)), 6),
            "depthSigmaMm": round(float(_clip((depth_sigma if depth_sigma is not None else 0.2) * scale, 0.01, 6.0)), 6),
            "riskDistanceSigmaMm": round(float(_clip((risk_sigma if risk_sigma is not None else 0.08) * scale, 0.001, 3.0)), 6),
        }
    stratified_sigma = {
        "cortex": _scaled_sigma(0.9),
        "hippocampus": _scaled_sigma(1.05),
        "thalamus": _scaled_sigma(1.1),
        "brainstem": _scaled_sigma(1.25),
        "cerebellum": _scaled_sigma(1.15),
        "default": _scaled_sigma(1.0),
    }

    dynamic_uncertainty_profile = {
        "source": str(profile_source or ("qiu2018_stats_dynamic" if stats else "default")),
        "calibratedFromHeldOutAnatomicalErrors": False,
        "interpretation": "heuristic_sensitivity_parameters_not_measured_surgical_uncertainty",
        "residualMm": residual,
        "jacobianStd": jac_std,
        "entrySigmaMm": entry_sigma,
        "angleSigmaDeg": angle_sigma,
        "depthSigmaMm": depth_sigma,
        "riskDistanceSigmaMm": risk_sigma,
        "stratifiedByRegionClass": stratified_sigma,
    }


def _quality_gate_failure_card():
    st = reconstruction_quality_state if isinstance(reconstruction_quality_state, dict) else {}
    return {
        "constraint": "reconstructionQualityGate",
        "scope": "legacy_technical_checks_not_anatomical_validation",
        "source": st.get("source"),
        "metrics": deepcopy(st.get("metrics", {})),
        "thresholds": deepcopy(st.get("thresholds", {})),
        "blockingReasons": list(st.get("blockingReasons", [])),
        "nonBlockingReasons": list(st.get("nonBlockingReasons", [])),
        "metricEvidence": deepcopy(st.get("metricEvidence", {})),
        "qualityBuilderEvidence": list(st.get("qualityBuilderEvidence", [])),
    }


def _enforce_reconstruction_quality_gate():
    st = reconstruction_quality_state if isinstance(reconstruction_quality_state, dict) else {}
    if not st.get("gateEnabled", False):
        return
    if st.get("passed", True):
        return
    raise ContractError(
        "Reconstruction quality gate blocked optimization",
        code="reconstruction_quality_gate_block",
        status_code=422,
        details=list(st.get("blockingReasons", [])) or ["quality_gate_failed"],
    )


def _ensure_atlas_loader(force_retry=False):
    """Start atlas loading in background when needed.

    This keeps the server usable even when launched via `flask run` (where
    __main__ is not executed), and avoids duplicate loader threads.
    """
    global atlas_loader_thread
    with atlas_loader_lock:
        if atlas_loaded:
            return False
        if _atlas_loader_alive():
            return False
        if atlas_error and not force_retry:
            return False
        atlas_loader_thread = threading.Thread(
            target=load_atlas,
            daemon=True,
            name="atlas-loader",
        )
        atlas_loader_thread.start()
        return True


def _load_label_driven_atlas_mode(active_mode):
    global atlas_annotation, atlas_loaded, atlas_error, structure_map, descendant_ids
    global atlas_tree_ref, acronym_record_map
    if active_mode not in ("qiu2018", "mri_ccf_hybrid"):
        raise RuntimeError(f"Unsupported label-driven atlas mode: {active_mode}")

    mode_title = "Qiu2018" if active_mode == "qiu2018" else "MRI hybrid"
    phase_prefix = "qiu2018" if active_mode == "qiu2018" else "mri_hybrid"
    payload = _qiu_payload() if active_mode == "qiu2018" else _mri_payload()

    _set_atlas_load_state(
        state="loading",
        phase=f"{phase_prefix}_annotation",
        message=f"Loading {mode_title} annotation volume",
        step=1,
        progress=0.12,
    )
    _annotation = (
        _load_qiu2018_array().astype(np.uint32)
        if active_mode == "qiu2018"
        else _load_mri_hybrid_array().astype(np.uint32)
    )
    if _annotation.shape != ATLAS_SHAPE:
        raise RuntimeError(
            f"{mode_title} annotation shape {_annotation.shape} != expected {ATLAS_SHAPE}. "
            "Please resample to planner atlas grid first."
        )

    _set_atlas_load_state(
        state="loading",
        phase=f"{phase_prefix}_mapping",
        message=f"Loading {mode_title} region mapping",
        step=2,
        progress=0.28,
    )
    region_map = (
        _load_qiu2018_region_map()
        if active_mode == "qiu2018"
        else _load_mri_hybrid_region_map()
    )
    _set_atlas_load_state(
        state="loading",
        phase=f"{phase_prefix}_label_index",
        message="Indexing available labels from reconstruction volume",
        step=3,
        progress=0.38,
    )
    id_count_map = _count_qiu_labels_chunked(_annotation, chunk_depth=64)
    _set_atlas_load_state(
        state="loading",
        phase=f"{phase_prefix}_label_index",
        message=f"Indexed {len(id_count_map)} non-zero region labels",
        step=3,
        progress=0.42,
    )

    id_to_acronyms = {}
    for acronym, sid in region_map.items():
        try:
            sid_i = int(sid)
        except Exception:
            continue
        key = str(acronym).strip()
        if not key:
            continue
        id_to_acronyms.setdefault(sid_i, []).append(key)

    _desc_ids = {}
    _struct_map = {}
    _acronym_map = {}
    planner_seed = {}
    available_ids = sorted(id_count_map.keys())
    tree_for_mode = None
    atlas_hash = None
    region_index_source = "label_only"
    region_index_cache_path = None

    use_ontology = (
        active_mode == "mri_ccf_hybrid"
        and LABEL_DRIVEN_USE_ALLEN_ONTOLOGY
    )
    if use_ontology:
        try:
            from allensdk.core.reference_space_cache import ReferenceSpaceCache

            _set_atlas_load_state(
                state="loading",
                phase=f"{phase_prefix}_ontology",
                message="Loading Allen ontology for full-region indexing",
                step=3,
                progress=0.46,
            )
            cache_dir = Path.home() / ".allensdk_cache"
            cache_dir.mkdir(exist_ok=True)
            rsc = ReferenceSpaceCache(
                resolution=25,
                reference_space_key="annotation/ccf_2017",
                manifest=str(cache_dir / "manifest.json"),
            )
            tree_for_mode = rsc.get_structure_tree()
            records = _iter_tree_records(tree_for_mode)
            _desc_ids_all = {}
            total_records = max(1, len(records))
            for ridx, rec in enumerate(records, start=1):
                sid = rec.get("id")
                if sid is None:
                    continue
                sid = int(sid)
                _desc_ids_all[sid] = frozenset(_descendant_set_from_tree(tree_for_mode, sid))
                if ridx == 1 or ridx % max(1, total_records // 12) == 0 or ridx == total_records:
                    prog = 0.46 + 0.08 * (ridx / total_records)
                    _set_atlas_load_state(
                        state="loading",
                        phase=f"{phase_prefix}_ontology",
                        message=f"Indexing ontology descendants {ridx}/{total_records}",
                        step=3,
                        progress=min(0.54, prog),
                    )

            atlas_hash = _compute_atlas_hash(_annotation, _desc_ids_all)
            _acronym_map = _rebuild_tree_acronym_cache(tree_for_mode)
            if STARTUP_FULL_REGION_PLANNING:
                cached = _load_region_index_cache(active_mode, atlas_hash)
                if isinstance(cached, dict):
                    planner_seed = dict(cached.get("plannerSeed") or {})
                    _struct_map = {int(k): dict(v) for k, v in (cached.get("structureMap") or {}).items()}
                    _desc_ids = {
                        int(k): frozenset(int(v) for v in vals)
                        for k, vals in (cached.get("descendantIds") or {}).items()
                    }
                    region_index_source = "cache_hit"
                    region_index_cache_path = cached.get("cachePath")
                else:
                    planner_seed, struct_seed, desc_seed = _build_allen_full_region_seed(
                        _annotation,
                        tree_for_mode,
                        records,
                        _desc_ids_all,
                    )
                    _struct_map = {int(k): dict(v) for k, v in struct_seed.items()}
                    _desc_ids = {
                        int(k): frozenset(int(v) for v in vals)
                        for k, vals in desc_seed.items()
                    }
                    region_index_source = "built"
                    region_index_cache_path = _save_region_index_cache(
                        active_mode,
                        atlas_hash,
                        planner_seed,
                        _struct_map,
                        {int(k): [int(v) for v in sorted(vals)] for k, vals in _desc_ids.items()},
                    )
            else:
                planner_seed, struct_seed, desc_seed = _build_allen_full_region_seed(
                    _annotation,
                    tree_for_mode,
                    records,
                    _desc_ids_all,
                )
                _struct_map = {int(k): dict(v) for k, v in struct_seed.items()}
                _desc_ids = {
                    int(k): frozenset(int(v) for v in vals)
                    for k, vals in desc_seed.items()
                }
                region_index_source = "built"
        except Exception as exc:
            log.warning(
                f"[{mode_title}] Allen ontology not available for full-region indexing, fallback to label-only mode: {exc}"
            )
            tree_for_mode = None
            planner_seed = {}
            _desc_ids = {}
            _struct_map = {}
            _acronym_map = {}
            region_index_source = "label_only"

    if not _desc_ids:
        label_stats = _compute_label_centroid_stats_chunked(
            _annotation,
            label_ids=available_ids,
            chunk_depth=REGION_INDEX_CACHE_CHUNK_DEPTH,
        )
        left_label_stats = _compute_label_centroid_stats_chunked(
            _annotation,
            label_ids=available_ids,
            chunk_depth=REGION_INDEX_CACHE_CHUNK_DEPTH,
            hemisphere="left",
        )
        total_ids = max(1, len(available_ids))
        for idx, sid in enumerate(available_ids, start=1):
            vcount = int(id_count_map.get(sid, 0))
            acronyms = id_to_acronyms.get(sid) or [f"RID_{sid}"]
            primary = str(acronyms[0])
            _desc_ids[sid] = frozenset([sid])
            _struct_map[sid] = {
                "acronym": primary,
                "name": primary,
                "voxel_count": vcount,
                "volume_mm3": round(vcount * (VOXEL_SIZE_UM / 1000) ** 3, 4),
                "n_subregions": 1,
            }
            stat = left_label_stats.get(int(sid)) or label_stats.get(int(sid))
            if stat and int(stat.get("count", 0) or 0) > 0:
                centroid = (
                    float(stat["sum_i"]) / float(stat["count"]),
                    float(stat["sum_j"]) / float(stat["count"]),
                    float(stat["sum_k"]) / float(stat["count"]),
                )
                ap_mm, dv_mm, ml_mm = _voxel_centroid_to_bregma_mm(centroid)
                planner_seed[primary] = {
                    "id": int(sid),
                    "ap": round(float(ap_mm), 3),
                    "dv": round(float(dv_mm), 3),
                    "ml": round(float(ml_mm), 3),
                    "category": "atlas",
                }
            for alias in acronyms:
                alias_key = str(alias).strip()
                if not alias_key:
                    continue
                _acronym_map[alias_key.lower()] = {
                    "id": sid,
                    "acronym": alias_key,
                    "name": alias_key,
                    "safe_name": alias_key,
                    "graph_id": None,
                    "graph_order": None,
                    "structure_set_ids": [],
                }
            if idx == 1 or idx % max(1, total_ids // 10) == 0 or idx == total_ids:
                prog = 0.38 + 0.18 * (idx / total_ids)
                _set_atlas_load_state(
                    state="loading",
                    phase=f"{phase_prefix}_regions",
                    message=f"Indexed labels {idx}/{total_ids}",
                    step=3,
                    progress=min(0.56, prog),
                )

    # Ensure region-map aliases are discoverable even when ontology is enabled.
    for acronym, sid in region_map.items():
        alias_key = str(acronym).strip()
        if not alias_key:
            continue
        try:
            sid_i = int(sid)
        except Exception:
            continue
        if sid_i not in _desc_ids and sid_i in id_count_map:
            _desc_ids[sid_i] = frozenset([sid_i])
        if sid_i not in _struct_map and sid_i in id_count_map:
            vcount = int(id_count_map.get(sid_i, 0))
            _struct_map[sid_i] = {
                "acronym": alias_key,
                "name": alias_key,
                "voxel_count": vcount,
                "volume_mm3": round(vcount * (VOXEL_SIZE_UM / 1000) ** 3, 4),
                "n_subregions": 1,
            }
        _acronym_map.setdefault(alias_key.lower(), {
            "id": sid_i,
            "acronym": alias_key,
            "name": alias_key,
            "safe_name": alias_key,
            "graph_id": None,
            "graph_order": None,
            "structure_set_ids": [],
        })

    # Keep legacy core planner coordinates while remapping IDs if provided.
    for acronym, reg in list(PLANNER_REGIONS.items()):
        mapped_id = int(region_map.get(acronym, reg["id"]))
        if mapped_id in id_count_map:
            reg["id"] = mapped_id
            _desc_ids[mapped_id] = frozenset([mapped_id])
            if acronym.lower() not in _acronym_map:
                _acronym_map[acronym.lower()] = {
                    "id": mapped_id,
                    "acronym": acronym,
                    "name": acronym,
                    "safe_name": acronym,
                    "graph_id": None,
                    "graph_order": None,
                    "structure_set_ids": [],
                }
            seed_key = next(
                (
                    existing_key
                    for existing_key in planner_seed
                    if str(existing_key).strip().lower() == str(acronym).strip().lower()
                ),
                None,
            )
            if seed_key is None:
                planner_seed[acronym] = {
                    "id": int(mapped_id),
                    "ap": float(reg.get("ap", 0.0)),
                    "dv": float(reg.get("dv", 0.0)),
                    "ml": float(reg.get("ml", 0.0)),
                    "category": str(reg.get("category", "core")),
                }
            else:
                planner_seed[seed_key]["category"] = str(reg.get("category", "core"))

    if active_mode == "qiu2018":
        _update_reconstruction_quality_state(_annotation, profile_source="qiu2018_stats_dynamic")
    else:
        mri_stats = _load_mri_hybrid_stats()
        mri_reg_summary = _load_mri_hybrid_registration_summary()
        jac_path = payload.get("jacobianPath")
        _update_reconstruction_quality_state(
            _annotation,
            stats_override=mri_stats,
            registration_summary_override=mri_reg_summary,
            jacobian_path_override=jac_path,
            profile_source="mri_ccf_stats_dynamic" if mri_stats else "mri_ccf_default",
        )

    _set_atlas_load_state(
        state="loading",
        phase="signature",
        message="Computing atlas signature",
        step=4,
        progress=0.66,
    )
    if not atlas_hash:
        atlas_hash = _compute_atlas_hash(_annotation, _desc_ids)

    with atlas_lock:
        atlas_annotation = _annotation
        structure_map = _struct_map
        descendant_ids = _desc_ids
        atlas_tree_ref = tree_for_mode
        acronym_record_map = _acronym_map
        _merge_startup_planner_seed(planner_seed)
        _rebuild_region_centroids()
        atlas_loaded = True
        atlas_signature["atlasMode"] = active_mode
        atlas_signature["atlasSource"] = "qiu2018_local" if active_mode == "qiu2018" else "mri_ccf_hybrid_local"
        atlas_signature["shape"] = list(_annotation.shape)
        atlas_signature["hash"] = atlas_hash
        atlas_signature["availableRegionCount"] = int(
            sum(1 for _, info in _struct_map.items() if int(info.get("voxel_count", 0) or 0) > 0)
        )
        atlas_signature["catalogRegionCount"] = int(len(_struct_map))
        atlas_signature["presentRegionCount"] = int(
            sum(
                1
                for _, info in _struct_map.items()
                if isinstance(info, dict) and int(info.get("voxel_count", 0) or 0) > 0
            )
        )
        atlas_signature["regionIndexMode"] = "startup_full" if STARTUP_FULL_REGION_PLANNING else "planner_only"
        atlas_signature["regionIndexCache"] = {
            "enabled": bool(REGION_INDEX_CACHE_ENABLED),
            "source": str(region_index_source),
            "path": region_index_cache_path,
            "plannerSeedCount": int(len(planner_seed)),
        }
        if active_mode == "mri_ccf_hybrid":
            atlas_signature["mriDatasetProfile"] = payload.get("datasetProfile")
        if not version_freeze_state.get("frozen") and not version_freeze_state.get("atlasHash"):
            version_freeze_state["atlasHash"] = atlas_hash

    _set_atlas_load_state(
        state="loading",
        phase="risk_model",
        message="Initializing risk model",
        step=5,
        progress=0.82,
    )
    extra_masks = {}
    precomputed_sdf = None
    layered_sdf_bundle = {}
    extra_meta = {}
    if active_mode == "mri_ccf_hybrid":
        disable_mri_structured_risk = str(
            os.getenv("PROBEPLANNER_DISABLE_MRI_STRUCTURED_RISK", "false")
        ).strip().lower() in {"1", "true", "yes", "on"}
        if not disable_mri_structured_risk:
            extra_masks = _load_mri_hybrid_structured_masks()
            layered_sdf_bundle = _load_layered_sdf_bundle(payload.get("layeredSdfPath"))
            precomputed_sdf = (
                np.asarray(layered_sdf_bundle.get("signed_sdf_mm"), dtype=np.float32)
                if isinstance(layered_sdf_bundle, dict) and layered_sdf_bundle.get("signed_sdf_mm") is not None
                else None
            )
            for sk, mk in extra_masks.items():
                extra_meta[sk] = {
                    "kind": "mri_structured",
                    "voxels": int(np.count_nonzero(mk)),
                    "path": payload.get("skullMaskPath" if sk == "skull" else "outerTissueMaskPath"),
                }
            layered_masks, layered_meta = _derive_masks_from_layered_sdf_bundle(
                layered_sdf_bundle,
                threshold_mm=MRI_CCF_LAYERED_MASK_THRESHOLD_MM,
            )
            for sk, mk in layered_masks.items():
                if sk not in extra_masks:
                    extra_masks[sk] = mk
                meta_row = dict(layered_meta.get(sk, {}))
                meta_row.setdefault("kind", "layered_sdf_channel")
                extra_meta.setdefault(sk, meta_row)
        else:
            log.warning(
                "MRI structured risk masks disabled by PROBEPLANNER_DISABLE_MRI_STRUCTURED_RISK=1"
            )
        # If Allen ontology is unavailable for hybrid mode, fallback to lightweight
        # keyword masks derived from the label-driven structure map so risk model
        # remains available instead of hard-disabled.
        if tree_for_mode is None and not DISABLE_ATLAS_KEYWORD_RISK:
            kw_mask, kw_meta, kw_bucket_masks = _build_keyword_risk_mask_from_structure_maps(
                _annotation,
                _struct_map,
                _desc_ids,
            )
            if kw_mask is not None and int(np.count_nonzero(kw_mask)) > 0:
                added_bucket = 0
                for bucket, bucket_mask in (kw_bucket_masks or {}).items():
                    if bucket_mask is None:
                        continue
                    vox = int(np.count_nonzero(bucket_mask))
                    if vox <= 0:
                        continue
                    source_key = f"atlas_keywords:{bucket}"
                    extra_masks.setdefault(source_key, bucket_mask)
                    extra_meta.setdefault(
                        source_key,
                        {
                            "kind": "atlas_keyword_bucket_fallback",
                            "bucket": bucket,
                            "voxels": vox,
                        },
                    )
                    added_bucket += 1
                if added_bucket <= 0:
                    extra_masks.setdefault("atlas_keywords", kw_mask)
                    extra_meta.setdefault(
                        "atlas_keywords",
                        {
                            "kind": "atlas_fallback",
                            "voxels": int(np.count_nonzero(kw_mask)),
                            "detail": kw_meta or {},
                        },
                    )
    try:
        _mark_risk_model_initializing("Risk model initializing")
        log.info(
            f"[risk-init] mode={active_mode} treeProvided={tree_for_mode is not None} "
            f"extraMaskKeys={sorted(list((extra_masks or {}).keys()))}"
        )
        _initialize_risk_model(
            _annotation,
            tree=tree_for_mode,
            extra_source_masks=extra_masks or None,
            extra_source_meta=extra_meta or None,
            precomputed_signed_sdf_mm=precomputed_sdf,
            precomputed_layered_sdf_channels_mm=(
                layered_sdf_bundle.get("channels")
                if isinstance(layered_sdf_bundle, dict)
                else None
            ),
        )
    except MemoryError as exc:
        log.error(f"Risk model init degraded in {active_mode} mode due memory pressure: {exc}")
        _degrade_risk_model(f"memory_error: {exc}")

    atlas_signature["riskModel"] = deepcopy(risk_model_summary)
    atlas_signature["reconstructionQuality"] = deepcopy(reconstruction_quality_state)
    atlas_signature["dynamicUncertaintyProfile"] = deepcopy(dynamic_uncertainty_profile)
    atlas_signature["mriConfig"] = _mri_payload() if active_mode == "mri_ccf_hybrid" else None
    atlas_error = None
    _set_atlas_load_state(
        state="ready",
        phase="ready",
        message=f"{mode_title} atlas is ready",
        step=8,
        progress=1.0,
        finished=True,
    )
    log.info(f"Atlas ready ({active_mode} mode)")


def load_atlas():
    """Load atlas volume for the active atlas mode."""
    global atlas_annotation, atlas_loaded, atlas_error, structure_map, descendant_ids
    global atlas_tree_ref, acronym_record_map
    try:
        active_mode = ACTIVE_ATLAS_MODE
        _reset_planner_regions()
        atlas_error = None
        atlas_loaded = False
        _set_atlas_load_state(
            started=True,
            state="loading",
            phase="init",
            message="Preparing atlas cache",
            step=0,
            total_steps=8,
            progress=0.0,
        )

        if active_mode in ("qiu2018", "mri_ccf_hybrid"):
            _load_label_driven_atlas_mode(active_mode)
            return

        from allensdk.core.reference_space_cache import ReferenceSpaceCache

        _set_atlas_load_state(
            state="loading",
            phase="cache",
            message="Initializing Allen ReferenceSpaceCache",
            step=1,
            progress=0.08,
        )
        log.info("Initialising Allen ReferenceSpaceCache (25  m)...")
        cache_dir = Path.home() / ".allensdk_cache"
        cache_dir.mkdir(exist_ok=True)

        rsc = ReferenceSpaceCache(
            resolution=25,
            reference_space_key="annotation/ccf_2017",
            manifest=str(cache_dir / "manifest.json"),
        )

        _set_atlas_load_state(
            state="loading",
            phase="annotation",
            message="Loading annotation volume",
            step=2,
            progress=0.18,
        )
        log.info("Loading annotation volume (may download ~110 MB on first run)...")
        t0 = time.time()
        annotation, meta = rsc.get_annotation_volume()
        log.info(f"Annotation loaded in {time.time()-t0:.1f}s  shape={annotation.shape}")

        _annotation = annotation.astype(np.uint32)

        _set_atlas_load_state(
            state="loading",
            phase="ontology",
            message="Fetching structure ontology",
            step=3,
            progress=0.33,
        )
        log.info("Fetching structure ontology...")
        tree = rsc.get_structure_tree()
        _acronym_map = _rebuild_tree_acronym_cache(tree)
        records = _iter_tree_records(tree)
        _desc_ids_all = {}
        region_total = max(1, len(records))
        for idx, rec in enumerate(records, start=1):
            sid = rec.get("id")
            if sid is None:
                continue
            sid = int(sid)
            _desc_ids_all[sid] = frozenset(_descendant_set_from_tree(tree, sid))
            if idx == 1 or idx % max(1, region_total // 12) == 0 or idx == region_total:
                prog = 0.33 + 0.13 * (idx / region_total)
                _set_atlas_load_state(
                    state="loading",
                    phase="regions",
                    message=f"Indexing ontology descendants {idx}/{region_total}",
                    step=4,
                    progress=min(0.46, prog),
                )

        _set_atlas_load_state(
            state="loading",
            phase="signature",
            message="Computing atlas signature",
            step=5,
            progress=0.68,
        )
        atlas_hash = _compute_atlas_hash(_annotation, _desc_ids_all)

        planner_seed = {}
        struct_seed = {}
        desc_seed = {}
        cache_source = "built"
        cache_path = None
        if STARTUP_FULL_REGION_PLANNING:
            _set_atlas_load_state(
                state="loading",
                phase="regions_cache",
                message="Loading cached full-brain region index",
                step=5,
                progress=0.72,
            )
            cached = _load_region_index_cache("allen_sdk", atlas_hash)
            if isinstance(cached, dict):
                planner_seed = dict(cached.get("plannerSeed") or {})
                struct_seed = dict(cached.get("structureMap") or {})
                desc_seed = dict(cached.get("descendantIds") or {})
                cache_source = "cache_hit"
                cache_path = cached.get("cachePath")
            else:
                _set_atlas_load_state(
                    state="loading",
                    phase="regions_full_build",
                    message="Building full-brain region index (startup one-time)",
                    step=5,
                    progress=0.76,
                )
                planner_seed, struct_seed, desc_seed = _build_allen_full_region_seed(
                    _annotation,
                    tree,
                    records,
                    _desc_ids_all,
                )
                cache_path = _save_region_index_cache(
                    "allen_sdk",
                    atlas_hash,
                    planner_seed,
                    struct_seed,
                    desc_seed,
                )
        else:
            region_items = list(PLANNER_REGIONS.values())
            region_total = max(1, len(region_items))
            for idx, reg in enumerate(region_items, start=1):
                sid = int(reg["id"])
                desc = _desc_ids_all.get(sid, frozenset([sid]))
                centroid_info = _compute_descendant_centroid(_annotation, desc)
                if centroid_info is None:
                    centroid_info = {"ap_mm": reg["ap"], "dv_mm": reg["dv"], "ml_mm": reg["ml"], "voxel_count": 0}
                acronym = next((k for k, v in PLANNER_REGIONS.items() if int(v.get("id", -1)) == sid), f"RID_{sid}")
                planner_seed[acronym] = {
                    "id": sid,
                    "ap": round(float(centroid_info["ap_mm"]), 3),
                    "dv": round(float(centroid_info["dv_mm"]), 3),
                    "ml": round(float(centroid_info["ml_mm"]), 3),
                    "category": reg.get("category", "core"),
                }
                rec = _acronym_map.get(str(acronym).lower(), {})
                vcount = int(centroid_info.get("voxel_count", 0) or 0)
                struct_seed[sid] = {
                    "acronym": str(rec.get("acronym", acronym)),
                    "name": str(rec.get("name", acronym)),
                    "voxel_count": vcount,
                    "volume_mm3": round(vcount * (VOXEL_SIZE_UM / 1000) ** 3, 4),
                    "n_subregions": int(len(desc)),
                }
                desc_seed[sid] = [int(v) for v in sorted(desc)]
                prog = 0.46 + 0.12 * (idx / region_total)
                _set_atlas_load_state(
                    state="loading",
                    phase="regions",
                    message=f"Indexing planner region {idx}/{region_total}",
                    step=5,
                    progress=min(0.58, prog),
                )

        _desc_ids = {int(k): frozenset(int(v) for v in vals) for k, vals in desc_seed.items()}
        _struct_map = {int(k): dict(v) for k, v in struct_seed.items()}
        with atlas_lock:
            atlas_annotation = _annotation
            structure_map    = _struct_map
            descendant_ids   = _desc_ids
            atlas_tree_ref   = tree
            acronym_record_map = _acronym_map
            _merge_startup_planner_seed(planner_seed)
            _rebuild_region_centroids()
            atlas_loaded     = True
            atlas_signature["atlasMode"] = "allen_sdk"
            atlas_signature["atlasSource"] = "annotation/ccf_2017"
            atlas_signature["shape"] = list(_annotation.shape)
            atlas_signature["hash"] = atlas_hash
            atlas_signature["availableRegionCount"] = int(len(planner_seed) if planner_seed else len(_acronym_map))
            atlas_signature["catalogRegionCount"] = int(len(_acronym_map))
            atlas_signature["presentRegionCount"] = int(
                sum(
                    1
                    for _, info in _struct_map.items()
                    if isinstance(info, dict) and int(info.get("voxel_count", 0) or 0) > 0
                )
            )
            atlas_signature["regionIndexMode"] = "startup_full" if STARTUP_FULL_REGION_PLANNING else "planner_only"
            atlas_signature["regionIndexCache"] = {
                "enabled": bool(REGION_INDEX_CACHE_ENABLED),
                "source": cache_source,
                "path": cache_path,
                "plannerSeedCount": int(len(planner_seed)),
            }
            if not version_freeze_state.get("frozen") and not version_freeze_state.get("atlasHash"):
                version_freeze_state["atlasHash"] = atlas_hash

        if AUTO_PRELOAD_REGION_KEYS and not STARTUP_FULL_REGION_PLANNING:
            for key in AUTO_PRELOAD_REGION_KEYS:
                _register_region_from_atlas_key(key)
            log.info(f"Region preload list loaded: {len(AUTO_PRELOAD_REGION_KEYS)} keys requested")

        if AUTO_PRELOAD_ALL_REGIONS and not STARTUP_FULL_REGION_PLANNING:
            log.warning(
                "PROBEPLANNER_PRELOAD_ALL_REGIONS=true is expensive; "
                "preloading all acronyms for planning cache..."
            )
            preload_count = 0
            for key in sorted(acronym_record_map.keys()):
                if _register_region_from_atlas_key(key.upper()):
                    preload_count += 1
            log.info(f"Region full preload complete: +{preload_count} regions")

        _set_atlas_load_state(
            state="loading",
            phase="risk_model",
            message="Initializing risk model",
            step=6,
            progress=0.82,
        )
        try:
            _mark_risk_model_initializing("Risk model initializing")
            _initialize_risk_model(_annotation, tree)
        except MemoryError as exc:
            log.error(f"Risk model init degraded in allen_sdk mode due memory pressure: {exc}")
            _degrade_risk_model(f"memory_error: {exc}")
        atlas_signature["riskModel"] = deepcopy(risk_model_summary)
        log.info("Atlas ready")

        # [NEW-1]             
        _set_atlas_load_state(
            state="loading",
            phase="mesh_warmup",
            message="Warming mesh cache",
            step=7,
            progress=0.92,
        )
        warm_up_mesh_cache()
        atlas_error = None
        _set_atlas_load_state(
            state="ready",
            phase="ready",
            message="Atlas is ready",
            step=8,
            progress=1.0,
            finished=True,
        )

    except ImportError:
        atlas_error = (
            "allensdk not installed for allen_sdk mode. "
            "Run: pip install -r apps/backend-compat/requirements-legacy.txt"
        )
        _set_atlas_load_state(
            state="error",
            phase="error",
            message=atlas_error,
            progress=1.0,
            finished=True,
        )
        log.error(atlas_error)
    except Exception as e:
        atlas_error = str(e)
        _set_atlas_load_state(
            state="error",
            phase="error",
            message=atlas_error,
            progress=1.0,
            finished=True,
        )
        log.error(f"Atlas load failed: {e}\n{traceback.format_exc()}")


#        [NEW-1] Allen Mesh Proxy                                                                                                       
#
# Brain3DView.jsx        ?https://api.brain-map.org/api/v2/structure_mesh/{id}.obj
#        CORS                    /api/allen_mesh/{id}
#
# Brain3DView.jsx                 ?fetchAllenMesh       
#    ? `https://api.brain-map.org/api/v2/structure_mesh/${structureId}.obj`
#       `/api/allen_mesh/${structureId}`

ALLEN_MESH_BASE = "https://download.alleninstitute.org/informatics-archive/current-release/mouse_ccf/annotation/ccf_2017/structure_meshes"

MESH_CACHE_DIR = Path(
    os.getenv(
        "PROBEPLANNER_MESH_CACHE_DIR",
        str(Path.home() / ".probeplanner" / "mesh_cache"),
    )
)
MESH_BASE_DIR = MESH_CACHE_DIR / "base"
MESH_LOD_DIR = MESH_CACHE_DIR / "lod"
MESH_LOD_META = MESH_CACHE_DIR / "lod_meta.json"
try:
    MESH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MESH_BASE_DIR.mkdir(parents=True, exist_ok=True)
    MESH_LOD_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    fallback_dir = Path.cwd() / ".probeplanner_mesh_cache"
    MESH_CACHE_DIR = fallback_dir
    MESH_BASE_DIR = MESH_CACHE_DIR / "base"
    MESH_LOD_DIR = MESH_CACHE_DIR / "lod"
    MESH_LOD_META = MESH_CACHE_DIR / "lod_meta.json"
    MESH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MESH_BASE_DIR.mkdir(parents=True, exist_ok=True)
    MESH_LOD_DIR.mkdir(parents=True, exist_ok=True)

#         tructure_id (int)  ?OBJ        ?#                            
_mesh_cache: dict = {}
_mesh_cache_lock = threading.Lock()

#                 ID               
_WARM_UP_IDS = [
    385,   # VISp
    382,   # CA1
    726,   # DG
    218,   # LP
    733,   # VPM
    302,   # SC  (SCsg              ?
    672,   # CP
    128,   # MRN
    463,   # CA3
    254,   # RSP
    322,   # SSp
]


def _mesh_cache_key(structure_id: int, lod: int) -> str:
    return f"{int(structure_id)}:lod{int(max(0, lod))}"


def _mesh_disk_path(structure_id: int, lod: int) -> Path:
    if int(lod) <= 0:
        return MESH_BASE_DIR / f"{int(structure_id)}.obj"
    return MESH_LOD_DIR / f"{int(structure_id)}.lod{int(lod)}.obj"


def _read_mesh_disk(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        try:
            return path.read_text(encoding="latin-1")
        except Exception:
            return None


def _fetch_mesh_obj_resolved(structure_id: int, lod: int = 0):
    """
    Fetch mesh OBJ with offline LOD support.
    Returns (obj_text, resolved_lod, cache_source).
    """
    sid = int(structure_id)
    req_lod = max(0, int(lod))
    mem_key = _mesh_cache_key(sid, req_lod)

    with _mesh_cache_lock:
        if mem_key in _mesh_cache:
            text, resolved_lod = _mesh_cache[mem_key]
            return text, resolved_lod, "memory"

    # 1) disk cache for requested LOD
    req_path = _mesh_disk_path(sid, req_lod)
    if req_path.exists():
        text = _read_mesh_disk(req_path)
        if text:
            with _mesh_cache_lock:
                _mesh_cache[mem_key] = (text, req_lod)
            return text, req_lod, "disk"

    # 2) for LOD>0 fallback to base mesh (topology-safe fallback)
    if req_lod > 0:
        base_text, _, src = _fetch_mesh_obj_resolved(sid, lod=0)
        with _mesh_cache_lock:
            _mesh_cache[mem_key] = (base_text, 0)
        return base_text, 0, f"{src}:fallback_base"

    # 3) network fetch base mesh
    url = f"{ALLEN_MESH_BASE}/{sid}.obj"
    resp = http_requests.get(url, timeout=20)
    resp.raise_for_status()
    obj_text = resp.text

    try:
        req_path.write_text(obj_text, encoding="utf-8")
    except Exception as exc:
        log.warning(f"[MeshProxy] failed to persist base mesh id={sid}: {exc}")

    with _mesh_cache_lock:
        _mesh_cache[mem_key] = (obj_text, 0)
    return obj_text, 0, "network"


def _fetch_mesh_obj(structure_id: int, lod: int = 0) -> str:
    text, _, _ = _fetch_mesh_obj_resolved(structure_id, lod=lod)
    return text


MRI_SURFACE_LAYER_ALIASES = {
    "brain": "brain_surface",
    "brain_surface": "brain_surface",
    "brainSurface": "brain_surface",
    "surface": "brain_surface",
    "outer": "outer_tissue",
    "outer_tissue": "outer_tissue",
    "outerTissue": "outer_tissue",
    "outer_boundary": "outer_tissue",
    "skull": "skull",
}


def _normalize_mri_surface_layer(raw):
    key = str(raw or "").strip()
    if not key:
        return ""
    return MRI_SURFACE_LAYER_ALIASES.get(key, MRI_SURFACE_LAYER_ALIASES.get(key.lower(), key))


def _load_mri_surface_manifest(mri_payload=None):
    payload = mri_payload if isinstance(mri_payload, dict) else _mri_payload()
    manifest_path = payload.get("mriSurfaceMeshManifestPath")
    if not manifest_path or not _path_exists(manifest_path):
        return None, None
    try:
        path = Path(str(manifest_path)).expanduser().resolve()
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh), path
    except Exception as exc:
        log.warning(f"[MriSurfaceMesh] failed to read manifest {manifest_path}: {exc}")
        return None, None


def _resolve_mri_surface_mesh_path(raw_path, manifest_path):
    if not raw_path:
        return None
    try:
        candidate = Path(str(raw_path)).expanduser()
        if candidate.is_absolute() and candidate.exists():
            return candidate.resolve()
        if manifest_path:
            rel = Path(str(raw_path))
            base = Path(manifest_path).expanduser().resolve().parent
            for root in (base, base.parent):
                joined = (root / rel).resolve()
                if joined.exists():
                    return joined
        if candidate.exists():
            return candidate.resolve()
    except Exception:
        return None
    return None


def _build_mri_surface_mesh_status(mri_payload=None):
    payload = mri_payload if isinstance(mri_payload, dict) else _mri_payload()
    manifest, manifest_path = _load_mri_surface_manifest(payload)
    status = {
        "configured": bool(payload.get("mriSurfaceMeshManifestConfigured", False)),
        "manifestPath": payload.get("mriSurfaceMeshManifestPath"),
        "ready": False,
        "layers": {},
        "missingLayers": ["brain_surface", "outer_tissue", "skull"],
    }
    if not isinstance(manifest, dict):
        return status
    layers = manifest.get("layers") if isinstance(manifest.get("layers"), dict) else {}
    missing = []
    layer_status = {}
    for layer in ("brain_surface", "outer_tissue", "skull"):
        info = layers.get(layer) if isinstance(layers.get(layer), dict) else {}
        lods = info.get("lods") if isinstance(info.get("lods"), dict) else {}
        lod_status = {}
        has_any = False
        for lod_key, lod_info in lods.items():
            raw_path = lod_info.get("path") if isinstance(lod_info, dict) else None
            resolved_path = _resolve_mri_surface_mesh_path(raw_path, manifest_path)
            exists = bool(resolved_path)
            has_any = has_any or exists
            lod_status[str(lod_key)] = {
                "path": raw_path,
                "resolvedPath": str(resolved_path) if resolved_path else None,
                "exists": exists,
                "vertexCount": (lod_info or {}).get("vertexCount") if isinstance(lod_info, dict) else None,
                "faceCount": (lod_info or {}).get("faceCount") if isinstance(lod_info, dict) else None,
                "stepSize": (lod_info or {}).get("stepSize") if isinstance(lod_info, dict) else None,
            }
        if not has_any:
            missing.append(layer)
        layer_status[layer] = {
            "ready": has_any,
            "displayName": info.get("displayName") or layer,
            "manifestDisplayName": info.get("displayName"),
            "source": info.get("source"),
            "voxelCount": info.get("voxelCount"),
            "lods": lod_status,
        }
        if layer == "brain_surface":
            source_name = str(info.get("source") or "").strip().lower()
            if source_name in {"annotation", "ccf_annotation", "atlas_annotation", "active_annotation_volume"}:
                provenance = "atlas_annotation"
                display_name = "Atlas-derived brain surface"
            elif source_name in {"mri_brain_mask", "native_mri_brain_mask"}:
                provenance = "declared_mri_mask"
                display_name = "MRI mask brain surface"
            else:
                provenance = "unverified"
                display_name = "Brain surface (source unverified)"
            layer_status[layer].update(
                displayName=display_name, provenanceType=provenance,
                independentRegistrationEvidence=False,
            )
    status.update(
        {
            "ready": len(missing) == 0,
            "configured": bool(manifest_path and manifest_path.exists()),
            "manifestPath": str(manifest_path) if manifest_path else payload.get("mriSurfaceMeshManifestPath"),
            "schemaVersion": manifest.get("schemaVersion"),
            "generatedAt": manifest.get("generatedAt"),
            "layers": layer_status,
            "missingLayers": missing,
        }
    )
    return status


def _resolve_mri_surface_mesh(layer, lod=0, mri_payload=None):
    normalized_layer = _normalize_mri_surface_layer(layer)
    manifest, manifest_path = _load_mri_surface_manifest(mri_payload)
    if not isinstance(manifest, dict):
        raise FileNotFoundError("MRI surface mesh manifest is not configured")
    layers = manifest.get("layers") if isinstance(manifest.get("layers"), dict) else {}
    info = layers.get(normalized_layer)
    if not isinstance(info, dict):
        raise FileNotFoundError(f"MRI surface layer not found: {normalized_layer}")
    lods = info.get("lods") if isinstance(info.get("lods"), dict) else {}
    requested_lod = max(0, int(lod))
    for resolved_lod in (requested_lod, 0, 1, 2):
        lod_info = lods.get(str(resolved_lod))
        if not isinstance(lod_info, dict):
            continue
        raw_path = lod_info.get("path")
        resolved_path = _resolve_mri_surface_mesh_path(raw_path, manifest_path)
        if resolved_path:
            return resolved_path, normalized_layer, resolved_lod, lod_info
    canonical = info.get("canonicalPath")
    resolved_canonical = _resolve_mri_surface_mesh_path(canonical, manifest_path)
    if resolved_canonical:
        return resolved_canonical, normalized_layer, 0, {"path": canonical}
    raise FileNotFoundError(f"MRI surface layer has no available OBJ: {normalized_layer}")


def warm_up_mesh_cache():
    """Warm up common mesh ids in a background thread."""
    def _warm():
        log.info(f"[MeshProxy]     {len(_WARM_UP_IDS)}        ?..")
        ok = 0
        for sid in _WARM_UP_IDS:
            try:
                _fetch_mesh_obj(sid, lod=0)
                ok += 1
                log.debug(f"[MeshProxy] warmed id={sid}")
            except Exception as e:
                log.warning(f"[MeshProxy]     id={sid}    : {e}")
        log.info(f"[MeshProxy]       : {ok}/{len(_WARM_UP_IDS)}    ")

    t = threading.Thread(target=_warm, daemon=True)
    t.start()


@app.route("/api/allen_mesh/<int:structure_id>", methods=["GET"])
def allen_mesh_proxy(structure_id):
    """
        Allen Brain Atlas structure_mesh API              ORS      ?
        Allen CCFv3 OBJ                     m       (AP, DV, ML) ?    Brain3DView.jsx  ?parseAllenOBJ()           Three.js       ?
        ?
        Content-Type:  text/plain
        Cache-Control: public, max-age=86400  (       ?24    )
    """
    try:
        obj_text, resolved_lod, cache_source = _fetch_mesh_obj_resolved(structure_id, lod=0)
        return Response(
            obj_text,
            status=200,
            mimetype="text/plain",
            headers={
                "Cache-Control": "public, max-age=86400",
                "X-Structure-Id": str(structure_id),
                "X-Lod": str(resolved_lod),
                "X-Cache-Source": cache_source,
            },
        )
    except http_requests.exceptions.HTTPError as e:
        return _json_upstream_error(
            str(e),
            status=502,
            code="allen_api_error",
            context=f"Allen mesh proxy id={structure_id}",
        )
    except http_requests.exceptions.Timeout:
        log.warning(f"[MeshProxy]        id={structure_id}")
        return _json_error("Allen API timeout", status=504, code="allen_api_timeout")
    except Exception as e:
        return _json_internal_error(e, context=f"Allen mesh proxy id={structure_id}")


@app.route("/api/allen_mesh_lod/<int:structure_id>", methods=["GET"])
def allen_mesh_lod_proxy(structure_id):
    """Serve offline LOD mesh when available, fallback to base mesh safely."""
    try:
        lod = _parse_int_query_arg("lod", default=0, min_value=0, max_value=2)
        obj_text, resolved_lod, cache_source = _fetch_mesh_obj_resolved(structure_id, lod=lod)
        return Response(
            obj_text,
            status=200,
            mimetype="text/plain",
            headers={
                "Cache-Control": "public, max-age=86400",
                "X-Structure-Id": str(structure_id),
                "X-Lod-Requested": str(lod),
                "X-Lod": str(resolved_lod),
                "X-Cache-Source": cache_source,
                "X-Lod-Fallback": "1" if resolved_lod != lod else "0",
            },
        )
    except ContractError as exc:
        return _json_contract_error(exc)
    except http_requests.exceptions.HTTPError as e:
        return _json_upstream_error(
            str(e),
            status=502,
            code="allen_api_error",
            context=f"Allen mesh LOD proxy id={structure_id}",
        )
    except http_requests.exceptions.Timeout:
        log.warning(f"[MeshProxy] timeout id={structure_id}, lod={request.args.get('lod')}")
        return _json_error("Allen API timeout", status=504, code="allen_api_timeout")
    except Exception as e:
        return _json_internal_error(e, context=f"Allen mesh LOD proxy id={structure_id}")


@app.route("/api/allen_mesh/batch", methods=["POST"])
def allen_mesh_batch():
    """
                               ?        ? { "ids": [385, 382, 726, ...] }
       :   { "ok": true, "loaded": [...], "failed": [...] }
    """
    try:
        body = _request_json_object(required=True)
        ids  = [int(i) for i in body.get("ids", [])]
        if not ids:
            return _json_error("No ids provided", status=400, code="validation_error")

        loaded, failed = [], []
        for sid in ids[:20]:  #    ?20          
            try:
                _fetch_mesh_obj(sid, lod=0)
                loaded.append(sid)
            except Exception as e:
                log.warning(f"[MeshProxy] batch     id={sid}: {e}")
                failed.append(sid)

        return jsonify({"ok": True, "loaded": loaded, "failed": failed})
    except ContractError as exc:
        return _json_contract_error(exc)
    except BadRequest as exc:
        return _json_bad_request(exc)
    except Exception as e:
        return _json_internal_error(e, context="Allen mesh batch preload")


@app.route("/api/mesh_lod/<int:structure_id>", methods=["GET"])
def mesh_lod_proxy(structure_id):
    """
    Atlas-mode aware mesh endpoint used by UI diagnostics.
    qiu2018 currently reuses Allen-ID mesh assets for compatible region IDs.
    """
    if ACTIVE_ATLAS_MODE in ("allen_sdk", "qiu2018", "mri_ccf_hybrid"):
        return allen_mesh_lod_proxy(structure_id)
    return _json_error(
        f"Mesh endpoint not implemented for atlas mode: {ACTIVE_ATLAS_MODE}",
        status=501,
        code="mesh_mode_not_supported",
    )


@app.route("/api/mri_surface_meshes/status", methods=["GET"])
def mri_surface_meshes_status():
    payload = _mri_payload()
    return jsonify(
        {
            "ok": True,
            "activeAtlasMode": ACTIVE_ATLAS_MODE,
            "mriSurfaceMeshes": _build_mri_surface_mesh_status(payload),
        }
    )


@app.route("/api/mri_surface_mesh/<layer>", methods=["GET"])
def mri_surface_mesh_proxy(layer):
    return mri_surface_mesh_lod_proxy(layer)


@app.route("/api/mri_surface_mesh_lod/<layer>", methods=["GET"])
def mri_surface_mesh_lod_proxy(layer):
    try:
        lod = _parse_int_query_arg("lod", default=0, min_value=0, max_value=2)
        payload = _mri_payload()
        path, normalized_layer, resolved_lod, lod_info = _resolve_mri_surface_mesh(
            layer,
            lod=lod,
            mri_payload=payload,
        )
        obj_text = _read_mesh_disk(path)
        if not obj_text:
            raise FileNotFoundError(str(path))
        return Response(
            obj_text,
            status=200,
            mimetype="text/plain",
            headers={
                "Cache-Control": "public, max-age=86400",
                "X-Mri-Surface-Layer": normalized_layer,
                "X-Lod-Requested": str(lod),
                "X-Lod": str(resolved_lod),
                "X-Vertex-Count": str(lod_info.get("vertexCount", "")),
                "X-Face-Count": str(lod_info.get("faceCount", "")),
                "X-Cache-Source": "disk",
            },
        )
    except ContractError as exc:
        return _json_contract_error(exc)
    except FileNotFoundError as exc:
        log.warning(
            "[MriSurfaceMesh] unavailable requestId=%s layer=%s lod=%s: %s",
            _request_id(),
            layer,
            request.args.get("lod"),
            exc,
        )
        return _json_error(
            "MRI surface mesh unavailable",
            status=404,
            code="mri_surface_mesh_unavailable",
            details=["Run scripts/build_mri_surface_meshes.py for the active MRI profile."],
        )
    except Exception as exc:
        return _json_internal_error(
            exc,
            context=f"MRI surface mesh layer={layer}",
            message="MRI surface mesh unavailable",
            code="mri_surface_mesh_unavailable",
        )


#        Voxel coverage engine                                                                                                             

def _sample_trajectory_path_indices(
    ap_mm,
    ml_mm,
    theta_deg,
    phi_deg,
    depth_um,
    skull_tilt_deg=0.0,
    step_um=25.0,
    entry_dv_corr_mm=None,
):
    """Return ordered CCF voxel indices with their physical insertion depths.

    Unlike the legacy voxel-only sampler, this keeps duplicate voxel samples and
    appends the exact physical tip.  The depth vector therefore remains valid
    for oblique trajectories, whose rounded voxel indices are not one-to-one
    with fixed 25 um insertion steps.
    """
    tilt_rad = float(skull_tilt_deg) * math.pi / 180.0
    dv_corr_um = -float(ap_mm) * math.tan(tilt_rad) * 1000.0
    if entry_dv_corr_mm is not None:
        dv_corr_um = float(entry_dv_corr_mm) * 1000.0

    theta_rad = float(theta_deg) * math.pi / 180.0
    phi_rad = float(phi_deg) * math.pi / 180.0
    direction = np.array(
        [
            math.sin(theta_rad) * math.cos(phi_rad),
            math.cos(theta_rad),
            math.sin(theta_rad) * math.sin(phi_rad),
        ],
        dtype=np.float64,
    )
    entry_ccf = np.array(
        [
            BREGMA_UM[0] - float(ap_mm) * 1000.0,
            BREGMA_UM[1] + dv_corr_um,
            BREGMA_UM[2] + float(ml_mm) * 1000.0,
        ],
        dtype=np.float64,
    )
    total_depth_um = max(0.0, float(depth_um))
    sample_step_um = max(1e-6, float(step_um))
    depths_um = np.arange(0.0, total_depth_um + 1e-9, sample_step_um, dtype=np.float64)
    if depths_um.size == 0:
        depths_um = np.array([0.0], dtype=np.float64)
    if total_depth_um - float(depths_um[-1]) > 1e-6:
        depths_um = np.append(depths_um, total_depth_um)

    points = entry_ccf[None, :] + depths_um[:, None] * direction[None, :]
    indices = np.rint(points / float(VOXEL_SIZE_UM)).astype(np.int64)
    return [tuple(int(value) for value in row) for row in indices], [float(value) for value in depths_um]


def sample_trajectory_voxels(ap_mm, ml_mm, theta_deg, phi_deg, depth_um,
                              skull_tilt_deg=0.0, step_um=25.0, entry_dv_corr_mm=None):
    """
    Sample voxel indices along a probe trajectory.

    [FIX-1] All arithmetic done in CCFv3  m space  ?no mid-loop sign conversion.
    """
    path_indices, _ = _sample_trajectory_path_indices(
        ap_mm,
        ml_mm,
        theta_deg,
        phi_deg,
        depth_um,
        skull_tilt_deg=skull_tilt_deg,
        step_um=step_um,
        entry_dv_corr_mm=entry_dv_corr_mm,
    )

    # Preserve the public voxel-only contract for legacy coverage callers.
    voxels = []
    seen = set()
    for vi, vj, vk in path_indices:
        if not voxel_in_bounds(vi, vj, vk):
            continue
        key = (vi, vj, vk)
        if key in seen:
            continue
        seen.add(key)
        voxels.append(key)

    return voxels


def _trajectory_cache_key(ap_mm, ml_mm, theta_deg, phi_deg, depth_um, skull_tilt_deg, step_um,
                          entry_dv_corr_mm=None):
    return (
        round(float(ap_mm), 5),
        round(float(ml_mm), 5),
        round(float(theta_deg), 4),
        round(float(phi_deg), 4),
        round(float(depth_um), 3),
        round(float(skull_tilt_deg), 4),
        round(float(step_um), 4),
        None if entry_dv_corr_mm is None else round(float(entry_dv_corr_mm), 5),
    )


def sample_trajectory_voxels_cached(
    ap_mm,
    ml_mm,
    theta_deg,
    phi_deg,
    depth_um,
    skull_tilt_deg=0.0,
    step_um=25.0,
    entry_dv_corr_mm=None,
):
    key = _trajectory_cache_key(ap_mm, ml_mm, theta_deg, phi_deg, depth_um, skull_tilt_deg, step_um,
                                entry_dv_corr_mm)
    cached = _bounded_cache_get(trajectory_voxel_cache, trajectory_voxel_cache_lock, key)
    if cached is not None:
        return cached
    voxels = sample_trajectory_voxels(
        ap_mm,
        ml_mm,
        theta_deg,
        phi_deg,
        depth_um,
        skull_tilt_deg=skull_tilt_deg,
        step_um=step_um,
        entry_dv_corr_mm=entry_dv_corr_mm,
    )
    _bounded_cache_put(
        trajectory_voxel_cache,
        trajectory_voxel_cache_lock,
        key,
        voxels,
        TRAJECTORY_VOXEL_CACHE_MAX,
    )
    return voxels


def _brain_mask_path_summary_from_voxels(
    traj_voxels,
    *,
    step_um=25.0,
    outside_gap_um=BRAIN_BOUNDS_OUTSIDE_GAP_UM,
):
    """
    Evaluate whether a trajectory remains in brain mask (annotation > 0) after entry.
    This complements coarse AP/DV box checks with voxel-level continuity checks.
    """
    if atlas_annotation is None or not traj_voxels:
        return {"available": False, "passed": True}
    try:
        ii = np.array([v[0] for v in traj_voxels], dtype=np.int64)
        jj = np.array([v[1] for v in traj_voxels], dtype=np.int64)
        kk = np.array([v[2] for v in traj_voxels], dtype=np.int64)
        valid = (
            (ii >= 0)
            & (ii < int(atlas_annotation.shape[0]))
            & (jj >= 0)
            & (jj < int(atlas_annotation.shape[1]))
            & (kk >= 0)
            & (kk < int(atlas_annotation.shape[2]))
        )
        labels = np.zeros(ii.shape, dtype=np.int64)
        if bool(np.any(valid)):
            labels[valid] = np.asarray(atlas_annotation[ii[valid], jj[valid], kk[valid]], dtype=np.int64)
    except Exception:
        return {"available": False, "passed": True}

    inside = labels > 0
    n = int(inside.size)
    inside_count = int(np.count_nonzero(inside))
    if n <= 0 or inside_count <= 0:
        return {
            "available": True,
            "passed": False,
            "reason": "no_brain_intersection",
            "tipInside": False,
            "insideCount": int(inside_count),
            "sampleCount": int(n),
            "insideRatio": 0.0,
            "outsideGapUmThreshold": float(outside_gap_um),
            "outsideGapVoxThreshold": 0,
            "maxOutsideGapVox": 0,
            "maxOutsideGapUm": 0.0,
        }

    first_inside = int(np.argmax(inside))
    last_inside = int(n - 1 - np.argmax(inside[::-1]))
    tip_inside = bool(inside[-1])
    inside_after_entry = inside[first_inside:]
    inside_ratio_after_entry = float(np.mean(inside_after_entry)) if inside_after_entry.size else 0.0
    # Audit the complete post-entry path, including a terminal excursion after
    # the final in-brain sample. The previous span stopped at `last_inside`,
    # which could report a zero outside gap even when the tip was outside.
    inside_span = inside_after_entry

    max_outside_gap_vox = 0
    max_outside_gap_start_rel = None
    max_outside_gap_end_rel = None
    cur_gap = 0
    cur_gap_start_rel = None
    for rel_idx, flag in enumerate(inside_span.tolist()):
        if bool(flag):
            if cur_gap > max_outside_gap_vox and cur_gap_start_rel is not None:
                max_outside_gap_vox = cur_gap
                max_outside_gap_start_rel = int(cur_gap_start_rel)
                max_outside_gap_end_rel = int(rel_idx - 1)
            cur_gap = 0
            cur_gap_start_rel = None
        else:
            if cur_gap == 0:
                cur_gap_start_rel = int(rel_idx)
            cur_gap += 1
    if cur_gap > max_outside_gap_vox and cur_gap_start_rel is not None:
        max_outside_gap_vox = cur_gap
        max_outside_gap_start_rel = int(cur_gap_start_rel)
        max_outside_gap_end_rel = int(max(cur_gap_start_rel, inside_span.size - 1))

    safe_step_um = max(float(step_um), 1e-6)
    outside_gap_vox_threshold = int(max(1, math.ceil(float(outside_gap_um) / safe_step_um)))
    max_outside_gap_um = float(max_outside_gap_vox) * safe_step_um
    max_outside_gap_start_idx = None
    max_outside_gap_end_idx = None
    max_outside_gap_segment_mm = None
    if (
        max_outside_gap_vox > 0
        and max_outside_gap_start_rel is not None
        and max_outside_gap_end_rel is not None
    ):
        max_outside_gap_start_idx = int(first_inside + max_outside_gap_start_rel)
        max_outside_gap_end_idx = int(first_inside + max_outside_gap_end_rel)
        max_outside_gap_segment_mm = [
            round(float(max_outside_gap_start_idx) * safe_step_um / 1000.0, 6),
            round(float(max_outside_gap_end_idx) * safe_step_um / 1000.0, 6),
        ]
    exits_brain_midway = bool(max_outside_gap_vox >= outside_gap_vox_threshold)

    if not tip_inside:
        passed = False
        reason = "tip_outside_brain"
    elif exits_brain_midway:
        passed = False
        reason = "trajectory_exits_brain_midway"
    else:
        passed = True
        reason = "ok"

    return {
        "available": True,
        "passed": bool(passed),
        "reason": str(reason),
        "tipInside": bool(tip_inside),
        "firstInsideIndex": int(first_inside),
        "lastInsideIndex": int(last_inside),
        "insideCount": int(inside_count),
        "sampleCount": int(n),
        "insideRatio": round(float(inside_count / max(1, n)), 6),
        "insideRatioAfterEntry": round(float(inside_ratio_after_entry), 6),
        "stepUm": round(float(safe_step_um), 6),
        "outsideGapUmThreshold": round(float(outside_gap_um), 6),
        "outsideGapVoxThreshold": int(outside_gap_vox_threshold),
        "maxOutsideGapVox": int(max_outside_gap_vox),
        "maxOutsideGapUm": round(float(max_outside_gap_um), 6),
        "maxOutsideGapStartIndex": int(max_outside_gap_start_idx)
        if max_outside_gap_start_idx is not None
        else None,
        "maxOutsideGapEndIndex": int(max_outside_gap_end_idx)
        if max_outside_gap_end_idx is not None
        else None,
        "maxOutsideGapSegmentMm": max_outside_gap_segment_mm,
    }


def _candidate_brain_mask_path_summary(candidate, *, step_um=25.0):
    ap = float(candidate.get("ap_mm_corrected", candidate.get("ap_mm", 0.0)))
    ml = float(candidate.get("ml_mm_corrected", candidate.get("ml_mm", 0.0)))
    theta = float(candidate.get("theta_deg", 0.0))
    phi = float(candidate.get("phi_deg", 0.0))
    depth_um = float(candidate.get("depthUm_corrected", candidate.get("depthUm", 0.0)))
    skull_tilt = float(candidate.get("skullTiltDeg", 0.0))
    traj_voxels, _ = _sample_trajectory_path_indices(
        ap,
        ml,
        theta,
        phi,
        depth_um,
        skull_tilt_deg=skull_tilt,
        step_um=step_um,
        entry_dv_corr_mm=_probe_entry_dv_mm(candidate),
    )
    summary = _brain_mask_path_summary_from_voxels(traj_voxels, step_um=step_um)
    if isinstance(summary, dict):
        summary["sampleCount"] = int(summary.get("sampleCount", len(traj_voxels)))
    return summary


def pt_seg_dist_um(p, a, b):
    """Point-to-line-segment distance in  m (3D numpy arrays)."""
    ab = b - a
    t = np.dot(p - a, ab) / (np.dot(ab, ab) + 1e-12)
    t = float(np.clip(t, 0.0, 1.0))
    return float(np.linalg.norm(p - (a + t * ab)))


def voxel_coverage(traj_voxels, target_structure_ids,
                   entry_um=None, tip_um=None, coverage_radius_um=300):
    if atlas_annotation is None:
        raise RuntimeError("Atlas not loaded")

    probe_id_set = set()
    if traj_voxels:
        ii = np.array([v[0] for v in traj_voxels], dtype=np.int32)
        jj = np.array([v[1] for v in traj_voxels], dtype=np.int32)
        kk = np.array([v[2] for v in traj_voxels], dtype=np.int32)
        probe_id_set = set(atlas_annotation[ii, jj, kk].tolist())

    ea = np.array(entry_um, dtype=float) if entry_um is not None else None
    ta = np.array(tip_um,   dtype=float) if tip_um   is not None else None
    use_centroid = (ea is not None and ta is not None)

    result = {}
    for sid in target_structure_ids:
        desc = descendant_ids.get(sid, frozenset([sid]))
        covered = bool(probe_id_set & desc)
        if not covered and use_centroid:
            info = structure_map.get(sid, {})
            if info.get("voxel_count", 0) == 0:
                cent = REGION_CENTROIDS_UM.get(sid)
                if cent is not None:
                    dist = pt_seg_dist_um(np.array(cent), ea, ta)
                    covered = (dist <= coverage_radius_um)
        result[sid] = covered
    return result


def voxel_overlap_count(traj_voxels, structure_id):
    if atlas_annotation is None or not traj_voxels:
        return 0
    ii = np.array([v[0] for v in traj_voxels], dtype=np.int32)
    jj = np.array([v[1] for v in traj_voxels], dtype=np.int32)
    kk = np.array([v[2] for v in traj_voxels], dtype=np.int32)
    probe_ids = atlas_annotation[ii, jj, kk]
    desc = descendant_ids.get(structure_id, frozenset([structure_id]))
    return int(np.isin(probe_ids, list(desc)).sum())


def compute_trajectory_profile(traj_voxels, target_structure_ids, step_um=25.0, sample_depths_um=None):
    """Summarize sampled atlas-label intersections for each requested structure.

    Inputs are in-bounds atlas voxel indices; descendant labels count as hits.
    Repeated samples are counted, so n_voxels is not a unique-voxel volume or an
    active-contact count. One hit establishes coverage. ML <= 0 is assigned left.
    Finite, shape-matched sample depths retain the original path coordinates;
    otherwise depths use sample index times step_um. Entry/exit depths span the
    first/last hits, not necessarily one contiguous target interval.
    """
    if atlas_annotation is None or not traj_voxels:
        return {sid: {"covered": False, "n_voxels": 0, "coverage_frac": 0.0,
                      "entry_depth_um": None, "exit_depth_um": None,
                      "channel_span_um": 0, "voxel_score": 0.0,
                      "left_voxels": 0, "right_voxels": 0, "coveredSides": []}
                for sid in target_structure_ids}

    ii = np.array([v[0] for v in traj_voxels], dtype=np.int32)
    jj = np.array([v[1] for v in traj_voxels], dtype=np.int32)
    kk = np.array([v[2] for v in traj_voxels], dtype=np.int32)
    probe_labels = atlas_annotation[ii, jj, kk]
    ml_um = kk.astype(np.float64) * VOXEL_SIZE_UM - BREGMA_UM[2]
    depths_um = None
    if sample_depths_um is not None:
        try:
            trial_depths = np.asarray(sample_depths_um, dtype=np.float64)
            if trial_depths.shape == ii.shape and bool(np.all(np.isfinite(trial_depths))):
                depths_um = trial_depths
        except Exception:
            depths_um = None
    if depths_um is None:
        depths_um = np.arange(ii.size, dtype=np.float64) * float(step_um)

    result = {}
    for sid in target_structure_ids:
        desc = descendant_ids.get(sid, frozenset([sid]))
        mask = np.isin(probe_labels, list(desc))
        n_hit = int(mask.sum())

        if n_hit == 0:
            result[sid] = {"covered": False, "n_voxels": 0, "coverage_frac": 0.0,
                           "entry_depth_um": None, "exit_depth_um": None,
                           "channel_span_um": 0, "voxel_score": 0.0,
                           "left_voxels": 0, "right_voxels": 0,
                           "coveredSides": []}
            continue

        total_voxels  = structure_map.get(sid, {}).get("voxel_count", 1)
        coverage_frac = n_hit / max(total_voxels, 1)
        hit_indices   = np.where(mask)[0]
        entry_step    = int(hit_indices[0])
        exit_step     = int(hit_indices[-1])
        entry_depth_um = float(depths_um[entry_step])
        exit_depth_um = float(depths_um[exit_step])
        left_voxels = int(np.logical_and(mask, ml_um <= 0).sum())
        right_voxels = int(np.logical_and(mask, ml_um > 0).sum())
        covered_sides = []
        if left_voxels > 0:
            covered_sides.append("left")
        if right_voxels > 0:
            covered_sides.append("right")

        result[sid] = {
            "covered":         True,
            "n_voxels":        n_hit,
            "coverage_frac":   round(coverage_frac, 5),
            "entry_depth_um":  round(entry_depth_um, 6),
            "exit_depth_um":   round(exit_depth_um, 6),
            "channel_span_um": round(max(0.0, exit_depth_um - entry_depth_um), 6),
            "voxel_score":     round(math.sqrt(coverage_frac), 6),
            "left_voxels":     left_voxels,
            "right_voxels":    right_voxels,
            "coveredSides":    covered_sides,
        }

    return result


def _candidate_endpoint_target_summary(candidate, target_ids):
    """Summarize terminal target membership and post-target insertion length."""
    if atlas_annotation is None:
        return {
            "available": False,
            "tipLabelId": None,
            "tipInRequestedTarget": False,
            "tipTargetKeys": [],
            "lastTargetExitDepthUm": None,
            "overshootAfterLastTargetUm": None,
        }

    target_ids = {
        str(key): int(value)
        for key, value in (target_ids or {}).items()
        if value is not None
    }
    ap = float(candidate.get("ap_mm_corrected", candidate.get("ap_mm", 0.0)))
    ml = float(candidate.get("ml_mm_corrected", candidate.get("ml_mm", 0.0)))
    theta = float(candidate.get("theta_deg", 0.0))
    phi = float(candidate.get("phi_deg", 0.0))
    depth_um = max(0.0, float(candidate.get("depthUm_corrected", candidate.get("depthUm", 0.0)) or 0.0))
    skull_tilt = float(candidate.get("skullTiltDeg", 0.0))
    try:
        voxels, depths_um = _sample_trajectory_path_indices(
            ap,
            ml,
            theta,
            phi,
            depth_um,
            skull_tilt_deg=skull_tilt,
            step_um=25.0,
            entry_dv_corr_mm=_probe_entry_dv_mm(candidate),
        )
        labels = []
        for idx in voxels:
            labels.append(int(atlas_annotation[idx]) if voxel_in_bounds(*idx) else 0)
    except Exception:
        voxels, depths_um, labels = [], [], []

    target_descendants = {
        key: descendant_ids.get(sid, frozenset([sid]))
        for key, sid in target_ids.items()
    }
    tip_label = int(labels[-1]) if labels else None
    tip_target_keys = [
        key for key, descendants in target_descendants.items()
        if tip_label is not None and tip_label in descendants
    ]
    terminal_depths = [
        float(depth)
        for depth, label in zip(depths_um, labels)
        if any(int(label) in descendants for descendants in target_descendants.values())
    ]
    last_exit_um = max(terminal_depths, default=None)
    overshoot_um = max(0.0, depth_um - last_exit_um) if last_exit_um is not None else None
    return {
        "available": True,
        "tipLabelId": tip_label,
        "tipInRequestedTarget": bool(tip_target_keys),
        "tipTargetKeys": sorted(tip_target_keys),
        "lastTargetExitDepthUm": round(float(last_exit_um), 6) if last_exit_um is not None else None,
        "overshootAfterLastTargetUm": round(float(overshoot_um), 6) if overshoot_um is not None else None,
        "endpointSamplingStepUm": 25.0,
    }


def _build_unified_probe_audit(probe, target_keys, params):
    """Build one non-contradictory containment, endpoint, and risk audit."""
    target_ids = {
        str(key): int(PLANNER_REGIONS[key]["id"])
        for key in (target_keys or [])
        if key in PLANNER_REGIONS and PLANNER_REGIONS[key].get("id") is not None
    }
    brain = _candidate_brain_mask_path_summary(probe, step_um=25.0)
    endpoint = _candidate_endpoint_target_summary(probe, target_ids)
    risk_diag = probe.get("riskDiagnostics") if isinstance(probe.get("riskDiagnostics"), dict) else {}
    class_min = risk_diag.get("classMinDistanceMm") if isinstance(risk_diag.get("classMinDistanceMm"), dict) else {}
    class_threshold = risk_diag.get("classThresholdMm") if isinstance(risk_diag.get("classThresholdMm"), dict) else {}
    thresholds = sanitize_risk_thresholds(
        params.get("riskThresholdsMm"),
        default_mm=float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
    )

    completeness = risk_model_summary.get("riskLayerCompleteness")
    completeness_known = isinstance(completeness, dict) and bool(completeness)
    internal_rows = {}
    unavailable_internal = []
    available_internal = []
    violations = []
    for risk_class in sorted(INTERNAL_AVOIDANCE_RISK_CLASSES):
        layer = completeness.get(risk_class) if completeness_known else None
        usable = bool(layer.get("usable")) if isinstance(layer, dict) else risk_class in class_min
        if not usable:
            unavailable_internal.append(risk_class)
            internal_rows[risk_class] = {
                "status": (layer or {}).get("status", "unavailable") if isinstance(layer, dict) else "unavailable",
                "available": False,
                "distanceMm": None,
                "thresholdMm": None,
                "marginMm": None,
            }
            continue
        available_internal.append(risk_class)
        distance = _as_float_or_none(class_min.get(risk_class))
        threshold = _as_float_or_none(class_threshold.get(risk_class))
        if threshold is None:
            threshold = float(resolve_risk_threshold_mm(thresholds, risk_class))
        margin = float(distance - threshold) if distance is not None else None
        if margin is not None and margin < -1e-9:
            violations.append(risk_class)
        internal_rows[risk_class] = {
            "status": "ready" if distance is not None else "diagnostic_missing",
            "available": True,
            "distanceMm": round(float(distance), 6) if distance is not None else None,
            "thresholdMm": round(float(threshold), 6),
            "marginMm": round(float(margin), 6) if margin is not None else None,
        }

    internal_margins = [
        float(row["marginMm"])
        for row in internal_rows.values()
        if row.get("available") and row.get("marginMm") is not None
    ]
    missing_diagnostics = [
        risk_class
        for risk_class in available_internal
        if internal_rows[risk_class].get("marginMm") is None
    ]
    brain_passed = bool(brain.get("available") and brain.get("passed"))
    entry_layers = {}
    for risk_class in sorted(ENTRY_CORRIDOR_RISK_CLASSES):
        layer = completeness.get(risk_class) if completeness_known else None
        available = bool(layer.get("usable")) if isinstance(layer, dict) else risk_class in class_min
        entry_layers[risk_class] = {
            "available": available,
            "status": (layer or {}).get("status", "ready" if available else "unavailable")
            if isinstance(layer, dict)
            else ("ready" if available else "unavailable"),
            "minimumDistanceMm": round(float(class_min[risk_class]), 6)
            if available and _as_float_or_none(class_min.get(risk_class)) is not None
            else None,
        }
    if not brain_passed:
        entry_status = "failed_post_entry_containment"
    elif any(item["available"] for item in entry_layers.values()):
        entry_status = "allowed_entry_surface_semantics"
    else:
        entry_status = "entry_surface_layers_unavailable"

    overshoot_um = _as_float_or_none(endpoint.get("overshootAfterLastTargetUm"))
    return {
        "brainContainmentPassed": brain_passed,
        "tipInTarget": bool(endpoint.get("tipInRequestedTarget")),
        "distalOvershootMm": round(float(overshoot_um) / 1000.0, 6)
        if overshoot_um is not None
        else None,
        "internalRiskViolationCount": int(len(violations)),
        "minimumInternalRiskMarginMm": round(float(min(internal_margins)), 6)
        if internal_margins
        else None,
        "entrySurfaceCrossingStatus": entry_status,
        "brainContainmentAudit": brain,
        "targetEndpointAudit": endpoint,
        "internalRiskAudit": {
            "claimStatus": (
                "complete"
                if available_internal and not unavailable_internal and not missing_diagnostics
                else "partial"
                if available_internal
                else "unavailable"
            ),
            "availableClasses": available_internal,
            "unavailableClasses": unavailable_internal,
            "missingDiagnosticClasses": missing_diagnostics,
            "violationClasses": violations,
            "classes": internal_rows,
        },
        "entrySurfaceAudit": {
            "semantics": "crossing_allowed_only_at_entry; brain containment enforced after first entry",
            "classes": entry_layers,
        },
    }


def _candidate_target_ids(candidate):
    return {
        str(key): int(PLANNER_REGIONS[key]["id"])
        for key in (candidate.get("coveredRegions") or [])
        if key in PLANNER_REGIONS and PLANNER_REGIONS[key].get("id") is not None
    }


def _trim_candidate_to_target_tip(
    candidate,
    *,
    target_ids,
    params,
    constraint_modes,
    correction_factors,
    probe_radius_um,
):
    """Retain all covered targets but stop at the final target-label sample."""
    endpoint = _candidate_endpoint_target_summary(candidate, target_ids)
    terminal_depth_um = endpoint.get("lastTargetExitDepthUm")
    if terminal_depth_um is None:
        return None
    try:
        depth_scale = float((correction_factors or DEFAULT_CORRECTION_FACTORS).get("dv", 1.0))
    except Exception:
        return None

    # The endpoint summary samples the exact physical tip as well as regular
    # 25 um samples. Rebuilding at the final in-target depth therefore makes
    # insertion depth a decision variable without treating a deduplicated
    # voxel-array index as a physical distance.
    current_depth_um = float(terminal_depth_um)
    for _ in range(3):
        raw_depth_um = current_depth_um / max(abs(depth_scale), 1e-9)
        if raw_depth_um <= 1e-6:
            return None
        trimmed = _build_candidate_from_geometry(
            base_probe=candidate,
            ap_mm=float(candidate.get("ap_mm", 0.0)),
            ml_mm=float(candidate.get("ml_mm", 0.0)),
            theta_deg=float(candidate.get("theta_deg", 0.0)),
            phi_deg=float(candidate.get("phi_deg", 0.0)),
            depth_um=raw_depth_um,
            target_ids=target_ids,
            params=params,
            constraint_modes=constraint_modes,
            correction_factors=correction_factors,
            probe_radius_um=probe_radius_um,
            source_tag=f"{candidate.get('source', 'grid')}:target_tip",
        )
        if trimmed is None:
            return None
        summary = _candidate_endpoint_target_summary(trimmed, target_ids)
        if bool(summary.get("tipInRequestedTarget")) and float(summary.get("overshootAfterLastTargetUm") or 0.0) <= 1e-6:
            trimmed["endpointTargeting"] = summary
            return trimmed
        next_depth_um = summary.get("lastTargetExitDepthUm")
        if next_depth_um is None or float(next_depth_um) >= current_depth_um - 1e-6:
            return None
        current_depth_um = float(next_depth_um)
    return None


def _probe_direction_unit(theta_deg, phi_deg):
    theta_rad = float(theta_deg) * math.pi / 180.0
    phi_rad = float(phi_deg) * math.pi / 180.0
    vec = np.array(
        [
            math.sin(theta_rad) * math.cos(phi_rad),
            math.cos(theta_rad),
            math.sin(theta_rad) * math.sin(phi_rad),
        ],
        dtype=np.float64,
    )
    norm = float(np.linalg.norm(vec))
    if norm <= 1e-12:
        return np.array([0.0, 1.0, 0.0], dtype=np.float64)
    return vec / norm


def _skull_entry_dv_mm(ap_mm, skull_tilt_deg):
    """Positive-ventral entry displacement in the existing tilted skull plane."""
    return -float(ap_mm) * math.tan(math.radians(float(skull_tilt_deg)))


def _probe_entry_dv_mm(probe):
    value = probe.get("entryDvCorr_mm_corrected", probe.get("entryDvCorr_mm"))
    if value is not None:
        return float(value)
    return _skull_entry_dv_mm(probe.get("ap_mm_corrected", probe.get("ap_mm", 0.0)),
                              probe.get("skullTiltDeg", 0.0))


def _corrected_entry_dv_mm(ap_mm, skull_tilt_deg, correction_factors):
    corr = correction_factors or DEFAULT_CORRECTION_FACTORS
    return (_skull_entry_dv_mm(ap_mm, skull_tilt_deg) * float(corr.get("dv", 1.0))
            + float(corr.get("dvShift", 0.0)))


def _probe_entry_um(ap_mm, ml_mm, skull_tilt_deg=0.0, entry_dv_corr_mm=None):
    tilt_rad = float(skull_tilt_deg) * math.pi / 180.0
    dv_corr_um = -float(ap_mm) * math.tan(tilt_rad) * 1000.0
    if entry_dv_corr_mm is not None:
        try:
            dv_corr_um = float(entry_dv_corr_mm) * 1000.0
        except Exception:
            pass
    return np.array(
        [
            BREGMA_UM[0] - float(ap_mm) * 1000.0,
            BREGMA_UM[1] + dv_corr_um,
            BREGMA_UM[2] + float(ml_mm) * 1000.0,
        ],
        dtype=np.float64,
    )


def _build_probe_contact_offsets_um(direction, radius_um, use_cylinder=True):
    if not use_cylinder or radius_um <= 1e-6:
        return [np.zeros(3, dtype=np.float64)]

    dir_unit = np.asarray(direction, dtype=np.float64)
    ref = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    if abs(float(np.dot(dir_unit, ref))) > 0.95:
        ref = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    axis_a = np.cross(dir_unit, ref)
    axis_a_norm = float(np.linalg.norm(axis_a))
    if axis_a_norm <= 1e-12:
        return [np.zeros(3, dtype=np.float64)]
    axis_a = axis_a / axis_a_norm
    axis_b = np.cross(dir_unit, axis_a)
    axis_b_norm = float(np.linalg.norm(axis_b))
    if axis_b_norm <= 1e-12:
        return [np.zeros(3, dtype=np.float64)]
    axis_b = axis_b / axis_b_norm

    offsets = [np.zeros(3, dtype=np.float64)]
    radii = [0.55 * float(radius_um), float(radius_um)]
    angles = [0.0, 90.0, 180.0, 270.0]
    for rr in radii:
        for deg in angles:
            ang = deg * math.pi / 180.0
            offsets.append(axis_a * (math.cos(ang) * rr) + axis_b * (math.sin(ang) * rr))
    return offsets


def _probe_spin_deg(probe):
    values = [_require_number(key, probe[key]) for key in ("spin_deg", "spin") if key in probe]
    if len(values) == 2 and values[0] != values[1]:
        raise ContractError("spin_deg and spin must agree")
    return values[0] if values else 0.0


def _probe_hardware_geometry(probe, *, force_centerline=False):
    """Return physical shank geometry used by contact/collision explanations."""
    raw_type = str(probe.get("probeType") or probe.get("probePreset") or "").strip()
    ptype = raw_type.lower()

    def _num(*keys, default=None):
        for key in keys:
            if key in probe and probe.get(key) not in (None, ""):
                try:
                    return float(probe.get(key))
                except Exception:
                    continue
        return default

    if force_centerline:
        shank_count = 1
    else:
        shank_count = int(max(1, round(_num("shankCount", "shanks", "nShanks", default=0) or 0)))
        if shank_count <= 0:
            if "4sh" in ptype or "4-sh" in ptype:
                shank_count = 4
            else:
                shank_count = 1

    spacing_um = _num("shankSpacingUm", "shankPitchUm", "shankPitch", default=None)
    if spacing_um is None:
        spacing_um = 250.0 if shank_count > 1 else 0.0
    spacing_um = float(max(0.0, spacing_um))
    radius_um = _num("radiusUm", "probeRadiusUm", default=None)
    if radius_um is None:
        width_um = _num("shankWidthUm", "shankWidth", default=70.0)
        radius_um = max(5.0, float(width_um) / 2.0)
    radius_um = float(max(0.0, radius_um))
    offsets_um = [
        (float(i) - (float(shank_count) - 1.0) / 2.0) * spacing_um
        for i in range(shank_count)
    ]
    return {
        "probeType": raw_type or "unknown",
        "shankCount": int(shank_count),
        "shankSpacingUm": float(spacing_um),
        "probeRadiusUm": float(radius_um),
        "shankOffsetsUm": offsets_um,
        "spinDeg": _probe_spin_deg(probe),
    }


def _probe_lateral_axis_um(direction, spin_deg=0.0):
    """Rotate projected ML around the insertion axis in CCF AP/DV/ML coordinates."""
    spin_deg = _require_number("spin_deg", spin_deg)
    dir_unit = np.asarray(direction, dtype=np.float64)
    n = float(np.linalg.norm(dir_unit))
    if n <= 1e-12:
        return np.array([0.0, 0.0, 1.0], dtype=np.float64)
    dir_unit = dir_unit / n
    ml_axis = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    lateral = ml_axis - dir_unit * float(np.dot(ml_axis, dir_unit))
    ln = float(np.linalg.norm(lateral))
    if ln <= 1e-9:
        lateral = np.cross(dir_unit, np.array([0.0, 1.0, 0.0], dtype=np.float64))
        ln = float(np.linalg.norm(lateral))
    if ln <= 1e-9:
        lateral = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        ln = 1.0
    lateral = lateral / ln
    if spin_deg == 0.0:
        return lateral
    angle = math.radians(spin_deg % 360.0)
    return lateral * math.cos(angle) + np.cross(dir_unit, lateral) * math.sin(angle)


def _build_probe_swept_contact_samples_um(probe, direction, radius_um, use_cylinder=True):
    force_centerline = not bool(use_cylinder)
    geom = _probe_hardware_geometry(probe, force_centerline=force_centerline)
    if not use_cylinder:
        geom["probeRadiusUm"] = 0.0
        geom["shankCount"] = 1
        geom["shankSpacingUm"] = 0.0
        geom["shankOffsetsUm"] = [0.0]
    radius_um = float(max(0.0, radius_um if use_cylinder else 0.0))
    radial_offsets = _build_probe_contact_offsets_um(direction, radius_um, use_cylinder=use_cylinder)
    lateral_axis = _probe_lateral_axis_um(direction, geom["spinDeg"])
    samples = []
    for shank_idx, offset_scalar_um in enumerate(geom.get("shankOffsetsUm") or [0.0]):
        shank_offset = lateral_axis * float(offset_scalar_um)
        for radial in radial_offsets:
            samples.append(
                {
                    "shankIndex": int(shank_idx),
                    "shankOffsetUm": float(offset_scalar_um),
                    "offset": shank_offset + np.asarray(radial, dtype=np.float64),
                }
            )
    if not samples:
        samples = [{"shankIndex": 0, "shankOffsetUm": 0.0, "offset": np.zeros(3, dtype=np.float64)}]
    return samples, geom


def _pick_structure_label_from_samples(
    annotation,
    center_um,
    offsets_um,
):
    labels = []
    for off in offsets_um:
        p = center_um + off
        vi = int(round(float(p[0]) / VOXEL_SIZE_UM))
        vj = int(round(float(p[1]) / VOXEL_SIZE_UM))
        vk = int(round(float(p[2]) / VOXEL_SIZE_UM))
        if not voxel_in_bounds(vi, vj, vk):
            continue
        sid = int(annotation[vi, vj, vk])
        if sid <= 0:
            continue
        labels.append(sid)

    if not labels:
        return None, 0.0

    counts = {}
    for sid in labels:
        counts[sid] = counts.get(sid, 0) + 1
    best_sid = max(counts.keys(), key=lambda s: (counts[s], -int(s)))
    strength = float(counts[best_sid]) / float(max(1, len(labels)))
    return int(best_sid), float(strength)


def _pick_structure_contacts_from_samples(annotation, center_um, swept_samples):
    """Return every anatomical label touched by the physical swept probe sample set."""
    contacts = {}
    valid_sample_count = 0
    for sample in swept_samples:
        off = np.asarray(sample.get("offset", np.zeros(3)), dtype=np.float64)
        p = center_um + off
        vi = int(round(float(p[0]) / VOXEL_SIZE_UM))
        vj = int(round(float(p[1]) / VOXEL_SIZE_UM))
        vk = int(round(float(p[2]) / VOXEL_SIZE_UM))
        if not voxel_in_bounds(vi, vj, vk):
            continue
        valid_sample_count += 1
        sid = int(annotation[vi, vj, vk])
        if sid <= 0:
            continue
        rec = contacts.setdefault(
            sid,
            {
                "regionId": int(sid),
                "count": 0,
                "shankIndices": set(),
                "shankOffsetsUm": set(),
                "hemisphereCounts": {},
            },
        )
        rec["count"] += 1
        shank_idx = int(sample.get("shankIndex", 0))
        rec["shankIndices"].add(shank_idx)
        rec["shankOffsetsUm"].add(round(float(sample.get("shankOffsetUm", 0.0)), 3))
        ml_um = float(p[2] - BREGMA_UM[2])
        hemisphere = "midline"
        if ml_um > 1e-6:
            hemisphere = "right"
        elif ml_um < -1e-6:
            hemisphere = "left"
        hemi_counts = rec["hemisphereCounts"]
        hemi_counts[hemisphere] = int(hemi_counts.get(hemisphere, 0)) + 1

    out = []
    denom = max(1, valid_sample_count)
    for rec in contacts.values():
        hemi_counts = rec.get("hemisphereCounts", {})
        hemisphere = (
            max(hemi_counts.keys(), key=lambda k: (hemi_counts.get(k, 0), k))
            if hemi_counts
            else "midline"
        )
        out.append(
            {
                "regionId": int(rec["regionId"]),
                "contactStrength": float(rec["count"]) / float(denom),
                "hemisphere": hemisphere,
                "hemispheres": sorted(hemi_counts),
                "hemisphereCounts": dict(sorted(hemi_counts.items())),
                "shankIndices": sorted(int(x) for x in rec.get("shankIndices", set())),
                "shankOffsetsUm": sorted(float(x) for x in rec.get("shankOffsetsUm", set())),
            }
        )
    out.sort(key=lambda x: (-float(x.get("contactStrength", 0.0)), int(x.get("regionId", 0))))
    return out


def _risk_summary_for_contact_interval(d_start_um, d_end_um, risk_diag):
    summary = {
        "riskClass": None,
        "minDistanceMm": None,
        "thresholdMm": None,
        "riskSeverity": None,
    }
    if not isinstance(risk_diag, dict):
        return summary
    best_seg = None
    best_overlap = 0.0
    for seg in risk_diag.get("dangerSegmentsTop3", []) or []:
        try:
            s_um = float(seg.get("startDepthMm", seg.get("startMm", 0.0))) * 1000.0
            e_um = float(seg.get("endDepthMm", seg.get("endMm", s_um / 1000.0))) * 1000.0
        except Exception:
            continue
        overlap = max(0.0, min(float(d_end_um), e_um) - max(float(d_start_um), s_um))
        if overlap > best_overlap:
            best_overlap = overlap
            best_seg = seg
    if isinstance(best_seg, dict) and best_overlap > 0:
        summary.update(
            {
                "riskClass": str(
                    best_seg.get("sourceClass")
                    or best_seg.get("hitStructureClass")
                    or best_seg.get("class")
                    or best_seg.get("bucket")
                    or ""
                ).strip() or None,
                "minDistanceMm": best_seg.get("minDistanceMm"),
                "thresholdMm": best_seg.get("thresholdMm"),
                "riskSeverity": best_seg.get("severity"),
            }
        )
        return summary
    summary.update(
        {
            "riskClass": risk_diag.get("nearestSourceClass"),
            "minDistanceMm": risk_diag.get("minDistanceMm"),
        }
    )
    thresholds = risk_diag.get("classThresholdMm")
    if isinstance(thresholds, dict):
        cls = str(summary.get("riskClass") or "default")
        summary["thresholdMm"] = thresholds.get(cls, thresholds.get("default"))
    return summary


def _resolve_structure_display(sid, struct_snapshot, planner_id_to_key, acronym_by_id):
    sid_i = int(sid)
    rec = struct_snapshot.get(sid_i, {}) if isinstance(struct_snapshot, dict) else {}
    acronym = str(rec.get("acronym") or "").strip()
    if not acronym:
        acronym = str(planner_id_to_key.get(sid_i) or "").strip()
    if not acronym:
        acronym = str(acronym_by_id.get(sid_i) or "").strip()
    if not acronym:
        acronym = f"RID_{sid_i}"
    name = str(rec.get("name") or acronym).strip() or acronym
    return acronym, name


def _compute_probe_contact_profile(
    probe,
    *,
    step_um=25.0,
    min_contact_length_um=0.0,
    use_cylinder=True,
):
    with atlas_lock:
        annotation = atlas_annotation
        atlas_hash = atlas_signature.get("hash")
        struct_snapshot = dict(structure_map)
        planner_snapshot = {
            int(v.get("id")): str(k)
            for k, v in PLANNER_REGIONS.items()
            if isinstance(v, dict) and v.get("id") is not None
        }
        acronym_snapshot = {
            int(v.get("id")): str(v.get("acronym") or k).strip()
            for k, v in acronym_record_map.items()
            if isinstance(v, dict) and v.get("id") is not None
        }
    if annotation is None:
        return {
            "samplingStepUm": float(step_um),
            "contactRegionSet": [],
            "contactIntervals": [],
            "contactProfile": [],
            "mode": "unavailable",
        }

    ap_mm = float(probe.get("ap_mm_corrected", probe.get("ap_mm", probe.get("ap", 0.0))))
    ml_mm = float(probe.get("ml_mm_corrected", probe.get("ml_mm", probe.get("ml", 0.0))))
    theta_deg = float(probe.get("theta_deg", probe.get("theta", 0.0)))
    phi_deg = float(probe.get("phi_deg", probe.get("phi", 0.0)))
    depth_um = float(probe.get("depthUm_corrected", probe.get("depthUm", 0.0)))
    skull_tilt_deg = float(probe.get("skullTiltDeg", 0.0))
    entry_dv_corr_mm = probe.get("entryDvCorr_mm_corrected", probe.get("entryDvCorr_mm"))
    probe_geom = _probe_hardware_geometry(probe, force_centerline=not use_cylinder)
    radius_um = float(probe_geom.get("probeRadiusUm", probe.get("radiusUm", 35.0)))
    if not use_cylinder:
        radius_um = 0.0
    shank_offsets_um = [float(x) for x in (probe_geom.get("shankOffsetsUm") or [0.0])]
    hull_radius_um = float(radius_um + (max(abs(x) for x in shank_offsets_um) if shank_offsets_um else 0.0))
    probe_id = str(probe.get("probeId") or probe.get("id") or probe.get("label") or "").strip()
    sampling_step_um = float(max(5.0, step_um))
    min_len_um = float(max(0.0, min_contact_length_um))

    cache_key_payload = {
        "contactSchemaVersion": 2,
        "atlasHash": atlas_hash,
        "ap": round(ap_mm, 4),
        "ml": round(ml_mm, 4),
        "theta": round(theta_deg, 4),
        "phi": round(phi_deg, 4),
        "depthUm": round(depth_um, 2),
        "skullTiltDeg": round(skull_tilt_deg, 4),
        "entryDvCorrMm": round(float(entry_dv_corr_mm), 4)
        if entry_dv_corr_mm is not None
        else None,
        "radiusUm": round(radius_um, 3),
        "hullRadiusUm": round(hull_radius_um, 3),
        "probeType": str(probe_geom.get("probeType") or ""),
        "shankCount": int(probe_geom.get("shankCount", 1)),
        "shankSpacingUm": round(float(probe_geom.get("shankSpacingUm", 0.0)), 3),
        "spinDeg": probe_geom["spinDeg"],
        "stepUm": round(sampling_step_um, 3),
        "minLengthUm": round(min_len_um, 3),
        "mode": "cylinder" if use_cylinder else "centerline",
    }
    cache_key = hashlib.sha256(
        json.dumps(cache_key_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    with contact_profile_cache_lock:
        cached = contact_profile_cache.get(cache_key)
        if isinstance(cached, dict):
            return deepcopy(cached)

    direction = _probe_direction_unit(theta_deg, phi_deg)
    entry_um = _probe_entry_um(
        ap_mm,
        ml_mm,
        skull_tilt_deg=skull_tilt_deg,
        entry_dv_corr_mm=entry_dv_corr_mm,
    )
    max_depth = (ATLAS_SHAPE[1] * VOXEL_SIZE_UM) - BREGMA_UM[1] - 50.0
    effective_depth = float(max(0.0, min(depth_um, max_depth)))
    n_steps = int(effective_depth / sampling_step_um) + 1
    swept_samples, probe_geom = _build_probe_swept_contact_samples_um(
        probe,
        direction,
        radius_um,
        use_cylinder=use_cylinder,
    )
    radius_um = float(probe_geom.get("probeRadiusUm", radius_um))
    shank_offsets_um = [float(x) for x in (probe_geom.get("shankOffsetsUm") or [0.0])]
    hull_radius_um = float(radius_um + (max(abs(x) for x in shank_offsets_um) if shank_offsets_um else 0.0))
    collision_model = (
        "multi_shank_swept_cylinder"
        if use_cylinder and int(probe_geom.get("shankCount", 1)) > 1
        else ("swept_cylinder" if use_cylinder else "centerline")
    )

    risk_diag = None
    try:
        risk_probe = dict(probe)
        risk_probe["radiusUm"] = hull_radius_um if use_cylinder else 0.0
        risk_probe["probeRadiusUm"] = hull_radius_um if use_cylinder else 0.0
        risk_probe["shankCount"] = int(probe_geom.get("shankCount", 1))
        risk_probe["shankSpacingUm"] = float(probe_geom.get("shankSpacingUm", 0.0))
        risk_diag = _compute_probe_risk_diagnostics(
            risk_probe,
            step_um=max(25.0, sampling_step_um),
            threshold_mm=None,
        )
    except Exception:
        risk_diag = None

    samples_by_region = {}
    for i_step in range(n_steps):
        d_um = float(i_step) * sampling_step_um
        center_um = entry_um + direction * d_um
        for contact in _pick_structure_contacts_from_samples(annotation, center_um, swept_samples):
            sid = int(contact.get("regionId", 0))
            if sid <= 0:
                continue
            samples_by_region.setdefault(sid, []).append(
                {
                    "regionId": sid,
                    "depthUm": int(round(d_um)),
                    "contactStrength": float(contact.get("contactStrength", 0.0)),
                    "hemisphere": str(contact.get("hemisphere") or "midline"),
                    "hemisphereCounts": dict(contact["hemisphereCounts"]),
                    "shankIndices": list(contact.get("shankIndices") or []),
                    "shankOffsetsUm": list(contact.get("shankOffsetsUm") or []),
                }
            )

    intervals = []

    def _flush_contact_interval(current):
        if current is None:
            return
        seg_len = int(current["dEndUm"] - current["dStartUm"] + sampling_step_um)
        if seg_len < min_len_um:
            return
        acronym, name = _resolve_structure_display(
            current["regionId"],
            struct_snapshot,
            planner_snapshot,
            acronym_snapshot,
        )
        hemi_counts = current.get("_hemisphereCounts", {})
        hemi_major = (
            max(hemi_counts.keys(), key=lambda k: (hemi_counts.get(k, 0), k))
            if hemi_counts
            else "midline"
        )
        risk_summary = _risk_summary_for_contact_interval(
            int(current["dStartUm"]),
            int(current["dEndUm"]),
            risk_diag,
        )
        intervals.append(
            {
                "probeId": probe_id,
                "regionId": int(current["regionId"]),
                "acronym": acronym,
                "region": acronym,
                "key": acronym,
                "label": acronym,
                "name": name,
                "dStartUm": int(current["dStartUm"]),
                "dEndUm": int(current["dEndUm"]),
                "lengthUm": int(seg_len),
                "hemisphere": hemi_major,
                "hemispheres": sorted(current["_sampleHemisphereCounts"]),
                "hemisphereCounts": dict(sorted(current["_sampleHemisphereCounts"].items())),
                "contactStrength": round(
                    float(current.get("_strengthAcc", 0.0)) / float(max(1, current.get("_samples", 1))),
                    6,
                ),
                "riskClass": risk_summary.get("riskClass"),
                "minDistanceMm": risk_summary.get("minDistanceMm"),
                "thresholdMm": risk_summary.get("thresholdMm"),
                "riskSeverity": risk_summary.get("riskSeverity"),
                "mode": "cylinder" if use_cylinder else "centerline",
                "collisionModel": collision_model,
                "probeRadiusUm": round(float(radius_um), 6),
                "physicalHullRadiusUm": round(float(hull_radius_um), 6),
                "shankCount": int(probe_geom.get("shankCount", 1)),
                "shankSpacingUm": round(float(probe_geom.get("shankSpacingUm", 0.0)), 6),
                "shankIndices": sorted(int(x) for x in current.get("_shankIndices", set())),
                "shankOffsetsUm": sorted(float(x) for x in current.get("_shankOffsetsUm", set())),
            }
        )

    for sid, region_samples in samples_by_region.items():
        current = None
        for sample in sorted(region_samples, key=lambda x: int(x.get("depthUm", 0))):
            depth_um_i = int(sample["depthUm"])
            hemi = str(sample.get("hemisphere") or "midline")
            sample_hemi_counts = sample["hemisphereCounts"]
            strength = float(sample.get("contactStrength", 0.0))
            shank_indices = set(int(x) for x in sample.get("shankIndices", []) if x is not None)
            shank_offsets = set(float(x) for x in sample.get("shankOffsetsUm", []) if x is not None)
            if current is None or (depth_um_i - int(current["dEndUm"])) > int(round(sampling_step_um * 1.5)):
                _flush_contact_interval(current)
                current = {
                    "regionId": int(sid),
                    "dStartUm": depth_um_i,
                    "dEndUm": depth_um_i,
                    "_strengthAcc": strength,
                    "_samples": 1,
                    "_hemisphereCounts": {hemi: 1},
                    "_sampleHemisphereCounts": dict(sample_hemi_counts),
                    "_shankIndices": set(shank_indices),
                    "_shankOffsetsUm": set(shank_offsets),
                }
            else:
                current["dEndUm"] = depth_um_i
                current["_strengthAcc"] = float(current["_strengthAcc"]) + strength
                current["_samples"] = int(current["_samples"]) + 1
                hemi_counts = current.get("_hemisphereCounts", {})
                hemi_counts[hemi] = int(hemi_counts.get(hemi, 0)) + 1
                current["_hemisphereCounts"] = hemi_counts
                # Keep minority shank/radial contacts; the legacy label is only a majority summary.
                sample_counts = current["_sampleHemisphereCounts"]
                for side, count in sample_hemi_counts.items():
                    sample_counts[side] = sample_counts.get(side, 0) + count
                current.setdefault("_shankIndices", set()).update(shank_indices)
                current.setdefault("_shankOffsetsUm", set()).update(shank_offsets)
        _flush_contact_interval(current)

    aggregate = {}
    for item in intervals:
        key = str(item.get("acronym") or f"RID_{item.get('regionId')}")
        agg = aggregate.get(key)
        if agg is None:
            agg = {
                "regionId": int(item.get("regionId", 0)),
                "acronym": key,
                "name": str(item.get("name") or key),
                "totalLengthUm": 0,
                "maxStrength": 0.0,
                "intervalCount": 0,
                "hemispheres": set(),
            }
            aggregate[key] = agg
        agg["totalLengthUm"] += int(item.get("lengthUm", 0))
        agg["maxStrength"] = max(float(agg["maxStrength"]), float(item.get("contactStrength", 0.0)))
        agg["intervalCount"] += 1
        agg["hemispheres"].update(item["hemispheres"])

    contact_profile = []
    for key, agg in aggregate.items():
        contact_profile.append(
            {
                "regionId": int(agg["regionId"]),
                "acronym": key,
                "name": str(agg["name"]),
                "totalLengthUm": int(agg["totalLengthUm"]),
                "contactStrength": round(float(agg["maxStrength"]), 6),
                "intervalCount": int(agg["intervalCount"]),
                "hemispheres": sorted(list(agg["hemispheres"])),
            }
        )
    contact_profile.sort(key=lambda x: (-int(x.get("totalLengthUm", 0)), str(x.get("acronym", ""))))
    intervals.sort(key=lambda x: (int(x.get("dStartUm", 0)), -int(x.get("lengthUm", 0))))

    payload = {
        "samplingStepUm": float(sampling_step_um),
        "contactRegionSet": [str(x.get("acronym")) for x in contact_profile],
        "contactIntervals": intervals,
        "contactProfile": contact_profile,
        "mode": "cylinder" if use_cylinder else "centerline",
        "collisionModel": collision_model,
        "physicalProbe": {
            "probeType": str(probe_geom.get("probeType") or ""),
            "shankCount": int(probe_geom.get("shankCount", 1)),
            "shankSpacingUm": round(float(probe_geom.get("shankSpacingUm", 0.0)), 6),
            "probeRadiusUm": round(float(radius_um), 6),
            "physicalHullRadiusUm": round(float(hull_radius_um), 6),
            "shankOffsetsUm": [round(float(x), 6) for x in shank_offsets_um],
            "spinDeg": probe_geom["spinDeg"],
            "spinConvention": "ccf_ap_dv_ml_right_hand_about_centerline_projected_ml",
            "collisionModel": collision_model,
        },
    }

    with contact_profile_cache_lock:
        if len(contact_profile_cache) >= max(64, CONTACT_PROFILE_CACHE_MAX):
            contact_profile_cache.clear()
        contact_profile_cache[cache_key] = payload
    return deepcopy(payload)


#        Optimization helpers                                                                                                             

def seg_seg_dist(p0, p1, q0, q1):
    """Minimum Euclidean distance between finite segments, including points."""
    p0, p1, q0, q1 = (np.asarray(p, dtype=np.float64) for p in (p0, p1, q0, q1))
    u = p1 - p0
    v = q1 - q0
    w0 = p0 - q0
    a, c = float(np.dot(u, u)), float(np.dot(v, v))

    def point_segment_squared(offset, direction, length_squared):
        t = min(1.0, max(0.0, float(np.dot(offset, direction)) / length_squared)) if length_squared else 0.0
        residual = offset - t * direction
        return float(np.dot(residual, residual))

    # A constrained minimum lies on one of four boundaries or at an interior
    # least-squares solution. SVD avoids cancellation in a*c - dot(u,v)**2.
    best = min(point_segment_squared(w0, v, c),
               point_segment_squared(p1 - q0, v, c),
               point_segment_squared(-w0, u, a),
               point_segment_squared(q1 - p0, u, a))
    if a and c:
        parameters = np.linalg.lstsq(np.column_stack((u, -v)), -w0, rcond=None)[0]
        s, t = parameters
        if 0.0 <= s <= 1.0 and 0.0 <= t <= 1.0:
            residual = w0 + s * u - t * v
            best = min(best, float(np.dot(residual, residual)))
    return float(math.sqrt(best))


def _probe_radius_um(probe):
    try:
        return max(0.0, float(probe.get("radiusUm", probe.get("probeRadiusUm", 35.0))))
    except Exception:
        return 35.0


def _probe_shank_segments_um(probe):
    try:
        entry = np.asarray(probe["entry_xyz"], dtype=np.float64)
        tip = np.asarray(probe["tip_xyz"], dtype=np.float64)
    except Exception:
        return []
    direction = tip - entry
    dn = float(np.linalg.norm(direction))
    if dn <= 1e-9:
        return [(entry, tip, _probe_radius_um(probe), 0)]
    direction = direction / dn
    geom = _probe_hardware_geometry(probe)
    lateral = _probe_lateral_axis_um(direction, geom["spinDeg"])
    radius_um = float(geom.get("probeRadiusUm", _probe_radius_um(probe)))
    segments = []
    for shank_idx, offset_scalar_um in enumerate(geom.get("shankOffsetsUm") or [0.0]):
        offset = lateral * float(offset_scalar_um)
        segments.append((entry + offset, tip + offset, radius_um, int(shank_idx)))
    return segments or [(entry, tip, radius_um, 0)]


def trajectory_surface_clearance_um(a, b):
    segs_a = _probe_shank_segments_um(a)
    segs_b = _probe_shank_segments_um(b)
    if not segs_a or not segs_b:
        return float("inf")
    best = float("inf")
    for ea, ta, ra, _ia in segs_a:
        for eb, tb, rb, _ib in segs_b:
            centerline = seg_seg_dist(ea, ta, eb, tb)
            surface = centerline - float(ra) - float(rb)
            best = min(best, float(surface))
    return float(best)


def trajs_collide(a, b, min_clearance_um, min_entry_um=3500):
    segs_a = _probe_shank_segments_um(a)
    segs_b = _probe_shank_segments_um(b)
    if not segs_a or not segs_b:
        return False
    entry_surface = min(
        float(np.linalg.norm(ea - eb) - float(ra) - float(rb))
        for ea, _ta, ra, _ia in segs_a
        for eb, _tb, rb, _ib in segs_b
    )
    if entry_surface < min_entry_um:
        return True
    return trajectory_surface_clearance_um(a, b) < min_clearance_um


def make_trajectory_xyz(ap_mm, ml_mm, theta_deg, phi_deg, depth_um, skull_tilt_deg=0.0,
                        *, entry_dv_corr_mm=None):
    """Return CCF micrometre geometry and positive-ventral entry DV in mm."""
    tilt_rad   = skull_tilt_deg * math.pi / 180.0
    dv_corr_um = -ap_mm * math.tan(tilt_rad) * 1000.0
    if entry_dv_corr_mm is not None:
        dv_corr_um = float(entry_dv_corr_mm) * 1000.0
    entry = np.array([
        BREGMA_UM[0] - ap_mm * 1000.0,
        BREGMA_UM[1] + dv_corr_um,
        BREGMA_UM[2] + ml_mm * 1000.0,
    ])
    th = theta_deg * math.pi / 180.0
    ph = phi_deg   * math.pi / 180.0
    direction = np.array([
        math.sin(th) * math.cos(ph),
        math.cos(th),
        math.sin(th) * math.sin(ph),
    ])
    tip = entry + direction * depth_um
    return entry.tolist(), tip.tolist(), direction.tolist(), dv_corr_um / 1000.0


def _tip_ap_dv_mm(ap_mm, entry_dv_mm, direction, depth_um):
    """CCF X points posteriorly; reported AP points anteriorly, DV ventrally."""
    depth_mm = float(depth_um) / 1000.0
    return (float(ap_mm) - float(direction[0]) * depth_mm,
            float(entry_dv_mm) + float(direction[1]) * depth_mm)


def trajectory_tip_mm(probe):
    """Compute tip AP/DV (mm from bregma/skull frame) from a trajectory payload."""
    ap = float(probe.get("ap_mm_corrected", probe.get("ap_mm", 0)))
    depth_um = float(probe.get("depthUm_corrected", probe.get("depthUm", 0)))
    direction = probe.get("dir")
    if direction is None:
        direction = _probe_direction_unit(probe.get("theta_deg", 0.0), probe.get("phi_deg", 0.0))
    return _tip_ap_dv_mm(ap, _probe_entry_dv_mm(probe), direction, depth_um)


def has_hemisphere_mismatch(probe):
    """Return True when probe entry hemisphere mismatches any covered region hemisphere."""
    ml = float(probe.get("ml_mm_corrected", probe.get("ml_mm", 0)))
    covered = probe.get("coveredRegions", []) or []
    for key in covered:
        region = PLANNER_REGIONS.get(key)
        if not region:
            continue
        region_ml = float(region.get("ml", 0))
        if abs(region_ml) < 0.5 or abs(ml) < 0.5:
            continue
        if (ml > 0) != (region_ml > 0):
            return True
    return False


def _clip01(value):
    return float(max(0.0, min(1.0, value)))


def _stable_seed_from_values(*values):
    canonical = "|".join(str(v) for v in values)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) & 0xFFFFFFFF


def _normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _softplus(x):
    # Stable softplus for risk penalties.
    if x > 35.0:
        return float(x)
    if x < -35.0:
        return math.exp(float(x))
    return math.log1p(math.exp(float(x)))


def _resolve_parallel_workers(value, default_workers=1):
    try:
        workers = int(value)
    except Exception:
        workers = int(default_workers)
    workers = max(1, workers)
    # Keep thread count bounded to avoid oversubscription on dense NumPy workloads.
    return int(min(workers, max(1, (os.cpu_count() or workers))))


def _bounded_cache_put(cache_obj, lock_obj, key, value, max_size):
    with lock_obj:
        cache_obj[key] = value
        if len(cache_obj) > int(max_size):
            try:
                cache_obj.pop(next(iter(cache_obj)))
            except Exception:
                cache_obj.clear()
                cache_obj[key] = value


def _bounded_cache_get(cache_obj, lock_obj, key):
    with lock_obj:
        return cache_obj.get(key)


def _set_runtime_thread_budget(workers):
    threads = max(1, int(workers))
    os.environ["OMP_NUM_THREADS"] = str(threads)
    os.environ["MKL_NUM_THREADS"] = str(threads)
    os.environ["OPENBLAS_NUM_THREADS"] = str(threads)
    os.environ["NUMEXPR_MAX_THREADS"] = str(threads)
    try:
        import contextlib
        import io

        # Optional accelerator only; some local NumPy 2.x environments still
        # have NumExpr wheels compiled against NumPy 1.x and print ABI errors
        # to stderr during import before raising.
        with contextlib.redirect_stderr(io.StringIO()):
            import numexpr as _ne  # pragma: no cover - optional
        _ne.set_num_threads(threads)
    except Exception:
        pass


def _wilson_ci95(successes, total):
    n = int(max(0, total))
    if n <= 0:
        return [0.0, 1.0]
    k = int(max(0, min(successes, n)))
    z = 1.959963984540054
    phat = float(k) / float(n)
    denom = 1.0 + (z * z) / n
    center = (phat + (z * z) / (2.0 * n)) / denom
    margin = (
        z
        * math.sqrt((phat * (1.0 - phat) / n) + (z * z) / (4.0 * n * n))
        / denom
    )
    return [_clip01(center - margin), _clip01(center + margin)]


def _estimate_bounds_margin_mm(candidate):
    ap = float(candidate.get("ap_mm_corrected", candidate.get("ap_mm", 0.0)))
    tip_ap, tip_dv = trajectory_tip_mm(candidate)
    margins = [
        ap - ENTRY_AP_MIN_MM,
        ENTRY_AP_MAX_MM - ap,
        tip_ap - TIP_POST_LIMIT_MM,
        MAX_DV_MM - tip_dv,
    ]
    return float(min(margins))


def _estimate_hemisphere_confidence(candidate, sigma_mm):
    ml = float(candidate.get("ml_mm_corrected", candidate.get("ml_mm", 0.0)))
    covered = candidate.get("coveredRegions", []) or []
    region_signs = []
    for key in covered:
        reg = PLANNER_REGIONS.get(key)
        if reg is None:
            continue
        region_ml = float(reg.get("ml", 0.0))
        if abs(region_ml) < 0.2:
            continue
        region_signs.append(1.0 if region_ml > 0 else -1.0)

    if not region_signs:
        return 1.0

    expected_sign = 1.0 if sum(region_signs) >= 0 else -1.0
    if sigma_mm <= 1e-9:
        if abs(ml) < 1e-9:
            return 0.5
        return 1.0 if (ml > 0) == (expected_sign > 0) else 0.0

    z = abs(ml) / sigma_mm
    same_sign_prob = _normal_cdf(z)
    if (ml > 0) == (expected_sign > 0):
        return _clip01(same_sign_prob)
    return _clip01(1.0 - same_sign_prob)


def _drcc_enabled(params):
    return bool(params.get("algorithm") == "drcc" or params.get("drccEnabled", False))


def _resolve_candidate_risk_diag(candidate, params):
    threshold_mm = float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT))
    threshold_by_class = params.get("riskThresholdsMm")
    risk_diag = candidate.get("riskDiagnostics")
    if isinstance(risk_diag, dict):
        return risk_diag
    risk_diag = _compute_probe_risk_diagnostics(
        candidate,
        step_um=RISK_DIAG_STEP_UM,
        threshold_mm=threshold_mm,
        threshold_by_class=threshold_by_class,
        cvar_alpha=float(params.get("drccCvarAlpha", DRCC_CVAR_ALPHA_DEFAULT)),
    )
    if isinstance(risk_diag, dict):
        candidate["riskDiagnostics"] = risk_diag
    return risk_diag


def _candidate_drcc_metrics(candidate, params):
    risk_diag = _resolve_candidate_risk_diag(candidate, params)
    min_dist = None
    nearest_cls = None
    risk_integral = 0.0
    risk_cvar = 0.0
    if isinstance(risk_diag, dict):
        nearest_cls = risk_diag.get("nearestSourceClass")
        if risk_diag.get("minDistanceMm") is not None:
            min_dist = float(risk_diag.get("minDistanceMm"))
        risk_integral = float(risk_diag.get("riskIntegralShortfallMm", 0.0) or 0.0)
        risk_cvar = float(risk_diag.get("riskTailShortfallMm", 0.0) or 0.0)

    if min_dist is None:
        min_dist = float(candidate.get("minSafetyDistanceMm", 0.0) or 0.0)

    threshold_mm = float(
        resolve_risk_threshold_mm(params.get("riskThresholdsMm"), nearest_cls)
    )
    sigma = float(
        max(
            1e-6,
            math.sqrt(
                float(params.get("entrySigmaMm", params.get("perturbSigmaMm", 0.15))) ** 2
                + float(params.get("depthSigmaMm", 0.2)) ** 2
                + float(params.get("riskDistanceSigmaMm", RISK_DISTANCE_SIGMA_MM_DEFAULT)) ** 2
            ),
        )
    )
    violate_prob = _clip01(_normal_cdf((threshold_mm - float(min_dist)) / sigma))
    wasserstein_radius = float(params.get("drccWassersteinRadius", DRCC_WASSERSTEIN_RADIUS_DEFAULT))
    worst_prob = _clip01(violate_prob + max(0.0, wasserstein_radius))
    epsilon = float(params.get("drccRiskEpsilon", DRCC_RISK_EPSILON_DEFAULT))
    feasible = bool(worst_prob <= epsilon + 1e-12)

    return {
        "thresholdMm": round(threshold_mm, 6),
        "minDistanceMm": round(float(min_dist), 6),
        "violationProb": round(float(violate_prob), 6),
        "worstCaseViolationProb": round(float(worst_prob), 6),
        "riskIntegralShortfallMm": round(float(risk_integral), 6),
        "riskTailShortfallMm": round(float(risk_cvar), 6),
        "wassersteinRadius": round(float(wasserstein_radius), 6),
        "riskBudgetEpsilon": round(float(epsilon), 6),
        "feasible": feasible,
    }





def _risk_prefilter_sort_key(candidate):
    info = candidate.get("riskPrefilter") if isinstance(candidate.get("riskPrefilter"), dict) else {}
    bucket = str(info.get("bucket") or "unknown").strip().lower()
    rank_map = {
        "feasible": 0,
        "unknown": 1,
        "near": 2,
        "rejected": 3,
    }
    margin = float(info.get("worstMarginMm", -1e9) or -1e9)
    return (rank_map.get(bucket, 3), -margin)


def _risk_class_is_entry_corridor_only(candidate, risk_diag, risk_class, *, step_um=RISK_DIAG_STEP_UM):
    """
    cortex_outside/brain_surface/drilling_surface are boundary semantics, not
    ordinary internal obstacles. A probe necessarily starts outside/at the
    entry surface; that initial entry corridor must not clear the candidate
    pool. True out-of-brain trajectories remain blocked by brainBounds.
    """
    cls = str(risk_class or "").strip().lower()
    if cls not in ENTRY_CORRIDOR_RISK_CLASSES:
        return False
    if not isinstance(risk_diag, dict):
        return False

    brain_summary = candidate.get("brainBoundsDiagnostics")
    if not isinstance(brain_summary, dict) or not brain_summary.get("available"):
        try:
            brain_summary = _candidate_brain_mask_path_summary(candidate, step_um=step_um)
        except Exception:
            brain_summary = None
    if isinstance(brain_summary, dict) and brain_summary.get("available"):
        # Do not mask a real continuity violation; brainBounds owns it.
        if not brain_summary.get("passed", True):
            return False
    return True


def _candidate_risk_margin_summary(
    candidate,
    params,
    *,
    threshold_by_class=None,
    step_um=RISK_DIAG_STEP_UM,
):
    if threshold_by_class is None:
        threshold_by_class = sanitize_risk_thresholds(
            params.get("riskThresholdsMm"),
            default_mm=float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
        )
    risk_diag = candidate.get("riskDiagnostics")
    if not isinstance(risk_diag, dict):
        risk_diag = _compute_probe_risk_diagnostics(
            candidate,
            step_um=step_um,
            threshold_mm=float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
            threshold_by_class=threshold_by_class,
            cvar_alpha=float(params.get("drccCvarAlpha", DRCC_CVAR_ALPHA_DEFAULT)),
        )
        if isinstance(risk_diag, dict):
            candidate["riskDiagnostics"] = risk_diag
    if not isinstance(risk_diag, dict):
        return {
            "available": False,
            "worstMarginMm": None,
            "worstClass": None,
            "distanceMm": None,
            "thresholdMm": None,
            "riskDiagnostics": None,
        }

    class_min_map = (
        risk_diag.get("classMinDistanceMm")
        if isinstance(risk_diag.get("classMinDistanceMm"), dict)
        else {}
    )
    margin_items = []
    ignored_entry_classes = []
    for cls_name, cls_dist in class_min_map.items():
        cls_key = str(cls_name or "").strip().lower()
        if cls_key in ENTRY_CORRIDOR_RISK_CLASSES:
            ignored_entry_classes.append(str(cls_name))
            continue
        try:
            dist_mm = float(cls_dist)
        except Exception:
            continue
        thr_mm = float(resolve_risk_threshold_mm(threshold_by_class, cls_name))
        margin_items.append((str(cls_name), dist_mm - thr_mm, dist_mm, thr_mm))

    if not margin_items:
        nearest_cls = risk_diag.get("nearestSourceClass")
        nearest_key = str(nearest_cls or "").strip().lower()
        if nearest_key in ENTRY_CORRIDOR_RISK_CLASSES:
            if nearest_cls:
                ignored_entry_classes.append(str(nearest_cls))
            return {
                "available": False,
                "worstMarginMm": None,
                "worstClass": None,
                "distanceMm": None,
                "thresholdMm": None,
                "riskDiagnostics": risk_diag,
                "ignoredEntryCorridorClass": str(nearest_cls or ""),
                "ignoredEntryCorridorClasses": sorted(set(ignored_entry_classes)),
            }
        min_dist = risk_diag.get("minSignedDistanceMm")
        if min_dist is None:
            min_dist = risk_diag.get("minDistanceMm")
        try:
            dist_mm = float(min_dist)
        except Exception:
            dist_mm = None
        if dist_mm is not None:
            thr_mm = float(resolve_risk_threshold_mm(threshold_by_class, nearest_cls))
            margin_items.append((str(nearest_cls or "default"), dist_mm - thr_mm, dist_mm, thr_mm))

    if not margin_items:
        return {
            "available": False,
            "worstMarginMm": None,
            "worstClass": None,
            "distanceMm": None,
            "thresholdMm": None,
            "riskDiagnostics": risk_diag,
            "ignoredEntryCorridorClasses": sorted(set(ignored_entry_classes)),
        }

    worst_class, worst_margin, worst_dist, worst_thr = min(margin_items, key=lambda x: x[1])
    return {
        "available": True,
        "worstMarginMm": float(worst_margin),
        "worstClass": worst_class,
        "distanceMm": float(worst_dist),
        "thresholdMm": float(worst_thr),
        "riskDiagnostics": risk_diag,
        "ignoredEntryCorridorClasses": sorted(set(ignored_entry_classes)),
        "classMarginsMm": {
            cls: round(float(margin), 6) for cls, margin, _, _ in margin_items
        },
    }


def _prefilter_candidates_for_auto_avoid(candidates, params, constraint_modes):
    summary = {
        "enabled": bool(params.get("autoAvoidEnabled", AUTO_AVOID_ENABLED_DEFAULT)),
        "requestedCandidateCount": int(len(candidates or [])),
        "keptCandidateCount": int(len(candidates or [])),
        "rejectedCount": 0,
        "nearCount": 0,
        "feasibleCount": 0,
        "keptMode": "disabled",
        "bufferMm": float(params.get("autoAvoidSafetyBufferMm", AUTO_AVOID_SAFETY_BUFFER_MM_DEFAULT)),
        "nearMarginMm": float(params.get("autoAvoidNearMarginMm", AUTO_AVOID_NEAR_MARGIN_MM_DEFAULT)),
        "stepUm": float(params.get("autoAvoidCoarseStepUm", AUTO_AVOID_COARSE_STEP_UM_DEFAULT)),
        "policyMode": "off",
    }
    if not candidates:
        return [], [], summary

    policy = _continuous_risk_policy(constraint_modes)
    summary["policyMode"] = str(policy.get("effectiveMode") or "off")
    if (not summary["enabled"]) or summary["policyMode"] == "off":
        summary["keptMode"] = "disabled"
        for cand in candidates:
            cand["riskPrefilter"] = {
                "bucket": "unknown",
                "worstMarginMm": None,
                "worstClass": None,
                "distanceMm": None,
                "thresholdMm": None,
                "policyMode": summary["policyMode"],
                "bufferMm": summary["bufferMm"],
                "stepUm": summary["stepUm"],
            }
        return list(candidates), [], summary

    # In warn-mode we still compute buckets for guidance/ranking, but we do not
    # hard-drop candidates; this preserves warning/failure-card observability.
    warn_passthrough = summary["policyMode"] == "warn"

    threshold_by_class = sanitize_risk_thresholds(
        params.get("riskThresholdsMm"),
        default_mm=float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
    )
    buffer_mm = float(summary["bufferMm"])
    near_margin_mm = float(summary["nearMarginMm"])
    step_um = float(summary["stepUm"])

    feasible = []
    near = []
    rejected = []

    def _eval_prefilter_candidate(cand):
        risk_summary = _candidate_risk_margin_summary(
            cand,
            params,
            threshold_by_class=threshold_by_class,
            step_um=step_um,
        )
        if not risk_summary.get("available"):
            bucket = "unknown"
            return cand, risk_summary, bucket
        else:
            margin = float(risk_summary.get("worstMarginMm", -1e9))
            if margin >= buffer_mm:
                bucket = "feasible"
            elif margin >= -near_margin_mm:
                bucket = "near"
            else:
                bucket = "rejected"
        return cand, risk_summary, bucket

    worker_count = _resolve_parallel_workers(
        params.get("prefilterWorkers", COMPUTE_PREFILTER_WORKERS_DEFAULT),
        default_workers=COMPUTE_PREFILTER_WORKERS_DEFAULT,
    )
    if worker_count > 1 and len(candidates) >= max(24, worker_count * 4):
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="prefilter") as ex:
            eval_rows = list(ex.map(_eval_prefilter_candidate, candidates))
    else:
        eval_rows = [_eval_prefilter_candidate(cand) for cand in candidates]

    for cand, risk_summary, bucket in eval_rows:
        if bucket in ("unknown", "feasible"):
            feasible.append(cand)
        elif bucket == "near":
            near.append(cand)
        else:
            rejected.append(cand)
        cand["riskPrefilter"] = {
            "bucket": bucket,
            "worstMarginMm": (
                round(float(risk_summary["worstMarginMm"]), 6)
                if risk_summary.get("worstMarginMm") is not None
                else None
            ),
            "worstClass": risk_summary.get("worstClass"),
            "distanceMm": (
                round(float(risk_summary["distanceMm"]), 6)
                if risk_summary.get("distanceMm") is not None
                else None
            ),
            "thresholdMm": (
                round(float(risk_summary["thresholdMm"]), 6)
                if risk_summary.get("thresholdMm") is not None
                else None
            ),
            "policyMode": summary["policyMode"],
            "bufferMm": round(buffer_mm, 6),
            "nearMarginMm": round(near_margin_mm, 6),
            "stepUm": round(step_um, 6),
            "available": bool(risk_summary.get("available")),
        }

    if warn_passthrough:
        kept = list(candidates)
        summary["keptMode"] = "warn_passthrough"
    else:
        kept = feasible if feasible else near
        summary["keptMode"] = "feasible_only" if feasible else "near_only" if near else "none"
    summary["feasibleCount"] = int(len(feasible))
    summary["nearCount"] = int(len(near))
    summary["rejectedCount"] = int(len(rejected))
    summary["keptCandidateCount"] = int(len(kept))
    summary["workersUsed"] = int(worker_count)
    return list(kept), list(near), summary


def _candidate_passes_single_enforced_constraints(
    candidate,
    params,
    probe_params,
    constraint_modes,
    *,
    threshold_by_class=None,
    risk_step_um=RISK_DIAG_STEP_UM,
):
    max_angle = float(params.get("maxAngle", 15))
    min_risk_distance_mm = float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT))
    threshold_by_class = threshold_by_class or sanitize_risk_thresholds(
        params.get("riskThresholdsMm"),
        default_mm=min_risk_distance_mm,
    )

    if constraint_modes.get("maxAngle") == "enforce":
        if float(candidate.get("theta_deg", 0.0)) > max_angle:
            return False, "maxAngle", None

    if constraint_modes.get("lambdaBoundary") == "enforce":
        ap_check = float(candidate.get("ap_mm_corrected", candidate.get("ap_mm", 0.0)))
        if ap_check < LAMBDA_AP_MM:
            return False, "lambdaBoundary", None

    if constraint_modes.get("brainBounds") == "enforce":
        ap_check = float(candidate.get("ap_mm_corrected", candidate.get("ap_mm", 0.0)))
        tip_ap, tip_dv = trajectory_tip_mm(candidate)
        if ap_check < ENTRY_AP_MIN_MM or ap_check > ENTRY_AP_MAX_MM:
            return False, "brainBounds", None
        if tip_ap < TIP_POST_LIMIT_MM or tip_dv > MAX_DV_MM:
            return False, "brainBounds", None
        brain_mask_summary = _candidate_brain_mask_path_summary(candidate, step_um=risk_step_um)
        if brain_mask_summary.get("available") and not brain_mask_summary.get("passed", True):
            return False, "brainBounds", brain_mask_summary

    endpoint_policy = str(params.get("targetEndpointPolicy", "trajectory_intersects")).strip().lower()
    if endpoint_policy == "tip_in_target":
        endpoint_summary = _candidate_endpoint_target_summary(candidate, _candidate_target_ids(candidate))
        candidate["endpointTargeting"] = endpoint_summary
        if not bool(endpoint_summary.get("tipInRequestedTarget")):
            return False, "targetEndpoint", endpoint_summary
        if float(endpoint_summary.get("overshootAfterLastTargetUm") or 0.0) > 1e-6:
            return False, "targetEndpoint", endpoint_summary

    if constraint_modes.get("hemisphere") == "enforce" and has_hemisphere_mismatch(candidate):
        return False, "hemisphere", None

    continuous_mode = _continuous_risk_policy(constraint_modes).get("effectiveMode", "off")
    if continuous_mode == "enforce":
        risk_summary = _candidate_risk_margin_summary(
            candidate,
            params,
            threshold_by_class=threshold_by_class,
            step_um=risk_step_um,
        )
        if risk_summary.get("available") and float(risk_summary.get("worstMarginMm", -1e9)) < 0.0:
            return False, "continuousRiskDistance", risk_summary
        return True, None, risk_summary

    return True, None, None


def _covered_targets_from_selected(selected, target_keys):
    target_set = set(target_keys or [])
    covered = set()
    for cand in selected or []:
        for key in cand.get("coveredRegions", []) or []:
            if key in target_set:
                covered.add(key)
    return covered


def _build_candidate_from_geometry(
    *,
    base_probe,
    ap_mm,
    ml_mm,
    theta_deg,
    phi_deg,
    depth_um,
    target_ids,
    params,
    constraint_modes,
    correction_factors,
    probe_radius_um,
    source_tag="auto_repair_local",
):
    corr = correction_factors or DEFAULT_CORRECTION_FACTORS
    skull_tilt = float(base_probe.get("skullTiltDeg", params.get("skullTiltDeg", 0.0)))
    ap_corr = float(ap_mm) * float(corr.get("ap", 1.0)) + float(corr.get("apShift", 0.0))
    ml_corr = float(ml_mm) * float(corr.get("ml", 1.0)) + float(corr.get("mlShift", 0.0))
    dep_corr = float(depth_um) * float(corr.get("dv", 1.0))
    theta = float(theta_deg)
    phi = float(phi_deg) % 360.0

    entry, tip, direction, dv_corr = make_trajectory_xyz(ap_corr, ml_corr, theta, phi, dep_corr, skull_tilt,
        entry_dv_corr_mm=_corrected_entry_dv_mm(ap_mm, skull_tilt, corr))
    if constraint_modes.get("brainBounds") == "enforce":
        tip_ap, tip_dv = _tip_ap_dv_mm(ap_corr, dv_corr, direction, dep_corr)
        if ap_corr < ENTRY_AP_MIN_MM or ap_corr > ENTRY_AP_MAX_MM:
            return None
        if tip_ap < TIP_POST_LIMIT_MM or tip_dv > MAX_DV_MM:
            return None

    path_indices, path_depths_um = _sample_trajectory_path_indices(
        ap_corr,
        ml_corr,
        theta,
        phi,
        dep_corr,
        skull_tilt_deg=skull_tilt,
        step_um=25.0,
        entry_dv_corr_mm=dv_corr,
    )
    if not path_indices:
        return None
    if constraint_modes.get("brainBounds") == "enforce":
        brain_mask_summary = _brain_mask_path_summary_from_voxels(path_indices, step_um=25.0)
        if brain_mask_summary.get("available") and not brain_mask_summary.get("passed", True):
            return None

    valid_path = [
        (voxel, depth)
        for voxel, depth in zip(path_indices, path_depths_um)
        if voxel_in_bounds(*voxel)
    ]
    traj_voxels = [voxel for voxel, _ in valid_path]
    traj_depths_um = [depth for _, depth in valid_path]
    if not traj_voxels:
        return None
    profile = compute_trajectory_profile(
        traj_voxels,
        list(target_ids.values()),
        step_um=25.0,
        sample_depths_um=traj_depths_um,
    )
    entry_um_arr = np.array(entry, dtype=float)
    tip_um_arr = np.array(tip, dtype=float)
    covered_keys = [
        k for k, sid in target_ids.items()
        if profile.get(sid, {}).get("covered", False)
    ]
    for k, sid in target_ids.items():
        if k in covered_keys:
            continue
        if structure_map.get(sid, {}).get("voxel_count", 0) == 0:
            dist = pt_seg_dist_um(np.array(REGION_CENTROIDS_UM[sid]), entry_um_arr, tip_um_arr)
            if dist <= float(params.get("coverageRadius", 600)):
                covered_keys.append(k)
                fallback_side = "right" if ml_corr > 0 else "left"
                profile[sid] = {
                    "covered": True,
                    "n_voxels": 0,
                    "coverage_frac": 0.0,
                    "entry_depth_um": None,
                    "exit_depth_um": None,
                    "channel_span_um": 0,
                    "voxel_score": 0.0,
                    "left_voxels": 1 if fallback_side == "left" else 0,
                    "right_voxels": 1 if fallback_side == "right" else 0,
                    "coveredSides": [fallback_side],
                }

    if not covered_keys:
        return None

    physical_geom = _probe_hardware_geometry(
        {
            **(params or {}),
            **(base_probe or {}),
            "radiusUm": probe_radius_um,
            "probeRadiusUm": probe_radius_um,
        }
    )
    candidate = {
        "id": base_probe.get("id"),
        "ap_mm": round(float(ap_mm), 5),
        "ml_mm": round(float(ml_mm), 5),
        "depthUm": round(float(depth_um), 3),
        "ap_mm_corrected": round(float(ap_corr), 5),
        "ml_mm_corrected": round(float(ml_corr), 5),
        "depthUm_corrected": round(float(dep_corr), 2),
        "theta_deg": round(float(theta), 4),
        "phi_deg": round(float(phi), 4),
        "skullTiltDeg": round(float(skull_tilt), 4),
        "entryDvCorr_mm": round(_skull_entry_dv_mm(ap_mm, skull_tilt), 5),
        "entryDvCorr_mm_corrected": round(float(dv_corr), 5),
        "entry_xyz": [round(float(x), 1) for x in entry],
        "tip_xyz": [round(float(x), 1) for x in tip],
        "dir": [round(float(x), 6) for x in direction],
        "coveredRegions": list(covered_keys),
        "coveredRegionSides": {
            k: profile[target_ids[k]].get("coveredSides", [])
            for k in covered_keys
            if target_ids[k] in profile
        },
        "overlapVoxels": {k: profile[target_ids[k]].get("n_voxels", 0) for k in covered_keys},
        "nVoxelsTotal": len(traj_voxels),
        "radiusUm": round(float(probe_radius_um), 4),
        "probeRadiusUm": round(float(probe_radius_um), 4),
        "probeType": str(physical_geom.get("probeType") or ""),
        "shanks": int(physical_geom.get("shankCount", 1)),
        "shankCount": int(physical_geom.get("shankCount", 1)),
        "shankSpacingUm": round(float(physical_geom.get("shankSpacingUm", 0.0)), 4),
        "shankPitchUm": round(float(physical_geom.get("shankSpacingUm", 0.0)), 4),
        "source": str(source_tag),
        "brainBoundsDiagnostics": brain_mask_summary if constraint_modes.get("brainBounds") == "enforce" else None,
        "trajectoryProfile": {
            k: profile[target_ids[k]]
            for k in covered_keys
            if target_ids[k] in profile
        },
    }
    if constraint_modes.get("hemisphere") == "enforce" and has_hemisphere_mismatch(candidate):
        return None
    return candidate


def _auto_repair_single_probe(
    probe,
    *,
    selected_others,
    candidate_pool,
    target_ids,
    target_keys,
    params,
    probe_params,
    constraint_modes,
    correction_factors,
    min_clearance,
    min_entry,
):
    threshold_by_class = sanitize_risk_thresholds(
        params.get("riskThresholdsMm"),
        default_mm=float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
    )
    probe_radius_um = max(
        5.0,
        float(
            probe_params.get(
                "probeRadiusUm",
                probe_params.get("shankWidthUm", 70) / 2.0,
            )
        ),
    )

    current_sig = _candidate_signature(probe)
    current_cov = set(probe.get("coveredRegions", []) or [])
    best_pool = None
    best_pool_tuple = None
    for cand in candidate_pool or []:
        if _candidate_signature(cand) == current_sig:
            continue
        cand_cov = set(cand.get("coveredRegions", []) or [])
        if current_cov and not (cand_cov & current_cov):
            continue
        if any(trajs_collide(cand, s, min_clearance, min_entry) for s in selected_others):
            continue
        ok, _, risk_summary = _candidate_passes_single_enforced_constraints(
            cand,
            params,
            probe_params,
            constraint_modes,
            threshold_by_class=threshold_by_class,
            risk_step_um=RISK_DIAG_STEP_UM,
        )
        if not ok:
            continue
        delta = _candidate_min_adjustment_delta(probe, cand) or {
            "deltaEntryUm": 1e9,
            "deltaAngleDeg": 1e9,
            "deltaDepthUm": 1e9,
            "weightedCost": 1e9,
        }
        overlap = len(current_cov & cand_cov)
        margin = (
            float(risk_summary.get("worstMarginMm", 0.0))
            if isinstance(risk_summary, dict) and risk_summary.get("worstMarginMm") is not None
            else -1e9
        )
        cmp_tuple = (
            overlap,
            len(cand_cov),
            margin,
            -float(delta.get("weightedCost", 1e9)),
            float(cand.get("explainScore", 0.0)),
        )
        if best_pool_tuple is None or cmp_tuple > best_pool_tuple:
            best_pool_tuple = cmp_tuple
            best_pool = (cand, delta, risk_summary)

    if best_pool is not None:
        cand, delta, risk_summary = best_pool
        return {
            "success": True,
            "candidate": deepcopy(cand),
            "method": "pool_replacement",
            "details": {
                "deltaEntryUm": round(float(delta.get("deltaEntryUm", 0.0)), 3),
                "deltaApUm": round(float(delta.get("deltaApUm", 0.0)), 3),
                "deltaMlUm": round(float(delta.get("deltaMlUm", 0.0)), 3),
                "deltaAngleDeg": round(float(delta.get("deltaAngleDeg", 0.0)), 6),
                "deltaDepthUm": round(float(delta.get("deltaDepthUm", 0.0)), 3),
                "riskMarginMm": (
                    round(float(risk_summary.get("worstMarginMm", 0.0)), 6)
                    if isinstance(risk_summary, dict) and risk_summary.get("worstMarginMm") is not None
                    else None
                ),
            },
        }

    entry_step = float(params.get("autoRepairEntryStepMm", AUTO_REPAIR_ENTRY_STEP_MM_DEFAULT))
    entry_max = float(params.get("autoRepairEntryMaxShiftMm", AUTO_REPAIR_ENTRY_MAX_SHIFT_MM_DEFAULT))
    angle_step = float(params.get("autoRepairAngleStepDeg", AUTO_REPAIR_ANGLE_STEP_DEG_DEFAULT))
    angle_max = float(params.get("autoRepairAngleMaxAdjustDeg", AUTO_REPAIR_ANGLE_MAX_ADJUST_DEG_DEFAULT))
    depth_step = float(params.get("autoRepairDepthStepMm", AUTO_REPAIR_DEPTH_STEP_MM_DEFAULT))
    depth_max = float(params.get("autoRepairDepthMaxAdjustMm", AUTO_REPAIR_DEPTH_MAX_ADJUST_MM_DEFAULT))
    max_variants = int(params.get("autoRepairMaxVariants", AUTO_REPAIR_MAX_VARIANTS_DEFAULT))

    def _sym_values(step, max_abs):
        vals = [0.0]
        if step <= 1e-9:
            return vals
        n = int(max(0, math.floor(max_abs / step)))
        for i in range(1, n + 1):
            v = round(float(i * step), 6)
            vals.extend([v, -v])
        return vals

    entry_offsets = _sym_values(entry_step, entry_max)
    theta_offsets = _sym_values(angle_step, angle_max)
    phi_offsets = _sym_values(angle_step, min(angle_max, angle_step * 2.0))
    depth_offsets = [0.0]
    if depth_step > 1e-9:
        n_depth = int(max(0, math.floor(depth_max / depth_step)))
        for i in range(1, n_depth + 1):
            depth_offsets.append(round(float(-i * depth_step), 6))

    ap0 = float(probe.get("ap_mm", 0.0))
    ml0 = float(probe.get("ml_mm", 0.0))
    theta0 = float(probe.get("theta_deg", 0.0))
    phi0 = float(probe.get("phi_deg", 0.0))
    depth0 = float(probe.get("depthUm", 10000.0))
    max_theta = float(params.get("maxAngle", 15))
    target_set = set(target_keys or [])

    variants = []
    for dap in entry_offsets:
        for dml in entry_offsets:
            for dtheta in theta_offsets:
                for dphi in phi_offsets:
                    for ddepth in depth_offsets:
                        movement_cost = (
                            math.hypot(dap, dml) * 1000.0
                            + abs(dtheta) * 140.0
                            + abs(ddepth) * 250.0
                        )
                        variants.append((movement_cost, dap, dml, dtheta, dphi, ddepth))
    variants.sort(key=lambda x: x[0])
    if max_variants > 0 and len(variants) > max_variants:
        variants = variants[:max_variants]

    best_feasible = None
    best_feasible_tuple = None
    best_near_margin = -1e9
    evaluated = 0
    require_risk_margin = (
        str(_continuous_risk_policy(constraint_modes).get("effectiveMode", "off")) == "enforce"
    )
    for movement_cost, dap, dml, dtheta, dphi, ddepth in variants:
        ap = ap0 + float(dap)
        ml = ml0 + float(dml)
        theta = max(0.0, min(max_theta, theta0 + float(dtheta)))
        phi = (phi0 + float(dphi)) % 360.0
        depth = max(500.0, depth0 + float(ddepth) * 1000.0)
        cand = _build_candidate_from_geometry(
            base_probe=probe,
            ap_mm=ap,
            ml_mm=ml,
            theta_deg=theta,
            phi_deg=phi,
            depth_um=depth,
            target_ids=target_ids,
            params=params,
            constraint_modes=constraint_modes,
            correction_factors=correction_factors,
            probe_radius_um=probe_radius_um,
            source_tag="auto_repair_local",
        )
        if cand is None:
            continue
        if any(trajs_collide(cand, s, min_clearance, min_entry) for s in selected_others):
            continue

        risk_summary = {}
        margin = 0.0
        if require_risk_margin:
            risk_summary = _candidate_risk_margin_summary(
                cand,
                params,
                threshold_by_class=threshold_by_class,
                step_um=RISK_DIAG_STEP_UM,
            )
            if not risk_summary.get("available"):
                continue
            margin = float(risk_summary.get("worstMarginMm", -1e9))
        else:
            # Optional diagnostic only. Brain-bound repair must still work when
            # continuous risk is warn/off or temporarily unavailable.
            try:
                risk_summary = _candidate_risk_margin_summary(
                    cand,
                    params,
                    threshold_by_class=threshold_by_class,
                    step_um=RISK_DIAG_STEP_UM,
                )
                if risk_summary.get("available") and risk_summary.get("worstMarginMm") is not None:
                    margin = float(risk_summary.get("worstMarginMm", 0.0))
            except Exception:
                risk_summary = {}
                margin = 0.0
        covered = set(cand.get("coveredRegions", []) or []) & target_set
        overlap = len(set(probe.get("coveredRegions", []) or []) & covered)
        evaluated += 1
        if margin > best_near_margin:
            best_near_margin = margin
        if margin < 0.0:
            continue
        cmp_tuple = (
            overlap,
            len(covered),
            margin,
            -float(movement_cost),
            float(cand.get("explainScore", 0.0)),
        )
        if best_feasible_tuple is None or cmp_tuple > best_feasible_tuple:
            best_feasible_tuple = cmp_tuple
            best_feasible = (cand, movement_cost, margin)
            if movement_cost <= max(entry_step * 1000.0, 100.0):
                break

    if best_feasible is None:
        return {
            "success": False,
            "method": "local_search",
            "details": {
                "evaluatedVariants": int(evaluated),
                "bestNearMarginMm": round(float(best_near_margin), 6)
                if best_near_margin > -1e8
                else None,
            },
        }

    cand, movement_cost, margin = best_feasible
    delta = _candidate_min_adjustment_delta(probe, cand) or {}
    return {
        "success": True,
        "candidate": cand,
        "method": "local_search",
        "details": {
            "evaluatedVariants": int(evaluated),
            "movementCost": round(float(movement_cost), 6),
            "riskMarginMm": round(float(margin), 6),
            "deltaEntryUm": round(float(delta.get("deltaEntryUm", 0.0) or 0.0), 3),
            "deltaApUm": round(float(delta.get("deltaApUm", 0.0) or 0.0), 3),
            "deltaMlUm": round(float(delta.get("deltaMlUm", 0.0) or 0.0), 3),
            "deltaAngleDeg": round(float(delta.get("deltaAngleDeg", 0.0) or 0.0), 6),
            "deltaDepthUm": round(float(delta.get("deltaDepthUm", 0.0) or 0.0), 3),
        },
    }


def _auto_repair_selection(
    selected,
    *,
    candidate_pool,
    target_keys,
    params,
    probe_params,
    constraint_modes,
    correction_factors,
    min_clearance,
    min_entry,
):
    summary = {
        "enabled": bool(params.get("autoRepairEnabled", AUTO_REPAIR_ENABLED_DEFAULT)),
        "attempted": 0,
        "repaired": 0,
        "applied": False,
        "items": [],
    }
    if not summary["enabled"] or not selected:
        return list(selected or []), summary

    threshold_by_class = sanitize_risk_thresholds(
        params.get("riskThresholdsMm"),
        default_mm=float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
    )
    risky = []
    for idx, probe in enumerate(selected):
        ok, reason, risk_summary = _candidate_passes_single_enforced_constraints(
            probe,
            params,
            probe_params,
            constraint_modes,
            threshold_by_class=threshold_by_class,
            risk_step_um=RISK_DIAG_STEP_UM,
        )
        if ok:
            continue
        if reason == "continuousRiskDistance" and isinstance(risk_summary, dict):
            margin = risk_summary.get("worstMarginMm")
            sort_margin = float(margin) if margin is not None else -1e6
        elif reason == "brainBounds":
            sort_margin = -1e7
            if not isinstance(risk_summary, dict):
                risk_summary = {"constraint": "brainBounds"}
        else:
            sort_margin = -1e6
            if not isinstance(risk_summary, dict):
                risk_summary = {"constraint": str(reason or "enforced_constraint")}
        risky.append((idx, float(sort_margin), risk_summary))
    if not risky:
        summary["skipReason"] = "no_enforced_constraint_violation"
        return list(selected), summary

    target_ids = {k: PLANNER_REGIONS[k]["id"] for k in target_keys if k in PLANNER_REGIONS}
    repaired = list(selected)
    risky.sort(key=lambda x: x[1])
    top_k = int(max(1, int(params.get("autoRepairTopK", AUTO_REPAIR_TOPK_DEFAULT))))
    for idx, margin, _risk in risky[:top_k]:
        summary["attempted"] += 1
        probe = repaired[idx]
        others = [p for j, p in enumerate(repaired) if j != idx]
        repair = _auto_repair_single_probe(
            probe,
            selected_others=others,
            candidate_pool=candidate_pool,
            target_ids=target_ids,
            target_keys=target_keys,
            params=params,
            probe_params=probe_params,
            constraint_modes=constraint_modes,
            correction_factors=correction_factors,
            min_clearance=min_clearance,
            min_entry=min_entry,
        )
        item = {
            "probeIdx": int(idx),
            "beforeMarginMm": round(float(margin), 6),
            "success": bool(repair.get("success", False)),
            "method": repair.get("method"),
            "details": repair.get("details", {}),
        }
        item["closestFeasibleDelta"] = _closest_feasible_delta_from_adjustment(
            {
                "deltaEntryUm": (repair.get("details") or {}).get("deltaEntryUm"),
                "deltaApUm": (repair.get("details") or {}).get("deltaApUm"),
                "deltaMlUm": (repair.get("details") or {}).get("deltaMlUm"),
                "deltaAngleDeg": (repair.get("details") or {}).get("deltaAngleDeg"),
                "deltaDepthUm": (repair.get("details") or {}).get("deltaDepthUm"),
            },
            target_subset=probe.get("coveredRegions", []),
        )
        if repair.get("success") and isinstance(repair.get("candidate"), dict):
            repaired[idx] = repair["candidate"]
            summary["repaired"] += 1
        summary["items"].append(item)

    summary["applied"] = bool(summary["repaired"] > 0)
    return repaired, summary


def _strict_fallback_decomposition(
    *,
    selected,
    candidates,
    target_keys,
    params,
    probe_params,
    constraint_modes,
    min_clearance,
    min_entry,
):
    summary = {
        "enabled": bool(params.get("strictFallbackDecompose", STRICT_FALLBACK_DECOMPOSE_DEFAULT)),
        "applied": False,
        "selectedCount": int(len(selected or [])),
        "feasibleSubsolution": [],
        "infeasibleTargets": [],
        "nearestFeasibleAdjustments": [],
    }
    if not summary["enabled"]:
        summary["skipReason"] = "disabled"
        return list(selected or []), sorted(set(target_keys or []) - _covered_targets_from_selected(selected, target_keys)), summary

    force_empty_recovery = not bool(selected)
    blockers = []
    if force_empty_recovery:
        summary["recoveryReason"] = "empty_selection_feasible_first_recovery"
        summary["blockerCount"] = 0
    else:
        pre_issues = _collect_safety_issues(
            selected,
            params,
            probe_params,
            constraint_modes,
            candidates=candidates,
        )
        blockers = [i for i in pre_issues if i.get("mode") == "enforce"]
        summary["blockerCount"] = int(len(blockers))
        if not blockers:
            summary["skipReason"] = "no_enforced_blocker"
            covered = _covered_targets_from_selected(selected, target_keys)
            return list(selected), sorted(set(target_keys or []) - covered), summary

    target_set = set(target_keys or [])
    threshold_by_class = sanitize_risk_thresholds(
        params.get("riskThresholdsMm"),
        default_mm=float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
    )
    safe_pool = []
    rejected_pool = []
    for cand in candidates or []:
        ok, reason, risk_summary = _candidate_passes_single_enforced_constraints(
            cand,
            params,
            probe_params,
            constraint_modes,
            threshold_by_class=threshold_by_class,
            risk_step_um=RISK_DIAG_STEP_UM,
        )
        cand_cov = set(cand.get("coveredRegions", []) or []) & target_set
        if not cand_cov:
            continue
        if ok:
            safe_pool.append(cand)
        else:
            rejected_pool.append((cand, reason, risk_summary))

    safe_pool = sorted(
        safe_pool,
        key=lambda c: (
            -len(set(c.get("coveredRegions", []) or []) & target_set),
            _risk_prefilter_sort_key(c),
            -float(c.get("explainScore", 0.0)),
            int(c.get("paretoRank", 10**6)),
            _candidate_signature(c),
        ),
    )

    fallback_selected = []
    covered = set()
    for cand in safe_pool:
        new_regions = [k for k in (cand.get("coveredRegions", []) or []) if k in target_set and k not in covered]
        if not new_regions:
            continue
        if any(trajs_collide(cand, s, min_clearance, min_entry) for s in fallback_selected):
            continue
        fallback_selected.append(cand)
        covered.update(new_regions)
        if covered == target_set:
            break

    uncovered = sorted(target_set - covered)
    summary["safePoolCount"] = int(len(safe_pool))
    summary["feasibleSubsolution"] = [
        {
            "id": cand.get("id"),
            "coveredRegions": [k for k in (cand.get("coveredRegions", []) or []) if k in target_set],
            "targetSubset": [k for k in (cand.get("coveredRegions", []) or []) if k in target_set],
            "riskMarginMm": (
                (cand.get("riskPrefilter") or {}).get("worstMarginMm")
                if isinstance(cand.get("riskPrefilter"), dict)
                else None
            ),
        }
        for cand in fallback_selected
    ]
    summary["infeasibleTargets"] = list(uncovered)

    for target in uncovered:
        best = None
        best_tuple = None
        for cand, reason, risk_summary in rejected_pool:
            covered_regions = set(cand.get("coveredRegions", []) or [])
            if target not in covered_regions:
                continue
            margin = (
                float(risk_summary.get("worstMarginMm", -1e9))
                if isinstance(risk_summary, dict) and risk_summary.get("worstMarginMm") is not None
                else -1e9
            )
            cmp_tuple = (
                margin,
                -int(cand.get("paretoRank", 10**6)),
                float(cand.get("explainScore", 0.0)),
            )
            if best_tuple is None or cmp_tuple > best_tuple:
                best_tuple = cmp_tuple
                best = (cand, reason, risk_summary)
        if best is None:
            continue
        cand, reason, risk_summary = best
        worst_margin = (
            float(risk_summary.get("worstMarginMm", 0.0))
            if isinstance(risk_summary, dict) and risk_summary.get("worstMarginMm") is not None
            else None
        )
        summary["nearestFeasibleAdjustments"].append(
            {
                "target": target,
                "targetSubset": [target],
                "candidateId": cand.get("id"),
                "blockingReason": reason,
                "requiredDeltaMm": round(abs(min(0.0, worst_margin)), 6) if worst_margin is not None else None,
                "currentMarginMm": round(worst_margin, 6) if worst_margin is not None else None,
                "riskClass": (
                    risk_summary.get("worstClass")
                    if isinstance(risk_summary, dict)
                    else None
                ),
                "closestFeasibleDelta": {
                    "entryShiftMm": None,
                    "angleDeltaDeg": None,
                    "depthRetreatMm": None,
                    "targetSubset": [target],
                    "riskMarginGapMm": round(abs(min(0.0, worst_margin)), 6) if worst_margin is not None else None,
                    "source": "best_rejected_candidate_margin",
                },
            }
        )

    if fallback_selected:
        summary["applied"] = True
        summary["selectedCount"] = int(len(fallback_selected))
        summary["feasibleSubsets"] = deepcopy(summary["feasibleSubsolution"])
        summary["infeasibleTargetsDetailed"] = deepcopy(summary["nearestFeasibleAdjustments"])
        return fallback_selected, uncovered, summary

    summary["skipReason"] = "no_feasible_subsolution"
    summary["feasibleSubsets"] = deepcopy(summary["feasibleSubsolution"])
    summary["infeasibleTargetsDetailed"] = deepcopy(summary["nearestFeasibleAdjustments"])
    return list(selected), sorted(target_set - _covered_targets_from_selected(selected, target_keys)), summary

def _candidate_reference_stability(candidate, params):
    references = params.get("stabilityReferenceTrajectories", []) or []
    if not bool(params.get("stabilityTieBreakerEnabled", False)) or not references:
        return {"available": False, "score": 0.0, "cost": 0.0}

    entry = candidate.get("entry_xyz")
    tip = candidate.get("tip_xyz")
    if not isinstance(entry, (list, tuple, np.ndarray)) or len(entry) != 3:
        return {"available": False, "score": 0.0, "cost": 0.0}
    if not isinstance(tip, (list, tuple, np.ndarray)) or len(tip) != 3:
        return {"available": False, "score": 0.0, "cost": 0.0}
    try:
        entry_arr = np.asarray(entry, dtype=np.float64)
        tip_arr = np.asarray(tip, dtype=np.float64)
    except Exception:
        return {"available": False, "score": 0.0, "cost": 0.0}
    direction = tip_arr - entry_arr
    direction_norm = float(np.linalg.norm(direction))
    if direction_norm <= 1e-9:
        return {"available": False, "score": 0.0, "cost": 0.0}
    direction /= direction_norm

    entry_weight = float(params.get("stabilityEntryWeight", 1.0))
    tip_weight = float(params.get("stabilityTipWeight", 1.0))
    angle_weight = float(params.get("stabilityAngleWeight", 1.0))
    best = None
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            continue
        ref_entry = reference.get("entryCcfUm")
        ref_tip = reference.get("tipCcfUm")
        if not isinstance(ref_entry, (list, tuple)) or len(ref_entry) != 3:
            continue
        if not isinstance(ref_tip, (list, tuple)) or len(ref_tip) != 3:
            continue
        try:
            ref_entry_arr = np.asarray(ref_entry, dtype=np.float64)
            ref_tip_arr = np.asarray(ref_tip, dtype=np.float64)
        except Exception:
            continue
        ref_direction = ref_tip_arr - ref_entry_arr
        ref_norm = float(np.linalg.norm(ref_direction))
        if ref_norm <= 1e-9:
            continue
        ref_direction /= ref_norm
        entry_mm = float(np.linalg.norm(entry_arr - ref_entry_arr) / 1000.0)
        tip_mm = float(np.linalg.norm(tip_arr - ref_tip_arr) / 1000.0)
        cosine = float(np.clip(np.dot(direction, ref_direction), -1.0, 1.0))
        angular_deg = float(math.degrees(math.acos(cosine)))
        cost = (
            entry_weight * entry_mm
            + tip_weight * tip_mm
            + angle_weight * (angular_deg / 10.0)
        )
        row = {
            "available": True,
            "referenceIndex": int(index),
            "entryDisplacementMm": round(entry_mm, 6),
            "tipDisplacementMm": round(tip_mm, 6),
            "angularChangeDeg": round(angular_deg, 6),
            "cost": float(cost),
            "score": float(1.0 / (1.0 + max(0.0, cost))),
        }
        if best is None or float(row["cost"]) < float(best["cost"]):
            best = row
    return best or {"available": False, "score": 0.0, "cost": 0.0}


def _compute_candidate_objectives(candidate, target_keys, params):
    targets = set(target_keys or [])
    covered = set(candidate.get("coveredRegions", []) or [])
    covered_targets = targets & covered
    n_targets = max(1, len(targets))

    overlap = candidate.get("overlapVoxels", {}) or {}
    overlap_sum = float(sum(overlap.get(k, 0) for k in covered_targets))
    coverage_frac = len(covered_targets) / n_targets
    voxel_quality = _clip01(math.log1p(max(overlap_sum, 0.0)) / 6.0)
    coverage_score = _clip01(0.75 * coverage_frac + 0.25 * voxel_quality)

    safety_margin_mm = _estimate_bounds_margin_mm(candidate)
    safety_score = _clip01((safety_margin_mm + 1.0) / 4.0)

    max_angle = float(params.get("maxAngle", 15))
    theta = float(candidate.get("theta_deg", 0.0))
    angle_score = _clip01((max_angle - theta) / max(max_angle, 1.0))

    w_cov = float(params.get("objectiveCoverageWeight", 0.5))
    w_safe = float(params.get("objectiveSafetyWeight", 0.3))
    w_angle = float(params.get("objectiveAngleWeight", 0.2))
    if bool(params.get("objectiveRiskPrimary", OBJECTIVE_RISK_PRIMARY_DEFAULT)):
        boost = float(params.get("objectiveRiskPrimaryBoost", OBJECTIVE_RISK_PRIMARY_BOOST_DEFAULT))
        w_safe = w_safe + boost
        w_cov = max(0.05, w_cov - 0.5 * boost)
        w_angle = max(0.05, w_angle - 0.5 * boost)
        norm = max(1e-9, w_cov + w_safe + w_angle)
        w_cov, w_safe, w_angle = w_cov / norm, w_safe / norm, w_angle / norm

    robust_enabled = bool(params.get("robustOptimization", False))
    sigma_entry_mm = float(params.get("entrySigmaMm", params.get("perturbSigmaMm", 0.15)))
    sigma_angle_deg = float(params.get("angleSigmaDeg", max(0.5, sigma_entry_mm * 6.0)))
    sigma_depth_mm = float(params.get("depthSigmaMm", max(0.05, sigma_entry_mm * 0.7)))
    robust_samples = int(params.get("robustSamples", 16))
    angle_prob = _clip01(_normal_cdf((max_angle - theta) / max(sigma_angle_deg, 1e-6)))
    bounds_margin = safety_margin_mm
    bounds_prob = _clip01(1.0 / (1.0 + math.exp(-bounds_margin / max(0.04, sigma_entry_mm * 1.5))))
    hemi_prob = _estimate_hemisphere_confidence(candidate, sigma_entry_mm)
    robust_score = _clip01(bounds_prob * angle_prob * hemi_prob)

    optimistic_no_robust = (w_cov * coverage_score) + (w_safe * 1.0) + (w_angle * angle_score)
    early_stop_threshold = params.get("_scoreEarlyStopThreshold")
    score_early_stop_enabled = bool(params.get("scoreEarlyStopEnabled", SCORE_EARLY_STOP_ENABLED_DEFAULT))
    score_early_stop_margin = float(params.get("scoreEarlyStopMargin", SCORE_EARLY_STOP_MARGIN_DEFAULT))
    robust_early_stopped = False

    if robust_enabled and robust_samples > 0:
        ap_base = float(candidate.get("ap_mm_corrected", candidate.get("ap_mm", 0.0)))
        ml_base = float(candidate.get("ml_mm_corrected", candidate.get("ml_mm", 0.0)))
        entry_dv_base = _probe_entry_dv_mm(candidate)
        phi_base = float(candidate.get("phi_deg", 0.0))
        depth_mm = float(candidate.get("depthUm_corrected", candidate.get("depthUm", 0.0))) / 1000.0
        robust_cache_key = (
            _candidate_signature(candidate),
            int(robust_samples),
            round(float(sigma_entry_mm), 6),
            round(float(sigma_angle_deg), 6),
            round(float(sigma_depth_mm), 6),
            round(float(max_angle), 6),
            round(entry_dv_base, 6),
        )
        cached_robust = _bounded_cache_get(
            robust_perturb_cache,
            robust_perturb_cache_lock,
            robust_cache_key,
        )
        if cached_robust is not None:
            sampled_score = float(cached_robust)
            robust_score = _clip01(0.55 * robust_score + 0.45 * sampled_score)
        else:
            region_signs = []
            for key in (candidate.get("coveredRegions", []) or []):
                reg = PLANNER_REGIONS.get(key)
                if not reg:
                    continue
                r_ml = float(reg.get("ml", 0.0))
                if abs(r_ml) >= 0.2:
                    region_signs.append(1.0 if r_ml > 0 else -1.0)
            expected_sign = 1.0 if sum(region_signs) >= 0 else -1.0
            has_expected_hemi = bool(region_signs)
            rng = np.random.default_rng(
                _stable_seed_from_values(
                    candidate.get("id", ""),
                    round(ap_base, 4),
                    round(ml_base, 4),
                    round(theta, 3),
                    round(phi_base, 3),
                    robust_samples,
                    sigma_entry_mm,
                    sigma_angle_deg,
                    sigma_depth_mm,
                )
            )

            pass_count = 0
            for iter_idx in range(robust_samples):
                d_ap, d_ml, d_dv = rng.normal(0.0, sigma_entry_mm, size=3)
                d_theta = float(rng.normal(0.0, sigma_angle_deg))
                d_phi = float(rng.normal(0.0, sigma_angle_deg * 1.5))
                d_depth = float(rng.normal(0.0, sigma_depth_mm))

                ap_p = ap_base + float(d_ap)
                ml_p = ml_base + float(d_ml)
                entry_dv_p = entry_dv_base + float(d_dv)
                theta_p = max(0.0, theta + d_theta)
                phi_p = (phi_base + d_phi) % 360.0
                depth_p = max(0.2, depth_mm + d_depth)

                th_r = math.radians(theta_p)
                ph_r = math.radians(phi_p)
                dir_x = math.sin(th_r) * math.cos(ph_r)
                dir_y = math.cos(th_r)
                tip_ap_p, tip_dv_p = _tip_ap_dv_mm(ap_p, entry_dv_p, [dir_x, dir_y], depth_p * 1000.)

                bounds_ok = (
                    ENTRY_AP_MIN_MM <= ap_p <= ENTRY_AP_MAX_MM
                    and tip_ap_p >= TIP_POST_LIMIT_MM
                    and tip_dv_p <= MAX_DV_MM
                )
                angle_ok = theta_p <= max_angle + 1e-9
                hemi_ok = (not has_expected_hemi) or ((ml_p > 0) == (expected_sign > 0))
                if bounds_ok and angle_ok and hemi_ok:
                    pass_count += 1

                if (
                    score_early_stop_enabled
                    and early_stop_threshold is not None
                    and robust_samples >= 8
                ):
                    processed = iter_idx + 1
                    remain = max(0, robust_samples - processed)
                    robust_upper = (pass_count + remain) / float(max(1, robust_samples))
                    optimistic_explain = optimistic_no_robust * (0.75 + 0.25 * robust_upper)
                    if optimistic_explain < float(early_stop_threshold) - float(score_early_stop_margin):
                        robust_early_stopped = True
                        break

            sampled_score = pass_count / float(max(1, robust_samples))
            robust_score = _clip01(0.55 * robust_score + 0.45 * sampled_score)
            if not robust_early_stopped:
                _bounded_cache_put(
                    robust_perturb_cache,
                    robust_perturb_cache_lock,
                    robust_cache_key,
                    sampled_score,
                    ROBUST_PERTURB_CACHE_MAX,
                )

    risk_diag = candidate.get("riskDiagnostics") if isinstance(candidate.get("riskDiagnostics"), dict) else {}
    class_threshold_map = (
        risk_diag.get("classThresholdMm")
        if isinstance(risk_diag.get("classThresholdMm"), dict)
        else {}
    )
    class_min_map = (
        risk_diag.get("classMinDistanceMm")
        if isinstance(risk_diag.get("classMinDistanceMm"), dict)
        else {}
    )
    risk_sigma_mm = max(0.005, float(params.get("riskSoftplusSigmaMm", OBJECTIVE_RISK_SIGMA_MM_DEFAULT)))

    class_shortfalls = []
    class_margins = []
    if class_min_map:
        dist_vals = []
        thr_vals = []
        for cls_name, dist_val in class_min_map.items():
            if str(cls_name or "").strip().lower() in ENTRY_CORRIDOR_RISK_CLASSES:
                continue
            try:
                dist_mm = float(dist_val)
            except Exception:
                continue
            dist_vals.append(dist_mm)
            thr_vals.append(float(resolve_risk_threshold_mm(class_threshold_map, cls_name)))
        if dist_vals:
            dist_arr = np.asarray(dist_vals, dtype=np.float64)
            thr_arr = np.asarray(thr_vals, dtype=np.float64)
            margin_arr = dist_arr - thr_arr
            class_margins = margin_arr.tolist()
            class_shortfalls = np.log1p(np.exp((thr_arr - dist_arr) / risk_sigma_mm)).tolist()

    nearest_margin_mm = None
    if risk_diag:
        min_signed = risk_diag.get("minSignedDistanceMm")
        if min_signed is None:
            min_signed = risk_diag.get("minDistanceMm")
        nearest_cls = risk_diag.get("nearestSourceClass")
        if min_signed is not None:
            try:
                min_signed = float(min_signed)
                nearest_thr = float(resolve_risk_threshold_mm(class_threshold_map, nearest_cls))
                nearest_margin_mm = min_signed - nearest_thr
                if not class_margins:
                    class_margins.append(nearest_margin_mm)
                    class_shortfalls.append(_softplus((nearest_thr - min_signed) / risk_sigma_mm))
            except Exception:
                nearest_margin_mm = None

    risk_integral_shortfall = float(risk_diag.get("riskIntegralShortfallMm", 0.0)) if risk_diag else 0.0
    risk_tail_shortfall = float(risk_diag.get("riskTailShortfallMm", 0.0)) if risk_diag else 0.0
    avg_shortfall = float(np.mean(class_shortfalls)) if class_shortfalls else 0.0
    margin_score = (
        _clip01(1.0 / (1.0 + max(0.0, -float(min(class_margins)))) / 0.15)
        if class_margins
        else 0.5
    )
    risk_path_weight = float(params.get("sdfRiskPathWeight", SDF_RISK_PATH_WEIGHT_DEFAULT))
    risk_margin_weight = float(params.get("sdfRiskMarginWeight", SDF_RISK_MARGIN_WEIGHT_DEFAULT))
    risk_penalty = (
        avg_shortfall * max(0.0, risk_margin_weight)
        + max(0.0, risk_integral_shortfall) * max(0.0, risk_path_weight)
        + max(0.0, risk_tail_shortfall) * max(
            0.0, float(params.get("sdfRiskCvarWeight", SDF_RISK_CVAR_WEIGHT_DEFAULT))
        )
    )
    continuous_risk_score = _clip01(1.0 / (1.0 + risk_penalty))
    safety_score = _clip01(0.20 * safety_score + 0.55 * continuous_risk_score + 0.25 * margin_score)

    weighted = {
        "coverage": w_cov * coverage_score,
        "safety": w_safe * safety_score,
        "angle": w_angle * angle_score,
    }
    explain_score = weighted["coverage"] + weighted["safety"] + weighted["angle"]
    if robust_enabled:
        explain_score *= (0.75 + 0.25 * robust_score)

    drcc_metrics = None
    if _drcc_enabled(params):
        drcc_metrics = _candidate_drcc_metrics(candidate, params)
        worst_prob = float(drcc_metrics.get("worstCaseViolationProb", 1.0))
        epsilon = max(1e-6, float(params.get("drccRiskEpsilon", DRCC_RISK_EPSILON_DEFAULT)))
        drcc_safety_score = _clip01(1.0 - worst_prob / epsilon)
        integral_shortfall = float(drcc_metrics.get("riskIntegralShortfallMm", 0.0))
        tail_shortfall = float(drcc_metrics.get("riskTailShortfallMm", 0.0))
        risk_integral_weight = float(params.get("sdfRiskIntegralWeight", SDF_RISK_INTEGRAL_WEIGHT_DEFAULT))
        risk_cvar_weight = float(params.get("sdfRiskCvarWeight", SDF_RISK_CVAR_WEIGHT_DEFAULT))
        risk_penalty = (
            max(0.0, integral_shortfall) * max(0.0, risk_integral_weight)
            + max(0.0, tail_shortfall) * max(0.0, risk_cvar_weight)
        )
        sdf_path_score = _clip01(1.0 / (1.0 + risk_penalty))
        safety_score = _clip01(0.55 * safety_score + 0.30 * drcc_safety_score + 0.15 * sdf_path_score)
        weighted["safety"] = w_safe * safety_score
        explain_score = weighted["coverage"] + weighted["safety"] + weighted["angle"]
        if robust_enabled:
            explain_score *= (0.75 + 0.25 * robust_score)
        explain_score *= 0.55 + 0.45 * sdf_path_score
        if not bool(drcc_metrics.get("feasible", False)):
            explain_score *= 0.45

    stability = _candidate_reference_stability(candidate, params)
    stability_penalty = 0.0
    if stability.get("available") and bool(params.get("stabilityTieBreakerEnabled", False)):
        stability_penalty = float(params.get("stabilityReferenceWeight", 0.05)) * float(
            stability.get("cost", 0.0)
        )
        explain_score -= stability_penalty
    weighted["stability"] = -float(stability_penalty)

    return {
        "scores": {
            "coverage": round(coverage_score, 6),
            "safety": round(safety_score, 6),
            "angle": round(angle_score, 6),
            "robust": round(robust_score, 6),
            "continuousRisk": round(continuous_risk_score, 6),
            "riskMarginScore": round(margin_score, 6),
            "riskPenalty": round(float(risk_penalty), 6),
            "nearestMarginMm": round(float(nearest_margin_mm), 6) if nearest_margin_mm is not None else None,
            "drccSafety": round(
                float(1.0 - float(drcc_metrics.get("worstCaseViolationProb", 1.0)))
                if isinstance(drcc_metrics, dict)
                else 0.0,
                6,
            ),
            "sdfPath": round(
                float(
                    1.0
                    / (
                        1.0
                        + float(drcc_metrics.get("riskIntegralShortfallMm", 0.0))
                        + float(drcc_metrics.get("riskTailShortfallMm", 0.0))
                    )
                )
                if isinstance(drcc_metrics, dict)
                else 0.0,
                6,
            ),
            "robustEarlyStopped": bool(robust_early_stopped),
            "referenceStability": round(float(stability.get("score", 0.0)), 6),
            "referenceStabilityCost": round(float(stability.get("cost", 0.0)), 6)
            if stability.get("available")
            else None,
            "referenceEntryDisplacementMm": stability.get("entryDisplacementMm"),
            "referenceTipDisplacementMm": stability.get("tipDisplacementMm"),
            "referenceAngularChangeDeg": stability.get("angularChangeDeg"),
            "referenceTrajectoryIndex": stability.get("referenceIndex"),
        },
        "weighted": {
            "coverage": round(weighted["coverage"], 6),
            "safety": round(weighted["safety"], 6),
            "angle": round(weighted["angle"], 6),
            "stability": round(weighted["stability"], 6),
        },
        "explainScore": round(explain_score, 6),
        "safetyMarginMm": round(safety_margin_mm, 6),
        "coveredGain": len(covered_targets),
        "drccMetrics": drcc_metrics,
    }


def _build_explain_summary(candidate):
    scores = candidate.get("objectiveScores", {}) or {}
    weighted = candidate.get("objectiveWeighted", {}) or {}
    robust = candidate.get("robustScore", scores.get("robust"))
    drcc = candidate.get("drccMetrics") if isinstance(candidate.get("drccMetrics"), dict) else {}
    drcc_txt = ""
    if drcc:
        drcc_txt = (
            f", DRCC(worstP={float(drcc.get('worstCaseViolationProb', 1.0)):.3f}, "
            f"eps={float(drcc.get('riskBudgetEpsilon', DRCC_RISK_EPSILON_DEFAULT)):.3f}, "
            f"ok={'Y' if drcc.get('feasible') else 'N'})"
        )
    return (
        f"Coverage={scores.get('coverage', 0):.3f} "
        f"(w {weighted.get('coverage', 0):.3f}), "
        f"Safety={scores.get('safety', 0):.3f} "
        f"(w {weighted.get('safety', 0):.3f}, cont={scores.get('continuousRisk', 0):.3f}, "
        f"pen={scores.get('riskPenalty', 0):.3f}), "
        f"Angle={scores.get('angle', 0):.3f} "
        f"(w {weighted.get('angle', 0):.3f}), "
        f"Robust={float(robust or 0):.3f}, "
        f"ReferenceStability={float(scores.get('referenceStability', 0.0) or 0.0):.3f} "
        f"(w {float(weighted.get('stability', 0.0) or 0.0):.3f})"
        f"{drcc_txt}"
    )


def _objective_context_signature(target_keys, params):
    stability_refs = []
    for ref in params.get("stabilityReferenceTrajectories", []) or []:
        if not isinstance(ref, dict):
            continue
        stability_refs.append(
            (
                tuple(round(float(v), 3) for v in (ref.get("entryCcfUm") or [])),
                tuple(round(float(v), 3) for v in (ref.get("tipCcfUm") or [])),
            )
        )
    return (
        tuple(sorted(set(target_keys or []))),
        str(params.get("algorithm", "greedy")),
        round(float(params.get("maxAngle", 15.0)), 4),
        bool(params.get("robustOptimization", False)),
        int(params.get("robustSamples", 16)),
        round(float(params.get("entrySigmaMm", params.get("perturbSigmaMm", 0.15))), 6),
        round(float(params.get("angleSigmaDeg", 2.0)), 6),
        round(float(params.get("depthSigmaMm", 0.2)), 6),
        bool(params.get("drccEnabled", False)),
        round(float(params.get("drccRiskEpsilon", DRCC_RISK_EPSILON_DEFAULT)), 6),
        round(float(params.get("drccWassersteinRadius", DRCC_WASSERSTEIN_RADIUS_DEFAULT)), 6),
        round(float(params.get("sdfRiskIntegralWeight", SDF_RISK_INTEGRAL_WEIGHT_DEFAULT)), 6),
        round(float(params.get("sdfRiskCvarWeight", SDF_RISK_CVAR_WEIGHT_DEFAULT)), 6),
        round(float(params.get("sdfRiskPathWeight", SDF_RISK_PATH_WEIGHT_DEFAULT)), 6),
        round(float(params.get("sdfRiskMarginWeight", SDF_RISK_MARGIN_WEIGHT_DEFAULT)), 6),
        bool(params.get("objectiveRiskPrimary", OBJECTIVE_RISK_PRIMARY_DEFAULT)),
        round(float(params.get("objectiveRiskPrimaryBoost", OBJECTIVE_RISK_PRIMARY_BOOST_DEFAULT)), 6),
        round(float(params.get("riskSoftplusSigmaMm", OBJECTIVE_RISK_SIGMA_MM_DEFAULT)), 6),
        round(float(params.get("objectiveCoverageWeight", 0.5)), 6),
        round(float(params.get("objectiveSafetyWeight", 0.3)), 6),
        round(float(params.get("objectiveAngleWeight", 0.2)), 6),
        bool(params.get("stabilityTieBreakerEnabled", False)),
        round(float(params.get("stabilityReferenceWeight", 0.05)), 6),
        round(float(params.get("stabilityEntryWeight", 1.0)), 6),
        round(float(params.get("stabilityTipWeight", 1.0)), 6),
        round(float(params.get("stabilityAngleWeight", 1.0)), 6),
        tuple(stability_refs),
    )


def _candidate_objective_cache_key(candidate, target_keys, params):
    return (_candidate_signature(candidate), round(_probe_entry_dv_mm(candidate), 6),
            _objective_context_signature(target_keys, params))


def _score_candidates(candidates, target_keys, params, progress_cb=None, cancel_cb=None):
    if not candidates:
        return {
            "evaluated": 0,
            "cacheHits": 0,
            "workersUsed": 1,
            "parallelMode": "none",
            "chunkSize": int(max(4, params.get("computeChunkSize", COMPUTE_CHUNK_SIZE_DEFAULT))),
            "earlyStopHits": 0,
            "earlyStopEnabled": bool(params.get("scoreEarlyStopEnabled", SCORE_EARLY_STOP_ENABLED_DEFAULT)),
        }
    cache_hits = 0
    evaluated = 0
    early_stop_hits = 0
    total = len(candidates)
    # Update about every ~2% to keep UI progress responsive without
    # overwhelming task-state writes.
    tick = max(1, total // 50)

    def _apply_obj_to_candidate(candidate, obj):
        candidate["objectiveScores"] = obj["scores"]
        candidate["objectiveWeighted"] = obj["weighted"]
        candidate["explainScore"] = obj["explainScore"]
        candidate["robustScore"] = obj["scores"]["robust"]
        candidate["safetyMarginMm"] = obj["safetyMarginMm"]
        candidate["coveredGain"] = obj["coveredGain"]
        candidate["drccMetrics"] = deepcopy(obj.get("drccMetrics"))
        if isinstance(obj.get("drccMetrics"), dict):
            candidate["drccFeasible"] = bool(obj["drccMetrics"].get("feasible", False))
            candidate["drccWorstCaseViolationProb"] = float(
                obj["drccMetrics"].get("worstCaseViolationProb", 1.0)
            )
        candidate["explain"] = {"summary": _build_explain_summary(candidate)}

    workers = _resolve_parallel_workers(
        params.get("computeWorkers", COMPUTE_WORKERS_DEFAULT),
        default_workers=COMPUTE_WORKERS_DEFAULT,
    )
    chunk_size = int(max(4, params.get("computeChunkSize", COMPUTE_CHUNK_SIZE_DEFAULT)))
    use_parallel = workers > 1 and total >= max(32, workers * 4)
    early_stop_enabled = bool(params.get("scoreEarlyStopEnabled", SCORE_EARLY_STOP_ENABLED_DEFAULT))
    early_stop_topk = int(max(4, params.get("scoreEarlyStopTopK", SCORE_EARLY_STOP_TOPK_DEFAULT)))
    top_heap = []

    def _current_threshold():
        if not early_stop_enabled:
            return None
        if len(top_heap) < early_stop_topk:
            return None
        return float(top_heap[0])

    def _update_top_heap(explain_score):
        if not early_stop_enabled:
            return
        value = float(explain_score)
        if len(top_heap) < early_stop_topk:
            import heapq
            heapq.heappush(top_heap, value)
            return
        if value > top_heap[0]:
            import heapq
            heapq.heapreplace(top_heap, value)

    def _score_single(idx, candidate, threshold=None):
        local_params = params
        if threshold is not None:
            local_params = dict(params)
            local_params["_scoreEarlyStopThreshold"] = float(threshold)
        cache_key = _candidate_objective_cache_key(candidate, target_keys, params)
        cached = _bounded_cache_get(candidate_objective_cache, candidate_cache_lock, cache_key)
        if cached is not None:
            return idx, deepcopy(cached), True
        obj = _compute_candidate_objectives(candidate, target_keys, local_params)
        can_cache = not bool((obj.get("scores") or {}).get("robustEarlyStopped", False))
        if can_cache:
            _bounded_cache_put(
                candidate_objective_cache,
                candidate_cache_lock,
                cache_key,
                deepcopy(obj),
                50000,
            )
        return idx, obj, False

    completed = 0
    for chunk_start in range(0, total, chunk_size):
        chunk = list(
            enumerate(
                candidates[chunk_start:chunk_start + chunk_size],
                start=chunk_start + 1,
            )
        )
        threshold = _current_threshold()
        if use_parallel and len(chunk) > 1:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="score-cand") as ex:
                futures = [ex.submit(_score_single, idx, cand, threshold) for idx, cand in chunk]
                for fut in as_completed(futures):
                    idx, obj, from_cache = fut.result()
                    candidate = candidates[idx - 1]
                    _apply_obj_to_candidate(candidate, obj)
                    evaluated += 1
                    if from_cache:
                        cache_hits += 1
                    if bool((obj.get("scores") or {}).get("robustEarlyStopped")):
                        early_stop_hits += 1
                    _update_top_heap(obj.get("explainScore", 0.0))
                    completed += 1
                    if progress_cb is not None and (completed % tick == 0 or completed == total):
                        try:
                            progress_cb(completed, total)
                        except Exception:
                            pass
                    if cancel_cb is not None and (completed % tick == 0):
                        try:
                            cancel_cb()
                        except Exception:
                            pass
        else:
            for idx, cand in chunk:
                _idx, obj, from_cache = _score_single(idx, cand, threshold)
                candidate = candidates[idx - 1]
                _apply_obj_to_candidate(candidate, obj)
                evaluated += 1
                if from_cache:
                    cache_hits += 1
                if bool((obj.get("scores") or {}).get("robustEarlyStopped")):
                    early_stop_hits += 1
                _update_top_heap(obj.get("explainScore", 0.0))
                completed += 1
                if progress_cb is not None and (completed % tick == 0 or completed == total):
                    try:
                        progress_cb(completed, total)
                    except Exception:
                        pass
                if cancel_cb is not None and (completed % tick == 0):
                    try:
                        cancel_cb()
                    except Exception:
                        pass

    if progress_cb is not None:
        try:
            progress_cb(total, total)
        except Exception:
            pass
    return {
        "evaluated": evaluated,
        "cacheHits": cache_hits,
        "workersUsed": int(workers if use_parallel else 1),
        "parallelMode": "thread_pool" if use_parallel else "single_thread",
        "chunkSize": int(chunk_size),
        "earlyStopEnabled": bool(early_stop_enabled),
        "earlyStopTopK": int(early_stop_topk),
        "earlyStopHits": int(early_stop_hits),
        "earlyStopThreshold": round(float(_current_threshold()), 6)
        if _current_threshold() is not None
        else None,
    }


def _merge_score_cache_stats(agg, stats):
    if agg is None:
        agg = {
            "evaluated": 0,
            "cacheHits": 0,
            "workersUsed": 1,
            "parallelMode": "single_thread",
            "chunkSize": int(COMPUTE_CHUNK_SIZE_DEFAULT),
            "earlyStopEnabled": False,
            "earlyStopTopK": 0,
            "earlyStopHits": 0,
            "earlyStopThreshold": None,
        }
    if not isinstance(stats, dict):
        return agg
    agg["evaluated"] += int(stats.get("evaluated", 0) or 0)
    agg["cacheHits"] += int(stats.get("cacheHits", 0) or 0)
    agg["workersUsed"] = max(int(agg.get("workersUsed", 1) or 1), int(stats.get("workersUsed", 1) or 1))
    if str(stats.get("parallelMode", "")).strip().lower() == "thread_pool":
        agg["parallelMode"] = "thread_pool"
    agg["chunkSize"] = int(stats.get("chunkSize", agg.get("chunkSize", COMPUTE_CHUNK_SIZE_DEFAULT)) or agg.get("chunkSize", COMPUTE_CHUNK_SIZE_DEFAULT))
    agg["earlyStopEnabled"] = bool(agg.get("earlyStopEnabled", False) or stats.get("earlyStopEnabled", False))
    agg["earlyStopTopK"] = max(int(agg.get("earlyStopTopK", 0) or 0), int(stats.get("earlyStopTopK", 0) or 0))
    agg["earlyStopHits"] += int(stats.get("earlyStopHits", 0) or 0)
    threshold = stats.get("earlyStopThreshold")
    if threshold is not None:
        agg["earlyStopThreshold"] = float(threshold)
    return agg


def _generate_and_score_streaming(
    target_keys,
    params,
    constraint_modes,
    correction_factors,
    probe_params,
    gen_progress_cb=None,
    score_progress_cb=None,
    cancel_cb=None,
):
    """
    True cross-stage stream:
    - producer thread generates candidate chunks from refine stage
    - consumer thread scores chunks immediately
    - prefilter runs once on fully generated/scored universe to keep behavior stable
    """
    queue_size = int(max(2, params.get("streamingQueueMaxSize", STREAMING_QUEUE_MAXSIZE_DEFAULT)))
    stream_q = queue.Queue(maxsize=queue_size)
    producer_state = {
        "error": None,
        "candidates": None,
        "candidateGenSummary": {},
    }
    generated_done = 0
    generated_total = 0
    scored_count = 0
    score_cache_stats = None
    runtime_budget = int(max(RUNTIME_NUM_THREADS_DEFAULT, params.get("computeWorkers", 1)))
    gen_workers = int(max(1, params.get("candidateGenWorkers", CANDIDATE_GEN_WORKERS_DEFAULT)))
    stream_score_workers = int(
        max(
            1,
            min(
                int(params.get("computeWorkers", COMPUTE_WORKERS_DEFAULT)),
                int(params.get("streamingScoreWorkersMax", STREAMING_SCORE_WORKERS_MAX_DEFAULT)),
                max(1, runtime_budget - gen_workers),
            ),
        )
    )
    stream_score_params = dict(params)
    stream_score_params["computeWorkers"] = int(stream_score_workers)
    score_buffer = []
    score_flush_size = int(
        max(
            128,
            params.get("streamingScoreBatchSize", STREAMING_SCORE_BATCH_SIZE_DEFAULT),
            params.get("computeChunkSize", COMPUTE_CHUNK_SIZE_DEFAULT) * 4,
        )
    )

    def _flush_score_buffer(force=False):
        nonlocal score_cache_stats, scored_count
        if not score_buffer:
            return
        if (not force) and len(score_buffer) < score_flush_size:
            return
        chunk = list(score_buffer)
        score_buffer.clear()
        stats = _score_candidates(
            chunk,
            target_keys,
            stream_score_params,
            progress_cb=None,
            cancel_cb=cancel_cb,
        )
        score_cache_stats = _merge_score_cache_stats(score_cache_stats, stats)
        scored_count += int(len(chunk))
        if score_progress_cb is not None:
            est_total = max(scored_count, generated_total or generated_done or scored_count)
            try:
                score_progress_cb(scored_count, est_total)
            except Exception:
                pass

    def _sink(chunk, done=None, total=None):
        stream_q.put(("chunk", chunk, done, total))

    def _producer():
        try:
            produced = generate_candidates_voxel(
                target_keys,
                params,
                constraint_modes,
                correction_factors=correction_factors,
                probe_params=probe_params,
                progress_cb=gen_progress_cb,
                cancel_cb=cancel_cb,
                candidate_sink=_sink,
            )
            producer_state["candidates"] = produced
            producer_state["candidateGenSummary"] = deepcopy(
                getattr(generate_candidates_voxel, "last_summary", {})
            )
        except Exception as exc:
            producer_state["error"] = exc
        finally:
            stream_q.put(("done", None, generated_done, generated_total))

    producer_thread = threading.Thread(
        target=_producer,
        name="candidate-producer",
        daemon=True,
    )
    producer_thread.start()

    while True:
        try:
            kind, payload, done_hint, total_hint = stream_q.get(timeout=0.2)
        except queue.Empty:
            if cancel_cb is not None:
                try:
                    cancel_cb()
                except Exception:
                    pass
            if (not producer_thread.is_alive()) and producer_state.get("error") is not None and stream_q.empty():
                break
            continue

        if kind == "chunk":
            if done_hint is not None:
                try:
                    generated_done = max(generated_done, int(done_hint))
                except Exception:
                    pass
            if total_hint is not None:
                try:
                    generated_total = max(generated_total, int(total_hint))
                except Exception:
                    pass
            chunk = payload if isinstance(payload, list) else []
            if chunk:
                score_buffer.extend(chunk)
                _flush_score_buffer(force=False)
        elif kind == "done":
            break

    producer_thread.join()
    if producer_state.get("error") is not None:
        raise producer_state["error"]
    _flush_score_buffer(force=True)

    candidate_universe = list(producer_state.get("candidates") or [])
    candidate_gen_summary = deepcopy(producer_state.get("candidateGenSummary") or {})
    if not candidate_universe:
        if score_cache_stats is None:
            score_cache_stats = {
                "evaluated": 0,
                "cacheHits": 0,
                "workersUsed": 1,
                "parallelMode": "none",
                "chunkSize": int(max(4, params.get("computeChunkSize", COMPUTE_CHUNK_SIZE_DEFAULT))),
                "earlyStopHits": 0,
                "earlyStopEnabled": bool(params.get("scoreEarlyStopEnabled", SCORE_EARLY_STOP_ENABLED_DEFAULT)),
                "earlyStopTopK": int(params.get("scoreEarlyStopTopK", SCORE_EARLY_STOP_TOPK_DEFAULT)),
                "earlyStopThreshold": None,
            }
        return [], [], [], {
            "enabled": bool(params.get("autoAvoidEnabled", AUTO_AVOID_ENABLED_DEFAULT)),
            "requestedCandidateCount": 0,
            "keptCandidateCount": 0,
            "nearCandidateCount": 0,
            "workersUsed": int(params.get("prefilterWorkers", COMPUTE_PREFILTER_WORKERS_DEFAULT)),
            "coarseStepUm": float(params.get("autoAvoidCoarseStepUm", AUTO_AVOID_COARSE_STEP_UM_DEFAULT)),
            "safetyBufferMm": float(params.get("autoAvoidSafetyBufferMm", AUTO_AVOID_SAFETY_BUFFER_MM_DEFAULT)),
            "nearMarginMm": float(params.get("autoAvoidNearMarginMm", AUTO_AVOID_NEAR_MARGIN_MM_DEFAULT)),
            "constraintAwareSearch": bool(params.get("constraintAwareSearch", CONSTRAINT_AWARE_SEARCH_DEFAULT)),
        }, score_cache_stats, candidate_gen_summary

    unscored = [c for c in candidate_universe if not isinstance(c.get("objectiveScores"), dict)]
    if unscored:
        stats = _score_candidates(
            unscored,
            target_keys,
            stream_score_params,
            progress_cb=None,
            cancel_cb=cancel_cb,
        )
        score_cache_stats = _merge_score_cache_stats(score_cache_stats, stats)
        scored_count += int(len(unscored))

    if score_progress_cb is not None:
        try:
            score_progress_cb(max(scored_count, len(candidate_universe)), len(candidate_universe))
        except Exception:
            pass

    prefiltered_candidates, near_candidates, auto_avoid_prefilter_summary = _prefilter_candidates_for_auto_avoid(
        candidate_universe,
        params,
        constraint_modes,
    )
    if prefiltered_candidates:
        candidates = prefiltered_candidates
    else:
        candidates = list(candidate_universe)

    if score_cache_stats is None:
        score_cache_stats = {
            "evaluated": 0,
            "cacheHits": 0,
            "workersUsed": 1,
            "parallelMode": "none",
            "chunkSize": int(max(4, params.get("computeChunkSize", COMPUTE_CHUNK_SIZE_DEFAULT))),
            "earlyStopHits": 0,
            "earlyStopEnabled": bool(params.get("scoreEarlyStopEnabled", SCORE_EARLY_STOP_ENABLED_DEFAULT)),
            "earlyStopTopK": int(params.get("scoreEarlyStopTopK", SCORE_EARLY_STOP_TOPK_DEFAULT)),
            "earlyStopThreshold": None,
        }
    return (
        candidates,
        candidate_universe,
        near_candidates,
        auto_avoid_prefilter_summary,
        score_cache_stats,
        candidate_gen_summary,
    )


def _candidate_signature(candidate):
    return (
        round(float(candidate.get("ap_mm_corrected", candidate.get("ap_mm", 0.0))), 5),
        round(float(candidate.get("ml_mm_corrected", candidate.get("ml_mm", 0.0))), 5),
        round(float(candidate.get("theta_deg", 0.0)), 4),
        round(float(candidate.get("phi_deg", 0.0)), 4),
        round(float(candidate.get("depthUm_corrected", candidate.get("depthUm", 0.0))), 2),
    )


def _apply_repair_preference_bias(candidates, params):
    pref = params.get("repairPreferredCandidate") if isinstance(params, dict) else None
    if not isinstance(pref, dict) or not candidates:
        return {"enabled": False, "applied": False}

    pref_id = str(pref.get("candidateId")) if pref.get("candidateId") is not None else None
    pref_sig = None
    if isinstance(pref.get("candidateSignature"), (list, tuple)) and len(pref.get("candidateSignature")) >= 5:
        try:
            pref_sig = tuple(
                [
                    round(float(pref["candidateSignature"][0]), 5),
                    round(float(pref["candidateSignature"][1]), 5),
                    round(float(pref["candidateSignature"][2]), 4),
                    round(float(pref["candidateSignature"][3]), 4),
                    round(float(pref["candidateSignature"][4]), 2),
                ]
            )
        except Exception:
            pref_sig = None
    pref_geom = pref.get("geometry") if isinstance(pref.get("geometry"), dict) else {}

    best = None
    best_cost = None
    match_kind = None
    for cand in candidates:
        cand_id = cand.get("id")
        if pref_id is not None and cand_id is not None and str(cand_id) == pref_id:
            best = cand
            best_cost = 0.0
            match_kind = "candidateId"
            break

        cand_sig = _candidate_signature(cand)
        if pref_sig is not None and cand_sig == pref_sig:
            best = cand
            best_cost = 0.0
            match_kind = "candidateSignature"
            break

        if pref_geom:
            try:
                dap = (float(cand.get("ap_mm_corrected", cand.get("ap_mm", 0.0))) - float(pref_geom.get("ap_mm", 0.0))) * 1000.0
                dml = (float(cand.get("ml_mm_corrected", cand.get("ml_mm", 0.0))) - float(pref_geom.get("ml_mm", 0.0))) * 1000.0
                dang = math.sqrt(
                    (float(cand.get("theta_deg", 0.0)) - float(pref_geom.get("theta_deg", 0.0))) ** 2
                    + (float(cand.get("phi_deg", 0.0)) - float(pref_geom.get("phi_deg", 0.0))) ** 2
                )
                dd = abs(float(cand.get("depthUm_corrected", cand.get("depthUm", 0.0))) - float(pref_geom.get("depthUm", 0.0)))
                cost = math.hypot(dap, dml) + 140.0 * dang + 0.25 * dd
            except Exception:
                continue
            if best_cost is None or cost < best_cost:
                best = cand
                best_cost = cost
                match_kind = "geometry"

    if best is None:
        return {"enabled": True, "applied": False, "reason": "no_matching_candidate"}

    old_score = float(best.get("explainScore", 0.0) or 0.0)
    best["repairPreferredCandidate"] = True
    # A strong but transparent bias: it only changes ordering inside the
    # already generated/scored candidate pool and does not bypass constraints.
    best["explainScore"] = old_score + 1000000.0
    obj = best.get("objectiveScores")
    if isinstance(obj, dict):
        obj["repairPreference"] = 1.0
    else:
        best["objectiveScores"] = {"repairPreference": 1.0}
    explain = best.get("explain")
    if not isinstance(explain, dict):
        explain = {}
    explain["repairPreference"] = {
        "matched": True,
        "matchKind": match_kind,
        "matchCost": round(float(best_cost or 0.0), 6),
    }
    best["explain"] = explain
    return {
        "enabled": True,
        "applied": True,
        "matchKind": match_kind,
        "matchCost": round(float(best_cost or 0.0), 6),
        "candidateId": best.get("id"),
        "candidateSignature": list(_candidate_signature(best)),
        "scoreBefore": round(old_score, 6),
        "scoreAfter": round(float(best.get("explainScore", 0.0)), 6),
    }


def _build_counterfactual_explanations(selected, candidates):
    if not selected:
        return
    selected_signatures = {_candidate_signature(p) for p in selected}
    alternatives = [
        c for c in sorted(
            candidates,
            key=lambda x: (
                -float(x.get("explainScore", 0.0)),
                int(x.get("paretoRank", 10**6)),
                _candidate_signature(x),
            ),
        )
        if _candidate_signature(c) not in selected_signatures
    ]
    if not alternatives:
        return
    best_alt = alternatives[0]
    for probe in selected:
        explain = probe.get("explain") if isinstance(probe.get("explain"), dict) else {}
        weighted = probe.get("objectiveWeighted", {}) or {}
        sorted_parts = sorted(
            [
                ("coverage", float(weighted.get("coverage", 0.0))),
                ("safety", float(weighted.get("safety", 0.0))),
                ("angle", float(weighted.get("angle", 0.0))),
            ],
            key=lambda item: item[1],
            reverse=True,
        )
        top_parts = ", ".join([f"{name}+{value:.3f}" for name, value in sorted_parts[:2]])
        if top_parts:
            explain["whySelected"] = f"Top weighted gains: {top_parts}"

        current_scores = probe.get("objectiveScores", {}) or {}
        alt_scores = best_alt.get("objectiveScores", {}) or {}
        d_cov = float(current_scores.get("coverage", 0.0)) - float(alt_scores.get("coverage", 0.0))
        d_safe = float(current_scores.get("safety", 0.0)) - float(alt_scores.get("safety", 0.0))
        d_ang = float(current_scores.get("angle", 0.0)) - float(alt_scores.get("angle", 0.0))
        d_total = float(probe.get("explainScore", 0.0)) - float(best_alt.get("explainScore", 0.0))
        explain["vsAlternative"] = (
            f"vs best alternative: Δcoverage={d_cov:+.3f}, Δsafety={d_safe:+.3f}, "
            f"Δangle={d_ang:+.3f}, Δscore={d_total:+.3f}"
        )
        explain["alternative"] = {
            "id": best_alt.get("id"),
            "ap_mm": round(float(best_alt.get("ap_mm", 0.0)), 4),
            "ml_mm": round(float(best_alt.get("ml_mm", 0.0)), 4),
            "theta_deg": round(float(best_alt.get("theta_deg", 0.0)), 4),
            "phi_deg": round(float(best_alt.get("phi_deg", 0.0)), 4),
            "explainScore": round(float(best_alt.get("explainScore", 0.0)), 6),
        }
        probe["explain"] = explain


def _build_preference_preview(candidates):
    if not candidates:
        return {}
    presets = {
        "balanced": (0.50, 0.30, 0.20),
        "coverage_first": (0.68, 0.22, 0.10),
        "safety_first": (0.25, 0.65, 0.10),
        "angle_first": (0.30, 0.20, 0.50),
    }
    preview = {}
    for preset, (w_cov, w_safe, w_ang) in presets.items():
        def _score(c):
            scores = c.get("objectiveScores", {}) or {}
            robust = float(c.get("robustScore", scores.get("robust", 1.0)))
            drcc = c.get("drccMetrics") if isinstance(c.get("drccMetrics"), dict) else {}
            drcc_bonus = 1.0
            if drcc:
                drcc_bonus = max(
                    0.2,
                    1.0 - float(drcc.get("worstCaseViolationProb", 1.0)),
                )
            base = (
                float(scores.get("coverage", 0.0)) * w_cov
                + float(scores.get("safety", 0.0)) * w_safe
                + float(scores.get("angle", 0.0)) * w_ang
            )
            return base * (0.75 + 0.25 * robust) * drcc_bonus

        ranked = sorted(
            candidates,
            key=lambda c: (-float(_score(c)), int(c.get("paretoRank", 10**6)), _candidate_signature(c)),
        )
        top_items = []
        for cand in ranked[:3]:
            top_items.append(
                {
                    "id": cand.get("id"),
                    "ap_mm": round(float(cand.get("ap_mm", 0.0)), 4),
                    "ml_mm": round(float(cand.get("ml_mm", 0.0)), 4),
                    "theta_deg": round(float(cand.get("theta_deg", 0.0)), 4),
                    "phi_deg": round(float(cand.get("phi_deg", 0.0)), 4),
                    "score": round(float(_score(cand)), 6),
                }
            )
        preview[preset] = {"top": top_items}
    return preview


def _dominates_objective(a, b, objective_fields):
    ge_all = True
    gt_any = False
    for key in objective_fields:
        av = float(a.get(key, 0.0))
        bv = float(b.get(key, 0.0))
        if av + 1e-12 < bv:
            ge_all = False
            break
        if av > bv + 1e-12:
            gt_any = True
    return ge_all and gt_any


def _assign_pareto_ranks(candidates, params):
    if not candidates:
        return {"frontCount": 0, "frontOneSize": 0, "poolSize": 0}

    if not params.get("paretoEnabled", True):
        for c in candidates:
            c["paretoRank"] = 1
        return {"frontCount": 1, "frontOneSize": len(candidates), "poolSize": len(candidates)}

    top_k = int(params.get("paretoTopK", 12))
    pool_size = min(len(candidates), max(top_k * 40, 120))
    constraint_aware = bool(params.get("constraintAwareSearch", CONSTRAINT_AWARE_SEARCH_DEFAULT))
    sorted_indices = sorted(
        range(len(candidates)),
        key=lambda idx: (
            _risk_prefilter_sort_key(candidates[idx]) if constraint_aware else (0, 0.0),
            -float(candidates[idx].get("explainScore", 0.0)),
            _candidate_signature(candidates[idx]),
        ),
    )
    pool_indices = sorted_indices[:pool_size]

    robust_enabled = bool(params.get("robustOptimization", False))
    objective_fields = ["coverage", "safety", "angle"]
    if robust_enabled:
        objective_fields.append("robust")
    if _drcc_enabled(params):
        objective_fields.append("drccSafety")

    scores_by_index = {
        idx: (candidates[idx].get("objectiveScores", {}) or {}) for idx in pool_indices
    }
    dominates = {idx: [] for idx in pool_indices}
    dominated_count = {idx: 0 for idx in pool_indices}

    for i in range(len(pool_indices)):
        a_idx = pool_indices[i]
        a_obj = scores_by_index[a_idx]
        for j in range(i + 1, len(pool_indices)):
            b_idx = pool_indices[j]
            b_obj = scores_by_index[b_idx]
            if _dominates_objective(a_obj, b_obj, objective_fields):
                dominates[a_idx].append(b_idx)
                dominated_count[b_idx] += 1
            elif _dominates_objective(b_obj, a_obj, objective_fields):
                dominates[b_idx].append(a_idx)
                dominated_count[a_idx] += 1

    fronts = []
    current = [idx for idx in pool_indices if dominated_count[idx] == 0]
    rank = 1
    while current:
        fronts.append(current)
        next_front = []
        for idx in current:
            candidates[idx]["paretoRank"] = rank
            for dominated in dominates[idx]:
                dominated_count[dominated] -= 1
                if dominated_count[dominated] == 0:
                    next_front.append(dominated)
        rank += 1
        current = next_front

    fallback_rank = rank
    for idx in sorted_indices[pool_size:]:
        candidates[idx]["paretoRank"] = fallback_rank

    for idx in pool_indices:
        if "paretoRank" not in candidates[idx]:
            candidates[idx]["paretoRank"] = fallback_rank

    front_one_size = len(fronts[0]) if fronts else 0
    return {"frontCount": len(fronts), "frontOneSize": front_one_size, "poolSize": pool_size}


#        Diversity-aware candidate reduction                                                                                 

def _jaccard(set_a, set_b):
    sa, sb = set(set_a), set(set_b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def diversity_reduce_candidates(candidates, top_k=6):
    """
    [FIX-3] Diversity-aware candidate reduction.
    Keeps candidates whose coverage sets are sufficiently distinct (Jaccard < 0.65).
    """
    JACCARD_THRESH = 0.65
    from collections import defaultdict
    groups = defaultdict(list)
    for c in candidates:
        key = (round(c["ap_mm"] * 10), round(c["ml_mm"] * 10))
        groups[key].append(c)

    reduced = []
    for group in groups.values():
        group.sort(key=lambda c: (
            _risk_prefilter_sort_key(c),
            int(c.get("paretoRank", 10**6)),
            -float(c.get("explainScore", 0.0)),
            -sum(c.get("overlapVoxels", {}).values()),
            -len(c.get("coveredRegions", [])),
            c.get("theta_deg", 0),
            c.get("phi_deg", 0),
            _candidate_signature(c),
        ))
        selected = [group[0]]
        for cand in group[1:]:
            if len(selected) >= top_k:
                break
            cov     = cand.get("coveredRegions", [])
            max_j   = max(_jaccard(cov, s.get("coveredRegions", [])) for s in selected)
            if max_j < JACCARD_THRESH:
                selected.append(cand)
        reduced.extend(selected)

    return reduced


#        Candidate generation                                                                                                             

def compute_structure_principal_axis(structure_id):
    """Return a CCF-XYZ PCA direction and an anterior-positive AP/DV/ML centroid."""
    if atlas_annotation is None:
        return None

    desc  = descendant_ids.get(structure_id, frozenset([structure_id]))
    mask  = np.isin(atlas_annotation, list(desc))
    voxel_coords = np.argwhere(mask).astype(np.float64)

    if len(voxel_coords) < 10:
        return None

    coords_mm = voxel_coords * (VOXEL_SIZE_UM / 1000.0)
    centroid = coords_mm.mean(axis=0)
    centered = coords_mm - centroid

    _, s_vals, Vt = np.linalg.svd(centered, full_matrices=False)
    principal = Vt[0]
    if principal[1] < 0:
        principal = -principal

    theta = float(np.degrees(np.arccos(min(1.0, principal[1]))))
    phi   = float(np.degrees(np.arctan2(principal[2], principal[0])) % 360)
    elongation = float(s_vals[0] / s_vals[1]) if s_vals[1] > 1e-9 else 1.0
    dv_span_um = float((coords_mm[:, 1].max() - coords_mm[:, 1].min()) * 1000)

    return {
        "theta_deg":       round(theta, 2),
        "phi_deg":         round(phi,   2),
        "centroid_bregma": [round(float(BREGMA_UM[0] / 1000.0 - centroid[0]), 3),
                            round(float(centroid[1] - BREGMA_UM[1] / 1000.0), 3),
                            round(float(centroid[2] - BREGMA_UM[2] / 1000.0), 3)],
        "dv_span_um":      round(dv_span_um, 1),
        "elongation":      round(elongation, 3),
        "principal_axis":  [round(float(x), 6) for x in principal],
    }


def generate_atlas_guided_candidates(target_keys, params, max_angle):
    """Generate candidates guided by PCA principal axis."""
    depth_um   = params.get("depthUm", 10000)
    skull_tilt = params.get("skullTiltDeg", 0.0)

    candidates = []
    for key in target_keys:
        sid  = PLANNER_REGIONS[key]["id"]
        axis = compute_structure_principal_axis(sid)
        if axis is None or axis["elongation"] < 1.5:
            continue

        base_theta = min(axis["theta_deg"], max_angle)
        base_phi   = axis["phi_deg"]
        priority   = "high" if axis["elongation"] > 3.0 else "normal"

        for d_theta in [-3, 0, 3]:
            for d_phi in [-20, 0, 20]:
                theta = max(0, min(max_angle, base_theta + d_theta))
                phi   = (base_phi + d_phi) % 360

                cent = axis["centroid_bregma"]
                th_r = math.radians(theta)
                ph_r = math.radians(phi)
                dir_x = math.sin(th_r) * math.cos(ph_r)
                dir_y = math.cos(th_r)
                dir_z = math.sin(th_r) * math.sin(ph_r)

                if abs(dir_y) < 0.05:
                    continue

                tilt_r = math.radians(skull_tilt)
                tan_t  = math.tan(tilt_r)
                denom  = 1 - dir_x * tan_t / dir_y

                if abs(denom) < 1e-6:
                    continue

                ap_mm = (cent[0] + dir_x * cent[1] / dir_y) / denom
                D_mm  = (cent[1] + ap_mm * tan_t) / dir_y
                ml_mm = cent[2] - dir_z * D_mm

                candidates.append({
                    "_source":      f"pca_{key}_{priority}",
                    "_priority":    priority,
                    "ap_mm":        ap_mm,
                    "ml_mm":        ml_mm,
                    "theta_deg":    theta,
                    "phi_deg":      phi,
                    "depthUm":      depth_um,
                    "skullTiltDeg": skull_tilt,
                })

    log.debug(f"PCA-guided candidates: {len(candidates)}")
    return candidates


def generate_multi_target_candidates(target_keys, params):
    """Generate pairwise multi-target line candidates."""
    from itertools import combinations

    depth_um   = params.get("depthUm",     10000)
    max_angle  = params.get("maxAngle",    15)
    skull_tilt = params.get("skullTiltDeg", 0.0)
    lambda_ap  = LAMBDA_AP_MM

    candidates = []
    for key_a, key_b in combinations(target_keys, 2):
        if key_a not in PLANNER_REGIONS or key_b not in PLANNER_REGIONS:
            continue

        sid_a = PLANNER_REGIONS[key_a]["id"]
        sid_b = PLANNER_REGIONS[key_b]["id"]
        ca = np.array(REGION_CENTROIDS_UM[sid_a], dtype=float)
        cb = np.array(REGION_CENTROIDS_UM[sid_b], dtype=float)

        direction = cb - ca
        if direction[1] < 0:
            ca, cb = cb, ca
            direction = -direction
        if direction[1] < 50:
            continue

        direction_n = direction / np.linalg.norm(direction)
        # Intersect the line with Y-By = tan(tilt)*(X-Bx), in CCF micrometres.
        tan_tilt = math.tan(math.radians(skull_tilt))
        denominator = direction_n[1] - tan_tilt * direction_n[0]
        if abs(denominator) < 1e-6:
            continue
        t_skull = (BREGMA_UM[1] - ca[1] + tan_tilt * (ca[0] - BREGMA_UM[0])) / denominator
        entry_um = ca + t_skull * direction_n

        ap_mm = (BREGMA_UM[0] - entry_um[0]) / 1000.0
        ml_mm = (entry_um[2]  - BREGMA_UM[2]) / 1000.0

        if ap_mm < lambda_ap:
            continue

        theta = float(np.degrees(np.arccos(min(1.0, abs(direction_n[1])))))
        phi   = float(np.degrees(np.arctan2(direction_n[2], direction_n[0])) % 360)

        if theta > max_angle:
            continue

        depth = float(np.linalg.norm(cb - entry_um)) + 1000.0
        depth = min(depth, depth_um)

        candidates.append({
            "_source":      f"pair_{key_a}_{key_b}",
            "_priority":    "high",
            "ap_mm":        round(ap_mm, 3),
            "ml_mm":        round(ml_mm, 3),
            "theta_deg":    round(theta, 2),
            "phi_deg":      round(phi,   2),
            "depthUm":      round(depth, 0),
            "skullTiltDeg": skull_tilt,
        })

    log.debug(f"Pairwise multi-target candidates: {len(candidates)}")
    return candidates


def _candidate_depth_variants(base_depth_um, max_depth_um, target_dv_um, enforce_brain_bounds):
    base = float(max(500.0, min(float(base_depth_um), float(max_depth_um))))
    values = [base]
    if enforce_brain_bounds:
        for margin_um in (500.0, 900.0, 1300.0, 1800.0, 2500.0):
            values.append(max(500.0, min(base, float(target_dv_um) + margin_um)))
    output = []
    seen = set()
    for value in values:
        rounded = round(float(value), 1)
        if rounded not in seen:
            seen.add(rounded)
            output.append(rounded)
    return output


def _target_local_ml_centers(ml_mm, bilateral):
    centers = [round(float(ml_mm), 6)]
    mirrored = round(-float(ml_mm), 6)
    if bilateral and mirrored not in centers:
        centers.append(mirrored)
    return centers


def _mirror_bilateral_candidate_spec(spec):
    ap, ml, theta, phi, depth, source = spec
    mirrored_phi = float(phi) if abs(float(theta)) <= 1e-12 else (-float(phi)) % 360.0
    return (
        float(ap),
        round(-float(ml), 3),
        float(theta),
        round(mirrored_phi, 1),
        float(depth),
        str(source),
    )


def generate_candidates_voxel(
    target_keys,
    params,
    constraint_modes,
    correction_factors=None,
    probe_params=None,
    progress_cb=None,
    cancel_cb=None,
    candidate_sink=None,
):
    """Construct and refine a finite pool from atlas-based trajectory proposals.

    Combine global and per-target local grids, principal-axis and target-pair
    proposals, with endpoint-aware depth variants and conditional reference
    proposals. Deduplicate specifications before coarse screening, diversity
    reduction and fine sampling. Forced seeds preserve eligible proposal
    families; they do not bypass downstream constraints. Later selection and
    strict audits remain separate from proposal generation. This sampled pool
    is not an exhaustive continuous-space search.
    """
    grid_mm    = params.get("gridSpacing", 600) / 1000.0
    angle_step = int(max(1, round(float(params.get("angleStep", 5)))))
    max_angle  = float(params.get("maxAngle", 15))
    max_angle_step = int(max(0, math.floor(max_angle)))
    depth_um   = params.get("depthUm", 10000)
    bilateral  = params.get("bilateral", False)
    skull_tilt = params.get("skullTiltDeg", 0.0)
    probe_radius_um = max(
        5.0,
        float((probe_params or {}).get("probeRadiusUm", (probe_params or {}).get("shankWidthUm", 70) / 2.0)),
    )
    shank_count = int(max(1, round(float((probe_params or {}).get("shankCount", (probe_params or {}).get("shanks", 1)) or 1))))
    shank_spacing_um = float(
        (probe_params or {}).get(
            "shankSpacingUm",
            (probe_params or {}).get("shankPitchUm", 250.0 if shank_count > 1 else 0.0),
        )
        or 0.0
    )
    probe_type = str((probe_params or {}).get("probeType") or (probe_params or {}).get("preset") or "").strip()
    corr = correction_factors or DEFAULT_CORRECTION_FACTORS
    progress_total = 1000.0

    def _emit_progress(value):
        if progress_cb is None:
            return
        try:
            progress_cb(float(value), progress_total)
        except Exception:
            pass

    MAX_ATLAS_DEPTH_UM = (ATLAS_SHAPE[1] * VOXEL_SIZE_UM) - BREGMA_UM[1] - 50
    depth_um = min(depth_um, MAX_ATLAS_DEPTH_UM)
    _emit_progress(20.0)

    regions    = {k: PLANNER_REGIONS[k] for k in target_keys if k in PLANNER_REGIONS}
    target_ids = {k: PLANNER_REGIONS[k]["id"] for k in target_keys if k in PLANNER_REGIONS}
    target_dv_um = max(
        [float(r.get("dv", 0.0)) * 1000.0 for r in regions.values()] or [float(depth_um)]
    )

    def _depth_variants(base_depth_um, target_dv_override_um=None):
        if not bool(params.get("depthOptimizationEnabled", True)):
            return [float(depth_um)]
        return _candidate_depth_variants(
            base_depth_um,
            depth_um,
            target_dv_um if target_dv_override_um is None else target_dv_override_um,
            constraint_modes.get("brainBounds") == "enforce",
        )

    # Use planner-region AP convention directly. Negating here can mirror the
    # search grid and wipe out candidates for posterior targets in MRI/CCF.
    ap_vals = [r["ap"] for r in regions.values()]
    ml_vals = [r["ml"]  for r in regions.values()]
    if bilateral:
        ml_vals += [-ml for ml in ml_vals]

    margin = 2.5
    ap_min = min(ap_vals) - margin; ap_max = max(ap_vals) + margin
    ml_min = min(ml_vals) - margin; ml_max = max(ml_vals) + margin

    angle_dirs = [(0, 0)]
    for th in range(angle_step, max_angle_step + 1, angle_step):
        for ph in range(0, 360, 30):
            angle_dirs.append((th, ph))

    raw_specs = []
    ap = ap_min
    while ap <= ap_max + 1e-9:
        ap_eval = ap * corr["ap"] + corr["apShift"]
        if constraint_modes.get("lambdaBoundary") == "enforce" and ap_eval < LAMBDA_AP_MM:
            ap += grid_mm; continue
        ml = ml_min
        while ml <= ml_max + 1e-9:
            for theta, phi in angle_dirs:
                if theta <= max_angle:
                    for dep in _depth_variants(depth_um):
                        raw_specs.append((round(ap, 3), round(ml, 3), theta, phi,
                                          dep, "grid"))
            ml += grid_mm
        ap += grid_mm
    _emit_progress(180.0)
    n_global_grid = len(raw_specs)

    # A global lattice changes phase as a target set expands.  That can omit a
    # valid entry point that was reachable in a single-target request, even
    # though the point is still inside the combined search box.  Add compact
    # Target-anchored lattices must be generated for every request, including
    # single-target planning.  Otherwise a target can be reachable only after
    # adding an unrelated target because this candidate family was previously
    # gated on the target-set cardinality.
    n_target_local_grid = 0
    for target_key, region in regions.items():
        local_ap = float(region["ap"]) - margin
        local_ap_max = float(region["ap"]) + margin
        target_dv = float(region.get("dv", 0.0)) * 1000.0
        while local_ap <= local_ap_max + 1e-9:
            ap_eval = local_ap * corr["ap"] + corr["apShift"]
            if constraint_modes.get("lambdaBoundary") == "enforce" and ap_eval < LAMBDA_AP_MM:
                local_ap += grid_mm
                continue
            for local_ml_center in _target_local_ml_centers(region["ml"], bilateral):
                local_ml = local_ml_center - margin
                local_ml_max = local_ml_center + margin
                while local_ml <= local_ml_max + 1e-9:
                    for theta, phi in angle_dirs:
                        if theta <= max_angle:
                            for dep in _depth_variants(depth_um, target_dv):
                                raw_specs.append(
                                    (
                                        round(local_ap, 3),
                                        round(local_ml, 3),
                                        theta,
                                        phi,
                                        dep,
                                        f"target_grid_{target_key}",
                                    )
                                )
                                n_target_local_grid += 1
                    local_ml += grid_mm
            local_ap += grid_mm
    n_grid = len(raw_specs)

    pca_specs = generate_atlas_guided_candidates(target_keys, params, max_angle)
    for s in pca_specs:
        for dep in _depth_variants(s["depthUm"]):
            raw_specs.append((round(s["ap_mm"], 3), round(s["ml_mm"], 3),
                              round(s["theta_deg"], 1), round(s["phi_deg"], 1),
                              dep, s.get("_source", "pca")))
    _emit_progress(300.0)

    pair_specs = generate_multi_target_candidates(target_keys, params)
    for s in pair_specs:
        raw_specs.append((round(s["ap_mm"], 3), round(s["ml_mm"], 3),
                          round(s["theta_deg"], 1), round(s["phi_deg"], 1),
                          round(s["depthUm"]), s.get("_source", "pair")))

    n_reference_specs = 0
    if bool(params.get("stabilityTieBreakerEnabled", False)):
        ap_scale = float(corr.get("ap", 1.0) or 1.0)
        ml_scale = float(corr.get("ml", 1.0) or 1.0)
        dv_scale = float(corr.get("dv", 1.0) or 1.0)
        for ref_index, reference in enumerate(params.get("stabilityReferenceTrajectories", []) or []):
            try:
                ref_entry = np.asarray(reference["entryCcfUm"], dtype=np.float64)
                ref_tip = np.asarray(reference["tipCcfUm"], dtype=np.float64)
                direction = ref_tip - ref_entry
                physical_depth_um = float(np.linalg.norm(direction))
                if physical_depth_um <= 1e-6:
                    continue
                direction /= physical_depth_um
                theta = float(math.degrees(math.acos(float(np.clip(direction[1], -1.0, 1.0)))))
                phi = float(math.degrees(math.atan2(direction[2], direction[0])) % 360.0)
                if theta > max_angle + 2.0:
                    continue
                ap_corrected = float((BREGMA_UM[0] - ref_entry[0]) / 1000.0)
                ml_corrected = float((ref_entry[2] - BREGMA_UM[2]) / 1000.0)
                ap_raw = (ap_corrected - float(corr.get("apShift", 0.0))) / ap_scale
                ml_raw = (ml_corrected - float(corr.get("mlShift", 0.0))) / ml_scale
                depth_raw = min(float(depth_um), physical_depth_um / dv_scale)
            except Exception:
                continue
            entry_offsets = (
                (0.0, 0.0),
                (-0.1, 0.0), (0.1, 0.0), (0.0, -0.1), (0.0, 0.1),
                (-0.2, 0.0), (0.2, 0.0), (0.0, -0.2), (0.0, 0.2),
                (-0.3, 0.0), (0.3, 0.0), (0.0, -0.3), (0.0, 0.3),
            )
            angle_offsets = ((0.0, 0.0), (-1.0, 0.0), (1.0, 0.0), (0.0, -10.0), (0.0, 10.0))
            for delta_ap, delta_ml in entry_offsets:
                for delta_theta, delta_phi in angle_offsets:
                    theta_variant = max(0.0, min(max_angle, theta + delta_theta))
                    phi_variant = (phi + delta_phi) % 360.0
                    raw_specs.append(
                        (
                            round(ap_raw + delta_ap, 3),
                            round(ml_raw + delta_ml, 3),
                            round(theta_variant, 1),
                            round(phi_variant, 1),
                            round(depth_raw, 1),
                            f"stability_reference_{ref_index}",
                        )
                    )
                    n_reference_specs += 1
    _emit_progress(420.0)

    log.info(
        f"Candidate specs: grid={n_global_grid} + targetLocal={n_target_local_grid} + pca={len(pca_specs)} "
        f"+ pair={len(pair_specs)} + reference={n_reference_specs} = {len(raw_specs)} total"
    )

    seen_specs: set = set()
    deduped = []
    for spec in raw_specs:
        key = (spec[0], spec[1], spec[2], spec[3], round(float(spec[4]), 1))
        if key not in seen_specs:
            seen_specs.add(key)
            deduped.append(spec)
    _emit_progress(520.0)
    candidate_gen_summary = {
        "specTotal": int(len(raw_specs)),
        "specDeduped": int(len(deduped)),
        "gridSpecs": int(n_grid),
        "globalGridSpecs": int(n_global_grid),
        "targetLocalGridSpecs": int(n_target_local_grid),
        "pcaSpecs": int(len(pca_specs)),
        "pairSpecs": int(len(pair_specs)),
        "stabilityReferenceSpecs": int(n_reference_specs),
        "depthVariantCount": int(max(1, len(_depth_variants(depth_um)))),
        "depthVariantsUm": [float(v) for v in _depth_variants(depth_um)],
    }
    coarse_reject_counts = {
        "bounds": 0,
        "emptyTrajectory": 0,
        "brainMaskNoIntersection": 0,
        "brainMaskRejected": 0,
        "brainMaskRecoverableKept": 0,
        "noCoverage": 0,
    }
    coarse_reject_by_source = {}
    refine_reject_counts = {
        "bounds": 0,
        "emptyTrajectory": 0,
        "brainMaskNoIntersection": 0,
        "brainMaskRejected": 0,
        "noCoverage": 0,
        "hemisphereMismatch": 0,
    }
    coarse_reject_lock = threading.Lock()
    refine_reject_lock = threading.Lock()

    def _bump_coarse_reject(reason_key, source=None):
        key = str(reason_key or "unknown")
        source_key = str(source or "unknown")
        with coarse_reject_lock:
            coarse_reject_counts[key] = int(coarse_reject_counts.get(key, 0)) + 1
            source_counts = coarse_reject_by_source.setdefault(source_key, {})
            source_counts[key] = int(source_counts.get(key, 0)) + 1

    def _coarse_reject_snapshot():
        with coarse_reject_lock:
            return {k: int(v) for k, v in coarse_reject_counts.items()}

    def _coarse_reject_by_source_snapshot():
        with coarse_reject_lock:
            return {
                str(source): {str(reason): int(count) for reason, count in counts.items()}
                for source, counts in coarse_reject_by_source.items()
            }

    def _bump_refine_reject(reason_key):
        key = str(reason_key or "unknown")
        with refine_reject_lock:
            refine_reject_counts[key] = int(refine_reject_counts.get(key, 0)) + 1

    def _refine_reject_snapshot():
        with refine_reject_lock:
            return {k: int(v) for k, v in refine_reject_counts.items()}

    worker_count = _resolve_parallel_workers(
        params.get("candidateGenWorkers", params.get("computeWorkers", CANDIDATE_GEN_WORKERS_DEFAULT)),
        default_workers=CANDIDATE_GEN_WORKERS_DEFAULT,
    )
    coarse_step_um = float(max(25.0, params.get("candidateCoarseStepUm", CANDIDATE_COARSE_STEP_UM_DEFAULT)))
    refine_step_um = float(max(5.0, params.get("candidateRefineStepUm", CANDIDATE_REFINE_STEP_UM_DEFAULT)))
    if refine_step_um > coarse_step_um:
        refine_step_um = coarse_step_um
    refine_fraction = float(max(0.05, min(1.0, params.get("candidateRefineFraction", CANDIDATE_REFINE_FRACTION_DEFAULT))))
    sink_batch_size = int(max(8, params.get("streamingScoreBatchSize", STREAMING_SCORE_BATCH_SIZE_DEFAULT)))
    coverage_radius_um = float(params.get("coverageRadius", 600))
    total_specs = max(1, len(deduped))
    progress_tick = max(1, total_specs // 60)
    sink_buffer = []

    def _flush_sink(done=None, total=None, force=False):
        if candidate_sink is None:
            return
        if not sink_buffer:
            return
        if (not force) and len(sink_buffer) < sink_batch_size:
            return
        payload = list(sink_buffer)
        sink_buffer.clear()
        try:
            candidate_sink(payload, done=done, total=total)
        except Exception as sink_exc:
            log.warning(f"candidate_sink flush failed: {sink_exc}")

    def _spec_geometry(spec):
        ap_s, ml_s, theta, phi, dep, source = spec
        ap_corr = ap_s * corr["ap"] + corr["apShift"]
        ml_corr = ml_s * corr["ml"] + corr["mlShift"]
        dep_corr = dep * corr["dv"]
        entry, tip, direction, dv_corr = make_trajectory_xyz(
            ap_corr, ml_corr, theta, phi, dep_corr, skull_tilt,
            entry_dv_corr_mm=_corrected_entry_dv_mm(ap_s, skull_tilt, corr)
        )
        return (
            float(ap_s), float(ml_s), float(theta), float(phi), float(dep), str(source),
            float(ap_corr), float(ml_corr), float(dep_corr), entry, tip, direction, float(dv_corr),
        )

    def _bounds_ok(ap_corr, dep_corr, direction, dv_corr):
        if constraint_modes.get("brainBounds") != "enforce":
            return True
        tip_ap, tip_dv = _tip_ap_dv_mm(ap_corr, dv_corr, direction, dep_corr)
        if ap_corr < ENTRY_AP_MIN_MM or ap_corr > ENTRY_AP_MAX_MM:
            return False
        if tip_ap < TIP_POST_LIMIT_MM or tip_dv > MAX_DV_MM:
            return False
        return True

    def _coarse_eval(item):
        spec_idx, spec = item
        try:
            (
                ap_s, ml_s, theta, phi, dep, source,
                ap_corr, ml_corr, dep_corr, entry, tip, direction, dv_corr,
            ) = _spec_geometry(spec)
            if not _bounds_ok(ap_corr, dep_corr, direction, dv_corr):
                _bump_coarse_reject("bounds", source)
                return None
            path_indices, path_depths_um = _sample_trajectory_path_indices(
                ap_corr,
                ml_corr,
                theta,
                phi,
                dep_corr,
                skull_tilt_deg=skull_tilt,
                step_um=coarse_step_um,
                entry_dv_corr_mm=dv_corr,
            )
            if not path_indices:
                _bump_coarse_reject("emptyTrajectory", source)
                return None
            generation_brain_bounds_violation = None
            if constraint_modes.get("brainBounds") == "enforce":
                brain_mask_summary = _brain_mask_path_summary_from_voxels(
                    path_indices,
                    step_um=coarse_step_um,
                )
                if brain_mask_summary.get("available") and not brain_mask_summary.get("passed", True):
                    reason = str(brain_mask_summary.get("reason") or "")
                    # Enforced brain bounds are planning constraints, not just a
                    # post-hoc safety contract. Keep invalid trajectories out of
                    # the optimizer by default; repair/fallback should work from
                    # feasible geometry or explicitly generated local variants.
                    if reason == "no_brain_intersection":
                        _bump_coarse_reject("brainMaskNoIntersection", source)
                        return None
                    if bool(params.get("candidateKeepRecoverableBrainBounds", False)):
                        generation_brain_bounds_violation = reason or "brain_mask_violation"
                        _bump_coarse_reject("brainMaskRecoverableKept", source)
                    else:
                        _bump_coarse_reject("brainMaskRejected", source)
                        return None
            valid_path = [
                (voxel, depth)
                for voxel, depth in zip(path_indices, path_depths_um)
                if voxel_in_bounds(*voxel)
            ]
            traj_voxels = [voxel for voxel, _ in valid_path]
            traj_depths_um = [depth for _, depth in valid_path]
            if not traj_voxels:
                _bump_coarse_reject("emptyTrajectory", source)
                return None
            profile = compute_trajectory_profile(
                traj_voxels,
                list(target_ids.values()),
                step_um=coarse_step_um,
                sample_depths_um=traj_depths_um,
            )
            covered_keys = [
                k for k, sid in target_ids.items()
                if profile.get(sid, {}).get("covered", False)
            ]
            if not covered_keys:
                entry_um_arr = np.asarray(entry, dtype=np.float64)
                tip_um_arr = np.asarray(tip, dtype=np.float64)
                for key, sid in target_ids.items():
                    if structure_map.get(sid, {}).get("voxel_count", 0) != 0:
                        continue
                    dist_um = pt_seg_dist_um(np.asarray(REGION_CENTROIDS_UM[sid]), entry_um_arr, tip_um_arr)
                    if dist_um <= coverage_radius_um:
                        covered_keys.append(key)
            if not covered_keys:
                _bump_coarse_reject("noCoverage", source)
                return None
            covered_region_sides = {}
            for key in covered_keys:
                target_profile = profile.get(target_ids[key], {})
                sides = [
                    str(side) for side in (target_profile.get("coveredSides") or [])
                    if str(side) in ("left", "right")
                ]
                if not sides:
                    if int(target_profile.get("left_voxels", 0) or 0) > 0:
                        sides.append("left")
                    if int(target_profile.get("right_voxels", 0) or 0) > 0:
                        sides.append("right")
                if not sides:
                    sides = ["right" if ml_corr > 0 else "left"]
                covered_region_sides[key] = sorted(set(sides))
            overlap_score = float(
                sum(float(profile.get(target_ids[k], {}).get("n_voxels", 0)) for k in covered_keys)
            )
            return {
                "idx": int(spec_idx),
                "spec": spec,
                "coveredKeys": covered_keys,
                "coveredRegionSides": covered_region_sides,
                "coveredCount": int(len(covered_keys)),
                "overlapScore": overlap_score,
                "forced": bool(str(source).lower().startswith(("pca_", "pair_"))),
                "brainBoundsGenerationViolation": generation_brain_bounds_violation,
            }
        except Exception:
            source = spec[5] if isinstance(spec, (list, tuple)) and len(spec) > 5 else "unknown"
            _bump_coarse_reject("exception", source)
            return None

    indexed_specs = list(enumerate(deduped, start=1))
    coarse_rows = []
    if worker_count > 1 and len(indexed_specs) >= max(32, worker_count * 4):
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="cand-coarse") as ex:
            for pos, row in zip(range(1, len(indexed_specs) + 1), ex.map(_coarse_eval, indexed_specs)):
                if row is not None:
                    coarse_rows.append(row)
                if pos % progress_tick == 0 or pos == len(indexed_specs):
                    _emit_progress(520.0 + 160.0 * (pos / max(1, len(indexed_specs))))
                    if cancel_cb is not None:
                        try:
                            cancel_cb()
                        except Exception:
                            pass
    else:
        for pos, item in enumerate(indexed_specs, start=1):
            row = _coarse_eval(item)
            if row is not None:
                coarse_rows.append(row)
            if pos % progress_tick == 0 or pos == len(indexed_specs):
                _emit_progress(520.0 + 160.0 * (pos / max(1, len(indexed_specs))))
                if cancel_cb is not None:
                    try:
                        cancel_cb()
                    except Exception:
                        pass

    # Close only missing target sides under exact ML reflection. Lattices
    # stepped from independent lower bounds can have different phases on the
    # two sides, but mirroring every feasible ray needlessly doubles normal
    # bilateral pools. Mirrored rays still pass all coarse constraints.
    bilateral_mirror_spec_count = 0
    bilateral_mirror_accepted_count = 0
    bilateral_missing_tokens_before = []
    bilateral_missing_tokens_after = []
    if bilateral and coarse_rows:
        required_side_tokens = {
            f"{key}:{side}" for key in target_ids for side in ("left", "right")
        }

        def _coarse_side_tokens(rows):
            tokens = set()
            for coarse_row in rows:
                for key, sides in (coarse_row.get("coveredRegionSides") or {}).items():
                    for side in sides:
                        if side in ("left", "right"):
                            tokens.add(f"{key}:{side}")
            return tokens

        present_side_tokens = _coarse_side_tokens(coarse_rows)
        missing_side_tokens = required_side_tokens - present_side_tokens
        bilateral_missing_tokens_before = sorted(missing_side_tokens)
        mirror_items = []
        next_spec_idx = len(deduped) + 1
        for coarse_row in list(coarse_rows):
            mirrored_tokens = {
                f"{key}:{'right' if side == 'left' else 'left'}"
                for key, sides in (coarse_row.get("coveredRegionSides") or {}).items()
                for side in sides
                if side in ("left", "right")
            }
            if not (mirrored_tokens & missing_side_tokens):
                continue
            mirrored = _mirror_bilateral_candidate_spec(coarse_row["spec"])
            key = (
                mirrored[0], mirrored[1], mirrored[2], mirrored[3],
                round(float(mirrored[4]), 1),
            )
            if key in seen_specs:
                continue
            seen_specs.add(key)
            deduped.append(mirrored)
            mirror_items.append((next_spec_idx, mirrored))
            next_spec_idx += 1
        bilateral_mirror_spec_count = len(mirror_items)
        for item in mirror_items:
            mirrored_row = _coarse_eval(item)
            if mirrored_row is not None:
                coarse_rows.append(mirrored_row)
                bilateral_mirror_accepted_count += 1
        candidate_gen_summary["specDeduped"] = int(len(deduped))
        bilateral_missing_tokens_after = sorted(
            required_side_tokens - _coarse_side_tokens(coarse_rows)
        )
    candidate_gen_summary["bilateralMirrorSpecCount"] = int(bilateral_mirror_spec_count)
    candidate_gen_summary["bilateralMirrorAcceptedCount"] = int(
        bilateral_mirror_accepted_count
    )
    candidate_gen_summary["bilateralMissingTokensBeforeMirror"] = (
        bilateral_missing_tokens_before
    )
    candidate_gen_summary["bilateralMissingTokensAfterMirror"] = (
        bilateral_missing_tokens_after
    )

    if not coarse_rows:
        candidate_gen_summary.update(
            {
                "workersUsed": int(worker_count),
                "coarseKept": 0,
                "refineSelected": 0,
                "finalCount": 0,
                "coarseStepUm": float(coarse_step_um),
                "refineStepUm": float(refine_step_um),
                "refineFraction": float(refine_fraction),
                "coarseRejectReasons": _coarse_reject_snapshot(),
                "coarseRejectReasonsBySource": _coarse_reject_by_source_snapshot(),
                "refineRejectReasons": _refine_reject_snapshot(),
            }
        )
        generate_candidates_voxel.last_summary = candidate_gen_summary
        _emit_progress(1000.0)
        log.info(f"    0      ?   : {target_keys}")
        return []

    coarse_accepted_counts_by_source = {}
    coarse_coverage_counts_by_target = {str(key): 0 for key in target_ids}
    coarse_coverage_counts_by_target_and_source = {str(key): {} for key in target_ids}
    for row in coarse_rows:
        spec = row.get("spec") or ()
        source = str(spec[5]) if len(spec) > 5 else "unknown"
        coarse_accepted_counts_by_source[source] = int(coarse_accepted_counts_by_source.get(source, 0)) + 1
        for key in row.get("coveredKeys") or []:
            target_key = str(key)
            coarse_coverage_counts_by_target[target_key] = int(
                coarse_coverage_counts_by_target.get(target_key, 0)
            ) + 1
            by_source = coarse_coverage_counts_by_target_and_source.setdefault(target_key, {})
            by_source[source] = int(by_source.get(source, 0)) + 1

    forced_idxs = {int(r["idx"]) for r in coarse_rows if bool(r.get("forced"))}
    per_target_best = {}
    target_diversity_seeds = int(
        max(1, params.get("candidateTargetDiversitySeeds", CANDIDATE_TARGET_DIVERSITY_SEEDS_DEFAULT))
    )
    target_entry_variants = int(
        max(1, params.get("candidateTargetEntryVariants", CANDIDATE_TARGET_ENTRY_VARIANTS_DEFAULT))
    )
    target_diversity_counts = {}
    target_entry_variant_counts = {}
    target_side_seed_counts = {}
    for key in target_ids.keys():
        rows = [r for r in coarse_rows if key in (r.get("coveredKeys") or [])]
        if not rows:
            target_diversity_counts[key] = 0
            target_side_seed_counts[key] = {"left": 0, "right": 0}
            continue
        ranked_target_rows = sorted(
            rows,
            key=lambda r: (
                int(r.get("coveredCount", 0)),
                float(r.get("overlapScore", 0.0)),
                -int(r.get("idx", 10**9)),
            ),
            reverse=True,
        )
        per_target_best[key] = int(ranked_target_rows[0]["idx"])
        diversity_rows = ranked_target_rows[:target_diversity_seeds]
        forced_idxs |= {int(row["idx"]) for row in diversity_rows}
        target_diversity_counts[key] = int(len(diversity_rows))

        side_counts = {"left": 0, "right": 0}
        if bilateral:
            for side in ("left", "right"):
                side_rows = [
                    row for row in ranked_target_rows
                    if side in (row.get("coveredRegionSides", {}).get(key) or [])
                ]
                if side_rows:
                    forced_idxs.add(int(side_rows[0]["idx"]))
                    side_counts[side] = 1
        target_side_seed_counts[key] = side_counts

        # Preserve several angle/depth variants for every target-local entry
        # point.  A high-overlap ray at one entry can violate the MRI-derived
        # risk layers while a nearby angular variant is safe; collapsing them
        # before fine evaluation makes a joint multi-probe solution disappear.
        local_groups = {}
        local_source = f"target_grid_{key}"
        for row in rows:
            spec = row.get("spec") or ()
            if len(spec) < 6 or str(spec[5]) != local_source:
                continue
            entry_key = (round(float(spec[0]), 4), round(float(spec[1]), 4))
            local_groups.setdefault(entry_key, []).append(row)
        added_for_target = 0
        for group_rows in local_groups.values():
            ranked_group = sorted(
                group_rows,
                key=lambda r: (
                    int(r.get("coveredCount", 0)),
                    float(r.get("overlapScore", 0.0)),
                    -int(r.get("idx", 10**9)),
                ),
                reverse=True,
            )
            selected_group = ranked_group[:target_entry_variants]
            forced_idxs |= {int(row["idx"]) for row in selected_group}
            added_for_target += len(selected_group)
        target_entry_variant_counts[key] = int(added_for_target)

    ranked_rows = sorted(
        coarse_rows,
        key=lambda r: (
            1 if bool(r.get("forced")) else 0,
            int(r.get("coveredCount", 0)),
            float(r.get("overlapScore", 0.0)),
            -int(r.get("idx", 10**9)),
        ),
        reverse=True,
    )
    min_refine = min(len(ranked_rows), max(32, len(target_ids) * 6, len(forced_idxs)))
    refine_keep = max(min_refine, int(math.ceil(len(ranked_rows) * refine_fraction)))
    chosen_idxs = set(int(r["idx"]) for r in ranked_rows[:refine_keep]) | forced_idxs
    chosen_rows = [r for r in ranked_rows if int(r["idx"]) in chosen_idxs]
    chosen_rows = sorted(chosen_rows, key=lambda r: int(r["idx"]))
    _emit_progress(700.0)

    def _refine_eval(row):
        try:
            (
                ap_s, ml_s, theta, phi, dep, source,
                ap_corr, ml_corr, dep_corr, entry, tip, direction, dv_corr,
            ) = _spec_geometry(row["spec"])
            if not _bounds_ok(ap_corr, dep_corr, direction, dv_corr):
                _bump_refine_reject("bounds")
                return None
            path_indices, path_depths_um = _sample_trajectory_path_indices(
                ap_corr,
                ml_corr,
                theta,
                phi,
                dep_corr,
                skull_tilt_deg=skull_tilt,
                step_um=refine_step_um,
                entry_dv_corr_mm=dv_corr,
            )
            if not path_indices:
                _bump_refine_reject("emptyTrajectory")
                return None
            brain_mask_summary = None
            if constraint_modes.get("brainBounds") == "enforce":
                brain_mask_summary = _brain_mask_path_summary_from_voxels(
                    path_indices,
                    step_um=refine_step_um,
                )
                if brain_mask_summary.get("available") and not brain_mask_summary.get("passed", True):
                    reason = str(brain_mask_summary.get("reason") or "")
                    if reason == "no_brain_intersection":
                        _bump_refine_reject("brainMaskNoIntersection")
                    else:
                        _bump_refine_reject("brainMaskRejected")
                    return None
            entry_um_arr = np.asarray(entry, dtype=np.float64)
            tip_um_arr = np.asarray(tip, dtype=np.float64)
            valid_path = [
                (voxel, depth)
                for voxel, depth in zip(path_indices, path_depths_um)
                if voxel_in_bounds(*voxel)
            ]
            traj_voxels = [voxel for voxel, _ in valid_path]
            traj_depths_um = [depth for _, depth in valid_path]
            if not traj_voxels:
                _bump_refine_reject("emptyTrajectory")
                return None
            profile = compute_trajectory_profile(
                traj_voxels,
                list(target_ids.values()),
                step_um=refine_step_um,
                sample_depths_um=traj_depths_um,
            )
            covered_keys = [
                k for k, sid in target_ids.items()
                if profile.get(sid, {}).get("covered", False)
            ]
            for key, sid in target_ids.items():
                if key in covered_keys:
                    continue
                if structure_map.get(sid, {}).get("voxel_count", 0) != 0:
                    continue
                dist_um = pt_seg_dist_um(np.asarray(REGION_CENTROIDS_UM[sid]), entry_um_arr, tip_um_arr)
                if dist_um <= coverage_radius_um:
                    covered_keys.append(key)
                    fallback_side = "right" if ml_corr > 0 else "left"
                    profile[sid] = {
                        "covered": True,
                        "n_voxels": 0,
                        "coverage_frac": 0.0,
                        "entry_depth_um": None,
                        "exit_depth_um": None,
                        "channel_span_um": 0,
                        "voxel_score": 0.0,
                        "left_voxels": 1 if fallback_side == "left" else 0,
                        "right_voxels": 1 if fallback_side == "right" else 0,
                        "coveredSides": [fallback_side],
                    }
            if not covered_keys:
                _bump_refine_reject("noCoverage")
                return None
            if constraint_modes.get("hemisphere") == "enforce" and has_hemisphere_mismatch(
                {"ml_mm_corrected": ml_corr, "coveredRegions": covered_keys}
            ):
                _bump_refine_reject("hemisphereMismatch")
                return None

            overlap_counts = {
                k: int(profile[target_ids[k]].get("n_voxels", 0))
                for k in covered_keys
            }
            covered_region_sides = {
                k: profile[target_ids[k]].get("coveredSides", [])
                for k in covered_keys
                if target_ids[k] in profile
            }
            return {
                "_specIdx": int(row["idx"]),
                "ap_mm": ap_s,
                "ml_mm": ml_s,
                "depthUm": dep,
                "ap_mm_corrected": round(ap_corr, 5),
                "ml_mm_corrected": round(ml_corr, 5),
                "depthUm_corrected": round(dep_corr, 2),
                "theta_deg": theta,
                "phi_deg": phi,
                "skullTiltDeg": skull_tilt,
                "entryDvCorr_mm": round(_skull_entry_dv_mm(ap_s, skull_tilt), 5),
                "entryDvCorr_mm_corrected": round(dv_corr, 5),
                "entry_xyz": [round(x, 1) for x in entry],
                "tip_xyz": [round(x, 1) for x in tip],
                "dir": [round(x, 6) for x in direction],
                "coveredRegions": covered_keys,
                "coveredRegionSides": covered_region_sides,
                "overlapVoxels": overlap_counts,
                "nVoxelsTotal": len(traj_voxels),
                "radiusUm": round(probe_radius_um, 4),
                "probeRadiusUm": round(probe_radius_um, 4),
                "probeType": probe_type,
                "shanks": int(shank_count),
                "shankCount": int(shank_count),
                "shankSpacingUm": round(float(shank_spacing_um), 4),
                "shankPitchUm": round(float(shank_spacing_um), 4),
                "shankWidthUm": round(float((probe_params or {}).get("shankWidthUm", probe_radius_um * 2.0)), 4),
                "source": source,
                "brainBoundsDiagnostics": brain_mask_summary if constraint_modes.get("brainBounds") == "enforce" else None,
                "trajectoryProfile": {
                    k: profile[target_ids[k]]
                    for k in covered_keys
                    if target_ids[k] in profile
                },
            }
        except Exception:
            return None

    refined_rows = []
    if worker_count > 1 and len(chosen_rows) >= max(16, worker_count * 2):
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="cand-refine") as ex:
            for pos, cand in zip(range(1, len(chosen_rows) + 1), ex.map(_refine_eval, chosen_rows)):
                if cand is not None:
                    refined_rows.append(cand)
                    if candidate_sink is not None:
                        sink_buffer.append(cand)
                        _flush_sink(done=pos, total=len(chosen_rows), force=False)
                if pos % progress_tick == 0 or pos == len(chosen_rows):
                    _emit_progress(700.0 + 280.0 * (pos / max(1, len(chosen_rows))))
                    if cancel_cb is not None:
                        try:
                            cancel_cb()
                        except Exception:
                            pass
    else:
        for pos, row in enumerate(chosen_rows, start=1):
            cand = _refine_eval(row)
            if cand is not None:
                refined_rows.append(cand)
                if candidate_sink is not None:
                    sink_buffer.append(cand)
                    _flush_sink(done=pos, total=len(chosen_rows), force=False)
            if pos % progress_tick == 0 or pos == len(chosen_rows):
                _emit_progress(700.0 + 280.0 * (pos / max(1, len(chosen_rows))))
                if cancel_cb is not None:
                    try:
                        cancel_cb()
                    except Exception:
                        pass

    _flush_sink(done=len(chosen_rows), total=len(chosen_rows), force=True)
    refined_rows.sort(key=lambda c: int(c.get("_specIdx", 10**9)))
    candidates = []
    for cand_id, cand in enumerate(refined_rows):
        cand.pop("_specIdx", None)
        cand["id"] = int(cand_id)
        candidates.append(cand)
    refined_coverage_counts_by_target = {
        str(key): int(sum(1 for cand in candidates if key in (cand.get("coveredRegions") or [])))
        for key in target_ids
    }

    endpoint_policy = str(params.get("targetEndpointPolicy", "trajectory_intersects")).strip().lower()
    if endpoint_policy == "tip_in_target":
        trimmed_candidates = []
        seen_trimmed = set()
        for cand in candidates:
            if bool(params.get("depthOptimizationEnabled", True)):
                trimmed = _trim_candidate_to_target_tip(
                    cand,
                    target_ids=target_ids,
                    params=params,
                    constraint_modes=constraint_modes,
                    correction_factors=corr,
                    probe_radius_um=probe_radius_um,
                )
            else:
                endpoint = _candidate_endpoint_target_summary(cand, target_ids)
                trimmed = cand if bool(endpoint.get("tipInRequestedTarget")) else None
                if trimmed is not None:
                    trimmed["endpointTargeting"] = endpoint
            if trimmed is None:
                continue
            signature = _candidate_signature(trimmed)
            if signature in seen_trimmed:
                continue
            seen_trimmed.add(signature)
            trimmed_candidates.append(trimmed)
        candidates = trimmed_candidates
        for cand_id, cand in enumerate(candidates):
            cand["id"] = int(cand_id)
        candidate_gen_summary["targetEndpointPolicy"] = "tip_in_target"
        candidate_gen_summary["endpointTrimmedCount"] = int(len(candidates))
        candidate_gen_summary["depthOptimizationEnabled"] = bool(
            params.get("depthOptimizationEnabled", True)
        )
    else:
        candidate_gen_summary["targetEndpointPolicy"] = "trajectory_intersects"

    candidate_gen_summary["targetDiversitySeedsPerTarget"] = int(target_diversity_seeds)
    candidate_gen_summary["targetDiversityForcedSpecCount"] = int(
        sum(target_diversity_counts.values())
    )
    candidate_gen_summary["targetDiversitySeedCounts"] = {
        str(key): int(count) for key, count in target_diversity_counts.items()
    }
    candidate_gen_summary["targetSideDiversitySeedCounts"] = target_side_seed_counts
    candidate_gen_summary["targetEntryVariantsPerLocalEntry"] = int(target_entry_variants)
    candidate_gen_summary["targetEntryVariantForcedSpecCount"] = int(
        sum(target_entry_variant_counts.values())
    )
    candidate_gen_summary["targetEntryVariantSeedCounts"] = {
        str(key): int(count) for key, count in target_entry_variant_counts.items()
    }
    candidate_gen_summary["coarseAcceptedCountsBySource"] = {
        str(source): int(count) for source, count in coarse_accepted_counts_by_source.items()
    }
    candidate_gen_summary["coarseRejectReasonsBySource"] = _coarse_reject_by_source_snapshot()
    candidate_gen_summary["coarseCoverageCountsByTarget"] = coarse_coverage_counts_by_target
    candidate_gen_summary["coarseCoverageCountsByTargetAndSource"] = (
        coarse_coverage_counts_by_target_and_source
    )
    candidate_gen_summary["refinedCoverageCountsByTarget"] = refined_coverage_counts_by_target
    candidate_gen_summary["finalCoverageCountsByTarget"] = {
        str(key): int(sum(1 for cand in candidates if key in (cand.get("coveredRegions") or [])))
        for key in target_ids
    }

    log.info(
        f"Candidate pipeline: specs={len(deduped)} coarseKept={len(coarse_rows)} "
        f"refined={len(chosen_rows)} final={len(candidates)} workers={worker_count}"
    )
    candidate_gen_summary.update(
        {
            "workersUsed": int(worker_count),
            "coarseKept": int(len(coarse_rows)),
            "refineSelected": int(len(chosen_rows)),
            "finalCount": int(len(candidates)),
            "coarseStepUm": float(coarse_step_um),
            "refineStepUm": float(refine_step_um),
            "refineFraction": float(refine_fraction),
            "coarseRejectReasons": _coarse_reject_snapshot(),
            "coarseRejectReasonsBySource": _coarse_reject_by_source_snapshot(),
            "refineRejectReasons": _refine_reject_snapshot(),
        }
    )
    generate_candidates_voxel.last_summary = candidate_gen_summary
    log.info(f"    {len(candidates)}      ?   : {target_keys}")
    _emit_progress(1000.0)
    return candidates


generate_candidates_voxel.last_summary = {}


#        Greedy optimizer                                                                                                                     

def greedy_optimize(target_keys, candidates, min_clearance, min_entry):
    """Greedy set-cover with voxel-quality tie-breaking."""
    uncovered    = set(target_keys)
    selected     = []
    selected_set = set()

    while uncovered:
        best        = None
        best_n_new  = 0
        best_voxels = -1
        best_rank   = 10**9
        best_score  = -1.0
        best_tuple  = None

        for c in candidates:
            if id(c) in selected_set:
                continue
            if any(trajs_collide(c, s, min_clearance, min_entry) for s in selected):
                continue

            new_regions = [r for r in c["coveredRegions"] if r in uncovered]
            n_new = len(new_regions)
            if n_new == 0:
                continue

            ov     = c.get("overlapVoxels", {})
            voxels = sum(ov.get(r, 0) for r in new_regions)
            rank = int(c.get("paretoRank", 10**6))
            score = float(c.get("explainScore", 0.0))
            prefilter = c.get("riskPrefilter") if isinstance(c.get("riskPrefilter"), dict) else {}
            bucket = str(prefilter.get("bucket") or "unknown").strip().lower()
            bucket_priority = {"feasible": 2, "unknown": 1, "near": 0}.get(bucket, -1)
            risk_margin = float(prefilter.get("worstMarginMm", -1e9) or -1e9)
            if bool(c.get("objectiveScores", {}).get("referenceStabilityCost") is not None):
                # In stability mode the final weighted objective is the
                # tie-breaker among equal coverage/risk-rank candidates.
                score_tuple = (bucket_priority, n_new, -rank, score, voxels, risk_margin)
            else:
                score_tuple = (bucket_priority, n_new, -rank, voxels, score, risk_margin)

            if (
                best_tuple is None
                or score_tuple > best_tuple
                or (
                    score_tuple == best_tuple
                    and _candidate_signature(c) < _candidate_signature(best or {})
                )
            ):
                best_n_new  = n_new
                best_voxels = voxels
                best_rank = rank
                best_score = score
                best_tuple = score_tuple
                best        = c

        if best is None or best_n_new == 0:
            break

        selected.append(best)
        selected_set.add(id(best))
        for r in best["coveredRegions"]:
            uncovered.discard(r)

    return {"selected": selected, "uncovered": list(uncovered)}


#        Branch-and-Bound exact optimizer                                                                                   

def exact_optimize(target_keys, candidates, min_clearance, min_entry, time_limit_s=25):
    """
    Branch-and-Bound with LP-relaxation lower bounds.
    [FIX-3] Uses diversity_reduce_candidates() for pre-reduction.
    """
    import time as _time

    n = len(candidates)
    m = len(target_keys)

    if n == 0:
        return {"selected": [], "uncovered": list(target_keys),
                "milpStatus": "Optimal", "milpNodes": 0}

    BIG_M = n + 1

    cov_idx = [
        frozenset(i for i, k in enumerate(target_keys)
                  if k in candidates[j].get("coveredRegions", []))
        for j in range(n)
    ]
    cov_by = [
        [j for j in range(n) if i in cov_idx[j]]
        for i in range(m)
    ]

    conflicts = [set() for _ in range(n)]
    n_conflicts = 0
    for a in range(n):
        for b in range(a + 1, n):
            if trajs_collide(candidates[a], candidates[b], min_clearance, min_entry):
                conflicts[a].add(b)
                conflicts[b].add(a)
                n_conflicts += 1
    log.info(f"B&B model: {n} candidates, {m} targets, {n_conflicts} conflict pairs")

    def lp_bound(n_selected, excluded_set, covered_set):
        lb = float(n_selected)
        for i in range(m):
            if i in covered_set:
                continue
            k = sum(1 for j in cov_by[i] if j not in excluded_set)
            lb += BIG_M if k == 0 else 1.0 / k
        return lb

    order = sorted(
        range(n),
        key=lambda j: (
            _risk_prefilter_sort_key(candidates[j]),
            int(candidates[j].get("paretoRank", 10**6)),
            -(len(cov_idx[j]) / (1.0 + len(conflicts[j]))),
            -float(candidates[j].get("explainScore", 0.0)),
            _candidate_signature(candidates[j]),
        ),
    )

    t_start  = _time.time()
    best_obj = [float("inf")]
    best_sel = [None]
    nodes    = [0]

    def bb(sel_list, sel_set, excl_set, cov_set, o_pos):
        if _time.time() - t_start > time_limit_s:
            return
        nodes[0] += 1
        n_uncov  = m - len(cov_set)
        curr_obj = len(sel_list) + BIG_M * n_uncov
        if n_uncov == 0:
            if curr_obj < best_obj[0]:
                best_obj[0] = curr_obj
                best_sel[0] = list(sel_list)
            return
        if lp_bound(len(sel_list), excl_set, cov_set) >= best_obj[0]:
            return
        branch_j = -1
        while o_pos < n:
            j = order[o_pos]
            if j not in excl_set and j not in sel_set:
                branch_j = j
                break
            o_pos += 1
        if branch_j == -1:
            if curr_obj < best_obj[0]:
                best_obj[0] = curr_obj
                best_sel[0] = list(sel_list)
            return
        new_excl = excl_set | conflicts[branch_j]
        new_cov  = cov_set  | cov_idx[branch_j]
        sel_list.append(branch_j); sel_set.add(branch_j)
        bb(sel_list, sel_set, new_excl, new_cov, o_pos + 1)
        sel_list.pop(); sel_set.discard(branch_j)
        if _time.time() - t_start > time_limit_s:
            return
        excl_set.add(branch_j)
        bb(sel_list, sel_set, excl_set, cov_set, o_pos + 1)
        excl_set.discard(branch_j)

    bb([], set(), set(), frozenset(), 0)

    timed_out = _time.time() - t_start >= time_limit_s
    status    = "Feasible" if timed_out else "Optimal"

    if best_sel[0] is None:
        log.warning("B&B timed out with no solution; falling back to greedy")
        res = greedy_optimize(target_keys, candidates, min_clearance, min_entry)
        res["milpStatus"] = "FallbackGreedy"
        res["milpNodes"]  = nodes[0]
        return res

    selected  = [candidates[j] for j in best_sel[0]]
    cov_keys  = {k for c in selected for k in c.get("coveredRegions", [])}
    uncovered = [k for k in target_keys if k not in cov_keys]
    log.info(f"B&B done: {len(selected)} probes, {len(uncovered)} uncovered, "
             f"{nodes[0]} nodes, status={status}, "
             f"elapsed={_time.time()-t_start:.1f}s")
    return {"selected": selected, "uncovered": uncovered,
            "milpStatus": status, "milpNodes": nodes[0]}


def _summarize_selected_drcc(selected):
    if not selected:
        return {
            "selectedCount": 0,
            "meanWorstCaseViolationProb": None,
            "maxWorstCaseViolationProb": None,
            "meanMinSafetyDistanceMm": None,
            "meanRiskIntegralShortfallMm": None,
        }
    probs = []
    mins = []
    integrals = []
    for cand in selected:
        drcc = cand.get("drccMetrics") if isinstance(cand.get("drccMetrics"), dict) else {}
        wp = drcc.get("worstCaseViolationProb")
        md = drcc.get("minDistanceMm")
        ri = drcc.get("riskIntegralShortfallMm")
        if wp is not None:
            probs.append(float(wp))
        if md is not None:
            mins.append(float(md))
        if ri is not None:
            integrals.append(float(ri))
    return {
        "selectedCount": len(selected),
        "meanWorstCaseViolationProb": round(float(np.mean(probs)), 6) if probs else None,
        "maxWorstCaseViolationProb": round(float(np.max(probs)), 6) if probs else None,
        "meanMinSafetyDistanceMm": round(float(np.mean(mins)), 6) if mins else None,
        "meanRiskIntegralShortfallMm": round(float(np.mean(integrals)), 6) if integrals else None,
    }


def _drcc_profile_weights(profile_key):
    profiles = {
        "balanced": {"coverage": 0.50, "safety": 0.30, "angle": 0.20, "drcc": 0.75},
        "coverage_first": {"coverage": 0.72, "safety": 0.18, "angle": 0.10, "drcc": 0.45},
        "safety_first": {"coverage": 0.25, "safety": 0.60, "angle": 0.15, "drcc": 1.00},
        "angle_first": {"coverage": 0.30, "safety": 0.25, "angle": 0.45, "drcc": 0.55},
    }
    return profiles.get(profile_key, profiles["balanced"])


def _drcc_candidate_utility(candidate, uncovered, selected, min_clearance, min_entry, weights, strict):
    if any(trajs_collide(candidate, s, min_clearance, min_entry) for s in selected):
        return None
    new_regions = [r for r in candidate.get("coveredRegions", []) if r in uncovered]
    if not new_regions:
        return None
    scores = candidate.get("objectiveScores", {}) or {}
    drcc = candidate.get("drccMetrics") if isinstance(candidate.get("drccMetrics"), dict) else {}
    worst_p = float(drcc.get("worstCaseViolationProb", 1.0))
    feasible = bool(drcc.get("feasible", False))
    if strict and not feasible:
        return None
    utility = (
        len(new_regions) * 2.0
        + float(weights["coverage"]) * float(scores.get("coverage", 0.0))
        + float(weights["safety"]) * float(scores.get("safety", 0.0))
        + float(weights["angle"]) * float(scores.get("angle", 0.0))
        + float(weights["drcc"]) * (1.0 - worst_p)
    )
    # Penalize heavy shortfall tails to favor smoother risk profiles along the whole path.
    utility -= 0.4 * float(drcc.get("riskTailShortfallMm", 0.0) or 0.0)
    return utility


def _drcc_candidate_linear_cost(candidate, params, profile_key):
    """
    Linearized DRCC cost used by exact_pool B&B solver.
    Lower cost is better.
    """
    weights = _drcc_profile_weights(profile_key)
    scores = candidate.get("objectiveScores") if isinstance(candidate.get("objectiveScores"), dict) else {}
    drcc = candidate.get("drccMetrics") if isinstance(candidate.get("drccMetrics"), dict) else {}
    coverage = float(scores.get("coverage", 0.0))
    safety = float(scores.get("safety", 0.0))
    angle = float(scores.get("angle", 0.0))
    worst_p = float(drcc.get("worstCaseViolationProb", 1.0))
    feasible = bool(drcc.get("feasible", False))
    integral_shortfall = float(drcc.get("riskIntegralShortfallMm", 0.0) or 0.0)
    tail_shortfall = float(drcc.get("riskTailShortfallMm", 0.0) or 0.0)
    epsilon = max(1e-6, float(params.get("drccRiskEpsilon", DRCC_RISK_EPSILON_DEFAULT)))

    quality = (
        float(weights["coverage"]) * coverage
        + float(weights["safety"]) * safety
        + float(weights["angle"]) * angle
        + float(weights["drcc"]) * (1.0 - worst_p)
    )
    cost = (
        1.0
        - 0.42 * quality
        + 0.95 * worst_p
        + 0.16 * max(0.0, integral_shortfall)
        + 0.24 * max(0.0, tail_shortfall)
    )
    if worst_p > epsilon:
        cost += 0.8 * ((worst_p - epsilon) / epsilon)
    if not feasible:
        cost += 4.0 if bool(params.get("drccStrict", True)) else 1.5
    return max(0.02, float(cost))


def _resolve_cvxpy_mi_solver(preferred=None):
    if cp is None:
        return None
    try:
        installed = {str(item).upper() for item in cp.installed_solvers()}
    except Exception:
        installed = set()
    preferred_name = str(preferred or "").strip().upper()
    if preferred_name and preferred_name in installed:
        return preferred_name
    # Priority: commercial exact solvers first, then open-source fallback.
    for name in ("GUROBI", "MOSEK", "CPLEX", "SCIP", "ECOS_BB"):
        if name in installed:
            return name
    return None


def _drcc_misocp_optimize(
    target_keys,
    candidates,
    min_clearance,
    min_entry,
    params,
    profile_key,
):
    """
    Mixed-integer SOCP DRCC optimizer.
    - Decision vars: x_j (select candidate j), u_i (target i uncovered)
    - Constraints: coverage, pairwise collision exclusion
    - SOCP term: ||[sum(integral_shortfall*x), sum(tail_shortfall*x)]||_2
    """
    if not candidates:
        return {
            "selected": [],
            "uncovered": list(target_keys),
            "status": "Empty",
            "nodes": None,
            "poolSize": 0,
            "poolProvenOptimal": False,
            "objective": None,
            "solverUsed": None,
            "solverRequested": "misocp",
            "details": "No candidates available",
        }
    if cp is None:
        return {
            "selected": [],
            "uncovered": list(target_keys),
            "status": "SolverUnavailable",
            "nodes": None,
            "poolSize": 0,
            "poolProvenOptimal": False,
            "objective": None,
            "solverUsed": None,
            "solverRequested": "misocp",
            "details": "cvxpy is not installed",
        }

    strict = bool(params.get("drccStrict", True))
    max_cands = int(params.get("drccMisocpMaxCandidates", DRCC_MISOCP_MAX_CANDIDATES_DEFAULT))
    max_cands = max(8, min(max_cands, len(candidates)))
    time_limit_s = float(params.get("drccMisocpTimeLimitS", DRCC_MISOCP_TIME_LIMIT_S_DEFAULT))
    soc_weight = float(params.get("drccMisocpSocWeight", DRCC_MISOCP_SOC_WEIGHT_DEFAULT))

    ranked = sorted(
        candidates,
        key=lambda c: (
            _risk_prefilter_sort_key(c),
            0 if bool((c.get("drccMetrics") or {}).get("feasible", False)) else 1,
            int(c.get("paretoRank", 10**6)),
            float((c.get("drccMetrics") or {}).get("worstCaseViolationProb", 1.0)),
            -len(c.get("coveredRegions", []) or []),
            -float(c.get("explainScore", 0.0)),
            _candidate_signature(c),
        ),
    )
    pool = list(ranked[:max_cands])
    if strict:
        pool = [c for c in pool if bool((c.get("drccMetrics") or {}).get("feasible", False))]
        if not pool:
            return {
                "selected": [],
                "uncovered": list(target_keys),
                "status": "NoFeasibleInPool",
                "nodes": None,
                "poolSize": 0,
                "poolProvenOptimal": False,
                "objective": None,
                "solverUsed": None,
                "solverRequested": "misocp",
                "details": "Strict DRCC removed all candidates from MISOCP pool",
            }

    target_index = {k: i for i, k in enumerate(target_keys)}
    usable = []
    for cand in pool:
        covered = [target_index[k] for k in cand.get("coveredRegions", []) if k in target_index]
        if covered:
            usable.append((cand, frozenset(covered)))
    if not usable:
        return {
            "selected": [],
            "uncovered": list(target_keys),
            "status": "NoCoverageInPool",
            "nodes": None,
            "poolSize": 0,
            "poolProvenOptimal": False,
            "objective": None,
            "solverUsed": None,
            "solverRequested": "misocp",
            "details": "No candidate covers requested targets",
        }

    pool = [item[0] for item in usable]
    cov_idx = [item[1] for item in usable]
    n = len(pool)
    m = len(target_keys)
    costs = np.asarray([_drcc_candidate_linear_cost(c, params, profile_key) for c in pool], dtype=float)
    integral_vec = np.asarray(
        [
            float(((cand.get("drccMetrics") or {}).get("riskIntegralShortfallMm", 0.0)) or 0.0)
            for cand in pool
        ],
        dtype=float,
    )
    tail_vec = np.asarray(
        [
            float(((cand.get("drccMetrics") or {}).get("riskTailShortfallMm", 0.0)) or 0.0)
            for cand in pool
        ],
        dtype=float,
    )
    total_cost = float(np.sum(costs)) if costs.size else 0.0
    big_m = max(10.0, total_cost + 1.0)

    cover_by = [[j for j in range(n) if i in cov_idx[j]] for i in range(m)]
    conflict_pairs = []
    for a in range(n):
        for b in range(a + 1, n):
            if trajs_collide(pool[a], pool[b], min_clearance, min_entry):
                conflict_pairs.append((a, b))

    x = cp.Variable(n, boolean=True)
    u = cp.Variable(m, boolean=True)
    y_int = cp.Variable(nonneg=True)
    y_tail = cp.Variable(nonneg=True)
    z_soc = cp.Variable(nonneg=True)

    constraints = [
        y_int == integral_vec @ x,
        y_tail == tail_vec @ x,
        cp.norm(cp.hstack([y_int, y_tail]), 2) <= z_soc,
    ]
    for i in range(m):
        idx = cover_by[i]
        if idx:
            constraints.append(cp.sum(x[idx]) + u[i] >= 1)
        else:
            constraints.append(u[i] == 1)
    for a, b in conflict_pairs:
        constraints.append(x[a] + x[b] <= 1)

    objective = cp.Minimize(costs @ x + big_m * cp.sum(u) + max(0.0, soc_weight) * z_soc)
    problem = cp.Problem(objective, constraints)

    preferred = str(params.get("drccMisocpSolver", "")).strip().upper() or None
    solver_name = _resolve_cvxpy_mi_solver(preferred=preferred)
    if solver_name is None:
        return {
            "selected": [],
            "uncovered": list(target_keys),
            "status": "SolverUnavailable",
            "nodes": None,
            "poolSize": int(n),
            "poolProvenOptimal": False,
            "objective": None,
            "solverUsed": None,
            "solverRequested": "misocp",
            "details": "No mixed-integer solver available in cvxpy",
        }

    solve_kwargs = {"solver": solver_name}
    if solver_name == "GUROBI":
        solve_kwargs["TimeLimit"] = max(2.0, time_limit_s)
        solve_kwargs["MIPGap"] = 1e-4
    elif solver_name == "MOSEK":
        solve_kwargs["mosek_params"] = {"MSK_DPAR_MIO_MAX_TIME": max(2.0, time_limit_s)}
    elif solver_name == "CPLEX":
        solve_kwargs["cplex_params"] = {"timelimit": max(2.0, time_limit_s), "mip.tolerances.mipgap": 1e-4}
    elif solver_name == "SCIP":
        solve_kwargs["scip_params"] = {"limits/time": max(2.0, time_limit_s), "limits/gap": 1e-4}
    elif solver_name == "ECOS_BB":
        solve_kwargs["mi_max_iters"] = int(max(1000, min(250000, round(time_limit_s * 800.0))))

    try:
        problem.solve(**solve_kwargs)
    except Exception as exc:
        return {
            "selected": [],
            "uncovered": list(target_keys),
            "status": "SolveError",
            "nodes": None,
            "poolSize": int(n),
            "poolProvenOptimal": False,
            "objective": None,
            "solverUsed": solver_name,
            "solverRequested": "misocp",
            "details": f"{type(exc).__name__}: {exc}",
        }

    status = str(problem.status or "unknown")
    status_norm = status.lower()
    if status_norm not in ("optimal", "optimal_inaccurate"):
        return {
            "selected": [],
            "uncovered": list(target_keys),
            "status": status,
            "nodes": None,
            "poolSize": int(n),
            "poolProvenOptimal": False,
            "objective": None,
            "solverUsed": solver_name,
            "solverRequested": "misocp",
            "details": "MISOCP did not return a feasible optimum",
        }

    x_val = np.asarray(x.value if x.value is not None else np.zeros(n), dtype=float).reshape(-1)
    selected_idx = [i for i, v in enumerate(x_val.tolist()) if float(v) >= 0.5]
    selected = []
    for idx in selected_idx:
        cand = pool[idx]
        if any(trajs_collide(cand, s, min_clearance, min_entry) for s in selected):
            continue
        selected.append(cand)
    covered_keys = {k for c in selected for k in (c.get("coveredRegions", []) or [])}
    uncovered = [k for k in target_keys if k not in covered_keys]

    objective_value = float(problem.value) if problem.value is not None else None
    proven = status_norm == "optimal"
    return {
        "selected": selected,
        "uncovered": uncovered,
        "status": status,
        "nodes": None,
        "poolSize": int(n),
        "poolProvenOptimal": bool(proven),
        "objective": round(objective_value, 6) if objective_value is not None else None,
        "solverUsed": solver_name,
        "solverRequested": "misocp",
        "socNormValue": round(float(z_soc.value), 6) if z_soc.value is not None else None,
        "details": None,
    }


def _drcc_exact_pool_optimize(
    target_keys,
    candidates,
    min_clearance,
    min_entry,
    params,
    profile_key,
):
    """
    Exact (within reduced pool) DRCC optimizer by branch-and-bound.
    This gives a provable optimum for the selected pool and objective.
    """
    if not candidates:
        return {
            "selected": [],
            "uncovered": list(target_keys),
            "status": "Empty",
            "nodes": 0,
            "poolSize": 0,
            "poolProvenOptimal": True,
            "objective": None,
        }

    strict = bool(params.get("drccStrict", True))
    max_cands = int(params.get("drccExactMaxCandidates", DRCC_EXACT_MAX_CANDIDATES_DEFAULT))
    max_cands = max(8, min(max_cands, len(candidates)))
    time_limit_s = float(params.get("drccExactTimeLimitS", DRCC_EXACT_TIME_LIMIT_S_DEFAULT))

    ranked = sorted(
        candidates,
        key=lambda c: (
            _risk_prefilter_sort_key(c),
            0 if bool((c.get("drccMetrics") or {}).get("feasible", False)) else 1,
            int(c.get("paretoRank", 10**6)),
            float((c.get("drccMetrics") or {}).get("worstCaseViolationProb", 1.0)),
            -len(c.get("coveredRegions", []) or []),
            -float(c.get("explainScore", 0.0)),
            _candidate_signature(c),
        ),
    )
    pool = list(ranked[:max_cands])
    if strict:
        pool = [c for c in pool if bool((c.get("drccMetrics") or {}).get("feasible", False))]
        if not pool:
            return {
                "selected": [],
                "uncovered": list(target_keys),
                "status": "NoFeasibleInPool",
                "nodes": 0,
                "poolSize": 0,
                "poolProvenOptimal": True,
                "objective": None,
            }

    target_index = {k: i for i, k in enumerate(target_keys)}
    usable = []
    for cand in pool:
        cov = [target_index[k] for k in cand.get("coveredRegions", []) if k in target_index]
        if cov:
            usable.append((cand, frozenset(cov)))
    if not usable:
        return {
            "selected": [],
            "uncovered": list(target_keys),
            "status": "NoCoverageInPool",
            "nodes": 0,
            "poolSize": 0,
            "poolProvenOptimal": True,
            "objective": None,
        }

    pool = [x[0] for x in usable]
    cov_idx = [x[1] for x in usable]
    n = len(pool)
    m = len(target_keys)
    costs = [_drcc_candidate_linear_cost(c, params, profile_key) for c in pool]
    min_cost = min(costs) if costs else 0.05
    max_cov = max((len(c) for c in cov_idx), default=1)
    # Coverage-first objective: one uncovered target should always cost more than
    # any realistic in-pool cost improvement. This prevents "safe-but-miss-target"
    # solutions from dominating when target coverage is feasible.
    total_cost = float(sum(costs))
    big_m = max(10.0, total_cost + 1.0)

    cover_by = [[j for j in range(n) if i in cov_idx[j]] for i in range(m)]
    conflicts = [set() for _ in range(n)]
    for a in range(n):
        for b in range(a + 1, n):
            if trajs_collide(pool[a], pool[b], min_clearance, min_entry):
                conflicts[a].add(b)
                conflicts[b].add(a)

    order = sorted(
        range(n),
        key=lambda j: (
            -len(cov_idx[j]),
            costs[j],
            int(pool[j].get("paretoRank", 10**6)),
            _candidate_signature(pool[j]),
        ),
    )

    t_start = time.time()
    timed_out = [False]
    nodes = [0]
    best_obj = [float("inf")]
    best_sel = [None]

    def _is_target_coverable(target_i, selected_set, remaining_indices):
        for j in cover_by[target_i]:
            if j in selected_set or j in remaining_indices:
                if any((s in conflicts[j]) for s in selected_set):
                    continue
                return True
        return False

    def _branch(pos, selected_list, selected_set, covered_set, current_cost):
        if time.time() - t_start > time_limit_s:
            timed_out[0] = True
            return
        nodes[0] += 1

        uncovered_count = m - len(covered_set)
        current_obj = current_cost + big_m * uncovered_count

        if pos >= len(order):
            if current_obj < best_obj[0]:
                best_obj[0] = current_obj
                best_sel[0] = list(selected_list)
            return

        remaining_indices = set(order[pos:])
        lb = current_cost
        if uncovered_count > 0:
            lb += math.ceil(uncovered_count / max(1, max_cov)) * min_cost
            for ti in range(m):
                if ti in covered_set:
                    continue
                if not _is_target_coverable(ti, selected_set, remaining_indices):
                    lb += big_m
        if lb >= best_obj[0]:
            return

        j = order[pos]
        if not any((s in conflicts[j]) for s in selected_set):
            selected_set.add(j)
            selected_list.append(j)
            _branch(
                pos + 1,
                selected_list,
                selected_set,
                covered_set | cov_idx[j],
                current_cost + costs[j],
            )
            selected_list.pop()
            selected_set.discard(j)

        _branch(pos + 1, selected_list, selected_set, covered_set, current_cost)

    _branch(0, [], set(), frozenset(), 0.0)
    if best_sel[0] is None:
        return {
            "selected": [],
            "uncovered": list(target_keys),
            "status": "TimeoutNoSolution" if timed_out[0] else "NoSolution",
            "nodes": int(nodes[0]),
            "poolSize": int(n),
            "poolProvenOptimal": not bool(timed_out[0]),
            "objective": None,
        }

    selected = [pool[j] for j in best_sel[0]]
    covered_keys = {k for c in selected for k in (c.get("coveredRegions", []) or [])}
    uncovered = [k for k in target_keys if k not in covered_keys]
    return {
        "selected": selected,
        "uncovered": uncovered,
        "status": "Optimal" if not timed_out[0] else "Feasible",
        "nodes": int(nodes[0]),
        "poolSize": int(n),
        "poolProvenOptimal": not bool(timed_out[0]),
        "objective": round(float(best_obj[0]), 6),
    }


def _drcc_cvar_relaxation_summary(target_keys, candidates, params, profile_key):
    usable = []
    key_set = set(target_keys or [])
    for cand in candidates:
        covered = set(cand.get("coveredRegions", []) or []) & key_set
        if not covered:
            continue
        cost = float(_drcc_candidate_linear_cost(cand, params, profile_key))
        usable.append((cand, covered, cost))
    if not usable:
        return {
            "status": "empty",
            "selectedIds": [],
            "objective": None,
            "relaxedLowerBound": None,
            "coveredCount": 0,
            "targetCount": int(len(target_keys or [])),
        }

    # Optimistic lower bound from per-target best cost normalized by maximal coverage span.
    max_cover = max(1, max(len(covered) for _, covered, _ in usable))
    lb_terms = []
    for key in key_set:
        key_costs = [cost for _, covered, cost in usable if key in covered]
        if key_costs:
            lb_terms.append(min(key_costs))
    relaxed_lb = float(sum(lb_terms) / float(max_cover)) if lb_terms else None

    uncovered = set(key_set)
    selected = []
    selected_ids = []
    objective = 0.0
    while uncovered:
        best = None
        best_ratio = None
        for cand, covered, cost in usable:
            gain = len(covered & uncovered)
            if gain <= 0:
                continue
            ratio = float(cost) / float(gain)
            tie = (ratio, cost, int(cand.get("paretoRank", 10**6)), _candidate_signature(cand))
            if best_ratio is None or tie < best_ratio:
                best_ratio = tie
                best = (cand, covered, cost)
        if best is None:
            break
        cand, covered, cost = best
        selected.append(cand)
        selected_ids.append(cand.get("id"))
        objective += float(cost)
        uncovered -= covered

    return {
        "status": "ok",
        "selectedIds": selected_ids,
        "objective": round(float(objective), 6) if selected else None,
        "relaxedLowerBound": round(float(relaxed_lb), 6) if relaxed_lb is not None else None,
        "coveredCount": int(len(key_set) - len(uncovered)),
        "targetCount": int(len(key_set)),
    }


def _drcc_plan_from_selected_ids(selected_ids, candidates, target_keys, min_clearance, min_entry):
    id_order = []
    id_to_candidate = {}
    for cand in candidates:
        cid = cand.get("id")
        if cid is None:
            continue
        if cid not in id_to_candidate:
            id_to_candidate[cid] = cand
            id_order.append(cid)

    selected = []
    selected_seen = set()
    for cid in selected_ids or []:
        cand = id_to_candidate.get(cid)
        if cand is None:
            continue
        if cid in selected_seen:
            continue
        if any(trajs_collide(cand, s, min_clearance, min_entry) for s in selected):
            continue
        selected.append(cand)
        selected_seen.add(cid)

    uncovered = set(target_keys or [])
    for cand in selected:
        for reg in cand.get("coveredRegions", []) or []:
            uncovered.discard(reg)
    return {
        "selected": selected,
        "uncovered": sorted(uncovered),
        "candidateOrder": id_order,
    }


def _drcc_greedy_select(target_keys, candidates, min_clearance, min_entry, profile_key, strict=True):
    uncovered = set(target_keys)
    selected = []
    selected_ids = set()
    weights = _drcc_profile_weights(profile_key)

    while uncovered:
        best = None
        best_score = None
        for cand in candidates:
            cid = cand.get("id")
            if cid in selected_ids:
                continue
            new_regions = [r for r in cand.get("coveredRegions", []) if r in uncovered]
            if not new_regions:
                continue
            score = _drcc_candidate_utility(
                cand,
                uncovered=uncovered,
                selected=selected,
                min_clearance=min_clearance,
                min_entry=min_entry,
                weights=weights,
                strict=strict,
            )
            if score is None:
                continue
            drcc = cand.get("drccMetrics") if isinstance(cand.get("drccMetrics"), dict) else {}
            feasible = bool(drcc.get("feasible", False))
            min_dist = float(drcc.get("minDistanceMm", 0.0))
            cmp_tuple = (
                len(new_regions),
                float(score),
                1 if feasible else 0,
                -int(cand.get("paretoRank", 10**6)),
                -min_dist,
            )
            if best_score is None or cmp_tuple > best_score:
                best_score = cmp_tuple
                best = cand

        if best is None:
            # If strict mode fails to find a feasible covering candidate, allow the
            # least risky infeasible candidate so optimization can still progress.
            if strict:
                fallback = None
                fallback_tuple = None
                for cand in candidates:
                    cid = cand.get("id")
                    if cid in selected_ids:
                        continue
                    if any(trajs_collide(cand, s, min_clearance, min_entry) for s in selected):
                        continue
                    new_regions = [r for r in cand.get("coveredRegions", []) if r in uncovered]
                    if not new_regions:
                        continue
                    drcc = cand.get("drccMetrics") if isinstance(cand.get("drccMetrics"), dict) else {}
                    worst_p = float(drcc.get("worstCaseViolationProb", 1.0))
                    prefilter = cand.get("riskPrefilter") if isinstance(cand.get("riskPrefilter"), dict) else {}
                    bucket = str(prefilter.get("bucket") or "unknown").strip().lower()
                    bucket_rank = {"feasible": 0, "unknown": 1, "near": 2}.get(bucket, 3)
                    margin = float(prefilter.get("worstMarginMm", -1e9) or -1e9)
                    tup = (bucket_rank, -len(new_regions), worst_p, -margin, int(cand.get("paretoRank", 10**6)))
                    if fallback_tuple is None or tup < fallback_tuple:
                        fallback_tuple = tup
                        fallback = cand
                best = fallback
            if best is None:
                break

        selected.append(best)
        selected_ids.add(best.get("id"))
        for region in best.get("coveredRegions", []):
            uncovered.discard(region)

    return {"selected": selected, "uncovered": sorted(uncovered)}


def drcc_optimize(target_keys, candidates, min_clearance, min_entry, params):
    """
    Distributionally-Robust Chance-Constrained optimization over candidate trajectories.

    This keeps legacy greedy/exact/milp intact while adding a fourth method:
    - robust risk budget: sup_Q P_Q(d_min < tau) <= epsilon
    - SDF full-path penalty: integral + tail shortfall
    - preference-aware profile switch (coverage/safety/balanced/angle)
    """
    params = dict(params or {})
    if not candidates:
        return {
            "selected": [],
            "uncovered": list(target_keys),
            "milpStatus": "DRCC-Empty",
            "milpNodes": None,
            "drccSummary": {
                "epsilon": float(params.get("drccRiskEpsilon", DRCC_RISK_EPSILON_DEFAULT)),
                "wassersteinRadius": float(
                    params.get("drccWassersteinRadius", DRCC_WASSERSTEIN_RADIUS_DEFAULT)
                ),
                "evaluatedCandidates": 0,
                "profile": params.get("objectivePreset", "balanced"),
                "profiles": {},
            },
        }

    top_k = int(params.get("drccEvalTopK", DRCC_EVAL_TOPK_DEFAULT))
    top_k = max(20, min(top_k, len(candidates)))
    solver_mode = str(params.get("drccSolver", DRCC_SOLVER_DEFAULT)).strip().lower() or DRCC_SOLVER_DEFAULT
    if solver_mode not in ("heuristic", "exact_pool", "hybrid", "cvar_relax", "misocp"):
        solver_mode = DRCC_SOLVER_DEFAULT
    adaptive_budget = {
        "enabled": bool(params.get("drccAdaptiveBudget", DRCC_ADAPTIVE_BUDGET_DEFAULT)),
        "candidateCount": int(len(candidates)),
        "applied": False,
    }
    if adaptive_budget["enabled"] and len(candidates) > 0:
        n_cands = int(len(candidates))
        if n_cands >= 4000:
            scale = 0.32
        elif n_cands >= 2500:
            scale = 0.45
        elif n_cands >= 1500:
            scale = 0.60
        elif n_cands >= 900:
            scale = 0.78
        else:
            scale = 1.0
        if scale < 1.0:
            top_k = max(20, min(top_k, int(math.ceil(top_k * scale))))
            params["drccExactMaxCandidates"] = max(
                8,
                int(math.ceil(float(params.get("drccExactMaxCandidates", DRCC_EXACT_MAX_CANDIDATES_DEFAULT)) * scale)),
            )
            params["drccMisocpMaxCandidates"] = max(
                8,
                int(math.ceil(float(params.get("drccMisocpMaxCandidates", DRCC_MISOCP_MAX_CANDIDATES_DEFAULT)) * scale)),
            )
            params["drccExactTimeLimitS"] = max(
                2.0,
                float(params.get("drccExactTimeLimitS", DRCC_EXACT_TIME_LIMIT_S_DEFAULT)) * max(0.45, scale),
            )
            params["drccMisocpTimeLimitS"] = max(
                2.0,
                float(params.get("drccMisocpTimeLimitS", DRCC_MISOCP_TIME_LIMIT_S_DEFAULT)) * max(0.45, scale),
            )
            adaptive_budget.update(
                {
                    "applied": True,
                    "scale": round(float(scale), 4),
                    "drccEvalTopK": int(top_k),
                    "drccExactMaxCandidates": int(params["drccExactMaxCandidates"]),
                    "drccMisocpMaxCandidates": int(params["drccMisocpMaxCandidates"]),
                    "drccExactTimeLimitS": round(float(params["drccExactTimeLimitS"]), 3),
                    "drccMisocpTimeLimitS": round(float(params["drccMisocpTimeLimitS"]), 3),
                }
            )
        solver_before = solver_mode
        if n_cands >= 4200 and solver_mode in ("exact_pool", "hybrid"):
            solver_mode = "cvar_relax"
        elif n_cands >= 2600 and solver_mode == "exact_pool":
            solver_mode = "hybrid"
        elif n_cands <= 900 and solver_mode in ("heuristic", "hybrid"):
            solver_mode = "exact_pool"
        if solver_mode != solver_before:
            adaptive_budget["solverAdjusted"] = {
                "from": solver_before,
                "to": solver_mode,
            }

    ranked = sorted(
        candidates,
        key=lambda c: (
            _risk_prefilter_sort_key(c),
            int(c.get("paretoRank", 10**6)),
            -len(c.get("coveredRegions", []) or []),
            -float(c.get("explainScore", 0.0)),
            _candidate_signature(c),
        ),
    )
    # Keep pool diversity by forcing per-target representation before truncation.
    per_target_keep = max(2, min(10, int(params.get("drccPerTargetKeep", 6))))
    forced = []
    seen_forced = set()
    for key in target_keys:
        kept = 0
        for cand in ranked:
            covered = cand.get("coveredRegions", []) or []
            if key not in covered:
                continue
            sig = _candidate_signature(cand)
            if sig in seen_forced:
                continue
            forced.append(cand)
            seen_forced.add(sig)
            kept += 1
            if kept >= per_target_keep:
                break

    eval_limit = max(top_k, len(forced), len(target_keys) * 4)
    evaluated = []
    seen_eval = set()
    for cand in forced + ranked:
        sig = _candidate_signature(cand)
        if sig in seen_eval:
            continue
        evaluated.append(cand)
        seen_eval.add(sig)
        if len(evaluated) >= eval_limit:
            break

    for cand in evaluated:
        if not isinstance(cand.get("drccMetrics"), dict):
            cand["drccMetrics"] = _candidate_drcc_metrics(cand, params)
            cand["drccFeasible"] = bool(cand["drccMetrics"].get("feasible", False))
            cand["drccWorstCaseViolationProb"] = float(
                cand["drccMetrics"].get("worstCaseViolationProb", 1.0)
            )

    evaluated_cover = {k: False for k in target_keys}
    for cand in evaluated:
        for key in cand.get("coveredRegions", []) or []:
            if key in evaluated_cover:
                evaluated_cover[key] = True
    pool_missing_targets = sorted([k for k, ok in evaluated_cover.items() if not ok])

    profiles = ["balanced", "coverage_first", "safety_first", "angle_first"]
    strict = bool(params.get("drccStrict", True))
    profile_results = {}
    for profile in profiles:
        plan = _drcc_greedy_select(
            target_keys=target_keys,
            candidates=evaluated,
            min_clearance=min_clearance,
            min_entry=min_entry,
            profile_key=profile,
            strict=strict,
        )
        profile_results[profile] = {
            **plan,
            "summary": _summarize_selected_drcc(plan.get("selected", [])),
        }

    preferred_profile = str(params.get("objectivePreset", "balanced"))
    if preferred_profile not in profile_results:
        preferred_profile = "balanced"
    chosen = profile_results[preferred_profile]

    exact_plan = None
    misocp_plan = None
    if solver_mode in ("exact_pool", "hybrid"):
        exact_plan = _drcc_exact_pool_optimize(
            target_keys=target_keys,
            candidates=evaluated,
            min_clearance=min_clearance,
            min_entry=min_entry,
            params=params,
            profile_key=preferred_profile,
        )
        profile_results["exact_pool"] = {
            "selected": list(exact_plan.get("selected", [])),
            "uncovered": list(exact_plan.get("uncovered", [])),
            "summary": _summarize_selected_drcc(exact_plan.get("selected", [])),
            "status": exact_plan.get("status"),
            "poolProvenOptimal": exact_plan.get("poolProvenOptimal"),
            "nodes": exact_plan.get("nodes"),
            "objective": exact_plan.get("objective"),
        }
        if solver_mode == "exact_pool":
            chosen = profile_results["exact_pool"]
        elif solver_mode == "hybrid":
            exact_selected = profile_results["exact_pool"].get("selected", [])
            if exact_selected:
                chosen = profile_results["exact_pool"]

    if solver_mode == "misocp":
        misocp_plan = _drcc_misocp_optimize(
            target_keys=target_keys,
            candidates=evaluated,
            min_clearance=min_clearance,
            min_entry=min_entry,
            params=params,
            profile_key=preferred_profile,
        )
        profile_results["misocp"] = {
            "selected": list((misocp_plan or {}).get("selected", [])),
            "uncovered": list((misocp_plan or {}).get("uncovered", [])),
            "summary": _summarize_selected_drcc((misocp_plan or {}).get("selected", [])),
            "status": (misocp_plan or {}).get("status"),
            "poolProvenOptimal": bool((misocp_plan or {}).get("poolProvenOptimal", False)),
            "poolSize": int((misocp_plan or {}).get("poolSize", 0) or 0),
            "objective": (misocp_plan or {}).get("objective"),
            "solverUsed": (misocp_plan or {}).get("solverUsed"),
            "details": (misocp_plan or {}).get("details"),
        }
        if profile_results["misocp"].get("selected"):
            chosen = profile_results["misocp"]

    cvar_relaxation = _drcc_cvar_relaxation_summary(
        target_keys=target_keys,
        candidates=evaluated,
        params=params,
        profile_key=preferred_profile,
    )
    cvar_plan = _drcc_plan_from_selected_ids(
        selected_ids=cvar_relaxation.get("selectedIds") if isinstance(cvar_relaxation, dict) else [],
        candidates=evaluated,
        target_keys=target_keys,
        min_clearance=min_clearance,
        min_entry=min_entry,
    )
    profile_results["cvar_relax"] = {
        "selected": list(cvar_plan.get("selected", [])),
        "uncovered": list(cvar_plan.get("uncovered", [])),
        "summary": _summarize_selected_drcc(cvar_plan.get("selected", [])),
    }
    if isinstance(cvar_relaxation, dict):
        cvar_relaxation["postCollisionSelectedCount"] = int(len(cvar_plan.get("selected", [])))
        cvar_relaxation["postCollisionUncoveredCount"] = int(len(cvar_plan.get("uncovered", [])))
        cvar_relaxation["postCollisionUncovered"] = list(cvar_plan.get("uncovered", []))
    if solver_mode == "cvar_relax" and cvar_plan.get("selected"):
        chosen = profile_results["cvar_relax"]

    chosen_linear_objective = (
        float(
            sum(
                _drcc_candidate_linear_cost(cand, params, preferred_profile)
                for cand in (chosen.get("selected") or [])
            )
        )
        if isinstance(chosen, dict)
        else None
    )
    exact_objective = (exact_plan or {}).get("objective") if isinstance(exact_plan, dict) else None
    optimality_gap_exact = None
    if chosen_linear_objective is not None and exact_objective not in (None, ""):
        denom = max(1e-9, abs(float(exact_objective)))
        optimality_gap_exact = max(0.0, (float(chosen_linear_objective) - float(exact_objective)) / denom)
    relaxed_lb = cvar_relaxation.get("relaxedLowerBound") if isinstance(cvar_relaxation, dict) else None
    optimality_gap_relaxed = None
    if chosen_linear_objective is not None and relaxed_lb not in (None, ""):
        denom = max(1e-9, abs(float(relaxed_lb)))
        optimality_gap_relaxed = max(0.0, (float(chosen_linear_objective) - float(relaxed_lb)) / denom)

    solver_selected = "exact_pool" if chosen is profile_results.get("exact_pool") else "heuristic"
    if solver_mode == "misocp":
        solver_selected = (
            "misocp"
            if chosen is profile_results.get("misocp")
            else "misocp_fallback_heuristic"
        )
    elif solver_mode == "cvar_relax":
        solver_selected = (
            "cvar_relax"
            if chosen is profile_results.get("cvar_relax")
            else "cvar_relax_fallback_heuristic"
        )

    return {
        "selected": chosen["selected"],
        "uncovered": chosen["uncovered"],
        "milpStatus": (
            "DRCC-ExactPool-Optimal"
            if chosen is profile_results.get("exact_pool") and str((exact_plan or {}).get("status")) == "Optimal"
            else "DRCC-ExactPool-Feasible"
            if chosen is profile_results.get("exact_pool")
            else "DRCC-MISOCP-Optimal"
            if chosen is profile_results.get("misocp")
            and str((misocp_plan or {}).get("status", "")).lower() == "optimal"
            else "DRCC-MISOCP-Feasible"
            if chosen is profile_results.get("misocp")
            else "DRCC-CVaR-Relax"
            if chosen is profile_results.get("cvar_relax")
            else "DRCC-Heuristic"
        ),
        "milpNodes": (
            int((exact_plan or {}).get("nodes", 0))
            if chosen is profile_results.get("exact_pool")
            else None
        ),
        "drccSummary": {
            "epsilon": round(float(params.get("drccRiskEpsilon", DRCC_RISK_EPSILON_DEFAULT)), 6),
            "wassersteinRadius": round(
                float(params.get("drccWassersteinRadius", DRCC_WASSERSTEIN_RADIUS_DEFAULT)), 6
            ),
            "evaluatedCandidates": int(len(evaluated)),
            "forcedCandidates": int(len(forced)),
            "poolMissingTargets": pool_missing_targets,
            "strict": strict,
            "profile": preferred_profile,
            "solver": solver_mode,
            "adaptiveBudget": adaptive_budget,
            "solverSelected": solver_selected,
            "chosenLinearObjective": round(float(chosen_linear_objective), 6)
            if chosen_linear_objective is not None
            else None,
            "optimalityGapVsExactPool": round(float(optimality_gap_exact), 6)
            if optimality_gap_exact is not None
            else None,
            "optimalityGapVsRelaxedBound": round(float(optimality_gap_relaxed), 6)
            if optimality_gap_relaxed is not None
            else None,
            "cvarRelaxation": cvar_relaxation,
            "misocp": {
                "status": (misocp_plan or {}).get("status"),
                "poolProvenOptimal": bool((misocp_plan or {}).get("poolProvenOptimal", False)),
                "poolSize": int((misocp_plan or {}).get("poolSize", 0) or 0),
                "objective": (misocp_plan or {}).get("objective"),
                "solverUsed": (misocp_plan or {}).get("solverUsed"),
                "details": (misocp_plan or {}).get("details"),
                "socNormValue": (misocp_plan or {}).get("socNormValue"),
            } if misocp_plan is not None else None,
            "exactPool": {
                "status": (exact_plan or {}).get("status"),
                "poolProvenOptimal": bool((exact_plan or {}).get("poolProvenOptimal", False)),
                "poolSize": int((exact_plan or {}).get("poolSize", 0) or 0),
                "nodes": int((exact_plan or {}).get("nodes", 0) or 0),
                "objective": (exact_plan or {}).get("objective"),
            } if exact_plan is not None else None,
            "profiles": {
                key: {
                    "selectedCount": int(len(value.get("selected", []))),
                    "uncoveredCount": int(len(value.get("uncovered", []))),
                    **(value.get("summary") or {}),
                }
                for key, value in profile_results.items()
            },
        },
        "preferenceProfiles": {
            key: {
                "selectedIds": [cand.get("id") for cand in value.get("selected", [])],
                "uncovered": list(value.get("uncovered", [])),
                **(value.get("summary") or {}),
            }
            for key, value in profile_results.items()
        },
    }


def coverage_boost_voxel(
    selected,
    uncovered_keys,
    target_keys,
    params,
    probe_params,
    constraint_modes,
    correction_factors,
):
    """
    Greedy coverage repair for residual uncovered targets.
    Tries local entry offsets + angle perturbations around uncovered target centroids.
    """
    if not uncovered_keys:
        return {"selected": selected, "uncovered": []}

    corr = correction_factors or DEFAULT_CORRECTION_FACTORS
    depth_um = float(params.get("depthUm", probe_params.get("depthUm", 10000)))
    skull_tilt = float(params.get("skullTiltDeg", 0.0))
    max_angle = int(max(0, math.floor(float(params.get("maxAngle", 15)))))
    min_clearance = float(params.get("minClearance", 1000))
    min_entry = float(probe_params.get("entrySpacingUm", 3500))
    probe_radius_um = max(
        5.0,
        float(probe_params.get("probeRadiusUm", probe_params.get("shankWidthUm", 70) / 2.0)),
    )
    coverage_radius = float(params.get("coverageRadius", 600))
    target_ids = {k: PLANNER_REGIONS[k]["id"] for k in target_keys if k in PLANNER_REGIONS}

    angles = [(0, 0)]
    for th in range(3, max_angle + 1, 3):
        for ph in range(0, 360, 30):
            angles.append((th, ph))
    offsets = [
        (0.0, 0.0),
        (0.2, 0.0), (-0.2, 0.0), (0.0, 0.2), (0.0, -0.2),
        (0.4, 0.0), (-0.4, 0.0), (0.0, 0.4), (0.0, -0.4),
        (0.3, 0.3), (-0.3, -0.3), (0.5, 0.0), (0.0, 0.5),
    ]

    boosted = list(selected)
    remaining = list(uncovered_keys)

    for region_key in list(uncovered_keys):
        if region_key not in remaining:
            continue
        reg = PLANNER_REGIONS.get(region_key)
        if reg is None:
            continue

        ap_target_raw = float(reg["ap"])
        ml_target_raw = float(reg["ml"])
        best_candidate = None
        best_score = (-1, -1.0, -1.0)

        for dap, dml in offsets:
            ap_raw = round(ap_target_raw + dap, 3)
            ml_raw = round(ml_target_raw + dml, 3)

            for theta, phi in angles:
                ap_corr = ap_raw * corr["ap"] + corr["apShift"]
                ml_corr = ml_raw * corr["ml"] + corr["mlShift"]
                dep_corr = depth_um * corr["dv"]

                if constraint_modes.get("lambdaBoundary") == "enforce" and ap_corr < LAMBDA_AP_MM:
                    continue

                entry, tip, direction, dv_corr = make_trajectory_xyz(
                    ap_corr, ml_corr, theta, phi, dep_corr, skull_tilt,
                    entry_dv_corr_mm=_corrected_entry_dv_mm(ap_raw, skull_tilt, corr)
                )

                if constraint_modes.get("brainBounds") == "enforce":
                    tip_ap, tip_dv = _tip_ap_dv_mm(ap_corr, dv_corr, direction, dep_corr)
                    if ap_corr < ENTRY_AP_MIN_MM or ap_corr > ENTRY_AP_MAX_MM:
                        continue
                    if tip_ap < TIP_POST_LIMIT_MM or tip_dv > MAX_DV_MM:
                        continue

                traj_voxels = sample_trajectory_voxels_cached(
                    ap_corr, ml_corr, theta, phi, dep_corr, skull_tilt, step_um=25.0,
                    entry_dv_corr_mm=dv_corr
                )
                if constraint_modes.get("brainBounds") == "enforce":
                    brain_mask_summary = _brain_mask_path_summary_from_voxels(
                        traj_voxels,
                        step_um=25.0,
                    )
                    if brain_mask_summary.get("available") and not brain_mask_summary.get("passed", True):
                        continue
                profile = compute_trajectory_profile(
                    traj_voxels, list(target_ids.values()), step_um=25.0
                )

                covered_keys = [
                    k for k, sid in target_ids.items() if profile.get(sid, {}).get("covered", False)
                ]
                entry_um_arr = np.array(entry, dtype=float)
                tip_um_arr = np.array(tip, dtype=float)
                for k, sid in target_ids.items():
                    if k in covered_keys:
                        continue
                    if structure_map.get(sid, {}).get("voxel_count", 0) != 0:
                        continue
                    dist = pt_seg_dist_um(np.array(REGION_CENTROIDS_UM[sid]), entry_um_arr, tip_um_arr)
                    if dist > coverage_radius:
                        continue
                    covered_keys.append(k)
                    fallback_side = "right" if ml_corr > 0 else "left"
                    profile[sid] = {
                        "covered": True,
                        "n_voxels": 0,
                        "coverage_frac": 0.0,
                        "entry_depth_um": None,
                        "exit_depth_um": None,
                        "channel_span_um": 0,
                        "voxel_score": 0.0,
                        "left_voxels": 1 if fallback_side == "left" else 0,
                        "right_voxels": 1 if fallback_side == "right" else 0,
                        "coveredSides": [fallback_side],
                    }

                if region_key not in covered_keys:
                    continue

                overlap_counts = {k: profile[target_ids[k]].get("n_voxels", 0) for k in covered_keys}
                covered_region_sides = {
                    k: profile[target_ids[k]].get("coveredSides", [])
                    for k in covered_keys
                    if target_ids[k] in profile
                }
                candidate = {
                    "id": f"boost_{region_key}_{len(boosted)}_{theta}_{phi}",
                    "ap_mm": ap_raw,
                    "ml_mm": ml_raw,
                    "depthUm": depth_um,
                    "ap_mm_corrected": round(ap_corr, 5),
                    "ml_mm_corrected": round(ml_corr, 5),
                    "depthUm_corrected": round(dep_corr, 2),
                    "theta_deg": theta,
                    "phi_deg": phi,
                    "skullTiltDeg": skull_tilt,
                    "entryDvCorr_mm": round(_skull_entry_dv_mm(ap_raw, skull_tilt), 5),
                    "entryDvCorr_mm_corrected": round(dv_corr, 5),
                    "entry_xyz": [round(x, 1) for x in entry],
                    "tip_xyz": [round(x, 1) for x in tip],
                    "dir": [round(x, 6) for x in direction],
                    "coveredRegions": covered_keys,
                    "coveredRegionSides": covered_region_sides,
                    "overlapVoxels": overlap_counts,
                    "nVoxelsTotal": len(traj_voxels),
                    "radiusUm": round(probe_radius_um, 4),
                    "source": f"boost_{region_key}",
                    "trajectoryProfile": {
                        k: profile[target_ids[k]] for k in covered_keys if target_ids[k] in profile
                    },
                }

                if any(trajs_collide(candidate, s, min_clearance, min_entry) for s in boosted):
                    continue
                if constraint_modes.get("hemisphere") == "enforce" and has_hemisphere_mismatch(candidate):
                    continue

                _score_candidates([candidate], target_keys, params)
                gain = len(set(covered_keys) & set(remaining))
                rank_pref = -int(candidate.get("paretoRank", 10**6))
                explain_score = float(candidate.get("explainScore", 0.0))
                score_tuple = (gain, explain_score, rank_pref)
                if score_tuple > best_score:
                    best_candidate = candidate
                    best_score = score_tuple

        if best_candidate is not None:
            boosted.append(best_candidate)
            covered_now = set(best_candidate.get("coveredRegions", []) or [])
            remaining = [k for k in remaining if k not in covered_now]

    return {"selected": boosted, "uncovered": remaining}


#        Flask routes                                                                                                                               

class ContractError(Exception):
    def __init__(self, message, details=None, status_code=400, code="validation_error", extra=None):
        super().__init__(message)
        self.message = message
        self.details = details or []
        self.status_code = status_code
        self.code = code
        self.extra = extra if isinstance(extra, dict) else {}


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _request_id():
    return getattr(g, "request_id", None) or uuid.uuid4().hex


def _resolve_task_id(raw_body):
    if not isinstance(raw_body, dict):
        return _request_id()
    for key in ("taskId", "requestId", "jobId"):
        value = raw_body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _request_id()


def _check_task_cancelled(task_id, stage):
    if not task_id:
        return
    if task_registry.is_cancelled(task_id):
        raise ContractError(
            f"Task cancelled at stage: {stage}",
            status_code=409,
            code="task_cancelled",
            details=[f"taskId={task_id}", f"stage={stage}"],
        )


def _update_task_progress(task_id, stage, progress, message="", status="running"):
    if not task_id:
        return
    try:
        task_registry.update(
            task_id,
            status=status,
            stage=str(stage),
            progress=float(progress),
            message=str(message or ""),
        )
    except Exception:
        # Progress reporting must never break optimization flow.
        pass
    try:
        _set_planning_runtime_state(
            status=status,
            stage=stage,
            progress=progress,
            message=message,
            task_id=task_id,
        )
    except Exception:
        # Unified workflow state is best effort and must not break main task flow.
        pass


def _extract_bearer_token(header_value):
    if not header_value:
        return None
    parts = header_value.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


def _json_error(message, status=400, code="validation_error", details=None, extra=None):
    payload = {
        "ok": False,
        "error": message,
        "code": code,
        "details": details or [],
        "requestId": _request_id(),
        "engineVersion": ENGINE_VERSION,
    }
    if extra:
        payload.update(extra)
    return jsonify(payload), status


def _api_log_context(context=None):
    if context:
        return str(context)
    try:
        return f"{request.method} {request.path}"
    except RuntimeError:
        return "request"


def _safe_internal_error_message(context="request"):
    label = str(context or "request").strip() or "request"
    return f"Unexpected {label} failure. See backend logs with requestId={_request_id()}."


def _safe_task_internal_error_message():
    return f"Task failed internally. See backend logs with requestId={_request_id()}."


def _json_bad_request(exc=None, message="Invalid JSON request body", details=None):
    detail_items = list(details or [])
    if exc is not None:
        description = str(getattr(exc, "description", "") or "").strip()
        if description and description not in detail_items:
            detail_items.append(description)
    return _json_error(
        message,
        status=400,
        code="invalid_request_json",
        details=detail_items or ["Request body must be valid JSON."],
    )


def _request_json_object(required=False):
    if not required:
        raw = request.get_data(cache=True)
        if not raw and not request.is_json:
            return {}
        if raw and not request.is_json:
            raise ContractError(
                "Request body must be JSON",
                status_code=400,
                code="invalid_request_json",
                details=["Set Content-Type: application/json or send an empty body."],
            )
    body = request.get_json(force=required, silent=False)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ContractError(
            "Request body must be a JSON object",
            status_code=400,
            code="invalid_request_json",
            details=["Top-level JSON value must be an object."],
        )
    return body


def _parse_int_query_arg(name, default=None, min_value=None, max_value=None):
    raw = request.args.get(name)
    if raw is None or str(raw).strip() == "":
        if default is None:
            raise ContractError(
                f"Missing query parameter: {name}",
                code="invalid_query_param",
                details=[f"{name} must be an integer."],
            )
        value = default
    else:
        try:
            value = int(str(raw).strip())
        except Exception:
            raise ContractError(
                f"Invalid query parameter: {name}",
                code="invalid_query_param",
                details=[f"{name} must be an integer."],
            )

    if min_value is not None and value < min_value:
        raise ContractError(
            f"Invalid query parameter: {name}",
            code="invalid_query_param",
            details=[f"{name} must be at least {min_value}."],
        )
    if max_value is not None and value > max_value:
        raise ContractError(
            f"Invalid query parameter: {name}",
            code="invalid_query_param",
            details=[f"{name} must be at most {max_value}."],
        )
    return value


def _json_internal_error(
    exc=None,
    context=None,
    details=None,
    message="Internal server error",
    code="internal_error",
):
    if exc is not None:
        log.exception(
            "%s failed requestId=%s: %s",
            _api_log_context(context),
            _request_id(),
            exc,
        )
    return _json_error(
        message,
        status=500,
        code=code,
        details=list(details or ["Inspect backend logs using the response requestId."]),
    )


def _json_contract_error(exc, extra=None):
    merged_extra = deepcopy(getattr(exc, "extra", {}) or {})
    if isinstance(extra, dict):
        merged_extra.update(extra)
    return _json_error(
        exc.message,
        status=exc.status_code,
        code=exc.code,
        details=exc.details,
        extra=merged_extra,
    )


def _public_task_state_snapshot(state):
    if not isinstance(state, dict):
        return state
    out = deepcopy(state)
    out["status"] = normalize_task_status(out.get("status"), TASK_STATE_QUEUED)
    result = out.get("result") if isinstance(out.get("result"), dict) else None
    code = str((result or {}).get("code") or out.get("code") or "").strip()
    if out["status"] == TASK_STATE_FAILED and code == "internal_error":
        safe_message = _safe_task_internal_error_message()
        out["message"] = safe_message
        out["details"] = [safe_message]
        if result is not None:
            redacted = deepcopy(result)
            redacted["error"] = safe_message
            redacted["details"] = [safe_message]
            out["result"] = redacted
    return out


def _json_upstream_error(message, status, code, context=None):
    log.warning(
        "%s upstream failure requestId=%s: %s",
        _api_log_context(context),
        _request_id(),
        message,
    )
    return _json_error(
        "Upstream service error",
        status=status,
        code=code,
        details=["Inspect backend logs using the response requestId."],
    )


def _public_atlas_error(error_text=None):
    if not str(error_text or "").strip():
        return ""
    return "Atlas loading failed. Inspect backend logs using the response requestId."


def _public_atlas_load_snapshot(load_snapshot, error_text=None):
    payload = deepcopy(load_snapshot) if isinstance(load_snapshot, dict) else {}
    if str(error_text or "").strip():
        payload["state"] = "error"
        payload["phase"] = payload.get("phase") or "error"
        payload["message"] = _public_atlas_error(error_text)
    return payload


def _json_atlas_not_ready(load_snapshot=None, context="Atlas not ready"):
    if atlas_error:
        log.warning("%s requestId=%s: %s", context, _request_id(), atlas_error)
    load = load_snapshot if isinstance(load_snapshot, dict) else {}
    phase = str(load.get("phase") or load.get("state") or "loading")
    progress = float(load.get("progress") or 0.0)
    return _json_error(
        "Atlas not loaded yet, please wait and retry.",
        status=503,
        code="atlas_not_ready",
        details=[f"Atlas loading phase={phase}, progress={progress:.3f}"],
    )


@app.errorhandler(ContractError)
def _handle_contract_error(exc):
    return _json_contract_error(exc)


@app.errorhandler(BadRequest)
def _handle_bad_request(exc):
    return _json_bad_request(exc)


@app.errorhandler(HTTPException)
def _handle_http_exception(exc):
    status = int(getattr(exc, "code", 500) or 500)
    name = str(getattr(exc, "name", "") or "HTTP error").strip()
    code = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "http_error"
    public_messages = {
        404: "Resource not found",
        405: "Method not allowed",
        413: "Request body too large",
        415: "Unsupported media type",
    }
    return _json_error(public_messages.get(status, name), status=status, code=code)


@app.errorhandler(Exception)
def _handle_unexpected_exception(exc):
    return _json_internal_error(exc, context="Unhandled request")


def _format_contract_error_for_task(exc, max_items=3, max_chars=420):
    """
    Build a concise task-facing message from ContractError while preserving key details.
    Task polling currently consumes task.message only, so we include top detail items here
    to avoid opaque 'blocked optimization' UI messages.
    """
    base = str(getattr(exc, "message", "") or getattr(exc, "code", "") or "Contract error").strip()
    details = getattr(exc, "details", None)
    if not isinstance(details, (list, tuple)):
        return base
    parts = [str(x).strip() for x in details if str(x).strip()]
    if not parts:
        return base
    preview = "; ".join(parts[: max(1, int(max_items))])
    msg = f"{base} ({preview})"
    if len(msg) <= int(max_chars):
        return msg
    return f"{msg[: max(0, int(max_chars) - 1)].rstrip()}…"


def _coerce_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
    return bool(value)


def _require_dict(name, value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ContractError(f"{name} must be an object")
    return value


def _require_number(name, value, minimum=None, maximum=None, integer=False):
    if isinstance(value, bool):
        raise ContractError(f"{name} must be a number")
    try:
        number = int(value) if integer else float(value)
    except Exception as exc:
        raise ContractError(f"{name} must be a number") from exc
    if not math.isfinite(number):
        raise ContractError(f"{name} must be finite")
    if minimum is not None and number < minimum:
        raise ContractError(f"{name} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise ContractError(f"{name} must be <= {maximum}")
    return number


def _normalize_short_text(name, value, *, maximum=120):
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple)):
        raise ContractError(f"{name} must be a string")
    text = str(value).strip()
    if not text:
        return None
    if len(text) > maximum:
        raise ContractError(f"{name} must be <= {maximum} chars")
    return text


def _normalize_clinical_context(raw_context):
    raw = _require_dict("clinicalContext", raw_context or {})
    intended_use = str(
        raw.get("intendedUse", CLINICAL_INTENDED_USE_LOCK)
    ).strip().lower() or CLINICAL_INTENDED_USE_LOCK
    species = str(raw.get("species", CLINICAL_SPECIES_LOCK)).strip().lower() or CLINICAL_SPECIES_LOCK
    if intended_use != CLINICAL_INTENDED_USE_LOCK:
        raise ContractError(
            f"clinicalContext.intendedUse must be '{CLINICAL_INTENDED_USE_LOCK}' for this build"
        )
    if species != CLINICAL_SPECIES_LOCK:
        raise ContractError(
            f"clinicalContext.species must be '{CLINICAL_SPECIES_LOCK}' (current atlas scope)"
        )

    study_arm = str(raw.get("studyArm", "unspecified")).strip().lower() or "unspecified"
    if study_arm not in CLINICAL_STUDY_ARMS:
        raise ContractError(
            "clinicalContext.studyArm must be one of "
            + "/".join(CLINICAL_STUDY_ARMS)
        )

    out = {
        "intendedUse": intended_use,
        "species": species,
        "studyArm": study_arm,
        "caseId": _normalize_short_text("clinicalContext.caseId", raw.get("caseId"), maximum=80),
        "hospitalId": _normalize_short_text("clinicalContext.hospitalId", raw.get("hospitalId"), maximum=80),
        "deviceModel": _normalize_short_text("clinicalContext.deviceModel", raw.get("deviceModel"), maximum=80),
        "batchId": _normalize_short_text(
            "clinicalContext.batchId",
            raw.get("batchId", raw.get("experimentBatch")),
            maximum=80,
        ),
        "operatorId": _normalize_short_text("clinicalContext.operatorId", raw.get("operatorId"), maximum=80),
    }
    if "expertAcceptanceRate" in raw and raw.get("expertAcceptanceRate") not in (None, ""):
        out["expertAcceptanceRate"] = _require_number(
            "clinicalContext.expertAcceptanceRate",
            raw.get("expertAcceptanceRate"),
            minimum=0.0,
            maximum=1.0,
        )
    else:
        out["expertAcceptanceRate"] = None
    return out


def _hash_payload(payload):
    try:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except Exception:
        canonical = str(payload)
    return hashlib.sha256(canonical.encode("utf-8", errors="ignore")).hexdigest()


def _payload_without_response_hash(payload):
    if not isinstance(payload, dict):
        return payload
    if "responseHash" not in payload:
        return payload
    cloned = dict(payload)
    cloned.pop("responseHash", None)
    return cloned


def _payload_for_deterministic_hash(payload):
    transient_keys = {
        "requestId",
        "taskId",
        "requestHash",
        "responseHash",
        "timeMs",
        "elapsedMs",
        "planningTimeMs",
        "candidateCache",
        "computeProfile",
        "deterministicResponseHash",
        "replayOf",
        "lastSeen",
        "updatedAt",
        "plannerExecutionBackend",
        "nativeEngineBypassReason",
    }

    def _strip(value):
        if isinstance(value, dict):
            return {k: _strip(v) for k, v in value.items() if k not in transient_keys}
        if isinstance(value, list):
            return [_strip(v) for v in value]
        return value

    return _strip(payload)


def _attach_payload_hashes(req_payload, resp_payload):
    if isinstance(resp_payload, dict):
        if app.config.get("PAPER_RUNTIME_IDENTITY"):
            resp_payload.setdefault("runtimeIdentity", deepcopy(app.config["PAPER_RUNTIME_IDENTITY"]))
        resp_payload.setdefault("environmentSignature", deepcopy(environment_signature))
        resp_payload.setdefault("environmentSignatureHash", environment_signature.get("signatureHash"))
    req_hash = _hash_payload(req_payload)
    resp_payload["requestHash"] = req_hash
    resp_payload["responseHash"] = _hash_payload(_payload_without_response_hash(resp_payload))
    resp_payload["deterministicResponseHash"] = _hash_payload(
        _payload_for_deterministic_hash(resp_payload)
    )
    return req_hash, resp_payload["responseHash"]


def _summarize_payload(payload):
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}
    summary = {"keys": sorted(payload.keys())}
    if "targetKeys" in payload and isinstance(payload["targetKeys"], list):
        summary["targetCount"] = len(payload["targetKeys"])
    if "trajectories" in payload and isinstance(payload["trajectories"], list):
        summary["trajectoryCount"] = len(payload["trajectories"])
    summary["payloadHash"] = _hash_payload(payload)[:16]
    return summary


def _write_audit_record(record):
    line = json.dumps(record, ensure_ascii=True)
    with audit_lock:
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _persist_replay_record(request_id, req_payload, resp_payload):
    if not request_id:
        return
    req_hash = (
        resp_payload.get("requestHash")
        if isinstance(resp_payload, dict)
        else None
    ) or _hash_payload(req_payload)
    resp_hash = (
        resp_payload.get("responseHash")
        if isinstance(resp_payload, dict)
        else None
    ) or _hash_payload(_payload_without_response_hash(resp_payload))
    record = {
        "requestId": request_id,
        "timestamp": _utc_now_iso(),
        "engineVersion": ENGINE_VERSION,
        "atlasHash": atlas_signature.get("hash"),
        "environmentSignature": deepcopy(environment_signature),
        "environmentSignatureHash": environment_signature.get("signatureHash"),
        "requestHash": req_hash,
        "responseHash": resp_hash,
        "request": deepcopy(req_payload),
        "response": deepcopy(resp_payload),
    }
    with replay_lock:
        replay_index[request_id] = record
        if len(replay_index) > REPLAY_RETENTION:
            stale_keys = sorted(replay_index.keys())[:-REPLAY_RETENTION]
            for key in stale_keys:
                replay_index.pop(key, None)
        out_path = REPLAY_DIR / f"{request_id}.json"
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)


def _load_replay_record(request_id):
    if not request_id:
        return None
    with replay_lock:
        if request_id in replay_index:
            return deepcopy(replay_index[request_id])
    disk_path = REPLAY_DIR / f"{request_id}.json"
    if not disk_path.exists():
        return None
    try:
        with disk_path.open("r", encoding="utf-8") as fh:
            record = json.load(fh)
        with replay_lock:
            replay_index[request_id] = record
        return deepcopy(record)
    except Exception:
        return None


def _safe_abs_path(path_like, fallback=None, allowed_roots=None):
    try:
        raw = str(path_like or "").strip()
        if not raw and fallback is not None:
            path = Path(fallback).expanduser().resolve()
        elif not raw:
            return None
        else:
            path = Path(raw).expanduser().resolve()
    except Exception:
        raise ContractError(
            "Invalid file system path",
            status_code=400,
            code="path_invalid",
            details=["Path could not be resolved."],
        )
    roots = list(allowed_roots or [])
    if roots and not any(path_is_under(path, root) for root in roots):
        raise ContractError(
            "Path is outside the allowed workspace",
            status_code=400,
            code="path_outside_workspace",
            details=[
                "Use a path under PROBEPLANNER_STATE_DIR or configure PROBEPLANNER_BENCHMARK_ALLOWED_ROOTS.",
            ],
        )
    return path


def _safe_benchmark_path(path_like, fallback=None):
    roots = None if BENCHMARK_ALLOW_EXTERNAL_PATHS else BENCHMARK_ALLOWED_ROOTS
    return _safe_abs_path(path_like, fallback=fallback, allowed_roots=roots)


def _benchmark_default_template_paths(out_dir):
    template_dir = out_dir / BENCHMARK_DEFAULT_TEMPLATE_DIRNAME
    return {
        "templateDir": template_dir,
        "expertCsv": template_dir / BENCHMARK_DEFAULT_EXPERT_BASENAME,
        "manualOverrideCsv": template_dir / BENCHMARK_DEFAULT_MANUAL_OVERRIDE_BASENAME,
        "trialCsv": template_dir / BENCHMARK_DEFAULT_TRIAL_BASENAME,
        "residualCsv": template_dir / BENCHMARK_DEFAULT_RESIDUAL_BASENAME,
        "paramTemplate": template_dir / BENCHMARK_DEFAULT_PARAM_TEMPLATE_BASENAME,
    }


def _benchmark_script_manifest():
    scripts = {
        "benchmark": BENCHMARK_SCRIPT,
        "reliability": BENCHMARK_RELIABILITY_SCRIPT,
        "externalValidation": BENCHMARK_EXTERNAL_VALIDATION_SCRIPT,
        "paperPackage": BENCHMARK_PAPER_SCRIPT,
        "goldenReplay": BENCHMARK_GOLDEN_SCRIPT,
        "versionSignature": BENCHMARK_SIGNATURE_SCRIPT,
        "uncertaintyCalibration": BENCHMARK_CALIBRATION_SCRIPT,
        "silentStudy": CLINICAL_SILENT_SCRIPT,
        "interventional": CLINICAL_INTERVENTIONAL_SCRIPT,
    }
    return {
        key: {"path": str(path), "exists": bool(path.exists())}
        for key, path in scripts.items()
    }


def _benchmark_manifest(out_dir):
    files = []
    if not out_dir.exists() or not out_dir.is_dir():
        return files
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        file_type = (
            "plot" if suffix in (".png", ".jpg", ".jpeg", ".svg", ".webp") else
            "csv" if suffix == ".csv" else
            "markdown" if suffix in (".md", ".markdown") else
            "json" if suffix == ".json" else
            "other"
        )
        rel = path.relative_to(out_dir).as_posix()
        try:
            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            size = int(stat.st_size)
        except Exception:
            mtime = None
            size = None
        files.append(
            {
                "name": rel,
                "sizeBytes": size,
                "modifiedAt": mtime,
                "type": file_type,
                "group": rel.split("/", 1)[0] if "/" in rel else "root",
            }
        )
    return files


def _validate_benchmark_filename(name):
    if not isinstance(name, str) or not name.strip():
        return None
    raw = name.strip().replace("\\", "/")
    candidate = Path(raw)
    if candidate.is_absolute():
        return None
    parts = [p for p in candidate.parts if p not in ("", ".")]
    if not parts:
        return None
    if any(p == ".." for p in parts):
        return None
    return Path(*parts).as_posix()


def _tail_lines(text, max_lines=15):
    lines = (text or "").strip().splitlines()
    return lines[-max_lines:]


def _write_csv_template(path, header, sample_rows=None, overwrite=False):
    if path.exists() and not overwrite:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [",".join(header)]
    for row in (sample_rows or []):
        cells = []
        for value in row:
            cell = str(value)
            if "," in cell or '"' in cell:
                cell = '"' + cell.replace('"', '""') + '"'
            cells.append(cell)
        rows.append(",".join(cells))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return True


def _write_json_template(path, payload, overwrite=False):
    if path.exists() and not overwrite:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def _init_benchmark_workspace(
    replay_dir,
    out_dir,
    expert_csv=None,
    manual_override_csv=None,
    trial_csv=None,
    residual_csv=None,
    param_template=None,
    overwrite_templates=False,
):
    replay_dir = _safe_benchmark_path(replay_dir, benchmark_last_replay_dir or REPLAY_DIR)
    out_dir = _safe_benchmark_path(out_dir, benchmark_last_out_dir or BENCHMARK_DEFAULT_OUT_DIR)
    if replay_dir is None or out_dir is None:
        raise ContractError("Invalid benchmark path", code="benchmark_path_invalid")
    defaults = _benchmark_default_template_paths(out_dir)
    expert_csv = _safe_benchmark_path(expert_csv, defaults["expertCsv"])
    manual_override_csv = _safe_benchmark_path(manual_override_csv, defaults["manualOverrideCsv"])
    trial_csv = _safe_benchmark_path(trial_csv, defaults["trialCsv"])
    residual_csv = _safe_benchmark_path(residual_csv, defaults["residualCsv"])
    param_template = _safe_benchmark_path(param_template, defaults["paramTemplate"])

    replay_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    defaults["templateDir"].mkdir(parents=True, exist_ok=True)

    created = []
    if _write_csv_template(
        expert_csv,
        ["requestId", "expertProbeCount", "expertCoverageFraction", "expertMinSafetyDistanceMm"],
        sample_rows=[["example_request", "2", "0.92", "0.48"]],
        overwrite=overwrite_templates,
    ):
        created.append(str(expert_csv))
    if _write_csv_template(
        manual_override_csv,
        ["caseId", "manualOverride", "overrideReason", "correctionPath"],
        sample_rows=[["example_case", "0", "", ""]],
        overwrite=overwrite_templates,
    ):
        created.append(str(manual_override_csv))
    if _write_csv_template(
        trial_csv,
        ["arm", "safetyPass", "planningTimeSec", "coverageRate"],
        sample_rows=[
            ["manual", "1", "180", "0.88"],
            ["manual_plus_system", "1", "122", "0.91"],
        ],
        overwrite=overwrite_templates,
    ):
        created.append(str(trial_csv))
    if _write_csv_template(
        residual_csv,
        ["entryErrorMm", "angleErrorDeg", "depthErrorMm", "riskDistanceErrorMm"],
        sample_rows=[["0.12", "1.8", "0.21", "0.05"]],
        overwrite=overwrite_templates,
    ):
        created.append(str(residual_csv))
    if _write_json_template(
        param_template,
        {
            "algorithm": "drcc",
            "drccEnabled": True,
            "objectivePreset": "balanced",
            "targetingMode": "single_hemisphere",
            "minRiskDistanceMm": 0.35,
            "drccRiskEpsilon": 0.08,
            "drccWassersteinRadius": 0.03,
            "drccEvalTopK": 240,
            "drccStrict": True,
            "drccCvarAlpha": 0.9,
            "drccSolver": "exact_pool",
            "drccExactMaxCandidates": 42,
            "drccExactTimeLimitS": 18.0,
            "drccMisocpMaxCandidates": 56,
            "drccMisocpTimeLimitS": 45.0,
            "drccMisocpSocWeight": 0.15,
            "sdfRiskIntegralWeight": 1.0,
            "sdfRiskCvarWeight": 0.7,
            "selectiveConformalEnabled": True,
            "selectiveConformalAlpha": 0.1,
            "selectiveReviewProbThreshold": 0.8,
            "selectiveRejectProbThreshold": 0.35,
            "uncertaintyCalibration": {
                "entrySigmaMm": 0.15,
                "angleSigmaDeg": 2.0,
                "depthSigmaMm": 0.2,
                "riskDistanceSigmaMm": 0.08,
            },
        },
        overwrite=overwrite_templates,
    ):
        created.append(str(param_template))

    return {
        "replayDir": str(replay_dir),
        "outDir": str(out_dir),
        "templateDir": str(defaults["templateDir"]),
        "expertCsv": str(expert_csv),
        "manualOverrideCsv": str(manual_override_csv),
        "trialCsv": str(trial_csv),
        "residualCsv": str(residual_csv),
        "paramTemplate": str(param_template),
        "createdFiles": created,
    }


def _run_pipeline_step(step_name, cmd, cwd, timeout_sec=600):
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    stdout_tail = _tail_lines(proc.stdout, max_lines=20)
    stderr_tail = _tail_lines(proc.stderr, max_lines=20)
    step = {
        "name": step_name,
        "command": " ".join([str(c) for c in cmd]),
        "returnCode": int(proc.returncode),
        "stdoutTail": stdout_tail,
        "stderrTail": stderr_tail,
        "status": "ok" if proc.returncode == 0 else "failed",
    }
    return step


def _run_benchmark(
    replay_dir,
    out_dir,
    expert_csv=None,
    manual_override_csv=None,
    trial_csv=None,
    residual_csv=None,
    param_template=None,
    options=None,
):
    if not BENCHMARK_RUN_ENABLED:
        raise ContractError(
            "Benchmark execution is disabled",
            status_code=403,
            code="benchmark_run_disabled",
            details=["Set PROBEPLANNER_ENABLE_BENCHMARK_RUN=1 to enable subprocess benchmark execution."],
        )
    if not BENCHMARK_SCRIPT.exists():
        raise ContractError(
            "Benchmark script not found",
            code="benchmark_script_missing",
            details=[str(BENCHMARK_SCRIPT)],
    )
    options = dict(options or {})
    replay_dir = _safe_benchmark_path(replay_dir, benchmark_last_replay_dir or REPLAY_DIR)
    out_dir = _safe_benchmark_path(out_dir, benchmark_last_out_dir or BENCHMARK_DEFAULT_OUT_DIR)
    if replay_dir is None or out_dir is None:
        raise ContractError("Invalid benchmark path", code="benchmark_path_invalid")
    defaults = _benchmark_default_template_paths(out_dir)
    expert_csv = _safe_benchmark_path(expert_csv, defaults["expertCsv"])
    manual_override_csv = _safe_benchmark_path(manual_override_csv, defaults["manualOverrideCsv"])
    trial_csv = _safe_benchmark_path(trial_csv, defaults["trialCsv"])
    residual_csv = _safe_benchmark_path(residual_csv, defaults["residualCsv"])
    param_template = _safe_benchmark_path(param_template, defaults["paramTemplate"])
    out_dir.mkdir(parents=True, exist_ok=True)

    run_reliability = bool(options.get("runReliability", True))
    run_paper_package = bool(options.get("runPaperPackage", True))
    run_golden_replay = bool(options.get("runGoldenReplayCheck", True))
    run_version_signature = bool(options.get("runVersionSignature", True))
    run_silent_study = bool(options.get("runSilentStudy", True))
    run_interventional = bool(
        options.get(
            "runInterventional",
            bool(trial_csv and Path(trial_csv).exists()),
        )
    )
    run_uncertainty_calib = bool(
        options.get(
            "runUncertaintyCalibration",
            bool(residual_csv and Path(residual_csv).exists()),
        )
    )
    run_external_validation = bool(options.get("runExternalValidation", True))
    strict_golden_replay = bool(options.get("strictGoldenReplay", True))

    steps = []
    critical_failures = []

    benchmark_cmd = [
        sys.executable,
        str(BENCHMARK_SCRIPT),
        "--replay-dir",
        str(replay_dir),
        "--out-dir",
        str(out_dir),
    ]
    if expert_csv:
        benchmark_cmd.extend(["--expert-csv", str(expert_csv)])
    main_step = _run_pipeline_step(
        "benchmark",
        benchmark_cmd,
        cwd=BENCHMARK_SCRIPT.parent,
        timeout_sec=10 * 60,
    )
    steps.append(main_step)
    if main_step["status"] != "ok":
        details = main_step["stderrTail"] or main_step["stdoutTail"] or [f"returnCode={main_step['returnCode']}"]
        raise ContractError(
            "Benchmark run failed",
            code="benchmark_run_failed",
            details=details,
            status_code=500,
        )

    if run_reliability:
        reliability_out = out_dir / "reliability"
        step = _run_pipeline_step(
            "reliability",
            [
                sys.executable,
                str(BENCHMARK_RELIABILITY_SCRIPT),
                "--replay-dir",
                str(replay_dir),
                "--out-dir",
                str(reliability_out),
                "--bins",
                "10",
            ],
            cwd=BENCHMARK_RELIABILITY_SCRIPT.parent,
            timeout_sec=8 * 60,
        ) if BENCHMARK_RELIABILITY_SCRIPT.exists() else {
            "name": "reliability",
            "status": "missing_script",
            "scriptPath": str(BENCHMARK_RELIABILITY_SCRIPT),
        }
        steps.append(step)

    if run_external_validation:
        external_out = out_dir / "external_validation"
        step = _run_pipeline_step(
            "external_validation",
            [
                sys.executable,
                str(BENCHMARK_EXTERNAL_VALIDATION_SCRIPT),
                "--replay-dir",
                str(replay_dir),
                "--out-dir",
                str(external_out),
            ],
            cwd=BENCHMARK_EXTERNAL_VALIDATION_SCRIPT.parent,
            timeout_sec=8 * 60,
        ) if BENCHMARK_EXTERNAL_VALIDATION_SCRIPT.exists() else {
            "name": "external_validation",
            "status": "missing_script",
            "scriptPath": str(BENCHMARK_EXTERNAL_VALIDATION_SCRIPT),
        }
        steps.append(step)

    if run_silent_study:
        silent_out = out_dir / "silent_study"
        cmd = [
            sys.executable,
            str(CLINICAL_SILENT_SCRIPT),
            "--replay-dir",
            str(replay_dir),
            "--out-dir",
            str(silent_out),
        ]
        if manual_override_csv and manual_override_csv.exists():
            cmd.extend(["--manual-override-csv", str(manual_override_csv)])
        step = _run_pipeline_step(
            "silent_study",
            cmd,
            cwd=CLINICAL_SILENT_SCRIPT.parent,
            timeout_sec=8 * 60,
        ) if CLINICAL_SILENT_SCRIPT.exists() else {
            "name": "silent_study",
            "status": "missing_script",
            "scriptPath": str(CLINICAL_SILENT_SCRIPT),
        }
        steps.append(step)

    if run_version_signature:
        replay_file = None
        replay_candidates = list(sorted(replay_dir.glob("*.json"), key=lambda p: p.stat().st_mtime if p.exists() else 0))
        if replay_candidates:
            replay_file = replay_candidates[-1]
        signature_out = out_dir / "version_signature.json"
        cmd = [
            sys.executable,
            str(BENCHMARK_SIGNATURE_SCRIPT),
            "--out",
            str(signature_out),
        ]
        if replay_file:
            cmd.extend(["--replay-file", str(replay_file)])
        if param_template and param_template.exists():
            cmd.extend(["--param-template", str(param_template)])
        step = _run_pipeline_step(
            "version_signature",
            cmd,
            cwd=BENCHMARK_SIGNATURE_SCRIPT.parent,
            timeout_sec=3 * 60,
        ) if BENCHMARK_SIGNATURE_SCRIPT.exists() else {
            "name": "version_signature",
            "status": "missing_script",
            "scriptPath": str(BENCHMARK_SIGNATURE_SCRIPT),
        }
        steps.append(step)

    if run_paper_package:
        benchmark_csv = out_dir / "benchmark_results.csv"
        trajectory_csv = out_dir / "trajectory_metrics.csv"
        failure_csv = out_dir / "silent_study" / "failure_cards.csv"
        if not failure_csv.exists():
            failure_csv = out_dir / "failure_mode_counts.csv"
        paper_out = out_dir / "paper_figures"
        cmd = [
            sys.executable,
            str(BENCHMARK_PAPER_SCRIPT),
            "--benchmark-csv",
            str(benchmark_csv),
            "--replay-dir",
            str(replay_dir),
            "--out-dir",
            str(paper_out),
        ]
        if trajectory_csv.exists():
            cmd.extend(["--trajectory-csv", str(trajectory_csv)])
        if failure_csv.exists():
            cmd.extend(["--failure-csv", str(failure_csv)])
        signature_json = out_dir / "version_signature.json"
        if signature_json.exists():
            cmd.extend(["--version-signature-json", str(signature_json)])
        step = _run_pipeline_step(
            "paper_package",
            cmd,
            cwd=BENCHMARK_PAPER_SCRIPT.parent,
            timeout_sec=8 * 60,
        ) if BENCHMARK_PAPER_SCRIPT.exists() else {
            "name": "paper_package",
            "status": "missing_script",
            "scriptPath": str(BENCHMARK_PAPER_SCRIPT),
        }
        steps.append(step)

    if run_golden_replay:
        golden_cmd = [
            sys.executable,
            str(BENCHMARK_GOLDEN_SCRIPT),
            "--replay-dir",
            str(replay_dir),
            "--engine-version",
            str(ENGINE_VERSION),
            "--strict-scope",
        ]
        atlas_hash_for_scope = str((atlas_signature or {}).get("hash") or "").strip()
        if atlas_hash_for_scope:
            golden_cmd.extend(["--atlas-hash", atlas_hash_for_scope])
        step = _run_pipeline_step(
            "golden_replay_check",
            golden_cmd,
            cwd=BENCHMARK_GOLDEN_SCRIPT.parent,
            timeout_sec=5 * 60,
        ) if BENCHMARK_GOLDEN_SCRIPT.exists() else {
            "name": "golden_replay_check",
            "status": "missing_script",
            "scriptPath": str(BENCHMARK_GOLDEN_SCRIPT),
        }
        steps.append(step)
        if strict_golden_replay and step.get("status") == "failed":
            details = step.get("stderrTail") or step.get("stdoutTail") or ["Golden replay check failed"]
            raise ContractError(
                "Golden replay check failed",
                code="benchmark_reproducibility_failed",
                details=details,
                status_code=500,
            )

    if run_interventional:
        if trial_csv and trial_csv.exists():
            step = _run_pipeline_step(
                "interventional_trial",
                [
                    sys.executable,
                    str(CLINICAL_INTERVENTIONAL_SCRIPT),
                    "--csv",
                    str(trial_csv),
                    "--out-dir",
                    str(out_dir / "interventional"),
                ],
                cwd=CLINICAL_INTERVENTIONAL_SCRIPT.parent,
                timeout_sec=5 * 60,
            ) if CLINICAL_INTERVENTIONAL_SCRIPT.exists() else {
                "name": "interventional_trial",
                "status": "missing_script",
                "scriptPath": str(CLINICAL_INTERVENTIONAL_SCRIPT),
            }
        else:
            step = {
                "name": "interventional_trial",
                "status": "skipped",
                "reason": "trial_csv_missing",
                "trialCsv": str(trial_csv) if trial_csv else None,
            }
        steps.append(step)

    if run_uncertainty_calib:
        if residual_csv and residual_csv.exists():
            step = _run_pipeline_step(
                "uncertainty_calibration",
                [
                    sys.executable,
                    str(BENCHMARK_CALIBRATION_SCRIPT),
                    "--csv",
                    str(residual_csv),
                    "--out",
                    str(out_dir / "uncertainty_calibration.json"),
                ],
                cwd=BENCHMARK_CALIBRATION_SCRIPT.parent,
                timeout_sec=3 * 60,
            ) if BENCHMARK_CALIBRATION_SCRIPT.exists() else {
                "name": "uncertainty_calibration",
                "status": "missing_script",
                "scriptPath": str(BENCHMARK_CALIBRATION_SCRIPT),
            }
        else:
            step = {
                "name": "uncertainty_calibration",
                "status": "skipped",
                "reason": "residual_csv_missing",
                "residualCsv": str(residual_csv) if residual_csv else None,
            }
        steps.append(step)

    for step in steps:
        step_name = step.get("name")
        step_status = step.get("status")
        if step_status == "missing_script":
            critical_failures.append(step_name)
        elif step_status == "failed":
            # Historical replay corpora can contain old-response drift. In non-strict
            # mode keep that as a visible reproducibility warning without blocking
            # paper-package generation; strict mode remains a hard gate.
            if step_name == "golden_replay_check" and not strict_golden_replay:
                continue
            critical_failures.append(step_name)

    return {
        "replayDir": str(replay_dir),
        "outDir": str(out_dir),
        "runOptions": {
            "runReliability": run_reliability,
            "runExternalValidation": run_external_validation,
            "runPaperPackage": run_paper_package,
            "runGoldenReplayCheck": run_golden_replay,
            "strictGoldenReplay": strict_golden_replay,
            "runVersionSignature": run_version_signature,
            "runSilentStudy": run_silent_study,
            "runInterventional": run_interventional,
            "runUncertaintyCalibration": run_uncertainty_calib,
        },
        "templatePaths": {
            "expertCsv": str(expert_csv) if expert_csv else None,
            "manualOverrideCsv": str(manual_override_csv) if manual_override_csv else None,
            "trialCsv": str(trial_csv) if trial_csv else None,
            "residualCsv": str(residual_csv) if residual_csv else None,
            "paramTemplate": str(param_template) if param_template else None,
        },
        "pipeline": steps,
        "pipelinePassed": len(critical_failures) == 0,
        "pipelineFailures": critical_failures,
        "reproducibilityGatePassed": not (
            strict_golden_replay and any(step.get("name") == "golden_replay_check" and step.get("status") == "failed" for step in steps)
        ),
        "files": _benchmark_manifest(out_dir),
    }


def _load_version_freeze_state():
    if not VERSION_FREEZE_FILE.exists():
        return
    try:
        with VERSION_FREEZE_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            version_freeze_state.update({
                "frozen": bool(data.get("frozen", False)),
                "engineVersion": str(data.get("engineVersion", ENGINE_VERSION)),
                "atlasHash": data.get("atlasHash"),
                "frozenAt": data.get("frozenAt"),
                "frozenBy": data.get("frozenBy"),
                "note": str(data.get("note", "")),
            })
    except Exception as exc:
        log.warning(f"Failed to load version freeze state: {exc}")


def _persist_version_freeze_state():
    with VERSION_FREEZE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(version_freeze_state, fh, ensure_ascii=False, indent=2)


def _normalize_constraint_modes(raw_modes):
    incoming = _require_dict("constraintModes", raw_modes)
    merged = dict(incoming)
    details = []

    for alias, canon in CONSTRAINT_ALIASES.items():
        if alias in incoming and canon in incoming and incoming[alias] != incoming[canon]:
            details.append(f"constraintModes.{alias} conflicts with {canon}")
        if alias in incoming and canon not in incoming:
            merged[canon] = incoming[alias]

    unknown = [
        k for k in merged.keys()
        if k not in CONSTRAINT_KEYS and k not in OPTIONAL_CONSTRAINT_KEYS and k not in CONSTRAINT_ALIASES
    ]
    if unknown:
        details.extend([f"Unknown constraint key: {k}" for k in sorted(unknown)])

    normalized = {}
    for key in CONSTRAINT_KEYS:
        if key not in merged:
            details.append(f"Missing constraint key: {key}")
            continue
        mode = merged[key]
        if mode not in CONSTRAINT_MODES:
            details.append(
                f"Invalid mode for {key}: {mode} (expected {', '.join(CONSTRAINT_MODES)})"
            )
            continue
        normalized[key] = mode

    for key in OPTIONAL_CONSTRAINT_KEYS:
        if key not in merged:
            continue
        mode = merged[key]
        if mode not in CONSTRAINT_MODES:
            details.append(
                f"Invalid mode for {key}: {mode} (expected {', '.join(CONSTRAINT_MODES)})"
            )
            continue
        normalized[key] = mode

    if details:
        raise ContractError("Constraint contract violation", details=details, code="constraint_contract")

    return normalized


def _normalize_correction_factors(raw_corr):
    corr = dict(DEFAULT_CORRECTION_FACTORS)
    if raw_corr in (None, {}):
        return corr, False
    raw = _require_dict("correctionFactors", raw_corr)
    unknown = [k for k in raw.keys() if k not in corr]
    if unknown:
        raise ContractError(
            "Unknown correctionFactors keys",
            details=[f"Unknown correction key: {k}" for k in sorted(unknown)],
        )
    for key, default in DEFAULT_CORRECTION_FACTORS.items():
        value = raw.get(key, default)
        corr[key] = _require_number(f"correctionFactors.{key}", value)
    if corr["ap"] <= 0 or corr["ml"] <= 0 or corr["dv"] <= 0:
        raise ContractError("Scale factors ap/ml/dv must be > 0")
    applied = any(abs(corr[k] - v) > 1e-12 for k, v in DEFAULT_CORRECTION_FACTORS.items())
    return corr, applied


def _normalize_ci_pair(name, value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ContractError(f"{name} must be [lo, hi]")
    lo = _require_number(f"{name}[0]", value[0])
    hi = _require_number(f"{name}[1]", value[1])
    if lo > hi:
        lo, hi = hi, lo
    return [float(lo), float(hi)]


def _normalize_correction_uncertainty(raw_unc):
    if raw_unc in (None, {}):
        return None
    raw = _require_dict("correctionUncertainty", raw_unc)
    out = {}
    if "sigmaMm" in raw:
        out["sigmaMm"] = _require_number("correctionUncertainty.sigmaMm", raw["sigmaMm"], minimum=0)
    for key in (
        "apScaleCi95",
        "mlScaleCi95",
        "dvScaleCi95",
        "apShiftCi95",
        "mlShiftCi95",
        "dvShiftCi95",
    ):
        if key in raw:
            out[key] = _normalize_ci_pair(f"correctionUncertainty.{key}", raw[key])
    return out or None


def _validate_target_keys(value):
    if not isinstance(value, list) or not value:
        raise ContractError("targetKeys must be a non-empty array")

    clean = []
    for k in value:
        if not isinstance(k, str) or not k.strip():
            raise ContractError("targetKeys items must be non-empty strings")
        clean.append(k.strip())

    _auto_register_regions_if_needed(clean)
    unknown = [k for k in clean if k not in PLANNER_REGIONS]
    if unknown:
        raise ContractError(
            "Unknown target regions",
            details=[f"Unknown target region: {k}" for k in unknown],
        )
    return list(dict.fromkeys(clean))


def _normalize_targeting_mode(value):
    raw = str(value if value is not None else "single_hemisphere").strip().lower()
    alias_map = {
        "single": "single_hemisphere",
        "single_hemi": "single_hemisphere",
        "single_hemisphere": "single_hemisphere",
        "unilateral": "single_hemisphere",
        "bilateral": "bilateral_symmetric",
        "bilateral_symmetric": "bilateral_symmetric",
        "bilateral_symmetry": "bilateral_symmetric",
        "symmetric": "bilateral_symmetric",
    }
    mode = alias_map.get(raw, raw)
    if mode not in TARGETING_MODES:
        raise ContractError(
            "params.targetingMode must be one of single_hemisphere/bilateral_symmetric"
        )
    return mode


def _resolve_dynamic_uncertainty_inputs(uncertainty_calib, params_in):
    explicit = set()
    for key in ("entrySigmaMm", "angleSigmaDeg", "depthSigmaMm", "riskDistanceSigmaMm"):
        if key in (uncertainty_calib or {}):
            explicit.add(key)
        elif key in (params_in or {}):
            explicit.add(key)

    profile = dynamic_uncertainty_profile if isinstance(dynamic_uncertainty_profile, dict) else {}
    defaults = {
        "entrySigmaMm": float(params_in.get("perturbSigmaMm", 0.15)),
        "angleSigmaDeg": 2.0,
        "depthSigmaMm": 0.2,
        "riskDistanceSigmaMm": float(RISK_DISTANCE_SIGMA_MM_DEFAULT),
    }
    resolved = {}
    source_map = {}
    for key, fallback in defaults.items():
        profile_value = _metric_float(profile.get(key))
        if profile_value is not None and key not in explicit:
            resolved[key] = profile_value
            source_map[key] = f"dynamic:{profile.get('source', 'unknown')}"
        else:
            resolved[key] = fallback
            source_map[key] = "request_or_default"
    return resolved, source_map


TRE_INFLATED_RISK_THRESHOLD_KEYS = {"default", "vessel", "ventricle", "sulcus", "skull"}


def _pick_first_numeric(*values):
    for value in values:
        try:
            if value is None:
                continue
            num = float(value)
            if math.isfinite(num):
                return num
        except Exception:
            continue
    return None


def _resolve_risk_margin_tre_input(params_in, uncertainty_calib, planner_calib_unc):
    params_in = params_in if isinstance(params_in, dict) else {}
    uncertainty_calib = uncertainty_calib if isinstance(uncertainty_calib, dict) else {}
    planner_calib_unc = planner_calib_unc if isinstance(planner_calib_unc, dict) else {}

    explicit = _pick_first_numeric(
        uncertainty_calib.get("riskMarginTreMm"),
        params_in.get("riskMarginTreMm"),
        uncertainty_calib.get("landmarkTreMm"),
        params_in.get("landmarkTreMm"),
        uncertainty_calib.get("bregmaLambdaTreMm"),
        params_in.get("bregmaLambdaTreMm"),
        planner_calib_unc.get("riskMarginTreMm"),
        planner_calib_unc.get("landmarkTreMm"),
        planner_calib_unc.get("bregmaLambdaTreMm"),
    )
    if explicit is not None:
        return explicit

    paired = []
    for source in (uncertainty_calib, params_in, planner_calib_unc):
        for key in ("bregmaTreMm", "lambdaTreMm"):
            val = _pick_first_numeric(source.get(key))
            if val is not None:
                paired.append(val)
    if paired:
        return max(paired)
    return 0.0


def _inflate_risk_thresholds_for_tre(thresholds, tre_margin_mm):
    margin = float(max(0.0, tre_margin_mm or 0.0))
    clean = thresholds if isinstance(thresholds, dict) else {}
    out = {}
    for key, value in clean.items():
        try:
            base = float(value)
        except Exception:
            continue
        if str(key) in TRE_INFLATED_RISK_THRESHOLD_KEYS:
            out[str(key)] = round(float(base + margin), 6)
        else:
            out[str(key)] = round(float(base), 6)
    if "default" not in out:
        out["default"] = round(float(MIN_RISK_DISTANCE_MM_DEFAULT + margin), 6)
    return out


def _normalize_repair_preferred_candidate(raw):
    """Sanitize a frontend one-click repair hint.

    This is a preference hint only: the candidate must still pass every
    safety/geometry constraint before it can be selected.
    """
    if not isinstance(raw, dict):
        return None
    out = {}
    if raw.get("candidateId") is not None:
        out["candidateId"] = str(raw.get("candidateId"))
    sig = raw.get("candidateSignature")
    if isinstance(sig, (list, tuple)) and len(sig) >= 5:
        try:
            out["candidateSignature"] = [
                round(float(sig[0]), 5),
                round(float(sig[1]), 5),
                round(float(sig[2]), 4),
                round(float(sig[3]), 4),
                round(float(sig[4]), 2),
            ]
        except Exception:
            pass
    geom = raw.get("geometry")
    if isinstance(geom, dict):
        clean_geom = {}
        for key in ("ap_mm", "ml_mm", "theta_deg", "phi_deg", "depthUm"):
            if geom.get(key) is None:
                continue
            try:
                clean_geom[key] = float(geom.get(key))
            except Exception:
                continue
        if clean_geom:
            out["geometry"] = clean_geom
    for key in ("deltaApUm", "deltaMlUm", "deltaEntryUm", "deltaAngleDeg", "deltaDepthUm"):
        if raw.get(key) is None:
            continue
        try:
            out[key] = float(raw.get(key))
        except Exception:
            continue
    return out or None


def _normalize_stability_reference_trajectories(raw):
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ContractError("params.stabilityReferenceTrajectories must be an array")
    if len(raw) > 16:
        raise ContractError("params.stabilityReferenceTrajectories supports at most 16 probes")
    output = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ContractError(
                f"params.stabilityReferenceTrajectories[{index}] must be an object"
            )
        row = {}
        for source_key, output_key in (("entryCcfUm", "entryCcfUm"), ("tipCcfUm", "tipCcfUm")):
            value = item.get(source_key)
            if not isinstance(value, (list, tuple)) or len(value) != 3:
                raise ContractError(
                    f"params.stabilityReferenceTrajectories[{index}].{source_key} must be [x,y,z]"
                )
            row[output_key] = [
                _require_number(
                    f"params.stabilityReferenceTrajectories[{index}].{source_key}[{axis}]",
                    value[axis],
                    minimum=-100000.0,
                    maximum=100000.0,
                )
                for axis in range(3)
            ]
        output.append(row)
    return output


def _normalize_optimize_payload(raw_body):
    body = _require_dict("requestBody", raw_body)
    target_keys = _validate_target_keys(body.get("targetKeys"))
    clinical_context = _normalize_clinical_context(body.get("clinicalContext"))

    params_in = _require_dict("params", body.get("params", {}))
    probe_in = _require_dict("probeParams", body.get("probeParams", {}))
    modes = _normalize_constraint_modes(body.get("constraintModes", {}))
    corr, corr_applied = _normalize_correction_factors(body.get("correctionFactors", {}))
    corr_unc = _normalize_correction_uncertainty(body.get("correctionUncertainty"))

    algorithm = params_in.get("algorithm", "greedy")
    if algorithm not in ("greedy", "exact", "milp", "drcc"):
        raise ContractError("params.algorithm must be one of greedy/exact/milp/drcc")

    objective_preset = str(params_in.get("objectivePreset", "balanced")).strip().lower()
    if objective_preset not in ("balanced", "coverage_first", "safety_first", "angle_first"):
        raise ContractError("params.objectivePreset must be one of balanced/coverage_first/safety_first/angle_first")
    targeting_mode = _normalize_targeting_mode(params_in.get("targetingMode", "single_hemisphere"))
    target_endpoint_policy = str(
        params_in.get("targetEndpointPolicy", "trajectory_intersects")
    ).strip().lower()
    if target_endpoint_policy not in ("trajectory_intersects", "tip_in_target"):
        raise ContractError(
            "params.targetEndpointPolicy must be trajectory_intersects or tip_in_target"
        )
    stability_references = _normalize_stability_reference_trajectories(
        params_in.get("stabilityReferenceTrajectories", [])
    )
    uncertainty_calib = params_in.get("uncertaintyCalibration", {})
    if uncertainty_calib in (None, ""):
        uncertainty_calib = {}
    uncertainty_calib = _require_dict("params.uncertaintyCalibration", uncertainty_calib)
    planner_calib = _load_planner_calibration_profile(params_in.get("calibrationProfilePath"))
    planner_calib_unc = (
        planner_calib.get("uncertaintyCalibration", {})
        if isinstance(planner_calib.get("uncertaintyCalibration"), dict)
        else {}
    )
    planner_calib_weights = (
        planner_calib.get("objectiveWeights", {})
        if isinstance(planner_calib.get("objectiveWeights"), dict)
        else {}
    )
    planner_calib_thresholds = (
        planner_calib.get("riskThresholdsMm", {})
        if isinstance(planner_calib.get("riskThresholdsMm"), dict)
        else {}
    )
    for key in uncertainty_calib.keys():
        if key not in (
            "entrySigmaMm",
            "angleSigmaDeg",
            "depthSigmaMm",
            "riskDistanceSigmaMm",
            "riskMarginTreMm",
            "landmarkTreMm",
            "bregmaLambdaTreMm",
            "bregmaTreMm",
            "lambdaTreMm",
        ):
            raise ContractError(f"Unknown params.uncertaintyCalibration key: {key}")

    dynamic_unc_values, dynamic_unc_source = _resolve_dynamic_uncertainty_inputs(
        uncertainty_calib,
        params_in,
    )

    entry_sigma_input = uncertainty_calib.get(
        "entrySigmaMm",
        params_in.get(
            "entrySigmaMm",
            planner_calib_unc.get(
                "entrySigmaMm",
                dynamic_unc_values.get("entrySigmaMm", params_in.get("perturbSigmaMm", 0.15)),
            ),
        ),
    )
    angle_sigma_input = uncertainty_calib.get(
        "angleSigmaDeg",
        params_in.get(
            "angleSigmaDeg",
            planner_calib_unc.get("angleSigmaDeg", dynamic_unc_values.get("angleSigmaDeg", 2.0)),
        ),
    )
    depth_sigma_input = uncertainty_calib.get(
        "depthSigmaMm",
        params_in.get(
            "depthSigmaMm",
            planner_calib_unc.get("depthSigmaMm", dynamic_unc_values.get("depthSigmaMm", 0.2)),
        ),
    )
    risk_dist_sigma_input = uncertainty_calib.get(
        "riskDistanceSigmaMm",
        params_in.get(
            "riskDistanceSigmaMm",
            planner_calib_unc.get(
                "riskDistanceSigmaMm",
                dynamic_unc_values.get("riskDistanceSigmaMm", RISK_DISTANCE_SIGMA_MM_DEFAULT),
            ),
        ),
    )
    risk_margin_tre_input = _resolve_risk_margin_tre_input(
        params_in,
        uncertainty_calib,
        planner_calib_unc,
    )
    risk_threshold_template, risk_thresholds_mm = _resolve_risk_thresholds_with_template(params_in)
    if "riskThresholdsMm" not in params_in and planner_calib_thresholds:
        base_default = float(
            params_in.get(
                "minRiskDistanceMm",
                planner_calib_thresholds.get("default", MIN_RISK_DISTANCE_MM_DEFAULT),
            )
        )
        merged = sanitize_risk_thresholds(
            {**risk_thresholds_mm, **planner_calib_thresholds},
            default_mm=base_default,
        )
        risk_thresholds_mm = _enforce_boundary_risk_threshold_floors(merged)

    params = {
        "algorithm": algorithm,
        "minClearance": _require_number(
            "params.minClearance", params_in.get("minClearance", 1000), minimum=50, maximum=10000
        ),
        "coverageRadius": _require_number(
            "params.coverageRadius", params_in.get("coverageRadius", 600), minimum=25, maximum=5000
        ),
        "gridSpacing": _require_number(
            "params.gridSpacing", params_in.get("gridSpacing", 600), minimum=50, maximum=5000
        ),
        "candidateExpansionEnabled": _coerce_bool(params_in.get("candidateExpansionEnabled"), True),
        "angleStep": _require_number(
            "params.angleStep", params_in.get("angleStep", 5), minimum=1, maximum=30, integer=True
        ),
        "maxAngle": _require_number(
            "params.maxAngle", params_in.get("maxAngle", 15), minimum=0, maximum=85
        ),
        "skullTiltDeg": _require_number(
            "params.skullTiltDeg", params_in.get("skullTiltDeg", 0), minimum=-30, maximum=30
        ),
        "targetingMode": targeting_mode,
        "targetEndpointPolicy": target_endpoint_policy,
        "depthOptimizationEnabled": _coerce_bool(
            params_in.get("depthOptimizationEnabled", True)
        ),
        "includeCandidatePreview": _coerce_bool(params_in.get("includeCandidatePreview", False)),
        "bilateral": _coerce_bool(params_in.get("bilateral", False)),
        "coverageBoost": _coerce_bool(params_in.get("coverageBoost", False)),
        "robustOptimization": _coerce_bool(params_in.get("robustOptimization", True)),
        "robustSamples": _require_number(
            "params.robustSamples", params_in.get("robustSamples", 16), minimum=4, maximum=64, integer=True
        ),
        "perturbSigmaMm": _require_number(
            "params.perturbSigmaMm", params_in.get("perturbSigmaMm", 0.15), minimum=0.01, maximum=1.0
        ),
        "entrySigmaMm": _require_number(
            "params.entrySigmaMm", entry_sigma_input,
            minimum=0.01, maximum=2.0
        ),
        "angleSigmaDeg": _require_number(
            "params.angleSigmaDeg", angle_sigma_input,
            minimum=0.1, maximum=25.0
        ),
        "depthSigmaMm": _require_number(
            "params.depthSigmaMm", depth_sigma_input,
            minimum=0.01, maximum=5.0
        ),
        "riskDistanceSigmaMm": _require_number(
            "params.riskDistanceSigmaMm", risk_dist_sigma_input, minimum=0.001, maximum=2.0
        ),
        "riskMarginTreMm": _require_number(
            "params.riskMarginTreMm", risk_margin_tre_input, minimum=0.0, maximum=5.0
        ),
        "minRiskDistanceMm": _require_number(
            "params.minRiskDistanceMm",
            params_in.get(
                "minRiskDistanceMm",
                planner_calib_thresholds.get("default", MIN_RISK_DISTANCE_MM_DEFAULT),
            ),
            minimum=0.0,
            maximum=10.0,
        ),
        "contactStepUm": _require_number(
            "params.contactStepUm",
            params_in.get("contactStepUm", 25.0),
            minimum=5.0,
            maximum=500.0,
        ),
        "contactMinLengthUm": _require_number(
            "params.contactMinLengthUm",
            params_in.get("contactMinLengthUm", 0.0),
            minimum=0.0,
            maximum=20000.0,
        ),
        "contactMode": str(params_in.get("contactMode", "cylinder")).strip().lower() or "cylinder",
        "riskThresholdTemplate": risk_threshold_template,
        "riskThresholdsMm": risk_thresholds_mm,
        "paretoEnabled": _coerce_bool(params_in.get("paretoEnabled", True)),
        "paretoTopK": _require_number(
            "params.paretoTopK", params_in.get("paretoTopK", 12), minimum=2, maximum=40, integer=True
        ),
        "objectivePreset": objective_preset,
        "objectiveCoverageWeight": _require_number(
            "params.objectiveCoverageWeight", params_in.get("objectiveCoverageWeight", 0.5), minimum=0, maximum=2
        ),
        "objectiveSafetyWeight": _require_number(
            "params.objectiveSafetyWeight", params_in.get("objectiveSafetyWeight", 0.3), minimum=0, maximum=2
        ),
        "objectiveAngleWeight": _require_number(
            "params.objectiveAngleWeight", params_in.get("objectiveAngleWeight", 0.2), minimum=0, maximum=2
        ),
        "stabilityTieBreakerEnabled": _coerce_bool(
            params_in.get("stabilityTieBreakerEnabled", False)
        ),
        "stabilityReferenceWeight": _require_number(
            "params.stabilityReferenceWeight",
            params_in.get("stabilityReferenceWeight", 0.05),
            minimum=0.0,
            maximum=2.0,
        ),
        "stabilityEntryWeight": _require_number(
            "params.stabilityEntryWeight",
            params_in.get("stabilityEntryWeight", 1.0),
            minimum=0.0,
            maximum=10.0,
        ),
        "stabilityTipWeight": _require_number(
            "params.stabilityTipWeight",
            params_in.get("stabilityTipWeight", 1.0),
            minimum=0.0,
            maximum=10.0,
        ),
        "stabilityAngleWeight": _require_number(
            "params.stabilityAngleWeight",
            params_in.get("stabilityAngleWeight", 1.0),
            minimum=0.0,
            maximum=10.0,
        ),
        "stabilityReferenceTrajectories": stability_references,
        "stabilityReferenceProbeCount": _require_number(
            "params.stabilityReferenceProbeCount",
            params_in.get("stabilityReferenceProbeCount", len(stability_references)),
            minimum=0,
            maximum=16,
            integer=True,
        ),
        "objectiveRiskPrimary": _coerce_bool(
            params_in.get("objectiveRiskPrimary", OBJECTIVE_RISK_PRIMARY_DEFAULT)
        ),
        "objectiveRiskPrimaryBoost": _require_number(
            "params.objectiveRiskPrimaryBoost",
            params_in.get("objectiveRiskPrimaryBoost", OBJECTIVE_RISK_PRIMARY_BOOST_DEFAULT),
            minimum=0.0,
            maximum=0.8,
        ),
        "riskSoftplusSigmaMm": _require_number(
            "params.riskSoftplusSigmaMm",
            params_in.get("riskSoftplusSigmaMm", OBJECTIVE_RISK_SIGMA_MM_DEFAULT),
            minimum=0.005,
            maximum=0.5,
        ),
        "sdfRiskPathWeight": _require_number(
            "params.sdfRiskPathWeight",
            params_in.get("sdfRiskPathWeight", SDF_RISK_PATH_WEIGHT_DEFAULT),
            minimum=0.0,
            maximum=10.0,
        ),
        "sdfRiskMarginWeight": _require_number(
            "params.sdfRiskMarginWeight",
            params_in.get("sdfRiskMarginWeight", SDF_RISK_MARGIN_WEIGHT_DEFAULT),
            minimum=0.0,
            maximum=10.0,
        ),
        "computeWorkers": _require_number(
            "params.computeWorkers",
            params_in.get("computeWorkers", COMPUTE_WORKERS_DEFAULT),
            minimum=1,
            maximum=64,
            integer=True,
        ),
        "prefilterWorkers": _require_number(
            "params.prefilterWorkers",
            params_in.get("prefilterWorkers", COMPUTE_PREFILTER_WORKERS_DEFAULT),
            minimum=1,
            maximum=64,
            integer=True,
        ),
        "computeChunkSize": _require_number(
            "params.computeChunkSize",
            params_in.get("computeChunkSize", COMPUTE_CHUNK_SIZE_DEFAULT),
            minimum=4,
            maximum=2048,
            integer=True,
        ),
        "candidateGenWorkers": _require_number(
            "params.candidateGenWorkers",
            params_in.get("candidateGenWorkers", CANDIDATE_GEN_WORKERS_DEFAULT),
            minimum=1,
            maximum=64,
            integer=True,
        ),
        "candidateCoarseStepUm": _require_number(
            "params.candidateCoarseStepUm",
            params_in.get("candidateCoarseStepUm", CANDIDATE_COARSE_STEP_UM_DEFAULT),
            minimum=25.0,
            maximum=250.0,
        ),
        "candidateRefineStepUm": _require_number(
            "params.candidateRefineStepUm",
            params_in.get("candidateRefineStepUm", CANDIDATE_REFINE_STEP_UM_DEFAULT),
            minimum=5.0,
            maximum=100.0,
        ),
        "candidateRefineFraction": _require_number(
            "params.candidateRefineFraction",
            params_in.get("candidateRefineFraction", CANDIDATE_REFINE_FRACTION_DEFAULT),
            minimum=0.05,
            maximum=1.0,
        ),
        "candidateTargetDiversitySeeds": _require_number(
            "params.candidateTargetDiversitySeeds",
            params_in.get("candidateTargetDiversitySeeds", CANDIDATE_TARGET_DIVERSITY_SEEDS_DEFAULT),
            minimum=1,
            maximum=64,
            integer=True,
        ),
        "candidateTargetEntryVariants": _require_number(
            "params.candidateTargetEntryVariants",
            params_in.get("candidateTargetEntryVariants", CANDIDATE_TARGET_ENTRY_VARIANTS_DEFAULT),
            minimum=1,
            maximum=12,
            integer=True,
        ),
        "scoreEarlyStopEnabled": _coerce_bool(
            params_in.get("scoreEarlyStopEnabled", SCORE_EARLY_STOP_ENABLED_DEFAULT)
        ),
        "scoreEarlyStopTopK": _require_number(
            "params.scoreEarlyStopTopK",
            params_in.get("scoreEarlyStopTopK", SCORE_EARLY_STOP_TOPK_DEFAULT),
            minimum=4,
            maximum=5000,
            integer=True,
        ),
        "scoreEarlyStopMargin": _require_number(
            "params.scoreEarlyStopMargin",
            params_in.get("scoreEarlyStopMargin", SCORE_EARLY_STOP_MARGIN_DEFAULT),
            minimum=0.0,
            maximum=0.5,
        ),
        "streamingPipelineEnabled": _coerce_bool(
            params_in.get("streamingPipelineEnabled", STREAMING_PIPELINE_ENABLED_DEFAULT)
        ),
        "streamingScoreBatchSize": _require_number(
            "params.streamingScoreBatchSize",
            params_in.get("streamingScoreBatchSize", STREAMING_SCORE_BATCH_SIZE_DEFAULT),
            minimum=8,
            maximum=2048,
            integer=True,
        ),
        "streamingQueueMaxSize": _require_number(
            "params.streamingQueueMaxSize",
            params_in.get("streamingQueueMaxSize", STREAMING_QUEUE_MAXSIZE_DEFAULT),
            minimum=2,
            maximum=256,
            integer=True,
        ),
        "streamingScoreWorkersMax": _require_number(
            "params.streamingScoreWorkersMax",
            params_in.get("streamingScoreWorkersMax", STREAMING_SCORE_WORKERS_MAX_DEFAULT),
            minimum=1,
            maximum=16,
            integer=True,
        ),
        "streamingAllowOversubscribe": _coerce_bool(
            params_in.get("streamingAllowOversubscribe", STREAMING_ALLOW_OVERSUBSCRIBE_DEFAULT)
        ),
        "drccEnabled": _coerce_bool(params_in.get("drccEnabled", algorithm == "drcc")),
        "drccRiskEpsilon": _require_number(
            "params.drccRiskEpsilon",
            params_in.get("drccRiskEpsilon", DRCC_RISK_EPSILON_DEFAULT),
            minimum=0.001,
            maximum=0.5,
        ),
        "drccWassersteinRadius": _require_number(
            "params.drccWassersteinRadius",
            params_in.get("drccWassersteinRadius", DRCC_WASSERSTEIN_RADIUS_DEFAULT),
            minimum=0.0,
            maximum=0.5,
        ),
        "drccEvalTopK": _require_number(
            "params.drccEvalTopK",
            params_in.get("drccEvalTopK", DRCC_EVAL_TOPK_DEFAULT),
            minimum=20,
            maximum=2000,
            integer=True,
        ),
        "drccPerTargetKeep": _require_number(
            "params.drccPerTargetKeep",
            params_in.get("drccPerTargetKeep", 6),
            minimum=1,
            maximum=20,
            integer=True,
        ),
        "drccCvarAlpha": _require_number(
            "params.drccCvarAlpha",
            params_in.get("drccCvarAlpha", DRCC_CVAR_ALPHA_DEFAULT),
            minimum=0.5,
            maximum=0.99,
        ),
        "drccStrict": _coerce_bool(params_in.get("drccStrict", True)),
        "drccSolver": str(params_in.get("drccSolver", DRCC_SOLVER_DEFAULT)).strip().lower() or DRCC_SOLVER_DEFAULT,
        "drccExactMaxCandidates": _require_number(
            "params.drccExactMaxCandidates",
            params_in.get("drccExactMaxCandidates", DRCC_EXACT_MAX_CANDIDATES_DEFAULT),
            minimum=8,
            maximum=120,
            integer=True,
        ),
        "drccExactTimeLimitS": _require_number(
            "params.drccExactTimeLimitS",
            params_in.get("drccExactTimeLimitS", DRCC_EXACT_TIME_LIMIT_S_DEFAULT),
            minimum=2.0,
            maximum=120.0,
        ),
        "drccMisocpMaxCandidates": _require_number(
            "params.drccMisocpMaxCandidates",
            params_in.get("drccMisocpMaxCandidates", DRCC_MISOCP_MAX_CANDIDATES_DEFAULT),
            minimum=8,
            maximum=200,
            integer=True,
        ),
        "drccMisocpTimeLimitS": _require_number(
            "params.drccMisocpTimeLimitS",
            params_in.get("drccMisocpTimeLimitS", DRCC_MISOCP_TIME_LIMIT_S_DEFAULT),
            minimum=2.0,
            maximum=300.0,
        ),
        "drccMisocpSocWeight": _require_number(
            "params.drccMisocpSocWeight",
            params_in.get("drccMisocpSocWeight", DRCC_MISOCP_SOC_WEIGHT_DEFAULT),
            minimum=0.0,
            maximum=20.0,
        ),
        "drccAdaptiveBudget": _coerce_bool(
            params_in.get("drccAdaptiveBudget", DRCC_ADAPTIVE_BUDGET_DEFAULT)
        ),
        "sdfRiskIntegralWeight": _require_number(
            "params.sdfRiskIntegralWeight",
            params_in.get("sdfRiskIntegralWeight", SDF_RISK_INTEGRAL_WEIGHT_DEFAULT),
            minimum=0.0,
            maximum=10.0,
        ),
        "sdfRiskCvarWeight": _require_number(
            "params.sdfRiskCvarWeight",
            params_in.get("sdfRiskCvarWeight", SDF_RISK_CVAR_WEIGHT_DEFAULT),
            minimum=0.0,
            maximum=10.0,
        ),
        "autoAvoidEnabled": _coerce_bool(
            params_in.get("autoAvoidEnabled", AUTO_AVOID_ENABLED_DEFAULT)
        ),
        "autoAvoidCoarseStepUm": _require_number(
            "params.autoAvoidCoarseStepUm",
            params_in.get("autoAvoidCoarseStepUm", AUTO_AVOID_COARSE_STEP_UM_DEFAULT),
            minimum=10.0,
            maximum=200.0,
        ),
        "autoAvoidSafetyBufferMm": _require_number(
            "params.autoAvoidSafetyBufferMm",
            params_in.get("autoAvoidSafetyBufferMm", AUTO_AVOID_SAFETY_BUFFER_MM_DEFAULT),
            minimum=0.0,
            maximum=0.5,
        ),
        "autoAvoidNearMarginMm": _require_number(
            "params.autoAvoidNearMarginMm",
            params_in.get("autoAvoidNearMarginMm", AUTO_AVOID_NEAR_MARGIN_MM_DEFAULT),
            minimum=0.0,
            maximum=0.6,
        ),
        "constraintAwareSearch": _coerce_bool(
            params_in.get("constraintAwareSearch", CONSTRAINT_AWARE_SEARCH_DEFAULT)
        ),
        "autoRepairEnabled": _coerce_bool(
            params_in.get("autoRepairEnabled", AUTO_REPAIR_ENABLED_DEFAULT)
        ),
        "autoRepairTopK": _require_number(
            "params.autoRepairTopK",
            params_in.get("autoRepairTopK", AUTO_REPAIR_TOPK_DEFAULT),
            minimum=1,
            maximum=200,
            integer=True,
        ),
        "autoRepairEntryStepMm": _require_number(
            "params.autoRepairEntryStepMm",
            params_in.get("autoRepairEntryStepMm", AUTO_REPAIR_ENTRY_STEP_MM_DEFAULT),
            minimum=0.05,
            maximum=0.5,
        ),
        "autoRepairEntryMaxShiftMm": _require_number(
            "params.autoRepairEntryMaxShiftMm",
            params_in.get("autoRepairEntryMaxShiftMm", AUTO_REPAIR_ENTRY_MAX_SHIFT_MM_DEFAULT),
            minimum=0.1,
            maximum=4.0,
        ),
        "autoRepairAngleStepDeg": _require_number(
            "params.autoRepairAngleStepDeg",
            params_in.get("autoRepairAngleStepDeg", AUTO_REPAIR_ANGLE_STEP_DEG_DEFAULT),
            minimum=1.0,
            maximum=10.0,
        ),
        "autoRepairAngleMaxAdjustDeg": _require_number(
            "params.autoRepairAngleMaxAdjustDeg",
            params_in.get("autoRepairAngleMaxAdjustDeg", AUTO_REPAIR_ANGLE_MAX_ADJUST_DEG_DEFAULT),
            minimum=1.0,
            maximum=30.0,
        ),
        "autoRepairDepthStepMm": _require_number(
            "params.autoRepairDepthStepMm",
            params_in.get("autoRepairDepthStepMm", AUTO_REPAIR_DEPTH_STEP_MM_DEFAULT),
            minimum=0.05,
            maximum=1.0,
        ),
        "autoRepairDepthMaxAdjustMm": _require_number(
            "params.autoRepairDepthMaxAdjustMm",
            params_in.get("autoRepairDepthMaxAdjustMm", AUTO_REPAIR_DEPTH_MAX_ADJUST_MM_DEFAULT),
            minimum=0.1,
            maximum=5.0,
        ),
        "autoRepairMaxVariants": _require_number(
            "params.autoRepairMaxVariants",
            params_in.get("autoRepairMaxVariants", AUTO_REPAIR_MAX_VARIANTS_DEFAULT),
            minimum=20,
            maximum=5000,
            integer=True,
        ),
        "repairPreferredCandidate": _normalize_repair_preferred_candidate(
            params_in.get("repairPreferredCandidate")
        ),
        "strictFallbackDecompose": _coerce_bool(
            params_in.get("strictFallbackDecompose", STRICT_FALLBACK_DECOMPOSE_DEFAULT)
        ),
        "calibrationProfilePath": str(params_in.get("calibrationProfilePath", "") or "").strip(),
        "selectiveConformalEnabled": _coerce_bool(
            params_in.get("selectiveConformalEnabled", algorithm == "drcc")
        ),
        "selectiveConformalAlpha": _require_number(
            "params.selectiveConformalAlpha",
            params_in.get("selectiveConformalAlpha", SELECTIVE_CONFORMAL_ALPHA_DEFAULT),
            minimum=0.01,
            maximum=0.49,
        ),
        "selectiveReviewProbThreshold": _require_number(
            "params.selectiveReviewProbThreshold",
            params_in.get(
                "selectiveReviewProbThreshold",
                SELECTIVE_REVIEW_PROB_THRESHOLD_DEFAULT,
            ),
            minimum=0.5,
            maximum=0.99,
        ),
        "selectiveRejectProbThreshold": _require_number(
            "params.selectiveRejectProbThreshold",
            params_in.get(
                "selectiveRejectProbThreshold",
                SELECTIVE_REJECT_PROB_THRESHOLD_DEFAULT,
            ),
            minimum=0.0,
            maximum=0.49,
        ),
    }

    if params["targetingMode"] == "bilateral_symmetric":
        params["bilateral"] = True
    if params["algorithm"] == "drcc":
        params["drccEnabled"] = True
        params["robustOptimization"] = True
        params["paretoEnabled"] = True
    if params["drccSolver"] not in ("heuristic", "exact_pool", "hybrid", "cvar_relax", "misocp"):
        params["drccSolver"] = DRCC_SOLVER_DEFAULT
    if params["contactMode"] not in ("centerline", "cylinder"):
        params["contactMode"] = "cylinder"
    params["computeWorkers"] = _resolve_parallel_workers(
        params.get("computeWorkers", COMPUTE_WORKERS_DEFAULT),
        default_workers=COMPUTE_WORKERS_DEFAULT,
    )
    params["prefilterWorkers"] = _resolve_parallel_workers(
        params.get("prefilterWorkers", COMPUTE_PREFILTER_WORKERS_DEFAULT),
        default_workers=COMPUTE_PREFILTER_WORKERS_DEFAULT,
    )
    params["computeChunkSize"] = int(max(4, params.get("computeChunkSize", COMPUTE_CHUNK_SIZE_DEFAULT)))
    params["candidateGenWorkers"] = _resolve_parallel_workers(
        params.get("candidateGenWorkers", CANDIDATE_GEN_WORKERS_DEFAULT),
        default_workers=CANDIDATE_GEN_WORKERS_DEFAULT,
    )
    params["candidateCoarseStepUm"] = float(
        max(25.0, params.get("candidateCoarseStepUm", CANDIDATE_COARSE_STEP_UM_DEFAULT))
    )
    params["candidateRefineStepUm"] = float(
        max(5.0, params.get("candidateRefineStepUm", CANDIDATE_REFINE_STEP_UM_DEFAULT))
    )
    if params["candidateRefineStepUm"] > params["candidateCoarseStepUm"]:
        params["candidateRefineStepUm"] = float(params["candidateCoarseStepUm"])
    params["candidateRefineFraction"] = float(
        max(0.05, min(1.0, params.get("candidateRefineFraction", CANDIDATE_REFINE_FRACTION_DEFAULT)))
    )
    params["scoreEarlyStopTopK"] = int(max(4, params.get("scoreEarlyStopTopK", SCORE_EARLY_STOP_TOPK_DEFAULT)))
    params["scoreEarlyStopMargin"] = float(
        max(0.0, min(0.5, params.get("scoreEarlyStopMargin", SCORE_EARLY_STOP_MARGIN_DEFAULT)))
    )
    params["streamingScoreBatchSize"] = int(
        max(8, min(2048, params.get("streamingScoreBatchSize", STREAMING_SCORE_BATCH_SIZE_DEFAULT)))
    )
    params["streamingQueueMaxSize"] = int(
        max(2, min(256, params.get("streamingQueueMaxSize", STREAMING_QUEUE_MAXSIZE_DEFAULT)))
    )
    params["streamingScoreWorkersMax"] = int(
        max(1, min(16, params.get("streamingScoreWorkersMax", STREAMING_SCORE_WORKERS_MAX_DEFAULT)))
    )
    _set_runtime_thread_budget(max(RUNTIME_NUM_THREADS_DEFAULT, int(params["computeWorkers"])))
    params["autoRepairEntryMaxShiftMm"] = max(
        float(params["autoRepairEntryStepMm"]),
        float(params["autoRepairEntryMaxShiftMm"]),
    )
    params["autoRepairAngleMaxAdjustDeg"] = max(
        float(params["autoRepairAngleStepDeg"]),
        float(params["autoRepairAngleMaxAdjustDeg"]),
    )
    params["autoRepairDepthMaxAdjustMm"] = max(
        float(params["autoRepairDepthStepMm"]),
        float(params["autoRepairDepthMaxAdjustMm"]),
    )
    if params["selectiveReviewProbThreshold"] <= params["selectiveRejectProbThreshold"]:
        params["selectiveReviewProbThreshold"] = min(
            0.99, float(params["selectiveRejectProbThreshold"]) + 0.2
        )

    base_min_risk_distance_mm = float(params["minRiskDistanceMm"])
    base_risk_thresholds_mm = deepcopy(params["riskThresholdsMm"])
    risk_margin_tre_mm = round(float(params.get("riskMarginTreMm", 0.0)), 6)
    params["baseMinRiskDistanceMm"] = round(base_min_risk_distance_mm, 6)
    params["riskThresholdsMmBase"] = deepcopy(base_risk_thresholds_mm)
    if risk_margin_tre_mm > 0:
        params["minRiskDistanceMm"] = round(base_min_risk_distance_mm + risk_margin_tre_mm, 6)
        params["riskThresholdsMm"] = _inflate_risk_thresholds_for_tre(
            base_risk_thresholds_mm,
            risk_margin_tre_mm,
        )
        params["riskThresholdsMm"] = _enforce_boundary_risk_threshold_floors(params["riskThresholdsMm"])
    params["riskMarginTreMm"] = risk_margin_tre_mm
    params["riskMarginInflation"] = {
        "source": "public_atlas_landmark_tre",
        "marginMm": risk_margin_tre_mm,
        "appliedTo": sorted(TRE_INFLATED_RISK_THRESHOLD_KEYS),
        "baseMinRiskDistanceMm": params["baseMinRiskDistanceMm"],
        "effectiveMinRiskDistanceMm": round(float(params["minRiskDistanceMm"]), 6),
    }
    params["uncertaintyCalibration"] = {
        "entrySigmaMm": float(params["entrySigmaMm"]),
        "angleSigmaDeg": float(params["angleSigmaDeg"]),
        "depthSigmaMm": float(params["depthSigmaMm"]),
        "riskDistanceSigmaMm": float(params["riskDistanceSigmaMm"]),
        "riskMarginTreMm": float(params["riskMarginTreMm"]),
        "sourceMap": dynamic_unc_source,
        "dynamicProfile": {
            "source": dynamic_uncertainty_profile.get("source"),
            "residualMm": dynamic_uncertainty_profile.get("residualMm"),
            "jacobianStd": dynamic_uncertainty_profile.get("jacobianStd"),
        },
    }

    w_sum = (
        params["objectiveCoverageWeight"]
        + params["objectiveSafetyWeight"]
        + params["objectiveAngleWeight"]
    )
    if w_sum <= 1e-9:
        params["objectiveCoverageWeight"] = 0.5
        params["objectiveSafetyWeight"] = 0.3
        params["objectiveAngleWeight"] = 0.2
    else:
        params["objectiveCoverageWeight"] = params["objectiveCoverageWeight"] / w_sum
        params["objectiveSafetyWeight"] = params["objectiveSafetyWeight"] / w_sum
        params["objectiveAngleWeight"] = params["objectiveAngleWeight"] / w_sum

    has_explicit_weights = (
        "objectiveCoverageWeight" in params_in
        and "objectiveSafetyWeight" in params_in
        and "objectiveAngleWeight" in params_in
    )
    if (not has_explicit_weights) and planner_calib_weights:
        params["objectiveCoverageWeight"] = float(planner_calib_weights.get("coverage", params["objectiveCoverageWeight"]))
        params["objectiveSafetyWeight"] = float(planner_calib_weights.get("safety", params["objectiveSafetyWeight"]))
        params["objectiveAngleWeight"] = float(planner_calib_weights.get("angle", params["objectiveAngleWeight"]))
        has_explicit_weights = True
    if not has_explicit_weights:
        preset_weights = {
            "balanced": (0.50, 0.30, 0.20),
            "coverage_first": (0.68, 0.22, 0.10),
            "safety_first": (0.25, 0.65, 0.10),
            "angle_first": (0.30, 0.20, 0.50),
        }
        if params.get("objectivePreset") in preset_weights:
            c_w, s_w, a_w = preset_weights[params["objectivePreset"]]
            params["objectiveCoverageWeight"] = float(c_w)
            params["objectiveSafetyWeight"] = float(s_w)
            params["objectiveAngleWeight"] = float(a_w)

    depth_um = _require_number(
        "probeParams.depthUm", probe_in.get("depthUm", params_in.get("depthUm", 10000)),
        minimum=500, maximum=50000
    )
    entry_spacing_um = _require_number(
        "probeParams.entrySpacingUm", probe_in.get("entrySpacingUm", 3500), minimum=50, maximum=20000
    )
    shank_width_um = _require_number(
        "probeParams.shankWidthUm", probe_in.get("shankWidthUm", 70), minimum=10, maximum=500
    )
    shank_count = int(
        _require_number(
            "probeParams.shanks",
            probe_in.get("shanks", probe_in.get("shankCount", 1)),
            minimum=1,
            maximum=16,
        )
    )
    shank_pitch_um = _require_number(
        "probeParams.shankPitchUm",
        probe_in.get("shankPitchUm", probe_in.get("shankSpacingUm", 250 if shank_count > 1 else 0)),
        minimum=0,
        maximum=2000,
    )
    probe_params = dict(probe_in)
    probe_params["depthUm"] = depth_um
    probe_params["entrySpacingUm"] = entry_spacing_um
    probe_params["shankWidthUm"] = shank_width_um
    probe_params["probeRadiusUm"] = max(5.0, shank_width_um / 2.0)
    probe_params["radiusUm"] = probe_params["probeRadiusUm"]
    probe_params["shanks"] = int(shank_count)
    probe_params["shankCount"] = int(shank_count)
    probe_params["shankPitchUm"] = float(shank_pitch_um)
    probe_params["shankSpacingUm"] = float(shank_pitch_um)
    params["depthUm"] = depth_um

    return {
        "targetKeys": target_keys,
        "params": params,
        "probeParams": probe_params,
        "constraintModes": modes,
        "correctionFactors": corr,
        "correctionApplied": corr_applied,
        "correctionUncertainty": corr_unc,
        "clinicalContext": clinical_context,
        "plannerCalibration": planner_calib,
    }


def _normalize_trajectory(traj, index):
    if not isinstance(traj, dict):
        raise ContractError(f"trajectories[{index}] must be an object")
    normalized = {
        "ap_mm": _require_number(f"trajectories[{index}].ap_mm", traj.get("ap_mm", 0), minimum=-20, maximum=20),
        "ml_mm": _require_number(f"trajectories[{index}].ml_mm", traj.get("ml_mm", 0), minimum=-20, maximum=20),
        "entryDvCorr_mm": _require_number(
            f"trajectories[{index}].entryDvCorr_mm", traj.get("entryDvCorr_mm", 0), minimum=-20, maximum=20
        ),
        "phi_deg": _require_number(f"trajectories[{index}].phi_deg", traj.get("phi_deg", 0), minimum=0, maximum=360),
        "theta_deg": _require_number(
            f"trajectories[{index}].theta_deg", traj.get("theta_deg", 0), minimum=0, maximum=90
        ),
        "depthUm": _require_number(
            f"trajectories[{index}].depthUm", traj.get("depthUm", 10000), minimum=500, maximum=50000
        ),
        "skullTiltDeg": _require_number(
            f"trajectories[{index}].skullTiltDeg", traj.get("skullTiltDeg", 0), minimum=-30, maximum=30
        ),
        "coveredRegions": traj.get("coveredRegions", []),
    }
    if "entryDvCorr_mm" not in traj:
        normalized["entryDvCorr_mm"] = _skull_entry_dv_mm(normalized["ap_mm"], normalized["skullTiltDeg"])
    for field in ("ap_mm_corrected", "ml_mm_corrected", "entryDvCorr_mm_corrected", "depthUm_corrected"):
        if field in traj:
            normalized[field] = _require_number(f"trajectories[{index}].{field}", traj[field],
                minimum=1e-6 if field == "depthUm_corrected" else -20,
                maximum=50000 if field == "depthUm_corrected" else 20)
    return normalized


def _normalize_export_trajectory(traj, index):
    normalized = _normalize_trajectory(traj, index)
    normalized['spin_deg'] = _require_number(
        f'trajectories[{index}].spin_deg', traj.get('spin_deg', traj.get('spin', 0)))
    normalized['probeType'] = _normalize_short_text(f'trajectories[{index}].probeType', traj.get('probeType'))
    fields = {
        'shankCount': ('shankCount', 'shanks', 'nShanks'),
        'shankPitchUm': ('shankPitchUm', 'shankSpacingUm'),
        'radiusUm': ('radiusUm', 'probeRadiusUm'),
        'shankWidthUm': ('shankWidthUm',),
        'shankThicknessUm': ('shankThicknessUm',),
    }
    for key, aliases in fields.items():
        values = [_require_number(f'trajectories[{index}].{alias}', traj[alias], minimum=0)
                  for alias in aliases if traj.get(alias) is not None]
        if not values:
            continue
        value = values[0]
        if any(v != value for v in values):
            raise ContractError(f'Conflicting {key} aliases')
        if key == 'shankCount':
            if not value.is_integer() or not 1 <= value <= 64:
                raise ContractError('shankCount must be an integer in [1, 64]')
            value = int(value)
        normalized[key] = value
    return normalized


def _normalize_export_payload(raw_body):
    body = _require_dict("requestBody", raw_body)
    trajs_raw = body.get("trajectories", [])
    if not isinstance(trajs_raw, list) or not trajs_raw:
        raise ContractError("trajectories must be a non-empty array")
    trajectories = [_normalize_export_trajectory(t, i) for i, t in enumerate(trajs_raw)]
    corr, corr_applied = _normalize_correction_factors(body.get("correctionFactors", {}))
    corr_unc = _normalize_correction_uncertainty(body.get("correctionUncertainty"))
    probe_type = _normalize_short_text('probeType', body.get('probeType'))
    return {
        "trajectories": trajectories,
        "probeType": probe_type,
        "correctionFactors": corr,
        "correctionApplied": corr_applied,
        "correctionUncertainty": corr_unc,
    }


def _normalize_coverage_payload(raw_body):
    body = _require_dict("requestBody", raw_body)
    trajs_raw = body.get("trajectories", [])
    if not isinstance(trajs_raw, list):
        raise ContractError("trajectories must be an array")
    trajectories = [_normalize_trajectory(t, i) for i, t in enumerate(trajs_raw)]
    target_keys = body.get("targetKeys", [])
    if not isinstance(target_keys, list):
        raise ContractError("targetKeys must be an array")
    _auto_register_regions_if_needed(target_keys)
    unknown = [k for k in target_keys if k not in PLANNER_REGIONS]
    if unknown:
        raise ContractError(
            "Unknown target regions",
            details=[f"Unknown target region: {k}" for k in unknown],
        )
    return {"trajectories": trajectories, "targetKeys": target_keys}


def _normalize_contact_probe_payload(raw_probe, index):
    if not isinstance(raw_probe, dict):
        raise ContractError(f"probes[{index}] must be an object")

    ap_mm = raw_probe.get("ap_mm_corrected", raw_probe.get("ap_mm", raw_probe.get("ap", 0.0)))
    ml_mm = raw_probe.get("ml_mm_corrected", raw_probe.get("ml_mm", raw_probe.get("ml", 0.0)))
    theta_deg = raw_probe.get("theta_deg", raw_probe.get("theta", 0.0))
    phi_deg = raw_probe.get("phi_deg", raw_probe.get("phi", 0.0))
    depth_um = raw_probe.get("depthUm_corrected", raw_probe.get("depthUm", 0.0))
    entry_dv_corr_mm = raw_probe.get(
        "entryDvCorr_mm_corrected",
        raw_probe.get("entryDvCorr_mm", None),
    )
    skull_tilt_deg = raw_probe.get("skullTiltDeg", 0.0)
    ap_mm = _require_number(f"probes[{index}].ap_mm", ap_mm, minimum=-20.0, maximum=20.0)
    skull_tilt_deg = _require_number(f"probes[{index}].skullTiltDeg", skull_tilt_deg, minimum=-30.0, maximum=30.0)
    if entry_dv_corr_mm is None:
        entry_dv_corr_mm = _skull_entry_dv_mm(ap_mm, skull_tilt_deg)
    radius_um = raw_probe.get("radiusUm", 35.0)
    shank_count = raw_probe.get("shankCount", raw_probe.get("shanks", raw_probe.get("nShanks", 1)))
    shank_spacing_um = raw_probe.get(
        "shankSpacingUm",
        raw_probe.get("shankPitchUm", raw_probe.get("shankPitch", 250 if str(raw_probe.get("probeType", "")).lower().find("4sh") >= 0 else 0)),
    )
    shank_width_um = raw_probe.get("shankWidthUm", raw_probe.get("shankWidth", None))

    return {
        "ap_mm": ap_mm,
        "ml_mm": _require_number(f"probes[{index}].ml_mm", ml_mm, minimum=-20.0, maximum=20.0),
        "theta_deg": _require_number(
            f"probes[{index}].theta_deg", theta_deg, minimum=0.0, maximum=90.0
        ),
        "phi_deg": _require_number(
            f"probes[{index}].phi_deg", phi_deg, minimum=-720.0, maximum=720.0
        ),
        "spin_deg": _probe_spin_deg(raw_probe),
        "depthUm": _require_number(
            f"probes[{index}].depthUm", depth_um, minimum=100.0, maximum=50000.0
        ),
        "entryDvCorr_mm": _require_number(
            f"probes[{index}].entryDvCorr_mm",
            entry_dv_corr_mm,
            minimum=-20.0,
            maximum=20.0,
        ),
        "skullTiltDeg": skull_tilt_deg,
        "radiusUm": _require_number(
            f"probes[{index}].radiusUm",
            radius_um,
            minimum=0.0,
            maximum=1000.0,
        ),
        "probeRadiusUm": _require_number(
            f"probes[{index}].probeRadiusUm",
            raw_probe.get("probeRadiusUm", radius_um),
            minimum=0.0,
            maximum=1000.0,
        ),
        "shanks": int(
            _require_number(
                f"probes[{index}].shanks",
                shank_count,
                minimum=1.0,
                maximum=16.0,
            )
        ),
        "shankCount": int(
            _require_number(
                f"probes[{index}].shankCount",
                shank_count,
                minimum=1.0,
                maximum=16.0,
            )
        ),
        "shankSpacingUm": _require_number(
            f"probes[{index}].shankSpacingUm",
            shank_spacing_um,
            minimum=0.0,
            maximum=2000.0,
        ),
        "shankPitchUm": _require_number(
            f"probes[{index}].shankPitchUm",
            shank_spacing_um,
            minimum=0.0,
            maximum=2000.0,
        ),
        **(
            {
                "shankWidthUm": _require_number(
                    f"probes[{index}].shankWidthUm",
                    shank_width_um,
                    minimum=0.0,
                    maximum=1000.0,
                )
            }
            if shank_width_um is not None
            else {}
        ),
        "probeType": str(raw_probe.get("probeType", "")).strip(),
        "coveredRegions": raw_probe.get("coveredRegions", []),
    }


def _normalize_contact_profile_payload(raw_body):
    body = _require_dict("requestBody", raw_body)
    probes_raw = body.get("probes", [])
    if not isinstance(probes_raw, list):
        raise ContractError("probes must be an array")
    if not probes_raw:
        return {
            "probes": [],
            "stepUm": 25.0,
            "minContactLengthUm": 0.0,
            "contactMode": "cylinder",
        }
    probes = [_normalize_contact_probe_payload(item, idx) for idx, item in enumerate(probes_raw)]
    step_um = _require_number("stepUm", body.get("stepUm", 25.0), minimum=5.0, maximum=500.0)
    min_contact_length_um = _require_number(
        "minContactLengthUm",
        body.get("minContactLengthUm", 0.0),
        minimum=0.0,
        maximum=20000.0,
    )
    contact_mode = str(body.get("contactMode", "cylinder")).strip().lower()
    if contact_mode not in ("centerline", "cylinder"):
        contact_mode = "cylinder"
    return {
        "probes": probes,
        "stepUm": float(step_um),
        "minContactLengthUm": float(min_contact_length_um),
        "contactMode": contact_mode,
    }


def _normalize_landmark_payload(raw_body):
    body = _require_dict("requestBody", raw_body)
    lm = _require_dict("landmarks", body.get("landmarks", {}))

    def _point(name, fallback):
        value = lm.get(name, fallback)
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ContractError(f"landmarks.{name} must be [x,y,z]")
        return np.array(
            [
                _require_number(f"landmarks.{name}[0]", value[0]),
                _require_number(f"landmarks.{name}[1]", value[1]),
                _require_number(f"landmarks.{name}[2]", value[2]),
            ],
            dtype=float,
        )

    return {
        "bregma": _point("bregma", [0, 0, 0]),
        "lambda": _point("lambda", [-4, 0, 0]),
        "mlRef": _point("mlRef", [0, 0, 2]),
        "sigmaMm": _require_number(
            "landmarks.sigmaMm",
            body.get("sigmaMm", lm.get("sigmaMm", 0.10)),
            minimum=0.005,
            maximum=2.0,
        ),
    }


def _apply_correction(ap_mm, ml_mm, dv_mm, corr):
    return (
        ap_mm * corr["ap"] + corr["apShift"],
        ml_mm * corr["ml"] + corr["mlShift"],
        dv_mm * corr["dv"] + corr["dvShift"],
    )


def _interval_from_uncertainty(raw_value, scale_ci, shift_ci):
    if scale_ci is None or shift_ci is None:
        return None
    candidates = [
        raw_value * scale_ci[0] + shift_ci[0],
        raw_value * scale_ci[0] + shift_ci[1],
        raw_value * scale_ci[1] + shift_ci[0],
        raw_value * scale_ci[1] + shift_ci[1],
    ]
    return [float(min(candidates)), float(max(candidates))]


def _build_coordinate_error_interval(raw_ap, raw_ml, raw_dv, corr_unc):
    if not corr_unc:
        return None
    out = {}
    ap_interval = _interval_from_uncertainty(raw_ap, corr_unc.get("apScaleCi95"), corr_unc.get("apShiftCi95"))
    ml_interval = _interval_from_uncertainty(raw_ml, corr_unc.get("mlScaleCi95"), corr_unc.get("mlShiftCi95"))
    dv_interval = _interval_from_uncertainty(raw_dv, corr_unc.get("dvScaleCi95"), corr_unc.get("dvShiftCi95"))
    if ap_interval is not None:
        out["apCi95"] = [round(ap_interval[0], 4), round(ap_interval[1], 4)]
    if ml_interval is not None:
        out["mlCi95"] = [round(ml_interval[0], 4), round(ml_interval[1], 4)]
    if dv_interval is not None:
        out["dvCi95"] = [round(dv_interval[0], 4), round(dv_interval[1], 4)]
    return out or None


def _effective_risk_threshold_payload_for_probe(probe, fallback_threshold_mm):
    fallback_threshold_mm = float(max(0.0, fallback_threshold_mm))
    risk_diag = probe.get("riskDiagnostics") if isinstance(probe.get("riskDiagnostics"), dict) else {}
    class_min = risk_diag.get("classMinDistanceMm") if isinstance(risk_diag.get("classMinDistanceMm"), dict) else {}
    class_thr = risk_diag.get("classThresholdMm") if isinstance(risk_diag.get("classThresholdMm"), dict) else {}
    if not class_min:
        return {
            "riskClass": "default",
            "thresholdMm": round(fallback_threshold_mm, 6),
            "distanceMm": _as_float_or_none(probe.get("minSafetyDistanceMm")),
            "marginMm": None,
            "thresholdSource": "minRiskDistanceMm",
        }

    worst = None
    ignored_entry_classes = []
    for risk_class, raw_dist in class_min.items():
        risk_class_key = str(risk_class or "").strip().lower()
        if risk_class_key in ENTRY_CORRIDOR_RISK_CLASSES:
            ignored_entry_classes.append(str(risk_class))
            continue
        dist = _as_float_or_none(raw_dist)
        if dist is None:
            continue
        thr = _as_float_or_none(class_thr.get(risk_class))
        if thr is None:
            thr = _as_float_or_none(class_thr.get("default"))
        if thr is None:
            thr = fallback_threshold_mm
        margin = float(dist) - float(thr)
        row = {
            "riskClass": str(risk_class),
            "thresholdMm": round(float(max(0.0, thr)), 6),
            "distanceMm": round(float(dist), 6),
            "marginMm": round(float(margin), 6),
            "thresholdSource": f"riskThresholdsMm.{risk_class}",
        }
        if worst is None or margin < float(worst.get("marginMm", 0.0)):
            worst = row
    if worst is not None:
        return worst

    if ignored_entry_classes:
        distance = _as_float_or_none(probe.get("minSafetyDistanceMm"))
        if distance is None:
            distance = _as_float_or_none(probe.get("minDistanceMm"))
        if distance is None:
            distance = 0.0
        return {
            "riskClass": "entry_corridor_boundary",
            "thresholdMm": 0.0,
            "distanceMm": round(float(max(0.0, distance)), 6),
            "marginMm": round(float(max(0.0, distance)), 6),
            "thresholdSource": "entryCorridorBoundaryIgnored",
            "ignoredEntryCorridorClasses": sorted(set(ignored_entry_classes)),
        }

    return {
        "riskClass": "default",
        "thresholdMm": round(fallback_threshold_mm, 6),
        "distanceMm": _as_float_or_none(probe.get("minSafetyDistanceMm")),
        "marginMm": None,
        "thresholdSource": "minRiskDistanceMm",
    }


def _compute_probe_uncertainty_metrics(probe, target_keys, params):
    if not target_keys:
        return None

    robust_samples = int(max(4, params.get("robustSamples", 16)))
    entry_sigma_mm = float(params.get("entrySigmaMm", params.get("perturbSigmaMm", 0.15)))
    angle_sigma_deg = float(params.get("angleSigmaDeg", 2.0))
    depth_sigma_mm = float(params.get("depthSigmaMm", 0.2))
    risk_distance_sigma_mm = float(
        params.get("riskDistanceSigmaMm", RISK_DISTANCE_SIGMA_MM_DEFAULT)
    )
    risk_threshold_payload = _effective_risk_threshold_payload_for_probe(
        probe,
        float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
    )
    risk_threshold_mm = float(risk_threshold_payload.get("thresholdMm", MIN_RISK_DISTANCE_MM_DEFAULT))

    ap0 = float(probe.get("ap_mm_corrected", probe.get("ap_mm", 0.0)))
    ml0 = float(probe.get("ml_mm_corrected", probe.get("ml_mm", 0.0)))
    theta0 = float(probe.get("theta_deg", 0.0))
    phi0 = float(probe.get("phi_deg", 0.0))
    depth_mm0 = float(probe.get("depthUm_corrected", probe.get("depthUm", 0.0))) / 1000.0
    skull_tilt = float(probe.get("skullTiltDeg", 0.0))
    entry_dv0 = _probe_entry_dv_mm(probe)
    max_angle = float(params.get("maxAngle", 15.0))

    target_ids = {k: PLANNER_REGIONS[k]["id"] for k in target_keys if k in PLANNER_REGIONS}
    if not target_ids:
        return None

    region_signs = []
    for key in (probe.get("coveredRegions", []) or []):
        reg = PLANNER_REGIONS.get(key)
        if not reg:
            continue
        v = float(reg.get("ml", 0.0))
        if abs(v) >= 0.2:
            region_signs.append(1.0 if v > 0 else -1.0)
    has_hemi_expectation = bool(region_signs)
    expected_sign = 1.0 if sum(region_signs) >= 0 else -1.0

    rng = np.random.default_rng(
        _stable_seed_from_values(
            "probe_uncertainty",
            probe.get("id", ""),
            round(ap0, 4),
            round(ml0, 4),
            round(theta0, 3),
            round(phi0, 3),
            robust_samples,
            entry_sigma_mm,
            angle_sigma_deg,
            depth_sigma_mm,
        )
    )

    cover_fracs = []
    pass_count = 0
    min_margins = []
    risk_distances = []
    tip_pts = []

    for _ in range(robust_samples):
        d_ap, d_ml, d_dv = rng.normal(0.0, entry_sigma_mm, size=3)
        d_theta = float(rng.normal(0.0, angle_sigma_deg))
        d_phi = float(rng.normal(0.0, angle_sigma_deg * 1.5))
        d_depth = float(rng.normal(0.0, depth_sigma_mm))

        ap_p = ap0 + float(d_ap)
        ml_p = ml0 + float(d_ml)
        theta_p = max(0.0, theta0 + d_theta)
        phi_p = (phi0 + d_phi) % 360.0
        depth_mm_p = max(0.2, depth_mm0 + d_depth)
        depth_um_p = depth_mm_p * 1000.0

        entry_um, tip_um, direction, entry_dv_p = make_trajectory_xyz(
            ap_p, ml_p, theta_p, phi_p, depth_um_p, skull_tilt,
            entry_dv_corr_mm=entry_dv0 + float(d_dv)
        )
        tip_ap, tip_dv = _tip_ap_dv_mm(ap_p, entry_dv_p, direction, depth_um_p)
        min_margin = min(
            ap_p - ENTRY_AP_MIN_MM,
            ENTRY_AP_MAX_MM - ap_p,
            tip_ap - TIP_POST_LIMIT_MM,
            MAX_DV_MM - tip_dv,
        )
        min_margins.append(float(min_margin))

        bounds_ok = (
            ENTRY_AP_MIN_MM <= ap_p <= ENTRY_AP_MAX_MM
            and tip_ap >= TIP_POST_LIMIT_MM
            and tip_dv <= MAX_DV_MM
        )
        angle_ok = theta_p <= max_angle + 1e-9
        hemi_ok = (not has_hemi_expectation) or ((ml_p > 0) == (expected_sign > 0))
        if bounds_ok and angle_ok and hemi_ok:
            pass_count += 1

        vox = sample_trajectory_voxels_cached(ap_p, ml_p, theta_p, phi_p, depth_um_p, skull_tilt,
                                             step_um=25.0, entry_dv_corr_mm=entry_dv_p)
        profile = compute_trajectory_profile(vox, list(target_ids.values()), step_um=25.0)
        covered_n = sum(1 for sid in target_ids.values() if profile.get(sid, {}).get("covered"))
        cover_fracs.append(covered_n / max(1, len(target_ids)))
        risk_dist = _risk_distance_from_voxels_mm(vox)
        if risk_dist is not None:
            # Optional measurement/calibration noise for risk-distance readout.
            risk_dist = max(0.0, float(risk_dist + rng.normal(0.0, risk_distance_sigma_mm)))
            risk_distances.append(risk_dist)

        tip_arr = np.array(tip_um, dtype=float)
        tip_pts.append(
            [
                (tip_arr[2] - BREGMA_UM[2]) / 1000.0,
                (tip_arr[1] - BREGMA_UM[1]) / 1000.0,
                (BREGMA_UM[0] - tip_arr[0]) / 1000.0,
            ]
        )

    frac_arr = np.array(cover_fracs, dtype=float) if cover_fracs else np.array([0.0], dtype=float)
    margin_arr = np.array(min_margins, dtype=float) if min_margins else np.array([0.0], dtype=float)
    risk_arr = np.array(risk_distances, dtype=float) if risk_distances else None
    tip_arr = np.array(tip_pts, dtype=float) if tip_pts else np.zeros((1, 3), dtype=float)
    tip_std = np.std(tip_arr, axis=0)

    ci_lo = float(np.percentile(frac_arr, 2.5))
    ci_hi = float(np.percentile(frac_arr, 97.5))
    pass_rate = float(pass_count / max(1, robust_samples))
    tip_sigma_mm = float(np.linalg.norm(tip_std))

    out = {
        "coverageMean": round(float(np.mean(frac_arr)), 6),
        "coverageCi95": [round(ci_lo, 6), round(ci_hi, 6)],
        "passRate": round(pass_rate, 6),
        "minSafetyMarginMmCi95": [
            round(float(np.percentile(margin_arr, 2.5)), 6),
            round(float(np.percentile(margin_arr, 97.5)), 6),
        ],
        "tipSigmaAxesMm": [round(float(v), 6) for v in tip_std.tolist()],
        "tipSigmaMm": round(tip_sigma_mm, 6),
        "entrySigmaMm": round(entry_sigma_mm, 6),
        "angleSigmaDeg": round(angle_sigma_deg, 6),
        "depthSigmaMm": round(depth_sigma_mm, 6),
        "coneHalfAngleDeg": round(max(0.1, angle_sigma_deg * 1.75), 6),
    }
    if risk_arr is not None and risk_arr.size:
        out["minSafetyDistanceMmMean"] = round(float(np.mean(risk_arr)), 6)
        out["minSafetyDistanceMmCi95"] = [
            round(float(np.percentile(risk_arr, 2.5)), 6),
            round(float(np.percentile(risk_arr, 97.5)), 6),
        ]
        pass_cnt = int(np.sum(risk_arr >= risk_threshold_mm))
        prob_ci = _wilson_ci95(pass_cnt, int(risk_arr.size))
        out["safetyPassProbability"] = round(pass_cnt / float(risk_arr.size), 6)
        out["safetyPassProbabilityCi95"] = [round(float(prob_ci[0]), 6), round(float(prob_ci[1]), 6)]
        out["safetyThresholdMm"] = round(risk_threshold_mm, 6)
        out["safetyThresholdClass"] = risk_threshold_payload.get("riskClass")
        out["safetyThresholdSource"] = risk_threshold_payload.get("thresholdSource")
        out["riskDistanceSigmaMm"] = round(risk_distance_sigma_mm, 6)
    else:
        deterministic_min = probe.get("minSafetyDistanceMm")
        if deterministic_min is None:
            deterministic_min = _compute_probe_min_risk_distance_mm(probe, step_um=RISK_DIAG_STEP_UM)
        if deterministic_min is not None:
            deterministic_min = float(deterministic_min)
            pass_cnt = 1 if deterministic_min >= risk_threshold_mm else 0
            prob_ci = _wilson_ci95(pass_cnt, 1)
            out["minSafetyDistanceMmMean"] = round(deterministic_min, 6)
            out["minSafetyDistanceMmCi95"] = [round(deterministic_min, 6), round(deterministic_min, 6)]
            out["safetyPassProbability"] = float(pass_cnt)
            out["safetyPassProbabilityCi95"] = [
                round(float(prob_ci[0]), 6),
                round(float(prob_ci[1]), 6),
            ]
            out["safetyThresholdMm"] = round(risk_threshold_mm, 6)
            out["safetyThresholdClass"] = risk_threshold_payload.get("riskClass")
            out["safetyThresholdSource"] = risk_threshold_payload.get("thresholdSource")
            out["riskDistanceSigmaMm"] = round(risk_distance_sigma_mm, 6)
            out["riskDistanceFallback"] = True
    return out


def _apply_selective_conformal(selected, params):
    enabled = bool(params.get("selectiveConformalEnabled", False))
    alpha = float(params.get("selectiveConformalAlpha", SELECTIVE_CONFORMAL_ALPHA_DEFAULT))
    alpha = min(0.49, max(0.01, alpha))
    review_thr = float(
        params.get("selectiveReviewProbThreshold", SELECTIVE_REVIEW_PROB_THRESHOLD_DEFAULT)
    )
    reject_thr = float(
        params.get("selectiveRejectProbThreshold", SELECTIVE_REJECT_PROB_THRESHOLD_DEFAULT)
    )
    max_ci_width = float(
        params.get("selectiveReviewMaxProbCiWidth", SELECTIVE_REVIEW_MAX_PROB_CI_WIDTH_DEFAULT)
    )
    max_ci_width = min(1.0, max(0.05, max_ci_width))
    if review_thr <= reject_thr:
        review_thr = min(0.99, reject_thr + 0.2)

    if not selected:
        return {
            "enabled": enabled,
            "alpha": round(alpha, 6),
            "reviewProbThreshold": round(review_thr, 6),
            "rejectProbThreshold": round(reject_thr, 6),
            "reviewMaxProbCiWidth": round(max_ci_width, 6),
            "nCalib": 0,
            "qHatMm": None,
            "decisionCounts": {"accept": 0, "review_required": 0, "reject": 0},
        }

    # Selection-aware calibration proxy:
    # compute non-conformity on the selected set and apply quantile inflation to min-distance.
    nonconformity = []
    for probe in selected:
        conf = probe.get("safetyDistanceConfidence") if isinstance(probe.get("safetyDistanceConfidence"), dict) else {}
        threshold = conf.get("thresholdMm", params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT))
        try:
            threshold = float(threshold)
        except Exception:
            threshold = float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT))
        mean_dist = probe.get("minSafetyDistanceMeanMm", probe.get("minSafetyDistanceMm"))
        if mean_dist is None:
            continue
        mean_dist = float(mean_dist)
        nonconformity.append(max(0.0, threshold - mean_dist))

    q_hat = 0.0
    if enabled and nonconformity:
        vals = np.sort(np.asarray(nonconformity, dtype=np.float64))
        n = vals.size
        q_idx = int(math.ceil((n + 1) * (1.0 - alpha)) - 1)
        q_idx = max(0, min(q_idx, n - 1))
        q_hat = float(vals[q_idx])

    counts = {"accept": 0, "review_required": 0, "reject": 0}
    for probe in selected:
        conf = probe.get("safetyDistanceConfidence") if isinstance(probe.get("safetyDistanceConfidence"), dict) else {}
        threshold = conf.get("thresholdMm", params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT))
        try:
            threshold = float(threshold)
        except Exception:
            threshold = float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT))
        prob = conf.get("probability", None)
        try:
            prob = float(prob) if prob is not None else None
        except Exception:
            prob = None
        prob_ci = conf.get("ci95") if isinstance(conf, dict) else None
        prob_ci_width = None
        if isinstance(prob_ci, (list, tuple)) and len(prob_ci) >= 2:
            lo = _as_float_or_none(prob_ci[0])
            hi = _as_float_or_none(prob_ci[1])
            if lo is not None and hi is not None:
                prob_ci_width = float(abs(hi - lo))
        prob_guaranteed = max(0.0, (prob if prob is not None else 0.0) - alpha)
        mean_dist = probe.get("minSafetyDistanceMeanMm", probe.get("minSafetyDistanceMm"))
        mean_dist = float(mean_dist) if mean_dist is not None else None
        guaranteed_lb = (mean_dist - q_hat) if mean_dist is not None else None
        review_reasons = []
        if prob is None:
            review_reasons.append("missing_safety_probability")
        elif prob < review_thr:
            review_reasons.append("low_safety_probability")
        if prob_ci_width is not None and prob_ci_width > max_ci_width:
            review_reasons.append("wide_probability_ci")
        if guaranteed_lb is not None and guaranteed_lb < threshold:
            review_reasons.append("post_selection_lower_bound_below_threshold")

        if prob is not None and prob <= reject_thr:
            decision = "reject"
        elif (
            guaranteed_lb is not None
            and guaranteed_lb >= threshold
            and prob_guaranteed >= review_thr
            and not review_reasons
        ):
            decision = "accept"
        else:
            decision = "review_required"
        counts[decision] = counts.get(decision, 0) + 1

        sc_payload = {
            "enabled": enabled,
            "alpha": round(alpha, 6),
            "qHatMm": round(q_hat, 6),
            "guaranteedLowerBoundMm": round(float(guaranteed_lb), 6) if guaranteed_lb is not None else None,
            "probabilityGuaranteed": round(float(prob_guaranteed), 6),
            "reviewProbThreshold": round(review_thr, 6),
            "rejectProbThreshold": round(reject_thr, 6),
            "reviewMaxProbCiWidth": round(max_ci_width, 6),
            "probabilityCiWidth": round(float(prob_ci_width), 6) if prob_ci_width is not None else None,
            "decision": decision,
            "reviewReasons": review_reasons,
        }
        probe["selectiveConformal"] = sc_payload
        probe["manualReviewRequired"] = decision != "accept"
        probe["confidenceStatus"] = "acceptable" if decision == "accept" else (
            "rejected" if decision == "reject" else "low_confidence_review"
        )
        probe["reviewReason"] = ";".join(review_reasons) if review_reasons else (
            "selective_conformal_reject" if decision == "reject" else None
        )
        if isinstance(conf, dict):
            conf["postSelection"] = sc_payload
            probe["safetyDistanceConfidence"] = conf

    return {
        "enabled": enabled,
        "alpha": round(alpha, 6),
        "reviewProbThreshold": round(review_thr, 6),
        "rejectProbThreshold": round(reject_thr, 6),
        "reviewMaxProbCiWidth": round(max_ci_width, 6),
        "nCalib": int(len(nonconformity)),
        "qHatMm": round(float(q_hat), 6),
        "decisionCounts": counts,
    }


def _threshold_source_label(risk_thresholds, risk_class):
    if isinstance(risk_thresholds, dict) and risk_class in risk_thresholds:
        return f"riskThresholdsMm.{risk_class}"
    return "riskThresholdsMm.default(minRiskDistanceMm)"


def _flatten_risk_class_distance_fields(class_min):
    out = {field: None for field in RISK_CLASS_OUTPUT_DISTANCE_FIELD.values()}
    if not isinstance(class_min, dict):
        return out
    for risk_class, field in RISK_CLASS_OUTPUT_DISTANCE_FIELD.items():
        raw = class_min.get(risk_class)
        if raw is None and risk_class == "cortex_outside":
            raw = class_min.get("outer_tissue")
        try:
            out[field] = round(float(raw), 6) if raw is not None else None
        except Exception:
            out[field] = None
    return out


def _most_dangerous_segment_payload(risk_diag):
    if not isinstance(risk_diag, dict):
        return None
    segments = risk_diag.get("dangerSegmentsTop3")
    if isinstance(segments, list) and segments:
        seg = segments[0]
        if isinstance(seg, dict):
            return {
                "startMm": seg.get("startDepthMm"),
                "endMm": seg.get("endDepthMm"),
                "minDistanceMm": seg.get("minDistanceMm"),
                "thresholdMm": seg.get("thresholdMm"),
                "hitStructureClass": seg.get("hitStructureClass"),
                "severityScore": seg.get("severityScore"),
                "sampleCount": seg.get("sampleCount"),
            }
    return None


def _threshold_source_from_risk_diag(risk_diag, risk_thresholds):
    risk_class = None
    most_dangerous = _most_dangerous_segment_payload(risk_diag)
    if isinstance(most_dangerous, dict):
        risk_class = most_dangerous.get("hitStructureClass")
    if risk_class is None and isinstance(risk_diag, dict):
        risk_class = risk_diag.get("nearestSourceClass")
    if str(risk_class or "").strip().lower() in ENTRY_CORRIDOR_RISK_CLASSES:
        return "entryCorridorBoundaryIgnored"
    return _threshold_source_label(risk_thresholds, risk_class)


def _candidate_min_adjustment_delta(probe, cand):
    try:
        theta_a = float(probe.get("theta_deg", 0.0))
        theta_b = float(cand.get("theta_deg", 0.0))
        phi_a = float(probe.get("phi_deg", 0.0))
        phi_b = float(cand.get("phi_deg", 0.0))
        ap_a = float(probe.get("ap_mm", 0.0))
        ap_b = float(cand.get("ap_mm", 0.0))
        ml_a = float(probe.get("ml_mm", 0.0))
        ml_b = float(cand.get("ml_mm", 0.0))
        d_a = float(probe.get("depthUm", 0.0))
        d_b = float(cand.get("depthUm", 0.0))
    except Exception:
        return None
    delta_angle_deg = math.sqrt((theta_b - theta_a) ** 2 + (phi_b - phi_a) ** 2)
    delta_ap_um = (ap_b - ap_a) * 1000.0
    delta_ml_um = (ml_b - ml_a) * 1000.0
    delta_entry_um = math.hypot((ap_b - ap_a) * 1000.0, (ml_b - ml_a) * 1000.0)
    delta_depth_um = abs(d_b - d_a)
    weighted_cost = delta_entry_um + 140.0 * delta_angle_deg + 0.25 * delta_depth_um
    return {
        "deltaAngleDeg": round(float(delta_angle_deg), 6),
        "deltaApUm": round(float(delta_ap_um), 3),
        "deltaMlUm": round(float(delta_ml_um), 3),
        "deltaEntryUm": round(float(delta_entry_um), 3),
        "deltaDepthUm": round(float(delta_depth_um), 3),
        "weightedCost": round(float(weighted_cost), 6),
    }


def _closest_feasible_delta_from_adjustment(min_adjustment, target_subset=None):
    if not isinstance(min_adjustment, dict):
        return None

    def _num(key, default=0.0):
        try:
            return float(min_adjustment.get(key, default) or default)
        except Exception:
            return float(default)

    delta_depth_um = _num("deltaDepthUm")
    # A positive depth delta means the nearest feasible candidate differs in
    # depth; for a failure card the actionable conservative direction is
    # reported as a retreat magnitude.
    return {
        "entryShiftMm": round(_num("deltaEntryUm") / 1000.0, 6),
        "apShiftMm": round(_num("deltaApUm") / 1000.0, 6),
        "mlShiftMm": round(_num("deltaMlUm") / 1000.0, 6),
        "angleDeltaDeg": round(_num("deltaAngleDeg"), 6),
        "depthRetreatMm": round(abs(delta_depth_um) / 1000.0, 6),
        "targetSubset": list(target_subset or []),
    }


def _closest_feasible_delta_from_alternative(alternative_reason, target_subset=None):
    if not isinstance(alternative_reason, dict):
        return None
    nearest = alternative_reason.get("nearestFeasible")
    if not isinstance(nearest, dict):
        return None
    min_adjustment = nearest.get("minAdjustment")
    out = _closest_feasible_delta_from_adjustment(min_adjustment, target_subset=target_subset)
    if out is None:
        return None
    out["candidateId"] = nearest.get("candidateId")
    out["candidateSignature"] = nearest.get("candidateSignature")
    out["source"] = "nearest_feasible_candidate"
    if isinstance(nearest.get("geometry"), dict):
        out["geometry"] = deepcopy(nearest.get("geometry"))
    return out


def _build_failure_alternative_reason(
    probe_idx,
    probe,
    selected,
    candidates,
    params,
    constraint_modes,
    min_clearance,
    min_entry,
    risk_thresholds,
):
    if not candidates:
        return None
    current_sig = _candidate_signature(probe)
    selected_others = [p for i, p in enumerate(selected) if i != probe_idx]
    current_cov = set(probe.get("coveredRegions", []) or [])
    nearest_feasible = None
    nearest_feasible_cost = None
    first_reason_payload = None
    ranked_alts = sorted(
        candidates,
        key=lambda c: (
            -float(c.get("explainScore", 0.0)),
            int(c.get("paretoRank", 10**6)),
            _candidate_signature(c),
        ),
    )
    for cand in ranked_alts:
        if _candidate_signature(cand) == current_sig:
            continue
        cand_cov = set(cand.get("coveredRegions", []) or [])
        if current_cov and not (cand_cov & current_cov):
            continue

        reason_code = None
        reason_en = None
        reason_cn = None
        collides = any(trajs_collide(cand, s, min_clearance, min_entry) for s in selected_others)
        hemisphere_bad = constraint_modes.get("hemisphere") == "enforce" and has_hemisphere_mismatch(cand)
        risk_bad = False
        cand_min = None
        cand_thr = None
        cand_cls = None

        if collides:
            reason_code = "collision"
            reason_en = "Rejected because it collides with other selected trajectories"
            reason_cn = "未选中：与其他已选轨迹发生碰撞"
        elif hemisphere_bad:
            reason_code = "hemisphere_mismatch"
            reason_en = "Rejected because hemisphere constraint would be violated"
            reason_cn = "未选中：会违反半球约束"
        else:
            risk_diag = cand.get("riskDiagnostics")
            if not isinstance(risk_diag, dict):
                risk_diag = _compute_probe_risk_diagnostics(
                    cand,
                    step_um=RISK_DIAG_STEP_UM,
                    threshold_mm=float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
                    threshold_by_class=risk_thresholds,
                    cvar_alpha=float(params.get("drccCvarAlpha", DRCC_CVAR_ALPHA_DEFAULT)),
                )
                if risk_diag is not None:
                    cand["riskDiagnostics"] = risk_diag
            cand_min = cand.get("minSafetyDistanceMm")
            if cand_min is None and isinstance(risk_diag, dict):
                cand_min = risk_diag.get("minDistanceMm")
            if cand_min is None:
                cand_min = _compute_probe_min_risk_distance_mm(cand, step_um=RISK_DIAG_STEP_UM)
            cand_cls = risk_diag.get("nearestSourceClass") if isinstance(risk_diag, dict) else None
            cand_thr = resolve_risk_threshold_mm(risk_thresholds, cand_cls)
            if cand_min is not None and float(cand_min) < float(cand_thr):
                risk_bad = True
                reason_code = "risk_threshold"
                reason_en = (
                    f"Rejected because min safety distance {float(cand_min):.3f} mm "
                    f"is below threshold {float(cand_thr):.3f} mm"
                )
                reason_cn = (
                    f"未选中：最小安全距离 {float(cand_min):.3f} mm "
                    f"低于阈值 {float(cand_thr):.3f} mm"
                )
            elif len(cand_cov) < len(current_cov):
                reason_code = "coverage_loss"
                reason_en = (
                    f"Rejected because coverage drops ({len(cand_cov)} vs {len(current_cov)} regions)"
                )
                reason_cn = f"未选中：覆盖下降（{len(cand_cov)} 对 {len(current_cov)} 脑区）"
            else:
                reason_code = "lower_score"
                reason_en = "Rejected by Pareto/explainable score ordering"
                reason_cn = "未选中：Pareto/可解释评分排序更低"

        if not collides and not hemisphere_bad and not risk_bad:
            delta = _candidate_min_adjustment_delta(probe, cand)
            if delta is not None:
                score = float(delta.get("weightedCost", 10**9))
                if nearest_feasible_cost is None or score < nearest_feasible_cost:
                    nearest_feasible_cost = score
                    nearest_feasible = {
                        "candidateId": cand.get("id"),
                        "candidateSignature": list(_candidate_signature(cand)),
                        "candidateExplainScore": round(float(cand.get("explainScore", 0.0)), 6),
                        "candidateParetoRank": int(cand.get("paretoRank", 10**6)),
                        "minSafetyDistanceMm": round(float(cand_min), 6) if cand_min is not None else None,
                        "riskThresholdMm": round(float(cand_thr), 6) if cand_thr is not None else None,
                        "riskClass": cand_cls,
                        "minAdjustment": {
                            "deltaAngleDeg": delta["deltaAngleDeg"],
                            "deltaApUm": delta["deltaApUm"],
                            "deltaMlUm": delta["deltaMlUm"],
                            "deltaEntryUm": delta["deltaEntryUm"],
                            "deltaDepthUm": delta["deltaDepthUm"],
                        },
                        "geometry": {
                            "ap_mm": round(float(cand.get("ap_mm_corrected", cand.get("ap_mm", 0.0))), 6),
                            "ml_mm": round(float(cand.get("ml_mm_corrected", cand.get("ml_mm", 0.0))), 6),
                            "theta_deg": round(float(cand.get("theta_deg", 0.0)), 6),
                            "phi_deg": round(float(cand.get("phi_deg", 0.0)), 6),
                            "depthUm": round(float(cand.get("depthUm_corrected", cand.get("depthUm", 0.0))), 3),
                        },
                    }

        if first_reason_payload is None:
            first_reason_payload = {
                "candidateId": cand.get("id"),
                "candidateSignature": list(_candidate_signature(cand)),
                "candidateExplainScore": round(float(cand.get("explainScore", 0.0)), 6),
                "candidateParetoRank": int(cand.get("paretoRank", 10**6)),
                "reasonCode": reason_code,
                "reasonEn": reason_en,
                "reasonCn": reason_cn,
            }

    if first_reason_payload is None and nearest_feasible is None:
        return None
    out = first_reason_payload or {
        "candidateId": None,
        "candidateSignature": None,
        "candidateExplainScore": None,
        "candidateParetoRank": None,
        "reasonCode": "no_ranked_alternative",
        "reasonEn": "No ranked alternative candidate was available",
        "reasonCn": "没有可排序的备选轨迹",
    }
    out["nearestFeasible"] = nearest_feasible
    return out


def _build_failure_counterfactual_suggestions(
    probe,
    evaluated_min_risk,
    threshold_mm,
    risk_diag=None,
    alternative_reason=None,
):
    out = []
    try:
        min_r = float(evaluated_min_risk)
        thr = float(threshold_mm)
    except Exception:
        return out

    if min_r < thr:
        out.append(
            {
                "parameter": "minRiskDistanceMm",
                "current": round(thr, 6),
                "requiredAtMost": round(min_r, 6),
                "deltaMm": round(thr - min_r, 6),
                "reason": "Reduce safety threshold for this risk class/profile",
            }
        )

    theta = float(probe.get("theta_deg", 0.0))
    if theta > 0.0:
        out.append(
            {
                "parameter": "maxAngle",
                "current": round(theta, 6),
                "requiredAtMost": round(max(0.0, theta - 1.0), 6),
                "deltaDeg": 1.0,
                "reason": "Reduce tilt angle to increase separation from nearby risk structures",
            }
        )

    if isinstance(risk_diag, dict):
        segs = risk_diag.get("dangerSegmentsTop3")
        if isinstance(segs, list) and segs:
            top = segs[0]
            if isinstance(top, dict) and top.get("startDepthMm") is not None:
                start_depth = float(top.get("startDepthMm"))
                out.append(
                    {
                        "parameter": "depthUm",
                        "current": round(float(probe.get("depthUm", 0.0)), 3),
                        "requiredAtMost": round(max(0.2, start_depth - 0.05) * 1000.0, 3),
                        "reason": "Shorten depth to avoid highest-risk segment",
                    }
                )

    nearest_feasible = None
    if isinstance(alternative_reason, dict):
        nearest_feasible = (
            alternative_reason.get("nearestFeasible")
            if isinstance(alternative_reason.get("nearestFeasible"), dict)
            else None
        )
    if nearest_feasible:
        min_adj = nearest_feasible.get("minAdjustment") if isinstance(nearest_feasible.get("minAdjustment"), dict) else {}
        delta_angle = float(min_adj.get("deltaAngleDeg", 0.0) or 0.0)
        delta_ap = float(min_adj.get("deltaApUm", 0.0) or 0.0)
        delta_ml = float(min_adj.get("deltaMlUm", 0.0) or 0.0)
        delta_entry = float(min_adj.get("deltaEntryUm", 0.0) or 0.0)
        delta_depth = float(min_adj.get("deltaDepthUm", 0.0) or 0.0)
        out.append(
            {
                "parameter": "nearestFeasibleCandidate",
                "candidateId": nearest_feasible.get("candidateId"),
                "candidateSignature": nearest_feasible.get("candidateSignature"),
                "geometry": nearest_feasible.get("geometry"),
                "deltaAngleDeg": round(delta_angle, 6),
                "deltaApUm": round(delta_ap, 3),
                "deltaMlUm": round(delta_ml, 3),
                "deltaEntryUm": round(delta_entry, 3),
                "deltaDepthUm": round(delta_depth, 3),
                "reason": "Closest feasible alternative under current constraints",
            }
        )
        if delta_angle > 0:
            theta_now = float(probe.get("theta_deg", 0.0) or 0.0)
            out.append(
                {
                    "parameter": "theta_deg",
                    "current": round(theta_now, 6),
                    "deltaDeg": round(delta_angle, 6),
                    "reason": "Rotate toward nearest feasible solution",
                }
            )
        if delta_entry > 0:
            out.append(
                {
                    "parameter": "entryOffsetUm",
                    "deltaUm": round(delta_entry, 3),
                    "reason": "Translate entry point to nearest feasible solution",
                }
            )
        if delta_depth > 0:
            out.append(
                {
                    "parameter": "depthUm",
                    "current": round(float(probe.get("depthUm", 0.0)), 3),
                    "deltaUm": round(delta_depth, 3),
                    "reason": "Adjust insertion depth to nearest feasible solution",
            }
        )
    return out


def _build_brain_bounds_failure_card(
    *,
    probe_idx,
    probe,
    brain_mask_summary,
    selected,
    candidates,
    params,
    constraint_modes,
    min_clearance,
    min_entry,
    risk_thresholds,
):
    summary = brain_mask_summary if isinstance(brain_mask_summary, dict) else {}
    reason = str(summary.get("reason") or "trajectory_exits_brain")
    step_um = float(summary.get("stepUm", RISK_DIAG_STEP_UM) or RISK_DIAG_STEP_UM)
    segment = summary.get("maxOutsideGapSegmentMm")
    if not (isinstance(segment, list) and len(segment) == 2):
        sample_count = int(summary.get("sampleCount", 0) or 0)
        last_inside = summary.get("lastInsideIndex")
        depth_um = float(probe.get("depthUm_corrected", probe.get("depthUm", 0.0)) or 0.0)
        if reason == "tip_outside_brain" and last_inside is not None and sample_count > 0:
            start_mm = max(0.0, float(int(last_inside)) * step_um / 1000.0)
            end_mm = max(start_mm, float(sample_count - 1) * step_um / 1000.0)
            segment = [round(start_mm, 6), round(end_mm, 6)]
        else:
            segment = [0.0, round(max(0.0, depth_um / 1000.0), 6)]

    threshold_mm = round(float(summary.get("outsideGapUmThreshold", BRAIN_BOUNDS_OUTSIDE_GAP_UM) or 0.0) / 1000.0, 6)
    alternative_reason = _build_failure_alternative_reason(
        probe_idx=probe_idx,
        probe=probe,
        selected=selected,
        candidates=candidates,
        params=params,
        constraint_modes=constraint_modes,
        min_clearance=min_clearance,
        min_entry=min_entry,
        risk_thresholds=risk_thresholds,
    )
    closest_delta = _closest_feasible_delta_from_alternative(
        alternative_reason,
        target_subset=probe.get("coveredRegions", []),
    )
    fallback_delta = None
    if closest_delta is None:
        last_inside = summary.get("lastInsideIndex")
        if last_inside is not None:
            safe_depth_mm = max(0.0, float(int(last_inside)) * step_um / 1000.0)
            depth_mm = float(probe.get("depthUm_corrected", probe.get("depthUm", 0.0)) or 0.0) / 1000.0
            retreat_mm = max(0.0, depth_mm - safe_depth_mm)
            fallback_delta = {
                "entryShiftMm": 0.0,
                "apShiftMm": 0.0,
                "mlShiftMm": 0.0,
                "angleDeltaDeg": 0.0,
                "depthRetreatMm": round(float(retreat_mm), 6),
                "targetSubset": list(probe.get("coveredRegions", []) or []),
                "source": "brain_mask_last_inside_depth",
            }

    return {
        "probeIdx": int(probe_idx),
        "constraint": "brainBounds",
        "segmentDepthMm": segment,
        "segmentRangeMm": segment,
        "segmentSampleCount": int(summary.get("sampleCount", 0) or 0),
        "hitStructureClass": "brain_outside",
        "hitSource": "brain_mask",
        "brainBoundsReason": reason,
        "tipInside": bool(summary.get("tipInside", False)),
        "minDistanceMm": 0.0,
        "evaluatedMinDistanceMm": 0.0,
        "thresholdMm": threshold_mm,
        "thresholdSource": "brainBounds.outsideGapUmThreshold",
        "maxOutsideGapMm": round(float(summary.get("maxOutsideGapUm", 0.0) or 0.0) / 1000.0, 6),
        "brainMaskSummary": summary,
        "alternativeCandidate": alternative_reason,
        "closestFeasibleDelta": closest_delta or fallback_delta,
        "counterfactualPassIf": [
            {
                "parameter": "depthUm",
                "deltaUm": round(float((closest_delta or fallback_delta or {}).get("depthRetreatMm", 0.0)) * 1000.0, 3),
                "reason": "Retreat depth until the tip and path remain inside the brain mask",
            }
        ],
    }


def _build_candidate_pool_failure_card(
    *,
    candidate_universe,
    candidate_pool,
    target_keys,
    params,
    probe_params,
    constraint_modes,
    funnel_diagnostics=None,
    enforced_filter_summary=None,
):
    funnel = funnel_diagnostics if isinstance(funnel_diagnostics, dict) else {}
    counts = funnel.get("rejectionCounts") if isinstance(funnel.get("rejectionCounts"), dict) else {}
    top_reason = None
    if counts:
        top_reason = max(counts.items(), key=lambda item: int(item[1] or 0))[0]
    if not top_reason and isinstance(enforced_filter_summary, dict):
        reasons = enforced_filter_summary.get("reasons") if isinstance(enforced_filter_summary.get("reasons"), dict) else {}
        if reasons:
            top_reason = max(reasons.items(), key=lambda item: int(item[1] or 0))[0]
    top_reason = str(top_reason or "no_feasible_candidate")

    candidates = list(candidate_pool or []) or list(candidate_universe or [])
    best = None
    best_key = None
    for cand in candidates:
        info = cand.get("riskPrefilter") if isinstance(cand.get("riskPrefilter"), dict) else {}
        try:
            margin = float(info.get("worstMarginMm", -1e9) if info.get("worstMarginMm") is not None else -1e9)
        except Exception:
            margin = -1e9
        key = (
            margin,
            len(cand.get("coveredRegions", []) or []),
            float(cand.get("explainScore", 0.0) or 0.0),
        )
        if best_key is None or key > best_key:
            best_key = key
            best = cand

    base_card = {
        "probeIdx": None,
        "constraint": "candidateFeasibility",
        "reasonCode": top_reason,
        "targetSubset": list(target_keys or []),
        "segmentDepthMm": None,
        "segmentRangeMm": None,
        "minDistanceMm": None,
        "evaluatedMinDistanceMm": None,
        "thresholdMm": None,
        "thresholdSource": None,
        "closestFeasibleDelta": None,
        "counterfactualPassIf": [],
        "candidateFunnel": deepcopy(funnel.get("funnel", {})) if isinstance(funnel.get("funnel"), dict) else {},
    }
    if not isinstance(best, dict):
        return base_card

    base_card["probeIdx"] = int(best.get("id", 0) or 0)
    base_card["candidateSignature"] = list(_candidate_signature(best))
    base_card["targetSubset"] = list(best.get("coveredRegions", []) or target_keys or [])

    if top_reason in ("out_of_brain", "brainBounds", "tip_outside_brain", "trajectory_exits_brain_midway"):
        summary = _candidate_brain_mask_path_summary(best, step_um=RISK_DIAG_STEP_UM)
        if isinstance(summary, dict) and summary.get("available") and not summary.get("passed", True):
            return _build_brain_bounds_failure_card(
                probe_idx=int(best.get("id", 0) or 0),
                probe=best,
                brain_mask_summary=summary,
                selected=[best],
                candidates=candidates,
                params=params,
                constraint_modes=constraint_modes,
                min_clearance=float(params.get("minClearance", 1000)),
                min_entry=float(probe_params.get("entrySpacingUm", 3500)),
                risk_thresholds=sanitize_risk_thresholds(
                    params.get("riskThresholdsMm"),
                    default_mm=float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
                ),
            )

    threshold_by_class = sanitize_risk_thresholds(
        params.get("riskThresholdsMm"),
        default_mm=float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
    )
    risk_diag = _compute_probe_risk_diagnostics(
        best,
        step_um=RISK_DIAG_STEP_UM,
        threshold_mm=float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
        threshold_by_class=threshold_by_class,
    )
    if isinstance(risk_diag, dict):
        hit_class = risk_diag.get("nearestSourceClass")
        threshold_mm = resolve_risk_threshold_mm(threshold_by_class, hit_class)
        danger = risk_diag.get("dangerSegmentsTop3") if isinstance(risk_diag.get("dangerSegmentsTop3"), list) else []
        seg = danger[0] if danger and isinstance(danger[0], dict) else {}
        start = seg.get("startDepthMm")
        end = seg.get("endDepthMm")
        min_signed = risk_diag.get("minSignedDistanceMm")
        min_dist = risk_diag.get("minDistanceMm")
        evaluated = min_signed if min_signed is not None else min_dist
        base_card.update(
            {
                "constraint": "continuousRiskDistance"
                if top_reason in ("risk_distance", "ventricle_hit", "out_of_brain", "continuousRiskDistance")
                else "candidateFeasibility",
                "hitStructureClass": hit_class,
                "hitSource": risk_diag.get("nearestSource"),
                "segmentDepthMm": [start, end] if start is not None and end is not None else None,
                "segmentRangeMm": [start, end] if start is not None and end is not None else None,
                "minDistanceMm": round(float(min_dist), 6) if min_dist is not None else None,
                "minSignedDistanceMm": round(float(min_signed), 6) if min_signed is not None else None,
                "evaluatedMinDistanceMm": round(float(evaluated), 6) if evaluated is not None else None,
                "thresholdMm": round(float(threshold_mm), 6),
                "thresholdSource": _threshold_source_label(threshold_by_class, hit_class),
                "dangerSegmentsTop3": danger[:3],
            }
        )
        if start is not None:
            depth_um = float(best.get("depthUm", best.get("depthUm_corrected", 0.0)) or 0.0)
            retreat_mm = max(0.0, depth_um / 1000.0 - max(0.0, float(start) - 0.05))
            base_card["closestFeasibleDelta"] = {
                "entryShiftMm": 0.0,
                "apShiftMm": 0.0,
                "mlShiftMm": 0.0,
                "angleDeltaDeg": 0.0,
                "depthRetreatMm": round(float(retreat_mm), 6),
                "targetSubset": list(best.get("coveredRegions", []) or target_keys or []),
                "source": "nearest_rejected_candidate_depth_retreat",
            }
            base_card["counterfactualPassIf"].append(
                {
                    "parameter": "depthUm",
                    "deltaUm": round(float(retreat_mm) * 1000.0, 3),
                    "reason": "Retreat before the highest-risk segment and rerun planning",
                }
            )
    return base_card


def _collect_safety_issues(
    selected,
    params,
    probe_params,
    constraint_modes,
    candidates=None,
):
    issues = []
    max_angle = params.get("maxAngle", 15)
    min_clearance = params.get("minClearance", 1000)
    min_entry = probe_params.get("entrySpacingUm", 3500)
    min_risk_distance_mm = float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT))
    risk_thresholds = sanitize_risk_thresholds(
        params.get("riskThresholdsMm"),
        default_mm=min_risk_distance_mm,
    )
    continuous_risk_policy = _continuous_risk_policy(constraint_modes)

    for idx, probe in enumerate(selected):
        theta = float(probe.get("theta_deg", 0))
        ap_check = float(probe.get("ap_mm_corrected", probe.get("ap_mm", 0)))
        mode = constraint_modes["maxAngle"]
        if mode != "off" and theta > max_angle:
            issues.append({
                "probeIdx": idx,
                "constraint": "maxAngle",
                "mode": mode,
                "msgEn": f"Tilt angle {theta:.2f} exceeds maxAngle={max_angle:.2f}",
                "msgCn": f"倾角 {theta:.2f}° 超过最大角度 {max_angle:.2f}°",
            })
        mode = constraint_modes["lambdaBoundary"]
        if mode != "off" and ap_check < LAMBDA_AP_MM:
            issues.append({
                "probeIdx": idx,
                "constraint": "lambdaBoundary",
                "mode": mode,
                "msgEn": f"AP {ap_check:.3f} mm is posterior to lambda boundary {LAMBDA_AP_MM:.1f} mm",
                "msgCn": f"AP {ap_check:.3f} mm 位于 lambda 边界 {LAMBDA_AP_MM:.1f} mm 之后",
            })

        mode = constraint_modes["brainBounds"]
        if mode != "off":
            tip_ap, tip_dv = trajectory_tip_mm(probe)
            if ap_check < ENTRY_AP_MIN_MM or ap_check > ENTRY_AP_MAX_MM:
                issues.append({
                    "probeIdx": idx,
                    "constraint": "brainBounds",
                    "mode": mode,
                    "msgEn": (
                        f"Entry AP {ap_check:.3f} mm outside safe range "
                        f"[{ENTRY_AP_MIN_MM:.1f}, {ENTRY_AP_MAX_MM:.1f}] mm"
                    ),
                    "msgCn": (
                        f"入点 AP {ap_check:.3f} mm 超出安全范围 "
                        f"[{ENTRY_AP_MIN_MM:.1f}, {ENTRY_AP_MAX_MM:.1f}] mm"
                    ),
                })
            if tip_ap < TIP_POST_LIMIT_MM:
                issues.append({
                    "probeIdx": idx,
                    "constraint": "brainBounds",
                    "mode": mode,
                    "msgEn": (
                        f"Tip AP {tip_ap:.3f} mm is posterior to brain limit "
                        f"{TIP_POST_LIMIT_MM:.1f} mm"
                    ),
                    "msgCn": f"探针尖端 AP {tip_ap:.3f} mm 超出后界 {TIP_POST_LIMIT_MM:.1f} mm",
                })
            if tip_dv > MAX_DV_MM:
                issues.append({
                    "probeIdx": idx,
                    "constraint": "brainBounds",
                    "mode": mode,
                    "msgEn": (
                        f"Tip DV {tip_dv:.3f} mm exceeds max brain depth {MAX_DV_MM:.1f} mm"
                    ),
                    "msgCn": f"探针尖端 DV {tip_dv:.3f} mm 超出最大深度 {MAX_DV_MM:.1f} mm",
                })
            brain_mask_summary = _candidate_brain_mask_path_summary(
                probe,
                step_um=RISK_DIAG_STEP_UM,
            )
            if brain_mask_summary.get("available") and not brain_mask_summary.get("passed", True):
                reason = str(brain_mask_summary.get("reason") or "trajectory_exits_brain")
                max_gap_um = float(brain_mask_summary.get("maxOutsideGapUm", 0.0))
                tip_inside = bool(brain_mask_summary.get("tipInside", False))
                failure_card = _build_brain_bounds_failure_card(
                    probe_idx=idx,
                    probe=probe,
                    brain_mask_summary=brain_mask_summary,
                    selected=selected,
                    candidates=candidates,
                    params=params,
                    constraint_modes=constraint_modes,
                    min_clearance=min_clearance,
                    min_entry=min_entry,
                    risk_thresholds=risk_thresholds,
                )
                issues.append({
                    "probeIdx": idx,
                    "constraint": "brainBounds",
                    "mode": mode,
                    "brainMaskSummary": brain_mask_summary,
                    "failureCard": failure_card,
                    "msgEn": (
                        f"Trajectory violates brain mask continuity "
                        f"(reason={reason}, tipInside={tip_inside}, maxOutsideGapUm={max_gap_um:.1f})"
                    ),
                    "msgCn": (
                        f"轨迹违反脑掩膜连续性（原因={reason}，"
                        f"tipInside={tip_inside}，最大出脑间隙={max_gap_um:.1f}um）"
                    ),
                })
            min_risk = probe.get("minSafetyDistanceMm")
            risk_diag = probe.get("riskDiagnostics")
            if not isinstance(risk_diag, dict):
                risk_diag = _compute_probe_risk_diagnostics(
                    probe,
                    step_um=RISK_DIAG_STEP_UM,
                    threshold_mm=min_risk_distance_mm,
                    threshold_by_class=risk_thresholds,
                    cvar_alpha=float(params.get("drccCvarAlpha", DRCC_CVAR_ALPHA_DEFAULT)),
                )
                if risk_diag is not None:
                    probe["riskDiagnostics"] = risk_diag
            if min_risk is None and isinstance(risk_diag, dict):
                min_risk = risk_diag.get("minDistanceMm")
            if min_risk is None:
                min_risk = _compute_probe_min_risk_distance_mm(probe, step_um=RISK_DIAG_STEP_UM)
            if min_risk is not None:
                probe["minSafetyDistanceMm"] = round(float(min_risk), 6)
            signed_min_risk = None
            nearest_risk_class = None
            if isinstance(risk_diag, dict):
                nearest_risk_class = risk_diag.get("nearestSourceClass")
                signed_raw = risk_diag.get("minSignedDistanceMm")
                if signed_raw is not None:
                    try:
                        signed_min_risk = float(signed_raw)
                    except Exception:
                        signed_min_risk = None
            effective_threshold_mm = resolve_risk_threshold_mm(risk_thresholds, nearest_risk_class)
            evaluated_min_risk = float(min_risk) if min_risk is not None else None
            if signed_min_risk is not None:
                evaluated_min_risk = (
                    signed_min_risk
                    if evaluated_min_risk is None
                    else min(float(evaluated_min_risk), float(signed_min_risk))
                )
            continuous_risk_mode = continuous_risk_policy["effectiveMode"]
            if continuous_risk_mode == "enforce" and evaluated_min_risk is None:
                issues.append({
                    "probeIdx": idx,
                    "constraint": "continuousRiskDistance",
                    "mode": continuous_risk_mode,
                    "requestedMode": continuous_risk_policy["requestedMode"],
                    "modeAdjusted": bool(
                        continuous_risk_policy["downgraded"]
                        or continuous_risk_policy.get("sourceLimited")
                    ),
                    "modeReason": continuous_risk_policy["reason"],
                    "failureCard": {
                        "probeIdx": idx,
                        "constraint": "continuousRiskDistance",
                        "hitStructureClass": None,
                        "hitSource": "risk_model_unavailable",
                        "minDistanceMm": None,
                        "evaluatedMinDistanceMm": None,
                        "thresholdMm": float(effective_threshold_mm),
                        "thresholdByClassMm": risk_thresholds,
                        "thresholdSource": _threshold_source_label(risk_thresholds, nearest_risk_class),
                        "riskModelEnabled": bool(continuous_risk_policy.get("riskModelEnabled")),
                        "modeReason": continuous_risk_policy["reason"],
                    },
                    "msgEn": (
                        "Continuous risk distance is enforced, but no SDF/risk distance "
                        "diagnostic was available for this trajectory"
                    ),
                    "msgCn": "连续风险距离已设为强制，但该轨迹没有可用的 SDF/风险距离诊断",
                })
            if (
                continuous_risk_mode != "off"
                and evaluated_min_risk is not None
                and float(evaluated_min_risk) < float(effective_threshold_mm)
            ):
                if _risk_class_is_entry_corridor_only(
                    probe,
                    risk_diag,
                    nearest_risk_class,
                    step_um=RISK_DIAG_STEP_UM,
                ):
                    probe["entryCorridorRiskIgnored"] = {
                        "riskClass": str(nearest_risk_class or ""),
                        "evaluatedMinDistanceMm": round(float(evaluated_min_risk), 6),
                        "thresholdMm": round(float(effective_threshold_mm), 6),
                        "reason": "initial_entry_corridor_is_handled_by_brainBounds",
                    }
                else:
                    danger_segments = (
                        risk_diag.get("dangerSegmentsTop3", [])
                        if isinstance(risk_diag, dict)
                        else []
                    )
                    danger_segments = [seg for seg in danger_segments if isinstance(seg, dict)]
                    segments = (
                        risk_diag.get("thresholdSegmentsMm", [])
                        if isinstance(risk_diag, dict)
                        else []
                    )
                    if danger_segments:
                        seg = danger_segments[0]
                    elif segments:
                        seg = segments[0]
                    else:
                        d = float(risk_diag.get("closestDepthMm", 0.0)) if isinstance(risk_diag, dict) else 0.0
                        seg = {
                            "startDepthMm": round(max(0.0, d - 0.05), 6),
                            "endDepthMm": round(d + 0.05, 6),
                            "sampleCount": 1,
                        }
                    seg_start = seg.get("startDepthMm")
                    seg_end = seg.get("endDepthMm")
                    if seg_start is None or seg_end is None:
                        d = float(risk_diag.get("closestDepthMm", 0.0)) if isinstance(risk_diag, dict) else 0.0
                        seg_start = round(max(0.0, d - 0.05), 6)
                        seg_end = round(d + 0.05, 6)
                    threshold_source = _threshold_source_label(risk_thresholds, nearest_risk_class)
                    alternative_reason = _build_failure_alternative_reason(
                        probe_idx=idx,
                        probe=probe,
                        selected=selected,
                        candidates=candidates,
                        params=params,
                        constraint_modes=constraint_modes,
                        min_clearance=min_clearance,
                        min_entry=min_entry,
                        risk_thresholds=risk_thresholds,
                    )
                    counterfactual_suggestions = _build_failure_counterfactual_suggestions(
                        probe=probe,
                        evaluated_min_risk=evaluated_min_risk,
                        threshold_mm=effective_threshold_mm,
                        risk_diag=risk_diag,
                        alternative_reason=alternative_reason,
                    )
                    failure_card = {
                        "probeIdx": idx,
                        "constraint": "continuousRiskDistance",
                        "segmentDepthMm": [seg_start, seg_end],
                        "segmentRangeMm": [seg_start, seg_end],
                        "segmentSampleCount": int(seg.get("sampleCount", 0)),
                        "hitStructureClass": (
                            risk_diag.get("nearestSourceClass")
                            if isinstance(risk_diag, dict)
                            else None
                        ),
                        "hitSource": (
                            risk_diag.get("nearestSource")
                            if isinstance(risk_diag, dict)
                            else None
                        ),
                        "minDistanceMm": round(float(min_risk), 6) if min_risk is not None else None,
                        "minSignedDistanceMm": round(float(signed_min_risk), 6)
                        if signed_min_risk is not None
                        else None,
                        "evaluatedMinDistanceMm": round(float(evaluated_min_risk), 6),
                        "thresholdMm": round(float(effective_threshold_mm), 6),
                        "thresholdByClassMm": (
                            risk_diag.get("classThresholdMm")
                            if isinstance(risk_diag, dict)
                            else risk_thresholds
                        ),
                        "thresholdSource": threshold_source,
                        "dangerSegmentsTop3": danger_segments[:3],
                        "alternativeCandidate": alternative_reason,
                        "closestFeasibleDelta": _closest_feasible_delta_from_alternative(
                            alternative_reason,
                            target_subset=probe.get("coveredRegions", []),
                        ),
                        "counterfactualPassIf": counterfactual_suggestions,
                    }
                    issues.append({
                        "probeIdx": idx,
                        "constraint": "continuousRiskDistance",
                        "mode": continuous_risk_mode,
                        "requestedMode": continuous_risk_policy["requestedMode"],
                        "modeAdjusted": bool(
                            continuous_risk_policy["downgraded"]
                            or continuous_risk_policy.get("sourceLimited")
                        ),
                        "modeReason": continuous_risk_policy["reason"],
                        "failureCard": failure_card,
                        "msgEn": (
                            f"Continuous min safety distance {float(evaluated_min_risk):.3f} mm is below "
                            f"threshold {float(effective_threshold_mm):.3f} mm; "
                            f"hit={failure_card.get('hitStructureClass') or 'unknown'} "
                            f"segment={float(seg_start):.3f}-{float(seg_end):.3f} mm; "
                            f"thresholdSource={threshold_source}"
                        ),
                        "msgCn": (
                            f"连续最小安全距离 {float(evaluated_min_risk):.3f} mm 低于阈值 "
                            f"{float(effective_threshold_mm):.3f} mm；阈值来源 {threshold_source}"
                        ),
                    })

        mode = constraint_modes["hemisphere"]
        if mode != "off" and has_hemisphere_mismatch(probe):
            issues.append({
                "probeIdx": idx,
                "constraint": "hemisphere",
                "mode": mode,
                "msgEn": "Entry hemisphere does not match one or more covered target regions",
                "msgCn": "入点半球与覆盖目标脑区侧别不一致",
            })

    for i in range(len(selected)):
        for j in range(i + 1, len(selected)):
            a = selected[i]
            b = selected[j]
            mode = constraint_modes["minEntrySpacing"]
            entry_surface = (
                float(np.linalg.norm(np.array(a["entry_xyz"]) - np.array(b["entry_xyz"])))
                - _probe_radius_um(a)
                - _probe_radius_um(b)
            )
            if mode != "off" and entry_surface < min_entry:
                issues.append({
                    "probeIdx": i,
                    "constraint": "minEntrySpacing",
                    "mode": mode,
                    "msgEn": (
                        f"Entry surface spacing between probe {i+1} and {j+1} is "
                        f"{entry_surface:.1f} um (< {min_entry:.1f} um)"
                    ),
                    "msgCn": f"探针{i+1}/{j+1}入点表面间距 {entry_surface:.1f} um，小于 {min_entry:.1f} um",
                })
            mode = constraint_modes["minClearance"]
            surface_clearance = trajectory_surface_clearance_um(a, b)
            if mode != "off" and surface_clearance < min_clearance:
                issues.append({
                    "probeIdx": i,
                    "constraint": "minClearance",
                    "mode": mode,
                    "msgEn": (
                        f"Continuous capsule clearance between probe {i+1} and {j+1} is "
                        f"{surface_clearance:.1f} um (< {min_clearance:.1f} um)"
                    ),
                    "msgCn": f"探针{i+1}/{j+1}连续体积最小净距 {surface_clearance:.1f} um，小于 {min_clearance:.1f} um",
                })
    return issues


def _enforce_safety_contract(issues, extra=None):
    blockers = [i for i in issues if i.get("mode") == "enforce"]
    if blockers:
        raise ContractError(
            "Safety contract blocked optimization",
            details=[f"{i['constraint']}: {i.get('msgEn', '')}" for i in blockers],
            status_code=422,
            code="safety_contract_block",
            extra=extra,
        )


def _compute_registration_uncertainty(
    ap_scale, ml_scale, dv_scale, ap_shift, ml_shift, dv_shift, bl_dist, sigma_mm=0.10
):
    sigma_mm = float(max(0.005, sigma_mm))
    rel_err = max(0.01, sigma_mm / max(bl_dist, 0.1))
    k95 = 1.96
    return {
        "sigmaMm": sigma_mm,
        "apScaleCi95": [round(max(0.0, ap_scale * (1 - k95 * rel_err)), 6), round(ap_scale * (1 + k95 * rel_err), 6)],
        "mlScaleCi95": [round(max(0.0, ml_scale * (1 - k95 * rel_err)), 6), round(ml_scale * (1 + k95 * rel_err), 6)],
        "dvScaleCi95": [round(max(0.0, dv_scale * (1 - k95 * rel_err)), 6), round(dv_scale * (1 + k95 * rel_err), 6)],
        "apShiftCi95": [round(ap_shift - k95 * sigma_mm * ap_scale, 6), round(ap_shift + k95 * sigma_mm * ap_scale, 6)],
        "mlShiftCi95": [round(ml_shift - k95 * sigma_mm * ml_scale, 6), round(ml_shift + k95 * sigma_mm * ml_scale, 6)],
        "dvShiftCi95": [round(dv_shift - k95 * sigma_mm * dv_scale, 6), round(dv_shift + k95 * sigma_mm * dv_scale, 6)],
    }


def _infer_probe_entry_side(probe):
    ml = float(probe.get("ml_mm_corrected", probe.get("ml_mm", 0.0)))
    return "right" if ml > 0 else "left"


def _candidate_region_side_map(candidate, target_keys):
    target_set = set(target_keys or [])
    out = {}

    raw = candidate.get("coveredRegionSides")
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key not in target_set:
                continue
            sides = value if isinstance(value, (list, tuple, set)) else [value]
            side_set = set()
            for side in sides:
                s = str(side).strip().lower()
                if s in ("left", "right"):
                    side_set.add(s)
            if side_set:
                out[key] = side_set

    fallback_side = _infer_probe_entry_side(candidate)
    for key in candidate.get("coveredRegions", []) or []:
        if key not in target_set:
            continue
        if key not in out:
            out[key] = {fallback_side}

    return {k: sorted(v) for k, v in out.items()}


def _required_coverage_tokens(target_keys, targeting_mode):
    if targeting_mode == "bilateral_symmetric":
        tokens = set()
        for key in target_keys:
            tokens.add(f"{key}:left")
            tokens.add(f"{key}:right")
        return tokens
    return set(target_keys)


def _candidate_coverage_tokens(candidate, target_keys, targeting_mode):
    target_set = set(target_keys or [])
    if targeting_mode != "bilateral_symmetric":
        return {k for k in (candidate.get("coveredRegions", []) or []) if k in target_set}

    side_map = _candidate_region_side_map(candidate, target_keys)
    tokens = set()
    for key, sides in side_map.items():
        for side in sides:
            tokens.add(f"{key}:{side}")
    return tokens


def _candidate_cover_identity(candidate, target_keys, targeting_mode):
    """Bind pool membership to geometry, hardware and tokens, not rounded UI controls."""
    segments = _probe_shank_segments_um(candidate)
    geometry = tuple((tuple(float(v) for v in start), tuple(float(v) for v in end), float(radius))
                     for start, end, radius, _ in segments)
    tokens = tuple(sorted(_candidate_coverage_tokens(candidate, target_keys, targeting_mode)))
    return geometry, tokens


def _final_coverage_state(selected, target_keys, targeting_mode):
    required = _required_coverage_tokens(target_keys, targeting_mode)
    covered = set().union(*(_candidate_coverage_tokens(p, target_keys, targeting_mode) for p in selected)) if selected else set()
    covered &= required
    missing = sorted(required - covered)
    sides = []
    if targeting_mode == "bilateral_symmetric":
        sides = [{"key": token.rsplit(":", 1)[0], "side": token.rsplit(":", 1)[1]} for token in missing]
        uncovered = sorted({item["key"] for item in sides})
    else:
        uncovered = missing
    return required, covered, uncovered, sides


def _bounded_exact_token_cover_selection(
    candidates,
    target_keys,
    targeting_mode,
    min_clearance,
    min_entry,
    incumbent,
    time_limit_s=5.0,
    audit=None,
    cancel=None,
):
    required = _required_coverage_tokens(target_keys, targeting_mode)
    token_sets = [_candidate_coverage_tokens(c, target_keys, targeting_mode) for c in candidates]
    indices_by_identity = {id(c): i for i, c in enumerate(candidates)}
    indices_by_signature = {_candidate_cover_identity(c, target_keys, targeting_mode): i for i, c in enumerate(candidates)}
    incumbent_indices = []
    for c in incumbent or []:
        index = indices_by_identity.get(id(c))
        if index is None:
            index = indices_by_signature.get(_candidate_cover_identity(c, target_keys, targeting_mode))
        if index is None:
            # Recovery candidates outside the supplied pool cannot support a pool certificate.
            if audit is not None:
                audit.update(status="not_run_external_incumbent", domain="supplied finite pool")
            return list(incumbent)
        incumbent_indices.append(index)
    keys = [(int(c.get("paretoRank", 10**6)), -float(c.get("explainScore", 0.0)),
             -int(sum((c.get("overlapVoxels", {}) or {}).values())), _candidate_signature(c))
            for c in candidates]
    indices, diagnostics = bounded_token_cover(
        token_sets, required,
        lambda a, b: trajs_collide(candidates[a], candidates[b], min_clearance, min_entry),
        incumbent_indices, keys, time_limit_s=time_limit_s, node_limit=100000, cancel=cancel,
    )
    if audit is not None:
        audit.update(diagnostics)
        audit["stageScope"] = "selection before auto-repair and fallback"
    return [candidates[i] for i in indices]


def _augment_selection_for_targeting_mode(selected, candidates, target_keys, targeting_mode, min_clearance, min_entry,
                                         audit=None, cancel=None):
    required_tokens = _required_coverage_tokens(target_keys, targeting_mode)
    covered_tokens = set()
    for probe in selected:
        covered_tokens |= _candidate_coverage_tokens(probe, target_keys, targeting_mode)

    if targeting_mode != "bilateral_symmetric":
        missing = sorted(required_tokens - covered_tokens)
        if required_tokens:
            exact_selected = _bounded_exact_token_cover_selection(
                candidates,
                target_keys,
                targeting_mode,
                min_clearance,
                min_entry,
                selected,
                time_limit_s=5.0,
                audit=audit,
                cancel=cancel,
            )
            exact_covered = set()
            for probe in exact_selected:
                exact_covered |= _candidate_coverage_tokens(
                    probe, target_keys, targeting_mode
                )
            if required_tokens <= exact_covered:
                selected = exact_selected
                covered_tokens = exact_covered
                missing = []
        return selected, missing, covered_tokens, required_tokens

    remaining = set(required_tokens - covered_tokens)
    selected_aug = list(selected)
    selected_id_set = {probe.get("id") for probe in selected_aug if probe.get("id") is not None}

    while remaining:
        best = None
        best_gain = set()
        best_rank = 10**9
        best_score = -1.0
        best_voxels = -1

        for cand in candidates:
            cand_id = cand.get("id")
            if cand_id is not None and cand_id in selected_id_set:
                continue
            if any(trajs_collide(cand, s, min_clearance, min_entry) for s in selected_aug):
                continue

            gain = _candidate_coverage_tokens(cand, target_keys, targeting_mode) & remaining
            if not gain:
                continue

            gain_n = len(gain)
            rank = int(cand.get("paretoRank", 10**6))
            score = float(cand.get("explainScore", 0.0))
            voxels = int(sum((cand.get("overlapVoxels", {}) or {}).values()))

            if (
                gain_n > len(best_gain)
                or (gain_n == len(best_gain) and rank < best_rank)
                or (gain_n == len(best_gain) and rank == best_rank and score > best_score)
                or (gain_n == len(best_gain) and rank == best_rank and score == best_score and voxels > best_voxels)
            ):
                best = cand
                best_gain = gain
                best_rank = rank
                best_score = score
                best_voxels = voxels

        if best is None:
            break

        selected_aug.append(best)
        if best.get("id") is not None:
            selected_id_set.add(best.get("id"))
        covered_tokens |= best_gain
        remaining -= best_gain

    missing = sorted(remaining)
    if required_tokens:
        exact_selected = _bounded_exact_token_cover_selection(
            candidates,
            target_keys,
            targeting_mode,
            min_clearance,
            min_entry,
            selected_aug,
            time_limit_s=5.0,
            audit=audit,
            cancel=cancel,
        )
        exact_covered = set()
        for probe in exact_selected:
            exact_covered |= _candidate_coverage_tokens(probe, target_keys, targeting_mode)
        if required_tokens <= exact_covered and (
            missing or len(exact_selected) < len(selected_aug)
        ):
            selected_aug = exact_selected
            covered_tokens = exact_covered
            missing = []
    return selected_aug, missing, covered_tokens, required_tokens


def _build_side_coverage_summary(selected, target_keys):
    summary = {k: {"left": False, "right": False} for k in target_keys}
    for probe in selected:
        side_map = _candidate_region_side_map(probe, target_keys)
        for key, sides in side_map.items():
            if key not in summary:
                continue
            for side in sides:
                if side in ("left", "right"):
                    summary[key][side] = True
    return summary


def _ensure_probe_safety_statistics(probe, threshold_mm):
    min_distance = probe.get("minSafetyDistanceMm")
    if min_distance is None:
        risk_diag = probe.get("riskDiagnostics")
        if isinstance(risk_diag, dict):
            min_distance = risk_diag.get("minDistanceMm")
        if min_distance is None:
            min_distance = _compute_probe_min_risk_distance_mm(probe, step_um=RISK_DIAG_STEP_UM)
        if min_distance is None:
            margin_ci = probe.get("safetyMarginCi95") or probe.get("minSafetyMarginMmCi95")
            if isinstance(margin_ci, (list, tuple)) and len(margin_ci) >= 1:
                try:
                    min_distance = max(0.0, float(margin_ci[0]))
                    probe["minSafetyDistanceFallback"] = "from_margin_ci95"
                except Exception:
                    min_distance = None
        if min_distance is None and not bool(risk_model_summary.get("enabled")):
            min_distance = float(max(0.0, threshold_mm * 2.0))
            probe["minSafetyDistanceFallback"] = "risk_model_disabled_default"
        if min_distance is not None:
            min_distance = round(float(min_distance), 6)
            probe["minSafetyDistanceMm"] = min_distance

    if min_distance is not None and "minSafetyDistanceCi95" not in probe:
        probe["minSafetyDistanceCi95"] = [float(min_distance), float(min_distance)]
    if min_distance is not None and "minSafetyDistanceMeanMm" not in probe:
        probe["minSafetyDistanceMeanMm"] = float(min_distance)

    confidence = probe.get("safetyDistanceConfidence")
    if not isinstance(confidence, dict):
        confidence = {}

    threshold_mm = round(float(threshold_mm), 6)
    confidence.setdefault("thresholdMm", threshold_mm)
    if min_distance is not None and confidence.get("probability") is None:
        pass_flag = 1 if float(min_distance) >= threshold_mm else 0
        ci = _wilson_ci95(pass_flag, 1)
        confidence["probability"] = float(pass_flag)
        confidence["ci95"] = [round(float(ci[0]), 6), round(float(ci[1]), 6)]
        confidence["fallback"] = True
    elif min_distance is not None and "ci95" not in confidence:
        prob = float(confidence.get("probability", 0.0))
        pass_flag = 1 if prob >= 0.5 else 0
        ci = _wilson_ci95(pass_flag, 1)
        confidence["ci95"] = [round(float(ci[0]), 6), round(float(ci[1]), 6)]
    if confidence:
        probe["safetyDistanceConfidence"] = confidence


def _as_float_or_none(value):
    try:
        if value is None:
            return None
        out = float(value)
        if not math.isfinite(out):
            return None
        return out
    except Exception:
        return None


def _normalize_interval_pair(interval, fallback_value):
    if isinstance(interval, (list, tuple)) and len(interval) >= 2:
        lo = _as_float_or_none(interval[0])
        hi = _as_float_or_none(interval[1])
        if lo is not None and hi is not None:
            if lo > hi:
                lo, hi = hi, lo
            return [round(float(lo), 6), round(float(hi), 6)]
    fv = _as_float_or_none(fallback_value)
    if fv is None:
        return [0.0, 0.0]
    return [round(float(fv), 6), round(float(fv), 6)]


def _build_probe_statistical_safety_payload(probe, threshold_mm, clinical_context):
    threshold_payload = _effective_risk_threshold_payload_for_probe(probe, threshold_mm)
    threshold_mm = round(float(max(0.0, threshold_payload.get("thresholdMm", threshold_mm))), 6)
    min_distance = None
    threshold_class = str(threshold_payload.get("riskClass") or "").strip().lower()
    if threshold_class and threshold_class != "default":
        min_distance = _as_float_or_none(threshold_payload.get("distanceMm"))
    if min_distance is None:
        min_distance = _as_float_or_none(probe.get("minSafetyDistanceMm"))
    if min_distance is None:
        min_distance = _as_float_or_none(probe.get("minDistanceMm"))
    if min_distance is None:
        risk_diag = probe.get("riskDiagnostics")
        if isinstance(risk_diag, dict):
            min_distance = _as_float_or_none(risk_diag.get("minDistanceMm"))
    if min_distance is None:
        min_distance = 0.0
        probe["minSafetyDistanceFallback"] = "statistical_contract_default"

    min_distance = round(float(max(0.0, min_distance)), 6)
    ci95_distance = _normalize_interval_pair(
        probe.get("minSafetyDistanceCi95") or probe.get("minDistanceCi95"),
        min_distance,
    )

    conf = probe.get("safetyDistanceConfidence") if isinstance(probe.get("safetyDistanceConfidence"), dict) else {}
    probability = _as_float_or_none(conf.get("probability"))
    if probability is None:
        probability = 1.0 if min_distance >= threshold_mm else 0.0
    probability = round(float(max(0.0, min(1.0, probability))), 6)
    ci95_probability = _normalize_interval_pair(conf.get("ci95"), probability)

    covered_regions = list(probe.get("coveredRegions") or [])
    primary_region = str(covered_regions[0]) if covered_regions else "<none>"
    ml_value = _as_float_or_none(probe.get("ml_mm_corrected", probe.get("ml_mm")))
    hemisphere = "unknown"
    if ml_value is not None:
        hemisphere = "right" if ml_value >= 0 else "left"
    batch_id = str((clinical_context or {}).get("batchId") or "unknown")
    atlas_mode = str((clinical_context or {}).get("atlasMode") or ACTIVE_ATLAS_MODE or "unknown")
    mri_mode = str(
        (clinical_context or {}).get("mriMode")
        or ("mri_ccf_hybrid" if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid" else "off")
    )
    mri_profile = str(
        (clinical_context or {}).get("mriProfile")
        or (clinical_context or {}).get("mriProfileId")
        or ((_mri_payload() or {}).get("profileId") if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid" else "off")
        or "unknown"
    )
    probe_type = str(
        probe.get("probeType")
        or probe.get("probePreset")
        or (clinical_context or {}).get("probeType")
        or "unknown"
    )
    passed = bool(min_distance >= threshold_mm)
    failure_reason = None if passed else (
        f"minDistance {min_distance:.3f} mm below threshold {threshold_mm:.3f} mm"
    )

    return {
        "minDistanceMm": min_distance,
        "minDistanceCi95": ci95_distance,
        "minDistanceByClass": deepcopy(probe.get("minDistanceByClass") or probe.get("minDistanceByClassMm") or {}),
        **_flatten_risk_class_distance_fields(probe.get("minDistanceByClass") or probe.get("minDistanceByClassMm") or {}),
        "mostDangerousSegmentMm": deepcopy(probe.get("mostDangerousSegmentMm")),
        "thresholdSource": threshold_payload.get("thresholdSource") or probe.get("thresholdSource"),
        "thresholdClass": threshold_payload.get("riskClass"),
        "thresholdMarginMm": threshold_payload.get("marginMm"),
        "pSafetyGtThreshold": probability,
        "pSafetyGtThresholdCi95": ci95_probability,
        "thresholdMm": threshold_mm,
        "pass": passed,
        "status": "pass" if passed else "fail",
        "failureReason": failure_reason,
        "fallbackUsed": bool(conf.get("fallback") or probe.get("minSafetyDistanceFallback")),
        "calibrationTags": {
            "brainRegion": primary_region,
            "hemisphere": hemisphere,
            "batch": batch_id,
            "atlasMode": atlas_mode,
            "mriMode": mri_mode,
            "mriProfile": mri_profile,
            "probeType": probe_type,
        },
    }


def _build_statistical_contract_summary(selected, stratified_calibration):
    probes = list(selected or [])
    probe_count = int(len(probes))
    required_fields = [
        "coverage",
        "minDistanceMm",
        "minDistanceByClass",
        "minDistanceCi95",
        "pSafetyGtThreshold",
        "pSafetyGtThresholdCi95",
        "entrySigmaMm",
        "angleSigmaDeg",
        "depthSigmaMm",
        "riskDistanceSigmaMm",
        "passFail",
        "manualReviewRequired",
    ]
    coverage = {field: 0 for field in required_fields}

    for probe in probes:
        stats = probe.get("statisticalSafety") if isinstance(probe.get("statisticalSafety"), dict) else {}
        if _as_float_or_none(stats.get("minDistanceMm")) is not None:
            coverage["minDistanceMm"] += 1
        if isinstance(stats.get("minDistanceCi95"), (list, tuple)) and len(stats.get("minDistanceCi95")) >= 2:
            coverage["minDistanceCi95"] += 1
        if _as_float_or_none(stats.get("pSafetyGtThreshold")) is not None:
            coverage["pSafetyGtThreshold"] += 1
        if isinstance(stats.get("pSafetyGtThresholdCi95"), (list, tuple)) and len(stats.get("pSafetyGtThresholdCi95")) >= 2:
            coverage["pSafetyGtThresholdCi95"] += 1
        if _as_float_or_none(probe.get("coverage")) is not None:
            coverage["coverage"] += 1
        if isinstance(probe.get("minDistanceByClass"), dict) and probe.get("minDistanceByClass"):
            coverage["minDistanceByClass"] += 1
        for sigma_field in ("entrySigmaMm", "angleSigmaDeg", "depthSigmaMm", "riskDistanceSigmaMm"):
            if _as_float_or_none(probe.get(sigma_field)) is not None:
                coverage[sigma_field] += 1
        if probe.get("passFail") in ("pass", "fail"):
            coverage["passFail"] += 1
        if isinstance(probe.get("manualReviewRequired"), bool):
            coverage["manualReviewRequired"] += 1

    field_coverage = {
        key: {
            "count": int(val),
            "ratio": round(float(val) / float(max(1, probe_count)), 6),
        }
        for key, val in coverage.items()
    }
    strat_dims = []
    if isinstance(stratified_calibration, dict):
        for row in stratified_calibration.get("dimensions") or []:
            if not isinstance(row, dict):
                continue
            dim = str(row.get("dimension") or "").strip()
            if dim:
                strat_dims.append(dim)
    return {
        "probeCount": probe_count,
        "requiredPerTrajectory": required_fields,
        "fieldCoverage": field_coverage,
        "stratifiedDimensions": sorted(set(strat_dims)),
    }


def _probe_coverage_rate(probe, target_keys):
    if not target_keys:
        return 0.0
    covered = set(probe.get("coveredRegions", []) or [])
    hits = sum(1 for key in target_keys if key in covered)
    return float(hits) / float(max(1, len(target_keys)))


def _calibration_bootstrap_ci(clean, bins=8, n_boot=200):
    if not clean or len(clean) < 2:
        return {
            "observedRate": None,
            "ece": None,
            "brier": None,
            "calibrationSlope": None,
        }
    pred = np.asarray([float(p) for p, _ in clean], dtype=float)
    obs = np.asarray([float(y) for _, y in clean], dtype=float)
    n = int(pred.size)
    bins = max(3, int(bins))
    n_boot = int(max(50, min(1000, n_boot)))
    rng = np.random.default_rng(_stable_seed_from_values("calibration_bootstrap", n, round(float(np.mean(pred)), 6)))

    def _stats_for_indices(idx):
        p = pred[idx]
        y = obs[idx]
        brier = float(np.mean((p - y) ** 2))
        observed = float(np.mean(y))
        bucket_ece = 0.0
        for bi in range(bins):
            lo = bi / float(bins)
            hi = (bi + 1) / float(bins)
            mask = (p >= lo) & ((p < hi) if bi < bins - 1 else (p <= hi))
            if not np.any(mask):
                continue
            bucket_ece += int(np.sum(mask)) * abs(float(np.mean(p[mask])) - float(np.mean(y[mask])))
        ece = bucket_ece / float(max(1, p.size))
        if p.size >= 2 and float(np.var(p)) > 1e-12:
            slope = float(np.cov(p, y, bias=True)[0, 1] / np.var(p))
        else:
            slope = np.nan
        return observed, ece, brier, slope

    samples = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, size=n)
        samples.append(_stats_for_indices(idx))
    arr = np.asarray(samples, dtype=float)

    def _ci(col):
        vals = arr[:, col]
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            return None
        return [
            round(float(np.percentile(vals, 2.5)), 6),
            round(float(np.percentile(vals, 97.5)), 6),
        ]

    return {
        "observedRate": _ci(0),
        "ece": _ci(1),
        "brier": _ci(2),
        "calibrationSlope": _ci(3),
    }


def _calibration_stats(points, bins=8):
    clean = []
    for p, y in points:
        try:
            pp = float(p)
            yy = int(y)
        except Exception:
            continue
        pp = max(0.0, min(1.0, pp))
        yy = 1 if yy > 0 else 0
        clean.append((pp, yy))
    if not clean:
        return {
            "count": 0,
            "meanPredicted": None,
            "observedRate": None,
            "observedRateCi95": None,
            "ece": None,
            "brier": None,
            "calibrationSlope": None,
            "calibrationIntercept": None,
            "calibrationCurve": [],
            "reliabilityCurve": [],
            "bootstrapCi95": _calibration_bootstrap_ci([]),
        }

    pred_arr_all = np.asarray([p for p, _ in clean], dtype=float)
    mean_pred = float(np.mean(pred_arr_all))
    y_arr = np.asarray([y for _, y in clean], dtype=float)
    observed = float(np.mean(y_arr))
    brier = float(np.mean(np.asarray([(p - y) ** 2 for p, y in clean], dtype=float)))
    observed_ci = _wilson_ci95(int(np.sum(y_arr)), int(len(y_arr)))
    if len(clean) >= 2 and float(np.var(pred_arr_all)) > 1e-12:
        slope = float(np.cov(pred_arr_all, y_arr, bias=True)[0, 1] / np.var(pred_arr_all))
        intercept = float(observed - slope * mean_pred)
    else:
        slope = None
        intercept = None

    bins = max(3, int(bins))
    bucket = {i: [] for i in range(bins)}
    for p, y in clean:
        idx = min(bins - 1, max(0, int(math.floor(p * bins))))
        bucket[idx].append((p, y))
    ece_num = 0.0
    for i in range(bins):
        arr = bucket.get(i) or []
        if not arr:
            continue
        p_mean = float(np.mean(np.asarray([x[0] for x in arr], dtype=float)))
        y_mean = float(np.mean(np.asarray([x[1] for x in arr], dtype=float)))
        ece_num += len(arr) * abs(p_mean - y_mean)
    ece = ece_num / max(1, len(clean))

    curve = []
    for i in range(bins):
        arr = bucket.get(i) or []
        if not arr:
            continue
        pred_arr = np.asarray([x[0] for x in arr], dtype=float)
        obs_arr = np.asarray([x[1] for x in arr], dtype=float)
        ci = _wilson_ci95(int(np.sum(obs_arr)), int(len(obs_arr)))
        curve.append(
            {
                "bin": int(i),
                "count": int(len(arr)),
                "predicted": round(float(np.mean(pred_arr)), 6),
                "observed": round(float(np.mean(obs_arr)), 6),
                "observedCi95": [round(float(ci[0]), 6), round(float(ci[1]), 6)],
            }
        )

    bootstrap_ci = _calibration_bootstrap_ci(clean, bins=bins)
    return {
        "count": int(len(clean)),
        "meanPredicted": round(mean_pred, 6),
        "observedRate": round(observed, 6),
        "observedRateCi95": [round(float(observed_ci[0]), 6), round(float(observed_ci[1]), 6)],
        "ece": round(float(ece), 6),
        "brier": round(float(brier), 6),
        "calibrationSlope": round(float(slope), 6) if slope is not None else None,
        "calibrationIntercept": round(float(intercept), 6) if intercept is not None else None,
        "calibrationCurve": curve,
        "reliabilityCurve": curve,
        "bootstrapCi95": bootstrap_ci,
    }


def _build_stratified_calibration_summary(selected, clinical_context):
    """
    Stratified reliability report for publication-grade auditing.
    Dimensions: brain_region / hemisphere / batch / atlas_mode / probe_type / mri_profile,
    plus hospital/device_model for deployment drift auditing.
    """
    groups = {}
    active_mri_profile = None
    if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid":
        try:
            active_mri = _mri_payload()
            if isinstance(active_mri, dict):
                active_mri_profile = active_mri.get("profileId") or active_mri.get("datasetProfile")
        except Exception:
            active_mri_profile = None

    def _push(group_key, p, y):
        groups.setdefault(group_key, []).append((p, y))

    for probe in selected:
        conf = probe.get("safetyDistanceConfidence") if isinstance(probe.get("safetyDistanceConfidence"), dict) else {}
        prob = conf.get("probability")
        threshold = conf.get("thresholdMm", MIN_RISK_DISTANCE_MM_DEFAULT)
        min_dist = probe.get("minSafetyDistanceMm")
        if prob is None or min_dist is None:
            continue
        try:
            p = float(prob)
            y = 1 if float(min_dist) >= float(threshold) else 0
        except Exception:
            continue
        _push(("global", "all"), p, y)

        regions = list(probe.get("coveredRegions", []) or [])
        if not regions:
            regions = ["<none>"]
        for region in regions:
            _push(("brain_region", str(region)), p, y)

        ml = probe.get("ml_mm_corrected", probe.get("ml_mm"))
        if ml is not None:
            hemi = "right" if float(ml) >= 0 else "left"
            _push(("hemisphere", hemi), p, y)
        else:
            _push(("hemisphere", "unknown"), p, y)

        hospital = clinical_context.get("hospitalId") or "unknown"
        device = clinical_context.get("deviceModel") or "unknown"
        batch = clinical_context.get("batchId") or "unknown"
        atlas_mode = clinical_context.get("atlasMode") or ACTIVE_ATLAS_MODE or "unknown"
        mri_mode = clinical_context.get("mriMode") or ("mri_ccf_hybrid" if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid" else "off")
        mri_profile = (
            clinical_context.get("mriProfile")
            or clinical_context.get("mriProfileId")
            or active_mri_profile
            or ("off" if ACTIVE_ATLAS_MODE != "mri_ccf_hybrid" else "unknown")
        )
        probe_type = probe.get("probeType") or probe.get("probePreset") or clinical_context.get("probeType") or "unknown"
        _push(("hospital", str(hospital)), p, y)
        _push(("device_model", str(device)), p, y)
        _push(("batch", str(batch)), p, y)
        _push(("atlas_mode", str(atlas_mode)), p, y)
        _push(("mri_mode", str(mri_mode)), p, y)
        _push(("mri_profile", str(mri_profile)), p, y)
        _push(("probe_type", str(probe_type)), p, y)

    global_stats = _calibration_stats(groups.get(("global", "all"), []))
    rows = []
    for (dimension, label), pts in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1])):
        if dimension == "global":
            continue
        stats = _calibration_stats(pts)
        rows.append(
            {
                "dimension": dimension,
                "label": label,
                **stats,
            }
        )
    rows.sort(key=lambda r: (0 if r["count"] else 1, -(r["count"] or 0), r["dimension"], r["label"]))
    dimension_rows = []
    by_dim = {}
    for row in rows:
        dim = str(row.get("dimension") or "")
        if not dim:
            continue
        by_dim.setdefault(dim, []).append(row)
    for dim, arr in sorted(by_dim.items(), key=lambda kv: kv[0]):
        n_total = int(sum(int(x.get("count") or 0) for x in arr))
        weighted_ece_num = 0.0
        weighted_brier_num = 0.0
        weighted_slope_num = 0.0
        for x in arr:
            n = int(x.get("count") or 0)
            ece = _as_float_or_none(x.get("ece"))
            brier = _as_float_or_none(x.get("brier"))
            slope = _as_float_or_none(x.get("calibrationSlope"))
            if n <= 0:
                continue
            if ece is not None:
                weighted_ece_num += float(n) * float(ece)
            if brier is not None:
                weighted_brier_num += float(n) * float(brier)
            if slope is not None:
                weighted_slope_num += float(n) * float(slope)
        weighted_ece = weighted_ece_num / float(max(1, n_total))
        weighted_brier = weighted_brier_num / float(max(1, n_total))
        weighted_slope = weighted_slope_num / float(max(1, n_total))
        dimension_rows.append(
            {
                "dimension": dim,
                "groupCount": int(len(arr)),
                "sampleCount": n_total,
                "eceWeighted": round(float(weighted_ece), 6) if n_total > 0 else None,
                "brierWeighted": round(float(weighted_brier), 6) if n_total > 0 else None,
                "calibrationSlopeWeighted": round(float(weighted_slope), 6) if n_total > 0 else None,
            }
        )
    return {
        "global": global_stats,
        "groups": rows,
        "dimensions": dimension_rows,
    }


def _build_primary_endpoint_summary(
    selected, target_keys, required_tokens, covered_tokens, elapsed_ms, clinical_context
):
    coverage_rate = float(len(covered_tokens)) / float(max(1, len(required_tokens)))
    min_safety_distances = []
    safety_probabilities = []
    guaranteed_probabilities = []
    review_required = 0
    for probe in selected:
        d = probe.get("minSafetyDistanceMm")
        if d is not None:
            min_safety_distances.append(float(d))
        conf = probe.get("safetyDistanceConfidence")
        if isinstance(conf, dict) and conf.get("probability") is not None:
            safety_probabilities.append(float(conf.get("probability")))
        sc = probe.get("selectiveConformal")
        if isinstance(sc, dict):
            if sc.get("probabilityGuaranteed") is not None:
                guaranteed_probabilities.append(float(sc.get("probabilityGuaranteed")))
            if sc.get("decision") == "review_required":
                review_required += 1

    mean_safety_probability = (
        float(np.mean(np.asarray(safety_probabilities, dtype=float)))
        if safety_probabilities
        else None
    )
    mean_guaranteed_probability = (
        float(np.mean(np.asarray(guaranteed_probabilities, dtype=float)))
        if guaranteed_probabilities
        else None
    )
    min_safety = (
        float(np.min(np.asarray(min_safety_distances, dtype=float)))
        if min_safety_distances
        else None
    )
    expert_acceptance = clinical_context.get("expertAcceptanceRate")
    if expert_acceptance is not None:
        expert_acceptance = float(expert_acceptance)

    return {
        "coverageRate": round(coverage_rate, 6),
        "continuousMinSafetyDistanceMm": round(min_safety, 6) if min_safety is not None else None,
        "safetyPassProbability": round(mean_safety_probability, 6)
        if mean_safety_probability is not None
        else None,
        "safetyPassProbabilityGuaranteed": round(mean_guaranteed_probability, 6)
        if mean_guaranteed_probability is not None
        else None,
        "planningTimeMs": int(max(0, elapsed_ms)),
        "expertAcceptanceRate": round(expert_acceptance, 6)
        if expert_acceptance is not None
        else None,
        "targetRegionCount": int(len(target_keys)),
        "selectedProbeCount": int(len(selected)),
        "reviewRequiredCount": int(review_required),
    }


def _build_candidate_stage_breakdown(
    *,
    candidate_gen_summary,
    auto_avoid_prefilter_summary,
    candidate_universe_count,
    candidate_pool_count,
    selected_count,
    strict_fallback_summary,
    score_cache_stats,
):
    gen = candidate_gen_summary if isinstance(candidate_gen_summary, dict) else {}
    pre = auto_avoid_prefilter_summary if isinstance(auto_avoid_prefilter_summary, dict) else {}
    strict = strict_fallback_summary if isinstance(strict_fallback_summary, dict) else {}
    score = score_cache_stats if isinstance(score_cache_stats, dict) else {}

    spec_total = int(gen.get("specDeduped", gen.get("specTotal", 0)) or 0)
    coarse_kept = int(gen.get("coarseKept", 0) or 0)
    refined_count = int(candidate_universe_count or 0)
    prefilter_kept = int(candidate_pool_count or 0)
    safe_pool_count = int(strict.get("safePoolCount", prefilter_kept) or prefilter_kept)
    scored_count = int(score.get("evaluated", refined_count) or refined_count)
    selected_count = int(selected_count or 0)

    coarse_reject = (
        gen.get("coarseRejectReasons", {})
        if isinstance(gen.get("coarseRejectReasons"), dict)
        else {}
    )
    coarse_reject_total = max(0, spec_total - coarse_kept)
    coarse_reasons = []
    if coarse_reject_total > 0:
        for key, value in coarse_reject.items():
            count = int(value or 0)
            if count <= 0:
                continue
            coarse_reasons.append(
                {
                    "code": str(key),
                    "count": count,
                    "ratio": round(count / max(coarse_reject_total, 1), 6),
                }
            )
        coarse_reasons.sort(key=lambda row: int(row.get("count", 0)), reverse=True)

    prefilter_drop = max(0, refined_count - prefilter_kept)
    prefilter_reasons = []
    pre_rejected = int(pre.get("rejectedCount", 0) or 0)
    pre_near = int(pre.get("nearCount", 0) or 0)
    pre_feasible = int(pre.get("feasibleCount", 0) or 0)
    kept_mode = str(pre.get("keptMode", "disabled") or "disabled")
    if pre_rejected > 0:
        prefilter_reasons.append(
            {
                "code": "risk_prefilter_rejected",
                "count": pre_rejected,
                "ratio": round(pre_rejected / max(prefilter_drop, 1), 6),
            }
        )
    if pre_near > 0 and kept_mode == "feasible_only":
        prefilter_reasons.append(
            {
                "code": "risk_prefilter_near_not_kept",
                "count": pre_near,
                "ratio": round(pre_near / max(prefilter_drop, 1), 6),
            }
        )
    if kept_mode == "near_only" and pre_feasible <= 0 and pre_near > 0:
        prefilter_reasons.append(
            {
                "code": "risk_prefilter_fallback_near_only",
                "count": pre_near,
                "ratio": round(pre_near / max(prefilter_drop, 1), 6),
            }
        )

    strict_drop = max(0, prefilter_kept - safe_pool_count)
    strict_reasons = []
    blocker_count = int(strict.get("blockerCount", 0) or 0)
    if strict_drop > 0:
        strict_reasons.append(
            {
                "code": "enforced_constraints_blocked",
                "count": strict_drop,
                "ratio": round(strict_drop / max(prefilter_kept, 1), 6),
            }
        )
    if blocker_count > 0:
        strict_reasons.append(
            {
                "code": "selected_blocker_count",
                "count": blocker_count,
                "ratio": round(blocker_count / max(selected_count, 1), 6),
            }
        )

    selected_drop = max(0, safe_pool_count - selected_count)
    selected_reasons = []
    infeasible_targets = strict.get("infeasibleTargets", [])
    if selected_drop > 0:
        selected_reasons.append(
            {
                "code": "solver_or_collision_filtered",
                "count": selected_drop,
                "ratio": round(selected_drop / max(safe_pool_count, 1), 6),
            }
        )
    if isinstance(infeasible_targets, list) and infeasible_targets:
        selected_reasons.append(
            {
                "code": "infeasible_targets",
                "count": int(len(infeasible_targets)),
                "ratio": round(len(infeasible_targets) / max(spec_total, 1), 6),
            }
        )

    stages = [
        {
            "stage": "generated_specs",
            "count": spec_total,
            "droppedFromPrev": 0,
            "dropRatioFromPrev": 0.0,
            "reasons": [],
        },
        {
            "stage": "coarse_refine_kept",
            "count": refined_count,
            "droppedFromPrev": max(0, spec_total - refined_count),
            "dropRatioFromPrev": round(max(0, spec_total - refined_count) / max(spec_total, 1), 6),
            "reasons": coarse_reasons[:6],
        },
        {
            "stage": "risk_prefilter_kept",
            "count": prefilter_kept,
            "droppedFromPrev": prefilter_drop,
            "dropRatioFromPrev": round(prefilter_drop / max(refined_count, 1), 6),
            "reasons": prefilter_reasons[:6],
        },
        {
            "stage": "constraint_feasible_pool",
            "count": safe_pool_count,
            "droppedFromPrev": strict_drop,
            "dropRatioFromPrev": round(strict_drop / max(prefilter_kept, 1), 6),
            "reasons": strict_reasons[:6],
        },
        {
            "stage": "scored",
            "count": scored_count,
            "droppedFromPrev": 0,
            "dropRatioFromPrev": 0.0,
            "reasons": [],
        },
        {
            "stage": "selected",
            "count": selected_count,
            "droppedFromPrev": selected_drop,
            "dropRatioFromPrev": round(selected_drop / max(safe_pool_count, 1), 6),
            "reasons": selected_reasons[:6],
        },
    ]

    return {
        "stages": stages,
        "prefilter": {
            "enabled": bool(pre.get("enabled", False)),
            "keptMode": kept_mode,
            "feasibleCount": pre_feasible,
            "nearCount": pre_near,
            "rejectedCount": pre_rejected,
        },
        "summary": {
            "specTotal": spec_total,
            "refinedCount": refined_count,
            "prefilterKept": prefilter_kept,
            "safePoolCount": safe_pool_count,
            "selectedCount": selected_count,
            "solverCoverageLoss": int(len(infeasible_targets))
            if isinstance(infeasible_targets, list)
            else 0,
        },
    }


def _funnel_reason_ratios(counts):
    total = int(sum(int(v or 0) for v in counts.values()))
    return {
        str(k): round(int(v or 0) / max(total, 1), 6)
        for k, v in counts.items()
        if int(v or 0) > 0
    }


def _bump_reason(counter, key, n=1):
    key = str(key or "unknown")
    counter[key] = int(counter.get(key, 0)) + int(n or 0)


def _map_coarse_reject_to_funnel_reason(key):
    norm = str(key or "").strip().lower()
    if norm in ("bounds", "emptytrajectory", "brainmasknointersection", "brainmaskrejected"):
        return "out_of_brain"
    if norm == "nocoverage":
        return "no_target_coverage"
    if norm == "brainmaskrecoverablekept":
        return "out_of_brain_recoverable"
    return norm or "unknown"


def _map_refine_reject_to_funnel_reason(key):
    norm = str(key or "").strip().lower()
    if norm in ("bounds", "emptytrajectory", "brainmasknointersection", "brainmaskrejected"):
        return "out_of_brain"
    if norm == "nocoverage":
        return "no_target_coverage"
    if norm == "hemispheremismatch":
        return "hemisphere_mismatch"
    return norm or "unknown"


def _risk_reason_from_prefilter(candidate):
    info = candidate.get("riskPrefilter") if isinstance(candidate.get("riskPrefilter"), dict) else {}
    cls = str(info.get("worstClass") or info.get("nearestSourceClass") or "").strip().lower()
    src = str(info.get("worstSource") or info.get("nearestSource") or "").strip().lower()
    text = f"{cls} {src}"
    if "ventricle" in text:
        return "ventricle_hit"
    if "outside" in text or "skull" in text or "brain_surface" in text:
        return "out_of_brain"
    return "risk_distance"


def _candidate_fine_risk_ok(candidate, params, probe_params, constraint_modes):
    threshold_by_class = sanitize_risk_thresholds(
        params.get("riskThresholdsMm"),
        default_mm=float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
    )
    ok, reason, risk_summary = _candidate_passes_single_enforced_constraints(
        candidate,
        params,
        probe_params,
        constraint_modes,
        threshold_by_class=threshold_by_class,
        risk_step_um=RISK_DIAG_STEP_UM,
    )
    return ok, reason, risk_summary


def _filter_candidates_by_enforced_constraints(
    candidates,
    params,
    probe_params,
    constraint_modes,
    *,
    stage="enforced_constraints",
):
    """
    Keep solver inputs strictly feasible for enforced geometry/safety constraints.

    The final safety contract should remain a last-resort audit, not the first
    place where impossible trajectories are removed. In particular, brainBounds
    violations such as tip_outside_brain must not enter the optimizer pool.
    """
    items = list(candidates or [])
    summary = {
        "enabled": True,
        "stage": str(stage),
        "inputCount": int(len(items)),
        "keptCount": int(len(items)),
        "rejectedCount": 0,
        "reasons": {},
    }
    if not items:
        return [], summary

    kept = []
    for cand in items:
        ok, reason, detail = _candidate_fine_risk_ok(cand, params, probe_params, constraint_modes)
        if ok:
            kept.append(cand)
            continue
        reason_key = str(reason or "enforced_constraint")
        if reason_key == "brainBounds" and isinstance(detail, dict):
            reason_key = str(detail.get("reason") or "brainBounds")
        _bump_reason(summary["reasons"], reason_key, 1)
    summary["keptCount"] = int(len(kept))
    summary["rejectedCount"] = int(len(items) - len(kept))
    return kept, summary


def _build_candidate_funnel_diagnostics(
    *,
    candidate_gen_summary,
    auto_avoid_prefilter_summary,
    candidate_universe,
    candidate_pool,
    selected,
    strict_fallback_summary,
    score_cache_stats,
    params,
    probe_params,
    constraint_modes,
    min_clearance,
    min_entry,
):
    gen = candidate_gen_summary if isinstance(candidate_gen_summary, dict) else {}
    pre = auto_avoid_prefilter_summary if isinstance(auto_avoid_prefilter_summary, dict) else {}
    strict = strict_fallback_summary if isinstance(strict_fallback_summary, dict) else {}
    score = score_cache_stats if isinstance(score_cache_stats, dict) else {}
    universe = list(candidate_universe or [])
    pool = list(candidate_pool or [])
    selected = list(selected or [])

    generated = int(gen.get("specDeduped", gen.get("specTotal", len(universe))) or 0)
    coarse_reject = gen.get("coarseRejectReasons", {}) if isinstance(gen.get("coarseRejectReasons"), dict) else {}
    refine_reject = gen.get("refineRejectReasons", {}) if isinstance(gen.get("refineRejectReasons"), dict) else {}
    reason_counts = {
        "out_of_brain": 0,
        "ventricle_hit": 0,
        "angle_limit": 0,
        "depth_limit": 0,
        "no_target_coverage": 0,
        "hemisphere_mismatch": 0,
        "probe_spacing": 0,
        "mri_quality_gate": 0,
        "risk_distance": 0,
    }
    step_reasons = {}

    def _stage_reason(stage, reason, n=1):
        stage_map = step_reasons.setdefault(stage, {})
        _bump_reason(stage_map, reason, n)
        if reason in reason_counts:
            _bump_reason(reason_counts, reason, n)

    coarse_brain_reject = (
        int(coarse_reject.get("bounds", 0) or 0)
        + int(coarse_reject.get("emptyTrajectory", 0) or 0)
        + int(coarse_reject.get("brainMaskNoIntersection", 0) or 0)
        + int(coarse_reject.get("brainMaskRejected", 0) or 0)
    )
    refine_brain_reject = (
        int(refine_reject.get("bounds", 0) or 0)
        + int(refine_reject.get("emptyTrajectory", 0) or 0)
        + int(refine_reject.get("brainMaskNoIntersection", 0) or 0)
        + int(refine_reject.get("brainMaskRejected", 0) or 0)
    )
    no_coverage = int(coarse_reject.get("noCoverage", 0) or 0) + int(refine_reject.get("noCoverage", 0) or 0)

    for key, count in coarse_reject.items():
        mapped = _map_coarse_reject_to_funnel_reason(key)
        if mapped == "out_of_brain_recoverable":
            continue
        stage = "afterCoverage" if mapped == "no_target_coverage" else "afterBrainBounds"
        _stage_reason(stage, mapped, int(count or 0))
    for key, count in refine_reject.items():
        mapped = _map_refine_reject_to_funnel_reason(key)
        stage = "afterCoverage"
        if mapped == "out_of_brain":
            stage = "afterBrainBounds"
        elif mapped == "hemisphere_mismatch":
            stage = "afterHemisphere"
        _stage_reason(stage, mapped, int(count or 0))

    after_brain_bounds = max(
        0,
        generated - coarse_brain_reject - refine_brain_reject,
    )

    hemi_reject = 0
    if constraint_modes.get("hemisphere") == "enforce":
        for cand in universe:
            try:
                if has_hemisphere_mismatch(cand):
                    hemi_reject += 1
            except Exception:
                pass
    if hemi_reject:
        _stage_reason("afterHemisphere", "hemisphere_mismatch", hemi_reject)
    after_hemisphere = max(0, after_brain_bounds - hemi_reject)

    pool_ids = {id(c) for c in pool}
    coarse_risk_reject = 0
    for cand in universe:
        info = cand.get("riskPrefilter") if isinstance(cand.get("riskPrefilter"), dict) else {}
        bucket = str(info.get("bucket") or "").strip().lower()
        if id(cand) not in pool_ids and bucket in ("rejected", "near"):
            coarse_risk_reject += 1
            _stage_reason("afterCoarseRisk", _risk_reason_from_prefilter(cand), 1)
    kept_mode = str(pre.get("keptMode") or "").strip().lower()
    if (
        not coarse_risk_reject
        and kept_mode != "warn_passthrough"
        and int(pre.get("rejectedCount", 0) or 0) > 0
    ):
        _stage_reason("afterCoarseRisk", "risk_distance", int(pre.get("rejectedCount", 0) or 0))
        coarse_risk_reject = int(pre.get("rejectedCount", 0) or 0)
    if kept_mode == "warn_passthrough":
        coarse_risk_reject = 0
    after_coarse_risk = max(0, after_hemisphere - int(coarse_risk_reject))

    after_coverage = max(0, after_coarse_risk - int(no_coverage))

    spacing_reject = 0
    if selected and pool:
        for cand in pool:
            if any(_candidate_signature(cand) == _candidate_signature(sel) for sel in selected):
                continue
            try:
                if any(trajs_collide(cand, sel, min_clearance, min_entry) for sel in selected):
                    spacing_reject += 1
            except Exception:
                pass
    if spacing_reject:
        _stage_reason("afterSpacing", "probe_spacing", spacing_reject)
    after_spacing = max(0, after_coverage - spacing_reject)

    fine_reject = 0
    for cand in pool:
        try:
            ok, reason, risk_summary = _candidate_fine_risk_ok(cand, params, probe_params, constraint_modes)
        except Exception:
            continue
        if ok:
            continue
        fine_reject += 1
        if reason == "maxAngle":
            _stage_reason("afterFineRisk", "angle_limit", 1)
        elif reason == "brainBounds":
            _stage_reason("afterFineRisk", "out_of_brain", 1)
        elif reason == "hemisphere":
            _stage_reason("afterFineRisk", "hemisphere_mismatch", 1)
        elif reason == "continuousRiskDistance":
            if isinstance(risk_summary, dict):
                _stage_reason("afterFineRisk", _risk_reason_from_prefilter({"riskPrefilter": {
                    "worstClass": risk_summary.get("worstClass"),
                    "worstSource": risk_summary.get("worstSource"),
                }}), 1)
            else:
                _stage_reason("afterFineRisk", "risk_distance", 1)
        else:
            _stage_reason("afterFineRisk", str(reason or "risk_distance"), 1)
    after_fine_risk = min(after_spacing, max(0, after_coverage - fine_reject))

    q = reconstruction_quality_state if isinstance(reconstruction_quality_state, dict) else {}
    if bool(q.get("gateEnabled")) and not bool(q.get("passed", True)):
        _stage_reason("preflight", "mri_quality_gate", 1)

    selected_count = int(len(selected))
    funnel = {
        "generatedCandidates": int(generated),
        "afterBrainBounds": int(after_brain_bounds),
        "afterHemisphere": int(after_hemisphere),
        "afterCoarseRisk": int(after_coarse_risk),
        "afterCoverage": int(after_coverage),
        "afterSpacing": int(after_spacing),
        "afterFineRisk": int(after_fine_risk),
        "selected": int(selected_count),
    }

    stages = []
    ordered = [
        ("generatedCandidates", generated),
        ("afterBrainBounds", after_brain_bounds),
        ("afterHemisphere", after_hemisphere),
        ("afterCoarseRisk", after_coarse_risk),
        ("afterCoverage", after_coverage),
        ("afterSpacing", after_spacing),
        ("afterFineRisk", after_fine_risk),
        ("selected", selected_count),
    ]
    prev = None
    for key, count in ordered:
        count = int(count or 0)
        dropped = 0 if prev is None else max(0, int(prev) - count)
        reasons = step_reasons.get(key, {})
        stages.append(
            {
                "stage": key,
                "count": count,
                "droppedFromPrev": dropped,
                "dropRatioFromPrev": round(dropped / max(int(prev or count or 1), 1), 6) if prev is not None else 0.0,
                "reasons": [
                    {"code": str(r), "count": int(n), "ratio": round(int(n) / max(dropped, 1), 6)}
                    for r, n in sorted(reasons.items(), key=lambda item: int(item[1]), reverse=True)
                    if int(n) > 0
                ],
            }
        )
        prev = count

    abnormal_fast_zero_guard = {
        "triggered": bool(generated > 0 and selected_count == 0),
        "likelyStage": None,
        "message": None,
    }
    if abnormal_fast_zero_guard["triggered"]:
        min_stage = min(
            ((k, int(v or 0)) for k, v in funnel.items() if k != "selected"),
            key=lambda item: item[1],
            default=(None, 0),
        )
        abnormal_fast_zero_guard["likelyStage"] = min_stage[0]
        abnormal_fast_zero_guard["message"] = (
            "No selected probe after candidate funnel; inspect stage counts and rejection ratios."
        )

    return {
        "funnel": funnel,
        "stages": stages,
        "rejectionCounts": reason_counts,
        "rejectionRatios": _funnel_reason_ratios(reason_counts),
        "stepReasons": step_reasons,
        "feasibleFirstPolicy": {
            "enabled": bool(params.get("constraintAwareSearch", CONSTRAINT_AWARE_SEARCH_DEFAULT)),
            "rankingOrder": [
                "safety_feasible",
                "coverage",
                "entry_angle",
                "depth",
                "uncertainty",
            ],
        },
        "scoreEvaluated": int(score.get("evaluated", len(pool)) or 0),
        "strictFallback": {
            "enabled": bool(strict.get("enabled", False)),
            "applied": bool(strict.get("applied", False)),
            "safePoolCount": int(strict.get("safePoolCount", 0) or 0),
            "infeasibleTargets": deepcopy(strict.get("infeasibleTargetsDetailed", strict.get("infeasibleTargets", []))),
        },
        "abnormalFastZeroGuard": abnormal_fast_zero_guard,
    }


def _run_optimize_pipeline(raw_body, task_id=None):
    task_id = task_id or _request_id()
    normalized = _normalize_optimize_payload(raw_body)
    params = normalized["params"]
    result = run_density_policy(
        raw_body, params["gridSpacing"], params["angleStep"], params["candidateExpansionEnabled"],
        lambda body: _run_optimize_pipeline_once(body, task_id=task_id),
        cancel=lambda: _check_task_cancelled(task_id, "density_retry"),
    )
    partial = bool(result.get("uncovered") or result.get("uncoveredSides"))
    _update_task_progress(task_id, "completed", 1.0, "Planning policy completed",
                          status="partial" if partial else "completed")
    return result


def _run_optimize_pipeline_once(raw_body, task_id=None):
    task_id = task_id or _request_id()
    _update_task_progress(task_id, "pre_validation", 0.02, "Validating request payload")
    _check_task_cancelled(task_id, "pre_validation")
    if version_freeze_state.get("frozen"):
        version_mismatch = version_freeze_state.get("engineVersion") != ENGINE_VERSION
        frozen_atlas_hash = version_freeze_state.get("atlasHash")
        current_atlas_hash = atlas_signature.get("hash")
        atlas_mismatch = bool(frozen_atlas_hash and current_atlas_hash and frozen_atlas_hash != current_atlas_hash)
        if version_mismatch or atlas_mismatch:
            details = [
                f"Frozen engineVersion={version_freeze_state.get('engineVersion')}",
                f"Running engineVersion={ENGINE_VERSION}",
            ]
            if frozen_atlas_hash:
                details.append(f"Frozen atlasHash={frozen_atlas_hash}")
                details.append(f"Running atlasHash={current_atlas_hash}")
            raise ContractError(
                "Version freeze mismatch",
                details=details,
                status_code=409,
                code="version_freeze_mismatch",
            )

    payload = _normalize_optimize_payload(raw_body)
    _update_task_progress(task_id, "payload_normalized", 0.08, "Request payload normalized")
    _check_task_cancelled(task_id, "payload_normalized")
    target_keys = payload["targetKeys"]
    params = payload["params"]
    probe_params = payload["probeParams"]
    constraint_modes = payload["constraintModes"]
    correction_factors = payload["correctionFactors"]
    correction_unc = payload["correctionUncertainty"]
    clinical_context = payload.get("clinicalContext", _normalize_clinical_context({}))

    params.setdefault("depthUm", probe_params.get("depthUm", 10000))
    min_clearance = params.get("minClearance", 1000)
    min_entry = probe_params.get("entrySpacingUm", 3500)

    _update_task_progress(task_id, "quality_gate_check", 0.12, "Checking reconstruction quality gate")
    _check_task_cancelled(task_id, "quality_gate_check")
    _enforce_reconstruction_quality_gate()

    t0 = time.time()
    structured_log(
        log,
        "optimize.start",
        requestId=_request_id(),
        taskId=task_id,
        targetCount=len(target_keys),
        targetingMode=params.get("targetingMode"),
        algorithm=params.get("algorithm"),
    )
    def _gen_progress(done, total):
        total_n = max(1.0, float(total or 1.0))
        done_n = max(0.0, min(float(done or 0.0), total_n))
        frac = done_n / total_n
        pct = 0.12 + (0.34 - 0.12) * frac
        if done_n < total_n:
            msg = f"Generating candidates {int(round(done_n))}/{int(round(total_n))}"
            stage = "candidate_generating"
        else:
            msg = "Candidate trajectories generated"
            stage = "candidate_generated"
        _update_task_progress(task_id, stage, pct, msg)

    def _score_progress(done, total):
        total_n = max(1, int(total or 1))
        done_n = max(0, min(int(done or 0), total_n))
        frac = done_n / total_n
        pct = 0.38 + (0.50 - 0.38) * frac
        if done_n < total_n:
            msg = f"Scoring candidates {done_n}/{total_n}"
            stage = "candidate_scoring"
        else:
            msg = "Candidates scored"
            stage = "candidate_scored"
        _update_task_progress(task_id, stage, pct, msg)

    runtime_budget = int(max(RUNTIME_NUM_THREADS_DEFAULT, params.get("computeWorkers", 1)))
    oversubscribed = (
        int(params.get("computeWorkers", COMPUTE_WORKERS_DEFAULT))
        + int(params.get("candidateGenWorkers", CANDIDATE_GEN_WORKERS_DEFAULT))
    ) > runtime_budget
    streaming_requested = bool(params.get("streamingPipelineEnabled", STREAMING_PIPELINE_ENABLED_DEFAULT))
    streaming_applied = bool(streaming_requested)
    streaming_reason = "enabled"
    if streaming_requested and oversubscribed and (not bool(params.get("streamingAllowOversubscribe", False))):
        streaming_applied = False
        streaming_reason = "auto_disabled_oversubscribed_workers"
    params["_streamingPipelineRequested"] = bool(streaming_requested)
    params["_streamingPipelineApplied"] = bool(streaming_applied)
    params["_streamingPipelineReason"] = str(streaming_reason)

    if streaming_applied:
        (
            candidates,
            candidate_universe,
            near_candidates,
            auto_avoid_prefilter_summary,
            score_cache_stats,
            candidate_gen_summary,
        ) = _generate_and_score_streaming(
            target_keys=target_keys,
            params=params,
            constraint_modes=constraint_modes,
            correction_factors=correction_factors,
            probe_params=probe_params,
            gen_progress_cb=_gen_progress,
            score_progress_cb=_score_progress,
            cancel_cb=lambda: _check_task_cancelled(task_id, "candidate_streaming"),
        )
        _update_task_progress(
            task_id,
            "candidate_prefiltered",
            0.38,
            (
                f"Risk pre-filter kept {len(candidates)}/{len(candidate_universe)} "
                f"(near={len(near_candidates)})"
            ),
        )
        _check_task_cancelled(task_id, "candidate_prefiltered")
    else:
        if streaming_requested and streaming_reason != "enabled":
            _update_task_progress(
                task_id,
                "candidate_streaming_fallback",
                0.10,
                "Streaming pipeline auto-disabled due to worker oversubscription",
            )
        candidates = generate_candidates_voxel(
            target_keys,
            params,
            constraint_modes,
            correction_factors=correction_factors,
            probe_params=probe_params,
            progress_cb=_gen_progress,
            cancel_cb=lambda: _check_task_cancelled(task_id, "candidate_generating"),
        )
        candidate_gen_summary = deepcopy(getattr(generate_candidates_voxel, "last_summary", {}))
        _update_task_progress(task_id, "candidate_generated", 0.34, "Candidate trajectories generated")
        _check_task_cancelled(task_id, "candidate_generated")
        candidate_universe = list(candidates)
        prefiltered_candidates, near_candidates, auto_avoid_prefilter_summary = _prefilter_candidates_for_auto_avoid(
            candidates,
            params,
            constraint_modes,
        )
        if prefiltered_candidates:
            candidates = prefiltered_candidates
        else:
            candidates = list(candidates)
        _update_task_progress(
            task_id,
            "candidate_prefiltered",
            0.38,
            (
                f"Risk pre-filter kept {len(candidates)}/{len(candidate_universe)} "
                f"(near={len(near_candidates)})"
            ),
        )
        _check_task_cancelled(task_id, "candidate_prefiltered")
        score_cache_stats = _score_candidates(
            candidates,
            target_keys,
            params,
            progress_cb=_score_progress,
            cancel_cb=lambda: _check_task_cancelled(task_id, "candidate_scoring"),
        )

    enforced_filter_summary = {
        "enabled": False,
        "inputCount": int(len(candidates or [])),
        "keptCount": int(len(candidates or [])),
        "rejectedCount": 0,
        "reasons": {},
    }
    if candidates:
        candidates, enforced_filter_summary = _filter_candidates_by_enforced_constraints(
            candidates,
            params,
            probe_params,
            constraint_modes,
            stage="solver_input",
        )
        filtered_near, near_filter_summary = _filter_candidates_by_enforced_constraints(
            near_candidates,
            params,
            probe_params,
            constraint_modes,
            stage="near_candidate_pool",
        )
        near_candidates = filtered_near
        enforced_filter_summary["nearPool"] = near_filter_summary
        if isinstance(auto_avoid_prefilter_summary, dict):
            auto_avoid_prefilter_summary["enforcedConstraintFilter"] = enforced_filter_summary
        _update_task_progress(
            task_id,
            "candidate_enforced_filtered",
            0.42,
            (
                f"Enforced safety filter kept {len(candidates)}/"
                f"{int(enforced_filter_summary.get('inputCount', 0))}"
            ),
        )
        _check_task_cancelled(task_id, "candidate_enforced_filtered")
    if len(candidate_universe) <= 0:
        reject_reasons = (
            candidate_gen_summary.get("coarseRejectReasons", {})
            if isinstance(candidate_gen_summary, dict)
            else {}
        )
        top_reasons = sorted(
            [
                f"{str(k)}={int(v)}"
                for k, v in (reject_reasons or {}).items()
                if int(v) > 0
            ],
            key=lambda item: int(item.split("=", 1)[1]),
            reverse=True,
        )
        details = [
            f"targetCount={len(target_keys)}",
            f"specTotal={int((candidate_gen_summary or {}).get('specTotal', 0))}",
            f"specDeduped={int((candidate_gen_summary or {}).get('specDeduped', 0))}",
            f"coarseKept={int((candidate_gen_summary or {}).get('coarseKept', 0))}",
            f"brainBoundsMode={constraint_modes.get('brainBounds', 'warn')}",
        ]
        if top_reasons:
            details.append(f"coarseRejectTop={'; '.join(top_reasons[:4])}")
        empty_stage_breakdown = _build_candidate_stage_breakdown(
            candidate_gen_summary=candidate_gen_summary,
            auto_avoid_prefilter_summary=auto_avoid_prefilter_summary,
            candidate_universe_count=len(candidate_universe),
            candidate_pool_count=len(candidates),
            selected_count=0,
            strict_fallback_summary={"enabled": bool(params.get("strictFallbackDecompose", False)), "applied": False},
            score_cache_stats=score_cache_stats,
        )
        empty_funnel_diagnostics = _build_candidate_funnel_diagnostics(
            candidate_gen_summary=candidate_gen_summary,
            auto_avoid_prefilter_summary=auto_avoid_prefilter_summary,
            candidate_universe=candidate_universe,
            candidate_pool=candidates,
            selected=[],
            strict_fallback_summary={"enabled": bool(params.get("strictFallbackDecompose", False)), "applied": False},
            score_cache_stats=score_cache_stats,
            params=params,
            probe_params=probe_params,
            constraint_modes=constraint_modes,
            min_clearance=min_clearance,
            min_entry=min_entry,
        )
        empty_stage_breakdown["candidateFunnelDiagnostics"] = empty_funnel_diagnostics
        empty_failure_card = _build_candidate_pool_failure_card(
            candidate_universe=candidate_universe,
            candidate_pool=candidates,
            target_keys=target_keys,
            params=params,
            probe_params=probe_params,
            constraint_modes=constraint_modes,
            funnel_diagnostics=empty_funnel_diagnostics,
            enforced_filter_summary=enforced_filter_summary,
        )
        raise ContractError(
            "No feasible candidate trajectories generated under current settings",
            details=details,
            status_code=422,
            code="candidate_generation_empty",
            extra={
                "candidateStageBreakdown": empty_stage_breakdown,
                "candidateFunnelDiagnostics": empty_funnel_diagnostics,
                "failureCards": [empty_failure_card],
                "autoAvoidPrefilter": auto_avoid_prefilter_summary,
                "computeProfile": {
                    "candidateGeneration": candidate_gen_summary,
                    "streamingPipelineApplied": bool(params.get("_streamingPipelineApplied", False)),
                    "streamingPipelineReason": str(params.get("_streamingPipelineReason", "")),
                },
                "candidateCount": len(candidates),
                "candidateUniverseCount": len(candidate_universe),
            },
        )
    if len(candidates) <= 0:
        empty_stage_breakdown = _build_candidate_stage_breakdown(
            candidate_gen_summary=candidate_gen_summary,
            auto_avoid_prefilter_summary=auto_avoid_prefilter_summary,
            candidate_universe_count=len(candidate_universe),
            candidate_pool_count=0,
            selected_count=0,
            strict_fallback_summary={"enabled": bool(params.get("strictFallbackDecompose", False)), "applied": False},
            score_cache_stats=score_cache_stats,
        )
        empty_funnel_diagnostics = _build_candidate_funnel_diagnostics(
            candidate_gen_summary=candidate_gen_summary,
            auto_avoid_prefilter_summary=auto_avoid_prefilter_summary,
            candidate_universe=candidate_universe,
            candidate_pool=[],
            selected=[],
            strict_fallback_summary={"enabled": bool(params.get("strictFallbackDecompose", False)), "applied": False},
            score_cache_stats=score_cache_stats,
            params=params,
            probe_params=probe_params,
            constraint_modes=constraint_modes,
            min_clearance=min_clearance,
            min_entry=min_entry,
        )
        empty_stage_breakdown["candidateFunnelDiagnostics"] = empty_funnel_diagnostics
        reasons = enforced_filter_summary.get("reasons", {}) if isinstance(enforced_filter_summary, dict) else {}
        reason_text = "; ".join(f"{k}={v}" for k, v in sorted(reasons.items(), key=lambda item: int(item[1]), reverse=True)[:5])
        empty_failure_card = _build_candidate_pool_failure_card(
            candidate_universe=candidate_universe,
            candidate_pool=candidates,
            target_keys=target_keys,
            params=params,
            probe_params=probe_params,
            constraint_modes=constraint_modes,
            funnel_diagnostics=empty_funnel_diagnostics,
            enforced_filter_summary=enforced_filter_summary,
        )
        details = [
            f"targetCount={len(target_keys)}",
            f"candidateUniverseCount={len(candidate_universe)}",
            f"enforcedFilterInput={int(enforced_filter_summary.get('inputCount', 0))}",
            f"brainBoundsMode={constraint_modes.get('brainBounds', 'warn')}",
        ]
        if reason_text:
            details.append(f"enforcedFilterTop={reason_text}")
        raise ContractError(
            "No feasible candidate trajectories remain after enforced safety filtering",
            details=details,
            status_code=422,
            code="candidate_enforced_filter_empty",
            extra={
                "candidateStageBreakdown": empty_stage_breakdown,
                "candidateFunnelDiagnostics": empty_funnel_diagnostics,
                "failureCards": [empty_failure_card],
                "autoAvoidPrefilter": auto_avoid_prefilter_summary,
                "enforcedConstraintFilter": enforced_filter_summary,
                "computeProfile": {
                    "candidateGeneration": candidate_gen_summary,
                    "streamingPipelineApplied": bool(params.get("_streamingPipelineApplied", False)),
                    "streamingPipelineReason": str(params.get("_streamingPipelineReason", "")),
                },
                "candidateCount": 0,
                "candidateUniverseCount": len(candidate_universe),
            },
        )
    _update_task_progress(task_id, "candidate_scored", 0.50, "Candidates scored")
    _check_task_cancelled(task_id, "candidate_scored")
    repair_preference_summary = _apply_repair_preference_bias(candidates, params)
    pareto_summary = _assign_pareto_ranks(candidates, params)
    _update_task_progress(task_id, "pareto_ranked", 0.62, "Pareto ranking completed")
    _check_task_cancelled(task_id, "pareto_ranked")

    algorithm = params.get("algorithm", "greedy")
    targeting_mode = params.get("targetingMode", "single_hemisphere")
    solver_runtime_policy = {
        "requestedAlgorithm": str(algorithm),
        "effectiveAlgorithm": str(algorithm),
        "candidateCount": int(len(candidates)),
        "dynamic": True,
        "reason": "as_requested",
        "reductionTopK": None,
        "timeLimitS": None,
    }
    if algorithm == "greedy":
        result = greedy_optimize(target_keys, candidates, min_clearance, min_entry)
        result.setdefault("milpStatus", None)
        result.setdefault("milpNodes", None)
    elif algorithm == "drcc":
        result = drcc_optimize(target_keys, candidates, min_clearance, min_entry, params)
        result.setdefault("milpStatus", "DRCC-Optimal")
        result.setdefault("milpNodes", None)
        solver_runtime_policy["effectiveAlgorithm"] = "drcc"
        if isinstance(result.get("drccSummary"), dict):
            solver_runtime_policy["drccAdaptiveBudget"] = result["drccSummary"].get("adaptiveBudget")
    else:
        candidate_count = int(len(candidates))
        if candidate_count >= 3200:
            solver_runtime_policy.update(
                {
                    "effectiveAlgorithm": "greedy",
                    "reason": "candidate_scale_greedy_fallback",
                    "reductionTopK": 0,
                    "timeLimitS": 0,
                }
            )
            result = greedy_optimize(target_keys, candidates, min_clearance, min_entry)
            result.setdefault("milpStatus", "GreedyFallbackLargePool")
            result.setdefault("milpNodes", None)
        else:
            if candidate_count >= 1400:
                top_k = 8
                time_limit_s = 18
                reason = "candidate_scale_hybrid_reduced_exact"
            elif candidate_count >= 500:
                top_k = 10
                time_limit_s = 22
                reason = "candidate_scale_reduced_exact"
            else:
                top_k = 14
                time_limit_s = 25
                reason = "small_pool_exact"
            solver_runtime_policy.update(
                {
                    "effectiveAlgorithm": "exact_reduced",
                    "reason": reason,
                    "reductionTopK": int(top_k),
                    "timeLimitS": float(time_limit_s),
                }
            )
            reduced = diversity_reduce_candidates(candidates, top_k=top_k)
            log.info(
                f"Dynamic solver policy: requested={algorithm} effective=exact_reduced "
                f"candidates={len(candidates)} reduced={len(reduced)} topK={top_k} t={time_limit_s}s"
            )
            result = exact_optimize(target_keys, reduced, min_clearance, min_entry, time_limit_s=time_limit_s)
    result["solverRuntimePolicy"] = solver_runtime_policy

    _update_task_progress(task_id, "solver_completed", 0.74, "Core optimization solver completed")
    if params.get("coverageBoost", False) and result.get("uncovered"):
        boosted = coverage_boost_voxel(
            result.get("selected", []),
            result.get("uncovered", []),
            target_keys,
            params,
            probe_params,
            constraint_modes,
            correction_factors,
        )
        boosted["milpStatus"] = result.get("milpStatus")
        boosted["milpNodes"] = result.get("milpNodes")
        boosted["solverRuntimePolicy"] = result.get("solverRuntimePolicy", solver_runtime_policy)
        result = boosted
    _update_task_progress(task_id, "coverage_boosted", 0.82, "Coverage boost completed")
    _check_task_cancelled(task_id, "coverage_boosted")

    token_cover_audit = {}
    selected_aug, missing_tokens, covered_tokens, required_tokens = _augment_selection_for_targeting_mode(
        result.get("selected", []),
        candidates,
        target_keys,
        targeting_mode,
        min_clearance,
        min_entry,
        audit=token_cover_audit,
        cancel=lambda: _check_task_cancelled(task_id, "token_cover_search"),
    )
    result["selected"] = selected_aug
    token_cover_selection = [_candidate_cover_identity(p, target_keys, targeting_mode) for p in selected_aug]
    _update_task_progress(task_id, "targeting_mode_augmented", 0.88, "Targeting mode augmentation completed")
    _check_task_cancelled(task_id, "targeting_mode_augmented")
    if targeting_mode == "bilateral_symmetric":
        uncovered_side_items = []
        for token in missing_tokens:
            key, _, side = token.partition(":")
            if key and side:
                uncovered_side_items.append({"key": key, "side": side})
        result["uncovered"] = sorted({item["key"] for item in uncovered_side_items})
    else:
        uncovered_side_items = []
        result["uncovered"] = sorted(set(missing_tokens))

    auto_repair_summary = {
        "enabled": bool(params.get("autoRepairEnabled", AUTO_REPAIR_ENABLED_DEFAULT)),
        "applied": False,
        "attempted": 0,
        "repaired": 0,
        "items": [],
    }
    if result.get("selected"):
        repaired_selected, auto_repair_summary = _auto_repair_selection(
            result.get("selected", []),
            candidate_pool=(candidate_universe + list(near_candidates)),
            target_keys=target_keys,
            params=params,
            probe_params=probe_params,
            constraint_modes=constraint_modes,
            correction_factors=correction_factors,
            min_clearance=min_clearance,
            min_entry=min_entry,
        )
        if auto_repair_summary.get("applied"):
            result["selected"] = repaired_selected
            covered_after_repair = _covered_targets_from_selected(repaired_selected, target_keys)
            result["uncovered"] = sorted(set(target_keys) - covered_after_repair)
    _update_task_progress(
        task_id,
        "auto_repair_checked",
        0.90,
        f"Auto-repair repaired {int(auto_repair_summary.get('repaired', 0))} probes",
    )
    _check_task_cancelled(task_id, "auto_repair_checked")

    strict_fallback_summary = {
        "enabled": bool(params.get("strictFallbackDecompose", STRICT_FALLBACK_DECOMPOSE_DEFAULT)),
        "applied": False,
    }
    fallback_selected, fallback_uncovered, strict_fallback_summary = _strict_fallback_decomposition(
        selected=result.get("selected", []),
        candidates=(candidate_universe + list(near_candidates)),
        target_keys=target_keys,
        params=params,
        probe_params=probe_params,
        constraint_modes=constraint_modes,
        min_clearance=min_clearance,
        min_entry=min_entry,
    )
    if strict_fallback_summary.get("applied"):
        result["selected"] = fallback_selected
        result["uncovered"] = sorted(set(fallback_uncovered))
    _update_task_progress(
        task_id,
        "strict_fallback_checked",
        0.92,
        (
            "Strict fallback applied"
            if strict_fallback_summary.get("applied")
            else "Strict fallback checked"
        ),
    )
    _check_task_cancelled(task_id, "strict_fallback_checked")

    selected = deepcopy(result["selected"])
    _build_counterfactual_explanations(selected, candidates)
    preference_preview = _build_preference_preview(candidates)
    preference_profiles = result.get("preferenceProfiles", {})
    robust_ci_coverages = []
    robust_ci_passrates = []
    risk_distance_means = []
    safety_pass_probabilities = []
    contact_region_union = set()
    risk_threshold_mm = float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT))
    contact_step_um = float(max(5.0, params.get("contactStepUm", 25.0)))
    contact_min_len_um = float(max(0.0, params.get("contactMinLengthUm", 0.0)))
    contact_mode = str(params.get("contactMode", "cylinder")).strip().lower()
    use_cylinder_contact = contact_mode != "centerline"
    for probe in selected:
        raw_ap = float(probe.get("ap_mm", 0))
        raw_ml = float(probe.get("ml_mm", 0))
        raw_dv = float(probe.get("entryDvCorr_mm", 0))
        corr_ap, corr_ml, corr_dv = _apply_correction(raw_ap, raw_ml, raw_dv, correction_factors)
        probe.setdefault("ap_mm_corrected", round(corr_ap, 5))
        probe.setdefault("ml_mm_corrected", round(corr_ml, 5))
        probe.setdefault("entryDvCorr_mm_corrected", round(corr_dv, 5))
        probe.setdefault("depthUm_corrected", round(float(probe.get("depthUm", 0)) * correction_factors["dv"], 2))
        interval = _build_coordinate_error_interval(raw_ap, raw_ml, raw_dv, correction_unc)
        if interval is not None:
            probe["errorInterval"] = interval

        risk_diag = _compute_probe_risk_diagnostics(
            probe,
            step_um=RISK_DIAG_STEP_UM,
            threshold_mm=float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
            threshold_by_class=params.get("riskThresholdsMm"),
            cvar_alpha=float(params.get("drccCvarAlpha", DRCC_CVAR_ALPHA_DEFAULT)),
        )
        if risk_diag is not None:
            probe["riskDiagnostics"] = risk_diag
            class_min = risk_diag.get("classMinDistanceMm") if isinstance(risk_diag.get("classMinDistanceMm"), dict) else {}
            probe["minDistanceByClass"] = deepcopy(class_min)
            probe["minDistanceByClassMm"] = deepcopy(class_min)
            flattened_class_distances = _flatten_risk_class_distance_fields(class_min)
            probe.update(flattened_class_distances)
            probe["mostDangerousSegmentMm"] = _most_dangerous_segment_payload(risk_diag)
            probe["thresholdSource"] = _threshold_source_from_risk_diag(
                risk_diag,
                params.get("riskThresholdsMm"),
            )
            min_risk_distance = risk_diag.get("minDistanceMm")
            if min_risk_distance is not None:
                probe["minSafetyDistanceMm"] = round(float(min_risk_distance), 6)
            probe["safetyDistanceModel"] = deepcopy(risk_model_summary)

        probe.update(_build_unified_probe_audit(probe, target_keys, params))

        unc_metrics = _compute_probe_uncertainty_metrics(probe, target_keys, params)
        if unc_metrics is not None:
            probe["coverageConfidence"] = {
                "mean": unc_metrics["coverageMean"],
                "ci95": unc_metrics["coverageCi95"],
                "passRate": unc_metrics["passRate"],
            }
            probe["safetyMarginCi95"] = unc_metrics["minSafetyMarginMmCi95"]
            if "minSafetyDistanceMmCi95" in unc_metrics:
                probe["minSafetyDistanceCi95"] = unc_metrics["minSafetyDistanceMmCi95"]
                probe["riskDistanceSigmaMm"] = unc_metrics.get("riskDistanceSigmaMm")
                probe["minSafetyDistanceMeanMm"] = unc_metrics.get("minSafetyDistanceMmMean")
                if unc_metrics.get("minSafetyDistanceMmMean") is not None:
                    risk_distance_means.append(float(unc_metrics["minSafetyDistanceMmMean"]))
            if "safetyPassProbability" in unc_metrics:
                probe["safetyDistanceConfidence"] = {
                    "thresholdMm": unc_metrics.get("safetyThresholdMm"),
                    "thresholdClass": unc_metrics.get("safetyThresholdClass"),
                    "thresholdSource": unc_metrics.get("safetyThresholdSource"),
                    "probability": unc_metrics.get("safetyPassProbability"),
                    "ci95": unc_metrics.get("safetyPassProbabilityCi95"),
                }
                if unc_metrics.get("safetyPassProbability") is not None:
                    safety_pass_probabilities.append(float(unc_metrics["safetyPassProbability"]))
            probe["uncertainty"] = {
                "entrySigmaMm": unc_metrics["entrySigmaMm"],
                "angleSigmaDeg": unc_metrics["angleSigmaDeg"],
                "depthSigmaMm": unc_metrics["depthSigmaMm"],
                "tipSigmaMm": unc_metrics["tipSigmaMm"],
                "tipSigmaAxesMm": unc_metrics["tipSigmaAxesMm"],
                "coneHalfAngleDeg": unc_metrics["coneHalfAngleDeg"],
            }
            probe["entrySigmaMm"] = unc_metrics["entrySigmaMm"]
            probe["angleSigmaDeg"] = unc_metrics["angleSigmaDeg"]
            probe["depthSigmaMm"] = unc_metrics["depthSigmaMm"]
            probe["riskDistanceSigmaMm"] = unc_metrics.get("riskDistanceSigmaMm", probe.get("riskDistanceSigmaMm"))
            robust_ci_coverages.append(float(unc_metrics["coverageMean"]))
            robust_ci_passrates.append(float(unc_metrics["passRate"]))

        contact_profile = _compute_probe_contact_profile(
            probe,
            step_um=contact_step_um,
            min_contact_length_um=contact_min_len_um,
            use_cylinder=use_cylinder_contact,
        )
        probe["contactRegionSet"] = contact_profile.get("contactRegionSet", [])
        probe["contactIntervals"] = contact_profile.get("contactIntervals", [])
        probe["contactProfile"] = contact_profile.get("contactProfile", [])
        probe["contactSamplingStepUm"] = contact_profile.get("samplingStepUm", contact_step_um)
        probe["contactMode"] = contact_profile.get("mode", "cylinder" if use_cylinder_contact else "centerline")
        for key in probe.get("contactRegionSet", []):
            contact_region_union.add(str(key))

        _ensure_probe_safety_statistics(probe, threshold_mm=risk_threshold_mm)
        conf = probe.get("safetyDistanceConfidence", {})
        statistical_safety = _build_probe_statistical_safety_payload(
            probe,
            threshold_mm=risk_threshold_mm,
            clinical_context=clinical_context,
        )
        probe["statisticalSafety"] = statistical_safety
        probe["coverage"] = round(_probe_coverage_rate(probe, target_keys), 6)
        probe["minDistanceMm"] = statistical_safety.get("minDistanceMm")
        probe["minDistanceCi95"] = statistical_safety.get("minDistanceCi95")
        probe["pSafetyGtThreshold"] = statistical_safety.get("pSafetyGtThreshold")
        probe["pSafetyGtThresholdCi95"] = statistical_safety.get("pSafetyGtThresholdCi95")
        probe["pass"] = bool(statistical_safety.get("pass"))
        probe["passFail"] = "pass" if probe["pass"] else "fail"
        probe["failureReason"] = statistical_safety.get("failureReason")
        probe["primaryEndpoints"] = {
            "coverageRate": probe["coverage"],
            "continuousMinSafetyDistanceMm": statistical_safety.get("minDistanceMm"),
            "minDistanceByClass": deepcopy(statistical_safety.get("minDistanceByClass") or {}),
            **_flatten_risk_class_distance_fields(statistical_safety.get("minDistanceByClass") or {}),
            "mostDangerousSegmentMm": probe.get("mostDangerousSegmentMm"),
            "thresholdSource": statistical_safety.get("thresholdSource") or probe.get("thresholdSource"),
            "thresholdClass": statistical_safety.get("thresholdClass"),
            "thresholdMarginMm": statistical_safety.get("thresholdMarginMm"),
            "entrySigmaMm": probe.get("entrySigmaMm"),
            "angleSigmaDeg": probe.get("angleSigmaDeg"),
            "depthSigmaMm": probe.get("depthSigmaMm"),
            "riskDistanceSigmaMm": probe.get("riskDistanceSigmaMm"),
            "minSafetyDistanceCi95": statistical_safety.get("minDistanceCi95"),
            "safetyPassProbability": statistical_safety.get("pSafetyGtThreshold"),
            "safetyPassProbabilityCi95": statistical_safety.get("pSafetyGtThresholdCi95"),
            "safetyThresholdMm": statistical_safety.get("thresholdMm"),
            "pass": statistical_safety.get("pass"),
            "failureReason": statistical_safety.get("failureReason"),
            "brainContainmentPassed": probe.get("brainContainmentPassed"),
            "tipInTarget": probe.get("tipInTarget"),
            "distalOvershootMm": probe.get("distalOvershootMm"),
            "internalRiskViolationCount": probe.get("internalRiskViolationCount"),
            "minimumInternalRiskMarginMm": probe.get("minimumInternalRiskMarginMm"),
            "entrySurfaceCrossingStatus": probe.get("entrySurfaceCrossingStatus"),
            "safetyPassProbabilityGuaranteed": (
                (probe.get("selectiveConformal") or {}).get("probabilityGuaranteed")
                if isinstance(probe.get("selectiveConformal"), dict)
                else None
            ),
            "reviewDecision": (
                (probe.get("selectiveConformal") or {}).get("decision")
                if isinstance(probe.get("selectiveConformal"), dict)
                else None
            ),
        }
    _check_task_cancelled(task_id, "per_probe_metrics_ready")
    _update_task_progress(task_id, "per_probe_metrics_ready", 0.94, "Per-probe metrics computed")

    required_tokens, covered_tokens, result["uncovered"], uncovered_side_items = _final_coverage_state(
        selected, target_keys, targeting_mode
    )
    token_cover_audit["returnedSelectionUnchanged"] = sorted(token_cover_selection) == sorted(
        _candidate_cover_identity(p, target_keys, targeting_mode) for p in selected
    )
    token_cover_audit["appliesToReturnedSelection"] = bool(
        token_cover_audit["returnedSelectionUnchanged"]
        and token_cover_audit.get("status") == "proven_optimal"
        and required_tokens <= covered_tokens
    )
    side_coverage = _build_side_coverage_summary(selected, target_keys)
    continuous_risk_policy = _continuous_risk_policy(constraint_modes)
    selective_conformal_summary = _apply_selective_conformal(selected, params)
    stratified_calibration = _build_stratified_calibration_summary(selected, clinical_context)
    statistical_contract_summary = _build_statistical_contract_summary(selected, stratified_calibration)
    for probe in selected:
        if not isinstance(probe.get("primaryEndpoints"), dict):
            continue
        sc = probe.get("selectiveConformal") if isinstance(probe.get("selectiveConformal"), dict) else {}
        probe["primaryEndpoints"]["safetyPassProbabilityGuaranteed"] = (
            sc.get("probabilityGuaranteed") if sc else None
        )
        probe["primaryEndpoints"]["reviewDecision"] = sc.get("decision") if sc else None
        probe["primaryEndpoints"]["manualReviewRequired"] = bool(probe.get("manualReviewRequired"))
        probe["primaryEndpoints"]["confidenceStatus"] = probe.get("confidenceStatus")
        probe["primaryEndpoints"]["reviewReason"] = probe.get("reviewReason")

    safety_issues = _collect_safety_issues(
        selected,
        params,
        probe_params,
        constraint_modes,
        candidates=(candidate_universe + list(near_candidates)),
    )
    failure_cards = [i.get("failureCard") for i in safety_issues if isinstance(i.get("failureCard"), dict)]
    candidate_stage_breakdown = _build_candidate_stage_breakdown(
        candidate_gen_summary=candidate_gen_summary,
        auto_avoid_prefilter_summary=auto_avoid_prefilter_summary,
        candidate_universe_count=len(candidate_universe),
        candidate_pool_count=len(candidates),
        selected_count=len(selected),
        strict_fallback_summary=strict_fallback_summary,
        score_cache_stats=score_cache_stats,
    )
    candidate_funnel_diagnostics = _build_candidate_funnel_diagnostics(
        candidate_gen_summary=candidate_gen_summary,
        auto_avoid_prefilter_summary=auto_avoid_prefilter_summary,
        candidate_universe=candidate_universe,
        candidate_pool=candidates,
        selected=selected,
        strict_fallback_summary=strict_fallback_summary,
        score_cache_stats=score_cache_stats,
        params=params,
        probe_params=probe_params,
        constraint_modes=constraint_modes,
        min_clearance=min_clearance,
        min_entry=min_entry,
    )
    candidate_stage_breakdown["candidateFunnelDiagnostics"] = candidate_funnel_diagnostics
    _enforce_safety_contract(
        safety_issues,
        extra={
            "failureCards": failure_cards,
            "safetyIssues": safety_issues,
            "candidateStageBreakdown": candidate_stage_breakdown,
            "candidateFunnelDiagnostics": candidate_funnel_diagnostics,
            "autoAvoidPrefilter": auto_avoid_prefilter_summary,
            "autoRepairSummary": auto_repair_summary,
            "repairPreferenceSummary": repair_preference_summary,
            "strictFallbackSummary": strict_fallback_summary,
            "candidateCount": len(candidates),
            "candidateUniverseCount": len(candidate_universe),
            "riskConstraintPolicy": continuous_risk_policy,
        },
    )
    _update_task_progress(task_id, "safety_contract_checked", 0.98, "Safety contract checked")

    elapsed_ms = int((time.time() - t0) * 1000)
    primary_endpoint_summary = _build_primary_endpoint_summary(
        selected=selected,
        target_keys=target_keys,
        required_tokens=required_tokens,
        covered_tokens=covered_tokens,
        elapsed_ms=elapsed_ms,
        clinical_context=clinical_context,
    )
    if isinstance(stratified_calibration, dict):
        global_cal = stratified_calibration.get("global") if isinstance(stratified_calibration.get("global"), dict) else {}
        if global_cal:
            primary_endpoint_summary["safetyCalibrationEce"] = global_cal.get("ece")
            primary_endpoint_summary["safetyCalibrationBrier"] = global_cal.get("brier")
    log.info(
        f"Optimize ({algorithm}): {len(selected)} probes, "
        f"{len(result['uncovered'])} uncovered, {elapsed_ms}ms"
    )
    structured_log(
        log,
        "optimize.completed",
        requestId=_request_id(),
        taskId=task_id,
        elapsedMs=elapsed_ms,
        selectedProbeCount=len(selected),
        uncoveredCount=len(result["uncovered"]),
        candidatePool=summarize_candidate_pool(candidates),
        cacheHits=int((score_cache_stats or {}).get("cacheHits", 0)),
    )
    _update_task_progress(task_id, "candidate_round_completed", 0.98, "Candidate round completed")

    return {
        "ok": True,
        "selected": selected,
        "uncovered": result["uncovered"],
        "uncoveredSides": uncovered_side_items,
        "targetingMode": targeting_mode,
        "requiredCoverageUnits": len(required_tokens),
        "coveredCoverageUnits": len(covered_tokens),
        "sideCoverage": side_coverage,
        "voxelMode": True,
        "algorithm": algorithm,
        "rankingMode": "drcc_pareto_robust" if algorithm == "drcc"
        else "pareto_robust" if params.get("paretoEnabled") and params.get("robustOptimization")
        else "pareto" if params.get("paretoEnabled")
        else "robust" if params.get("robustOptimization")
        else "default",
        "drccEnabled": bool(params.get("drccEnabled", False)),
        "drccSummary": result.get("drccSummary"),
        "preferenceProfiles": preference_profiles,
        "selectiveConformalSummary": selective_conformal_summary,
        "stratifiedCalibration": stratified_calibration,
        "statisticalContract": statistical_contract_summary,
        "robustOptimization": bool(params.get("robustOptimization", False)),
        "paretoEnabled": bool(params.get("paretoEnabled", False)),
        "robustParams": {
            "samples": int(params.get("robustSamples", 16)),
            "perturbSigmaMm": round(float(params.get("perturbSigmaMm", 0.15)), 6),
            "entrySigmaMm": round(float(params.get("entrySigmaMm", params.get("perturbSigmaMm", 0.15))), 6),
            "angleSigmaDeg": round(float(params.get("angleSigmaDeg", 2.0)), 6),
            "depthSigmaMm": round(float(params.get("depthSigmaMm", 0.2)), 6),
            "riskDistanceSigmaMm": round(float(params.get("riskDistanceSigmaMm", RISK_DISTANCE_SIGMA_MM_DEFAULT)), 6),
            "riskMarginTreMm": round(float(params.get("riskMarginTreMm", 0.0)), 6),
            "minRiskDistanceMm": round(float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)), 6),
            "baseMinRiskDistanceMm": round(float(params.get("baseMinRiskDistanceMm", params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT))), 6),
            "riskThresholdTemplate": str(params.get("riskThresholdTemplate", RISK_THRESHOLD_TEMPLATE_DEFAULT)),
            "riskThresholdsMm": deepcopy(params.get("riskThresholdsMm") or {}),
            "riskThresholdsMmBase": deepcopy(params.get("riskThresholdsMmBase") or {}),
            "riskMarginInflation": deepcopy(params.get("riskMarginInflation") or {}),
            "drccRiskEpsilon": round(float(params.get("drccRiskEpsilon", DRCC_RISK_EPSILON_DEFAULT)), 6),
            "drccWassersteinRadius": round(float(params.get("drccWassersteinRadius", DRCC_WASSERSTEIN_RADIUS_DEFAULT)), 6),
            "drccSolver": str(params.get("drccSolver", DRCC_SOLVER_DEFAULT)),
            "drccPerTargetKeep": int(params.get("drccPerTargetKeep", 6)),
            "drccExactMaxCandidates": int(params.get("drccExactMaxCandidates", DRCC_EXACT_MAX_CANDIDATES_DEFAULT)),
            "drccExactTimeLimitS": round(float(params.get("drccExactTimeLimitS", DRCC_EXACT_TIME_LIMIT_S_DEFAULT)), 3),
            "drccMisocpMaxCandidates": int(params.get("drccMisocpMaxCandidates", DRCC_MISOCP_MAX_CANDIDATES_DEFAULT)),
            "drccMisocpTimeLimitS": round(float(params.get("drccMisocpTimeLimitS", DRCC_MISOCP_TIME_LIMIT_S_DEFAULT)), 3),
            "drccMisocpSocWeight": round(float(params.get("drccMisocpSocWeight", DRCC_MISOCP_SOC_WEIGHT_DEFAULT)), 6),
            "sdfRiskPathWeight": round(float(params.get("sdfRiskPathWeight", SDF_RISK_PATH_WEIGHT_DEFAULT)), 6),
            "sdfRiskMarginWeight": round(float(params.get("sdfRiskMarginWeight", SDF_RISK_MARGIN_WEIGHT_DEFAULT)), 6),
            "riskSoftplusSigmaMm": round(float(params.get("riskSoftplusSigmaMm", OBJECTIVE_RISK_SIGMA_MM_DEFAULT)), 6),
            "objectiveRiskPrimary": bool(params.get("objectiveRiskPrimary", OBJECTIVE_RISK_PRIMARY_DEFAULT)),
            "objectiveRiskPrimaryBoost": round(float(params.get("objectiveRiskPrimaryBoost", OBJECTIVE_RISK_PRIMARY_BOOST_DEFAULT)), 6),
            "computeWorkers": int(params.get("computeWorkers", COMPUTE_WORKERS_DEFAULT)),
            "prefilterWorkers": int(params.get("prefilterWorkers", COMPUTE_PREFILTER_WORKERS_DEFAULT)),
            "computeChunkSize": int(params.get("computeChunkSize", COMPUTE_CHUNK_SIZE_DEFAULT)),
        },
        "uncertaintyCalibration": params.get("uncertaintyCalibration"),
        "riskThresholdTemplate": str(params.get("riskThresholdTemplate", RISK_THRESHOLD_TEMPLATE_DEFAULT)),
        "riskThresholdsMm": deepcopy(params.get("riskThresholdsMm") or {}),
        "riskThresholdsMmBase": deepcopy(params.get("riskThresholdsMmBase") or {}),
        "riskMarginInflation": deepcopy(params.get("riskMarginInflation") or {}),
        "riskThresholdTemplates": _risk_threshold_template_payload(
            params.get("riskThresholdTemplate", RISK_THRESHOLD_TEMPLATE_DEFAULT)
        ),
        "paretoParams": {
            "topK": int(params.get("paretoTopK", 12)),
            "objectivePreset": params.get("objectivePreset", "balanced"),
        },
        "objectiveWeights": {
            "coverage": round(float(params.get("objectiveCoverageWeight", 0.5)), 6),
            "safety": round(float(params.get("objectiveSafetyWeight", 0.3)), 6),
            "angle": round(float(params.get("objectiveAngleWeight", 0.2)), 6),
        },
        "objectiveRiskConfig": {
            "riskPrimary": bool(params.get("objectiveRiskPrimary", OBJECTIVE_RISK_PRIMARY_DEFAULT)),
            "riskPrimaryBoost": round(float(params.get("objectiveRiskPrimaryBoost", OBJECTIVE_RISK_PRIMARY_BOOST_DEFAULT)), 6),
            "riskSoftplusSigmaMm": round(float(params.get("riskSoftplusSigmaMm", OBJECTIVE_RISK_SIGMA_MM_DEFAULT)), 6),
            "sdfRiskPathWeight": round(float(params.get("sdfRiskPathWeight", SDF_RISK_PATH_WEIGHT_DEFAULT)), 6),
            "sdfRiskMarginWeight": round(float(params.get("sdfRiskMarginWeight", SDF_RISK_MARGIN_WEIGHT_DEFAULT)), 6),
        },
        "objectivePreset": params.get("objectivePreset", "balanced"),
        "preferencePreview": preference_preview,
        "clinicalContract": _clinical_contract_snapshot(),
        "clinicalContext": clinical_context,
        "primaryEndpointSummary": primary_endpoint_summary,
        "uncertaintySummary": {
            "meanCoverage": round(float(np.mean(robust_ci_coverages)), 6) if robust_ci_coverages else None,
            "meanPassRate": round(float(np.mean(robust_ci_passrates)), 6) if robust_ci_passrates else None,
            "meanMinSafetyDistanceMm": round(float(np.mean(risk_distance_means)), 6) if risk_distance_means else None,
            "meanSafetyPassProbability": round(float(np.mean(safety_pass_probabilities)), 6)
            if safety_pass_probabilities
            else None,
            "probeCount": len(robust_ci_coverages),
        },
        "contactSummary": {
            "samplingStepUm": round(contact_step_um, 6),
            "minContactLengthUm": round(contact_min_len_um, 6),
            "contactMode": "cylinder" if use_cylinder_contact else "centerline",
            "contactRegionSet": sorted(contact_region_union),
        },
        "paretoSummary": pareto_summary,
        "milpStatus": result.get("milpStatus"),
        "milpNodes": result.get("milpNodes"),
        "tokenCoverSearch": token_cover_audit,
        "solverRuntimePolicy": result.get("solverRuntimePolicy", solver_runtime_policy),
        "candidateCount": len(candidates),
        "candidateUniverseCount": len(candidate_universe),
        "candidatePreview": deepcopy(candidates) if bool(params.get("includeCandidatePreview", False)) else None,
        "autoAvoidPrefilter": auto_avoid_prefilter_summary,
        "autoRepairSummary": auto_repair_summary,
        "repairPreferenceSummary": repair_preference_summary,
        "strictFallbackSummary": strict_fallback_summary,
        "candidateCache": {
            "evaluated": int((score_cache_stats or {}).get("evaluated", len(candidates))),
            "cacheHits": int((score_cache_stats or {}).get("cacheHits", 0)),
            "workersUsed": int((score_cache_stats or {}).get("workersUsed", 1)),
            "parallelMode": (score_cache_stats or {}).get("parallelMode", "single_thread"),
            "chunkSize": int((score_cache_stats or {}).get("chunkSize", COMPUTE_CHUNK_SIZE_DEFAULT)),
            "earlyStopEnabled": bool((score_cache_stats or {}).get("earlyStopEnabled", False)),
            "earlyStopTopK": int((score_cache_stats or {}).get("earlyStopTopK", 0) or 0),
            "earlyStopHits": int((score_cache_stats or {}).get("earlyStopHits", 0) or 0),
            "earlyStopThreshold": (score_cache_stats or {}).get("earlyStopThreshold"),
        },
        "computeProfile": {
            "computeWorkersRequested": int(params.get("computeWorkers", COMPUTE_WORKERS_DEFAULT)),
            "prefilterWorkersRequested": int(params.get("prefilterWorkers", COMPUTE_PREFILTER_WORKERS_DEFAULT)),
            "candidateGenWorkersRequested": int(params.get("candidateGenWorkers", CANDIDATE_GEN_WORKERS_DEFAULT)),
            "streamingPipelineEnabled": bool(params.get("streamingPipelineEnabled", STREAMING_PIPELINE_ENABLED_DEFAULT)),
            "streamingPipelineRequested": bool(params.get("_streamingPipelineRequested", params.get("streamingPipelineEnabled", STREAMING_PIPELINE_ENABLED_DEFAULT))),
            "streamingPipelineApplied": bool(params.get("_streamingPipelineApplied", params.get("streamingPipelineEnabled", STREAMING_PIPELINE_ENABLED_DEFAULT))),
            "streamingPipelineReason": str(params.get("_streamingPipelineReason", "enabled")),
            "streamingScoreBatchSize": int(params.get("streamingScoreBatchSize", STREAMING_SCORE_BATCH_SIZE_DEFAULT)),
            "streamingQueueMaxSize": int(params.get("streamingQueueMaxSize", STREAMING_QUEUE_MAXSIZE_DEFAULT)),
            "streamingScoreWorkersMax": int(params.get("streamingScoreWorkersMax", STREAMING_SCORE_WORKERS_MAX_DEFAULT)),
            "computeChunkSize": int(params.get("computeChunkSize", COMPUTE_CHUNK_SIZE_DEFAULT)),
            "scoreWorkersUsed": int((score_cache_stats or {}).get("workersUsed", 1)),
            "scoreParallelMode": (score_cache_stats or {}).get("parallelMode", "single_thread"),
            "prefilterWorkersUsed": int((auto_avoid_prefilter_summary or {}).get("workersUsed", 1)),
            "candidateGeneration": candidate_gen_summary,
            "solverRuntimePolicy": result.get("solverRuntimePolicy", solver_runtime_policy),
            "runtimeThreadBudget": int(max(RUNTIME_NUM_THREADS_DEFAULT, params.get("computeWorkers", 1))),
            "sdfGpuEnabled": bool(SDF_GPU_ENABLED_DEFAULT),
            "trajectoryVoxelCacheSize": int(len(trajectory_voxel_cache)),
            "riskDiagCacheSize": int(len(risk_diag_cache)),
            "robustPerturbCacheSize": int(len(robust_perturb_cache)),
        },
        "candidateStageBreakdown": candidate_stage_breakdown,
        "candidateFunnelDiagnostics": candidate_funnel_diagnostics,
        "timeMs": elapsed_ms,
        "safetyIssues": safety_issues,
        "failureCards": failure_cards,
        "riskConstraintPolicy": continuous_risk_policy,
        "degradationNotices": _build_degradation_notices(
            constraint_modes=constraint_modes,
            qiu_payload=_qiu_payload(),
            mri_payload=_mri_payload(),
        ),
        "correctionApplied": payload["correctionApplied"],
        "correctionFactors": correction_factors,
        "correctionUncertainty": correction_unc,
        "atlasSignature": atlas_signature,
        "atlasHash": atlas_signature.get("hash"),
        "activeAtlasMode": ACTIVE_ATLAS_MODE,
        "riskModel": deepcopy(risk_model_summary),
        "mriLayerStatus": _build_mri_layer_status(
            mri_payload=_mri_payload(),
            risk_snapshot=deepcopy(risk_model_summary),
            active_mode=ACTIVE_ATLAS_MODE,
        ),
        "plannerCalibrationProfile": deepcopy(payload.get("plannerCalibration", planner_calibration_profile)),
        "reconstructionQuality": deepcopy(reconstruction_quality_state),
        "qualityGateFailureCard": _quality_gate_failure_card(),
        "versionFrozen": version_freeze_state.get("frozen", False),
        "versionFreezeState": deepcopy(version_freeze_state),
    }


_load_version_freeze_state()


@app.before_request
def _request_guard():
    g.started_at = time.time()
    g.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
    g.auth_subject = "anonymous"

    if not request.path.startswith("/api"):
        return None
    if not AUTH_ENABLED:
        if not is_loopback_addr(request.remote_addr):
            return _json_error(
                "Authentication required for non-local API access",
                status=403,
                code="auth_required",
                details=["Set PROBEPLANNER_API_TOKEN before exposing the backend outside localhost."],
            )
        return None

    token = _extract_bearer_token(request.headers.get("Authorization", ""))
    if token != API_TOKEN:
        return _json_error(
            "Unauthorized",
            status=401,
            code="unauthorized",
            details=["Missing or invalid bearer token"],
        )
    g.auth_subject = "token:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    return None


@app.after_request
def _request_audit(response):
    request_id = getattr(g, "request_id", None)
    if request_id:
        response.headers["X-Request-Id"] = request_id
    response.headers["X-Engine-Version"] = ENGINE_VERSION

    if request.path.startswith("/api"):
        duration_ms = int((time.time() - getattr(g, "started_at", time.time())) * 1000)
        try:
            payload_summary = _summarize_payload(request.get_json(silent=True))
        except Exception:
            payload_summary = {"type": "unavailable"}
        audit_record = {
            "ts": _utc_now_iso(),
            "requestId": request_id,
            "engineVersion": ENGINE_VERSION,
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "durationMs": duration_ms,
            "remoteAddr": request.headers.get("X-Forwarded-For", request.remote_addr),
            "authSubject": getattr(g, "auth_subject", "anonymous"),
            "payload": payload_summary,
        }
        try:
            _write_audit_record(audit_record)
        except Exception as exc:
            log.warning(f"Audit log write failed: {exc}")
    return response


@app.route("/api/atlas/mode", methods=["GET", "POST"])
def api_atlas_mode():
    global ACTIVE_ATLAS_MODE
    qiu_payload = _qiu_payload()
    mri_payload = _mri_payload()
    with risk_model_lock:
        risk_snapshot = deepcopy(risk_model_summary)
    mri_layer_status = _build_mri_layer_status(
        mri_payload=mri_payload,
        risk_snapshot=risk_snapshot,
        active_mode=ACTIVE_ATLAS_MODE,
    )
    active_profile_contract = _build_active_profile_contract(
        qiu_payload=qiu_payload,
        mri_payload=mri_payload,
        risk_snapshot=risk_snapshot,
    )
    mode_contracts = {
        "allen_sdk": {
            "mode": "allen_sdk",
            "profileId": "allen_ccfv3",
            "artifactReady": True,
            "annotationPath": "AllenSDK ReferenceSpaceCache annotation",
            "regionMapPath": "AllenSDK structure tree",
            "manifestPath": None,
            "statsPath": None,
            "skullMaskPath": None,
            "outerTissueMaskPath": None,
            "layeredSdfPath": None,
            "missingArtifacts": [],
            "contractViolations": [],
        },
        "qiu2018": {
            "mode": "qiu2018",
            "profileId": "qiu2018",
            "artifactReady": bool(qiu_payload.get("annotationConfigured")),
            "annotationPath": qiu_payload.get("annotationPath"),
            "regionMapPath": qiu_payload.get("regionMapPath"),
            "manifestPath": None,
            "statsPath": qiu_payload.get("statsPath"),
            "jacobianPath": qiu_payload.get("jacobianPath"),
            "skullMaskPath": None,
            "outerTissueMaskPath": None,
            "layeredSdfPath": None,
            "missingArtifacts": [] if qiu_payload.get("annotationConfigured") else ["annotation"],
            "contractViolations": [],
        },
        "mri_ccf_hybrid": {
            "mode": "mri_ccf_hybrid",
            "profileId": mri_payload.get("profileId") or mri_payload.get("datasetProfile"),
            "artifactReady": bool(mri_payload.get("artifactReady", False)),
            "manifestPath": mri_payload.get("manifestPath"),
            "activeProfilePath": mri_payload.get("activeProfilePath"),
            "annotationPath": mri_payload.get("annotationPath"),
            "regionMapPath": mri_payload.get("regionMapV2Path") or mri_payload.get("regionMapPath"),
            "statsPath": mri_payload.get("statsPath"),
            "registrationSummaryPath": mri_payload.get("registrationSummaryPath"),
            "jacobianPath": mri_payload.get("jacobianPath"),
            "skullMaskPath": mri_payload.get("skullMaskPath"),
            "outerTissueMaskPath": mri_payload.get("outerTissueMaskPath"),
            "layeredSdfPath": mri_payload.get("layeredSdfPath"),
            "mriSurfaceMeshManifestPath": mri_payload.get("mriSurfaceMeshManifestPath"),
            "missingArtifacts": list(mri_payload.get("missingArtifacts") or []),
            "contractViolations": deepcopy(mri_payload.get("contractViolations") or []),
            "pathConsistency": deepcopy(mri_payload.get("pathConsistency") or {}),
        },
    }
    if request.method == "GET":
        return jsonify(
            {
                "ok": True,
                "activeAtlasMode": ACTIVE_ATLAS_MODE,
                "supportedAtlasModes": list(ATLAS_MODES),
                "qiu2018": qiu_payload,
                "mriCcfHybrid": mri_payload,
                "mriLayerStatus": mri_layer_status,
                # Backward-compatible aliases for frontends that read flat status flags.
                "annotationConfigured": bool(qiu_payload["annotationConfigured"] or mri_payload["annotationConfigured"]),
                "regionMapConfigured": bool(qiu_payload["regionMapConfigured"] or mri_payload["regionMapConfigured"]),
                "statsConfigured": bool(qiu_payload["statsConfigured"] or mri_payload["statsConfigured"]),
                "jacobianConfigured": bool(qiu_payload["jacobianConfigured"] or mri_payload["jacobianConfigured"]),
                "reconstructionQuality": deepcopy(reconstruction_quality_state),
                "qualityGateFailureCard": _quality_gate_failure_card(),
                "dynamicUncertaintyProfile": deepcopy(dynamic_uncertainty_profile),
                "artifactReady": bool(
                    mri_payload.get("artifactReady", True)
                    if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid"
                    else qiu_payload.get("annotationConfigured", True)
                ),
                "missingArtifacts": list(
                    mri_payload.get("missingArtifacts") or []
                    if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid"
                    else ([] if qiu_payload.get("annotationConfigured") else ["annotation"])
                ),
                "requestId": _request_id(),
                "engineVersion": ENGINE_VERSION,
            }
        )

    body = _request_json_object(required=False)
    requested_mode = _normalize_atlas_mode(body.get("mode"))
    paper_identity = app.config.get("PAPER_RUNTIME_IDENTITY") or {}
    locked_mode = paper_identity.get("atlasMode") if paper_identity.get("profileContractSha256") else None
    if locked_mode and requested_mode != locked_mode:
        return _json_error(
            "This paper runtime is bound to its startup atlas profile; start a separate runtime to change mode.",
            status=409, code="paper_profile_mode_locked",
        )
    with atlas_loader_lock:
        if requested_mode != ACTIVE_ATLAS_MODE and _atlas_loader_alive():
            return _json_error(
                "Atlas loader is busy, please retry after current load completes.",
                status=409,
                code="atlas_loader_busy",
            )
    changed = _apply_active_atlas_mode(requested_mode)
    if changed or not atlas_loaded:
        _ensure_atlas_loader(force_retry=True)
    return jsonify(
        {
            "ok": True,
            "changed": bool(changed),
            "activeAtlasMode": ACTIVE_ATLAS_MODE,
            "supportedAtlasModes": list(ATLAS_MODES),
            "requestId": _request_id(),
            "engineVersion": ENGINE_VERSION,
        }
    )


@app.route("/api/mri/reconstruct/start", methods=["POST"])
def api_mri_reconstruct_start():
    body = _request_json_object(required=False)
    task = _new_mri_task(kind="reconstruct")
    task_id = task["taskId"]
    with mri_reconstruct_lock:
        mri_reconstruct_tasks[task_id] = task

    activate_after = _coerce_bool(body.get("activateAfterBuild", True), default=True)
    switch_mode = _coerce_bool(body.get("switchMode", True), default=True)

    def _worker():
        bundle = {}
        try:
            _update_mri_task(
                task_id,
                state="running",
                stage="validate_input",
                progress=0.12,
                message="Validating MRI/CCF artifacts",
            )

            bundle = {
                "manifestPath": body.get("manifestPath"),
                "activeProfilePath": body.get("activeProfilePath"),
                "annotationPath": body.get("annotationPath"),
                "regionMapPath": body.get("regionMapPath"),
                "regionMapV2Path": body.get("regionMapV2Path"),
                "statsPath": body.get("statsPath"),
                "registrationSummaryPath": body.get("registrationSummaryPath"),
                "jacobianPath": body.get("jacobianPath"),
                "skullMaskPath": body.get("skullMaskPath"),
                "outerTissueMaskPath": body.get("outerTissueMaskPath"),
                "layeredSdfPath": body.get("layeredSdfPath"),
                "datasetProfile": body.get("datasetProfile") or "custom",
            }

            label_image = str(body.get("labelImage") or "").strip()
            output_dir = str(body.get("outputDir") or "").strip()
            auto_build = _coerce_bool(body.get("autoBuild", False), default=False)
            if auto_build and label_image and output_dir:
                script_path = (Path(__file__).resolve().parents[1] / "scripts" / "build_mri_ccf_hybrid_artifacts.py")
                if not script_path.exists():
                    raise RuntimeError(f"Missing script: {script_path}")
                _update_mri_task(
                    task_id,
                    stage="build_artifacts",
                    progress=0.32,
                    message="Building MRI hybrid artifacts from label image",
                )
                cmd = [
                    sys.executable,
                    str(script_path),
                    "--label-image",
                    str(label_image),
                    "--output-dir",
                    str(output_dir),
                    "--expected-shape",
                    ",".join(str(v) for v in ATLAS_SHAPE),
                ]
                reference_image = str(body.get("referenceImage") or "").strip()
                if reference_image:
                    cmd.extend(["--reference-image", reference_image])
                subprocess.run(cmd, check=True)
                out_dir = Path(output_dir).expanduser().resolve()
                bundle["annotationPath"] = str(out_dir / "annotation.npy")
                bundle["regionMapPath"] = bundle.get("regionMapPath") or str(out_dir / "region_map.json")
                bundle["regionMapV2Path"] = bundle.get("regionMapV2Path") or str(out_dir / "region_map_v2.json")
                bundle["statsPath"] = bundle.get("statsPath") or str(out_dir / "stats.json")
                bundle["registrationSummaryPath"] = (
                    bundle.get("registrationSummaryPath") or str(out_dir / "registration_summary.json")
                )
                bundle["jacobianPath"] = bundle.get("jacobianPath") or str(out_dir / "jacobian.npy")
                bundle["skullMaskPath"] = bundle.get("skullMaskPath") or str(out_dir / "skull_mask.npy")
                bundle["outerTissueMaskPath"] = bundle.get("outerTissueMaskPath") or str(out_dir / "outer_tissue_mask.npy")
                bundle["layeredSdfPath"] = bundle.get("layeredSdfPath") or str(out_dir / "layered_sdf.npz")
                bundle["manifestPath"] = bundle.get("manifestPath") or str(out_dir / "artifact_manifest.json")
                bundle["activeProfilePath"] = bundle.get("activeProfilePath") or str(out_dir / "active_profile.json")

            _update_mri_task(
                task_id,
                stage="validate_artifacts",
                progress=0.64,
                message="Validating MRI hybrid artifact bundle",
            )
            normalized, summary = _validate_mri_artifact_bundle(bundle)
            _update_mri_task(
                task_id,
                stage="store_config",
                progress=0.82,
                message="Persisting MRI hybrid runtime config",
            )
            _mri_runtime_update(normalized)

            if activate_after:
                if switch_mode:
                    _apply_active_atlas_mode("mri_ccf_hybrid")
                _ensure_atlas_loader(force_retry=True)

            _update_mri_task(
                task_id,
                state="completed",
                stage="completed",
                progress=1.0,
                message="MRI hybrid artifacts ready",
                result={
                    "bundle": normalized,
                    "summary": summary,
                    "activateAfterBuild": bool(activate_after),
                    "switchMode": bool(switch_mode),
                    "activeAtlasMode": ACTIVE_ATLAS_MODE,
                },
            )
        except Exception as exc:
            log.exception("MRI reconstruct worker failed taskId=%s: %s", task_id, exc)
            repair_hints = _build_mri_repair_hints(str(exc), bundle)
            _update_mri_task(
                task_id,
                state="failed",
                stage="failed",
                progress=1.0,
                message=str(exc),
                error=str(exc),
                repairHints=repair_hints,
            )

    threading.Thread(target=_worker, daemon=True, name=f"mri-reconstruct-{task_id}").start()
    return jsonify(
        {
            "ok": True,
            "taskId": task_id,
            "state": TASK_STATE_QUEUED,
            "status": TASK_STATE_QUEUED,
            "requestId": _request_id(),
            "engineVersion": ENGINE_VERSION,
        }
    )


@app.route("/api/mri/reconstruct/status", methods=["GET"])
def api_mri_reconstruct_status():
    task_id = str(request.args.get("taskId") or "").strip()
    def _normalize_task_row(row):
        if not isinstance(row, dict):
            return row
        out = _public_mri_task_snapshot(row)
        out["status"] = normalize_task_status(out.get("status", out.get("state", TASK_STATE_QUEUED)))
        out["state"] = out["status"]
        out["progress"] = _normalize_progress_01(out.get("progress", 0.0))
        return out
    with mri_reconstruct_lock:
        if task_id:
            task = _normalize_task_row(mri_reconstruct_tasks.get(task_id))
            if task is None:
                return _json_error("MRI reconstruct task not found", status=404, code="mri_task_not_found")
            return jsonify(
                {
                    "ok": True,
                    "task": task,
                    "requestId": _request_id(),
                    "engineVersion": ENGINE_VERSION,
                }
            )
        tasks = [_normalize_task_row(x) for x in mri_reconstruct_tasks.values()]
    tasks.sort(key=lambda x: str(x.get("updatedAt") or ""), reverse=True)
    return jsonify(
        {
            "ok": True,
            "tasks": tasks[:20],
            "requestId": _request_id(),
            "engineVersion": ENGINE_VERSION,
        }
    )


@app.route("/api/mri/registration/summary", methods=["GET"])
def api_mri_registration_summary():
    payload = _mri_payload()
    configured = bool(payload.get("registrationSummaryConfigured"))
    summary = _load_mri_hybrid_registration_summary() if configured else {}
    stats = _load_mri_hybrid_stats() if bool(payload.get("statsConfigured")) else {}
    legacy_tre_enabled = bool(
        stats.get("anatomicalTreAvailable", summary.get("anatomicalTreAvailable", True))
    )
    # Keep the QC panel tied to the active profile contract.  The registration
    # summary often contains intermediate/proxy metrics, while stats.json is the
    # final quality report produced for the activated profile.
    summary_metrics = {
        "dice": _metric_from_stats(summary, "dice"),
        "hd95Mm": _metric_from_stats(summary, "hd95Mm", "hd95"),
        "treMm": _metric_from_stats(summary, "treMm", "tre"),
        "jacobianAnomalyRatio": _metric_from_stats(
            summary,
            "jacobianAnomalyRatio",
            "jacAnomalyRatio",
            "jacobian_anomaly_ratio",
        ),
    }
    stats_metrics = {
        "dice": _metric_from_stats(stats, "dice"),
        "hd95Mm": _metric_from_stats(stats, "hd95Mm", "hd95"),
        "treMm": _metric_from_stats(stats, "treMm", "tre"),
        "jacobianAnomalyRatio": _metric_from_stats(
            stats,
            "jacobianAnomalyRatio",
            "jacAnomalyRatio",
            "jacobian_anomaly_ratio",
        ),
    }
    consistency = payload.get("pathConsistency") if isinstance(payload.get("pathConsistency"), dict) else {}
    consistency_paths = consistency.get("paths") if isinstance(consistency.get("paths"), dict) else {}
    reg_consistency = (
        consistency_paths.get("registration_summary")
        if isinstance(consistency_paths.get("registration_summary"), dict)
        else {}
    )
    reg_payload_path = reg_consistency.get("payloadPath") or payload.get("registrationSummaryPath")
    reg_manifest_path = reg_consistency.get("manifestPath")
    registration_matches_manifest = (
        not reg_manifest_path
        or not reg_payload_path
        or _canonical_existing_path(reg_payload_path) == _canonical_existing_path(reg_manifest_path)
    )
    try:
        stats_profile_root = Path(str(payload.get("statsPath") or "")).expanduser().resolve().parent
        reg_path_obj = Path(str(reg_payload_path or "")).expanduser().resolve()
        reg_profile_root = reg_path_obj.parent.parent if reg_path_obj.parent.name.lower() == "registration" else reg_path_obj.parent
        stats_and_registration_same_profile = bool(
            str(payload.get("statsPath") or "").strip()
            and str(reg_payload_path or "").strip()
            and _canonical_existing_path(stats_profile_root) == _canonical_existing_path(reg_profile_root)
        )
    except Exception:
        stats_and_registration_same_profile = False
    stats_authoritative = bool(
        payload.get("statsConfigured")
        and consistency.get("passed") is True
        and registration_matches_manifest
        and stats_and_registration_same_profile
    )
    primary_metrics = stats_metrics if stats_authoritative else summary_metrics
    fallback_metrics = summary_metrics if stats_authoritative else stats_metrics
    primary_label = "active_profile_stats" if stats_authoritative else "registration_summary"
    fallback_label = "registration_summary_fallback" if stats_authoritative else "stats_json_fallback"
    metrics = {
        "dice": primary_metrics["dice"],
        "hd95Mm": primary_metrics["hd95Mm"],
        "treMm": primary_metrics["treMm"],
        "jacobianAnomalyRatio": primary_metrics["jacobianAnomalyRatio"],
    }
    metric_evidence = {}
    metric_records = {}
    for mk in ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio"):
        if primary_metrics.get(mk) is not None:
            metric_evidence[mk] = primary_label
            metric_records[mk] = stats if stats_authoritative else summary
        elif stats_authoritative and fallback_metrics.get(mk) is not None:
            metrics[mk] = fallback_metrics[mk]
            metric_evidence[mk] = fallback_label
            metric_records[mk] = summary
    reg_block = summary.get("registration") if isinstance(summary.get("registration"), dict) else {}
    if metrics["treMm"] is None and legacy_tre_enabled:
        off = reg_block.get("initialCenterOffset")
        if isinstance(off, (list, tuple)) and len(off) >= 3:
            try:
                metrics["treMm"] = float(np.linalg.norm(np.asarray(off[:3], dtype=np.float64)))
                metric_evidence["treMm"] = "registration_center_offset_proxy"
            except Exception:
                pass
    if metrics["dice"] is None:
        affine_metric = _metric_float(reg_block.get("affineMetric"))
        rigid_metric = _metric_float(reg_block.get("rigidMetric"))
        metric_base = affine_metric if affine_metric is not None else rigid_metric
        if metric_base is not None:
            metrics["dice"] = _clip(1.0 - float(metric_base), 0.0, 1.0)
            metric_evidence["dice"] = "registration_metric_proxy"
    if metrics["hd95Mm"] is None and metrics["treMm"] is not None:
        metrics["hd95Mm"] = max(0.0, float(metrics["treMm"]) * 0.6)
        metric_evidence["hd95Mm"] = "tre_proxy"
    if metrics["jacobianAnomalyRatio"] is None and reg_block.get("usedDeformable") is False:
        metrics["jacobianAnomalyRatio"] = 0.0
        metric_evidence["jacobianAnomalyRatio"] = "rigid_affine_no_deformable"
    jacobian_summary = _compute_jacobian_summary_from_file(payload.get("jacobianPath"))
    if metrics["jacobianAnomalyRatio"] is None and isinstance(jacobian_summary, dict):
        metrics["jacobianAnomalyRatio"] = jacobian_summary.get("anomalyRatio")
        metric_evidence["jacobianAnomalyRatio"] = str(jacobian_summary.get("evidence") or "jacobian_volume")
    # Last resort fallback for malformed/older stats files.
    for mk in ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio"):
        if metrics.get(mk) is None and stats_metrics.get(mk) is not None:
            metrics[mk] = stats_metrics[mk]
            metric_evidence[mk] = "stats_json_fallback"
            metric_records[mk] = stats
    for key, value in list(metrics.items()):
        if value is not None:
            metrics[key] = round(float(value), 6)
    metric_ci = stats.get("metricCi95") if isinstance(stats.get("metricCi95"), dict) else {}
    tre_axis = (
        stats.get("treAxisMm")
        or stats.get("treByAxisMm")
        or summary.get("treAxisMm")
        or summary.get("treByAxisMm")
        or {}
    )
    transform = {
        "usedRigid": bool(reg_block.get("usedRigid", False)),
        "usedAffine": bool(reg_block.get("usedAffine", False)),
        "usedDeformable": bool(reg_block.get("usedDeformable", False)),
        "skipDeformable": bool(reg_block.get("skipDeformable", False)),
        "type": "deformable" if reg_block.get("usedDeformable") else "affine" if reg_block.get("usedAffine") else "rigid" if reg_block.get("usedRigid") else "unknown",
        "rigidMetric": _metric_float(reg_block.get("rigidMetric")),
        "affineMetric": _metric_float(reg_block.get("affineMetric")),
        "deformableMetric": _metric_float(reg_block.get("deformableMetric")),
    }
    quality_snapshot = deepcopy(reconstruction_quality_state) if isinstance(reconstruction_quality_state, dict) else {}
    verified_hashes = (payload.get("verifiedArtifactHashes") or {}) if consistency.get("passed") is True else {}
    bound_metric_records = {
        key: record for key, record in metric_records.items()
        if verified_hashes.get("stats" if record is stats else "registration_summary")
    }
    evidence_assessment = assess_registration_metrics(
        metrics, metric_evidence, bound_metric_records,
        verified_hashes=verified_hashes,
        jacobian=jacobian_summary,
    )
    return jsonify(
        {
            "ok": True,
            "activeAtlasMode": ACTIVE_ATLAS_MODE,
            "configured": configured,
            "path": payload.get("registrationSummaryPath"),
            "profileId": payload.get("profileId") or payload.get("datasetProfile"),
            "manifestPath": payload.get("manifestPath"),
            "statsPath": payload.get("statsPath"),
            "datasetProfile": payload.get("datasetProfile"),
            "pathConsistency": deepcopy(payload.get("pathConsistency") or {}),
            "summary": summary,
            "metrics": metrics,
            **evidence_assessment,
            "controlPointResidual": stats.get("controlPointResidual") or summary.get("controlPointResidual"),
            "qualityGateStatus": "FAIL" if quality_snapshot.get("passed") is False else "PASS" if evidence_assessment["measurementEvidenceComplete"] else "INCOMPLETE_MEASUREMENT_EVIDENCE",
            "qualityGatePassed": quality_snapshot.get("passed") is True and evidence_assessment["measurementEvidenceComplete"],
            "technicalChecksPassed": quality_snapshot.get("passed"),
            "qualityGateReasons": quality_snapshot.get("blockingReasons") or quality_snapshot.get("reasons") or [],
            "thresholds": quality_snapshot.get("thresholds") or {},
            "metricCi95": metric_ci,
            "treAxisMm": tre_axis,
            "transform": transform,
            "jacobianSummary": jacobian_summary or {},
            "metricEvidence": metric_evidence,
            "requestId": _request_id(),
            "engineVersion": ENGINE_VERSION,
        }
    )


@app.route("/api/mri/overlay/status", methods=["GET"])
def api_mri_overlay_status():
    payload = _mri_payload()
    mri_active = ACTIVE_ATLAS_MODE == "mri_ccf_hybrid"
    quality_snapshot = deepcopy(reconstruction_quality_state) if isinstance(reconstruction_quality_state, dict) else {}
    risk_snapshot = deepcopy(risk_model_summary) if isinstance(risk_model_summary, dict) else {}
    registration_summary = (
        _load_mri_hybrid_registration_summary()
        if bool(payload.get("registrationSummaryConfigured"))
        else {}
    )
    mri_template_path = str(
        registration_summary.get("ambmcImage")
        or registration_summary.get("mriTemplate")
        or registration_summary.get("mriTemplatePath")
        or ""
    ).strip()
    mri_template_configured = bool(mri_active and mri_template_path and _path_exists(mri_template_path))

    layers = {
        "ccfTemplate": {
            "configured": bool(atlas_annotation is not None),
            "source": "active_annotation_volume",
        },
        "mriTemplate": {
            "configured": mri_template_configured,
            "path": mri_template_path or None,
            "source": "registration_summary_mri_template" if mri_template_configured else "not_configured",
        },
        "annotationBoundary": {
            "configured": bool(payload.get("annotationConfigured")),
            "path": payload.get("annotationPath"),
        },
        "skullBoundary": {
            "configured": bool(payload.get("skullMaskConfigured")),
            "path": payload.get("skullMaskPath"),
        },
        "outerTissueBoundary": {
            "configured": bool(payload.get("outerTissueMaskConfigured")),
            "path": payload.get("outerTissueMaskPath"),
        },
        "layeredSdf": {
            "configured": bool(payload.get("layeredSdfConfigured")),
            "path": payload.get("layeredSdfPath"),
            "riskSourceTag": risk_snapshot.get("sourceTag"),
        },
        "checkerboard": {
            "configured": bool(payload.get("annotationConfigured") and payload.get("registrationSummaryConfigured")),
            "source": "ccf_vs_mri_overlay",
        },
        "jacobianHeatmap": {
            "configured": bool(payload.get("jacobianConfigured")),
            "path": payload.get("jacobianPath"),
            "source": "jacobian_determinant_volume",
        },
    }
    missing = [key for key, row in layers.items() if not row.get("configured")]
    return jsonify(
        {
            "ok": True,
            "activeAtlasMode": ACTIVE_ATLAS_MODE,
            "profileId": payload.get("profileId") or payload.get("datasetProfile"),
            "manifestPath": payload.get("manifestPath"),
            "statsPath": payload.get("statsPath"),
            "pathConsistency": deepcopy(payload.get("pathConsistency") or {}),
            "qualityGatePassed": quality_snapshot.get("passed"),
            "layers": layers,
            "missingLayers": missing,
            "overlayModes": [
                "ccf_template",
                "mri_template",
                "annotation_boundary",
                "skull_boundary",
                "outer_tissue_boundary",
                "checkerboard",
                "jacobian_heatmap",
            ],
            "opacity": {"min": 0.0, "max": 1.0, "default": 0.55},
            "requestId": _request_id(),
            "engineVersion": ENGINE_VERSION,
        }
    )


def _qc_metric_float(value):
    out = _metric_float(value)
    if out is None:
        return None
    return round(float(out), 6)


def _qc_metric(metrics, *keys):
    if not isinstance(metrics, dict):
        return None
    for key in keys:
        value = _metric_float(metrics.get(key))
        if value is not None:
            return round(float(value), 6)
    return None


def _qc_ci(metric_ci, *keys):
    if not isinstance(metric_ci, dict):
        return None
    for key in keys:
        raw = metric_ci.get(key)
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            lo = _metric_float(raw[0])
            hi = _metric_float(raw[1])
            if lo is not None and hi is not None:
                if lo > hi:
                    lo, hi = hi, lo
                return [round(float(lo), 6), round(float(hi), 6)]
    return None


def _qc_check(status, message, evidence=None, required=None):
    return {
        "status": status,
        "passed": True if status == "pass" else False if status == "fail" else None,
        "assessmentScope": "metadata_consistency_only",
        "message": str(message or ""),
        "evidence": evidence,
        "required": required,
    }


def _build_mri_registration_qc_payload():
    summary_resp = api_mri_registration_summary()
    overlay_resp = api_mri_overlay_status()
    try:
        summary_payload = summary_resp.get_json(silent=True) or {}
    except Exception:
        summary_payload = {}
    try:
        overlay_payload = overlay_resp.get_json(silent=True) or {}
    except Exception:
        overlay_payload = {}

    summary = summary_payload.get("summary") if isinstance(summary_payload.get("summary"), dict) else {}
    metrics_raw = summary_payload.get("metrics") if isinstance(summary_payload.get("metrics"), dict) else {}
    metric_ci = summary_payload.get("metricCi95") if isinstance(summary_payload.get("metricCi95"), dict) else {}
    tre_axis = summary_payload.get("treAxisMm") if isinstance(summary_payload.get("treAxisMm"), dict) else {}
    transform = summary_payload.get("transform") if isinstance(summary_payload.get("transform"), dict) else {}
    jacobian = summary_payload.get("jacobianSummary") if isinstance(summary_payload.get("jacobianSummary"), dict) else {}
    quality_reasons = summary_payload.get("qualityGateReasons")
    if not isinstance(quality_reasons, list):
        quality_reasons = []
    anatomical_tre_available = summary_payload.get("anatomicalTreAvailable") is True
    control_point_residual = summary_payload.get("controlPointResidual")

    reg_block = summary.get("registration") if isinstance(summary.get("registration"), dict) else {}
    tre_mean = _qc_metric(metrics_raw, "treMeanMm", "treMm", "tre")
    tre_median = _qc_metric(metrics_raw, "treMedianMm", "treMedian", "treMm", "tre")
    tre_max = _qc_metric(metrics_raw, "treMaxMm", "treMax", "treMm", "tre")
    metrics = {
        "dice": _qc_metric(metrics_raw, "dice"),
        "hd95Mm": _qc_metric(metrics_raw, "hd95Mm", "hd95"),
        "treMeanMm": tre_mean,
        "treMedianMm": tre_median,
        "treMaxMm": tre_max,
        "treMm": tre_mean,
        "treCi95Mm": _qc_ci(metric_ci, "treMm", "tre", "treMeanMm"),
        "treAxisMm": {
            "AP": _qc_metric(tre_axis, "AP", "ap", "apMm"),
            "ML": _qc_metric(tre_axis, "ML", "ml", "mlMm"),
            "DV": _qc_metric(tre_axis, "DV", "dv", "dvMm"),
        },
        "jacobianMean": _qc_metric(jacobian, "mean"),
        "jacobianStd": _qc_metric(jacobian, "std"),
        "jacobianMin": _qc_metric(jacobian, "min"),
        "jacobianMax": _qc_metric(jacobian, "max"),
        "jacobianAnomalyRatio": _qc_metric(metrics_raw, "jacobianAnomalyRatio", "jacAnomalyRatio"),
    }
    if metrics["jacobianAnomalyRatio"] is None:
        metrics["jacobianAnomalyRatio"] = _qc_metric(jacobian, "anomalyRatio")

    ccf_template = str(summary.get("ccfTemplate") or summary.get("fixedImage") or summary.get("fixed") or "").strip()
    ambmc_image = str(summary.get("ambmcImage") or summary.get("mriTemplate") or summary.get("movingImage") or summary.get("moving") or "").strip()
    fixed_lower = ccf_template.lower()
    moving_lower = ambmc_image.lower()
    direction_ok = bool(
        ccf_template
        and ambmc_image
        and ("ccf" in fixed_lower or "allen" in fixed_lower or "template" in fixed_lower)
        and ("ambmc" in moving_lower or "mri" in moving_lower or "moving" in moving_lower)
    )
    if ccf_template or ambmc_image:
        direction_status = "recorded" if direction_ok else "warn"
        direction_message = f"fixed={ccf_template or '-'}; moving={ambmc_image or '-'}"
    else:
        direction_status = "unknown"
        direction_message = "fixed/moving image paths are not recorded"

    spacing_payload = summary.get("spacing") if isinstance(summary.get("spacing"), dict) else {}
    spacing_mm = (
        spacing_payload.get("spacingMm")
        or spacing_payload.get("voxelSizeMm")
        or summary.get("spacingMm")
        or summary.get("voxelSizeMm")
    )
    spacing_evidence = spacing_mm
    spacing_status = "unknown"
    spacing_message = "spacing is not recorded in registration_summary"
    if spacing_mm is None:
        manifest_path = summary_payload.get("manifestPath") or overlay_payload.get("manifestPath")
        manifest = _safe_read_json_obj(manifest_path)
        voxel_um = _metric_float(manifest.get("voxelSizeUm")) if isinstance(manifest, dict) else None
        atlas_shape = manifest.get("atlasShape") if isinstance(manifest, dict) else None
        if voxel_um is not None:
            inferred_mm = float(voxel_um) / 1000.0
            spacing_mm = [inferred_mm, inferred_mm, inferred_mm]
            spacing_evidence = {
                "source": "artifact_manifest.voxelSizeUm",
                "voxelSizeUm": float(voxel_um),
                "atlasShape": atlas_shape,
                "spacingMm": spacing_mm,
            }
            spacing_message = f"spacingMm inferred from manifest voxelSizeUm={float(voxel_um):.3f}um"
    if isinstance(spacing_mm, (list, tuple)) and len(spacing_mm) >= 3:
        vals = [_metric_float(x) for x in spacing_mm[:3]]
        if all(x is not None for x in vals):
            spacing_status = "recorded" if max(abs(float(x) - 0.025) for x in vals) <= 0.0025 else "warn"
            if not str(spacing_message or "").startswith("spacingMm inferred"):
                spacing_message = "spacingMm=" + ",".join(f"{float(x):.6f}" for x in vals)
    elif _metric_float(spacing_mm) is not None:
        val = float(_metric_float(spacing_mm))
        spacing_status = "recorded" if abs(val - 0.025) <= 0.0025 else "warn"
        spacing_message = f"spacingMm={val:.6f}"

    interpolation_payload = summary.get("interpolationPolicy") if isinstance(summary.get("interpolationPolicy"), dict) else {}
    if interpolation_payload:
        label_policy = str(interpolation_payload.get("label") or interpolation_payload.get("annotation") or "").lower()
        intensity_policy = str(interpolation_payload.get("intensity") or interpolation_payload.get("mri") or "").lower()
        interp_ok = ("nearest" in label_policy) and ("linear" in intensity_policy or "bspline" in intensity_policy)
        interpolation_status = "recorded" if interp_ok else "warn"
        interpolation_message = json.dumps(interpolation_payload, ensure_ascii=False)
    else:
        interpolation_status = "assumed"
        interpolation_message = "label/mask=NearestNeighbor; intensity=Linear"

    checks = {
        "fixedMovingDirection": _qc_check(
            direction_status,
            direction_message,
            evidence={"fixed": ccf_template or None, "moving": ambmc_image or None},
            required="fixed=CCF, moving=AMBMC/MRI",
        ),
        "spacing": _qc_check(
            spacing_status,
            spacing_message,
            evidence=spacing_evidence,
            required="25um == 0.025mm in atlas voxel space",
        ),
        "interpolationPolicy": _qc_check(
            interpolation_status,
            interpolation_message,
            evidence=interpolation_payload or None,
            required="label/mask=NearestNeighbor; intensity=Linear",
        ),
    }

    transform_stages = {
        "rigid": bool(transform.get("usedRigid")),
        "affine": bool(transform.get("usedAffine")),
        "synDeformable": bool(transform.get("usedDeformable")),
        "skipDeformable": bool(transform.get("skipDeformable")),
        "type": transform.get("type") or "unknown",
        "metrics": {
            "rigidMetric": _qc_metric(transform, "rigidMetric"),
            "affineMetric": _qc_metric(transform, "affineMetric"),
            "deformableMetric": _qc_metric(transform, "deformableMetric"),
        },
    }

    layers = overlay_payload.get("layers") if isinstance(overlay_payload.get("layers"), dict) else {}
    overlay_layers = {
        "orthogonalOverlay": {
            "configured": bool(layers.get("ccfTemplate", {}).get("configured") and layers.get("mriTemplate", {}).get("configured")),
            "source": "ccf_template + mri_template",
        },
        "checkerboardOverlay": deepcopy(layers.get("checkerboard") or {}),
        "ccfBoundaryOverlay": deepcopy(layers.get("annotationBoundary") or {}),
        "mriBoundaryOverlay": deepcopy(layers.get("mriTemplate") or {}),
        "skullBoundaryOverlay": deepcopy(layers.get("skullBoundary") or {}),
        "outerBoundaryOverlay": deepcopy(layers.get("outerTissueBoundary") or {}),
        "jacobianHeatmap": deepcopy(layers.get("jacobianHeatmap") or {}),
    }
    missing_overlays = [key for key, row in overlay_layers.items() if not (isinstance(row, dict) and row.get("configured"))]

    thresholds = summary_payload.get("thresholds") if isinstance(summary_payload.get("thresholds"), dict) else {}
    dice_threshold = thresholds.get("diceMin", thresholds.get("minDice"))
    hd95_threshold = thresholds.get("hd95MaxMm", thresholds.get("maxHd95Mm"))
    tre_threshold = thresholds.get("treMaxMm", thresholds.get("maxTreMm"))
    jac_threshold = thresholds.get("jacobianAnomalyMax", thresholds.get("maxJacobianAnomalyRatio"))
    dice_threshold = _metric_float(dice_threshold)
    hd95_threshold = _metric_float(hd95_threshold)
    tre_threshold = _metric_float(tre_threshold)
    jac_threshold = _metric_float(jac_threshold)
    dice_value = _metric_float(metrics.get("dice"))
    hd95_value = _metric_float(metrics.get("hd95Mm"))
    tre_mean_value = _metric_float(metrics.get("treMeanMm"))
    tre_median_value = _metric_float(metrics.get("treMedianMm"))
    tre_max_value = _metric_float(metrics.get("treMaxMm"))
    jac_value = _metric_float(metrics.get("jacobianAnomalyRatio"))

    export_rows = [
        {"section": "metric", "name": "Dice", "value": metrics.get("dice"), "unit": "", "threshold": dice_threshold, "passed": (dice_value is None or dice_threshold is None or dice_value >= dice_threshold)},
        {"section": "metric", "name": "HD95", "value": metrics.get("hd95Mm"), "unit": "mm", "threshold": hd95_threshold, "passed": (hd95_value is None or hd95_threshold is None or hd95_value <= hd95_threshold)},
        {"section": "metric", "name": "TRE mean", "value": metrics.get("treMeanMm"), "unit": "mm", "threshold": tre_threshold, "passed": (tre_mean_value is None or tre_threshold is None or tre_mean_value <= tre_threshold)},
        {"section": "metric", "name": "TRE median", "value": metrics.get("treMedianMm"), "unit": "mm", "threshold": tre_threshold, "passed": (tre_median_value is None or tre_threshold is None or tre_median_value <= tre_threshold)},
        {"section": "metric", "name": "TRE max", "value": metrics.get("treMaxMm"), "unit": "mm", "threshold": tre_threshold, "passed": (tre_max_value is None or tre_threshold is None or tre_max_value <= tre_threshold)},
        {"section": "metric", "name": "Jacobian anomaly ratio", "value": metrics.get("jacobianAnomalyRatio"), "unit": "", "threshold": jac_threshold, "passed": (jac_value is None or jac_threshold is None or jac_value <= jac_threshold)},
        {"section": "check", "name": "fixed/moving", "value": checks["fixedMovingDirection"]["status"], "unit": "", "threshold": checks["fixedMovingDirection"]["required"], "passed": checks["fixedMovingDirection"]["passed"]},
        {"section": "check", "name": "spacing", "value": checks["spacing"]["status"], "unit": "", "threshold": checks["spacing"]["required"], "passed": checks["spacing"]["passed"]},
        {"section": "check", "name": "interpolation", "value": checks["interpolationPolicy"]["status"], "unit": "", "threshold": checks["interpolationPolicy"]["required"], "passed": checks["interpolationPolicy"]["passed"]},
    ]
    row_failures = []
    for row in export_rows:
        if row.get("passed") is not False:
            continue
        name = str(row.get("name") or "QC row")
        value = row.get("value")
        threshold = row.get("threshold")
        unit = row.get("unit") or ""
        if row.get("section") == "metric":
            row_failures.append(f"{name} {value}{unit} violates threshold {threshold}")
        else:
            row_failures.append(f"{name} check status {value} does not satisfy {threshold}")
    combined_reasons = []
    for reason in list(quality_reasons) + row_failures:
        if reason and reason not in combined_reasons:
            combined_reasons.append(reason)
    upstream_passed = summary_payload.get("technicalChecksPassed")
    technical_passed = (upstream_passed is not False) and len(combined_reasons) == 0
    legacy_metrics = deepcopy(metrics)
    measurement_metrics = summary_payload.get("measurementMetrics") or {}
    assessments = summary_payload.get("metricAssessment") or {}
    evidence_complete = summary_payload.get("measurementEvidenceComplete") is True
    for key in ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio"):
        metrics[key] = measurement_metrics.get(key)
    metrics["treMeanMm"] = measurement_metrics.get("treMm")
    # A single stored TRE cannot also establish its median, maximum or axis errors.
    metrics["treMedianMm"] = None
    metrics["treMaxMm"] = None
    metrics["treAxisMm"] = {"AP": None, "ML": None, "DV": None}
    metrics["treCi95Mm"] = (summary_payload.get("measurementCi95") or {}).get("treMm")
    if not (assessments.get("jacobianAnomalyRatio") or {}).get("usableAsMeasurement"):
        for key in ("jacobianMean", "jacobianStd", "jacobianMin", "jacobianMax"):
            metrics[key] = None
    row_keys = {"Dice": "dice", "HD95": "hd95Mm", "TRE mean": "treMeanMm",
                "TRE median": "treMedianMm", "TRE max": "treMaxMm", "Jacobian anomaly ratio": "jacobianAnomalyRatio"}
    for row in export_rows:
        key = row_keys.get(row["name"])
        if key:
            row["value"] = metrics.get(key)
            assessment_key = "treMm" if key.startswith("tre") else key
            row["evidenceStatus"] = (assessments.get(assessment_key) or {}).get("status", "unavailable")
            if row["value"] is None:
                row["passed"] = None
        else:
            row["evidenceStatus"] = row.get("value")
    quality_passed = technical_passed and evidence_complete
    quality_gate = {
        "passed": bool(quality_passed),
        "status": "PASS" if quality_passed else "INCOMPLETE_MEASUREMENT_EVIDENCE" if technical_passed else "FAIL",
        "technicalChecksPassed": technical_passed,
        "scope": "profile_measurements_not_subject_surgical_validation",
        "blockingReasons": combined_reasons,
        "thresholds": thresholds,
        "failureCard": _quality_gate_failure_card(),
    }

    return {
        "ok": True,
        "activeAtlasMode": ACTIVE_ATLAS_MODE,
        "profileId": summary_payload.get("profileId") or overlay_payload.get("profileId"),
        "manifestPath": summary_payload.get("manifestPath") or overlay_payload.get("manifestPath"),
        "statsPath": summary_payload.get("statsPath") or overlay_payload.get("statsPath"),
        "registrationSummaryPath": summary_payload.get("path"),
        "pathConsistency": deepcopy(summary_payload.get("pathConsistency") or overlay_payload.get("pathConsistency") or {}),
        "metrics": metrics,
        "legacyDiagnostics": legacy_metrics,
        "metricAssessment": assessments,
        "measurementMetrics": measurement_metrics,
        "measurementEvidenceComplete": evidence_complete,
        "anatomicalTreAvailable": anatomical_tre_available,
        "controlPointResidual": control_point_residual,
        "metricEvidence": deepcopy(summary_payload.get("metricEvidence") or {}),
        "jacobianSummary": jacobian,
        "transform": transform_stages,
        "checks": checks,
        "overlay": {
            "layers": overlay_layers,
            "missingLayers": missing_overlays,
            "opacity": overlay_payload.get("opacity") or {"min": 0.0, "max": 1.0, "default": 0.55},
        },
        "qualityGate": quality_gate,
        "summary": summary,
        "exportRows": export_rows,
        "requestId": _request_id(),
        "engineVersion": ENGINE_VERSION,
    }


@app.route("/api/mri/registration/qc", methods=["GET"])
def api_mri_registration_qc():
    return jsonify(_build_mri_registration_qc_payload())


@app.route("/api/mri/registration/qc/export", methods=["GET"])
def api_mri_registration_qc_export():
    payload = _build_mri_registration_qc_payload()
    fmt = str(request.args.get("format") or "json").strip().lower()
    if fmt not in {"json", "csv"}:
        return _json_error(
            "Unsupported registration QC export format",
            status=400,
            code="invalid_query_param",
            details=["format must be json or csv."],
        )
    if fmt == "csv":
        rows = payload.get("exportRows") if isinstance(payload.get("exportRows"), list) else []
        headers = ["section", "name", "value", "unit", "threshold", "passed", "evidenceStatus"]
        def _csv_cell(value):
            text = "" if value is None else str(value)
            text = text.replace('"', '""')
            return f'"{text}"' if any(ch in text for ch in [",", '"', "\n", "\r"]) else text
        lines = [",".join(headers)]
        for row in rows:
            lines.append(",".join(_csv_cell(row.get(key)) for key in headers))
        return Response(
            "\n".join(lines) + "\n",
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=registration_qc.csv"},
        )
    return jsonify(payload)


def _load_qc_volume(path_text, preferred_keys=None):
    if not path_text:
        return None
    try:
        p = Path(str(path_text)).expanduser()
        if not p.exists():
            return None
        if p.suffix.lower() == ".npy":
            arr = np.load(p, mmap_mode="r", allow_pickle=False)
            return arr if getattr(arr, "ndim", 0) == 3 else None
        if p.suffix.lower() == ".npz":
            pack = np.load(p, allow_pickle=False)
            keys = list(preferred_keys or []) + list(pack.files)
            seen = set()
            for key in keys:
                if key in seen or key not in pack.files:
                    continue
                seen.add(key)
                arr = pack[key]
                if getattr(arr, "ndim", 0) == 3:
                    return arr
    except Exception:
        return None
    return None


def _slice_2d(volume, axis, index):
    if volume is None or getattr(volume, "ndim", 0) != 3:
        return None
    axis_i = int(axis)
    axis_i = 0 if axis_i < 0 else 2 if axis_i > 2 else axis_i
    size = int(volume.shape[axis_i])
    idx = int(index) if index is not None else size // 2
    idx = max(0, min(size - 1, idx))
    if axis_i == 0:
        out = np.asarray(volume[idx, :, :])
    elif axis_i == 1:
        out = np.asarray(volume[:, idx, :])
    else:
        out = np.asarray(volume[:, :, idx])
    return np.flipud(np.asarray(out))


def _normalize_u8(slice_arr):
    arr = np.asarray(slice_arr)
    if arr.size == 0:
        return np.zeros((1, 1), dtype=np.uint8)
    finite = np.isfinite(arr)
    if not np.any(finite):
        return np.zeros(arr.shape, dtype=np.uint8)
    vals = arr[finite].astype(np.float32)
    lo, hi = np.percentile(vals, [2, 98])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        hi = float(np.max(vals))
        lo = float(np.min(vals))
    if hi <= lo:
        return np.zeros(arr.shape, dtype=np.uint8)
    norm = np.clip((arr.astype(np.float32) - lo) / (hi - lo), 0, 1)
    return np.asarray(norm * 255, dtype=np.uint8)


def _label_color_slice(label_slice):
    labels = np.asarray(label_slice, dtype=np.uint32)
    r = ((labels * 37 + 23) % 255).astype(np.uint8)
    g = ((labels * 67 + 71) % 255).astype(np.uint8)
    b = ((labels * 97 + 113) % 255).astype(np.uint8)
    bg = labels == 0
    r[bg] = 5
    g[bg] = 14
    b[bg] = 28
    return np.stack([r, g, b], axis=-1)


def _boundary_mask(slice_arr):
    arr = np.asarray(slice_arr)
    if arr.ndim != 2:
        return np.zeros((1, 1), dtype=bool)
    mask = np.zeros(arr.shape, dtype=bool)
    mask[:-1, :] |= arr[:-1, :] != arr[1:, :]
    mask[1:, :] |= arr[:-1, :] != arr[1:, :]
    mask[:, :-1] |= arr[:, :-1] != arr[:, 1:]
    mask[:, 1:] |= arr[:, :-1] != arr[:, 1:]
    return mask & (arr != 0)


_atlas_section_source = None
_atlas_section_lock = threading.Lock()


def _get_atlas_section_source():
    global _atlas_section_source
    if ACTIVE_ATLAS_MODE != "mri_ccf_hybrid":
        raise SectionError("MRI section requires the active MRI/CCF profile")
    runtime = _mri_runtime_get()
    path = MRI_CCF_MANIFEST_PATH or runtime.get("manifestPath")
    if not path:
        path = _mri_payload().get("manifestPath")
    if not path:
        raise SectionError("Bound section manifest unavailable")
    expected = (app.config.get("PAPER_RUNTIME_IDENTITY") or {}).get("artifactManifestSha256")
    with _atlas_section_lock:
        if _atlas_section_source is None or _atlas_section_source.manifest_path != Path(path).resolve():
            _atlas_section_source = AtlasSectionSource(path, expected_manifest_hash=expected)
        if expected and _atlas_section_source.manifest_hash != expected:
            raise SectionError("Bound section manifest hash mismatch")
        _atlas_section_source.verify_unchanged()
        return _atlas_section_source


@app.route("/api/mri/section", methods=["GET"])
def api_mri_section():
    axis = str(request.args.get("axis") or "y").lower()
    mode = str(request.args.get("mode") or "mri_ccf_overlay").lower()
    try:
        coordinate = float(request.args.get("coordinate", "nan"))
    except ValueError:
        coordinate = float("nan")
    if axis not in SECTION_AXES or mode not in {"mri_only", "mri_ccf_overlay", "ccf_only"} or not np.isfinite(coordinate):
        return _json_error("Invalid section axis, coordinate or mode", status=400, code="invalid_query_param")
    try:
        result = _get_atlas_section_source().section(axis, coordinate, mode)
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response
    except SectionError as exc:
        return _json_error(str(exc), status=409, code="section_source_unavailable")


@app.route("/api/mri/registration/qc/overlay_image", methods=["GET"])
def api_mri_registration_qc_overlay_image():
    mode = str(request.args.get("mode") or "boundary").strip().lower()
    if mode not in {"boundary", "checkerboard", "jacobian"}:
        return _json_error(
            "Unsupported registration QC overlay mode",
            status=400,
            code="invalid_query_param",
            details=["mode must be boundary, checkerboard, or jacobian."],
        )
    axis_name = str(request.args.get("axis") or "coronal").strip().lower()
    if axis_name not in SECTION_AXES:
        return _json_error(
            "Unsupported registration QC overlay axis",
            status=400,
            code="invalid_query_param",
            details=["axis must be coronal, sagittal, horizontal, ap, ml, or dv."],
        )
    index_arg = request.args.get("index")
    try:
        index = _parse_int_query_arg("index") if index_arg is not None else None
    except ContractError as exc:
        return _json_contract_error(exc)

    try:
        rgb, metadata = _get_atlas_section_source().qc_image(axis_name, index, mode)
        from PIL import Image
        img = Image.fromarray(rgb, mode="RGB")
        max_side = 1100
        if max(img.size) > max_side:
            scale = max_side / float(max(img.size))
            img = img.resize((max(1, int(img.size[0] * scale)), max(1, int(img.size[1] * scale))))
        bio = BytesIO()
        img.save(bio, format="PNG")
        bio.seek(0)
        response = send_file(bio, mimetype="image/png", download_name=f"registration_qc_{mode}_{axis_name}.png")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Section-Comparison"] = metadata["comparison"]
        response.headers["X-Section-Manifest-SHA256"] = metadata["manifestSha256"]
        response.headers["X-Section-Index"] = str(metadata["index"])
        return response
    except SectionError as exc:
        return _json_error(str(exc), status=409, code="section_source_unavailable")
    except Exception as exc:
        return _json_internal_error(
            exc,
            context="MRI registration QC overlay render",
            message="Failed to render QC overlay",
            code="qc_overlay_render_failed",
        )


@app.route("/api/mri/activate", methods=["POST"])
def api_mri_activate():
    body = _request_json_object(required=False)
    bundle = None
    task_id = str(body.get("taskId") or "").strip()
    if task_id:
        with mri_reconstruct_lock:
            task = deepcopy(mri_reconstruct_tasks.get(task_id))
        if not task:
            return _json_error("MRI reconstruct task not found", status=404, code="mri_task_not_found")
        if str(task.get("state")) != "completed":
            return _json_error("MRI reconstruct task is not completed yet", status=409, code="mri_task_not_ready")
        result = task.get("result") if isinstance(task.get("result"), dict) else {}
        bundle = result.get("bundle") if isinstance(result.get("bundle"), dict) else None

    if bundle is None:
        bundle = {
            "manifestPath": body.get("manifestPath"),
            "activeProfilePath": body.get("activeProfilePath"),
            "annotationPath": body.get("annotationPath"),
            "regionMapPath": body.get("regionMapPath"),
            "regionMapV2Path": body.get("regionMapV2Path"),
            "statsPath": body.get("statsPath"),
            "registrationSummaryPath": body.get("registrationSummaryPath"),
            "jacobianPath": body.get("jacobianPath"),
            "skullMaskPath": body.get("skullMaskPath"),
            "outerTissueMaskPath": body.get("outerTissueMaskPath"),
            "layeredSdfPath": body.get("layeredSdfPath"),
            "datasetProfile": body.get("datasetProfile") or "custom",
        }

    try:
        normalized, summary = _validate_mri_artifact_bundle(bundle)
    except Exception as exc:
        log.warning(
            "Invalid MRI hybrid artifact bundle requestId=%s: %s",
            _request_id(),
            exc,
        )
        return _json_error(
            "Invalid MRI hybrid artifact bundle",
            status=400,
            code="mri_bundle_invalid",
            details=["Inspect backend logs using the response requestId."],
        )
    _mri_runtime_update(normalized)
    # Refresh quality state immediately from the activated MRI bundle even if
    # atlas is already loaded (no background reload), so gate uses real stats.
    try:
        mri_stats = _load_mri_hybrid_stats()
        mri_reg_summary = _load_mri_hybrid_registration_summary()
        jac_path = normalized.get("jacobianPath")
        annotation_for_quality = None
        with atlas_lock:
            if (
                atlas_loaded
                and ACTIVE_ATLAS_MODE == "mri_ccf_hybrid"
                and atlas_annotation is not None
                and tuple(getattr(atlas_annotation, "shape", ())) == tuple(ATLAS_SHAPE)
            ):
                annotation_for_quality = atlas_annotation
        _update_reconstruction_quality_state(
            annotation_for_quality,
            stats_override=mri_stats,
            registration_summary_override=mri_reg_summary,
            jacobian_path_override=jac_path,
            profile_source="mri_ccf_stats_dynamic" if mri_stats else "mri_ccf_default",
        )
    except Exception as exc:
        log.warning(f"Failed to refresh MRI reconstruction quality state after activate: {exc}")
    switch_mode = _coerce_bool(body.get("switchMode", True), default=True)
    if switch_mode:
        _apply_active_atlas_mode("mri_ccf_hybrid")
    _ensure_atlas_loader(force_retry=True)
    return jsonify(
        {
            "ok": True,
            "activeAtlasMode": ACTIVE_ATLAS_MODE,
            "bundle": normalized,
            "summary": summary,
            "requestId": _request_id(),
            "engineVersion": ENGINE_VERSION,
        }
    )


@app.route("/api/mri/appearance", methods=["GET", "POST"])
def api_mri_appearance():
    if request.method == "GET":
        payload = _mri_payload()
        with risk_model_lock:
            risk_snapshot = deepcopy(risk_model_summary)
        return jsonify(
            {
                "ok": True,
                "useMriAppearance": bool(payload.get("useMriAppearance", MRI_CCF_USE_APPEARANCE)),
                "appearanceLayers": {
                    "showOuterBoundary": bool(payload.get("showOuterBoundary", MRI_CCF_SHOW_OUTER_BOUNDARY)),
                    "showSkull": bool(payload.get("showSkull", MRI_CCF_SHOW_SKULL)),
                    "showBrainSurface": bool(payload.get("showBrainSurface", MRI_CCF_SHOW_BRAIN_SURFACE)),
                    "showCcfLabels": bool(payload.get("showCcfLabels", MRI_CCF_SHOW_CCF_LABELS)),
                    "overlayMode": payload.get("overlayMode") or MRI_CCF_OVERLAY_MODE,
                },
                "mriCcfHybrid": payload,
                "mriLayerStatus": _build_mri_layer_status(
                    mri_payload=payload,
                    risk_snapshot=risk_snapshot,
                    active_mode=ACTIVE_ATLAS_MODE,
                ),
                "activeAtlasMode": ACTIVE_ATLAS_MODE,
                "requestId": _request_id(),
                "engineVersion": ENGINE_VERSION,
            }
        )

    body = _request_json_object(required=False)
    if "useMriAppearance" not in body:
        return _json_error(
            "Missing required field: useMriAppearance",
            status=400,
            code="mri_appearance_missing",
        )

    update_payload = {"useMriAppearance": body.get("useMriAppearance")}
    for optional_key in (
        "showOuterBoundary",
        "showSkull",
        "showBrainSurface",
        "showCcfLabels",
        "overlayMode",
    ):
        if optional_key in body:
            update_payload[optional_key] = body.get(optional_key)
    runtime = _mri_runtime_update(update_payload)
    payload = _mri_payload()
    with risk_model_lock:
        risk_snapshot = deepcopy(risk_model_summary)
    return jsonify(
        {
            "ok": True,
            "activeAtlasMode": ACTIVE_ATLAS_MODE,
            "useMriAppearance": bool(payload.get("useMriAppearance", MRI_CCF_USE_APPEARANCE)),
            "appearanceLayers": {
                "showOuterBoundary": bool(payload.get("showOuterBoundary", MRI_CCF_SHOW_OUTER_BOUNDARY)),
                "showSkull": bool(payload.get("showSkull", MRI_CCF_SHOW_SKULL)),
                "showBrainSurface": bool(payload.get("showBrainSurface", MRI_CCF_SHOW_BRAIN_SURFACE)),
                "showCcfLabels": bool(payload.get("showCcfLabels", MRI_CCF_SHOW_CCF_LABELS)),
                "overlayMode": payload.get("overlayMode") or MRI_CCF_OVERLAY_MODE,
            },
            "mriRuntime": runtime,
            "mriCcfHybrid": payload,
            "mriLayerStatus": _build_mri_layer_status(
                mri_payload=payload,
                risk_snapshot=risk_snapshot,
                active_mode=ACTIVE_ATLAS_MODE,
            ),
            "requestId": _request_id(),
            "engineVersion": ENGINE_VERSION,
        }
    )


@app.route("/api/status", methods=["GET"])
def api_status():
    region_scope = str(request.args.get("regionScope", "all")).strip().lower() or "all"
    include_regions = str(request.args.get("includeRegions", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    requested_mode = _normalize_atlas_mode(request.args.get("atlasMode", ACTIVE_ATLAS_MODE))
    # Status is observational: a second browser must not reset the active atlas.
    retry_requested = str(request.args.get("retry", "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    _ensure_atlas_loader(force_retry=retry_requested)
    with atlas_lock:
        atlas_load_snapshot = deepcopy(atlas_load_state)
        atlas_loaded_snapshot = bool(atlas_loaded)
        atlas_error_snapshot = atlas_error
        planner_region_count = len(PLANNER_REGIONS)
        indexed_region_count = len(acronym_record_map)
        structure_snapshot = dict(structure_map)
    present_region_count = int(
        sum(
            1
            for _, info in structure_snapshot.items()
            if isinstance(info, dict) and int(info.get("voxel_count", 0) or 0) > 0
        )
    )
    catalog_region_count = int(
        max(
            planner_region_count,
            indexed_region_count,
            len(structure_snapshot),
        )
    )
    if include_regions:
        regions_payload = _build_region_catalog(scope=region_scope)
        region_count = int(len(regions_payload))
        available_region_count = _compute_available_region_count(regions_payload)
        if region_scope in ("all", "full", "catalog"):
            catalog_region_count = int(max(catalog_region_count, region_count))
            present_region_count = int(
                sum(
                    1
                    for _, info in regions_payload.items()
                    if isinstance(info, dict) and int(info.get("voxel_count", 0) or 0) > 0
                )
            )
    else:
        regions_payload = {}
        if region_scope in ("planner", "curated", "selected"):
            region_count = int(planner_region_count)
            available_region_count = int(planner_region_count)
        else:
            available_region_count = int(_compute_available_region_count(None))
            if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid":
                region_count = int(max(indexed_region_count, planner_region_count, available_region_count))
            else:
                region_count = int(available_region_count)
        if atlas_loaded_snapshot and region_scope in ("all", "full", "catalog"):
            # Keep lightweight status counts aligned with /api/regions without returning
            # the full catalog payload when includeRegions=0.
            count_payload = _build_region_catalog(scope=region_scope)
            if isinstance(count_payload, dict) and count_payload:
                region_count = int(len(count_payload))
                available_region_count = int(_compute_available_region_count(count_payload))
                catalog_region_count = int(max(catalog_region_count, region_count))
                present_region_count = int(
                    sum(1 for _, info in count_payload.items() if bool(info.get("presentInVolume", False)))
                )
    full_brain_planner_ready = True
    if ACTIVE_ATLAS_MODE == "allen_sdk":
        full_brain_planner_ready = bool(
            atlas_loaded_snapshot
            and max(region_count, available_region_count, indexed_region_count) >= 1000
        )
    elif ACTIVE_ATLAS_MODE == "mri_ccf_hybrid":
        full_brain_planner_ready = bool(
            atlas_loaded_snapshot
            and planner_region_count >= max(1, present_region_count)
        )
    elif ACTIVE_ATLAS_MODE == "qiu2018":
        full_brain_planner_ready = bool(
            atlas_loaded_snapshot
            and planner_region_count >= max(1, present_region_count)
        )
    region_index_cache = (
        atlas_signature.get("regionIndexCache")
        if isinstance(atlas_signature.get("regionIndexCache"), dict)
        else {}
    )
    region_index_status = {
        "mode": atlas_signature.get("regionIndexMode") or ("startup_full" if STARTUP_FULL_REGION_PLANNING else "planner_only"),
        "startupFullRegionPlanning": bool(STARTUP_FULL_REGION_PLANNING),
        "dynamicRegionProvisioning": bool(DYNAMIC_REGION_PROVISION_ENABLED),
        "cacheEnabled": bool(REGION_INDEX_CACHE_ENABLED),
        "cacheSource": str(region_index_cache.get("source") or "unknown"),
        "cacheHit": str(region_index_cache.get("source") or "").lower() == "cache_hit",
        "cachePath": region_index_cache.get("path"),
        "plannerSeedCount": int(region_index_cache.get("plannerSeedCount") or planner_region_count or 0),
        "plannerRegionCount": int(planner_region_count),
        "indexedRegionCount": int(indexed_region_count),
        "catalogRegionCount": int(catalog_region_count),
        "presentRegionCount": int(present_region_count),
        "availableRegionCount": int(available_region_count),
        "fullBrainPlannerReady": bool(full_brain_planner_ready),
        "catalogPlannerAligned": bool(
            atlas_loaded_snapshot
            and (
                planner_region_count >= max(1, catalog_region_count)
                if ACTIVE_ATLAS_MODE == "allen_sdk"
                else planner_region_count >= max(1, present_region_count)
            )
        ),
    }
    loader_alive = _atlas_loader_alive()
    qiu_payload = _qiu_payload()
    mri_payload = _mri_payload()
    active_artifact_ready = True
    active_missing_artifacts = []
    if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid":
        active_artifact_ready = bool(mri_payload.get("artifactReady", False))
        active_missing_artifacts = list(mri_payload.get("missingArtifacts") or [])
    elif ACTIVE_ATLAS_MODE == "qiu2018":
        active_artifact_ready = bool(qiu_payload.get("annotationConfigured"))
        active_missing_artifacts = [] if active_artifact_ready else ["annotation"]
    with risk_model_lock:
        risk_snapshot = deepcopy(risk_model_summary)
    mri_layer_status = _build_mri_layer_status(
        mri_payload=mri_payload,
        risk_snapshot=risk_snapshot,
        active_mode=ACTIVE_ATLAS_MODE,
    )
    active_profile_contract = _build_active_profile_contract(
        qiu_payload=qiu_payload,
        mri_payload=mri_payload,
        risk_snapshot=risk_snapshot,
    )
    mode_contracts = {
        "allen_sdk": {
            "mode": "allen_sdk",
            "profileId": "allen_ccfv3",
            "artifactReady": True,
            "manifestPath": None,
            "annotationPath": "AllenSDK ReferenceSpaceCache annotation",
            "regionMapPath": "AllenSDK structure tree",
            "statsPath": None,
            "registrationSummaryPath": None,
            "jacobianPath": None,
            "skullMaskPath": None,
            "outerTissueMaskPath": None,
            "layeredSdfPath": None,
            "missingArtifacts": [],
            "contractViolations": [],
            "pathConsistency": {"passed": True, "mismatches": []},
        },
        "qiu2018": {
            "mode": "qiu2018",
            "profileId": "qiu2018",
            "artifactReady": bool(qiu_payload.get("annotationConfigured")),
            "manifestPath": None,
            "annotationPath": qiu_payload.get("annotationPath"),
            "regionMapPath": qiu_payload.get("regionMapPath"),
            "statsPath": qiu_payload.get("statsPath"),
            "registrationSummaryPath": None,
            "jacobianPath": qiu_payload.get("jacobianPath"),
            "skullMaskPath": None,
            "outerTissueMaskPath": None,
            "layeredSdfPath": None,
            "missingArtifacts": [] if qiu_payload.get("annotationConfigured") else ["annotation"],
            "contractViolations": [],
            "pathConsistency": {"passed": True, "mismatches": []},
        },
        "mri_ccf_hybrid": {
            "mode": "mri_ccf_hybrid",
            "profileId": mri_payload.get("profileId") or mri_payload.get("datasetProfile"),
            "artifactReady": bool(mri_payload.get("artifactReady", False)),
            "manifestPath": mri_payload.get("manifestPath"),
            "activeProfilePath": mri_payload.get("activeProfilePath"),
            "annotationPath": mri_payload.get("annotationPath"),
            "regionMapPath": mri_payload.get("regionMapV2Path") or mri_payload.get("regionMapPath"),
            "statsPath": mri_payload.get("statsPath"),
            "registrationSummaryPath": mri_payload.get("registrationSummaryPath"),
            "jacobianPath": mri_payload.get("jacobianPath"),
            "skullMaskPath": mri_payload.get("skullMaskPath"),
            "outerTissueMaskPath": mri_payload.get("outerTissueMaskPath"),
            "layeredSdfPath": mri_payload.get("layeredSdfPath"),
            "mriSurfaceMeshManifestPath": mri_payload.get("mriSurfaceMeshManifestPath"),
            "missingArtifacts": list(mri_payload.get("missingArtifacts") or []),
            "contractViolations": deepcopy(mri_payload.get("contractViolations") or []),
            "pathConsistency": deepcopy(mri_payload.get("pathConsistency") or {}),
        },
    }
    load_progress_raw = float(atlas_load_snapshot.get("progress") or 0.0)
    if load_progress_raw <= 1.0:
        atlas_load_pct = max(0.0, min(100.0, load_progress_raw * 100.0))
    else:
        atlas_load_pct = max(0.0, min(100.0, load_progress_raw))
    public_atlas_error = _public_atlas_error(atlas_error_snapshot)
    public_atlas_load_snapshot = _public_atlas_load_snapshot(atlas_load_snapshot, atlas_error_snapshot)
    native_engine_enabled = _native_engine_service_enabled()
    native_engine_url = _native_engine_base_url()
    native_engine_health = {}
    native_engine_error = None
    if native_engine_enabled:
        try:
            health_resp = http_requests.get(f"{native_engine_url}/healthz", timeout=2)
            health_resp.raise_for_status()
            native_engine_health = health_resp.json() if hasattr(health_resp, "json") else {}
        except Exception as exc:
            native_engine_error = str(exc)
            native_engine_health = {}
    native_engine_ready = bool(
        native_engine_enabled
        and native_engine_health.get("ok")
        and native_engine_health.get("atlasLoaded")
        and native_engine_health.get("regionIndexLoaded")
    )
    effective_atlas_loaded_snapshot = bool(atlas_loaded_snapshot or native_engine_ready)
    effective_public_atlas_error = None if native_engine_ready else public_atlas_error
    effective_full_brain_planner_ready = bool(full_brain_planner_ready or native_engine_ready)
    if native_engine_ready:
        region_index_status["fullBrainPlannerReady"] = True
        region_index_status["nativeEngineBacked"] = True
        region_index_status["nativeEngineRegionIndexLoaded"] = True
    degradation_notices = _build_degradation_notices(
        constraint_modes=DEFAULT_CONSTRAINT_MODES,
        atlas_loaded_snapshot=effective_atlas_loaded_snapshot,
        atlas_load_snapshot=public_atlas_load_snapshot,
        atlas_error_snapshot=effective_public_atlas_error,
        qiu_payload=qiu_payload,
        mri_payload=mri_payload,
    )
    mri_repair_notice = _build_mri_task_notice()
    if isinstance(mri_repair_notice, dict):
        merged = list(degradation_notices or [])
        merged.append(mri_repair_notice)
        deduped = []
        seen = set()
        for notice in merged:
            if not isinstance(notice, dict):
                continue
            key = str(
                notice.get("id")
                or notice.get("code")
                or notice.get("messageEn")
                or notice.get("messageCn")
                or notice.get("titleEn")
                or notice.get("titleCn")
                or uuid.uuid4().hex
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(notice)
        degradation_notices = deduped
    degradation_reasons = [
        {
            "code": n.get("code") or n.get("id"),
            "level": n.get("level"),
            "reasonEn": n.get("reasonEn") or n.get("messageEn"),
            "reasonCn": n.get("reasonCn") or n.get("messageCn"),
            "impactEn": n.get("impactEn"),
            "impactCn": n.get("impactCn"),
            "recoveryEn": n.get("recoveryEn") or n.get("actionEn"),
            "recoveryCn": n.get("recoveryCn") or n.get("actionCn"),
        }
        for n in (degradation_notices or [])
        if isinstance(n, dict)
    ]
    workflow_state = _build_unified_workflow_state(
        atlas_loaded_snapshot=effective_atlas_loaded_snapshot,
        atlas_load_snapshot=public_atlas_load_snapshot,
        atlas_error_snapshot=effective_public_atlas_error,
    )
    latest_mri_task = _latest_mri_task_snapshot()
    planning_last = {}
    with workflow_state_lock:
        planning_last = deepcopy(planning_runtime_state.get("lastResultSummary") or {})
    calib_profile_snapshot = _load_planner_calibration_profile(None)
    active_risk_threshold_template = _normalize_risk_threshold_template(
        planning_last.get("riskThresholdTemplate")
    )
    return jsonify({
        "ok":          effective_atlas_loaded_snapshot,
        "statusContractVersion": "probeplanner.status.v2",
        "engineMode":  ENGINE_MODE,
        "engineVersion": ENGINE_VERSION,
        "engineServiceEnabled": bool(native_engine_enabled),
        "engineServiceUrl": native_engine_url if native_engine_enabled else None,
        "nativeEngineReady": bool(native_engine_ready),
        "nativeEngineHealth": deepcopy(native_engine_health),
        "nativeEngineError": native_engine_error,
        "activeAtlasMode": ACTIVE_ATLAS_MODE,
        "effectiveAtlasMode": "native_engine" if native_engine_ready else ACTIVE_ATLAS_MODE,
        "requestedAtlasMode": requested_mode,
        "atlasModeMatchesRequest": requested_mode == ACTIVE_ATLAS_MODE,
        "supportedAtlasModes": list(ATLAS_MODES),
        "activeProfile": active_profile_contract,
        "modeContracts": mode_contracts,
        "qiu2018": qiu_payload,
        "mriCcfHybrid": mri_payload,
        "mriProfile": {
            "active": bool(ACTIVE_ATLAS_MODE == "mri_ccf_hybrid"),
            "profileId": mri_payload.get("profileId") or mri_payload.get("datasetProfile"),
            "artifactReady": bool(mri_payload.get("artifactReady", False)),
            "missingArtifacts": list(mri_payload.get("missingArtifacts") or []),
            "manifestPath": mri_payload.get("manifestPath"),
            "activeProfilePath": mri_payload.get("activeProfilePath"),
            "statsPath": mri_payload.get("statsPath"),
            "registrationSummaryPath": mri_payload.get("registrationSummaryPath"),
            "annotationPath": mri_payload.get("annotationPath"),
            "skullMaskPath": mri_payload.get("skullMaskPath"),
            "outerTissueMaskPath": mri_payload.get("outerTissueMaskPath"),
            "layeredSdfPath": mri_payload.get("layeredSdfPath"),
            "jacobianPath": mri_payload.get("jacobianPath"),
            "pathConsistency": deepcopy(mri_payload.get("pathConsistency") or {}),
        },
        "artifactContract": {
            "version": "probeplanner.artifact_contract.v1",
            "activeAtlasMode": ACTIVE_ATLAS_MODE,
            "active": bool(ACTIVE_ATLAS_MODE in ("mri_ccf_hybrid", "qiu2018")),
            "mode": str(mri_payload.get("artifactContractMode") or "none") if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid" else "none",
            "artifactReady": bool(active_artifact_ready),
            "missingArtifacts": list(active_missing_artifacts or []),
            "profileId": (mri_payload.get("profileId") or mri_payload.get("datasetProfile")) if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid" else None,
            "manifestPath": mri_payload.get("manifestPath") if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid" else None,
            "manifestHash": mri_payload.get("manifestHash") if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid" else None,
            "paths": deepcopy(mri_payload.get("artifactPaths") or {}) if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid" else {},
            "pathConsistency": deepcopy(mri_payload.get("pathConsistency") or {}) if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid" else {},
        },
        "mriLayerStatus": mri_layer_status,
        "latestMriReconstructTask": latest_mri_task,
        # Backward-compatible aliases for frontends that read flat status flags.
        "annotationConfigured": bool(qiu_payload["annotationConfigured"] or mri_payload["annotationConfigured"]),
        "regionMapConfigured": bool(qiu_payload["regionMapConfigured"] or mri_payload["regionMapConfigured"]),
        "statsConfigured": bool(qiu_payload["statsConfigured"] or mri_payload["statsConfigured"]),
        "jacobianConfigured": bool(qiu_payload["jacobianConfigured"] or mri_payload["jacobianConfigured"]),
        "versionFrozen": version_freeze_state.get("frozen", False),
        "versionFreezeState": version_freeze_state,
        "requestId": _request_id(),
        "authEnabled": AUTH_ENABLED,
        "atlasLoaded": effective_atlas_loaded_snapshot,
        "backendAtlasLoaded": atlas_loaded_snapshot,
        "atlasError":  effective_public_atlas_error,
        "backendAtlasError": public_atlas_error,
        "atlasLoaderAlive": loader_alive,
        "atlasLoad": public_atlas_load_snapshot,
        "atlasLoadPct": round(float(atlas_load_pct), 3),
        "voxelSize":   VOXEL_SIZE_UM,
        "atlasShape":  list(ATLAS_SHAPE),
        "atlasSignature": atlas_signature,
        "atlasHash": atlas_signature.get("hash"),
        "riskModel": deepcopy(risk_model_summary),
        "performanceProfile": {
            "candidateGeneration": {
                "workersDefault": int(CANDIDATE_GEN_WORKERS_DEFAULT),
                "coarseStepUm": float(CANDIDATE_COARSE_STEP_UM_DEFAULT),
                "refineStepUm": float(CANDIDATE_REFINE_STEP_UM_DEFAULT),
                "refineFraction": float(CANDIDATE_REFINE_FRACTION_DEFAULT),
            },
            "streamingPipeline": {
                "enabledDefault": bool(STREAMING_PIPELINE_ENABLED_DEFAULT),
                "scoreBatchSize": int(STREAMING_SCORE_BATCH_SIZE_DEFAULT),
                "queueMaxSize": int(STREAMING_QUEUE_MAXSIZE_DEFAULT),
                "scoreWorkersMax": int(STREAMING_SCORE_WORKERS_MAX_DEFAULT),
                "allowOversubscribe": bool(STREAMING_ALLOW_OVERSUBSCRIBE_DEFAULT),
            },
            "caches": {
                "trajectoryVoxelMax": int(TRAJECTORY_VOXEL_CACHE_MAX),
                "riskDiagMax": int(RISK_DIAG_CACHE_MAX),
                "robustPerturbMax": int(ROBUST_PERTURB_CACHE_MAX),
                "contactProfileMax": int(CONTACT_PROFILE_CACHE_MAX),
                "trajectoryVoxelSize": int(len(trajectory_voxel_cache)),
                "riskDiagSize": int(len(risk_diag_cache)),
                "robustPerturbSize": int(len(robust_perturb_cache)),
                "contactProfileSize": int(len(contact_profile_cache)),
            },
            "memory": {
                "qiu2018MemmapEnabled": bool(QIU2018_MEMMAP_ENABLED),
                "mriCcfMemmapEnabled": bool(MRI_CCF_MEMMAP_ENABLED),
                "sdfCacheEnabled": bool(SDF_CACHE_ENABLED),
            },
            "solver": {
                "drccAdaptiveBudgetDefault": bool(DRCC_ADAPTIVE_BUDGET_DEFAULT),
                "dynamicExactGreedyFallbackCandidateThreshold": 3200,
            },
        },
        "reconstructionQuality": deepcopy(reconstruction_quality_state),
        "qualityGateFailureCard": _quality_gate_failure_card(),
        "dynamicUncertaintyProfile": deepcopy(dynamic_uncertainty_profile),
        "plannerCalibrationProfile": deepcopy(calib_profile_snapshot),
        "workflowState": workflow_state,
        "workflowSummary": workflow_state.get("summary", {}),
        "riskThresholdTemplates": _risk_threshold_template_payload(active_risk_threshold_template),
        "riskConstraintPolicy": _continuous_risk_policy(DEFAULT_CONSTRAINT_MODES),
        "degradationNotices": degradation_notices,
        "degradationReasons": degradation_reasons,
        "clinicalContract": _clinical_contract_snapshot(),
        "environmentSignature": deepcopy(environment_signature),
        "environmentSignatureHash": environment_signature.get("signatureHash"),
        "includeRegions": bool(include_regions),
        "regionScope": region_scope,
        "regionCount": region_count,
        "availableRegionCount": available_region_count,
        "plannerRegionCount": planner_region_count,
        "indexedRegionCount": indexed_region_count,
        "fullBrainPlannerReady": effective_full_brain_planner_ready,
        "regionIndex": region_index_status,
        "dynamicRegionProvisioning": DYNAMIC_REGION_PROVISION_ENABLED,
        "artifactReady": bool(active_artifact_ready),
        "missingArtifacts": active_missing_artifacts,
        "catalogRegionCount": int(catalog_region_count),
        "presentRegionCount": int(present_region_count),
        "meshCacheSize": len(_mesh_cache),          # [NEW-1]             
        "regions": regions_payload,
        "lastSeen": _utc_now_iso(),
    })


@app.route("/api/regions", methods=["GET"])
def api_regions():
    region_scope = str(request.args.get("scope", "all")).strip().lower() or "all"
    payload = _build_region_catalog(scope=region_scope)
    result = []
    planner_ready_count = 0
    present_count = 0
    for key, rec in payload.items():
        planner_ready = bool(rec.get("plannerReady", False))
        present_in_volume = bool(rec.get("presentInVolume", False))
        planner_ready_count += 1 if planner_ready else 0
        present_count += 1 if present_in_volume else 0
        result.append({
            "key":         key,
            "structureId": rec.get("structureId"),
            "structureIdPath": rec.get("structureIdPath", []),
            "ap":          rec.get("ap", 0.0),
            "dv":          rec.get("dv", 0.0),
            "ml":          rec.get("ml", 0.0),
            "category":    rec.get("category", "dynamic"),
            "colorHex":    rec.get("colorHex", ""),
            "colorR":      rec.get("colorR"),
            "colorG":      rec.get("colorG"),
            "colorB":      rec.get("colorB"),
            "groupId":     rec.get("groupId"),
            "groupKey":    rec.get("groupKey", ""),
            "groupName":   rec.get("groupName", ""),
            "groupNameCn": rec.get("groupNameCn", ""),
            "voxelCount":  rec.get("voxel_count", 0),
            "volumeMm3":   rec.get("volume_mm3", 0.0),
            "nSubregions": rec.get("n_subregions", 0),
            "plannerReady": planner_ready,
            "presentInVolume": present_in_volume,
            "unavailableReason": rec.get("unavailableReason", ""),
            "name":        rec.get("name", key),
        })
    return jsonify({
        "regions": result,
        "scope": region_scope,
        "activeAtlasMode": ACTIVE_ATLAS_MODE,
        "regionCount": len(result),
        "catalogRegionCount": len(result),
        "presentRegionCount": present_count,
        "plannerRegionCount": planner_ready_count,
        "missingRegionCount": max(0, len(result) - present_count),
        "requestId": _request_id(),
        "engineVersion": ENGINE_VERSION,
    })


@app.route("/api/regions_unity", methods=["GET"])
def api_regions_unity():
    """
    Lightweight region catalog for Unity bridge bootstrap.
    Keeps response shape minimal and stable to avoid parser fallback to default 31 regions.
    """
    region_scope = str(request.args.get("scope", "all")).strip().lower() or "all"
    payload = _build_region_catalog(scope=region_scope)
    regions = []
    for key, rec in payload.items():
        sid = rec.get("structureId")
        try:
            sid = int(sid)
        except Exception:
            sid = -1
        if sid <= 0:
            continue
        regions.append(
            {
                "key": str(key),
                "structureId": sid,
                "structureIdPath": rec.get("structureIdPath", []),
                "ap": float(rec.get("ap", 0.0) or 0.0),
                "dv": float(rec.get("dv", 0.0) or 0.0),
                "ml": float(rec.get("ml", 0.0) or 0.0),
                "category": str(rec.get("category", "dynamic") or "dynamic"),
                "name": str(rec.get("name", key) or key),
                "colorHex": str(rec.get("colorHex", "") or ""),
                "colorR": rec.get("colorR"),
                "colorG": rec.get("colorG"),
                "colorB": rec.get("colorB"),
                "groupKey": str(rec.get("groupKey", "") or ""),
                "groupName": str(rec.get("groupName", "") or ""),
                "groupNameCn": str(rec.get("groupNameCn", "") or ""),
                "voxelCount": int(rec.get("voxel_count", 0) or 0),
                "nSubregions": int(rec.get("n_subregions", 0) or 0),
                "volumeAvailabilityKnown": bool(atlas_loaded),
                "presentInVolume": bool(rec.get("presentInVolume", False)),
                "unavailableReason": str(rec.get("unavailableReason", "") or ""),
            }
        )
    return jsonify(
        {
            "regions": regions,
            "regionCount": len(regions),
            "scope": region_scope,
            "activeAtlasMode": ACTIVE_ATLAS_MODE,
            "requestId": _request_id(),
            "engineVersion": ENGINE_VERSION,
        }
    )


def _native_engine_service_enabled():
    return str(os.environ.get("PROBEPLANNER_USE_ENGINE_SERVICE", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _native_engine_base_url():
    return str(os.environ.get("PROBEPLANNER_ENGINE_URL") or "http://127.0.0.1:7878").rstrip("/")


_NATIVE_ENGINE_PERF_STAGES = {
    "payload_normalized",
    "atlas_ready",
    "candidate_generating",
    "candidate_generated",
    "candidate_prefiltered",
    "candidate_scoring",
    "candidate_scored",
    "pareto_ranked",
    "solver_completed",
    "payload_finalized",
}


def _normalize_native_engine_payload(engine_payload, body):
    payload = deepcopy(engine_payload if isinstance(engine_payload, dict) else {})
    payload.setdefault("ok", True)
    payload.setdefault("selected", [])
    payload.setdefault("uncovered", list(body.get("targetKeys") or []))
    payload.setdefault("algorithm", str((body.get("params") or {}).get("algorithm") or "engine"))
    payload.setdefault("candidateCount", int(payload.pop("candidate_count", 0) or 0) if "candidate_count" in payload else int(payload.get("candidateCount", 0) or 0))
    payload.setdefault(
        "candidateUniverseCount",
        int(payload.pop("candidate_universe_count", 0) or 0)
        if "candidate_universe_count" in payload
        else int(payload.get("candidateUniverseCount", 0) or 0),
    )
    payload.setdefault("timeMs", int(payload.pop("time_ms", 0) or 0) if "time_ms" in payload else int(payload.get("timeMs", 0) or 0))
    payload.setdefault("computeProfile", payload.pop("compute_profile", {}) if "compute_profile" in payload else payload.get("computeProfile", {}))
    payload.setdefault("candidateStageBreakdown", {
        "engineService": True,
        "nativeEngineStatus": payload.get("engineStatus") or payload.get("engine_status"),
    })
    payload["nativeEngineService"] = {
        "enabled": True,
        "url": _native_engine_base_url(),
        "status": payload.get("engineStatus") or payload.get("engine_status"),
    }
    compute_profile = payload.get("computeProfile") if isinstance(payload.get("computeProfile"), dict) else {}
    fallback_keys = compute_profile.get("fallbackTargetKeys") if isinstance(compute_profile, dict) else []
    if not isinstance(fallback_keys, list):
        fallback_keys = []
    fallback_keys = [str(key) for key in fallback_keys if str(key or "").strip()]
    fallback_set = set(fallback_keys)
    uncovered_keys = payload.get("uncovered") if isinstance(payload.get("uncovered"), list) else []
    fallback_uncovered = [str(key) for key in uncovered_keys if str(key) in fallback_set]
    atlas_backed_uncovered = [str(key) for key in uncovered_keys if str(key) not in fallback_set]
    payload["fallbackTargetKeys"] = fallback_keys
    payload["fallbackUncovered"] = fallback_uncovered
    payload["atlasBackedUncovered"] = atlas_backed_uncovered
    payload["atlasBackedUncoveredCount"] = int(len(atlas_backed_uncovered))
    if fallback_keys:
        notices = payload.get("degradationNotices") if isinstance(payload.get("degradationNotices"), list) else []
        notices.append({
            "code": "native_fallback_targets_excluded",
            "severity": "warning",
            "messageEn": "Some requested targets are not atlas-backed in the native engine and were excluded from hard coverage gates.",
            "messageCn": "部分请求目标未被 native atlas 索引支持，已从真实覆盖门禁中排除。",
            "targetKeys": fallback_keys,
        })
        payload["degradationNotices"] = notices
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    probe_params = body.get("probeParams") if isinstance(body.get("probeParams"), dict) else {}
    constraint_modes = body.get("constraintModes") if isinstance(body.get("constraintModes"), dict) else DEFAULT_CONSTRAINT_MODES
    correction_factors = body.get("correctionFactors") if isinstance(body.get("correctionFactors"), dict) else DEFAULT_CORRECTION_FACTORS
    correction_unc = body.get("correctionUncertainty") if isinstance(body.get("correctionUncertainty"), dict) else {}
    payload.setdefault("riskThresholdTemplate", str(params.get("riskThresholdTemplate", RISK_THRESHOLD_TEMPLATE_DEFAULT)))
    payload.setdefault("riskThresholdsMm", deepcopy(params.get("riskThresholdsMm") or {}))
    payload.setdefault("minRiskDistanceMm", float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)))
    payload.setdefault("correctionApplied", bool(body.get("correctionApplied", False)))
    payload.setdefault("correctionFactors", deepcopy(correction_factors))
    payload.setdefault("correctionUncertainty", deepcopy(correction_unc))
    selected_for_correction = payload.get("selected") if isinstance(payload.get("selected"), list) else []
    for probe in selected_for_correction:
        if not isinstance(probe, dict):
            continue
        try:
            raw_ap = float(probe.get("ap_mm", 0.0) or 0.0)
            raw_ml = float(probe.get("ml_mm", 0.0) or 0.0)
            raw_dv = float(probe.get("entryDvCorr_mm", 0.0) or 0.0)
            corr_ap, corr_ml, corr_dv = _apply_correction(raw_ap, raw_ml, raw_dv, correction_factors)
            probe["ap_mm_corrected"] = round(corr_ap, 5)
            probe["ml_mm_corrected"] = round(corr_ml, 5)
            probe["entryDvCorr_mm_corrected"] = round(corr_dv, 5)
            interval = _build_coordinate_error_interval(raw_ap, raw_ml, raw_dv, correction_unc)
            if interval is not None:
                probe["errorInterval"] = interval
        except Exception:
            continue
    unified_audit_checked = 0
    unified_audit_errors = []
    target_keys = [str(key) for key in (body.get("targetKeys") or [])]
    for probe_index, probe in enumerate(selected_for_correction):
        if not isinstance(probe, dict):
            continue
        if all(
            key in probe
            for key in (
                "brainContainmentPassed",
                "tipInTarget",
                "distalOvershootMm",
                "internalRiskViolationCount",
                "entrySurfaceCrossingStatus",
            )
        ):
            unified_audit_checked += 1
            continue
        if not atlas_loaded:
            probe.update({
                "brainContainmentPassed": None,
                "tipInTarget": None,
                "distalOvershootMm": None,
                "internalRiskViolationCount": None,
                "minimumInternalRiskMarginMm": None,
                "entrySurfaceCrossingStatus": "audit_unavailable",
                "brainContainmentAudit": {"available": False, "reason": "compat_atlas_not_loaded"},
                "targetEndpointAudit": {"available": False, "reason": "compat_atlas_not_loaded"},
                "internalRiskAudit": {
                    "claimStatus": "unavailable",
                    "availableClasses": [],
                    "unavailableClasses": sorted(INTERNAL_AVOIDANCE_RISK_CLASSES),
                    "missingDiagnosticClasses": [],
                    "violationClasses": [],
                    "classes": {},
                },
                "entrySurfaceAudit": {
                    "semantics": "audit unavailable because compatibility atlas is not loaded",
                    "classes": {},
                },
            })
            continue
        try:
            risk_diag = _compute_probe_risk_diagnostics(
                probe,
                step_um=RISK_DIAG_STEP_UM,
                threshold_mm=float(params.get("minRiskDistanceMm", MIN_RISK_DISTANCE_MM_DEFAULT)),
                threshold_by_class=params.get("riskThresholdsMm"),
                cvar_alpha=float(params.get("drccCvarAlpha", DRCC_CVAR_ALPHA_DEFAULT)),
            )
            if isinstance(risk_diag, dict):
                probe["riskDiagnostics"] = risk_diag
            probe.update(_build_unified_probe_audit(probe, target_keys, params))
            unified_audit_checked += 1
        except Exception as exc:
            unified_audit_errors.append({"probeIdx": probe_index, "error": str(exc)})
            probe.setdefault("brainContainmentPassed", None)
            probe.setdefault("tipInTarget", None)
            probe.setdefault("distalOvershootMm", None)
            probe.setdefault("internalRiskViolationCount", None)
            probe.setdefault("minimumInternalRiskMarginMm", None)
            probe.setdefault("entrySurfaceCrossingStatus", "audit_error")
            probe.setdefault("internalRiskAudit", {"claimStatus": "unavailable"})
    try:
        selected = payload.get("selected") if isinstance(payload.get("selected"), list) else []
        safety_issues = _collect_safety_issues(
            selected,
            params,
            probe_params,
            constraint_modes,
            candidates=selected,
        )
        payload["safetyIssues"] = safety_issues
        blocking = [
            issue for issue in safety_issues
            if isinstance(issue, dict) and str(issue.get("mode") or "").lower() == "enforce"
        ]
        audit = {
            "checked": True,
            "issueCount": int(len(safety_issues)),
            "blockingCount": int(len(blocking)),
            "source": "flask_compat_post_native_audit",
            "unifiedProbeAuditCount": int(unified_audit_checked),
            "unifiedProbeAuditUnavailableCount": int(
                sum(
                    1
                    for probe in selected
                    if isinstance(probe, dict) and probe.get("brainContainmentPassed") is None
                )
            ),
            "unifiedProbeAuditErrors": unified_audit_errors,
        }
        breakdown = payload.get("candidateStageBreakdown")
        if not isinstance(breakdown, dict):
            breakdown = {}
        breakdown["nativeCompatSafetyAudit"] = audit
        payload["candidateStageBreakdown"] = breakdown
        if blocking:
            payload["ok"] = False
            payload["code"] = "native_engine_constraint_violation"
            payload["error"] = "Native planner-engine returned candidates that violate enforced constraints"
            payload["details"] = [
                str(issue.get("msgEn") or issue.get("constraint") or "enforced constraint violation")
                for issue in blocking[:5]
            ]
    except Exception as exc:
        breakdown = payload.get("candidateStageBreakdown")
        if not isinstance(breakdown, dict):
            breakdown = {}
        breakdown["nativeCompatSafetyAudit"] = {
            "checked": False,
            "error": str(exc),
            "source": "flask_compat_post_native_audit",
        }
        payload["candidateStageBreakdown"] = breakdown
    return payload


def _run_native_engine_service_pipeline(body, task_id):
    base_url = _native_engine_base_url()
    submit_body = deepcopy(body)
    submit_body["taskId"] = task_id
    try:
        _update_task_progress(task_id, "engine_submit", 0.03, "Submitting request to native planner-engine")
        submitted = http_requests.post(f"{base_url}/v1/tasks", json=submit_body, timeout=10)
        submitted.raise_for_status()
        submit_payload = submitted.json()
        engine_task_id = str(submit_payload.get("taskId") or task_id)
        _update_task_progress(task_id, "engine_queued", 0.05, "Native planner-engine task queued")
        deadline = time.monotonic() + float(os.environ.get("PROBEPLANNER_ENGINE_TIMEOUT_S", "900") or 900)
        while time.monotonic() < deadline:
            _check_task_cancelled(task_id, "engine_service")
            if task_registry.is_cancelled(task_id):
                try:
                    http_requests.post(f"{base_url}/v1/tasks/{engine_task_id}/cancel", timeout=5)
                except Exception:
                    pass
                raise ContractError("Task cancelled", status_code=499, code="task_cancelled")
            status_resp = http_requests.get(f"{base_url}/v1/tasks/{engine_task_id}", timeout=10)
            status_resp.raise_for_status()
            status_payload = status_resp.json()
            stage = str(status_payload.get("stage") or "engine_running")
            progress = float(status_payload.get("progress") or 0.0)
            message = str(status_payload.get("message") or stage)
            public_stage = stage if stage in _NATIVE_ENGINE_PERF_STAGES else (
                f"engine_{stage}" if not stage.startswith("engine_") else stage
            )
            _update_task_progress(
                task_id,
                public_stage,
                max(0.05, min(0.94, 0.05 + progress * 0.89)),
                message,
            )
            state = str(status_payload.get("status") or "").strip().lower()
            if state == "completed":
                result = status_payload.get("result") if isinstance(status_payload.get("result"), dict) else {}
                return _normalize_native_engine_payload(result, body)
            if state in {"failed", "cancelled"}:
                raise ContractError(
                    str(status_payload.get("error") or status_payload.get("message") or f"Native engine task {state}"),
                    status_code=502,
                    code=f"native_engine_{state}",
                    extra={"nativeEngineTask": status_payload},
                )
            time.sleep(float(os.environ.get("PROBEPLANNER_ENGINE_POLL_S", "0.25") or 0.25))
        try:
            http_requests.post(f"{base_url}/v1/tasks/{engine_task_id}/cancel", timeout=5)
        except Exception:
            pass
        raise ContractError(
            "Native planner-engine task timed out",
            status_code=504,
            code="native_engine_timeout",
        )
    except ContractError:
        raise
    except Exception as exc:
        raise ContractError(
            "Native planner-engine request failed",
            details=[str(exc)],
            status_code=502,
            code="native_engine_unavailable",
        )


@app.route("/api/optimize", methods=["POST"])
def api_optimize():
    """Full optimization pipeline with voxel coverage."""
    if not atlas_loaded and not _native_engine_service_enabled():
        _ensure_atlas_loader(force_retry=False)
        with atlas_lock:
            load_snapshot = deepcopy(atlas_load_state)
        return _json_atlas_not_ready(load_snapshot, context="Optimize request")

    task_id = None
    try:
        body = _request_json_object(required=True)
        effective_request_payload = body
        task_id = _resolve_task_id(body)
        _set_planning_runtime_state(
            status=TASK_STATE_RUNNING,
            stage="received",
            progress=0.01,
            message="Request received",
            task_id=task_id,
            error=None,
        )
        task_registry.clear(task_id)
        task_registry.begin(task_id, stage="received", progress=0.01, message="Request received")
        schema_errors = validate_optimize_schema(body)
        if schema_errors:
            raise ContractError(
                "Request schema validation failed",
                details=schema_errors,
                status_code=400,
                code="schema_validation_error",
            )
        if _native_engine_service_enabled():
            _update_task_progress(task_id, "payload_normalized", 0.08, "Request payload normalized")
            _check_task_cancelled(task_id, "payload_normalized")
            engine_body = _normalize_optimize_payload(body)
            effective_request_payload = engine_body
            _update_task_progress(task_id, "quality_gate_check", 0.12, "Checking reconstruction quality gate")
            _check_task_cancelled(task_id, "quality_gate_check")
            _enforce_reconstruction_quality_gate()
            params = engine_body.get("params") if isinstance(engine_body.get("params"), dict) else {}
            if (
                str(params.get("algorithm") or "").strip().lower() == "drcc"
                and str(params.get("drccSolver") or "").strip().lower() == "misocp"
            ):
                raise ContractError(
                    "Native planner-engine does not implement legacy Python MISOCP/CVX semantics",
                    details=[
                        "Use exact_pool, hybrid, heuristic, or cvar_relax for native DRCC.",
                        "Run with the legacy Python backend only when a historical MISOCP comparison is explicitly required.",
                    ],
                    status_code=400,
                    code="unsupported_native_drcc_solver",
                )
            requires_strict_python_contract = (
                str(params.get("targetEndpointPolicy") or "trajectory_intersects").strip().lower()
                == "tip_in_target"
                or bool(params.get("stabilityTieBreakerEnabled"))
            )
            if requires_strict_python_contract:
                if not atlas_loaded:
                    _ensure_atlas_loader(force_retry=False)
                    with atlas_lock:
                        load_snapshot = deepcopy(atlas_load_state)
                    raise ContractError(
                        "Atlas is not ready for strict endpoint/stability planning",
                        details=[str(load_snapshot.get("message") or "Atlas loading is still in progress")],
                        status_code=503,
                        code="atlas_not_ready",
                        extra={"atlasLoad": load_snapshot},
                    )
                response_payload = _run_optimize_pipeline(body, task_id=task_id)
                response_payload["plannerExecutionBackend"] = "python_strict_contract"
                response_payload["nativeEngineBypassReason"] = (
                    "tip_in_target_or_reference_stability_requires_latest_python_contract"
                )
            else:
                response_payload = _run_native_engine_service_pipeline(engine_body, task_id=task_id)
                response_payload["plannerExecutionBackend"] = "native_engine_service"
        else:
            response_payload = _run_optimize_pipeline(body, task_id=task_id)
            response_payload["plannerExecutionBackend"] = "python_backend"
        _update_task_progress(task_id, "finalizing_payload", 0.965, "Finalizing optimization payload")
        response_payload["taskId"] = task_id
        response_payload["requestId"] = _request_id()
        response_payload["engineVersion"] = ENGINE_VERSION
        _attach_payload_hashes(effective_request_payload, response_payload)
        _update_task_progress(task_id, "hashing_payload", 0.985, "Computing response hashes")
        _persist_replay_record(_request_id(), effective_request_payload, response_payload)
        _update_task_progress(task_id, "responding", 0.995, "Sending optimization response")
        task_registry.finish(
            task_id,
            status="completed",
            stage="completed",
            progress=1.0,
            message="Optimization completed",
            result=deepcopy(response_payload),
        )
        _set_planning_runtime_state(
            status=TASK_STATE_COMPLETED,
            stage="completed",
            progress=1.0,
            message="Optimization completed",
            task_id=task_id,
            result_summary={
                "selectedCount": int(len(response_payload.get("selected") or [])),
                "uncoveredCount": int(len(response_payload.get("uncovered") or [])),
                "algorithm": str(response_payload.get("algorithm") or ""),
                "timeMs": int(response_payload.get("timeMs") or 0),
                "riskThresholdTemplate": str(
                    response_payload.get("riskThresholdTemplate")
                    or RISK_THRESHOLD_TEMPLATE_DEFAULT
                ),
            },
            error=None,
        )
        return jsonify(response_payload)
    except ContractError as exc:
        exc_extra = deepcopy(getattr(exc, "extra", {}) or {})
        if task_id:
            status = "cancelled" if str(exc.code) == "task_cancelled" else "failed"
            stage = "cancelled" if status == "cancelled" else "failed"
            msg = _format_contract_error_for_task(exc)
            task_result = {
                "ok": False,
                "error": exc.message,
                "code": exc.code,
                "details": list(getattr(exc, "details", []) or []),
            }
            if isinstance(exc_extra, dict):
                task_result.update(deepcopy(exc_extra))
            task_registry.finish(
                task_id,
                status=status,
                stage=stage,
                progress=1.0,
                message=msg,
                details=list(getattr(exc, "details", []) or []),
                result=task_result,
            )
            _set_planning_runtime_state(
                status=TASK_STATE_FAILED,
                stage=stage,
                progress=1.0,
                message=msg,
                task_id=task_id,
                error=msg,
            )
        extra = exc_extra if isinstance(exc_extra, dict) else {}
        if str(exc.code) == "reconstruction_quality_gate_block":
            extra.update({
                "failureCards": [_quality_gate_failure_card()],
                "reconstructionQuality": deepcopy(reconstruction_quality_state),
            })
        return _json_contract_error(exc, extra=extra)
    except BadRequest as exc:
        return _json_bad_request(exc)
    except Exception as e:
        safe_message = _safe_internal_error_message("optimization")
        if task_id:
            task_registry.finish(
                task_id,
                status="failed",
                stage="failed",
                progress=1.0,
                message=safe_message,
                details=[safe_message],
                result={
                    "ok": False,
                    "error": safe_message,
                    "code": "internal_error",
                    "details": [safe_message],
                },
            )
            _set_planning_runtime_state(
                status=TASK_STATE_FAILED,
                stage="failed",
                progress=1.0,
                message=safe_message,
                task_id=task_id,
                error=safe_message,
            )
        return _json_internal_error(e, context="Optimize request")


@app.route("/api/task/cancel", methods=["POST"])
def api_task_cancel():
    body = _request_json_object(required=False)
    task_id = _resolve_task_id(body)
    if not task_id:
        return _json_error(
            "taskId is required",
            status=400,
            code="validation_error",
            details=["Provide taskId/requestId/jobId in request body"],
        )
    task_registry.cancel(task_id)
    _set_planning_runtime_state(
        status=TASK_STATE_FAILED,
        stage="cancelled",
        progress=1.0,
        message="Task cancelled",
        task_id=task_id,
        error="cancelled",
    )
    structured_log(log, "task.cancelled", requestId=_request_id(), taskId=task_id)
    return jsonify(
        {
            "ok": True,
            "taskId": task_id,
            "cancelled": True,
            "requestId": _request_id(),
            "engineVersion": ENGINE_VERSION,
        }
    )


@app.route("/api/task/status/<task_id>", methods=["GET"])
def api_task_status(task_id):
    task_id = str(task_id or "").strip()
    if not task_id:
        return _json_error("taskId is required", status=400, code="validation_error")
    state = _public_task_state_snapshot(task_registry.get(task_id))
    return jsonify(
        {
            "ok": True,
            "taskId": task_id,
            "cancelled": bool(state.get("cancelled") or task_registry.is_cancelled(task_id)),
            "task": state,
            "requestId": _request_id(),
            "engineVersion": ENGINE_VERSION,
        }
    )


@app.route("/api/coverage", methods=["POST"])
def api_coverage():
    """Compute precise voxel coverage for planned trajectories."""
    if not atlas_loaded:
        _ensure_atlas_loader(force_retry=False)
        with atlas_lock:
            load_snapshot = deepcopy(atlas_load_state)
        return _json_atlas_not_ready(load_snapshot, context="Coverage request")

    try:
        body = _request_json_object(required=True)
        payload = _normalize_coverage_payload(body)
        trajs = payload["trajectories"]
        target_keys = payload["targetKeys"]
        target_ids = {k: PLANNER_REGIONS[k]["id"] for k in target_keys if k in PLANNER_REGIONS}

        enriched = []
        for traj in trajs:
            voxels = sample_trajectory_voxels_cached(
                traj.get("ap_mm_corrected", traj["ap_mm"]),
                traj.get("ml_mm_corrected", traj["ml_mm"]),
                traj.get("theta_deg", 0),
                traj.get("phi_deg", 0),
                traj.get("depthUm_corrected", traj.get("depthUm", 10000)),
                traj.get("skullTiltDeg", 0),
                25.0,
                entry_dv_corr_mm=_probe_entry_dv_mm(traj),
            )

            cov_dict = voxel_coverage(voxels, list(target_ids.values()))
            covered_keys = [k for k, sid in target_ids.items() if cov_dict.get(sid, False)]
            overlap_counts = {k: voxel_overlap_count(voxels, target_ids[k]) for k in covered_keys}
            enriched.append(
                {
                    **traj,
                    "coveredRegions": covered_keys,
                    "overlapVoxels": overlap_counts,
                    "nVoxelsTotal": len(voxels),
                }
            )

        return jsonify(
            {
                "ok": True,
                "trajectories": enriched,
                "requestId": _request_id(),
                "engineVersion": ENGINE_VERSION,
            }
        )
    except ContractError as exc:
        return _json_contract_error(exc)
    except BadRequest as exc:
        return _json_bad_request(exc)
    except Exception as e:
        return _json_internal_error(e, context="Coverage request")


@app.route("/api/probe_contact_profile", methods=["POST"])
def api_probe_contact_profile():
    """Return depth-ordered contact profile along each probe trajectory."""
    if not atlas_loaded:
        _ensure_atlas_loader(force_retry=False)
        with atlas_lock:
            load_snapshot = deepcopy(atlas_load_state)
        return _json_atlas_not_ready(load_snapshot, context="Probe contact profile request")

    try:
        body = _request_json_object(required=True)
        payload = _normalize_contact_profile_payload(body)
        probes = payload["probes"]
        if not probes:
            return jsonify(
                {
                    "ok": True,
                    "probes": [],
                    "contactRegionSet": [],
                    "samplingStepUm": float(payload["stepUm"]),
                    "contactMode": payload["contactMode"],
                    "atlasHash": atlas_signature.get("hash"),
                    "requestId": _request_id(),
                    "engineVersion": ENGINE_VERSION,
                }
            )

        step_um = float(payload["stepUm"])
        min_contact_length_um = float(payload["minContactLengthUm"])
        use_cylinder = payload["contactMode"] != "centerline"
        response_items = []
        region_set = set()

        for idx, probe in enumerate(probes):
            profile = _compute_probe_contact_profile(
                probe,
                step_um=step_um,
                min_contact_length_um=min_contact_length_um,
                use_cylinder=use_cylinder,
            )
            response_items.append(
                {
                    "index": idx,
                    "probeType": probe.get("probeType"),
                    **profile,
                }
            )
            for key in profile.get("contactRegionSet", []):
                region_set.add(str(key))

        return jsonify(
            {
                "ok": True,
                "probes": response_items,
                "contactRegionSet": sorted(region_set),
                "samplingStepUm": step_um,
                "minContactLengthUm": min_contact_length_um,
                "contactMode": "cylinder" if use_cylinder else "centerline",
                "atlasHash": atlas_signature.get("hash"),
                "requestId": _request_id(),
                "engineVersion": ENGINE_VERSION,
            }
        )
    except ContractError as exc:
        return _json_contract_error(exc)
    except BadRequest as exc:
        return _json_bad_request(exc)
    except Exception as e:
        return _json_internal_error(e, context="Probe contact profile request")


def _export_plan_provenance(body, trajectories):
    request_id = body.get("planRequestId")
    if not request_id:
        return {"status": "unverified_geometry", "reason": "not_linked_to_saved_planner_result"}
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", request_id):
        raise ContractError("Invalid planRequestId")
    record = _load_replay_record(request_id)
    response = record.get("response", {}) if isinstance(record, dict) else {}
    if not response or response.get("responseHash") != body.get("planResponseHash"):
        raise ContractError("Source plan missing or response hash mismatch")
    selected = response.get("selected")
    if not isinstance(selected, list) or len(selected) != len(trajectories):
        raise ContractError("Export trajectories do not match the saved plan")
    if [_normalize_export_trajectory(item, i) for i, item in enumerate(selected)] != trajectories:
        raise ContractError("Export trajectories do not match the saved plan")
    saved_corr = response.get("correctionFactors", record.get("request", {}).get("correctionFactors", {}))
    if _normalize_correction_factors(body.get("correctionFactors", {}))[0] != _normalize_correction_factors(saved_corr)[0]:
        raise ContractError("Export correction factors do not match the saved plan")
    return {
        "status": "linked_to_saved_planner_result",
        "requestId": request_id,
        "requestHash": response.get("requestHash"),
        "responseHash": response.get("responseHash"),
        "runtimeIdentity": deepcopy(response.get("runtimeIdentity")),
        "scope": "Saved raw/resolved geometry, correction factors and recorded hardware; no new constraint or anatomical validation",
    }


@app.route("/api/export_pinpoint", methods=["POST"])
def api_export_pinpoint():
    """Generate Pinpoint-compatible export JSON."""
    try:
        body = _request_json_object(required=True)
        payload = _normalize_export_payload(body)
        trajs = payload["trajectories"]
        probe_type = payload["probeType"]
        corr = payload["correctionFactors"]
        corr_unc = payload["correctionUncertainty"]
        source_plan = _export_plan_provenance(body, trajs)

        probes = []
        for i, t in enumerate(trajs):
            raw_ap = float(t.get("ap_mm", 0))
            raw_ml = float(t.get("ml_mm", 0))
            raw_dv = float(t.get("entryDvCorr_mm", 0))
            corr_ap, corr_ml, corr_dv = _apply_correction(raw_ap, raw_ml, raw_dv, corr)
            corr_ap = float(t.get("ap_mm_corrected", corr_ap))
            corr_ml = float(t.get("ml_mm_corrected", corr_ml))
            corr_dv = float(t.get("entryDvCorr_mm_corrected", corr_dv))
            depth_mm = float(t.get("depthUm_corrected", float(t.get("depthUm", 10000)) * corr["dv"])) / 1000.0

            probe = {
                "id": f"P{i+1}",
                "ap": -corr_ap,
                "ml": corr_ml,
                "dv": corr_dv,
                "depth": depth_mm,
                "phi": float(t.get("phi_deg", 0)),
                "theta": float(t.get("theta_deg", 0)),
                "spin": t['spin_deg'],
                "probeType": t['probeType'] or probe_type,
                "coveredRegions": t.get("coveredRegions", []),
            }
            for key in ('shankCount', 'shankPitchUm', 'radiusUm', 'shankWidthUm', 'shankThicknessUm'):
                if key in t:
                    probe[key] = t[key]
            interval = _build_coordinate_error_interval(raw_ap, raw_ml, raw_dv, corr_unc)
            if interval is not None:
                probe["errorInterval"] = interval
            probes.append(probe)

        pinpoint_data = {
            "_version": "1.0",
            "_software": "ProbeplannerGUI / optimizer_server",
            "_atlas": "CCFv3_25um",
            "_atlasHash": atlas_signature.get("hash"),
            "_engineVersion": ENGINE_VERSION,
            "_coordinateSystem": "pinpoint_ap_posterior_positive_mm",
            "_sourcePlan": source_plan,
            "_exportRuntime": deepcopy(app.config.get("PAPER_RUNTIME_IDENTITY")),
            "_requestId": _request_id(),
            "_coordinateCorrectionApplied": payload["correctionApplied"] or any(
                t.get(field + "_corrected", t[field]) != t[field]
                for t in trajs for field in ("ap_mm", "ml_mm", "entryDvCorr_mm", "depthUm")),
            "_correctionFactors": corr,
            "_correctionUncertainty": corr_unc,
            "probes": probes,
        }

        response_payload = {
            "ok": True,
            "pinpointData": pinpoint_data,
            "requestId": _request_id(),
            "engineVersion": ENGINE_VERSION,
            "atlasSignature": atlas_signature,
            "atlasHash": atlas_signature.get("hash"),
            "versionFrozen": version_freeze_state.get("frozen", False),
            "versionFreezeState": deepcopy(version_freeze_state),
        }
        _attach_payload_hashes(body, response_payload)
        _persist_replay_record(_request_id(), body, response_payload)
        return jsonify(response_payload)
    except ContractError as exc:
        return _json_contract_error(exc)
    except BadRequest as exc:
        return _json_bad_request(exc)
    except Exception as e:
        return _json_internal_error(e, context="Pinpoint export request")


@app.route("/api/register_landmarks", methods=["POST"])
def api_register_landmarks():
    """Compute coordinate correction factors from individual mouse landmarks."""
    try:
        _set_registration_runtime_state(
            status=TASK_STATE_RUNNING,
            stage="validation",
            progress=0.12,
            message="Validating landmark payload",
            error=None,
        )
        body = _request_json_object(required=True)
        payload = _normalize_landmark_payload(body)
        _set_registration_runtime_state(
            status=TASK_STATE_RUNNING,
            stage="solve",
            progress=0.55,
            message="Solving landmark correction factors",
        )
        bregma = payload["bregma"]
        lambda_ = payload["lambda"]
        ml_ref = payload["mlRef"]

        bl_dist_ap = float(np.abs(bregma[0] - lambda_[0]))
        bl_dist = float(np.linalg.norm(bregma - lambda_))
        ml_dist = float(np.abs(bregma[2] - ml_ref[2]))
        if ml_dist < 0.01:
            ml_dist = float(np.sqrt((bregma[1] - ml_ref[1]) ** 2 + (bregma[2] - ml_ref[2]) ** 2))

        if bl_dist_ap < 0.01:
            bl_dist_ap = ATLAS_BL_DIST_MM
        if ml_dist < 0.01:
            ml_dist = ATLAS_ML_DIST_MM

        ap_scale = ATLAS_BL_DIST_MM / bl_dist_ap
        ml_scale = ATLAS_ML_DIST_MM / ml_dist
        dv_scale = (ap_scale + ml_scale) / 2.0

        ap_shift = float(-bregma[0] * ap_scale)
        ml_shift = float(-bregma[2] * ml_scale)
        dv_shift = float(-bregma[1] * dv_scale)

        factors = {
            "ap": round(ap_scale, 5),
            "ml": round(ml_scale, 5),
            "dv": round(dv_scale, 5),
            "apShift": round(ap_shift, 3),
            "mlShift": round(ml_shift, 3),
            "dvShift": round(dv_shift, 3),
        }
        uncertainty = _compute_registration_uncertainty(
            ap_scale,
            ml_scale,
            dv_scale,
            ap_shift,
            ml_shift,
            dv_shift,
            bl_dist,
            payload.get("sigmaMm", 0.10),
        )

        log.info(
            f"Registration: BL_dist={bl_dist:.2f}mm -> APx{ap_scale:.4f}, MLx{ml_scale:.4f}"
        )
        result_payload = {
            "factors": factors,
            "uncertainty": uncertainty,
            "bregmaLambdaDistMeasured": round(bl_dist, 3),
            "mlRefDistMeasured": round(ml_dist, 3),
        }
        _set_registration_runtime_state(
            status=TASK_STATE_COMPLETED,
            stage="completed",
            progress=1.0,
            message="Landmark registration completed",
            result=result_payload,
            error=None,
        )

        return jsonify(
            {
                "ok": True,
                "factors": factors,
                "uncertainty": uncertainty,
                "bregmaLambdaDistMeasured": round(bl_dist, 3),
                "bregmaLambdaDistAtlas": ATLAS_BL_DIST_MM,
                "mlRefDistMeasured": round(ml_dist, 3),
                "mlRefDistAtlas": ATLAS_ML_DIST_MM,
                "requestId": _request_id(),
                "engineVersion": ENGINE_VERSION,
            }
        )
    except ContractError as exc:
        _set_registration_runtime_state(
            status=TASK_STATE_FAILED,
            stage="failed",
            progress=1.0,
            message=exc.message,
            error=exc.message,
        )
        return _json_contract_error(exc)
    except BadRequest as exc:
        _set_registration_runtime_state(
            status=TASK_STATE_FAILED,
            stage="failed",
            progress=1.0,
            message="Invalid JSON request body",
            error="Invalid JSON request body",
        )
        return _json_bad_request(exc)
    except Exception as e:
        safe_message = _safe_internal_error_message("registration")
        _set_registration_runtime_state(
            status=TASK_STATE_FAILED,
            stage="failed",
            progress=1.0,
            message=safe_message,
            error=safe_message,
        )
        return _json_internal_error(e, context="Registration request")


@app.route("/api/version", methods=["GET"])
def api_version_state():
    region_catalog_count = len(_build_region_catalog(scope="all"))
    available_region_count = _compute_available_region_count(
        _build_region_catalog(scope="all")
    )
    qiu_payload = _qiu_payload()
    mri_payload = _mri_payload()
    with atlas_lock:
        atlas_load_snapshot = deepcopy(atlas_load_state)
        atlas_loaded_snapshot = bool(atlas_loaded)
        atlas_error_snapshot = atlas_error
        structure_snapshot = dict(structure_map)
    present_region_count = int(
        sum(
            1
            for _, info in structure_snapshot.items()
            if isinstance(info, dict) and int(info.get("voxel_count", 0) or 0) > 0
        )
    )
    with workflow_state_lock:
        planning_last = deepcopy(planning_runtime_state.get("lastResultSummary") or {})
    active_risk_template = _normalize_risk_threshold_template(
        planning_last.get("riskThresholdTemplate")
    )
    public_atlas_error = _public_atlas_error(atlas_error_snapshot)
    public_atlas_load_snapshot = _public_atlas_load_snapshot(atlas_load_snapshot, atlas_error_snapshot)
    return jsonify({
        "ok": True,
        "engineVersion": ENGINE_VERSION,
        "atlasSignature": atlas_signature,
        "atlasHash": atlas_signature.get("hash"),
        "environmentSignature": deepcopy(environment_signature),
        "environmentSignatureHash": environment_signature.get("signatureHash"),
        "riskModel": deepcopy(risk_model_summary),
        "workflowState": _build_unified_workflow_state(
            atlas_loaded_snapshot=atlas_loaded_snapshot,
            atlas_load_snapshot=public_atlas_load_snapshot,
            atlas_error_snapshot=public_atlas_error,
        ),
        "riskThresholdTemplates": _risk_threshold_template_payload(active_risk_template),
        "reconstructionQuality": deepcopy(reconstruction_quality_state),
        "qualityGateFailureCard": _quality_gate_failure_card(),
        "dynamicUncertaintyProfile": deepcopy(dynamic_uncertainty_profile),
        "riskConstraintPolicy": _continuous_risk_policy(DEFAULT_CONSTRAINT_MODES),
        "degradationNotices": _build_degradation_notices(
            constraint_modes=DEFAULT_CONSTRAINT_MODES,
            qiu_payload=qiu_payload,
            mri_payload=mri_payload,
        ),
        "qiu2018": qiu_payload,
        "mriCcfHybrid": mri_payload,
        "artifactReady": bool(
            mri_payload.get("artifactReady", False)
            if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid"
            else qiu_payload.get("annotationConfigured", True)
        ),
        "missingArtifacts": list(
            mri_payload.get("missingArtifacts") or []
            if ACTIVE_ATLAS_MODE == "mri_ccf_hybrid"
            else ([] if qiu_payload.get("annotationConfigured") else ["annotation"])
        ),
        "clinicalContract": _clinical_contract_snapshot(),
        "regionCount": region_catalog_count,
        "catalogRegionCount": region_catalog_count,
        "presentRegionCount": present_region_count,
        "availableRegionCount": available_region_count,
        "plannerRegionCount": len(PLANNER_REGIONS),
        "indexedRegionCount": len(acronym_record_map),
        "dynamicRegionProvisioning": DYNAMIC_REGION_PROVISION_ENABLED,
        "versionFreezeState": version_freeze_state,
        "requestId": _request_id(),
    })


@app.route("/api/reconstruction/quality", methods=["GET"])
def api_reconstruction_quality():
    return jsonify(
        {
            "ok": True,
            "activeAtlasMode": ACTIVE_ATLAS_MODE,
            "quality": deepcopy(reconstruction_quality_state),
            "dynamicUncertaintyProfile": deepcopy(dynamic_uncertainty_profile),
            "qualityGateFailureCard": _quality_gate_failure_card(),
            "qiu2018": _qiu_payload(),
            "mriCcfHybrid": _mri_payload(),
            "requestId": _request_id(),
            "engineVersion": ENGINE_VERSION,
        }
    )


@app.route("/api/clinical_contract", methods=["GET"])
def api_clinical_contract():
    return jsonify(
        {
            "ok": True,
            "clinicalContract": _clinical_contract_snapshot(),
            "engineVersion": ENGINE_VERSION,
            "atlasHash": atlas_signature.get("hash"),
            "requestId": _request_id(),
        }
    )


@app.route("/api/version/freeze", methods=["POST"])
def api_freeze_version():
    body = _request_json_object(required=False)
    freeze = _coerce_bool(body.get("freeze", True), default=True)
    note = str(body.get("note", "")).strip()
    actor = getattr(g, "auth_subject", "anonymous")

    version_freeze_state["frozen"] = freeze
    version_freeze_state["engineVersion"] = ENGINE_VERSION
    version_freeze_state["atlasHash"] = atlas_signature.get("hash")
    version_freeze_state["frozenAt"] = _utc_now_iso() if freeze else None
    version_freeze_state["frozenBy"] = actor if freeze else None
    version_freeze_state["note"] = note

    try:
        _persist_version_freeze_state()
    except Exception as exc:
        return _json_internal_error(
            exc,
            context="Version freeze persist",
            message="Failed to persist version freeze state",
            code="version_freeze_persist_failed",
        )

    return jsonify({
        "ok": True,
        "versionFreezeState": version_freeze_state,
        "requestId": _request_id(),
        "engineVersion": ENGINE_VERSION,
    })


@app.route("/api/replay/<request_id>", methods=["GET"])
def api_get_replay(request_id):
    record = _load_replay_record(request_id)
    if record is None:
        return _json_error("Replay record not found", status=404, code="not_found")
    return jsonify(
        {
            "ok": True,
            "record": record,
            "requestId": _request_id(),
            "engineVersion": ENGINE_VERSION,
        }
    )


@app.route("/api/replay/<request_id>/rerun", methods=["POST"])
def api_rerun_replay(request_id):
    if not atlas_loaded:
        with atlas_lock:
            load_snapshot = deepcopy(atlas_load_state)
        return _json_atlas_not_ready(load_snapshot, context="Replay rerun request")

    record = _load_replay_record(request_id)
    if record is None:
        return _json_error("Replay record not found", status=404, code="not_found")

    try:
        request_payload = record.get("request") or {}
        response_payload = _run_optimize_pipeline(request_payload)
        response_payload["requestId"] = _request_id()
        response_payload["engineVersion"] = ENGINE_VERSION
        response_payload["replayOf"] = request_id
        _attach_payload_hashes(request_payload, response_payload)
        _persist_replay_record(_request_id(), request_payload, response_payload)
        return jsonify(response_payload)
    except ContractError as exc:
        return _json_contract_error(exc)
    except Exception as e:
        return _json_internal_error(e, context="Replay rerun request")


@app.route("/api/benchmark/status", methods=["GET"])
def api_benchmark_status():
    global benchmark_last_replay_dir
    global benchmark_last_out_dir
    global benchmark_last_expert_csv
    global benchmark_last_manual_override_csv
    global benchmark_last_trial_csv
    global benchmark_last_residual_csv
    global benchmark_last_param_template

    try:
        replay_dir = _safe_benchmark_path(request.args.get("replayDir"), benchmark_last_replay_dir or REPLAY_DIR)
        out_dir = _safe_benchmark_path(request.args.get("outDir"), benchmark_last_out_dir or BENCHMARK_DEFAULT_OUT_DIR)
        defaults = _benchmark_default_template_paths(out_dir)
        expert_csv = _safe_benchmark_path(request.args.get("expertCsv"), benchmark_last_expert_csv or defaults["expertCsv"])
        manual_override_csv = _safe_benchmark_path(
            request.args.get("manualOverrideCsv"),
            benchmark_last_manual_override_csv or defaults["manualOverrideCsv"],
        )
        trial_csv = _safe_benchmark_path(request.args.get("trialCsv"), benchmark_last_trial_csv or defaults["trialCsv"])
        residual_csv = _safe_benchmark_path(
            request.args.get("residualCsv"),
            benchmark_last_residual_csv or defaults["residualCsv"],
        )
        param_template = _safe_benchmark_path(
            request.args.get("paramTemplate"),
            benchmark_last_param_template or defaults["paramTemplate"],
        )

        benchmark_last_replay_dir = replay_dir
        benchmark_last_out_dir = out_dir
        benchmark_last_expert_csv = expert_csv
        benchmark_last_manual_override_csv = manual_override_csv
        benchmark_last_trial_csv = trial_csv
        benchmark_last_residual_csv = residual_csv
        benchmark_last_param_template = param_template

        replay_count = 0
        if replay_dir and replay_dir.exists():
            replay_count = len(list(replay_dir.glob("*.json")))

        template_paths = {
            "expertCsv": str(expert_csv) if expert_csv else None,
            "manualOverrideCsv": str(manual_override_csv) if manual_override_csv else None,
            "trialCsv": str(trial_csv) if trial_csv else None,
            "residualCsv": str(residual_csv) if residual_csv else None,
            "paramTemplate": str(param_template) if param_template else None,
        }
        template_exists = {
            key: bool(value and Path(value).exists())
            for key, value in template_paths.items()
        }

        return jsonify(
            {
                "ok": True,
                "scriptPath": str(BENCHMARK_SCRIPT),
                "scriptExists": BENCHMARK_SCRIPT.exists(),
                "scripts": _benchmark_script_manifest(),
                "runEnabled": BENCHMARK_RUN_ENABLED,
                "allowExternalPaths": BENCHMARK_ALLOW_EXTERNAL_PATHS,
                "allowedRoots": [str(root) for root in BENCHMARK_ALLOWED_ROOTS],
                "replayDir": str(replay_dir),
                "outDir": str(out_dir),
                "templateDir": str(defaults["templateDir"]),
                "templatePaths": template_paths,
                "templateExists": template_exists,
                "replayCount": replay_count,
                "initialized": bool(out_dir.exists() and defaults["templateDir"].exists()),
                "files": _benchmark_manifest(out_dir),
                "lastRun": deepcopy(benchmark_last_manifest),
                "requestId": _request_id(),
                "engineVersion": ENGINE_VERSION,
            }
        )
    except ContractError as exc:
        return _json_contract_error(exc)
    except Exception as exc:
        return _json_internal_error(exc, context="Benchmark status request")


@app.route("/api/benchmark/init", methods=["POST"])
def api_benchmark_init():
    global benchmark_last_replay_dir
    global benchmark_last_out_dir
    global benchmark_last_expert_csv
    global benchmark_last_manual_override_csv
    global benchmark_last_trial_csv
    global benchmark_last_residual_csv
    global benchmark_last_param_template

    body = _request_json_object(required=False)
    replay_dir = body.get("replayDir", str(benchmark_last_replay_dir or REPLAY_DIR))
    out_dir = body.get("outDir", str(benchmark_last_out_dir or BENCHMARK_DEFAULT_OUT_DIR))
    expert_csv = body.get("expertCsv", None)
    manual_override_csv = body.get("manualOverrideCsv", None)
    trial_csv = body.get("trialCsv", None)
    residual_csv = body.get("residualCsv", None)
    param_template = body.get("paramTemplate", None)
    overwrite_templates = _coerce_bool(body.get("overwriteTemplates", False), default=False)

    try:
        result = _init_benchmark_workspace(
            replay_dir=replay_dir,
            out_dir=out_dir,
            expert_csv=expert_csv,
            manual_override_csv=manual_override_csv,
            trial_csv=trial_csv,
            residual_csv=residual_csv,
            param_template=param_template,
            overwrite_templates=overwrite_templates,
        )
        benchmark_last_replay_dir = Path(result["replayDir"])
        benchmark_last_out_dir = Path(result["outDir"])
        benchmark_last_expert_csv = _safe_benchmark_path(result["expertCsv"], None)
        benchmark_last_manual_override_csv = _safe_benchmark_path(result["manualOverrideCsv"], None)
        benchmark_last_trial_csv = _safe_benchmark_path(result["trialCsv"], None)
        benchmark_last_residual_csv = _safe_benchmark_path(result["residualCsv"], None)
        benchmark_last_param_template = _safe_benchmark_path(result["paramTemplate"], None)
        return jsonify(
            {
                "ok": True,
                **result,
                "scripts": _benchmark_script_manifest(),
                "files": _benchmark_manifest(Path(result["outDir"])),
                "requestId": _request_id(),
                "engineVersion": ENGINE_VERSION,
            }
        )
    except ContractError as exc:
        return _json_contract_error(exc)
    except Exception as exc:
        return _json_internal_error(exc, context="Benchmark init request")


@app.route("/api/benchmark/run", methods=["POST"])
def api_benchmark_run():
    global benchmark_last_replay_dir
    global benchmark_last_out_dir
    global benchmark_last_expert_csv
    global benchmark_last_manual_override_csv
    global benchmark_last_trial_csv
    global benchmark_last_residual_csv
    global benchmark_last_param_template
    global benchmark_last_manifest

    body = _request_json_object(required=False)
    replay_dir = body.get("replayDir", str(benchmark_last_replay_dir or REPLAY_DIR))
    out_dir = body.get("outDir", str(benchmark_last_out_dir or BENCHMARK_DEFAULT_OUT_DIR))
    expert_csv = body.get("expertCsv", benchmark_last_expert_csv)
    manual_override_csv = body.get("manualOverrideCsv", benchmark_last_manual_override_csv)
    trial_csv = body.get("trialCsv", benchmark_last_trial_csv)
    residual_csv = body.get("residualCsv", benchmark_last_residual_csv)
    param_template = body.get("paramTemplate", benchmark_last_param_template)
    options = body.get("options", {})
    if not isinstance(options, dict):
        options = {}

    if not benchmark_lock.acquire(blocking=False):
        return _json_error(
            "Benchmark is already running",
            status=409,
            code="benchmark_busy",
        )
    try:
        result = _run_benchmark(
            replay_dir=replay_dir,
            out_dir=out_dir,
            expert_csv=expert_csv,
            manual_override_csv=manual_override_csv,
            trial_csv=trial_csv,
            residual_csv=residual_csv,
            param_template=param_template,
            options=options,
        )
        benchmark_last_replay_dir = Path(result["replayDir"])
        benchmark_last_out_dir = Path(result["outDir"])
        benchmark_last_expert_csv = _safe_benchmark_path(result["templatePaths"].get("expertCsv"), None)
        benchmark_last_manual_override_csv = _safe_benchmark_path(result["templatePaths"].get("manualOverrideCsv"), None)
        benchmark_last_trial_csv = _safe_benchmark_path(result["templatePaths"].get("trialCsv"), None)
        benchmark_last_residual_csv = _safe_benchmark_path(result["templatePaths"].get("residualCsv"), None)
        benchmark_last_param_template = _safe_benchmark_path(result["templatePaths"].get("paramTemplate"), None)
        benchmark_last_manifest = {
            "runAt": _utc_now_iso(),
            "pipelinePassed": bool(result.get("pipelinePassed", False)),
            "pipelineFailures": list(result.get("pipelineFailures", [])),
            "pipeline": result.get("pipeline", []),
        }
        return jsonify(
            {
                "ok": True,
                **result,
                "scripts": _benchmark_script_manifest(),
                "requestId": _request_id(),
                "engineVersion": ENGINE_VERSION,
            }
        )
    except ContractError as exc:
        return _json_contract_error(exc)
    except subprocess.TimeoutExpired:
        return _json_error(
            "Benchmark timeout",
            status=504,
            code="benchmark_timeout",
            details=["Process exceeded 10 minutes timeout"],
        )
    except Exception as exc:
        return _json_internal_error(exc, context="Benchmark run request")
    finally:
        benchmark_lock.release()


@app.route("/api/benchmark/file/<path:filename>", methods=["GET"])
def api_benchmark_file(filename):
    try:
        out_dir = _safe_benchmark_path(request.args.get("outDir"), benchmark_last_out_dir or BENCHMARK_DEFAULT_OUT_DIR)
        if out_dir is None:
            return _json_error("Invalid benchmark path", status=400, code="benchmark_path_invalid")
        safe_name = _validate_benchmark_filename(filename)
        if not safe_name:
            return _json_error("Invalid benchmark filename", status=400, code="validation_error")
        target = (out_dir / safe_name).resolve()
        if out_dir.resolve() not in target.parents and target != out_dir.resolve():
            return _json_error("Invalid benchmark filename", status=400, code="validation_error")
        if not target.exists() or not target.is_file():
            return _json_error("Benchmark file not found", status=404, code="not_found")
        return send_file(str(target), as_attachment=False, download_name=Path(safe_name).name)
    except ContractError as exc:
        return _json_contract_error(exc)
    except Exception as exc:
        return _json_internal_error(exc, context="Benchmark file request")


def _frontend_build_dir():
    raw = str(os.getenv("PROBEPLANNER_STATIC_DIR", "")).strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path(__file__).resolve().parents[1] / "build").resolve()


def _looks_like_unity_brotli(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            head = fh.read(256)
        return b"UnityWeb Compressed Content (brotli)" in head
    except Exception:
        return False


def _apply_frontend_static_headers(response, path: Path):
    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix == ".html":
        response.headers["Cache-Control"] = "no-cache"
    elif suffix in {".js", ".css", ".map", ".png", ".ico", ".json", ".txt"}:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif suffix == ".unityweb":
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        if name.endswith(".wasm.unityweb"):
            response.headers["Content-Type"] = "application/wasm"
        elif name.endswith(".framework.js.unityweb"):
            response.headers["Content-Type"] = "application/javascript; charset=utf-8"
        elif name.endswith(".data.unityweb"):
            response.headers["Content-Type"] = "application/octet-stream"
        if _looks_like_unity_brotli(path):
            response.headers["Content-Encoding"] = "br"
            response.headers["Vary"] = "Accept-Encoding"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


@app.route("/", defaults={"asset_path": ""}, methods=["GET"])
@app.route("/<path:asset_path>", methods=["GET"])
def serve_frontend(asset_path):
    if str(asset_path or "").startswith("api/"):
        return _json_error("API endpoint not found", status=404, code="not_found")

    build_dir = _frontend_build_dir()
    index_file = build_dir / "index.html"
    if not index_file.exists():
        log.warning("Frontend build missing requestId=%s: %s", _request_id(), index_file)
        return _json_error(
            "Frontend build not found",
            status=404,
            code="frontend_build_missing",
            details=["Run the frontend build or set PROBEPLANNER_STATIC_DIR to a valid build directory."],
        )

    if asset_path:
        candidate = (build_dir / asset_path).resolve()
        if (candidate == build_dir or build_dir in candidate.parents) and candidate.is_file():
            if candidate.suffix.lower() == ".map" and not SERVE_SOURCE_MAPS:
                return _json_error(
                    "Source map serving is disabled",
                    status=404,
                    code="source_map_disabled",
                )
            try:
                return _apply_frontend_static_headers(send_file(str(candidate)), candidate)
            except Exception as exc:
                return _json_internal_error(exc, context="Frontend static asset request")

    try:
        return _apply_frontend_static_headers(send_file(str(index_file)), index_file)
    except Exception as exc:
        return _json_internal_error(exc, context="Frontend index request")


if __name__ == "__main__":
    log.info("=" * 60)
    log.info("BCI Probe Optimizer Backend  [v4 + Allen Mesh Proxy]")
    log.info("Fixes applied:")
    log.info("  [FIX-1] sample_trajectory_voxels: AP direction corrected")
    log.info("  [FIX-2] load_atlas: duplicate stub removed")
    log.info("  [FIX-3] B&B reduction: diversity-aware candidate selection")
    log.info("New features:")
    log.info("  [NEW-1] GET /api/allen_mesh/<id>   ?Allen CCFv3 mesh proxy")
    log.info("  [NEW-1] POST /api/allen_mesh/batch  ?batch preload")
    log.info("=" * 60)

    _ensure_atlas_loader(force_retry=True)

    host = str(os.getenv("PROBEPLANNER_HOST", "127.0.0.1")).strip() or "127.0.0.1"
    try:
        port = int(str(os.getenv("PROBEPLANNER_PORT", "5000")).strip() or "5000")
    except Exception:
        port = 5000

    log.info(f"Server starting at http://localhost:{port}")
    log.info("Atlas loading in background (first run ~2 min for download)")
    log.info("Poll GET /api/status until atlasLoaded=true before optimising")
    log.info("=" * 60)

    app.run(
        host=host,
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True,
    )
