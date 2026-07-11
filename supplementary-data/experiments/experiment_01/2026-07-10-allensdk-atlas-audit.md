# AllenSDK Atlas Resource Audit

- Generated at: `2026-07-10T05:42:39+00:00`
- Overall status: **PASS**
- AllenSDK calls: `ReferenceSpaceCache(...), get_annotation_volume(), get_template_volume(), get_structure_tree()`
- Reference space: `annotation/ccf_2017` at 25 um
- ProbePlanner artifact root: `${PROBEPLANNER_PROFILE_ROOT}`

AllenSDK was used to obtain the CCF annotation volume, reference template, and anatomical structure tree. ProbePlanner audits these resources before planning by checking volume dimensions, voxel spacing, region identifiers, anatomical acronyms, and structure-tree parent-child mappings.

**Table 1. AllenSDK-derived atlas resources used by ProbePlanner.**

| Resource | AllenSDK source/call | Shape / spacing | ProbePlanner check | SHA-256 |
| --- | --- | --- | --- | --- |
| AllenSDK package | Python import allensdk 2.16.2 | Python 3.10.20 | PASS | N/A |
| CCF template | ReferenceSpaceCache.get_template_volume() | [528, 320, 456] / [25.0, 25.0, 25.0] um | Loaded through AllenSDK cache | e4a2b483e842 |
| Annotation volume | ReferenceSpaceCache.get_annotation_volume() | [528, 320, 456] / [25.0, 25.0, 25.0] um | artifact content hash match=True | c620cbcc5621 |
| Structure tree | ReferenceSpaceCache.get_structure_tree() | 1327 records | region_map_v2 matches 4/4 targets | 0b176d98eea8 |
| Planner annotation artifact | artifact_manifest.json annotation | [528, 320, 456] / uint32 | array_equal=True | 8cd42d0f72ae |
| Planner region map | region_map_v2.json or region_map.json | 1327 entries | target mapping pass=True | f36f81b45a95 |

**Target acronym mapping audit.**

| Input acronym | Allen ID | Name | Parent | Children | Planner region_map | Annotation labels |
| --- | --- | --- | --- | --- | --- | --- |
| CA1 | 382 | Field CA1 | CA (375) | 4 | PASS | direct=True, descendants=1 |
| VPM | 733 | Ventral posteromedial nucleus of the thalamus | VP (709) | 0 | PASS | direct=True, descendants=1 |
| LP | 218 | Lateral posterior nucleus of the thalamus | LAT (138) | 0 | PASS | direct=True, descendants=1 |
| VISp | 385 | Primary visual area | VIS (669) | 6 | PASS | direct=False, descendants=6 |

**Interpretation.**

The audit passes when AllenSDK resources load successfully, the annotation/template metadata report the expected 25 um CCF grid, target acronyms resolve to Allen region IDs, the planner region map preserves those IDs, and the planner annotation artifact has the same content hash as the AllenSDK annotation array.
