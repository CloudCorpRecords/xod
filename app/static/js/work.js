// ==============================================================
// WORK (Phase 1, simplicity redesign): the ONE composer, the
// plain-words plan check, and the header status line.
//
// There is no "Hand off" vs "Missions" choice any more. The user
// says the job once (here, or with the chat composer's hand-off
// button); WorkRouter decides quick task vs multi-step mission,
// and big or sensitive jobs show their plan in plain words before
// anything runs. The model picker and the Local chip moved one
// layer down, into the status line's detail panel — the functions
// behind them (model_switcher.js, app.js) are untouched and keep
// working on the same element ids.
// ==============================================================
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => (typeof escapeHtml === "function" ? escapeHtml(String(s ?? "")) : String(s ?? ""));

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    return res.json().catch(() => ({}));
  }

  // ---------- keep going if a step fails ----------
  // Behaviour, not a surface choice: missions keep going by
  // default (a sensible default for a background helper). The
  // choice still exists, one layer down in the composer's Details
  // area, and every mission's own Details shows the state it ran
  // with (missions.js detail view).
  const COF_KEY = "xod.work.continueOnFailure";
  window.workContinueOnFailure = () => {
    try { return localStorage.getItem(COF_KEY) !== "0"; } catch (_) { return true; }
  };
  window.setWorkContinueOnFailure = (v) => {
    try { localStorage.setItem(COF_KEY, v ? "1" : "0"); } catch (_) {}
  };

  // ---------- dispatch ----------
  async function dispatch(text, route) {
    if (route.kind === "mission" && window.__xodCreateMission) {
      const m = await window.__xodCreateMission(text, window.workContinueOnFailure());
      if (m) {
        if (typeof showToast === "function") showToast("On it — Xod planned the steps and started.");
        return true;
      }
      // Planning failed server-side: fall through to a plain task
      // so the job itself is never lost.
    }
    if (window.__xodCreateTask) return window.__xodCreateTask(text);
    const res = await api("/api/tasks", { method: "POST", body: JSON.stringify({ request: text }) });
    return !!res.success;
  }

  // ---------- the plain-words plan check ----------
  let pending = null;   // { text, route }

  function showPlanCard(text, route, steps, loading) {
    const card = $("workPlanCard");
    if (!card) return;
    pending = { text, route };
    const reason = window.WorkRouter ? WorkRouter.confirmReason(route) : "Check the plan before it runs.";
    let body = "";
    if (loading) {
      body = `<p class="text-[13px] text-zinc-400 mt-2">Xod is working out the steps…</p>`;
    } else if (steps && steps.length) {
      body = `<p class="text-[13px] text-zinc-200 mt-2">Here's what Xod will do:</p>
        <ol class="list-decimal ml-5 mt-1 space-y-0.5 text-[13px] text-zinc-300">
          ${steps.map((s) => `<li>${esc(s.title)}</li>`).join("")}
        </ol>`;
    } else if (route.kind === "mission") {
      body = `<p class="text-[13px] text-zinc-200 mt-2">Xod will plan the steps for: “${esc(text)}”</p>`;
    } else {
      body = `<p class="text-[13px] text-zinc-200 mt-2">Xod will do this: “${esc(text)}”</p>`;
    }
    card.innerHTML = `
      <div class="rounded-lg border border-amber-400/30 bg-amber-400/[0.06] px-3 py-2.5">
        <p class="text-[13px] font-medium text-amber-100">${esc(reason)}</p>
        ${body}
        <div class="flex flex-wrap gap-2 mt-3">
          <button type="button" class="task-btn task-btn-primary" onclick="confirmWorkPlan()" ${loading ? "disabled" : ""}>Go</button>
          <button type="button" class="task-btn task-btn-quiet" onclick="cancelWorkPlan()">Not yet</button>
        </div>
      </div>`;
    card.classList.remove("hidden");
    if (window.lucide) lucide.createIcons();
  }

  function hidePlanCard() {
    const card = $("workPlanCard");
    if (card) { card.classList.add("hidden"); card.innerHTML = ""; }
  }

  window.confirmWorkPlan = async () => {
    const p = pending;
    pending = null;
    hidePlanCard();
    if (p) await dispatch(p.text, p.route);
  };
  window.cancelWorkPlan = () => {
    pending = null;
    hidePlanCard();
    const input = $("workInput");
    if (input) input.focus();
  };

  // ---------- the one composer ----------
  window.submitWorkText = async (text) => {
    text = String(text || "").trim();
    if (!text || !window.WorkRouter) return false;
    const route = WorkRouter.classifyWorkRequest(text);
    if (!route) return false;
    if (!route.needsConfirm) { await dispatch(text, route); return true; }
    // Big or sensitive: show the plan in plain words first.
    if (typeof openTasksPanel === "function") openTasksPanel();
    if (route.kind === "mission") {
      showPlanCard(text, route, null, true);
      let steps = null;
      try {
        const res = await api("/api/missions/plan", { method: "POST", body: JSON.stringify({ goal: text }) });
        if (res.success && Array.isArray(res.steps) && res.steps.length) steps = res.steps;
      } catch (_) { /* no preview: the confirm card falls back to the goal in plain words */ }
      if (pending) showPlanCard(text, route, steps, false);
    } else {
      showPlanCard(text, route, null, false);
    }
    return true;
  };

  window.submitWorkFromPanel = async () => {
    const input = $("workInput");
    if (!input) return;
    const text = input.value.trim();
    if (!text) return input.focus();
    if (await window.submitWorkText(text)) input.value = "";
  };

  // ---------- the status line ----------
  // One human sentence next to Xod's face, on every surface:
  // "All good." / "Working on 2 things." / "Needs you: 1".
  let lastStatusFetch = 0;
  async function refreshStatusLine(force) {
    const now = Date.now();
    if (!force && now - lastStatusFetch < 4000) return;
    lastStatusFetch = now;
    const line = $("xodStatusText");
    if (!line) return;
    try {
      const [counts, ms] = await Promise.all([api("/api/tasks/counts"), api("/api/missions")]);
      const missions = (Array.isArray(ms.missions) ? ms.missions : []).filter((m) => !m.scratch);
      const needs = (counts.waiting || 0) + missions.filter((m) => m.state === "paused").length;
      const working = (counts.working || 0) + (counts.queued || 0)
        + missions.filter((m) => m.state === "planning" || m.state === "running").length;
      window.__xodStatus = { needsYou: needs, working };
      line.textContent = WorkRouter.statusLineText({ needsYou: needs, working });
      const dot = $("xodStatusDot");
      if (dot) dot.className = `size-2 rounded-full ${needs ? "bg-amber-400" : working ? "bg-sky-400" : "bg-emerald-400"}`;
      const detail = $("xodStatusDetailLine");
      if (detail) {
        detail.textContent = needs
          ? `${needs} ${needs === 1 ? "thing needs" : "things need"} your decision. Open Work to answer.`
          : working
            ? `Xod is working on ${working} ${working === 1 ? "thing" : "things"}. Nothing needs you right now.`
            : "All good. Nothing needs you right now.";
      }
    } catch (_) { /* keep the last known line rather than guessing */ }
  }
  window.__xodRefreshStatusLine = () => refreshStatusLine(false);

  window.toggleStatusDetail = () => {
    const p = $("xodStatusPanel");
    if (!p) return;
    p.classList.toggle("hidden");
    if (!p.classList.contains("hidden")) refreshStatusLine(true);
  };
  document.addEventListener("click", (e) => {
    const p = $("xodStatusPanel");
    if (p && !p.classList.contains("hidden") && !e.target.closest("#xodStatusPanel, #xodStatusBtn")) p.classList.add("hidden");
  });

  // ---------- init ----------
  const chk = $("missionContinueChk");
  if (chk) chk.checked = window.workContinueOnFailure();
  refreshStatusLine(true);
  setInterval(() => refreshStatusLine(false), 8000);
})();
