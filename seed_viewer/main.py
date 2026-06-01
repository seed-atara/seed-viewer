"""
main.py — Seed Viewer launcher.

Shows a tool selector on startup. Each tool opens in its own window.
Settings are configured via the gear icon (saved to ~/.seed-viewer.env).

To add a new tool: add an entry to TOOLS below. Rebuild not required for dev
testing — run `python -m seed_viewer.main` directly from source.

To release to freelancers:
    git tag v0.2.0 && git push origin v0.2.0
    → GitHub Actions builds Mac + Windows automatically.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import threading

# On Windows, PyInstaller --noconsole builds have no console at all.
# Every subprocess call to a console app (ffmpeg.exe) then causes Windows
# to spawn a new console window for it. Fix: allocate one hidden console
# at startup — all subprocesses inherit it silently.
if sys.platform == "win32" and getattr(sys, "frozen", False):
    import ctypes
    ctypes.windll.kernel32.AllocConsole()
    _hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if _hwnd:
        ctypes.windll.user32.ShowWindow(_hwnd, 0)  # SW_HIDE = 0

# Write a startup diagnostic log to help debug path/ffmpeg issues.
def _write_debug_log() -> None:
    import tempfile, traceback
    log_path = os.path.join(tempfile.gettempdir(), "seed_viewer_debug.txt")
    lines = [f"seed-viewer startup diagnostic", f"frozen={getattr(sys, 'frozen', False)}", ""]
    try:
        from seed_viewer.paths import SHOTS_ROOT, FFMPEG_EXE, config_summary, find_shot_folder
        cfg = config_summary()
        lines += [f"drive_root : {cfg['drive_root']}",
                  f"shots_root : {cfg['shots_root']}",
                  f"shots_exist: {cfg['shots_exist']}",
                  f"db_found   : {cfg['db_found']}",
                  f"ffmpeg     : {cfg['ffmpeg']}", ""]
        # Test one shot lookup
        test_shot = "999_TRL_1060"
        sf = find_shot_folder(test_shot)
        lines.append(f"find_shot_folder({test_shot!r}) = {sf}")
        if sf:
            children = [p.name for p in sf.iterdir()][:10]
            lines.append(f"  contents: {children}")
        lines.append("")
        # Test ffmpeg
        import subprocess
        r = subprocess.run([cfg["ffmpeg"], "-version"],
                          capture_output=True, text=True, timeout=10)
        lines.append(f"ffmpeg -version exit={r.returncode}")
        lines.append(r.stdout.splitlines()[0] if r.stdout else r.stderr[:200])
    except Exception:
        lines.append(traceback.format_exc())
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass

_write_debug_log()
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

HERE = Path(__file__).parent

# ── Tool registry ──────────────────────────────────────────────────────────────
# Each entry is either a "module" tool (opens a Qt window via launch())
# or a "cli" tool (runs a subprocess with configurable args via CliForm).
#
# To add a Qt-window tool:
#   {"kind":"module", "key":"...", "label":"...", "desc":"...",
#    "module":"seed_viewer.your_module", "fn":"launch", "accent":"#HEX"}
#
# To add a CLI tool (beeble, magnific, etc.):
#   {"kind":"cli", "key":"...", "label":"...", "desc":"...",
#    "cmd":["python", "-m", "seed_viewer.cli.beeble_submit"],
#    "args": [
#        {"flag":"--shot",  "label":"Shot",  "placeholder":"081_PTY_1380"},
#        {"flag":"--force", "label":"Force", "type":"bool"},
#    ],
#    "accent":"#HEX"}

TOOLS: list[dict] = [
    {
        "kind":    "module",
        "key":     "viewer",
        "label":   "Shot Viewer",
        "desc":    "Contact sheet · Wipe A/B compare · Playback",
        "module":  "seed_viewer.viewer",
        "fn":      "launch",
        "accent":  "#3A86FF",
    },
    {
        "kind":    "module",
        "key":     "roto_align",
        "label":   "Roto Align",
        "desc":    "Align masks to plates · Export RGBA talent",
        "module":  "seed_viewer.roto_align",
        "fn":      "launch",
        "accent":  "#FF6B6B",
    },
    # ── CLI tools (add more below as they become available) ────────────────────
    # {
    #     "kind":    "cli",
    #     "key":     "beeble",
    #     "label":   "Beeble Submit",
    #     "desc":    "Submit BG removal job to Beeble",
    #     "cmd":     ["python", "-m", "seed_viewer.cli.beeble_submit"],
    #     "args": [
    #         {"flag": "--shot",  "label": "Shot code",  "placeholder": "081_PTY_1380"},
    #         {"flag": "--force", "label": "Force redo",  "type": "bool"},
    #     ],
    #     "accent":  "#06D6A0",
    # },
]

# ── Palette ────────────────────────────────────────────────────────────────────

BG       = "#141414"
CARD_BG  = "#1E1E1E"
CARD_BRD = "#2A2A2A"
TEXT_PRI = "#F0F0F0"
TEXT_SEC = "#888888"
OK_COL   = "#06D6A0"
ERR_COL  = "#FF6B6B"


def _apply_palette(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window,          QColor(BG))
    pal.setColor(QPalette.WindowText,      QColor(TEXT_PRI))
    pal.setColor(QPalette.Base,            QColor(CARD_BG))
    pal.setColor(QPalette.AlternateBase,   QColor("#252525"))
    pal.setColor(QPalette.Text,            QColor(TEXT_PRI))
    pal.setColor(QPalette.Button,          QColor(CARD_BG))
    pal.setColor(QPalette.ButtonText,      QColor(TEXT_PRI))
    pal.setColor(QPalette.Highlight,       QColor("#3A86FF"))
    pal.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    app.setPalette(pal)


# ── Settings dialog ────────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    """Configure drive paths. Saves to ~/.seed-viewer.env."""

    ENV_VARS = [
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
            edit.setStyleSheet(f"background: {CARD_BG}; color: {TEXT_PRI}; "
                               f"border: 1px solid {CARD_BRD}; border-radius: 4px; padding: 4px;")
            form.addRow(f"{label}:", edit)
            self._fields[key] = edit

        note = QLabel("Changes take effect on next launch.\n"
                       "Saved to ~/.seed-viewer.env")
        note.setStyleSheet(f"color: {TEXT_SEC}; font-size: 11px;")

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
        vbox.addWidget(btns)

    def _save(self) -> None:
        env_path = Path.home() / ".seed-viewer.env"
        lines = ["# Seed Viewer — path configuration (auto-generated)"]
        for key, label, tip, _ in self.ENV_VARS:
            val = self._fields[key].text().strip()
            lines.append(f"# {tip}")
            lines.append(f"{key}={val}")
            lines.append("")
        env_path.write_text("\n".join(lines), encoding="utf-8")
        self.accept()


# ── CLI tool runner ────────────────────────────────────────────────────────────

class _Emitter(QObject):
    line = Signal(str)


class CliRunner(QDialog):
    """
    Generic form + live output console for CLI tools.
    Builds a command line from the tool's 'args' spec and runs it
    in a background thread, streaming stdout/stderr to the console.
    """

    def __init__(self, tool: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tool["label"])
        self.setMinimumSize(560, 420)
        self._tool    = tool
        self._proc    = None
        self._fields: dict[str, QLineEdit | QPushButton] = {}
        self._emit    = _Emitter()
        self._emit.line.connect(self._append_line)

        # Form
        form = QFormLayout()
        form.setSpacing(10)
        for arg in tool.get("args", []):
            if arg.get("type") == "bool":
                btn = QPushButton(arg["label"])
                btn.setCheckable(True)
                btn.setStyleSheet(f"background: {CARD_BG}; color: {TEXT_SEC}; "
                                   f"border: 1px solid {CARD_BRD}; border-radius: 4px; padding: 4px 10px;")
                btn.toggled.connect(lambda checked, b=btn: b.setStyleSheet(
                    f"background: {tool['accent']}; color: white; border: none; border-radius: 4px; padding: 4px 10px;"
                    if checked else
                    f"background: {CARD_BG}; color: {TEXT_SEC}; border: 1px solid {CARD_BRD}; border-radius: 4px; padding: 4px 10px;"
                ))
                form.addRow(f"{arg['label']}:", btn)
                self._fields[arg["flag"]] = btn
            else:
                edit = QLineEdit()
                edit.setPlaceholderText(arg.get("placeholder", ""))
                edit.setStyleSheet(f"background: {CARD_BG}; color: {TEXT_PRI}; "
                                   f"border: 1px solid {CARD_BRD}; border-radius: 4px; padding: 4px;")
                form.addRow(f"{arg.get('label', arg['flag'])}:", edit)
                self._fields[arg["flag"]] = edit

        # Console
        self._console = QPlainTextEdit()
        self._console.setReadOnly(True)
        self._console.setStyleSheet(
            f"background: #0A0A0A; color: #CCCCCC; "
            f"font-family: 'Iosevka Term', 'Courier New', monospace; font-size: 12px; "
            f"border: 1px solid {CARD_BRD}; border-radius: 4px;"
        )
        self._console.setMinimumHeight(200)

        # Buttons
        self._run_btn = QPushButton("Run")
        self._run_btn.setStyleSheet(
            f"background: {tool['accent']}; color: white; border: none; "
            f"border-radius: 4px; padding: 8px 20px; font-weight: bold;"
        )
        self._run_btn.clicked.connect(self._run)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet(
            f"background: {CARD_BG}; color: {TEXT_SEC}; border: 1px solid {CARD_BRD}; "
            f"border-radius: 4px; padding: 8px 20px;"
        )
        self._stop_btn.clicked.connect(self._stop)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._run_btn)
        btn_row.addWidget(self._stop_btn)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(20, 16, 20, 16)
        vbox.setSpacing(12)
        vbox.addLayout(form)
        vbox.addWidget(self._console)
        vbox.addLayout(btn_row)

    def _build_cmd(self) -> list[str]:
        cmd = list(self._tool["cmd"])
        for flag, widget in self._fields.items():
            if isinstance(widget, QPushButton):
                if widget.isChecked():
                    cmd.append(flag)
            else:
                val = widget.text().strip()
                if val:
                    cmd.extend([flag, val])
        return cmd

    def _run(self) -> None:
        cmd = self._build_cmd()
        self._console.clear()
        self._console.appendPlainText("$ " + " ".join(cmd))
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        def _worker():
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                for line in self._proc.stdout:
                    self._emit.line.emit(line.rstrip())
                self._proc.wait()
                self._emit.line.emit(f"\n[exit {self._proc.returncode}]")
            except Exception as e:
                self._emit.line.emit(f"ERROR: {e}")
            finally:
                self._emit.line.emit("")
                self._run_btn.setEnabled(True)
                self._stop_btn.setEnabled(False)

        threading.Thread(target=_worker, daemon=True).start()

    def _stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def _append_line(self, text: str) -> None:
        self._console.appendPlainText(text)
        self._console.verticalScrollBar().setValue(
            self._console.verticalScrollBar().maximum()
        )


# ── Tool card ──────────────────────────────────────────────────────────────────

class ToolCard(QFrame):
    def __init__(self, tool: dict, parent=None):
        super().__init__(parent)
        self._tool   = tool
        self._window = None

        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(f"""
            ToolCard {{
                background: {CARD_BG};
                border: 1px solid {CARD_BRD};
                border-radius: 8px;
            }}
            ToolCard:hover {{
                border: 1px solid {tool['accent']};
            }}
        """)

        bar = QFrame()
        bar.setFixedWidth(4)
        bar.setStyleSheet(f"background: {tool['accent']}; border-radius: 2px;")

        name_lbl = QLabel(tool["label"])
        name_lbl.setFont(QFont("", 14, QFont.Bold))
        name_lbl.setStyleSheet(f"color: {TEXT_PRI}; border: none; background: transparent;")

        kind_tag = "App" if tool.get("kind") == "module" else "CLI"
        desc_lbl = QLabel(f"{tool['desc']}")
        desc_lbl.setStyleSheet(f"color: {TEXT_SEC}; border: none; background: transparent;")
        desc_lbl.setWordWrap(True)

        btn = QPushButton("Open")
        btn.setFixedWidth(80)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {tool['accent']};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background: {tool['accent']}CC; }}
        """)
        btn.clicked.connect(self._open)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        text_col.addWidget(name_lbl)
        text_col.addWidget(desc_lbl)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 16, 16, 16)
        row.addWidget(bar)
        row.addSpacing(12)
        row.addLayout(text_col)
        row.addStretch()
        row.addWidget(btn)

    def _open(self) -> None:
        kind = self._tool.get("kind", "module")

        if kind == "cli":
            dlg = CliRunner(self._tool, self.window())
            dlg.show()
            return

        # module tool — open Qt window
        if self._window and self._window.isVisible():
            self._window.raise_()
            self._window.activateWindow()
            return
        try:
            mod = importlib.import_module(self._tool["module"])
            fn  = getattr(mod, self._tool["fn"])
            self._window = fn()
            if self._window:
                self._window.show()
        except ImportError as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Import error",
                                 f"Could not load {self._tool['module']}:\n{e}")


