#!/usr/bin/env python3
"""
shot_viewer_qt.py  —  PySide6 rewrite of shot_viewer.py

SGI / Tron workstation aesthetic: custom QSS, Iosevka Term font,
PanelHeader corner-bracket widgets, WipeView fills the window.

Usage:
    python shot_viewer_qt.py
    python shot_viewer_qt.py --shot 999_TRL_1060
    python shot_viewer_qt.py --sequence 006_HONG_KONG_ATTACK

Keyboard shortcuts:
    W        toggle wipe / A          S   swap A<->B
    R/G/B    channel greyscale        O   overlay toggle
    ←/→      step frame               ↑/↓ prev/next shot
    Space    hold=play, release=pause (double-tap=reset to frame 0)
    E drag   exposure    C drag  gamma    V drag  saturation
    BackSpace reset grade             Shift+R  reload
    \        cinema mode (image only, fullscreen — press again to exit)
"""
from __future__ import annotations

import argparse
import json
import math
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageDraw, ImageEnhance
except ImportError:
    print("Pillow required:  pip install Pillow"); sys.exit(1)

from PySide6.QtCore import (
    Qt, QTimer, QThread, QObject, QEvent, QPoint, QRect, QSize, Signal, Slot,
    QCoreApplication, QMimeData,
)
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QPainter, QPen, QBrush, QPixmap, QImage,
    QKeySequence, QCursor, QPainterPath, QAction, QLinearGradient,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QComboBox,
    QPushButton, QRadioButton, QButtonGroup, QListWidget, QListWidgetItem,
    QPlainTextEdit, QScrollArea, QSplitter, QSizePolicy,
    QHBoxLayout, QVBoxLayout, QGridLayout, QStatusBar, QToolButton, QSlider,
    QDialog, QLineEdit, QMessageBox, QFileDialog, QMenu, QAbstractItemView,
)
from PySide6.QtWidgets import QListView

# ── Project paths ─────────────────────────────────────────────────────────────

_REPO = Path(__file__).parent
from seed_viewer.paths import _find_db_path as _sv_find_db_path
DB_PATH = _sv_find_db_path() or (Path(__file__).parent / 'shot_database.json')
FONT_DIR = _REPO / "assets" / "fonts"

try:
    from seed_viewer.paths import (
        find_shot_folder as _pipeline_find_shot_folder,
        load_omitted, load_ccb,
    )
    _USE_PIPELINE = True
except ImportError:
    _USE_PIPELINE = False
    def load_omitted(): return set()
    def load_ccb():     return set()

# ── Resolutions ───────────────────────────────────────────────────────────────

RESOLUTIONS = {"HD": (1280, 720), "UHD": (1920, 1080)}

# ── Layer definitions ─────────────────────────────────────────────────────────

LAYERS: dict[str, dict] = {
    "plate":      {"label": "Plate 4K",       "globs": ["plate/mp4/*_4k.mp4","plate/mp4/*_4K_*.mp4",
                                                          "mp4/*_4k.mp4","mp4/*_4K_*.mp4","mp4/*.mp4"],           "is_video": True},
    "plate_hd":   {"label": "Plate HD",       "globs": ["plate/mp4/*_hd.mp4","plate/mp4/*_HD_*.mp4",
                                                          "mp4/*_hd.mp4","mp4/*_HD_*.mp4"],                       "is_video": True},
    "ffs":        {"label": "First Frame",    "globs": ["keyframes/ff/*.png",
                                                          "firstframe/*.png","mp4/*_ff_*.png"],                    "is_video": False},
    "wai":        {"label": "WAI",            "globs": ["vfx/*_wai_V*.mp4","vfx/*_wai_*.mp4"],                   "is_video": True},
    "comp":       {"label": "Comp",           "globs": ["comp/*/*.mp4"],                                          "is_video": True},
    "upres":      {"label": "Upres/Magnific", "globs": ["upres/*.mp4"],                                           "is_video": True},
    "depth":      {"label": "Depth",          "globs": ["depth/*_review.mp4"],                                    "is_video": True},
    "mask":       {"label": "Mask (comb)",    "globs": ["masks/*_combined_*.mp4"],                                "is_video": True},
    "mask_roto":  {"label": "Mask (roto)",    "globs": ["masks/*_rot_rtm-*.mp4"],                                 "is_video": True},
    "faceswap":   {"label": "Faceswap",       "globs": ["faceswap/*.mp4"],                                        "is_video": True},
    "uvx_albedo": {"label": "UVX Albedo",     "globs": ["unividx/intrinsic_V*/*_albedo.mp4"],                    "is_video": True},
    "uvx_irrad":  {"label": "UVX Irradiance", "globs": ["unividx/intrinsic_V*/*_irradiance.mp4"],                "is_video": True},
    "uvx_normal": {"label": "UVX Normal",     "globs": ["unividx/*/normal*.mp4","unividx/*/*_normal.mp4"],        "is_video": True},
    "uvx_alpha":  {"label": "UVX Alpha",      "globs": ["unividx/alpha_V*/*_alpha.mp4"],                         "is_video": True},
    "uvx_fg":     {"label": "UVX FG",         "globs": ["unividx/alpha_V*/*_fg.mp4"],                            "is_video": True},
    "uvx_bg":     {"label": "UVX BG",         "globs": ["unividx/alpha_V*/*_bg.mp4"],                            "is_video": True},
    "uvx_relit":  {"label": "UVX Relit",      "globs": ["unividx/relight_V*/*_rgb.mp4"],                         "is_video": True},
}
CASCADE_ORDER = ["comp", "wai", "ffs", "plate_hd", "plate"]
THUMB_W, THUMB_H = 160, 90    # 2-column grid (16:9, fits 2 per row in ~360px browser)

CACHE_DIR = Path(tempfile.gettempdir()) / "ks_shot_viewer"
CACHE_DIR.mkdir(exist_ok=True)
def _resolve_ff_bin(name: str) -> str:
    """Resolve ffmpeg/ffprobe: bundled copy first (PyInstaller), then PATH."""
    if getattr(sys, "frozen", False):
        import platform as _p
        ext = ".exe" if _p.system() == "Windows" else ""
        b = Path(getattr(sys, "_MEIPASS", "")) / "ffmpeg" / f"{name}{ext}"
        if b.exists():
            return str(b)
    found = shutil.which(name)
    return found if found else name

_FFMPEG  = _resolve_ff_bin("ffmpeg")
_FFPROBE = _resolve_ff_bin("ffprobe")
# Suppress console windows on Windows for every subprocess call
_POPEN_FLAGS: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ── SGI / Tron palette  (v06 spec) ───────────────────────────────────────────

C_DARK     = "#06080C"   # Background
C_BAR      = "#0B0E14"   # Panels
C_BAR2     = "#080A0F"   # Slightly darker
C_BTN      = "#0F1520"   # Buttons
C_FG       = "#E0F7FF"   # Text
C_DIM      = "#6B7C8E"   # Muted
C_DIM2     = "#1A1F2A"   # Borders
C_CYAN     = "#00F0FF"   # Accent cyan   (active states)
C_CYAN_DIM = "#003344"   # Dim cyan
C_PURPLE   = "#9000FF"   # Accent purple (hover states)
C_TEAL     = "#00e5cc"
C_AMBER    = "#f4c542"
C_AMBER2   = "#3a2800"
C_GREEN    = "#1aff6a"
C_RED      = "#ff4444"
C_CA       = "#0a2a40"
C_CB       = "#2a1400"

# ── QSS ───────────────────────────────────────────────────────────────────────

QSS = f"""
QMainWindow, QWidget {{
    background: {C_DARK};
    color: {C_FG};
    font-family: "Share Tech Mono", "Consolas", "Iosevka Term", "Courier New", monospace;
    font-size: 9pt;
}}
QMenuBar {{
    background: {C_BAR};
    color: {C_FG};
    border-bottom: 1px solid {C_CYAN_DIM};
    padding: 1px 0;
    spacing: 0;
}}
QMenuBar::item {{
    padding: 4px 10px;
    background: transparent;
}}
QMenuBar::item:selected, QMenuBar::item:pressed {{
    background: {C_DIM2};
    color: {C_CYAN};
}}
QMenu {{
    background: #0d1220;
    color: {C_FG};
    border: 1px solid {C_CYAN_DIM};
    padding: 2px 0;
}}
QMenu::item {{
    padding: 4px 28px 4px 14px;
}}
QMenu::item:selected {{
    background: #003344;
    color: {C_CYAN};
}}
QMenu::separator {{
    height: 1px;
    background: {C_CYAN_DIM};
    margin: 2px 8px;
}}
QComboBox {{
    background: #0d1220;
    color: {C_CYAN};
    border: 1px solid {C_CYAN_DIM};
    border-radius: 0;
    padding: 1px 6px;
    min-height: 18px;
}}
QComboBox:hover {{ border-color: {C_CYAN}; }}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: right center;
    width: 14px;
    border-left: 1px solid {C_CYAN_DIM};
    background: {C_BAR};
}}
QComboBox QAbstractItemView {{
    background: #0d1220;
    color: {C_FG};
    border: 1px solid {C_CYAN_DIM};
    selection-background-color: #003344;
    selection-color: {C_CYAN};
    outline: none;
    padding: 2px;
}}
QPushButton {{
    background: {C_BTN};
    color: {C_FG};
    border: 1px solid {C_DIM2};
    padding: 2px 10px;
    min-height: 18px;
}}
QPushButton:hover {{ border-color: {C_PURPLE}; color: {C_PURPLE}; }}
QPushButton:pressed {{ background: #1a0030; border-color: {C_PURPLE}; }}
QPushButton:checked {{ background: #001830; border-color: {C_CYAN}; color: {C_CYAN}; }}
QRadioButton {{ color: {C_DIM}; spacing: 4px; }}
QRadioButton:checked {{ color: {C_FG}; }}
QRadioButton::indicator {{
    width: 10px; height: 10px;
    border: 1px solid {C_DIM};
    border-radius: 5px;
    background: {C_DARK};
}}
QRadioButton::indicator:checked {{
    background: {C_CYAN};
    border-color: {C_CYAN};
}}
QScrollBar:vertical {{
    background: {C_BAR2};
    width: 6px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {C_DIM2};
    min-height: 24px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{ background: {C_CYAN_DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {C_BAR2};
    height: 6px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background: {C_DIM2};
    min-width: 24px;
    border-radius: 3px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QSplitter::handle {{
    background: {C_CYAN_DIM};
}}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QListWidget {{
    background: {C_DARK};
    border: none;
    outline: none;
}}
QListWidget::item {{ border: none; padding: 0; }}
QListWidget::item:selected {{ background: #001a2a; }}
QListWidget::item:hover {{ background: #0a0f18; }}
QPlainTextEdit, QTextEdit {{
    background: #050810;
    color: #4a6080;
    border: 1px solid {C_DIM2};
    selection-background-color: #1a0030;
    font-size: 8pt;
}}
QLineEdit {{
    background: {C_DARK};
    color: {C_CYAN};
    border: 1px solid {C_DIM2};
    padding: 1px 4px;
}}
QLineEdit:focus {{ border-color: {C_PURPLE}; }}
QLabel {{ background: transparent; }}
QStatusBar {{
    background: {C_BAR2};
    color: {C_DIM};
    border-top: 1px solid {C_CYAN_DIM};
    font-size: 8pt;
}}
QStatusBar::item {{ border: none; }}
QLineEdit {{
    background: #0d1220;
    color: {C_CYAN};
    border: 1px solid {C_CYAN_DIM};
    padding: 2px 4px;
}}
QLineEdit:focus {{ border-color: {C_CYAN}; }}
QDialog {{ background: {C_DARK}; }}
QMessageBox {{ background: {C_DARK}; }}
QToolTip {{
    background: #0d1220;
    color: {C_CYAN};
    border: 1px solid {C_CYAN_DIM};
    padding: 2px 6px;
    font-size: 8pt;
}}
QLabel {{ background: transparent; }}
QToolButton {{
    color: {C_CYAN};
    background: transparent;
    border: 1px solid transparent;
    padding: 2px;
    min-width: 22px;
    min-height: 22px;
    font-size: 10pt;
}}
QToolButton:hover  {{ border: 1px solid {C_CYAN}; }}
QToolButton:pressed {{ background: {C_CYAN_DIM}; }}
QSlider::groove:horizontal {{
    height: 4px;
    background: #0E1218;
    border: 1px solid {C_CYAN_DIM};
}}
QSlider::handle:horizontal {{
    background: {C_CYAN};
    border: none;
    width: 8px;
    height: 14px;
    margin: -5px 0;
}}
QSlider::sub-page:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {C_CYAN}, stop:1 {C_PURPLE});
    border: 1px solid {C_CYAN_DIM};
}}
"""

# ── Main-thread dispatcher ────────────────────────────────────────────────────

class _CallEvent(QEvent):
    _etype = QEvent.Type(QEvent.registerEventType())
    def __init__(self, fn):
        super().__init__(self._etype)
        self.fn = fn

class _Dispatcher(QObject):
    _inst: Optional["_Dispatcher"] = None

    @classmethod
    def instance(cls) -> "_Dispatcher":
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def customEvent(self, ev: QEvent):
        if ev.type() == _CallEvent._etype:
            ev.fn()

def post_to_main(fn):
    """Post callable to main thread. Thread-safe."""
    QCoreApplication.postEvent(_Dispatcher.instance(), _CallEvent(fn))


class _SpaceFilter(QObject):
    """
    Application-level event filter that intercepts Space before any focused
    widget (QComboBox, QPushButton, etc.) can consume it.  This ensures
    hold-to-play always works regardless of which panel has focus.
    """
    def __init__(self, viewer: "ShotViewerApp", parent=None):
        super().__init__(parent)
        self._viewer = viewer

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Space:
            fw = QApplication.focusWidget()
            if isinstance(fw, (QLineEdit, QPlainTextEdit)):
                return False   # let text inputs keep Space
            if not ev.isAutoRepeat():
                self._viewer._on_space_press()
            return True        # swallow initial AND auto-repeat — never reaches combobox
        if ev.type() == QEvent.Type.KeyRelease and ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat():
            fw = QApplication.focusWidget()
            if isinstance(fw, (QLineEdit, QPlainTextEdit)):
                return False
            self._viewer._on_space_release()
            return True
        return False


# ── FrameCache (logic verbatim from shot_viewer.py) ───────────────────────────

