#!/bin/bash
# Wrap the PyInstaller build (dist/Hawkeye Jobs) into dist/HawkeyeJobs-Linux.AppImage.
# Run from the repo root on Linux x86-64 after PyInstaller. Downloads appimagetool if missing.
set -euo pipefail

APPDIR=build/AppDir
TOOL=build/appimagetool-x86_64.AppImage
TOOL_URL=https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib"
cp -a "dist/Hawkeye Jobs" "$APPDIR/usr/lib/hawkeye-jobs"
cp packaging/linux/AppRun "$APPDIR/AppRun"
cp packaging/linux/hawkeye-jobs.desktop "$APPDIR/hawkeye-jobs.desktop"
cp packaging/icon.png "$APPDIR/hawkeye-jobs.png"
ln -sf hawkeye-jobs.png "$APPDIR/.DirIcon"

if [ ! -x "$TOOL" ]; then
  curl -fsSL "$TOOL_URL" -o "$TOOL"
  chmod +x "$TOOL"
fi
# Runners have no FUSE: let appimagetool unpack itself instead of mounting.
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$TOOL" --no-appstream "$APPDIR" dist/HawkeyeJobs-Linux.AppImage
