"""
One-Click Muse Connect: this Xod offers itself to a Muse in one click.

The owner clicks "Connect to Muse" in the Family panel. This service
builds a signed connection offer whose payload is the capability
manifest (what this Xod has and does not have, in plain words), hands
it out as a link, a paste-able text block and a short visual code,
and tracks the connection from offered to live to revoked.

The offer is signed with the same device identity the family
registry carries (services/family_registry.py device_identity,
Ed25519 via services/mail_crypto.py), over the same canonical JSON.
Nothing new is trusted by default: the Muse side verifies the
signature, checks expiry and whatever family-gate facts it can
reach, and its human gives the one approval. After the hello round
trip both ends hold the same state, proven by read-back, not by
either side's say-so.

Transport in v1 is the rail that already works: the family
tree-inbox (services/tree_inbox.py) and signed Agent Mail. The MCP
server surface is the documented upgrade path (docs in
ONE-CLICK-MUSE-CONNECT-SPEC-2026-11-06) and changes nothing here
except how tasks travel.

State lives at DATA_DIR/muse-connect/state.json:
  offers       offer_id -> {offer, state, inbox_dropped}
  connections  offer_id -> {offer_id, muse, state, accepted_at,
                            live_at, revoked_at}
"""
import base64
import calendar
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.paths import DATA_DIR
from app.services import mail_crypto as mc

OFFER_TYPE = "xod-muse-connect-offer"
HELLO_TYPE = "xod-muse-connect-hello"
REVOKE_TYPE = "xod-muse-connect-revoke"
OFFER_TTL_SECONDS = 24 * 3600

# States an offer or connection can be in. "expired" is computed for
# offers nobody accepted inside their window; the rest are stored.
STATES = ("offered", "accepted", "live", "revoked", "expired")


# ---------------------------------------------------------------- seams
# Small wrappers so tests can stand in a fresh identity and a temp
# inbox without a keystore or a family drive.

def _identity():
    from app.services import family_registry
    return family_registry.device_identity()


def _inbox_dir() -> Optional[Path]:
    try:
        from app.services import tree_inbox
        return tree_inbox.inbox_dir()
    except Exception:
        return None


def _registry_view() -> Dict[str, Any]:
    try:
        from app.services import family_registry
        return family_registry.load_registry()
    except Exception:
        return {"state": "missing", "members": [], "devices": [],
                "account": None, "family": None}


