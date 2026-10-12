// Sidebar: projects (folders with chats and saved files) and loose chats grouped by date.
// Chats and files can be dragged onto a project; dropping a chat on "Chats" takes it out of its project.
// Every chat also has a ⋯ menu (rename, move to, delete) for when dragging is awkward.

let localProjects = [];
let unfiledChats = [];
let activeProjectSlug = "";
let selectedProjectColor = "#6366f1";
let sidebarProjectFilter = "";
let expandedProjectFolders = new Set(sidebarLoad("xod.sidebar.expanded", []));
let collapsedSidebarSections = new Set(sidebarLoad("xod.sidebar.collapsed", []));
let sidebarDrag = null;

function sidebarLoad(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch (e) { return fallback; }
}
function sidebarSave() {
  try {
    localStorage.setItem("xod.sidebar.expanded", JSON.stringify([...expandedProjectFolders]));
    localStorage.setItem("xod.sidebar.collapsed", JSON.stringify([...collapsedSidebarSections]));
  } catch (e) {}
}

async function loadProjects() {
  try {
    const res = await fetch("/api/projects");
    const data = await res.json();
    localProjects = data.projects || [];
    unfiledChats = data.unfiled || [];
    renderProjectsSidebar();
  } catch (err) {
    console.error("Failed to load projects:", err);
  }
}

// Older code calls loadSessions() after every chat change; one refresh covers both lists.
function loadSessions() {
  clearTimeout(loadSessions._t);
  loadSessions._t = setTimeout(loadProjects, 150);
}

// A chat started while a project is in focus belongs to that project.
async function sidebarOnNewSession(sessionId) {
  const slug = activeWorkingScope && activeWorkingScope.type === "project" ? activeWorkingScope.slug : "";
  if (!sessionId || !slug) return;
  if (unfiledChats.some(c => c.id === sessionId) || !localProjects.some(p => (p.sessions || []).some(s => s.id === sessionId))) {
    await moveChatToProject(sessionId, slug, { quiet: true });
  }
}

function chatDateGroup(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = d.getTime();
  if (t >= startOfToday) return "Today";
  if (t >= startOfToday - 86400000) return "Yesterday";
  if (t >= startOfToday - 6 * 86400000) return "Previous 7 days";
  if (t >= startOfToday - 29 * 86400000) return "Previous 30 days";
  return "Older";
}

function sidebarMatches(text) {
  const q = sidebarProjectFilter.toLowerCase().trim();
  return !q || String(text || "").toLowerCase().includes(q);
}

function chatRowHtml(s, fromSlug) {
  const isCurrent = s.id === currentSessionId;
  return `
    <div class="sb-row sb-chat ${isCurrent ? "is-current" : ""}" draggable="true"
         data-kind="chat" data-id="${escapeHtml(s.id)}" data-from="${escapeHtml(fromSlug)}" title="${escapeHtml(s.title || "Untitled chat")}">
      <span class="sb-title">${escapeHtml(s.title || "Untitled chat")}</span>
      <span class="sb-meta">${escapeHtml(s.time_ago_str || "")}</span>
      <button class="sb-more" data-action="chat-menu" title="Rename, move or delete" aria-label="Chat options"><i data-lucide="more-horizontal" class="size-3.5"></i></button>
    </div>`;
}

