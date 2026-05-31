# Project map

```text
apps/ui
  React UI and Unity WebGL host.

apps/backend-compat
  Flask API compatibility layer copied from the original app.
  Default path runs the Python optimizer.
  Engine-service mode forwards /api/optimize to planner-engine.

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

Primary local flow:

```powershell
npm run ui:install
npm run dev:stack
npm run perf:baseline -- --backend-url http://127.0.0.1:5000 --frontend-url http://127.0.0.1:3000 --runs 3 --out reports\baseline --write-baseline
```

Native engine flow after Windows C++ Build Tools and Windows SDK are installed:

```powershell
npm run rust:check
npm run engine:start
npm run backend:start -- --UseEngineService
```
