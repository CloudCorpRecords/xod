"""
Self-editing gate.

  Q1 tiers: small -> red, Gemma 12B -> yellow, Claude Opus/Fable, GPT-5, "astra", strong local coders -> green
  Q2 policy: protected files, protected safety code, approval-flow text, paths outside app/tests are refused
  Q3 staged checks catch a broken change and pass a good one
  Q4 green: change applied when idle, then reverted (stub editor, throwaway file)
  Q5 yellow: the real local model writes a proposal; nothing changes until approved; reject leaves code as-is
  Q6 red: refused
Uses its own settings/history folder.
"""
import asyncio, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys as _sys; _sys.path.insert(0, str(ROOT)); from app.paths import DATA_DIR as _DATA  # noqa: E402  (outside the repo)
RUN = _DATA / "test_runs" / "self_edit"
RUN.mkdir(parents=True, exist_ok=True)
os.environ["XOD_DATASET_DB"] = str(RUN / "dataset.db")
sys.path.insert(0, str(ROOT))

from app.services import self_edit as se  # noqa: E402
se.WORK_DIR = RUN / "work"
se.SETTINGS_PATH = se.WORK_DIR / "settings.json"
se.HISTORY_PATH = se.WORK_DIR / "history.json"
import shutil  # noqa: E402
shutil.rmtree(se.WORK_DIR, ignore_errors=True)

results = []


