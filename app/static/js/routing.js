// Smart model routing UI (services/llm_routing.py): the offer to retry with a stronger model,
// "answered by" notes with cost, and the Model routing panel (mode, cloud model, budget, spend).
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const api = async (url, method = "GET", body) =>
    (await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined })).json();
  const money = (v) => (v == null ? "cost unknown" : v < 0.01 ? "under 1¢" : `about $${v.toFixed(2)}`);

  function lastUserText() {
    for (let i = conversationHistory.length - 1; i >= 0; i--) {
      const m = conversationHistory[i];
      if (m.role === "user") return typeof m.content === "string" ? m.content : (m.content.find?.((p) => p.type === "text")?.text || "");
    }
    return "";
  }

  window.retryWithStrongerModel = () => {
    const text = lastUserText();
    if (!text || isStreaming) return;
    window.__nextRoute = "cloud";
    const input = document.getElementById("chatInput");
    input.value = text;
    document.getElementById("chatForm").requestSubmit();
    setTimeout(() => { window.__nextRoute = null; }, 500);
  };

  window.renderRouteEvent = (ev) => {
    const box = document.getElementById("chatMessages");
    if (!box) return;
    const el = document.createElement("div");
    el.className = "max-w-4xl mx-auto w-full";
    if (ev.type === "route_info") {
      el.innerHTML = `<p class="ml-12 text-[11px] text-zinc-500 flex items-center gap-1.5"><i data-lucide="cloud" class="size-3"></i>
        Answered by ${esc(ev.model)}${ev.reason && ev.reason !== "you asked" ? ` (local model struggled: ${esc(ev.reason)})` : ""} · ${ev.calls} call${ev.calls === 1 ? "" : "s"} · ${money(ev.cost_usd)}</p>`;
    } else {
      el.innerHTML = `
        <div class="ml-12 p-3.5 rounded-2xl border border-indigo-400/30 bg-indigo-500/[0.06] flex flex-wrap items-center gap-3">
          <i data-lucide="sparkles" class="size-4 text-indigo-300"></i>
          <p class="text-[13px] text-zinc-200 flex-1 min-w-[200px]">The local model had trouble with this. Try it with <b>${esc(ev.model)}</b>? ${esc(money(ev.est_cost_usd))}. Your conversation is sent to that provider.</p>
          <button data-route="go" class="px-3 py-1.5 rounded-lg bg-indigo-400 text-surface-900 text-[12px] font-semibold hover:bg-indigo-300">Try it</button>
          <button data-route="no" class="px-2.5 py-1.5 rounded-lg text-[12px] text-zinc-400 hover:text-white">No thanks</button>
        </div>`;
      el.addEventListener("click", (e) => {
        const b = e.target.closest("[data-route]");
        if (!b) return;
        el.remove();
        if (b.dataset.route === "go") retryWithStrongerModel();
      });
    }
    box.appendChild(el);
    if (window.lucide) lucide.createIcons();
    if (typeof scrollChatToBottom === "function") scrollChatToBottom(true);
  };

  // ---------------------------------------------------------------- panel
  async function render() {
    const body = document.getElementById("routingBody");
    if (!body) return;
    const d = await api("/api/routing");
    const s = d.settings;
    const pct = s.monthly_budget_usd ? Math.min(100, (d.month_spend_usd / s.monthly_budget_usd) * 100) : 0;
    const modes = [
      ["off", "Off", "Everything stays on this Mac."],
      ["ask", "Ask me", "When the local model struggles, offer to retry with the cloud model, showing the cost first."],
      ["auto", "Automatic", "After two failed steps, finish that answer with the cloud model, within your monthly budget."],
    ];
    body.innerHTML = `
      <section class="space-y-2">
        <h4 class="text-[13px] font-semibold text-white">Stronger model for hard steps</h4>
        ${modes.map(([v, l, d2]) => `<label class="flex gap-3 p-3 rounded-xl border cursor-pointer ${s.mode === v ? "border-brand-500/60 bg-brand-500/[0.06]" : "border-white/[0.06]"}">
          <input type="radio" name="rtmode" value="${v}" ${s.mode === v ? "checked" : ""} class="mt-1 accent-amber-400">
          <span><span class="text-[13px] text-white">${l}</span><br><span class="text-[12px] text-zinc-400">${d2}</span></span></label>`).join("")}
      </section>
      <section class="space-y-1.5">
        <h4 class="text-[13px] font-semibold text-white">Cloud model</h4>
        <p class="text-[12px] ${d.cloud_model ? "text-zinc-300" : "text-amber-300"}">${d.cloud_model ? esc(d.cloud_model) : "No cloud API key yet. Add one in Settings (it's kept in your Keychain)."}</p>
        <div class="flex gap-2">
          <select id="rtProvider" class="bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-[12px] text-white">
            ${["", "anthropic", "openai", "gemini", "openrouter", "groq", "fireworks"].map((p) => `<option value="${p}" ${s.provider === p ? "selected" : ""}>${p || "First with a key"}</option>`).join("")}
          </select>
          <input id="rtModel" value="${esc(s.model)}" placeholder="model (optional)" class="flex-1 bg-surface-900 border border-surface-700 rounded-lg px-2.5 py-1.5 text-[12px] text-white">
        </div>
      </section>
      <section class="space-y-2">
        <h4 class="text-[13px] font-semibold text-white">This month</h4>
        <div class="h-2 rounded-full bg-surface-700 overflow-hidden"><div class="h-full ${pct >= 100 ? "bg-red-400" : "bg-brand-500"}" style="width:${pct}%"></div></div>
        <p class="text-[12px] text-zinc-400">$${d.month_spend_usd.toFixed(2)} of $<input id="rtBudget" type="number" min="0" step="1" value="${s.monthly_budget_usd}" class="w-16 bg-surface-900 border border-surface-700 rounded px-1.5 py-0.5 text-white"> budget. Automatic mode stops at the budget.</p>
        ${d.by_model.length ? `<table class="w-full text-[12px] text-zinc-300"><tbody>${d.by_model.map((m) => `<tr class="border-b border-white/[0.04]"><td class="py-1">${esc(m.model)}</td><td class="text-zinc-500">${m.calls} calls</td><td class="text-right">$${m.cost_usd.toFixed(3)}</td></tr>`).join("")}</tbody></table>`
          : `<p class="text-[12px] text-zinc-500">No cloud calls yet this month.</p>`}
        <p class="text-[11px] text-zinc-500">Costs use the provider's token counts and approximate list prices.</p>
      </section>
      <button data-rt-save class="px-4 py-2 rounded-xl bg-brand-500 text-surface-900 text-[13px] font-semibold hover:bg-brand-400">Save</button>`;
  }

  window.openRoutingPanel = () => {
    let root = document.getElementById("routingPanel");
    if (!root) {
      root = document.createElement("div");
      root.id = "routingPanel";
      root.className = "tasks-panel";
      root.innerHTML = `
        <div class="tasks-scrim" data-close-routing></div>
        <aside class="tasks-drawer" role="dialog" aria-label="Model routing">
          <header class="px-5 pt-5 pb-3 flex items-start justify-between gap-3 border-b border-white/[0.06]">
            <div><h3 class="font-bold text-lg text-white">Model routing</h3>
              <p class="text-[12px] text-zinc-400 mt-0.5">Private and free on your Mac by default; a cloud model only when it helps, with your say.</p></div>
            <button data-close-routing class="p-1 -mr-1 rounded-lg text-zinc-500 hover:text-white hover:bg-surface-700" title="Close"><i data-lucide="x" class="size-4"></i></button>
          </header>
          <div id="routingBody" class="flex-1 overflow-y-auto px-5 py-4 space-y-5"></div>
        </aside>`;
      document.body.appendChild(root);
      root.addEventListener("click", async (e) => {
        if (e.target.closest("[data-close-routing]")) {
          root.classList.remove("is-open");
          return setTimeout(() => root.classList.add("hidden"), 250);
        }
        if (e.target.closest("[data-rt-save]")) {
          await api("/api/routing", "POST", {
            mode: root.querySelector("input[name=rtmode]:checked")?.value || "off",
            provider: root.querySelector("#rtProvider").value, model: root.querySelector("#rtModel").value.trim(),
            monthly_budget_usd: parseFloat(root.querySelector("#rtBudget").value) || 0,
          });
          showToast("Saved");
          render();
        }
      });
      if (window.lucide) lucide.createIcons();
    }
    root.classList.remove("hidden");
    requestAnimationFrame(() => root.classList.add("is-open"));
    render();
  };
})();
