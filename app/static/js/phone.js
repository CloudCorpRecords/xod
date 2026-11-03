// Your phone: talk to Xod through a Telegram bot of your own. Backend: services/phone.py.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = async (url, method = "GET", body) =>
    (await fetch(url, { method, headers: body !== undefined ? { "Content-Type": "application/json" } : {}, body: body !== undefined ? JSON.stringify(body) : undefined })).json();
  const btn = "px-3 py-1.5 rounded-lg text-[12px] font-semibold";
  const primary = `${btn} bg-brand-500 text-surface-900 hover:bg-brand-400`;
  const quiet = `${btn} font-medium text-zinc-300 hover:text-white hover:bg-surface-700`;
  const field = "w-full bg-surface-900 border border-surface-700 rounded-lg px-2.5 py-1.5 text-[13px] text-white font-mono focus:outline-none focus:border-brand-500";
  let st = null, poll = null;

  function connectView() {
    return `
      <section class="space-y-3">
        <div class="task-card space-y-2">
          <p class="task-title">Xod in your pocket</p>
          <p class="text-[12px] text-zinc-400">Message Xod from your phone with Telegram. It works on this computer as usual and
            writes back when it's done, asks you on your phone before anything is sent, paid or deleted, and you approve with one tap.</p>
          <ul class="text-[12px] text-zinc-400 space-y-1 list-disc pl-4">
            <li>You make your own bot, so nobody else's service sits in between. Only your Telegram account is listened to.</li>
            <li>Nothing on this computer is opened to the internet: Xod asks Telegram for your messages.</li>
            <li>What you write to the bot, and its replies, pass through Telegram's servers like any Telegram chat. Keep this off if that's not OK for you.</li>
          </ul>
        </div>
        <ol class="text-[12px] text-zinc-300 space-y-1.5 list-decimal pl-4">
          <li>In Telegram, open <a class="text-brand-300 underline" href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a>, send <span class="font-mono">/newbot</span> and pick any name.</li>
          <li>It replies with a token (like <span class="font-mono">123456789:AAH…</span>). Paste it here:</li>
        </ol>
        <input id="phToken" class="${field}" placeholder="123456789:AAH…" autocomplete="off" spellcheck="false">
        <button data-ph="connect" class="${primary}">Connect</button>
        <p id="phErr" class="text-[12px] text-red-300"></p>
      </section>`;
  }

  function pairView() {
    return `
      <section class="space-y-3">
        <div class="task-card space-y-2">
          <p class="task-title">One last step</p>
          <p class="text-[12px] text-zinc-400">On your phone, open Telegram, find <span class="font-mono text-white">@${esc(st.bot)}</span> and send it this code:</p>
          <p class="font-mono text-3xl tracking-[0.3em] text-white text-center py-2">${esc(st.pair_code || "")}</p>
          <p class="text-[12px] text-zinc-400">Or, if Telegram is on this computer too, <a class="text-brand-300 underline" href="${esc(st.pair_link || "#")}" target="_blank" rel="noopener">open the link</a>: it's the same account.</p>
        </div>
        <p class="text-[11px] text-zinc-500">${st.listening ? "Waiting for the code…" : "Not listening: check the token."}${st.last_error ? ` <span class="text-amber-300">${esc(st.last_error)}</span>` : ""}</p>
        <button data-ph="disconnect" class="${quiet}">Use a different bot</button>
      </section>`;
  }

  function linkedView() {
    return `
      <section class="space-y-3">
        <div class="task-card space-y-1.5">
          <p class="task-title">Linked with ${esc(st.who || "your phone")}</p>
          <p class="text-[12px] text-zinc-400">Write to <span class="font-mono text-white">@${esc(st.bot)}</span> on Telegram. Each message becomes a task here;
            you'll get the result there, and Approve / Cancel buttons when something needs your OK. Reply to a question to answer it.</p>
          ${st.last_error ? `<p class="text-[11px] text-amber-300">Can't reach Telegram right now: ${esc(st.last_error)}</p>` : ""}
        </div>
        <label class="flex items-start gap-2 text-[12px] text-zinc-300">
          <input type="checkbox" data-ph="notify" ${st.notify_all ? "checked" : ""} class="mt-0.5">
          <span>Tell my phone about every task (when one needs me, or finishes), not only the ones I started from it</span></label>
        <p class="text-[11px] text-zinc-500">Xod has to be running on this computer. Closing the window keeps it running in the ${navigator.userAgent.includes("Mac") ? "menu bar" : "tray"}.</p>
        <div class="flex gap-2">
          <button data-ph="test" class="${quiet}">Send a test message</button>
          <button data-ph="disconnect" class="${quiet} text-red-300">Unlink</button>
        </div>
      </section>`;
  }

  function androidView() {
    return `
      <section class="space-y-3">
        <div class="task-card space-y-2">
          <p class="task-title">Pair an Android phone (Xod app)</p>
          <p class="text-[12px] text-zinc-400">For the Xod phone app (not Telegram): Xod shows a QR, the phone scans it and is
            set up and paired in one step. The code inside works once and expires in 10 minutes. Compare the safety number on
            both screens. The phone always shows it's connected, nothing hidden.</p>
          <div id="phPairBox"></div>
          <div class="flex flex-wrap gap-2">
            <button data-ph="pairag" class="${primary}">Pair a phone</button>
            <button data-ph="blescan" class="${quiet}">Pair over Bluetooth</button>
          </div>
          <div id="phPhones"></div>
        </div>
      </section>`;
  }

  function iphoneView() {
    return `
      <section class="space-y-3">
        <div class="task-card space-y-2">
          <p class="task-title">iPhone through Mirroring (Mac only)</p>
          <p class="text-[12px] text-zinc-400">Xod reads and taps your iPhone in the iPhone Mirroring window.
            Off unless you switch it on. If you unlock your iPhone, Mirroring pauses and Xod says so.</p>
          <label class="flex items-start gap-2 text-[12px] text-zinc-300">
            <input type="checkbox" data-ph="mirror" class="mt-0.5">
            <span>Let Xod use iPhone Mirroring</span></label>
          <p id="phMirrorMsg" class="text-[11px] text-zinc-500"></p>
        </div>
      </section>`;
  }

  async function loadMirror() {
    const cb = document.querySelector('[data-ph="mirror"]'), msg = document.getElementById("phMirrorMsg");
    if (!cb) return;
    try {
      const d = await api("/api/phones/iphone-mirroring");
      cb.checked = !!d.enabled;
      if (msg) msg.textContent = d.message || "";
    } catch (e) { /* panel still works */ }
  }

  async function loadPhones() {
    const box = document.getElementById("phPhones");
    if (!box) return;
    try {
      const d = await api("/api/phones");
      const mac = d.computer || {};
      box.innerHTML = `<p class="text-[11px] text-zinc-500">This computer: ${mac.locked === true ? "screen locked" : mac.locked === false ? "screen awake" : "lock unknown"}${mac.awake_held ? " · staying awake" : ""}</p>` +
        ((d.phones || []).length ? d.phones.map((ph) => `<p class="text-[12px] text-zinc-300">${esc(ph.nickname || ph.address)} <span class="text-zinc-500">${esc(ph.address)}</span>
          ${ph.online ? '<span class="text-emerald-300">online</span>' : '<span class="text-zinc-500">last seen ' + (ph.last_seen ? new Date(ph.last_seen * 1000).toLocaleString() : "never") + "</span>"}
          <button data-ph-unpair="${esc(ph.address)}" class="${quiet}">Unpair</button></p>`).join("")
        : '<p class="text-[12px] text-zinc-500">No Android phone paired yet.</p>');
    } catch (e) { /* the panel still works without it */ }
  }

  async function render() {
    const body = document.getElementById("phoneBody");
    if (!body) return;
    try { st = await api("/api/phone"); } catch (e) { body.innerHTML = `<p class="text-red-300 text-[12px]">Couldn't reach Xod.</p>`; return; }
    body.innerHTML = (!st.connected ? connectView() : !st.paired ? pairView() : linkedView()) + androidView() + iphoneView();
    loadPhones(); loadMirror();
    clearInterval(poll);
    if (st.connected && !st.paired) poll = setInterval(async () => {       // pairing happens on the phone: notice it here
      const now = await api("/api/phone").catch(() => null);
      if (now && now.paired) { clearInterval(poll); render(); showToast("Your phone is linked"); }
    }, 2500);
  }

  async function onClick(e) {
    const el = e.target.closest("[data-ph], [data-close-phone]");
    if (!el) return;
    if (el.hasAttribute("data-close-phone")) return window.closePhonePanel();
    const a = el.dataset.ph;
    if (a === "connect") {
      el.disabled = true; el.textContent = "Checking…";
      const r = await api("/api/phone/connect", "POST", { token: document.getElementById("phToken").value });
      if (!r.success) { document.getElementById("phErr").textContent = r.error; el.disabled = false; el.textContent = "Connect"; return; }
      return render();
    }
    if (a === "disconnect") {
      if (!confirm("Unlink your phone? You can link it again any time.")) return;
      await api("/api/phone/disconnect", "POST");
      return render();
    }
    if (a === "pairag") {
      const box = document.getElementById("phPairBox");
      box.innerHTML = '<p class="text-[12px] text-zinc-400">Making a QR…</p>';
      const r = await api("/api/phone/pair-offer");
      if (!r.success) { box.innerHTML = `<p class="text-[12px] text-red-300">${esc(r.error || "Couldn't make a QR.")}</p>`; return; }
      box.innerHTML = `<div class="bg-white p-2 rounded-lg w-fit">${r.qr_svg || ""}</div>
        <p class="text-[12px] text-zinc-300">Scan this in the Xod phone app. Safety number: <span class="font-mono text-white">${esc(r.safety || "")}</span></p>
        <p class="text-[11px] text-zinc-500">Expires ${esc(new Date(r.expires_at * 1000).toLocaleTimeString())}. If pairing doesn't finish, make a new QR.</p>
        <p class="text-[11px] text-zinc-500">${r.away
          ? "Away from home, the phone reaches this computer over Tailscale: keep Tailscale on, on both."
          : "The phone reaches this computer on the same Wi-Fi only. To use it from anywhere, install Tailscale (free) on this computer and the phone, signed in to the same account."}</p>`;
      return;
    }
    if (a === "blescan") {
      const box = document.getElementById("phPairBox");
      box.innerHTML = '<p class="text-[12px] text-zinc-400">Looking for phones nearby… (open the Xod phone app and tap "Pair nearby")</p>';
      const r = await api("/api/phone/ble/scan");
      if (!r.success) { box.innerHTML = `<p class="text-[12px] text-red-300">${esc(r.error || "Bluetooth didn't work.")}</p>`; return; }
      box.innerHTML = r.phones.length
        ? `<p class="text-[12px] text-zinc-300">Phones nearby:</p>` + r.phones.map((p) =>
            `<button data-ph="blepair" data-id="${esc(p.id)}" class="${quiet} block">${esc(p.name)} <span class="text-zinc-500">${esc(p.id)}</span></button>`).join("")
        : '<p class="text-[12px] text-zinc-400">No phone found. On the phone, open Xod and tap "Pair nearby", then try again.</p>';
      return;
    }
    if (a === "blepair") {
      const box = document.getElementById("phPairBox");
      box.innerHTML = '<p class="text-[12px] text-zinc-400">Sending the pairing offer over Bluetooth…</p>';
      const r = await api("/api/phone/ble/pair", "POST", { id: el.dataset.id });
      box.innerHTML = r.success
        ? `<p class="text-[12px] text-zinc-300">Tap <b>Pair</b> on your phone. Safety number: <span class="font-mono text-white">${esc(r.safety || "")}</span>: check the phone shows the same.</p>`
        : `<p class="text-[12px] text-red-300">${esc(r.error || "Couldn't send it.")}</p>`;
      return;
    }
    const unpair = e.target.closest("[data-ph-unpair]");
    if (unpair) {
      await api("/api/phones/" + encodeURIComponent(unpair.dataset.phUnpair), "DELETE");
      return render();
    }
    if (a === "test") {
      const r = await api("/api/phone/test", "POST");
      return showToast(r.success ? "Sent: check your phone" : r.error);
    }
    if (a === "mirror" && e.type === "change") {
      const r = await api("/api/phones/iphone-mirroring", "POST", { on: el.checked });
      const msg = document.getElementById("phMirrorMsg");
      if (msg) msg.textContent = r.message || "";
    }
    if (a === "notify" && e.type === "change") {
      await api("/api/phone/notify", "POST", { all_tasks: el.checked });
    }
  }

  window.openPhonePanel = () => {
    let root = document.getElementById("phonePanel");
    if (!root) {
      root = document.createElement("div");
      root.id = "phonePanel";
      root.className = "tasks-panel";
      root.innerHTML = `
        <div class="tasks-scrim" data-close-phone></div>
        <aside class="tasks-drawer" role="dialog" aria-label="Your phone">
          <header class="px-5 pt-5 pb-3 flex items-start justify-between gap-3 border-b border-white/[0.06]">
            <div><h3 class="font-bold text-lg text-white">Your phone</h3>
              <p class="text-[12px] text-zinc-400 mt-0.5">Hand Xod jobs from anywhere, and approve them with a tap.</p></div>
            <button data-close-phone class="p-1 -mr-1 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700" title="Close"><i data-lucide="x" class="size-4"></i></button>
          </header>
          <div id="phoneBody" class="flex-1 overflow-y-auto px-5 py-4 space-y-4"></div>
        </aside>`;
      document.body.appendChild(root);
      root.addEventListener("click", onClick);
      root.addEventListener("change", onClick);
      if (window.lucide) lucide.createIcons();
    }
    root.classList.remove("hidden");
    requestAnimationFrame(() => root.classList.add("is-open"));
    render();
  };
  window.closePhonePanel = () => {
    const root = document.getElementById("phonePanel");
    if (!root) return;
    clearInterval(poll);
    root.classList.remove("is-open");
    setTimeout(() => root.classList.add("hidden"), 250);
  };
})();
