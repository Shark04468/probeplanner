param(
  [string]$Python = "",
  [string]$ArtifactRoot = "",
  [string]$OutDir = "reports/paper-allensdk-atlas-audit",
  [string]$EvidenceDir = "docs/evidence"
)

$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $PSScriptRoot "audit_allensdk_atlas_resources.py"
if (-not (Test-Path -LiteralPath $scriptPath)) {
  throw "Missing audit script: $scriptPath"
}

if ([string]::IsNullOrWhiteSpace($Python)) {
  if (-not [string]::IsNullOrWhiteSpace($env:PROBEPLANNER_ALLENSDK_PYTHON)) {
    $Python = $env:PROBEPLANNER_ALLENSDK_PYTHON
  }
}

if ([string]::IsNullOrWhiteSpace($Python)) {
  $Python = "python"
}

$argsList = @(
  $scriptPath,
  "--out", $OutDir,
  "--evidence-dir", $EvidenceDir
)

if (-not [string]::IsNullOrWhiteSpace($ArtifactRoot)) {
  $argsList += @("--artifact-root", $ArtifactRoot)
}

Write-Host ("[allensdk-audit] python={0}" -f $Python)
Write-Host ("[allensdk-audit] out={0}" -f $OutDir)
& $Python @argsList
exit $LASTEXITCODE
