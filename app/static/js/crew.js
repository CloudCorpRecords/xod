// The Crew (backend: services/crew.py, /api/crew): coding jobs Xod hands to Claude Code or Muse and conducts natively.
// Pick a job to see every step live, decide what the agent asks (run this? edit that?), answer its questions, send a
// follow-up or a change of course, interrupt or stop it, or start a new job. A chip shows anywhere in the app while an
// agent is waiting for you.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const DOT = { starting: "bg-sky-400 animate-pulse", working: "bg-emerald-400 animate-pulse", waiting: "bg-amber-400 animate-pulse",
                done: "bg-emerald-600", failed: "bg-red-400", stopped: "bg-zinc-600", interrupted: "bg-zinc-400" };
  const WORD = { starting: "starting", working: "working", waiting: "needs you", done: "done", failed: "failed", stopped: "stopped", interrupted: "paused" };
  const ICON = { tool: "⚙", says: "💬", asks: "✋", you: "🙋", xod: "✅", status: "•", error: "⚠" };
  let root = null, current = null, timer = null, chip = null;
  let listOnly = false;          // a narrow window showing the list instead of the open job
  const WHO = { claude: "Claude Code", muse: "Muse" };
  const who = (j) => WHO[j.agent] || j.agent || "The agent";

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
        <div class="text-zinc-500 text-[11px] truncate mt-0.5">${esc(j.name)} · ${esc((j.project || j.folder || "").replace(/^\/Users\/[^/]+/, "~"))} · ${ago(j.updated)} ago</div>
      </button>`).join("") : `<p class="text-zinc-500 text-[12px] px-3 py-2">No jobs yet. Start one below, or ask Xod ("have claude code fix the tests in ~/code/app").</p>`;
  }

  function detail(j) {
    const asks = j.approvals.map((a) => `
      <div class="p-3 rounded-xl bg-amber-500/10 border border-amber-500/30">
        <div class="text-amber-100 text-[13px]">${esc(who(j))} wants to <b>${esc(a.what)}</b></div>
        ${a.detail && !a.what.toLowerCase().includes(a.detail.toLowerCase()) ? `<div class="text-amber-200/70 text-[12px] mt-0.5">${esc(a.detail)}</div>` : ""}
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
    const usage = (j.usage && (j.usage.totalTokens || j.usage.inputTokens) ? ` · ${(j.usage.totalTokens || (j.usage.inputTokens || 0) + (j.usage.outputTokens || 0)).toLocaleString()} tokens` : "");
    return `
      <div class="px-4 py-3 border-b border-white/[0.06] flex items-start gap-2">
        <div class="min-w-0 flex-1"><div class="text-white font-medium truncate">${esc(j.title)}</div>
          <div class="text-zinc-500 text-[11px]">${esc(j.name)} · ${esc(who(j))} · ${esc(WORD[j.status] || j.status)} · ${esc(j.project || j.folder)}${usage}</div></div>
        ${["working", "waiting", "starting"].includes(j.status) ? `<button data-c="interrupt" class="px-2 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600 text-[12px]">Interrupt</button>` : ""}
        ${["working", "waiting", "starting"].includes(j.status) ? `<button data-c="stop" class="px-2 py-1 rounded-md text-red-300 bg-red-500/10 hover:bg-red-500/20 text-[12px]">Stop</button>`
          : j.status !== "stopped" ? `<button data-c="stop" class="px-2 py-1 rounded-md text-zinc-400 hover:text-white hover:bg-surface-700 text-[12px]" title="Close its session: it won't take follow-ups after this">End</button>` : ""}
      </div>
      <div class="flex-1 overflow-y-auto px-4 py-3 space-y-2" data-c-scroll>
        ${asks}${qs}
        ${j.error ? `<div class="text-red-300 text-[12px]">${esc(j.error)}</div>` : ""}
        ${(j.files || []).length ? `<div class="text-[12px] text-zinc-400">Files changed: ${j.files.map((f) => `<span class="font-mono text-zinc-200">${esc(f)}</span>`).join(", ")}</div>` : ""}
        ${copyBox(j)}
        <div class="text-[12px] font-mono">${steps}</div>
      </div>
      <div class="p-2 border-t border-white/[0.06] flex gap-1.5 items-center">
        <input data-c-msg class="flex-1 bg-surface-700 text-white rounded-md px-2 py-1.5 text-[12px]" placeholder="Tell ${esc(who(j))} something (a follow-up), Enter sends">
        <label class="text-[11px] text-zinc-400 flex items-center gap-1"><input type="checkbox" data-c-steer> change course now</label>
      </div>`;
  }

  // A job's own copy (a git worktree on its own branch): what it changed, and merge / pull request / discard.
  const reviews = {};
  function copyBox(j) {
    const w = j.worktree;
    if (!w) return "";
    if (w.closed === "merged") return `<div class="text-[12px] text-emerald-300">✓ Merged into ${esc(w.base)} in ${esc(w.repo_name)}: your checkout has the change.</div>`;
    if (w.closed) return `<div class="text-[12px] text-zinc-400">Its copy was ${esc(w.closed)}${w.pr ? `: <a href="${esc(w.pr)}" target="_blank" rel="noopener" class="text-sky-300 hover:underline">${esc(w.pr)}</a>` : ""}.</div>`;
    const busy = ["starting", "working", "waiting"].includes(j.status);
    const r = reviews[j.id];
    const files = r && r.files ? r.files.map((f) => `<div class="flex gap-2 font-mono text-[12px]"><span class="text-zinc-200 truncate flex-1">${esc(f.file)}</span>
        <span class="text-emerald-400">+${f.added ?? "?"}</span><span class="text-red-300">−${f.removed ?? "?"}</span></div>`).join("") : "";
    return `<div class="p-3 rounded-xl bg-surface-900 border border-white/[0.06] space-y-2">
      <div class="text-[12px] text-zinc-400">Works in its own copy: branch <span class="font-mono text-zinc-200">${esc(w.branch)}</span> from <span class="font-mono text-zinc-200">${esc(w.base)}</span> in ${esc(w.repo_name)}. Your checkout is untouched until you merge.</div>
      ${busy ? "" : `<div class="flex flex-wrap gap-1.5">
        <button data-c-land="review" class="px-2.5 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600 text-[12px]">${r ? "Refresh changes" : "See changes"}</button>
        <button data-c-land="merge" class="px-2.5 py-1 rounded-md bg-emerald-500/20 text-emerald-200 hover:bg-emerald-500/30 text-[12px]">Merge into ${esc(w.base)}</button>
        <button data-c-land="pr" class="px-2.5 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600 text-[12px]">Open pull request</button>
        <button data-c-land="discard" class="px-2.5 py-1 rounded-md bg-red-500/15 text-red-200 hover:bg-red-500/25 text-[12px]">Discard</button></div>`}
      ${r && r.error ? `<div class="text-red-300 text-[12px]">${esc(r.error)}</div>` : ""}
      ${r && r.files ? (r.files.length ? `<div>${files}</div><details class="text-[12px]"><summary class="cursor-pointer text-zinc-400 hover:text-zinc-200">The diff (+${r.added} −${r.removed})</summary>
          <pre class="mt-1 max-h-80 overflow-auto p-2 rounded-md bg-black/40 text-zinc-300 font-mono text-[11px] whitespace-pre">${esc(r.patch)}${r.truncated ? "\n…" : ""}</pre></details>` : `<div class="text-zinc-500 text-[12px]">No changes.</div>`) : ""}
    </div>`;
  }

  async function land(act) {
    if (act === "discard" && !confirm("Throw away this job's copy and its branch? Its changes will be gone.")) return;
    if (act === "pr" && !confirm("Push the job's branch and open a pull request on GitHub?")) return;
    const r = await api(`/api/crew/${current}/${act}`, {});
    if (act === "review") reviews[current] = r.success ? r : { error: r.error };
    else if (!r.success) reviews[current] = { ...(reviews[current] || {}), error: r.error };
    else { delete reviews[current]; if (typeof showToast === "function") showToast(r.message || "Done"); }
    refresh();
  }

  const when = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  function usageLine(u) {
    if (!u || !u.available) return "Muse isn't installed.";
    if (u.error) return `Muse usage: couldn't read it (${esc(u.error)})`;
    const part = (name, x) => x ? `${name} ${Math.round(x.used)}%` + (x.resets_at ? ` (resets ${x.used >= 100 ? when(x.resets_at) : "later"})` : "") : "";
    const txt = [part("window", u.window), part("weekly", u.weekly)].filter(Boolean).join(" · ");
    return (u.out ? `<span class="text-amber-300">Muse is out of usage until ${u.until ? when(u.until) : "its reset"}.</span> ` : "") + `Muse usage: ${txt || "unknown"}`;
  }

  let modelsLoaded = false;
  async function refreshLoop() {
    const body = root.querySelector("[data-c-loop-body]");
    const box = root.querySelector("[data-c-loop]");
    if (!body || !box || !box.open) return;
    const L = await api("/api/crew/loop");
    if (!L || L.success === false) { body.textContent = "Couldn't read the loop."; return; }
    const rows = (L.repos || []).map((r) => {
      const j = r.last_job;
      const job = j ? `${esc(j.status)}${j.steps ? `, ${j.steps} steps` : ""}${j.model ? ` on ${esc(j.model)}` : ""}` : "nothing handed over yet";
      const next = r.next_start ? ` · next start ${when(r.next_start)}` : "";
      return `<div><b class="text-zinc-200">${esc(r.name)}</b>: ${job}${next}</div>`;
    }).join("");
    body.innerHTML = `<div>${L.handoff_on ? "On" : "Off (no repos watched)"}${L.auto_approve && L.auto_approve.on ? (L.auto_approve.mode === "away" ? " · auto-approve when you're away" : " · auto-approve on") : (L.auto_approve && L.auto_approve.paused_until ? " · auto-approve paused" : "")}${L.auto_approve && L.auto_approve.awake_held ? " · keeping the display awake" : ""}</div>`
      + `<div>${usageLine(L.usage)}</div>` + rows;
    if (!modelsLoaded && L.models) {
      modelsLoaded = true;
      root.querySelector("[data-c-model-default]").value = L.models.default || "";
      root.querySelector("[data-c-model-cheap]").value = L.models.cheap || "";
      root.querySelector("[data-c-model-handoff]").checked = (L.models.cheap_for || []).includes("handoff");
      root.querySelector("[data-c-model-weekly]").value = L.models.cheap_above_weekly ?? "";
      root.querySelector("[data-c-model-save]").onclick = async () => {
        const w = root.querySelector("[data-c-model-weekly]").value.trim();
        const r = await api("/api/crew/settings/models", {
          default: root.querySelector("[data-c-model-default]").value, cheap: root.querySelector("[data-c-model-cheap]").value,
          cheap_for: root.querySelector("[data-c-model-handoff]").checked ? ["handoff"] : [],
          ...(w === "" ? { clear_cheap_above_weekly: true } : { cheap_above_weekly: Number(w) }),
        });
        if (typeof showToast === "function") showToast(r.success ? "Saved: new Muse jobs use these models." : r.error);
      };
    }
  }

  async function refresh() {
    if (!root || root.classList.contains("hidden")) return;
    refreshAutoApprove();
    refreshLoop();
    const d = await api("/api/crew");
    const jobs = d.jobs || [];
    if (current && !jobs.some((j) => j.id === current)) current = null;
    if (!current && jobs.length) current = jobs[0].id;
    setHtml(root.querySelector("[data-c-list]"), list(jobs));
    const right = root.querySelector("[data-c-detail]");
    // A narrow window shows one side at a time: the open job across the whole board, or the list (← All jobs).
    const narrow = root.firstElementChild.clientWidth < 900;
    const side = root.querySelector("[data-c-side]");
    side.classList.toggle("hidden", narrow && !!current && !listOnly);
    side.classList.toggle("w-72", !narrow);
    side.classList.toggle("w-full", narrow);
    right.classList.toggle("hidden", narrow && (listOnly || !current));
    if (!current) { setHtml(right, `<div class="m-auto text-zinc-500 text-[12px] p-6">Pick a job, or start one.</div>`); return; }
    const r = await api(`/api/crew/${encodeURIComponent(current)}`);
    if (!r.success) return;
    const typing = right.querySelector("[data-c-msg]") === document.activeElement || [...right.querySelectorAll("[data-c-free]")].includes(document.activeElement);
    if (typing) return;                                   // don't redraw while you type there
    const sc = right.querySelector("[data-c-scroll]");
    const atBottom = !sc || sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 40;
    setHtml(right, (narrow ? `<button data-c="back" class="self-start m-2 mb-0 px-2 py-1 rounded-md text-[12px] text-zinc-400 hover:text-white hover:bg-surface-700">← All jobs</button>` : "") + detail(r.job));
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
              <p class="text-zinc-500 text-[11px]">Coding jobs Xod hands to Claude Code or Muse and follows step by step. What they ask to do waits here for your OK.</p></div>
            <button data-c="close" class="p-1 rounded-lg text-zinc-500 hover:text-white" title="Close">✕</button>
          </div>
          <div class="flex flex-1 min-h-0">
            <div data-c-side class="w-72 shrink-0 border-r border-white/[0.06] flex flex-col">
              <div data-c-list class="flex-1 overflow-y-auto p-2 space-y-1"></div>
              <div class="p-2 border-t border-white/[0.06] space-y-1.5">
                <select data-c-agent class="w-full bg-surface-700 text-zinc-200 rounded-md px-2 py-1 text-[12px]"><option value="claude">Claude Code</option><option value="muse">Muse</option></select>
                <textarea data-c-task rows="3" class="w-full bg-surface-700 text-zinc-200 rounded-md px-2 py-1 text-[12px]" placeholder="What should it do?"></textarea>
                <input data-c-folder class="w-full bg-surface-700 text-zinc-200 rounded-md px-2 py-1 text-[12px]" placeholder="Project folder (default: home)">
                <label class="flex items-center gap-1.5 text-[11px] text-zinc-400" title="In a git repository the job gets a branch and folder of its own; you merge it when you're happy."><input type="checkbox" data-c-isolate checked> Its own copy (git projects)</label>
                <button data-c="start" class="w-full px-2 py-1.5 rounded-md bg-brand-500 text-surface-900 font-semibold hover:bg-brand-400">Start the job</button>
                <p data-c-note class="text-[11px] text-zinc-400"></p>
                <div class="space-y-1 text-[11px] text-zinc-400" title="Xod presses Allow for you on Muse and Claude Code approvals in Crew jobs and on the Muse app's Needs review cards (needs Accessibility access). Only when I'm away: Muse's cards are pressed only while the screen is locked or you haven't typed or clicked for a few minutes, so Muse never takes the screen while you work. Requests Xod's safety check flags, and protected writes, still wait for you. Each one is logged in the approvals history. You can also just tell Xod: &quot;pause Muse approvals for an hour&quot;.">
                  <div>Approve what Muse and Claude Code ask</div>
                  <div class="flex items-center gap-1">
                    <select data-c-autoapprove class="flex-1 min-w-0 bg-surface-700 text-zinc-200 rounded-md px-1.5 py-1 text-[11px]">
                      <option value="off">Off (every card waits for me)</option>
                      <option value="away">Only when I'm away</option>
                      <option value="always">Always</option>
                    </select>
                    <button data-c-autoapprove-pause class="shrink-0 px-2 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600 hidden">Pause 1h</button>
                  </div>
                  <div data-c-autoapprove-state class="text-zinc-500"></div>
                </div>
                <p data-c-autoapprove-note class="text-[11px] text-amber-300 hidden"></p>
                <div data-c-autoapprove-fix class="hidden flex gap-1">
                  <button data-c-accessibility-open class="px-2 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600">Open Accessibility settings</button>
                  <button data-c-accessibility-reset class="px-2 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600">Reset my stale grant</button>
                </div>
                <div data-c-partner-repair class="hidden space-y-1 text-[11px] text-zinc-300">
                  <ol data-c-partner-repair-steps class="list-decimal ml-4 space-y-0.5"></ol>
                  <div class="flex gap-1">
                    <button data-c-partner-reset class="px-2 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600">Reset the Partner's stale grant</button>
                    <button data-c-partner-open class="px-2 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600">Open Accessibility settings</button>
                  </div>
                </div>
                <details data-c-tools class="text-[11px] text-zinc-400"><summary class="cursor-pointer hover:text-zinc-200">Muse's tools for jobs</summary>
                  <p class="mt-1">Each one Muse starts before a job begins (all of yours took minutes), so jobs get only the ones you tick.</p>
                  <div data-c-toolbox class="mt-1 max-h-40 overflow-y-auto grid grid-cols-2 gap-x-2"></div></details>
                <details data-c-loop class="text-[11px] text-zinc-400" open><summary class="cursor-pointer hover:text-zinc-200">Loop: work handed to Muse</summary>
                  <div data-c-loop-body class="mt-1 space-y-1"></div>
                  <div class="mt-2 space-y-1">
                    <div class="flex items-center gap-1"><span class="w-20 shrink-0">Model</span><input data-c-model-default class="flex-1 min-w-0 bg-surface-700 text-zinc-200 rounded px-1.5 py-0.5" placeholder="Muse's default"></div>
                    <div class="flex items-center gap-1"><span class="w-20 shrink-0">Cheaper model</span><input data-c-model-cheap class="flex-1 min-w-0 bg-surface-700 text-zinc-200 rounded px-1.5 py-0.5" placeholder="e.g. muse-spark-1.3"></div>
                    <label class="flex items-center gap-1.5"><input type="checkbox" data-c-model-handoff> Use the cheaper model for handed-over work</label>
                    <div class="flex items-center gap-1"><span>…and for every job once the weekly allowance is at</span><input data-c-model-weekly type="number" min="0" max="100" class="w-12 bg-surface-700 text-zinc-200 rounded px-1 py-0.5" placeholder="off">%</div>
                    <button data-c-model-save class="px-2 py-0.5 rounded bg-surface-700 text-zinc-200 hover:bg-surface-600">Save models</button>
                  </div></details>
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
    loadAutoApprove();
    refresh();
    clearInterval(timer);
    timer = setInterval(refresh, 1500);
  }

  async function onClick(e) {
    const t = e.target;
    if (t === root || t.closest('[data-c="close"]')) { root.classList.add("hidden"); clearInterval(timer); return; }
    const pick = t.closest("[data-c-pick]");
    if (pick) { current = pick.dataset.cPick; listOnly = false; root.querySelector("[data-c-detail]").dataset.html = ""; refresh(); return; }
    if (t.closest('[data-c="back"]')) { listOnly = true; refresh(); return; }
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
    const landBtn = t.closest("[data-c-land]");
    if (landBtn && current) { landBtn.disabled = true; await land(landBtn.dataset.cLand); return; }
    const act = t.closest('[data-c="interrupt"], [data-c="stop"]');
    if (act) { await api(`/api/crew/${current}/${act.dataset.c}`, {}); refresh(); return; }
    if (t.closest('[data-c="start"]')) {
      const task = root.querySelector("[data-c-task]").value.trim(), folder = root.querySelector("[data-c-folder]").value.trim();
      const note = root.querySelector("[data-c-note]");
      const agent = root.querySelector("[data-c-agent]").value;
      if (!task) { note.textContent = "Say what it should do."; return; }
      note.textContent = "Starting…";
      const isolate = root.querySelector("[data-c-isolate]").checked;
      const r = await api("/api/crew", { task, folder, agent, isolate: isolate ? null : false });
      note.textContent = r.success ? "" : r.error;
      if (r.success) { current = r.job.id; root.querySelector("[data-c-task]").value = ""; }
      refresh();
    }
  }

  function autoApproveState(st) {
    if (st.paused_until) {
      const mins = Math.max(1, Math.round((st.paused_until * 1000 - Date.now()) / 60000));
      return `Paused for ${mins} more min.`;
    }
    if (st.mode === "away") return st.pressing_now ? `You're away (${(st.you && st.you.why) || "idle"}): pressing cards.` : `Waiting: ${(st.you && st.you.why) || "you're here"}. Cards are pressed after ${st.away_minutes} min idle or when the screen locks.`;
    if (st.mode === "always") return "Pressing every card as it appears.";
    return "";
  }

  function showAutoApprove(st) {
    const box = root.querySelector("[data-c-autoapprove]");
    if (box && document.activeElement !== box) box.value = st.mode || (st.on ? "always" : "off");
    const pause = root.querySelector("[data-c-autoapprove-pause]");
    if (pause) {
      pause.classList.toggle("hidden", (st.mode || "off") === "off");
      pause.textContent = st.paused_until ? "Resume" : "Pause 1h";
    }
    const state = root.querySelector("[data-c-autoapprove-state]");
    if (state) state.textContent = autoApproveState(st);
    const note = root.querySelector("[data-c-autoapprove-note]");
    if (note) {
      const bits = [];
      if (st.accessibility_error) bits.push(st.accessibility_error);
      else if (st.on && st.awake_held) bits.push("Staying awake while this is on, so the computer doesn't lock mid-job.");
      if (st.on && st.locked) bits.push("Screen is locked: cards are still read; a press while locked is unproven.");
      const perm = st.permissions || {};
      if (perm.notice && !bits.includes(perm.notice)) bits.push(perm.notice);
      const partner = st.partner || {};
      if (partner.notice && !bits.includes(partner.notice)) bits.push(partner.notice);
      note.textContent = bits.join(" ");
      note.classList.toggle("hidden", !bits.length);
      const fix = root.querySelector("[data-c-autoapprove-fix]");
      if (fix) {
        fix.classList.toggle("hidden", !(perm.notice || partner.notice));
        const reset = fix.querySelector("[data-c-accessibility-reset]");
        if (reset) reset.classList.toggle("hidden", !perm.can_reset);
      }
      const repair = root.querySelector("[data-c-partner-repair]");
      if (repair) {
        const steps = partner.stale_grant && partner.repair_steps ? partner.repair_steps : null;
        repair.classList.toggle("hidden", !steps);
        if (steps) {
          const ol = repair.querySelector("[data-c-partner-repair-steps]");
          if (ol) ol.innerHTML = steps.map((s) => `<li>${esc(s.text)}</li>`).join("");
        }
      }
    }
  }

  async function setAutoApprove(body) {
    const r = await api("/api/crew/settings/auto-approve", body);
    if (!r || !r.mode) { showAutoApprove(await api("/api/crew/settings/auto-approve")); return; }
    showAutoApprove(r);
    const said = { off: "Muse and Claude Code approvals wait for you again.",
                   always: "Xod now approves everything Muse and Claude Code ask, and logs each one. (In Crew jobs, ones its safety check flags still wait for you.)",
                   away: `Xod approves Muse's cards only while you're away (screen locked or ${r.away_minutes} min idle). Crew jobs' requests are approved as they come.` };
    if (typeof showToast === "function") showToast(r.accessibility_error || (r.paused_until ? autoApproveState(r) + " Then it comes back on." : said[r.mode]));
  }

  async function loadAutoApprove() {
    const box = root.querySelector("[data-c-autoapprove]");
    if (!box) return;
    showAutoApprove(await api("/api/crew/settings/auto-approve"));
    box.onchange = () => setAutoApprove({ mode: box.value });
    const pause = root.querySelector("[data-c-autoapprove-pause]");
    if (pause) pause.onclick = () => setAutoApprove(pause.textContent === "Resume" ? { mode: "resume" } : { mode: "pause", pause_minutes: 60 });
    const openBtn = root.querySelector("[data-c-accessibility-open]");
    if (openBtn) openBtn.onclick = async () => {
      const r = await api("/api/crew/settings/auto-approve/accessibility/open-settings", {});
      if (typeof showToast === "function" && r && r.message) showToast(r.message);
    };
    const resetBtn = root.querySelector("[data-c-accessibility-reset]");
    if (resetBtn) resetBtn.onclick = async () => {
      if (!window.confirm("Clear Xod's Accessibility entry so macOS asks fresh? Xod stays unapproved until you flip it back on in the prompt that follows.")) return;
      const r = await api("/api/crew/settings/auto-approve/accessibility/reset-grant", {});
      if (typeof showToast === "function" && r && r.message) showToast(r.message);
      showAutoApprove(await api("/api/crew/settings/auto-approve"));
    };
    const partnerResetBtn = root.querySelector("[data-c-partner-reset]");
    if (partnerResetBtn) partnerResetBtn.onclick = async () => {
      if (!window.confirm("Clear the Xod Partner's stale Accessibility entry? The Partner restarts, Xod asks it to ask macOS again, and you finish by flipping the fresh Xod Partner row on in Accessibility settings.")) return;
      const r = await api("/api/crew/settings/auto-approve/accessibility/reset-partner-grant", {});
      if (typeof showToast === "function" && r && r.message) showToast(r.message);
      showAutoApprove(await api("/api/crew/settings/auto-approve"));
    };
    const partnerOpenBtn = root.querySelector("[data-c-partner-open]");
    if (partnerOpenBtn) partnerOpenBtn.onclick = async () => {
      const r = await api("/api/crew/settings/auto-approve/accessibility/open-settings", {});
      if (typeof showToast === "function" && r && r.message) showToast(r.message);
    };
  }

  // The watcher can hit (or clear) a problem seconds after the switch is flipped, so the note under
  // it is refreshed with the panel — not only when Crew is opened or the switch is toggled.
  async function refreshAutoApprove() {
    if (!root || !root.querySelector("[data-c-autoapprove]")) return;
    showAutoApprove(await api("/api/crew/settings/auto-approve"));
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

  // Anywhere in the app: a chip when an agent is waiting for your OK.
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
    chip.innerHTML = `<span class="size-2 rounded-full bg-amber-400 animate-pulse"></span>${waiting.length === 1 ? esc(who(waiting[0])) : "Crew"} needs your OK on ${waiting.length === 1 ? `"${esc(waiting[0].title)}"` : `${waiting.length} jobs`} · Open Crew`;
  }
  setInterval(watch, 4000);

  window.openCrew = open;
})();
