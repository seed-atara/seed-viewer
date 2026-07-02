#!/usr/bin/env python3
"""
roto_align.py — Fast roto-mask-to-plate alignment tool.

All shots pre-discovered and first/last frames preloaded in background at startup.
Switching shots is instant once preloaded. Only the roto mask moves (offset/scale).

Keyboard (global — works regardless of which control has focus):
  Left / Right          step one frame  (or mask frame when in scrub mode)
  Shift + Left/Right    step 10 frames  (or 10 mask frames in scrub mode)
  Home / End            first / last frame
  [ / ]                 previous / next shot
  , / .                 nudge mask time offset  -1 / +1
  Alt + , / .           nudge plate step  -1 / +1
  S                     toggle Mask Scrub mode (plate frozen, arrows walk mask)

Usage:
    python roto_align.py
    python roto_align.py --shot 081_PTY_1320
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Optional

import numpy as np
from PIL import Image

from PySide6.QtCore import QEvent, Qt, QThread, QTimer, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QGroupBox,
    QHBoxLayout, QLabel, QMainWindow, QPushButton, QSizePolicy,
    QSpinBox, QStatusBar, QVBoxLayout, QWidget,
)

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from seed_viewer.paths import SHOTS_ROOT, find_shot_folder, _load_db

ALIGN_JSON = "roto_align.json"
_WVER_RE   = re.compile(r"_bg_wai_(V\d+)_render\.mp4$", re.IGNORECASE)
CACHE_W    = 1280
CACHE_H    = 720
PRELOAD_R  = 8      # local scrub lookahead
_FNUM_RE   = re.compile(r"(\d{4,})\.\w+$")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class AlignData:
    time_offset: int   = 0
    step:        int   = 1    # mask frames per display frame  (1=24fps mask, 2=48fps mask)
    plate_step:  int   = 1    # plate files per display frame  (1=24fps plate, 2=48fps plate)
    scale_x:     float = 1.0
    scale_y:     float = 1.0
    translate_x: int   = 0   # pixels at native 4K resolution, positive = right
    translate_y: int   = 0   # pixels at native 4K resolution, positive = down


@dataclass
class ShotData:
    shotcode:    str
    shot_folder: Path
    plate_files: list[Path] = field(default_factory=list)
    mask_files:  list[Path] = field(default_factory=list)
    align:       AlignData  = field(default_factory=AlignData)
    wai_render:  Optional[Path] = None   # vfx/*_bg_wai_V*_render.mp4 if present

    @property
    def has_plate(self) -> bool: return bool(self.plate_files)
    @property
    def has_mask(self)  -> bool: return bool(self.mask_files)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fnum(p: Path) -> int:
    m = _FNUM_RE.search(p.name)
    return int(m.group(1)) if m else 0


def load_sequence(folder: Path) -> list[Path]:
    return sorted(
        [p for p in folder.glob("*.png") if _FNUM_RE.search(p.name)], key=_fnum)


def _find_mask_dir(shot_folder: Path) -> Path:
    """Return best available mask directory for the shot."""
    combined = shot_folder / "masks" / "combined"
    if combined.exists() and any(combined.glob("*.png")):
        return combined
    # Fallback: PNGs directly in masks/
    direct = shot_folder / "masks"
    if direct.exists() and any(direct.glob("*.png")):
        return direct
    return combined   # return canonical even if empty so browse label is useful


def _find_wai_render(shot_folder: Path) -> Optional[Path]:
    vfx = shot_folder / "vfx"
    if not vfx.exists():
        return None
    candidates = []
    for p in vfx.glob("*_wai_*_render.mp4"):
        m = _WVER_RE.search(p.name)
        if m:
            candidates.append((int(m.group(1)[1:]), p))
    return max(candidates)[1] if candidates else None


def discover_shots() -> list[ShotData]:
    db = _load_db()
    shots: list[ShotData] = []
    for sc, entry in sorted(db.items()):
        if entry.get("full_cg") or entry.get("sequence") != "081_ANTIGUA_POOL_PARTY":
            continue
        d = find_shot_folder(sc)
        if d is None:
            continue
        # new structure: plate/3840x2160/png; legacy fallback: 3840x2160/png
        plate_dir = d / "plate" / "3840x2160" / "png"
        if not plate_dir.exists():
            plate_dir = d / "3840x2160" / "png"
        mask_dir = _find_mask_dir(d)
        sd = ShotData(
            shotcode    = sc,
            shot_folder = d,
            plate_files = load_sequence(plate_dir) if plate_dir.exists() else [],
            mask_files  = load_sequence(mask_dir)  if mask_dir.exists()  else [],
            align       = _load_align(d),
            wai_render  = _find_wai_render(d),
        )
        shots.append(sd)
    return shots


def _load_align(shot_folder: Path) -> AlignData:
    p = shot_folder / "masks" / ALIGN_JSON
    if p.exists():
        try:
            d = json.loads(p.read_text())
            return AlignData(**{k: v for k, v in d.items()
                                if k in AlignData.__dataclass_fields__})
        except Exception:
            pass
    # Auto-derive offset from frame numbers so masks snap to plate by default
    plate_files = load_sequence(shot_folder / "3840x2160" / "png") if (shot_folder / "3840x2160" / "png").exists() else []
    mask_files  = load_sequence(shot_folder / "masks" / "combined")  if (shot_folder / "masks" / "combined").exists()  else []
    n_p, n_m = len(plate_files), len(mask_files)
    if n_p and n_m:
        # Step: if masks are ~2x plates → step=2 (48fps masks over 24fps plates)
        #       if masks are ~0.5x plates → step=1 but plates are 48fps; align by fnum
        step = _auto_step(n_p, n_m)
        if step == 1:
            # offset so mask[0] aligns to the plate frame with the same source fnum
            plate_fnum0 = _fnum(plate_files[0])
            mask_fnum0  = _fnum(mask_files[0])
            offset = -(mask_fnum0 - plate_fnum0)   # mask_idx = plate_idx*1 + offset
        else:
            offset = 0
        return AlignData(step=step, time_offset=offset)
    return AlignData()


def _save_align(shot_folder: Path, data: AlignData) -> None:
    p = shot_folder / "masks" / ALIGN_JSON
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(data), indent=2))


def _auto_step(n_p: int, n_m: int) -> int:
    return 2 if (n_p > 0 and n_m > 0 and 1.6 <= n_m / n_p <= 2.4) else 1


def _next_version(shot_folder: Path) -> str:
    td = shot_folder / "talent"
    nums = [int(d.name[1:]) for d in td.iterdir()
            if d.is_dir() and re.match(r"^V\d{3}$", d.name)] if td.exists() else []
    return f"V{max(nums, default=0) + 1:03d}"


# ---------------------------------------------------------------------------
# Frame cache (shared across all shots)
# ---------------------------------------------------------------------------

class FrameCache:
    """
    Caches display-resolution images keyed by file Path.
    All shots share one pool; preloads first+last of every shot at startup,
    then adds a local scrub window around the current position.
    """

    def __init__(self) -> None:
        self._cache: dict[Path, Image.Image] = {}
        self._loading: set[Path] = set()
        self._lock  = Lock()
        self._pool  = ThreadPoolExecutor(max_workers=6)
        self._ready_callbacks: list = []   # callables notified on any new load

    def get(self, path: Optional[Path]) -> Optional[Image.Image]:
        if path is None:
            return None
        return self._cache.get(path)

    def preload(self, paths: list[Optional[Path]]) -> None:
        for p in paths:
            if p is None:
                continue
            with self._lock:
                if p in self._cache or p in self._loading:
                    continue
                self._loading.add(p)
            self._pool.submit(self._load, p)

    def _load(self, path: Path) -> None:
        try:
            # PIL's C decoders are NOT safe on concurrent pool threads in the
            # frozen bundle (access violation) — serialize decode process-wide.
            import sys as _sys
            import threading as _thr
            if not hasattr(_sys, "_seed_img_lock"):
                _sys._seed_img_lock = _thr.Lock()
            with _sys._seed_img_lock:
                img = Image.open(path)
                mode = "L" if self._is_mask(path) else "RGB"
                img  = img.convert(mode).resize((CACHE_W, CACHE_H), Image.BILINEAR)
            with self._lock:
                self._cache[path] = img
        except Exception:
            pass
        finally:
            with self._lock:
                self._loading.discard(path)
            for cb in self._ready_callbacks:
                cb()

    @staticmethod
    def _is_mask(path: Path) -> bool:
        # Heuristic: paths inside "masks" are grayscale mattes
        return "masks" in path.parts

    def evict_except(self, keep: set[Path]) -> None:
        with self._lock:
            for k in [k for k in self._cache if k not in keep]:
                del self._cache[k]

    def loading_count(self) -> int:
        with self._lock:
            return len(self._loading)

    def cached_count(self) -> int:
        return len(self._cache)


# ---------------------------------------------------------------------------
# Compositing
# ---------------------------------------------------------------------------

def _to_pixmap(img: Image.Image) -> QPixmap:
    raw  = img.tobytes("raw", "RGB")
    W, H = img.size
    qi   = QImage(raw, W, H, W * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qi)


def _place_mask(mask: Image.Image, W: int, H: int,
                sx: float, sy: float, tx: int, ty: int) -> Image.Image:
    """Scale, translate and center mask onto a W×H canvas (display coords)."""
    mw = max(1, round(W * sx))
    mh = max(1, round(H * sy))
    # tx/ty are in native-4K pixels; scale to display coords
    # display is CACHE_W×CACHE_H, native is 3840×2160
    dx = round(tx * W / 3840)
    dy = round(ty * H / 2160)
    ms = mask.resize((mw, mh), Image.BILINEAR)
    cv = Image.new("L", (W, H), 0)
    ox = (W - mw) // 2 + dx
    oy = (H - mh) // 2 + dy
    cv.paste(ms, (ox, oy))
    return cv


def composite(
    plate:  Image.Image,
    mask:   Optional[Image.Image],
    sx:     float,
    sy:     float,
    tx:     int  = 0,
    ty:     int  = 0,
    mode:   str  = "premul",   # "premul" | "overlay"
) -> QPixmap:
    W, H = plate.size
    if mask is not None:
        cv = _place_mask(mask, W, H, sx, sy, tx, ty)
        pa = np.frombuffer(plate.tobytes(), dtype=np.uint8).reshape(H, W, 3).astype(np.float32)
        ma = np.frombuffer(cv.tobytes(),    dtype=np.uint8).reshape(H, W).astype(np.float32) / 255.0

        if mode == "overlay":
            # Show plate at full brightness; tint mask area green at 40%
            green = np.zeros((H, W, 3), dtype=np.float32)
            green[:, :, 1] = 200.0
            out = np.clip(pa + green * ma[:, :, None] * 0.45, 0, 255).astype(np.uint8)
        else:  # premul
            out = (pa * ma[:, :, None]).clip(0, 255).astype(np.uint8)

        return _to_pixmap(Image.fromarray(out, "RGB"))
    return _to_pixmap(plate)


def render_rgba_4k(plate_path: Path, mask_path: Optional[Path],
                   sx: float, sy: float,
                   tx: int = 0, ty: int = 0) -> Image.Image:
    plate = Image.open(plate_path).convert("RGB")
    W, H  = plate.size
    if mask_path and mask_path.exists():
        mask   = Image.open(mask_path).convert("L")
        mw, mh = max(1, round(W * sx)), max(1, round(H * sy))
        scaled  = mask.resize((mw, mh), Image.LANCZOS)
        canvas  = Image.new("L", (W, H), 0)
        canvas.paste(scaled, ((W - mw) // 2 + tx, (H - mh) // 2 + ty))
        alpha   = canvas
    else:
        alpha = Image.new("L", (W, H), 255)
    rgba = plate.convert("RGBA")
    rgba.putalpha(alpha)
    return rgba


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Timeline bar
# ---------------------------------------------------------------------------

from PySide6.QtCore import Signal as _Signal
from PySide6.QtGui  import QPainter, QColor, QFont

class TimelineBar(QWidget):
    """
    Narrow bar showing:
      ░░░░░░░░░  full plate range  (dark)
      ████████   mask coverage     (green / orange)
      │           current frame    (bright yellow tick)
      ◀ ▶         first/last valid markers
    Click to jump to frame.
    """
    frame_clicked = _Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._n_plate    = 0
        self._first_v    = 0
        self._last_v     = 0
        self._cur        = 0
        self._has_mask   = False

    def update_state(self, n_plate: int, first_valid: int, last_valid: int,
                     cur: int, has_mask: bool) -> None:
        self._n_plate  = n_plate
        self._first_v  = first_valid
        self._last_v   = last_valid
        self._cur      = cur
        self._has_mask = has_mask
        self.update()

    def _x(self, idx: int) -> int:
        if self._n_plate <= 1:
            return 0
        return round(idx / (self._n_plate - 1) * (self.width() - 1))

    def paintEvent(self, event):
        p  = QPainter(self)
        W  = self.width()
        H  = self.height()

        # Background — plate range
        p.fillRect(0, 4, W, H - 8, QColor("#2a2a2a"))

        if self._n_plate > 0:
            # Mask coverage band
            if self._has_mask and self._last_v >= self._first_v:
                x0 = self._x(self._first_v)
                x1 = self._x(self._last_v)
                cov = (self._last_v - self._first_v + 1) / self._n_plate
                col = QColor("#44aa44") if cov > 0.9 else QColor("#cc8800")
                p.fillRect(x0, 8, max(1, x1 - x0), H - 16, col)
            elif not self._has_mask:
                p.fillRect(0, 8, W, H - 16, QColor("#882222"))

            # First/last valid tick marks
            if self._has_mask:
                p.setPen(QColor("#aaffaa"))
                xf = self._x(self._first_v)
                xl = self._x(self._last_v)
                p.drawLine(xf, 2, xf, H - 2)
                p.drawLine(xl, 2, xl, H - 2)

            # Current frame — bright yellow
            xc = self._x(self._cur)
            p.setPen(QColor("#ffee00"))
            p.drawLine(xc, 0, xc, H)

            # Frame counter text
            p.setPen(QColor("#888888"))
            f = QFont(); f.setPointSize(8); p.setFont(f)
            p.drawText(4, H - 4, f"{self._cur+1}/{self._n_plate}")
            if self._has_mask:
                txt = f"mask {self._first_v}..{self._last_v}"
                p.drawText(W // 2 - 40, H - 4, txt)

        p.end()

    def mousePressEvent(self, event):
        if self._n_plate > 1:
            frac = event.position().x() / self.width()
            idx  = round(frac * (self._n_plate - 1))
            self.frame_clicked.emit(max(0, min(self._n_plate - 1, idx)))

    def mouseMoveEvent(self, event):
        if event.buttons() and self._n_plate > 1:
            frac = event.position().x() / self.width()
            idx  = round(frac * (self._n_plate - 1))
            self.frame_clicked.emit(max(0, min(self._n_plate - 1, idx)))


# ---------------------------------------------------------------------------
# Background export worker
# ---------------------------------------------------------------------------

class _FullExportThread(QThread):
    progress = _Signal(str)    # status-bar message every frame
    console  = _Signal(str)    # line to print
    done     = _Signal(int)    # emitted when finished: n_shots processed

    def __init__(self, shots: list) -> None:
        super().__init__()
        self._shots = shots

    def run(self) -> None:
        shots_with_plates = [s for s in self._shots if s.plate_files]
        n_shots = len(shots_with_plates)

        for si, sd in enumerate(shots_with_plates):
            a   = sd.align
            ps  = max(1, a.plate_step)
            nd  = max(1, len(sd.plate_files) // ps)

            version = _next_version(sd.shot_folder)
            ver_dir = sd.shot_folder / "talent" / version
            png_dir = ver_dir / "png"
            out_mp4 = ver_dir / f"{sd.shotcode}_talent_{version}.mp4"
            png_dir.mkdir(parents=True, exist_ok=True)

            self.console.emit(f"\n{'='*60}")
            self.console.emit(f"  [{si+1}/{n_shots}]  {sd.shotcode}  {version}  ({nd} frames)")

            # ---- write all RGBA frames ----
            for pidx in range(nd):
                file_idx = pidx * ps
                if not (0 <= file_idx < len(sd.plate_files)):
                    continue
                pp   = sd.plate_files[file_idx]
                midx = pidx * a.step + a.time_offset
                mp   = (sd.mask_files[midx]
                        if sd.mask_files and 0 <= midx < len(sd.mask_files)
                        else None)
                seq     = pidx + 1
                out_png = png_dir / f"{sd.shotcode}_talent_{version}.{seq:04d}.png"

                self.progress.emit(
                    f"[{si+1}/{n_shots}]  {sd.shotcode}  PNG {pidx+1}/{nd}  "
                    f"{'[no mask]' if mp is None else ''}")

                rgba = render_rgba_4k(pp, mp, a.scale_x, a.scale_y,
                                      a.translate_x, a.translate_y)
                rgba.save(out_png, compress_level=1)

            self.console.emit(f"  -> {nd} RGBA PNGs written")

            # ---- encode over-black premultiplied MP4 ----
            png_pattern = str(png_dir / f"{sd.shotcode}_talent_{version}.%04d.png")
            fg_ob = (
                "[0:v]format=rgba,split[src][asrc];"
                "[asrc]alphaextract[mask];"
                "[src]format=rgb24[rgb];"
                "[rgb][mask]blend=all_mode=multiply[ob];"
                "[ob]format=yuv420p[out]"
            )
            cmd_mp4 = [
                "ffmpeg", "-y",
                "-framerate", "24",
                "-start_number", "1",
                "-i", png_pattern,
                "-filter_complex", fg_ob,
                "-map", "[out]",
                "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                "-color_primaries", "bt709", "-color_trc", "bt709",
                "-colorspace", "bt709", "-color_range", "tv",
                "-movflags", "+faststart",
                str(out_mp4),
            ]
            self.progress.emit(
                f"[{si+1}/{n_shots}]  {sd.shotcode}  encoding MP4...")
            r = subprocess.run(cmd_mp4, capture_output=True, text=True)
            if r.returncode != 0:
                self.console.emit(f"  ERROR MP4: {r.stderr[-400:]}")
            else:
                mb = out_mp4.stat().st_size / 1048576
                self.console.emit(f"  -> {out_mp4.name}  ({mb:.1f} MB)")

        self.done.emit(n_shots)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class AlignWindow(QMainWindow):

    def __init__(self, start_shot: Optional[str] = None) -> None:
        super().__init__()
        self.setWindowTitle("Roto Align")

        self.shots: list[ShotData] = []
        self.shot_idx        = 0
        self.plate_idx       = 0
        self._lock           = False
        self._cache          = FrameCache()
        self._scrub_mode     = False   # mask scrub: plate frozen, arrows walk mask
        self._scrub_mask_idx = 0       # absolute mask file index in scrub mode

        self._resize_timer = QTimer(); self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._refresh)

        self._build_ui()
        QApplication.instance().installEventFilter(self)

        # Notify UI when any frame becomes available
        self._cache._ready_callbacks.append(self._on_frame_ready)

        self.statusBar().showMessage("Discovering shots...")
        QApplication.processEvents()

        self.shots = discover_shots()
        self._populate_combo()

        # Preload first+last of ALL shots in background immediately
        self._preload_all_firstlast()

        idx = 0
        if start_shot:
            for i, sd in enumerate(self.shots):
                if sd.shotcode == start_shot:
                    idx = i; break
        self.shot_combo.setCurrentIndex(idx)
        self._load_shot(idx)

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(8)

        left = QVBoxLayout(); left.setSpacing(4)
        root.addLayout(left, 0)

        # Shot navigation
        g = QGroupBox("Shot  ([ ]  to step shots)")
        gl = QVBoxLayout(g)
        self.shot_combo = QComboBox()
        self.shot_combo.currentIndexChanged.connect(self._load_shot)
        gl.addWidget(self.shot_combo)

        snav = QHBoxLayout()
        b_ps = QPushButton("< Prev Shot"); b_ps.clicked.connect(self._prev_shot)
        b_ns = QPushButton("Next Shot >"); b_ns.clicked.connect(self._next_shot)
        snav.addWidget(b_ps); snav.addWidget(b_ns)
        gl.addLayout(snav)

        for attr, browse_fn in [("plate_label", self._browse_plate),
                                 ("mask_label",  self._browse_mask)]:
            row = QHBoxLayout()
            lbl = QLabel("—"); lbl.setWordWrap(True); setattr(self, attr, lbl)
            btn = QPushButton("Browse"); btn.setFixedWidth(60); btn.clicked.connect(browse_fn)
            row.addWidget(lbl, 1); row.addWidget(btn)
            gl.addLayout(row)

        # WAI render fallback
        wai_row = QHBoxLayout()
        self.wai_label = QLabel("WAI: —"); self.wai_label.setWordWrap(True)
        self.btn_use_wai = QPushButton("Use WAI"); self.btn_use_wai.setFixedWidth(72)
        self.btn_use_wai.setEnabled(False)
        self.btn_use_wai.clicked.connect(self._use_wai_render)
        wai_row.addWidget(self.wai_label, 1); wai_row.addWidget(self.btn_use_wai)
        gl.addLayout(wai_row)

        self.seq_info    = QLabel("plate: 0   mask: 0")
        self.coverage_lbl = QLabel("")
        self.coverage_lbl.setWordWrap(True)
        self.preload_lbl  = QLabel("")
        gl.addWidget(self.seq_info)
        gl.addWidget(self.coverage_lbl)
        gl.addWidget(self.preload_lbl)
        left.addWidget(g)

        # Frame nav
        g2 = QGroupBox("Frame  (← →  Shift±10  Home End)")
        gf = QVBoxLayout(g2)
        self.frame_spin = QSpinBox(); self.frame_spin.setMinimum(0)
        self.frame_spin.valueChanged.connect(self._on_frame_spin)
        bp = QPushButton("<"); bp.setFixedWidth(28); bp.clicked.connect(lambda: self._step(-1))
        bn = QPushButton(">"); bn.setFixedWidth(28); bn.clicked.connect(lambda: self._step(1))
        nr = QHBoxLayout(); nr.addWidget(bp); nr.addWidget(self.frame_spin, 1); nr.addWidget(bn)
        gf.addLayout(nr)
        fr = QHBoxLayout()
        bf = QPushButton("First"); bf.clicked.connect(self._go_first)
        bl = QPushButton("Last");  bl.clicked.connect(self._go_last)
        fr.addWidget(bf); fr.addWidget(bl)
        gf.addLayout(fr)
        # Valid-range buttons
        vr = QHBoxLayout()
        bfv = QPushButton("First Valid"); bfv.clicked.connect(self._go_first_valid)
        blv = QPushButton("Last Valid");  blv.clicked.connect(self._go_last_valid)
        vr.addWidget(bfv); vr.addWidget(blv)
        gf.addLayout(vr)
        self.mask_info = QLabel("mask: —"); self.mask_info.setWordWrap(True)
        gf.addWidget(self.mask_info)

        # Mask scrub mode
        self.btn_scrub = QPushButton("Mask Scrub  (S)")
        self.btn_scrub.setCheckable(True)
        self.btn_scrub.setStyleSheet(
            "QPushButton:checked { background:#884400; color:#ffcc66; font-weight:bold; }")
        self.btn_scrub.clicked.connect(self._toggle_scrub)
        gf.addWidget(self.btn_scrub)
        self.btn_capture = QPushButton("Capture Offset from Scrub")
        self.btn_capture.setEnabled(False)
        self.btn_capture.clicked.connect(self._capture_offset)
        gf.addWidget(self.btn_capture)

        left.addWidget(g2)

        # Alignment
        g3 = QGroupBox("Mask Alignment  (, . nudge offset)")
        ga = QVBoxLayout(g3)
        ga.addWidget(QLabel("Plate step  (1=24fps plate  2=48fps plate):"))
        self.plate_step_spin = QSpinBox(); self.plate_step_spin.setRange(1, 4)
        self.plate_step_spin.valueChanged.connect(self._on_plate_step)
        ga.addWidget(self.plate_step_spin)
        ga.addWidget(QLabel("Mask step  (1=24fps mask  2=48fps mask):"))
        self.step_spin = QSpinBox(); self.step_spin.setRange(1, 4)
        self.step_spin.valueChanged.connect(self._on_ctrl)
        ga.addWidget(self.step_spin)
        ga.addWidget(QLabel("Time offset:"))
        self.offset_spin = QSpinBox(); self.offset_spin.setRange(-9999, 9999)
        self.offset_spin.valueChanged.connect(self._on_ctrl)
        ga.addWidget(self.offset_spin)
        ga.addWidget(QLabel("Scale X:"))
        self.sx = QDoubleSpinBox(); self.sx.setRange(0.1, 4.0)
        self.sx.setSingleStep(0.001); self.sx.setDecimals(4); self.sx.setValue(1.0)
        self.sx.valueChanged.connect(self._on_ctrl)
        ga.addWidget(self.sx)
        ga.addWidget(QLabel("Scale Y:"))
        self.sy = QDoubleSpinBox(); self.sy.setRange(0.1, 4.0)
        self.sy.setSingleStep(0.001); self.sy.setDecimals(4); self.sy.setValue(1.0)
        self.sy.valueChanged.connect(self._on_ctrl)
        ga.addWidget(self.sy)

        ga.addWidget(QLabel("Translate X  (px, + = right):"))
        self.tx = QSpinBox(); self.tx.setRange(-3840, 3840); self.tx.setSingleStep(4)
        self.tx.valueChanged.connect(self._on_ctrl)
        ga.addWidget(self.tx)
        ga.addWidget(QLabel("Translate Y  (px, + = down):"))
        self.ty = QSpinBox(); self.ty.setRange(-2160, 2160); self.ty.setSingleStep(4)
        self.ty.valueChanged.connect(self._on_ctrl)
        ga.addWidget(self.ty)
        left.addWidget(g3)

        # View mode
        g5 = QGroupBox("View Mode")
        gv = QHBoxLayout(g5)
        self.btn_premul  = QPushButton("Premul");  self.btn_premul.setCheckable(True)
        self.btn_overlay = QPushButton("Overlay"); self.btn_overlay.setCheckable(True)
        self.btn_premul.setChecked(True)
        self.btn_premul.clicked.connect(lambda: self._set_mode("premul"))
        self.btn_overlay.clicked.connect(lambda: self._set_mode("overlay"))
        gv.addWidget(self.btn_premul); gv.addWidget(self.btn_overlay)
        left.addWidget(g5)

        # Actions
        g4 = QGroupBox("Actions")
        gac = QVBoxLayout(g4)
        bs  = QPushButton("Save Alignment");                  bs.clicked.connect(self._save)
        be  = QPushButton("Export First+Last RGBA (4K)");     be.clicked.connect(self._export)
        bba = QPushButton("Batch Export ALL  (first+last RGBA  4K)")
        bba.clicked.connect(self._batch_export)
        bbf = QPushButton("Batch Export FULL  (all frames RGBA + MP4)")
        bbf.setStyleSheet("font-weight: bold;")
        bbf.clicked.connect(self._batch_export_full)
        gac.addWidget(bs); gac.addWidget(be); gac.addWidget(bba); gac.addWidget(bbf)
        left.addWidget(g4)
        left.addStretch(1)

        # Image + timeline in a column
        img_col = QVBoxLayout(); img_col.setSpacing(2)
        self.img_label = QLabel("Loading shots...")
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_label.setStyleSheet("background:#111; color:#555;")
        self.img_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.img_label.setMinimumSize(800, 430)
        self.timeline = TimelineBar()
        self.timeline.frame_clicked.connect(self._on_timeline_click)
        img_col.addWidget(self.img_label, 1)
        img_col.addWidget(self.timeline)

        img_wrap = QWidget()
        img_wrap.setLayout(img_col)
        root.addWidget(img_wrap, 1)

        self._view_mode = "premul"
        self.setStatusBar(QStatusBar())
        self.resize(1600, 900)

    # -----------------------------------------------------------------------
    # Global keyboard
    # -----------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            k    = event.key()
            mods = event.modifiers()
            n    = 10 if (mods & Qt.KeyboardModifier.ShiftModifier) else 1
            if k == Qt.Key.Key_S:
                self._toggle_scrub(); return True
            if self._scrub_mode:
                if k == Qt.Key.Key_Left:   self._scrub_step(-n); return True
                if k == Qt.Key.Key_Right:  self._scrub_step(n);  return True
                if k == Qt.Key.Key_Home:   self._scrub_step(-9999999); return True
                if k == Qt.Key.Key_End:    self._scrub_step( 9999999); return True
                return super().eventFilter(obj, event)
            if k == Qt.Key.Key_Left:        self._step(-n);          return True
            if k == Qt.Key.Key_Right:       self._step(n);           return True
            if k == Qt.Key.Key_Home:        self._go_first();         return True
            if k == Qt.Key.Key_End:         self._go_last();          return True
            if k == Qt.Key.Key_BracketLeft: self._prev_shot();        return True
            if k == Qt.Key.Key_BracketRight:self._next_shot();        return True
            if k == Qt.Key.Key_Comma:
                if mods & Qt.KeyboardModifier.AltModifier:
                    self.plate_step_spin.setValue(
                        max(1, self.plate_step_spin.value() - 1)); return True
                self.offset_spin.setValue(self.offset_spin.value() - 1); return True
            if k == Qt.Key.Key_Period:
                if mods & Qt.KeyboardModifier.AltModifier:
                    self.plate_step_spin.setValue(
                        min(4, self.plate_step_spin.value() + 1)); return True
                self.offset_spin.setValue(self.offset_spin.value() + 1); return True
        return super().eventFilter(obj, event)

    # -----------------------------------------------------------------------
    # Plate-step helpers
    # -----------------------------------------------------------------------

    def _n_display(self, sd: Optional["ShotData"] = None) -> int:
        """Number of displayable frames given current plate_step."""
        if sd is None:
            sd = self._cur()
        if not sd or not sd.plate_files:
            return 0
        ps = self.plate_step_spin.value()
        return max(1, len(sd.plate_files) // max(1, ps))

    def _plate_file(self, pidx: int, sd: Optional["ShotData"] = None) -> Optional[Path]:
        """Return the actual plate file for display frame pidx."""
        if sd is None:
            sd = self._cur()
        if not sd or not sd.plate_files:
            return None
        file_idx = pidx * self.plate_step_spin.value()
        return sd.plate_files[file_idx] if 0 <= file_idx < len(sd.plate_files) else None

    def _on_plate_step(self) -> None:
        if self._lock:
            return
        sd = self._cur()
        if sd:
            nd = self._n_display(sd)
            self._lock = True
            self.frame_spin.setMaximum(max(0, nd - 1))
            self.plate_idx = min(self.plate_idx, max(0, nd - 1))
            self.frame_spin.setValue(self.plate_idx)
            self._lock = False
        self._preload_local()
        self._refresh()

    # -----------------------------------------------------------------------
    # WAI render fallback
    # -----------------------------------------------------------------------

    def _use_wai_render(self) -> None:
        sd = self._cur()
        if not sd or not sd.wai_render:
            self.statusBar().showMessage("No WAI render found for this shot")
            return

        tmp_dir = sd.shot_folder / "masks" / ".wai_temp"
        existing = load_sequence(tmp_dir) if tmp_dir.exists() else []
        if existing:
            sd.mask_files = existing
            self._on_wai_loaded(sd)
            return

        self.statusBar().showMessage(
            f"Extracting WAI luma frames...  {sd.wai_render.name}")
        QApplication.processEvents()

        def _extract():
            tmp_dir.mkdir(parents=True, exist_ok=True)
            cmd = [
                "ffmpeg", "-y",
                "-i", str(sd.wai_render),
                "-vf", "format=gray",
                "-start_number", "1",
                str(tmp_dir / "wai_%04d.png"),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            return r.returncode == 0, r.stderr

        ok, err = _extract()
        if ok:
            sd.mask_files = load_sequence(tmp_dir)
            self._on_wai_loaded(sd)
        else:
            self.statusBar().showMessage(f"WAI extraction failed: {err[-200:]}")

    def _on_wai_loaded(self, sd: "ShotData") -> None:
        self.seq_info.setText(
            f"plate: {len(sd.plate_files)}   mask: {len(sd.mask_files)}  [WAI]")
        self.mask_label.setText(
            str(sd.shot_folder / "masks" / ".wai_temp"))
        # WAI is 1920×1080 → set scale 1.0; it will be upscaled to plate size on export
        self._lock = True
        self.sx.setValue(1.0); self.sy.setValue(1.0)
        self._lock = False
        self._cache.preload(sd.mask_files[:4] + sd.mask_files[-4:])
        self._refresh()
        self.statusBar().showMessage(
            f"Using WAI render  ({len(sd.mask_files)} frames, 1920×1080 → upscale on export)")

    # -----------------------------------------------------------------------
    # Shot preloading
    # -----------------------------------------------------------------------

    def _preload_all_firstlast(self) -> None:
        """Queue first+last plate and mask frames for all shots."""
        paths: list[Optional[Path]] = []
        for sd in self.shots:
            a  = sd.align
            ps = max(1, a.plate_step)
            nd = max(1, len(sd.plate_files) // ps) if sd.plate_files else 0
            if sd.plate_files and nd > 0:
                paths += [sd.plate_files[0],
                          sd.plate_files[min((nd - 1) * ps, len(sd.plate_files) - 1)]]
            if sd.mask_files:
                for pidx in ([0, nd - 1] if nd > 0 else []):
                    midx = pidx * a.step + a.time_offset
                    if 0 <= midx < len(sd.mask_files):
                        paths.append(sd.mask_files[midx])
                paths += [sd.mask_files[0], sd.mask_files[-1]]
        self._cache.preload(paths)
        self._update_preload_label()

    def _preload_local(self) -> None:
        """Preload scrub window around current plate frame."""
        sd = self._cur()
        if not sd:
            return
        step   = self.step_spin.value()
        offset = self.offset_spin.value()
        paths: list[Optional[Path]] = []
        nd = self._n_display(sd)
        for di in range(-PRELOAD_R, PRELOAD_R + 1):
            pidx = self.plate_idx + di
            midx = pidx * step + offset
            if 0 <= pidx < nd:
                pf = self._plate_file(pidx, sd)
                if pf:
                    paths.append(pf)
            if 0 <= midx < len(sd.mask_files):
                paths.append(sd.mask_files[midx])
        self._cache.preload(paths)

    def _update_preload_label(self) -> None:
        total  = sum(2 + (2 if sd.mask_files else 0) for sd in self.shots)
        cached = self._cache.cached_count()
        loading = self._cache.loading_count()
        if loading:
            self.preload_lbl.setText(f"preloading... {cached}/{total} frames")
        elif cached < total:
            self.preload_lbl.setText(f"cached: {cached}/{total}")
        else:
            self.preload_lbl.setText(f"all first/last preloaded ({cached} frames)")

    def _on_frame_ready(self) -> None:
        """Called from a background loader thread when a frame finishes.

        Widgets must ONLY be touched from the GUI thread (QLabel.setPixmap from a
        worker is an access-violation lottery in the frozen build) — marshal via a
        queued invocation onto the main thread."""
        from PySide6.QtCore import QMetaObject, Qt as _Qt
        QMetaObject.invokeMethod(self, "_on_frame_ready_main", _Qt.QueuedConnection)

    @Slot()
    def _on_frame_ready_main(self) -> None:
        self._update_preload_label()
        self._refresh()

    # -----------------------------------------------------------------------
    # Shot navigation
    # -----------------------------------------------------------------------

    def _populate_combo(self) -> None:
        self.shot_combo.blockSignals(True)
        self.shot_combo.clear()
        for sd in self.shots:
            tag = ""
            if not sd.has_plate: tag += " [no plate]"
            if not sd.has_mask:  tag += " [no mask]"
            self.shot_combo.addItem(sd.shotcode + tag)
        self.shot_combo.blockSignals(False)

    def _cur(self) -> Optional[ShotData]:
        if 0 <= self.shot_idx < len(self.shots):
            return self.shots[self.shot_idx]
        return None

    def _prev_shot(self) -> None:
        if self.shot_idx > 0:
            self.shot_combo.setCurrentIndex(self.shot_idx - 1)

    def _next_shot(self) -> None:
        if self.shot_idx < len(self.shots) - 1:
            self.shot_combo.setCurrentIndex(self.shot_idx + 1)

    def _load_shot(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.shots):
            return
        self.shot_idx  = idx
        sd             = self.shots[idx]
        self.plate_idx = 0

        self.seq_info.setText(
            f"plate: {len(sd.plate_files)}   mask: {len(sd.mask_files)}")
        plate_dir = sd.shot_folder / "3840x2160" / "png"
        mask_dir  = sd.shot_folder / "masks" / "combined"
        self.plate_label.setText(str(plate_dir))
        self.mask_label.setText(str(mask_dir))

        self._lock = True
        self.plate_step_spin.setValue(sd.align.plate_step)
        nd = max(1, len(sd.plate_files) // max(1, sd.align.plate_step))
        self.frame_spin.setMaximum(max(0, nd - 1))
        self.frame_spin.setValue(0)
        self.offset_spin.setValue(sd.align.time_offset)
        self.step_spin.setValue(sd.align.step)
        self.sx.setValue(sd.align.scale_x)
        self.sy.setValue(sd.align.scale_y)
        self.tx.setValue(sd.align.translate_x)
        self.ty.setValue(sd.align.translate_y)
        self._lock = False

        # WAI render info
        if sd.wai_render:
            self.wai_label.setText(sd.wai_render.name)
            self.btn_use_wai.setEnabled(True)
        else:
            self.wai_label.setText("WAI: not found")
            self.btn_use_wai.setEnabled(False)

        self._preload_local()
        self._refresh()

    def _browse_plate(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select Plate PNG Folder")
        if not d or not (sd := self._cur()):
            return
        sd.plate_files = load_sequence(Path(d))
        self.plate_label.setText(d)
        self.seq_info.setText(f"plate: {len(sd.plate_files)}   mask: {len(sd.mask_files)}")
        nd = self._n_display(sd)
        self.frame_spin.setMaximum(max(0, nd - 1))
        self._preload_local(); self._refresh()

    def _browse_mask(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select Mask PNG Folder")
        if not d or not (sd := self._cur()):
            return
        sd.mask_files = load_sequence(Path(d))
        self.mask_label.setText(d)
        self.seq_info.setText(f"plate: {len(sd.plate_files)}   mask: {len(sd.mask_files)}")
        self._preload_local(); self._refresh()

    # -----------------------------------------------------------------------
    # Frame navigation
    # -----------------------------------------------------------------------

    def _step(self, delta: int) -> None:
        sd = self._cur()
        if not sd or not sd.plate_files:
            return
        self.plate_idx = max(0, min(self._n_display(sd) - 1, self.plate_idx + delta))
        self._lock = True
        self.frame_spin.setValue(self.plate_idx)
        self._lock = False
        self._preload_local()
        self._refresh()

    def _on_frame_spin(self, val: int) -> None:
        if self._lock:
            return
        self.plate_idx = val
        self._preload_local()
        self._refresh()

    def _go_first(self) -> None: self._step(-9999999)
    def _go_last(self)  -> None: self._step( 9999999)

    def _valid_range(self) -> tuple[int, int]:
        """Return (first_valid_display_idx, last_valid_display_idx) where mask is in range."""
        sd = self._cur()
        if not sd or not sd.plate_files:
            return 0, 0
        nd  = self._n_display(sd)
        if not sd.mask_files:
            return 0, nd - 1
        n_m  = len(sd.mask_files)
        step = self.step_spin.value()
        off  = self.offset_spin.value()
        first = next((i for i in range(nd)       if 0 <= i * step + off < n_m), 0)
        last  = next((i for i in range(nd-1, -1, -1) if 0 <= i * step + off < n_m), nd - 1)
        return first, last

    def _go_first_valid(self) -> None:
        f, _ = self._valid_range()
        self.plate_idx = f
        self._lock = True; self.frame_spin.setValue(f); self._lock = False
        self._preload_local(); self._refresh()

    def _go_last_valid(self) -> None:
        _, l = self._valid_range()
        self.plate_idx = l
        self._lock = True; self.frame_spin.setValue(l); self._lock = False
        self._preload_local(); self._refresh()

    def _update_coverage(self) -> None:
        sd = self._cur()
        if not sd:
            return
        if not sd.mask_files:
            self.coverage_lbl.setStyleSheet("color: #ff4444; font-weight: bold;")
            self.coverage_lbl.setText("NO MASKS for this shot")
            return
        f, l = self._valid_range()
        n_p  = len(sd.plate_files)
        n_valid = l - f + 1 if l >= f else 0
        pct = int(100 * n_valid / n_p) if n_p else 0
        if n_valid == n_p:
            self.coverage_lbl.setStyleSheet("color: #44cc44;")
            self.coverage_lbl.setText(f"mask covers full plate  ({n_valid}f)")
        elif n_valid == 0:
            self.coverage_lbl.setStyleSheet("color: #ff4444; font-weight: bold;")
            self.coverage_lbl.setText("mask covers 0 frames — check offset/step")
        else:
            self.coverage_lbl.setStyleSheet("color: #ffaa33;")
            self.coverage_lbl.setText(
                f"mask covers plate[{f}..{l}]  ({n_valid}/{n_p}f  {pct}%)\n"
                f"  {n_p-n_valid}f uncovered  ({f} head + {n_p-1-l} tail)")

    # -----------------------------------------------------------------------
    # Alignment controls
    # -----------------------------------------------------------------------

    def _on_ctrl(self) -> None:
        if self._lock:
            return
        self._preload_local()
        self._refresh()

    def _mask_idx(self, pidx: int) -> int:
        return pidx * self.step_spin.value() + self.offset_spin.value()

    def _mask_path(self, pidx: int) -> Optional[Path]:
        sd = self._cur()
        if self._scrub_mode:
            if sd and 0 <= self._scrub_mask_idx < len(sd.mask_files):
                p = sd.mask_files[self._scrub_mask_idx]
                self.mask_info.setStyleSheet(
                    "color: #ffcc66; font-weight: bold;")
                self.mask_info.setText(
                    f"SCRUB  mask[{self._scrub_mask_idx}/{len(sd.mask_files)-1}]  {p.name}")
                return p
            self.mask_info.setStyleSheet("color: #ff4444; font-weight: bold;")
            self.mask_info.setText("SCRUB — no mask files")
            return None
        midx = self._mask_idx(pidx)
        if sd and 0 <= midx < len(sd.mask_files):
            p = sd.mask_files[midx]
            self.mask_info.setStyleSheet("color: #cccccc;")
            self.mask_info.setText(f"mask[{midx}]  {p.name}")
            return p
        self.mask_info.setStyleSheet("color: #ff4444; font-weight: bold;")
        self.mask_info.setText(
            f"NO MASK  (mask[{midx}] out of range 0..{len(sd.mask_files)-1 if sd else '?'})")
        return None

    # -----------------------------------------------------------------------
    # Mask scrub mode
    # -----------------------------------------------------------------------

    def _toggle_scrub(self) -> None:
        sd = self._cur()
        self._scrub_mode = not self._scrub_mode
        self.btn_scrub.setChecked(self._scrub_mode)
        self.btn_capture.setEnabled(self._scrub_mode and bool(sd and sd.mask_files))
        if self._scrub_mode and sd and sd.mask_files:
            # Initialise scrub index to wherever the current alignment lands
            midx = self._mask_idx(self.plate_idx)
            self._scrub_mask_idx = max(0, min(len(sd.mask_files) - 1, midx))
        self._refresh()

    def _scrub_step(self, delta: int) -> None:
        sd = self._cur()
        if not sd or not sd.mask_files:
            return
        self._scrub_mask_idx = max(
            0, min(len(sd.mask_files) - 1, self._scrub_mask_idx + delta))
        # Preload neighbours in mask sequence
        paths = []
        for di in range(-PRELOAD_R, PRELOAD_R + 1):
            mi = self._scrub_mask_idx + di
            if 0 <= mi < len(sd.mask_files):
                paths.append(sd.mask_files[mi])
        self._cache.preload(paths)
        self._refresh()

    def _capture_offset(self) -> None:
        """Compute time_offset so mask[scrub_idx] aligns to current plate frame."""
        sd = self._cur()
        if not sd:
            return
        # mask_idx = plate_idx * step + offset  =>  offset = mask_idx - plate_idx * step
        new_offset = self._scrub_mask_idx - self.plate_idx * self.step_spin.value()
        self.offset_spin.setValue(new_offset)
        # Exit scrub mode automatically
        self._scrub_mode = False
        self.btn_scrub.setChecked(False)
        self.btn_capture.setEnabled(False)
        self._refresh()
        self.statusBar().showMessage(
            f"Offset captured: {new_offset}  "
            f"(mask[{self._scrub_mask_idx}] locked to plate[{self.plate_idx}])")

    # -----------------------------------------------------------------------
    # Display
    # -----------------------------------------------------------------------

    def _refresh(self) -> None:
        sd = self._cur()
        if not sd or not sd.plate_files:
            self.img_label.setText("No plate sequence")
            return

        self._update_coverage()

        pf = self._plate_file(self.plate_idx)
        plate_img = self._cache.get(pf)
        if plate_img is None:
            self.statusBar().showMessage(
                f"[{self.plate_idx+1}/{self._n_display()}]  loading plate...")
            return

        mask_path = self._mask_path(self.plate_idx)
        mask_img  = self._cache.get(mask_path)

        px = composite(
            plate_img, mask_img,
            self.sx.value(), self.sy.value(),
            self.tx.value(), self.ty.value(),
            self._view_mode,
        )

        lw = max(self.img_label.width(),  800)
        lh = max(self.img_label.height(), 450)
        px = px.scaled(lw, lh,
                       Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.FastTransformation)
        self.img_label.setPixmap(px)

        # Border: orange = scrub mode, red = no mask, none = normal
        if self._scrub_mode:
            self.img_label.setStyleSheet(
                "background:#111; border: 4px solid #ff8800;")
        elif mask_path is None:
            self.img_label.setStyleSheet(
                "background:#111; border: 4px solid #ff3333;")
        else:
            self.img_label.setStyleSheet("background:#111; border: none;")

        # Update timeline bar
        fv, lv = self._valid_range()
        self.timeline.update_state(
            n_plate    = len(sd.plate_files),
            first_valid = fv,
            last_valid  = lv,
            cur        = self.plate_idx,
            has_mask   = bool(sd.mask_files),
        )

        no_mask_str = "  *** NO MASK ***" if mask_path is None else ""
        tx_str = f"  tx={self.tx.value()}  ty={self.ty.value()}" \
                 if (self.tx.value() or self.ty.value()) else ""
        ps_str = f"  ps={self.plate_step_spin.value()}" \
                 if self.plate_step_spin.value() != 1 else ""
        self.statusBar().showMessage(
            f"[{self.plate_idx+1}/{self._n_display()}]  {sd.shotcode}  "
            f"|  offset={self.offset_spin.value()}  step={self.step_spin.value()}{ps_str}  "
            f"sx={self.sx.value():.4f}  sy={self.sy.value():.4f}"
            + tx_str + no_mask_str
            + (f"  [{self._cache.loading_count()} loading]"
               if self._cache.loading_count() else ""))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._resize_timer.start(80)

    # -----------------------------------------------------------------------
    # View mode
    # -----------------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        self._view_mode = mode
        self.btn_premul.setChecked(mode == "premul")
        self.btn_overlay.setChecked(mode == "overlay")
        self._refresh()

    # -----------------------------------------------------------------------
    # Timeline click
    # -----------------------------------------------------------------------

    def _on_timeline_click(self, idx: int) -> None:
        sd = self._cur()
        if not sd or not sd.plate_files:
            return
        self.plate_idx = max(0, min(len(sd.plate_files) - 1, idx))
        self._lock = True
        self.frame_spin.setValue(self.plate_idx)
        self._lock = False
        self._preload_local()
        self._refresh()

    # -----------------------------------------------------------------------
    # Save / Export
    # -----------------------------------------------------------------------

    def _save(self) -> None:
        sd = self._cur()
        if not sd:
            return
        sd.align = AlignData(
            time_offset = self.offset_spin.value(),
            step        = self.step_spin.value(),
            plate_step  = self.plate_step_spin.value(),
            scale_x     = self.sx.value(),
            scale_y     = self.sy.value(),
            translate_x = self.tx.value(),
            translate_y = self.ty.value(),
        )
        _save_align(sd.shot_folder, sd.align)
        self.statusBar().showMessage(
            f"Saved  {sd.shotcode}  ->  masks/{ALIGN_JSON}")

    def _export(self) -> None:
        sd = self._cur()
        if not sd or not sd.plate_files:
            self.statusBar().showMessage("No sequence loaded")
            return

        version = _next_version(sd.shot_folder)
        png_dir = sd.shot_folder / "talent" / version / "png"
        png_dir.mkdir(parents=True, exist_ok=True)
        sx, sy  = self.sx.value(), self.sy.value()
        tx, ty  = self.tx.value(), self.ty.value()

        nd = self._n_display(sd)
        for label, pidx in [("first", 0), ("last", nd - 1)]:
            pp  = self._plate_file(pidx)
            if pp is None:
                continue
            mp  = self._mask_path(pidx)
            fn  = _fnum(pp)
            self.statusBar().showMessage(
                f"Rendering {label} frame {fn:04d}  (4K)...")
            QApplication.processEvents()
            rgba = render_rgba_4k(pp, mp, sx, sy, tx, ty)
            out  = png_dir / f"{sd.shotcode}_talent_{version}.{fn:04d}.png"
            rgba.save(out, compress_level=2)

        self.statusBar().showMessage(
            f"Exported first+last ->  talent/{version}/png/  ({sd.shotcode})")

    def _batch_export(self) -> None:
        """Export first+last RGBA 4K PNGs for every shot using saved alignment."""
        results = []
        total   = len([s for s in self.shots if s.plate_files])
        done    = 0

        for sd in self.shots:
            if not sd.plate_files:
                results.append(f"  SKIP  {sd.shotcode:<22}  no plate")
                continue

            a   = sd.align
            ps  = max(1, a.plate_step)
            nd  = max(1, len(sd.plate_files) // ps)

            version = _next_version(sd.shot_folder)
            png_dir = sd.shot_folder / "talent" / version / "png"
            png_dir.mkdir(parents=True, exist_ok=True)

            written = []
            for label, pidx in [("first", 0), ("last", nd - 1)]:
                file_idx = pidx * ps
                if not (0 <= file_idx < len(sd.plate_files)):
                    continue
                pp = sd.plate_files[file_idx]
                fn = _fnum(pp)

                # resolve mask using saved alignment (not UI spinboxes)
                midx = pidx * a.step + a.time_offset
                mp   = sd.mask_files[midx] if (sd.mask_files and 0 <= midx < len(sd.mask_files)) else None

                done += 1
                self.statusBar().showMessage(
                    f"[{done}/{total*2}]  {sd.shotcode}  {label}  frame {fn:04d} ...")
                QApplication.processEvents()

                rgba = render_rgba_4k(pp, mp, a.scale_x, a.scale_y,
                                      a.translate_x, a.translate_y)
                out  = png_dir / f"{sd.shotcode}_talent_{version}.{fn:04d}.png"
                rgba.save(out, compress_level=2)
                written.append(out.name)

            mask_note = f"mask OK" if sd.mask_files else "NO MASK (solid alpha)"
            results.append(
                f"  {sd.shotcode:<22}  {version}  {mask_note}  -> {', '.join(written)}")

        # Print summary to stdout and status bar
        print("\n=== Batch Export ===")
        for r in results:
            print(r)
        self.statusBar().showMessage(
            f"Batch export done  —  {len([r for r in results if 'SKIP' not in r])} shots  "
            f"(see console for details)")

    def _batch_export_full(self) -> None:
        """Launch background thread: all RGBA frames + over-black MP4 per shot."""
        if hasattr(self, "_export_thread") and self._export_thread.isRunning():
            self.statusBar().showMessage(
                "Export already running — wait for it to finish")
            return
        self._export_thread = _FullExportThread(self.shots)
        self._export_thread.progress.connect(self.statusBar().showMessage)
        self._export_thread.console.connect(print)
        self._export_thread.done.connect(self._on_export_done)
        self._export_thread.start()
        self.statusBar().showMessage(
            "Full batch export started  (running in background — UI stays live)...")

    def _on_export_done(self, n_shots: int) -> None:
        self.statusBar().showMessage(
            f"Full batch export complete  —  {n_shots} shots  (see console)")


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--shot", metavar="SHOTCODE")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    win = AlignWindow(start_shot=args.shot)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()



def launch() -> "QMainWindow | None":
    """Called by the launcher. Returns the roto align window."""
    from PySide6.QtWidgets import QApplication
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    win = AlignWindow()
    return win

