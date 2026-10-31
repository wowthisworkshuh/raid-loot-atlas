# Raid Loot Atlas: collect the loot files your SPT install uses
#
# Zips each map's looseLoot.json (plus the item list) from your SPT folder into one file on your
# Desktop. Send that zip to Claude, and the atlas gets rebuilt from your server's exact data.
# It only reads files; nothing in SPT is changed.
#
#   powershell -ExecutionPolicy Bypass -File collect-server-loot.ps1 -Spt "C:\SPT"

param([string]$Spt = "C:\SPT")

$ErrorActionPreference = "Stop"
if (-not (Test-Path $Spt)) { throw "No folder at $Spt. Pass your SPT folder: -Spt 'D:\Games\SPT'" }

# SPT 4.x keeps its database under SPT_Runtime (or SPT_Data in some layouts); find it rather than guess
$hit = Get-ChildItem -Path $Spt -Recurse -Filter looseLoot.json -ErrorAction SilentlyContinue |
       Where-Object { $_.FullName -match '[\\/]database[\\/]locations[\\/]bigmap[\\/]' } | Select-Object -First 1
if (-not $hit) { throw "Couldn't find SPT's database under $Spt (looked for database\locations\bigmap\looseLoot.json)." }
$db = $hit.Directory.Parent.Parent.FullName
Write-Host "SPT database: $db"

$maps = "bigmap","factory4_day","factory4_night","woods","shoreline","interchange","lighthouse",
        "rezervbase","laboratory","labyrinth","sandbox","sandbox_high","tarkovstreets"
$stage = Join-Path $env:TEMP "loot-atlas-collect"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
foreach ($m in $maps) {
  $src = Join-Path $db "locations\$m"
  if (-not (Test-Path "$src\looseLoot.json")) { Write-Warning "no looseLoot.json for $m"; continue }
  $dst = New-Item -ItemType Directory -Force (Join-Path $stage "database\locations\$m")
  Copy-Item "$src\looseLoot.json","$src\staticLoot.json","$src\staticContainers.json" $dst -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force (Join-Path $stage "database\templates") | Out-Null
Copy-Item (Join-Path $db "templates\items.json"),(Join-Path $db "templates\handbook.json") (Join-Path $stage "database\templates")
New-Item -ItemType Directory -Force (Join-Path $stage "database\locales\global") | Out-Null
Copy-Item (Join-Path $db "locales\global\en.json") (Join-Path $stage "database\locales\global")

$zip = Join-Path ([Environment]::GetFolderPath("Desktop")) "loot-atlas-server-data.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $stage "database") -DestinationPath $zip -CompressionLevel Optimal
Remove-Item $stage -Recurse -Force
"{0}  ({1:N0} MB)" -f $zip, ((Get-Item $zip).Length / 1MB) | Write-Host
Write-Host "Send this zip to Claude."
