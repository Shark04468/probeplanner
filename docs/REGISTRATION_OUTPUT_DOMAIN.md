# Registration Output-Domain Guard

The legacy SimpleITK registration helper optimizes on a coarse image and resamples
onto a full-resolution reference. Its B-spline must cover the latter. Rounding
the coarse image size does not necessarily preserve the final output voxel
centers. Outside its domain a SimpleITK B-spline evaluates as identity, so a
composite can abruptly lose the nonlinear component at a terminal plane.

`build_ambmc_true_outer_pipeline._register_ambmc_to_ccf` initializes the B-spline
from `fixed_full`, while keeping the original coarse optimization grid and
optimizer settings. It checks the eight physical voxel-center corners both
before optimization and on the optimized B-spline returned by SimpleITK.
Convexity of the B-spline domain makes this sufficient for output-grid coverage.
Origin, anisotropic spacing and direction are included in the check.

A failed coverage check raises `RegistrationDomainError`; it cannot silently
become an affine-only success. An ordinary optimizer failure retains the
existing affine fallback, with its reason in `deformableError`. A successful
deformable stage reports its domain geometry and coverage in `deformableDomain`.
Quick and explicitly nondeformable modes leave this field null.

The returned SimpleITK transform uses the resampler's pull convention: a fixed
output point maps to a moving input point. Its historical function/file names
do not establish a forward point-transform convention. Do not apply it directly
as moving-to-fixed landmark coordinates without handling that direction.

Run the focused tests with the imaging environment (SimpleITK 2.5.3 is present
in the historical Python 3.10 lock):

```powershell
python -m pytest apps/backend-compat/tests/test_registration_domain.py -v
```

The tests import the actual builder. They include the old-domain negative case
at CCF dimensions 528 x 320 x 456 and 25-um spacing, anisotropic/oblique geometry,
nonzero displacements on all faces, serialization, entry-point integration,
post-optimizer rejection and ordinary-failure reporting.

## Scope

This guard verifies geometry coverage only. It does not prove positive Jacobians,
correct anatomical orientation, local CA2/cerebellar alignment, independent TRE,
or surgical safety. It does not repair already generated volumes. The legacy
builder's intensity-derived outer/skull masks remain heuristic; this change does
not make them measured skull evidence or replace the MRI-intermediate route.

Any new scientific fit still requires its own source/transform hashes, same-run
QC, appropriate inverse mapping and independent local anatomical evaluation.
Historical failure records and existing figures must not be overwritten by a
synthetic engineering test or by this source change.

References: [SimpleITK spatial transformations](https://insightsoftwareconsortium.github.io/SimpleITK-Notebooks/Python_html/22_Transforms.html),
[B-spline transform domain API](https://simpleitk.org/doxygen/latest/html/classitk_1_1simple_1_1BSplineTransform.html).
