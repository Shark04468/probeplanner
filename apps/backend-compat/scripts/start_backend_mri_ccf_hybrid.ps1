param(
  [string]$ProjectRoot = "",
  [string]$PythonExe = "",
  [string]$ManifestPath = "",
  [string]$ActiveProfilePath = "",
  [string]$AnnotationPath,
  [string]$RegionMapPath = "",
  [string]$StatsPath = "",
  [string]$JacobianPath = "",
  [string]$SkullMaskPath = "",
  [string]$OuterTissueMaskPath = "",
  [string]$LayeredSdfPath = "",
  [string]$DatasetProfile = "custom",
  [int]$Port = 5000,
  [int]$WaitSeconds = 120,
  [switch]$NoMemmap,
  [switch]$PersistUserEnv
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot "path_defaults.ps1")
if ((Get-Variable -Name ProjectRoot -ErrorAction SilentlyContinue) -and [string]::IsNullOrWhiteSpace($ProjectRoot)) { $ProjectRoot = Get-ProbePlannerBackendRoot }
if ((Get-Variable -Name PythonExe -ErrorAction SilentlyContinue) -and [string]::IsNullOrWhiteSpace($PythonExe)) { $PythonExe = Get-ProbePlannerPython }
if ((Get-Variable -Name AntsBinDir -ErrorAction SilentlyContinue) -and [string]::IsNullOrWhiteSpace($AntsBinDir)) { $AntsBinDir = Get-ProbePlannerAntsBin }
if ((Get-Variable -Name QiuRoot -ErrorAction SilentlyContinue) -and [string]::IsNullOrWhiteSpace($QiuRoot)) { $QiuRoot = Join-Path (Get-ProbePlannerWorkspaceRoot) ".data\qiu2018" }
if ((Get-Variable -Name DataRoot -ErrorAction SilentlyContinue) -and [string]::IsNullOrWhiteSpace($DataRoot)) { $DataRoot = Join-ProbePlannerMriPath "raw" }
if ((Get-Variable -Name DatasetProfile -ErrorAction SilentlyContinue) -and [string]::IsNullOrWhiteSpace($DatasetProfile)) { $DatasetProfile = "mri_ccf_profile" }
if ((Get-Variable -Name ProfileDir -ErrorAction SilentlyContinue) -and [string]::IsNullOrWhiteSpace($ProfileDir)) { $ProfileDir = Join-ProbePlannerMriPath ("planner_artifacts\" + $DatasetProfile) }
if ((Get-Variable -Name OutputDir -ErrorAction SilentlyContinue) -and [string]::IsNullOrWhiteSpace($OutputDir)) { $OutputDir = Join-ProbePlannerMriPath "planner_artifacts\legacy_pipeline_output" }
if ((Get-Variable -Name WorkDir -ErrorAction SilentlyContinue) -and [string]::IsNullOrWhiteSpace($WorkDir)) { $WorkDir = Join-ProbePlannerMriPath ("ants_syn\" + $DatasetProfile) }
if ((Get-Variable -Name FixedImage -ErrorAction SilentlyContinue) -and [string]::IsNullOrWhiteSpace($FixedImage)) { $FixedImage = Join-ProbePlannerMriPath "raw\allen_ccf\average_template_25.nrrd" }
if ((Get-Variable -Name FixedAnnotation -ErrorAction SilentlyContinue) -and [string]::IsNullOrWhiteSpace($FixedAnnotation)) { $FixedAnnotation = Join-ProbePlannerMriPath "raw\allen_ccf\CCFv3_annotation_25.nrrd" }
if ((Get-Variable -Name MovingImage -ErrorAction SilentlyContinue) -and [string]::IsNullOrWhiteSpace($MovingImage)) { $MovingImage = Join-ProbePlannerMriPath "raw\ambmc\ambmc-c57bl6-model-symmet_v0.8_extracted\ambmc-c57bl6-model-symmet_v0.8-nii\ambmc-c57bl6-model-symmet_v0.8.nii" }
if ((Get-Variable -Name MovingMask -ErrorAction SilentlyContinue) -and [string]::IsNullOrWhiteSpace($MovingMask)) { $MovingMask = Join-ProbePlannerMriPath "ants_syn\ambmc_to_ccf\moving_masks\ambmc_moving_brain_mask_clean.nii.gz" }

function Resolve-OptionalPath([string]$inputPath) {
  if ([string]::IsNullOrWhiteSpace($inputPath)) { return "" }
  if (-not (Test-Path $inputPath)) {
    throw "Path does not exist: $inputPath"
  }
  return (Resolve-Path $inputPath).Path
}

function Set-ProcessEnv([string]$name, [string]$value) {
  if ([string]::IsNullOrWhiteSpace($value)) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
  } else {
    Set-Item -Path "Env:$name" -Value $value
  }
}

function Set-OptionalUserEnv([string]$name, [string]$value) {
  if ($PersistUserEnv) {
    if ([string]::IsNullOrWhiteSpace($value)) {
      [Environment]::SetEnvironmentVariable($name, $null, 'User')
    } else {
      [Environment]::SetEnvironmentVariable($name, $value, 'User')
    }
  }
}

function Get-ListeningProcIds([int]$TargetPort) {
  $out = @()
  $netstatCmd = Get-Command netstat -ErrorAction SilentlyContinue
  if ($netstatCmd) {
    try {
      $lines = & $netstatCmd.Source -ano | Select-String ":$TargetPort" | Select-String "LISTENING"
      foreach ($line in $lines) {
        $parts = ($line.ToString() -split "\s+") | Where-Object { $_ -ne "" }
        if ($parts.Count -lt 5) { continue }
        $pid = $parts[-1]
        if ($pid -match '^\d+$') { $out += [int]$pid }
      }
    } catch {}
  }
  try {
    $conns = Get-NetTCPConnection -State Listen -LocalPort $TargetPort -ErrorAction Stop
    foreach ($c in $conns) {
      if ($c.OwningProcess -is [int]) { $out += [int]$c.OwningProcess }
    }
  } catch {}
  return ($out | Select-Object -Unique)
}

if (-not (Test-Path $PythonExe)) { throw "Python executable not found at $PythonExe" }
if (-not (Test-Path $ProjectRoot)) { throw "Project root not found: $ProjectRoot" }
if ([string]::IsNullOrWhiteSpace($ManifestPath) -and [string]::IsNullOrWhiteSpace($AnnotationPath)) {
  throw "Either ManifestPath or AnnotationPath is required."
}

$manifestResolved = Resolve-OptionalPath $ManifestPath
$activeProfileResolved = Resolve-OptionalPath $ActiveProfilePath
$annotationResolved = Resolve-OptionalPath $AnnotationPath
$regionMapResolved = Resolve-OptionalPath $RegionMapPath
$statsResolved = Resolve-OptionalPath $StatsPath
$jacobianResolved = Resolve-OptionalPath $JacobianPath
$skullResolved = Resolve-OptionalPath $SkullMaskPath
$outerResolved = Resolve-OptionalPath $OuterTissueMaskPath
$layeredResolved = Resolve-OptionalPath $LayeredSdfPath

$validator = Join-Path $ProjectRoot 'scripts\validate_mri_ccf_inputs.py'
if (-not (Test-Path $validator)) {
  throw "Validator script not found: $validator"
}

$validateArgs = @(
  $validator
)
if (-not [string]::IsNullOrWhiteSpace($manifestResolved)) {
  $validateArgs += @('--manifest', $manifestResolved, '--expected-shape', '528,320,456')
} else {
  $validateArgs += @('--annotation', $annotationResolved, '--expected-shape', '528,320,456')
  if (-not [string]::IsNullOrWhiteSpace($regionMapResolved)) { $validateArgs += @('--region-map', $regionMapResolved) }
  if (-not [string]::IsNullOrWhiteSpace($statsResolved)) { $validateArgs += @('--stats', $statsResolved) }
  if (-not [string]::IsNullOrWhiteSpace($jacobianResolved)) { $validateArgs += @('--jacobian', $jacobianResolved) }
  if (-not [string]::IsNullOrWhiteSpace($skullResolved)) { $validateArgs += @('--skull-mask', $skullResolved) }
  if (-not [string]::IsNullOrWhiteSpace($outerResolved)) { $validateArgs += @('--outer-tissue-mask', $outerResolved) }
  if (-not [string]::IsNullOrWhiteSpace($layeredResolved)) { $validateArgs += @('--layered-sdf', $layeredResolved) }
}

Write-Host "[MRI+CCF] Validating inputs..."
& $PythonExe @validateArgs
if ($LASTEXITCODE -ne 0) {
  throw "MRI+CCF input validation failed (exit code $LASTEXITCODE)."
}

# If a manifest is provided, expand artifact paths into explicit environment
# variables.  This prevents stale seed/default profiles from being selected by
# backend fallback logic when the user switches MRI profiles.
if (-not [string]::IsNullOrWhiteSpace($manifestResolved)) {
  $manifestObj = Get-Content $manifestResolved -Raw | ConvertFrom-Json
  $artifactObj = $manifestObj.artifacts
  function Read-ArtifactPath([string]$name) {
    try {
      $rec = $artifactObj.$name
      if ($rec -and $rec.path -and (Test-Path ([string]$rec.path))) {
        return (Resolve-Path ([string]$rec.path)).Path
      }
    } catch {}
    return ""
  }
  if ([string]::IsNullOrWhiteSpace($annotationResolved)) { $annotationResolved = Read-ArtifactPath "annotation" }
  if ([string]::IsNullOrWhiteSpace($regionMapResolved)) { $regionMapResolved = Read-ArtifactPath "region_map" }
  if ([string]::IsNullOrWhiteSpace($statsResolved)) { $statsResolved = Read-ArtifactPath "stats" }
  if ([string]::IsNullOrWhiteSpace($jacobianResolved)) { $jacobianResolved = Read-ArtifactPath "jacobian" }
  if ([string]::IsNullOrWhiteSpace($skullResolved)) { $skullResolved = Read-ArtifactPath "skull_mask" }
  if ([string]::IsNullOrWhiteSpace($outerResolved)) { $outerResolved = Read-ArtifactPath "outer_tissue_mask" }
  if ([string]::IsNullOrWhiteSpace($layeredResolved)) { $layeredResolved = Read-ArtifactPath "layered_sdf" }
  if ([string]::IsNullOrWhiteSpace($activeProfileResolved)) { $activeProfileResolved = Split-Path -Parent $manifestResolved }
}

Set-ProcessEnv 'PROBEPLANNER_ATLAS_MODE' 'mri_ccf_hybrid'
Set-ProcessEnv 'PROBEPLANNER_MRI_CCF_MANIFEST_PATH' $manifestResolved
Set-ProcessEnv 'PROBEPLANNER_MRI_CCF_ACTIVE_PROFILE_PATH' $activeProfileResolved
Set-ProcessEnv 'PROBEPLANNER_MRI_CCF_ANNOTATION_PATH' $annotationResolved
Set-ProcessEnv 'PROBEPLANNER_MRI_CCF_REGION_MAP_PATH' $regionMapResolved
Set-ProcessEnv 'PROBEPLANNER_MRI_CCF_STATS_PATH' $statsResolved
Set-ProcessEnv 'PROBEPLANNER_MRI_CCF_JACOBIAN_PATH' $jacobianResolved
Set-ProcessEnv 'PROBEPLANNER_MRI_CCF_SKULL_MASK_PATH' $skullResolved
Set-ProcessEnv 'PROBEPLANNER_MRI_CCF_OUTER_TISSUE_MASK_PATH' $outerResolved
Set-ProcessEnv 'PROBEPLANNER_MRI_CCF_LAYERED_SDF_PATH' $layeredResolved
Set-ProcessEnv 'PROBEPLANNER_MRI_CCF_DATASET_PROFILE' $DatasetProfile
Set-ProcessEnv 'PROBEPLANNER_MRI_CCF_MEMMAP' ($(if ($NoMemmap) { 'false' } else { 'true' }))
Set-ProcessEnv 'PROBEPLANNER_MRI_CCF_DEFAULT_PROFILE_DIR' $activeProfileResolved

Set-OptionalUserEnv 'PROBEPLANNER_ATLAS_MODE' 'mri_ccf_hybrid'
Set-OptionalUserEnv 'PROBEPLANNER_MRI_CCF_MANIFEST_PATH' $manifestResolved
Set-OptionalUserEnv 'PROBEPLANNER_MRI_CCF_ACTIVE_PROFILE_PATH' $activeProfileResolved
Set-OptionalUserEnv 'PROBEPLANNER_MRI_CCF_ANNOTATION_PATH' $annotationResolved
Set-OptionalUserEnv 'PROBEPLANNER_MRI_CCF_REGION_MAP_PATH' $regionMapResolved
Set-OptionalUserEnv 'PROBEPLANNER_MRI_CCF_STATS_PATH' $statsResolved
Set-OptionalUserEnv 'PROBEPLANNER_MRI_CCF_JACOBIAN_PATH' $jacobianResolved
Set-OptionalUserEnv 'PROBEPLANNER_MRI_CCF_SKULL_MASK_PATH' $skullResolved
Set-OptionalUserEnv 'PROBEPLANNER_MRI_CCF_OUTER_TISSUE_MASK_PATH' $outerResolved
Set-OptionalUserEnv 'PROBEPLANNER_MRI_CCF_LAYERED_SDF_PATH' $layeredResolved
Set-OptionalUserEnv 'PROBEPLANNER_MRI_CCF_DATASET_PROFILE' $DatasetProfile
Set-OptionalUserEnv 'PROBEPLANNER_MRI_CCF_MEMMAP' ($(if ($NoMemmap) { 'false' } else { 'true' }))
Set-OptionalUserEnv 'PROBEPLANNER_MRI_CCF_DEFAULT_PROFILE_DIR' $activeProfileResolved

# Work around Start-Process env merge issue on Windows when both Path/PATH exist.
try {
  if ((Test-Path Env:Path) -and (Test-Path Env:PATH)) {
    Remove-Item Env:PATH -ErrorAction SilentlyContinue
  }
} catch {
  Write-Warning ("[env] failed to normalize PATH casing: {0}" -f $_.Exception.Message)
}

$existing = Get-ListeningProcIds -TargetPort $Port
foreach ($procId in $existing) {
  try {
    Write-Host "[MRI+CCF] Stopping existing process on port ${Port}: PID=$procId"
    Stop-Process -Id ([int]$procId) -Force -ErrorAction SilentlyContinue
  } catch {}
}
Start-Sleep -Milliseconds 800
$stillListening = Get-ListeningProcIds -TargetPort $Port
if ($stillListening -and $stillListening.Count -gt 0) {
  throw "Port $Port is still occupied by: $($stillListening -join ', ')"
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdout = Join-Path $ProjectRoot ("backend_mri_ccf_stdout_{0}.log" -f $stamp)
$stderr = Join-Path $ProjectRoot ("backend_mri_ccf_stderr_{0}.log" -f $stamp)

$wrapper = Join-Path $ProjectRoot ("backend_mri_ccf_start_{0}.cmd" -f $stamp)
$envLines = @(
  '@echo off',
  ('cd /d "{0}"' -f $ProjectRoot),
  'set "PROBEPLANNER_ATLAS_MODE=mri_ccf_hybrid"',
  ('set "PROBEPLANNER_MRI_CCF_MANIFEST_PATH={0}"' -f $manifestResolved),
  ('set "PROBEPLANNER_MRI_CCF_ACTIVE_PROFILE_PATH={0}"' -f $activeProfileResolved),
  ('set "PROBEPLANNER_MRI_CCF_ANNOTATION_PATH={0}"' -f $annotationResolved),
  ('set "PROBEPLANNER_MRI_CCF_REGION_MAP_PATH={0}"' -f $regionMapResolved),
  ('set "PROBEPLANNER_MRI_CCF_STATS_PATH={0}"' -f $statsResolved),
  ('set "PROBEPLANNER_MRI_CCF_JACOBIAN_PATH={0}"' -f $jacobianResolved),
  ('set "PROBEPLANNER_MRI_CCF_SKULL_MASK_PATH={0}"' -f $skullResolved),
  ('set "PROBEPLANNER_MRI_CCF_OUTER_TISSUE_MASK_PATH={0}"' -f $outerResolved),
  ('set "PROBEPLANNER_MRI_CCF_LAYERED_SDF_PATH={0}"' -f $layeredResolved),
  ('set "PROBEPLANNER_MRI_CCF_DATASET_PROFILE={0}"' -f $DatasetProfile),
  ('set "PROBEPLANNER_MRI_CCF_MEMMAP={0}"' -f ($(if ($NoMemmap) { 'false' } else { 'true' }))),
  ('set "PROBEPLANNER_MRI_CCF_DEFAULT_PROFILE_DIR={0}"' -f $activeProfileResolved),
  ('"{0}" src/optimizer_server.py 1> "{1}" 2> "{2}"' -f $PythonExe, $stdout, $stderr)
)
$envLines | Set-Content -Path $wrapper -Encoding ASCII

$p = Start-Process -FilePath $wrapper `
  -WorkingDirectory $ProjectRoot `
  -WindowStyle Hidden `
  -PassThru

Write-Host "[MRI+CCF] Backend started. PID=$($p.Id), Python=$PythonExe, Port=$Port"
Write-Host "[MRI+CCF] wrapper=$wrapper"
Write-Host "[MRI+CCF] stdout=$stdout"
Write-Host "[MRI+CCF] stderr=$stderr"

$statusUrl = "http://localhost:$Port/api/status?atlasMode=mri_ccf_hybrid&includeRegions=0"
$ready = $false
for ($i = 1; $i -le $WaitSeconds; $i++) {
  try {
    $s = Invoke-RestMethod $statusUrl -TimeoutSec 6
    $phase = [string]($s.atlasLoad.phase)
    $progress = [double]($s.atlasLoad.progress)
    $mode = [string]($s.activeAtlasMode)
    $loaded = [bool]($s.atlasLoaded)
    $err = [string]($s.atlasError)
    Write-Host ("[MRI+CCF] wait {0}/{1} mode={2} loaded={3} phase={4} progress={5:P0}" -f $i, $WaitSeconds, $mode, $loaded, $phase, $progress)
    if (-not [string]::IsNullOrWhiteSpace($err)) {
      throw "Backend reported atlasError: $err"
    }
    if ($mode -eq 'mri_ccf_hybrid' -and $loaded) {
      $ready = $true
      break
    }
  } catch {
    Write-Host "[MRI+CCF] waiting... $($_.Exception.Message)"
  }
  Start-Sleep -Seconds 1
}

if ($ready) {
  Write-Host "[MRI+CCF] Ready. Frontend atlas mode can now switch to MRI+CCF Hybrid."
} else {
  Write-Warning "[MRI+CCF] Backend did not become ready within $WaitSeconds s. Check logs:"
  Write-Warning "  $stdout"
  Write-Warning "  $stderr"
}
