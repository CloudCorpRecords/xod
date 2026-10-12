// ==============================================================
// XOD - spoken replies (services/speech.py)
// Reads answers aloud with a voice that runs on this Mac. The reply is
// spoken a sentence or two at a time while it's still being written:
// each piece becomes a short WAV on the server, the next ones are made
// while the current one plays, and the companion's mouth follows the
// sound. Code, tables and the source list aren't read out. Talking,
// typing, Escape or the speaker button stop it.
// ==============================================================
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let info = { settings: { mode: "voice", voice: "", rate: 190 }, voices: [], voice: "", has_natural: true, rate: { min: 140, max: 280, default: 190 } };
  let voiceAskedAt = 0;

  async function load() {
    try { info = await (await fetch("/api/speech")).json(); } catch (_) {}
    paintButton();
  }

  // ---------------------------------------------------------------- playing
  let ctx = null, analyser = null, current = null, levelRaf = 0;
  const queue = [];          // [{ text, audio: Promise<AudioBuffer|null> }]
  let playing = false, generation = 0, idleWaiters = [];

  function audioCtx() {
    if (!ctx) {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      analyser.connect(ctx.destination);
    }
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    return ctx;
  }

  async function fetchAudio(text, gen) {
    try {
      const res = await fetch("/api/speech/say", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
      if (gen !== generation || res.status === 204 || !res.ok) return null;
      const buf = await res.arrayBuffer();
      if (gen !== generation) return null;
      return await audioCtx().decodeAudioData(buf);
    } catch (_) { return null; }
  }

  function enqueue(text) {
    if (!text || !/[A-Za-z0-9]/.test(text)) return;
    const gen = generation;
    queue.push({ text, audio: fetchAudio(text, gen), gen });
    // Make at most three pieces ahead of what's playing.
    if (!playing) playNext();
    paintButton();
  }

  async function playNext() {
    if (playing) return;
    const item = queue.shift();
    if (!item) { finishedAll(); return; }
    playing = true;
    paintButton();
    const buffer = await item.audio;
    if (item.gen !== generation) { playing = false; return; }
    if (!buffer) { playing = false; playNext(); return; }
    const c = audioCtx();
    const src = c.createBufferSource();
    src.buffer = buffer;
    src.connect(analyser);
    current = src;
    src.onended = () => {
      if (current === src) current = null;
      playing = false;
      if (item.gen === generation) playNext();
    };
    src.start();
    mouthLoop();
  }

  // The companion's mouth opens with the loudness of the voice.
  function mouthLoop() {
    cancelAnimationFrame(levelRaf);
    const data = new Float32Array(analyser.fftSize);
    const tick = () => {
      if (!current) return;
      analyser.getFloatTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
      const rms = Math.sqrt(sum / data.length);
      try { window.XodMascot && XodMascot.voice(rms); } catch (_) {}
      levelRaf = requestAnimationFrame(tick);
    };
    levelRaf = requestAnimationFrame(tick);
  }

  function finishedAll() {
    playing = false;
    paintButton();
    if (!speaking()) { const w = idleWaiters; idleWaiters = []; w.forEach((f) => f()); }
  }

  function speaking() { return playing || queue.length > 0 || !!current || !!(reply && reply.active && !reply.finished); }

  function stop() {
    generation++;
    queue.length = 0;
    if (current) { try { current.onended = null; current.stop(); } catch (_) {} current = null; }
    playing = false;
    if (reply) reply.active = false;
    cancelAnimationFrame(levelRaf);
    paintButton();
    const w = idleWaiters; idleWaiters = []; w.forEach((f) => f());
  }

  // ---------------------------------------------------------------- following a reply as it streams
  // reply: { buf, pos, active, done, finished, inCode, saidCode, saidTable, pending, first }
  let reply = null;

  function startReply(forceSpeak) {
    stop();
    const mode = info.settings.mode;
    const asked = Date.now() - voiceAskedAt < 15000;
    const active = forceSpeak || mode === "always" || (mode === "voice" && asked);
    voiceAskedAt = 0;
    reply = { buf: "", pos: 0, active, done: false, finished: false, inCode: false, lineStart: true, pending: "", first: true };
    if (active) audioCtx();
    paintButton();
  }

  // Splits what's arrived so far into pieces worth saying: whole sentences (or lines), with the
  // first piece short so the voice starts quickly. Skips code and tables, stops at the sources.
  const BOUNDARY = /(?<!\b\d)(?<!\b[A-Z])[.!?](?=["')\]]*\s)|\n/g;
  function pump() {
    const r = reply;
    if (!r || !r.active) return;
    for (let guard = 0; guard < 400; guard++) {
      const rest = r.buf.slice(r.pos);
      if (!rest) break;
      if (r.inCode) {
        const end = rest.indexOf("```");
        if (end < 0) { if (r.done) r.pos = r.buf.length; break; }
        const nl = rest.indexOf("\n", end + 3);
        r.pos += nl < 0 ? rest.length : nl + 1;
        r.inCode = false; r.lineStart = true;
        continue;
      }
      if (r.lineStart) {
        const lead = rest.replace(/^[ \t]+/, "");
        if (!r.done && lead.length < 16 && !lead.includes("\n")) break;      // not enough to tell what this line is
        if (lead.startsWith("```")) {
          flushPending(r, true);
          if (!r.saidCode) { r.saidCode = true; enqueue("I've put the code in the reply."); }
          r.inCode = true; r.pos += rest.indexOf("```") + 3;
          continue;
        }
        if (/^\|/.test(lead)) {
          const nl = rest.indexOf("\n");
          if (nl < 0 && !r.done) break;
          flushPending(r, true);
          if (!r.saidTable) { r.saidTable = true; enqueue("There's a table in the reply."); }
          r.pos += nl < 0 ? rest.length : nl + 1;
          continue;
        }
        if (/^(\*\*|#+\s*)?\s*(source links|sources)\b/i.test(lead) || /^>\s*⚠️/.test(lead)) {
          flushPending(r, true);
          r.finished = true; r.pos = r.buf.length;
          break;
        }
      }
      BOUNDARY.lastIndex = 0;
      const m = BOUNDARY.exec(rest);
      if (!m) {
        if (r.done) { r.pending += rest; r.pos = r.buf.length; flushPending(r, true); }
        break;
      }
      const cut = m.index + 1;
      r.pending += rest.slice(0, cut);
      r.pos += cut;
      r.lineStart = rest[m.index] === "\n";
      flushPending(r, r.lineStart && rest[cut] === "\n");
    }
    if (r.done && r.pos >= r.buf.length) { flushPending(r, true); r.finished = true; if (!playing && !queue.length) finishedAll(); }
  }

  function flushPending(r, force) {
    const text = r.pending.trim();
    const enough = r.first ? 18 : 90;
    if (!text || (!force && text.length < enough)) return;
    r.pending = "";
    r.first = false;
    enqueue(text);
  }

  window.addEventListener("xod:agent", (e) => {
    const ev = e.detail || {};
    if (!reply) return;
    if (ev.type === "content" && reply.active) { reply.buf += ev.content || ""; pump(); }
    else if (ev.type === "done" || ev.type === "error") { reply.done = true; pump(); }
  });

  // Plain text read aloud now (the Listen button on a reply).
  function speakText(text) {
    startReply(true);
    reply.buf = text;
    reply.done = true;
    pump();
  }

  // ---------------------------------------------------------------- the speaker button and its menu
  function paintButton() {
    const btn = document.getElementById("chatSpeakBtn");
    if (!btn) return;
    const on = info.settings.mode === "always";
    const talking = playing || queue.length > 0 || !!current;
    btn.classList.toggle("is-on", on);
    btn.classList.toggle("is-speaking", talking);
    btn.title = talking ? "Stop speaking (Esc)" : on ? "Xod reads every reply aloud. Click for voice options."
      : info.settings.mode === "voice" ? "Xod answers aloud when you talk to it. Click for voice options." : "Spoken replies are off. Click for voice options.";
    btn.setAttribute("aria-label", talking ? "Stop speaking" : "Voice options");
  }

  async function save(patch) {
    try {
      const r = await (await fetch("/api/speech/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) })).json();
      if (r.settings) info.settings = r.settings;
    } catch (_) {}
    paintButton();
  }

  function menu() {
    let el = document.getElementById("speechMenu");
    if (el) { el.remove(); return; }
    const btn = document.getElementById("chatSpeakBtn");
    if (!btn) return;
    el = document.createElement("div");
    el.id = "speechMenu";
    el.className = "speech-menu";
    const s = info.settings;
    const chosen = s.voice || info.voice;
    const groups = { premium: "Natural (Premium)", enhanced: "Natural (Enhanced)", standard: "Standard" };
    const en = info.voices.filter((v) => v.lang.startsWith("en"));
    const others = info.voices.filter((v) => !v.lang.startsWith("en"));
    const opts = (list) => Object.keys(groups).map((q) => {
      const vs = list.filter((v) => v.quality === q);
      return vs.length ? `<optgroup label="${groups[q]}">${vs.map((v) => `<option value="${esc(v.name)}" ${v.name === chosen ? "selected" : ""}>${esc(v.name.replace(/\s*\((Premium|Enhanced)\)/, ""))} · ${esc(v.lang.replace("_", "-"))}</option>`).join("")}</optgroup>` : "";
    }).join("");
    el.innerHTML = `
      <p class="speech-menu-title">Spoken replies</p>
      <div class="speech-modes" role="radiogroup">
        ${[["always", "Read every reply"], ["voice", "When I talk to Xod"], ["off", "Off"]].map(([m, l]) =>
          `<button type="button" role="radio" data-mode="${m}" aria-checked="${s.mode === m}">${l}</button>`).join("")}
      </div>
      <label class="speech-row"><span>Voice</span>
        <select data-voice>${opts(en)}${others.length ? `<optgroup label="Other languages">${others.map((v) => `<option value="${esc(v.name)}" ${v.name === chosen ? "selected" : ""}>${esc(v.name)} · ${esc(v.lang.replace("_", "-"))}</option>`).join("")}</optgroup>` : ""}</select>
      </label>
      <label class="speech-row"><span>Speed</span>
        <input type="range" data-rate min="${info.rate.min}" max="${info.rate.max}" step="10" value="${s.rate}">
      </label>
      <div class="speech-actions">
        <button type="button" data-preview class="speech-btn">Hear it</button>
        <span class="speech-note">Runs on this Mac. Nothing is sent anywhere.</span>
      </div>
      ${info.has_natural ? "" : `
      <div class="speech-tip">
        <p>For a far more natural voice, download a free <b>Premium</b> voice (Zoe or Ava, for example) in System Settings → Accessibility → Spoken Content → System voice → Manage Voices. Xod picks it up automatically.</p>
        <button type="button" data-open-settings class="speech-btn">Open Spoken Content</button>
      </div>`}`;
    document.body.appendChild(el);
    const b = btn.getBoundingClientRect();
    el.style.left = `${Math.max(12, Math.min(innerWidth - el.offsetWidth - 12, b.right - el.offsetWidth))}px`;
    el.style.top = `${Math.max(12, b.top - el.offsetHeight - 10)}px`;
    el.addEventListener("click", async (e) => {
      const m = e.target.closest("[data-mode]");
      if (m) {
        await save({ mode: m.dataset.mode });
        el.querySelectorAll("[data-mode]").forEach((x) => x.setAttribute("aria-checked", String(x.dataset.mode === info.settings.mode)));
        if (info.settings.mode === "off") stop();
      }
      if (e.target.closest("[data-preview]")) {
        stop();
        startReply(true);
        reply.buf = "Hi! This is how I'll sound when I read my answers to you.";
        reply.done = true;
        pump();
      }
      if (e.target.closest("[data-open-settings]")) {
        fetch("/api/speech/open-voice-settings", { method: "POST" }).catch(() => {});
        setTimeout(load, 20000);      // a downloaded voice shows up on the next look
      }
    });
    el.querySelector("[data-voice]").addEventListener("change", (e) => save({ voice: e.target.value }));
    el.querySelector("[data-rate]").addEventListener("change", (e) => save({ rate: Number(e.target.value) }));
    setTimeout(() => document.addEventListener("pointerdown", function away(e) {
      if (!el.contains(e.target) && !e.target.closest("#chatSpeakBtn")) { el.remove(); document.removeEventListener("pointerdown", away, true); }
    }, true), 0);
  }

  function onButton() {
    if (playing || queue.length || current) { stop(); return; }
    menu();
  }

  // A Listen button on every finished reply.
  function addListenButtons(root = document) {
    root.querySelectorAll(".action-footer:not([data-listen])").forEach((footer) => {
      footer.dataset.listen = "1";
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn-listen hover:text-white flex items-center gap-1 px-2.5 py-1 rounded-lg hover:bg-white/[0.06] transition-colors";
      b.title = "Read this reply aloud";
      b.innerHTML = `<i data-lucide="volume-2" class="size-3.5"></i> Listen`;
      footer.insertBefore(b, footer.children[1] || null);
      if (window.lucide) lucide.createIcons();
    });
  }

  function replyText(btn) {
    const wrap = btn.closest(".message-bubble-animate") || btn.closest(".flex-1");
    const prose = wrap && wrap.querySelector(".prose-custom");
    if (!prose) return "";
    const clone = prose.cloneNode(true);
    // Code and tables become markers the splitter knows ("I've put the code in the reply").
    const line = (t) => { const p = document.createElement("p"); p.textContent = t; return p; };
    clone.querySelectorAll("pre").forEach((n) => n.replaceWith(line("```"), line("```")));
    clone.querySelectorAll("table").forEach((n) => n.replaceWith(line("| table |")));
    clone.querySelectorAll(".xp-signature").forEach((n) => n.remove());
    // innerText keeps the line breaks the splitter needs.
    document.body.appendChild(clone);
    clone.style.cssText = "position:absolute;left:-9999px;top:0;width:600px";
    const text = clone.innerText;
    clone.remove();
    return text;
  }

  // ---------------------------------------------------------------- wiring
  function init() {
    const btn = document.getElementById("chatSpeakBtn");
    if (btn) btn.addEventListener("click", onButton);

    // A new question: anything still being read is stale.
    const form = document.getElementById("chatForm");
    if (form) form.addEventListener("submit", () => setTimeout(() => startReply(false), 0), true);

    // Asked by voice: answer by voice (when that's the setting).
    if (typeof window.quickSendPrompt === "function") {
      const orig = window.quickSendPrompt;
      window.quickSendPrompt = function (text) { voiceAskedAt = Date.now(); return orig.apply(this, arguments); };
    }
    // Don't let the mic hear Xod: talking stops the voice, and hands-free listening waits for it to finish.
    try {
      if (typeof LocalSpeechRecognition !== "undefined") {
        const start = LocalSpeechRecognition.prototype.start;
        LocalSpeechRecognition.prototype.start = function () {
          const self = this, args = arguments;
          if (speaking()) { idleWaiters.push(() => setTimeout(() => start.apply(self, args), 350)); return; }
          return start.apply(self, args);
        };
      }
      if (typeof window.startJarvisVoice === "function") {
        const orig = window.startJarvisVoice;
        window.startJarvisVoice = function () { stop(); return orig.apply(this, arguments); };
      }
    } catch (_) {}

    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && speaking()) stop(); });
    const input = document.getElementById("chatInput");
    if (input) input.addEventListener("input", () => { if (playing || queue.length) stop(); });

    document.addEventListener("click", (e) => {
      const b = e.target.closest && e.target.closest(".btn-listen");
      if (!b) return;
      if (playing || queue.length || current) { stop(); return; }
      speakText(replyText(b));
    });
    addListenButtons();
    new MutationObserver(() => addListenButtons()).observe(document.getElementById("chatMessages") || document.body, { childList: true, subtree: true });
    load();
  }

  window.XodSpeech = { stop, speaking, speakText, reload: load, state: () => ({ playing, queued: queue.length, mode: info.settings.mode, reply: reply && { active: reply.active, finished: reply.finished } }) };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
