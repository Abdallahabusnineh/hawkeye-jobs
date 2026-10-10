#!/bin/bash
# Hawkeye Jobs installer for macOS.
#
#   curl -fsSL https://abdallahabusnineh.github.io/hawkeye-jobs/install.sh | bash
#
# Downloads the latest release for this Mac (Apple Silicon or Intel), checks it against the
# release's SHA256SUMS.txt, copies the app into /Applications and opens it. Files downloaded with
# curl are not marked "downloaded from the internet", so macOS opens the app without the
# "could not verify the developer" block that a browser download gets.
set -euo pipefail

REPO="Abdallahabusnineh/hawkeye-jobs"
APP_NAME="Hawkeye Jobs.app"
DEST="/Applications"

say() { printf '\n==> %s\n' "$1"; }
fail() { printf '\nError: %s\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "This installer is for macOS. Download other systems from https://abdallahabusnineh.github.io/hawkeye-jobs/"

if [ "$(uname -m)" = "arm64" ] || [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then
  FILE="HawkeyeJobs-macOS-AppleSilicon.dmg"
else
  FILE="HawkeyeJobs-macOS-Intel.dmg"
fi
BASE="https://github.com/$REPO/releases/latest/download"

WORK="$(mktemp -d -t hawkeye-install)"
MOUNT="$WORK/mount"
cleanup() {
  hdiutil detach "$MOUNT" -quiet 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

say "Downloading $FILE"
curl -fL --progress-bar "$BASE/$FILE" -o "$WORK/$FILE" || fail "Download failed. Check your internet connection and try again."
curl -fsSL "$BASE/SHA256SUMS.txt" -o "$WORK/SHA256SUMS.txt" || fail "Could not download the checksums."

say "Checking the download"
EXPECTED="$(awk -v f="$FILE" '$2 == f {print $1}' "$WORK/SHA256SUMS.txt")"
ACTUAL="$(shasum -a 256 "$WORK/$FILE" | awk '{print $1}')"
[ -n "$EXPECTED" ] && [ "$EXPECTED" = "$ACTUAL" ] || fail "The download is damaged or was changed (checksum mismatch). Nothing was installed."

say "Installing to $DEST"
mkdir -p "$MOUNT"
hdiutil attach "$WORK/$FILE" -nobrowse -readonly -quiet -mountpoint "$MOUNT" || fail "Could not open the disk image."
[ -d "$MOUNT/$APP_NAME" ] || fail "The disk image does not contain $APP_NAME."
osascript -e 'tell application "Hawkeye Jobs" to quit' >/dev/null 2>&1 || true
SUDO=""
[ -w "$DEST" ] || SUDO="sudo"
$SUDO rm -rf "$DEST/$APP_NAME"
$SUDO ditto "$MOUNT/$APP_NAME" "$DEST/$APP_NAME"
# Also clear the mark in case an earlier browser download left it on files we just replaced.
$SUDO xattr -dr com.apple.quarantine "$DEST/$APP_NAME" 2>/dev/null || true

say "Done. Opening Hawkeye Jobs"
open "$DEST/$APP_NAME"
