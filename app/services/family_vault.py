"""
Family Link: the family vault — API keys shared inside one owner's
family (V1, with revocation/rotation as V2).

Design: FAMILY-KEYS-AND-INFERENCE-SHARING-DESIGN-2026-11-01.md and
FAMILY-VAULT-BUILD-PLAN-2026-11-01.md. The sharing boundary is the
SIGNED family registry (services/family_registry.py): only devices in
trusted_members ever become recipients, and this module refuses to
publish or apply anything when the registry view carries no trusted
members. An unsigned or forged registry gets nothing.

Shape:
  - One encrypted bundle file in the family synced space
    (family/vault.bundle.json inside the space; see bundle_path()
    for the ladder: the rdrive files root first, the registry's own
    space — today a Google Drive folder — as the alternate root).
    Ciphertext only, ever:
      {family, version, created_by, updated, recipients: [ids],
       wrapped: {device_id: sealed envelope of the bundle key},
       nonce, ciphertext}
    The bundle key is a fresh 32 random bytes per publish. It is
    wrapped per recipient with the sealed-box construction Xod
    already ships (services/mail_crypto.py: ephemeral X25519 +
    HKDF-SHA256 + ChaCha20-Poly1305, addressed to "xod-family:<id>"),
    and never exists in the synced space in plaintext. The payload is
    the provider secrets as {field: value}, encrypted under the
    bundle key with ChaCha20-Poly1305, AAD binding family + version.
  - Publish (source device): reads this device's own secrets from the
    OS keystore (services/secret_store.py), encrypts for the current
    trusted recipient set, writes the bundle, reads it back through
    the file and proves it can unwrap its own copy before reporting
    success (bus claim discipline).
  - Apply (receiving device): if this device is a trusted recipient
    and the bundle version is newer than the one already applied,
    unwrap the bundle key with this device's own X25519 private key
    (OS keystore only), decrypt, and write each secret into this
    device's own keystore via secret_store.save — plaintext lives
    only there afterwards, exactly like a key typed by hand. This is
    what makes the vault work while the source device sleeps.
  - Per-device overrides survive: the setting
    family_vault_local_only="on" makes apply an explicit no-op, and
    fields listed in family_vault_pinned_fields (a JSON list) are
    never overwritten by a sync.
  - Revocation IS rotation (V2): republishing after a device leaves
    the trusted set mints a fresh bundle key and wraps it only for
    the remaining recipients; the removed device's wrapped entry is
    gone and it cannot unwrap the new version. (What it already
    unwrapped into its own keystore cannot be remotely wiped — the
    design note says so plainly; provider-side rotation answers a
    suspected compromise.) rewrap_after_revocation() is the trigger
    the revocation path calls so the re-wrap actually happens, and
    status() flags any bundle that still wraps for a revoked device
    until some trusted device with keys republishes.
  - Enroll -> keys (R12): sync_keys() is the completion step a device
    runs when its enrollment lands (and at startup): if this device
    is a trusted recipient it fetches the bundle from the synced
    space and applies it, ending in a presence-only has_key
    read-back for the family default provider — no manual key
    juggling, per the interop principle (2026-11-02).
  - Session walls (the owner's family session rule, 2026-11-09): a
    process that cannot reach its OS keystore from its own session
    (SSH/headless on macOS: Keychain -25308; a Windows service/SSH
    session: Credential Manager error 1312) never touches the
    keystore directly — the device's OWN running Xod service, which
    lives in the session that can, performs the keystore work.
    Same-device processes use the loopback vault routes / sync_keys;
    an enrolled requester holding its own identity uses the signed
    family key channel (services/family_key_channel.py), where this
    device's service unwraps and delivers, sealed to the requester,
    after the registry + SSO + recipient checks. The trust boundary
    is the family gate, not the GUI session.

Nothing here prints, logs, or returns a key value: reports carry
provider field names and presence booleans only.

The phone is approval-only in V1 (plan recommendation; the owner has not
ruled otherwise): it may sign approvals/revocations but is never a
vault recipient unless its record is a full Xod member record a
future build enrolls as one.
"""
import json
import os
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from app.services import family_registry, mail_crypto as mc

BUNDLE_NAME = "vault.bundle.json"
VERSION_SETTING = "family_vault_applied_version"
LOCAL_ONLY_SETTING = "family_vault_local_only"
PINNED_SETTING = "family_vault_pinned_fields"
PATH_SETTING = "family_vault_path"
# The family default brain's key field (OpenAI, gpt-6.1-terra): the
# field sync_keys() verifies presence for in its has_key read-back.
DEFAULT_PROVIDER_FIELD = "openai_api_key"
_ADDRESS = "xod-family:{}"
_AAD = "xod-family-vault|{}|{}"


