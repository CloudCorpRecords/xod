"""
Xod's own research notebook, daily plan, rewards and sharing (services/curiosity.py, soul.py), and understanding
questions plus labelling its training data by how turns went (services/turn_quality.py).

In a throwaway data folder and dataset. The companion's reading is faked (no browser) so the sharing limits can
be checked over a pretend day; the question rewrite and the self-check use LM Studio's Gemma (never a cloud model):
  .venv/bin/python tests/smoke/run_inner_life_test.py
"""
import asyncio
import json
import os
import random
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


FAKE = {
    "volcanoes": [("I went looking into volcanoes: Iceland's Fagradalsfjall erupted from a dike that rose 15 km in days.",
                   "the dike was about 15 km long | eruptions began in March 2021", "why do some fissures reopen years later?", "plate tectonics"),
                  ("I got curious about volcanoes again: lava from Kilauea can reach 1,170 °C, hot enough to glow orange-yellow.",
                   "Kilauea lava is about 1,170 °C | colour shows temperature", "how do volcanologists measure lava temperature?", "none")],
    "sourdough": [("I went looking into sourdough: the tang comes from lactic and acetic acid made by bacteria in the starter.",
                   "lactobacilli make lactic acid | colder proofing makes it more sour", "why does rye ferment faster than wheat?", "rye bread")],
}


async def fake_ask(system, user, max_tokens=160):
    if "LEARNED" in user:
        for topic, opts in FAKE.items():
            if topic in user:
                l, f, n, sp = random.choice(opts)
                return f"LEARNED: {l}\nFACTS: {f}\nNEXT: {n}\nSPARK: {sp}"
        return "LEARNED: I was thinking about plate tectonics: plates move about as fast as fingernails grow.\nFACTS: a few cm a year | driven by mantle convection\nNEXT: what stops a plate?\nSPARK: none"
    if "PROMPT:" in user:
        return "You could turn last week's bread notes into a baking schedule.\nPROMPT: Make me a sourdough baking schedule"
    return "I wonder if the cursor gets tired of blinking."


