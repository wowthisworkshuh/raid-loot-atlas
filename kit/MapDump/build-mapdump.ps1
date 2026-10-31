# Raid Loot Atlas: build and install the map dump plugin, or collect its output
#
#   Build + install:   powershell -ExecutionPolicy Bypass -File build-mapdump.ps1 -Spt "C:\SPT"
#   Collect dumps:     powershell -ExecutionPolicy Bypass -File build-mapdump.ps1 -Spt "C:\SPT" -Collect
#   Remove plugin:     powershell -ExecutionPolicy Bypass -File build-mapdump.ps1 -Spt "C:\SPT" -Uninstall
#
# Needs the .NET SDK (8 or newer): https://dotnet.microsoft.com/download. The plugin only reads the
# map; it goes on your own game, not the server or the headless, and you can remove it after.

param([string]$Spt = "C:\SPT", [switch]$Collect, [switch]$Uninstall)
$ErrorActionPreference = "Stop"
if (-not (Test-Path "$Spt\EscapeFromTarkov.exe")) { throw "No SPT game at $Spt. Pass your SPT folder: -Spt 'D:\Games\SPT'" }
$pluginDir = Join-Path $Spt "BepInEx\plugins\SurvivalPack.MapDump"
$dumpDir = Join-Path $Spt "BepInEx\survivalpack-mapdump"

if ($Uninstall) {
  Remove-Item $pluginDir -Recurse -Force -ErrorAction SilentlyContinue
  Write-Host "Removed $pluginDir (dumps are kept in $dumpDir)"; return
}

if ($Collect) {
  if (-not (Test-Path $dumpDir)) { throw "No dumps yet. Load into a raid on each map first (the plugin writes after ~20 s)." }
  $maps = Get-ChildItem $dumpDir -Directory
  foreach ($m in $maps) {
    $meta = Get-Content (Join-Path $m.FullName "meta.json") -Raw | ConvertFrom-Json
    "{0,-16} {1,8:N0} walk triangles  {2,4} doors  {3,4} containers  {4,3} exits" -f $m.Name, $meta.navmeshTriangles, $meta.doors, $meta.containers, $meta.exits | Write-Host
  }
  $zip = Join-Path ([Environment]::GetFolderPath("Desktop")) "loot-atlas-mapdump.zip"
  Remove-Item $zip -ErrorAction SilentlyContinue
  Compress-Archive -Path (Join-Path $dumpDir "*") -DestinationPath $zip -CompressionLevel Optimal
  "{0}  ({1:N1} MB). Send this zip to Claude." -f $zip, ((Get-Item $zip).Length / 1MB) | Write-Host
  return
}

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { throw ".NET SDK not found. Install it from https://dotnet.microsoft.com/download and run this again." }
Push-Location $PSScriptRoot
try {
  dotnet build SurvivalPack.MapDump.csproj -c Release "-p:SptPath=$Spt" -nologo
  if ($LASTEXITCODE -ne 0) { throw "Build failed. Copy the error lines above to Claude." }
  New-Item -ItemType Directory -Force $pluginDir | Out-Null
  Copy-Item "bin\Release\netstandard2.1\SurvivalPack.MapDump.dll" $pluginDir -Force
  Write-Host "Installed to $pluginDir"
  Write-Host "Now load into a raid on each map (offline or hosting is fine). About 20 s after the raid loads, a box"
  Write-Host "top-left says the map dump was written. Ctrl+F10 writes it again. Then run this script with -Collect."
} finally { Pop-Location }
