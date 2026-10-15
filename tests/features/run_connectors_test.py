#!/usr/bin/env python3
"""
Connected apps without signing in to anything:

  C1  "This Mac" connects as a built-in MCP server and lists its tools (reads read-only, writes ask).
  C2  Notion and Linear: Xod discovers their sign-in server, registers itself, and opens the right
      sign-in page (captured here instead of opening a browser). No login happens.
  C3  A wrong sign-in code ends as a clear error, not a hang or crash.
  C4  GitHub with a bad token reports the failure.
  C5  The local-only guard lets a sign-in provider return to /oauth/callback, and nothing else.
  C6  Removing a connector forgets its tokens.

Uses a throwaway data folder and an in-memory keychain, so nothing touches your real Keychain.
Needs internet for C2-C4.
  .venv/bin/python tests/features/run_connectors_test.py
"""
import asyncio
import os
import shutil
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod_connectors_", dir="/tmp"))
os.environ["XOD_DATA_DIR"] = str(TMP)

import keyring  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402


class MemoryKeyring(KeyringBackend):
    priority = 1
    store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


keyring.set_keyring(MemoryKeyring())

from app.services import connectors, keychain, mcp_client, mcp_oauth  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""), flush=True)


opened = []
mcp_oauth.open_in_browser = lambda url: opened.append(url)   # capture instead of opening Safari/Chrome
REDIRECT = "http://127.0.0.1:8765/oauth/callback"


async def wait_state(cid, want, timeout=60):
    for _ in range(timeout * 4):
        st = next(c for c in connectors.catalog() if c["id"] == cid)
        if st["state"] in want:
            return st
        await asyncio.sleep(0.25)
    return st


async def main():
    hub = mcp_client.mcp_hub

    # C1
    r = await connectors.connect("mac", REDIRECT)
    if sys.platform != "darwin":
        # Its tools drive the Mac's own apps: off a Mac it isn't offered (they'd all fail), and says why.
        st = next(c for c in connectors.catalog() if c["id"] == "mac")
        check("C1 This Mac isn't offered off a Mac, and says why", not r["success"] and st["state"] == "off"
              and "only works on a Mac" in (st.get("unavailable") or ""), f"{r} {st.get('unavailable')}")
    else:
        st = await wait_state("mac", ("connected", "error"))
        tools = next(s for s in hub.status() if s["name"] == "mac")["tools"]
        reads = {t["name"] for t in tools if t["read_only"]}
        check("C1 This Mac connects", st["state"] == "connected" and len(tools) == 18, f"{len(tools)} tools, {st.get('error')}")
        from app.services.mcp_client import _destructive
        c = hub.conns["mac"]
        deletes = sorted(t.name for t in c.tools if _destructive(t))
        check("C1b deleting tools are marked destructive (they always ask)",
              deletes == ["calendar_delete_event", "mail_delete", "notes_delete", "reminders_delete"], str(deletes))
        check("C1 reads are read-only, writes ask", "calendar_events" in reads and "mail_draft" not in reads and "notes_create" not in reads)

    # C2 + C3 (with --all: every one-click sign-in in the catalog, through Xod's own sign-in, not just a metadata check)
    from urllib.parse import urlparse as _u
    pairs = [("notion", "notion"), ("linear", "linear.app")]
    if "--all" in sys.argv:
        pairs = [(c["id"], "") for c in connectors.CATALOG if c["kind"] == "oauth"]
    for cid, host in pairs:
        opened.clear()
        await connectors.connect(cid, REDIRECT)
        for _ in range(120):
            if opened:
                break
            await asyncio.sleep(0.25)
        url = opened[0] if opened else ""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        good = ((host in urllib.parse.urlparse(url).netloc if host else bool(url)) and q.get("client_id") and q.get("code_challenge")
                and q.get("redirect_uri", [""])[0] == REDIRECT and q.get("state"))
        check(f"C2 {cid}: registers and opens its sign-in page", bool(good), urllib.parse.urlparse(url).netloc or "no page opened")
        st = next(c for c in connectors.catalog() if c["id"] == cid)
        check(f"C2 {cid}: shows as signing in", st["state"] == "signing_in", st["state"])
        state = q.get("state", [""])[0]
        took = mcp_oauth.finish("not-a-real-code", state)
        st = await wait_state(cid, ("error", "connected", "needs_sign_in"), 60)
        check(f"C3 {cid}: a bad code ends as an error", took and st["state"] == "error", f"{st['state']}: {(st.get('error') or '')[:90]}")
        check(f"C3 {cid}: no tokens saved", not mcp_oauth.has_tokens(cid))

    # C4
    await connectors.connect("github", REDIRECT, token="ghp_not_a_real_token_000000000000000000")
    st = await wait_state("github", ("error", "connected"), 60)
    check("C4 GitHub with a bad token reports it", st["state"] == "error", (st.get("error") or "")[:100])

    # C6
    connectors.disconnect("github")
    check("C6 removing forgets the token", keychain.get_password(mcp_client.KEYCHAIN, "github:bearer_token") is None)

    for c in list(hub.conns.values()):
        await c.stop()


def guard_checks():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app, base_url="http://127.0.0.1:8000")
    r = c.get("/oauth/callback?code=x&state=nothing", headers={"sec-fetch-site": "cross-site"})
    check("C5 provider can return to /oauth/callback", r.status_code == 200 and "expired" in r.text, str(r.status_code))
    r = c.get("/api/connectors", headers={"sec-fetch-site": "cross-site"})
    check("C5 other pages still refuse cross-site", r.status_code == 403, str(r.status_code))
    r = c.get("/oauth/callback?code=x&state=y", headers={"origin": "https://evil.example"})
    check("C5 callback refuses scripted cross-site calls", r.status_code == 403, str(r.status_code))


try:
    asyncio.run(main())
    guard_checks()
finally:
    shutil.rmtree(TMP, ignore_errors=True)

failed = [n for n, ok in results if not ok]
print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
sys.exit(1 if failed else 0)
