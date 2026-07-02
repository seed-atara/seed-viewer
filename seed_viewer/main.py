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
from typing import Callable

from PySide6.QtCore import Qt, Signal, QObject, QThread, QTimer
from PySide6.QtGui import QFont, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

HERE = Path(__file__).parent


# ── Users / permissions (gating) ────────────────────────────────────────────────

def _load_agents() -> list[dict]:
    import json
    try:
        return json.loads((HERE / "agents.json").read_text(encoding="utf-8")).get("agents", [])
    except Exception:
        return []


def _current_user() -> str:
    return (os.environ.get("SF_USER", "") or "").strip().lower()


def _user_allowed(tool: dict) -> bool:
    """Tools with no 'requires' are open to everyone. Otherwise the configured
    SF_USER must be a supervisor/producer, or list the permission in their
    agents.json 'tools'."""
    req = tool.get("requires")
    if not req:
        return True
    u = _current_user()
    for a in _load_agents():
        if a.get("name", "").lower() == u:
            if a.get("role") in ("supervisor", "producer"):
                return True
            perms = a.get("tools") or []
            return req in perms or "all" in perms
    return False


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
        "accent":  "#7fb2e5",
    },
    {
        "kind":    "module",
        "key":     "pipeline",
        "label":   "Pipeline · Checkout",
        "desc":    "Sign in · claim shots · publish versions · run tools",
        "module":  "seed_viewer.pipeline_panel",
        "fn":      "launch",
        "accent":  "#6a5fae",
    },
    # (Seed Image Edit retired as a standalone tile — it now lives as the "✦ Finish" tab
    #  inside Seed Studio. seed_image_edit.py is still bundled and imported by the studio.)
    {
        "kind":    "module",
        "key":     "pip",
        "label":   "PIP",
        "desc":    "The seed inside the fruit — chat with the entire pipeline: shots · check-ins · generate · finish · deliver",
        "module":  "seed_viewer.seed_pip",
        "fn":      "launch",
        "accent":  "#e8b33a",
    },
    {
        "kind":    "module",
        "key":     "seed_studio",
        "label":   "Seed Studio",
        "desc":    "Generate (Seedance·Seedream) + Animate (Beeble) · cached playback · auto-prompt/mask · pipeline in→out",
        "module":  "seed_viewer.seed_studio",
        "fn":      "launch",
        "accent":  "#e8b33a",
    },
]

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

    ENV_VARS = [
        ("SF_USER",        "User",        "Your username (gates tools like Beeble to permitted users)",
         "sholto"),
        ("SF_DRIVE_ROOT",  "Drive root",  "Path to the mounted project drive root",
         "G:/Shared drives/SATOSHI_DRIVE/SATOSHI  or  /Volumes/SATOSHI_DRIVE/SATOSHI"),
        ("SF_SHOTS_ROOT",  "Shots root",  "Root of all shot folders (leave blank = drive root/_SHOTS)",
         ""),
        ("SF_FFMPEG_EXE",  "FFmpeg path", "Full path to ffmpeg binary (leave blank = use bundled)",
         ""),
        ("BEEBLE_API_KEY",    "Beeble API key",    "Required to submit Beeble jobs (leave blank if not using)",
         ""),
        ("ANTHROPIC_API_KEY", "Anthropic API key", "Only needed for Beeble --auto-prompt (Claude vision)",
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
        vbox.addWidget(btns)

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
        tm = self._tool.get("tool_module")
        if tm:
            # frozen: re-invoke the exe; dev: re-invoke the launcher module
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--tool", tm]
            else:
                cmd = [sys.executable, "-m", "seed_viewer.main", "--tool", tm]
        else:
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
    """Hero tool card: big glyph, name, description, full-width Open."""
    _GLYPHS = {"viewer": "▦", "pipeline": "⛁", "seed_studio": "✦", "pip": "◉"}

    def __init__(self, tool: dict, parent=None):
        super().__init__(parent)
        self._tool   = tool
        self._window = None
        ac = tool["accent"]

        self.setFixedSize(250, 270)
        self.setStyleSheet(f"""
            ToolCard {{
                background: {CARD_BG};
                border: 1px solid {CARD_BRD};
                border-radius: 14px;
            }}
            ToolCard:hover {{ border: 1px solid {ac}; }}
        """)

        glyph = QLabel(self._GLYPHS.get(tool.get("key", ""), "◇"))
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setStyleSheet(f"color: {ac}; font-size: 34pt; background: transparent; border: none;")

        name_lbl = QLabel(tool["label"])
        name_lbl.setAlignment(Qt.AlignCenter)
        name_lbl.setStyleSheet(f"color: {TEXT_PRI}; font-size: 13pt; font-weight: 700; "
                               "background: transparent; border: none;")

        desc_lbl = QLabel(tool["desc"])
        desc_lbl.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"color: {TEXT_SEC}; font-size: 9pt; "
                               "background: transparent; border: none;")

        btn = QPushButton("Open")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(38)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {ac};
                color: {ON_ACCENT};
                border: none;
                border-radius: 8px;
                font-weight: bold;
                font-size: 11pt;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{ background: {ac}CC; }}
        """)
        btn.clicked.connect(self._open)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 22, 18, 18)
        lay.setSpacing(10)
        lay.addWidget(glyph)
        lay.addWidget(name_lbl)
        lay.addWidget(desc_lbl, 1)
        lay.addWidget(btn)

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
        self.setWindowTitle("SEEDSTUDIO")

        from seed_viewer.paths import config_summary
        cfg = config_summary()
        from seed_viewer import updater
        _ver = updater.installed_version()
        _vtext = _ver if str(_ver).startswith("v") else f"v{_ver}"
        self.setWindowTitle(f"SEEDSTUDIO {_vtext}")

        # ── top utility row (version left · settings right) ──
        ver_lbl = QLabel(_vtext)
        ver_lbl.setStyleSheet(f"color: {TEXT_SEC}; font-size: 10pt;")
        gear = QPushButton("⚙  Settings")
        gear.setCursor(Qt.PointingHandCursor)
        gear.setStyleSheet(f"background: transparent; color: {TEXT_SEC}; "
                           f"border: 1px solid {CARD_BRD}; border-radius: 6px; padding: 6px 14px;")
        gear.clicked.connect(self._open_settings)
        top = QHBoxLayout()
        top.addWidget(ver_lbl)
        top.addStretch()
        top.addWidget(gear)

        # ── hero brand block, centered ──
        title = QLabel("◢ SEEDSTUDIO")
        _tf = QFont("", 30, QFont.Black)
        _tf.setLetterSpacing(QFont.AbsoluteSpacing, 6)
        title.setFont(_tf)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {CY}; background: transparent;")
        tagline = QLabel("T H E   F U T U R E   O F   F I L M M A K I N G")
        tagline.setAlignment(Qt.AlignCenter)
        tagline.setStyleSheet(f"color: {TEXT_SEC}; font-size: 11pt; background: transparent;")

        # ── tool cards, centered row ──
        cards = QHBoxLayout()
        cards.setSpacing(18)
        cards.addStretch()
        for tool in [t for t in TOOLS if _user_allowed(t)]:
            cards.addWidget(ToolCard(tool))
        cards.addStretch()

        # ── status footer ──
        ok = cfg["shots_exist"] and cfg["db_found"]
        status_parts = []
        if not cfg["shots_exist"]:
            status_parts.append("Shots root not found — mount drive or check settings")
        if not cfg["db_found"]:
            status_parts.append("shot_database.json not found on drive")
        status_text = ("●  " + "  ·  ".join(status_parts)) if status_parts else (
            f"●  {cfg['shots_root']}     ·     SEED STUDIOS — AI IS OUR PRACTICE")
        status = QLabel(status_text)
        status.setAlignment(Qt.AlignCenter)
        status.setStyleSheet(f"color: {OK_COL if ok else ERR_COL}; font-size: 9pt;")
        status.setWordWrap(True)

        root = QVBoxLayout()
        root.setContentsMargins(28, 18, 28, 18)
        root.addLayout(top)
        root.addStretch(2)
        root.addWidget(title)
        root.addSpacing(4)
        root.addWidget(tagline)
        root.addSpacing(34)
        root.addLayout(cards)
        root.addStretch(3)
        root.addWidget(status)

        cw = QWidget()
        cw.setLayout(root)
        self.setCentralWidget(cw)
        self.resize(900, 620)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Settings saved",
                                    "Paths saved. Restart the viewer for changes to take effect.")


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
    _apply_palette(app)
    win = LauncherWindow()
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
