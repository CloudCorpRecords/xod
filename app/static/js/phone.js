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

  async function render() {
    const body = document.getElementById("phoneBody");
    if (!body) return;
    try { st = await api("/api/phone"); } catch (e) { body.innerHTML = `<p class="text-red-300 text-[12px]">Couldn't reach Xod.</p>`; return; }
    body.innerHTML = !st.connected ? connectView() : !st.paired ? pairView() : linkedView();
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
    if (a === "test") {
      const r = await api("/api/phone/test", "POST");
      return showToast(r.success ? "Sent: check your phone" : r.error);
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
