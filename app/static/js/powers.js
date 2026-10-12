// ==============================================================
// XOD - powers
// While Xod works, its companion leaves its perch next to the reply
// and does the work where you can see it: it conjures each tool as it
// starts and taps it when it's done, peeks over browser snapshots,
// writes the answer with a quill that follows the text, lassoes
// anything that needs your OK, and signs the answer before flying
// home. Outside the chat it opens panels with a little spell and,
// when nothing's happening, nudges a card on the home screen.
// Each look travels its own way: the moon flies, the cat pounces,
// the ghost phases, the robot zips, the owl flaps, the sprout hops.
// Decorative only: it never blocks a click, and if anything here
// fails the app keeps working.
// ==============================================================
(() => {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion || !window.XodMascot) return;
  const M = window.XodMascot;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const now = () => performance.now();
  const SIZE = 44;

  // ---------------------------------------------------------------- colour and particles
  let info = M.info();
  function applyLook() {
    info = M.info();
    document.documentElement.style.setProperty("--xp-glow", info.glow);
  }
  applyLook();
  window.addEventListener("xod:character", () => setTimeout(applyLook, 0));

  const SHAPES = {
    star: '<path d="M5 0 L6.2 3.8 L10 5 L6.2 6.2 L5 10 L3.8 6.2 L0 5 L3.8 3.8Z"/>',
    paw: '<circle cx="5" cy="6.5" r="2.3"/><circle cx="2.2" cy="3.8" r="1.1"/><circle cx="4.1" cy="2.2" r="1.1"/><circle cx="6.1" cy="2.3" r="1.1"/><circle cx="7.9" cy="3.9" r="1.1"/>',
    wisp: '<circle cx="5" cy="5" r="4"/>',
    pixel: '<rect x="2" y="2" width="6" height="6" rx="1"/>',
    feather: '<path d="M5 0.5 Q8.5 4 5 9.5 Q1.5 4 5 0.5Z"/><path d="M5 2 L5 9.5" stroke="rgba(0,0,0,0.25)" stroke-width="0.6"/>',
    leaf: '<path d="M1 9 Q1 2 9 1 Q8.5 8.5 1 9Z"/>',
  };
  function particle(x, y, { kind = info.trail, dx = 0, dy = 0, rot = 0, size = 9, life = 900, still = false } = {}) {
    const p = document.createElement("span");
    p.className = `xp-p xp-p-${kind}${still ? " is-still" : ""}`;
    p.innerHTML = `<svg viewBox="0 0 10 10" width="${size}" height="${size}">${SHAPES[kind] || SHAPES.star}</svg>`;
    p.style.left = `${x - size / 2}px`;
    p.style.top = `${y - size / 2}px`;
    p.style.setProperty("--dx", `${dx}px`);
    p.style.setProperty("--dy", `${dy}px`);
    p.style.setProperty("--r", `${rot}deg`);
    p.style.animationDuration = `${life}ms`;
    document.body.appendChild(p);
    setTimeout(() => p.remove(), life + 50);
  }
  function burst(x, y, n = 8, spread = 26) {
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2 + Math.random() * 0.6;
      const d = spread * (0.6 + Math.random() * 0.6);
      particle(x, y, { dx: Math.cos(a) * d, dy: Math.sin(a) * d, rot: Math.random() * 240 - 120, size: 7 + Math.random() * 4, life: 700 });
    }
  }
  const centerOf = (el) => { const b = el.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; };

  // ---------------------------------------------------------------- the familiar
  // A second companion that can leave its perch. It's an ordinary .xod-mascot, so mascot.js
  // animates its face and body; this file only moves it around and tells it what to hold.
  const wrap = document.createElement("div");
  wrap.className = "xod-familiar";
  wrap.setAttribute("aria-hidden", "true");
  wrap.innerHTML = `<span class="xod-mascot xod-mascot--familiar" data-mood="idle" data-live="0"></span>`;
  const famEl = wrap.firstElementChild;
  const F = {
    out: false, x: 0, y: 0, vx: 0, vy: 0,
    target: null,        // { el, anchor(rect) -> {x,y}, look(rect) -> {x,y}, prop, onArrive }
    trip: null,          // { from, start, dur, style }
    perch: null,         // the chat avatar it came out of
    lastEventAt: 0, trailAt: 0, inkAt: 0, lastCaret: null,
  };
  const puppet = () => M.puppet(famEl);

  function mountFamiliar() {
    if (!wrap.isConnected) document.body.appendChild(wrap);
  }

  function chatBox() { return document.getElementById("chatMessages"); }
  function liveAvatar() {
    const all = document.querySelectorAll("#chatMessages .xod-mascot--chat");
    return all.length ? all[all.length - 1] : null;
  }
  function currentReply() {
    const av = liveAvatar();
    return av ? av.parentElement : null;
  }

  // Keep it inside the chat's visible area; if the target has scrolled away, it waits at the edge.
  function clampToChat(p) {
    const box = chatBox();
    if (!box) return p;
    const b = box.getBoundingClientRect();
    return { x: clamp(p.x, b.left + SIZE * 0.6, b.right - SIZE * 0.6), y: clamp(p.y, b.top + SIZE * 0.7, b.bottom - SIZE * 0.7) };
  }

  function goalOf(target) {
    if (!target || !target.el || !target.el.isConnected) return null;
    const r = target.el.getBoundingClientRect();
    const raw = target.anchor ? target.anchor(r, target.el) : { x: r.left + r.width / 2, y: r.top };
    return raw ? clampToChat(raw) : null;
  }

  function leavePerch() {
    mountFamiliar();
    const perch = liveAvatar();
    if (!perch || !perch.getBoundingClientRect().width) return false;
    F.perch = perch;
    perch.classList.add("is-away");
    const c = centerOf(perch);
    F.x = c.x; F.y = c.y; F.vx = 0; F.vy = 0;
    F.out = true;
    famEl.dataset.live = "1";
    wrap.classList.add("is-out");
    const p = puppet();
    if (p) p.kick({ y: -60, sy: 4 });
    burst(c.x, c.y, 6, 18);
    return true;
  }

  function goTo(target, { quick = false } = {}) {
    F.lastEventAt = now();
    // In a spoken conversation the companion in the conversation dock does the reacting.
    if (window.XodConversation && XodConversation.active() && !F.out) return;
    if (!F.out && !leavePerch()) return;
    const same = F.target && F.target.el === target.el && !F.trip;
    F.target = target;
    if (same) return;
    const goal = goalOf(target) || { x: F.x, y: F.y };
    const dist = Math.hypot(goal.x - F.x, goal.y - F.y);
    if (dist < 6) { arrive(); return; }
    const style = info.travel || "fly";
    const base = { fly: 1, pounce: 0.9, phase: 1.1, zip: 0.6, flap: 1.15, hop: 1.2 }[style] || 1;
    const dur = clamp((quick ? 220 : 320) + dist * 0.75, 320, 1100) * base;
    F.trip = { from: { x: F.x, y: F.y }, start: now(), dur, style, dist };
    const p = puppet();
    if (p) {
      p.traveling(true);
      if (style === "pounce") p.kick({ sy: -6, sx: 5 });                      // crouch before the jump
      if (style === "zip") p.kick({ rot: -Math.sign(goal.x - F.x) * 200 });
    }
  }

  function arrive() {
    const t = F.target;
    const p = puppet();
    if (p) {
      p.traveling(false);
      p.lean(0);
      const s = F.trip ? F.trip.style : "fly";
      if (s === "pounce" || s === "hop") p.kick({ sy: -5, sx: 5, y: 20 });     // land with a squash
      else p.kick({ sy: -2.5, sx: 2.5 });
    }
    if (F.trip && F.trip.style === "phase") burst(F.x, F.y, 6, 16);
    F.trip = null;
    if (t && t.onArrive) { try { t.onArrive(); } catch (_) {} t.onArrive = null; }
  }

  function goHome() {
    if (!F.out) return;
    // Something still needs your OK: wait by it instead of going home.
    const a = F.approval;
    if (a && a.btn.isConnected && !a.btn.disabled) { if (!(F.target && F.target.el === a.btn)) onApproval(); return; }
    F.approval = null;
    const perch = F.perch && F.perch.isConnected ? F.perch : liveAvatar();
    if (!perch) { hide(); return; }
    goTo({
      el: perch, home: true,
      anchor: (r) => ({ x: r.left + r.width / 2, y: r.top + r.height / 2 }),
      onArrive: () => {
        const c = centerOf(perch);
        hide();
        const pp = M.puppet(perch);
        if (pp) { pp.kick({ y: -70, sy: -4, sx: 4 }); }
        burst(c.x, c.y, 7, 20);
      },
    });
  }

  function hide() {
    F.out = false; F.target = null; F.trip = null; F.signing = false;
    famEl.dataset.live = "0";
    wrap.classList.remove("is-out");
    wrap.style.opacity = "";
    document.querySelectorAll(".xod-mascot--chat.is-away").forEach((a) => a.classList.remove("is-away"));
    const p = puppet();
    if (p) { p.traveling(false); p.lean(0); }
  }

  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const easeBack = (t) => { const c1 = 1.5, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); };

  let last = now();
  function tick() {
    const t = now();
    const dt = Math.min(0.05, (t - last) / 1000);
    last = t;
    try { step(t, dt); } catch (e) { if (!F.warned) { F.warned = true; console.warn("Xod powers:", e); } }
    requestAnimationFrame(tick);
  }

  function step(t, dt) {
    if (!F.out) return;
    if (F.perch && !F.perch.isConnected) { hide(); return; }      // chat cleared or switched
    // Nothing new for a while and the reply is finished: go home.
    let streaming = false;
    try { streaming = typeof isStreaming !== "undefined" && !!isStreaming; } catch (_) {}
    if (!streaming && !F.signing && t - F.lastEventAt > 2500 && !(F.target && F.target.home) && !(F.target && F.target.stay)) goHome();
    if (t - F.lastEventAt > 45000) goHome();

    const goal = goalOf(F.target);
    if (!goal) {
      // What it was working on is gone (re-rendered or removed): head home, or vanish if home is gone too.
      if (F.target && F.target.home) { hide(); return; }
      goHome();
      if (!F.out) return;
    }
    const g = goalOf(F.target) || { x: F.x, y: F.y };
    const p = puppet();

    if (F.trip) {
      const tr = F.trip;
      const k = clamp((t - tr.start) / tr.dur, 0, 1);
      let e = ease(k), ox = 0, oy = 0, op = 1;
      const h = clamp(tr.dist * 0.35, 30, 140);
      switch (tr.style) {
        case "pounce": oy = -Math.sin(Math.PI * k) * h * 1.2; e = k < 0.12 ? 0 : ease((k - 0.12) / 0.88); break;
        case "zip": e = easeBack(k); oy = -Math.sin(Math.PI * k) * 8; break;
        case "flap": oy = -Math.sin(Math.PI * k) * h * 0.6 + Math.sin(k * Math.PI * 8) * 5; break;
        case "hop": { const hops = clamp(Math.round(tr.dist / 90), 2, 5); oy = -Math.abs(Math.sin(k * Math.PI * hops)) * clamp(tr.dist / hops * 0.6, 18, 60); e = k; break; }
        case "phase": e = k < 0.5 ? 0 : 1; op = Math.abs(1 - 2 * k) < 0.15 ? 0 : Math.abs(1 - 2 * k); break;
        default: oy = -Math.sin(Math.PI * k) * h * 0.5;
      }
      const nx = lerp(tr.from.x, g.x, e) + ox, ny = lerp(tr.from.y, g.y, e) + oy;
      F.vx = (nx - F.x) / Math.max(dt, 0.001); F.vy = (ny - F.y) / Math.max(dt, 0.001);
      F.x = nx; F.y = ny;
      wrap.style.opacity = op < 1 ? op.toFixed(2) : "";
      if (p) {
        p.lean(clamp(F.vx / 40, -22, 22));
        p.lookAt(g.x + (g.x - tr.from.x), g.y, 300);
      }
      // trail
      if (t - F.trailAt > (tr.style === "pounce" ? 140 : 70) && tr.style !== "phase") {
        F.trailAt = t;
        const still = tr.style === "pounce" || tr.style === "hop";
        if (!still || (tr.style === "pounce" && (k < 0.15 || k > 0.85)) || (tr.style === "hop" && oy > -6)) {
          particle(F.x - Math.sign(F.vx || 1) * 10, F.y + 14 + (still ? 6 : 0), {
            dx: still ? 0 : -F.vx * 0.04 + (Math.random() - 0.5) * 10, dy: still ? 0 : 10 + Math.random() * 10,
            rot: still ? (F.vx > 0 ? 90 : -90) : Math.random() * 180, size: still ? 9 : 6 + Math.random() * 4, life: still ? 1300 : 800, still,
          });
        }
      }
      if (tr.style === "phase" && !tr.puffed && k > 0.35) { tr.puffed = true; burst(tr.from.x, tr.from.y, 6, 16); }
      if (k >= 1) arrive();
    } else {
      // Follow the target (it moves as the chat scrolls and grows) on a soft spring.
      const kk = (F.target && F.target.k) || 90, dd = 2 * Math.sqrt(kk);
      F.vx += (kk * (g.x - F.x) - dd * F.vx) * dt;
      F.vy += (kk * (g.y - F.y) - dd * F.vy) * dt;
      F.x += F.vx * dt; F.y += F.vy * dt;
      if (p) {
        p.lean(clamp(F.vx / 60, -10, 10));
        const look = F.target && F.target.look ? F.target.look(F.target.el.getBoundingClientRect(), F.target.el) : null;
        if (look) p.lookAt(look.x, look.y, 400);
        if (F.target && F.target.prop) p.hold(F.target.prop, 400);
      }
      if (F.target && F.target.whileThere) F.target.whileThere(t);
    }
    // Gentle hover so it never looks parked.
    const bob = F.trip ? 0 : Math.sin(t / 420) * 2.5;
    wrap.style.transform = `translate(${(F.x - SIZE / 2).toFixed(1)}px, ${(F.y - SIZE / 2 + bob).toFixed(1)}px)`;
  }

  // ---------------------------------------------------------------- where the answer is being written
  function caretOf(prose) {
    const walker = document.createTreeWalker(prose, NodeFilter.SHOW_TEXT, { acceptNode: (n) => (n.textContent.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP) });
    let lastText = null;
    while (walker.nextNode()) lastText = walker.currentNode;
    if (!lastText) return null;
    const range = document.createRange();
    const len = lastText.textContent.replace(/\s+$/, "").length;
    range.setStart(lastText, Math.max(0, len - 1));
    range.setEnd(lastText, Math.max(0, len));
    const rects = range.getClientRects();
    const r = rects[rects.length - 1];
    return r ? { x: r.right, y: r.bottom } : null;
  }

  function write(reply) {
    const prose = reply && reply.querySelector(".prose-custom");
    if (!prose) return;
    if (F.target && F.target.writing && F.target.el === prose) { F.lastEventAt = now(); return; }
    goTo({
      el: prose, writing: true, prop: "quill", k: 260,
      anchor: (r, el) => {
        const c = caretOf(el) || { x: r.left + 20, y: r.top + 16 };
        F.lastCaret = c;
        return { x: c.x + 20, y: c.y + 18 };      // just below and ahead of the newest words, where nothing's written yet
      },
      look: () => F.lastCaret,
      whileThere: (t) => {
        const c = F.lastCaret;
        if (!c) return;
        if (F.prevCaret && (F.prevCaret.x !== c.x || F.prevCaret.y !== c.y) && t - F.inkAt > 110) {
          F.inkAt = t;
          particle(c.x + 2, c.y - 4, { dx: (Math.random() - 0.5) * 14, dy: -8 - Math.random() * 10, size: 5 + Math.random() * 3, life: 700, rot: Math.random() * 180 });
          const pp = puppet();
          if (pp) pp.kick({ rot: 30, y: 6 });                     // a little flick of the quill
        }
        F.prevCaret = c;
      },
    });
  }

  // ---------------------------------------------------------------- what the agent is doing
  const SEARCHY = /search|browse|browser|web|fetch|navigate|scrape|crawl|read_url|page|news|reddit|hacker/i;
  const propFor = (name) => (SEARCHY.test(name || "") ? "mag" : /file|write|edit|move|trash|command|terminal|computer|code/i.test(name || "") ? "wrench" : "wand");

  function onTool(ev) {
    const reply = currentReply();
    const pill = document.getElementById(`tool-${ev.id}`) || (reply && [...reply.querySelectorAll(".tool-pill")].pop());
    if (!pill) return;
    pill.classList.add("xp-conjure", "xp-charged");
    const sparkle = () => { const c = centerOf(pill); burst(c.x, c.y, 5, 16); };
    goTo({
      el: pill, prop: propFor(ev.name),
      anchor: (r) => ({ x: r.right + 4, y: r.top - 12 }),
      look: (r) => ({ x: r.left + r.width / 2, y: r.top + r.height / 2 }),
      onArrive: sparkle,
    });
  }

  function onToolDone(ev) {
    const pill = document.getElementById(`tool-${ev.id}`);
    if (pill) {
      pill.classList.add("xp-done");
      const c = centerOf(pill);
      if (F.target && F.target.el === pill && !F.trip) {
        const p = puppet();
        if (p) p.kick({ y: 40, sy: -3, sx: 3 });                    // a tap
      }
      burst(c.x, c.y, 7, 22);
    }
    const data = ev.data || {};
    if (data.needs_approval) { setTimeout(onApproval, 30); return; }
    if (data.screenshot_uri) {
      const card = document.getElementById(`ss-preview-${ev.id}`);
      if (card) {
        goTo({
          el: card, prop: "mag",
          anchor: (r) => ({ x: r.left + r.width * 0.72, y: r.top - 10 }),       // perched on the top edge, peeking in
          look: (r) => ({ x: r.left + r.width * 0.5, y: r.top + r.height * 0.6 }),
        });
      }
    }
  }

  function onApproval() {
    const cards = document.querySelectorAll("[data-mcp-approval]");
    const card = cards[cards.length - 1];
    const btn = card && card.querySelector('[data-mcp="approve"]:not(:disabled)');
    if (!card || !btn) return;
    const box = card.firstElementChild || card;
    box.classList.add("xp-lasso");
    F.approval = { btn, box };
    goTo({
      el: btn, stay: true, prop: "wand",
      anchor: (r) => ({ x: r.right + 26, y: r.top - 4 }),
      look: (r) => ({ x: r.left + r.width / 2, y: r.top + r.height / 2 }),
      onArrive: () => { M.glyph(famEl, "!", "is-bang"); },
      whileThere: (t) => {
        if (btn.disabled || !btn.isConnected) {
          box.classList.remove("xp-lasso");
          F.approval = null; F.target.stay = false; F.target.whileThere = null; F.lastEventAt = now();
          const p = puppet();
          if (p) p.kick({ y: -70, sy: -4, sx: 4 });                 // thank you!
          setTimeout(() => { if (!F.approval) goHome(); }, 900);
          return;
        }
        if (t - (F.nudgeAt || 0) > 1600) { F.nudgeAt = t; const p = puppet(); if (p) p.kick({ x: -60, rot: -120 }); }   // "this one!"
      },
    });
  }

  function onError() {
    const reply = currentReply();
    const prose = reply && reply.querySelector(".prose-custom");
    if (!prose) return;
    prose.classList.remove("xp-wobble"); void prose.offsetWidth; prose.classList.add("xp-wobble");
    goTo({ el: prose, anchor: (r) => ({ x: r.left + 30, y: r.top - 8 }), look: (r) => ({ x: r.left + 60, y: r.top + 12 }) });
  }

  // When the answer is finished, it signs it with a flourish and flies home.
  function onDone() {
    const reply = currentReply();
    const prose = reply && reply.querySelector(".prose-custom");
    if (!F.out || !prose || !(prose.textContent || "").trim()) { goHome(); return; }
    const sig = document.createElement("div");
    sig.className = "xp-signature";
    sig.setAttribute("aria-hidden", "true");
    sig.innerHTML = `<svg viewBox="0 0 220 26" width="220" height="26"><path d="M4 18 C 24 2, 34 30, 54 14 S 84 4, 98 16 S 128 26, 146 12 S 182 6, 214 14" /></svg>`;
    prose.insertAdjacentElement("afterend", sig);
    const path = sig.querySelector("path");
    const len = path.getTotalLength();
    path.style.strokeDasharray = `${len}`;
    path.style.strokeDashoffset = `${len}`;
    F.signing = true;
    const dur = 1100;
    const pointAt = (k) => {
      const pt = path.getPointAtLength(len * k);
      const m = path.getScreenCTM();
      return m ? { x: pt.x * m.a + m.e, y: pt.y * m.d + m.f } : null;
    };
    goTo({
      el: sig, prop: "quill",
      anchor: () => { const p0 = pointAt(0); return p0 ? { x: p0.x + 24, y: p0.y - 22 } : null; },
      onArrive: () => {
        const draw = () => {
          const k = clamp((now() - start2) / dur, 0, 1);
          path.style.strokeDashoffset = `${len * (1 - k)}`;
          const p = pointAt(k);
          if (p && F.target && F.target.el === sig) {
            F.target.anchor = () => ({ x: p.x + 24, y: p.y - 22 });
            if (Math.random() < 0.35) particle(p.x, p.y, { dx: (Math.random() - 0.5) * 12, dy: -10, size: 5, life: 600 });
          }
          if (k < 1) requestAnimationFrame(draw);
          else {
            F.signing = false;
            const end = pointAt(1);
            if (end) burst(end.x, end.y, 6, 14);
            setTimeout(() => goHome(), 250);
            setTimeout(() => { sig.classList.add("is-fading"); setTimeout(() => sig.remove(), 900); }, 1800);
          }
        };
        const start2 = now();
        requestAnimationFrame(draw);
      },
    });
  }

  let replyStarted = false;
  window.addEventListener("xod:agent", (e) => {
    const ev = e.detail || {};
    // app.js renders each event right after announcing it; act once the DOM has caught up.
    setTimeout(() => {
      try {
        switch (ev.type) {
          case "tool_call": case "subagent_tool_call": onTool(ev); break;
          case "tool_result": case "subagent_tool_result": onToolDone(ev); break;
          case "content": if ((ev.content || "").trim() || (F.target && F.target.writing)) write(currentReply()); break;
          case "status": if (/approv|confirm|your ok|permission/i.test(ev.content || "")) onApproval(); break;
          case "error": onError(); break;
          case "done": onDone(); break;
        }
        if (!replyStarted && ev.type !== "done") replyStarted = true;
      } catch (err) { if (!F.warned) { F.warned = true; console.warn("Xod powers:", err); } }
    }, 0);
  });

  // ---------------------------------------------------------------- spells around the page
  // A beam from one companion to something it just did.
  function beam(from, to, { dotted = false } = {}) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", `xp-beam${dotted ? " is-dotted" : ""}`);
    const mx = (from.x + to.x) / 2, my = Math.min(from.y, to.y) - clamp(Math.abs(to.x - from.x) * 0.25, 30, 160);
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", `M${from.x} ${from.y} Q${mx} ${my} ${to.x} ${to.y}`);
    svg.appendChild(path);
    document.body.appendChild(svg);
    const len = path.getTotalLength();
    path.style.setProperty("--len", len);
    for (let i = 1; i <= 6; i++) {
      const pt = path.getPointAtLength((len * i) / 7);
      setTimeout(() => particle(pt.x, pt.y, { dx: (Math.random() - 0.5) * 16, dy: (Math.random() - 0.5) * 16, size: 6 + Math.random() * 3, life: 700, rot: Math.random() * 180 }), i * 45);
    }
    setTimeout(() => burst(to.x, to.y, 7, 22), 330);
    setTimeout(() => svg.remove(), 1100);
  }

  function caster() {
    const hero = document.querySelector(".xod-mascot--hero");
    if (hero && hero.offsetParent && hero.getBoundingClientRect().width) return hero;
    return document.querySelector(".xod-mascot--small");
  }

  function cast(toPoint, { dotted = false } = {}) {
    const c = caster();
    if (!c || !toPoint) return;
    const from = centerOf(c);
    const p = M.puppet(c);
    if (p) { p.hold("wand", 1100); p.lookAt(toPoint.x, toPoint.y, 1200); p.kick({ y: -40, rot: toPoint.x > from.x ? 160 : -160 }); }
    setTimeout(() => beam({ x: from.x + 10, y: from.y - 6 }, toPoint, { dotted }), 120);
  }

  // Panels and dialogs open with a little spell from the companion in the header.
  function watchPanels() {
    new MutationObserver((records) => {
      for (const rec of records) {
        const el = rec.target;
        if (!(el instanceof HTMLElement) || !el.id) continue;
        const was = rec.oldValue || "";
        const drawerOpened = el.classList.contains("tasks-panel") && el.classList.contains("is-open") && !/\bis-open\b/.test(was);
        const modalOpened = /Modal$/.test(el.id) && /\bhidden\b/.test(was) && !el.classList.contains("hidden");
        if (!drawerOpened && !modalOpened) continue;
        const panel = drawerOpened ? el.querySelector(".tasks-drawer") : el.firstElementChild;
        if (!panel) continue;
        setTimeout(() => {
          const b = panel.getBoundingClientRect();
          if (!b.width) return;
          const target = drawerOpened ? { x: b.left + 2, y: b.top + 60 } : { x: b.left + 40, y: b.top + 24 };
          cast(target);
          panel.classList.remove("xp-summoned"); void panel.offsetWidth; panel.classList.add("xp-summoned");
          setTimeout(() => panel.classList.remove("xp-summoned"), 1200);
        }, drawerOpened ? 260 : 60);
      }
    }).observe(document.body, { attributes: true, attributeFilter: ["class"], attributeOldValue: true, subtree: true });
  }

  // On the home screen, the big one keeps an eye on the card you're hovering, and now and then
  // lifts one with a little telekinesis.
  function homeTricks() {
    document.addEventListener("pointerover", (e) => {
      const card = e.target.closest && e.target.closest("#welcomeHeroStarters .starter-card");
      if (!card) return;
      const c = centerOf(card);
      M.lookAt(c.x, c.y, 1500);
    });
    const later = () => setTimeout(levitate, 22000 + Math.random() * 26000);
    function levitate() {
      try {
        const hero = document.querySelector(".xod-mascot--hero");
        const cards = [...document.querySelectorAll("#welcomeHeroStarters .starter-card")].filter((c) => c.offsetParent);
        if (!document.hidden && hero && hero.offsetParent && cards.length && M.mood() === "idle" && !document.querySelector(".starter-card:hover")) {
          const card = cards[Math.floor(Math.random() * cards.length)];
          const c = centerOf(card);
          const hp = M.puppet(hero);
          if (hp) hp.lookAt(c.x, c.y, 2200);
          setTimeout(() => {
            cast({ x: c.x, y: card.getBoundingClientRect().top + 4 }, { dotted: true });
            card.classList.remove("xp-levitate"); void card.offsetWidth; card.classList.add("xp-levitate");
            setTimeout(() => card.classList.remove("xp-levitate"), 2000);
          }, 500);
        }
      } catch (_) {}
      later();
    }
    later();
  }

  // Using a reply's buttons gets a reaction from that reply's companion.
  function footerReactions() {
    document.addEventListener("click", (e) => {
      const btn = e.target.closest && e.target.closest(".action-footer button");
      if (!btn) return;
      const reply = btn.closest(".message-bubble-animate, .max-w-4xl");
      const av = reply && reply.querySelector(".xod-mascot--chat");
      const p = av && M.puppet(av);
      const c = centerOf(btn);
      if (p) { p.lookAt(c.x, c.y, 1500); p.kick({ y: -50, sy: -3, sx: 3 }); }
      burst(c.x, c.y, 6, 18);
    }, true);
  }

  function init() {
    mountFamiliar();
    watchPanels();
    homeTricks();
    footerReactions();
    requestAnimationFrame(tick);
  }

  window.XodPowers = { cast, beam, burst, goHome, hide, state: () => ({ out: F.out, target: F.target && (F.target.writing ? "writing" : F.target.home ? "home" : F.target.el && F.target.el.className) }) };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
