"""
Self-knowledge, junk-scope exclusions, and answer consumption for locate
(DEFECT-FILE-FINDER-SELF-KNOWLEDGE-2026-11-02, fix lane
tree/fix-finder-self-knowledge).

Written failing-first against main 09e7df9. The four observed failures:

  A  "Find the folder where the Xod source code lives" (the graded R1
     text, dispatched by hunt_routing with roots=[home]) ended in an ask
     between two iOS DerivedData build products (Xod.app / Xod.build
     under ~/xod-ios-build-logs). It must resolve found, top candidate
     ~/Documents/xod. Real-home check: SKIPPED when the canonical tree
     is absent (this test is Mac-shaped by design).
  B  "Find the changelog file for the Xod project" (graded R2) ranked
     ~/Documents/GitHub/xod/docs/CHANGELOG.md first and STILL asked,
     because a copy lives under an _archive-xod-worktrees tree. It must
     resolve found with that GitHub path, no ask. B2 repeats the shape on a
     synthetic tree (a live project changelog shadowed by an _archive
     copy) so the exclusion is covered anywhere.
  C  A find whose only candidates are junk-class files (two identical
     Kotlin cache .tab files under */app/build/* caches, matched on the
     weak tokens 'to'/'source') produced a "two close matches" ask
     between identical cache files. It must return an honest not_found.
     C2 repeats it under a caches-jvm tree with no "build" segment.
     C3 is the control: two genuine user documents (Smith leases) must
     still ask - real ambiguity is not suppressed.
  D  Answer consumption (app/services/task_queue.py): a locate task
     waiting on an ask, answered with the canonical path, re-ran the
     hunt and re-asked the identical question (the answers Tree sent
     live on 2026-11-02 never cleared the waiting state). An answer that
     names a candidate's exact path, or another absolute path that
     exists, must finish the task done with that path. An answer with
     no usable path keeps the old continue behaviour.

No model, no Keychain, no network:
  PYTHON_KEYRING_BACKEND=tests.support.filekeyring.FileKeyring \
    ~/Documents/xod/.venv/bin/python tests/locate/run_locate_self_knowledge_test.py
(The main checkout's .venv interpreter is used because a git worktree
carries no .venv of its own; the test inserts its own tree at sys.path[0].)
"""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-selknow-data-")

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


def plant(base, rel, body="x", when=None):
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, bytes):
        p.write_bytes(body)
    else:
        p.write_text(body)
    if when:
        ts = datetime(*when).timestamp()
        os.utime(p, (ts, ts))
    return p


class EmptyIndex:
    def search(self, clues, roots):
        return []


R1 = "Find the folder where the Xod source code lives. Reply with its full path."
R2 = "Find the changelog file for the Xod project. Reply with its full path."


