"""
Checks hosted MCP servers before they go in (or stay in) Xod's one-click connector catalog (services/connectors.py).

For each address it sends an MCP "initialize" and sorts the answer:
  open     answers without signing in (docs, public data)
  oauth    asks for sign-in and publishes what Xod's one-click sign-in needs: OAuth metadata with dynamic client
           registration (RFC 9728 → RFC 8414 → registration_endpoint), so no developer account or client ID is needed
  needs    asks for sign-in but can't be set up in one click (no registration endpoint: needs your own app or a key)
  down     doesn't answer as an MCP server

  python scripts/check_connectors.py                # the catalog
  python scripts/check_connectors.py URL [URL …]    # candidates
"""
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "xod-check", "version": "1"}}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


async def _json(c: httpx.AsyncClient, url: str) -> Optional[Dict[str, Any]]:
    try:
        r = await c.get(url, headers={"Accept": "application/json"})
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


async def check(url: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        try:
            r = await c.post(url, json=INIT, headers=HEADERS)
        except Exception as e:
            return {"url": url, "kind": "down", "why": f"{type(e).__name__}"}
        if r.status_code == 200 and ("jsonrpc" in r.text or "event:" in r.text):
            return {"url": url, "kind": "open", "why": "answers without signing in"}
        if r.status_code not in (401, 403):
            return {"url": url, "kind": "down", "why": f"HTTP {r.status_code}"}
        # Where the sign-in metadata is: the 401's resource_metadata, else the well-known spots.
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        m = re.search(r'resource_metadata="([^"]+)"', r.headers.get("www-authenticate", ""))
        prm = await _json(c, m.group(1)) if m else None
        prm = prm or await _json(c, f"{origin}/.well-known/oauth-protected-resource{p.path}") or await _json(c, f"{origin}/.well-known/oauth-protected-resource")
        issuers = (prm or {}).get("authorization_servers") or [origin]
        meta = None
        for iss in issuers:
            ip = urlparse(iss)
            io = f"{ip.scheme}://{ip.netloc}"
            path = ip.path.rstrip("/")
            meta = (await _json(c, f"{io}/.well-known/oauth-authorization-server{path}") or await _json(c, f"{io}/.well-known/openid-configuration{path}")
                    or await _json(c, f"{iss.rstrip('/')}/.well-known/oauth-authorization-server"))
            if meta:
                break
        if meta and meta.get("registration_endpoint"):
            return {"url": url, "kind": "oauth", "why": f"sign-in at {urlparse(meta.get('authorization_endpoint', '')).netloc}, registers itself"}
        return {"url": url, "kind": "needs", "why": "sign-in without self-registration (needs your own app or a key)" if meta else "asks for a key or token"}


async def main(urls):
    if not urls:
        from app.services.connectors import CATALOG
        urls = [c["url"] for c in CATALOG if c.get("url")]
    res = await asyncio.gather(*[check(u) for u in urls])
    for r in res:
        print(f"{r['kind']:6} {r['url']:55} {r['why']}")
    return res


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
