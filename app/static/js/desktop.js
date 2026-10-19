// Xod at this Mac's real screen (backend: services/desktop.py, desktop_task.py).
//  - Add-ons → "Use this computer's screen": macOS permissions (Screen Recording, Accessibility) with Allow, and the apps
//    Xod may use (each asked for the first time), with Remove.
//  - While Xod is working on the screen, a chip shows what it's doing, with Stop. Moving the mouse stops it too.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const post = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }).then((r) => r.json());

  window.fillDesktopExtra = async () => {
    const box = document.querySelector("[data-desktop-extra]");
    if (!box) return;
    let s;
    try { s = await (await fetch("/api/desktop/status")).json(); } catch (e) { return; }
    if (!s.supported) { box.innerHTML = `<p class="text-[12px] text-amber-300">${esc(s.message)}</p>`; return; }
    const perm = (ok, name) => `<span class="inline-flex items-center gap-1 text-[12px] ${ok ? "text-emerald-300" : "text-amber-300"}">
      <i data-lucide="${ok ? "check" : "alert-triangle"}" class="size-3.5"></i>${name}</span>`;
    const apps = (s.apps || []).map((b) => `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-surface-700 text-[11px] text-zinc-300">${esc(b)}
      <button data-desktop-forget="${esc(b)}" class="text-zinc-500 hover:text-red-300" title="Ask again before using it">×</button></span>`).join(" ");
    box.innerHTML = `
      <div class="flex flex-wrap items-center gap-3">${perm(s.screen, "Screen Recording")}${perm(s.accessibility, "Accessibility")}
        ${s.screen && s.accessibility ? "" : `<button data-desktop-allow class="px-2.5 py-1 rounded-lg text-[12px] bg-brand-500 text-surface-900 font-semibold hover:bg-brand-400">Allow in macOS</button>`}</div>
      ${s.screen && s.accessibility ? "" : `<p class="text-[11px] text-zinc-500 mt-1">In System Settings → Privacy & Security, switch on <b class="text-zinc-300">${esc(s.app)}</b> under both (if it isn't listed, press + and pick <span class="font-mono break-all">${esc(s.app_path)}</span>). macOS may ask you to reopen it.${s.app !== "Xod" ? ` <span class="text-zinc-400">This Xod was started from ${esc(s.app)}, so macOS asks for ${esc(s.app)}; in the Xod app it's listed as Xod.</span>` : ""}</p>`}
      <p class="text-[11px] text-zinc-500 mt-2">Apps Xod may use: ${apps || "none yet (it asks the first time)"}</p>`;
    if (window.lucide) lucide.createIcons();
  };

  // Switching the add-on on asks macOS straight away (its prompt, then the exact Settings page), and the card ticks
  // each permission off by itself as you switch it on: nothing to go looking for.
  document.addEventListener("change", async (e) => {
    const t = e.target.closest('[data-pack-toggle="desktop-control"]');
    if (!t || !t.checked) return;
    setTimeout(async () => {
      const r = await post("/api/desktop/permissions");
      if (r.missing && r.missing.length && typeof showToast === "function") showToast(r.message);
      watchPermissions();
    }, 600);
  });

  let permTimer = null;
  function watchPermissions() {
    clearInterval(permTimer);
    let last = "";
    const started = Date.now();
    permTimer = setInterval(async () => {
      if (Date.now() - started > 10 * 60 * 1000) return clearInterval(permTimer);
      let s;
      try { s = await (await fetch("/api/desktop/status")).json(); } catch (err) { return; }
      const now = `${s.screen}${s.accessibility}`;
      if (now !== last) {
        last = now;
        window.fillDesktopExtra();
        if (s.screen && s.accessibility) {
          clearInterval(permTimer);
          if (typeof showToast === "function") showToast("Xod can see your screen and use the mouse and keyboard now.");
        } else if (s.screen && !s.accessibility) {
          post("/api/desktop/permissions", {});          // the next page: Accessibility
        }
      }
    }, 2000);
  }

  document.addEventListener("click", async (e) => {
    const allow = e.target.closest("[data-desktop-allow]");
    if (allow) {
      const r = await post("/api/desktop/permissions");
      if (typeof showToast === "function" && r.message) showToast(r.message);
      watchPermissions();
      return;
    }
    const forget = e.target.closest("[data-desktop-forget]");
    if (forget) {
      await post("/api/desktop/apps/forget", { bundle: forget.dataset.desktopForget });
      window.fillDesktopExtra();
      return;
    }
    if (e.target.closest("[data-desktop-stop]")) {
      await post("/api/desktop/stop");
    }
  });

  // The chip while a screen task runs.
  let chip = null;
  async function watch() {
    let s;
    try { s = await (await fetch("/api/desktop/status")).json(); } catch (e) { return; }
    const t = s.task;
    if (!t || !t.running) { if (chip) chip.classList.add("hidden"); return; }
    if (!chip) {
      chip = document.createElement("div");
      chip.className = "fixed bottom-24 right-5 z-[80] max-w-sm flex items-center gap-2.5 px-3.5 py-2.5 rounded-2xl bg-surface-850 border border-brand-500/40 shadow-2xl text-[12px]";
      document.body.appendChild(chip);
    }
    chip.classList.remove("hidden");
    chip.innerHTML = `<span class="size-2 rounded-full bg-brand-400 animate-pulse shrink-0"></span>
      <div class="min-w-0"><div class="text-white">Xod is using your screen · step ${t.step}</div>
        <div class="text-zinc-400 truncate" title="${esc(t.now)}">${esc(t.now || t.goal)}</div>
        <div class="text-zinc-500">Move the mouse to take over.</div></div>
      <button data-desktop-stop class="px-2 py-1 rounded-md text-red-300 bg-red-500/10 hover:bg-red-500/20 shrink-0">Stop</button>`;
  }
  setInterval(() => { if (window.enabledPacks && window.enabledPacks.has("desktop-control")) watch(); }, 1500);
})();
