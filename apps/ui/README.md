# UI

This is the migrated React UI from the original project. It remains compatible
with the Flask API at `http://127.0.0.1:5000`.

Commands:

```powershell
npm --prefix apps/ui install
npm run ui:start
npm run ui:build
npm run ui:e2e
```

The UI no longer contains Flask backend source files; backend compatibility code
lives in `apps/backend-compat`.
