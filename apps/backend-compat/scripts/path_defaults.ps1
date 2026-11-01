function Get-ProbePlannerWorkspaceRoot {
  if (-not [string]::IsNullOrWhiteSpace($env:PROBEPLANNER_WORKSPACE_ROOT)) {
    return $env:PROBEPLANNER_WORKSPACE_ROOT
  }
  return (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

function Get-ProbePlannerBackendRoot {
  if (-not [string]::IsNullOrWhiteSpace($env:PROBEPLANNER_BACKEND_COMPAT_ROOT)) {
    return $env:PROBEPLANNER_BACKEND_COMPAT_ROOT
  }
  return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Get-ProbePlannerPython {
  if (-not [string]::IsNullOrWhiteSpace($env:PROBEPLANNER_PYTHON)) {
    return $env:PROBEPLANNER_PYTHON
  }
  return "python"
}

function Get-ProbePlannerMriRoot {
  if (-not [string]::IsNullOrWhiteSpace($env:PROBEPLANNER_MRI_CCF_ROOT)) {
    return $env:PROBEPLANNER_MRI_CCF_ROOT
  }
  return (Join-Path (Get-ProbePlannerWorkspaceRoot) ".data\mri_ccf")
}

function Join-ProbePlannerMriPath {
  param([Parameter(Mandatory=$true)][string]$RelativePath)
  return (Join-Path (Get-ProbePlannerMriRoot) $RelativePath)
}

function Get-ProbePlannerAntsBin {
  if (-not [string]::IsNullOrWhiteSpace($env:PROBEPLANNER_ANTS_BIN)) {
    return $env:PROBEPLANNER_ANTS_BIN
  }
  return (Join-Path (Get-ProbePlannerWorkspaceRoot) "tools\ants\ants-2.6.5-windows-2022-X64-VS2019\ants-2.6.5\bin")
}
