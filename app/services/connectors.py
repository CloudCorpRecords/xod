"""
One-click connected apps: a catalog of MCP servers Xod knows how to set up.

Each connector becomes an ordinary entry in mcp_servers.json (so it shows up, is approved and
can be removed like any other server), marked with "connector": <id>. Secrets never go in the
file: OAuth tokens and pasted tokens live in the Keychain.

kinds:
  oauth  hosted server with sign-in in the browser and automatic client registration
  own    hosted server that needs your own OAuth client (Google Workspace)
  token  hosted server signed in with a token you paste (GitHub, until Xod has a registered app)
  open   hosted server that needs no account (public docs and data)
  local  runs on this Mac, no account

Every hosted one is checked before it goes in: scripts/check_connectors.py (it answers as an MCP server over Streamable
HTTP, and an "oauth" one lets Xod register by itself, so no developer account is needed), then
tests/features/run_connectors_test.py --all, which goes through Xod's own sign-in up to each sign-in page. Left out
for now: Figma (refuses clients it hasn't approved), Gamma (refuses Xod's local return address), Asana and Square
(older SSE transport), Box, Slack and HubSpot (need your own app). Each has a group, for the panel.
"""
import sys
from typing import Any, Dict, List, Optional

from app.services import mcp_client, mcp_oauth


def _o(cid: str, name: str, icon: str, group: str, url: str, blurb: str, **kw: Any) -> Dict[str, Any]:
    return {"id": cid, "name": name, "icon": icon, "group": group, "kind": "oauth", "url": url, "blurb": blurb, "trust": "read_only", **kw}


def _open(cid: str, name: str, icon: str, url: str, blurb: str) -> Dict[str, Any]:
    """No account: public docs and data. Nothing of yours to change, so its tools run without asking."""
    return {"id": cid, "name": name, "icon": icon, "group": "Look things up (no account)", "kind": "open", "url": url,
            "trust": "always", "blurb": blurb}


