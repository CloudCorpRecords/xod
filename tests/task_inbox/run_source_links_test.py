"""
Agent regression test: exact-reply requests get no appended garnish.

The chat layer appended a "**Source links**" block (or a "web search found
nothing" warning) to finished answers whenever the evidence board held sources.
That broke replies the user dictated exactly ("reply with exactly this text"),
failing the proving loop's exact-reply check. _source_link_events now takes the
user's query and stays silent when the query dictates the reply's exact form.

Run: .venv/bin/python tests/task_inbox/run_source_links_test.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import app.agent.researcher as RS

_board = lambda sources, trail: SimpleNamespace(sources=sources, tool_audit_trail=trail)
SRC = [{"url": "https://example.com/a", "title": "Example A"}]
EXACT = "Automated eval. Do not use the web, do not search, and reply with exactly this text and nothing else: XOD-EVAL-OK"
NORMAL = "What is the capital of France?"


def main():
    events = RS.AgenticResearcher._source_link_events
    # Sources on the board, exact-reply demand: silence.
    out = events(_board(SRC, []), "XOD-EVAL-OK", EXACT)
    assert out == [], f"exact-reply answer was garnished: {out}"
    print("PASS: no source links appended to an exact reply", flush=True)
    # Searched, found nothing, exact-reply demand: no warning either.
    out = events(_board([], [{"tool": "web_search"}]), "XOD-EVAL-OK", EXACT)
    assert out == [], f"exact-reply answer got the search warning: {out}"
    print("PASS: no search warning appended to an exact reply", flush=True)
    # Ordinary question: the links still arrive.
    out = events(_board(SRC, []), "Paris.", NORMAL)
    assert len(out) == 1 and "Source links" in out[0]["content"], out
    print("PASS: ordinary answers still get their source links", flush=True)
    # Ordinary question, empty search: the warning still arrives.
    out = events(_board([], [{"tool": "web_search"}]), "Paris.", NORMAL)
    assert len(out) == 1 and "Web search didn't return" in out[0]["content"], out
    print("PASS: ordinary answers still get the empty-search warning", flush=True)
    # An answer that already links its sources is left alone either way.
    out = events(_board(SRC, []), "See [Example A](https://example.com/a).", NORMAL)
    assert out == [], out
    print("PASS: answers with their own links are untouched", flush=True)


if __name__ == "__main__":
    main()
