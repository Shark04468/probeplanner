param(
    [string]$ArtifactRoot = $env:PROBEPLANNER_ARTIFACT_ROOT,
    [string]$HostName = $(if ($env:PROBEPLANNER_ENGINE_HOST) { $env:PROBEPLANNER_ENGINE_HOST } else { "127.0.0.1" }),
    [int]$Port = $(if ($env:PROBEPLANNER_ENGINE_PORT) { [int]$env:PROBEPLANNER_ENGINE_PORT } else { 7878 }),
    [switch]$KillExisting
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Test-ProjectOwnedProcess {
    param($ProcessInfo)
    $rootPrefix = (Resolve-Path $Root).Path
    $exe = [string]($ProcessInfo.ExecutablePath)
    $cmd = [string]($ProcessInfo.CommandLine)
    if (-not [string]::IsNullOrWhiteSpace($exe) -and $exe.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    if (-not [string]::IsNullOrWhiteSpace($cmd) -and $cmd.IndexOf($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $true
    }
    return $false
}

function Test-NativeArtifactProfile {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    return (
        (Test-Path (Join-Path $Path "annotation.npy")) -and
        (
            (Test-Path (Join-Path $Path "region_map_v2.json")) -or
            (Test-Path (Join-Path $Path "region_map.json"))
        )
    )
}

if ([string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    $candidates = @(
        (Join-Path $Root ".probeplanner\mri_ccf_hybrid_seed"),
        (Join-Path $Root "artifacts\ambmc_true_outer_full"),
        (Join-Path $Root ".cache\native-artifacts")
    )
    foreach ($candidate in $candidates) {
        if (Test-NativeArtifactProfile $candidate) {
            $ArtifactRoot = $candidate
            break
        }
    }
}
if ([string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    throw "PROBEPLANNER_ARTIFACT_ROOT is required. Pass -ArtifactRoot or create a local artifact root with annotation.npy and region_map_v2.json or region_map.json."
}
if (-not (Test-Path $ArtifactRoot)) {
    throw "Artifact root does not exist: $ArtifactRoot"
}
$ProfileName = $env:PROBEPLANNER_MRI_CCF_PROFILE_NAME
if (-not [string]::IsNullOrWhiteSpace($env:PROBEPLANNER_MRI_CCF_ACTIVE_PROFILE_PATH)) {
    $activeProfile = $env:PROBEPLANNER_MRI_CCF_ACTIVE_PROFILE_PATH
    if (Test-NativeArtifactProfile $activeProfile) {
        $ArtifactRoot = (Resolve-Path $activeProfile).Path
    }
} elseif (-not [string]::IsNullOrWhiteSpace($env:PROBEPLANNER_MRI_CCF_DEFAULT_PROFILE_DIR)) {
    $activeProfile = $env:PROBEPLANNER_MRI_CCF_DEFAULT_PROFILE_DIR
    if (Test-NativeArtifactProfile $activeProfile) {
        $ArtifactRoot = (Resolve-Path $activeProfile).Path
    }
} elseif (-not [string]::IsNullOrWhiteSpace($ProfileName)) {
    $profileRoot = Join-Path $ArtifactRoot $ProfileName
    if (Test-NativeArtifactProfile $profileRoot) {
        $ArtifactRoot = (Resolve-Path $profileRoot).Path
    }
}
$env:PROBEPLANNER_ARTIFACT_ROOT = $ArtifactRoot
$env:PROBEPLANNER_ENGINE_HOST = $HostName
$env:PROBEPLANNER_ENGINE_PORT = [string]$Port

if ($KillExisting) {
    $rootPrefix = (Resolve-Path $Root).Path
    Get-CimInstance Win32_Process -Filter "Name='planner-engine-http.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessId -ne $PID -and
            $_.ExecutablePath -and
            $_.ExecutablePath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)
        } |
        ForEach-Object {
            Write-Host "Stopping stale planner-engine-http PID $($_.ProcessId) at $($_.ExecutablePath)"
            Stop-Process -Id ([int]$_.ProcessId) -Force -ErrorAction SilentlyContinue
        }
    $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.OwningProcess -and $_.OwningProcess -ne $PID } |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $connections) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        if ($proc -and (Test-ProjectOwnedProcess $proc)) {
            Write-Host "Stopping project-owned process PID $procId on port $Port"
            Stop-Process -Id ([int]$procId) -Force -ErrorAction SilentlyContinue
        } else {
            throw "Port $Port is already in use by non-ProbePlanner process PID $procId. Stop it manually or choose another PROBEPLANNER_ENGINE_PORT."
        }
    }
}

