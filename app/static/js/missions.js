// ==============================================================
// MISSIONS: the parent orchestrator (see services/missions.py)
// Lives inside the Tasks drawer: Xod plans a goal into steps,
// runs them in order as tasks, and checks each one on disk.
// ==============================================================
(() => {
  let missions = [];
  let selectedId = null;
  let detail = null;
  let lastRendered = "";

  const $ = (id) => document.getElementById(id);
  const esc = (s) => (typeof escapeHtml === "function" ? escapeHtml(String(s ?? "")) : String(s ?? ""));
  const ago = (t) => {
    if (!t) return "";
    const s = Math.max(1, Math.round(Date.now() / 1000 - t));
    return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`;
  };

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    return res.json().catch(() => ({}));
  }

  const panelOpen = () => { const p = $("tasksPanel"); return p && !p.classList.contains("hidden"); };

  async function refresh() {
    if (!panelOpen()) return;
    const data = await api("/api/missions");
    if (Array.isArray(data.missions)) missions = data.missions;
    if (selectedId) {
      const d = await api(`/api/missions/${selectedId}`);
      detail = d.mission || null;
      if (!detail) selectedId = null;
    }
    render();
  }

  // ---------- rendering ----------
  const stateChip = (state) => {
    const styles = {
      planning: "bg-amber-500/15 text-amber-300",
      running: "bg-sky-500/15 text-sky-300",
      paused: "bg-orange-500/15 text-orange-300",
      done: "bg-emerald-500/15 text-emerald-300",
      failed: "bg-red-500/15 text-red-300",
    };
    return `<span class="px-1.5 py-0.5 rounded text-[10px] font-semibold ${styles[state] || "bg-white/10 text-zinc-300"}">${esc(state)}</span>`;
  };

  const verifiedChip = (s) => {
    const map = {
      verified: ["Verified on disk", "bg-emerald-500/15 text-emerald-300"],
      contradicted: ["Not true on disk", "bg-red-500/15 text-red-300"],
      failed: ["Failed", "bg-red-500/15 text-red-300"],
      unverifiable: ["Could not be checked", "bg-amber-500/15 text-amber-300"],
    };
    const hit = map[s.verified_status];
    if (hit) return `<span class="px-1.5 py-0.5 rounded text-[10px] font-semibold ${hit[1]}">${hit[0]}</span>`;
    if (s.status === "waiting") return `<span class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-orange-500/15 text-orange-300">Needs you</span>`;
    if (s.status === "skipped") return `<span class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-white/10 text-zinc-400">Skipped</span>`;
    return `<span class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-white/10 text-zinc-400">${esc(s.status)}</span>`;
  };

  function missionRow(m) {
    const p = m.progress || { subtasks: 0, done: 0, verified: 0 };
    const when = ago(m.finished_at || m.updated_at || m.created_at);
    return `<button onclick="selectMission('${m.id}')" class="w-full text-left bg-surface-900 border ${selectedId === m.id ? "border-brand-500" : "border-surface-700"} rounded-lg px-3 py-2 hover:border-brand-500">
      <div class="flex items-center justify-between gap-2">
        <span class="text-[13px] text-white font-medium truncate">${esc(m.goal)}</span>${stateChip(m.state)}
      </div>
      <div class="text-[11px] text-zinc-500 mt-0.5">${p.subtasks ? `${p.done}/${p.subtasks} steps done · ${p.verified} verified · ` : ""}${esc(when)}</div>
      ${m.failure_reason ? `<div class="text-[11px] text-red-300 mt-0.5">${esc(m.failure_reason)}</div>` : ""}
    </button>`;
  }

  function detailHtml(m) {
    if (!m) return "";
    const steps = (m.subtasks || []).map((s) => `
      <div class="border border-surface-700 rounded-lg px-3 py-2">
        <div class="flex items-center justify-between gap-2">
          <span class="text-[12px] text-white font-medium">${s.idx + 1}. ${esc(s.title)}</span>${verifiedChip(s)}
        </div>
        <div class="text-[12px] text-zinc-400 mt-0.5">${esc(s.instruction)}</div>
        ${(s.expected_effects || []).length ? `<div class="text-[11px] text-zinc-500 mt-1">Expected on disk: ${s.expected_effects.map((e) => `${esc(e.kind)} ${esc(e.path)}`).join(" · ")}</div>` : ""}
        ${(s.evidence || []).map((e) => `<div class="text-[11px] text-zinc-400 mt-0.5">Checked: ${esc(e)}</div>`).join("")}
        ${s.claimed ? `<details class="mt-1"><summary class="text-[11px] text-zinc-500 cursor-pointer">What the agent reported</summary><div class="text-[12px] text-zinc-300 mt-1 whitespace-pre-wrap">${esc(s.claimed)}</div></details>` : ""}
      </div>`).join("");
    const rep = m.report;
    const totals = rep ? `<div class="text-[11px] text-zinc-500 mt-2">Report: ${rep.totals.done}/${rep.totals.subtasks} steps done, ${rep.totals.verified} verified on disk, ${rep.totals.failed} failed. Usage: ${esc((rep.usage || {}).status || "")}.</div>` : "";
    return `<div class="flex items-center justify-between gap-2 mt-1">
        <span class="text-[12px] font-semibold text-white">Plan and checks</span>
        <button onclick="selectMission(null)" class="text-[11px] text-zinc-500 hover:text-white">Close</button>
      </div>
      <div class="space-y-2 mt-2">${steps || '<div class="text-[12px] text-zinc-500">Planning...</div>'}</div>${totals}`;
  }

  function render() {
    const list = $("missionsList");
    if (!list) return;
    const html = (missions.length ? missions.map(missionRow).join("") : '<div class="text-[12px] text-zinc-500">No missions yet. Give Xod a bigger job and it will plan the steps itself.</div>')
      + (selectedId && detail ? detailHtml(detail) : "");
    if (html === lastRendered) return;
    lastRendered = html;
    list.innerHTML = html;
  }

  // ---------- actions ----------
  window.selectMission = (id) => {
    selectedId = id;
    detail = null;
    lastRendered = "";
    refresh();
  };

  window.createMissionFromPanel = async () => {
    const input = $("newMissionInput");
    const goal = (input.value || "").trim();
    if (!goal) return;
    const res = await api("/api/missions", {
      method: "POST",
      body: JSON.stringify({ goal, continue_on_failure: !!($("missionContinueChk") || {}).checked }),
    });
    if (res.success) {
      input.value = "";
      selectedId = res.mission.id;
      lastRendered = "";
      refresh();
    }
  };

  // Refresh with the Tasks drawer: piggyback on its open/close and poll while open.
  const openOrig = window.openTasksPanel;
  window.openTasksPanel = () => { if (openOrig) openOrig(); refresh(); };
  setInterval(refresh, 4000);
})();
