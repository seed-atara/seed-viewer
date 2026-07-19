# mirror_release.ps1 — stage the latest SeedViewer release on the shared drive.
# Run on the dev machine after each tag builds (Claude runs this as part of the
# release ritual). The updater on artist machines then pulls from the drive at
# Google speed instead of GitHub's asset CDN, with GitHub kept as fallback.
$ErrorActionPreference = "Stop"
$repo = "seed-atara/seed-viewer"
$dst = "G:\Shared drives\SATOSHI_DRIVE\SATOSHI\_TOOLS\releases"
New-Item -ItemType Directory -Force $dst | Out-Null

$rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" `
       -Headers @{ "User-Agent" = "seedviewer-mirror" }
$tag = $rel.tag_name
$zipName = "SeedViewer-win-$tag.zip"
$zipPath = Join-Path $dst $zipName

if (-not (Test-Path $zipPath)) {
    $asset = $rel.assets | Where-Object { $_.name -eq "SeedViewer-win.zip" }
    if (-not $asset) { throw "no SeedViewer-win.zip on $tag" }
    Write-Host "downloading $tag ($([math]::Round($asset.size/1MB)) MB) from GitHub once..."
    $tmp = Join-Path $env:TEMP $zipName
    Invoke-WebRequest $asset.browser_download_url -OutFile $tmp `
        -Headers @{ "User-Agent" = "seedviewer-mirror" }
    Copy-Item $tmp $zipPath -Force
    Remove-Item $tmp
} else {
    Write-Host "$tag already mirrored"
}

@{ tag = $tag; file = $zipName } | ConvertTo-Json |
    Out-File -Encoding utf8 (Join-Path $dst "latest.json")

# keep the last 3 mirrored releases
Get-ChildItem $dst -Filter "SeedViewer-win-v*.zip" |
    Sort-Object Name | Select-Object -SkipLast 3 | Remove-Item -Force
Write-Host "mirrored: $tag -> $dst"
