param(
  [string]$QiuRoot = "",
  [string]$ProjectRoot = "",
  [string]$PythonExe = "",
  [int]$Port = 5000,
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

function Pick-First([string]$dir, [string[]]$patterns) {
  foreach ($pat in $patterns) {
    $hit = Get-ChildItem -Path $dir -File -Filter $pat -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $hit) { return $hit.FullName }
  }
  return ""
}

function Require-Path([string]$path, [string]$message) {
  if (-not (Test-Path $path)) { throw $message }
}

Require-Path $ProjectRoot "ProjectRoot not found: $ProjectRoot"
Require-Path $PythonExe "Python executable not found: $PythonExe"

$inputDir = Join-Path $QiuRoot "input"
$outputDir = Join-Path $QiuRoot "output"
$logDir = Join-Path $QiuRoot "logs"
New-Item -Path $QiuRoot -ItemType Directory -Force | Out-Null
New-Item -Path $inputDir -ItemType Directory -Force | Out-Null
New-Item -Path $outputDir -ItemType Directory -Force | Out-Null
New-Item -Path $logDir -ItemType Directory -Force | Out-Null

$labelPath = Pick-First $inputDir @(
  "*label*.nii.gz", "*annotation*.nii.gz", "*label*.nii", "*annotation*.nii",
  "*label*.nrrd", "*annotation*.nrrd",
  "*label*.mnc", "*annotation*.mnc", "*label*.mha", "*annotation*.mha",
  "*label*.npy", "*annotation*.npy", "*label*.npz", "*annotation*.npz"
)
$referencePath = Pick-First $inputDir @(
  "*reference*.nii.gz", "*template*.nii.gz", "*atlas*.nii.gz",
  "*reference*.nii", "*template*.nii", "*atlas*.nii",
  "*reference*.nrrd", "*template*.nrrd", "*atlas*.nrrd"
)
$gtLabelPath = Pick-First $inputDir @(
  "*gt*label*.nii.gz", "*label*gt*.nii.gz", "*ground*truth*.nii.gz",
  "*gt*label*.nii", "*label*gt*.nii",
  "*gt*label*.nrrd", "*label*gt*.nrrd", "*ground*truth*.nrrd"
)
$jacobianPath = Pick-First $inputDir @(
  "*jacob*.nii.gz", "*jacobian*.nii.gz", "*jacob*.nii", "*jacobian*.nii",
  "*jacob*.nrrd", "*jacobian*.nrrd",
  "*jacob*.npy", "*jacobian*.npy", "*jacob*.npz", "*jacobian*.npz"
)
$warpPath = Pick-First $inputDir @(
  "*warp*.nii.gz", "*deform*.nii.gz", "*field*.nii.gz",
  "*warp*.nii", "*deform*.nii", "*field*.nii",
  "*warp*.nrrd", "*deform*.nrrd", "*field*.nrrd"
)
$fixedLmPath = Pick-First $inputDir @("*fixed*landmark*.csv", "*landmark*fixed*.csv", "*fixed*.csv")
$warpedLmPath = Pick-First $inputDir @("*warped*landmark*.csv", "*landmark*warped*.csv", "*moving*landmark*.csv", "*warped*.csv")
$regionMapJson = Pick-First $inputDir @("*region*map*.json", "*acronym*id*.json")
$regionMapCsv = Pick-First $inputDir @("*region*map*.csv", "*acronym*id*.csv")

