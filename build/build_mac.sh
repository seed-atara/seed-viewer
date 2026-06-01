#!/bin/bash
# Local macOS build script
# Usage: bash build/build_mac.sh /path/to/seed-film
set -e

SEED_FILM="${1:?Usage: bash build/build_mac.sh /path/to/seed-film}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Seed Viewer — local macOS build ==="
echo "  seed-film: $SEED_FILM"
echo "  seed-viewer: $REPO"
echo ""

# Prepare source files
python "$REPO/build/prepare_sources.py" --source "$SEED_FILM"

# Download FFmpeg if needed
python "$REPO/build/download_ffmpeg.py"

# Build
cd "$REPO"
pyinstaller build/seed_viewer_mac.spec \
    --distpath build/dist \
    --workpath build/work \
    --clean

echo ""
echo "=== Build complete ==="
echo "App: $REPO/build/dist/SeedViewer.app"
echo ""
echo "To create DMG:"
echo "  brew install create-dmg"
echo "  create-dmg --volname 'Seed Viewer' build/dist/SeedViewer-mac.dmg build/dist/SeedViewer/"
