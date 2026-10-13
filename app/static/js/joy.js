// Things that make Xod happy, and that it sometimes asks for.
//  - Treats: every answer and build you finish together leaves a little stardust (services/soul.py
//    earns it on the server). Give one back: it flies to Xod, who catches it, munches with full
//    cheeks, gets heart eyes and does a little dance.
//  - Catch: a glowing star you can grab and fling around the window. Xod chases it, catches it
//    (streaks count), brings it back and asks for another throw.
//  - Wishes: now and then, when you're around and it's quiet, it asks for something small: a treat,
//    a game, a pet, one good thing about your day, or to show you a trick. "Not now" is always fine
//    and costs nothing; at most a few a day.
//  - Little rituals: a good-morning stretch, a giggle when you keep petting it (a tickle), heart eyes
//    when you thank it, a twinkle when it earns stardust.
// Decorative: if anything here fails, the app keeps working.
(() => {
  if (!window.XodMascot || !window.XodSoul) return;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  const now = () => performance.now();
  const api = async (url, body) => (await fetch(url, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
  const W = XodSoul.wanderer;
  const soul = () => (XodSoul.state && XodSoul.state().soul) || {};
  const name = () => { const s = soul(); return s.name && s.bond >= 15 ? s.name : ""; };
  const today = () => new Date().toISOString().slice(0, 10);
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  };
  // app.js keeps `isStreaming` as a top-level let (not on window): read it by name.
  const streamingNow = () => { try { return typeof isStreaming !== "undefined" && !!isStreaming; } catch (e) { return false; } };

  // Where the wanderer hangs out for a moment with you: above the chat box, middle of the window.
  function stage() {
    const f = document.getElementById("chatForm");
    const r = f && f.getBoundingClientRect();
    const y = r && r.top > 200 ? r.top - 90 : innerHeight * 0.55;
    return { x: innerWidth * 0.5 + (Math.random() - 0.5) * 120, y: clamp(y, 140, innerHeight - 140) };
  }
  // Each outing has a number; "go home in a bit" only applies if nothing new started since.
  let outing = 0;
  function out(what) {
    outing++;
    W.leave();
    W.with(what);
    W.el.classList.add("is-joy");          // a bit bigger while it's with you, so faces read
  }
  function done(after = 1600) {
    const mine = ++outing;
    W.with("winding-down");
    setTimeout(() => { if (mine === outing) { W.el.classList.remove("is-joy"); W.home(); } }, after);
  }
  const burst = (x, y, n = 10, r = 30) => { if (window.XodPowers) XodPowers.burst(x, y, n, r); };

  // ---------------------------------------------------------------- treats
  let treating = false;
  async function treat(fromEl) {
    if (treating) return;
    treating = true;
    try {
      let r;
      try { r = await api("/api/soul/event", { kind: "treat" }); } catch (e) { return; }
      if (r) XodSoul.apply(r);
      out("treat");
      const spot = stage();
      if (r && r.no_stardust) {
        W.fly(spot, () => {
          W.say("No stardust yet. It shows up when we finish things together ✦", 3600);
          done(3800);
        });
        return;
      }
      const src = fromEl && fromEl.getBoundingClientRect && fromEl.getBoundingClientRect().width
        ? XodSoul.wanderer.centerOf(fromEl) : { x: innerWidth / 2, y: innerHeight - 40 };
      W.fly(spot, () => toss(src, () => {
        XodMascot.munch(1500);
        W.say(pick(["Nom nom…", "*munch munch*", "Mmmf."]), 1400);
        setTimeout(() => {
          XodMascot.love(2600);
          XodMascot.dance(2400);
          const p = W.pos();
          burst(p.x, p.y, 14, 40);
          for (let i = 0; i < 3; i++) setTimeout(() => XodMascot.glyph(W.body, "♥"), i * 260);
          W.say(pick(["Mmm, stardust!", "Crunchy. Sparkly. Perfect.", "Tastes like a good day.", `Thank you${name() ? ", " + name() : ""}!`, "Best snack in the galaxy."]), 2600);
          done(3000);
        }, 1500);
      }));
    } finally {
      setTimeout(() => { treating = false; }, 1500);
    }
  }

  // A star arcs from where you gave it into Xod's mouth; Xod watches it come.
  function toss(from, onCaught) {
    const star = document.createElement("span");
    star.className = "xj-star";
    star.textContent = "✦";
    document.body.appendChild(star);
    const p = W.puppet();
    const t0 = now(), dur = reduceMotion ? 1 : 900;
    const frame = () => {
      const to = W.pos();
      const k = clamp((now() - t0) / dur, 0, 1), e = 1 - Math.pow(1 - k, 2);
      const x = from.x + (to.x - from.x) * e, y = from.y + (to.y - 8 - from.y) * e - Math.sin(Math.PI * k) * 140;
      star.style.transform = `translate(${x - 10}px, ${y - 10}px) rotate(${k * 540}deg) scale(${1 - k * 0.45})`;
      if (p) p.lookAt(x, y, 300);
      if (k > 0.55 && !star.dataset.ooh) { star.dataset.ooh = "1"; if (p) p.kick({ y: -30, sy: 3 }); }
      if (k < 1) { requestAnimationFrame(frame); return; }
      star.remove();
      if (p) p.kick({ sy: -4, sx: 4 });
      burst(to.x, to.y - 6, 6, 14);
      onCaught();
    };
    requestAnimationFrame(frame);
  }

  // ---------------------------------------------------------------- catch
  const G = { on: false, ball: null, hud: null, b: null, raf: 0, catches: 0, streak: 0, best: 0, lastThrow: 0, state: "held" };
  function play() {
    if (G.on) return;
    if (reduceMotion) { W.say && W.say("Catch needs motion turned on in your Mac's settings.", 2600); return; }
    G.on = true; G.catches = 0; G.streak = 0; G.best = 0; G.lastThrow = now(); G.thrown = 0;
    out("play");
    const spot = stage();
    W.fly(spot, () => {
      W.say(pick(["Throw it! Drag the star and let go.", "Ooh, a game! Fling the star.", "Ready! Grab the star and throw it."]), 3200);
      makeBall(spot.x + 60, spot.y - 50);
      G.raf = requestAnimationFrame(tick);
    });
    G.hud = document.createElement("div");
    G.hud.className = "xj-hud";
    G.hud.innerHTML = `<span class="xj-dot"></span><span data-n>Catch · drag the star and let go</span><button data-end>Done</button>`;
    document.body.appendChild(G.hud);
    G.hud.querySelector("[data-end]").addEventListener("click", () => endPlay());
  }

  function makeBall(x, y) {
    const el = document.createElement("div");
    el.className = "xj-ball";
    el.innerHTML = "<i>✦</i>";
    document.body.appendChild(el);
    G.ball = el;
    G.b = { x, y, vx: 0, vy: 0, spin: 0, rot: 0 };
    G.state = "free";
    el.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      el.setPointerCapture(e.pointerId);
      G.state = "drag";
      G.drag = { lastX: e.clientX, lastY: e.clientY, lastT: now(), vx: 0, vy: 0, moved: 0 };
      el.classList.add("is-held");
    });
    el.addEventListener("pointermove", (e) => {
      if (G.state !== "drag") return;
      const d = G.drag, t = now(), k = Math.max(8, t - d.lastT);
      d.vx = d.vx * 0.5 + ((e.clientX - d.lastX) / k) * 1000 * 0.5;
      d.vy = d.vy * 0.5 + ((e.clientY - d.lastY) / k) * 1000 * 0.5;
      d.moved += Math.hypot(e.clientX - d.lastX, e.clientY - d.lastY);
      d.lastX = e.clientX; d.lastY = e.clientY; d.lastT = t;
      G.b.x = e.clientX; G.b.y = e.clientY;
    });
    const release = () => {
      if (G.state !== "drag") return;
      const d = G.drag;
      el.classList.remove("is-held");
      if (d.moved < 6) { d.vx = (Math.random() - 0.5) * 900; d.vy = -900 - Math.random() * 300; }   // a click: a little toss
      G.b.vx = clamp(d.vx, -2600, 2600); G.b.vy = clamp(d.vy, -2600, 2600);
      G.b.spin = G.b.vx / 6;
      G.state = "air";
      G.lastThrow = now();
      G.thrown++;
      const p = W.puppet();
      if (p) p.kick({ y: -40, sy: 3 });
    };
    el.addEventListener("pointerup", release);
    el.addEventListener("pointercancel", release);
  }

  let lastT = 0;
  function tick(t) {
    if (!G.on) return;
    const dt = Math.min(0.033, lastT ? (t - lastT) / 1000 : 0.016);
    lastT = t;
    const b = G.b;
    const floor = innerHeight - 22, left = 22, right = innerWidth - 22, top = 22;
    if (b && (G.state === "air" || G.state === "free")) {
      b.vy += 1700 * dt;
      b.x += b.vx * dt; b.y += b.vy * dt;
      if (b.x < left) { b.x = left; b.vx = Math.abs(b.vx) * 0.7; }
      if (b.x > right) { b.x = right; b.vx = -Math.abs(b.vx) * 0.7; }
      if (b.y < top) { b.y = top; b.vy = Math.abs(b.vy) * 0.6; }
      if (b.y > floor) {
        b.y = floor;
        if (Math.abs(b.vy) > 160) { b.vy = -Math.abs(b.vy) * 0.58; b.vx *= 0.82; if (G.state === "air") G.bounced = true; }
        else { b.vy = 0; b.vx *= Math.pow(0.02, dt); }
      }
      b.rot += b.spin * dt; b.spin *= Math.pow(0.4, dt);
      if (G.state === "free" && Math.hypot(b.vx, b.vy) < 20 && b.y >= floor - 1) G.state = "rest";
    }
    if (b && G.state === "rest") b.vx *= 0.9;
    // Xod: watch it, chase it when it's flying or lying there, carry it when it has it.
    const me = W.pos();
    const p = W.puppet();
    if (b && G.state !== "held") {
      if (p) p.lookAt(b.x, b.y, 200);
      if (G.state === "air" || G.state === "rest") {
        const dx = b.x - me.x, dy = b.y - 10 - me.y, d = Math.hypot(dx, dy);
        const speed = G.state === "air" ? 820 : 520;
        const step = Math.min(d, speed * dt);
        W.moveTo(me.x + (dx / (d || 1)) * step, me.y + (dy / (d || 1)) * step);
        if (p) { p.traveling(d > 30); p.lean(clamp(dx / 14, -16, 16)); }
        if (d < 34) caught();
      } else if (G.state === "drag" && p) {
        p.traveling(false); p.lean(0);
        if (Math.random() < dt * 3) p.kick({ y: -35, sy: 2.5 });            // bouncing with excitement
      }
    }
    if (G.state === "held" && G.carry) {
      const c = G.carry, k = clamp((now() - c.t0) / c.dur, 0, 1), e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      W.moveTo(c.from.x + (c.to.x - c.from.x) * e, c.from.y + (c.to.y - c.from.y) * e - Math.sin(Math.PI * e) * 50);
      const m = W.pos();
      b.x = m.x + 30; b.y = m.y + 14; b.rot += 90 * dt;          // held off to the side, face visible
      if (k >= 1) {
        G.carry = null;
        if (p) { p.traveling(false); p.lean(0); p.kick({ sy: -3, sx: 3 }); }
        b.vx = 60; b.vy = -260; G.state = "free";                          // drops it in front of you
        W.say(G.streak >= 3 ? pick([`${G.streak} in a row!`, "I'm on a roll!", "Again again!"]) : pick(["Again!", "Throw it again!", "One more?", "Fetch!"]), 1800);
      }
    }
    if (G.ball && b) G.ball.style.transform = `translate(${b.x - 13}px, ${b.y - 13}px) rotate(${b.rot}deg)`;
    if (now() - G.lastThrow > 40000 && G.state !== "drag") return endPlay(true);
    G.raf = requestAnimationFrame(tick);
  }

  function caught() {
    const airborne = G.state === "air" && !G.bounced;
    G.bounced = false;
    G.state = "held";
    const m = W.pos();
    if (airborne) {
      G.catches++; G.streak++; G.best = Math.max(G.best, G.streak);
      XodMascot.beHappy(900);
      burst(m.x, m.y, 10, 26);
      XodMascot.glyph(W.body, G.streak >= 2 ? `${G.streak}!` : pick(["got it!", "caught!", "yes!"]), "is-note");
      if (G.streak === 3 || G.streak === 6) XodMascot.dance(1700);
    } else {
      G.streak = 0;
      XodMascot.glyph(W.body, pick(["got it", "hup"]), "is-giggle");
    }
    const hudN = G.hud && G.hud.querySelector("[data-n]");
    if (hudN) hudN.textContent = `Catch · ${G.catches} caught${G.best >= 2 ? ` · best streak ${G.best}` : ""}`;
    // Bring it back toward you.
    const ptr = lastPointer || { x: innerWidth / 2, y: innerHeight / 2 };
    const to = { x: clamp(ptr.x - 70, 60, innerWidth - 60), y: clamp(ptr.y - 40, 100, innerHeight - 120) };
    G.carry = { from: m, to, t0: now(), dur: clamp(Math.hypot(to.x - m.x, to.y - m.y) * 1.3, 450, 1300) };
  }

  async function endPlay(timedOut = false) {
    if (!G.on) return;
    G.on = false;
    cancelAnimationFrame(G.raf);
    lastT = 0;
    if (G.ball) { const b = G.ball; b.classList.add("is-gone"); setTimeout(() => b.remove(), 400); }
    if (G.hud) { G.hud.remove(); G.hud = null; }
    G.ball = null; G.b = null;
    const p = W.puppet();
    if (p) { p.traveling(false); p.lean(0); }
    if (G.thrown) {
      try { XodSoul.apply(await api("/api/soul/event", { kind: "play", detail: String(G.best) })); } catch (e) {}
    }
    const line = !G.thrown ? (timedOut ? "Okay, maybe later!" : "Aw. Next time!")
      : G.best >= 5 ? `${G.best} in a row! That was amazing.` : G.catches >= 3 ? "That was so fun. Thank you!" : "Fun! Let's play again sometime.";
    W.say(line, 2600);
    if (G.catches >= 3) { XodMascot.love(2000); XodMascot.dance(2000); }
    W.with("chat");
    done(2800);
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && G.on) endPlay(); });
  let lastPointer = null;
  window.addEventListener("pointermove", (e) => { lastPointer = { x: e.clientX, y: e.clientY }; lastActive = Date.now(); }, { passive: true });

  // ---------------------------------------------------------------- wishes
  let lastActive = Date.now();
  ["keydown", "pointerdown"].forEach((ev) => window.addEventListener(ev, () => { lastActive = Date.now(); }, { passive: true, capture: true }));
  const WISHES = {
    treat: { ask: () => pick(["Could I have a little stardust? ✦", "I'm a tiny bit peckish… stardust?", "Is there a snack? Stardust, maybe?"]), yes: "Here you go" },
    play: { ask: () => pick(["Wanna play catch?", "I'm bouncy today. Catch?", "Throw me the star? Just once?"]), yes: "Let's play" },
    pet: { ask: () => pick(["Pet me? Just a little. (Wiggle the cursor over me.)", "Could I have a pat? Wiggle the cursor over me.", "Head pat? Wiggle your cursor over me."]), yes: null },
    good: { ask: () => pick([`Tell me one good thing about today${name() ? ", " + name() : ""}?`, "What's one good thing that happened today?", "Something nice today? I'd love to hear it."]), yes: "Sure" },
    trick: { ask: () => pick(["Want to see a trick?", "I learned a trick. Can I show you?", "Watch this? It's a good one."]), yes: "Show me!" },
  };
  function canWish() {
    const s = soul();
    if (!s.settings || s.settings.wander === false || reduceMotion || document.hidden) return false;
    if (Date.now() - lastActive > 90000 || Date.now() - lastActive < 4000) return false;   // you're here, but not mid-click
    if (W.out() || W.busy() || streamingNow() || G.on || treating) return false;
    const input = document.getElementById("chatInput");
    if (input && (input.value || "").trim()) return false;                                   // you're writing
    const day = store.get("xod.wishes", { date: "", n: 0 });
    if (day.date === today() && day.n >= 3) return false;
    return Date.now() - store.get("xod.wishAt", 0) > 25 * 60 * 1000;
  }
  function wish(kind) {
    const s = soul();
    const kinds = kind ? [kind] : [...((s.stardust || 0) > 0 ? ["treat", "treat"] : []), "play", "pet", "good", "trick"];
    const k = pick(kinds);
    const w = WISHES[k];
    store.set("xod.wishAt", Date.now());
    const day = store.get("xod.wishes", { date: "", n: 0 });
    store.set("xod.wishes", { date: today(), n: day.date === today() ? day.n + 1 : 1 });
    out("wish");
    W.fly(stage(), () => {
      const p = W.puppet();
      if (p) p.kick({ y: -30, sy: 3 });
      XodMascot.glyph(W.body, "?", "is-ask");
      W.say(`<p class="xj-wish">${w.ask()}</p><div class="xj-wish-actions">${w.yes ? `<button data-w="yes" class="is-main">${w.yes}</button>` : ""}<button data-w="no">Not now</button></div>`, 0, true);
      W.bubble.onclick = (e) => {
        const b = e.target.closest("[data-w]");
        if (!b) return;
        W.bubble.onclick = null;
        clearTimeout(wishTimer);
        if (b.dataset.w === "no") { W.say(pick(["Okay! Later.", "No worries.", "That's okay!"]), 1600); return done(1700); }
        grant(k);
      };
      let petted = false;
      const onPet = () => {
        if (k !== "pet" || petted) return;
        petted = true;
        W.bubble.onclick = null;
        clearTimeout(wishTimer);
        grant("pet");
      };
      window.addEventListener("xod:pet", onPet, { once: false });
      const wishTimer = setTimeout(() => {
        window.removeEventListener("xod:pet", onPet);
        if (W.with() !== "wish") return;
        W.bubble.onclick = null;
        W.say(pick(["Maybe later!", "Okay, another time."]), 1600);
        done(1700);
      }, 22000);
      setTimeout(() => window.removeEventListener("xod:pet", onPet), 23000);
    });
  }
  async function grant(k) {
    api("/api/soul/event", { kind: "wish" }).then((r) => XodSoul.apply(r)).catch(() => {});
    W.hush();
    if (k === "treat") { W.with(null); return treat(); }
    if (k === "play") { W.with(null); return play(); }
    if (k === "pet") {
      XodMascot.love(2400);
      W.say(pick(["Mmm. Thank you.", "♥", "That was nice."]), 2200);
      return done(2600);
    }
    if (k === "good") {
      const input = document.getElementById("chatInput");
      if (input) {
        input.value = "One good thing about today: ";
        input.focus();
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
      XodMascot.love(1800);
      W.say("I'm listening ♥", 1800);
      return done(1900);
    }
    if (k === "trick") {
      const p = W.puppet();
      W.say("Ta-…", 900);
      if (p) p.fidget(p.info.special || "hop");
      setTimeout(() => { XodMascot.flip(W.body); }, 900);
      setTimeout(() => { XodMascot.dance(2000); const m = W.pos(); burst(m.x, m.y, 14, 40); W.say("…da!", 1800); }, 1800);
      return done(4200);
    }
  }
  setInterval(() => { if (Math.random() < 0.3 && canWish()) wish(); }, 60000);

  // ---------------------------------------------------------------- little rituals
  // Speak from wherever Xod is: the big one on the home screen, or a short visit from the wanderer.
  function speak(text, ms = 3000, fidget) {
    const hero = document.querySelector(".xod-mascot--hero");
    if (hero && hero.getBoundingClientRect().width && document.getElementById("xodBubble")?.offsetParent) {
      if (fidget) XodMascot.fidget(fidget);
      setTimeout(() => XodMascot.say(text, ms), fidget ? 900 : 0);
      return;
    }
    if (W.out() || W.busy()) return;
    out("chat");
    const h = document.querySelector(".xod-mascot--small");
    const r = h && h.getBoundingClientRect();
    W.fly(r && r.width ? { x: r.left + 120, y: r.bottom + 50 } : stage(), () => {
      const p = W.puppet();
      if (fidget && p) p.fidget(fidget);
      setTimeout(() => W.say(text, ms), fidget ? 900 : 0);
      done(ms + (fidget ? 1300 : 400));
    });
  }

  // Hello for the day: a stretch in the morning, a quieter hello later on.
  setTimeout(() => {
    if (store.get("xod.greetDay", "") === today()) return;
    store.set("xod.greetDay", today());
    const h = new Date().getHours(), n = name();
    const line = h >= 5 && h < 12 ? `Good morning${n ? ", " + n : ""}! ☀`
      : h >= 12 && h < 18 ? `Hi${n ? " " + n : ""}! Good to see you.`
      : h >= 18 && h < 23 ? `Evening${n ? ", " + n : ""}. ✨`
      : `Up late${n ? ", " + n : ""}? Me too.`;
    speak(line, 3200, h >= 5 && h < 12 ? "stretch" : "hop");
  }, 3500);

  // Tickled: you kept petting.
  window.addEventListener("xod:tickle", () => {
    const line = pick(["Hehe, that tickles!", "Hahaha stop!", "Hee hee hee!"]);
    if (W.out()) W.say(line, 1600); else XodMascot.say(line, 1600);
  });

  // From the chat: you thanked it, or it earned stardust.
  window.addEventListener("xod:soul", (e) => {
    const ev = e.detail || {};
    if (ev.kind === "praise") {
      XodMascot.love(2600);
      const m = document.querySelector(".xod-mascot--hero") || document.querySelector(".xod-mascot--small");
      if (m) for (let i = 0; i < 3; i++) setTimeout(() => XodMascot.glyph(m, "♥"), i * 220);
    }
    if (ev.earned) {
      const m = document.querySelector(".xod-mascot--small") || document.querySelector(".xod-mascot--hero");
      if (m) setTimeout(() => XodMascot.glyph(m, `+${ev.earned} ✦`, "is-dust"), 600);
      dockCount();
    }
  });

  // ---------------------------------------------------------------- the little dock
  // Hover over Xod (home screen or the header): Treat and Catch.
  const dock = document.createElement("div");
  dock.className = "xj-dock";
  dock.innerHTML = `<button data-j="treat" title="Give Xod a stardust treat"><span>✦</span><b data-count>0</b></button>
    <button data-j="play" title="Play catch"><i data-lucide="circle-dot" class="size-3.5"></i></button>
    <button data-j="heart" title="Xod's heart"><i data-lucide="heart" class="size-3.5"></i></button>`;
  document.body.appendChild(dock);
  if (window.lucide) lucide.createIcons();
  function dockCount() { const c = dock.querySelector("[data-count]"); if (c) c.textContent = String(soul().stardust || 0); }
  let dockFor = null, dockHide = 0;
  function showDock(m) {
    clearTimeout(dockHide);
    if (G.on) return;
    dockFor = m;
    dockCount();
    const r = m.getBoundingClientRect();
    const below = r.bottom + 44 < innerHeight;
    dock.style.left = `${clamp(r.left + r.width / 2, 70, innerWidth - 70)}px`;
    dock.style.top = `${below ? r.bottom + 6 : r.top - 40}px`;
    dock.classList.add("is-on");
  }
  function hideDock(ms = 900) { clearTimeout(dockHide); dockHide = setTimeout(() => dock.classList.remove("is-on"), ms); }
  document.addEventListener("pointerover", (e) => {
    const m = e.target.closest && e.target.closest(".xod-mascot--hero, .xod-mascot--small");
    if (m) showDock(m);
    else if (e.target.closest && e.target.closest(".xj-dock")) clearTimeout(dockHide);
  });
  document.addEventListener("pointerout", (e) => {
    if (e.target.closest && (e.target.closest(".xod-mascot--hero, .xod-mascot--small") || e.target.closest(".xj-dock"))) hideDock();
  });
  dock.addEventListener("click", (e) => {
    const b = e.target.closest("[data-j]");
    if (!b) return;
    hideDock(0);
    if (b.dataset.j === "treat") treat(b);
    else if (b.dataset.j === "play") play();
    else XodSoul.openHeart();
  });

  window.XodJoy = { treat, play, endPlay, wish, speak, state: () => ({ playing: G.on, catches: G.catches, best: G.best, ball: G.b ? { ...G.b } : null, held: G.state }) };
})();
