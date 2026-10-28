"""
The phone MCP core (slice 2): Xod's agent, Crew jobs and other MCP clients
reach paired Android phones through these calls. app/phone_mcp.py is the
stdio MCP server over this module; tests drive this module directly with a
fake transport.

Trust rules (the line between a phone assistant and stalkerware):
- Only a phone that paired on its own screen is reachable. Pairing pins
  keys (Agent Mail contacts); an unpaired address is refused, and a phone
  whose keys changed is refused until a person re-pairs - Agent Mail's
  send already raises on changed keys, and replies are only accepted from
  the pinned address with a matching request id.
- Permissions live on the phone. Each capability (messages, apps, screen,
  tap, location, sms, files) is off until switched on in the phone app.
  The phone reports its switches in its hello; Xod mirrors them here and
  returns "not allowed on this phone" without sending anything when a
  capability is off or was never reported as on. The phone enforces the same check
  itself, so a stale mirror can't grant anything.
- Anything that sends, pays, deletes or unlocks still asks on the phone
  (needs_ok in the request); reading and opening apps don't.
- Every call is logged here (phones_log.jsonl in the data dir) and shown
  in the phone app's recent actions (the phone logs its side).

Calls go over Agent Mail as {"phone_action": ..., "request_id": ...}
messages; replies come back as Agent Mail replies carrying the same
request_id. phone_list also reports this computer: locked / awake.
"""
import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.phone_mcp")

REGISTRY_PATH = DATA_DIR / "phones.json"
LOG_PATH = DATA_DIR / "phones_log.jsonl"

CAPABILITIES = ("messages", "apps", "screen", "tap", "location", "sms", "files",
                "notifications", "calendar", "contacts", "network", "background_apps")
MIRROR_ADDRESS = "iphone-mirroring"  # the virtual phone backed by macOS iPhone Mirroring (services/iphone_mirror.py)

# The shared contract with the Android app: every action below is listed in
# docs/phone_actions.json with the same capability and needs_tap. The file is
# the single source both sides test against, so a rename can't drift again.
CONTRACT_PATH = Path(__file__).resolve().parents[2] / "docs" / "phone_actions.json"


