// Activity panel: everything Xod did, newest first, with Undo where possible (services/activity.py).
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let timer = null;

  function dayLabel(ts) {
    const d = new Date(ts * 1000), now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (d.getTime() >= start) return "Today";
    if (d.getTime() >= start - 86400000) return "Yesterday";
    return d.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
  }

  function originChip(o) {
    if (!o || o.startsWith("chat")) return "";
    const [kind] = o.split(":");
    const label = { task: "Task", routine: "Routine", mcp: "Other app" }[kind] || kind;
    return `<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-surface-700 text-zinc-400">${esc(label)}</span>`;
  }

  async function render() {
    const body = document.getElementById("activityBody");
    if (!body) return;
    const { items } = await (await fetch("/api/activity?limit=300")).json();
    if (!items.length) {
      body.innerHTML = `<p class="text-[13px] text-zinc-500 text-center py-10">Nothing yet. When Xod changes files, runs commands or browses for you, it shows up here.</p>`;
      return;
    }
    const today = items.filter((i) => dayLabel(i.ts) === "Today");
    const changed = today.filter((i) => i.undoable).length;
    let html = `<p class="text-[13px] text-zinc-300">Today Xod did <b class="text-white">${today.length}</b> thing${today.length === 1 ? "" : "s"}${changed ? `, ${changed} of them can be undone` : ""}.</p>`;
    let day = "";
    for (const it of items) {
      const d = dayLabel(it.ts);
      if (d !== day) { day = d; html += `<p class="text-[11px] uppercase tracking-wider text-zinc-500 font-semibold pt-4 pb-1">${esc(d)}</p>`; }
      const time = new Date(it.ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
      html += `
        <div class="flex items-start gap-3 py-2.5 border-b border-white/[0.04] ${it.undone_at ? "opacity-50" : ""}">
          <span class="text-[11px] text-zinc-500 w-14 shrink-0 pt-0.5">${esc(time)}</span>
          <div class="min-w-0 flex-1">
            <p class="text-[13px] ${it.ok ? "text-zinc-100" : "text-red-300"} flex items-center gap-2 flex-wrap">${esc(it.title)} ${originChip(it.origin)}</p>
            ${it.detail ? `<p class="text-[11px] text-zinc-500 font-mono truncate" title="${esc(it.detail)}">${esc(it.detail)}</p>` : ""}
            ${it.note && !it.undoable ? `<p class="text-[11px] text-zinc-500">${esc(it.note)}</p>` : ""}
          </div>
          ${it.undone_at ? `<span class="text-[11px] text-zinc-500 shrink-0">Undone</span>`
            : it.undoable ? `<button data-undo="${esc(it.id)}" class="shrink-0 px-2.5 py-1 rounded-lg text-[12px] text-zinc-200 bg-surface-700 hover:bg-surface-600 flex items-center gap-1"><i data-lucide="undo-2" class="size-3.5"></i>Undo</button>` : ""}
        </div>`;
    }
    body.innerHTML = html;
    if (window.lucide) lucide.createIcons();
  }

  async function undo(id, force = false) {
    const r = await (await fetch(`/api/activity/${id}/undo`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ force }) })).json();
    if (r.conflict) {
      if (confirm(r.detail)) return undo(id, true);
      return;
    }
    showToast(r.ok ? `Undone: ${r.detail}` : r.detail);
    render();
  }

  window.openActivityPanel = () => {
    let root = document.getElementById("activityPanel");
    if (!root) {
      root = document.createElement("div");
      root.id = "activityPanel";
      root.className = "tasks-panel";
      root.innerHTML = `
        <div class="tasks-scrim" data-close-activity></div>
        <aside class="tasks-drawer" role="dialog" aria-label="Activity">
          <header class="px-5 pt-5 pb-3 flex items-start justify-between gap-3 border-b border-white/[0.06]">
            <div>
              <h3 class="font-bold text-lg text-white">Activity</h3>
              <p class="text-[12px] text-zinc-400 mt-0.5">Everything Xod did on your Mac. File changes can be undone.</p>
            </div>
            <button data-close-activity class="p-1 -mr-1 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700" title="Close"><i data-lucide="x" class="size-4"></i></button>
          </header>
          <div id="activityBody" class="flex-1 overflow-y-auto px-5 py-4"></div>
        </aside>`;
      document.body.appendChild(root);
      root.addEventListener("click", (e) => {
        if (e.target.closest("[data-close-activity]")) return window.closeActivityPanel();
        const b = e.target.closest("[data-undo]");
        if (b) undo(b.dataset.undo);
      });
    }
    root.classList.remove("hidden");
    requestAnimationFrame(() => root.classList.add("is-open"));   // slide in (whimsy.css)
    render();
    clearInterval(timer);
    timer = setInterval(() => { if (root.classList.contains("is-open")) render(); }, 5000);
  };
  window.closeActivityPanel = () => {
    clearInterval(timer);
    const root = document.getElementById("activityPanel");
    if (!root) return;
    root.classList.remove("is-open");
    setTimeout(() => root.classList.add("hidden"), 250);
  };
})();
