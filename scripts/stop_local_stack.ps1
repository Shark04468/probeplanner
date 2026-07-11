param(
    [int[]]$Ports = @(7878, 7879, 5000, 3000, 3100)
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RootPrefix = (Resolve-Path $Root).Path

function Test-ProjectOwnedProcess {
    param($ProcessInfo)
    $exe = [string]($ProcessInfo.ExecutablePath)
    $cmd = [string]($ProcessInfo.CommandLine)
    if (-not [string]::IsNullOrWhiteSpace($exe) -and $exe.StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    if (-not [string]::IsNullOrWhiteSpace($cmd)) {
        if ($cmd.IndexOf($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }
        foreach ($needle in @(
            "scripts/run_engine.ps1",
            "apps/backend-compat/scripts/start_backend_compat.ps1"
        )) {
            if ($cmd.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                return $true
            }
        }
    }
    return $false
}

foreach ($port in $Ports) {
    $connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
        Where-Object { $_.OwningProcess -and $_.OwningProcess -ne $PID } |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $connections) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        if ($proc -and (Test-ProjectOwnedProcess $proc)) {
            Write-Host "Stopping project-owned PID $procId on port $port"
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        } else {
            Write-Warning "Leaving non-ProbePlanner PID $procId on port $port untouched"
        }
    }
}
