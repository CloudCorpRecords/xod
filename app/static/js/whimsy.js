// ==============================================================
// XOD - small joys
// The night sky, the greeting, rotating ideas in the input and a
// few tiny reactions. The moon itself lives in mascot.js.
// Everything here is decorative: if it fails, the app keeps working.
// ==============================================================
(() => {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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
        const b = s.getBoundingClientRect();
        window.dispatchEvent(new CustomEvent("xod:shooting-star", { detail: { x: b.left + 60, y: b.top + 20 } }));
      }
      scheduleShootingStar();
    }, 25000 + Math.random() * 35000);
  }

  // ---------- Wire up ----------
  function init() {
    buildSky();
    const g = document.getElementById("xodGreeting");
    if (g) g.textContent = greeting();

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
    setInterval(rotatePlaceholder, 4500);
    scheduleShootingStar();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
