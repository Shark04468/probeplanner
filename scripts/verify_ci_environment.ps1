param(
    [string]$ArtifactRoot = $env:PROBEPLANNER_ARTIFACT_ROOT,
    [string]$ProfileName = $env:PROBEPLANNER_MRI_CCF_PROFILE_NAME,
    [string]$BackendBaselineSummary = $env:PERF_BASELINE_SUMMARY,
    [string]$SdfBaselineSummary = $env:SDF_BASELINE_SUMMARY,
    [string]$Browser3dBaselineSummary = $env:BROWSER3D_BASELINE_SUMMARY,
    [switch]$RequireBackendBaseline,
    [switch]$RequireSdfBaseline,
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
$layeredSdf = Join-Path $resolvedArtifactRoot "layered_sdf.npz"
$requiredSdfGroups = @(
    @("signed_sdf_mm"),
    @("skull_sdf_mm"),
    @("outer_tissue_sdf_mm", "cortex_outside_sdf_mm"),
    @("ventricle_sdf_mm"),
    @("vessel_sdf_mm"),
    @("sulcus_sdf_mm"),
    @("brain_surface_sdf_mm"),
    @("drilling_surface_sdf_mm")
)
if (-not (Test-Path (Join-Path $resolvedArtifactRoot "annotation.npy"))) {
    throw "Artifact profile is missing annotation.npy: $resolvedArtifactRoot"
}
if (-not (Test-Path $regionMapV2) -and -not (Test-Path $regionMapLegacy)) {
    throw "Artifact profile is missing region_map_v2.json or region_map.json: $resolvedArtifactRoot"
}
if (-not (Test-Path $layeredSdf)) {
    $missingSdfGroups = @()
    foreach ($group in $requiredSdfGroups) {
        $hasGroup = $false
        foreach ($channel in $group) {
            if (Test-Path (Join-Path $resolvedArtifactRoot "$channel.npy")) {
                $hasGroup = $true
                break
            }
        }
        if (-not $hasGroup) {
            $missingSdfGroups += ($group -join " or ")
        }
    }
    if ($missingSdfGroups.Count -gt 0) {
        throw "Artifact profile is missing layered_sdf.npz and mmap-ready SDF channel files: $($missingSdfGroups -join ', ')"
    }
}

if ($RequireBackendBaseline) {
    Require-Path "PERF_BASELINE_SUMMARY" $BackendBaselineSummary | Out-Null
}
if ($RequireSdfBaseline) {
    Require-Path "SDF_BASELINE_SUMMARY" $SdfBaselineSummary | Out-Null
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
    $playwrightCheck = @'
const fs = require("node:fs");
const { chromium } = require("@playwright/test");
const executablePath = chromium.executablePath();
if (!fs.existsSync(executablePath)) {
  throw new Error(`Playwright Chromium executable not found: ${executablePath}. Run npx playwright install chromium.`);
}
console.log(executablePath);
'@
    $playwrightChromiumPath = $playwrightCheck | node -
}

[pscustomobject]@{
    artifactRoot = $resolvedArtifactRoot
    hasRegionMapV2 = Test-Path $regionMapV2
    hasRegionMapLegacy = Test-Path $regionMapLegacy
    hasLayeredSdfNpz = Test-Path $layeredSdf
    backendBaseline = $BackendBaselineSummary
    sdfBaseline = $SdfBaselineSummary
    browser3dBaseline = $Browser3dBaselineSummary
    gpu = if ($RequireGpu) { $gpu.Name } else { $null }
    playwrightChromium = if ($RequireGpu) { $playwrightChromiumPath } else { $null }
} | ConvertTo-Json -Depth 4
