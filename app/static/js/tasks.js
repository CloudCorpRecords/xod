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
    const signature = JSON.stringify(tasks);
    if (signature === lastRendered && list.childElementCount) return;
    lastRendered = signature;
    const openIds = new Set([...list.querySelectorAll("details[open][data-id]")].map((d) => d.dataset.id));
    const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.task : null;
    const scroll = list.scrollTop;
    // Keep any half-typed answers.
    list.querySelectorAll("textarea[data-task]").forEach((ta) => { drafts[ta.dataset.task] = ta.value; });

    const groups = [
      ["Needs you", tasks.filter((t) => t.status === "waiting"), waitingCard],
      ["Working", tasks.filter((t) => t.status === "working"), workingCard],
      ["Up next", tasks.filter((t) => t.status === "queued"), queuedCard],
      ["Finished", tasks.filter((t) => ["done", "failed", "cancelled"].includes(t.status)), finishedCard],
    ];
    const html = groups.filter(([, items]) => items.length).map(([name, items, card]) => `
      <section>
        <h4 class="tasks-group">${name} <span>${items.length}</span></h4>
        <div class="space-y-2.5">${items.map(card).join("")}</div>
      </section>`).join("");
    list.innerHTML = html || `
      <div class="text-center py-14 px-6">
        <p class="text-zinc-200 text-[14px]">Nothing on your plate.</p>
        <p class="text-zinc-500 text-[12px] mt-1">Hand off anything that takes clicking around: applications, price checks, sign-ups, messages.</p>
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

  function workingCard(t) {
    return `<article class="task-card is-working">
      <div class="flex items-start justify-between gap-3">
        <p class="task-title">${esc(t.title)}</p>
        <button class="task-btn task-btn-quiet shrink-0" onclick="taskAction('${t.id}','cancel')">Stop</button>
      </div>
      <p class="task-now"><span class="task-pulse"></span>${esc(t.now || "Working…")}</p>
      ${progressList(t, 5)}
      <p class="text-[11px] text-zinc-500 mt-1">${t.steps || 0} steps · started ${ago(t.created_at)}</p>
    </article>`;
  }

  function queuedCard(t) {
    return `<article class="task-card">
      <div class="flex items-center justify-between gap-3">
        <p class="task-title">${esc(t.title)}</p>
        <button class="task-btn task-btn-quiet shrink-0" onclick="taskAction('${t.id}','cancel')">Cancel</button>
      </div>
      <p class="text-[12px] text-zinc-500 mt-1">${esc(t.now || "Waiting its turn")}</p>
    </article>`;
  }

  function finishedCard(t) {
    const label = t.status === "done" ? "Done" : t.status === "failed" ? "Didn't finish" : "Cancelled";
    return `<details class="task-card is-${t.status}" data-id="${t.id}">
      <summary class="flex items-center justify-between gap-3 cursor-pointer">
        <span class="task-title truncate">${esc(t.title)}</span>
        <span class="task-state">${label} · ${ago(t.finished_at || t.updated_at)}</span>
      </summary>
      <div class="mt-3 space-y-3">
        ${t.result ? `<div class="task-md prose-custom text-[13px]">${md(t.result)}</div>` : ""}
        ${(t.history || []).length ? `<details><summary class="text-[12px] text-zinc-400 cursor-pointer">How it went (${t.history.length} notes)</summary>${progressList(t, 40)}</details>` : ""}
        <div class="flex flex-wrap gap-2">
          ${t.status !== "done" ? `<button class="task-btn" onclick="taskAction('${t.id}','retry')">Try again</button>` : ""}
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
    const res = await api("/api/tasks", { method: "POST", body: JSON.stringify({ request: text }) });
    if (!res.success) { alert((res.detail || res.error) || "Couldn't add that task."); return false; }
    lastStatus[res.task.id] = res.task.status;
    toast(`On it: ${res.task.title}`, () => openTasksPanel());
    refresh();
    return true;
  }

  window.createTaskFromPanel = async () => {
    const input = $("newTaskInput");
    const text = input.value.trim();
    if (!text) return input.focus();
    if (await createTask(text)) input.value = "";
  };

  window.handOffFromComposer = async () => {
    const input = $("chatInput");
    const text = (input && input.value.trim()) || "";
    if (!text) {
      openTasksPanel();
      setTimeout(() => $("newTaskInput").focus(), 260);
      return;
    }
    if (await createTask(text)) {
      input.value = "";
      input.dispatchEvent(new Event("input"));
    }
  };

  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && isOpen()) closeTasksPanel(); });
  refresh();
  schedule(10000);
})();
