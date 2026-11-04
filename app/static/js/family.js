// Family: the Family Link screen. Lists the machines in the family
// registry (services/family_registry.py, GET /api/family) with their capabilities,
// and carries the "Message Tree" composer (POST /api/tree/message,
// services/tree_inbox.py): a note from here lands in the family tree-inbox
// for Tree, the family's Muse agent, to pick up.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = async (url) => (await fetch(url)).json();
  const post = async (url) => (await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json();
  let poll = null;
  let devPoll = null;
  let devFlow = null;   // {user_code, verification_uri, state, message, account} while a device sign-in runs

  const ago = (t) => {
    if (!t) return "not seen yet";
    const s = Math.round(Date.now() / 1000 - new Date(t).getTime() / 1000);
    if (isNaN(s)) return esc(t);
    return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
  };

  function memberRow(m) {
    const caps = (m.capabilities || []).map((c) =>
      `<span class="px-1.5 py-0.5 rounded-md bg-surface-700 text-zinc-300 text-[11px]">${esc(c)}</span>`).join("");
    return `
      <li class="task-card space-y-1.5">
        <div class="flex items-center gap-2">
          <span class="size-2 rounded-full bg-emerald-400"></span>
          <span class="text-white text-[13px] font-semibold">${esc(m.name)}</span>
          <span class="text-zinc-500 text-[11px]">${esc(m.platform)}</span>
          ${m.verified ? `<span class="px-1.5 py-0.5 rounded-md bg-emerald-900 text-emerald-300 text-[11px]">signature verified</span>` : `<span class="px-1.5 py-0.5 rounded-md bg-surface-700 text-zinc-400 text-[11px]">unsigned</span>`}
        </div>
        <p class="text-[12px] text-zinc-400">${m.endpoint ? `Tailnet <span class="font-mono text-zinc-200">${esc(m.endpoint)}</span> · ` : ""}last seen ${ago(m.last_seen)}</p>
        ${caps ? `<div class="flex flex-wrap gap-1">${caps}</div>` : `<p class="text-[12px] text-zinc-500">No capabilities listed.</p>`}
        ${m.note ? `<p class="text-[12px] text-zinc-500">${esc(m.note)}</p>` : ""}
      </li>`;
  }

  function otherRow(d) {
    return `
      <li class="flex items-center gap-2 text-[13px]">
        <span class="size-2 rounded-full bg-zinc-600"></span>
        <span class="text-zinc-300">${esc(d.name)}</span>
        <span class="text-zinc-500 text-[11px]">${esc(d.platform)} · ${esc(d.status)}</span>
      </li>`;
  }

  // SSO control surface (services/sso_control.py, GET /api/family/control):
  // who am I here, is my control legitimate, what still stands between a
  // fresh install and working. Rendered above the registry detail.
  function controlView(c) {
    if (!c || !c.account) return "";
    const a = c.account, g = c.gate || {}, reg = g.registry || {};
    const head = a.session_state === "signed_in" && a.provider === "github"
      ? `<span class="text-emerald-300">Signed in as ${esc(a.account || "")} (GitHub)</span>`
      : a.session_state === "signed_in"
        ? `<span class="text-amber-300">Signed in as ${esc(a.account || "")} — GitHub not proven yet</span>`
        : a.session_state === "unknown"
          ? `<span class="text-amber-300">Can't check sign-in right now</span>`
          : `<span class="text-zinc-300">Not signed in</span>`;
    const reasons = (g.reasons || []).map((x) => `<li>${esc(x)}</li>`).join("");
    const steps = (c.setup || []).map((s) =>
      `<li class="flex gap-2"><span>${s.state === "done" ? "✅" : s.state === "blocked" ? "⛔" : "▸"}</span><span><span class="text-zinc-200">${esc(s.label)}</span>${s.detail ? `<br><span class="text-zinc-500">${esc(s.detail)}</span>` : ""}</span></li>`).join("");
    const keys = (c.keys || []).map((k) =>
      `<span class="px-1.5 py-0.5 rounded-md ${k.saved ? "bg-emerald-900 text-emerald-300" : "bg-surface-700 text-zinc-400"} text-[11px]">${esc(k.name)}${k.saved ? " · saved" : " · not set"}</span>`).join("");
    const svc = Object.entries(c.service_secrets || {}).map(([name, v]) =>
      `<p class="text-[12px] text-zinc-400">Service secret ${esc(name)}: ${v.present ? "in this device's keystore (rides the family vault)" : "not in the vault yet — optional; device sign-in doesn't need it"}.</p>`).join("");
    const dev = devFlow ? `
      <div class="task-card space-y-1.5">
        <p class="task-title">GitHub device sign-in</p>
        ${devFlow.state === "pending" || devFlow.state === "slow_down" ? `
          <p class="text-[12px] text-zinc-400">Go to <span class="font-mono text-zinc-200">${esc(devFlow.verification_uri || "github.com/login/device")}</span> (your phone works) and enter:</p>
          <p class="font-mono text-lg text-white tracking-widest">${esc(devFlow.user_code || "")}</p>
          <p class="text-[12px] text-zinc-500">${esc(devFlow.message || "Waiting for the approval on GitHub…")}</p>`
        : `<p class="text-[12px] text-zinc-400">${esc(devFlow.message || devFlow.state)}${devFlow.account ? ` — ${esc(devFlow.account)}` : ""}</p>`}
      </div>` : "";
    return `
      <section class="space-y-3">
        <div class="task-card space-y-1.5">
          <p class="task-title">${esc(reg.family || a.family || "Your family")} · account</p>
          <p class="text-[13px]">${head}${a.account && a.session_state !== "signed_in" ? ` <span class="text-zinc-500">· family account ${esc(a.account)}</span>` : ""}</p>
          <p class="text-[12px] text-zinc-400">This device: ${reg.my_device_id ? esc(reg.my_device_id) : "no device id yet"} · ${reg.my_trusted ? "verified family member" : reg.revoked ? "removed from the family" : "not a verified member yet"}${a.github_configured === false ? " · GitHub web sign-in isn't switched on on your rdrive (device sign-in still works)" : ""}</p>
          ${g.allowed ? `<p class="text-[12px] text-emerald-300">Control is live: both checks pass — verified family member and a proven GitHub sign-in for this account.</p>`
            : `<p class="text-[12px] font-semibold text-amber-300">Control is paused until these pass:</p><ul class="text-[12px] text-zinc-400 list-disc pl-5 space-y-1">${reasons}</ul>`}
          ${a.session_state !== "signed_in" || a.provider !== "github" ? `<button data-sso-action="device-begin" class="px-2.5 py-1 rounded-lg bg-surface-700 text-zinc-100 text-[12px]">Sign in with GitHub (device code)</button>` : ""}
          ${c.recovery && c.recovery.needed ? `<p class="text-[12px] text-amber-300">${esc(c.recovery.message)}</p><button data-sso-action="vault-apply" class="px-2.5 py-1 rounded-lg bg-surface-700 text-zinc-100 text-[12px]">Apply the family vault</button>` : ""}
        </div>
        ${dev}
        ${steps ? `<div class="task-card space-y-1.5"><p class="task-title">Getting to working</p><ul class="text-[12px] space-y-1.5">${steps}</ul></div>` : ""}
        <div class="task-card space-y-1.5">
          <p class="task-title">Keys & providers</p>
          <div class="flex flex-wrap gap-1">${keys}</div>
          ${svc}
          <p class="text-[12px] text-zinc-500">Keys are edited in Settings; the family vault shares them between verified members only.</p>
        </div>
        <div class="task-card space-y-1.5">
          <p class="task-title">Approvals waiting</p>
          <p class="text-[12px] text-zinc-400">${(c.approvals && c.approvals.phone_pending) || 0} phone join ask(s) — decide them in the Phones panel. ${(c.approvals && c.approvals.joining) || 0} device(s) waiting for an approver.</p>
          <button data-sso-action="open-phones" class="px-2.5 py-1 rounded-lg bg-surface-700 text-zinc-100 text-[12px]">Open Phones</button>
        </div>
      </section>`;
  }

  function body(r) {
    if (!r || r.state === undefined) return `<p class="text-[12px] text-zinc-400">Could not load the family registry.</p>`;
    if (r.state === "missing") {
      return `
        <section class="space-y-3">
          <div class="task-card space-y-2">
            <p class="task-title">No family yet</p>
            <p class="text-[12px] text-zinc-400">${esc(r.message || "No family registry found yet.")}</p>
          </div>
        </section>`;
    }
    if (r.state === "invalid") {
      const problems = (r.problems || []).map((p) => `<li>${esc(p)}</li>`).join("");
      return `
        <section class="space-y-3">
          <div class="task-card space-y-2">
            <p class="task-title">The registry could not be read</p>
            <p class="text-[12px] text-zinc-400">${esc(r.message || "")}</p>
            ${problems ? `<ul class="text-[12px] text-amber-300 list-disc pl-5 space-y-1">${problems}</ul>` : ""}
          </div>
        </section>`;
    }
    const others = (r.devices || []).filter((d) => d.status !== "active");
    const problems = (r.problems || []).map((p) => `<li>${esc(p)}</li>`).join("");
    return `
      <section class="space-y-3">
        <div class="task-card space-y-1.5">
          <p class="task-title">Your machines, one family</p>
          <p class="text-[12px] text-zinc-400">This list comes from the family registry.
            ${r.interim_unsigned ? "Some records are still unsigned, so treat those as a directory of your machines, not proof of identity."
              : "Every member's record is signature-verified and covered by the family approval."}
            The family vault shares provider keys between verified members only; borrowed inference is signed the same way.</p>
          ${r.vault ? `<p class="text-[12px] text-zinc-400">Family vault: ${r.vault.state === "ok" ? `bundle v${esc(r.vault.version)} for ${(r.vault.recipients || []).length} member(s); this device has applied v${esc(r.vault.applied_version)}${r.vault.local_only ? " (pinned local-only)" : ""}` : "no bundle published yet"}.</p>` : ""}
        </div>
        <p class="text-[12px] font-semibold text-zinc-300">Family members</p>
        ${(r.members || []).length ? `<ul class="space-y-2">${r.members.map(memberRow).join("")}</ul>`
          : `<p class="text-[12px] text-zinc-500">No active members in the registry yet.</p>`}
        ${others.length ? `<p class="text-[12px] font-semibold text-zinc-300">Not active</p><ul class="space-y-1.5">${others.map(otherRow).join("")}</ul>` : ""}
        ${problems ? `<div class="task-card space-y-1"><p class="text-[12px] font-semibold text-amber-300">Some records were skipped</p><ul class="text-[12px] text-zinc-400 list-disc pl-5 space-y-1">${problems}</ul></div>` : ""}
      </section>`;
  }

  async function render() {
    const el = document.getElementById("familyBody");
    if (!el) return;
    let r = null, c = null;
    try { r = await api("/api/family"); } catch (e) { /* honest empty render below */ }
    try { c = await api("/api/family/control"); } catch (e) { /* control header just omits */ }
    el.innerHTML = controlView(c) + body(r);
  }

  async function deviceBegin() {
    try {
      const out = await post("/api/family/control/device/begin");
      devFlow = out;
      if (out.state === "pending") {
        clearInterval(devPoll);
        devPoll = setInterval(devicePoll, Math.max(5, out.interval || 5) * 1000);
      }
    } catch (e) { devFlow = { state: "failed", message: "Sign-in could not be started." }; }
    render();
  }

  async function devicePoll() {
    try {
      const out = await post("/api/family/control/device/poll");
      if (out.state === "pending") { devFlow = { ...devFlow, ...out }; return; }
      if (out.state === "slow_down") { devFlow = { ...devFlow, ...out }; clearInterval(devPoll); devPoll = setInterval(devicePoll, Math.max(5, out.interval || 10) * 1000); return; }
      clearInterval(devPoll);
      devFlow = out.state === "none" ? devFlow : out;
    } catch (e) { /* a missed poll is not a verdict; the next render tells the truth */ }
    render();
  }

  window.openFamilyPanel = () => {
    let root = document.getElementById("familyPanel");
    if (!root) {
      root = document.createElement("div");
      root.id = "familyPanel";
      root.className = "tasks-panel";
      root.innerHTML = `
        <div class="tasks-scrim" data-close-family></div>
        <aside class="tasks-drawer" role="dialog" aria-label="Family">
          <header class="px-5 pt-5 pb-3 flex items-start justify-between gap-3 border-b border-white/[0.06]">
            <div><h3 class="font-bold text-lg text-white">Family</h3>
              <p class="text-[12px] text-zinc-400 mt-0.5">Your machines and what each one offers.</p></div>
            <button data-close-family class="p-1 -mr-1 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700" title="Close"><i data-lucide="x" class="size-4"></i></button>
          </header>
          <div class="px-5 py-4 border-b border-white/[0.06] space-y-2">
            <p class="text-[12px] font-semibold text-zinc-300">Message Tree</p>
            <p class="text-[12px] text-zinc-500">Send a note straight to Tree, your Muse agent — it lands in the family inbox and Tree picks it up from there. No relaying it yourself.</p>
            <textarea id="treeText" rows="3" class="task-answer w-full" placeholder="What should Tree know?"></textarea>
            <div class="flex items-center gap-2">
              <select id="treeKind" class="rounded-lg bg-surface-700 text-zinc-200 text-[12px] px-2 py-1.5">
                <option value="note">Note</option>
                <option value="task">Task</option>
                <option value="result">Result</option>
              </select>
              <button id="treeSend" class="px-3 py-1.5 rounded-lg bg-emerald-900 text-emerald-200 text-[12px] font-semibold hover:bg-emerald-800">Send to Tree</button>
              <span id="treeState" class="text-[12px] text-zinc-500"></span>
            </div>
          </div>
          <div id="familyBody" class="flex-1 overflow-y-auto px-5 py-4 space-y-4"></div>
        </aside>`;
      document.body.appendChild(root);
      root.addEventListener("click", (e) => { if (e.target.closest("[data-close-family]")) window.closeFamilyPanel(); });
      root.querySelector("#treeSend").addEventListener("click", async () => {
        const textEl = root.querySelector("#treeText");
        const stateEl = root.querySelector("#treeState");
        const text = (textEl.value || "").trim();
        if (!text) { stateEl.textContent = "Write something first."; return; }
        stateEl.textContent = "Sending…";
        try {
          const r = await (await fetch("/api/tree/message", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ kind: root.querySelector("#treeKind").value, text }),
          })).json();
          if (r.success && r.state === "inbox") {
            stateEl.textContent = "Sent — Tree will pick it up from the family inbox.";
            textEl.value = "";
          } else if (r.success && r.state === "queued") {
            stateEl.textContent = "Queued on this machine — the family drive isn't reachable from here right now. It will drain when it is.";
            textEl.value = "";
          } else {
            stateEl.textContent = r.message || "Could not send.";
          }
        } catch (e) { stateEl.textContent = "Could not send — Xod's backend did not answer."; }
      });
      root.addEventListener("click", async (e) => {
        const btn = e.target.closest("[data-sso-action]");
        if (!btn) return;
        const action = btn.getAttribute("data-sso-action");
        if (action === "device-begin") deviceBegin();
        if (action === "vault-apply") { try { await post("/api/family/vault/apply"); } catch (err) { /* render shows the new truth */ } render(); }
        if (action === "open-phones") { window.closeFamilyPanel(); if (window.openPhonePanel) window.openPhonePanel(); }
      });
      if (window.lucide) lucide.createIcons();
    }
    root.classList.remove("hidden");
    requestAnimationFrame(() => root.classList.add("is-open"));
    render();
    clearInterval(poll);
    poll = setInterval(render, 15000);
  };
  window.closeFamilyPanel = () => {
    const root = document.getElementById("familyPanel");
    if (!root) return;
    clearInterval(poll);
    root.classList.remove("is-open");
    setTimeout(() => root.classList.add("hidden"), 250);
  };
})();
