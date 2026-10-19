// Add-on packs (services/packs.py): hides menu items of packs that are off, and the Add-ons panel.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let packs = [];

  function apply() {
    window.enabledPacks = new Set(packs.filter((p) => p.enabled).map((p) => p.id));
    document.querySelectorAll("[data-pack]").forEach((el) => {
      const on = window.enabledPacks.has(el.dataset.pack);
      el.classList.toggle("pack-off", !on);
    });
    const starters = document.getElementById("welcomeHeroStarters");
    if (starters && (typeof activeWorkingScope === "undefined" || !activeWorkingScope) && typeof welcomeStartersHtml === "function") {
      starters.innerHTML = welcomeStartersHtml();
      if (window.lucide) lucide.createIcons();
    }
  }

  async function load() {
    try { packs = (await (await fetch("/api/packs")).json()).packs || []; } catch (e) { packs = []; }
    apply();
  }

  function render() {
    const body = document.getElementById("packsBody");
    if (!body) return;
    body.innerHTML = packs.map((p) => `
      <label class="flex items-start gap-3 p-3.5 rounded-2xl border ${p.enabled ? "border-brand-500/40 bg-brand-500/[0.05]" : "border-white/[0.06]"} cursor-pointer">
        <div class="size-9 rounded-xl bg-surface-700 flex items-center justify-center shrink-0"><i data-lucide="${esc(p.icon)}" class="size-4 text-zinc-200"></i></div>
        <div class="flex-1 min-w-0">
          <p class="text-[14px] text-white">${esc(p.name)}</p>
          <p class="text-[12px] text-zinc-400">${esc(p.description)}${p.tools ? ` <span class="text-zinc-500">${p.tools} tools.</span>` : ""}</p>
        </div>
        <input type="checkbox" data-pack-toggle="${esc(p.id)}" ${p.enabled ? "checked" : ""} class="mt-1 accent-amber-400 size-4">
      </label>${p.id === "desktop-control" && p.enabled ? `<div data-desktop-extra class="-mt-1 px-3.5 pb-1"></div>` : ""}`).join("") + `<p class="text-[12px] text-zinc-500">Core abilities are always on: research, the browser, your files, memory, tasks, routines, reports, connected apps and voice.</p>`;
    if (window.lucide) lucide.createIcons();
    if (window.fillDesktopExtra) window.fillDesktopExtra();   // desktop.js: macOS permissions and the apps Xod may use
  }

  window.openPacksPanel = () => {
    let root = document.getElementById("packsPanel");
    if (!root) {
      root = document.createElement("div");
      root.id = "packsPanel";
      root.className = "tasks-panel";
      root.innerHTML = `
        <div class="tasks-scrim" data-close-packs></div>
        <aside class="tasks-drawer" role="dialog" aria-label="Add-ons">
          <header class="px-5 pt-5 pb-3 flex items-start justify-between gap-3 border-b border-white/[0.06]">
            <div><h3 class="font-bold text-lg text-white">Add-ons</h3>
              <p class="text-[12px] text-zinc-400 mt-0.5">Turn on the extra abilities you want. Off means hidden from Xod and the menus.</p></div>
            <button data-close-packs class="p-1 -mr-1 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700" title="Close"><i data-lucide="x" class="size-4"></i></button>
          </header>
          <div id="packsBody" class="flex-1 overflow-y-auto px-5 py-4 space-y-2.5"></div>
        </aside>`;
      document.body.appendChild(root);
      root.addEventListener("click", (e) => {
        if (e.target.closest("[data-close-packs]")) {
          root.classList.remove("is-open");
          setTimeout(() => root.classList.add("hidden"), 250);
        }
      });
      root.addEventListener("change", async (e) => {
        const t = e.target.closest("[data-pack-toggle]");
        if (!t) return;
        await fetch(`/api/packs/${t.dataset.packToggle}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: t.checked }) });
        await load();
        render();
      });
    }
    root.classList.remove("hidden");
    requestAnimationFrame(() => root.classList.add("is-open"));
    render();
  };

  document.addEventListener("DOMContentLoaded", load);
})();
