param(
  [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
  [string]$PythonExe = "",
  [string]$CondaEnvName = "",
  [int]$Port = 5000,
  [string]$AtlasMode = "allen_sdk",
  [string]$MriManifestPath = "",
  [string]$MriActiveProfilePath = "",
  [string]$MriAnnotationPath = "",
  [string]$MriRegionMapPath = "",
  [string]$MriRegistrationSummaryPath = "",
  [string]$MriStatsPath = "",
  [string]$MriJacobianPath = "",
  [string]$MriSkullMaskPath = "",
  [string]$MriOuterTissueMaskPath = "",
  [string]$MriLayeredSdfPath = "",
  [string]$MriDatasetProfile = "",
  [switch]$MriUseAppearance,
  [int]$WaitSeconds = 60,
  [switch]$KeepLogs
)

$ErrorActionPreference = "Stop"

function Get-ListeningProcIds([int]$TargetPort) {
  $out = @()
  $lines = netstat -ano | Select-String ":$TargetPort" | Select-String "LISTENING"
  foreach ($line in $lines) {
    $parts = ($line.ToString() -split "\s+") | Where-Object { $_ -ne "" }
    if ($parts.Count -lt 5) { continue }
    $candidate = $parts[-1]
    if ($candidate -match "^\d+$") {
      $out += [int]$candidate
    }
  }
  return ($out | Select-Object -Unique)
}

function Describe-Proc([int]$ProcId) {
  try {
    return Get-CimInstance Win32_Process -Filter "ProcessId=$ProcId" |
      Select-Object ProcessId, Name, ExecutablePath, CommandLine
  } catch {
    return $null
  }
}

function Stop-PortListeners([int]$TargetPort) {
  $pids = Get-ListeningProcIds -TargetPort $TargetPort
  if (-not $pids -or $pids.Count -eq 0) {
    Write-Host "[clean] no existing listener on :$TargetPort"
    return
  }
  foreach ($procId in $pids) {
    $procInfo = Describe-Proc -ProcId $procId
    if ($procInfo) {
      Write-Host ("[clean] killing PID={0} Name={1} Exe={2}" -f $procInfo.ProcessId, $procInfo.Name, $procInfo.ExecutablePath)
    } else {
      Write-Host ("[clean] killing PID={0}" -f $procId)
    }
    try {
      Stop-Process -Id $procId -Force -ErrorAction Stop
    } catch {
      Write-Warning ("[clean] failed to kill PID={0}: {1}" -f $procId, $_.Exception.Message)
    }
  }
  Start-Sleep -Milliseconds 500
}

function Resolve-OptionalPath([string]$PathText) {
  if ([string]::IsNullOrWhiteSpace($PathText)) { return "" }
  $expanded = [Environment]::ExpandEnvironmentVariables($PathText.Trim())
  if (-not [System.IO.Path]::IsPathRooted($expanded)) {
    $expanded = Join-Path $ProjectRoot $expanded
  }
  if (-not (Test-Path $expanded)) {
    throw "Path not found: $expanded"
  }
  return (Resolve-Path $expanded).Path
}

function Find-MriSeedPath([string[]]$CandidateRelativePaths) {
  $roots = @(
    (Join-Path $ProjectRoot ".probeplanner\\mri_ccf_hybrid_seed"),
    (Join-Path $env:USERPROFILE ".probeplanner\\mri_ccf_hybrid_seed")
  )
  foreach ($root in $roots) {
    if (-not (Test-Path $root)) { continue }
    foreach ($rel in $CandidateRelativePaths) {
      $candidate = Join-Path $root $rel
      if (Test-Path $candidate) {
        return (Resolve-Path $candidate).Path
      }
    }
  }
  return ""
}

function Find-NearbyArtifact([string]$SourcePath, [string[]]$CandidateRelativePaths) {
  if ([string]::IsNullOrWhiteSpace($SourcePath)) { return "" }
  if (-not (Test-Path $SourcePath)) { return "" }
  $resolved = (Resolve-Path $SourcePath).Path
  $base = if ((Get-Item $resolved).PSIsContainer) { $resolved } else { Split-Path -Parent $resolved }
  $dirs = @($base)
  $parent = Split-Path -Parent $base
  if (-not [string]::IsNullOrWhiteSpace($parent)) { $dirs += $parent }
  if ((Split-Path -Leaf $base).ToLower() -eq "registration") {
    $pp = Split-Path -Parent $parent
    if (-not [string]::IsNullOrWhiteSpace($pp)) { $dirs += $pp }
  }
  foreach ($d in ($dirs | Select-Object -Unique)) {
    foreach ($rel in $CandidateRelativePaths) {
      $candidate = Join-Path $d $rel
      if (Test-Path $candidate) {
        return (Resolve-Path $candidate).Path
      }
    }
  }
  return ""
}

if (-not (Test-Path $ProjectRoot)) {
  throw "ProjectRoot not found: $ProjectRoot"
}
if (-not (Test-Path $PythonExe)) {
  throw "Python executable not found: $PythonExe"
}
$serverScript = Join-Path $ProjectRoot "src\optimizer_server.py"
if (-not (Test-Path $serverScript)) {
  throw "Backend script not found: $serverScript"
}

if ([string]::IsNullOrWhiteSpace($CondaEnvName)) {
  $m = [regex]::Match($PythonExe, "\\envs\\([^\\]+)\\python\.exe$", [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
  if ($m.Success) {
    $CondaEnvName = $m.Groups[1].Value
  }
}

$mode = [string]$AtlasMode
if ([string]::IsNullOrWhiteSpace($mode)) { $mode = "allen_sdk" }
$mode = $mode.Trim().ToLower()
if ($mode -ne "allen_sdk" -and $mode -ne "qiu2018" -and $mode -ne "mri_ccf_hybrid") {
  throw "AtlasMode must be one of: allen_sdk, qiu2018, mri_ccf_hybrid"
}

Stop-PortListeners -TargetPort $Port

# Keep env explicit to avoid stale cross-mode settings.
$env:PROBEPLANNER_ATLAS_MODE = $mode
if ($mode -eq "allen_sdk") {
  Remove-Item Env:PROBEPLANNER_QIU2018_ANNOTATION_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_QIU2018_REGION_MAP_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_QIU2018_STATS_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_QIU2018_JACOBIAN_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_ANNOTATION_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_REGION_MAP_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_STATS_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_JACOBIAN_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_SKULL_MASK_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_OUTER_TISSUE_MASK_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_LAYERED_SDF_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_DATASET_PROFILE -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_MANIFEST_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_ACTIVE_PROFILE_PATH -ErrorAction SilentlyContinue
}
if ($mode -eq "qiu2018") {
  Remove-Item Env:PROBEPLANNER_MRI_CCF_ANNOTATION_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_REGION_MAP_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_STATS_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_JACOBIAN_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_SKULL_MASK_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_OUTER_TISSUE_MASK_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_LAYERED_SDF_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_DATASET_PROFILE -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_MANIFEST_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:PROBEPLANNER_MRI_CCF_ACTIVE_PROFILE_PATH -ErrorAction SilentlyContinue
}
if ($mode -eq "mri_ccf_hybrid") {
  $resolvedMriActiveProfile = Resolve-OptionalPath $MriActiveProfilePath
  $resolvedMriAnnotation = Resolve-OptionalPath $MriAnnotationPath
  if ([string]::IsNullOrWhiteSpace($resolvedMriAnnotation)) {
    $resolvedMriAnnotation = Find-MriSeedPath @("annotation.npy", "annotation.npz")
  }
  if ([string]::IsNullOrWhiteSpace($resolvedMriAnnotation)) {
    throw "mri_ccf_hybrid requires annotation path. Pass -MriAnnotationPath or place seed at $ProjectRoot\\.probeplanner\\mri_ccf_hybrid_seed\\annotation.npy"
  }
  $resolvedMriManifest = Resolve-OptionalPath $MriManifestPath
  if ([string]::IsNullOrWhiteSpace($resolvedMriManifest)) {
    $resolvedMriManifest = Find-NearbyArtifact $resolvedMriAnnotation @("artifact_manifest.json")
  }
  if ([string]::IsNullOrWhiteSpace($resolvedMriManifest)) {
    $resolvedMriManifest = Find-MriSeedPath @("artifact_manifest.json")
  }
  if ([string]::IsNullOrWhiteSpace($resolvedMriManifest)) {
    throw "mri_ccf_hybrid requires artifact_manifest.json. Pass -MriManifestPath or keep it beside annotation."
  }
  $resolvedMriRegionMap = Resolve-OptionalPath $MriRegionMapPath
  if ([string]::IsNullOrWhiteSpace($resolvedMriRegionMap)) {
    $resolvedMriRegionMap = Find-MriSeedPath @("region_map.json")
  }
  $resolvedMriRegSummary = Resolve-OptionalPath $MriRegistrationSummaryPath
  if ([string]::IsNullOrWhiteSpace($resolvedMriRegSummary)) {
    $resolvedMriRegSummary = Find-NearbyArtifact $resolvedMriManifest @("registration_summary.json", "registration\\registration_summary.json")
  }
  if ([string]::IsNullOrWhiteSpace($resolvedMriRegSummary)) {
    $resolvedMriRegSummary = Find-MriSeedPath @("registration_summary.json", "registration\\registration_summary.json")
  }
  $resolvedMriStats = Resolve-OptionalPath $MriStatsPath
  if ([string]::IsNullOrWhiteSpace($resolvedMriStats)) {
    $resolvedMriStats = Find-MriSeedPath @("stats.json")
  }
  $resolvedMriJacobian = Resolve-OptionalPath $MriJacobianPath
  if ([string]::IsNullOrWhiteSpace($resolvedMriJacobian)) {
    $resolvedMriJacobian = Find-MriSeedPath @("jacobian.npy", "jacobian.npz")
  }
  $resolvedMriSkull = Resolve-OptionalPath $MriSkullMaskPath
  if ([string]::IsNullOrWhiteSpace($resolvedMriSkull)) {
    $resolvedMriSkull = Find-MriSeedPath @("skull_mask.npy", "skull_mask.npz")
  }
  $resolvedMriOuter = Resolve-OptionalPath $MriOuterTissueMaskPath
  if ([string]::IsNullOrWhiteSpace($resolvedMriOuter)) {
    $resolvedMriOuter = Find-MriSeedPath @("outer_tissue_mask.npy", "outer_tissue_mask.npz")
  }
  $resolvedMriSdf = Resolve-OptionalPath $MriLayeredSdfPath
  if ([string]::IsNullOrWhiteSpace($resolvedMriSdf)) {
    $resolvedMriSdf = Find-MriSeedPath @("layered_sdf.npz", "layered_sdf.npy")
  }

  $env:PROBEPLANNER_PROJECT_ROOT = $ProjectRoot
  $env:PROBEPLANNER_MRI_CCF_MANIFEST_PATH = $resolvedMriManifest
  if (-not [string]::IsNullOrWhiteSpace($resolvedMriActiveProfile)) { $env:PROBEPLANNER_MRI_CCF_ACTIVE_PROFILE_PATH = $resolvedMriActiveProfile } else { Remove-Item Env:PROBEPLANNER_MRI_CCF_ACTIVE_PROFILE_PATH -ErrorAction SilentlyContinue }
  $env:PROBEPLANNER_MRI_CCF_ANNOTATION_PATH = $resolvedMriAnnotation
  if (-not [string]::IsNullOrWhiteSpace($resolvedMriRegionMap)) { $env:PROBEPLANNER_MRI_CCF_REGION_MAP_PATH = $resolvedMriRegionMap } else { Remove-Item Env:PROBEPLANNER_MRI_CCF_REGION_MAP_PATH -ErrorAction SilentlyContinue }
  if (-not [string]::IsNullOrWhiteSpace($resolvedMriStats)) { $env:PROBEPLANNER_MRI_CCF_STATS_PATH = $resolvedMriStats } else { Remove-Item Env:PROBEPLANNER_MRI_CCF_STATS_PATH -ErrorAction SilentlyContinue }
  if (-not [string]::IsNullOrWhiteSpace($resolvedMriJacobian)) { $env:PROBEPLANNER_MRI_CCF_JACOBIAN_PATH = $resolvedMriJacobian } else { Remove-Item Env:PROBEPLANNER_MRI_CCF_JACOBIAN_PATH -ErrorAction SilentlyContinue }
  if (-not [string]::IsNullOrWhiteSpace($resolvedMriSkull)) { $env:PROBEPLANNER_MRI_CCF_SKULL_MASK_PATH = $resolvedMriSkull } else { Remove-Item Env:PROBEPLANNER_MRI_CCF_SKULL_MASK_PATH -ErrorAction SilentlyContinue }
  if (-not [string]::IsNullOrWhiteSpace($resolvedMriOuter)) { $env:PROBEPLANNER_MRI_CCF_OUTER_TISSUE_MASK_PATH = $resolvedMriOuter } else { Remove-Item Env:PROBEPLANNER_MRI_CCF_OUTER_TISSUE_MASK_PATH -ErrorAction SilentlyContinue }
  if (-not [string]::IsNullOrWhiteSpace($resolvedMriSdf)) { $env:PROBEPLANNER_MRI_CCF_LAYERED_SDF_PATH = $resolvedMriSdf } else { Remove-Item Env:PROBEPLANNER_MRI_CCF_LAYERED_SDF_PATH -ErrorAction SilentlyContinue }
  if (-not [string]::IsNullOrWhiteSpace($MriDatasetProfile)) { $env:PROBEPLANNER_MRI_CCF_DATASET_PROFILE = $MriDatasetProfile }
  if ($PSBoundParameters.ContainsKey("MriUseAppearance")) {
    $env:PROBEPLANNER_MRI_CCF_USE_APPEARANCE = ($(if ($MriUseAppearance) { "true" } else { "false" }))
  }

  Write-Host ("[mri] manifest={0}" -f $resolvedMriManifest)
  Write-Host ("[mri] annotation={0}" -f $resolvedMriAnnotation)
  if (-not [string]::IsNullOrWhiteSpace($resolvedMriRegSummary)) { Write-Host ("[mri] registration_summary={0}" -f $resolvedMriRegSummary) }
  if (-not [string]::IsNullOrWhiteSpace($resolvedMriRegionMap)) { Write-Host ("[mri] region_map={0}" -f $resolvedMriRegionMap) }
  if (-not [string]::IsNullOrWhiteSpace($resolvedMriStats)) { Write-Host ("[mri] stats={0}" -f $resolvedMriStats) }
}
if (-not [string]::IsNullOrWhiteSpace($CondaEnvName)) {
  $env:CONDA_DEFAULT_ENV = $CondaEnvName
}
try {
  $pythonDir = Split-Path -Parent $PythonExe
  $condaPrefix = Split-Path -Parent $pythonDir
  if (Test-Path $condaPrefix) {
    $env:CONDA_PREFIX = $condaPrefix
  }
} catch {
  # keep launch robust; env hints are best-effort
}
# Work around Start-Process env merge issue on Windows when both Path/PATH exist.
try {
  if ((Test-Path Env:Path) -and (Test-Path Env:PATH)) {
    Remove-Item Env:PATH -ErrorAction SilentlyContinue
  }
} catch {
  Write-Warning ("[env] failed to normalize PATH casing: {0}" -f $_.Exception.Message)
}

$stdout = Join-Path $ProjectRoot "backend_clean_stdout.log"
$stderr = Join-Path $ProjectRoot "backend_clean_stderr.log"
if (-not $KeepLogs) {
  if (Test-Path $stdout) { Remove-Item $stdout -Force -ErrorAction SilentlyContinue }
  if (Test-Path $stderr) { Remove-Item $stderr -Force -ErrorAction SilentlyContinue }
}

$proc = Start-Process `
  -FilePath $PythonExe `
  -ArgumentList @("src/optimizer_server.py") `
  -WorkingDirectory $ProjectRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdout `
  -RedirectStandardError $stderr `
  -PassThru

Write-Host ("[start] PID={0} Python={1} Mode={2} Port={3}" -f $proc.Id, $PythonExe, $mode, $Port)
Write-Host ("[logs] stdout={0}" -f $stdout)
Write-Host ("[logs] stderr={0}" -f $stderr)

$statusUrl = "http://localhost:$Port/api/status?includeRegions=0&atlasMode=$mode"
$ready = $false
for ($i = 1; $i -le $WaitSeconds; $i++) {
  try {
    $s = Invoke-RestMethod $statusUrl -TimeoutSec 5
    $phase = [string]($s.atlasLoad.phase)
    $state = [string]($s.atlasLoad.state)
    $progress = [double]($s.atlasLoad.progress)
    $loaded = [bool]($s.atlasLoaded)
    $err = [string]($s.atlasError)
    $envName = [string]($s.environmentSignature.condaDefaultEnv)
    Write-Host ("[wait {0}/{1}] state={2} phase={3} progress={4:P0} loaded={5} env={6}" -f $i, $WaitSeconds, $state, $phase, $progress, $loaded, $envName)
    if (-not [string]::IsNullOrWhiteSpace($err)) {
      throw "atlasError: $err"
    }
    if ($loaded) {
      $ready = $true
      break
    }
  } catch {
    Write-Host ("[wait {0}/{1}] pending: {2}" -f $i, $WaitSeconds, $_.Exception.Message)
  }
  Start-Sleep -Seconds 1
}

if (-not $ready) {
  Write-Warning ("[done] backend not ready within {0}s, check logs above." -f $WaitSeconds)
  exit 1
}

$final = Invoke-RestMethod $statusUrl -TimeoutSec 6
$finalMode = [string]$final.activeAtlasMode
$finalEnv = [string]($final.environmentSignature.condaDefaultEnv)
$riskEnabled = [bool]($final.riskModel.enabled)
Write-Host ("[ok] atlasLoaded={0} mode={1} env={2} riskEnabled={3}" -f $final.atlasLoaded, $finalMode, $finalEnv, $riskEnabled)
