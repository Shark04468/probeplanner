"""Request schema guardrails for backend API."""

from __future__ import annotations

from typing import Dict, List


def validate_optimize_schema(body: object) -> List[str]:
    errs: List[str] = []
    if not isinstance(body, dict):
        return ["requestBody must be an object"]

    target_keys = body.get("targetKeys")
    if not isinstance(target_keys, list) or not target_keys:
        errs.append("targetKeys must be a non-empty array")
    elif any(not isinstance(k, str) or not k.strip() for k in target_keys):
        errs.append("targetKeys items must be non-empty strings")

    params = body.get("params")
    if params is not None and not isinstance(params, dict):
        errs.append("params must be an object")

    probe_params = body.get("probeParams")
    if probe_params is not None and not isinstance(probe_params, dict):
        errs.append("probeParams must be an object")

    modes = body.get("constraintModes")
    if modes is not None and not isinstance(modes, dict):
        errs.append("constraintModes must be an object")

    correction = body.get("correctionFactors")
    if correction is not None and not isinstance(correction, dict):
        errs.append("correctionFactors must be an object")

    correction_unc = body.get("correctionUncertainty")
    if correction_unc is not None and not isinstance(correction_unc, dict):
        errs.append("correctionUncertainty must be an object")

    clinical = body.get("clinicalContext")
    if clinical is not None and not isinstance(clinical, dict):
        errs.append("clinicalContext must be an object")

    risk_thresholds = None
    if isinstance(params, dict):
        risk_thresholds = params.get("riskThresholdsMm")
    if risk_thresholds is not None and not isinstance(risk_thresholds, dict):
        errs.append("params.riskThresholdsMm must be an object")

    return errs


RISK_THRESHOLD_KEYS = (
    "default",
    "vessel",
    "ventricle",
    "sulcus",
    "skull",
    "cortex_outside",
    "brain_surface",
    "drilling_surface",
)


def sanitize_risk_thresholds(raw: object, default_mm: float) -> Dict[str, float]:
    out: Dict[str, float] = {"default": float(default_mm)}
    if raw is None:
        return out
    if not isinstance(raw, dict):
        return out
    for key in RISK_THRESHOLD_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        try:
            num = float(value)
        except Exception:
            continue
        out[key] = max(0.0, min(10.0, num))
    return out
