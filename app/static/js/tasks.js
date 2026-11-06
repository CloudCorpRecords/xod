// ==============================================================
// TASKS: jobs Xod does in the background (see services/task_queue.py)
// ==============================================================
(() => {
  let tasks = [];
  let lastStatus = {};          // id -> status, to notice changes
  let pollTimer = null;
  let lastRendered = "";        // skip re-rendering when nothing changed (keeps scroll, focus, open reports)
  const drafts = {};            // id -> unsent answer text, kept across refreshes

  const $ = (id) => document.getElementById(id);
  const esc = (s) => (typeof escapeHtml === "function" ? escapeHtml(String(s ?? "")) : String(s ?? ""));
  const md = (s) => { try { return marked.parse(s || ""); } catch (_) { return esc(s); } };
  const ago = (t) => {
    const s = Math.max(1, Math.round(Date.now() / 1000 - t));
    return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`;
  };

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    return res.json().catch(() => ({}));
  }

  // ---------- open / close ----------
  window.openTasksPanel = () => {
    lastRendered = "";
    $("tasksPanel").classList.remove("hidden");
    requestAnimationFrame(() => $("tasksPanel").classList.add("is-open"));
    refresh();
    schedule(3000);
  };
  window.closeTasksPanel = () => {
    $("tasksPanel").classList.remove("is-open");
    setTimeout(() => $("tasksPanel").classList.add("hidden"), 250);
    schedule(10000);
  };
  const isOpen = () => !$("tasksPanel").classList.contains("hidden");

  function schedule(ms) {
    clearInterval(pollTimer);
    pollTimer = setInterval(refresh, ms);
  }

  // ---------- data ----------
  async function refresh() {
    const data = await api("/api/tasks");
    if (!data.tasks) return;
    noticeChanges(data.tasks);
    tasks = data.tasks;
    const c = data.counts || {};
    window.__xodTasksWorking = (c.working || 0) > 0;   // the moon looks busy while tasks run
    const badge = $("tasksBadge");
    if (badge) {
      const waiting = c.waiting || 0;
      badge.classList.toggle("hidden", !waiting && !c.working);
      badge.classList.toggle("is-working", !waiting && !!c.working);
      badge.textContent = waiting ? String(waiting) : "";
      badge.title = waiting ? `${waiting} need${waiting === 1 ? "s" : ""} you` : "Working";
    }
    if (window.__xodRefreshStatusLine) window.__xodRefreshStatusLine();
    if (isOpen()) render();
  }

  function noticeChanges(next) {
    for (const t of next) {
      const before = lastStatus[t.id];
      if (before && before !== t.status) {
        if (t.status === "waiting") toast(`“${t.title}” needs you`, () => openTasksPanel());
        if (t.status === "done") toast(`Done: ${t.title}`, () => openTasksPanel());
        if (t.status === "failed") toast(`Couldn't finish: ${t.title}`, () => openTasksPanel());
      }
      lastStatus[t.id] = t.status;
    }
  }

  function toast(text, onClick) {
    const el = document.createElement("button");
    el.className = "task-toast";
    el.textContent = text;
    el.onclick = () => { el.remove(); onClick && onClick(); };
    document.body.appendChild(el);
    requestAnimationFrame(() => el.classList.add("is-visible"));
    setTimeout(() => { el.classList.remove("is-visible"); setTimeout(() => el.remove(), 300); }, 6000);
  }

  // ---------- render ----------
  function render() {
    const list = $("tasksList");
    if (!list) return;
    const signature = JSON.stringify(tasks) + "|" + (window.__xodMissionsSignature || "");
    if (signature === lastRendered && list.childElementCount) return;
    lastRendered = signature;
    const openIds = new Set([...list.querySelectorAll("details[open][data-id]")].map((d) => d.dataset.id));
    const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.task : null;
    const scroll = list.scrollTop;
    // Keep any half-typed answers.
    list.querySelectorAll("textarea[data-task]").forEach((ta) => { drafts[ta.dataset.task] = ta.value; });

    // Phase 1: the Tasks panel is the Work feed. Missions publish their
    // cards into the same three sections via window.__xodMissionBuckets
    // (missions.js), so "Needs you" approvals are pinned at the very top
    // of the whole feed, then running jobs, then finished results.
    const mb = window.__xodMissionBuckets || { needs: "", running: "", finished: "", counts: { needs: 0, running: 0, finished: 0 } };
    // Phase 2: scratch/proving jobs (origin stamp, services/task_queue.py)
    // never appear in the Work feed; they live in the Playground
    // (Settings - Advanced). Display-only; nothing is deleted.
    const real = tasks.filter((t) => !t.scratch);
    window.__xodScratchTaskCount = tasks.length - real.length;
    const groups = [
      ["Needs you", real.filter((t) => t.status === "waiting"), waitingCard, mb.needs, mb.counts.needs],
      ["Working", real.filter((t) => ["working", "queued", "paused"].includes(t.status)), (t) => (t.status === "queued" ? queuedCard(t) : t.status === "paused" ? pausedCard(t) : workingCard(t)), mb.running, mb.counts.running],
      ["Finished", real.filter((t) => ["done", "failed", "cancelled"].includes(t.status)), finishedCard, mb.finished, mb.counts.finished],
    ];
    const html = groups.filter(([, items, , extra]) => items.length || extra).map(([name, items, card, extra, extraCount]) => `
      <section>
        <h4 class="tasks-group">${name} <span>${items.length + (extraCount || 0)}</span></h4>
        <div class="space-y-2.5">${items.map(card).join("")}${extra || ""}</div>
      </section>`).join("");
    list.innerHTML = html || `
      <div class="text-center py-14 px-6">
        <p class="text-zinc-200 text-[14px]">Nothing on your plate.</p>
        <p class="text-zinc-500 text-[12px] mt-1">Give Xod a job above — anything that takes clicking around: applications, price checks, sign-ups, messages.</p>
      </div>`;
    list.querySelectorAll("textarea[data-task]").forEach((ta) => { ta.value = drafts[ta.dataset.task] || ""; });
    list.querySelectorAll("details[data-id]").forEach((d) => { if (openIds.has(d.dataset.id)) d.open = true; });
    if (focused) { const ta = list.querySelector(`textarea[data-task="${focused}"]`); if (ta) ta.focus(); }
    list.scrollTop = scroll;
    if (window.lucide) lucide.createIcons();
  }

  const shot = (t) => t.has_screenshot
    ? `<button class="task-shot" onclick="viewTaskShot('${t.id}')" title="See the page Xod paused on">
         <img src="/api/tasks/${t.id}/screenshot?t=${Math.round(t.updated_at)}" alt="The page Xod paused on" loading="lazy"></button>` : "";

  function waitingCard(t) {
    const d = t.waiting_detail || {};
    let body = "", actions = "";
    if (t.waiting_kind === "approval") {
      body = `<p class="text-[13px] text-zinc-200">Ready to press <b>“${esc(d.label || "Submit")}”</b>${d.page ? ` on ${esc(d.page)}` : ""}.</p>`;
      actions = `
        <button class="task-btn task-btn-primary" onclick="taskAction('${t.id}','approve')">Approve</button>
        <button class="task-btn" onclick="answerTaskPrompt('${t.id}')">Change something</button>
        <button class="task-btn task-btn-quiet" onclick="taskAction('${t.id}','cancel')">Cancel</button>`;
    } else if (t.waiting_kind === "question") {
      body = `<div class="task-md text-[13px]">${md(d.message || "I have a question.")}</div>
        <textarea data-task="${t.id}" rows="2" class="task-answer" placeholder="Your answer…"
          onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendTaskAnswer('${t.id}')}"></textarea>`;
      actions = `<button class="task-btn task-btn-primary" onclick="sendTaskAnswer('${t.id}')">Send answer</button>
        <button class="task-btn task-btn-quiet" onclick="taskAction('${t.id}','cancel')">Cancel</button>`;
    } else if (t.waiting_kind === "maintenance") {
      // Phase 5: a self-maintenance fault Xod already tried to repair
      // itself. One calm sentence; the technical record sits in Details;
      // Fix it exists only where a manual path exists.
      body = `<p class="text-[13px] text-zinc-200">${esc(d.message || t.title)}</p>
        ${d.detail ? `<details class="mt-1"><summary class="text-[12px] text-zinc-500 cursor-pointer">Details</summary><p class="text-[12px] text-zinc-400 mt-1">${esc(d.detail)}</p></details>` : ""}`;
      actions = `
        ${d.fix ? `<button class="task-btn task-btn-primary" onclick="fixMaintenance('${t.id}')">Fix it</button>` : ""}
        <button class="task-btn task-btn-quiet" onclick="taskAction('${t.id}','delete')">Remove</button>`;
    } else {
      body = `<p class="text-[13px] text-zinc-200">The site is showing a verification check I won't try to get around.</p>`;
      actions = `<button class="task-btn task-btn-primary" onclick="showTaskPage('${t.id}')">Open the page</button>
        <button class="task-btn" onclick="taskAction('${t.id}','retry')">I cleared it, continue</button>
        <button class="task-btn task-btn-quiet" onclick="taskAction('${t.id}','cancel')">Cancel</button>`;
    }
    return `<article class="task-card is-waiting">
      <div class="flex gap-3">
        <div class="min-w-0 flex-1 space-y-2">
          <p class="task-title">${esc(t.title)}</p>
          ${body}
        </div>
        ${shot(t)}
      </div>
      <div class="flex flex-wrap gap-2 mt-3">${actions}</div>
    </article>`;
  }

  // Progress notes (status lines the agent wrote while working), newest last.
  function progressList(t, limit) {
    const notes = (Array.isArray(t.history) ? t.history : []).slice(-limit);
    if (!notes.length) return "";
    return `<ol class="task-progress">${notes.map((n) =>
      `<li><span>${esc(n.text)}</span><time>${ago(n.at)}</time></li>`).join("")}</ol>`;
  }

  // The task's durable plan (services/orchestrator.py), one line per step, live.
  const PLAN_MARK = { completed: "✓", in_progress: "…", pending: "·", blocked: "✗", cancelled: "–" };
  function planList(t) {
    const items = (t.plan_json && t.plan_json.items) || [];
    if (!items.length) return t.mode === "stepwise" ? `<p class="text-[12px] text-zinc-500 mt-2">Planning…</p>` : "";
    return `<ol class="task-plan mt-2 space-y-1 text-[12px]">${items.map((i) => `
      <li class="plan-${i.status}" title="${esc(i.note || "")}">
        <span class="inline-block w-4 text-center">${PLAN_MARK[i.status] || "·"}</span>
        <span class="${i.status === "cancelled" ? "line-through text-zinc-500" : i.status === "in_progress" ? "text-zinc-100" : "text-zinc-300"}">${esc(i.text)}</span>
        ${i.blocker ? `<div class="ml-5 text-amber-300/90">${esc(i.blocker)}</div>` : ""}
      </li>`).join("")}</ol>
      ${t.plan_version ? `<p class="text-[10px] text-zinc-600 mt-1">plan v${t.plan_version}</p>` : ""}`;
  }
  // Steers as they stand: queued is not applied.
  function steerList(t) {
    const st = (t.steers || []).filter((s) => s.state === "queued" || Date.now() / 1000 - (s.applied_at || 0) < 120);
    if (!st.length) return "";
    return `<ul class="mt-2 space-y-0.5 text-[11px]">${st.map((s) => `<li class="${s.state === "queued" ? "text-amber-300" : "text-emerald-300/80"}">
      ${s.state === "queued" ? "Queued, not applied yet" : "Applied"}: ${esc(s.kind)}${s.body ? ` “${esc(s.body.slice(0, 80))}”` : ""}${s.applied_note && s.state !== "queued" ? ` — ${esc(s.applied_note.slice(0, 120))}` : ""}</li>`).join("")}</ul>`;
  }
  const steerButtons = (t, paused) => t.mode !== "stepwise" ? "" : `
      <div class="flex flex-wrap gap-2 mt-2">
        ${paused ? `<button class="task-btn task-btn-primary" onclick="steerTask('${t.id}','resume')">Resume</button>`
                 : `<button class="task-btn" onclick="steerTask('${t.id}','pause')">Pause</button>`}
        <button class="task-btn" onclick="steerTask('${t.id}','redirect')">Change target</button>
        <button class="task-btn" onclick="steerTask('${t.id}','steer')">Add instruction</button>
      </div>`;
  function latestNote(t) {
    const notes = Array.isArray(t.history) ? t.history : [];
    return notes.length ? notes[notes.length - 1].text : "";
  }

  function workingCard(t) {
    return `<article class="task-card is-working">
      <div class="flex items-start justify-between gap-3">
        <p class="task-title">${esc(t.title)}</p>
        <button class="task-btn task-btn-quiet shrink-0" onclick="taskAction('${t.id}','cancel')">Stop</button>
      </div>
      <p class="task-now"><span class="task-pulse"></span>${esc(t.now || latestNote(t) || "Working…")}</p>
      ${t.mode === "stepwise" ? planList(t) + steerList(t) + steerButtons(t, false) + `<p class="text-[11px] text-zinc-500 mt-1">${t.id} · ${t.steps || 0} tool steps · started ${ago(t.created_at)}${t.last_progress_at ? ` · last activity ${ago(t.last_progress_at)}` : ""}</p>` : `${(t.history || []).length ? `<details class="mt-1" data-id="${t.id}"><summary class="text-[12px] text-zinc-500 cursor-pointer">Details</summary>${progressList(t, 40)}</details>` : ""}<p class="text-[11px] text-zinc-500 mt-1">${t.steps || 0} steps · started ${ago(t.created_at)}</p>`}
    </article>`;
  }

  function pausedCard(t) {
    return `<article class="task-card">
      <div class="flex items-start justify-between gap-3">
        <p class="task-title">${esc(t.title)}</p>
        <button class="task-btn task-btn-quiet shrink-0" onclick="taskAction('${t.id}','cancel')">Cancel</button>
      </div>
      <p class="text-[12px] text-zinc-400 mt-1">Paused. Nothing is running for it.</p>
      ${planList(t)}${steerList(t)}${steerButtons(t, true)}
    </article>`;
  }

  function queuedCard(t) {
    return `<article class="task-card">
      <div class="flex items-center justify-between gap-3">
        <p class="task-title">${esc(t.title)}</p>
        <button class="task-btn task-btn-quiet shrink-0" onclick="taskAction('${t.id}','cancel')">Cancel</button>
      </div>
      <p class="text-[12px] text-zinc-500 mt-1">${esc(t.now || "Waiting its turn")}</p>
      ${t.mode === "stepwise" ? `<p class="text-[11px] text-emerald-300/80 mt-1">Acknowledged · ${t.id}</p>` + planList(t) + steerList(t) : ""}
    </article>`;
  }


  // Phase 0 (simplicity redesign): a failure headline is ONE plain sentence.
  // The full technical text is never rewritten or dropped; it lives verbatim
  // under the card's Details disclosure. Keep jargon ("capability contract",
  // "dispatch", tool lists) out of the sentence: map what we recognise, and
  // fall back to a calm generic line rather than quoting the raw error.
  function plainFailureSentence(task) {
    const raw = `${task.result || ""} ${task.now || ""}`;
    if (/capability contract|declared capability|dispatched|contract equips/i.test(raw))
      return "A tool Xod needed was missing from the plan.";
    if (/not installed|no module named|command not found|ENOENT|no such file or directory/i.test(raw))
      return "A tool Xod needed isn't installed on this computer.";
    if (/timed out|timeout|deadline/i.test(raw))
      return "It took too long, so Xod stopped.";
    if (/usage limit|rate limit|quota|out of usage/i.test(raw))
      return "Xod hit a usage limit and had to stop.";
    if (/permission|not allowed|denied/i.test(raw))
      return "Xod wasn't allowed to do one of the steps.";
    if (/network|connection|offline|unreachable/i.test(raw))
      return "Xod couldn't reach something it needed.";
    return "Xod couldn't finish this one.";
  }

  function failedCard(t) {
    return `<article class="task-card is-failed">
      <div class="flex items-center justify-between gap-3">
        <span class="task-title truncate">${esc(t.title)}</span>
        <span class="task-state">Didn't finish · ${ago(t.finished_at || t.updated_at)}</span>
      </div>
      <p class="text-[13px] text-zinc-200 mt-1.5">${esc(plainFailureSentence(t))}</p>
      <details class="mt-2" data-id="${t.id}">
        <summary class="text-[12px] text-zinc-400 cursor-pointer">Details</summary>
        <div class="mt-2 space-y-3">
          ${t.result ? `<div class="task-md prose-custom text-[13px]">${md(t.result)}</div>` : ""}
          ${(t.history || []).length ? `<details><summary class="text-[12px] text-zinc-400 cursor-pointer">How it went (${t.history.length} notes)</summary>${progressList(t, 40)}</details>` : ""}
        </div>
      </details>
      <div class="flex flex-wrap gap-2 mt-3">
        <button class="task-btn task-btn-primary" onclick="taskAction('${t.id}','retry')">Try again</button>
        <button class="task-btn task-btn-quiet" onclick="taskAction('${t.id}','delete')">Remove</button>
      </div>
    </article>`;
  }

  // Phase 1: a finished job reads as an outcome ("Done. The folder is
  // sorted."), one line, with the full report one tap down in Details.
  function outcomeCard(t) {
    const outcome = (window.WorkRouter && WorkRouter.plainOutcome) ? WorkRouter.plainOutcome(t) : `Done: ${t.title}`;
    return `<article class="task-card is-done">
      <div class="flex items-center justify-between gap-3">
        <span class="task-title truncate">${esc(outcome)}</span>
        <span class="task-state">${ago(t.finished_at || t.updated_at)}</span>
      </div>
      ${outcome !== `Done: ${t.title}` ? `<p class="text-[12px] text-zinc-500 mt-0.5 truncate">${esc(t.title)}</p>` : ""}
      <details class="mt-2" data-id="${t.id}">
        <summary class="text-[12px] text-zinc-400 cursor-pointer">Details</summary>
        <div class="mt-2 space-y-3">
          ${t.result ? `<div class="task-md prose-custom text-[13px]">${md(t.result)}</div>` : ""}
          ${(t.history || []).length ? `<details><summary class="text-[12px] text-zinc-400 cursor-pointer">How it went (${t.history.length} notes)</summary>${progressList(t, 40)}</details>` : ""}
        </div>
      </details>
      <div class="flex flex-wrap gap-2 mt-3">
        ${!String(t.title).startsWith("↻") ? `<button class="task-btn" onclick="openRoutineEditor({source:'task:${t.id}'})">Save as routine</button>` : ""}
        <button class="task-btn task-btn-quiet" onclick="taskAction('${t.id}','delete')">Remove</button>
      </div>
    </article>`;
  }

  function finishedCard(t) {
    if (t.status === "failed") return failedCard(t);
    if (t.status === "done") return outcomeCard(t);
    const label = t.status === "done" ? "Done" : t.status === "failed" ? "Didn't finish" : "Cancelled";
    return `<details class="task-card is-${t.status}" data-id="${t.id}">
      <summary class="flex items-center justify-between gap-3 cursor-pointer">
        <span class="task-title truncate">${esc(t.title)}</span>
        <span class="task-state">${label} · ${ago(t.finished_at || t.updated_at)}</span>
      </summary>
      <div class="mt-3 space-y-3">
        ${t.result ? `<div class="task-md prose-custom text-[13px]">${md(t.result)}</div>` : ""}
        ${t.mode === "stepwise" && !t.result ? planList(t) : ""}
        ${(t.history || []).length ? `<details><summary class="text-[12px] text-zinc-400 cursor-pointer">How it went (${t.history.length} notes)</summary>${progressList(t, 40)}</details>` : ""}
        <div class="flex flex-wrap gap-2">
          ${t.status !== "done" ? `<button class="task-btn" onclick="taskAction('${t.id}','retry')">Try again</button>` : ""}
          ${t.status === "done" && !String(t.title).startsWith("↻") ? `<button class="task-btn" onclick="openRoutineEditor({source:'task:${t.id}'})">Save as routine</button>` : ""}
          <button class="task-btn task-btn-quiet" onclick="taskAction('${t.id}','delete')">Remove</button>
        </div>
      </div>
    </details>`;
  }

  // ---------- actions ----------
  window.taskAction = async (id, action) => {
    const res = action === "delete"
      ? await api(`/api/tasks/${id}`, { method: "DELETE" })
      : await api(`/api/tasks/${id}/${action}`, { method: "POST" });
    if (res && res.success === false && res.error) alert(res.error);
    refresh();
  };

  window.steerTask = async (id, kind) => {
    let body = "";
    if (kind === "redirect" || kind === "steer") {
      body = prompt(kind === "redirect" ? "What should it aim at instead? (finished steps are kept)" : "What should it also do or keep in mind?");
      if (!body) return;
    }
    const res = await api(`/api/tasks/${id}/steer`, { method: "POST", body: JSON.stringify({ kind, body }) });
    if (res.success === false) alert(res.error);
    else toast(res.state === "applied" ? `Applied: ${res.note || kind}` : `Queued: ${kind}, applies ${res.applies || "at the next step"}`);
    lastRendered = "";
    refresh();
  };

  // Phase 5: the Fix it button on a self-maintenance Needs-you card.
  window.fixMaintenance = async (id) => {
    const res = await api("/api/maintenance/fix", { method: "POST", body: JSON.stringify({ task_id: id }) });
    if (res && res.ok === false && res.detail) alert(res.detail);
    refresh();
  };

  window.sendTaskAnswer = async (id) => {
    const ta = document.querySelector(`textarea[data-task="${id}"]`);
    const text = (ta && ta.value.trim()) || "";
    if (!text) return ta && ta.focus();
    delete drafts[id];
    const res = await api(`/api/tasks/${id}/answer`, { method: "POST", body: JSON.stringify({ text }) });
    if (res.success === false) alert(res.error);
    refresh();
  };

  window.answerTaskPrompt = async (id) => {
    const text = prompt("What should Xod change before sending?");
    if (!text) return;
    await api(`/api/tasks/${id}/answer`, { method: "POST", body: JSON.stringify({ text }) });
    refresh();
  };

  window.showTaskPage = async (id) => {
    await api(`/api/tasks/${id}/show`, { method: "POST" });
    closeTasksPanel();
    if (typeof openAutonomousCockpitModal === "function") openAutonomousCockpitModal();
  };

  window.viewTaskShot = (id) => {
    $("taskShotImg").src = `/api/tasks/${id}/screenshot?t=${Date.now()}`;
    $("taskShotViewer").classList.remove("hidden");
  };

  async function createTask(text) {
    // Orchestrated: the ack (task id + scope) comes back before anything runs.
    const res = await api("/api/orchestrator/tasks", { method: "POST", body: JSON.stringify({ request: text }) });
    if (!res.success) { alert((res.detail || res.error) || "Couldn't add that task."); return false; }
    lastStatus[res.ack.task_id] = res.ack.status;
    toast(`Acknowledged ${res.ack.task_id}: ${res.ack.scope}`, () => openTasksPanel());
    refresh();
    return true;
  }

  // Phase 1: the one composer. Both the Work panel's box and the chat
  // composer's hand-off button go through submitWorkText (work.js),
  // which routes task vs mission internally; createTask stays as the
  // fallback if work.js failed to load, so the capability never shrinks.
  window.__xodCreateTask = createTask;
  window.__xodRenderWorkFeed = () => { lastRendered = ""; render(); };

  window.createTaskFromPanel = async () => {
    const input = $("workInput") || $("newTaskInput");
    if (!input) return;
    const text = input.value.trim();
    if (!text) return input.focus();
    if (window.submitWorkText) { if (await window.submitWorkText(text)) input.value = ""; }
    else if (await createTask(text)) input.value = "";
  };

  window.handOffFromComposer = async () => {
    const input = $("chatInput");
    const text = (input && input.value.trim()) || "";
    if (!text) {
      openTasksPanel();
      setTimeout(() => { const w = $("workInput"); if (w) w.focus(); }, 260);
      return;
    }
    if (window.submitWorkText) {
      if (await window.submitWorkText(text)) {
        input.value = "";
        input.dispatchEvent(new Event("input"));
      }
    } else if (await createTask(text)) {
      input.value = "";
      input.dispatchEvent(new Event("input"));
    }
  };

  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && isOpen()) closeTasksPanel(); });
  refresh();
  schedule(10000);
})();