function renderProjectsSidebar() {
  const container = document.getElementById("projectsTreeContainer");
  if (!container) return;
  const filtering = !!sidebarProjectFilter.trim();
  let html = "";

  // ---- Projects
  const projCollapsed = collapsedSidebarSections.has("projects") && !filtering;
  let projHtml = "";
  let shownProjects = 0;
  for (const p of localProjects) {
    const sessions = (p.sessions || []).filter(s => sidebarMatches(s.title) || sidebarMatches(p.name));
    const files = (p.files || []).filter(f => sidebarMatches(f.filename) || sidebarMatches(p.name));
    if (filtering && !sidebarMatches(p.name) && !sessions.length && !files.length) continue;
    shownProjects++;
    const open = expandedProjectFolders.has(p.slug) || (filtering && (sessions.length || files.length));
    const isScoped = activeWorkingScope && (activeWorkingScope.slug === p.slug || activeWorkingScope.path === p.path);
    const count = (p.sessions || []).length + (p.files || []).length + (p.apps || []).length;
    const icon = (!p.icon || p.icon === "folder") ? (open ? "folder-open" : "folder") : p.icon;
    projHtml += `
      <div class="sb-project" data-drop="project" data-slug="${escapeHtml(p.slug)}">
        <div class="sb-row sb-project-row ${isScoped ? "is-scoped" : ""}" data-action="toggle-project" data-slug="${escapeHtml(p.slug)}" title="${escapeHtml(p.description || "")}">
          <i data-lucide="${open ? "chevron-down" : "chevron-right"}" class="size-3 text-zinc-500 shrink-0"></i>
          <i data-lucide="${icon}" class="size-3.5 shrink-0" style="color:${isScoped ? "#10b981" : (p.color || "#a1a1aa")}"></i>
          <span class="sb-title">${escapeHtml(p.name || p.slug)}</span>
          ${isScoped ? `<span class="sb-pill">In focus</span>` : ""}
          <button class="sb-more" data-action="scope-project" title="${isScoped ? "Stop focusing on this project" : "Focus Xod on this project (new chats go here)"}"><i data-lucide="crosshair" class="size-3"></i></button>
          ${count ? `<span class="sb-count">${count}</span>` : ""}
        </div>
        ${open ? `
          <div class="sb-children">
            ${(p.apps || []).map(a => `
              <div class="sb-row sb-app" data-action="open-app" data-path="${escapeHtml(a.path)}" title="Open ${escapeHtml(a.title)} in the preview">
                <i data-lucide="play" class="size-3 shrink-0 text-brand-400"></i>
                <span class="sb-title">${escapeHtml(a.title)}</span>
              </div>`).join("")}
            ${sessions.map(s => chatRowHtml(s, p.slug)).join("")}
            ${files.map(f => `
              <div class="sb-row sb-file" draggable="true" data-kind="file" data-slug="${escapeHtml(p.slug)}"
                   data-subfolder="${escapeHtml(f.subfolder)}" data-filename="${escapeHtml(f.filename)}" title="${escapeHtml(f.filename)} · ${escapeHtml(f.size_formatted || "")}">
                <i data-lucide="file-text" class="size-3 shrink-0 text-amber-400/80"></i>
                <span class="sb-title">${escapeHtml(f.filename)}</span>
              </div>`).join("")}
            ${!sessions.length && !files.length && !(p.apps || []).length ? `<div class="sb-empty">Empty — drag chats or files here</div>` : ""}
          </div>` : ""}
      </div>`;
  }
  html += `
    <div class="sb-section">
      <button class="sb-section-head" data-action="toggle-section" data-section="projects">
        <i data-lucide="${projCollapsed ? "chevron-right" : "chevron-down"}" class="size-3"></i>
        <span>Projects</span><span class="sb-count">${localProjects.length}</span>
        <span class="flex-1"></span>
        <span class="sb-head-btn" data-action="new-project" title="New project"><i data-lucide="plus" class="size-3.5"></i></span>
      </button>
      ${projCollapsed ? "" : (projHtml || `<div class="sb-empty">${filtering ? "No matching projects" : "No projects yet"}</div>`)}
    </div>`;

  // ---- Loose chats, grouped by date
  const chats = unfiledChats.filter(c => sidebarMatches(c.title));
  const chatsCollapsed = collapsedSidebarSections.has("chats") && !filtering;
  let chatHtml = "";
  let group = "";
  for (const c of chats) {
    const g = chatDateGroup(c.updated_at);
    if (g !== group) {
      group = g;
      chatHtml += `<div class="sb-group">${g}</div>`;
    }
    chatHtml += chatRowHtml(c, "");
  }
  html += `
    <div class="sb-section sb-chats" data-drop="chats">
      <button class="sb-section-head" data-action="toggle-section" data-section="chats">
        <i data-lucide="${chatsCollapsed ? "chevron-right" : "chevron-down"}" class="size-3"></i>
        <span>Chats</span><span class="sb-count">${unfiledChats.length}</span>
      </button>
      ${chatsCollapsed ? "" : (chatHtml || `<div class="sb-empty">${filtering ? "No matching chats" : "Chats that aren't in a project show up here"}</div>`)}
    </div>`;

  container.innerHTML = html;
  if (window.lucide) lucide.createIcons();
}

function toggleProjectFolder(slug) {
  if (expandedProjectFolders.has(slug)) expandedProjectFolders.delete(slug);
  else expandedProjectFolders.add(slug);
  activeProjectSlug = slug;
  sidebarSave();
  renderProjectsSidebar();
}

function toggleProjectScope(slug) {
  const p = localProjects.find(x => x.slug === slug);
  if (!p) return;
  if (activeWorkingScope && activeWorkingScope.slug === slug) {
    clearActiveWorkingScope();
    renderProjectsSidebar();
    return;
  }
  setActiveWorkingScope({ name: p.name, path: p.path, type: "project", slug: p.slug, icon: p.icon, color: p.color });
}

