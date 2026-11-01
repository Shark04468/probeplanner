param(
  [string]$ProjectRoot = "",
  [string]$PythonExe = "",
  [string]$OutputDir = "",
  [string]$DatasetProfile = "ambmc_true_outer",
  [string]$DeformableMesh = "8,8,8",
  [double]$DeformableLearningRate = 0.5,
  [int]$DeformableIterations = 180,
  [double]$DeformableSampling = 0.20,
  [string]$DeformableSmoothingSigmas = "2,1,0",
  [double]$RegistrationSpacingMm = 0.10,
  [switch]$Quick,
  [switch]$SkipDeformable,
  [switch]$StartBackend
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ProjectRoot)) { throw "Project root not found: $ProjectRoot" }
if (-not (Test-Path $PythonExe)) { throw "Python executable not found: $PythonExe" }

$pipeline = Join-Path $ProjectRoot "scripts\build_ambmc_true_outer_pipeline.py"
if (-not (Test-Path $pipeline)) { throw "Pipeline script missing: $pipeline" }

$args = @(
  $pipeline,
  "--project-root", $ProjectRoot,
  "--output-dir", $OutputDir,
  "--dataset-profile", $DatasetProfile,
  "--deformable-mesh", $DeformableMesh,
  "--deformable-learning-rate", "$DeformableLearningRate",
  "--deformable-iterations", "$DeformableIterations",
  "--deformable-sampling", "$DeformableSampling",
  "--deformable-smoothing-sigmas", $DeformableSmoothingSigmas,
  "--registration-spacing-mm", "$RegistrationSpacingMm"
)
if ($Quick) { $args += "--quick" }
if ($SkipDeformable) { $args += "--skip-deformable" }

Write-Host "[STEP] Build AMBMC->CCF true outer-layer artifacts"
& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
  throw "AMBMC true-outer pipeline failed with exit code $LASTEXITCODE"
}

if ($StartBackend) {
  $starter = Join-Path $ProjectRoot "scripts\start_backend_mri_ccf_hybrid.ps1"
  Write-Host "[STEP] Start backend in mri_ccf_hybrid mode"
  powershell -NoProfile -ExecutionPolicy Bypass -File $starter `
    -ProjectRoot $ProjectRoot `
    -PythonExe $PythonExe `
    -AnnotationPath (Join-Path $OutputDir "annotation.npy") `
    -RegionMapPath (Join-Path $OutputDir "region_map.json") `
    -StatsPath (Join-Path $OutputDir "stats.json") `
    -SkullMaskPath (Join-Path $OutputDir "skull_mask.npy") `
    -OuterTissueMaskPath (Join-Path $OutputDir "outer_tissue_mask.npy") `
    -LayeredSdfPath (Join-Path $OutputDir "layered_sdf.npz") `
    -DatasetProfile $DatasetProfile `
    -WaitSeconds 240
}

Write-Host "======================================================="
Write-Host "[DONE] AMBMC true-outer pipeline finished."
Write-Host "OutputDir: $OutputDir"
Write-Host "======================================================="
