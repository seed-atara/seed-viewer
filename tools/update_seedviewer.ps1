# update_seedviewer.ps1 — clean, versioned SeedViewer update (no in-place overwrites).
#
# Every release installs into its OWN folder under %LOCALAPPDATA%\SeedViewer\<tag>;
# a "current" junction flips atomically to the new version; the previous version
# stays on disk for instant rollback. User config (~/.seed-viewer.env), the drive
# and the state DB are never touched. Safe to run any number of times a day —
# if you're already on the latest it just relaunches the app.
#
#   powershell -ExecutionPolicy Bypass -File update_seedviewer.ps1
#
$ErrorActionPreference = "Stop"
$repo = "seed-atara/seed-viewer"
$root = Join-Path $env:LOCALAPPDATA "SeedViewer"
New-Item -ItemType Directory -Force $root | Out-Null

Write-Host "checking latest release..."
$rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" `
       -Headers @{ "User-Agent" = "seedviewer-updater" }
$tag = $rel.tag_name
$asset = $rel.assets | Where-Object { $_.name -eq "SeedViewer-win.zip" }
if (-not $asset) { throw "no SeedViewer-win.zip on release $tag" }
$dst = Join-Path $root $tag

if (-not (Test-Path $dst)) {
    Write-Host "downloading $tag ($([math]::Round($asset.size/1MB)) MB)..."
    $zip = Join-Path $env:TEMP "SeedViewer-$tag.zip"
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip `
        -Headers @{ "User-Agent" = "seedviewer-updater" }
    Write-Host "closing any running SeedViewer..."
    Get-Process SeedViewer -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 1
    Write-Host "installing to $dst ..."
    Expand-Archive $zip -DestinationPath $dst -Force
    Remove-Item $zip
} else {
    Write-Host "$tag already installed - relaunching"
    Get-Process SeedViewer -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 1
}

# flip the 'current' junction atomically to the new version
$cur = Join-Path $root "current"
if (Test-Path $cur) { cmd /c rmdir "$cur" }
cmd /c mklink /J "$cur" "$dst" | Out-Null

# keep the last 2 versions for rollback, prune older ones
$old = Get-ChildItem $root -Directory |
       Where-Object { $_.Name -match "^v\d" } |
       Sort-Object { [version]($_.Name -replace "^v", "") } |
       Select-Object -SkipLast 2
foreach ($o in $old) { Remove-Item -Recurse -Force $o.FullName }

$exe = Get-ChildItem $cur -Recurse -Filter SeedViewer.exe | Select-Object -First 1
if (-not $exe) { throw "SeedViewer.exe not found in $cur" }
Write-Host "launching $tag"
Start-Process $exe.FullName
Write-Host "done - rollback: run the exe inside the previous version folder in $root"
