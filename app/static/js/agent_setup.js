// Agent setup (backend: services/agent_clis.py, /api/agents/{id}/setup): installs a terminal agent (Claude Code, Muse,
// Codex …) with its own installer, signs it in (its page opens in the browser; a code it shows can be pasted here),
// checks it answers, and looks after it later (update, health check). Each button is the person's own OK.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const FIRST = ["claude", "muse", "codex", "gemini"];
  const MARK = { done: ["✓", "text-emerald-400"], running: ["…", "text-sky-300 animate-pulse"], todo: ["○", "text-zinc-400"],
                 failed: ["!", "text-red-400"], blocked: ["○", "text-zinc-600"] };
  const ACT = { install: "Install", login: "Sign in", test: "Check it" };
  let root = null, current = "claude", timer = null, busy = "", note = "", report = "";

  const api = async (url, body) => {
    try {
      const r = await fetch(url, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      return await r.json();
    } catch (e) { return { success: false, error: "Xod didn't answer." }; }
  };
  const setHtml = (el, html) => { if (el && el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; } };  // no redraw under a click

  function agentList(agents) {
    const order = [...agents].sort((a, b) => ((FIRST.indexOf(a.id) + 1 || 99) - (FIRST.indexOf(b.id) + 1 || 99)));
    return order.map((a) => `
      <button data-s-pick="${esc(a.id)}" class="w-full text-left px-3 py-2 rounded-lg ${a.id === current ? "bg-surface-700" : "hover:bg-surface-750"}">
        <div class="flex items-center gap-2"><span class="text-white text-[13px] truncate">${esc(a.name)}</span>
          <span class="ml-auto text-[11px] ${a.installed && a.signed_in ? "text-emerald-400" : a.installed ? "text-amber-300" : "text-zinc-500"}">
            ${a.installed ? (a.signed_in ? "ready" : a.signed_in === false ? "sign in" : "installed") : "not installed"}</span></div>
      </button>`).join("");
  }

  function detail(d) {
    if (!d || d.success === false) return `<p class="text-red-300">${esc((d && d.error) || "Couldn't read it.")}</p>`;
    const acct = d.claude_app && d.claude_app.account;
    const steps = d.steps.map((s) => {
      const [m, cls] = MARK[s.state] || MARK.todo;
      const wait = s.id === "login" && s.state === "running" ? `
        <div class="mt-2 space-y-1.5">
          ${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener" class="text-sky-300 hover:underline text-[12px]">Open the sign-in page again</a>` : ""}
          ${s.code ? `<div class="text-[12px] text-zinc-300">The page should show the code <span class="font-mono text-white">${esc(s.code)}</span></div>` : ""}
          <div class="flex gap-1.5"><input data-s-code class="flex-1 bg-surface-700 text-white rounded-md px-2 py-1 font-mono text-[12px]" placeholder="If the page shows a code, paste it here">
            <button data-s-do="code" class="px-2 py-1 rounded-md bg-surface-600 text-zinc-200 hover:bg-surface-500 text-[12px]">Enter</button></div>
        </div>` : "";
      return `<div class="flex gap-3 py-2.5 border-b border-white/[0.05]">
          <span class="w-4 text-center ${cls}">${m}</span>
          <div class="flex-1 min-w-0"><div class="text-white">${esc(s.label)}</div>
            <div class="text-zinc-400 text-[12px] break-words">${esc(s.detail)}</div>${wait}</div>
          ${d.next === s.id && s.state !== "running" ? `<button data-s-do="${s.id}" ${busy ? "disabled" : ""} class="self-start shrink-0 px-3 py-1.5 rounded-md bg-brand-500 text-surface-900 font-semibold hover:bg-brand-400 disabled:opacity-50">${busy === s.id ? "Working…" : ACT[s.id]}</button>` : ""}
        </div>`;
    }).join("");
    const console_ = d.agent === "claude" && d.next === "login" && d.steps[1].state !== "running" ? `<button data-s-do="login-console" ${busy ? "disabled" : ""} class="text-[11px] text-zinc-400 hover:text-white underline">Sign in with an Anthropic Console account instead (API billing)</button>` : "";
    return `
      <div class="flex items-center gap-2 mb-1"><h4 class="text-white text-[15px] font-semibold">${esc(d.name)}</h4>
        ${d.version ? `<span class="text-zinc-500 text-[12px]">${esc(d.version)}</span>` : ""}
        <span class="ml-auto text-[12px] ${d.ready ? "text-emerald-400" : "text-zinc-400"}">${esc(d.message)}</span></div>
      ${d.agent === "claude" ? `<p class="text-zinc-500 text-[12px] mb-2">${acct ? `Claude app on this computer: signed in as ${esc(acct.email)}${acct.organization ? ` (${esc(acct.organization)})` : ""}.` : d.claude_app && d.claude_app.installed ? "The Claude app is installed." : ""}</p>` : ""}
      <div>${steps}</div>
      <div class="mt-2">${console_}</div>
      ${d.installed ? `<div class="flex gap-2 mt-4">
        ${d.can_update ? `<button data-s-do="update" ${busy ? "disabled" : ""} class="px-2.5 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600 text-[12px]">${busy === "update" ? "Updating…" : "Update"}</button>` : ""}
        ${d.can_doctor ? `<button data-s-do="doctor" ${busy ? "disabled" : ""} class="px-2.5 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600 text-[12px]">${busy === "doctor" ? "Checking…" : "Health check"}</button>` : ""}
        ${d.ready ? `<button data-s-do="test" ${busy ? "disabled" : ""} class="px-2.5 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600 text-[12px]">${busy === "test" ? "Asking…" : "Check again"}</button>` : ""}
      </div>` : ""}
      ${note ? `<p class="mt-3 text-[12px] text-zinc-300 whitespace-pre-wrap">${esc(note)}</p>` : ""}
      ${report ? `<pre class="mt-2 p-2 rounded-md bg-black/40 text-zinc-300 font-mono text-[11px] whitespace-pre-wrap max-h-64 overflow-auto">${esc(report)}</pre>` : ""}`;
  }

  async function refresh() {
    if (!root || root.classList.contains("hidden")) return;
    const [t, d] = await Promise.all([api("/api/terminals"), api(`/api/agents/${encodeURIComponent(current)}/setup`)]);
    setHtml(root.querySelector("[data-s-list]"), agentList(t.agents || []));
    setHtml(root.querySelector("[data-s-detail]"), detail(d));
  }

  async function act(what) {
    if (busy) return;
    const step = what === "login-console" ? "login" : what;
    const body = { method: what === "login-console" ? "console" : "" };
    if (what === "code") body.code = (root.querySelector("[data-s-code]") || {}).value || "";
    busy = step; note = ""; report = ""; refresh();
    const r = await api(`/api/agents/${encodeURIComponent(current)}/setup/${step}`, body);
    busy = "";
    note = r.message || r.note || r.next || r.error || (r.success ? "Done." : "That didn't work.");
    if (step === "doctor") report = r.report || "";
    else if (!r.success && r.output) report = r.output;
    refresh();
  }

  function open(agent) {
    if (typeof agent === "string" && agent) current = agent;
    if (!root) {
      root = document.createElement("div");
      root.id = "agentSetupPanel";
      root.className = "fixed inset-0 z-[85] flex items-center justify-center p-4 bg-black/60";
      root.innerHTML = `
        <div class="w-full max-w-3xl h-[70vh] flex flex-col rounded-2xl bg-surface-850 border border-white/[0.08] shadow-2xl text-[13px] overflow-hidden">
          <div class="flex items-center justify-between px-4 py-3 border-b border-white/[0.06]">
            <div><h3 class="text-white font-semibold text-[15px]">Agent setup</h3>
              <p class="text-zinc-500 text-[11px]">Xod installs coding agents, signs them in and keeps them up to date. You can also just ask in chat ("set up Claude Code").</p></div>
            <button data-s="close" class="p-1 rounded-lg text-zinc-500 hover:text-white" title="Close">✕</button>
          </div>
          <div class="flex flex-1 min-h-0">
            <div data-s-list class="w-52 shrink-0 border-r border-white/[0.06] overflow-y-auto p-2 space-y-1"></div>
            <div data-s-detail class="flex-1 min-w-0 overflow-y-auto p-4"></div>
          </div>
        </div>`;
      document.body.appendChild(root);
      root.addEventListener("click", (e) => {
        if (e.target === root || e.target.closest('[data-s="close"]')) { close(); return; }
        const pick = e.target.closest("[data-s-pick]");
        if (pick) { current = pick.dataset.sPick; note = ""; report = ""; refresh(); return; }
        const d = e.target.closest("[data-s-do]");
        if (d) act(d.dataset.sDo);
      });
      root.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.matches("[data-s-code]")) act("code"); });
    }
    root.classList.remove("hidden");
    refresh();
    clearInterval(timer);
    timer = setInterval(refresh, 3000);
  }

  function close() {
    root.classList.add("hidden");
    clearInterval(timer);
  }

  window.openAgentSetup = open;
})();
