param(
    [string]$ArtifactRoot = $env:PROBEPLANNER_ARTIFACT_ROOT,
    [string]$HostName = $(if ($env:PROBEPLANNER_ENGINE_HOST) { $env:PROBEPLANNER_ENGINE_HOST } else { "127.0.0.1" }),
    [int]$Port = $(if ($env:PROBEPLANNER_ENGINE_PORT) { [int]$env:PROBEPLANNER_ENGINE_PORT } else { 7878 }),
    [switch]$KillExisting
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    $candidates = @(
        (Join-Path $Root ".probeplanner\mri_ccf_hybrid_seed"),
        (Join-Path $Root "artifacts\ambmc_true_outer_full"),
        (Join-Path $Root ".cache\native-artifacts")
    )
    foreach ($candidate in $candidates) {
        if ((Test-Path (Join-Path $candidate "annotation.npy")) -and (Test-Path (Join-Path $candidate "region_map_v2.json"))) {
            $ArtifactRoot = $candidate
            break
        }
    }
}
if ([string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    throw "PROBEPLANNER_ARTIFACT_ROOT is required. Pass -ArtifactRoot or create a local artifact root with annotation.npy and region_map_v2.json."
}
if (-not (Test-Path $ArtifactRoot)) {
    throw "Artifact root does not exist: $ArtifactRoot"
}
$env:PROBEPLANNER_ARTIFACT_ROOT = $ArtifactRoot
$env:PROBEPLANNER_ENGINE_HOST = $HostName
$env:PROBEPLANNER_ENGINE_PORT = [string]$Port

if ($KillExisting) {
    $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.OwningProcess -and $_.OwningProcess -ne $PID }
    foreach ($connection in $connections) {
        Stop-Process -Id $connection.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

if (-not $env:PROBEPLANNER_ATLAS_NPY) {
    $AtlasPath = Join-Path $ArtifactRoot "annotation.npy"
    if (Test-Path $AtlasPath) { $env:PROBEPLANNER_ATLAS_NPY = $AtlasPath }
}
if (-not $env:PROBEPLANNER_REGION_MAP_JSON) {
    $RegionPath = Join-Path $ArtifactRoot "region_map_v2.json"
    if (Test-Path $RegionPath) { $env:PROBEPLANNER_REGION_MAP_JSON = $RegionPath }
}
if (-not $env:PROBEPLANNER_SDF_NPZ) {
    $SdfPath = Join-Path $ArtifactRoot "layered_sdf.npz"
    if (Test-Path $SdfPath) { $env:PROBEPLANNER_SDF_NPZ = $SdfPath }
}
if (-not $env:PROBEPLANNER_SDF_NPY) {
    $CachedSdfNpyPath = Join-Path $Root ".cache\native-artifacts\signed_sdf_mm.npy"
    $SdfNpyPath = Join-Path $ArtifactRoot "signed_sdf_mm.npy"
    if (Test-Path $CachedSdfNpyPath) {
        $env:PROBEPLANNER_SDF_NPY = $CachedSdfNpyPath
    } elseif (Test-Path $SdfNpyPath) {
        $env:PROBEPLANNER_SDF_NPY = $SdfNpyPath
    }
}
if (-not $env:PROBEPLANNER_SDF_CHANNEL) {
    $env:PROBEPLANNER_SDF_CHANNEL = "signed_sdf_mm"
}
$LayeredChannels = @(
    "skull_sdf_mm",
    "outer_tissue_sdf_mm",
    "cortex_outside_sdf_mm",
    "ventricle_sdf_mm",
    "vessel_sdf_mm",
    "sulcus_sdf_mm",
    "brain_surface_sdf_mm",
    "drilling_surface_sdf_mm"
)
foreach ($channel in $LayeredChannels) {
    $envName = "PROBEPLANNER_{0}_NPY" -f $channel.ToUpperInvariant()
    if (-not [Environment]::GetEnvironmentVariable($envName, "Process")) {
        $cached = Join-Path $Root ".cache\native-artifacts\$channel.npy"
        $artifact = Join-Path $ArtifactRoot "$channel.npy"
        if (Test-Path $cached) {
            [Environment]::SetEnvironmentVariable($envName, $cached, "Process")
        } elseif (Test-Path $artifact) {
            [Environment]::SetEnvironmentVariable($envName, $artifact, "Process")
        }
    }
}
$EnvInfo = & (Join-Path $PSScriptRoot "rust_env.ps1")
$Cargo = $EnvInfo.Cargo
& $Cargo run --release -p planner-engine --bin planner-engine-http
exit $LASTEXITCODE
