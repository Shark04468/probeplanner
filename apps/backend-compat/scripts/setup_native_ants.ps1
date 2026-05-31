param(
  [string]$ProjectRoot = "",
  [string]$Version = "2.6.5"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$antsRoot = Join-Path $ProjectRoot "tools\ants"
$downloadDir = Join-Path $antsRoot "downloads"
$assetName = "ants-$Version-windows-2022-X64-VS2019.zip"
$zipPath = Join-Path $downloadDir $assetName
$extractRoot = Join-Path $antsRoot "ants-$Version-windows-2022-X64-VS2019"
$releaseUrl = "https://github.com/ANTsX/ANTs/releases/download/v$Version/$assetName"

New-Item -ItemType Directory -Force -Path $downloadDir, $extractRoot | Out-Null

if (-not (Test-Path $zipPath)) {
  Write-Host "[DOWNLOAD] $releaseUrl"
  Invoke-WebRequest -Uri $releaseUrl -OutFile $zipPath -Headers @{ "User-Agent" = "probeplanner-setup" }
} else {
  Write-Host "[SKIP] Existing ANTs zip: $zipPath"
}

$needExtract = $true
$existingReg = Get-ChildItem -Path $extractRoot -Recurse -File -Filter "antsRegistration.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existingReg) { $needExtract = $false }
if ($needExtract) {
  Write-Host "[EXTRACT] $zipPath"
  Expand-Archive -Path $zipPath -DestinationPath $extractRoot -Force
}

$bin = (Get-ChildItem -Path $extractRoot -Recurse -File -Filter "antsRegistration.exe" | Select-Object -First 1).DirectoryName
if (-not $bin) { throw "antsRegistration.exe not found after extraction." }

$required = @("antsRegistration.exe", "antsApplyTransforms.exe", "CreateJacobianDeterminantImage.exe")
foreach ($cmd in $required) {
  $p = Join-Path $bin $cmd
  if (-not (Test-Path $p)) { throw "Missing ANTs command: $p" }
}

$envFile = Join-Path $antsRoot "ants_env.ps1"
@"
# Auto-generated. Dot-source this file before running native ANTs scripts:
# . "$envFile"
`$env:ANTSPATH = "$bin\"
`$env:PATH = "$bin;`$env:PATH"
"@ | Set-Content -Path $envFile -Encoding UTF8

$versionText = (& (Join-Path $bin "antsRegistration.exe") --version) -join "`n"
$out = [ordered]@{
  ok = $true
  antsBinDir = $bin
  envFile = $envFile
  version = $versionText
}
$out | ConvertTo-Json -Depth 4
