# Project map

```text
apps/ui
  React UI and Unity WebGL host.

apps/backend-compat
  Flask API compatibility layer copied from the original app.
  Default launcher path enables the engine service and forwards /api/optimize
  to planner-engine.
  The old Python optimizer is retained only behind -LegacyPythonBackend for
  historical comparison and unsupported MISOCP/CVX runs.

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

Primary local native flow:

```powershell
npm run ui:install
npm run dev:stack -- -ArtifactRoot $env:PROBEPLANNER_ARTIFACT_ROOT
npm run perf:baseline -- --backend-url http://127.0.0.1:5000 --frontend-url http://127.0.0.1:3000 --runs 3 --out reports\baseline --write-baseline
```

Explicit engine-service flow after Windows C++ Build Tools and Windows SDK are installed:

```powershell
npm run rust:check
npm run engine:start -- -ArtifactRoot $env:PROBEPLANNER_ARTIFACT_ROOT
npm run backend:start -- --UseEngineService
```

Legacy Python comparison flow:

```powershell
npm run dev:stack -- -ArtifactRoot $env:PROBEPLANNER_ARTIFACT_ROOT -LegacyPythonBackend
```
