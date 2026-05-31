param(
  [string]$ProjectRoot = "",
  [string]$PythonExe = "",
  [string]$ProfileDir = "",
  [string]$DatasetProfile = "ambmc_true_outer_full_jacobian",
  [string]$WorkDir = "",
  [string]$FixedImage = "",
  [string]$FixedAnnotation = "",
  [string]$MovingImage = "",
  [string]$MovingMask = "",
  [string]$ExistingWarpPath = "",
  [string]$AntsBinDir = "",
  [int]$Threads = 12,
  [switch]$UseExistingWarp,
  [switch]$KeepGeometry,
  [switch]$N4,
  [double]$MaxJacobianAnomaly = 0.03,
  [switch]$AllowUnsafeJacobian,
  [switch]$StartBackend,
  [int]$WaitSeconds = 240
)

$ErrorActionPreference = "Stop"

function Require-Path([string]$PathValue, [string]$Label) {
  if ([string]::IsNullOrWhiteSpace($PathValue) -or -not (Test-Path $PathValue)) {
    throw "$Label not found: $PathValue"
  }
  return (Resolve-Path $PathValue).Path
}

function Resolve-AntsCommand([string]$Name) {
  if (-not [string]::IsNullOrWhiteSpace($AntsBinDir)) {
    $candidate = Join-Path $AntsBinDir $Name
    if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
    $candidateExe = Join-Path $AntsBinDir ($Name + ".exe")
    if (Test-Path $candidateExe) { return (Resolve-Path $candidateExe).Path }
  }
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $cmdExe = Get-Command ($Name + ".exe") -ErrorAction SilentlyContinue
  if ($cmdExe) { return $cmdExe.Source }
  return ""
}

function Invoke-Checked {
  param(
    [Parameter(Mandatory=$true)][string]$Exe,
    [Parameter(Mandatory=$true)][object[]]$Args,
    [Parameter(Mandatory=$true)][string]$StepName
  )
  Write-Host "[STEP] $StepName"
  Write-Host ("       {0} {1}" -f $Exe, ($Args -join " "))
  & $Exe @Args
  if ($LASTEXITCODE -ne 0) {
    throw "$StepName failed with exit code $LASTEXITCODE"
  }
}

