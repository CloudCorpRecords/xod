"""Fast checks a staged self-edit must pass (run by self_edit.run_checks from the ORIGINAL test files).
No network, no model calls, no browser: core modules import and their basic behaviour holds."""
import asyncio, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def main():
    import app.main  # noqa: F401  (the whole app imports)
    from app.agent import healing
    from app.agent.researcher import AgenticResearcher
    from app.agent.evidence import EvidenceBoard
    from app.agent.tools import TOOL_DEFINITIONS, execute_tool
    from app.services import self_edit
    from app.services.browser_sandbox import _looks_like_final_submit

    names = [t["function"]["name"] for t in TOOL_DEFINITIONS]
    check("tool names unique", len(names) == len(set(names)))
    check("submit gate still guards Submit", _looks_like_final_submit("Submit application", {"tag": "button"}))
    check("side-effect tools still protected", "computer_run_command" in healing.SIDE_EFFECT_TOOLS and "browser_click" in healing.SIDE_EFFECT_TOOLS)
    check("result checks", not healing.check_result("computer_run_command", {}, {"exit_code": 1})["ok"]
          and healing.check_result("web_search", {}, {"results": [1]})["ok"])
    check("classify", healing.classify("No such file or directory")["kind"] == "not_found")
    b = EvidenceBoard("smoke")
    b.update_from_tool("computer_check_job", {"job_id": "j"}, {"success": True, "status": "completed", "new_output": ["[out] ok"]})
    check("evidence keeps tool results", "[out] ok" in b.to_markdown_summary())
    r = AgenticResearcher()
    check("planner arg normalising", r._normalize_tool_args([{"name": "a", "val": 1}], 1) == [{"a": 1}])
    check("self-edit gate tiers", self_edit.tier_for("lm_studio", "qwen2.5-1.5b")["tier"] == "red"
          and self_edit.tier_for("anthropic", "claude-opus-5-5")["tier"] == "green")
    res = asyncio.run(execute_tool("computer_check_job", {"job_id": "nope"}))
    check("tool dispatch", res.get("success") is False)


try:
    main()
except Exception as e:
    import traceback
    traceback.print_exc()
    check("smoke test ran", False, str(e))
print("SUMMARY", "PASS" if all(ok for _, ok in results) else "FAIL", [n for n, ok in results if not ok])
sys.exit(0 if all(ok for _, ok in results) else 1)
