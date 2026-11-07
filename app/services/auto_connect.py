"""
Auto-Connect: Xod finds the AIs already on this computer and connects
itself, in one click.

Direction (2026-11-06): it should be super easy, one click, or
the person just tells their AI "I have these already installed, can you
go and connect me" and Xod goes ahead and automatically connects,
looks for what is installed, connects itself, super easy if they are
already logged in.

Two halves:

  Xod side (this module). One "Connect my AIs" action sweeps the
  machine Xod runs on: DETECT the assistant footprints that are
  actually here (Claude Desktop, Claude Code, Codex / the ChatGPT
  desktop app), read only a signed-in signal where one is readable
  without touching any credential value, then CONNECT each signed-in
  assistant in the same action: merge Xod's MCP entry into that
  assistant's own config (backup first, idempotent, the exact merge
  shapes proven in MULTI-ASSISTANT-CONNECT-TEST-2026-11-06), mint a
  signed connection offer internally and record the connection in
  the muse-connect registry as pending verification (the v1 offer
  machinery stays the audit trail), then VERIFY where a client can
  be pinged headlessly and mark the connection live, or "verify in
  app" for a desktop client whose attach happens on first chat
  (observed on the Razer, 2026-11-06: Claude Desktop does not spawn
  configured servers at launch). A signed-out assistant is reported
  "sign in first" and never logged into; nothing here types a
  password or reads a secret value, ever.

  Assistant side (the discovery doc). Xod publishes a stable local
  discovery document at GET /api/connect/discovery and drops a copy
  in the family shared folder next to the tree-inbox
  (xod-connect-discovery.json), so an AI on this machine can connect
  itself without the person pasting anything: find the endpoint,
  fetch or mint an offer, accept, verify. The paste flow from v1
  stays as the fallback.

Trust note: auto-connect wires MCP and registers a connection on the
person's own machine, signed with the same device identity as every
other offer. Xod's task approval lanes are untouched (a connected
assistant is a supervised executor, never a silent one) and every
connection stays revocable from either side, exactly as in v1.

State lives at DATA_DIR/auto-connect/state.json:
  assistants   assistant id -> {state, offer_id, detail, updated_at}
  last_run     ISO time of the last sweep
Connection records themselves live in the muse-connect store
(services/muse_connect.py), one per assistant, so the Family panel
and the revoke path see them like any other connection.
"""
import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.paths import APP_ROOT, DATA_DIR
from app.services import muse_connect as mx

DISCOVERY_TYPE = "xod-connect-discovery"
DISCOVERY_FILE = "xod-connect-discovery.json"

# Connection states an auto-connect record can be in. The first four
# are the sweep's own words; "live" is shared with muse_connect.
STATE_NOT_FOUND = "not_found"
STATE_DETECTED = "detected"
STATE_SIGN_IN_FIRST = "sign_in_first"
STATE_PENDING = "pending-verification"
STATE_NEEDS_FIRST_CHAT = "needs-first-chat"
STATE_LIVE = "live"

# The words the Family panel shows for each state. Deliberately few.
DISPLAY = {
    STATE_NOT_FOUND: "Not found",
    STATE_DETECTED: "Detected",
    STATE_SIGN_IN_FIRST: "Detected, sign in first",
    STATE_PENDING: "Connected, verify in app",
    STATE_NEEDS_FIRST_CHAT: "Connected, verify in app",
    STATE_LIVE: "Connected",
}


# ---------------------------------------------------------------- seams
# Small wrappers so tests can stand in a fixture home and OS without
# touching the real machine, matching the muse_connect style.

def _home() -> Path:
    return Path.home()


def _system() -> str:
    return platform.system()


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _shared_folder() -> Optional[Path]:
    """The family shared folder (parent of the tree-inbox), where
    family-rules.json and the discovery copy live."""
    inbox = mx._inbox_dir()
    return inbox.parent if inbox is not None else None


# ---------------------------------------------------------------- detect

