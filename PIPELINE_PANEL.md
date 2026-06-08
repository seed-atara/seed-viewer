# Pipeline · Checkout (Artist Hub) tool

The **Pipeline · Checkout** tool in the launcher is the Artist Hub: sign in, claim
shots, publish versions (with artifact gates), run tools — natively, in the
installable app. It's the desktop counterpart to the web producer Hub; both talk to
the same shared state.

## How it's bundled (no source committed here)
The panel + its pipeline backend live in **seed-film** and are copied in at build
time by `build/prepare_sources.py` (alongside viewer.py / roto_align.py):

```
seed-film/artist_hub_qt.py        -> seed_viewer/pipeline_panel.py   (launch())
seed-film/pipeline_{paths,naming,state,silo,resolve,artifacts,auth}.py
seed-film/{task_spec,agents}.json -> seed_viewer/...
seed-film/comfy_workflows/        -> seed_viewer/comfy_workflows/
```

All are **gitignored** here — they're frozen into the PyInstaller binary, so the
freelancer gets a compiled app with no exposed source. They import each other as
top-level modules (the panel does `sys.path.insert(HERE)`), so no import patching
is needed.

## Config
The panel reads `~/.seed-viewer.env` (the same file the Settings dialog writes) via
`pipeline_paths`, which now loads it. The panel's own **⚙ Setup** dialog also writes
it: drive root (`SF_SHOTS_ROOT`), scratch (`SF_SCRATCH`), and max cache size
(`SF_SILO_CACHE_MIB`). Multi-user state needs `SF_STATE_DSN` pointed at the shared
Postgres (else it's local SQLite).

## Build / release (unchanged)
```bash
python build/prepare_sources.py --source /path/to/seed-film
python build/download_ffmpeg.py
pyinstaller build/seed_viewer_win.spec    # or _mac.spec
# or: make release VERSION=x.y.z   (tags -> GitHub Actions builds Mac + Windows)
```

> Verified headless: `seed_viewer.pipeline_panel.launch()` constructs the
> ArtistHub window and `refresh()` runs inside the package. **GUI interaction +
> the frozen build still need on-device testing** (PyInstaller + a real display).
