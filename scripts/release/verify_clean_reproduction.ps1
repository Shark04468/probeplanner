param(
  [string]$SourceRef = "HEAD",
  [string]$PythonExe = "python",
  [string]$RustToolchain = "1.95.0",
  [switch]$KeepWorkspace
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$lockedPython = "3.10.20"
$actualPython = (& $PythonExe -c "import platform; print(platform.python_version())").Trim()
if ($LASTEXITCODE -ne 0) { throw "failed to inspect Python interpreter: $PythonExe" }
if ($actualPython -ne $lockedPython) {
  throw "Python $actualPython does not match locked version $lockedPython"
}
$runBase = Join-Path $repo ".probeplanner\reproduction"
$runRoot = Join-Path $runBase ([guid]::NewGuid().ToString("N"))
$source = Join-Path $runRoot "source"
$archive = Join-Path $runRoot "source.tar"
New-Item -ItemType Directory -Path $source -Force | Out-Null

try {
  git -C $repo archive --format=tar --output=$archive $SourceRef
  if ($LASTEXITCODE -ne 0) { throw "git archive failed for $SourceRef" }
  tar -xf $archive -C $source

  $env:npm_config_cache = Join-Path $runRoot "npm-cache"
  $env:CARGO_TARGET_DIR = Join-Path $runRoot "cargo-target"
  $env:PROBEPLANNER_STATE_DIR = Join-Path $runRoot "state"
  $env:PROBEPLANNER_REPLAY_DIR = Join-Path $env:PROBEPLANNER_STATE_DIR "replay"
  $env:PROBEPLANNER_AUDIT_LOG = Join-Path $env:PROBEPLANNER_STATE_DIR "audit.log"
  $env:PYTHONDONTWRITEBYTECODE = "1"
  $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"

  Push-Location $source
  try {
    & $PythonExe scripts/release/verify_release.py
    if ($LASTEXITCODE -ne 0) { throw "release verification failed" }

    npm ci
    if ($LASTEXITCODE -ne 0) { throw "root npm ci failed" }
    npm --prefix apps/ui ci
    if ($LASTEXITCODE -ne 0) { throw "UI npm ci failed" }
    npm run typecheck
    if ($LASTEXITCODE -ne 0) { throw "TypeScript check failed" }
    npm test
    if ($LASTEXITCODE -ne 0) { throw "root tests failed" }
    npm run ui:test
    if ($LASTEXITCODE -ne 0) { throw "UI tests failed" }
    npm --prefix apps/ui run quality
    if ($LASTEXITCODE -ne 0) { throw "UI quality checks failed" }
    npm run ui:build
    if ($LASTEXITCODE -ne 0) { throw "UI build failed" }

    $venv = Join-Path $runRoot "venv"
    & $PythonExe -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Python venv creation failed" }
    $venvPython = Join-Path $venv "Scripts\python.exe"
    if (-not (Test-Path $venvPython)) { $venvPython = Join-Path $venv "bin/python" }
    & $venvPython -m pip install --no-cache-dir -r apps/backend-compat/requirements-lock-py310.txt
    if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed" }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Python dependency check failed" }
    $testOutput = & $venvPython -m pytest `
      apps/backend-compat/tests/test_optimizer_server.py `
      apps/backend-compat/tests/test_validation_scripts.py -q 2>&1
    $testExitCode = $LASTEXITCODE
    $testOutput | ForEach-Object { Write-Host $_ }
    if ($testExitCode -ne 0) { throw "backend and validation tests failed" }
    $testText = $testOutput | Out-String
    if ($testText -notmatch "(?m)(\d+) passed") { throw "pytest pass count was not reported" }
    $passed = [int]$Matches[1]
    if ($passed -ne 112) { throw "expected exactly 112 passed tests, observed $passed" }
    $expectedRust = "1.95.0"
    $actualRust = (& rustc "+$RustToolchain" --version).Split()[1]
    if ($actualRust -ne $expectedRust) { throw "Rust version $actualRust does not match locked $expectedRust" }
    cargo "+$RustToolchain" test -p planner-engine
    if ($LASTEXITCODE -ne 0) { throw "cargo test failed" }
  } finally {
    Pop-Location
  }
  Write-Host "Clean-cache reproduction passed for $SourceRef in $runRoot"
} finally {
  if (-not $KeepWorkspace -and (Test-Path $runRoot)) {
    $resolvedRunBase = [System.IO.Path]::GetFullPath($runBase)
    $resolvedRunRoot = [System.IO.Path]::GetFullPath($runRoot)
    if (-not $resolvedRunRoot.StartsWith($resolvedRunBase, [StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to remove unexpected reproduction path: $resolvedRunRoot"
    }
    Remove-Item -LiteralPath $runRoot -Recurse -Force
  }
}
