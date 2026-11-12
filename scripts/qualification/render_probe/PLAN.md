# Bounded browser capability diagnostic

This is a development diagnostic following failed actual scene run 43205019600,
not a software-UI, anatomical or scientific-validation experiment. Frozen
application 56868d57c23cbbb1207666f5e420bf47019591c6 stays untouched.

Compare Playwright 1.58.2 default headless shell and channel `chromium` headless
on one fresh macos-15 ARM runner. Keep Node 24.14.0 and the frozen UI lockfile.
Install the pinned Chromium distribution normally. Do not add GPU, software
renderer, security-bypass or timeout-expansion flags. Run modes sequentially.

Record OS/architecture, binary version, WebGL renderer/device information where
available, WebGL2 context, basic shader compile/link, actual known-color pixels,
context loss, 24 animation frames and raw canvas screenshot. A 60-second bound
per mode and 15-minute job bound apply. No atlas download or planning request is
needed. Full application, reference data and geometry are not mocked; they are
not run in this diagnostic at all.

Retain both outcomes, errors and all file hashes. Basic WebGL success is not proof
of correct Unity shaders, complete CCF+MRI rendering, a faster planning request,
or hardware acceleration. Renderer strings are observations, not certification.
Do not select a mode solely from the lower frame interval in this tiny example.
A later actual-scene comparison needs a separate fixed contract and all previous
required-resource gates. No old failure or frozen version is replaced.

Checklist: contract fixed before launch; local syntax and guard checks; publish
only the evidence branch; run once; verify archive and member hashes; inspect
both screenshots; record a bounded next decision.
