# Paper runtime and MRI evidence contract

The paper validation uses the Python implementation, not the Rust service. In this
unreleased revision, `paper:start` and `backend:start` explicitly select this Python
implementation. A desktop launcher starting successfully does not establish version
or algorithm equivalence. Existing published tags are unchanged.

## Explicit local launch

Install `apps/backend-compat/requirements-lock-py310.txt` into a new Python 3.10 virtual environment using ordinary dependency resolution. Run `pip check` and the test suites. Build `apps/ui` with the committed npm lock file.

Prepare a new profile using `apps/backend-compat/scripts/prepare_paper_profile.py`. This preserves original metadata, copies unchanged image/mask/SDF files, and generates separately identified relative-path deployment metadata. It does not improve registration or create anatomical validation. Public redistribution of the input data requires a separate license audit.

From the repository root, using the tested Python environment:

```text
python apps/backend-compat/scripts/start_paper_backend.py --profile <prepared-profile> --run-dir <new-run-directory> --static-dir apps/ui/build --port 5039
```

Equivalent npm commands (using `python` from the activated tested environment):

```text
npm run paper:start -- --profile <prepared-profile> --run-dir <new-run-directory> --static-dir apps/ui/build --port 5039
npm run backend:start -- --help
npm --prefix apps/backend-compat start -- --help
npm run backend:test
```

The full current backend test command includes validation-script tests. Its count
may exceed the archived 112-test release; that historical result is not rewritten
or treated as a count target for the expanded revision suite. The original
`test_backend_compat.ps1` preserves its historical fixed-count contract and is not
the current npm test entry point.

`--static-dir` is optional for an API-only run. If supplied, it must contain an
existing built `index.html`. Invalid static paths, existing run directories and
occupied loopback ports fail before expensive atlas import or output creation.
No listener is killed. Choose a different port if occupied. The probe is only a
preflight, not an atomic reservation; a later bind failure remains a startup error.
`--check-only` does not open a listener and therefore does not require a free port.

The legacy `start_backend_compat.ps1` is absent after antivirus deletion; its
classification is unresolved. It is not restored or whitelisted. `dev:stack` and
`desktop:stack` are unavailable with that missing dependency and fail before their
old process-stop/start operations. They are not alternate paper entry points.
Old flags such as `-UseEngineService`, `-LegacyPythonBackend`, `-ArtifactRoot` or
`-KillExisting` are not accepted by the paper CLI; there is no silent native-engine
fallback. Independent Rust tooling is separate from the paper Python algorithm.

`--check-only` performs initialization and writes registration summary/QC snapshots without starting a listener. The run directory must not exist. The server binds only to loopback. Existing `PROBEPLANNER_API_TOKEN` authentication is retained; other inherited `PROBEPLANNER_*` configuration is replaced by the documented paper configuration. Token values are never included in the runtime report.

Startup verifies every file in `paper_profile.json`, rejects escaping paths, requires the backend manifest and path checks, loads the real atlas, and initializes all supplied ontology records using the same descendant/left-hemisphere centroid helper as the earlier validation harness. This is explicit data preparation, not a monkeypatch of the planner.

`runtime.json` and `/api/paper/runtime` identify the optimizer, launcher, ontology initialization script, profile manifest and ontology hashes, package versions, worker count and scope. The paper campaign must use this same runtime or demonstrate equivalent initialization before reporting comparable results.

## Multi-shank orientation convention

ProbePlanner positions shanks symmetrically around the recorded insertion centerline. The zero-spin lateral axis is CCF ML projected perpendicular to the insertion direction; for an ML-parallel insertion the fallback is `cross(direction, DV)`. `spin_deg` (legacy alias `spin`) is a right-handed rotation about entry-to-tip in CCF `(AP, DV, ML)` coordinates. A vertical DV insertion at +90 degrees rotates the positive lateral axis from ML toward AP. Pitch is measured between adjacent shank centerlines.

The viewer mapping `(ML, -DV, -AP)` reverses handedness, so Unity applies the negative angle around its insertion direction. Contact sampling and pairwise collision segments use the same rotated shank offsets. Contact-cache identity includes spin, and the contact response reports `physicalProbe.spinDeg` and `spinConvention`. Invalid or conflicting supplied spin values are rejected by the contact API. Unspecified spin remains zero; imports remain unvalidated geometry.

