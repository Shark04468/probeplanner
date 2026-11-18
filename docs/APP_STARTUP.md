# Start and inspect the maintained application

This is the maintained application, not a replacement for the frozen
`v0.3.0-paper` release or its archived results. It uses the existing Python
planning engine. The optional Tauri and native-engine experiments are not
required to run it.

## Install and launch

Use Node.js 24.14.0 and Python 3.11 in a virtual environment. With that
environment active, run these commands from the repository root:

```sh
python -m pip install -r apps/backend-compat/requirements.txt
python -m pip check
npm run app:build
npm run app:start -- --profile "<verified-profile>" --run-dir "<new-run-directory>" --port 5039
```

Open the local address printed after the atlas loads. The supplied profile
must already exist and pass the manifest and file checks. Install does not
download data. If the port is busy, choose a different port; the launcher
does not stop other services. Run directories are never overwritten.

Add `--research-display "<package>/display.json"` and use a separate run
directory and port to open the [CCF + MRI research viewer](RESEARCH_CCF_MRI_PREVIEW.md).
This mode does not generate or export a plan. A low-memory startup puts mesh and
MRI image responses in a serial queue, without changing the data or resolution.

## Inspect and export

The toolbar identifies the active session. The backend panel shows the
optimizer and profile SHA-256, and the display manifest SHA-256 when an MRI
research package is present. The launched page connects only to the backend
that served it. It ignores remembered development-server addresses. Reload
the page after restarting the service; an old page cannot make changes to a
replacement instance of this launcher.

In a planning session, use **Results** to inspect coverage and audits. Under
**Reproducibility identifiers**, **Download replay JSON** retrieves the saved
request and response only if their recorded identifiers match the displayed
result. It preserves the saved JSON bytes, including signed zero. An imported
geometry without a backend record is not eligible. This
download can contain local paths in the recorded metadata; review it before
sharing. It is separate from the existing **I/O** geometry exports.

## Verification and limits

Install `requirements-registration.txt` in place of `requirements.txt` for
the full backend test suite. For the combined source checks, also run
`npm ci` at the repository root and then `npm run quality`.
The **App compatibility** workflow defines automated build and software
regression checks on Windows, Linux, and macOS. It does not download the
large releases, run a full real-profile planning session, or qualify 3D GPU
rendering across platforms. Actual rich-atlas sessions still require sufficient
memory and the separately versioned data.

Neither a successful browser session nor a software plan establishes
subject-specific anatomical accuracy, surgical safety, or native-space
implantation accuracy.