def _my_record(view: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import family_registry
    my_id = family_registry.my_device_id()
    for rec in (view.get("members") or []) + (view.get("devices") or []):
        if rec.get("id") == my_id:
            return rec
    return {}


def _rules_ref() -> Optional[Dict[str, str]]:
    """The shared rules file this Xod can see, by hash only.

    family-rules.json lives in the rdrive shared folder (the tree
    inbox's parent space). When it is reachable, the offer names it
    and its sha256 so the Muse side can check both sides hold the
    same rules. Absent is honest: no rules block in the offer.
    """
    inbox = _inbox_dir()
    candidates = []
    if inbox is not None:
        candidates.append(inbox.parent / "family-rules.json")
    try:
        from app.services import family_registry
        candidates.append(
            family_registry.registry_path().parent.parent.parent
            / "family-rules.json")
    except Exception:
        pass
    for path in candidates:
        try:
            if path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                return {"file": "family-rules.json", "sha256": digest}
        except OSError:
            continue
    return None


# ---------------------------------------------------------------- store

def _store_path() -> Path:
    return DATA_DIR / "muse-connect" / "state.json"


def _load_store() -> Dict[str, Any]:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("offers", {})
            data.setdefault("connections", {})
            return data
    except Exception:
        pass
    return {"offers": {}, "connections": {}}


def _save_store(store: Dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=1), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- manifest

def manifest_payload() -> Tuple[Dict[str, Any], str]:
    """(manifest dict, plain-language summary) for the handshake.

    Prefers PR #44's capability manifest when that module is
    installed; on plain main it builds the same shape from what is
    here, and says so in the summary. The offer hashes whichever
    payload was built, so the handshake stays honest in both
    worlds.
    """
    try:
        from app.services import capability_manifest
        manifest = capability_manifest.build_manifest()
        return manifest, capability_manifest.summary_text(manifest)
    except Exception:
        pass
    import platform as _platform
    view = _registry_view()
    rec = _my_record(view)
    name = rec.get("name") or "This Xod"
    plat = rec.get("platform") or _platform.system()
    manifest = {
        "source": "fallback (capability manifest not installed "
                  "on this build)",
        "device": {"name": name, "platform": plat},
        "family_members": len(view.get("members") or []),
    }
    summary = (f"{name} on {plat}. This build does not carry the "
               "full capability manifest yet, so treat this summary "
               "as partial: a Muse connected here should ask "
               "before assuming a tool exists.")
    return manifest, summary


# ---------------------------------------------------------------- offer

def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _parse_iso(text: str) -> float:
    """Epoch seconds for a YYYY-MM-DDTHH:MM:SSZ stamp, 0.0 when the
    stamp is unreadable."""
    try:
        return float(calendar.timegm(
            time.strptime(text or "", "%Y-%m-%dT%H:%M:%SZ")))
    except Exception:
        return 0.0


def confirm_code(core: Dict[str, Any]) -> str:
    """The 8 character visual cross-check code, derived from the
    signed core so both screens compute it independently. The code
    covers the core without the code field itself (and without the
    signature, which is never part of a core)."""
    bare = {k: v for k, v in core.items() if k != "code"}
    digest = hashlib.sha256(mc.canonical(bare)).digest()
    return base64.b32encode(digest[:5]).decode()


def build_offer(identity=None, ttl: int = OFFER_TTL_SECONDS) -> Dict[str, Any]:
    """The signed offer payload (no state attached)."""
    ident = identity or _identity()
    view = _registry_view()
    rec = _my_record(view)
    manifest, summary = manifest_payload()
    issued = _now()
    core: Dict[str, Any] = {
        "type": OFFER_TYPE,
        "version": 1,
        "offer_id": uuid.uuid4().hex,
        "issued_at": _iso(issued),
        "expires_at": _iso(issued + ttl),
        "xod": {
            "device_id": rec.get("id") or "",
            "name": rec.get("name") or "",
            "platform": rec.get("platform") or "",
            "sign_pub": ident.sign_pub,
            "box_pub": ident.box_pub,
        },
        "endpoint": rec.get("endpoint"),
        "account": view.get("account") or rec.get("account") or "",
        "family": view.get("family") or "",
        "manifest": {
            "hash": hashlib.sha256(mc.canonical(manifest)).hexdigest(),
            "summary": summary,
        },
    }
    rules = _rules_ref()
    if rules:
        core["rules"] = rules
    core["code"] = confirm_code(core)
    offer = dict(core)
    offer["signature"] = ident.sign(mc.canonical(core))
    return offer


def verify_offer(offer: Any, now: Optional[float] = None) -> Tuple[bool, List[str]]:
    """(ok, problems). The same checks the Muse side runs: shape,
    signature against the enclosed key, expiry."""
    problems: List[str] = []
    if not isinstance(offer, dict):
        return False, ["offer is not an object"]
    if offer.get("type") != OFFER_TYPE:
        problems.append("not a Muse connect offer")
    if offer.get("version") != 1:
        problems.append("unsupported offer version")
    core = {k: v for k, v in offer.items() if k != "signature"}
    sig = offer.get("signature") or ""
    pub = (offer.get("xod") or {}).get("sign_pub") or ""
    if not pub or not sig:
        problems.append("offer carries no key or no signature")
    elif not mc.verify(pub, mc.canonical(core), sig):
        problems.append("signature does not verify")
    if offer.get("code") and offer.get("code") != confirm_code(core):
        problems.append("confirm code does not match the payload")
    exp_ts = _parse_iso(offer.get("expires_at") or "")
    if not exp_ts:
        problems.append("expiry is unreadable")
    if exp_ts and (now if now is not None else _now()) > exp_ts:
        problems.append("offer has expired")
    return (not problems), problems


def offer_link(offer: Dict[str, Any]) -> str:
    return "xod://connect?offer=" + mc.b64e(mc.canonical(offer))


def offer_text_block(offer: Dict[str, Any]) -> str:
    code = offer.get("code") or ""
    grouped = f"{code[:4]} {code[4:]}" if len(code) == 8 else code
    return "\n".join([
        "XOD CONNECT OFFER",
        f"Code: {grouped}",
        mc.b64e(mc.canonical(offer)),
        "Paste this whole block to your Muse.",
    ])


# ---------------------------------------------------------------- flow

def create_offer() -> Dict[str, Any]:
    """Build, sign, store and (best effort) drop an offer into the
    family tree-inbox for family-local Muses. Returns the panel
    view: offer, link, text block, state."""
    offer = build_offer()
    store = _load_store()
    dropped = False
    inbox = _inbox_dir()
    if inbox is not None:
        try:
            inbox.mkdir(parents=True, exist_ok=True)
            path = inbox / f"muse-connect-offer-{offer['offer_id']}.json"
            path.write_text(json.dumps(offer, indent=1), encoding="utf-8")
            dropped = True
        except OSError:
            dropped = False
    store["offers"][offer["offer_id"]] = {
        "offer": offer, "state": "offered", "inbox_dropped": dropped,
    }
    _save_store(store)
    return {"offer": offer, "link": offer_link(offer),
            "text_block": offer_text_block(offer),
            "state": "offered", "inbox_dropped": dropped}


def _offer_state(rec: Dict[str, Any], now: float) -> str:
    if rec.get("state") == "revoked":
        return "revoked"
    offer = rec.get("offer") or {}
    conn = rec.get("connection_state")
    if conn:
        return conn
    exp_ts = _parse_iso(offer.get("expires_at") or "")
    if exp_ts and now > exp_ts:
        return "expired"
    return rec.get("state") or "offered"


def scan_inbox(store: Dict[str, Any]) -> bool:
    """Fold any hellos and revocations sitting in the tree-inbox
    into the store. A hello for a live offer marks the connection
    accepted and drops a receipt back into the inbox; the receipt
    is what flips the Muse side to live. Returns True when the
    store changed."""
    inbox = _inbox_dir()
    if inbox is None or not inbox.is_dir():
        return False
    changed = False
    for path in sorted(inbox.glob("muse-connect-hello-*.json")):
        try:
            hello = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if hello.get("type") != HELLO_TYPE:
            continue
        offer_id = hello.get("offer_id") or ""
        rec = store["offers"].get(offer_id)
        if rec is None or rec.get("state") == "revoked":
            continue
        conn = store["connections"].get(offer_id)
        if conn is None:
            store["connections"][offer_id] = {
                "offer_id": offer_id,
                "muse": hello.get("muse") or {},
                "state": "accepted",
                "accepted_at": hello.get("accepted_at") or _iso(_now()),
                "live_at": None,
                "revoked_at": None,
            }
            conn = store["connections"][offer_id]
            changed = True
        if conn.get("state") == "accepted":
            receipt = {
                "type": "xod-muse-connect-receipt",
                "offer_id": offer_id,
                "state": "live",
                "received_at": _iso(_now()),
            }
            try:
                (inbox / f"muse-connect-receipt-{offer_id}.json") \
                    .write_text(json.dumps(receipt, indent=1),
                                encoding="utf-8")
                conn["state"] = "live"
                conn["live_at"] = receipt["received_at"]
                changed = True
            except OSError:
                pass
    for path in sorted(inbox.glob("muse-connect-revoke-*.json")):
        try:
            note = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if note.get("type") != REVOKE_TYPE:
            continue
        offer_id = note.get("offer_id") or ""
        conn = store["connections"].get(offer_id)
        if conn and conn.get("state") != "revoked":
            conn["state"] = "revoked"
            conn["revoked_at"] = note.get("revoked_at") or _iso(_now())
            changed = True
    return changed


def status() -> Dict[str, Any]:
    """The panel view: every offer with its folded state, and every
    connection. Reading status is what advances accepted to live,
    so the panel polling is the v1 engine; nothing here raises."""
    store = _load_store()
    if scan_inbox(store):
        _save_store(store)
    now = _now()
    offers = []
    for offer_id, rec in sorted(store["offers"].items()):
        conn = store["connections"].get(offer_id)
        rec_view = dict(rec)
        rec_view["connection_state"] = conn.get("state") if conn else None
        offers.append({
            "offer_id": offer_id,
            "offer": rec.get("offer"),
            "state": _offer_state(rec_view, now),
            "inbox_dropped": bool(rec.get("inbox_dropped")),
            "link": offer_link(rec["offer"]) if rec.get("offer") else "",
            "text_block": offer_text_block(rec["offer"])
            if rec.get("offer") else "",
        })
    connections = []
    for offer_id, conn in sorted(store["connections"].items()):
        rec = store["offers"].get(offer_id) or {}
        offer = rec.get("offer") or {}
        connections.append({
            "offer_id": offer_id,
            "muse": conn.get("muse") or {},
            "state": conn.get("state"),
            "code": offer.get("code") or "",
            "xod_name": (offer.get("xod") or {}).get("name") or "",
            "accepted_at": conn.get("accepted_at"),
            "live_at": conn.get("live_at"),
            "revoked_at": conn.get("revoked_at"),
        })
    return {"offers": offers, "connections": connections}


def record_connection(offer: Dict[str, Any], muse: Dict[str, Any],
                      state: str) -> Dict[str, Any]:
    """Register an internally made connection (Auto-Connect,
    services/auto_connect.py): the offer goes into the store as the
    audit trail and the connection starts in the given state,
    without a hello round trip. The panel and the revoke path treat
    it like any other connection from then on."""
    store = _load_store()
    offer_id = offer["offer_id"]
    if offer_id not in store["offers"]:
        store["offers"][offer_id] = {
            "offer": offer, "state": "offered", "inbox_dropped": False,
            "origin": "auto-connect",
        }
    if offer_id not in store["connections"]:
        store["connections"][offer_id] = {
            "offer_id": offer_id,
            "muse": muse,
            "state": state,
            "accepted_at": _iso(_now()),
            "live_at": None,
            "revoked_at": None,
        }
    _save_store(store)
    return store["connections"][offer_id]


def update_connection_state(offer_id: str, state: str) -> bool:
    """Move a stored connection to a new state (Auto-Connect's
    verification step). Returns False when no such connection."""
    store = _load_store()
    conn = store["connections"].get(offer_id)
    if conn is None:
        return False
    conn["state"] = state
    if state == "live" and not conn.get("live_at"):
        conn["live_at"] = _iso(_now())
    _save_store(store)
    return True


def revoke(offer_id: str) -> Dict[str, Any]:
    """End a connection from this side: stored state flips at once
    and a revocation note lands in the inbox so the Muse closes
    its side too."""
    store = _load_store()
    conn = store["connections"].get(offer_id)
    if conn is None:
        rec = store["offers"].get(offer_id)
        if rec is None:
            return {"success": False, "error": "No such connection."}
        rec["state"] = "revoked"
    else:
        conn["state"] = "revoked"
        conn["revoked_at"] = _iso(_now())
    _save_store(store)
    inbox = _inbox_dir()
    if inbox is not None:
        try:
            inbox.mkdir(parents=True, exist_ok=True)
            note = {"type": REVOKE_TYPE, "offer_id": offer_id,
                    "revoked_at": _iso(_now()), "by": "xod"}
            (inbox / f"muse-connect-revoke-{offer_id}.json") \
                .write_text(json.dumps(note, indent=1), encoding="utf-8")
        except OSError:
            pass
    return {"success": True, "offer_id": offer_id, "state": "revoked"}
