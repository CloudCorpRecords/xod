"""
Family Link: the family key channel — keystore work done by the
device's OWN running Xod service, on behalf of an authenticated
family requester (R12, the owner's family session rule, 2026-11-09).

The rule: a device that is enrolled in the family (signed registry
membership) and signed into the family SSO can use the family keys.
The OS keystore's session walls are not the trust boundary and must
not act like one: on macOS a process outside the GUI session cannot
read the Keychain item (-25308, errSecInteractionNotAllowed — the
wall every SSH-launched process hit in the 2026-11-09 benches), and
on Windows Credential Manager refuses writes from service and SSH
sessions (error 1312, the 2026-11-02 ceremony). A headless process
therefore never touches a keystore it cannot reach. It asks the
device's own Xod service — the process that lives in the session
that CAN reach the keystore (the desktop app / interactive-session
worker; the Nov 2 Windows proof already worked exactly this way) —
and the service performs the read/unwrap/delivery, or the keystore
write, itself.

Wire: POST /v1/family/keys on the serving device's beacon
(services/xod_network.py, the tailnet-only listener that also
carries /v1/family/inference). Three checks, all server-side:

  1. membership: the caller is in the signed registry's
     trusted_members (services/family_registry.py), and the request
     body is signed with that device's registry key;
  2. SSO: the request carries the caller's signed sign-in assertion
     in the phone_enroll shape (GitHub, the family account, fresh,
     signed by the very key asking) — the R6 gate's second leg;
  3. recipient: for delivery, the caller must be a recipient of the
     current vault bundle — enrollment alone earns nothing the
     bundle does not wrap for it.

Ops:
  - "deliver": the serving device unwraps the family bundle with
    its own identity (falling back to its own keystore) and returns
    the requested fields SEALED to the caller's box key
    (services/mail_crypto.py). Values cross the wire only inside
    that envelope, exist in plaintext only in the two processes'
    memory, and are never logged: the audit trail (event log, like
    phone_enroll's) records device ids and field NAMES only.
  - "apply": the serving device runs family_vault.apply() in its
    own process — the keystore write happens in the session that
    can do it — and reports the apply state.

Failure is explicit, mirroring family_inference: a refusal is a
plain reason (KeyRefused, HTTP 403 at the beacon); an unreachable
server is KeyBlocked on the caller, never a silent wait. A device
with family_keys_serve="off" serves nothing. Nothing here prints,
logs, or returns a key value outside the sealed delivery envelope.
"""
import os
import time

from app.services import family_registry, family_vault, mail_crypto as mc

BEACON_PORT = 8791
CLOCK_SKEW = 600
SSO_TTL = 2 * 3600          # the phone_enroll advertisement window
SSO_FUTURE_SKEW = 300
SERVE_SETTING = "family_keys_serve"
_ADDRESS = "xod-family:{}"
MAX_FIELDS = 16


class KeyBlocked(Exception):
    """The key request could not happen, stated plainly."""


class KeyRefused(PermissionError):
    """The serving device refused this caller. Maps to HTTP 403."""


def _setting(name, default=""):
    try:
        from app.services.storage import get_setting
        return get_setting(name, default) or default
    except Exception:
        return default


def serve_enabled() -> bool:
    return _setting(SERVE_SETTING, "on") != "off"


def _allowed_fields() -> set:
    from app.services import secret_store
    fields = set(secret_store.SECRET_FIELDS)
    try:
        from app.services import sso_control
        fields |= set(sso_control.SERVICE_SECRET_FIELDS.values())
    except Exception:
        pass
    return fields


def _audit(kind: str, device_id: str, server_id: str, payload: dict) -> None:
    try:
        from app.services import event_log
        event_log.emit(kind, actor="family_key_channel",
                       ids={"device": device_id, "server": server_id},
                       payload=payload)
    except Exception:
        pass  # the audit line is evidence, never a reason to fail the flow


# ---------------------------------------------------------------- signing

def sso_assertion(identity, account: str, device_id: str, issued_at=None) -> dict:
    """The family sign-in proof, in exactly the shape phone_enroll
    intake verifies: signed by the device key being served."""
    issued = int(issued_at if issued_at is not None else time.time())
    payload = {"account": account, "device_id": device_id,
               "issued_at": issued, "provider": "github"}
    return {**payload, "signature": identity.sign(mc.canonical(payload))}


def signed_key_request(identity, my_id: str, server_id: str, op: str,
                       fields, account: str, issued_at=None) -> dict:
    body = {"kind": "xod-family-key-request", "v": 1,
            "from": my_id, "to": server_id, "op": op,
            "fields": sorted({str(f) for f in (fields or [])}),
            "sso": sso_assertion(identity, account, my_id, issued_at),
            "ts": int(time.time()), "nonce": mc.b64e(os.urandom(12))}
    body["signature"] = identity.sign(mc.canonical(body))
    return body


