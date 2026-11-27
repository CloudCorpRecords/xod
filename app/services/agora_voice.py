"""
Agora Voice Brief integration for Xod (local-first, bring-your-own-key).

Ported directly from the verified standalone Agora -> Xod voice-brief bridge:
- Token007 (AccessToken2) minting from user's own Agora App ID + App Certificate (stdlib only)
- ConvoAI REST start / history / stop for the `xodbrief` preset
- History fetch BEFORE stop (Agora's history endpoint only serves RUNNING agents)
- Exact five-section brief extraction (last assistant message with >=3 labels,
  merged with later single-section re-reads, "Ready to run it?" stripped,
  unspecified sections become "not specified", never invented)
- First-class brief intake in Xod's SQLite database (review, confirm, launch as a mission)
- Dual delivery in the exact bridge envelope:
    {"id", "ts", "from_device": "macbook-agora", "from_app": "agora-xodbrief",
     "kind": "xod_voice_brief", "text": ..., "read": false}
  to (a) local ~/agora-brief-inbox/ (atomic .tmp -> rename) and
  (b) optional configured family tree-inbox over SSH/scp (atomic .tmp -> rename).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import re
import secrets
import sqlite3
import struct
import subprocess
import time
import uuid
import zlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings
from app.paths import DATA_DIR
from app.services.storage import get_setting, set_setting

logger = logging.getLogger("xod.agora_voice")

AGORA_API_BASE = os.getenv(
    "AGORA_CONVOAI_BASE_URL", "https://api.agora.io/api/conversational-ai-agent"
).rstrip("/")
AGORA_CREDENTIAL_LENGTH = 32
DEFAULT_EXPIRY_SECONDS = 3600
MAX_EXPIRY_SECONDS = 86400
DEFAULT_MANAGED_PRESET = "deepgram_nova_3,openai_gpt_4o_mini,minimax_speech_2_6_turbo"
CUSTOM_LLM_PRESET = "deepgram_nova_3,minimax_speech_2_6_turbo"

# Phone/Watch call suppression for Desktop "Hey Xod" wake listener so talking to Xod on
# iPhone/Android/Watch never accidentally wakes the desktop app.
_PHONE_CALL_ACTIVE_UNTIL: float = 0.0
_AGORA_LLM_SECRET: str = ""
_TUNNEL_PROC: Any = None
_TUNNEL_URL: str = ""


def mark_phone_call_active(seconds: int = 1800) -> None:
    global _PHONE_CALL_ACTIVE_UNTIL
    _PHONE_CALL_ACTIVE_UNTIL = max(_PHONE_CALL_ACTIVE_UNTIL, time.time() + max(15, int(seconds)))


def clear_phone_call_active() -> None:
    global _PHONE_CALL_ACTIVE_UNTIL
    _PHONE_CALL_ACTIVE_UNTIL = 0.0


def is_phone_call_active() -> bool:
    return time.time() < _PHONE_CALL_ACTIVE_UNTIL


def get_agora_llm_secret() -> str:
    global _AGORA_LLM_SECRET
    if not _AGORA_LLM_SECRET:
        _AGORA_LLM_SECRET = secrets.token_urlsafe(32)
    return _AGORA_LLM_SECRET


def ensure_custom_llm_url(local_port: int = 8010) -> str:
    """
    Returns a public HTTPS URL for `/api/agora/v1/chat/completions` so Agora ConvoAI can
    call Xod's FULL agent (with all tools, memory, terminal, Tailscale remote_device).
    Uses AGORA_CUSTOM_LLM_URL if set, else an ephemeral `cloudflared` quick tunnel if installed.
    """
    global _TUNNEL_PROC, _TUNNEL_URL
    explicit = (os.environ.get("AGORA_CUSTOM_LLM_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit if explicit.endswith("/chat/completions") else f"{explicit}/api/agora/v1/chat/completions"

    if _TUNNEL_URL and _TUNNEL_PROC is not None and _TUNNEL_PROC.poll() is None:
        return f"{_TUNNEL_URL}/api/agora/v1/chat/completions"

    import shutil
    import subprocess
    cf = shutil.which("cloudflared")
    if not cf:
        return ""
    try:
        kwargs: Dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen([cf, "tunnel", "--url", f"http://127.0.0.1:{local_port}", "--no-autoupdate"], **kwargs)
        deadline = time.time() + 10.0
        found_url = ""
        while time.time() < deadline and proc.poll() is None:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                time.sleep(0.1)
                continue
            m = re.search(r"(https://[a-z0-9-]+\.trycloudflare\.com)", line)
            if m:
                found_url = m.group(1)
                break
        if found_url:
            _TUNNEL_PROC = proc
            _TUNNEL_URL = found_url
            logger.info("Started ephemeral cloudflared tunnel for Agora Full-Tools LLM: %s", found_url)
            return f"{found_url}/api/agora/v1/chat/completions"
        try:
            proc.terminate()
        except Exception:
            pass
    except Exception as e:
        logger.debug("cloudflared tunnel unavailable: %s", e)
    return ""

SECTION_LABELS: List[str] = [
    "Mission",
    "Done looks like",
    "Guardrails",
    "My calls versus yours",
    "The window",
]
NOT_SPECIFIED = "not specified"
FROM_DEVICE = "macbook-agora"
FROM_APP = "agora-xodbrief"
KIND = "xod_voice_brief"

DEFAULT_BRIEF_INBOX_DIR = os.path.expanduser("~/agora-brief-inbox")
DEFAULT_BRIEF_STAGING_DIR = os.path.expanduser("~/agora-brief-inbox-staging")

_LABEL_RE = re.compile(
    r"\b(" + "|".join(re.escape(label) for label in SECTION_LABELS) + r")\s*[.:]\s*"
)
_READY_RE = re.compile(r"\s*Ready to run it\?.*$", re.DOTALL)

DB_PATH = DATA_DIR / "xod.db"

XOD_BRIEF_GREETING = (
    "Xod here. What are we running? Give me the mission in one line, "
    "and I will build the brief with you."
)

XOD_CALL_GREETING = "Hey! Xod here. What's up?"


def build_xod_call_instructions(owner_name: Optional[str] = None) -> str:
    owner = (owner_name or _owner_display_name()).strip() or "the user"
    mem_extra = ""
    try:
        from app.services import user_memory
        core = user_memory.core_note() or ""
        recent = user_memory.recent_chats_note(limit=4) or ""
        if core or recent:
            mem_extra = f"\n\nContext you already know about {owner}:\n{core}\n{recent}"
    except Exception:
        pass
    return (
        f"You are Xod, {owner}'s personal AI assistant on a live phone call. "
        "Talk like a sharp, warm, natural friend on the phone — concise (1 to 3 short spoken sentences), "
        "direct, and genuinely helpful. Answer questions, brainstorm, give advice, or talk through whatever "
        "they bring up. Do NOT ask them for a mission brief, guardrails, or structured intake unless they "
        "explicitly say they want to build a mission brief. Never read out emojis, markdown, asterisks, or URLs."
        f"{mem_extra}"
    )


def _owner_display_name() -> str:
    """Resolve the user's name from Xod's local profile if available, else 'the user'."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=2.0)
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key IN ('user_name', 'owner_name') AND value != '' LIMIT 1"
            ).fetchone()
            if row and str(row[0]).strip():
                return str(row[0]).strip()
        finally:
            conn.close()
    except Exception:
        pass
    return "the user"


