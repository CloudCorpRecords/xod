// Family: the Family Link F0 read-only screen. Lists the machines in the family
// registry (services/family_registry.py, GET /api/family) with their capabilities.
// No actions in F0: joining, sync and task handoff arrive in later phases.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = async (url) => (await fetch(url)).json();
  let poll = null;

  const ago = (t) => {
    if (!t) return "not seen yet";
    const s = Math.round(Date.now() / 1000 - new Date(t).getTime() / 1000);
    if (isNaN(s)) return esc(t);
    return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
  };

  function memberRow(m) {
    const caps = (m.capabilities || []).map((c) =>
      `<span class="px-1.5 py-0.5 rounded-md bg-surface-700 text-zinc-300 text-[11px]">${esc(c)}</span>`).join("");
    return `
      <li class="task-card space-y-1.5">
        <div class="flex items-center gap-2">
          <span class="size-2 rounded-full bg-emerald-400"></span>
          <span class="text-white text-[13px] font-semibold">${esc(m.name)}</span>
          <span class="text-zinc-500 text-[11px]">${esc(m.platform)}</span>
        </div>
        <p class="text-[12px] text-zinc-400">${m.endpoint ? `Tailnet <span class="font-mono text-zinc-200">${esc(m.endpoint)}</span> · ` : ""}last seen ${ago(m.last_seen)}</p>
        ${caps ? `<div class="flex flex-wrap gap-1">${caps}</div>` : `<p class="text-[12px] text-zinc-500">No capabilities listed.</p>`}
        ${m.note ? `<p class="text-[12px] text-zinc-500">${esc(m.note)}</p>` : ""}
      </li>`;
  }

  function otherRow(d) {
    return `
      <li class="flex items-center gap-2 text-[13px]">
        <span class="size-2 rounded-full bg-zinc-600"></span>
        <span class="text-zinc-300">${esc(d.name)}</span>
        <span class="text-zinc-500 text-[11px]">${esc(d.platform)} · ${esc(d.status)}</span>
      </li>`;
  }

  function body(r) {
    if (!r || r.state === undefined) return `<p class="text-[12px] text-zinc-400">Could not load the family registry.</p>`;
    if (r.state === "missing") {
      return `
        <section class="space-y-3">
          <div class="task-card space-y-2">
            <p class="task-title">No family yet</p>
            <p class="text-[12px] text-zinc-400">${esc(r.message || "No family registry found yet.")}</p>
          </div>
        </section>`;
    }
    if (r.state === "invalid") {
      const problems = (r.problems || []).map((p) => `<li>${esc(p)}</li>`).join("");
      return `
        <section class="space-y-3">
          <div class="task-card space-y-2">
            <p class="task-title">The registry could not be read</p>
            <p class="text-[12px] text-zinc-400">${esc(r.message || "")}</p>
            ${problems ? `<ul class="text-[12px] text-amber-300 list-disc pl-5 space-y-1">${problems}</ul>` : ""}
          </div>
        </section>`;
    }
    const others = (r.devices || []).filter((d) => d.status !== "active");
    const problems = (r.problems || []).map((p) => `<li>${esc(p)}</li>`).join("");
    return `
      <section class="space-y-3">
        <div class="task-card space-y-1.5">
          <p class="task-title">Your machines, one family</p>
          <p class="text-[12px] text-zinc-400">This list comes from the family registry. In this first step the
            registry is unsigned, so treat it as a directory of your machines, not proof of identity.
            Joining, settings and task handoff arrive in later steps; this screen only reads.</p>
        </div>
        <p class="text-[12px] font-semibold text-zinc-300">Family members</p>
        ${(r.members || []).length ? `<ul class="space-y-2">${r.members.map(memberRow).join("")}</ul>`
          : `<p class="text-[12px] text-zinc-500">No active members in the registry yet.</p>`}
        ${others.length ? `<p class="text-[12px] font-semibold text-zinc-300">Not active</p><ul class="space-y-1.5">${others.map(otherRow).join("")}</ul>` : ""}
        ${problems ? `<div class="task-card space-y-1"><p class="text-[12px] font-semibold text-amber-300">Some records were skipped</p><ul class="text-[12px] text-zinc-400 list-disc pl-5 space-y-1">${problems}</ul></div>` : ""}
      </section>`;
  }

  async function render() {
    const el = document.getElementById("familyBody");
    if (!el) return;
    let r = null;
    try { r = await api("/api/family"); } catch (e) { /* honest empty render below */ }
    el.innerHTML = body(r);
  }

  window.openFamilyPanel = () => {
    let root = document.getElementById("familyPanel");
    if (!root) {
      root = document.createElement("div");
      root.id = "familyPanel";
      root.className = "tasks-panel";
      root.innerHTML = `
        <div class="tasks-scrim" data-close-family></div>
        <aside class="tasks-drawer" role="dialog" aria-label="Family">
          <header class="px-5 pt-5 pb-3 flex items-start justify-between gap-3 border-b border-white/[0.06]">
            <div><h3 class="font-bold text-lg text-white">Family</h3>
              <p class="text-[12px] text-zinc-400 mt-0.5">Your machines and what each one offers.</p></div>
            <button data-close-family class="p-1 -mr-1 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700" title="Close"><i data-lucide="x" class="size-4"></i></button>
          </header>
          <div id="familyBody" class="flex-1 overflow-y-auto px-5 py-4 space-y-4"></div>
        </aside>`;
      document.body.appendChild(root);
      root.addEventListener("click", (e) => { if (e.target.closest("[data-close-family]")) window.closeFamilyPanel(); });
      if (window.lucide) lucide.createIcons();
    }
    root.classList.remove("hidden");
    requestAnimationFrame(() => root.classList.add("is-open"));
    render();
    clearInterval(poll);
    poll = setInterval(render, 15000);
  };
  window.closeFamilyPanel = () => {
    const root = document.getElementById("familyPanel");
    if (!root) return;
    clearInterval(poll);
    root.classList.remove("is-open");
    setTimeout(() => root.classList.add("hidden"), 250);
  };
})();
