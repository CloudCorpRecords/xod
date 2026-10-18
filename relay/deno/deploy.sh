#!/bin/bash
# Puts the Xod Post Office on Deno Deploy, with its data in Deno KV (Deno Deploy's built-in database).
# Run it in a terminal (the first command opens a sign-in page: "Sign in with GitHub"):
#   ORG=<your Deno org> bash relay/deno/deploy.sh      # app "xod-post", at https://xod-post.<org>.deno.net
# Without a saved sign-in (no keychain), each step asks to sign in again; DENO_DEPLOY_TOKEN (an org access token) skips that.
# It's safe to run again: it skips what already exists and deploys the current code.
set -u
cd "$(dirname "$0")/.."                      # relay/: main.mjs imports ../relay.mjs
DENO="${DENO:-$(command -v deno || true)}"
if [ -z "$DENO" ]; then DENO="npx --yes deno"; fi
DEPLOY="$DENO run -A jsr:@deno/deploy"       # the deploy tool itself (newer than the one some Deno versions bundle)
ORG="${ORG:?set ORG to your Deno Deploy organization (deno run -A jsr:@deno/deploy orgs list)}"
APP="${APP:-xod-post}"
DB="${DB:-xod-post-kv}"
IGNORE="--ignore=worker --ignore=vercel --ignore=node_modules --ignore=server.py --ignore=Dockerfile"

if [ -z "${SKIP_CREATE:-}" ]; then
echo "== 1/4 the app ($APP)"
$DEPLOY create . --org "$ORG" --app "$APP" --source local --runtime-mode dynamic --entrypoint deno/main.mjs \
  --install-command "" --build-command "" --build-timeout 5 --build-memory-limit 1024 --region us $IGNORE \
  || echo "(it may exist already: carrying on)"
fi

if [ -z "${SKIP_PROVISION:-}" ]; then
  echo "== 2/4 its database ($DB, Deno KV)"
  $DEPLOY database provision "$DB" --kind denokv --org "$ORG" || echo "(it may exist already: carrying on)"
fi

echo "== 3/4 attaching the database to the app"
$DEPLOY database assign "$DB" --org "$ORG" --app "$APP" || echo "(it may be attached already: carrying on)"

echo "== 4/4 deploying to production"
$DEPLOY --org "$ORG" --app "$APP" --prod $IGNORE . && echo "== done: check https://$APP.$ORG.deno.net/.well-known/xod-relay"
