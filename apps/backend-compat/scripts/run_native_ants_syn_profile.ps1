param(
  [string]$ProjectRoot = "",
  [string]$PythonExe = "",
  [string]$AntsBinDir = "",
  [string]$ProfileDir = "",
  [string]$DatasetProfile = "ambmc_true_outer_full_jacobian_native",
  [string]$WorkDir = "",
  [string]$FixedImage = "",
  [string]$FixedAnnotation = "",
  [string]$MovingImage = "",
  [string]$MovingMask = "",
  [int]$Threads = 12,
  [double]$SynGradientStep = 0.035,
  [double]$SynUpdateFieldSigma = 4.0,
  [double]$SynTotalFieldSigma = 1.0,
  [double]$MaxJacobianAnomaly = 0.03,
  [switch]$N4,
  [switch]$SkipRegistration,
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

function AntsExe([string]$Name) {
  $p = Join-Path $AntsBinDir $Name
  if (Test-Path $p) { return (Resolve-Path $p).Path }
  throw "ANTs command not found: $p. Run scripts\\setup_native_ants.ps1 first."
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
$AntsBinDir = Require-Path $AntsBinDir "AntsBinDir"
$FixedImage = Require-Path $FixedImage "FixedImage"
$FixedAnnotation = Require-Path $FixedAnnotation "FixedAnnotation"
$MovingImage = Require-Path $MovingImage "MovingImage"
if (-not [string]::IsNullOrWhiteSpace($MovingMask)) {
  $MovingMask = Require-Path $MovingMask "MovingMask"
}

New-Item -ItemType Directory -Force -Path $WorkDir, $ProfileDir | Out-Null
$WorkDir = (Resolve-Path $WorkDir).Path
$ProfileDir = (Resolve-Path $ProfileDir).Path
$prepared = Join-Path $WorkDir "prepared"
$antsOut = Join-Path $WorkDir "ants"
New-Item -ItemType Directory -Force -Path $prepared, $antsOut | Out-Null

$prepareScript = Join-Path $ProjectRoot "scripts\prepare_ants_syn_inputs.py"
$jacScript = Join-Path $ProjectRoot "scripts\build_real_jacobian_from_warp.py"
$qualityScript = Join-Path $ProjectRoot "scripts\build_mri_quality_metrics.py"
$manifestScript = Join-Path $ProjectRoot "scripts\refresh_mri_artifact_manifest.py"
$validator = Join-Path $ProjectRoot "scripts\validate_mri_ccf_inputs.py"
$pitfallValidator = Join-Path $ProjectRoot "scripts\validate_ants_syn_pitfalls.py"
foreach ($p in @($prepareScript, $jacScript, $qualityScript, $manifestScript, $validator, $pitfallValidator)) {
  if (-not (Test-Path $p)) { throw "Missing script: $p" }
}

$registrationExe = AntsExe "antsRegistration.exe"
$jacobianExe = AntsExe "CreateJacobianDeterminantImage.exe"
$env:ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS = [string]$Threads
$env:ANTSPATH = "$AntsBinDir\"
$env:PATH = "$AntsBinDir;$env:PATH"

# Step 1: geometry-safe prepared inputs.
$prepArgs = @(
  $prepareScript,
  "--fixed-image", $FixedImage,
  "--fixed-annotation", $FixedAnnotation,
  "--moving-image", $MovingImage,
  "--output-dir", $prepared
)
if ($N4) { $prepArgs += "--n4" }
if (-not [string]::IsNullOrWhiteSpace($MovingMask)) { $prepArgs += @("--moving-mask", $MovingMask) }
Invoke-Checked -Exe $PythonExe -Args $prepArgs -StepName "Prepare CCF fixed / AMBMC moving images and masks"

$metadata = Get-Content (Join-Path $prepared "metadata.json") -Raw | ConvertFrom-Json
$fixedPrepared = [string]$metadata.outputs.fixedImage
$movingPrepared = [string]$metadata.outputs.movingImage
$fixedMaskPrepared = [string]$metadata.outputs.fixedMask
$movingMaskPrepared = [string]$metadata.outputs.movingMask

# Step 2: native ANTs registration.  We avoid shell wrappers on Windows.
$prefix = Join-Path $antsOut "ambmc_to_ccf_"
$warpPath = $prefix + "1Warp.nii.gz"
if (-not $SkipRegistration) {
  $synSpec = "SyN[$SynGradientStep,$SynUpdateFieldSigma,$SynTotalFieldSigma]"
  $argsReg = @(
    "--dimensionality", "3",
    "--float", "1",
    "--output", "[$prefix,$($prefix)Warped.nii.gz]",
    "--interpolation", "Linear",
    "--use-histogram-matching", "1",
    "--winsorize-image-intensities", "[0.005,0.995]",
    "--initial-moving-transform", "[$fixedPrepared,$movingPrepared,1]",
    "--masks", "[$fixedMaskPrepared,$movingMaskPrepared]",
    "--transform", "Rigid[0.1]",
    "--metric", "MI[$fixedPrepared,$movingPrepared,1,32,Regular,0.25]",
    "--convergence", "[1000x500x250x100,1e-6,10]",
    "--shrink-factors", "8x4x2x1",
    "--smoothing-sigmas", "3x2x1x0vox",
    "--transform", "Affine[0.1]",
    "--metric", "MI[$fixedPrepared,$movingPrepared,1,32,Regular,0.25]",
    "--convergence", "[1000x500x250x100,1e-6,10]",
    "--shrink-factors", "8x4x2x1",
    "--smoothing-sigmas", "3x2x1x0vox",
    "--transform", $synSpec,
    "--metric", "CC[$fixedPrepared,$movingPrepared,1,4]",
    "--convergence", "[120x80x40x20,1e-6,10]",
    "--shrink-factors", "8x4x2x1",
    "--smoothing-sigmas", "3x2x1x0vox",
    "--verbose", "1"
  )
  Invoke-Checked -Exe $registrationExe -Args $argsReg -StepName "Run native ANTs Rigid+Affine+SyN"
} else {
  Write-Host "[SKIP] Registration skipped; reusing $warpPath"
}
$warpPath = Require-Path $warpPath "Native ANTs 1Warp"

# Step 3: native Jacobian determinant image, explicitly non-log determinant.
$nativeJacNii = Join-Path $antsOut "jacobian_det_native.nii.gz"
Invoke-Checked -Exe $jacobianExe -Args @("3", $warpPath, $nativeJacNii, "0", "0") -StepName "Create native ANTs Jacobian determinant image"

# Step 4: planner-format Jacobian and metrics.
$jacOut = Join-Path $ProfileDir "jacobian.npy"
$statsPath = Join-Path $ProfileDir "stats.json"
$jacArgs = @($jacScript, "--warp-image", $warpPath, "--output-jacobian", $jacOut, "--expected-shape", "528,320,456")
if (Test-Path $statsPath) { $jacArgs += @("--stats", $statsPath) }
Invoke-Checked -Exe $PythonExe -Args $jacArgs -StepName "Convert warp Jacobian to planner jacobian.npy and update stats"

$qualityArgs = @($qualityScript, "--stats", $statsPath, "--registration-summary", (Join-Path $ProfileDir "registration\registration_summary.json"), "--jacobian", $jacOut)
$qualityDir = Join-Path $ProfileDir "quality_eval"
if (Test-Path (Join-Path $qualityDir "pred_mask.npy")) {
  $qualityArgs += @(
    "--pred-mask", (Join-Path $qualityDir "pred_mask.npy"),
    "--gt-mask", (Join-Path $qualityDir "gt_mask.npy"),
    "--fixed-landmarks-csv", (Join-Path $qualityDir "fixed_landmarks.csv"),
    "--warped-landmarks-csv", (Join-Path $qualityDir "warped_landmarks.csv")
  )
} elseif (Test-Path (Join-ProbePlannerMriPath "planner_artifacts\ambmc_true_outer_full\quality_eval\pred_mask.npy")) {
  $qualityArgs += @(
    "--pred-mask", (Join-ProbePlannerMriPath "planner_artifacts\ambmc_true_outer_full\quality_eval\pred_mask.npy"),
    "--gt-mask", (Join-ProbePlannerMriPath "planner_artifacts\ambmc_true_outer_full\quality_eval\gt_mask.npy"),
    "--fixed-landmarks-csv", (Join-ProbePlannerMriPath "planner_artifacts\ambmc_true_outer_full\quality_eval\fixed_landmarks.csv"),
    "--warped-landmarks-csv", (Join-ProbePlannerMriPath "planner_artifacts\ambmc_true_outer_full\quality_eval\warped_landmarks.csv")
  )
}
Invoke-Checked -Exe $PythonExe -Args $qualityArgs -StepName "Update profile quality metrics"

# Step 5: manifest and validators.
Invoke-Checked -Exe $PythonExe -Args @($manifestScript, "--profile-dir", $ProfileDir, "--profile-id", $DatasetProfile) -StepName "Refresh artifact_manifest.json"
$manifestPath = Join-Path $ProfileDir "artifact_manifest.json"
Invoke-Checked -Exe $PythonExe -Args @($validator, "--manifest", $manifestPath, "--expected-shape", "528,320,456") -StepName "Validate MRI+CCF artifact contract"
$pitfallArgs = @($pitfallValidator, "--prepared-dir", $prepared, "--profile-dir", $ProfileDir, "--max-jacobian-anomaly", ([string]$MaxJacobianAnomaly))
$pitfallOk = $true
try {
  Invoke-Checked -Exe $PythonExe -Args $pitfallArgs -StepName "Validate ANTs/SyN pitfall gates"
} catch {
  $pitfallOk = $false
  if ($AllowUnsafeJacobian) {
    Write-Warning $_.Exception.Message
  } else {
    throw
  }
}

Write-Host (@{
  ok = $pitfallOk
  antsBinDir = $AntsBinDir
  preparedDir = $prepared
  workDir = $WorkDir
  warpPath = $warpPath
  nativeJacobianNii = $nativeJacNii
  plannerJacobian = $jacOut
  manifestPath = $manifestPath
} | ConvertTo-Json -Depth 4)

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
  ) -StepName "Start backend with native ANTs profile"
}
