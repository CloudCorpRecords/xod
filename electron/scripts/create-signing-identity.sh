#!/bin/bash
# One-time setup: create Xod's self-signed code signing identity on this Mac (AGE-57).
#
# Why: macOS binds privacy grants and keychain "Always Allow" entries to an app's designated
# requirement. Ad-hoc builds have a cdhash-only requirement that changes every build, so grants
# die on every update. A certificate, even a free self-signed one, gives a requirement of
# "identifier + certificate leaf hash" that is identical for every build signed with it, so
# grants made once keep matching. Full story: docs/SIGNING.md.
#
# What this script does, once, on a Mac:
#   1. Creates a self-signed code signing certificate named "Xod Release Signing"
#      (RSA 2048, 10 year validity, code-signing EKU), in a temporary folder.
#   2. Imports it into your login keychain and marks it trusted for code signing in your
#      user trust settings (no sudo; the system-domain variant is printed at the end for
#      the case where TCC still falls back to cdhash matching on this machine).
#   3. Leaves a password-protected .p12 at ~/xod-signing-identity.p12 (mode 600). That file
#      is the identity: back it up somewhere safe (the Secure Vault), because losing it, or
#      replacing it with a new certificate, resets every grant one more time. It is also
#      what the release workflow needs as the CSC_LINK secret so CI builds sign with the
#      same identity (exact commands printed at the end; nothing is uploaded by this script).
#
# The script refuses to run if the identity already exists: overwriting it would mint a new
# certificate and orphan every grant bound to the old one, which is the bug this fixes.
# It asks for two passwords interactively (the .p12 export password, twice). Nothing here
# costs money and nothing leaves this machine.
set -euo pipefail

NAME="Xod Release Signing"
P12_OUT="${XOD_SIGNING_P12_OUT:-$HOME/xod-signing-identity.p12}"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

if [ "$(uname)" != "Darwin" ]; then
  echo "This script runs on a Mac (it needs security and codesign)." >&2
  exit 1
fi

if security find-identity -v -p codesigning 2>/dev/null | grep -q "\"$NAME\""; then
  echo "Identity \"$NAME\" already exists in the keychain; nothing to do."
  echo "Do NOT replace it: a new certificate would orphan every grant bound to the current one."
  exit 0
fi

if [ -e "$P12_OUT" ]; then
  echo "$P12_OUT already exists but no matching identity is in the keychain." >&2
  echo "Import that file (security import \"$P12_OUT\" -k \"$KEYCHAIN\" -T /usr/bin/codesign)" >&2
  echo "instead of creating a second identity, or move it aside first if it is not the identity." >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Choose a password for the exported .p12 (you will need it for the CSC_KEY_PASSWORD"
echo "GitHub secret and for any future import; store it in the Secure Vault, not in a file)."
read -r -s -p "P12 password: " P12_PW; echo
read -r -s -p "P12 password again: " P12_PW2; echo
if [ "$P12_PW" != "$P12_PW2" ] || [ -z "$P12_PW" ]; then
  echo "Passwords do not match or are empty; nothing created." >&2
  exit 1
fi

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$WORK/key.pem" -out "$WORK/cert.pem" \
  -days 3650 -subj "/CN=$NAME" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature" \
  -addext "extendedKeyUsage=critical,codeSigning"

# -legacy: macOS security import cannot read the newer PKCS#12 ciphers openssl 3 writes
# by default; the legacy profile is the one it accepts.
openssl pkcs12 -export -legacy \
  -inkey "$WORK/key.pem" -in "$WORK/cert.pem" \
  -name "$NAME" -out "$WORK/xod-signing.p12" -passout "pass:$P12_PW"

security import "$WORK/xod-signing.p12" -k "$KEYCHAIN" -P "$P12_PW" -T /usr/bin/codesign
security add-trusted-cert -p codeSign -k "$KEYCHAIN" "$WORK/cert.pem"

cp "$WORK/xod-signing.p12" "$P12_OUT"
chmod 600 "$P12_OUT"
unset P12_PW P12_PW2

echo
security find-identity -v -p codesigning | grep "\"$NAME\"" || {
  echo "The identity does not list as valid for code signing; setup is INCOMPLETE." >&2
  exit 1
}
echo "Identity \"$NAME\" created, imported, and trusted for code signing."
echo "Exported identity file: $P12_OUT (mode 600). Back it up in the Secure Vault now."
echo
echo "Next steps (run them yourself; this script uploads nothing):"
echo "  1. Give CI the same identity, so release builds sign identically to local ones:"
echo "       base64 -i \"$P12_OUT\" | gh secret set CSC_LINK --repo CloudCorpRecords/xod"
echo "       gh secret set CSC_KEY_PASSWORD --repo CloudCorpRecords/xod   (the P12 password)"
echo "  2. Local builds: export XOD_MAC_SIGN_IDENTITY=\"$NAME\" before npm run dist,"
echo "     or rely on the partner script, which finds this identity by name."
echo "  3. If a granted build still loses its grants on the next build (some macOS versions"
echo "     only honor requirement matching for system-trusted anchors), trust the identity"
echo "     in the system domain once, with the certificate from the export folder:"
echo "       openssl pkcs12 -in \"$P12_OUT\" -clcerts -nokeys -out /tmp/xod-signing-cert.pem"
echo "       sudo security add-trusted-cert -d -p codeSign -k /Library/Keychains/System.keychain /tmp/xod-signing-cert.pem"
echo "  4. The paid alternative (Developer ID, adds notarization) is covered in docs/SIGNING.md."
