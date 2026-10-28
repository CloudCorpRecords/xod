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

    async def fake_start(task, folder="", title="", approval_mode=None, agent="", isolate=None):
        jid = f"j{len(started) + 1}"
        started.append({"task": task, "folder": folder, "agent": agent, "isolate": isolate, "title": title})
        jobs[jid] = {"id": jid, "status": "working"}
        return jobs[jid]

    def fake_job(jid):
        if jid not in jobs:
            raise crew.CrewError("gone")
        return jobs[jid]

    crew.start, crew.job = fake_start, fake_job

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
