# Project map

```text
apps/ui
  React UI and Unity WebGL host.

apps/backend-compat
  Flask API compatibility layer copied from the original app.
  The paper:start / backend:start npm commands explicitly initialize the
  paper Python optimizer from a hash-bound prepared profile.
  Native-engine comparisons are a separate algorithm/runtime, not a fallback.

planner-engine
  Rust native core and local HTTP task service.
  Owns trajectory sampling, SDF sampling, bitset coverage, conflict matrix,
  and solver kernels.

bindings/python
  PyO3 native extension for phase-1 direct hot-path replacement inside Flask.

compat/python
  Adapter examples for Flask to call the native extension or engine service.

apps/perf-gate
  Fixed replay benchmark and 3D FPS/load gate.
```

Supported paper flow in this unreleased revision (activate the locked Python
environment and install the UI npm lock first):

```powershell
npm --prefix apps/ui ci
npm run ui:build
npm run backend:test
npm run paper:start -- --profile "<prepared-profile>" --run-dir "<new-run-directory>" --static-dir apps/ui/build --port 5039
```

The UI and API share `http://127.0.0.1:5039`. Runtime identity is exposed at
`/api/paper/runtime`. Full initialization and scientific scope are defined in
[the paper runtime contract](PAPER_RUNTIME_AND_MRI_EVIDENCE.md).

Independent native tooling still requires Windows C++ Build Tools and the Windows
SDK on Windows; it is not the current paper Python runtime:

```powershell
npm run rust:check
npm run engine:start -- -ArtifactRoot $env:PROBEPLANNER_ARTIFACT_ROOT
```

The legacy combined `dev:stack` and `desktop:stack` routes require a launcher
deleted by security software. They fail before any process-stop/start operation
while it is absent. Old PowerShell flags are not accepted by `paper:start`; do not
restore or whitelist the blocked file to run a comparison. Use a separately bound,
explicit comparison contract rather than assuming Python/native equivalence.