function toggleSidebarProjectSearch() {
  const box = document.getElementById("sidebarProjectSearchBox");
  const input = document.getElementById("sidebarProjectSearchInput");
  if (!box) return;
  if (box.classList.contains("hidden")) {
    box.classList.remove("hidden");
    if (input) input.focus();
  } else {
    box.classList.add("hidden");
    sidebarProjectFilter = "";
    if (input) input.value = "";
    renderProjectsSidebar();
  }
}

function filterSidebarProjects() {
  const input = document.getElementById("sidebarProjectSearchInput");
  sidebarProjectFilter = input ? input.value : "";
  renderProjectsSidebar();
}

// ---------------------------------------------------------------- moving things

function projectName(slug) {
  if (!slug) return "Chats";
  const p = localProjects.find(x => x.slug === slug);
  return p ? (p.name || slug) : slug;
}

async function moveChatToProject(sessionId, slug, opts = {}) {
  try {
    const res = await fetch("/api/projects/assign-session", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, project_slug: slug || "" }),
    });
    const data = await res.json();
    if (!data.success) throw new Error("not moved");
    if (slug) expandedProjectFolders.add(slug);
    sidebarSave();
    await loadProjects();
    if (!opts.quiet) showToast(`Moved to ${projectName(slug)}`);
  } catch (e) {
    showToast("Couldn't move that chat");
  }
}

async function moveFileToProject(fromSlug, subfolder, filename, toSlug) {
  try {
    const res = await fetch("/api/projects/move-file", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from_slug: fromSlug, subfolder, filename, to_slug: toSlug }),
    });
    const data = await res.json();
    if (data.error) { showToast(data.error); return; }
    expandedProjectFolders.add(toSlug);
    sidebarSave();
    await loadProjects();
    showToast(`Moved ${filename} to ${projectName(toSlug)}`);
  } catch (e) {
    showToast("Couldn't move that file");
  }
}

