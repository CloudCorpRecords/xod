"""
How long does an answer take, and where does the time go? Runs real questions through the research
pipeline with whatever model Xod is set to use, and times every model call, tool and phase.

Uses a throwaway data folder, never yours:
  .venv/bin/python tests/perf/run_speed_bench.py                 # the standard set
  .venv/bin/python tests/perf/run_speed_bench.py --only 1 --runs 2
Results also go to test_runs/perf/<timestamp>.json, so before/after can be compared.
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("XOD_DATA_DIR", tempfile.mkdtemp(prefix="xod-perf-"))
os.environ.setdefault("XOD_NO_UPDATE_CHECK", "1")

QUESTIONS = [
    ("research", "What's the latest news about the James Webb Space Telescope?"),
    ("research", "Compare the pricing of Notion and Obsidian"),
    ("research", "Who won the most recent Formula 1 Grand Prix?"),
    ("research", "What are the best budget mechanical keyboards right now?"),
    ("direct", "Explain what a hash map is in two sentences."),
]


async def run_one(question: str):
    from app.agent.researcher import AgenticResearcher
    from app.services.llm import llm_service

    calls = []
    orig_stream, orig_chat = llm_service.stream_chat_completion, llm_service.chat_completion

    async def stream(*a, **kw):
        rec = {"kind": "stream", "task": kw.get("task"), "think": kw.get("think"), "start": time.time(),
               "prompt_chars": len(json.dumps(kw.get("messages") or (a[0] if a else []))), "out_chars": 0, "reason_chars": 0}
        calls.append(rec)
        async for ch in orig_stream(*a, **kw):
            if ch.get("type") == "content":
                rec.setdefault("first", time.time())
                rec["out_chars"] += len(ch.get("content", ""))
            elif ch.get("type") == "reasoning":
                rec.setdefault("first_reason", time.time())
                rec["reason_chars"] += len(ch.get("content", ""))
            yield ch
        rec["end"] = time.time()

    async def chat(*a, **kw):
        rec = {"kind": "chat", "task": kw.get("task"), "think": kw.get("think"), "start": time.time(),
               "prompt_chars": len(json.dumps(kw.get("messages") or (a[0] if a else [])))}
        calls.append(rec)
        try:
            msg = await orig_chat(*a, **kw)
            rec["out_chars"] = len(msg.get("content") or "")
            return msg
        finally:
            rec["end"] = time.time()

    llm_service.stream_chat_completion, llm_service.chat_completion = stream, chat
    events, t0 = [], time.time()
    first_content = None
    tools = {}
    try:
        r = AgenticResearcher()
        async for ev in r.run_research_stream([{"role": "user", "content": question}]):
            now = time.time() - t0
            t = ev.get("type")
            events.append((round(now, 2), t, (ev.get("content") or ev.get("name") or "")[:60] if isinstance(ev.get("content") or ev.get("name") or "", str) else ""))
            if t == "content" and first_content is None:
                first_content = now
            if t == "tool_call":
                tools[ev["id"]] = {"name": ev["name"], "start": now}
            if t == "tool_result" and ev.get("id") in tools:
                tools[ev["id"]]["end"] = now
    finally:
        llm_service.stream_chat_completion, llm_service.chat_completion = orig_stream, orig_chat
    total = time.time() - t0
    for c in calls:
        c["took"] = round(c.get("end", time.time()) - c["start"], 2)
        c["ttft"] = round(c["first"] - c["start"], 2) if c.get("first") else None
        c["at"] = round(c["start"] - t0, 2)
        for k in ("start", "end", "first", "first_reason"):
            c.pop(k, None)
    return {"question": question, "total": round(total, 1), "first_words": round(first_content, 1) if first_content else None,
            "model_calls": len(calls), "calls": calls,
            "tools": [{"name": v["name"], "took": round(v.get("end", total) - v["start"], 2)} for v in tools.values()],
            "events": events}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, nargs="*", help="question numbers (1-based)")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--label", default="")
    ap.add_argument("--warm", action="store_true", help="warm the model first, as typing a question does in the app")
    a = ap.parse_args()
    from app.services.llm import llm_service
    info = llm_service.get_endpoint_info()
    print(f"Model: {info['provider']} / {info['model']}\n")
    qs = [q for i, q in enumerate(QUESTIONS, 1) if not a.only or i in a.only]
    out = []
    for kind, q in qs:
        for _ in range(a.runs):
            if a.warm:
                from app.services import warmup
                t = time.time()
                await warmup._warm()
                await asyncio.sleep(max(0, 4 - (time.time() - t)))     # someone typing for a few seconds
            r = await run_one(q)
            r["kind"] = kind
            out.append(r)
            print(f"{r['total']:6.1f}s total  {r['first_words'] or 0:6.1f}s to first words  {r['model_calls']} model calls  — {q}")
            for c in r["calls"]:
                print(f"         call @{c['at']:5.1f}s  {c['took']:5.1f}s  task={c['task']} think={c['think']} prompt={c['prompt_chars']}ch out={c.get('out_chars')}ch reasoning={c.get('reason_chars', 0)}ch ttft={c.get('ttft')}")
            for t in r["tools"]:
                print(f"         tool  {t['took']:5.1f}s  {t['name']}")
    res = [r for r in out if r["kind"] == "research"]
    if res:
        avg = sum(r["total"] for r in res) / len(res)
        print(f"\nResearched answers: average {avg:.1f}s, slowest {max(r['total'] for r in res):.1f}s, "
              f"average {sum(r['model_calls'] for r in res) / len(res):.1f} model calls")
    d = ROOT / "test_runs" / "perf"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{time.strftime('%Y%m%d-%H%M%S')}{'-' + a.label if a.label else ''}.json"
    f.write_text(json.dumps({"model": info["model"], "provider": info["provider"], "results": out}, indent=1))
    print(f"\nSaved {f.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
