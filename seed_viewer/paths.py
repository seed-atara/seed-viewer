"""
paths.py — Path resolution for Seed Viewer standalone builds.

Config priority:
  1. Environment variables
  2. ~/.seed-viewer.env  (user home)
  3. .env.local alongside the executable (for dev runs)
  4. Built-in defaults (platform-dependent drive paths)

FFmpeg: bundled inside the app at _MEIPASS/ffmpeg/ffmpeg[.exe].
If SF_FFMPEG_EXE is set it overrides the bundle.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path

# ── Locate config files ────────────────────────────────────────────────────────

def _app_dir() -> Path:
    """Directory of the running script / frozen executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


# Load in priority order: user-home file first (can be overridden by env), then dev .env.local
_load_env_file(Path.home() / ".seed-viewer.env")
_load_env_file(_app_dir() / ".env.local")


# ── FFmpeg resolution ──────────────────────────────────────────────────────────

def _resolve_ffmpeg() -> str:
    override = os.environ.get("SF_FFMPEG_EXE", "").strip()
    if override:
        return override
    # Bundled ffmpeg inside the PyInstaller MEIPASS directory
    if getattr(sys, "frozen", False):
        mei = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        ffmpeg_name = "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
        bundled = mei / "ffmpeg" / ffmpeg_name
        if bundled.exists():
            return str(bundled)
    # Dev fallback: ffmpeg on PATH
    return shutil.which("ffmpeg") or "ffmpeg"


# ── Drive root defaults ────────────────────────────────────────────────────────

_SYS = platform.system()
_DEFAULT_DRIVE = (
    Path(r"G:\Shared drives\SATOSHI_DRIVE\SATOSHI") if _SYS == "Windows"
    else Path("/Volumes/SATOSHI_DRIVE/SATOSHI")
)


def _drive_root() -> Path:
    v = os.environ.get("SF_DRIVE_ROOT", "").strip()
    return Path(v) if v else _DEFAULT_DRIVE


# ── Public path constants ──────────────────────────────────────────────────────

DRIVE_ROOT   = _drive_root()
SHOTS_ROOT   = Path(os.environ.get("SF_SHOTS_ROOT", "").strip() or str(DRIVE_ROOT / "_SHOTS"))
FFMPEG_EXE   = _resolve_ffmpeg()

# ── JSON data ─────────────────────────────────────────────────────────────────
# Data files live on the drive root (not inside the bundle) so they stay current.

_DB_CACHE: dict | None = None


def _find_db_path() -> Path | None:
    """Look for shot_database.json: drive root first, then legacy locations."""
    candidates = [
        DRIVE_ROOT / "shot_database.json",
        SHOTS_ROOT / "shot_database.json",
        SHOTS_ROOT.parent / "shot_database.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _load_db() -> dict:
    global _DB_CACHE
    if _DB_CACHE is None:
        p = _find_db_path()
        try:
            _DB_CACHE = json.loads(p.read_text(encoding="utf-8")) if p else {}
        except Exception:
            _DB_CACHE = {}
    return _DB_CACHE


def _load_json_set(filename: str, key: str) -> set[str]:
    p = _find_db_path()
    if p is None:
        return set()
    candidate = p.parent / filename
    if not candidate.exists():
        return set()
    try:
        return set(json.loads(candidate.read_text(encoding="utf-8")).get(key, []))
    except Exception:
        return set()


def load_omitted() -> set[str]:
    return _load_json_set("omitted_shots.json", "omitted")


def is_omitted(shotcode: str) -> bool:
    return shotcode in load_omitted()


def load_ccb() -> set[str]:
    return _load_json_set("ccb_shots.json", "ccb")


def is_ccb(shotcode: str) -> bool:
    return shotcode in load_ccb()


# ── Shot discovery ─────────────────────────────────────────────────────────────

def sequence_for_shot(shotcode: str) -> str | None:
    return _load_db().get(shotcode, {}).get("sequence")


def shot_path(shotcode: str, *, root: Path | None = None) -> Path | None:
    root = root or SHOTS_ROOT
    seq = sequence_for_shot(shotcode)
    if not seq:
        return None
    return root / seq / shotcode


def find_shot_folder(shotcode: str, *, root: Path | None = None) -> Path | None:
    root = root or SHOTS_ROOT
    # 1. New structure: SHOTS_ROOT/<SEQUENCE>/<SHOTCODE>/
    p = shot_path(shotcode, root=root)
    if p and p.is_dir():
        return p
    # 2. Legacy PULL_PT* batches
    for batch in sorted(root.glob("PULL_PT*")):
        if not batch.is_dir():
            continue
        for d in batch.iterdir():
            if d.name.upper().startswith(shotcode.upper()):
                return d
    # 3. FULL_AI
    full_ai = root / "FULL_AI" / shotcode
    return full_ai if full_ai.exists() else None


# ── Config summary (for first-run / about dialogs) ────────────────────────────

def config_summary() -> dict:
    return {
        "drive_root":  str(DRIVE_ROOT),
        "shots_root":  str(SHOTS_ROOT),
        "ffmpeg":      FFMPEG_EXE,
        "db_found":    _find_db_path() is not None,
        "shots_exist": SHOTS_ROOT.exists(),
    }
