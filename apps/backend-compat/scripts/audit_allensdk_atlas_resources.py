from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_TARGETS = ["CA1", "VPM", "LP", "VISp"]
DEFAULT_REFERENCE_SPACE_KEY = "annotation/ccf_2017"
DEFAULT_RESOLUTION_UM = 25


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_array_content(arr: np.ndarray) -> str:
    h = hashlib.sha256()
    arr_c = np.asarray(arr)
    for idx in range(arr_c.shape[0]):
        slab = np.ascontiguousarray(arr_c[idx])
        h.update(slab.view(np.uint8))
    return h.hexdigest()


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_meta(meta: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (meta or {}).items():
        if isinstance(value, np.ndarray):
            out[key] = value.tolist()
        elif isinstance(value, (np.integer, np.floating)):
            out[key] = value.item()
        else:
            out[key] = value
    return out


def spacing_um_from_meta(meta: dict[str, Any]) -> list[float] | None:
    directions = meta.get("space directions")
    if directions is None:
        return None
    arr = np.asarray(directions, dtype=float)
    if arr.ndim != 2:
        return None
    return [round(float(np.linalg.norm(row)), 6) for row in arr]


def tree_records(tree: Any) -> list[dict[str, Any]]:
    records = tree.nodes()
    return [dict(r) for r in records]


def build_parent_child_maps(records: list[dict[str, Any]]) -> tuple[dict[int, int | None], dict[int, list[int]], dict[int, dict[str, Any]]]:
    by_id: dict[int, dict[str, Any]] = {}
    parent_by_id: dict[int, int | None] = {}
    children_by_id: dict[int, list[int]] = {}
    for rec in records:
        sid = int(rec["id"])
        by_id[sid] = rec
        path = [int(v) for v in rec.get("structure_id_path") or []]
        parent_id = path[-2] if len(path) >= 2 else None
        parent_by_id[sid] = parent_id
        if parent_id is not None:
            children_by_id.setdefault(parent_id, []).append(sid)
    for children in children_by_id.values():
        children.sort()
    return parent_by_id, children_by_id, by_id


def descendant_ids_from_paths(records: list[dict[str, Any]], sid: int) -> set[int]:
    out: set[int] = set()
    for rec in records:
        path = [int(v) for v in rec.get("structure_id_path") or []]
        if sid in path:
            out.add(int(rec["id"]))
    return out or {sid}


def load_region_map(path: Path) -> dict[str, int]:
    data = load_json(path, {})
    if isinstance(data, dict) and data.get("schemaVersion") == "region_map_v2":
        out: dict[str, int] = {}
        for entry in data.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            rid = entry.get("id")
            aliases = list(entry.get("aliases") or [])
            canonical = entry.get("canonicalAcronym")
            if canonical:
                aliases.append(canonical)
            for alias in aliases:
                if alias:
                    out[str(alias)] = int(rid)
        return out
    if isinstance(data, dict):
        return {str(k): int(v) for k, v in data.items()}
    if isinstance(data, list):
        out = {}
        for entry in data:
            if isinstance(entry, dict) and entry.get("acronym") and entry.get("id") is not None:
                out[str(entry["acronym"])] = int(entry["id"])
        return out
    return {}


def resolve_artifact_paths(artifact_root: Path) -> dict[str, Path | None]:
    manifest_path = artifact_root / "artifact_manifest.json"
    manifest = load_json(manifest_path, {})
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else {}

    def pick(name: str, fallback: str) -> Path | None:
        raw = (artifacts.get(name) or {}).get("path") if isinstance(artifacts, dict) else None
        if raw:
            return Path(raw)
        candidate = artifact_root / fallback
        return candidate if candidate.exists() else None

    return {
        "manifest": manifest_path if manifest_path.exists() else None,
        "annotation": pick("annotation", "annotation.npy"),
        "region_map": pick("region_map", "region_map.json"),
        "region_map_v2": pick("region_map_v2", "region_map_v2.json"),
    }


def default_artifact_root() -> Path | None:
    env = os.environ.get("PROBEPLANNER_ARTIFACT_ROOT") or os.environ.get("MRI_CCF_ARTIFACT_ROOT")
    if env:
        return Path(env)
    return None


def short_sha(value: str | None, n: int = 12) -> str:
    return value[:n] if value else "N/A"


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    sep = ["---"] * len(header)
    body = rows[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def build_markdown(report: dict[str, Any]) -> str:
    resources = report["resources"]
    integration = report["probePlannerIntegration"]
    targets = report["targetMapping"]
    status = report["status"]
    calls = ", ".join(report.get("allensdkCalls") or [])

    resource_rows = [
        ["Resource", "AllenSDK source/call", "Shape / spacing", "ProbePlanner check", "SHA-256"],
        [
            "AllenSDK package",
            f"Python import allensdk {report['environment'].get('allensdkVersion')}",
            f"Python {report['environment'].get('pythonVersion')}",
            "PASS" if status.get("allensdkAvailable") else "FAIL",
            "N/A",
        ],
        [
            "CCF template",
            "ReferenceSpaceCache.get_template_volume()",
            f"{resources['template'].get('shape')} / {resources['template'].get('spacingUm')} um",
            "Loaded through AllenSDK cache",
            short_sha(resources["template"].get("sha256")),
        ],
        [
            "Annotation volume",
            "ReferenceSpaceCache.get_annotation_volume()",
            f"{resources['annotation'].get('shape')} / {resources['annotation'].get('spacingUm')} um",
            f"artifact content hash match={integration.get('annotationContentHashMatch')}",
            short_sha(resources["annotation"].get("sha256")),
        ],
        [
            "Structure tree",
            "ReferenceSpaceCache.get_structure_tree()",
            f"{resources['structureTree'].get('recordCount')} records",
            f"region_map_v2 matches {integration.get('targetRegionMapMatches')}/{len(targets)} targets",
            short_sha(resources["structureTree"].get("sha256")),
        ],
        [
            "Planner annotation artifact",
            "artifact_manifest.json annotation",
            f"{integration.get('artifactAnnotationShape')} / {integration.get('artifactAnnotationDtype')}",
            f"array_equal={integration.get('annotationArrayEqual')}",
            short_sha(integration.get("artifactAnnotationFileSha256")),
        ],
        [
            "Planner region map",
            "region_map_v2.json or region_map.json",
            f"{integration.get('regionMapEntries')} entries",
            f"target mapping pass={status.get('targetMappingPass')}",
            short_sha(integration.get("regionMapSha256")),
        ],
    ]

    target_rows = [["Input acronym", "Allen ID", "Name", "Parent", "Children", "Planner region_map", "Annotation labels"]]
    for item in targets:
        target_rows.append(
            [
                item["inputAcronym"],
                str(item.get("allenId")),
                item.get("name") or "",
                f"{item.get('parentAcronym')} ({item.get('parentId')})",
                str(item.get("childCount")),
                "PASS" if item.get("regionMapMatch") else "FAIL",
                f"direct={item.get('directLabelPresent')}, descendants={item.get('presentDescendantLabelCount')}",
            ]
        )

    lines = [
        "# AllenSDK Atlas Resource Audit",
        "",
        f"- Generated at: `{report.get('generatedAt')}`",
        f"- Overall status: **{status.get('overall')}**",
        f"- AllenSDK calls: `{calls}`",
        f"- Reference space: `{resources.get('referenceSpaceKey')}` at {resources.get('resolutionUm')} um",
        f"- ProbePlanner artifact root: `{integration.get('artifactRoot')}`",
        "",
        "AllenSDK was used to obtain the CCF annotation volume, reference template, and anatomical structure tree. ProbePlanner audits these resources before planning by checking volume dimensions, voxel spacing, region identifiers, anatomical acronyms, and structure-tree parent-child mappings.",
        "",
        "**Table 1. AllenSDK-derived atlas resources used by ProbePlanner.**",
        "",
        markdown_table(resource_rows),
        "",
        "**Target acronym mapping audit.**",
        "",
        markdown_table(target_rows),
        "",
        "**Interpretation.**",
        "",
        "The audit passes when AllenSDK resources load successfully, the annotation/template metadata report the expected 25 um CCF grid, target acronyms resolve to Allen region IDs, the planner region map preserves those IDs, and the planner annotation artifact has the same content hash as the AllenSDK annotation array.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit AllenSDK atlas resources used by ProbePlanner.")
    ap.add_argument("--cache-dir", default=str(Path.home() / ".allensdk_cache"))
    ap.add_argument("--artifact-root", default="")
    ap.add_argument("--out", default="reports/paper-allensdk-atlas-audit")
    ap.add_argument("--evidence-dir", default="docs/evidence")
    ap.add_argument("--targets", nargs="+", default=DEFAULT_TARGETS)
    ap.add_argument("--resolution-um", type=int, default=DEFAULT_RESOLUTION_UM)
    ap.add_argument("--reference-space-key", default=DEFAULT_REFERENCE_SPACE_KEY)
    args = ap.parse_args()

    out_dir = Path(args.out)
    evidence_dir = Path(args.evidence_dir)
    cache_dir = Path(args.cache_dir).expanduser().resolve()
    artifact_root = Path(args.artifact_root).expanduser().resolve() if args.artifact_root else default_artifact_root()
    if artifact_root is None:
        raise RuntimeError("No artifact root found. Pass --artifact-root or set PROBEPLANNER_ARTIFACT_ROOT.")

    from allensdk.core.reference_space_cache import ReferenceSpaceCache

    allensdk_version = metadata.version("allensdk")
    rsc = ReferenceSpaceCache(
        resolution=int(args.resolution_um),
        reference_space_key=str(args.reference_space_key),
        manifest=str(cache_dir / "manifest.json"),
    )

    calls = [
        "ReferenceSpaceCache(...)",
        "get_annotation_volume()",
        "get_template_volume()",
        "get_structure_tree()",
    ]
    annotation, annotation_meta_raw = rsc.get_annotation_volume()
    template, template_meta_raw = rsc.get_template_volume()
    tree = rsc.get_structure_tree()
    records = tree_records(tree)
    parent_by_id, children_by_id, by_id = build_parent_child_maps(records)

    annotation_meta = normalize_meta(dict(annotation_meta_raw))
    template_meta = normalize_meta(dict(template_meta_raw))
    annotation_spacing_um = spacing_um_from_meta(annotation_meta)
    template_spacing_um = spacing_um_from_meta(template_meta)

    annotation_path = cache_dir / "annotation" / "ccf_2017" / f"annotation_{args.resolution_um}.nrrd"
    template_path = cache_dir / f"average_template_{args.resolution_um}.nrrd"
    structure_tree_path = cache_dir / "structures.json"

    annotation_content_sha = sha256_array_content(annotation)
    template_content_sha = sha256_array_content(template)
    present_labels = {int(v) for v in np.unique(annotation).tolist() if int(v) > 0}

    artifact_paths = resolve_artifact_paths(artifact_root)
    region_map_path = artifact_paths.get("region_map_v2") or artifact_paths.get("region_map")
    region_map = load_region_map(region_map_path) if region_map_path else {}

    artifact_annotation_shape = None
    artifact_annotation_dtype = None
    artifact_annotation_content_sha = None
    annotation_array_equal = False
    annotation_shape_match = False
    artifact_annotation_path = artifact_paths.get("annotation")
    if artifact_annotation_path and artifact_annotation_path.exists():
        artifact_annotation = np.load(artifact_annotation_path, mmap_mode="r")
        artifact_annotation_shape = list(artifact_annotation.shape)
        artifact_annotation_dtype = str(artifact_annotation.dtype)
        annotation_shape_match = tuple(artifact_annotation.shape) == tuple(annotation.shape)
        artifact_annotation_content_sha = sha256_array_content(artifact_annotation)
        annotation_array_equal = (
            annotation_shape_match
            and str(artifact_annotation.dtype) == str(annotation.dtype)
            and artifact_annotation_content_sha == annotation_content_sha
        )

    target_mapping: list[dict[str, Any]] = []
    for acronym in args.targets:
        recs = tree.get_structures_by_acronym([acronym])
        if not recs:
            target_mapping.append(
                {
                    "inputAcronym": acronym,
                    "mapped": False,
                    "regionMapMatch": False,
                    "directLabelPresent": False,
                    "presentDescendantLabelCount": 0,
                }
            )
            continue
        rec = dict(recs[0])
        sid = int(rec["id"])
        parent_id = parent_by_id.get(sid)
        parent = by_id.get(parent_id) if parent_id is not None else None
        descendants = descendant_ids_from_paths(records, sid)
        direct_present = sid in present_labels
        present_descendants = sorted(descendants.intersection(present_labels))
        mapped_region_id = region_map.get(acronym)
        target_mapping.append(
            {
                "inputAcronym": acronym,
                "mapped": True,
                "allenId": sid,
                "name": rec.get("name"),
                "parentId": parent_id,
                "parentAcronym": parent.get("acronym") if parent else None,
                "childCount": len(children_by_id.get(sid, [])),
                "descendantCount": len(descendants),
                "directLabelPresent": direct_present,
                "presentDescendantLabelCount": len(present_descendants),
                "regionMapId": mapped_region_id,
                "regionMapMatch": mapped_region_id == sid,
            }
        )

    target_region_map_matches = sum(1 for item in target_mapping if item.get("regionMapMatch"))
    target_mapping_pass = all(item.get("mapped") and item.get("regionMapMatch") for item in target_mapping)
    resource_pass = (
        list(annotation.shape) == [528, 320, 456]
        and list(template.shape) == [528, 320, 456]
        and annotation_spacing_um == [float(args.resolution_um)] * 3
        and template_spacing_um == [float(args.resolution_um)] * 3
        and len(records) > 0
    )
    integration_pass = bool(annotation_array_equal and target_mapping_pass and region_map)

    manifest_path = artifact_paths.get("manifest")
    report = {
        "generatedAt": utc_now(),
        "status": {
            "overall": "PASS" if resource_pass and integration_pass else "FAIL",
            "allensdkAvailable": True,
            "resourcePass": resource_pass,
            "integrationPass": integration_pass,
            "targetMappingPass": target_mapping_pass,
        },
        "environment": {
            "pythonExecutable": sys.executable,
            "pythonVersion": platform.python_version(),
            "platform": platform.platform(),
            "allensdkVersion": allensdk_version,
            "numpyVersion": metadata.version("numpy"),
        },
        "allensdkCalls": calls,
        "resources": {
            "referenceSpaceKey": args.reference_space_key,
            "resolutionUm": args.resolution_um,
            "manifestPath": str(cache_dir / "manifest.json"),
            "annotation": {
                "path": str(annotation_path),
                "shape": list(annotation.shape),
                "dtype": str(annotation.dtype),
                "spacingUm": annotation_spacing_um,
                "meta": annotation_meta,
                "sha256": sha256_file(annotation_path),
                "contentSha256": annotation_content_sha,
                "presentLabelCount": len(present_labels),
            },
            "template": {
                "path": str(template_path),
                "shape": list(template.shape),
                "dtype": str(template.dtype),
                "spacingUm": template_spacing_um,
                "meta": template_meta,
                "sha256": sha256_file(template_path),
                "contentSha256": template_content_sha,
            },
            "structureTree": {
                "path": str(structure_tree_path),
                "recordCount": len(records),
                "rootId": int(by_id.get(997, {}).get("id", 997)) if by_id else None,
                "sha256": sha256_file(structure_tree_path),
            },
        },
        "targetMapping": target_mapping,
        "probePlannerIntegration": {
            "artifactRoot": str(artifact_root),
            "artifactManifestPath": str(manifest_path) if manifest_path else None,
            "artifactManifestSha256": sha256_file(manifest_path) if manifest_path else None,
            "artifactAnnotationPath": str(artifact_annotation_path) if artifact_annotation_path else None,
            "artifactAnnotationShape": artifact_annotation_shape,
            "artifactAnnotationDtype": artifact_annotation_dtype,
            "artifactAnnotationFileSha256": sha256_file(artifact_annotation_path) if artifact_annotation_path else None,
            "artifactAnnotationContentSha256": artifact_annotation_content_sha,
            "annotationContentHashMatch": artifact_annotation_content_sha == annotation_content_sha,
            "annotationArrayEqual": annotation_array_equal,
            "annotationShapeMatch": annotation_shape_match,
            "regionMapPath": str(region_map_path) if region_map_path else None,
            "regionMapSha256": sha256_file(region_map_path) if region_map_path else None,
            "regionMapEntries": len(region_map),
            "targetRegionMapMatches": target_region_map_matches,
        },
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "atlas_audit.json"
    md_path = out_dir / "table1_allensdk_atlas_resources.md"
    csv_path = out_dir / "target_mapping.csv"
    write_json(json_path, report)
    md_path.write_text(build_markdown(report), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        fieldnames = [
            "inputAcronym",
            "allenId",
            "name",
            "parentAcronym",
            "parentId",
            "childCount",
            "descendantCount",
            "regionMapId",
            "regionMapMatch",
            "directLabelPresent",
            "presentDescendantLabelCount",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for item in target_mapping:
            writer.writerow({key: item.get(key) for key in fieldnames})

    date_prefix = datetime.now().strftime("%Y-%m-%d")
    evidence_md = evidence_dir / f"{date_prefix}-allensdk-atlas-audit.md"
    evidence_json = evidence_dir / f"{date_prefix}-allensdk-atlas-audit.json"
    evidence_md.parent.mkdir(parents=True, exist_ok=True)
    evidence_md.write_text(build_markdown(report), encoding="utf-8")
    write_json(evidence_json, report)

    print(f"status={report['status']['overall']}")
    print(f"json={json_path}")
    print(f"markdown={md_path}")
    print(f"evidence={evidence_md}")
    return 0 if report["status"]["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
