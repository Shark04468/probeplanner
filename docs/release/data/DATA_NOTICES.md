# Data attribution and conditions

These data are distinct from the MIT-licensed ProbePlanner source code. The
notices below accompany the population-template demonstration profile. No
subject-specific accuracy, surgical safety or histological accuracy is claimed.

## AMBMC

Source: AMBMC, The University of Queensland, symmetric average wild-type C57BL/6J
mouse MRI model v0.8. Source page: https://imaging.org.au/AMBMC/Model/
Original distribution: https://osf.io/download/g8p6a/

The file-level COPYING in the byte-verified original distribution is reproduced
in AMBMC_COPYING.txt. License: Creative Commons Attribution 3.0 Australia,
https://creativecommons.org/licenses/by/3.0/au/ . The OSF project-level "No
license" metadata is recorded separately and does not replace that bundled
file-level notice. Preserve attribution, the license notice and changes when
redistributing AMBMC-derived material; do not label it as MIT software.

Reference: Janke AL and Ullmann JF (2015). Robust methods to create ex vivo
minimum deformation atlases for brain mapping. Methods 73:18-26.
https://doi.org/10.1016/j.ymeth.2015.01.005

Acknowledgment: the C57BL/6J model was funded by National Health and Medical
Research Council Enabling Grant 436673.

Derived material in this profile includes the retained MRI resampling into CCF
space and MRI-related mask/surface/SDF artifacts. Those historical derivatives
are not newly registered or anatomically validated by this release packaging.
The complete original registration application chain is unavailable. The
public-copy operation changes path metadata and corresponding deployment hashes
only; scientific arrays and mesh bytes remain the evaluated bytes.

## Allen Institute

Source: Allen Institute, Allen Mouse Brain Common Coordinate Framework, CCFv3
2017 annotation at 25 micrometers. Deployed label integers are Allen ontology
IDs. Official annotation:
https://download.alleninstitute.org/informatics-archive/current-release/mouse_ccf/annotation/ccf_2017/annotation_25.nrrd

Use, copying, redistribution and derivatives of Allen content are subject to
the Allen Institute Terms of Use and Citation Policy, including the conditions
for research/noncommercial use. The MIT source-code license does not grant
additional rights in Allen data. Consult the current terms for other uses.

- https://alleninstitute.org/legal/terms-of-use
- https://alleninstitute.org/legal/citation-policy
- https://knowledge.brain-map.org/

This profile contains Allen annotation converted to NumPy storage, ontology/
region metadata and derived atlas brain surfaces/SDF representations. Preserve
source attribution and the applicable notices. No endorsement by the Allen
Institute, AMBMC or The University of Queensland is implied.

Historical registration metadata names a publication/transgenic-grid NRRD with
ordinal label indices. Its bytes and raw label integers differ from the CCF 2017
ontology-ID array. The separately recorded label-encoding reconciliation must
not be mistaken for MRI registration validation. Do not substitute the ordinal
array directly into the planner.

## Deployment And Provenance

The active runtime contract is paper_profile.json; artifact_manifest.json binds
the actual deployed artifacts. PUBLIC_DERIVATION.json lists original and public
file identities. Files under provenance/path_projected are historical metadata
copies with path strings redacted, not the original private bytes and not active
runtime contracts. Original frozen records remain retained separately.

The file named skull_mask.npy and related legacy surface/SDF channels retain
their historical names. This does not make them measured skull anatomy or
validated bregma/lambda landmarks. Risk masks unavailable in the evaluated
profile remain unavailable; packaging adds no safety evidence.
