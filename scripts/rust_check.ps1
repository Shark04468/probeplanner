$ErrorActionPreference = "Stop"
$EnvInfo = & (Join-Path $PSScriptRoot "rust_env.ps1")
$Cargo = $EnvInfo.Cargo
& $Cargo check
exit $LASTEXITCODE
