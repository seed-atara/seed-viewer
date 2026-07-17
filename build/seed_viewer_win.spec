# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Windows — produces SeedViewer.exe + support folder
#
# Run from repo root (PowerShell):
#   pyinstaller build\seed_viewer_win.spec

import sys
from pathlib import Path

REPO    = Path(SPECPATH).parent   # repo root
ASSETS  = REPO / "assets"
FFMPEG  = REPO / "build" / "ffmpeg" / "ffmpeg.exe"
FFPROBE = REPO / "build" / "ffmpeg" / "ffprobe.exe"

block_cipher = None

SV = REPO / "seed_viewer"   # pipeline backend is copied here by prepare_sources

a = Analysis(
    [str(REPO / "seed_viewer" / "main.py")],
    # include seed_viewer/ on pathex so the Artist Hub's top-level backend imports
    # (import pipeline_state, …) resolve to the copies prepare_sources placed there.
    pathex=[str(REPO), str(SV)],
    binaries=[
        (str(FFMPEG),  "ffmpeg"),   # bundled at _MEIPASS/ffmpeg/ffmpeg.exe
        (str(FFPROBE), "ffmpeg"),   # bundled at _MEIPASS/ffmpeg/ffprobe.exe
    ],
    datas=[
        (str(ASSETS / "fonts"), "assets/fonts"),
        # Artist Hub data files — must sit next to the bundled backend modules.
        (str(SV / "task_spec.json"), "seed_viewer"),
        (str(SV / "agents.json"),    "seed_viewer"),
        # local figure-segmentation model (u2netp, 4.7MB) -> _MEIPASS/u2netp.onnx
        *( [(str(SV / "u2netp.onnx"), ".")] if (SV / "u2netp.onnx").exists() else [] ),
    ],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "numpy",
        # SEED BRIDGE console (main.py's own home screen since 2026-07-05) + the
        # v14-rechromed stations it launches via importlib.import_module() — PyInstaller's
        # static analyzer can't trace those string-based lookups, so each needs an explicit
        # entry, both bare and seed_viewer.-qualified (learned the hard way: the personal
        # Bridge build shipped broken twice from missing entries here before this).
        "seed_viewer.seed_console", "seed_console",
        "seed_viewer.seed_theme_v14", "seed_theme_v14",
        "seed_viewer.shot_viewer_v14", "shot_viewer_v14",
        "seed_viewer.seed_studio_v14", "seed_studio_v14",
        "seed_viewer.seed_pip_v14", "seed_pip_v14",
        "seed_viewer.artist_hub_v14", "artist_hub_v14",
        # SEED LENS station (05) — launched via importlib from seed_console like the rest
        "seed_viewer.seed_lens_gui", "seed_lens_gui",
        "seed_viewer.seed_lens", "seed_lens",
        # SEED ATMOS station (06) — same importlib pattern; mirkifier is its physics core
        "seed_viewer.seed_atmos_gui", "seed_atmos_gui",
        "seed_viewer.seed_atmos", "seed_atmos",
        "seed_viewer.mirkifier", "mirkifier",
        # seed_console.py's Settings gear reaches back into main.py for SettingsDialog —
        # being the literal PyInstaller entry-point script does NOT make a module
        # separately importable by its dotted name from other bundled code; verified by
        # a real frozen-build test that failed without this exact entry.
        "seed_viewer.main", "main",
        # dynamically loaded via importlib.import_module() in main.py
        "seed_viewer.viewer",
        "seed_viewer.roto_align",
        "seed_viewer.pipeline_panel",
        "seed_viewer.seed_image_edit",
        "seed_viewer.seed_gen", "seed_viewer.seed_relight",  # reused by Seed Studio
        "seed_viewer.seed_studio",  # Seed Studio — unified generate + animate (module tool)
        "seed_viewer.mcp_server",   # pipeline MCP server, run via SeedViewer.exe --mcp (stdlib-only)
        "seed_viewer.updater", "seed_viewer._buildinfo",
        # the Artist Hub backend (imported as top-level modules by the panel)
        "pipeline_state", "pipeline_auth", "pipeline_artifacts", "pipeline_resolve",
        "pipeline_naming", "pipeline_silo", "pipeline_paths", "hub_client",
        # Seed Image Edit's render core (imported bare by seed_image_edit)
        "cine_cam",
        # client-delivery backend — imported bare (lazily) by mcp_server's stage/send tools
        "deliver_stills", "aspera_send", "aspera_pull",
        # generative backend — Seedance/Seedream client + internal key (bare imports)
        "seedance_client", "seed_ark_key", "ark_assets", "seed_theme",
        "seed_viewer.seed_pip", "seed_pip", "seed_anthropic_key",
        # Beeble backend — SwitchX client + internal key (bare imports). seed_relight/seed_studio
        # reuse sibling modules via bare imports, so those must be importable bare too.
        "beeble_client", "seed_beeble_key", "seed_gen", "seed_relight",
        "seed_studio", "seed_player", "auto_prompt", "seed_image_edit",
        # local figure segmentation (u2netp via onnxruntime) — instant on-device matte
        "seg_figure", "onnxruntime",
        # direct-mode Postgres driver (DirectBackend on a drive-connected machine)
        "psycopg2", "psycopg2._psycopg", "psycopg2.extras",
        "requests",
        # stdlib submodules hub_client uses that nothing else pulls in (PyInstaller
        # can miss these → "No module named 'http.cookiejar'" at panel load).
        "http.cookiejar", "urllib.request", "urllib.parse", "urllib.error",
    ],
    hookspath=[str(REPO / "build" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "scipy", "pandas", "IPython",
        "tkinter",
        "PySide6.Qt3DCore", "PySide6.Qt3DExtras", "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
        "PySide6.QtDataVisualization", "PySide6.QtCharts",
        "PySide6.QtWebEngine", "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SeedViewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # UPX corrupts python3xx.dll on newer runners ->
                            # "Failed to start embedded python interpreter". Off.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # add path to .ico file when available
    version_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,              # off — see EXE() above
    name="SeedViewer",
)
