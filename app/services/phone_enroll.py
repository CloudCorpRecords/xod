"""
Phase 1: the phone joins the family by sign-in, not by hunting a QR.

The phone signs in with the family account (GitHub, through rdrive)
and advertises itself to this Xod: a family record it signed with its
own family key (services/family_registry.py). One tap here — or a
signed approval from an already-enrolled phone — files that record
into the signed family registry, and the phone is a full member: its
own Ed25519 key, per-device revocation, the safety number compared on
both screens exactly as the mail pairing has always done. The QR
pair-offer (services/phone_pair.py) remains as the offline fallback.

The advertisement carries two phone-signed variants of the record,
because a record's signature covers its status and the registry's
trust comes from the approver's digest signature, never from the
status word: "joining" (the intent the panel displays) and "active"
(filed byte-identical on approval; inert until a trusted approval
covers the set — a forged unapproved record earns no trust, as the
family vault suite proves). Revocation appends a signed revocation
entry and never edits the record; a revoked key cannot come back,
a rotated key re-enrolls by replacing the record inside the same
approver-signed write.

Submission is not trust. Anyone may submit an advertisement (the
intake endpoint is unauthenticated, like a join request); the
self-signature proves the key, the account/family binding proves the
intent, and only the owner's tap or a trusted member's signature
enrolls. Intake refuses a device id that collides with an existing
member under a different key (impersonation), and every intake is
audit-logged with the key fingerprint, so a foreign or canary key
showing up in an advertisement is visible in the log.

Local presence (the owner's rule, 2026-11-05): "if device is on Bluetooth
or same wifi and it sees it on the list in xod it can just approve
it." When the transport that carried an advertisement can evidence
that the device is HERE — the request arrived from this computer's
own local network, classified by services/local_presence.py from the
observed source address, never from anything the advertisement
claims — intake runs the owner's approval itself once the
advertisement has passed the full gate above and pended. The gate
does not move: signatures, family/account binding and the signed
GitHub sign-in assertion are all still required, a prior owner
denial is never overridden, and an ask with no presence evidence
(the Agent Mail channel, a tailnet or off-LAN source) pends for the
tap exactly as before. Bluetooth is not an evidence channel: the
desktop's BLE surface observes only an anonymous service UUID and a
user-set name with rotating addresses, nothing that binds a
sighting to a signed advertisement (see local_presence.py). Every
auto-approval is audit-logged with the evidence and the registry
state before/after, and the rule is the owner's setting
(auto_approve_enabled, default on).

Secrets: private keys live only in the OS keystore (this device,
services/keychain.py through family_registry.device_identity) and in
the phone's Keystore-wrapped vault. This module stores and moves
public keys and signatures only, and never logs key material beyond
a short fingerprint.
"""
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR
from app.services import family_registry as fr
from app.services import mail_crypto as mc

logger = logging.getLogger("xod.phone_enroll")

STATE_PATH = DATA_DIR / "phone_enroll.json"
ADVERTISE_KIND = "xod-phone-advertise"
ADVERTISE_TTL = 2 * 3600        # an advertisement is a live ask, not a standing claim
FUTURE_SKEW = 300
_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _fingerprint(public_key: str) -> str:
    return hashlib.sha256((public_key or "").encode()).hexdigest()[:12]


def safety_for(record: Dict[str, Any]) -> str:
    """The number both screens show for comparison — the mail pairing's
    own formula over the phone's family keys."""
    return mc.safety_number(record.get("public_key") or "", record.get("box_key") or "")


# ---------------------------------------------------------------- state file

def _load() -> Dict[str, Any]:
    try:
        st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def _save(st: Dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(st, indent=1), encoding="utf-8")
    except Exception as e:
        logger.warning(f"phone_enroll: {e}")


def _audit(kind: str, device_id: str, public_key: str) -> None:
    try:
        from app.services import event_log
        event_log.emit(kind, actor="phone_enroll",
                       ids={"device": device_id, "key": _fingerprint(public_key)})
    except Exception:
        pass  # the audit line is evidence, never a reason to fail the flow


# ------------------------------------------------- local-presence rule

AUTO_SETTING_KEY = "phone_enroll_auto_approve_local"


