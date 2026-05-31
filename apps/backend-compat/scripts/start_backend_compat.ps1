param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
  [string]$PythonExe = $(if ($env:PROBEPLANNER_PYTHON_EXE) { $env:PROBEPLANNER_PYTHON_EXE } else { "" }),
  [int]$Port = 5000,
  [string]$EngineUrl = "http://127.0.0.1:7878",
  [string]$ArtifactRoot = $env:PROBEPLANNER_ARTIFACT_ROOT,
  [string]$MriProfileName = $(if ($env:PROBEPLANNER_MRI_CCF_PROFILE_NAME) { $env:PROBEPLANNER_MRI_CCF_PROFILE_NAME } else { "ambmc_true_outer_full" }),
  [switch]$UseEngineService,
  [switch]$LegacyPythonBackend
)

$ErrorActionPreference = "Stop"
$EnableEngineService = $UseEngineService -or (-not $LegacyPythonBackend)
if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path $PythonExe)) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if (-not $cmd) { throw "Python executable not found at $PythonExe and python is not on PATH" }
  $PythonExe = $cmd.Source
}

$existing = netstat -ano | Select-String ":$Port\s+.*LISTENING" | ForEach-Object {
  ($_ -split "\s+")[-1]
} | Where-Object { $_ -match "^\d+$" } | Select-Object -Unique

foreach ($procId in $existing) {
  try { Stop-Process -Id ([int]$procId) -Force -ErrorAction SilentlyContinue } catch {}
}
foreach ($procId in $existing) {
  try {
    Wait-Process -Id ([int]$procId) -Timeout 10 -ErrorAction SilentlyContinue
  } catch {}
}

$logs = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$stdout = Join-Path $logs "backend_stdout.log"
$stderr = Join-Path $logs "backend_stderr.log"
foreach ($logFile in @($stdout, $stderr)) {
  for ($attempt = 0; $attempt -lt 10; $attempt++) {
    try {
      if (Test-Path $logFile) { Remove-Item $logFile -Force }
      break
    } catch {
      if ($attempt -eq 9) { throw }
      Start-Sleep -Milliseconds 300
    }
  }
}

$env:PROBEPLANNER_ENGINE_URL = $EngineUrl
if (-not [string]::IsNullOrWhiteSpace($ArtifactRoot)) {
  $resolvedArtifactRoot = (Resolve-Path $ArtifactRoot).Path
  $env:PROBEPLANNER_ARTIFACT_ROOT = $resolvedArtifactRoot
  if ([string]::IsNullOrWhiteSpace($env:PROBEPLANNER_MRI_CCF_DEFAULT_PROFILE_DIR)) {
    $manifestInRoot = Join-Path $resolvedArtifactRoot "artifact_manifest.json"
    if ((Test-Path $manifestInRoot) -and (Test-Path (Join-Path $resolvedArtifactRoot "annotation.npy"))) {
      $env:PROBEPLANNER_MRI_CCF_DEFAULT_PROFILE_DIR = $resolvedArtifactRoot
      $env:PROBEPLANNER_ATLAS_MODE = "mri_ccf_hybrid"
    } elseif (-not [string]::IsNullOrWhiteSpace($MriProfileName)) {
      $profileDir = Join-Path $resolvedArtifactRoot $MriProfileName
      if (Test-Path $profileDir) {
        $env:PROBEPLANNER_MRI_CCF_DEFAULT_PROFILE_DIR = (Resolve-Path $profileDir).Path
        $env:PROBEPLANNER_ATLAS_MODE = "mri_ccf_hybrid"
      }
    }
  }
}
if ($EnableEngineService) {
  $env:PROBEPLANNER_USE_ENGINE_SERVICE = "1"
} else {
  Remove-Item Env:\PROBEPLANNER_USE_ENGINE_SERVICE -ErrorAction SilentlyContinue
}

$p = Start-Process -FilePath $PythonExe `
  -ArgumentList @("src/optimizer_server.py") `
  -WorkingDirectory $ProjectRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdout `
  -RedirectStandardError $stderr `
  -PassThru

Write-Host "Backend compat started. PID=$($p.Id), Python=$PythonExe, Port=$Port"
Write-Host "ProjectRoot=$ProjectRoot"
Write-Host "EngineUrl=$EngineUrl UseEngineService=$EnableEngineService"
Write-Host "stdout=$stdout"
Write-Host "stderr=$stderr"