Current shanks are represented as cylinders, not detailed rectangular cross sections. The risk-layer diagnostic uses a conservative centerline hull radius rather than a rotated per-shank tissue model. These representations and a successful file round trip do not establish external Pinpoint hardware-pivot/convention equivalence, implantation accuracy or surgical safety. Any external use requires explicit coordinate, pivot and hardware mapping verification.

## Contact-side visualization contract

Contact sampling retains every sampled CCF side (`left`, `right`, `midline`) in `contactIntervals[].hemispheres` and the actual swept-sample counts in `hemisphereCounts`. The singular `hemisphere` remains the historical majority-of-depth-summary label for compatibility; it is not a complete side inventory. Region aggregates union the sampled sides. The minimum contact length filters contiguous region intervals, not independently thresholded per-side lengths. Counts are sampling counts, not tissue volumes or probabilities.

The UI passes these sampled sides to Unity without applying its optional LR label correction: physical CCF ML and mesh coordinates already share positive ML = positive viewer x. In slice/contact modes, only the sampled side of a contact region is exempt from slicing. Midline-only sampling does not exempt either whole hemisphere. Missing legacy side arrays confer no whole-side exception. Fill, outline and picking use the same fragment-side rule, and the explicit hemisphere filter still takes precedence. This display contract does not validate imported trajectories or anatomical registration.

## Historical diagnostics are not measurements

For compatibility, the registration-summary API still exposes old numeric fields in `metrics` and `metricCi95`. They may contain historical proxies or heuristic intervals. `metricsScope=legacy_diagnostics_not_validation` is explicit. They must not be used as anatomical validation, confidence intervals or calibrated surgical uncertainty.

The following old fallbacks are not measurements:

- norm of the initial registration center offset used as TRE;
- one minus an optimizer objective used as Dice;
- a fixed multiple of TRE used as HD95;
- a fixed +/-10 percent range used as a confidence interval;
- a zero Jacobian anomaly inferred only from absence of a deformable stage.

New consumers use `measurementMetrics`, `measurementCi95` and `metricAssessment`. Missing or unverified values are null, never zero. A declaration such as `anatomicalTreAvailable=true` or an evidence string such as `landmarks` alone is insufficient. A stored scalar TRE does not establish a median, maximum or axis-specific errors.

## Measurement provenance requirements

To expose a measured metric, its source JSON record must contain `metricValidation[metricKey]` with the reported `value`, `method` and role-to-artifact bindings. Each binding contains the artifact `key` and `sha256`. These must match required artifacts actually verified by the active manifest loader; absent, optional-unverified or mismatched hashes do not qualify.

- Dice/HD95: method `transformed_moving_mask_comparison`, with `moving_mask`, `transformed_moving_mask`, `fixed_mask` and `forward_transform` bindings. A mask freshly segmented from a warped intensity image cannot substitute for the original moving mask provenance.
- TRE: method `held_out_anatomical_landmarks`, `usedForRegistration=false`, explicit `anatomicalTreAvailable=true`, and `moving_landmarks`, `transformed_moving_landmarks`, `fixed_landmarks` and `forward_transform` bindings.
- Jacobian anomaly: the actual sampled volume result must match the reported value and its required `jacobian` artifact must pass hashing. A placeholder or affine-stage flag is insufficient.
- A confidence interval additionally needs a declared bootstrap method and finite ordered bounds. Historical automatically constructed ranges remain diagnostic only.

These declarations and hashes establish a software provenance contract, not an independent judgment that anatomical annotations are correct. Their scientific origin and measurement implementation require separate review. No new landmark annotations or measurement declarations were manufactured for the population template.

The current Jacobian summary samples the volume. Its extrema and anomaly fraction are sampled statistics, not a proof of no fold anywhere in the volume (`wholeVolumeFoldFreeEstablished=false`). Automatically derived uncertainty sigmas remain explicitly marked heuristic sensitivity parameters, not empirically calibrated surgical uncertainty.

## QC and compatibility scope

The QC endpoint/export uses the measurement view and preserves prior values under `legacyDiagnostics`. An unavailable metric has a blank CSV value, a blank pass field and an explicit `evidenceStatus`. Missing evidence cannot yield overall PASS.

Internal reconstruction `passed` retains its historical role as a conservative technical planning gate; its explicit `gateScope` is not anatomical validation. Registration-summary and QC validation pass fields require complete measurement evidence. UI indicators use the explicit incomplete status rather than turning a technical pass into an anatomical pass.

