"""Separate historical QC diagnostics from hash-bound measurement evidence."""

import math


METRIC_KEYS = ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio")
PROXY_TOKENS = ("proxy", "center_offset", "placeholder", "constant", "rigid_affine_no_deformable")
ROLES = {
    "dice": ("moving_mask", "transformed_moving_mask", "fixed_mask", "forward_transform"),
    "hd95Mm": ("moving_mask", "transformed_moving_mask", "fixed_mask", "forward_transform"),
    "treMm": ("moving_landmarks", "transformed_moving_landmarks", "fixed_landmarks", "forward_transform"),
    "jacobianAnomalyRatio": ("jacobian", "displacement_field", "fixed_image", "moving_image", "warped_mri"),
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


def artifacts_bound(declaration, roles, verified_hashes):
    bindings = mapping(declaration.get("artifacts"))
    return all(
        isinstance(bindings.get(role), dict)
        and isinstance(bindings[role].get("key"), str)
        and isinstance(bindings[role].get("sha256"), str)
        and bindings[role].get("sha256")
        and verified_hashes.get(bindings[role].get("key")) == bindings[role]["sha256"]
        for role in roles
    )


def jacobian_derivation_bound(record, declaration, verified_hashes, summary, value):
    """Check declared derivation, not numerical replay or anatomical accuracy."""
    summary = mapping(summary)
    run_id = record.get("registrationRunId")
    if not isinstance(run_id, str) or not run_id.strip() or declaration.get("runId") != run_id:
        return False
    required = {
        "method": "displacement_field_jacobian_determinant",
        "jacobianValueType": "determinant_not_log",
        "transformDirection": "fixed_to_moving",
        "transformComponents": "full_composite",
        "derivativeCoordinates": "physical",
    }
    if any(declaration.get(key) != expected for key, expected in required.items()):
        return False
    if not artifacts_bound(declaration, ROLES["jacobianAnomalyRatio"], verified_hashes):
        return False
    bindings = declaration["artifacts"]
    # These roles must identify the files the active profile actually displays/reads.
    if any(bindings[role].get("key") != role for role in ("jacobian", "warped_mri")):
        return False
    if len({bindings[role]["key"] for role in ROLES["jacobianAnomalyRatio"]}) != 5:
        return False
    if (summary.get("jacobianValueType") != "determinant_not_log"
            or summary.get("sampling") != "strided_whole_volume"
            or summary.get("anomalyBounds") != [0.2, 5.0]
            or summary.get("nonfiniteSampleCount") != 0):
        return False
    sampling = mapping(declaration.get("sampling"))
    for key in ("sampleCount", "totalVoxels", "sampleStride"):
        observed = summary.get(key)
        if type(observed) is not int or observed <= 0 or type(sampling.get(key)) is not int or sampling[key] != observed:
            return False
    if summary["sampleCount"] != (summary["totalVoxels"] - 1) // summary["sampleStride"] + 1:
        return False
    return (same_reported_value(declaration.get("value"), value)
            and same_reported_value(summary.get("anomalyRatio"), value))


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
            usable = jacobian_derivation_bound(record, declaration, verified_hashes, jacobian, value)
        elif declaration and not proxy:
            roles_bound = artifacts_bound(declaration, ROLES[key], verified_hashes)
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
