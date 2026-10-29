"""
Shared memory across your Xods: what one of your computers knows about you (user_memory's facts, said there or from
your paired phone) is known on your others. Design: docs/SHARED_MEMORY.md.

What's shared: facts about you and forgets, nothing else (no chats, files, notes, settings or keys).

Off by default (setting memory_sync). Sharing with a computer starts from this one ("Share with Laptop" in Your Xods)
and runs only after an approval card on that one, so both ends were agreed to by the person. Stop sharing, or turn it
off, and nothing more is sent or taken; "Remove what came from Laptop" drops those facts here only.

Transport: the Xod network's beacon (xod_network, your Tailscale address only). Every request is checked twice: Tailscale
says the caller is signed in to the same account (`tailscale whois`), and it is signed by the keys pinned when the card
was approved. A reinstall (new keys) stops sharing until it's approved again. Each batch of changes is sealed and
signed with mail_crypto for the computer that asked, on top of Tailscale's own encryption. Each Xod has its own sync
keys in its data folder (memory_sync_key.json), so this doesn't need Agent Mail.

  POST /v1/memory/hello     {from, sign_pub, box_pub}: asks to share (an approval card here); answers with our keys
  GET  /v1/memory/changes   ?since=<seq>, signed headers: our own changes after that, sealed for the caller
  POST /v1/memory/poke      signed headers: "I changed something", so we pull from them now

Pull, not push: every PULL_EVERY seconds while on, and right after a poke, each Xod asks each computer it shares with
for changes since its cursor, so a computer that was off catches up when it's back. merge() never logs what it got,
so nothing echoes back and forth.
"""
import asyncio
import json
import logging
import os
import re
import socket
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR
from app.services import mail_crypto as mc

logger = logging.getLogger("xod.memory_sync")

SETTING = "memory_sync"
PEERS_SETTING = "memory_sync_peers"
KEY_PATH = Path(DATA_DIR) / "memory_sync_key.json"
PULL_EVERY = 120.0
CLOCK_SKEW = 300
MAX_LOG = 50

_state: Dict[str, Any] = {"loop": None, "wake": None, "log": [], "task": None}


class SyncError(Exception):
    pass


# ---------------------------------------------------------------- setting, keys, peers

def enabled() -> bool:
    from app.services.storage import get_setting
    return get_setting(SETTING, "off") == "on"


def set_enabled(on: bool) -> Dict[str, Any]:
    from app.services.storage import set_setting
    set_setting(SETTING, "on" if on else "off")
    if on:
        identity()
        nudge()
    return status()


def identity() -> mc.Identity:
    try:
        d = json.loads(KEY_PATH.read_text())
        return mc.Identity.from_secrets(d["sign"], d["box"])
    except (OSError, ValueError, KeyError):
        pass
    ident = mc.Identity.new()
    sign, box = ident.secrets()
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = KEY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"sign": sign, "box": box}))
    os.chmod(tmp, 0o600)
    os.replace(tmp, KEY_PATH)
    return ident


def _addr(sign_pub: str) -> str:
    return "xod-memory:" + sign_pub[:22]


def _me() -> str:
    return socket.gethostname().split(".")[0][:60]


