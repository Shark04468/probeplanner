param(
    [string]$PythonExe = $(if ($env:PROBEPLANNER_PYTHON_EXE) { $env:PROBEPLANNER_PYTHON_EXE } else { "" }),
    [string]$ArtifactRoot = $env:PROBEPLANNER_ARTIFACT_ROOT,
    [string]$OutputDir = "",
    [string]$SdfChannel = "signed_sdf_mm",
    [string]$SdfChannels = "signed_sdf_mm,skull_sdf_mm,outer_tissue_sdf_mm,cortex_outside_sdf_mm,ventricle_sdf_mm,vessel_sdf_mm,sulcus_sdf_mm,brain_surface_sdf_mm,drilling_surface_sdf_mm"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $OutputDir) {
    $OutputDir = Join-Path $Root ".cache\native-artifacts"
}

if ([string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    throw "PROBEPLANNER_ARTIFACT_ROOT is required. Pass -ArtifactRoot to the MRI/atlas artifact directory."
}
if (-not (Test-Path $ArtifactRoot)) {
    throw "Artifact root does not exist: $ArtifactRoot"
}

if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "Python executable not found at $PythonExe and python is not on PATH" }
    $PythonExe = $cmd.Source
}

& $PythonExe (Join-Path $PSScriptRoot "prepare_native_artifacts.py") `
    --artifact-root $ArtifactRoot `
    --output-dir $OutputDir `
    --sdf-channel $SdfChannel `
    --sdf-channels $SdfChannels
exit $LASTEXITCODE
