#!/usr/bin/env python3
"""
prepare_sources.py — Copy viewer source files from seed-film into seed-viewer.

Run this from the seed-viewer repo root before building:
    python build/prepare_sources.py --source /path/to/seed-film

What it does:
  1. Copies shot_viewer_qt.py -> seed_viewer/viewer.py
  2. Copies roto_align.py    -> seed_viewer/roto_align.py
  3. Patches the import of pipeline_paths -> seed_viewer.paths in both files
  4. Strips the delivery dispatch block from viewer.py (internal-only)
  5. Copies assets/fonts/

Delivery dispatch (delivery/shot.py calls) is stripped from the public viewer.
Everything else (viewing, wipe, playback, thumbnails) is preserved exactly.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

HERE       = Path(__file__).parent
REPO_ROOT  = HERE.parent
SV_PKG     = REPO_ROOT / "seed_viewer"

# Lines in shot_viewer_qt.py that import or call delivery/* — strip these blocks
# from the public version. The viewer still loads shots; it just can't dispatch deliveries.
_DELIVERY_IMPORT_RE = re.compile(
    r"^(from delivery|import delivery|from \.delivery|"
    r"from deliver_|import deliver_)",
    re.MULTILINE,
)

# Class/method blocks to remove from viewer (delivery dispatch panel)
# These are identified by their docstring / class name markers in shot_viewer_qt.py
_DELIVERY_BLOCK_MARKERS = [
    "class DeliveryPanel",
    "class DeliverySidebar",
    "_delivery_panel",
    "_build_delivery",
    "run_shot(",
    "DeliveryResult",
    "from delivery",
]


def _patch_imports(src: str, is_roto: bool = False) -> str:
    """Replace 'from pipeline_paths import ...' with 'from seed_viewer.paths import ...'"""
    src = re.sub(
        r"from pipeline_paths import",
        "from seed_viewer.paths import",
        src,
    )
    src = re.sub(
        r"import pipeline_paths\b",
        "import seed_viewer.paths as pipeline_paths",
        src,
    )
    # Patch DB_PATH so the bundled app reads shot_database.json from the drive,
    # not from _REPO (which is the PyInstaller temp dir in a frozen build).
    src = re.sub(
        r"^DB_PATH\s*=\s*_REPO\s*/\s*[\"']shot_database\.json[\"']",
        "from seed_viewer.paths import _find_db_path as _sv_find_db_path\n"
        "DB_PATH = _sv_find_db_path() or (Path(__file__).parent / 'shot_database.json')",
        src,
        flags=re.MULTILINE,
    )
    # Patch load_db() to delegate to seed_viewer.paths so it always reads from the drive.
    src = re.sub(
        r"def load_db\(\) -> dict:\n"
        r"    if DB_PATH\.exists\(\):\n"
        r"        return json\.loads\(DB_PATH\.read_text\(encoding=[\"']utf-8[\"']\)\)\n"
        r"    return \{\}",
        "def load_db() -> dict:\n"
        "    from seed_viewer.paths import _load_db as _sv_load_db\n"
        "    return _sv_load_db()",
        src,
    )
    return src


def _strip_delivery(src: str) -> str:
    """
    Remove delivery dispatch code from viewer.py.
    Strategy: find delivery-related import lines and comment them out,
    then find the DeliveryPanel class definition and replace it with a stub.
    This is conservative — only removes what's explicitly delivery-related.
    """
    lines = src.splitlines(keepends=True)
    out = []
    skip_until_blank_class = False

    for line in lines:
        # Strip delivery imports
        if _DELIVERY_IMPORT_RE.match(line.strip()):
            out.append(f"# STRIPPED: {line}")
            continue

        # Stub out delivery panel class (heuristic: class DeliveryPanel/Sidebar)
        if re.match(r"^class Delivery(Panel|Sidebar|View)\b", line.strip()):
            out.append(line)
            out.append("    pass  # delivery dispatch not included in viewer package\n")
            skip_until_blank_class = True
            continue

        if skip_until_blank_class:
            # Skip until next top-level class/def
            if re.match(r"^(class |def )\w", line):
                skip_until_blank_class = False
                out.append(line)
            continue

        out.append(line)

    return "".join(out)


def _add_launch_fn(src: str, fn_body: str) -> str:
    """Append a module-level launch() function if not already present."""
    if "\ndef launch(" in src or "\ndef launch(" in src:
        return src
    return src + "\n\n" + fn_body + "\n"


VIEWER_LAUNCH = '''
def launch() -> "QMainWindow | None":
    """Called by the launcher. Returns the main viewer window.

    Frozen builds use this instead of main(), so it must replicate main()'s
    app-level setup: dispatcher (thumbnail delivery), viewer stylesheet/fonts,
    and the Space event filter (hold-to-play). Skipping any of these is why
    the bundle previously had blank thumbnails and dead playback.
    """
    from PySide6.QtWidgets import QApplication
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    try:
        _load_fonts()
        app.setFont(_best_mono_font())
    except Exception:
        pass
    # Create the dispatcher on the MAIN thread before ShotViewerApp() spawns
    # its thumbnail worker pool. Otherwise the first worker thread creates it
    # lazily with worker-thread affinity and thumbnail events never deliver.
    _Dispatcher.instance()
    win = ShotViewerApp()
    try:
        win.setStyleSheet(QSS)
    except Exception:
        pass
    # Install the app-level Space filter so hold-to-play works regardless of
    # focus. Keep a reference on the window so it is not garbage-collected.
    win._space_filter = _SpaceFilter(win)
    app.installEventFilter(win._space_filter)
    return win
'''

ROTO_LAUNCH = '''
def launch() -> "QMainWindow | None":
    """Called by the launcher. Returns the roto align window."""
    from PySide6.QtWidgets import QApplication
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    win = AlignWindow()
    return win
'''


# The Artist Hub panel + the pipeline backend it needs at runtime. These are
# copied into seed_viewer/ at build time and gitignored (NOT committed to the
# public repo) — the frozen binary bundles them, so no source is exposed. They
# import each other as top-level modules; artist_hub_qt does sys.path.insert(HERE)
# so the copies resolve within seed_viewer/. No import patching needed.
_PIPELINE_PANEL = "artist_hub_qt.py"                 # -> seed_viewer/pipeline_panel.py
_PIPELINE_BACKEND = [
    "pipeline_paths.py", "pipeline_naming.py", "pipeline_state.py", "pipeline_silo.py",
    "pipeline_resolve.py", "pipeline_artifacts.py", "pipeline_auth.py", "hub_client.py",
]
_PIPELINE_DATA = ["task_spec.json", "agents.json"]


def _copy_pipeline(source_repo: Path, dry_run: bool) -> None:
    """Bundle the Artist Hub panel + its backend into seed_viewer/ (gitignored)."""
    jobs = [(source_repo / _PIPELINE_PANEL, SV_PKG / "pipeline_panel.py")]
    jobs += [(source_repo / m, SV_PKG / m) for m in _PIPELINE_BACKEND + _PIPELINE_DATA]
    for src, dst in jobs:
        if not src.exists():
            print(f"  WARN: {src.name} not found — skip (artist hub may not load)")
            continue
        if dry_run:
            print(f"  DRY: {src.name} -> seed_viewer/{dst.name}")
        else:
            shutil.copy2(src, dst)
            print(f"  OK : {src.name} -> seed_viewer/{dst.name}")
    # ComfyUI workflow templates (optional, for the tools)
    wf_src = source_repo / "comfy_workflows"
    if wf_src.exists() and not dry_run:
        (SV_PKG / "comfy_workflows").mkdir(exist_ok=True)
        for f in wf_src.glob("*.json"):
            shutil.copy2(f, SV_PKG / "comfy_workflows" / f.name)


def prepare(source_repo: Path, dry_run: bool = False) -> None:
    if not source_repo.exists():
        sys.exit(f"ERROR: seed-film repo not found: {source_repo}")

    tasks = [
        (source_repo / "shot_viewer_qt.py", SV_PKG / "viewer.py",      False, True),
        (source_repo / "roto_align.py",     SV_PKG / "roto_align.py",  True,  False),
    ]
    _copy_pipeline(source_repo, dry_run)

    for src_path, dst_path, is_roto, strip_delivery in tasks:
        if not src_path.exists():
            print(f"  WARN: {src_path.name} not found in source repo — skip")
            continue

        src = src_path.read_text(encoding="utf-8")

        # Patch imports
        src = _patch_imports(src, is_roto=is_roto)

        # Strip delivery from viewer
        if strip_delivery:
            src = _strip_delivery(src)

        # Add launch() entry point
        launch_fn = ROTO_LAUNCH if is_roto else VIEWER_LAUNCH
        src = _add_launch_fn(src, launch_fn)

        if dry_run:
            print(f"  DRY: {src_path.name} -> {dst_path.name}  ({len(src)} chars)")
        else:
            dst_path.write_text(src, encoding="utf-8")
            print(f"  OK : {src_path.name} -> {dst_path.name}")

    # Copy fonts
    font_src = source_repo / "assets" / "fonts"
    font_dst = REPO_ROOT / "assets" / "fonts"
    if font_src.exists():
        if dry_run:
            print(f"  DRY: copy fonts -> {font_dst}")
        else:
            font_dst.mkdir(parents=True, exist_ok=True)
            for f in font_src.glob("*.ttc"):
                shutil.copy2(f, font_dst / f.name)
            print(f"  OK : fonts -> {font_dst}")

    print("\nDone. Next steps:")
    print("  1. Review seed_viewer/viewer.py — check launch() class name")
    print("  2. Review seed_viewer/roto_align.py — check launch() class name")
    print("  3. Run: python build/download_ffmpeg.py")
    print("  4. Run: pyinstaller build/seed_viewer_mac.spec  (on Mac)")
    print("       or: pyinstaller build/seed_viewer_win.spec  (on Windows)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True,
                    help="Path to seed-film repo root (e.g. /path/to/seed-film)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    prepare(Path(args.source), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
