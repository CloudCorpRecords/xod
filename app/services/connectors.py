"""
One-click connected apps: a catalog of MCP servers Xod knows how to set up.

Each connector becomes an ordinary entry in mcp_servers.json (so it shows up, is approved and
can be removed like any other server), marked with "connector": <id>. Secrets never go in the
file: OAuth tokens and pasted tokens live in the Keychain.

kinds:
  oauth  hosted server with sign-in in the browser and automatic client registration
  own    hosted server that needs your own OAuth client (Google Workspace)
  token  hosted server signed in with a token you paste (GitHub, until Xod has a registered app)
  local  runs on this Mac, no account
"""
from typing import Any, Dict, List, Optional

from app.services import mcp_client, mcp_oauth

CATALOG: List[Dict[str, Any]] = [
    {"id": "mac", "name": "This Mac", "icon": "laptop",
     "blurb": "Calendar, Reminders, Notes and Mail on this Mac, including Google or iCloud accounts you've added to macOS. Nothing leaves the computer.",
     "kind": "local", "command": "{xod_python}", "args": ["-m", "app.connectors.mac"], "trust": "read_only"},
    {"id": "notion", "name": "Notion", "icon": "notebook-pen",
     "blurb": "Search, read and update your Notion pages and databases.",
     "kind": "oauth", "url": "https://mcp.notion.com/mcp", "trust": "read_only"},
    {"id": "linear", "name": "Linear", "icon": "list-checks",
     "blurb": "Find, create and update Linear issues and projects.",
     "kind": "oauth", "url": "https://mcp.linear.app/mcp", "trust": "read_only"},
    {"id": "github", "name": "GitHub", "icon": "github",
     "blurb": "Repos, issues, pull requests and code search.",
     "kind": "token", "url": "https://api.githubcopilot.com/mcp/", "trust": "read_only",
     "token_help": "Make a fine-grained token at github.com/settings/personal-access-tokens with access to the repos you want, then paste it here. It goes into your Keychain.",
     "token_url": "https://github.com/settings/personal-access-tokens/new"},
    {"id": "google-calendar", "name": "Google Calendar (official)", "icon": "calendar",
     "blurb": "Google's own Calendar server. Needs your own Google Cloud OAuth client; most people should use This Mac instead.",
     "kind": "own", "url": "https://calendarmcp.googleapis.com/mcp/v1", "trust": "read_only", "advanced": True,
     "scope": "https://www.googleapis.com/auth/calendar.events.readonly https://www.googleapis.com/auth/calendar.calendarlist.readonly",
     "setup_url": "https://developers.google.com/workspace/calendar/api/guides/configure-mcp-server"},
    {"id": "gmail", "name": "Gmail (official)", "icon": "mail",
     "blurb": "Google's own Gmail server. Needs your own Google Cloud OAuth client; most people should use This Mac instead.",
     "kind": "own", "url": "https://gmailmcp.googleapis.com/mcp/v1", "trust": "read_only", "advanced": True,
     "scope": "https://www.googleapis.com/auth/gmail.readonly",
     "setup_url": "https://developers.google.com/workspace/guides/configure-mcp-servers"},
]
_BY_ID = {c["id"]: c for c in CATALOG}


def catalog() -> List[Dict[str, Any]]:
    servers = {s["name"]: s for s in mcp_client.mcp_hub.status()}
    out = []
    for c in CATALOG:
        s = servers.get(c["id"])
        state = "off"
        if s:
            state = ("connected" if s["connected"] else "signing_in" if s["signing_in"]
                     else "needs_sign_in" if s["needs_sign_in"] else "error" if s["error"] else "connecting")
        out.append({**{k: v for k, v in c.items() if k not in ("command", "args")}, "state": state,
                    "error": _friendly(c, s and s["error"]), "tools": len(s["tools"]) if s else 0})
    return out


def _friendly(c: Dict[str, Any], error: Optional[str]) -> Optional[str]:
    if not error:
        return None
    low = error.lower()
    if c["kind"] == "token" and ("401" in low or "unauthorized" in low or "error response" in low):
        return f"{c['name']} didn't accept that token. Check it has access, or make a new one."
    if "connecterror" in low or "name resolution" in low or "timed out" in low:
        return f"Couldn't reach {c['name']}. Check your internet connection."
    if "oauthflowerror" in low or "sign-in was cancelled" in low:
        return "The sign-in didn't finish. Try again."
    if "not allowed xod" in low or "automation" in low:
        return error
    return error


async def connect(cid: str, redirect_uri: str, token: Optional[str] = None,
                  client_id: Optional[str] = None, client_secret: Optional[str] = None) -> Dict[str, Any]:
    """Adds (or re-signs-in) a connector and connects it. May open a sign-in page in the browser."""
    c = _BY_ID.get(cid)
    if not c:
        return {"success": False, "error": "Unknown connector."}
    import keyring
    cfg = mcp_client.load_config()
    spec = cfg.get(cid) or {"enabled": True, "trust": c.get("trust", "ask"), "connector": cid}
    spec["enabled"] = True
    if c["kind"] == "local":
        spec.update(command=c["command"], args=c["args"])
    else:
        spec["url"] = c["url"]
        spec["auth"] = "bearer" if c["kind"] == "token" else "oauth"
        if c.get("scope"):
            spec["scope"] = c["scope"]
    if c["kind"] == "token":
        if token:
            keyring.set_password(mcp_client.KEYCHAIN, f"{cid}:bearer_token", token.strip())
        elif not keyring.get_password(mcp_client.KEYCHAIN, f"{cid}:bearer_token"):
            return {"success": False, "needs_token": True, "error": "Paste a token first."}
    if c["kind"] == "own":
        if client_id:
            mcp_oauth.set_own_client(cid, client_id, client_secret or "", redirect_uri)
        elif not keyring.get_password(mcp_client.KEYCHAIN, f"{cid}:oauth_client"):
            return {"success": False, "needs_client": True, "error": "Enter your Google OAuth client ID first."}
    cfg[cid] = spec
    mcp_client._save_config(cfg)
    # Signing in can take minutes in the browser: connect in the background; the panel polls catalog().
    import asyncio
    _tasks[cid] = asyncio.create_task(mcp_client.mcp_hub.connect(cid, interactive=True, redirect_uri=redirect_uri))
    return {"success": True, "started": True}


_tasks: Dict[str, Any] = {}


def disconnect(cid: str) -> bool:
    return mcp_client.remove_server(cid)