def report(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def test_tiers():
    cases = [("lm_studio", "google/gemma-4-12b-qat", "yellow"), ("specialist", "research_planner", "red"),
             ("lm_studio", "mlx-community/Qwen2.5-1.5B-Instruct-4bit", "red"), ("anthropic", "claude-opus-5-5", "green"),
             ("anthropic", "claude-fable-5-1", "green"), ("openai", "gpt-5", "green"), ("openai", "astra-1", "green"),
             ("lm_studio", "qwen/qwen3-coder-30b", "green"), ("lm_studio", "llama-3.2-3b-instruct", "red"),
             ("openrouter", "some/unknown-model", "yellow")]
    for p, m, want in cases:
        got = se.tier_for(p, m)["tier"]
        report(f"Q1 tier {p}:{m}", got == want, f"→ {got}")
    se.save_settings({"tier_overrides": {"lm_studio:google/gemma-4-12b-qat": "green"}})
    report("Q1 override wins", se.tier_for("lm_studio", "google/gemma-4-12b-qat")["tier"] == "green")
    se.save_settings({"tier_overrides": {}})


def test_policy():
    jw = (ROOT / "app/services/job_watch.py").read_text()
    sb = (ROOT / "app/services/browser_sandbox.py").read_text()
    cases = [
        ("secret store", [{"path": "app/services/secret_store.py", "find": "SERVICE =", "replace": "SERVICE  ="}]),
        ("self-edit gate", [{"path": "app/services/self_edit.py", "find": "MAX_FILES = 6", "replace": "MAX_FILES = 60"}]),
        ("submit gate code", [{"path": "app/services/browser_sandbox.py", "find": "def _looks_like_final_submit(label: str, element: Optional[Dict[str, Any]]) -> bool:",
                               "replace": "def _looks_like_final_submit(label: str, element: Optional[Dict[str, Any]]) -> bool:\n    return False"}]),
        ("approval flow text", [{"path": "app/services/browser_sandbox.py", "find": "pending_submit", "replace": "pending_x"}]
         if sb.count("pending_submit") == 1 else
         [{"path": "app/services/browser_sandbox.py", "find": sb[sb.index("self.pending_submit"):sb.index("self.pending_submit") + 40],
           "replace": sb[sb.index("self.pending_submit"):sb.index("self.pending_submit") + 40].replace("pending_submit", "pending_x")}]),
        ("outside app/tests", [{"path": "data/profile.json", "create": "{}"}]),
        ("path escape", [{"path": "app/../run.sh", "find": "uvicorn", "replace": "x"}]),
    ]
    for name, edits in cases:
        r = se.build_change(edits)
        report(f"Q2 refuses {name}", "error" in r, r.get("error", "ALLOWED")[:110])
    ok = se.build_change([{"path": "app/services/job_watch.py", "find": "MAX_WAIT_SEC = 600", "replace": "MAX_WAIT_SEC = 900"}])
    report("Q2 allows a normal change", "files" in ok and ok["changed_lines"] == 2, ok.get("error", ""))


def test_checks():
    bad = se.build_change([{"path": "app/services/job_watch.py", "find": "MAX_WAIT_SEC = 600", "replace": "MAX_WAIT_SEC = = 600"}])
    r1 = se.run_checks(bad, "test-bad")
    report("Q3 broken change fails checks", not r1["ok"], next((c["check"] for c in r1["results"] if not c["ok"]), ""))
    good = se.build_change([{"path": "app/services/job_watch.py", "find": "MAX_WAIT_SEC = 600", "replace": "MAX_WAIT_SEC = 900"}])
    t = time.time()
    r2 = se.run_checks(good, "test-good")
    report("Q3 good change passes checks", r2["ok"], f"{time.time()-t:.0f}s: " + ", ".join(c["check"] for c in r2["results"]))
    report("Q3 real code untouched by checks", "MAX_WAIT_SEC = 600" in (ROOT / "app/services/job_watch.py").read_text())


async def test_green_apply_revert():
    probe = ROOT / "app/static/selfedit_probe.txt"
    probe.unlink(missing_ok=True)

    async def stub(ed, messages):
        return {"summary": "Add a probe file", "edits": [{"path": "app/static/selfedit_probe.txt", "create": "hello from a self-edit\n"}]}
    real = se._ask_editor
    se._ask_editor = stub
    se.save_settings({"editor": {"provider": "anthropic", "model": "claude-opus-5-5"}})
    from app.config import settings
    had_key = settings.anthropic_api_key
    settings.anthropic_api_key = "test-key-not-used"   # the stub never calls the API
    try:
        r = await se.request_change("add a probe file", requested_by="test")
        for _ in range(60):
            c = se.get_change(r.get("change_id", ""))
            if c and c["status"] in ("applied", "failed"):
                break
            await asyncio.sleep(1)
        applied = probe.exists() and c and c["status"] == "applied"
        report("Q4 green change applied when idle", applied, f"{r.get('status')} → {c and c['status']}")
        rv = se.revert(r["change_id"])
        report("Q4 revert puts the code back", rv["ok"] and not probe.exists() and se.get_change(r["change_id"])["status"] == "reverted", rv["detail"])
    finally:
        se._ask_editor = real
        settings.anthropic_api_key = had_key
        se.save_settings({"editor": None})
        probe.unlink(missing_ok=True)


async def test_yellow_real_model():
    before = (ROOT / "app/services/job_watch.py").read_text()
    t = time.time()
    r = await se.request_change("In app/services/job_watch.py, change PROGRESS_EVERY_SEC from 15 to 20 seconds.",
                                ["app/services/job_watch.py"], requested_by="test")
    after = (ROOT / "app/services/job_watch.py").read_text()
    c = se.get_change(r.get("change_id", "")) or {}
    good_diff = "+PROGRESS_EVERY_SEC = 20" in c.get("diff", "")
    report("Q5 local model writes a checked proposal", r.get("success") and r.get("status") == "proposed" and good_diff,
           f"{time.time()-t:.0f}s {r.get('model')} {r.get('status')} {r.get('error', '')[:150]}")
    report("Q5 nothing changed before approval", before == after)
    if r.get("success"):
        rj = se.reject(r["change_id"])
        report("Q5 reject leaves code as-is", rj["ok"] and (ROOT / "app/services/job_watch.py").read_text() == before)


async def test_red():
    from app.config import settings      # the model in use here, whichever it is (LM Studio, Ollama…), marked too weak
    se.save_settings({"tier_overrides": {f"{settings.llm_provider}:{settings.llm_model}": "red",
                                         "lm_studio:google/gemma-4-12b-qat": "red"}})
    r = await se.request_change("anything")
    se.save_settings({"tier_overrides": {}})
    report("Q6 red model refused", not r["success"] and r.get("tier") == "red", r.get("error", "")[:100])


async def main():
    test_tiers()
    test_policy()
    test_checks()
    await test_green_apply_revert()
    await test_red()
    await test_yellow_real_model()
    print("SUMMARY", "PASS" if all(ok for _, ok in results) else "FAIL", [n for n, ok in results if not ok], flush=True)

asyncio.run(main())
