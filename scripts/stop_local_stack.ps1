param(
    [int[]]$Ports = @(7878, 5000, 3100)
)

$ErrorActionPreference = "Stop"

foreach ($port in $Ports) {
    $connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
        Where-Object { $_.OwningProcess -and $_.OwningProcess -ne $PID } |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $connections) {
        Write-Host "Stopping PID $procId on port $port"
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}
