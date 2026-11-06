// ==============================================================
// SURFACES (Phase 2, simplicity redesign): the five top-level
// surfaces - Ask, Work, Library, Family, Settings - are the app's
// whole primary navigation. The 21-destination Tools menu is gone;
// every one of its items lives in exactly one mapped home below
// (the mapping table in XOD-SIMPLICITY-REDESIGN-2026-11-05.md s.3),
// or is reachable by asking. Nothing was deleted: every old opener
// (openHealthPanel, openRoutingPanel, openModelLabModal, ...) still
// exists and is what the rows below call.
//
//   Ask      = home: face, status line, chats, the one composer.
//   Work     = the Work drawer (tasks.js): Feed / Routines & goals
//              section / History tab (Activity, with undo).
//   Library  = projects, files, media, memory, collections.
//   Family   = device cards (family.js) + Messages (Agent Mail).
//   Settings = Brain (the model picker's final home), Look, Voice,
//              Connections, Updates, and the Advanced drawer
//              (closed by default): Model routing, Model Lab,
//              Health diagnostics, Playground.
// ==============================================================
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => (typeof escapeHtml === "function" ? escapeHtml(String(s ?? "")) : String(s ?? ""));

  async function api(path) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" } });
    return res.json().catch(() => ({}));
  }

  // ---------- panel open/close helpers (tasks-drawer pattern) ----------
  function openDrawer(id) {
    const p = $(id);
    if (!p) return;
    p.classList.remove("hidden");
    requestAnimationFrame(() => p.classList.add("is-open"));
  }
  function closeDrawer(id) {
    const p = $(id);
    if (!p) return;
    p.classList.remove("is-open");
    setTimeout(() => p.classList.add("hidden"), 250);
  }
  window.openLibraryPanel = () => { if (window.surfaceHighlight) window.surfaceHighlight("library"); openDrawer("libraryPanel"); renderLibraryProjects(); };
  window.closeLibraryPanel = () => closeDrawer("libraryPanel");
  window.openSettingsPanel = () => { if (window.surfaceHighlight) window.surfaceHighlight("settings"); openDrawer("settingsPanel"); renderPlayground(); syncBrainMirror(); };
  window.closeSettingsPanel = () => closeDrawer("settingsPanel");

  function closeAllSurfaces() {
    ["tasksPanel", "libraryPanel", "settingsPanel", "familyPanel", "healthPanel",
     "activityPanel", "routinesPanel", "xodsPanel", "phonePanel", "mailPanel"].forEach((id) => {
      const p = $(id);
      if (p && !p.classList.contains("hidden")) {
        p.classList.remove("is-open");
        setTimeout(() => p.classList.add("hidden"), 250);
      }
    });
  }

  // ---------- the five-surface navigation ----------
  window.surfaceHighlight = (name) => {
    document.querySelectorAll("#surfaceNav [data-surface]").forEach((b) =>
      b.classList.toggle("is-active", b.dataset.surface === name));
  };
  window.goSurface = (name) => {
    window.surfaceHighlight(name);
    if (name === "ask") {
      closeAllSurfaces();
      const input = $("chatInput");
      if (input) input.focus();
      return;
    }
    if (name === "work") { if (window.openTasksPanel) openTasksPanel(); return; }
    if (name === "library") { window.openLibraryPanel(); return; }
    if (name === "family") { if (window.openFamilyPanel) openFamilyPanel(); return; }
    if (name === "settings") { window.openSettingsPanel(); return; }
  };

  // Opening a drawer directly (old deep links, other panels) keeps
  // the nav highlight honest.
  ["openTasksPanel", "openFamilyPanel"].forEach((fn) => {
    const orig = window[fn];
    if (orig) window[fn] = () => {
      window.surfaceHighlight(fn === "openTasksPanel" ? "work" : "family");
      return orig();
    };
  });

  // ---------- Work tabs: Feed / Routines & goals / History ----------
  window.showWorkTab = (tab) => {
    const feed = tab === "feed";
    const form = $("workComposerForm");
    if (form) form.classList.toggle("hidden", !feed);
    const scroll = $("workFeedScroll");
    if (scroll) scroll.classList.toggle("hidden", !feed);
    const routines = $("workRoutinesView");
    if (routines) routines.classList.toggle("hidden", tab !== "routines");
    const history = $("workHistoryView");
    if (history) history.classList.toggle("hidden", tab !== "history");
    [["workTabFeed", "feed"], ["workTabRoutines", "routines"], ["workTabHistory", "history"]].forEach(([id, t]) => {
      const b = $(id);
      if (b) b.classList.toggle("is-active", t === tab);
    });
    if (tab === "history" && window.__xodRenderActivityInto) {
      const body = $("workHistoryBody");
      if (body) window.__xodRenderActivityInto(body);
    }
    if (tab === "routines") renderWorkRoutines();
  };

  async function renderWorkRoutines() {
    const host = $("workRoutinesList");
    if (!host) return;
    try {
      const [r, g] = await Promise.all([api("/api/routines"), api("/api/goals")]);
      const routines = r.routines || [];
      const goals = g.goals || [];
      const rows = [
        ...goals.map((x) => `<div class="task-card"><p class="task-title">${esc(x.title || x.text || "Goal")}</p><p class="text-[12px] text-zinc-400">Goal · ${esc(x.status || "active")}</p></div>`),
        ...routines.map((x) => `<div class="task-card"><p class="task-title">${esc(x.name || x.title || "Routine")}</p><p class="text-[12px] text-zinc-400">Routine · ${x.enabled === false ? "paused" : "on"}</p></div>`),
      ];
      host.innerHTML = rows.length ? rows.join("")
        : `<p class="text-[13px] text-zinc-500 py-6 text-center">No routines or goals yet. Ask Xod to repeat something, or set a goal it should work toward.</p>`;
    } catch (_) {
      host.innerHTML = `<p class="text-[13px] text-zinc-500 py-6 text-center">Could not load routines just now.</p>`;
    }
  }

  // ---------- Library: real projects (Playground rules still apply) ----------
  async function renderLibraryProjects() {
    const host = $("libraryProjects");
    if (!host) return;
    try {
      const data = await api("/api/projects");
      const real = (data.projects || []).filter((p) => !p.scratch);
      host.innerHTML = real.length
        ? real.map((p) => `
          <button onclick="openLibraryProject('${esc(p.slug)}')" class="w-full text-left task-card hover:border-brand-500">
            <p class="task-title">${esc(p.name)}</p>
            ${p.description ? `<p class="text-[12px] text-zinc-400 truncate">${esc(p.description)}</p>` : ""}
          </button>`).join("")
        : `<p class="text-[13px] text-zinc-500">No projects yet. Ask Xod to start one.</p>`;
    } catch (_) {
      host.innerHTML = `<p class="text-[13px] text-zinc-500">Could not load projects just now.</p>`;
    }
    if (window.lucide) lucide.createIcons();
  }

  window.openLibraryProject = (slug) => {
    window.closeLibraryPanel();
    if (typeof switchSidebarTab === "function") switchSidebarTab("projects");
    setTimeout(() => {
      const row = document.querySelector(`.sb-project-row[data-slug="${CSS.escape(slug)}"]`);
      if (row) row.click();
    }, 60);
  };

  // ---------- Settings - Advanced - Playground ----------
  // Scratch projects (Phase 0) and scratch tasks/missions (Phase 2's
  // origin stamp) live here, out of the main lists. Display-only:
  // nothing is deleted, and everything stays reachable.
  async function renderPlayground() {
    const host = $("settingsPlayground");
    if (!host) return;
    try {
      const [projects, tasks, missions] = await Promise.all([
        api("/api/projects"), api("/api/tasks"), api("/api/missions"),
      ]);
      const pgProjects = (projects.projects || []).filter((p) => p.scratch);
      const pgTasks = (tasks.tasks || []).filter((t) => t.scratch);
      const pgMissions = (missions.missions || []).filter((m) => m.scratch);
      const item = (label, sub) => `<div class="py-1.5 border-b border-white/[0.04]"><p class="text-[13px] text-zinc-200">${esc(label)}</p><p class="text-[11px] text-zinc-500">${esc(sub)}</p></div>`;
      host.innerHTML = `
        ${pgProjects.length ? `<p class="text-[11px] uppercase tracking-wider text-zinc-500 font-semibold pt-1">Projects (${pgProjects.length})</p>` + pgProjects.map((p) => item(p.name, "Scratch project")).join("") : ""}
        ${pgTasks.length ? `<p class="text-[11px] uppercase tracking-wider text-zinc-500 font-semibold pt-2">Jobs (${pgTasks.length})</p>` + pgTasks.map((t) => item(t.title, `Job · ${t.status}`)).join("") : ""}
        ${pgMissions.length ? pgMissions.map((m) => item(m.goal, `Bigger job · ${m.state}`)).join("") : ""}
        ${(!pgProjects.length && !pgTasks.length && !pgMissions.length) ? `<p class="text-[12px] text-zinc-500">Empty. Test and proving work Xod does for itself lands here instead of in your lists.</p>` : ""}`;
    } catch (_) {
      host.innerHTML = `<p class="text-[12px] text-zinc-500">Could not load the Playground just now.</p>`;
    }
  }
  window.__xodRenderPlayground = renderPlayground;

  // ---------- Settings - Brain mirror in the status detail ----------
  // The model picker itself lives in Settings - Brain (its final
  // home). The status detail keeps a one-line summary so the top bar
  // still answers "which model?" without opening Settings.
  function syncBrainMirror() {
    const mirror = $("statusBrainMirror");
    const src = $("headerStatusText");
    if (mirror && src) mirror.textContent = src.textContent || "";
  }
  window.__xodSyncBrainMirror = syncBrainMirror;
  const origToggleStatus = window.toggleStatusDetail;
  if (origToggleStatus) {
    window.toggleStatusDetail = () => { origToggleStatus(); setTimeout(syncBrainMirror, 60); };
  }
  setInterval(syncBrainMirror, 5000);
})();