def auto_approve_enabled() -> bool:
    """The owner's local-presence rule is the default (2026-11-05); this
    setting is the owner's override, stored like every other switch
    (services/storage, the auto_model pattern)."""
    try:
        from app.services.storage import get_setting
        return get_setting(AUTO_SETTING_KEY, "1") == "1"
    except Exception:
        return True


def set_auto_approve_enabled(on: bool) -> bool:
    from app.services.storage import set_setting
    set_setting(AUTO_SETTING_KEY, "1" if on else "0")
    return auto_approve_enabled()


def _registry_snapshot(view: Dict[str, Any], device_id: str) -> Dict[str, Any]:
    rec = next((d for d in view.get("devices") or [] if d["id"] == device_id), None)
    return {"members": len(view.get("devices") or []),
            "device_status": (rec or {}).get("status"),
            "device_trusted": fr.trusted_member(view, device_id) is not None}


def _try_auto_approve(device_id: str, record: Dict[str, Any],
                      presence: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The owner's tap, taken by the rule: the advertisement has
    already passed the full pairing gate and pended, and the
    transport evidenced local presence. Runs approve() itself — the
    same signing authority, the same read-back — then audit-logs the
    evidence and the registry state before/after. Returns the
    enrolled result, or None to leave the ask pending (rule off, or
    this device cannot sign approvals; never a failure of intake)."""
    if not auto_approve_enabled():
        return None
    before = _registry_snapshot(fr.load_registry(), device_id)
    r = approve(device_id)
    pub = record.get("public_key") or ""
    if not r.get("ok"):
        try:
            from app.services import event_log
            event_log.emit("family.auto_approve_unavailable", actor="phone_enroll",
                           ids={"device": device_id, "key": _fingerprint(pub),
                                "evidence": presence.get("kind")},
                           payload={"reason": r.get("error") or "", "presence": presence})
        except Exception:
            pass
        return None
    after = _registry_snapshot(fr.load_registry(), device_id)
    entry = {"device_id": device_id, "name": record.get("name"),
             "at": _now_iso(), "evidence": presence.get("kind"),
             "source": presence.get("source"), "detail": presence.get("detail"),
             "before": before, "after": after}
    with _lock:
        st = _load()
        log = st.get("auto_approvals") or []
        log.append(entry)
        st["auto_approvals"] = log[-50:]
        _save(st)
    try:
        from app.services import event_log
        event_log.emit("family.auto_approved", actor="phone_enroll",
                       ids={"device": device_id, "key": _fingerprint(pub),
                            "evidence": presence.get("kind")},
                       payload={"presence": presence, "before": before, "after": after})
    except Exception:
        pass
    return {"ok": True, "state": "enrolled", "device_id": device_id,
            "auto_approved": True, "presence": presence,
            "control_paired": r.get("control_paired"), "member": r.get("member")}


# ---------------------------------------------------------------- registry IO

def _raw_path(path=None) -> Path:
    return Path(path).expanduser() if path else fr.registry_path()


def _load_raw(path=None) -> Optional[Dict[str, Any]]:
    p = _raw_path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("devices"), list) else None


def _write_raw(data: Dict[str, Any], path=None) -> None:
    p = _raw_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = dict(data)
    out["updated"] = _now_iso()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _observe_trust(view: Dict[str, Any]) -> Dict[str, str]:
    """Remember the last verified trusted set (id -> public key). Any set
    change untrusts everyone in the file until re-approved; approvals by
    a phone are authorized against this last-known-good set, with the
    approver's key re-verified against the current file."""
    st = _load()
    known = st.get("trusted_keys") or {}
    if view.get("approval_verified"):
        current = {d["id"]: d["public_key"] for d in view.get("trusted_members") or []}
        if current != known:
            st["trusted_keys"] = current
            _save(st)
            known = current
    return known


def _my_authority(view: Dict[str, Any]):
    """(my_id, identity) when this device may sign family approvals:
    its registry record is trusted (or it is the founder approver) and
    the keychain identity matches the record. Else (None, reason)."""
    my_id = fr.my_device_id()
    if not my_id:
        return None, "this device has no family device id yet (setting family_device_id)"
    record = next((d for d in view.get("devices") or [] if d["id"] == my_id), None)
    if record is None:
        return None, f"this device ({my_id}) has no record in the family registry"
    trusted = fr.trusted_member(view, my_id) is not None
    founder = bool(view.get("approval_verified") and view.get("approved_by") == my_id)
    known = _observe_trust(view)
    remembered = known.get(my_id) == record.get("public_key")
    if not (trusted or founder or remembered):
        return None, f"this device ({my_id}) is not a trusted family member, so it cannot approve joins"
    ident = fr.device_identity()
    if ident.sign_pub != record.get("public_key"):
        return None, "this device's keychain identity does not match its registry record"
    return (my_id, ident), ""


# ---------------------------------------------------------------- intake

def intake(advertisement: Dict[str, Any], mail_sender: Optional[str] = None,
           presence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Take a phone's signed advertisement and pend it for the owner's tap.
    `presence` is local-presence evidence observed by the TRANSPORT
    (services/local_presence.py), never a claim inside the
    advertisement: with it, and the full gate passed, the owner's rule
    (2026-11-05) approves the ask without the tap. Returns
    {ok, state, problems}; never raises on bad input."""
    problems: List[str] = []
    adv = advertisement if isinstance(advertisement, dict) else {}
    if adv.get("kind") != ADVERTISE_KIND:
        problems.append("not a phone advertisement")
    record = adv.get("record") if isinstance(adv.get("record"), dict) else {}
    active = adv.get("record_active") if isinstance(adv.get("record_active"), dict) else {}
    rid = record.get("id")
    if not (isinstance(rid, str) and rid.strip()):
        problems.append("the advertisement's record has no device id")
        return {"ok": False, "state": "refused", "problems": problems}
    mail = (adv.get("mail_address") or "").strip().lower()
    if mail_sender and mail != mail_sender.strip().lower():
        problems.append("the advertisement names a different mail address than its sender")
    try:
        issued = float(adv.get("issued_at") or 0)
    except Exception:
        issued = 0
    now = time.time()
    if issued < now - ADVERTISE_TTL:
        problems.append("the advertisement is stale (over two hours old); ask again from the phone")
    elif issued > now + FUTURE_SKEW:
        problems.append("the advertisement is dated in the future")
    view = fr.load_registry()
    if view.get("state") != "ok":
        problems.append("this computer has no readable family registry yet, so nothing can enroll")
        return {"ok": False, "state": "refused", "problems": problems}
    family, account = adv.get("family"), adv.get("account")
    if family != view.get("family"):
        problems.append("the advertisement is for a different family than this computer's registry")
    if account != view.get("account"):
        problems.append("the advertisement is bound to a different account than this family's")
    if record.get("status") != "joining":
        problems.append("the advertised record must be signed as 'joining' (the intent the panel shows)")
    if active.get("status") != "active":
        problems.append("the enrollment variant must be signed as 'active'")
    if not problems:
        if not fr.verify_record(record, family, account):
            problems.append("the record's signature does not verify against its own key")
        elif not fr.verify_record(active, family, account):
            problems.append("the active variant's signature does not verify against its own key")
        else:
            for f in fr.CORE_FIELDS:
                if f == "status":
                    continue
                if record.get(f) != active.get(f):
                    problems.append("the active variant is not the joining record's twin")
                    break
    pub = record.get("public_key") or ""
    # Standing rule (2026-11-02): the phone pairs/enrolls only when it is
    # properly signed in — the advertisement must carry the phone's signed
    # sign-in assertion (GitHub, the family account, fresh, signed by the
    # very key being enrolled). The QR fallback changes nothing here:
    # enrollment only ever consumes advertisements that passed intake.
    sso = adv.get("sso") if isinstance(adv.get("sso"), dict) else None
    if sso is None:
        problems.append("the advertisement carries no sign-in proof; the phone must be "
                        "signed in with the family account (GitHub) before it can ask to join")
    else:
        if sso.get("provider") != "github":
            problems.append("the family's sign-in is GitHub; the phone's session must be a GitHub session")
        if sso.get("account") != view.get("account"):
            problems.append("the phone's signed-in account does not match this family's account")
        try:
            sso_issued = float(sso.get("issued_at") or 0)
        except Exception:
            sso_issued = 0
        if sso_issued < now - ADVERTISE_TTL or sso_issued > now + FUTURE_SKEW:
            problems.append("the sign-in proof is stale; sign in again on the phone and ask once more")
        payload = {"account": sso.get("account"), "device_id": rid,
                   "issued_at": sso.get("issued_at"), "provider": sso.get("provider")}
        if pub and not mc.verify(pub, mc.canonical(payload), sso.get("signature") or ""):
            problems.append("the sign-in proof's signature does not verify against the phone's family key")
    existing = {d["id"]: d for d in view.get("devices") or []}
    ex = existing.get(rid)
    if ex and not problems:
        if ex.get("public_key") == pub:
            if ex.get("status") == "active":
                return {"ok": True, "state": "already_enrolled", "device_id": rid}
            if ex.get("status") == "revoked":
                problems.append("this family key was revoked; rotate the family key on the "
                                  "phone and advertise again under the new key")
            else:
                problems.append("this device is already waiting in the registry itself")
        elif ex.get("status") != "revoked":
            problems.append(f"device id {rid} already belongs to a family member under a "
                            "different key (impersonation refused)")
    if problems:
        _audit("family.advertise_refused", rid, pub)
        return {"ok": False, "state": "refused", "problems": problems}
    fp = f"{rid}|{pub}"
    with _lock:
        st = _load()
        denied = st.get("denied") or {}
        if fp in denied and issued <= float(denied[fp].get("issued_at") or 0):
            return {"ok": False, "state": "denied",
                    "problems": ["the owner denied this phone's last ask; ask again "
                                 "from the phone to re-pend it"]}
        pend = st.get("pending") or {}
        prior = pend.get(rid)
        if prior and (prior.get("record") or {}).get("public_key") != pub:
            return {"ok": False, "state": "refused",
                    "problems": ["a different key is already waiting under this device id"]}
        pend[rid] = {"record": record, "record_active": active, "account": account,
                     "provider": adv.get("provider"), "family": family,
                     "mail_address": mail, "issued_at": issued, "received_at": now,
                     "presence": presence}
        st["pending"] = pend
        if mail:
            links = st.get("links") or {}
            links[mail] = rid
            st["links"] = links
        _save(st)
    _audit("family.advertise", rid, pub)
    result: Dict[str, Any] = {"ok": True, "state": "pending", "device_id": rid,
                              "safety": safety_for(record)}
    if presence:
        result["presence"] = presence
        auto = _try_auto_approve(rid, record, presence)
        if auto is not None:
            return auto
    return result


def pending() -> List[Dict[str, Any]]:
    st = _load()
    out = []
    for rid, e in sorted((st.get("pending") or {}).items()):
        rec = e.get("record") or {}
        out.append({"record": rec, "safety": safety_for(rec),
                    "mail_address": e.get("mail_address") or "",
                    "provider": e.get("provider"), "account": e.get("account"),
                    "issued_at": e.get("issued_at"), "received_at": e.get("received_at"),
                    "presence": e.get("presence")})
    return out


# ---------------------------------------------------------------- enrollment

def approve(device_id: str, path=None) -> Dict[str, Any]:
    """The owner's one tap: file the phone's own active record and sign
    the member digest over the new set. Read back before claiming it.
    The owner's local-presence rule takes this same tap through
    _try_auto_approve; there is no second, weaker approval path."""
    view = fr.load_registry(path)
    _observe_trust(view)
    existing = next((d for d in view.get("devices") or [] if d["id"] == device_id), None)
    if existing and existing.get("status") == "active" \
            and fr.trusted_member(view, device_id) is not None:
        return {"ok": True, "state": "already_enrolled", "device_id": device_id}
    with _lock:
        st = _load()
        entry = (st.get("pending") or {}).get(device_id)
    if entry is None:
        return {"ok": False, "error": f"no pending advertisement for {device_id}"}
    auth, why = _my_authority(view)
    if auth is None:
        return {"ok": False, "error": why}
    my_id, ident = auth
    record_active = entry["record_active"]
    raw = _load_raw(path)
    if raw is None:
        return {"ok": False, "error": "the family registry could not be read for writing"}
    devices = []
    replaced = False
    for d in raw["devices"]:
        if isinstance(d, dict) and d.get("id") == device_id:
            if d.get("public_key") != record_active.get("public_key") \
                    and next((x for x in view["devices"] if x["id"] == device_id), {}).get("status") != "revoked":
                return {"ok": False, "error": f"{device_id} already has a different key in the registry"}
            devices.append(record_active)
            replaced = True
        else:
            devices.append(d)
    if not replaced:
        devices.append(record_active)
    raw["devices"] = devices
    if any(e.get("device_id") == device_id for e in raw.get("revocations") or []):
        raw["revocations"] = [e for e in raw["revocations"] if e.get("device_id") != device_id]
    signed = fr.sign_approval(raw, ident, my_id)
    _write_raw(signed, path)
    view2 = fr.load_registry(path)
    member = fr.trusted_member(view2, device_id)
    if member is None:
        return {"ok": False, "error": "the registry write did not verify on read-back; the phone is not enrolled"}
    with _lock:
        st = _load()
        (st.get("pending") or {}).pop(device_id, None)
        _save(st)
    _observe_trust(view2)
    control = False
    mail = entry.get("mail_address") or ""
    if mail:
        try:
            from app.services import phone_mcp
            control = any(p.get("address") == mail for p in phone_mcp.paired_phones())
        except Exception:
            control = False
    _audit("family.enrolled", device_id, record_active.get("public_key") or "")
    return {"ok": True, "state": "enrolled", "device_id": device_id,
            "control_paired": control, "member": member}


def deny(device_id: str) -> Dict[str, Any]:
    with _lock:
        st = _load()
        entry = (st.get("pending") or {}).pop(device_id, None)
        if entry is None:
            return {"ok": False, "error": f"no pending advertisement for {device_id}"}
        pub = (entry.get("record") or {}).get("public_key") or ""
        denied = st.get("denied") or {}
        denied[f"{device_id}|{pub}"] = {"device_id": device_id,
                                        "issued_at": entry.get("issued_at") or 0,
                                        "at": time.time()}
        st["denied"] = denied
        _save(st)
    _audit("family.denied", device_id, pub)
    return {"ok": True, "state": "denied", "device_id": device_id}


def revoke(device_id: str, path=None) -> Dict[str, Any]:
    """Append a signed revocation (the record itself is never edited)
    and read back that the device is out."""
    view = fr.load_registry(path)
    rec = next((d for d in view.get("devices") or [] if d["id"] == device_id), None)
    if rec is None:
        return {"ok": False, "error": f"{device_id} is not in the family registry"}
    if rec.get("status") == "revoked":
        return {"ok": True, "state": "already_revoked", "device_id": device_id}
    auth, why = _my_authority(view)
    if auth is None:
        return {"ok": False, "error": why}
    my_id, ident = auth
    raw = _load_raw(path)
    if raw is None:
        return {"ok": False, "error": "the family registry could not be read for writing"}
    raw.setdefault("revocations", []).append(fr.sign_revocation(raw, ident, my_id, device_id))
    _write_raw(raw, path)
    view2 = fr.load_registry(path)
    rec2 = next((d for d in view2.get("devices") or [] if d["id"] == device_id), None)
    if rec2 is None or rec2.get("status") != "revoked" \
            or fr.trusted_member(view2, device_id) is not None:
        return {"ok": False, "error": "the revocation did not verify on read-back"}
    _audit("family.revoked", device_id, rec.get("public_key") or "")
    return {"ok": True, "state": "revoked", "device_id": device_id}


# ---------------------------------------------------------------- phone as approver

def join_candidates(path=None) -> List[Dict[str, Any]]:
    """Devices in the registry file whose verified record is not covered
    by the last known-good approval — what an approver (this screen, or
    an enrolled phone) is being asked to sign for. The digest is the
    exact set an approval signature would cover."""
    view = fr.load_registry(path)
    known = _observe_trust(view)
    raw = _load_raw(path)
    if raw is None:
        return []
    digest = fr.member_digest(raw)
    out = []
    for d in view.get("devices") or []:
        if d.get("verified") and d.get("status") == "active" \
                and known.get(d["id"]) != d.get("public_key"):
            out.append({"record": d, "digest": digest, "safety": safety_for(d)})
    return out


def apply_phone_approval(device_id: str, signature: str, approver_id: str, path=None) -> Dict[str, Any]:
    """File an approval signed by an enrolled phone: the signer must be
    in the last known-good trusted set under the same key it holds in
    the current file, and the signature must cover the CURRENT
    candidate digest, recomputed here — never one the caller supplies."""
    view = fr.load_registry(path)
    known = _observe_trust(view)
    raw = _load_raw(path)
    if raw is None:
        return {"ok": False, "error": "the family registry could not be read for writing"}
    digest = fr.member_digest(raw)
    if device_id not in [r["id"] for r in digest.get("devices") or []]:
        return {"ok": False, "error": f"{device_id} is not in the set awaiting approval"}
    approver = next((d for d in raw["devices"] if isinstance(d, dict) and d.get("id") == approver_id), None)
    if approver is None or known.get(approver_id) != approver.get("public_key"):
        return {"ok": False, "error": f"{approver_id} is not a trusted family member, so it cannot approve joins"}
    if not fr.verify_record(approver, raw.get("family"), raw.get("account")):
        return {"ok": False, "error": "the approver's own record does not verify in the current registry"}
    if not mc.verify(approver["public_key"], mc.canonical(digest), signature or ""):
        return {"ok": False, "error": "the approval signature does not cover the current member set"}
    raw["approved_by"] = approver_id
    raw["approval_signature"] = signature
    _write_raw(raw, path)
    view2 = fr.load_registry(path)
    if not view2.get("approval_verified") or fr.trusted_member(view2, device_id) is None:
        return {"ok": False, "error": "the phone's approval did not verify on read-back"}
    _observe_trust(view2)
    _audit("family.approved_by_phone", device_id, approver.get("public_key") or "")
    return {"ok": True, "state": "enrolled", "device_id": device_id, "approved_by": approver_id}


# ---------------------------------------------------------------- gates + state

def control_refused(mail_address: str) -> bool:
    """True when the phone linked to this mail address has been revoked
    from the family: control in both directions refuses (wired into
    phone_api.allowed and the desktop->phone dispatch)."""
    addr = (mail_address or "").strip().lower()
    if not addr:
        return False
    st = _load()
    device_id = (st.get("links") or {}).get(addr)
    if not device_id:
        return False
    view = fr.load_registry()
    if device_id in (view.get("revoked_by_registry") or []):
        return True
    rec = next((d for d in view.get("devices") or [] if d["id"] == device_id), None)
    return bool(rec and rec.get("status") == "revoked")


def _github_client_id() -> Optional[str]:
    try:
        from app.services import sso_control
        return sso_control.github_client_id() or None
    except Exception:
        return None


def state(path=None) -> Dict[str, Any]:
    """Everything the Phones panel's family section (and the phone's own
    family screen) renders: the account up top, pending asks with their
    safety numbers, members, and joins awaiting an approver."""
    view = fr.load_registry(path)
    known = _observe_trust(view)
    st = _load()
    links = st.get("links") or {}
    by_device = {}
    for addr, did in links.items():
        by_device.setdefault(did, addr)
    members = []
    for d in view.get("devices") or []:
        members.append({"id": d["id"], "name": d["name"], "platform": d["platform"],
                        "status": d["status"], "verified": bool(d.get("verified")),
                        "trusted": known.get(d["id"]) == d.get("public_key")
                        and d.get("status") == "active",
                        "public_key": d["public_key"], "safety": safety_for(d),
                        "capabilities": d.get("capabilities") or [],
                        "last_seen": d.get("last_seen"),
                        "mail_address": by_device.get(d["id"])})
    pend = pending()
    provider = next((p.get("provider") for p in pend if p.get("provider")), None)
    return {"family": view.get("family"), "account": view.get("account"),
            # The raw registry (public keys + signatures only — the synced
            # family file is public by design) so the PHONE can verify the
            # membership proof itself instead of trusting this computer's
            # word for it (EnrollmentGate in the companion).
            "registry": _load_raw(path),
            "provider": provider, "registry_state": view.get("state"),
            # Xod's GitHub app (public client id) so a phone can "Sign in with GitHub" without one built in.
            "github_client_id": _github_client_id(),
            "interim_unsigned": bool(view.get("interim_unsigned")),
            "my_device_id": fr.my_device_id() or None,
            "my_trusted": bool(fr.my_device_id() and known.get(fr.my_device_id())),
            # The owner's local-presence rule (2026-11-05): its on/off state and the
            # audit trail of approvals it took, newest first, for the panel.
            "auto_approve_local": auto_approve_enabled(),
            "auto_approvals": list(reversed((st.get("auto_approvals") or [])[-10:])),
            "pending": pend, "members": members, "joining": join_candidates(path)}