def _verify_key_request(body: dict, view: dict) -> dict:
    if not isinstance(body, dict) or body.get("kind") != "xod-family-key-request":
        raise KeyRefused("not a family key request")
    caller_id = body.get("from") or ""
    record = family_registry.trusted_member(view, caller_id)
    if record is None:
        raise KeyRefused(
            "the calling device is not a trusted family member "
            "(not active, not signature-verified, or revoked)")
    sig = body.get("signature")
    if not sig:
        raise KeyRefused("the request is unsigned")
    core = {k: v for k, v in body.items() if k != "signature"}
    if not mc.verify(record["public_key"], mc.canonical(core), sig):
        raise KeyRefused("the request signature does not verify")
    ts = body.get("ts") or 0
    try:
        fresh = abs(time.time() - float(ts)) <= CLOCK_SKEW
    except (TypeError, ValueError):
        fresh = False
    if not fresh:
        raise KeyRefused("the request is stale")
    # The SSO leg (the R6 gate's second half): a fresh GitHub sign-in
    # assertion for the family account, signed by the calling key.
    sso = body.get("sso") if isinstance(body.get("sso"), dict) else None
    if sso is None:
        raise KeyRefused("the request carries no sign-in proof; the calling "
                         "device must be signed in with the family account")
    if sso.get("provider") != "github":
        raise KeyRefused("the family's sign-in is GitHub; the caller's "
                         "session must be a GitHub session")
    if sso.get("account") != view.get("account"):
        raise KeyRefused("the caller's signed-in account does not match "
                         "this family's account")
    try:
        issued = float(sso.get("issued_at") or 0)
    except (TypeError, ValueError):
        issued = 0
    now = time.time()
    if issued < now - SSO_TTL or issued > now + SSO_FUTURE_SKEW:
        raise KeyRefused("the sign-in proof is stale; sign in again and ask once more")
    payload = {"account": sso.get("account"), "device_id": caller_id,
               "issued_at": sso.get("issued_at"), "provider": sso.get("provider")}
    if not mc.verify(record["public_key"], mc.canonical(payload),
                     sso.get("signature") or ""):
        raise KeyRefused("the sign-in proof's signature does not verify "
                         "against the calling device's family key")
    op = body.get("op")
    if op not in ("deliver", "apply"):
        raise KeyRefused(f"unknown key operation {op!r}")
    fields = body.get("fields") or []
    if not isinstance(fields, list) or len(fields) > MAX_FIELDS \
            or any(not isinstance(f, str) for f in fields):
        raise KeyRefused("fields must be a short list of provider field names")
    unknown = [f for f in fields if f not in _allowed_fields()]
    if unknown:
        raise KeyRefused(f"not family key fields: {unknown}")
    if op == "deliver" and not fields:
        raise KeyRefused("a delivery request names no fields")
    return record


# ---------------------------------------------------------------- serving

def _unwrap_bundle_values(my_id: str, view: dict, identity) -> dict:
    """The current bundle's payload as {field: value}, opened with
    THIS device's identity. {} when this device cannot open its
    wrapped copy (not a recipient, or the bundle is absent)."""
    bundle = family_vault.read_bundle()
    if bundle.get("state") != "ok" or my_id not in (bundle.get("recipients") or []):
        return {}
    creator = family_registry.trusted_member(view, bundle.get("created_by") or "")
    expect = creator["public_key"] if creator else None
    key = family_vault._open_bundle_key(bundle["wrapped"][my_id], identity,
                                        my_id, expect_signer=expect)
    return family_vault._decrypt_payload(key, bundle.get("family"),
                                         int(bundle.get("version") or 0), bundle)


