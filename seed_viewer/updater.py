"""Auto-update for the Seed Viewer launcher.

On launch the app compares its baked-in version (stamped by CI into _buildinfo.py)
to the latest GitHub release. If a newer one exists it offers a one-click update that
swaps in the new build and relaunches — Windows via the .zip, macOS via the .dmg.

The version CHECK is safe + cross-platform (requests). The actual swap is OS-specific
and mirrors the proven reinstall_viewer.bat logic: spawn a detached updater that waits
for the app to quit, replaces the install, and relaunches.
"""
from __future__ import annotations

import os
import re
import sys
import subprocess
import tempfile
from pathlib import Path

REPO = "seed-atara/seed-viewer"
LATEST_REDIRECT = f"https://github.com/{REPO}/releases/latest"
WIN_ASSET = f"https://github.com/{REPO}/releases/latest/download/SeedViewer-win.zip"
MAC_ASSET = f"https://github.com/{REPO}/releases/latest/download/SeedViewer-mac.dmg"


# ── version ──────────────────────────────────────────────────────────────────
def installed_version() -> str:
    """The build's stamped version (CI writes _buildinfo.VERSION); '0.0.0' if absent
    (e.g. running from source) so the check no-ops rather than nags."""
    try:
        from seed_viewer import _buildinfo  # type: ignore
        return str(_buildinfo.VERSION)
    except Exception:
        return "0.0.0"


def _vt(s: str) -> tuple:
    nums = re.findall(r"\d+", s or "")
    nums = [int(x) for x in nums[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def latest_version(timeout: int = 5) -> str | None:
    """The newest published tag, via the /releases/latest redirect (no API token)."""
    try:
        import requests
        r = requests.get(LATEST_REDIRECT, allow_redirects=True, timeout=timeout)
        m = re.search(r"/tag/(v[\d.]+)", r.url)
        return m.group(1) if m else None
    except Exception:
        return None


def check() -> str | None:
    """Return the latest tag iff it is STRICTLY NEWER than installed, else None."""
    cur, latest = installed_version(), latest_version()
    if latest and _vt(latest) > _vt(cur):
        return latest
    return None


# ── update (OS-specific swap) ────────────────────────────────────────────────
def _install_root() -> Path:
    """Folder to replace. Windows: the dir holding SeedViewer.exe.
    macOS: the SeedViewer.app bundle."""
    exe = Path(sys.executable)
    if sys.platform == "darwin":
        for p in exe.parents:
            if p.suffix == ".app":
                return p
        return exe.parent
    return exe.parent


def run_update_and_quit(app=None) -> None:
    """Spawn the detached OS updater, then quit so it can replace the files."""
    root = _install_root()
    if sys.platform == "darwin":
        subprocess.Popen(["/bin/sh", _mac_script(root)], start_new_session=True)
    else:
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — survives our exit
        subprocess.Popen(["cmd", "/c", _win_script(root)],
                         creationflags=0x00000008 | 0x00000200, close_fds=True)
    if app is not None:
        app.quit()
    else:
        os._exit(0)


def _win_script(root: Path) -> str:
    dest = str(root)
    bat = Path(tempfile.gettempdir()) / "seedviewer_update.bat"
    bat.write_text(
        "@echo off\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        "taskkill /f /im SeedViewer.exe >nul 2>&1\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        'set "ZIP=%TEMP%\\SeedViewer-win.zip"\r\n'
        f'curl -L -o "%ZIP%" "{WIN_ASSET}"\r\n'
        "if errorlevel 1 exit /b 1\r\n"
        'set "STAGE=%TEMP%\\sv_update_stage"\r\n'
        'rmdir /s /q "%STAGE%" 2>nul\r\n'
        'mkdir "%STAGE%"\r\n'
        'tar -xf "%ZIP%" -C "%STAGE%"\r\n'
        f'rmdir /s /q "{dest}" 2>nul\r\n'
        # retry once if the dir was still locked, so the move never nests
        f'if exist "{dest}" ( ping -n 3 127.0.0.1 >nul & rmdir /s /q "{dest}" 2>nul )\r\n'
        f'if exist "{dest}" exit /b 1\r\n'
        f'move "%STAGE%\\SeedViewer" "{dest}" >nul\r\n'
        'rmdir /s /q "%STAGE%" 2>nul\r\n'
        f'start "" "{dest}\\SeedViewer.exe"\r\n',
        encoding="utf-8")
    return str(bat)


def _mac_script(root: Path) -> str:
    app = str(root)
    sh = Path(tempfile.gettempdir()) / "seedviewer_update.sh"
    sh.write_text(
        "#!/bin/sh\n"
        "sleep 2\n"
        'DMG="$TMPDIR/SeedViewer-mac.dmg"\n'
        f'curl -L -o "$DMG" "{MAC_ASSET}" || exit 1\n'
        'MNT=$(hdiutil attach "$DMG" -nobrowse 2>/dev/null | grep -o "/Volumes/.*" | head -1)\n'
        '[ -z "$MNT" ] && exit 1\n'
        f'rm -rf "{app}"\n'
        f'cp -R "$MNT/SeedViewer.app" "{app}"\n'
        'hdiutil detach "$MNT" >/dev/null 2>&1\n'
        f'open "{app}"\n',
        encoding="utf-8")
    os.chmod(sh, 0o755)
    return str(sh)
