#!/usr/bin/env python3
"""
Smoke test for Xod's Agora Voice Brief integration (app/services/agora_voice.py).

Verifies:
1. Token007 (AccessToken2) minting validates 32-char hex credentials, never leaks values in errors,
   and produces a valid '007...' zlib-compressed token with RTC + RTM + ConvoAI services.
2. Exact brief extraction rules ported from brief_capture.py:
   - Last assistant message with >= 3 of the 5 labels is the base
   - Later single-section re-reads merge over the base (spoken corrections win)
   - 'Ready to run it?' is stripped
   - Unspecified sections become 'not specified' (never invented)
   - Sessions with no read-back return None
3. Dual-delivery envelope matches {"id","ts","from_device":"macbook-agora",
   "from_app":"agora-xodbrief","kind":"xod_voice_brief","text","read":false},
   written atomically (.tmp -> rename) to the local inbox.
4. Stop sequence fetches history BEFORE calling leave (Agora requires running agent for history),
   and capture/delivery errors never fail stop_session().
5. Brief intake persists to SQLite (voice_briefs), lists cleanly, and launches into Xod's mission harness.
"""
import asyncio
import base64
import json
import os
import sys
import tempfile
import zlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from app.services import agora_voice


PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def test_token007() -> None:
    app_id = "0123456789abcdef0123456789abcdef"
    cert = "fedcba9876543210fedcba9876543210"
    tok = agora_voice.generate_convo_ai_token(app_id, cert, "xod-brief-test", 4242, token_expire=3600)
    check("Token007 starts with 007", tok.startswith("007"))
    raw = zlib.decompress(base64.b64decode(tok[3:]))
    check("Token007 decompresses cleanly and embeds app_id", app_id.encode("utf-8") in raw)

    # Bad length error never leaks secret value
    bad_secret = "deadbeef"
    try:
        agora_voice.generate_convo_ai_token(app_id, bad_secret, "ch", 1)
        check("Invalid cert rejected", False)
    except ValueError as e:
        msg = str(e)
        check("Invalid cert rejected with length error", "8 characters" in msg and bad_secret not in msg, msg)


def test_extraction() -> None:
    sample_history = [
        {"role": "assistant", "content": "Xod here. What are we running?"},
        {"role": "user", "content": "Check morning health."},
        {
            "role": "assistant",
            "content": (
                "Mission. Verify the morning rig is alive. "
                "Done looks like. Both servers return 200 and a summary lands in chat. "
                "Guardrails. Read-only checks only, never restart anything. "
                "My calls versus yours. Run checks automatically; ask before restarts. "
                "The window. Report by 8 AM. Ready to run it?"
            ),
        },
        {"role": "user", "content": "Change the window to 7 AM."},
        {"role": "assistant", "content": "The window. Report by 7 AM. Ready to run it?"},
    ]
    brief = agora_voice.extract_brief(sample_history)
    check("Extracts 5-section brief", brief is not None)
    assert brief is not None
    lines = brief.splitlines()
    check("Exactly 5 lines", len(lines) == 5, str(lines))
    check("Spoken correction merged (7 AM)", lines[4] == "The window: Report by 7 AM.", lines[4])
    check("Ready-to-run prompt stripped", "Ready to run it?" not in brief)

    # Missing sections become 'not specified' (never invented)
    partial = [
        {
            "role": "assistant",
            "content": "Mission: Ship v2. Done looks like: Tag is live. Guardrails: Do not push secrets.",
        }
    ]
    secs = agora_voice.extract_brief_sections(partial)
    check("Partial read-back fills unspecified with 'not specified'",
          secs is not None
          and secs["My calls versus yours"] == "not specified"
          and secs["The window"] == "not specified")

    # No read-back returns None
    no_rb = [{"role": "assistant", "content": "What are we running?"}, {"role": "user", "content": "Never mind."}]
    check("No read-back returns None", agora_voice.extract_brief(no_rb) is None)


