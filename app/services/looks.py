"""
Living Look packs (see docs note XOD-LIVING-LOOK): a pack is data, never code.

A pack lives in the data folder's looks/<pack-id>/ directory:

  pack.json          the manifest: character, style, a theme (flavor + palette),
                     one entry per state, and the quirks the pack declares
  <state>.webp       the keyframe image for a state
  <state>.mp4        the animation loop for a state

The six pack states are fixed, so the UI never has a gap: idle, working, looking,
playing, upgrading, celebrating. The companion's live moods (mascot.js) map onto
them in MOOD_TO_STATE; playing and upgrading have no mood of their own, the
renderer reaches them from joy (a dance, a treat) and from an update in flight.

Verification is claimed versus verified, the same discipline as the task ladder:
the manifest must parse, every state must name a keyframe and a clip that exist
inside the pack folder (paths may not escape it), a recorded keyframe hash must
match the file on disk, the theme must parse, and every declared quirk must be a
behavior the app actually has (KNOWN_QUIRKS) bound to the state it belongs to.
A pack that fails verification is listed as broken and cannot be switched to.
"""
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

LOOKS_DIR = DATA_DIR / "looks"

# The built-in looks, drawn in code by static/js/mascot.js. The original six;
# "moon" is the permanent default and is always selectable. A custom pack is an
# alternative beside these, never a replacement for them.
BUILTIN_LOOKS = ("moon", "cat", "ghost", "robot", "owl", "sprout")

PACK_STATES = ("idle", "working", "looking", "playing", "upgrading", "celebrating")

# The companion's moods (static/js/mascot.js) and the pack state each one shows.
# Mirrored in mascot.js as PACK_MOOD_TO_STATE; the looks test pins this table.
MOOD_TO_STATE = {
    "idle": "idle",
    "sleepy": "idle",
    "thinking": "working",
    "working": "working",
    "searching": "looking",
    "waiting": "looking",
    "listening": "looking",
    "error": "looking",
    "happy": "celebrating",
}

# Quirks the app actually implements, and the state each belongs to. In pack
# rendering the celebrating clip IS the finish flourish: it plays whenever a
# task completes (the happy flash), so wand-tap-finish is real behavior, not a
# promise. A pack may only declare quirks from this table, bound as listed.
KNOWN_QUIRKS = {
    "wand-tap-finish": "celebrating",
}

_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,30}")


def looks_dir() -> Path:
    LOOKS_DIR.mkdir(parents=True, exist_ok=True)
    return LOOKS_DIR


def pack_dir(pack_id: str) -> Optional[Path]:
    """The pack's folder, or None for an id that cannot name one."""
    if not pack_id or not _ID_RE.fullmatch(pack_id):
        return None
    d = looks_dir() / pack_id
    return d if d.is_dir() else None


def is_pack(pack_id: str) -> bool:
    return pack_dir(pack_id) is not None


def _asset_path(d: Path, name: Any) -> Optional[Path]:
    """Resolve a manifest asset name inside the pack folder; None if it escapes."""
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        return None
    p = (d / name).resolve()
    return p if p.parent == d.resolve() else None


def verify_pack(pack_id: str) -> Dict[str, Any]:
    """Full claimed-versus-verified report for one installed pack."""
    rep: Dict[str, Any] = {"id": pack_id, "ok": False, "states": {}, "quirks": [],
                           "errors": [], "manifest": None}
    d = pack_dir(pack_id)
    if d is None:
        rep["errors"].append("no such pack")
        return rep
    raw = d / "pack.json"
    if not raw.is_file():
        rep["errors"].append("pack.json missing")
        return rep
    try:
        man = json.loads(raw.read_text(encoding="utf8"))
        if not isinstance(man, dict):
            raise ValueError("manifest is not an object")
    except Exception as e:
        rep["errors"].append(f"pack.json does not parse: {e}")
        return rep
    rep["manifest"] = man
    if man.get("pack") not in (None, pack_id):
        rep["errors"].append(f"manifest names pack {man.get('pack')!r}, folder is {pack_id!r}")

    states = man.get("states")
    if not isinstance(states, dict):
        rep["errors"].append("manifest has no states object")
        states = {}
    for st in PACK_STATES:
        entry = states.get(st)
        srep: Dict[str, Any] = {"file": None, "animation_file": None, "sha256_ok": None}
        rep["states"][st] = srep
        if not isinstance(entry, dict):
            rep["errors"].append(f"state {st}: missing from the manifest")
            continue
        for key in ("file", "animation_file"):
            name = entry.get(key)
            srep[key] = name
            p = _asset_path(d, name)
            if p is None:
                rep["errors"].append(f"state {st}: {key} {name!r} is not a file inside the pack")
            elif not p.is_file():
                rep["errors"].append(f"state {st}: {key} {name!r} does not exist on disk")
        key_path = _asset_path(d, entry.get("file"))
        want = entry.get("sha256")
        if key_path is not None and key_path.is_file() and want:
            got = hashlib.sha256(key_path.read_bytes()).hexdigest()
            srep["sha256_ok"] = got == want
            if not srep["sha256_ok"]:
                rep["errors"].append(f"state {st}: keyframe hash does not match the manifest")

    theme = man.get("theme")
    if not isinstance(theme, dict) or not isinstance(theme.get("palette"), dict):
        rep["errors"].append("theme does not parse: needs a theme object with a palette")

    quirks = man.get("quirks") or []
    if not isinstance(quirks, list):
        rep["errors"].append("quirks is not a list")
        quirks = []
    for q in quirks:
        qid = q.get("id") if isinstance(q, dict) else None
        bound = q.get("bound_state") if isinstance(q, dict) else None
        rep["quirks"].append({"id": qid, "bound_state": bound})
        if qid not in KNOWN_QUIRKS:
            rep["errors"].append(f"quirk {qid!r} is not a behavior the app has")
        elif bound != KNOWN_QUIRKS[qid]:
            rep["errors"].append(
                f"quirk {qid!r} belongs to state {KNOWN_QUIRKS[qid]!r}, pack binds it to {bound!r}")

    rep["ok"] = not rep["errors"]
    return rep


def _summary(rep: Dict[str, Any]) -> Dict[str, Any]:
    man = rep.get("manifest") or {}
    theme = man.get("theme") if isinstance(man.get("theme"), dict) else {}
    return {
        "id": rep["id"],
        "ok": rep["ok"],
        "errors": rep["errors"],
        "version": man.get("version"),
        "status": man.get("status"),
        "name": rep["id"].replace("-", " ").title(),
        "blurb": theme.get("flavor") or man.get("style") or "",
        "states": {st: {"file": s["file"], "animation_file": s["animation_file"]}
                   for st, s in rep["states"].items()},
        "quirks": rep["quirks"],
    }


def list_packs() -> List[Dict[str, Any]]:
    """Every installed pack with its verification summary, broken ones included."""
    out = []
    for d in sorted(looks_dir().iterdir()):
        if d.is_dir() and _ID_RE.fullmatch(d.name):
            out.append(_summary(verify_pack(d.name)))
    return out
