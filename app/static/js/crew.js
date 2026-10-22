// The Crew (backend: services/crew.py, /api/crew): coding jobs Xod hands to Muse and conducts natively. Pick a job to
// see every step live, decide what Muse asks (run this? edit that?), answer its questions, send a follow-up or a change
// of course, interrupt or stop it, or start a new job. A chip shows anywhere in the app while Muse is waiting for you.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const DOT = { starting: "bg-sky-400 animate-pulse", working: "bg-emerald-400 animate-pulse", waiting: "bg-amber-400 animate-pulse",
                done: "bg-emerald-600", failed: "bg-red-400", stopped: "bg-zinc-600", interrupted: "bg-zinc-400" };
  const WORD = { starting: "starting", working: "working", waiting: "needs you", done: "done", failed: "failed", stopped: "stopped", interrupted: "paused" };
  const ICON = { tool: "⚙", says: "💬", asks: "✋", you: "🙋", status: "•", error: "⚠" };
  let root = null, current = null, timer = null, chip = null;

  const api = async (url, body) => {
    try {
      const r = await fetch(url, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      return await r.json();
    } catch (e) { return { success: false, error: "Xod didn't answer." }; }
  };
  const ago = (t) => { const s = Math.max(0, Date.now() / 1000 - t); return s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`; };
  const setHtml = (el, html) => { if (el && el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; } };  // no redraw under a click

  function list(jobs) {
    return jobs.length ? jobs.map((j) => `
      <button data-c-pick="${esc(j.id)}" class="w-full text-left px-3 py-2 rounded-lg ${j.id === current ? "bg-surface-700" : "hover:bg-surface-750"}">
        <div class="flex items-center gap-2"><span class="size-2 rounded-full shrink-0 ${DOT[j.status] || DOT.stopped}"></span>
          <span class="text-white text-[13px] truncate">${esc(j.title)}</span>
          <span class="ml-auto text-[11px] ${j.status === "waiting" ? "text-amber-300" : "text-zinc-500"}">${esc(WORD[j.status] || j.status)}</span></div>
        <div class="text-zinc-500 text-[11px] truncate mt-0.5">${esc(j.name)} · ${esc((j.folder || "").replace(/^\/Users\/[^/]+/, "~"))} · ${ago(j.updated)} ago</div>
      </button>`).join("") : `<p class="text-zinc-500 text-[12px] px-3 py-2">No jobs yet. Start one below, or ask Xod ("have muse fix the tests in ~/code/app").</p>`;
  }

  function detail(j) {
    const asks = j.approvals.map((a) => `
      <div class="p-3 rounded-xl bg-amber-500/10 border border-amber-500/30">
        <div class="text-amber-100 text-[13px]">Muse wants to <b>${esc(a.what)}</b></div>
        ${a.warning ? `<div class="text-red-300 text-[12px] mt-1">Safety check: ${esc(a.warning)}</div>` : ""}
        ${a.protected ? `<div class="text-amber-300 text-[12px] mt-1">This writes to a protected place.</div>` : ""}
        <div class="flex gap-1.5 mt-2">
          <button data-c-decide="${esc(a.id)}" data-d="once" class="px-2.5 py-1 rounded-md bg-emerald-500/20 text-emerald-200 hover:bg-emerald-500/30 text-[12px]">Approve once</button>
          <button data-c-decide="${esc(a.id)}" data-d="session" class="px-2.5 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600 text-[12px]">For this job</button>
          <button data-c-decide="${esc(a.id)}" data-d="deny" class="px-2.5 py-1 rounded-md bg-red-500/15 text-red-200 hover:bg-red-500/25 text-[12px]">Deny</button>
        </div></div>`).join("");
    const qs = j.questions.map((q) => `
      <div class="p-3 rounded-xl bg-sky-500/10 border border-sky-500/30" data-c-q="${esc(q.id)}">
        ${q.questions.map((x) => `<div class="text-sky-100 text-[13px]">${esc(x.question || x.header)}</div>
          <div class="flex flex-wrap gap-1.5 mt-1.5">${(x.options || []).map((o) => `<button data-c-answer="${esc(q.id)}" data-qid="${esc(x.id)}" data-choice="${esc(o)}" class="px-2.5 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600 text-[12px]">${esc(o)}</button>`).join("")}
          <input data-c-free="${esc(x.id)}" class="flex-1 min-w-[160px] bg-surface-700 text-white rounded-md px-2 py-1 text-[12px]" placeholder="Or write an answer, Enter sends"></div>`).join("")}
      </div>`).join("");
    const steps = j.steps.map((s) => `
      <div class="flex gap-2 py-1 ${s.kind === "you" ? "text-sky-200" : s.kind === "error" ? "text-red-300" : s.kind === "asks" ? "text-amber-200" : "text-zinc-300"}">
        <span class="w-4 shrink-0 text-center">${ICON[s.kind] || "·"}</span>
        <div class="min-w-0 flex-1"><div class="${s.kind === "says" ? "whitespace-pre-wrap" : "truncate"}">${esc(s.text)}</div>
          ${s.result ? `<pre class="mt-0.5 text-[11px] text-zinc-500 whitespace-pre-wrap max-h-24 overflow-hidden">${esc(s.result)}</pre>` : ""}</div>
        <span class="text-[10px] text-zinc-600 shrink-0">${s.status && s.status !== "completed" ? esc(s.status) : ""}</span>
      </div>`).join("");
    const usage = j.usage && (j.usage.totalTokens || j.usage.inputTokens) ? ` · ${(j.usage.totalTokens || (j.usage.inputTokens || 0) + (j.usage.outputTokens || 0)).toLocaleString()} tokens` : "";
    return `
      <div class="px-4 py-3 border-b border-white/[0.06] flex items-start gap-2">
        <div class="min-w-0 flex-1"><div class="text-white font-medium truncate">${esc(j.title)}</div>
          <div class="text-zinc-500 text-[11px]">${esc(j.name)} · Muse · ${esc(WORD[j.status] || j.status)} · ${esc(j.folder)}${usage}</div></div>
        ${["working", "waiting", "starting"].includes(j.status) ? `<button data-c="interrupt" class="px-2 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600 text-[12px]">Interrupt</button>` : ""}
        ${j.status !== "stopped" ? `<button data-c="stop" class="px-2 py-1 rounded-md text-red-300 bg-red-500/10 hover:bg-red-500/20 text-[12px]">Stop</button>` : ""}
      </div>
      <div class="flex-1 overflow-y-auto px-4 py-3 space-y-2" data-c-scroll>
        ${asks}${qs}
        ${j.error ? `<div class="text-red-300 text-[12px]">${esc(j.error)}</div>` : ""}
        ${(j.files || []).length ? `<div class="text-[12px] text-zinc-400">Files changed: ${j.files.map((f) => `<span class="font-mono text-zinc-200">${esc(f)}</span>`).join(", ")}</div>` : ""}
        <div class="text-[12px] font-mono">${steps}</div>
      </div>
      <div class="p-2 border-t border-white/[0.06] flex gap-1.5 items-center">
        <input data-c-msg class="flex-1 bg-surface-700 text-white rounded-md px-2 py-1.5 text-[12px]" placeholder="Tell Muse something (a follow-up), Enter sends">
        <label class="text-[11px] text-zinc-400 flex items-center gap-1"><input type="checkbox" data-c-steer> change course now</label>
      </div>`;
  }

  async function refresh() {
    if (!root || root.classList.contains("hidden")) return;
    const d = await api("/api/crew");
    const jobs = d.jobs || [];
    if (current && !jobs.some((j) => j.id === current)) current = null;
    if (!current && jobs.length) current = jobs[0].id;
    setHtml(root.querySelector("[data-c-list]"), list(jobs));
    const right = root.querySelector("[data-c-detail]");
    if (!current) { setHtml(right, `<div class="m-auto text-zinc-500 text-[12px] p-6">Pick a job, or start one.</div>`); return; }
    const r = await api(`/api/crew/${encodeURIComponent(current)}`);
    if (!r.success) return;
    const typing = right.querySelector("[data-c-msg]") === document.activeElement || [...right.querySelectorAll("[data-c-free]")].includes(document.activeElement);
    if (typing) return;                                   // don't redraw while you type there
    const sc = right.querySelector("[data-c-scroll]");
    const atBottom = !sc || sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 40;
    setHtml(right, detail(r.job));
    const sc2 = right.querySelector("[data-c-scroll]");
    if (sc2 && atBottom) sc2.scrollTop = sc2.scrollHeight;
  }

  function open() {
    if (!root) {
      root = document.createElement("div");
      root.id = "crewPanel";
      root.className = "fixed inset-0 z-[85] flex items-center justify-center p-4 bg-black/60";
      root.innerHTML = `
        <div class="w-full max-w-5xl h-[84vh] flex flex-col rounded-2xl bg-surface-850 border border-white/[0.08] shadow-2xl text-[13px] overflow-hidden">
          <div class="flex items-center justify-between px-4 py-3 border-b border-white/[0.06]">
            <div><h3 class="text-white font-semibold text-[15px]">Crew</h3>
              <p class="text-zinc-500 text-[11px]">Coding jobs Xod hands to Muse and follows step by step. What Muse asks to do waits here for your OK.</p></div>
            <button data-c="close" class="p-1 rounded-lg text-zinc-500 hover:text-white" title="Close">✕</button>
          </div>
          <div class="flex flex-1 min-h-0">
            <div class="w-72 shrink-0 border-r border-white/[0.06] flex flex-col">
              <div data-c-list class="flex-1 overflow-y-auto p-2 space-y-1"></div>
              <div class="p-2 border-t border-white/[0.06] space-y-1.5">
                <textarea data-c-task rows="3" class="w-full bg-surface-700 text-zinc-200 rounded-md px-2 py-1 text-[12px]" placeholder="What should Muse do?"></textarea>
                <input data-c-folder class="w-full bg-surface-700 text-zinc-200 rounded-md px-2 py-1 text-[12px]" placeholder="Project folder (default: home)">
                <button data-c="start" class="w-full px-2 py-1.5 rounded-md bg-brand-500 text-surface-900 font-semibold hover:bg-brand-400">Start a Muse job</button>
                <p data-c-note class="text-[11px] text-zinc-400"></p>
                <details data-c-tools class="text-[11px] text-zinc-400"><summary class="cursor-pointer hover:text-zinc-200">Muse's tools for jobs</summary>
                  <p class="mt-1">Each one Muse starts before a job begins (all of yours took minutes), so jobs get only the ones you tick.</p>
                  <div data-c-toolbox class="mt-1 max-h-40 overflow-y-auto grid grid-cols-2 gap-x-2"></div></details>
              </div>
            </div>
            <div data-c-detail class="flex-1 min-w-0 flex flex-col"></div>
          </div>
        </div>`;
      document.body.appendChild(root);
      root.addEventListener("click", onClick);
      root.addEventListener("keydown", onKey);
    }
    root.classList.remove("hidden");
    loadTools();
    refresh();
    clearInterval(timer);
    timer = setInterval(refresh, 1500);
  }

  async function onClick(e) {
    const t = e.target;
    if (t === root || t.closest('[data-c="close"]')) { root.classList.add("hidden"); clearInterval(timer); return; }
    const pick = t.closest("[data-c-pick]");
    if (pick) { current = pick.dataset.cPick; root.querySelector("[data-c-detail]").dataset.html = ""; refresh(); return; }
    const dec = t.closest("[data-c-decide]");
    if (dec) {
      dec.disabled = true;
      const r = await api(`/api/crew/${current}/decide`, { approval: dec.dataset.cDecide, decision: dec.dataset.d });
      if (!r.success && typeof showToast === "function") showToast(r.error);
      refresh(); return;
    }
    const ans = t.closest("[data-c-answer]");
    if (ans) {
      const r = await api(`/api/crew/${current}/answer`, { question: ans.dataset.cAnswer, answers: [{ question: ans.dataset.qid, choice: ans.dataset.choice }] });
      if (!r.success && typeof showToast === "function") showToast(r.error);
      refresh(); return;
    }
    const act = t.closest('[data-c="interrupt"], [data-c="stop"]');
    if (act) { await api(`/api/crew/${current}/${act.dataset.c}`, {}); refresh(); return; }
    if (t.closest('[data-c="start"]')) {
      const task = root.querySelector("[data-c-task]").value.trim(), folder = root.querySelector("[data-c-folder]").value.trim();
      const note = root.querySelector("[data-c-note]");
      if (!task) { note.textContent = "Say what Muse should do."; return; }
      note.textContent = "Starting…";
      const r = await api("/api/crew", { task, folder });
      note.textContent = r.success ? "" : r.error;
      if (r.success) { current = r.job.id; root.querySelector("[data-c-task]").value = ""; }
      refresh();
    }
  }

  async function loadTools() {
    const t = await api("/api/crew/settings/muse-tools");
    const box = root.querySelector("[data-c-toolbox]");
    if (!box || !t.available) return;
    box.innerHTML = t.available.length ? t.available.map((n) => `<label class="flex items-center gap-1 truncate"><input type="checkbox" data-c-tool="${esc(n)}" ${t.chosen.includes(n) ? "checked" : ""}>${esc(n)}</label>`).join("")
      : `<span>Muse has no tools set up.</span>`;
    box.onchange = async () => {
      const names = [...box.querySelectorAll("[data-c-tool]:checked")].map((x) => x.dataset.cTool);
      await api("/api/crew/settings/muse-tools", { names });
    };
  }

  async function onKey(e) {
    if (e.key !== "Enter" || e.shiftKey) return;
    const msg = e.target.closest("[data-c-msg]");
    if (msg && msg.value.trim()) {
      const steer = root.querySelector("[data-c-steer]")?.checked;
      const text = msg.value; msg.value = ""; msg.blur();
      const r = await api(`/api/crew/${current}/message`, { text, steer });
      if (!r.success && typeof showToast === "function") showToast(r.error);
      refresh(); return;
    }
    const free = e.target.closest("[data-c-free]");
    if (free && free.value.trim()) {
      const q = free.closest("[data-c-q]").dataset.cQ;
      const r = await api(`/api/crew/${current}/answer`, { question: q, answers: [{ question: free.dataset.cFree, text: free.value }] });
      free.blur();
      if (!r.success && typeof showToast === "function") showToast(r.error);
      refresh();
    }
  }

  // Anywhere in the app: a chip when Muse is waiting for your OK.
  async function watch() {
    if (window.enabledPacks && !window.enabledPacks.has("cli-agents")) return;
    const d = await api("/api/crew");
    const waiting = (d.jobs || []).filter((j) => j.status === "waiting");
    if (!waiting.length) { if (chip) chip.classList.add("hidden"); return; }
    if (!chip) {
      chip = document.createElement("button");
      chip.className = "fixed bottom-24 left-5 z-[80] flex items-center gap-2 px-3.5 py-2 rounded-2xl bg-amber-500/15 border border-amber-500/40 text-amber-100 text-[12px] shadow-2xl";
      chip.addEventListener("click", () => { current = (waiting[0] || {}).id || current; open(); });
      document.body.appendChild(chip);
    }
    chip.classList.remove("hidden");
    chip.innerHTML = `<span class="size-2 rounded-full bg-amber-400 animate-pulse"></span>Muse needs your OK on ${waiting.length === 1 ? `"${esc(waiting[0].title)}"` : `${waiting.length} jobs`} · Open Crew`;
  }
  setInterval(watch, 4000);

  window.openCrew = open;
})();
