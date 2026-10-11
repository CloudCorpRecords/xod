// ==============================================================
// XOD - the moon
// A small character with a body that moves on springs, eyes that
// look at what matters, a mouth that talks while answers stream,
// and moods that follow what the agent is doing. Decorative only:
// if anything here fails, the app keeps working.
// ==============================================================
(() => {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const now = () => performance.now();
  let uid = 0;

  // ---------------------------------------------------------------- drawing
  function moonSvg() {
    const id = `xm${uid++}`;
    const eye = (cx, side) => `
      <g class="xm-eye xm-eye-${side}">
        <ellipse class="xm-iris" cx="${cx}" cy="33" rx="2.6" ry="3.4" fill="#0f1222"/>
        <circle class="xm-shine" cx="${cx + 1}" cy="31.7" r="0.95" fill="#fffaf0"/>
        <circle class="xm-shine2" cx="${cx - 0.9}" cy="34.6" r="0.4" fill="#fffaf0" opacity="0.7"/>
      </g>`;
    return `
<svg viewBox="0 0 64 64" aria-hidden="true">
  <defs>
    <radialGradient id="${id}-glow" cx="50%" cy="50%" r="50%">
      <stop offset="40%" stop-color="#f2b24a" stop-opacity="0.45"/>
      <stop offset="100%" stop-color="#f2b24a" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="${id}-moon" cx="38%" cy="30%" r="75%">
      <stop offset="0%" stop-color="#fff7e2"/>
      <stop offset="55%" stop-color="#ffdb8f"/>
      <stop offset="100%" stop-color="#e9a443"/>
    </radialGradient>
    <clipPath id="${id}-disc"><circle cx="32" cy="34" r="22"/></clipPath>
  </defs>
  <g class="xm-rings" fill="none" stroke="#ffd06b" stroke-width="0.8">
    <circle cx="32" cy="34" r="24"/><circle cx="32" cy="34" r="24"/><circle cx="32" cy="34" r="24"/>
  </g>
  <circle class="xm-glow" cx="32" cy="34" r="31" fill="url(#${id}-glow)"/>
  <g class="xm-rig">
    <g class="xm-body">
      <circle cx="32" cy="34" r="22" fill="url(#${id}-moon)"/>
      <circle cx="44" cy="23" r="3.2" fill="#c98a2e" opacity="0.22"/>
      <circle cx="47.5" cy="43" r="1.9" fill="#c98a2e" opacity="0.2"/>
      <circle cx="18.5" cy="25" r="1.5" fill="#c98a2e" opacity="0.2"/>
      <path class="xm-phase" clip-path="url(#${id}-disc)" fill="#0f1222" opacity="0"/>
      <ellipse class="xm-cheek" cx="19" cy="40.5" rx="3.6" ry="2.2" fill="#ee8fae" opacity="0.5"/>
      <ellipse class="xm-cheek" cx="45" cy="40.5" rx="3.6" ry="2.2" fill="#ee8fae" opacity="0.5"/>
      <g class="xm-face">
        <g class="xm-eyes">
          <g class="xm-open">${eye(25, "l")}${eye(39, "r")}</g>
          <g class="xm-shut" fill="none" stroke="#0f1222" stroke-width="1.8" stroke-linecap="round">
            <path d="M22 33.5 Q25 36.2 28 33.5"/><path d="M36 33.5 Q39 36.2 42 33.5"/>
          </g>
          <g class="xm-joy" fill="none" stroke="#0f1222" stroke-width="1.9" stroke-linecap="round">
            <path d="M22 34.5 Q25 30.5 28 34.5"/><path d="M36 34.5 Q39 30.5 42 34.5"/>
          </g>
          <g class="xm-dizzy" fill="none" stroke="#0f1222" stroke-width="1.3" stroke-linecap="round">
            <path class="xm-swirl" d="M25 33 m-0.6 0 a0.6 0.6 0 1 1 1.2 0 a1.4 1.4 0 1 1 -2.8 0 a2.2 2.2 0 1 1 4.4 0"/>
            <path class="xm-swirl" d="M39 33 m-0.6 0 a0.6 0.6 0 1 1 1.2 0 a1.4 1.4 0 1 1 -2.8 0 a2.2 2.2 0 1 1 4.4 0"/>
          </g>
        </g>
        <path class="xm-mouth" fill="#0f1222" stroke="#0f1222" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
      </g>
      <path class="xm-sweat" d="M49 24 q2.2 3.4 0 4.6 q-2.2 -1.2 0 -4.6z" fill="#9fd8f0" opacity="0"/>
    </g>
  </g>
  <g class="xm-orbit">
    <path d="M32 3.5 L33.2 6.6 L36.3 7.8 L33.2 9 L32 12.1 L30.8 9 L27.7 7.8 L30.8 6.6 Z" fill="#ffd06b"/>
  </g>
  <text class="xm-z" x="50" y="18" font-size="9" font-family="Fraunces, serif" font-style="italic" fill="#fffaf0">z</text>
  <text class="xm-z xm-z2" x="53" y="13" font-size="7" font-family="Fraunces, serif" font-style="italic" fill="#fffaf0">z</text>
</svg>`;
  }

  // Mouth as one shape: corners at 32±w, centre bent by c (smile > 0, frown < 0), opened by o.
  function mouthPath(w, c, o) {
    const y = 40.4, cy = y - c * 0.25;
    const top = y + c, bot = top + Math.max(0, o) * 1.6;
    return `M${(32 - w).toFixed(2)} ${cy.toFixed(2)} Q32 ${top.toFixed(2)} ${(32 + w).toFixed(2)} ${cy.toFixed(2)} ` +
           `Q32 ${bot.toFixed(2)} ${(32 - w).toFixed(2)} ${cy.toFixed(2)}Z`;
  }

  // Tonight's real moon phase (0 new … 0.5 full … 1 new), for the big moon on the home screen.
  function lunarPhase(d = new Date()) {
    const SYNODIC = 29.530588853, NEW_MOON = Date.UTC(2000, 0, 6, 18, 14);
    return (((d - NEW_MOON) / 86400000 / SYNODIC) % 1 + 1) % 1;
  }
  function phaseName(p) {
    const names = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous", "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"];
    return names[Math.round(p * 8) % 8];
  }
  function phasePath(p) {
    const k = Math.cos(2 * Math.PI * p), rx = (22 * Math.abs(k)).toFixed(2);
    const waxing = p < 0.5;
    const limb = waxing ? 0 : 1;                       // shadow on the left while waxing
    const term = waxing ? (k > 0 ? 0 : 1) : (k > 0 ? 1 : 0);
    return `M32 12 A22 22 0 0 ${limb} 32 56 A${rx} 22 0 0 ${term} 32 12Z`;
  }

  // ---------------------------------------------------------------- springs
  class Spring {
    constructor(v, k = 170, d = 18) { this.x = v; this.v = 0; this.t = v; this.k = k; this.d = d; }
    step(dt) {
      if (reduceMotion) { this.x = this.t; this.v = 0; return this.x; }
      const a = this.k * (this.t - this.x) - this.d * this.v;
      this.v += a * dt; this.x += this.v * dt;
      return this.x;
    }
    kick(v) { if (!reduceMotion) this.v += v; }
  }

  // ---------------------------------------------------------------- shared state
  const S = {
    mood: "idle",            // idle | thinking | searching | working | waiting | listening | error | sleepy
    moodSince: now(),
    happyUntil: 0, errorUntil: 0, dizzyUntil: 0, yawnUntil: 0, curiousUntil: 0, startleUntil: 0, oohUntil: 0,
    talk: 0,                 // energy from streaming text, decays
    pointer: null, pointerAt: 0,
    typingAt: 0,
    lookAtPoint: null, lookAtUntil: 0,   // something worth a glance (a shooting star, an approval card)
    lastActivity: Date.now(),
    wander: { x: 0, y: -0.6 },
  };
  const rigs = new Map();    // element -> rig

  function rigFor(el) {
    let r = rigs.get(el);
    if (r) return r;
    const q = (s) => el.querySelector(s);
    r = {
      el, svg: q("svg"),
      rig: q(".xm-rig"), eyes: q(".xm-eyes"), open: q(".xm-open"), shut: q(".xm-shut"), joy: q(".xm-joy"),
      dizzy: q(".xm-dizzy"), mouth: q(".xm-mouth"), cheeks: el.querySelectorAll(".xm-cheek"), sweat: q(".xm-sweat"),
      irises: el.querySelectorAll(".xm-iris"), eyeL: q(".xm-eye-l"), eyeR: q(".xm-eye-r"),
      // body
      x: new Spring(0, 120, 14), y: new Spring(0, 120, 12), rot: new Spring(0, 90, 11),
      sx: new Spring(1, 260, 13), sy: new Spring(1, 260, 13),
      // face
      lx: new Spring(0, 200, 22), ly: new Spring(0, 200, 22), lid: new Spring(1, 140, 20), pupil: new Spring(1, 120, 16),
      mw: new Spring(2.6, 220, 22), mc: new Spring(2.6, 220, 22), mo: new Spring(0, 320, 22), cheek: new Spring(0.5, 60, 14),
      // dragging (hero only), in CSS pixels
      dx: new Spring(0, 140, 9), dy: new Spring(0, 140, 9),
      blinkAt: now() + 1500 + Math.random() * 3000, blinkT: -1, double: false,
      hero: el.classList.contains("xod-mascot--hero"),
    };
    rigs.set(el, r);
    if (r.hero) {
      const p = lunarPhase();
      const ph = q(".xm-phase");
      // Shade the dark part only when there's a visible crescent or gibbous; near new or full the disc stays bright.
      const lit = (1 - Math.cos(2 * Math.PI * p)) / 2;
      if (ph && lit > 0.08 && lit < 0.92) { ph.setAttribute("d", phasePath(p)); ph.setAttribute("opacity", "0.14"); }
      el.title = `Tonight's moon: ${phaseName(p).toLowerCase()}`;
    }
    return r;
  }

  // The header and hero moons are always alive; in the chat only the newest reply's moon is.
  function liveMascots() {
    const live = [...document.querySelectorAll(".xod-mascot:not(.xod-mascot--chat)")];
    const chat = document.querySelectorAll(".xod-mascot--chat");
    if (chat.length) live.push(chat[chat.length - 1]);
    return live.filter((m) => m.querySelector("svg") && m.offsetParent !== null);
  }

  function mountMascots(root = document) {
    root.querySelectorAll(".xod-mascot").forEach((m) => {
      if (!m.querySelector("svg")) {
        m.innerHTML = moonSvg();
        rigFor(m);
      }
    });
    // Older chat moons settle into a still, smiling pose.
    const chat = document.querySelectorAll(".xod-mascot--chat");
    for (let i = 0; i < chat.length - 1; i++) {
      const r = rigs.get(chat[i]);
      if (r && !r.settled) { r.settled = true; paintStill(r); }
    }
  }

  function paintStill(r) {
    if (r.rig) r.rig.setAttribute("transform", "");
    if (r.eyes) r.eyes.setAttribute("transform", "");
    if (r.mouth) r.mouth.setAttribute("d", mouthPath(2.6, 2.6, 0));
    r.el.dataset.mood = "idle";
    for (const g of [r.shut, r.joy, r.dizzy]) if (g) g.style.opacity = 0;
    if (r.open) r.open.style.opacity = 1;
  }

  // ---------------------------------------------------------------- moods
  function setMood(m) {
    if (S.mood !== m) { S.mood = m; S.moodSince = now(); }
  }
  function beHappy(ms = 1600) {
    S.happyUntil = now() + ms;
    for (const r of rigs.values()) { r.y.kick(-60); r.sy.kick(-3); r.sx.kick(3); }
  }
  function noteActivity() {
    S.lastActivity = Date.now();
    if (S.mood === "sleepy") {
      setMood("idle");
      S.startleUntil = now() + 700;
      for (const r of rigs.values()) { r.sy.kick(4); r.y.kick(-40); r.blinkAt = now() + 250; r.double = true; }
    }
  }

  // ---------------------------------------------------------------- per-frame
  let last = now();
  function frame() {
    const t = now();
    const dt = Math.min(0.05, (t - last) / 1000);
    last = t;
    S.talk *= Math.pow(0.08, dt);              // talking fades within about a second of the last words

    const idleMs = Date.now() - S.lastActivity;
    if (S.mood === "idle" && idleMs > 2 * 60 * 1000) setMood("sleepy");
    const drowsy = S.mood === "idle" && idleMs > 75 * 1000;
    if (drowsy && t > S.yawnUntil + 9000 && Math.random() < dt * 0.15) S.yawnUntil = t + 1500;

    for (const el of liveMascots()) {
      const r = rigFor(el);
      if (r.settled) continue;
      animate(r, t, dt, drowsy);
    }
    requestAnimationFrame(frame);
  }

  function attentionPoint(r, t) {
    if (S.lookAtPoint && t < S.lookAtUntil) return S.lookAtPoint;
    if (t - S.typingAt < 1800) {
      const input = document.getElementById("chatInput");
      if (input && input.offsetParent) {
        const b = input.getBoundingClientRect();
        const frac = clamp((input.value || "").length / 60, 0, 1);
        return { x: b.left + 20 + frac * (b.width - 40), y: b.top + b.height / 2 };
      }
    }
    if (S.pointer && t - S.pointerAt < 5000) return S.pointer;
    return null;
  }

  function animate(r, t, dt, drowsy) {
    const happy = t < S.happyUntil, dizzy = t < S.dizzyUntil, error = t < S.errorUntil;
    const yawn = t < S.yawnUntil, startle = t < S.startleUntil, ooh = t < S.oohUntil;
    const mood = happy ? "happy" : error ? "error" : S.mood;
    if (r.el.dataset.mood !== mood) r.el.dataset.mood = mood;
    const talking = S.talk > 0.04 && !happy && !error;
    const ts = t / 1000;

    // ---- where to look
    let lx = 0, ly = 0;
    const box = r.el.getBoundingClientRect();
    const cx = box.left + box.width / 2, cy = box.top + box.height / 2;
    const pt = attentionPoint(r, t);
    let toward = 0;
    if (mood === "searching") {
      lx = Math.sin(ts * 5.2) * 2.6; ly = -0.4;                      // reading across a page
    } else if (mood === "thinking" || mood === "working") {
      if (Math.random() < dt * 1.2) S.wander = { x: Math.random() * 2 - 1, y: -0.3 - Math.random() * 0.7 };
      lx = S.wander.x * 2.4; ly = S.wander.y * 2;
    } else if (mood === "error") {
      lx = 0; ly = 1.8;
    } else if (pt) {
      const dx = pt.x - cx, dy = pt.y - cy, dist = Math.hypot(dx, dy) || 1;
      const reach = Math.min(1, dist / 160);
      lx = (dx / dist) * 2.6 * reach; ly = (dy / dist) * 2.2 * reach;
      toward = clamp(dx / 400, -1, 1);
    } else if (mood === "idle") {
      if (Math.random() < dt * 0.35) S.wander = { x: Math.random() * 2 - 1, y: Math.random() * 1.2 - 0.6 };
      lx = S.wander.x * 1.8; ly = S.wander.y * 1.5;
    }
    if (dizzy) { lx = Math.cos(ts * 9) * 1.4; ly = Math.sin(ts * 9) * 1.4; }
    r.lx.t = lx; r.ly.t = ly;

    // ---- body
    const breath = mood === "sleepy" ? 0.035 * Math.sin(ts * 1.4) : 0.012 * Math.sin(ts * 2.1);
    let rot = toward * 6, y = 0, x = toward * 1.2;
    if (mood === "waiting") rot = 9;
    if (t < S.curiousUntil) rot = -10;
    if (mood === "listening") { y = -1; }
    if (mood === "thinking" || mood === "working") rot = Math.sin(ts * 1.3) * 4;
    if (dizzy) rot = Math.sin(ts * 7) * 10;
    if (mood === "error") x = Math.sin(ts * 40) * (t < S.errorUntil - 3200 ? 0.6 : 0);
    if (mood === "sleepy") rot = -6;
    r.rot.t = rot; r.y.t = y; r.x.t = x;
    r.sy.t = 1 + breath + (mood === "listening" ? 0.03 : 0); r.sx.t = 1 - breath * 0.6;

    // ---- eyelids and pupils
    let lid = 1;
    if (mood === "working") lid = 0.72;
    if (mood === "error") lid = 0.8;
    if (drowsy) lid = clamp(1 - (Date.now() - S.lastActivity - 75000) / 45000, 0.35, 1);
    if (startle || mood === "listening" || ooh) lid = 1.15;
    r.lid.t = lid;
    let pupil = 1;
    if (pt && mood === "idle") {
      const d = Math.hypot(pt.x - cx, pt.y - cy);
      pupil = d < 90 ? 1.18 : 1;                                       // a little delighted when you're close
    }
    if (mood === "listening" || ooh) pupil = 1.25;
    r.pupil.t = pupil;

    // blinking
    if (t > r.blinkAt && r.blinkT < 0 && mood !== "sleepy" && !happy && !dizzy) r.blinkT = 0;
    let blink = 1;
    if (r.blinkT >= 0) {
      r.blinkT += dt;
      const p = r.blinkT / 0.16;
      blink = p < 0.5 ? 1 - p * 1.85 : Math.min(1, 0.075 + (p - 0.5) * 1.85);
      if (p >= 1) {
        r.blinkT = -1;
        if (r.double || Math.random() < 0.18) { r.double = false; r.blinkAt = t + 120; }
        else r.blinkAt = t + 2400 + Math.random() * 4500;
      }
    }

    // which eyes
    const eyesShut = mood === "sleepy" || yawn;
    const eyesJoy = happy || r.petting > 0.6;
    const set = dizzy ? "dizzy" : eyesJoy ? "joy" : eyesShut ? "shut" : "open";
    for (const [name, g] of [["open", r.open], ["shut", r.shut], ["joy", r.joy], ["dizzy", r.dizzy]]) {
      if (g) { const o = name === set ? "1" : "0"; if (g.style.opacity !== o) g.style.opacity = o; }
    }

    // ---- mouth
    let mw = 2.6, mc = 2.6, mo = 0;
    if (mood === "thinking" || mood === "searching") { mw = 1.3; mc = 0.2; mo = 0.9; }
    if (mood === "working") { mw = 1.9; mc = 0.6; mo = 0; }
    if (mood === "waiting") { mw = 2; mc = 0.4; mo = 0; }
    if (mood === "listening" || ooh) { mw = 1.4; mc = 0.2; mo = 1.4; }
    if (mood === "error") { mw = 2.2; mc = -1.7; mo = 0; }
    if (mood === "sleepy") { mw = 1.1; mc = 0.2; mo = 0.6 + Math.sin(ts * 1.4) * 0.4; }
    if (talking) {
      mw = 2.3; mc = 1.4;
      mo = clamp(S.talk, 0, 1) * (1.1 + Math.sin(ts * 21) * 0.9 + Math.sin(ts * 13) * 0.5);
    }
    if (yawn) { mw = 1.9; mc = 0.3; mo = 3.2 * Math.sin(Math.PI * clamp(1 - (S.yawnUntil - t) / 1500, 0, 1)); }
    if (happy || r.petting > 0.6) { mw = 3.3; mc = 3.1; mo = 1.6; }
    r.mw.t = mw; r.mc.t = mc; r.mo.t = Math.max(0, mo);

    r.cheek.t = happy || r.petting > 0.3 ? 0.95 : mood === "error" ? 0.15 : 0.5;
    r.petting = Math.max(0, (r.petting || 0) - dt * 0.5);

    // ---- integrate
    for (const s of [r.x, r.y, r.rot, r.sx, r.sy, r.lx, r.ly, r.lid, r.pupil, r.mw, r.mc, r.mo, r.cheek, r.dx, r.dy]) s.step(dt);

    // ---- paint
    const sxv = r.sx.x, syv = r.sy.x;
    // Squash and stretch from the bottom of the moon, so it sits on the ground when it lands.
    r.rig.setAttribute("transform",
      `translate(${r.x.x.toFixed(2)} ${(r.y.x / 10).toFixed(2)}) rotate(${r.rot.x.toFixed(2)} 32 46) ` +
      `translate(32 56) scale(${sxv.toFixed(3)} ${syv.toFixed(3)}) translate(-32 -56)`);
    r.eyes.setAttribute("transform", `translate(${r.lx.x.toFixed(2)} ${r.ly.x.toFixed(2)})`);
    const open = clamp(r.lid.x * blink, 0.05, 1.25);
    for (const [g, ex] of [[r.eyeL, 25], [r.eyeR, 39]]) {
      if (g) g.setAttribute("transform", `translate(${ex} 33) scale(${(0.9 + 0.1 * r.pupil.x).toFixed(3)} ${(open * r.pupil.x).toFixed(3)}) translate(${-ex} -33)`);
    }
    r.mouth.setAttribute("d", mouthPath(r.mw.x, r.mc.x, r.mo.x));
    r.cheeks.forEach((c) => c.setAttribute("opacity", clamp(r.cheek.x, 0, 1).toFixed(2)));
    if (r.sweat) r.sweat.setAttribute("opacity", mood === "error" ? "0.9" : "0");
    if (r.hero && (Math.abs(r.dx.x) > 0.1 || Math.abs(r.dy.x) > 0.1 || r.dragging)) {
      r.el.style.translate = `${r.dx.x.toFixed(1)}px ${r.dy.x.toFixed(1)}px`;
    } else if (r.el.style.translate) {
      r.el.style.translate = "";
    }
  }

  // ---------------------------------------------------------------- speech bubble & sparkles
  let bubbleTimer = null;
  function say(text, ms = 3200) {
    const bubble = document.getElementById("xodBubble");
    if (!bubble || !bubble.offsetParent) return;
    bubble.textContent = text;
    bubble.classList.add("is-visible");
    S.talk = Math.min(1, S.talk + 0.9);
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(() => bubble.classList.remove("is-visible"), ms);
  }

  const SPARK_COLORS = ["#f2b24a", "#ffd06b", "#ee8fae", "#6fd0bf", "#fffaf0"];
  function sparkleBurst(el, count = 9) {
    if (reduceMotion || !el) return;
    const b = el.getBoundingClientRect();
    if (!b.width) return;
    const cx = b.left + b.width / 2, cy = b.top + b.height / 2;
    for (let i = 0; i < count; i++) {
      const s = document.createElement("span");
      s.className = "sparkle";
      const angle = (i / count) * Math.PI * 2 + Math.random() * 0.5;
      const dist = 22 + Math.random() * 26;
      s.style.left = `${cx - 4}px`;
      s.style.top = `${cy - 4}px`;
      s.style.setProperty("--dx", `${Math.cos(angle) * dist}px`);
      s.style.setProperty("--dy", `${Math.sin(angle) * dist}px`);
      s.style.setProperty("--c", SPARK_COLORS[i % SPARK_COLORS.length]);
      document.body.appendChild(s);
      setTimeout(() => s.remove(), 950);
    }
  }

  function heart(el) {
    if (reduceMotion) return;
    const b = el.getBoundingClientRect();
    const h = document.createElement("span");
    h.className = "xm-heart";
    h.textContent = "♥";
    h.style.left = `${b.left + b.width * (0.55 + Math.random() * 0.3)}px`;
    h.style.top = `${b.top + b.height * 0.15}px`;
    document.body.appendChild(h);
    setTimeout(() => h.remove(), 1300);
  }

  // ---------------------------------------------------------------- touching the moon
  const HELLOS = [
    "Hi! I'm Xod. I like questions.",
    "Ask me something you've been wondering about.",
    "Mostly moon. Partly search engine.",
    "I'll do the digging. You do the deciding.",
    "That tickles.",
    "No question is too small. Or too big, honestly.",
  ];
  let helloIndex = 0;
  let boops = [];

  function onMascotClick(m) {
    const r = rigFor(m);
    if (r.justDragged) { r.justDragged = false; return; }
    noteActivity();
    const t = now();
    boops = boops.filter((b) => t - b < 4000);
    boops.push(t);
    if (boops.length >= 6) {
      boops = [];
      m.classList.remove("is-spinning");
      void m.offsetWidth;
      m.classList.add("is-spinning");
      setTimeout(() => m.classList.remove("is-spinning"), 950);
      sparkleBurst(m, 14);
      S.dizzyUntil = t + 2200;
      if (r.hero) setTimeout(() => say("Whoa. The stars are spinning.", 2200), 900);
      return;
    }
    r.sy.kick(-5); r.sx.kick(5); r.y.kick(20);
    S.happyUntil = t + 900;
    if (r.hero) { say(HELLOS[helloIndex % HELLOS.length]); helloIndex++; }
  }

  // Grab the big moon and let go: it springs back. Moving the cursor back and forth over it is petting.
  function wireTouch() {
    let drag = null;
    document.addEventListener("pointerdown", (e) => {
      const m = e.target.closest(".xod-mascot--hero");
      if (!m || reduceMotion) return;
      const r = rigFor(m);
      drag = { r, x0: e.clientX, y0: e.clientY, lastX: e.clientX, lastY: e.clientY, lastT: now(), vx: 0, vy: 0, moved: false };
      m.setPointerCapture?.(e.pointerId);
    });
    document.addEventListener("pointermove", (e) => {
      const moveX = S.pointer ? e.clientX - S.pointer.x : 0;
      S.pointer = { x: e.clientX, y: e.clientY };
      S.pointerAt = now();
      noteActivity();
      if (drag) {
        const ddx = e.clientX - drag.x0, ddy = e.clientY - drag.y0;
        if (!drag.moved && Math.hypot(ddx, ddy) > 5) { drag.moved = true; drag.r.dragging = true; }
        if (drag.moved) {
          const band = (d) => (d * 60) / (60 + Math.abs(d));         // stretchy, never far
          drag.r.dx.x = drag.r.dx.t = band(ddx);
          drag.r.dy.x = drag.r.dy.t = band(ddy);
          const t = now(), k = Math.max(1, t - drag.lastT);
          drag.vx = (e.clientX - drag.lastX) / k; drag.vy = (e.clientY - drag.lastY) / k;
          drag.lastX = e.clientX; drag.lastY = e.clientY; drag.lastT = t;
          drag.r.rot.t = clamp(ddx / 4, -18, 18);
          S.oohUntil = now() + 300;
        }
        return;
      }
      // petting: quick back-and-forth across a live moon
      const m = e.target.closest?.(".xod-mascot");
      if (m && Math.abs(moveX) > 2) {
        const r = rigFor(m);
        const dir = Math.sign(moveX);
        if (r.lastDir && dir !== r.lastDir) {
          // A real back-and-forth: several quick reversals in a row.
          const t = now();
          r.flips = (r.flips || []).filter((f) => t - f < 1200);
          r.flips.push(t);
          if (r.flips.length >= 4) r.petting = Math.min(1.4, (r.petting || 0) + 0.25);
          if (r.petting > 0.9 && (!r.heartAt || now() - r.heartAt > 700)) { r.heartAt = now(); heart(m); }
          r.sx.kick(1.2); r.rot.kick(dir * 20);
        }
        r.lastDir = dir;
      }
    }, { passive: true });
    const release = () => {
      if (!drag) return;
      const r = drag.r;
      if (drag.moved) {
        r.justDragged = true;
        setTimeout(() => { r.justDragged = false; }, 50);
        r.dragging = false;
        r.dx.t = 0; r.dy.t = 0;
        r.dx.kick(-drag.vx * 300); r.dy.kick(-drag.vy * 300);
        const fast = Math.hypot(drag.vx, drag.vy) > 1.2 || Math.hypot(r.dx.x, r.dy.x) > 35;
        r.sy.kick(-4); r.sx.kick(4);
        if (fast) { S.happyUntil = now() + 1200; say("Wheee.", 1500); sparkleBurst(r.el, 8); }
      }
      drag = null;
    };
    document.addEventListener("pointerup", release);
    document.addEventListener("pointercancel", release);
    document.addEventListener("click", (e) => {
      const m = e.target.closest(".xod-mascot");
      if (m) onMascotClick(m);
    });
  }

  // ---------------------------------------------------------------- what the agent is doing
  const SEARCHY = /search|browse|browser|web|fetch|navigate|scrape|crawl|read_url|page|news|reddit|hacker/i;
  let wasBusy = false;
  function onAgentEvent(ev) {
    if (!ev || !ev.type) return;
    switch (ev.type) {
      case "tool_call":
      case "subagent_tool_call":
        setMood(SEARCHY.test(ev.name || "") ? "searching" : "working");
        break;
      case "tool_result":
        if (ev.data && ev.data.needs_approval) waitForApproval();
        else if (S.mood === "searching" || S.mood === "working") setMood("thinking");
        break;
      case "status":
        if (/approv|confirm|your ok|permission/i.test(ev.content || "")) waitForApproval();
        break;
      case "content":
        S.talk = Math.min(1, S.talk + Math.min(0.6, (ev.content || "").length / 30));
        if (S.mood === "searching" || S.mood === "working") setMood("thinking");
        break;
      case "error":
        S.errorUntil = now() + 3600;
        break;
    }
  }

  function waitForApproval() {
    setMood("waiting");
    const card = document.querySelector(".mcp-approval, [data-approval], .approval-card");
    if (card) {
      const b = card.getBoundingClientRect();
      S.lookAtPoint = { x: b.left + b.width / 2, y: b.top + b.height / 2 };
      S.lookAtUntil = now() + 4000;
    }
  }

  function watchAgent() {
    let busy = false;
    try { busy = typeof isStreaming !== "undefined" && !!isStreaming; } catch (_) {}
    busy = busy || !!window.__xodTasksWorking;
    let listening = false;
    try { listening = typeof isJarvisListening !== "undefined" && !!isJarvisListening; } catch (_) {}

    if (listening) setMood("listening");
    else if (busy) {
      S.lastActivity = Date.now();
      if (!["searching", "working", "waiting", "thinking"].includes(S.mood)) setMood("thinking");
    } else if (S.mood !== "idle" && S.mood !== "sleepy") setMood("idle");

    if (wasBusy && !busy) {
      beHappy();
      sparkleBurst(document.querySelector(".xod-mascot--small"));
    }
    wasBusy = busy;
  }

  // ---------------------------------------------------------------- the rest of the world
  function wireWorld() {
    const input = document.getElementById("chatInput");
    if (input) {
      input.addEventListener("input", () => {
        S.typingAt = now();
        noteActivity();
        for (const el of liveMascots()) { const r = rigFor(el); r.y.kick(6); }   // a small nod per keystroke
        if (/\?\s*$/.test(input.value)) S.curiousUntil = now() + 1600;
      });
    }
    document.addEventListener("keydown", noteActivity, { passive: true });

    // Back after a while: a little hello.
    let hiddenAt = 0;
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { hiddenAt = Date.now(); return; }
      if (hiddenAt && Date.now() - hiddenAt > 10 * 60 * 1000) {
        noteActivity();
        beHappy(1200);
        setTimeout(() => say("Welcome back.", 2400), 400);
      }
    });

    // Shooting stars are worth a look.
    window.addEventListener("xod:shooting-star", (e) => {
      S.lookAtPoint = e.detail; S.lookAtUntil = now() + 1300; S.oohUntil = now() + 1100;
    });
    window.addEventListener("xod:agent", (e) => onAgentEvent(e.detail));
  }

  function init() {
    mountMascots();
    new MutationObserver((records) => {
      for (const rec of records) {
        for (const n of rec.addedNodes) {
          if (n.nodeType === 1 && (n.matches(".xod-mascot") || n.querySelector(".xod-mascot"))) {
            mountMascots(document);
            return;
          }
        }
      }
    }).observe(document.body, { childList: true, subtree: true });
    wireTouch();
    wireWorld();
    try {
      if (!localStorage.getItem("xod.saidHello")) {
        setTimeout(() => say("Hi! I'm Xod. Pick a card, or ask me anything.", 5000), 1100);
        localStorage.setItem("xod.saidHello", "1");
      }
    } catch (_) {}
    setInterval(watchAgent, 250);
    requestAnimationFrame(frame);
  }

  window.XodMascot = { say, sparkleBurst, beHappy, lunarPhase, phaseName, mood: () => S.mood };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