def load_action_contract(path: Path = CONTRACT_PATH) -> Dict[str, Dict[str, Any]]:
    """{action name: {"capability": ..., "needs_tap": ...}} from docs/phone_actions.json ({} if absent, e.g. packaged)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {a["name"]: {"capability": a.get("capability", ""), "needs_tap": bool(a.get("needs_tap"))}
                for a in data.get("actions", [])}
    except Exception:
        return {}


ACTION_CONTRACT = load_action_contract()


def contract_mismatches(tools: Optional[Dict[str, tuple]] = None,
                        contract: Optional[Dict[str, Dict[str, Any]]] = None) -> List[str]:
    """Ways TOOLS and docs/phone_actions.json disagree ([] means they match exactly)."""
    tools = TOOLS if tools is None else tools
    contract = ACTION_CONTRACT if contract is None else contract
    if not contract:
        return ["docs/phone_actions.json is missing or unreadable"]
    out = []
    used = set()
    for tool, (action, capability, needs_ok) in sorted(tools.items()):
        used.add(action)
        spec = contract.get(action)
        if spec is None:
            out.append(f"{tool} sends {action}, which is not in docs/phone_actions.json")
        elif spec["capability"] != capability or spec["needs_tap"] != bool(needs_ok):
            out.append(f"{tool} sends {action} as capability={capability!r} needs_tap={bool(needs_ok)}, "
                       f"but the contract says capability={spec['capability']!r} needs_tap={spec['needs_tap']}")
    for action in sorted(set(contract) - used):
        out.append(f"{action} is in docs/phone_actions.json but no phone tool sends it")
    return out


# tool -> (phone action, capability, needs the phone owner's tap)
# Action names are the phone's own (docs/phone_actions.json); tool names stay stable for MCP clients.
TOOLS: Dict[str, tuple] = {
    "phone_send": ("phone_notify", "messages", True),      # a message to the phone's owner: it sends, so it asks
    "phone_ask": ("phone_ask", "messages", False),         # the tap *is* the answer
    "phone_open_app": ("phone_open_app", "apps", False),
    "phone_screen_read": ("phone_screen_read", "screen", False),
    "phone_screen_tap": ("phone_screen_tap", "tap", True),
    "phone_location": ("phone_location", "location", False),
    # Background (headless / second-display) actions from the Android handoff spec.
    "phone_sms_send": ("phone_sms_send", "sms", True),            # sends, so it needs a tap on the phone
    "phone_notifications": ("phone_notifications", "notifications", False),
    "phone_calendar": ("phone_calendar", "calendar", False),
    "phone_contacts": ("phone_contacts", "contacts", False),
    "phone_fetch": ("phone_fetch", "network", False),             # an HTTP fetch from the phone's network
    "phone_app_run": ("phone_app_run", "background_apps", False),  # tier-2: app on an invisible second display
    "phone_files_list": ("phone_files_list", "files", False),
    "phone_file_read": ("phone_file_read", "files", False),
    "phone_stop": ("phone_stop", "", False),                            # stop a running phone action: always allowed, it only stops
}
ASK_TIMEOUT = 300.0


# ------------------------------------------------------------------ registry (paired phones)

def _load() -> Dict[str, Any]:
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"phones": {}}


def _save(st: Dict[str, Any]) -> None:
    try:
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY_PATH.write_text(json.dumps(st, indent=1), encoding="utf-8")
    except Exception as e:
        logger.warning(f"phone_mcp: {e}")


def pair_phone(address: str, nickname: str = "") -> Dict[str, Any]:
    """Marks an Agent Mail contact as a paired phone. The pairing itself happens on the
    phone (it pins this computer); here we only accept addresses whose keys are already
    pinned as a known/mine contact, so nothing can be paired from Xod's side alone."""
    from app.services import agent_mail
    address = (address or "").strip().lower()
    c = agent_mail.contact(address)
    if not c or c.get("trust") not in ("mine", "known"):
        return {"success": False, "error": f"{address} isn't a pinned contact. Pair it on the phone first "
                                           "(it must approve on its own screen), then mark it as yours in Agent Mail."}
    st = _load()
    st["phones"][address] = {**st["phones"].get(address, {}), "address": address,
                             "nickname": nickname or c.get("nickname") or address,
                             "safety": c.get("safety"), "paired_at": time.time(),
                             "capabilities": st["phones"].get(address, {}).get("capabilities") or {},
                             "last_seen": st["phones"].get(address, {}).get("last_seen")}
    _save(st)
    return {"success": True, "phone": st["phones"][address]}


def unpair_phone(address: str) -> bool:
    st = _load()
    return bool(st["phones"].pop((address or "").lower(), None)) and not _save(st)


def record_hello(address: str, hello: Dict[str, Any]) -> None:
    """The phone's hello (sent after pairing and on app start): its capability switches,
    battery and app version. This is the mirror of permissions that live on the phone."""
    st = _load()
    ph = st["phones"].get((address or "").lower())
    if not ph:
        return
    caps = hello.get("capabilities") or {}
    ph["capabilities"] = {k: bool(caps.get(k)) for k in CAPABILITIES if k in caps}
    for k in ("battery", "app_version", "model"):
        if hello.get(k) is not None:
            ph[k] = hello[k]
    ph["last_seen"] = time.time()
    _save(st)


def paired_phones() -> List[Dict[str, Any]]:
    return list(_load()["phones"].values())


def phone_list() -> Dict[str, Any]:
    from app.services import power
    pst = power.status()
    mac = {"locked": pst["locked"], "awake": pst["awake_held"], "awake_held": pst["awake_held"],
           "platform": pst["platform"]}
    out = []
    for ph in paired_phones():
        out.append({**ph, "online": bool(ph.get("last_seen") and time.time() - ph["last_seen"] < 1800)})
    try:
        from app.services import iphone_mirror
        out.append(iphone_mirror.list_entry())
    except Exception:
        pass
    return {"phones": out, "computer": mac}


# ------------------------------------------------------------------ audit log (this side)

def _audit(entry: Dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(), **entry}) + "\n")
    except Exception:
        pass


def audit_log(limit: int = 100) -> List[Dict[str, Any]]:
    try:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(x) for x in lines if x.strip()]
    except Exception:
        return []


# ------------------------------------------------------------------ transport (Agent Mail)

