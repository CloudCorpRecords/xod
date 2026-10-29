// Your Xods: one-click sign-in with Tailscale, and the other Xods on the same account. Backend: services/xod_network.py.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = async (url, method = "GET", body) =>
    (await fetch(url, { method, headers: body !== undefined ? { "Content-Type": "application/json" } : {}, body: body !== undefined ? JSON.stringify(body) : undefined })).json();
  const btn = "px-3 py-1.5 rounded-lg text-[12px] font-semibold";
  const primary = `${btn} bg-brand-500 text-surface-900 hover:bg-brand-400`;
  const quiet = `${btn} font-medium text-zinc-300 hover:text-white hover:bg-surface-700`;
  let poll = null;

  function signInView(st) {
    const installed = st.installed !== false;
    return `
      <section class="space-y-3">
        <div class="task-card space-y-2">
          <p class="task-title">Sign in once, and your Xods find each other</p>
          <p class="text-[12px] text-zinc-400">Sign in with Google, Microsoft, GitHub or Apple through Tailscale (free). Every computer
            you sign in with the same account sees the others, privately and from anywhere. Xod keeps no account of its own.</p>
        </div>
        <button data-xn="signin" class="${primary}">${installed ? "Sign in" : "Get Tailscale"}</button>
        ${st.signing_in ? `<p class="text-[12px] text-zinc-400">Finish signing in on the page that opened; this updates by itself.</p>` : ""}
        <p id="xnMsg" class="text-[12px] text-zinc-400"></p>
      </section>`;
  }

  function listView(r) {
    const st = r.status || {};
    const rows = (r.xods || []).map((x) => `
      <li class="flex items-center gap-2 text-[13px]">
        <span class="size-2 rounded-full ${x.online ? "bg-emerald-400" : "bg-zinc-600"}"></span>
        <span class="text-white">${esc(x.name)}</span>
        <span class="text-zinc-500 text-[11px]">${esc(x.os)}${x.same_account ? "" : " · shared with you"}${x.online ? "" : " · offline"}</span>
      </li>`).join("");
    return `
      <section class="space-y-3">
        <div class="task-card space-y-1.5">
          <p class="task-title">Signed in${st.account ? ` as ${esc(st.account)}` : ""}</p>
          <p class="text-[12px] text-zinc-400">This computer is <span class="font-mono text-white">${esc(st.name)}</span>.
            Sign in with the same account on your other computers and they show up here.</p>
        </div>
        <p class="text-[12px] font-semibold text-zinc-300">Other Xods</p>
        ${rows ? `<ul class="space-y-1.5">${rows}</ul>` : `<p class="text-[12px] text-zinc-500">None yet. Xod looks again every few seconds while this is open.</p>`}
        ${r.others ? `<p class="text-[11px] text-zinc-500">${r.others} other device${r.others === 1 ? "" : "s"} on your tailnet without Xod.</p>` : ""}
        <label class="flex items-start gap-2 text-[12px] text-zinc-300">
          <input type="checkbox" data-xn="findable" ${r.findable ? "checked" : ""} class="mt-0.5">
          <span>Let my other Xods find this computer</span></label>
        ${memoryView(r)}
      </section>`;
  }

  // Shared memory (services/memory_sync.py): facts about you, shared only with computers you approved on both ends.
  function memoryView(r) {
    const m = r.memory || {};
    const ago = (t) => { if (!t) return "never"; const s = Math.round(Date.now() / 1000 - t); return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`; };
    const state = { paired: "sharing", waiting: "waiting for your OK on that computer", asked: "asked to share: approve the card here" };
    const peers = (m.peers || []).map((p) => `
      <li class="text-[12px] space-y-0.5">
        <div class="flex items-center gap-2"><span class="text-white">${esc(p.name || p.host)}</span>
          <span class="text-zinc-500">${esc(state[p.state] || p.state)}${p.state === "paired" ? ` · synced ${ago(p.last_sync)}` : ""}</span></div>
        ${p.error ? `<div class="text-amber-300">${esc(p.error)}</div>` : ""}
        <div class="flex gap-2"><button data-xn="mstop" data-key="${esc(p.sign_pub)}" class="${quiet} !px-2 !py-0.5">Stop sharing</button>
          <button data-xn="mremove" data-key="${esc(p.sign_pub)}" class="${quiet} !px-2 !py-0.5">Remove what came from it</button></div>
      </li>`).join("");
    const sharing = new Set((m.peers || []).map((p) => p.host));
    const offer = (r.xods || []).filter((x) => x.same_account && x.online && !sharing.has(x.host)).map((x) =>
      `<button data-xn="mshare" data-host="${esc(x.host)}" class="${quiet} !px-2 !py-0.5">Share with ${esc(x.name)}</button>`).join("");
    const log = (m.log || []).slice(0, 5).map((l) => `<li>${esc(l.text)} · ${ago(l.at)}</li>`).join("");
    return `
      <div class="task-card space-y-2 mt-2">
        <label class="flex items-start gap-2 text-[12px] text-zinc-300">
          <input type="checkbox" data-xn="memory" ${m.on ? "checked" : ""} class="mt-0.5">
          <span><span class="text-white font-semibold">Share what Xod knows about you with my other computers</span><br>
            Facts only (your name, work, preferences…), never chats or files. Each computer has to approve it.
            Forgetting a fact on one forgets it on all of them.</span></label>
        ${m.on ? `${peers ? `<ul class="space-y-2">${peers}</ul>` : ""}
          ${offer ? `<div class="flex flex-wrap gap-1">${offer}</div>` : (peers ? "" : `<p class="text-[11px] text-zinc-500">Turn this on on your other computer too, then share with it here.</p>`)}
          ${log ? `<ul class="text-[11px] text-zinc-500 space-y-0.5">${log}</ul>` : ""}` : ""}
        <p id="xnMemMsg" class="text-[12px] text-zinc-400"></p>
      </div>`;
  }

  async function render() {
    const body = document.getElementById("xodsBody");
    if (!body) return;
    let r;
    try { r = await api("/api/xods"); } catch { body.innerHTML = '<p class="text-[12px] text-red-300">Xod didn\'t answer.</p>'; return; }
    try { r.memory = await api("/api/memory-sync"); } catch { r.memory = null; }
    if (body.contains(document.activeElement) && document.activeElement.dataset.xn) return;   // don't redraw under a click
    const st = r.status || {};
    body.innerHTML = st.signed_in ? listView(r) : signInView(st);
  }

  async function onClick(e) {
    const el = e.target.closest("[data-xn]");
    if (e.target.closest("[data-close-xods]")) return window.closeXodsPanel();
    if (!el) return;
    const a = el.dataset.xn;
    if (a === "signin" && e.type === "click") {
      el.disabled = true;
      el.textContent = "Opening…";
      const r = await api("/api/xods/sign-in", "POST");
      const msg = document.getElementById("xnMsg");
      if (msg) msg.textContent = r.message || r.error || "";
      el.disabled = false;
      return render();
    }
    if (a === "findable" && e.type === "change") {
      await api("/api/xods/findable", "POST", { on: el.checked });
    }
    const mem = async (body) => {
      const r = await api("/api/memory-sync", "POST", body);
      await render();
      const msg = document.getElementById("xnMemMsg");
      if (msg) msg.textContent = r.error || r.message || (r.removed !== undefined ? `Removed ${r.removed} fact${r.removed === 1 ? "" : "s"}.` : "");
    };
    if (a === "memory" && e.type === "change") return mem({ on: el.checked });
    if (e.type !== "click") return;
    if (a === "mshare") { el.disabled = true; el.textContent = "Asking…"; return mem({ share: el.dataset.host }); }
    if (a === "mstop") return mem({ stop: el.dataset.key });
    if (a === "mremove") return mem({ remove: el.dataset.key });
  }

  window.openXodsPanel = () => {
    let root = document.getElementById("xodsPanel");
    if (!root) {
      root = document.createElement("div");
      root.id = "xodsPanel";
      root.className = "tasks-panel";
      root.innerHTML = `
        <div class="tasks-scrim" data-close-xods></div>
        <aside class="tasks-drawer" role="dialog" aria-label="Your Xods">
          <header class="px-5 pt-5 pb-3 flex items-start justify-between gap-3 border-b border-white/[0.06]">
            <div><h3 class="font-bold text-lg text-white">Your Xods</h3>
              <p class="text-[12px] text-zinc-400 mt-0.5">Every computer you've signed in, working together.</p></div>
            <button data-close-xods class="p-1 -mr-1 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700" title="Close"><i data-lucide="x" class="size-4"></i></button>
          </header>
          <div id="xodsBody" class="flex-1 overflow-y-auto px-5 py-4 space-y-4"></div>
        </aside>`;
      document.body.appendChild(root);
      root.addEventListener("click", onClick);
      root.addEventListener("change", onClick);
      if (window.lucide) lucide.createIcons();
    }
    root.classList.remove("hidden");
    requestAnimationFrame(() => root.classList.add("is-open"));
    render();
    clearInterval(poll);
    poll = setInterval(render, 5000);
  };
  window.closeXodsPanel = () => {
    const root = document.getElementById("xodsPanel");
    if (!root) return;
    clearInterval(poll);
    root.classList.remove("is-open");
    setTimeout(() => root.classList.add("hidden"), 250);
  };
})();
