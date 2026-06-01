# Seed Viewer

Shot review and roto alignment tool for the Seed Film pipeline.

## Download

Go to [**Releases**](../../releases/latest) and download the build for your platform:

| Platform | File | Notes |
|----------|------|-------|
| macOS (Apple Silicon / Intel) | `SeedViewer-mac.dmg` | Open, drag to Applications |
| Windows 11 | `SeedViewer-win.zip` | Extract, run `SeedViewer.exe` |

> **macOS note:** On first launch, right-click → Open (or go to System Settings → Privacy & Security → Open Anyway). The app is not notarised yet.

---

## First-time setup

### 1. Mount the project drive

The viewer reads shots from the shared Google Drive. Mount it before launching.

| Platform | Default path expected |
|----------|----------------------|
| macOS | `/Volumes/SATOSHI_DRIVE/SATOSHI` |
| Windows | `G:\Shared drives\SATOSHI_DRIVE\SATOSHI` |

If your drive mounts at a different path, configure it (see below).

### 2. Configure paths (if non-default)

Create a file called `.seed-viewer.env` in your **home folder**:

- macOS: `/Users/<you>/.seed-viewer.env`
- Windows: `C:\Users\<you>\.seed-viewer.env`

Copy from the template below and adjust the paths:

```
# .seed-viewer.env
SF_DRIVE_ROOT=/Volumes/SATOSHI_DRIVE/SATOSHI
SF_SHOTS_ROOT=/Volumes/SATOSHI_DRIVE/SATOSHI/_SHOTS
SF_FFMPEG_EXE=
```

> Leave `SF_FFMPEG_EXE` empty — ffmpeg is bundled inside the app.
> On Windows use forward slashes or double backslashes: `G:/Shared drives/SATOSHI_DRIVE/SATOSHI`

### 3. Launch

Double-click the app. The launcher opens with all available tools.

---

## Tools

| Tool | What it does |
|------|-------------|
| **Shot Viewer** | Contact sheet of all shots. Wipe A/B comparison between any two layers (Plate, WAI, Comp, First Frame, etc). Playback. |
| **Roto Align** | Align Beeble masks / WAI renders to their source plates. Export RGBA talent PNGs and over-black MP4. |

More tools will be added in future releases.

---

## Keyboard shortcuts (Shot Viewer)

| Key | Action |
|-----|--------|
| Space | Play/pause |
| ← → | Previous / next frame |
| Shift + ← → | Step ±10 frames |
| [ ] | Previous / next shot |
| W | Drag wipe divider |

## Keyboard shortcuts (Roto Align)

| Key | Action |
|-----|--------|
| ← → | Step plate frame |
| Shift + ← → | Step ±10 |
| , . | Mask offset ±1 |
| Alt + , . | Plate step ±1 |
| S | Toggle mask scrub mode |
| W | Use WAI render as mask |
| [ ] | Previous / next shot |

---

## Requirements

- macOS 13+ (Ventura) or Windows 11
- Google Drive mounted (project access)
- FFmpeg is bundled — no separate install needed

---

## Troubleshooting

**"No shots found"**  
Check that the drive is mounted and `SF_SHOTS_ROOT` in `.seed-viewer.env` is correct.

**macOS: "App is damaged and can't be opened"**  
Run in Terminal: `xattr -cr /Applications/SeedViewer.app`

**Windows: Antivirus blocks the exe**  
Add an exclusion for `SeedViewer.exe` in Windows Security settings.

**Viewer opens but shows no thumbnails**  
Check that ffmpeg is accessible. The bundled ffmpeg should work; if not, set `SF_FFMPEG_EXE=/path/to/ffmpeg` in `.seed-viewer.env`.
