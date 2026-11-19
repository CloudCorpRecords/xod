"""
Talk voice redesign, repro tests for the two field defects reported on 2026-11-19
(desktop Xod 1.1.12): voice mode starts talking on its own, and it does not wait for
him to finish talking. Pure logic, no Mac, no Chrome, no model needed:

  .venv/bin/python tests/smoke/run_voice_turntaking_test.py

Covers, against app/static/js/turntaking.js (driven through node) and
app/services/speech_to_text.py wake_match:

  1. The pause set: mid-thought pauses of 1.5 to 2.5 s after an incomplete
     utterance must NOT close the turn. A complete utterance closes after the
     shorter sustained silence, an incomplete one only after the long hold.
  2. The echo and background set: Xod's own words coming back through the mic,
     quiet background speech, and 1 to 2 word noises must NOT interrupt or be
     answered. A real new question at the user's own level does interrupt.
  3. A real "stop" interrupts on one word, however quiet or echo-like.
  4. Wake word: the alone matches are gone ("Facade", "Hey God", "he's odd"
     never wake), the greeting is required, and a low Whisper confidence
     never wakes. A false wake must never produce spoken output, and this is
     the gate that decides it.
  5. The follow-up window is 6 s (it was 9 to 12 s).

XOD_TEST_ROOT points the test at another checkout (a base tree, for the
red/green proof); it defaults to this checkout.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("XOD_TEST_ROOT") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


NODE_HARNESS = r"""
const path = require("path");
let T = null;
try { T = require(path.join(process.argv[2], "app", "static", "js", "turntaking.js")); } catch (e) { console.log(JSON.stringify({ error: String(e) })); process.exit(2); }
const out = [];
const t = (name, fn) => { let ok = false, detail = ""; try { ok = !!fn(); } catch (e) { detail = String(e); } out.push({ name, ok, detail }); };

// 1. the pause set: 1.5 to 2.5 s mid-thought pauses must not close the turn
t("pause 2.5s after '...trip to Kyoto and' keeps the turn open", () => !T.turnClosed(2500, "I was thinking about the trip to Kyoto and", 6000));
t("pause 2.5s after '...the reason I wanted to ask is because' keeps the turn open", () => !T.turnClosed(2500, "So the main reason I wanted to ask is because", 7000));
t("pause 2.0s after '...will be like on' keeps the turn open", () => !T.turnClosed(2000, "Can you check what the weather will be like on", 5000));
t("pause 1.5s after a complete question keeps the turn open", () => !T.turnClosed(1500, "What time is it in Tokyo?", 3000));
t("a complete question closes after the sustained silence", () => T.turnClosed(1800, "What time is it in Tokyo?", 3000));
t("an incomplete thought closes only after the long hold", () => T.turnClosed(3600, "I was thinking about the trip to Kyoto and", 6000));
t("completeness: dangling ending is incomplete", () => T.completeness("I need to find the") === "incomplete");
t("completeness: final punctuation is complete", () => T.completeness("The meeting is at three.") === "complete");
t("completeness: a bare word is unknown, not complete", () => T.completeness("Hello") === "unknown");
t("an unknown ending gets a longer hold than a complete one", () => T.silenceNeededMs("Hello friend", 2000) > T.silenceNeededMs("Hello friend.", 2000));

// 2. the echo and background set must not interrupt
const said = "The tallest mountain in Africa is Kilimanjaro, in Tanzania.";
t("Xod's own words back through the mic do not interrupt", () => T.classifyBargeIn({ text: "the tallest mountain in Africa is Kilimanjaro", level: 0.08, userLevel: 0.05, said }) === "ignore");
t("background speech far quieter than the user does not interrupt", () => T.classifyBargeIn({ text: "and now the news at eleven tonight", level: 0.01, userLevel: 0.06, said: "" }) === "ignore");
t("a two word noise does not interrupt", () => T.classifyBargeIn({ text: "yeah okay", level: 0.06, userLevel: 0.05, said: "" }) === "ignore");
t("a real new question at the user's level does interrupt", () => T.classifyBargeIn({ text: "actually what about tomorrow instead", level: 0.055, userLevel: 0.05, said }) === "interrupt");

// 3. a real stop interrupts on one word
t("'stop' interrupts on one word", () => T.classifyBargeIn({ text: "stop", level: 0.01, userLevel: 0.06, said }) === "stop");
t("'wait, hold on' interrupts at once", () => T.classifyBargeIn({ text: "wait, hold on", level: 0.05, userLevel: 0.05, said: "" }) === "stop");

// 5. follow-up window and the classifier plug-in interface (Phase 2 lands behind it)
t("the follow-up window is 6 seconds", () => T.FOLLOW_UP_MS === 6000);
t("an energy classifier is the built-in speech classifier", () => { const c = T.createEnergyClassifier(); return c.kind === "energy" && c.classify(0.05, { floor: 0.004, xodTalking: false }) === true && c.classify(0.001, { floor: 0.004, xodTalking: false }) === false; });
t("a real speech classifier can be registered behind the interface", () => { T.registerSpeechClassifier({ kind: "silero", classify: () => true }); const ok = T.activeClassifierKind() === "silero"; T.registerSpeechClassifier(null); return ok && T.activeClassifierKind() === "energy"; });

console.log(JSON.stringify(out));
"""


def main():
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(NODE_HARNESS)
        harness = f.name
    try:
        p = subprocess.run(["node", harness, str(ROOT)], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(harness)
    if p.returncode != 0 or not p.stdout.strip():
        check("turntaking.js loads in node", False, (p.stdout + p.stderr)[:300])
    else:
        for r in json.loads(p.stdout.strip().splitlines()[-1]):
            check(r["name"], r["ok"], r.get("detail", ""))

    # 4. the wake word, in Python
    try:
        from app.services import speech_to_text as stt
        cases = [
            ("Hey Xod, what time is it?", 2.0, None, True),
            ("Hey Zod", 1.0, None, True),
            ("Hey Xod, what time is it?", 2.0, 0.9, True),
            ("Hey Xod, what time is it?", 2.0, 0.2, False),   # low confidence never wakes
            ("Facade", 1.0, None, False),                      # alone match, removed
            ("Hey God", 1.0, None, False),                     # alone match, removed
            ("He's odd", 1.0, None, False),                    # alone match, removed
            ("Xod, what time is it?", 2.0, None, False),       # the greeting is required
            ("Hey Siri, what is the weather", 2.0, None, False),
            ("Hey there, how are you doing", 2.0, None, False),
            ("I told Todd the facade was odd", 3.0, None, False),
        ]
        for text, secs, conf, want in cases:
            r = stt.wake_match(text, secs, conf)
            check(f"wake: {text!r} conf={conf} -> {'wakes' if want else 'ignored'}", r["wake"] == want, json.dumps(r))
        r = stt.wake_match("Hey Xod, what time is it?", 2.0, 0.9)
        check("wake: the rest of the sentence survives", "what time" in r["rest"].lower(), json.dumps(r))
    except Exception as e:
        check("speech_to_text.wake_match imports and runs", False, str(e)[:300])

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)} passed; failing: {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
