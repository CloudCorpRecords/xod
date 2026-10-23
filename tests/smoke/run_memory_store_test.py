"""
Unified memory store (services/memory_store.py): one interface over facts, long-term memories and recall
episodes, local-first on embedded Chroma, replaceable by a remote Chroma URL, with a built-in SQLite
store when Chroma isn't installed/working. Migration imports about_you.json facts, the xod.db `memories`
table and recall.db episodes without deleting any source, and is safe to run twice.

Throwaway data folder only; no model needed (deterministic hashed vectors):
  .venv/bin/python tests/smoke/run_memory_store_test.py
Set XOD_TEST_CHROMA=0 to exercise only the SQLite fallback.
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="xod-memstore-"))
os.environ["XOD_DATA_DIR"] = str(DATA)
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


def seed_sources():
    # about_you.json fact (services/user_memory.py format)
    (DATA / "about_you.json").write_text(json.dumps({
        "facts": [{"id": "f1", "text": "GitHub is already authenticated as CloudCorpRecords",
                   "topic": "projects", "key": None, "source": "you said",
                   "added": time.time(), "updated": time.time(), "used": 0, "pinned": True}],
        "past": [], "settings": {"learn": True, "recall": True}, "gathered_at": {}}))
    # legacy long-term memories in xod.db
    c = sqlite3.connect(DATA / "xod.db")
    c.execute("""CREATE TABLE memories (id TEXT, category TEXT, title TEXT, content TEXT, tags TEXT,
                 source_session_id TEXT, embedding BLOB, embedding_model TEXT, embedding_dim INTEGER,
                 importance REAL, access_count INTEGER, last_accessed_at REAL, created_at REAL, updated_at REAL)""")
    c.execute("INSERT INTO memories VALUES ('mem_1','project_architecture','Xod stack',"
              "'Xod uses FastAPI and SQLite','fastapi,sqlite',NULL,NULL,'hash',768,4.0,0,0,?,?)",
              (time.time(), time.time()))
    c.commit(); c.close()
    # legacy recall episode in recall.db
    c = sqlite3.connect(DATA / "recall.db")
    c.execute("""CREATE TABLE episodes (id TEXT PRIMARY KEY, kind TEXT, ref TEXT, ts REAL, title TEXT,
                 text TEXT, session_id TEXT, embedding BLOB, emb_model TEXT)""")
    c.execute("INSERT INTO episodes VALUES ('ep1','chat','s1',?,'Logo decision',"
              "'We decided the logo should be a marigold moon','s1',NULL,'hash')", (time.time(),))
    c.commit(); c.close()


async def main():
    from app.services import memory_store as MS

    seed_sources()

    store = MS.get_store(force="sqlite")
    check("sqlite fallback store is available", store.backend == "sqlite", store.backend)

    r = store.upsert({"id": "memory:1", "kind": "memory", "title": "Tea preference",
                      "text": "Rene prefers short answers and strong tea", "category": "preferences",
                      "tags": ["tea"], "importance": 4.0})
    check("upsert returns the record", r["id"] == "memory:1")
    hits = store.search("strong tea", kinds=["memory"], limit=3)
    check("search finds it by meaning/keywords", hits and hits[0]["id"] == "memory:1", str(hits)[:200])
    check("other kinds are filtered out", not store.search("strong tea", kinds=["episode"], limit=3))
    check("delete works", store.delete("memory:1") and not store.get("memory:1"))

    counts = MS.migrate(store=store)
    check("migration imports all three sources",
          counts.get("facts") == 1 and counts.get("memories") == 1 and counts.get("episodes") == 1, str(counts))
    again = MS.migrate(store=store)
    check("migration is idempotent (nothing new twice)",
          again.get("facts") == 0 and again.get("memories") == 0 and again.get("episodes") == 0, str(again))
    check("sources are kept, not deleted",
          (DATA / "about_you.json").exists() and sqlite3.connect(DATA / "xod.db")
          .execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1)
    gh = store.search("github authenticated", kinds=["fact"], limit=3)
    check("migrated fact is searchable as a fact", gh and "CloudCorpRecords" in gh[0]["text"], str(gh)[:200])
    ep = store.search("marigold moon logo", kinds=["episode"], limit=3)
    check("migrated episode is searchable as an episode", ep and ep[0]["id"] == "episode:ep1", str(ep)[:200])
    stats = store.stats()
    check("stats report backend + counts", stats["backend"] == "sqlite" and stats["total"] >= 3, str(stats))

    if os.environ.get("XOD_TEST_CHROMA", "1") != "0":
        try:
            import chromadb  # noqa: F401
            chroma = MS.get_store(force="chroma")
            check("embedded chroma store is available", chroma.backend == "chroma", chroma.backend)
            chroma.upsert({"id": "memory:c1", "kind": "memory", "title": "Chroma tea",
                           "text": "Chroma remembers the strong tea preference", "category": "preferences"})
            hits = chroma.search("strong tea", kinds=["memory"], limit=3)
            check("chroma search works", hits and hits[0]["id"] == "memory:c1", str(hits)[:200])
            counts = MS.migrate(store=chroma)
            check("migration into chroma imports the sources", counts.get("total", 0) >= 3, str(counts))
        except ImportError:
            check("embedded chroma store is available", False, "chromadb not installed in this env")

    for name, st in (("sqlite", store), ("chroma", locals().get("chroma"))):
        if st is None:
            continue
        try:
            st.upsert({"id": "memory:secret", "kind": "memory", "title": "clinic portal",
                       "text": "my password for the clinic portal is hunter2-Blue, card 4111 1111 1111 1111"})
            refused = False
        except ValueError:
            refused = True
        check(f"secrets are refused, not stored ({name})", refused and not st.get("memory:secret"))

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
