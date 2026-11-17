# Frozen paper three-platform CI contract

Prepared 2026-11-17 for reviewer R1-03 at the author's request for three-OS GitHub CI.

The orchestration branch is not the evaluated application. Each runner separately
checks out unmodified paper commit 56868d57c23cbbb1207666f5e420bf47019591c6.
No tag, DOI, application source, original test or archived result is changed.

Platforms are Windows Server 2022 x64, Ubuntu 22.04 x64, and macOS 15 ARM64.
Python 3.10.20, Node 24.14.0, the original Python lock and both npm locks are used.
The original optional surface-build requirement is installed without changing it.

The fixed cases are retrospective/search_unilateral_ca1,
confirmation/independent_request_01 and confirmation/reused_negative_control.
These are the already-declared release portability cases, not new holdouts.
Public data and evidence are downloaded from the immutable release and hash checked.
The original candidate-generation/exact replay and release verification scripts run
unchanged. They check the original 112-test subset, 329 backend tests, 152 frontend
tests, six surface tests, packaging tests, UI lint/build and archived exact models.
Counts overlap. The original Windows-only packaging symlink skip policy is retained,
not silently counted as a pass. Every failure, timeout and log is retained.

This is software and numerical-reproduction CI. It does not test a live browser,
Unity WebGL rendering, interactive GUI export, novice usability, native-GPU support,
Intel macOS or anatomical correctness. The earlier frozen-Mac 600-second UI request
timeout remains evidence and is not overwritten or reclassified by this matrix.
The 3600-second offline replay budget is the pre-existing replay budget, not a
replacement for the separate interactive-request deadline.

## External harness correction

Initial run 44119161805 was cancelled after read-only review found that the
original Windows release gate rejects the original Windows-junction test's
explicit skip on non-Windows hosts. Cancellation is not a pass. The original
application, tests and historical gate remain unchanged. The replacement external
portable orchestrator calls the same source, environment, profile, replay and
exact-verification functions and executes the same full test sets. Packaging
must collect exactly 203 tests. On non-Windows, only the exact Windows-junction
test with reason `Windows reparse-point test` is not applicable; it is reported
separately, never counted as passed. The original Windows symlink-privilege
allowance remains. Every other skip, failure, error or changed count fails.
The 112/329/152/6 suites permit no skips. This is not a claim that the original
Windows release gate itself is now portable. A 180-minute job budget reserves
time for the previously declared stages and artifact upload; it does not change
the separate frozen Mac UI timeout.

Run 44120535644 exposed a path-type error in the newly added external driver's
contract serialization, before the full test stages. Its results are retained.
The driver now passes a Path to the frozen hash helper; a new contract-stage
test exercises this integration. This does not change any frozen application
file, original test, result or numerical threshold.