class VaultError(Exception):
    """A vault refusal with a plain reason. Never carries key material."""


KEYSTORE_UNREACHABLE_MESSAGE = (
    "the OS keystore could not be reached from this process, so the bundle was not applied. "
    "The backend must run in a session that can reach the keystore (on Windows, the signed-in "
    "user's interactive session: Credential Manager refuses writes from service and SSH "
    "sessions). Nothing was marked applied; run the apply again from such a session.")
"""Why an apply stops when the keystore refuses: on Windows the
Electron-spawned backend's token cannot write Credential Manager
(error 1312's family), and the keyring error used to escape apply()
untyped, surfacing as a bare HTTP 500 (0.9.23 production ceremony).
The service now returns this as the typed keystore_unreachable
state instead: honest about the cause, and actionable."""


def _rdrive_files_root() -> Path:
    return Path.home() / "Documents" / "rdrive" / "data" / "files"


def _bundle_candidates() -> list:
    """Ordered candidate homes for the bundle in the family synced
    space — the family_rules ladder, adapted to the bundle: an
    explicitly configured location (env, then setting) is the sole
    candidate; otherwise the rdrive files root (each account folder's
    family/ subfolder) comes first, and the space the registry lives
    in (today a Google Drive folder; a plain synced folder works the
    same way) is the alternate root."""
    env = os.environ.get("XOD_FAMILY_VAULT_PATH", "").strip()
    if env:
        return [Path(env).expanduser()]
    saved = _setting(PATH_SETTING, "").strip()
    if saved:
        return [Path(saved).expanduser()]
    out = []
    files_root = _rdrive_files_root()
    if files_root.is_dir():
        for account in sorted(files_root.iterdir()):
            if account.is_dir():
                out.append(account / "family" / BUNDLE_NAME)
    out.append(family_registry.registry_path().parent / BUNDLE_NAME)
    return out


def bundle_path() -> Path:
    """Where the bundle is read from / written to: the first
    candidate that already holds a bundle (an existing bundle is
    never silently migrated to another space), else the first
    candidate — so a fresh publish lands in the rdrive space when
    this machine has one."""
    candidates = _bundle_candidates()
    for cand in candidates:
        if cand.is_file():
            return cand
    return candidates[0]


def bundle_home() -> dict:
    """Which rung of the ladder the bundle resolved through, for the
    Family screen and audits: env, setting, rdrive, or
    registry-space. Presence-level: a space name and a path."""
    path = bundle_path()
    env = os.environ.get("XOD_FAMILY_VAULT_PATH", "").strip()
    if env:
        space = "env"
    elif _setting(PATH_SETTING, "").strip():
        space = "setting"
    else:
        try:
            path.relative_to(_rdrive_files_root())
            space = "rdrive"
        except ValueError:
            space = "registry-space"
    return {"space": space, "path": str(path)}


def _setting(name, default=""):
    try:
        from app.services.storage import get_setting
        return get_setting(name, default) or default
    except Exception:
        return default


def _set_setting(name, value):
    from app.services.storage import set_setting
    set_setting(name, value)


def local_only() -> bool:
    return _setting(LOCAL_ONLY_SETTING, "off") == "on"


def pinned_fields() -> set:
    try:
        rows = json.loads(_setting(PINNED_SETTING, "[]"))
        return {str(x) for x in rows} if isinstance(rows, list) else set()
    except ValueError:
        return set()


def _seal_bundle_key(bundle_key: bytes, recipient: dict, sender) -> dict:
    payload = {"bundle_key": mc.b64e(bundle_key)}
    return mc.seal(sender, _ADDRESS.format("family-vault"),
                   _ADDRESS.format(recipient["id"]), recipient["box_key"], payload)


def _open_bundle_key(envelope: dict, identity, my_id: str,
                     expect_signer: str = None) -> bytes:
    opened = mc.open_envelope(identity, _ADDRESS.format(my_id), envelope)
    if not opened.get("signature_ok"):
        raise VaultError("the wrapped bundle key is not signed by its sender")
    # Key-substitution guard: the envelope must be signed by the device
    # the bundle names as its creator, and that device must be trusted.
    signer = (opened.get("keys") or {}).get("sign")
    if expect_signer and signer != expect_signer:
        raise VaultError("the wrapped bundle key was signed by an unexpected device")
    msg = opened.get("msg") or {}
    key_b64 = msg.get("bundle_key")
    if not key_b64:
        raise VaultError("the wrapped bundle for this device carries no key")
    return mc.b64d(key_b64)


