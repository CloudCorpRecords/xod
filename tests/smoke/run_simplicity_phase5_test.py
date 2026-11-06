"""
Phase 5 of the simplicity redesign (silent self-maintenance):
  - update policy defaults to automatic (config-level), and off is respected
  - the Settings - Updates row reads in plain words with no required action;
    the switch and Check now sit one layer down
  - a stubbed available update installs with NO prompt: a Finished result
    card in Work + a History entry, and no Needs-you card
  - a stubbed repairable health fault is repaired silently: outcome in
    History, no Needs-you card
  - a stubbed unrepairable fault produces exactly ONE calm Needs-you card
    (deduped), with Fix it wired where a manual path exists
  - the Phase 3 post office in its paused state routes to the same
    Needs-you treatment; a version change across a restart is reported as
    "Xod updated itself last night." exactly once
Stubbed update source + stubbed doctors + a throwaway XOD_DATA_DIR; no
server, no real updater, nothing real is updated or restarted.
  python tests/smoke/run_simplicity_phase5_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="xod-phase5-"))
os.environ["XOD_DATA_DIR"] = str(TMP)
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.pop("XOD_NO_UPDATE_CHECK", None)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def tasks_where(**want):
    from app.services.task_queue import task_queue
    import sqlite3
    from app.paths import DATA_DIR
    c = sqlite3.connect(str(DATA_DIR / "xod.db"))
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute("SELECT * FROM tasks").fetchall()]
    c.close()
    out = []
    for r in rows:
        if all(r.get(k) == v for k, v in want.items()):
            t = task_queue.get(r["id"])
            out.append(t)
    return out


class FakeDoctor:
    """A doctor whose one failing check is repaired by its existing fix."""

    def __init__(self):
        self.fixed = False
        self.fix_calls = []

    async def check(self):
        if self.fixed:
            checks = [{"name": "browser", "label": "Browser", "level": "ok", "ok": True,
                       "detail": "Xod's browser is running", "fix": None, "fix_label": None}]
        else:
            checks = [{"name": "browser", "label": "Browser", "level": "fail", "ok": False,
                       "detail": "Xod's browser stopped responding.", "fix": "restart_browser",
                       "fix_label": "Restart it"}]
        return {"overall": "ok" if self.fixed else "fail", "checks": checks,
                "recent_fixes": [], "recent_failures": []}

    async def fix(self, fix_id):
        self.fix_calls.append(fix_id)
        self.fixed = True
        return {"ok": True, "detail": "Restarted Xod's browser"}


class StubbornDoctor:
    """A doctor whose fix exists but genuinely does not repair the fault,
    plus a fault with no fix of Xod's own at all."""

    async def check(self):
        return {"overall": "fail", "checks": [
            {"name": "chat_model", "label": "Chat model", "level": "fail", "ok": False,
             "detail": "No model loaded.", "fix": "load_chat_model", "fix_label": "Load it"},
            {"name": "disk", "label": "Disk space", "level": "fail", "ok": False,
             "detail": "Only 2 GB free.", "fix": None, "fix_label": None},
            {"name": "keys", "label": "API keys", "level": "warn", "ok": False,
             "detail": "No web search key.", "fix": None, "fix_label": None},
        ], "recent_fixes": [], "recent_failures": []}

    async def fix(self, fix_id):
        return {"ok": False, "detail": "Couldn't load the chat model"}


