"""Separate historical QC diagnostics from hash-bound measurement evidence."""

import math


METRIC_KEYS = ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio")
PROXY_TOKENS = ("proxy", "center_offset", "placeholder", "constant", "rigid_affine_no_deformable")
ROLES = {
    "dice": ("moving_mask", "transformed_moving_mask", "fixed_mask", "forward_transform"),
    "hd95Mm": ("moving_mask", "transformed_moving_mask", "fixed_mask", "forward_transform"),
    "treMm": ("moving_landmarks", "transformed_moving_landmarks", "fixed_landmarks", "forward_transform"),
}


def finite_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def mapping(value):
    return value if isinstance(value, dict) else {}


def same_reported_value(left, right):
    left, right = finite_number(left), finite_number(right)
    return left is not None and right is not None and round(left, 6) == round(right, 6)


def assess_registration_metrics(metrics, sources, records, verified_hashes=None, jacobian=None):
    """Only callers that actually verified required artifacts may supply hashes.

    Declarations describe measurement provenance, not animal-level accuracy.
    Legacy values remain untouched in the caller's compatibility fields.
    """
    verified_hashes = verified_hashes or {}
    assessment, measured, intervals = {}, {}, {}
    for key in METRIC_KEYS:
        value = finite_number(metrics.get(key))
        source = str(sources.get(key) or "unverified").lower()
        record = mapping(records.get(key))
        explicit = mapping(record.get("metricEvidence")).get(key)
        if explicit and not any(token in source for token in PROXY_TOKENS):
            source = str(explicit).lower()
        proxy = any(token in source for token in PROXY_TOKENS)
        declaration = mapping(mapping(record.get("metricValidation")).get(key))
        usable = False
        if key == "jacobianAnomalyRatio":
            usable = bool(
                jacobian and jacobian.get("realJacobianEvidence") is True
                and verified_hashes.get("jacobian")
                and same_reported_value(jacobian.get("anomalyRatio"), value)
            )
        elif declaration and not proxy:
            bindings = mapping(declaration.get("artifacts"))
            roles_bound = all(
                isinstance(bindings.get(role), dict)
                and bindings[role].get("sha256")
                and verified_hashes.get(bindings[role].get("key")) == bindings[role]["sha256"]
                for role in ROLES[key]
            )
            usable = roles_bound and same_reported_value(declaration.get("value"), value)
            if key == "treMm":
                usable = bool(usable and record.get("anatomicalTreAvailable") is True
                              and declaration.get("method") == "held_out_anatomical_landmarks"
                              and declaration.get("usedForRegistration") is False)
            else:
                usable = bool(usable and declaration.get("method") == "transformed_moving_mask_comparison")
        usable = bool(usable and value is not None and not proxy)
        assessment[key] = {
            "status": "measurement_bound" if usable else "unavailable" if value is None else "legacy_proxy" if proxy else "unverified_report",
            "source": source,
            "usableAsMeasurement": usable,
            "reportedValue": value,
        }
        measured[key] = value if usable else None
        ci = mapping(record.get("metricCi95")).get(key)
        if usable and declaration.get("ciMethod") == "bootstrap" and isinstance(ci, (list, tuple)) and len(ci) == 2:
            bounds = [finite_number(item) for item in ci]
            if all(item is not None for item in bounds) and bounds[0] <= bounds[1]:
                intervals[key] = bounds
    return {
        "metricAssessment": assessment,
        "measurementMetrics": measured,
        "measurementCi95": intervals,
        "anatomicalTreAvailable": assessment["treMm"]["usableAsMeasurement"],
        "measurementEvidenceComplete": all(row["usableAsMeasurement"] for row in assessment.values()),
        "metricsScope": "legacy_diagnostics_not_validation",
        "measurementScope": "same_profile_artifact_bound_measurements_not_subject_surgical_validation",
        "wholeVolumeFoldFreeEstablished": False,
    }
