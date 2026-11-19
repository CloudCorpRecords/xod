// ==============================================================
// XOD - turn-taking for hands-free conversation (conversation.js)
// Pure decisions, no DOM and no mic: when is a turn really over, and
// was that sound during a reply really the user interrupting? Kept
// separate from conversation.js so the rules can be tested on their
// own (tests/smoke/run_voice_turntaking_test.py drives this file
// through node) and so a real speech classifier can plug in behind
// classifySpeech() later (Phase 2: Silero VAD; the built-in energy
// classifier is the fallback until then).
//
// Why this exists (field report, 2026-11-19): the old end of turn was
// a fixed silence timer, 1300 ms listening and 500 ms while Xod was
// busy, so a normal mid-thought pause ended the turn and Xod answered
// half a sentence. And anything transcribed while Xod spoke, 2 words
// or more, counted as an interruption, so Xod heard its own voice (or
// the TV) and started answering itself.
// ==============================================================
(function (root) {
  // Ends mid-thought: a conjunction, article, preposition, auxiliary or
  // similar word the sentence cannot really end on. Wider than the old
  // DANGLING_RE in conversation.js, which this replaces.
  const DANGLING_RE = /\b(and|or|but|so|the|a|an|to|of|with|without|um|uh|like|because|that|if|my|for|from|into|by|at|on|in|then|is|are|was|were|be|been|being|about|it|its|their|his|her|our|your|can|could|will|would|shall|should|do|does|did|have|has|had|i|you|we|they|he|she|there|what|when|where|which|who|how|why|than|as|per|via|going|want|wants|need|needs|let|lets)\s*[.,]*$|(\.\.\.|…)\s*$/i;
  const STOP_RE = /^(okay[, ]+)?(stop|cancel|never ?mind|nevermind|shush|hush|quiet|be quiet|shut up|wait|hold on|pause)\b[\s.!,]*(please|xod|it)?[\s.!]*$/i;

  const words = (t) => (String(t || "").match(/[A-Za-z0-9']+/g) || []).length;

  // "complete": the words can stand as a finished thought.
  // "incomplete": they plainly cannot (dangling ending, trailing comma).
  // "unknown": no terminal punctuation either way; holds longer than
  // complete, shorter than incomplete.
  function completeness(text) {
    const t = String(text || "").trim();
    if (!t) return "unknown";
    if (DANGLING_RE.test(t)) return "incomplete";
    if (/[,;:\u2014-]\s*$/.test(t)) return "incomplete";
    if (/[.!?]["')\]]*\s*$/.test(t)) return "complete";
    return "unknown";
  }

  // How much sustained silence closes a turn, by what the words look
  // like. A mid-thought pause of 1.5 to 2.5 s must never close one:
  // incomplete gets the long hold (3500 ms), unknown 2400 ms, and even
  // a complete thought waits 1700 ms (1900 ms after a long utterance).
  function silenceNeededMs(text, utteranceMs = 0) {
    const c = completeness(text);
    if (c === "incomplete") return 3500;
    if (c === "unknown") return 2400;
    return utteranceMs > 20000 ? 1900 : 1700;
  }

  function turnClosed(silenceMs, text, utteranceMs = 0) {
    return silenceMs >= silenceNeededMs(text, utteranceMs);
  }

  // How much of what was just heard is words Xod itself just said:
  // its own voice coming back through the speakers.
  function echoOverlap(text, said) {
    const a = new Set(String(said || "").toLowerCase().match(/[a-z']+/g) || []);
    const b = String(text || "").toLowerCase().match(/[a-z']+/g) || [];
    if (!b.length || !a.size) return 0;
    return b.filter((w) => a.has(w)).length / b.length;
  }

  function isStopPhrase(text) {
    const t = String(text || "").trim();
    if (STOP_RE.test(t)) return true;
    // A short phrase that opens with the stop words ("wait, hold on", "stop, wait"):
    // the exact STOP_RE shape does not cover the trailing words, and a person
    // interrupting does not speak in regex.
    return words(t) <= 4 && /^(okay[, ]+)?(stop|wait|hold on)\b/i.test(t);
  }

  // A sound while Xod is speaking or thinking. Returns:
  //   "stop":      a real stop phrase; interrupt at once, on one word.
  //   "interrupt": confirmed as the user: 3 words or more, at or near
  //                the learned user level, and not Xod's own echo.
  //   "ignore":    everything else. Silence, never a spoken response.
  function classifyBargeIn({ text, level = 0, userLevel = 0, said = "" } = {}) {
    const t = String(text || "").trim();
    if (!t) return "ignore";
    if (isStopPhrase(t)) return "stop";
    if (words(t) < 3) return "ignore";
    if (echoOverlap(t, said) > 0.6) return "ignore";
    if (userLevel > 0 && level > 0 && level < userLevel * 0.55) return "ignore";
    return "interrupt";
  }

  // ------------------------------------------------------------ speech classifier (Phase 2 plug-in)
  // The interface a classifier implements: classify(rms, ctx) -> bool,
  // ctx = { floor, xodTalking }. Silero VAD registers here when it is
  // loaded; until then the built-in energy classifier decides, with
  // the same thresholds conversation.js has always used.
  function createEnergyClassifier() {
    return {
      kind: "energy",
      classify(rms, ctx = {}) {
        const floor = ctx.floor || 0.004;
        let on = Math.max(0.012, floor * 3.4);
        if (ctx.xodTalking) on = Math.max(on * 2.2, 0.03);
        return rms > on;
      },
    };
  }
  let registered = null;
  function registerSpeechClassifier(c) { registered = c && typeof c.classify === "function" ? c : null; }
  function activeClassifierKind() { return registered ? registered.kind || "custom" : "energy"; }
  function classifySpeech(rms, ctx = {}) {
    if (registered) return !!registered.classify(rms, ctx);
    return createEnergyClassifier().classify(rms, ctx);
  }

  const api = {
    DANGLING_RE, STOP_RE, words, completeness, silenceNeededMs, turnClosed,
    echoOverlap, isStopPhrase, classifyBargeIn,
    createEnergyClassifier, registerSpeechClassifier, activeClassifierKind, classifySpeech,
    FOLLOW_UP_MS: 6000,          // the follow-up window after an answer (was 9000 to 12000)
    WAKE_CONFIDENCE_MIN: 0.5,    // a wake transcript below this never opens a conversation
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.XodTurnTaking = api;
})(typeof window !== "undefined" ? window : globalThis);