def peers() -> Dict[str, Dict[str, Any]]:
    """{sign_pub: {host, name, ip, sign_pub, box_pub, state: asked | waiting | paired, cursor, last_sync, error}}.
    asked: they asked us and the card here is waiting. waiting: we asked them and their card is waiting."""
    from app.services.storage import get_setting
    try:
        d = json.loads(get_setting(PEERS_SETTING, "{}") or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _save_peers(p: Dict[str, Dict[str, Any]]) -> None:
    from app.services.storage import set_setting
    set_setting(PEERS_SETTING, json.dumps(p))


def _update_peer(key: str, **changes: Any) -> Optional[Dict[str, Any]]:
    p = peers()
    if key not in p:
        return None
    p[key].update(changes)
    _save_peers(p)
    return p[key]


def _note(text: str) -> None:
    _state["log"].append({"at": time.time(), "text": text})
    del _state["log"][:-MAX_LOG]
    logger.info(f"shared memory: {text}")


def _good_key(k: Any) -> bool:
    try:
        return isinstance(k, str) and len(mc.b64d(k)) == 32
    except Exception:
        return False


def status() -> Dict[str, Any]:
    return {"on": enabled(), "me": _me(),
            "peers": [{k: v for k, v in x.items() if k not in ("grant", "box_pub")} for x in peers().values()],
            "log": list(reversed(_state["log"][-20:]))}


# ---------------------------------------------------------------- asking another computer to share

def _signed(path: str, extra: str = "") -> Dict[str, str]:
    me, ts = identity(), str(int(time.time()))
    return {"X-Xod-Memory-Key": me.sign_pub, "X-Xod-Memory-Time": ts,
            "X-Xod-Memory-Sig": me.sign(f"xod-memory|{path}|{extra}|{ts}".encode())}


async def share_with(machine: str) -> Dict[str, Any]:
    """Asks another of your Xods to share. It answers once you approve the card on that computer."""
    from app.services import xod_network
    if not enabled():
        raise SyncError("Turn on shared memory here first.")
    x = await xod_network._target(machine)
    me = identity()
    try:
        r = await xod_network._call("POST", x["ip"], "/v1/memory/hello",
                                    {"from": _me(), "sign_pub": me.sign_pub, "box_pub": me.box_pub})
    except xod_network.NetworkError as e:
        raise SyncError(f"{x['name']}: {e}")
    if not (_good_key(r.get("sign_pub")) and _good_key(r.get("box_pub"))):
        raise SyncError(f"{x['name']} didn't answer like a Xod that shares memory. Update Xod there.")
    p = peers()
    old = p.get(r["sign_pub"]) or {}
    p[r["sign_pub"]] = {**old, "host": x["host"], "name": x["name"], "ip": x["ip"], "sign_pub": r["sign_pub"],
                        "box_pub": r["box_pub"], "state": "paired" if r.get("state") == "paired" else "waiting",
                        "cursor": old.get("cursor") or 0, "error": None}
    _save_peers(p)
    _note(f"Asked {x['name']} to share; approve it there" if r.get("state") != "paired" else f"Sharing with {x['name']}")
    nudge()
    return {"machine": x["name"], "state": p[r["sign_pub"]]["state"],
            "message": f"Approve it on {x['name']} (the card is in its chat and on your phone)."
            if r.get("state") != "paired" else f"Already sharing with {x['name']}."}


def stop_sharing(key: str) -> bool:
    p = peers()
    gone = p.pop(key, None)
    _save_peers(p)
    if gone:
        _note(f"Stopped sharing with {gone.get('name') or gone.get('host')}")
    return bool(gone)


def remove_received(key: str) -> int:
    from app.services import user_memory
    x = peers().get(key) or {}
    host = x.get("host") or ""
    n = user_memory.remove_received(host) if host else 0
    if n:
        _note(f"Removed {n} fact{'s' if n != 1 else ''} that came from {x.get('name') or host}")
    return n


# ---------------------------------------------------------------- the beacon's side (called by xod_network)

def _who(caller_ip: str) -> Dict[str, Any]:
    from app.services import xod_network
    who = xod_network._caller(caller_ip)
    if not who["same_account"]:
        raise PermissionError("Only your own computers can share memory with this one.")
    return who


def receive_hello(caller_ip: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Another of your Xods asks to share: an approval card here (once per set of keys). Answers with our keys."""
    who = _who(caller_ip)
    if not enabled():
        raise PermissionError(f"Shared memory is off on {_me()}. Turn it on there first (Your Xods).")
    sign_pub, box_pub = body.get("sign_pub"), body.get("box_pub")
    if not (_good_key(sign_pub) and _good_key(box_pub)):
        raise ValueError("Bad keys.")
    me = identity()
    name = re.sub(r"[^\w .-]", "", str(body.get("from") or who["host"] or caller_ip))[:60]
    p = peers()
    x = p.get(sign_pub)
    if x and x.get("state") == "paired":
        return {"state": "paired", "sign_pub": me.sign_pub, "box_pub": me.box_pub, "from": _me()}
    for k in [k for k, v in p.items() if v.get("host") == who["host"] and k != sign_pub and who["host"]]:
        p.pop(k)    # the same computer with new keys (reinstalled): the old pairing is gone, this asks again
    grant = uuid.uuid4().hex
    from app.services import sentinel
    held = sentinel.hold("memory_sync_accept", {"key": sign_pub, "_grant": grant},
                         {"rule": "memory_sync_pair",
                          "why": f"Your Xod on {name} wants to share what Xod knows about you with this computer "
                                 f"(facts only, no chats or files). Forgetting a fact on either one forgets it on both."})
    p[sign_pub] = {"host": who["host"] or name, "name": name, "ip": caller_ip, "sign_pub": sign_pub, "box_pub": box_pub,
                   "state": "asked", "grant": grant, "approval_id": held.get("approval_id"), "cursor": 0, "error": None}
    _save_peers(p)
    _note(f"{name} asked to share: waiting for your OK")
    return {"state": "waiting_for_ok", "sign_pub": me.sign_pub, "box_pub": me.box_pub, "from": _me()}


def accept(key: str, grant: str) -> Dict[str, Any]:
    """The card's Approve: start sharing with that computer. Only with the card's own grant."""
    p = peers()
    x = p.get(key)
    if not x or x.get("state") != "asked" or not grant or grant != x.get("grant"):
        return {"success": False, "error": "That request to share isn't waiting any more."}
    x.update(state="paired", grant="", approval_id=None)
    _save_peers(p)
    _note(f"Sharing with {x['name']}")
    nudge()
    return {"success": True, "message": f"Sharing what Xod knows about you with {x['name']}."}


def _check_signed(caller_ip: str, headers: Dict[str, str], path: str, extra: str = "") -> Dict[str, Any]:
    _who(caller_ip)
    if not enabled():
        raise PermissionError(f"Shared memory is off on {_me()}.")
    key = headers.get("x-xod-memory-key") or ""
    x = peers().get(key)
    if not x:
        raise PermissionError(f"{_me()} isn't sharing with that computer (or its keys changed). Share again from there.")
    if x.get("state") == "asked":
        raise PermissionError(f"Waiting for your OK on {_me()}.")
    try:
        ts = int(headers.get("x-xod-memory-time") or 0)
    except ValueError:
        ts = 0
    if abs(time.time() - ts) > CLOCK_SKEW:
        raise PermissionError("Its clock is off.")
    if not mc.verify(key, f"xod-memory|{path}|{extra}|{ts}".encode(), headers.get("x-xod-memory-sig") or ""):
        raise PermissionError("That request isn't signed by the computer you share with.")
    return x


def serve_changes(caller_ip: str, headers: Dict[str, str], since: int) -> Dict[str, str]:
    """Our own changes after `since`, sealed for the computer that asked."""
    from app.services import user_memory
    x = _check_signed(caller_ip, headers, "changes", str(since))
    if x.get("state") == "waiting":
        _update_peer(x["sign_pub"], state="paired")    # it answers our signed request: its card was approved
    data = user_memory.changes_since(max(0, int(since)))
    me = identity()
    return mc.seal(me, _addr(me.sign_pub), _addr(x["sign_pub"]), x["box_pub"], {"kind": "memory_changes", **data})


def poked(caller_ip: str, headers: Dict[str, str]) -> Dict[str, Any]:
    _check_signed(caller_ip, headers, "poke")
    nudge()
    return {"ok": True}


# ---------------------------------------------------------------- pulling

async def _fetch(x: Dict[str, Any], since: int) -> Dict[str, Any]:
    """GET its /v1/memory/changes (the sealed envelope). Tests replace this with a direct call."""
    import httpx

    from app.services import xod_network
    url = f"http://{x['ip']}:{xod_network.BEACON_PORT}/v1/memory/changes"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, params={"since": since}, headers=_signed("changes", str(since)))
    except httpx.HTTPError as e:
        raise SyncError(f"couldn't reach it ({type(e).__name__})")
    if r.status_code != 200:
        try:
            why = r.json().get("detail")
        except ValueError:
            why = None
        raise SyncError(str(why or f"it answered {r.status_code}"))
    return r.json()


async def pull(x: Dict[str, Any]) -> Dict[str, Any]:
    """Takes one computer's changes since our cursor, checks they're sealed and signed by its pinned keys, merges."""
    from app.services import user_memory
    since = int(x.get("cursor") or 0)
    envelope = await _fetch(x, since)
    me = identity()
    try:
        got = mc.open_envelope(me, _addr(me.sign_pub), envelope)
    except ValueError:
        raise SyncError("its answer couldn't be opened with this computer's keys; not used")
    msg = got["msg"]
    if not got["signature_ok"] or got["keys"].get("sign") != x["sign_pub"] or msg.get("from") != _addr(x["sign_pub"]):
        raise SyncError("its answer wasn't signed by the keys you approved; not used")
    if msg.get("kind") != "memory_changes":
        raise SyncError("unexpected answer")
    res = await asyncio.to_thread(user_memory.merge, list(msg.get("changes") or [])[:5000], x.get("host") or x.get("name"))
    _update_peer(x["sign_pub"], cursor=int(msg.get("seq") or since), last_sync=time.time(), error=None, state="paired")
    if res["kept"] or res["forgot"]:
        bits = []
        if res["kept"]:
            bits.append(f"got {len(res['kept'])} fact{'s' if len(res['kept']) != 1 else ''}")
        if res["forgot"]:
            bits.append(f"forgot {len(res['forgot'])}")
        _note(f"{' and '.join(bits).capitalize()} from {x.get('name') or x.get('host')}")
    return res


async def _refresh_ips() -> None:
    from app.services import xod_network
    try:
        f = await xod_network.find()
    except Exception:
        return
    by_host = {x["host"]: x for x in f.get("xods") or [] if x.get("same_account")}
    p = peers()
    for v in p.values():
        hit = by_host.get(v.get("host"))
        if hit:
            v["ip"] = hit["ip"]
    _save_peers(p)


async def sync_once() -> None:
    if not enabled():
        return
    todo = [x for x in peers().values() if x.get("state") in ("waiting", "paired") and x.get("ip")]
    for x in todo:
        try:
            await pull(x)
        except SyncError as e:
            if "waiting for your ok" in str(e).lower():
                _update_peer(x["sign_pub"], error=None)
            else:
                _update_peer(x["sign_pub"], error=str(e)[:200])
        except Exception as e:
            _update_peer(x["sign_pub"], error=f"{type(e).__name__}")
            logger.debug(f"shared memory pull: {e}")


async def _poke_all() -> None:
    import httpx

    from app.services import xod_network
    for x in [x for x in peers().values() if x.get("state") == "paired" and x.get("ip")]:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                await c.post(f"http://{x['ip']}:{xod_network.BEACON_PORT}/v1/memory/poke", headers=_signed("poke"))
        except Exception:
            pass


def nudge(poke: bool = False) -> None:
    """Pull soon, from any thread. poke: a fact changed here, so tell the others to pull from us too (never set for a
    poke we received, or two computers would poke each other forever)."""
    if poke:
        _state["poke"] = True
    loop, wake = _state.get("loop"), _state.get("wake")
    if loop is not None and wake is not None:
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:
            pass


async def run_forever() -> None:
    _state["loop"] = asyncio.get_running_loop()
    _state["wake"] = asyncio.Event()
    last_ips = 0.0
    while True:
        try:
            if enabled() and peers():
                if time.time() - last_ips > 600:
                    await _refresh_ips()
                    last_ips = time.time()
                await sync_once()
                if _state.get("poke"):
                    _state["poke"] = False
                    await _poke_all()
        except Exception as e:
            logger.warning(f"shared memory: {e}")
        try:
            await asyncio.wait_for(_state["wake"].wait(), PULL_EVERY)
        except asyncio.TimeoutError:
            pass
        _state["wake"].clear()
        await asyncio.sleep(2)     # a burst of edits becomes one pull