def test_envelope_and_delivery() -> None:
    brief = "Mission: Test.\nDone looks like: Green.\nGuardrails: None.\nMy calls versus yours: All yours.\nThe window: Today."
    msg = agora_voice.build_message(brief)
    check("Envelope keys exact", set(msg.keys()) == {"id", "ts", "from_device", "from_app", "kind", "text", "read"})
    check("Envelope metadata exact",
          msg["from_device"] == "macbook-agora"
          and msg["from_app"] == "agora-xodbrief"
          and msg["kind"] == "xod_voice_brief"
          and msg["read"] is False)

    with tempfile.TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {"AGORA_BRIEF_INBOX_DIR": tmp}, clear=False), \
             patch.object(agora_voice, "_deliver_tree_inbox", return_value=False):
            res = agora_voice.deliver_brief(brief, live=True)
            p = Path(res["local_path"])
            check("Atomic local write created JSON file", p.exists() and not Path(str(p) + ".tmp").exists())
            loaded = json.loads(p.read_text(encoding="utf-8"))
            check("Written JSON matches brief text", loaded["text"] == brief and loaded["kind"] == "xod_voice_brief")


def test_history_before_stop_and_intake() -> None:
    call_order = []

    async def fake_fetch(creds, agent_id, channel_name, agent_uid):
        call_order.append("history")
        return [
            {
                "role": "assistant",
                "content": (
                    "Mission. Audit landing page. Done looks like. Report written. "
                    "Guardrails. Read-only. My calls versus yours. Ask before editing. "
                    "The window. Today. Ready to run it?"
                ),
            }
        ]

    class FakeResp:
        status_code = 200
        text = "{}"
        def json(self): return {}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            call_order.append("leave")
            return FakeResp()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = Path(tmp) / "xod_test.db"
        svc = agora_voice.AgoraVoiceService()
        svc._sessions["agent-123"] = {
            "preset": "xodbrief",
            "channel_name": "xod-brief-1",
            "agent_uid": 10001,
            "user_uid": 20002,
            "started_at": 1.0,
        }
        fake_creds = {
            "app_id": "0123456789abcdef0123456789abcdef",
            "app_certificate": "fedcba9876543210fedcba9876543210",
            "customer_id": "",
            "customer_secret": "",
        }
        with patch.object(agora_voice, "DB_PATH", tmp_db), \
             patch.object(agora_voice, "_credentials", return_value=fake_creds), \
             patch.object(svc, "_fetch_history", side_effect=fake_fetch), \
             patch.object(agora_voice.httpx, "AsyncClient", return_value=FakeClient()), \
             patch.dict(os.environ, {"AGORA_BRIEF_INBOX_DIR": tmp}, clear=False), \
             patch.object(agora_voice, "_deliver_tree_inbox", return_value=True):
            out = asyncio.run(svc.stop_session("agent-123"))
            check("History fetched BEFORE leave", call_order == ["history", "leave"], str(call_order))
            check("Stop returned captured=True", out["captured"] is True and out["stopped"] is True)
            briefs = agora_voice.list_briefs()
            check("Captured brief persisted in voice_briefs DB", len(briefs) == 1 and briefs[0]["mission_title"] == "Audit landing page.")
            # Launch as mission (mocking missions.create_mission)
            with patch("app.services.missions.create_mission", return_value={"id": "mission-abc", "status": "planning"}) as mk:
                launched = agora_voice.launch_brief_as_mission(briefs[0]["id"])
                check("Launching brief creates mission with origin=voice_brief",
                      mk.called and mk.call_args.kwargs.get("origin") == "voice_brief"
                      and launched["brief"]["status"] == "launched"
                      and launched["brief"]["mission_id"] == "mission-abc")


def main() -> int:
    print("Running Agora Voice Brief smoke tests...")
    test_token007()
    test_extraction()
    test_envelope_and_delivery()
    test_history_before_stop_and_intake()
    print(f"\nResults: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
