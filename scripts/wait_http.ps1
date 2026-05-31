param(
    [Parameter(Mandatory = $true)]
    [string]$Url,
    [int]$TimeoutSeconds = 120,
    [int]$IntervalMilliseconds = 1000,
    [int]$ExpectedStatusCode = 200
)

$ErrorActionPreference = "Stop"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$lastError = $null

while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10
        if ([int]$response.StatusCode -eq $ExpectedStatusCode) {
            Write-Host "Ready: $Url ($($response.StatusCode))"
            if ($response.Content) {
                $response.Content
            }
            exit 0
        }
        $lastError = "Unexpected status $($response.StatusCode)"
    } catch {
        $lastError = $_.Exception.Message
    }
    Start-Sleep -Milliseconds $IntervalMilliseconds
}

throw "Timed out waiting for $Url after ${TimeoutSeconds}s. Last error: $lastError"
