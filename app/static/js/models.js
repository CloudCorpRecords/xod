// AI models: what's in use for each job, what works right now (and why not), every model on every server with what
// it can do and its score, getting more, and model servers on your network. Backend: services/model_hub.py,
// model_scores.py, model_sources.py, capabilities.py.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = async (url, method = "GET", body) =>
    (await fetch(url, { method, headers: body !== undefined ? { "Content-Type": "application/json" } : {}, body: body !== undefined ? JSON.stringify(body) : undefined })).json();
  const btn = "px-2.5 py-1 rounded-lg text-[12px] font-semibold";
  const primary = `${btn} bg-brand-500 text-surface-900 hover:bg-brand-400`;
  const quiet = `${btn} font-medium text-zinc-300 hover:text-white hover:bg-surface-700 border border-white/[0.06]`;
  const field = "w-full bg-surface-900 border border-surface-700 rounded-lg px-2.5 py-1.5 text-[13px] text-white focus:outline-none focus:border-brand-500";
  const DOT = { ok: "bg-emerald-400", limited: "bg-amber-400", off: "bg-zinc-500" };
  let data = null, caps = null, cat = null, found = null, tests = {}, pulls = {}, timer = null;

  const badge = (on, yes, no, unknown) =>
    on === true ? `<span class="xm-badge xm-yes">${yes}</span>` : on === false ? (no ? `<span class="xm-badge xm-no">${no}</span>` : "")
      : unknown ? `<span class="xm-badge">${unknown}</span>` : "";
  const gb = (x) => (x ? `${x} GB` : "");
  const keyOf = (m) => `${m.provider}:${m.model}`;

  function inUse() {
    const r = data.roles;
    return `
      <section class="space-y-2">
        <h4 class="xm-h">In use</h4>
        <div class="xm-role"><b>Chat</b><span>${esc(r.chat.model)} <span class="text-zinc-500">· ${esc(r.chat.server)}</span></span></div>
        <div class="xm-role"><b>Seeing</b><span>${esc(r.seeing.how)}</span></div>
        <div class="xm-role"><b>Memory search</b><span>${esc(r.memory.model)} <span class="text-zinc-500" title="${esc(r.memory.why)}">· fixed</span></span></div>
      </section>`;
  }

  function works() {
    if (!caps) return `<section><h4 class="xm-h">What works now</h4><p class="text-[12px] text-zinc-500">Checking…</p></section>`;
    return `
      <section class="space-y-1.5">
        <h4 class="xm-h">What works now <span class="text-zinc-500 font-normal">${caps.counts.ok} working · ${caps.counts.limited} limited · ${caps.counts.off} off</span></h4>
        ${caps.items.map((i) => `
          <div class="xm-cap">
            <span class="xm-dot ${DOT[i.status]}" title="${i.status}"></span>
            <div class="min-w-0 flex-1"><p class="text-[13px] text-white">${esc(i.name)}</p><p class="text-[12px] text-zinc-400">${esc(i.why)}</p></div>
            ${i.fix ? `<button class="${quiet} shrink-0" data-xm="fix" data-action="${esc(i.fix.action)}">${esc(i.fix.label)}</button>` : ""}
          </div>`).join("")}
      </section>`;
  }

  function scoreBlock(m) {
    const t = tests[keyOf(m)];
    if (t && t.status === "running") return `<p class="text-[12px] text-brand-300">Testing: ${esc(t.step || "…")}</p>`;
    const s = (t && t.result) || m.score;
    if (!s) return "";
    if (s.error) return `<p class="text-[12px] text-red-300">${esc(s.error)}</p>`;
    const rows = Object.entries(s.checks || {}).map(([k, v]) => `<li>${v.ok ? "✓" : "✗"} ${esc(k.replace("_", " "))}: ${esc(v.detail)}</li>`).join("");
    return `<details class="text-[12px] text-zinc-400"><summary class="cursor-pointer"><b class="text-white">${s.score}/100</b> on this computer ·
      ${s.speed_tps} words/s${s.seeing ? ` · sees: ${s.seeing.ok ? "yes" : "no"}` : ""}</summary>
      <ul class="mt-1 space-y-0.5">${rows}</ul>${(s.notes || []).map((n) => `<p class="text-amber-300 mt-1">${esc(n)}</p>`).join("")}</details>`;
  }

  function modelCard(m) {
    const using = m.in_use || [];
    return `
      <div class="xm-model ${using.length ? "xm-using" : ""}">
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <p class="text-[13px] text-white font-mono truncate" title="${esc(m.model)}">${esc(m.model)}</p>
            <p class="text-[11px] text-zinc-500">${esc(m.server)} · ${esc(m.where)}${m.size_gb ? ` · ${gb(m.size_gb)}` : ""}${m.fits === false ? ` · <span class="text-amber-300">too big for the GPU</span>` : ""}${m.online === false ? ` · <span class="text-red-300">not answering</span>` : ""}</p>
          </div>
          ${using.length ? `<span class="xm-badge xm-yes shrink-0">${using.join(", ")}</span>` : ""}
        </div>
        <div class="flex flex-wrap gap-1 mt-1.5">
          ${badge(m.tools, "calls tools", "no tools", "tools: untested")}${badge(m.vision, "sees", "")}${badge(m.thinking, "thinks", "")}
          ${m.context ? `<span class="xm-badge">${Math.round(m.context / 1024)}k context</span>` : ""}
        </div>
        ${scoreBlock(m)}
        <div class="flex flex-wrap gap-1.5 mt-2">
          ${using.includes("chat") ? "" : `<button class="${quiet}" data-xm="use" data-job="chat" data-i="${data.models.indexOf(m)}">Use for chat</button>`}
          ${m.vision && m.provider === "ollama" && !using.includes("seeing") ? `<button class="${quiet}" data-xm="use" data-job="seeing" data-i="${data.models.indexOf(m)}">Use for seeing</button>` : ""}
          ${m.where === "online" ? "" : `<button class="${quiet}" data-xm="test" data-i="${data.models.indexOf(m)}">${m.score ? "Test again" : "Test it"}</button>`}
        </div>
      </div>`;
  }

  function pullButton(name) {
    const p = pulls[name];
    if (p && p.status === "downloading") return `<span class="text-[12px] text-brand-300">${p.percent ?? 0}%${p.total_gb ? ` of ${p.total_gb} GB` : ""}</span>`;
    if (p && p.status === "done") return `<span class="text-[12px] text-emerald-300">Downloaded</span>`;
    if (p && (p.status === "error" || p.status === "failed")) return `<span class="text-[12px] text-red-300" title="${esc(p.error)}">Failed</span>`;
    return `<button class="${primary}" data-xm="pull" data-name="${esc(name)}">Download</button>`;
  }

  function getMore() {
    if (!cat) return `<section><h4 class="xm-h">Get more models</h4><p class="text-[12px] text-zinc-500">Loading…</p></section>`;
    const row = (m) => `
      <div class="xm-get">
        <div class="min-w-0 flex-1"><p class="text-[13px] text-white">${esc(m.label)} <span class="text-zinc-500">· ${gb(m.size_gb)}</span>
          ${m.recommended ? `<span class="xm-badge xm-yes">best fit</span>` : ""}${m.fits ? "" : `<span class="xm-badge xm-no">too big</span>`}</p>
          <p class="text-[12px] text-zinc-400">${esc(m.blurb)}</p></div>
        ${m.installed ? `<span class="text-[12px] text-zinc-500">Have it</span>` : pullButton(m.name)}
      </div>`;
    return `
      <section class="space-y-2">
        <h4 class="xm-h">Get more models</h4>
        ${cat.running ? "" : `<p class="text-[12px] text-amber-300">Ollama isn't running; downloads need it. ${cat.can_install ? "Install or start it in Tools → Setup & AI model." : ""}</p>`}
        <p class="text-[12px] text-zinc-500">For chat (they all call tools)</p>${cat.chat.map(row).join("")}
        <p class="text-[12px] text-zinc-500 pt-1">For seeing (looks at pictures and pages for a chat model that can't)</p>${cat.seeing.map(row).join("")}
        <div class="pt-1 space-y-1.5">
          <p class="text-[12px] text-zinc-500">Or search Hugging Face (GGUF models; Ollama downloads them):</p>
          <div class="flex gap-1.5"><input id="xmSearch" class="${field}" placeholder="e.g. qwen, llama, mistral, phi" value="${esc(found?.q || "")}">
            <button class="${quiet}" data-xm="search">Search</button></div>
          ${found ? (found.error ? `<p class="text-[12px] text-red-300">${esc(found.error)}</p>` : found.results.map((r) => `
            <div class="xm-get"><div class="min-w-0 flex-1"><p class="text-[13px] text-white font-mono truncate">${esc(r.repo)}</p>
              <p class="text-[11px] text-zinc-500">${r.downloads.toLocaleString()} downloads${r.sees ? " · sees" : ""}</p></div>${pullButton(r.pull)}</div>`).join("")) : ""}
        </div>
      </section>`;
  }

  function servers() {
    return `
      <section class="space-y-2">
        <h4 class="xm-h">Model servers</h4>
        <p class="text-[12px] text-zinc-400">Servers on this computer are found by themselves. Add one on your network (a PC with a big GPU,
          a home server, Tailscale) or an online OpenAI-compatible service. Xod never switches to one by itself.</p>
        ${data.sources.map((s) => `<div class="xm-get"><div class="min-w-0 flex-1"><p class="text-[13px] text-white">${esc(s.name)}</p>
          <p class="text-[11px] text-zinc-500 font-mono">${esc(s.url)} · ${esc(s.where)}${s.has_key ? " · key in Keychain" : ""}</p></div>
          <button class="${quiet}" data-xm="unsource" data-id="${esc(s.id)}">Remove</button></div>`).join("")}
        <div class="grid gap-1.5">
          <input id="xmUrl" class="${field}" placeholder="Address, e.g. http://192.168.1.20:11434">
          <div class="flex gap-1.5"><input id="xmName" class="${field}" placeholder="Name (optional)"><input id="xmKey" class="${field}" type="password" placeholder="Key (if it needs one)"></div>
          <div><button class="${primary}" data-xm="source">Add server</button> <span id="xmSrcMsg" class="text-[12px]"></span></div>
        </div>
      </section>`;
  }

  function render() {
    const body = document.getElementById("modelsBody");
    if (!body) return;
    if (!data) { body.innerHTML = `<p class="text-[12px] text-zinc-500">Looking for models…</p>`; return; }
    body.innerHTML = inUse() + works() +
      `<section class="space-y-2" id="xmList"><h4 class="xm-h">Your models <span class="text-zinc-500 font-normal">${((n, s) => `${n} on ${s} server${s === 1 ? "" : "s"}`)(data.models.length, new Set(data.models.map((m) => m.server)).size)}</span></h4>
        ${data.models.map(modelCard).join("") || `<p class="text-[12px] text-zinc-500">No models found yet.</p>`}</section>` + getMore() + servers();
  }

  async function load() {
    [data, caps] = await Promise.all([api("/api/models").catch(() => null), api("/api/capabilities").catch(() => null)]);
    render();
    cat = await api("/api/models/catalog").catch(() => null);
    render();
  }

  function watch() {
    clearInterval(timer);
    timer = setInterval(async () => {
      let changed = false;
      for (const [k, t] of Object.entries(tests)) {
        if (t.status !== "running") continue;
        tests[k] = await api(`/api/models/test?key=${encodeURIComponent(k)}`);
        changed = true;
        if (tests[k].status !== "running") { data = await api("/api/models"); caps = await api("/api/capabilities"); }
      }
      for (const [n, p] of Object.entries(pulls)) {
        if (p.status !== "downloading") continue;
        pulls[n] = { ...(await api(`/api/ollama/pull?name=${encodeURIComponent(p.as || n)}`)), as: p.as };
        changed = true;
        if (pulls[n].status === "done") { data = await api("/api/models"); cat = await api("/api/models/catalog"); caps = await api("/api/capabilities"); }
      }
      if (changed) render();
      if (!Object.values(tests).some((t) => t.status === "running") && !Object.values(pulls).some((p) => p.status === "downloading")) clearInterval(timer);
    }, 1500);
  }

  async function pull(name) {
    const r = await api("/api/ollama/pull", "POST", { name });       // a Hugging Face repo is resolved to a quantization here
    if (!r.success) return showToast(r.error || "Couldn't start the download");
    pulls[name] = { status: "downloading", percent: 0, as: r.name };   // progress is under the resolved name
    render(); watch();
  }

  async function onClick(e) {
    const el = e.target.closest("[data-xm], [data-close-models]");
    if (!el) return;
    if (el.hasAttribute("data-close-models")) return window.closeModelsPanel();
    const a = el.dataset.xm, m = data && data.models[Number(el.dataset.i)];
    if (a === "use") {
      el.disabled = true;
      const r = await api("/api/models/use", "POST", { job: el.dataset.job, provider: m.provider, model: m.model, base_url: m.base_url });
      showToast(r.success ? `${m.model} is now used for ${el.dataset.job}` : r.error);
      return load();
    }
    if (a === "test") {
      const r = await api("/api/models/test", "POST", { provider: m.provider, model: m.model, base_url: m.base_url, sees: !!m.vision });
      tests[keyOf(m)] = { status: "running", step: "Starting" };
      render(); watch();
      if (!r.success) showToast(r.error || "Couldn't start the test");
      return;
    }
    if (a === "pull") return pull(el.dataset.name);
    if (a === "search") {
      const q = document.getElementById("xmSearch").value;
      el.disabled = true; el.textContent = "Searching…";
      found = { ...(await api(`/api/models/search?q=${encodeURIComponent(q)}`)), q };
      return render();
    }
    if (a === "source") {
      const msg = document.getElementById("xmSrcMsg");
      msg.textContent = "Checking…"; msg.className = "text-[12px] text-zinc-400";
      const r = await api("/api/models/sources", "POST", { url: document.getElementById("xmUrl").value, name: document.getElementById("xmName").value,
                                                           key: document.getElementById("xmKey").value });
      if (!r.success) { msg.textContent = r.error; msg.className = "text-[12px] text-red-300"; return; }
      showToast(`Added: ${r.models.length} model${r.models.length === 1 ? "" : "s"}`);
      return load();
    }
    if (a === "unsource") { await api(`/api/models/sources/${el.dataset.id}`, "DELETE"); return load(); }
    if (a === "fix") {
      const act = el.dataset.action || "";
      if (act === "models") return document.getElementById("xmList")?.scrollIntoView({ behavior: "smooth" });
      if (act === "test") {
        const cur = data.models.find((x) => (x.in_use || []).includes("chat"));
        if (cur) { el.dataset.i = data.models.indexOf(cur); el.dataset.xm = "test"; return onClick({ target: el }); }
        return;
      }
      if (act === "get_vision") {
        const best = (cat?.seeing || []).find((x) => x.fits && !x.installed);
        return best ? pull(best.name) : showToast("No vision model fits this computer's GPU.");
      }
      if (act.startsWith("pull:")) return pull(act.slice(5));
      if (act.startsWith("link:")) return window.open(act.slice(5), "_blank", "noopener");
      if (act === "install_media") { await api("/api/media/libraries/install", "POST"); return showToast("Downloading the image engine in the background"); }
      if (act === "connectors") { window.closeModelsPanel(); return window.openMcpPanel && window.openMcpPanel(); }
      if (act === "phone") { window.closeModelsPanel(); return window.openPhonePanel && window.openPhonePanel(); }
      if (act === "keys") { window.closeModelsPanel(); return window.openSettingsModal && window.openSettingsModal(); }
    }
  }

  window.openModelsPanel = () => {
    let root = document.getElementById("modelsPanel");
    if (!root) {
      root = document.createElement("div");
      root.id = "modelsPanel";
      root.className = "tasks-panel";
      root.innerHTML = `
        <div class="tasks-scrim" data-close-models></div>
        <aside class="tasks-drawer xm-wide" role="dialog" aria-label="AI models">
          <header class="px-5 pt-5 pb-3 flex items-start justify-between gap-3 border-b border-white/[0.06]">
            <div><h3 class="font-bold text-lg text-white">AI models</h3>
              <p class="text-[12px] text-zinc-400 mt-0.5">Which model does what, what works right now, and more to try.</p></div>
            <button data-close-models class="p-1 -mr-1 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700" title="Close"><i data-lucide="x" class="size-4"></i></button>
          </header>
          <div id="modelsBody" class="flex-1 overflow-y-auto px-5 py-4 space-y-5"></div>
        </aside>`;
      document.body.appendChild(root);
      root.addEventListener("click", onClick);
      root.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.id === "xmSearch") root.querySelector('[data-xm="search"]').click(); });
      if (window.lucide) lucide.createIcons();
    }
    root.classList.remove("hidden");
    requestAnimationFrame(() => root.classList.add("is-open"));
    data = null; caps = null; render(); load();
  };
  window.closeModelsPanel = () => {
    const root = document.getElementById("modelsPanel");
    if (!root) return;
    root.classList.remove("is-open");
    setTimeout(() => root.classList.add("hidden"), 250);
  };
})();
