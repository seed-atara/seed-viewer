# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for macOS — produces SeedViewer.app
#
# Run from repo root:
#   pyinstaller build/seed_viewer_mac.spec

import sys
from pathlib import Path

REPO    = Path(SPECPATH).parent   # repo root
ASSETS  = REPO / "assets"
FFMPEG  = REPO / "build" / "ffmpeg" / "ffmpeg"
FFPROBE = REPO / "build" / "ffmpeg" / "ffprobe"

block_cipher = None

SV = REPO / "seed_viewer"   # pipeline backend is copied here by prepare_sources

a = Analysis(
    [str(REPO / "seed_viewer" / "main.py")],
    # include seed_viewer/ on pathex so the Artist Hub's top-level backend imports
    # (import pipeline_state, …) resolve to the copies prepare_sources placed there.
    pathex=[str(REPO), str(SV)],
    binaries=[
        (str(FFMPEG),  "ffmpeg"),   # bundled at _MEIPASS/ffmpeg/ffmpeg
        (str(FFPROBE), "ffmpeg"),   # bundled at _MEIPASS/ffmpeg/ffprobe
    ],
    datas=[
        (str(ASSETS / "fonts"), "assets/fonts"),
        # Artist Hub data files — must sit next to the bundled backend modules.
        (str(SV / "task_spec.json"), "seed_viewer"),
        (str(SV / "agents.json"),    "seed_viewer"),
    ],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PIL._tkinter_finder",
        "numpy",
        # dynamically loaded via importlib.import_module() in main.py
        "seed_viewer.viewer",
        "seed_viewer.roto_align",
        "seed_viewer.pipeline_panel",
        "seed_viewer.updater", "seed_viewer._buildinfo",
        # the Artist Hub backend (imported as top-level modules by the panel)
        "pipeline_state", "pipeline_auth", "pipeline_artifacts", "pipeline_resolve",
        "pipeline_naming", "pipeline_silo", "pipeline_paths", "hub_client",
        # gated CLI tools — run in-process via the exe's --tool self-invoke
        "seed_viewer.beeble_submit", "seed_viewer.magnific_submit",
        "beeble_submit", "magnific_submit", "requests",
        # stdlib submodules hub_client uses that nothing else pulls in (PyInstaller
        # can miss these → "No module named 'http.cookiejar'" at panel load).
        "http.cookiejar", "urllib.request", "urllib.parse", "urllib.error",
    ],
    hookspath=[str(REPO / "build" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy packages not needed
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
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,         # None = build for current arch
    codesign_identity=None,   # set to your Developer ID for notarisation
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SeedViewer",
)

app = BUNDLE(
    coll,
    name="SeedViewer.app",
    icon=None,              # add path to .icns file when available
    bundle_identifier="ai.seedstudio.seedviewer",
    version="0.1.0",
    info_plist={
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,   # supports dark mode
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSMinimumSystemVersion": "13.0",
        "NSAppleEventsUsageDescription": "Seed Viewer needs access to the file system to load shots.",
    },
)
