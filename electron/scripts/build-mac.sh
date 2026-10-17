#!/bin/bash
# Builds Xod.app and its installer, one step at a time, and checks the signature where it matters:
# on the app inside the finished .dmg, which is what people download. (Letting electron-builder sign
# and make the .dmg in one go sometimes packed the app before its final signature was written, and
# macOS then calls a downloaded copy "damaged".)
#
# Usage: scripts/build-mac.sh [arm64|x64] (default arm64). Build each arch on a Mac of that arch:
# the bundled Python comes from the build machine, so cross-building would ship the wrong one.
set -euo pipefail
cd "$(dirname "$0")/.."

ARCH="${1:-arm64}"
if [ "$ARCH" != "arm64" ] && [ "$ARCH" != "x64" ]; then
  echo "Usage: scripts/build-mac.sh [arm64|x64]" >&2
  exit 1
fi

npm run bundle-python
rm -rf dist
npx electron-builder --mac dir --$ARCH -c.mac.identity=- --publish never
codesign --verify --deep --strict dist/mac-$ARCH/Xod.app
npx electron-builder --mac dmg --$ARCH --prepackaged dist/mac-$ARCH/Xod.app -c.mac.identity=null --publish never

DMG=$(ls dist/Xod-*-$ARCH.dmg)
MNT=$(mktemp -d)
hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$DMG" -quiet
trap 'hdiutil detach "$MNT" -quiet || true' EXIT
codesign --verify --deep --strict "$MNT/Xod.app"
echo "Built $DMG; the app inside has a valid signature."
