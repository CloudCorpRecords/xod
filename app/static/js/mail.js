// Agent Mail: an address for this Xod, and end-to-end encrypted messages with other AI agents.
// Backend: services/agent_mail.py. Everything another agent writes is shown as plain text (escaped).
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = async (url, method = "GET", body) =>
    (await fetch(url, { method, headers: body !== undefined ? { "Content-Type": "application/json" } : {}, body: body !== undefined ? JSON.stringify(body) : undefined })).json();
  const when = (t) => {
    if (!t) return "";
    const d = new Date(t * 1000), now = new Date();
    return d.toDateString() === now.toDateString() ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
      : d.toLocaleDateString([], { month: "short", day: "numeric" });
  };
  const btn = "px-3 py-1.5 rounded-lg text-[12px] font-semibold";
  const primary = `${btn} bg-brand-500 text-surface-900 hover:bg-brand-400`;
  const quiet = `${btn} font-medium text-zinc-300 hover:text-white hover:bg-surface-700`;
  const field = "w-full bg-surface-900 border border-surface-700 rounded-lg px-2.5 py-1.5 text-[13px] text-white focus:outline-none focus:border-brand-500";


  // Phase 0 (simplicity redesign): senders show friendly names, never raw
  // tailnet addresses. Sources, in order: the nickname saved on the contact,
  // the family registry's name for that machine, then plain rules for the
  // user's own devices (MacBook Pro, Razer, Your iPhone, Your phone), then
  // the name part of the address on its own. The raw address stays available
  // as a hover title and in the Contacts view, where it is the detail.
  let contactNames = {};
  let familyNames = {};
  const humanDeviceName = (name) => {
    const slug = String(name || "").toLowerCase();
    if (/macbook/.test(slug)) return "MacBook Pro";
    if (/rene-home-main|razer|blade/.test(slug)) return "Razer";
    if (/iphone/.test(slug)) return "Your iPhone";
    if (/pocophone|s25|android|phone/.test(slug)) return "Your phone";
    return String(name || "").replace(/[-_]+/g, " ").trim();
  };
  function friendlySender(address) {
    const raw = String(address ?? "");
    const addr = raw.trim().toLowerCase();
    if (!addr) return raw;
    if (contactNames[addr]) return contactNames[addr];
    const [local = "", domainRaw = ""] = addr.split("@");
    const host = domainRaw.split(":")[0];
    const hostLabel = host.split(".")[0];
    if (familyNames[host]) return humanDeviceName(familyNames[host]);
    if (familyNames[hostLabel]) return humanDeviceName(familyNames[hostLabel]);
    if (/macbook/.test(hostLabel)) return "MacBook Pro";
    if (/rene-home-main|razer|blade/.test(hostLabel)) return "Razer";
    if (/iphone/.test(hostLabel) || local === "iphone") return "Your iPhone";
    if (local === "phone" || /pocophone|s25|android/.test(hostLabel)) return "Your phone";
    if (local.startsWith("takeover-check-")) return "A test agent";
    if (local) return local.replace(/[._-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    return raw;
  }
  const friendlyList = (addrs) => String(addrs ?? "").split(",").map((a) => friendlySender(a.trim())).join(", ");

  // Phase 3 (simplicity redesign): plain words for machines. MailWords
  // (mail_words.js) knows machine payloads from human words and turns a
  // capability set into sentences; if it failed to load, Messages falls
  // back to the Phase 0/2 rendering rather than breaking.
  const MW = window.MailWords || null;
  // The device an address lives on, in friendly words ("MacBook Pro",
  // "Razer", "Your iPhone"), from the address's host part - or "" when
  // the host isn't one of the user's own devices (a shared post office).
  function deviceFromAddress(address) {
    const host = (String(address ?? "").split("@")[1] || "").split(":")[0];
    const label = host.split(".")[0];
    if (familyNames[host]) return humanDeviceName(familyNames[host]);
    if (familyNames[label]) return humanDeviceName(familyNames[label]);
    if (/macbook/.test(label)) return "MacBook Pro";
    if (/rene-home-main|razer|blade/.test(label)) return "Razer";
    if (/iphone/.test(label)) return "Your iPhone";
    if (/pocophone|s25|android|phone/.test(label)) return "Your phone";
    return "";
  }
  // "A test agent on your MacBook Pro" - who is asking, in plain words:
  // the agent's own name (from the address's name part or its contact
  // nickname), plus where it lives when that's one of your devices.
  function requesterIntro(address) {
    const peer = String(address ?? "").split(",")[0].trim().toLowerCase();
    const local = peer.split("@")[0];
    let name;
    if (contactNames[peer]) name = contactNames[peer];
    else if (local.startsWith("takeover-check-")) name = "A test agent";
    else if (local) name = local.replace(/[._-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    else name = friendlySender(peer);
    const device = deviceFromAddress(peer);
    if (!device || name === device) return name;
    const place = device.startsWith("Your ") ? `your ${device.slice(5)}` : `your ${device}`;
    return name.toLowerCase() === device.toLowerCase() ? name : `${name} on ${place}`;
  }
  // The first capability set any message in the thread carries (a hello
  // payload), or null when the agent never shared one.
  function threadCapabilities(messages) {
    if (!MW) return null;
    for (const m of messages || []) {
      const caps = MW.capabilitySet(MW.parseMachinePayload(m.text));
      if (caps) return caps;
    }
    return null;
  }
  async function loadNameMaps() {
    try {
      const { contacts } = await api("/api/mail/contacts");
      contactNames = {};
      for (const c of contacts || []) if (c.nickname) contactNames[String(c.address).toLowerCase()] = c.nickname;
    } catch (e) {}
    try {
      const fam = await api("/api/family");
      familyNames = {};
      for (const m of fam.members || fam.devices || []) {
        if (!m || !m.name) continue;
        const ep = String(m.endpoint || "").replace(/^https?:\/\//, "").split("/")[0];
        if (ep) familyNames[ep.toLowerCase()] = m.name;
        familyNames[String(m.name).toLowerCase()] = m.name;
      }
    } catch (e) {}
  }

  let view = { box: "inbox", thread: null, compose: null, sender: null };
  let st = null;

  // ---------------------------------------------------------------- header badge
  async function refreshBadge() {
    try {
      st = await api("/api/mail/status");
      const n = (st.unread || 0) + (st.requests || 0) + (st.drafts || 0);
      const b = document.getElementById("mailBadge");
      if (b) { b.textContent = n; b.classList.toggle("hidden", !n); }
    } catch (e) {}
  }
  setInterval(refreshBadge, 30000);
  setTimeout(refreshBadge, 1500);

  // ---------------------------------------------------------------- views
  function setupView() {
    return `
      <section class="space-y-3">
        <div class="task-card space-y-2">
          <p class="task-title">Give this Xod an address</p>
          <p class="text-[12px] text-zinc-400">Like email, but for AI agents. Other Xods can message yours, ask it for help, and you can connect several agents (yours or friends') together.</p>
          <ul class="text-[12px] text-zinc-400 space-y-1 list-disc pl-4">
            <li>Messages are end-to-end encrypted. Whatever carries them can't read them or see who sent them.</li>
            <li>No server needed: this computer can be its own post office. Messages, contacts and your keys stay here (keys in your Keychain).</li>
            <li>Xod never sends or runs anything from mail without your OK.</li>
          </ul>
        </div>
        <label class="block text-[12px] text-zinc-400">Your name
          <input id="mlName" class="${field} mt-1" placeholder="e.g. sam" autocomplete="off" spellcheck="false"></label>
        <fieldset class="space-y-1.5 text-[12px] text-zinc-300">
          <label class="flex items-start gap-2"><input type="radio" name="mlWhere" value="home" ${st?.default_relay ? "" : "checked"} class="mt-0.5">
            <span>Host it on this computer <span class="text-zinc-500">(no server: other agents reach this computer directly)</span></span></label>
          <div id="mlNets" class="pl-6 space-y-1 text-[12px] text-zinc-400">Looking at this computer's networks…</div>
          <label class="flex items-start gap-2"><input type="radio" name="mlWhere" value="relay" ${st?.default_relay ? "checked" : ""} class="mt-0.5">
            <span>Use a shared post office <span class="text-zinc-500">(a relay someone runs: works when this computer is off)</span></span></label>
          <input id="mlRelay" class="${field} ml-6" value="${esc(st?.default_relay || "")}" placeholder="post.example.com" autocomplete="off" spellcheck="false">
        </fieldset>
        <p class="text-[11px] text-zinc-500">Your address will be <span class="font-mono" id="mlPreview">name@relay</span>. Anyone can run a shared post office too: see relay/README.md in Xod's repository.</p>
        <button data-ml="setup" class="${primary}">Create my address</button>
        <p id="mlSetupErr" class="text-[12px] text-red-300"></p>
      </section>`;
  }

  // Phase 3: the header is calm. The post office heals itself, so a stopped
  // one shows NOTHING while Xod restarts it; only a genuinely failed
  // recovery earns one line - "Messages are paused." - plus a Fix it
  // button. Addresses, ports and error text live under Details, and the
  // safety number no longer lives here at all: it belongs to the pairing
  // moment, on both devices, not to an idle surface.
  function headerCard() {
    const home = st.home;
    const paused = Boolean(st.self_hosted && home && !home.running && home.paused);
    const detailBits = [
      `<p class="text-[11px] text-zinc-500 font-mono mt-1" title="Your full agent address, for sharing">${esc(st.address)}</p>`,
      st.self_hosted && home ? `<p class="text-[11px] text-zinc-500 mt-1">Post office: ${esc(home.domain || "")} · port ${esc(home.port)}</p>` : "",
      home && home.last_error ? `<p class="text-[11px] text-zinc-500 mt-1">${esc(home.last_error)}</p>` : "",
      st.last_error ? `<p class="text-[11px] text-zinc-500 mt-1">${esc(st.last_error)}</p>` : "",
    ].join("");
    return `
      <div class="task-card space-y-1.5">
        <div class="flex items-center justify-between gap-2">
          <p class="text-[13px] text-white truncate" title="Your agent address">${esc(friendlySender(st.address))}</p>
          <button data-ml="copy" class="${quiet} px-2 py-1">Copy</button>
        </div>
        ${paused ? `<div class="flex items-center gap-2 flex-wrap"><p class="text-[12px] text-zinc-200">Messages are paused.</p><button data-ml="fix-post" class="${primary} py-1">Fix it</button></div>` : ""}
        ${st.self_hosted && home && home.running ? `<p class="text-[11px] text-emerald-300">Hosted on this computer: ${/\.ts\.net/.test(st.address) ? "reachable on your Tailscale network" : "reachable on this network"} while it's on.</p>` : ""}
        ${!paused && st.last_error ? `<p class="text-[11px] text-zinc-500">Messages couldn't be checked just now. Xod keeps trying.</p>` : `<p class="text-[11px] text-zinc-500">Checked ${esc(when(st.last_check)) || "never"} · checks every 30 seconds</p>`}
        ${st.outbox ? `<p class="text-[11px] text-zinc-400">${st.outbox} waiting in your outbox (the other side wasn't reachable): Xod keeps trying. <button data-ml="retry" class="underline">Try now</button></p>` : ""}
        <details><summary class="text-[11px] text-zinc-500 cursor-pointer">Details</summary>${detailBits}</details>
      </div>`;
  }

  function tabs() {
    const t = [["inbox", "Inbox", st.unread], ["requests", "Requests", st.requests], ["drafts", "Waiting for OK", st.drafts],
               ["contacts", "Contacts", 0]];
    if (st.held) t.push(["held", "Held", st.held]);
    return `<div class="flex gap-1 flex-wrap">${t.map(([k, label, n]) => `
      <button data-ml-box="${k}" class="px-2.5 py-1 rounded-lg text-[12px] ${view.box === k ? "bg-surface-600 text-white" : "text-zinc-400 hover:text-white hover:bg-surface-700"}">${label}${n ? ` <span class="text-brand-300">${n}</span>` : ""}</button>`).join("")}
      <button data-ml="compose" class="ml-auto ${primary} py-1">New message</button></div>`;
  }

  function composeView(c) {
    return `
      <div class="task-card space-y-2">
        <p class="task-title">${c.thread ? "Reply" : "New message"}</p>
        ${c.thread ? `<p class="text-[12px] text-zinc-400">To <span title="${esc(c.to)}">${esc(friendlyList(c.to))}</span></p>` : `<input id="mlTo" class="${field}" placeholder="Agent addresses, comma-separated (dana@post.example.com)" value="${esc(c.to || "")}" spellcheck="false">`}
        ${c.thread ? "" : `<input id="mlSubject" class="${field}" placeholder="Subject (optional)">`}
        <textarea id="mlText" rows="5" class="${field}" placeholder="Write your message…"></textarea>
        ${c.thread ? "" : `<label class="text-[12px] text-zinc-400 flex items-center gap-2"><input id="mlTask" type="checkbox" class="accent-amber-400"> Ask their agent to do this as a task (they approve it first)</label>`}
        <div class="flex gap-2"><button data-ml="send" class="${primary}">Send</button><button data-ml="cancel-compose" class="${quiet}">Cancel</button></div>
        <p id="mlSendErr" class="text-[12px] text-red-300"></p>
      </div>`;
  }

  async function listView() {
    if (view.box === "contacts") {
      const { contacts } = await api("/api/mail/contacts");
      if (!contacts.length) return `<p class="text-[12px] text-zinc-500">No contacts yet. Anyone you write to, or accept from Requests, shows up here.</p>`;
      return contacts.map((c) => `
        <div class="task-card space-y-1.5" data-ml-contact="${esc(c.address)}">
          <div class="flex items-center justify-between gap-2"><p class="text-[13px] text-white truncate" title="${esc(c.address)}">${esc(c.nickname || friendlySender(c.address))}</p>
            <select data-ml="trust" class="bg-surface-900 border border-surface-700 rounded-lg px-2 py-1 text-[11px] text-white">
              ${[["known", "Contact"], ["mine", "My own agent"], ["unknown", "Not a contact"], ["blocked", "Blocked"]].map(([v, l]) => `<option value="${v}" ${c.trust === v ? "selected" : ""}>${l}</option>`).join("")}
            </select></div>
          ${c.verified ? `<p class="text-[11px] text-emerald-300">Safety number checked with them</p>` : ""}
          <details><summary class="text-[11px] text-zinc-500 cursor-pointer">Details</summary>
            <p class="text-[11px] text-zinc-500 font-mono mt-1">${esc(c.address)}</p>
            <p class="text-[11px] text-zinc-500 mt-1">Safety number <span class="font-mono text-zinc-300">${esc(c.safety)}</span></p></details>
          ${(c.trust === "known" || c.trust === "mine") ? `<label class="text-[11px] text-zinc-400 flex items-center gap-2"><input type="checkbox" data-ml="autoreply" ${c.auto_reply ? "checked" : ""} class="accent-brand-400"> Auto-reply to their messages</label>` : ""}
          <div class="flex gap-2">${c.verified ? "" : `<button data-ml="verify" class="${quiet} px-2 py-1">I compared it with them</button>`}
            <button data-ml="message" class="${quiet} px-2 py-1">Message</button></div>
        </div>`).join("");
    }
    const { threads } = await api(`/api/mail/threads?box=${view.box}`);
    if (view.box === "drafts") {
      if (!threads.length) return `<p class="text-[12px] text-zinc-500">Nothing waiting. When Xod writes to another agent for you, it waits here for your OK.</p>`;
      return threads.map((d) => `
        <div class="task-card space-y-2" data-ml-draft="${esc(d.id)}">
          <p class="text-[12px] text-zinc-400">To <span class="text-zinc-200" title="${esc((d.recipients || [d.peer]).join(", "))}">${esc((d.recipients || [d.peer]).map((a) => friendlySender(a)).join(", "))}</span>${d.kind === "task_request" ? ` · <span class="text-brand-300">asks them to do a task</span>` : ""} · ${d.origin === "task" ? "the result of a task" : "written by Xod"}</p>
          ${d.subject ? `<p class="text-[13px] text-white">${esc(d.subject)}</p>` : ""}
          <textarea data-ml-draft-text rows="5" class="${field} text-[12px]">${esc(d.text)}</textarea>
          <div class="flex gap-2"><button data-ml="approve" class="${primary}">Approve and send</button><button data-ml="discard" class="${quiet}">Discard</button></div>
        </div>`).join("");
    }
    if (view.box === "held") {
      return threads.map((m) => `
        <div class="task-card space-y-1.5 border-amber-500/30" data-ml-held="${esc(m.peer)}">
          <p class="text-[12px] text-amber-300">${esc(m.problem)}</p>
          <p class="text-[12px] text-zinc-400">Claims to be from <span title="${esc(m.peer)}">${esc(friendlySender(m.peer))}</span> · ${esc(when(m.created_at))}</p>
          <p class="text-[12px] text-zinc-300 whitespace-pre-wrap">${esc((m.text || "").slice(0, 400))}</p>
          ${/keys changed/i.test(m.problem || "") ? `<button data-ml="accept-keys" class="${quiet} px-2 py-1">I checked their new safety number: trust it</button>` : ""}
        </div>`).join("") || `<p class="text-[12px] text-zinc-500">Nothing held.</p>`;
    }
    if (!threads.length) {
      return `<p class="text-[12px] text-zinc-500">${view.box === "requests" ? "No requests. Messages from agents who aren't your contacts land here first." : "No messages yet. Share your address with someone, or write to an agent."}</p>`;
    }
    // Phase 3: the inbox groups by sender. One row per agent - friendly
    // name, the latest real message, and a count - so a pile of threads
    // from the same machine reads as one conversation. A group with a
    // single thread opens it directly; a group with several opens the
    // sender's thread list first. Previews never show raw machine JSON.
    const threadRow = (t) => {
      const m = t.last;
      const who = t.peers.filter((p) => p !== st.address).join(", ") || m.peer;
      const preview = MW ? MW.plainPreview(m.text, m.subject) : (m.subject || m.text);
      return `
        <button data-ml-thread="${esc(t.thread)}" class="w-full text-left task-card space-y-1 hover:border-white/20">
          <div class="flex items-center justify-between gap-2">
            <p class="text-[13px] ${t.unread ? "text-white font-semibold" : "text-zinc-200"} truncate" title="${esc(who)}">${esc(who.split(", ").map((a) => friendlySender(a)).join(", "))}</p>
            <span class="text-[11px] text-zinc-500 shrink-0">${esc(when(t.last_at))}</span></div>
          <p class="text-[12px] text-zinc-400 truncate">${m.kind === "task_request" ? `<span class="text-brand-300">Task request · </span>` : ""}${esc(preview)}</p>
        </button>`;
    };
    if (!MW) return threads.map(threadRow).join("");
    const groups = MW.groupThreadsBySender(threads, st.address);
    if (view.sender) {
      const g = groups.find((x) => x.key === view.sender);
      if (g && g.threads.length > 1) {
        const name = g.peers.map((a) => friendlySender(a)).join(", ");
        return `<button data-ml="all-senders" class="text-[12px] text-zinc-400 hover:text-white">← All senders</button>
          <p class="text-[13px] text-white">${esc(name)}</p>` + g.threads.map(threadRow).join("");
      }
      view.sender = null;
    }
    return groups.map((g) => {
      if (g.threads.length === 1) return threadRow(g.threads[0]);
      const m = g.last;
      const name = g.peers.map((a) => friendlySender(a)).join(", ");
      const preview = MW.plainPreview(m.text, m.subject);
      return `
        <button data-ml-sender="${esc(g.key)}" class="w-full text-left task-card space-y-1 hover:border-white/20">
          <div class="flex items-center justify-between gap-2">
            <p class="text-[13px] ${g.unread ? "text-white font-semibold" : "text-zinc-200"} truncate">${esc(name)}</p>
            <span class="text-[11px] text-zinc-500 shrink-0">${esc(when(g.last_at))}</span></div>
          <p class="text-[12px] text-zinc-400 truncate">${esc(preview)}</p>
          <p class="text-[11px] text-zinc-500">${g.count} message${g.count === 1 ? "" : "s"} · ${g.threads.length} conversations</p>
        </button>`;
    }).join("");
  }

  async function threadView(id) {
    const { messages } = await api(`/api/mail/thread/${encodeURIComponent(id)}`);
    const lastIn = [...messages].reverse().find((m) => m.direction === "in");
    const peer = lastIn ? lastIn.peer : (messages[0]?.recipients || [messages[0]?.peer]).join(", ");
    const isRequest = view.box === "requests";
    // Phase 3: a connection request reads in plain words - who is asking
    // (friendly name + where), what it can do and what it cannot do, from
    // the capability set it shared. The raw payload never renders on the
    // surface; it lives under the card's Details.
    const caps = isRequest ? threadCapabilities(messages) : null;
    const helloMsg = isRequest && MW ? messages.find((m) => MW.parseMachinePayload(m.text)) : null;
    const requestCard = isRequest ? `
      <div class="task-card space-y-2" data-ml-request>
        <p class="text-[13px] text-white">${esc(requesterIntro(peer))} wants to connect.</p>
        ${caps ? `${MW.canSentence(caps) ? `<p class="text-[12px] text-zinc-300">${esc(MW.canSentence(caps))}</p>` : ""}${MW.cannotSentence(caps) ? `<p class="text-[12px] text-zinc-300">${esc(MW.cannotSentence(caps))}</p>` : ""}`
          : `<p class="text-[12px] text-zinc-300">It can: send you messages.</p><p class="text-[12px] text-zinc-300">It cannot: do anything else unless you allow it.</p>`}
        <div class="flex gap-2"><button data-ml="accept" data-peer="${esc(peer)}" class="${primary}">Allow</button><button data-ml="block" data-peer="${esc(peer)}" class="${quiet}">Block</button></div>
        ${helloMsg ? `<details><summary class="text-[11px] text-zinc-500 cursor-pointer">Details</summary><p class="font-mono text-[11px] text-zinc-500 whitespace-pre-wrap mt-1">${esc(helloMsg.text)}</p></details>` : ""}
      </div>` : "";
    // A message whose text is a machine payload renders as one plain line;
    // the raw payload sits under its own Details, never on the surface.
    const messageBody = (m) => {
      const payload = MW ? MW.parseMachinePayload(m.text) : null;
      if (!payload) return `${m.subject ? `<p class="text-[13px] text-white mt-0.5">${esc(m.subject)}</p>` : ""}
          <p class="text-[13px] text-zinc-200 whitespace-pre-wrap mt-0.5">${esc(m.text)}</p>`;
      const line = MW.capabilitySet(payload) ? "Shared what it can do." : "An automatic message between agents.";
      return `<p class="text-[12px] text-zinc-400 mt-0.5">${line}</p>
          <details><summary class="text-[11px] text-zinc-500 cursor-pointer">Details</summary><p class="font-mono text-[11px] text-zinc-500 whitespace-pre-wrap mt-1">${esc(m.text)}</p></details>`;
    };
    return `
      <button data-ml="back" class="text-[12px] text-zinc-400 hover:text-white">← Back</button>
      <p class="text-[13px] text-white" title="${esc(peer)}">${esc(friendlyList(peer))}</p>
      ${requestCard}
      <div class="space-y-2">${messages.filter((m) => m.status !== "draft").map((m) => `
        <div class="rounded-xl px-3 py-2 ${m.direction === "out" ? "bg-brand-500/10 border border-brand-500/20 ml-6" : "bg-surface-900 border border-white/[0.06] mr-6"}">
          <p class="text-[11px] text-zinc-500">${m.direction === "out" ? "You" : `<span title="${esc(m.peer)}">${esc(friendlySender(m.peer))}</span>`} · ${esc(when(m.created_at))}${m.direction === "in" && m.verified ? ` · <span class="text-emerald-300" title="Signed with their key and opened only by yours">verified</span>` : ""}${m.status === "failed" ? ` · <span class="text-red-300">not sent: ${esc(m.problem || "")}</span>` : ""}</p>
          ${messageBody(m)}
          ${m.kind === "task_request" && m.direction === "in" && !isRequest ? `<div class="mt-2 flex items-center gap-2">${m.task_id ? `<span class="text-[12px] text-zinc-400">Running as a task (see Tasks). The result comes back as a reply for your OK.</span>`
            : `<button data-ml="run" data-id="${esc(m.id)}" class="${primary} py-1 whitespace-nowrap shrink-0">Run this task</button><span class="text-[11px] text-zinc-500">It still asks before sending, paying or deleting anything.</span>`}</div>` : ""}
        </div>`).join("")}</div>
      ${!isRequest && lastIn ? `<div class="flex gap-2 flex-wrap">
        <button data-ml="reply" data-peer="${esc(lastIn.peer)}" data-thread="${esc(id)}" data-msg="${esc(lastIn.msg_id)}" class="${quiet} bg-surface-700">Reply</button>
        <button data-ml="xod-reply" data-id="${esc(lastIn.id)}" class="${quiet} bg-surface-700">Let Xod draft a reply</button></div>` : ""}`;
  }

  async function render() {
    const body = document.getElementById("mailBody");
    if (!body) return;
    await refreshBadge();
    if (!st.set_up) { body.innerHTML = setupView(); wireSetup(); return; }
    await loadNameMaps();
    let main;
    if (view.compose) main = composeView(view.compose);
    else if (view.thread) main = await threadView(view.thread);
    else main = await listView();
    body.innerHTML = `${headerCard()}${view.thread || view.compose ? "" : tabs()}<div class="space-y-2">${main}</div>`;
    if (window.lucide) lucide.createIcons();
  }

  function wireSetup() {
    const name = document.getElementById("mlName"), relay = document.getElementById("mlRelay"), prev = document.getElementById("mlPreview");
    const nets = document.getElementById("mlNets");
    const where = () => (document.querySelector("input[name=mlWhere]:checked") || {}).value;
    const net = () => (document.querySelector("input[name=mlNet]:checked") || {}).value || "";
    const upd = () => {
      const dom = where() === "home" ? net() : (relay.value || "relay").replace(/^https?:\/\//, "");
      prev.textContent = `${(name.value || "name").toLowerCase()}@${dom || "this-computer"}`;
      relay.disabled = where() === "home";
    };
    api("/api/mail/networks").then((r) => {
      const opts = (r.networks || []);
      nets.innerHTML = opts.map((n, i) => n.domain
        ? `<label class="flex items-start gap-2"><input type="radio" name="mlNet" value="${esc(n.domain)}" data-kind="${esc(n.kind)}" ${i === 0 ? "checked" : ""} class="mt-0.5"><span><span class="font-mono text-zinc-200">${esc(n.domain)}</span><br>${esc(n.label)}</span></label>`
        : `<p class="text-zinc-500">${esc(n.label)}</p>`).join("") || "<p>Couldn't find this computer's network name.</p>";
      nets.addEventListener("change", upd);
      upd();
    }).catch(() => { nets.textContent = "Couldn't read this computer's networks."; });
    document.querySelectorAll("input[name=mlWhere]").forEach((r) => r.addEventListener("change", upd));
    name.addEventListener("input", upd); relay.addEventListener("input", upd); upd();
  }

  async function onClick(e) {
    const el = e.target.closest("[data-ml],[data-ml-box],[data-ml-thread],[data-ml-sender],[data-close-mail]");
    if (!el) return;
    if (el.matches("[data-close-mail]")) return window.closeMailPanel();
    if (el.dataset.mlBox) { view = { box: el.dataset.mlBox }; return render(); }
    if (el.dataset.mlSender) { view.sender = el.dataset.mlSender; return render(); }
    if (el.dataset.mlThread) { view.thread = el.dataset.mlThread; return render(); }
    const a = el.dataset.ml;
    if (a === "trust" && e.type === "change") {
      const addr = el.closest("[data-ml-contact]").dataset.mlContact;
      await api(`/api/mail/contacts/${encodeURIComponent(addr)}`, "PATCH", { trust: el.value });
      return showToast("Saved");
    }
    if (a === "autoreply" && e.type === "change") {
      const addr = el.closest("[data-ml-contact]").dataset.mlContact;
      const r = await api(`/api/mail/contacts/${encodeURIComponent(addr)}`, "PATCH", { auto_reply: el.checked });
      if (!r.success && el.checked) { el.checked = false; return showToast("Auto-reply is only for known contacts"); }
      return showToast("Saved");
    }
    if (e.type !== "click") return;
    if (a === "setup") {
      el.disabled = true; el.textContent = "Creating…";
      const home = (document.querySelector("input[name=mlWhere]:checked") || {}).value === "home";
      const net = document.querySelector("input[name=mlNet]:checked");
      const r = await api("/api/mail/setup", "POST", { name: document.getElementById("mlName").value,
        relay: home ? "this computer" : document.getElementById("mlRelay").value, network: home && net ? net.value : "" });
      if (!r.success) { document.getElementById("mlSetupErr").textContent = r.error; el.disabled = false; el.textContent = "Create my address"; return; }
      showToast(`You're ${r.address}`);
      return render();
    }
    if (a === "copy") { await navigator.clipboard.writeText(st.address); return showToast("Address copied"); }
    if (a === "fix-post") {
      el.disabled = true; el.textContent = "Fixing…";
      const r = await api("/api/mail/home/repair", "POST", {});
      showToast(r.success ? "Messages are back" : "Still paused — Xod will keep trying");
      return render();
    }
    if (a === "all-senders") { view.sender = null; return render(); }
    if (a === "retry") { const r = await api("/api/mail/outbox/retry", "POST", {}); showToast(r.sent ? `Delivered ${r.sent}` : "Still not reachable; Xod keeps trying"); return render(); }
    if (a === "compose") { view.compose = {}; return render(); }
    if (a === "message") { view = { box: "inbox", compose: { to: el.closest("[data-ml-contact]").dataset.mlContact } }; return render(); }
    if (a === "cancel-compose") { view.compose = null; return render(); }
    if (a === "back") { view.thread = null; return render(); }
    if (a === "reply") { view.compose = { to: el.dataset.peer, thread: el.dataset.thread, in_reply_to: el.dataset.msg }; view.thread = null; return render(); }
    if (a === "send") {
      const c = view.compose;
      const body = { to: c.thread ? c.to : document.getElementById("mlTo").value, text: document.getElementById("mlText").value,
                     subject: c.thread ? "" : document.getElementById("mlSubject").value, thread: c.thread || null, in_reply_to: c.in_reply_to || null,
                     kind: c.thread ? "reply" : (document.getElementById("mlTask").checked ? "task_request" : "message") };
      el.disabled = true; el.textContent = "Sending…";
      const r = await api("/api/mail/send", "POST", body);
      if (!r.success) { document.getElementById("mlSendErr").textContent = r.error; el.disabled = false; el.textContent = "Send"; return; }
      showToast(r.errors?.length ? `Sent to ${r.sent_to.join(", ")}; ${r.errors.join("; ")}` : "Sent");
      view = { box: "inbox", thread: r.thread };
      return render();
    }
    if (a === "approve" || a === "discard") {
      const card = el.closest("[data-ml-draft]"), id = card.dataset.mlDraft;
      if (a === "approve") {
        await api(`/api/mail/drafts/${id}`, "PATCH", { text: card.querySelector("[data-ml-draft-text]").value });
        el.disabled = true; el.textContent = "Sending…";
        const r = await api(`/api/mail/drafts/${id}/approve`, "POST");
        showToast(r.success ? "Sent" : r.error || "Couldn't send");
      } else {
        await api(`/api/mail/drafts/${id}/discard`, "POST");
      }
      return render();
    }
    if (a === "accept" || a === "block") {
      await api(`/api/mail/contacts/${encodeURIComponent(el.dataset.peer)}`, "PATCH", { trust: a === "accept" ? "known" : "blocked" });
      view = { box: a === "accept" ? "inbox" : "requests", thread: a === "accept" ? view.thread : null };
      return render();
    }
    if (a === "verify") {
      await api(`/api/mail/contacts/${encodeURIComponent(el.closest("[data-ml-contact]").dataset.mlContact)}`, "PATCH", { verified: true });
      return render();
    }
    if (a === "accept-keys") {
      if (!confirm("Only do this after comparing the new safety number with them directly.")) return;
      const r = await api(`/api/mail/contacts/${encodeURIComponent(el.closest("[data-ml-held]").dataset.mlHeld)}/accept-keys`, "POST");
      showToast(r.success ? "New keys trusted" : r.error);
      return render();
    }
    if (a === "run") {
      if (!confirm("Run this as a Xod task? It still asks before sending, paying, posting or deleting anything.")) return;
      const r = await api(`/api/mail/messages/${el.dataset.id}/run`, "POST");
      showToast(r.success ? "Started; see Tasks" : r.error);
      return render();
    }
    if (a === "xod-reply") {
      el.disabled = true; el.textContent = "Xod is writing…";
      const r = await api(`/api/mail/messages/${el.dataset.id}/draft-reply`, "POST");
      showToast(r.success ? "Draft ready for your OK" : r.error || "Couldn't write one");
      view = { box: "drafts" };
      return render();
    }
  }

  window.openMailPanel = () => {
    let root = document.getElementById("mailPanel");
    if (!root) {
      root = document.createElement("div");
      root.id = "mailPanel";
      root.className = "tasks-panel";
      root.innerHTML = `
        <div class="tasks-scrim" data-close-mail></div>
        <aside class="tasks-drawer" role="dialog" aria-label="Agent Mail">
          <header class="px-5 pt-5 pb-3 flex items-start justify-between gap-3 border-b border-white/[0.06]">
            <div><h3 class="font-bold text-lg text-white">Messages</h3>
              <p class="text-[12px] text-zinc-400 mt-0.5">Encrypted messages between your machines and agents. Nothing is sent or run without your OK.</p></div>
            <button data-close-mail class="p-1 -mr-1 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700" title="Close"><i data-lucide="x" class="size-4"></i></button>
          </header>
          <div id="mailBody" class="flex-1 overflow-y-auto px-5 py-4 space-y-4"></div>
        </aside>`;
      document.body.appendChild(root);
      root.addEventListener("click", onClick);
      root.addEventListener("change", onClick);
    }
    view = { box: "inbox" };
    root.classList.remove("hidden");
    requestAnimationFrame(() => root.classList.add("is-open"));
    api("/api/mail/check", "POST").catch(() => {}).finally(render);
    render();
  };
  window.closeMailPanel = () => {
    const root = document.getElementById("mailPanel");
    if (!root) return;
    root.classList.remove("is-open");
    setTimeout(() => root.classList.add("hidden"), 250);
  };
})();
