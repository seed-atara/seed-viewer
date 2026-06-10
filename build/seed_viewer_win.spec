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
    ],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "numpy",
        # dynamically loaded via importlib.import_module() in main.py
        "seed_viewer.viewer",
        "seed_viewer.roto_align",
        "seed_viewer.pipeline_panel",
        # the Artist Hub backend (imported as top-level modules by the panel)
        "pipeline_state", "pipeline_auth", "pipeline_artifacts", "pipeline_resolve",
        "pipeline_naming", "pipeline_silo", "pipeline_paths", "hub_client",
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
    upx=True,               # compress the exe on Windows (reduce size ~30%)
    upx_exclude=["vcruntime140.dll", "python3*.dll"],
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
    upx=True,
    upx_exclude=["vcruntime140.dll"],
    name="SeedViewer",
)
