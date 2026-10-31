"""
Brain integration seams (the tree/brain-integrated merge of lanes 1-3).

  Seam A: missions' usage section reads real numbers from Lane 1's ledger.
  usage_ledger.mission_totals sums exactly the records a mission caused:
  its planning calls (context "mission:<id>", stamped by the runner) and its
  subtasks' execution calls (context "task:<task id>", stamped by the queue),
  resolved through mission_subtasks. Nothing else is counted, and an unknown
  mission answers {} so the report keeps saying "not tracked".

  Seam B: Auto's send-time pick ranks the active key's models. pick_for
  fetches the list with the active profile's key; its minute cache is keyed
  by the active profile too, so switching keys re-ranks instead of serving
  the previous key's pick.

Everything runs against a throwaway data folder and the file keyring; the
only "keys" are invented strings.

  .venv/bin/python tests/smoke/run_brain_integration_test.py
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-brain-integ-"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_SECRETS_SERVICE": "Xod test keys",
                   "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"),
                   "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


def main():
    from app.paths import DATA_DIR
    from app.services import missions, usage_ledger

    # ---- Seam A: mission_totals over the ledger --------------------------------
    db = Path(DATA_DIR) / "xod.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE mission_subtasks (id TEXT, mission_id TEXT, idx INTEGER, "
                 "title TEXT, instruction TEXT, expected_effects TEXT, status TEXT, task_id TEXT)")
    conn.executemany("INSERT INTO mission_subtasks (id, mission_id, task_id) VALUES (?, ?, ?)",
                     [("s1", "m1", "t1"), ("s2", "m1", "t2"), ("s3", "m2", "tother")])
    conn.commit()
    conn.close()

    def rec(context, pt, ct):
        usage_ledger.append_record("openai", "gpt-4o", key_profile="Default",
                                   usage={"prompt_tokens": pt, "completion_tokens": ct},
                                   context=context)

    rec("mission:m1", 1000, 500)     # planning call: 1500 tokens, cost 0.0075
    rec("task:t1", 2000, 0)          # subtask 1: 2000 tokens, cost 0.005
    rec("task:t2", 0, 1000)          # subtask 2: 1000 tokens, cost 0.01
    rec("task:tother", 9000, 9000)   # another mission's task: not counted
    rec("chat", 9000, 9000)          # unrelated chat: not counted

    totals = usage_ledger.mission_totals("m1")
    check("seam A: mission_totals finds the mission's records", bool(totals))
    check("seam A: only the mission's own calls are summed",
          totals.get("calls") == 3, f"calls={totals.get('calls')}")
    check("seam A: token math across planning + subtask tags",
          totals.get("total_tokens") == 4500, f"total={totals.get('total_tokens')}")
    check("seam A: cost math from the dated price table",
          abs((totals.get("cost_usd") or 0) - 0.0225) < 1e-9, f"cost={totals.get('cost_usd')}")
    check("seam A: unknown mission answers {} (report stays honest)",
          usage_ledger.mission_totals("nope") == {})
    section = missions._usage_section("m1")
    check("seam A: the mission report's usage section is tracked and matches",
          section.get("status") == "tracked" and section.get("calls") == 3
          and section.get("total_tokens") == 4500)

    # ---- Seam B: the pick cache belongs to the active key ----------------------
    from app.services import auto_model, key_profiles

    class FakeLLM:
        def __init__(self):
            self.calls = 0
            self.ids = ["gpt-4o"]

        async def list_models(self, provider_override=None, key_profile_id=None):
            self.calls += 1
            return [{"id": i} for i in self.ids]

    fake = FakeLLM()
    auto_model._pick_cache.clear()
    first = asyncio.run(auto_model.pick_for(fake, "openai"))
    check("seam B: first pick fetched and ranked the active key's list",
          first.get("pick") == "gpt-4o" and fake.calls == 1)
    again = asyncio.run(auto_model.pick_for(fake, "openai"))
    check("seam B: same key inside the window is served from cache",
          again.get("pick") == "gpt-4o" and fake.calls == 1)

    listing = key_profiles.create_profile("openai", "Second", "")
    second_id = next(p["id"] for p in listing["profiles"] if p["name"] == "Second")
    key_profiles.set_active("openai", second_id)
    fake.ids = ["gpt-4o-mini"]
    switched = asyncio.run(auto_model.pick_for(fake, "openai"))
    check("seam B: switching keys re-fetches and re-ranks (no inherited pick)",
          switched.get("pick") == "gpt-4o-mini" and fake.calls == 2,
          f"pick={switched.get('pick')} calls={fake.calls}")

    default_id = next(p["id"] for p in listing["profiles"] if p["name"] == "Default")
    key_profiles.set_active("openai", default_id)
    fake.ids = ["gpt-4o"]
    back = asyncio.run(auto_model.pick_for(fake, "openai"))
    check("seam B: switching back lands on the first key's own cached pick",
          back.get("pick") == "gpt-4o" and fake.calls == 2)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
