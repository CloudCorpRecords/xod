// ==============================================================
// XOD - the companion
// A small character with a body that moves on springs, eyes that
// look at what matters, a mouth that talks while answers stream,
// and moods that follow what the agent is doing. It comes in a few
// looks (moon, cat, ghost, robot, owl, sprout): they share one face,
// so every mood works for all of them, and each adds its own moving
// parts and habits. Decorative only: if anything here fails, the app
// keeps working.
// ==============================================================
(() => {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const now = () => performance.now();
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  let uid = 0;

  // ---------------------------------------------------------------- characters
  // The shared face sits at fixed points: eyes at (25,33) and (39,33), mouth at (32,40), cheeks at
  // (19,40.5) and (45,40.5). A character draws a body around it and may add parts that move:
  // any element with data-part="name" data-pivot="x y" (and data-mode="rotate|scale|y") gets a
  // spring, which the character's drive() aims every frame.
  const part = (name, pivot, mode = "rotate") => `data-part="${name}" data-pivot="${pivot}" data-mode="${mode}"`;
  const grad = (id, stops, cx = "38%", cy = "30%") =>
    `<radialGradient id="${id}" cx="${cx}" cy="${cy}" r="75%">${stops.map(([o, c]) => `<stop offset="${o}%" stop-color="${c}"/>`).join("")}</radialGradient>`;

  const CHARACTERS = {
    moon: {
      name: "Moon", blurb: "The original. Follows tonight's real phase.",
      glow: "#f2b24a",
      travel: "fly", trail: "star",
      body: (id) => `
        <defs>${grad(`${id}-b`, [[0, "#fff7e2"], [55, "#ffdb8f"], [100, "#e9a443"]])}</defs>
        <circle cx="32" cy="34" r="22" fill="url(#${id}-b)"/>
        <circle cx="44" cy="23" r="3.2" fill="#c98a2e" opacity="0.22"/>
        <circle cx="47.5" cy="43" r="1.9" fill="#c98a2e" opacity="0.2"/>
        <circle cx="18.5" cy="25" r="1.5" fill="#c98a2e" opacity="0.2"/>
        <path class="xm-phase" clip-path="url(#${id}-disc)" fill="#0f1222" opacity="0"/>`,
      special: "eclipse",
      hellos: ["Hi! I'm Xod. I like questions.", "Mostly moon. Partly search engine.", "I'll do the digging. You do the deciding."],
    },
    cat: {
      name: "Cat", blurb: "Twitchy ears, a swishy tail, and opinions.",
      glow: "#a196ec",
      travel: "pounce", trail: "paw",
      behind: () => `
        <path ${part("tail", "46 51")} d="M45 51 C55 53 60 47 58.5 38 C57.8 33 59.5 29.5 63 29.5" fill="none" stroke="#8a7fd6" stroke-width="4.2" stroke-linecap="round"/>
        <g ${part("earL", "20 22")}><path d="M12 28 L15 7 L28 17Z" fill="#a59bea"/><path d="M15.5 22 L16.8 12 L23.5 17.5Z" fill="#ee8fae" opacity="0.65"/></g>
        <g ${part("earR", "44 22")}><path d="M52 28 L49 7 L36 17Z" fill="#a59bea"/><path d="M48.5 22 L47.2 12 L40.5 17.5Z" fill="#ee8fae" opacity="0.65"/></g>`,
      body: (id) => `
        <defs>${grad(`${id}-b`, [[0, "#f1edff"], [55, "#c9c1f6"], [100, "#9187dc"]])}</defs>
        <ellipse cx="32" cy="36" rx="22" ry="20.5" fill="url(#${id}-b)"/>
        <g stroke="#6c62b8" stroke-width="0.7" stroke-linecap="round" opacity="0.55">
          <path d="M14 39 L6 37.5"/><path d="M14 41.5 L6.5 42.5"/><path d="M50 39 L58 37.5"/><path d="M50 41.5 L57.5 42.5"/>
        </g>`,
      front: () => `<path d="M30.7 37.3 L33.3 37.3 L32 38.7Z" fill="#ee8fae"/>`,
      special: "twitch",
      hellos: ["Mrrp. Hi.", "I knocked nothing off your desk. Yet.", "Ask me something. I'll pretend I'm not interested."],
    },
    ghost: {
      name: "Ghost", blurb: "Floats, waves, and sometimes vanishes.",
      glow: "#9fe7ff",
      travel: "phase", trail: "wisp",
      float: true,
      behind: () => `
        <ellipse ${part("armL", "14 38")} cx="10" cy="40" rx="4" ry="3" fill="#cfe7f3"/>
        <ellipse ${part("armR", "50 38")} cx="54" cy="40" rx="4" ry="3" fill="#cfe7f3"/>`,
      body: (id) => `
        <defs>${grad(`${id}-b`, [[0, "#ffffff"], [60, "#e3f2fa"], [100, "#a9cfe2"]])}</defs>
        <path class="xc-hem" d="${ghostPath(0)}" fill="url(#${id}-b)"/>`,
      special: "vanish",
      hellos: ["Boo. Did I get you?", "I'm mostly see-through. My answers aren't.", "Haunting your Mac, politely."],
    },
    robot: {
      name: "Robot", blurb: "A tidy little bot. The light shows what it's doing.",
      glow: "#6fd0bf",
      travel: "zip", trail: "pixel",
      ink: "#7cf3e0", cheek: "#ee8fae",
      behind: () => `
        <g ${part("antenna", "32 14")}>
          <path d="M32 14 L32 5.5" stroke="#9fb0c4" stroke-width="1.6" stroke-linecap="round"/>
          <circle class="xc-bulb" cx="32" cy="4.5" r="2.7"/>
        </g>
        <rect x="6.5" y="29" width="4.5" height="11" rx="2" fill="#9fb0c4"/>
        <rect x="53" y="29" width="4.5" height="11" rx="2" fill="#9fb0c4"/>`,
      body: (id) => `
        <defs>${grad(`${id}-b`, [[0, "#f6f9fc"], [55, "#cdd8e4"], [100, "#93a6bb"]])}</defs>
        <rect x="10" y="13.5" width="44" height="41" rx="13" fill="url(#${id}-b)"/>
        <rect x="14.5" y="22.5" width="35" height="25" rx="8.5" fill="#151a33"/>
        <rect class="xc-scan" x="15.5" y="24" width="33" height="1.2" rx="0.6" fill="#7cf3e0" opacity="0"/>
        <circle cx="16" cy="51" r="1" fill="#93a6bb"/><circle cx="48" cy="51" r="1" fill="#93a6bb"/>`,
      cheekOpacity: 0.35,
      special: "scan",
      hellos: ["Beep. Hello. Boop.", "Running on your Mac and good vibes.", "Ready when you are. I don't do coffee breaks."],
    },
    owl: {
      name: "Owl", blurb: "A night owl. Big eyes, small wings, wise-ish.",
      glow: "#f2b24a",
      travel: "flap", trail: "feather",
      behind: () => `
        <ellipse ${part("wingL", "15 33")} cx="12" cy="41" rx="5" ry="10" fill="#9a6337"/>
        <ellipse ${part("wingR", "49 33")} cx="52" cy="41" rx="5" ry="10" fill="#9a6337"/>
        <path ${part("tuftL", "20 17")} d="M15 20 L16.5 8 L25.5 15Z" fill="#b97a45"/>
        <path ${part("tuftR", "44 17")} d="M49 20 L47.5 8 L38.5 15Z" fill="#b97a45"/>`,
      body: (id) => `
        <defs>${grad(`${id}-b`, [[0, "#f6d6ab"], [55, "#d49a63"], [100, "#9a6337"]])}</defs>
        <ellipse cx="32" cy="35" rx="21.5" ry="21" fill="url(#${id}-b)"/>
        <ellipse cx="32" cy="47" rx="11.5" ry="8" fill="#fbe8cc" opacity="0.75"/>
        <g fill="none" stroke="#c9905a" stroke-width="0.7" stroke-linecap="round" opacity="0.8">
          <path d="M27 46 l1.5 1.4 l1.5 -1.4"/><path d="M34 46 l1.5 1.4 l1.5 -1.4"/><path d="M30.5 50 l1.5 1.4 l1.5 -1.4"/>
        </g>
        <circle cx="25" cy="33" r="6.6" fill="#fffaf0"/><circle cx="39" cy="33" r="6.6" fill="#fffaf0"/>
        <circle cx="25" cy="33" r="6.6" fill="none" stroke="#c9905a" stroke-width="0.8"/>
        <circle cx="39" cy="33" r="6.6" fill="none" stroke="#c9905a" stroke-width="0.8"/>`,
      front: () => `<path d="M30.4 36.4 L33.6 36.4 L32 39.3Z" fill="#f2b24a" stroke="#c98a2e" stroke-width="0.4" stroke-linejoin="round"/>`,
      special: "headturn",
      hellos: ["Hoo. Hi.", "Up late? Me too. Always.", "I've read a lot of pages. Ask me one."],
    },
    sprout: {
      name: "Sprout", blurb: "Bouncy and green. Blooms when it's happy.",
      glow: "#93d17f",
      travel: "hop", trail: "leaf",
      bounce: 1.35,
      behind: () => `
        <g ${part("leaves", "32 17")}>
          <path d="M32 17 L32 8.5" stroke="#5f9b44" stroke-width="1.6" stroke-linecap="round"/>
          <path d="M32 12.5 Q22 3.5 16.5 11.5 Q25 16.5 32 12.5Z" fill="#7cc35a"/>
          <path d="M32 10 Q42.5 0.5 48.5 8 Q40 14 32 10Z" fill="#93d17f"/>
          <g ${part("flower", "32 7.5", "scale")}>
            <g fill="#ee8fae"><circle cx="32" cy="4.4" r="2.4"/><circle cx="35" cy="6.6" r="2.4"/><circle cx="33.9" cy="10" r="2.4"/><circle cx="30.1" cy="10" r="2.4"/><circle cx="29" cy="6.6" r="2.4"/></g>
            <circle cx="32" cy="7.5" r="1.9" fill="#ffd06b"/>
          </g>
        </g>`,
      body: (id) => `
        <defs>${grad(`${id}-b`, [[0, "#ecfbd9"], [55, "#b4e38d"], [100, "#6fae4f"]])}</defs>
        <ellipse cx="32" cy="37" rx="21" ry="19.5" fill="url(#${id}-b)"/>
        <path d="M22 22 Q32 18 42 22" fill="none" stroke="#fffaf0" stroke-width="1.2" stroke-linecap="round" opacity="0.45"/>`,
      special: "bloom",
      hellos: ["Hi! I'm growing on you.", "Water me with questions.", "Small sprout. Big answers."],
    },
  };
  const ORDER = Object.keys(CHARACTERS);
  const GENERIC_HELLOS = ["Ask me something you've been wondering about.", "That tickles.", "No question is too small. Or too big, honestly.", "Right-click me to change how I look."];
  const FIDGETS = [["hop", 700], ["look", 1600], ["wink", 900], ["stretch", 1500], ["hum", 2600], ["wiggle", 900]];
  const SPECIAL_MS = { eclipse: 2400, twitch: 1400, vanish: 2200, scan: 1800, headturn: 2000, bloom: 2600 };

  function ghostPath(ph) {
    // Dome on top, a wavy hem that ripples as it floats.
    const pts = [52, 45.5, 39, 32.5, 26, 19.5, 13];
    let d = "M12 34 A20 20 0 0 1 52 34 L52 52";
    for (let i = 1; i < pts.length; i++) {
      const x0 = pts[i - 1], x1 = pts[i], mid = (x0 + x1) / 2;
      const amp = 3.4 * Math.sin(ph + i * 1.3);
      d += ` Q${mid.toFixed(2)} ${(54 + amp).toFixed(2)} ${x1} 52`;
    }
    return d + " Z";
  }

  // ---------------------------------------------------------------- drawing
  function characterSvg(cid) {
    const c = CHARACTERS[cid] || CHARACTERS.moon;
    const id = `xm${uid++}`;
    const ink = c.ink || "#0f1222";
    const eye = (cx, side) => `
      <g class="xm-eye xm-eye-${side}">
        <ellipse class="xm-iris" cx="${cx}" cy="33" rx="2.6" ry="3.4" fill="${ink}"/>
        <circle class="xm-shine" cx="${cx + 1}" cy="31.7" r="0.95" fill="#fffaf0"/>
        <circle class="xm-shine2" cx="${cx - 0.9}" cy="34.6" r="0.4" fill="#fffaf0" opacity="0.7"/>
      </g>`;
    const cheek = c.cheek || "#ee8fae";
    return `
<svg viewBox="0 0 64 64" aria-hidden="true" data-character="${cid}">
  <defs>
    <radialGradient id="${id}-glow" cx="50%" cy="50%" r="50%">
      <stop offset="40%" stop-color="${c.glow}" stop-opacity="0.45"/>
      <stop offset="100%" stop-color="${c.glow}" stop-opacity="0"/>
    </radialGradient>
    <clipPath id="${id}-disc"><circle cx="32" cy="34" r="22"/></clipPath>
  </defs>
  <g class="xm-rings" fill="none" stroke="${c.glow}" stroke-width="0.8">
    <circle cx="32" cy="34" r="24"/><circle cx="32" cy="34" r="24"/><circle cx="32" cy="34" r="24"/>
  </g>
  <circle class="xm-glow" cx="32" cy="34" r="31" fill="url(#${id}-glow)"/>
  <g class="xm-rig">
    <g class="xm-behind">${c.behind ? c.behind(id) : ""}</g>
    <g class="xm-body">
      ${c.body(id)}
      <ellipse class="xm-cheek" cx="19" cy="40.5" rx="3.6" ry="2.2" fill="${cheek}" opacity="0.5"/>
      <ellipse class="xm-cheek" cx="45" cy="40.5" rx="3.6" ry="2.2" fill="${cheek}" opacity="0.5"/>
      <g class="xm-face">
        <g class="xm-eyes">
          <g class="xm-open">${eye(25, "l")}${eye(39, "r")}</g>
          <g class="xm-shut" fill="none" stroke="${ink}" stroke-width="1.8" stroke-linecap="round">
            <path d="M22 33.5 Q25 36.2 28 33.5"/><path d="M36 33.5 Q39 36.2 42 33.5"/>
          </g>
          <g class="xm-joy" fill="none" stroke="${ink}" stroke-width="1.9" stroke-linecap="round">
            <path d="M22 34.5 Q25 30.5 28 34.5"/><path d="M36 34.5 Q39 30.5 42 34.5"/>
          </g>
          <g class="xm-dizzy" fill="none" stroke="${ink}" stroke-width="1.3" stroke-linecap="round">
            <path class="xm-swirl" d="M25 33 m-0.6 0 a0.6 0.6 0 1 1 1.2 0 a1.4 1.4 0 1 1 -2.8 0 a2.2 2.2 0 1 1 4.4 0"/>
            <path class="xm-swirl" d="M39 33 m-0.6 0 a0.6 0.6 0 1 1 1.2 0 a1.4 1.4 0 1 1 -2.8 0 a2.2 2.2 0 1 1 4.4 0"/>
          </g>
        </g>
        <path class="xm-mouth" d="${mouthPath(2.6, 2.6, 0)}" fill="${ink}" stroke="${ink}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
      </g>
      ${c.front ? c.front(id) : ""}
      <path class="xm-sweat" d="M49 24 q2.2 3.4 0 4.6 q-2.2 -1.2 0 -4.6z" fill="#9fd8f0" opacity="0"/>
    </g>
    <g class="xm-props">
      <g class="xp xp-mag"><circle cx="52" cy="43" r="5" fill="rgba(159,216,240,0.28)" stroke="#fffaf0" stroke-width="1.6"/><path d="M55.6 46.6 L60 51" stroke="#c98a2e" stroke-width="2.4" stroke-linecap="round"/><path d="M49.6 41 Q50.6 39.6 52.2 39.4" fill="none" stroke="#fffaf0" stroke-width="0.9" stroke-linecap="round" opacity="0.8"/></g>
      <g class="xp xp-wrench"><path d="M49 53 L57 45" stroke="#c4d0dd" stroke-width="2.6" stroke-linecap="round"/><circle cx="58" cy="44" r="3.4" fill="#c4d0dd"/><path d="M58 44 L61.5 40.5" stroke="#151a33" stroke-width="2" stroke-linecap="round"/></g>
      <g class="xp xp-quill"><path d="M50 54 Q55 42 62.5 33.5 Q61.5 43 52.5 52.5 Z" fill="#fffaf0"/><path d="M50.2 53.8 Q56 44 62 34.5" fill="none" stroke="#c9c2b4" stroke-width="0.6"/><circle class="xp-ink" cx="49.6" cy="54.6" r="0.9" fill="#0f1222"/></g>
      <g class="xp xp-wand"><path d="M49 54 L58 42" stroke="#e4dccd" stroke-width="1.8" stroke-linecap="round"/><path class="xp-wand-star" d="M58.5 35.5 L59.8 39 L63.3 40.2 L59.8 41.4 L58.5 44.9 L57.2 41.4 L53.7 40.2 L57.2 39 Z" fill="${c.glow}"/></g>
    </g>
    <g class="xm-cap">
      <path d="M17 18.5 Q31 7 44 11.5 Q54 15 57 25 Q50 19.5 43 18 Q30 15.5 17 18.5Z" fill="#6f63c9"/>
      <path d="M16 19 Q30 14.5 44 17.5" fill="none" stroke="#fffaf0" stroke-width="2.6" stroke-linecap="round"/>
      <circle cx="57.5" cy="26" r="2.6" fill="#fffaf0"/>
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
  let character = "moon";
  try { const saved = localStorage.getItem("xod.character"); if (CHARACTERS[saved]) character = saved; } catch (_) {}
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

  const characterOf = (el) => (CHARACTERS[el.dataset.character] ? el.dataset.character : character);

  function rigFor(el) {
    let r = rigs.get(el);
    if (r) return r;
    const q = (s) => el.querySelector(s);
    const cid = q("svg")?.dataset.character || "moon";
    const c = CHARACTERS[cid];
    const b = c.bounce || 1;
    r = {
      el, svg: q("svg"), cid, c,
      rig: q(".xm-rig"), body: q(".xm-body"), eyes: q(".xm-eyes"), open: q(".xm-open"), shut: q(".xm-shut"), joy: q(".xm-joy"),
      dizzy: q(".xm-dizzy"), mouth: q(".xm-mouth"), cheeks: el.querySelectorAll(".xm-cheek"), sweat: q(".xm-sweat"),
      eyeL: q(".xm-eye-l"), eyeR: q(".xm-eye-r"), phase: q(".xm-phase"), hem: q(".xc-hem"), scan: q(".xc-scan"),
      // body; a bouncier character has softer damping
      x: new Spring(0, 120, 14), y: new Spring(0, 120, 12 / b), rot: new Spring(0, 90, 11 / b),
      sx: new Spring(1, 260, 13 / b), sy: new Spring(1, 260, 13 / b),
      // face
      lx: new Spring(0, 200, 22), ly: new Spring(0, 200, 22), lid: new Spring(1, 140, 20), pupil: new Spring(1, 120, 16),
      wink: new Spring(1, 300, 24),
      mw: new Spring(2.6, 220, 22), mc: new Spring(2.6, 220, 22), mo: new Spring(0, 320, 22), cheek: new Spring(0.5, 60, 14),
      fade: new Spring(1, 60, 12),
      // dragging (hero only), in CSS pixels
      dx: new Spring(0, 140, 9), dy: new Spring(0, 140, 9),
      blinkAt: now() + 1500 + Math.random() * 3000, blinkT: -1, double: false,
      hero: el.classList.contains("xod-mascot--hero"),
      preview: el.classList.contains("xod-mascot--preview"),
      parts: {},
      fidget: null, nextFidget: now() + 4000 + Math.random() * 6000,
      notesAt: 0,
    };
    el.querySelectorAll("[data-part]").forEach((p) => {
      const [px, py] = p.dataset.pivot.split(" ").map(Number);
      r.parts[p.dataset.part] = { el: p, px, py, mode: p.dataset.mode, s: new Spring(0, 140, 9 / b) };
    });
    rigs.set(el, r);
    if (r.phase && r.hero) {
      const p = lunarPhase();
      // Shade the dark part only when there's a visible crescent or gibbous; near new or full the disc stays bright.
      const lit = (1 - Math.cos(2 * Math.PI * p)) / 2;
      if (lit > 0.08 && lit < 0.92) { r.restPhase = phasePath(p); r.phase.setAttribute("d", r.restPhase); r.phase.setAttribute("opacity", "0.14"); }
      el.title = `Tonight's moon: ${phaseName(p).toLowerCase()}`;
    } else if (r.hero) {
      el.title = `Hi, I'm Xod (${c.name.toLowerCase()} look). Right-click to change it.`;
    }
    return r;
  }

  // The header and hero are always alive; in the chat only the newest reply's companion is.
  function liveMascots() {
    const live = [...document.querySelectorAll(".xod-mascot:not(.xod-mascot--chat)")];
    const chat = document.querySelectorAll(".xod-mascot--chat");
    if (chat.length) live.push(chat[chat.length - 1]);
    return live.filter((m) => m.querySelector("svg") && (m.offsetParent !== null || m.dataset.live === "1"));
  }

  function mount(m) {
    m.innerHTML = characterSvg(characterOf(m));
    rigs.delete(m);
    rigFor(m);
  }

  function mountMascots(root = document) {
    root.querySelectorAll(".xod-mascot").forEach((m) => {
      const svg = m.querySelector("svg");
      if (!svg || svg.dataset.character !== characterOf(m)) mount(m);
    });
    // Older chat companions settle into a still, smiling pose.
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

  // ---------------------------------------------------------------- choosing a look
  function setCharacter(cid, { save = true, greet = true } = {}) {
    if (!CHARACTERS[cid]) return;
    const changed = cid !== character;
    character = cid;
    try { localStorage.setItem("xod.character", cid); } catch (_) {}
    try { window.dispatchEvent(new CustomEvent("xod:character", { detail: cid })); } catch (_) {}
    if (save) {
      fetch("/api/companion", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ character: cid }) }).catch(() => {});
    }
    if (!changed) return;
    document.querySelectorAll(".xod-mascot").forEach((m) => {
      if (m.dataset.character) return;
      const visible = m.offsetParent !== null && !reduceMotion;
      if (!visible) { mount(m); return; }
      m.classList.remove("is-arriving");
      m.classList.add("is-poofing");
      poof(m);
      setTimeout(() => {
        m.classList.remove("is-poofing");
        mount(m);
        mountMascots();
        void m.offsetWidth;
        m.classList.add("is-arriving");
        const r = rigs.get(m);
        if (r) { r.sy.x = 0.6; r.sx.x = 1.3; }
        setTimeout(() => m.classList.remove("is-arriving"), 700);
      }, 180);
    });
    if (greet) setTimeout(() => { beHappy(1200); say(CHARACTERS[cid].hellos[0], 2600); }, 420);
    document.querySelectorAll("[data-pick-character]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.pickCharacter === cid)));
  }

  async function loadCharacter() {
    try {
      const j = await (await fetch("/api/companion")).json();
      if (j && CHARACTERS[j.character] && j.character !== character) setCharacter(j.character, { save: false, greet: false });
    } catch (_) {}
  }

  function openPicker() {
    let root = document.getElementById("companionPanel");
    if (!root) {
      root = document.createElement("div");
      root.id = "companionPanel";
      root.className = "tasks-panel hidden";
      root.innerHTML = `
        <div class="tasks-scrim" data-close-companion></div>
        <aside class="tasks-drawer" role="dialog" aria-label="Xod's look">
          <header class="px-5 pt-5 pb-3 flex items-start justify-between gap-3 border-b border-white/[0.06]">
            <div><h3 class="font-bold text-lg text-white">Xod's look</h3>
              <p class="text-[12px] text-zinc-400 mt-0.5">Same Xod, different outfit. Each one has its own habits; pet them, drag them, leave them alone for a while.</p></div>
            <button data-close-companion class="p-1 -mr-1 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700" title="Close"><i data-lucide="x" class="size-4"></i></button>
          </header>
          <div class="flex-1 overflow-y-auto px-5 py-4">
            <div class="xc-grid">
              ${ORDER.map((id) => `
                <button type="button" class="xc-card" data-pick-character="${id}" aria-pressed="${id === character}">
                  <span class="xod-mascot xod-mascot--preview" data-character="${id}" data-mood="idle"></span>
                  <span class="xc-name">${CHARACTERS[id].name}</span>
                  <span class="xc-blurb">${CHARACTERS[id].blurb}</span>
                </button>`).join("")}
            </div>
            <p class="text-[12px] text-zinc-500 mt-4">Tip: right-click Xod on the home screen to come back here. Double-click for a flip.</p>
          </div>
        </aside>`;
      document.body.appendChild(root);
      root.addEventListener("click", (e) => {
        if (e.target.closest("[data-close-companion]")) {
          root.classList.remove("is-open");
          setTimeout(() => root.classList.add("hidden"), 250);
          return;
        }
        const card = e.target.closest("[data-pick-character]");
        if (card) {
          setCharacter(card.dataset.pickCharacter);
          const r = rigs.get(card.querySelector(".xod-mascot"));
          if (r) { r.y.kick(-80); r.sy.kick(-4); r.sx.kick(4); }
        }
      });
      if (window.lucide) lucide.createIcons();
    }
    mountMascots(root);
    root.classList.remove("hidden");
    requestAnimationFrame(() => root.classList.add("is-open"));
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
      for (const r of rigs.values()) {
        r.sy.kick(4); r.y.kick(-40); r.blinkAt = now() + 250; r.double = true;
        if (r.hero) glyph(r.el, "!", "is-bang");
      }
    }
  }

  // ---------------------------------------------------------------- per-frame
  let last = now();
  function frame() {
    const t = now();
    const dt = Math.min(0.05, (t - last) / 1000);
    last = t;
    S.talk *= Math.pow(0.08, dt);              // talking fades within about a second of the last words

    const idleMs = S.forced && t < S.forced.until && S.forced.mood === "sleepy" ? 3e5 : Date.now() - S.lastActivity;
    if (S.mood === "idle" && idleMs > 2 * 60 * 1000) setMood("sleepy");
    const drowsy = S.mood === "idle" && idleMs > 75 * 1000;
    if (drowsy && t > S.yawnUntil + 9000 && Math.random() < dt * 0.15) S.yawnUntil = t + 1500;

    if (t - (S.purgedAt || 0) > 5000) {
      S.purgedAt = t;
      for (const el of [...rigs.keys()]) if (!el.isConnected) rigs.delete(el);
    }
    for (const el of liveMascots()) {
      const r = rigFor(el);
      if (r.settled) continue;
      try { animate(r, t, dt, drowsy); } catch (e) { if (!S.warned) { S.warned = true; console.warn("Xod companion:", e); } }
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

  // Small habits while nothing is going on: a hop, a look around, a wink, a stretch, a hum,
  // or the character's own trick.
  function maybeFidget(r, t, mood, drowsy) {
    if (r.fidget && t < r.fidget.until) return;
    r.fidget = null;
    if (reduceMotion || mood !== "idle" || drowsy || r.dragging || (r.petting || 0) > 0.1 || t < S.happyUntil) {
      r.nextFidget = Math.max(r.nextFidget, t + 2500);
      return;
    }
    if (t < r.nextFidget) return;
    const special = r.c.special;
    const useSpecial = special && Math.random() < (r.preview ? 0.6 : 0.35);
    const [name, dur] = useSpecial ? [special, SPECIAL_MS[special]] : pick(FIDGETS);
    r.fidget = { name, start: t, until: t + dur, dur };
    r.nextFidget = t + dur + (r.preview ? 2500 + Math.random() * 3500 : 7000 + Math.random() * 11000);
  }

  function animate(r, t, dt, drowsy) {
    const happy = t < S.happyUntil, dizzy = t < S.dizzyUntil, error = t < S.errorUntil;
    const yawn = t < S.yawnUntil, startle = t < S.startleUntil, ooh = t < S.oohUntil;
    // Previews in the picker keep their own calm mood, apart from being picked.
    const baseMood = r.preview ? "idle" : S.mood;
    const mood = happy ? "happy" : error && !r.preview ? "error" : baseMood;
    if (r.el.dataset.mood !== mood) r.el.dataset.mood = mood;
    const talking = S.talk > 0.04 && !happy && !error && !r.preview;
    const ts = t / 1000;

    maybeFidget(r, t, mood, drowsy && !r.preview);
    const f = r.fidget;
    const fp = f ? clamp((t - f.start) / f.dur, 0, 1) : 0;
    const fid = f ? f.name : "";
    if (r.el.dataset.fidget !== fid) r.el.dataset.fidget = fid;

    // ---- where to look
    let lx = 0, ly = 0;
    const box = r.el.getBoundingClientRect();
    const cx = box.left + box.width / 2, cy = box.top + box.height / 2;
    const pt = attentionPoint(r, t);
    let toward = 0;
    const own = r.lookUntil > t ? r.lookPoint : null;
    const pdist = S.pointer ? Math.hypot(S.pointer.x - cx, S.pointer.y - cy) : 1e9;
    // Shy: you've parked the cursor right next to it and gone still.
    const shy = r.hero && mood === "idle" && pdist < box.width * 0.9 && t - S.pointerAt > 1800 && t - S.pointerAt < 20000;
    if (own && !dizzy) {
      const dx = own.x - cx, dy = own.y - cy, dist = Math.hypot(dx, dy) || 1;
      const reach = Math.min(1, dist / 120);
      lx = (dx / dist) * 2.7 * reach; ly = (dy / dist) * 2.3 * reach;
      toward = clamp(dx / 400, -1, 1);
    } else if (mood === "searching") {
      lx = Math.sin(ts * 5.2) * 2.6; ly = -0.4;                      // reading across a page
    } else if (mood === "thinking" || mood === "working") {
      if (Math.random() < dt * 1.2) S.wander = { x: Math.random() * 2 - 1, y: -0.3 - Math.random() * 0.7 };
      lx = S.wander.x * 2.4; ly = S.wander.y * 2;
    } else if (mood === "error") {
      lx = 0; ly = 1.8;
    } else if (shy) {
      lx = -1.6; ly = 1.6;
    } else if (pt && !r.preview) {
      const q = pt;
      const dx = q.x - cx, dy = q.y - cy, dist = Math.hypot(dx, dy) || 1;
      const reach = Math.min(1, dist / 160);
      lx = (dx / dist) * 2.6 * reach; ly = (dy / dist) * 2.2 * reach;
      toward = clamp(dx / 400, -1, 1);
    } else if (mood === "idle") {
      if (Math.random() < dt * 0.35) r.wander = { x: Math.random() * 2 - 1, y: Math.random() * 1.2 - 0.6 };
      const w = r.wander || S.wander;
      lx = w.x * 1.8; ly = w.y * 1.5;
    }
    if (fid === "look" || fid === "headturn") { lx = fp < 0.42 ? -2.9 : fp < 0.55 ? 0 : 2.9; ly = 0.2; }
    if (fid === "scan") { lx = Math.sin(fp * Math.PI * 4) * 2.8; ly = 0; }
    if (dizzy) { lx = Math.cos(ts * 9) * 1.4; ly = Math.sin(ts * 9) * 1.4; }
    r.lx.t = lx; r.ly.t = ly;

    // ---- body
    const breath = mood === "sleepy" ? 0.035 * Math.sin(ts * 1.4) : 0.012 * Math.sin(ts * 2.1);
    let rot = toward * 6, y = 0, x = toward * 1.2, sx = 1 - breath * 0.6, sy = 1 + breath;
    if (r.c.float) y = Math.sin(ts * 1.8) * 18;                       // ghosts don't touch the ground
    if (mood === "waiting") rot = 9;
    if (t < S.curiousUntil) rot = -10;
    if (mood === "listening") { y -= 10; sy += 0.03; }
    if (mood === "thinking" || mood === "working") rot = Math.sin(ts * 1.3) * 4;
    if (dizzy) rot = Math.sin(ts * 7) * 10;
    if (mood === "error") x = Math.sin(ts * 40) * (t < S.errorUntil - 3200 ? 0.6 : 0);
    if (mood === "sleepy") rot = -6;
    if (shy) rot = -5;
    // fidgets
    if (fid === "hop") {
      if (fp < 0.2) { sy = 0.86; sx = 1.1; }
      else if (!f.kicked) { f.kicked = true; r.y.kick(-95); r.sy.kick(5); r.sx.kick(-3); }
    }
    if (fid === "stretch") {
      if (fp < 0.55) { sy = 1.13; sx = 0.9; }
      else rot = Math.sin(ts * 30) * 3 * (1 - fp);
    }
    if (fid === "hum") rot = Math.sin(ts * 4.2) * 6;
    if (fid === "wiggle") rot = Math.sin(fp * Math.PI * 6) * 9 * (1 - fp);
    if (fid === "headturn") { sx = fp > 0.42 && fp < 0.55 ? 0.82 : 1; rot = fp < 0.42 ? -4 : fp > 0.55 ? 4 : 0; }
    if (fid === "bloom" && !f.kicked && fp > 0.1) { f.kicked = true; r.y.kick(-70); }
    r.rot.t = rot + (r.lean || 0); r.y.t = y; r.x.t = x;
    r.sy.t = sy; r.sx.t = sx;

    // ---- eyelids and pupils
    let lid = 1;
    if (mood === "working") lid = 0.72;
    if (mood === "error") lid = 0.8;
    if (drowsy && !r.preview) lid = clamp(1 - (Date.now() - S.lastActivity - 75000) / 45000, 0.35, 1);
    if (startle || mood === "listening" || ooh) lid = 1.15;
    if (fid === "headturn" || fid === "scan") lid = 1.12;
    r.lid.t = lid;
    let pupil = 1;
    if (pt && mood === "idle" && !r.preview) {
      const d = Math.hypot(pt.x - cx, pt.y - cy);
      pupil = d < 90 ? 1.18 : 1;                                       // a little delighted when you're close
    }
    if (mood === "listening" || ooh) pupil = 1.25;
    r.pupil.t = pupil;
    r.wink.t = fid === "wink" && fp > 0.15 && fp < 0.75 ? 0.06 : 1;

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
    const eyesShut = (mood === "sleepy" && !r.preview) || yawn || (fid === "stretch" && fp < 0.6) || (fid === "hum" && fp > 0.2);
    const eyesJoy = happy || r.petting > 0.6 || (fid === "bloom" && fp > 0.25 && fp < 0.85);
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
    if (mood === "sleepy" && !r.preview) { mw = 1.1; mc = 0.2; mo = 0.6 + Math.sin(ts * 1.4) * 0.4; }
    if (shy) { mw = 1.6; mc = 1.5; mo = 0; }
    if (fid === "hum") { mw = 1.2; mc = 0.4; mo = 0.6 + Math.sin(ts * 7) * 0.3; }
    if (fid === "stretch" && fp < 0.6) { mw = 1.6; mc = 0.3; mo = 2.4; }
    if (fid === "wink") { mw = 3; mc = 3; mo = 0.3; }
    if (fid === "vanish" && fp > 0.6 && fp < 0.85) { mw = 1.8; mc = 0.4; mo = 2.6; }   // "boo!"
    if (talking) {
      mw = 2.3; mc = 1.4;
      mo = clamp(S.talk, 0, 1) * (1.1 + Math.sin(ts * 21) * 0.9 + Math.sin(ts * 13) * 0.5);
    }
    if (yawn) { mw = 1.9; mc = 0.3; mo = 3.2 * Math.sin(Math.PI * clamp(1 - (S.yawnUntil - t) / 1500, 0, 1)); }
    if (happy || r.petting > 0.6) { mw = 3.3; mc = 3.1; mo = 1.6; }
    r.mw.t = mw; r.mc.t = mc; r.mo.t = Math.max(0, mo);

    const cheekBase = r.c.cheekOpacity ?? 0.5;
    r.cheek.t = happy || r.petting > 0.3 || shy || fid === "wink" ? 0.95 : mood === "error" ? 0.15 : cheekBase;
    r.petting = Math.max(0, (r.petting || 0) - dt * 0.5);

    // ---- something to hold: a magnifier while searching, a wrench while working, a quill while talking
    let prop = "";
    if (!r.preview) {
      if (mood === "searching") prop = "mag";
      else if (mood === "working") prop = "wrench";
      else if (talking) prop = "quill";
      if (r.propUntil > t) prop = r.prop;
    }
    if (r.el.dataset.prop !== prop) r.el.dataset.prop = prop;

    // ---- the character's own parts
    drive(r, { t, ts, dt, mood, happy, talking, fid, fp, shy, petting: r.petting || 0 });

    // ---- particles
    if (fid === "hum" && t > r.notesAt) { r.notesAt = t + 480; glyph(r.el, pick(["♪", "♫", "♪"]), "is-note"); }

    // ---- integrate
    for (const s of [r.x, r.y, r.rot, r.sx, r.sy, r.lx, r.ly, r.lid, r.pupil, r.wink, r.mw, r.mc, r.mo, r.cheek, r.dx, r.dy, r.fade]) s.step(dt);
    for (const p of Object.values(r.parts)) p.s.step(dt);

    // ---- paint
    const sxv = r.sx.x, syv = r.sy.x;
    // Squash and stretch from the bottom, so it sits on the ground when it lands.
    r.rig.setAttribute("transform",
      `translate(${r.x.x.toFixed(2)} ${(r.y.x / 10).toFixed(2)}) rotate(${r.rot.x.toFixed(2)} 32 46) ` +
      `translate(32 56) scale(${sxv.toFixed(3)} ${syv.toFixed(3)}) translate(-32 -56)`);
    r.eyes.setAttribute("transform", `translate(${r.lx.x.toFixed(2)} ${r.ly.x.toFixed(2)})`);
    const open = clamp(r.lid.x * blink, 0.05, 1.25);
    for (const [g, ex, w] of [[r.eyeL, 25, 1], [r.eyeR, 39, r.wink.x]]) {
      if (g) g.setAttribute("transform", `translate(${ex} 33) scale(${(0.9 + 0.1 * r.pupil.x).toFixed(3)} ${clamp(open * r.pupil.x * w, 0.05, 1.5).toFixed(3)}) translate(${-ex} -33)`);
    }
    r.mouth.setAttribute("d", mouthPath(r.mw.x, r.mc.x, r.mo.x));
    r.cheeks.forEach((c) => c.setAttribute("opacity", clamp(r.cheek.x, 0, 1).toFixed(2)));
    if (r.sweat) r.sweat.setAttribute("opacity", mood === "error" ? "0.9" : "0");
    for (const p of Object.values(r.parts)) {
      const v = p.s.x;
      const tr = p.mode === "scale" ? `translate(${p.px} ${p.py}) scale(${clamp(v, 0, 1.4).toFixed(3)}) translate(${-p.px} ${-p.py})`
        : p.mode === "y" ? `translate(0 ${v.toFixed(2)})` : `rotate(${v.toFixed(2)} ${p.px} ${p.py})`;
      p.el.setAttribute("transform", tr);
    }
    const fade = clamp(r.fade.x, 0, 1), op = fade < 0.995 ? fade.toFixed(2) : "";
    if (r.svg.style.opacity !== op) r.svg.style.opacity = op;
    if (r.hero && (Math.abs(r.dx.x) > 0.1 || Math.abs(r.dy.x) > 0.1 || r.dragging)) {
      r.el.style.translate = `${r.dx.x.toFixed(1)}px ${r.dy.x.toFixed(1)}px`;
    } else if (r.el.style.translate) {
      r.el.style.translate = "";
    }
  }

  // Each character's moving parts, aimed by mood and habit.
  function drive(r, k) {
    const P = r.parts, set = (n, v) => { if (P[n]) P[n].s.t = v; };
    const kick = (n, v) => { if (P[n]) P[n].s.kick(v); };
    const { ts, mood, happy, fid, fp } = k;
    const sleepy = mood === "sleepy" && !r.preview;
    switch (r.cid) {
      case "moon": {
        if (!r.phase) break;
        if (fid === "eclipse") {
          // A shadow sweeps across, new moon to new moon.
          r.phase.setAttribute("d", phasePath(0.02 + fp * 0.96));
          r.phase.setAttribute("opacity", (0.3 * Math.sin(Math.PI * fp)).toFixed(3));
          r.eclipsing = true;
        } else if (r.eclipsing) {
          r.eclipsing = false;
          if (r.restPhase) { r.phase.setAttribute("d", r.restPhase); r.phase.setAttribute("opacity", "0.14"); }
          else r.phase.setAttribute("opacity", "0");
        }
        break;
      }
      case "cat": {
        let tail = Math.sin(ts * 1.6) * 10, earL = 0, earR = 0;
        if (happy || k.petting > 0.5) tail = Math.sin(ts * 9) * 16;
        if (mood === "thinking" || mood === "searching" || mood === "working") tail = Math.sin(ts * 3) * 14;
        if (sleepy) tail = 22;
        if (mood === "listening") { earL = 8; earR = -8; }
        if (mood === "error") { earL = -28; earR = 28; tail = 30; }
        if (sleepy) { earL = -12; earR = 12; }
        if (fid === "twitch") {
          tail = Math.sin(fp * Math.PI * 5) * 24;
          if (!r.twitched || r.twitched < fp - 0.3) { r.twitched = fp; kick(fp < 0.5 ? "earL" : "earR", fp < 0.5 ? -260 : 260); }
        } else r.twitched = 0;
        if (Math.random() < k.dt * 0.25 && mood === "idle") kick(Math.random() < 0.5 ? "earL" : "earR", (Math.random() - 0.5) * 300);
        if (r.traveling) { tail = -35; earL = -18; earR = 18; }       // streamlined mid-pounce
        set("tail", tail); set("earL", earL); set("earR", earR);
        break;
      }
      case "ghost": {
        if (r.hem) r.hem.setAttribute("d", ghostPath(ts * (happy ? 7 : mood === "idle" ? 3 : 5)));
        let armL = Math.sin(ts * 2.2) * 10, armR = -Math.sin(ts * 2.2 + 0.6) * 10;
        if (happy || k.petting > 0.5) { armL = -40 + Math.sin(ts * 12) * 15; armR = 40 - Math.sin(ts * 12) * 15; }
        if (fid === "vanish" && fp > 0.6 && fp < 0.85) { armL = -55; armR = 55; }
        if (mood === "error") { armL = 25; armR = -25; }
        if (r.traveling) { armL = 50; armR = -50; }
        set("armL", armL); set("armR", armR);
        r.fade.t = fid === "vanish" && fp > 0.1 && fp < 0.6 ? 0.08 : sleepy ? 0.7 : 1;
        if (fid === "vanish" && fp > 0.6 && !r.booed) {
          r.booed = true;
          r.sy.kick(5); r.sx.kick(5);
          if (r.hero) say("Boo!", 1200); else if (!r.preview) glyph(r.el, "!", "is-bang");
          if (r.preview) glyph(r.el, "boo", "is-boo");
        }
        if (fid !== "vanish") r.booed = false;
        break;
      }
      case "robot": {
        let ant = Math.sin(ts * 2) * 4;
        if (mood === "thinking" || mood === "searching" || mood === "working") ant = Math.sin(ts * 9) * 9;
        if (happy) ant = Math.sin(ts * 14) * 14;
        if (sleepy) ant = 25;
        if (r.traveling) ant = -30 * Math.sign(r.lean || 1);
        set("antenna", ant);
        if (r.scan) {
          const scanning = fid === "scan" || mood === "searching";
          const sp = fid === "scan" ? fp : (ts % 1.4) / 1.4;
          r.scan.setAttribute("opacity", scanning ? (0.7 * Math.sin(Math.PI * sp)).toFixed(2) : "0");
          r.scan.setAttribute("y", (24 + sp * 21).toFixed(2));
        }
        if (fid === "scan" && !r.beeped && fp > 0.5) { r.beeped = true; if (r.hero && Math.random() < 0.5) say("Beep boop. All systems cosy.", 1800); }
        if (fid !== "scan") r.beeped = false;
        break;
      }
      case "owl": {
        let wL = 0, wR = 0, tuft = 0;
        if (happy || k.petting > 0.5) { wL = -30 + Math.sin(ts * 16) * 22; wR = 30 - Math.sin(ts * 16) * 22; }
        if (mood === "listening" || fid === "headturn") tuft = 10;
        if (mood === "error" || sleepy) tuft = -14;
        if (mood === "thinking") { wL = 8; wR = -8; }                // wings folded, hmm
        if (r.traveling) { wL = -45 + Math.sin(ts * 22) * 35; wR = 45 - Math.sin(ts * 22) * 35; }
        set("wingL", wL); set("wingR", wR);
        set("tuftL", -tuft); set("tuftR", tuft);
        if (fid === "headturn" && !r.hooted && fp > 0.6) { r.hooted = true; if (r.hero && Math.random() < 0.4) say("Hoo.", 1200); }
        if (fid !== "headturn") r.hooted = false;
        break;
      }
      case "sprout": {
        let sway = Math.sin(ts * 1.8) * 7, bloom = 0;
        if (happy || k.petting > 0.5) { sway = Math.sin(ts * 10) * 14; bloom = 1; }
        if (fid === "bloom") { bloom = fp > 0.12 && fp < 0.9 ? 1 : 0; sway = Math.sin(ts * 6) * 10; }
        if (sleepy) sway = -18;
        if (mood === "error") sway = 20;
        if (mood === "thinking" || mood === "working") sway = Math.sin(ts * 3) * 9;
        if (r.traveling) sway = Math.sin(ts * 16) * 16;
        set("leaves", sway); set("flower", bloom);
        break;
      }
    }
  }

  // ---------------------------------------------------------------- speech bubble & particles
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

  // A puff of cloud when a look changes.
  function poof(el) {
    if (reduceMotion) return;
    const b = el.getBoundingClientRect();
    if (!b.width) return;
    const n = 8;
    for (let i = 0; i < n; i++) {
      const s = document.createElement("span");
      s.className = "xm-puff";
      const a = (i / n) * Math.PI * 2;
      const size = b.width * (0.22 + Math.random() * 0.14);
      s.style.width = s.style.height = `${size}px`;
      s.style.left = `${b.left + b.width / 2 - size / 2}px`;
      s.style.top = `${b.top + b.height / 2 - size / 2}px`;
      s.style.setProperty("--dx", `${Math.cos(a) * b.width * 0.45}px`);
      s.style.setProperty("--dy", `${Math.sin(a) * b.height * 0.4}px`);
      document.body.appendChild(s);
      setTimeout(() => s.remove(), 700);
    }
  }

  // Little floating marks: ♥ for petting, ♪ for humming, ? when you ask, ! when startled.
  function glyph(el, text, variant = "") {
    if (reduceMotion || !el) return;
    const b = el.getBoundingClientRect();
    if (!b.width || b.bottom < 0 || b.top > innerHeight) return;
    const g = document.createElement("span");
    g.className = `xm-heart ${variant}`;
    g.textContent = text;
    g.style.fontSize = `${clamp(b.width * 0.17, 9, 18)}px`;
    g.style.left = `${b.left + b.width * (0.6 + Math.random() * 0.3)}px`;
    g.style.top = `${b.top + b.height * 0.08}px`;
    document.body.appendChild(g);
    setTimeout(() => g.remove(), 1400);
  }
  const heart = (el) => glyph(el, "♥");

  // ---------------------------------------------------------------- touching it
  let helloIndex = 0;
  let boops = [];

  function onMascotClick(m) {
    const r = rigFor(m);
    if (r.justDragged) { r.justDragged = false; return; }
    if (m.closest("[data-pick-character]")) return;              // the picker handles its own
    noteActivity();
    const t = now();
    r.fidget = null;
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
    if (r.hero) {
      const lines = [...r.c.hellos, ...GENERIC_HELLOS];
      say(lines[helloIndex % lines.length]);
      helloIndex++;
    }
  }

  function flip(m) {
    if (reduceMotion) return;
    m.classList.remove("is-flipping", "is-spinning");
    void m.offsetWidth;
    m.classList.add("is-flipping");
    setTimeout(() => m.classList.remove("is-flipping"), 900);
    setTimeout(() => sparkleBurst(m, 10), 420);
    S.happyUntil = now() + 1400;
  }

  // Grab the big one and let go: it springs back. Moving the cursor back and forth over it is petting.
  function wireTouch() {
    let drag = null;
    document.addEventListener("pointerdown", (e) => {
      const m = e.target.closest(".xod-mascot--hero");
      if (!m || reduceMotion || e.button !== 0) return;
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
        if (!drag.moved && Math.hypot(ddx, ddy) > 5) { drag.moved = true; drag.r.dragging = true; drag.r.fidget = null; }
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
      // petting: quick back-and-forth across a live companion
      const m = e.target.closest?.(".xod-mascot");
      if (m && Math.abs(moveX) > 2) {
        const r = rigFor(m);
        const dir = Math.sign(moveX);
        if (r.lastDir && dir !== r.lastDir) {
          // A real back-and-forth: several quick reversals in a row.
          const t = now();
          r.flips = (r.flips || []).filter((f) => t - f < 1200);
          r.flips.push(t);
          if (r.flips.length >= 4) { r.petting = Math.min(1.4, (r.petting || 0) + 0.25); r.fidget = null; }
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
    document.addEventListener("dblclick", (e) => {
      const m = e.target.closest(".xod-mascot");
      if (m && !m.closest("[data-pick-character]")) flip(m);
    });
    document.addEventListener("contextmenu", (e) => {
      const m = e.target.closest(".xod-mascot--hero, .xod-mascot--small");
      if (!m) return;
      e.preventDefault();
      openPicker();
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
    if (S.forced && now() < S.forced.until) { setMood(S.forced.mood); return; }
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
      const small = document.querySelector(".xod-mascot--small");
      sparkleBurst(small);
      // Now and then a finished job earns a little flip.
      if (small && Math.random() < 0.25) flip(small);
    }
    wasBusy = busy;
  }

  // ---------------------------------------------------------------- the rest of the world
  function wireWorld() {
    const input = document.getElementById("chatInput");
    let askedAt = 0;
    if (input) {
      input.addEventListener("input", () => {
        S.typingAt = now();
        noteActivity();
        for (const el of liveMascots()) { const r = rigFor(el); r.fidget = null; r.y.kick(6); }   // a small nod per keystroke
        if (/\?\s*$/.test(input.value)) {
          S.curiousUntil = now() + 1600;
          if (now() - askedAt > 4000) { askedAt = now(); glyph(document.querySelector(".xod-mascot--hero"), "?", "is-ask"); }
        }
        if ((input.value || "").length === 280) S.oohUntil = now() + 900;   // that's a long one
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
    loadCharacter();
    try {
      if (!localStorage.getItem("xod.saidHello")) {
        setTimeout(() => say("Hi! I'm Xod. Pick a card, or ask me anything.", 5000), 1100);
        localStorage.setItem("xod.saidHello", "1");
      }
    } catch (_) {}
    setInterval(watchAgent, 250);
    requestAnimationFrame(frame);
  }

  // For powers.js: let a companion look somewhere, lean, hold something, or get a nudge.
  function puppet(el) {
    if (!el || !el.querySelector("svg")) return null;
    const r = rigFor(el);
    return {
      el, info: { id: r.cid, ...r.c },
      lookAt(x, y, ms = 1200) { r.lookPoint = { x, y }; r.lookUntil = now() + ms; },
      lean(deg) { r.lean = deg; },
      hold(prop, ms = 1200) { r.prop = prop; r.propUntil = now() + ms; },
      traveling(on) { r.traveling = !!on; },
      kick({ x = 0, y = 0, rot = 0, sx = 0, sy = 0 } = {}) { r.x.kick(x); r.y.kick(y); r.rot.kick(rot); r.sx.kick(sx); r.sy.kick(sy); },
      fidget(name) { const dur = SPECIAL_MS[name] || (FIDGETS.find((f) => f[0] === name) || [, 1200])[1]; r.fidget = { name, start: now(), until: now() + dur, dur }; },
    };
  }

  window.openCompanionPicker = openPicker;
  window.XodMascot = {
    say, sparkleBurst, beHappy, flip, lunarPhase, phaseName, mood: () => S.mood,
    characters: () => ORDER.map((id) => ({ id, name: CHARACTERS[id].name, blurb: CHARACTERS[id].blurb })),
    character: () => character, setCharacter, puppet, glyph,
    info: () => ({ id: character, ...CHARACTERS[character] }),
    lookAt: (x, y, ms = 1200) => { S.lookAtPoint = { x, y }; S.lookAtUntil = now() + ms; },
    // For recordings and checks: hold a mood (idle, thinking, searching, working, waiting, listening, sleepy) or flash one (happy, error, dizzy).
    demo: (mood, ms = 4000) => {
      const t = now();
      if (mood === "happy") S.happyUntil = t + ms;
      else if (mood === "error") S.errorUntil = t + ms;
      else if (mood === "dizzy") S.dizzyUntil = t + ms;
      else { S.forced = { mood, until: t + ms }; setMood(mood); }
    },
    // Play a habit now on every live companion; "special" plays each one's own trick.
    fidget: (name) => {
      for (const el of liveMascots()) {
        const r = rigFor(el), n = name === "special" ? r.c.special : name;
        const dur = SPECIAL_MS[n] || (FIDGETS.find((f) => f[0] === n) || [, 1500])[1];
        r.fidget = { name: n, start: now(), until: now() + dur, dur };
      }
    },
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