def build_xod_brief_instructions(owner_name: Optional[str] = None) -> str:
    owner = (owner_name or _owner_display_name()).strip() or "the user"
    return (
        f"You are Xod, the voice front-end of a desktop AI agent. The user briefs you out loud, "
        f"the way they would brief a chief of staff, and you turn their spoken words into a mission "
        f"brief an agent can actually execute. The user is {owner}, who runs many agent workflows at once.\n\n"
        "Run the conversation in three modes:\n\n"
        "INTAKE (your default mode). Build the brief one question at a time, in this order, skipping anything already answered:\n"
        "1. The mission, in one line: what is being run or built?\n"
        "2. The done-state: what does finished look like, and how would anyone verify it actually happened? Push for something checkable, not a feeling.\n"
        "3. Guardrails: what must the agent never do, touch, or send? What is off-limits?\n"
        f"4. Decision rights: what should the agent handle on its own, and what is the one kind of decision that must come back to {owner}?\n"
        "5. The window: deadline, schedule, or how long the agent should keep going before reporting.\n\n"
        "Keep each question to a single short sentence. Do not lecture, and do not ask two questions at once.\n\n"
        'READ-BACK. When the user says "that\'s the brief", or when all five points are covered, read the brief back in five short spoken sections, '
        'in this order: Mission. Done looks like. Guardrails. My calls versus yours. The window. Speak plainly, one or two sentences per section, '
        'with a beat between sections. If a point was never specified, say "not specified" for that section instead of inventing one. '
        'Then ask: "Ready to run it?" If the user corrects any section, confirm the change and re-read only that section.\n\n'
        'DEBRIEF. If the user says "debrief" at any point, switch modes: ask what got done, what is blocked, and what happens next, one at a time. '
        "Then close with a three-line spoken status: Done. Blocked. Next.\n\n"
        "Style rules for every mode:\n"
        "- Crisp, warm, zero filler. You are a chief of staff, not a chatbot.\n"
        "- Never invent details the user did not give you.\n"
        "- Standing rules you may reflect back when relevant: work is verified at the destination, not by a report about it; "
        "an agent should check what it can check itself instead of asking; and a decision comes back to the user only when it is genuinely theirs.\n"
    )


