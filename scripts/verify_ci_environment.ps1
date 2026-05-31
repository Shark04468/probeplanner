param(
    [string]$ArtifactRoot = $env:PROBEPLANNER_ARTIFACT_ROOT,
    [string]$ProfileName = $env:PROBEPLANNER_MRI_CCF_PROFILE_NAME,
    [string]$BackendBaselineSummary = $env:PERF_BASELINE_SUMMARY,
    [string]$Browser3dBaselineSummary = $env:BROWSER3D_BASELINE_SUMMARY,
    [switch]$RequireBackendBaseline,
    [switch]$RequireBrowser3dBaseline,
    [switch]$RequireGpu
)

$ErrorActionPreference = "Stop"

function Require-Path {
    param(
        [string]$Label,
        [string]$Path
    )
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "$Label is not configured"
    }
    if (-not (Test-Path $Path)) {
        throw "$Label not found: $Path"
    }
    return (Resolve-Path $Path).Path
}

$resolvedArtifactRoot = Require-Path "PROBEPLANNER_ARTIFACT_ROOT" $ArtifactRoot
if (-not (Test-Path (Join-Path $resolvedArtifactRoot "annotation.npy")) -and -not [string]::IsNullOrWhiteSpace($ProfileName)) {
    $profileCandidate = Join-Path $resolvedArtifactRoot $ProfileName
    if (Test-Path $profileCandidate) {
        $resolvedArtifactRoot = (Resolve-Path $profileCandidate).Path
    }
}
$regionMapV2 = Join-Path $resolvedArtifactRoot "region_map_v2.json"
$regionMapLegacy = Join-Path $resolvedArtifactRoot "region_map.json"
if (-not (Test-Path (Join-Path $resolvedArtifactRoot "annotation.npy"))) {
    throw "Artifact profile is missing annotation.npy: $resolvedArtifactRoot"
}
if (-not (Test-Path $regionMapV2) -and -not (Test-Path $regionMapLegacy)) {
    throw "Artifact profile is missing region_map_v2.json or region_map.json: $resolvedArtifactRoot"
}

if ($RequireBackendBaseline) {
    Require-Path "PERF_BASELINE_SUMMARY" $BackendBaselineSummary | Out-Null
}
if ($RequireBrowser3dBaseline) {
    Require-Path "BROWSER3D_BASELINE_SUMMARY" $Browser3dBaselineSummary | Out-Null
}
if ($RequireGpu) {
    $gpu = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -and $_.Name -notmatch "Basic Display|Remote Display" } |
        Select-Object -First 1
    if (-not $gpu) {
        throw "No non-basic GPU/display adapter detected for headed 3D gate"
    }
}

[pscustomobject]@{
    artifactRoot = $resolvedArtifactRoot
    hasRegionMapV2 = Test-Path $regionMapV2
    hasRegionMapLegacy = Test-Path $regionMapLegacy
    backendBaseline = $BackendBaselineSummary
    browser3dBaseline = $Browser3dBaselineSummary
    gpu = if ($RequireGpu) { $gpu.Name } else { $null }
} | ConvertTo-Json -Depth 4
