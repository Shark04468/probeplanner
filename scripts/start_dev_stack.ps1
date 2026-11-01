param(
    [string]$PythonExe = $(if ($env:PROBEPLANNER_PYTHON_EXE) { $env:PROBEPLANNER_PYTHON_EXE } else { "" }),
    [string]$ArtifactRoot = $env:PROBEPLANNER_ARTIFACT_ROOT,
    [string]$EngineHost = $(if ($env:PROBEPLANNER_ENGINE_HOST) { $env:PROBEPLANNER_ENGINE_HOST } else { "127.0.0.1" }),
    [int]$EnginePort = $(if ($env:PROBEPLANNER_ENGINE_PORT) { [int]$env:PROBEPLANNER_ENGINE_PORT } else { 7878 }),
    [string]$UiHost = $(if ($env:PROBEPLANNER_UI_HOST) { $env:PROBEPLANNER_UI_HOST } else { "127.0.0.1" }),
    [int]$UiPort = $(if ($env:PROBEPLANNER_UI_PORT) { [int]$env:PROBEPLANNER_UI_PORT } else { 3000 }),
    [switch]$UseEngineService,
    [switch]$LegacyPythonBackend,
    [switch]$NoStopExisting
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$LegacyBackendLauncher = Join-Path $Root "apps/backend-compat/scripts/start_backend_compat.ps1"
if (-not (Test-Path -LiteralPath $LegacyBackendLauncher -PathType Leaf)) {
  throw "Legacy combined stack is unavailable: backend launcher is absent. No processes were stopped or started. Use npm run paper:start -- --profile <prepared-profile> --run-dir <new-run> --static-dir apps/ui/build. See docs/PAPER_RUNTIME_AND_MRI_EVIDENCE.md; do not restore or whitelist a security-blocked file."
}
$EnableEngineService = $UseEngineService -or (-not $LegacyPythonBackend)

if (-not $NoStopExisting) {
  $portsToStop = @($EnginePort, 7878, 7879, 5000, $UiPort, 3000, 3100, 5173) | Select-Object -Unique
  & (Join-Path $Root "scripts\stop_local_stack.ps1") -Ports $portsToStop
}

if ($EnableEngineService) {
  $engineArgs = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $Root "scripts\run_engine.ps1"),
    "-HostName", $EngineHost,
    "-Port", "$EnginePort",
    "-KillExisting"
  )
  if (-not [string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    $engineArgs += @("-ArtifactRoot", $ArtifactRoot)
  }
  Start-Process -FilePath "powershell" `
    -ArgumentList $engineArgs `
    -WorkingDirectory $Root `
    -WindowStyle Hidden
  $engineUrl = "http://$EngineHost`:$EnginePort"
  $healthy = $false
  for ($attempt = 0; $attempt -lt 90; $attempt++) {
    try {
      $health = Invoke-RestMethod -Uri "$engineUrl/healthz" -TimeoutSec 2
      if ($health.ok -and $health.atlasLoaded -and $health.regionIndexLoaded -and $health.sdfLoaded) {
        $healthy = $true
        break
      }
    } catch {}
    Start-Sleep -Seconds 1
  }
  if (-not $healthy) {
    throw "Native planner-engine did not become healthy with atlas+SDF at $engineUrl. Start with -LegacyPythonBackend only for explicit legacy debugging."
  }
}

$backendArgs = @{
  EngineUrl = "http://$EngineHost`:$EnginePort"
}
if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
  $backendArgs.PythonExe = $PythonExe
}
if (-not [string]::IsNullOrWhiteSpace($ArtifactRoot)) {
  $backendArgs.ArtifactRoot = $ArtifactRoot
}
if ($EnableEngineService) {
  $backendArgs.UseEngineService = $true
}
if ($LegacyPythonBackend) {
  $backendArgs.LegacyPythonBackend = $true
}
& (Join-Path $Root "apps\backend-compat\scripts\start_backend_compat.ps1") @backendArgs

$logs = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$uiStdout = Join-Path $logs "ui_stdout.log"
$uiStderr = Join-Path $logs "ui_stderr.log"
foreach ($logFile in @($uiStdout, $uiStderr)) {
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
$npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCmd) {
  $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
}
if (-not $npmCmd) {
  throw "npm executable not found on PATH"
}
Start-Process -FilePath $npmCmd.Source `
  -ArgumentList @("--prefix", "apps/ui", "start", "--", "--host", $UiHost, "--port", "$UiPort", "--strictPort") `
  -WorkingDirectory $Root `
  -RedirectStandardOutput $uiStdout `
  -RedirectStandardError $uiStderr `
  -WindowStyle Hidden

$uiUrl = "http://$UiHost`:$UiPort"
$uiHealthy = $false
for ($attempt = 0; $attempt -lt 90; $attempt++) {
  try {
    $response = Invoke-WebRequest -Uri $uiUrl -TimeoutSec 2 -UseBasicParsing
    if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
      $uiHealthy = $true
      break
    }
  } catch {}
  Start-Sleep -Seconds 1
}
if (-not $uiHealthy) {
  $lastUiError = ""
  if (Test-Path $uiStderr) {
    $lastUiError = (Get-Content $uiStderr -Tail 20) -join "`n"
  }
  throw "UI dev server did not become reachable at $uiUrl. Last stderr: $lastUiError"
}

Write-Host "Dev stack ready. UI=$uiUrl Backend=http://127.0.0.1:5000 Engine=http://$EngineHost`:$EnginePort UseEngineService=$EnableEngineService"
