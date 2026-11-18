# Original-CCF MRI research inspection

This opt-in mode displays a separately reconstructed MRI in the actual maintained
ProbePlanner UI. It does not replace a paper profile, certify anatomical accuracy,
or enable native-space planning. Default paper launches remain unchanged.

## Launch

Use a verified paper profile for the CCF annotation and ontology, and a separate
research display package for the new MRI. The software does not bundle this
package. Use only a reconstruction you are authorized to access and retain
its source-data notices. Do not use the old paper MRI volume as a substitute.

From the repository root in the activated Python environment, install the
locked UI dependencies, build the interface, and start the preview:

```powershell
npm --prefix apps/ui ci
npm run ui:build
npm run paper:start -- --profile "<verified-paper-profile>" --research-display "<research-package>/display.json" --run-dir "<new-run-directory>" --static-dir apps/ui/build --port 5040
```

Build without a `REACT_APP_FLASK_BASE` override when the backend serves the UI;
the page will use the same local address as its API. Open
`http://127.0.0.1:5040`, expand **3D Brain View**, and move the section slider
into tissue. Wait until the region-loading indicator completes and the section
is visible. The viewer defaults to the MRI/CCF overlay; use its existing camera
and slice controls to inspect another plane. A high slice can legitimately lie
outside the MRI sampling domain.

After loading the atlas, the research launcher checks available memory. Below
3 GiB of headroom it serves mesh and MRI image responses one at a time, and the
UI explains the switch once per page. Page and status requests do not wait in
that queue. If memory headroom cannot be measured, the same limit applies
without claiming low memory. This policy is recorded as
`researchMeshLoading` in `runtime.json` and in `/api/status`. It does not
reduce the number of regions or downsample the MRI. If memory is exhausted later,
close other viewers and retry rather than using a partial scene.

To return to ordinary planning, start a separate session with the same command
without `--research-display` and choose a new run directory and an unused port.
The ordinary paper deployment profile and release tags are not modified.

The launcher checks that both profiles bind the same annotation bytes. It does
not allocate the old MRI-derived risk fields in research mode. Backend planning,
plan-export, reconstruction, profile-switching and archived-task endpoints are
blocked. Existing CCF mesh and contact inspection remain available. A displayed
CCF contact is not an MRI/anatomical or safety acceptance result.

The real UI labels the MRI as a research preview and disables Run. Old MRI-derived
brain, outer-tissue and skull surfaces are unavailable, not silently reused with
new MRI pixels. Ordinary screenshot/inspection exports are not surgical plans.

## Display contract

`schemaVersion = probeplanner.ccf_mri_research_display.v1` requires:

- `coordinateFrame = allen_ccf25_original_ras` and `purpose = research_display_only`.
- `planningEligible = false`, `anatomyAccepted = false`; no ambiguous `valid` key.
- `voxelSizeUm = 25`, an `atlasShape`, and a distinct `profileId`.
- SHA-256 bindings for `annotation`, `warped_mri`, `sampling_domain`, `jacobian`
  and `reconstruction_provenance`. Paths must stay inside the package directory.
- Unsigned/integer NPY labels and uncompressed read-only NIfTI volumes, in mm,
  with matching shape, slope 1, intercept 0 and this original-CCF RAS affine:

```text
  0       0       0.025   0
 -0.025   0       0       0
  0      -0.025   0       0
  0       0       0       1
```

The binary sampling domain must equal the finite MRI domain. It is **not** a brain
mask, skull mask or alignment-validation mask. The provenance record must bind
the same four data hashes and retain the false planning/anatomy flags.

Original volumes are not header-reoriented or resampled by the display reader.
Its AP/DV/ML index mapping produces the existing viewer's world-coordinate planes.
The atlas bregma offset is only the viewer's coordinate convention: it does not
establish this specimen's bregma, lambda or a stereotaxic calibration.

MRI interpolation requires valid contributing planes; zero-weight neighbors do
not erase valid integer planes because of floating-point coordinate roundoff.
Unknown samples remain transparent. CCF-only labels can remain visible outside
MRI support and must not be read as evidence that MRI coverage exists there.
Jacobian QC preserves missing values and nonpositive values explicitly.

## Integrity and interpretation

Hashes are checked when loading; bound files' fingerprints are checked on API
requests. A changed/missing artifact stops inspection requests without falling
back to an old MRI. Section responses identify the manifest, all five source
hashes, coordinate frame, interpolation, sampling coverage and research scope.
The research viewer's current profile and overlay identity are reported in
`registrationPreview` from `/api/status?includeRegions=0`.

Successful loading, unit tests, matched UI pixels and positive sampled Jacobians
are software/geometric checks, not independent local anatomical validation.
An author review is not independent validation. Missing support, external folds,
fine-label disagreement and missing compatible ground truth must be documented
for each reconstruction; they cannot be hidden by the display contract.

The launcher starts with a new cache. Existing Allen meshes may be imported into
that run's cache with an explicit hash/copy record. Such reuse is neither a new
network provenance check nor an MRI-derived surface. Failed downloads, exclusions
by the viewer's aggregation policy and incomplete streams remain distinguishable.

## Tests

`test_research_atlas_sections.py` covers frame, domain, interpolation, identity,
nonfinite support, hash changes and QC behavior; legacy section tests remain.
`test_research_preview.py` exercises real Flask request/response hooks and rejection
paths. `Brain3DView.research.test.jsx` checks the research badge and unavailable
MRI surface controls. Real-browser verification must separately wait for finished
mesh streaming and a loaded MRI section with the expected manifest hash.
