param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
  [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = (Join-Path $ProjectRoot "src")
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
$testOutput = & $PythonExe -m pytest `
  (Join-Path $ProjectRoot "tests\test_optimizer_server.py") `
  (Join-Path $ProjectRoot "tests\test_validation_scripts.py") -q 2>&1
$exitCode = $LASTEXITCODE
$testOutput | ForEach-Object { Write-Host $_ }
if ($exitCode -ne 0) { throw "backend and validation tests failed" }
$testText = $testOutput | Out-String
if ($testText -notmatch "(?m)(\d+) passed") { throw "pytest pass count was not reported" }
$passed = [int]$Matches[1]
if ($passed -ne 112) { throw "expected exactly 112 passed tests, observed $passed" }
