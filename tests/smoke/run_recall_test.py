"""
Xod's memory of what it has done (services/recall.py, work_state.py, user_memory's timeline), with pretend
history in a throwaway data folder: dated chats, a finished background task and one waiting for approval.

Checks that past chats and tasks are indexed (greetings skipped), found by keywords and by meaning, narrowed by
time ("yesterday", "last week"), that "what was that ramen place you found last week?" is answered from memory
with a visible memory step, that "anything waiting on me?" is answered from the task list, and that a fact that
changed ("moved to Denver") keeps the old one on a timeline.

Uses LM Studio's chat and embedding models (never a cloud model); a throwaway keyring:
  .venv/bin/python tests/smoke/run_recall_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-recall-")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


def chat(title, q, a, when):
    from app.services import storage
    s = storage.create_session(title=title, persona="ultimate_researcher")
    u = storage.add_message(s["id"], "user", q)
    r = storage.add_message(s["id"], "assistant", a)
    db = storage.get_db_connection()
    db.execute("UPDATE messages SET created_at = ? WHERE id = ?", (when, u["id"]))
    db.execute("UPDATE messages SET created_at = ? WHERE id = ?", (when + 20, r["id"]))
    db.commit()
    db.close()
    return s["id"]


def task(title, request, status, result="", when=None, waiting=None):
    from app.services import task_queue as tq
    c = tq._conn()
    now = when or time.time()
    c.execute("INSERT INTO tasks (id, created_at, updated_at, finished_at, title, request, status, waiting_kind, waiting_detail, result, history) "
              "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (f"task-{int(now)}{len(title)}", now - 600, now, now if status in ("done", "failed") else None, title, request, status,
               waiting, json.dumps({"label": "Submit application"}) if waiting else None, result, "[]"))
    c.commit()
    c.close()


async def main():
    from app.config import settings
    from tests.support.local_model import local_model  # LLM_* if set, else LM Studio
    settings.llm_provider, settings.llm_base_url, settings.llm_model = local_model()
    from app.services import recall, work_state, user_memory
    from app.services import task_queue as _tq   # creates the tasks table  # noqa: F401

    now = datetime.now()
    last_week = (now.replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday() + 5)).timestamp()
    yesterday = (now.replace(hour=15, minute=0, second=0, microsecond=0) - timedelta(days=1)).timestamp()
    chat("Ramen in Portland", "Find me a really good ramen place in Portland",
         "Try **Afuri Ramen** on SE Morrison St: their yuzu shio ramen is the standout. Kayo's Ramen Bar is a good second.", last_week)
    chat("Note apps", "compare Notion vs Obsidian for keeping my class notes",
         "For class notes Obsidian wins: local Markdown files, fast search, works offline. Notion is better for sharing with students.", yesterday)
    chat("Hi", "hi", "Hi! What can I do for you?", time.time() - 3000)
    chat("Quiz app name", "what's a good name for my lab safety quiz app", "How about **LabSafe Sprint** or **Goggles On**?", time.time() - 1800)
    task("Apply to Acme chemistry outreach role", "Apply to the chemistry outreach coordinator job at Acme", "done",
         "Applied. The page confirmed: Application received, reference #AC-2291.", when=time.time() - 7200)
    task("Apply to Globex lab manager", "Apply to the lab manager job at Globex", "waiting", waiting="approval")

    n = await recall.index_new()
    st = recall.stats()
    check("past chats and finished tasks are indexed (greetings skipped)", n == 4 and st.get("chat", {}).get("count") == 3 and st.get("task", {}).get("count") == 1, f"{n} {st}")
    emb = recall._conn().execute("SELECT DISTINCT emb_model FROM episodes").fetchall()
    print("      (embeddings:", ", ".join(r[0] for r in emb) + ")")

    r = await recall.search("ramen")
    check("keywords: 'ramen' finds the ramen chat", r["results"] and "Afuri" in r["results"][0]["text"], str(r)[:300])
    r = await recall.search("which note-taking apps did we weigh up for my school stuff")
    check("meaning: 'note-taking apps … school stuff' finds Notion vs Obsidian", r["results"] and "Obsidian" in r["results"][0]["text"],
          json.dumps([x["title"] for x in r["results"]]))
    r = await recall.search("what did we talk about yesterday")
    check("time: 'yesterday' narrows to yesterday", r["window"] == "yesterday" and r["in_window"] and r["results"][0]["title"] == "Note apps",
          json.dumps([(x["title"], x["when"]) for x in r["results"]]))
    r = await recall.search("restaurant recommendation from last week")
    check("time + meaning: 'restaurant … last week'", r["window"] == "last week" and r["results"] and "Afuri" in r["results"][0]["text"],
          json.dumps([(x["title"], x["when"]) for x in r["results"]]))
    r = await recall.search("acme application reference number")
    check("finished background tasks are remembered", r["results"] and "AC-2291" in r["results"][0]["text"])

    pw = recall.parse_when
    check("time words: last week / in September / 3 days ago / on Monday", all(pw(x) for x in ("last week", "back in September", "3 days ago", "on monday")))

    note = work_state.note()
    check("its own work: the waiting task and the finished one", "WAITING for your OK" in note and "Globex" in note and "Acme" in note, note)

    from app.agent.researcher import AgenticResearcher

    async def ask(q):
        out, tools = "", []
        async for ev in AgenticResearcher().run_research_stream([{"role": "user", "content": q}]):
            if ev.get("type") == "content":
                out += ev["content"]
            if ev.get("type") == "tool_call":
                tools.append(ev["name"])
        return out, tools
    out, tools = await ask("what was that ramen place you found for me last week?")
    check("'that ramen place you found last week' is answered from memory", "Afuri" in out, out[:300])
    check("…with the memory lookup shown as a step", "memory_search" in tools and "web_search" not in tools, str(tools))
    out, tools = await ask("anything waiting on me?")
    check("'anything waiting on me?' comes from its task list", "Globex" in out, out[:300])
    out, tools = await ask("how did the Acme application go?")
    check("'how did the Acme application go?' knows the result", "AC-2291" in out or "received" in out.lower(), out[:300])

    user_memory.add("Lives in Austin", "location", "city")
    user_memory.add("Lives in Denver", "location", "city")
    past = user_memory.load()["past"]
    check("a fact that changed stays on the timeline", any("Austin" in p["text"] for p in past) and
          [f["text"] for f in user_memory.facts() if f.get("key") == "city"] == ["Lives in Denver"])
    check("…and comes up when asked about before", "Austin" in user_memory.relevant_note("where did I live before?"))

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