class AgentMailTransport:
    """Signed, sealed calls over Agent Mail. Replies are verified by Agent Mail itself:
    only mail from the pinned phone address, opened and signature-checked, is read."""

    async def send(self, address: str, payload: Dict[str, Any]) -> str:
        from app.services import agent_mail
        rid = payload["request_id"]
        res = await agent_mail.send(address, json.dumps(payload), subject=payload.get("phone_action") or "phone",
                                    kind="message", origin="phone_mcp")
        if not res.get("success"):
            raise RuntimeError(res.get("error") or f"Couldn't reach {address}.")
        return rid

    async def wait_reply(self, address: str, request_id: str, timeout: float) -> Optional[Dict[str, Any]]:
        from app.services import agent_mail
        deadline = time.time() + timeout
        while time.time() < deadline:
            await agent_mail.check(notify=False)
            with agent_mail._db() as c:
                rows = c.execute("SELECT text FROM mail_messages WHERE direction='in' AND peer=? AND status='new' "
                                 "ORDER BY created_at DESC LIMIT 50", (address,)).fetchall()
            for r in rows:
                try:
                    d = json.loads(r["text"] or "")
                except ValueError:
                    continue
                if d.get("request_id") == request_id:
                    return d
            await asyncio.sleep(0.5)
        return None


transport: Any = AgentMailTransport()


async def _call_mirror(tool: str, **args: Any) -> Dict[str, Any]:
    """phone_* against iPhone Mirroring (Mac only). Only screen read/tap exist there;
    everything else is an Android-phone tool. Off until switched on in Settings."""
    from app.services import iphone_mirror
    _audit({"tool": tool, "phone": MIRROR_ADDRESS, "args": {k: str(v)[:120] for k, v in args.items()}})
    if tool not in ("phone_screen_read", "phone_screen_tap"):
        return {"success": False, "error": "That tool isn't available on an iPhone through Mirroring. "
                                           "Only phone_screen_read and phone_screen_tap are."}
    if not iphone_mirror.enabled():
        return {"success": False, "error": "iPhone Mirroring is off. Switch it on in Xod's phone panel first."}
    try:
        if tool == "phone_screen_read":
            out = await asyncio.to_thread(iphone_mirror.read_screen)
        else:
            out = await asyncio.to_thread(iphone_mirror.tap, int(args.get("x", 0)), int(args.get("y", 0)))
    except Exception as e:
        out = {"success": False, "error": str(e)}
    _audit({"tool": tool, "phone": MIRROR_ADDRESS, "result": str(out)[:200]})
    return out


# ------------------------------------------------------------------ calling a phone

async def call_tool(tool: str, address: str = "", timeout: float = ASK_TIMEOUT, **args: Any) -> Dict[str, Any]:
    """One MCP tool call. Refuses unpaired phones and known-off capabilities before
    anything is sent; the phone re-checks both itself."""
    if tool == "phone_list":
        return phone_list()
    if tool not in TOOLS:
        return {"success": False, "error": f"Unknown phone tool {tool}."}
    if (address or "").strip().lower() == MIRROR_ADDRESS:
        return await _call_mirror(tool, **args)
    phones = {p["address"]: p for p in paired_phones()}
    if not phones:
        return {"success": False, "error": "No phone is paired. Pair one on the phone itself (Pair a phone in Xod)."}
    phone = phones.get((address or "").lower()) or (list(phones.values())[0] if len(phones) == 1 else None)
    if not phone:
        return {"success": False, "error": f"{address} isn't a paired phone. Paired: {', '.join(phones)}."}
    action, capability, needs_ok = TOOLS[tool]
    caps = phone.get("capabilities") or {}
    if capability and caps.get(capability) is not True:  # off until the phone reports it switched on
        _audit({"tool": tool, "phone": phone["address"], "result": "not allowed on this phone"})
        return {"success": False, "error": f"not allowed on this phone: '{capability}' is switched off in the phone app."}
    payload = {"phone_action": action, "request_id": uuid.uuid4().hex, "needs_ok": needs_ok, **args}
    _audit({"tool": tool, "phone": phone["address"], "args": {k: str(v)[:120] for k, v in args.items()}})
    try:
        rid = await transport.send(phone["address"], payload)
    except Exception as e:  # changed keys raise here (Agent Mail): refuse, never fall back
        _audit({"tool": tool, "phone": phone["address"], "result": f"refused: {e}"})
        return {"success": False, "error": str(e)}
    reply = await transport.wait_reply(phone["address"], rid, timeout if tool == "phone_ask" else min(timeout, 120.0))
    if reply is None:
        out = {"success": False, "error": "The phone didn't answer in time (it may be asleep or offline)."}
    else:
        out = {"success": bool(reply.get("ok", True)), **{k: v for k, v in reply.items() if k != "request_id"}}
        if reply.get("capabilities"):
            record_hello(phone["address"], reply)
    _audit({"tool": tool, "phone": phone["address"], "result": str(out)[:200]})
    return out


