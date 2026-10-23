// Abilities (backend: services/abilities.py, /api/abilities): what Xod can do with this Mac's own apps, one Allow each,
// like a phone's permission screen. Allow switches the This Mac connector on and has macOS ask its own question once.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const TINT = { messages: "bg-emerald-500", location: "bg-sky-500", calendar: "bg-rose-500", home: "bg-amber-500", reminders: "bg-indigo-500", contacts: "bg-zinc-500" };
  let root = null, busy = "", notes = {};

  const api = async (url, body) => {
    try {
      const r = await fetch(url, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      return await r.json();
    } catch (e) { return { success: false, error: "Xod didn't answer." }; }
  };

  function row(a) {
    const done = a.state === "allowed";
    const label = busy === a.id ? "Asking…" : done ? "Allowed" : a.state === "partly" ? "Finish" : a.state === "denied" ? "Fix" : "Allow";
    const note = notes[a.id] || a.note;
    return `<div class="flex items-start gap-3 py-3 border-b border-white/[0.05]">
        <span class="size-9 rounded-xl ${TINT[a.id] || "bg-zinc-600"} grid place-items-center shrink-0"><i data-lucide="${esc(a.icon)}" class="size-4 text-white"></i></span>
        <div class="flex-1 min-w-0"><div class="text-white">${esc(a.name)}</div><div class="text-zinc-400 text-[12px]">${esc(a.blurb)}</div>
          ${note ? `<div class="text-[11px] mt-0.5 ${a.state === "denied" ? "text-amber-300" : "text-zinc-500"}">${esc(note)}</div>` : ""}
          ${done ? `<div class="text-[11px] text-zinc-500 mt-0.5">Try: “${esc(a.try)}”</div>` : ""}</div>
        <button data-ab="${esc(a.id)}" ${done || busy ? "disabled" : ""} class="shrink-0 px-3 py-1.5 rounded-full text-[12px] font-semibold ${done ? "text-emerald-400" : "bg-surface-700 text-zinc-100 hover:bg-surface-600"} disabled:cursor-default">${done ? "✓ " : ""}${label}</button>
      </div>`;
  }

  async function refresh() {
    const d = await api("/api/abilities");
    const box = root.querySelector("[data-ab-list]");
    box.innerHTML = d.supported === false ? `<p class="text-zinc-400 text-[13px] py-4">${esc(d.why)}</p>` : (d.abilities || []).map(row).join("");
    if (window.lucide) window.lucide.createIcons();
  }

  function open() {
    if (!root) {
      root = document.createElement("div");
      root.id = "abilitiesPanel";
      root.className = "fixed inset-0 z-[85] flex items-center justify-center p-4 bg-black/60";
      root.innerHTML = `
        <div class="w-full max-w-md max-h-[86vh] flex flex-col rounded-3xl bg-surface-850 border border-white/[0.08] shadow-2xl text-[13px] overflow-hidden">
          <div class="flex items-start gap-3 px-5 pt-5 pb-2">
            <div class="flex-1"><h3 class="text-white font-semibold text-[16px]">Abilities</h3>
              <p class="text-zinc-500 text-[12px]">Let Xod use this Mac's own apps. macOS asks you once for each, and you can change it any time in System Settings. Sending a message or unlocking something always asks you first.</p></div>
            <button data-ab-close class="p-1 rounded-lg text-zinc-500 hover:text-white" title="Close">✕</button>
          </div>
          <div data-ab-list class="flex-1 overflow-y-auto px-5 pb-4"></div>
        </div>`;
      document.body.appendChild(root);
      root.addEventListener("click", async (e) => {
        if (e.target === root || e.target.closest("[data-ab-close]")) { root.classList.add("hidden"); return; }
        const b = e.target.closest("[data-ab]");
        if (!b || busy) return;
        busy = b.dataset.ab; refresh();
        const r = await api(`/api/abilities/${busy}/allow`, {});
        notes[busy] = r.message || r.error || "";
        busy = ""; refresh();
      });
    }
    root.classList.remove("hidden");
    refresh();
  }

  window.openAbilities = open;
})();
