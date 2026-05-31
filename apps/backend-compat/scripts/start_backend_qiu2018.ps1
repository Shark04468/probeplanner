param(
  [string]$ProjectRoot = "",
  [string]$PythonExe = "",
  [string]$AnnotationPath,
  [string]$RegionMapPath = "",
  [string]$StatsPath = "",
  [string]$JacobianPath = "",
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

if (-not (Test-Path $PythonExe)) {
  throw "Python executable not found at $PythonExe"
}
if (-not (Test-Path $ProjectRoot)) {
  throw "Project root not found: $ProjectRoot"
}
if ([string]::IsNullOrWhiteSpace($AnnotationPath)) {
  throw "AnnotationPath is required. Provide Qiu2018 annotation .npy/.npz"
}

$annotationResolved = Resolve-OptionalPath $AnnotationPath
$regionMapResolved = Resolve-OptionalPath $RegionMapPath
$statsResolved = Resolve-OptionalPath $StatsPath
$jacobianResolved = Resolve-OptionalPath $JacobianPath

$validator = Join-Path $ProjectRoot 'scripts\validate_qiu2018_inputs.py'
if (-not (Test-Path $validator)) {
  throw "Validator script not found: $validator"
}

$validateArgs = @(
  $validator,
  '--annotation', $annotationResolved,
  '--expected-shape', '528,320,456'
)
if (-not [string]::IsNullOrWhiteSpace($regionMapResolved)) { $validateArgs += @('--region-map', $regionMapResolved) }
if (-not [string]::IsNullOrWhiteSpace($statsResolved)) { $validateArgs += @('--stats', $statsResolved) }
if (-not [string]::IsNullOrWhiteSpace($jacobianResolved)) { $validateArgs += @('--jacobian', $jacobianResolved) }
if ($NoMemmap) { $validateArgs += '--no-memmap' }

Write-Host "[Qiu2018] Validating inputs..."
& $PythonExe @validateArgs
if ($LASTEXITCODE -ne 0) {
  throw "Qiu2018 input validation failed (exit code $LASTEXITCODE)."
}

Set-ProcessEnv 'PROBEPLANNER_ATLAS_MODE' 'qiu2018'
Set-ProcessEnv 'PROBEPLANNER_QIU2018_ANNOTATION_PATH' $annotationResolved
Set-ProcessEnv 'PROBEPLANNER_QIU2018_REGION_MAP_PATH' $regionMapResolved
Set-ProcessEnv 'PROBEPLANNER_QIU2018_STATS_PATH' $statsResolved
Set-ProcessEnv 'PROBEPLANNER_QIU2018_JACOBIAN_PATH' $jacobianResolved
Set-ProcessEnv 'PROBEPLANNER_QIU2018_MEMMAP' ($(if ($NoMemmap) { 'false' } else { 'true' }))

Set-OptionalUserEnv 'PROBEPLANNER_ATLAS_MODE' 'qiu2018'
Set-OptionalUserEnv 'PROBEPLANNER_QIU2018_ANNOTATION_PATH' $annotationResolved
Set-OptionalUserEnv 'PROBEPLANNER_QIU2018_REGION_MAP_PATH' $regionMapResolved
Set-OptionalUserEnv 'PROBEPLANNER_QIU2018_STATS_PATH' $statsResolved
Set-OptionalUserEnv 'PROBEPLANNER_QIU2018_JACOBIAN_PATH' $jacobianResolved
Set-OptionalUserEnv 'PROBEPLANNER_QIU2018_MEMMAP' ($(if ($NoMemmap) { 'false' } else { 'true' }))

$existing = netstat -ano | Select-String ":$Port\s+.*LISTENING" | ForEach-Object {
  ($_ -split '\s+')[-1]
} | Where-Object { $_ -match '^\d+$' } | Select-Object -Unique

foreach ($procId in $existing) {
  try { Stop-Process -Id ([int]$procId) -Force -ErrorAction SilentlyContinue } catch {}
}

$stdout = Join-Path $ProjectRoot 'backend_qiu2018_stdout.log'
$stderr = Join-Path $ProjectRoot 'backend_qiu2018_stderr.log'
if (Test-Path $stdout) { Remove-Item $stdout -Force }
if (Test-Path $stderr) { Remove-Item $stderr -Force }

$p = Start-Process -FilePath $PythonExe `
  -ArgumentList @('src/optimizer_server.py') `
  -WorkingDirectory $ProjectRoot `
  -RedirectStandardOutput $stdout `
  -RedirectStandardError $stderr `
  -PassThru

Write-Host "[Qiu2018] Backend started. PID=$($p.Id), Python=$PythonExe, Port=$Port"
Write-Host "[Qiu2018] stdout=$stdout"
Write-Host "[Qiu2018] stderr=$stderr"

$statusUrl = "http://localhost:$Port/api/status?atlasMode=qiu2018"
$ready = $false
for ($i = 1; $i -le $WaitSeconds; $i++) {
  try {
    $s = Invoke-RestMethod $statusUrl -TimeoutSec 6
    $phase = [string]($s.atlasLoad.phase)
    $progress = [double]($s.atlasLoad.progress)
    $mode = [string]($s.activeAtlasMode)
    $loaded = [bool]($s.atlasLoaded)
    $err = [string]($s.atlasError)
    Write-Host ("[Qiu2018] wait {0}/{1} mode={2} loaded={3} phase={4} progress={5:P0}" -f $i, $WaitSeconds, $mode, $loaded, $phase, $progress)
    if (-not [string]::IsNullOrWhiteSpace($err)) {
      throw "Backend reported atlasError: $err"
    }
    if ($mode -eq 'qiu2018' -and $loaded) {
      $ready = $true
      break
    }
  } catch {
    Write-Host "[Qiu2018] waiting... $($_.Exception.Message)"
  }
  Start-Sleep -Seconds 1
}

if ($ready) {
  Write-Host "[Qiu2018] Ready. You can now switch frontend Atlas mode to Qiu2018 and run optimization."
} else {
  Write-Warning "[Qiu2018] Backend did not become ready within $WaitSeconds s. Check logs:"
  Write-Warning "  $stdout"
  Write-Warning "  $stderr"
}