Existing threshold violations are retained as conservative technical failures, including legacy diagnostics. This does not promote those diagnostics to scientific measurements. Historical files, test expectations and failure evidence remain unchanged.

Planning remains atlas/population-template software planning. Neither this runtime nor file integrity demonstrates subject-specific TRE, skull landmark accuracy, native surgical safety or histological placement accuracy.
# Atlas envelope contract

## Connection identity

Automatic browser connection discovers the backend's active atlas before reading the catalog; neither a default nor a stored browser preference requests a mode switch. Only an explicit user action sends a mode-change POST. Status queries do not change the active atlas, and posting its current mode does not reset a loaded atlas. The paper launcher records `atlasMode` with its profile identity; the mode endpoint rejects a different mode for that bound runtime. This is not a claim that every administrative mutation endpoint has been audited or that the development server is suitable for public hosting.

## Display mesh fidelity

The shared CCF-to-viewer mapping reverses handedness. The OBJ loader corrects whole-file winding using supplied normals, or reverses winding for standard OBJ without normals. It does not move vertices. No per-face repair is inferred from noisy MRI normals. The depth-weighting control adjusts ordinary alpha blending; it is not a verified order-independent transparency implementation.

New offline surface builds require `requirements-surface-build.txt` (VTK 9.5.2 in addition to the paper Python lock). The default `--lod-method preserve_topology` extracts the complete mask surface once, then uses `vtkDecimatePro` with topology and boundary preservation, splitting disabled and accumulated error. `--lod-steps 1,2,4` now specifies density factors for this method; the recorded extraction step is always one. The requested reductions may not be achieved. Explicit `--lod-method legacy_steps` reproduces the old coarse-grid extraction without silently substituting it when VTK is missing.

Builder metadata records source hashes, actual counts, controls and component/boundary/non-manifold edge statistics. Independent sampled point-to-triangle comparisons are additional display-fidelity checks, not continuous Hausdorff guarantees, anatomical TRE or registration evidence. Existing source defects are retained; no smoothing, hole filling, component deletion, mask changes or new skull landmarks are performed. Derived surfaces belong in a separately verified deployment profile, never overwriting archived surface manifests or inputs.

## Genuine envelope

The viewer's optional atlas envelope loads Allen structure 997 through the shared CCF mesh loader. It is not a procedural ellipsoid or subject-specific MRI surface. Its surface-state event records structure ID, source, requested and resolved LOD, visibility and failure. It uses the same hemisphere and three-axis clipping state as atlas regions. A missing mesh remains unavailable without fabricated anatomy.

The MRI profile's enabled brain surface suppresses this additional envelope. This does not remove the profile's brain, outer-tissue or skull layers. Readiness requires every requested surface to be both loaded and visible with a known actual LOD. Changing layer visibility is not registration validation. Existing mask provenance and anatomical evidence limitations remain unchanged.

## Actual MRI sections

`/api/mri/section` returns RGBA intensity/label sections and explicit world-plane geometry from the active profile's hash-verified inputs. The supported legacy grid is array `(AP,DV,ML)` at 25 um, with viewer coordinates `(ML-5700, -(DV-440), -(AP-5400))/1000` mm. Its direction-neutral NIfTI affine is checked, not silently canonicalized. This nominal atlas origin is not an individual bregma measurement.

MRI intensity is linearly interpolated normal to the section; CCF labels use nearest-neighbor sampling. All sections share a deterministic global nonzero-sample p1/p99 display window. Pixels quantized to black are transparent, while visible MRI outside the annotation remains visible; overlay also retains nonzero CCF labels. This display rule is not brain segmentation. The endpoint reports source hashes, manifest identity, indices, interpolation and raster-to-world geometry. Missing, changed or inconsistent inputs fail closed.

Below mode uses one actual plane and slab mode uses two bounding planes in the existing Unity scene. The viewer defers sampling until outline bounds finish loading, replays the normalized slice request against those completed bounds and propagates its physical center to all clipping consumers. Stale responses are rejected and pending/failed planes cannot report Ready. Active-axis coordinate labels use the acknowledged physical center, including snapping. A successful recovery at the same center verifies internal display persistence, not anatomical registration accuracy.

The QC checkerboard explicitly compares warped MRI intensity with CCF labels, not Allen template intensity. Boundary overlays contain genuine MRI and annotation edges. Jacobian QC uses its actual volume and does not replace a missing volume with a label image. These visual diagnostics do not supply missing independent masks or held-out anatomical landmarks.

