// Job search results as a card in the chat (Apply / Open per row, pick several, quick filters), and
// "your job profile": the details and résumé Xod uses to fill applications. Applications run as tasks
// that stop for your OK before submitting. Backend: services/job_hunt.py, agent/jobs.py.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = async (url, body) => (await fetch(url, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
  const age = (d) => d == null ? "" : d < 1 ? "today" : d < 2 ? "yesterday" : d < 60 ? `${Math.floor(d)}d ago` : `${Math.floor(d / 30)}mo ago`;
  const isRemote = (j) => j.remote || /remote/i.test(j.location || "");
  let pendingApply = null;           // what to apply to once the profile is filled in
  // Starting applications writes answers for each job first (services/job_tailor.py): say so while it does.
  const startApplying = (ids) => { if (typeof showToast === "function") showToast(`Writing answers for ${ids.length === 1 ? "this job" : `${ids.length} jobs`} from your résumé…`); return api("/api/jobs/apply", { ids }); };

  // ------------------------------------------------------------------ the card
  function card(ev) {
    const el = document.createElement("div");
    el.className = "jobs-card";
    const S = { remote: false, hideSenior: false, q: "", shown: 12, picked: new Set(), applied: new Set() };
    const all = ev.jobs || [];
    const draw = () => {
      let jobs = all.filter((j) => (!S.remote || isRemote(j)) && (!S.hideSenior || !/\b(senior|staff|principal|lead|head|director|manager)\b/i.test(j.title)));
      if (S.q) { const q = S.q.toLowerCase(); jobs = jobs.filter((j) => `${j.company} ${j.title} ${j.location}`.toLowerCase().includes(q)); }
      el.innerHTML = `
        <div class="jc-head">
          <div><p class="jc-title"><i data-lucide="briefcase" class="size-4"></i>${esc(ev.role)}${ev.acronym ? ` <span>(${esc(ev.acronym)})</span>` : ""} · ${jobs.length} open</p>
            <p class="jc-sub">${ev.boards_checked ? `Checked live on ${ev.boards_checked} company job boards` : "From the practice careers site"}${(ev.narrowed || []).length ? ` · ${esc(ev.narrowed.join(", "))}` : ""}</p></div>
          <button class="jc-profile" data-j="profile"><i data-lucide="${ev.profile && ev.profile.ready ? "user-check" : "user-pen"}" class="size-3.5"></i>${ev.profile && ev.profile.ready ? "Your job profile" : "Add your details to apply"}</button>
        </div>
        <div class="jc-filters">
          <label><input type="checkbox" data-f="remote" ${S.remote ? "checked" : ""}> Remote only</label>
          <label><input type="checkbox" data-f="senior" ${S.hideSenior ? "checked" : ""}> Hide senior / lead</label>
          <input type="search" data-f="q" placeholder="Filter by company, title or city" value="${esc(S.q)}">
        </div>
        <div class="jc-list">${jobs.slice(0, S.shown).map((j) => `
          <div class="jc-row ${S.applied.has(j.id) ? "is-applied" : ""}">
            <input type="checkbox" data-pick="${esc(j.id)}" ${S.picked.has(j.id) ? "checked" : ""} ${S.applied.has(j.id) ? "disabled" : ""} aria-label="Select">
            <span class="jc-logo">${esc((j.company || "?").slice(0, 1).toUpperCase())}</span>
            <div class="jc-main"><p class="jc-role">${esc(j.title)}${j.practice ? ` <span class="jc-practice" title="A made-up job on the practice careers site (demo/careers): nothing is sent to a real company">Practice</span>` : ""}</p>
              <p class="jc-meta"><b>${esc(j.company)}</b> · ${esc((j.location || "").slice(0, 60))}${j.workplace && !new RegExp(j.workplace, "i").test(j.location || "") ? ` · ${esc(j.workplace.toLowerCase())}` : isRemote(j) && !/remote/i.test(j.location || "") ? " · remote" : ""}${j.age_days != null ? ` · ${age(j.age_days)}` : ""}${j.pay ? ` · <span class="jc-pay">${esc(j.pay.slice(0, 44))}</span>` : ""}${j.fit ? ` · fit ${j.fit}%` : ""}</p></div>
            <a class="jc-btn" href="${esc(j.url)}" target="_blank" rel="noopener noreferrer">Open</a>
            <button class="jc-btn jc-apply" data-apply="${esc(j.id)}" ${S.applied.has(j.id) ? "disabled" : ""}>${S.applied.has(j.id) ? "In Tasks" : "Apply"}</button>
          </div>`).join("") || `<p class="jc-empty">Nothing matches those filters.</p>`}</div>
        <div class="jc-foot">
          ${jobs.length > S.shown ? `<button data-j="more">Show ${Math.min(12, jobs.length - S.shown)} more</button>` : "<span></span>"}
          <button class="jc-main-btn" data-j="apply-picked" ${S.picked.size ? "" : "disabled"}>Apply to ${S.picked.size || ""} selected</button>
        </div>
        <p class="jc-note">Applying runs in Tasks: Xod fills the form from your profile and résumé and stops for your OK before each submit.</p>`;
      if (window.lucide) lucide.createIcons();
    };
    el.addEventListener("change", (e) => {
      const f = e.target.dataset.f;
      if (f === "remote") S.remote = e.target.checked;
      if (f === "senior") S.hideSenior = e.target.checked;
      const p = e.target.dataset.pick;
      if (p) { e.target.checked ? S.picked.add(p) : S.picked.delete(p); }
      if (f || p) draw();
    });
    el.addEventListener("input", (e) => {
      if (e.target.dataset.f !== "q") return;
      S.q = e.target.value;
      clearTimeout(S.t);
      S.t = setTimeout(() => { draw(); const i = el.querySelector("[data-f=q]"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }, 250);
    });
    el.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-j], [data-apply]");
      if (!b) return;
      if (b.dataset.j === "profile") return openProfile();
      if (b.dataset.j === "more") { S.shown += 12; return draw(); }
      const ids = b.dataset.apply ? [b.dataset.apply] : [...S.picked];
      if (!ids.length) return;
      b.disabled = true;
      const r = await api("/api/jobs/profile").then((p) => p.status.ready ? startApplying(ids) : api("/api/jobs/apply", { ids })).finally(() => { b.disabled = false; });
      if (r.needs_profile) { pendingApply = { ids, after: (tasks) => { tasks.forEach((t) => S.applied.add(ids[tasks.indexOf(t)])); S.picked.clear(); draw(); } }; return openProfile(r.missing); }
      if (!r.ok) { if (typeof showToast === "function") showToast(r.error || "Couldn't start that."); return; }
      ids.forEach((id) => S.applied.add(id));
      S.picked.clear();
      draw();
      if (typeof showToast === "function") showToast(`Started ${r.tasks.length} application${r.tasks.length === 1 ? "" : "s"} in Tasks`);
      window.dispatchEvent(new CustomEvent("xod:build", { detail: { phase: "launch", title: "applications" } }));
    });
    draw();
    return el;
  }

  function attach(ev, wrapper) {
    if (!wrapper) return;
    const col = wrapper.lastElementChild || wrapper;
    const prose = col.querySelector(".prose-custom");
    col.insertBefore(card(ev), prose ? prose.nextSibling : null);
  }

  // ------------------------------------------------------------------ your job profile
  const FIELDS = [
    ["full_name", "Full name", "text"], ["email", "Email", "email"], ["phone", "Phone", "tel"], ["location", "Where you live", "text"],
    ["linkedin", "LinkedIn", "url"], ["github", "GitHub", "url"], ["portfolio", "Website / portfolio", "url"],
    ["work_authorization", "Work authorization", "text", "e.g. US citizen, green card, H-1B"], ["needs_sponsorship", "Need visa sponsorship?", "text", "yes / no"],
    ["years_of_experience", "Years of experience", "text"], ["desired_salary", "Desired salary", "text"], ["willing_to_relocate", "Willing to relocate?", "text", "yes / no / within the US"],
    ["short_bio", "A few lines about you", "area"], ["common_answers", "Answers to common questions", "area", "Why this company? Notice period? Anything you'd always say…"],
  ];
  let modal = null;
  const areaText = (v) => typeof v === "string" ? v : (v && typeof v === "object" && Object.keys(v).length) ? JSON.stringify(v) : "";
  // opts: {suggested: {field: value}, from: {field: where}, choices: [files], note} from the chat (agent/jobs.py)
  async function openProfile(missing, opts = {}) {
    const { profile } = await api("/api/jobs/profile");
    modal?.remove();
    modal = document.createElement("div");
    modal.className = "jp-wrap";
    const need = new Set(missing || []);
    modal.innerHTML = `<form class="jp" autocomplete="on">
      <div class="jp-head"><p class="jp-title"><i data-lucide="user-pen" class="size-4"></i>Your job profile</p><button type="button" data-x aria-label="Close"><i data-lucide="x" class="size-4"></i></button></div>
      <p class="jp-note">Xod fills applications with this. It stays in a private file on this computer, and every application still stops for your OK before it's sent.</p>
      <div class="jp-auto"><button type="button" data-auto><i data-lucide="wand-sparkles" class="size-3.5"></i>Fill it in for me</button>
        <span class="jp-auto-note">Finds your résumé on this computer or in Google Drive (when it's connected) and fills in the empty boxes for you to check.</span></div>
      <label class="jp-resume ${need.has("resume_path") ? "is-needed" : ""}"><span>Résumé (PDF or Word)</span>
        <div><input name="resume_path" value="${esc(profile.resume_path || "")}" placeholder="/Users/you/Documents/Resume.pdf"><button type="button" data-find>Find it</button></div>
        <div class="jp-found"></div></label>
      <div class="jp-grid">${FIELDS.map(([k, label, type, ph]) => `<label class="${need.has(k) ? "is-needed" : ""} ${type === "area" ? "jp-wide" : ""}"><span>${label}</span>${type === "area"
        ? `<textarea name="${k}" rows="3" placeholder="${esc(ph || "")}">${esc(areaText(profile[k]))}</textarea>`
        : `<input name="${k}" type="${type}" value="${esc(profile[k] || "")}" placeholder="${esc(ph || "")}">`}</label>`).join("")}</div>
      <div class="jp-foot"><button type="button" data-x>Cancel</button><button type="button" class="jp-all" data-all title="${pendingApply ? "Fill in what's missing from your résumé, save, and start the applications" : "Fill in what's missing from your résumé and save"}"><i data-lucide="sparkles" class="size-3.5"></i>Do it all</button><button type="submit" class="jp-save">${pendingApply ? "Save and apply" : "Save"}</button></div></form>`;
    document.body.appendChild(modal);
    if (window.lucide) lucide.createIcons();
    const form = modal.firstElementChild;
    const note = modal.querySelector(".jp-auto-note");
    const current = () => Object.fromEntries(new FormData(form).entries());
    // Puts suggestions into the empty boxes only, and says where each came from.
    const fill = (r) => {
      Object.entries(r.fields || {}).forEach(([k, v]) => {
        const el = form.querySelector(`[name=${k}]`);
        if (!el || el.value.trim()) return;
        el.value = v;
        const lab = el.closest("label");
        lab?.classList.remove("is-needed");
        lab?.classList.add("is-filled");
        if (lab && r.from && r.from[k]) lab.title = `From ${r.from[k]}`;
      });
      const box = modal.querySelector(".jp-found");
      box.innerHTML = (r.choices || []).length ? `<p>Which one is yours?</p>` + r.choices.map((f, i) => `<button type="button" data-choice="${i}"><i data-lucide="${f.source === "drive" ? "hard-drive" : "file-text"}" class="size-3"></i>${esc(f.name)}<em>${esc(f.source === "drive" ? "Google Drive" : (f.path || "").replace(/^\/Users\/[^/]+|^C:\\Users\\[^\\]+/i, "~"))}</em></button>`).join("") : "";
      box._choices = r.choices || [];
      if (r.note) { note.textContent = r.note; note.classList.add("is-done"); }
      if (window.lucide) lucide.createIcons();
    };
    const autofill = async (body = {}) => {
      const b = modal.querySelector("[data-auto]");
      b.disabled = true;
      note.textContent = "Looking for your résumé…";
      try { const r = await api("/api/jobs/profile/autofill", { current: current(), ...body }); fill(r); return r; }
      catch (e) { note.textContent = "Couldn't look just now. Fill it in by hand, or try again."; return null; }
      finally { b.disabled = false; }
    };
    if (opts.suggested && Object.keys(opts.suggested).length || (opts.choices || []).length) fill({ fields: opts.suggested, from: opts.from, choices: opts.choices, note: opts.note });
    else if (opts.note) note.textContent = opts.note;
    else if ((missing || []).length) autofill();     // opened because something's missing: look for it straight away
    modal.addEventListener("click", async (e) => {
      if (e.target === modal || e.target.closest("[data-x]")) { modal.remove(); pendingApply = null; return; }
      if (e.target.closest("[data-auto]")) return autofill();
      const ch = e.target.closest("[data-choice]");
      if (ch) { const f = modal.querySelector(".jp-found")._choices[+ch.dataset.choice]; return autofill({ pick: f }); }
      if (e.target.closest("[data-all]")) {
        const b = e.target.closest("[data-all]");
        b.disabled = true;
        const r = await autofill();
        b.disabled = false;
        if (r) form.requestSubmit();       // saves what's in the form now and starts the applications (or says what's missing)
        return;
      }
      if (e.target.closest("[data-find]")) {
        const box = modal.querySelector(".jp-found");
        box.textContent = "Looking…";
        const { files } = await api("/api/jobs/resumes");
        box.innerHTML = files.length ? files.map((f) => `<button type="button" data-pickfile="${esc(f.path)}"><i data-lucide="file-text" class="size-3"></i>${esc(f.name)}<em>${esc(f.path.replace(/^\/Users\/[^/]+/, "~"))}</em></button>`).join("")
          : "No files named résumé or CV found. Type the path instead.";
        if (window.lucide) lucide.createIcons();
      }
      const pf = e.target.closest("[data-pickfile]");
      if (pf) { form.resume_path.value = pf.dataset.pickfile; modal.querySelector(".jp-found").innerHTML = ""; }
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = Object.fromEntries(new FormData(form).entries());
      const r = await api("/api/jobs/profile", body);
      if (!r.status.ready) {
        const names = { full_name: "your name", email: "your email", resume_path: "a résumé file that exists" };
        if (typeof showToast === "function") showToast(`Still needed: ${r.status.missing.map((m) => names[m] || m).join(", ")}`);
        r.status.missing.forEach((m) => form.querySelector(`[name=${m}]`)?.closest("label")?.classList.add("is-needed"));
        return;
      }
      modal.remove();
      if (pendingApply) {
        const p = pendingApply;
        pendingApply = null;
        const res = p.ids ? await startApplying(p.ids) : null;
        if (res && res.ok) { p.after && p.after(res.tasks); if (typeof showToast === "function") showToast(`Started ${res.tasks.length} application${res.tasks.length === 1 ? "" : "s"} in Tasks`); }
        else if (p.prompt && typeof quickSendPrompt === "function") quickSendPrompt(p.prompt);
      } else if (typeof showToast === "function") showToast("Saved your job profile");
    });
  }

  // From the chat: "apply to 2 and 5" but the profile isn't ready -> the form, then the same request again.
  function onProfileNeeded(ev) {
    pendingApply = { ids: ev.ids || [] };
    openProfile(ev.missing, { suggested: ev.suggested, from: ev.from, choices: ev.choices, note: ev.note });
  }

  // "do it all" in the chat saved the profile and started them: the form (if it's open) is done.
  function onApplied(ev) {
    pendingApply = null;
    modal?.remove();
    window.dispatchEvent(new CustomEvent("xod:build", { detail: { phase: "launch", title: "applications" } }));
  }

  function fromHistory(m, wrapper) {
    const ev = (m.tool_events || []).filter((t) => t.type === "jobs").pop();
    if (ev) attach(ev, wrapper);
  }

  window.XodJobs = { attach, fromHistory, openProfile, onProfileNeeded, onApplied };
})();
