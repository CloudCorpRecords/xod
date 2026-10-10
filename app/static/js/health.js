// ==============================================================
// HEALTH: Xod's own checks and fixes (see services/doctor.py)
// ==============================================================
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => (typeof escapeHtml === "function" ? escapeHtml(String(s ?? "")) : String(s ?? ""));
  const ago = (t) => {
    const s = Math.max(1, Math.round(Date.now() / 1000 - t));
    return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
  };
  const COLORS = { ok: "#8fbf9a", warn: "#f2b24a", fail: "#ef7b6b" };
  let report = null;
  let busy = "";
  let selfEdit = null;
  let editBusy = "";
  const TIER = { green: ["#8fbf9a", "Can change Xod's code (checked first, revertible)"],
                 yellow: ["#f2b24a", "Can propose code changes; you approve each one"],
                 red: ["#ef7b6b", "Can't change code"] };
  const EDITOR_PRESETS = [
    ["", "Same as the chat model"],
    ["anthropic|claude-opus-5-5", "Claude Opus 5.5 (Anthropic key)"],
    ["anthropic|claude-fable-5-1", "Claude Fable 5.1 (Anthropic key)"],
    ["custom", "Other model…"],
  ];

  window.openHealthPanel = () => {
    $("healthPanel").classList.remove("hidden");
    requestAnimationFrame(() => $("healthPanel").classList.add("is-open"));
    if (report) render();
    refreshHealth(true);
  };
  window.closeHealthPanel = () => {
    $("healthPanel").classList.remove("is-open");
    setTimeout(() => $("healthPanel").classList.add("hidden"), 250);
  };

  async function loadSelfEdit() {
    try { selfEdit = await (await fetch("/api/self-edit")).json(); } catch (_) { selfEdit = null; }
  }

  async function seAction(path, body) {
    const res = await (await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}) })).json();
    if (res.status) selfEdit = res.status; else if (res.settings) selfEdit = res;
    return res;
  }

  window.selfEditSetting = async (field, value) => {
    await seAction("/api/self-edit/settings", { [field]: value });
    render();
  };

  window.selfEditEditor = async (value) => {
    if (value === "custom") {
      const provider = prompt("Provider (anthropic, openai, openrouter, gemini, lm_studio, ollama):", "openai");
      if (!provider) return render();
      const model = prompt("Model id:", "");
      if (!model) return render();
      await seAction("/api/self-edit/settings", { editor: { provider: provider.trim(), model: model.trim() } });
    } else if (!value) {
      await seAction("/api/self-edit/settings", { clear_editor: true });
    } else {
      const [provider, model] = value.split("|");
      await seAction("/api/self-edit/settings", { editor: { provider, model } });
    }
    render();
  };

  window.selfEditChange = async (id, action) => {
    if (action === "revert" && !confirm("Put back the code from before this change? Xod reloads itself.")) return;
    editBusy = id; render();
    const res = await seAction(`/api/self-edit/${id}/${action}`);
    editBusy = "";
    if (typeof showToast === "function") showToast(res.ok ? res.detail : `Couldn't: ${res.detail}`);
    render();
  };

  window.requestSelfEdit = async () => {
    const box = $("selfEditGoal");
    const goal = (box && box.value.trim()) || "";
    if (!goal) return box && box.focus();
    editBusy = "request"; render();
    let res;
    try {
      res = await (await fetch("/api/self-edit/request", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal }) })).json();
    } catch (e) { res = { success: false, error: e.message }; }
    editBusy = "";
    await loadSelfEdit();
    if (typeof showToast === "function") showToast(res.success ? `${res.summary} · ${res.status}` : `No change made: ${res.error}`);
    render();
  };

  function selfEditSection() {
    if (!selfEdit) return "";
    const ed = selfEdit.editor, st = selfEdit.settings;
    const [color, meaning] = TIER[ed.tier] || TIER.red;
    const cur = st.editor ? `${st.editor.provider}|${st.editor.model}` : "";
    const opts = EDITOR_PRESETS.map(([v, label]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${esc(label)}</option>`).join("")
      + (cur && !EDITOR_PRESETS.some(([v]) => v === cur) ? `<option value="${esc(cur)}" selected>${esc(cur.replace("|", ": "))}</option>` : "");
    const changes = (selfEdit.changes || []).map((c) => {
      const btns = c.status === "proposed"
        ? `<button class="task-btn task-btn-primary" onclick="selfEditChange('${c.id}','approve')" ${editBusy ? "disabled" : ""}>Approve</button>
           <button class="task-btn task-btn-quiet" onclick="selfEditChange('${c.id}','reject')" ${editBusy ? "disabled" : ""}>Reject</button>`
        : c.status === "applied" ? `<button class="task-btn" onclick="selfEditChange('${c.id}','revert')" ${editBusy ? "disabled" : ""}>Revert</button>` : "";
      const diff = (c.diff || "").split("\n").slice(0, 400).map((l) =>
        `<span class="${l.startsWith("+") && !l.startsWith("+++") ? "text-emerald-300" : l.startsWith("-") && !l.startsWith("---") ? "text-rose-300" : l.startsWith("@@") ? "text-indigo-300" : "text-zinc-400"}">${esc(l)}</span>`).join("\n");
      const checks = (c.checks || []).map((r) => `${r.ok ? "✓" : "✗"} ${esc(r.check)}`).join(" · ");
      return `<details class="task-card" ${c.status === "proposed" ? "open" : ""}>
        <summary class="flex items-center justify-between gap-3 cursor-pointer">
          <span class="task-title truncate">${esc(c.summary)}</span>
          <span class="task-state">${esc(c.status)} · ${ago(c.created_at)}</span>
        </summary>
        <div class="mt-2 space-y-2 text-[12px] text-zinc-400">
          <p>By ${esc(c.model)} (${esc(c.tier)}) · ${c.changed_lines} lines in ${esc((c.files || []).join(", "))}</p>
          <p>Checks: ${checks || "none"}</p>
          ${c.error ? `<p class="text-rose-300">${esc(c.error)}</p>` : ""}
          <pre class="text-[11px] leading-snug bg-surface-900 border border-white/[0.06] rounded-lg p-2 overflow-x-auto max-h-72">${diff}</pre>
          <div class="flex gap-2">${btns}</div>
        </div>
      </details>`;
    }).join("");
    return `<div class="space-y-2">
      <p class="tasks-group">Self-editing <span>Xod changing its own code</span></p>
      <div class="task-card space-y-2">
        <div class="flex items-start gap-3">
          <span class="mt-1.5 size-2 rounded-full shrink-0" style="background:${color}"></span>
          <div class="flex-1 min-w-0">
            <p class="task-title">${esc(ed.model)} <span class="text-zinc-500 font-normal">· ${esc(ed.source)}</span></p>
            <p class="text-[12.5px] text-zinc-400">${esc(meaning)}. ${esc(ed.why)}.${ed.note ? " " + esc(ed.note) : ""}</p>
          </div>
        </div>
        <label class="flex items-center gap-2 text-[12px] text-zinc-400">Code is written by
          <select onchange="selfEditEditor(this.value)" class="flex-1 bg-surface-900 border border-surface-700 rounded-lg px-2 py-1 text-zinc-100 text-[12px]">${opts}</select></label>
        <div class="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-zinc-400">
          <label class="flex items-center gap-1.5"><input type="checkbox" ${st.enabled ? "checked" : ""} onchange="selfEditSetting('enabled', this.checked)"> Allow self-editing</label>
          <label class="flex items-center gap-1.5"><input type="checkbox" ${st.auto_apply_green ? "checked" : ""} onchange="selfEditSetting('auto_apply_green', this.checked)"> Green models apply without asking</label>
        </div>
        <p class="text-[11px] text-zinc-500">Never editable: submit/pay/send approvals, saved logins and API keys, the repair safety list, and this gate.</p>
        ${ed.tier !== "red" && st.enabled ? `<div class="flex gap-2">
          <input id="selfEditGoal" type="text" placeholder="Ask Xod to change itself, e.g. “make wait_for default to 5 minutes”"
            class="flex-1 min-w-0 bg-surface-900 border border-surface-700 rounded-lg px-2.5 py-1.5 text-[12.5px] text-white placeholder:text-zinc-500"
            onkeydown="if(event.key==='Enter')requestSelfEdit()">
          <button class="task-btn task-btn-primary" onclick="requestSelfEdit()" ${editBusy ? "disabled" : ""}>${editBusy === "request" ? "Writing & testing…" : "Go"}</button>
        </div>` : ""}
      </div>
      ${changes}
    </div>`;
  }

  window.refreshHealth = async (show) => {
    if (show && !report) $("healthBody").innerHTML = `<p class="text-[13px] text-zinc-400">Checking…</p>`;
    if (show) loadSelfEdit().then(() => { if (!$("healthPanel").classList.contains("hidden")) render(); });
    try {
      report = await (await fetch("/api/doctor")).json();
    } catch (_) {
      report = null;
      $("healthBody").innerHTML = `<p class="text-[13px] text-zinc-400">Couldn't reach Xod's server.</p>`;
      return;
    }
    updateDot();
    if (!$("healthPanel").classList.contains("hidden")) render();
  };

  window.runHealthFix = async (fix, label) => {
    busy = fix;
    render();
    try {
      const res = await (await fetch("/api/doctor/fix", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fix }),
      })).json();
      report = res.report || report;
      if (typeof showToast === "function") showToast(res.ok ? `✓ ${res.detail}` : `Couldn't fix it: ${res.detail}`);
    } finally {
      busy = "";
      updateDot();
      render();
    }
  };

  function updateDot() {
    const dot = $("healthMenuDot");
    if (!dot || !report) return;
    dot.classList.toggle("hidden", report.overall === "ok");
    dot.style.background = COLORS[report.overall] || COLORS.warn;
  }

  function render() {
    if (!report) return;
    const draft = $("selfEditGoal") ? $("selfEditGoal").value : "";
    const typing = document.activeElement && document.activeElement.id === "selfEditGoal";
    const checks = report.checks.map((c) => `
      <div class="task-card flex items-start gap-3">
        <span class="mt-1.5 size-2 rounded-full shrink-0" style="background:${COLORS[c.level]}"></span>
        <div class="flex-1 min-w-0">
          <p class="task-title">${esc(c.label)}</p>
          <p class="text-[12.5px] text-zinc-400 mt-0.5">${esc(c.detail)}</p>
        </div>
        ${c.fix && !c.ok ? `<button onclick="runHealthFix('${esc(c.fix)}')" ${busy ? "disabled" : ""}
            class="shrink-0 px-2.5 py-1 rounded-lg text-[12px] font-semibold bg-brand-500 text-surface-900 hover:bg-brand-400 disabled:opacity-50">
            ${busy === c.fix ? "Fixing…" : esc(c.fix_label || "Fix")}</button>` : ""}
      </div>`).join("");
    const fixes = (report.recent_fixes || []).slice().reverse().map((f) =>
      `<li class="flex gap-2"><span class="text-zinc-500 shrink-0 w-16">${ago(f.at)}</span><span>${esc(f.text)}</span></li>`).join("");
    const fails = (report.recent_failures || []).slice().reverse().map((f) =>
      `<li class="flex gap-2"><span class="text-zinc-500 shrink-0 w-16">${ago(f.at)}</span><span><b class="text-zinc-300 font-medium">${esc(f.tool)}</b> · ${esc(f.kind.replace(/_/g, " "))}: ${esc(f.detail.slice(0, 140))}</span></li>`).join("");
    const headline = { ok: "Everything's working.", warn: "Working, with a few things to know.", fail: "Something needs attention." }[report.overall];
    $("healthBody").innerHTML = `
      <p class="text-[13px] text-zinc-200">${headline} <span class="text-zinc-500">Checked ${ago(report.at)}.</span></p>
      <div class="space-y-2">${checks}</div>
      ${fixes ? `<div><p class="tasks-group">Fixed by itself</p><ul class="space-y-1.5 text-[12.5px] text-zinc-300">${fixes}</ul></div>` : ""}
      ${fails ? `<div><p class="tasks-group">Recent tool errors <span>repairs are tried automatically</span></p><ul class="space-y-1.5 text-[12.5px] text-zinc-400">${fails}</ul></div>` : ""}
      ${selfEditSection()}`;
    const box = $("selfEditGoal");
    if (box && draft) { box.value = draft; if (typing) box.focus(); }
  }

  // Keep the menu dot current without opening the panel.
  setTimeout(() => refreshHealth(false), 4000);
  setInterval(() => refreshHealth(false), 120000);
})();
