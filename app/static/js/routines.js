// Routines: teach once, repeat by hand or on a schedule (services/routines.py).
// "Save as routine" on a chat reply or finished task opens the editor prefilled with the request
// and the steps that worked; the drawer lists routines with Run now / pause / edit / delete.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const api = async (url, method = "GET", body) =>
    (await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined })).json();

  // ---------------------------------------------------------------- drawer
  async function renderList() {
    const body = document.getElementById("routinesBody");
    if (!body) return;
    const { routines } = await api("/api/routines");
    if (!routines.length) {
      body.innerHTML = `<div class="text-center py-10 space-y-2">
        <p class="text-[13px] text-zinc-300">No routines yet.</p>
        <p class="text-[12px] text-zinc-500">Do something once (in a chat or as a task), then press <b class="text-zinc-300">Save as routine</b> under the answer. Xod remembers the steps and can repeat them on a schedule.</p>
        <button data-rt="new" class="mt-2 px-3 py-1.5 rounded-lg text-[12px] bg-surface-700 text-white hover:bg-surface-600">Write one from scratch</button></div>`;
      return;
    }
    body.innerHTML = routines.map((r) => {
      const next = r.enabled && r.next_run_at ? new Date(r.next_run_at * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" }) : null;
      const last = r.last_run_at ? `Last run ${new Date(r.last_run_at * 1000).toLocaleDateString()}${r.last_status && r.last_status !== "running" ? ` · ${r.last_status === "done" ? "worked" : r.last_status}` : r.last_status === "running" ? " · running now" : ""}` : "Never run";
      return `<div class="task-card space-y-2 ${r.enabled ? "" : "opacity-60"}" data-id="${esc(r.id)}">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="task-title">${esc(r.name)}</p>
            <p class="text-[12px] text-zinc-400">${esc(r.schedule_text)}${next ? ` · next ${esc(next)}` : ""}</p>
          </div>
          <label class="flex items-center gap-1.5 text-[11px] text-zinc-400 shrink-0 cursor-pointer">
            <input type="checkbox" data-rt="toggle" ${r.enabled ? "checked" : ""} class="accent-amber-400"> On
          </label>
        </div>
        <p class="text-[12px] text-zinc-500">${esc(last)} · ${r.steps.length} learned step${r.steps.length === 1 ? "" : "s"} · ran ${r.runs}×</p>
        <div class="flex flex-wrap gap-2">
          <button class="task-btn" data-rt="run">Run now</button>
          <button class="task-btn task-btn-quiet" data-rt="edit">Edit</button>
          <button class="task-btn task-btn-quiet" data-rt="delete">Delete</button>
        </div>
      </div>`;
    }).join("");
  }

  window.openRoutinesPanel = () => {
    let root = document.getElementById("routinesPanel");
    if (!root) {
      root = document.createElement("div");
      root.id = "routinesPanel";
      root.className = "tasks-panel";
      root.innerHTML = `
        <div class="tasks-scrim" data-rt="close"></div>
        <aside class="tasks-drawer" role="dialog" aria-label="Routines">
          <header class="px-5 pt-5 pb-3 flex items-start justify-between gap-3 border-b border-white/[0.06]">
            <div>
              <h3 class="font-bold text-lg text-white">Routines</h3>
              <p class="text-[12px] text-zinc-400 mt-0.5">Jobs Xod repeats for you. Each run shows up in Tasks and still asks before sending or paying.</p>
            </div>
            <div class="flex items-center gap-1">
              <button data-rt="new" class="px-2.5 py-1 rounded-lg text-[12px] text-zinc-300 hover:text-white hover:bg-surface-700">New</button>
              <button data-rt="close" class="p-1 -mr-1 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700" title="Close"><i data-lucide="x" class="size-4"></i></button>
            </div>
          </header>
          <div id="routinesBody" class="flex-1 overflow-y-auto px-5 py-4 space-y-3"></div>
        </aside>`;
      document.body.appendChild(root);
      root.addEventListener("click", onDrawerClick);
      root.addEventListener("change", onDrawerClick);
      if (window.lucide) lucide.createIcons();
    }
    root.classList.remove("hidden");
    requestAnimationFrame(() => root.classList.add("is-open"));
    renderList();
  };
  const closeDrawer = () => {
    const root = document.getElementById("routinesPanel");
    if (!root) return;
    root.classList.remove("is-open");
    setTimeout(() => root.classList.add("hidden"), 250);
  };

  async function onDrawerClick(e) {
    const el = e.target.closest("[data-rt]");
    if (!el) return;
    const act = el.dataset.rt, card = el.closest("[data-id]"), id = card?.dataset.id;
    if (act === "close") return closeDrawer();
    if (act === "new") return openRoutineEditor({});
    if (act === "toggle" && e.type === "change") { await api(`/api/routines/${id}`, "PATCH", { enabled: el.checked }); return renderList(); }
    if (act === "run" && e.type === "click") {
      const r = await api(`/api/routines/${id}/run`, "POST");
      showToast(r.success ? "Started. Follow it in Tasks." : r.error);
      return renderList();
    }
    if (act === "edit" && e.type === "click") {
      const { routines } = await api("/api/routines");
      return openRoutineEditor({ routine: routines.find((r) => r.id === id) });
    }
    if (act === "delete" && e.type === "click" && confirm("Delete this routine?")) { await api(`/api/routines/${id}`, "DELETE"); renderList(); }
  }

  // ---------------------------------------------------------------- editor
  window.openRoutineEditor = async ({ source, routine } = {}) => {
    let data = routine ? { name: routine.name, instructions: routine.instructions, steps: routine.steps } : { name: "", instructions: "", steps: [] };
    if (source && !routine) data = await api(`/api/routines/suggest?source=${encodeURIComponent(source)}`);
    const sched = routine?.schedule || { kind: "manual" };
    const kind = sched.kind === "weekly" && (sched.days || []).join() === DAYS.slice(0, 5).join() ? "weekdays" : sched.kind;
    const modal = document.createElement("div");
    modal.id = "routineEditor";
    modal.className = "fixed inset-0 z-[95] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm";
    modal.innerHTML = `
      <div class="w-full max-w-xl max-h-[92vh] overflow-y-auto rounded-3xl bg-surface-850 border border-white/[0.08] shadow-2xl p-6 space-y-4">
        <div class="flex items-center justify-between">
          <h3 class="text-white text-lg font-semibold">${routine ? "Edit routine" : "Save as routine"}</h3>
          <button data-ed="cancel" class="p-1.5 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700"><i data-lucide="x" class="size-4"></i></button>
        </div>
        <label class="block"><span class="text-[12px] text-zinc-400">Name</span>
          <input id="rtName" value="${esc(data.name)}" placeholder="e.g. Morning news brief" class="mt-1 w-full bg-surface-900 border border-surface-700 rounded-lg px-3 py-2 text-[13px] text-white focus:outline-none focus:border-brand-500"></label>
        <label class="block"><span class="text-[12px] text-zinc-400">What should Xod do?</span>
          <textarea id="rtInstr" rows="3" class="mt-1 w-full bg-surface-900 border border-surface-700 rounded-lg px-3 py-2 text-[13px] text-white focus:outline-none focus:border-brand-500">${esc(data.instructions)}</textarea></label>
        <div>
          <span class="text-[12px] text-zinc-400">Steps that worked last time ${data.steps.length ? "(Xod uses them as a guide; remove any that shouldn't repeat)" : ""}</span>
          <ol id="rtSteps" class="mt-1 space-y-1">${data.steps.length ? data.steps.map((s, i) => `<li class="flex items-start gap-2 text-[12px] text-zinc-300 bg-surface-900/60 rounded-lg px-2.5 py-1.5"><span class="text-zinc-500">${i + 1}.</span><span class="flex-1 break-words" data-step>${esc(s)}</span><button data-ed="drop" class="text-zinc-500 hover:text-red-300" title="Remove step"><i data-lucide="x" class="size-3.5"></i></button></li>`).join("")
            : `<li class="text-[12px] text-zinc-500">None yet. After the first successful run, Xod fills these in itself.</li>`}</ol>
        </div>
        <div>
          <span class="text-[12px] text-zinc-400">When</span>
          <div class="mt-1 flex flex-wrap items-center gap-2">
            <select id="rtKind" class="bg-surface-900 border border-surface-700 rounded-lg px-2.5 py-1.5 text-[13px] text-white">
              ${[["manual", "Only when I run it"], ["daily", "Every day"], ["weekdays", "Weekdays"], ["weekly", "On certain days"], ["every", "Every few hours"]].map(([v, l]) => `<option value="${v}" ${kind === v ? "selected" : ""}>${l}</option>`).join("")}
            </select>
            <input id="rtTime" type="time" value="${esc(sched.time || "09:00")}" class="bg-surface-900 border border-surface-700 rounded-lg px-2.5 py-1 text-[13px] text-white">
            <span id="rtHoursWrap" class="text-[13px] text-zinc-300">every <input id="rtHours" type="number" min="1" max="168" value="${esc(sched.hours || 6)}" class="w-16 bg-surface-900 border border-surface-700 rounded-lg px-2 py-1 text-white"> hours</span>
          </div>
          <div id="rtDays" class="mt-2 flex gap-1.5">${DAYS.map((d) => `<label class="px-2 py-1 rounded-lg bg-surface-900 border border-surface-700 text-[12px] text-zinc-300 cursor-pointer"><input type="checkbox" value="${d}" ${(sched.days || []).includes(d) ? "checked" : ""} class="mr-1 accent-amber-400">${d[0].toUpperCase() + d.slice(1)}</label>`).join("")}</div>
        </div>
        <p class="text-[11px] text-zinc-500">Each run becomes a task: you'll see progress in Tasks and get a notification. Anything that sends, pays, posts or deletes still waits for your OK.</p>
        <div class="flex justify-end gap-2">
          <button data-ed="cancel" class="px-3 py-2 rounded-lg text-[13px] text-zinc-400 hover:text-white">Cancel</button>
          <button data-ed="save" class="px-4 py-2 rounded-xl bg-brand-500 text-surface-900 text-[13px] font-semibold hover:bg-brand-400">${routine ? "Save" : "Save routine"}</button>
        </div>
      </div>`;
    document.body.appendChild(modal);
    if (window.lucide) lucide.createIcons();
    const sync = () => {
      const k = modal.querySelector("#rtKind").value;
      modal.querySelector("#rtTime").style.display = ["daily", "weekdays", "weekly"].includes(k) ? "" : "none";
      modal.querySelector("#rtHoursWrap").style.display = k === "every" ? "" : "none";
      modal.querySelector("#rtDays").style.display = k === "weekly" ? "flex" : "none";
    };
    modal.querySelector("#rtKind").addEventListener("change", sync);
    sync();
    modal.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-ed]");
      if (!b) return;
      if (b.dataset.ed === "cancel") return modal.remove();
      if (b.dataset.ed === "drop") return b.closest("li").remove();
      if (b.dataset.ed === "save") {
        const k = modal.querySelector("#rtKind").value, time = modal.querySelector("#rtTime").value || "09:00";
        const schedule = k === "manual" ? { kind: "manual" }
          : k === "daily" ? { kind: "daily", time }
          : k === "weekdays" ? { kind: "weekly", time, days: DAYS.slice(0, 5) }
          : k === "weekly" ? { kind: "weekly", time, days: [...modal.querySelectorAll("#rtDays input:checked")].map((i) => i.value) }
          : { kind: "every", hours: +modal.querySelector("#rtHours").value || 6 };
        const payload = { name: modal.querySelector("#rtName").value, instructions: modal.querySelector("#rtInstr").value, schedule,
                          steps: [...modal.querySelectorAll("#rtSteps [data-step]")].map((el) => el.textContent) };
        let r;
        if (routine) r = await api(`/api/routines/${routine.id}`, "PATCH", payload);
        else {
          r = await api("/api/routines", "POST", { ...payload, source });
          if (r.success) await api(`/api/routines/${r.routine.id}`, "PATCH", { steps: payload.steps });  // keep any steps removed in the editor
        }
        if (!r.success) return showToast(r.error || "Couldn't save");
        modal.remove();
        showToast(routine ? "Routine saved" : `Saved. ${r.routine.schedule_text === "When you run it" ? "Run it any time from Routines." : r.routine.schedule_text + "."}`);
        if (document.getElementById("routinesPanel")?.classList.contains("is-open")) renderList();
      }
    });
  };

  // "Save as routine" under a chat reply
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-save-routine]")) {
      if (!currentSessionId) return showToast("Send a message first.");
      openRoutineEditor({ source: `chat:${currentSessionId}` });
    }
  });
})();
