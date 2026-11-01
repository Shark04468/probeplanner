# Backend Compatibility Layer

This app is the migrated Flask API surface from the original project.

The current unreleased revision explicitly starts the paper Python optimizer with
a prepared, hash-bound profile. Activate the locked Python 3.10 environment first.
From the repository root:

```powershell
npm run backend:start -- --profile "<prepared-profile>" --run-dir "<new-run-directory>" --static-dir apps/ui/build --port 5039
npm run backend:test
```

From this backend directory, the same CLI is `npm start -- <arguments>` and the
full backend/validation suite is `npm test`. Paths are relative to the working
directory; quote paths containing spaces. The run directory must not already
exist. `--static-dir` is optional for an API-only run.

The former PowerShell launcher is absent following antivirus deletion, not
classified as a confirmed false positive. Do not restore or whitelist it. The
old combined native/dev/desktop launch paths are not supported paper workflows.
The paper CLI rejects old engine-service flags and replaces inherited engine
configuration; it never silently forwards planning to the Rust implementation.
See [the runtime contract](../../docs/PAPER_RUNTIME_AND_MRI_EVIDENCE.md).

The backend still owns legacy atlas endpoints, mesh proxy endpoints, replay
persistence, and frontend compatibility while compute kernels move into
`planner-engine`.
