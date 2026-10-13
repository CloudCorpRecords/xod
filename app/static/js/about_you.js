// What Xod knows about you (services/user_memory.py): the fact sheet in Settings (edit, forget, add, switch
// learning off), "Get to know me from this Mac" (pick sources, see every fact before it's kept), and the little
// "Remembered … · Undo" note under an answer when Xod picked up something about you.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const TOPIC = { identity: "You", work: "Work", location: "Where", people: "People", projects: "Projects",
                  interests: "Interests", preferences: "Preferences", routines: "Routines", other: "Other" };
  let data = null;
  let proposals = null;

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
    return res.json();
  }

  async function load() {
    try { data = await api("/api/about-you"); } catch (e) { data = null; }
    render();
  }

  function factRow(f) {
    return `<li class="xa-fact" data-id="${esc(f.id)}">
      <span class="xa-text" title="${esc(f.source || "")}">${esc(f.text)}</span>
      <span class="xa-src">${esc(f.source || "")}</span>
      <button class="xa-btn" data-a="edit" title="Edit"><i data-lucide="pencil" class="size-3"></i></button>
      <button class="xa-btn" data-a="forget" title="Forget this"><i data-lucide="x" class="size-3.5"></i></button>
    </li>`;
  }

  function render() {
    const host = document.getElementById("aboutYouSection");
    if (!host) return;
    if (!data) { host.innerHTML = ""; return; }
    const groups = {};
    for (const f of data.facts) (groups[f.topic] = groups[f.topic] || []).push(f);
    const list = Object.keys(TOPIC).filter((t) => groups[t]).map((t) =>
      `<div class="xa-group"><div class="xa-topic">${TOPIC[t]}</div><ul>${groups[t].map(factRow).join("")}</ul></div>`).join("");
    host.innerHTML = `
      <div class="space-y-3 rounded-xl bg-surface-750 p-4 border border-surface-700">
        <div class="flex items-center justify-between gap-3">
          <label class="text-xs font-semibold text-zinc-200 flex items-center gap-1.5">
            <i data-lucide="brain" class="size-4 text-brand-400"></i> What Xod knows about you
          </label>
          <span class="text-[11px] text-zinc-500">Kept on this Mac. Edit or forget anything.</span>
        </div>
        ${data.facts.length ? `<div class="xa-list">${list}</div>`
          : `<p class="text-xs text-zinc-400">Nothing yet. Tell Xod about yourself in a chat ("I'm a nurse in Austin"), add a fact below, or let it get to know you from this Mac.</p>`}
        <div class="flex gap-2">
          <input id="xaNew" class="flex-1 bg-surface-900 border border-surface-650 rounded-lg px-2.5 py-1.5 text-xs text-zinc-100 focus:outline-none focus:border-brand-500" placeholder="Add something, e.g. I prefer short answers" maxlength="240">
          <button class="xa-pill" data-a="add">Add</button>
        </div>
        <div class="flex flex-wrap items-center gap-2 pt-1">
          <button class="xa-cta" data-a="gather"><i data-lucide="sparkles" class="size-3.5"></i> Get to know me from this Mac…</button>
          <label class="text-[11px] text-zinc-400 flex items-center gap-1.5 ml-auto">
            <input type="checkbox" data-a="learn" ${data.settings.learn ? "checked" : ""}> Learn from our chats
          </label>
          ${data.facts.length ? `<button class="text-[11px] text-zinc-500 hover:text-rose-300" data-a="forget-all">Forget everything</button>` : ""}
        </div>
        <div id="xaGather"></div>
      </div>`;
    if (proposals) renderGather();
    if (window.lucide) lucide.createIcons();
  }

  function renderSources() {
    const box = document.getElementById("xaGather");
    if (!box) return;
    box.innerHTML = `<div class="xa-panel">
      <p class="text-xs text-zinc-300">Pick what Xod may look at. It reads these once, on this Mac, and shows you every fact before keeping any.</p>
      ${data.sources.map((s) => `<label class="xa-src-row">
        <input type="checkbox" value="${esc(s.id)}" ${s.id === "contacts" ? "" : "checked"}>
        <span><b>${esc(s.label)}</b><br><span class="text-zinc-400">${esc(s.reads)}${s.asks ? ` · ${esc(s.asks)}` : ""}${s.model ? " · your AI model reads the list to sum it up" : ""}</span></span>
      </label>`).join("")}
      <div class="flex gap-2 justify-end pt-1">
        <button class="text-[12px] text-zinc-400 hover:text-white" data-a="gather-cancel">Cancel</button>
        <button class="xa-pill xa-go" data-a="gather-go">Look</button>
      </div></div>`;
  }

  function renderGather() {
    const box = document.getElementById("xaGather");
    if (!box) return;
    const p = proposals;
    const errs = Object.entries(p.errors || {}).map(([k, v]) => `<li>${esc((data.sources.find((s) => s.id === k) || {}).label || k)}: ${esc(v)}</li>`).join("");
    box.innerHTML = `<div class="xa-panel">
      ${p.proposals.length ? `<p class="text-xs text-zinc-300">Here's what Xod found. Untick anything you'd rather it didn't keep.</p>
        <ul class="space-y-1">${p.proposals.map((x, i) => `<li><label class="xa-src-row">
          <input type="checkbox" data-i="${i}" ${x.known ? "" : "checked"}>
          <span>${esc(x.text)} <span class="text-zinc-500">· from ${esc(x.source_label)}${x.known ? " · already known" : ""}${x.replaces ? ` · replaces “${esc(x.replaces)}”` : ""}</span></span>
        </label></li>`).join("")}</ul>` : `<p class="text-xs text-zinc-400">Nothing new turned up.</p>`}
      ${errs ? `<ul class="text-[11px] text-amber-300/90 list-disc pl-4">${errs}</ul>` : ""}
      <div class="flex gap-2 justify-end pt-1">
        <button class="text-[12px] text-zinc-400 hover:text-white" data-a="gather-cancel">${p.proposals.length ? "Cancel" : "Close"}</button>
        ${p.proposals.length ? `<button class="xa-pill xa-go" data-a="keep">Keep these</button>` : ""}
      </div></div>`;
  }

  async function act(a, el) {
    const li = el.closest("[data-id]");
    try {
      if (a === "forget" && li) {
        await api(`/api/about-you/fact/${encodeURIComponent(li.dataset.id)}`, { method: "DELETE" });
        return load();
      }
      if (a === "edit" && li) {
        const span = li.querySelector(".xa-text");
        const text = prompt("Edit what Xod knows:", span.textContent);
        if (text && text.trim() && text.trim() !== span.textContent) {
          await api(`/api/about-you/fact/${encodeURIComponent(li.dataset.id)}`, { method: "PUT", body: JSON.stringify({ text }) });
          load();
        }
        return;
      }
      if (a === "add") {
        const inp = document.getElementById("xaNew");
        if (!inp.value.trim()) return;
        await api("/api/about-you/fact", { method: "POST", body: JSON.stringify({ text: inp.value }) });
        inp.value = "";
        return load();
      }
      if (a === "forget-all") {
        if (!confirm("Forget everything Xod knows about you? This can't be undone.")) return;
        await api("/api/about-you", { method: "DELETE" });
        return load();
      }
      if (a === "gather") { proposals = null; return renderSources(); }
      if (a === "gather-cancel") { proposals = null; document.getElementById("xaGather").innerHTML = ""; return; }
      if (a === "gather-go") {
        const ids = [...document.querySelectorAll("#xaGather input[type=checkbox]:checked")].map((c) => c.value);
        if (!ids.length) return;
        el.disabled = true;
        el.innerHTML = '<i data-lucide="loader-2" class="size-3.5 animate-spin"></i> Looking…';
        if (window.lucide) lucide.createIcons();
        proposals = await api("/api/about-you/gather", { method: "POST", body: JSON.stringify({ sources: ids }) });
        return renderGather();
      }
      if (a === "keep") {
        const picked = [...document.querySelectorAll("#xaGather input[data-i]:checked")].map((c) => proposals.proposals[+c.dataset.i]);
        await api("/api/about-you/keep", { method: "POST", body: JSON.stringify({ proposals: picked }) });
        proposals = null;
        if (window.showToast) showToast(`Kept ${picked.length} thing${picked.length === 1 ? "" : "s"} about you`);
        return load();
      }
    } catch (e) {
      if (window.showToast) showToast(`Couldn't do that: ${e.message}`); else alert(e.message);
      if (a === "gather-go") renderSources();
    }
  }

  document.addEventListener("click", (e) => {
    const b = e.target.closest("#aboutYouSection [data-a]");
    if (b && b.tagName !== "INPUT") act(b.dataset.a, b);
    const u = e.target.closest("[data-mem-undo]");
    if (u) {
      const ids = u.dataset.memUndo.split(",");
      Promise.all(ids.map((id) => api(`/api/about-you/fact/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => {})))
        .then(() => { u.closest(".xa-note").innerHTML = '<i data-lucide="check" class="size-3"></i> Forgotten'; if (window.lucide) lucide.createIcons(); });
    }
  });
  document.addEventListener("change", (e) => {
    if (e.target.matches("#aboutYouSection [data-a=learn]")) api("/api/about-you/settings", { method: "POST", body: JSON.stringify({ learn: e.target.checked }) });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.id === "xaNew") { e.preventDefault(); act("add", e.target); }
  });

  // Settings opened: refresh the sheet.
  const open = window.openSettingsModal;
  if (typeof open === "function") {
    window.openSettingsModal = function (...args) { const r = open.apply(this, args); load(); return r; };
  }

  // In a chat: Xod picked something up (or forgot something) — say so under the answer, with Undo.
  window.XodAboutYou = {
    reload: load,
    // The answer is done but Xod is still working out what to remember: check back for up to ~40 s.
    async follow(turn, container) {
      for (const wait of [2000, 3000, 4000, 5000, 6000, 8000, 12000]) {
        await new Promise((r) => setTimeout(r, wait));
        let r;
        try { r = await api(`/api/about-you/turn/${encodeURIComponent(turn)}`); } catch (e) { return; }
        if (r.done) return this.note(r, container);
      }
    },
    note(ev, container) {
      if (!container || (!ev.learned?.length && !ev.forgot?.length)) return;
      const el = document.createElement("div");
      el.className = "xa-note";
      if (ev.learned?.length) {
        el.innerHTML = `<i data-lucide="brain" class="size-3"></i> <span>Remembered: ${ev.learned.map((f) => esc(f.text)).join(" · ")}</span>
          <button data-mem-undo="${esc(ev.learned.map((f) => f.id).join(","))}">Undo</button>`;
      } else {
        el.innerHTML = `<i data-lucide="eraser" class="size-3"></i> <span>Forgot: ${ev.forgot.map(esc).join(" · ")}</span>`;
      }
      container.appendChild(el);
      if (window.lucide) lucide.createIcons();
    },
  };
})();
