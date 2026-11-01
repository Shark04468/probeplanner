param(
    [Parameter(Mandatory = $true)]
    [string]$Url,
    [int]$TimeoutSeconds = 120,
    [int]$IntervalMilliseconds = 1000,
    [int]$ExpectedStatusCode = 200,
    [string[]]$RequireJsonTruthy = @()
)

$ErrorActionPreference = "Stop"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$lastError = $null

function Get-JsonPathValue {
    param(
        [Parameter(Mandatory = $true)]$InputObject,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $current = $InputObject
    foreach ($part in ($Path -split '\.')) {
        if ([string]::IsNullOrWhiteSpace($part)) { continue }
        if ($null -eq $current) { return $null }
        if ($current -is [System.Collections.IDictionary]) {
            if (-not $current.Contains($part)) { return $null }
            $current = $current[$part]
            continue
        }
        $property = $current.PSObject.Properties[$part]
        if ($null -eq $property) { return $null }
        $current = $property.Value
    }
    return $current
}

while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10
        if ([int]$response.StatusCode -eq $ExpectedStatusCode) {
            if ($RequireJsonTruthy.Count -gt 0) {
                try {
                    $json = $response.Content | ConvertFrom-Json
                } catch {
                    $lastError = "Response was not valid JSON: $($_.Exception.Message)"
                    Start-Sleep -Milliseconds $IntervalMilliseconds
                    continue
                }
                $missing = @()
                foreach ($field in $RequireJsonTruthy) {
                    $value = Get-JsonPathValue -InputObject $json -Path $field
                    if ($value -ne $true) {
                        $missing += "$field=$value"
                    }
                }
                if ($missing.Count -gt 0) {
                    $lastError = "JSON readiness fields not true: $($missing -join ', ')"
                    Start-Sleep -Milliseconds $IntervalMilliseconds
                    continue
                }
            }
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
