// MCP: outside tool servers Xod can use, approvals for their actions, and how to use Xod from other apps.
// Backend: services/mcp_client.py (client), app/mcp_server.py (Xod as a server).
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = async (url, method = "GET", body) =>
    (await fetch(url, { method, headers: body !== undefined ? { "Content-Type": "application/json" } : {}, body: body !== undefined ? JSON.stringify(body) : undefined })).json();
  const TRUST = { ask: "Ask before every action", read_only: "Read-only actions run; others ask", always: "Run everything (trusted)" };

  // ---------------------------------------------------------------- approval card in chat
  window.renderMcpApproval = (data) => {
    const box = document.getElementById("chatMessages");
    if (!box || !data?.approval_id || document.querySelector(`[data-mcp-approval="${data.approval_id}"]`)) return;
    const [, server, tool] = String(data.tool || data.message || "").split("__");
    const card = document.createElement("div");
    card.className = "max-w-4xl mx-auto w-full";
    card.dataset.mcpApproval = data.approval_id;
    card.innerHTML = `
      <div class="ml-12 p-4 rounded-2xl border border-amber-500/40 bg-amber-500/[0.06] space-y-2">
        <p class="text-[13px] text-amber-200 font-medium flex items-center gap-2"><i data-lucide="shield-question" class="size-4"></i>${esc(data.message || "Xod wants to use an outside tool")}</p>
        <pre class="text-[11px] text-zinc-300 bg-surface-900/70 rounded-lg p-2.5 overflow-x-auto max-h-48">${esc(JSON.stringify(data.args || {}, null, 2))}</pre>
        <div class="flex gap-2">
          <button data-mcp="approve" class="px-3 py-1.5 rounded-lg bg-brand-500 text-surface-900 text-[12px] font-semibold hover:bg-brand-400">Approve and run</button>
          <button data-mcp="decline" class="px-3 py-1.5 rounded-lg text-[12px] text-zinc-300 hover:text-white hover:bg-surface-700">Decline</button>
        </div>
        <div data-mcp-result class="text-[12px] text-zinc-300"></div>
      </div>`;
    box.appendChild(card);
    if (window.lucide) lucide.createIcons();
    card.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-mcp]");
      if (!b) return;
      card.querySelectorAll("[data-mcp]").forEach((x) => (x.disabled = true));
      const out = card.querySelector("[data-mcp-result]");
      if (b.dataset.mcp === "decline") {
        await api(`/api/mcp/decline/${data.approval_id}`, "POST");
        out.textContent = "Declined. Nothing was run.";
        return;
      }
      out.textContent = "Running…";
      const r = await api(`/api/mcp/approve/${data.approval_id}`, "POST");
      out.innerHTML = r.success
        ? `<span class="text-emerald-300">Done.</span><pre class="mt-1 text-[11px] whitespace-pre-wrap max-h-60 overflow-y-auto">${esc((r.content || "").slice(0, 4000))}</pre>`
        : `<span class="text-red-300">${esc(r.error || "It didn't work.")}</span>`;
    });
  };

  // ---------------------------------------------------------------- panel
  async function render() {
    const body = document.getElementById("mcpBody");
    if (!body) return;
    const [{ servers, pending }, cfg] = await Promise.all([api("/api/mcp"), api("/api/mcp/self-config")]);
    body.innerHTML = `
      ${pending.length ? `<section class="space-y-2"><h4 class="text-[13px] font-semibold text-amber-200">Waiting for your OK</h4>
        ${pending.map((p) => `<div class="task-card space-y-1.5"><p class="text-[13px] text-white">${esc(p.tool_name)} <span class="text-zinc-500">on ${esc(p.server)}</span></p>
          <pre class="text-[11px] text-zinc-400 overflow-x-auto">${esc(JSON.stringify(p.args, null, 1)).slice(0, 600)}</pre>
          <div class="flex gap-2"><button class="task-btn" data-mcpp="approve" data-id="${esc(p.id)}">Approve</button><button class="task-btn task-btn-quiet" data-mcpp="decline" data-id="${esc(p.id)}">Decline</button></div></div>`).join("")}</section>` : ""}

      <section class="space-y-2">
        <h4 class="text-[13px] font-semibold text-white">Tool servers Xod can use</h4>
        ${servers.length ? servers.map((s) => `
          <div class="task-card space-y-2" data-server="${esc(s.name)}">
            <div class="flex items-center justify-between gap-2">
              <p class="task-title flex items-center gap-2"><span class="size-2 rounded-full ${s.connected ? "bg-emerald-400" : s.enabled ? "bg-red-400" : "bg-zinc-500"}"></span>${esc(s.name)}</p>
              <label class="text-[11px] text-zinc-400 flex items-center gap-1.5"><input type="checkbox" data-mcps="enabled" ${s.enabled ? "checked" : ""} class="accent-amber-400"> On</label>
            </div>
            <p class="text-[11px] text-zinc-500 font-mono break-all">${esc(s.command)}</p>
            ${s.error ? `<p class="text-[12px] text-red-300">${esc(s.error)}</p>` : ""}
            ${s.secret_env.length ? `<p class="text-[11px] text-zinc-500">Secrets in Keychain: ${esc(s.secret_env.join(", "))}</p>` : ""}
            <select data-mcps="trust" class="w-full bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-[12px] text-white">
              ${Object.entries(TRUST).map(([k, v]) => `<option value="${k}" ${s.trust === k ? "selected" : ""}>${v}</option>`).join("")}
            </select>
            ${s.tools.length ? `<details><summary class="text-[12px] text-zinc-400 cursor-pointer">${s.tools.length} tools</summary>
              <ul class="mt-1.5 space-y-1">${s.tools.map((t) => `<li class="text-[12px] text-zinc-300"><span class="font-mono">${esc(t.name)}</span>${t.read_only ? ` <span class="text-[10px] text-emerald-300">read-only</span>` : ""}<br><span class="text-zinc-500">${esc(t.description)}</span></li>`).join("")}</ul></details>` : ""}
            <button class="task-btn task-btn-quiet" data-mcps="remove">Remove</button>
          </div>`).join("") : `<p class="text-[12px] text-zinc-500">None yet. MCP servers give Xod new abilities: email, calendars, Notion, GitHub, databases and more. Find them at modelcontextprotocol.io.</p>`}
        <details class="pt-1" ${servers.length ? "" : "open"}>
          <summary class="text-[13px] text-brand-300 cursor-pointer">Add a server</summary>
          <p class="text-[12px] text-zinc-400 mt-2">Paste the server's config, in the same format as Claude Desktop's <code>mcpServers</code>. API keys in it go into your Keychain, not a file.</p>
          <textarea id="mcpAddJson" rows="7" spellcheck="false" class="mt-2 w-full bg-surface-900 border border-surface-700 rounded-lg px-3 py-2 text-[12px] font-mono text-white focus:outline-none focus:border-brand-500" placeholder='{"mcpServers": {"filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/you/Documents"]}}}'></textarea>
          <button data-mcpa="add" class="mt-2 px-3 py-1.5 rounded-lg bg-brand-500 text-surface-900 text-[12px] font-semibold hover:bg-brand-400">Add and connect</button>
        </details>
      </section>

      <section class="space-y-2 pt-2 border-t border-white/[0.06]">
        <h4 class="text-[13px] font-semibold text-white pt-3">Use Xod from other apps</h4>
        <p class="text-[12px] text-zinc-400">Claude Desktop, Cursor, Claude Code and other MCP apps can ask Xod to research, start tasks, make images or run your routines. Xod must be running; its approval rules still apply. Add this to the app's MCP settings:</p>
        <pre id="mcpSelfCfg" class="text-[11px] text-zinc-200 bg-surface-900 rounded-lg p-3 overflow-x-auto">${esc(JSON.stringify(cfg.config, null, 2))}</pre>
        <button data-mcpa="copy" class="px-3 py-1.5 rounded-lg text-[12px] text-zinc-200 bg-surface-700 hover:bg-surface-600">Copy</button>
      </section>`;
    if (window.lucide) lucide.createIcons();
  }

  async function onClick(e) {
    const el = e.target.closest("[data-mcpp],[data-mcps],[data-mcpa],[data-close-mcp]");
    if (!el) return;
    if (el.matches("[data-close-mcp]")) return window.closeMcpPanel();
    if (el.dataset.mcpp && e.type === "click") {
      const r = await api(`/api/mcp/${el.dataset.mcpp}/${el.dataset.id}`, "POST");
      showToast(el.dataset.mcpp === "approve" ? (r.success ? "Done" : r.error || "Didn't work") : "Declined");
      return render();
    }
    const name = el.closest("[data-server]")?.dataset.server;
    if (el.dataset.mcps === "enabled" && e.type === "change") { await api(`/api/mcp/servers/${name}`, "PATCH", { enabled: el.checked }); return render(); }
    if (el.dataset.mcps === "trust" && e.type === "change") { await api(`/api/mcp/servers/${name}`, "PATCH", { trust: el.value }); showToast("Saved"); return; }
    if (el.dataset.mcps === "remove" && e.type === "click" && confirm(`Remove ${name}? Its Keychain secrets are deleted too.`)) {
      await api(`/api/mcp/servers/${name}`, "DELETE"); return render();
    }
    if (el.dataset.mcpa === "add" && e.type === "click") {
      let block;
      try { block = JSON.parse(document.getElementById("mcpAddJson").value); } catch (err) { return showToast("That isn't valid JSON."); }
      el.textContent = "Connecting…"; el.disabled = true;
      const r = await api("/api/mcp/servers", "POST", block);
      showToast(r.success ? `Added ${r.added.join(", ")}` : r.error);
      return render();
    }
    if (el.dataset.mcpa === "copy" && e.type === "click") {
      await navigator.clipboard.writeText(document.getElementById("mcpSelfCfg").textContent);
      showToast("Copied");
    }
  }

  window.openMcpPanel = () => {
    let root = document.getElementById("mcpPanel");
    if (!root) {
      root = document.createElement("div");
      root.id = "mcpPanel";
      root.className = "tasks-panel";
      root.innerHTML = `
        <div class="tasks-scrim" data-close-mcp></div>
        <aside class="tasks-drawer" role="dialog" aria-label="Connected apps">
          <header class="px-5 pt-5 pb-3 flex items-start justify-between gap-3 border-b border-white/[0.06]">
            <div><h3 class="font-bold text-lg text-white">Connected apps (MCP)</h3>
              <p class="text-[12px] text-zinc-400 mt-0.5">Give Xod outside tools, and let other AI apps use Xod.</p></div>
            <button data-close-mcp class="p-1 -mr-1 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700" title="Close"><i data-lucide="x" class="size-4"></i></button>
          </header>
          <div id="mcpBody" class="flex-1 overflow-y-auto px-5 py-4 space-y-5"></div>
        </aside>`;
      document.body.appendChild(root);
      root.addEventListener("click", onClick);
      root.addEventListener("change", onClick);
    }
    root.classList.remove("hidden");
    requestAnimationFrame(() => root.classList.add("is-open"));
    render();
  };
  window.closeMcpPanel = () => {
    const root = document.getElementById("mcpPanel");
    if (!root) return;
    root.classList.remove("is-open");
    setTimeout(() => root.classList.add("hidden"), 250);
  };
})();
