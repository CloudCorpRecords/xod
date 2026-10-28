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
      </section>`;
  }

  async function render() {
    const body = document.getElementById("xodsBody");
    if (!body) return;
    let r;
    try { r = await api("/api/xods"); } catch { body.innerHTML = '<p class="text-[12px] text-red-300">Xod didn\'t answer.</p>'; return; }
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
