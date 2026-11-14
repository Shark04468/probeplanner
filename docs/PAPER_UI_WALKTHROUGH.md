# Configure, inspect and export a paper plan

This walkthrough describes actual controls in `v0.3.0-paper`, commit
`56868d57c23cbbb1207666f5e420bf47019591c6`. It is a later documentation addendum,
not a new application release or an independent usability study. The published
tag and its experimental evidence remain unchanged.
Run the original published tag, not this documentation-revision branch, when
reproducing the paper hashes. Docstring-only edits still change source bytes.

## Start the matching application

Follow [v0.3.0 reproduction](release/v0.3.0-reproduction.md) to install the pinned
environment, verify the public profile, build the interface and start the paper
Python service. Open the loopback address printed by that launcher. The native
Rust example in the README is a separate workflow, not the paper backend.

Check `/api/paper/runtime` against the release identities before using a result.
The frozen optimizer SHA-256 is
`5439589a385d161a457cc4899e89cdc999712798c8dd4d8440a1c4657144a0f9`.
If the backend is offline, the atlas is unavailable or hashes disagree, resolve
the reported problem rather than treating the display as a validated plan.

## Select targets and settings

1. Choose **EN** for the English control labels used here. In **Regions**, use
   **Search region name or acronym...** to find **CA1**, select the matching
   region, then repeat for **CA3**. Clear the search and confirm both targets
   are selected. A parent structure includes its descendant atlas labels.
2. In **Targeting Mode**, choose **Single hemisphere acceptable**. This requires
   each region on an acceptable side; it does not require both left and right
   copies. Bilateral mode instead creates a separate requirement for each side.
3. Set **Target endpoint policy** to **Tip inside target**. This constrains the
   distal tip, not the locations of active recording contacts.
4. Select **NP 2.0 single-shank (1-shank)** in the probe-model control. The preset
   has one shank and a 10-mm maximum depth. Some preset text remains bilingual
   in the released interface.
5. For the archived S1 example, **Min clearance** is **1000 um** and **Max angle**
   is **15 degrees**. Turn **Reference stability** **Off** and start without a
   previous plan. The complete recorded request, not these few visible values
   alone, defines the reproducibility contract.
6. Select **Run Optimization** once. Wait for the request to finish. Do not
   replace a timeout, incomplete plan or error with a successful screenshot
   from a different request.

These are example settings, not a recommendation for an individual implantation.
Changing regions, thresholds, profile or software changes the evaluated request.

## Inspect the result

Open **Results** and inspect the coverage summary, requested targets and selected
probe count. Expand the probe row to inspect insertion depth, entry/tip CCF
coordinates, angles, containment, endpoint status and distal overshoot.

The archived S1 request `6853d8ef492d4f31a83206d025d46b60` returned one probe,
two of two target requirements and 5.500-mm insertion depth. These are recorded
example results, not a guarantee for a new request. Its optimizer identity is
the frozen hash above. It displayed **Review required** and **Internal-risk
evidence: Unavailable**; neither status should be relabelled as surgical safety.

Inspect **Unified trajectory audit** and **Reproducibility identifiers**. The
audit tests stated software predicates using represented atlas data. Missing
vessel, ventricle or other risk masks provide no evidence that those structures
are avoided. Anatomical trajectory coverage is not a count of recording neurons
or proof of active-contact coverage.

## Inspect the 3D view

Open the 3D view, select the CCF+MRI template mode and use the available camera
and slice controls. Wait for the requested target fills, atlas outlines and MRI
section to load before interpreting or capturing the scene. A nonblank canvas
alone is insufficient: the section, surfaces and targets must be present.

The horizontal slice/contact mode preserves contacted target-side geometry while
slicing other displayed tissue. Display clipping does not change the selected
trajectory or its audit. Imported geometry is explicitly not revalidated.

The MRI is a population-template display, not this animal's MRI. A visually
plausible overlay does not validate local CA2/cerebellar registration, skull
landmarks, native-space safety or histological implantation accuracy. Incomplete
mesh loading, missing sections and GPU errors remain failures, not accepted
substitutes for the complete CCF+MRI scene. End-to-end macOS operation is not
established by the current qualification attempts.

## Export and retain identities

1. Open **I/O**, then **Export**.
2. Choose **.pplan (JSON)** and select **Download .pplan**.
3. Choose **.txt** and select **Download .txt** to obtain the text representation.
4. Retain the request, response, runtime/profile identities and exported files
   together. The text header declares its coordinate convention; do not
   interpret its AP sign as identical to the viewer's AP label without reading
   that header. Exported geometry is not validation of an external holder or
   stereotaxic-device coordinate transform.

The actual S1 export reconciliation recorded a maximum 0.048-um tip-coordinate
serialization difference. This checks numeric conversion, not anatomical TRE.
An import round trip similarly does not create a new planning or safety result.

## Evidence boundary and recovery

- The [public evidence guide](release/v0.3.0-public-evidence.md) links the
  version-matched evidence routes; Supplementary Figure S1 documents the real
  configuration, view, inspection and export sequence.
- If **Run Optimization** is disabled, inspect backend/atlas readiness. Do not
  fabricate a plan by editing browser state or importing an unrelated outcome.
- If the request completes but the scene is incomplete, retain its result and
  diagnose rendering separately. Do not repeatedly submit the same expensive
  request just to obtain a screenshot.
- A feasible atlas plan does not certify holders, headstages, surgical access,
  individual anatomy, bregma/lambda or implantation accuracy.
