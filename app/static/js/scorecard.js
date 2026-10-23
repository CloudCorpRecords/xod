// The Scorecard (backend: services/scorecard.py, /api/scorecard): how Xod is doing, from what already happened. Chat
// turns (its own check after each answer, and your corrections), Crew jobs, model calls, and the routing check
// (services/evals.py: real requests replayed through the router and planner), each next to the window before it.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (x) => (x === null || x === undefined ? "–" : `${Math.round(x * 100)}%`);
  const when = (t) => new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  let root = null, days = 7, timer = null;

  const api = async (url, body) => {
    try {
      const r = await fetch(url, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      return await r.json();
    } catch (e) { return {}; }
  };

  // A number with how it moved since the window before (up is good unless `lowerIsBetter`).
  function stat(label, now, before, opts = {}) {
    const fmt = opts.fmt || ((x) => (x === null || x === undefined ? "–" : x));
    let delta = "";
    if (typeof now === "number" && typeof before === "number" && now !== before) {
      const better = opts.lowerIsBetter ? now < before : now > before;
      delta = `<span class="text-[11px] ${better ? "text-emerald-400" : "text-red-300"}">${now > before ? "▲" : "▼"} ${esc(fmt(before))} before</span>`;
    }
    return `<div class="rounded-xl bg-surface-900 border border-white/[0.06] px-3 py-2">
      <div class="text-zinc-500 text-[11px]">${esc(label)}</div>
      <div class="text-white text-[18px] font-semibold leading-tight">${esc(fmt(now))}</div>${delta}</div>`;
  }

  function render(d) {
    // The period before with nothing in it isn't "0 before": it's no comparison at all.
    const n = d.now || {}, b = d.before || {}, t = n.turns || {}, c = n.crew || {}, m = n.calls || {};
    const tb = (b.turns || {}).turns ? b.turns : {}, cb = (b.crew || {}).jobs ? b.crew : {}, mb = (b.calls || {}).calls ? b.calls : {};
    const r = d.routing || {}, running = r.running;
    const suiteBox = (x) => {
      const L = x.latest;
      const failed = L ? (L.failed || []).map((f) => `<div class="text-[12px] py-1"><span class="text-zinc-200">${esc(f.q)}</span><div class="text-red-300 text-[11px]">${esc(f.why)}</div></div>`).join("") : "";
      const runs = (x.runs || []).slice(0, 4).map((y) => `
        <div class="flex items-center gap-2 text-[11px] py-0.5"><span class="text-zinc-500 w-28 shrink-0">${when(y.at)}</span>
          <span class="${y.passed === y.total ? "text-emerald-400" : "text-amber-300"} w-14 shrink-0">${y.passed}/${y.total}</span>
          <span class="text-zinc-400 truncate">${esc(y.label || "")}${y.partial ? " (some cases)" : ""}</span></div>`).join("");
      return `<div class="rounded-xl bg-surface-900 border border-white/[0.06] p-3 mt-2">
        <div class="text-zinc-300 text-[12px] mb-1">${esc(x.about)}</div>
        ${L ? `<div class="flex items-baseline gap-3"><span class="text-[22px] font-semibold ${L.passed === L.total ? "text-emerald-400" : "text-amber-300"}">${L.passed}/${L.total}</span>
            <span class="text-zinc-400 text-[11px]">${esc(L.model)} · ${when(L.at)}</span></div>${failed}` : `<p class="text-zinc-500 text-[12px]">Not run yet.</p>`}
        ${runs ? `<div class="mt-1">${runs}</div>` : ""}</div>`;
    };
    return `
      <div class="space-y-5">
        <section><h4 class="text-white font-semibold mb-2">Chat answers</h4>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
            ${stat("Answers", t.turns, tb.turns)}
            ${stat("Passed Xod's own check", t.pass_rate, tb.pass_rate, { fmt: pct })}
            ${stat("You corrected it", (t.corrected || 0) + (t.asked_again || 0), (tb.corrected || 0) + (tb.asked_again || 0), { lowerIsBetter: true })}
            ${stat("Marked bad", t.bad, tb.bad, { lowerIsBetter: true })}
          </div>
          <div class="mt-2"><div class="text-zinc-400 text-[12px] mb-1">What went wrong</div>${wrong}</div></section>
        <section><h4 class="text-white font-semibold mb-2">Crew jobs</h4>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
            ${stat("Jobs", c.jobs, cb.jobs)}
            ${stat("Finished well", c.success_rate, cb.success_rate, { fmt: pct })}
            ${stat("Asked your OK", c.approvals_asked, cb.approvals_asked)}
            ${stat("Typical length (min)", c.median_minutes, cb.median_minutes, { lowerIsBetter: true })}
          </div></section>
        <section><h4 class="text-white font-semibold mb-2">Model calls</h4>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
            ${stat("Calls", m.calls, mb.calls)}
            ${stat("Average seconds", m.avg_seconds, mb.avg_seconds, { lowerIsBetter: true })}
            ${stat("On this computer", m.local_calls, mb.local_calls)}
            ${stat("Cost", m.cost_usd, mb.cost_usd, { lowerIsBetter: true, fmt: (x) => `$${Number(x || 0).toFixed(2)}` })}
          </div></section>
        <section><div class="flex items-center gap-2 mb-2"><h4 class="text-white font-semibold">Routing check</h4>
            <span class="text-zinc-500 text-[11px] flex-1">Real requests put through Xod's router and planner with its model, to see it picks the right thing. Nothing is run.</span>
            <button data-sc="run" ${running ? "disabled" : ""} class="px-2.5 py-1 rounded-md bg-surface-700 text-zinc-200 hover:bg-surface-600 text-[12px] disabled:opacity-50">${running ? `Checking ${running.done}/${running.total}…` : "Run it now"}</button></div>
          ${(r.suites || []).map(suiteBox).join("")}</section>
      </div>`;
  }

  async function refresh() {
    if (!root || root.classList.contains("hidden")) return;
    const d = await api(`/api/scorecard?days=${days}`);
    const box = root.querySelector("[data-sc-body]");
    const html = render(d);
    if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
    clearTimeout(timer);
    timer = setTimeout(refresh, d.routing && d.routing.running ? 2000 : 15000);
  }

  function open() {
    if (!root) {
      root = document.createElement("div");
      root.id = "scorecardPanel";
      root.className = "fixed inset-0 z-[85] flex items-center justify-center p-4 bg-black/60";
      root.innerHTML = `
        <div class="w-full max-w-3xl max-h-[86vh] flex flex-col rounded-2xl bg-surface-850 border border-white/[0.08] shadow-2xl text-[13px] overflow-hidden">
          <div class="flex items-center gap-3 px-4 py-3 border-b border-white/[0.06]">
            <div class="flex-1"><h3 class="text-white font-semibold text-[15px]">Scorecard</h3>
              <p class="text-zinc-500 text-[11px]">How Xod is doing, from what already happened, next to the period before.</p></div>
            <select data-sc-days class="bg-surface-700 text-zinc-200 rounded-md px-2 py-1 text-[12px]">
              <option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></select>
            <button data-sc="close" class="p-1 rounded-lg text-zinc-500 hover:text-white" title="Close">✕</button>
          </div>
          <div data-sc-body class="flex-1 overflow-y-auto p-4"></div>
        </div>`;
      document.body.appendChild(root);
      root.addEventListener("click", async (e) => {
        if (e.target === root || e.target.closest('[data-sc="close"]')) { root.classList.add("hidden"); clearTimeout(timer); return; }
        if (e.target.closest('[data-sc="run"]')) { await api("/api/evals/run", {}); refresh(); }
      });
      root.querySelector("[data-sc-days]").addEventListener("change", (e) => { days = +e.target.value; refresh(); });
    }
    root.classList.remove("hidden");
    refresh();
  }

  window.openScorecard = open;
})();
