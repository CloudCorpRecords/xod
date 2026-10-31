#!/bin/sh
# Build and install the Xod Partner app (v1) on this Mac.
#
# Assembles "Xod Partner.app" (bundle id dev.xod.partner) in ~/Applications:
# a small compiled launcher as the bundle executable, the Python partner in
# Resources, ad-hoc signed with the fixed identifier dev.xod.partner (the
# build is frozen; see README). Then points the dev.xod.partner LaunchAgent
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
swiftc -O -o "$STAGE/Contents/MacOS/XodPartner" "$SRC/launcher/XodPartner.swift"
codesign --force --deep --sign - --identifier dev.xod.partner "$STAGE"
codesign --verify --strict "$STAGE"

rm -rf "$APP"
mv "$STAGE" "$APP"
echo "installed app: $APP"
codesign -dv "$APP" 2>&1 | grep -E "Identifier|Signature" || true

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
