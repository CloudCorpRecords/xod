// Xod's version and updates: a pill at the bottom of the sidebar that always shows where things stand
// ("Xod 0.3.0 · up to date", "Xod 0.4.0 is out [Update]", a download bar, "Restarting…"), plus a notice on
// the home screen when a new version is out. In the desktop app, Update downloads, checks and installs it
// (electron/updater.js); in a browser it links to the release page (services/updates.py).
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const desk = window.xodDesktop && window.xodDesktop.updates;
  let st = null;

  const ago = (t) => {
    if (!t) return "";
    const m = Math.round((Date.now() - t) / 60000);
    return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
  };

  // The browser fallback: the server's own check (once a day, cached).
  async function serverStatus(force) {
    try {
      const u = await (await fetch("/api/update" + (force ? "?force=1" : ""))).json();
      return { state: u.available ? "manual" : u.latest ? "uptodate" : "idle", current: u.current, latest: u.latest, page: u.url,
               enabled: u.enabled, checkedAt: u.latest ? Date.now() : 0, browser: true };
    } catch (e) {
      return null;
    }
  }

  function pill() {
    const host = document.getElementById("xodVersion");
    if (!host || !st) return;
    const s = st.state;
    const cur = esc(st.current || "");
    const latest = esc(st.latest || "");
    let body = "", cls = "xv";
    if (s === "checking") {
      body = `<i data-lucide="loader-2" class="size-3.5 animate-spin"></i><span class="xv-text">Xod ${cur} · checking for updates…</span>`;
    } else if (s === "available" || s === "ready" || s === "manual") {
      cls += " is-new";
      const label = s === "manual" ? "Download" : s === "ready" ? "Restart to update" : "Update";
      body = `<i data-lucide="sparkles" class="size-3.5"></i><span class="xv-text">Xod ${latest} is out</span>
        <button class="xv-btn" data-xv="install">${label}</button>`;
    } else if (s === "downloading") {
      cls += " is-new";
      const pct = Math.round((st.progress || 0) * 100);
      body = `<i data-lucide="download" class="size-3.5"></i><span class="xv-text">Getting Xod ${latest}… ${pct}%</span>
        <span class="xv-bar"><span style="width:${pct}%"></span></span>`;
    } else if (s === "installing") {
      cls += " is-new";
      body = `<i data-lucide="loader-2" class="size-3.5 animate-spin"></i><span class="xv-text">Restarting into Xod ${latest}…</span>`;
    } else if (s === "error") {
      cls += " is-err";
      body = `<i data-lucide="alert-circle" class="size-3.5"></i><span class="xv-text" title="${esc(st.error)}">Xod ${cur} · couldn't check</span>
        <button class="xv-btn" data-xv="check">Try again</button>`;
    } else if (s === "uptodate") {
      body = `<i data-lucide="check-circle-2" class="size-3.5 text-emerald-400"></i><span class="xv-text" title="Checked ${ago(st.checkedAt)}">Xod ${cur} · up to date</span>
        <button class="xv-icon" data-xv="check" title="Check now (last checked ${ago(st.checkedAt)})"><i data-lucide="refresh-cw" class="size-3"></i></button>`;
    } else {
      body = `<span class="xv-text">Xod ${cur}</span><button class="xv-icon" data-xv="check" title="Check for updates"><i data-lucide="refresh-cw" class="size-3"></i></button>`;
    }
    host.className = cls;
    host.innerHTML = body;
    if (window.lucide) lucide.createIcons();
  }

  function banner() {
    const show = st && ["available", "ready", "downloading", "manual"].includes(st.state);
    let el = document.getElementById("updateBanner");
    let dismissed = false;
    try { dismissed = localStorage.getItem("xod.updateDismissed") === st?.latest; } catch (e) {}
    if (!show || dismissed) { if (el) el.remove(); return; }
    const anchor = document.getElementById("missingKeyBanner");
    if (!anchor) return;
    if (!el) {
      el = document.createElement("div");
      el.id = "updateBanner";
      el.className = "max-w-md mx-auto px-4 py-3 rounded-xl bg-brand-500/10 border border-brand-500/30 text-left flex items-center gap-3";
      anchor.parentNode.insertBefore(el, anchor);
    }
    const label = st.state === "manual" ? "Download" : st.state === "ready" ? "Restart to update" : "Update";
    const busy = st.state === "downloading" ? ` <span class="text-zinc-400">(getting it… ${Math.round((st.progress || 0) * 100)}%)</span>` : "";
    el.innerHTML = `
      <i data-lucide="sparkles" class="size-4 text-brand-300 shrink-0"></i>
      <p class="text-[13px] text-zinc-200 flex-1">Xod ${esc(st.latest)} is out.${busy} <a href="${esc(st.page)}" target="_blank" rel="noopener" class="text-brand-300 hover:underline">What's new</a></p>
      <button data-xv="install" class="px-3 py-1.5 rounded-lg bg-amber-500 text-black text-[12px] font-semibold hover:bg-amber-400 shrink-0">${label}</button>
      <button data-xv="later" class="text-[12px] text-zinc-400 hover:text-white" title="Hide this until the next version">Later</button>`;
    if (window.lucide) lucide.createIcons();
  }

  function render(next) {
    if (!next) return;
    st = next;
    pill();
    banner();
  }

  async function act(what) {
    if (what === "later") {
      try { localStorage.setItem("xod.updateDismissed", st.latest); } catch (e) {}
      return banner();
    }
    if (desk) {
      if (what === "check") render({ ...st, state: "checking" });
      render(await (what === "install" ? desk.install() : desk.check()));
      return;
    }
    if (what === "install" && st.page) return window.open(st.page, "_blank", "noopener");
    render({ ...st, state: "checking" });
    render(await serverStatus(true));
  }

  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-xv]");
    if (b && (b.closest("#xodVersion") || b.closest("#updateBanner"))) act(b.dataset.xv);
  });

  async function boot() {
    if (desk) {
      desk.onState(render);
      render(await desk.status());
    } else {
      render(await serverStatus(false));
    }
  }
  window.XodUpdates = { state: () => st, check: () => act("check"), install: () => act("install"), _render: render };
  setTimeout(boot, 1200);
  setInterval(pill, 60000);   // keeps "checked 5 min ago" fresh
})();
