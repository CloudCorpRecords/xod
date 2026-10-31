#!/bin/sh
# Build and install the Xod Partner app (v1) on this Mac.
#
# Assembles "Xod Partner.app" (bundle id dev.xod.partner) in ~/Applications:
# a small compiled launcher as the bundle executable, the Python partner and
# a bundled minimal CPython in Resources, ad-hoc signed with the fixed
# identifier dev.xod.partner (the build is frozen; see README). Then points
# the dev.xod.partner LaunchAgent
# at the bundle executable and reloads it. The v0 state dir, token, inbox,
# and log history are kept exactly as they are.
#
# Run from the repo root or anywhere:  sh partner/install.sh
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"
STATE="$HOME/Library/Application Support/XodPartner"
APP="$HOME/Applications/Xod Partner.app"
AGENT="$HOME/Library/LaunchAgents/dev.xod.partner.plist"
DOMAIN="gui/$(id -u)"

mkdir -p "$STATE/inbox" "$STATE/rejected" "$HOME/Applications" "$HOME/Library/LaunchAgents"

STAGE="$(mktemp -d)/Xod Partner.app"
mkdir -p "$STAGE/Contents/MacOS" "$STAGE/Contents/Resources/partner"
cp "$SRC/app/Info.plist" "$STAGE/Contents/Info.plist"
cp "$SRC/xod_partner.py" "$SRC/partner_ax.py" "$SRC/config.json" "$STAGE/Contents/Resources/partner/"

# Bundle a minimal CPython inside the app: the interpreter, libpython, and
# the stdlib (site-packages and the test/dev suites excluded; the Partner
# is stdlib-only). The child arrangement then mirrors Xod's own backend:
# an interpreter that is part of this bundle's seal. The earlier build
# spawned /usr/bin/python3, which on this Mac resolves into Xcode's
# Python.app, a foreign identity that can change with a dev-tool update.
# The source tree can be overridden for future builds.
PY_SRC="${XOD_PARTNER_PY_SRC:-/Applications/Xod.app/Contents/Resources/python}"
[ -x "$PY_SRC/bin/python3.12" ] || { echo "python source tree not found at $PY_SRC" >&2; exit 1; }
mkdir -p "$STAGE/Contents/Resources/python/bin" "$STAGE/Contents/Resources/python/lib"
cp "$PY_SRC/bin/python3.12" "$STAGE/Contents/Resources/python/bin/"
cp "$PY_SRC/lib/libpython3.12.dylib" "$STAGE/Contents/Resources/python/lib/"
rsync -a --exclude site-packages --exclude test --exclude idlelib \
  --exclude tkinter --exclude turtledemo --exclude __pycache__ \
  "$PY_SRC/lib/python3.12/" "$STAGE/Contents/Resources/python/lib/python3.12/"
# The staged interpreter must run from inside the bundle layout before
# anything is signed; if its prefix does not resolve into the stage, stop.
"$STAGE/Contents/Resources/python/bin/python3.12" -c \
  "import ctypes, http.server, json, sys; assert sys.prefix.endswith('Contents/Resources/python'), sys.prefix"
echo "bundled interpreter verified"

swiftc -O -o "$STAGE/Contents/MacOS/XodPartner" "$SRC/launcher/XodPartner.swift"
codesign --force --deep --sign - --identifier dev.xod.partner "$STAGE"
codesign --verify --strict "$STAGE"

rm -rf "$APP"
mv "$STAGE" "$APP"
echo "installed app: $APP"
codesign -dv "$APP" 2>&1 | grep -E "Identifier|Signature" || true
CDHASH="$(codesign -dvvvv "$APP" 2>&1 | sed -n 's/^CandidateCDHash sha256=//p')"
printf "cdhash=%s\nbuilt=%s\nversion=1.1\n" "$CDHASH" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE/build.txt"
echo "build record: $STATE/build.txt (CDHash $CDHASH)"
echo "NOTE: pair (or repair) the Accessibility switch ONLY against this exact"
echo "build. For an ad-hoc signed app the Accessibility row binds to the"
echo "build's code hash, so any reinstall mints a new build the old row will"
echo "not cover; only the supervised repair (a reset from Xod, then one flip)"
echo "binds it again."

sed -e "s|__APP_EXE__|$APP/Contents/MacOS/XodPartner|g" \
    -e "s|__STATE_DIR__|$STATE|g" \
    "$SRC/dev.xod.partner.plist.template" > "$AGENT"

launchctl bootout "$DOMAIN/dev.xod.partner" 2>/dev/null || true
# bootout is asynchronous: bootstrap raced against it fails with EIO, so
# wait until the label is really gone (up to ~10s), then bootstrap.
for i in 1 2 3 4 5 6 7 8 9 10; do
  launchctl print "$DOMAIN/dev.xod.partner" >/dev/null 2>&1 || break
  sleep 1
done
launchctl bootstrap "$DOMAIN" "$AGENT" || { sleep 3; launchctl bootstrap "$DOMAIN" "$AGENT"; }
launchctl enable "$DOMAIN/dev.xod.partner"
echo "agent dev.xod.partner reloaded; state dir: $STATE"
