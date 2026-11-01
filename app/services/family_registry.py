"""
Family Link F0: the family registry reader.

The family registry is a small JSON file in the synced bus space (the
interim home until rdrive carries it in F1). One record per device:

  id            stable machine id ("macbook-pro")
  name          display name ("Renes-MacBook-Pro-2")
  platform      "macOS", "Windows 11", "Android", ...
  endpoint      tailnet address, or null when none is recorded
  capabilities  bus-style free text list ("cloud model: ...", "macOS hands")
  public_key    the device public key (placeholder until keys enroll)
  signature     reserved; null in F0
  last_seen     ISO timestamp of the last announcement
  status        "joining", "active" or "revoked"
  note          optional short human context

The registry carries NO secrets: no API keys, no tokens, no passwords.

INTERIM TRUST (read this before building on the reader): records are
UNSIGNED in F0. Signatures and account binding arrive in F1/F4, and
until then nothing in this module authenticates anyone. The reader
flags every view interim_unsigned=True, and F0 uses it for display
only (the read-only Family screen). Do not make trust, routing or
permission decisions from these records yet.

Reading is forgiving on purpose: a missing file is an honest empty
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
        "signature": raw.get("signature"),
        "last_seen": last_seen,
        "status": status,
        "note": note,
    }


def load_registry(path=None) -> dict:
    """The family view the screen renders: state, members (active
    devices), every valid device, and any problems found on the way.
    Never raises."""
    path = Path(path).expanduser() if path else registry_path()
    view = {
        "state": "ok",
        "path": str(path),
        # F0 records are unsigned; this flag is the reader being honest
        # about it until F1/F4 land signatures (see module docstring).
        "interim_unsigned": True,
        "family": None,
        "version": None,
        "updated": None,
        "devices": [],
        "members": [],
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
    view["version"] = data.get("version")
    view["updated"] = data.get("updated")
    seen = set()
    for raw in data["devices"]:
        record = _clean_record(raw, view["problems"])
        if record is None:
            continue
        if record["id"] in seen:
            view["problems"].append(f"skipped duplicate record id {record['id']}")
            continue
        seen.add(record["id"])
        view["devices"].append(record)
    view["members"] = [d for d in view["devices"] if d["status"] == "active"]
    return view
