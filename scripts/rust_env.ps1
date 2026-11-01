$Root = Split-Path -Parent $PSScriptRoot
$env:CARGO_HOME = Join-Path $Root ".cargo"
$env:RUSTUP_HOME = Join-Path $Root ".rustup"
$env:PYO3_USE_ABI3_FORWARD_COMPATIBILITY = "1"
$Cargo = Join-Path $env:CARGO_HOME "bin\cargo.exe"
$Rustup = Join-Path $env:CARGO_HOME "bin\rustup.exe"

if (-not (Test-Path $Cargo)) {
  throw "Local cargo not found. Run scripts\install_rust_local.ps1 first."
}

$Vswhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
$VsPath = $null
$VsDevCmd = $null
if (Test-Path $Vswhere) {
  $VsPath = & $Vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
  if ($VsPath) {
    $VsDevCmd = Join-Path $VsPath "Common7\Tools\VsDevCmd.bat"
  }
}

if ($VsDevCmd -and (Test-Path $VsDevCmd)) {
  $Cmd = "`"$VsDevCmd`" -no_logo -arch=amd64 -host_arch=amd64 && set"
  $EnvDump = & cmd.exe /s /c $Cmd
  foreach ($Line in $EnvDump) {
    if ($Line -match "^(.*?)=(.*)$") {
      Set-Item -Path ("Env:{0}" -f $Matches[1]) -Value $Matches[2]
    }
  }
}

[pscustomobject]@{
  Root = $Root
  CargoHome = $env:CARGO_HOME
  RustupHome = $env:RUSTUP_HOME
  Cargo = $Cargo
  Rustup = $Rustup
  VisualStudioPath = $VsPath
  VisualStudioDevCmd = $VsDevCmd
}