class FrameCache:
    MAX_BYTES = 2 * 1024 * 1024 * 1024

    def __init__(self, src: Path, w: int, h: int, on_progress=None):
        self.src         = src
        self.w           = w
        self.h           = h
        self._on_progress = on_progress
        self._frames: list[bytes] = []
        self._lock       = threading.Lock()
        self._stop       = threading.Event()
        self.n_total: int  = 0
        self.loaded:  int  = 0
        self.done:    bool = False
        self.capped:  bool = False
        threading.Thread(target=self._decode, daemon=True).start()

    def stop(self):
        self._stop.set()

    def get_frame(self, idx: int) -> Optional[Image.Image]:
        with self._lock:
            if idx < len(self._frames):
                return Image.frombytes("RGB", (self.w, self.h), self._frames[idx])
        return None

    def _decode(self):
        try:
            r = subprocess.run(
                [_FFPROBE,"-v","quiet","-select_streams","v:0",
                 "-count_packets","-show_entries","stream=nb_read_packets",
                 "-of","csv=p=0", str(self.src)],
                capture_output=True, text=True, timeout=10,
                creationflags=_POPEN_FLAGS)
            self.n_total = max(1, int(r.stdout.strip()))
        except Exception:
            self.n_total = 0

        frame_bytes = self.w * self.h * 3
        proc = subprocess.Popen(
            [_FFMPEG, "-i", str(self.src),
             "-f","rawvideo","-pix_fmt","rgb24",
             "-vf", f"scale={self.w}:{self.h}:force_original_aspect_ratio=decrease,pad={self.w}:{self.h}:(ow-iw)/2:(oh-ih)/2:color=#07090f", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=_POPEN_FLAGS)
        total_bytes = 0
        try:
            while not self._stop.is_set():
                raw = proc.stdout.read(frame_bytes)
                if len(raw) < frame_bytes:
                    break
                total_bytes += frame_bytes
                if total_bytes > self.MAX_BYTES:
                    self.capped = True
                    break
                with self._lock:
                    self._frames.append(raw)
                    self.loaded = len(self._frames)
                    if self.n_total == 0:
                        self.n_total = self.loaded
                if self._on_progress:
                    post_to_main(self._on_progress)
        finally:
            proc.kill(); proc.wait()
        with self._lock:
            self.loaded = len(self._frames)
            if self.n_total == 0 or self.n_total < self.loaded:
                self.n_total = self.loaded
        self.done = True
        if self._on_progress:
            post_to_main(self._on_progress)

# ── DB / filesystem helpers ───────────────────────────────────────────────────

def load_db() -> dict:
    from seed_viewer.paths import _load_db as _sv_load_db
    return _sv_load_db()

def load_delivered_shots() -> set:
    log = _REPO / "delivery_log.json"
    if not log.exists():
        return set()
    try:
        entries = json.loads(log.read_text(encoding="utf-8"))
        return {e["shot_id"] for e in entries if "shot_id" in e}
    except Exception:
        return set()

@lru_cache(maxsize=512)
def find_shot_folder(shotcode: str) -> Optional[Path]:
    if _USE_PIPELINE:
        return _pipeline_find_shot_folder(shotcode)
    return None

def find_all_versions(sf: Path, layer: str) -> list[Path]:
    spec = LAYERS.get(layer)
    if not spec:
        return []
    results = []
    for pat in spec["globs"]:
        results.extend(sf.glob(pat))
    if layer in ("plate","plate_hd"):
        results = [p for p in results if "_ff_" not in p.stem]
    def _key(p: Path):
        m = re.search(r'[vV](\d+)', p.stem)
        ver = int(m.group(1)) if m else 0
        is_hd = bool(re.search(r'[_-]hd[_.\-]', p.stem, re.IGNORECASE))
        return (ver, 0 if is_hd else 1)
    return sorted(set(results), key=_key, reverse=True)

def find_source(sf: Path, layer: str, version_idx: int = 0) -> Optional[Path]:
    vs = find_all_versions(sf, layer)
    return vs[version_idx] if version_idx < len(vs) else None

def find_cascade(sf: Path) -> tuple[Optional[Path], str]:
    for layer in CASCADE_ORDER:
        p = find_source(sf, layer)
        if p:
            return p, layer
    return None, ""

def _ver_label(path: Path, idx: int) -> str:
    m = re.search(r'[vV](\d+)', path.stem)
    tag = f"v{int(m.group(1)):04d}" if m else f"#{idx}"
    return f"{tag}{'  (latest)' if idx==0 else ''}  {path.name[:36]}"

def _placeholder(w: int, h: int, text: str = "") -> Image.Image:
    img = Image.new("RGB", (w, h), (7, 9, 16))
    if text:
        d = ImageDraw.Draw(img)
        d.text((w//2, h//2), text, fill=(60, 80, 80), anchor="mm")
    return img

def _pil_to_qpixmap(img: Image.Image) -> QPixmap:
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3,
                  QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())

def get_first_frame(src: Path, layer: str) -> Optional[Path]:
    spec = LAYERS.get(layer, {})
    if not spec.get("is_video", False):
        return src
    mtime  = int(src.stat().st_mtime)
    cached = CACHE_DIR / f"{mtime}_{src.stem}.jpg"
    if cached.exists():
        return cached
    subprocess.run([_FFMPEG,"-y","-i",str(src),"-vframes","1","-q:v","3",str(cached)],
                   capture_output=True, creationflags=_POPEN_FLAGS)
    return cached if cached.exists() else None

def get_last_frame(src: Path, layer: str) -> Optional[Path]:
    spec = LAYERS.get(layer, {})
    if not spec.get("is_video", False):
        return src
    mtime  = int(src.stat().st_mtime)
    cached = CACHE_DIR / f"{mtime}_{src.stem}_last.jpg"
    if cached.exists():
        return cached
    probe = subprocess.run(
        [_FFPROBE,"-v","quiet","-select_streams","v:0",
         "-count_packets","-show_entries","stream=nb_read_packets",
         "-of","csv=p=0", str(src)],
        capture_output=True, text=True, creationflags=_POPEN_FLAGS)
    try:
        n = int(probe.stdout.strip())
        subprocess.run(
            [_FFMPEG,"-y","-i",str(src),"-vf",
             f"select=eq(n\\,{max(0,n-1)})","-vsync","0","-vframes","1","-q:v","3",str(cached)],
            capture_output=True, creationflags=_POPEN_FLAGS)
    except Exception:
        subprocess.run(
            [_FFMPEG,"-y","-sseof","-1","-i",str(src),"-vframes","1","-q:v","3",str(cached)],
            capture_output=True, creationflags=_POPEN_FLAGS)
    return cached if cached.exists() else None

def get_frame_at(src: Path, layer: str, frame_n: int) -> Optional[Path]:
    spec = LAYERS.get(layer, {})
    if not spec.get("is_video", False):
        return src
    mtime  = int(src.stat().st_mtime)
    cached = CACHE_DIR / f"{mtime}_{src.stem}_f{frame_n}.jpg"
    if cached.exists():
        return cached
    subprocess.run(
        [_FFMPEG,"-y","-i",str(src),"-vf",
         f"select=eq(n\\,{frame_n})","-vsync","0","-vframes","1","-q:v","3",str(cached)],
        capture_output=True, creationflags=_POPEN_FLAGS)
    return cached if cached.exists() else None

def load_display_image(src: Optional[Path], layer: str, w: int, h: int,
                       last: bool = False, custom_frame: Optional[int] = None) -> Image.Image:
    if src is None:
        return _placeholder(w, h, "no source")
    frame = (get_frame_at(src, layer, custom_frame) if custom_frame is not None
             else get_last_frame(src, layer) if last
             else get_first_frame(src, layer))
    if not frame or not frame.exists():
        return _placeholder(w, h, "extraction failed")
    try:
        raw = Image.open(frame).convert("RGB")
    except Exception:
        return _placeholder(w, h, "load error")
    sw, sh = raw.size
    # Letterbox: fit entire frame, no crop.  Black bars match palette bg.
    scale = min(w / sw, h / sh)
    nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
    if nw == w and nh == h:
        return raw.resize((w, h), Image.LANCZOS)
    scaled = raw.resize((nw, nh), Image.LANCZOS)
    out = Image.new("RGB", (w, h), (7, 9, 16))
    out.paste(scaled, ((w - nw) // 2, (h - nh) // 2))
    return out

def _draw_pix(p: QPainter, pix: QPixmap, W: int, H: int):
    pw, ph = pix.width(), pix.height()
    if pw == W and ph == H:
        p.drawPixmap(0, 0, pix)
    else:
        p.drawPixmap(0, 0, W, H, pix)


# ── PanelHeader widget ────────────────────────────────────────────────────────

class PanelHeader(QWidget):
    """Thin section header: colored left accent + title text + horizontal rule."""
    def __init__(self, title: str, color: str = C_CYAN,
                 readout: str = "", parent=None):
        super().__init__(parent)
        self._title   = title.upper()
        self._color   = QColor(color)
        self._readout = readout
        self.setFixedHeight(20)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def set_readout(self, text: str):
        self._readout = text
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QColor(C_BAR))

        # 3px left accent bar
        p.fillRect(0, 0, 3, H, self._color)

        f = p.font(); f.setPointSize(7); f.setBold(True); p.setFont(f)
        fm = p.fontMetrics()

        # Title
        p.setPen(self._color)
        title_text = f"  {self._title}  "
        tw = fm.horizontalAdvance(title_text)
        p.drawText(3, 0, tw + 3, H, Qt.AlignmentFlag.AlignVCenter, title_text)
        x0 = 3 + tw + 3

        # Right readout
        rw = 0
        if self._readout:
            rtext = f"  {self._readout}  "
            rw = fm.horizontalAdvance(rtext)
            p.setPen(QColor(C_DIM))
            p.drawText(W - rw, 0, rw, H, Qt.AlignmentFlag.AlignVCenter, rtext)

        # Horizontal rule between title and readout
        rule_y = H // 2
        dim_color = QColor(self._color)
        dim_color.setAlphaF(0.25)
        p.setPen(QPen(dim_color, 1))
        p.drawLine(x0, rule_y, W - rw - 4, rule_y)
        p.end()


# ── ShotCell widget (used in QListWidget) ─────────────────────────────────────

class ShotCell(QWidget):
    """One browser row: thin accent + thumbnail + shotcode + layer badge."""
    def __init__(self, shotcode: str, accent: str = C_CYAN_DIM,
                 is_omit: bool = False, delivered: bool = False, parent=None):
        super().__init__(parent)
        self._shotcode = shotcode
        self._accent   = accent
        self._is_omit  = is_omit
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        bg = QColor("#140808" if is_omit else C_DARK)
        pal = self.palette(); pal.setColor(self.backgroundRole(), bg)
        self.setAutoFillBackground(True); self.setPalette(pal)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        # Accent top bar (2px neon line)
        accent_bar = QWidget(self)
        accent_bar.setFixedHeight(2)
        accent_bar.setAutoFillBackground(True)
        ap = accent_bar.palette(); ap.setColor(accent_bar.backgroundRole(), QColor(accent))
        accent_bar.setPalette(ap)
        layout.addWidget(accent_bar)

        # Thumbnail
        self.thumb_lbl = QLabel(self)
        self.thumb_lbl.setFixedSize(THUMB_W, THUMB_H)
        self.thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph = _pil_to_qpixmap(_placeholder(THUMB_W, THUMB_H, "..."))
        self.thumb_lbl.setPixmap(ph)
        layout.addWidget(self.thumb_lbl)

        # Shot code + badge (compact single row)
        info_row = QWidget(self)
        info_row.setAutoFillBackground(True)
        info_row.setPalette(pal)
        info_layout = QHBoxLayout(info_row)
        info_layout.setContentsMargins(3, 1, 3, 1)
        info_layout.setSpacing(2)

        code_txt = shotcode.split("_")[-1] if "_" in shotcode else shotcode
        code_lbl = QLabel(code_txt, info_row)
        base_style = f"font-size: 7pt; font-weight: bold; background: transparent;"
        if is_omit:
            code_lbl.setStyleSheet(f"color: #884444; text-decoration: line-through; {base_style}")
        else:
            code_lbl.setStyleSheet(f"color: {C_FG}; {base_style}")
        info_layout.addWidget(code_lbl)
        info_layout.addStretch()

        if delivered:
            dlv = QLabel("DLV", info_row)
            dlv.setStyleSheet(f"color: {C_GREEN}; font-size: 6pt; background: transparent;")
            info_layout.addWidget(dlv)
        elif is_omit:
            om = QLabel("OMT", info_row)
            om.setStyleSheet("color: #cc3333; font-size: 6pt; background: transparent;")
            info_layout.addWidget(om)

        layout.addWidget(info_row)

        # Source label (clipped to width)
        self.src_lbl = QLabel("", self)
        self.src_lbl.setStyleSheet(
            f"color: {C_DIM}; font-size: 6pt; padding: 0 3px 1px 3px; background: transparent;")
        layout.addWidget(self.src_lbl)

    def set_thumbnail(self, pil_img: Image.Image):
        try:
            self.thumb_lbl.setPixmap(_pil_to_qpixmap(pil_img))
        except RuntimeError:
            pass

    def set_source(self, text: str):
        try:
            self.src_lbl.setText(text)
        except RuntimeError:
            pass

    def sizeHint(self) -> QSize:
        return QSize(THUMB_W + 2, THUMB_H + 26)


# ── ShotBrowser ───────────────────────────────────────────────────────────────

class ShotBrowser(QWidget):
    shot_selected  = Signal(str)       # shotcode
    seq_changed    = Signal(str)       # sequence name

    def __init__(self, db: dict, delivered: set, parent=None):
        super().__init__(parent)
        self.db        = db
        self.delivered = delivered
        self._pool     = ThreadPoolExecutor(max_workers=4)
        self._cells: dict[str, ShotCell] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ───────────────────────────────────────────────────────────
        self._hdr = PanelHeader("SHOT BROWSER", C_AMBER)
        layout.addWidget(self._hdr)

        # Filter bar
        filter_bar = QWidget(self)
        filter_bar.setAutoFillBackground(True)
        fb_pal = filter_bar.palette()
        fb_pal.setColor(filter_bar.backgroundRole(), QColor(C_BAR))
        filter_bar.setPalette(fb_pal)
        fb_layout = QHBoxLayout(filter_bar)
        fb_layout.setContentsMargins(4, 3, 4, 3)
        fb_layout.setSpacing(4)

        seqs = sorted({v.get("sequence","") for v in db.values() if v.get("sequence")})
        self._seq_cb = QComboBox(filter_bar)
        self._seq_cb.addItems(["ALL"] + seqs)
        self._seq_cb.setFixedHeight(20)
        self._seq_cb.currentTextChanged.connect(self._on_seq_change)
        fb_layout.addWidget(self._seq_cb, stretch=1)

        all_labels = [LAYERS[k]["label"] for k in LAYERS] + ["Cascade"]
        self._layer_cb = QComboBox(filter_bar)
        self._layer_cb.addItems(all_labels)
        self._layer_cb.setCurrentText("Plate HD")
        self._layer_cb.setFixedWidth(90)
        self._layer_cb.setFixedHeight(20)
        self._layer_cb.currentTextChanged.connect(lambda _: self._refresh())
        fb_layout.addWidget(self._layer_cb)

        refresh_btn = QPushButton("⟳", filter_bar)
        refresh_btn.setFixedSize(22, 20)
        refresh_btn.clicked.connect(self._refresh)
        fb_layout.addWidget(refresh_btn)

        self._count_lbl = QLabel("", filter_bar)
        self._count_lbl.setStyleSheet(f"color: {C_DIM}; font-size: 7pt;")
        fb_layout.addWidget(self._count_lbl)

        layout.addWidget(filter_bar)

        # Separator
        sep = QFrame(self)
        sep.setFixedHeight(1)
        sep.setAutoFillBackground(True)
        sp = sep.palette(); sp.setColor(sep.backgroundRole(), QColor(C_CYAN_DIM))
        sep.setPalette(sp)
        layout.addWidget(sep)

        # ── Grid (2-column) ──────────────────────────────────────────────────
        self._list = QListWidget(self)
        self._list.setFlow(QListView.Flow.LeftToRight)
        self._list.setWrapping(True)
        self._list.setResizeMode(QListView.ResizeMode.Adjust)
        self._list.setUniformItemSizes(True)
        self._list.setSpacing(2)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.itemClicked.connect(self._on_item_click)
        layout.addWidget(self._list, stretch=1)

        self._status_lbl = QLabel("", self)
        self._status_lbl.setStyleSheet(f"color: {C_DIM}; font-size: 7pt; padding: 2px 6px;")
        layout.addWidget(self._status_lbl)

        # Min width = 2 cells + spacing + scrollbar
        self.setMinimumWidth(2 * (THUMB_W + 2) + 3 * 2 + 20)
        QTimer.singleShot(50, self._refresh)

    def select_shot(self, shotcode: str):
        for i in range(self._list.count()):
            item = self._list.item(i)
            w = self._list.itemWidget(item)
            if w and getattr(w, "_shotcode", "") == shotcode:
                self._list.setCurrentItem(item)
                self._list.scrollToItem(item)
                return

    def current_layer_key(self) -> str:
        label = self._layer_cb.currentText()
        if label == "Cascade":
            return "cascade"
        for k, v in LAYERS.items():
            if v["label"] == label:
                return k
        return "ffs"

    def current_seq(self) -> str:
        return self._seq_cb.currentText()

    def set_seq(self, seq: str):
        idx = self._seq_cb.findText(seq)
        if idx >= 0:
            self._seq_cb.setCurrentIndex(idx)

    def _on_seq_change(self, seq: str):
        self._refresh()
        self.seq_changed.emit(seq)

    def _on_item_click(self, item: QListWidgetItem):
        w = self._list.itemWidget(item)
        if w and hasattr(w, "_shotcode") and not w._is_omit:
            self.shot_selected.emit(w._shotcode)

    def _refresh(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._pool = ThreadPoolExecutor(max_workers=4)
        self._cells.clear()
        self._list.clear()

        seq   = self._seq_cb.currentText()
        layer = self.current_layer_key()

        try:
            omitted = load_omitted()
        except Exception:
            omitted = set()

        shots = sorted(
            self.db.values(),
            key=lambda s: (
                s.get("sequence",""),
                int(s["shotcode"].split("_")[-1])
                if s.get("shotcode","").split("_")[-1].isdigit() else 0,
            )
        )
        if seq != "ALL":
            shots = [s for s in shots if s.get("sequence") == seq]

        self._status_lbl.setText(f"Loading {len(shots)} shots…")
        self._count_lbl.setText(str(len(shots)))

        for shot in shots:
            sc     = shot.get("shotcode","")
            is_omit = sc in omitted
            accent  = "#661111" if is_omit else C_CYAN_DIM
            cell = ShotCell(sc, accent=accent, is_omit=is_omit,
                            delivered=(sc in self.delivered))
            self._cells[sc] = cell
            item = QListWidgetItem(self._list)
            item.setSizeHint(cell.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, cell)
            if not is_omit:
                self._pool.submit(self._load_async, cell, sc, layer)

        self._status_lbl.setText(f"{len(shots)} shots")

    def _load_async(self, cell: ShotCell, shotcode: str, layer: str):
        sf = find_shot_folder(shotcode)
        if sf:
            src, used = (find_cascade(sf) if layer == "cascade"
                         else (find_source(sf, layer), layer))
        else:
            src, used = None, ""
        img = load_display_image(src, used, THUMB_W, THUMB_H)
        src_text = f"[{used}]  {src.name[:32] if src else '—'}"
        post_to_main(lambda c=cell, i=img, t=src_text: (c.set_thumbnail(i), c.set_source(t)))


# ── WipeView ─────────────────────────────────────────────────────────────────

class WipeView(QWidget):
    """
    PIL-composite wipe/A/B viewer with zoom/pan and RAM-cache playback.
    All composite math identical to shot_viewer.py WipeCanvas.
    """
    cache_progress = Signal()          # emitted when cache loads a new frame
    frame_changed  = Signal(int, int)  # (current_frame, total_frames)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 225)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SplitHCursor)

        self.disp_w = 1280
        self.disp_h = 720
        self.wipe_x = 640
        self.mode   = "wipe"
        self._drag  = False

        self.img_a = _placeholder(self.disp_w, self.disp_h, "A")
        self.img_b = _placeholder(self.disp_w, self.disp_h, "B")

        self._zoom  = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._mid_last: Optional[QPoint] = None

        self._channel: Optional[str] = None
        self._overlay_on    = True
        self._overlay_lines: list[str] = []

        self.path_a: Optional[Path] = None
        self.path_b: Optional[Path] = None
        self._play_stop  = threading.Event()
        self._playing    = False
        self._play_wipe  = False
        self._cache_a: Optional[FrameCache] = None
        self._cache_b: Optional[FrameCache] = None
        self._play_cache: Optional[FrameCache] = None
        self._play_label_ram = "A"
        self._sched_start_time  = 0.0
        self._sched_start_frame = 0
        self._play_frame_num = 0
        self._is_frozen      = False
        self._last_play_img: Optional[Image.Image] = None
        self._last_play_fa:  Optional[Image.Image] = None
        self._last_play_fb:  Optional[Image.Image] = None
        self._last_play_label = ""
        self._last_space_release = 0.0

        self._overscan  = 0.0   # 0..0.10 — scales image out from center
        self._safe_area = 0.0   # 0 = off, else 0.50..1.00

        self._pixmap:   Optional[QPixmap] = None   # playback composite
        self._pixmap_a: Optional[QPixmap] = None   # processed A side (zoom/channel applied)
        self._pixmap_b: Optional[QPixmap] = None   # processed B side
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._apply_resize)
        self._pending_resize: Optional[tuple[int,int]] = None

        self.cache_progress.connect(self._cache_tick, Qt.ConnectionType.QueuedConnection)
        self._render()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_a(self, img: Image.Image):
        self.img_a = img
        self._last_play_img = self._last_play_fa = self._last_play_fb = None
        self._play_frame_num = 0
        self._is_frozen = False
        if self._play_cache is self._cache_a:
            self._play_cache = None
        self._render()

    def set_b(self, img: Image.Image):
        self.img_b = img
        self._last_play_img = self._last_play_fa = self._last_play_fb = None
        self._play_frame_num = 0
        self._is_frozen = False
        if self._play_cache is self._cache_b:
            self._play_cache = None
        self._render()

    def set_mode(self, m: str):
        self.mode = m
        self._render()

    def set_wipe_x(self, x: int):
        self.wipe_x = max(0, min(self.disp_w, x))
        self.update()   # GPU-only repaint; no PIL work

    def set_overlay(self, lines: list[str]):
        self._overlay_lines = lines
        self._render()

    def resize_canvas(self, w: int, h: int):
        if self.disp_w > 0:
            self.wipe_x = int(self.wipe_x * w / self.disp_w)
        else:
            self.wipe_x = w // 2
        self.disp_w, self.disp_h = w, h

    def load_source(self, side: str, path: Optional[Path]):
        _VIDEO_EXTS = {".mp4",".mov",".mxf",".avi",".mkv"}
        old = self._cache_a if side == "a" else self._cache_b
        if old:
            old.stop()
        if path and path.exists() and path.suffix.lower() in _VIDEO_EXTS:
            prog_cb = lambda: self.cache_progress.emit()
            cache = FrameCache(path, self.disp_w, self.disp_h, on_progress=prog_cb)
        else:
            cache = None
        if side == "a":
            self._cache_a = cache
        else:
            self._cache_b = cache
        QTimer.singleShot(250, self._cache_bar_tick)

    def step_frame(self, delta: int):
        if self._playing:
            return
        target = max(0, self._play_frame_num + delta)
        if self.mode == "wipe":
            ca, cb = self._cache_a, self._cache_b
            # Don't step past what either still-loading cache has decoded yet.
            if ca and target >= ca.loaded and not (ca.done or ca.capped): return
            if cb and target >= cb.loaded and not (cb.done or cb.capped): return
            # For a finished cache that's shorter, hold its last frame (stay in sync).
            if ca and ca.loaded > 0: target = min(target, ca.loaded - 1)
            if cb and cb.loaded > 0: target = min(target, cb.loaded - 1)
            fa = ca.get_frame(target) if ca else None
            fb = cb.get_frame(target) if cb else None
            fa = fa or self.img_a
            fb = fb or self.img_b
            self._play_frame_num  = target
            self._last_play_fa    = fa
            self._last_play_fb    = fb
            self._last_play_img   = fa
            self._last_play_label = "A|B"
            self._is_frozen       = True
            total = (ca.n_total if ca else 0) or (cb.n_total if cb else 0)
            self.frame_changed.emit(target, total)
        else:
            use_b = (self.mode == "b")
            cache = self._cache_b if use_b else self._cache_a
            frame = cache.get_frame(target) if cache else None
            if frame is None:
                return
            self._play_frame_num  = target
            self._last_play_img   = frame
            self._last_play_fa    = None
            self._last_play_fb    = None
            self._last_play_label = "B" if use_b else "A"
            self._is_frozen       = True
            self.frame_changed.emit(target, cache.n_total if cache else 0)
        self._render()

    def seek_frame(self, n: int):
        if self._playing:
            return
        ca, cb = self._cache_a, self._cache_b
        total = (ca.n_total if ca else 0) or (cb.n_total if cb else 0)
        n = max(0, min(n, max(0, total - 1)))
        self._play_frame_num = n
        fa = ca.get_frame(n) if ca else None
        fb = cb.get_frame(n) if cb else None
        fa = fa or self.img_a
        fb = fb or self.img_b
        self._last_play_fa    = fa
        self._last_play_fb    = fb
        self._last_play_img   = fa
        self._last_play_label = "A|B"
        self._is_frozen       = True
        self._render()
        self.frame_changed.emit(n, total)

    def set_overscan(self, pct: float):
        self._overscan = max(0.0, min(0.10, pct))
        self._render()

    def set_safe_area(self, frac: float):
        self._safe_area = frac
        self.update()

    # ── Playback ─────────────────────────────────────────────────────────────

    def start_play(self, double_tap: bool = False):
        if double_tap:
            self._is_frozen      = False
            self._last_play_img  = None
            self._play_frame_num = 0
            self._render()
            return
        if self._playing:
            return
        self._is_frozen  = False
        self._playing    = True
        self._play_stop.clear()
        if self.mode == "wipe":
            self._play_wipe = True
            primary = self._cache_a or self._cache_b
            if primary is None:
                self._playing = False; self._play_wipe = False; return
            self._play_cache        = primary
            self._sched_start_frame = self._play_frame_num
            self._sched_start_time  = time.monotonic()
            self._schedule_next_frame()
        else:
            self._play_wipe = False
            use_b  = (self.mode == "b")
            cache  = self._cache_b if use_b else self._cache_a
            if cache is None:
                self._playing = False; return
            self._play_cache        = cache
            self._play_label_ram    = "B" if use_b else "A"
            self._sched_start_frame = self._play_frame_num
            self._sched_start_time  = time.monotonic()
            self._schedule_next_frame()

    def stop_play(self):
        self._last_space_release = time.time()
        self._play_stop.set()

    def _cache_tick(self):
        if not self._playing:
            self._render()

    def _cache_bar_tick(self):
        a_done = (self._cache_a is None or self._cache_a.done)
        b_done = (self._cache_b is None or self._cache_b.done)
        if not self._playing:
            self._render()
        if not (a_done and b_done):
            QTimer.singleShot(250, self._cache_bar_tick)

    def _schedule_next_frame(self):
        if self._play_stop.is_set() or not self._playing:
            self._playing    = False
            self._is_frozen  = True
            self._play_cache = None
            self._play_wipe  = False
            self._render()   # build _pixmap_a/_pixmap_b from frozen frames NOW
            return           # so wipe drag immediately after stop shows the right frame

        elapsed = time.monotonic() - self._sched_start_time
        target  = self._sched_start_frame + int(elapsed * 24.0)

        if self._play_wipe:
            ca, cb = self._cache_a, self._cache_b
            primary = self._play_cache
            if primary is None:
                QTimer.singleShot(0, lambda: self._on_play_done(True)); return

            # Wait for the SLOWER of the two caches — never show mismatched frames.
            if ca and target >= ca.loaded and not (ca.done or ca.capped):
                QTimer.singleShot(10, self._schedule_next_frame); return
            if cb and target >= cb.loaded and not (cb.done or cb.capped):
                QTimer.singleShot(10, self._schedule_next_frame); return

            # End when primary is exhausted
            if (primary.done or primary.capped) and target >= primary.loaded:
                QTimer.singleShot(int(1000/24)+5, lambda: self._on_play_done(True)); return

            # Both caches have this frame — fetch; if one ran shorter, hold its last frame.
            fa = ca.get_frame(target) if ca else None
            fb = cb.get_frame(target) if cb else None
            if fa is None and ca and ca.loaded > 0:
                fa = ca.get_frame(ca.loaded - 1)
            if fb is None and cb and cb.loaded > 0:
                fb = cb.get_frame(cb.loaded - 1)
            fa = fa or self.img_a
            fb = fb or self.img_b

            self._play_frame_num = target
            self._show_wipe_play_frame(fa, fb)
            self.frame_changed.emit(target, primary.n_total if primary else 0)
        else:
            cache = self._play_cache
            if cache is None:
                return
            if (cache.done or cache.capped) and target >= cache.loaded:
                QTimer.singleShot(0, lambda: self._on_play_done(True)); return
            frame = cache.get_frame(target)
            if frame is None:
                QTimer.singleShot(10, self._schedule_next_frame); return
            self._play_frame_num = target
            self._show_play_frame(frame, self._play_label_ram)
            self.frame_changed.emit(target, cache.n_total if cache else 0)
            if cache.done and target + 1 >= cache.loaded:
                QTimer.singleShot(int(1000/24)+5, lambda: self._on_play_done(True))
                return

        next_idx  = target + 1
        next_time = self._sched_start_time + (next_idx - self._sched_start_frame) / 24.0
        delay_ms  = max(1, int((next_time - time.monotonic()) * 1000))
        QTimer.singleShot(delay_ms, self._schedule_next_frame)

    def _on_play_done(self, _reached_end: bool = False):
        if _reached_end and not self._play_stop.is_set():
            # Space still held — loop back to frame 0
            self._play_frame_num    = 0
            self._sched_start_frame = 0
            self._sched_start_time  = time.monotonic()
            self._schedule_next_frame()
            return
        self._playing    = False
        self._is_frozen  = True
        self._play_cache = None
        self._play_wipe  = False
        self._render()

    def _show_play_frame(self, img: Image.Image, label: str):
        self._last_play_img   = img
        self._last_play_label = label
        self._last_play_fa    = None
        self._last_play_fb    = None
        self._pixmap = _pil_to_qpixmap(self._process(img))
        self.update()

    def _show_wipe_play_frame(self, fa: Optional[Image.Image],
                               fb: Optional[Image.Image]):
        a = self._process(fa) if fa else self._process(self.img_a)
        b = self._process(fb) if fb else self._process(self.img_b)
        comp = self._wipe_compose(a, b)
        self._last_play_fa    = fa
        self._last_play_fb    = fb
        self._last_play_img   = comp
        self._last_play_label = "A|B"
        self._pixmap = _pil_to_qpixmap(comp)
        self.update()

    # ── Composite / zoom helpers ──────────────────────────────────────────────

    def _clamp_pan(self):
        W, H = self.disp_w, self.disp_h
        self._pan_x = max(0.0, min(self._pan_x, W - W / self._zoom))
        self._pan_y = max(0.0, min(self._pan_y, H - H / self._zoom))

    def _zoom_image(self, img: Image.Image) -> Image.Image:
        if self._zoom == 1.0:
            return img
        W, H = self.disp_w, self.disp_h
        vw, vh = W / self._zoom, H / self._zoom
        x0, y0 = int(self._pan_x), int(self._pan_y)
        return img.crop((x0, y0, x0+int(vw), y0+int(vh))).resize((W,H), Image.BILINEAR)

    def _apply_channel(self, img: Image.Image) -> Image.Image:
        if self._channel is None:
            return img
        idx = {"r":0,"g":1,"b":2}[self._channel]
        return img.split()[idx].convert("RGB")

    def _process(self, img: Image.Image) -> Image.Image:
        if img.size != (self.disp_w, self.disp_h):
            img = img.resize((self.disp_w, self.disp_h), Image.BILINEAR)
        return self._zoom_image(self._apply_channel(img))

    def _wipe_compose(self, a: Image.Image, b: Image.Image) -> Image.Image:
        x = self.wipe_x
        comp = Image.new("RGB", (self.disp_w, self.disp_h))
        comp.paste(a.crop((0, 0, x, self.disp_h)), (0, 0))
        comp.paste(b.crop((x, 0, self.disp_w, self.disp_h)), (x, 0))
        return comp

    def _render(self):
        if self._playing:
            return
        # Sync disp size to actual widget size.
        ww, wh = self.width(), self.height()
        if ww > 10 and wh > 10 and (ww != self.disp_w or wh != self.disp_h):
            self.resize_canvas(ww, wh)

        if self._is_frozen and self._last_play_img is not None:
            # Frozen-on-frame: build per-side pixmaps so mode changes (W key, wipe drag) work.
            if self._last_play_fa is not None:
                a = self._process(self._last_play_fa)
                b = self._process(self._last_play_fb or self.img_b)
            elif self._last_play_label == "B":
                a = self._process(self.img_a)
                b = self._process(self._last_play_img)
            else:
                a = self._process(self._last_play_img)
                b = self._process(self.img_b)
            self._pixmap_a = _pil_to_qpixmap(a)
            self._pixmap_b = _pil_to_qpixmap(b)
            self._pixmap   = None
        else:
            # Normal display path: build per-side QPixmaps; compose in paintEvent.
            self._pixmap_a = _pil_to_qpixmap(self._process(self.img_a))
            self._pixmap_b = _pil_to_qpixmap(self._process(self.img_b))
            self._pixmap   = None
        self.update()

    # ── paintEvent ────────────────────────────────────────────────────────────

    def paintEvent(self, ev):
        p = QPainter(self)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QColor("#000005"))

        # Overscan: scale image up from center, overscanning edges
        if self._overscan > 0:
            sc = 1.0 + self._overscan
            cx, cy = W / 2.0, H / 2.0
            p.translate(cx, cy)
            p.scale(sc, sc)
            p.translate(-cx, -cy)

        # ── Image composite ────────────────────────────────────────────────────
        if self._playing and self._pixmap:
            # Playback: pre-composited frame pixmap
            _draw_pix(p, self._pixmap, W, H)
        elif self.mode == "wipe" and (self._pixmap_a or self._pixmap_b):
            # GPU wipe: clip each side directly — zero PIL per drag event
            wx = int(self.wipe_x * W / max(1, self.disp_w))
            wx = max(0, min(W, wx))
            if self._pixmap_a and wx > 0:
                p.drawPixmap(QRect(0, 0, wx, H),
                             self._pixmap_a, QRect(0, 0, wx, H))
            if self._pixmap_b and wx < W:
                p.drawPixmap(QRect(wx, 0, W - wx, H),
                             self._pixmap_b, QRect(wx, 0, W - wx, H))
        elif self.mode == "a" and self._pixmap_a:
            _draw_pix(p, self._pixmap_a, W, H)
        elif self.mode == "b" and self._pixmap_b:
            _draw_pix(p, self._pixmap_b, W, H)

        # Reset transform for all overlays (wipe seam, badges, safe area, info)
        p.resetTransform()

        # Scale factors (image coords → widget coords)
        sx = W / max(1, self.disp_w)
        sy = H / max(1, self.disp_h)

        # Cache bar
        cache = self._play_cache or self._cache_a or self._cache_b
        if cache:
            n   = max(1, cache.n_total)
            frac = min(1.0, cache.loaded / n)
            bar_h = 6
            y0   = H - bar_h
            p.fillRect(0, y0, W, bar_h, QColor("#1a0000"))
            fw = max(0, int(W * frac))
            if fw > 0:
                r = int(180 * (1.0 - frac))
                g = int(160 * frac)
                p.fillRect(0, y0, fw, bar_h, QColor(r, g, 0))
            if self._play_frame_num > 0 or self._is_frozen:
                px = int(W * min(1.0, self._play_frame_num / n))
                p.setPen(QPen(QColor("white"), 1))
                p.drawLine(px, y0 - 1, px, H)
            if cache.n_total > 0:
                lbl = (f"{cache.loaded}/{cache.n_total}" if not cache.done
                       else f"checked {cache.loaded}f")
                p.setPen(QColor("#888"))
                fnt = p.font(); fnt.setPointSize(7); p.setFont(fnt)
                p.drawText(W - 80, y0, 76, bar_h,
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, lbl)

        # Wipe line + A/B labels
        if self.mode == "wipe":
            wx = int(self.wipe_x * sx)
            p.setPen(QPen(QColor("white"), 2))
            p.drawLine(wx, 0, wx, H)
            fnt = p.font(); fnt.setPointSize(9); fnt.setBold(True); p.setFont(fnt)
            p.setPen(QColor(C_DIM))
            if wx > 24:
                p.drawText(8, 4, wx - 16, 20,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "A")
            if wx < W - 16:
                p.drawText(wx + 8, 4, W - wx - 16, 20,
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, "B")

        # Channel badge
        if self._channel:
            ch = self._channel.upper()
            col = {"R": QColor("#ff4444"), "G": QColor("#44ff44"), "B": QColor("#4488ff")}[ch]
            p.fillRect(W - 34, 4, 30, 20, QColor("#111"))
            p.setPen(col)
            fnt = p.font(); fnt.setPointSize(9); fnt.setBold(True); p.setFont(fnt)
            p.drawText(W - 34, 4, 30, 20, Qt.AlignmentFlag.AlignCenter, ch)

        # Playback status badge
        if self._playing or self._is_frozen:
            lbl = f"{'▶' if self._playing else '■'} {self._last_play_label}"
            col = QColor("#44cc66") if self._playing else QColor("#cc8844")
            p.fillRect(0, 0, 70, 20, QColor("#111"))
            fnt = p.font(); fnt.setPointSize(8); fnt.setBold(True); p.setFont(fnt)
            p.setPen(col)
            p.drawText(4, 0, 66, 20, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, lbl)

        # Info overlay
        if self._overlay_on and self._overlay_lines:
            fnt = p.font(); fnt.setPointSize(9); fnt.setBold(False); p.setFont(fnt)
            fm = p.fontMetrics()
            pad, lh = 8, 17
            tw = max(fm.horizontalAdvance(l) for l in self._overlay_lines) + pad
            th = len(self._overlay_lines) * lh + pad
            p.fillRect(pad, pad, tw, th, QColor(0x11, 0x11, 0x11, 200))
            p.setPen(QPen(QColor("#444444"), 1))
            p.drawRect(pad, pad, tw, th)
            p.setPen(QColor("white"))
            for i, line in enumerate(self._overlay_lines):
                p.drawText(pad + 5, pad + 4 + i * lh + lh // 2 - fm.height() // 2,
                           tw - 8, fm.height() + 2,
                           Qt.AlignmentFlag.AlignLeft, line)

        # Safe-area guide (drawn last so it sits on top of the wipe seam)
        if self._safe_area > 0:
            sa = self._safe_area
            ix = int(W * (1 - sa) / 2)
            iy = int(H * (1 - sa) / 2)
            p.setPen(QPen(QColor(C_AMBER), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(ix, iy, W - 2*ix, H - 2*iy)

        p.end()

    # ── Mouse events ─────────────────────────────────────────────────────────

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            if self.mode == "wipe":
                self._drag = True
                sx = self.disp_w / max(1, self.width())
                self.set_wipe_x(int(ev.position().x() * sx))
        elif ev.button() == Qt.MouseButton.MiddleButton:
            self._mid_last = ev.position().toPoint()
        elif ev.button() == Qt.MouseButton.RightButton:
            self._zoom  = 1.0
            self._pan_x = 0.0
            self._pan_y = 0.0
            self._render()

    def mouseMoveEvent(self, ev):
        if self._drag and self.mode == "wipe":
            sx = self.disp_w / max(1, self.width())
            self.set_wipe_x(int(ev.position().x() * sx))
        if self._mid_last is not None:
            pos = ev.position().toPoint()
            dx = (pos.x() - self._mid_last.x()) / self._zoom
            dy = (pos.y() - self._mid_last.y()) / self._zoom
            self._pan_x -= dx
            self._pan_y -= dy
            self._mid_last = pos
            self._clamp_pan()
            self._render()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag = False
        elif ev.button() == Qt.MouseButton.MiddleButton:
            self._mid_last = None

    def wheelEvent(self, ev):
        delta = ev.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15
        old_z = self._zoom
        new_z = max(1.0, min(30.0, old_z * factor))
        pos = ev.position()
        # Zoom toward cursor
        sx = self.disp_w / max(1, self.width())
        sy = self.disp_h / max(1, self.height())
        ix = pos.x() * sx
        iy = pos.y() * sy
        self._pan_x += ix / old_z - ix / new_z
        self._pan_y += iy / old_z - iy / new_z
        self._zoom = new_z
        self._clamp_pan()
        self._render()

    # ── Resize handling ───────────────────────────────────────────────────────

    def resizeEvent(self, ev):
        w, h = ev.size().width(), ev.size().height()
        if w < 200 or h < 100:
            return
        if w == self.disp_w and h == self.disp_h:
            return
        self._pending_resize = (w, h)
        self._resize_timer.start(350)

    def _apply_resize(self):
        if self._pending_resize is None:
            return
        w, h = self._pending_resize
        self._pending_resize = None
        self.resize_canvas(w, h)
        # Notify app to reload (emitted via a reference stored by app)
        if hasattr(self, '_on_resize_cb') and self._on_resize_cb:
            self._on_resize_cb(w, h)


# ── Viewer overlay widgets ────────────────────────────────────────────────────

class ViewerOverlayInfo(QWidget):
    """Top-left semi-transparent info box drawn over the wipe view."""
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._shot  = ""
        self._seq   = ""
        self._a_lbl = "—"
        self._b_lbl = "—"
        self._pos   = ""

    def set_info(self, shot: str, seq: str, a_lbl: str, b_lbl: str, pos: str = ""):
        self._shot  = shot
        self._seq   = seq
        self._a_lbl = a_lbl
        self._b_lbl = b_lbl
        self._pos   = pos
        self.update()
        self.adjustSize()

    def paintEvent(self, ev):
        lines = [
            f"{self._shot}  {self._pos}",
            self._seq,
            f"A: {self._a_lbl}",
            f"B: {self._b_lbl}",
        ]
        if not self._shot:
            return
        p = QPainter(self)
        fnt = p.font()
        fnt.setPointSize(8)
        p.setFont(fnt)
        fm  = p.fontMetrics()
        pad = 8
        lh  = fm.height() + 3
        tw  = max(fm.horizontalAdvance(l) for l in lines if l) + pad * 2
        th  = len(lines) * lh + pad
        # dark semi-opaque bg
        p.fillRect(0, 0, tw, th, QColor(6, 8, 12, 200))
        # corner brackets
        br = 6
        pen = QPen(QColor(C_CYAN_DIM), 1)
        p.setPen(pen)
        for (x1, y1, x2, y2) in [
            (0,br,0,0), (0,0,br,0),      # TL
            (tw-br,0,tw,0),(tw,0,tw,br),  # TR
            (0,th-br,0,th),(0,th,br,th),  # BL
            (tw-br,th,tw,th),(tw,th,tw,th-br),  # BR
        ]:
            p.drawLine(x1, y1, x2, y2)
        # text lines
        for i, line in enumerate(lines):
            if not line:
                continue
            if i == 0:
                p.setPen(QColor(C_CYAN))
                f2 = p.font(); f2.setBold(True); p.setFont(f2)
            else:
                p.setPen(QColor(C_DIM))
                f2 = p.font(); f2.setBold(False); p.setFont(f2)
            p.drawText(pad, pad//2 + i * lh,
                       tw - pad, lh,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
        p.end()
        self.setFixedSize(max(180, tw + 4), th + 4)


class ViewerIconStrip(QWidget):
    """Top-right icon strip drawn over the wipe view."""
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(2)
        self._btns: dict[str, "QToolButton"] = {}

    def add_icon(self, key: str, glyph: str, tip: str, callback) -> QToolButton:
        btn = QToolButton(self)
        btn.setText(glyph)
        btn.setToolTip(tip)
        btn.setFixedSize(24, 22)
        btn.clicked.connect(callback)
        self.layout().addWidget(btn)
        self._btns[key] = btn
        return btn

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(6, 8, 12, 200))
        p.end()
        super().paintEvent(ev)


# ── Gradient timeline ─────────────────────────────────────────────────────────

class GradientTimeline(QWidget):
    seek_requested = Signal(int)   # target frame index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(C_BAR))
        self.setPalette(pal)
        self._current = 0
        self._total   = 0

    def set_frame(self, current: int, total: int):
        self._current = current
        self._total   = total
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QColor(C_BAR))

        # gradient bar
        bar_h  = 10
        bar_y  = (H - bar_h) // 2
        bar_x  = 8
        bar_w  = W - 80   # leave room for counter on the right
        if bar_w > 20:
            grad = QLinearGradient(bar_x, 0, bar_x + bar_w, 0)
            grad.setColorAt(0.0, QColor(C_CYAN))
            grad.setColorAt(1.0, QColor(C_PURPLE))
            p.fillRect(bar_x, bar_y, bar_w, bar_h, grad)
            # frame marker
            if self._total > 0:
                fx = bar_x + int(bar_w * self._current / max(1, self._total - 1))
                p.setPen(QPen(QColor("white"), 2))
                p.drawLine(fx, bar_y - 2, fx, bar_y + bar_h + 2)

        # frame counter
        if self._total > 0:
            lbl = f"{self._current} / {self._total}"
            p.setPen(QColor(C_DIM))
            fnt = p.font(); fnt.setPointSize(7); p.setFont(fnt)
            p.drawText(W - 76, 0, 72, H,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, lbl)
        p.end()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self._total > 0:
            bar_x = 8
            bar_w = self.width() - 80
            rel = (ev.position().x() - bar_x) / max(1, bar_w)
            rel = max(0.0, min(1.0, rel))
            self.seek_requested.emit(int(rel * max(0, self._total - 1)))


# ── Icon transport bar ────────────────────────────────────────────────────────

class IconTransport(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(C_BAR))
        self.setPalette(pal)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(3)

        def _icon_btn(glyph: str, tip: str) -> QToolButton:
            b = QToolButton(self)
            b.setText(glyph)
            b.setToolTip(tip)
            b.setFixedSize(28, 28)
            return b

        self.btn_first = _icon_btn("⏮", "First frame")
        self.btn_prev  = _icon_btn("◀", "Prev frame")
        self.btn_play  = _icon_btn("▶", "Play / Pause")
        self.btn_next  = _icon_btn("▶▶", "Next frame")
        for b in (self.btn_first, self.btn_prev, self.btn_play, self.btn_next):
            lay.addWidget(b)

        lay.addSpacing(12)

        # Overscan slider
        os_lbl = QLabel("OVERSCAN", self)
        os_lbl.setStyleSheet(f"color: {C_DIM}; font-size: 7pt; background: transparent;")
        lay.addWidget(os_lbl)
        self._overscan_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._overscan_slider.setRange(0, 100)
        self._overscan_slider.setValue(0)
        self._overscan_slider.setFixedWidth(80)
        self._overscan_slider.setToolTip("Overscan 0–10%")
        lay.addWidget(self._overscan_slider)
        self._overscan_lbl = QLabel("0%", self)
        self._overscan_lbl.setStyleSheet(f"color: {C_DIM}; font-size: 7pt; background: transparent;")
        self._overscan_lbl.setFixedWidth(28)
        lay.addWidget(self._overscan_lbl)

        lay.addSpacing(8)

        # Safe area slider
        sa_lbl = QLabel("SAFE AREA", self)
        sa_lbl.setStyleSheet(f"color: {C_DIM}; font-size: 7pt; background: transparent;")
        lay.addWidget(sa_lbl)
        self._sa_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._sa_slider.setRange(0, 100)
        self._sa_slider.setValue(0)
        self._sa_slider.setFixedWidth(80)
        self._sa_slider.setToolTip("Safe area 50–100% (0 = off)")
        lay.addWidget(self._sa_slider)
        self._sa_lbl = QLabel("off", self)
        self._sa_lbl.setStyleSheet(f"color: {C_DIM}; font-size: 7pt; background: transparent;")
        self._sa_lbl.setFixedWidth(30)
        lay.addWidget(self._sa_lbl)

        lay.addStretch()

        self._overscan_slider.valueChanged.connect(self._on_overscan)
        self._sa_slider.valueChanged.connect(self._on_safe_area)

    _overscan_cb = None
    _safe_area_cb = None

    def _on_overscan(self, v: int):
        pct = v / 1000.0   # 0..0.1
        self._overscan_lbl.setText(f"{v/10:.0f}%")
        if self._overscan_cb:
            self._overscan_cb(pct)

    def _on_safe_area(self, v: int):
        if v == 0:
            frac = 0.0
            self._sa_lbl.setText("off")
        else:
            frac = 0.50 + v / 200.0   # 0..100 → 0.50..1.00
            self._sa_lbl.setText(f"{int(frac*100)}%")
        if self._safe_area_cb:
            self._safe_area_cb(frac)

    def set_playing(self, playing: bool):
        self.btn_play.setText("⏸" if playing else "▶")


# ── Collapsible content section ───────────────────────────────────────────────

class CollapsibleSection(QWidget):
    """Chevron header + collapsible body used in ContentPanel."""
    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header row (clickable)
        self._hdr = QWidget(self)
        self._hdr.setFixedHeight(22)
        self._hdr.setAutoFillBackground(True)
        hp = self._hdr.palette()
        hp.setColor(self._hdr.backgroundRole(), QColor(C_BAR))
        self._hdr.setPalette(hp)
        self._hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        hdr_lay = QHBoxLayout(self._hdr)
        hdr_lay.setContentsMargins(4, 0, 8, 0)
        hdr_lay.setSpacing(4)

        # 3px left accent
        acc = QWidget(self._hdr); acc.setFixedWidth(3); acc.setFixedHeight(22)
        acc.setAutoFillBackground(True)
        ap = acc.palette(); ap.setColor(acc.backgroundRole(), QColor(C_CYAN_DIM))
        acc.setPalette(ap)
        hdr_lay.addWidget(acc)

        self._chevron = QLabel("▾" if expanded else "▸", self._hdr)
        self._chevron.setStyleSheet(f"color: {C_CYAN}; font-size: 8pt; background: transparent;")
        hdr_lay.addWidget(self._chevron)

        title_lbl = QLabel(title.upper(), self._hdr)
        title_lbl.setStyleSheet(f"color: {C_CYAN}; font-size: 7pt; font-weight: bold; background: transparent; letter-spacing: 1px;")
        hdr_lay.addWidget(title_lbl)
        hdr_lay.addStretch()

        outer.addWidget(self._hdr)

        # Body widget (indented)
        self._body = QWidget(self)
        body_outer = QHBoxLayout(self._body)
        body_outer.setContentsMargins(6, 2, 0, 4)
        body_outer.setSpacing(0)
        self._body_lay = QVBoxLayout()
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(3)
        body_outer.addLayout(self._body_lay)
        outer.addWidget(self._body)
        self._expanded = expanded
        self._body.setVisible(expanded)

        self._hdr.mousePressEvent = lambda _: self._toggle()

    def _toggle(self):
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._chevron.setText("▾" if self._expanded else "▸")

    def add_widget(self, w: QWidget):
        self._body_lay.addWidget(w)

    def add_layout(self, lay):
        self._body_lay.addLayout(lay)


# ── ContentPanel ──────────────────────────────────────────────────────────────

class ContentPanel(QWidget):
    layer_a_changed  = Signal(str)   # layer key
    layer_b_changed  = Signal(str)
    ver_a_changed    = Signal(int)
    ver_b_changed    = Signal(int)
    deliver_clicked  = Signal()
    beeble_clicked   = Signal()
    magnific_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(280)
        self.setMaximumWidth(360)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scrollable content
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll, stretch=1)

        container = QWidget()
        scroll.setWidget(container)
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 2, 0, 4)
        lay.setSpacing(0)

        # Layer label → key mapping
        _ll = {k: LAYERS[k]["label"] for k in LAYERS}; _ll["cascade"] = "Cascade"
        self._label_to_key = {v: k for k, v in _ll.items()}
        all_labels = [LAYERS[k]["label"] for k in LAYERS] + ["Cascade"]

        def _dim_lbl(text: str, w=None) -> QLabel:
            l = QLabel(text)
            l.setStyleSheet(f"color: {C_DIM}; font-size: 8pt; background: transparent;")
            l.setWordWrap(True)
            if w:
                l.setFixedWidth(w)
            return l

        def _micro_lbl(text: str, color: str = C_CYAN) -> QLabel:
            l = QLabel(text)
            l.setStyleSheet(f"color: {color}; font-size: 7pt; font-weight: bold; background: transparent; letter-spacing: 1px;")
            return l

        # ── SHOT INFO ─────────────────────────────────────────────────────
        si = CollapsibleSection("SHOT INFO")
        self._seq_lbl    = _dim_lbl("")
        self._seq_lbl.setStyleSheet(f"color: {C_FG}; font-size: 9pt; font-weight: bold; background: transparent;")
        self._range_lbl  = _dim_lbl("")
        self._status_lbl_info = _dim_lbl("")
        si.add_widget(self._seq_lbl)
        si.add_widget(self._range_lbl)
        si.add_widget(self._status_lbl_info)
        lay.addWidget(si)

        # ── ELEMENT SELECTION ─────────────────────────────────────────────
        es = CollapsibleSection("ELEMENT SELECTION")
        # Layer A
        es.add_widget(_micro_lbl("LAYER A  —", C_CYAN))
        self._layer_a_cb = QComboBox()
        self._layer_a_cb.addItems(all_labels)
        self._layer_a_cb.setCurrentText("Plate HD")
        self._layer_a_cb.currentTextChanged.connect(
            lambda t: self.layer_a_changed.emit(self._label_to_key.get(t, "plate_hd")))
        es.add_widget(self._layer_a_cb)
        ver_a_row = QWidget(); vrl_a = QHBoxLayout(ver_a_row)
        vrl_a.setContentsMargins(0, 0, 0, 0); vrl_a.setSpacing(4)
        vrl_a.addWidget(_micro_lbl("VER", C_CYAN))
        self._ver_a_cb = QComboBox()
        self._ver_a_cb.currentIndexChanged.connect(lambda i: self.ver_a_changed.emit(max(0, i)))
        vrl_a.addWidget(self._ver_a_cb, stretch=1)
        es.add_widget(ver_a_row)
        # spacer
        sp = QWidget(); sp.setFixedHeight(6)
        es.add_widget(sp)
        # Layer B
        es.add_widget(_micro_lbl("LAYER B  —", C_AMBER))
        self._layer_b_cb = QComboBox()
        self._layer_b_cb.addItems(all_labels)
        self._layer_b_cb.setCurrentText("WAI")
        self._layer_b_cb.currentTextChanged.connect(
            lambda t: self.layer_b_changed.emit(self._label_to_key.get(t, "wai")))
        es.add_widget(self._layer_b_cb)
        ver_b_row = QWidget(); vrl_b = QHBoxLayout(ver_b_row)
        vrl_b.setContentsMargins(0, 0, 0, 0); vrl_b.setSpacing(4)
        vrl_b.addWidget(_micro_lbl("VER", C_AMBER))
        self._ver_b_cb = QComboBox()
        self._ver_b_cb.currentIndexChanged.connect(lambda i: self.ver_b_changed.emit(max(0, i)))
        vrl_b.addWidget(self._ver_b_cb, stretch=1)
        es.add_widget(ver_b_row)
        lay.addWidget(es)

        # ── GRADE ─────────────────────────────────────────────────────────
        gr = CollapsibleSection("GRADE")
        grade_row = QWidget(); grade_lay = QHBoxLayout(grade_row)
        grade_lay.setContentsMargins(0, 0, 0, 0); grade_lay.setSpacing(6)
        self._grade_cs_cb = QComboBox()
        self._grade_cs_cb.addItem("Rec.709 (VFX)")
        self._grade_cs_cb.setToolTip("Colorspace (stub)")
        grade_lay.addWidget(self._grade_cs_cb, stretch=1)
        gear_btn = QToolButton(); gear_btn.setText("⚙"); gear_btn.setFixedSize(24, 22)
        gear_btn.setToolTip("Grade settings")
        grade_lay.addWidget(gear_btn)
        gr.add_widget(grade_row)
        self._grade_lbl = _dim_lbl("")
        self._grade_lbl.setWordWrap(True)
        gr.add_widget(self._grade_lbl)
        lay.addWidget(gr)

        # ── NOTES ─────────────────────────────────────────────────────────
        nt = CollapsibleSection("NOTES")
        self._fb_shot_lbl = QLabel("")
        self._fb_shot_lbl.setStyleSheet(f"color: {C_CYAN}; font-size: 8pt; font-weight: bold; background: transparent;")
        nt.add_widget(self._fb_shot_lbl)
        self._fb_text = QPlainTextEdit()
        self._fb_text.setReadOnly(True)
        self._fb_text.setMinimumHeight(70)
        self._fb_text.setMaximumHeight(140)
        self._fb_text.setPlaceholderText("Add note or feedback…")
        nt.add_widget(self._fb_text)
        lay.addWidget(nt)

        # ── ACTIONS ───────────────────────────────────────────────────────
        ac = CollapsibleSection("ACTIONS")
        act_row = QWidget(); act_lay = QHBoxLayout(act_row)
        act_lay.setContentsMargins(0, 0, 0, 0); act_lay.setSpacing(8)
        def _act_btn(glyph: str, tip: str) -> QToolButton:
            b = QToolButton(); b.setText(glyph); b.setFixedSize(32, 30); b.setToolTip(tip)
            return b
        self._btn_deliver  = _act_btn("✈", "Deliver…")
        self._btn_beeble   = _act_btn("☁", "Submit Beeble")
        self._btn_magnific = _act_btn("✦", "Submit Magnific")
        self._btn_deliver.clicked.connect(self.deliver_clicked)
        self._btn_beeble.clicked.connect(self.beeble_clicked)
        self._btn_magnific.clicked.connect(self.magnific_clicked)
        for b in (self._btn_deliver, self._btn_beeble, self._btn_magnific):
            act_lay.addWidget(b)
        act_lay.addStretch()
        ac.add_widget(act_row)
        lay.addWidget(ac)

        lay.addStretch()

    # ── Update helpers ────────────────────────────────────────────────────

    def update_shot_info(self, seq: str, range_text: str, shotcode: str):
        self._seq_lbl.setText(seq)
        self._range_lbl.setText(range_text)
        self._fb_shot_lbl.setText(shotcode)

    def update_feedback(self, text: str):
        self._fb_text.setPlainText(text)
        self._fb_text.verticalScrollBar().setValue(0)

    def update_grade(self, exposure: dict, gamma: dict, saturation: dict):
        parts = []
        for side in ("a", "b"):
            e, g, s = exposure[side], gamma[side], saturation[side]
            bits = []
            if e != 0.0:  bits.append(f"exp{e:+.3f}")
            if g != 1.0:  bits.append(f"g{g:.3f}")
            if s != 1.0:  bits.append(f"sat{s:.3f}")
            if bits:
                parts.append(f"{'A' if side=='a' else 'B'}: {' '.join(bits)}")
        self._grade_lbl.setText("   ".join(parts) if parts else "")

    def update_versions(self, side: str, labels: list[str]):
        cb = self._ver_a_cb if side == "a" else self._ver_b_cb
        cb.blockSignals(True)
        cb.clear()
        cb.addItems(labels if labels else ["(no versions)"])
        cb.setCurrentIndex(0)
        cb.blockSignals(False)

    def set_layer(self, side: str, key: str):
        label = LAYERS.get(key, {}).get("label", key)
        if key == "cascade":
            label = "Cascade"
        cb = self._layer_a_cb if side == "a" else self._layer_b_cb
        cb.blockSignals(True)
        idx = cb.findText(label)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        cb.blockSignals(False)

    def layer_a_key(self) -> str:
        t = self._layer_a_cb.currentText()
        return self._label_to_key.get(t, "plate_hd")

    def layer_b_key(self) -> str:
        t = self._layer_b_cb.currentText()
        return self._label_to_key.get(t, "wai")

    def ver_a_idx(self) -> int:
        return max(0, self._ver_a_cb.currentIndex())

    def ver_b_idx(self) -> int:
        return max(0, self._ver_b_cb.currentIndex())


# ── ShotViewerApp (main window) ───────────────────────────────────────────────

class ShotViewerApp(QMainWindow):
    def __init__(self, initial_shot: str = "", initial_sequence: str = ""):
        super().__init__()
        self.setWindowTitle("|  S E E D  |")
        self.resize(1680, 960)

        self.db        = load_db()
        self.delivered = load_delivered_shots()
        self._seq_shots: list[str] = []
        self._seq_idx   = -1
        self._load_id   = 0

        self._layer_a_key = "plate_hd"
        self._layer_b_key = "wai"
        self._vers_a: list[Path] = []
        self._vers_b: list[Path] = []
        self._ver_a_idx = 0
        self._ver_b_idx = 0
        self._frame_pos = "first"
        self._custom_frame = 0
        self._current_shot = ""

        self._exposure   = {"a": 0.0, "b": 0.0}
        self._gamma      = {"a": 1.0, "b": 1.0}
        self._saturation = {"a": 1.0, "b": 1.0}
        self._held_keys: set[str] = set()
        self._grade_drag_x: Optional[int]    = None
        self._grade_drag_sides: Optional[tuple] = None

        self._raw_a = _placeholder(1280, 720, "A")
        self._raw_b = _placeholder(1280, 720, "B")

        self._build_ui()
        self._build_menubar()
        self._bind_shortcuts()

        if initial_shot:
            QTimer.singleShot(200, lambda: self._load_shot(initial_shot))
        elif initial_sequence:
            QTimer.singleShot(200, lambda: self._set_sequence(initial_sequence))

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        main_lay = QVBoxLayout(central)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # ── Brand header strip (below menu bar) ───────────────────────────────
        brand_strip = QWidget(central)
        brand_strip.setFixedHeight(22)
        brand_strip.setAutoFillBackground(True)
        bs_pal = brand_strip.palette()
        bs_pal.setColor(brand_strip.backgroundRole(), QColor(C_BAR2))
        brand_strip.setPalette(bs_pal)
        bs_lay = QHBoxLayout(brand_strip)
        bs_lay.setContentsMargins(0, 0, 10, 0)
        bs_lay.setSpacing(0)

        bs_accent = QWidget(brand_strip); bs_accent.setFixedSize(3, 22)
        bs_accent.setAutoFillBackground(True)
        bsa_pal = bs_accent.palette()
        bsa_pal.setColor(bs_accent.backgroundRole(), QColor(C_CYAN))
        bs_accent.setPalette(bsa_pal)
        bs_lay.addWidget(bs_accent)

        brand_lbl = QLabel("  SGI TRON VFX  //  PIPELINE  ", brand_strip)
        brand_lbl.setStyleSheet(
            f"color: {C_CYAN_DIM}; font-size: 7pt; font-weight: bold; "
            f"letter-spacing: 2px; background: transparent;")
        bs_lay.addWidget(brand_lbl)
        bs_lay.addStretch()
        proj_lbl = QLabel("PROJECT: SEED_FILM", brand_strip)
        proj_lbl.setStyleSheet(f"color: {C_DIM}; font-size: 7pt; background: transparent;")
        bs_lay.addWidget(proj_lbl)
        self._brand_strip = brand_strip
        main_lay.addWidget(brand_strip)

        # ── Shot header strip ─────────────────────────────────────────────────
        shot_strip = QWidget(central)
        shot_strip.setFixedHeight(40)
        shot_strip.setAutoFillBackground(True)
        ss_pal = shot_strip.palette()
        ss_pal.setColor(shot_strip.backgroundRole(), QColor(C_BAR))
        shot_strip.setPalette(ss_pal)
        ss_lay = QHBoxLayout(shot_strip)
        ss_lay.setContentsMargins(10, 0, 10, 0)
        ss_lay.setSpacing(8)

        viewer_tag = QLabel("▸  VIEWER", shot_strip)
        viewer_tag.setStyleSheet(f"color: {C_CYAN}; font-size: 7pt; font-weight: bold; background: transparent;")
        ss_lay.addWidget(viewer_tag)

        ss_lay.addStretch()

        self._shot_lbl = QLabel("—", shot_strip)
        self._shot_lbl.setStyleSheet(
            f"color: {C_CYAN}; font-size: 16pt; font-weight: bold; "
            f"letter-spacing: 4px; background: transparent;")
        self._shot_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ss_lay.addWidget(self._shot_lbl)

        ss_lay.addSpacing(16)

        # A · WIPE · B pill
        self._pill_labels: dict[str, QLabel] = {}
        pill_row = QWidget(shot_strip)
        pill_lay = QHBoxLayout(pill_row)
        pill_lay.setContentsMargins(0, 0, 0, 0)
        pill_lay.setSpacing(3)
        for mode_key, mode_text in [("a", "A"), ("wipe", "WIPE"), ("b", "B")]:
            if mode_text != "A":
                sep = QLabel("·", pill_row)
                sep.setStyleSheet(f"color: {C_DIM}; font-size: 8pt; background: transparent;")
                pill_lay.addWidget(sep)
            lbl = QLabel(mode_text, pill_row)
            style_active  = f"color: {C_CYAN}; font-size: 8pt; font-weight: bold; background: transparent;"
            style_inactive = f"color: {C_DIM}; font-size: 8pt; background: transparent;"
            lbl.setStyleSheet(style_active if mode_key == "wipe" else style_inactive)
            pill_lay.addWidget(lbl)
            self._pill_labels[mode_key] = lbl
        ss_lay.addWidget(pill_row)

        ss_lay.addStretch()

        # Nav arrows  < N / M >
        nav_prev = QToolButton(shot_strip); nav_prev.setText("<"); nav_prev.setFixedSize(22, 22)
        nav_prev.clicked.connect(self._prev_shot)
        ss_lay.addWidget(nav_prev)
        self._seq_pos_lbl = QLabel("—", shot_strip)
        self._seq_pos_lbl.setStyleSheet(f"color: {C_DIM}; font-size: 8pt; background: transparent;")
        ss_lay.addWidget(self._seq_pos_lbl)
        nav_next = QToolButton(shot_strip); nav_next.setText(">"); nav_next.setFixedSize(22, 22)
        nav_next.clicked.connect(self._next_shot)
        ss_lay.addWidget(nav_next)

        self._shot_strip = shot_strip
        main_lay.addWidget(shot_strip)

        # ── Main row: browser | viewer_pane | content ─────────────────────────
        self._splitter = QSplitter(Qt.Orientation.Horizontal, central)
        self._splitter.setHandleWidth(1)
        main_lay.addWidget(self._splitter, stretch=1)

        # Shot browser
        self._browser = ShotBrowser(self.db, self.delivered, self._splitter)
        self._browser.shot_selected.connect(self._load_shot)
        self._browser.seq_changed.connect(self._on_seq_change_from_browser)
        self._splitter.addWidget(self._browser)

        # Viewer pane (WipeView + overlays + timeline + transport)
        viewer_pane = QWidget(self._splitter)
        viewer_pane.setAutoFillBackground(True)
        vp_pal = viewer_pane.palette()
        vp_pal.setColor(viewer_pane.backgroundRole(), QColor(C_DARK))
        viewer_pane.setPalette(vp_pal)
        vp_lay = QVBoxLayout(viewer_pane)
        vp_lay.setContentsMargins(0, 0, 0, 0)
        vp_lay.setSpacing(0)

        # WipeView (1px cyan border wrapper)
        wipe_border = QWidget(viewer_pane)
        wipe_border.setAutoFillBackground(True)
        wb_pal = wipe_border.palette()
        wb_pal.setColor(wipe_border.backgroundRole(), QColor(C_CYAN_DIM))
        wipe_border.setPalette(wb_pal)
        wb_lay = QVBoxLayout(wipe_border)
        wb_lay.setContentsMargins(1, 1, 1, 1)
        wb_lay.setSpacing(0)

        self.wipe = WipeView(wipe_border)
        self.wipe._on_resize_cb = self._on_viewer_resize
        self.wipe.setMouseTracking(True)
        self.wipe.mouseMoveEvent = self._wipe_mouse_move_proxy(self.wipe.mouseMoveEvent)
        wb_lay.addWidget(self.wipe)
        vp_lay.addWidget(wipe_border, stretch=1)

        # Viewer overlays (positioned inside WipeView as children)
        self._viewer_info = ViewerOverlayInfo(self.wipe)
        self._viewer_info.setFixedSize(200, 76)
        self._viewer_info.move(12, 12)
        self._viewer_info.show()

        self._viewer_icons = ViewerIconStrip(self.wipe)
        self._viewer_icons.add_icon("zoom",    "□",  "Reset zoom",        lambda: (setattr(self.wipe, "_zoom", 1.0), setattr(self.wipe, "_pan_x", 0.0), setattr(self.wipe, "_pan_y", 0.0), self.wipe._render()))
        self._viewer_icons.add_icon("mask",    "M",  "Mask (n/a)",        lambda: None)
        self._viewer_icons.add_icon("overlay", "▣",  "Toggle overlay [O]", self._toggle_overlay)
        self._viewer_icons.add_icon("channel", "▤",  "Cycle channel",     self._cycle_channel)
        self._viewer_icons.add_icon("swap",    "⇄",  "Swap A/B [S]",      self._swap)
        self._viewer_icons.add_icon("full",    "▼",  "Fullscreen [F12]",  self._toggle_fullscreen)
        self._viewer_icons.adjustSize()
        self._viewer_icons.move(max(0, self.wipe.width() - self._viewer_icons.width() - 12), 12)
        self._viewer_icons.show()

        def _reposition_overlays(w: int, h: int):
            self._viewer_info.move(12, 12)
            self._viewer_icons.adjustSize()
            self._viewer_icons.move(max(0, w - self._viewer_icons.width() - 12), 12)
        self.wipe._on_resize_cb = lambda w, h: (self._on_viewer_resize(w, h), _reposition_overlays(w, h))

        # Gradient timeline
        self._timeline = GradientTimeline(viewer_pane)
        self._timeline.seek_requested.connect(self.wipe.seek_frame)
        self.wipe.frame_changed.connect(self._timeline.set_frame)
        vp_lay.addWidget(self._timeline)

        # Icon transport row
        self._transport = IconTransport(viewer_pane)
        self._transport.btn_first.clicked.connect(lambda: self.wipe.seek_frame(0))
        self._transport.btn_prev.clicked.connect(lambda: self.wipe.step_frame(-1))
        self._transport.btn_next.clicked.connect(lambda: self.wipe.step_frame(+1))
        self._transport.btn_play.clicked.connect(self._toggle_play)
        self._transport._overscan_cb  = self.wipe.set_overscan
        self._transport._safe_area_cb = self.wipe.set_safe_area
        vp_lay.addWidget(self._transport)

        self._splitter.addWidget(viewer_pane)

        # Content panel
        self._content = ContentPanel(self._splitter)
        self._content.layer_a_changed.connect(self._on_layer_a_change)
        self._content.layer_b_changed.connect(self._on_layer_b_change)
        self._content.ver_a_changed.connect(lambda i: self._on_ver_change("a", i))
        self._content.ver_b_changed.connect(lambda i: self._on_ver_change("b", i))
        self._content.deliver_clicked.connect(self._open_deliver_dialog)
        self._content.beeble_clicked.connect(self._beeble_current)
        self._content.magnific_clicked.connect(self._magnific_current)
        self._splitter.addWidget(self._content)

        # Splitter proportions: browser fixed, viewer expands, content fixed
        self._splitter.setSizes([THUMB_W * 2 + 50, 9999, 300])
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 0)

        # ── Invisible legacy labels (keep refs alive for _update_status) ──────
        self._mode_lbl = QLabel("", self)   # hidden — pill updates instead
        self._mode_lbl.hide()
        self._zoom_lbl = QLabel("", self)
        self._zoom_lbl.hide()

        # ── Status bar ────────────────────────────────────────────────────────
        sb = QStatusBar(self)
        self.setStatusBar(sb)
        self._status_lbl = QLabel("No shot loaded", self)
        self._status_lbl.setStyleSheet(f"color: {C_DIM}; padding: 0 8px;")
        sb.addWidget(self._status_lbl, stretch=1)
        self._canvas_size_lbl = QLabel("", self)
        self._canvas_size_lbl.setStyleSheet(f"color: {C_DIM2}; padding: 0 8px;")
        sb.addPermanentWidget(self._canvas_size_lbl)

    def _wipe_mouse_move_proxy(self, original_fn):
        """Wrap WipeView mouseMoveEvent to also fire grade-drag logic."""
        def _proxy(ev):
            original_fn(ev)
            self._on_grade_motion(ev)
        return _proxy

    def _build_menubar(self):
        mb = self.menuBar()

        fm = mb.addMenu("FILE")
        fm.addAction("Jump to Shot…",       self._jump_to_shot,      "Ctrl+J")
        fm.addAction("Reveal in Explorer",  self._reveal_in_explorer,"Ctrl+E")
        fm.addSeparator()
        fm.addAction("Quit",                self.close,               "Ctrl+Q")

        em = mb.addMenu("EDIT")
        em.addAction("Copy shotcode",   lambda: self._copy(self._current_shot))
        em.addAction("Copy path A",     lambda: self._copy(str(self.wipe.path_a or "")))
        em.addAction("Copy path B",     lambda: self._copy(str(self.wipe.path_b or "")))

        vm = mb.addMenu("VIEW")
        vm.addAction("Toggle Overlay  [O]",   self._toggle_overlay)
        vm.addAction("Reset Zoom  [R-click]", lambda: (
            setattr(self.wipe, "_zoom", 1.0),
            setattr(self.wipe, "_pan_x", 0.0),
            setattr(self.wipe, "_pan_y", 0.0),
            self.wipe._render()))
        vm.addSeparator()
        vm.addAction("Red channel  [R]",   lambda: self._toggle_channel("r"))
        vm.addAction("Green channel  [G]", lambda: self._toggle_channel("g"))
        vm.addAction("Blue channel  [B]",  lambda: self._toggle_channel("b"))
        vm.addSeparator()
        vm.addAction("Wipe mode  [W]",  lambda: self._set_mode("wipe"))
        vm.addAction("A only",          lambda: self._set_mode("a"))
        vm.addAction("B only",          lambda: self._set_mode("b"))

        sm = mb.addMenu("SHOTS")
        sm.addAction("Previous  [↑]",  self._prev_shot)
        sm.addAction("Next  [↓]",      self._next_shot)
        sm.addAction("Jump to Shot…",  self._jump_to_shot)

        rm = mb.addMenu("RENDER")
        rm.addAction("Submit Beeble",        self._beeble_current)
        rm.addAction("Submit Magnific",      self._magnific_current)
        rm.addSeparator()
        rm.addAction("Deliver…",             self._open_deliver_dialog)
        rm.addSeparator()
        rm.addAction("Poll Beeble+Magnific", self._poll_all)

        hm = mb.addMenu("HELP")
        hm.addAction("Keyboard shortcuts…", self._show_shortcuts)


    def _bind_shortcuts(self):
        def sc(key, fn):
            from PySide6.QtGui import QShortcut
            s = QShortcut(QKeySequence(key), self)
            s.activated.connect(fn)
            return s
        sc("W",          lambda: self._toggle_wipe())
        sc("S",          self._swap)
        sc("R",          lambda: self._toggle_channel("r"))
        sc("G",          lambda: self._toggle_channel("g"))
        sc("B",          lambda: self._toggle_channel("b"))
        sc("O",          self._toggle_overlay)
        sc("Backspace",  self._reset_grade)
        sc("Shift+R",    self._refresh_shot)
        sc("F",          self._toggle_frame_pos)
        sc("Left",       lambda: self.wipe.step_frame(-1))
        sc("Right",      lambda: self.wipe.step_frame(+1))
        sc("Up",         self._prev_shot)
        sc("Down",       self._next_shot)
        sc("F12",        self._toggle_fullscreen)
        sc("\\",         self._toggle_cinema)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _toggle_cinema(self):
        _CINEMA_PANELS = [
            self._brand_strip, self._shot_strip,
            self._browser, self._content,
            self._timeline, self._transport,
            self._viewer_info, self._viewer_icons,
        ]
        going_cinema = self._brand_strip.isVisible()
        for w in _CINEMA_PANELS:
            w.setVisible(not going_cinema)
        self.menuBar().setVisible(not going_cinema)
        self.statusBar().setVisible(not going_cinema)
        if going_cinema:
            self.showFullScreen()
        else:
            self.showNormal()

    # ── Space playback (called by _SpaceFilter — works regardless of focus) ──────

    def _on_space_press(self):
        is_double = (not self.wipe._playing and
                     (time.time() - self.wipe._last_space_release) < 0.35)
        if is_double:
            self.wipe.start_play(double_tap=True)
        else:
            self.wipe.start_play()

    def _on_space_release(self):
        self.wipe.stop_play()
        self._transport.set_playing(False)

    # ── Key events (grade keys only — Space handled by _SpaceFilter) ──────────

    def keyPressEvent(self, ev):
        key = ev.text().lower()
        if key:
            self._held_keys.add(key)
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        key = ev.text().lower()
        if key:
            self._held_keys.discard(key)
            if key in ("e", "c", "v"):
                self._grade_drag_x     = None
                self._grade_drag_sides = None
        super().keyReleaseEvent(ev)

    # ── Grade drag ────────────────────────────────────────────────────────────

    def _grade_sides_at(self, widget_x_screen: int) -> tuple:
        mode = self.wipe.mode
        if mode == "a":
            return ("a",)
        if mode == "b":
            return ("b",)
        wipe_screen = self.wipe.mapToGlobal(
            QPoint(int(self.wipe.wipe_x * self.wipe.width() / max(1, self.wipe.disp_w)), 0)).x()
        if abs(widget_x_screen - wipe_screen) < 40:
            return ("a","b")
        return ("a",) if widget_x_screen < wipe_screen else ("b",)

    def _on_grade_motion(self, ev):
        grade_key = None
        keys = self._held_keys
        if "e" in keys:   grade_key = "e"
        elif "c" in keys: grade_key = "c"
        elif "v" in keys: grade_key = "v"
        if grade_key is None:
            self._grade_drag_x = self._grade_drag_sides = None
            return
        x_screen = self.wipe.mapToGlobal(ev.position().toPoint()).x()
        if self._grade_drag_x is None:
            self._grade_drag_sides = self._grade_sides_at(x_screen)
            self._grade_drag_x = x_screen
            return
        dx = x_screen - self._grade_drag_x
        self._grade_drag_x = x_screen
        if dx == 0:
            return
        v = math.copysign(abs(dx) ** 1.5, dx)
        sides = self._grade_drag_sides or ("a","b")
        if grade_key == "e":
            delta = v * 0.00025
            for s in sides:
                self._exposure[s] = round(max(-4.0, min(4.0, self._exposure[s]+delta)), 4)
        elif grade_key == "c":
            delta = v * 0.000125
            for s in sides:
                self._gamma[s] = round(max(0.1, min(4.0, self._gamma[s]+delta)), 4)
        elif grade_key == "v":
            delta = v * 0.000125
            for s in sides:
                self._saturation[s] = round(max(0.0, min(4.0, self._saturation[s]+delta)), 4)
        self._grade_changed(sides)

    # ── Toggle / mode helpers ─────────────────────────────────────────────────

    def _toggle_wipe(self):
        new = "a" if self.wipe.mode == "wipe" else "wipe"
        self._set_mode(new)

    def _set_mode(self, mode: str):
        self.wipe.set_mode(mode)
        self._mode_lbl.setText(mode.upper())
        style_active   = f"color: {C_CYAN}; font-size: 8pt; font-weight: bold; background: transparent;"
        style_inactive = f"color: {C_DIM}; font-size: 8pt; background: transparent;"
        for m, lbl in self._pill_labels.items():
            lbl.setStyleSheet(style_active if m == mode else style_inactive)

    def _toggle_play(self):
        if self.wipe._playing:
            self.wipe.stop_play()
            self._transport.set_playing(False)
        else:
            self._on_space_press()
            self._transport.set_playing(True)

    def _cycle_channel(self):
        current = self.wipe._channel
        order = [None, "r", "g", "b"]
        next_ch = order[(order.index(current) + 1) % len(order)]
        self.wipe._channel = next_ch
        self.wipe._render()

    def _toggle_channel(self, ch: str):
        self.wipe._channel = None if self.wipe._channel == ch else ch
        self.wipe._render()

    def _toggle_overlay(self):
        self.wipe._overlay_on = not self.wipe._overlay_on
        self.wipe._render()

    def _toggle_frame_pos(self):
        pos = self._frame_pos
        new = "last" if pos == "first" else "first"
        self._frame_pos = new
        self._reload()

    def _on_mode_change(self, mode: str):
        self.wipe.set_mode(mode)
        self._mode_lbl.setText(mode.upper())

    def _on_frame_pos_change(self, pos: str, custom_frame: int):
        self._frame_pos    = pos
        self._custom_frame = custom_frame
        self._reload()

    def _on_viewer_resize(self, w: int, h: int):
        self._canvas_size_lbl.setText(f"{w}x{h}")
        if self._current_shot:
            self._reload_async()   # async — never blocks UI on resize
        else:
            self.wipe.resize_canvas(w, h)
            ph = _placeholder(w, h, "")
            self._raw_a = ph; self._raw_b = ph
            self.wipe.set_a(ph); self.wipe.set_b(ph)

    # ── Layer / version handling ───────────────────────────────────────────────

    def _on_layer_a_change(self, key: str):
        self._layer_a_key = key
        self._refresh_versions("a")
        self._update_overlay()
        self._load_a()

    def _on_layer_b_change(self, key: str):
        self._layer_b_key = key
        self._refresh_versions("b")
        self._update_overlay()
        self._load_b()

    def _on_ver_change(self, side: str, idx: int):
        if side == "a":
            self._ver_a_idx = idx
            self._load_a()
        else:
            self._ver_b_idx = idx
            self._load_b()

    def _refresh_versions(self, side: str):
        sc = self._current_shot
        sf = find_shot_folder(sc) if sc else None
        layer = self._layer_a_key if side == "a" else self._layer_b_key
        vers = find_all_versions(sf, layer) if sf and layer != "cascade" else []
        labels = [_ver_label(p, i) for i, p in enumerate(vers)] or ["(no versions)"]
        if side == "a":
            self._vers_a = vers
            self._ver_a_idx = 0
        else:
            self._vers_b = vers
            self._ver_b_idx = 0
        self._content.update_versions(side, labels)

    # ── Image fetching ────────────────────────────────────────────────────────

    def _fetch(self, layer: str, side: str, ver_idx: int = 0) -> tuple:
        sc = self._current_shot
        if not sc:
            return _placeholder(self.wipe.disp_w, self.wipe.disp_h, side), None
        sf = find_shot_folder(sc)
        if not sf:
            self._set_status(f"Shot folder not found: {sc}")
            return _placeholder(self.wipe.disp_w, self.wipe.disp_h, f"{side}: not found"), None
        if layer == "cascade":
            src, used = find_cascade(sf)
        else:
            src  = find_source(sf, layer, ver_idx)
            used = layer if src else ""
        if not src:
            return _placeholder(self.wipe.disp_w, self.wipe.disp_h, f"{side}: [{layer}] none"), None
        last = (self._frame_pos == "last")
        cf   = self._custom_frame if self._frame_pos == "custom" else None
        img  = load_display_image(src, used, self.wipe.disp_w, self.wipe.disp_h, last=last, custom_frame=cf)
        play_src = src if src.suffix.lower() == ".mp4" else None
        return img, play_src

    # ── Grade ─────────────────────────────────────────────────────────────────

    def _apply_grade(self, img: Image.Image, side: str) -> Image.Image:
        e, g, s = self._exposure[side], self._gamma[side], self._saturation[side]
        if e == 0.0 and g == 1.0 and s == 1.0:
            return img
        if e != 0.0:
            img = ImageEnhance.Brightness(img).enhance(2.0 ** e)
        if g != 1.0:
            img = img.point(lambda x: int(min(255, max(0, 255 * (x/255) ** (1.0/g)))))
        if s != 1.0:
            img = ImageEnhance.Color(img).enhance(s)
        return img

    def _grade_changed(self, sides=("a","b")):
        # Update static images directly — bypass set_a/set_b which reset _play_frame_num.
        if "a" in sides:
            self.wipe.img_a = self._apply_grade(self._raw_a, "a")
        if "b" in sides:
            self.wipe.img_b = self._apply_grade(self._raw_b, "b")
        self.wipe._render()
        self._content.update_grade(self._exposure, self._gamma, self._saturation)

    def _reset_grade(self):
        self._exposure   = {"a":0.0,"b":0.0}
        self._gamma      = {"a":1.0,"b":1.0}
        self._saturation = {"a":1.0,"b":1.0}
        self._grade_drag_x = self._grade_drag_sides = None
        self._grade_changed()

    # ── Image loading ─────────────────────────────────────────────────────────

    # _load_a / _load_b / _reload are all routed through _reload_async so
    # ffmpeg + PIL never block the main thread.
    def _load_a(self):  self._reload_async(sides=("a",))
    def _load_b(self):  self._reload_async(sides=("b",))
    def _reload(self):  self._reload_async()

    def _reload_async(self, sides=("a", "b")):
        w = max(10, self.wipe.width())
        h = max(10, self.wipe.height())
        if w != self.wipe.disp_w or h != self.wipe.disp_h:
            self.wipe.resize_canvas(w, h)

        # Preserve frame position across layer switches / reloads.
        saved_frame = self.wipe._play_frame_num

        # Show loading placeholders immediately for the sides being loaded.
        ph = _placeholder(w, h, "")
        if "a" in sides:
            self.wipe.set_a(ph)
        if "b" in sides:
            self.wipe.set_b(ph)

        # set_a/set_b reset _play_frame_num — restore it so the transport bar
        # marker and step-frame operations stay at the correct position.
        self.wipe._play_frame_num = saved_frame

        self._load_id += 1
        load_id = self._load_id
        sc       = self._current_shot
        la, lb   = self._layer_a_key, self._layer_b_key
        vai, vbi = self._ver_a_idx, self._ver_b_idx
        last     = (self._frame_pos == "last")
        cf       = self._custom_frame if self._frame_pos == "custom" else None
        # Snapshot current raw images for the side NOT being reloaded.
        prev_raw_a, prev_path_a = self._raw_a, self.wipe.path_a
        prev_raw_b, prev_path_b = self._raw_b, self.wipe.path_b

        def _fetch_one(layer, side, ver_idx):
            sf = find_shot_folder(sc)
            if not sf:
                return _placeholder(w, h, f"{side}: not found"), None
            if layer == "cascade":
                src, used = find_cascade(sf)
            else:
                src  = find_source(sf, layer, ver_idx)
                used = layer if src else ""
            if not src:
                return _placeholder(w, h, f"{side}: [{layer}] none"), None
            img = load_display_image(src, used, w, h, last=last, custom_frame=cf)
            return img, (src if src.suffix.lower() == ".mp4" else None)

        def _bg():
            raw_a = path_a = raw_b = path_b = None
            if "a" in sides:
                raw_a, path_a = _fetch_one(la, "A", vai)
            if "b" in sides:
                raw_b, path_b = _fetch_one(lb, "B", vbi)

            def _commit():
                if self._load_id != load_id:
                    return
                if "a" in sides:
                    self._raw_a = raw_a
                    self.wipe.path_a = path_a
                    self.wipe.load_source("a", path_a)
                    self.wipe.set_a(self._apply_grade(raw_a, "a"))
                if "b" in sides:
                    self._raw_b = raw_b
                    self.wipe.path_b = path_b
                    self.wipe.load_source("b", path_b)
                    self.wipe.set_b(self._apply_grade(raw_b, "b"))
                # Restore frame position — set_a/set_b reset it to 0.
                self.wipe._play_frame_num = saved_frame
                self._update_status()
                self._update_overlay()

            post_to_main(_commit)

        threading.Thread(target=_bg, daemon=True).start()

    def _prefetch_layers(self, shotcode: str):
        """Extract first frames for all layers in background so layer switches are instant."""
        def _bg():
            sf = find_shot_folder(shotcode) if shotcode else None
            if not sf:
                return
            for layer in LAYERS:
                try:
                    src = find_source(sf, layer)
                    if src and src.exists():
                        get_first_frame(src, layer)
                except Exception:
                    pass
        threading.Thread(target=_bg, daemon=True).start()

    def _prefetch_adjacent(self):
        """Pre-warm first frames for the shot before and after the current one."""
        shots = self._seq_shots
        idx   = self._seq_idx
        for delta in (-1, +1):
            ni = idx + delta
            if 0 <= ni < len(shots):
                self._prefetch_layers(shots[ni])

    # ── Shot navigation ───────────────────────────────────────────────────────

    def _load_shot(self, shotcode: str = ""):
        sc = (shotcode or self._current_shot).strip().upper()
        if not sc:
            return
        self._current_shot = sc
        self.setWindowTitle(f"|  S E E D  |  //  {sc}")
        self._shot_lbl.setText(sc)

        if sc not in self._seq_shots:
            info = self.db.get(sc, {})
            seq  = info.get("sequence","")
            if seq:
                shots = sorted(
                    [s["shotcode"] for s in self.db.values() if s.get("sequence") == seq],
                    key=lambda c: int(c.split("_")[-1]) if c.split("_")[-1].isdigit() else 0)
                self._seq_shots = shots
                self._browser.set_seq(seq)
        if sc in self._seq_shots:
            self._seq_idx = self._seq_shots.index(sc)
        self._update_seq_pos()

        self._browser.select_shot(sc)
        self._refresh_versions("a")
        self._refresh_versions("b")
        self._update_feedback()

        row   = self.db.get(sc, {})
        seq   = row.get("sequence","")
        start = row.get("start_frame")
        total = row.get("total_frames")
        rng   = (f"frames {start}–{start+total-1}  ({total}f)"
                 if start is not None and total is not None else "")
        self._content.update_shot_info(seq, rng, sc)
        self._reload_async()
        # Pre-warm all layers for this shot + neighbours in background.
        self._prefetch_layers(sc)
        self._prefetch_adjacent()

    def _prev_shot(self):
        if self._seq_shots and self._seq_idx > 0:
            self._seq_idx -= 1
            self._load_shot(self._seq_shots[self._seq_idx])

    def _next_shot(self):
        if self._seq_shots and self._seq_idx < len(self._seq_shots) - 1:
            self._seq_idx += 1
            self._load_shot(self._seq_shots[self._seq_idx])

    def _set_sequence(self, seq: str):
        self._browser.set_seq(seq)
        shots = sorted(
            [s["shotcode"] for s in self.db.values() if s.get("sequence") == seq],
            key=lambda c: int(c.split("_")[-1]) if c.split("_")[-1].isdigit() else 0)
        self._seq_shots = shots
        self._seq_idx   = 0
        if shots:
            self._load_shot(shots[0])

    def _on_seq_change_from_browser(self, seq: str):
        if seq != "ALL":
            shots = sorted(
                [s["shotcode"] for s in self.db.values() if s.get("sequence") == seq],
                key=lambda c: int(c.split("_")[-1]) if c.split("_")[-1].isdigit() else 0)
            self._seq_shots = shots
            self._seq_idx   = 0

    # ── Status / overlay ─────────────────────────────────────────────────────

    def _set_status(self, text: str):
        self._status_lbl.setText(text)

    def _update_status(self):
        sc = self._current_shot
        if not sc:
            return
        nav   = f"  [{self._seq_idx+1}/{len(self._seq_shots)}]" if self._seq_shots else ""
        la    = LAYERS.get(self._layer_a_key, {}).get("label", self._layer_a_key)
        lb    = LAYERS.get(self._layer_b_key, {}).get("label", self._layer_b_key)
        cache = self.wipe._play_cache or self.wipe._cache_a or self.wipe._cache_b
        pct   = f"  │  CACHE {int(100*cache.loaded/max(1,cache.n_total))}%" if cache else ""
        zoom  = f"  │  x{self.wipe._zoom:.1f}" if self.wipe._zoom != 1.0 else ""
        self._status_lbl.setText(
            f"{sc}{nav}  │  {la} / {lb}  │  {self.wipe.mode.upper()}{zoom}{pct}")
        if self.wipe._zoom != 1.0:
            self._zoom_lbl.setText(f"x{self.wipe._zoom:.1f}")
        else:
            self._zoom_lbl.setText("")

    def _update_seq_pos(self):
        if self._seq_shots:
            self._seq_pos_lbl.setText(f"{self._seq_idx+1} / {len(self._seq_shots)}")
        else:
            self._seq_pos_lbl.setText("—")

    def _update_overlay(self):
        sc  = self._current_shot
        seq = self.db.get(sc, {}).get("sequence","")
        la  = LAYERS.get(self._layer_a_key, {}).get("label", self._layer_a_key)
        lb  = LAYERS.get(self._layer_b_key, {}).get("label", self._layer_b_key)
        pos = f"[{self._seq_idx+1}/{len(self._seq_shots)}]" if self._seq_shots else ""
        # Update modern overlay widget
        self._viewer_info.set_info(sc, seq, la, lb, pos)
        # Keep legacy text overlay as fallback
        lines = [sc + (f"  {pos}" if pos else "")]
        if seq:
            lines.append(seq)
        lines.append(f"A: {la}")
        lines.append(f"B: {lb}")
        self.wipe.set_overlay(lines)

    def _update_feedback(self):
        sc = self._current_shot
        sf = find_shot_folder(sc) if sc else None
        fb = (sf / "feedback.txt") if sf else None
        content = "(no feedback yet)"
        if fb and fb.exists():
            try:
                content = fb.read_text(encoding="utf-8")
            except Exception:
                content = "[error reading feedback.txt]"
        self._content.update_feedback(content)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _swap(self):
        self._layer_a_key, self._layer_b_key = self._layer_b_key, self._layer_a_key
        self._raw_a,  self._raw_b  = self._raw_b,  self._raw_a
        ea, eb = self._exposure["a"], self._exposure["b"]
        ga, gb = self._gamma["a"],    self._gamma["b"]
        self._exposure = {"a":eb,"b":ea}
        self._gamma    = {"a":gb,"b":ga}
        img_a = self._apply_grade(self._raw_a, "a")
        img_b = self._apply_grade(self._raw_b, "b")
        # Update static images without resetting playback state
        self.wipe.img_a = img_a
        self.wipe.img_b = img_b
        # Swap frozen-frame buffers so the display reflects the new A/B order
        self.wipe._last_play_fa, self.wipe._last_play_fb = (
            self.wipe._last_play_fb, self.wipe._last_play_fa
        )
        lbl = self.wipe._last_play_label
        if lbl == "A":
            self.wipe._last_play_label = "B"
            self.wipe._last_play_img   = self.wipe._last_play_fb
        elif lbl == "B":
            self.wipe._last_play_label = "A"
            self.wipe._last_play_img   = self.wipe._last_play_fa
        self.wipe._cache_a, self.wipe._cache_b = self.wipe._cache_b, self.wipe._cache_a
        self.wipe._render()
        self._vers_a, self._vers_b = self._vers_b, self._vers_a
        self._content.set_layer("a", self._layer_a_key)
        self._content.set_layer("b", self._layer_b_key)
        self._content.update_grade(self._exposure, self._gamma, self._saturation)

    def _jump_to_shot(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Jump to Shot")
        dlg.setFixedWidth(320)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Shot code:", dlg))
        entry = QLineEdit(self._current_shot, dlg)
        entry.selectAll()
        lay.addWidget(entry)
        btn_row = QWidget(dlg); btn_lay = QHBoxLayout(btn_row)
        go_btn = QPushButton("GO", dlg); go_btn.setDefault(True)
        cancel_btn = QPushButton("Cancel", dlg)
        btn_lay.addStretch(); btn_lay.addWidget(cancel_btn); btn_lay.addWidget(go_btn)
        lay.addWidget(btn_row)
        go_btn.clicked.connect(lambda: (self._load_shot(entry.text().strip().upper()), dlg.accept()))
        cancel_btn.clicked.connect(dlg.reject)
        entry.returnPressed.connect(go_btn.click)
        dlg.exec()

    def _copy(self, text: str):
        QApplication.clipboard().setText(text)

    def _reveal_in_explorer(self):
        sc = self._current_shot
        sf = find_shot_folder(sc) if sc else None
        if sf and sf.exists():
            subprocess.Popen(["explorer", str(sf)])

    def _beeble_current(self):
        sc = self._current_shot
        if not sc:
            return
        script = _REPO / "beeble_submit.py"
        threading.Thread(
            target=lambda: subprocess.run(
                [sys.executable, str(script), "--shot", sc, "--auto-prompt"],
                cwd=str(_REPO)), daemon=True).start()
        self._set_status(f"Beeble: submitting {sc} …")

    def _magnific_current(self):
        sc = self._current_shot
        if not sc:
            return
        script = _REPO / "magnific_submit.py"
        threading.Thread(
            target=lambda: subprocess.run(
                [sys.executable, str(script), "--shot", sc, "--source", "wai", "--resolution", "4k"],
                cwd=str(_REPO)), daemon=True).start()
        self._set_status(f"Magnific: submitting {sc} wai→4k …")

    def _poll_all(self):
        def _run():
            out = ["=== Beeble poll ==="]
            r = subprocess.run([sys.executable, str(_REPO/"beeble_submit.py"), "--poll"],
                               cwd=str(_REPO), capture_output=True, text=True)
            out.append((r.stdout+r.stderr).strip() or "(nothing pending)")
            out += ["","=== Magnific poll ==="]
            r2 = subprocess.run([sys.executable, str(_REPO/"magnific_submit.py"), "--poll"],
                                cwd=str(_REPO), capture_output=True, text=True)
            out.append((r2.stdout+r2.stderr).strip() or "(nothing pending)")
            post_to_main(lambda: QMessageBox.information(
                self, "Poll", "\n".join(out)))
        threading.Thread(target=_run, daemon=True).start()

    def _open_deliver_dialog(self):
        QMessageBox.information(self, "Deliver", "Delivery dialog not yet ported to Qt version.")

    def _refresh_shot(self):
        find_shot_folder.cache_clear()
        self._refresh_versions("a")
        self._refresh_versions("b")
        self._reload()
        self._update_feedback()

    def _show_shortcuts(self):
        txt = (
            "W          toggle wipe\n"
            "S          swap A / B\n"
            "R / G / B  channel greyscale\n"
            "O          overlay toggle\n"
            "← →        step frame\n"
            "↑ ↓        prev / next shot\n"
            "Space      hold=play, release=pause\n"
            "           double-tap=reset to frame 0\n"
            "E drag     exposure\n"
            "C drag     gamma\n"
            "V drag     saturation\n"
            "Backspace  reset grade\n"
            "Shift+R    reload from disk"
        )
        QMessageBox.information(self, "Keyboard Shortcuts", txt)


# ── Font setup ────────────────────────────────────────────────────────────────

def _load_fonts():
    for stem in ("iosevka-term-regular", "iosevka-term-bold"):
        for ext in (".ttf", ".ttc"):
            p = FONT_DIR / (stem + ext)
            if p.exists():
                QFontDatabase.addApplicationFont(str(p))
                break

def _best_mono_font() -> QFont:
    families = QFontDatabase.families()
    # v06 spec: Share Tech Mono > Consolas (always on Windows) > fallbacks
    # Iosevka Term SGr has broken vertical metrics — kept only as last resort
    for candidate in ("Share Tech Mono", "Consolas", "Cascadia Code",
                      "JetBrains Mono", "Courier New", "Iosevka Term"):
        if any(candidate.lower() in f.lower() for f in families):
            return QFont(candidate, 9)
    return QFont("monospace", 9)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Shot viewer — PySide6 SGI/Tron edition")
    ap.add_argument("--shot",     default="", metavar="SHOTCODE")
    ap.add_argument("--sequence", default="", metavar="SEQ")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # Fusion responds best to QSS
    app.setStyleSheet(QSS)

    _load_fonts()
    app.setFont(_best_mono_font())

    # Ensure dispatcher exists on main thread
    _Dispatcher.instance()

    win = ShotViewerApp(
        initial_shot=args.shot.upper() if args.shot else "",
        initial_sequence=args.sequence,
    )

    # Install app-level Space filter so hold-to-play works regardless of focus
    _space_filter = _SpaceFilter(win)
    app.installEventFilter(_space_filter)

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()



def launch() -> "QMainWindow | None":
    """Called by the launcher. Returns the main viewer window."""
    from PySide6.QtWidgets import QApplication
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    win = ShotViewerApp()
    return win