# ---------------------------------------------------------------------------
# AccessToken2 / Token007 (pure Python stdlib, Agora official wire format)
# ---------------------------------------------------------------------------

def _pack_uint16(v: int) -> bytes:
    return struct.pack("<H", v)


def _pack_uint32(v: int) -> bytes:
    return struct.pack("<I", v)


def _pack_string(s: bytes) -> bytes:
    return _pack_uint16(len(s)) + s


def _pack_map_uint32(m: Dict[int, int]) -> bytes:
    out = _pack_uint16(len(m))
    for k in sorted(m.keys()):
        out += _pack_uint16(k) + _pack_uint32(m[k])
    return out


def _pack_service_rtc(channel_name: str, account: str, privileges: Dict[int, int]) -> bytes:
    return (
        _pack_uint16(1)
        + _pack_map_uint32(privileges)
        + _pack_string(channel_name.encode("utf-8"))
        + _pack_string(account.encode("utf-8") if account else b"")
    )


def _pack_service_rtm(user_id: str, privileges: Dict[int, int]) -> bytes:
    return (
        _pack_uint16(2)
        + _pack_map_uint32(privileges)
        + _pack_string(user_id.encode("utf-8"))
    )


def _pack_service_convo_ai() -> bytes:
    return _pack_uint16(9) + _pack_map_uint32({})


def _require_valid_credentials(app_id: str, app_certificate: str) -> None:
    """Validate 32-char hex App ID and App Certificate without ever logging their values."""
    wrong: List[Tuple[str, int]] = []
    for name, val in (("app_id", app_id), ("app_certificate", app_certificate)):
        if len(val) != AGORA_CREDENTIAL_LENGTH:
            wrong.append((name, len(val)))
            continue
        try:
            bytes.fromhex(val)
        except (TypeError, ValueError):
            raise ValueError(
                f"Agora {name} must be a {AGORA_CREDENTIAL_LENGTH}-character hexadecimal value."
            ) from None
    if wrong:
        detail = " and ".join(f"{name} is {length} characters" for name, length in wrong)
        raise ValueError(
            f"Agora app_id and app_certificate must each be {AGORA_CREDENTIAL_LENGTH} hex characters ({detail})."
        )


def _build_access_token2(
    app_id: str,
    app_certificate: str,
    expire: int,
    services: List[Tuple[int, bytes]],
    *,
    issue_ts: Optional[int] = None,
    salt: Optional[int] = None,
) -> str:
    ts = int(time.time()) if issue_ts is None else int(issue_ts)
    s = secrets.SystemRandom().randint(1, 99999999) if salt is None else int(salt)
    app_id_bytes = app_id.encode("utf-8")
    app_cert_bytes = app_certificate.encode("utf-8")

    signing = hmac.new(_pack_uint32(ts), app_cert_bytes, hashlib.sha256).digest()
    signing = hmac.new(_pack_uint32(s), signing, hashlib.sha256).digest()

    signing_info = (
        _pack_string(app_id_bytes)
        + _pack_uint32(ts)
        + _pack_uint32(expire)
        + _pack_uint32(s)
        + _pack_uint16(len(services))
    )
    for _, svc_bytes in sorted(services, key=lambda item: item[0]):
        signing_info += svc_bytes

    signature = hmac.new(signing, signing_info, hashlib.sha256).digest()
    compressed = zlib.compress(_pack_string(signature) + signing_info)
    return "007" + base64.b64encode(compressed).decode("utf-8")


