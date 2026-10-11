#!/bin/bash
# Builds Xod.app and its installer, one step at a time, and checks the signature where it matters:
# on the app inside the finished .dmg, which is what people download. (Letting electron-builder sign
# and make the .dmg in one go sometimes packed the app before its final signature was written, and
# macOS then calls a downloaded copy "damaged".)
set -euo pipefail
cd "$(dirname "$0")/.."

npm run bundle-python
rm -rf dist
npx electron-builder --mac dir --arm64 -c.mac.identity=- --publish never
codesign --verify --deep --strict dist/mac-arm64/Xod.app
npx electron-builder --mac dmg --arm64 --prepackaged dist/mac-arm64/Xod.app -c.mac.identity=null --publish never

DMG=$(ls dist/Xod-*-arm64.dmg)
MNT=$(mktemp -d)
hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$DMG" -quiet
trap 'hdiutil detach "$MNT" -quiet || true' EXIT
codesign --verify --deep --strict "$MNT/Xod.app"
echo "Built $DMG; the app inside has a valid signature."
