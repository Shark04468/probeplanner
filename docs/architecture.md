# Native planner architecture

## Target route

```text
React UI or future Tauri/Wails shell
        |
local HTTP/gRPC compatibility API
        |
planner-engine service
        |
mmap atlas + SDF + region index + candidate and solver kernels
```

## Migration order

1. Keep the existing Flask and React application running unchanged.
2. Use `apps/perf-gate` to freeze replay cases and record current baseline metrics.
3. Move hot kernels into `planner-engine`:
   - trajectory voxel sampling
   - trajectory profile and region overlap
   - SDF trilinear sampling
   - candidate conflict matrix
   - greedy/exact solver kernels over bitsets
4. Add a Python compatibility adapter later with PyO3 or a local engine service.
5. Once engine metrics pass the gate, route `/api/optimize` through the engine.
6. Only revisit Tauri/Wails or Unity Native after backend compute is no longer the bottleneck.

## Implemented entrypoints

- Phase 1 native extension: `bindings/python` exposes PyO3 wrappers for trajectory sampling, SDF sampling, and conflict pairs.
- Phase 1 Python adapter: `compat/python/native_kernel_adapter.py` keeps return types plain enough to replace local Flask hot blocks.
- Phase 2 service: `planner-engine-http` exposes `/healthz`, `/v1/optimize`, `/v1/tasks`, `/v1/tasks/{taskId}`, and `/v1/tasks/{taskId}/cancel`.
- Measurement gate: `apps/perf-gate` keeps the original Flask/React app measurable while engine migration proceeds.

## Boundary

This workspace is intentionally independent from `../probe-planner`. It can read
replay files during case preparation and call the running app over HTTP, but it
does not edit the current UI or Flask backend.