$ProjectRoot = Require-Path $ProjectRoot "ProjectRoot"
$PythonExe = Require-Path $PythonExe "PythonExe"
$FixedImage = Require-Path $FixedImage "FixedImage"
$FixedAnnotation = Require-Path $FixedAnnotation "FixedAnnotation"
$MovingImage = Require-Path $MovingImage "MovingImage"
if (-not [string]::IsNullOrWhiteSpace($MovingMask)) {
  $MovingMask = Require-Path $MovingMask "MovingMask"
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
$WorkDir = (Resolve-Path $WorkDir).Path
$ProfileDir = (Resolve-Path $ProfileDir).Path
$prepared = Join-Path $WorkDir "prepared"
$antsOut = Join-Path $WorkDir "ants"
New-Item -ItemType Directory -Force -Path $prepared | Out-Null
New-Item -ItemType Directory -Force -Path $antsOut | Out-Null

$prepareScript = Join-Path $ProjectRoot "scripts\prepare_ants_syn_inputs.py"
$jacScript = Join-Path $ProjectRoot "scripts\build_real_jacobian_from_warp.py"
$qualityScript = Join-Path $ProjectRoot "scripts\build_mri_quality_metrics.py"
$manifestScript = Join-Path $ProjectRoot "scripts\refresh_mri_artifact_manifest.py"
$validator = Join-Path $ProjectRoot "scripts\validate_mri_ccf_inputs.py"
foreach ($p in @($prepareScript, $jacScript, $qualityScript, $manifestScript, $validator)) {
  if (-not (Test-Path $p)) { throw "Missing script: $p" }
}

# Step 1: prepare geometry-safe ANTs inputs.
$prepArgs = @(
  $prepareScript,
  "--fixed-image", $FixedImage,
  "--fixed-annotation", $FixedAnnotation,
  "--moving-image", $MovingImage,
  "--output-dir", $prepared
)
if (-not [string]::IsNullOrWhiteSpace($MovingMask)) { $prepArgs += @("--moving-mask", $MovingMask) }
if ($KeepGeometry) { $prepArgs += "--keep-geometry" }
if ($N4) { $prepArgs += "--n4" }
Invoke-Checked -Exe $PythonExe -Args $prepArgs -StepName "Prepare ANTs inputs and masks"

$metadata = Get-Content (Join-Path $prepared "metadata.json") -Raw | ConvertFrom-Json
$fixedPrepared = [string]$metadata.outputs.fixedImage
$movingPrepared = [string]$metadata.outputs.movingImage
$fixedMaskPrepared = [string]$metadata.outputs.fixedMask
$movingMaskPrepared = [string]$metadata.outputs.movingMask

# Step 2: run SyN or reuse an existing verified displacement field.
if ([string]::IsNullOrWhiteSpace($ExistingWarpPath)) {
  $candidateExisting = Join-Path $ProfileDir "registration\ambmc_to_ccf_displacement.nii.gz"
  if (Test-Path $candidateExisting) { $ExistingWarpPath = $candidateExisting }
}

$warpPath = ""
if ($UseExistingWarp -and -not [string]::IsNullOrWhiteSpace($ExistingWarpPath)) {
  $warpPath = Require-Path $ExistingWarpPath "ExistingWarpPath"
  Write-Host "[STEP] Reusing existing deformable warp: $warpPath"
} else {
  $antsSyn = Resolve-AntsCommand "antsRegistrationSyN.sh"
  if ([string]::IsNullOrWhiteSpace($antsSyn)) {
    throw @"
ANTs command antsRegistrationSyN.sh was not found.
Install ANTs in WSL/Linux or set -AntsBinDir, then rerun.
If you already have a valid SyN warp, rerun with:
  -UseExistingWarp -ExistingWarpPath "<path-to-1Warp.nii.gz-or-displacement.nii.gz>"
"@
  }
  $prefix = Join-Path $antsOut "ambmc_to_ccf_"
  $env:ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS = [string]$Threads
  $antsArgs = @(
    "-d", "3",
    "-f", $fixedPrepared,
    "-m", $movingPrepared,
    "-o", $prefix,
    "-t", "s",
    "-n", ([string]$Threads),
    "-x", ($fixedMaskPrepared + "," + $movingMaskPrepared)
  )
  Invoke-Checked -Exe $antsSyn -Args $antsArgs -StepName "Run ANTs SyN deformable registration"
  $warpCandidate = $prefix + "1Warp.nii.gz"
  $warpPath = Require-Path $warpCandidate "ANTs SyN warp"
}

# Step 3/4: generate real Jacobian determinant and reject constant placeholder output.
$jacOut = Join-Path $ProfileDir "jacobian.npy"
$statsPath = Join-Path $ProfileDir "stats.json"
$jacArgs = @($jacScript, "--warp-image", $warpPath, "--output-jacobian", $jacOut, "--expected-shape", "528,320,456")
if (Test-Path $statsPath) { $jacArgs += @("--stats", $statsPath) }
Invoke-Checked -Exe $PythonExe -Args $jacArgs -StepName "Build Jacobian determinant from warp"

$jacCheck = & $PythonExe -c "import numpy as np, sys; j=np.load(r'$jacOut', mmap_mode='r'); s=np.asarray(j[::32,::32,::32], dtype='float32'); print(float(s.mean()), float(s.std()), float(s.min()), float(s.max())); sys.exit(0 if float(s.std()) > 1e-6 and float(s.max()-s.min()) > 1e-6 else 3)"
Write-Host "[CHECK] Jacobian sample mean/std/min/max: $jacCheck"
if ($LASTEXITCODE -ne 0) {
  throw "Jacobian still looks constant. This indicates an affine/identity transform, wrong warp file, or failed deformable registration."
}

# Step 5: refill real quality metrics when available.
$qualityArgs = @($qualityScript, "--stats", $statsPath, "--registration-summary", (Join-Path $ProfileDir "registration\registration_summary.json"), "--jacobian", $jacOut)
$localQuality = Join-Path $ProfileDir "quality_eval"
$fallbackQuality = (Join-ProbePlannerMriPath "planner_artifacts\ambmc_true_outer_full\quality_eval")
if (Test-Path (Join-Path $localQuality "pred_mask.npy")) {
  $qualityArgs += @(
    "--pred-mask", (Join-Path $localQuality "pred_mask.npy"),
    "--gt-mask", (Join-Path $localQuality "gt_mask.npy"),
    "--fixed-landmarks-csv", (Join-Path $localQuality "fixed_landmarks.csv"),
    "--warped-landmarks-csv", (Join-Path $localQuality "warped_landmarks.csv")
  )
} elseif (Test-Path (Join-Path $fallbackQuality "pred_mask.npy")) {
  $qualityArgs += @(
    "--pred-mask", (Join-Path $fallbackQuality "pred_mask.npy"),
    "--gt-mask", (Join-Path $fallbackQuality "gt_mask.npy"),
    "--fixed-landmarks-csv", (Join-Path $fallbackQuality "fixed_landmarks.csv"),
    "--warped-landmarks-csv", (Join-Path $fallbackQuality "warped_landmarks.csv")
  )
}
Invoke-Checked -Exe $PythonExe -Args $qualityArgs -StepName "Update stats.json with real quality metrics"

$statsObj = Get-Content $statsPath -Raw | ConvertFrom-Json
$jacAnomaly = $null
try {
  $jacAnomaly = [double]$statsObj.metrics.jacobianAnomalyRatio
} catch {}
if ($null -ne $jacAnomaly -and $jacAnomaly -gt $MaxJacobianAnomaly) {
  $msg = "Jacobian anomaly ratio $jacAnomaly exceeds max $MaxJacobianAnomaly. This suggests local folding/overfit; rerun with stronger regularization or better masks."
  if ($AllowUnsafeJacobian) {
    Write-Warning $msg
  } else {
    throw ($msg + " Use -AllowUnsafeJacobian only for visualization/debug, not strict planning.")
  }
}

# Step 6: refresh strict manifest and validate the profile contract.
Invoke-Checked -Exe $PythonExe -Args @($manifestScript, "--profile-dir", $ProfileDir, "--profile-id", $DatasetProfile) -StepName "Refresh artifact_manifest.json"
$manifestPath = Join-Path $ProfileDir "artifact_manifest.json"
Invoke-Checked -Exe $PythonExe -Args @($validator, "--manifest", $manifestPath, "--expected-shape", "528,320,456") -StepName "Validate MRI+CCF profile"

$summary = @{
  ok = $true
  profileDir = $ProfileDir
  profileId = $DatasetProfile
  manifestPath = $manifestPath
  warpPath = $warpPath
  jacobianPath = $jacOut
  preparedDir = $prepared
}
Write-Host ($summary | ConvertTo-Json -Depth 4)

if ($StartBackend) {
  $starter = Join-Path $ProjectRoot "scripts\start_backend_mri_ccf_hybrid.ps1"
  Invoke-Checked -Exe "powershell" -Args @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $starter,
    "-ProjectRoot", $ProjectRoot,
    "-PythonExe", $PythonExe,
    "-ManifestPath", $manifestPath,
    "-ActiveProfilePath", $ProfileDir,
    "-DatasetProfile", $DatasetProfile,
    "-WaitSeconds", ([string]$WaitSeconds)
  ) -StepName "Start backend with validated MRI+CCF profile"
}
