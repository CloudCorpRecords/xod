// Xod's version and updates. Phase 5 (simplicity redesign): updates are
// AUTOMATIC by default - Xod checks and installs on its own (in the desktop
// app, electron/updater.js downloads in the background and installs when Xod
// quits) and reports the outcome as a result card in Work. The user is never
// prompted: no banner, no Update button on the surface. Settings - Updates
// reads in plain words ("Automatic - last updated Tuesday."); the policy
// switch and a manual "Check now" live one layer down, in that section's
// Update settings details. With automatic updates OFF, the old prompt
// behaviour returns (the pill offers the update; the browser build links to
// the release page, services/updates.py).
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const desk = window.xodDesktop && window.xodDesktop.updates;
  let st = null;
  let pol = null;   // { policy, automatic, last_updated } from /api/update

  const ago = (t) => {
    if (!t) return "";
    const m = Math.round((Date.now() - t) / 60000);
    return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
  };
  const weekday = (t) => new Date(t * 1000).toLocaleDateString(undefined, { weekday: "long" });

  const automatic = () => !pol || pol.automatic !== false;   // default: automatic

  // The server's policy + last-updated record, and (browser build) its check.
  async function serverStatus(force) {
    try {
      const u = await (await fetch("/api/update" + (force ? "?force=1" : ""))).json();
      pol = { policy: u.policy, automatic: u.automatic, last_updated: u.last_updated };
      return { state: u.available ? "manual" : u.latest ? "uptodate" : "idle", current: u.current, latest: u.latest, page: u.url,
               enabled: u.enabled, checkedAt: u.latest ? Date.now() : 0, browser: true };
    } catch (e) {
      return null;
    }
  }

  // The plain-words Settings line: "Automatic - last updated Tuesday."
  function policyLine() {
    const el = document.getElementById("updatesPolicyLine");
    const toggle = document.getElementById("autoUpdateToggle");
    if (toggle && pol) toggle.checked = automatic();
    if (!el) return;
    if (!automatic()) {
      el.textContent = "Off — Xod won't update itself. Turn automatic updates on below and Xod handles them on its own.";
      return;
    }
    const last = pol && pol.last_updated && pol.last_updated.at;
    el.textContent = last ? `Automatic — last updated ${weekday(pol.last_updated.at)}.` : "Automatic — up to date.";
  }

  window.setUpdatePolicy = async (on) => {
    try {
      const r = await (await fetch("/api/update/policy", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ policy: on ? "automatic" : "off" }) })).json();
      if (pol) { pol.policy = r.policy; pol.automatic = r.automatic; }
      else pol = { policy: r.policy, automatic: r.automatic, last_updated: null };
    } catch (e) { /* keep the last known line rather than guessing */ }
    policyLine();
    pill();
    banner();
  };

  // AGE-48: the top-bar pill, rendered from this same state (no second
  // checker, no new endpoint). The running version, plus a green dot and
  // the word Current when it is the latest release, the newer version
  // named when one is out, and the plain version alone while a check is
  // still running or its result is unknown. Version number plus one
  // status word; no sentences in the top bar.
  function topPill() {
    const el = document.getElementById("xodVersionTop");
    if (!el) return;
    if (!st || !st.current) {
      el.classList.add("hidden");
      el.classList.remove("flex");
      return;
    }
    let dot = "", word = "";
    if (st.state === "uptodate") {
      dot = "bg-emerald-400";
      word = "Current";
    } else if (st.state === "downloading" && st.latest) {
      dot = "bg-amber-400 animate-pulse";
      const pct = Math.round((st.progress || 0) * 100);
      word = st.stage === "verifying" ? "Verifying…"
           : st.stage === "unpacking" ? "Unpacking…"
           : `${esc(st.latest)} · ${pct}%`;
    } else if (st.state === "ready" && st.latest) {
      dot = "bg-emerald-400 animate-pulse";
      word = `Restart · ${esc(st.latest)}`;
    } else if (st.state === "installing" && st.latest) {
      dot = "bg-amber-400 animate-pulse";
      word = "Updating…";
    } else if (["available", "manual"].includes(st.state) && st.latest) {
      dot = "bg-amber-400";
      word = `${esc(st.latest)} is out`;
    }
    el.innerHTML = (dot ? `<span class="size-1.5 rounded-full ${dot} shrink-0"></span>` : "")
      + `<span>${esc(st.current)}</span>`
      + (word ? `<span class="${st.state === "ready" ? "text-emerald-300 font-medium" : "text-zinc-500"}">${word}</span>` : "");
    el.classList.remove("hidden");
    el.classList.add("flex");
    renderPopover();
  }

  // The top-bar pill's click: the existing Settings panel, scrolled to
  // its Updates section, where Check now and the policy switch live.
  function openSettings() {
    hidePopover(true);
    if (window.openSettingsPanel) openSettingsPanel();
    requestAnimationFrame(() => {
      const el = document.getElementById("updatesSection");
      if (el) el.scrollIntoView({ block: "start" });
    });
  }

  // Hover / click status popover anchored to #xodVersionTop.
  let popPinned = false;
  let popHover = false;
  let popTimer = null;

  const fmtMB = (b) => b ? `${(b / (1024 * 1024)).toFixed(0)} MB` : "";

  function ensurePopover() {
    let pop = document.getElementById("xodUpdatePopover");
    if (pop) return pop;
    pop = document.createElement("div");
    pop.id = "xodUpdatePopover";
    pop.className = "hidden fixed z-[90] w-72 rounded-xl bg-surface-900/95 backdrop-blur-md border border-white/10 shadow-2xl p-3.5 text-left text-[12px] text-zinc-200";
    pop.addEventListener("mouseenter", () => { popHover = true; if (popTimer) { clearTimeout(popTimer); popTimer = null; } });
    pop.addEventListener("mouseleave", () => { popHover = false; scheduleHidePopover(); });
    document.body.appendChild(pop);
    return pop;
  }

  function positionPopover(pop) {
    const anchor = document.getElementById("xodVersionTop");
    if (!anchor) return;
    const r = anchor.getBoundingClientRect();
    const left = Math.max(12, Math.min(r.left, window.innerWidth - 300));
    pop.style.top = `${Math.round(r.bottom + 8)}px`;
    pop.style.left = `${Math.round(left)}px`;
  }

  function showPopover(pin) {
    if (pin) popPinned = true;
    const pop = ensurePopover();
    renderPopover();
    positionPopover(pop);
    pop.classList.remove("hidden");
  }

  function hidePopover(force) {
    if (!force && (popPinned || popHover)) return;
    popPinned = false;
    const pop = document.getElementById("xodUpdatePopover");
    if (pop) pop.classList.add("hidden");
  }

  function scheduleHidePopover() {
    if (popPinned) return;
    if (popTimer) clearTimeout(popTimer);
    popTimer = setTimeout(() => { if (!popHover && !popPinned) hidePopover(true); }, 220);
  }

  function togglePopover(ev) {
    if (ev) { ev.preventDefault(); ev.stopPropagation(); }
    const pop = document.getElementById("xodUpdatePopover");
    if (pop && !pop.classList.contains("hidden") && popPinned) {
      hidePopover(true);
    } else {
      showPopover(true);
    }
  }

  function renderPopover() {
    const pop = document.getElementById("xodUpdatePopover");
    if (!pop || !st) return;
    const s = st.state;
    const cur = esc(st.current || "");
    const latest = esc(st.latest || "");
    const pct = Math.round((st.progress || 0) * 100);
    const autoBadge = automatic()
      ? `<span class="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 text-[10px] font-medium">Auto ON</span>`
      : `<span class="px-1.5 py-0.5 rounded bg-zinc-700/80 text-zinc-400 text-[10px] font-medium">Auto OFF</span>`;

    let statusHtml = "";
    let primaryBtn = "";
    if (s === "checking") {
      statusHtml = `<div class="flex items-center gap-2 text-zinc-300"><i data-lucide="loader-2" class="size-3.5 animate-spin text-amber-400 shrink-0"></i><span>Checking GitHub for updates…</span></div>`;
    } else if (s === "downloading") {
      const bytes = st.bytesTotal > 0 ? ` (${fmtMB(st.bytesDone)} / ${fmtMB(st.bytesTotal)})` : "";
      const stageText = st.stage === "verifying" ? `Verifying SHA-256 checksum for ${latest}…`
                      : st.stage === "unpacking" ? `Unpacking & verifying Xod ${latest}…`
                      : `Downloading Xod ${latest}… ${pct}%${bytes}`;
      statusHtml = `
        <div class="flex items-center gap-2 text-amber-200"><i data-lucide="download" class="size-3.5 shrink-0"></i><span>${stageText}</span></div>
        <div class="mt-2 h-1.5 w-full rounded-full bg-white/10 overflow-hidden"><div class="h-full bg-amber-400 transition-all duration-200" style="width:${st.stage === "verifying" || st.stage === "unpacking" ? 100 : pct}%"></div></div>
        <div class="mt-1.5 text-[11px] text-zinc-400">Runs in the background — we'll alert you when it's ready to restart.</div>`;
    } else if (s === "ready") {
      statusHtml = `
        <div class="flex items-center gap-2 text-emerald-300 font-medium"><i data-lucide="sparkles" class="size-3.5 shrink-0"></i><span>Xod ${latest} is ready to install</span></div>
        <div class="mt-1 text-[11px] text-zinc-400">Downloaded & verified. Restart Xod to apply now, or it will install automatically when you quit.</div>`;
      primaryBtn = `<button data-xv="install" class="w-full mt-2.5 px-3 py-1.5 rounded-lg bg-amber-500 hover:bg-amber-400 text-black font-semibold text-[12px] transition-colors">Restart &amp; Update to ${latest}</button>`;
    } else if (s === "installing") {
      statusHtml = `<div class="flex items-center gap-2 text-amber-300"><i data-lucide="loader-2" class="size-3.5 animate-spin shrink-0"></i><span>Restarting into Xod ${latest}…</span></div>`;
    } else if (s === "available") {
      statusHtml = `<div class="flex items-center gap-2 text-amber-200"><i data-lucide="sparkles" class="size-3.5 shrink-0"></i><span>Xod ${latest} is available — starting download…</span></div>`;
      if (!automatic()) primaryBtn = `<button data-xv="install" class="w-full mt-2.5 px-3 py-1.5 rounded-lg bg-amber-500 hover:bg-amber-400 text-black font-semibold text-[12px] transition-colors">Download &amp; Update</button>`;
    } else if (s === "manual") {
      statusHtml = `<div class="flex items-center gap-2 text-amber-200"><i data-lucide="download" class="size-3.5 shrink-0"></i><span>Xod ${latest} is out</span></div>
        <div class="mt-1 text-[11px] text-zinc-400">${esc(st.error || "Download the new installer from GitHub.")}</div>`;
      primaryBtn = `<button data-xv="install" class="w-full mt-2.5 px-3 py-1.5 rounded-lg bg-amber-500 hover:bg-amber-400 text-black font-semibold text-[12px] transition-colors">Open Download Page</button>`;
    } else if (s === "error") {
      statusHtml = `<div class="flex items-center gap-2 text-rose-300"><i data-lucide="alert-circle" class="size-3.5 shrink-0"></i><span>Couldn't check for updates</span></div>
        <div class="mt-1 text-[11px] text-zinc-400 break-words">${esc(st.error || "")}</div>`;
    } else if (s === "uptodate") {
      statusHtml = `<div class="flex items-center gap-2 text-emerald-300"><i data-lucide="check-circle-2" class="size-3.5 shrink-0"></i><span>You're up to date</span></div>
        <div class="mt-1 text-[11px] text-zinc-400">Checked ${ago(st.checkedAt) || "just now"}. New versions download automatically.</div>`;
    } else {
      statusHtml = `<div class="text-zinc-300">Running Xod ${cur}</div>
        <div class="mt-1 text-[11px] text-zinc-400">${automatic() ? "Automatic updates are enabled." : "Automatic updates are off."}</div>`;
    }

    const notesLink = st.page ? `<a href="${esc(st.page)}" target="_blank" rel="noopener" class="text-zinc-400 hover:text-white text-[11px]">Release notes</a>` : "";
    pop.innerHTML = `
      <div class="flex items-center justify-between gap-2 pb-2 mb-2 border-b border-white/[0.08]">
        <span class="font-semibold text-zinc-100">Xod ${cur}</span>
        ${autoBadge}
      </div>
      ${statusHtml}
      ${primaryBtn}
      <div class="mt-3 pt-2 border-t border-white/[0.08] flex items-center justify-between gap-2">
        <button data-xv="check" class="inline-flex items-center gap-1 text-[11px] text-zinc-300 hover:text-white transition-colors"><i data-lucide="refresh-cw" class="size-3"></i><span>Check now</span></button>
        <div class="flex items-center gap-2.5">
          ${notesLink}
          <button data-xv="settings" class="text-[11px] text-zinc-400 hover:text-white transition-colors">Settings</button>
        </div>
      </div>`;
    if (window.lucide) lucide.createIcons();
  }

  // Floating restart-ready alert (Antigravity / Muse style) when an update
  // has finished downloading & verifying and needs a restart to apply.
  function restartToast() {
    const show = st && st.state === "ready" && st.latest;
    let el = document.getElementById("xodRestartToast");
    let dismissed = false;
    try { dismissed = sessionStorage.getItem("xod.restartToastDismissed") === st?.latest; } catch (e) {}
    if (!show || dismissed) { if (el) el.remove(); return; }
    if (!el) {
      el = document.createElement("div");
      el.id = "xodRestartToast";
      el.className = "fixed bottom-5 right-5 z-[95] w-80 rounded-2xl bg-surface-900/95 backdrop-blur-md border border-amber-400/35 shadow-2xl p-4 text-left animate-fade-in";
      document.body.appendChild(el);
    }
    const latest = esc(st.latest);
    const notes = st.page ? ` <a href="${esc(st.page)}" target="_blank" rel="noopener" class="text-amber-300 hover:underline">What's new</a>` : "";
    el.innerHTML = `
      <div class="flex items-start gap-3">
        <div class="size-8 rounded-xl bg-amber-500/15 border border-amber-400/30 flex items-center justify-center shrink-0 mt-0.5">
          <i data-lucide="sparkles" class="size-4 text-amber-300"></i>
        </div>
        <div class="flex-1 min-w-0">
          <div class="text-[13px] font-semibold text-zinc-100">Xod ${latest} is ready</div>
          <p class="mt-0.5 text-[12px] text-zinc-400 leading-snug">Update downloaded &amp; verified. Restart Xod to apply it now.${notes}</p>
          <div class="mt-3 flex items-center gap-2">
            <button data-xv="install" class="px-3 py-1.5 rounded-lg bg-amber-500 hover:bg-amber-400 text-black text-[12px] font-semibold transition-colors">Restart to update</button>
            <button data-xv="later-toast" class="px-2.5 py-1.5 rounded-lg text-[12px] text-zinc-400 hover:text-white hover:bg-white/5 transition-colors">Later</button>
          </div>
        </div>
      </div>`;
    if (window.lucide) lucide.createIcons();
  }

  function pill() {
    topPill();
    const host = document.getElementById("xodVersion");
    if (!host || !st) return;
    const s = st.state;
    const cur = esc(st.current || "");
    const latest = esc(st.latest || "");
    let body = "", cls = "xv";
    if (automatic()) {
      // No action needed, ever: states read as calm facts, with no buttons.
      if (s === "checking") {
        body = `<i data-lucide="loader-2" class="size-3.5 animate-spin"></i><span class="xv-text">Xod ${cur} · checking for updates…</span>`;
      } else if (s === "downloading") {
        const pct = Math.round((st.progress || 0) * 100);
        body = `<i data-lucide="download" class="size-3.5"></i><span class="xv-text">Getting Xod ${latest}… ${pct}% — it installs itself.</span>
          <span class="xv-bar"><span style="width:${pct}%"></span></span>`;
      } else if (s === "installing") {
        body = `<i data-lucide="loader-2" class="size-3.5 animate-spin"></i><span class="xv-text">Restarting into Xod ${latest}…</span>`;
      } else if (s === "available" || s === "ready" || s === "manual") {
        body = `<i data-lucide="sparkles" class="size-3.5"></i><span class="xv-text">Xod ${latest} is on its way — it installs itself.</span>`;
      } else if (s === "error") {
        body = `<i data-lucide="check-circle-2" class="size-3.5 text-emerald-400"></i><span class="xv-text" title="${esc(st.error)}">Xod ${cur} · automatic updates on</span>`;
      } else if (s === "uptodate") {
        body = `<i data-lucide="check-circle-2" class="size-3.5 text-emerald-400"></i><span class="xv-text" title="Checked ${ago(st.checkedAt)}">Xod ${cur} · up to date</span>`;
      } else {
        body = `<span class="xv-text">Xod ${cur} · automatic updates on</span>`;
      }
      host.className = cls;
      host.innerHTML = body;
      if (window.lucide) lucide.createIcons();
      policyLine();
      return;
    }
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
    policyLine();
  }

  function banner() {
    // Automatic updates never prompt: the home-screen notice only exists
    // when the user turned automatic updates off.
    const show = !automatic() && st && ["available", "ready", "downloading", "manual"].includes(st.state);
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
    window.__xodUpdating = ["downloading", "installing"].includes(next.state);   // mascot.js: a look pack shows its upgrading state meanwhile
    pill();
    banner();
    restartToast();
  }

  async function act(what) {
    if (what === "later") {
      try { localStorage.setItem("xod.updateDismissed", st.latest); } catch (e) {}
      return banner();
    }
    if (what === "later-toast") {
      try { sessionStorage.setItem("xod.restartToastDismissed", st.latest); } catch (e) {}
      return restartToast();
    }
    if (what === "settings") {
      return openSettings();
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
    if (b && (b.closest("#xodVersion") || b.closest("#updateBanner") || b.closest("#xodUpdatePopover") || b.closest("#xodRestartToast"))) {
      act(b.dataset.xv);
      return;
    }
    if (!e.target.closest("#xodUpdatePopover") && !e.target.closest("#xodVersionTop")) {
      hidePopover(true);
    }
  });

  function wireTopPill() {
    const el = document.getElementById("xodVersionTop");
    if (!el || el.dataset.xvWired) return;
    el.dataset.xvWired = "1";
    el.addEventListener("mouseenter", () => showPopover(false));
    el.addEventListener("mouseleave", () => scheduleHidePopover());
    // Capture click so clicking the top pill opens/pins the status popover
    // (which has a Settings link) instead of immediately jumping away.
    el.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopImmediatePropagation();
      togglePopover(ev);
    }, true);
  }

  async function boot() {
    wireTopPill();
    if (desk) {
      desk.onState(render);
      render(await desk.status());
      serverStatus(false).then(() => { policyLine(); pill(); banner(); restartToast(); });   // policy + last-updated for the Settings line
    } else {
      render(await serverStatus(false));
    }
  }
  window.XodUpdates = { state: () => st, check: () => act("check"), install: () => act("install"), openSettings, togglePopover, _render: render };
  setTimeout(boot, 1200);
  setInterval(pill, 60000);   // keeps "checked 5 min ago" fresh
})();
