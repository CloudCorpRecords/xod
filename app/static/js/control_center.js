/**
 * Xod Unified Control Center UI.
 * Single dashboard for AI Brain, Mac Computer Control permissions,
 * Apps & MCPs (with Smart MCP Tool Routing), and Signed-in Chrome / Browser logins.
 */
(function () {
  function esc(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function pill(ok, label) {
    return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium border ${
      ok
        ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
        : "bg-amber-500/15 text-amber-200 border-amber-500/30"
    }"><span class="size-1.5 rounded-full ${ok ? "bg-emerald-400" : "bg-amber-400"}"></span>${esc(label)}</span>`;
  }

  function updateTopBadge(issueCount) {
    const badge = document.getElementById("ccNavBadge");
    if (!badge) return;
    if (issueCount > 0) {
      badge.textContent = String(issueCount);
      badge.classList.remove("hidden");
    } else {
      badge.textContent = "";
      badge.classList.add("hidden");
    }
  }

  async function openMacPermission(pane) {
    try {
      await fetch("/api/desktop/open-permissions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pane }),
      });
    } catch (_) {}
  }

  async function updateControlSetting(patch) {
    try {
      const res = await fetch("/api/control-center/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (res.ok) renderSnapshot(await res.json());
    } catch (_) {}
  }

  function renderSnapshot(data) {
    updateTopBadge(data.issue_count || 0);
    const body = document.getElementById("ccModalBody");
    if (!body) return;

    const issues = data.issues || [];
    const brain = data.brain || {};
    const mcp = data.mcp || {};
    const mac = data.mac_control || {};
    const br = data.browser_sessions || {};
    const route = mcp.last_route || {};

    const issuesHtml = issues.length
      ? `<div class="space-y-2">
          <div class="text-[11px] uppercase tracking-wider font-semibold text-amber-300">Needs Attention (${issues.length})</div>
          ${issues.map((it) => `
            <div class="rounded-xl bg-amber-500/10 border border-amber-500/25 p-3 flex items-center justify-between gap-3">
              <div class="min-w-0">
                <div class="text-[13px] font-semibold text-amber-100">${esc(it.title)}</div>
                <div class="text-[12px] text-amber-200/80 mt-0.5">${esc(it.detail)}</div>
              </div>
              <button type="button" data-cc-action="${esc(it.action_type)}" data-cc-target="${esc(it.action_target)}"
                class="px-3 py-1.5 rounded-lg bg-brand-500 hover:bg-brand-400 text-surface-900 text-[12px] font-semibold shrink-0">
                ${esc(it.action_label || "Fix")}
              </button>
            </div>
          `).join("")}
        </div>`
      : `<div class="rounded-xl bg-emerald-500/10 border border-emerald-500/25 px-3.5 py-2.5 flex items-center gap-2 text-[12px] text-emerald-200">
          <span class="size-2 rounded-full bg-emerald-400"></span>
          All systems healthy — Mac control, MCP tools, and AI brain are ready.
        </div>`;

    const routeNote = route.total_tools
      ? `Last turn routed <b>${route.selected_tools}</b> of <b>${route.total_tools}</b> tools (saved ${route.saved_tools || 0}).`
      : `${mcp.total_tools || 0} tools across ${mcp.connected_count || 0} connected servers.`;

    body.innerHTML = `
      ${issuesHtml}

      <div class="grid grid-cols-1 md:grid-cols-2 gap-3.5">
        <!-- 1. AI Brain & Voice -->
        <section class="rounded-xl bg-surface-850 border border-white/[0.07] p-4 space-y-2.5">
          <div class="flex items-center justify-between">
            <h4 class="text-[13px] font-semibold text-white">AI Brain &amp; Voice</h4>
            ${pill(!!brain.model, brain.provider || "Active")}
          </div>
          <div class="text-[12px] text-zinc-300 font-mono truncate" title="${esc(brain.model)}">${esc(brain.model || "Not configured")}</div>
          <div class="flex flex-wrap gap-1.5 pt-0.5">
            ${(brain.keychain_keys || []).map((k) => `<span class="px-2 py-0.5 rounded-md bg-surface-800 border border-white/[0.06] text-[11px] text-zinc-300">${esc(k)}</span>`).join("")}
            ${brain.agora_configured ? `<span class="px-2 py-0.5 rounded-md bg-brand-500/15 border border-brand-500/30 text-[11px] text-brand-200">Agora${brain.agora_convoai_ready ? " ConvoAI" : " RTC"}</span>` : ""}
          </div>
          <div class="pt-1 flex gap-2">
            <button type="button" onclick="closeControlCenter(); openSettingsPanel();" class="px-2.5 py-1 rounded-lg bg-surface-700 hover:bg-surface-600 text-[12px] text-zinc-200">Settings</button>
            <button type="button" onclick="closeControlCenter(); openVoiceBriefModal();" class="px-2.5 py-1 rounded-lg bg-surface-700 hover:bg-surface-600 text-[12px] text-zinc-200">Voice Brief</button>
          </div>
        </section>

        <!-- 2. Mac Computer Control -->
        <section class="rounded-xl bg-surface-850 border border-white/[0.07] p-4 space-y-2.5">
          <div class="flex items-center justify-between">
            <h4 class="text-[13px] font-semibold text-white">Mac Computer Control</h4>
            ${pill(!!mac.ready, mac.ready ? "Ready" : "Permissions needed")}
          </div>
          <div class="flex flex-wrap gap-1.5">
            ${pill(!!mac.screen, "Screen Recording")}
            ${pill(!!mac.accessibility, "Accessibility")}
            ${pill(!!mac.microphone, "Microphone")}
          </div>
          <div class="pt-1 flex flex-wrap gap-2">
            <button type="button" data-cc-action="mac_permission" data-cc-target="screen" class="px-2.5 py-1 rounded-lg bg-surface-700 hover:bg-surface-600 text-[12px] text-zinc-200">Screen Recording</button>
            <button type="button" data-cc-action="mac_permission" data-cc-target="accessibility" class="px-2.5 py-1 rounded-lg bg-surface-700 hover:bg-surface-600 text-[12px] text-zinc-200">Accessibility</button>
          </div>
        </section>

        <!-- 3. Apps & MCPs + Smart Tool Routing -->
        <section class="rounded-xl bg-surface-850 border border-white/[0.07] p-4 space-y-2.5">
          <div class="flex items-center justify-between">
            <h4 class="text-[13px] font-semibold text-white">Apps &amp; MCP Tools</h4>
            ${pill((mcp.needs_attention || []).length === 0, `${mcp.connected_count || 0}/${mcp.total_servers || 0} connected`)}
          </div>
          <label class="flex items-center justify-between gap-2 text-[12px] text-zinc-200 bg-surface-900/70 rounded-lg px-2.5 py-2 border border-white/[0.05] cursor-pointer">
            <span>Smart MCP Tool Routing <span class="text-zinc-500">(faster, higher accuracy)</span></span>
            <input type="checkbox" id="ccSmartRoutingChk" class="accent-brand-500" ${mcp.smart_routing ? "checked" : ""}>
          </label>
          <p class="text-[11px] text-zinc-400">${routeNote}</p>
          <div class="space-y-1.5 max-h-32 overflow-y-auto pr-1">
            ${(mcp.servers || []).map((s) => `
              <div class="flex items-center justify-between text-[12px] py-1 border-b border-white/[0.04] last:border-0">
                <span class="text-zinc-200 truncate">${esc(s.name)} <span class="text-zinc-500">(${s.tool_count})</span></span>
                ${s.connected
                  ? `<span class="text-emerald-400 text-[11px]">Connected</span>`
                  : `<button type="button" data-cc-action="mcp_reconnect" data-cc-target="${esc(s.name)}" class="text-amber-300 hover:text-amber-200 text-[11px] underline">${s.needs_sign_in ? "Sign in" : "Reconnect"}</button>`}
              </div>
            `).join("")}
          </div>
          <div class="pt-1">
            <button type="button" onclick="closeControlCenter(); if(window.openMcpPanel) openMcpPanel(); else openSettingsPanel();" class="px-2.5 py-1 rounded-lg bg-surface-700 hover:bg-surface-600 text-[12px] text-zinc-200">Manage All 32 Connectors &amp; MCPs</button>
          </div>
        </section>

        <!-- 4. Signed-in Chrome & Saved Browser Sessions -->
        <section class="rounded-xl bg-surface-850 border border-white/[0.07] p-4 space-y-2.5">
          <div class="flex items-center justify-between">
            <h4 class="text-[13px] font-semibold text-white">Signed-in Chrome &amp; Browser</h4>
            ${pill(!!br.chrome_running, br.chrome_running ? `${br.chrome_tabs_count} Chrome tabs` : "Chrome idle")}
          </div>
          <label class="flex items-center justify-between gap-2 text-[12px] text-zinc-200 bg-surface-900/70 rounded-lg px-2.5 py-2 border border-white/[0.05] cursor-pointer">
            <span>Prefer my signed-in Chrome tabs</span>
            <input type="checkbox" id="ccPreferChromeChk" class="accent-brand-500" ${br.prefer_live_chrome ? "checked" : ""}>
          </label>
          <div class="text-[11px] text-zinc-400">
            Saved vault logins: ${(br.saved_logins || []).length
              ? (br.saved_logins || []).slice(0, 8).map((l) => `<span class="inline-block mr-1.5 text-zinc-300">${esc(l.site)}</span>`).join("")
              : "None saved yet (use Take Over once or signed-in Chrome)."}
          </div>
          <div class="pt-1 flex flex-wrap gap-2">
            <button type="button" onclick="closeControlCenter(); openQuickActionsModal('tabs');" class="px-2.5 py-1 rounded-lg bg-surface-700 hover:bg-surface-600 text-[12px] text-zinc-200">Live Chrome Tabs</button>
            <button type="button" onclick="closeControlCenter(); openAutonomousCockpitModal();" class="px-2.5 py-1 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/30 text-[12px] text-emerald-300">Take Over Browser</button>
          </div>
        </section>
      </div>
    `;

    body.querySelectorAll("[data-cc-action]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const action = btn.getAttribute("data-cc-action");
        const target = btn.getAttribute("data-cc-target");
        if (action === "mac_permission") {
          await openMacPermission(target);
        } else if (action === "mcp_reconnect") {
          closeControlCenter();
          if (typeof window.openMcpPanel === "function") window.openMcpPanel();
          else if (typeof window.openSettingsPanel === "function") window.openSettingsPanel();
        }
      });
    });

    const sr = document.getElementById("ccSmartRoutingChk");
    if (sr) sr.addEventListener("change", () => updateControlSetting({ smart_mcp_routing: sr.checked }));
    const pc = document.getElementById("ccPreferChromeChk");
    if (pc) pc.addEventListener("change", () => updateControlSetting({ prefer_live_chrome: pc.checked }));
  }

  async function refreshControlCenter() {
    try {
      const res = await fetch("/api/control-center");
      if (!res.ok) return;
      const data = await res.json();
      renderSnapshot(data);
    } catch (_) {}
  }

  function openControlCenter() {
    const modal = document.getElementById("controlCenterModal");
    if (!modal) return;
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    refreshControlCenter();
    if (window.lucide && typeof window.lucide.createIcons === "function") window.lucide.createIcons();
  }

  function closeControlCenter() {
    const modal = document.getElementById("controlCenterModal");
    if (!modal) return;
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
  }

  document.addEventListener("DOMContentLoaded", () => {
    setTimeout(async () => {
      try {
        const r = await fetch("/api/control-center");
        if (r.ok) {
          const d = await r.json();
          updateTopBadge(d.issue_count || 0);
        }
      } catch (_) {}
    }, 1200);
  });

  window.openControlCenter = openControlCenter;
  window.closeControlCenter = closeControlCenter;
  window.refreshControlCenter = refreshControlCenter;
})();
