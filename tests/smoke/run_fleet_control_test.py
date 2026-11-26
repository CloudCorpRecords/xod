"""Smoke tests for Fleet Control, Rubber-Stamp Auto-Approver, Cross-Chat Agent Bus,
and Shared Fleet Blackboard + Chat-Summary Sync.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = ""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


print("Running Fleet Control, Cross-Chat Bus & Blackboard smoke tests...")

from app.services import fleet_control, xod_network, memory_layers, harness, control_center

with tempfile.TemporaryDirectory() as tmpdir:
    tmp_path = Path(tmpdir)
    with patch.object(fleet_control, "_dir", return_value=tmp_path):
        # 1. Rubber-stamp policy modes
        orig_mode = fleet_control.mode()
        try:
            fleet_control.set_mode("act")
            v_act = fleet_control.evaluate_fleet_card("task", "ref_1", "laptop", "run tests")
            check("act mode auto-approves with rubberstamp policy", v_act["auto_approve"] is True and v_act["policy"] == "rubberstamp")

            fleet_control.set_mode("decide")
            v_dec = fleet_control.evaluate_fleet_card("task", "ref_2", "laptop", "run tests")
            check("decide mode audits without auto-approving", v_dec["auto_approve"] is False and v_dec["decision"] == "audit_only")

            fleet_control.set_mode("off")
            v_off = fleet_control.evaluate_fleet_card("task", "ref_3", "laptop", "run tests")
            check("off mode holds for manual ask", v_off["auto_approve"] is False and v_off["decision"] == "ask")
            check("All 3 evaluations written to immutable audit.jsonl", fleet_control.audit_count() == 3)

            # 2. xod_network.receive_task integration under rubber-stamp vs off
            fleet_control.set_mode("act")
            with patch.object(xod_network, "_caller", return_value={"host": "laptop", "same_account": True}):
                with patch.object(xod_network, "accept", return_value={"success": True, "task_id": "t_auto_1", "title": "Auto task"}):
                    r = xod_network.receive_task("100.64.0.2", {"request": "check build status", "from": "laptop"})
                    check("receive_task auto-approves verified same-account task in act mode", r.get("status") == "auto_approved" and r.get("task_id") == "t_auto_1", str(r))

            with patch.object(xod_network, "_caller", return_value={"host": "stranger", "same_account": False}):
                blocked = False
                try:
                    xod_network.receive_task("100.64.0.99", {"request": "unauthorized"})
                except xod_network.NetworkError:
                    blocked = True
                check("receive_task still refuses non-account caller", blocked)

            # 3. Shared Fleet Blackboard + CRDT merge
            e1 = fleet_control.blackboard_post("release", "Building v1.1.36", actor="xod", branch="main", files=["app/main.py"])
            e2 = fleet_control.blackboard_post("mcp", "Smart routing active", actor="fleet")
            items = fleet_control.blackboard_read(limit=10)
            check("Blackboard stores and reads active entries", len(items) == 2 and items[0]["topic"] in ("release", "mcp"))

            m = fleet_control.blackboard_merge([
                {"id": e1["id"], "topic": "release", "summary": "Updated summary", "actor": "remote", "origin": "laptop", "updated_at": e1["updated_at"] + 10},
                {"id": "bb_remote_1", "topic": "sync", "summary": "Remote note", "actor": "remote", "origin": "laptop", "updated_at": e1["updated_at"] + 5},
            ])
            check("Blackboard CRDT merge updates newer and adds remote", m["merged"] == 2 and m["total"] == 3, str(m))
            updated = next(x for x in fleet_control.blackboard_read(limit=10) if x["id"] == e1["id"])
            check("Blackboard newest updated_at wins", updated["summary"] == "Updated summary")

            # 4. memory_layers.render() injects Fleet Blackboard + recent chats
            rendered = memory_layers.render({
                "facts": [],
                "past": [],
                "fleet": {
                    "blackboard": [updated],
                    "recent_chats": [{"session_id": "abcd1234", "title": "Release Chat", "source": "desktop", "last_request": "ship it"}],
                },
            })
            check("memory_layers.render includes Active Fleet Blackboard", "Active Fleet Blackboard" in rendered and "Updated summary" in rendered)
            check("memory_layers.render includes Recent chat lanes", "Recent chat lanes" in rendered and "Release Chat" in rendered)
        finally:
            fleet_control.set_mode(orig_mode)

# 5. Harness surface, Agent tools, Control Center, and FastAPI routes
harness_names = {t["name"] for t in harness.TOOL_SURFACE}
for tname in ("chat_list", "chat_read", "chat_handoff", "chat_steer", "blackboard_read", "blackboard_post", "fleet_policy_read"):
    check(f"Harness tool registered: {tname}", tname in harness_names)

from app.agent.tools import TOOL_DEFINITIONS
agent_names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
for tname in ("fleet_chat_list", "fleet_chat_read", "fleet_chat_handoff", "fleet_chat_steer", "fleet_blackboard_read", "fleet_blackboard_post"):
    check(f"Agent tool registered: {tname}", tname in agent_names)

snap = control_center.snapshot()
check("Control Center snapshot includes fleet block", isinstance(snap.get("fleet"), dict) and "auto_approve_mode" in snap["fleet"])

from app.main import app as fastapi_app
routes = {r.path for r in fastapi_app.routes}
for ep in ("/api/fleet/status", "/api/fleet/policy", "/api/fleet/chats", "/api/fleet/handoff", "/api/fleet/blackboard"):
    check(f"FastAPI route registered: {ep}", ep in routes)

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