async def main():
    from app.config import settings
    settings.llm_provider, settings.llm_base_url, settings.llm_model = "lm_studio", "http://127.0.0.1:1234/v1", "google/gemma-4-12b-qat"
    from app.services import soul, curiosity, turn_quality, dataset_recorder

    # A person who keeps talking about volcanoes and sourdough
    for t in ["tell me about volcanoes in Iceland", "volcanoes and lava flows", "my sourdough starter smells odd",
              "sourdough hydration tips", "volcanoes near Naples", "sourdough rye loaf"]:
        soul.learn_from_message(t)
    check("its interests come from what you talk about", {"volcanoes", "sourdough"} <= set(soul.interests()), str(soul.interests()))

    # ---------------------------------------------------------------- today
    day = curiosity.today()
    check("it picks a plan for the day", day["plan"] in curiosity.PLANS and day["label"], str(day))
    check("…and keeps it all day", curiosity.today()["plan"] == day["plan"])

    # ---------------------------------------------------------------- reading up, notebook, novelty
    FAKE["volcanoes"] = [FAKE["volcanoes"][0]]
    vol = {"name": "volcanoes", "origin": "you", "thread": ""}
    n1 = await curiosity.read_up(fake_ask, "voice", "Sam", web=False, topic=vol)
    check("a read becomes a notebook note with facts and a follow-up question",
          n1 and len(n1["facts"]) == 2 and n1["next"].endswith("?"), json.dumps(n1)[:300])
    c = curiosity._conn()
    t = dict(c.execute("SELECT * FROM topics WHERE name = 'volcanoes'").fetchone())
    own = [r["name"] for r in c.execute("SELECT name FROM topics WHERE origin = 'itself'").fetchall()]
    c.close()
    check("the follow-up becomes a thread it will pull on next time", t["thread"] == n1["next"], t["thread"])
    check("a related topic it noticed joins its own list", "plate tectonics" in own or "rye bread" in own, str(own))
    same = {"name": "volcanoes", "origin": "you", "thread": ""}
    n2 = await curiosity.read_up(fake_ask, "voice", "Sam", web=False, topic=same)
    print(f"      (novelty: first {n1['novelty']}, repeat {n2['novelty']})")
    check("reading the same thing again is worth less (novelty from embeddings)", n2["novelty"] < 0.35 < n1["novelty"] and n2["reward"] < n1["reward"],
          f"{n1['novelty']} {n2['novelty']}")

    # ---------------------------------------------------------------- a pretend day of musing: sporadic sharing
    real_ask = soul._ask
    soul._ask = fake_ask
    soul.local_endpoint = lambda: {"provider": "lm_studio"}     # its thoughts would use the local model
    s = soul.load()
    s["settings"]["explore_web"] = False
    soul.save(s)
    clock = [time.time()]
    real_time = time.time
    curiosity.time.time = lambda: clock[0]                     # hours pass between musings
    shared_flags = []
    for i in range(10):
        e = await soul.muse()
        if e:
            shared_flags.append(not e.get("quiet"))
            if not e.get("quiet"):
                soul.react(e["id"], "seen")                     # you read it, so the next one isn't blocked by an unread one
        clock[0] += 1.5 * 3600
    curiosity.time.time = real_time
    n_shared = sum(shared_flags)
    print(f"      (10 musings over 15 hours: {n_shared} brought to you)")
    check("ten musings in a day: only a couple are brought to you", len(shared_flags) >= 8 and 0 < n_shared <= 2, str(shared_flags))
    check("the rest stay quiet (read, in the journal and notebook)", soul.public()["unseen"] <= 1 and len(curiosity.notes(50)) >= 3)

    c = curiosity._conn()
    curiosity._nudge(c, last_share=time.time())
    c.commit()
    c.close()
    pending_entry = {"id": "jtest", "kind": "found"}
    ok, _ = curiosity.consider_sharing(pending_entry, None)
    check("hours apart: right after sharing, nothing more is shared", not ok)
    ok, _ = curiosity.consider_sharing({"id": "jask", "kind": "thought"}, None, asked=True)
    check("'What are you thinking about?' always gets an answer", ok)

    # ---------------------------------------------------------------- rewards
    c = curiosity._conn()
    c.execute("UPDATE notes SET journal_id = 'jlike' WHERE id = ?", (n1["id"],))
    c.commit()
    before = dict(c.execute("SELECT a, b FROM topics WHERE name = 'volcanoes'").fetchone())
    c.close()
    curiosity.reaction("jlike", "liked")
    c = curiosity._conn()
    after = dict(c.execute("SELECT a, b FROM topics WHERE name = 'volcanoes'").fetchone())
    c.close()
    check("'Thanks!' on something it shared rewards that topic", after["a"] >= before["a"] + 1.4, f"{before} {after}")
    c = curiosity._conn()
    c.execute("UPDATE topics SET last = 0, a = 9, b = 1 WHERE name = 'volcanoes'")
    c.execute("UPDATE topics SET last = 0, a = 1, b = 9, mentioned = '' WHERE name = 'sourdough'")
    c.commit()
    c.close()
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
          bool(work_state.OWN_RE.search("what have you been reading about lately?")) and "volcanoes" in curiosity.recent_note())
    soul._ask = real_ask

    # ---------------------------------------------------------------- old backlog of postcards
    s = soul.load()
    for i in range(5):
        s["journal"].append({"id": f"old{i}", "ts": time.time(), "activity": "wonder", "kind": "found", "text": f"old {i}", "seen": False})
    s.pop("share_v", None)
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
    check("the Model Lab gets the counts", st.get("turns", 0) >= 5 and st.get("corrected") == 1 and st.get("lessons") == 1, str(st))

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
