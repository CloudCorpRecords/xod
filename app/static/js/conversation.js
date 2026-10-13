// ==============================================================
// XOD - conversation (hands-free)
// "Hey Xod" (or the mic button, or ⌃⌥Space in the desktop app) starts
// a back-and-forth: you talk, Xod answers out loud, and it keeps
// listening for a follow-up without the wake word, until you say
// you're done or it's been quiet for a while.
//
// Everything runs on this Mac: the mic is read in the page, speech is
// found by its loudness (with a floor that follows the room's noise),
// each utterance is transcribed by local Whisper (/api/voice/...), and
// answers are spoken by speech.js. Talking over Xod turns it down at
// once and, if it was really you and not its own echo, stops it and
// listens. The companion in the dock hears you (it bobs with your
// voice), thinks, and talks with its mouth following the sound.
// ==============================================================
(() => {
  const now = () => performance.now();
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const desktop = window.xodDesktop || null;     // the desktop app's bridge (electron/preload.js)
  const log = (...a) => { if (window.__xodTalkLog) window.__xodTalkLog.push([Math.round(performance.now()), ...a]); };

  // ---------------------------------------------------------------- what it says without thinking
  const FILLERS = {
    search: ["Let me look that up.", "One sec, checking.", "Let me check.", "Looking that up."],
    other: ["On it.", "Give me a moment.", "Working on it."],
  };
  const GOODBYES = ["Anytime.", "Talk soon.", "Happy to help."];
  const END_RE = /^(okay[, ]+)?(that'?s (all|it|everything)|that is (all|it)|goodbye|bye( bye)?|good ?night|stop listening|we'?re done|i'?m (done|good)|end (the )?(conversation|chat)|thanks?,? (that'?s|that is) (all|it)|thank you,? (that'?s|that is) (all|it)|no,? (that'?s|that is) (all|it)|nothing else|no thanks?)\b/i;
  const STOP_RE = /^(okay[, ]+)?(stop|cancel|never ?mind|nevermind|shush|hush|quiet|be quiet|shut up|wait|hold on|pause)\b[\s.!,]*(please|xod|it)?[\s.!]*$/i;
  const REPEAT_RE = /^(say (that|it) again|repeat (that|it)|what did you (just )?say|come again|pardon|sorry,? what)\b/i;
  const THANKS_RE = /^(thanks|thank you|cheers|perfect|great|awesome|cool)[\s.!,]*(xod)?[\s.!]*$/i;

  // ---------------------------------------------------------------- state
  const C = {
    state: "off",          // off | wake | listening | transcribing | thinking | speaking
    active: false,         // in a conversation (not just waiting for the wake word)
    wakeOn: false,         // listening for "Hey Xod" between conversations
    muted: false,
    followUntil: 0,        // the follow-up window: talk before this, or it ends
    turn: null,            // { text, filler, content, startedAt }
    lastAnswer: "",
    lastHeardAt: 0,
  };

  // ---------------------------------------------------------------- the microphone
  const M = { stream: null, ctx: null, proc: null, rate: 48000, opening: null };
  const V = {                 // voice activity
    floor: 0.004, speaking: false, above: 0, quietMs: 0,
    preroll: [], seg: [], segRms: [], segStart: 0, frameMs: 43,
    partialAt: 0, partialBusy: false, segId: 0,
  };

  async function openMic() {
    if (M.stream) return true;
    if (M.opening) return M.opening;
    M.opening = (async () => {
      try {
        M.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false } });   // no auto-gain: it lifts a far-off TV to your level
      } catch (e) {
        toast("Xod can't hear you: microphone access is blocked. Allow it in System Settings → Privacy & Security → Microphone.");
        return false;
      }
      M.ctx = new AudioContext();
      M.rate = M.ctx.sampleRate;
      const src = M.ctx.createMediaStreamSource(M.stream);
      M.proc = M.ctx.createScriptProcessor(2048, 1, 1);
      V.frameMs = (2048 / M.rate) * 1000;
      M.proc.onaudioprocess = (e) => { try { onFrame(new Float32Array(e.inputBuffer.getChannelData(0))); } catch (err) { console.warn("Xod conversation:", err); } };
      src.connect(M.proc);
      // ScriptProcessor only runs when connected to an output; a silent gain keeps it from being heard.
      const mute = M.ctx.createGain();
      mute.gain.value = 0;
      M.proc.connect(mute);
      mute.connect(M.ctx.destination);
      return true;
    })();
    const ok = await M.opening;
    M.opening = null;
    return ok;
  }

  function closeMic() {
    try { M.proc && M.proc.disconnect(); } catch (_) {}
    try { M.stream && M.stream.getTracks().forEach((t) => t.stop()); } catch (_) {}
    try { M.ctx && M.ctx.close(); } catch (_) {}
    M.stream = M.ctx = M.proc = null;
    V.speaking = false; V.seg = []; V.preroll = [];
  }

  // Every ~43 ms of sound: is someone talking?
  function onFrame(buf) {
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const rms = Math.sqrt(sum / buf.length);
    const xodTalking = !!(window.XodSpeech && XodSpeech.audioPlaying());
    if (!V.speaking) {
      // The noise floor follows the room: down quickly, up slowly.
      V.floor = rms < V.floor ? V.floor * 0.9 + rms * 0.1 : V.floor * 0.998 + rms * 0.002;
      V.floor = clamp(V.floor, 0.0015, 0.05);
    }
    let on = Math.max(0.012, V.floor * 3.4), off = Math.max(0.008, V.floor * 2.1);
    if (xodTalking) { on = Math.max(on * 2.2, 0.03); off = Math.max(off * 1.6, 0.02); }   // its own echo isn't you
    if (C.active && C.state === "listening") {
      try { window.XodMascot && XodMascot.hear(V.speaking ? rms : rms * 0.4); } catch (_) {}
      setLevel(rms);
    }
    if (C.muted || C.state === "off") return;

    if (!V.speaking) {
      V.preroll.push(buf);
      const keep = Math.ceil(450 / V.frameMs);
      if (V.preroll.length > keep) V.preroll.splice(0, V.preroll.length - keep);
      V.above = rms > on ? V.above + 1 : 0;
      if (V.above >= (xodTalking ? 5 : 3)) {
        V.speaking = true;
        V.seg = V.preroll.slice();
        V.segRms = [];
        V.preroll = [];
        V.segStart = now();
        V.quietMs = 0;
        V.segId++;
        log("speech-start", C.state, +rms.toFixed(4), +on.toFixed(4), xodTalking);
        speechStarted();
      }
      return;
    }
    V.seg.push(buf);
    V.segRms.push(rms);
    V.quietMs = rms < off ? V.quietMs + V.frameMs : 0;
    const dur = now() - V.segStart;
    const endAfter = C.state === "wake" ? 550 : (xodTalking || C.state === "thinking") ? 500 : 950;
    const maxMs = C.state === "wake" ? 7000 : 25000;
    if (V.quietMs >= endAfter || dur > maxMs) {
      const seg = V.seg, level = loudness(V.segRms);
      V.speaking = false; V.seg = []; V.segRms = []; V.above = 0;
      log("speech-end", C.state, Math.round(dur - V.quietMs), +level.toFixed(4));
      speechEnded(seg, dur - V.quietMs, level);
    } else if (C.active && C.state === "listening" && dur > 1100 && now() - V.partialAt > 1100 && !V.partialBusy) {
      livePartial(V.seg.slice(), V.segId);
    }
  }

  // 16 kHz mono WAV, which is what Whisper wants.
  function wav(chunks) {
    const total = chunks.reduce((n, a) => n + a.length, 0);
    const all = new Float32Array(total);
    let o = 0;
    for (const a of chunks) { all.set(a, o); o += a.length; }
    const out = 16000, n = Math.floor(total * out / M.rate);
    const view = new DataView(new ArrayBuffer(44 + n * 2));
    const str = (off, t) => { for (let i = 0; i < t.length; i++) view.setUint8(off + i, t.charCodeAt(i)); };
    str(0, "RIFF"); view.setUint32(4, 36 + n * 2, true); str(8, "WAVE"); str(12, "fmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, out, true); view.setUint32(28, out * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    str(36, "data"); view.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) {
      const v = clamp(all[Math.min(total - 1, Math.floor(i * M.rate / out))], -1, 1);
      view.setInt16(44 + i * 2, v * 0x7fff, true);
    }
    return new Blob([view], { type: "audio/wav" });
  }

  async function post(path, blob) {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "audio/wav" }, body: blob });
    return res.json();
  }

  // How loud you are when you talk to Xod, learned from what it actually took as your words. Speech
  // much quieter than that is someone else: a TV, a call next door, people across the room.
  function loudness(levels) {
    if (!levels.length) return 0;
    const top = levels.slice().sort((a, b) => b - a).slice(0, Math.max(3, Math.ceil(levels.length * 0.3)));
    return top.reduce((a, b) => a + b, 0) / top.length;
  }
  let voiceLevel = 0;
  try { voiceLevel = Number(localStorage.getItem("xod.voiceLevel")) || 0; } catch (_) {}
  let ignoredInARow = 0;
  function learnVoice(level) {
    if (!level) return;
    ignoredInARow = 0;
    if (voiceLevel) level = clamp(level, voiceLevel * 0.5, voiceLevel * 2);   // one shout or whisper doesn't reset it
    voiceLevel = voiceLevel ? voiceLevel * 0.75 + level * 0.25 : level;
    try { localStorage.setItem("xod.voiceLevel", voiceLevel.toFixed(5)); } catch (_) {}
  }
  function tooQuiet(level) {
    if (!(voiceLevel > 0 && level > 0 && level < voiceLevel * 0.3)) return false;
    // Ignored three times running while it's waiting for you: maybe you're further from the mic now.
    if (C.state === "listening" && ++ignoredInARow >= 3) { voiceLevel *= 0.7; ignoredInARow = 0; }
    return true;
  }
  // Ends mid-thought ("…and", "…so the", "…because"): wait a moment for the rest instead of answering half.
  const DANGLING_RE = /\b(and|or|but|so|the|a|an|to|of|with|um|uh|like|because|that|if|my|for|then|is|are|about)\s*[.,…-]*$/i;

  // ---------------------------------------------------------------- hearing something
  function speechStarted() {
    C.lastHeardAt = now();
    if (!C.active) return;                                   // wake mode: nothing to show yet
    if (C.state === "listening") {
      C.followUntil = 0;
      if (C.pendingTimer) { clearTimeout(C.pendingTimer); C.pendingTimer = null; }   // the rest of the sentence
      caption("you", C.pendingText ? C.pendingText + "…" : "…", true);
      setState("listening", "Listening…");
    } else if (window.XodSpeech && XodSpeech.audioPlaying()) {
      XodSpeech.duck(true);                                  // you might be talking over it: get out of the way
    }
  }

  async function livePartial(chunks, id) {
    V.partialAt = now();
    V.partialBusy = true;
    try {
      const r = await post("/api/voice/transcribe?partial=1", wav(chunks));
      if (r.text && V.speaking && id === V.segId && C.state === "listening") caption("you", r.text + "…", true);
    } catch (_) {}
    V.partialBusy = false;
  }

  async function speechEnded(chunks, ms, level = 0) {
    if (ms < 280) { if (window.XodSpeech) XodSpeech.duck(false); return; }   // a cough, a click
    if (tooQuiet(level)) {                                    // someone else, further away
      log("ignored: quieter than your voice", +level.toFixed(4), +voiceLevel.toFixed(4));
      if (window.XodSpeech) XodSpeech.duck(false);
      if (C.state === "listening") finishPending() || setState("listening", "Listening…");
      return;
    }
    if (C.state === "wake") return wakeCheck(chunks, ms);
    if (!C.active) return;
    const during = C.state;                                   // listening | thinking | speaking
    const xodWasTalking = !!(window.XodSpeech && XodSpeech.audioPlaying());
    if (during === "listening") setState("transcribing", "Got it…");
    let text = "", conf = 1;
    try {
      const r = await post("/api/voice/transcribe", wav(chunks));
      text = (r.text || "").trim();
      if (typeof r.confidence === "number") conf = r.confidence;
      if (r.dropped) log("dropped", r.dropped);
    } catch (_) {}
    text = text.replace(/^\[.*?\]$|^\(.*?\)$/, "").trim();       // Whisper's "[BLANK_AUDIO]", "(music)"
    log("heard", during, text, xodWasTalking, conf);
    if (during === "listening") {
      if (!words(text)) { if (!finishPending()) backToListening(true); return; }
      learnVoice(level);
      if (C.pendingText) { text = C.pendingText + " " + text; C.pendingText = ""; }
      if (conf < 0.35 && words(text) <= 6) {                  // it isn't sure what it heard: ask, don't guess
        caption("you", text + " (?)");
        say("Sorry, I didn't catch that. Could you say it again?");
        backToListening(false);
        return;
      }
      if (DANGLING_RE.test(text) && !/\?\s*$/.test(text)) {
        C.pendingText = text.replace(/[.…]+\s*$/, "");
        caption("you", C.pendingText + "…", true);
        setState("listening", "Go on…");
        C.pendingTimer = setTimeout(() => { C.pendingTimer = null; finishPending(); }, 1600);
        return;
      }
      return handle(text);
    }
    // Something was said while Xod was busy or talking.
    if (!words(text) || isEcho(text)) { if (window.XodSpeech) XodSpeech.duck(false); return; }
    if (STOP_RE.test(text) || END_RE.test(text)) return handle(text);
    if (xodWasTalking || (window.XodSpeech && XodSpeech.audioPlaying())) {
      if (words(text) >= 2) return handle(text, { interrupt: true });    // "actually, what about tomorrow?"
      if (window.XodSpeech) XodSpeech.duck(false);
    }
    // While it's still thinking, a clear new question replaces the old one ("Actually, …", "Wait, …",
    // or anything said as a question). Other chatter in the room is left alone.
    if (words(text) >= 3 && (/^(actually|wait|no|instead|sorry|hmm|oh|also|and|but|what about|how about)\b/i.test(text) || /\?\s*$/.test(text))) {
      return handle(text, { interrupt: true });
    }
  }

  const words = (t) => (t.match(/[A-Za-z0-9']+/g) || []).length;

  // A sentence it was holding for the rest ("…and"): nothing more came, so take it as it is.
  function finishPending() {
    const t = C.pendingText;
    if (!t || !C.active || V.speaking) return false;
    C.pendingText = "";
    handle(t);
    return true;
  }

  // Is this just Xod's own voice coming back through the mic?
  function isEcho(text) {
    const said = ((window.XodSpeech && XodSpeech.saying()) || "") + " " + (C.recentSaid || "");
    const a = new Set(said.toLowerCase().match(/[a-z']+/g) || []);
    const b = text.toLowerCase().match(/[a-z']+/g) || [];
    if (!b.length || !a.size) return false;
    const shared = b.filter((w) => a.has(w)).length;
    return shared / b.length > 0.6;
  }

  async function wakeCheck(chunks, ms) {
    if (ms > 7000) return;
    let r;
    try { r = await post("/api/voice/wake", wav(chunks)); } catch (_) { return; }
    if (!r || !r.wake || C.active) return;
    await start({ source: "wake", quiet: !!(r.rest && words(r.rest) >= 2) });
    if (r.rest && words(r.rest) >= 2) handle(r.rest);      // "Hey Xod, what's the weather tomorrow?" in one breath
  }

  // ---------------------------------------------------------------- what you said
  async function handle(text, { interrupt = false } = {}) {
    if (!C.active) return;
    caption("you", text);
    C.lastHeardAt = now();
    C.followUntil = 0;                       // it's not waiting any more: no timing out mid-thought
    let busy = false;
    try { busy = typeof isStreaming !== "undefined" && !!isStreaming; } catch (_) {}
    // A new question while it's still working or talking replaces the old one.
    interrupt = interrupt || busy || !!(window.XodSpeech && XodSpeech.speaking());
    if (END_RE.test(text)) {
      stopEverything();
      say(pick(GOODBYES));
      setState("speaking", "Bye for now");
      C.endAfterSpeech = true;
      return;
    }
    if (STOP_RE.test(text)) {
      stopEverything();
      chime("listen");
      backToListening();
      return;
    }
    if (interrupt && !REPEAT_RE.test(text) && !THANKS_RE.test(text)) {
      stopEverything();
      // Let the cancelled reply finish tidying up before the new question goes out.
      setState("thinking", "Okay…");
      setTimeout(() => ask(text), 350);
      return;
    }
    if (REPEAT_RE.test(text) && C.lastAnswer) {
      setState("speaking", "Saying it again");
      window.XodSpeech && XodSpeech.speakText(C.lastAnswer);
      return;
    }
    if (THANKS_RE.test(text)) {
      say(pick(["You're welcome.", "Anytime.", "Glad to help."]));
      setState("speaking", "");
      return;
    }
    if (interrupt) stopEverything();
    setState("transcribing", "Got it…");
    // Quick computer commands ("open Notes", "volume 40") don't need the model.
    if (words(text) <= 7) {
      try {
        const r = await (await fetch("/api/voice/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: text, speak_back: false, safe_only: true }) })).json();
        if (r && r.success && r.fastpath) {
          say(r.spoken || "Done.");
          setState("speaking", "Done");
          return;
        }
      } catch (_) {}
    }
    ask(text);
  }

  function ask(text) {
    if (!C.active) return;
    C.turn = { text, filler: false, content: false, startedAt: now() };
    setState("thinking", "Thinking…");
    window.__xodVoiceTurn = true;
    try { quickSendPrompt(text); } finally { setTimeout(() => { window.__xodVoiceTurn = false; }, 400); }
    clearTimeout(C.slowTimer);
    C.slowTimer = setTimeout(() => {           // nothing yet after a while: say so, once
      if (C.turn && !C.turn.filler && !C.turn.content && C.state === "thinking") { C.turn.filler = true; say(pick(FILLERS.other)); }
    }, 4500);
  }

  function say(text) {
    C.recentSaid = text;
    window.XodSpeech && XodSpeech.say(text);
  }

  function stopEverything() {
    clearTimeout(C.slowTimer);
    try { window.XodSpeech && XodSpeech.stop(); } catch (_) {}
    try { if (typeof isStreaming !== "undefined" && isStreaming) stopResearch(); } catch (_) {}
  }

  // ---------------------------------------------------------------- following the answer
  window.addEventListener("xod:agent", (e) => {
    if (!C.active || !C.turn) return;
    const ev = e.detail || {};
    if (ev.type === "tool_call" && !C.turn.filler && !C.turn.content) {
      C.turn.filler = true;
      say(pick(/search|browse|web|fetch|news/i.test(ev.name || "") ? FILLERS.search : FILLERS.other));
    }
    if (ev.type === "status" && C.state === "thinking" && ev.content) setState("thinking", ev.content.replace(/^•\s*/, ""));
    if (ev.type === "content") { C.turn.content = true; clearTimeout(C.slowTimer); }
    if (ev.type === "done" || ev.type === "error") {
      clearTimeout(C.slowTimer);
      const bubble = [...document.querySelectorAll("#chatMessages .prose-custom")].pop();
      if (bubble) C.lastAnswer = bubble.innerText.split(/\n\s*(Sources|Source links)\b/)[0];
      if (ev.type === "error") { chime("error"); }
    }
  });
  window.addEventListener("xod:speech", (e) => {
    const d = e.detail || {};
    if (!C.active) return;
    if (d.type === "piece") {
      C.recentSaid = d.text;
      caption("xod", d.text);
      if (C.state === "thinking" || C.state === "transcribing") setState("speaking", "Speaking · say “stop” to interrupt");
    }
  });

  // The turn is over when Xod has stopped both working and talking.
  setInterval(() => {
    if (!C.active) return;
    let busy = false;
    try { busy = typeof isStreaming !== "undefined" && !!isStreaming; } catch (_) {}
    const talking = !!(window.XodSpeech && XodSpeech.speaking());
    if ((C.state === "thinking" || C.state === "speaking") && !busy && !talking && !V.speaking) {
      if (!C.quietSince) C.quietSince = now();
      if (now() - C.quietSince > 350) {
        C.quietSince = 0;
        if (C.endAfterSpeech) { C.endAfterSpeech = false; end("bye"); return; }
        chime("listen");
        backToListening(true);
      }
    } else C.quietSince = 0;
    placeDock();
    if (C.state === "listening" && C.followUntil && !V.speaking) {
      const left = C.followUntil - now();
      setFollow(clamp(left / C.followMs, 0, 1));
      if (left <= 0) end("quiet");
    }
  }, 120);

  function backToListening(followUp = false) {
    C.turn = null;
    setState("listening", followUp ? pick(["Anything else?", "Listening…", "Go ahead…"]) : "Listening…");
    C.followMs = followUp ? 9000 : 12000;
    C.followUntil = now() + C.followMs;
    setFollow(1);
  }

  // The short lines it says without thinking are made ahead of time, in one background request
  // (many at once would queue your question behind them).
  let linesPreparedAt = 0;
  function prepareLines() {
    if (Date.now() - linesPreparedAt < 10 * 60 * 1000) return;
    linesPreparedAt = Date.now();
    fetch("/api/speech/prepare", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texts: [...FILLERS.search, ...FILLERS.other, ...GOODBYES, "The rest is on screen.", "You're welcome.", "Glad to help."] }) }).catch(() => {});
  }

  // ---------------------------------------------------------------- starting and ending
  async function start({ source = "button", quiet = false } = {}) {
    if (C.active) return;
    if (!(await openMic())) { setState("off"); return; }
    C.active = true;
    C.endAfterSpeech = false;
    stopEverything();
    showDock(source);
    fetch("/api/llm/warm?voice=1", { method: "POST" }).catch(() => {});
    fetch("/api/voice/prepare", { method: "POST" }).catch(() => {});
    prepareLines();
    if (!quiet) chime("start");
    try { window.XodMascot && XodMascot.setVoiceMood("listening"); } catch (_) {}
    backToListening(false);
    if (desktop) try { desktop.setListening(true); } catch (_) {}
  }

  function end(reason = "user") {
    if (!C.active) return;
    C.active = false;
    C.turn = null;
    clearTimeout(C.slowTimer);
    if (reason === "user") stopEverything();
    chime(reason === "quiet" ? "doze" : "end");
    try { window.XodMascot && XodMascot.setVoiceMood(null); } catch (_) {}
    if (reason === "quiet") { try { XodMascot.puppet(dockMascot())?.fidget("stretch"); } catch (_) {} }
    hideDock();
    if (C.wakeOn) setState("wake");
    else { setState("off"); closeMic(); }
    if (desktop) try { desktop.setListening(C.wakeOn); } catch (_) {}
  }

  function toggle(source = "button") { if (C.active) end("user"); else start({ source }); }

  async function setWake(on) {
    C.wakeOn = !!on;
    document.body.classList.toggle("xod-wake-on", C.wakeOn);
    if (C.wakeOn && !C.active) {
      fetch("/api/voice/prepare", { method: "POST" }).catch(() => {});
      prepareLines();
      if (await openMic()) setState("wake");
      else { C.wakeOn = false; document.body.classList.remove("xod-wake-on"); }
    }
    if (!C.wakeOn && !C.active) { setState("off"); closeMic(); }
    if (desktop) try { desktop.setListening(C.wakeOn || C.active); } catch (_) {}
  }

  // ---------------------------------------------------------------- the dock
  let dock = null;
  const dockMascot = () => dock && dock.querySelector(".xod-mascot--talk");

  function showDock(source) {
    if (!dock) {
      dock = document.createElement("div");
      dock.id = "talkDock";
      dock.className = "talk-dock";
      dock.setAttribute("role", "dialog");
      dock.setAttribute("aria-label", "Conversation with Xod");
      dock.innerHTML = `
        <div class="talk-stage">
          <span class="talk-ring"></span><span class="talk-ring talk-ring--2"></span>
          <span class="xod-mascot xod-mascot--talk" data-mood="listening"></span>
        </div>
        <div class="talk-body">
          <p class="talk-state" aria-live="polite">Listening…</p>
          <p class="talk-caption talk-caption--you"></p>
          <p class="talk-caption talk-caption--xod"></p>
          <div class="talk-follow"><span></span></div>
        </div>
        <div class="talk-actions">
          <button type="button" data-talk="mute" title="Pause listening"><i data-lucide="mic" class="size-4"></i></button>
          <button type="button" data-talk="end" title="End the conversation (Esc)"><i data-lucide="x" class="size-4"></i></button>
        </div>`;
      document.body.appendChild(dock);
      if (window.lucide) lucide.createIcons();
      dock.addEventListener("click", (e) => {
        const b = e.target.closest("[data-talk]");
        if (!b) return;
        if (b.dataset.talk === "end") end("user");
        if (b.dataset.talk === "mute") {
          C.muted = !C.muted;
          dock.classList.toggle("is-muted", C.muted);
          b.title = C.muted ? "Listen again" : "Pause listening";
          setState(C.state, C.muted ? "Paused. Click the mic to listen again." : "Listening…");
          if (C.muted) C.followUntil = 0; else backToListening();
        }
      });
    }
    dock.querySelector(".talk-caption--you").textContent = "";
    dock.querySelector(".talk-caption--xod").textContent = source === "wake" ? "" : "";
    dock.classList.remove("is-leaving", "is-muted");
    C.muted = false;
    dock.classList.add("is-open");
    dock.dataset.source = source;
    document.body.classList.add("xod-talking");
    placeDock();
  }

  function hideDock() {
    if (!dock) return;
    dock.classList.add("is-leaving");
    dock.classList.remove("is-open");
    document.body.classList.remove("xod-talking");
    setTimeout(() => { if (!C.active && dock) dock.classList.remove("is-leaving"); }, 600);
  }

  function setState(state, label) {
    C.state = state;
    if (window.XodMascot) try { XodMascot.setVoiceMood(state === "listening" || state === "transcribing" ? "listening" : null); } catch (_) {}
    if (!dock) return;
    dock.dataset.state = state;
    if (label !== undefined && label !== "") dock.querySelector(".talk-state").textContent = label;
    if (state !== "listening") setFollow(0);
  }

  function caption(who, text, partial = false) {
    if (!dock) return;
    const el = dock.querySelector(`.talk-caption--${who}`);
    el.textContent = text;
    el.classList.toggle("is-partial", partial);
    if (who === "you" && !partial) dock.querySelector(".talk-caption--xod").textContent = "";
  }

  function setFollow(f) { if (dock) dock.style.setProperty("--follow", f.toFixed(3)); }

  // Sit just above the composer (and its "Thinking…" bar), centred on it, wherever the layout puts it.
  function placeDock() {
    if (!dock || !C.active) return;
    const status = document.getElementById("liveStatusBar");
    const form = document.getElementById("chatForm");
    if (!form) return;
    // The panel that holds both the "Thinking…" bar and the composer.
    let panel = form.parentElement;
    while (panel && status && !panel.contains(status)) panel = panel.parentElement;
    const anchor = panel && panel !== document.body ? panel : form;
    const a = anchor.getBoundingClientRect(), f = form.getBoundingClientRect();
    dock.style.bottom = `${Math.max(16, innerHeight - a.top + 12)}px`;
    dock.style.left = `${f.left + f.width / 2}px`;
    document.documentElement.style.setProperty("--talk-dock-h", `${dock.offsetHeight + 24}px`);
  }
  window.addEventListener("resize", placeDock);
  let lastLevelPaint = 0;
  function setLevel(rms) {
    if (!dock || now() - lastLevelPaint < 45) return;
    lastLevelPaint = now();
    dock.style.setProperty("--lvl", clamp(rms * 14, 0, 1).toFixed(3));
  }

  // While Xod talks, the rings follow its voice instead.
  (function voiceRings() {
    const tick = () => {
      if (dock && C.active && (C.state === "speaking" || C.state === "thinking") && window.XodSpeech && XodSpeech.audioPlaying()) {
        dock.style.setProperty("--lvl", clamp(XodSpeech.level() * 9, 0, 1).toFixed(3));
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  })();

  // ---------------------------------------------------------------- little sounds
  // Each look has its own: the moon chimes, the cat goes "mrrp", the robot beeps, the owl hoots…
  let earCtx = null;
  const TIMBRE = {
    moon: { type: "sine", base: 660 }, cat: { type: "triangle", base: 700, bend: 1.3 }, ghost: { type: "sine", base: 520, vib: 9 },
    robot: { type: "square", base: 880, gain: 0.035 }, owl: { type: "sine", base: 392, owl: true }, sprout: { type: "triangle", base: 988 },
  };
  function chime(kind) {
    try { window.dispatchEvent(new CustomEvent("xod:chime", { detail: kind })); } catch (_) {}
    try {
      earCtx = earCtx || new AudioContext();
      if (earCtx.state === "suspended") earCtx.resume();
      const id = (window.XodMascot && XodMascot.character && XodMascot.character()) || "moon";
      const tb = TIMBRE[id] || TIMBRE.moon;
      const b = tb.base;
      const seq = { start: [[b, 0, 0.12], [b * 1.5, 0.11, 0.2]], listen: [[b * 1.5, 0, 0.09]], end: [[b * 1.5, 0, 0.12], [b, 0.11, 0.22]],
                    doze: [[b, 0, 0.18], [b * 0.84, 0.17, 0.18], [b * 0.67, 0.34, 0.32]], error: [[b * 0.6, 0, 0.16], [b * 0.5, 0.15, 0.25]] }[kind] || [];
      const t0 = earCtx.currentTime + 0.01;
      for (const [f, at, dur] of seq) {
        const o = earCtx.createOscillator(), g = earCtx.createGain();
        o.type = tb.type;
        o.frequency.setValueAtTime(tb.owl ? f * 0.95 : f, t0 + at);
        if (tb.bend) o.frequency.exponentialRampToValueAtTime(f * tb.bend, t0 + at + dur * 0.7);
        if (tb.owl) o.frequency.exponentialRampToValueAtTime(f * 1.04, t0 + at + dur * 0.5);
        if (tb.vib) {
          const lfo = earCtx.createOscillator(), lg = earCtx.createGain();
          lfo.frequency.value = tb.vib; lg.gain.value = f * 0.03;
          lfo.connect(lg); lg.connect(o.frequency); lfo.start(t0 + at); lfo.stop(t0 + at + dur + 0.05);
        }
        const peak = tb.gain || 0.09;
        g.gain.setValueAtTime(0.0001, t0 + at);
        g.gain.exponentialRampToValueAtTime(peak, t0 + at + 0.012);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + at + dur);
        o.connect(g); g.connect(earCtx.destination);
        o.start(t0 + at); o.stop(t0 + at + dur + 0.02);
      }
    } catch (_) {}
  }

  function toast(text) { if (window.showToast) showToast(text); else console.warn(text); }

  // ---------------------------------------------------------------- wiring
  async function init() {
    // The mic button, Option+Space and the old hands-free functions all start a conversation now.
    window.toggleJarvisVoice = () => toggle("button");
    window.startJarvisVoice = () => start({ source: "button" });
    window.stopJarvisVoice = () => end("user");
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && C.active) { e.preventDefault(); end("user"); } });
    // Typing means you've switched to the keyboard: the conversation steps aside.
    const input = document.getElementById("chatInput");
    if (input) input.addEventListener("keydown", (e) => { if (C.active && e.key.length === 1) end("user"); });
    if (desktop && desktop.onTalk) desktop.onTalk(() => toggle("shortcut"));
    try {
      const s = await (await fetch("/api/speech")).json();
      if (s.settings && s.settings.wake) setWake(true);
    } catch (_) {}
  }

  window.XodConversation = {
    start: (o) => start(o), end: (r) => end(r), toggle, setWake, active: () => C.active,
    state: () => ({ state: C.state, active: C.active, wake: C.wakeOn, muted: C.muted }),
    // For tests and recordings: as if this had just been said.
    __heard: (text) => (C.state === "wake" ? wakeCheckText(text) : handle(text)),
  };
  async function wakeCheckText(text) {
    if (/^(hey|hi|ok|okay)?[\s,]*(xod|zod)\b/i.test(text)) {
      const rest = text.replace(/^(hey|hi|ok|okay)?[\s,]*(xod|zod)[\s,.!]*/i, "");
      await start({ source: "wake", quiet: words(rest) >= 2 });
      if (words(rest) >= 2) handle(rest);
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
