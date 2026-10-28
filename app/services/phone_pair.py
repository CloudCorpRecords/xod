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
set up with. The mail itself is end-to-end encrypted either way.
"""
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from typing import Any, Dict, Optional

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
        if ts:
            return f"http://{ts['name']}:{home_post.PORT}"
        return f"http://{home_post.local_name()}.local:{home_post.PORT}"
    if domain:
        return agent_mail.relay_base(domain)
    nets = [n for n in home_post.networks() if n.get("domain")]
    if nets:
        return f"http://{nets[0]['domain']}"
    return f"http://{home_post.local_name()}.local:{home_post.PORT}"


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
               "code": code, "expires_at": int(expires)}
    text = json.dumps(payload, separators=(",", ":"))
    return {"success": True, "payload": payload, "qr_text": text, "qr_svg": qr_svg(text),
            "safety": st.get("safety_number"), "computer": st.get("address"),
            "post_office": payload["post_office"], "expires_at": int(expires)}


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


def cancel() -> None:
    with _lock:
        _save({})
