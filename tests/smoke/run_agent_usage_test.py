"""
Agent usage and model choice (services/agent_usage.py, crew.models / pick_model, muse_native modelId), with no real Muse:
  - `muse usage` text is read into percentages and reset times; a full window means out, with the reset time
  - the model a Muse job gets: default, the cheaper one for chosen kinds of job, or once the weekly allowance passes N%
  - bad model ids are refused; the chosen model reaches Muse as session/start's modelId
  .venv/bin/python tests/smoke/run_agent_usage_test.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-usage-"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


REAL = "Tier: Muse Code High Usage\nWindow: 100% used, resets in 1h 24m\nWeekly: 85% used, resets in 4d 20h\n"


async def main():
    from app.services import agent_usage, crew
    from app.services.muse_native import MuseHost

    print("U1 reading `muse usage`")
    u = agent_usage.parse(REAL, now=1000.0)
    check("tier, window and weekly are read", u["tier"] == "Muse Code High Usage" and u["window"]["used"] == 100 and u["weekly"]["used"] == 85, str(u))
    check("…with reset times (1h 24m, 4d 20h)", u["window"]["resets_at"] == 1000 + 5040 and u["weekly"]["resets_at"] == 1000 + 4 * 86400 + 20 * 3600)
    check("a full window means out", u["out"] is True)
    ok = agent_usage.parse("Window: 12% used, resets in 45m\nWeekly: 30% used, resets in 2d", now=0)
    check("minutes-only and days-only resets; not out", ok["window"]["resets_at"] == 2700 and ok["weekly"]["resets_at"] == 172800 and ok["out"] is False)
    check("text it doesn't recognise: nothing made up", agent_usage.parse("hello") == {"tier": None, "window": None, "weekly": None, "out": False})
    check("out until the later full reset", agent_usage._until(agent_usage.parse(REAL, now=0)) == 5040)

    print("U2 which model a Muse job gets")
    weekly = {"used": 50.0}
    agent_usage.muse = lambda refresh=False: {"weekly": dict(weekly)}
    check("nothing set: Muse's own default", crew.pick_model("handoff") == ("", ""))
    crew.set_models(default="muse-wonder-1.0", cheap="muse-spark-1.3", cheap_for=["handoff"])
    check("handed-over work gets the cheaper model", crew.pick_model("handoff")[0] == "muse-spark-1.3")
    check("other jobs get the default", crew.pick_model("")[0] == "muse-wonder-1.0")
    crew.set_models(cheap_above_weekly=80)
    check("weekly below the line: default", crew.pick_model("")[0] == "muse-wonder-1.0")
    weekly["used"] = 85.0
    m, why = crew.pick_model("")
    check("weekly at 85% with an 80% line: the cheaper model, and says why", m == "muse-spark-1.3" and "85%" in why, why)
    crew.set_models(cheap_above_weekly=None)
    check("the weekly rule can be switched off", crew.pick_model("")[0] == "muse-wonder-1.0")
    try:
        crew.set_models(cheap="spark; rm -rf /")
        refused = False
    except crew.CrewError:
        refused = True
    check("a model id that isn't one is refused", refused)

    print("U3 the model reaches Muse")
    sent = {}
    h = MuseHost("muse", str(TMP))

    async def fake_command(method, params, timeout=120):
        sent[method] = params
        return {"session": {"sessionId": "s1"}}
    h.command = fake_command
    await h.start_session(str(TMP), model="muse-spark-1.3")
    check("session/start carries modelId", sent["session/start"].get("modelId") == "muse-spark-1.3", str(sent))
    await h.start_session(str(TMP))
    check("…and leaves it out when none is chosen (Muse's default)", "modelId" not in sent["session/start"])


if __name__ == "__main__":
    asyncio.run(main())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)