CATALOG: List[Dict[str, Any]] = [
    {"id": "mac", "name": "This Mac", "icon": "laptop", "group": "On this computer",
     "blurb": "Calendar, Reminders, Notes and Mail on this Mac, including Google or iCloud accounts you've added to macOS. Nothing leaves the computer.",
     "kind": "local", "command": "{xod_python}", "args": ["-m", "app.connectors.mac"], "trust": "read_only",
     "only_on": "darwin", "unavailable_why": "It talks to the Mac's own Calendar, Reminders, Notes and Mail apps, so it only works on a Mac."},
    _o("notion", "Notion", "notebook-pen", "Work and tasks", "https://mcp.notion.com/mcp", "Search, read and update your Notion pages and databases."),
    _o("linear", "Linear", "list-checks", "Work and tasks", "https://mcp.linear.app/mcp", "Find, create and update Linear issues and projects."),
    _o("atlassian", "Jira and Confluence", "kanban-square", "Work and tasks", "https://mcp.atlassian.com/v1/mcp",
       "Atlassian's server: search and update Jira issues and Confluence pages."),
    _o("todoist", "Todoist", "check-square", "Work and tasks", "https://ai.todoist.net/mcp", "Your Todoist tasks and projects: read, add, complete."),
    _o("clickup", "ClickUp", "list-todo", "Work and tasks", "https://mcp.clickup.com/mcp", "Tasks, docs and spaces in ClickUp."),
    _o("monday", "monday.com", "layout-grid", "Work and tasks", "https://mcp.monday.com/mcp", "Boards, items and updates in monday.com."),
    _o("airtable", "Airtable", "table", "Work and tasks", "https://mcp.airtable.com/mcp", "Read and update your Airtable bases."),
    _o("zapier", "Zapier", "zap", "Work and tasks", "https://mcp.zapier.com/api/mcp/mcp",
       "Reach the 8,000 apps Zapier connects (Gmail, Slack, Sheets, HubSpot…): pick which actions Xod may use in Zapier."),
    _o("dropbox", "Dropbox", "package", "Files", "https://mcp.dropbox.com/mcp", "Find, read and organize files in your Dropbox."),
    _o("canva", "Canva", "palette", "Design and content", "https://mcp.canva.com/mcp", "Find and create Canva designs."),
    _o("webflow", "Webflow", "globe", "Design and content", "https://mcp.webflow.com/mcp", "Your Webflow sites, pages and CMS."),
    _o("wix", "Wix", "globe", "Design and content", "https://mcp.wix.com/mcp", "Your Wix sites and their content."),
    _o("stripe", "Stripe", "credit-card", "Money", "https://mcp.stripe.com", "Customers, payments, invoices and products in Stripe."),
    _o("paypal", "PayPal", "wallet", "Money", "https://mcp.paypal.com/mcp", "Invoices, orders and transactions in PayPal."),
    _o("intercom", "Intercom", "message-circle", "Customers", "https://mcp.intercom.com/mcp", "Conversations and contacts in Intercom."),
    {"id": "github", "name": "GitHub", "icon": "github", "group": "Developers",
     "blurb": "Repos, issues, pull requests and code search.",
     "kind": "token", "url": "https://api.githubcopilot.com/mcp/", "trust": "read_only",
     "token_help": "Make a fine-grained token at github.com/settings/personal-access-tokens with access to the repos you want, then paste it here. It goes into your Keychain.",
     "token_url": "https://github.com/settings/personal-access-tokens/new",
     "from_cli": "gh"},            # or one click: the sign-in the GitHub CLI (gh) already has on this computer
    _o("sentry", "Sentry", "bug", "Developers", "https://mcp.sentry.dev/mcp", "Errors and issues in Sentry, with their stack traces."),
    _o("vercel", "Vercel", "triangle", "Developers", "https://mcp.vercel.com", "Projects, deployments and logs on Vercel."),
    _o("netlify", "Netlify", "cloud", "Developers", "https://netlify-mcp.netlify.app/mcp", "Sites, deploys and settings on Netlify."),
    _o("supabase", "Supabase", "database", "Developers", "https://mcp.supabase.com/mcp", "Your Supabase projects: tables, queries, logs."),
    _o("neon", "Neon", "database", "Developers", "https://mcp.neon.tech/mcp", "Neon Postgres projects, branches and queries."),
    _o("prisma", "Prisma Postgres", "database", "Developers", "https://mcp.prisma.io/mcp", "Prisma Postgres databases."),
    _o("cloudflare", "Cloudflare", "cloud", "Developers", "https://mcp.cloudflare.com/mcp", "Your Cloudflare account: Workers, DNS, settings."),
    _open("huggingface", "Hugging Face", "smile", "https://huggingface.co/mcp", "Search models, datasets, papers and Spaces on Hugging Face."),
    _open("mslearn", "Microsoft Learn", "book-open", "https://learn.microsoft.com/api/mcp", "Microsoft's official docs: Windows, Office, Azure, .NET, PowerShell."),
    _open("context7", "Context7", "library", "https://mcp.context7.com/mcp", "Up-to-date documentation for thousands of code libraries."),
    _open("deepwiki", "DeepWiki", "git-branch", "https://mcp.deepwiki.com/mcp", "Ask about any public GitHub repository: how it works, where things are."),
    _open("cloudflare-docs", "Cloudflare docs", "book-open", "https://docs.mcp.cloudflare.com/mcp", "Cloudflare's documentation."),
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
    {"id": "google-drive", "name": "Google Drive (official)", "icon": "hard-drive",
     "blurb": "Google's own Drive server, read only: Xod can find your résumé and other files there to fill in forms. Needs your own Google Cloud OAuth client.",
     "kind": "own", "url": "https://drivemcp.googleapis.com/mcp/v1", "trust": "read_only", "advanced": True,
     "scope": "https://www.googleapis.com/auth/drive.readonly",
     "setup_url": "https://developers.google.com/workspace/drive/api/guides/configure-mcp-server"},
]
_BY_ID = {c["id"]: c for c in CATALOG}


_gh_cache: Dict[str, Any] = {"at": 0.0, "account": None}


def gh_account() -> Optional[str]:
    """The GitHub account the GitHub CLI (gh) is signed in to on this computer, if any (checked at most once a minute)."""
    import shutil
    import subprocess
    import time
    if time.time() - _gh_cache["at"] < 60:
        return _gh_cache["account"]
    account = None
    try:
        from app.services.agent_clis import search_path, _env
        gh = shutil.which("gh", path=search_path())
        if gh:
            r = subprocess.run([gh, "api", "user", "--jq", ".login"], capture_output=True, text=True, timeout=15,
                               env=_env(), stdin=subprocess.DEVNULL)
            if r.returncode == 0 and r.stdout.strip():
                account = r.stdout.strip().splitlines()[0][:100]
    except Exception:
        account = None
    _gh_cache.update(at=time.time(), account=account)
    return account


def _gh_token() -> Optional[str]:
    """The GitHub CLI's own token, for connecting GitHub with one click (the person's click is the OK)."""
    import shutil
    import subprocess
    from app.services.agent_clis import search_path, _env
    gh = shutil.which("gh", path=search_path())
    if not gh:
        return None
    r = subprocess.run([gh, "auth", "token", "--hostname", "github.com"], capture_output=True, text=True, timeout=15,
                       env=_env(), stdin=subprocess.DEVNULL)
    tok = r.stdout.strip()
    return tok if r.returncode == 0 and tok and " " not in tok and len(tok) < 400 else None