def generate_convo_ai_token(
    app_id: str,
    app_certificate: str,
    channel_name: str,
    uid: int,
    token_expire: int = DEFAULT_EXPIRY_SECONDS,
    privilege_expire: int = 0,
) -> str:
    """Build a combined RTC + RTM + ConvoAI Token007 (AccessToken2)."""
    _require_valid_credentials(app_id, app_certificate)
    if token_expire <= 0:
        raise ValueError("token_expire must be positive")
    expire = min(int(token_expire), MAX_EXPIRY_SECONDS)
    priv_expire = min(int(privilege_expire), MAX_EXPIRY_SECONDS) if privilege_expire > 0 else expire
    account = "" if uid == 0 else str(uid)

    rtc_svc = _pack_service_rtc(
        channel_name,
        account,
        {1: priv_expire, 2: priv_expire, 3: priv_expire, 4: priv_expire},
    )
    rtm_svc = _pack_service_rtm(account, {1: expire})
    convo_svc = _pack_service_convo_ai()
    return _build_access_token2(
        app_id,
        app_certificate,
        expire,
        [(1, rtc_svc), (2, rtm_svc), (9, convo_svc)],
    )


# ---------------------------------------------------------------------------
# Brief extraction & dual delivery (ported verbatim from brief_capture.py)
# ---------------------------------------------------------------------------

def _role_of(item: Any) -> str:
    role = item.get("role") if isinstance(item, dict) else getattr(item, "role", None)
    if role is None:
        return ""
    value = getattr(role, "value", role)
    return str(value).lower()


def _content_of(item: Any) -> str:
    content = (
        item.get("content")
        if isinstance(item, dict)
        else getattr(item, "content", None)
    )
    return content or ""


def parse_sections(text: str) -> Dict[str, str]:
    """Split one read-back message into {label: section text}."""
    matches = list(_LABEL_RE.finditer(text))
    sections: Dict[str, str] = {}
    for i, match in enumerate(matches):
        label = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        body = _READY_RE.sub("", body).strip()
        if body:
            sections[label] = body
    return sections


def extract_brief_sections(contents: Any) -> Optional[Dict[str, str]]:
    """Extract the final five-section brief dict, or None if no read-back occurred.

    Base = LAST assistant message with >= 3 of the 5 section labels.
    Later assistant messages with exactly 1 label are single-section re-reads
    after a spoken correction and are merged over the base.
    Unspecified sections become 'not specified' (never invented).
    """
    if isinstance(contents, dict):
        contents = contents.get("contents") or []
    assistant_texts = [
        _content_of(item)
        for item in (contents or [])
        if _role_of(item) == "assistant" and _content_of(item).strip()
    ]

    base: Optional[Dict[str, str]] = None
    base_index = -1
    for idx, text in enumerate(assistant_texts):
        sections = parse_sections(text)
        if len(sections) >= 3:
            base = sections
            base_index = idx
    if base is None:
        return None

    for text in assistant_texts[base_index + 1 :]:
        sections = parse_sections(text)
        if len(sections) == 1:
            base.update(sections)

    return {label: base.get(label, NOT_SPECIFIED) for label in SECTION_LABELS}


def extract_brief(contents: Any) -> Optional[str]:
    """Return the final brief as five labeled lines, or None if no read-back occurred."""
    sections = extract_brief_sections(contents)
    if sections is None:
        return None
    return "\n".join(f"{label}: {sections[label]}" for label in SECTION_LABELS)


def build_message(brief_text: str) -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "from_device": FROM_DEVICE,
        "from_app": FROM_APP,
        "kind": KIND,
        "text": brief_text,
        "read": False,
    }


def _local_inbox_dir(live: bool = True) -> str:
    if live:
        return os.getenv("AGORA_BRIEF_INBOX_DIR", DEFAULT_BRIEF_INBOX_DIR)
    return os.getenv("AGORA_BRIEF_STAGING_DIR", DEFAULT_BRIEF_STAGING_DIR)


def _tree_inbox_target() -> Tuple[str, str]:
    ssh_target = (
        get_setting("agora_tree_inbox_ssh", "")
        or os.getenv("AGORA_BRIEF_TREE_INBOX_SSH", "")
        or os.getenv("AGORA_BRIEF_RAZER_SSH", "")
    ).strip()
    remote_dir = (
        get_setting("agora_tree_inbox_dir", "")
        or os.getenv("AGORA_BRIEF_TREE_INBOX_DIR", "")
    ).strip()
    return ssh_target, remote_dir


def _write_local(directory: str, message: Dict[str, Any]) -> str:
    os.makedirs(directory, exist_ok=True)
    final_path = os.path.join(directory, f"{message['id']}.json")
    tmp_path = final_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(message, fh, indent=2)
        fh.write("\n")
    os.replace(tmp_path, final_path)
    return final_path