if (-not $env:PROBEPLANNER_ATLAS_NPY) {
    $AtlasPath = Join-Path $ArtifactRoot "annotation.npy"
    if (Test-Path $AtlasPath) { $env:PROBEPLANNER_ATLAS_NPY = $AtlasPath }
}
if (-not $env:PROBEPLANNER_REGION_MAP_JSON) {
    $RegionPathV2 = Join-Path $ArtifactRoot "region_map_v2.json"
    $RegionPathLegacy = Join-Path $ArtifactRoot "region_map.json"
    if (Test-Path $RegionPathV2) {
        $env:PROBEPLANNER_REGION_MAP_JSON = $RegionPathV2
    } elseif (Test-Path $RegionPathLegacy) {
        $env:PROBEPLANNER_REGION_MAP_JSON = $RegionPathLegacy
    }
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
$CacheNativeArtifacts = Join-Path $Root ".cache\native-artifacts"
$RequiredSdfChannels = @("signed_sdf_mm") + $LayeredChannels
$RequiredSdfChannelGroups = @(
    @("signed_sdf_mm"),
    @("skull_sdf_mm"),
    @("outer_tissue_sdf_mm", "cortex_outside_sdf_mm"),
    @("ventricle_sdf_mm"),
    @("vessel_sdf_mm"),
    @("sulcus_sdf_mm"),
    @("brain_surface_sdf_mm"),
    @("drilling_surface_sdf_mm")
)
$missingNativeSdf = @()
foreach ($group in $RequiredSdfChannelGroups) {
    $groupReady = $false
    foreach ($channel in $group) {
        $cached = Join-Path $CacheNativeArtifacts "$channel.npy"
        $artifact = Join-Path $ArtifactRoot "$channel.npy"
        if ((Test-Path $cached) -or (Test-Path $artifact)) {
            $groupReady = $true
            break
        }
    }
    if (-not $groupReady) {
        $missingNativeSdf += ($group -join "/")
    }
}
if ($missingNativeSdf.Count -gt 0) {
    $npzPath = Join-Path $ArtifactRoot "layered_sdf.npz"
    if (-not (Test-Path $npzPath)) {
        throw "Native engine requires mmap-ready SDF .npy files for channels [$($missingNativeSdf -join ', ')] or layered_sdf.npz containing those channels."
    }
    Write-Host "Preparing mmap-ready native SDF artifacts for missing channels: $($missingNativeSdf -join ', ')"
    & (Join-Path $PSScriptRoot "prepare_native_artifacts.ps1") `
        -ArtifactRoot $ArtifactRoot `
        -OutputDir $CacheNativeArtifacts `
        -SdfChannel "signed_sdf_mm" `
        -SdfChannels ($RequiredSdfChannels -join ",")
    if ($LASTEXITCODE -ne 0) {
        throw "prepare_native_artifacts.ps1 failed with exit code $LASTEXITCODE"
    }
}
if (-not $env:PROBEPLANNER_SDF_NPY) {
    $CachedSdfNpyPath = Join-Path $CacheNativeArtifacts "signed_sdf_mm.npy"
    $SdfNpyPath = Join-Path $ArtifactRoot "signed_sdf_mm.npy"
    if (Test-Path $CachedSdfNpyPath) {
        $env:PROBEPLANNER_SDF_NPY = $CachedSdfNpyPath
    } elseif (Test-Path $SdfNpyPath) {
        $env:PROBEPLANNER_SDF_NPY = $SdfNpyPath
    }
}
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