if ([string]::IsNullOrWhiteSpace($labelPath)) {
  $readmeCandidates = @(
    (Join-Path $QiuRoot "README.txt"),
    (Join-Path $QiuRoot "README_inputs.txt"),
    (Join-Path $QiuRoot "README_placement.txt")
  )
  $readmePath = $readmeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
  if ([string]::IsNullOrWhiteSpace($readmePath)) {
    $readmePath = $readmeCandidates[0]
  }
  Write-Host "[QiuAuto] No label volume found in $inputDir" -ForegroundColor Yellow
  Write-Host "[QiuAuto] Please place one file, e.g. label.nii.gz, then rerun." -ForegroundColor Yellow
  Write-Host "[QiuAuto] README: $readmePath" -ForegroundColor Yellow
  exit 2
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$artifactDir = Join-Path $outputDir ("artifacts_" + $stamp)
New-Item -Path $artifactDir -ItemType Directory -Force | Out-Null

$buildScript = Join-Path $ProjectRoot "scripts\build_qiu2018_artifacts.py"
$validateScript = Join-Path $ProjectRoot "scripts\validate_qiu2018_inputs.py"
$startScript = Join-Path $ProjectRoot "scripts\start_backend_qiu2018.ps1"
Require-Path $buildScript "Missing script: $buildScript"
Require-Path $validateScript "Missing script: $validateScript"
Require-Path $startScript "Missing script: $startScript"

$buildArgs = @(
  $buildScript,
  "--label-image", $labelPath,
  "--output-dir", $artifactDir,
  "--expected-shape", "528,320,456",
  "--target-spacing-um", "25,25,25"
)
if (-not [string]::IsNullOrWhiteSpace($referencePath)) { $buildArgs += @("--reference-image", $referencePath) }
if (-not [string]::IsNullOrWhiteSpace($gtLabelPath)) { $buildArgs += @("--gt-label-image", $gtLabelPath) }
if (-not [string]::IsNullOrWhiteSpace($jacobianPath)) {
  $buildArgs += @("--jacobian-image", $jacobianPath)
} elseif (-not [string]::IsNullOrWhiteSpace($warpPath)) {
  $buildArgs += @("--warp-field", $warpPath)
}
if ((-not [string]::IsNullOrWhiteSpace($fixedLmPath)) -and (-not [string]::IsNullOrWhiteSpace($warpedLmPath))) {
  $buildArgs += @("--fixed-landmarks-csv", $fixedLmPath, "--warped-landmarks-csv", $warpedLmPath)
}
if (-not [string]::IsNullOrWhiteSpace($regionMapJson)) {
  $buildArgs += @("--region-map-json", $regionMapJson)
} elseif (-not [string]::IsNullOrWhiteSpace($regionMapCsv)) {
  $buildArgs += @("--region-map-csv", $regionMapCsv)
} else {
  $buildArgs += @("--emit-rid-region-map")
}

$buildLog = Join-Path $logDir ("build_qiu2018_" + $stamp + ".log")
Write-Host "[QiuAuto] Build artifacts..." -ForegroundColor Cyan
& $PythonExe @buildArgs 2>&1 | Tee-Object -FilePath $buildLog
if ($LASTEXITCODE -ne 0) { throw "build_qiu2018_artifacts failed. See $buildLog" }

$annotationOut = Join-Path $artifactDir "annotation.npy"
$statsOut = Join-Path $artifactDir "stats.json"
$jacOut = Join-Path $artifactDir "jacobian.npy"
$mapOut = Join-Path $artifactDir "region_map.json"

Require-Path $annotationOut "Generated annotation not found: $annotationOut"

$validateArgs = @(
  $validateScript,
  "--annotation", $annotationOut,
  "--expected-shape", "528,320,456"
)
if (Test-Path $statsOut) { $validateArgs += @("--stats", $statsOut) }
if (Test-Path $jacOut) { $validateArgs += @("--jacobian", $jacOut) }
if (Test-Path $mapOut) { $validateArgs += @("--region-map", $mapOut) }

$validateLog = Join-Path $logDir ("validate_qiu2018_" + $stamp + ".log")
Write-Host "[QiuAuto] Validate artifacts..." -ForegroundColor Cyan
& $PythonExe @validateArgs 2>&1 | Tee-Object -FilePath $validateLog
if ($LASTEXITCODE -ne 0) { throw "validate_qiu2018_inputs failed. See $validateLog" }

$startArgs = @{
  ProjectRoot = $ProjectRoot
  PythonExe = $PythonExe
  AnnotationPath = $annotationOut
  Port = $Port
}
if (Test-Path $statsOut) { $startArgs["StatsPath"] = $statsOut }
if (Test-Path $jacOut) { $startArgs["JacobianPath"] = $jacOut }
if (Test-Path $mapOut) { $startArgs["RegionMapPath"] = $mapOut }
if ($PersistUserEnv) { $startArgs["PersistUserEnv"] = $true }

$startLog = Join-Path $logDir ("start_qiu2018_" + $stamp + ".log")
Write-Host "[QiuAuto] Start backend in Qiu2018 mode..." -ForegroundColor Cyan
& $startScript @startArgs 2>&1 | Tee-Object -FilePath $startLog
if ($LASTEXITCODE -ne 0) { throw "start_backend_qiu2018 failed. See $startLog" }

try {
  $status = Invoke-RestMethod "http://localhost:$Port/api/status?atlasMode=qiu2018" -TimeoutSec 10
  Write-Host ("[QiuAuto] activeAtlasMode={0} atlasLoaded={1} regionCount={2} plannerRegionCount={3}" -f `
      $status.activeAtlasMode, $status.atlasLoaded, $status.regionCount, $status.plannerRegionCount) -ForegroundColor Green
} catch {
  Write-Warning "[QiuAuto] Could not fetch /api/status. Please check backend logs."
}

Write-Host "[QiuAuto] Done." -ForegroundColor Green
Write-Host "[QiuAuto] Artifacts: $artifactDir"
Write-Host "[QiuAuto] Logs: $logDir"
