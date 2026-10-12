// The preview pane: apps and games Xod builds run here, beside the chat, like an IDE's preview.
// Tabs: Preview (the app, sandboxed), Files (what's really on disk), Console (its logs and errors).
// Backend: services/previews.py (sandboxed serving + headless check), agent/builder.py (the builder).
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = { get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } },
                  set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} } };
  const SANDBOX = "allow-scripts allow-pointer-lock allow-modals allow-forms allow-popups allow-downloads";
  const S = { app: null, tab: "preview", logs: [], errors: 0, open: false, building: false, phone: false, file: null, reloadTimer: 0 };

  // ------------------------------------------------------------------ the pane
  function pane() {
    let el = document.getElementById("previewPane");
    if (el) return el;
    const host = document.querySelector("main")?.parentElement;
    if (!host) return null;
    el = document.createElement("aside");
    el.id = "previewPane";
    el.className = "preview-pane hidden";
    el.style.setProperty("--pv-w", `${store.get("xod_preview_w", 46)}%`);
    el.innerHTML = `
      <div class="pv-grip" title="Drag to resize"></div>
      <div class="pv-head">
        <div class="pv-title"><i data-lucide="app-window" class="size-4 text-brand-400"></i><span data-pv="title">Preview</span>
          <span class="pv-state" data-pv="state"></span></div>
        <div class="pv-actions">
          <button data-act="reload" title="Reload"><i data-lucide="rotate-cw" class="size-3.5"></i></button>
          <button data-act="phone" title="Phone size"><i data-lucide="smartphone" class="size-3.5"></i></button>
          <button data-act="check" title="Run Xod's check (plays it headlessly and lists errors)"><i data-lucide="bug-play" class="size-3.5"></i></button>
          <button data-act="external" title="Open in your browser"><i data-lucide="external-link" class="size-3.5"></i></button>
          <button data-act="close" title="Collapse"><i data-lucide="panel-right-close" class="size-3.5"></i></button>
        </div>
      </div>
      <div class="pv-tabs">
        <button data-tab="preview" class="on"><i data-lucide="play" class="size-3"></i>Preview</button>
        <button data-tab="files"><i data-lucide="folder-tree" class="size-3"></i>Files</button>
        <button data-tab="console"><i data-lucide="terminal" class="size-3"></i>Console <span class="pv-badge hidden" data-pv="badge"></span></button>
      </div>
      <div class="pv-body">
        <div data-panel="preview" class="pv-stage"><div class="pv-frame-wrap" data-pv="wrap"></div>
          <div class="pv-building hidden" data-pv="building"><div class="pv-spinner"></div><p data-pv="buildmsg">Building…</p></div></div>
        <div data-panel="files" class="pv-files hidden"><div class="pv-filelist" data-pv="files"></div><pre class="pv-code" data-pv="code"></pre></div>
        <div data-panel="console" class="pv-console hidden"><div class="pv-console-bar">
            <button data-act="clear">Clear</button><button data-act="fix" class="pv-fix hidden"><i data-lucide="wand-sparkles" class="size-3"></i>Ask Xod to fix these</button></div>
          <div class="pv-logs" data-pv="logs"></div></div>
      </div>`;
    host.appendChild(el);
    el.addEventListener("click", onClick);
    grip(el.querySelector(".pv-grip"), el);
    if (window.lucide) lucide.createIcons();
    return el;
  }
  const $ = (k) => pane()?.querySelector(`[data-pv="${k}"]`);

  function grip(handle, el) {
    handle.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      const host = el.parentElement.getBoundingClientRect();
      el.classList.add("pv-dragging");
      const move = (ev) => {
        const pct = Math.min(75, Math.max(25, ((host.right - ev.clientX) / host.width) * 100));
        el.style.setProperty("--pv-w", `${pct}%`);
      };
      const up = () => {
        el.classList.remove("pv-dragging");
        store.set("xod_preview_w", parseFloat(el.style.getPropertyValue("--pv-w")));
        handle.removeEventListener("pointermove", move);
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up, { once: true });
    });
  }

  function onClick(e) {
    const t = e.target.closest("[data-tab]");
    if (t) return tab(t.dataset.tab);
    const a = e.target.closest("[data-act]")?.dataset.act;
    const f = e.target.closest("[data-file]");
    if (f) return showFile(f.dataset.file);
    if (a === "reload") reload();
    else if (a === "close") close();
    else if (a === "external" && S.app) window.open(S.app.url, "_blank");
    else if (a === "phone") { S.phone = !S.phone; pane().classList.toggle("pv-phone", S.phone); }
    else if (a === "check") check();
    else if (a === "clear") { S.logs = []; S.errors = 0; renderLogs(); }
    else if (a === "fix") askToFix();
  }

  function tab(name) {
    S.tab = name;
    pane().querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
    pane().querySelectorAll("[data-panel]").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== name));
    if (name === "files") loadFiles();
  }

  // ------------------------------------------------------------------ open / close
  function open(app, { focusTab } = {}) {
    if (!app?.url) return;
    const el = pane();
    if (!el) return;
    const changed = !S.app || S.app.url !== app.url;
    S.app = { ...S.app, ...app };
    S.open = true;
    el.classList.remove("hidden");
    document.body.classList.add("xod-previewing");
    $("title").textContent = app.title || "Preview";
    if (changed) { S.logs = []; S.errors = 0; renderLogs(); load(); }
    tab(focusTab || S.tab || "preview");
    edgeButton();
    store.set("xod_preview_last", { url: S.app.url, root: S.app.root, title: S.app.title, path: S.app.path });
  }

  function close() {
    pane()?.classList.add("hidden");
    document.body.classList.remove("xod-previewing");
    S.open = false;
    const wrap = $("wrap");
    if (wrap) wrap.innerHTML = "";   // stop the game (and its sound) when collapsed
    edgeButton();
  }

  function load() {
    const wrap = $("wrap");
    if (!wrap || !S.app) return;
    wrap.innerHTML = "";
    const f = document.createElement("iframe");
    f.setAttribute("sandbox", SANDBOX);
    f.setAttribute("allow", "fullscreen; gamepad; autoplay");
    f.setAttribute("title", S.app.title || "Preview");
    f.src = `${S.app.url}${S.app.url.includes("?") ? "&" : "?"}t=${Date.now()}`;
    wrap.appendChild(f);
    f.addEventListener("load", () => setTimeout(() => { try { f.focus(); } catch (e) {} }, 50));
    $("state").textContent = "";
  }
  function reload() { S.logs = []; S.errors = 0; renderLogs(); load(); }

  // The app's console arrives by postMessage from the bridge previews.py injects.
  window.addEventListener("message", (e) => {
    const f = $("wrap")?.querySelector("iframe");
    if (!f || e.source !== f.contentWindow || !e.data || !e.data.__xodPreview) return;
    if (e.data.storage && S.app?.root) {
      fetch("/api/preview/storage", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ root: S.app.root, data: e.data.storage }) }).catch(() => {});
      return;
    }
    const entry = e.data.entry || {};
    if (entry.level === "ready") return;
    S.logs.push(entry);
    if (S.logs.length > 400) S.logs.shift();
    if (entry.level === "error") S.errors++;
    renderLogs();
  });

  function renderLogs() {
    const box = $("logs");
    if (!box) return;
    box.innerHTML = S.logs.length ? S.logs.map((l) => `<div class="pv-log pv-${esc(l.level)}"><span>${l.level === "error" ? "✕" : l.level === "warn" ? "!" : "›"}</span><code>${esc(l.text)}</code></div>`).join("")
      : `<p class="pv-empty">Nothing logged yet. Errors and console.log output from the app show up here.</p>`;
    box.scrollTop = box.scrollHeight;
    const badge = $("badge");
    badge.textContent = S.errors;
    badge.classList.toggle("hidden", !S.errors);
    pane().querySelector(".pv-fix").classList.toggle("hidden", !S.errors);
  }

  async function check() {
    if (!S.app) return;
    $("state").textContent = "Checking…";
    try {
      const r = await (await fetch("/api/preview/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: S.app.url }) })).json();
      const probs = [...(r.errors || []), ...(r.failed || []).map((x) => `Couldn't load ${x}`), ...(r.blank ? ["The page is blank."] : [])];
      if (!r.ran) S.logs.push({ level: "warn", text: r.note || "Couldn't run the check." });
      else if (!probs.length) S.logs.push({ level: "log", text: "Xod's check: played it for a few seconds, no errors." });
      probs.forEach((p) => { S.logs.push({ level: "error", text: p }); S.errors++; });
      renderLogs();
      tab("console");
    } finally { $("state").textContent = ""; }
  }

  function askToFix() {
    const errs = S.logs.filter((l) => l.level === "error").slice(-8).map((l) => `- ${l.text.slice(0, 300)}`).join("\n");
    if (typeof quickSendPrompt === "function") quickSendPrompt(`Fix the errors in the preview of ${S.app?.title || "the app"}:\n${errs}`);
  }

  // ------------------------------------------------------------------ files
  async function loadFiles() {
    const list = $("files");
    if (!list || !S.app?.root) return;
    try {
      const r = await (await fetch(`/api/preview/files?root=${encodeURIComponent(S.app.root)}`)).json();
      const files = r.files || [];
      list.innerHTML = `<p class="pv-where" title="${esc(r.path)}">${esc((r.path || "").replace(/^\/Users\/[^/]+/, "~"))}</p>` +
        (files.map((f) => `<button data-file="${esc(f.path)}" class="${S.file === f.path ? "on" : ""}" ${f.text ? "" : "disabled"}>
          <i data-lucide="${fileIcon(f.path)}" class="size-3.5"></i><span>${esc(f.path)}</span><em>${f.bytes < 1024 ? f.bytes + " B" : Math.round(f.bytes / 1024) + " KB"}</em></button>`).join("")
          || `<p class="pv-empty">No files yet.</p>`);
      if (window.lucide) lucide.createIcons();
      if (!S.file && files.length) showFile((files.find((f) => /\.(js|mjs)$/.test(f.path)) || files[0]).path);
      else if (S.file) showFile(S.file);
    } catch (e) { list.innerHTML = `<p class="pv-empty">Couldn't list the files.</p>`; }
  }
  const fileIcon = (p) => /\.html?$/.test(p) ? "file-code-2" : /\.css$/.test(p) ? "palette" : /\.m?js$/.test(p) ? "braces" : /\.json$/.test(p) ? "file-json" : "file";

  async function showFile(path) {
    S.file = path;
    pane().querySelectorAll("[data-file]").forEach((b) => b.classList.toggle("on", b.dataset.file === path));
    try {
      const r = await (await fetch(`/api/preview/file?root=${encodeURIComponent(S.app.root)}&path=${encodeURIComponent(path)}`)).json();
      const lines = String(r.text || "").split("\n");
      $("code").innerHTML = lines.map((l, i) => `<span class="ln">${i + 1}</span>${esc(l)}`).join("\n");
    } catch (e) { $("code").textContent = "Couldn't open it."; }
  }

  // ------------------------------------------------------------------ the builder's events
  function onBuildStarted(ev) {
    S.building = true;
    open({ url: ev.url, root: ev.root, title: ev.title, path: ev.path });
    const b = $("building");
    b.classList.remove("hidden");
    $("buildmsg").textContent = `Building ${ev.title || "it"}…`;
    window.dispatchEvent(new CustomEvent("xod:build", { detail: { phase: "start", title: ev.title } }));
  }

  function onStatus(text) {
    if (S.building && $("buildmsg")) $("buildmsg").textContent = text;
  }

  function onToolResult(ev) {
    if (!S.app || ev.name !== "computer_write_file" || !ev.data?.path || !S.app.path || !ev.data.path.startsWith(S.app.path)) return;
    if (S.tab === "files") loadFiles();
    clearTimeout(S.reloadTimer);
    if (!S.building) S.reloadTimer = setTimeout(reload, 400);
  }

  function onBuildDone(ev, bubble) {
    S.building = false;
    $("building")?.classList.add("hidden");
    open({ url: ev.url, root: ev.root, title: ev.title, path: ev.path });
    reload();
    if (S.tab === "files") loadFiles();
    if (bubble?.wrapper) bubble.wrapper.lastElementChild?.appendChild(card(ev));
    window.dispatchEvent(new CustomEvent("xod:build", { detail: { phase: "done", title: ev.title, ok: ev.check?.ok } }));
    if (ev.project && typeof loadProjects === "function") loadProjects();
    else if (ev.project && typeof renderProjectsSidebar === "function") renderProjectsSidebar();
  }

  function card(ev) {
    const n = (ev.check?.errors?.length || 0) + (ev.check?.failed?.length || 0) + (ev.check?.blank ? 1 : 0);
    const el = document.createElement("div");
    el.className = "pv-card";
    el.innerHTML = `<div class="pv-card-icon"><i data-lucide="app-window" class="size-5"></i></div>
      <div class="min-w-0"><p class="pv-card-title">${esc(ev.title || "App")}</p>
        <p class="pv-card-sub">${(ev.files || []).length} file${(ev.files || []).length === 1 ? "" : "s"} · ${ev.check?.ran ? (n ? `${n} error${n === 1 ? "" : "s"} left` : "ran with no errors") : "not checked"}</p></div>
      <button class="pv-card-open"><i data-lucide="play" class="size-3.5"></i>Open preview</button>`;
    el.querySelector("button").addEventListener("click", () => open({ url: ev.url, root: ev.root, title: ev.title, path: ev.path }));
    setTimeout(() => window.lucide && lucide.createIcons(), 0);
    return el;
  }

  // A chat loaded from history: give its last build a card and remember it for the edge button.
  function fromHistory(m, wrapper) {
    const ev = (m.tool_events || []).filter((t) => t.type === "build_done").pop();
    if (!ev) return;
    // The token in old URLs is from a previous run of the server: ask for a fresh one.
    fetch(`/api/preview/apps?folder=${encodeURIComponent(ev.path)}`).then((r) => r.json()).then(({ apps }) => {
      const app = (apps || [])[0];
      if (!app) return;
      const fresh = { ...ev, url: app.url, root: app.root };
      wrapper?.lastElementChild?.appendChild(card(fresh));
      S.recent = fresh;
      edgeButton();
    }).catch(() => {});
  }

  // A small tab on the right edge to bring the pane back when it's collapsed.
  function edgeButton() {
    let b = document.getElementById("previewEdgeBtn");
    const app = S.app || S.recent;
    if (!b) {
      b = document.createElement("button");
      b.id = "previewEdgeBtn";
      b.className = "pv-edge hidden";
      b.innerHTML = `<i data-lucide="panel-right-open" class="size-3.5"></i><span>Preview</span>`;
      b.addEventListener("click", () => open(S.app || S.recent));
      document.querySelector("main")?.appendChild(b);
      if (window.lucide) lucide.createIcons();
    }
    b.classList.toggle("hidden", S.open || !app);
  }

  // The scope bar's Preview button shows when the folder in focus has apps in it.
  async function refreshForScope(path) {
    const btn = document.getElementById("scopePreviewBtn");
    if (!btn) return;
    if (!path) { btn.classList.add("hidden"); return; }
    try {
      const { apps } = await (await fetch(`/api/preview/apps?folder=${encodeURIComponent(path)}`)).json();
      btn.classList.toggle("hidden", !apps?.length);
      btn.onclick = () => apps?.length && open({ url: apps[0].url, root: apps[0].root, title: apps[0].title, path: apps[0].path });
    } catch (e) { btn.classList.add("hidden"); }
  }

  // New chat: forget the last app's edge button.
  function reset() { S.recent = null; if (!S.open) S.app = null; edgeButton(); }

  window.XodPreview = { open, close, reload, check, onBuildStarted, onBuildDone, onToolResult, onStatus, fromHistory, refreshForScope, reset,
                        state: () => ({ open: S.open, app: S.app, tab: S.tab, errors: S.errors, logs: S.logs.slice(-50), building: S.building }) };
})();
