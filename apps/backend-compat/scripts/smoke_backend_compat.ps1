param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
  [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = (Join-Path $ProjectRoot "src")
$files = @(
  "src\optimizer_server.py",
  "src\backend\api.py",
  "src\backend\candidate.py",
  "src\backend\contract.py",
  "src\backend\risk_model.py",
  "src\backend\security.py"
) | ForEach-Object { Join-Path $ProjectRoot $_ }
& $PythonExe -m py_compile @files
Write-Host "Backend compat syntax smoke passed."
