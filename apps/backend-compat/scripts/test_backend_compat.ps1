param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
  [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = (Join-Path $ProjectRoot "src")
& $PythonExe -m pytest (Join-Path $ProjectRoot "tests\test_optimizer_server.py") -q