# ── Launcher window ────────────────────────────────────────────────────────────

class LauncherWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Seed Viewer")
        self.setMinimumWidth(420)

        from seed_viewer.paths import config_summary
        cfg = config_summary()

        # Header
        title = QLabel("Seed Viewer")
        title.setFont(QFont("", 22, QFont.Bold))
        title.setStyleSheet(f"color: {TEXT_PRI};")

        ok = cfg["shots_exist"] and cfg["db_found"]
        status_parts = []
        if not cfg["shots_exist"]:
            status_parts.append("Shots root not found — mount drive or check settings")
        if not cfg["db_found"]:
            status_parts.append("shot_database.json not found on drive")
        status_text = "  ·  ".join(status_parts) if status_parts else cfg["shots_root"]
        status = QLabel(status_text)
        status.setStyleSheet(f"color: {OK_COL if ok else ERR_COL}; font-size: 11px;")
        status.setWordWrap(True)

        # Gear / settings button
        gear = QPushButton("⚙  Settings")
        gear.setStyleSheet(
            f"background: {CARD_BG}; color: {TEXT_SEC}; "
            f"border: 1px solid {CARD_BRD}; border-radius: 4px; padding: 4px 12px;"
        )
        gear.clicked.connect(self._open_settings)

        header_row = QHBoxLayout()
        header_row.addWidget(title)
        header_row.addStretch()
        header_row.addWidget(gear)

        header = QVBoxLayout()
        header.setSpacing(4)
        header.addLayout(header_row)
        header.addWidget(status)

        # Tool cards
        cards_col = QVBoxLayout()
        cards_col.setSpacing(10)
        for tool in TOOLS:
            cards_col.addWidget(ToolCard(tool))
        cards_col.addStretch()

        cards_w = QWidget()
        cards_w.setLayout(cards_col)

        scroll = QScrollArea()
        scroll.setWidget(cards_w)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        root = QVBoxLayout()
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)
        root.addLayout(header)
        root.addWidget(scroll)

        cw = QWidget()
        cw.setLayout(root)
        self.setCentralWidget(cw)
        self.resize(440, len(TOOLS) * 110 + 160)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Settings saved",
                                    "Paths saved. Restart the viewer for changes to take effect.")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    app = QApplication.instance() or QApplication(sys.argv)
    _apply_palette(app)
    win = LauncherWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
