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

  let view = { box: "inbox", thread: null, compose: null };
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

  function headerCard() {
    return `
      <div class="task-card space-y-1.5">
        <div class="flex items-center justify-between gap-2">
          <p class="text-[13px] text-white font-mono truncate" title="Your agent address">${esc(st.address)}</p>
          <button data-ml="copy" class="${quiet} px-2 py-1">Copy</button>
        </div>
        <details><summary class="text-[11px] text-zinc-500 cursor-pointer">Safety number</summary>
          <p class="text-[11px] text-zinc-400 mt-1">Compare this with someone (in person or on a call) to be sure your messages reach the real you.</p>
          <p class="font-mono text-[12px] text-zinc-200 mt-1">${esc(st.safety_number)}</p></details>
        ${st.last_error ? `<p class="text-[11px] text-amber-300">${esc(st.last_error)}</p>` : `<p class="text-[11px] text-zinc-500">Checked ${esc(when(st.last_check)) || "never"} · checks every 30 seconds</p>`}
        ${st.self_hosted ? `<p class="text-[11px] ${st.home && st.home.running ? "text-emerald-300" : "text-amber-300"}">${st.home && st.home.running
          ? `Hosted on this computer (port ${esc(st.home.port)}): ${/\.ts\.net/.test(st.address) ? "reachable on your Tailscale network" : "reachable on this network"} while it's on.`
          : "This computer's post office isn't running. Restart Xod, or check that nothing else uses its port."}</p>` : ""}
        ${st.outbox ? `<p class="text-[11px] text-zinc-400">${st.outbox} waiting in your outbox (the other side wasn't reachable): Xod keeps trying. <button data-ml="retry" class="underline">Try now</button></p>` : ""}
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
        ${c.thread ? `<p class="text-[12px] text-zinc-400">To ${esc(c.to)}</p>` : `<input id="mlTo" class="${field}" placeholder="Agent addresses, comma-separated (dana@post.example.com)" value="${esc(c.to || "")}" spellcheck="false">`}
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
          <div class="flex items-center justify-between gap-2"><p class="text-[13px] text-white font-mono truncate">${esc(c.nickname || c.address)}</p>
            <select data-ml="trust" class="bg-surface-900 border border-surface-700 rounded-lg px-2 py-1 text-[11px] text-white">
              ${[["known", "Contact"], ["mine", "My own agent"], ["unknown", "Not a contact"], ["blocked", "Blocked"]].map(([v, l]) => `<option value="${v}" ${c.trust === v ? "selected" : ""}>${l}</option>`).join("")}
            </select></div>
          ${c.nickname ? `<p class="text-[11px] text-zinc-500 font-mono">${esc(c.address)}</p>` : ""}
          <p class="text-[11px] text-zinc-500">Safety number <span class="font-mono text-zinc-300">${esc(c.safety)}</span>${c.verified ? ` <span class="text-emerald-300">· checked</span>` : ""}</p>
          <div class="flex gap-2">${c.verified ? "" : `<button data-ml="verify" class="${quiet} px-2 py-1">I compared it with them</button>`}
            <button data-ml="message" class="${quiet} px-2 py-1">Message</button></div>
        </div>`).join("");
    }
    const { threads } = await api(`/api/mail/threads?box=${view.box}`);
    if (view.box === "drafts") {
      if (!threads.length) return `<p class="text-[12px] text-zinc-500">Nothing waiting. When Xod writes to another agent for you, it waits here for your OK.</p>`;
      return threads.map((d) => `
        <div class="task-card space-y-2" data-ml-draft="${esc(d.id)}">
          <p class="text-[12px] text-zinc-400">To <span class="font-mono text-zinc-200">${esc((d.recipients || [d.peer]).join(", "))}</span>${d.kind === "task_request" ? ` · <span class="text-brand-300">asks them to do a task</span>` : ""} · ${d.origin === "task" ? "the result of a task" : "written by Xod"}</p>
          ${d.subject ? `<p class="text-[13px] text-white">${esc(d.subject)}</p>` : ""}
          <textarea data-ml-draft-text rows="5" class="${field} text-[12px]">${esc(d.text)}</textarea>
          <div class="flex gap-2"><button data-ml="approve" class="${primary}">Approve and send</button><button data-ml="discard" class="${quiet}">Discard</button></div>
        </div>`).join("");
    }
    if (view.box === "held") {
      return threads.map((m) => `
        <div class="task-card space-y-1.5 border-amber-500/30" data-ml-held="${esc(m.peer)}">
          <p class="text-[12px] text-amber-300">${esc(m.problem)}</p>
          <p class="text-[12px] text-zinc-400">Claims to be from <span class="font-mono">${esc(m.peer)}</span> · ${esc(when(m.created_at))}</p>
          <p class="text-[12px] text-zinc-300 whitespace-pre-wrap">${esc((m.text || "").slice(0, 400))}</p>
          ${/keys changed/i.test(m.problem || "") ? `<button data-ml="accept-keys" class="${quiet} px-2 py-1">I checked their new safety number: trust it</button>` : ""}
        </div>`).join("") || `<p class="text-[12px] text-zinc-500">Nothing held.</p>`;
    }
    if (!threads.length) {
      return `<p class="text-[12px] text-zinc-500">${view.box === "requests" ? "No requests. Messages from agents who aren't your contacts land here first." : "No messages yet. Share your address with someone, or write to an agent."}</p>`;
    }
    return threads.map((t) => {
      const m = t.last;
      const who = t.peers.filter((p) => p !== st.address).join(", ") || m.peer;
      return `
        <button data-ml-thread="${esc(t.thread)}" class="w-full text-left task-card space-y-1 hover:border-white/20">
          <div class="flex items-center justify-between gap-2">
            <p class="text-[13px] ${t.unread ? "text-white font-semibold" : "text-zinc-200"} truncate font-mono">${esc(who)}</p>
            <span class="text-[11px] text-zinc-500 shrink-0">${esc(when(t.last_at))}</span></div>
          <p class="text-[12px] text-zinc-400 truncate">${m.kind === "task_request" ? `<span class="text-brand-300">Task request · </span>` : ""}${esc(m.subject || m.text)}</p>
        </button>`;
    }).join("");
  }

  async function threadView(id) {
    const { messages } = await api(`/api/mail/thread/${encodeURIComponent(id)}`);
    const lastIn = [...messages].reverse().find((m) => m.direction === "in");
    const peer = lastIn ? lastIn.peer : (messages[0]?.recipients || [messages[0]?.peer]).join(", ");
    const isRequest = view.box === "requests";
    return `
      <button data-ml="back" class="text-[12px] text-zinc-400 hover:text-white">← Back</button>
      <p class="text-[13px] text-white font-mono">${esc(peer)}</p>
      ${isRequest ? `<div class="task-card space-y-2 border-amber-500/30"><p class="text-[12px] text-zinc-300">This agent isn't in your contacts. Accept to reply and see their future messages in your Inbox.</p>
        <div class="flex gap-2"><button data-ml="accept" data-peer="${esc(peer)}" class="${primary}">Accept</button><button data-ml="block" data-peer="${esc(peer)}" class="${quiet}">Block</button></div></div>` : ""}
      <div class="space-y-2">${messages.filter((m) => m.status !== "draft").map((m) => `
        <div class="rounded-xl px-3 py-2 ${m.direction === "out" ? "bg-brand-500/10 border border-brand-500/20 ml-6" : "bg-surface-900 border border-white/[0.06] mr-6"}">
          <p class="text-[11px] text-zinc-500">${m.direction === "out" ? "You" : esc(m.peer)} · ${esc(when(m.created_at))}${m.direction === "in" && m.verified ? ` · <span class="text-emerald-300" title="Signed with their key and opened only by yours">verified</span>` : ""}${m.status === "failed" ? ` · <span class="text-red-300">not sent: ${esc(m.problem || "")}</span>` : ""}</p>
          ${m.subject ? `<p class="text-[13px] text-white mt-0.5">${esc(m.subject)}</p>` : ""}
          <p class="text-[13px] text-zinc-200 whitespace-pre-wrap mt-0.5">${esc(m.text)}</p>
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
    const el = e.target.closest("[data-ml],[data-ml-box],[data-ml-thread],[data-close-mail]");
    if (!el) return;
    if (el.matches("[data-close-mail]")) return window.closeMailPanel();
    if (el.dataset.mlBox) { view = { box: el.dataset.mlBox }; return render(); }
    if (el.dataset.mlThread) { view.thread = el.dataset.mlThread; return render(); }
    const a = el.dataset.ml;
    if (a === "trust" && e.type === "change") {
      const addr = el.closest("[data-ml-contact]").dataset.mlContact;
      await api(`/api/mail/contacts/${encodeURIComponent(addr)}`, "PATCH", { trust: el.value });
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
            <div><h3 class="font-bold text-lg text-white">Agent Mail</h3>
              <p class="text-[12px] text-zinc-400 mt-0.5">Encrypted messages between AI agents. Nothing is sent or run without your OK.</p></div>
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
