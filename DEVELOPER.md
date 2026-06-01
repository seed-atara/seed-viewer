# Developer Guide — Seed Viewer

Internal notes for building and releasing. Not for freelancers.

---

## Architecture

```
seed-viewer/          ← PUBLIC repo (this one)
    seed_viewer/
        __init__.py
        main.py       ← launcher window, tool registry
        paths.py      ← standalone-aware pipeline_paths
        viewer.py     ← GENERATED from seed-film/shot_viewer_qt.py
        roto_align.py ← GENERATED from seed-film/roto_align.py
    build/
        prepare_sources.py  ← copies + adapts from seed-film
        download_ffmpeg.py
        seed_viewer_mac.spec
        seed_viewer_win.spec
        build_mac.sh
        build_win.bat
    .github/workflows/release.yml  ← CI/CD

seed-film/            ← PRIVATE repo (source of truth for viewer code)
    shot_viewer_qt.py
    roto_align.py
    pipeline_paths.py
    ingest.py  (not included in viewer package)
    delivery/  (not included in viewer package)
```

`viewer.py` and `roto_align.py` are **generated** — never edit them directly in seed-viewer. Edit in seed-film, then re-run `prepare_sources.py`.

---

## Adding a new tool

1. Write the tool in seed-film as `your_tool.py` with a `def launch() -> QMainWindow` function
2. Add it to `prepare_sources.py` `tasks` list
3. Register it in `seed_viewer/main.py` `TOOLS` list
4. Re-run `prepare_sources.py --source /path/to/seed-film`
5. Tag and release

---

## Local build (macOS)

```bash
# Prerequisites: Python 3.11+, pip install -r requirements-build.txt
cd /path/to/seed-viewer
bash build/build_mac.sh /path/to/seed-film
open build/dist/SeedViewer.app   # test
```

## Local build (Windows)

```powershell
cd C:\path\to\seed-viewer
build\build_win.bat C:\path\to\seed-film
.\build\dist\SeedViewer\SeedViewer.exe   # test
```

---

## Release process (auto via GitHub Actions)

1. Make sure seed-film is up to date and pushed
2. Tag seed-viewer:
   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```
3. GitHub Actions runs:
   - Checks out both repos
   - Runs `prepare_sources.py`
   - Builds on macOS + Windows
   - Creates a GitHub Release with both binaries

### Required GitHub Secrets

| Secret | Value |
|--------|-------|
| `SEED_FILM_TOKEN` | GitHub Personal Access Token with `repo` scope on `seed-atara/seed-film` |

Set via: seed-viewer repo → Settings → Secrets and variables → Actions → New secret

---

## Paths used by the viewer

The standalone viewer does NOT use `.env.local` from seed-film.

Config hierarchy (paths.py):
1. `~/.seed-viewer.env` (user home)
2. `.env.local` next to the executable (dev runs)
3. Hardcoded defaults (`G:\Shared drives\SATOSHI_DRIVE\SATOSHI` on Windows, `/Volumes/...` on Mac)

Key env vars: `SF_DRIVE_ROOT`, `SF_SHOTS_ROOT`, `SF_FFMPEG_EXE`

FFmpeg is bundled at `_MEIPASS/ffmpeg/ffmpeg[.exe]`. The env var overrides it.

---

## macOS notarisation (future)

For unsigned builds, freelancers need to right-click → Open or run:
```bash
xattr -cr /Applications/SeedViewer.app
```

For proper notarisation:
1. Enroll in Apple Developer Program ($99/yr)
2. Set `codesign_identity` in `seed_viewer_mac.spec`
3. Add notarisation step to `release.yml`

---

## Size expectations

| Component | Size (approx) |
|-----------|--------------|
| PySide6 | ~90 MB |
| Python + stdlib | ~30 MB |
| Pillow + numpy | ~15 MB |
| FFmpeg binary | ~40 MB |
| App code | ~1 MB |
| **Total** | **~175 MB** |

The DMG/zip will be roughly this size. Normal for a PySide6 app.
