function Get-ProbePlannerListeningProcIds {
  param([int]$TargetPort)
  $out = @()
  $netstatCmd = Get-Command netstat -ErrorAction SilentlyContinue
  if ($netstatCmd) {
    try {
      $lines = & $netstatCmd.Source -ano | Select-String ":$TargetPort" | Select-String "LISTENING"
      foreach ($line in $lines) {
        $parts = ($line.ToString() -split "\s+") | Where-Object { $_ -ne "" }
        if ($parts.Count -lt 5) { continue }
        $candidate = $parts[-1]
        if ($candidate -match "^\d+$") { $out += [int]$candidate }
      }
    } catch {}
  }
  try {
    $connections = Get-NetTCPConnection -State Listen -LocalPort $TargetPort -ErrorAction Stop
    foreach ($connection in $connections) {
      if ($connection.OwningProcess -is [int]) { $out += [int]$connection.OwningProcess }
    }
  } catch {}
  return ($out | Select-Object -Unique)
}

function Get-ProbePlannerProcessInfo {
  param([int]$ProcessId)
  try {
    return Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop |
      Select-Object ProcessId, Name, ExecutablePath, CommandLine
  } catch {
    return $null
  }
}

function Test-ProbePlannerOwnedProcess {
  param(
    $ProcessInfo,
    [string]$ProjectRoot
  )
  if (-not $ProcessInfo) { return $false }
  $rootPrefix = ""
  if (-not [string]::IsNullOrWhiteSpace($ProjectRoot) -and (Test-Path $ProjectRoot)) {
    $rootPrefix = (Resolve-Path $ProjectRoot).Path
  }
  $exe = [string]($ProcessInfo.ExecutablePath)
  $cmd = [string]($ProcessInfo.CommandLine)
  if (-not [string]::IsNullOrWhiteSpace($rootPrefix)) {
    if (-not [string]::IsNullOrWhiteSpace($exe) -and $exe.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
      return $true
    }
    if (-not [string]::IsNullOrWhiteSpace($cmd) -and $cmd.IndexOf($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
      return $true
    }
  }
  return (-not [string]::IsNullOrWhiteSpace($cmd) -and $cmd.IndexOf("src/optimizer_server.py", [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
}

function Stop-ProbePlannerPortListeners {
  param(
    [int]$TargetPort,
    [string]$ProjectRoot,
    [switch]$ThrowOnForeign
  )
  $procIds = Get-ProbePlannerListeningProcIds -TargetPort $TargetPort
  if (-not $procIds -or $procIds.Count -eq 0) {
    Write-Host "[port] no existing listener on :$TargetPort"
    return
  }
  foreach ($procId in $procIds) {
    $procInfo = Get-ProbePlannerProcessInfo -ProcessId ([int]$procId)
    if (Test-ProbePlannerOwnedProcess -ProcessInfo $procInfo -ProjectRoot $ProjectRoot) {
      Write-Host ("[port] stopping ProbePlanner-owned listener :{0} PID={1}" -f $TargetPort, $procId)
      Stop-Process -Id ([int]$procId) -Force -ErrorAction SilentlyContinue
    } else {
      $name = if ($procInfo) { [string]$procInfo.Name } else { "unknown" }
      $message = "Port $TargetPort is already in use by non-ProbePlanner process PID=$procId Name=$name. Stop it manually or choose a different port."
      if ($ThrowOnForeign) { throw $message }
      Write-Warning $message
    }
  }
  Start-Sleep -Milliseconds 500
}