def handle_key_request(body: dict, view=None, my_id=None, identity=None) -> dict:
    """The serving half, transport-free so it is fully testable. The
    beacon route wraps this; the audit entry is written here, on the
    serving device, before the answer returns. Key values leave only
    inside the sealed envelope (deliver) — never in a return field,
    a log line, or an error."""
    if not serve_enabled():
        raise KeyRefused("this device does not serve family key requests")
    view = view or family_registry.load_registry()
    record = _verify_key_request(body, view)
    caller_id = record["id"]
    my_id = my_id or family_registry.my_device_id()
    if not my_id:
        raise KeyRefused("this device has no family device id")
    if body.get("to") not in (None, "", my_id):
        raise KeyRefused("the request is addressed to a different device")
    op = body.get("op")
    if op == "apply":
        # The keystore write happens HERE, in the session-resident
        # service — the whole point of the channel.
        try:
            out = family_vault.apply(my_id, view=view)
        except family_vault.VaultError as e:
            raise KeyRefused(str(e)) from e
        _audit("family.key_apply_served", caller_id, my_id,
               {"state": out.get("state"), "version": out.get("version")})
        return {"op": "apply", "served_by": my_id, "for": caller_id, **out}
    # op == "deliver": the per-recipient check comes before any value
    # is touched — the bundle must wrap for the caller.
    fields = list(body.get("fields") or [])
    bundle = family_vault.read_bundle()
    if bundle.get("state") != "ok":
        raise KeyRefused("no readable family vault bundle on this device")
    if caller_id not in (bundle.get("recipients") or []):
        raise KeyRefused("the calling device is not a recipient of the "
                         "current family vault bundle")
    if not record.get("box_key"):
        raise KeyRefused("the calling device has no box key to seal the "
                         "delivery to")
    identity = identity or family_registry.device_identity()
    try:
        values = _unwrap_bundle_values(my_id, view, identity)
    except Exception:
        values = {}
    from app.services import secret_store
    missing = []
    for field in fields:
        if field not in values:
            try:
                own = secret_store.get(field)
            except Exception:
                own = ""
            if own:
                values[field] = own
            else:
                missing.append(field)
    if missing:
        raise KeyRefused(f"this device does not hold {missing} to deliver")
    envelope = mc.seal(identity, _ADDRESS.format(my_id), _ADDRESS.format(caller_id),
                       record["box_key"],
                       {"fields": {f: values[f] for f in fields}})
    _audit("family.key_delivered", caller_id, my_id,
           {"fields": sorted(fields), "bundle_version": bundle.get("version")})
    return {"op": "deliver", "served_by": my_id, "for": caller_id,
            "envelope": envelope}


# ---------------------------------------------------------------- requesting

def request_keys(server_ip: str, server_id: str, fields=None, op: str = "deliver",
                 identity=None, my_id=None, account=None, port: int = BEACON_PORT,
                 timeout: float = 25.0) -> dict:
    """The requesting half. Raises KeyBlocked — explicitly, with the
    reason — whenever the keys cannot be fetched: this device has no
    family identity to ask under, the server is asleep/unreachable,
    or it refused. Delivered values come back in process memory
    only; nothing is stored or logged here."""
    if not my_id:
        raise KeyBlocked("this device has no family id to request keys under")
    identity = identity or family_registry.device_identity()
    if account is None:
        account = family_registry.load_registry().get("account") or ""
    body = signed_key_request(identity, my_id, server_id, op, fields, account)
    import httpx
    url = f"http://{server_ip}:{port}/v1/family/keys"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=body)
    except httpx.HTTPError as e:
        raise KeyBlocked(
            f"the serving device at {server_ip}:{port} is unreachable "
            f"({type(e).__name__}); it may be asleep or offline. "
            "Key delivery is blocked, not queued.") from e
    if r.status_code != 200:
        detail = ""
        try:
            detail = str(r.json().get("detail") or "")
        except ValueError:
            pass
        raise KeyBlocked(f"the serving device refused the key request "
                         f"({r.status_code}){': ' + detail if detail else ''}")
    out = r.json()
    if op != "deliver":
        return out
    envelope = out.get("envelope") or {}
    try:
        opened = mc.open_envelope(identity, _ADDRESS.format(my_id), envelope)
    except Exception as e:
        raise KeyBlocked(f"the delivered envelope could not be opened "
                         f"({type(e).__name__})") from e
    # Key-substitution guard: the envelope must be signed by the
    # serving device this registry knows, not by whoever answered.
    view = family_registry.load_registry()
    server = family_registry.trusted_member(view, server_id)
    signer = (opened.get("keys") or {}).get("sign")
    if not opened.get("signature_ok") or server is None \
            or signer != server.get("public_key"):
        raise KeyBlocked("the delivered envelope is not signed by the "
                         "serving device the family registry trusts")
    values = (opened.get("msg") or {}).get("fields") or {}
    if not isinstance(values, dict):
        raise KeyBlocked("the delivered envelope carries no key fields")
    return {"op": "deliver", "served_by": server_id, "for": my_id,
            "fields": {str(k): str(v) for k, v in values.items()}}


def fetch_keys_into_keystore(server_ip: str, server_id: str, fields,
                             identity=None, my_id=None, account=None,
                             port: int = BEACON_PORT, timeout: float = 25.0) -> dict:
    """Enroll -> keys for a headless requester: fetch the fields over
    the family channel and store them in THIS device's own keystore,
    ending in a presence-only read-back ({field: held}). The values
    pass through this process's memory and its own store only."""
    out = request_keys(server_ip, server_id, fields, op="deliver",
                       identity=identity, my_id=my_id, account=account,
                       port=port, timeout=timeout)
    from app.services import secret_store
    try:
        for field, value in out["fields"].items():
            secret_store.save(field, value)
    except Exception as e:
        raise KeyBlocked(f"this device's own keystore refused the delivered "
                         f"key ({type(e).__name__})") from e
    present = {f: bool(secret_store.stored(f)) for f in out["fields"]}
    return {"state": "stored", "served_by": server_id, "fields": present,
            "has_key": bool(present.get(family_vault.DEFAULT_PROVIDER_FIELD))}
