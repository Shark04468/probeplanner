$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $scriptDir "render_mri_ccf_registration_qc_figure.py"

$candidates = @(
  @{ Exe = "python"; Args = @() },
  @{ Exe = "py"; Args = @("-3.14") },
  @{ Exe = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"; Args = @() }
)

$checkCode = "import numpy, matplotlib, PIL"
$selected = $null

foreach ($candidate in $candidates) {
  try {
    & $candidate.Exe @($candidate.Args) -c $checkCode *> $null
    if ($LASTEXITCODE -eq 0) {
      $selected = $candidate
      break
    }
  } catch {
    continue
  }
}

if ($null -eq $selected) {
  throw "No Python runtime with numpy, matplotlib, and Pillow was found for MRI-to-CCF QC rendering."
}

Write-Host "Using Python runtime: $($selected.Exe) $($selected.Args -join ' ')"
& $selected.Exe @($selected.Args) $scriptPath @args
exit $LASTEXITCODE
