#!/bin/bash
# Builds Xod.app and its installer, one step at a time, and checks the signature where it matters:
# on the app inside the finished .dmg, which is what people download. (Letting electron-builder sign
# and make the .dmg in one go sometimes packed the app before its final signature was written, and
# macOS then calls a downloaded copy "damaged".)
#
# Signing identity (AGE-57, docs/SIGNING.md): macOS binds privacy grants (Accessibility, Screen
# Recording) and keychain "Always Allow" entries to the app's designated requirement. An ad-hoc
# signature has no certificate, so that requirement is the build's cdhash alone, and every rebuild
# orphans every grant. This script therefore signs with a real certificate whenever one is
# available, in this order:
#   1. XOD_MAC_SIGN_IDENTITY (a keychain identity name or hash), when set;
#   2. the certificate electron-builder imports from CSC_LINK / CSC_KEY_PASSWORD (the release
#      workflow passes these from GitHub secrets), when CSC_LINK is set;
#   3. ad-hoc, only as a fallback for local builds with no certificate, with a loud warning.
# When a certificate was expected (case 1 or 2) the built app's designated requirement is
# verified with scripts/check_designated_requirement.py and the build FAILS if the product is
# ad-hoc or cdhash-anchored anyway. An exported-but-empty CSC_LINK (a workflow env whose secret
# is unset) counts as "no certificate", never as a certificate that fails to decode.
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

if [ -z "${CSC_LINK:-}" ]; then unset CSC_LINK; fi
if [ -z "${CSC_KEY_PASSWORD:-}" ]; then unset CSC_KEY_PASSWORD; fi

SIGN_MODE="adhoc"
if [ -n "${XOD_MAC_SIGN_IDENTITY:-}" ]; then
  SIGN_MODE="identity"
elif [ -n "${CSC_LINK:-}" ]; then
  SIGN_MODE="csc"
else
  echo "WARNING: no signing identity configured (XOD_MAC_SIGN_IDENTITY and CSC_LINK are both unset)." >&2
  echo "This build will be ad-hoc signed: its designated requirement is this build's cdhash only," >&2
  echo "so macOS privacy grants and keychain Always Allow entries will NOT survive the next update." >&2
  echo "See docs/SIGNING.md for the one-time identity setup (AGE-57)." >&2
fi

npm run bundle-python
rm -rf dist
if [ "$SIGN_MODE" = "identity" ]; then
  npx electron-builder --mac dir --$ARCH -c.mac.identity="$XOD_MAC_SIGN_IDENTITY" --publish never
elif [ "$SIGN_MODE" = "csc" ]; then
  npx electron-builder --mac dir --$ARCH --publish never
else
  npx electron-builder --mac dir --$ARCH -c.mac.identity=- --publish never
fi
APP="dist/mac-$ARCH/Xod.app"
codesign --verify --deep --strict "$APP"
if [ "$SIGN_MODE" = "adhoc" ]; then
  python3 ../scripts/check_designated_requirement.py --expect-any "$APP" || true
else
  python3 ../scripts/check_designated_requirement.py "$APP"
fi
npx electron-builder --mac dmg --$ARCH --prepackaged "$APP" -c.mac.identity=null --publish never

DMG=$(ls dist/Xod-*-$ARCH.dmg)
MNT=$(mktemp -d)
hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$DMG" -quiet
trap 'hdiutil detach "$MNT" -quiet || true' EXIT
codesign --verify --deep --strict "$MNT/Xod.app"
if [ "$SIGN_MODE" != "adhoc" ]; then
  python3 ../scripts/check_designated_requirement.py "$MNT/Xod.app"
fi
echo "Built $DMG; the app inside has a valid signature (signing mode: $SIGN_MODE)."
