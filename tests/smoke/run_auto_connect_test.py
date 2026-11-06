"""
Auto-Connect (services/auto_connect.py), against a fixture home on a
fixture OS, no keystore, no network, no real assistant touched:

  - detection reads footprints honestly: Claude Desktop signed in
    (sessionKey cookie NAME present, never a value), Codex installed
    but signed out (no auth.json), Claude Code absent
  - a status read before any sweep says Detected / sign in first /
    Not found, and runs no merge
  - the JSON merge is idempotent, keeps other servers, and leaves a
    backup of the original; the TOML merge writes one
    [mcp_servers.xod] table and never a second
  - one sweep connects the signed-in desktop as verify-in-app
    (lazy attach), refuses to wire the signed-out Codex and leaves
    its config untouched, and records a signed offer per connection
    in the muse-connect store as the audit trail
  - after sign-in appears, the next sweep wires Codex and Claude
    Code, the headless verify marks them live in both stores, and a
    third sweep changes nothing at all: same config bytes, same
    offer ids, same connection count
  - the discovery doc carries the endpoints and the MCP entry, and
    drops into the shared folder next to the tree-inbox
  .venv/bin/python tests/smoke/run_auto_connect_test.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-auto-connect-"))
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


from app.services import mail_crypto as mc
from app.services import muse_connect as mx
from app.services import auto_connect as ac

IDENT = mc.Identity.new()
HOME = TMP / "home"
INBOX = TMP / "shared" / "tree-inbox"
INBOX.mkdir(parents=True)
SHARED = TMP / "shared"

mx._identity = lambda: IDENT
mx._inbox_dir = lambda: INBOX
ac._home = lambda: HOME
ac._system = lambda: "Darwin"
ac._shared_folder = lambda: SHARED
ac._verify = lambda entry: (True, "stub: transport verified")

ENTRY = {"command": "/fixture/python", "args": ["-m", "app.mcp_server"],
         "cwd": "/fixture/xod", "env": {"XOD_URL": "http://127.0.0.1:8000"}}

# --- fixtures -----------------------------------------------------------
claude_dir = HOME / "Library" / "Application Support" / "Claude"
claude_dir.mkdir(parents=True)
desktop_cfg = claude_dir / "claude_desktop_config.json"
desktop_cfg.write_text(json.dumps(
    {"mcpServers": {"other": {"command": "/bin/true"}}}), encoding="utf-8")
# A cookie store whose NAME bytes include sessionKey. Only the name
# is ever scanned; the value bytes here are filler on purpose.
(claude_dir / "Cookies").write_bytes(b"...\x00sessionKey\x00filler...")

codex_dir = HOME / ".codex"
codex_dir.mkdir(parents=True)
codex_cfg = codex_dir / "config.toml"
codex_cfg.write_text("# fixture codex config\n", encoding="utf-8")

# --- detection ----------------------------------------------------------
dets = {d["id"]: d for d in ac.detect()}
check("desktop detected installed and signed in",
      dets["claude-desktop"]["installed"] and dets["claude-desktop"]["signed_in"])
check("codex detected installed, signed out",
      dets["codex"]["installed"] and not dets["codex"]["signed_in"])
check("claude code detected absent", not dets["claude-code"]["installed"])

view = ac.status()
states = {a["id"]: a["state"] for a in view["assistants"]}
check("pre-sweep states are honest words",
      states == {"claude-desktop": "detected", "claude-code": "not_found",
                 "codex": "sign_in_first"}, json.dumps(states))
check("a status read merged nothing",
      "xod" not in json.loads(desktop_cfg.read_text())["mcpServers"]
      and "[mcp_servers.xod]" not in codex_cfg.read_text())

# --- merges, directly -----------------------------------------------------
probe = TMP / "probe.json"
probe.write_text('{"mcpServers": {"keep": {"command": "/bin/true"}}}',
                 encoding="utf-8")
r1 = ac.merge_json_config(probe, ENTRY)
r2 = ac.merge_json_config(probe, ENTRY)
merged = json.loads(probe.read_text())
check("json merge adds xod once and keeps the rest",
      r1["changed"] and r2["already"]
      and list(merged["mcpServers"]) == ["keep", "xod"])
baks = list(TMP.glob("probe.json.bak-auto-connect-*"))
check("json merge left exactly one backup of the original",
      len(baks) == 1 and json.loads(baks[0].read_text())["mcpServers"].keys() == {"keep"})

tprobe = TMP / "probe.toml"
tprobe.write_text("# hi\n", encoding="utf-8")
t1 = ac.merge_toml_config(tprobe, ENTRY)
t2 = ac.merge_toml_config(tprobe, ENTRY)
check("toml merge writes one table and never a second",
      t1["changed"] and t2["already"]
      and tprobe.read_text().count("[mcp_servers.xod]") == 1)
check("toml merge left a backup", len(list(TMP.glob("probe.toml.bak-auto-connect-*"))) == 1)

# --- merge targets: the layout that exists wins -----------------------------
store_cfg = TMP / "store-layout" / "claude_desktop_config.json"
store_cfg.parent.mkdir(parents=True)
store_cfg.write_text("{}", encoding="utf-8")
fake_det = {"kind": "json",
            "config_path": str(TMP / "store-layout" / "classic.json"),
            "extra_config_paths": [str(store_cfg)]}
res = ac.merge_config(fake_det, ENTRY)
check("store-layout install merges where it lives, no stray classic file",
      res["ok"] and not (TMP / "store-layout" / "classic.json").exists()
      and "xod" in json.loads(store_cfg.read_text())["mcpServers"])

# --- windows Claude-Data layout (the Razer's real one) -----------------------
WINHOME = TMP / "winhome"
wcfg = (WINHOME / "AppData" / "Local" / "Claude-Data"
        / "claude_pzs8sxrjxfjjc" / "claude_desktop_config.json")
wcfg.parent.mkdir(parents=True)
wcfg.write_text("{}", encoding="utf-8")
(wcfg.parent / "Default" / "Network").mkdir(parents=True)
(wcfg.parent / "Default" / "Network" / "Cookies").write_bytes(
    b"...\x00sessionKey\x00filler...")
wdet = ac.detect_one("claude-desktop", WINHOME, "Windows")
check("windows Claude-Data layout detected installed and signed in",
      wdet["installed"] and wdet["signed_in"], json.dumps(wdet))
wres = ac.merge_config(wdet, ENTRY)
check("windows merge lands in the Claude-Data config, no classic file made",
      wres["ok"] and "xod" in json.loads(wcfg.read_text())["mcpServers"]
      and not (WINHOME / "AppData" / "Roaming" / "Claude").exists())

# --- locked cookie store: signal unknown, never guessed -----------------------
LOCKHOME = TMP / "lockhome"
lcfg = (LOCKHOME / "AppData" / "Local" / "Claude-Data"
        / "claude_pzs8sxrjxfjjc" / "claude_desktop_config.json")
lcfg.parent.mkdir(parents=True)
lcfg.write_text("{}", encoding="utf-8")
(LOCKHOME / "AppData" / "Local" / "Claude-Data" / "claude_pzs8sxrjxfjjc"
 / "Network").mkdir(parents=True)
locked = (LOCKHOME / "AppData" / "Local" / "Claude-Data"
          / "claude_pzs8sxrjxfjjc" / "Network" / "Cookies")
locked.write_bytes(b"placeholder")
from unittest import mock
real_read_bytes = Path.read_bytes


def locked_read(self):
    if self.name == "Cookies":
        raise PermissionError("locked by the running app")
    return real_read_bytes(self)


with mock.patch.object(Path, "read_bytes", locked_read), \
        mock.patch.object(ac.shutil, "copy2",
                          side_effect=OSError("still locked")):
    ldet = ac.detect_one("claude-desktop", LOCKHOME, "Windows")
check("locked cookie store reads as unknown sign-in, not signed out",
      ldet["installed"] and ldet["signed_in"] is None, json.dumps(ldet))

# --- sweep 1: signed-in desktop only --------------------------------------
out = ac.run_sweep()
states = {a["id"]: a["state"] for a in out["assistants"]}
check("sweep: desktop wired, verify in app",
      states["claude-desktop"] == "needs-first-chat", json.dumps(states))
check("sweep: signed-out codex reported, not wired",
      states["codex"] == "sign_in_first"
      and "[mcp_servers.xod]" not in codex_cfg.read_text())
check("sweep: absent claude code stays not found",
      states["claude-code"] == "not_found")
check("desktop config now holds the xod entry beside the other server",
      set(json.loads(desktop_cfg.read_text())["mcpServers"]) == {"other", "xod"})
conns = mx.status()["connections"]
auto_conns = [c for c in conns if (c["muse"] or {}).get("kind") == "auto-connect"]
check("one connection recorded, pending verification, offer is the audit trail",
      len(auto_conns) == 1 and auto_conns[0]["state"] == "pending-verification"
      and (auto_conns[0]["muse"] or {}).get("assistant") == "claude-desktop",
      json.dumps(conns))
desktop_offer = auto_conns[0]["offer_id"]
offer_rec = [o for o in mx.status()["offers"] if o["offer_id"] == desktop_offer]
check("the recorded offer verifies", bool(offer_rec)
      and mx.verify_offer(offer_rec[0]["offer"])[0])
check("discovery doc dropped beside the tree-inbox",
      (SHARED / "xod-connect-discovery.json").is_file())

# --- sweep 2: codex and claude code sign in --------------------------------
(codex_dir / "auth.json").write_text("{}", encoding="utf-8")
(HOME / ".claude.json").write_text(json.dumps(
    {"oauthAccount": {"emailAddress": "someone@example.invalid"}}), encoding="utf-8")
out = ac.run_sweep()
states = {a["id"]: a["state"] for a in out["assistants"]}
check("sweep 2: codex and claude code live after verify",
      states["codex"] == "live" and states["claude-code"] == "live",
      json.dumps(states))
check("sweep 2: desktop keeps its first offer (no duplicate connection)",
      {a["id"]: a["offer_id"] for a in out["assistants"]}["claude-desktop"] == desktop_offer)
check("codex config gained exactly one xod table",
      codex_cfg.read_text().count("[mcp_servers.xod]") == 1)
check("claude code config gained the xod entry, account block intact",
      "xod" in json.loads((HOME / ".claude.json").read_text())["mcpServers"]
      and "oauthAccount" in json.loads((HOME / ".claude.json").read_text()))
conns = mx.status()["connections"]
by_assistant = {(c["muse"] or {}).get("assistant"): c["state"]
                for c in conns if (c["muse"] or {}).get("kind") == "auto-connect"}
check("muse-connect store mirrors the sweep states",
      by_assistant == {"claude-desktop": "pending-verification",
                       "claude-code": "live", "codex": "live"},
      json.dumps(by_assistant))

# --- sweep 3: fully idempotent ---------------------------------------------
before = {p: p.read_bytes() for p in
          (desktop_cfg, codex_cfg, HOME / ".claude.json")}
out = ac.run_sweep()
after = {p: p.read_bytes() for p in before}
check("sweep 3 changes no config bytes", before == after)
check("sweep 3 records no new connections",
      len([c for c in mx.status()["connections"]
           if (c["muse"] or {}).get("kind") == "auto-connect"]) == 3)

# --- discovery --------------------------------------------------------------
doc = ac.discovery_doc()
check("discovery doc names itself and the endpoints",
      doc["type"] == "xod-connect-discovery"
      and doc["endpoints"]["auto_connect_run"].endswith("/api/auto-connect/run")
      and doc["endpoints"]["offer"].endswith("/api/muse-connect/offer"))
check("discovery doc carries the MCP entry and the AI steps",
      doc["mcp_entry"]["xod"]["args"] == ["-m", "app.mcp_server"]
      and len(doc["for_an_ai"]) >= 5)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("FAILED:", ", ".join(failed))
    sys.exit(1)
