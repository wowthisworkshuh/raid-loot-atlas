# Raid Loot Atlas: shrink an exported map model for the browser
#
# Takes <map>.raw.glb from the Unity exporter and writes <map>.glb next to it: merged meshes,
# simplified, and meshopt-compressed. Needs Node.js 18+ (https://nodejs.org). The first run
# downloads gltf-transform through npx.
#
#   powershell -ExecutionPolicy Bypass -File optimize-map.ps1 -In "C:\TarkovMaps\Customs\LootAtlasExport\bigmap.raw.glb"
#
# Aim for under ~40 MB per map so it loads quickly for everyone. If a map comes out bigger,
# run again with a lower -Ratio (0.15, 0.1).

param(
  [Parameter(Mandatory = $true)][string]$In,
  [double]$Ratio = 0.25,       # keep this share of triangles
  [double]$SimplifyError = 0.002      # how far simplification may move a surface (share of the model size)
)
$ErrorActionPreference = "Stop"
if (-not (Get-Command npx -ErrorAction SilentlyContinue)) { throw "Node.js isn't installed (npx not found). Get it from https://nodejs.org" }
if (-not (Test-Path $In)) { throw "No file at $In" }

$out = $In -replace '\.raw\.glb$', '.glb'
if ($out -eq $In) { $out = [IO.Path]::ChangeExtension($In, ".web.glb") }

npx --yes @gltf-transform/cli@4 optimize "$In" "$out" `
  --compress meshopt --simplify true --simplify-ratio $Ratio --simplify-error $SimplifyError `
  --texture-compress webp --texture-size 512 --instance true --join true
if ($LASTEXITCODE -ne 0) { throw "gltf-transform failed (exit $LASTEXITCODE)" }

"{0}: {1:N1} MB -> {2:N1} MB" -f (Split-Path $out -Leaf), ((Get-Item $In).Length / 1MB), ((Get-Item $out).Length / 1MB) | Write-Host