def main():
    print("H1 the update policy defaults to automatic, and off is a real off")
    from app.services import updates
    check("policy defaults to automatic (nothing stored)", updates.policy() == "automatic", updates.policy())
    check("automatic means enabled and auto", updates.enabled() and updates.auto_enabled())
    st = asyncio.run(updates.status())
    check("status reports the policy in plain fields",
          st.get("policy") == "automatic" and st.get("automatic") is True and "last_updated" in st, str(st))
    updates.set_policy("off")
    check("policy off sticks", updates.policy() == "off" and not updates.enabled() and not updates.auto_enabled())
    updates.set_enabled(True)
    check("the old on/off switch maps back to automatic", updates.policy() == "automatic")
    updates.set_enabled(False)
    check("the old switch off maps to policy off", updates.policy() == "off")
    updates.set_policy("automatic")

    print("H2 the Settings - Updates row is plain words, no required action")
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    upd_js = (ROOT / "app" / "static" / "js" / "updates.js").read_text(encoding="utf-8")
    electron = (ROOT / "electron" / "updater.js").read_text(encoding="utf-8")
    check("the row has a plain policy line", 'id="updatesPolicyLine"' in html and "Automatic" in html)
    check("the switch and Check now are one layer down (inside details)",
          'id="autoUpdateToggle"' in html and "Update settings" in html and "Check now" in html
          and html.index("Update settings") > html.index('id="xodVersion"'))
    check("the line reads 'Automatic - last updated <weekday>.'",
          "Automatic — last updated" in upd_js and "Automatic — up to date." in upd_js)
    check("automatic mode shows no install button in the pill",
          "it installs itself" in upd_js and "No action needed" in upd_js)
    check("the home-screen banner never shows in automatic mode",
          "!automatic() && st" in upd_js)
    check("the desktop updater knows the policy and does not prompt in automatic mode",
          "autoPolicy" in electron and "installs itself when Xod quits" in electron)

    print("H3 a stubbed available update installs itself, no prompt")
    from app.services import self_maintenance
    calls = {"status": 0, "install": 0}

    async def stub_status(force=False):
        calls["status"] += 1
        return {"current": "0.9.9", "latest": "9.9.9", "available": True, "enabled": True}

    async def stub_install(st):
        calls["install"] += 1

    res = asyncio.run(self_maintenance.run_update_cycle(stub_status, stub_install))
    check("the cycle updated", res.get("state") == "updated", str(res))
    check("the stub installer ran exactly once, with no human in the loop",
          calls == {"status": 1, "install": 1}, str(calls))
    done = tasks_where(status="done", title="Xod updated itself last night.")
    check("a Finished result card carries the outcome sentence",
          len(done) == 1 and done[0]["result"] == "Xod updated itself last night.", str(len(done)))
    check("the card is real work, not scratch (origin rules)",
          bool(done) and done[0]["origin"] == "self" and done[0]["scratch"] is False,
          str(done[0] if done else None))
    check("no Needs-you card was created for a successful update",
          len(tasks_where(status="waiting")) == 0)
    check("the last-updated stamp was saved", updates.last_updated().get("version") == "9.9.9",
          str(updates.last_updated()))
    from app.services import activity
    hist = [a for a in activity.recent(50) if a["title"] == "Xod updated itself last night."]
    check("History holds the outcome with the technical record in its detail",
          len(hist) == 1 and "9.9.9" in hist[0]["detail"], str(hist[:1]))

    print("H4 off is respected: no check, no install, no card")
    updates.set_policy("off")
    before = len(tasks_where(status="done", title="Xod updated itself last night."))
    res = asyncio.run(self_maintenance.run_update_cycle(stub_status, stub_install))
    check("the cycle reports off and does nothing",
          res.get("state") == "off" and calls == {"status": 1, "install": 1}, str((res, calls)))
    check("no new result card while off",
          len(tasks_where(status="done", title="Xod updated itself last night.")) == before)
    updates.set_policy("automatic")

    print("H5 a repairable health fault is repaired silently")
    fake = FakeDoctor()
    res = asyncio.run(self_maintenance.run_health_cycle(fake, mail_status_fn=lambda: {"home": None}))
    check("the existing fix ran", fake.fix_calls == ["restart_browser"], str(fake.fix_calls))
    check("the cycle reports it repaired", res.get("repaired") == ["browser"], str(res))
    check("no Needs-you card for a repaired fault", len(tasks_where(status="waiting")) == 0)
    fixed_cards = tasks_where(status="done", title="Xod fixed its browser on its own.")
    check("a Finished card reports the fix as an outcome", len(fixed_cards) == 1, str(len(fixed_cards)))
    hist = [a for a in activity.recent(50) if a["title"] == "Xod fixed its browser on its own."]
    check("History holds the fix with the technical record in its detail",
          len(hist) == 1 and "Restarted Xod's browser" in hist[0]["detail"], str(hist[:1]))

    print("H6 an unrepairable fault: exactly one calm Needs-you card")
    stubborn = StubbornDoctor()
    asyncio.run(self_maintenance.run_health_cycle(stubborn, mail_status_fn=lambda: {"home": None}))
    asyncio.run(self_maintenance.run_health_cycle(stubborn, mail_status_fn=lambda: {"home": None}))
    waiting = tasks_where(status="waiting")
    check("exactly two cards wait (model + disk), warnings never surface",
          len(waiting) == 2, str([(t["title"], t["waiting_kind"]) for t in waiting]))
    model = next((t for t in waiting if t["title"] == "Xod's chat model needs a hand."), None)
    check("the model card is one calm sentence, maintenance kind",
          bool(model) and model["waiting_kind"] == "maintenance", str(model))
    check("its Fix it names the existing manual path",
          bool(model) and (model["waiting_detail"] or {}).get("fix") == "doctor:load_chat_model",
          str(model["waiting_detail"] if model else None))
    check("the technical record sits in the card's Details, not the headline",
          bool(model) and "Xod tried its own repair" in (model["waiting_detail"] or {}).get("detail", ""))
    disk = next((t for t in waiting if "disk space" in t["title"]), None)
    check("a fault with no repair of Xod's own gets a card but no invented Fix it",
          bool(disk) and (disk["waiting_detail"] or {}).get("fix") is None, str(disk))
    check("nothing unrecovered landed in Finished",
          len(tasks_where(status="done", title="Xod's chat model needs a hand.")) == 0)
    ok = self_maintenance.resolve_needs_you("health:chat_model", "Xod fixed its chat model on its own.")
    after = tasks_where(status="done", title="Xod's chat model needs a hand.")
    check("when it later heals, the card completes instead of stacking",
          ok and len(after) == 1 and after[0]["result"] == "Xod fixed its chat model on its own.",
          str((ok, len(after))))

    print("H7 messages paused routes to the same Needs-you treatment")
    paused = lambda: {"home": {"paused": True, "running": False, "last_error": "Port 8790 is in use"}}
    res = asyncio.run(self_maintenance.run_health_cycle(FakeDoctor(), mail_status_fn=paused))
    res = asyncio.run(self_maintenance.run_health_cycle(FakeDoctor(), mail_status_fn=paused))
    msg = tasks_where(status="waiting", title="Messages are paused.")
    check("paused messages surface once, calmly, with the existing repair as Fix it",
          len(msg) == 1 and (msg[0]["waiting_detail"] or {}).get("fix") == "mail:repair",
          str([(t["title"], t["waiting_detail"]) for t in msg]))

    print("H8 a version change across a restart is reported exactly once")
    n0 = len(tasks_where(status="done", title="Xod updated itself last night."))
    first = self_maintenance.note_update_if_changed("1.2.3")
    check("the first-ever run only stamps the version (no invented story)",
          first is None and len(tasks_where(status="done", title="Xod updated itself last night.")) == n0)
    from app.services.storage import set_setting
    set_setting("app_version_seen", "1.2.2")
    rid = self_maintenance.note_update_if_changed("1.2.3")
    check("a real version change reports the outcome card",
          bool(rid) and len(tasks_where(status="done", title="Xod updated itself last night.")) == n0 + 1)
    again = self_maintenance.note_update_if_changed("1.2.3")
    check("and only once", again is None
          and len(tasks_where(status="done", title="Xod updated itself last night.")) == n0 + 1)

    print("H9 the surface keeps its promises (source checks)")
    main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    tasks_js = (ROOT / "app" / "static" / "js" / "tasks.js").read_text(encoding="utf-8")
    sm_py = (ROOT / "app" / "services" / "self_maintenance.py").read_text(encoding="utf-8")
    check("the policy endpoint and the maintenance Fix it endpoint exist",
          "/api/update/policy" in main_py and "/api/maintenance/fix" in main_py)
    check("the background loop starts with the app", "self_maintenance.start()" in main_py)
    check("Needs-you maintenance cards render one sentence + Fix it",
          'waiting_kind === "maintenance"' in tasks_js and "fixMaintenance" in tasks_js and "Fix it" in tasks_js)
    check("self-repair only routes through repairs that already exist",
          "doctor_obj.fix" in sm_py and "repair_home_post" in sm_py and "restart_browser" not in sm_py)

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)} checks passed"
          + (f"  FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
