"""
main.py — Seed Viewer entry point.

Shows the SEED BRIDGE console on startup (seed_console.BridgeWindow) — every
station opens in its own window. Settings are configured via the gear icon
(saved to ~/.seed-viewer.env).

To add a new station: add an entry to seed_console.py's MODES (seed-film).
Rebuild not required for dev testing — run `python -m seed_viewer.main`
directly from source.

To release to freelancers:
    git tag v0.2.0 && git push origin v0.2.0
    → GitHub Actions builds Mac + Windows automatically.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile

# On Windows, PyInstaller --noconsole builds have no console at all.
# Every subprocess call to a console app (ffmpeg.exe) then causes Windows
# to spawn a new console window for it. Fix: allocate one hidden console
# at startup — all subprocesses inherit it silently.
# NOTE: skip this in --mcp mode — allocating a console would steal the stdin/stdout
# PIPE that Claude connected for the MCP stdio transport (the server would then read
# the hidden console instead of Claude, i.e. "not running").
_MCP_MODE = (len(sys.argv) >= 2 and sys.argv[1] == "--mcp")
if sys.platform == "win32" and getattr(sys, "frozen", False) and not _MCP_MODE:
    import ctypes
    ctypes.windll.kernel32.AllocConsole()
    _hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if _hwnd:
        ctypes.windll.user32.ShowWindow(_hwnd, 0)  # SW_HIDE = 0

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QVBoxLayout,
)

HERE = Path(__file__).parent


def _run_tool(modname: str, args: list[str]) -> None:
    """Dispatch target for the frozen exe self-invoking a bundled CLI tool
    (SeedViewer.exe --tool beeble_submit --shot ...). Runs it in-process."""
    # importing paths loads ~/.seed-viewer.env into os.environ, so the tool sees
    # SF_* paths + API keys (BEEBLE_API_KEY, etc.) the artist configured.
    try:
        from seed_viewer import paths as _paths  # noqa: F401
    except Exception:
        pass
    sys.path.insert(0, str(HERE))
    sys.argv = [modname] + list(args)
    mod = importlib.import_module(f"seed_viewer.{modname}")
    mod.main()


# ── Palette — SEED design system (single source of truth: seed_theme) ─────────
try:
    import seed_theme as _theme          # bundled next to the other pipeline modules
    from seed_theme import C as _C
    BG, CARD_BG, CARD_BRD = _C.BG0, _C.BG1, _C.STROKE
    TEXT_PRI, TEXT_SEC = _C.TEXT, _C.TEXT_MUT
    OK_COL, ERR_COL = _C.ACCENT, _C.WARN
    CY, AM = _C.ACCENT, _C.WARN
    ON_ACCENT = _C.ON_ACCENT
except Exception:                        # dev checkout before prepare_sources ran
    _theme = None
    BG, CARD_BG, CARD_BRD = "#0d1117", "#151b23", "#2b3743"
    TEXT_PRI, TEXT_SEC = "#e6edf3", "#94a3b1"
    OK_COL, ERR_COL, CY, AM = "#22d3ee", "#fbbf24", "#22d3ee", "#fbbf24"
    ON_ACCENT = "#06181d"


def _apply_palette(app: QApplication) -> None:
    if _theme is not None:
        _theme.apply(app)                # Fusion + palette + font + master QSS
        return
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window,          QColor(BG))
    pal.setColor(QPalette.WindowText,      QColor(TEXT_PRI))
    pal.setColor(QPalette.Base,            QColor(CARD_BG))
    pal.setColor(QPalette.AlternateBase,   QColor(CARD_BG))
    pal.setColor(QPalette.Text,            QColor(TEXT_PRI))
    pal.setColor(QPalette.Button,          QColor(CARD_BG))
    pal.setColor(QPalette.ButtonText,      QColor(TEXT_PRI))
    pal.setColor(QPalette.Highlight,       QColor(CY))
    pal.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    app.setPalette(pal)


# ── Settings dialog ────────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    """Configure drive paths. Saves to ~/.seed-viewer.env."""

    # Beeble/Anthropic keys are NOT configured here: they ship bundled with the app
    # (seed_beeble_key.py / seed_anthropic_key.py, written from CI secrets at build
    # time), so a manual field here was both redundant and a footgun, a leftover or
    # mistyped value in ~/.seed-viewer.env would silently override the working
    # bundled key with no way for an artist to know why a tool stopped authenticating.
    ENV_VARS = [
        ("SF_USER",        "User",        "Your username (gates tools like Beeble to permitted users)",
         "sholto"),
        ("SF_DRIVE_ROOT",  "Drive root",  "Path to the mounted project drive root",
         "G:/Shared drives/SATOSHI_DRIVE/SATOSHI  or  /Volumes/SATOSHI_DRIVE/SATOSHI"),
        ("SF_SHOTS_ROOT",  "Shots root",  "Root of all shot folders (leave blank = drive root/_SHOTS)",
         ""),
        ("SF_FFMPEG_EXE",  "FFmpeg path", "Full path to ffmpeg binary (leave blank = use bundled)",
         ""),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        self._fields: dict[str, QLineEdit] = {}

        env_path = Path.home() / ".seed-viewer.env"
        current: dict[str, str] = {}
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    current[k.strip()] = v.strip()

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(12)

        for key, label, tip, placeholder in self.ENV_VARS:
            edit = QLineEdit()
            edit.setText(current.get(key, ""))
            edit.setPlaceholderText(placeholder)
            edit.setToolTip(tip)
            if key.endswith(("_API_KEY", "_SK", "_SECRET", "_TOKEN")):
                edit.setEchoMode(QLineEdit.Password)   # never show secrets in the clear
            form.addRow(f"{label}:", edit)
            self._fields[key] = edit

        note = QLabel("Changes take effect on next launch.\n"
                       "Saved to ~/.seed-viewer.env")
        note.setStyleSheet(f"color: {TEXT_SEC}; font-size: 11px;")

        connect_btn = QPushButton("🔌  Connect to Claude (pipeline MCP)")
        connect_btn.setToolTip("Register this app as an MCP server so your Claude (Desktop / Code) "
                               "can drive the pipeline. Set your User above and Save first.")
        connect_btn.setStyleSheet(f"background: {CARD_BG}; color: {OK_COL}; "
                                  f"border: 1px solid {OK_COL}; border-radius: 4px; padding: 7px 14px;")
        connect_btn.clicked.connect(self._connect_claude)

        reset_btn = QPushButton("🧹  Reset local cache / start clean…")
        reset_btn.setToolTip("Clear this machine's caches, thumbnails, and logs — for a fresh "
                             "install, a new freelancer, or troubleshooting stuck local state.")
        reset_btn.setStyleSheet(f"background: {CARD_BG}; color: {TEXT_PRI}; "
                                f"border: 1px solid {CARD_BRD}; border-radius: 4px; padding: 7px 14px;")
        reset_btn.clicked.connect(self._open_reset_dialog)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        for btn in btns.buttons():
            btn.setStyleSheet(f"background: {CARD_BG}; color: {TEXT_PRI}; "
                               f"border: 1px solid {CARD_BRD}; border-radius: 4px; padding: 6px 16px;")

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(24, 20, 24, 20)
        vbox.setSpacing(16)
        vbox.addLayout(form)
        vbox.addWidget(note)
        vbox.addWidget(connect_btn)
        vbox.addWidget(reset_btn)
        vbox.addWidget(btns)

    def _open_reset_dialog(self) -> None:
        ResetStateDialog(self).exec()

    def _connect_claude(self) -> None:
        """Register SeedViewer as an MCP server in the artist's Claude Desktop config, and
        show the Claude Code one-liner. The server runs via `SeedViewer.exe --mcp`."""
        import json
        import platform
        if getattr(sys, "frozen", False):
            command, args, cwd = sys.executable, ["--mcp"], None
        else:                                   # dev: re-invoke the launcher module
            command, args = sys.executable, ["-m", "seed_viewer.main", "--mcp"]
            cwd = str(Path(__file__).resolve().parent.parent)
        home = Path.home(); sysname = platform.system()
        if sysname == "Windows":
            cfg = Path(os.environ.get("APPDATA", str(home / "AppData/Roaming"))) / "Claude" / "claude_desktop_config.json"
        elif sysname == "Darwin":
            cfg = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        else:
            cfg = home / ".config" / "Claude" / "claude_desktop_config.json"
        data = {}
        if cfg.exists():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        entry = {"command": command, "args": args}
        if cwd:
            entry["cwd"] = cwd
        data.setdefault("mcpServers", {})["seed-pipeline"] = entry
        try:
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "Connect to Claude", f"Couldn't write config:\n{e}")
            return
        code_line = "claude mcp add seed-pipeline -- " + " ".join([command] + args)
        QMessageBox.information(
            self, "Connected to Claude",
            f"Added 'seed-pipeline' to Claude Desktop:\n{cfg}\n\n"
            f"Restart Claude Desktop to load it, then ask it “what shots am I assigned?”\n\n"
            f"For Claude Code instead, run:\n{code_line}")

    def _save(self) -> None:
        # MERGE with whatever is already in the file rather than overwrite it wholesale.
        # ~/.seed-viewer.env is shared with the Artist Hub's own Setup dialog (different
        # keys: SF_HUB_API/SF_SCRATCH/SF_SILO_CACHE_MIB/SF_COMFY_URL) and with manually
        # added keys (SEED_ARK_AK/SEED_ARK_SK). A naive full rewrite here discards
        # whatever the OTHER dialog last saved, and vice versa, which looked to artists
        # like "my settings keep reverting to defaults" after using both screens.
        env_path = Path.home() / ".seed-viewer.env"
        current: dict[str, str] = {}
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    current[k.strip()] = v.strip()
        for key, label, tip, _ in self.ENV_VARS:
            current[key] = self._fields[key].text().strip()
        tips = {key: tip for key, label, tip, _ in self.ENV_VARS}
        lines = ["# Seed Viewer — path configuration (auto-generated)"]
        for key, val in current.items():
            if key in tips:
                lines.append(f"# {tips[key]}")
            lines.append(f"{key}={val}")
            lines.append("")
        env_path.write_text("\n".join(lines), encoding="utf-8")
        self.accept()


def _dir_size(p: Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def _clean_state_targets() -> list[tuple[str, Path]]:
    """Every local cache/log/temp path that is safe to delete unconditionally: pure
    derived data the app rebuilds on demand. Deliberately excludes checkout sandboxes
    (SF_SCRATCH/work/*), generated render output, saved cinematic looks, and any local
    pipeline_state.db fallback — those can hold real, unsynced artist work and must
    never be part of a one-click wipe."""
    home = Path.home()
    temp = Path(tempfile.gettempdir())
    scratch = Path(os.environ.get("SF_SCRATCH") or (temp / "seed_film"))
    candidates = [
        ("Local media cache", scratch / "cache"),
        ("Viewer thumbnail cache", temp / "ks_shot_viewer"),
        ("Studio thumbnail cache", home / ".seed_studio_thumbs"),
        ("ARK asset lookup index", home / ".seed_ark_cache.json"),
        ("PIP daily usage counter", home / ".seed_pip_usage.json"),
        ("Viewer log", temp / "seed_viewer.log"),
        ("Studio log", temp / "seed_studio.log"),
        ("Crash log", home / "seedstudio_crash.log"),
        ("Debug test frame", temp / "sv_test_frame.jpg"),
        ("Updater staging log", temp / "seedviewer_update.log"),
        ("Updater staging script", temp / "seedviewer_update.bat"),
        ("Updater staging script", temp / "seedviewer_update.sh"),
        ("Local login secret (regenerates automatically)", HERE / ".hub_secret"),
    ]
    candidates += [("Cinematic preview frame", p) for p in temp.glob("_cine_*_f0.png")]
    return [(label, p) for label, p in candidates if p.exists()]


class ResetStateDialog(QDialog):
    """Lists every safe-to-delete local cache/log/temp file and clears them on
    confirm — for a fresh install, handing a machine to a new freelancer, or
    troubleshooting local state that's stuck or corrupted. Never touches checkout
    sandboxes, generated output, saved looks, or a local state database; those can
    hold real work. Settings (~/.seed-viewer.env) is opt-in via its own checkbox
    since clearing it means re-running Setup."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reset local cache")
        self.setMinimumWidth(520)
        self._targets = _clean_state_targets()

        v = QVBoxLayout(self)
        total = sum((_dir_size(p) if p.is_dir() else p.stat().st_size) for _, p in self._targets)
        v.addWidget(QLabel(f"This will delete {len(self._targets)} local cache/log "
                           f"item(s), about {total / 1e6:.1f} MB:"))

        lst = QListWidget()
        for label, p in self._targets:
            lst.addItem(f"{label}  —  {p}")
        lst.setMaximumHeight(220)
        v.addWidget(lst)

        self._also_settings = QCheckBox("Also reset Settings (User / Drive root / Shots "
                                        "root / FFmpeg path) — you'll need to run Setup again")
        v.addWidget(self._also_settings)

        note = QLabel("NOT touched: any in-progress checkout sandboxes, your generated "
                      "renders, and saved cinematic looks — those can hold real work.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_SEC}; font-size: 11px;")
        v.addWidget(note)

        btns = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Yes).setText("Delete these")
        for btn in btns.buttons():
            btn.setStyleSheet(f"background: {CARD_BG}; color: {TEXT_PRI}; "
                               f"border: 1px solid {CARD_BRD}; border-radius: 4px; padding: 6px 16px;")
        btns.accepted.connect(self._do_reset)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _do_reset(self) -> None:
        errors = []
        for label, p in self._targets:
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
            except OSError as e:
                errors.append(f"{label}: {e}")
        if self._also_settings.isChecked():
            env_path = Path.home() / ".seed-viewer.env"
            try:
                if env_path.exists():
                    env_path.unlink()
            except OSError as e:
                errors.append(f"Settings: {e}")
        if errors:
            QMessageBox.warning(self, "Reset local cache",
                                "Cleared most items, but some failed:\n" + "\n".join(errors))
        else:
            msg = "Local cache cleared."
            if self._also_settings.isChecked():
                msg += " Settings were reset too, run Setup again before using the app."
            QMessageBox.information(self, "Reset local cache", msg)
        self.accept()


# ── Entry point ────────────────────────────────────────────────────────────────

def _show_debug_dialog(app: "QApplication") -> None:
    import traceback, subprocess as _sp, tempfile
    from PySide6.QtWidgets import QMessageBox
    from pathlib import Path
    lines = []
    try:
        from seed_viewer.paths import config_summary, find_shot_folder
        cfg = config_summary()
        sf = find_shot_folder("999_TRL_1060")

        # Find a source file (first PNG or mp4)
        src = None
        for pat in ["firstframe/*.png", "mp4/*_4k.mp4", "mp4/*.mp4"]:
            hits = sorted(sf.glob(pat)) if sf else []
            if hits:
                src = hits[0]
                lines.append(f"source: {src.name}  ({pat})")
                break
        if not src:
            lines.append("source: NONE FOUND")

        # Test PIL open
        if src and src.suffix.lower() == ".png":
            try:
                from PIL import Image
                img = Image.open(src)
                lines.append(f"PIL.Image.open: OK  {img.size} {img.mode}")
            except Exception as e:
                lines.append(f"PIL.Image.open FAILED: {e}")

        # Test ffmpeg frame extract
        if src and src.suffix.lower() == ".mp4":
            try:
                from PIL import Image
                out = Path(tempfile.gettempdir()) / "sv_test_frame.jpg"
                r = _sp.run([cfg["ffmpeg"], "-y", "-i", str(src), "-vframes", "1", "-q:v", "3", str(out)],
                            capture_output=True, timeout=30)
                if out.exists():
                    img = Image.open(out)
                    lines.append(f"ffmpeg extract+PIL: OK  {img.size}")
                else:
                    lines.append(f"ffmpeg extract: NO OUTPUT  exit={r.returncode}")
                    lines.append(r.stderr[-300:] if r.stderr else "(no stderr)")
            except Exception as e:
                lines.append(f"ffmpeg extract FAILED: {e}")

    except Exception:
        lines.append(traceback.format_exc())

    msg = QMessageBox()
    msg.setWindowTitle("Seed Viewer — Thumbnail Debug")
    msg.setText("\n".join(lines) or "(no output)")
    msg.exec()


class _UpdateChecker(QThread):
    """Background check: GitHub latest tag vs the baked-in build version."""
    found = Signal(str)

    def run(self):
        try:
            from seed_viewer import updater
            v = updater.check()
            if v:
                self.found.emit(v)
        except Exception:
            pass


def _prompt_update(win, latest):
    from PySide6.QtWidgets import QMessageBox, QApplication
    from seed_viewer import updater
    cur = updater.installed_version()
    if QMessageBox.question(
            win, "Update available",
            f"A newer Seed Viewer is available.\n\n"
            f"Installed:  {cur}\nLatest:     {latest}\n\n"
            "Update now? The app will close, update, and relaunch.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes:
        updater.run_update_and_quit(QApplication.instance())


def _run_mcp() -> None:
    """Run the pipeline MCP server over stdio (no Qt). The bundled exe is launched as
    `SeedViewer.exe --mcp` by the artist's Claude (Desktop / Code). Mirrors _run_tool:
    load the artist's env, put seed_viewer/ on sys.path so the bundled pipeline modules
    (hub_client, pipeline_*) resolve, then hand control to the server."""
    # A windowed PyInstaller build has no console, so the C runtime leaves sys.stdin/
    # sys.stdout unusable for the pipes Claude connected. Rebind them to the inherited
    # OS standard handles so the JSON-RPC protocol can actually read/write.
    if getattr(sys, "frozen", False):
        import io
        try:
            if sys.platform == "win32":
                import ctypes
                import msvcrt
                k32 = ctypes.windll.kernel32
                fd_in = msvcrt.open_osfhandle(k32.GetStdHandle(-10), os.O_RDONLY)   # STD_INPUT
                fd_out = msvcrt.open_osfhandle(k32.GetStdHandle(-11), 0)            # STD_OUTPUT
                sys.stdin = io.TextIOWrapper(io.FileIO(fd_in, "r"), encoding="utf-8")
                sys.stdout = io.TextIOWrapper(io.FileIO(fd_out, "w"), encoding="utf-8", newline="\n")
                try:
                    fd_err = msvcrt.open_osfhandle(k32.GetStdHandle(-12), 0)        # STD_ERROR
                    sys.stderr = io.TextIOWrapper(io.FileIO(fd_err, "w"), encoding="utf-8", newline="\n")
                except Exception:
                    pass
            else:
                sys.stdin = io.TextIOWrapper(io.FileIO(0, "r"), encoding="utf-8")
                sys.stdout = io.TextIOWrapper(io.FileIO(1, "w"), encoding="utf-8", newline="\n")
        except Exception:
            pass
    try:
        from seed_viewer import paths as _paths  # noqa: F401  (loads ~/.seed-viewer.env)
    except Exception:
        pass
    sys.path.insert(0, str(HERE))
    importlib.import_module("seed_viewer.mcp_server").main()


def _install_crash_log():
    """Capture hard crashes (incl. segfaults / fatal Qt errors) + uncaught Python exceptions
    to a log file, so 'it just crashed' becomes diagnosable."""
    import faulthandler
    from pathlib import Path
    try:
        log = Path.home() / "seedstudio_crash.log"
        _f = open(log, "a", buffering=1, encoding="utf-8")
        faulthandler.enable(file=_f)          # dumps the C stack on a fatal signal
        import datetime
        _f.write(f"\n=== session {datetime.datetime.now().isoformat(timespec='seconds')} ===\n")

        def _hook(t, v, tb):
            import traceback
            _f.write("UNCAUGHT EXCEPTION:\n")
            traceback.print_exception(t, v, tb, file=_f)
            sys.__excepthook__(t, v, tb)
        sys.excepthook = _hook
    except Exception:
        pass


def main():
    # Frozen exe self-invokes for the pipeline MCP server: SeedViewer.exe --mcp
    if len(sys.argv) >= 2 and sys.argv[1] == "--mcp":
        _run_mcp()
        return
    _install_crash_log()
    # Frozen exe self-invokes for bundled CLI tools: SeedViewer.exe --tool beeble_submit ...
    if len(sys.argv) >= 3 and sys.argv[1] == "--tool":
        _run_tool(sys.argv[2], sys.argv[3:])
        return

    app = QApplication.instance() or QApplication(sys.argv)
    # Tested and promoted 2026-07-05: the "SEED BRIDGE" console (built and proven as a
    # personal-only build first) is now the app's actual home screen, replacing the old
    # tile-grid LauncherWindow. seed_theme_v14 layers its own rules on top of seed_theme's
    # (see seed_theme_v14.apply()'s own docstring) — falls back to the plain palette if
    # unavailable for any reason, same resilience _apply_palette already had on its own.
    try:
        import seed_theme_v14 as _v14_theme
        _v14_theme.apply(app)
    except Exception:
        _apply_palette(app)
    import seed_console
    win = seed_console.launch()
    win.show()
    # auto-update: background check; prompts on the main thread if a newer build exists
    win._update_checker = _UpdateChecker()
    win._update_checker.found.connect(lambda v: _prompt_update(win, v))
    QTimer.singleShot(1500, win._update_checker.start)
    sys.exit(app.exec())


if __name__ == "__main__":
    # Must run BEFORE anything else: lets a frozen build spawn multiprocessing workers
    # (e.g. parallel clip rendering) without re-bootstrapping the whole GUI / fork-bombing.
    import multiprocessing
    multiprocessing.freeze_support()
    main()
