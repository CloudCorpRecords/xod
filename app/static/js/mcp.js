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
    const [{ servers: all, pending }, cfg, { connectors }] = await Promise.all([api("/api/mcp"), api("/api/mcp/self-config"), api("/api/connectors")]);
    const servers = all.filter((s) => !s.connector);          // connectors are shown as cards above
    const byName = Object.fromEntries(all.map((s) => [s.name, s]));
    const main = connectors.filter((c) => !c.advanced), extra = connectors.filter((c) => c.advanced);
    // Grouped (Work and tasks, Design, Money, Developers…); a group opens by itself when one of its apps is on.
    const groups = [...new Set(main.map((c) => c.group || "Other"))];
    const groupHtml = (g, i) => {
      const cs = main.filter((c) => (c.group || "Other") === g);
      const anyOn = cs.some((c) => c.state !== "off");
      return `<details class="pt-1" ${i === 0 || anyOn ? "open" : ""}><summary class="text-[12px] text-zinc-300 cursor-pointer font-semibold">${esc(g)}
        <span class="text-zinc-500 font-normal">${cs.length}${anyOn ? ` · ${cs.filter((c) => c.state === "connected").length} on` : ""}</span></summary>
        <div class="space-y-2 mt-2">${cs.map((c) => connectorCard(c, byName[c.id])).join("")}</div></details>`;
    };
    body.innerHTML = `
      ${pending.length ? `<section class="space-y-2"><h4 class="text-[13px] font-semibold text-amber-200">Waiting for your OK</h4>
        ${pending.map((p) => `<div class="task-card space-y-1.5"><p class="text-[13px] text-white">${esc(p.tool_name)} <span class="text-zinc-500">on ${esc(p.server)}</span></p>
          <pre class="text-[11px] text-zinc-400 overflow-x-auto">${esc(JSON.stringify(p.args, null, 1)).slice(0, 600)}</pre>
          <div class="flex gap-2"><button class="task-btn" data-mcpp="approve" data-id="${esc(p.id)}">Approve</button><button class="task-btn task-btn-quiet" data-mcpp="decline" data-id="${esc(p.id)}">Decline</button></div></div>`).join("")}</section>` : ""}

      <section class="space-y-2" id="mcpImport">
        <h4 class="text-[13px] font-semibold text-white">Add any MCP server</h4>
        <p class="text-[12px] text-zinc-400">Paste whatever the server's page says to add: a JSON block (Claude, Cursor, VS Code, Windsurf…), an install command (<span class="font-mono">claude mcp add …</span>, <span class="font-mono">npx …</span>), an install link or just its address. Xod works out the rest and tries it.</p>
        <textarea id="mcpImportText" rows="4" spellcheck="false" class="w-full bg-surface-900 border border-surface-700 rounded-lg px-3 py-2 text-[12px] font-mono text-white focus:outline-none focus:border-brand-500" placeholder="https://mcp.example.com/mcp"></textarea>
        <button data-mcpi="check" class="px-3 py-1.5 rounded-lg bg-brand-500 text-surface-900 text-[12px] font-semibold hover:bg-brand-400">Check it</button>
        <div id="mcpImportResult" class="space-y-2"></div>
      </section>

      <section class="space-y-2 pt-2 border-t border-white/[0.06]">
        <h4 class="text-[13px] font-semibold text-white pt-3">Apps</h4>
        <p class="text-[12px] text-zinc-400">Connect once; Xod uses them when a request needs them. Reading is automatic, anything that changes something asks you first.</p>
        ${groups.map(groupHtml).join("")}
        ${extra.length ? `<details class="pt-1"><summary class="text-[12px] text-zinc-400 cursor-pointer">More (advanced)</summary>
          <div class="space-y-2 mt-2">${extra.map((c) => connectorCard(c, byName[c.id])).join("")}</div></details>` : ""}
      </section>

      <section class="space-y-2 pt-2 border-t border-white/[0.06]">
        <h4 class="text-[13px] font-semibold text-white pt-3">Other tool servers</h4>
        ${servers.length ? servers.map((s) => `
          <div class="task-card space-y-2" data-server="${esc(s.name)}">
            <div class="flex items-center justify-between gap-2">
              <p class="task-title flex items-center gap-2"><span class="size-2 rounded-full ${s.connected ? "bg-emerald-400" : s.enabled ? "bg-red-400" : "bg-zinc-500"}"></span>${esc(s.name)}</p>
              <label class="text-[11px] text-zinc-400 flex items-center gap-1.5"><input type="checkbox" data-mcps="enabled" ${s.enabled ? "checked" : ""} class="accent-amber-400"> On</label>
            </div>
            <p class="text-[11px] text-zinc-500 font-mono break-all">${esc(s.command)}</p>
            ${s.signing_in ? `<p class="text-[12px] text-amber-200">Finish signing in in your browser…</p>` : ""}
            ${s.needs_sign_in && !s.signing_in ? `<p class="text-[12px] text-amber-200">Sign in needed. <button class="underline" data-mcps="signin">Sign in</button></p>` : ""}
            ${s.error ? `<p class="text-[12px] text-red-300">${esc(s.error)} <button class="underline text-zinc-300" data-mcps="retry">Try again</button></p>` : ""}
            ${s.secret_env.length ? `<p class="text-[11px] text-zinc-500">Secrets in Keychain: ${esc(s.secret_env.join(", "))}</p>` : ""}
            <select data-mcps="trust" class="w-full bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-[12px] text-white">
              ${Object.entries(TRUST).map(([k, v]) => `<option value="${k}" ${s.trust === k ? "selected" : ""}>${v}</option>`).join("")}
            </select>
            ${s.tools.length ? `<details><summary class="text-[12px] text-zinc-400 cursor-pointer">${s.tools.length} tools</summary>
              <ul class="mt-1.5 space-y-1">${s.tools.map((t) => `<li class="text-[12px] text-zinc-300"><span class="font-mono">${esc(t.name)}</span>${t.read_only ? ` <span class="text-[10px] text-emerald-300">read-only</span>` : ""}<br><span class="text-zinc-500">${esc(t.description)}</span></li>`).join("")}</ul></details>` : ""}
            <button class="task-btn task-btn-quiet" data-mcps="remove">Remove</button>
          </div>`).join("") : `<p class="text-[12px] text-zinc-500">Any other MCP server works too: databases, Slack, Figma and more. Find them at modelcontextprotocol.io.</p>`}
      </section>

      <section class="space-y-2 pt-2 border-t border-white/[0.06]">
        <h4 class="text-[13px] font-semibold text-white pt-3">Use Xod from other apps</h4>
        <p class="text-[12px] text-zinc-400">Claude Desktop, Cursor, Claude Code and other MCP apps can ask Xod to research, start tasks, make images or run your routines. Xod must be running; its approval rules still apply. Add this to the app's MCP settings:</p>
        <pre id="mcpSelfCfg" class="text-[11px] text-zinc-200 bg-surface-900 rounded-lg p-3 overflow-x-auto">${esc(JSON.stringify(cfg.config, null, 2))}</pre>
        <button data-mcpa="copy" class="px-3 py-1.5 rounded-lg text-[12px] text-zinc-200 bg-surface-700 hover:bg-surface-600">Copy</button>
      </section>`;
    if (lastImport) {                      // what was pasted and how it went survive a refresh
      document.getElementById("mcpImportText").value = importText;
      renderImport(lastImport.res, lastImport.results);
    }
    if (window.lucide) lucide.createIcons();
    clearTimeout(pollTimer);
    if ((connectors.some((c) => c.state === "connecting" || c.state === "signing_in") || servers.some((s) => s.signing_in))
        && document.getElementById("mcpPanel")?.classList.contains("is-open")) {
      pollTimer = setTimeout(render, 2000);
    }
  }
  let pollTimer = null;

  const STATE = {
    connected: ["bg-emerald-400", "Connected"], signing_in: ["bg-amber-400 animate-pulse", "Finish signing in in your browser…"],
    connecting: ["bg-amber-400 animate-pulse", "Connecting…"], needs_sign_in: ["bg-amber-400", "Sign in needed"],
    error: ["bg-red-400", "Couldn't connect"], off: ["bg-zinc-600", ""],
  };
  function connectorCard(c, server) {
    const [dot, label] = STATE[c.state] || STATE.off;
    const on = c.state !== "off";
    const needsInput = !on || c.state === "error" || c.state === "needs_sign_in";
    return `
      <div class="task-card space-y-2" data-connector="${esc(c.id)}" ${c.id === "mac" ? "data-no-pc-words" : ""}>
        <div class="flex items-start gap-3">
          <span class="size-9 rounded-xl bg-surface-900 border border-white/[0.06] grid place-items-center shrink-0"><i data-lucide="${esc(c.icon)}" class="size-4 text-zinc-200"></i></span>
          <div class="flex-1 min-w-0">
            <p class="task-title flex items-center gap-2">${esc(c.name)}${on ? `<span class="size-2 rounded-full ${dot}"></span><span class="text-[11px] font-normal text-zinc-400">${label}${c.state === "connected" ? ` · ${c.tools} tools` : ""}</span>` : ""}</p>
            <p class="text-[12px] text-zinc-400 mt-0.5">${esc(c.blurb)}</p>
          </div>
        </div>
        ${c.unavailable ? `<p class="text-[12px] text-zinc-500">Not on this computer: ${esc(c.unavailable)}</p>` : ""}
        ${c.state === "error" && c.error ? `<p class="text-[12px] text-red-300">${esc(c.error)}</p>` : ""}
        ${c.cli_account && needsInput ? `<div class="flex items-center gap-2 flex-wrap"><button data-cn="cli" class="px-3 py-1.5 rounded-lg bg-brand-500 text-surface-900 text-[12px] font-semibold hover:bg-brand-400">Connect as ${esc(c.cli_account)}</button>
          <span class="text-[11px] text-zinc-500">Uses the sign-in the GitHub CLI (gh) already has here.</span></div>` : ""}
        ${c.from_cli && c.state === "connected" ? `<label class="flex items-center gap-2 text-[11px] text-zinc-400"><input type="checkbox" data-cn="share" ${c.shared_with_agents ? "checked" : ""}> Share with Crew jobs (Claude Code, Muse)</label>` : ""}
        ${c.kind === "token" && needsInput ? `${c.cli_account ? `<details class="text-[11px] text-zinc-500"><summary class="cursor-pointer hover:text-zinc-300">Or paste a token instead</summary>` : ""}<div class="space-y-1.5${c.cli_account ? " mt-1.5" : ""}"><p class="text-[11px] text-zinc-500">${esc(c.token_help)} <a href="${esc(c.token_url)}" target="_blank" rel="noopener" class="text-brand-300 hover:underline">Make a token</a></p>
          <input type="password" data-cn-token autocomplete="off" placeholder="Paste token" class="w-full bg-surface-900 border border-surface-700 rounded-lg px-2.5 py-1.5 text-[12px] text-white focus:outline-none focus:border-brand-500">
          ${c.cli_account ? `<button data-cn="connect" class="px-3 py-1.5 rounded-lg bg-surface-700 text-zinc-200 text-[12px] hover:bg-surface-600">Connect with this token</button>` : ""}</div>${c.cli_account ? "</details>" : ""}` : ""}
        ${c.kind === "own" && needsInput ? `<div class="space-y-1.5"><p class="text-[11px] text-zinc-500">Create a Desktop OAuth client in Google Cloud and enable the API (<a href="${esc(c.setup_url)}" target="_blank" rel="noopener" class="text-brand-300 hover:underline">Google's guide</a>). Add <span class="font-mono">${esc(location.origin)}/oauth/callback</span> as a redirect URI.</p>
          <input data-cn-client autocomplete="off" placeholder="Client ID" class="w-full bg-surface-900 border border-surface-700 rounded-lg px-2.5 py-1.5 text-[12px] text-white">
          <input type="password" data-cn-secret autocomplete="off" placeholder="Client secret" class="w-full bg-surface-900 border border-surface-700 rounded-lg px-2.5 py-1.5 text-[12px] text-white"></div>` : ""}
        ${c.id === "mac" && !on ? `<p class="text-[11px] text-zinc-500">macOS asks once per app the first time Xod uses Calendar, Reminders, Notes or Mail.</p>` : ""}
        <div class="flex items-center gap-2 flex-wrap">
          ${needsInput && !c.unavailable && !c.cli_account ? `<button data-cn="connect" class="px-3 py-1.5 rounded-lg bg-brand-500 text-surface-900 text-[12px] font-semibold hover:bg-brand-400">${c.kind === "oauth" || c.kind === "own" ? (on ? "Sign in again" : "Connect and sign in") : on ? "Try again" : c.kind === "open" ? "Connect (no account)" : "Connect"}</button>` : ""}
          ${c.state === "signing_in" ? `<span class="text-[11px] text-zinc-500">A sign-in page opened in your browser.</span>` : ""}
          ${server && c.state === "connected" ? `<select data-cn="trust" class="bg-surface-900 border border-surface-700 rounded-lg px-2 py-1 text-[11px] text-white">
            ${Object.entries(TRUST).map(([k, v]) => `<option value="${k}" ${server.trust === k ? "selected" : ""}>${v}</option>`).join("")}</select>` : ""}
          ${on ? `<button data-cn="disconnect" class="px-2 py-1.5 rounded-lg text-[12px] text-zinc-400 hover:text-white hover:bg-surface-700">Disconnect</button>` : ""}
        </div>
      </div>`;
  }

  async function onConnector(el, e) {
    const card = el.closest("[data-connector]");
    const id = card.dataset.connector;
    if (el.dataset.cn === "share" && e.type === "change") { await api("/api/connectors/share-with-agents", "POST", { on: el.checked }); return showToast(el.checked ? "Crew jobs get GitHub too" : "Crew jobs won't get GitHub"); }
    if (el.dataset.cn === "cli" && e.type === "click") {
      el.disabled = true; el.textContent = "Connecting…";
      const r = await api(`/api/connectors/${id}/connect`, "POST", { use_cli: true });
      if (!r.success) showToast(r.error || "Couldn't connect");
      return render();
    }
    if (el.dataset.cn === "trust" && e.type === "change") { await api(`/api/mcp/servers/${id}`, "PATCH", { trust: el.value }); return showToast("Saved"); }
    if (e.type !== "click") return;
    if (el.dataset.cn === "disconnect") {
      if (!confirm("Disconnect? Its sign-in is removed from your Keychain.")) return;
      await api(`/api/connectors/${id}`, "DELETE");
      return render();
    }
    if (el.dataset.cn === "connect") {
      const body = {
        token: card.querySelector("[data-cn-token]")?.value || null,
        client_id: card.querySelector("[data-cn-client]")?.value || null,
        client_secret: card.querySelector("[data-cn-secret]")?.value || null,
      };
      el.disabled = true; el.textContent = "Starting…";
      const r = await api(`/api/connectors/${id}/connect`, "POST", body);
      if (!r.success) { showToast(r.error || "Couldn't start"); }
      return render();
    }
  }

  // ---------------------------------------------------------------- paste anything (services/mcp_import.py)
  let importText = "", lastImport = null;
  const KIND = { web: "Web address", program: "Program on this computer" };
  function probeLine(s) {
    const p = s.probe;
    if (s.runtime) return `<p class="text-[12px] text-amber-200">It runs with <span class="font-mono">${esc(s.runtime.program)}</span>, which needs ${esc(s.runtime.what)} on this computer.
      <button data-mcpi="install" data-ask="${esc(s.runtime.ask)}" class="underline">${esc(s.runtime.ask)} for me</button></p>`;
    if (!p) return s.kind === "program" ? `<p class="text-[12px] text-zinc-400">Xod starts it when it connects (the first start can take a minute while it downloads).</p>` : "";
    const color = p.ok ? (p.auth === "oauth" ? "text-amber-200" : "text-emerald-300") : "text-red-300";
    return `<p class="text-[12px] ${color}">${esc(p.why)}</p>`;
  }
  function renderImport(res, results) {
    const box = document.getElementById("mcpImportResult");
    if (!box) return;
    if (res.error) { box.innerHTML = `<p class="text-[12px] text-red-300">${esc(res.error)}</p>`; return; }
    const done = Object.fromEntries((results || []).map((r) => [r.name, r]));
    box.innerHTML = res.servers.map((s) => {
      const r = done[s.name];
      return `<div class="task-card space-y-2" data-imp="${esc(s.name)}">
        <p class="task-title flex items-center gap-2">${esc(s.name)} <span class="text-[11px] font-normal text-zinc-400">${KIND[s.kind]}</span></p>
        <p class="text-[11px] text-zinc-500 font-mono break-all">${esc(s.summary)}</p>
        ${probeLine(s)}
        ${s.needs.map((n) => `<label class="block text-[11px] text-zinc-400">${esc(n.label)}${n.hint ? ` <span class="text-zinc-500">· ${esc(n.hint)}</span>` : ""}
          <input data-need="${esc(n.id)}" ${n.secret ? 'type="password"' : ""} autocomplete="off" class="mt-1 w-full bg-surface-900 border border-surface-700 rounded-lg px-2.5 py-1.5 text-[12px] text-white focus:outline-none focus:border-brand-500"></label>`).join("")}
        ${s.needs.some((n) => n.secret) ? `<p class="text-[11px] text-zinc-500">Keys go into your Keychain / Credential Manager, not a file.</p>` : ""}
        ${r ? `<p class="text-[12px] ${r.success ? "text-emerald-300" : "text-red-300"}">${r.success
          ? (r.signing_in ? esc(r.message) : `Connected · ${r.tools} tools${r.tool_names?.length ? `: ${esc(r.tool_names.slice(0, 6).join(", "))}${r.tools > 6 ? "…" : ""}` : ""}`)
          : esc(r.error)}</p>` : ""}
      </div>`;
    }).join("") + (res.servers.length ? `<button data-mcpi="add" class="px-3 py-1.5 rounded-lg bg-brand-500 text-surface-900 text-[12px] font-semibold hover:bg-brand-400">${(results || []).length ? "Try again" : `Connect${res.servers.length > 1 ? ` all ${res.servers.length}` : ""}`}</button>` : "");
  }
  async function onImport(el) {
    const box = document.getElementById("mcpImportResult");
    if (el.dataset.mcpi === "install") {
      window.closeMcpPanel();
      if (typeof usePrompt === "function") usePrompt(el.dataset.ask);
      return;
    }
    if (el.dataset.mcpi === "check") {
      importText = document.getElementById("mcpImportText").value;
      if (!importText.trim()) return showToast("Paste something first");
      el.disabled = true; el.textContent = "Checking…";
      box.innerHTML = `<p class="text-[12px] text-zinc-400">Reading it and asking the server…</p>`;
      const res = await api("/api/mcp/import/check", "POST", { text: importText });
      el.disabled = false; el.textContent = "Check it";
      lastImport = { res, results: null };
      return renderImport(res);
    }
    if (el.dataset.mcpi === "add") {
      const values = {};
      box.querySelectorAll("[data-imp]").forEach((card) => {
        values[card.dataset.imp] = Object.fromEntries([...card.querySelectorAll("[data-need]")].map((i) => [i.dataset.need, i.value]));
      });
      el.disabled = true; el.textContent = "Connecting…";
      const res = await api("/api/mcp/import/check", "POST", { text: importText });
      const out = await api("/api/mcp/import/add", "POST", { text: importText, values });
      lastImport = { res, results: out.results || [] };
      renderImport(res, out.results || []);
      // Keep what was typed (a failed key can be fixed and tried again).
      box.querySelectorAll("[data-imp]").forEach((card) => card.querySelectorAll("[data-need]").forEach((i) => {
        i.value = (values[card.dataset.imp] || {})[i.dataset.need] || "";
      }));
      if (out.results?.some((r) => r.success)) {
        showToast("Connected");
        setTimeout(render, out.results.some((r) => r.signing_in) ? 2500 : 300);
      }
    }
  }

  async function onClick(e) {
    const imp = e.target.closest("[data-mcpi]");
    if (imp && e.type === "click") return onImport(imp);
    const cn = e.target.closest("[data-cn]");
    if (cn) return onConnector(cn, e);
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
    if ((el.dataset.mcps === "signin" || el.dataset.mcps === "retry") && e.type === "click") {
      el.disabled = true;
      await api(`/api/mcp/servers/${name}/connect`, "POST", { interactive: el.dataset.mcps === "signin" });
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
