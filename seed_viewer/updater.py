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
    # Replace the install IN PLACE with robocopy /MIR instead of "rmdir the dir + move the
    # new one in". The old approach failed for many users: rmdir of a just-killed app's dir
    # is often blocked (handles not released / AV lock) -> the script bailed and the app was
    # never updated (it just re-prompts); and `move` of a directory across drives silently
    # fails. robocopy mirrors file-by-file, works across volumes, retries locked files, and
    # never needs to delete the parent dir. A log + pause-on-failure means it's never a
    # silent "nothing happened".
    dest = str(root)
    bat = Path(tempfile.gettempdir()) / "seedviewer_update.bat"
    bat.write_text(
        "@echo off\r\n"
        'set "LOG=%TEMP%\\seedviewer_update.log"\r\n'
        'echo [seed update] %date% %time%  dest="' + dest + '" > "%LOG%"\r\n'
        "ping -n 3 127.0.0.1 >nul\r\n"
        "taskkill /f /im SeedViewer.exe >nul 2>&1\r\n"
        "ping -n 4 127.0.0.1 >nul\r\n"          # let the OS release the exe handle
        'set "ZIP=%TEMP%\\SeedViewer-win.zip"\r\n'
        f'echo Downloading... >> "%LOG%"\r\n'
        f'curl -L -o "%ZIP%" "{WIN_ASSET}" >> "%LOG%" 2>&1\r\n'
        'if errorlevel 1 ( echo DOWNLOAD FAILED >> "%LOG%" & echo Update download failed - see %LOG% & pause & exit /b 1 )\r\n'
        'set "STAGE=%TEMP%\\sv_update_stage"\r\n'
        'rmdir /s /q "%STAGE%" 2>nul\r\n'
        'mkdir "%STAGE%"\r\n'
        'tar -xf "%ZIP%" -C "%STAGE%" >> "%LOG%" 2>&1\r\n'
        'if not exist "%STAGE%\\SeedViewer\\SeedViewer.exe" ( echo EXTRACT FAILED >> "%LOG%" & echo Update extract failed - see %LOG% & pause & exit /b 1 )\r\n'
        f'echo Installing to "{dest}" >> "%LOG%"\r\n'
        f'robocopy "%STAGE%\\SeedViewer" "{dest}" /MIR /R:5 /W:2 /NFL /NDL /NJH /NJS >> "%LOG%" 2>&1\r\n'
        # robocopy: exit codes 0-7 are success, 8+ are failure
        'if %ERRORLEVEL% GEQ 8 ( echo INSTALL FAILED %ERRORLEVEL% >> "%LOG%" & echo Update install failed - see %LOG% & pause & exit /b 1 )\r\n'
        'rmdir /s /q "%STAGE%" 2>nul\r\n'
        'echo [done] >> "%LOG%"\r\n'
        f'start "" "{dest}\\SeedViewer.exe"\r\n',
        encoding="utf-8")
    return str(bat)


def _mac_script(root: Path) -> str:
    # Mirror the Windows hardening: log everything, fail LOUDLY (a dialog, not a silent
    # no-op), copy the bundle with `ditto` (the correct macOS bundle copy), and STRIP THE
    # QUARANTINE off the freshly-downloaded app — otherwise Gatekeeper blocks the relaunch
    # and it looks like "nothing happened".
    app = str(root)
    fail = ('osascript -e \'display alert "Seed Viewer update failed" message "%s See '
            '$TMPDIR/seedviewer_update.log."\' >/dev/null 2>&1; exit 1')
    sh = Path(tempfile.gettempdir()) / "seedviewer_update.sh"
    sh.write_text(
        "#!/bin/sh\n"
        'LOG="$TMPDIR/seedviewer_update.log"\n'
        f'echo "[seed update] $(date) app={app}" > "$LOG"\n'
        "sleep 2\n"
        'DMG="$TMPDIR/SeedViewer-mac.dmg"\n'
        f'curl -L -o "$DMG" "{MAC_ASSET}" >> "$LOG" 2>&1 || {{ {fail % "Download failed."} ; }}\n'
        'MNT=$(hdiutil attach "$DMG" -nobrowse 2>>"$LOG" | grep -o "/Volumes/.*" | head -1)\n'
        f'[ -z "$MNT" ] && {{ {fail % "Could not mount the disk image."} ; }}\n'
        f'rm -rf "{app}"\n'
        f'ditto "$MNT/SeedViewer.app" "{app}" >> "$LOG" 2>&1 || {{ hdiutil detach "$MNT" >/dev/null 2>&1; {fail % "Install failed (check folder permissions)."} ; }}\n'
        f'xattr -dr com.apple.quarantine "{app}" 2>/dev/null\n'
        'hdiutil detach "$MNT" >/dev/null 2>&1\n'
        'echo "[done]" >> "$LOG"\n'
        f'open "{app}"\n',
        encoding="utf-8")
    os.chmod(sh, 0o755)
    return str(sh)
