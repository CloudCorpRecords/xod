"""
"Pair a phone" (slice 1): one QR that sets an Android phone up and pairs it.

Xod shows a QR holding {post_office, computer address, safety number,
one-time code, expiry}. The phone app scans it and, in one step, claims its
own address at that post office and pins this computer's keys. Scanning is
the only use of the code: it expires after 10 minutes, is stored here only
as a hash, and is consumed the moment a registration uses it, so a photo of
an old QR pairs nothing. The safety number is in the QR and on both
screens, so the person can still check the phone paired with *this*
computer and not someone who copied a fresh QR.

Why a code at all: this computer's post office only lets names be claimed
from the computer itself (post_office.only_here), which is what forced the
first setup onto USB + `adb reverse`. A valid, unused pairing code is the
one other way in, and only while Xod is showing the QR.

Reaching home: the post-office URL in the QR is Tailscale when present,
else the same-network (.local) name, else the shared relay Agent Mail was
set up with. The QR also lists every route to this computer's own post
office (home_post.routes(): .local name, LAN IP, Tailscale name and IP);
the phone keeps them all and uses whichever answers, so a phone paired at
home keeps working on mobile data when Tailscale is on both. Phones paired
before routes existed learn them over mail (phone_api "link.routes"), which
is sealed and signed, never from the unauthenticated post office itself.
The mail itself is end-to-end encrypted either way.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.phone_pair")

STATE_PATH = DATA_DIR / "phone_pair.json"
CODE_TTL = 600  # 10 minutes
_lock = threading.Lock()


def _load() -> Dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(st: Dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(st), encoding="utf-8")
    except Exception as e:
        logger.warning(f"phone_pair: {e}")


def _hash(code: str) -> str:
    return hashlib.sha256((code or "").strip().encode()).hexdigest()


def post_office_url() -> str:
    """How a phone reaches this computer's post office, best first."""
    from app.services import agent_mail, home_post
    cfg = agent_mail._config()
    domain = cfg.get("domain") or ""
    if cfg.get("home"):
        ts = home_post.tailscale()
        if ts and not os.environ.get("XOD_POST_ROUTES"):
            return f"http://{ts['name']}:{home_post.PORT}"
        return (home_post.routes() or [f"http://{home_post.local_name()}.local:{home_post.PORT}"])[0]
    if domain:
        return agent_mail.relay_base(domain)
    nets = [n for n in home_post.networks() if n.get("domain")]
    if nets:
        return f"http://{nets[0]['domain']}"
    return f"http://{home_post.local_name()}.local:{home_post.PORT}"


def routes() -> List[str]:
    """Every way a paired phone can reach its mailbox, starting with the QR's post office. Only this computer's own
    post office has more than one; a shared relay is reached at its one name."""
    from app.services import agent_mail, home_post
    first = post_office_url()
    more = home_post.routes() if agent_mail._config().get("home") else []
    return list(dict.fromkeys([first] + more))


def reach() -> Dict[str, Any]:
    """What the phone screens say about reaching this computer: its routes and whether any works away from home."""
    from app.services import home_post
    r = routes()
    return {"routes": r, "away": home_post.reaches_away(r), "tailscale": bool(home_post.tailscale())}


def create_offer() -> Dict[str, Any]:
    """A fresh pairing QR payload. Any previous code stops working."""
    from app.services import agent_mail
    st = agent_mail.status()
    if not st.get("set_up"):
        return {"success": False, "error": "Set up Agent Mail first (Agent Mail -> set up): the phone pairs over it."}
    code = secrets.token_urlsafe(9)
    expires = time.time() + CODE_TTL
    with _lock:
        _save({"code_hash": _hash(code), "expires_at": expires, "created_at": time.time()})
    payload = {"v": 1, "kind": "xod-phone-pair", "post_office": post_office_url(),
               "computer": st.get("address"), "safety": st.get("safety_number"),
               "code": code, "expires_at": int(expires), "routes": routes()}
    text = json.dumps(payload, separators=(",", ":"))
    return {"success": True, "payload": payload, "qr_text": text, "qr_svg": qr_svg(text),
            "safety": st.get("safety_number"), "computer": st.get("address"),
            "post_office": payload["post_office"], "expires_at": int(expires), **reach()}


def qr_svg(text: str) -> Optional[str]:
    """The QR as inline SVG (qrcode's svg factory needs no Pillow). None if the lib is missing."""
    try:
        import io

        import qrcode
        import qrcode.image.svg
        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue().decode()
    except Exception as e:
        logger.debug(f"phone_pair: no QR image ({e})")
        return None


def _live(st, code: str) -> bool:
    return bool(code and st.get("code_hash")
                and hmac.compare_digest(str(st["code_hash"]), _hash(code))
                and time.time() < float(st.get("expires_at") or 0))


def check_code(code: str) -> bool:
    """True if this is the live pairing code. Does not consume it."""
    return _live(_load(), code)


def consume_code(code: str) -> bool:
    """One-time: a code that paired a phone can never pair another. Called by the post office
    only after the registration's own signature has checked out. Check and clear are atomic
    under one lock, so two registrations racing for the same code cannot both get in."""
    with _lock:
        if not _live(_load(), code):
            return False
        _save({})
        return True


def paired(address: str, sign_pub: str, box_pub: str) -> None:
    """A phone registered with the live one-time code (from the QR it scanned, or the Bluetooth offer it accepted on
    its own screen): pin the exact keys its signed registration carried as yours and list it as your phone, so its
    requests are answered. Without this the phone believed it was paired while this computer ignored it."""
    from app.services import agent_mail, phone_mcp
    address = address.lower()
    c = agent_mail.contact(address)
    if c and (c.get("sign_pub"), c.get("box_pub")) != (sign_pub, box_pub):
        with agent_mail._db() as db:     # the same name re-paired with new keys (the app was reinstalled)
            db.execute("DELETE FROM mail_contacts WHERE address=?", (address,))
    agent_mail._pin(address, sign_pub, box_pub, "mine")
    agent_mail.set_contact(address, trust="mine")
    phone_mcp.pair_phone(address, address.split("@")[0])
    logger.info(f"phone_pair: {address} paired with this computer")


def cancel() -> None:
    with _lock:
        _save({})
