"""
Bluetooth-presence evidence for pairing auto-approve (the owner's
rule, 2026-11-05): "if device is on Bluetooth or same wifi and it
sees it on the list in xod it can just approve it."

services/local_presence.py covers the Wi-Fi half: evidence observed
by the transport from the request's source address. This module is
the Bluetooth half, and it exists because a bare BLE sighting is NOT
evidence: the desktop's BLE surface (services/phone_ble.py) observes
only an anonymous service UUID and a user-set device name, and
Android rotates BLE addresses, so nothing in a sighting, on its own,
says WHICH phone it is. The binding is cryptographic instead. While
its pairing ask is live, the phone serves a signed presence token
over the same GATT service it already uses for "Pair nearby"
(the phone side: dev.xod.android BlePairing serving what
dev.xod.mail BlePresence mints):

    {"kind": "xod-ble-presence", "device_id": ...,
     "adv_issued_at": ..., "issued_at": ..., "nonce": ...,
     "signature": ...}

The signature is the phone's family key over the canonical payload,
and adv_issued_at is the issued_at of the phone's current signed
advertisement, so the token is bound to one specific ask. The
desktop reads the token over the radio (only a device within BLE
range can answer), records the sighting here, and evidence_for()
re-verifies the signature against the ASK's public key, the key the
advertisement's own verified record carries, before the sighting
may stand in for the owner's tap in services/phone_enroll.py. A
token signed by any other key, a token minted for a different ask,
a stale token, or a stale sighting is not evidence, exactly as an
off-subnet address is not evidence in local_presence.
"""
import time
from typing import Any, Dict, Optional

from app.services import mail_crypto as mc

TOKEN_KIND = "xod-ble-presence"
TOKEN_TTL = 600        # a token stops being believable this long after it was minted
SIGHTING_TTL = 600     # a desktop observation stops counting this long after it was read
FUTURE_SKEW = 300      # matches phone_enroll.FUTURE_SKEW

_sightings: Dict[str, Dict[str, Any]] = {}   # device_id -> {"token", "observed_at", "ble_address", "rssi"}


def clear() -> None:
    _sightings.clear()


def observe(token: Any, ble_address: Optional[str] = None,
            rssi: Optional[int] = None, now: Optional[float] = None) -> bool:
    """Record a token the desktop read over BLE. Structural checks
    only: the signature is verified at evidence time, against the
    key of the ask it claims to bind to (the observer may not have
    seen the ask yet). Returns False on garbage."""
    if not isinstance(token, dict) or token.get("kind") != TOKEN_KIND:
        return False
    device_id = token.get("device_id")
    if not (isinstance(device_id, str) and device_id.strip()):
        return False
    try:
        float(token.get("issued_at") or 0)
        float(token.get("adv_issued_at") or 0)
    except Exception:
        return False
    if not (isinstance(token.get("signature"), str) and token.get("signature")):
        return False
    _sightings[device_id] = {"token": token,
                             "observed_at": time.time() if now is None else now,
                             "ble_address": ble_address, "rssi": rssi}
    return True


def evidence_for(device_id: str, public_key: str, adv_issued_at: Any,
                 now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """{"kind", "source", "detail"} when a recorded BLE sighting is
    bound to this exact ask and signed by the ask's own key, else
    None. Same return shape as local_presence.classify, so intake
    treats both channels identically."""
    s = _sightings.get(device_id)
    if s is None or not public_key:
        return None
    token = s["token"]
    if token.get("device_id") != device_id:
        return None
    try:
        if int(float(token.get("adv_issued_at") or 0)) != int(float(adv_issued_at or 0)):
            return None
        issued = float(token.get("issued_at") or 0)
    except Exception:
        return None
    payload = {k: v for k, v in token.items() if k != "signature"}
    if not mc.verify(public_key, mc.canonical(payload), token.get("signature") or ""):
        return None
    observed = float(s["observed_at"])
    now = time.time() if now is None else now
    if issued > observed + FUTURE_SKEW or observed - issued > TOKEN_TTL:
        return None
    if now - observed > SIGHTING_TTL or issued > now + FUTURE_SKEW:
        return None
    return {"kind": "bluetooth", "source": s.get("ble_address") or "ble",
            "detail": "this computer read a signed Bluetooth token from the phone nearby, "
                      "bound to this ask and signed by the phone's own family key"}
