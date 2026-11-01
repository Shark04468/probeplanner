param(
  [string]$ProjectRoot = "",
  [string]$PythonExe = "",
  [string]$OutputDir = "",
  [switch]$StartBackend
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
  throw "ProjectRoot is required. Pass the repository root or apps/backend-compat root."
}
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
  throw "PythonExe is required."
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
  throw "OutputDir is required."
}

if (-not (Test-Path $ProjectRoot)) { throw "Project root not found: $ProjectRoot" }
if (-not (Test-Path $PythonExe)) { throw "Python executable not found: $PythonExe" }

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$repoBackendCompat = Join-Path $ProjectRoot "apps\backend-compat"
if (Test-Path (Join-Path $repoBackendCompat "scripts\build_mri_ccf_hybrid_artifacts.py")) {
  $ProjectRoot = $repoBackendCompat
}

$builder = Join-Path $ProjectRoot "scripts\build_mri_ccf_hybrid_artifacts.py"
$validator = Join-Path $ProjectRoot "scripts\validate_mri_ccf_inputs.py"
$starter = Join-Path $ProjectRoot "scripts\start_backend_mri_ccf_hybrid.ps1"
if (-not (Test-Path $builder)) {
  throw "ProjectRoot must be the repository root or apps/backend-compat root; missing builder: $builder"
}

$seedAnnotation = Join-Path $ProjectRoot ".probeplanner\mri_ccf_hybrid_seed\annotation.npy"
$seedRegionMap = Join-Path $ProjectRoot ".probeplanner\mri_ccf_hybrid_seed\region_map.json"

if (-not (Test-Path $seedAnnotation)) {
  throw "Missing seed annotation: $seedAnnotation"
}
if (-not (Test-Path $seedRegionMap)) {
  throw "Missing seed region map: $seedRegionMap"
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

Write-Host "[STEP] Build MRI+CCF artifacts (auto skull/outer/sdf)"
& $PythonExe $builder `
  --label-image $seedAnnotation `
  --region-map-json $seedRegionMap `
  --output-dir $OutputDir `
  --expected-shape "528,320,456" `
  --target-spacing-um "25,25,25" `
  --dataset-profile "public_template_auto"
if ($LASTEXITCODE -ne 0) {
  throw "Artifact build failed with exit code $LASTEXITCODE"
}

$annotation = Join-Path $OutputDir "annotation.npy"
$regionMap = Join-Path $OutputDir "region_map.json"
$stats = Join-Path $OutputDir "stats.json"
$skullMask = Join-Path $OutputDir "skull_mask.npy"
$outerMask = Join-Path $OutputDir "outer_tissue_mask.npy"
$layeredSdf = Join-Path $OutputDir "layered_sdf.npz"

Write-Host "[STEP] Validate MRI+CCF artifacts"
& $PythonExe $validator `
  --annotation $annotation `
  --region-map $regionMap `
  --stats $stats `
  --skull-mask $skullMask `
  --outer-tissue-mask $outerMask `
  --layered-sdf $layeredSdf `
  --expected-shape "528,320,456"
if ($LASTEXITCODE -ne 0) {
  throw "Artifact validation failed with exit code $LASTEXITCODE"
}

if ($StartBackend) {
  Write-Host "[STEP] Start backend in mri_ccf_hybrid mode"
  powershell -ExecutionPolicy Bypass -File $starter `
    -ProjectRoot $ProjectRoot `
    -PythonExe $PythonExe `
    -AnnotationPath $annotation `
    -RegionMapPath $regionMap `
    -StatsPath $stats `
    -SkullMaskPath $skullMask `
    -OuterTissueMaskPath $outerMask `
    -LayeredSdfPath $layeredSdf `
    -DatasetProfile "public_template_auto" `
    -WaitSeconds 180
}

Write-Host "======================================================="
Write-Host "[DONE] Public-template auto layers are ready."
Write-Host "OutputDir: $OutputDir"
Write-Host "annotation: $annotation"
Write-Host "skullMask : $skullMask"
Write-Host "outerMask : $outerMask"
Write-Host "layeredSdf: $layeredSdf"
Write-Host "======================================================="