def _encrypt_payload(bundle_key: bytes, family, version: int, secrets: dict) -> dict:
    nonce = os.urandom(12)
    aad = _AAD.format(family, version).encode()
    ct = ChaCha20Poly1305(bundle_key).encrypt(nonce, mc.canonical(secrets), aad)
    return {"nonce": mc.b64e(nonce), "ciphertext": mc.b64e(ct)}


def _decrypt_payload(bundle_key: bytes, family, version: int, bundle: dict) -> dict:
    aad = _AAD.format(family, version).encode()
    raw = ChaCha20Poly1305(bundle_key).decrypt(
        mc.b64d(bundle["nonce"]), mc.b64d(bundle["ciphertext"]), aad)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise VaultError("vault payload is not a key map")
    return {str(k): str(v) for k, v in data.items()}


def read_bundle(path=None) -> dict:
    """The bundle file as stored (ciphertext only), or an honest
    missing/invalid state. Never raises."""
    path = Path(path).expanduser() if path else bundle_path()
    if not path.exists():
        return {"state": "missing", "path": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"state": "invalid", "path": str(path), "problem": f"not readable JSON: {e}"}
    if not isinstance(data, dict) or "ciphertext" not in data or "wrapped" not in data:
        return {"state": "invalid", "path": str(path), "problem": "not a vault bundle"}
    return {"state": "ok", "path": str(path), **data}


def status(view=None, my_id=None) -> dict:
    """Presence-level vault state for the Family screen: bundle
    present, version, recipients, whether this device has applied it.
    No key values, no lengths."""
    view = view or family_registry.load_registry()
    bundle = read_bundle()
    revoked = set(view.get("revoked_by_registry") or [])
    revoked |= {d["id"] for d in view.get("devices") or [] if d.get("status") == "revoked"}
    out = {"state": bundle["state"], "version": bundle.get("version"),
           "recipients": bundle.get("recipients") or [],
           "created_by": bundle.get("created_by"),
           "updated": bundle.get("updated"),
           "applied_version": int(_setting(VERSION_SETTING, "0") or 0),
           "local_only": local_only(),
           "bundle_home": bundle_home(),
           # Non-empty means a revocation has landed in the registry
           # but no trusted device with keys has republished yet: the
           # stored bundle still wraps for the removed device. The
           # honest flag until rewrap_after_revocation clears it.
           "wraps_revoked": sorted(set(bundle.get("recipients") or []) & revoked),
           "i_am_recipient": bool(my_id and my_id in (bundle.get("recipients") or [])),
           "trusted_recipients": [d["id"] for d in view.get("trusted_members") or []]}
    return out


