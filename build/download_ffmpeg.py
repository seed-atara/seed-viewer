#!/usr/bin/env python3
"""
download_ffmpeg.py — Download static FFmpeg binaries for bundling.

Downloads the correct binary for the current platform into build/ffmpeg/.
PyInstaller then bundles this into the app.

Usage:
    python build/download_ffmpeg.py
"""
from __future__ import annotations

import os
import platform
import stat
import sys
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

HERE   = Path(__file__).parent
OUTDIR = HERE / "ffmpeg"

# Static binary sources (no runtime deps, no GPL codec issues for our use case)
URLS = {
    # macOS universal binary (arm64 + x86_64) from evermeet.cx
    "Darwin": [
        {"url": "https://evermeet.cx/ffmpeg/ffmpeg-7.1.zip",  "archive": "ffmpeg-7.1.zip",        "bin_name": "ffmpeg"},
        {"url": "https://evermeet.cx/ffmpeg/ffprobe-7.1.zip", "archive": "ffprobe-7.1.zip",       "bin_name": "ffprobe"},
    ],
    # Windows static build from gyan.dev (essentials) — both ffmpeg.exe and ffprobe.exe in bin/
    "Windows": [
        {
            "url":      "https://github.com/GyanD/codexffmpeg/releases/download/7.1/ffmpeg-7.1-essentials_build.zip",
            "archive":  "ffmpeg-7.1-essentials_build.zip",
            "bin_name": "ffmpeg.exe",
            "bin_path": "ffmpeg-7.1-essentials_build/bin/ffmpeg.exe",
        },
        {
            "url":      "https://github.com/GyanD/codexffmpeg/releases/download/7.1/ffmpeg-7.1-essentials_build.zip",
            "archive":  "ffmpeg-7.1-essentials_build.zip",
            "bin_name": "ffprobe.exe",
            "bin_path": "ffmpeg-7.1-essentials_build/bin/ffprobe.exe",
        },
    ],
}

SYS = platform.system()


def _progress(block_num, block_size, total_size):
    downloaded = block_num * block_size
    pct = min(100, downloaded * 100 // total_size) if total_size > 0 else 0
    bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
    print(f"\r  [{bar}] {pct}%  ", end="", flush=True)


def _download_one(info: dict) -> None:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    final_bin = OUTDIR / info["bin_name"]

    if final_bin.exists():
        print(f"  Already present: {final_bin}")
        return

    archive_path = OUTDIR / info["archive"]
    # Re-use a cached archive (Windows: ffmpeg and ffprobe share the same zip)
    if not archive_path.exists():
        print(f"  Downloading {info['url']} ...")
        urlretrieve(info["url"], archive_path, reporthook=_progress)
        print()

    print(f"  Extracting {info['bin_name']} ...")
    with zipfile.ZipFile(archive_path) as zf:
        bin_inside = info.get("bin_path", info["bin_name"])
        with zf.open(bin_inside) as src, open(final_bin, "wb") as dst:
            dst.write(src.read())

    if SYS != "Windows":
        final_bin.chmod(final_bin.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    print(f"  Ready: {final_bin}")


def download() -> None:
    entries = URLS.get(SYS)
    if not entries:
        sys.exit(f"ERROR: No FFmpeg URL configured for {SYS}")

    print(f"Downloading FFmpeg binaries ({SYS})...")
    for info in entries:
        _download_one(info)

    # Clean up any leftover archives
    for info in entries:
        p = OUTDIR / info["archive"]
        if p.exists():
            p.unlink()


if __name__ == "__main__":
    download()
