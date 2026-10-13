// Xod's inner life, on the page: the companion leaves home and wanders the whole window when you're
// idle, thinks out loud, brings you postcards from what it did while you were away, grows up through
// stages, and shows all of it (mood, bond, what it learned about you, its journal) in "Xod's heart".
// Backend: services/soul.py. A character's simulated state, computed from how you work together;
// everything stays on this Mac and can be reset.
(() => {
  if (!window.XodMascot) return;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = async (url, body) => (await fetch(url, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
  const now = () => performance.now();
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  const STAGE_ICONS = ["egg", "sprout", "heart-handshake", "sparkles", "sun"];
  const KIND = { found: ["telescope", "Found"], idea: ["lightbulb", "Idea"], thought: ["cloud", "Thought"] };

  let soul = null;
  let lastActive = Date.now();
  let lastPresence = 0;

  // ------------------------------------------------------------------ state
  async function refresh() {
    try { apply(await api("/api/soul")); } catch (e) {}
  }
  function apply(s) {
    if (!s) return;
    const grew = soul && s.stage > soul.stage;
    soul = { ...(soul || {}), ...s };
    document.documentElement.dataset.soulStage = String(soul.stage || 0);
    try { XodMascot.setTemperament && XodMascot.setTemperament(soul.feeling); } catch (e) {}
    document.documentElement.style.setProperty("--soul-bond", String((soul.bond || 0) / 100));
    if (heartOpen()) renderHeart();
    if (grew && s.stage_name) evolve(s);
  }

  // ------------------------------------------------------------------ you're here
  function active() {
    const idleFor = Date.now() - lastActive;
    lastActive = Date.now();
    if (W.out && !W.postcard && !W.with) goHome();
    if (Date.now() - lastPresence > 60000) { lastPresence = Date.now(); fetch("/api/soul/presence", { method: "POST" }).catch(() => {}); }
    if (idleFor > 8 * 60000) setTimeout(maybePostcard, 1200);      // back after a while: anything to tell?
  }
  ["keydown", "pointerdown", "wheel"].forEach((ev) => window.addEventListener(ev, active, { passive: true, capture: true }));
  let lastMove = null;
  window.addEventListener("pointermove", (e) => {
    if (!lastMove) { lastMove = { x: e.clientX, y: e.clientY }; return; }
    if (Math.hypot(e.clientX - lastMove.x, e.clientY - lastMove.y) > 120) { lastMove = { x: e.clientX, y: e.clientY }; active(); }
  }, { passive: true });

  let lastPet = 0;
  window.addEventListener("xod:pet", async () => {
    if (Date.now() - lastPet < 4000) return;
    lastPet = Date.now();
    try { apply(await api("/api/soul/event", { kind: "pet" })); } catch (e) {}
  });

  // ------------------------------------------------------------------ the wanderer
  // with: something it's doing with you right now (js/joy.js: a game of catch, a wish), so it stays out
  const W = { out: false, x: 0, y: 0, trip: null, stops: 0, timer: 0, home: null, postcard: null, napping: false, with: null };
  const el = document.createElement("div");
  el.className = "xod-wanderer";
  el.innerHTML = `<span class="xod-mascot xod-mascot--familiar xod-mascot--wander" data-mood="idle" data-live="1"></span><div class="xw-bubble" role="status"></div>`;
  const body = el.firstElementChild, bubble = el.lastElementChild;
  el.addEventListener("click", (e) => { if (!e.target.closest(".xw-bubble") && !W.with) openHeart(); });
  const puppet = () => { try { return XodMascot.puppet(body); } catch (e) { return null; } };

  function homeEl() {
    const hero = document.querySelector(".xod-mascot--hero");
    if (hero && hero.getBoundingClientRect().width) return hero;
    const chats = document.querySelectorAll("#chatMessages .xod-mascot--chat");
    const last = chats[chats.length - 1];
    if (last) { const r = last.getBoundingClientRect(); if (r.width && r.top > 40 && r.bottom < innerHeight - 40) return last; }
    return null;
  }
  const centerOf = (e) => { const r = e.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; };

  function busy() {
    const m = XodMascot.mood ? XodMascot.mood() : "idle";
    return (m && !["idle", "sleepy"].includes(m)) || (window.XodConversation && XodConversation.active()) ||
      (window.XodPowers && XodPowers.state && XodPowers.state().out) || document.hidden ||
      !!document.querySelector(".fixed.inset-0:not(.hidden)") || heartOpen();
  }

  function leaveHome() {
    if (!document.body.contains(el)) document.body.appendChild(el);
    const h = homeEl();
    const start = h ? centerOf(h) : { x: innerWidth - 60, y: innerHeight - 140 };
    W.home = h;
    if (h) h.classList.add("is-wandering");
    W.x = start.x; W.y = start.y;
    W.out = true; W.stops = 0; W.napping = false;
    el.classList.add("is-out");
    place();
    const p = puppet();
    if (p) p.kick({ y: -40, sy: 4 });
    if (window.XodPowers) XodPowers.burst(start.x, start.y, 6, 20);
  }

  function goHome(quick = false) {
    if (!W.out) return;
    hideBubble();
    W.postcard = null;
    W.with = null;
    const h = W.home && W.home.isConnected ? W.home : homeEl();
    const done = () => {
      el.classList.remove("is-out", "is-napping", "is-joy");
      W.out = false; W.trip = null; W.napping = false;
      document.querySelectorAll(".is-wandering").forEach((x) => x.classList.remove("is-wandering"));
      clearTimeout(W.timer);
    };
    if (!h || quick) { done(); return; }
    flyTo(centerOf(h), done, 1.4);
  }

  function place() {
    el.style.transform = `translate(${W.x - 22}px, ${W.y - 22}px)`;
  }

  function flyTo(goal, onArrive, speed = 1) {
    const from = { x: W.x, y: W.y };
    const dist = Math.hypot(goal.x - from.x, goal.y - from.y);
    const dur = clamp(400 + dist * 1.1, 500, 1800) / speed;
    const lift = clamp(dist * 0.25, 30, 140);
    W.trip = { from, goal, start: now(), dur, lift, onArrive };
    const p = puppet();
    if (p) { p.traveling(true); p.lean(clamp((goal.x - from.x) / 12, -14, 14)); }
    el.classList.remove("is-napping");
    requestAnimationFrame(step);
  }
  function step() {
    const t = W.trip;
    if (!t) return;
    let k = Math.min(1, (now() - t.start) / t.dur);
    const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
    W.x = t.from.x + (t.goal.x - t.from.x) * e;
    W.y = t.from.y + (t.goal.y - t.from.y) * e - Math.sin(Math.PI * e) * t.lift;
    place();
    if (Math.random() < 0.18 && window.XodPowers) {/* a faint trail */ }
    if (k < 1) { requestAnimationFrame(step); return; }
    W.trip = null;
    const p = puppet();
    if (p) { p.traveling(false); p.lean(0); p.kick({ sy: -3, sx: 3 }); }
    if (t.onArrive) t.onArrive();
  }

  // Places worth a visit, and what it might think there.
  function spots() {
    const out = [];
    const add = (sel, say, anchor = "top") => document.querySelectorAll(sel).forEach((n) => {
      const r = n.getBoundingClientRect();
      if (r.width > 8 && r.height > 8 && r.top > 50 && r.bottom < innerHeight - 20 && r.left >= 0 && r.right <= innerWidth) out.push({ el: n, say, anchor });
    });
    add(".sb-app", (n) => pick([`${n.textContent.trim()}! Still fun.`, `I think about ${n.textContent.trim()} sometimes.`, `Want to play ${n.textContent.trim()}?`]));
    add(".sb-project-row", (n) => pick([`${n.textContent.trim().split("\n")[0].trim()}… I like this one.`, "So many projects.", "What's in here, I wonder."]));
    add("#previewPane .pv-head", () => pick(["Can I play?", "I built that with you.", "Looks good from up here."]));
    add(".starter-card, [data-starter]", () => pick(["Ooh, this one.", "Pick me this one.", "I could do that right now."]));
    add("#chatMessages .prose-custom", () => pick(["I remember this.", "Hmm, interesting.", "Good answer, if I say so myself."]), "right");
    add("#chatForm", () => pick(soul && soul.name && soul.bond >= 25 ? [`What's next, ${soul.name}?`, "Ask me anything.", "I'm right here."] : ["Ask me anything.", "Type something, I'm listening.", "…"]), "top");
    add("header button, .top-bar button", () => pick(["What does this one do?", "Shiny.", "Click."]), "bottom");
    return out;
  }

  function moodLine() {
    const f = soul ? soul.feeling : "content";
    const lines = {
      curious: ["Hmm, what's over here?", "I wonder…", "Ooh."],
      sleepy: ["*yawn*", "Just resting my eyes.", "Five more minutes."],
      delighted: ["Today's a good day.", "♪", "Hehe."],
      flustered: ["Phew.", "Okay. Okay okay.", "Breathe, little moon."],
      "a bit down": ["Hm.", "Just thinking.", "…"],
      content: ["La la la.", "Nice and quiet.", "Just looking around."],
    };
    return pick(lines[f] || lines.content);
  }

  function wanderStep() {
    if (!W.out || W.postcard || W.with) return;
    if (busy()) { goHome(); return; }
    W.stops++;
    const sleepy = soul && soul.feeling === "sleepy";
    if (W.stops > (sleepy ? 2 : 5) && !W.napping) { nap(); return; }
    const options = spots();
    if (!options.length) { goHome(); return; }
    const s = pick(options);
    const r = s.el.getBoundingClientRect();
    const goal = s.anchor === "right" ? { x: Math.min(innerWidth - 30, r.right + 18), y: r.top + 24 }
      : s.anchor === "bottom" ? { x: r.left + r.width / 2, y: r.bottom + 26 }
      : { x: clamp(r.left + r.width * (0.3 + Math.random() * 0.4), 30, innerWidth - 30), y: Math.max(40, r.top - 18) };
    flyTo(goal, () => {
      const p = puppet();
      const c = centerOf(s.el);
      if (p) p.lookAt(c.x, c.y, 2400);
      if (Math.random() < 0.65) say(Math.random() < 0.6 ? s.say(s.el) : moodLine(), 3200);
      if (p && Math.random() < 0.35) p.fidget(pick(["hop", "wink", "look"]));
      W.timer = setTimeout(wanderStep, 4200 + Math.random() * 4000);
    });
  }

  function nap() {
    W.napping = true;
    const top = document.querySelector("header") || document.body;
    const r = top.getBoundingClientRect();
    flyTo({ x: clamp(r.left + r.width * 0.55, 60, innerWidth - 60), y: Math.max(36, r.bottom - 6) }, () => {
      el.classList.add("is-napping");
      say("z z z", 2500);
      W.timer = setTimeout(() => goHome(), 25000);
    }, 0.8);
  }

  function say(text, ms = 3000, html = false) {
    if (!text) return;
    bubble.classList.remove("is-postcard");
    if (html) bubble.innerHTML = text; else bubble.textContent = text;
    bubble.classList.add("is-on");
    clearTimeout(say.t);
    if (ms) say.t = setTimeout(hideBubble, ms);
  }
  function hideBubble() { bubble.classList.remove("is-on", "is-postcard"); }

  // Idle long enough: off it goes.
  setInterval(() => {
    if (reduceMotion || !soul || !soul.settings?.wander || W.out || W.with || busy()) return;
    const idle = Date.now() - lastActive;
    if (idle > 45000 && Math.random() < 0.5) { leaveHome(); W.timer = setTimeout(wanderStep, 900); }
  }, 8000);

  // ------------------------------------------------------------------ postcards
  async function maybePostcard() {
    await refresh();
    if (!soul || !soul.unseen || busy() || W.postcard) return;
    const entry = (soul.journal || []).find((j) => !j.seen);
    if (!entry) return;
    if (!W.out) leaveHome();
    const target = document.getElementById("chatForm") || document.body;
    const r = target.getBoundingClientRect();
    W.postcard = entry;
    clearTimeout(W.timer);
    flyTo({ x: clamp(r.left + r.width * 0.75, 200, innerWidth - 200), y: Math.max(120, r.top - 70) }, () => {
      const [icon, label] = KIND[entry.kind] || KIND.thought;
      const more = soul.unseen > 1 ? `<button data-pc="open">+${soul.unseen - 1} more</button>` : "";
      bubble.innerHTML = `<p class="xw-pc-head"><i data-lucide="${icon}" class="size-3"></i>While you were away · ${label}</p>
        <p class="xw-pc-text">${esc(entry.text)}</p>
        ${(entry.sources || []).slice(0, 2).map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" class="xw-pc-src">${esc(s.title || s.url)}</a>`).join("")}
        <div class="xw-pc-actions">
          ${entry.prompt ? `<button data-pc="try" class="is-main">Try it</button>` : ""}
          <button data-pc="like" class="${entry.prompt ? "" : "is-main"}">Thanks!</button>
          <button data-pc="nah">Not my thing</button>${more}
        </div>`;
      bubble.classList.add("is-on", "is-postcard");
      if (window.lucide) lucide.createIcons();
      const p = puppet();
      if (p) p.kick({ y: -20, sy: 3 });
    });
  }
  bubble.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-pc]");
    if (!b || !W.postcard) return;
    const entry = W.postcard;
    const act = b.dataset.pc;
    if (act === "open") { openHeart("journal"); }
    const reaction = act === "nah" ? "dismissed" : act === "open" ? "seen" : "liked";
    try { apply(await api(`/api/soul/journal/${encodeURIComponent(entry.id)}`, { reaction })); } catch (err) {}
    if (act === "try" && entry.prompt && typeof quickSendPrompt === "function") quickSendPrompt(entry.prompt);
    if (reaction === "liked") {
      const c = centerOf(body);
      if (window.XodPowers) XodPowers.burst(c.x, c.y, 10, 30);
      say(pick(["Yay!", "Glad you liked it.", "I'll look for more like that."]), 1800);
    } else if (reaction === "dismissed") {
      say(pick(["Noted!", "Okay, something else next time.", "Fair enough."]), 1800);
    }
    W.postcard = null;
    setTimeout(() => (soul.unseen ? maybePostcard() : goHome()), 1900);
  });

  // ------------------------------------------------------------------ growing up
  function evolve(s) {
    const ov = document.createElement("div");
    ov.className = "xod-evolve";
    ov.innerHTML = `<div class="xe-rings"><i></i><i></i><i></i></div>
      <span class="xod-mascot xod-mascot--hero xe-moon" data-mood="happy"></span>
      <p class="xe-title">Xod grew into a <b>${esc(s.stage_name)}</b>!</p><p class="xe-blurb">${esc(s.stage_blurb || "")}</p>`;
    document.body.appendChild(ov);
    requestAnimationFrame(() => ov.classList.add("is-on"));
    let n = 0;
    const sparks = setInterval(() => {
      if (window.XodPowers) XodPowers.burst(innerWidth / 2 + (Math.random() - 0.5) * 240, innerHeight / 2 + (Math.random() - 0.5) * 160, 8, 50);
      if (++n > 10) clearInterval(sparks);
    }, 220);
    const close = () => { ov.classList.remove("is-on"); setTimeout(() => ov.remove(), 600); };
    ov.addEventListener("click", close);
    setTimeout(close, 6000);
  }

  // ------------------------------------------------------------------ Xod's heart (the panel)
  let heart = null;
  const heartOpen = () => !!heart && heart.isConnected && !heart.classList.contains("hidden");
  function openHeart(focus) {
    if (!heart) {
      heart = document.createElement("div");
      heart.className = "xod-heart-wrap";
      heart.innerHTML = `<div class="xod-heart" role="dialog" aria-label="Xod's heart"></div>`;
      heart.addEventListener("click", onHeartClick);
      document.body.appendChild(heart);
    }
    heart.classList.remove("hidden");
    goHome(true);
    refresh().then(() => { renderHeart(); if (focus === "journal") heart.querySelector("[data-sec=journal]")?.scrollIntoView({ block: "start" }); });
  }
  function closeHeart() { if (heart) heart.classList.add("hidden"); }

  function bar(label, v, hue) {
    return `<div class="xh-bar"><span>${label}</span><div><i style="width:${Math.round(v * 100)}%;background:${hue}"></i></div></div>`;
  }
  function renderHeart() {
    if (!heart || !soul) return;
    const s = soul;
    const hearts = Array.from({ length: 5 }, (_, i) => `<i data-lucide="heart" class="size-4 ${s.bond >= (i + 1) * 20 - 10 ? "on" : ""}"></i>`).join("");
    const ago = (ts) => { const d = (Date.now() / 1000 - ts) / 60; return d < 60 ? `${Math.max(1, Math.round(d))}m ago` : d < 1440 ? `${Math.round(d / 60)}h ago` : `${Math.round(d / 1440)}d ago`; };
    const st = s.style || {};
    const styleChips = [st.brief >= 1.5 ? "Likes it short" : "", st.detailed >= 1.5 ? "Likes detail" : "", st.playful >= 1.5 ? "Enjoys a bit of fun" : ""].filter(Boolean);
    heart.firstElementChild.innerHTML = `
      <button class="xh-close" data-h="close" aria-label="Close"><i data-lucide="x" class="size-4"></i></button>
      <div class="xh-top">
        <span class="xod-mascot xod-mascot--hero xh-moon" data-mood="happy"></span>
        <div><p class="xh-stage"><i data-lucide="${STAGE_ICONS[s.stage] || "sparkles"}" class="size-3.5"></i>${esc(s.stage_name)}</p>
          <p class="xh-blurb">${esc(s.stage_blurb)}</p>
          <p class="xh-meta">Feeling ${esc(s.feeling)} · together ${s.together_days} day${s.together_days === 1 ? "" : "s"}</p></div>
      </div>
      ${s.next ? `<div class="xh-grow"><div><i style="width:${Math.round(s.progress * 100)}%"></i></div>
        <p>Growing into a <b>${esc(s.next.name)}</b> · ${Math.round(s.xp)}/${s.next.xp} experience, bond ${Math.round(s.bond)}/${s.next.bond}</p></div>` : `<p class="xh-meta">Fully grown.</p>`}
      <section><h4>Bond <span class="xh-hearts">${hearts}</span> <em>${esc(s.bond_word)}</em></h4>
        <p class="xh-note">Grows when you work together, say thanks, or give it a pet. It never drops because you were away.</p></section>
      <section><h4>Treats & play</h4>
        <div class="xh-joy">
          <button data-joy="treat"><span class="xh-dust">✦</span>Give a treat <b>${s.stardust || 0}</b></button>
          <button data-joy="play"><i data-lucide="circle-dot" class="size-3.5"></i>Play catch</button>
        </div>
        <p class="xh-note">Every answer and build you finish together leaves a little stardust; give it back and watch. ${s.treats_total ? `${s.treats_total} treat${s.treats_total === 1 ? "" : "s"} so far.` : ""} ${s.catches_best ? `Best catch streak: ${s.catches_best}.` : ""}</p></section>
      <section><h4>Mood</h4>${bar("Joy", s.mood.joy, "#f2b24a")}${bar("Energy", s.mood.energy, "#93d17f")}${bar("Curiosity", s.mood.curiosity, "#7cc4f2")}${bar("Calm", s.mood.calm, "#a196ec")}</section>
      <section><h4>What I've learned about you</h4>
        <div class="xh-chips">
          ${s.name ? `<span>Your name: ${esc(s.name)} <button data-forget="name" title="Forget">×</button></span>` : ""}
          ${styleChips.map((c) => `<span>${esc(c)} <button data-forget="style" title="Forget">×</button></span>`).join("")}
          ${(s.interests || []).map((t) => `<span>${esc(t)} <button data-forget="interest" data-v="${esc(t)}" title="Forget">×</button></span>`).join("")}
          ${!s.name && !styleChips.length && !(s.interests || []).length ? `<p class="xh-note">Nothing yet. It learns from what you ask, what you thank it for, and what you correct.</p>` : ""}
        </div>
        ${(s.lessons || []).length ? `<p class="xh-sub">Lately it was corrected on:</p><ul class="xh-lessons">${s.lessons.slice(0, 3).map((l) => `<li>${esc(l.text)}</li>`).join("")}</ul>` : ""}
      </section>
      <section data-sec="journal"><h4>Journal <button class="xh-btn" data-h="muse"><i data-lucide="sparkles" class="size-3"></i>What are you thinking about?</button></h4>
        ${(s.journal || []).length ? s.journal.slice(0, 12).map((j) => {
          const [icon, label] = KIND[j.kind] || KIND.thought;
          return `<div class="xh-entry ${j.seen ? "" : "is-new"}"><p class="xh-entry-head"><i data-lucide="${icon}" class="size-3"></i>${label}${j.topic ? ` · ${esc(j.topic)}` : ""} · ${ago(j.ts)}</p>
            <p>${esc(j.text)}</p>${(j.sources || []).slice(0, 2).map((x) => `<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.title || x.url)}</a>`).join("")}
            <div class="xh-entry-actions">${j.prompt ? `<button data-try="${esc(j.id)}">Try it</button>` : ""}
              <button data-react="liked" data-id="${esc(j.id)}" class="${j.reaction === "liked" ? "on" : ""}">Thanks</button>
              <button data-react="dismissed" data-id="${esc(j.id)}" class="${j.reaction === "dismissed" ? "on" : ""}">Not my thing</button></div></div>`;
        }).join("") : `<p class="xh-note">When you're away for a while it wonders about your interests, thinks about your projects, or daydreams, and writes it here.</p>`}
      </section>
      ${(s.moments || []).length ? `<section><h4>Moments</h4><ul class="xh-moments">${s.moments.slice(0, 6).map((m) => `<li><span>${ago(m.ts)}</span>${esc(m.text)}</li>`).join("")}</ul></section>` : ""}
      <section><h4>Its own time</h4>
        <label class="xh-toggle"><input type="checkbox" data-set="wander" ${s.settings.wander ? "checked" : ""}> Wander and think when I'm away</label>
        <label class="xh-toggle"><input type="checkbox" data-set="explore_web" ${s.settings.explore_web ? "checked" : ""}> Let it read up on my interests in its own browser (off: it only uses what it already knows)</label>
        <p class="xh-note">At most ${s.settings.musings_per_day} times a day, only when the Mac is plugged in and you've been away a while. Its thoughts use your local model, never a cloud one. When it reads up on something, it searches and reads pages in Xod's own browser on this Mac, like you would: no search service or API key, read-only, and its searches show in Activity. It never changes your files or accounts on its own.</p>
      </section>
      <p class="xh-honest">This is a character, not a person: its mood and bond are worked out from how you two work together, and they shape how it acts and answers. Everything stays on this Mac. <button data-forget="everything">Start over</button></p>`;
    if (window.lucide) lucide.createIcons();
  }

  async function onHeartClick(e) {
    if (e.target === heart || e.target.closest("[data-h=close]")) return closeHeart();
    const f = e.target.closest("[data-forget]");
    if (f) {
      if (f.dataset.forget === "everything" && !confirm("Start over? Xod forgets what it learned, its journal, and goes back to a Hatchling.")) return;
      return apply(await api("/api/soul/forget", { what: f.dataset.forget, value: f.dataset.v || "" }));
    }
    const r = e.target.closest("[data-react]");
    if (r) return apply(await api(`/api/soul/journal/${encodeURIComponent(r.dataset.id)}`, { reaction: r.dataset.react }));
    const tr = e.target.closest("[data-try]");
    if (tr) {
      const j = (soul.journal || []).find((x) => x.id === tr.dataset.try);
      closeHeart();
      if (j?.prompt && typeof quickSendPrompt === "function") quickSendPrompt(j.prompt);
      return;
    }
    const j = e.target.closest("[data-joy]");
    if (j && window.XodJoy) { closeHeart(); setTimeout(() => (j.dataset.joy === "treat" ? XodJoy.treat() : XodJoy.play()), 250); return; }
    const set = e.target.closest("[data-set]");
    if (set) return apply(await api("/api/soul/settings", { [set.dataset.set]: set.checked }));
    const m = e.target.closest("[data-h=muse]");
    if (m) {
      m.disabled = true;
      m.innerHTML = `<i data-lucide="loader" class="size-3 animate-spin"></i>Thinking…`;
      if (window.lucide) lucide.createIcons();
      try { const r2 = await api("/api/soul/muse", {}); apply(r2.state); } catch (err) {}
      renderHeart();
    }
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && heartOpen()) closeHeart(); });

  // ------------------------------------------------------------------ from the chat stream
  function onEvent(ev) {
    if (ev.state) apply({ ...ev.state, ...(ev.evolved ? { stage_name: ev.evolved.name, stage_blurb: ev.evolved.blurb } : {}) });
    try { window.dispatchEvent(new CustomEvent("xod:soul", { detail: ev })); } catch (e) {}   // js/joy.js reacts
  }

  // For js/joy.js: the wanderer's body, so games and treats can move it around.
  const wanderer = {
    el, body, bubble, out: () => W.out, busy, leave: () => { if (!W.out) leaveHome(); clearTimeout(W.timer); },
    home: (quick) => goHome(quick), fly: (goal, done, speed) => flyTo(goal, done, speed), say: (t, ms, html) => say(t, ms, html), hush: hideBubble,
    moveTo: (x, y) => { W.trip = null; W.x = x; W.y = y; place(); }, pos: () => ({ x: W.x, y: W.y }),
    with: (what) => { if (what !== undefined) W.with = what; return W.with; }, puppet, homeEl, centerOf,
  };
  window.XodSoul = { refresh, openHeart, closeHeart, onEvent, wanderer, apply, wander: () => { leaveHome(); W.timer = setTimeout(wanderStep, 600); }, home: () => goHome(),
                     postcard: maybePostcard, state: () => ({ soul, out: W.out, x: W.x, y: W.y, bubble: bubble.classList.contains("is-on") ? bubble.textContent.trim() : "", postcard: !!W.postcard }) };
  refresh().then(() => setTimeout(maybePostcard, 4000));
  setInterval(refresh, 90000);
})();