def publish(secrets: dict, my_id: str, view=None, identity=None, path=None) -> dict:
    """Encrypt `secrets` ({field: value}) for every trusted member and
    write the bundle beside the registry. The publisher must itself be
    a trusted member. Returns field NAMES only."""
    view = view or family_registry.load_registry()
    trusted = view.get("trusted_members") or []
    ids = [d["id"] for d in trusted]
    if my_id not in ids:
        raise VaultError("this device is not a trusted family member; refusing to publish")
    recipients = [d for d in trusted if d.get("box_key")]
    if len(recipients) != len(trusted):
        missing = [d["id"] for d in trusted if not d.get("box_key")]
        raise VaultError(f"trusted members without a box key cannot receive the vault: {missing}")
    identity = identity or family_registry.device_identity()
    path = Path(path).expanduser() if path else bundle_path()
    prev = read_bundle(path)
    version = int(prev.get("version") or 0) + 1 if prev.get("state") == "ok" else 1
    bundle_key = os.urandom(32)  # fresh every publish: revocation = rotation
    enc = _encrypt_payload(bundle_key, view.get("family"), version, secrets)
    bundle = {
        "family": view.get("family"),
        "version": version,
        "created_by": my_id,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "recipients": [d["id"] for d in recipients],
        "wrapped": {d["id"]: _seal_bundle_key(bundle_key, d, identity) for d in recipients},
        **enc,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(bundle, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    # Read back and prove the publisher's own copy unwraps before
    # calling this a success (bus claim discipline).
    back = read_bundle(path)
    if back.get("state") != "ok" or back.get("version") != version:
        raise VaultError("bundle read-back does not match what was written")
    mine = _open_bundle_key(back["wrapped"][my_id], identity, my_id)
    roundtrip = _decrypt_payload(mine, back.get("family"), version, back)
    if set(roundtrip) != set(secrets):
        raise VaultError("bundle read-back decrypt does not match the published fields")
    return {"state": "published", "version": version,
            "recipients": bundle["recipients"], "fields": sorted(secrets)}


def _refresh_settings_snapshot() -> bool:
    """Re-read the keystore into the running process's config settings
    snapshot (app.config.settings) after a vault apply landed keys.

    The snapshot is taken once at process start, and step execution
    (services/harness.py _tool_config) and /api/config read provider
    keys from it, so keys delivered by a vault apply stayed invisible
    to both until a restart (Run 6 attempt 3: sync reported the key
    present while step 1 read "no OpenAI key saved"). reload_secrets
    is the same refresh the /api/secrets/retry endpoint runs. Best
    effort: the keys ARE in the keystore either way; False means
    snapshot readers keep the pre-apply view until the next refresh,
    and the applied result says so."""
    try:
        from app import config
        config.reload_secrets()
        return True
    except Exception:
        return False


def apply(my_id: str, view=None, identity=None, path=None) -> dict:
    """Unwrap the current bundle into this device's own keystore, if
    this device is a trusted recipient and the version is new.
    Returns an explicit state, never a silent no-op."""
    if local_only():
        return {"state": "local_only",
                "message": "this device is pinned local-only; the vault was not applied"}
    view = view or family_registry.load_registry()
    if family_registry.trusted_member(view, my_id) is None:
        return {"state": "not_trusted",
                "message": "this device is not a trusted family member; the vault was not applied"}
    bundle = read_bundle(path)
    if bundle.get("state") != "ok":
        return {"state": bundle.get("state", "missing"),
                "message": "no readable vault bundle in the synced space"}
    if my_id not in (bundle.get("recipients") or []):
        return {"state": "not_recipient",
                "message": "this device is not a recipient of the current bundle"}
    version = int(bundle.get("version") or 0)
    applied = int(_setting(VERSION_SETTING, "0") or 0)
    if version <= applied:
        return {"state": "current", "version": version,
                "message": "the applied bundle is already this version"}
    try:
        identity = identity or family_registry.device_identity()
    except Exception as e:
        return {"state": "keystore_unreachable",
                "message": f"{KEYSTORE_UNREACHABLE_MESSAGE} ({type(e).__name__})"}
    creator = family_registry.trusted_member(view, bundle.get("created_by") or "")
    if creator is None:
        return {"state": "not_trusted",
                "message": "the bundle's creator is not a trusted family member; it was not applied"}
    try:
        bundle_key = _open_bundle_key(bundle["wrapped"][my_id], identity, my_id,
                                      expect_signer=creator["public_key"])
        secrets = _decrypt_payload(bundle_key, bundle.get("family"), version, bundle)
    except VaultError:
        raise
    except Exception as e:
        raise VaultError(f"this device could not unwrap the bundle ({type(e).__name__})") from e
    from app.services import secret_store
    pins = pinned_fields()
    applied_fields, skipped = [], []
    try:
        for field, value in secrets.items():
            if field in pins:
                skipped.append(field)
                continue
            secret_store.save(field, value)
            applied_fields.append(field)
    except Exception as e:
        # A keystore that refuses mid-apply is a typed state, never a
        # bare failure: fields already written stay written, the rest
        # did not land, and the version is NOT marked applied, so a
        # retry from a session that can reach the keystore completes
        # the same bundle cleanly.
        return {"state": "keystore_unreachable",
                "message": f"{KEYSTORE_UNREACHABLE_MESSAGE} ({type(e).__name__})",
                "fields_written": sorted(applied_fields)}
    _set_setting(VERSION_SETTING, str(version))
    refreshed = _refresh_settings_snapshot()
    return {"state": "applied", "version": version,
            "fields": sorted(applied_fields), "pinned_skipped": sorted(skipped),
            "settings_refreshed": refreshed}


def publish_from_keystore(my_id: str, view=None) -> dict:
    """The source-device write path: gather this device's own provider
    secrets from its keystore (values stay in memory, in process) and
    publish them to the family."""
    from app.services import secret_store
    secrets = {}
    for field in secret_store.SECRET_FIELDS:
        value = secret_store.stored(field)
        if value:
            secrets[field] = value
    if not secrets:
        raise VaultError("this device holds no provider keys to share")
    return publish(secrets, my_id, view=view)


# ------------------------------------------------------- enroll -> keys

def _audit(kind: str, device_id: str, payload: dict) -> None:
    """The family audit trail (field names and states only, never a
    value). Evidence, never a reason to fail the flow."""
    try:
        from app.services import event_log
        event_log.emit(kind, actor="family_vault",
                       ids={"device": device_id}, payload=payload)
    except Exception:
        pass


def _bundle_field_names(my_id: str, view, identity, path=None) -> list:
    """The field NAMES the current bundle carries for this device,
    via a read-only unwrap (nothing is written). [] when this device
    cannot open its wrapped copy."""
    bundle = read_bundle(path)
    if bundle.get("state") != "ok" or my_id not in (bundle.get("recipients") or []):
        return []
    try:
        identity = identity or family_registry.device_identity()
        creator = family_registry.trusted_member(view, bundle.get("created_by") or "")
        expect = creator["public_key"] if creator else None
        key = _open_bundle_key(bundle["wrapped"][my_id], identity, my_id,
                               expect_signer=expect)
        secrets = _decrypt_payload(key, bundle.get("family"),
                                   int(bundle.get("version") or 0), bundle)
        return sorted(secrets)
    except Exception:
        return []


def sync_keys(my_id: str = None, view=None, identity=None, path=None,
              expect_field: str = DEFAULT_PROVIDER_FIELD) -> dict:
    """Enroll -> keys, the completion step: when this device is a
    trusted family member, fetch the bundle from the synced space
    and apply it into this device's own keystore, then verify —
    presence only — that the family default provider's key is now
    held (has_key). Runs when enrollment lands and at startup; a
    device that is not (yet) a trusted recipient gets an explicit
    state, never a silent no-op and never key material. Never
    raises: enrollment and startup callers take the state and move
    on."""
    try:
        my_id = my_id or family_registry.my_device_id()
        if not my_id:
            out = {"state": "no_device_id",
                   "message": "this device has no family device id yet "
                              "(setting family_device_id)"}
            _audit("family.vault_sync", "", out)
            return out
        try:
            out = apply(my_id, view=view, identity=identity, path=path)
        except VaultError as e:
            out = {"state": "refused", "message": str(e)}
        if out.get("state") in ("applied", "current"):
            from app.services import secret_store
            view = view or family_registry.load_registry()
            fields = out.get("fields") or _bundle_field_names(
                my_id, view, identity, path)
            present = {f: bool(secret_store.stored(f)) for f in fields}
            out["keys_present"] = present
            out["has_key"] = bool(present.get(expect_field))
        _audit("family.vault_sync", my_id,
               {"state": out.get("state"), "version": out.get("version"),
                "fields": sorted(out.get("keys_present") or {})})
        return out
    except Exception as e:
        return {"state": "error",
                "message": f"vault sync failed in this process ({type(e).__name__})"}


def rewrap_after_revocation(revoked_id: str, my_id: str = None, view=None) -> dict:
    """The revocation trigger: if the stored bundle still wraps for
    the device that just left, republish from THIS device's keystore
    — publish() mints a fresh bundle key for the current trusted set
    only, so the removed device is wrapped out. Honest states when
    this device cannot be the one to do it: not_needed (nothing
    wraps for it), not_trusted, no_keys, keystore_unreachable
    (another trusted device with keys must republish; status()
    keeps flagging wraps_revoked until one does). Never raises."""
    try:
        my_id = my_id or family_registry.my_device_id()
        if not my_id:
            return {"state": "no_device_id",
                    "message": "this device has no family device id yet"}
        view = view or family_registry.load_registry()
        bundle = read_bundle()
        if bundle.get("state") != "ok" or revoked_id not in (bundle.get("recipients") or []):
            return {"state": "not_needed",
                    "message": "the stored bundle does not wrap for the removed device"}
        if family_registry.trusted_member(view, my_id) is None:
            return {"state": "not_trusted",
                    "message": "this device is not a trusted family member; "
                               "it cannot republish the vault"}
        try:
            out = publish_from_keystore(my_id, view=view)
        except VaultError as e:
            if "no provider keys" in str(e):
                from app.services import secret_store
                if not secret_store.health().get("readable", True):
                    return {"state": "keystore_unreachable",
                            "message": KEYSTORE_UNREACHABLE_MESSAGE}
                return {"state": "no_keys",
                        "message": "this device holds no provider keys to republish with"}
            return {"state": "refused", "message": str(e)}
        except Exception as e:
            return {"state": "keystore_unreachable",
                    "message": f"{KEYSTORE_UNREACHABLE_MESSAGE} ({type(e).__name__})"}
        result = {"state": "rewrapped", "version": out.get("version"),
                  "recipients": out.get("recipients")}
        _audit("family.vault_rewrap", my_id,
               {"revoked": revoked_id, **result})
        return result
    except Exception as e:
        return {"state": "error",
                "message": f"vault rewrap failed in this process ({type(e).__name__})"}
