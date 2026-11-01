# ProbePlanner Desktop Shell

This is the desktop packaging boundary for the native upgrade. It reuses the existing React UI build and keeps planner-engine as a local service process.

Current acceptance still runs against the production static UI plus local engine/backend gates. The Tauri shell is intentionally thin:

- UI source: `apps/ui`
- Production assets: `apps/ui/build`
- Native engine: `planner-engine` via local HTTP on `127.0.0.1:7878`
- Compatibility backend: `apps/backend-compat` via local HTTP on `127.0.0.1:5000`

Build prerequisites:

```powershell
npm run ui:build
npm run desktop:check
scripts/start_desktop_stack.ps1 -ArtifactRoot $env:PROBEPLANNER_ARTIFACT_ROOT
cd apps\desktop-tauri\src-tauri
cargo build --release
```

Runtime process model:

- `scripts/start_desktop_stack.ps1` starts planner-engine, backend compatibility, and an optional static UI server with readiness polling.
- `scripts/stop_local_stack.ps1` stops the local ports used by the desktop stack.
- The Tauri binary is still a thin host. Installer packaging and sidecar-managed engine/backend startup remain separate release tasks; performance acceptance still runs against the production UI served from `apps/ui/build` so WebGL results are directly comparable to the browser baseline.
