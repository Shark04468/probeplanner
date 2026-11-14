# Bounded resource recovery before the same graphics diagnosis

Previous run43554847226 is terminalFAIL. It did not launch a browser. Both
artifact archives have verified hashes. Backend112and frontend152passed.
Resource preparation stopped after2652/3264responses: GET
/api/allen_mesh_lod/816?lod=0 returnedHTTP500. The backend log atlines2645-2723
records upstream TLS ConnectionResetError54. The same region's later lod1,lod2
and base requests succeeded. No hash mismatch or disk shortage was observed.

Change only the external preparation helper: allow at most2GETopen attempts for
HTTP500/502/503/504before any body sink is created. Wait1second, retain response
status, headers, bounded error body plus its SHA and timestamps. All response
length, SHA, requestedLOD/structure and deadline gates remain. No retries on
identity, header, body/hash,403,404,429, disk or deadline failures. Preserve the
previous failed run and disclose any recovered request; do not call it a flawless
first attempt or cold-delivery result. No rate-limit workaround.

The same helper-level90second per-response and1500second preparation deadlines,
4workers,6GBreserve,3264response inventory and all hashes remain unchanged.
The graphics observer, original frozen application, plan, profile, browser
300second observation/900second budget and original tests are unchanged.

One new actual Mac run after focused retry tests and full helper regression.
Use separate branch evidence/macos-graphics-recovery-20261114. Do not create a
paper release or merge. If collection fails, preserve it and diagnose the
specific failure; no automatic workflow rerun. Do not claim fullMac/anatomyPASS.
