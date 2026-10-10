// ==============================================================
// XOD - small joys
// The moon mascot, the night sky, the greeting, and a few tiny
// reactions. Everything here is decorative: if it fails, the app
// keeps working exactly as before.
// ==============================================================
(() => {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let uid = 0;

  // ---------- The moon ----------
  function moonSvg() {
    const id = `xm${uid++}`;
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
  </defs>
  <circle class="xm-glow" cx="32" cy="34" r="31" fill="url(#${id}-glow)"/>
  <g class="xm-body">
    <circle cx="32" cy="34" r="22" fill="url(#${id}-moon)"/>
    <circle cx="44" cy="23" r="3.2" fill="#c98a2e" opacity="0.22"/>
    <circle cx="47.5" cy="43" r="1.9" fill="#c98a2e" opacity="0.2"/>
    <circle cx="18.5" cy="25" r="1.5" fill="#c98a2e" opacity="0.2"/>
    <ellipse class="xm-cheek" cx="19" cy="40.5" rx="3.6" ry="2.2" fill="#ee8fae" opacity="0.5"/>
    <ellipse class="xm-cheek" cx="45" cy="40.5" rx="3.6" ry="2.2" fill="#ee8fae" opacity="0.5"/>
    <g class="xm-eyes">
      <g class="xm-lids">
        <g class="xm-open">
          <ellipse cx="25" cy="33" rx="2.6" ry="3.4" fill="#0f1222"/>
          <ellipse cx="39" cy="33" rx="2.6" ry="3.4" fill="#0f1222"/>
          <circle cx="26" cy="31.7" r="0.95" fill="#fffaf0"/>
          <circle cx="40" cy="31.7" r="0.95" fill="#fffaf0"/>
        </g>
      </g>
      <g class="xm-shut" fill="none" stroke="#0f1222" stroke-width="1.8" stroke-linecap="round">
        <path d="M22 33.5 Q25 36.2 28 33.5"/><path d="M36 33.5 Q39 36.2 42 33.5"/>
      </g>
      <g class="xm-joy" fill="none" stroke="#0f1222" stroke-width="1.9" stroke-linecap="round">
        <path d="M22 34.5 Q25 30.5 28 34.5"/><path d="M36 34.5 Q39 30.5 42 34.5"/>
      </g>
    </g>
    <path class="xm-smile" d="M29.4 40.4 Q32 43 34.6 40.4" fill="none" stroke="#0f1222" stroke-width="1.6" stroke-linecap="round"/>
    <ellipse class="xm-o" cx="32" cy="41.6" rx="1.4" ry="1.7" fill="#0f1222"/>
    <path class="xm-grin" d="M28.3 39.8 Q32 46 35.7 39.8 Z" fill="#0f1222"/>
  </g>
  <g class="xm-orbit">
    <path d="M32 3.5 L33.2 6.6 L36.3 7.8 L33.2 9 L32 12.1 L30.8 9 L27.7 7.8 L30.8 6.6 Z" fill="#ffd06b"/>
  </g>
  <text class="xm-z" x="50" y="18" font-size="9" font-family="Fraunces, serif" font-style="italic" fill="#fffaf0">z</text>
  <text class="xm-z xm-z2" x="53" y="13" font-size="7" font-family="Fraunces, serif" font-style="italic" fill="#fffaf0">z</text>
</svg>`;
  }

  // The header and hero moons are always "alive". In the chat, only the newest
  // reply's moon reacts; older ones stay quietly put.
  const mascots = () => {
    const live = [...document.querySelectorAll(".xod-mascot:not(.xod-mascot--chat)")];
    const chat = document.querySelectorAll(".xod-mascot--chat");
    if (chat.length) live.push(chat[chat.length - 1]);
    return live;
  };

  function mountMascots(root = document) {
    root.querySelectorAll(".xod-mascot").forEach((m) => {
      if (!m.querySelector("svg")) m.innerHTML = moonSvg();
    });
  }

  function settleOldChatMoons() {
    const chat = document.querySelectorAll(".xod-mascot--chat");
    for (let i = 0; i < chat.length - 1; i++) {
      if (chat[i].dataset.mood !== "idle") chat[i].dataset.mood = "idle";
      const eyes = chat[i].querySelector(".xm-eyes");
      if (eyes) eyes.style.transform = "";
    }
  }

  // ---------- Mood ----------
  let baseMood = "idle"; // idle | thinking | sleepy
  let happyUntil = 0;

  function applyMood() {
    const mood = Date.now() < happyUntil ? "happy" : baseMood;
    mascots().forEach((m) => {
      if (m.dataset.mood !== mood) m.dataset.mood = mood;
    });
  }

  function beHappy(ms = 1800) {
    happyUntil = Date.now() + ms;
    applyMood();
    setTimeout(applyMood, ms + 20);
  }

  // ---------- Eyes ----------
  let pointer = null;
  let wanderTarget = { x: 0, y: -1 };

  function lookAt(m, dx, dy) {
    const eyes = m.querySelector(".xm-eyes");
    if (eyes) eyes.style.transform = `translate(${dx.toFixed(2)}px, ${dy.toFixed(2)}px)`;
  }

  function updateEyes() {
    mascots().forEach((m) => {
      const mood = m.dataset.mood;
      if (mood === "sleepy" || mood === "happy") return lookAt(m, 0, 0);
      if (mood === "thinking") return lookAt(m, wanderTarget.x * 2.4, wanderTarget.y * 2);
      if (!pointer) return lookAt(m, 0, 0);
      const r = m.getBoundingClientRect();
      if (!r.width) return;
      const dx = pointer.x - (r.left + r.width / 2);
      const dy = pointer.y - (r.top + r.height / 2);
      const dist = Math.hypot(dx, dy) || 1;
      const reach = Math.min(1, dist / 160);
      lookAt(m, (dx / dist) * 2.6 * reach, (dy / dist) * 2.2 * reach);
    });
  }

  setInterval(() => {
    const a = Math.random() * Math.PI * 2;
    wanderTarget = { x: Math.cos(a), y: Math.min(0.3, Math.sin(a)) };
  }, 900);

  // ---------- Blinking ----------
  function blink(m) {
    m.classList.add("is-blinking");
    setTimeout(() => m.classList.remove("is-blinking"), 170);
  }
  function scheduleBlink() {
    setTimeout(() => {
      mascots().forEach((m) => {
        if (m.dataset.mood !== "idle" && m.dataset.mood !== "thinking") return;
        blink(m);
        if (Math.random() < 0.2) setTimeout(() => blink(m), 260);
      });
      scheduleBlink();
    }, 2600 + Math.random() * 4200);
  }

  // ---------- Idle -> sleepy ----------
  const SLEEP_AFTER_MS = 2 * 60 * 1000;
  let lastActivity = Date.now();

  function noteActivity() {
    lastActivity = Date.now();
    if (baseMood === "sleepy") {
      baseMood = "idle";
      applyMood();
      mascots().forEach(blink);
    }
  }

  // ---------- Watch the agent ----------
  let wasStreaming = false;
  function watchAgent() {
    let streaming = false;
    try { streaming = typeof isStreaming !== "undefined" && !!isStreaming; } catch (_) {}
    // Background tasks count as "working" too (set by tasks.js).
    streaming = streaming || !!window.__xodTasksWorking;

    if (streaming) {
      baseMood = "thinking";
      lastActivity = Date.now();
    } else if (baseMood === "thinking") {
      baseMood = "idle";
    } else if (baseMood === "idle" && Date.now() - lastActivity > SLEEP_AFTER_MS) {
      baseMood = "sleepy";
    }

    if (wasStreaming && !streaming) {
      beHappy();
      const header = document.querySelector(".xod-mascot--small");
      if (header) sparkleBurst(header);
    }
    wasStreaming = streaming;
    settleOldChatMoons();
    applyMood();
  }

  // ---------- Sparkles ----------
  const SPARK_COLORS = ["#f2b24a", "#ffd06b", "#ee8fae", "#6fd0bf", "#fffaf0"];
  function sparkleBurst(el, count = 9) {
    if (reduceMotion) return;
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
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

  // ---------- Talking ----------
  const HELLOS = [
    "Hi! I'm Xod. I like questions.",
    "Ask me something you've been wondering about.",
    "Mostly moon. Partly search engine.",
    "I'll do the digging. You do the deciding.",
    "That tickles.",
    "No question is too small. Or too big, honestly.",
  ];
  let helloIndex = 0;
  let bubbleTimer = null;
  let boops = [];

  function say(text, ms = 3200) {
    const bubble = document.getElementById("xodBubble");
    if (!bubble) return;
    bubble.textContent = text;
    bubble.classList.add("is-visible");
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(() => bubble.classList.remove("is-visible"), ms);
  }

  function onMascotClick(m) {
    noteActivity();
    const now = Date.now();
    boops = boops.filter((t) => now - t < 4000);
    boops.push(now);

    if (boops.length >= 7) {
      boops = [];
      m.classList.remove("is-spinning");
      void m.offsetWidth;
      m.classList.add("is-spinning");
      setTimeout(() => m.classList.remove("is-spinning"), 950);
      sparkleBurst(m, 14);
      beHappy(1400);
      if (m.classList.contains("xod-mascot--hero")) say("Wheee.", 1800);
      return;
    }

    m.classList.remove("is-squished");
    void m.offsetWidth;
    m.classList.add("is-squished");
    setTimeout(() => m.classList.remove("is-squished"), 460);
    beHappy(900);
    if (m.classList.contains("xod-mascot--hero")) {
      say(HELLOS[helloIndex % HELLOS.length]);
      helloIndex++;
    }
  }

  // ---------- Greeting ----------
  function greeting() {
    const now = new Date();
    const h = now.getHours();
    const day = now.getDay();
    if (h < 5) return "Up late? Let's make it worth it.";
    if (h < 12) return day === 1 ? "Fresh week. Good morning." : "Good morning.";
    if (h < 17) return day === 5 ? "Good afternoon. Almost the weekend." : "Good afternoon.";
    if (h < 22) return "Good evening.";
    return "Night owl hours. I'm up too.";
  }

  // ---------- Placeholder ideas ----------
  const DEFAULT_PLACEHOLDER = "Ask anything…";
  const IDEAS = [
    DEFAULT_PLACEHOLDER,
    "Who are Notion's biggest competitors?",
    "What is linear.app built with?",
    "Find a profitable SaaS for sale under $50k",
    "Turn that last answer into a table",
    "What's everyone talking about on Hacker News?",
  ];
  let ideaIndex = 0;
  function rotatePlaceholder() {
    const input = document.getElementById("chatInput");
    if (!input || input.value || !IDEAS.includes(input.placeholder)) return;
    if (document.activeElement === input) return;
    ideaIndex = (ideaIndex + 1) % IDEAS.length;
    input.classList.add("placeholder-fading");
    setTimeout(() => {
      input.placeholder = IDEAS[ideaIndex];
      input.classList.remove("placeholder-fading");
    }, 300);
  }

  // ---------- Sky ----------
  function buildSky() {
    const sky = document.getElementById("nightSky");
    if (!sky) return;
    const count = Math.min(90, Math.round((window.innerWidth * window.innerHeight) / 16000));
    const frag = document.createDocumentFragment();
    for (let i = 0; i < count; i++) {
      const s = document.createElement("span");
      const size = Math.random() < 0.85 ? 1 : 2;
      s.className = Math.random() < 0.2 ? "star star--warm" : "star";
      s.style.left = `${Math.random() * 100}%`;
      // denser near the top, like a real sky above the horizon glow
      s.style.top = `${Math.pow(Math.random(), 1.6) * 85}%`;
      s.style.width = s.style.height = `${size}px`;
      s.style.setProperty("--o", (0.15 + Math.random() * 0.5).toFixed(2));
      s.style.setProperty("--d", `${4 + Math.random() * 6}s`);
      s.style.setProperty("--delay", `${-Math.random() * 10}s`);
      frag.appendChild(s);
    }
    sky.appendChild(frag);
  }

  function scheduleShootingStar() {
    if (reduceMotion) return;
    setTimeout(() => {
      const sky = document.getElementById("nightSky");
      if (sky && document.visibilityState === "visible") {
        const s = document.createElement("span");
        s.className = "shooting-star";
        s.style.left = `${5 + Math.random() * 60}%`;
        s.style.top = `${4 + Math.random() * 30}%`;
        s.style.setProperty("--angle", `${12 + Math.random() * 22}deg`);
        sky.appendChild(s);
        setTimeout(() => s.remove(), 1200);
      }
      scheduleShootingStar();
    }, 25000 + Math.random() * 35000);
  }

  // ---------- Wire up ----------
  function init() {
    mountMascots();
    buildSky();

    new MutationObserver((records) => {
      for (const r of records) {
        for (const n of r.addedNodes) {
          if (n.nodeType !== 1) continue;
          if (n.matches(".xod-mascot") || n.querySelector(".xod-mascot")) {
            mountMascots(n.parentNode || document);
          }
        }
      }
    }).observe(document.body, { childList: true, subtree: true });

    const g = document.getElementById("xodGreeting");
    if (g) g.textContent = greeting();

    document.addEventListener("pointermove", (e) => {
      pointer = { x: e.clientX, y: e.clientY };
      noteActivity();
    }, { passive: true });
    document.addEventListener("keydown", noteActivity, { passive: true });

    document.addEventListener("click", (e) => {
      const m = e.target.closest(".xod-mascot");
      if (m) onMascotClick(m);
    });

    const form = document.getElementById("chatForm");
    const send = document.getElementById("sendButton");
    if (form && send) {
      form.addEventListener("submit", () => {
        if (reduceMotion) return;
        send.classList.remove("is-launching");
        void send.offsetWidth;
        send.classList.add("is-launching");
        setTimeout(() => send.classList.remove("is-launching"), 600);
      }, true);
    }

    // First visit only: a small hello.
    try {
      if (!localStorage.getItem("xod.saidHello")) {
        setTimeout(() => say("Hi! I'm Xod. Pick a card, or ask me anything.", 5000), 1100);
        localStorage.setItem("xod.saidHello", "1");
      }
    } catch (_) {}

    const tick = () => { updateEyes(); requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
    setInterval(watchAgent, 300);
    setInterval(rotatePlaceholder, 4500);
    scheduleBlink();
    scheduleShootingStar();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
