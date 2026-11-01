$ErrorActionPreference = "Stop"
$EnvInfo = & (Join-Path $PSScriptRoot "rust_env.ps1")
$Cargo = $EnvInfo.Cargo
$Jobs = if ($env:CARGO_BUILD_JOBS) { [int]$env:CARGO_BUILD_JOBS } else { 1 }
& $Cargo check --jobs $Jobs
exit $LASTEXITCODE
