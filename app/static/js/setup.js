// First-run setup wizard (backend: services/setup_wizard.py, services/local_engine.py).
// Steps: check the Mac → pick an AI model (LM Studio / Ollama / built-in / cloud) → download if
// needed → say hi to it → starter tasks. Opens by itself for new users; Tools → "Setup & AI model" reopens it.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let state = null;
  let choice = null;      // {provider, model, label, needsDownload}
  let pollTimer = null;

  const STARTERS = [
    { icon: "globe", text: "What's new on Hacker News today? Give me the top 5 with one line each." },
    { icon: "folder-search", text: "Look through my Downloads folder and tell me what's taking up the most space." },
    { icon: "image", text: "Make an image of a cozy cabin under the northern lights." },
    { icon: "file-text", text: "Research the best note-taking apps for Mac and save a short comparison to a project." },
  ];

  function shell(inner) {
    let root = $("setupWizard");
    if (!root) {
      root = document.createElement("div");
      root.id = "setupWizard";
      root.className = "fixed inset-0 z-[90] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm";
      document.body.appendChild(root);
    }
    root.innerHTML = `
      <div class="w-full max-w-2xl max-h-[92vh] overflow-y-auto rounded-3xl bg-surface-850 border border-white/[0.08] shadow-2xl">
        <div class="px-7 pt-6 pb-2 flex items-center justify-between">
          <div class="flex items-center gap-3">
            <div class="xod-mascot xod-mascot--small" data-mood="happy"></div>
            <span class="font-display text-xl text-white">Set up Xod</span>
          </div>
          <button data-setup="close" class="p-1.5 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700" title="Close"><i data-lucide="x" class="size-4"></i></button>
        </div>
        <div class="px-7 pb-7">${inner}</div>
      </div>`;
    if (window.lucide) lucide.createIcons();
  }

  function chip(ok, text) {
    return `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[12px] ${ok ? "bg-emerald-500/10 text-emerald-300" : "bg-amber-500/10 text-amber-300"}">
      <i data-lucide="${ok ? "check" : "alert-triangle"}" class="size-3.5"></i>${esc(text)}</span>`;
  }

  function option(o) {
    return `<button data-setup="pick" data-provider="${esc(o.provider)}" data-model="${esc(o.model)}" data-label="${esc(o.label)}" data-download="${o.needsDownload ? 1 : 0}"
      class="w-full text-left p-4 rounded-2xl border transition-all ${o.recommended ? "border-brand-500/60 bg-brand-500/[0.07]" : "border-white/[0.08] bg-surface-900/60"} hover:border-brand-400 ${o.disabled ? "opacity-40 pointer-events-none" : ""}">
      <div class="flex items-start gap-3">
        <div class="size-9 rounded-xl flex items-center justify-center shrink-0 ${o.recommended ? "bg-brand-500/20 text-brand-300" : "bg-surface-700 text-zinc-300"}"><i data-lucide="${o.icon}" class="size-4"></i></div>
        <div class="min-w-0 flex-1">
          <p class="text-white text-[14px] font-medium flex items-center gap-2 flex-wrap">${esc(o.title)}${o.recommended ? `<span class="text-[10px] px-2 py-0.5 rounded-full bg-brand-500 text-surface-900 font-semibold">Recommended</span>` : ""}</p>
          <p class="text-[12px] text-zinc-400 mt-0.5">${esc(o.sub)}</p>
        </div>
      </div>
    </button>`;
  }

  function stepChoose() {
    const s = state;
    const opts = [];
    const lm = s.lm_studio, ol = s.ollama;
    // An API key you already saved comes first: nothing to download and no local model running.
    for (const [i, c] of (s.cloud || []).entries()) {
      opts.push({ provider: c.provider, model: c.model, label: c.label, icon: "cloud", title: `${c.label} (your API key)`,
                  sub: "Uses the API key you saved. Nothing to download, and no local model has to run on this computer.",
                  recommended: i === 0 });
    }
    const keyFirst = opts.length > 0;
    if (lm.online && (lm.loaded || lm.models.length)) {
      const m = lm.loaded || lm.models[0];
      // A model over ~6 GB on a 16 GB Mac pushes it into swap, and every answer waits on the disk.
      const gb = (lm.sizes || {})[m];
      const heavy = gb && s.mac.ram_gb && gb > s.mac.ram_gb * 0.38;
      opts.push({ provider: "lm_studio", model: m, label: m, icon: "server", title: `LM Studio: ${m}`,
                  sub: (lm.loaded ? "Already running. Nothing to download." : "Installed in LM Studio. Xod loads it when needed.")
                       + (heavy ? ` At ${gb.toFixed(1)} GB it's large for a ${s.mac.ram_gb} GB Mac, so answers will be slow; the built-in ${(s.builtin.find((x) => x.recommended) || {}).label || "model"} is about three times faster.` : ""),
                  recommended: !!lm.loaded && !heavy && !keyFirst });
    }
    // Ollama models are listed in their own section (ollamaSection) on a PC; on a Mac, alongside the built-in ones.
    if (s.mac.apple_silicon && ol.online && ol.models.length) {
      for (const m of ol.models) opts.push({ provider: "ollama", model: m, label: m, icon: "server", title: `Ollama: ${m}`,
                                             sub: "Already installed. Nothing to download." });
    }
    const rec = s.builtin.find((m) => m.recommended);
    const noLocal = !opts.some((o) => o.recommended);
    for (const m of s.builtin) {
      if (!m.recommended && !m.downloaded) continue;   // others are under "More sizes"
      opts.push({ provider: "xod", model: m.repo, label: m.label, icon: "sparkles",
                  title: `Built-in: ${m.label}`, needsDownload: !m.downloaded,
                  sub: `${m.blurb} ${m.downloaded ? "Downloaded." : `One ${m.download_gb} GB download.`}`,
                  recommended: m.recommended && noLocal, disabled: !s.mac.apple_silicon });
    }
    const more = s.builtin.filter((m) => !m.recommended && !m.downloaded);
    shell(`
      <h2 class="text-white text-lg font-semibold mt-2">${s.mac.windows ? "Your PC" : "Your Mac"}</h2>
      <div class="flex flex-wrap gap-2 mt-2">
        ${s.mac.windows ? "" : chip(s.mac.apple_silicon, s.mac.apple_silicon ? "Apple silicon" : "Not Apple silicon")}
        ${hub && !s.mac.apple_silicon ? chip(hub.machine.runs_on !== "cpu", hub.machine.summary) : ""}
        ${hub && hub.machine.power && hub.machine.power.battery && !hub.machine.power.plugged_in ? chip(false, "On battery") : ""}
        ${chip(s.mac.ram_gb >= 8, `${s.mac.ram_gb} GB memory`)}
        ${chip(s.mac.disk_free_gb >= 10, `${Math.round(s.mac.disk_free_gb)} GB free`)}
        ${chip(true, s.mac.os || `macOS ${s.mac.macos}`)}
      </div>
      ${s.mac.issues.map((i) => `<p class="text-[12px] text-amber-300 mt-2">${esc(i)}</p>`).join("")}

      <h2 class="text-white text-lg font-semibold mt-6">Pick Xod's brain</h2>
      <p class="text-[13px] text-zinc-400 mt-1">${keyFirst ? "Your API key works right away. The local options below run on your " + (s.mac.windows ? "PC" : "Mac") + " and stay private. " : opts.length || (hub && hub.models.length) ? `Everything below runs on your ${s.mac.windows ? "PC" : "Mac"} and stays private. ` : "No model found on this computer yet. "}You can change it any time in Settings.</p>
      <div class="space-y-2.5 mt-3">${opts.map(option).join("")}</div>
      ${otherServers()}
      ${s.mac.apple_silicon ? "" : ollamaSection()}
      ${more.length ? `
        <details class="mt-3">
          <summary class="text-[13px] text-zinc-400 cursor-pointer hover:text-white">More built-in sizes</summary>
          <div class="space-y-2.5 mt-2.5">${more.map((m) => option({ provider: "xod", model: m.repo, label: m.label, icon: "sparkles",
            title: `Built-in: ${m.label}`, needsDownload: true, disabled: !m.fits || !s.mac.apple_silicon,
            sub: `${m.blurb} ${m.download_gb} GB download.${m.fits ? "" : ` Needs ${m.min_ram_gb} GB+ of memory.`}` })).join("")}</div>
        </details>` : ""}
      <div class="mt-5 p-4 rounded-2xl border border-white/[0.06] bg-surface-900/40 flex items-center gap-3">
        <i data-lucide="cloud" class="size-4 text-zinc-400 shrink-0"></i>
        <p class="text-[12px] text-zinc-400 flex-1">${opts.length ? "Prefer a cloud model" : "Use a cloud model"} (Fireworks, Claude, GPT, Gemini)? Add its API key in Settings. ${s.cloud_keys.length ? `You already have: ${esc(s.cloud_keys.join(", "))}.` : ""}</p>
        <button data-setup="settings" class="px-3 py-1.5 rounded-lg text-[12px] text-zinc-200 bg-surface-700 hover:bg-surface-600 shrink-0">Open Settings</button>
      </div>`);
  }

  // ---------------------------------------------------------------- Ollama (backend: services/ollama_hub.py)
  const btn = "px-3 py-1.5 rounded-lg text-[12px] shrink-0";
  const badge = (cls, text) => `<span class="text-[10px] px-2 py-0.5 rounded-full ${cls}">${esc(text)}</span>`;

  function pullBar(name, p) {
    return `<div data-pull="${esc(name)}" class="mt-2.5">
      <div class="h-2 rounded-full bg-surface-700 overflow-hidden"><div data-pull-bar class="h-full bg-brand-500 transition-all" style="width:${p.percent || 0}%"></div></div>
      <div class="flex items-center justify-between mt-1.5">
        <span data-pull-text class="text-[11px] text-zinc-400">${esc(pullText(p))}</span>
        <button data-setup="ol-cancel" data-name="${esc(name)}" class="text-[11px] text-zinc-500 hover:text-white">Stop</button>
      </div></div>`;
  }

  function pullText(p) {
    if (!p.total_gb) return `${p.phase || "Starting"}…`;
    return `${p.downloaded_gb} of ${p.total_gb} GB (${p.percent}%)${p.mb_per_s ? ` · ${p.mb_per_s} MB/s` : ""}${p.phase && p.phase !== "Downloading" ? ` · ${p.phase}` : ""}`;
  }

  // Where models run on this computer (services/machine.py): a GPU, a Mac's shared memory, or the processor.
  function bestGpu() {
    const pc = hub.machine;
    return pc.runs_on === "gpu" ? pc.gpus.find((g) => g.name === pc.gpu) : null;
  }

  function fitText(gb, fits, fitsNow) {
    const pc = hub.machine, g = bestGpu();
    const busy = fits && fitsNow === false ? ` · other programs are using your ${pc.runs_on === "gpu" ? "GPU" : "memory"} right now (${hub.room_now_gb} GB free), so it would start slower` : "";
    if (pc.runs_on === "cpu") return (fits ? "Fits your memory (runs on the processor)" : "Too big for this computer's memory: very slow") + busy;
    if (pc.runs_on === "unified") return (fits ? `Fits your ${pc.ram_gb} GB Mac` : `Too big for your ${pc.ram_gb} GB Mac: very slow`) + busy;
    if (fits) return "Fits your GPU" + busy;
    return gb < g.memory_gb ? `Tight on your ${g.memory_gb} GB GPU: some of it may run on the processor, slower`
                            : "Bigger than your GPU: runs partly on the processor, slower";
  }

  function installedRow(m) {
    const caps = [m.tools ? badge("bg-emerald-500/10 text-emerald-300", "Tools") : badge("bg-amber-500/10 text-amber-300", "No tools"),
                  m.thinking ? badge("bg-surface-700 text-zinc-300", "Thinks") : "", m.vision ? badge("bg-surface-700 text-zinc-300", "Sees images") : ""].join(" ");
    return `<div class="flex items-center gap-2 p-3 rounded-2xl border ${m.in_use ? "border-brand-500/60 bg-brand-500/[0.07]" : "border-white/[0.08] bg-surface-900/60"}">
      <i data-lucide="server" class="size-4 text-zinc-400 shrink-0 ml-1"></i>
      <div class="min-w-0 flex-1">
        <p class="text-white text-[13px] font-medium truncate flex items-center gap-2">${esc(m.name)} ${m.in_use ? badge("bg-brand-500 text-surface-900 font-semibold", "In use") : ""}</p>
        <p class="text-[11px] text-zinc-400 mt-0.5 flex items-center gap-1.5 flex-wrap">${m.size_gb} GB ${caps}</p>
        ${m.tools ? "" : `<p class="text-[11px] text-amber-300/90 mt-1">Can't use tools, so it can chat but not browse, work with files or run tasks.</p>`}
      </div>
      ${m.in_use ? "" : `<button data-setup="pick" data-provider="ollama" data-model="${esc(m.name)}" data-label="${esc(m.name)}" data-download="0" class="${btn} bg-surface-700 text-white hover:bg-surface-600">Use</button>`}
      ${m.in_use ? "" : `<button data-setup="ol-delete" data-name="${esc(m.name)}" class="p-1.5 rounded-lg text-zinc-500 hover:text-red-300 hover:bg-surface-700" title="Delete from this computer"><i data-lucide="trash-2" class="size-3.5"></i></button>`}
    </div>`;
  }

  function catalogCard(m) {
    const p = m.pull || {};
    const busy = p.status === "downloading";
    const action = busy ? "" : `<button data-setup="ol-pull" data-name="${esc(m.name)}" class="${btn} ${m.recommended ? "bg-brand-500 text-surface-900 font-semibold hover:bg-brand-400" : "bg-surface-700 text-white hover:bg-surface-600"}">Download</button>`;
    return `<div class="p-3.5 rounded-2xl border ${m.recommended ? "border-brand-500/60 bg-brand-500/[0.07]" : "border-white/[0.08] bg-surface-900/60"}">
      <div class="flex items-start gap-3">
        <div class="min-w-0 flex-1">
          <p class="text-white text-[13px] font-medium flex items-center gap-2 flex-wrap">${esc(m.label)}
            ${m.recommended ? badge("bg-brand-500 text-surface-900 font-semibold", "Recommended") : ""}</p>
          <p class="text-[11px] text-zinc-400 mt-0.5">${esc(m.blurb)}</p>
          <p class="text-[11px] mt-1 ${m.fits && m.fits_now !== false ? "text-emerald-300/90" : "text-amber-300/90"}">${m.size_gb} GB · ${fitText(m.size_gb, m.fits, m.fits_now)}</p>
        </div>${action}
      </div>
      ${busy ? pullBar(m.name, p) : p.status === "error" ? `<p class="text-[11px] text-red-300 mt-2">${esc(p.error)}</p>` : ""}
    </div>`;
  }

  // Other model servers Xod found running here (llama.cpp, Jan, KoboldCpp…; services/autoconnect.py).
  function otherServers() {
    const servers = ((auto && auto.servers) || []).filter((x) => x.provider === "local");
    if (!servers.length) return "";
    const cur = state.current;
    return `<h3 class="text-white text-[15px] font-semibold mt-6">Other model servers <span class="text-zinc-500 font-normal text-[12px]">found running on this computer</span></h3>
      <div class="space-y-2 mt-3">${servers.flatMap((sv) => (sv.models.length ? sv.models : [{ id: "default" }]).map((m) => {
        const inUse = cur.provider === "local" && cur.model === m.id && (auto.current.base_url || "") === sv.base_url;
        return `<div class="flex items-center gap-2 p-3 rounded-2xl border ${inUse ? "border-brand-500/60 bg-brand-500/[0.07]" : "border-white/[0.08] bg-surface-900/60"}">
          <i data-lucide="server" class="size-4 text-zinc-400 shrink-0 ml-1"></i>
          <div class="min-w-0 flex-1">
            <p class="text-white text-[13px] font-medium truncate flex items-center gap-2">${esc(m.id)} ${inUse ? badge("bg-brand-500 text-surface-900 font-semibold", "In use") : ""}</p>
            <p class="text-[11px] text-zinc-400 mt-0.5">${esc(sv.name)} · ${esc(sv.base_url.replace(/\/v1$/, ""))}</p>
          </div>
          ${inUse ? "" : `<button data-setup="pick" data-provider="local" data-model="${esc(m.id)}" data-label="${esc(m.id)}" data-base="${esc(sv.base_url)}" data-download="0" class="${btn} bg-surface-700 text-white hover:bg-surface-600">Use</button>`}
        </div>`;
      })).join("")}</div>`;
  }

  function ollamaSection() {
    const h = hub;
    if (!h) return "";
    let top;
    if (!h.installed_app) {
      const i = h.install || {};
      const working = ["downloading", "installing", "starting"].includes(i.status);
      top = `<div class="p-4 rounded-2xl border border-brand-500/40 bg-brand-500/[0.06]">
        <p class="text-white text-[13px] font-medium">Install Ollama to run models on this PC</p>
        <p class="text-[12px] text-zinc-400 mt-1">Free and open source (ollama.com). Xod downloads the official installer (about 1.5 GB, it includes the GPU libraries), checks it's signed by Ollama and installs it for you. No admin needed.</p>
        ${working ? `<p class="text-[12px] text-brand-300 mt-3 flex items-center gap-2"><i data-lucide="loader-2" class="size-3.5 animate-spin"></i>${
            i.status === "downloading" ? `Downloading Ollama… ${i.percent}%` : i.status === "installing" ? "Installing…" : "Starting Ollama…"}</p>`
          : `<div class="mt-3 flex items-center gap-3">${h.can_install
              ? `<button data-setup="ol-install" class="${btn} bg-brand-500 text-surface-900 font-semibold hover:bg-brand-400">Install Ollama</button>`
              : `<a href="https://ollama.com/download" target="_blank" class="${btn} bg-brand-500 text-surface-900 font-semibold">Get Ollama</a>`}
              ${i.status === "error" ? `<span class="text-[12px] text-red-300">${esc(i.error)}</span>` : ""}</div>`}
      </div>`;
    } else if (!h.running) {
      top = `<div class="p-4 rounded-2xl border border-amber-500/30 bg-amber-500/[0.05] flex items-center gap-3">
        <p class="text-[13px] text-zinc-200 flex-1">Ollama is installed but not running.</p>
        <button data-setup="ol-start" class="${btn} bg-brand-500 text-surface-900 font-semibold hover:bg-brand-400">Start Ollama</button></div>`;
    }
    const usable = h.models.filter((m) => !m.embedding);
    return `
      <h3 class="text-white text-[15px] font-semibold mt-6">Local models <span class="text-zinc-500 font-normal text-[12px]">with Ollama${h.version ? ` ${esc(h.version)}` : ""}</span></h3>
      ${top ? `<div class="mt-3">${top}</div>` : ""}
      ${h.running && usable.length ? `<p class="text-[12px] text-zinc-400 mt-3">On this computer</p><div class="space-y-2 mt-2">${usable.map(installedRow).join("")}</div>` : ""}
      ${h.running ? `
        <p class="text-[12px] text-zinc-400 mt-4">Get a model <span class="text-zinc-500">· picked for ${bestGpu() ? `your ${bestGpu().memory_gb} GB GPU` : h.machine.runs_on === "unified" ? `your ${h.machine.ram_gb} GB Mac` : "this computer's processor"}</span></p>
        <div class="space-y-2 mt-2">${h.catalog.filter((m) => !m.installed).map(catalogCard).join("")}</div>
        <div class="mt-4 p-3.5 rounded-2xl border border-white/[0.06] bg-surface-900/40">
          <p class="text-[12px] text-zinc-300">Add any model</p>
          <p class="text-[11px] text-zinc-500 mt-0.5">A name from ollama.com (like <span class="text-zinc-400">llama3.1:8b</span>) or a Hugging Face link to a GGUF model.</p>
          <form data-setup-form="ol-find" class="mt-2 flex gap-2">
            <input id="olQuery" autocomplete="off" spellcheck="false" placeholder="huggingface.co/… or model:tag" class="flex-1 min-w-0 px-3 py-1.5 rounded-lg bg-surface-800 border border-white/[0.08] text-[12px] text-white placeholder-zinc-600 outline-none focus:border-brand-400">
            <button class="${btn} bg-surface-700 text-white hover:bg-surface-600">Find</button>
          </form>
          <div id="olFound">${foundHtml()}</div>
        </div>` : ""}`;
  }

  let found = null;      // result of the "Add any model" lookup
  function foundHtml() {
    if (!found) return "";
    if (found.loading) return `<p class="text-[11px] text-zinc-400 mt-2">Looking it up…</p>`;
    if (!found.success) return `<p class="text-[11px] text-red-300 mt-2">${esc(found.error)}</p>`;
    const p = (hub.pulls.find((x) => x.name === found.name)) || {};
    const fits = !found.size_gb || found.size_gb + 2.5 <= hub.budget_gb;
    const full = /:[^/]*$/.test(found.name) ? found.name : `${found.name}:latest`;   // how Ollama lists it
    const have = hub.models.find((m) => m.name === found.name || m.name === full);
    return `<div class="mt-2.5 flex items-center gap-3">
        <div class="min-w-0 flex-1">
          <p class="text-[12px] text-white truncate">${esc(found.name)}</p>
          <p class="text-[11px] ${fits ? "text-zinc-400" : "text-amber-300/90"}">${found.source === "huggingface" ? "Hugging Face" : "ollama.com"}${found.quant ? ` · ${esc(found.quant)}` : ""}${found.size_gb ? ` · ${found.size_gb} GB · ${fitText(found.size_gb, fits)}` : ""}</p>
        </div>
        ${p.status === "downloading" ? "" : have
          ? (have.in_use ? badge("bg-brand-500 text-surface-900 font-semibold", "In use")
             : `<button data-setup="pick" data-provider="ollama" data-model="${esc(have.name)}" data-label="${esc(have.name)}" data-download="0" class="${btn} bg-surface-700 text-white hover:bg-surface-600">Use</button>`)
          : `<button data-setup="ol-pull" data-name="${esc(found.name)}" class="${btn} bg-brand-500 text-surface-900 font-semibold hover:bg-brand-400">Download</button>`}
      </div>${p.status === "downloading" ? pullBar(found.name, p) : ""}`;
  }

  let hub = null;
  let hubTimer = null;
  async function loadHub() {
    try { hub = await (await fetch("/api/ollama")).json(); } catch (e) { hub = null; }
  }

  // While Ollama installs or models download: move the bars in place; redraw when something finishes.
  function watchHub() {
    clearInterval(hubTimer);
    hubTimer = setInterval(async () => {
      if (!$("setupWizard") || !hub) return clearInterval(hubTimer);
      const installing = ["downloading", "installing", "starting"].includes((hub.install || {}).status);
      const active = hub.pulls.filter((p) => p.status === "downloading");
      if (!installing && !active.length) return clearInterval(hubTimer);
      if (installing) {
        const prev = hub.install.status;
        await loadHub();
        if (hub.install.status !== prev || hub.install.status === "downloading") redraw();
        return;
      }
      let finished = false;
      for (const p of active) {
        const s = await (await fetch(`/api/ollama/pull?name=${encodeURIComponent(p.name)}`)).json();
        Object.assign(p, s);
        if (s.status !== "downloading") { finished = true; continue; }
        document.querySelectorAll(`[data-pull="${CSS.escape(p.name)}"]`).forEach((el) => {
          el.querySelector("[data-pull-bar]").style.width = `${s.percent}%`;
          el.querySelector("[data-pull-text]").textContent = pullText(s);
        });
      }
      if (finished) {
        const done = active.filter((p) => p.status === "done").map((p) => p.name);
        await loadHub();
        redraw();
        if (done.length) showToast(`${done.join(", ")} downloaded. Click Use to switch to it.`);
      }
    }, 1500);
  }

  function redraw() {
    const scroller = document.querySelector("#setupWizard > div");
    const top = scroller ? scroller.scrollTop : 0;
    const q = $("olQuery") ? $("olQuery").value : "";
    stepChoose();
    const again = document.querySelector("#setupWizard > div");
    if (again) again.scrollTop = top;
    if ($("olQuery")) $("olQuery").value = q;
  }

  async function olPost(path, body) {
    return (await fetch(`/api/ollama/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })).json();
  }

  function stepDownload() {
    shell(`
      <h2 class="text-white text-lg font-semibold mt-2">Downloading ${esc(choice.label)}</h2>
      <p class="text-[13px] text-zinc-400 mt-1">One time only. You can keep using your Mac; Xod saves it in the Hugging Face cache.</p>
      <div class="mt-5 h-3 rounded-full bg-surface-700 overflow-hidden"><div id="setupBar" class="h-full bg-brand-500 transition-all" style="width:0%"></div></div>
      <p id="setupBarText" class="text-[12px] text-zinc-400 mt-2">Starting…</p>
      <div class="mt-6 flex justify-between">
        <button data-setup="back" class="px-3 py-2 rounded-lg text-[13px] text-zinc-400 hover:text-white">Back</button>
      </div>`);
    const tick = async () => {
      const d = await (await fetch(`/api/setup/download?repo=${encodeURIComponent(choice.model)}`)).json();
      const bar = $("setupBar"), txt = $("setupBarText");
      if (!bar) return clearInterval(pollTimer);
      bar.style.width = `${d.percent}%`;
      if (d.status === "error") { clearInterval(pollTimer); txt.innerHTML = `<span class="text-red-300">Download failed: ${esc(d.error)}</span>`; return; }
      txt.textContent = `${d.downloaded_gb} of ${d.total_gb} GB (${d.percent}%)`;
      if (d.status === "done") { clearInterval(pollTimer); await useChoice(); }
    };
    clearInterval(pollTimer);
    pollTimer = setInterval(tick, 1500);
    tick();
  }

  async function useChoice() {
    const r = await (await fetch("/api/setup/choose", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: choice.provider, model: choice.model, base_url: choice.base || "" }) })).json();
    if (!r.success) { showToast(r.error || "Couldn't use that model"); return stepChoose(); }
    stepTest();
  }

  async function stepTest() {
    shell(`
      <h2 class="text-white text-lg font-semibold mt-2">Saying hi to ${esc(choice.label)}</h2>
      <p class="text-[13px] text-zinc-400 mt-1">${choice.provider === "xod" ? "Starting the built-in model (the first start takes up to a minute)…"
        : choice.provider === "ollama" ? "Loading it into memory and checking it answers (the first load can take a minute)…" : "Checking it answers…"}</p>
      <div id="setupTest" class="mt-5 p-4 rounded-2xl bg-surface-900/60 border border-white/[0.06] text-[13px] text-zinc-300 flex items-center gap-2">
        <i data-lucide="loader-2" class="size-4 animate-spin text-brand-400"></i> Waiting for an answer…
      </div>`);
    const r = await (await fetch("/api/setup/test", { method: "POST" })).json();
    const box = $("setupTest");
    if (!box) return;
    if (!r.success) {
      box.innerHTML = `<span class="text-red-300">${esc(r.error)}</span>`;
      box.insertAdjacentHTML("afterend", `<div class="mt-4 flex gap-2"><button data-setup="back" class="px-3 py-2 rounded-lg text-[13px] bg-surface-700 text-white">Pick another</button><button data-setup="retest" class="px-3 py-2 rounded-lg text-[13px] text-zinc-300 hover:text-white">Try again</button></div>`);
      return;
    }
    await fetch("/api/setup/complete", { method: "POST" });
    stepDone(r);
  }

  function stepDone(r) {
    shell(`
      <h2 class="text-white text-lg font-semibold mt-2">You're all set</h2>
      <div class="mt-3 p-4 rounded-2xl bg-emerald-500/[0.07] border border-emerald-500/20 text-[13px] text-zinc-200">
        <span class="text-emerald-300 font-medium">${esc(choice.label)}:</span> “${esc(r.reply)}” <span class="text-zinc-500">(${r.seconds}s)</span>
      </div>
      <h3 class="text-white text-[15px] font-semibold mt-6">Try one of these</h3>
      <div class="grid sm:grid-cols-2 gap-2.5 mt-3">
        ${STARTERS.map((t, i) => `<button data-setup="starter" data-i="${i}" class="text-left p-3.5 rounded-2xl border border-white/[0.08] bg-surface-900/60 hover:border-brand-400 flex gap-3">
          <i data-lucide="${t.icon}" class="size-4 text-brand-300 shrink-0 mt-0.5"></i><span class="text-[13px] text-zinc-200">${esc(t.text)}</span></button>`).join("")}
      </div>
      <p class="text-[12px] text-zinc-500 mt-5">Tip: hand Xod a longer job in <b class="text-zinc-300">Tasks</b>, and it asks before anything is sent, paid or deleted.</p>
      <div class="mt-5 flex justify-end"><button data-setup="close" class="px-4 py-2 rounded-xl bg-brand-500 text-surface-900 text-[13px] font-semibold hover:bg-brand-400">Start using Xod</button></div>`);
  }

  async function open() {
    shell(`<div class="py-16 text-center text-zinc-400 text-[13px]"><i data-lucide="loader-2" class="size-5 animate-spin mx-auto mb-2 text-brand-400"></i>Checking your computer…</div>`);
    await reload();
    stepChoose();
    watchHub();
  }

  let auto = null;      // what autoconnect found: other servers, this computer
  async function reload() {
    // A fresh look around first (it may start Ollama or connect a model), then what there is.
    auto = await fetch("/api/autoconnect/check", { method: "POST" }).then((r) => r.json()).catch(() => null);
    const [s] = await Promise.all([fetch("/api/setup").then((r) => r.json()), loadHub()]);
    state = s;
  }

  function close() {
    clearInterval(pollTimer);
    clearInterval(hubTimer);
    const root = $("setupWizard");
    if (root) root.remove();
    if (typeof loadConfig === "function") loadConfig();
  }

  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-setup]");
    if (!b || !b.closest("#setupWizard")) return;
    const act = b.dataset.setup;
    if (act === "close") return close();
    if (act === "settings") { close(); return openSettingsModal(); }
    if (act === "back") { clearInterval(pollTimer); await reload(); stepChoose(); return watchHub(); }
    if (act === "ol-install" || act === "ol-start") {
      b.disabled = true;
      b.textContent = act === "ol-install" ? "Starting…" : "Starting Ollama…";
      const r = await olPost(act === "ol-install" ? "install" : "start");
      if (!r.success) showToast(r.error || "Couldn't start Ollama");
      await reload(); redraw(); return watchHub();
    }
    if (act === "ol-pull") {
      b.disabled = true;
      const r = await olPost("pull", { name: b.dataset.name });
      if (!r.success) { b.disabled = false; return showToast(r.error || "Couldn't start the download"); }
      await loadHub(); redraw(); return watchHub();
    }
    if (act === "ol-cancel") { await olPost("pull/cancel", { name: b.dataset.name }); await loadHub(); return redraw(); }
    if (act === "ol-delete") {
      if (!confirm(`Delete ${b.dataset.name} from this computer? You can download it again later.`)) return;
      const r = await olPost("delete", { name: b.dataset.name });
      if (!r.success) return showToast(r.error || "Couldn't delete it");
      await reload(); return redraw();
    }
    if (act === "retest") return stepTest();
    if (act === "starter") {
      const t = STARTERS[+b.dataset.i];
      close();
      const input = $("chatInput") || document.querySelector("#chatForm textarea");
      if (input) { input.value = t.text; input.focus(); input.dispatchEvent(new Event("input")); }
      return;
    }
    if (act === "pick") {
      choice = { provider: b.dataset.provider, model: b.dataset.model, label: b.dataset.label, base: b.dataset.base || "",
                 needsDownload: b.dataset.download === "1" };
      if (choice.needsDownload) {
        const r = await (await fetch("/api/setup/download", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repo: choice.model }) })).json();
        if (!r.success) return showToast(r.error || "Couldn't start the download");
        return stepDownload();
      }
      return useChoice();
    }
  });

  document.addEventListener("submit", async (e) => {
    const f = e.target.closest("[data-setup-form='ol-find']");
    if (!f) return;
    e.preventDefault();
    const query = $("olQuery").value.trim();
    if (!query) return;
    found = { loading: true };
    $("olFound").innerHTML = foundHtml();
    found = await olPost("resolve", { query });
    $("olFound").innerHTML = foundHtml();
    if (window.lucide) lucide.createIcons();
  });

  window.openSetupWizard = open;
  document.addEventListener("DOMContentLoaded", async () => {
    try {
      const s = await (await fetch("/api/setup")).json();
      if (s.show_on_start) { await loadHub(); state = s; stepChoose(); watchHub(); }
    } catch (e) {}
  });
})();
