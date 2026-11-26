/**
 * Xod Desktop Quick Actions modal.
 * Gives computer users the exact same 1-click Post & Web, Live Chrome Tabs,
 * and Launch App shortcuts as the Xod phone Remote Hub.
 */
(function () {
  const PLATFORMS = [
    { id: "Facebook", url: "https://www.facebook.com" },
    { id: "X", url: "https://x.com" },
    { id: "LinkedIn", url: "https://www.linkedin.com" },
    { id: "Threads", url: "https://www.threads.net" },
    { id: "Instagram", url: "https://www.instagram.com" },
    { id: "Website", url: "" },
  ];

  const QUICK_APPS = [
    "Google Chrome",
    "Safari",
    "Messages",
    "Notes",
    "Calendar",
    "Slack",
    "Terminal",
    "Finder",
    "Visual Studio Code",
  ];

  let activeTab = "post";
  let selectedPlatform = "Facebook";

  function esc(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setStatus(msg, isError) {
    const el = document.getElementById("qaStatus");
    if (!el) return;
    if (!msg) {
      el.textContent = "";
      el.classList.add("hidden");
      return;
    }
    el.textContent = msg;
    el.className = "rounded-lg px-3 py-2 text-[12px] " + (
      isError
        ? "bg-red-500/10 border border-red-500/25 text-red-300"
        : "bg-emerald-500/10 border border-emerald-500/25 text-emerald-300"
    );
    el.classList.remove("hidden");
  }

  function renderPlatforms() {
    const wrap = document.getElementById("qaPlatforms");
    if (!wrap) return;
    wrap.innerHTML = PLATFORMS.map((p) => {
      const on = p.id === selectedPlatform;
      return `<button type="button" data-qa-platform="${esc(p.id)}" class="px-3 py-1.5 rounded-full text-[12px] font-medium border transition-colors ${
        on
          ? "bg-brand-500 text-surface-900 border-brand-400"
          : "bg-surface-800 text-zinc-300 border-white/[0.08] hover:bg-surface-700"
      }">${esc(p.id)}</button>`;
    }).join("");
    wrap.querySelectorAll("[data-qa-platform]").forEach((btn) => {
      btn.addEventListener("click", () => {
        selectedPlatform = btn.getAttribute("data-qa-platform") || "Facebook";
        renderPlatforms();
      });
    });
  }

  function renderApps() {
    const wrap = document.getElementById("qaAppsGrid");
    if (!wrap) return;
    wrap.innerHTML = QUICK_APPS.map((name) => `
      <button type="button" data-qa-app="${esc(name)}" class="px-3 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-white/[0.08] text-left text-[12px] font-medium text-zinc-200 transition-colors truncate">
        ${esc(name)}
      </button>
    `).join("");
    wrap.querySelectorAll("[data-qa-app]").forEach((btn) => {
      btn.addEventListener("click", () => launchDesktopApp(btn.getAttribute("data-qa-app") || ""));
    });
  }

  function showTab(tab) {
    activeTab = tab;
    setStatus("", false);
    ["post", "tabs", "launch"].forEach((id) => {
      const pane = document.getElementById("qaPane_" + id);
      const btn = document.getElementById("qaTab_" + id);
      const on = id === tab;
      if (pane) pane.classList.toggle("hidden", !on);
      if (btn) {
        btn.className = "px-3 py-1.5 rounded-lg text-[12px] font-medium transition-colors " + (
          on
            ? "bg-brand-500 text-surface-900"
            : "bg-surface-800 text-zinc-300 hover:bg-surface-700"
        );
      }
    });
    if (tab === "tabs") refreshChromeTabs();
  }

  async function refreshChromeTabs() {
    const list = document.getElementById("qaTabsList");
    if (!list) return;
    list.innerHTML = `<p class="text-[12px] text-zinc-500 py-3">Loading open Chrome tabs…</p>`;
    try {
      const res = await fetch("/api/desktop/chrome-tabs");
      const data = await res.json();
      const tabs = data.tabs || [];
      if (!tabs.length) {
        list.innerHTML = `<p class="text-[12px] text-zinc-400 py-3">${esc(data.error || "No open Chrome tabs found.")}</p>`;
        return;
      }
      list.innerHTML = tabs.map((t) => {
        const title = t.title || t.url || "Untitled tab";
        const host = (t.url || "").replace(/^https?:\/\//i, "").slice(0, 64);
        return `
          <div class="rounded-lg bg-surface-800/80 border border-white/[0.06] p-2.5 flex items-center gap-2.5">
            <div class="flex-1 min-w-0">
              <div class="text-[13px] font-medium text-zinc-100 truncate">${esc(title)}</div>
              <div class="text-[11px] text-zinc-500 truncate">${esc(host)}</div>
            </div>
            <button type="button" data-qa-focus-w="${t.window}" data-qa-focus-t="${t.tab}" class="px-2.5 py-1 rounded-md bg-surface-700 hover:bg-surface-600 text-[12px] text-zinc-200 shrink-0">Focus</button>
            <button type="button" data-qa-sum-title="${esc(title)}" data-qa-sum-url="${esc(t.url || "")}" class="px-2.5 py-1 rounded-md bg-brand-500/20 hover:bg-brand-500/30 text-[12px] text-brand-200 shrink-0">Summarize</button>
          </div>
        `;
      }).join("");
      list.querySelectorAll("[data-qa-focus-w]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const w = Number(btn.getAttribute("data-qa-focus-w"));
          const t = Number(btn.getAttribute("data-qa-focus-t"));
          try {
            const r = await fetch("/api/desktop/chrome-focus", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ window: w, tab: t }),
            });
            if (!r.ok) throw new Error((await r.json()).detail || "Could not focus tab");
            setStatus("Focused tab in Google Chrome.", false);
          } catch (e) {
            setStatus(String(e.message || e), true);
          }
        });
      });
      list.querySelectorAll("[data-qa-sum-title]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const title = btn.getAttribute("data-qa-sum-title") || "";
          const url = btn.getAttribute("data-qa-sum-url") || "";
          sendToChat(`Read and summarize my open Chrome tab "${title}" (${url}).`);
        });
      });
    } catch (e) {
      list.innerHTML = `<p class="text-[12px] text-red-300 py-3">Could not load Chrome tabs.</p>`;
    }
  }

  function sendToChat(prompt) {
    closeQuickActionsModal();
    if (typeof window.goSurface === "function") window.goSurface("ask");
    if (typeof window.quickSendPrompt === "function") {
      window.quickSendPrompt(prompt);
    } else {
      const input = document.getElementById("chatInput");
      if (input) {
        input.value = prompt;
        input.focus();
      }
    }
  }

  function submitPostAction() {
    const msgEl = document.getElementById("qaPostText");
    const urlEl = document.getElementById("qaPostUrl");
    const msg = (msgEl ? msgEl.value : "").trim();
    const customUrl = (urlEl ? urlEl.value : "").trim();
    if (!msg) {
      setStatus("Write what you want Xod to post or do on the site first.", true);
      return;
    }
    const target = selectedPlatform === "Website" && customUrl ? customUrl : selectedPlatform;
    const urlHint = customUrl && selectedPlatform !== "Website" ? ` Include this link: ${customUrl}.` : "";
    const prompt = `Use my browser on this computer to open ${target} (using my logged-in session), draft and prepare this post, and ask me before clicking publish:${urlHint}\n\n${msg}`;
    if (msgEl) msgEl.value = "";
    sendToChat(prompt);
  }

  async function openUrlAction() {
    const urlEl = document.getElementById("qaPostUrl");
    const url = (urlEl ? urlEl.value : "").trim();
    if (!url) {
      setStatus("Enter a URL to open in Chrome.", true);
      return;
    }
    try {
      const r = await fetch("/api/desktop/open-url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Could not open URL");
      setStatus(`Opened ${data.opened || url} in Chrome.`, false);
    } catch (e) {
      setStatus(String(e.message || e), true);
    }
  }

  async function launchDesktopApp(appName) {
    const name = String(appName || "").trim();
    if (!name) {
      setStatus("Enter an app name to launch.", true);
      return;
    }
    try {
      const r = await fetch("/api/desktop/launch-app", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app: name }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || `Could not open ${name}`);
      setStatus(`Launched ${data.launched || name}.`, false);
    } catch (e) {
      setStatus(String(e.message || e), true);
    }
  }

  function openQuickActionsModal(initialTab) {
    const modal = document.getElementById("quickActionsModal");
    if (!modal) return;
    renderPlatforms();
    renderApps();
    showTab(initialTab || activeTab || "post");
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function closeQuickActionsModal() {
    const modal = document.getElementById("quickActionsModal");
    if (!modal) return;
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
  }

  window.openQuickActionsModal = openQuickActionsModal;
  window.closeQuickActionsModal = closeQuickActionsModal;
  window.showQuickActionsTab = showTab;
  window.submitQuickPostAction = submitPostAction;
  window.openQuickUrlAction = openUrlAction;
  window.refreshQuickChromeTabs = refreshChromeTabs;
  window.launchCustomQuickApp = () => {
    const el = document.getElementById("qaCustomAppInput");
    if (el) launchDesktopApp(el.value);
  };
})();
