$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Tools = Join-Path $Root ".tools"
New-Item -ItemType Directory -Force -Path $Tools | Out-Null
$RustupInit = Join-Path $Tools "rustup-init.exe"
if (-not (Test-Path $RustupInit)) {
  Invoke-WebRequest -Uri "https://win.rustup.rs/x86_64" -OutFile $RustupInit
}
$env:CARGO_HOME = Join-Path $Root ".cargo"
$env:RUSTUP_HOME = Join-Path $Root ".rustup"
& $RustupInit -y --no-modify-path --profile minimal --default-toolchain stable
& (Join-Path $env:CARGO_HOME "bin\rustup.exe") default stable
& (Join-Path $env:CARGO_HOME "bin\cargo.exe") --version
