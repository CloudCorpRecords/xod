"""
Long-form context (services/longform_context.py, AGE-96): the chunk store, budgeted recall
and budget-driven compaction that let a long conversation or a long document stay usable
without losing the thread (docs/LONGFORM-CONTEXT-DESIGN.md).

  L1  chunking: paragraph packing, oversized-paragraph split, overlap, token estimates
  L2  ingest: sources/chunks persisted, re-ingest replaces, status() counts
  L3  recall: rare-term hit, hard budget guarantee, document order, session scoping
  L4  fit_budget: under budget is identity; over budget keeps system + verbatim tail,
      replaces the middle with the injected summary, and the result fits the budget
  L5  compaction fallback: a failing summarizer yields the extractive digest, still
      within budget, never the full history
  L6  nothing is lost: after compaction, recall() returns a verbatim detail that only
      existed in a compacted middle message
  L7  message rendering: non-string content ingests without error

No model needed: the summarizer seam is injected. Throwaway data folder:
  .venv/bin/python tests/smoke/run_longform_context_test.py
"""
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-longform-"))
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def para(tag, words=60):
    return f"{tag} " + " ".join(f"w{i}" for i in range(words)) + f" end of {tag}."


def main():
    from app.services import longform_context as lf

    print("L1 chunking")
    text = "\n\n".join(para(f"p{i}") for i in range(10))
    chunks = lf.chunk_text(text)
    check("multi-paragraph text splits into several chunks", len(chunks) >= 3, f"got {len(chunks)}")
    check("every chunk within target + one paragraph", all(len(c) <= lf.CHUNK_CHARS + 400 for c in chunks))
    big = "x" * (lf.CHUNK_CHARS * 3 + 10)
    big_chunks = lf.chunk_text(big)
    check("oversized paragraph is hard-split", len(big_chunks) >= 3, f"got {len(big_chunks)}")
    check("consecutive chunks overlap", chunks[0][-40:] in chunks[1] or chunks[1].startswith(chunks[0][-lf.CHUNK_OVERLAP:][:40]))
    check("token estimate is ceil(chars/4)", lf.estimate_tokens("abcd") == 1 and lf.estimate_tokens("abcde") == 2
          and lf.estimate_tokens("") == 0)

    print("L2 ingest and status")
    doc = "\n\n".join(para(f"doc{i}") for i in range(5))
    r1 = lf.ingest_text(doc, kind="document", title="Spec", source_id="doc-1")
    st = lf.status()
    check("ingest reports chunks", r1["chunks"] >= 2, str(r1))
    check("status counts the source and its chunks", st["sources"] == 1 and st["chunks"] == r1["chunks"], str(st))
    r2 = lf.ingest_text(doc + "\n\n" + para("extra"), kind="document", title="Spec", source_id="doc-1")
    st2 = lf.status()
    check("re-ingest replaces instead of duplicating", st2["sources"] == 1 and st2["chunks"] == r2["chunks"],
          f"{st} -> {st2} r2={r2}")

    print("L3 budgeted recall")
    needle_doc = "\n\n".join([para("filler a"), para("filler b"),
                              "The launch code is quixotic-7731 and must not change.",
                              para("filler c"), para("filler d")])
    lf.ingest_text(needle_doc, kind="document", title="Notes", source_id="doc-2")
    got = lf.recall("quixotic launch code", budget_tokens=2000)
    check("recall finds the rare-term chunk", "quixotic-7731" in got["text"], got["text"][:120])
    check("recall names its source", got["sources"] == ["doc-2"] or "doc-2" in got["sources"], str(got["sources"]))
    tight = lf.recall("filler", budget_tokens=60)
    check("assembled tokens never exceed the budget", tight["tokens_used"] <= 60, str(tight["tokens_used"]))
    wide = lf.recall("filler", budget_tokens=100000, session_id=None)
    seqs = [int(line.split("· part ")[1].rstrip("]")) for line in wide["text"].splitlines() if "· part " in line]
    check("chunks return in document order", seqs == sorted(seqs), str(seqs))
    lf.ingest_messages("sess-A", [{"role": "user", "content": "apples zebraword here"},
                                  {"role": "assistant", "content": "noted"}])
    lf.ingest_messages("sess-B", [{"role": "user", "content": "oranges zebraword there"},
                                  {"role": "assistant", "content": "noted"}])
    scoped = lf.recall("zebraword", budget_tokens=2000, session_id="sess-B")
    check("session scoping excludes other sessions", "oranges zebraword" in scoped["text"]
          and "apples zebraword" not in scoped["text"], scoped["text"][:160])

    print("L4 fit_budget")
    async def run_fit():
        small = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        same = await lf.fit_budget("s-small", small, budget_tokens=1000)
        check("under budget returns messages unchanged", same is small)

        msgs = [{"role": "system", "content": "You are Xod."}]
        for i in range(30):
            msgs.append({"role": "user" if i % 2 == 0 else "assistant",
                         "content": f"turn {i} " + "detail " * 40})
        orig_summarizer = lf.summarizer

        async def fake_summarizer(older, prior):
            return f"SUMMARY of {len(older)} earlier turns."
        lf.summarizer = fake_summarizer
        try:
            fitted = await lf.fit_budget("s-big", msgs, budget_tokens=800)
        finally:
            lf.summarizer = orig_summarizer
        total = sum(lf.estimate_tokens(lf._content_text(m.get("content"))) for m in fitted)
        check("over-budget result fits the budget", total <= 800, f"total={total} n={len(fitted)}")
        check("system message kept first", fitted[0]["role"] == "system" and fitted[0]["content"] == "You are Xod.")
        check("middle replaced by the injected summary", any("SUMMARY of" in lf._content_text(m.get("content"))
                                                             for m in fitted))
        check("newest tail kept verbatim", fitted[-1]["content"] == msgs[-1]["content"]
              and fitted[-2]["content"] == msgs[-2]["content"])
        check("history shrank", len(fitted) < len(msgs), f"{len(fitted)} vs {len(msgs)}")
        return msgs

    msgs = asyncio.run(run_fit())

    print("L5 failing summarizer falls back to a digest")
    async def run_fail():
        orig = lf.summarizer

        async def boom(older, prior):
            raise RuntimeError("model down")
        lf.summarizer = boom
        try:
            return await lf.fit_budget("s-fail", msgs, budget_tokens=800)
        finally:
            lf.summarizer = orig
    fitted_fail = asyncio.run(run_fail())
    total_fail = sum(lf.estimate_tokens(lf._content_text(m.get("content"))) for m in fitted_fail)
    check("digest fallback still fits the budget", total_fail <= 800, f"total={total_fail}")
    check("digest fallback is not the full history", len(fitted_fail) < len(msgs))
    check("digest carries real middle content", any("turn 0" in lf._content_text(m.get("content"))
                                                    and "compacted to fit" in lf._content_text(m.get("content"))
                                                    for m in fitted_fail))

    print("L6 compacted detail stays recallable verbatim")
    secret_msgs = [{"role": "system", "content": "sys"}]
    for i in range(30):
        body = f"turn {i} " + "padding " * 40
        if i == 3:
            body = "turn 3 the vault code is magenta-4452 remember it " + "padding " * 30
        secret_msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": body})

    async def run_secret():
        orig = lf.summarizer

        async def fake(older, prior):
            return "a summary with no codes in it"
        lf.summarizer = fake
        try:
            return await lf.fit_budget("s-secret", secret_msgs, budget_tokens=800)
        finally:
            lf.summarizer = orig
    fitted_secret = asyncio.run(run_secret())
    check("the code is gone from the compacted history",
          all("magenta-4452" not in lf._content_text(m.get("content")) for m in fitted_secret))
    back = lf.recall("magenta vault code", budget_tokens=2000, session_id="s-secret")
    check("recall returns the compacted detail verbatim", "magenta-4452" in back["text"], back["text"][:160])

    print("L7 non-string content")
    r7 = lf.ingest_messages("s-mixed", [
        {"role": "user", "content": [{"type": "text", "text": "a picture of a zebrafish"}]},
        {"role": "assistant", "content": {"note": "structured reply"}}])
    check("structured content ingests", r7["chunks"] >= 1, str(r7))
    got7 = lf.recall("zebrafish", budget_tokens=1000, session_id="s-mixed")
    check("structured content is recallable", "zebrafish" in got7["text"], got7["text"][:120])

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
