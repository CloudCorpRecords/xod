"""
Xod's own research (services/curiosity.py, soul.py): it only reads up on what it isn't sure of (questions from chats
it got wrong or hedged on, and things it probes itself on and turns out unsure about), keeps the answers in its
notebook, uses them in later chats, and only now and then tells you. Plus understanding follow-ups and labelling
training data by how turns went (services/turn_quality.py).

In a throwaway data folder and dataset. Web pages are faked (no browser); the uncertainty probe, reading the pages,
the question rewrite, the self-check and the chat use LM Studio's Gemma (never a cloud model):
  .venv/bin/python tests/smoke/run_inner_life_test.py
"""
import asyncio
import json
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="xod-inner-")
os.environ["XOD_DATA_DIR"] = TMP
os.environ["XOD_DATASET_DB"] = str(Path(TMP) / "dataset.db")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


# Made-up pages, so the answer can only have come from reading them.
PAGES = {
    "grimsvotn": [{"title": "Grímsvötn eruption 2026 - Icelandic Met Office", "url": "https://example.org/grimsvotn-2026",
                   "content": "The 2026 eruption of Grímsvötn began on 3 March 2026 at 04:10, after a week of earthquakes under "
                              "Vatnajökull. The ash plume reached 9 km. It was the first eruption there since 2011."}],
    "default": [{"title": "Volcano facts", "url": "https://example.org/volcano",
                 "content": "Kilauea lava reaches about 1,170 °C. Lava colour shows temperature: yellow is hottest."}],
}


async def fake_search_and_read(q, read_top=2, budget=900, **kw):
    key = "grimsvotn" if "msv" in q.lower() or "grims" in q.lower() else "default"
    return {"results": PAGES[key]}


def fake_ask_factory(sure: bool):
    """Stands in for the local model during a pretend day (fast): sure=True answers the same way every time, confidently."""
    calls = {"n": 0}

    async def ask(system, user, max_tokens=160):
        calls["n"] += 1
        if "factual questions about" in user:
            topic = re.search(r"about (.+?) that", user).group(1)
            return "\n".join(f"What is fact number {i} about {topic} {random.randint(0, 9999)}?" for i in range(3))
        if "sure: <0-100>" in user:
            n = len(re.findall(r"^\d+\. ", user.split("Questions:")[1], re.M))
            if sure:
                return "\n".join(f"{i + 1}. It is exactly forty two kilometres | sure: 95" for i in range(n))
            return "\n".join(f"{i + 1}. {random.choice(['A red bird', 'In 1952 in Peru', 'About nine tonnes', 'Nobody, it was a myth'])} "
                             f"| sure: {random.randint(10, 40)}" for i in range(n))
        if "ANSWER:" in user:
            return ("ANSWER: Kilauea lava reaches about 1,170 °C.\nCHANGED: no\nLEARNED: I wasn't sure how hot lava gets, so I "
                    "looked it up: Kilauea's reaches about 1,170 °C.\nFACTS: 1,170 °C | yellow is hottest\nNEXT: how is lava "
                    "temperature measured?\nSPARK: none")
        if "PROMPT:" in user:
            return "You could turn last week's notes into a schedule.\nPROMPT: Make me a schedule"
        return "I wonder if the cursor gets tired of blinking."
    return ask