def _deliver_tree_inbox(message: Dict[str, Any], staging: bool = False) -> bool:
    """Copy the message into the configured family tree-inbox over scp (.tmp -> atomic rename).

    Best effort: returns False instead of raising so a network flap never breaks stop.
    """
    ssh_target, base_remote_dir = _tree_inbox_target()
    if not ssh_target or not base_remote_dir:
        return False
    remote_dir = (
        base_remote_dir.rstrip("/\\") + "-staging"
        if staging and not base_remote_dir.endswith("-staging")
        else base_remote_dir
    )
    local_tmp = os.path.join(
        os.getenv("TMPDIR", "/tmp"), f"xod-brief-{message['id']}.json"
    )
    remote_tmp = f"{remote_dir}/{message['id']}.json.tmp"
    remote_final = f"{remote_dir}/{message['id']}.json"
    is_windows = bool(re.match(r"^[A-Za-z]:[\\/]", remote_dir))
    try:
        with open(local_tmp, "w", encoding="utf-8") as fh:
            json.dump(message, fh, indent=2)
            fh.write("\n")
        if is_windows:
            win_dir = remote_dir.replace("/", "\\")
            win_tmp = remote_tmp.replace("/", "\\")
            win_final = remote_final.replace("/", "\\")
            mkdir_cmd = f"powershell -NoProfile -Command \"New-Item -ItemType Directory -Force '{win_dir}' | Out-Null\""
            mv_cmd = f"powershell -NoProfile -Command \"Move-Item -Force '{win_tmp}' '{win_final}'\""
        else:
            mkdir_cmd = f"mkdir -p '{remote_dir}'"
            mv_cmd = f"mv -f '{remote_tmp}' '{remote_final}'"

        subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", ssh_target, mkdir_cmd],
            check=True,
            timeout=45,
            capture_output=True,
        )
        subprocess.run(
            ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", local_tmp, f"{ssh_target}:{remote_tmp}"],
            check=True,
            timeout=45,
            capture_output=True,
        )
        subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", ssh_target, mv_cmd],
            check=True,
            timeout=45,
            capture_output=True,
        )
        return True
    except Exception:
        logger.warning("Tree-inbox delivery failed for brief %s", message["id"], exc_info=True)
        return False
    finally:
        try:
            os.remove(local_tmp)
        except OSError:
            pass


def deliver_brief(brief_text: str, live: bool = True) -> Dict[str, Any]:
    """Write one brief to the local inbox and (if configured) the family tree-inbox."""
    message = build_message(brief_text)
    local_dir = _local_inbox_dir(live=live)
    local_path = _write_local(local_dir, message)
    tree_ok = _deliver_tree_inbox(message, staging=not live)
    return {
        "id": message["id"],
        "ts": message["ts"],
        "local_path": local_path,
        "tree_inbox_delivered": tree_ok,
        "live": live,
    }


# ---------------------------------------------------------------------------
# Xod DB brief intake (first-class brief records -> confirm -> mission harness)
# ---------------------------------------------------------------------------

