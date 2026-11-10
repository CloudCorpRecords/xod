"""
Family Link: the family registry reader, with signed records (V0).

The family registry is a small JSON file in the synced bus space (the
interim home until rdrive carries it in F1). One record per device:

  id            stable machine id ("macbook-pro")
  name          display name ("Owners-MacBook-Pro")
  platform      "macOS", "Windows 11", "Android", ...
  endpoint      tailnet address, or null when none is recorded
  capabilities  bus-style free text list ("cloud model: ...", "macOS hands")
  public_key    the device Ed25519 public key (placeholder until keys enroll)
  box_key       the device X25519 public key (family vault wrapping)
  account       the family account this record binds to
  signature     Ed25519 signature over the record core (null in F0)
  last_seen     ISO timestamp of the last announcement
  status        "joining", "active" or "revoked"
  note          optional short human context

The registry carries NO secrets: no API keys, no tokens, no passwords.

TRUST (V0, supersedes the F0 interim note): a record earns trust only
when ALL of these hold:

  1. it carries a real public key and a signature that verifies over
     the record core with that key (services/mail_crypto.py);
  2. its account matches the registry file's account (binding);
  3. the registry file carries an approval signature from an enrolled
     device over the member digest (the join-approval ceremony: the
     approver signs the exact set of {id, public_key, status} it
     approved, so injecting or editing any record breaks it);
  4. no verified revocation entry names it.

Anything else is display-only: legacy unsigned records still load and
render (flagged verified=False, and the view stays interim_unsigned),
but they are NOT in trusted_members and nothing may route, share keys,
or take permission decisions from them. A record whose signature is
present but does not verify is refused outright: it is reported in
problems and never reaches devices or members.

The founder bootstrap: the first device self-approves (approved_by is
its own id, its record is self-signed, and it signs the digest that
includes itself). Later devices are approved by any already-trusted
device; in the owner's family the phone is the approver (approval-only: it
holds no provider keys).

Reading stays forgiving on purpose: a missing file is an honest empty
state, a corrupt file is an honest invalid state, and a malformed
record is skipped and reported in "problems" while the good records
around it still load. The reader never raises.
"""
import json
import os
import sys
from pathlib import Path

from app.paths import DATA_DIR

STATUSES = ("joining", "active", "revoked")
KEY_PLACEHOLDER = "PLACEHOLDER-F0-no-key-enrolled"
FAMILY_SERVICE = "Xod Family Link"

# The fields a device's signature covers. Anything rendered or routed
# on must be in here, so editing it after signing breaks verification.
CORE_FIELDS = ("id", "name", "platform", "endpoint", "capabilities",
               "public_key", "box_key", "account", "status", "last_seen")


