// The Agent page: Xod at a glance, one tab per kind of thing it does (services/agent_page.py).
//   Activity   every job, with a one-line result; open one for its steps by agent (MAIN, SUBAGENT 01…)
//   Approvals  every "waiting for your OK" and what you decided
//   Browser    jobs that used the browser, with their last screenshot
//   Schedules  reminders, routines, goals' check-ins, the morning brief
//   Identity   its soul and what it remembers
//   Team       which model works on each tier, and outside teammate agents
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = async (url, method = "GET", body) =>
    (await fetch(url, { method, headers: body !== undefined ? { "Content-Type": "application/json" } : {}, body: body !== undefined ? JSON.stringify(body) : undefined })).json();
  const TABS = [
    ["activity", "list", "Activity"], ["usage", "gauge", "Usage"], ["approvals", "shield-check", "Approvals"], ["browser", "monitor", "Browser"],
    ["schedules", "clock", "Schedules"], ["identity", "fingerprint", "Identity"], ["team", "users", "Team"],
  ];
  let tab = "activity", timer = null;

  const ago = (t) => {
    if (!t) return "";
    const s = Math.max(0, Date.now() / 1000 - t);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return new Date(t * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
  };
  const when = (t) => t ? new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";
  const dayOf = (t) => {
    const d = new Date(t * 1000), today = new Date();
    if (d.toDateString() === today.toDateString()) return "Today";
    const y = new Date(today); y.setDate(today.getDate() - 1);
    return d.toDateString() === y.toDateString() ? "Yesterday" : d.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
  };
  const STATUS = {
    done: ["Completed", "xa-pill-ok"], working: ["Working", "xa-pill-run"], queued: ["Queued", "xa-pill-run"],
    waiting: ["Waiting for you", "xa-pill-wait"], failed: ["Didn't finish", "xa-pill-bad"], cancelled: ["Cancelled", "xa-pill-quiet"],
  };
  const agentName = (a) => a === "main" ? "MAIN" : /^sub(\d+)$/.test(a) ? `SUBAGENT ${a.slice(3).padStart(2, "0")}` : a.toUpperCase();

  // ---------------------------------------------------------------- tabs
  async function activity(body) {
    const { items } = await api("/api/agent/activity");
    if (!items.length) return empty(body, "Nothing yet.", "Jobs show up here as Xod does them: tasks, goal check-ins, routines and chats where it used tools.");
    let last = "";
    body.innerHTML = items.map((it) => {
      const d = dayOf(it.at), head = d !== last ? `<p class="xa-day">${esc(d)}</p>` : "";
      last = d;
      const running = it.status === "working" || it.status === "queued";
      return `${head}<button class="xa-row" data-open="${esc(it.id)}">
        <span class="xa-ico ${running ? "xa-ico-run" : ""}">${running ? `<i data-lucide="loader" class="size-4 animate-spin"></i>` :
          `<i data-lucide="${it.kind === "chat" ? "message-square" : it.kind === "own" ? "sparkles" : it.status === "waiting" ? "hand" : it.status === "failed" ? "x" : "check"}" class="size-4"></i>`}</span>
        <span class="min-w-0 flex-1 text-left"><span class="xa-title">${esc(it.title)}</span>
          <span class="xa-sub">${esc(it.summary || "")}</span>
          <span class="xa-time">${esc(ago(it.at))}${it.agents > 1 ? ` · ${it.agents} agents` : ""}</span></span>
      </button>`;
    }).join("");
  }

  async function openDetail(origin) {
    const { detail: d } = await api(`/api/agent/activity?origin=${encodeURIComponent(origin)}`);
    const sheet = document.getElementById("xaSheet");
    const [label, cls] = STATUS[d.status] || ["", "xa-pill-quiet"];
    const sections = (d.order || []).map((a) => `<p class="xa-agent">${esc(agentName(a))}</p>` + (d.steps[a] || []).map((s) => `
      <div class="xa-step"><span class="${s.ok ? "xa-tick" : "xa-cross"}">${s.ok ? "✓" : "✕"}</span>
        <span class="min-w-0"><span class="xa-step-title">${esc(s.title)}</span>${s.detail ? `<span class="xa-step-detail">${esc(s.detail)}</span>` : ""}</span></div>`).join("")).join("");
    sheet.innerHTML = `<div class="xa-sheet-grab"></div>
      <div class="flex items-center justify-between">${label ? `<span class="xa-pill ${cls}">${esc(label)}</span>` : "<span></span>"}
        <span class="xa-time">${esc(when(d.at || 0))}</span></div>
      <h3 class="xa-sheet-title">${esc(d.title || "")}</h3>
      ${d.now && d.status !== "done" ? `<p class="xa-sub">${esc(d.now)}</p>` : ""}
      ${d.usage && d.usage.calls ? `<p class="xa-faint mt-1">${d.usage.calls} model call${d.usage.calls !== 1 ? "s" : ""} · ${num(d.usage.prompt + d.usage.completion)} tokens (${num(d.usage.prompt)} in, ${num(d.usage.completion)} out) · ${Math.round(d.usage.ms / 1000)} s of model time${d.usage.cost ? ` · ${usd(d.usage.cost)}` : ""} · ${esc((d.usage.models || []).join(", "))}</p>` : ""}
      ${d.result ? `<div class="xa-result">${window.marked ? marked.parse(d.result.replace(/<!--[\s\S]*?-->/g, "").slice(0, 4000)) : esc(d.result)}</div>` : ""}
      ${d.has_screenshot ? `<img class="xa-shot-big" src="/api/tasks/${esc(d.id.slice(5))}/screenshot" alt="The page at the end">` : ""}
      ${sections || `<p class="xa-sub mt-3">No steps recorded for this one.</p>`}
      <button class="xa-btn mt-4" data-close-sheet>Close</button>`;
    sheet.parentElement.classList.add("is-open");
    if (window.lucide) lucide.createIcons();
  }

  // ---------------------------------------------------------------- usage (services/usage.py)
  let range = "7d";
  const num = (n) => { n = Number(n || 0); return n >= 1e6 ? `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k` : String(Math.round(n)); };
  const usd = (v) => !v ? "$0" : v < 0.01 ? "<$0.01" : `$${v.toFixed(v < 1 ? 3 : 2)}`;
  async function usage(body) {
    const d = await api(`/api/agent/usage?origin=${range}`);
    const t = d.totals;
    const RANGES = [["today", "Today"], ["7d", "7 days"], ["30d", "30 days"], ["90d", "90 days"]];
    const localPct = t.total ? Math.round((100 * t.local_tokens) / t.total) : 0;
    const bars = d.hourly.length && range === "today"
      ? Array.from({ length: 24 }, (_, h) => ({ label: `${h}:00`, local: (d.hourly.find((x) => x.hour === h) || {}).tokens || 0, cloud: 0 }))
      : d.daily.map((x) => ({ label: x.day.slice(5), local: x.local, cloud: x.cloud, cost: x.cost }));
    const peak = Math.max(1, ...bars.map((b) => b.local + b.cloud));
    const maxPurpose = Math.max(1, ...d.by_purpose.map((p) => p.tokens || 0));
    body.innerHTML = `
      <div class="xa-seg">${RANGES.map(([k, l]) => `<button data-range="${k}" class="${range === k ? "is-on" : ""}">${l}</button>`).join("")}</div>
      ${!t.calls ? `<p class="xa-faint py-6">No model calls ${range === "today" ? "today" : "in this time"} yet.</p>` : `
      <div class="xa-stats">
        <div class="xa-stat"><span class="xa-stat-k">Tokens</span><span class="xa-stat-v">${num(t.total)}</span><span class="xa-stat-s">${num(t.prompt)} in · ${num(t.completion)} out</span></div>
        <div class="xa-stat"><span class="xa-stat-k">Model calls</span><span class="xa-stat-v">${num(t.calls)}</span><span class="xa-stat-s">${Math.round(t.ms / 1000 / 60)} min of model time</span></div>
        <div class="xa-stat"><span class="xa-stat-k">Cloud cost</span><span class="xa-stat-v">${usd(t.cost)}</span><span class="xa-stat-s">${t.cloud_tokens ? `${num(t.cloud_tokens)} cloud tokens` : "all on this computer"}</span></div>
        <div class="xa-stat"><span class="xa-stat-k">Speed</span><span class="xa-stat-v">${t.tokens_per_sec ?? "–"}</span><span class="xa-stat-s">tokens a second, written</span></div>
      </div>
      <div class="xa-split"><span style="width:${localPct}%" class="xa-split-local"></span><span style="width:${100 - localPct}%" class="xa-split-cloud"></span></div>
      <p class="xa-faint">${localPct}% on this computer (free, private) · ${100 - localPct}% cloud${t.estimated ? ` · ${t.estimated} call${t.estimated !== 1 ? "s" : ""} estimated (~4 characters a token: the server gave no count)` : ""}</p>
      ${d.budget && d.budget.monthly_usd ? `<p class="xa-sub">This month: ${usd(d.budget.month_spend)} of your ${usd(d.budget.monthly_usd)} cloud budget</p>` : ""}

      <p class="xa-day">${range === "today" ? "Today, by hour" : "By day"}</p>
      <div class="xa-chart">${bars.map((b) => `<div class="xa-bar" title="${esc(b.label)}: ${num(b.local + b.cloud)} tokens${b.cost ? ` · ${usd(b.cost)}` : ""}">
          <span class="xa-bar-cloud" style="height:${(100 * b.cloud) / peak}%"></span><span class="xa-bar-local" style="height:${(100 * b.local) / peak}%"></span></div>`).join("")}</div>
      <div class="xa-chart-x"><span>${esc(bars[0]?.label || "")}</span><span>${esc(bars[bars.length - 1]?.label || "")}</span></div>

      <p class="xa-day">By model</p>
      <div class="xa-table">${d.by_model.map((m) => `<div class="xa-tr"><span class="xa-td-main"><b>${esc(m.model)}</b><small>${m.local ? "this computer" : esc(m.provider)}</small></span>
        <span>${num(m.calls)} calls</span><span>${num((m.prompt || 0) + (m.completion || 0))} tok</span><span>${m.tokens_per_sec ? `${m.tokens_per_sec} t/s` : "–"}</span><span>${m.local ? "free" : usd(m.cost)}</span></div>`).join("")}</div>

      <p class="xa-day">What it was for</p>
      ${d.by_purpose.map((p) => `<div class="xa-hbar"><span class="xa-hbar-label">${esc(p.label)}</span>
        <span class="xa-hbar-track"><span style="width:${(100 * (p.tokens || 0)) / maxPurpose}%"></span></span><span class="xa-hbar-n">${num(p.tokens)}</span></div>`).join("")}
      ${d.by_agent.length > 1 ? `<p class="xa-faint mt-1">${d.by_agent.map((a) => `${a.who === "main" ? "Main agent" : "Subagents"}: ${num(a.tokens)} tokens`).join(" · ")}</p>` : ""}

      ${d.by_job.length ? `<p class="xa-day">Biggest jobs</p>${d.by_job.map((j) => `<button class="xa-row" data-open="${esc(j.origin)}">
          <span class="xa-ico"><i data-lucide="${j.origin.startsWith("task:") ? "check" : j.origin.startsWith("chat:") ? "message-square" : "sparkles"}" class="size-4"></i></span>
          <span class="min-w-0 flex-1 text-left"><span class="xa-title">${esc(j.job || j.origin)}</span>
          <span class="xa-sub">${num(j.tokens)} tokens · ${num(j.calls)} calls${j.agents > 1 ? ` · ${j.agents} agents` : ""}${j.cost ? ` · ${usd(j.cost)}` : ""}</span>
          <span class="xa-time">${esc(ago(j.at))}</span></span></button>`).join("")}` : ""}

      <p class="xa-day">Recent calls</p>
      <div class="xa-table xa-calls">${d.recent.map((r) => `<div class="xa-tr" title="${esc(r.job || "")}">
        <span class="xa-td-main"><b>${esc(r.label)}</b><small>${esc(r.model)}${r.agent && r.agent !== "main" ? ` · ${esc(agentName(r.agent))}` : ""}${r.job ? ` · ${esc(r.job.slice(0, 40))}` : ""}</small></span>
        <span>${num(r.prompt_tokens)} → ${num(r.completion_tokens)}${r.estimated ? "*" : ""}</span><span>${r.ms != null ? `${(r.ms / 1000).toFixed(1)} s` : "–"}</span>
        <span>${r.tokens_per_sec ? `${r.tokens_per_sec} t/s` : ""}</span><span>${r.local ? "" : usd(r.cost_usd)}</span><span class="xa-faint">${esc(ago(r.ts))}</span></div>`).join("")}</div>`}`;
  }

  async function approvals(body) {
    const { items } = await api("/api/agent/approvals");
    if (!items.length) return empty(body, "No approvals yet.", "Every time Xod waits for your OK (a connected app's change, the safety check, a task's submit) it's listed here with what you decided.");
    const ICON = { app: "plug", safety: "shield-alert", task: "mouse-pointer-click" };
    const WORD = { allowed: ["Allowed", "xa-ok"], declined: ["Declined", "xa-bad"], waiting: ["Waiting", "xa-wait"] };
    body.innerHTML = `<p class="xa-day">Approvals history</p>` + items.map((a) => {
      const [w, cls] = WORD[a.decision] || [a.decision, ""];
      return `<div class="xa-row"><span class="xa-ico"><i data-lucide="${ICON[a.kind] || "check"}" class="size-4"></i></span>
        <span class="min-w-0 flex-1"><span class="xa-title">${esc(a.title)} <span class="xa-faint">· ${esc(a.app)}</span></span>
          ${a.detail ? `<span class="xa-sub">${esc(a.detail)}</span>` : ""}
          <span class="xa-time"><span class="${cls}">${esc(w)}</span> · ${esc(ago(a.decided_at || a.ts))}</span></span></div>`;
    }).join("");
  }

  async function browser(body) {
    const { items } = await api("/api/agent/browser");
    if (!items.length) return empty(body, "No browser jobs yet.", "Tasks that work in Xod's browser (forms, shops, sites) show here with the last thing they saw.");
    body.innerHTML = `<p class="xa-day">${items.length} browser job${items.length !== 1 ? "s" : ""}</p>` + items.map((b) => `
      <button class="xa-row" data-open="task:${esc(b.id)}">
        ${b.shot ? `<img class="xa-thumb" src="${esc(b.shot)}" loading="lazy" alt="">` : `<span class="xa-thumb xa-thumb-empty"><i data-lucide="globe" class="size-5"></i></span>`}
        <span class="min-w-0 flex-1 text-left"><span class="xa-title">${esc(b.title)}</span>
          <span class="xa-sub">${esc(b.site || "")}</span><span class="xa-time">${esc(ago(b.at))}</span></span></button>`).join("");
  }

  async function schedules(body) {
    const s = await api("/api/agent/schedules");
    const row = (icon, name, sub, extra = "") => `<div class="xa-row"><span class="xa-ico"><i data-lucide="${icon}" class="size-4"></i></span>
      <span class="min-w-0 flex-1"><span class="xa-title">${esc(name)}</span><span class="xa-sub">${esc(sub)}</span>${extra}</span></div>`;
    const tomorrow9 = (() => { const d = new Date(Date.now() + 86400000); d.setHours(9, 0, 0, 0); return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); })();
    body.innerHTML = `
      <p class="xa-day">Reminders</p>
      <div class="xa-add"><input id="xaRemText" placeholder="Follow up with Saphira" class="xa-input flex-1">
        <input id="xaRemAt" type="datetime-local" value="${tomorrow9}" class="xa-input"><button class="xa-btn" data-rem-add>Add</button></div>
      ${s.reminders.map((r) => row("bell", r.name, r.done ? `Done · ${r.when}` : r.when.replace(/^Once, /, ""))).join("") || `<p class="xa-faint">None. They arrive as a notification here and on your phone.</p>`}
      <p class="xa-day">Daily & weekly</p>
      ${s.daily.map((r) => row("calendar-clock", r.name, `${r.when}${r.enabled ? "" : " · paused"}`)).join("") || `<p class="xa-faint">No routines on a schedule. Save one from any chat or task (Save as routine).</p>`}
      ${s.brief.enabled ? row("sun", "Morning brief", `Every day at ${s.brief.time}`) : ""}
      ${s.interval.length ? `<p class="xa-day">Every few hours</p>${s.interval.map((r) => row("timer", r.name, r.when)).join("")}` : ""}
      ${s.goals.length ? `<p class="xa-day">Goals</p>${s.goals.map((g) => row("target", g.name, `${g.progress.done}/${g.progress.total} steps · check-in ${g.next ? when(g.next) : "soon"}`)).join("")}` : ""}
      ${s.manual.length ? `<p class="xa-day">When you run them</p>${s.manual.map((r) => row("play", r.name, r.when)).join("")}` : ""}
      <button class="xa-btn mt-3" data-open-routines>Routines & goals…</button>`;
  }

  async function identity(body) {
    const d = await api("/api/agent/identity");
    const soul = d.soul || {}, mem = d.memory || {}, you = d.about_you || {};
    body.innerHTML = `
      <div class="xa-cards">
        <button class="xa-card xa-card-soul" data-open-soul><span class="xa-card-k">SOUL</span><span class="xa-card-s">${esc(soul.stage || "")}</span>
          <span class="xa-card-b">${esc(soul.blurb || "")}</span>
          <span class="xa-card-f">${esc(soul.mood || "")}${soul.days ? ` · ${soul.days} day${soul.days !== 1 ? "s" : ""} together` : ""}</span></button>
        <button class="xa-card xa-card-mem" data-open-memory><span class="xa-card-k">MEMORY</span>
          <span class="xa-card-s">${esc(String(you.count ?? 0))} thing${you.count === 1 ? "" : "s"} about you${mem && (mem.total_memories ?? mem.count) != null ? ` · ${esc(String(mem.total_memories ?? mem.count))} memories` : ""}</span>
          <span class="xa-card-b">${(you.recent || []).slice(0, 3).map(esc).join("<br>")}</span>
          <span class="xa-card-f">Open to see or edit</span></button>
      </div>
      ${soul.knows_you_as ? `<p class="xa-sub mt-3">Knows you as ${esc(soul.knows_you_as)}${soul.bond ? ` · bond: ${esc(soul.bond)}` : ""}</p>` : ""}
      ${(soul.interests || []).length ? `<p class="xa-day">Curious about</p><p class="xa-sub">${(soul.interests || []).map(esc).join(" · ")}</p>` : ""}
      ${(soul.lessons || []).length ? `<p class="xa-day">What it learned</p>${soul.lessons.map((l) => `<p class="xa-sub">• ${esc(l)}</p>`).join("")}` : ""}`;
  }

  async function team(body) {
    body.innerHTML = `<p class="xa-faint py-6">Looking at every model and agent Xod can use…</p>`;
    const d = await api("/api/agent/team");
    const alone = new Set(Object.values(d.tiers).map((t) => `${t.provider}:${t.model}`)).size === 1 && !d.agents.some((a) => a.teammate);
    const TIER = { small: ["Small", "Quick lookups and mechanical edits"], standard: ["Standard", "Most work (the chat model)"], large: ["Large", "Hard reasoning, stubborn bugs"] };
    const opt = (b, cur) => `<option value="${esc(b.provider)}|${esc(b.model)}" ${cur && cur.provider === b.provider && cur.model === b.model ? "selected" : ""}>${esc(b.model)} · ${esc(b.where === "online" ? b.provider : b.where)}${b.score ? ` · ${b.score}/100` : ""}</option>`;
    body.innerHTML = `
      <p class="xa-sub">When a job has pieces that can run side by side, or a hard part, Xod hands them to subagents on these tiers. Their steps show as SUBAGENT 01, 02… in Activity. More cloud keys or local models = more choices here.</p>
      <label class="xa-toggle"><input type="checkbox" data-team-on ${d.settings.enabled ? "checked" : ""}> Let Xod use the team</label>
      ${alone ? `<p class="xa-sub xa-wait">Right now every tier is the same model, so Xod works alone. Add another model that calls tools (AI models → Get more), a cloud key (Settings) or a teammate agent, and the team switches on.</p>` : ""}
      ${["small", "standard", "large"].map((t) => {
        const cur = d.tiers[t];
        return `<div class="xa-row"><span class="xa-ico"><i data-lucide="${t === "small" ? "zap" : t === "large" ? "brain" : "cpu"}" class="size-4"></i></span>
          <span class="min-w-0 flex-1"><span class="xa-title">${TIER[t][0]}</span><span class="xa-sub">${TIER[t][1]}</span>
          <select class="xa-input mt-1.5 w-full" data-tier="${t}"><option value="">Automatic (${esc(cur.model)})</option>${d.brains.map((b) => opt(b, cur.auto ? null : cur)).join("")}</select></span></div>`;
      }).join("")}
      <p class="xa-day">Outside agents</p>
      ${d.agents.length ? d.agents.map((a) => `<label class="xa-row"><span class="xa-ico"><i data-lucide="bot" class="size-4"></i></span>
          <span class="min-w-0 flex-1"><span class="xa-title">${esc(a.server)}</span><span class="xa-sub">via ${esc(a.tool)}: ${esc(a.description)}</span></span>
          <input type="checkbox" data-mate="${esc(a.server)}" ${a.teammate ? "checked" : ""} class="accent-amber-400"></label>`).join("")
        : `<p class="xa-faint">Connect another agent as an MCP server (another Xod, a coding agent…) in Connected apps, and it can be a teammate here.</p>`}
      <p class="xa-faint mt-2">Local subagents share your GPU, so they take turns; cloud ones run truly side by side. The safety check looks at every subagent action too.</p>`;
  }

  function empty(body, title, text) {
    body.innerHTML = `<div class="text-center py-12 space-y-2"><p class="text-[13px] text-zinc-300">${esc(title)}</p><p class="text-[12px] text-zinc-500 max-w-xs mx-auto">${esc(text)}</p></div>`;
  }

  // ---------------------------------------------------------------- page
  async function render() {
    const body = document.getElementById("xaBody");
    if (!body) return;
    document.querySelectorAll("[data-xa-tab]").forEach((b) => b.classList.toggle("is-on", b.dataset.xaTab === tab));
    try {
      await ({ activity, usage, approvals, browser, schedules, identity, team }[tab])(body);
    } catch (e) {
      body.innerHTML = `<p class="text-[12px] text-red-300 py-6">Couldn't load this: ${esc(e.message)}</p>`;
    }
    const head = await api("/api/agent").catch(() => null);
    if (head) document.getElementById("xaStatus").textContent = head.status;
    if (window.lucide) lucide.createIcons();
    clearTimeout(timer);
    if ((tab === "activity" || tab === "usage") && document.getElementById("agentPanel")?.classList.contains("is-open")
        && !document.querySelector(".xa-sheet-wrap.is-open")) timer = setTimeout(render, tab === "usage" ? 10000 : 5000);
  }

  async function onClick(e) {
    const t = e.target.closest("[data-xa-tab]");
    if (t) { tab = t.dataset.xaTab; return render(); }
    const rg = e.target.closest("[data-range]");
    if (rg) { range = rg.dataset.range; return render(); }
    if (e.target.closest("[data-close-agent]")) return window.closeAgentPanel();
    if (e.target.closest("[data-close-sheet]") || e.target.matches(".xa-sheet-wrap")) return document.querySelector(".xa-sheet-wrap").classList.remove("is-open");
    const open = e.target.closest("[data-open]");
    if (open) return openDetail(open.dataset.open);
    if (e.target.closest("[data-open-routines]")) { window.closeAgentPanel(); return window.openRoutinesPanel && openRoutinesPanel(); }
    if (e.target.closest("[data-open-soul]")) { window.closeAgentPanel(); return window.XodSoul && XodSoul.openHeart(); }
    if (e.target.closest("[data-open-memory]")) { window.closeAgentPanel(); return window.openMemoryVaultModal && openMemoryVaultModal(); }
    if (e.target.closest("[data-rem-add]")) {
      const text = document.getElementById("xaRemText").value, at = document.getElementById("xaRemAt").value;
      const r = await api("/api/agent/reminders", "POST", { text, at });
      showToast(r.success ? "Reminder set" : r.error);
      return r.success && render();
    }
  }
  async function onChange(e) {
    const tier = e.target.closest("[data-tier]");
    if (tier) {
      const [provider, model] = tier.value ? tier.value.split("|") : [null, null];
      await api("/api/agent/team", "POST", { tiers: { [tier.dataset.tier]: provider ? { provider, model } : null } });
      showToast("Saved"); return render();
    }
    const mate = e.target.closest("[data-mate]");
    if (mate) {
      const chosen = [...document.querySelectorAll("[data-mate]")].filter((x) => x.checked).map((x) => x.dataset.mate);
      await api("/api/agent/team", "POST", { agents: chosen }); return showToast("Saved");
    }
    if (e.target.closest("[data-team-on]")) { await api("/api/agent/team", "POST", { enabled: e.target.checked }); showToast("Saved"); }
  }

  window.openAgentPanel = (which) => {
    if (which) tab = which;
    let root = document.getElementById("agentPanel");
    if (!root) {
      root = document.createElement("div");
      root.id = "agentPanel";
      root.className = "tasks-panel";
      root.innerHTML = `
        <div class="tasks-scrim" data-close-agent></div>
        <aside class="tasks-drawer xm-wide xa-drawer" role="dialog" aria-label="Xod">
          <header class="xa-head">
            <button data-close-agent class="xa-round" title="Close"><i data-lucide="x" class="size-5"></i></button>
            <div class="xa-who"><div class="xa-moon"><span></span><span></span><i></i></div>
              <p class="xa-name">Xod</p><p id="xaStatus" class="xa-status">…</p></div>
            <span class="xa-round xa-round-ghost"></span>
          </header>
          <nav class="xa-tabs">${TABS.map(([k, icon, label]) => `<button data-xa-tab="${k}" title="${label}" aria-label="${label}"><i data-lucide="${icon}" class="size-5"></i><span>${label}</span></button>`).join("")}</nav>
          <div id="xaBody" class="flex-1 overflow-y-auto px-5 py-3"></div>
          <div class="xa-sheet-wrap"><div id="xaSheet" class="xa-sheet"></div></div>
        </aside>`;
      document.body.appendChild(root);
      root.addEventListener("click", onClick);
      root.addEventListener("change", onChange);
    }
    root.classList.remove("hidden");
    requestAnimationFrame(() => root.classList.add("is-open"));
    render();
  };
  window.closeAgentPanel = () => {
    const root = document.getElementById("agentPanel");
    if (!root) return;
    clearTimeout(timer);
    root.classList.remove("is-open");
    setTimeout(() => root.classList.add("hidden"), 250);
  };
})();
