"""Smoke tests for Personal Training Data Vault and Local Model Harness Turbo."""
import hashlib
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import harness_turbo, training_vault, control_center
from app.services.mcp_client import MCPHub
from app import config as cfg


def test_pii_and_secret_scrubber():
    raw = (
        "Key sk-proj-abc1234567890XYZ987654, GitHub ghp_1234567890abcdefghij123456, "
        "Google AIzaSyD-1234567890abcdef123456, JWT eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123456, "
        "email alice@example.com, phone (415) 555-0199, path /Users/alice/projects/app, "
        "password=SuperSecret99"
    )
    cleaned, count = training_vault.scrub_text(raw)
    assert count >= 7, f"Expected >=7 redactions, got {count}: {cleaned}"
    for leak in ("sk-proj-", "ghp_12345", "AIzaSyD-", "eyJhbGci", "alice@example.com", "555-0199", "/Users/alice", "SuperSecret99"):
        assert leak not in cleaned, f"Leaked sensitive string {leak!r} in: {cleaned}"
    assert "/Users/[USER]/projects/app" in cleaned
    assert "[REDACTED_EMAIL]" in cleaned
    assert "[REDACTED_PHONE]" in cleaned
    print("  [PASS] PII & secret scrubber")


def test_training_vault_export_and_checksums():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db = tmp_path / "test_xod.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(
            """
            CREATE TABLE llm_examples (
                id TEXT PRIMARY KEY, created_at REAL, task TEXT, group_id TEXT,
                model TEXT, provider TEXT, think INTEGER, messages_json TEXT,
                response TEXT, prompt_hash TEXT, latency_ms INTEGER, label INTEGER,
                label_reason TEXT, meta_json TEXT
            );
            CREATE TABLE preference_pairs (
                id TEXT PRIMARY KEY, created_at REAL, task TEXT, example_id TEXT,
                prompt_json TEXT, chosen TEXT, rejected TEXT, source TEXT, note TEXT
            );
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, title TEXT, persona TEXT, created_at REAL, updated_at REAL
            );
            CREATE TABLE messages (
                id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
                reasoning TEXT, tool_events TEXT, created_at REAL
            );
            """
        )
        now = time.time()
        conn.execute(
            "INSERT INTO llm_examples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ex-1", now, "browser_step", "grp-1", "claude-sonnet", "anthropic", 0,
                json.dumps([{"role": "user", "content": "Open /Users/bob/docs and email bob@example.com"}]),
                '{"action": "click", "target": "submit"}', "h1", 120, 1, "worked", "{}",
            ),
        )
        conn.execute(
            "INSERT INTO preference_pairs VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "pp-1", now, "browser_step", "ex-bad",
                json.dumps([{"role": "user", "content": "Fill form with key sk-live-1234567890abcdef1234"}]),
                '{"action": "ask_user"}', '{"action": "guess"}', "rule_fix", "no guessing",
            ),
        )
        conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", ("s-1", "Test Chat", "general", now, now))
        conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", ("m-1", "s-1", "user", "Call me at 555-234-5678", "", "", now))
        conn.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
            ("m-2", "s-1", "assistant", "Done!", "", json.dumps([{"tool": "web_search", "ok": True}]), now + 1),
        )
        conn.commit()
        conn.close()

        c = training_vault.counts(db_path=db)
        assert c["sft_chat"] == 2, f"Expected 2 sft_chat, got {c}"
        assert c["dpo_preferences"] == 1, f"Expected 1 dpo_preferences, got {c}"
        assert c["agent_trajectories"] == 2, f"Expected 2 agent_trajectories, got {c}"

        out_dir = tmp_path / "vault_out"
        res = training_vault.export_vault(dest_dir=str(out_dir), scrub_pii=True, db_path=db)
        assert res["ok"] is True
        assert res["counts"]["sft_chat"] == 2
        assert res["counts"]["dpo_preferences"] == 1
        assert res["counts"]["agent_trajectories"] == 2
        assert res["redactions_count"] >= 4

        # Verify files exist and contain zero leaked PII
        for fname in ("sft_chat.jsonl", "dpo_preferences.jsonl", "agent_trajectories.jsonl", "dataset_card.json", "SHA256SUMS.txt"):
            fpath = out_dir / fname
            assert fpath.exists(), f"Missing {fname}"
            text = fpath.read_text(encoding="utf-8")
            for leak in ("bob@example.com", "/Users/bob", "sk-live-123456", "555-234-5678"):
                assert leak not in text, f"PII leak {leak!r} in {fname}"

        # Verify SHA256SUMS.txt matches actual file digests
        sums = (out_dir / "SHA256SUMS.txt").read_text(encoding="utf-8").strip().splitlines()
        assert len(sums) == 4
        for line in sums:
            expected_hash, fname = line.split("  ", 1)
            actual_hash = hashlib.sha256((out_dir / fname).read_bytes()).hexdigest()
            assert actual_hash == expected_hash, f"Checksum mismatch for {fname}"

    dests = training_vault.list_destinations()
    assert any(d["kind"] == "local" for d in dests)
    print("  [PASS] Training Vault export, dataset_card.json, SHA256SUMS.txt & destinations")