def registry_path() -> Path:
    """Where the registry lives: env override, then the saved setting,
    then the bus space on this platform."""
    env = os.environ.get("XOD_FAMILY_REGISTRY_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    try:
        from app.services.storage import get_setting
        saved = (get_setting("family_registry_path", "") or "").strip()
    except Exception:
        saved = ""
    if saved:
        return Path(saved).expanduser()
    return _default_path()


def _default_path() -> Path:
    if sys.platform == "darwin":
        cloud = Path.home() / "Library" / "CloudStorage"
        for drive in sorted(cloud.glob("GoogleDrive-*")):
            candidate = drive / "My Drive" / "xod-bus" / "family" / "registry.json"
            if candidate.parent.parent.exists():
                return candidate
        # No Drive folder mounted: fall back to the data dir, and the
        # missing state will name that path honestly.
        return DATA_DIR / "family" / "registry.json"
    if sys.platform == "win32":
        return Path("G:/My Drive/xod-bus/family/registry.json")
    return DATA_DIR / "family" / "registry.json"


# ---------------------------------------------------------------- identity

def device_identity():
    """This device's family identity (Ed25519 sign + X25519 box).

    Generated on first use; the private keys live only in the OS
    keystore through services/keychain.py (the "Xod Family Link"
    service inside Xod's single vault item), never on disk, never in
    the synced space. Mirrors how services/mail_crypto.py identities
    are held, but in the keystore rather than a data-dir file because
    this identity guards the family vault.

    The stored identity is load-bearing from the moment it is
    advertised: the family vault is sealed to its box key, so the
    identity a device resolves must be the one that was enrolled,
    byte for byte. Two rules follow (both were Run 6 failure modes):

    - Absence is proven against the store, not against this process's
      cached vault snapshot. keychain.py caches the vault per process;
      a long-lived process that read its (then empty) vault before
      another process enrolled this device would otherwise see "no
      identity", generate a second one, and overwrite the enrolled
      identity with it. When the cached view shows no identity, the
      store is read fresh before any generation is considered.
    - A stored identity that cannot be used (unreadable store,
      corrupt values, only one of the two keys present) fails loudly.
      Silently regenerating would orphan everything already sealed
      to the enrolled identity, so it is never done.
    """
    from app.services import keychain, mail_crypto as mc
    sign = keychain.get_password(FAMILY_SERVICE, "device_sign_key")
    box = keychain.get_password(FAMILY_SERVICE, "device_box_key")
    if not (sign and box):
        # The cached vault may predate this device's enrollment by
        # another process; read the store fresh before concluding
        # that no identity exists.
        keychain.reset_cache()
        sign = keychain.get_password(FAMILY_SERVICE, "device_sign_key")
        box = keychain.get_password(FAMILY_SERVICE, "device_box_key")
    if sign and box:
        try:
            return mc.Identity.from_secrets(sign, box)
        except Exception as e:
            raise keychain.Unreadable(
                "this device's stored family identity could not be read "
                "back; refusing to replace it with a new one") from e
    if sign or box:
        raise keychain.Unreadable(
            "this device's stored family identity is incomplete (only one "
            "of its two keys is present); refusing to replace it with a "
            "new one")
    problem = keychain.problem()
    if problem:
        raise keychain.Unreadable(problem)
    ident = mc.Identity.new()
    sign, box = ident.secrets()
    keychain.set_password(FAMILY_SERVICE, "device_sign_key", sign)
    keychain.set_password(FAMILY_SERVICE, "device_box_key", box)
    # Converge on the durable record: if another process stored an
    # identity while this one was generating, the stored one wins, so
    # every process ends up holding the identity that is actually
    # persisted (and, once advertised, enrolled).
    keychain.reset_cache()
    stored_sign = keychain.get_password(FAMILY_SERVICE, "device_sign_key")
    stored_box = keychain.get_password(FAMILY_SERVICE, "device_box_key")
    if stored_sign and stored_box and (stored_sign, stored_box) != (sign, box):
        try:
            return mc.Identity.from_secrets(stored_sign, stored_box)
        except Exception as e:
            raise keychain.Unreadable(
                "this device's stored family identity could not be read "
                "back; refusing to replace it with a new one") from e
    return ident


def record_core(record: dict, family, account) -> dict:
    """The exact object a device signature covers."""
    core = {f: record.get(f) for f in CORE_FIELDS}
    core["family"] = family
    if not core.get("account"):
        core["account"] = account
    return core


def sign_record(record: dict, identity, family, account) -> dict:
    """A copy of the record signed by its own device key."""
    from app.services import mail_crypto as mc
    out = dict(record)
    out["public_key"] = identity.sign_pub
    out["box_key"] = identity.box_pub
    if account and not out.get("account"):
        out["account"] = account
    out["signature"] = identity.sign(mc.canonical(record_core(out, family, account)))
    return out


def verify_record(record: dict, family, account) -> bool:
    """True only for a real key + a signature that verifies + binding."""
    from app.services import mail_crypto as mc
    pub = record.get("public_key") or ""
    sig = record.get("signature")
    if not pub or pub == KEY_PLACEHOLDER or not sig:
        return False
    if account and record.get("account") and record["account"] != account:
        return False
    return mc.verify(pub, mc.canonical(record_core(record, family, account)), sig)


def member_digest(data: dict) -> dict:
    """What the approver signs: the exact approved member set."""
    rows = []
    for raw in data.get("devices") or []:
        if isinstance(raw, dict) and raw.get("id"):
            rows.append({"id": raw["id"], "public_key": raw.get("public_key"),
                         "status": raw.get("status")})
    rows.sort(key=lambda r: r["id"])
    return {"family": data.get("family"), "account": data.get("account"),
            "devices": rows}


def sign_approval(data: dict, identity, approver_id: str) -> dict:
    """A copy of the registry payload carrying the approver's signature
    over the member digest (the one-tap join approval, in data form)."""
    from app.services import mail_crypto as mc
    out = dict(data)
    out["approved_by"] = approver_id
    out["approval_signature"] = identity.sign(mc.canonical(member_digest(out)))
    return out


def _verify_approval(data: dict, verified_by_id: dict) -> bool:
    """The approval signature verifies against the approver's own
    verified record in the same file."""
    from app.services import mail_crypto as mc
    approver = data.get("approved_by")
    sig = data.get("approval_signature")
    if not approver or not sig:
        return False
    record = verified_by_id.get(approver)
    if record is None:
        return False
    return mc.verify(record["public_key"], mc.canonical(member_digest(data)), sig)


def _verified_revocations(data: dict, verified_by_id: dict) -> dict:
    """{device_id: revoker_id} for revocation entries whose signature
    verifies against a verified device's key in the same file. A
    revocation signed by the device itself also counts (self-retire)."""
    from app.services import mail_crypto as mc
    out = {}
    for entry in data.get("revocations") or []:
        if not isinstance(entry, dict):
            continue
        target, by, sig = entry.get("device_id"), entry.get("by"), entry.get("signature")
        if not target or not by or not sig:
            continue
        signer = verified_by_id.get(by)
        if signer is None:
            continue
        core = {"action": "revoke", "family": data.get("family"),
                "account": data.get("account"), "device_id": target, "by": by}
        if mc.verify(signer["public_key"], mc.canonical(core), sig):
            out[target] = by
    return out


def sign_revocation(data: dict, identity, revoker_id: str, target_id: str) -> dict:
    """A revocation entry signed by the revoker (or the device itself)."""
    from app.services import mail_crypto as mc
    core = {"action": "revoke", "family": data.get("family"),
            "account": data.get("account"), "device_id": target_id, "by": revoker_id}
    return {"device_id": target_id, "by": revoker_id,
            "signature": identity.sign(mc.canonical(core))}


def my_device_id() -> str:
    """This device's own registry id: env override, then the saved
    setting (family_device_id, set at enrollment)."""
    env = os.environ.get("XOD_FAMILY_DEVICE_ID", "").strip()
    if env:
        return env
    try:
        from app.services.storage import get_setting
        return (get_setting("family_device_id", "") or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------- reader

def _clean_record(raw, problems):
    """One registry record, validated; None (with a problem noted) when
    the record cannot be trusted to render."""
    if not isinstance(raw, dict):
        problems.append("skipped a record that is not an object")
        return None
    rid = raw.get("id")
    name = raw.get("name")
    platform = raw.get("platform")
    status = raw.get("status")
    if not (isinstance(rid, str) and rid.strip()):
        problems.append("skipped a record with no id")
        return None
    if not (isinstance(name, str) and name.strip()):
        problems.append(f"skipped record {rid}: no name")
        return None
    if not (isinstance(platform, str) and platform.strip()):
        problems.append(f"skipped record {rid}: no platform")
        return None
    if status not in STATUSES:
        problems.append(f"skipped record {rid}: unknown status {status!r}")
        return None
    capabilities = raw.get("capabilities") or []
    if not isinstance(capabilities, list) or not all(isinstance(c, str) for c in capabilities):
        problems.append(f"skipped record {rid}: capabilities is not a list of strings")
        return None
    endpoint = raw.get("endpoint")
    if endpoint is not None and not isinstance(endpoint, str):
        problems.append(f"skipped record {rid}: endpoint is not a string")
        return None
    public_key = raw.get("public_key")
    if not (isinstance(public_key, str) and public_key.strip()):
        public_key = KEY_PLACEHOLDER
    box_key = raw.get("box_key")
    if box_key is not None and not isinstance(box_key, str):
        box_key = None
    account = raw.get("account")
    if account is not None and not isinstance(account, str):
        account = None
    last_seen = raw.get("last_seen")
    if last_seen is not None and not isinstance(last_seen, str):
        last_seen = None
    note = raw.get("note")
    if note is not None and not isinstance(note, str):
        note = None
    return {
        "id": rid.strip(),
        "name": name.strip(),
        "platform": platform.strip(),
        "endpoint": endpoint,
        "capabilities": capabilities,
        "public_key": public_key,
        "box_key": box_key,
        "account": account,
        "signature": raw.get("signature"),
        "last_seen": last_seen,
        "status": status,
        "note": note,
        "verified": False,
    }


def load_registry(path=None) -> dict:
    """The family view the screen renders: state, members (active
    devices), trusted_members (active AND signature-verified AND
    covered by the approval signature AND not revoked), every valid
    device, and any problems found on the way. Never raises."""
    path = Path(path).expanduser() if path else registry_path()
    view = {
        "state": "ok",
        "path": str(path),
        # True while any member is not signature-verified: the view is
        # then a directory, not proof of identity (the F0 honesty rule,
        # kept). Signed records move their own entry to verified=True;
        # the flag clears only when every member verifies and the
        # approval signature covers the set.
        "interim_unsigned": True,
        "family": None,
        "account": None,
        "version": None,
        "updated": None,
        "approved_by": None,
        "approval_verified": False,
        "devices": [],
        "members": [],
        "trusted_members": [],
        "revoked_by_registry": [],
        "problems": [],
        "message": "",
    }
    if not path.exists():
        view["state"] = "missing"
        view["message"] = (
            "No family registry found yet. It will live at "
            f"{path} once the first device writes it."
        )
        return view
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        view["state"] = "invalid"
        view["problems"].append(f"registry is not readable JSON: {e}")
        view["message"] = "The family registry exists but could not be read."
        return view
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        view["state"] = "invalid"
        view["problems"].append("registry has no devices list")
        view["message"] = "The family registry exists but is not in the family format."
        return view
    view["family"] = data.get("family")
    view["account"] = data.get("account")
    view["version"] = data.get("version")
    view["updated"] = data.get("updated")
    view["approved_by"] = data.get("approved_by")
    family, account = data.get("family"), data.get("account")
    seen = set()
    verified_by_id = {}
    for raw in data["devices"]:
        record = _clean_record(raw, view["problems"])
        if record is None:
            continue
        if record["id"] in seen:
            view["problems"].append(f"skipped duplicate record id {record['id']}")
            continue
        seen.add(record["id"])
        signed = bool(record["signature"]) and record["public_key"] != KEY_PLACEHOLDER
        if signed:
            if verify_record(record, family, account):
                record["verified"] = True
                verified_by_id[record["id"]] = record
            else:
                # A signature that does not verify is a forgery signal,
                # not a legacy record: refuse the record entirely.
                view["problems"].append(
                    f"refused record {record['id']}: signature does not verify")
                continue
        view["devices"].append(record)
    # Verified revocations override a record's own claimed status.
    revoked = _verified_revocations(data, verified_by_id)
    for record in view["devices"]:
        if record["id"] in revoked:
            record["status"] = "revoked"
            record["revoked_by"] = revoked[record["id"]]
            view["revoked_by_registry"].append(record["id"])
    view["approval_verified"] = _verify_approval(data, verified_by_id)
    if data.get("approval_signature") and not view["approval_verified"]:
        view["problems"].append("registry approval signature does not verify; no member is trusted")
    view["members"] = [d for d in view["devices"] if d["status"] == "active"]
    if view["approval_verified"]:
        view["trusted_members"] = [d for d in view["members"] if d["verified"]]
    view["interim_unsigned"] = not (
        view["members"] and view["approval_verified"]
        and len(view["trusted_members"]) == len(view["members"]))
    return view


def trusted_member(view: dict, device_id: str):
    """The trusted record for a device id, or None. This is the only
    lookup vault and inference code may authorize from."""
    for d in view.get("trusted_members") or []:
        if d["id"] == device_id:
            return d
    return None
