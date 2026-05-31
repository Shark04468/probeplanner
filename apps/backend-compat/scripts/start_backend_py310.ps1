param(
  [string]$ProjectRoot = "",
  [string]$PythonExe = "",
  [int]$Port = 5000
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
if (-not (Test-Path $PythonExe)) {
  throw "Python executable not found at $PythonExe"
}

$existing = netstat -ano | Select-String ":$Port\s+.*LISTENING" | ForEach-Object {
  ($_ -split '\s+')[-1]
} | Where-Object { $_ -match '^\d+$' } | Select-Object -Unique

foreach ($procId in $existing) {
  try { Stop-Process -Id ([int]$procId) -Force -ErrorAction SilentlyContinue } catch {}
}

$stdout = Join-Path $ProjectRoot 'backend_live_stdout.log'
$stderr = Join-Path $ProjectRoot 'backend_live_stderr.log'
if (Test-Path $stdout) { Remove-Item $stdout -Force }
if (Test-Path $stderr) { Remove-Item $stderr -Force }

$p = Start-Process -FilePath $PythonExe `
  -ArgumentList @('src/optimizer_server.py') `
  -WorkingDirectory $ProjectRoot `
  -RedirectStandardOutput $stdout `
  -RedirectStandardError $stderr `
  -PassThru

Write-Host "Backend started. PID=$($p.Id), Python=$PythonExe, Port=$Port"
Write-Host "stdout=$stdout"
Write-Host "stderr=$stderr"
