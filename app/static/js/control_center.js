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

  async function updateFleetPolicy(patch) {
    try {
      const res = await fetch("/api/fleet/policy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (res.ok) refreshControlCenter();
    } catch (_) {}
  }

  let pendingAutonomyTarget = null;

  async function setAutonomyMode(targetMode, confirmedDisclaimer) {
    const errEl = document.getElementById("ccAutonomyErr");
    if (errEl) { errEl.textContent = ""; errEl.classList.add("hidden"); }
    try {
      const res = await fetch("/api/autonomy/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: targetMode, confirmed_disclaimer: !!confirmedDisclaimer }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (errEl) {
          errEl.textContent = data.detail || "Could not switch autonomy mode.";
          errEl.classList.remove("hidden");
        }
        return;
      }
      pendingAutonomyTarget = null;
      refreshControlCenter();
    } catch (e) {
      if (errEl) {
        errEl.textContent = String(e.message || e);
        errEl.classList.remove("hidden");
      }
    }
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
    const fleet = data.fleet || {};
    const autonomy = data.autonomy || { mode: "standard", locked: false, lock_reasons: [], disclaimer: "" };
    const vault = data.training_vault || { counts: {}, destinations: [], default_dir: "", last_export: {} };
    const vCounts = vault.counts || {};
    const vDests = vault.destinations || [];
    const vLast = vault.last_export || {};
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
          <div class="flex items-center justify-between gap-2">
            <h4 class="text-[13px] font-semibold text-white">AI Brain &amp; Voice</h4>
            <div class="flex items-center gap-1.5">
              ${brain.local_turbo_active ? `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-cyan-500/15 text-cyan-200 border border-cyan-500/35" title="Local Model Harness Turbo: 6-tool MCP cap + tight 2-step rolling compaction">Local Turbo</span>` : ""}
              ${pill(!!brain.model, brain.provider || "Active")}
            </div>
          </div>
          <div class="text-[12px] text-zinc-300 font-mono truncate" title="${esc(brain.model)}">${esc(brain.model || "Not configured")}</div>
          <div class="text-[11px] text-zinc-400">
            Prompt cache hit rate today: <b class="text-emerald-300">${brain.cache_hit_rate || 0}%</b>
            ${brain.cached_tokens ? `<span class="text-zinc-500">(${Number(brain.cached_tokens).toLocaleString()} cached tokens)</span>` : ""}
          </div>
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

      <!-- 5. Autonomy Modes: Standard / Turbo / Xod Mode (locked while an agent is running) -->
      <section class="rounded-xl bg-surface-850 border border-white/[0.08] p-4 space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h4 class="text-[13px] font-semibold text-white">Autonomy Mode</h4>
            <p class="text-[11px] text-zinc-400">Pick how freely Xod runs. Locked automatically while any agent or task is active so the harness stays stable.</p>
          </div>
          ${autonomy.locked
            ? `<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-amber-500/15 text-amber-200 border border-amber-500/35">Locked · ${esc((autonomy.lock_reasons || []).join(", ") || "agent running")}</span>`
            : pill(autonomy.mode === "standard", autonomy.mode === "xod" ? "Xod Mode Active" : autonomy.mode === "turbo" ? "Turbo Active" : "Standard")}
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-2">
          ${[
            ["standard", "Standard", "Guarded approvals & normal assistant voice."],
            ["turbo", "Turbo", "All safety rails off. Max-speed autonomous execution."],
            ["xod", "Xod Mode", "All rails off + free-thinking autonomous soul with its own choices."],
          ].map(([m, title, desc]) => {
            const active = (autonomy.mode || "standard") === m;
            const accent = m === "xod" ? "border-purple-400/60 bg-purple-500/15" : m === "turbo" ? "border-amber-400/60 bg-amber-500/15" : "border-brand-400/60 bg-brand-500/15";
            return `
              <button type="button" data-cc-autonomy="${m}" ${autonomy.locked && !active ? "disabled" : ""}
                class="text-left rounded-xl p-3 border transition-all ${active ? accent : "border-white/[0.07] bg-surface-900/70 hover:bg-surface-800"} ${autonomy.locked && !active ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}">
                <div class="flex items-center justify-between gap-1">
                  <span class="text-[12px] font-semibold text-white">${title}</span>
                  ${active ? `<span class="text-[10px] uppercase tracking-wider font-semibold text-brand-300">Active</span>` : ""}
                </div>
                <p class="text-[11px] text-zinc-400 mt-1 leading-snug">${desc}</p>
              </button>
            `;
          }).join("")}
        </div>
        <div id="ccAutonomyDisclaimer" class="${pendingAutonomyTarget ? "" : "hidden"} rounded-xl bg-red-500/10 border border-red-500/30 p-3 space-y-2.5">
          <div class="text-[12px] font-semibold text-red-200">Liability Disclaimer — Enable ${pendingAutonomyTarget === "xod" ? "Xod Mode" : "Turbo Mode"}?</div>
          <p class="text-[11px] text-red-100/90 leading-relaxed">${esc(autonomy.disclaimer || "")}</p>
          <div class="flex flex-wrap gap-2">
            <button type="button" id="ccConfirmAutonomyBtn" class="px-3 py-1.5 rounded-lg bg-red-500 hover:bg-red-400 text-white text-[12px] font-semibold">I accept full responsibility — Enable</button>
            <button type="button" id="ccCancelAutonomyBtn" class="px-3 py-1.5 rounded-lg bg-surface-700 hover:bg-surface-600 text-zinc-200 text-[12px]">Cancel</button>
          </div>
        </div>
        <div id="ccAutonomyErr" class="hidden rounded-lg bg-red-500/10 border border-red-500/25 px-3 py-2 text-[12px] text-red-300"></div>
      </section>

      <!-- 6. Fleet Control, Rubber-Stamp Auto-Approve & Cross-Chat Blackboard -->
      <section class="rounded-xl bg-surface-850 border border-white/[0.07] p-4 space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h4 class="text-[13px] font-semibold text-white">Fleet Control &amp; Cross-Chat Memory</h4>
            <p class="text-[11px] text-zinc-400">Agent-managed fleet approvals (same rubber-stamp policy as Muse) + shared working blackboard across chats and Xods.</p>
          </div>
          ${pill(fleet.auto_approve_mode === "act", fleet.auto_approve_mode === "act" ? "Rubber-Stamp Active" : fleet.auto_approve_mode === "decide" ? "Audit Only" : "Manual Ask")}
        </div>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="inline-flex rounded-lg bg-surface-900 p-1 border border-white/[0.06] gap-1">
            ${[["act", "Rubber-Stamp (Auto)"], ["decide", "Audit Only"], ["off", "Ask"]].map(([m, lbl]) => `
              <button type="button" data-cc-fleet-mode="${m}" class="px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors ${
                (fleet.auto_approve_mode || "act") === m
                  ? "bg-brand-500 text-surface-900"
                  : "text-zinc-300 hover:bg-surface-700"
              }">${lbl}</button>
            `).join("")}
          </div>
          <label class="flex items-center gap-2 text-[12px] text-zinc-200 cursor-pointer">
            <input type="checkbox" id="ccPermissionlessChk" class="accent-brand-500" ${brain.permissionless_mode ? "checked" : ""}>
            <span>Permissionless Mode <span class="text-zinc-500">(zero routine approval cards, audited)</span></span>
          </label>
          <label class="flex items-center gap-2 text-[12px] text-zinc-200 cursor-pointer">
            <input type="checkbox" id="ccFleetSummariesChk" class="accent-brand-500" ${fleet.sync_chat_summaries !== false ? "checked" : ""}>
            <span>Share chat summaries &amp; Fleet Blackboard</span>
          </label>
        </div>
        <div class="flex flex-wrap gap-3 text-[11px] text-zinc-400">
          <span>Audited fleet stamps: <b class="text-zinc-200">${fleet.audit_count || 0}</b></span>
          <span>Muse approver: <b class="text-zinc-200">${esc(fleet.muse_mode || "always")}</b></span>
          <span>Active blackboard items: <b class="text-zinc-200">${fleet.blackboard_count || 0}</b></span>
        </div>
        ${(fleet.blackboard || []).length ? `
          <div class="space-y-1.5 max-h-28 overflow-y-auto pt-1 border-t border-white/[0.05]">
            ${(fleet.blackboard || []).slice(0, 5).map((b) => `
              <div class="text-[11px] text-zinc-300 flex items-center justify-between gap-2">
                <span class="truncate"><span class="text-cyan-300 font-medium">[${esc(b.topic)}]</span> ${esc(b.summary)}</span>
                <span class="text-zinc-500 shrink-0">${esc(b.actor)}</span>
              </div>
            `).join("")}
          </div>
        ` : ""}
      </section>

      <!-- 7. Personal Training Data Vault -->
      <section class="rounded-xl bg-surface-850 border border-white/[0.07] p-4 space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h4 class="text-[13px] font-semibold text-white">Personal Training Data Vault</h4>
            <p class="text-[11px] text-zinc-400">Package your SFT chats, DPO preference pairs, and agent trajectories into clean ML-ready JSONL + SHA-256 checksums for fine-tuning or backup.</p>
          </div>
          ${pill((vCounts.total_records || 0) > 0, `${Number(vCounts.total_records || 0).toLocaleString()} records`)}
        </div>
        <div class="grid grid-cols-3 gap-2 text-center">
          <div class="rounded-lg bg-surface-900/70 border border-white/[0.05] py-2 px-2">
            <div class="text-[15px] font-semibold text-white">${Number(vCounts.sft_chat || 0).toLocaleString()}</div>
            <div class="text-[10px] uppercase tracking-wider text-zinc-400">SFT Chats</div>
          </div>
          <div class="rounded-lg bg-surface-900/70 border border-white/[0.05] py-2 px-2">
            <div class="text-[15px] font-semibold text-white">${Number(vCounts.dpo_preferences || 0).toLocaleString()}</div>
            <div class="text-[10px] uppercase tracking-wider text-zinc-400">DPO Pairs</div>
          </div>
          <div class="rounded-lg bg-surface-900/70 border border-white/[0.05] py-2 px-2">
            <div class="text-[15px] font-semibold text-white">${Number(vCounts.agent_trajectories || 0).toLocaleString()}</div>
            <div class="text-[10px] uppercase tracking-wider text-zinc-400">Trajectories</div>
          </div>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <select id="ccVaultDestSelect" class="rounded-lg bg-surface-900 border border-white/[0.08] px-2.5 py-1.5 text-[12px] text-zinc-200 focus:outline-none">
            ${vDests.map((d) => `<option value="${esc(d.path)}" ${d.path === vault.default_dir ? "selected" : ""}>${esc(d.label)}${d.free_gb != null ? ` (${d.free_gb} GB free)` : ""}</option>`).join("")}
            <option value="__custom__">Custom folder path…</option>
          </select>
          <input id="ccVaultCustomDir" type="text" value="${esc(vault.default_dir || "")}" placeholder="/Volumes/MyDrive/Xod-Training-Vault"
            class="rounded-lg bg-surface-900 border border-white/[0.08] px-2.5 py-1.5 text-[12px] text-zinc-200 font-mono focus:outline-none">
        </div>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <label class="flex items-center gap-2 text-[12px] text-zinc-200 cursor-pointer">
            <input type="checkbox" id="ccVaultScrubChk" class="accent-brand-500" checked>
            <span>Scrub API keys, emails, phones &amp; home paths <span class="text-zinc-500">(recommended)</span></span>
          </label>
          <button type="button" id="ccVaultExportBtn"
            class="px-3 py-1.5 rounded-lg bg-brand-500 hover:bg-brand-400 text-surface-900 text-[12px] font-semibold shrink-0">
            Package &amp; Export Dataset
          </button>
        </div>
        <div id="ccVaultStatus" class="text-[11px] text-zinc-400">
          ${vLast.exported_at
            ? `Last export: <span class="text-zinc-200 font-mono">${esc(vLast.dest_dir || "")}</span> (${Number((vLast.counts || {}).total_records || 0).toLocaleString()} records, ~${Number(vLast.estimated_tokens || 0).toLocaleString()} tokens, ${vLast.redactions_count || 0} redactions).`
            : "Exports sft_chat.jsonl, dpo_preferences.jsonl, agent_trajectories.jsonl, dataset_card.json, and SHA256SUMS.txt."}
        </div>
      </section>
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
    body.querySelectorAll("[data-cc-fleet-mode]").forEach((btn) => {
      btn.addEventListener("click", () => updateFleetPolicy({ auto_approve_mode: btn.getAttribute("data-cc-fleet-mode") }));
    });
    const fs = document.getElementById("ccFleetSummariesChk");
    if (fs) fs.addEventListener("change", () => updateFleetPolicy({ sync_chat_summaries: fs.checked }));
    const pm = document.getElementById("ccPermissionlessChk");
    if (pm) pm.addEventListener("change", () => updateFleetPolicy({ permissionless_mode: pm.checked }));
    body.querySelectorAll("[data-cc-autonomy]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.getAttribute("data-cc-autonomy");
        if (!target || target === (autonomy.mode || "standard")) return;
        if (target === "standard") {
          setAutonomyMode("standard", false);
        } else {
          pendingAutonomyTarget = target;
          renderSnapshot(data);
        }
      });
    });
    const cBtn = document.getElementById("ccConfirmAutonomyBtn");
    if (cBtn) cBtn.addEventListener("click", () => { if (pendingAutonomyTarget) setAutonomyMode(pendingAutonomyTarget, true); });
    const xBtn = document.getElementById("ccCancelAutonomyBtn");
    if (xBtn) xBtn.addEventListener("click", () => { pendingAutonomyTarget = null; renderSnapshot(data); });

    const vSel = document.getElementById("ccVaultDestSelect");
    const vCustom = document.getElementById("ccVaultCustomDir");
    if (vSel && vCustom) {
      vSel.addEventListener("change", () => {
        if (vSel.value && vSel.value !== "__custom__") vCustom.value = vSel.value;
        else vCustom.focus();
      });
    }
    const vBtn = document.getElementById("ccVaultExportBtn");
    if (vBtn) {
      vBtn.addEventListener("click", async () => {
        const statusEl = document.getElementById("ccVaultStatus");
        const scrub = !!(document.getElementById("ccVaultScrubChk") && document.getElementById("ccVaultScrubChk").checked);
        const dest = (vCustom && vCustom.value.trim()) || (vSel && vSel.value !== "__custom__" ? vSel.value : "");
        vBtn.disabled = true;
        vBtn.textContent = "Packaging…";
        if (statusEl) statusEl.textContent = "Scrubbing and writing JSONL dataset bundle…";
        try {
          const res = await fetch("/api/training-vault/export", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ dest_dir: dest, scrub_pii: scrub }),
          });
          const out = await res.json().catch(() => ({}));
          if (out.ok) {
            if (statusEl) {
              statusEl.innerHTML = `<span class="text-emerald-300">Exported ${Number((out.counts || {}).total_records || 0).toLocaleString()} records (~${Number(out.estimated_tokens || 0).toLocaleString()} tokens, ${out.redactions_count || 0} redactions) to <span class="font-mono">${esc(out.dest_dir)}</span>.</span>`;
            }
          } else if (statusEl) {
            statusEl.innerHTML = `<span class="text-red-300">${esc(out.error || "Export failed.")}</span>`;
          }
        } catch (e) {
          if (statusEl) statusEl.innerHTML = `<span class="text-red-300">${esc(String(e.message || e))}</span>`;
        } finally {
          vBtn.disabled = false;
          vBtn.textContent = "Package & Export Dataset";
        }
      });
    }
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
