# Compatibility layer plan

The existing Flask API should remain stable while compute moves out of
`optimizer_server.py`.

## Step 1: native extension

Expose the Rust kernels through PyO3 or a local engine process, but keep
`/api/optimize` request and response shapes unchanged.

Initial replacement order:

1. `sample_trajectory_voxels`
2. trajectory profile and overlap calculation
3. SDF trilinear sampling
4. candidate conflict matrix

## Step 2: local engine service

Run `planner-engine` as a resident local service:

- preload atlas, SDF, and region index once
- expose `/healthz`, `/v1/optimize`, `/v1/tasks/:id`, and `/v1/tasks/:id/cancel`
- stream progress over server-sent events or a task status endpoint
- keep Flask as a compatibility adapter for the current UI

## Step 3: UI host

Only after compute gates pass, decide whether the UI stays browser-based,
moves to Tauri/Wails, or shifts the 3D-heavy workflow to Unity Native.
