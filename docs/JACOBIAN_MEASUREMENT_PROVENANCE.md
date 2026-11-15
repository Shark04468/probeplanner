# Jacobian measurement provenance

A readable, hash-verified determinant array is not automatically evidence for
the active MRI registration. A nonconstant array can belong to another run;
a valid identity/affine transform can have a constant determinant.

The legacy `metrics` and raw `jacobianSummary` remain diagnostic compatibility
fields. `measurementMetrics.jacobianAnomalyRatio` is populated only when the
caller verifies the reporting artifact and every referenced artifact and the
report declares the following `metricValidation.jacobianAnomalyRatio` contract:

- `runId` matches the nonempty reporting record's `registrationRunId`.
- `method` is `displacement_field_jacobian_determinant`.
- `jacobianValueType` is `determinant_not_log`; `derivativeCoordinates` is
  `physical`; `transformComponents` is `full_composite`.
- `transformDirection` is `fixed_to_moving`: the pull map on the fixed output
  grid, not an inferred meaning of a filename containing "forward".
- `artifacts` binds distinct manifest keys for `jacobian`, `displacement_field`,
  `fixed_image`, `moving_image` and `warped_mri` with their verified SHA-256 values.
  The determinant and displayed MRI must use the active canonical `jacobian`
  and `warped_mri` keys, not substitute verified files from the same directory.
- `sampling` contains integer `sampleCount`, `totalVoxels` and `sampleStride`
  matching the runtime whole-volume strided sample. No sampled nonfinite value
  is silently discarded for measurement acceptance. `value` matches the sampled
  anomaly ratio; the runtime diagnostic range remains [0.2, 5.0].

This checks a **hash-bound declared derivation**, not a mathematical replay of
the determinant computation or transform application. The runtime does not infer
correct alignment from these declarations. Missing/inconsistent declarations
leave the raw diagnostic intact but suppress the measurement, QC/CSV pass cell,
and frontend measurement summary. No synthetic declaration is added to any
historical profile to make it pass.

The sample cannot prove absence of folds between samples or throughout the full
volume; `wholeVolumeFoldFreeEstablished` remains false. Independent local
anatomy, transform replay, complete same-run outputs, and native-space planning
acceptance are separate requirements. No population template is individual MRI.

References: [SimpleITK 2.5 determinant definition](https://simpleitk.org/doxygen/v2_5/html/classitk_1_1simple_1_1DisplacementFieldJacobianDeterminantFilter.html)
and [W3C PROV-O derivation relationships](https://www.w3.org/TR/prov-o/).
