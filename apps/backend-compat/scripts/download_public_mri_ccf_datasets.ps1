param(
  [string]$DataRoot = "",
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

function Ensure-Dir([string]$path) {
  if (-not (Test-Path $path)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
  }
}

function Download-IfMissing([string]$url, [string]$outPath) {
  if ((Test-Path $outPath) -and ((Get-Item $outPath).Length -gt 0)) {
    Write-Host "[SKIP] $outPath"
    return
  }
  Ensure-Dir (Split-Path -Path $outPath -Parent)
  Write-Host "[GET]  $url"
  try {
    Invoke-WebRequest -Uri $url -OutFile $outPath -TimeoutSec 600
  } catch {
    Write-Host "[WARN] Invoke-WebRequest failed, fallback to curl.exe"
    & curl.exe -L $url -o $outPath
  }
  if (-not (Test-Path $outPath)) {
    throw "Download failed: $url"
  }
  if ((Get-Item $outPath).Length -le 0) {
    throw "Downloaded empty file: $outPath"
  }
}

function Extract-AmbmcIfTarPayload([string]$inputPath, [string]$outputDir) {
  if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found: $PythonExe"
  }
  Ensure-Dir $outputDir
  $py = @"
from pathlib import Path
import tarfile

src = Path(r"$inputPath")
out_dir = Path(r"$outputDir")
out_dir.mkdir(parents=True, exist_ok=True)

if not src.exists():
    raise SystemExit(f"Missing source file: {src}")

try:
    with tarfile.open(src, "r:") as tf:
        members = tf.getmembers()
        if not members:
            raise SystemExit(0)
        tf.extractall(out_dir)
        print(f"[OK] extracted {len(members)} members from {src.name} to {out_dir}")
except tarfile.ReadError:
    print(f"[SKIP] {src.name} is not a tar payload.")
"@
  $tmp = Join-Path $env:TEMP "extract_ambmc_payload.py"
  Set-Content -Path $tmp -Value $py -Encoding UTF8
  & $PythonExe $tmp
  Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}

function Write-Manifest([string]$manifestPath) {
  $payload = @{
    createdAt = (Get-Date).ToString("s")
    dataRoot = $DataRoot
    datasets = @(
      @{
        name = "Allen CCFv3 25um"
        path = (Join-Path $DataRoot "allen_ccf")
        files = @(
          "average_template_25.nrrd",
          "CCFv3_annotation_25.nrrd",
          "CCFv3_annotation_ITKSNAP_labels.txt",
          "CCFv3_annotation_OntologyColor_labels.txt"
        )
      },
      @{
        name = "OCT Mouse Brain Templates (Mendeley)"
        path = (Join-Path $DataRoot "oct_mendeley")
        files = @(
          "MouseBrainTemplate_OCT_50microns.nii.gz",
          "MouseBrainTemplate_Attn_50microns.nii.gz"
        )
      },
      @{
        name = "AMBMC C57BL/6 15um model (OSF)"
        path = (Join-Path $DataRoot "ambmc")
        files = @(
          "ambmc-c57bl6-model-symmet_v0.8.nii",
          "ambmc-c57bl6-model-nonsym_v0.8.nii"
        )
        note = "These two downloads are tar payloads despite .nii extension; extracted copies are under *_extracted folders."
      }
    )
  }
  $json = $payload | ConvertTo-Json -Depth 6
  Set-Content -Path $manifestPath -Value $json -Encoding UTF8
}

Ensure-Dir $DataRoot

# Allen CCFv3 (official)
$allenDir = Join-Path $DataRoot "allen_ccf"
Ensure-Dir $allenDir
Download-IfMissing "https://download.alleninstitute.org/publications/allen_mouse_brain_common_coordinate_framework/transgenic_lines_25_um_grid/ccf/average_template_25.nrrd" (Join-Path $allenDir "average_template_25.nrrd")
Download-IfMissing "https://download.alleninstitute.org/publications/allen_mouse_brain_common_coordinate_framework/transgenic_lines_25_um_grid/ccf/CCFv3_annotation_25.nrrd" (Join-Path $allenDir "CCFv3_annotation_25.nrrd")
Download-IfMissing "https://download.alleninstitute.org/publications/allen_mouse_brain_common_coordinate_framework/transgenic_lines_25_um_grid/ccf/CCFv3_annotation_ITKSNAP_labels.txt" (Join-Path $allenDir "CCFv3_annotation_ITKSNAP_labels.txt")
Download-IfMissing "https://download.alleninstitute.org/publications/allen_mouse_brain_common_coordinate_framework/transgenic_lines_25_um_grid/ccf/CCFv3_annotation_OntologyColor_labels.txt" (Join-Path $allenDir "CCFv3_annotation_OntologyColor_labels.txt")

# OCT templates (Mendeley public API listed files)
$octDir = Join-Path $DataRoot "oct_mendeley"
Ensure-Dir $octDir
Download-IfMissing "https://data.mendeley.com/public-files/datasets/33wfgxpmp8/files/050c25c9-0eda-4527-83b0-0559e23c848d/file_downloaded" (Join-Path $octDir "MouseBrainTemplate_OCT_50microns.nii.gz")
Download-IfMissing "https://data.mendeley.com/public-files/datasets/33wfgxpmp8/files/5fd5762f-c706-4afc-8066-b8e4aba9911d/file_downloaded" (Join-Path $octDir "MouseBrainTemplate_Attn_50microns.nii.gz")

# AMBMC (OSF links from AMBMC model page)
$ambmcDir = Join-Path $DataRoot "ambmc"
Ensure-Dir $ambmcDir
Download-IfMissing "https://osf.io/download/g8p6a/" (Join-Path $ambmcDir "ambmc-c57bl6-model-symmet_v0.8.nii")
Download-IfMissing "https://osf.io/download/t9chg/" (Join-Path $ambmcDir "ambmc-c57bl6-model-nonsym_v0.8.nii")
Extract-AmbmcIfTarPayload (Join-Path $ambmcDir "ambmc-c57bl6-model-symmet_v0.8.nii") (Join-Path $ambmcDir "ambmc-c57bl6-model-symmet_v0.8_extracted")
Extract-AmbmcIfTarPayload (Join-Path $ambmcDir "ambmc-c57bl6-model-nonsym_v0.8.nii") (Join-Path $ambmcDir "ambmc-c57bl6-model-nonsym_v0.8_extracted")

$manifest = Join-Path $DataRoot "dataset_manifest.json"
Write-Manifest $manifest

Write-Host "======================================================="
Write-Host "[DONE] Public MRI+CCF datasets prepared."
Write-Host "Data root: $DataRoot"
Write-Host "Manifest : $manifest"
Write-Host "======================================================="