def catalog() -> List[Dict[str, Any]]:
    servers = {s["name"]: s for s in mcp_client.mcp_hub.status()}
    out = []
    for c in CATALOG:
        s = servers.get(c["id"])
        state = "off"
        if s:
            state = ("connected" if s["connected"] else "signing_in" if s["signing_in"]
                     else "needs_sign_in" if s["needs_sign_in"] else "error" if s["error"] else "connecting")
        unavailable = c.get("unavailable_why") if c.get("only_on") and not sys.platform.startswith(c["only_on"]) else None
        out.append({**{k: v for k, v in c.items() if k not in ("command", "args")}, "state": state,
                    "error": _friendly(c, s and s["error"]), "tools": len(s["tools"]) if s else 0, "unavailable": unavailable,
                    "group": c.get("group") or ("More (advanced)" if c.get("advanced") else "Other")})
        if c.get("from_cli") == "gh":
            out[-1]["cli_account"] = gh_account()
            out[-1]["shared_with_agents"] = share_with_agents()
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
                  client_id: Optional[str] = None, client_secret: Optional[str] = None, use_cli: bool = False) -> Dict[str, Any]:
    """Adds (or re-signs-in) a connector and connects it. May open a sign-in page in the browser. use_cli: GitHub
    with the sign-in the GitHub CLI already has (no token to make and paste)."""
    c = _BY_ID.get(cid)
    if not c:
        return {"success": False, "error": "Unknown connector."}
    if use_cli:
        if c.get("from_cli") != "gh":
            return {"success": False, "error": f"{c['name']} can't use a command-line sign-in."}
        import asyncio
        token = await asyncio.to_thread(_gh_token)
        if not token:
            return {"success": False, "needs_token": True,
                    "error": "The GitHub CLI isn't signed in on this computer. Run `gh auth login` once, or paste a token."}
    if c.get("only_on") and not sys.platform.startswith(c["only_on"]):
        return {"success": False, "error": c.get("unavailable_why") or "Not available on this computer."}
    from app.services import keychain
    cfg = mcp_client.load_config()
    spec = cfg.get(cid) or {"enabled": True, "trust": c.get("trust", "ask"), "connector": cid}
    spec["enabled"] = True
    if c["kind"] == "local":
        spec.update(command=c["command"], args=c["args"])
    elif c["kind"] == "open":
        spec["url"] = c["url"]           # no account: a plain connection
        spec.pop("auth", None)
    else:
        spec["url"] = c["url"]
        spec["auth"] = "bearer" if c["kind"] == "token" else "oauth"
        if c.get("scope"):
            spec["scope"] = c["scope"]
    if c["kind"] == "token":
        if token:
            keychain.set_password(mcp_client.KEYCHAIN, f"{cid}:bearer_token", token.strip())
        elif not keychain.get_password(mcp_client.KEYCHAIN, f"{cid}:bearer_token"):
            return {"success": False, "needs_token": True, "error": "Paste a token first."}
    if c["kind"] == "own":
        if client_id:
            mcp_oauth.set_own_client(cid, client_id, client_secret or "", redirect_uri)
        elif not keychain.get_password(mcp_client.KEYCHAIN, f"{cid}:oauth_client"):
            return {"success": False, "needs_client": True, "error": "Enter your Google OAuth client ID first."}
    cfg[cid] = spec
    mcp_client._save_config(cfg)
    # Signing in can take minutes in the browser: connect in the background; the panel polls catalog().
    import asyncio
    _tasks[cid] = asyncio.create_task(mcp_client.mcp_hub.connect(cid, interactive=True, redirect_uri=redirect_uri))
    return {"success": True, "started": True}


_tasks: Dict[str, Any] = {}


# ------------------------------------------------------------------ sharing connectors with agent jobs
SHAREABLE = ("github",)       # hosted, signed in with a token Xod holds: an agent job can be handed the same connection


def share_with_agents(on: Optional[bool] = None) -> bool:
    """Whether Crew jobs (Claude Code, Muse) get Xod's GitHub connection too. On unless you turn it off."""
    from app.services.storage import get_setting, set_setting
    if on is not None:
        set_setting("crew_share_connectors", "1" if on else "0")
    return (get_setting("crew_share_connectors", "1") or "1") != "0"


def for_agents() -> Dict[str, Dict[str, Any]]:
    """The connectors to hand an agent job, as {name: {"url", "token"}}: connected, shareable ones, when sharing is on.
    The token goes to the agent in memory (an environment variable or its protocol), never into a file."""
    if not share_with_agents():
        return {}
    from app.services import keychain
    cfg = mcp_client.load_config()
    out: Dict[str, Dict[str, Any]] = {}
    for cid in SHAREABLE:
        spec = cfg.get(cid) or {}
        if not spec.get("enabled") or not spec.get("url"):
            continue
        tok = keychain.get_password(mcp_client.KEYCHAIN, f"{cid}:bearer_token")
        if tok:
            out[cid] = {"url": spec["url"], "token": tok}
    return out


def disconnect(cid: str) -> bool:
    return mcp_client.remove_server(cid)
