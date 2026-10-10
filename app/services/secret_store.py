"""
API keys and tokens live in the macOS Keychain, nowhere else.

Before this, keys sat in plain text in three places: .env, the settings table in data/xod.db,
and data/media_models_config.json. migrate() moves each one into the Keychain, reads it back
to be sure, and only then removes the plain-text copies. Keychain items show up as
"Xod API keys" in Keychain Access, one per key (account = the setting name).

Keys are held in this process's memory while Xod runs (it needs them to call the APIs), but
they are never written to disk again, never put in the environment (child processes such as
Chrome or the media worker don't inherit them), and the UI only ever sees a masked form.
"""
import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import keyring
from app.paths import DATA_DIR, ENV_PATH, DEV_ENV_PATH

logger = logging.getLogger("xod.secrets")

SERVICE = os.environ.get("XOD_SECRETS_SERVICE", "Xod API keys")
BASE_DIR = Path(__file__).resolve().parent.parent.parent
MEDIA_CONFIG = DATA_DIR / "media_models_config.json"

SECRET_FIELDS: List[str] = [
    "trustmrr_api_key", "you_api_key", "tavily_api_key", "youtube_api_key",
    "openai_api_key", "anthropic_api_key", "gemini_api_key", "groq_api_key",
    "openrouter_api_key", "fireworks_api_key", "huggingface_token",
]
# Other names the same secret has been stored under.
ALIASES: Dict[str, List[str]] = {
    "you_api_key": ["ydc_api_key"],
    "huggingface_token": ["hf_token"],
}


def is_secret(name: str) -> bool:
    n = name.lower()
    return n in SECRET_FIELDS or any(n in a for a in ALIASES.values())


def canonical(name: str) -> str:
    n = name.lower()
    for field, aliases in ALIASES.items():
        if n in aliases:
            return field
    return n


def get(name: str) -> str:
    try:
        return keyring.get_password(SERVICE, canonical(name)) or ""
    except Exception as e:
        logger.warning(f"Keychain read failed for {canonical(name)}: {type(e).__name__}")
        return ""


def save(name: str, value: str) -> None:
    """Saves a key (an empty value deletes it). Raises if the Keychain refuses."""
    field, value = canonical(name), (value or "").strip()
    if value:
        keyring.set_password(SERVICE, field, value)
        if keyring.get_password(SERVICE, field) != value:
            raise RuntimeError(f"Keychain didn't keep {field}")
    else:
        try:
            keyring.delete_password(SERVICE, field)
        except keyring.errors.PasswordDeleteError:
            pass


def mask(value: str) -> str:
    if not value:
        return ""
    return value[:4] + "…" + value[-4:] if len(value) > 12 else "••••••"


# ---------------------------------------------------------------------- migration
def _env_lines(path: Path = ENV_PATH) -> List[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def _env_value(lines: List[str], key: str) -> str:
    for line in lines:
        m = re.match(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=\s*(.*)$", line, re.I)
        if m:
            return m.group(1).strip().strip("'\"")
    return ""


def migrate() -> Dict[str, str]:
    """
    Moves every plain-text key into the Keychain, then scrubs the plain-text copies.
    A copy is only removed after the Keychain has the key (read back and compared).
    Returns {field: what happened} (no values).
    """
    from app.services import storage

    report: Dict[str, str] = {}
    env_files = {p: _env_lines(p) for p in (ENV_PATH, DEV_ENV_PATH)}
    env_lines = [l for lines in env_files.values() for l in lines]
    try:
        media_cfg = json.loads(MEDIA_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        media_cfg = None

    safe_to_scrub: List[str] = []
    for field in SECRET_FIELDS:
        names = [field] + ALIASES.get(field, [])
        found = [v for v in
                 [storage.get_setting(n, "") for n in names] +
                 [_env_value(env_lines, n.upper()) for n in names] +
                 ([media_cfg.get(field, "")] if media_cfg and field == "huggingface_token" else [])
                 if v]
        in_keychain = get(field)
        if not found and not in_keychain:
            continue
        try:
            if not in_keychain:
                save(field, found[0])  # DB first: it's what the app has been using
                report[field] = "moved to Keychain"
            else:
                report[field] = "already in Keychain" if not found else "already in Keychain; removed plain-text copies"
            safe_to_scrub.append(field)
        except Exception as e:
            report[field] = f"NOT moved ({type(e).__name__}); plain-text copy kept"
            logger.error(f"Could not move {field} to the Keychain: {e}")

    # Scrub plain-text copies of everything the Keychain now has.
    scrub_names = {n for f in safe_to_scrub for n in [f] + ALIASES.get(f, [])}
    for n in scrub_names:
        storage.delete_setting(n)
    for path, lines in env_files.items():
        kept = [l for l in lines
                if not any(re.match(rf"^\s*(?:export\s+)?{re.escape(n.upper())}\s*=", l, re.I) for n in scrub_names)]
        if lines and len(kept) != len(lines):
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
            os.chmod(path, 0o600)
    if media_cfg and "huggingface_token" in media_cfg and "huggingface_token" in safe_to_scrub:
        media_cfg.pop("huggingface_token")
        MEDIA_CONFIG.write_text(json.dumps(media_cfg, indent=2), encoding="utf-8")
    for n in scrub_names:  # don't hand keys to child processes either
        os.environ.pop(n.upper(), None)
    if report:
        logger.info("API keys: " + ", ".join(f"{k} {v}" for k, v in report.items()))
    return report


def status() -> List[Dict[str, object]]:
    """For the settings UI: which keys are saved, masked."""
    return [{"name": f, "saved": bool(v), "masked": mask(v)} for f in SECRET_FIELDS for v in [get(f)]]