def _init_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_briefs (
                id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                agent_id TEXT DEFAULT '',
                channel_name TEXT DEFAULT '',
                mission_title TEXT NOT NULL,
                brief_text TEXT NOT NULL,
                sections_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ready',
                mission_id TEXT DEFAULT '',
                delivery_json TEXT DEFAULT '{}',
                created_at REAL NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def save_captured_brief(
    brief_text: str,
    sections: Dict[str, str],
    *,
    agent_id: str = "",
    channel_name: str = "",
    delivery: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _init_db()
    brief_id = (delivery or {}).get("id") or str(uuid.uuid4())
    ts = (delivery or {}).get("ts") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    mission_title = (sections.get("Mission") or "Voice brief").strip()
    if mission_title.lower() == NOT_SPECIFIED:
        mission_title = "Voice brief"
    now = time.time()
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO voice_briefs
            (id, ts, agent_id, channel_name, mission_title, brief_text, sections_json, status, mission_id, delivery_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', '', ?, ?)
            """,
            (
                brief_id,
                ts,
                agent_id,
                channel_name,
                mission_title[:240],
                brief_text,
                json.dumps(sections),
                json.dumps(delivery or {}),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_brief(brief_id) or {}


def _row_to_brief(row: Tuple[Any, ...]) -> Dict[str, Any]:
    try:
        sections = json.loads(row[6] or "{}")
    except Exception:
        sections = {}
    try:
        delivery = json.loads(row[9] or "{}")
    except Exception:
        delivery = {}
    return {
        "id": row[0],
        "ts": row[1],
        "agent_id": row[2],
        "channel_name": row[3],
        "mission_title": row[4],
        "brief_text": row[5],
        "sections": sections,
        "status": row[7],
        "mission_id": row[8] or None,
        "delivery": delivery,
        "created_at": row[10],
    }


def list_briefs(limit: int = 30) -> List[Dict[str, Any]]:
    _init_db()
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    try:
        rows = conn.execute(
            """
            SELECT id, ts, agent_id, channel_name, mission_title, brief_text,
                   sections_json, status, mission_id, delivery_json, created_at
            FROM voice_briefs
            WHERE status != 'dismissed'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [_row_to_brief(r) for r in rows]
    finally:
        conn.close()


def get_brief(brief_id: str) -> Optional[Dict[str, Any]]:
    _init_db()
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    try:
        row = conn.execute(
            """
            SELECT id, ts, agent_id, channel_name, mission_title, brief_text,
                   sections_json, status, mission_id, delivery_json, created_at
            FROM voice_briefs
            WHERE id = ?
            """,
            (brief_id,),
        ).fetchone()
        return _row_to_brief(row) if row else None
    finally:
        conn.close()


def launch_brief_as_mission(brief_id: str, continue_on_failure: bool = True) -> Dict[str, Any]:
    """Confirm a captured voice brief and hand it to Xod's mission harness."""
    brief = get_brief(brief_id)
    if not brief:
        raise ValueError("Brief not found")
    if brief.get("mission_id"):
        from app.services import missions
        existing = missions.get_mission(brief["mission_id"])
        if existing:
            return {"brief": brief, "mission": existing}

    from app.services import missions
    goal = (
        "Voice Brief (confirmed by user):\n\n"
        + brief["brief_text"]
    )
    mission = missions.create_mission(
        goal,
        continue_on_failure=continue_on_failure,
        origin="voice_brief",
    )
    mission_id = mission.get("id", "")
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    try:
        conn.execute(
            "UPDATE voice_briefs SET status = 'launched', mission_id = ? WHERE id = ?",
            (mission_id, brief_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"brief": get_brief(brief_id), "mission": mission}


def dismiss_brief(brief_id: str) -> bool:
    _init_db()
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    try:
        cur = conn.execute(
            "UPDATE voice_briefs SET status = 'dismissed' WHERE id = ?",
            (brief_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ConvoAI session lifecycle (credentials -> Token007 -> join -> history -> leave)
# ---------------------------------------------------------------------------

def _credentials() -> Dict[str, str]:
    return {
        "app_id": (settings.agora_app_id or "").strip(),
        "app_certificate": (settings.agora_app_certificate or "").strip(),
        "customer_id": (settings.agora_customer_id or "").strip(),
        "customer_secret": (settings.agora_customer_secret or "").strip(),
    }


def is_configured() -> bool:
    c = _credentials()
    return bool(c["app_id"] and c["app_certificate"])


def _rest_auth_header(
    creds: Dict[str, str],
    *,
    channel_name: str = "",
    uid: int = 0,
) -> str:
    """Use Basic auth if Customer ID + Secret are configured, else Token007."""
    if creds["customer_id"] and creds["customer_secret"]:
        raw = f"{creds['customer_id']}:{creds['customer_secret']}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")
    token = generate_convo_ai_token(
        app_id=creds["app_id"],
        app_certificate=creds["app_certificate"],
        channel_name=channel_name,
        uid=uid,
        token_expire=DEFAULT_EXPIRY_SECONDS,
    )
    return f"agora token={token}"


class AgoraVoiceService:
    """Manages active Agora ConvoAI voice brief sessions inside Xod's local backend."""

    def __init__(self) -> None:
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def active_sessions(self) -> List[Dict[str, Any]]:
        return [
            {
                "agent_id": aid,
                "channel_name": meta.get("channel_name", ""),
                "preset": meta.get("preset", "xodbrief"),
                "started_at": meta.get("started_at", 0),
            }
            for aid, meta in self._sessions.items()
        ]

    def generate_client_config(
        self,
        channel: Optional[str] = None,
        uid: Optional[int] = None,
    ) -> Dict[str, Any]:
        creds = _credentials()
        if not creds["app_id"] or not creds["app_certificate"]:
            raise ValueError(
                "Agora App ID and App Certificate are not configured. Add them in Settings → Connections."
            )
        user_uid = random.randint(1000, 9999999) if uid is None or uid <= 0 else int(uid)
        agent_uid = random.randint(10000000, 99999999)
        channel_name = (channel or "").strip() or f"xod-brief-{int(time.time())}-{random.randint(1000, 9999)}"
        token = generate_convo_ai_token(
            app_id=creds["app_id"],
            app_certificate=creds["app_certificate"],
            channel_name=channel_name,
            uid=user_uid,
            token_expire=DEFAULT_EXPIRY_SECONDS,
        )
        return {
            "app_id": creds["app_id"],
            "token": token,
            "uid": user_uid,
            "channel_name": channel_name,
            "agent_uid": agent_uid,
        }

    async def start_session(
        self,
        channel_name: str,
        agent_uid: int,
        user_uid: int,
        preset: str = "call",
        instructions: Optional[str] = None,
        greeting: Optional[str] = None,
        output_audio_codec: Optional[str] = None,
    ) -> Dict[str, Any]:
        creds = _credentials()
        if not creds["app_id"] or not creds["app_certificate"]:
            raise ValueError(
                "Agora App ID and App Certificate are not configured. Add them in Settings → Connections."
            )
        channel_name = (channel_name or "").strip()
        if not channel_name:
            raise ValueError("channel_name is required")
        if int(agent_uid) <= 0 or int(user_uid) <= 0:
            raise ValueError("agent_uid and user_uid must be positive integers")

        is_call = (preset or "").strip().lower() in ("call", "phone", "chat", "companion")
        effective_instructions = (instructions or "").strip() or (
            build_xod_call_instructions() if is_call else build_xod_brief_instructions()
        )
        effective_greeting = (greeting or "").strip() or (
            XOD_CALL_GREETING if is_call else XOD_BRIEF_GREETING
        )

        agent_rtc_token = generate_convo_ai_token(
            app_id=creds["app_id"],
            app_certificate=creds["app_certificate"],
            channel_name=channel_name,
            uid=int(agent_uid),
            token_expire=DEFAULT_EXPIRY_SECONDS,
        )
        params: Dict[str, Any] = {
            "data_channel": "rtm",
            "enable_metrics": True,
            "enable_error_message": True,
            "audio_scenario": "chorus",
        }
        if isinstance(output_audio_codec, str) and output_audio_codec.strip():
            params["output_audio_codec"] = output_audio_codec.strip()

        custom_llm_url = await asyncio.to_thread(ensure_custom_llm_url) if is_call else ""
        llm_block: Dict[str, Any] = {
            "style": "openai",
            "input_modalities": ["text"],
            "greeting_message": effective_greeting,
            "failure_message": "Give me just a second, checking that now.",
            "max_history": 30,
            "system_messages": [
                {"role": "system", "content": effective_instructions}
            ],
            "params": {
                "max_tokens": 512,
                "temperature": 0.7,
                "top_p": 0.95,
            },
        }
        effective_preset = DEFAULT_MANAGED_PRESET
        if custom_llm_url:
            effective_preset = CUSTOM_LLM_PRESET
            llm_block["url"] = custom_llm_url
            llm_block["api_key"] = get_agora_llm_secret()
            llm_block["params"]["model"] = "xod-full-tools"

        agent_name = f"xodcall-{int(time.time())}-{random.randint(1000, 9999)}" if is_call else f"xodbrief-{int(time.time())}-{random.randint(1000, 9999)}"
        payload: Dict[str, Any] = {
            "name": agent_name,
            "preset": effective_preset,
            "properties": {
                "channel": channel_name,
                "token": agent_rtc_token,
                "agent_rtc_uid": str(int(agent_uid)),
                "remote_rtc_uids": [str(int(user_uid))],
                "enable_string_uid": False,
                "idle_timeout": 60,
                "advanced_features": {"enable_rtm": True, "enable_tools": True},
                "turn_detection": {
                    "config": {
                        "speech_threshold": 0.5,
                        "start_of_speech": {
                            "mode": "vad",
                            "vad_config": {
                                "interrupt_duration_ms": 160,
                                "prefix_padding_ms": 300,
                            },
                        },
                        "end_of_speech": {
                            "mode": "vad",
                            "vad_config": {"silence_duration_ms": 480},
                        },
                    },
                    "language": "en-US",
                },
                "parameters": params,
                "tts": {
                    "vendor": "minimax",
                    "params": {
                        "voice_setting": {"voice_id": "English_captivating_female1"}
                    },
                },
                "llm": llm_block,
                "asr": {
                    "vendor": "deepgram",
                    "params": {"language": "en"},
                    "language": "en-US",
                },
            },
        }

        url = f"{AGORA_API_BASE}/v2/projects/{creds['app_id']}/join"
        headers = {
            "Authorization": _rest_auth_header(creds, channel_name=channel_name, uid=int(agent_uid)),
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                detail = resp.text[:400]
                logger.warning("Agora join failed status=%s body=%s", resp.status_code, detail)
                raise RuntimeError(f"Agora ConvoAI start failed ({resp.status_code}): {detail}")
            data = resp.json()

        agent_id = data.get("agent_id") or data.get("agentId") or ""
        if not agent_id:
            raise RuntimeError("Agora ConvoAI start returned no agent_id")

        self._sessions[agent_id] = {
            "preset": preset or "xodbrief",
            "channel_name": channel_name,
            "agent_uid": int(agent_uid),
            "user_uid": int(user_uid),
            "started_at": time.time(),
        }
        logger.info("Started Agora voice brief agent_id=%s channel=%s preset=%s", agent_id, channel_name, preset)
        return {
            "agent_id": agent_id,
            "channel_name": channel_name,
            "status": "started",
            "preset": preset or "xodbrief",
        }

    async def _fetch_history(
        self,
        creds: Dict[str, str],
        agent_id: str,
        channel_name: str,
        agent_uid: int,
    ) -> List[Dict[str, Any]]:
        url = f"{AGORA_API_BASE}/v2/projects/{creds['app_id']}/agents/{agent_id}/history"
        headers = {
            "Authorization": _rest_auth_header(creds, channel_name=channel_name, uid=agent_uid),
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                logger.warning("Agora history fetch returned %s for agent_id=%s", resp.status_code, agent_id)
                return []
            data = resp.json()
            contents = data.get("contents") if isinstance(data, dict) else []
            return contents if isinstance(contents, list) else []

    async def stop_session(self, agent_id: str) -> Dict[str, Any]:
        """Fetch history BEFORE stopping (Agora requirement), stop agent, capture & deliver brief.

        Capture or delivery failures are logged and NEVER fail the stop itself.
        """
        agent_id = (agent_id or "").strip()
        if not agent_id:
            raise ValueError("agent_id is required")

        creds = _credentials()
        meta = self._sessions.pop(agent_id, {})
        preset = meta.get("preset", "xodbrief")
        channel_name = meta.get("channel_name", "")
        agent_uid = int(meta.get("agent_uid", 0))

        # 1. Capture history BEFORE stopping (Agora history API only serves RUNNING agents)
        sections: Optional[Dict[str, str]] = None
        brief_text: Optional[str] = None
        if preset == "xodbrief" and creds["app_id"] and creds["app_certificate"]:
            try:
                contents = await self._fetch_history(creds, agent_id, channel_name, agent_uid)
                sections = extract_brief_sections(contents)
                if sections is not None:
                    brief_text = "\n".join(f"{label}: {sections[label]}" for label in SECTION_LABELS)
                    logger.info("Captured xodbrief from history agent_id=%s", agent_id)
                else:
                    logger.info("xodbrief session ended without a brief read-back agent_id=%s", agent_id)
            except Exception:
                logger.warning("xodbrief history capture failed agent_id=%s", agent_id, exc_info=True)

        # 2. Stop the running Agora ConvoAI agent
        stop_ok = False
        if creds["app_id"] and creds["app_certificate"]:
            url = f"{AGORA_API_BASE}/v2/projects/{creds['app_id']}/agents/{agent_id}/leave"
            headers = {
                "Authorization": _rest_auth_header(creds, channel_name=channel_name, uid=agent_uid),
            }
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(url, headers=headers)
                    stop_ok = resp.status_code < 400
                    if not stop_ok:
                        logger.warning("Agora leave returned %s for agent_id=%s", resp.status_code, agent_id)
            except Exception:
                logger.warning("Agora leave failed for agent_id=%s", agent_id, exc_info=True)

        # 3. Dual-deliver and intake captured brief into Xod (never fails stop)
        captured_record: Optional[Dict[str, Any]] = None
        if brief_text and sections is not None:
            delivery: Dict[str, Any] = {}
            try:
                delivery = await asyncio.to_thread(deliver_brief, brief_text, True)
            except Exception:
                logger.warning("xodbrief dual delivery failed agent_id=%s", agent_id, exc_info=True)
            try:
                captured_record = save_captured_brief(
                    brief_text,
                    sections,
                    agent_id=agent_id,
                    channel_name=channel_name,
                    delivery=delivery,
                )
            except Exception:
                logger.warning("xodbrief DB intake failed agent_id=%s", agent_id, exc_info=True)

        return {
            "agent_id": agent_id,
            "stopped": stop_ok,
            "captured": captured_record is not None,
            "brief": captured_record,
        }


agora_voice_service = AgoraVoiceService()
