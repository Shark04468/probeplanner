# Backend Compatibility Layer

This app is the migrated Flask API surface from the original project.

Default mode keeps the existing Python optimizer path:

```powershell
npm run backend:start
```

Native engine mode keeps the same `/api/optimize` request/response envelope but
forwards optimization work to `planner-engine`:

```powershell
npm run engine:start
npm run backend:start -- --UseEngineService
```

The switch is controlled by:

- `PROBEPLANNER_USE_ENGINE_SERVICE=1`
- `PROBEPLANNER_ENGINE_URL=http://127.0.0.1:7878`

The backend still owns legacy atlas endpoints, mesh proxy endpoints, replay
persistence, and frontend compatibility while compute kernels move into
`planner-engine`.