def _marker_in_file(path: Path, marker: bytes) -> bool:
    """True when a name marker appears in a file's bytes. Used only
    for cookie-store NAME checks (for example "sessionKey"): the
    bytes are scanned for the name, no value is ever extracted,
    decoded, or returned."""
    try:
        return marker in path.read_bytes()
    except OSError:
        return False


def _key_in_text(path: Path, key: str) -> bool:
    """True when a JSON key NAME appears in a text file (for example
    "oauthAccount" in ~/.claude.json). Names only, never values."""
    try:
        return f'"{key}"' in path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _claude_desktop_paths(home: Path, system: str) -> Tuple[Path, List[Path]]:
    """(config path, extra install-evidence paths) for Claude
    Desktop. Windows carries three layouts: the classic per-user
    install, the Store package's virtualized Roaming, and the
    current Store build's real data home at
    AppData/Local/Claude-Data/<package family>/ (the layout the
    Razer actually uses, found by live proof 2026-11-06)."""
    if system == "Darwin":
        base = home / "Library" / "Application Support" / "Claude"
        return base / "claude_desktop_config.json", [Path("/Applications/Claude.app")]
    if system == "Windows":
        store = (home / "AppData" / "Local" / "Packages"
                 / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming" / "Claude")
        extras = [store / "claude_desktop_config.json"]
        data_root = home / "AppData" / "Local" / "Claude-Data"
        try:
            extras += sorted(data_root.glob("*/claude_desktop_config.json"))
        except OSError:
            pass
        classic = home / "AppData" / "Roaming" / "Claude"
        return classic / "claude_desktop_config.json", extras
    base = home / ".config" / "Claude"
    return base / "claude_desktop_config.json", []


def _marker_in_copy(path: Path) -> Optional[bool]:
    """Marker check through a temp copy (a running Chromium app
    locks its cookie store, and a copy can sometimes still be
    read). None when even the copy cannot be read."""
    import tempfile
    try:
        fd, tmp = tempfile.mkstemp(prefix="xod-ck-")
        os.close(fd)
        shutil.copy2(path, tmp)
        try:
            return _marker_in_file(Path(tmp), b"sessionKey")
        finally:
            os.unlink(tmp)
    except OSError:
        return None


def _cookie_signal(config_path: Path, extra_paths: List[Path]) -> Optional[bool]:
    """Claude's sign-in signal: a sessionKey cookie NAME in the
    app's own cookie store, beside whichever config layout exists.
    The Claude-Data layout nests its Chromium stores a few levels
    under the config's folder, so that folder is walked (names
    only, depth-bounded).

    True: the name is there. False: a store was readable and the
    name is not in it (or no store exists yet). None: a store
    exists but the running app holds it locked, so the signal
    cannot be read from here; unknown, never guessed."""
    stores: List[Path] = []
    for cfg in [config_path] + list(extra_paths):
        stores += [cfg.parent / "Cookies", cfg.parent / "Network" / "Cookies"]
        if "Claude-Data" in cfg.parts:
            base = cfg.parent
            depth0 = len(base.parts)
            try:
                for dirpath, dirnames, filenames in os.walk(base):
                    if len(Path(dirpath).parts) - depth0 > 3:
                        dirnames[:] = []
                        continue
                    if "Cookies" in filenames:
                        stores.append(Path(dirpath) / "Cookies")
            except OSError:
                pass
    readable = 0
    locked = 0
    for store in stores:
        if not store.is_file():
            continue
        try:
            if b"sessionKey" in store.read_bytes():
                return True
            readable += 1
            continue
        except OSError:
            pass
        copied = _marker_in_copy(store)
        if copied is True:
            return True
        if copied is False:
            readable += 1
        else:
            locked += 1
    if readable:
        return False
    if locked:
        return None
    return False


def detect_one(assistant_id: str, home: Path, system: str) -> Dict[str, Any]:
    """One assistant's footprint: installed, a signed-in signal when
    one is readable, and the config file a connect would merge into.
    Presence and names only; no credential value is ever read."""
    if assistant_id == "claude-desktop":
        config, extra = _claude_desktop_paths(home, system)
        installed = (config.parent.is_dir() or config.is_file()
                     or any(p.exists() for p in extra)
                     or (system == "Windows"
                         and (home / "AppData" / "Local" / "Claude-Data").is_dir()))
        signal = _cookie_signal(config, extra) if installed else False
        return {"id": assistant_id, "name": "Claude Desktop",
                "kind": "json", "config_path": str(config),
                "extra_config_paths": [str(p) for p in extra],
                "installed": installed,
                "signed_in": signal}
    if assistant_id == "claude-code":
        cfg = home / ".claude.json"
        installed = cfg.is_file() or (home / ".claude").is_dir()
        return {"id": assistant_id, "name": "Claude Code",
                "kind": "json", "config_path": str(cfg),
                "extra_config_paths": [],
                "installed": installed,
                "signed_in": installed and _key_in_text(cfg, "oauthAccount")}
    if assistant_id == "codex":
        base = home / ".codex"
        cfg = base / "config.toml"
        app = False
        if system == "Darwin":
            app = Path("/Applications/ChatGPT.app").exists()
        elif system == "Windows":
            try:
                app = any((home / "AppData" / "Local" / "Packages")
                          .glob("OpenAI.Codex*"))
            except OSError:
                app = False
        installed = base.is_dir() or app
        return {"id": assistant_id, "name": "ChatGPT (Codex)",
                "kind": "toml", "config_path": str(cfg),
                "extra_config_paths": [], "app_installed": app,
                "installed": installed,
                "signed_in": installed and (base / "auth.json").is_file()}
    raise ValueError(f"unknown assistant {assistant_id}")


ASSISTANT_IDS = ("claude-desktop", "claude-code", "codex")


def detect() -> List[Dict[str, Any]]:
    home, system = _home(), _system()
    return [detect_one(aid, home, system) for aid in ASSISTANT_IDS]


# ---------------------------------------------------------------- merge

def mcp_entry(port: Optional[int] = None) -> Dict[str, Any]:
    """Xod's MCP server entry, the exact shape GET
    /api/mcp/self-config hands out (this install's python, this
    checkout, the local backend URL)."""
    if port is None:
        try:
            port = int(os.environ.get("XOD_BACKEND_PORT") or 8000)
        except ValueError:
            port = 8000
    return {"command": sys.executable,
            "args": ["-m", "app.mcp_server"],
            "cwd": str(APP_ROOT),
            "env": {"XOD_URL": f"http://127.0.0.1:{port}"}}


def _backup(path: Path) -> Optional[Path]:
    """Copy a config file aside before the first change to it.
    Returns the backup path, or None when there was nothing to
    copy. One backup per file per day is enough of an audit trail;
    an existing same-day backup is kept, not overwritten."""
    if not path.is_file():
        return None
    stamp = time.strftime("%Y-%m-%d", time.localtime())
    bak = path.with_name(path.name + f".bak-auto-connect-{stamp}")
    if bak.exists():
        return bak
    try:
        shutil.copy2(path, bak)
        return bak
    except OSError:
        return None


def merge_json_config(path: Path, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Merge the xod entry into a JSON MCP config (Claude Desktop's
    claude_desktop_config.json, Claude Code's ~/.claude.json).
    Idempotent: an identical entry already there is a no-op and is
    reported as already connected, never duplicated. An entry that
    differs is updated in place. Backup first, always."""
    data: Dict[str, Any] = {}
    existed = path.is_file()
    if existed:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"ok": False, "error": "config is not a JSON object"}
        except Exception:
            return {"ok": False, "error": "config is not readable JSON; left untouched"}
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers
    if servers.get("xod") == entry:
        return {"ok": True, "already": True, "changed": False, "backup": None}
    backup = _backup(path) if existed else None
    servers["xod"] = entry
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"could not write config: {e}"}
    return {"ok": True, "already": False, "changed": True,
            "backup": str(backup) if backup else None}


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def merge_toml_config(path: Path, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Merge the xod entry into Codex's ~/.codex/config.toml as a
    [mcp_servers.xod] table (the route today's test proved; per-call
    -c overrides silently registered nothing). Presence of the
    table is already connected: the section is never duplicated.
    Backup first, always."""
    text = ""
    existed = path.is_file()
    if existed:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            return {"ok": False, "error": f"could not read config: {e}"}
    if re.search(r"(?m)^\[mcp_servers\.xod\]\s*$", text):
        return {"ok": True, "already": True, "changed": False, "backup": None}
    env = entry.get("env") or {}
    lines = ["", "[mcp_servers.xod]",
             f"command = {_toml_str(entry['command'])}",
             "args = [" + ", ".join(_toml_str(a) for a in entry["args"]) + "]",
             f"cwd = {_toml_str(entry['cwd'])}",
             "", "[mcp_servers.xod.env]"]
    for key in sorted(env):
        lines.append(f"{key} = {_toml_str(str(env[key]))}")
    lines.append(f"PYTHONPATH = {_toml_str(entry['cwd'])}")
    block = "\n".join(lines) + "\n"
    backup = _backup(path) if existed else None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(block)
    except OSError as e:
        return {"ok": False, "error": f"could not write config: {e}"}
    return {"ok": True, "already": False, "changed": True,
            "backup": str(backup) if backup else None}


def merge_config(det: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
    """Merge into every config location an assistant actually uses.
    Claude Desktop on Windows may live in the Store layout instead
    of the classic one; when the Store config file exists it is a
    merge target too."""
    primary = Path(det["config_path"])
    extras = [Path(p) for p in det.get("extra_config_paths") or []
              if Path(p).is_file()]
    if extras and not primary.is_file():
        # The install actually lives in the other layout (the
        # Windows Store package): merge there, do not conjure a
        # config file in a layout nothing reads.
        targets = extras
    else:
        targets = [primary] + extras
    results = []
    for target in targets:
        if det["kind"] == "toml":
            results.append(merge_toml_config(target, entry))
        else:
            results.append(merge_json_config(target, entry))
    if any(not r["ok"] for r in results):
        bad = next(r for r in results if not r["ok"])
        return {"ok": False, "error": bad["error"]}
    return {"ok": True,
            "already": all(r["already"] for r in results),
            "changed": any(r["changed"] for r in results),
            "backup": next((r["backup"] for r in results if r.get("backup")), None)}


# ---------------------------------------------------------------- verify

async def _ping(entry: Dict[str, Any]) -> Tuple[List[str], Any]:
    """The reference MCP client pattern from today's test: spawn
    app.mcp_server with exactly the merged entry, initialize, list
    tools, and call list_routines through to the live backend."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (entry.get("env") or {}).items()})
    params = StdioServerParameters(command=entry["command"],
                                   args=list(entry["args"]),
                                   cwd=entry.get("cwd"), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("list_routines", {})
            return [t.name for t in tools.tools], result


def verify_entry(entry: Dict[str, Any], timeout: float = 25.0) -> Tuple[bool, str]:
    """(ok, detail). Proves the merged wiring reaches the live Xod
    backend. A failure is reported, never smoothed over."""
    try:
        names, _ = asyncio.run(asyncio.wait_for(_ping(entry), timeout=timeout))
    except Exception as e:
        return False, f"MCP ping failed: {type(e).__name__}: {e}"
    if "list_routines" not in names:
        return False, f"MCP server answered without the expected tools: {names}"
    return True, f"MCP transport verified ({len(names)} tools, list_routines answered)"


# The sweep calls this name so tests can stand in a canned verdict.
_verify: Callable[[Dict[str, Any]], Tuple[bool, str]] = verify_entry


# ---------------------------------------------------------------- store

def _store_path() -> Path:
    return DATA_DIR / "auto-connect" / "state.json"


def _load_store() -> Dict[str, Any]:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("assistants", {})
            return data
    except Exception:
        pass
    return {"assistants": {}, "last_run": None}


def _save_store(store: Dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=1), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- discovery

def discovery_doc(port: Optional[int] = None) -> Dict[str, Any]:
    """The stable local discovery document. Everything an AI on
    this machine needs to connect itself with no paste: where the
    backend is, which endpoints mint and check offers, the MCP
    entry, and the steps in order."""
    entry = mcp_entry(port)
    base = entry["env"]["XOD_URL"]
    manifest, _summary = mx.manifest_payload()
    return {
        "type": DISCOVERY_TYPE,
        "version": 1,
        "generated_at": _iso(_now()),
        "xod": {"backend": base, "manifest_hash":
                hashlib.sha256(mx.mc.canonical(manifest)).hexdigest()},
        "endpoints": {
            "discovery": f"{base}/api/connect/discovery",
            "self_config": f"{base}/api/mcp/self-config",
            "auto_connect_status": f"{base}/api/auto-connect",
            "auto_connect_run": f"{base}/api/auto-connect/run",
            "offer": f"{base}/api/muse-connect/offer",
            "connect_status": f"{base}/api/muse-connect",
            "revoke": f"{base}/api/muse-connect/revoke",
        },
        "mcp_entry": {"xod": entry},
        "for_an_ai": [
            "You are an AI assistant on the same machine as a Xod "
            "install. Your human wants you connected to it. Do the "
            "whole connection yourself; do not ask them to paste "
            "anything.",
            "1. GET the self_config endpoint above (or use mcp_entry "
            "here) and merge the xod entry into your own MCP config, "
            "exactly as given. Keep any other entries. If your "
            "config already holds an identical xod entry, you are "
            "already wired; do not duplicate it.",
            "2. POST to the offer endpoint to mint a signed "
            "connection offer, or POST to auto_connect_run and let "
            "Xod do the whole sweep itself. Verify any offer's "
            "signature, code, and expiry before trusting it (the "
            "tree-connect CLI does this: verify, accept, sync).",
            "3. Accept: register the connection and send the hello "
            "on the rail the offer names (family tree-inbox or Agent "
            "Mail). Read the receipt back; connected means the "
            "state at BOTH ends reads live, not that a call "
            "returned ok.",
            "4. After connecting, read family-rules.json from the "
            "family shared folder (the offer names its hash). The "
            "rules scoped to your kind apply from then on.",
            "5. Xod's approval lanes still apply to every task you "
            "send. A step that needs its human's approval waits; "
            "waiting is a state, not a failure. Either side can "
            "revoke at any time (revoke endpoint, or the tree-connect "
            "CLI); check state before sending a task.",
        ],
        "fallback": "If no endpoint answers, Xod may not be running. "
                    "The paste flow still works: ask your human to "
                    "click Connect to Muse in Xod's Family panel "
                    "and paste you the block.",
    }


def drop_discovery_file(port: Optional[int] = None) -> List[str]:
    """Drop the discovery doc in the family shared folder, next to
    the tree-inbox, so a local AI finds it by looking at the folder
    it already watches. Best effort; returns the paths written."""
    doc = discovery_doc(port)
    written = []
    folder = _shared_folder()
    targets = []
    if folder is not None:
        targets.append(folder / DISCOVERY_FILE)
        inbox = mx._inbox_dir()
        if inbox is not None:
            targets.append(inbox / DISCOVERY_FILE)
    for target in targets:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            written.append(str(target))
        except OSError:
            continue
    return written


# ---------------------------------------------------------------- sweep

def _record(store_rec: Dict[str, Any], state: str, detail: str,
            offer_id: Optional[str] = None) -> Dict[str, Any]:
    store_rec.update({"state": state, "detail": detail,
                      "updated_at": _iso(_now())})
    if offer_id:
        store_rec["offer_id"] = offer_id
    return store_rec


def _ensure_offer(det: Dict[str, Any],
                  rec: Dict[str, Any]) -> Tuple[Optional[str], str, bool]:
    """Mint a signed offer for this assistant and register the
    connection in the muse-connect store as pending verification,
    once. A later sweep reuses the same record (revoked ones are
    left alone: ending a connection is a human decision, the sweep
    never resurrects it). Returns (offer_id, note, ended)."""
    existing = rec.get("offer_id")
    if existing:
        view = mx.status()
        conn = next((c for c in view["connections"]
                     if c["offer_id"] == existing), None)
        if conn is not None:
            if conn["state"] == "revoked":
                return existing, "connection was revoked; left ended", True
            return existing, "offer already recorded", False
    try:
        offer = mx.build_offer()
    except Exception as e:
        return None, (f"offer not minted from here ({type(e).__name__}); "
                      "the in-app sweep signs with the device key"), False
    mx.record_connection(
        offer,
        {"name": det["name"], "kind": "auto-connect",
         "assistant": det["id"]},
        STATE_PENDING)
    return offer["offer_id"], "signed offer recorded as the audit trail", False


def run_sweep(port: Optional[int] = None) -> Dict[str, Any]:
    """The one click. Detect, connect every signed-in assistant,
    verify what can be pinged, record everything. Never signs
    anyone in, never reads a credential value."""
    store = _load_store()
    entry = mcp_entry(port)
    results = []
    for det in detect():
        rec = store["assistants"].setdefault(det["id"], {})
        if not det["installed"]:
            results.append(_record(rec, STATE_NOT_FOUND, "not installed here"))
            continue
        if det["signed_in"] is False:
            results.append(_record(
                rec, STATE_SIGN_IN_FIRST,
                "installed but no sign-in found; open it and sign in, "
                "then run Connect my AIs again"))
            continue
        merged = merge_config(det, entry)
        if not merged["ok"]:
            results.append(_record(rec, STATE_PENDING,
                                   f"config merge failed: {merged['error']}"))
            continue
        offer_id, note, ended = _ensure_offer(det, rec)
        if ended:
            results.append(_record(rec, STATE_PENDING, note, offer_id))
            continue
        wire = "already wired" if merged["already"] else "MCP entry merged"
        if det["id"] == "claude-desktop":
            # Observed 2026-11-06: this client attaches lazily, on
            # first chat. Wired is provable; attach is the app's.
            sig = (" Sign-in signal unreadable from here (the "
                   "running app holds its cookie store open), so "
                   "this rests on presence, not a verified session."
                   if det["signed_in"] is None else "")
            results.append(_record(
                rec, STATE_NEEDS_FIRST_CHAT,
                f"{wire}; {note}. This client attaches on its first "
                f"chat, so the last check happens inside the app.{sig}",
                offer_id))
            continue
        ok, detail = _verify(entry)
        if ok:
            if offer_id:
                mx.update_connection_state(offer_id, STATE_LIVE)
            results.append(_record(rec, STATE_LIVE,
                                   f"{wire}; {note}; {detail}", offer_id))
        else:
            results.append(_record(rec, STATE_PENDING,
                                   f"{wire}; {note}; {detail}", offer_id))
    store["last_run"] = _iso(_now())
    _save_store(store)
    dropped = drop_discovery_file(port)
    return _view(store, dropped)


def _view(store: Dict[str, Any], dropped: Optional[List[str]] = None) -> Dict[str, Any]:
    home, system = _home(), _system()
    assistants = []
    for aid in ASSISTANT_IDS:
        det = detect_one(aid, home, system)
        rec = store["assistants"].get(aid) or {}
        state = rec.get("state") or (
            STATE_NOT_FOUND if not det["installed"] else
            STATE_SIGN_IN_FIRST if det["signed_in"] is False
            else STATE_DETECTED)
        assistants.append({
            "id": aid, "name": det["name"],
            "installed": det["installed"], "signed_in": det["signed_in"],
            "app_installed": det.get("app_installed"),
            "config_path": det["config_path"],
            "state": state, "display": DISPLAY.get(state, state),
            "offer_id": rec.get("offer_id"),
            "detail": rec.get("detail") or "",
            "updated_at": rec.get("updated_at"),
        })
    return {"assistants": assistants, "last_run": store.get("last_run"),
            "discovery_files": dropped or []}


def status() -> Dict[str, Any]:
    """The panel view: live detection folded over the stored sweep
    states. Reading status runs no merges and signs nothing."""
    return _view(_load_store())
