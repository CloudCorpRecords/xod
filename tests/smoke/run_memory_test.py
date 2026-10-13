"""
What Xod knows about you (services/user_memory.py), with the local model: it picks up facts you say about
yourself (and ignores questions and requests), "remember that ..." and "forget ..." work, secrets are never kept,
a newer fact replaces an older one, the facts reach the router so "what's my name?" is answered directly, and
"Get to know me from this Mac" proposes facts (from a pretend home folder) without keeping anything until asked.

Throwaway data folder and a pretend home folder; uses LM Studio's model, never a cloud one:
  .venv/bin/python tests/smoke/run_memory_test.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-memory-")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"          # never your real Keychain items
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
FAKE_HOME = Path(tempfile.mkdtemp(prefix="xod-home-"))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


async def main():
    from app.config import settings
    settings.llm_provider, settings.llm_base_url, settings.llm_model = "lm_studio", "http://127.0.0.1:1234/v1", "google/gemma-4-12b-qat"
    from app.services import user_memory as M

    check("a question isn't something to learn from", not M.worth_a_look("what's my name?"))
    check("…nor a plain request", not M.worth_a_look("find me remote python jobs in Europe"))
    check("talking about yourself is", M.worth_a_look("I'm a nurse in Austin and my dog is called Biscuit"))

    r = await M.learn_from_message("Hey, I'm Rosa. I work as a nurse at St. David's in Austin and my dog is called Biscuit.")
    texts = " | ".join(f["text"] for f in M.facts())
    check("it picks up name, work, place and pet", all(w in texts for w in ("Rosa", "nurse", "Austin", "Biscuit")), texts)
    check("…and says what it learned", len(r["learned"]) >= 3, str(r))
    check("the name is filed as the name", any(f.get("key") == "name" and "Rosa" in f["text"] for f in M.facts()), texts)

    await M.learn_from_message("Actually I just moved to Denver last month.")
    cities = [f["text"] for f in M.facts() if f.get("key") == "city" or "Denver" in f["text"] or "Austin" in f["text"]]
    check("a newer fact replaces the older one", any("Denver" in c for c in cities) and not any("Lives in Austin" == c for c in cities), str(cities))

    await M.learn_from_message("remember that I prefer short answers")
    check("'remember that …' keeps it", any("short" in f["text"].lower() for f in M.facts()))

    r = await M.learn_from_message("my password for the clinic portal is hunter2-Blue, my card is 4111 1111 1111 1111")
    check("passwords and card numbers are never kept", not any("hunter2" in f["text"] or "4111" in f["text"] for f in M.facts()),
          str(M.facts()))
    check("…even when added by hand", M.add("API key is fw_abcdefghijklmnop1234") is None)

    r = await M.learn_from_message("forget about my dog")
    check("'forget …' removes it", not any("Biscuit" in f["text"] for f in M.facts()) and r["forgot"], str(r))

    note = M.core_note()
    check("the always-on note leads with the name", note.splitlines()[1].startswith("- Name is Rosa"), note[:300])
    check("…and stays the same between calls (prompt cache)", note == M.core_note())

    from app.agent.researcher import AgenticResearcher
    msgs = AgenticResearcher.router_messages("what's my name?", [])
    check("the router sees it", "Rosa" in msgs[0]["content"])
    out = ""
    async for ev in AgenticResearcher().run_research_stream([{"role": "user", "content": "what's my name?"}]):
        if ev.get("type") == "content":
            out += ev["content"]
    check("\"what's my name?\" is answered from memory", "Rosa" in out, out[:200])

    from app.services import soul
    check("the companion calls you by it too", soul.load().get("name") == "Rosa")

    # From this Mac: a pretend home with a git project; locale and git are read for real but not printed.
    proj = FAKE_HOME / "Developer" / "tidepool"
    (proj / ".git").mkdir(parents=True)
    (proj / "README.md").write_text("# Tidepool\n\nA tiny iOS app that tracks local tide times for surfers.\n")
    real_home = os.environ.get("HOME")
    os.environ["HOME"] = str(FAKE_HOME)
    try:
        found = M._projects()
    finally:
        os.environ["HOME"] = real_home
    check("projects: a git project and what its README says", found and found[0].startswith("tidepool: A tiny iOS app"), str(found))
    n_before = len(M.facts())
    got = await M.gather(["locale"])
    check("gathering proposes facts and keeps nothing yet", got["proposals"] and len(M.facts()) == n_before, str(got)[:200])
    kept = M.keep(got["proposals"][:1])
    check("…until you keep them, with where they came from", kept and kept[0]["source"].startswith("from "))

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