async def main():
    from app.config import settings
    settings.llm_provider, settings.llm_base_url, settings.llm_model = "lm_studio", "http://127.0.0.1:1234/v1", "google/gemma-4-12b-qat"
    from app.services import soul, curiosity, turn_quality, dataset_recorder, web_reader
    web_reader.search_and_read = fake_search_and_read
    turn_quality._judging = 99     # no background self-checks piling onto Gemma during the test (they're called directly below)

    for t in ["tell me about volcanoes in Iceland", "volcanoes and lava flows", "my sourdough starter smells odd",
              "sourdough hydration tips", "volcanoes near Naples", "sourdough rye loaf"]:
        soul.learn_from_message(t)
    check("its interests come from what you talk about", {"volcanoes", "sourdough"} <= set(soul.interests()), str(soul.interests()))
    day = curiosity.today()
    check("it picks a plan for the day, and keeps it", day["plan"] in curiosity.PLANS and curiosity.today()["plan"] == day["plan"], str(day))

    # ---------------------------------------------------------------- what it's unsure of
    r = curiosity.researchable
    check("only general questions get looked up: not about you, not requests",
          r("how tall is Mount Fuji?") and not r("what's my dentist's name?") and not r("write me a poem about rain")
          and not r("when is our anniversary"))

    async def gemma(system, user, max_tokens=160):
        return await soul._ask(system, user, max_tokens)
    t0 = time.time()
    u = await curiosity.uncertainty(gemma, ["What is the capital of France?", "How many legs does a spider have?",
                                            "Who won the 2026 Tour de France?", "What was the exact attendance at the 2026 Grímsvötn science open day?"])
    print(f"      (uncertainty in {time.time() - t0:.0f}s: " + ", ".join(f"{x['question'][:28]}… {x['uncertainty']}" for x in u) + ")")
    check("it's sure of what it knows (France, spiders) and unsure of what it can't know (2026 events)",
          max(u[0]["uncertainty"], u[1]["uncertainty"]) < curiosity.UNSURE <= min(u[2]["uncertainty"], u[3]["uncertainty"]), json.dumps(u)[:400])

    t0 = time.time()
    scored = await curiosity.probe(gemma, "Sam", {"name": "volcanoes", "thread": ""})
    print(f"      (probe in {time.time() - t0:.0f}s: " + "; ".join(f"{s['question'][:50]} {s['uncertainty']}" for s in scored) + ")")
    c = curiosity._conn()
    statuses = {r["status"] for r in c.execute("SELECT status FROM gaps WHERE source = 'probe'").fetchall()}
    c.close()
    check("probing a topic writes questions and scores how unsure it is of each (all recorded)",
          len(scored) >= 2 and all(0 <= s["uncertainty"] <= 1 for s in scored) and statuses <= {"open", "known"}, json.dumps(scored)[:300])

    # ---------------------------------------------------------------- from your chats
    turn = turn_quality.begin("c1", "when did the 2026 Grímsvötn eruption begin?")
    turn_quality.finish(turn, "Grímsvötn hasn't erupted since 2011, as far as I know.", [])
    turn_quality.begin("c1", "no, that's wrong, it erupted this year")
    turn = turn_quality.begin("c2", "how many moons does Neptune have?")
    turn_quality.finish(turn, "I'm not sure of the latest count, but Neptune has at least 14 known moons.", [])
    turn = turn_quality.begin("c3", "what's my sister's favourite colour?")
    turn_quality.begin("c3", "no, that's wrong")
    gaps = {g["question"]: g for g in curiosity.open_gaps(50)}
    check("a question it got corrected on becomes something to look up", any("Grímsvötn" in q and g["source"] == "correction" for q, g in gaps.items()), str(list(gaps)))
    check("…and one it hedged on", any("Neptune" in q and g["source"] == "hedge" for q, g in gaps.items()))
    check("…but nothing about you", not any("sister" in q for q in gaps))

    # ---------------------------------------------------------------- reading up on it (Gemma reads the pages)
    t0 = time.time()
    note = await curiosity.read_up(gemma, "You are Xod's inner voice. Plain words.", "Sam", web=True)
    print(f"      (read up in {time.time() - t0:.0f}s: {json.dumps({k: note[k] for k in ('topic', 'question', 'answer', 'changed', 'reward')}) if note else None})")
    check("chat questions it got wrong are looked up first", note and "Grímsvötn" in note["question"] and note["from_chat"], str(note)[:200])
    check("…the answer comes from the pages, and it knows its guess was wrong",
          note and "3 March" in note["answer"] and note["changed"], str(note)[:300])
    c = curiosity._conn()
    g = c.execute("SELECT status, note_id FROM gaps WHERE note_id = ?", (note["id"] if note else "",)).fetchone()
    c.close()
    check("…and the question is marked answered", g and g["status"] == "answered")

    # ---------------------------------------------------------------- using it later
    k = await curiosity.knowledge("when did the Grimsvotn eruption start this year?")
    k2 = await curiosity.knowledge("best deep dish pizza in Chicago")
    print(f"      (notebook match: {[x['sim'] for x in k]}, unrelated: {[x['sim'] for x in k2]})")
    check("a later question finds what it read up on; an unrelated one doesn't", k and "3 March" in k[0]["answer"] and not k2)

    from app.agent.researcher import AgenticResearcher
    out, tools = "", []
    async for ev in AgenticResearcher().run_research_stream([{"role": "user", "content": "when did the Grímsvötn eruption start this year?"}]):
        if ev.get("type") == "content":
            out += ev["content"]
        if ev.get("type") == "tool_call":
            tools.append(ev["name"])
    print(f"      (chat: {tools} {out[:160]!r})")
    check("in a chat it answers from its notebook (shown as a Notebook step)", "notebook_search" in tools and "March" in out, out[:300])

    # ---------------------------------------------------------------- nothing it's unsure of: no busywork
    c = curiosity._conn()
    c.execute("UPDATE gaps SET status = 'answered' WHERE status = 'open'")
    c.commit()
    c.close()
    before = len(curiosity.notes(100))
    n = await curiosity.read_up(fake_ask_factory(sure=True), "voice", "Sam", web=True, topic={"name": "sourdough", "thread": "", "origin": "you"})
    c = curiosity._conn()
    known = c.execute("SELECT COUNT(*) FROM gaps WHERE topic = 'sourdough' AND status = 'known'").fetchone()[0]
    c.close()
    check("when it's sure of everything it asks itself about a topic, it doesn't read up (and remembers it knew)",
          n is None and len(curiosity.notes(100)) == before and known >= 2, f"{n} {known}")
    check("with web reading off there's nothing new to learn from, so no pretend research",
          await curiosity.read_up(fake_ask_factory(sure=False), "voice", "Sam", web=False) is None)

    # ---------------------------------------------------------------- a pretend day: sporadic sharing
    real_ask = soul._ask
    soul._ask = fake_ask_factory(sure=False)
    soul.local_endpoint = lambda: {"provider": "lm_studio"}
    c = curiosity._conn()            # a "poke around" day, so the count doesn't depend on which plan it happened to pick
    curiosity._put(c, "today", {"date": curiosity._today(), "plan": "wander", "topic": "", "label": "poking around", "reads_or_thoughts": 0,
                                "reward": 0.0, "shared": 0})
    c.commit()
    c.close()
    clock = [time.time()]
    real_time = time.time
    curiosity.time.time = lambda: clock[0]                     # hours pass between musings
    shared_flags = []
    for i in range(10):
        e = await soul.muse()
        print(f"      musing {i}: {(e or {}).get('activity')} {(e or {}).get('kind')} {'shared' if e and not e.get('quiet') else ''}" if e else f"      musing {i}: nothing")
        if e:
            shared_flags.append(not e.get("quiet"))
            if not e.get("quiet"):
                soul.react(e["id"], "seen")
        clock[0] += 1.5 * 3600
    curiosity.time.time = real_time
    n_shared = sum(shared_flags)
    print(f"      (10 musings over 15 hours: {len(shared_flags)} did something, {n_shared} brought to you)")
    check("a day of musing: only a couple are brought to you", len(shared_flags) >= 5 and 0 < n_shared <= 2, str(shared_flags))
    quiet = [j for j in soul.load()["journal"][-len(shared_flags):] if j.get("quiet")]
    check("the rest stay quiet (in the journal and notebook)", soul.public()["unseen"] <= 1 and len(quiet) == len(shared_flags) - n_shared)
    c = curiosity._conn()
    curiosity._nudge(c, last_share=time.time())
    c.commit()
    c.close()
    ok, _ = curiosity.consider_sharing({"id": "jtest", "kind": "found"}, None)
    check("hours apart: right after sharing, nothing more is shared", not ok)
    ok, _ = curiosity.consider_sharing({"id": "jask", "kind": "thought"}, None, asked=True)
    check("'What are you thinking about?' always gets an answer", ok)

    # ---------------------------------------------------------------- rewards
    c = curiosity._conn()
    c.execute("UPDATE notes SET journal_id = 'jlike' WHERE id = ?", (note["id"],))
    c.commit()
    tname = note["topic"]
    before = dict(c.execute("SELECT a, b FROM topics WHERE name = ?", (tname,)).fetchone())
    c.close()
    curiosity.reaction("jlike", "liked")
    c = curiosity._conn()
    after = dict(c.execute("SELECT a, b FROM topics WHERE name = ?", (tname,)).fetchone())
    c.execute("UPDATE topics SET last = 0, a = 9, b = 1 WHERE name = 'volcanoes'")
    c.execute("UPDATE topics SET last = 0, a = 1, b = 9, mentioned = '' WHERE name = 'sourdough'")
    c.commit()
    c.close()
    check("'Thanks!' on something it shared rewards that topic", after["a"] >= before["a"] + 1.4, f"{before} {after}")
    picks = [curiosity._sample_topic(curiosity._conn(), ["volcanoes", "sourdough"])["name"] for _ in range(300)]
    check("topics that paid off get picked more (Thompson sampling)", picks.count("volcanoes") > 2.5 * picks.count("sourdough"),
          f"{picks.count('volcanoes')} vs {picks.count('sourdough')}")
    c = curiosity._conn()
    a0 = c.execute("SELECT a FROM topics WHERE name = 'sourdough'").fetchone()[0]
    c.close()
    soul.learn_from_message("what do you know about sourdough discard?")
    c = curiosity._conn()
    a1 = c.execute("SELECT a FROM topics WHERE name = 'sourdough'").fetchone()[0]
    c.close()
    check("you bringing up a topic it reads about is a small reward", a1 > a0, f"{a0} {a1}")
    from app.services import work_state
    check("'what have you been reading about?' reaches its notebook",
          bool(work_state.OWN_RE.search("what have you been reading about lately?")) and "Grímsvötn" in curiosity.recent_note(10))
    soul._ask = real_ask

    # ---------------------------------------------------------------- old backlog of postcards
    s = soul.load()
    for i in range(5):
        s["journal"].append({"id": f"old{i}", "ts": time.time(), "activity": "wonder", "kind": "found", "text": f"old {i}", "seen": False})
    soul.save(s)
    raw = json.loads(soul.PATH.read_text())
    raw.pop("share_v", None)
    soul.PATH.write_text(json.dumps(raw))
    check("an old pile of unread postcards becomes just the newest one", soul.public(soul.load())["unseen"] == 1)

    # ---------------------------------------------------------------- understanding the question (Gemma)
    hist = [{"role": "user", "content": "Compare the Sony WH-1000XM6 and the Bose QuietComfort Ultra headphones"},
            {"role": "assistant", "content": "The **Sony WH-1000XM6** has slightly better noise cancelling and battery (30 h). "
                                             "The **Bose QuietComfort Ultra** is more comfortable and has better call quality."}]
    check("a follow-up needs understanding; a standalone question or a first message doesn't",
          turn_quality.needs_understanding("how much is the second one?", hist) and
          not turn_quality.needs_understanding("what's the capital of Peru", hist) and
          not turn_quality.needs_understanding("how much is it?", []))
    from app.services import warmup
    await warmup._warm()            # as the app does while you type
    t0 = time.time()
    u = await turn_quality.understand("how much is the second one?", hist)
    print(f"      (understood in {time.time() - t0:.1f}s: {u!r})")
    check("'how much is the second one?' → about the Bose", "bose" in u.lower() and "sony" not in u.lower(), u)

    # ---------------------------------------------------------------- labels from how the turn went
    msgs = [{"role": "system", "content": "router"}, {"role": "user", "content": "what is the boiling point of water at the top of Everest"}]
    turn = turn_quality.begin("s1", "what is the boiling point of water at the top of Everest")
    ex = dataset_recorder.capture(task="router", messages=msgs, response="Water boils at 100 °C everywhere.")
    noise = dataset_recorder.capture(task="soul", messages=msgs, response="a thought")
    check("the turn's model calls are tied to it (its own musings aren't)", ex in turn["examples"] and noise not in turn["examples"])
    turn_quality.finish(turn, "Water boils at 100 °C everywhere.", [])
    turn_quality.begin("s1", "no, that's wrong — it's lower up there because of the air pressure")
    lab = dataset_recorder._conn().execute("SELECT label, label_reason FROM llm_examples WHERE id = ?", (ex,)).fetchone()
    check("a correction labels them bad", lab["label"] == -1 and "corrected" in lab["label_reason"], str(dict(lab)))
    note = turn_quality.lessons_note("boiling point of water on Everest?")
    check("…and becomes a lesson for similar questions", "corrected" in note and "air pressure" in note, note)
    check("…not for unrelated ones", turn_quality.lessons_note("best pizza in Chicago") == "")

    turn = turn_quality.begin("s2", "give me a haiku about autumn")
    ex2 = dataset_recorder.capture(task="router", messages=msgs, response="Leaves fall...")
    turn_quality.finish(turn, "Crisp leaves drift and fall / ...", [])
    turn_quality.begin("s2", "perfect, thank you!")
    lab = dataset_recorder._conn().execute("SELECT label FROM llm_examples WHERE id = ?", (ex2,)).fetchone()
    check("thanks labels them good", lab["label"] == 1)

    turn = turn_quality.begin("s3", "what's the tallest mountain in Japan")
    ex3 = dataset_recorder.capture(task="router", messages=msgs, response="Mount Fuji")
    turn_quality.finish(turn, "Mount Fuji, at 3,776 m, is the tallest mountain in Japan.", [])
    turn_quality.begin("s3", "what's the tallest mountain in japan??")
    lab = dataset_recorder._conn().execute("SELECT label FROM llm_examples WHERE id = ?", (ex3,)).fetchone()
    check("asking the same thing again labels them bad", lab["label"] == -1)

    # ---------------------------------------------------------------- its own check (Gemma)
    soul.local_endpoint = lambda: {"provider": "lm_studio", "base_url": "http://127.0.0.1:1234/v1", "model": "google/gemma-4-12b-qat", "api_key": "lm-studio"}
    good = turn_quality.begin("s4", "what is the capital of Australia")
    turn_quality.finish(good, "The capital of Australia is Canberra.", [])
    bad = turn_quality.begin("s5", "what is the capital of Australia")
    exb = dataset_recorder.capture(task="router", messages=msgs, response="Sydney")
    turn_quality.finish(bad, "Here's a recipe for banana bread: mash three bananas, add flour and bake.", [])
    vg = await turn_quality.self_check(good["id"])
    vb = await turn_quality.self_check(bad["id"])
    check("its own check passes a good answer and fails an off-topic one", vg == "pass" and vb == "fail", f"{vg} {vb}")
    lab = dataset_recorder._conn().execute("SELECT label FROM llm_examples WHERE id = ?", (exb,)).fetchone()
    check("…and a failed check labels the turn's examples bad", lab["label"] == -1)
    st = turn_quality.stats()
    check("the Model Lab gets the counts", st.get("turns", 0) >= 5 and st.get("corrected", 0) >= 1 and st.get("lessons", 0) >= 1, str(st))

    # ---------------------------------------------------------------- in a real chat turn
    from app.agent.researcher import AgenticResearcher
    hist2 = [{"role": "user", "content": "tell me about the Eiffel Tower"},
             {"role": "assistant", "content": "The Eiffel Tower is a wrought-iron tower in Paris, finished in 1889 for the World's Fair."},
             {"role": "user", "content": "how tall is it?"}]
    seen = None
    async for ev in AgenticResearcher().run_research_stream(hist2):
        if ev.get("type") == "understood":
            seen = ev["content"]
            break
        if ev.get("type") in ("content", "tool_call"):
            break
    check("in a chat, 'how tall is it?' is understood as about the Eiffel Tower", seen and "eiffel" in seen.lower(), str(seen))

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
