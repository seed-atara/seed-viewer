# Seed Viewer — build and release tasks
#
# Usage:
#   make dev                         # run from source (no build)
#   make prepare SOURCE=/path/to/seed-film
#   make release VERSION=0.2.0       # tag + push -> triggers CI build

PYTHON   := python
SEED_FILM ?= ../seed-film

.PHONY: dev prepare release build-mac build-win clean

# Run directly from source — instant, no PyInstaller needed
dev:
	$(PYTHON) -m seed_viewer.main

# Copy + adapt source files from seed-film
prepare:
	$(PYTHON) build/prepare_sources.py --source $(SEED_FILM)

# Download bundled FFmpeg for current platform
ffmpeg:
	$(PYTHON) build/download_ffmpeg.py

# Build for current platform (requires: make prepare + make ffmpeg first)
build-mac:
	pyinstaller build/seed_viewer_mac.spec --distpath build/dist --workpath build/work --clean

build-win:
	pyinstaller build\seed_viewer_win.spec --distpath build\dist --workpath build\work --clean

# Tag a release and push — triggers GitHub Actions CI build on both platforms
# Usage: make release VERSION=0.2.0
release:
ifndef VERSION
	$(error VERSION is required: make release VERSION=0.2.0)
endif
	@echo "Tagging v$(VERSION) and pushing..."
	git add -A
	git diff-index --quiet HEAD || git commit -m "Release v$(VERSION)"
	git tag v$(VERSION)
	git push origin main
	git push origin v$(VERSION)
	@echo ""
	@echo "Build triggered. Watch progress at:"
	@echo "  https://github.com/seed-atara/seed-viewer/actions"

# Clean build artifacts
clean:
	rm -rf build/dist build/work
