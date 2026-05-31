# ProbePlanner Native Upgrade

This independent workspace keeps the current React/Flask app as the compatibility
surface while moving the performance-critical planning path toward a native
planner engine.

## Layout

- `apps/ui`: migrated React UI and Unity WebGL host.
- `apps/backend-compat`: migrated Flask compatibility API.
- `apps/perf-gate`: repeatable replay benchmark and 3D performance gate.
- `planner-engine`: Rust native core skeleton for trajectory, SDF, bitset, and solver kernels.
- `bindings/python`: PyO3 native extension entrypoint for phase-1 Flask hot-path replacement.
- `compat/python`: Flask-side client and adapter examples for phase-1/phase-2 integration.
- `contracts`: request/response contracts shared by the compatibility layer and engine.
- `docs`: architecture notes and migration sequence.
- `docs/artifact-packaging.md`: artifact root, CI runner, and acceptance gate contract.

## First commands

```powershell
npm install
npm run ui:install
npm run prepare:cases -- --source-project ..\probe-planner
npm run ui:build
npm run perf:baseline -- --backend-url http://127.0.0.1:5000 --frontend-url http://127.0.0.1:3000 --runs 3 --out reports\baseline --write-baseline
npm run perf:gate -- --backend-url http://127.0.0.1:5000 --frontend-url http://127.0.0.1:3000 --runs 3 --baseline reports\baseline\perf-baseline.json --out reports\latest
```

The benchmark runner expects the existing backend and frontend to be started
outside this workspace. It does not mutate `../probe-planner`.

## Native acceptance

Native planning defaults to strict atlas targets. Requests with non-atlas-backed
targets fail closed unless they explicitly opt into demo fallback coverage.
DRCC MISOCP is not silently downgraded in the native engine; supported native
solver modes are `exact_pool`, `hybrid`, `heuristic`, and `cvar_relax`.

See `docs/artifact-packaging.md` for the artifact layout, self-hosted CI runner
variables, backend gate, SDF adversarial gate, 3D gate, and MRI registration QC
gate.
