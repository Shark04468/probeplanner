param(
    [string]$PythonExe = $(if ($env:PROBEPLANNER_PYTHON_EXE) { $env:PROBEPLANNER_PYTHON_EXE } else { "" }),
    [string]$ArtifactRoot = $env:PROBEPLANNER_ARTIFACT_ROOT,
    [string]$EngineHost = $(if ($env:PROBEPLANNER_ENGINE_HOST) { $env:PROBEPLANNER_ENGINE_HOST } else { "127.0.0.1" }),
    [int]$EnginePort = $(if ($env:PROBEPLANNER_ENGINE_PORT) { [int]$env:PROBEPLANNER_ENGINE_PORT } else { 7878 }),
    [switch]$UseEngineService,
    [switch]$LegacyPythonBackend,
    [switch]$NoStopExisting
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$EnableEngineService = $UseEngineService -or (-not $LegacyPythonBackend)

if (-not $NoStopExisting) {
  $portsToStop = @($EnginePort, 7878, 7879, 5000, 3000, 3100) | Select-Object -Unique
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

Start-Process -FilePath "npm" `
  -ArgumentList @("--prefix", "apps/ui", "start") `
  -WorkingDirectory $Root `
  -WindowStyle Hidden

Write-Host "Dev stack requested. UI=http://127.0.0.1:3000 Backend=http://127.0.0.1:5000 Engine=http://$EngineHost`:$EnginePort UseEngineService=$EnableEngineService"