def test_local_model_harness_turbo_compaction():
    assert harness_turbo.is_local_provider({"provider": "xod"}) is True
    assert harness_turbo.is_local_provider({"provider": "lm_studio"}) is True
    assert harness_turbo.is_local_provider({"provider": "ollama"}) is True
    assert harness_turbo.is_local_provider({"provider": "custom", "base_url": "http://127.0.0.1:11434/v1"}) is True
    assert harness_turbo.is_local_provider({"provider": "anthropic", "base_url": "https://api.anthropic.com"}) is False

    # 3 tool messages of 700 chars each:
    # - Cloud defaults (keep_recent_tools=3, threshold=900) leave all 3 untouched.
    # - Local Turbo (keep_recent_tools=2, threshold=500, max_stale_chars=240) compacts the oldest one!
    big_tool = "A" * 700
    base_msgs = [
        {"role": "system", "content": "You are Xod."},
        {"role": "tool", "content": big_tool},
        {"role": "tool", "content": big_tool},
        {"role": "tool", "content": big_tool},
    ]
    cloud_payload = harness_turbo.optimize_payload(
        {"messages": [dict(m) for m in base_msgs]},
        {"provider": "openai", "base_url": "https://api.openai.com/v1"},
    )
    assert len(cloud_payload["messages"][1]["content"]) == 700

    local_payload = harness_turbo.optimize_payload(
        {"messages": [dict(m) for m in base_msgs]},
        {"provider": "xod", "base_url": "http://127.0.0.1:8080/v1"},
    )
    assert len(local_payload["messages"][1]["content"]) <= 240, f"Expected local compaction <=240, got {len(local_payload['messages'][1]['content'])}"
    assert len(local_payload["messages"][2]["content"]) == 700
    assert len(local_payload["messages"][3]["content"]) == 700
    print("  [PASS] Local Model Harness Turbo aggressive compaction")


def test_local_model_mcp_tool_cap():
    hub = MCPHub()
    fake_defs = [
        {
            "type": "function",
            "function": {
                "name": f"mcp__github__issue_tool_{i}",
                "description": f"[github] Manage github issue {i}",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for i in range(15)
    ]
    hub.tool_definitions = lambda: fake_defs  # type: ignore

    orig_provider = getattr(cfg, "LLM_PROVIDER", "")
    try:
        cfg.LLM_PROVIDER = "xod"
        chosen = hub.tools_for_query("check github issues", limit=16)
        assert len(chosen) <= harness_turbo.LOCAL_MCP_TOOL_CAP, f"Expected <=6 tools for local provider, got {len(chosen)}"
        assert hub.last_route.get("local_turbo") is True

        cfg.LLM_PROVIDER = "anthropic"
        chosen_cloud = hub.tools_for_query("check github issues", limit=16)
        assert len(chosen_cloud) > harness_turbo.LOCAL_MCP_TOOL_CAP
        assert hub.last_route.get("local_turbo") is False
    finally:
        cfg.LLM_PROVIDER = orig_provider
    print("  [PASS] Local Model 6-tool MCP routing cap")


def test_control_center_and_routes():
    snap = control_center.snapshot()
    assert "training_vault" in snap
    assert "counts" in snap["training_vault"]
    assert "local_turbo_active" in snap["brain"]

    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/training-vault/status" in paths
    assert "/api/training-vault/export" in paths
    print("  [PASS] Control Center snapshot & FastAPI routes")


if __name__ == "__main__":
    print("Running Training Vault & Local Model Harness Turbo smoke tests...")
    test_pii_and_secret_scrubber()
    test_training_vault_export_and_checksums()
    test_local_model_harness_turbo_compaction()
    test_local_model_mcp_tool_cap()
    test_control_center_and_routes()
    print("ALL TRAINING VAULT & LOCAL TURBO TESTS PASSED!")
