# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for SEED BRIDGE — Johannes' personal build (v14). NOT the team's
# spec: separate entry point (seed_console.py, not main.py), separate output name
# (SeedBridge, not SeedViewer) so it installs alongside the team build without
# colliding, built + released by its own workflow (release-personal.yml) under its
# own tag pattern (personal-v*), always as a GitHub prerelease.
#
# Run from repo root (PowerShell):
#   pyinstaller build\seed_viewer_win_v14.spec

import sys
from pathlib import Path

REPO    = Path(SPECPATH).parent   # repo root
ASSETS  = REPO / "assets"
FFMPEG  = REPO / "build" / "ffmpeg" / "ffmpeg.exe"
FFPROBE = REPO / "build" / "ffmpeg" / "ffprobe.exe"

block_cipher = None

SV = REPO / "seed_viewer"   # pipeline backend is copied here by prepare_sources

a = Analysis(
    [str(REPO / "seed_viewer" / "seed_console.py")],
    pathex=[str(REPO), str(SV)],
    binaries=[
        (str(FFMPEG),  "ffmpeg"),
        (str(FFPROBE), "ffmpeg"),
    ],
    datas=[
        (str(ASSETS / "fonts"), "assets/fonts"),
        (str(SV / "task_spec.json"), "seed_viewer"),
        (str(SV / "agents.json"),    "seed_viewer"),
        *( [(str(SV / "u2netp.onnx"), ".")] if (SV / "u2netp.onnx").exists() else [] ),
    ],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "numpy",
        # the Bridge shell itself + its theme
        "seed_viewer.seed_console", "seed_console",
        "seed_viewer.seed_theme_v14", "seed_theme_v14",
        # the v14 re-chromed stations — reached only via importlib.import_module()
        # string lookups in seed_console.py's _import_first(), which PyInstaller's
        # static analyzer cannot trace. Without an explicit entry here they're
        # silently absent from the frozen build even though prepare_sources.py
        # copied their .py source in (this is exactly what broke personal-v14.1.0:
        # every station failed with "no launch() entry point found").
        "seed_viewer.shot_viewer_v14", "shot_viewer_v14",
        "seed_viewer.seed_studio_v14", "seed_studio_v14",
        "seed_viewer.seed_pip_v14", "seed_pip_v14",
        # every tool a mode card can launch, same set the team spec bundles
        "seed_viewer.viewer",
        "seed_viewer.roto_align",
        "seed_viewer.pipeline_panel",
        "seed_viewer.seed_image_edit",
        "seed_viewer.seed_gen", "seed_viewer.seed_relight",
        "seed_viewer.seed_studio",
        "seed_viewer.mcp_server",
        "seed_viewer.updater", "seed_viewer._buildinfo",
        "pipeline_state", "pipeline_auth", "pipeline_artifacts", "pipeline_resolve",
        "pipeline_naming", "pipeline_silo", "pipeline_paths", "hub_client",
        "cine_cam",
        "deliver_stills", "aspera_send", "aspera_pull",
        "seedance_client", "seed_ark_key", "ark_assets", "seed_theme",
        "seed_viewer.seed_pip", "seed_pip", "seed_anthropic_key",
        "beeble_client", "seed_beeble_key", "seed_gen", "seed_relight",
        "seed_studio", "seed_player", "auto_prompt", "seed_image_edit",
        "seg_figure", "onnxruntime",
        "psycopg2", "psycopg2._psycopg", "psycopg2.extras",
        "requests",
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
    name="SeedBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    version_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="SeedBridge",
)
