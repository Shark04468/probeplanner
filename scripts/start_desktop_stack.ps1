param(
    [string]$ArtifactRoot = $env:PROBEPLANNER_ARTIFACT_ROOT,
    [int]$EnginePort = 7878,
    [int]$BackendPort = 5000,
    [int]$UiPort = 3100,
    [switch]$NoUiServer
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$LegacyBackendLauncher = Join-Path $Root "apps/backend-compat/scripts/start_backend_compat.ps1"
if (-not (Test-Path -LiteralPath $LegacyBackendLauncher -PathType Leaf)) {
    throw "Legacy desktop stack is unavailable: backend launcher is absent. No processes were stopped or started. Use npm run paper:start -- --profile <prepared-profile> --run-dir <new-run> --static-dir apps/ui/build. See docs/PAPER_RUNTIME_AND_MRI_EVIDENCE.md; do not restore or whitelist a security-blocked file."
}

if ([string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    throw "PROBEPLANNER_ARTIFACT_ROOT is required for the desktop stack."
}
if (-not (Test-Path $ArtifactRoot)) {
    throw "Artifact root not found: $ArtifactRoot"
}

& (Join-Path $PSScriptRoot "stop_local_stack.ps1") -Ports @($EnginePort, $BackendPort, $UiPort)

Start-Process -FilePath powershell -WindowStyle Hidden -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "run_engine.ps1"),
    "-ArtifactRoot", $ArtifactRoot, "-Port", $EnginePort, "-KillExisting"
)
& (Join-Path $PSScriptRoot "wait_http.ps1") -Url "http://127.0.0.1:$EnginePort/healthz" -TimeoutSeconds 180 -RequireJsonTruthy ok,atlasLoaded,sdfLoaded,riskSdfReady,regionIndexLoaded

Start-Process -FilePath powershell -WindowStyle Hidden -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "apps/backend-compat/scripts/start_backend_compat.ps1"),
    "-ArtifactRoot", $ArtifactRoot, "-Port", $BackendPort, "-EngineUrl", "http://127.0.0.1:$EnginePort", "-UseEngineService"
)
& (Join-Path $PSScriptRoot "wait_http.ps1") -Url "http://127.0.0.1:$BackendPort/api/status" -TimeoutSeconds 240 -RequireJsonTruthy ok,nativeEngineReady

if (-not $NoUiServer) {
    Start-Process -FilePath python -WindowStyle Hidden -ArgumentList @(
        "-m", "http.server", [string]$UiPort, "--directory", (Join-Path $Root "apps/ui/build")
    )
    & (Join-Path $PSScriptRoot "wait_http.ps1") -Url "http://127.0.0.1:$UiPort" -TimeoutSeconds 120
}

Write-Host "ProbePlanner native desktop stack is ready."
Write-Host "Engine:  http://127.0.0.1:$EnginePort"
Write-Host "Backend: http://127.0.0.1:$BackendPort"
if (-not $NoUiServer) { Write-Host "UI:      http://127.0.0.1:$UiPort" }