def main():
    from app.services import locate as loc

    home = Path.home()

    # ---- A: Xod's own source folder, real home -------------------------
    src = home / "Documents" / "xod"
    if src.is_dir():
        res = loc.locate(R1, roots=[str(home)], index_backend=EmptyIndex(),
                         content_deadline_s=5, hunt_deadline_s=30)
        check("A: source folder hunt resolves found, not ask",
              res["status"] == "found", str(res)[:300])
        check("A: top candidate is ~/Documents/xod",
              bool(res["candidates"]) and res["candidates"][0]["path"] == str(src),
              str(res["candidates"][:2]))
    else:
        print(f"SKIP  A: {src} does not exist on this machine")

    # ---- B: Xod's changelog, real home ---------------------------------
    changelog = home / "Documents" / "GitHub" / "xod" / "docs" / "CHANGELOG.md"
    if changelog.is_file():
        res = loc.locate(R2, roots=[str(home)], index_backend=EmptyIndex(),
                         content_deadline_s=5, hunt_deadline_s=30)
        check("B: changelog hunt resolves found, not ask",
              res["status"] == "found", str(res)[:300])
        check("B: top candidate is the GitHub clone's docs/CHANGELOG.md",
              bool(res["candidates"]) and res["candidates"][0]["path"] == str(changelog),
              str(res["candidates"][:2]))
    else:
        print(f"SKIP  B: {changelog} does not exist on this machine")

    # ---- B2: live changelog shadowed by an _archive copy ---------------
    base = Path(tempfile.mkdtemp(prefix="xod-selknow-b2-"))
    live = plant(base, "acme/docs/CHANGELOG.md", "# acme changelog (live project)")
    plant(base, "_archive-old/acme/docs/CHANGELOG.md", "# acme changelog (archived copy)")
    res = loc.locate("find the changelog file for the acme project",
                     roots=[str(base)], index_backend=EmptyIndex())
    check("B2: archived copy never shadows the live changelog (found, no ask)",
          res["status"] == "found" and bool(res["candidates"])
          and res["candidates"][0]["path"] == str(live),
          str(res)[:300])

    # ---- C: only junk-class candidates -> honest not_found -------------
    body = "kotlin cache bytes, identical in both caches"
    for label, rels in (
        ("C", ["tiny/app/build/kotlin/compileDebugKotlin/cacheable/caches-jvm/jvm/kotlin/class-fq-name-to-source.tab",
               "tiny/app/build/kotlin/compileDebugUnitTestKotlin/cacheable/caches-jvm/jvm/kotlin/class-fq-name-to-source.tab"]),
        ("C2", ["lib/caches-jvm/a/class-fq-name-to-source.tab",
                "lib/caches-jvm/b/class-fq-name-to-source.tab"]),
    ):
        cbase = Path(tempfile.mkdtemp(prefix=f"xod-selknow-{label.lower()}-"))
        for rel in rels:
            plant(cbase, rel, body)
        res = loc.locate("Find the file with class-fq-name-to-source in its name. Reply with its full path.",
                         roots=[str(cbase)], index_backend=EmptyIndex())
        check(f"{label}: identical cache files are not_found, never an ask",
              res["status"] == "not_found" and not res["candidates"] and not res.get("question"),
              str(res)[:300])

    # ---- C3 control: genuine ambiguity still asks ----------------------
    base = Path(tempfile.mkdtemp(prefix="xod-selknow-c3-"))
    lease_a = plant(base, "Legal/Smith Lease.pdf", b"%PDF fake", (2025, 3, 1))
    lease_b = plant(base, "Archive/Smith Lease.pdf", b"%PDF fake", (2025, 3, 1))
    res = loc.locate("find the Smith lease", roots=[str(base)], index_backend=EmptyIndex())
    check("C3 control: two genuine user documents still ask, both named",
          res["status"] == "ask"
          and {c["path"] for c in res["candidates"][:2]} == {str(lease_a), str(lease_b)}
          and bool(res.get("question")), str(res)[:300])

    # ---- D: answer consumption at the task layer -----------------------
    from app.services.task_queue import TaskQueue

    dbase = Path(tempfile.mkdtemp(prefix="xod-selknow-d-"))
    real_src = dbase / "Documents" / "xod"
    real_src.mkdir(parents=True)
    cand1 = dbase / "buildlogs" / "Xod.app"
    cand2 = dbase / "buildlogs" / "Xod.build"
    cand1.mkdir(parents=True)
    cand2.mkdir(parents=True)

    def cand_dict(p, score):
        st = p.stat()
        return {"path": str(p), "name": p.name, "kind": "folder", "is_folder": True,
                "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
                "modified_ts": st.st_mtime, "size": st.st_size, "score": score,
                "why": ["name matches 'xod'", "the name is exactly the thing described"]}

    ask_payload = {
        "request": R1, "status": "ask",
        "question": "Two close matches: Xod.app and Xod.build. Which one did you mean?",
        "clues": {"name_tokens": ["xod", "source", "code"], "kind": "folder", "year": None,
                  "excluded_years": [], "content_words": [], "purpose_terms": [],
                  "want_folder": True, "folder_by_contents": False, "date_label": ""},
        "tried": [{"route": "filename", "terms": ["xod", "source", "code"], "hits": 2}],
        "candidates": [cand_dict(cand1, 65.0), cand_dict(cand2, 75.0)],
    }

    q = TaskQueue()

    def waiting_task():
        t = q.create(R1)
        q._update(t["id"], status="waiting", waiting_kind="question",
                  waiting_detail={"message": "ask"}, result="ask report",
                  evidence_json=json.dumps([{"tool": "locate_files", "payload": ask_payload}]))
        return t["id"]

    # D1: the answer names a canonical path that was NOT a candidate.
    tid = waiting_task()
    out = q.answer(tid, f"Neither of those. The Xod source code lives at {real_src}")
    after = q.get(tid)
    check("D1: an answer naming the canonical path completes the task",
          out.get("success") is True and after["status"] == "done",
          f"out={out} status={after['status']}")
    check("D1: the delivered result names the canonical path",
          str(real_src) in (after.get("result") or ""), str(after.get("result"))[:300])

    # D2: the answer names a candidate's exact path.
    tid = waiting_task()
    out = q.answer(tid, f"The second one: {cand2}")
    after = q.get(tid)
    check("D2: an answer naming a candidate's path completes with that candidate",
          out.get("success") is True and after["status"] == "done"
          and str(cand2) in (after.get("result") or ""),
          f"out={out} status={after['status']} result={str(after.get('result'))[:200]}")

    # D3 control: an answer with no usable path keeps the continue flow.
    tid = waiting_task()
    out = q.answer(tid, "the other one please")
    after = q.get(tid)
    check("D3 control: a pathless answer still queues a continuation",
          out.get("success") is True and after["status"] == "queued"
          and (after.get("next_action") or {}).get("type") == "continue",
          f"out={out} status={after['status']} next={after.get('next_action')}")

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)} passed; failed={failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
