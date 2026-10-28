"""
The handoff carried by Xod (services/handoff_dispatch.py), against a stand-in Crew in a throwaway repo and data folder:
  - work left for Muse Code after its last section starts one Muse job in that repo, working in the folder itself
  - nothing to do (Muse wrote last, or nobody names Muse Code) starts nothing
  - the same work never starts twice; a running job is never doubled; new work after a finished job starts a new one
  - a failed job is tried again only after a while, and only RETRIES times
  - repos that aren't git repos are refused; the setting is empty until you add one
  .venv/bin/python tests/smoke/run_handoff_dispatch_test.py
"""
import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-handoff-"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


HEAD = "# AI Handoff\n\n## How to use\n\n- Mention Muse Code here and it isn't work.\n"


def section(author, body, arrow="-> next AI"):
    return f"\n## 2026-10-27 - {author} {arrow}\n\n### Open\n{body}\n"


async def main():
    from app.services import crew, handoff_dispatch as hd

    repo = TMP / "repo"
    (repo / "docs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    hf = repo / "docs" / "AI_HANDOFF.md"
    started, jobs = [], {}

    async def fake_start(task, folder="", title="", approval_mode=None, agent="", isolate=None, kind=""):
        jid = f"j{len(started) + 1}"
        started.append({"task": task, "folder": folder, "agent": agent, "isolate": isolate, "title": title, "kind": kind})
        jobs[jid] = {"id": jid, "status": "working"}
        return jobs[jid]

    def fake_job(jid):
        if jid not in jobs:
            raise crew.CrewError("gone")
        return jobs[jid]

    crew.start, crew.job = fake_start, fake_job
    from app.services import agent_usage
    usage = {"out": False, "until": None}
    agent_usage.muse = lambda refresh=False: dict(usage)     # never the real `muse usage` in a test

    print("H1 whose turn it is")
    hf.write_text(HEAD + section("Muse Code (muse-wonder-1.0)", "- none"))
    check("Muse wrote last: nothing to do", await hd.check(str(repo)) is None and not started)
    hf.write_text(hf.read_text() + section("Claude Code (claude-opus-5-5)", "- Lunch is at noon."))
    check("a section that doesn't name Muse Code isn't work for it", await hd.check(str(repo)) is None and not started)
    check("the 'How to use' text doesn't count", hd.work_for_muse(HEAD) == [])

    print("H2 handing it over")
    hf.write_text(hf.read_text() + section("Claude Code (claude-opus-5-5)", "- Muse Code (code): add the QR pairing."))
    j = await hd.check(str(repo))
    check("work for Muse Code starts one Muse job", j is not None and len(started) == 1, str(started))
    s = started[0] if started else {}
    check("…in that repo, in the folder itself", s.get("agent") == "muse" and s.get("folder") == str(repo) and s.get("isolate") is False)
    check("…as a 'handoff' job (so it can get the cheaper model)", s.get("kind") == "handoff")
    check("…told where the new work starts and not to commit or push",
          "add the QR pairing" not in s.get("task", "") and "## 2026-10-27 - Claude Code (claude-opus-5-5) -> next AI" in s.get("task", "")
          and "do not commit, push" in s.get("task", ""), s.get("task", "")[:300])
    check("a running job is never doubled", await hd.check(str(repo)) is None and len(started) == 1)
    jobs["j1"]["status"] = "done"
    check("the same work, finished, never starts again", await hd.check(str(repo)) is None and len(started) == 1)
    hf.write_text(hf.read_text() + section("Claude Code (claude-opus-5-5)", "- Muse Code: one more thing."))
    check("new work after a finished job starts a new one", await hd.check(str(repo)) is not None and len(started) == 2)

    print("H3 failures")
    jobs["j2"]["status"] = "failed"
    check("a failed job isn't retried straight away", await hd.check(str(repo)) is None and len(started) == 2)
    st = hd._load_state()
    st[str(repo)]["at"] -= hd.RETRY_AFTER_SEC + 1
    hd._save_state(st)
    check("…but is after a while", await hd.check(str(repo)) is not None and len(started) == 3)
    for _ in range(5):
        jobs[f"j{len(started)}"]["status"] = "failed"
        st = hd._load_state()
        st[str(repo)]["at"] -= hd.RETRY_AFTER_SEC + 1
        hd._save_state(st)
        await hd.check(str(repo))
    check(f"…and gives up after {hd.RETRIES} retries", len(started) == 1 + 1 + hd.RETRIES, str(len(started)))
    hf.write_text(hf.read_text() + section("Muse Code (muse-wonder-1.0)", "- none"))
    check("once Muse writes back, there's no work left", await hd.check(str(repo)) is None)

    print("H3b out of usage")
    hf.write_text(hf.read_text() + section("Claude Code (claude-opus-5-5)", "- Muse Code: read the texts."))
    j = await hd.check(str(repo))
    n0 = len(started)
    real_err = ("API error 429 [request_id=b4ebed53]: Subscription quota exhausted. "
                "Your usage window resets at 2099-10-28T04:28:14Z. (rate_limit_error)")
    jobs[j["id"]].update(status="failed", error=real_err)
    check("a usage-limit error is read, with its reset time", hd.quota_reset(real_err) and hd.quota_reset(real_err) > 4000000000)
    check("…and other errors aren't", hd.quota_reset("boom: file not found") is None)
    await hd.check(str(repo))
    st = hd._load_state()[str(repo)]
    check("out of usage: no new job, waits until the reset", len(started) == n0 and st.get("wait_until", 0) > 4000000000, str(st))
    st["at"] -= hd.RETRY_AFTER_SEC + 1
    hd._save_state({**hd._load_state(), str(repo): st})
    check("…not even after the usual retry delay", await hd.check(str(repo)) is None and len(started) == n0)
    tries_before = st["tries"]
    st["wait_until"] = 0
    hd._save_state({**hd._load_state(), str(repo): st})
    j2 = await hd.check(str(repo))
    check("after the reset: the same work starts again", j2 is not None and len(started) == n0 + 1)
    check("…and the wait didn't use up a retry", hd._load_state()[str(repo)]["tries"] == tries_before, str(hd._load_state()[str(repo)]))
    check("a usage limit with no reset time waits a while", abs(hd.quota_reset("429 Too Many Requests") - (__import__("time").time() + hd.QUOTA_WAIT_SEC)) < 5)
    jobs[j2["id"]]["status"] = "done"
    hf.write_text(hf.read_text() + section("Muse Code (muse-wonder-1.0)", "- none"))

    print("H3c usage checked before starting")
    hf.write_text(hf.read_text() + section("Claude Code (claude-opus-5-5)", "- Muse Code: the clipboard."))
    n0 = len(started)
    usage.update(out=True, until=4102444800.0)
    check("Muse at 100%: no job starts", await hd.check(str(repo)) is None and len(started) == n0)
    check("…and it waits for the reset Muse reported", hd._load_state()[str(repo)].get("wait_until") == 4102444800.0)
    usage.update(out=False, until=None)
    st = hd._load_state(); st[str(repo)]["wait_until"] = 0; hd._save_state(st)
    j3 = await hd.check(str(repo))
    check("usage back: the job starts", j3 is not None and len(started) == n0 + 1)
    jobs[j3["id"]]["status"] = "done"
    hf.write_text(hf.read_text() + section("Muse Code (muse-wonder-1.0)", "- none"))

    print("H3d only a local handoff file")
    hf.write_text(hf.read_text() + section("Claude Code (claude-opus-5-5)", "- Muse Code: from a pull."))
    n0 = len(started)
    subprocess.run(["git", "-C", str(repo), "add", "-f", "docs/AI_HANDOFF.md"], check=True)
    check("the file tracked by git: nothing starts", await hd.check(str(repo)) is None and len(started) == n0)
    check("…and it's noted once", hd._load_state()[str(repo)].get("refused_tracked") is True)
    subprocess.run(["git", "-C", str(repo), "rm", "-q", "--cached", "docs/AI_HANDOFF.md"], check=True)
    j4 = await hd.check(str(repo))
    check("untracked again: the work is handed over", j4 is not None and len(started) == n0 + 1)
    jobs[j4["id"]]["status"] = "done"
    hf.write_text(hf.read_text() + section("Muse Code (muse-wonder-1.0)", "- none"))
    check("a folder git can't read counts as not safe", hd.tracked_by_git(str(TMP / "nowhere")) is True)

    print("H4 the setting")
    check("no repos until you add one", hd.repos() == [])
    hd.start = lambda: None
    st = hd.set_repos([str(TMP / "not-a-repo"), str(repo), str(repo)])
    check("only git repos are kept, once each", st["repos"] == [str(repo.resolve())], str(st["repos"]))

    print("H5 the real handoff file reads right (read-only)")
    real = ROOT / "docs" / "AI_HANDOFF.md"
    if real.exists():
        secs = hd.sections(real.read_text(encoding="utf-8"))
        check("every dated section has an author", secs and all(s["author"] for s in secs), str([s["heading"] for s in secs if not s["author"]][:3]))
    else:
        print("  (no docs/AI_HANDOFF.md here, skipped)")


if __name__ == "__main__":
    asyncio.run(main())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)
