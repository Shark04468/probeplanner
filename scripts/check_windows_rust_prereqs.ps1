$ErrorActionPreference = "Stop"

$EnvInfo = & (Join-Path $PSScriptRoot "rust_env.ps1")

function Test-Command($Name) {
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }
  return $null
}

function Write-Check($Name, $Value) {
  if ($Value) {
    Write-Host ("[ok]   {0}: {1}" -f $Name, $Value)
    return $true
  }
  Write-Host ("[miss] {0}" -f $Name)
  return $false
}

$ok = $true

$ok = (Write-Check "workspace cargo" $EnvInfo.Cargo) -and $ok
$ok = (Write-Check "workspace rustup" $EnvInfo.Rustup) -and $ok

$workspaceRustc = Join-Path (Split-Path -Parent $EnvInfo.Cargo) "rustc.exe"
$ok = (Write-Check "workspace rustc" $workspaceRustc) -and $ok

$rustcOnPath = Test-Command "rustc.exe"
$cargoOnPath = Test-Command "cargo.exe"
Write-Check "optional rustc on PATH" $rustcOnPath | Out-Null
Write-Check "optional cargo on PATH" $cargoOnPath | Out-Null
$ok = (Write-Check "MSVC linker link.exe" (Test-Command "link.exe")) -and $ok
$ok = (Write-Check "MSVC compiler cl.exe" (Test-Command "cl.exe")) -and $ok

$vswhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vswhere) {
  $vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
  $ok = (Write-Check "Visual Studio C++ tools" $vsPath) -and $ok
} else {
  Write-Host "[miss] Visual Studio Installer vswhere.exe"
  $ok = $false
}

$sdkLibRoot = "C:\Program Files (x86)\Windows Kits\10\Lib"
$sdkLib = $null
if (Test-Path $sdkLibRoot) {
  $sdkLib = Get-ChildItem $sdkLibRoot -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending |
    Select-Object -First 1 -ExpandProperty FullName
}
$ok = (Write-Check "Windows SDK lib" $sdkLib) -and $ok

if (-not $ok) {
  Write-Host ""
  Write-Host "Missing Rust/MSVC prerequisites. RustRover is an IDE and does not replace Visual Studio C++ Build Tools."
  Write-Host "Install Visual Studio Build Tools with:"
  Write-Host "- MSVC v143 x64/x86 build tools"
  Write-Host "- Windows 10 or Windows 11 SDK"
  exit 1
}

Write-Host ""
Write-Host "Windows Rust prerequisites look available."