# ------------------------------------------------------------------ approvals from the phone (slice 3)

async def ask_approval(address: str, title: str, detail: str, approve_label: str = "Approve",
                       timeout: float = ASK_TIMEOUT) -> Dict[str, Any]:
    """Shows an approval on the paired Android phone (buttons on the phone) and waits.
    Returns {"approved": bool}. This is the Agent Mail path; the Telegram path in
    services/phone.py is unchanged for people who prefer it."""
    return await call_tool("phone_ask", address=address, timeout=timeout,
                           question=f"{title}\n\n{detail}", options=[approve_label, "Decline"], kind="approval")


# ------------------------------------------------------------------ approvals bridge (slice 3)

_bridge_task: Any = None


def _pushed() -> set:
    return set(_load().get("pushed_approvals") or [])


def _mark_pushed(key: str) -> None:
    st = _load()
    st["pushed_approvals"] = sorted(set(st.get("pushed_approvals") or []) | {key})[-200:]
    _save(st)


async def bridge_once() -> int:
    """Waiting Crew/task approvals and questions -> the paired phone's buttons, once each.
    Answers are applied back through the same functions the board uses. Returns how many
    were pushed. Only runs when a phone is paired and allows messages."""
    phones = [p for p in paired_phones() if (p.get("capabilities") or {}).get("messages") is True]
    if not phones:
        return 0
    addr = phones[0]["address"]
    pushed = _pushed()
    n = 0
    from app.services.task_queue import task_queue
    for t in task_queue.list(limit=50):
        if t.get("status") != "waiting":
            continue
        kind = t.get("waiting_kind") or "approval"
        key = f"task:{t['id']}:{kind}"
        if key in pushed:
            continue
        _mark_pushed(key)
        detail = (t.get("waiting_detail") or {}).get("message") or (t.get("waiting_detail") or {}).get("label") or ""
        ans = await call_tool("phone_ask", address=addr, question=f"{t.get('title') or 'A task'}\n\n{detail}",
                              options=["Approve", "Cancel"] if kind == "approval" else [], kind=kind)
        n += 1
        if kind == "approval":
            if ans.get("answer") == "Approve" or ans.get("approved"):
                task_queue.approve(t["id"])
            elif ans.get("answer") == "Cancel":
                await task_queue.cancel(t["id"])
        elif ans.get("answer"):
            task_queue.answer(t["id"], str(ans["answer"]))
    try:
        from app.services import crew
        for j in crew.jobs(active_only=True):
            for a in j.get("approvals") or []:
                key = f"crew:{j['id']}:{a['id']}"
                if key in pushed:
                    continue
                _mark_pushed(key)
                ans = await call_tool("phone_ask", address=addr,
                                      question=f"{j.get('title') or 'Crew job'} wants to {a.get('what') or 'do something'}",
                                      options=["Approve", "Deny"], kind="approval")
                n += 1
                if ans.get("answer") == "Approve" or ans.get("approved"):
                    await crew.decide(j["id"], a["id"], "once")
                elif ans.get("answer") == "Deny":
                    await crew.decide(j["id"], a["id"], "deny")
            for q in j.get("questions") or []:
                key = f"crewq:{j['id']}:{q['id']}"
                if key in pushed:
                    continue
                _mark_pushed(key)
                qs = q.get("questions") or [{}]
                opts = qs[0].get("options") or []
                ans = await call_tool("phone_ask", address=addr,
                                      question=qs[0].get("question") or qs[0].get("header") or "A question",
                                      options=opts, kind="question")
                n += 1
                if ans.get("answer"):
                    a0 = str(ans["answer"])
                    await crew.answer(j["id"], q["id"], [{"question": qs[0].get("id", ""),
                                                          **({"choice": a0} if a0 in opts else {"text": a0})}])
    except Exception as e:
        logger.debug(f"phone bridge (crew): {e}")
    return n


async def _bridge_loop() -> None:
    while True:
        try:
            await bridge_once()
        except Exception as e:
            logger.debug(f"phone bridge: {e}")
        await asyncio.sleep(15)


def start_bridge() -> None:
    global _bridge_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _bridge_task is None or _bridge_task.done():
        _bridge_task = loop.create_task(_bridge_loop())