async function renameChat(sessionId) {
  const all = [...unfiledChats, ...localProjects.flatMap(p => p.sessions || [])];
  const chat = all.find(c => c.id === sessionId);
  const title = prompt("Rename chat", chat ? chat.title : "");
  if (title === null || !title.trim()) return;
  await fetch(`/api/sessions/${sessionId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: title.trim() }),
  });
  loadProjects();
}

// ---------------------------------------------------------------- ⋯ menu

function closeSidebarMenu() {
  const m = document.getElementById("sidebarMenu");
  if (m) m.remove();
}

function openChatMenu(btn, sessionId, fromSlug) {
  closeSidebarMenu();
  const m = document.createElement("div");
  m.id = "sidebarMenu";
  m.className = "sb-menu";
  const targets = [{ slug: "", name: "Chats (no project)", icon: "messages-square" },
    ...localProjects.map(p => ({ slug: p.slug, name: p.name || p.slug, icon: (!p.icon || p.icon === "folder") ? "folder" : p.icon, color: p.color }))]
    .filter(t => t.slug !== fromSlug);
  m.innerHTML = `
    <button data-menu="rename"><i data-lucide="pencil" class="size-3.5"></i>Rename</button>
    <div class="sb-menu-label">Move to</div>
    <div class="sb-menu-scroll">
      ${targets.map(t => `<button data-menu="move" data-slug="${escapeHtml(t.slug)}"><i data-lucide="${t.icon}" class="size-3.5" ${t.color ? `style="color:${t.color}"` : ""}></i><span class="truncate">${escapeHtml(t.name)}</span></button>`).join("")}
    </div>
    <div class="sb-menu-sep"></div>
    <button data-menu="delete" class="is-danger"><i data-lucide="trash-2" class="size-3.5"></i>Delete</button>`;
  document.body.appendChild(m);
  // Anchor to the row: the ⋯ button only has a size while the row is hovered.
  const r = (btn.closest(".sb-row") || btn).getBoundingClientRect();
  const below = r.bottom + 4 + m.offsetHeight <= window.innerHeight - 8;
  m.style.top = `${Math.max(8, below ? r.bottom + 4 : r.top - m.offsetHeight - 4)}px`;
  m.style.left = `${Math.max(8, Math.min(r.right - m.offsetWidth, window.innerWidth - m.offsetWidth - 8))}px`;
  if (window.lucide) lucide.createIcons();
  m.addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-menu]");
    if (!b) return;
    closeSidebarMenu();
    if (b.dataset.menu === "rename") renameChat(sessionId);
    else if (b.dataset.menu === "move") moveChatToProject(sessionId, b.dataset.slug);
    else if (b.dataset.menu === "delete") deleteSession(sessionId, { stopPropagation() {} });
  });
}

document.addEventListener("click", (e) => {
  if (!e.target.closest("#sidebarMenu") && !e.target.closest("[data-action='chat-menu']")) closeSidebarMenu();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSidebarMenu(); });

// ---------------------------------------------------------------- clicks and drags (delegated)

function initSidebarEvents() {
  const tree = document.getElementById("projectsTreeContainer");
  if (!tree || tree.dataset.wired) return;
  tree.dataset.wired = "1";

  tree.addEventListener("click", (e) => {
    const act = e.target.closest("[data-action]");
    const row = e.target.closest(".sb-row");
    if (act) {
      const a = act.dataset.action;
      if (a === "chat-menu") {
        e.stopPropagation();
        const chat = act.closest(".sb-chat");
        if (document.getElementById("sidebarMenu")) closeSidebarMenu();
        else openChatMenu(act, chat.dataset.id, chat.dataset.from);
        return;
      }
      if (a === "scope-project") { e.stopPropagation(); toggleProjectScope(act.closest(".sb-project").dataset.slug); return; }
      if (a === "new-project") { e.stopPropagation(); openCreateProjectModal(); return; }
      if (a === "toggle-section") {
        const s = act.dataset.section;
        collapsedSidebarSections.has(s) ? collapsedSidebarSections.delete(s) : collapsedSidebarSections.add(s);
        sidebarSave();
        renderProjectsSidebar();
        return;
      }
      if (a === "toggle-project") { toggleProjectFolder(act.dataset.slug); return; }
      if (a === "open-app") {
        const app = localProjects.flatMap(p => p.apps || []).find(x => x.path === act.dataset.path);
        if (app && window.XodPreview) XodPreview.open({ url: app.url, root: app.root, title: app.title, path: app.path });
        return;
      }
    }
    if (row && row.dataset.kind === "chat") selectSession(row.dataset.id);
    else if (row && row.dataset.kind === "file") previewProjectFile(row.dataset.slug, row.dataset.subfolder, row.dataset.filename);
  });

  tree.addEventListener("dragstart", (e) => {
    const row = e.target.closest("[draggable='true']");
    if (!row) return;
    closeSidebarMenu();
    sidebarDrag = row.dataset.kind === "chat"
      ? { kind: "chat", id: row.dataset.id, from: row.dataset.from }
      : { kind: "file", slug: row.dataset.slug, subfolder: row.dataset.subfolder, filename: row.dataset.filename };
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", row.dataset.id || row.dataset.filename);
    row.classList.add("is-dragging");
    tree.classList.add("is-dragging");
  });

  tree.addEventListener("dragend", () => {
    sidebarDrag = null;
    tree.classList.remove("is-dragging");
    tree.querySelectorAll(".is-dragging, .drop-ok").forEach(el => el.classList.remove("is-dragging", "drop-ok"));
    clearTimeout(initSidebarEvents._open);
  });

  const dropTargetFor = (el) => {
    if (!sidebarDrag) return null;
    const zone = el.closest("[data-drop]");
    if (!zone) return null;
    if (zone.dataset.drop === "chats") return sidebarDrag.kind === "chat" && sidebarDrag.from ? zone : null;
    const slug = zone.dataset.slug;
    if (sidebarDrag.kind === "chat" && slug === sidebarDrag.from) return null;
    if (sidebarDrag.kind === "file" && slug === sidebarDrag.slug) return null;
    return zone;
  };

  tree.addEventListener("dragover", (e) => {
    const zone = dropTargetFor(e.target);
    tree.querySelectorAll(".drop-ok").forEach(el => { if (el !== zone) el.classList.remove("drop-ok"); });
    if (!zone) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (!zone.classList.contains("drop-ok")) {
      zone.classList.add("drop-ok");
      // Hovering a closed project opens it, so a file can be checked before letting go.
      clearTimeout(initSidebarEvents._open);
      const slug = zone.dataset.slug;
      if (slug && !expandedProjectFolders.has(slug)) {
        initSidebarEvents._open = setTimeout(() => {
          if (sidebarDrag) { expandedProjectFolders.add(slug); renderProjectsSidebar(); }
        }, 800);
      }
    }
  });

  tree.addEventListener("dragleave", (e) => {
    const zone = e.target.closest("[data-drop]");
    if (zone && !zone.contains(e.relatedTarget)) zone.classList.remove("drop-ok");
  });

  tree.addEventListener("drop", (e) => {
    const zone = dropTargetFor(e.target);
    const drag = sidebarDrag;
    if (!zone || !drag) return;
    e.preventDefault();
    zone.classList.remove("drop-ok");
    const to = zone.dataset.drop === "chats" ? "" : zone.dataset.slug;
    if (drag.kind === "chat") moveChatToProject(drag.id, to);
    else moveFileToProject(drag.slug, drag.subfolder, drag.filename, to);
  });
}

document.addEventListener("DOMContentLoaded", initSidebarEvents);
