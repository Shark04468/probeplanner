# ProbePlanner Unity viewer source

This is recovered historical viewer source with explicit submission-revision repairs,
not a claim that the old prebuilt WebGL binary was reproducibly built from these bytes.
The source import inventory is retained with the revision audit outside this public tree.
Original archived files were not modified. IDE files and a legacy runtime watermark
suppression script were not imported; use an appropriately licensed editor without
bypassing its licensing or export restrictions.

## Toolchain

The recovered project specifies Tuanjie 1.8.3 / Unity-compatible 2022.3.62t5.
`Packages/manifest.json` and `packages-lock.json` bind its package dependencies.
WebGL build support is required. Compatibility with another editor is not assumed.

Use the editor's batch mode with `-projectPath` pointing to this directory:

1. `-batchmode -nographics -executeMethod BrainPlanner.Editor.PaperBuildChecks.PrepareAndCheck -quit -logFile <prepare-log>`
2. `-batchmode -nographics -buildTarget WebGL -executeMethod BrainPlanner.Editor.WebGLBatchBuild.BuildWebGL -quit -logFile <build-log>`

The first command normalizes duplicate controller/camera/helper/light objects in the
recovered scene through the editor API, preserving the first controller's serialized
references. Inspect the scene diff and retain the preparation log. The second command
checks the scene contract before building into `WebGL-Build/`. Cache directories and
build output are not source. A complete accepted release must bind generated WebGL
assets to this source, package lock, editor version and successful build log.

The React wrapper in `../ui/public/unity/index.html` must be deployed with that build's
matching `Build/` files. Do not mix the revised wrapper with the previous binary.

## Runtime contracts

- The selected `backend` query parameter is decoded and validated before mesh loading.
  Without it, a browser viewer uses its own origin. The localhost editor fallback is
  only used when there is no browser URL.
- `ReactBridge.DispatchCommand(string)` is the sole JavaScript SendMessage entry.
  A typed allowlist dispatches to that bridge instance rather than broadcasting typed
  subsystem setters with incompatible signatures.
- A `stateAck` means the command handler ran. Asynchronous region loading is reported
  separately; an acknowledgement is not proof that all assets loaded or anatomy aligned.
- Runtime errors are not converted into successful acknowledgements or suppressed as
  optional slice/lighting failures.

This viewer is not independent evidence of MRI anatomical registration, individual
skull landmarks or surgical safety. Atlas-derived surfaces remain atlas-derived.
