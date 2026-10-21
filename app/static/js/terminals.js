// Terminals (backend: services/agent_clis.py, /api/terminals): every terminal session Xod runs, agents (Muse,
// Claude Code, Codex …) and programs, side by side. Pick one to see its screen live, type into it, press keys, stop
// it, or start another. Xod's chat uses the same sessions, so you can watch what it's doing and step in.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const DOT = { working: "bg-emerald-400 animate-pulse", idle: "bg-zinc-400", waiting: "bg-amber-400 animate-pulse", finished: "bg-zinc-700" };
  const WORD = { working: "working", idle: "idle", waiting: "waiting for you", finished: "finished" };
  let root = null, current = null, timer = null, agents = [];

  async function json(url, opts) {
    try { return await (await fetch(url, opts)).json(); } catch (e) { return { success: false, error: "Xod didn't answer." }; }
  }
  const post = (url, body, method = "POST") => json(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

  async function refresh() {
    if (!root || root.classList.contains("hidden")) return;
    const d = await json("/api/terminals");
    agents = d.agents || [];
    const list = d.sessions || [];
    if (current && !list.some((s) => s.session === current || s.name === current)) current = null;
    if (!current && list.length) current = list[0].session;
    const html = list.length ? list.map((s) => `
      <button data-t-pick="${esc(s.session || "")}" class="w-full text-left px-3 py-2 rounded-lg ${s.session === current ? "bg-surface-700" : "hover:bg-surface-750"}">
        <div class="flex items-center gap-2"><span class="size-2 rounded-full shrink-0 ${DOT[s.state] || DOT.idle}"></span>
          <span class="text-white text-[13px] truncate">${esc(s.name || s.agent)}</span><span class="text-zinc-500 text-[11px] ml-auto">${esc(WORD[s.state] || "")}</span></div>
        <div class="text-zinc-500 text-[11px] truncate mt-0.5">${esc(s.last || s.command || "")}</div>
      </button>`).join("") : `<p class="text-zinc-500 text-[12px] px-3 py-2">No sessions yet. Start one below, or ask Xod ("use muse to …").</p>`;
    // Only redraw when something changed: redrawing under the pointer swallowed clicks on the list.
    const box = root.querySelector("[data-t-list]");
    if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
    const sel = root.querySelector("[data-t-agent]");
    if (sel && !sel.dataset.filled) {
      sel.innerHTML = agents.filter((a) => a.installed).map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join("")
        + `<option value="">Any command…</option>`;
      sel.dataset.filled = "1";
      sel.dispatchEvent(new Event("change"));
    }
    if (current) await screen();
    else root.querySelector("[data-t-screen]").textContent = "";
  }

  async function screen() {
    const d = await json(`/api/terminals/${encodeURIComponent(current)}`);
    const pre = root.querySelector("[data-t-screen]");
    if (!d.success) { pre.textContent = d.error || ""; return; }
    const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
    pre.textContent = d.screen || "";
    if (atBottom) pre.scrollTop = pre.scrollHeight;
    root.querySelector("[data-t-title]").textContent = `${d.name} · ${d.agent} · ${WORD[d.state] || ""} · ${d.folder}`;
    root.querySelector("[data-t-waiting]").classList.toggle("hidden", d.state !== "waiting");
  }

  function open() {
    if (!root) {
      root = document.createElement("div");
      root.id = "terminalsPanel";
      root.className = "fixed inset-0 z-[85] flex items-center justify-center p-4 bg-black/60";
      root.innerHTML = `
        <div class="w-full max-w-5xl h-[82vh] flex flex-col rounded-2xl bg-surface-850 border border-white/[0.08] shadow-2xl text-[13px] overflow-hidden">
          <div class="flex items-center justify-between px-4 py-3 border-b border-white/[0.06]">
            <div><h3 class="text-white font-semibold text-[15px]">Terminals</h3>
              <p class="text-zinc-500 text-[11px]">The agents and programs Xod runs for you. Xod's chat uses these same sessions.</p></div>
            <button data-t="close" class="p-1 rounded-lg text-zinc-500 hover:text-white" title="Close">✕</button>
          </div>
          <div class="flex flex-1 min-h-0">
            <div class="w-64 shrink-0 border-r border-white/[0.06] flex flex-col">
              <div data-t-list class="flex-1 overflow-y-auto p-2 space-y-1"></div>
              <div class="p-2 border-t border-white/[0.06] space-y-1.5">
                <select data-t-agent class="w-full bg-surface-700 text-zinc-200 rounded-md px-2 py-1"></select>
                <input data-t-command class="hidden w-full bg-surface-700 text-zinc-200 rounded-md px-2 py-1 font-mono text-[12px]" placeholder="npm run dev">
                <input data-t-folder class="w-full bg-surface-700 text-zinc-200 rounded-md px-2 py-1 text-[12px]" placeholder="Folder (default: home)">
                <button data-t="start" class="w-full px-2 py-1.5 rounded-md bg-brand-500 text-surface-900 font-semibold hover:bg-brand-400">Start</button>
                <p data-t-msg class="text-[11px] text-zinc-400"></p>
              </div>
            </div>
            <div class="flex-1 min-w-0 flex flex-col">
              <div class="px-3 py-2 border-b border-white/[0.06] flex items-center gap-2">
                <span data-t-title class="text-zinc-300 truncate flex-1"></span>
                <button data-t="stop" class="px-2 py-1 rounded-md text-red-300 bg-red-500/10 hover:bg-red-500/20 shrink-0">Stop</button>
              </div>
              <div data-t-waiting class="hidden px-3 py-1.5 bg-amber-500/10 text-amber-200 text-[12px]">It's asking you something: answer below (1, y, Enter …).</div>
              <pre data-t-screen class="flex-1 overflow-auto m-0 p-3 bg-black/40 text-zinc-200 font-mono text-[12px] leading-[1.35] whitespace-pre"></pre>
              <div class="p-2 border-t border-white/[0.06] flex gap-1.5 items-center">
                <input data-t-input class="flex-1 bg-surface-700 text-white rounded-md px-2 py-1.5 font-mono text-[12px]" placeholder="Type to it, Enter sends">
                ${["enter", "ctrl+c", "escape", "up", "y", "n", "1"].map((k) => `<button data-t-key="${k}" class="px-2 py-1 rounded-md bg-surface-700 text-zinc-300 hover:bg-surface-600 text-[11px]">${k}</button>`).join("")}
              </div>
            </div>
          </div>
        </div>`;
      document.body.appendChild(root);
      root.addEventListener("click", async (e) => {
        if (e.target === root || e.target.closest('[data-t="close"]')) { close(); return; }
        const pick = e.target.closest("[data-t-pick]");
        if (pick) { current = pick.dataset.tPick; root.querySelector("[data-t-list]").dataset.html = ""; refresh(); return; }
        const key = e.target.closest("[data-t-key]");
        if (key && current) { await post(`/api/terminals/${encodeURIComponent(current)}/input`, { key: key.dataset.tKey }); setTimeout(screen, 300); return; }
        if (e.target.closest('[data-t="stop"]') && current) {
          await json(`/api/terminals/${encodeURIComponent(current)}`, { method: "DELETE" });
          current = null; refresh(); return;
        }
        if (e.target.closest('[data-t="start"]')) {
          const agent = root.querySelector("[data-t-agent]").value;
          const command = root.querySelector("[data-t-command]").value.trim();
          const folder = root.querySelector("[data-t-folder]").value.trim();
          const msg = root.querySelector("[data-t-msg]");
          msg.textContent = "Starting…";
          const r = await post("/api/terminals", agent ? { agent, folder, new: true } : { command, folder });
          msg.textContent = r.success ? "" : (r.error || "Couldn't start it.");
          if (r.success) current = r.session;
          refresh();
        }
      });
      root.querySelector("[data-t-agent]").addEventListener("change", (e) => {
        root.querySelector("[data-t-command]").classList.toggle("hidden", !!e.target.value);
      });
      root.querySelector("[data-t-input]").addEventListener("keydown", async (e) => {
        if (e.key !== "Enter" || !current) return;
        const text = e.target.value;
        e.target.value = "";
        await post(`/api/terminals/${encodeURIComponent(current)}/input`, { text, enter: true });
        setTimeout(screen, 400);
      });
    }
    root.classList.remove("hidden");
    refresh();
    clearInterval(timer);
    timer = setInterval(refresh, 1200);
  }

  function close() {
    root.classList.add("hidden");
    clearInterval(timer);
  }

  window.openTerminals = open;
})();
