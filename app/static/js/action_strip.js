/**
 * Inline Live Action Strip for Browser & Computer Use inside Chat.
 * Shows live step progress, clickable mini proof screenshot, Stop, and Take Over
 * right inside the active assistant bubble whenever Xod runs browser/desktop/chrome tools.
 */
(function () {
  const TRACKED_PREFIXES = ["browser_", "desktop_", "mcp__computer-control__"];

  function isTracked(name) {
    const n = String(name || "").toLowerCase();
    return TRACKED_PREFIXES.some((p) => n.startsWith(p));
  }

  function esc(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function humanLabel(name, args) {
    const n = String(name || "");
    const a = args || {};
    if (n === "desktop_chrome_tabs") return "Listing your signed-in Chrome tabs";
    if (n === "desktop_chrome_focus_tab") return `Focusing Chrome tab${a.query ? ` (${a.query})` : ""}`;
    if (n === "desktop_chrome_read_tab") return `Reading signed-in Chrome tab${a.query ? ` (${a.query})` : ""}`;
    if (n === "desktop_chrome_open_and_read") return `Opening ${a.url || "page"} in your Chrome`;
    if (n === "desktop_chrome_draft_text") return "Drafting text in your signed-in Chrome";
    if (n === "desktop_look") return "Looking at your Mac screen";
    if (n === "desktop_click") return "Clicking on your Mac screen";
    if (n === "desktop_type") return "Typing on your Mac";
    if (n === "desktop_open_app") return `Opening ${a.name || "app"}`;
    if (n.startsWith("browser_navigate") || n.startsWith("browser_open")) return `Opening ${a.url || "website"}`;
    if (n.startsWith("browser_click")) return "Clicking on webpage";
    if (n.startsWith("browser_type") || n.startsWith("browser_fill")) return "Typing into webpage";
    if (n.startsWith("browser_")) return `Browser: ${n.replace(/^browser_/, "").replace(/_/g, " ")}`;
    if (n.startsWith("desktop_")) return `Mac: ${n.replace(/^desktop_/, "").replace(/_/g, " ")}`;
    if (n.startsWith("mcp__computer-control__")) return `Computer: ${n.split("__")[2] || "action"}`;
    return n;
  }

  function shortPill(name) {
    return String(name || "")
      .replace(/^mcp__computer-control__/, "")
      .replace(/^desktop_chrome_/, "chrome ")
      .replace(/^desktop_/, "mac ")
      .replace(/^browser_/, "")
      .replace(/_/g, " ")
      .slice(0, 22);
  }

  function ensureStrip(bubble) {
    if (!bubble || !bubble.wrapper) return null;
    const host = bubble.wrapper.querySelector(".flex-1");
    if (!host) return null;
    let strip = host.querySelector(".xod-action-strip");
    if (strip) return strip;

    strip = document.createElement("div");
    strip.className = "xod-action-strip mb-3 rounded-xl bg-surface-850/95 border border-brand-500/25 p-3 space-y-2 shadow-lg";
    strip.dataset.steps = "0";
    strip.innerHTML = `
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-2 min-w-0">
          <span data-as-dot class="size-2 rounded-full bg-emerald-400 animate-pulse shrink-0"></span>
          <span data-as-badge class="text-[10px] uppercase tracking-wider font-semibold text-emerald-300 shrink-0">Live</span>
          <span data-as-title class="text-[12px] font-medium text-zinc-100 truncate">Working on your computer…</span>
        </div>
        <div class="flex items-center gap-1.5 shrink-0">
          <button type="button" data-as-stop class="px-2 py-1 rounded-md bg-red-500/15 hover:bg-red-500/25 border border-red-500/30 text-[11px] font-medium text-red-200">Stop</button>
          <button type="button" data-as-takeover class="px-2 py-1 rounded-md bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/30 text-[11px] font-medium text-emerald-200">Take Over</button>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <img data-as-thumb alt="Live preview" class="hidden w-28 h-18 object-cover rounded-lg border border-white/[0.1] cursor-pointer shrink-0 bg-surface-900">
        <div data-as-trail class="flex flex-wrap gap-1 flex-1 min-w-0"></div>
      </div>
    `;

    strip.querySelector("[data-as-stop]").addEventListener("click", () => {
      if (typeof window.stopResearch === "function") window.stopResearch();
    });
    strip.querySelector("[data-as-takeover]").addEventListener("click", () => {
      if (typeof window.openAutonomousCockpitModal === "function") window.openAutonomousCockpitModal();
    });
    strip.querySelector("[data-as-thumb]").addEventListener("click", (e) => {
      const viewer = document.getElementById("taskShotViewer");
      const img = document.getElementById("taskShotImg");
      if (viewer && img && e.target.src) {
        img.src = e.target.src;
        viewer.classList.remove("hidden");
      }
    });

    host.insertBefore(strip, host.firstChild);
    return strip;
  }

  async function refreshThumb(strip, data) {
    const img = strip.querySelector("[data-as-thumb]");
    if (!img) return;
    if (data && typeof data.screenshot_uri === "string" && data.screenshot_uri.startsWith("data:image")) {
      img.src = data.screenshot_uri;
      img.classList.remove("hidden");
      return;
    }
    try {
      const r = await fetch("/api/browser/proof-thumb");
      if (!r.ok) return;
      const j = await r.json();
      if (j && j.ok && j.image_b64) {
        img.src = "data:image/jpeg;base64," + j.image_b64;
        img.classList.remove("hidden");
      }
    } catch (_) {}
  }

  function onToolCall(bubble, name, args) {
    if (!isTracked(name)) return;
    const strip = ensureStrip(bubble);
    if (!strip) return;
    const count = Number(strip.dataset.steps || "0") + 1;
    strip.dataset.steps = String(count);
    const title = strip.querySelector("[data-as-title]");
    if (title) title.textContent = humanLabel(name, args);
    const trail = strip.querySelector("[data-as-trail]");
    if (trail) {
      const pill = document.createElement("span");
      pill.className = "px-2 py-0.5 rounded-md bg-surface-900/90 border border-white/[0.07] text-[11px] text-zinc-300";
      pill.textContent = `${count}. ${shortPill(name)}`;
      trail.appendChild(pill);
    }
  }

  function onToolResult(bubble, name, summary, data) {
    if (!isTracked(name)) return;
    const strip = bubble && bubble.wrapper && bubble.wrapper.querySelector(".xod-action-strip");
    if (!strip) return;
    if (String(name || "").startsWith("browser_") || (data && data.screenshot_uri)) {
      refreshThumb(strip, data);
    }
  }

  function onDone(bubble) {
    const strip = bubble && bubble.wrapper && bubble.wrapper.querySelector(".xod-action-strip");
    if (!strip) return;
    const steps = Number(strip.dataset.steps || "0");
    const dot = strip.querySelector("[data-as-dot]");
    const badge = strip.querySelector("[data-as-badge]");
    const stopBtn = strip.querySelector("[data-as-stop]");
    if (dot) dot.className = "size-2 rounded-full bg-brand-400 shrink-0";
    if (badge) {
      badge.className = "text-[10px] uppercase tracking-wider font-semibold text-brand-300 shrink-0";
      badge.textContent = `Done · ${steps} ${steps === 1 ? "step" : "steps"}`;
    }
    if (stopBtn) stopBtn.classList.add("hidden");
  }

  window.XodActionStrip = { onToolCall, onToolResult, onDone };
})();
