// Family: the Family Link screen. Lists the machines in the family
// registry (services/family_registry.py, GET /api/family) with their capabilities,
// and carries the "Message Tree" composer (POST /api/tree/message,
// services/tree_inbox.py): a note from here lands in the family tree-inbox
// for Tree, the family's Muse agent, to pick up.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = async (url) => (await fetch(url)).json();
  const post = async (url, body = {}) => (await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
  const send = async (url, method, body) => (await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })).json();
  let poll = null;
  let devPoll = null;
  let devFlow = null;   // {user_code, verification_uri, state, message, account} while a device sign-in runs

  const ago = (t) => {
    if (!t) return "not seen yet";
    const s = Math.round(Date.now() / 1000 - new Date(t).getTime() / 1000);
    if (isNaN(s)) return esc(t);
    return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
  };

  // Phase 2: a family member is a device card - friendly name, ONE
  // status line, what it can do in plain words. Endpoints and other
  // plumbing stay off the card (they live in pairing/details flows).
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
        <p class="text-[12px] text-zinc-400">${esc(m.name)} - last seen ${ago(m.last_seen)}</p>
        ${m.endpoint ? `<details><summary class="text-[11px] text-zinc-500 cursor-pointer">Details</summary><p class="text-[11px] text-zinc-500 font-mono mt-0.5">${esc(m.endpoint)}</p></details>` : ""}
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
          ${a.device_flow_configured === false ? `<div class="flex flex-wrap items-center gap-1.5"><p class="text-[12px] text-zinc-400 w-full">GitHub sign-in needs Xod's GitHub app: at github.com/settings/applications/new make one, tick <b>Enable Device Flow</b>, then paste its Client ID here (it isn't a secret).</p><input id="ssoClientId" placeholder="Client ID" class="px-2 py-1 rounded-lg bg-surface-800 text-zinc-100 text-[12px] font-mono"><button data-sso-action="client-id-save" class="px-2.5 py-1 rounded-lg bg-surface-700 text-zinc-100 text-[12px]">Save</button></div>` : ""}
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

  // One-Click Muse Connect (services/muse_connect.py): one button
  // makes a signed offer carrying the capability manifest summary;
  // the card shows the link, the paste-able block and the visual
  // code, then the live state of every connected Muse, each with
  // a revoke button. All the words a downloader needs are on this
  // card; the machinery stays under it.
  let museOffer = null;   // the freshest offer view from a click this session

  function museConnectSection(mc) {
    const conns = (mc && mc.connections) || [];
    const connRows = conns.map((c) => `
      <div class="flex items-center gap-2 flex-wrap">
        <span class="size-2 rounded-full ${c.state === "live" ? "bg-emerald-400" : c.state === "revoked" ? "bg-zinc-600" : "bg-amber-400"}"></span>
        <span class="text-[12px] text-zinc-200 flex-1">Connected to ${esc((c.muse && c.muse.name) || "a Muse")}${c.state === "live" ? ". Rules are shared." : c.state === "accepted" ? " - finishing the first hello." : c.state === "revoked" ? " - connection ended." : ""}</span>
        ${c.state !== "revoked" ? `<button data-muse-revoke="${esc(c.offer_id)}" class="px-2.5 py-1 rounded-lg bg-surface-700 text-zinc-100 text-[12px]">Revoke</button>` : ""}
      </div>`).join("");
    const waiting = ((mc && mc.offers) || []).filter((o) => o.state === "offered");
    const offerView = museOffer ? `
      <p class="text-[12px] text-zinc-300">Paste this to your Muse. Check the code matches on both screens:</p>
      <p class="font-mono text-lg text-white tracking-widest">${esc(museOffer.code)}</p>
      <input readonly value="${esc(museOffer.link)}" class="w-full px-2 py-1 rounded-lg bg-surface-800 text-zinc-300 text-[11px] font-mono" onclick="this.select()">
      <textarea readonly rows="4" class="task-answer w-full font-mono text-[11px]" onclick="this.select()">${esc(museOffer.text_block)}</textarea>
      <div class="flex gap-2">
        <button data-muse-copy="link" class="px-2.5 py-1 rounded-lg bg-surface-700 text-zinc-100 text-[12px]">Copy link</button>
        <button data-muse-copy="block" class="px-2.5 py-1 rounded-lg bg-surface-700 text-zinc-100 text-[12px]">Copy block</button>
      </div>
      <p class="text-[12px] text-zinc-500">${esc(museOffer.summary)}</p>` : "";
    return `
      <section class="space-y-3">
        <div class="task-card space-y-2">
          <p class="task-title">Connect to Muse</p>
          <p class="text-[12px] text-zinc-400">Let your Muse send this Xod tasks and read back the receipts. Your approvals still apply.</p>
          ${connRows}
          ${waiting.length && !museOffer ? `<p class="text-[12px] text-zinc-400">Waiting for your Muse to accept. The offer also sits in the family inbox for a Muse that watches it.</p>` : ""}
          ${offerView}
          <button data-muse-action="offer" class="px-3 py-1.5 rounded-lg bg-brand-500 text-surface-900 hover:bg-brand-400 text-[12px] font-semibold">Connect to Muse</button>
        </div>
      </section>`;
  }

  // Auto-Connect (services/auto_connect.py): one button and Xod
  // finds the AIs already on this computer, connects the ones the
  // person is signed into, and shows each one's state in plain
  // words. Signed-out AIs say "sign in first"; nothing here ever
  // logs anyone in.
  function autoConnectSection(ac) {
    const rows = ((ac && ac.assistants) || []).map((a) => `
      <div class="flex items-center gap-2 flex-wrap">
        <span class="size-2 rounded-full ${a.state === "live" ? "bg-emerald-400" : a.state === "not_found" ? "bg-zinc-600" : "bg-amber-400"}"></span>
        <span class="text-[12px] text-zinc-200 flex-1">${esc(a.name)}: ${esc(a.display || a.state)}</span>
      </div>`).join("");
    return `
      <section class="space-y-3">
        <div class="task-card space-y-2">
          <p class="task-title">Connect my AIs</p>
          <p class="text-[12px] text-zinc-400">One click: Xod finds the AIs already on this computer and connects to the ones you are signed into. Your approvals still apply.</p>
          ${rows}
          <button data-auto-action="run" class="px-3 py-1.5 rounded-lg bg-brand-500 text-surface-900 hover:bg-brand-400 text-[12px] font-semibold">Connect my AIs</button>
        </div>
      </section>`;
  }

  // Phase 3 (simplicity redesign): connection requests surface as cards in
  // Family, in plain words - who is asking (friendly name + where), what it
  // can do and what it cannot do - with Allow / Block. Raw payloads and
  // addresses stay under each card's Details. The same calm paused line as
  // Messages appears here when the post office could not heal itself.
  const MW = window.MailWords || null;
  const famFriendly = (address) => {
    const addr = String(address || "").toLowerCase();
    const [local = "", domainRaw = ""] = addr.split("@");
    const label = domainRaw.split(":")[0].split(".")[0];
    let device = "";
    if (/macbook/.test(label)) device = "MacBook Pro";
    else if (/home-main|razer|blade/.test(label)) device = "Razer";
    else if (/iphone/.test(label)) device = "Your iPhone";
    else if (/pocophone|s25|android|phone/.test(label)) device = "Your phone";
    let name;
    if (local.startsWith("takeover-check-")) name = "A test agent";
    else if (local === "phone") name = "Your phone";
    else if (local === "iphone") name = "Your iPhone";
    else name = local ? local.replace(/[._-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : addr;
    if (!device || name === device) return name;
    const place = device.startsWith("Your ") ? `your ${device.slice(5)}` : `your ${device}`;
    return name.toLowerCase() === device.toLowerCase() ? name : `${name} on ${place}`;
  };

  function pausedLine(mailStatus) {
    const home = mailStatus && mailStatus.home;
    if (!(mailStatus && mailStatus.self_hosted && home && !home.running && home.paused)) return "";
    return `
      <div class="task-card flex items-center gap-2 flex-wrap">
        <p class="text-[12px] text-zinc-200 flex-1">Messages are paused.</p>
        <button data-family-fix="post" class="px-3 py-1.5 rounded-lg bg-brand-500 text-surface-900 hover:bg-brand-400 text-[12px] font-semibold">Fix it</button>
      </div>`;
  }

  function requestsSection(reqThreads) {
    if (!MW || !reqThreads || !reqThreads.length) return "";
    const groups = MW.groupThreadsBySender(reqThreads, "");
    if (!groups.length) return "";
    const cards = groups.map((g) => {
      const peer = g.key;
      const caps = MW.capabilitySet(MW.parseMachinePayload(g.last && g.last.text));
      const preview = MW.plainPreview(g.last && g.last.text, g.last && g.last.subject);
      return `
      <div class="task-card space-y-2" data-family-request="${esc(peer)}">
        <p class="text-[13px] text-white">${esc(famFriendly(peer))} wants to connect.</p>
        ${caps ? `${MW.canSentence(caps) ? `<p class="text-[12px] text-zinc-300">${esc(MW.canSentence(caps))}</p>` : ""}${MW.cannotSentence(caps) ? `<p class="text-[12px] text-zinc-300">${esc(MW.cannotSentence(caps))}</p>` : ""}`
          : `<p class="text-[12px] text-zinc-300">${esc(preview)}</p>`}
        <div class="flex gap-2">
          <button data-family-request-action="allow" class="px-3 py-1.5 rounded-lg bg-brand-500 text-surface-900 hover:bg-brand-400 text-[12px] font-semibold">Allow</button>
          <button data-family-request-action="block" class="px-3 py-1.5 rounded-lg font-medium text-zinc-300 hover:text-white hover:bg-surface-700 text-[12px]">Block</button>
        </div>
        <details><summary class="text-[11px] text-zinc-500 cursor-pointer">Details</summary><p class="text-[11px] text-zinc-500 font-mono mt-1">${esc(peer)}</p></details>
      </div>`;
    }).join("");
    return `
      <section class="space-y-2">
        <p class="text-[12px] font-semibold text-zinc-300">Requests waiting</p>
        ${cards}
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
    let r = null, c = null, mailStatus = null, reqThreads = [], mc = null, ac = null;
    try { r = await api("/api/family"); } catch (e) { /* honest empty render below */ }
    try { c = await api("/api/family/control"); } catch (e) { /* control header just omits */ }
    try { mailStatus = await api("/api/mail/status"); } catch (e) { /* Messages health line just omits */ }
    try { const t = await api("/api/mail/threads?box=requests"); reqThreads = t.threads || []; } catch (e) { /* request cards just omit */ }
    try { mc = await api("/api/muse-connect"); } catch (e) { /* connect card just omits */ }
    try { ac = await api("/api/auto-connect"); } catch (e) { /* auto-connect card just omits */ }
    el.innerHTML = pausedLine(mailStatus) + controlView(c) + museConnectSection(mc) + autoConnectSection(ac) + requestsSection(reqThreads) + body(r);
  }

  async function saveClientId() {
    const box = document.getElementById("ssoClientId");
    let out;
    try { out = await post("/api/family/control/github-client-id", { client_id: (box && box.value || "").trim() }); }
    catch (e) { out = { success: false, error: "Couldn't save it." }; }
    devFlow = out.success ? { state: "saved", message: "Saved. Sign in with GitHub now." } : { state: "failed", message: out.error };
    render();
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
            <button data-family-action="messages" class="surface-row" style="background:rgba(255,255,255,0.03)">
              <span class="flex-1 min-w-0"><span class="block text-zinc-100 text-[13px] font-semibold">Messages</span><span class="block text-[12px] text-zinc-500">Messages between your machines and agents (Agent Mail), real messages only.</span></span>
              <span class="text-zinc-500 text-[12px]">Open</span>
            </button>
            <div class="flex gap-2">
              <button data-family-action="xods" class="flex-1 px-3 py-2 rounded-lg bg-surface-700 hover:bg-surface-600 text-zinc-100 text-[12px]">Your Xods</button>
              <button data-family-action="phone" class="flex-1 px-3 py-2 rounded-lg bg-surface-700 hover:bg-surface-600 text-zinc-100 text-[12px]">Your phone</button>
            </div>
            <p class="text-[12px] font-semibold text-zinc-300 pt-1">Message Tree</p>
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
      root.addEventListener("click", (e) => {
        const b = e.target.closest("[data-family-action]");
        if (!b) return;
        const a = b.getAttribute("data-family-action");
        window.closeFamilyPanel();
        if (a === "messages" && window.openMailPanel) window.openMailPanel();
        if (a === "xods" && window.openXodsPanel) window.openXodsPanel();
        if (a === "phone" && window.openPhonePanel) window.openPhonePanel();
      });
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
        const fix = e.target.closest("[data-family-fix]");
        if (fix) {
          fix.disabled = true; fix.textContent = "Fixing…";
          try { await post("/api/mail/home/repair"); } catch (err) { /* render shows the new truth */ }
          render();
          return;
        }
        const reqBtn = e.target.closest("[data-family-request-action]");
        if (reqBtn) {
          const card = reqBtn.closest("[data-family-request]");
          const peer = card && card.getAttribute("data-family-request");
          if (peer) {
            try { await send(`/api/mail/contacts/${encodeURIComponent(peer)}`, "PATCH", { trust: reqBtn.getAttribute("data-family-request-action") === "allow" ? "known" : "blocked" }); } catch (err) { /* render shows the new truth */ }
          }
          render();
          return;
        }
        const btn = e.target.closest("[data-sso-action]");
        if (!btn) return;
        const action = btn.getAttribute("data-sso-action");
        if (action === "device-begin") deviceBegin();
        if (action === "client-id-save") saveClientId();
        if (action === "vault-apply") { try { await post("/api/family/vault/apply"); } catch (err) { /* render shows the new truth */ } render(); }
        if (action === "open-phones") { window.closeFamilyPanel(); if (window.openPhonePanel) window.openPhonePanel(); }
      });
      root.addEventListener("click", async (e) => {
        const copyBtn = e.target.closest("[data-muse-copy]");
        if (copyBtn && museOffer) {
          const text = copyBtn.getAttribute("data-muse-copy") === "link" ? museOffer.link : museOffer.text_block;
          try { await navigator.clipboard.writeText(text); copyBtn.textContent = "Copied"; setTimeout(() => { copyBtn.textContent = copyBtn.getAttribute("data-muse-copy") === "link" ? "Copy link" : "Copy block"; }, 1500); } catch (err) { /* the field above is selectable by hand */ }
          return;
        }
        const revBtn = e.target.closest("[data-muse-revoke]");
        if (revBtn) {
          try { await post("/api/muse-connect/revoke", { offer_id: revBtn.getAttribute("data-muse-revoke") }); } catch (err) { /* render shows the new truth */ }
          render();
          return;
        }
        const autoBtn = e.target.closest("[data-auto-action]");
        if (autoBtn) {
          autoBtn.disabled = true; autoBtn.textContent = "Connecting...";
          try { await post("/api/auto-connect/run", {}); } catch (err) { /* render shows the new truth */ }
          render();
          return;
        }
        const btn = e.target.closest("[data-muse-action]");
        if (!btn) return;
        btn.disabled = true; btn.textContent = "Making your offer...";
        try {
          const out = await post("/api/muse-connect/offer");
          if (out && out.offer) {
            const code = out.offer.code || "";
            museOffer = {
              link: out.link || "",
              text_block: out.text_block || "",
              code: code.length === 8 ? `${code.slice(0, 4)} ${code.slice(4)}` : code,
              summary: (out.offer.manifest && out.offer.manifest.summary) || "",
            };
          }
        } catch (err) { /* render shows the new truth */ }
        render();
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
