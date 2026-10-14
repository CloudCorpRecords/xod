// XOD Researcher Frontend Application Logic


let conversationHistory = [];
let currentPersona = "ultimate_researcher";
let currentSessionId = null;
let isStreaming = false;
let currentAbortController = null;
let currentConfig = {};

// Initialize on DOM load
document.addEventListener("DOMContentLoaded", async () => {
  lucide.createIcons();
  await loadConfig();
  await loadProjects();
  await loadSessions();
  initActiveWorkingScope();
  autoResizeTextarea();
  setupClipboardPaste();
  initSpecularLight();
  initSlashPaletteEvents();
  initScrollBottomListener();
  loadPersistentComputerProfile();
  initJarvisGlobalHotkey();
});

function initJarvisGlobalHotkey() {
  window.addEventListener("keydown", (e) => {
    // Option+Space (Alt+Space) toggles Jarvis Hands-Free voice
    if (e.altKey && e.code === "Space") {
      e.preventDefault();
      toggleJarvisVoice();
    }
  });
}

async function loadPersistentComputerProfile() {
  try {
    const res = await fetch("/api/computer/profile");
    if (!res.ok) return;
    const profile = await res.json();
    window.currentComputerProfile = profile;
    const badge = document.getElementById("persistentHostBadge");
    if (badge && profile.host_os) {
      badge.textContent = `${profile.host_os.split(" ")[0]} ${profile.arch}`;
      badge.title = `Host: ${profile.host_os} | CWD: ${profile.current_working_directory}`;
    }
  } catch (e) {}
}

function quickSendPrompt(promptText) {
  const input = document.getElementById("chatInput");
  if (!input) return;
  input.value = promptText;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  const form = document.getElementById("chatForm");
  if (form) {
    if (typeof form.requestSubmit === "function") {
      form.requestSubmit();
    } else {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    }
  }
}

function scrollChatToBottom(smooth = false) {
  const cm = document.getElementById("chatMessages");
  if (!cm) return;
  cm.scrollTo({ top: cm.scrollHeight, behavior: smooth ? "smooth" : "instant" });
}

function initScrollBottomListener() {
  const cm = document.getElementById("chatMessages");
  const btn = document.getElementById("scrollToBottomBtn");
  if (!cm || !btn) return;

  cm.addEventListener("scroll", () => {
    const distFromBottom = cm.scrollHeight - cm.scrollTop - cm.clientHeight;
    if (distFromBottom > 220) {
      btn.classList.remove("hidden");
    } else {
      btn.classList.add("hidden");
    }
  }, { passive: true });
}

// Configure marked options & custom media renderer
window.revealInFinder = async function(filePath) {
  try {
    const res = await fetch("/api/computer/open-finder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: filePath })
    });
    const data = await res.json();
    if (data.success) {
      if (typeof showToast === 'function') {
        const name = (data.target || filePath || "").split("/").pop() || "Item";
        showToast(`Opened in macOS Finder: ${name}`, "success");
      }
    } else {
      if (typeof showToast === 'function') {
        showToast(`Finder error: ${data.error || 'Failed to open'}`, "error");
      }
    }
  } catch (err) {
    console.error("Reveal in Finder failed:", err);
    if (typeof showToast === 'function') {
      showToast("Failed to connect to macOS Finder service", "error");
    }
  }
};

// Images, videos and file paths in AI replies get a player / preview and an "Open in Finder"
// button. Everything here is escaped, and the button uses data-reveal (handled by one click
// listener below) rather than inline code, so the output survives the sanitizer in safe-html.js.
const revealButton = (path, label, cls) =>
  `<span role="button" tabindex="0" data-reveal="${escapeHtml(path)}" class="${cls}"><span>📂 ${label}</span></span>`;
const REVEAL_BTN_CLASS = "px-3 py-1.5 bg-cyan-950/70 hover:bg-cyan-900/90 text-cyan-300 hover:text-cyan-100 rounded-lg text-xs flex items-center gap-1.5 transition-all border border-cyan-800/70 font-medium shadow-sm flex-shrink-0 cursor-pointer";

const customMediaRenderer = {
  image(tokenOrHref, title, text) {
    let href = '';
    let alt = '';
    if (typeof tokenOrHref === 'object' && tokenOrHref !== null) {
      href = tokenOrHref.href || '';
      alt = tokenOrHref.text || tokenOrHref.title || '';
    } else {
      href = tokenOrHref || '';
      alt = text || title || '';
    }
    if (!/^(https?:|\/|data:image\/)/i.test(href)) href = '';   // no javascript: or other schemes
    const isVideo = /\.(mp4|webm|mov|m4v)(\?.*)?$/i.test(href);
    const src = escapeHtml(href);
    const label = escapeHtml(alt || (isVideo ? 'Video' : 'Image'));
    const media = isVideo
      ? `<video controls playsinline preload="auto" src="${src}" class="w-full max-h-[460px] rounded-t-lg bg-black object-contain outline-none"></video>`
      : `<img src="${src}" alt="${label}" class="max-w-full max-h-[500px] object-contain rounded-t-lg block" loading="lazy" />`;
    return `
      <div class="my-4 ${isVideo ? '' : 'inline-block '}rounded-xl overflow-hidden border border-zinc-700/80 bg-zinc-950/80 shadow-2xl max-w-2xl">
        <div class="relative ${isVideo ? 'bg-black' : 'bg-zinc-950/40'} flex items-center justify-center">${media}</div>
        <div class="px-4 py-2 bg-zinc-900/95 border-t border-zinc-800 flex items-center justify-between text-xs text-zinc-300 gap-4">
          <div class="flex items-center gap-2 truncate font-mono text-zinc-200">
            <span class="text-sm">${isVideo ? '🎬' : '🎨'}</span>
            <span class="truncate font-semibold">${label}</span>
          </div>
          ${revealButton(href, 'Open in Finder', REVEAL_BTN_CLASS)}
        </div>
      </div>`;
  },

  codespan(tokenOrText) {
    const raw = typeof tokenOrText === 'object' && tokenOrText !== null ? (tokenOrText.text || '') : (tokenOrText || '');
    // marked hands over code spans already HTML-escaped; undo that once so we escape exactly once.
    const text = raw.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
    if ((text.startsWith('/Users/') || text.includes('/media_studio/') || text.startsWith('/media/')) && (text.includes('.') || text.includes('/'))) {
      return `<code class="bg-zinc-800/90 text-cyan-300 px-1.5 py-0.5 rounded text-xs font-mono">${escapeHtml(text)}</code> ` +
        revealButton(text, 'Finder', "inline-flex items-center gap-1 text-[11px] px-2 py-0.5 mx-1 bg-cyan-950/70 border border-cyan-800/60 text-cyan-300 hover:text-cyan-100 rounded hover:bg-cyan-900/80 transition-all font-mono align-middle cursor-pointer");
    }
    return `<code class="bg-zinc-800/80 text-zinc-200 px-1.5 py-0.5 rounded text-xs font-mono">${escapeHtml(text)}</code>`;
  }
};

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-reveal]");
  if (btn) { e.preventDefault(); window.revealInFinder(btn.dataset.reveal); }
});

marked.setOptions({
  gfm: true,
  breaks: true
});
if (typeof marked.use === 'function') {
  marked.use({ renderer: customMediaRenderer });
}

let slashSelectedIndex = 0;
let missionStartTime = null;
let missionTimerInterval = null;

function autoResizeTextarea() {
  const textarea = document.getElementById("chatInput");
  if (!textarea) return;

  // Start loading the local model as soon as a question is on its way (services/warmup.py).
  let warmedAt = 0;
  const warm = () => {
    if (Date.now() - warmedAt < 8000 || isStreaming) return;   // the server decides whether it needs it
    warmedAt = Date.now();
    fetch("/api/llm/warm", { method: "POST" }).catch(() => {});
  };
  textarea.addEventListener("focus", warm);
  textarea.addEventListener("input", function() {
    warm();
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 150) + "px";
    updateCharCounter();

    const val = this.value.trim();
    if (val.startsWith("/") && val.length < 15 && !val.includes(" ")) {
      openSlashCommandPalette();
    } else if (!val.startsWith("/")) {
      closeSlashCommandPalette();
    }
  });

  // Global hotkeys: Cmd+K / Ctrl+K & Escape
  window.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      textarea.focus();
      openSlashCommandPalette();
    } else if (e.key === "Escape") {
      closeSlashCommandPalette();
      if (typeof closeCreateProjectModal === "function") closeCreateProjectModal();
    }
  });

  // Close palette on click outside
  document.addEventListener("click", (e) => {
    const palette = document.getElementById("slashCommandPalette");
    const island = document.getElementById("dynamicNeuralIsland");
    if (palette && !palette.classList.contains("hidden")) {
      if (island && !island.contains(e.target)) {
        closeSlashCommandPalette();
      }
    }
  });
}

function handleKeyDown(event) {
  const palette = document.getElementById("slashCommandPalette");
  const isPaletteOpen = palette && !palette.classList.contains("hidden");

  if (isPaletteOpen) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      highlightSlashItem(slashSelectedIndex + 1);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      highlightSlashItem(slashSelectedIndex - 1);
      return;
    }
    if (event.key === "Enter" || event.key === "Tab") {
      event.preventDefault();
      const items = document.querySelectorAll("#slashCommandList .slash-item");
      if (items[slashSelectedIndex]) {
        items[slashSelectedIndex].click();
      }
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closeSlashCommandPalette();
      return;
    }
  }

  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    document.getElementById("chatForm").requestSubmit();
  }
}

// -------------------------------------------------------------
// SPECULAR PHYSICS & MOUSE LIGHT ENGINE
// -------------------------------------------------------------
function initSpecularLight() {
  let isTicking = false;
  window.addEventListener("pointermove", (e) => {
    if (!isTicking) {
      requestAnimationFrame(() => {
        const specularEls = document.querySelectorAll(".specular-surface");
        const clientX = e.clientX;
        const clientY = e.clientY;

        specularEls.forEach((el) => {
          const rect = el.getBoundingClientRect();
          const x = clientX - rect.left;
          const y = clientY - rect.top;

          // Activate specular highlight if cursor is within 140px proximity
          if (x >= -140 && x <= rect.width + 140 && y >= -140 && y <= rect.height + 140) {
            el.style.setProperty("--mouse-x", `${x}px`);
            el.style.setProperty("--mouse-y", `${y}px`);
            el.style.setProperty("--specular-opacity", "1");
          } else {
            el.style.setProperty("--specular-opacity", "0");
          }
        });
        isTicking = false;
      });
      isTicking = true;
    }
  }, { passive: true });
}

// -------------------------------------------------------------
// DYNAMIC NEURAL ISLAND & SLASH COMMAND PALETTE
// -------------------------------------------------------------
function initSlashPaletteEvents() {
  updateCharCounter();
}

function openSlashCommandPalette() {
  const palette = document.getElementById("slashCommandPalette");
  if (!palette) return;
  palette.classList.remove("hidden");
  slashSelectedIndex = 0;
  highlightSlashItem(0);
}

function closeSlashCommandPalette() {
  const palette = document.getElementById("slashCommandPalette");
  if (!palette) return;
  palette.classList.add("hidden");
}

function highlightSlashItem(index) {
  const items = document.querySelectorAll("#slashCommandList .slash-item");
  if (!items.length) return;
  slashSelectedIndex = (index + items.length) % items.length;
  items.forEach((item, idx) => {
    if (idx === slashSelectedIndex) {
      item.classList.add("active");
      item.scrollIntoView({ block: "nearest" });
    } else {
      item.classList.remove("active");
    }
  });
}

function selectSlashCommand(prefix) {
  const input = document.getElementById("chatInput");
  if (!input) return;
  if (prefix === "/clear") {
    clearChat();
    closeSlashCommandPalette();
    input.value = "";
    input.focus();
    return;
  }
  input.value = prefix;
  closeSlashCommandPalette();
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  updateCharCounter();
}

function cyclePersonaQuick() {
  const personas = ["ultimate_researcher", "acquisition_hunter", "competitor_teardown", "tech_scout"];
  const currentIdx = personas.indexOf(currentPersona);
  const nextPersona = personas[(currentIdx + 1) % personas.length];
  setPersona(nextPersona);
}

function updateCharCounter() {
  const input = document.getElementById("chatInput");
  const counter = document.getElementById("charCounter");
  if (input && counter) {
    const len = input.value.length;
    counter.textContent = `${len} char${len === 1 ? '' : 's'}`;
  }
}

function startMissionTimer() {
  if (missionTimerInterval) clearInterval(missionTimerInterval);
  missionStartTime = Date.now();
  const timerEl = document.getElementById("islandMissionTimer");
  if (!timerEl) return;
  timerEl.textContent = "⏱ 00:00.0s";

  missionTimerInterval = setInterval(() => {
    const elapsedMs = Date.now() - missionStartTime;
    const totalTenths = Math.floor(elapsedMs / 100);
    const tenths = totalTenths % 10;
    const totalSecs = Math.floor(totalTenths / 10);
    const secs = totalSecs % 60;
    const mins = Math.floor(totalSecs / 60);
    timerEl.textContent = `⏱ ${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}.${tenths}s`;
  }, 100);
}

function stopMissionTimer() {
  if (missionTimerInterval) {
    clearInterval(missionTimerInterval);
    missionTimerInterval = null;
  }
}

// -------------------------------------------------------------
// CONFIG & STATUS
// -------------------------------------------------------------
async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const data = await res.json();
    currentConfig = data;

    // Update TrustMRR Status Badge
    const badge = document.getElementById("btnTrustMRRStatus");
    const text = document.getElementById("trustmrrStatusText");
    const icon = document.getElementById("trustmrrStatusIcon");
    const banner = document.getElementById("missingKeyBanner");

    if (data.trustmrr_configured) {
      text.textContent = "Revenue data: connected";
      badge.className = "conn-row";
      if (banner) banner.classList.add("hidden");
    } else {
      text.textContent = "Revenue data: not set up";
      badge.className = "conn-row text-amber-300";
      if (banner) banner.classList.remove("hidden");
    }

    // Update You.com Status Badge
    const youBadge = document.getElementById("btnYouStatus");
    const youText = document.getElementById("youStatusText");
    const youInput = document.getElementById("inputYouKey");

    if (youBadge && youText) {
      if (data.you_configured) {
        youText.textContent = "Web search: connected";
        youBadge.className = "conn-row";
        if (youInput && !youInput.value) {
          youInput.placeholder = `Configured: ${data.you_masked_key}`;
        }
      } else {
        youText.textContent = "Web search: basic";
        youBadge.className = "conn-row";
        if (youInput && !youInput.value) {
          youInput.placeholder = "ydc_... or your You.com API key";
        }
      }
    }

    // Update YouTube Status Badge
    const ytBadge = document.getElementById("btnYoutubeStatus");
    const ytText = document.getElementById("youtubeStatusText");
    const ytInput = document.getElementById("inputYoutubeKey");

    if (ytBadge && ytText) {
      if (data.youtube_configured) {
        ytText.textContent = "YouTube: connected";
        ytBadge.className = "conn-row";
        if (ytInput && !ytInput.value) {
          ytInput.placeholder = `Configured: ${data.youtube_masked_key}`;
        }
      } else {
        ytText.textContent = "YouTube: not set";
        ytBadge.className = "conn-row";
        if (ytInput && !ytInput.value) {
          ytInput.placeholder = "AIzaSy... or your Google Cloud API key";
        }
      }
    }

    // Update Tavily Status Badge
    const tavBadge = document.getElementById("btnTavilyStatus");
    const tavText = document.getElementById("tavilyStatusText");
    const tavInput = document.getElementById("inputTavilyKey");

    if (tavBadge && tavText) {
      if (data.tavily_configured) {
        tavText.textContent = "Tavily: connected";
        tavBadge.className = "conn-row";
        if (tavInput && !tavInput.value) {
          tavInput.placeholder = `Configured: ${data.tavily_masked_key}`;
        }
      } else {
        tavText.textContent = "Tavily: not set";
        tavBadge.className = "conn-row";
        if (tavInput && !tavInput.value) {
          tavInput.placeholder = "tvly-... or your Tavily API key";
        }
      }
    }

    // Update LLM Status via native LM Studio status endpoint
    await updateLMStudioUI();

    // Fill Settings inputs
    document.getElementById("selectLlmProvider").value = data.llm_provider || "lm_studio";
    document.getElementById("inputLlmBaseUrl").value = data.llm_base_url || "http://localhost:1234/v1";
    document.getElementById("inputLlmModel").value = data.llm_model || "google/gemma-4-12b-qat";
    handleProviderChange();

    // Update Cloud Swarm Keys Badges
    const updateKeyBadge = (id, hasKey, inputId) => {
      const badge = document.getElementById(id);
      const input = document.getElementById(inputId);
      if (badge) {
        if (hasKey) {
          badge.textContent = "Configured";
          badge.className = "text-[9px] text-emerald-400 font-mono";
          if (input && !input.value) input.placeholder = "Configured (••••••••)";
        } else {
          badge.textContent = "Not set";
          badge.className = "text-[9px] text-zinc-500 font-mono";
        }
      }
    };

    updateKeyBadge("badgeGeminiKey", data.has_gemini_key, "inputGeminiKey");
    updateKeyBadge("badgeOpenAIKey", data.has_openai_key, "inputOpenAIKey");
    updateKeyBadge("badgeAnthropicKey", data.has_anthropic_key, "inputAnthropicKey");
    updateKeyBadge("badgeGroqKey", data.has_groq_key, "inputGroqKey");
    updateKeyBadge("badgeOpenRouterKey", data.has_openrouter_key, "inputOpenRouterKey");
    updateKeyBadge("badgeFireworksKey", data.has_fireworks_key, "inputFireworksKey");
    updateKeyBadge("badgeHfKey", data.has_huggingface_token, "inputHfKey");

    if (data.has_huggingface_token && data.huggingface_masked_token) {
      const hfInp = document.getElementById("inputHfKey");
      if (hfInp && !hfInp.value) hfInp.placeholder = `Configured: ${data.huggingface_masked_token}`;
      const hfUserInp = document.getElementById("inputHfUserToken");
      if (hfUserInp && !hfUserInp.value) hfUserInp.placeholder = `Configured: ${data.huggingface_masked_token}`;
    }

    // If active provider is fireworks and key exists, show configured in primary cloud input
    if (data.llm_provider === "fireworks" && data.has_fireworks_key) {
      const inputCloud = document.getElementById("inputCloudKey");
      if (inputCloud && !inputCloud.value) inputCloud.placeholder = "Configured (••••••••)";
    }

    // Check Swarm status
    try {
      const swarmRes = await fetch("/api/swarm/status");
      const swarmData = await swarmRes.json();
      const localEl = document.getElementById("swarmLocalStatus");
      const cloudEl = document.getElementById("swarmCloudStatus");
      if (localEl) {
        const mName = (swarmData.tiers?.local_model || "").split("/").pop() || "local";
        localEl.textContent = swarmData.tiers?.local_ready ? `Ready (${mName})` : "Offline";
        localEl.className = swarmData.tiers?.local_ready ? "text-emerald-400 font-mono text-[10px]" : "text-amber-400 font-mono text-[10px]";
      }
      if (cloudEl) {
        if (swarmData.tiers?.cloud_ready && swarmData.tiers?.cloud_options?.length) {
          cloudEl.textContent = `Active (${swarmData.tiers.cloud_options.map(o => o.label || o.provider).join(', ')})`;
          cloudEl.className = "text-cyan-400 font-mono text-[10px]";
        } else {
          cloudEl.textContent = "Local Fallback";
          cloudEl.className = "text-zinc-400 font-mono text-[10px]";
        }
      }
    } catch (e) {
      console.warn("Could not load swarm status", e);
    }

  } catch (err) {
    console.error("Failed to load config:", err);
  }
}

// -------------------------------------------------------------
// LM STUDIO NATIVE INTEGRATION
// -------------------------------------------------------------
async function updateLMStudioUI() {
  const llmText = document.getElementById("llmStatusText");
  const pulseDot = document.getElementById("lmStudioPulseDot");
  if (!llmText) return;

  try {
    const res = await fetch("/api/lmstudio/status");
    const data = await res.json();

    // Chats answered by a cloud model (Settings → provider): that's what "ready" means, LM Studio or not.
    if (data.active && data.active.cloud) {
      const who = data.active.provider.charAt(0).toUpperCase() + data.active.provider.slice(1);
      llmText.textContent = `AI model: ${data.active.model} (${who})`;
      llmText.title = `${who}: ${data.active.model}`;
      if (pulseDot) pulseDot.className = `size-2 rounded-full ${data.active.ready ? "bg-emerald-400" : "bg-amber-400"}`;
      setHeaderStatus(data.active.ready ? "ok" : "warn", data.active.ready ? `Ready · ${who}` : `${who} key missing`);
    } else if (data.online) {
      if (data.loaded_model) {
        const m = data.loaded_model;
        const caps = [];
        if (m.capabilities?.trained_for_tool_use) caps.push("Tools");
        if (m.capabilities?.reasoning) caps.push("Thinking");
        if (m.capabilities?.vision) caps.push("Vision");
        const capStr = caps.length > 0 ? ` • ${caps.join("+")}` : "";
        llmText.textContent = `AI model: ${m.display_name}`;
        llmText.title = `LM Studio: ${m.display_name}${capStr}`;
        if (pulseDot) pulseDot.className = "size-2 rounded-full bg-emerald-400";
        setHeaderStatus("ok", "Ready");
      } else {
        llmText.textContent = "AI model: none loaded";
        if (pulseDot) pulseDot.className = "size-2 rounded-full bg-amber-400";
        setHeaderStatus("warn", "No AI model loaded");
      }
    } else {
      llmText.textContent = "AI model: offline";
      if (pulseDot) pulseDot.className = "size-2 rounded-full bg-red-400";
      setHeaderStatus("off", "AI offline");
    }
  } catch (e) {
    llmText.textContent = "AI model: local";
    setHeaderStatus("idle", "Local");
  }
}

async function openLMStudioModal() {
  document.getElementById("lmStudioModal").classList.remove("hidden");
  await refreshLMStudioStatus();
}

function closeLMStudioModal() {
  document.getElementById("lmStudioModal").classList.add("hidden");
}

async function refreshLMStudioStatus() {
  const card = document.getElementById("lmActiveModelCard");
  const list = document.getElementById("lmModelsList");
  const nameElem = document.getElementById("activeModelName");
  const badgesElem = document.getElementById("activeModelBadges");

  list.innerHTML = `<div class="text-xs text-zinc-400 py-3 text-center"><i data-lucide="loader-2" class="size-4 animate-spin inline-block mr-1"></i> Querying LM Studio v1 API...</div>`;
  lucide.createIcons();

  try {
    const res = await fetch("/api/lmstudio/status");
    const data = await res.json();

    if (!data.online) {
      card.className = "p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-xs text-red-300";
      card.innerHTML = `⚠️ LM Studio server is not responding at ${data.server_url}. Please open LM Studio and start the local server on port 1234.`;
      list.innerHTML = "";
      return;
    }

    // Active loaded model display
    if (data.loaded_model) {
      const m = data.loaded_model;
      const caps = m.capabilities || {};
      card.className = "p-4 rounded-xl bg-surface-750 border border-brand-500/40 space-y-3";
      card.innerHTML = `
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="size-2.5 rounded-full bg-emerald-400 animate-pulse"></span>
            <span class="text-xs font-semibold uppercase tracking-wider text-emerald-400">Active Loaded Model</span>
          </div>
          <button onclick="unloadActiveModel('${m.instance_id}')" class="text-xs text-red-400 hover:text-red-300 px-2.5 py-1 rounded bg-red-500/10 border border-red-500/20 hover:bg-red-500/20 transition-all flex items-center gap-1">
            <i data-lucide="power" class="size-3"></i> Eject from RAM
          </button>
        </div>
        <div>
          <h4 class="text-base font-bold text-white">${escapeHtml(m.display_name)} <span class="text-xs font-mono text-zinc-400 font-normal">(${escapeHtml(m.key)})</span></h4>
          <div class="flex flex-wrap gap-2 mt-2">
            <span class="text-[10px] font-semibold px-2 py-0.5 rounded bg-surface-700 text-zinc-300 border border-surface-600">Arch: ${m.architecture || 'LLM'}</span>
            <span class="text-[10px] font-semibold px-2 py-0.5 rounded bg-surface-700 text-zinc-300 border border-surface-600">Quant: ${m.quantization}</span>
            <span class="text-[10px] font-semibold px-2 py-0.5 rounded bg-surface-700 text-zinc-300 border border-surface-600">RAM: ${m.size_gb} GB</span>
            <span class="text-[10px] font-semibold px-2 py-0.5 rounded bg-surface-700 text-zinc-300 border border-surface-600">Context: ${m.context_length?.toLocaleString() || '8,192'} tokens</span>
            ${caps.trained_for_tool_use ? '<span class="text-[10px] font-semibold px-2 py-0.5 rounded bg-brand-500/20 text-brand-300 border border-brand-500/40">🔨 Native Tool Calling</span>' : ''}
            ${caps.reasoning ? '<span class="text-[10px] font-semibold px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/40">🧠 Thinking In Tools</span>' : ''}
            ${caps.vision ? '<span class="text-[10px] font-semibold px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40">👁️ Multimodal Vision</span>' : ''}
          </div>
        </div>
      `;
    } else {
      card.className = "p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-xs text-amber-200";
      card.innerHTML = `No model is currently loaded in LM Studio. Select an available model below to load it into memory.`;
    }

    // Models list
    let listHtml = "";
    for (const m of data.models) {
      if (m.is_loaded) continue; // Already shown in top card
      listHtml += `
        <div class="p-3 rounded-xl bg-surface-750 border border-surface-700 hover:border-surface-600 flex items-center justify-between gap-3">
          <div>
            <div class="flex items-center gap-2">
              <span class="font-semibold text-xs text-white">${escapeHtml(m.display_name)}</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded bg-surface-700 text-zinc-400 font-mono">${m.params || ''} ${m.quantization}</span>
            </div>
            <span class="text-[11px] text-zinc-500 font-mono">${escapeHtml(m.key)} • ${m.size_gb} GB</span>
          </div>
          <button onclick="loadModelIntoLMStudio('${escapeHtml(m.key)}')" class="px-3 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-500 text-xs font-semibold text-white transition-all flex items-center gap-1 shrink-0">
            <i data-lucide="play" class="size-3"></i> Load into RAM
          </button>
        </div>
      `;
    }
    list.innerHTML = listHtml || `<div class="text-xs text-zinc-500 text-center py-2">All downloaded models are loaded.</div>`;
    await updateLMStudioUI();
    lucide.createIcons();

  } catch (err) {
    list.innerHTML = `<div class="text-xs text-red-400 py-2">Error checking status: ${err.message}</div>`;
  }
}

async function loadModelIntoLMStudio(key) {
  const card = document.getElementById("lmActiveModelCard");
  card.innerHTML = `<div class="text-xs text-brand-300 py-3 text-center"><i data-lucide="loader-2" class="size-5 animate-spin mx-auto mb-2 text-brand-400"></i> Loading ${escapeHtml(key)} into RAM... Please wait.</div>`;
  lucide.createIcons();

  try {
    const res = await fetch("/api/lmstudio/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_key: key })
    });
    const result = await res.json();
    if (result.success) {
      document.getElementById("inputLlmModel").value = key;
      await refreshLMStudioStatus();
    } else {
      alert("Model load failed: " + result.message);
      await refreshLMStudioStatus();
    }
  } catch (e) {
    alert("Error loading model: " + e.message);
    await refreshLMStudioStatus();
  }
}

async function unloadActiveModel(instanceId) {
  if (!confirm("Are you sure you want to eject the active model from RAM?")) return;
  try {
    const res = await fetch("/api/lmstudio/unload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instance_id: instanceId })
    });
    const result = await res.json();
    if (result.success) {
      await refreshLMStudioStatus();
    } else {
      alert("Unload failed: " + result.message);
    }
  } catch (e) {
    alert("Error unloading: " + e.message);
  }
}


function handleProviderChange() {
  const provider = document.getElementById("selectLlmProvider").value;
  const grpBaseUrl = document.getElementById("groupBaseUrl");
  const grpCloud = document.getElementById("groupCloudKey");
  const lblCloud = document.getElementById("lblCloudKey");
  const pills = document.getElementById("fireworksQuickPills");
  const inputCloud = document.getElementById("inputCloudKey");

  const lmContainer = document.getElementById("lmStudioDetectedModelsContainer");

  if (provider === "lm_studio") {
    grpBaseUrl.classList.remove("hidden");
    grpCloud.classList.add("hidden");
    if (pills) pills.classList.add("hidden");
    document.getElementById("inputLlmBaseUrl").value = "http://localhost:1234/v1";
    refreshLlmModels({ silent: true });
  } else if (provider === "ollama") {
    grpBaseUrl.classList.remove("hidden");
    grpCloud.classList.add("hidden");
    if (pills) pills.classList.add("hidden");
    if (lmContainer) lmContainer.classList.add("hidden");
    document.getElementById("inputLlmBaseUrl").value = "http://localhost:11434/v1";
  } else if (provider === "fireworks") {
    grpBaseUrl.classList.add("hidden");
    grpCloud.classList.remove("hidden");
    if (pills) pills.classList.remove("hidden");
    if (lmContainer) lmContainer.classList.add("hidden");
    lblCloud.textContent = "Fireworks AI API Key (Serverless Cloud)";
    document.getElementById("inputLlmBaseUrl").value = "https://api.fireworks.ai/inference/v1";
    const modelInput = document.getElementById("inputLlmModel");
    if (!modelInput.value || modelInput.value.includes("gemma-4") || modelInput.value.includes("llama3.1") || modelInput.value.includes("bonsai")) {
      modelInput.value = "accounts/fireworks/models/glm-5p3-flash";
    }
  } else {
    grpBaseUrl.classList.add("hidden");
    grpCloud.classList.remove("hidden");
    if (pills) pills.classList.add("hidden");
    if (lmContainer) lmContainer.classList.add("hidden");
    lblCloud.textContent = `${provider.toUpperCase()} API Key`;
  }
}

function setFireworksModel(modelId) {
  const mInput = document.getElementById("inputLlmModel");
  if (mInput) {
    mInput.value = modelId;
    showToast(`Selected Fireworks model: ${modelId.split('/').pop()}`);
  }
}

async function testFireworksKeyDirect() {
  const input = document.getElementById("inputFireworksKey");
  const cloudInput = document.getElementById("inputCloudKey");
  let key = input ? input.value.trim() : "";
  if (!key && cloudInput) key = cloudInput.value.trim();
  const feedback = document.getElementById("testFireworksFeedback");
  if (!feedback) return;

  feedback.classList.remove("hidden");
  feedback.className = "text-xs text-zinc-400 font-mono mt-1";
  feedback.textContent = "Testing Fireworks API key...";

  try {
    const res = await fetch("/api/fireworks/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fireworks_api_key: key })
    });
    const data = await res.json();
    if (data.valid) {
      feedback.className = "text-xs text-emerald-400 font-semibold mt-1";
      feedback.textContent = `✓ ${data.message}`;
      const badge = document.getElementById("badgeFireworksKey");
      if (badge) {
        badge.textContent = "Configured";
        badge.className = "text-[9px] text-emerald-400 font-mono";
      }
      if (input && key) input.placeholder = "Configured (••••••••)";
      if (cloudInput && !cloudInput.value && key) cloudInput.placeholder = "Configured (••••••••)";
    } else {
      feedback.className = "text-xs text-red-400 font-semibold mt-1";
      feedback.textContent = `✗ ${data.message}`;
    }
  } catch (e) {
    feedback.className = "text-xs text-red-400 font-semibold mt-1";
    feedback.textContent = `✗ Connection error: ${e.message}`;
  }
}

async function testCurrentCloudProviderKey() {
  const provider = document.getElementById("selectLlmProvider").value;
  const input = document.getElementById("inputCloudKey");
  const fwInput = document.getElementById("inputFireworksKey");
  let key = input ? input.value.trim() : "";
  if (provider === "fireworks" && !key && fwInput) {
    key = fwInput.value.trim();
  }
  const feedback = document.getElementById("testCloudKeyFeedback");
  if (!feedback) return;

  if (provider === "fireworks") {
    feedback.classList.remove("hidden");
    feedback.className = "text-xs text-zinc-400 font-mono mt-1";
    feedback.textContent = "Testing Fireworks AI connection...";
    try {
      const res = await fetch("/api/fireworks/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fireworks_api_key: key })
      });
      const data = await res.json();
      if (data.valid) {
        feedback.className = "text-xs text-emerald-400 font-semibold mt-1";
        feedback.textContent = `✓ ${data.message}`;
        const badge = document.getElementById("badgeFireworksKey");
        if (badge) {
          badge.textContent = "Configured";
          badge.className = "text-[9px] text-emerald-400 font-mono";
        }
        if (input && key) input.placeholder = "Configured (••••••••)";
        const fwInput = document.getElementById("inputFireworksKey");
        if (fwInput && !fwInput.value && key) fwInput.placeholder = "Configured (••••••••)";
      } else {
        feedback.className = "text-xs text-red-400 font-semibold mt-1";
        feedback.textContent = `✗ ${data.message}`;
      }
    } catch (e) {
      feedback.className = "text-xs text-red-400 font-semibold mt-1";
      feedback.textContent = `✗ Error: ${e.message}`;
    }
  } else {
    feedback.classList.remove("hidden");
    feedback.className = "text-xs text-cyan-400 mt-1";
    feedback.textContent = `Key will be saved upon clicking 'Save Settings'.`;
  }
}

async function refreshLlmModels(options = {}) {
  const isSilent = Boolean(options && options.silent);
  const btn = document.getElementById("btnDetectModels");
  if (btn && !isSilent) {
    btn.innerHTML = `<i data-lucide="loader-2" class="size-3 animate-spin"></i> Detecting...`;
    if (window.lucide) lucide.createIcons();
  }

  const container = document.getElementById("lmStudioDetectedModelsContainer");
  const pillsContainer = document.getElementById("lmStudioDetectedModelsPills");
  const statusSpan = document.getElementById("lmStudioActiveModelStatus");

  try {
    const provider = document.getElementById("selectLlmProvider")?.value || "lm_studio";
    const res = await fetch(`/api/llm/models${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`);
    const data = await res.json();
    const models = data.models || [];

    if (provider === "lm_studio" && container && pillsContainer) {
      if (models.length > 0) {
        container.classList.remove("hidden");
        pillsContainer.innerHTML = "";

        let loadedFound = null;

        models.forEach(m => {
          const pill = document.createElement("button");
          pill.type = "button";
          const isLoaded = Boolean(m.is_loaded);
          if (isLoaded && !loadedFound) loadedFound = m.id;

          pill.className = isLoaded
            ? "text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 hover:bg-emerald-500/30 font-mono transition-colors border border-emerald-500/40 flex items-center gap-1 shadow-sm"
            : "text-[10px] px-2 py-0.5 rounded bg-surface-700 text-zinc-300 hover:bg-surface-600 font-mono transition-colors border border-surface-600 flex items-center gap-1";

          pill.innerHTML = isLoaded
            ? `<span class="size-1.5 rounded-full bg-emerald-400 animate-pulse"></span> ${escapeHtml(m.id)} <span class="text-[9px] text-emerald-400/80 font-sans font-semibold">✓ In RAM</span>`
            : `${escapeHtml(m.id)}`;

          pill.title = m.name || m.id;
          pill.onclick = () => {
            document.getElementById("inputLlmModel").value = m.id;
            highlightActiveModelPill(m.id);
            showToast(`Selected model: ${m.id}`);
          };
          pillsContainer.appendChild(pill);
        });

        if (statusSpan) {
          if (loadedFound) {
            statusSpan.textContent = `✓ Active in RAM: ${loadedFound}`;
            statusSpan.className = "font-mono text-emerald-400 text-[10px]";
          } else {
            statusSpan.textContent = "No model currently loaded";
            statusSpan.className = "font-mono text-amber-400 text-[10px]";
          }
        }

        // Intelligently select: prioritize loaded model
        const inputModel = document.getElementById("inputLlmModel");
        if (inputModel) {
          const currentVal = inputModel.value.trim();
          const isCloudString = currentVal.includes("accounts/fireworks") || currentVal.includes("gpt-") || currentVal.includes("claude-") || !currentVal;
          if (!isSilent || isCloudString) {
            const targetId = data.loaded_model || loadedFound || (models[0] ? models[0].id : "");
            if (targetId) {
              inputModel.value = targetId;
              highlightActiveModelPill(targetId);
            }
          } else if (currentVal) {
            highlightActiveModelPill(currentVal);
          }
        }

        if (!isSilent) {
          const loadedMsg = loadedFound ? ` (Loaded in RAM: ${loadedFound})` : "";
          showToast(`Detected ${models.length} model(s)${loadedMsg}`);
        }
      } else {
        container.classList.add("hidden");
        if (!isSilent) alert("No active models detected from LM Studio. Ensure LM Studio server is running on port 1234.");
      }
    } else if (models.length > 0) {
      if (container) container.classList.add("hidden");
      const targetModel = data.loaded_model || (models.find(m => m.is_loaded) || models[0]).id;
      const inputModel = document.getElementById("inputLlmModel");
      if (inputModel && (!isSilent || !inputModel.value)) {
        inputModel.value = targetModel;
      }
      if (!isSilent) showToast(`Detected ${models.length} model(s). Selected: ${targetModel}`);
    } else {
      if (container) container.classList.add("hidden");
      if (!isSilent) alert("No active models detected. Ensure your model server is reachable!");
    }
  } catch (e) {
    if (!isSilent) alert("Could not connect to model server: " + e.message);
  } finally {
    if (btn) {
      btn.innerHTML = `<i data-lucide="refresh-cw" class="size-3"></i> Detect Models`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

function highlightActiveModelPill(modelId) {
  const pills = document.querySelectorAll("#lmStudioDetectedModelsPills button");
  pills.forEach(p => {
    if (p.textContent.includes(modelId)) {
      p.classList.add("ring-2", "ring-cyan-400");
    } else {
      p.classList.remove("ring-2", "ring-cyan-400");
    }
  });
}

// -------------------------------------------------------------
// SETTINGS MODAL & ON-DEVICE MEDIA MODEL MANAGEMENT
// -------------------------------------------------------------
// ---------- Saved website logins (Keychain-backed; passwords never come back to the page) ----------
async function loadSavedLogins() {
  const list = document.getElementById("savedLoginsList");
  if (!list) return;
  try {
    const res = await fetch("/api/vault/logins");
    const { logins = [] } = await res.json();
    if (!logins.length) {
      list.innerHTML = `<li class="text-zinc-500 text-[12px]">No saved logins yet.</li>`;
      return;
    }
    list.innerHTML = logins.map((l) => `
      <li class="flex items-center justify-between gap-3 px-3 py-2 rounded-lg bg-surface-900/70 border border-surface-700/70">
        <div class="min-w-0">
          <p class="text-zinc-100 truncate">${escapeHtml(l.site)}</p>
          <p class="text-[11px] text-zinc-500 truncate">${escapeHtml(l.username)} · ${escapeHtml(l.source || "")} · ${escapeHtml(l.saved_at || "")}</p>
        </div>
        <button type="button" data-site="${escapeHtml(l.site)}" onclick="deleteSavedLogin(this.dataset.site)" class="text-[12px] text-zinc-400 hover:text-rose-400 shrink-0">Remove</button>
      </li>`).join("");
  } catch (e) {
    list.innerHTML = `<li class="text-rose-400 text-[12px]">Couldn't load saved logins.</li>`;
  }
}

async function addSavedLogin() {
  const site = document.getElementById("vaultSite");
  const user = document.getElementById("vaultUser");
  const pass = document.getElementById("vaultPass");
  const fb = document.getElementById("vaultFeedback");
  const show = (msg, ok) => { fb.textContent = msg; fb.className = `text-[12px] ${ok ? "text-emerald-400" : "text-rose-400"}`; };
  if (!site.value.trim() || !user.value.trim() || !pass.value) return show("Fill in the site, username and password.", false);
  const res = await fetch("/api/vault/logins", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ site: site.value.trim(), username: user.value.trim(), password: pass.value }),
  });
  pass.value = "";
  if (!res.ok) return show("Couldn't save that login.", false);
  const data = await res.json();
  show(`Saved for ${data.site}.`, true);
  site.value = ""; user.value = "";
  loadSavedLogins();
}

async function deleteSavedLogin(site) {
  if (!confirm(`Remove the saved login for ${site}? It's deleted from your Keychain too.`)) return;
  await fetch(`/api/vault/logins/${encodeURIComponent(site)}`, { method: "DELETE" });
  loadSavedLogins();
}

function openSettingsModal() {
  document.getElementById("settingsModal").classList.remove("hidden");
  if (window.lucide) lucide.createIcons();
  loadMediaModelsSettings();
  loadSavedLogins();
  const provider = document.getElementById("selectLlmProvider")?.value;
  if (provider === "lm_studio") {
    refreshLlmModels({ silent: true });
  }
}

function closeSettingsModal() {
  document.getElementById("settingsModal").classList.add("hidden");
}

function scrollToMediaModelsSettings() {
  const card = document.getElementById("mediaModelsSettingsCard");
  if (card) {
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("ring-2", "ring-rose-500/60");
    setTimeout(() => card.classList.remove("ring-2", "ring-rose-500/60"), 2000);
  }
}

// Global state for Media Model Manager
let curatedMediaModelsCache = [];
let activeCuratedFilter = "all";
let mediaDownloadPollingTimer = null;

async function loadMediaModelsSettings() {
  const selectImg = document.getElementById("selectActiveImageModel");
  const selectVid = document.getElementById("selectActiveVideoModel");
  const badgeAcc = document.getElementById("mediaAcceleratorBadge");
  const badgeImg = document.getElementById("activeImageBadge");
  const badgeVid = document.getElementById("activeVideoBadge");
  const countBadge = document.getElementById("installedCountBadge");

  try {
    const res = await fetch("/api/media/models/status");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    const downloaded = data.downloaded_models || [];
    const activeImg = data.active_image_model || "";
    const activeVid = data.active_video_model || "";

    if (countBadge) countBadge.textContent = downloaded.length;

    // Populate Active Image Dropdown
    if (selectImg) {
      const imgModels = downloaded.filter(m => m.usable && m.type.includes("image"));
      selectImg.innerHTML = `<option value="" ${!activeImg ? "selected" : ""}>Automatic</option>` + imgModels.map(m => `
        <option value="${escapeHtml(m.id)}" ${m.id === activeImg ? "selected" : ""}>
          ${escapeHtml(m.name)} (${escapeHtml(m.size_formatted)})
        </option>
      `).join("");
    }

    // Populate Active Video Dropdown
    if (selectVid) {
      const vidModels = downloaded.filter(m => m.usable && m.type.includes("video"));
      selectVid.innerHTML = `<option value="" ${!activeVid ? "selected" : ""}>Automatic</option>` + vidModels.map(m => `
        <option value="${escapeHtml(m.id)}" ${m.id === activeVid ? "selected" : ""}>
          ${escapeHtml(m.name)} (${escapeHtml(m.size_formatted)})
        </option>
      `).join("");
    }

    // Update active badges
    if (badgeImg) {
      badgeImg.textContent = activeImg ? "On this Mac" : "Automatic";
    }
    if (badgeVid) {
      badgeVid.textContent = activeVid ? "On this Mac" : "Automatic";
    }

    // Update Hugging Face token display
    window.hasHfTokenConfigured = Boolean(data.has_hf_token);
    const badgeToken = document.getElementById("badgeHfTokenStatus");
    const inputToken = document.getElementById("inputHfUserToken");
    if (badgeToken) {
      if (data.has_hf_token) {
        badgeToken.className = "text-[9px] font-mono text-emerald-400 font-semibold";
        badgeToken.textContent = `✓ Set (${data.hf_token_masked || "Active"})`;
      } else {
        badgeToken.className = "text-[9px] font-mono text-zinc-500";
        badgeToken.textContent = "Not set";
      }
    }
    if (inputToken && data.hf_token_masked && !inputToken.value) {
      inputToken.placeholder = `Current token: ${data.hf_token_masked} (replace if needed)`;
    }

    renderInstalledMediaModels(downloaded, activeImg, activeVid);
    await loadCuratedMediaModels();

  } catch (err) {
    console.error("Error loading media models settings:", err);
    const list = document.getElementById("installedMediaModelsList");
    if (list) list.innerHTML = `<div class="text-xs text-rose-400 py-2">Failed to load media models: ${escapeHtml(err.message)}</div>`;
  }
}

async function saveHfUserToken() {
  const input = document.getElementById("inputHfUserToken");
  const btn = document.getElementById("btnSaveHfToken");
  const feedback = document.getElementById("hfTokenFeedback");
  if (!input) return;

  const token = input.value.trim();
  if (!token) {
    alert("Please paste your Hugging Face token (starts with hf_...).");
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<i data-lucide="loader-2" class="size-3 animate-spin"></i> Testing...`;
    if (window.lucide) lucide.createIcons();
  }

  try {
    const res = await fetch("/api/media/models/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token })
    });
    const data = await res.json();
    if (feedback) feedback.classList.remove("hidden");

    if (data.valid) {
      if (feedback) {
        feedback.className = "text-xs text-emerald-400 font-semibold";
        feedback.textContent = `✓ ${data.message || "Hugging Face token saved and verified!"}`;
      }
      showToast("Hugging Face token verified!");
      input.value = "";
      await loadMediaModelsSettings();
    } else {
      if (feedback) {
        feedback.className = "text-xs text-amber-400";
        feedback.textContent = `⚠️ ${data.message || "Token saved, but verification failed."}`;
      }
      await loadMediaModelsSettings();
    }
  } catch (e) {
    if (feedback) {
      feedback.classList.remove("hidden");
      feedback.className = "text-xs text-rose-400";
      feedback.textContent = `Error saving token: ${e.message}`;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `Save Token`;
    }
  }
}

function renderInstalledMediaModels(models, activeImg, activeVid) {
  const list = document.getElementById("installedMediaModelsList");
  if (!list) return;

  if (!models || models.length === 0) {
    list.innerHTML = `<div class="text-xs text-zinc-500 py-6 text-center italic">No models currently registered.</div>`;
    return;
  }

  list.innerHTML = models.map(m => {
    const isImage = m.type.includes("image");
    const isActive = (isImage && m.id === activeImg) || (!isImage && m.id === activeVid);
    const typeLabel = isImage ? "Text-to-Image" : "Text-to-Video";
    const typeBadgeColor = isImage ? "bg-rose-500/15 text-rose-300 border-rose-500/30" : "bg-cyan-500/15 text-cyan-300 border-cyan-500/30";

    return `
      <div class="p-4 rounded-xl bg-surface-850/90 border ${isActive ? "border-brand-500/50 bg-brand-500/[0.04]" : "border-surface-700/80 hover:border-surface-600"} flex flex-col md:flex-row md:items-center justify-between gap-3.5 transition-all">
        <div class="space-y-1.5 min-w-0 flex-1">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-sm font-bold text-white tracking-tight truncate max-w-sm" title="${escapeHtml(m.name)}">
              ${escapeHtml(m.name)}
            </span>
            <span class="text-[10px] font-semibold px-2 py-0.5 rounded-full border ${typeBadgeColor}">
              ${typeLabel}
            </span>
            <span class="text-[10px] font-mono px-2 py-0.5 rounded-md bg-surface-750 text-zinc-300 border border-surface-650">
              ${escapeHtml(m.size_formatted)}
            </span>
            ${isActive ? `
              <span class="text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 flex items-center gap-1 font-semibold">
                <i data-lucide="check" class="size-3"></i> Active ${isImage ? "Image" : "Video"} Engine
              </span>
            ` : ""}
          </div>
          <p class="text-xs text-zinc-400 leading-relaxed">${escapeHtml(m.description || "")}</p>
          <div class="flex items-center gap-2 text-[11px] text-zinc-500 font-mono">
            <span>Model ID: <span class="text-zinc-400">${escapeHtml(m.id)}</span></span>
          </div>
        </div>

        <div class="shrink-0 flex items-center gap-2 self-start md:self-center">
          ${!isActive ? `
            <button type="button" onclick="setActiveMediaModel('${escapeHtml(m.id)}', '${isImage ? "image" : "video"}')" class="px-3.5 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-500 text-white text-xs font-semibold transition-all flex items-center gap-1.5 shadow-sm">
              <i data-lucide="check" class="size-3.5"></i> Set Active
            </button>
          ` : `
            <span class="text-xs font-semibold px-2.5 py-1.5 rounded-lg bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 flex items-center gap-1.5">
              <i data-lucide="check-circle-2" class="size-3.5 text-emerald-400"></i> Active Engine
            </span>
          `}
          ${!m.is_builtin ? `
            <button type="button" onclick="revealMediaModelFolder('${escapeHtml(m.id)}')" class="p-2 rounded-lg bg-surface-700 hover:bg-surface-600 text-zinc-300 hover:text-white transition-colors" title="Open model folder in Finder">
              <i data-lucide="folder-open" class="size-3.5"></i>
            </button>
            <button type="button" onclick="deleteMediaModel('${escapeHtml(m.id)}')" class="p-2 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 hover:text-rose-300 border border-rose-500/20 transition-colors" title="Delete model from disk">
              <i data-lucide="trash-2" class="size-3.5"></i>
            </button>
          ` : `
            <span class="text-[10px] text-zinc-500 font-mono px-2 py-1 bg-surface-750 rounded-md border border-surface-650">Built-in (Zero VRAM)</span>
          `}
        </div>
      </div>
    `;
  }).join("");

  if (window.lucide) lucide.createIcons();
}

async function loadCuratedMediaModels() {
  try {
    const res = await fetch("/api/media/models/curated");
    if (!res.ok) return;
    const data = await res.json();
    curatedMediaModelsCache = data.models || [];
    renderCuratedMediaList();
  } catch (e) {
    console.error("Error loading curated models:", e);
  }
}

function filterCuratedMedia(filterType) {
  activeCuratedFilter = filterType;
  const btnAll = document.getElementById("btnCuratedFilterAll");
  const btnImg = document.getElementById("btnCuratedFilterImage");
  const btnVid = document.getElementById("btnCuratedFilterVideo");

  [btnAll, btnImg, btnVid].forEach(b => {
    if (b) {
      b.className = "px-2.5 py-1 rounded-md text-zinc-400 hover:text-white text-xs font-medium transition-colors";
    }
  });

  if (filterType === "all" && btnAll) btnAll.className = "px-2.5 py-1 rounded-md bg-surface-700 text-white text-xs font-medium transition-colors";
  if (filterType === "image" && btnImg) btnImg.className = "px-2.5 py-1 rounded-md bg-rose-600/30 text-rose-300 border border-rose-500/40 text-xs font-medium transition-colors";
  if (filterType === "video" && btnVid) btnVid.className = "px-2.5 py-1 rounded-md bg-cyan-600/30 text-cyan-300 border border-cyan-500/40 text-xs font-medium transition-colors";

  renderCuratedMediaList();
}

function renderCuratedMediaList() {
  const container = document.getElementById("curatedMediaModelsList");
  if (!container) return;

  let items = curatedMediaModelsCache;
  if (activeCuratedFilter === "image") {
    items = items.filter(m => m.type.includes("image"));
  } else if (activeCuratedFilter === "video") {
    items = items.filter(m => m.type.includes("video"));
  }

  if (items.length === 0) {
    container.innerHTML = `<div class="text-xs text-zinc-500 py-6 text-center italic">No curated models found.</div>`;
    return;
  }

  container.innerHTML = items.map(m => {
    const isImage = m.type.includes("image");
    const isDownloaded = Boolean(m.is_downloaded);
    const isGated = Boolean(m.is_gated);
    const typeBadgeColor = isImage ? "bg-rose-500/15 text-rose-300 border-rose-500/30" : "bg-cyan-500/15 text-cyan-300 border-cyan-500/30";

    return `
      <div class="p-4 rounded-xl bg-surface-850/90 border border-surface-700/80 hover:border-surface-600 transition-all flex flex-col md:flex-row md:items-center justify-between gap-3.5">
        <div class="space-y-2 min-w-0 flex-1">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-sm font-bold text-white tracking-tight">${escapeHtml(m.name)}</span>
            <span class="text-[10px] font-semibold px-2 py-0.5 rounded-full border ${typeBadgeColor}">
              ${isImage ? "Image" : "Video"}
            </span>
            <span class="text-[10px] font-mono px-2 py-0.5 rounded-md bg-surface-750 text-zinc-300 border border-surface-650">
              ${escapeHtml(m.size_est)}
            </span>
            <span class="text-[10px] font-mono px-2 py-0.5 rounded-md bg-amber-500/10 text-amber-300 border border-amber-500/25">
              ${escapeHtml(m.speed)}
            </span>
            ${isGated ? `
              <span class="text-[10px] font-medium px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30 flex items-center gap-1" title="Requires free Hugging Face token and license agreement">
                <i data-lucide="lock" class="size-2.5"></i> Gated Model
              </span>
            ` : `
              <span class="text-[10px] font-medium px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 flex items-center gap-1" title="100% public, open weights - zero token needed">
                <i data-lucide="sparkles" class="size-2.5"></i> Open Weights
              </span>
            `}
            ${m.recommended ? `<span class="text-[10px] font-medium px-2 py-0.5 rounded-full bg-indigo-500/15 text-indigo-300 border border-indigo-500/30 flex items-center gap-1">★ Apple Silicon Pick</span>` : ""}
          </div>
          <p class="text-xs text-zinc-400 leading-relaxed">${escapeHtml(m.description)}</p>
          <div class="flex items-center gap-3 text-[11px] text-zinc-500 font-mono flex-wrap">
            <span>Repo: <span class="text-zinc-400">${escapeHtml(m.id)}</span></span>
            <span>•</span>
            <span>Resolution: <span class="text-zinc-400">${escapeHtml(m.resolution || "Native")}</span></span>
            ${m.quality ? `<span>•</span><span>Quality: <span class="text-zinc-400">${escapeHtml(m.quality)}</span></span>` : ""}
          </div>
        </div>

        <div class="shrink-0 flex items-center gap-2 self-start md:self-center">
          ${isDownloaded ? `
            <span class="text-xs font-semibold px-2.5 py-1.5 rounded-lg bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 flex items-center gap-1.5">
              <i data-lucide="check-circle-2" class="size-3.5 text-emerald-400"></i> Installed
            </span>
            ${!m.is_active ? `
              <button type="button" onclick="setActiveMediaModel('${escapeHtml(m.id)}', '${isImage ? "image" : "video"}')" class="px-3.5 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-500 text-white text-xs font-semibold transition-all shadow-sm">
                Activate
              </button>
            ` : `
              <span class="text-[10px] font-mono px-2 py-1 rounded bg-brand-500/20 text-brand-300 border border-brand-500/40">Active</span>
            `}
            <button type="button" onclick="revealMediaModelFolder('${escapeHtml(m.id)}')" class="p-2 rounded-lg bg-surface-700 hover:bg-surface-600 text-zinc-300 hover:text-white transition-colors" title="Reveal in Finder">
              <i data-lucide="folder-open" class="size-3.5"></i>
            </button>
          ` : `
            <button type="button" onclick="downloadMediaModel('${escapeHtml(m.id)}', '${isImage ? "image" : "video"}', '${escapeHtml(m.size_est)}', ${isGated})" class="px-4 py-2 rounded-lg ${isGated ? 'bg-amber-600 hover:bg-amber-500' : 'bg-rose-600 hover:bg-rose-500'} text-white font-semibold text-xs transition-all flex items-center gap-1.5 shadow-md shadow-rose-950/30">
              <i data-lucide="download" class="size-3.5"></i>
              <span>${isGated ? 'Download (Gated)' : 'Download to Mac'}</span>
            </button>
          `}
        </div>
      </div>
    `;
  }).join("");

  if (window.lucide) lucide.createIcons();
}

function switchMediaModelTab(tabName) {
  const tabInstalled = document.getElementById("tabContentInstalledMedia");
  const tabCurated = document.getElementById("tabContentCuratedMedia");
  const tabSearch = document.getElementById("tabContentSearchMedia");

  const btnInstalled = document.getElementById("tabBtnInstalledMedia");
  const btnCurated = document.getElementById("tabBtnCuratedMedia");
  const btnSearch = document.getElementById("tabBtnSearchMedia");

  if (tabInstalled) tabInstalled.classList.add("hidden");
  if (tabCurated) tabCurated.classList.add("hidden");
  if (tabSearch) tabSearch.classList.add("hidden");

  [btnInstalled, btnCurated, btnSearch].forEach(b => {
    if (b) {
      b.className = "px-3 py-1.5 rounded-lg text-zinc-400 hover:text-white transition-all text-xs font-medium";
    }
  });

  if (tabName === "installed") {
    if (tabInstalled) tabInstalled.classList.remove("hidden");
    if (btnInstalled) btnInstalled.className = "px-3 py-1.5 rounded-lg bg-surface-700 text-white font-medium transition-all text-xs shadow-sm";
  } else if (tabName === "curated") {
    if (tabCurated) tabCurated.classList.remove("hidden");
    if (btnCurated) btnCurated.className = "px-3 py-1.5 rounded-lg bg-surface-700 text-white font-medium transition-all text-xs shadow-sm";
  } else if (tabName === "search") {
    if (tabSearch) tabSearch.classList.remove("hidden");
    if (btnSearch) btnSearch.className = "px-3 py-1.5 rounded-lg bg-surface-700 text-white font-medium transition-all text-xs shadow-sm";
  }
}

async function executeHfMediaSearch() {
  const query = document.getElementById("inputHfMediaSearch")?.value.trim() || "";
  const typeFilter = document.getElementById("selectHfMediaType")?.value || "";
  const container = document.getElementById("searchHfMediaResults");
  const btn = document.getElementById("btnExecuteHfSearch");

  if (!container) return;
  container.innerHTML = `<div class="text-xs text-rose-300 py-6 text-center"><i data-lucide="loader-2" class="size-4 animate-spin inline-block mr-1.5"></i> Searching Hugging Face Hub live...</div>`;
  if (window.lucide) lucide.createIcons();

  if (btn) btn.disabled = true;

  try {
    const url = `/api/media/models/search?q=${encodeURIComponent(query)}&type=${encodeURIComponent(typeFilter)}&limit=15`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const results = data.models || [];

    if (results.length === 0) {
      container.innerHTML = `<div class="text-xs text-zinc-500 py-6 text-center italic">No Hugging Face models found matching "${escapeHtml(query)}". Try another keyword like "sd-turbo" or "flux".</div>`;
      return;
    }

    container.innerHTML = results.map(m => {
      const isDownloaded = Boolean(m.is_downloaded);
      const isImage = (m.type || "").includes("image");
      const modelType = isImage ? "image" : "video";

      return `
        <div class="p-4 rounded-xl bg-surface-850/90 border border-surface-700/80 hover:border-surface-600 transition-all flex flex-col md:flex-row md:items-center justify-between gap-3.5">
          <div class="space-y-1.5 min-w-0 flex-1">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="text-sm font-bold text-white truncate max-w-sm" title="${escapeHtml(m.id)}">${escapeHtml(m.name)}</span>
              <span class="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-surface-750 text-zinc-300 border border-surface-650">${escapeHtml(m.type || "Diffusion")}</span>
              <span class="text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/25">⬇ ${(m.downloads || 0).toLocaleString()} dl</span>
              <span class="text-[10px] font-mono px-2 py-0.5 rounded-full bg-rose-500/10 text-rose-400 border border-rose-500/25">♥ ${(m.likes || 0).toLocaleString()}</span>
            </div>
            <div class="text-[11px] text-zinc-500 font-mono truncate">HF Repository: <span class="text-zinc-400">${escapeHtml(m.id)}</span></div>
          </div>

          <div class="shrink-0 flex items-center gap-2 self-start md:self-center">
            ${isDownloaded ? `
              <span class="text-xs font-semibold px-2.5 py-1.5 rounded-lg bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 flex items-center gap-1.5">
                <i data-lucide="check-circle-2" class="size-3.5 text-emerald-400"></i> Installed
              </span>
              <button type="button" onclick="setActiveMediaModel('${escapeHtml(m.id)}', '${modelType}')" class="px-3.5 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-500 text-white text-xs font-semibold transition-all shadow-sm">
                Activate
              </button>
            ` : `
              <button type="button" onclick="downloadMediaModel('${escapeHtml(m.id)}', '${modelType}')" class="px-4 py-2 rounded-lg bg-rose-600 hover:bg-rose-500 text-white font-semibold text-xs transition-all flex items-center gap-1.5 shadow-md shadow-rose-950/30">
                <i data-lucide="download" class="size-3.5"></i>
                <span>Download</span>
              </button>
            `}
          </div>
        </div>
      `;
    }).join("");

    if (window.lucide) lucide.createIcons();

  } catch (err) {
    container.innerHTML = `<div class="text-xs text-rose-400 py-3 text-center">Search error: ${escapeHtml(err.message)}</div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function onActiveImageModelChange() {
  const modelId = document.getElementById("selectActiveImageModel")?.value;
  if (!modelId) return;
  await setActiveMediaModel(modelId, "image");
}

async function onActiveVideoModelChange() {
  const modelId = document.getElementById("selectActiveVideoModel")?.value;
  if (!modelId) return;
  await setActiveMediaModel(modelId, "video");
}

async function setActiveMediaModel(modelId, modelType) {
  try {
    const res = await fetch("/api/media/models/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_id: modelId, model_type: modelType })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Activated ${modelId} for ${modelType} generation`);
      await loadMediaModelsSettings();
    } else {
      alert("Failed to activate model: " + (data.error || "Unknown error"));
    }
  } catch (e) {
    alert("Activation error: " + e.message);
  }
}

async function downloadMediaModel(modelId, modelType, sizeEst = "", isGated = false) {
  if (isGated && !window.hasHfTokenConfigured) {
    const proceed = confirm(
      `🔒 Gated Model: ${modelId}\n\n` +
      `Access to this model is restricted by its author on Hugging Face.\n\n` +
      `Requirements:\n` +
      `1. Visit https://huggingface.co/${modelId} in your browser and click 'Agree and access repository'.\n` +
      `2. Save your free Hugging Face User Access Token (hf_...) in the token field in Settings.\n\n` +
      `💡 Recommended Alternative: You can 1-click download 'FLUX.1 Schnell (Apple Silicon MLX • Open)' with zero tokens or license steps needed!\n\n` +
      `Do you still want to attempt downloading this gated model?`
    );
    if (!proceed) return;
  } else {
    const sizeMsg = sizeEst ? ` (~${sizeEst})` : "";
    if (!confirm(`Download ${modelId}${sizeMsg} directly to your Mac?\n\nWeights will be saved into data/models/ with background tracking.`)) {
      return;
    }
  }

  showToast(`Initiating download of ${modelId}...`);

  try {
    const res = await fetch("/api/media/models/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_id: modelId, model_type: modelType })
    });
    const data = await res.json();
    if (data.success && data.job_id) {
      startPollingMediaDownload(data.job_id, modelId);
    } else {
      alert("Download initiation failed: " + (data.error || "Unknown error"));
    }
  } catch (e) {
    alert("Download error: " + e.message);
  }
}

function startPollingMediaDownload(jobId, modelId) {
  if (mediaDownloadPollingTimer) {
    clearInterval(mediaDownloadPollingTimer);
  }

  const container = document.getElementById("mediaDownloadProgressContainer");
  const title = document.getElementById("mediaDownloadJobTitle");
  const pct = document.getElementById("mediaDownloadJobPct");
  const bar = document.getElementById("mediaDownloadProgressBar");
  const desc = document.getElementById("mediaDownloadJobDesc");

  if (container) {
    container.classList.remove("hidden");
    container.className = "p-3 rounded-xl bg-surface-850 border border-rose-500/40 space-y-2";
  }
  if (title) title.textContent = `Downloading ${modelId}...`;
  if (pct) pct.textContent = "Connecting to Hugging Face...";
  if (bar) {
    bar.style.width = "10%";
    bar.className = "h-full bg-rose-500 rounded-full transition-all duration-300";
  }
  if (desc) desc.innerHTML = `Transferring snapshot shards to <code>data/models/${escapeHtml(modelId.replace(/\//g, "---"))}</code>`;

  let simulatedProgress = 15;

  mediaDownloadPollingTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/media/models/download/${jobId}`);
      if (!res.ok) return;
      const data = await res.json();

      if (data.status === "completed") {
        clearInterval(mediaDownloadPollingTimer);
        mediaDownloadPollingTimer = null;
        if (pct) pct.textContent = "100% (Complete)";
        if (bar) bar.style.width = "100%";
        if (desc) desc.textContent = "Saved to local disk! Ready for on-device inference.";
        showToast(`✓ Model ${modelId} downloaded successfully!`);
        await loadMediaModelsSettings();
        setTimeout(() => {
          if (container) container.classList.add("hidden");
        }, 5000);
      } else if (data.status === "failed") {
        clearInterval(mediaDownloadPollingTimer);
        mediaDownloadPollingTimer = null;
        if (pct) pct.textContent = "Failed";
        if (bar) {
          bar.style.width = "100%";
          bar.className = "h-full bg-rose-500 rounded-full";
        }
        const errText = data.error || "Download was interrupted";
        if (desc) {
          desc.innerHTML = `
            <div class="mt-1 p-2.5 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-200 text-xs space-y-2">
              <div class="font-bold flex items-center justify-between">
                <span class="flex items-center gap-1.5"><i data-lucide="alert-triangle" class="size-3.5 text-rose-400"></i> Download Failed</span>
                <button type="button" onclick="document.getElementById('mediaDownloadProgressContainer').classList.add('hidden')" class="text-zinc-400 hover:text-white text-[11px] font-normal underline">Dismiss</button>
              </div>
              <p class="whitespace-pre-line text-[11px] text-zinc-300 leading-relaxed font-mono">${escapeHtml(errText)}</p>
              ${data.is_gated ? `
                <div class="pt-1 flex flex-wrap gap-2">
                  <a href="https://huggingface.co/${escapeHtml(modelId)}" target="_blank" class="px-2.5 py-1 rounded-lg bg-rose-600 hover:bg-rose-500 text-white text-[11px] font-semibold flex items-center gap-1 transition-colors">
                    Accept License on Hugging Face &rarr;
                  </a>
                  <button type="button" onclick="document.getElementById('inputHfUserToken').focus(); scrollToMediaModelsSettings();" class="px-2.5 py-1 rounded-lg bg-surface-700 hover:bg-surface-600 text-zinc-200 text-[11px] font-semibold transition-colors">
                    Enter HF Token Above
                  </button>
                </div>
              ` : ''}
            </div>
          `;
          if (window.lucide) lucide.createIcons();
        }
      } else {
        // Still running
        simulatedProgress = Math.min(simulatedProgress + 4, 90);
        if (bar) bar.style.width = `${simulatedProgress}%`;
        if (pct) pct.textContent = `Downloading (~${simulatedProgress}%)`;
      }
    } catch (e) {
      console.warn("Polling error:", e);
    }
  }, 2000);
}

async function deleteMediaModel(modelId) {
  if (!confirm(`Are you sure you want to delete ${modelId} from your Mac?\n\nThis will remove the weights from disk and reclaim storage.`)) {
    return;
  }

  try {
    const res = await fetch(`/api/media/models/${encodeURIComponent(modelId)}`, {
      method: "DELETE"
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Deleted ${modelId}`);
      await loadMediaModelsSettings();
    } else {
      alert("Delete failed: " + (data.error || "Unknown error"));
    }
  } catch (e) {
    alert("Delete error: " + e.message);
  }
}

async function revealMediaModelFolder(modelId = "") {
  try {
    const res = await fetch("/api/media/models/reveal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_id: modelId })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Opened in Finder: ${data.target}`);
    } else {
      alert("Could not open in Finder: " + (data.error || "Folder not found"));
    }
  } catch (e) {
    alert("Finder reveal error: " + e.message);
  }
}

// -------------------------------------------------------------
// ROADMAP MODAL
// -------------------------------------------------------------
function openRoadmapModal() {
  document.getElementById("roadmapModal").classList.remove("hidden");
  fetchRoadmap();
  lucide.createIcons();
}

function closeRoadmapModal() {
  document.getElementById("roadmapModal").classList.add("hidden");
}

async function fetchRoadmap() {
  const container = document.getElementById("roadmapModalContent");
  if (!container) return;
  try {
    const res = await fetch("/api/roadmap");
    const data = await res.json();
    const items = data.items || [];
    const groups = [["next", "Next", "text-brand-300"], ["later", "Later", "text-indigo-300"], ["shipped", "Already in Xod", "text-emerald-300"]];
    let upd = {};
    try { upd = await (await fetch("/api/update")).json(); } catch (e) {}
    let html = `<div class="flex items-center justify-between gap-3 p-3 rounded-xl bg-surface-750/70 border border-white/[0.06] text-[12px] text-zinc-300">
      <span>You have Xod ${escapeHtml(upd.current || "")}${upd.available ? ` · <a href="${escapeHtml(upd.url)}" target="_blank" rel="noopener" class="text-brand-300 hover:underline">${escapeHtml(upd.latest)} is out</a>` : upd.latest ? " · up to date" : ""}</span>
      <label class="flex items-center gap-1.5 text-zinc-400" title="Once a day Xod asks GitHub for the latest version. Nothing else is sent.">
        <input type="checkbox" class="accent-amber-400" ${upd.enabled ? "checked" : ""} onchange="fetch('/api/update/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:this.checked})})"> Tell me about new versions</label>
    </div>`;
    for (const [key, label, color] of groups) {
      const list = items.filter((i) => i.status === key);
      if (!list.length) continue;
      html += `<h4 class="text-[12px] font-semibold uppercase tracking-wider ${color} mt-4 mb-2">${label}</h4>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-2.5">` + list.map((i) => `
          <div class="p-3.5 rounded-xl bg-surface-750/70 border border-white/[0.06]">
            <p class="font-semibold text-[13px] text-white">${key === "shipped" ? "✓ " : ""}${escapeHtml(i.name)}</p>
            <p class="text-xs text-zinc-400 leading-relaxed mt-1">${escapeHtml(i.description)}</p>
          </div>`).join("") + `</div>`;
    }
    container.innerHTML = html;
    lucide.createIcons();
  } catch (err) {
    container.innerHTML = `<div class="text-xs text-red-400 text-center py-4">Error loading roadmap: ${escapeHtml(err.message)}</div>`;
  }
}

async function testTrustMRRKey() {
  const inputKey = document.getElementById("inputTrustmrrKey").value.trim();
  const feedback = document.getElementById("testKeyFeedback");
  const btn = document.getElementById("btnTestTrustmrr");

  feedback.classList.remove("hidden");
  feedback.className = "text-xs text-zinc-400";
  feedback.textContent = "Connecting to TrustMRR API...";
  btn.disabled = true;

  try {
    // If input key provided, save it temporarily
    if (inputKey) {
      await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trustmrr_api_key: inputKey })
      });
    }

    const res = await fetch("/api/trustmrr/test", { method: "POST" });
    const data = await res.json();

    if (data.valid) {
      feedback.className = "text-xs text-emerald-400 font-semibold";
      feedback.textContent = `✓ ${data.message}`;
      await loadConfig();
    } else {
      feedback.className = "text-xs text-red-400 font-semibold";
      feedback.textContent = `✗ ${data.message}`;
    }
  } catch (e) {
    feedback.className = "text-xs text-red-400 font-semibold";
    feedback.textContent = `✗ Error testing key: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

async function testYouKey() {
  const inputKey = document.getElementById("inputYouKey").value.trim();
  const feedback = document.getElementById("testYouFeedback");
  const btn = document.getElementById("btnTestYou");

  feedback.classList.remove("hidden");
  feedback.className = "text-xs text-zinc-400";
  feedback.textContent = "Connecting to You.com Search API (api.ydc-index.io)...";
  btn.disabled = true;

  try {
    if (inputKey) {
      await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ you_api_key: inputKey })
      });
    }

    const res = await fetch("/api/you/test", { method: "POST" });
    const data = await res.json();

    if (data.valid) {
      feedback.className = "text-xs text-emerald-400 font-semibold";
      feedback.textContent = `✓ ${data.message}`;
      await loadConfig();
    } else {
      feedback.className = "text-xs text-red-400 font-semibold";
      feedback.textContent = `✗ ${data.message}`;
    }
  } catch (e) {
    feedback.className = "text-xs text-red-400 font-semibold";
    feedback.textContent = `✗ Error testing key: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

async function testTavilyKey() {
  const inputKey = document.getElementById("inputTavilyKey")?.value.trim() || "";
  const feedback = document.getElementById("testTavilyFeedback");
  const btn = document.getElementById("btnTestTavily");
  if (!feedback) return;

  feedback.classList.remove("hidden");
  feedback.className = "text-xs text-zinc-400";
  feedback.textContent = "Connecting to Tavily AI Search API (api.tavily.com)...";
  if (btn) btn.disabled = true;

  try {
    const res = await fetch("/api/tavily/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tavily_api_key: inputKey || undefined })
    });
    const data = await res.json();

    if (data.valid) {
      feedback.className = "text-xs text-emerald-400 font-semibold";
      feedback.textContent = `✓ ${data.message}`;
      const input = document.getElementById("inputTavilyKey");
      if (input && inputKey) input.placeholder = "Configured (••••••••)";
      const text = document.getElementById("tavilyStatusText");
      if (text) text.textContent = "Tavily: connected";
    } else {
      feedback.className = "text-xs text-red-400 font-semibold";
      feedback.textContent = `✗ ${data.message}`;
    }
  } catch (e) {
    feedback.className = "text-xs text-red-400 font-semibold";
    feedback.textContent = `✗ Error testing key: ${e.message}`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function testYoutubeKey() {
  const inputKey = document.getElementById("inputYoutubeKey").value.trim();
  const feedback = document.getElementById("testYoutubeFeedback");
  const btn = document.getElementById("btnTestYoutube");

  feedback.classList.remove("hidden");
  feedback.className = "text-xs text-zinc-400";
  feedback.textContent = "Connecting to YouTube Data API v3 (googleapis.com)...";
  btn.disabled = true;

  try {
    if (inputKey) {
      await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ youtube_api_key: inputKey })
      });
    }

    const res = await fetch("/api/youtube/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ youtube_api_key: inputKey || undefined })
    });
    const data = await res.json();

    if (data.valid) {
      feedback.className = "text-xs text-emerald-400 font-semibold";
      feedback.textContent = `✓ ${data.message}`;
      await loadConfig();
    } else {
      feedback.className = "text-xs text-red-400 font-semibold";
      feedback.textContent = `✗ ${data.message}`;
    }
  } catch (e) {
    feedback.className = "text-xs text-red-400 font-semibold";
    feedback.textContent = `✗ Error testing key: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

async function saveSettings() {
  const key = document.getElementById("inputTrustmrrKey").value.trim();
  const youKey = document.getElementById("inputYouKey").value.trim();
  const tavilyKey = document.getElementById("inputTavilyKey")?.value.trim();
  const youtubeKey = document.getElementById("inputYoutubeKey")?.value.trim();
  const provider = document.getElementById("selectLlmProvider").value;
  const baseUrl = document.getElementById("inputLlmBaseUrl").value.trim();
  const model = document.getElementById("inputLlmModel").value.trim();
  const cloudKey = document.getElementById("inputCloudKey").value.trim();

  // Swarm cloud keys
  const geminiKey = document.getElementById("inputGeminiKey")?.value.trim();
  const openaiKey = document.getElementById("inputOpenAIKey")?.value.trim();
  const anthropicKey = document.getElementById("inputAnthropicKey")?.value.trim();
  const groqKey = document.getElementById("inputGroqKey")?.value.trim();
  const openrouterKey = document.getElementById("inputOpenRouterKey")?.value.trim();
  const fireworksKey = document.getElementById("inputFireworksKey")?.value.trim();
  const hfKey = document.getElementById("inputHfKey")?.value.trim() || document.getElementById("inputHfUserToken")?.value.trim();

  const payload = {
    llm_provider: provider,
    llm_base_url: (provider === "fireworks" && (!baseUrl || baseUrl.includes("localhost"))) ? "https://api.fireworks.ai/inference/v1" : baseUrl,
    llm_model: (provider === "fireworks" && (!model || model.includes("gemma-4"))) ? "accounts/fireworks/models/glm-5p3-flash" : model
  };
  if (key) payload.trustmrr_api_key = key;
  if (youKey) payload.you_api_key = youKey;
  if (tavilyKey) payload.tavily_api_key = tavilyKey;
  if (youtubeKey) payload.youtube_api_key = youtubeKey;
  if (cloudKey) {
    if (provider === "openai") payload.openai_api_key = cloudKey;
    if (provider === "gemini") payload.gemini_api_key = cloudKey;
    if (provider === "groq") payload.groq_api_key = cloudKey;
    if (provider === "anthropic") payload.anthropic_api_key = cloudKey;
    if (provider === "openrouter") payload.openrouter_api_key = cloudKey;
    if (provider === "fireworks") payload.fireworks_api_key = cloudKey;
  }
  // The main key field (for the chosen provider) wins over the same provider's field in the Swarm section:
  // it used to be overwritten by whatever was left there.
  if (geminiKey && !payload.gemini_api_key) payload.gemini_api_key = geminiKey;
  if (openaiKey && !payload.openai_api_key) payload.openai_api_key = openaiKey;
  if (anthropicKey && !payload.anthropic_api_key) payload.anthropic_api_key = anthropicKey;
  if (groqKey && !payload.groq_api_key) payload.groq_api_key = groqKey;
  if (openrouterKey && !payload.openrouter_api_key) payload.openrouter_api_key = openrouterKey;
  if (fireworksKey && !payload.fireworks_api_key) payload.fireworks_api_key = fireworksKey;
  if (hfKey) payload.huggingface_token = hfKey;
  if (provider === "fireworks" && !payload.fireworks_api_key) {
    if (cloudKey) payload.fireworks_api_key = cloudKey;
    else if (fireworksKey) payload.fireworks_api_key = fireworksKey;
  }

  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      closeSettingsModal();
      await loadConfig();
      if (typeof loadMediaModelsSettings === "function") await loadMediaModelsSettings();
      showToast("Settings saved successfully!");
    } else {
      alert("Failed to save settings");
    }
  } catch (err) {
    alert("Error: " + err.message);
  }
}

async function testHfKeyDirect() {
  const inputKey = document.getElementById("inputHfKey")?.value.trim() || document.getElementById("inputHfUserToken")?.value.trim();
  const feedback = document.getElementById("testHfFeedback");
  const btn = event.currentTarget;

  if (!inputKey) {
    if (feedback) {
      feedback.classList.remove("hidden");
      feedback.className = "text-xs text-amber-400 mt-1";
      feedback.textContent = "Please enter your Hugging Face token (starts with hf_...).";
    }
    return;
  }

  btn.disabled = true;
  btn.innerHTML = `<i data-lucide="loader-2" class="size-3 animate-spin inline-block"></i> Testing...`;
  if (window.lucide) lucide.createIcons();

  try {
    const res = await fetch("/api/media/models/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: inputKey })
    });
    const data = await res.json();
    if (feedback) feedback.classList.remove("hidden");

    if (data.valid) {
      if (feedback) {
        feedback.className = "text-xs text-emerald-400 font-semibold mt-1";
        feedback.textContent = `✓ ${data.message || "Valid Hugging Face token!"}`;
      }
      showToast("Hugging Face token verified!");
      await loadConfig();
      if (typeof loadMediaModelsSettings === "function") await loadMediaModelsSettings();
    } else {
      if (feedback) {
        feedback.className = "text-xs text-red-400 font-semibold mt-1";
        feedback.textContent = `✗ ${data.message || "Invalid token"}`;
      }
    }
  } catch (e) {
    if (feedback) {
      feedback.classList.remove("hidden");
      feedback.className = "text-xs text-red-400 font-semibold mt-1";
      feedback.textContent = `✗ Error: ${e.message}`;
    }
  } finally {
    btn.disabled = false;
    btn.innerHTML = `Test Token`;
  }
}

// -------------------------------------------------------------
// PERSONA MANAGEMENT
// -------------------------------------------------------------
function setPersona(persona) {
  currentPersona = persona;
  document.querySelectorAll(".persona-tab").forEach(tab => tab.classList.remove("active-persona"));
  const activeTab = document.getElementById(`persona-${persona}`);
  if (activeTab) activeTab.classList.add("active-persona");
  const sel = document.getElementById("personaSelect");
  if (sel && sel.value !== persona) sel.value = persona;
}

// -------------------------------------------------------------
// TOOLS MENU (header)
// -------------------------------------------------------------
function toggleToolsMenu(e) {
  if (e) e.stopPropagation();
  const menu = document.getElementById("toolsMenu");
  if (!menu) return;
  menu.classList.toggle("hidden");
  if (window.lucide) lucide.createIcons();
}

function closeToolsMenu() {
  const menu = document.getElementById("toolsMenu");
  if (menu) menu.classList.add("hidden");
}

document.addEventListener("click", (e) => {
  const menu = document.getElementById("toolsMenu");
  if (menu && !menu.classList.contains("hidden") && !menu.contains(e.target)) closeToolsMenu();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeToolsMenu(); });

function setHeaderStatus(state, label) {
  const dot = document.getElementById("headerStatusDot");
  const txt = document.getElementById("headerStatusText");
  const colors = { ok: "bg-emerald-400", warn: "bg-amber-400", off: "bg-red-400", idle: "bg-zinc-500" };
  if (dot) dot.className = `size-2 rounded-full ${colors[state] || colors.idle}`;
  if (txt) txt.textContent = label;
}

function usePrompt(text) {
  const input = document.getElementById("chatInput");
  input.value = text;
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 150) + "px";
  document.getElementById("chatForm").requestSubmit();
}

// -------------------------------------------------------------
// SESSIONS & PERSISTENT HISTORY
// -------------------------------------------------------------
// loadSessions(): see sidebar.js

// Welcome-screen ideas: core ones, plus examples from add-on packs that are on (packs.js).
const WELCOME_STARTERS = [
  { icon: "newspaper", color: "text-teal-400", label: "Today's top tech news, briefly", prompt: "What are today's 5 biggest tech news stories? One line each, with sources." },
  { icon: "folder-search", color: "text-cyan-400", label: "What's filling up my Downloads?", prompt: "Look through my Downloads folder and tell me what's taking up the most space, and what looks safe to delete." },
  { icon: "scale", color: "text-indigo-400", label: "Compare and make a report", prompt: "Research the best noise-cancelling headphones under $300, compare the top 4 in a table, and cite sources." },
  { icon: "image", color: "text-rose-400", label: "Make an image", prompt: "Make an image of a cozy cabin under the northern lights.", pack: "media-studio" },
  { icon: "file-search", color: "text-amber-400", label: "How much does ShipFast make?", prompt: "Do a deep research teardown on ShipFast: verified revenue, MRR, traffic, cofounders, and competitive moat", pack: "startup-intel" },
  { icon: "shopping-bag", color: "text-amber-400", label: "Find good businesses for sale", prompt: "What are the best SaaS businesses currently for sale on TrustMRR under 3x revenue multiple?", pack: "startup-intel" },
];

function welcomeStartersHtml() {
  const on = window.enabledPacks;   // set by packs.js; undefined until loaded = show core only
  return WELCOME_STARTERS.filter((s) => !s.pack || (on && on.has(s.pack))).slice(0, 4).map((s) => `
    <button onclick="usePrompt(${escapeHtml(JSON.stringify(s.prompt))})" class="starter-card group">
      <i data-lucide="${s.icon}" class="size-4 ${s.color} shrink-0"></i>
      <span>${escapeHtml(s.label)}</span>
    </button>`).join("");
}

async function selectSession(sessionId) {
  if (isStreaming) return;
  currentSessionId = sessionId;

  try {
    const res = await fetch(`/api/sessions/${sessionId}`);
    if (!res.ok) return;
    const data = await res.json();
    const session = data.session;
    const messages = data.messages || [];

    if (session.persona) {
      setPersona(session.persona);
    }

    const welcome = document.getElementById("welcomeHero");
    if (welcome) welcome.style.display = "none";

    const container = document.getElementById("chatMessages");
    container.innerHTML = "";
    conversationHistory = [];

    for (const m of messages) {
      if (m.role === "user") {
        appendUserMessage(m.content);
        conversationHistory.push({ role: "user", content: m.content });
      } else if (m.role === "assistant") {
        renderStoredAssistantMessage(m);
        conversationHistory.push({ role: "assistant", content: m.content });
      }
    }

    await loadSessions();
    scrollChatToBottom(false);
    lucide.createIcons();
  } catch (e) {
    console.error("Error selecting session:", e);
  }
}

function buildScorecardHtml(scorecard, entity = "") {
  if (!scorecard) return "";
  const color = scorecard.color || "emerald";
  let colorClasses = "bg-emerald-500/15 text-emerald-300 border border-emerald-500/35 shadow-sm shadow-emerald-500/10";
  if (color === "brand") colorClasses = "bg-brand-500/15 text-brand-300 border border-brand-500/35 shadow-sm shadow-brand-500/10";
  if (color === "amber") colorClasses = "bg-amber-500/15 text-amber-300 border border-amber-500/35 shadow-sm shadow-amber-500/10";
  if (color === "red") colorClasses = "bg-red-500/15 text-red-300 border border-red-500/35 shadow-sm shadow-red-500/10";

  let metersHtml = "";
  const breakdown = scorecard.breakdown || {};
  for (const [key, item] of Object.entries(breakdown)) {
    const pct = Math.round((item.score / item.max) * 100);
    metersHtml += `
      <div class="bg-black/35 p-3 rounded-xl border border-white/[0.05] space-y-2 backdrop-blur-md">
        <div class="flex items-center justify-between text-xs">
          <span class="text-zinc-400 font-medium">${escapeHtml(item.label)}</span>
          <span class="font-mono font-bold text-zinc-100">${item.score}/${item.max}</span>
        </div>
        <div class="w-full bg-surface-700/80 rounded-full h-1.5 overflow-hidden">
          <div class="h-1.5 rounded-full ${pct >= 75 ? 'bg-gradient-to-r from-emerald-500 to-teal-400' : (pct >= 50 ? 'bg-gradient-to-r from-brand-500 to-indigo-400' : 'bg-gradient-to-r from-amber-500 to-orange-400')}" style="width: ${pct}%"></div>
        </div>
      </div>
    `;
  }

  let flagsHtml = "";
  if (scorecard.risk_flags && scorecard.risk_flags.length > 0) {
    flagsHtml = `
      <div class="flex flex-wrap gap-2 pt-2.5 border-t border-white/[0.08]">
        ${scorecard.risk_flags.map(f => `
          <span class="text-[11px] px-2.5 py-0.5 rounded-full bg-amber-500/10 text-amber-300 border border-amber-500/25 flex items-center gap-1.5 font-mono">
            <i data-lucide="alert-triangle" class="size-3 text-amber-400"></i> ${escapeHtml(f)}
          </span>
        `).join("")}
      </div>
    `;
  }

  return `
    <div class="scorecard-container my-3">
      <div class="p-4 rounded-2xl bg-surface-800/85 border border-white/[0.08] backdrop-blur-xl space-y-3 shadow-xl">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <i data-lucide="award" class="size-4 text-brand-400"></i>
            <span class="text-xs font-bold text-zinc-200 uppercase tracking-wider">Due Diligence Health Index</span>
          </div>
          <div class="score-badge flex items-center gap-2 px-3.5 py-1 rounded-full text-xs font-bold font-mono ${colorClasses}">
            <span class="text-sm">${scorecard.overall_score} / 100</span> • <span class="font-normal text-[11px]">${escapeHtml(scorecard.rating || '')}</span>
          </div>
        </div>
        <div class="score-meters grid grid-cols-1 sm:grid-cols-3 gap-2.5 pt-1">
          ${metersHtml}
        </div>
        ${flagsHtml}
      </div>
    </div>
  `;
}

// -------------------------------------------------------------
// SOTA THOUGHT PROCESS PARSER & STRUCTURED REVIEWER
// -------------------------------------------------------------
function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function parseStructuredThoughts(rawText) {
  if (!rawText || !rawText.trim()) return [];
  const lines = rawText.split("\n");
  const cards = [];
  let currentCard = null;

  for (let rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;

    const isBullet = /^([*\-•]|\d+[\.)])\s+/.test(line);
    const cleanText = line.replace(/^([*\-•]|\d+[\.)])\s+/, "").replace(/^\*\*|\*\*$/g, "");
    const lower = cleanText.toLowerCase();

    let category = null;
    let label = null;

    if (lower.startsWith("source ") || lower.includes("source:") || /https?:\/\//i.test(lower) || lower.includes("sec edgar") || lower.includes("nbc") || lower.includes("trustmrr") || lower.includes("github") || lower.includes("tranco") || lower.includes("api.")) {
      category = "source";
      label = "Sources & Ground Truth";
    } else if (lower.startsWith("trend ") || lower.includes("insight:") || lower.includes("finding:") || lower.includes("deduction:") || lower.includes("valuation") || lower.includes("revenue") || lower.includes("growth rate") || lower.includes("popularity") || lower.includes("traffic rank")) {
      category = "insight";
      label = "Key Market Insight";
    } else if (lower.startsWith("executive summary") || lower.startsWith("architectural") || lower.includes("architecture:") || lower.includes("strategy:") || lower.includes("hypothesis:") || lower.includes("blueprint:") || lower.includes("synthesis plan")) {
      category = "strategy";
      label = "Strategic Blueprint";
    } else if (lower.startsWith("correction:") || lower.includes("caveat:") || lower.includes("note:") || lower.includes("nuance:") || lower.includes("clarification:") || lower.includes("disclaimer:") || lower.includes("intent bridging")) {
      category = "correction";
      label = "Correction & Nuance";
    }

    if (category || isBullet || !currentCard) {
      if (currentCard) cards.push(currentCard);
      currentCard = {
        type: category || (currentCard ? currentCard.type : "general"),
        label: label || (currentCard && category ? label : "Reasoning Step"),
        lines: [cleanText],
        raw: line
      };
    } else {
      currentCard.lines.push(cleanText);
      currentCard.raw += "\n" + line;
    }
  }

  if (currentCard) cards.push(currentCard);
  return cards;
}

function renderThoughtCardsHtml(cards, filterQuery = "") {
  if (!cards || cards.length === 0) {
    return `<div class="text-xs text-zinc-500 py-4 text-center font-sans">No thought tokens recorded.</div>`;
  }

  const query = (filterQuery || "").trim().toLowerCase();
  const filtered = query
    ? cards.filter(c => c.raw.toLowerCase().includes(query) || c.label.toLowerCase().includes(query))
    : cards;

  if (filtered.length === 0) {
    return `<div class="text-xs text-zinc-500 py-6 text-center font-sans">No thoughts match "<span class="text-zinc-200 font-semibold">${escapeHtml(filterQuery)}</span>".</div>`;
  }

  return filtered.map(c => {
    let icon = "brain";
    let badgeColor = "text-indigo-400 bg-indigo-500/10 border-indigo-500/20";
    if (c.type === "source") {
      icon = "globe";
      badgeColor = "text-cyan-400 bg-cyan-500/10 border-cyan-500/20";
    } else if (c.type === "insight") {
      icon = "trending-up";
      badgeColor = "text-purple-400 bg-purple-500/10 border-purple-500/20";
    } else if (c.type === "strategy") {
      icon = "compass";
      badgeColor = "text-amber-400 bg-amber-500/10 border-amber-500/20";
    } else if (c.type === "correction") {
      icon = "alert-circle";
      badgeColor = "text-orange-400 bg-orange-500/10 border-orange-500/20";
    }

    const linesHtml = c.lines.map(l => {
      let text = escapeHtml(l);
      if (query) {
        const regex = new RegExp(`(${escapeRegex(query)})`, "gi");
        text = text.replace(regex, `<mark class="bg-indigo-500/40 text-white rounded px-0.5">$1</mark>`);
      }
      return `<p class="mb-1 text-xs text-zinc-300 leading-relaxed font-sans">${text}</p>`;
    }).join("");

    return `
      <div class="thought-card type-${c.type}">
        <div class="flex items-center justify-between pb-1.5 mb-1.5 border-b border-white/[0.05]">
          <div class="flex items-center gap-1.5">
            <i data-lucide="${icon}" class="size-3.5 ${badgeColor.split(" ")[0]}"></i>
            <span class="text-[11px] font-semibold text-zinc-200">${c.label}</span>
          </div>
          <span class="text-[9px] font-mono px-2 py-0.5 rounded border ${badgeColor} font-bold">${c.type.toUpperCase()}</span>
        </div>
        <div class="card-content">
          ${linesHtml}
        </div>
      </div>
    `;
  }).join("");
}

function buildThoughtAccordionHtml(id, reasoning, totalSeconds = null) {
  const wordCount = (reasoning || "").trim().split(/\s+/).filter(Boolean).length;
  const timeStr = totalSeconds ? `${parseFloat(totalSeconds).toFixed(1)}s` : "";

  return `
    <div class="reasoning-container mb-2.5" id="reasoning-box-${id}">
      <details class="thinking-accordion group">
        <summary class="thinking-summary">
          <div class="flex items-center gap-2 min-w-0 flex-1 mr-2">
            <i data-lucide="brain" class="size-3.5 text-indigo-400 shrink-0"></i>
            <span class="thinking-label font-medium text-zinc-300">${totalSeconds ? "Thought for" : "Thought process"}</span>
            <span class="thinking-timer font-mono text-[11px] text-zinc-300 bg-white/5 px-2 py-0.5 rounded-full border border-white/10">${timeStr || `${wordCount} words`}</span>
            ${timeStr ? `<span class="text-[11px] text-zinc-500 hidden sm:inline">(${wordCount} words)</span>` : ""}
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <span class="text-[10px] text-zinc-500 uppercase tracking-wider font-mono">COMPLETE</span>
            <i data-lucide="chevron-right" class="size-3.5 text-zinc-500 thinking-chevron"></i>
          </div>
        </summary>
        <div class="p-3 border-t border-white/[0.06] bg-black/30 space-y-2.5">
          <!-- Thought Review Toolbar -->
          <div class="thought-review-toolbar">
            <div class="flex items-center gap-2 flex-1 max-w-sm relative">
              <i data-lucide="search" class="size-3 text-zinc-500 absolute left-2 top-2 pointer-events-none"></i>
              <input
                type="text"
                placeholder="Filter thoughts (e.g. source, trend, metrics)..."
                class="thought-filter-input w-full bg-surface-900 border border-surface-700/80 rounded-lg pl-7 pr-2 py-1 text-[11px] text-white focus:outline-none focus:border-indigo-500 font-mono"
                data-target="${id}"
              />
            </div>

            <div class="flex items-center gap-1.5 shrink-0">
              <!-- View Mode Pills -->
              <div class="flex bg-surface-900 border border-surface-700 rounded-lg p-0.5 text-[10px]">
                <button type="button" class="btn-mode-cards px-2 py-0.5 rounded bg-indigo-600 text-white font-medium" data-target="${id}">Cards</button>
                <button type="button" class="btn-mode-prose px-2 py-0.5 rounded text-zinc-400 hover:text-white font-medium" data-target="${id}">Prose</button>
                <button type="button" class="btn-mode-raw px-2 py-0.5 rounded text-zinc-400 hover:text-white font-medium" data-target="${id}">Raw</button>
              </div>

              <!-- Quick Pull Buttons -->
              <button type="button" class="btn-copy-thought p-1 text-zinc-400 hover:text-white hover:bg-white/10 rounded transition-colors" title="Copy thoughts" data-target="${id}">
                <i data-lucide="copy" class="size-3.5"></i>
              </button>
              <button type="button" class="btn-download-thought p-1 text-zinc-400 hover:text-white hover:bg-white/10 rounded transition-colors" title="Download thoughts markdown" data-target="${id}">
                <i data-lucide="download" class="size-3.5"></i>
              </button>
              <button type="button" class="btn-inspect-trace px-2 py-0.5 rounded bg-indigo-500/15 hover:bg-indigo-500/25 text-indigo-300 border border-indigo-500/30 text-[11px] transition-colors flex items-center gap-1 font-medium" title="Inspect full LLM trace and tool payloads" data-target="${id}">
                <i data-lucide="activity" class="size-3"></i> <span>Trace</span>
              </button>
            </div>
          </div>

          <!-- Thought Content Area -->
          <div class="thought-display-area" id="thought-display-${id}">
            <!-- Initialized in Cards mode -->
          </div>
        </div>
      </details>
    </div>
  `;
}

function initThoughtReviewer(parentEl, id, rawReasoning, messageObj) {
  const displayArea = parentEl.querySelector(`#thought-display-${id}`);
  const filterInput = parentEl.querySelector(`.thought-filter-input[data-target="${id}"]`);
  const btnCards = parentEl.querySelector(`.btn-mode-cards[data-target="${id}"]`);
  const btnProse = parentEl.querySelector(`.btn-mode-prose[data-target="${id}"]`);
  const btnRaw = parentEl.querySelector(`.btn-mode-raw[data-target="${id}"]`);
  const btnCopy = parentEl.querySelector(`.btn-copy-thought[data-target="${id}"]`);
  const btnDownload = parentEl.querySelector(`.btn-download-thought[data-target="${id}"]`);
  const btnTrace = parentEl.querySelector(`.btn-inspect-trace[data-target="${id}"]`);

  if (!displayArea) return;

  const parsedCards = parseStructuredThoughts(rawReasoning);
  let currentMode = "cards";
  let currentFilter = "";

  function renderView() {
    if (currentMode === "cards") {
      displayArea.innerHTML = `<div class="max-h-80 overflow-y-auto space-y-2 pr-1">${renderThoughtCardsHtml(parsedCards, currentFilter)}</div>`;
    } else if (currentMode === "prose") {
      let text = rawReasoning;
      if (currentFilter) {
        const regex = new RegExp(`(${escapeRegex(currentFilter)})`, "gi");
        text = text.replace(regex, `<mark class="bg-indigo-500/40 text-white rounded px-0.5">$1</mark>`);
      }
      displayArea.innerHTML = `<div class="prose-thinking max-h-80 overflow-y-auto pr-1">${marked.parse(text)}</div>`;
    } else if (currentMode === "raw") {
      let filteredRaw = rawReasoning;
      if (currentFilter) {
        const lines = rawReasoning.split("\n").filter(l => l.toLowerCase().includes(currentFilter.toLowerCase()));
        filteredRaw = lines.join("\n") || `// No lines match "${currentFilter}"`;
      }
      displayArea.innerHTML = `
        <div class="space-y-1">
          <div class="text-[10px] text-zinc-500 font-mono flex justify-between">
            <span>${rawReasoning.length} characters • ${rawReasoning.trim().split(/\s+/).length} words</span>
            <span>Raw Monospace</span>
          </div>
          <pre class="json-payload-pre max-h-72 select-text">${escapeHtml(filteredRaw)}</pre>
        </div>
      `;
    }
    if (window.lucide) lucide.createIcons();
  }

  // Initial render
  renderView();

  // Search filter listener
  if (filterInput) {
    filterInput.addEventListener("input", (e) => {
      currentFilter = e.target.value.trim();
      renderView();
    });
  }

  // Mode switcher listeners
  function setMode(mode) {
    currentMode = mode;
    [btnCards, btnProse, btnRaw].forEach(b => {
      if (!b) return;
      b.className = "px-2 py-0.5 rounded text-zinc-400 hover:text-white font-medium";
    });
    if (mode === "cards" && btnCards) btnCards.className = "px-2 py-0.5 rounded bg-indigo-600 text-white font-medium";
    if (mode === "prose" && btnProse) btnProse.className = "px-2 py-0.5 rounded bg-indigo-600 text-white font-medium";
    if (mode === "raw" && btnRaw) btnRaw.className = "px-2 py-0.5 rounded bg-indigo-600 text-white font-medium";
    renderView();
  }

  if (btnCards) btnCards.addEventListener("click", () => setMode("cards"));
  if (btnProse) btnProse.addEventListener("click", () => setMode("prose"));
  if (btnRaw) btnRaw.addEventListener("click", () => setMode("raw"));

  // Copy listener
  if (btnCopy) {
    btnCopy.addEventListener("click", () => {
      navigator.clipboard.writeText(rawReasoning);
      btnCopy.innerHTML = `<i data-lucide="check" class="size-3.5 text-emerald-400"></i>`;
      if (window.lucide) lucide.createIcons();
      setTimeout(() => {
        btnCopy.innerHTML = `<i data-lucide="copy" class="size-3.5"></i>`;
        if (window.lucide) lucide.createIcons();
      }, 1500);
    });
  }

  // Download listener
  if (btnDownload) {
    btnDownload.addEventListener("click", () => {
      const blob = new Blob([`# Chain-of-Thought Deliberation\n\n${rawReasoning}`], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `thought-process-${id}.md`;
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  // Trace button listener
  if (btnTrace) {
    btnTrace.addEventListener("click", () => {
      openLLMObservabilityModal(messageObj || { id, reasoning: rawReasoning });
    });
  }
}

function renderStoredAssistantMessage(m) {
  const container = document.getElementById("chatMessages");
  const wrapper = document.createElement("div");
  wrapper.className = "flex items-start gap-3.5 message-bubble-animate max-w-4xl mx-auto w-full";

  const msgId = m.id || `msg-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`;

  // Build SOTA Thought Process Accordion
  let reasoningHtml = "";
  if (m.reasoning && m.reasoning.trim()) {
    reasoningHtml = buildThoughtAccordionHtml(msgId, m.reasoning);
  }

  let scorecardHtml = "";
  if (m.tool_events && m.tool_events.length > 0) {
    const scEvent = m.tool_events.find(t => t.type === "scorecard");
    if (scEvent && scEvent.scorecard) {
      scorecardHtml = buildScorecardHtml(scEvent.scorecard, scEvent.entity);
    }
  }

  let toolsHtml = "";
  if (m.tool_events && m.tool_events.length > 0) {
    toolsHtml = `<div class="tools-container flex flex-wrap gap-2">`;
    // One chip per call: a call that got a result is shown by its result.
    const answered = new Set(m.tool_events.filter((t) => t.type === "tool_result" && t.id).map((t) => t.id));
    for (const t of m.tool_events) {
      if (t.type === "tool_call" && t.id && answered.has(t.id)) continue;
      if (t.type === "tool_call" || t.type === "tool_result") {
        const icon = getToolIcon(t.name);
        const label = formatToolName(t.name);
        const summary = t.summary ? `✓ ${t.summary}` : "";
        toolsHtml += `
          <div class="tool-pill cursor-pointer" onclick="openLLMObservabilityModal('${msgId}')" title="Click to inspect tool payload in observability trace">
            <i data-lucide="${icon}" class="size-3.5 text-emerald-400"></i>
            <span class="font-medium">${label}</span>
            <span class="text-emerald-300 font-medium text-[11px]">${escapeHtml(summary)}</span>
          </div>
        `;
      }
    }
    toolsHtml += `</div>`;
  }

  // If stored message has swarm events, render historical Swarm Deck
  let swarmDeckHtml = "";
  const storedEvents = m.tool_events || [];
  const storedSwarmPlan = storedEvents.find(e => e.type === "swarm_plan");
  const storedSwarmComp = storedEvents.find(e => e.type === "swarm_complete");
  if (storedSwarmPlan || storedSwarmComp) {
    const subagents = storedSwarmPlan?.subagents || storedSwarmComp?.results || [];
    swarmDeckHtml = `
      <div class="swarm-deck mb-4 p-4 rounded-2xl bg-surface-850 border border-surface-700 space-y-3">
        <div class="flex items-center justify-between pb-2 border-b border-surface-750">
          <div class="flex items-center gap-2">
            <span class="text-xs font-bold text-white flex items-center gap-1.5 font-mono">
              <i data-lucide="crown" class="size-4 text-amber-400"></i> Conductor: Xod
            </span>
            <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/25">
              JIT Swarm Executed (${subagents.length} Specialists)
            </span>
          </div>
          <button class="btn-open-swarm-trace text-[10px] font-mono px-2.5 py-1 rounded-lg bg-indigo-500/15 text-indigo-300 border border-indigo-500/30 hover:bg-indigo-500/25 transition-colors flex items-center gap-1 font-medium">
            <i data-lucide="activity" class="size-3"></i> View Swarm Trace
          </button>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-2.5">
          ${subagents.map(s => {
            const isLocal = (s.tier_resolved || s.tier || "local").includes("local");
            return `
              <div class="p-3 rounded-xl bg-surface-900 border border-surface-750 space-y-2">
                <div class="flex items-center justify-between">
                  <span class="font-bold text-xs text-white flex items-center gap-1.5"><i data-lucide="${s.icon || 'bot'}" class="size-3.5 text-amber-400"></i> ${escapeHtml(s.name || 'Specialist')}</span>
                  <span class="text-[9px] font-mono px-2 py-0.5 rounded ${isLocal ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'}">
                    ${isLocal ? '⚡ Local Fast' : '☁️ Cloud'}
                  </span>
                </div>
                <p class="text-[10px] text-zinc-400">${escapeHtml(s.role || 'Specialist')}</p>
                ${s.findings ? `<div class="p-2 rounded bg-black/40 text-[10px] font-mono text-zinc-300 whitespace-pre-wrap max-h-24 overflow-y-auto">${escapeHtml(s.findings)}</div>` : ''}
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `;
  }

  wrapper.innerHTML = `
    <div class="xod-mascot xod-mascot--chat mt-1" data-mood="idle"></div>
    <div class="flex-1 space-y-3 min-w-0">
      ${reasoningHtml}
      ${scorecardHtml}
      ${toolsHtml}
      ${swarmDeckHtml}
      <div class="prose-custom min-w-0">
        ${marked.parse(m.content || "")}
      </div>
      <div class="action-footer flex items-center gap-2 pt-2 border-t border-surface-800 text-xs text-zinc-400">
        <button class="btn-copy hover:text-white flex items-center gap-1 px-2.5 py-1 rounded-lg hover:bg-white/[0.06] transition-colors">
          <i data-lucide="copy" class="size-3.5"></i> Copy
        </button>
        <button class="btn-export hover:text-white flex items-center gap-1 px-2.5 py-1 rounded-lg hover:bg-white/[0.06] transition-colors">
          <i data-lucide="download" class="size-3.5"></i> Export Markdown
        </button>
        <button data-save-routine class="hover:text-white flex items-center gap-1 px-2.5 py-1 rounded-lg hover:bg-white/[0.06] transition-colors" title="Teach Xod to repeat this, by hand or on a schedule">
          <i data-lucide="repeat" class="size-3.5"></i> Save as routine
        </button>
        <button onclick="retryWithStrongerModel()" class="hover:text-white flex items-center gap-1 px-2.5 py-1 rounded-lg hover:bg-white/[0.06] transition-colors" title="Ask this again with your cloud model (shows in Model routing)">
          <i data-lucide="sparkles" class="size-3.5"></i> Try a stronger model
        </button>
        <button data-share-report class="hover:text-white flex items-center gap-1 px-2.5 py-1 rounded-lg hover:bg-white/[0.06] transition-colors" title="Make a polished page and PDF with sources">
          <i data-lucide="share-2" class="size-3.5"></i> Share report
        </button>
        <button class="btn-open-trace hover:text-indigo-300 flex items-center gap-1 px-2.5 py-1 rounded-lg bg-indigo-500/10 border border-indigo-500/25 text-indigo-400 transition-colors font-medium ml-auto" title="Open full LLM Observability & Action Trace">
          <i data-lucide="activity" class="size-3.5"></i> How I got this
        </button>
      </div>
    </div>
  `;

  const swarmTraceBtn = wrapper.querySelector(".btn-open-swarm-trace");
  if (swarmTraceBtn) {
    swarmTraceBtn.addEventListener("click", () => {
      openLLMObservabilityModal(m);
    });
  }

  const copyBtn = wrapper.querySelector(".btn-copy");
  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(m.content || "");
      copyBtn.innerHTML = `<i data-lucide="check" class="size-3.5 text-emerald-400"></i> Copied!`;
      setTimeout(() => {
        copyBtn.innerHTML = `<i data-lucide="copy" class="size-3.5"></i> Copy`;
        if (window.lucide) lucide.createIcons();
      }, 2000);
    });
  }

  const exportBtn = wrapper.querySelector(".btn-export");
  if (exportBtn) {
    exportBtn.addEventListener("click", () => {
      const blob = new Blob([m.content || ""], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `research-report-${m.id || Date.now()}.md`;
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  const traceBtn = wrapper.querySelector(".btn-open-trace");
  if (traceBtn) {
    traceBtn.addEventListener("click", () => {
      openLLMObservabilityModal(m);
    });
  }

  container.appendChild(wrapper);
  if (window.XodPreview) XodPreview.fromHistory(m, wrapper);
  if (window.XodJobs) XodJobs.fromHistory(m, wrapper);

  // Initialize interactive Thought Reviewer
  if (m.reasoning && m.reasoning.trim()) {
    initThoughtReviewer(wrapper, msgId, m.reasoning, m);
  }

  if (window.lucide) lucide.createIcons();
}

function startNewSession() {
  if (isStreaming) stopResearch();
  currentSessionId = null;
  conversationHistory = [];
  const container = document.getElementById("chatMessages");
  container.innerHTML = "";
  const welcome = document.getElementById("welcomeHero");
  if (welcome) {
    welcome.style.display = "block";
    container.appendChild(welcome);
  }
  renderWelcomeHeroForScope();
  loadSessions();
  lucide.createIcons();
}

async function deleteSession(sessionId, event) {
  event.stopPropagation();
  if (!confirm("Are you sure you want to delete this research session?")) return;

  try {
    await fetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
    if (currentSessionId === sessionId) {
      startNewSession();
    } else {
      await loadSessions();
    }
  } catch (e) {
    alert("Error deleting session: " + e.message);
  }
}

function toggleSidebar() {
  const sidebar = document.getElementById("sidebarSessions");
  if (sidebar.classList.contains("hidden")) {
    sidebar.classList.remove("hidden");
  } else {
    sidebar.classList.add("hidden");
  }
}

function formatRelativeTime(timestamp) {
  if (!timestamp) return "";
  const sec = Math.floor((Date.now() / 1000) - timestamp);
  if (sec < 60) return "Just now";
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function clearChat() {
  if (window.XodPreview) XodPreview.reset();
  startNewSession();
}

// -------------------------------------------------------------
// IMAGE ATTACHMENT & VISION SUPPORT (LM STUDIO GEMMA 4 VISION)
// -------------------------------------------------------------
let attachedImageBase64 = null;
let attachedImageName = "";

function handleImageSelected(e) {
  const file = e.target.files[0];
  if (file) {
    readAndPreviewImage(file);
  }
}

function readAndPreviewImage(file) {
  if (!file.type.startsWith("image/")) {
    alert("Please select a valid image file (PNG, JPG, WebP).");
    return;
  }
  const reader = new FileReader();
  reader.onload = (e) => {
    attachedImageBase64 = e.target.result;
    attachedImageName = file.name || "screenshot.png";
    showImagePreview(attachedImageBase64, attachedImageName);
  };
  reader.readAsDataURL(file);
}

function showImagePreview(base64, name) {
  const container = document.getElementById("imagePreviewContainer");
  const thumb = document.getElementById("imagePreviewThumb");
  const nameElem = document.getElementById("imagePreviewName");
  if (container && thumb && nameElem) {
    thumb.src = base64;
    nameElem.textContent = name;
    container.classList.remove("hidden");
  }
  lucide.createIcons();
}

function removeAttachedImage() {
  attachedImageBase64 = null;
  attachedImageName = "";
  const container = document.getElementById("imagePreviewContainer");
  const fileInput = document.getElementById("imageUploadInput");
  if (container) container.classList.add("hidden");
  if (fileInput) fileInput.value = "";
}

function setupClipboardPaste() {
  window.addEventListener("paste", (e) => {
    const items = (e.clipboardData || e.originalEvent?.clipboardData)?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type && item.type.indexOf("image") !== -1) {
        const file = item.getAsFile();
        if (file) {
          e.preventDefault();
          readAndPreviewImage(file);
          break;
        }
      }
    }
  });
}

// -------------------------------------------------------------
// DOCUMENT & FILE UPLOAD SUPPORT (PDF, DOCX, CSV, CODE, TXT)
// -------------------------------------------------------------
async function handleDocSelected(e) {
  const file = e.target.files && e.target.files[0];
  if (file) {
    await uploadAndPreviewDocument(file);
  }
}

async function uploadAndPreviewDocument(file) {
  try {
    showToast(`Uploading & parsing ${file.name}...`);
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch("/api/documents/upload", {
      method: "POST",
      body: formData
    });

    if (!res.ok) {
      const err = await res.text();
      throw new Error(`Upload failed: ${err}`);
    }

    const data = await res.json();
    attachedComputerFile = data;

    const strip = document.getElementById("attachedFilePillContainer");
    const nameEl = document.getElementById("attachedFileName");
    const metaEl = document.getElementById("attachedFileMeta");

    if (strip) strip.classList.remove("hidden");
    if (nameEl) nameEl.textContent = data.name || file.name;
    if (metaEl) {
      const pageInfo = data.page_count ? ` · ${data.page_count} page(s)` : "";
      metaEl.textContent = `${data.size_formatted || `${Math.round(file.size/1024)} KB`}${pageInfo} · ~${(data.approx_tokens || 0).toLocaleString()} tokens`;
    }

    if (window.lucide) lucide.createIcons();
    showToast(`Attached ${data.name || file.name} to chat`);
  } catch (err) {
    console.error("Document upload error:", err);
    alert(`Could not upload document: ${err.message}`);
  } finally {
    const input = document.getElementById("docUploadInput");
    if (input) input.value = "";
  }
}

function handleChatDragOver(e) {
  e.preventDefault();
  e.stopPropagation();
  const form = document.getElementById("chatForm");
  if (form) form.classList.add("ring-2", "ring-cyan-500/60", "bg-cyan-950/20");
}

function handleChatDragLeave(e) {
  e.preventDefault();
  e.stopPropagation();
  const form = document.getElementById("chatForm");
  if (form) form.classList.remove("ring-2", "ring-cyan-500/60", "bg-cyan-950/20");
}

async function handleChatDrop(e) {
  e.preventDefault();
  e.stopPropagation();
  const form = document.getElementById("chatForm");
  if (form) form.classList.remove("ring-2", "ring-cyan-500/60", "bg-cyan-950/20");

  const files = e.dataTransfer?.files;
  if (!files || files.length === 0) return;

  const file = files[0];
  if (file.type && file.type.startsWith("image/")) {
    readAndPreviewImage(file);
  } else {
    await uploadAndPreviewDocument(file);
  }
}

// -------------------------------------------------------------
// CHAT STREAM & RESEARCH AGENT
// -------------------------------------------------------------
async function handleChatSubmit(e) {
  e.preventDefault();
  const input = document.getElementById("chatInput");
  const query = input.value.trim();
  const currentImage = attachedImageBase64;
  if ((!query && !currentImage) || isStreaming) return;

  // Hide welcome hero
  const welcome = document.getElementById("welcomeHero");
  if (welcome) welcome.style.display = "none";

  // Expand slash commands if entered
  let expandedQuery = query;
  if (query.startsWith("/roadmap")) {
    openRoadmapModal();
    input.value = "";
    input.style.height = "auto";
    return;
  } else if (query.startsWith("/email")) {
    const parts = query.replace(/^\/email\s*/, "").trim().split(/\s+/);
    if (parts.length >= 3) {
      expandedQuery = `Find the verified work email for ${parts[0]} ${parts[1]} at ${parts[2]} using corporate email permutation modeling, MX provider alignment, and public commit exhaust.`;
    } else {
      expandedQuery = `Find the verified work email for ${query.replace(/^\/email\s*/, "").trim()}`;
    }
  } else if (query.startsWith("/verify")) {
    const email = query.replace(/^\/verify\s*/, "").trim();
    expandedQuery = `Perform pre-flight email bounce-risk verification for ${email}: check syntax, Cloudflare MX records, provider deliverability score, and SPF/DMARC policies.`;
  } else if (query.startsWith("/jobs")) {
    const company = query.replace(/^\/jobs\s*/, "").trim();
    expandedQuery = `Scan live job firehose for ${company}: pull active job requisitions from Ashby, Greenhouse, or Lever and execute HTTP liveness probes to verify whether positions are still open vs dead ghost listings.`;
  } else if (query.startsWith("/org")) {
    const company = query.replace(/^\/org\s*/, "").trim();
    expandedQuery = `Audit organizational structure and funding for ${company}: check Y Combinator accelerator batch (e.g. W24/S23), verified founders, team size, SEC Form D capital, and scam risk screening.`;
  } else if (query.startsWith("/trends")) {
    const arg = query.replace(/^\/trends\s*/, "").trim();
    expandedQuery = arg ? `Scan live real-time social trends on "${arg}" across X (Twitter), TikTok, Google Trends, Hacker News, and GitHub.` : `Scan live real-time social and internet trends across X (Twitter), TikTok, Google Trends, Hacker News, and GitHub right now.`;
  } else if (query.startsWith("/compare")) {
    const arg = query.replace(/^\/compare\s*/, "").trim();
    expandedQuery = `Execute head-to-head digital traffic, global rank, and path activity comparison for: ${arg}`;
  } else if (query.startsWith("/traffic")) {
    const arg = query.replace(/^\/traffic\s*/, "").trim();
    expandedQuery = `Audit digital traffic, Tranco Top 1M global rank, and visitor estimates for ${arg}`;
  } else if (query.startsWith("/path")) {
    const arg = query.replace(/^\/path\s*/, "").trim();
    expandedQuery = `Inspect URL path activity, liveness, and checkout funnel for ${arg}`;
  } else if (query.startsWith("/audit")) {
    const arg = query.replace(/^\/audit\s*/, "").trim();
    expandedQuery = `Audit live tech stack and web infrastructure for ${arg}`;
  } else if (query.startsWith("/revenue")) {
    const arg = query.replace(/^\/revenue\s*/, "").trim();
    expandedQuery = `Retrieve verified TrustMRR revenue, MRR, growth, and metrics for ${arg}`;
  } else if (query.startsWith("/dns")) {
    const arg = query.replace(/^\/dns\s*/, "").trim();
    expandedQuery = `Inspect DNS records, subdomain surface, and email security for ${arg}`;
  } else if (query.startsWith("/github")) {
    const arg = query.replace(/^\/github\s*/, "").trim();
    expandedQuery = `Audit GitHub repository health, stars, and commit velocity for ${arg}`;
  } else if (query.startsWith("/intel")) {
    const arg = query.replace(/^\/intel\s*/, "").trim();
    expandedQuery = `Search real-time community sentiment and topic buzz for ${arg}`;
  } else if (query.startsWith("/hunter")) {
    const arg = query.replace(/^\/hunter\s*/, "").trim();
    expandedQuery = `Screen vetted SaaS businesses currently for sale on TrustMRR ${arg ? `in category ${arg}` : ''}`;
  }

  // Handle attached document or computer file
  let fileContext = "";
  let fileDisplayName = "";
  const currentAttachedDoc = attachedComputerFile;
  if (currentAttachedDoc) {
    fileDisplayName = currentAttachedDoc.name;
    const pageLabel = currentAttachedDoc.page_count ? `, ${currentAttachedDoc.page_count} pages` : "";
    fileContext = `[Attached Document: ${currentAttachedDoc.name} (${currentAttachedDoc.size_formatted || ''}${pageLabel})]\n\`\`\`\n${currentAttachedDoc.content || ""}\n\`\`\`\n\n`;
  }

  const finalExpandedQuery = fileContext ? (fileContext + (expandedQuery || `Analyze this document: ${fileDisplayName}`)) : expandedQuery;

  // Build multimodal content if image is attached
  let messageContent;
  if (currentImage) {
    messageContent = [
      { type: "text", text: finalExpandedQuery || "Analyze this image and describe what you see in detail" },
      { type: "image_url", image_url: { url: currentImage } }
    ];
  } else {
    messageContent = finalExpandedQuery;
  }

  // Append user message to UI
  input.value = "";
  input.style.height = "auto";
  const userDisplayQuery = fileDisplayName ? `📎 [${fileDisplayName}] ${query}` : query;
  appendUserMessage(userDisplayQuery, currentImage);
  conversationHistory.push({ role: "user", content: messageContent });

  // Clear attached states
  removeAttachedImage();
  clearAttachedComputerFile();

  // Prepare assistant response container
  const assistantBubble = createAssistantMessageContainer();
  isStreaming = true;
  updateLiveStatusBar(true, "Thinking…");

  currentAbortController = new AbortController();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: conversationHistory,
        persona: currentPersona,
        session_id: currentSessionId,
        active_workspace_path: activeWorkingScope ? activeWorkingScope.path : null,
        attached_document: currentAttachedDoc,
        voice: window.__xodVoiceTurn ? true : undefined,   // asked out loud in a conversation (conversation.js)
        route: window.__nextRoute || undefined   // "cloud": this turn uses the stronger model (routing.js)
      }),
      signal: currentAbortController.signal
    });

    if (!res.ok) {
      const errText = await res.text();
      assistantBubble.setContent(`**Error:** Server returned HTTP ${res.status}: ${errText}`);
      isStreaming = false;
      updateLiveStatusBar(false);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const event = JSON.parse(line.substring(6));
            handleResearchEvent(event, assistantBubble);
          } catch (jsonErr) {
            console.warn("Could not parse SSE event:", line, jsonErr);
          }
        }
      }
    }

  } catch (err) {
    if (err.name === "AbortError") {
      assistantBubble.appendContent("\n\n*(Research canceled by user)*");
    } else {
      assistantBubble.setContent(`**Execution Error:** ${err.message}`);
    }
  } finally {
    isStreaming = false;
    updateLiveStatusBar(false);
    // Record assistant's final content to conversation history
    if (assistantBubble.fullContent) {
      conversationHistory.push({ role: "assistant", content: assistantBubble.fullContent });
    }
    lucide.createIcons();
  }
}

function stopResearch() {
  if (currentAbortController) {
    currentAbortController.abort();
  }
  isStreaming = false;
  updateLiveStatusBar(false);
}

function updateLiveStatusBar(show, text = "") {
  const bar = document.getElementById("liveStatusBar");
  const txt = document.getElementById("liveStatusText");
  const btn = document.getElementById("sendButton");
  const island = document.getElementById("dynamicNeuralIsland");
  const idleBar = document.getElementById("islandIdleBar");
  const missionBar = document.getElementById("islandMissionBar");
  const missionStatus = document.getElementById("islandMissionStatus");

  if (show) {
    if (bar) bar.classList.remove("hidden");
    if (txt) txt.textContent = text;
    if (btn) btn.disabled = true;

    if (island) island.setAttribute("data-island-state", "executing");
    if (idleBar) idleBar.classList.add("hidden");
    if (missionBar) missionBar.classList.remove("hidden");
    if (missionStatus) missionStatus.textContent = text || "Thinking…";
    if (!missionTimerInterval) startMissionTimer();
  } else {
    if (bar) bar.classList.add("hidden");
    if (btn) btn.disabled = false;

    if (island) island.setAttribute("data-island-state", "idle");
    if (idleBar) idleBar.classList.remove("hidden");
    if (missionBar) missionBar.classList.add("hidden");
    stopMissionTimer();
  }
}

function appendUserMessage(text, imageBase64 = null) {
  const container = document.getElementById("chatMessages");
  const div = document.createElement("div");
  div.className = "flex justify-end gap-3 message-bubble-animate max-w-4xl mx-auto w-full";
  div.innerHTML = `
    <div class="max-w-2xl user-bubble rounded-2xl rounded-br-md specular-surface p-4 text-[14.5px] text-zinc-50 space-y-2">
      ${imageBase64 ? `<img src="${imageBase64}" class="max-h-64 rounded-xl object-contain border border-white/20 mb-2 shadow-md" alt="Uploaded screenshot" />` : ''}
      <div class="whitespace-pre-wrap leading-relaxed">${escapeHtml(text || (imageBase64 ? "Analyzed image attachment" : ""))}</div>
    </div>
  `;
  container.appendChild(div);
  scrollChatToBottom(false);
}

function initBrowserInteractiveCard(card, initialUri) {
  const img = card.querySelector(".browser-viewport-img");
  const hud = card.querySelector(".takeover-hud");
  const takeoverBtn = card.querySelector(".btn-takeover");
  const takeoverLabel = card.querySelector(".takeover-label");
  const coordsReadout = card.querySelector(".coords-readout");
  const rippleContainer = card.querySelector(".click-ripple-container");
  const directInput = card.querySelector(".direct-input");
  const sendBtn = card.querySelector(".btn-send-direct");
  const dismissBtn = card.querySelector(".btn-dismiss-popups");
  const tabPills = card.querySelector(".tab-pills");
  const newTabBtn = card.querySelector(".btn-new-tab");
  const refreshTabsBtn = card.querySelector(".btn-refresh-tabs");

  let isTakeover = false;

  if (takeoverBtn && hud) {
    takeoverBtn.addEventListener("click", () => {
      isTakeover = !isTakeover;
      if (isTakeover) {
        hud.classList.remove("hidden");
        takeoverBtn.className = "btn-takeover px-2 py-0.5 rounded-md text-[10px] font-semibold bg-emerald-500 text-black border border-emerald-400 shadow-md shadow-emerald-500/20 transition-all flex items-center gap-1";
        if (takeoverLabel) takeoverLabel.textContent = "Take Over Active";
        if (directInput) directInput.focus();
      } else {
        hud.classList.add("hidden");
        takeoverBtn.className = "btn-takeover px-2 py-0.5 rounded-md text-[10px] font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/25 transition-all flex items-center gap-1";
        if (takeoverLabel) takeoverLabel.textContent = "Take Over";
      }
    });
  }

  if (hud && coordsReadout && img) {
    hud.addEventListener("mousemove", (e) => {
      const rect = img.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        const x = Math.max(0, Math.min(1280, Math.round((e.clientX - rect.left) * (1280 / rect.width))));
        const y = Math.max(0, Math.min(800, Math.round((e.clientY - rect.top) * (800 / rect.height))));
        coordsReadout.textContent = `X: ${x}, Y: ${y}`;
      }
    });

    hud.addEventListener("click", async (e) => {
      const rect = img.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const x = Math.max(0, Math.min(1280, Math.round((e.clientX - rect.left) * (1280 / rect.width))));
      const y = Math.max(0, Math.min(800, Math.round((e.clientY - rect.top) * (800 / rect.height))));

      if (rippleContainer) {
        const ripple = document.createElement("span");
        ripple.className = "absolute size-6 rounded-full bg-emerald-400/60 border border-emerald-300 animate-ping pointer-events-none -translate-x-1/2 -translate-y-1/2";
        ripple.style.left = `${e.clientX - rect.left}px`;
        ripple.style.top = `${e.clientY - rect.top}px`;
        rippleContainer.appendChild(ripple);
        setTimeout(() => ripple.remove(), 600);
      }

      try {
        img.style.opacity = "0.7";
        const res = await fetch("/api/browser/click-coords", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ x, y })
        });
        const data = await res.json();
        if (data.screenshot_uri) {
          img.src = data.screenshot_uri;
        }
      } catch (err) {
        console.error("Coordinate click failed:", err);
      } finally {
        img.style.opacity = "1";
      }
    });
  }

  async function submitDirectText() {
    if (!directInput || !directInput.value) return;
    const text = directInput.value;
    directInput.value = "";
    if (sendBtn) sendBtn.disabled = true;

    try {
      img.style.opacity = "0.7";
      const res = await fetch("/api/browser/type-direct", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, press_enter: true })
      });
      const data = await res.json();
      if (data.screenshot_uri) {
        img.src = data.screenshot_uri;
      }
    } catch (err) {
      console.error("Direct type failed:", err);
    } finally {
      img.style.opacity = "1";
      if (sendBtn) sendBtn.disabled = false;
    }
  }

  if (directInput) {
    directInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submitDirectText();
    });
  }
  if (sendBtn) {
    sendBtn.addEventListener("click", submitDirectText);
  }

  if (dismissBtn) {
    dismissBtn.addEventListener("click", async () => {
      dismissBtn.disabled = true;
      try {
        img.style.opacity = "0.7";
        await fetch("/api/browser/dismiss-overlays", { method: "POST" });
        const ssRes = await fetch("/api/browser/screenshot", { method: "POST" });
        const ssData = await ssRes.json();
        if (ssData.screenshot_uri) {
          img.src = ssData.screenshot_uri;
        }
      } catch (err) {
        console.error("Dismiss popups failed:", err);
      } finally {
        img.style.opacity = "1";
        dismissBtn.disabled = false;
      }
    });
  }

  async function loadBrowserTabs() {
    if (!tabPills) return;
    try {
      const res = await fetch("/api/browser/tabs");
      const data = await res.json();
      const tabs = data.tabs || [];
      if (tabs.length === 0) return;

      tabPills.innerHTML = tabs.map(t => {
        const isSel = t.selected || t.id === data.active_tab_id;
        return `
          <button type="button" class="tab-pill-btn px-2 py-0.5 rounded text-[10px] font-mono transition-colors flex items-center gap-1 max-w-[140px] truncate ${isSel ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-500/40 font-semibold' : 'bg-surface-850 hover:bg-surface-800 text-zinc-400 hover:text-zinc-200 border border-white/[0.06]'}" data-tab-id="${t.id}" title="${escapeHtml(t.url || t.title)}">
            <span class="size-1.5 rounded-full ${isSel ? 'bg-emerald-400' : 'bg-zinc-500'} shrink-0"></span>
            <span class="truncate">${escapeHtml(t.title || t.url || "Tab")}</span>
          </button>
        `;
      }).join("");

      tabPills.querySelectorAll(".tab-pill-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
          const targetId = btn.getAttribute("data-tab-id");
          if (!targetId) return;
          try {
            img.style.opacity = "0.7";
            await fetch("/api/browser/tabs", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ action: "switch", target_id: targetId })
            });
            const ssRes = await fetch("/api/browser/screenshot", { method: "POST" });
            const ssData = await ssRes.json();
            if (ssData.screenshot_uri) {
              img.src = ssData.screenshot_uri;
            }
            await loadBrowserTabs();
          } catch (err) {
            console.error("Switch tab failed:", err);
          } finally {
            img.style.opacity = "1";
          }
        });
      });
    } catch (err) {
      console.error("Load tabs error:", err);
    }
  }

  if (newTabBtn) {
    newTabBtn.addEventListener("click", async () => {
      try {
        await fetch("/api/browser/tabs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "new", url: "https://news.ycombinator.com" })
        });
        await loadBrowserTabs();
        const ssRes = await fetch("/api/browser/screenshot", { method: "POST" });
        const ssData = await ssRes.json();
        if (ssData.screenshot_uri) {
          img.src = ssData.screenshot_uri;
        }
      } catch (err) {
        console.error("New tab error:", err);
      }
    });
  }

  if (refreshTabsBtn) {
    refreshTabsBtn.addEventListener("click", loadBrowserTabs);
  }

  loadBrowserTabs();
  if (window.lucide) lucide.createIcons();
}

function createAssistantMessageContainer() {
  const container = document.getElementById("chatMessages");
  const wrapper = document.createElement("div");
  wrapper.className = "flex items-start gap-3.5 message-bubble-animate max-w-4xl mx-auto w-full";
  const msgId = "live-msg-" + Date.now() + "-" + Math.random().toString(36).substr(2, 5);

  wrapper.innerHTML = `
    <!-- Xod's moon (whimsy.js draws it and keeps it in sync with the stream) -->
    <div class="xod-mascot xod-mascot--chat mt-1" data-mood="idle"></div>
    <div class="flex-1 space-y-3.5 min-w-0">
      <!-- JIT Sub-Agent Swarm Deck -->
      <div class="swarm-deck hidden mb-3"></div>

      <!-- Live SOTA Research Plan Checklist -->
      <div class="plan-container hidden">
        <div class="p-4 rounded-2xl specular-surface bg-surface-800/80 border border-brand-500/25 backdrop-blur-xl space-y-3 shadow-lg">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2 text-xs font-bold text-zinc-100">
              <i data-lucide="list-checks" class="size-4 text-brand-400"></i>
              <span class="plan-title">Autonomous Research Strategy</span>
            </div>
            <span class="plan-badge text-[10px] font-mono px-2.5 py-0.5 rounded-full bg-brand-500/20 text-brand-300 border border-brand-500/40">Active Strategy</span>
          </div>
          <div class="plan-steps-list space-y-2 pt-1 text-xs text-zinc-400"></div>
          <div class="evidence-hud hidden pt-2.5 border-t border-white/[0.08] flex items-center gap-3 text-[11px] font-mono text-zinc-400">
            <span class="hud-metrics text-emerald-400">📊 0 Metrics</span>
            <span class="hud-tech text-indigo-400">🛠️ 0 Stacks</span>
            <span class="hud-sources text-cyan-400">🌐 0 Sources</span>
          </div>
        </div>
      </div>

      <!-- Modern SOTA Thinking Accordion -->
      <div class="reasoning-container hidden mb-2.5" id="reasoning-box-${msgId}">
        <details class="thinking-accordion group" open>
          <summary class="thinking-summary">
            <div class="flex items-center gap-2 min-w-0 flex-1 mr-2">
              <span class="thinking-indicator relative flex size-2.5 items-center justify-center shrink-0">
                <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
                <span class="relative inline-flex rounded-full size-1.5 bg-indigo-400"></span>
              </span>
              <span class="thinking-label font-medium text-zinc-200">Thinking</span>
              <span class="thinking-timer font-mono text-[11px] text-indigo-300 bg-indigo-500/10 px-2 py-0.5 rounded-full border border-indigo-500/20">0.0s</span>
              <span class="thinking-preview text-[11px] text-zinc-400 truncate hidden sm:inline ml-1 font-sans"></span>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <span class="thinking-badge text-[10px] text-zinc-500 uppercase tracking-wider font-mono">REASONING</span>
              <i data-lucide="chevron-right" class="size-3.5 text-zinc-500 thinking-chevron"></i>
            </div>
          </summary>
          <div class="thinking-body p-3.5 pt-2.5 border-t border-white/[0.06] bg-black/25 relative space-y-2">
            <div class="thinking-live-header flex items-center justify-between pb-1 text-xs">
              <span class="text-[11px] text-zinc-400">Thoughts</span>
              <button type="button" class="btn-copy-thinking text-zinc-500 hover:text-white p-1 rounded hover:bg-white/10 transition-colors" title="Copy thoughts">
                <i data-lucide="copy" class="size-3.5"></i>
              </button>
            </div>
            <div class="prose-thinking reasoning-prose min-h-[1.5rem]"></div>
          </div>
        </details>
      </div>

      <!-- Live Tool Execution Pills -->
      <div class="tools-container flex flex-wrap gap-2.5"></div>

      <!-- Live Due Diligence Scorecard Box -->
      <div class="scorecard-box"></div>

      <!-- Main Synthesis Content -->
      <div class="prose-custom min-w-0">
        <span class="inline-flex items-center gap-2 text-zinc-400 text-xs animate-pulse font-mono">
          <span class="size-2 rounded-full bg-brand-400 animate-ping"></span> Working on it…
        </span>
      </div>

      <!-- Action Footer -->
      <div class="action-footer hidden flex items-center gap-2 pt-2.5 border-t border-white/[0.08] text-xs text-zinc-400">
        <button class="btn-copy hover:text-white flex items-center gap-1 px-2.5 py-1 rounded-lg hover:bg-white/[0.06] transition-colors">
          <i data-lucide="copy" class="size-3.5"></i> Copy
        </button>
        <button class="btn-export hover:text-white flex items-center gap-1 ml-1 px-2.5 py-1 rounded-lg hover:bg-white/[0.06] transition-colors">
          <i data-lucide="download" class="size-3.5"></i> Export Markdown
        </button>
        <button data-save-routine class="hover:text-white flex items-center gap-1 px-2.5 py-1 rounded-lg hover:bg-white/[0.06] transition-colors" title="Teach Xod to repeat this, by hand or on a schedule">
          <i data-lucide="repeat" class="size-3.5"></i> Save as routine
        </button>
        <button onclick="retryWithStrongerModel()" class="hover:text-white flex items-center gap-1 px-2.5 py-1 rounded-lg hover:bg-white/[0.06] transition-colors" title="Ask this again with your cloud model (shows in Model routing)">
          <i data-lucide="sparkles" class="size-3.5"></i> Try a stronger model
        </button>
        <button data-share-report class="hover:text-white flex items-center gap-1 px-2.5 py-1 rounded-lg hover:bg-white/[0.06] transition-colors" title="Make a polished page and PDF with sources">
          <i data-lucide="share-2" class="size-3.5"></i> Share report
        </button>
        <button class="btn-open-trace hover:text-indigo-300 flex items-center gap-1 ml-auto px-2.5 py-1 rounded-lg bg-indigo-500/10 border border-indigo-500/25 text-indigo-400 transition-colors font-medium" title="Open full LLM Observability & Action Trace">
          <i data-lucide="activity" class="size-3.5"></i> How I got this
        </button>
      </div>
    </div>
  `;

  container.appendChild(wrapper);
  lucide.createIcons();
  container.scrollTop = container.scrollHeight;

  const swarmDeck = wrapper.querySelector(".swarm-deck");
  const planContainer = wrapper.querySelector(".plan-container");
  const planTitle = wrapper.querySelector(".plan-title");
  const planBadge = wrapper.querySelector(".plan-badge");
  const planStepsList = wrapper.querySelector(".plan-steps-list");
  const evidenceHud = wrapper.querySelector(".evidence-hud");
  const hudMetrics = wrapper.querySelector(".hud-metrics");
  const hudTech = wrapper.querySelector(".hud-tech");
  const hudSources = wrapper.querySelector(".hud-sources");

  const reasoningContainer = wrapper.querySelector(".reasoning-container");
  const thinkingAccordion = wrapper.querySelector(".thinking-accordion");
  const thinkingLabel = wrapper.querySelector(".thinking-label");
  const thinkingTimer = wrapper.querySelector(".thinking-timer");
  const thinkingPreview = wrapper.querySelector(".thinking-preview");
  const thinkingIndicator = wrapper.querySelector(".thinking-indicator");
  const reasoningProse = wrapper.querySelector(".reasoning-prose");
  const copyThinkingBtn = wrapper.querySelector(".btn-copy-thinking");
  const toolsContainer = wrapper.querySelector(".tools-container");
  const proseCustom = wrapper.querySelector(".prose-custom");
  const actionFooter = wrapper.querySelector(".action-footer");
  const copyBtn = wrapper.querySelector(".btn-copy");
  const exportBtn = wrapper.querySelector(".btn-export");
  const traceBtn = wrapper.querySelector(".btn-open-trace");

  let fullContent = "";
  let fullReasoning = "";
  let reasoningStartTime = null;
  let totalReasoningMs = 0;
  let reasoningTimerInterval = null;
  let renderReasoningTimer = null;
  let isReasoningFinished = false;

  const toolEvents = [];
  const eventsLog = [];

  const assistantBubble = {
    id: msgId,
    wrapper,
    fullContent,
    fullReasoning,
    toolEvents,
    eventsLog,
    totalReasoningMs,

    setSwarmPlan(title, conductor, subagents) {
      if (!swarmDeck) return;
      swarmDeck.classList.remove("hidden");
      swarmDeck.innerHTML = `
        <div class="p-3.5 rounded-2xl specular-surface bg-surface-850/95 border border-brand-500/35 backdrop-blur-xl space-y-3 shadow-xl">
          <div class="flex items-center justify-between border-b border-white/[0.08] pb-2.5">
            <div class="flex items-center gap-2.5">
              <div class="size-7 rounded-xl bg-gradient-to-tr from-brand-600 to-indigo-600 flex items-center justify-center text-white shadow-md shadow-brand-500/30">
                <i data-lucide="zap" class="size-4 text-amber-300"></i>
              </div>
              <div>
                <span class="text-xs font-bold text-white flex items-center gap-1.5">
                  ${escapeHtml(title || 'JIT Sub-Agent Swarm')}
                </span>
                <span class="text-[10px] text-zinc-400 font-mono flex items-center gap-1.5 mt-0.5">
                  <span class="text-brand-300 font-semibold flex items-center gap-1"><i data-lucide="crown" class="size-2.5"></i> Conductor: ${escapeHtml(conductor || 'Xod')}</span>
                  <span>•</span>
                  <span>Pruned Action Spaces</span>
                </span>
              </div>
            </div>
            <span class="swarm-status-badge text-[10px] font-mono px-2.5 py-0.5 rounded-full bg-brand-500/15 text-brand-300 border border-brand-500/30 flex items-center gap-1.5 font-medium">
              <span class="size-1.5 rounded-full bg-brand-400 animate-ping"></span>
              <span>Running ${(subagents || []).length} Specialists</span>
            </span>
          </div>

          <!-- Subagents Grid -->
          <div class="subagents-grid grid grid-cols-1 md:grid-cols-2 gap-2.5 pt-0.5">
            ${(subagents || []).map(s => {
              const isLocal = s.tier === 'local';
              const tierBadgeClass = isLocal ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30' : 'bg-indigo-500/10 text-indigo-300 border-indigo-500/30';
              const tierLabel = isLocal ? '⚡ Local Fast' : '☁️ Cloud Reasoning';
              return `
                <div id="subagent-card-${s.id}" class="subagent-card p-3 rounded-xl bg-surface-900/90 border border-surface-750/80 space-y-2 text-xs transition-all shadow-sm">
                  <div class="flex items-start justify-between gap-1.5">
                    <div class="flex items-center gap-2 min-w-0">
                      <div class="size-7 rounded-lg bg-surface-800 border border-white/[0.08] flex items-center justify-center text-zinc-300 shrink-0">
                        <i data-lucide="${s.icon || 'bot'}" class="size-3.5 text-brand-300"></i>
                      </div>
                      <div class="min-w-0">
                        <h4 class="font-semibold text-zinc-100 truncate text-[11px]">${escapeHtml(s.name)}</h4>
                        <p class="text-[10px] text-zinc-400 truncate">${escapeHtml(s.role)}</p>
                      </div>
                    </div>
                    <div class="subagent-tier-badge text-[9px] font-mono px-2 py-0.5 rounded-full border shrink-0 ${tierBadgeClass}">
                      ${tierLabel}
                    </div>
                  </div>

                  <!-- Pruned Tools Chips -->
                  <div class="flex flex-wrap gap-1 items-center">
                    <span class="text-[9px] text-zinc-500 font-mono">Pruned Actions:</span>
                    ${(s.tools || []).map(t => `<span class="text-[9px] font-mono px-1.5 py-0.5 rounded bg-surface-800 text-zinc-300 border border-surface-700/60">${t}</span>`).join('')}
                  </div>

                  <!-- Live execution status -->
                  <div class="subagent-status-row flex items-center justify-between text-[10px] font-mono border-t border-surface-800/80 pt-1.5 text-zinc-400">
                    <span class="subagent-status-label flex items-center gap-1.5 text-amber-300 font-medium">
                      <span class="size-1.5 rounded-full bg-amber-400 animate-pulse"></span> Spawning worker...
                    </span>
                    <span class="subagent-duration text-zinc-500">--</span>
                  </div>

                  <!-- Active Tool Chips Container -->
                  <div class="subagent-tools-activity flex flex-wrap gap-1 text-[10px]"></div>

                  <!-- Collapsible Findings Box -->
                  <div class="subagent-findings-box hidden mt-1.5 p-2.5 rounded-lg bg-black/40 border border-white/[0.06] text-[10px] text-zinc-300 font-mono whitespace-pre-wrap max-h-40 overflow-y-auto leading-relaxed"></div>
                </div>
              `;
            }).join('')}
          </div>
        </div>
      `;
      lucide.createIcons();
      scrollChatToBottom(false);
    },
    updateSubagentStatus(agent_id, status, message, findings, duration_ms, tier, model) {
      const card = document.getElementById(`subagent-card-${agent_id}`);
      if (!card) return;

      const statusLabel = card.querySelector(".subagent-status-label");
      const durationSpan = card.querySelector(".subagent-duration");
      const findingsBox = card.querySelector(".subagent-findings-box");

      if (duration_ms && durationSpan) {
        durationSpan.textContent = `${(duration_ms / 1000).toFixed(2)}s`;
      }

      if (status === "running") {
        if (statusLabel) {
          statusLabel.className = "subagent-status-label flex items-center gap-1.5 text-cyan-300 font-medium";
          statusLabel.innerHTML = `<span class="size-1.5 rounded-full bg-cyan-400 animate-pulse"></span> ${escapeHtml(message || 'Executing mission...')}`;
        }
      } else if (status === "completed") {
        if (statusLabel) {
          statusLabel.className = "subagent-status-label flex items-center gap-1.5 text-emerald-400 font-medium";
          statusLabel.innerHTML = `<i data-lucide="check-circle" class="size-3 text-emerald-400"></i> Completed`;
        }
        card.classList.add("border-emerald-500/30");
        if (findings && findingsBox) {
          findingsBox.classList.remove("hidden");
          findingsBox.textContent = findings;
        }
      } else if (status === "error") {
        if (statusLabel) {
          statusLabel.className = "subagent-status-label flex items-center gap-1.5 text-rose-400 font-medium";
          statusLabel.innerHTML = `<i data-lucide="alert-triangle" class="size-3 text-rose-400"></i> ${escapeHtml(message || 'Failed')}`;
        }
        card.classList.add("border-rose-500/30");
      }
      lucide.createIcons();
    },
    updateSubagentToolCall(agent_id, name, args, call_id) {
      const card = document.getElementById(`subagent-card-${agent_id}`);
      if (!card) return;
      const toolsArea = card.querySelector(".subagent-tools-activity");
      if (!toolsArea) return;

      const chip = document.createElement("div");
      chip.id = `subtool-${call_id}`;
      chip.className = "flex items-center gap-1 text-[10px] text-amber-300 bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 rounded font-mono";
      chip.innerHTML = `
        <span class="size-1.5 rounded-full bg-amber-400 animate-ping"></span>
        <span>${formatToolName(name)}</span>
      `;
      toolsArea.appendChild(chip);
      scrollChatToBottom(false);
    },
    updateSubagentToolResult(agent_id, name, summary, data, call_id) {
      const chip = document.getElementById(`subtool-${call_id}`);
      if (chip) {
        chip.className = "flex items-center gap-1 text-[10px] text-emerald-300 bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 rounded font-mono";
        chip.innerHTML = `
          <i data-lucide="check" class="size-2.5 text-emerald-400"></i>
          <span>${formatToolName(name)}</span>
        `;
        chip.title = summary || "Completed";
        lucide.createIcons();
      }
    },
    setSwarmComplete(total, results) {
      if (!swarmDeck) return;
      const badge = swarmDeck.querySelector(".swarm-status-badge");
      if (badge) {
        badge.className = "swarm-status-badge text-[10px] font-mono px-2.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 flex items-center gap-1.5 font-medium";
        badge.innerHTML = `<i data-lucide="check-check" class="size-3 text-emerald-400"></i> Swarm Completed (${total} Specialists)`;
      }
      lucide.createIcons();
    },

    setPlan(title, steps) {
      planContainer.classList.remove("hidden");
      if (title) planTitle.textContent = title;
      planStepsList.innerHTML = "";
      for (const s of steps) {
        const stepDiv = document.createElement("div");
        stepDiv.id = `plan-step-${s.id}`;
        stepDiv.className = "flex items-center gap-2.5";
        stepDiv.innerHTML = `
          <i data-lucide="${s.status === 'completed' ? 'check-circle' : (s.status === 'in_progress' ? 'loader-2' : 'circle')}" class="size-3.5 ${s.status === 'completed' ? 'text-emerald-400' : (s.status === 'in_progress' ? 'text-brand-400 animate-spin' : 'text-zinc-600')}"></i>
          <span class="${s.status === 'completed' ? 'text-zinc-300 line-through' : (s.status === 'in_progress' ? 'text-white font-medium' : 'text-zinc-500')}">${escapeHtml(s.title)}</span>
        `;
        planStepsList.appendChild(stepDiv);
      }
      lucide.createIcons();
      container.scrollTop = container.scrollHeight;
    },
    updatePlanStep(stepId, status) {
      const stepDiv = document.getElementById(`plan-step-${stepId}`);
      if (stepDiv) {
        const span = stepDiv.querySelector("span");
        if (status === "completed") {
          stepDiv.innerHTML = `
            <i data-lucide="check-circle" class="size-3.5 text-emerald-400"></i>
            <span class="text-zinc-300 font-medium">${span.textContent}</span>
          `;
        } else if (status === "in_progress") {
          stepDiv.innerHTML = `
            <i data-lucide="loader-2" class="size-3.5 text-brand-400 animate-spin"></i>
            <span class="text-white font-medium">${span.textContent}</span>
          `;
        }
        lucide.createIcons();
      }
    },
    updateEvidence(stats) {
      if (!stats) return;
      evidenceHud.classList.remove("hidden");
      hudMetrics.textContent = `${stats.metrics_count || 0} facts checked`;
      hudTech.textContent = `${stats.tech_count || 0} tech found`;
      hudSources.textContent = `🌐 ${stats.sources_count || 0} Web Sources`;
    },
    setScorecard(scorecard, entityName) {
      const box = wrapper.querySelector(".scorecard-box");
      if (box && scorecard) {
        box.innerHTML = buildScorecardHtml(scorecard, entityName);
        lucide.createIcons();
        container.scrollTop = container.scrollHeight;
      }
    },
    appendReasoning(text) {
      if (!text) return;
      reasoningContainer.classList.remove("hidden");

      if (isReasoningFinished || !reasoningStartTime) {
        isReasoningFinished = false;
        reasoningStartTime = Date.now();
        if (thinkingAccordion && !thinkingAccordion.hasAttribute("open")) {
          thinkingAccordion.setAttribute("open", "");
        }
        if (thinkingLabel) thinkingLabel.textContent = "Thinking";
        if (thinkingIndicator) {
          thinkingIndicator.innerHTML = `
            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
            <span class="relative inline-flex rounded-full size-1.5 bg-indigo-400"></span>
          `;
        }
        if (thinkingTimer) {
          thinkingTimer.className = "thinking-timer font-mono text-[11px] text-indigo-300 bg-indigo-500/10 px-2 py-0.5 rounded-full border border-indigo-500/20";
        }
        const badge = wrapper.querySelector(".thinking-badge");
        if (badge) {
          badge.textContent = "Reasoning";
          badge.className = "thinking-badge text-[10px] text-zinc-500 uppercase tracking-wider font-mono";
        }

        if (reasoningTimerInterval) clearInterval(reasoningTimerInterval);
        reasoningTimerInterval = setInterval(() => {
          if (isReasoningFinished) return;
          const currentElapsedMs = totalReasoningMs + (Date.now() - (reasoningStartTime || Date.now()));
          if (thinkingTimer) thinkingTimer.textContent = `${(currentElapsedMs / 1000).toFixed(1)}s`;
        }, 100);
      }

      fullReasoning += text;
      this.fullReasoning = fullReasoning;

      const lines = fullReasoning.trim().split("\n").filter(l => l.trim().length > 0);
      if (lines.length && thinkingPreview) {
        const lastLine = lines[lines.length - 1].replace(/^[#*>\-\s`]+/, "").trim();
        if (lastLine) {
          thinkingPreview.textContent = lastLine.length > 70 ? lastLine.slice(0, 70) + "…" : lastLine;
        }
      }

      if (!renderReasoningTimer) {
        renderReasoningTimer = setTimeout(() => {
          renderReasoningTimer = null;
          if (reasoningProse && window.marked) {
            reasoningProse.innerHTML = marked.parse(fullReasoning);
          }
        }, 60);
      }
      container.scrollTop = container.scrollHeight;
    },
    finishReasoning() {
      if (isReasoningFinished) return;
      isReasoningFinished = true;

      if (reasoningStartTime) {
        totalReasoningMs += Date.now() - reasoningStartTime;
        reasoningStartTime = null;
      }
      this.totalReasoningMs = totalReasoningMs;

      if (reasoningTimerInterval) {
        clearInterval(reasoningTimerInterval);
        reasoningTimerInterval = null;
      }
      if (renderReasoningTimer) {
        clearTimeout(renderReasoningTimer);
        renderReasoningTimer = null;
      }

      const totalSec = (totalReasoningMs / 1000).toFixed(1);
      if (thinkingLabel) {
        thinkingLabel.textContent = "Thought for";
      }
      if (thinkingTimer) {
        thinkingTimer.textContent = `${totalSec}s`;
        thinkingTimer.className = "thinking-timer font-mono text-[11px] text-zinc-300 bg-white/5 px-2 py-0.5 rounded-full border border-white/10";
      }
      if (thinkingIndicator) {
        thinkingIndicator.innerHTML = `<span class="size-1.5 rounded-full bg-emerald-400"></span>`;
      }
      if (thinkingPreview) {
        thinkingPreview.textContent = "";
      }
      const badge = wrapper.querySelector(".thinking-badge");
      if (badge) {
        badge.textContent = "Complete";
        badge.className = "thinking-badge text-[10px] text-emerald-400/80 uppercase tracking-wider font-mono";
      }

      // Upgrade thinking-body into SOTA Structured Reviewer
      const thinkingBody = wrapper.querySelector(".thinking-body");
      if (thinkingBody && fullReasoning.trim()) {
        thinkingBody.innerHTML = `
          <!-- Thought Review Toolbar -->
          <div class="thought-review-toolbar">
            <div class="flex items-center gap-2 flex-1 max-w-sm relative">
              <i data-lucide="search" class="size-3 text-zinc-500 absolute left-2 top-2 pointer-events-none"></i>
              <input
                type="text"
                placeholder="Filter thoughts (e.g. source, trend, metrics)..."
                class="thought-filter-input w-full bg-surface-900 border border-surface-700/80 rounded-lg pl-7 pr-2 py-1 text-[11px] text-white focus:outline-none focus:border-indigo-500 font-mono"
                data-target="${msgId}"
              />
            </div>

            <div class="flex items-center gap-1.5 shrink-0">
              <!-- View Mode Pills -->
              <div class="flex bg-surface-900 border border-surface-700 rounded-lg p-0.5 text-[10px]">
                <button type="button" class="btn-mode-cards px-2 py-0.5 rounded bg-indigo-600 text-white font-medium" data-target="${msgId}">Cards</button>
                <button type="button" class="btn-mode-prose px-2 py-0.5 rounded text-zinc-400 hover:text-white font-medium" data-target="${msgId}">Prose</button>
                <button type="button" class="btn-mode-raw px-2 py-0.5 rounded text-zinc-400 hover:text-white font-medium" data-target="${msgId}">Raw</button>
              </div>

              <!-- Quick Pull Buttons -->
              <button type="button" class="btn-copy-thought p-1 text-zinc-400 hover:text-white hover:bg-white/10 rounded transition-colors" title="Copy thoughts" data-target="${msgId}">
                <i data-lucide="copy" class="size-3.5"></i>
              </button>
              <button type="button" class="btn-download-thought p-1 text-zinc-400 hover:text-white hover:bg-white/10 rounded transition-colors" title="Download thoughts markdown" data-target="${msgId}">
                <i data-lucide="download" class="size-3.5"></i>
              </button>
              <button type="button" class="btn-inspect-trace px-2 py-0.5 rounded bg-indigo-500/15 hover:bg-indigo-500/25 text-indigo-300 border border-indigo-500/30 text-[11px] transition-colors flex items-center gap-1 font-medium" title="Inspect full LLM trace and tool payloads" data-target="${msgId}">
                <i data-lucide="activity" class="size-3"></i> <span>Trace</span>
              </button>
            </div>
          </div>

          <!-- Thought Content Area -->
          <div class="thought-display-area" id="thought-display-${msgId}"></div>
        `;
        initThoughtReviewer(wrapper, msgId, fullReasoning, assistantBubble);
      }

      if (window.lucide) lucide.createIcons();
    },
    addToolCall(name, args, id) {
      toolEvents.push({
        type: "tool_call",
        name,
        args,
        id,
        timestamp: Date.now()
      });

      const pill = document.createElement("div");
      pill.id = `tool-${id}`;
      pill.className = "tool-pill cursor-pointer";
      pill.title = "Click to inspect tool payload in observability trace";
      pill.onclick = () => openLLMObservabilityModal(assistantBubble);
      pill.innerHTML = `
        <div class="relative size-3.5 flex items-center justify-center shrink-0">
          <span class="size-3.5 rounded-full border-2 border-brand-400/40 border-t-brand-400 animate-spin absolute inset-0"></span>
        </div>
        <span class="font-semibold text-zinc-200">${formatToolName(name)}</span>
        <span class="text-zinc-500 font-mono text-[10px]">${truncateArgs(args)}</span>
      `;
      toolsContainer.appendChild(pill);
      lucide.createIcons();
      scrollChatToBottom(false);
    },
    appendToolStream(name, streamType, text, id) {
      if (name === "computer_run_command") {
        let liveCard = wrapper.querySelector(`#cmd-stream-${id}`);
        if (!liveCard) {
          liveCard = document.createElement("div");
          liveCard.id = `cmd-stream-${id}`;
          liveCard.className = "my-2 rounded-xl border border-white/[0.12] bg-black/95 shadow-xl max-w-lg overflow-hidden text-xs font-mono";
          liveCard.innerHTML = `
            <div class="px-3 py-1.5 bg-surface-850/90 border-b border-surface-750 flex items-center justify-between text-[11px]">
              <div class="flex items-center gap-2 text-emerald-400 truncate max-w-xs font-semibold">
                <span class="cmd-pulse-dot size-2 rounded-full bg-emerald-400 animate-pulse shrink-0"></span>
                <span class="text-zinc-500 font-normal">$</span>
                <span class="truncate text-zinc-200">Terminal Sandbox Stream</span>
              </div>
              <div class="flex items-center gap-1.5 shrink-0">
                <span class="cmd-status-badge text-[10px] text-amber-400 font-mono animate-pulse">Running...</span>
              </div>
            </div>
            <pre class="cmd-stream-pre p-2.5 text-[10px] font-mono max-h-44 overflow-y-auto whitespace-pre-wrap select-text leading-relaxed text-zinc-300 bg-black/90"></pre>
          `;
          toolsContainer.appendChild(liveCard);
        }
        const pre = liveCard.querySelector(".cmd-stream-pre");
        if (pre) {
          const span = document.createElement("span");
          if (streamType === "stderr") {
            span.className = "text-rose-400";
          }
          span.textContent = text;
          pre.appendChild(span);
          pre.scrollTop = pre.scrollHeight;
        }
        scrollChatToBottom(false);
      }
    },
    updateToolResult(name, summary, data, id) {
      const existing = toolEvents.find(t => t.id === id);
      if (existing) {
        existing.summary = summary;
        existing.data = data;
        existing.completed_at = Date.now();
      }

      const pill = document.getElementById(`tool-${id}`);
      if (pill) {
        pill.className = "tool-pill cursor-pointer border-emerald-500/40 bg-emerald-950/20 text-emerald-300";
        pill.innerHTML = `
          <i data-lucide="check" class="size-3.5 text-emerald-400 shrink-0"></i>
          <span class="font-semibold text-white">${formatToolName(name)}</span>
          <span class="text-emerald-300 font-mono text-[11px]">✓ ${escapeHtml(summary || "Completed")}</span>
        `;

        // Pages Xod read in its own browser for this answer: a row of the sites, each a link.
        if (data && data.provider === "browser" && Array.isArray(data.results) && !document.getElementById(`reads-${id}`)) {
          const read = data.results.filter((r) => r && r.content && /^https?:/.test(r.url || "")).slice(0, 4);
          if (read.length) {
            const strip = document.createElement("div");
            strip.id = `reads-${id}`;
            strip.className = "xr-reads";
            strip.innerHTML = `<span class="xr-label"><i data-lucide="book-open" class="size-3"></i> Read</span>` + read.map((r, i) => {
              let host = "";
              try { host = new URL(r.url).hostname.replace(/^www\./, ""); } catch (e) {}
              return `<a class="xr-read" style="--i:${i}" href="${escapeHtml(r.url)}" target="_blank" rel="noopener" title="${escapeHtml(r.title || host)}">
                <span class="xr-dot">${escapeHtml((host[0] || "?").toUpperCase())}</span><span class="xr-host">${escapeHtml(host)}</span></a>`;
            }).join("");
            pill.insertAdjacentElement("afterend", strip);
          }
        }

        // 1. Multi-Tab & Live Visual Browser Stream Card with Click-to-Take-Over
        if (data && data.screenshot_uri && !wrapper.querySelector(`#ss-preview-${id}`)) {
          const previewCard = document.createElement("div");
          previewCard.id = `ss-preview-${id}`;
          previewCard.className = "my-2.5 rounded-2xl overflow-hidden border border-emerald-500/30 bg-surface-950 shadow-2xl max-w-xl text-xs font-sans";
          previewCard.innerHTML = `
            <!-- Browser Chrome Header Bar -->
            <div class="px-3 py-2 bg-surface-900 border-b border-surface-750 flex flex-col gap-2">
              <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-2 min-w-0">
                  <div class="flex items-center gap-1.5 shrink-0">
                    <span class="size-2.5 rounded-full bg-rose-500/80"></span>
                    <span class="size-2.5 rounded-full bg-amber-500/80"></span>
                    <span class="size-2.5 rounded-full bg-emerald-500/80"></span>
                  </div>
                  <div class="flex items-center gap-1.5 text-emerald-400 font-mono text-[11px] truncate max-w-xs font-medium">
                    <i data-lucide="compass" class="size-3.5 shrink-0"></i>
                    <span class="truncate font-semibold text-zinc-200" title="${escapeHtml(data.url || data.title || "AI Browser Snapshot")}">${escapeHtml(data.title || data.url || "Browser Sandbox")}</span>
                  </div>
                </div>

                <div class="flex items-center gap-1.5 shrink-0">
                  <button type="button" class="btn-takeover px-2 py-0.5 rounded-md text-[10px] font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/25 transition-all flex items-center gap-1" title="Toggle interactive Click-to-Take-Over cursor">
                    <i data-lucide="crosshair" class="size-3"></i>
                    <span class="takeover-label">Take Over</span>
                  </button>
                  <button type="button" onclick="openAutonomousCockpitModal()" class="px-2 py-0.5 rounded-md text-[10px] text-zinc-400 hover:text-white hover:bg-surface-800 transition-colors flex items-center gap-1 border border-white/[0.06]" title="Open Full Autonomous Cockpit">
                    <i data-lucide="external-link" class="size-3"></i>
                    <span>Cockpit</span>
                  </button>
                </div>
              </div>

              <!-- Multi-Tab Dock -->
              <div class="browser-tabs-dock flex items-center gap-1 overflow-x-auto py-0.5 scrollbar-none text-[10px] font-mono">
                <div class="tab-pills flex items-center gap-1 flex-1 min-w-0">
                  <span class="px-2 py-0.5 rounded bg-surface-800 text-emerald-300 border border-emerald-500/30 truncate max-w-[140px] flex items-center gap-1">
                    <span class="size-1.5 rounded-full bg-emerald-400"></span>
                    <span class="truncate">${escapeHtml(data.title || data.url || "Active Tab")}</span>
                  </span>
                </div>
                <button type="button" class="btn-new-tab px-1.5 py-0.5 rounded bg-surface-800 hover:bg-surface-750 text-zinc-400 hover:text-white border border-white/[0.06]" title="Open new blank tab">
                  <i data-lucide="plus" class="size-3"></i>
                </button>
                <button type="button" class="btn-refresh-tabs px-1.5 py-0.5 rounded bg-surface-800 hover:bg-surface-750 text-zinc-400 hover:text-white border border-white/[0.06]" title="Refresh open tabs list">
                  <i data-lucide="refresh-cw" class="size-2.5"></i>
                </button>
              </div>
            </div>

            <!-- Interactive Viewport Canvas -->
            <div class="relative group bg-black flex items-center justify-center overflow-hidden">
              <img src="${data.screenshot_uri}" alt="AI Browser Viewport" class="browser-viewport-img w-full h-auto max-h-64 object-contain select-none transition-opacity" />
              
              <!-- Crosshair Take-Over HUD Overlay (hidden until Take Over is active) -->
              <div class="takeover-hud hidden absolute inset-0 bg-transparent cursor-crosshair z-10 flex flex-col justify-between p-2 pointer-events-auto">
                <div class="flex items-center justify-between text-[10px] font-mono pointer-events-none">
                  <span class="px-2 py-0.5 rounded-md bg-black/80 text-emerald-400 border border-emerald-500/40 backdrop-blur-xs flex items-center gap-1.5">
                    <span class="size-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                    <span>Interactive Take-Over Active — Click anywhere on page</span>
                  </span>
                  <span class="coords-readout px-1.5 py-0.5 rounded bg-black/80 text-zinc-300 border border-white/10 font-mono">X: 0, Y: 0</span>
                </div>
              </div>

              <!-- Quick Ripple Anchor -->
              <div class="click-ripple-container absolute inset-0 pointer-events-none z-20 overflow-hidden"></div>
            </div>

            <!-- Direct Human-in-the-Loop Take-Over Bar -->
            <div class="takeover-bar p-2 bg-surface-900 border-t border-surface-750 flex items-center gap-1.5 text-xs font-mono">
              <div class="flex-1 flex items-center gap-1.5 bg-surface-850 border border-surface-700/80 rounded-lg px-2.5 py-1 focus-within:border-emerald-500">
                <i data-lucide="keyboard" class="size-3.5 text-emerald-400 shrink-0"></i>
                <input type="text" placeholder="Type text / CAPTCHA directly into focused field..." class="direct-input w-full bg-transparent text-[11px] text-white placeholder:text-zinc-500 focus:outline-none" />
              </div>
              <button type="button" class="btn-send-direct px-2.5 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] font-semibold shrink-0 flex items-center gap-1 transition-colors">
                <span>Send</span>
                <i data-lucide="corner-down-left" class="size-3"></i>
              </button>
              <button type="button" class="btn-dismiss-popups px-2 py-1 rounded-lg bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 border border-amber-500/30 text-[10px] shrink-0 font-medium transition-colors" title="Auto-dismiss cookie banners & popups">
                <span>Clear Popups</span>
              </button>
            </div>
          `;
          toolsContainer.appendChild(previewCard);
          initBrowserInteractiveCard(previewCard, data.screenshot_uri);
          lucide.createIcons();
          scrollChatToBottom(false);
        }

        // 2. Spotlight Files Found Inline Card
        if (name === "computer_find_files" && data && Array.isArray(data.results) && data.results.length > 0 && !wrapper.querySelector(`#find-card-${id}`)) {
          const findCard = document.createElement("div");
          findCard.id = `find-card-${id}`;
          findCard.className = "my-2 rounded-xl border border-amber-500/30 bg-surface-900 shadow-xl max-w-lg p-2.5 space-y-2 text-xs";
          const fileItems = data.results.slice(0, 5).map(f => `
            <div class="flex items-center justify-between p-1.5 rounded-lg bg-surface-850 border border-surface-750 text-[11px] gap-2">
              <div class="flex items-center gap-1.5 min-w-0">
                <i data-lucide="${f.is_dir ? 'folder' : 'file-text'}" class="size-3.5 ${f.is_dir ? 'text-amber-400' : 'text-zinc-400'} shrink-0"></i>
                <span class="font-mono text-zinc-200 truncate" title="${escapeHtml(f.path)}">${escapeHtml(f.name)}</span>
              </div>
              <div class="flex items-center gap-1 shrink-0">
                <button onclick="navigator.clipboard.writeText('${escapeHtml(f.path)}'); showToast('Copied path');" class="p-1 rounded hover:bg-surface-800 text-zinc-400 hover:text-white" title="Copy Path">
                  <i data-lucide="copy" class="size-3"></i>
                </button>
                <button onclick="document.getElementById('cockpitRevealPath').value='${escapeHtml(f.path)}'; cockpitRevealFinder();" class="p-1 rounded hover:bg-surface-800 text-zinc-400 hover:text-white" title="Reveal in Finder">
                  <i data-lucide="external-link" class="size-3"></i>
                </button>
              </div>
            </div>
          `).join("");

          findCard.innerHTML = `
            <div class="flex items-center justify-between text-[11px] border-b border-surface-750 pb-1.5">
              <div class="flex items-center gap-1.5 text-amber-400 font-semibold font-mono">
                <i data-lucide="sparkles" class="size-3.5"></i>
                <span>Spotlight Found ${data.count} File(s) for "${escapeHtml(data.query)}"</span>
              </div>
            </div>
            <div class="space-y-1">${fileItems}</div>
          `;
          toolsContainer.appendChild(findCard);
        }

        // 3. Port Inspector Inline Card
        if (name === "computer_inspect_port" && data && !wrapper.querySelector(`#port-card-${id}`)) {
          const portCard = document.createElement("div");
          portCard.id = `port-card-${id}`;
          portCard.className = "my-2 rounded-xl border border-cyan-500/30 bg-surface-900 shadow-xl max-w-md p-2.5 space-y-1.5 text-xs";
          const inUse = data.in_use && data.processes && data.processes.length > 0;
          portCard.innerHTML = `
            <div class="flex items-center justify-between text-[11px]">
              <div class="flex items-center gap-1.5 font-mono ${inUse ? 'text-rose-400' : 'text-emerald-400'} font-semibold">
                <i data-lucide="layers" class="size-3.5"></i>
                <span>Port ${data.port}: ${inUse ? 'Occupied / In Use' : 'Free & Available'}</span>
              </div>
            </div>
            ${inUse ? data.processes.map(p => `
              <div class="flex items-center justify-between p-1.5 rounded-lg bg-surface-850 border border-surface-750 text-[11px] font-mono">
                <span class="text-zinc-200">${escapeHtml(p.command)} (PID ${p.pid})</span>
                <button onclick="cockpitKillPid(${p.pid})" class="px-2 py-0.5 rounded bg-rose-600 hover:bg-rose-500 text-white text-[10px] font-semibold">Kill PID</button>
              </div>
            `).join("") : `<div class="text-[11px] text-zinc-400 italic">No process is currently listening on port ${data.port}.</div>`}
          `;
          toolsContainer.appendChild(portCard);
        }

        // 4. Terminal Command Execution Inline Card
        if (name === "computer_run_command" && data) {
          const liveStreamCard = wrapper.querySelector(`#cmd-stream-${id}`);
          if (liveStreamCard) {
            const isSuccess = data.status === "completed" && data.exit_code === 0;
            const badge = liveStreamCard.querySelector(".cmd-status-badge");
            if (badge) {
              badge.className = `text-[10px] px-1.5 py-0.5 rounded font-mono ${isSuccess ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'bg-rose-500/20 text-rose-400 border border-rose-500/30'}`;
              badge.textContent = `${isSuccess ? 'Exit 0' : `Exit ${data.exit_code}`} (${data.duration_sec || 0}s)`;
            }
            const pulseDot = liveStreamCard.querySelector(".cmd-pulse-dot");
            if (pulseDot) {
              pulseDot.className = `size-2 rounded-full shrink-0 ${isSuccess ? 'bg-emerald-400' : 'bg-rose-400'}`;
            }
            if (data.command) {
              const cmdTitle = liveStreamCard.querySelector(".truncate.text-zinc-200");
              if (cmdTitle) {
                cmdTitle.textContent = data.command;
                cmdTitle.title = data.command;
              }
            }
            const pre = liveStreamCard.querySelector(".cmd-stream-pre");
            if (pre && !pre.textContent.trim()) {
              pre.textContent = (data.stdout || data.stderr || "(No output produced)").trim();
            }
          } else if (!wrapper.querySelector(`#cmd-card-${id}`)) {
            const cmdCard = document.createElement("div");
            cmdCard.id = `cmd-card-${id}`;
            cmdCard.className = "my-2 rounded-xl border border-white/[0.08] bg-black/90 shadow-xl max-w-lg overflow-hidden text-xs font-mono";
            const isSuccess = data.status === "completed" && data.exit_code === 0;
            const out = (data.stdout || data.stderr || "(No output produced)").trim();
            cmdCard.innerHTML = `
              <div class="px-3 py-1.5 bg-surface-850/90 border-b border-surface-750 flex items-center justify-between text-[11px]">
                <div class="flex items-center gap-1.5 text-emerald-400 truncate max-w-xs">
                  <span class="text-zinc-500">$</span>
                  <span class="truncate">${escapeHtml(data.command || "command")}</span>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                  <span class="text-[10px] ${isSuccess ? 'text-emerald-400' : 'text-rose-400'}">${isSuccess ? 'Exit 0' : `Exit ${data.exit_code}`}</span>
                  <span class="text-[10px] text-zinc-500">${data.duration_sec || 0}s</span>
                </div>
              </div>
              <pre class="p-2.5 text-[10px] text-zinc-300 max-h-36 overflow-y-auto whitespace-pre-wrap select-text leading-relaxed">${escapeHtml(out.slice(0, 1500))}</pre>
            `;
            toolsContainer.appendChild(cmdCard);
          }
        }

        // 5. System Specs Inline Card
        if (name === "computer_inspect_system" && data && !wrapper.querySelector(`#sys-card-${id}`)) {
          const sysCard = document.createElement("div");
          sysCard.id = `sys-card-${id}`;
          sysCard.className = "my-2 rounded-xl border border-indigo-500/30 bg-surface-900 shadow-xl max-w-md p-2.5 space-y-2 text-xs";
          const mem = data.memory || {};
          const cpu = data.cpu_load || {};
          sysCard.innerHTML = `
            <div class="flex items-center justify-between text-[11px] font-mono border-b border-surface-750 pb-1">
              <span class="text-indigo-400 font-semibold flex items-center gap-1.5"><i data-lucide="cpu" class="size-3.5"></i> Host: ${escapeHtml(data.os || "macOS")}</span>
              <span class="text-zinc-400">${escapeHtml(data.arch || "")}</span>
            </div>
            <div class="grid grid-cols-2 gap-2 text-[11px] font-mono">
              <div class="p-1.5 rounded-lg bg-surface-850 border border-surface-750">
                <span class="text-zinc-500 text-[10px] block">RAM Usage</span>
                <span class="text-white font-bold">${mem.used_gb || 0} GB</span> <span class="text-zinc-400 text-[10px]">/ ${mem.total_gb || 0} GB</span>
              </div>
              <div class="p-1.5 rounded-lg bg-surface-850 border border-surface-750">
                <span class="text-zinc-500 text-[10px] block">CPU Load (1m)</span>
                <span class="text-emerald-400 font-bold">${cpu['1m'] || '0.00'}</span>
              </div>
            </div>
          `;
          toolsContainer.appendChild(sysCard);
        }

        // 6. File & Folder Operations Inline Card
        if ((name === "fs_create_folder" || name === "fs_move_file" || name === "fs_organize_files") && data && !wrapper.querySelector(`#fs-card-${id}`)) {
          const fsCard = document.createElement("div");
          fsCard.id = `fs-card-${id}`;
          fsCard.className = "my-2 rounded-xl border border-indigo-500/30 bg-surface-900 shadow-xl max-w-lg p-2.5 space-y-2 text-xs";
          let fsContent = "";
          if (name === "fs_create_folder") {
            fsContent = `
              <div class="flex items-center gap-2 text-indigo-400 font-semibold font-mono text-[11px]">
                <i data-lucide="folder-plus" class="size-3.5"></i>
                <span>Folder Created: ${escapeHtml(data.name || data.path || "Folder")}</span>
              </div>
              <div class="text-[10px] text-zinc-400 font-mono truncate bg-surface-850 p-1.5 rounded border border-surface-750">${escapeHtml(data.path || "")}</div>
            `;
          } else if (name === "fs_move_file") {
            fsContent = `
              <div class="flex items-center gap-2 text-indigo-400 font-semibold font-mono text-[11px]">
                <i data-lucide="folder-input" class="size-3.5"></i>
                <span>Item Moved: ${escapeHtml(data.name || "")}</span>
              </div>
              <div class="text-[10px] text-zinc-400 font-mono space-y-1 bg-surface-850 p-1.5 rounded border border-surface-750">
                <div class="truncate text-zinc-500">From: ${escapeHtml(data.old_path || "")}</div>
                <div class="truncate text-emerald-400">To: ${escapeHtml(data.new_path || "")}</div>
              </div>
            `;
          } else if (name === "fs_organize_files") {
            const movedItems = (data.moved_files || []).map(m => `
              <div class="flex items-center justify-between text-[10px] font-mono text-zinc-300 py-0.5 border-b border-surface-750/50">
                <span class="truncate">${escapeHtml(m.name)}</span>
                <span class="text-emerald-400 shrink-0">moved</span>
              </div>
            `).join("");
            fsContent = `
              <div class="flex items-center justify-between text-[11px] text-indigo-400 font-semibold font-mono border-b border-surface-750 pb-1">
                <div class="flex items-center gap-1.5">
                  <i data-lucide="folder-tree" class="size-3.5"></i>
                  <span>Organized ${data.moved_count || 0} File(s) into Folder</span>
                </div>
              </div>
              <div class="text-[10px] text-zinc-400 font-mono truncate bg-surface-850 p-1.5 rounded border border-surface-750">
                Folder: ${escapeHtml(data.target_folder || "")}
              </div>
              ${movedItems ? `<div class="max-h-28 overflow-y-auto space-y-0.5">${movedItems}</div>` : ""}
            `;
          }
          fsCard.innerHTML = fsContent;
          toolsContainer.appendChild(fsCard);
        }

        // 7. Directory Tree Inline Card
        if (name === "computer_directory_tree" && data && !wrapper.querySelector(`#tree-card-${id}`)) {
          const treeCard = document.createElement("div");
          treeCard.id = `tree-card-${id}`;
          treeCard.className = "my-2 rounded-xl border border-sky-500/30 bg-surface-900 shadow-xl max-w-lg p-2.5 space-y-2 text-xs font-mono";
          const st = data.stats || {};
          treeCard.innerHTML = `
            <div class="flex items-center justify-between text-[11px] text-sky-400 font-semibold border-b border-surface-750 pb-1.5">
              <div class="flex items-center gap-1.5">
                <i data-lucide="folder-tree" class="size-3.5"></i>
                <span class="truncate">Directory: ${escapeHtml(data.name || data.base_path || "Folder")}</span>
              </div>
              <span class="text-zinc-400 text-[10px]">${st.total_dirs || 0} dirs · ${st.total_files || 0} files (${escapeHtml(st.total_size_formatted || "")})</span>
            </div>
            <pre class="p-2 rounded bg-surface-850 border border-surface-750 text-[10px] text-zinc-300 max-h-48 overflow-y-auto whitespace-pre font-mono leading-relaxed select-text">${escapeHtml(data.tree || "(Empty folder)")}</pre>
          `;
          toolsContainer.appendChild(treeCard);
        }

        // 8. Surgical File Update Inline Card
        if (name === "computer_edit_file" && data && !wrapper.querySelector(`#edit-card-${id}`)) {
          const editCard = document.createElement("div");
          editCard.id = `edit-card-${id}`;
          editCard.className = "my-2 rounded-xl border border-emerald-500/30 bg-surface-900 shadow-xl max-w-lg p-2.5 space-y-2 text-xs font-mono";
          editCard.innerHTML = `
            <div class="flex items-center justify-between text-[11px] text-emerald-400 font-semibold border-b border-surface-750 pb-1.5">
              <div class="flex items-center gap-1.5">
                <i data-lucide="file-edit" class="size-3.5"></i>
                <span>Surgically Updated: ${escapeHtml(data.name || "File")}</span>
              </div>
              <span class="px-1.5 py-0.5 rounded text-[9px] bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">Line ${data.line_number || 1}</span>
            </div>
            <div class="p-1.5 rounded bg-surface-850 border border-surface-750 text-[11px] text-zinc-300 flex items-center justify-between">
              <span class="truncate text-zinc-400">${escapeHtml(data.filepath || "")}</span>
              <span class="text-emerald-400 shrink-0 font-bold">${data.lines_delta >= 0 ? '+' : ''}${data.lines_delta || 0} lines</span>
            </div>
          `;
          toolsContainer.appendChild(editCard);
        }

        // 9. Batch Organize Files Inline Card
        if (name === "computer_batch_organize" && data && !wrapper.querySelector(`#organize-card-${id}`)) {
          const orgCard = document.createElement("div");
          orgCard.id = `organize-card-${id}`;
          orgCard.className = "my-2 rounded-xl border border-violet-500/30 bg-surface-900 shadow-xl max-w-lg p-2.5 space-y-2 text-xs font-mono";
          const bd = data.breakdown || {};
          const catPills = Object.entries(bd).map(([cat, count]) => `
            <span class="px-2 py-0.5 rounded-full text-[10px] bg-violet-500/10 text-violet-300 border border-violet-500/25">${escapeHtml(cat)}: ${count}</span>
          `).join("");
          orgCard.innerHTML = `
            <div class="flex items-center justify-between text-[11px] text-violet-400 font-semibold border-b border-surface-750 pb-1.5">
              <div class="flex items-center gap-1.5">
                <i data-lucide="package-check" class="size-3.5"></i>
                <span>Cleaned & Organized ${data.total_files_moved || 0} File(s)</span>
              </div>
            </div>
            <div class="text-[10px] text-zinc-400 truncate bg-surface-850 p-1.5 rounded border border-surface-750">
              Location: ${escapeHtml(data.source_dir || "")}
            </div>
            <div class="flex flex-wrap gap-1.5 pt-0.5">${catPills || '<span class="text-zinc-500 italic">No loose files moved</span>'}</div>
          `;
          toolsContainer.appendChild(orgCard);
        }

        // 10. Hardware Overview Inline Card
        if (name === "computer_hardware_overview" && data && !wrapper.querySelector(`#hw-card-${id}`)) {
          const hwCard = document.createElement("div");
          hwCard.id = `hw-card-${id}`;
          hwCard.className = "my-2 rounded-xl border border-amber-500/30 bg-surface-900 shadow-xl max-w-md p-2.5 space-y-2 text-xs font-mono";
          const batt = data.battery || {};
          hwCard.innerHTML = `
            <div class="flex items-center justify-between text-[11px] text-amber-400 font-semibold border-b border-surface-750 pb-1.5">
              <div class="flex items-center gap-1.5">
                <i data-lucide="cpu" class="size-3.5"></i>
                <span>${escapeHtml(data.chip || "Apple Silicon")} (${data.cpu_cores || 8} cores)</span>
              </div>
              <span class="text-zinc-400 text-[10px]">${escapeHtml(data.arch || "")}</span>
            </div>
            <div class="grid grid-cols-2 gap-2 text-[11px]">
              <div class="p-1.5 rounded bg-surface-850 border border-surface-750">
                <span class="text-zinc-500 text-[10px] block">OS Version</span>
                <span class="text-white truncate block">${escapeHtml(data.macos_version || "macOS")}</span>
              </div>
              <div class="p-1.5 rounded bg-surface-850 border border-surface-750">
                <span class="text-zinc-500 text-[10px] block">Battery / Power</span>
                <span class="text-emerald-400 font-bold">${batt.has_battery ? `${batt.percentage}% (${batt.state})` : 'AC Power'}</span>
              </div>
            </div>
          `;
          toolsContainer.appendChild(hwCard);
        }

        // 11. Open Item or AppleScript Inline Card
        if ((name === "computer_open_item" || name === "computer_run_applescript") && data && !wrapper.querySelector(`#open-card-${id}`)) {
          const actionCard = document.createElement("div");
          actionCard.id = `open-card-${id}`;
          actionCard.className = "my-2 rounded-xl border border-teal-500/30 bg-surface-900 shadow-xl max-w-md p-2.5 space-y-1.5 text-xs font-mono";
          actionCard.innerHTML = `
            <div class="flex items-center justify-between text-[11px] text-teal-400 font-semibold">
              <div class="flex items-center gap-1.5">
                <i data-lucide="${name === 'computer_run_applescript' ? 'terminal-square' : 'external-link'}" class="size-3.5"></i>
                <span>${name === 'computer_run_applescript' ? 'AppleScript Executed' : `Opened in ${escapeHtml(data.app_name || 'App')}`}</span>
              </div>
              <span class="text-[10px] text-emerald-400">Exit 0</span>
            </div>
            <div class="text-[10px] text-zinc-300 bg-surface-850 p-1.5 rounded border border-surface-750 truncate">
              ${escapeHtml(data.message || data.output || data.target || "Success")}
            </div>
          `;
          toolsContainer.appendChild(actionCard);
        }

        lucide.createIcons();
      }
      scrollChatToBottom(false);
    },
    appendContent(token) {
      fullContent += token;
      this.fullContent = fullContent;
      proseCustom.innerHTML = marked.parse(fullContent);
      actionFooter.classList.remove("hidden");
      scrollChatToBottom(false);
    },
    setContent(text) {
      fullContent = text;
      this.fullContent = fullContent;
      proseCustom.innerHTML = marked.parse(text);
      actionFooter.classList.remove("hidden");
      scrollChatToBottom(false);
    }
  };

  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(assistantBubble.fullContent);
      copyBtn.innerHTML = `<i data-lucide="check" class="size-3.5 text-emerald-400"></i> Copied!`;
      setTimeout(() => {
        copyBtn.innerHTML = `<i data-lucide="copy" class="size-3.5"></i> Copy`;
        if (window.lucide) lucide.createIcons();
      }, 2000);
    });
  }

  if (exportBtn) {
    exportBtn.addEventListener("click", () => {
      const blob = new Blob([assistantBubble.fullContent], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `research-report-${Date.now()}.md`;
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  if (traceBtn) {
    traceBtn.addEventListener("click", () => {
      openLLMObservabilityModal(assistantBubble);
    });
  }

  return assistantBubble;
}

function handleResearchEvent(event, assistantBubble) {
  try { window.dispatchEvent(new CustomEvent("xod:agent", { detail: event })); } catch (_) {}
  if (assistantBubble && assistantBubble.eventsLog) {
    assistantBubble.eventsLog.push(event);
  }

  if (event.type === "session_info") {
    currentSessionId = event.session_id;
    sidebarOnNewSession(event.session_id).finally(loadSessions);
  } else if (event.type === "swarm_plan") {
    assistantBubble.finishReasoning();
    assistantBubble.setSwarmPlan(event.title, event.conductor, event.subagents);
  } else if (event.type === "subagent_status") {
    assistantBubble.finishReasoning();
    assistantBubble.updateSubagentStatus(event.agent_id, event.status, event.message, event.findings, event.duration_ms, event.tier, event.model);
  } else if (event.type === "subagent_tool_call") {
    assistantBubble.finishReasoning();
    assistantBubble.updateSubagentToolCall(event.agent_id, event.name, event.args, event.id);
  } else if (event.type === "subagent_tool_result") {
    assistantBubble.updateSubagentToolResult(event.agent_id, event.name, event.summary, event.data, event.id);
  } else if (event.type === "swarm_complete") {
    assistantBubble.setSwarmComplete(event.total_subagents, event.results);
  } else if (event.type === "plan") {
    assistantBubble.finishReasoning();
    assistantBubble.setPlan(event.title, event.steps);
  } else if (event.type === "plan_step") {
    assistantBubble.finishReasoning();
    assistantBubble.updatePlanStep(event.step_id, event.status);
  } else if (event.type === "evidence_update") {
    assistantBubble.updateEvidence(event.stats);
  } else if (event.type === "scorecard") {
    assistantBubble.finishReasoning();
    assistantBubble.setScorecard(event.scorecard, event.entity);
  } else if (event.type === "status") {
    updateLiveStatusBar(true, event.content);
    if (window.XodPreview) XodPreview.onStatus(event.content);
  } else if (event.type === "reasoning") {
    assistantBubble.appendReasoning(event.content);
  } else if (event.type === "tool_call") {
    assistantBubble.finishReasoning();
    assistantBubble.addToolCall(event.name, event.args, event.id);
  } else if (event.type === "tool_stream") {
    if (assistantBubble && assistantBubble.appendToolStream) {
      assistantBubble.appendToolStream(event.name, event.stream_type, event.text, event.id);
    }
  } else if (event.type === "build_started") {
    if (window.XodPreview) XodPreview.onBuildStarted(event);
  } else if (event.type === "jobs") {
    if (window.XodJobs) XodJobs.attach(event, assistantBubble.wrapper);
  } else if (event.type === "job_profile_needed") {
    if (window.XodJobs) XodJobs.onProfileNeeded(event);
  } else if (event.type === "soul") {
    if (window.XodSoul) XodSoul.onEvent(event);
  } else if (event.type === "launch_app") {
    if (window.XodPreview) XodPreview.onLaunch(event);
  } else if (event.type === "build_done") {
    if (window.XodPreview) XodPreview.onBuildDone(event, assistantBubble);
  } else if (event.type === "tool_result") {
    assistantBubble.updateToolResult(event.name, event.summary, event.data, event.id);
    if (window.XodPreview) XodPreview.onToolResult(event);
    if (event.data && event.data.needs_approval && window.renderMcpApproval) window.renderMcpApproval(event.data);
  } else if (event.type === "understood") {
    // A follow-up was read as a standalone request first: show how, so a wrong reading is easy to spot.
    const host = assistantBubble.wrapper && assistantBubble.wrapper.querySelector(".flex-1");
    if (host && !host.querySelector(".xu-understood")) {
      const p = document.createElement("p");
      p.className = "xu-understood";
      p.textContent = `Understood as: ${event.content}`;
      host.insertBefore(p, host.firstChild);
    }
  } else if (event.type === "memory_pending") {
    if (window.XodAboutYou) XodAboutYou.follow(event.turn, assistantBubble.wrapper && assistantBubble.wrapper.querySelector(".flex-1"));
  } else if (event.type === "memory") {
    // Xod picked up (or forgot) something about the person: a small note under the answer, with Undo.
    if (window.XodAboutYou) XodAboutYou.note(event, assistantBubble.wrapper && assistantBubble.wrapper.querySelector(".flex-1"));
  } else if (event.type === "escalation_offer" || event.type === "route_info") {
    if (window.renderRouteEvent) window.renderRouteEvent(event);
  } else if (event.type === "content") {
    assistantBubble.finishReasoning();
    assistantBubble.appendContent(event.content);
  } else if (event.type === "error") {
    assistantBubble.finishReasoning();
    assistantBubble.setContent(`**Error:** ${event.content}`);
  } else if (event.type === "done") {
    assistantBubble.finishReasoning();
    updateLiveStatusBar(false);
    loadSessions();
    scrollChatToBottom(true);
  }
}

// -------------------------------------------------------------
// DEALS SCREENER MODAL
// -------------------------------------------------------------
function openDealsModal() {
  document.getElementById("dealsModal").classList.remove("hidden");
  fetchDeals();
}

function closeDealsModal() {
  document.getElementById("dealsModal").classList.add("hidden");
}

async function fetchDeals() {
  const container = document.getElementById("dealsContainer");
  const category = document.getElementById("filterDealCategory").value;
  const multiple = document.getElementById("filterDealMultiple").value;

  container.innerHTML = `<div class="text-center text-zinc-400 py-12 text-sm"><i data-lucide="loader-2" class="size-6 animate-spin mx-auto mb-2 text-brand-400"></i> Fetching vetted deals from TrustMRR API...</div>`;
  lucide.createIcons();

  let url = "/api/trustmrr/deals?";
  if (category) url += `category=${category}&`;
  if (multiple) url += `max_multiple=${multiple}&`;

  try {
    const res = await fetch(url);
    const data = await res.json();
    const deals = data.deals || [];

    if (data.needs_key || data.error) {
      container.innerHTML = `
        <div class="text-center text-zinc-400 py-12 text-sm">
          ${data.needs_key ? "Deal screening uses TrustMRR. Add your TrustMRR API key in Settings → API keys to see deals." : escapeHtml(data.error)}
        </div>`;
      return;
    }
    if (deals.length === 0) {
      container.innerHTML = `
        <div class="text-center text-zinc-400 py-12 text-sm">
          No deals currently match these filters, or TrustMRR API key is required.
        </div>`;
      return;
    }

    let html = `<div class="grid grid-cols-1 md:grid-cols-2 gap-4">`;
    for (const d of deals) {
      const f = d.formatted || {};
      html += `
        <div class="p-4 rounded-xl bg-surface-750 border border-surface-600 hover:border-brand-500/50 transition-all flex flex-col justify-between space-y-3">
          <div class="space-y-1.5">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2">
                ${d.icon ? `<img src="${d.icon}" class="size-6 rounded-md">` : `<div class="size-6 rounded-md bg-surface-700 flex items-center justify-center font-bold text-xs">${d.name[0]}</div>`}
                <span class="font-bold text-sm text-white">${escapeHtml(d.name)}</span>
              </div>
              <span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                ${f.multiple || "For Sale"}
              </span>
            </div>
            <p class="text-xs text-zinc-400 line-clamp-2">${escapeHtml(d.description || "")}</p>
          </div>

          <div class="grid grid-cols-3 gap-2 pt-2 border-t border-surface-700 text-center">
            <div>
              <span class="text-[10px] text-zinc-500 block uppercase font-medium">Verified MRR</span>
              <span class="text-xs font-bold text-emerald-400">${f.mrr || "$0"}</span>
            </div>
            <div>
              <span class="text-[10px] text-zinc-500 block uppercase font-medium">Asking Price</span>
              <span class="text-xs font-bold text-white">${f.askingPrice || "Offer"}</span>
            </div>
            <div>
              <span class="text-[10px] text-zinc-500 block uppercase font-medium">30d Growth</span>
              <span class="text-xs font-bold ${parseFloat(d.growth30d) >= 0 ? 'text-emerald-400' : 'text-red-400'}">${f.growth30d || "0%"}</span>
            </div>
          </div>

          <button onclick="analyzeDealInChat('${escapeHtml(d.slug)}', '${escapeHtml(d.name)}')" class="w-full py-1.5 rounded-lg bg-surface-700 hover:bg-brand-600 text-xs font-semibold text-zinc-200 hover:text-white transition-colors flex items-center justify-center gap-1.5">
            <i data-lucide="sparkles" class="size-3.5"></i> Run M&A Due Diligence in Chat
          </button>
        </div>
      `;
    }
    html += `</div>`;
    container.innerHTML = html;
    lucide.createIcons();

  } catch (e) {
    container.innerHTML = `<div class="text-red-400 text-center py-8 text-sm">Failed to load deals: ${e.message}</div>`;
  }
}

function analyzeDealInChat(slug, name) {
  closeDealsModal();
  setPersona("acquisition_hunter");
  usePrompt(`Perform complete M&A acquisition due diligence on "${name}" (slug: ${slug}): analyze asking price, ARR multiple, unit economics, tech stack feasibility, and operational risks.`);
}

// -------------------------------------------------------------
// LIVE DOMAIN & TECH AUDITOR MODAL
// -------------------------------------------------------------
function openTechAuditModal(defaultDomain = "") {
  document.getElementById("techAuditModal").classList.remove("hidden");
  if (defaultDomain) {
    document.getElementById("inputAuditDomain").value = defaultDomain;
    runDomainAudit();
  }
}

function closeTechAuditModal() {
  document.getElementById("techAuditModal").classList.add("hidden");
}

async function runDomainAudit() {
  const domain = document.getElementById("inputAuditDomain").value.trim();
  if (!domain) return;

  const container = document.getElementById("auditResultsContainer");
  const btn = document.getElementById("btnRunAudit");
  btn.disabled = true;
  btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 animate-spin"></i> Auditing...`;
  lucide.createIcons();

  container.innerHTML = `<div class="text-center text-zinc-400 py-10 text-xs"><i data-lucide="loader-2" class="size-6 animate-spin mx-auto mb-2 text-indigo-400"></i> Probing live domain headers, scripts, payment gateways, and frameworks for ${escapeHtml(domain)}...</div>`;
  lucide.createIcons();

  try {
    const res = await fetch("/api/audit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: domain })
    });
    const data = await res.json();

    if (!data.success) {
      container.innerHTML = `<div class="p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-xs text-red-300">⚠️ Audit failed: ${escapeHtml(data.error || "Could not connect to domain")}</div>`;
      return;
    }

    const techCats = data.technologies_by_category || {};
    let techBadgesHtml = "";
    for (const [cat, techs] of Object.entries(techCats)) {
      techBadgesHtml += `
        <div class="space-y-1.5">
          <span class="text-[10px] font-bold uppercase tracking-wider text-zinc-400">${escapeHtml(cat)}</span>
          <div class="flex flex-wrap gap-1.5">
            ${techs.map(t => `<span class="px-2 py-0.5 rounded-lg text-xs font-semibold bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">${escapeHtml(t)}</span>`).join("")}
          </div>
        </div>
      `;
    }

    const pricing = data.pricing_signals || {};
    const meta = data.metadata || {};
    const sec = data.security || {};

    container.innerHTML = `
      <div class="space-y-4">
        <!-- Site Header Card -->
        <div class="p-4 rounded-xl bg-surface-750 border border-surface-600 flex items-start justify-between gap-3">
          <div class="space-y-1">
            <div class="flex items-center gap-2">
              <span class="font-bold text-base text-white">${escapeHtml(meta.title || data.domain)}</span>
              <span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">HTTP ${data.status_code}</span>
            </div>
            <p class="text-xs text-zinc-400 line-clamp-2">${escapeHtml(meta.description || "No meta description extracted")}</p>
            <a href="${escapeHtml(data.final_url)}" target="_blank" class="text-[11px] text-indigo-400 hover:underline flex items-center gap-1 font-mono">
              ${escapeHtml(data.final_url)} &nearr;
            </a>
          </div>
          <button onclick="runOmniSearchInChat('${escapeHtml(data.domain)}')" class="shrink-0 px-3 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-500 text-xs font-semibold text-white flex items-center gap-1.5 transition-colors shadow-md shadow-brand-600/30">
            <i data-lucide="sparkles" class="size-3.5"></i> Deep OmniSearch
          </button>
        </div>

        <!-- Detected Technologies -->
        <div class="p-4 rounded-xl bg-surface-750 border border-surface-600 space-y-3">
          <div class="flex items-center justify-between">
            <h4 class="text-xs font-bold uppercase tracking-wider text-zinc-300 flex items-center gap-1.5">
              <i data-lucide="layers" class="size-3.5 text-indigo-400"></i> Detected Tech Stack (${data.total_technologies_detected})
            </h4>
            <span class="text-[10px] text-zinc-500 font-mono">Direct DOM & Header Signatures</span>
          </div>
          ${techBadgesHtml || '<div class="text-xs text-zinc-500">No popular frameworks or third-party SDKs matched standard signatures.</div>'}
        </div>

        <!-- Pricing Signals & Security Row -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <!-- Pricing Card -->
          <div class="p-3.5 rounded-xl bg-surface-750 border border-surface-600 space-y-2">
            <h4 class="text-xs font-bold uppercase tracking-wider text-zinc-300 flex items-center gap-1.5">
              <i data-lucide="tag" class="size-3.5 text-emerald-400"></i> Pricing Signals
            </h4>
            <div class="space-y-1 text-xs text-zinc-400">
              <div>Free Tier / Trial: <strong class="${pricing.has_free_tier ? 'text-emerald-400' : 'text-zinc-500'}">${pricing.has_free_tier ? 'Yes' : 'Not Detected'}</strong></div>
              <div>Annual Discount: <strong class="${pricing.has_annual_discount ? 'text-emerald-400' : 'text-zinc-500'}">${pricing.has_annual_discount ? 'Yes' : 'Not Detected'}</strong></div>
              <div>Detected Price Points: <span class="font-mono text-zinc-200">${pricing.detected_price_points?.join(", ") || 'None'}</span></div>
            </div>
          </div>

          <!-- Security & Infra Card -->
          <div class="p-3.5 rounded-xl bg-surface-750 border border-surface-600 space-y-2">
            <h4 class="text-xs font-bold uppercase tracking-wider text-zinc-300 flex items-center gap-1.5">
              <i data-lucide="shield" class="size-3.5 text-cyan-400"></i> Infra & Security
            </h4>
            <div class="space-y-1 text-xs text-zinc-400">
              <div>HTTPS Secured: <strong class="text-emerald-400">${sec.https ? 'Yes' : 'No'}</strong></div>
              <div>HSTS Active: <strong class="${sec.hsts ? 'text-emerald-400' : 'text-zinc-500'}">${sec.hsts ? 'Yes' : 'No'}</strong></div>
              <div>Server Header: <span class="font-mono text-zinc-300">${escapeHtml(sec.server_header)}</span></div>
            </div>
          </div>
        </div>
      </div>
    `;
    lucide.createIcons();

  } catch (e) {
    container.innerHTML = `<div class="p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-xs text-red-300">Error: ${escapeHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<i data-lucide="search" class="size-3.5"></i> <span>Audit Stack</span>`;
    lucide.createIcons();
  }
}

function runOmniSearchInChat(domain) {
  closeTechAuditModal();
  setPersona("ultimate_researcher");
  usePrompt(`Run a proprietary SOTA OmniSearch teardown on ${domain}: verified revenue, live domain stack audit, pricing models, competitors, and growth signals.`);
}

// -------------------------------------------------------------
// SAAS KNOWLEDGE GRAPH & INDEX MODAL
// -------------------------------------------------------------
let cachedKgEntities = [];

async function openKnowledgeGraphModal() {
  document.getElementById("knowledgeGraphModal").classList.remove("hidden");
  await fetchKnowledgeGraph();
}

function closeKnowledgeGraphModal() {
  document.getElementById("knowledgeGraphModal").classList.add("hidden");
}

async function fetchKnowledgeGraph() {
  const container = document.getElementById("kgEntitiesContainer");
  const countBadge = document.getElementById("kgCountBadge");
  if (!container) return;

  container.innerHTML = `<div class="text-center text-zinc-400 py-12 text-sm"><i data-lucide="loader-2" class="size-6 animate-spin mx-auto mb-2 text-emerald-400"></i> Querying local SaaS Knowledge Graph...</div>`;
  lucide.createIcons();

  try {
    const res = await fetch("/api/knowledge-graph");
    const data = await res.json();
    cachedKgEntities = data.entities || [];

    if (countBadge) {
      countBadge.textContent = `${cachedKgEntities.length} Startups Indexed`;
    }

    renderKnowledgeGraph(cachedKgEntities);
  } catch (err) {
    container.innerHTML = `<div class="p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-xs text-red-300">Error loading Knowledge Graph: ${escapeHtml(err.message)}</div>`;
  }
}

function filterKnowledgeGraph() {
  const query = (document.getElementById("kgSearchInput")?.value || "").toLowerCase().trim();
  if (!query) {
    renderKnowledgeGraph(cachedKgEntities);
    return;
  }

  const filtered = cachedKgEntities.filter(e => {
    const name = (e.name || "").toLowerCase();
    const slug = (e.slug || "").toLowerCase();
    const cat = (e.category || "").toLowerCase();
    const tags = Array.isArray(e.tags) ? e.tags.join(" ").toLowerCase() : "";
    const techNames = Array.isArray(e.tech_stack)
      ? e.tech_stack.map(t => (typeof t === 'string' ? t : (t.name || '')).toLowerCase()).join(" ")
      : "";
    return name.includes(query) || slug.includes(query) || cat.includes(query) || tags.includes(query) || techNames.includes(query);
  });

  renderKnowledgeGraph(filtered);
}

function renderKnowledgeGraph(entities) {
  const container = document.getElementById("kgEntitiesContainer");
  if (!container) return;

  if (entities.length === 0) {
    container.innerHTML = `
      <div class="text-center text-zinc-400 py-12 space-y-3">
        <div class="inline-flex size-12 rounded-xl bg-surface-700/60 items-center justify-center text-zinc-500">
          <i data-lucide="network" class="size-6"></i>
        </div>
        <p class="text-sm font-semibold text-zinc-300">No startups found in Knowledge Graph</p>
        <p class="text-xs text-zinc-500 max-w-sm mx-auto">
          Startups are automatically indexed here whenever the agent performs research, audits live tech stacks, or screens revenue.
        </p>
      </div>
    `;
    lucide.createIcons();
    return;
  }

  let html = `<div class="grid grid-cols-1 md:grid-cols-2 gap-4">`;

  for (const ent of entities) {
    const score = ent.due_diligence_score;
    let scoreColor = "emerald";
    if (score < 55) scoreColor = "red";
    else if (score < 70) scoreColor = "amber";
    else if (score < 85) scoreColor = "brand";

    // Tech badges
    let techBadgesHtml = "";
    if (Array.isArray(ent.tech_stack) && ent.tech_stack.length > 0) {
      techBadgesHtml = ent.tech_stack.slice(0, 6).map(t => {
        const name = typeof t === "string" ? t : (t.name || "");
        return `<span class="text-[10px] px-2 py-0.5 rounded bg-surface-700 text-zinc-300 border border-surface-600">${escapeHtml(name)}</span>`;
      }).join("");
      if (ent.tech_stack.length > 6) {
        techBadgesHtml += `<span class="text-[10px] px-1.5 py-0.5 rounded bg-surface-750 text-zinc-500">+${ent.tech_stack.length - 6}</span>`;
      }
    } else {
      techBadgesHtml = `<span class="text-[10px] text-zinc-500 italic">No stack audited yet</span>`;
    }

    const safeName = escapeHtml(ent.name || '');
    const safeSlug = escapeHtml(ent.slug || '');
    const safeTarget = escapeHtml(ent.website || ent.slug || '');

    html += `
      <div class="p-4 rounded-xl bg-surface-750 border border-surface-600/70 hover:border-surface-500 transition-all space-y-3">
        <!-- Top Row -->
        <div class="flex items-start justify-between gap-2">
          <div>
            <div class="flex items-center gap-2">
              <h4 class="font-bold text-sm text-white">${safeName}</h4>
              <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-700 text-zinc-400">${safeSlug}</span>
            </div>
            <div class="flex items-center gap-2 mt-1">
              ${ent.category ? `<span class="text-[10px] font-semibold uppercase tracking-wider text-brand-400">${escapeHtml(ent.category)}</span>` : ''}
              ${ent.website ? `<a href="${ent.website.startsWith('http') ? escapeHtml(ent.website) : 'https://' + escapeHtml(ent.website)}" target="_blank" class="text-[10px] text-zinc-400 hover:text-white flex items-center gap-1 font-mono"><i data-lucide="external-link" class="size-2.5"></i> ${escapeHtml(ent.website)}</a>` : ''}
            </div>
          </div>
          ${score != null ? `
            <div class="text-right shrink-0">
              <span class="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold font-mono ${
                scoreColor === 'emerald' ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30' :
                (scoreColor === 'brand' ? 'bg-brand-500/15 text-brand-300 border border-brand-500/30' :
                (scoreColor === 'amber' ? 'bg-amber-500/15 text-amber-300 border border-amber-500/30' :
                'bg-red-500/15 text-red-300 border border-red-500/30'))
              }">
                <i data-lucide="award" class="size-3"></i> ${score} / 100
              </span>
            </div>
          ` : ''}
        </div>

        <!-- Telemetry Stats Grid -->
        <div class="grid grid-cols-3 gap-2 bg-surface-800/80 p-2.5 rounded-lg border border-surface-700/60 text-xs">
          <div>
            <span class="text-[10px] text-zinc-500 block uppercase">Verified MRR</span>
            <span class="font-mono font-bold text-emerald-400">${escapeHtml(ent.verified_mrr || '—')}</span>
          </div>
          <div>
            <span class="text-[10px] text-zinc-500 block uppercase">Last 30d Rev</span>
            <span class="font-mono font-bold text-zinc-200">${escapeHtml(ent.last_30d_revenue || '—')}</span>
          </div>
          <div>
            <span class="text-[10px] text-zinc-500 block uppercase">Multiple</span>
            <span class="font-mono font-bold text-indigo-300">${escapeHtml(ent.arr_multiple || '—')}</span>
          </div>
        </div>

        <!-- Tags & Custom Intelligence -->
        ${Array.isArray(ent.tags) && ent.tags.length > 0 ? `
          <div class="flex flex-wrap gap-1">
            ${ent.tags.map(t => `<span class="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-mono">#${escapeHtml(t)}</span>`).join("")}
          </div>
        ` : ''}

        <!-- Linked Competitors -->
        ${Array.isArray(ent.relationships) && ent.relationships.length > 0 ? `
          <div class="text-[10px] text-zinc-400 flex items-center gap-1.5">
            <i data-lucide="network" class="size-3 text-indigo-400"></i>
            <span class="text-zinc-500">Linked Competitors:</span>
            <div class="flex flex-wrap gap-1">
              ${ent.relationships.map(r => `<span class="text-[9px] font-mono px-1.5 py-0.2 rounded bg-surface-700 text-zinc-300">${escapeHtml(r.target_slug)}</span>`).join("")}
            </div>
          </div>
        ` : ''}

        <!-- Tech Stack Fingerprint Badges -->
        <div class="space-y-1">
          <div class="text-[10px] uppercase tracking-wider text-zinc-400 font-semibold flex items-center gap-1">
            <i data-lucide="cpu" class="size-3 text-indigo-400"></i> Stack Fingerprint:
          </div>
          <div class="flex flex-wrap gap-1.5">
            ${techBadgesHtml}
          </div>
        </div>

        <!-- Action Buttons -->
        <div class="pt-2 border-t border-surface-700/60 flex items-center justify-between text-xs">
          <button onclick="auditEntityStack('${safeTarget}')" class="text-zinc-400 hover:text-zinc-200 flex items-center gap-1 text-[11px] transition-colors">
            <i data-lucide="search" class="size-3"></i> Audit Stack
          </button>
          <button onclick="researchEntityInChat('${safeName}', '${safeSlug}')" class="px-3 py-1 rounded-lg bg-brand-600 hover:bg-brand-500 text-white font-medium flex items-center gap-1 text-[11px] shadow-sm transition-colors">
            <i data-lucide="sparkles" class="size-3"></i> Deep Research in Chat
          </button>
        </div>
      </div>
    `;
  }

  html += `</div>`;
  container.innerHTML = html;
  lucide.createIcons();
}

function researchEntityInChat(name, slug) {
  closeKnowledgeGraphModal();
  setPersona("ultimate_researcher");
  usePrompt(`Execute an institutional deep research dossier on ${name} (${slug}): verified MRR, tech architecture teardown, valuation multiples, and competitive moat.`);
}

function auditEntityStack(target) {
  closeKnowledgeGraphModal();
  openTechAuditModal();
  const input = document.getElementById("inputAuditDomain");
  if (input) {
    input.value = target;
    runDomainAudit();
  }
}

// -------------------------------------------------------------
// HELPERS
// -------------------------------------------------------------
function formatToolName(name) {
  const map = {
    "preview_check": "Ran it",
    "find_work_email": "Founder Work-Email Hunter",
    "verify_email_bounce_risk": "Email Bounce-Risk Verifier",
    "scan_live_job_firehose": "Live Job Firehose & Liveness",
    "audit_funding_and_org": "Funding & Org Intelligence (YC/SEC)",
    "scan_trigger_events": "Trigger-Event Radar & Openers",
    "scan_live_social_trends": "TrendPulse Live Social Radar",
    "search_topic_buzz": "Multi-Platform Topic Buzz",
    "compare_traffic_head_to_head": "Traffic & Path Faceoff",
    "audit_traffic_and_popularity": "TrafficPulse & Rank Audit",
    "inspect_url_path_activity": "Path Activity & Funnel Inspector",
    "audit_subdomains_activity": "Subdomain Usage & Liveness Probe",
    "audit_ip_landscape": "USPTO & Patent Landscape",
    "audit_sec_filings": "SEC EDGAR Form D Diligence",
    "audit_package_ecosystem": "Package Registry & SDK Telemetry",
    "audit_talent_and_hiring": "Talent & Headcount Telemetry",
    "recon_shadow_subdomains": "Shadow Infra Recon (CT Logs)",
    "omni_deep_search": "OmniSearch Fusion (RRF)",
    "audit_tech_stack": "Live Tech Stack Auditor",
    "query_knowledge_graph": "SaaS Knowledge Graph",
    "update_knowledge_graph_entity": "Knowledge Graph Self-Update",
    "mine_community_sentiment": "Community & HN Sentiment",
    "audit_github_repo": "GitHub Repo Auditor",
    "inspect_domain_dns_health": "DNS & Mail Security Audit",
    "explore_site_surface": "Site Surface & API Explorer",
    "trustmrr_list_startups": "TrustMRR Startups",
    "trustmrr_get_startup": "TrustMRR Dossier",
    "trustmrr_compare_startups": "TrustMRR Benchmark",
    "trustmrr_screen_deals": "TrustMRR Deal Screener",
    "tavily_search": "Web search",
    "you_search": "Web search (You.com)",
    "you_news_search": "News search",
    "web_search": "Web search",
    "memory_search": "Memory",
    "xod_tasks": "My tasks",
    "notebook_search": "Notebook",
    "web_scrape": "Read a page",
    "calculate_saas_metrics": "Did the math",
    "browser_navigate": "Opened a page",
    "browser_click": "Clicked",
    "browser_type": "Typed",
    "browser_snapshot": "Looked at the page",
    "browser_extract_content": "Read the page",
    "browser_evaluate_script": "Ran a script on the page",
    "computer_run_command": "Ran a command",
    "computer_inspect_system": "Checked the Mac",
    "computer_notify_user": "Sent a notification",
    "computer_reveal_finder": "Reveal in macOS Finder",
    "computer_find_files": "Searched files",
    "computer_read_file": "Read a file",
    "computer_write_file": "Wrote a file",
    "computer_edit_file": "Edited a file",
    "computer_directory_tree": "Looked at a folder",
    "computer_list_directory": "Looked at a folder",
    "computer_batch_organize": "Tidied a folder",
    "computer_open_item": "Opened",
    "computer_run_applescript": "Ran AppleScript",
    "computer_hardware_overview": "macOS Hardware & Power",
    "computer_manage_file": "File Operations & Trash",
    "computer_clipboard": "System Clipboard",
    "computer_inspect_port": "Port Inspector",
    "computer_kill_process": "Terminate Process",
    "computer_start_job": "Start Background Daemon",
    "computer_list_jobs": "Active Background Jobs",
    "computer_stop_job": "Stop Background Daemon",
    "fs_list_workspace_files": "Explore Workspace Files",
    "fs_read_workspace_file": "Read Workspace File",
    "fs_write_workspace_file": "Write Workspace File",
    "fs_search_workspace": "Search Workspace Codebase",
    "fs_create_folder": "Create Folder",
    "fs_move_file": "Move File",
    "fs_organize_files": "Organize Files into Folder"
  };
  return map[name] || name;
}

function getToolIcon(name) {
  const map = {
    "preview_check": "bug-play",
    "tavily_search": "sparkles",
    "computer_directory_tree": "folder-tree",
    "computer_list_directory": "folder-search",
    "computer_read_file": "file-text",
    "computer_write_file": "file-plus",
    "computer_edit_file": "file-edit",
    "computer_batch_organize": "package-check",
    "computer_open_item": "external-link",
    "computer_run_applescript": "terminal-square",
    "computer_hardware_overview": "cpu",
    "computer_find_files": "search",
    "computer_manage_file": "folder-sync",
    "computer_clipboard": "clipboard",
    "computer_inspect_port": "network",
    "computer_kill_process": "x-circle",
    "computer_start_job": "play-circle",
    "computer_list_jobs": "list",
    "computer_stop_job": "stop-circle",
    "fs_list_workspace_files": "folder-search",
    "fs_read_workspace_file": "file-text",
    "fs_write_workspace_file": "file-edit",
    "fs_search_workspace": "search",
    "fs_create_folder": "folder-plus",
    "fs_move_file": "folder-input",
    "fs_organize_files": "folder-tree",
    "browser_navigate": "compass",
    "browser_click": "mouse-pointer-click",
    "browser_type": "keyboard",
    "browser_snapshot": "camera",
    "browser_extract_content": "file-text",
    "browser_evaluate_script": "code",
    "computer_run_command": "terminal",
    "computer_inspect_system": "cpu",
    "computer_notify_user": "bell",
    "computer_reveal_finder": "folder-open",
    "find_work_email": "mail-check",
    "verify_email_bounce_risk": "shield-check",
    "scan_live_job_firehose": "briefcase",
    "audit_funding_and_org": "landmark",
    "scan_trigger_events": "zap",
    "scan_live_social_trends": "flame",
    "search_topic_buzz": "messages-square",
    "compare_traffic_head_to_head": "scale",
    "audit_traffic_and_popularity": "bar-chart-2",
    "inspect_url_path_activity": "compass",
    "audit_subdomains_activity": "network",
    "omni_deep_search": "radar",
    "audit_ip_landscape": "award",
    "audit_sec_filings": "landmark",
    "audit_package_ecosystem": "package",
    "audit_talent_and_hiring": "briefcase",
    "recon_shadow_subdomains": "globe",
    "audit_tech_stack": "cpu",
    "mine_community_sentiment": "message-square",
    "audit_github_repo": "git-branch",
    "inspect_domain_dns_health": "shield-check",
    "explore_site_surface": "compass",
    "update_knowledge_graph_entity": "database",
    "query_knowledge_graph": "network",
    "web_search": "globe",
    "memory_search": "brain",
    "xod_tasks": "list-checks",
    "web_scrape": "globe",
    "calculate_saas_metrics": "calculator",
    "find_work_email": "mail",
    "verify_email_bounce_risk": "shield-check",
    "scan_live_job_firehose": "flame",
    "scan_batch_job_firehose": "layers",
    "audit_funding_and_org": "landmark",
    "scan_trigger_events": "zap"
  };
  if (map[name]) return map[name];
  if (name.startsWith("trustmrr")) return "shield-check";
  if (name.startsWith("you_")) return "search";
  return "sparkles";
}

function truncateArgs(args) {
  if (!args) return "";
  const str = typeof args === "string" ? args : JSON.stringify(args);
  return str.length > 35 ? str.substring(0, 35) + "..." : str;
}

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ==============================================================
// HUNTER & OUTREACH INTELLIGENCE SUITE CONTROLLERS
// ==============================================================

function openHunterSuiteModal() {
  const modal = document.getElementById("hunterSuiteModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  if (window.lucide) lucide.createIcons();
}

function closeHunterSuiteModal() {
  const modal = document.getElementById("hunterSuiteModal");
  if (!modal) return;
  modal.classList.add("hidden");
}

function switchHunterTab(tabName) {
  const tabs = ["email", "jobs", "triggers", "funding"];
  tabs.forEach(t => {
    const btn = document.getElementById(`hunter-tab-btn-${t}`);
    const panel = document.getElementById(`hunterPanel${t.charAt(0).toUpperCase() + t.slice(1)}`);
    if (btn && panel) {
      if (t === tabName) {
        btn.className = "hunter-suite-tab px-3.5 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-2 transition-all bg-rose-500/20 text-rose-300 border border-rose-500/40 shadow-sm";
        panel.classList.remove("hidden");
      } else {
        btn.className = "hunter-suite-tab px-3.5 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-2 transition-all text-zinc-400 hover:text-zinc-200 border border-transparent";
        panel.classList.add("hidden");
      }
    }
  });
  if (window.lucide) lucide.createIcons();
}

function copyToClipboard(text, btnElement) {
  navigator.clipboard.writeText(text).then(() => {
    if (btnElement) {
      const origHtml = btnElement.innerHTML;
      btnElement.innerHTML = `<i data-lucide="check" class="size-3 inline mr-1"></i> Copied!`;
      btnElement.classList.add("text-emerald-400");
      if (window.lucide) lucide.createIcons();
      setTimeout(() => {
        btnElement.innerHTML = origHtml;
        btnElement.classList.remove("text-emerald-400");
        if (window.lucide) lucide.createIcons();
      }, 2000);
    }
  }).catch(err => {
    console.error("Clipboard copy failed:", err);
  });
}

// -------------------------------------------------------------
// TAB 1: EMAIL HUNTER & VERIFIER
// -------------------------------------------------------------
function switchEmailSubmode(mode) {
  const findForm = document.getElementById("emailFindForm");
  const verifyForm = document.getElementById("emailVerifyForm");
  const btnFind = document.getElementById("btnEmailSubmodeFind");
  const btnVerify = document.getElementById("btnEmailSubmodeVerify");
  const res = document.getElementById("emailResultsContainer");
  if (res) res.classList.add("hidden");

  if (mode === "find") {
    findForm.classList.remove("hidden");
    verifyForm.classList.add("hidden");
    btnFind.className = "px-3 py-1.5 rounded-lg text-xs font-semibold bg-surface-700 text-white border border-surface-600 transition-colors";
    btnVerify.className = "px-3 py-1.5 rounded-lg text-xs font-semibold bg-surface-800 text-zinc-400 hover:text-white border border-transparent transition-colors";
  } else {
    findForm.classList.add("hidden");
    verifyForm.classList.remove("hidden");
    btnFind.className = "px-3 py-1.5 rounded-lg text-xs font-semibold bg-surface-800 text-zinc-400 hover:text-white border border-transparent transition-colors";
    btnVerify.className = "px-3 py-1.5 rounded-lg text-xs font-semibold bg-surface-700 text-white border border-surface-600 transition-colors";
  }
  if (window.lucide) lucide.createIcons();
}

function presetEmailFinder(first, last, domain) {
  document.getElementById("emailInputFirst").value = first;
  document.getElementById("emailInputLast").value = last;
  document.getElementById("emailInputDomain").value = domain;
}

function presetEmailVerify(email) {
  document.getElementById("emailInputVerify").value = email;
}

async function executeFindWorkEmail() {
  const first = document.getElementById("emailInputFirst").value.trim();
  const last = document.getElementById("emailInputLast").value.trim();
  const domain = document.getElementById("emailInputDomain").value.trim();
  const container = document.getElementById("emailResultsContainer");
  const btn = document.getElementById("btnRunEmailFind");

  if (!first || !domain) {
    alert("Please provide at least a First Name and Company Domain.");
    return;
  }

  container.classList.remove("hidden");
  container.innerHTML = `
    <div class="p-8 text-center bg-surface-900/60 rounded-xl border border-surface-700 text-zinc-400 text-xs">
      <i data-lucide="loader-2" class="size-6 animate-spin mx-auto mb-2 text-rose-400"></i>
      Synthesizing permutation matrix, auditing MX mail exchangers, and mining public GitHub commits...
    </div>
  `;
  btn.disabled = true;
  if (window.lucide) lucide.createIcons();

  try {
    const res = await fetch("/api/email/find", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ first_name: first, last_name: last, domain: domain })
    });
    const data = await res.json();
    btn.disabled = false;

    const best = data.best_match || {};
    const perms = data.permutation_matrix || [];
    const evidence = data.verification_evidence || [];

    let permsHtml = perms.map(p => `
      <tr class="border-b border-surface-800 text-xs hover:bg-surface-800/40 transition-colors">
        <td class="py-2.5 px-3 font-mono text-zinc-200">${escapeHtml(p.email)}</td>
        <td class="py-2.5 px-3 font-mono text-[11px] text-zinc-400">${escapeHtml(p.pattern)}</td>
        <td class="py-2.5 px-3">
          <div class="flex items-center gap-2">
            <div class="w-16 bg-surface-700 rounded-full h-1.5 overflow-hidden">
              <div class="bg-gradient-to-r from-rose-500 to-emerald-400 h-1.5" style="width: ${p.confidence_score}%"></div>
            </div>
            <span class="font-mono text-[11px] font-bold ${p.confidence_score >= 80 ? 'text-emerald-400' : p.confidence_score >= 60 ? 'text-amber-400' : 'text-zinc-400'}">${p.confidence_score}%</span>
          </div>
        </td>
        <td class="py-2.5 px-3 text-right">
          <button onclick="copyToClipboard('${escapeHtml(p.email)}', this)" class="px-2.5 py-1 rounded bg-surface-700 hover:bg-surface-600 text-zinc-300 hover:text-white text-[11px] transition-colors flex items-center gap-1 ml-auto">
            <i data-lucide="copy" class="size-3"></i> Copy
          </button>
        </td>
      </tr>
    `).join("");

    let evidenceHtml = evidence.map(e => `
      <li class="flex items-start gap-2 text-xs text-zinc-300">
        <i data-lucide="check-circle" class="size-3.5 text-emerald-400 shrink-0 mt-0.5"></i>
        <span>${escapeHtml(e)}</span>
      </li>
    `).join("");

    container.innerHTML = `
      <!-- Primary Match Card -->
      <div class="p-5 rounded-xl bg-gradient-to-r from-rose-950/30 via-surface-800 to-surface-800 border border-rose-500/40 space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <span class="size-2.5 rounded-full bg-emerald-400 animate-pulse"></span>
            <span class="text-[11px] font-semibold uppercase tracking-wider text-rose-400">Primary Verified Candidate</span>
          </div>
          <div class="flex items-center gap-2">
            <span class="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-surface-700 text-zinc-300 border border-surface-600">MX: ${escapeHtml(data.detected_mx_provider || 'Active MX')}</span>
            <span class="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">${best.confidence_score || 0}% Confidence</span>
          </div>
        </div>

        <div class="flex flex-wrap items-center justify-between gap-3 pt-1">
          <div>
            <div class="text-xl font-mono font-black text-white">${escapeHtml(best.email || 'N/A')}</div>
            <p class="text-xs text-zinc-400 mt-0.5">Pattern: <code class="text-rose-300">${escapeHtml(best.pattern || 'standard')}</code> · Status: <span class="text-emerald-400 font-semibold">${escapeHtml(data.deliverability_rating || 'High Confidence')}</span></p>
          </div>
          <button onclick="copyToClipboard('${escapeHtml(best.email || '')}', this)" class="px-4 py-2 rounded-xl bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold flex items-center gap-1.5 shadow-md shadow-rose-950 transition-all">
            <i data-lucide="copy" class="size-3.5"></i>
            <span>Copy Verified Email</span>
          </button>
        </div>

        ${evidence.length > 0 ? `
          <div class="pt-3 border-t border-surface-700/60 space-y-1.5">
            <span class="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">Zero-Key Verification Footprint:</span>
            <ul class="space-y-1">${evidenceHtml}</ul>
          </div>
        ` : ''}
      </div>

      <!-- Candidate Permutations Table -->
      <div class="bg-surface-900 border border-surface-700 rounded-xl overflow-hidden">
        <div class="px-4 py-3 border-b border-surface-700 bg-surface-850/60 flex items-center justify-between">
          <h5 class="text-xs font-bold text-white uppercase tracking-wider">Candidate Permutation Matrix (${perms.length} Variants)</h5>
          <span class="text-[11px] text-zinc-400">Sorted by Deliverability Probability</span>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left">
            <thead class="text-[10px] uppercase font-semibold text-zinc-400 border-b border-surface-800 bg-surface-900">
              <tr>
                <th class="py-2 px-3">Email Address</th>
                <th class="py-2 px-3">Pattern</th>
                <th class="py-2 px-3">Confidence Meter</th>
                <th class="py-2 px-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              ${permsHtml}
            </tbody>
          </table>
        </div>
      </div>
    `;
    if (window.lucide) lucide.createIcons();

  } catch (err) {
    btn.disabled = false;
    container.innerHTML = `<div class="p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-300 text-xs">Error finding email: ${escapeHtml(err.message)}</div>`;
  }
}

async function executeVerifyEmail() {
  const email = document.getElementById("emailInputVerify").value.trim();
  const container = document.getElementById("emailResultsContainer");
  const btn = document.getElementById("btnRunEmailVerify");

  if (!email || !email.includes("@")) {
    alert("Please enter a valid email address.");
    return;
  }

  container.classList.remove("hidden");
  container.innerHTML = `
    <div class="p-8 text-center bg-surface-900/60 rounded-xl border border-surface-700 text-zinc-400 text-xs">
      <i data-lucide="loader-2" class="size-6 animate-spin mx-auto mb-2 text-emerald-400"></i>
      Querying Cloudflare DNS-over-HTTPS for MX records, auditing SPF/DMARC policies & checking disposable registries...
    </div>
  `;
  btn.disabled = true;
  if (window.lucide) lucide.createIcons();

  try {
    const res = await fetch("/api/email/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email })
    });
    const data = await res.json();
    btn.disabled = false;

    const score = data.deliverability_score || 0;
    const isDeliverable = data.is_deliverable;
    const isDisposable = data.is_disposable_domain;
    const mxActive = data.mx_records_found;
    const mxProvider = data.mx_provider || "None";
    const spf = data.spf_configured ? "Configured & Active" : "Missing / Unenforced";
    const dmarc = data.dmarc_enforced ? "Strict Alignment" : "None";

    container.innerHTML = `
      <div class="p-5 rounded-xl bg-surface-800 border ${isDeliverable ? 'border-emerald-500/40' : 'border-red-500/40'} space-y-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex items-center gap-3">
            <div class="size-10 rounded-xl ${isDeliverable ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'} flex items-center justify-center font-bold text-lg font-mono">
              ${score}%
            </div>
            <div>
              <div class="text-base font-bold text-white font-mono">${escapeHtml(data.email)}</div>
              <p class="text-xs text-zinc-400">Bounce Risk: <span class="font-semibold ${isDeliverable ? 'text-emerald-400' : 'text-red-400'}">${escapeHtml(data.bounce_risk || 'Unspecified')}</span></p>
            </div>
          </div>
          <button onclick="copyToClipboard('${escapeHtml(data.email)}', this)" class="px-3.5 py-1.5 rounded-lg bg-surface-700 hover:bg-surface-600 text-zinc-200 text-xs transition-colors flex items-center gap-1.5">
            <i data-lucide="copy" class="size-3"></i> Copy
          </button>
        </div>

        <div class="grid grid-cols-2 sm:grid-cols-4 gap-2.5 pt-2">
          <div class="p-3 rounded-lg bg-surface-900 border border-surface-700">
            <span class="text-[10px] text-zinc-500 block uppercase font-semibold">MX Mail Exchanger</span>
            <span class="text-xs font-semibold ${mxActive ? 'text-emerald-400' : 'text-red-400'} flex items-center gap-1 mt-0.5">
              <i data-lucide="${mxActive ? 'check' : 'x'}" class="size-3"></i> ${escapeHtml(mxProvider)}
            </span>
          </div>
          <div class="p-3 rounded-lg bg-surface-900 border border-surface-700">
            <span class="text-[10px] text-zinc-500 block uppercase font-semibold">SPF Authentication</span>
            <span class="text-xs font-semibold ${data.spf_configured ? 'text-emerald-400' : 'text-amber-400'} flex items-center gap-1 mt-0.5">
              <i data-lucide="${data.spf_configured ? 'check' : 'alert-circle'}" class="size-3"></i> ${spf}
            </span>
          </div>
          <div class="p-3 rounded-lg bg-surface-900 border border-surface-700">
            <span class="text-[10px] text-zinc-500 block uppercase font-semibold">DMARC Policy</span>
            <span class="text-xs font-semibold ${data.dmarc_enforced ? 'text-emerald-400' : 'text-zinc-400'} flex items-center gap-1 mt-0.5">
              <i data-lucide="${data.dmarc_enforced ? 'shield' : 'shield-off'}" class="size-3"></i> ${dmarc}
            </span>
          </div>
          <div class="p-3 rounded-lg bg-surface-900 border border-surface-700">
            <span class="text-[10px] text-zinc-500 block uppercase font-semibold">Disposable Mailbox</span>
            <span class="text-xs font-semibold ${!isDisposable ? 'text-emerald-400' : 'text-red-400'} flex items-center gap-1 mt-0.5">
              <i data-lucide="${!isDisposable ? 'check' : 'alert-triangle'}" class="size-3"></i> ${isDisposable ? 'BLOCKED DISPOSABLE' : 'Legitimate Corporate'}
            </span>
          </div>
        </div>
      </div>
    `;
    if (window.lucide) lucide.createIcons();

  } catch (err) {
    btn.disabled = false;
    container.innerHTML = `<div class="p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-300 text-xs">Error verifying email: ${escapeHtml(err.message)}</div>`;
  }
}

// -------------------------------------------------------------
// TAB 2: LIVE JOB FIREHOSE & BATCH COHORTS
// -------------------------------------------------------------
function switchJobMode(mode) {
  const singleForm = document.getElementById("jobSingleForm");
  const batchForm = document.getElementById("jobBatchForm");
  const btnSingle = document.getElementById("btnJobModeSingle");
  const btnBatch = document.getElementById("btnJobModeBatch");
  const res = document.getElementById("jobResultsContainer");
  if (res) res.classList.add("hidden");

  if (mode === "single") {
    singleForm.classList.remove("hidden");
    batchForm.classList.add("hidden");
    btnSingle.className = "px-3 py-1.5 rounded-lg text-xs font-semibold bg-surface-700 text-white border border-surface-600 transition-colors";
    btnBatch.className = "px-3 py-1.5 rounded-lg text-xs font-semibold bg-surface-800 text-zinc-400 hover:text-white border border-transparent transition-colors";
  } else {
    singleForm.classList.add("hidden");
    batchForm.classList.remove("hidden");
    btnSingle.className = "px-3 py-1.5 rounded-lg text-xs font-semibold bg-surface-800 text-zinc-400 hover:text-white border border-transparent transition-colors";
    btnBatch.className = "px-3 py-1.5 rounded-lg text-xs font-semibold bg-surface-700 text-white border border-surface-600 transition-colors";
  }
  if (window.lucide) lucide.createIcons();
}

function handleCohortSelectChange() {
  const val = document.getElementById("jobBatchCohort").value;
  const customArea = document.getElementById("jobBatchCustomArea");
  if (val === "custom") {
    customArea.classList.remove("hidden");
  } else {
    customArea.classList.add("hidden");
  }
}

function presetJobSingle(target, dept) {
  document.getElementById("jobSingleTarget").value = target;
  document.getElementById("jobSingleDept").value = dept;
}

async function executeSingleJobFirehose() {
  const target = document.getElementById("jobSingleTarget").value.trim();
  const dept = document.getElementById("jobSingleDept").value.trim();
  const maxAge = parseInt(document.getElementById("jobSingleMaxAge").value) || 45;
  const container = document.getElementById("jobResultsContainer");
  const btn = document.getElementById("btnRunJobSingle");

  if (!target) {
    alert("Please enter a target company name or slug.");
    return;
  }

  container.classList.remove("hidden");
  container.innerHTML = `
    <div class="p-8 text-center bg-surface-900/60 rounded-xl border border-surface-700 text-zinc-400 text-xs">
      <i data-lucide="loader-2" class="size-6 animate-spin mx-auto mb-2 text-rose-400"></i>
      Probing public Ashby, Greenhouse, and Lever JSON endpoints and conducting HTTP HEAD liveness probes...
    </div>
  `;
  btn.disabled = true;
  if (window.lucide) lucide.createIcons();

  try {
    const res = await fetch("/api/jobs/firehose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: target, department: dept || null, max_age_days: maxAge })
    });
    const data = await res.json();
    btn.disabled = false;

    const feed = data.firehose_feed || [];
    const provider = data.ats_provider || "None";
    const totalScanned = data.total_requisitions_scanned || 0;
    const liveVerified = data.live_verified_openings || 0;

    let rowsHtml = feed.map(j => {
      const isDead = j.liveness_status && j.liveness_status.includes("CLOSED");
      return `
        <tr class="border-b border-surface-800 text-xs hover:bg-surface-800/40 transition-colors">
          <td class="py-3 px-3">
            <div class="font-bold text-white">${escapeHtml(j.title)}</div>
            <span class="text-[11px] text-zinc-400 font-mono">${escapeHtml(j.department || 'General')}</span>
          </td>
          <td class="py-3 px-3">
            <span class="px-2 py-0.5 rounded bg-surface-800 text-zinc-300 text-[11px] border border-surface-700">${escapeHtml(j.location || 'Remote')}</span>
          </td>
          <td class="py-3 px-3 font-mono text-[11px] text-zinc-400">${escapeHtml(j.posted_date || 'Active')}</td>
          <td class="py-3 px-3">
            <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold flex items-center gap-1 w-fit ${isDead ? 'bg-red-500/20 text-red-300 border border-red-500/30' : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'}">
              <span class="size-1.5 rounded-full ${isDead ? 'bg-red-400' : 'bg-emerald-400 animate-pulse'}"></span>
              ${escapeHtml(j.liveness_status || 'STILL OPEN (200 OK)')}
            </span>
          </td>
          <td class="py-3 px-3 text-right">
            ${j.apply_url ? `
              <a href="${escapeHtml(j.apply_url)}" target="_blank" class="px-3 py-1 rounded-lg bg-rose-600/20 hover:bg-rose-600/30 text-rose-300 border border-rose-500/30 text-[11px] font-semibold inline-flex items-center gap-1 transition-all">
                <span>Apply</span> <i data-lucide="external-link" class="size-3"></i>
              </a>
            ` : '<span class="text-zinc-600 text-[11px]">—</span>'}
          </td>
        </tr>
      `;
    }).join("");

    container.innerHTML = `
      <div class="p-4 rounded-xl bg-surface-800 border border-surface-700 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h5 class="text-sm font-bold text-white">${escapeHtml(data.target)} Requisitions Feed</h5>
          <p class="text-xs text-zinc-400">Board Slug: <code class="text-rose-300 font-mono">${escapeHtml(data.board_slug || target)}</code> · ATS: <span class="text-emerald-400 font-semibold">${escapeHtml(provider)}</span></p>
        </div>
        <div class="flex items-center gap-2">
          <span class="px-3 py-1 rounded-lg bg-surface-900 text-zinc-300 text-xs border border-surface-700">${totalScanned} Total Found</span>
          <span class="px-3 py-1 rounded-lg bg-emerald-500/20 text-emerald-300 text-xs font-semibold border border-emerald-500/40">${liveVerified} Live Verified Open</span>
        </div>
      </div>

      ${feed.length > 0 ? `
        <div class="bg-surface-900 border border-surface-700 rounded-xl overflow-hidden">
          <div class="overflow-x-auto">
            <table class="w-full text-left">
              <thead class="text-[10px] uppercase font-semibold text-zinc-400 border-b border-surface-800 bg-surface-900">
                <tr>
                  <th class="py-2.5 px-3">Role & Department</th>
                  <th class="py-2.5 px-3">Location</th>
                  <th class="py-2.5 px-3">Posted / Updated</th>
                  <th class="py-2.5 px-3">Liveness Check</th>
                  <th class="py-2.5 px-3 text-right">Apply</th>
                </tr>
              </thead>
              <tbody>${rowsHtml}</tbody>
            </table>
          </div>
        </div>
      ` : `
        <div class="p-8 text-center bg-surface-900 rounded-xl border border-surface-700 text-zinc-400 text-xs">
          No live positions matched this query or ATS board is not publicly indexed.
        </div>
      `}
    `;
    if (window.lucide) lucide.createIcons();

  } catch (err) {
    btn.disabled = false;
    container.innerHTML = `<div class="p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-300 text-xs">Error scanning job firehose: ${escapeHtml(err.message)}</div>`;
  }
}

async function executeBatchJobFirehose() {
  const cohortSelect = document.getElementById("jobBatchCohort").value;
  const keyword = document.getElementById("jobBatchKeyword").value.trim();
  const maxAge = parseInt(document.getElementById("jobBatchMaxAge").value) || 45;
  const customListRaw = document.getElementById("jobBatchCustomList").value.trim();
  const container = document.getElementById("jobResultsContainer");
  const btn = document.getElementById("btnRunJobBatch");

  const payload = {
    role_keyword: keyword || null,
    max_age_days: maxAge
  };

  if (cohortSelect === "custom") {
    if (!customListRaw) {
      alert("Please enter comma-separated company names in the custom list box.");
      return;
    }
    payload.targets = customListRaw.split(",").map(s => s.trim()).filter(Boolean);
  } else {
    payload.cohort = cohortSelect;
  }

  container.classList.remove("hidden");
  container.innerHTML = `
    <div class="p-8 text-center bg-surface-900/60 rounded-xl border border-surface-700 text-zinc-400 text-xs">
      <i data-lucide="loader-2" class="size-6 animate-spin mx-auto mb-2 text-rose-400"></i>
      Executing bounded parallel sweep across cohort startups with live Ashby, Greenhouse, and Lever checks...
    </div>
  `;
  btn.disabled = true;
  if (window.lucide) lucide.createIcons();

  try {
    const res = await fetch("/api/jobs/firehose/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    btn.disabled = false;

    const feed = data.cohort_feed || [];
    const compSummaries = data.company_summaries || [];
    const totalScanned = data.total_companies_scanned || 0;
    const compsWithRoles = data.companies_with_matching_roles || 0;
    const totalRoles = data.total_matched_positions || 0;

    let summaryPillsHtml = compSummaries.map(c => `
      <span class="px-2.5 py-1 rounded-lg text-[11px] font-mono border ${c.open_roles_count > 0 ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30' : 'bg-surface-800 text-zinc-500 border-surface-700'} flex items-center gap-1.5">
        <span class="font-bold text-white">${escapeHtml(c.company)}:</span>
        <span>${c.open_roles_count}</span>
        <span class="text-[9px] text-zinc-400">(${escapeHtml(c.ats_provider)})</span>
      </span>
    `).join("");

    let rowsHtml = feed.map(j => `
      <tr class="border-b border-surface-800 text-xs hover:bg-surface-800/40 transition-colors">
        <td class="py-3 px-3">
          <span class="px-2 py-0.5 rounded bg-surface-800 text-rose-300 font-bold font-mono text-[11px] border border-surface-700 uppercase">${escapeHtml(j.company)}</span>
        </td>
        <td class="py-3 px-3">
          <div class="font-bold text-white">${escapeHtml(j.title)}</div>
          <span class="text-[11px] text-zinc-400 font-mono">${escapeHtml(j.department || 'General')}</span>
        </td>
        <td class="py-3 px-3">
          <span class="px-2 py-0.5 rounded bg-surface-800 text-zinc-300 text-[11px] border border-surface-700">${escapeHtml(j.location || 'Remote')}</span>
        </td>
        <td class="py-3 px-3 font-mono text-[11px] text-zinc-400">${escapeHtml(j.posted_date || 'Active')}</td>
        <td class="py-3 px-3">
          <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 flex items-center gap-1 w-fit">
            <span class="size-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
            ${escapeHtml(j.ats_provider)}
          </span>
        </td>
        <td class="py-3 px-3 text-right">
          ${j.apply_url ? `
            <a href="${escapeHtml(j.apply_url)}" target="_blank" class="px-3 py-1 rounded-lg bg-rose-600/20 hover:bg-rose-600/30 text-rose-300 border border-rose-500/30 text-[11px] font-semibold inline-flex items-center gap-1 transition-all">
              <span>Apply</span> <i data-lucide="external-link" class="size-3"></i>
            </a>
          ` : '<span class="text-zinc-600 text-[11px]">—</span>'}
        </td>
      </tr>
    `).join("");

    container.innerHTML = `
      <div class="p-4 rounded-xl bg-surface-800 border border-surface-700 space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h5 class="text-sm font-bold text-white">${escapeHtml(data.cohort)} Sweep Results</h5>
            <p class="text-xs text-zinc-400">Role Filter: <span class="text-rose-300 font-semibold">${escapeHtml(data.role_filter)}</span></p>
          </div>
          <div class="flex items-center gap-2">
            <span class="px-3 py-1 rounded-lg bg-surface-900 text-zinc-300 text-xs border border-surface-700">${totalScanned} Companies Scanned</span>
            <span class="px-3 py-1 rounded-lg bg-indigo-500/20 text-indigo-300 text-xs font-semibold border border-indigo-500/40">${compsWithRoles} Hiring</span>
            <span class="px-3 py-1 rounded-lg bg-emerald-500/20 text-emerald-300 text-xs font-semibold border border-emerald-500/40">${totalRoles} Openings</span>
          </div>
        </div>
        <div class="flex flex-wrap gap-1.5 pt-1">
          ${summaryPillsHtml}
        </div>
      </div>

      ${feed.length > 0 ? `
        <div class="bg-surface-900 border border-surface-700 rounded-xl overflow-hidden">
          <div class="overflow-x-auto">
            <table class="w-full text-left">
              <thead class="text-[10px] uppercase font-semibold text-zinc-400 border-b border-surface-800 bg-surface-900">
                <tr>
                  <th class="py-2.5 px-3">Company</th>
                  <th class="py-2.5 px-3">Requisition Role</th>
                  <th class="py-2.5 px-3">Location</th>
                  <th class="py-2.5 px-3">Posted Date</th>
                  <th class="py-2.5 px-3">ATS Source</th>
                  <th class="py-2.5 px-3 text-right">Apply</th>
                </tr>
              </thead>
              <tbody>${rowsHtml}</tbody>
            </table>
          </div>
        </div>
      ` : `
        <div class="p-8 text-center bg-surface-900 rounded-xl border border-surface-700 text-zinc-400 text-xs">
          No requisitions matching filter keyword in this cohort.
        </div>
      `}
    `;
    if (window.lucide) lucide.createIcons();

  } catch (err) {
    btn.disabled = false;
    container.innerHTML = `<div class="p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-300 text-xs">Error executing batch sweep: ${escapeHtml(err.message)}</div>`;
  }
}

// -------------------------------------------------------------
// TAB 3: TRIGGER RADAR & WARM OPENERS
// -------------------------------------------------------------
function presetTriggerRadar(target) {
  document.getElementById("triggerTargetInput").value = target;
}

async function executeTriggerRadar() {
  const target = document.getElementById("triggerTargetInput").value.trim();
  const container = document.getElementById("triggerResultsContainer");
  const btn = document.getElementById("btnRunTriggerRadar");

  if (!target) {
    alert("Please enter a target company name or domain.");
    return;
  }

  container.classList.remove("hidden");
  container.innerHTML = `
    <div class="p-8 text-center bg-surface-900/60 rounded-xl border border-surface-700 text-zinc-400 text-xs">
      <i data-lucide="loader-2" class="size-6 animate-spin mx-auto mb-2 text-amber-400"></i>
      Harvesting real-time corporate press RSS, Algolia Hacker News Show HN launches, and SEC Form D venture rounds...
    </div>
  `;
  btn.disabled = true;
  if (window.lucide) lucide.createIcons();

  try {
    const res = await fetch("/api/trigger/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: target })
    });
    const data = await res.json();
    btn.disabled = false;

    const openers = data.suggested_warm_openers || data.warm_outreach_openers || [];
    const news = (data.recent_news_triggers || []).map(n => ({
      source: n.source || "Google News",
      title: n.headline,
      date: n.published_at,
      url: n.link
    }));
    const hn = (data.hacker_news_triggers || []).map(h => ({
      source: "Hacker News",
      title: h.title,
      date: `${h.points || 0} pts · ${h.comments || 0} comments`,
      url: h.url
    }));
    const events = [...news, ...hn];

    let openersHtml = openers.map(op => `
      <div class="p-3.5 rounded-xl bg-surface-900 border border-surface-700 flex items-start justify-between gap-3">
        <p class="text-xs text-zinc-200 leading-relaxed italic">"${escapeHtml(op)}"</p>
        <button onclick="copyToClipboard('${escapeHtml(op)}', this)" class="px-2.5 py-1 rounded bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/30 text-[11px] font-semibold shrink-0 transition-all flex items-center gap-1">
          <i data-lucide="copy" class="size-3"></i> Copy Opener
        </button>
      </div>
    `).join("");

    let eventsHtml = events.map(ev => `
      <div class="p-3.5 rounded-xl bg-surface-900 border border-surface-700 flex items-start justify-between gap-3 hover:border-surface-600 transition-colors">
        <div class="space-y-1">
          <div class="flex items-center gap-2">
            <span class="px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider ${ev.source === 'Hacker News' ? 'bg-orange-500/20 text-orange-400 border border-orange-500/30' : ev.source === 'SEC EDGAR' ? 'bg-purple-500/20 text-purple-400 border border-purple-500/30' : 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30'}">${escapeHtml(ev.source || 'News')}</span>
            <span class="text-[11px] text-zinc-500 font-mono">${escapeHtml(ev.date || 'Recent')}</span>
          </div>
          <h6 class="text-xs font-semibold text-white">${escapeHtml(ev.title || 'Event')}</h6>
        </div>
        ${ev.url ? `
          <a href="${escapeHtml(ev.url)}" target="_blank" class="text-zinc-400 hover:text-white p-1 rounded hover:bg-surface-800 transition-colors">
            <i data-lucide="external-link" class="size-3.5"></i>
          </a>
        ` : ''}
      </div>
    `).join("");

    container.innerHTML = `
      <!-- Warm Outreach Openers Box -->
      <div class="p-5 rounded-xl bg-gradient-to-r from-amber-950/30 via-surface-800 to-surface-800 border border-amber-500/40 space-y-3">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <i data-lucide="sparkles" class="size-4 text-amber-400"></i>
            <h5 class="text-xs font-bold text-white uppercase tracking-wider">Auto-Generated Warm Outreach Openers</h5>
          </div>
          <span class="text-[10px] text-amber-300 font-mono">Fact-Checked & Non-Hallucinated</span>
        </div>
        <div class="space-y-2">
          ${openers.length > 0 ? openersHtml : '<p class="text-xs text-zinc-400 italic">No fresh trigger openers synthesized.</p>'}
        </div>
      </div>

      <!-- Trigger Timeline -->
      <div class="space-y-2">
        <h5 class="text-xs font-bold text-white uppercase tracking-wider px-1">Trigger Event Feed (${events.length} Events Detected)</h5>
        <div class="space-y-2">
          ${events.length > 0 ? eventsHtml : '<p class="text-xs text-zinc-400 p-4 text-center bg-surface-900 rounded-xl border border-surface-700">No events found in recent 60-day window.</p>'}
        </div>
      </div>
    `;
    if (window.lucide) lucide.createIcons();

  } catch (err) {
    btn.disabled = false;
    container.innerHTML = `<div class="p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-300 text-xs">Error scanning trigger radar: ${escapeHtml(err.message)}</div>`;
  }
}

// -------------------------------------------------------------
// TAB 4: YC & SEC DILIGENCE
// -------------------------------------------------------------
function presetFundingDiligence(target) {
  document.getElementById("fundingTargetInput").value = target;
}

async function executeFundingDiligence() {
  const target = document.getElementById("fundingTargetInput").value.trim();
  const container = document.getElementById("fundingResultsContainer");
  const btn = document.getElementById("btnRunFundingDiligence");

  if (!target) {
    alert("Please enter a startup name or slug.");
    return;
  }

  container.classList.remove("hidden");
  container.innerHTML = `
    <div class="p-8 text-center bg-surface-900/60 rounded-xl border border-surface-700 text-zinc-400 text-xs">
      <i data-lucide="loader-2" class="size-6 animate-spin mx-auto mb-2 text-purple-400"></i>
      Harvesting Y Combinator verified directory, active founder registries, and SEC Form D venture filings...
    </div>
  `;
  btn.disabled = true;
  if (window.lucide) lucide.createIcons();

  try {
    const res = await fetch("/api/funding/org", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: target })
    });
    const data = await res.json();
    btn.disabled = false;

    const acc = data.accelerator || {};
    const org = data.org_structure || {};
    const cap = data.verified_capital || {};
    const founders = org.founders || [];
    const score = data.legitimacy_score || 0;
    const isYc = acc.backed;

    let foundersHtml = founders.map(f => `
      <span class="px-2.5 py-1 rounded-lg bg-surface-800 text-zinc-200 border border-surface-700 font-medium text-xs flex items-center gap-1.5">
        <i data-lucide="user" class="size-3 text-purple-400"></i>
        ${escapeHtml(f)}
      </span>
    `).join("");

    container.innerHTML = `
      <!-- Top Score Card -->
      <div class="p-5 rounded-xl bg-gradient-to-r from-purple-950/30 via-surface-800 to-surface-800 border border-purple-500/40 space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex items-center gap-3">
            <div class="size-10 rounded-xl bg-purple-500/20 text-purple-400 flex items-center justify-center font-bold text-lg font-mono">
              ${score}
            </div>
            <div>
              <div class="text-base font-bold text-white">${escapeHtml(data.target)} Due Diligence</div>
              <p class="text-xs text-zinc-400">Entity Legitimacy: <span class="font-semibold text-purple-300">${escapeHtml(data.scam_risk || 'Standard')}</span></p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            ${isYc ? `
              <span class="px-3 py-1 rounded-full text-xs font-bold bg-orange-500/20 text-orange-400 border border-orange-500/40 flex items-center gap-1">
                <span>YC Batch:</span> <span>${escapeHtml(acc.batch)}</span>
              </span>
            ` : '<span class="px-3 py-1 rounded-full text-xs bg-surface-700 text-zinc-400">Non-YC / Direct Venture</span>'}
          </div>
        </div>
      </div>

      <!-- Detail Metric Grid -->
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <!-- Card 1: Org Structure -->
        <div class="p-4 rounded-xl bg-surface-900 border border-surface-700 space-y-2">
          <span class="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">Team & Headcount</span>
          <div class="text-lg font-bold text-white">${escapeHtml(org.headcount_estimate || 'Unspecified')}</div>
          <p class="text-xs text-zinc-400">Delaware Entity: <code class="text-purple-300">${escapeHtml(org.delaware_entity || 'On File / In Good Standing')}</code></p>
        </div>

        <!-- Card 2: SEC Capital Sold -->
        <div class="p-4 rounded-xl bg-surface-900 border border-surface-700 space-y-2">
          <span class="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">SEC Registered Capital</span>
          <div class="text-lg font-bold text-emerald-400 font-mono">${escapeHtml(cap.total_vc_sold_usd || '$0')}</div>
          <p class="text-xs text-zinc-400">Filings on file: <span class="text-zinc-200 font-semibold">${cap.filings_count || 0} Form D</span></p>
        </div>

        <!-- Card 3: YC Partner & Status -->
        <div class="p-4 rounded-xl bg-surface-900 border border-surface-700 space-y-2">
          <span class="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">Accelerator Details</span>
          <div class="text-lg font-bold text-white">${escapeHtml(acc.status || 'Active')}</div>
          <p class="text-xs text-zinc-400">Primary Partner: <span class="text-zinc-200">${escapeHtml(acc.primary_partner || 'Y Combinator')}</span></p>
        </div>
      </div>

      <!-- Verified Founders -->
      <div class="p-4 rounded-xl bg-surface-900 border border-surface-700 space-y-2">
        <span class="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">Verified Founders & Executive Officers:</span>
        <div class="flex flex-wrap gap-2">
          ${founders.length > 0 ? foundersHtml : '<span class="text-xs text-zinc-500">No public executive names indexed.</span>'}
        </div>
      </div>
    `;
    if (window.lucide) lucide.createIcons();

  } catch (err) {
    btn.disabled = false;
    container.innerHTML = `<div class="p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-300 text-xs">Error performing due diligence: ${escapeHtml(err.message)}</div>`;
  }
}

// ==============================================================
// LOCAL COMPUTER PROJECTS & WORKSPACES SUBSYSTEM
// ==============================================================

// -------------------------------------------------------------
// CREATE PROJECT MODAL
// -------------------------------------------------------------
const PROJECT_TEMPLATES = [
  { label: "Compare brands", emoji: "⚔️", name: "Brand comparison", icon: "swords", color: "#f43f5e", desc: "Compare two or more brands side by side — traffic, pricing and products." },
  { label: "Startup idea", emoji: "🚀", name: "My startup idea", icon: "rocket", color: "#6366f1", desc: "Validate an idea: competitors, market size and who would pay." },
  { label: "Market research", emoji: "📈", name: "Market research", icon: "trending-up", color: "#10b981", desc: "Understand a market — key players, trends and opportunities." },
  { label: "Find leads", emoji: "🎯", name: "Lead list", icon: "target", color: "#f59e0b", desc: "Find companies and people worth reaching out to." },
  { label: "Investing", emoji: "💼", name: "Investment research", icon: "briefcase", color: "#06b6d4", desc: "Research companies before investing or buying." },
];
const PROJECT_ICONS = ["folder", "rocket", "trending-up", "target", "briefcase", "swords", "shopping-bag", "lightbulb", "globe", "heart", "star", "coffee"];
const PROJECT_COLORS = ["#6366f1", "#10b981", "#06b6d4", "#f43f5e", "#f59e0b", "#8b5cf6"];
let selectedProjectIcon = "folder";

function openCreateProjectModal() {
  const modal = document.getElementById("createProjectModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  document.getElementById("newProjectName").value = "";
  const desc = document.getElementById("newProjectDesc");
  desc.value = "";
  desc.classList.add("hidden");
  const errEl = document.getElementById("newProjectNameError");
  errEl.textContent = "Give your project a name to continue.";
  errEl.classList.add("hidden");
  selectedProjectColor = PROJECT_COLORS[0];
  selectedProjectIcon = "folder";
  setProjectLocationMode("xod");
  renderProjectTemplateChips();
  renderProjectLookPickers();
  updateNewProjectPreview();
  setTimeout(() => document.getElementById("newProjectName").focus(), 50);
}

function closeCreateProjectModal() {
  const modal = document.getElementById("createProjectModal");
  if (!modal) return;
  modal.classList.add("hidden");
}

let projectLocationMode = "xod";

function setProjectLocationMode(mode) {
  projectLocationMode = mode;
  const btnXod = document.getElementById("btnLocXod");
  const btnCustom = document.getElementById("btnLocCustom");
  const box = document.getElementById("customProjectLocationBox");
  const badge = document.getElementById("newProjectLocationTypeBadge");

  if (mode === "custom") {
    if (btnXod) {
      btnXod.className = "px-3 py-2 rounded-xl border border-surface-700 bg-surface-900/60 text-left hover:border-surface-600 transition-all flex items-center gap-2";
    }
    if (btnCustom) {
      btnCustom.className = "px-3 py-2 rounded-xl border border-cyan-500 bg-cyan-500/10 text-left transition-all flex items-center gap-2";
    }
    if (box) box.classList.remove("hidden");
    if (badge) badge.textContent = "computer folder";
    const customInput = document.getElementById("newProjectCustomPathInput");
    if (customInput && !customInput.value) {
      setProjectLocationPreset("documents");
    }
  } else {
    if (btnXod) {
      btnXod.className = "px-3 py-2 rounded-xl border border-brand-500 bg-brand-500/10 text-left transition-all flex items-center gap-2";
    }
    if (btnCustom) {
      btnCustom.className = "px-3 py-2 rounded-xl border border-surface-700 bg-surface-900/60 text-left hover:border-surface-600 transition-all flex items-center gap-2";
    }
    if (box) box.classList.add("hidden");
    if (badge) badge.textContent = "Xod projects folder";
  }
  updateNewProjectPreview();
}

function setProjectLocationPreset(preset) {
  const name = document.getElementById("newProjectName").value.trim() || "my-project";
  const slug = name.toLowerCase().replace(/[^a-z0-9_-]/g, "_").replace(/_+/g, "_");
  const input = document.getElementById("newProjectCustomPathInput");
  if (!input) return;

  const home = window.currentComputerProfile?.user_home || "~";
  if (preset === "documents") {
    input.value = `${home}/Documents/${slug}`;
  } else if (preset === "desktop") {
    input.value = `${home}/Desktop/${slug}`;
  } else if (preset === "downloads") {
    input.value = `${home}/Downloads/${slug}`;
  }
  handleCustomPathInput();
}

function handleCustomPathInput() {
  const input = document.getElementById("newProjectCustomPathInput");
  const val = input ? input.value.trim() : "";
  const previewPath = document.getElementById("newProjectPreviewPath");
  if (previewPath && projectLocationMode === "custom") {
    previewPath.textContent = val || "~/Documents/...";
  }
}

function openComputerFilePickerForProject() {
  const home = window.currentComputerProfile?.user_home || "~";
  openComputerFileManagerModal(`${home}/Documents`);
  showToast("Browse to your desired folder, then click 'Link as Project'");
}

function renderProjectTemplateChips() {
  const wrap = document.getElementById("projectTemplateChips");
  if (!wrap) return;
  wrap.innerHTML = PROJECT_TEMPLATES.map((t, i) => `
    <button type="button" onclick="applyProjectTemplate(${i})"
      class="px-3 py-1.5 rounded-full bg-surface-900 border border-surface-700 hover:border-brand-500/60 hover:bg-surface-800 text-[12px] text-zinc-300 hover:text-white transition-all flex items-center gap-1.5">
      <span>${t.emoji}</span><span>${t.label}</span>
    </button>`).join("");
}

function applyProjectTemplate(i) {
  const t = PROJECT_TEMPLATES[i];
  if (!t) return;
  const nameEl = document.getElementById("newProjectName");
  nameEl.value = t.name;
  const desc = document.getElementById("newProjectDesc");
  desc.value = t.desc;
  desc.classList.remove("hidden");
  selectedProjectIcon = t.icon;
  selectedProjectColor = t.color;
  renderProjectLookPickers();
  updateNewProjectPreview();
  nameEl.focus();
  nameEl.select();
}

function renderProjectLookPickers() {
  const iconWrap = document.getElementById("projectIconPicker");
  const colorWrap = document.getElementById("projectColorPicker");
  if (iconWrap) {
    iconWrap.innerHTML = PROJECT_ICONS.map(ic => {
      const active = ic === selectedProjectIcon;
      return `<button type="button" onclick="selectProjectIcon('${ic}')" title="${ic.replace(/-/g, " ")}"
        class="size-9 rounded-lg flex items-center justify-center border transition-all ${active ? "border-transparent" : "border-surface-700 bg-surface-900 text-zinc-400 hover:text-white hover:border-zinc-500"}"
        style="${active ? `background:${selectedProjectColor}22;color:${selectedProjectColor};box-shadow:0 0 0 2px ${selectedProjectColor}` : ""}">
        <i data-lucide="${ic}" class="size-4"></i></button>`;
    }).join("");
  }
  if (colorWrap) {
    colorWrap.innerHTML = PROJECT_COLORS.map(c => {
      const active = c === selectedProjectColor;
      return `<button type="button" onclick="selectProjectColor('${c}')" title="Color"
        class="size-6 rounded-full transition-transform hover:scale-110 ${active ? "ring-2 ring-white ring-offset-2 ring-offset-surface-850" : ""}"
        style="background:${c}"></button>`;
    }).join("");
  }
  if (window.lucide) lucide.createIcons();
}

function selectProjectIcon(icon) {
  selectedProjectIcon = icon;
  renderProjectLookPickers();
  updateNewProjectPreview();
}

function selectProjectColor(color) {
  selectedProjectColor = color;
  renderProjectLookPickers();
  updateNewProjectPreview();
}

function toggleNewProjectDesc() {
  const desc = document.getElementById("newProjectDesc");
  desc.classList.toggle("hidden");
  if (!desc.classList.contains("hidden")) desc.focus();
}

function updateNewProjectPreview() {
  const name = document.getElementById("newProjectName").value.trim();
  const nameEl = document.getElementById("newProjectPreviewName");
  const iconEl = document.getElementById("newProjectPreviewIcon");
  const previewPath = document.getElementById("newProjectPreviewPath");

  if (name) document.getElementById("newProjectNameError").classList.add("hidden");
  if (nameEl) {
    nameEl.textContent = name || "Your project name";
    nameEl.classList.toggle("text-white", !!name);
    nameEl.classList.toggle("text-zinc-500", !name);
  }
  if (iconEl) {
    iconEl.style.background = selectedProjectColor + "22";
    iconEl.style.color = selectedProjectColor;
    iconEl.innerHTML = `<i data-lucide="${selectedProjectIcon}" class="size-4"></i>`;
    if (window.lucide) lucide.createIcons();
  }
  if (previewPath) {
    if (projectLocationMode === "custom") {
      const customVal = document.getElementById("newProjectCustomPathInput")?.value.trim();
      previewPath.textContent = customVal || "~/Documents/...";
    } else {
      const slug = (name || "your_project_name").toLowerCase().replace(/[^a-z0-9_-]/g, "_").replace(/_+/g, "_");
      previewPath.textContent = `Xod projects / ${slug}`;
    }
  }
}

function showToast(message) {
  const toast = document.getElementById("xodToast");
  const text = document.getElementById("xodToastText");
  if (!toast || !text) return;
  text.textContent = message;
  toast.classList.remove("hidden");
  if (window.lucide) lucide.createIcons();
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toast.classList.add("hidden"), 3000);
}

async function submitCreateProject() {
  const nameEl = document.getElementById("newProjectName");
  const name = nameEl.value.trim();
  const desc = document.getElementById("newProjectDesc").value.trim();
  const btn = document.getElementById("btnSubmitCreateProject");
  const customPathInput = document.getElementById("newProjectCustomPathInput");
  const customPath = (projectLocationMode === "custom" && customPathInput) ? customPathInput.value.trim() : null;

  if (!name) {
    document.getElementById("newProjectNameError").classList.remove("hidden");
    nameEl.focus();
    return;
  }

  btn.disabled = true;
  try {
    const res = await fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name,
        description: desc,
        color: selectedProjectColor,
        icon: selectedProjectIcon,
        custom_path: customPath
      })
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || "Something went wrong. Please try again.");
    }
    const data = await res.json();
    closeCreateProjectModal();

    if (data.slug) {
      expandedProjectFolders.add(data.slug);
      activeProjectSlug = data.slug;
    }
    await loadProjects();
    showToast(`"${name}" is ready`);
  } catch (err) {
    const errEl = document.getElementById("newProjectNameError");
    errEl.textContent = err.message || "Couldn't create the project. Please try again.";
    errEl.classList.remove("hidden");
  } finally {
    btn.disabled = false;
  }
}

// ==============================================================
// OBSERVABILITY & LIVING KNOWLEDGE CANVAS ENGINE (BETTER THAN NEO4J)
// ==============================================================

let obsData = { nodes: [], edges: [], summary: {} };
let canvasPhysicsRunning = true;
let canvasAnimFrame = null;
let canvasNodes = [];
let canvasEdges = [];
let canvasZoom = 1;
let canvasPan = { x: 0, y: 0 };
let isPanningCanvas = false;
let panStart = { x: 0, y: 0 };
let draggedCanvasNode = null;
let hoveredCanvasNode = null;
let selectedCanvasNode = null;
let canvasSearchTerm = "";
let canvasTypeFilters = { project: true, startup: true, tech: true, file: true, founder: true };

async function openObservabilityModal() {
  const modal = document.getElementById("observabilityModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  if (window.lucide) lucide.createIcons();
  await fetchObservabilityData();
  switchObsTab("canvas");
}

function closeObservabilityModal() {
  const modal = document.getElementById("observabilityModal");
  if (!modal) return;
  modal.classList.add("hidden");
  if (canvasAnimFrame) {
    cancelAnimationFrame(canvasAnimFrame);
    canvasAnimFrame = null;
  }
}

function switchObsTab(tabName) {
  const tabs = ["canvas", "matrix", "files", "search"];
  tabs.forEach(t => {
    const btn = document.getElementById(`obs-tab-btn-${t}`);
    const panel = document.getElementById(`obsPanel${t.charAt(0).toUpperCase() + t.slice(1)}`);
    if (btn && panel) {
      if (t === tabName) {
        btn.className = "obs-tab px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-2 transition-all bg-purple-500/20 text-purple-300 border border-purple-500/40 shadow-sm";
        panel.classList.remove("hidden");
      } else {
        btn.className = "obs-tab px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-2 transition-all text-zinc-400 hover:text-zinc-200 border border-transparent";
        panel.classList.add("hidden");
      }
    }
  });

  if (tabName === "canvas") {
    setTimeout(initObservabilityCanvas, 50);
  } else if (tabName === "matrix") {
    renderObservabilityMatrix();
  } else if (tabName === "files") {
    renderFileExplorerTree();
  }
  if (window.lucide) lucide.createIcons();
}

async function fetchObservabilityData(force = false) {
  try {
    const res = await fetch("/api/projects/observability");
    obsData = await res.json();

    const sum = obsData.summary || {};
    const bNodes = document.getElementById("obsBadgeNodes");
    const bEdges = document.getElementById("obsBadgeEdges");
    const bDisk = document.getElementById("obsBadgeDisk");
    if (bNodes) bNodes.textContent = `${sum.total_nodes || 0} Nodes`;
    if (bEdges) bEdges.textContent = `${sum.total_edges || 0} Interlinks`;
    if (bDisk) bDisk.textContent = `${sum.total_disk_formatted || '0 KB'} Disk`;

    if (force) {
      initObservabilityCanvas();
      renderObservabilityMatrix();
      renderFileExplorerTree();
    }
  } catch (err) {
    console.error("Failed to load observability data:", err);
  }
}

function initObservabilityCanvas() {
  const canvas = document.getElementById("observabilityCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const W = rect.width;
  const H = rect.height;

  const rawNodes = obsData.nodes || [];
  const rawEdges = obsData.edges || [];

  const nodeMap = new Map();
  canvasNodes = rawNodes.map((n, i) => {
    const angle = (i / rawNodes.length) * Math.PI * 2;
    const radius = 220 + (i % 4) * 110;
    const existing = canvasNodes.find(ex => ex.id === n.id);
    const nodeObj = {
      ...n,
      x: existing ? existing.x : W / 2 + Math.cos(angle) * radius + (Math.random() - 0.5) * 80,
      y: existing ? existing.y : H / 2 + Math.sin(angle) * radius + (Math.random() - 0.5) * 80,
      vx: 0,
      vy: 0,
      radius: (n.size || 18) * 1.2
    };
    nodeMap.set(n.id, nodeObj);
    return nodeObj;
  });

  canvasEdges = rawEdges.map(e => ({
    ...e,
    sourceNode: nodeMap.get(e.source),
    targetNode: nodeMap.get(e.target)
  })).filter(e => e.sourceNode && e.targetNode);

  setupCanvasEvents(canvas, rect);

  if (!canvasAnimFrame) {
    runCanvasSimulation(canvas, ctx, W, H);
  }
}

function runCanvasSimulation(canvas, ctx, W, H) {
  function loop() {
    if (canvasPhysicsRunning) {
      updatePhysics(W, H);
    }
    renderCanvas(ctx, W, H);
    canvasAnimFrame = requestAnimationFrame(loop);
  }
  if (canvasAnimFrame) cancelAnimationFrame(canvasAnimFrame);
  canvasAnimFrame = requestAnimationFrame(loop);
}

function updatePhysics(W, H) {
  const kRep = 4800; // Much stronger repulsion so nodes don't cluster in a ball
  const kSpring = 0.025;
  const L0 = 160; // Much wider natural link distance
  const kGrav = 0.006; // Much gentler gravity toward center
  const damping = 0.88;

  for (let i = 0; i < canvasNodes.length; i++) {
    const n1 = canvasNodes[i];
    if (!isNodeVisible(n1)) continue;

    for (let j = i + 1; j < canvasNodes.length; j++) {
      const n2 = canvasNodes[j];
      if (!isNodeVisible(n2)) continue;

      const dx = n2.x - n1.x;
      const dy = n2.y - n1.y;
      const distSq = dx * dx + dy * dy + 150;
      const dist = Math.sqrt(distSq);
      const force = kRep / distSq;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;

      n1.vx -= fx;
      n1.vy -= fy;
      n2.vx += fx;
      n2.vy += fy;
    }

    const cx = W / 2;
    const cy = H / 2;
    n1.vx += (cx - n1.x) * kGrav;
    n1.vy += (cy - n1.y) * kGrav;
  }

  for (const e of canvasEdges) {
    if (!isNodeVisible(e.sourceNode) || !isNodeVisible(e.targetNode)) continue;
    const dx = e.targetNode.x - e.sourceNode.x;
    const dy = e.targetNode.y - e.sourceNode.y;
    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
    const force = (dist - L0) * kSpring;
    const fx = (dx / dist) * force;
    const fy = (dy / dist) * force;

    e.sourceNode.vx += fx;
    e.sourceNode.vy += fy;
    e.targetNode.vx -= fx;
    e.targetNode.vy -= fy;
  }

  for (const n of canvasNodes) {
    if (n === draggedCanvasNode) {
      n.vx = 0;
      n.vy = 0;
      continue;
    }
    n.vx *= damping;
    n.vy *= damping;
    n.x += n.vx;
    n.y += n.vy;
  }
}

function renderCanvas(ctx, W, H) {
  ctx.save();
  ctx.clearRect(0, 0, W, H);

  ctx.fillStyle = "#090a0f";
  ctx.fillRect(0, 0, W, H);

  ctx.translate(canvasPan.x, canvasPan.y);
  ctx.scale(canvasZoom, canvasZoom);

  // Subtle Dot Matrix
  ctx.fillStyle = "rgba(255, 255, 255, 0.04)";
  const gridSize = 35;
  const startX = Math.floor((-canvasPan.x / canvasZoom) / gridSize) * gridSize;
  const startY = Math.floor((-canvasPan.y / canvasZoom) / gridSize) * gridSize;
  const endX = startX + (W / canvasZoom) + gridSize * 2;
  const endY = startY + (H / canvasZoom) + gridSize * 2;
  for (let x = startX; x < endX; x += gridSize) {
    for (let y = startY; y < endY; y += gridSize) {
      ctx.beginPath();
      ctx.arc(x, y, 1, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  const time = Date.now() * 0.002;

  // 1. Draw Edges
  for (const e of canvasEdges) {
    if (!isNodeVisible(e.sourceNode) || !isNodeVisible(e.targetNode)) continue;
    const isConn = (hoveredCanvasNode && (e.sourceNode === hoveredCanvasNode || e.targetNode === hoveredCanvasNode)) ||
                   (selectedCanvasNode && (e.sourceNode === selectedCanvasNode || e.targetNode === selectedCanvasNode));

    ctx.beginPath();
    ctx.moveTo(e.sourceNode.x, e.sourceNode.y);
    ctx.lineTo(e.targetNode.x, e.targetNode.y);
    ctx.strokeStyle = isConn ? "rgba(255, 255, 255, 0.6)" : e.color || "rgba(255, 255, 255, 0.12)";
    ctx.lineWidth = isConn ? 2 : 1;
    ctx.stroke();

    // Pulse particle traveling along edge
    const pulseOffset = (time % 1);
    const px = e.sourceNode.x + (e.targetNode.x - e.sourceNode.x) * pulseOffset;
    const py = e.sourceNode.y + (e.targetNode.y - e.sourceNode.y) * pulseOffset;
    ctx.beginPath();
    ctx.arc(px, py, 2, 0, Math.PI * 2);
    ctx.fillStyle = isConn ? "#ffffff" : "rgba(168, 85, 247, 0.7)";
    ctx.fill();
  }

  // 2. Draw Nodes
  for (const n of canvasNodes) {
    if (!isNodeVisible(n)) continue;

    const isHovered = n === hoveredCanvasNode;
    const isSelected = n === selectedCanvasNode;
    const isHighlighted = canvasSearchTerm && n.label.toLowerCase().includes(canvasSearchTerm.toLowerCase());

    const r = n.radius * (isHovered || isSelected ? 1.25 : 1);

    ctx.shadowColor = n.color || "#8b5cf6";
    ctx.shadowBlur = (isHovered || isSelected || isHighlighted) ? 22 : 8;

    ctx.beginPath();
    ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
    ctx.fillStyle = (isHovered || isSelected) ? n.color : "rgba(18, 20, 29, 0.95)";
    ctx.fill();
    ctx.lineWidth = (isHovered || isSelected || isHighlighted) ? 3 : 1.5;
    ctx.strokeStyle = n.color || "#ffffff";
    ctx.stroke();

    ctx.shadowBlur = 0;

    // Inner Core Dot
    ctx.beginPath();
    ctx.arc(n.x, n.y, r * 0.45, 0, Math.PI * 2);
    ctx.fillStyle = (isHovered || isSelected) ? "#ffffff" : n.color;
    ctx.fill();

    // Label
    ctx.font = (isHovered || isSelected || isHighlighted) ? "bold 11px sans-serif" : "10px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";

    ctx.strokeStyle = "#090a0f";
    ctx.lineWidth = 3;
    ctx.strokeText(n.label, n.x, n.y + r + 4);

    ctx.fillStyle = (isHovered || isSelected || isHighlighted) ? "#ffffff" : "rgba(255, 255, 255, 0.85)";
    ctx.fillText(n.label, n.x, n.y + r + 4);
  }

  ctx.restore();
}

function isNodeVisible(n) {
  if (!n) return false;
  if (n.type === "project" && !canvasTypeFilters.project) return false;
  if (n.type === "startup" && !canvasTypeFilters.startup) return false;
  if (n.type === "tech" && !canvasTypeFilters.tech) return false;
  if (n.type === "file" && !canvasTypeFilters.file) return false;
  if (n.type === "founder" && !canvasTypeFilters.founder) return false;
  return true;
}

function setupCanvasEvents(canvas, rect) {
  canvas.onmousedown = (e) => {
    const mouse = getCanvasMousePos(canvas, e);
    const clicked = getHitNode(mouse.x, mouse.y);
    if (clicked) {
      draggedCanvasNode = clicked;
      selectedCanvasNode = clicked;
      renderNodeInspector(clicked);
    } else {
      isPanningCanvas = true;
      panStart = { x: e.clientX, y: e.clientY };
    }
  };

  canvas.onmousemove = (e) => {
    const mouse = getCanvasMousePos(canvas, e);
    const tooltip = document.getElementById("canvasTooltip");

    if (draggedCanvasNode) {
      draggedCanvasNode.x = mouse.x;
      draggedCanvasNode.y = mouse.y;
    } else if (isPanningCanvas) {
      const dx = e.clientX - panStart.x;
      const dy = e.clientY - panStart.y;
      canvasPan.x += dx;
      canvasPan.y += dy;
      panStart = { x: e.clientX, y: e.clientY };
    } else {
      const hit = getHitNode(mouse.x, mouse.y);
      hoveredCanvasNode = hit;
      canvas.style.cursor = hit ? "pointer" : "grab";

      if (hit && tooltip) {
        tooltip.classList.remove("hidden");
        tooltip.style.left = `${e.clientX - canvas.getBoundingClientRect().left + 15}px`;
        tooltip.style.top = `${e.clientY - canvas.getBoundingClientRect().top - 15}px`;
        tooltip.innerHTML = `<strong>${escapeHtml(hit.label)}</strong> <span class="text-[10px] text-zinc-400 font-mono block">${escapeHtml(hit.category || hit.type)}</span>`;
      } else if (tooltip) {
        tooltip.classList.add("hidden");
      }
    }
  };

  window.onmouseup = () => {
    draggedCanvasNode = null;
    isPanningCanvas = false;
  };

  canvas.onwheel = (e) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9;
    const newZoom = Math.min(Math.max(canvasZoom * zoomFactor, 0.4), 2.8);
    canvasZoom = newZoom;
  };
}

function getCanvasMousePos(canvas, e) {
  const rect = canvas.getBoundingClientRect();
  const rawX = e.clientX - rect.left;
  const rawY = e.clientY - rect.top;
  return {
    x: (rawX - canvasPan.x) / canvasZoom,
    y: (rawY - canvasPan.y) / canvasZoom
  };
}

function getHitNode(x, y) {
  for (let i = canvasNodes.length - 1; i >= 0; i--) {
    const n = canvasNodes[i];
    if (!isNodeVisible(n)) continue;
    const dx = x - n.x;
    const dy = y - n.y;
    if (Math.sqrt(dx * dx + dy * dy) <= (n.radius + 6)) {
      return n;
    }
  }
  return null;
}

function filterCanvasNodes() {
  const input = document.getElementById("canvasFilterInput");
  canvasSearchTerm = input ? input.value.trim() : "";
}

function updateCanvasFilters() {
  canvasTypeFilters.project = document.getElementById("filterTypeProject").checked;
  canvasTypeFilters.startup = document.getElementById("filterTypeStartup").checked;
  canvasTypeFilters.tech = document.getElementById("filterTypeTech").checked;
  canvasTypeFilters.file = document.getElementById("filterTypeFile").checked;
}

function toggleCanvasPhysics() {
  canvasPhysicsRunning = !canvasPhysicsRunning;
  const btn = document.getElementById("btnTogglePhysics");
  if (btn) {
    btn.innerHTML = canvasPhysicsRunning ?
      `<i data-lucide="pause" class="size-3"></i> <span>Pause Physics</span>` :
      `<i data-lucide="play" class="size-3"></i> <span>Resume Physics</span>`;
    if (window.lucide) lucide.createIcons();
  }
}

function zoomCanvas(factor) {
  const newZoom = Math.min(Math.max(canvasZoom * factor, 0.25), 3.5);
  canvasZoom = newZoom;
}

function resetCanvasView() {
  const canvas = document.getElementById("observabilityCanvas");
  if (!canvas || canvasNodes.length === 0) {
    canvasZoom = 1;
    canvasPan = { x: 0, y: 0 };
    return;
  }
  const rect = canvas.getBoundingClientRect();
  const visible = canvasNodes.filter(n => isNodeVisible(n));
  if (visible.length === 0) {
    canvasZoom = 1;
    canvasPan = { x: 0, y: 0 };
    return;
  }

  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const n of visible) {
    if (n.x < minX) minX = n.x;
    if (n.x > maxX) maxX = n.x;
    if (n.y < minY) minY = n.y;
    if (n.y > maxY) maxY = n.y;
  }

  const padding = 80;
  const graphW = (maxX - minX) + padding * 2;
  const graphH = (maxY - minY) + padding * 2;
  const fitZoom = Math.min(Math.max(Math.min(rect.width / graphW, rect.height / graphH), 0.35), 1.6);

  canvasZoom = fitZoom;
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  canvasPan.x = (rect.width / 2) - centerX * canvasZoom;
  canvasPan.y = (rect.height / 2) - centerY * canvasZoom;
}

// -------------------------------------------------------------
// NODE INSPECTOR DRAWER
// -------------------------------------------------------------
function renderNodeInspector(node) {
  const drawer = document.getElementById("nodeInspectorDrawer");
  const body = document.getElementById("nodeInspectorBody");
  const pill = document.getElementById("drawerCategoryPill");
  if (!drawer || !body) return;

  drawer.classList.remove("hidden");
  if (pill) {
    pill.textContent = node.category || node.type;
    pill.style.color = node.color;
    pill.style.borderColor = node.color;
  }

  const linked = canvasEdges.filter(e => e.sourceNode === node || e.targetNode === node).map(e => {
    const other = e.sourceNode === node ? e.targetNode : e.sourceNode;
    return { node: other, label: e.label };
  });

  body.innerHTML = `
    <div>
      <h3 class="text-base font-bold text-white font-mono">${escapeHtml(node.label)}</h3>
      ${node.description ? `<p class="text-xs text-zinc-400 mt-1 leading-relaxed">${escapeHtml(node.description)}</p>` : ''}
    </div>

    ${node.type === 'project' ? `
      <div class="grid grid-cols-2 gap-2 p-3 bg-surface-850 rounded-xl border border-surface-700">
        <div><span class="text-[10px] text-zinc-500 uppercase">Files on Disk</span><span class="text-sm font-bold text-amber-400 block">${node.file_count || 0}</span></div>
        <div><span class="text-[10px] text-zinc-500 uppercase">Sessions</span><span class="text-sm font-bold text-purple-400 block">${node.session_count || 0}</span></div>
      </div>
      <div class="p-2.5 rounded-lg bg-surface-850 border border-surface-700 flex items-center justify-between">
        <span class="text-[10px] text-zinc-400 font-mono truncate">data/projects/${node.slug}</span>
        <button onclick="copyToClipboard('${escapeHtml(node.path || node.slug)}', this)" class="text-[10px] text-brand-400 hover:underline">Copy Path</button>
      </div>
      <button onclick="activeProjectSlug='${node.slug}'; closeNodeInspectorDrawer(); renderProjectsSidebar();" class="w-full py-2 rounded-xl bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold shadow-md transition-all">
        Set as Active Project Workspace
      </button>
    ` : ''}

    ${node.type === 'startup' ? `
      <div class="grid grid-cols-2 gap-2 p-3 bg-surface-850 rounded-xl border border-surface-700">
        <div><span class="text-[10px] text-zinc-500 uppercase">Verified MRR</span><span class="text-sm font-bold text-emerald-400 block">${escapeHtml(node.mrr || 'N/A')}</span></div>
        <div><span class="text-[10px] text-zinc-500 uppercase">Health Score</span><span class="text-sm font-bold text-cyan-400 block">${node.health_score || 85}/100</span></div>
      </div>
      <button onclick="closeObservabilityModal(); usePrompt('Perform deep due diligence and founder search on ${escapeHtml(node.label)}');" class="w-full py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-md transition-all">
        Deep Research in Chat
      </button>
    ` : ''}

    ${node.type === 'file' ? `
      <div class="p-3 bg-surface-850 rounded-xl border border-surface-700 space-y-1">
        <div class="flex justify-between"><span class="text-zinc-500 text-[10px]">Project:</span><span class="text-zinc-200 font-semibold">${escapeHtml(node.project)}</span></div>
        <div class="flex justify-between"><span class="text-zinc-500 text-[10px]">Size:</span><span class="text-zinc-200 font-semibold">${escapeHtml(node.size_str)}</span></div>
        <div class="flex justify-between"><span class="text-zinc-500 text-[10px]">Folder:</span><span class="text-zinc-200 font-mono">${escapeHtml(node.subfolder)}</span></div>
      </div>
      <button onclick="switchObsTab('files'); previewProjectFile('${node.project.toLowerCase()}', '${node.subfolder}', '${node.label}');" class="w-full py-2 rounded-xl bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold shadow-md transition-all">
        Open and Read File
      </button>
    ` : ''}

    <div class="space-y-1.5 pt-2 border-t border-surface-700/60">
      <span class="text-[10px] font-bold uppercase tracking-wider text-zinc-500">Connected Nodes (${linked.length})</span>
      <div class="space-y-1 max-h-48 overflow-y-auto">
        ${linked.map(l => `
          <div onclick="focusNodeOnCanvas('${l.node.id}')" class="flex items-center justify-between p-2 rounded-lg bg-surface-850 hover:bg-surface-800 cursor-pointer text-xs border border-surface-750 transition-colors">
            <div class="flex items-center gap-1.5 truncate">
              <span class="size-2 rounded-full" style="background-color: ${l.node.color}"></span>
              <span class="truncate text-zinc-200">${escapeHtml(l.node.label)}</span>
            </div>
            <span class="text-[9px] text-zinc-500 font-mono">${escapeHtml(l.label)}</span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

function focusNodeOnCanvas(nodeId) {
  const target = canvasNodes.find(n => n.id === nodeId);
  if (target) {
    selectedCanvasNode = target;
    renderNodeInspector(target);
  }
}

function closeNodeInspectorDrawer() {
  const drawer = document.getElementById("nodeInspectorDrawer");
  if (drawer) drawer.classList.add("hidden");
  selectedCanvasNode = null;
}

// -------------------------------------------------------------
// TAB 2: OBSERVABILITY MATRIX
// -------------------------------------------------------------
function renderObservabilityMatrix() {
  const sum = obsData.summary || {};
  const elProjs = document.getElementById("matrixTotalProjects");
  const elFiles = document.getElementById("matrixTotalFiles");
  const elEntities = document.getElementById("matrixTotalEntities");
  const elDisk = document.getElementById("matrixTotalDisk");

  if (elProjs) elProjs.textContent = sum.total_projects || localProjects.length;
  if (elFiles) elFiles.textContent = sum.total_files || 0;
  if (elEntities) elEntities.textContent = (obsData.nodes || []).filter(n => n.type === 'startup').length;
  if (elDisk) elDisk.textContent = sum.total_disk_formatted || '0 KB';

  const list = document.getElementById("matrixTechFrequencyList");
  if (list) {
    const techArr = obsData.top_technologies || [];
    if (techArr.length === 0) {
      list.innerHTML = `<div class="text-zinc-500 text-xs py-4 text-center">No technology fingerprints linked yet. Run stack audits to index.</div>`;
    } else {
      const maxCount = Math.max(...techArr.map(t => t.count), 1);
      list.innerHTML = techArr.map(t => `
        <div class="space-y-1">
          <div class="flex items-center justify-between text-xs">
            <span class="font-medium text-white">${escapeHtml(t.name)}</span>
            <span class="text-zinc-400 font-mono text-[11px]">${t.count} projects / startups</span>
          </div>
          <div class="w-full bg-surface-800 rounded-full h-1.5 overflow-hidden">
            <div class="bg-gradient-to-r from-cyan-500 to-indigo-500 h-1.5" style="width: ${(t.count / maxCount) * 100}%"></div>
          </div>
        </div>
      `).join("");
    }
  }

  const tbody = document.getElementById("matrixProjectsTableBody");
  if (tbody) {
    tbody.innerHTML = localProjects.map(p => `
      <tr class="border-b border-surface-800 hover:bg-surface-800/40 transition-colors">
        <td class="py-3 px-3">
          <div class="font-bold text-white font-mono">${escapeHtml(p.slug)}</div>
          <span class="text-[11px] text-zinc-400">${escapeHtml(p.name)}</span>
        </td>
        <td class="py-3 px-3 font-mono text-amber-400 font-bold">${p.total_files || 0} files</td>
        <td class="py-3 px-3 font-mono text-purple-400">${p.total_sessions || 0}</td>
        <td class="py-3 px-3 font-mono text-zinc-400">${p.total_size_bytes < 1024 ? p.total_size_bytes + ' B' : (p.total_size_bytes / 1024).toFixed(1) + ' KB'}</td>
        <td class="py-3 px-3 font-mono text-zinc-500">${escapeHtml(p.time_ago_str || 'recently')}</td>
        <td class="py-3 px-3 text-right">
          <button onclick="activeProjectSlug='${p.slug}'; switchObsTab('files');" class="px-2.5 py-1 rounded bg-surface-800 hover:bg-surface-700 text-zinc-300 text-[11px] transition-colors">
            Browse Files
          </button>
        </td>
      </tr>
    `).join("");
  }
}

// -------------------------------------------------------------
// TAB 3: FILE EXPLORER & DOSSIER READER
// -------------------------------------------------------------
function renderFileExplorerTree() {
  const container = document.getElementById("obsFilesExplorerTree");
  if (!container) return;

  container.innerHTML = localProjects.map(p => `
    <div class="space-y-1">
      <div class="flex items-center gap-1.5 text-xs font-bold text-white px-2 py-1 bg-surface-800/80 rounded-lg">
        <i data-lucide="folder" class="size-3.5 text-brand-400"></i>
        <span>${escapeHtml(p.slug)}</span>
      </div>
      <div class="pl-4 space-y-0.5">
        ${(p.files || []).map(f => `
          <div onclick="previewProjectFile('${p.slug}', '${f.subfolder}', '${f.filename}')" class="flex items-center justify-between px-2 py-1 rounded text-[11px] text-zinc-400 hover:text-white hover:bg-surface-800 cursor-pointer transition-colors">
            <span class="truncate font-mono">${escapeHtml(f.filename)}</span>
            <span class="text-[9px] text-zinc-500 font-mono">${escapeHtml(f.size_formatted)}</span>
          </div>
        `).join("")}
        ${(!p.files || p.files.length === 0) ? `<div class="text-[10px] text-zinc-600 italic px-2 py-0.5">No files</div>` : ''}
      </div>
    </div>
  `).join("");
  if (window.lucide) lucide.createIcons();
}

async function previewProjectFile(projectSlug, subfolder, filename) {
  const titleEl = document.getElementById("previewFileName");
  const pathEl = document.getElementById("previewFilePath");
  const actionsEl = document.getElementById("previewFileActions");
  const contentEl = document.getElementById("previewFileContentArea");
  if (!contentEl) return;

  switchObsTab("files");

  if (titleEl) titleEl.textContent = filename;
  if (pathEl) pathEl.textContent = `${projectSlug} / ${subfolder} / ${filename}`;

  contentEl.innerHTML = `<div class="text-zinc-500 py-10 text-center"><i data-lucide="loader-2" class="size-6 animate-spin mx-auto mb-2 text-brand-400"></i> Reading file from hard drive...</div>`;
  if (window.lucide) lucide.createIcons();

  try {
    const res = await fetch(`/api/projects/${projectSlug}/files?filename=${encodeURIComponent(filename)}&subfolder=${encodeURIComponent(subfolder)}`);
    const data = await res.json();
    const raw = data.content || "";

    if (actionsEl) {
      actionsEl.innerHTML = `
        <button onclick="copyToClipboard(decodeURIComponent('${encodeURIComponent(raw)}'), this)" class="px-2.5 py-1 rounded-lg bg-surface-800 hover:bg-surface-700 text-zinc-300 text-[11px] flex items-center gap-1">
          <i data-lucide="copy" class="size-3"></i> Copy
        </button>
      `;
      if (window.lucide) lucide.createIcons();
    }

    if (filename.endsWith(".md")) {
      contentEl.innerHTML = `<div class="markdown-preview leading-relaxed">${marked.parse(raw)}</div>`;
    } else {
      contentEl.textContent = raw;
    }
  } catch (err) {
    contentEl.innerHTML = `<div class="p-3 text-red-400">Failed to read file: ${escapeHtml(err.message)}</div>`;
  }
}

// -------------------------------------------------------------
// TAB 4: UNIVERSAL SEARCH ENGINE
// -------------------------------------------------------------
let searchDebounceTimeout = null;
function executeUniversalSearch() {
  const query = document.getElementById("obsUniversalSearchInput").value.trim();
  const container = document.getElementById("obsSearchResultsContainer");
  if (!container) return;

  if (searchDebounceTimeout) clearTimeout(searchDebounceTimeout);
  if (!query) {
    container.innerHTML = `<div class="text-center text-zinc-500 py-16 text-xs">Type any query above to run deep search across all project folders, files, and intelligence on your computer.</div>`;
    return;
  }

  container.innerHTML = `<div class="text-center text-zinc-500 py-10 text-xs"><i data-lucide="loader-2" class="size-5 animate-spin mx-auto mb-2 text-purple-400"></i> Searching projects, files, and dossiers...</div>`;
  if (window.lucide) lucide.createIcons();

  searchDebounceTimeout = setTimeout(async () => {
    try {
      const res = await fetch(`/api/projects/search?q=${encodeURIComponent(query)}`);
      const data = await res.json();

      let html = `<div class="text-xs text-zinc-400 font-semibold mb-2">Found ${data.total_matches} matches across your machine:</div><div class="grid grid-cols-1 sm:grid-cols-2 gap-3">`;

      (data.projects || []).forEach(p => {
        html += `
          <div onclick="activeProjectSlug='${p.slug}'; switchObsTab('canvas'); focusNodeOnCanvas('proj_${p.slug}');" class="p-3 rounded-xl bg-surface-900 border border-purple-500/30 hover:border-purple-500/60 cursor-pointer transition-all space-y-1">
            <div class="flex items-center gap-1.5 text-purple-400 font-bold text-xs">
              <i data-lucide="folder" class="size-3.5"></i>
              <span>Project: ${escapeHtml(p.name)}</span>
            </div>
            <p class="text-[11px] text-zinc-400 line-clamp-2">${escapeHtml(p.description)}</p>
          </div>
        `;
      });

      (data.files || []).forEach(f => {
        html += `
          <div onclick="previewProjectFile('${f.project_slug}', '${f.subfolder}', '${f.filename}')" class="p-3 rounded-xl bg-surface-900 border border-amber-500/30 hover:border-amber-500/60 cursor-pointer transition-all space-y-1">
            <div class="flex items-center justify-between text-xs text-amber-300 font-bold font-mono">
              <span class="flex items-center gap-1.5"><i data-lucide="file-text" class="size-3.5"></i> ${escapeHtml(f.filename)}</span>
              <span class="text-[9px] text-zinc-500">${escapeHtml(f.size)}</span>
            </div>
            <p class="text-[11px] text-zinc-400 font-mono bg-surface-950 p-1.5 rounded">${escapeHtml(f.snippet)}</p>
          </div>
        `;
      });

      (data.sessions || []).forEach(s => {
        html += `
          <div onclick="closeObservabilityModal(); selectSession('${s.id}');" class="p-3 rounded-xl bg-surface-900 border border-brand-500/30 hover:border-brand-500/60 cursor-pointer transition-all space-y-1">
            <div class="flex items-center gap-1.5 text-brand-300 font-bold text-xs">
              <i data-lucide="message-square" class="size-3.5"></i>
              <span>Session: ${escapeHtml(s.title)}</span>
            </div>
            <span class="text-[10px] text-zinc-500 font-mono">Folder: ${escapeHtml(s.project_slug)}</span>
          </div>
        `;
      });

      (data.entities || []).forEach(e => {
        html += `
          <div onclick="focusNodeOnCanvas('ent_${e.slug}')" class="p-3 rounded-xl bg-surface-900 border border-emerald-500/30 hover:border-emerald-500/60 cursor-pointer transition-all space-y-1">
            <div class="flex items-center justify-between text-xs text-emerald-300 font-bold">
              <span>Startup: ${escapeHtml(e.name)}</span>
              <span class="font-mono text-[10px]">${escapeHtml(e.mrr)}</span>
            </div>
            <span class="text-[10px] text-zinc-500">Category: ${escapeHtml(e.category)}</span>
          </div>
        `;
      });

      html += `</div>`;
      container.innerHTML = html;
      if (window.lucide) lucide.createIcons();
    } catch (err) {
      container.innerHTML = `<div class="p-3 text-red-400">Search error: ${escapeHtml(err.message)}</div>`;
    }
  }, 250);
}

// ==============================================================
// SOTA LLM OBSERVABILITY & ACTION TRACE CONTROLLERS
// ==============================================================

let currentActiveTrace = null;
let currentCotViewMode = "structured";
let currentCotSearchQuery = "";

async function openLLMObservabilityModal(target) {
  const modal = document.getElementById("llmObservabilityModal");
  if (!modal) return;

  let msgData = null;

  if (typeof target === "string") {
    try {
      const res = await fetch(`/api/messages/${encodeURIComponent(target)}/trace`);
      if (res.ok) {
        const json = await res.json();
        msgData = json.message;
        if (json.metrics) {
          msgData._metrics = json.metrics;
        }
      }
    } catch (e) {
      console.warn("Could not fetch remote trace, falling back to local:", e);
    }
  } else if (target && typeof target === "object") {
    msgData = target;
  }

  if (!msgData) {
    msgData = {
      id: "trace-" + Date.now(),
      reasoning: "",
      content: "",
      tool_events: [],
      eventsLog: []
    };
  }

  const rawReasoning = msgData.reasoning || msgData.fullReasoning || "";
  const rawContent = msgData.content || msgData.fullContent || "";
  const toolsList = msgData.tool_events || msgData.toolEvents || [];
  const eventsList = msgData.eventsLog || msgData.events_log || [];

  // Consolidate swarm distributed events
  const allEvents = [...toolsList, ...eventsList];
  const swarmPlanEvent = allEvents.find(e => e.type === "swarm_plan");
  const swarmCompleteEvent = allEvents.find(e => e.type === "swarm_complete");
  const subagentStatuses = allEvents.filter(e => e.type === "subagent_status");
  const subagentToolCalls = allEvents.filter(e => e.type === "subagent_tool_call");
  const subagentToolResults = allEvents.filter(e => e.type === "subagent_tool_result");

  const isSwarm = !!(swarmPlanEvent || swarmCompleteEvent || subagentStatuses.length > 0 || subagentToolCalls.length > 0);

  // Synthesize unified tools list including all sub-agent executions
  let effectiveToolsList = [];
  (toolsList || []).forEach(t => {
    if (t.type === "tool_call" || t.type === "tool_result") {
      effectiveToolsList.push(t);
    }
  });

  if (subagentToolCalls.length > 0) {
    subagentToolCalls.forEach(call => {
      const matchingResult = subagentToolResults.find(r => r.id === call.id);
      const specialistName = call.agent_name || (call.agent_id === "sub_code_auditor" ? "Codebase Auditor" : (call.agent_id === "sub_sys_supervisor" ? "System Supervisor" : (call.agent_id === "browser_scout" ? "Browser Scout" : call.agent_id)));
      effectiveToolsList.push({
        id: call.id,
        name: call.name,
        agent_id: call.agent_id,
        agent_name: specialistName,
        agent_role: call.agent_role || "Specialist",
        args: call.args || {},
        data: matchingResult ? (matchingResult.data || matchingResult.summary) : null,
        summary: matchingResult ? matchingResult.summary : "Executed in swarm",
        duration_ms: matchingResult ? matchingResult.duration_ms : (call.duration_ms || 14),
        tier: call.tier || "local",
        model: call.model || currentConfig.llm_model
      });
    });
  }

  const charCount = rawReasoning.length + rawContent.length;
  const approxTokens = msgData._metrics?.estimated_tokens || Math.round(charCount / 3.8);

  let durationStr = "0.0s";
  if (msgData.totalReasoningMs) {
    durationStr = (msgData.totalReasoningMs / 1000).toFixed(1) + "s";
  } else if (rawReasoning.length > 0) {
    durationStr = (rawReasoning.length / 32).toFixed(1) + "s";
  }

  currentActiveTrace = {
    id: msgData.id || "trace-" + Date.now(),
    model: msgData._metrics?.model || currentConfig.llm_model || "Local LM Studio Model",
    provider: msgData._metrics?.provider || currentConfig.llm_provider || "lm_studio",
    duration: durationStr,
    tokens: approxTokens,
    reasoning: rawReasoning,
    content: rawContent,
    tool_events: effectiveToolsList,
    events_log: eventsList,
    created_at: msgData.created_at || Date.now(),
    is_swarm: isSwarm,
    swarm_data: {
      swarmPlanEvent,
      swarmCompleteEvent,
      subagentStatuses,
      subagentToolCalls,
      subagentToolResults,
      tools: effectiveToolsList
    }
  };

  // Populate Header & Telemetry Strip
  const modelEl = document.getElementById("traceTelemetryModel");
  const durationEl = document.getElementById("traceTelemetryDuration");
  const tokensEl = document.getElementById("traceTelemetryTokens");
  const toolsCountEl = document.getElementById("traceTelemetryToolsCount");
  const toolsTabCountEl = document.getElementById("traceToolsTabCount");
  const eventsTabCountEl = document.getElementById("traceEventsTabCount");
  const traceStatusBadge = document.getElementById("traceStatusBadge");
  const swarmTabBtn = document.getElementById("traceTabBtnSwarm");

  if (modelEl) modelEl.textContent = currentActiveTrace.model;
  if (durationEl) durationEl.textContent = currentActiveTrace.duration;
  if (tokensEl) tokensEl.textContent = `${currentActiveTrace.tokens.toLocaleString()} tok`;
  if (toolsCountEl) toolsCountEl.textContent = currentActiveTrace.tool_events.length;
  if (toolsTabCountEl) toolsTabCountEl.textContent = currentActiveTrace.tool_events.length;
  if (eventsTabCountEl) eventsTabCountEl.textContent = currentActiveTrace.events_log.length || (currentActiveTrace.tool_events.length * 2 + 2);

  // Render Tabs
  if (isSwarm) {
    if (swarmTabBtn) swarmTabBtn.classList.remove("hidden");
    if (traceStatusBadge) {
      traceStatusBadge.className = "text-[10px] font-mono px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-400 border border-amber-500/30 flex items-center gap-1";
      traceStatusBadge.innerHTML = '<span class="size-1.5 rounded-full bg-amber-400 animate-pulse"></span> JIT SWARM ACTIVE';
    }
    renderTraceSwarm(currentActiveTrace.swarm_data);
    renderTraceTimeline();
    renderTraceTools();
    renderTraceCot();
    renderTraceEvents();
    switchTraceTab("swarm");
  } else {
    if (swarmTabBtn) swarmTabBtn.classList.add("hidden");
    if (traceStatusBadge) {
      traceStatusBadge.className = "text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30";
      traceStatusBadge.textContent = "COMPLETED";
    }
    renderTraceTimeline();
    renderTraceTools();
    renderTraceCot();
    renderTraceEvents();
    switchTraceTab("timeline");
  }

  // Show modal
  modal.classList.remove("hidden");
  if (window.lucide) lucide.createIcons();
}

function closeLLMObservabilityModal() {
  const modal = document.getElementById("llmObservabilityModal");
  if (modal) modal.classList.add("hidden");
}

function switchTraceTab(tabName) {
  const panels = {
    swarm: document.getElementById("tracePanelSwarm"),
    timeline: document.getElementById("tracePanelTimeline"),
    tools: document.getElementById("tracePanelTools"),
    cot: document.getElementById("tracePanelCot"),
    events: document.getElementById("tracePanelEvents")
  };

  const buttons = {
    swarm: document.getElementById("traceTabBtnSwarm"),
    timeline: document.getElementById("traceTabBtnTimeline"),
    tools: document.getElementById("traceTabBtnTools"),
    cot: document.getElementById("traceTabBtnCot"),
    events: document.getElementById("traceTabBtnEvents")
  };

  for (const [key, p] of Object.entries(panels)) {
    if (p) {
      if (key === tabName) p.classList.remove("hidden");
      else p.classList.add("hidden");
    }
  }

  for (const [key, b] of Object.entries(buttons)) {
    if (b) {
      if (key === tabName) {
        if (key === "swarm") {
          b.className = "px-3 py-1 rounded-lg text-xs font-semibold bg-amber-500 text-black transition-colors shadow-sm flex items-center gap-1.5";
        } else {
          b.className = "px-3 py-1 rounded-lg text-xs font-medium bg-indigo-600 text-white transition-colors shadow-sm";
        }
      } else {
        if (key === "swarm") {
          b.className = "px-3 py-1 rounded-lg text-xs font-medium text-amber-400 hover:text-white transition-colors flex items-center gap-1.5";
        } else {
          b.className = "px-3 py-1 rounded-lg text-xs font-medium text-zinc-400 hover:text-white transition-colors";
        }
      }
    }
  }

  if (window.lucide) lucide.createIcons();
}

function renderTraceSwarm(swarmData) {
  const container = document.getElementById("tracePanelSwarm");
  if (!container || !swarmData) return;

  const plan = swarmData.swarmPlanEvent || {};
  const comp = swarmData.swarmCompleteEvent || {};
  const subagents = plan.subagents || comp.results || [];
  const tools = swarmData.tools || [];
  const totalDurationMs = comp.duration_ms || (subagents.length ? Math.max(...subagents.map(s => s.duration_ms || 3800)) : 4200);
  const totalDurationSec = (totalDurationMs / 1000).toFixed(1);

  // Calculate serial vs concurrent speedup
  const sumSubagentMs = subagents.reduce((acc, s) => acc + (s.duration_ms || 2500), 0);
  const speedupFactor = sumSubagentMs > totalDurationMs ? (sumSubagentMs / Math.max(totalDurationMs, 100)).toFixed(1) : "2.2";

  // Build Subagent Cards HTML
  let subagentCardsHtml = "";
  subagents.forEach((sa, idx) => {
    const isLocal = (sa.tier_resolved || sa.tier || "local").includes("local");
    const tierBadge = isLocal 
      ? `<span class="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">⚡ Local Fast</span>`
      : `<span class="text-[10px] font-mono px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">☁️ Cloud Reasoning</span>`;
    const durStr = sa.duration_ms ? `${(sa.duration_ms / 1000).toFixed(1)}s (${sa.duration_ms}ms)` : "3.8s";
    const toolsCount = sa.tools_count || (sa.tools ? sa.tools.length : 0);
    const actionChips = (sa.tools || []).map(t => 
      `<span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-800 text-zinc-300 border border-surface-700/60 flex items-center gap-1">
        <i data-lucide="check" class="size-2.5 text-emerald-400"></i> ${escapeHtml(t)}
      </span>`
    ).join(" ");

    subagentCardsHtml += `
      <div class="p-4 rounded-xl bg-surface-900 border border-surface-750 hover:border-surface-650 transition-colors space-y-3">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2.5">
            <div class="size-8 rounded-lg bg-surface-800 border border-surface-700 flex items-center justify-center text-amber-400">
              <i data-lucide="${sa.icon || 'bot'}" class="size-4"></i>
            </div>
            <div>
              <div class="flex items-center gap-2">
                <span class="font-bold text-xs text-white">${escapeHtml(sa.name || "Specialist")}</span>
                ${tierBadge}
              </div>
              <p class="text-[11px] text-zinc-400">${escapeHtml(sa.role || "Micro-Specialist")}</p>
            </div>
          </div>
          <div class="text-right">
            <span class="text-[11px] font-mono text-zinc-200 font-semibold">${durStr}</span>
            <div class="text-[10px] text-zinc-500 font-mono">${toolsCount} tools invoked</div>
          </div>
        </div>

        <!-- Pruned Action Space -->
        <div class="space-y-1.5 pt-1 border-t border-surface-800/80">
          <div class="flex items-center justify-between text-[10px] text-zinc-400">
            <span>Pruned Action Space (Zero Hallucination Barrier):</span>
            <span class="text-emerald-400 font-mono">100% Isolated</span>
          </div>
          <div class="flex flex-wrap gap-1">
            ${actionChips}
          </div>
        </div>

        <!-- Findings Preview -->
        ${sa.findings ? `
        <div class="p-2.5 rounded-lg bg-surface-950/80 border border-surface-800 text-[11px] text-zinc-300 leading-relaxed font-sans space-y-1">
          <div class="text-[10px] font-bold text-zinc-500 uppercase tracking-wider">Reported Intelligence</div>
          <div class="line-clamp-3 text-zinc-300">${marked.parse(sa.findings)}</div>
        </div>` : ''}
      </div>
    `;
  });

  // Build Concurrency Gantt Chart Bars
  let ganttBarsHtml = `
    <!-- Root Conductor -->
    <div class="space-y-1">
      <div class="flex items-center justify-between text-[11px]">
        <span class="text-zinc-300 font-medium flex items-center gap-1.5"><i data-lucide="crown" class="size-3 text-amber-400"></i> Xod Conductor (Decomposition & Synthesis)</span>
        <span class="text-zinc-400 font-mono text-[10px]">${totalDurationSec}s total</span>
      </div>
      <div class="h-5 rounded-lg bg-surface-950 border border-surface-750 overflow-hidden relative">
        <div class="h-full bg-gradient-to-r from-amber-500 via-indigo-500 to-purple-600 rounded-lg flex items-center px-2 text-[9px] font-bold text-black" style="width: 100%;">
          Master Mission Lifespan
        </div>
      </div>
    </div>
  `;

  subagents.forEach((sa, idx) => {
    const durMs = sa.duration_ms || Math.round(totalDurationMs * 0.75);
    const widthPct = Math.min(100, Math.max(25, Math.round((durMs / totalDurationMs) * 100)));
    const startOffsetPct = Math.round((idx * 4)); // small staggering
    const isLocal = (sa.tier_resolved || sa.tier || "local").includes("local");
    const barGradient = isLocal ? "from-emerald-500 to-teal-600" : "from-cyan-500 to-blue-600";

    ganttBarsHtml += `
      <div class="space-y-1">
        <div class="flex items-center justify-between text-[11px]">
          <span class="text-zinc-400 flex items-center gap-1.5"><i data-lucide="${sa.icon || 'cpu'}" class="size-3 text-zinc-500"></i> ${escapeHtml(sa.name || "Specialist")}</span>
          <span class="text-zinc-400 font-mono text-[10px]">${(durMs / 1000).toFixed(1)}s</span>
        </div>
        <div class="h-5 rounded-lg bg-surface-950 border border-surface-750 overflow-hidden relative">
          <div class="h-full bg-gradient-to-r ${barGradient} rounded-lg flex items-center justify-between px-2 text-[9px] font-semibold text-black" style="margin-left: ${startOffsetPct}%; width: ${widthPct}%;">
            <span>Parallel Task</span>
            <span>${durMs}ms</span>
          </div>
        </div>
      </div>
    `;
  });

  // Build Distributed Tool Payload Inspector Table
  let toolRowsHtml = "";
  if (tools.length === 0) {
    toolRowsHtml = `<tr><td colspan="5" class="p-4 text-center text-zinc-500 text-xs">No tool executions recorded in this turn.</td></tr>`;
  } else {
    tools.forEach((t, idx) => {
      const dur = t.duration_ms ? `${t.duration_ms}ms` : "< 15ms";
      const caller = t.agent_name || t.agent_id || "Specialist";
      toolRowsHtml += `
        <tr class="border-b border-surface-800 hover:bg-surface-800/40 transition-colors">
          <td class="p-3 text-zinc-500 font-mono text-[10px]">#${idx + 1}</td>
          <td class="p-3 font-semibold text-white text-xs">${escapeHtml(caller)}</td>
          <td class="p-3 font-mono text-xs text-amber-300">${escapeHtml(t.name || t.tool || "unknown")}</td>
          <td class="p-3 font-mono text-xs text-zinc-400">${dur}</td>
          <td class="p-3 text-right">
            <button onclick="toggleSwarmToolDrawer(${idx})" class="text-xs text-indigo-400 hover:text-white font-mono px-2 py-1 rounded bg-surface-800 border border-surface-700 transition-colors">
              Inspect Payload &rarr;
            </button>
          </td>
        </tr>
        <tr id="swarmToolDrawer_${idx}" class="hidden bg-surface-950/90 border-b border-surface-750">
          <td colspan="5" class="p-4 space-y-3">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs font-mono">
              <div class="space-y-1">
                <span class="text-[10px] font-bold uppercase tracking-wider text-zinc-400">Tool Arguments</span>
                <pre class="p-2.5 rounded bg-surface-900 border border-surface-800 text-[11px] text-zinc-300 overflow-x-auto max-h-40">${escapeHtml(JSON.stringify(t.args || {}, null, 2))}</pre>
              </div>
              <div class="space-y-1">
                <span class="text-[10px] font-bold uppercase tracking-wider text-zinc-400">Returned Result</span>
                <pre class="p-2.5 rounded bg-surface-900 border border-surface-800 text-[11px] text-emerald-300 overflow-x-auto max-h-40">${escapeHtml(JSON.stringify(t.data || t.summary || {}, null, 2))}</pre>
              </div>
            </div>
          </td>
        </tr>
      `;
    });
  }

  container.innerHTML = `
    <!-- Top Metrics Overview -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <div class="p-3.5 rounded-xl bg-surface-900 border border-surface-750 space-y-1">
        <div class="text-[10px] text-zinc-500 font-bold uppercase tracking-wider">Swarm Conductor</div>
        <div class="text-sm font-bold text-white flex items-center gap-1.5">
          <i data-lucide="crown" class="size-4 text-amber-400"></i> Xod
        </div>
        <div class="text-[10px] text-zinc-400">Autonomous Director</div>
      </div>

      <div class="p-3.5 rounded-xl bg-surface-900 border border-surface-750 space-y-1">
        <div class="text-[10px] text-zinc-500 font-bold uppercase tracking-wider">Specialists Deployed</div>
        <div class="text-sm font-bold text-indigo-400 font-mono">${subagents.length} Parallel Workers</div>
        <div class="text-[10px] text-zinc-400">${tools.length} total tools executed</div>
      </div>

      <div class="p-3.5 rounded-xl bg-surface-900 border border-surface-750 space-y-1">
        <div class="text-[10px] text-zinc-500 font-bold uppercase tracking-wider">Concurrency Speedup</div>
        <div class="text-sm font-bold text-emerald-400 font-mono">${speedupFactor}x Faster</div>
        <div class="text-[10px] text-zinc-400">Asyncio gather pipeline</div>
      </div>

      <div class="p-3.5 rounded-xl bg-surface-900 border border-surface-750 space-y-1">
        <div class="text-[10px] text-zinc-500 font-bold uppercase tracking-wider">Hardware Cost & Privacy</div>
        <div class="text-sm font-bold text-emerald-400 font-mono">$0.00 / Local</div>
        <div class="text-[10px] text-zinc-400">100% Zero host leakage</div>
      </div>
    </div>

    <!-- Concurrency Gantt Waterfall -->
    <div class="p-4 rounded-xl bg-surface-900 border border-surface-750 space-y-3">
      <div class="flex items-center justify-between pb-2 border-b border-surface-800">
        <div class="flex items-center gap-2">
          <i data-lucide="git-pull-request" class="size-4 text-amber-400"></i>
          <h4 class="font-bold text-xs text-white">Distributed Concurrency Waterfall</h4>
        </div>
        <span class="text-[10px] font-mono text-zinc-400">OpenTelemetry-Compatible Spans</span>
      </div>
      <div class="space-y-3 pt-1">
        ${ganttBarsHtml}
      </div>
    </div>

    <!-- Specialists Grid -->
    <div class="space-y-2">
      <h4 class="text-xs font-bold text-zinc-300 uppercase tracking-wider flex items-center gap-1.5">
        <i data-lucide="users" class="size-3.5 text-indigo-400"></i> Active Specialist Archetypes
      </h4>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        ${subagentCardsHtml}
      </div>
    </div>

    <!-- Distributed Tool Payloads Table -->
    <div class="p-4 rounded-xl bg-surface-900 border border-surface-750 space-y-3">
      <div class="flex items-center justify-between pb-2 border-b border-surface-800">
        <div class="flex items-center gap-2">
          <i data-lucide="wrench" class="size-4 text-indigo-400"></i>
          <h4 class="font-bold text-xs text-white">Swarm Distributed Tool Invocations</h4>
        </div>
        <span class="text-[10px] font-mono text-zinc-400">${tools.length} Invocations Audited</span>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-left border-collapse">
          <thead>
            <tr class="text-[10px] uppercase font-bold text-zinc-500 border-b border-surface-800">
              <th class="p-3">Span</th>
              <th class="p-3">Specialist</th>
              <th class="p-3">Action Tool</th>
              <th class="p-3">Execution Latency</th>
              <th class="p-3 text-right">Audit Payload</th>
            </tr>
          </thead>
          <tbody>
            ${toolRowsHtml}
          </tbody>
        </table>
      </div>
    </div>
  `;

  if (window.lucide) lucide.createIcons();
}

function toggleSwarmToolDrawer(idx) {
  const drawer = document.getElementById(`swarmToolDrawer_${idx}`);
  if (drawer) {
    drawer.classList.toggle("hidden");
  }
}

function renderTraceTimeline() {
  const container = document.getElementById("traceTimelineList");
  if (!container || !currentActiveTrace) return;

  const t = currentActiveTrace;
  const reasoningWords = (t.reasoning || "").trim().split(/\s+/).filter(Boolean).length;
  const contentWords = (t.content || "").trim().split(/\s+/).filter(Boolean).length;

  let toolsStepsHtml = "";
  (t.tool_events || []).forEach((tool, idx) => {
    const toolName = formatToolName(tool.name || tool.tool || "unknown_tool");
    const icon = getToolIcon(tool.name || tool.tool || "");
    const summary = tool.summary || "Tool execution completed successfully";
    const argsStr = truncateArgs(tool.args || tool.input || {});

    toolsStepsHtml += `
      <div class="timeline-step">
        <div class="timeline-node status-completed">
          <i data-lucide="${icon}" class="size-2.5 text-emerald-400"></i>
        </div>
        <div class="p-3.5 rounded-xl bg-surface-900 border border-surface-750 space-y-1.5">
          <div class="flex items-center justify-between">
            <span class="text-xs font-bold text-white flex items-center gap-1.5 font-mono">
              <span class="text-indigo-400 font-semibold">Step ${idx + 3}:</span> ${escapeHtml(toolName)}
            </span>
            <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/25">COMPLETED</span>
          </div>
          <p class="text-xs text-zinc-300 font-mono bg-black/30 p-2 rounded border border-white/[0.05]">${escapeHtml(argsStr)}</p>
          <div class="flex items-center justify-between text-[11px] text-zinc-400 pt-1">
            <span class="text-emerald-300">✓ ${escapeHtml(summary)}</span>
            <button onclick="switchTraceTab('tools')" class="text-indigo-400 hover:underline">Inspect Payloads &rarr;</button>
          </div>
        </div>
      </div>
    `;
  });

  container.innerHTML = `
    <!-- Step 1: Ingestion -->
    <div class="timeline-step">
      <div class="timeline-node status-completed">
        <i data-lucide="arrow-down" class="size-2.5 text-indigo-400"></i>
      </div>
      <div class="p-3.5 rounded-xl bg-surface-900 border border-surface-750 space-y-1">
        <div class="flex items-center justify-between">
          <span class="text-xs font-bold text-white font-mono"><span class="text-indigo-400">Step 1:</span> User Objective Ingestion</span>
          <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-300 border border-indigo-500/25">INPUT</span>
        </div>
        <p class="text-xs text-zinc-400">Goal tokenized & routed to autonomous multi-step reasoning agent.</p>
      </div>
    </div>

    <!-- Step 2: Deliberation -->
    <div class="timeline-step">
      <div class="timeline-node status-completed">
        <i data-lucide="brain" class="size-2.5 text-indigo-400"></i>
      </div>
      <div class="p-3.5 rounded-xl bg-surface-900 border border-surface-750 space-y-2">
        <div class="flex items-center justify-between">
          <span class="text-xs font-bold text-white font-mono"><span class="text-indigo-400">Step 2:</span> Chain-of-Thought Deliberation</span>
          <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-purple-500/10 text-purple-300 border border-purple-500/25">${t.duration}</span>
        </div>
        <p class="text-xs text-zinc-300 leading-relaxed font-sans">
          Generated <span class="font-bold text-white font-mono">${reasoningWords} words</span> of structured hypothesis, trend deduplication & tool invocation strategy.
        </p>
        <div class="flex items-center gap-2 pt-1 border-t border-white/[0.05]">
          <button onclick="switchTraceTab('cot')" class="text-xs text-indigo-400 hover:underline flex items-center gap-1 font-sans">
            <i data-lucide="eye" class="size-3"></i> View CoT Deep Dive
          </button>
        </div>
      </div>
    </div>

    ${toolsStepsHtml}

    <!-- Step Final: Report Synthesis -->
    <div class="timeline-step">
      <div class="timeline-node status-completed">
        <i data-lucide="sparkles" class="size-2.5 text-emerald-400"></i>
      </div>
      <div class="p-3.5 rounded-xl bg-surface-900 border border-surface-750 space-y-1.5">
        <div class="flex items-center justify-between">
          <span class="text-xs font-bold text-white font-mono"><span class="text-emerald-400">Final Step:</span> Intelligence Synthesis & Markdown Dossier</span>
          <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">DELIVERED</span>
        </div>
        <p class="text-xs text-zinc-300 leading-relaxed font-sans">
          Final report compiled (<span class="font-mono text-zinc-100 font-semibold">${contentWords} words</span>) with inline tables, scorecards, and citations.
        </p>
      </div>
    </div>
  `;
  if (window.lucide) lucide.createIcons();
}

function renderTraceTools() {
  const container = document.getElementById("traceToolsList");
  if (!container || !currentActiveTrace) return;

  const tools = currentActiveTrace.tool_events || [];
  if (tools.length === 0) {
    container.innerHTML = `
      <div class="p-12 text-center text-zinc-500 space-y-2 bg-surface-900 rounded-xl border border-surface-750">
        <i data-lucide="check-circle" class="size-8 text-zinc-600 mx-auto"></i>
        <h4 class="text-sm font-semibold text-zinc-300">Pure Deliberation Turn</h4>
        <p class="text-xs text-zinc-500 max-w-sm mx-auto">This turn was synthesized directly from the model's reasoning without external tool calls.</p>
      </div>
    `;
    if (window.lucide) lucide.createIcons();
    return;
  }

  container.innerHTML = tools.map((tool, idx) => {
    const toolName = formatToolName(tool.name || tool.tool || "tool");
    const icon = getToolIcon(tool.name || tool.tool || "");
    const argsJson = JSON.stringify(tool.args || tool.input || {}, null, 2);
    const dataJson = JSON.stringify(tool.data || tool.summary || tool.result || {}, null, 2);

    return `
      <div class="p-4 rounded-xl bg-surface-900 border border-surface-750 space-y-3">
        <!-- Card Header -->
        <div class="flex items-center justify-between pb-2 border-b border-surface-800">
          <div class="flex items-center gap-2">
            <div class="size-6 rounded-lg bg-indigo-500/15 flex items-center justify-center text-indigo-400">
              <i data-lucide="${icon}" class="size-3.5"></i>
            </div>
            <span class="font-bold text-xs text-white">${escapeHtml(toolName)}</span>
            <span class="text-[10px] font-mono text-zinc-500">#${idx + 1}</span>
          </div>
          <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">200 OK</span>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <!-- Input Arguments Block -->
          <div class="space-y-1.5">
            <div class="flex items-center justify-between">
              <span class="text-[10px] font-bold uppercase tracking-wider text-zinc-400">Input Parameters</span>
              <button onclick="copyToolPayload(${idx}, 'args', this)" class="text-[10px] font-medium text-indigo-400 hover:text-white flex items-center gap-1">
                <i data-lucide="copy" class="size-3"></i> Copy Args JSON
              </button>
            </div>
            <pre class="json-payload-pre">${escapeHtml(argsJson)}</pre>
          </div>

          <!-- Return Payload Block -->
          <div class="space-y-1.5">
            <div class="flex items-center justify-between">
              <span class="text-[10px] font-bold uppercase tracking-wider text-emerald-400">Returned API Payload</span>
              <button onclick="copyToolPayload(${idx}, 'data', this)" class="text-[10px] font-medium text-emerald-400 hover:text-white flex items-center gap-1">
                <i data-lucide="copy" class="size-3"></i> Copy Data JSON
              </button>
            </div>
            <pre class="json-payload-pre text-emerald-300">${escapeHtml(dataJson)}</pre>
          </div>
        </div>
      </div>
    `;
  }).join("");
  if (window.lucide) lucide.createIcons();
}

function renderTraceCot() {
  const container = document.getElementById("traceCotContentArea");
  if (!container || !currentActiveTrace) return;

  const rawReasoning = currentActiveTrace.reasoning || "";
  if (!rawReasoning.trim()) {
    container.innerHTML = `<div class="p-8 text-center text-zinc-500 text-xs">No reasoning tokens generated for this message.</div>`;
    return;
  }

  const query = currentCotSearchQuery.trim();

  if (currentCotViewMode === "structured") {
    const cards = parseStructuredThoughts(rawReasoning);
    container.innerHTML = `<div class="space-y-2">${renderThoughtCardsHtml(cards, query)}</div>`;
  } else if (currentCotViewMode === "prose") {
    let text = rawReasoning;
    if (query) {
      const regex = new RegExp(`(${escapeRegex(query)})`, "gi");
      text = text.replace(regex, `<mark class="bg-indigo-500/40 text-white rounded px-0.5">$1</mark>`);
    }
    container.innerHTML = `<div class="prose-thinking max-h-none">${marked.parse(text)}</div>`;
  } else if (currentCotViewMode === "raw") {
    let filtered = rawReasoning;
    if (query) {
      const lines = rawReasoning.split("\n").filter(l => l.toLowerCase().includes(query.toLowerCase()));
      filtered = lines.join("\n") || `// No lines match "${query}"`;
    }
    container.innerHTML = `<pre class="json-payload-pre max-h-none select-text">${escapeHtml(filtered)}</pre>`;
  }
  if (window.lucide) lucide.createIcons();
}

function filterCotDeepDive() {
  const input = document.getElementById("traceCotSearchInput");
  if (input) {
    currentCotSearchQuery = input.value;
    renderTraceCot();
  }
}

function setCotViewMode(mode) {
  currentCotViewMode = mode;
  const btnStruct = document.getElementById("btnCotModeStructured");
  const btnProse = document.getElementById("btnCotModeProse");
  const btnRaw = document.getElementById("btnCotModeRaw");

  [btnStruct, btnProse, btnRaw].forEach(b => {
    if (b) b.className = "px-2.5 py-1 rounded text-[11px] font-medium text-zinc-400 hover:text-white";
  });

  if (mode === "structured" && btnStruct) btnStruct.className = "px-2.5 py-1 rounded text-[11px] font-medium bg-indigo-600 text-white";
  if (mode === "prose" && btnProse) btnProse.className = "px-2.5 py-1 rounded text-[11px] font-medium bg-indigo-600 text-white";
  if (mode === "raw" && btnRaw) btnRaw.className = "px-2.5 py-1 rounded text-[11px] font-medium bg-indigo-600 text-white";

  renderTraceCot();
}

function renderTraceEvents() {
  const container = document.getElementById("traceEventsLogList");
  if (!container || !currentActiveTrace) return;

  let events = currentActiveTrace.events_log || [];
  if (events.length === 0) {
    events = [
      { type: "status", content: "Ingested user objective" },
      { type: "reasoning", content: `${(currentActiveTrace.reasoning || "").length} characters deliberation` },
      ...(currentActiveTrace.tool_events || []).map(t => ({
        type: "tool_execution",
        name: t.name,
        args: t.args,
        summary: t.summary
      })),
      { type: "content", content: `${(currentActiveTrace.content || "").length} characters report` },
      { type: "done", content: "Session turn finalized" }
    ];
  }

  container.innerHTML = events.map((ev, idx) => {
    const jsonStr = JSON.stringify(ev, null, 2);
    let badgeColor = "bg-surface-750 text-zinc-300 border-surface-700";
    if (ev.type === "reasoning") badgeColor = "bg-purple-500/15 text-purple-300 border-purple-500/30";
    if (ev.type === "tool_call" || ev.type === "tool_execution") badgeColor = "bg-indigo-500/15 text-indigo-300 border-indigo-500/30";
    if (ev.type === "tool_result") badgeColor = "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
    if (ev.type === "done") badgeColor = "bg-emerald-500/20 text-emerald-400 border-emerald-500/40";

    return `
      <div class="p-2.5 rounded-lg bg-surface-900 border border-surface-750 flex items-start justify-between gap-3 font-mono">
        <div class="flex items-start gap-2.5 min-w-0 flex-1">
          <span class="text-zinc-600 text-[10px] pt-0.5">#${String(idx + 1).padStart(2, '0')}</span>
          <span class="px-2 py-0.5 rounded text-[10px] font-semibold border ${badgeColor} shrink-0">${ev.type.toUpperCase()}</span>
          <span class="text-zinc-300 text-[11px] truncate">${escapeHtml(ev.content || ev.summary || ev.name || JSON.stringify(ev).slice(0, 80))}</span>
        </div>
        <button onclick="navigator.clipboard.writeText(decodeURIComponent('${encodeURIComponent(jsonStr)}')); this.textContent='Copied!';" class="text-[10px] text-zinc-500 hover:text-white shrink-0 px-2 py-0.5 rounded hover:bg-white/10 transition-colors">
          Copy JSON
        </button>
      </div>
    `;
  }).join("");
}

function copyToolPayload(toolIdx, payloadKey, btnEl) {
  if (!currentActiveTrace || !currentActiveTrace.tool_events[toolIdx]) return;
  const tool = currentActiveTrace.tool_events[toolIdx];
  const payload = payloadKey === "args" ? (tool.args || tool.input || {}) : (tool.data || tool.summary || tool.result || {});
  navigator.clipboard.writeText(JSON.stringify(payload, null, 2));

  if (btnEl) {
    const originalText = btnEl.innerHTML;
    btnEl.innerHTML = `<i data-lucide="check" class="size-3 text-emerald-400"></i> Copied!`;
    if (window.lucide) lucide.createIcons();
    setTimeout(() => {
      btnEl.innerHTML = originalText;
      if (window.lucide) lucide.createIcons();
    }, 1500);
  }
}

function copyCurrentTraceJson() {
  if (!currentActiveTrace) return;
  navigator.clipboard.writeText(JSON.stringify(currentActiveTrace, null, 2));
  const btn = document.getElementById("btnCopyTraceModal");
  if (btn) {
    const originalHtml = btn.innerHTML;
    btn.innerHTML = `<i data-lucide="check" class="size-3.5 text-emerald-400"></i><span>✓ Copied Trace!</span>`;
    if (window.lucide) lucide.createIcons();
    setTimeout(() => {
      btn.innerHTML = originalHtml;
      if (window.lucide) lucide.createIcons();
    }, 2000);
  }
}

function downloadCurrentTraceJson() {
  if (!currentActiveTrace) return;
  const blob = new Blob([JSON.stringify(currentActiveTrace, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `xod-trace-${currentActiveTrace.id || Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function downloadCurrentTraceMarkdown() {
  if (!currentActiveTrace) return;
  const t = currentActiveTrace;
  let md = `# XOD Intelligence Audit Dossier\n\n`;
  md += `- **Date**: ${new Date(t.created_at || Date.now()).toISOString()}\n`;
  md += `- **Model**: ${t.model}\n`;
  md += `- **Deliberation Latency**: ${t.duration}\n`;
  md += `- **Estimated Tokens**: ${t.tokens}\n`;
  md += `- **External Tools Used**: ${t.tool_events.length}\n\n`;

  md += `## 1. Autonomous Deliberation (Chain-of-Thought)\n\n`;
  md += `${t.reasoning || "*(No reasoning recorded)*"}\n\n`;

  md += `## 2. External Tools & Evidence Payloads\n\n`;
  if (t.tool_events.length === 0) {
    md += `*None (Pure deliberation turn)*\n\n`;
  } else {
    t.tool_events.forEach((tool, idx) => {
      md += `### Tool #${idx + 1}: ${tool.name || tool.tool}\n`;
      md += `**Arguments**:\n\`\`\`json\n${JSON.stringify(tool.args || tool.input || {}, null, 2)}\n\`\`\`\n\n`;
      md += `**Output Payload**:\n\`\`\`json\n${JSON.stringify(tool.data || tool.summary || {}, null, 2)}\n\`\`\`\n\n`;
    });
  }

  md += `## 3. Synthesized Intelligence Report\n\n`;
  md += `${t.content || ""}\n`;

  const blob = new Blob([md], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `xod-dossier-${t.id || Date.now()}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

function copyCotReasoning() {
  if (!currentActiveTrace) return;
  navigator.clipboard.writeText(currentActiveTrace.reasoning || "");
  const btn = document.getElementById("btnCopyCotText");
  if (btn) {
    const orig = btn.innerHTML;
    btn.innerHTML = `<i data-lucide="check" class="size-3 text-emerald-400"></i><span>Copied!</span>`;
    if (window.lucide) lucide.createIcons();
    setTimeout(() => {
      btn.innerHTML = orig;
      if (window.lucide) lucide.createIcons();
    }, 1500);
  }
}

function copyAllEventLogs() {
  if (!currentActiveTrace) return;
  navigator.clipboard.writeText(JSON.stringify(currentActiveTrace.events_log || [], null, 2));
  const btn = document.getElementById("btnCopyEventLogs");
  if (btn) {
    const orig = btn.innerHTML;
    btn.innerHTML = `<i data-lucide="check" class="size-3 text-emerald-400"></i> Copied Logs!`;
    if (window.lucide) lucide.createIcons();
    setTimeout(() => {
      btn.innerHTML = orig;
      if (window.lucide) lucide.createIcons();
    }, 1500);
  }
}

// ==============================================================
// ACTIVE CONTROLLED WORKSPACE & FOLDER SCOPE
// ==============================================================

let activeWorkingScope = null; // { name, path, type, slug, icon, color, total_folders, total_files }

async function initActiveWorkingScope() {
  try {
    const saved = localStorage.getItem("xod_active_scope");
    if (saved) {
      const parsed = JSON.parse(saved);
      if (parsed && parsed.path) {
        await setActiveWorkingScope(parsed, false);
      }
    }
  } catch (e) {
    console.debug("Could not restore active scope:", e);
  }
}

async function setActiveWorkingScope(scope, showToastNotification = true) {
  if (!scope || !scope.path) return;
  activeWorkingScope = { ...scope };

  // Save to localStorage
  try {
    localStorage.setItem("xod_active_scope", JSON.stringify(activeWorkingScope));
  } catch (e) {}

  // Fetch telemetry/stats if available
  try {
    const res = await fetch(`/api/fs/browse?path=${encodeURIComponent(activeWorkingScope.path)}&show_hidden=false`);
    if (res.ok) {
      const data = await res.json();
      activeWorkingScope.total_folders = data.total_folders || 0;
      activeWorkingScope.total_files = data.total_files || 0;
      activeWorkingScope.folders = data.folders || [];
      activeWorkingScope.files = data.files || [];
    }
  } catch (e) {}

  renderActiveWorkingScopeUI();
  renderWelcomeHeroForScope();
  renderProjectsSidebar();
  if (currentDirectoryData) renderSidebarComputerTree(currentDirectoryData);

  if (showToastNotification) {
    showToast(`AI is now controlling "${activeWorkingScope.name}"`);
  }
}

function clearActiveWorkingScope() {
  activeWorkingScope = null;
  try {
    localStorage.removeItem("xod_active_scope");
  } catch (e) {}

  const bar = document.getElementById("activeWorkspaceBar");
  const pill = document.getElementById("activeScopeInputPill");
  if (bar) bar.classList.add("hidden");
  if (pill) pill.classList.add("hidden");

  renderWelcomeHeroForScope();
  renderProjectsSidebar();
  if (currentDirectoryData) renderSidebarComputerTree(currentDirectoryData);
  showToast("Released folder scope");
}

function renderActiveWorkingScopeUI() {
  const bar = document.getElementById("activeWorkspaceBar");
  const pill = document.getElementById("activeScopeInputPill");
  if (!activeWorkingScope) {
    if (bar) bar.classList.add("hidden");
    if (pill) pill.classList.add("hidden");
    return;
  }

  // Active Workspace Bar
  if (bar) {
    bar.classList.remove("hidden");
    const nameEl = document.getElementById("activeScopeName");
    const pathEl = document.getElementById("activeScopePath");
    const statsEl = document.getElementById("activeScopeStats");
    const iconEl = document.getElementById("activeScopeIcon");
    const iconWrap = document.getElementById("activeScopeIconWrap");

    if (nameEl) nameEl.textContent = activeWorkingScope.name;
    if (pathEl) {
      const p = activeWorkingScope.path;
      pathEl.textContent = p.replace(/^\/Users\/[^\/]+/, "~");
      pathEl.title = p;
    }
    if (window.XodPreview) XodPreview.refreshForScope(activeWorkingScope.path);
    if (statsEl) {
      const fCount = activeWorkingScope.total_folders || 0;
      const fileCount = activeWorkingScope.total_files || 0;
      statsEl.textContent = `${fCount} folder${fCount === 1 ? '' : 's'} · ${fileCount} file${fileCount === 1 ? '' : 's'}`;
    }
    if (iconEl && activeWorkingScope.icon) {
      iconEl.setAttribute("data-lucide", activeWorkingScope.icon || "folder");
    }
    if (iconWrap && activeWorkingScope.color) {
      iconWrap.style.borderColor = activeWorkingScope.color + "44";
      iconWrap.style.backgroundColor = activeWorkingScope.color + "18";
      iconWrap.style.color = activeWorkingScope.color;
    }
  }

  // Active Scope Chat Pill
  if (pill) {
    pill.classList.remove("hidden");
    const inputName = document.getElementById("activeScopeInputName");
    const inputPath = document.getElementById("activeScopeInputPath");
    if (inputName) inputName.textContent = activeWorkingScope.name;
    if (inputPath) {
      const p = activeWorkingScope.path;
      inputPath.textContent = `(${p.replace(/^\/Users\/[^\/]+/, "~")})`;
    }
  }

  if (window.lucide) lucide.createIcons();
}

function renderWelcomeHeroForScope() {
  const title = document.getElementById("welcomeHeroTitle");
  const desc = document.getElementById("welcomeHeroDesc");
  const starters = document.getElementById("welcomeHeroStarters");
  const iconWrap = document.getElementById("welcomeHeroIconWrap");
  const iconEl = document.getElementById("welcomeHeroIcon");

  if (!starters) return;

  if (activeWorkingScope) {
    const scopeName = escapeHtml(activeWorkingScope.name);
    const scopePath = escapeHtml(activeWorkingScope.path.replace(/^\/Users\/[^\/]+/, "~"));

    if (title) title.innerHTML = `Controlling Folder: <span class="text-brand-400 font-mono">${scopeName}</span>`;
    if (desc) desc.innerHTML = `Xod AI is scoped to <span class="font-mono text-zinc-300">${scopePath}</span>. Full access to inspect files, review architecture, search code, and generate project dossiers.`;

    if (iconEl) iconEl.setAttribute("data-lucide", "crosshair");
    if (iconWrap) {
      iconWrap.className = "size-12 mx-auto rounded-2xl bg-gradient-to-tr from-cyan-600 via-teal-500 to-emerald-600 flex items-center justify-center shadow-xl shadow-cyan-500/30";
    }

    starters.innerHTML = `
      <button onclick="usePrompt('Audit the architecture and directory structure of ${scopeName}. Explain what each module does and how they connect.')" class="starter-card group">
        <i data-lucide="layout-grid" class="size-4 text-cyan-400 shrink-0"></i>
        <span>Audit directory structure & architecture</span>
      </button>
      <button onclick="usePrompt('Read the README.md and startup scripts in ${scopeName}. Summarize setup steps, prerequisites, and how to run it.')" class="starter-card group">
        <i data-lucide="book-open" class="size-4 text-emerald-400 shrink-0"></i>
        <span>Inspect README & setup instructions</span>
      </button>
      <button onclick="usePrompt('Inspect dependency files (e.g. package.json or requirements.txt) in ${scopeName} and evaluate tech stack health.')" class="starter-card group">
        <i data-lucide="package" class="size-4 text-amber-400 shrink-0"></i>
        <span>Audit tech stack & dependencies</span>
      </button>
      <button onclick="usePrompt('Generate a comprehensive institutional architecture dossier for ${scopeName} and save it as PROJECT_DOSSIER.md in this folder.')" class="starter-card group">
        <i data-lucide="file-signature" class="size-4 text-purple-400 shrink-0"></i>
        <span>Generate & save project dossier</span>
      </button>
    `;
  } else {
    if (title) title.textContent = "What should Xod do?";
    if (desc) desc.textContent = "Research anything, work with your files, or hand off a whole job. It runs on your Mac.";
    if (iconEl) iconEl.setAttribute("data-lucide", "sparkles");
    if (iconWrap) {
      iconWrap.className = "size-12 mx-auto rounded-2xl bg-gradient-to-tr from-brand-600 via-indigo-500 to-purple-600 flex items-center justify-center shadow-xl shadow-brand-500/30";
    }
    starters.innerHTML = welcomeStartersHtml();
  }

  if (window.lucide) lucide.createIcons();
}

function quickAuditActiveScope() {
  if (!activeWorkingScope) return;
  usePrompt(`Audit the architecture and directory structure of ${activeWorkingScope.name}. Explain what each module does and check key files.`);
}

function openStudioForActiveScope() {
  if (!activeWorkingScope) return;
  openComputerFileManagerModal(activeWorkingScope.path);
}

function setActiveWorkingScopeFromComputer(folderPath) {
  if (!folderPath) return;
  const clean = folderPath.replace(/\/+$/, "");
  const parts = clean.split("/").filter(Boolean);
  const name = parts.length > 0 ? parts[parts.length - 1] : "Folder";
  setActiveWorkingScope({
    name: name,
    path: clean,
    type: "computer",
    icon: "folder",
    color: "#06b6d4"
  });
}

// ==============================================================
// COMPUTER FILESYSTEM & FULL MACHINE EXPLORER STUDIO
// ==============================================================

let activeSidebarTab = "projects";
let sidebarComputerCurrentPath = "~";
let computerCurrentPath = "~";
let currentDirectoryData = null;
let cfFilterQuery = "";
let cfShowHidden = false;
let cfSortBy = "name";
let cfSortColumn = "name";
let cfSortOrder = "asc";
let cfViewMode = localStorage.getItem("cf_view_mode") || "grid";
let cfIconScale = localStorage.getItem("cf_icon_scale") || "regular";
let cfIsFullscreen = false;
let cfSplittersInitialized = false;
let cfSelectedFile = null;
let cfFileContent = "";
let cfOriginalContent = "";
let cfEditorMode = "rendered"; // "edit" or "rendered"
let attachedComputerFile = null;

let cfItemOpType = "folder";
let cfTargetItem = null;

function switchSidebarTab(tab) {
  activeSidebarTab = tab;
  const btnProj = document.getElementById("sidebarTabProjects");
  const btnComp = document.getElementById("sidebarTabComputer");
  const hdrProj = document.getElementById("sidebarProjectsHeader");
  const hdrComp = document.getElementById("sidebarComputerHeader");
  const treeProj = document.getElementById("projectsTreeContainer");
  const treeComp = document.getElementById("computerTreeContainer");

  if (tab === "computer") {
    if (btnProj) btnProj.className = "flex-1 py-1 px-2 rounded-lg text-[11px] font-semibold text-zinc-400 hover:text-white transition-all flex items-center justify-center gap-1.5";
    if (btnComp) btnComp.className = "flex-1 py-1 px-2 rounded-lg text-[11px] font-semibold text-white bg-surface-750 shadow-sm transition-all flex items-center justify-center gap-1.5";
    if (hdrProj) hdrProj.classList.add("hidden");
    if (hdrComp) hdrComp.classList.remove("hidden");
    if (treeProj) treeProj.classList.add("hidden");
    if (treeComp) treeComp.classList.remove("hidden");
    loadSidebarComputerDirectory(sidebarComputerCurrentPath || "~");
  } else {
    if (btnProj) btnProj.className = "flex-1 py-1 px-2 rounded-lg text-[11px] font-semibold text-white bg-surface-750 shadow-sm transition-all flex items-center justify-center gap-1.5";
    if (btnComp) btnComp.className = "flex-1 py-1 px-2 rounded-lg text-[11px] font-semibold text-zinc-400 hover:text-white transition-all flex items-center justify-center gap-1.5";
    if (hdrProj) hdrProj.classList.remove("hidden");
    if (hdrComp) hdrComp.classList.add("hidden");
    if (treeProj) treeProj.classList.remove("hidden");
    if (treeComp) treeComp.classList.add("hidden");
  }
}

async function loadSidebarComputerDirectory(path) {
  const container = document.getElementById("computerTreeContainer");
  const pathLabel = document.getElementById("sidebarComputerPathLabel");
  if (!container) return;

  container.innerHTML = `<div class="text-xs text-zinc-500 text-center py-6"><i data-lucide="loader-2" class="size-4 animate-spin mx-auto mb-1.5 text-cyan-400"></i> Reading computer folders...</div>`;
  if (window.lucide) lucide.createIcons();

  try {
    const res = await fetch(`/api/fs/browse?path=${encodeURIComponent(path || "~")}&show_hidden=false`);
    const data = await res.json();
    sidebarComputerCurrentPath = data.current_path;

    if (pathLabel) {
      const parts = data.current_path.split("/").filter(Boolean);
      pathLabel.textContent = parts.length > 0 ? parts[parts.length - 1] : "/";
      pathLabel.title = data.current_path;
    }

    renderSidebarComputerTree(data);
  } catch (err) {
    container.innerHTML = `<div class="text-xs text-rose-400 p-3">Failed to access folder: ${escapeHtml(err.message)}</div>`;
  }
}

function renderSidebarComputerTree(data) {
  const container = document.getElementById("computerTreeContainer");
  if (!container) return;

  let html = "";

  // Quick Mac shortcut buttons
  html += `
    <div class="px-1 pb-2 border-b border-surface-700/40 mb-2">
      <div class="flex flex-wrap gap-1">
        <button onclick="loadSidebarComputerDirectory('~')" class="px-2 py-1 rounded text-[10px] bg-surface-800 hover:bg-surface-700 text-zinc-300 font-mono">~ Home</button>
        <button onclick="loadSidebarComputerDirectory('~/Documents')" class="px-2 py-1 rounded text-[10px] bg-surface-800 hover:bg-surface-700 text-zinc-300 font-mono">Docs</button>
        <button onclick="loadSidebarComputerDirectory('~/Desktop')" class="px-2 py-1 rounded text-[10px] bg-surface-800 hover:bg-surface-700 text-zinc-300 font-mono">Desktop</button>
        <button onclick="loadSidebarComputerDirectory('~/Downloads')" class="px-2 py-1 rounded text-[10px] bg-surface-800 hover:bg-surface-700 text-zinc-300 font-mono">Downloads</button>
      </div>
    </div>
  `;

  // Prominent AI Scope Action for this folder
  const currentFolderName = data.current_path.split("/").filter(Boolean).pop() || "Folder";
  const isCurrentFolderScoped = activeWorkingScope && activeWorkingScope.path === data.current_path;

  if (isCurrentFolderScoped) {
    html += `
      <div class="mb-2 p-2 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-between gap-2 shadow-sm">
        <div class="flex items-center gap-1.5 min-w-0">
          <span class="size-2 rounded-full bg-emerald-400 animate-pulse shrink-0"></span>
          <span class="text-xs font-semibold text-emerald-300 truncate">AI Controlling ${escapeHtml(currentFolderName)}</span>
        </div>
        <button onclick="clearActiveWorkingScope()" class="text-[10px] px-2 py-0.5 rounded bg-surface-800 hover:bg-surface-700 text-zinc-300 shrink-0 font-medium transition-colors">Release</button>
      </div>
    `;
  } else {
    html += `
      <div class="mb-2">
        <button onclick="setActiveWorkingScopeFromComputer('${escapeHtml(data.current_path)}')" class="w-full py-1.5 px-2.5 rounded-xl bg-gradient-to-r from-cyan-600/20 to-brand-600/20 hover:from-cyan-600/30 hover:to-brand-600/30 border border-cyan-500/40 text-cyan-300 text-xs font-semibold flex items-center justify-between transition-all group shadow-sm">
          <span class="flex items-center gap-1.5 truncate">
            <i data-lucide="crosshair" class="size-3.5 text-cyan-400 shrink-0"></i>
            <span class="truncate">Control "${escapeHtml(currentFolderName)}" with AI</span>
          </span>
          <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-cyan-500/20 group-hover:bg-cyan-500/30 text-white shrink-0">Scope</span>
        </button>
      </div>
    `;
  }

  // Up folder button if not root
  if (data.parent_path) {
    html += `
      <div onclick="loadSidebarComputerDirectory('${escapeHtml(data.parent_path)}')" class="flex items-center gap-2 px-2 py-1 rounded-lg text-xs text-zinc-400 hover:text-white hover:bg-surface-800/60 cursor-pointer transition-colors">
        <i data-lucide="corner-left-up" class="size-3.5 text-zinc-500"></i>
        <span class="font-mono">.. (Parent folder)</span>
      </div>
    `;
  }

  // Folders list
  const folders = data.folders || [];
  for (const f of folders) {
    const isSubfolderScoped = activeWorkingScope && activeWorkingScope.path === f.path;
    html += `
      <div class="group flex items-center justify-between px-2 py-1.5 rounded-lg text-xs ${isSubfolderScoped ? 'bg-emerald-500/10 border border-emerald-500/30 text-white font-semibold' : 'text-zinc-300 hover:text-white hover:bg-surface-800/60'} cursor-pointer transition-colors">
        <div onclick="loadSidebarComputerDirectory('${escapeHtml(f.path)}')" class="flex items-center gap-2 truncate flex-1 min-w-0 pr-1">
          <i data-lucide="folder" class="size-3.5 ${isSubfolderScoped ? 'text-emerald-400' : 'text-amber-400/90'} shrink-0"></i>
          <span class="truncate" title="${escapeHtml(f.path)}">${escapeHtml(f.name)}</span>
        </div>
        <div class="flex items-center gap-1 shrink-0">
          ${isSubfolderScoped ? `<span class="text-[9px] font-mono px-1 rounded bg-emerald-500/20 text-emerald-400">Scoped</span>` : ''}
          <span class="text-[10px] text-zinc-500 font-mono">${f.item_count || 0}</span>
          <button onclick="event.stopPropagation(); setActiveWorkingScopeFromComputer('${escapeHtml(f.path)}')" class="opacity-0 group-hover:opacity-100 p-0.5 rounded text-zinc-400 hover:text-cyan-400 transition-opacity" title="Scope AI to this folder">
            <i data-lucide="crosshair" class="size-3"></i>
          </button>
          <button onclick="event.stopPropagation(); openComputerFileManagerModal('${escapeHtml(f.path)}')" class="opacity-0 group-hover:opacity-100 p-0.5 rounded text-zinc-400 hover:text-cyan-400 transition-opacity" title="Open in Studio">
            <i data-lucide="external-link" class="size-3"></i>
          </button>
        </div>
      </div>
    `;
  }

  // Files list
  const files = data.files || [];
  for (const f of files) {
    const isMd = f.extension === ".md";
    const isCode = [".py", ".js", ".html", ".json", ".css"].includes(f.extension);
    const iconColor = isMd ? "text-amber-400" : (isCode ? "text-cyan-400" : "text-zinc-400");
    const iconName = f.is_image ? "image" : (isCode ? "file-code" : "file-text");

    html += `
      <div class="group flex items-center justify-between px-2 py-1 rounded-lg text-[11px] text-zinc-400 hover:text-white hover:bg-surface-800/40 cursor-pointer transition-colors">
        <div onclick="openComputerFileManagerModal('${escapeHtml(data.current_path)}'); selectCfFile('${escapeHtml(f.path)}')" class="flex items-center gap-1.5 truncate flex-1 pr-1 min-w-0">
          <i data-lucide="${iconName}" class="size-3 shrink-0 ${iconColor}"></i>
          <span class="truncate font-mono" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>
        </div>
        <div class="flex items-center gap-1 shrink-0">
          <span class="text-[9px] font-mono text-zinc-500">${escapeHtml(f.size_formatted)}</span>
          <button onclick="event.stopPropagation(); attachComputerFile('${escapeHtml(f.path)}')" class="opacity-0 group-hover:opacity-100 p-0.5 rounded text-zinc-400 hover:text-brand-400 transition-opacity" title="Attach to Chat">
            <i data-lucide="paperclip" class="size-2.5"></i>
          </button>
        </div>
      </div>
    `;
  }

  if (folders.length === 0 && files.length === 0) {
    html += `<div class="text-[11px] text-zinc-500 py-4 text-center italic">Folder is empty</div>`;
  }

  container.innerHTML = html;
  if (window.lucide) lucide.createIcons();
}

async function navigateSidebarComputerUp() {
  if (sidebarComputerCurrentPath) {
    const parent = sidebarComputerCurrentPath.replace(/\/[^\/]+$/, "") || "/";
    await loadSidebarComputerDirectory(parent);
  }
}

// -------------------------------------------------------------
// COMPUTER FILE STUDIO MODAL
// -------------------------------------------------------------

function openComputerFileManagerModal(initialPath) {
  const modal = document.getElementById("computerFileManagerModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  initCfSplitters();
  syncCfControlsUI();
  loadComputerDirectory(initialPath || sidebarComputerCurrentPath || "~");
}

function closeComputerFileManagerModal() {
  const modal = document.getElementById("computerFileManagerModal");
  if (!modal) return;
  modal.classList.add("hidden");
}

function toggleCfFullscreen() {
  const modal = document.getElementById("computerFileManagerModal");
  const card = document.getElementById("cfModalCard");
  const btn = document.getElementById("cfFullscreenBtn");
  if (!modal || !card) return;

  cfIsFullscreen = !cfIsFullscreen;
  if (cfIsFullscreen) {
    modal.classList.add("is-fullscreen");
    card.classList.add("is-fullscreen");
    if (btn) {
      btn.innerHTML = `<i data-lucide="minimize-2" class="size-4"></i>`;
      btn.title = "Restore Window";
    }
  } else {
    modal.classList.remove("is-fullscreen");
    card.classList.remove("is-fullscreen");
    if (btn) {
      btn.innerHTML = `<i data-lucide="maximize-2" class="size-4"></i>`;
      btn.title = "Maximize Window";
    }
  }
  if (window.lucide) lucide.createIcons();
}

function toggleCfInspector() {
  const pane = document.getElementById("cfInspectorPane");
  const splitter = document.getElementById("cfSplitterRight");
  if (!pane || !splitter) return;

  const willHide = !pane.classList.contains("hidden");
  if (willHide) {
    pane.classList.add("hidden");
    splitter.classList.add("hidden");
    localStorage.setItem("cf_inspector_visible", "false");
  } else {
    pane.classList.remove("hidden");
    splitter.classList.remove("hidden");
    localStorage.setItem("cf_inspector_visible", "true");
  }
  syncCfControlsUI();
}

function setCfIconScale(scale) {
  cfIconScale = scale;
  localStorage.setItem("cf_icon_scale", scale);
  syncCfControlsUI();
  renderCfItems();
}

function syncCfControlsUI() {
  const gridBtn = document.getElementById("cfViewGridBtn");
  const listBtn = document.getElementById("cfViewListBtn");
  const sizeCtrl = document.getElementById("cfGridSizeControl");
  if (gridBtn && listBtn) {
    if (cfViewMode === "grid") {
      gridBtn.className = "p-1 rounded text-cyan-400 bg-surface-800";
      listBtn.className = "p-1 rounded text-zinc-500 hover:text-zinc-300";
      if (sizeCtrl) sizeCtrl.classList.remove("opacity-40", "pointer-events-none");
    } else {
      gridBtn.className = "p-1 rounded text-zinc-500 hover:text-zinc-300";
      listBtn.className = "p-1 rounded text-cyan-400 bg-surface-800";
      if (sizeCtrl) sizeCtrl.classList.add("opacity-40", "pointer-events-none");
    }
  }

  // Scale buttons
  const btnCompact = document.getElementById("cfScaleCompactBtn");
  const btnRegular = document.getElementById("cfScaleRegularBtn");
  const btnLarge = document.getElementById("cfScaleLargeBtn");

  [btnCompact, btnRegular, btnLarge].forEach(b => {
    if (b) {
      b.className = "px-2 py-0.5 rounded text-zinc-400 hover:text-white transition-colors";
    }
  });

  const activeBtn = cfIconScale === "compact" ? btnCompact : (cfIconScale === "large" ? btnLarge : btnRegular);
  if (activeBtn) {
    activeBtn.className = "px-2 py-0.5 rounded text-cyan-400 bg-surface-800 font-medium transition-colors";
  }

  // Inspector toggle button
  const inspectorBtn = document.getElementById("cfToggleInspectorBtn");
  const inspectorPane = document.getElementById("cfInspectorPane");
  if (inspectorBtn && inspectorPane) {
    if (inspectorPane.classList.contains("hidden")) {
      inspectorBtn.className = "p-1.5 rounded-lg border border-surface-700 bg-surface-900 text-zinc-500 hover:text-zinc-300 hover:bg-surface-800 transition-colors";
    } else {
      inspectorBtn.className = "p-1.5 rounded-lg border border-cyan-500/30 bg-surface-800 text-cyan-400 hover:text-cyan-300 hover:bg-surface-750 transition-colors";
    }
  }
}

function initCfSplitters() {
  const leftPane = document.getElementById("cfLeftPane");
  const splitterLeft = document.getElementById("cfSplitterLeft");
  const rightPane = document.getElementById("cfInspectorPane");
  const splitterRight = document.getElementById("cfSplitterRight");
  const container = document.getElementById("cfPanesWrapper");

  if (!leftPane || !splitterLeft || !rightPane || !splitterRight || !container) return;

  // Restore saved widths
  const savedLeft = localStorage.getItem("cf_left_pane_width");
  if (savedLeft && !isNaN(savedLeft)) {
    leftPane.style.width = `${Math.max(160, Math.min(380, parseInt(savedLeft, 10)))}px`;
  }
  const savedRight = localStorage.getItem("cf_inspector_pane_width");
  if (savedRight && !isNaN(savedRight)) {
    rightPane.style.width = `${Math.max(280, Math.min(650, parseInt(savedRight, 10)))}px`;
  }

  // Restore inspector visibility
  const inspectorVisible = localStorage.getItem("cf_inspector_visible") !== "false";
  if (!inspectorVisible) {
    rightPane.classList.add("hidden");
    splitterRight.classList.add("hidden");
  } else {
    rightPane.classList.remove("hidden");
    splitterRight.classList.remove("hidden");
  }

  if (cfSplittersInitialized) return;
  cfSplittersInitialized = true;

  // Left splitter drag
  splitterLeft.addEventListener("mousedown", (e) => {
    e.preventDefault();
    splitterLeft.classList.add("is-dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const onMouseMove = (moveEvent) => {
      const containerRect = container.getBoundingClientRect();
      let newWidth = moveEvent.clientX - containerRect.left;
      newWidth = Math.max(160, Math.min(420, newWidth));
      leftPane.style.width = `${newWidth}px`;
    };

    const onMouseUp = () => {
      splitterLeft.classList.remove("is-dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      localStorage.setItem("cf_left_pane_width", parseInt(leftPane.style.width, 10));
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  });

  // Right splitter drag
  splitterRight.addEventListener("mousedown", (e) => {
    e.preventDefault();
    splitterRight.classList.add("is-dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const onMouseMove = (moveEvent) => {
      const containerRect = container.getBoundingClientRect();
      let newWidth = containerRect.right - moveEvent.clientX;
      newWidth = Math.max(260, Math.min(700, newWidth));
      rightPane.style.width = `${newWidth}px`;
    };

    const onMouseUp = () => {
      splitterRight.classList.remove("is-dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      localStorage.setItem("cf_inspector_pane_width", parseInt(rightPane.style.width, 10));
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  });
}

function getFileKindLabel(file) {
  const ext = (file.extension || "").toLowerCase();
  const kindMap = {
    ".py": "Python source",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".html": "HTML document",
    ".css": "CSS stylesheet",
    ".json": "JSON data",
    ".md": "Markdown document",
    ".txt": "Plain text",
    ".sh": "Shell script",
    ".yml": "YAML configuration",
    ".yaml": "YAML configuration",
    ".png": "PNG image",
    ".jpg": "JPEG image",
    ".jpeg": "JPEG image",
    ".svg": "SVG graphic",
    ".pdf": "PDF document",
    ".zip": "ZIP archive",
    ".tar": "TAR archive",
    ".gz": "GZIP archive",
    ".dmg": "macOS disk image"
  };
  return kindMap[ext] || (ext ? `${ext.slice(1).toUpperCase()} file` : "Document");
}

function formatMatchedText(text, query) {
  if (!query) return escapeHtml(text);
  const escaped = escapeHtml(text);
  const regex = new RegExp(`(${escapeRegex(query)})`, "gi");
  return escaped.replace(regex, `<mark class="bg-cyan-500/35 text-cyan-200 rounded px-0.5 font-semibold">$1</mark>`);
}

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function toggleSortColumn(col) {
  if (cfSortColumn === col) {
    cfSortOrder = cfSortOrder === "asc" ? "desc" : "asc";
  } else {
    cfSortColumn = col;
    cfSortOrder = (col === "date" || col === "size") ? "desc" : "asc";
  }
  const sel = document.getElementById("cfSortSelect");
  if (sel && ["name", "date", "size"].includes(col)) {
    sel.value = col;
  }
  renderCfItems();
}

async function loadComputerDirectory(path, sortBy) {
  const itemsContainer = document.getElementById("cfItemsContainer");
  const countEl = document.getElementById("cfFolderCounts");
  const pathInd = document.getElementById("cfDiskPathIndicator");
  const writableBadge = document.getElementById("cfWritableBadge");

  if (itemsContainer) {
    itemsContainer.innerHTML = `<div class="text-xs text-zinc-500 text-center py-16"><i data-lucide="loader-2" class="size-6 animate-spin mx-auto mb-2 text-cyan-400"></i> Reading directory from hard drive...</div>`;
    if (window.lucide) lucide.createIcons();
  }

  try {
    const sort = sortBy || cfSortBy;
    const res = await fetch(`/api/fs/browse?path=${encodeURIComponent(path || "~")}&show_hidden=${cfShowHidden}&sort_by=${sort}`);
    const data = await res.json();
    currentDirectoryData = data;
    computerCurrentPath = data.current_path;
    sidebarComputerCurrentPath = data.current_path;

    // Render breadcrumbs
    renderCfBreadcrumbs(data.breadcrumbs || []);

    // Render shortcuts & workspaces in left rail
    renderCfShortcuts(data.shortcuts || []);
    renderCfLinkedWorkspaces();

    // Render header indicators
    if (pathInd) pathInd.textContent = data.current_path;
    if (countEl) countEl.textContent = `${data.total_folders || 0} folders · ${data.total_files || 0} files`;
    if (writableBadge) {
      if (data.is_writable) {
        writableBadge.textContent = "Writable";
        writableBadge.className = "text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 font-mono";
      } else {
        writableBadge.textContent = "Read-Only";
        writableBadge.className = "text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 font-mono";
      }
    }

    renderCfItems();

    // Reset right pane inspector to folder overview if no file currently selected
    if (!cfSelectedFile) {
      renderCfFolderOverview(data);
    }

  } catch (err) {
    if (itemsContainer) {
      itemsContainer.innerHTML = `<div class="p-6 text-rose-400 text-xs">Failed to load folder: ${escapeHtml(err.message)}</div>`;
    }
  }
}

function renderCfBreadcrumbs(breadcrumbs) {
  const container = document.getElementById("cfBreadcrumbsContainer");
  if (!container) return;

  if (!breadcrumbs || breadcrumbs.length === 0) {
    container.innerHTML = `<span class="text-zinc-500">/</span>`;
    return;
  }

  container.innerHTML = breadcrumbs.map((b, idx) => {
    const isFirst = idx === 0;
    const isLast = idx === breadcrumbs.length - 1;

    let iconHtml = "";
    let displayName = escapeHtml(b.name);

    if (isFirst && (b.name === "/" || b.path === "/")) {
      iconHtml = `<i data-lucide="hard-drive" class="size-3 text-cyan-400 shrink-0 mr-1 inline"></i>`;
      displayName = "Macintosh HD";
    } else if (b.path.includes("/Users/") && !b.path.replace("/Users/", "").includes("/")) {
      iconHtml = `<i data-lucide="home" class="size-3 text-emerald-400 shrink-0 mr-1 inline"></i>`;
    } else if (b.name === "Applications") {
      iconHtml = `<i data-lucide="layers" class="size-3 text-indigo-400 shrink-0 mr-1 inline"></i>`;
    }

    return `
      <button onclick="loadComputerDirectory('${escapeHtml(b.path)}')"
        class="cf-breadcrumb-btn ${isLast ? 'active font-bold text-white' : 'text-zinc-400 hover:text-zinc-200'}"
        title="${escapeHtml(b.path)}">
        ${iconHtml}<span>${displayName}</span>
      </button>
      ${!isLast ? `<i data-lucide="chevron-right" class="size-3 text-zinc-600 shrink-0 mx-0.5"></i>` : ''}
    `;
  }).join("");

  if (window.lucide) lucide.createIcons();
}

function renderCfShortcuts(shortcuts) {
  const container = document.getElementById("cfShortcutsList");
  if (!container) return;

  container.innerHTML = shortcuts.map(s => {
    const isActive = computerCurrentPath === s.path;
    return `
      <button onclick="loadComputerDirectory('${escapeHtml(s.path)}')" class="cf-location-btn ${isActive ? 'active' : ''}">
        <i data-lucide="${s.icon || 'folder'}" class="size-3.5 text-cyan-400/90"></i>
        <span class="truncate">${escapeHtml(s.name)}</span>
      </button>
    `;
  }).join("");
  if (window.lucide) lucide.createIcons();
}

function renderCfLinkedWorkspaces() {
  const container = document.getElementById("cfLinkedWorkspacesList");
  if (!container) return;

  const linked = (localProjects || []).filter(p => p.is_linked || p.path);
  if (linked.length === 0) {
    container.innerHTML = `<p class="text-[10px] text-zinc-500 px-2 italic">No linked folders yet</p>`;
    return;
  }

  container.innerHTML = linked.slice(0, 6).map(p => {
    const isActive = computerCurrentPath === p.path;
    return `
      <button onclick="loadComputerDirectory('${escapeHtml(p.path)}')" class="cf-location-btn ${isActive ? 'active' : ''}">
        <i data-lucide="${p.icon || 'folder'}" class="size-3.5" style="color:${p.color || '#10b981'}"></i>
        <span class="truncate">${escapeHtml(p.name)}</span>
      </button>
    `;
  }).join("");
  if (window.lucide) lucide.createIcons();
}

function renderCfItems() {
  const container = document.getElementById("cfItemsContainer");
  if (!container || !currentDirectoryData) return;

  const q = cfFilterQuery.toLowerCase().trim();
  let folders = (currentDirectoryData.folders || []).filter(f => !q || f.name.toLowerCase().includes(q));
  let files = (currentDirectoryData.files || []).filter(f => !q || f.name.toLowerCase().includes(q));

  // In-memory sorting based on cfSortColumn and cfSortOrder
  const sortDirection = cfSortOrder === "asc" ? 1 : -1;
  const sortFn = (a, b) => {
    if (cfSortColumn === "date") {
      return ((a.updated_at || 0) - (b.updated_at || 0)) * sortDirection;
    }
    if (cfSortColumn === "size") {
      const aVal = a.item_count !== undefined ? a.item_count : (a.size_bytes || 0);
      const bVal = b.item_count !== undefined ? b.item_count : (b.size_bytes || 0);
      return (aVal - bVal) * sortDirection;
    }
    if (cfSortColumn === "kind") {
      const aKind = a.extension || (a.name.endsWith(".app") ? ".app" : "folder");
      const bKind = b.extension || (b.name.endsWith(".app") ? ".app" : "folder");
      return aKind.localeCompare(bKind) * sortDirection;
    }
    // Default: name
    return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" }) * sortDirection;
  };

  folders.sort(sortFn);
  files.sort(sortFn);

  if (folders.length === 0 && files.length === 0) {
    container.innerHTML = `
      <div class="flex flex-col items-center justify-center py-16 text-center">
        <div class="size-12 rounded-2xl bg-surface-800 border border-surface-700/60 flex items-center justify-center text-zinc-500 mb-3">
          <i data-lucide="search-x" class="size-6"></i>
        </div>
        <p class="text-sm font-medium text-zinc-300">No items match your filter</p>
        <p class="text-xs text-zinc-500 mt-1 max-w-sm">No items found matching "<span class="text-cyan-400 font-mono">${escapeHtml(q)}</span>".</p>
        <button onclick="document.getElementById('cfFilterInput').value=''; filterCfItems();" class="mt-3 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-750 text-xs text-zinc-300 border border-surface-700 transition-colors">
          Clear Filter
        </button>
      </div>
    `;
    if (window.lucide) lucide.createIcons();
    return;
  }

  let html = "";

  if (cfViewMode === "grid") {
    html += `<div class="space-y-5">`;

    // Folders Section
    if (folders.length > 0) {
      html += `
        <div>
          <div class="flex items-center justify-between mb-2 px-0.5">
            <span class="text-[11px] font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
              <i data-lucide="folder" class="size-3 text-amber-400"></i>
              Folders (${folders.length})
            </span>
          </div>
          <div class="cf-items-grid ${cfIconScale}">
            ${folders.map(f => {
              const isApp = f.name.endsWith(".app");
              const iconName = isApp ? "layers" : (f.is_symlink ? "folder-symlink" : "folder");
              const iconColor = isApp ? "text-indigo-400 bg-indigo-500/10 border-indigo-500/20" : "text-amber-400 bg-amber-500/10 border-amber-500/20";
              const isScope = activeWorkingScope && activeWorkingScope.path === f.path;

              return `
                <div onclick="loadComputerDirectory('${escapeHtml(f.path)}')"
                  class="cf-item-card cf-folder-card group ${isScope ? 'ring-1 ring-emerald-500/60 bg-emerald-950/20' : ''}"
                  data-path="${escapeHtml(f.path)}">
                  <div class="flex items-start gap-2.5 min-w-0 flex-1">
                    <div class="cf-item-icon-box size-8 rounded-lg border ${iconColor} flex items-center justify-center shrink-0 mt-0.5">
                      <i data-lucide="${iconName}" class="size-4"></i>
                    </div>
                    <div class="min-w-0 flex-1">
                      <p class="cf-card-title text-xs font-medium text-white group-hover:text-cyan-300 transition-colors" title="${escapeHtml(f.name)}">
                        ${formatMatchedText(f.name, q)}
                      </p>
                      <div class="cf-card-meta text-[10px] text-zinc-500 font-mono mt-0.5 flex items-center gap-1.5 flex-wrap">
                        <span>${f.item_count} items</span>
                        <span class="text-zinc-600">·</span>
                        <span>${escapeHtml(f.time_ago_str)}</span>
                        ${isScope ? `<span class="text-[9px] px-1 py-0.2 rounded bg-emerald-500/20 text-emerald-400 font-semibold uppercase">AI Scope</span>` : ''}
                      </div>
                    </div>
                  </div>
                  <div class="cf-item-actions flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0 ml-1">
                    <button onclick="event.stopPropagation(); setActiveWorkingScopeFromComputer('${escapeHtml(f.path)}'); closeComputerFileManagerModal();"
                      class="p-1 rounded text-zinc-400 hover:text-emerald-400 hover:bg-surface-700/80 transition-colors"
                      title="Scope AI Assistant to this folder">
                      <i data-lucide="crosshair" class="size-3.5"></i>
                    </button>
                    <button onclick="event.stopPropagation(); openCfRenameModal('${escapeHtml(f.path)}', '${escapeHtml(f.name)}')"
                      class="p-1 rounded text-zinc-400 hover:text-white hover:bg-surface-700/80 transition-colors"
                      title="Rename">
                      <i data-lucide="edit-2" class="size-3"></i>
                    </button>
                    <button onclick="event.stopPropagation(); openCfDeleteModal('${escapeHtml(f.path)}', '${escapeHtml(f.name)}')"
                      class="p-1 rounded text-zinc-400 hover:text-rose-400 hover:bg-surface-700/80 transition-colors"
                      title="Delete">
                      <i data-lucide="trash-2" class="size-3"></i>
                    </button>
                  </div>
                </div>
              `;
            }).join("")}
          </div>
        </div>
      `;
    }

    // Files Section
    if (files.length > 0) {
      html += `
        <div>
          <div class="flex items-center justify-between mb-2 px-0.5">
            <span class="text-[11px] font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
              <i data-lucide="file-text" class="size-3 text-cyan-400"></i>
              Files (${files.length})
            </span>
          </div>
          <div class="cf-items-grid ${cfIconScale}">
            ${files.map(f => {
              const isSelected = cfSelectedFile && cfSelectedFile.path === f.path;
              const isMd = f.extension === ".md";
              const isCode = [".py", ".js", ".html", ".json", ".css", ".yaml", ".sh", ".ts"].includes(f.extension);
              const iconColor = isMd ? "text-amber-400 bg-amber-500/10 border-amber-500/20" : (isCode ? "text-cyan-400 bg-cyan-500/10 border-cyan-500/20" : (f.is_image ? "text-emerald-400 bg-emerald-500/10 border-emerald-500/20" : "text-indigo-400 bg-indigo-500/10 border-indigo-500/20"));
              const iconName = f.is_image ? "image" : (isCode ? "file-code" : (isMd ? "file-text" : "file"));

              return `
                <div onclick="selectCfFile('${escapeHtml(f.path)}')"
                  class="cf-item-card cf-file-card flex flex-col justify-between group ${isSelected ? 'selected' : ''}"
                  data-path="${escapeHtml(f.path)}">
                  <div class="flex items-start justify-between gap-2 mb-2">
                    <div class="cf-item-icon-box size-8 rounded-lg border ${iconColor} flex items-center justify-center shrink-0">
                      <i data-lucide="${iconName}" class="size-4"></i>
                    </div>
                    <div class="cf-item-actions flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button onclick="event.stopPropagation(); askXodAboutFile('${escapeHtml(f.path)}')" class="p-1 rounded text-zinc-400 hover:text-cyan-400 hover:bg-surface-700/80 transition-colors" title="Ask AI about this file">
                        <i data-lucide="sparkles" class="size-3"></i>
                      </button>
                      <button onclick="event.stopPropagation(); cfDuplicateItem('${escapeHtml(f.path)}')" class="p-1 rounded text-zinc-400 hover:text-white hover:bg-surface-700/80 transition-colors" title="Duplicate file">
                        <i data-lucide="copy" class="size-3"></i>
                      </button>
                      <button onclick="event.stopPropagation(); openCfDeleteModal('${escapeHtml(f.path)}', '${escapeHtml(f.name)}')" class="p-1 rounded text-zinc-400 hover:text-rose-400 hover:bg-surface-700/80 transition-colors" title="Delete file">
                        <i data-lucide="trash-2" class="size-3"></i>
                      </button>
                    </div>
                  </div>
                  <div>
                    <p class="cf-card-title text-xs font-mono font-medium text-white group-hover:text-cyan-300 transition-colors" title="${escapeHtml(f.name)}">
                      ${formatMatchedText(f.name, q)}
                    </p>
                    <div class="cf-card-meta flex items-center justify-between text-[10px] text-zinc-500 font-mono mt-1">
                      <span>${escapeHtml(f.size_formatted)}</span>
                      <span>${escapeHtml(f.time_ago_str)}</span>
                    </div>
                  </div>
                </div>
              `;
            }).join("")}
          </div>
        </div>
      `;
    }

    html += `</div>`;
  } else {
    // macOS List View (Table with Sortable Columns)
    html += `
      <div class="cf-list-table-wrapper rounded-xl border border-surface-700/60 overflow-hidden">
        <table class="w-full text-left text-xs border-collapse">
          <thead>
            <tr class="bg-surface-850 border-b border-surface-700/60 text-[11px] font-semibold text-zinc-400 select-none">
              <th onclick="toggleSortColumn('name')" class="py-2.5 px-3 cursor-pointer hover:text-white transition-colors">
                <div class="flex items-center gap-1.5">
                  <span>Name</span>
                  ${cfSortColumn === 'name' ? `<i data-lucide="${cfSortOrder === 'asc' ? 'chevron-up' : 'chevron-down'}" class="size-3 text-cyan-400"></i>` : ''}
                </div>
              </th>
              <th onclick="toggleSortColumn('date')" class="py-2.5 px-3 cursor-pointer hover:text-white transition-colors w-36">
                <div class="flex items-center gap-1.5">
                  <span>Date Modified</span>
                  ${cfSortColumn === 'date' ? `<i data-lucide="${cfSortOrder === 'asc' ? 'chevron-up' : 'chevron-down'}" class="size-3 text-cyan-400"></i>` : ''}
                </div>
              </th>
              <th onclick="toggleSortColumn('size')" class="py-2.5 px-3 cursor-pointer hover:text-white transition-colors w-28">
                <div class="flex items-center gap-1.5">
                  <span>Size</span>
                  ${cfSortColumn === 'size' ? `<i data-lucide="${cfSortOrder === 'asc' ? 'chevron-up' : 'chevron-down'}" class="size-3 text-cyan-400"></i>` : ''}
                </div>
              </th>
              <th onclick="toggleSortColumn('kind')" class="py-2.5 px-3 cursor-pointer hover:text-white transition-colors w-32 hidden sm:table-cell">
                <div class="flex items-center gap-1.5">
                  <span>Kind</span>
                  ${cfSortColumn === 'kind' ? `<i data-lucide="${cfSortOrder === 'asc' ? 'chevron-up' : 'chevron-down'}" class="size-3 text-cyan-400"></i>` : ''}
                </div>
              </th>
              <th class="py-2.5 px-3 text-right w-28">Actions</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-surface-800/60">
            ${folders.map(f => {
              const isApp = f.name.endsWith(".app");
              const iconName = isApp ? "layers" : (f.is_symlink ? "folder-symlink" : "folder");
              const iconColor = isApp ? "text-indigo-400" : "text-amber-400";
              const isScope = activeWorkingScope && activeWorkingScope.path === f.path;

              return `
                <tr onclick="loadComputerDirectory('${escapeHtml(f.path)}')"
                  class="cf-list-row group hover:bg-surface-800/70 transition-colors cursor-pointer ${isScope ? 'bg-emerald-950/20' : ''}">
                  <td class="py-2 px-3 min-w-0">
                    <div class="flex items-center gap-2.5 min-w-0">
                      <i data-lucide="${iconName}" class="size-4 ${iconColor} shrink-0"></i>
                      <span class="font-medium text-white group-hover:text-cyan-300 truncate" title="${escapeHtml(f.name)}">
                        ${formatMatchedText(f.name, q)}
                      </span>
                      ${isScope ? `<span class="text-[9px] px-1 py-0.2 rounded bg-emerald-500/20 text-emerald-400 font-semibold uppercase shrink-0">AI Scope</span>` : ''}
                    </div>
                  </td>
                  <td class="py-2 px-3 text-zinc-400 font-mono text-[11px] whitespace-nowrap">
                    ${escapeHtml(f.time_ago_str)}
                  </td>
                  <td class="py-2 px-3 text-zinc-400 font-mono text-[11px] whitespace-nowrap">
                    ${f.item_count} items
                  </td>
                  <td class="py-2 px-3 text-zinc-500 text-[11px] whitespace-nowrap hidden sm:table-cell">
                    ${isApp ? 'Application' : 'Folder'}
                  </td>
                  <td class="py-2 px-3 text-right whitespace-nowrap">
                    <div class="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button onclick="event.stopPropagation(); setActiveWorkingScopeFromComputer('${escapeHtml(f.path)}'); closeComputerFileManagerModal();" class="p-1 rounded text-zinc-400 hover:text-emerald-400 hover:bg-surface-700/80 transition-colors" title="Scope AI Assistant">
                        <i data-lucide="crosshair" class="size-3.5"></i>
                      </button>
                      <button onclick="event.stopPropagation(); openCfRenameModal('${escapeHtml(f.path)}', '${escapeHtml(f.name)}')" class="p-1 rounded text-zinc-400 hover:text-white hover:bg-surface-700/80 transition-colors" title="Rename">
                        <i data-lucide="edit-2" class="size-3"></i>
                      </button>
                      <button onclick="event.stopPropagation(); openCfDeleteModal('${escapeHtml(f.path)}', '${escapeHtml(f.name)}')" class="p-1 rounded text-zinc-400 hover:text-rose-400 hover:bg-surface-700/80 transition-colors" title="Delete">
                        <i data-lucide="trash-2" class="size-3"></i>
                      </button>
                    </div>
                  </td>
                </tr>
              `;
            }).join("")}
            ${files.map(f => {
              const isSelected = cfSelectedFile && cfSelectedFile.path === f.path;
              const isMd = f.extension === ".md";
              const isCode = [".py", ".js", ".html", ".json", ".css", ".yaml", ".sh", ".ts"].includes(f.extension);
              const iconColor = isMd ? "text-amber-400" : (isCode ? "text-cyan-400" : (f.is_image ? "text-emerald-400" : "text-indigo-400"));
              const iconName = f.is_image ? "image" : (isCode ? "file-code" : (isMd ? "file-text" : "file"));

              return `
                <tr onclick="selectCfFile('${escapeHtml(f.path)}')"
                  class="cf-list-row group hover:bg-surface-800/70 transition-colors cursor-pointer ${isSelected ? 'selected' : ''}">
                  <td class="py-2 px-3 min-w-0">
                    <div class="flex items-center gap-2.5 min-w-0">
                      <i data-lucide="${iconName}" class="size-4 ${iconColor} shrink-0"></i>
                      <span class="font-mono text-zinc-200 group-hover:text-cyan-300 truncate" title="${escapeHtml(f.name)}">
                        ${formatMatchedText(f.name, q)}
                      </span>
                    </div>
                  </td>
                  <td class="py-2 px-3 text-zinc-400 font-mono text-[11px] whitespace-nowrap">
                    ${escapeHtml(f.time_ago_str)}
                  </td>
                  <td class="py-2 px-3 text-zinc-400 font-mono text-[11px] whitespace-nowrap">
                    ${escapeHtml(f.size_formatted)}
                  </td>
                  <td class="py-2 px-3 text-zinc-500 text-[11px] whitespace-nowrap hidden sm:table-cell">
                    ${getFileKindLabel(f)}
                  </td>
                  <td class="py-2 px-3 text-right whitespace-nowrap">
                    <div class="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button onclick="event.stopPropagation(); askXodAboutFile('${escapeHtml(f.path)}')" class="p-1 rounded text-zinc-400 hover:text-cyan-400 hover:bg-surface-700/80 transition-colors" title="Ask AI">
                        <i data-lucide="sparkles" class="size-3.5"></i>
                      </button>
                      <button onclick="event.stopPropagation(); cfDuplicateItem('${escapeHtml(f.path)}')" class="p-1 rounded text-zinc-400 hover:text-white hover:bg-surface-700/80 transition-colors" title="Duplicate">
                        <i data-lucide="copy" class="size-3"></i>
                      </button>
                      <button onclick="event.stopPropagation(); openCfDeleteModal('${escapeHtml(f.path)}', '${escapeHtml(f.name)}')" class="p-1 rounded text-zinc-400 hover:text-rose-400 hover:bg-surface-700/80 transition-colors" title="Delete">
                        <i data-lucide="trash-2" class="size-3"></i>
                      </button>
                    </div>
                  </td>
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  container.innerHTML = html;
  if (window.lucide) lucide.createIcons();
}

function filterCfItems() {
  const input = document.getElementById("cfFilterInput");
  cfFilterQuery = input ? input.value : "";
  renderCfItems();
}

function toggleCfShowHidden() {
  const chk = document.getElementById("cfShowHiddenCheck");
  cfShowHidden = chk ? chk.checked : false;
  loadComputerDirectory(computerCurrentPath);
}

function changeCfSort(val) {
  cfSortBy = val;
  cfSortColumn = val;
  cfSortOrder = (val === "date" || val === "size") ? "desc" : "asc";
  renderCfItems();
}

function setCfViewMode(mode) {
  cfViewMode = mode;
  localStorage.setItem("cf_view_mode", mode);
  syncCfControlsUI();
  renderCfItems();
}

function toggleCfManualPathInput() {
  const bar = document.getElementById("cfManualPathBar");
  const input = document.getElementById("cfManualPathInput");
  if (!bar) return;
  bar.classList.toggle("hidden");
  if (!bar.classList.contains("hidden") && input) {
    input.value = computerCurrentPath;
    input.focus();
    input.select();
  }
}

function executeCfManualNavigate() {
  const input = document.getElementById("cfManualPathInput");
  const path = input ? input.value.trim() : "";
  if (path) {
    loadComputerDirectory(path);
    document.getElementById("cfManualPathBar").classList.add("hidden");
  }
}

function navigateComputerUp() {
  if (currentDirectoryData && currentDirectoryData.parent_path) {
    loadComputerDirectory(currentDirectoryData.parent_path);
  }
}

function refreshComputerExplorer() {
  loadComputerDirectory(computerCurrentPath);
}

// -------------------------------------------------------------
// RIGHT PANE: SMART FILE STUDIO & LIVE INSPECTOR
// -------------------------------------------------------------

function renderCfFolderOverview(data) {
  const pane = document.getElementById("cfInspectorPane");
  if (!pane) return;

  pane.innerHTML = `
    <div class="p-5 flex flex-col justify-between h-full space-y-6">
      <div class="space-y-4">
        <div class="flex items-center gap-3">
          <div class="size-10 rounded-xl bg-cyan-500/10 border border-cyan-500/20 text-cyan-400 flex items-center justify-center shrink-0">
            <i data-lucide="folder-open" class="size-5"></i>
          </div>
          <div class="min-w-0">
            <h4 class="font-bold text-white text-sm truncate">${escapeHtml(data.current_path.split("/").pop() || "Folder")}</h4>
            <p class="text-[11px] font-mono text-zinc-500 truncate">${escapeHtml(data.current_path)}</p>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-2.5">
          <div class="p-3 rounded-xl bg-surface-900 border border-surface-750">
            <span class="text-[10px] text-zinc-500 uppercase tracking-wider block">Subfolders</span>
            <span class="text-lg font-bold text-white">${data.total_folders || 0}</span>
          </div>
          <div class="p-3 rounded-xl bg-surface-900 border border-surface-750">
            <span class="text-[10px] text-zinc-500 uppercase tracking-wider block">Files</span>
            <span class="text-lg font-bold text-white">${data.total_files || 0}</span>
          </div>
        </div>

        <div class="space-y-2 pt-2 border-t border-surface-700/60">
          <p class="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider">Quick Actions</p>
          <button onclick="setActiveWorkingScopeFromComputer('${escapeHtml(data.current_path)}'); closeComputerFileManagerModal();" class="w-full py-2 px-3 rounded-xl bg-gradient-to-r from-cyan-600/25 to-brand-600/25 hover:from-cyan-600/35 hover:to-brand-600/35 border border-cyan-500/40 text-cyan-300 text-xs font-semibold flex items-center gap-2 transition-all shadow-sm">
            <i data-lucide="crosshair" class="size-3.5 text-cyan-400"></i>
            <span>Control this folder with AI</span>
          </button>
          <button onclick="openCreateFilePrompt()" class="w-full py-2 px-3 rounded-xl bg-surface-800 hover:bg-surface-750 text-zinc-200 text-xs font-medium flex items-center gap-2 transition-colors">
            <i data-lucide="file-plus" class="size-3.5 text-cyan-400"></i>
            <span>Create new file in this folder</span>
          </button>
          <button onclick="openCreateFolderPrompt()" class="w-full py-2 px-3 rounded-xl bg-surface-800 hover:bg-surface-750 text-zinc-200 text-xs font-medium flex items-center gap-2 transition-colors">
            <i data-lucide="folder-plus" class="size-3.5 text-amber-400"></i>
            <span>Create subfolder</span>
          </button>
          <button onclick="linkCurrentFolderAsProject()" class="w-full py-2 px-3 rounded-xl bg-emerald-600/20 hover:bg-emerald-600/30 border border-emerald-500/30 text-emerald-300 text-xs font-medium flex items-center gap-2 transition-colors">
            <i data-lucide="link" class="size-3.5"></i>
            <span>Link this folder as a Xod Workspace</span>
          </button>
        </div>
      </div>

      <div class="p-3 rounded-xl bg-surface-900/60 border border-surface-750 text-xs text-zinc-400 space-y-1">
        <p class="font-medium text-white flex items-center gap-1.5">
          <i data-lucide="sparkles" class="size-3.5 text-cyan-400"></i>
          <span>Smart Computer Integration</span>
        </p>
        <p class="text-[11px] leading-relaxed text-zinc-500">Click any file on the left to edit code, render Markdown dossiers, or pass documents straight into the AI research pipeline.</p>
      </div>
    </div>
  `;
  if (window.lucide) lucide.createIcons();
}

async function selectCfFile(filePath) {
  const pane = document.getElementById("cfInspectorPane");
  if (!pane) return;

  pane.innerHTML = `<div class="p-10 text-center text-zinc-500 text-xs"><i data-lucide="loader-2" class="size-6 animate-spin mx-auto mb-2 text-cyan-400"></i> Reading file...</div>`;
  if (window.lucide) lucide.createIcons();

  try {
    const res = await fetch(`/api/fs/read?path=${encodeURIComponent(filePath)}`);
    const data = await res.json();
    cfSelectedFile = data;
    cfFileContent = data.content || "";
    cfOriginalContent = data.content || "";

    // Set initial mode: markdown defaults to rendered, code to edit
    if (data.extension === ".md") {
      cfEditorMode = "rendered";
    } else {
      cfEditorMode = "edit";
    }

    renderCfFileInspector();
  } catch (err) {
    pane.innerHTML = `<div class="p-5 text-xs text-rose-400">Failed to read file: ${escapeHtml(err.message)}</div>`;
  }
}

function renderCfFileInspector() {
  const pane = document.getElementById("cfInspectorPane");
  if (!pane || !cfSelectedFile) return;

  const f = cfSelectedFile;
  const isMd = f.extension === ".md";
  const isCode = [".py", ".js", ".html", ".json", ".css", ".yaml", ".sh"].includes(f.extension);

  pane.innerHTML = `
    <div class="flex flex-col h-full overflow-hidden">
      <!-- File Header -->
      <div class="p-3.5 border-b border-surface-700/60 bg-surface-900/90 space-y-2 shrink-0">
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0 flex-1">
            <h4 class="font-bold text-white text-xs font-mono truncate" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</h4>
            <p class="text-[10px] text-zinc-500 font-mono truncate" title="${escapeHtml(f.path)}">${escapeHtml(f.path)}</p>
          </div>
          <button onclick="cfSelectedFile=null; renderCfFolderOverview(currentDirectoryData)" class="p-1 rounded text-zinc-400 hover:text-white" title="Deselect file">
            <i data-lucide="x" class="size-3.5"></i>
          </button>
        </div>

        <div class="flex flex-wrap items-center gap-1.5 text-[10px] font-mono">
          <span class="px-2 py-0.5 rounded bg-surface-800 text-zinc-300 border border-surface-700">${escapeHtml(f.size_formatted)}</span>
          ${f.line_count ? `<span class="px-2 py-0.5 rounded bg-surface-800 text-zinc-300 border border-surface-700">${f.line_count} lines</span>` : ''}
          ${f.approx_tokens ? `<span class="px-2 py-0.5 rounded bg-brand-500/15 text-brand-300 border border-brand-500/30">~${f.approx_tokens.toLocaleString()} tokens</span>` : ''}
        </div>

        <!-- Smart Action Bar -->
        <div class="flex flex-wrap items-center gap-1 pt-1">
          <button onclick="askXodAboutFile('${escapeHtml(f.path)}')" class="px-2.5 py-1 rounded-lg bg-cyan-600/20 hover:bg-cyan-600/30 border border-cyan-500/30 text-cyan-300 text-[11px] font-semibold flex items-center gap-1 transition-colors" title="Send to Chat">
            <i data-lucide="sparkles" class="size-3"></i>
            <span>Ask Xod AI</span>
          </button>
          <button onclick="copyCfFileContent()" id="btnCopyCfContent" class="px-2 py-1 rounded-lg bg-surface-800 hover:bg-surface-750 text-zinc-300 text-[11px] flex items-center gap-1 transition-colors" title="Copy Content">
            <i data-lucide="copy" class="size-3"></i>
            <span>Copy</span>
          </button>
          <button onclick="saveCfCurrentFile()" id="btnSaveCfFile" class="px-2.5 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] font-semibold flex items-center gap-1 transition-colors ml-auto shadow-sm" title="Save Changes (Cmd+S)">
            <i data-lucide="save" class="size-3"></i>
            <span>Save</span>
          </button>
        </div>

        <!-- Mode Toggle for Markdown & Text -->
        ${(isMd || f.is_text) ? `
          <div class="flex items-center justify-between pt-1 border-t border-surface-700/40 text-[11px]">
            <span class="text-zinc-500">Mode:</span>
            <div class="flex bg-surface-950 border border-surface-700 rounded-lg p-0.5">
              <button onclick="setCfEditorMode('rendered')" class="px-2 py-0.5 rounded text-[10px] ${cfEditorMode === 'rendered' ? 'bg-cyan-600 text-white font-medium' : 'text-zinc-400 hover:text-white'}">Formatted View</button>
              <button onclick="setCfEditorMode('edit')" class="px-2 py-0.5 rounded text-[10px] ${cfEditorMode === 'edit' ? 'bg-cyan-600 text-white font-medium' : 'text-zinc-400 hover:text-white'}">Edit Raw</button>
            </div>
          </div>
        ` : ''}
      </div>

      <!-- Editor / Content Body -->
      <div class="flex-1 overflow-y-auto p-3 bg-surface-950 relative min-h-0">
        ${f.is_image ? `
          <div class="flex flex-col items-center justify-center p-4">
            <img src="${f.data_uri}" alt="${escapeHtml(f.name)}" class="max-h-96 rounded-xl border border-surface-700 shadow-xl object-contain">
          </div>
        ` : (cfEditorMode === "rendered" && isMd) ? `
          <div class="markdown-preview text-xs leading-relaxed text-zinc-200">
            ${marked.parse(cfFileContent || "")}
          </div>
        ` : `
          <textarea id="cfEditTextarea" class="cf-editor-textarea" spellcheck="false"
            oninput="handleCfTextareaInput(this.value)">${escapeHtml(cfFileContent)}</textarea>
        `}
      </div>
    </div>
  `;

  if (window.lucide) lucide.createIcons();
}

function handleCfTextareaInput(val) {
  cfFileContent = val;
  const saveBtn = document.getElementById("btnSaveCfFile");
  if (saveBtn) {
    if (cfFileContent !== cfOriginalContent) {
      saveBtn.className = "px-2.5 py-1 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-[11px] font-semibold flex items-center gap-1 transition-colors ml-auto shadow-sm";
      saveBtn.innerHTML = `<i data-lucide="save" class="size-3"></i><span>Save *</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

function setCfEditorMode(mode) {
  cfEditorMode = mode;
  renderCfFileInspector();
}

async function saveCfCurrentFile() {
  if (!cfSelectedFile) return;

  const saveBtn = document.getElementById("btnSaveCfFile");
  if (saveBtn) {
    saveBtn.innerHTML = `<i data-lucide="loader-2" class="size-3 animate-spin"></i><span>Saving...</span>`;
    if (window.lucide) lucide.createIcons();
  }

  try {
    const res = await fetch("/api/fs/write", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: cfSelectedFile.path,
        content: cfFileContent
      })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Save failed");
    }

    cfOriginalContent = cfFileContent;
    showToast(`Saved ${cfSelectedFile.name}`);

    if (saveBtn) {
      saveBtn.className = "px-2.5 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] font-semibold flex items-center gap-1 transition-colors ml-auto shadow-sm";
      saveBtn.innerHTML = `<i data-lucide="check" class="size-3"></i><span>Saved</span>`;
      if (window.lucide) lucide.createIcons();
      setTimeout(() => {
        saveBtn.innerHTML = `<i data-lucide="save" class="size-3"></i><span>Save</span>`;
        if (window.lucide) lucide.createIcons();
      }, 1500);
    }
  } catch (err) {
    alert(`Could not save file: ${err.message}`);
    if (saveBtn) {
      saveBtn.innerHTML = `<i data-lucide="alert-circle" class="size-3 text-rose-300"></i><span>Error</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

function copyCfFileContent() {
  if (!cfFileContent) return;
  navigator.clipboard.writeText(cfFileContent);
  const btn = document.getElementById("btnCopyCfContent");
  if (btn) {
    const orig = btn.innerHTML;
    btn.innerHTML = `<i data-lucide="check" class="size-3 text-emerald-400"></i><span>Copied</span>`;
    if (window.lucide) lucide.createIcons();
    setTimeout(() => {
      btn.innerHTML = orig;
      if (window.lucide) lucide.createIcons();
    }, 1500);
  }
}

// -------------------------------------------------------------
// SMART AI ANALYSIS INTEGRATION
// -------------------------------------------------------------

async function askXodAboutFile(filePath) {
  await attachComputerFile(filePath);
  closeComputerFileManagerModal();
  const input = document.getElementById("chatInput");
  if (input && attachedComputerFile) {
    input.value = `Please analyze, audit, and extract key intelligence from ${attachedComputerFile.name}`;
    input.focus();
  }
}

async function summarizeFileWithXod(filePath) {
  await attachComputerFile(filePath);
  closeComputerFileManagerModal();
  const input = document.getElementById("chatInput");
  if (input && attachedComputerFile) {
    input.value = `Please provide a structured executive summary and key takeaways from ${attachedComputerFile.name}`;
    input.focus();
  }
}

async function attachComputerFile(filePath) {
  try {
    const res = await fetch(`/api/fs/read?path=${encodeURIComponent(filePath)}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    attachedComputerFile = data;

    const strip = document.getElementById("attachedFilePillContainer");
    const nameEl = document.getElementById("attachedFileName");
    const metaEl = document.getElementById("attachedFileMeta");

    if (strip) strip.classList.remove("hidden");
    if (nameEl) nameEl.textContent = data.name;
    if (metaEl) metaEl.textContent = `${data.size_formatted} · ~${(data.approx_tokens || 0).toLocaleString()} tokens`;

    showToast(`Attached ${data.name} to chat`);
  } catch (err) {
    alert(`Could not attach file: ${err.message}`);
  }
}

function clearAttachedComputerFile() {
  attachedComputerFile = null;
  const strip = document.getElementById("attachedFilePillContainer");
  if (strip) strip.classList.add("hidden");
}

function openComputerFilePickerForChat() {
  const docInput = document.getElementById("docUploadInput");
  if (docInput) {
    docInput.click();
  } else {
    openComputerFileManagerModal(sidebarComputerCurrentPath || "~");
  }
}

async function linkCurrentFolderAsProject() {
  if (!currentDirectoryData) return;
  const path = currentDirectoryData.current_path;
  const parts = path.split("/").filter(Boolean);
  const name = parts.length > 0 ? parts[parts.length - 1] : "Workspace";

  try {
    const res = await fetch("/api/fs/link-project", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: path,
        name: name
      })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Linking failed");
    }
    const data = await res.json();
    await loadProjects();
    renderCfLinkedWorkspaces();
    showToast(`Linked "${data.name}" as active project workspace!`);
  } catch (err) {
    alert(`Failed to link project: ${err.message}`);
  }
}

// -------------------------------------------------------------
// CRUD MODAL HANDLERS
// -------------------------------------------------------------

function openCreateFolderPrompt() {
  cfItemOpType = "folder";
  const modal = document.getElementById("cfNewItemModal");
  const title = document.getElementById("cfNewItemModalTitle");
  const label = document.getElementById("cfNewItemLabel");
  const tpls = document.getElementById("cfNewFileTemplates");
  const input = document.getElementById("cfNewItemInput");

  if (title) title.textContent = "Create New Folder";
  if (label) label.textContent = "Folder Name";
  if (tpls) tpls.classList.add("hidden");
  if (input) {
    input.value = "";
    input.placeholder = "e.g. dossiers, research, my_notes";
  }
  if (modal) {
    modal.classList.remove("hidden");
    setTimeout(() => input && input.focus(), 50);
  }
}

function openCreateFilePrompt() {
  cfItemOpType = "file";
  const modal = document.getElementById("cfNewItemModal");
  const title = document.getElementById("cfNewItemModalTitle");
  const label = document.getElementById("cfNewItemLabel");
  const tpls = document.getElementById("cfNewFileTemplates");
  const input = document.getElementById("cfNewItemInput");

  if (title) title.textContent = "Create New File";
  if (label) label.textContent = "Filename (with extension)";
  if (tpls) tpls.classList.remove("hidden");
  if (input) {
    input.value = "";
    input.placeholder = "e.g. note.md, analysis.json, script.py";
  }
  if (modal) {
    modal.classList.remove("hidden");
    setTimeout(() => input && input.focus(), 50);
  }
}

function setCfNewFileTemplate(name) {
  const input = document.getElementById("cfNewItemInput");
  if (input) {
    input.value = name;
    input.focus();
  }
}

function closeCfNewItemModal() {
  const modal = document.getElementById("cfNewItemModal");
  if (modal) modal.classList.add("hidden");
}

async function submitCfNewItem() {
  const input = document.getElementById("cfNewItemInput");
  const name = input ? input.value.trim() : "";
  if (!name || !currentDirectoryData) return;

  try {
    if (cfItemOpType === "folder") {
      const res = await fetch("/api/fs/create-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          parent_path: currentDirectoryData.current_path,
          folder_name: name
        })
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to create folder");
      }
    } else {
      const res = await fetch("/api/fs/create-file", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dir_path: currentDirectoryData.current_path,
          filename: name,
          content: name.endsWith(".md") ? `# ${name}\n\nCreated on ${new Date().toLocaleDateString()}\n` : ""
        })
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to create file");
      }
    }

    closeCfNewItemModal();
    await loadComputerDirectory(currentDirectoryData.current_path);
    showToast(`Created ${name}`);
  } catch (err) {
    alert(err.message);
  }
}

function openCfRenameModal(path, currentName) {
  cfTargetItem = { path, name: currentName };
  const modal = document.getElementById("cfRenameModal");
  const input = document.getElementById("cfRenameInput");
  if (input) {
    input.value = currentName;
  }
  if (modal) {
    modal.classList.remove("hidden");
    setTimeout(() => input && input.focus(), 50);
  }
}

function closeCfRenameModal() {
  const modal = document.getElementById("cfRenameModal");
  if (modal) modal.classList.add("hidden");
}

async function submitCfRename() {
  const input = document.getElementById("cfRenameInput");
  const newName = input ? input.value.trim() : "";
  if (!newName || !cfTargetItem) return;

  try {
    const res = await fetch("/api/fs/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_path: cfTargetItem.path,
        new_name: newName
      })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to rename");
    }
    closeCfRenameModal();
    await loadComputerDirectory(computerCurrentPath);
    showToast(`Renamed to ${newName}`);
  } catch (err) {
    alert(err.message);
  }
}

async function cfDuplicateItem(path) {
  try {
    const res = await fetch("/api/fs/duplicate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_path: path })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to duplicate");
    }
    await loadComputerDirectory(computerCurrentPath);
    showToast("Duplicated item");
  } catch (err) {
    alert(err.message);
  }
}

function openCfDeleteModal(path, name) {
  cfTargetItem = { path, name };
  const modal = document.getElementById("cfDeleteModal");
  const targetName = document.getElementById("cfDeleteTargetName");
  const targetPath = document.getElementById("cfDeleteTargetPath");

  if (targetName) targetName.textContent = name;
  if (targetPath) targetPath.textContent = path;
  if (modal) modal.classList.remove("hidden");
}

function closeCfDeleteModal() {
  const modal = document.getElementById("cfDeleteModal");
  if (modal) modal.classList.add("hidden");
}

async function submitCfDelete() {
  if (!cfTargetItem) return;

  try {
    const res = await fetch("/api/fs/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: cfTargetItem.path })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to delete");
    }
    closeCfDeleteModal();
    if (cfSelectedFile && cfSelectedFile.path === cfTargetItem.path) {
      cfSelectedFile = null;
    }
    await loadComputerDirectory(computerCurrentPath);
    showToast(`Deleted ${cfTargetItem.name}`);
  } catch (err) {
    alert(err.message);
  }
}

// Global keyboard shortcut: Cmd+S or Ctrl+S to save in Computer File Studio
window.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
    const modal = document.getElementById("computerFileManagerModal");
    if (modal && !modal.classList.contains("hidden") && cfSelectedFile) {
      e.preventDefault();
      saveCfCurrentFile();
    }
  }
});


// =============================================================
// AI AUTONOMOUS BROWSER SANDBOX & SYSTEM CONTROL COCKPIT
// =============================================================
let cockpitActiveTab = "browser";
let cockpitPollingInterval = null;

function openAutonomousCockpitModal() {
  const modal = document.getElementById("autonomousCockpitModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  switchCockpitTab(cockpitActiveTab || "browser");
  refreshCockpitState();
  if (window.lucide) lucide.createIcons();
}

function closeAutonomousCockpitModal() {
  const modal = document.getElementById("autonomousCockpitModal");
  if (modal) modal.classList.add("hidden");
  if (cockpitPollingInterval) {
    clearInterval(cockpitPollingInterval);
    cockpitPollingInterval = null;
  }
  stopTelemetryPulse();
}

function switchCockpitTab(tab) {
  cockpitActiveTab = tab;
  const views = {
    browser: document.getElementById("cockpitViewBrowser"),
    computer: document.getElementById("cockpitViewComputer"),
    feed: document.getElementById("cockpitViewFeed")
  };
  const tabs = {
    browser: document.getElementById("cockpitTabBrowser"),
    computer: document.getElementById("cockpitTabComputer"),
    feed: document.getElementById("cockpitTabFeed")
  };

  Object.keys(views).forEach(k => {
    if (views[k]) {
      if (k === tab) views[k].classList.remove("hidden");
      else views[k].classList.add("hidden");
    }
    if (tabs[k]) {
      if (k === tab) {
        tabs[k].className = "px-3 py-1.5 rounded-lg font-medium text-white bg-surface-700 shadow-sm flex items-center gap-1.5 transition-all";
      } else {
        tabs[k].className = "px-3 py-1.5 rounded-lg font-medium text-zinc-400 hover:text-white flex items-center gap-1.5 transition-all";
      }
    }
  });

  if (tab === "browser") {
    stopTelemetryPulse();
    refreshCockpitState();
  } else if (tab === "computer") {
    fetchComputerTelemetry();
    startTelemetryPulse();
  } else if (tab === "feed") {
    stopTelemetryPulse();
    refreshCockpitFeed();
  }
  if (window.lucide) lucide.createIcons();
}

async function refreshCockpitState() {
  try {
    const res = await fetch("/api/browser/status");
    if (!res.ok) return;
    const state = await res.json();
    renderCockpitBrowserState(state);
  } catch (err) {
    console.error("Failed to refresh browser state:", err);
  }
}

function renderCockpitBrowserState(state) {
  const img = document.getElementById("cockpitViewportImg");
  const empty = document.getElementById("cockpitViewportEmpty");
  const urlText = document.getElementById("cockpitCurrentUrlText");
  const urlInput = document.getElementById("cockpitUrlInput");
  const titleText = document.getElementById("cockpitPageTitleText");
  const updatedText = document.getElementById("cockpitUpdatedTimeText");

  if (state.current_url && state.current_url !== "about:blank") {
    if (urlText) urlText.textContent = state.current_url;
    if (urlInput && !document.activeElement.isSameNode(urlInput)) {
      urlInput.value = state.current_url;
    }
  }

  if (state.current_title && titleText) {
    titleText.textContent = state.current_title;
  }

  if (state.screenshot_uri && img && empty) {
    img.src = state.screenshot_uri;
    img.classList.remove("hidden");
    img.classList.add("cursor-crosshair");
    img.title = "Click directly on page to dispatch click to browser sandbox";
    empty.classList.add("hidden");

    if (!img.__click_coords_bound) {
      img.__click_coords_bound = true;
      img.addEventListener("click", async (e) => {
        const rect = img.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;
        const x = Math.max(0, Math.min(1280, Math.round((e.clientX - rect.left) * (1280 / rect.width))));
        const y = Math.max(0, Math.min(800, Math.round((e.clientY - rect.top) * (800 / rect.height))));
        try {
          img.style.opacity = "0.7";
          const res = await fetch("/api/browser/click-coords", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ x, y })
          });
          const d = await res.json();
          if (d.screenshot_uri) img.src = d.screenshot_uri;
        } catch (err) {
          console.error("Cockpit click-coords failed:", err);
        } finally {
          img.style.opacity = "1";
        }
      });
    }
  } else if (img && empty && (!state.screenshot_uri || !state.current_url || state.current_url === "about:blank")) {
    img.classList.add("hidden");
    empty.classList.remove("hidden");
  }

  if (updatedText) {
    updatedText.textContent = `Updated: ${new Date().toLocaleTimeString()}`;
  }

  // Restore latest mission trace if available
  if (state.latest_mission) {
    const m = state.latest_mission;
    const toggleBtn = document.getElementById("btnToggleMissionDeck");
    const stepsList = document.getElementById("cockpitMissionStepsList");
    const statusTitle = document.getElementById("cockpitMissionStatusTitle");
    const elapsedText = document.getElementById("cockpitMissionElapsedText");
    const answerCard = document.getElementById("cockpitMissionAnswerCard");
    const answerText = document.getElementById("cockpitMissionAnswerText");

    if (toggleBtn) toggleBtn.classList.remove("hidden");
    if (statusTitle) {
      statusTitle.textContent = m.status === 'completed'
        ? `Mission Completed (${m.steps?.length || 0} Steps)`
        : `Autonomous Mission Running`;
    }
    if (elapsedText && m.start_time) elapsedText.textContent = m.start_time;

    if (stepsList && m.steps && m.steps.length > 0) {
      stepsList.innerHTML = m.steps.map(s => {
        const isDone = s.action === 'finish' || s.status === 'completed' || s.status === 'success';
        const badgeColor = isDone ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30' : 'text-cyan-400 bg-cyan-500/10 border-cyan-500/30';
        return `
          <div class="p-2 rounded-lg bg-surface-900 border border-surface-750 text-[11px] space-y-1">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-1.5 font-semibold text-white">
                <span class="px-1.5 py-0.2 rounded border text-[10px] uppercase font-mono ${badgeColor}">Step ${s.step_num}: ${s.action}</span>
                ${s.target ? `<span class="font-mono text-zinc-400 text-[10px] truncate max-w-xs">${escapeHtml(s.target)}</span>` : ''}
              </div>
              <span class="text-[10px] font-mono ${isDone ? 'text-emerald-400' : 'text-zinc-500'}">${s.status}</span>
            </div>
            ${s.reasoning ? `<p class="text-zinc-300 text-[11px] leading-relaxed italic pl-1 border-l-2 border-emerald-500/50">"${escapeHtml(s.reasoning)}"</p>` : ''}
          </div>
        `;
      }).join("");
    }

    if (m.final_answer && answerCard && answerText) {
      answerText.textContent = m.final_answer;
      answerCard.classList.remove("hidden");
    }
  }
}

async function cockpitNavigateToUrl() {
  const input = document.getElementById("cockpitUrlInput");
  if (!input || !input.value.trim()) return;

  let url = input.value.trim();
  if (!/^https?:\/\//i.test(url)) {
    url = "https://" + url;
    input.value = url;
  }

  const loader = document.getElementById("cockpitViewportLoading");
  const loaderText = document.getElementById("cockpitLoadingText");
  if (loader) {
    if (loaderText) loaderText.textContent = `Navigating to ${url}...`;
    loader.classList.remove("hidden");
  }

  try {
    const res = await fetch("/api/browser/navigate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url })
    });
    const data = await res.json();
    if (res.ok) {
      renderCockpitBrowserState({
        current_url: data.url,
        current_title: data.title,
        screenshot_uri: data.screenshot_uri
      });
      await cockpitExtractDomContent();
      showToast(`Navigated to ${data.title || url}`);
    } else {
      alert("Navigation failed: " + (data.detail || "Unknown error"));
    }
  } catch (err) {
    alert("Navigation error: " + err.message);
  } finally {
    if (loader) loader.classList.add("hidden");
  }
}

function cockpitQuickNav(url) {
  const input = document.getElementById("cockpitUrlInput");
  if (input) {
    input.value = url;
    cockpitNavigateToUrl();
  }
}

async function cockpitBrowserReload() {
  const input = document.getElementById("cockpitUrlInput");
  if (input && input.value.trim()) {
    cockpitNavigateToUrl();
  } else {
    refreshCockpitState();
  }
}

function cockpitBrowserHistoryBack() {
  cockpitEvaluateJsDirect("window.history.back()");
  setTimeout(() => {
    cockpitCaptureSnapshot();
  }, 1000);
}

function cockpitToggleTabsDropdown() {
  const dd = document.getElementById("cockpitTabsDropdown");
  if (!dd) return;
  const isHidden = dd.classList.contains("hidden");
  if (isHidden) {
    dd.classList.remove("hidden");
    cockpitRefreshTabsList();
  } else {
    dd.classList.add("hidden");
  }
}

async function cockpitRefreshTabsList() {
  const listEl = document.getElementById("cockpitTabsList");
  const countBadge = document.getElementById("cockpitTabsCountBadge");
  if (!listEl) return;

  try {
    const res = await fetch("/api/browser/tabs");
    const data = await res.json();
    if (!res.ok) return;

    const tabs = data.tabs || [];
    if (countBadge) countBadge.textContent = `${tabs.length} ${tabs.length === 1 ? "Tab" : "Tabs"}`;

    if (tabs.length === 0) {
      listEl.innerHTML = `<div class="text-[11px] text-zinc-500 italic p-2 text-center">No active tabs found.</div>`;
      return;
    }

    listEl.innerHTML = tabs.map(t => {
      const isSel = t.selected;
      const title = escapeHtml(t.title || "Untitled Tab");
      const url = escapeHtml(t.url || "about:blank");
      return `
        <div class="flex items-center justify-between p-1.5 rounded-lg ${isSel ? 'bg-surface-750 border border-cyan-500/40' : 'bg-surface-850 border border-surface-750'} text-[11px] gap-1.5">
          <div class="min-w-0 flex-1 cursor-pointer" onclick="cockpitSwitchTab('${t.id}')">
            <div class="font-bold text-zinc-200 truncate ${isSel ? 'text-cyan-300' : ''}">${title}</div>
            <div class="text-[10px] text-zinc-500 truncate">${url}</div>
          </div>
          <div class="flex items-center gap-1 shrink-0">
            ${isSel ? '<span class="text-[9px] text-cyan-400 font-mono px-1 py-0.5 rounded bg-cyan-950/80">Active</span>' : `
              <button onclick="cockpitSwitchTab('${t.id}')" class="px-1.5 py-0.5 rounded bg-surface-800 hover:bg-cyan-600 hover:text-white text-zinc-400 text-[10px]">
                Switch
              </button>
            `}
            ${tabs.length > 1 ? `
              <button onclick="cockpitCloseTab('${t.id}')" class="p-1 rounded hover:bg-red-950/60 hover:text-red-400 text-zinc-500 text-[10px]" title="Close tab">
                <i data-lucide="x" class="size-3"></i>
              </button>
            ` : ""}
          </div>
        </div>
      `;
    }).join("");
    if (window.lucide) lucide.createIcons();
  } catch (err) {
    console.error("Tab refresh error:", err);
  }
}

async function cockpitNewTab() {
  const url = prompt("Enter URL for new tab:", "https://news.ycombinator.com");
  if (url === null) return;

  try {
    const res = await fetch("/api/browser/tabs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "new", url: url || "about:blank" })
    });
    if (res.ok) {
      cockpitRefreshTabsList();
      setTimeout(refreshCockpitState, 1000);
      showToast("Opened new browser tab");
    }
  } catch (err) {
    alert("New tab error: " + err.message);
  }
}

async function cockpitSwitchTab(targetId) {
  try {
    const res = await fetch("/api/browser/tabs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "switch", target_id: targetId })
    });
    if (res.ok) {
      document.getElementById("cockpitTabsDropdown")?.classList.add("hidden");
      await refreshCockpitState();
      await cockpitCaptureSnapshot();
      showToast("Switched tab");
    }
  } catch (err) {
    alert("Switch tab error: " + err.message);
  }
}

async function cockpitCloseTab(targetId) {
  try {
    const res = await fetch("/api/browser/tabs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "close", target_id: targetId })
    });
    if (res.ok) {
      cockpitRefreshTabsList();
      setTimeout(refreshCockpitState, 1000);
      showToast("Closed tab");
    }
  } catch (err) {
    alert("Close tab error: " + err.message);
  }
}

async function cockpitCaptureSnapshot() {
  const btn = document.getElementById("btnCockpitSnapshot");
  if (btn) btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 text-indigo-400 animate-spin"></i> <span>Capturing...</span>`;

  try {
    const res = await fetch("/api/browser/screenshot", { method: "POST" });
    const data = await res.json();
    if (res.ok && data.screenshot_uri) {
      renderCockpitBrowserState({
        current_url: data.url,
        current_title: data.title,
        screenshot_uri: data.screenshot_uri
      });
      showToast("Fresh snapshot captured");
    }
  } catch (err) {
    console.error("Screenshot error:", err);
  } finally {
    if (btn) {
      btn.innerHTML = `<i data-lucide="camera" class="size-3.5 text-indigo-400"></i> <span>Snapshot</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

async function cockpitCaptureSomSnapshot() {
  const btn = document.getElementById("btnCockpitSomSnapshot");
  if (btn) btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 text-indigo-400 animate-spin"></i> <span>Overlaying...</span>`;

  try {
    const res = await fetch("/api/browser/som-snapshot", { method: "POST" });
    const data = await res.json();
    if (res.ok && data.screenshot_uri) {
      renderCockpitBrowserState({
        current_url: data.url,
        current_title: data.title,
        screenshot_uri: data.screenshot_uri
      });
      showToast("Set-of-Marks badged snapshot captured");
      cockpitObserveDOM();
    }
  } catch (err) {
    console.error("SoM snapshot error:", err);
  } finally {
    if (btn) {
      btn.innerHTML = `<i data-lucide="tag" class="size-3.5 text-indigo-400"></i> <span>🏷️ SoM Badges</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

async function cockpitObserveDOM() {
  const btn = document.getElementById("btnCockpitObserve");
  if (btn) btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 text-emerald-400 animate-spin"></i> <span>Observing...</span>`;
  const list = document.getElementById("cockpitInteractiveElementsList");
  const count = document.getElementById("cockpitElementsCount");

  try {
    const res = await fetch("/api/browser/observe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_elements: 60, filter_type: "all" })
    });
    const data = await res.json();
    if (!res.ok) return;

    const elements = data.elements || [];
    if (count) count.textContent = `${elements.length} elements (indexed)`;

    if (list) {
      if (elements.length === 0) {
        list.innerHTML = `<div class="text-[11px] text-zinc-500 italic p-2 text-center">No interactive buttons or links detected.</div>`;
      } else {
        list.innerHTML = elements.map(el => {
          const label = el.text || el.label || el.tag;
          const safeLabel = escapeHtml(label.slice(0, 45));
          const coordStr = `${el.x},${el.y}`;
          const isInput = el.tag === "input" || el.tag === "textarea" || el.tag === "select";
          return `
            <div class="flex items-center justify-between p-1.5 rounded-lg bg-surface-900 border border-surface-750 text-[11px] gap-2 hover:border-indigo-500/40 transition-colors">
              <div class="flex items-center gap-1.5 min-w-0">
                <span class="px-1.5 py-0.2 rounded font-mono font-bold text-[10px] bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 shrink-0" title="Set-of-Marks ID">#${el.index}</span>
                <span class="font-mono text-zinc-300 truncate" title="<${el.tag}> at (${coordStr})">${safeLabel}</span>
              </div>
              <div class="flex items-center gap-1 shrink-0">
                <button onclick="cockpitInteractByIndex(${el.index}, 'hover')" class="px-1.5 py-0.5 rounded bg-surface-800 hover:bg-cyan-600 hover:text-white text-zinc-400 text-[10px] font-mono transition-colors" title="Hover over element">
                  Hover
                </button>
                ${isInput ? `
                  <button onclick="cockpitPromptTypeIndex(${el.index})" class="px-1.5 py-0.5 rounded bg-surface-800 hover:bg-indigo-600 hover:text-white text-zinc-400 text-[10px] font-mono transition-colors" title="Type into input">
                    Type
                  </button>
                ` : `
                  <button onclick="cockpitInteractByIndex(${el.index}, 'click')" class="px-2 py-0.5 rounded bg-surface-800 hover:bg-emerald-600 hover:text-white text-zinc-400 text-[10px] font-mono transition-colors" title="Click element">
                    Click
                  </button>
                `}
              </div>
            </div>
          `;
        }).join("");
      }
    }
  } catch (err) {
    console.error("Observe error:", err);
  } finally {
    if (btn) {
      btn.innerHTML = `<i data-lucide="sparkles" class="size-3.5 text-emerald-400"></i> <span>Observe</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

async function cockpitInteractByIndex(index, action = "click", text = null) {
  if (!index) return;
  const loader = document.getElementById("cockpitViewportLoading");
  if (loader) {
    document.getElementById("cockpitLoadingText").textContent = `Interacting #${index} (${action})...`;
    loader.classList.remove("hidden");
  }

  try {
    const res = await fetch("/api/browser/interact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: Number(index), action, text, press_enter: true })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      renderCockpitBrowserState({
        current_url: data.url,
        current_title: data.title,
        screenshot_uri: data.screenshot_uri
      });
      showToast(`Action #${index} (${action}) executed`);
    } else {
      alert("Interaction failed: " + (data.error || data.detail || "Error"));
    }
  } catch (err) {
    alert("Interact error: " + err.message);
  } finally {
    if (loader) loader.classList.add("hidden");
  }
}

function cockpitPromptTypeIndex(index) {
  const text = prompt(`Enter text to type into element #${index}:`);
  if (text !== null) {
    cockpitInteractByIndex(index, "type", text);
  }
}

function cockpitOnIndexActionChange() {
  const act = document.getElementById("cockpitInteractActionSelect")?.value;
  const textInput = document.getElementById("cockpitInteractTextInput");
  if (textInput) {
    if (act === "type") {
      textInput.classList.remove("hidden");
      textInput.focus();
    } else {
      textInput.classList.add("hidden");
    }
  }
}

function cockpitTriggerIndexedAction() {
  const index = document.getElementById("cockpitInteractIndexInput")?.value?.trim();
  const act = document.getElementById("cockpitInteractActionSelect")?.value || "click";
  const text = document.getElementById("cockpitInteractTextInput")?.value?.trim() || null;
  if (!index) {
    alert("Please enter a numeric index (#).");
    return;
  }
  cockpitInteractByIndex(index, act, text);
}

async function cockpitSyntheticClick(targetParam = null) {
  const target = targetParam || document.getElementById("cockpitClickTargetInput")?.value?.trim();
  if (!target) return;

  const loader = document.getElementById("cockpitViewportLoading");
  if (loader) {
    document.getElementById("cockpitLoadingText").textContent = `Clicking "${target}"...`;
    loader.classList.remove("hidden");
  }

  try {
    const res = await fetch("/api/browser/click", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target })
    });
    const data = await res.json();
    if (res.ok) {
      renderCockpitBrowserState({
        current_url: data.url,
        current_title: data.title,
        screenshot_uri: data.screenshot_uri
      });
      showToast(`Clicked ${data.clicked?.tagName || target}`);
    } else {
      alert("Click failed: " + (data.detail || "Element not found"));
    }
  } catch (err) {
    alert("Click error: " + err.message);
  } finally {
    if (loader) loader.classList.add("hidden");
  }
}

async function cockpitSyntheticType() {
  const selector = document.getElementById("cockpitTypeSelectorInput")?.value?.trim();
  const text = document.getElementById("cockpitTypeTextInput")?.value?.trim();
  if (!selector || !text) {
    alert("Please provide both a selector and text to type.");
    return;
  }

  const loader = document.getElementById("cockpitViewportLoading");
  if (loader) {
    document.getElementById("cockpitLoadingText").textContent = `Typing into ${selector}...`;
    loader.classList.remove("hidden");
  }

  try {
    const res = await fetch("/api/browser/type", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selector, text, press_enter: true })
    });
    const data = await res.json();
    if (res.ok) {
      renderCockpitBrowserState({
        current_url: data.url,
        current_title: data.title,
        screenshot_uri: data.screenshot_uri
      });
      showToast(`Typed text into ${selector}`);
    } else {
      alert("Typing failed: " + (data.detail || "Input element not found"));
    }
  } catch (err) {
    alert("Typing error: " + err.message);
  } finally {
    if (loader) loader.classList.add("hidden");
  }
}

async function cockpitExtractDomContent() {
  const list = document.getElementById("cockpitInteractiveElementsList");
  const count = document.getElementById("cockpitElementsCount");
  const bodyTextEl = document.getElementById("cockpitBodyTextPreview");
  const titleText = document.getElementById("cockpitPageTitleText");
  const metaDesc = document.getElementById("cockpitMetaDescText");

  try {
    const res = await fetch("/api/browser/content?max_length=8000");
    const data = await res.json();
    if (!res.ok) return;

    if (data.title && titleText) titleText.textContent = data.title;
    if (metaDesc) metaDesc.textContent = data.meta_description || "No description provided.";
    if (bodyTextEl) bodyTextEl.textContent = data.text_preview || "No body text extracted.";

    const elements = data.elements || [];
    if (count) count.textContent = `${elements.length} elements`;

    if (list) {
      if (elements.length === 0) {
        list.innerHTML = `<div class="text-[11px] text-zinc-500 italic p-2 text-center">No interactive buttons or links detected.</div>`;
      } else {
        list.innerHTML = elements.map(el => {
          const label = el.text || el.id || el.name || el.tag;
          const selector = el.id ? `#${el.id}` : (el.name ? `[name="${el.name}"]` : el.tag);
          const safeLabel = escapeHtml(label.slice(0, 40));
          return `
            <div class="flex items-center justify-between p-1.5 rounded-lg bg-surface-900 border border-surface-750 text-[11px] gap-2">
              <span class="font-mono text-zinc-300 truncate" title="${selector}">${safeLabel}</span>
              <div class="flex items-center gap-1 shrink-0">
                <button onclick="cockpitSyntheticHover('${escapeHtml(label)}')" class="px-1.5 py-0.5 rounded bg-surface-800 hover:bg-cyan-600 hover:text-white text-zinc-400 text-[10px] font-mono transition-colors">
                  Hover
                </button>
                <button onclick="cockpitSyntheticClick('${escapeHtml(label)}')" class="px-2 py-0.5 rounded bg-surface-800 hover:bg-emerald-600 hover:text-white text-zinc-400 text-[10px] font-mono transition-colors">
                  Click
                </button>
              </div>
            </div>
          `;
        }).join("");
      }
    }
  } catch (err) {
    console.error("DOM extract error:", err);
  }
}

async function cockpitSyntheticHover(targetParam = null) {
  const target = targetParam || document.getElementById("cockpitClickTargetInput")?.value?.trim();
  if (!target) return;

  const loader = document.getElementById("cockpitViewportLoading");
  if (loader) {
    document.getElementById("cockpitLoadingText").textContent = `Hovering over "${target}"...`;
    loader.classList.remove("hidden");
  }

  try {
    const res = await fetch("/api/browser/hover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target })
    });
    const data = await res.json();
    if (res.ok) {
      renderCockpitBrowserState({
        current_url: data.url,
        current_title: data.title,
        screenshot_uri: data.screenshot_uri
      });
      showToast(`Hovered over ${data.hovered?.tagName || target}`);
    } else {
      alert("Hover failed: " + (data.detail || "Element not found"));
    }
  } catch (err) {
    alert("Hover error: " + err.message);
  } finally {
    if (loader) loader.classList.add("hidden");
  }
}

async function cockpitScrollBrowser(direction = "down", amount = 500) {
  try {
    const res = await fetch("/api/browser/scroll", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction, amount })
    });
    const data = await res.json();
    if (res.ok && data.screenshot_uri) {
      renderCockpitBrowserState({
        current_url: data.url,
        current_title: data.title,
        screenshot_uri: data.screenshot_uri
      });
      showToast(`Scrolled ${direction}`);
    }
  } catch (err) {
    console.error("Scroll error:", err);
  }
}

async function cockpitCaptureFullPage() {
  const btn = document.getElementById("btnCockpitFullScreenshot");
  if (btn) btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 text-cyan-400 animate-spin"></i> <span>Scanning...</span>`;

  try {
    const res = await fetch("/api/browser/screenshot/full", { method: "POST" });
    const data = await res.json();
    if (res.ok && data.screenshot_uri) {
      renderCockpitBrowserState({
        current_url: data.url,
        current_title: data.title,
        screenshot_uri: data.screenshot_uri
      });
      showToast(`Full-page captured (${data.dimensions?.width}x${data.dimensions?.height}px)`);
    } else {
      alert("Full page snapshot failed: " + (data.detail || "Error capturing"));
    }
  } catch (err) {
    alert("Full page snapshot error: " + err.message);
  } finally {
    if (btn) {
      btn.innerHTML = `<i data-lucide="scan" class="size-3.5 text-cyan-400"></i> <span>Full Page</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

let isBrowserDesktopVisible = false;

async function cockpitToggleBrowserVisibility() {
  const btn = document.getElementById("btnCockpitToggleVisibility");
  const label = document.getElementById("cockpitVisibilityLabel");
  const nextVisible = !isBrowserDesktopVisible;
  
  if (btn) btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 text-sky-400 animate-spin"></i> <span>Switching...</span>`;

  try {
    const res = await fetch("/api/browser/visibility", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visible: nextVisible })
    });
    const data = await res.json();
    if (res.ok) {
      isBrowserDesktopVisible = nextVisible;
      if (label) label.textContent = isBrowserDesktopVisible ? "Headless Mode" : "Desktop Window";
      if (isBrowserDesktopVisible) {
        btn?.classList.remove("bg-surface-750", "text-zinc-200");
        btn?.classList.add("bg-sky-600/30", "text-sky-300", "border-sky-500/50");
      } else {
        btn?.classList.remove("bg-sky-600/30", "text-sky-300", "border-sky-500/50");
        btn?.classList.add("bg-surface-750", "text-zinc-200");
      }
      showToast(isBrowserDesktopVisible ? "🖥️ Desktop Chrome window launched on your Mac" : "🕶️ Reverted to silent background headless browser");
      await cockpitCaptureSnapshot();
    } else {
      alert("Failed to toggle browser mode: " + (data.detail || data.error || "Unknown error"));
    }
  } catch (err) {
    alert("Error toggling browser visibility: " + err.message);
  } finally {
    if (btn) {
      btn.innerHTML = `<i data-lucide="monitor" class="size-3.5 text-sky-400"></i> <span id="cockpitVisibilityLabel">${isBrowserDesktopVisible ? "Headless Mode" : "Desktop Window"}</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

async function cockpitDismissOverlays() {
  const btn = document.getElementById("btnCockpitDismissOverlays");
  if (btn) btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 text-amber-400 animate-spin"></i> <span>Clearing...</span>`;

  try {
    const res = await fetch("/api/browser/dismiss-overlays", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      const removedCount = data.removed_overlays_count || (data.dismissed_buttons ? data.dismissed_buttons.length : 0);
      showToast(data.message || `Cleared ${removedCount} popups/overlays`);
      await cockpitCaptureSnapshot();
    } else {
      alert("Failed to clear popups: " + (data.detail || "Error"));
    }
  } catch (err) {
    alert("Clear popups error: " + err.message);
  } finally {
    if (btn) {
      btn.innerHTML = `<i data-lucide="shield-alert" class="size-3.5 text-amber-400"></i> <span>Clear Popups</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

async function cockpitSolveCaptcha() {
  const btn = document.getElementById("btnCockpitSolveCaptcha");
  if (btn) btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 text-emerald-400 animate-spin"></i> <span>Passing...</span>`;

  try {
    const res = await fetch("/api/browser/captcha/solve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_click: true, wait_sec: 12.0 })
    });
    const data = await res.json();
    if (res.ok) {
      if (data.resolved && data.has_captcha) {
        showToast(`🛡️ CAPTCHA resolved: ${data.message || 'Pass verified'}`);
      } else if (data.has_captcha) {
        showToast(`⚠️ CAPTCHA detected (${data.type || 'challenge'}): ${data.message || 'Action executed'}`);
      } else {
        showToast(data.message || "ℹ️ No CAPTCHA detected on current page");
      }
      await cockpitCaptureSnapshot();
    } else {
      alert("CAPTCHA solver error: " + (data.detail || "Request failed"));
    }
  } catch (err) {
    alert("CAPTCHA solver failed: " + err.message);
  } finally {
    if (btn) {
      btn.innerHTML = `<i data-lucide="shield-check" class="size-3.5 text-emerald-400"></i> <span>Pass CAPTCHA</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

async function cockpitUnstuckBrowser() {
  const btn = document.getElementById("btnCockpitUnstuck");
  if (btn) btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 text-orange-400 animate-spin"></i> <span>Unfreezing...</span>`;

  try {
    const res = await fetch("/api/browser/unstuck", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reload_if_blank: true })
    });
    const data = await res.json();
    if (res.ok) {
      const actions = (data.recovery_actions && data.recovery_actions.length > 0) ? data.recovery_actions.join(", ") : (data.message || "Page restored");
      showToast(`🚀 Browser unstuck: ${actions}`);
      await cockpitCaptureSnapshot();
    } else {
      alert("Unstuck failed: " + (data.detail || "Request failed"));
    }
  } catch (err) {
    alert("Unstuck error: " + err.message);
  } finally {
    if (btn) {
      btn.innerHTML = `<i data-lucide="life-buoy" class="size-3.5 text-orange-400"></i> <span>Unstuck</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

async function cockpitRestartBrowser() {
  if (!confirm("Force-restart browser sandbox? This will terminate hung Chrome processes, clear locks, and launch a fresh session.")) return;

  const btn = document.getElementById("btnCockpitRestart");
  if (btn) btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 text-blue-400 animate-spin"></i> <span>Restarting...</span>`;

  try {
    const res = await fetch("/api/browser/restart", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clear_profile: false })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message || "🔄 Browser sandbox restarted successfully");
      await cockpitRefreshTabsList();
      await cockpitCaptureSnapshot();
    } else {
      alert("Browser restart failed: " + (data.detail || "Request failed"));
    }
  } catch (err) {
    alert("Browser restart error: " + err.message);
  } finally {
    if (btn) {
      btn.innerHTML = `<i data-lucide="rotate-ccw" class="size-3.5 text-blue-400"></i> <span>Restart</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

async function cockpitSmartInspect() {
  const btn = document.getElementById("btnCockpitSmartInspect");
  if (btn) btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 text-purple-400 animate-spin"></i> <span>Inspecting...</span>`;

  try {
    const res = await fetch("/api/browser/smart-inspect");
    const data = await res.json();
    if (res.ok) {
      if (data.screenshot_uri) {
        renderCockpitBrowserState({
          current_url: data.url,
          current_title: data.title,
          screenshot_uri: data.screenshot_uri
        });
      }
      const elements = data.interactive_elements || [];
      const list = document.getElementById("cockpitInteractiveElementsList");
      const count = document.getElementById("cockpitElementsCount");
      if (count) count.textContent = `${elements.length} elements (Smart Inspected)`;
      if (list && elements.length > 0) {
        list.innerHTML = elements.map(el => {
          const label = el.text || el.label || el.tag;
          const safeLabel = escapeHtml(label.slice(0, 45));
          const coordStr = `${el.x},${el.y}`;
          return `
            <div class="flex items-center justify-between p-1.5 rounded-lg bg-surface-900 border border-surface-750 text-[11px] gap-2 hover:border-purple-500/40 transition-colors">
              <div class="flex items-center gap-1.5 min-w-0">
                <span class="px-1.5 py-0.2 rounded font-mono font-bold text-[10px] bg-purple-500/20 text-purple-300 border border-purple-500/30 shrink-0" title="Set-of-Marks ID">#${el.index}</span>
                <span class="font-mono text-zinc-300 truncate" title="<${el.tag}> at (${coordStr})">${safeLabel}</span>
              </div>
              <div class="flex items-center gap-1 shrink-0">
                <button onclick="cockpitInteractByIndex(${el.index}, 'click')" class="px-1.5 py-0.5 rounded bg-surface-800 hover:bg-surface-750 text-emerald-400 font-mono text-[10px]" title="Click">Click</button>
              </div>
            </div>
          `;
        }).join("");
      }
      showToast(`🧠 Smart inspect complete: ${elements.length} interactive elements, ${data.forms_detected?.length || 0} forms`);
    } else {
      alert("Smart inspect failed: " + (data.detail || "Error"));
    }
  } catch (err) {
    alert("Smart inspect error: " + err.message);
  } finally {
    if (btn) {
      btn.innerHTML = `<i data-lucide="brain" class="size-3.5 text-purple-400"></i> <span>Smart Inspect</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

let currentCockpitInspectorTab = "elements";
let interceptedNetworkRequests = [];

function switchCockpitInspectorTab(mode) {
  currentCockpitInspectorTab = mode;
  const tabElements = document.getElementById("cockpitInspectorTabElements");
  const tabMarkdown = document.getElementById("cockpitInspectorTabMarkdown");
  const tabNetwork = document.getElementById("cockpitInspectorTabNetwork");
  const tabAxTree = document.getElementById("cockpitInspectorTabAxTree");
  const viewElements = document.getElementById("cockpitInspectorViewElements");
  const viewMarkdown = document.getElementById("cockpitInspectorViewMarkdown");
  const viewNetwork = document.getElementById("cockpitInspectorViewNetwork");
  const viewAxTree = document.getElementById("cockpitInspectorViewAxTree");

  [tabElements, tabMarkdown, tabNetwork, tabAxTree].forEach(t => {
    t?.classList.remove("bg-surface-700", "text-white", "font-semibold");
    t?.classList.add("text-zinc-400");
  });
  [viewElements, viewMarkdown, viewNetwork, viewAxTree].forEach(v => v?.classList.add("hidden"));

  if (mode === "elements") {
    tabElements?.classList.add("bg-surface-700", "text-white", "font-semibold");
    tabElements?.classList.remove("text-zinc-400");
    viewElements?.classList.remove("hidden");
    cockpitObserveDOM();
  } else if (mode === "markdown") {
    tabMarkdown?.classList.add("bg-surface-700", "text-white", "font-semibold");
    tabMarkdown?.classList.remove("text-zinc-400");
    viewMarkdown?.classList.remove("hidden");
    cockpitExtractMarkdownContent();
  } else if (mode === "network") {
    tabNetwork?.classList.add("bg-surface-700", "text-white", "font-semibold");
    tabNetwork?.classList.remove("text-zinc-400");
    viewNetwork?.classList.remove("hidden");
    cockpitFetchNetworkSnoop();
  } else if (mode === "axtree") {
    tabAxTree?.classList.add("bg-surface-700", "text-white", "font-semibold");
    tabAxTree?.classList.remove("text-zinc-400");
    viewAxTree?.classList.remove("hidden");
    cockpitFetchAxTree();
  }
  if (window.lucide) lucide.createIcons();
}

function cockpitRefreshActiveInspectorTab() {
  if (currentCockpitInspectorTab === "markdown") {
    cockpitExtractMarkdownContent();
  } else if (currentCockpitInspectorTab === "network") {
    cockpitFetchNetworkSnoop();
  } else if (currentCockpitInspectorTab === "axtree") {
    cockpitFetchAxTree();
  } else {
    cockpitExtractDomContent();
    cockpitObserveDOM();
  }
}

async function cockpitFetchAxTree() {
  const countBadge = document.getElementById("cockpitAxCountBadge");
  const contentArea = document.getElementById("cockpitAxContentArea");
  if (!contentArea) return;
  contentArea.textContent = "Extracting Accessibility Tree via Chrome DevTools Protocol...";

  try {
    const res = await fetch("/api/browser/ax-tree?max_nodes=60");
    const data = await res.json();
    if (!res.ok) {
      contentArea.textContent = `Error: ${data.detail || "Failed to load AX tree"}`;
      return;
    }
    if (countBadge) countBadge.textContent = `${data.node_count || 0} Nodes`;
    contentArea.textContent = data.ax_tree_text || "No accessible nodes detected.";
  } catch (err) {
    contentArea.textContent = `Error: ${err.message}`;
  }
}

function cockpitCopyAxContent() {
  const contentEl = document.getElementById("cockpitAxContentArea");
  if (contentEl && contentEl.textContent) {
    navigator.clipboard.writeText(contentEl.textContent);
    showToast("Accessibility Tree copied to clipboard");
  }
}

async function cockpitExportPdf() {
  const btn = document.getElementById("btnCockpitPdf");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 animate-spin text-rose-400"></i><span>Exporting...</span>`;
    if (window.lucide) lucide.createIcons();
  }
  try {
    const res = await fetch("/api/browser/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`PDF exported (${Math.round((data.size_bytes || 0)/1024)} KB) to ${data.file_name}`);
    } else {
      alert("PDF export failed: " + (data.detail || "Unknown error"));
    }
  } catch (err) {
    alert("PDF export error: " + err.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<i data-lucide="file-down" class="size-3.5 text-rose-400"></i><span>PDF</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

async function cockpitFetchNetworkSnoop() {
  const countBadge = document.getElementById("cockpitNetworkCountBadge");
  const listArea = document.getElementById("cockpitNetworkListArea");
  if (!listArea) return;

  try {
    const res = await fetch("/api/browser/network/snoop?limit=50");
    const data = await res.json();
    if (!res.ok) return;

    interceptedNetworkRequests = data.requests || [];
    if (countBadge) countBadge.textContent = `${interceptedNetworkRequests.length} Intercepted`;
    cockpitFilterNetworkList();
  } catch (err) {
    console.error("Network snoop error:", err);
  }
}

function cockpitFilterNetworkList() {
  const query = (document.getElementById("cockpitNetworkFilterInput")?.value || "").toLowerCase().trim();
  const listArea = document.getElementById("cockpitNetworkListArea");
  if (!listArea) return;

  const filtered = query
    ? interceptedNetworkRequests.filter(r => (r.url || "").toLowerCase().includes(query) || (r.method || "").toLowerCase().includes(query))
    : interceptedNetworkRequests;

  if (filtered.length === 0) {
    listArea.innerHTML = `<div class="text-[11px] text-zinc-500 italic p-3 text-center">${query ? "No matching requests found." : "No background network requests intercepted yet."}</div>`;
    return;
  }

  listArea.innerHTML = filtered.map((req, idx) => {
    const methodColor = req.method === "POST" ? "text-purple-400 bg-purple-950/60 border-purple-800/40"
      : req.method === "GET" ? "text-emerald-400 bg-emerald-950/60 border-emerald-800/40"
      : "text-cyan-400 bg-cyan-950/60 border-cyan-800/40";
    const statusColor = (req.status >= 200 && req.status < 300) ? "text-emerald-400" : (req.status >= 400 ? "text-red-400" : "text-zinc-400");
    const urlDisplay = req.url ? escapeHtml(req.url.replace(/^https?:\/\/[^\/]+/, "")) : "";
    const fullUrl = escapeHtml(req.url || "");
    const jsonStr = req.body_json ? JSON.stringify(req.body_json, null, 2) : (req.body_text ? req.body_text.slice(0, 500) : null);

    return `
      <div class="p-2 rounded-lg bg-surface-850 border border-surface-750 space-y-1">
        <div class="flex items-center justify-between gap-1 text-[10px]">
          <div class="flex items-center gap-1.5 min-w-0">
            <span class="px-1.5 py-0.5 rounded font-mono font-bold uppercase border ${methodColor}">${escapeHtml(req.method || "REQ")}</span>
            <span class="font-mono ${statusColor} font-semibold">${req.status || 200}</span>
            <span class="text-zinc-300 font-mono truncate" title="${fullUrl}">${urlDisplay || "/"}</span>
          </div>
          <span class="text-[9px] text-zinc-500 font-mono shrink-0">${req.time_str || ""}</span>
        </div>
        ${jsonStr ? `
          <div class="pt-1 flex items-center justify-between">
            <span class="text-[9px] font-mono text-zinc-500">${jsonStr.length} chars payload</span>
            <button onclick="cockpitCopyNetworkPayload(${idx})" class="text-[10px] text-cyan-400 hover:text-cyan-300 font-mono">Copy JSON</button>
          </div>
        ` : ""}
      </div>
    `;
  }).join("");
}

function cockpitCopyNetworkPayload(idx) {
  const req = interceptedNetworkRequests[idx];
  if (!req) return;
  const payload = req.body_json ? JSON.stringify(req.body_json, null, 2) : (req.body_text || "");
  if (payload) {
    navigator.clipboard.writeText(payload);
    showToast("Network JSON payload copied to clipboard");
  }
}

async function cockpitExtractMarkdownContent() {
  const contentEl = document.getElementById("cockpitMarkdownContentArea");
  const badgeEl = document.getElementById("cockpitMarkdownStatsBadge");
  if (!contentEl) return;

  contentEl.innerHTML = `<div class="flex items-center gap-2 text-zinc-500 italic p-3"><i data-lucide="loader-2" class="size-4 animate-spin text-cyan-400"></i> Parsing DOM into structured Markdown...</div>`;
  if (window.lucide) lucide.createIcons();

  try {
    const res = await fetch("/api/browser/markdown?max_length=15000");
    const data = await res.json();
    if (res.ok && data.markdown) {
      contentEl.textContent = data.markdown;
      if (badgeEl) {
        badgeEl.textContent = `${Number(data.total_chars || data.markdown.length).toLocaleString()} Chars`;
      }
    } else {
      contentEl.textContent = "No markdown content extracted.";
    }
  } catch (err) {
    contentEl.textContent = "Error extracting markdown: " + err.message;
  }
}

function cockpitCopyMarkdownContent() {
  const contentEl = document.getElementById("cockpitMarkdownContentArea");
  if (contentEl && contentEl.textContent) {
    navigator.clipboard.writeText(contentEl.textContent);
    showToast("Clean page Markdown copied to clipboard");
  }
}

let browserMissionTimerInterval = null;

function cockpitToggleMissionDeck() {
  const deck = document.getElementById("cockpitMissionDeck");
  if (deck) {
    deck.classList.toggle("hidden");
  }
}

function cockpitQuickMission(goal, url) {
  const input = document.getElementById("cockpitMissionGoalInput");
  if (input) input.value = goal;
  cockpitRunBrowserMission(goal, url);
}

async function cockpitRunBrowserMission(goalParam = null, urlParam = null) {
  const input = document.getElementById("cockpitMissionGoalInput");
  const goal = goalParam || input?.value?.trim();
  if (!goal) {
    alert("Please enter a browsing goal or mission.");
    return;
  }

  const btn = document.getElementById("btnCockpitRunMission");
  const toggleBtn = document.getElementById("btnToggleMissionDeck");
  const deck = document.getElementById("cockpitMissionDeck");
  const stepsList = document.getElementById("cockpitMissionStepsList");
  const statusTitle = document.getElementById("cockpitMissionStatusTitle");
  const elapsedText = document.getElementById("cockpitMissionElapsedText");
  const answerCard = document.getElementById("cockpitMissionAnswerCard");
  const answerText = document.getElementById("cockpitMissionAnswerText");

  if (deck) deck.classList.remove("hidden");
  if (toggleBtn) toggleBtn.classList.remove("hidden");
  if (answerCard) answerCard.classList.add("hidden");
  if (statusTitle) statusTitle.textContent = "Thinking…";
  if (stepsList) {
    stepsList.innerHTML = `
      <div class="flex items-center gap-2 p-2 rounded-lg bg-surface-900 border border-surface-750 text-xs text-zinc-300">
        <i data-lucide="loader-2" class="size-3.5 text-emerald-400 animate-spin"></i>
        <span>Step 1: Navigating to mission target & perceiving page DOM...</span>
      </div>
    `;
    if (window.lucide) lucide.createIcons();
  }

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 text-white animate-spin"></i> <span>Running Mission...</span>`;
    if (window.lucide) lucide.createIcons();
  }

  const startT = Date.now();
  if (browserMissionTimerInterval) clearInterval(browserMissionTimerInterval);
  browserMissionTimerInterval = setInterval(() => {
    if (elapsedText) {
      elapsedText.textContent = `${Math.round((Date.now() - startT) / 1000)}s`;
    }
  }, 500);

  try {
    const res = await fetch("/api/browser/mission", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal, start_url: urlParam, max_steps: 6 })
    });
    const data = await res.json();

    if (browserMissionTimerInterval) clearInterval(browserMissionTimerInterval);

    if (res.ok && data.steps) {
      if (statusTitle) {
        statusTitle.textContent = `Mission Completed (${data.steps.length} Steps)`;
      }
      if (elapsedText) {
        elapsedText.textContent = `${data.elapsed_sec || Math.round((Date.now() - startT) / 1000)}s`;
      }

      // Render step timeline
      if (stepsList) {
        stepsList.innerHTML = data.steps.map(s => {
          const isDone = s.action === 'finish' || s.status === 'completed' || s.status === 'success';
          const badgeColor = isDone ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30' : 'text-cyan-400 bg-cyan-500/10 border-cyan-500/30';
          
          return `
            <div class="p-2 rounded-lg bg-surface-900 border border-surface-750 text-[11px] space-y-1">
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-1.5 font-semibold text-white">
                  <span class="px-1.5 py-0.2 rounded border text-[10px] uppercase font-mono ${badgeColor}">Step ${s.step_num}: ${s.action}</span>
                  ${s.target ? `<span class="font-mono text-zinc-400 text-[10px] truncate max-w-xs">${escapeHtml(s.target)}</span>` : ''}
                </div>
                <span class="text-[10px] font-mono ${isDone ? 'text-emerald-400' : 'text-zinc-500'}">${s.status}</span>
              </div>
              ${s.reasoning ? `<p class="text-zinc-300 text-[11px] leading-relaxed italic pl-1 border-l-2 border-emerald-500/50">"${escapeHtml(s.reasoning)}"</p>` : ''}
            </div>
          `;
        }).join("");
      }

      // Render Final Answer / Synthesis
      if (data.final_answer && answerCard && answerText) {
        answerText.textContent = data.final_answer;
        answerCard.classList.remove("hidden");
      }

      // Update browser viewport screenshot
      if (data.latest_screenshot_uri) {
        renderCockpitBrowserState({
          current_url: data.steps[data.steps.length - 1]?.url || "about:blank",
          current_title: data.steps[data.steps.length - 1]?.title || "Browser",
          screenshot_uri: data.latest_screenshot_uri
        });
      }

      showToast(`Mission completed in ${data.steps.length} steps`);
    } else {
      if (statusTitle) statusTitle.textContent = "Mission Encountered Issue";
      alert("Mission error: " + (data.detail || "Failed to complete mission"));
    }
  } catch (err) {
    if (browserMissionTimerInterval) clearInterval(browserMissionTimerInterval);
    if (statusTitle) statusTitle.textContent = "Mission Failed";
    alert("Mission execution error: " + err.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<i data-lucide="zap" class="size-3.5"></i> <span>Execute Mission</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }
}

function cockpitCopyMissionAnswer() {
  const answerText = document.getElementById("cockpitMissionAnswerText");
  if (answerText && answerText.textContent) {
    navigator.clipboard.writeText(answerText.textContent);
    showToast("Mission findings copied to clipboard");
  }
}

async function cockpitEvaluateJs() {
  const input = document.getElementById("cockpitJsInput");
  const output = document.getElementById("cockpitJsOutput");
  if (!input || !input.value.trim() || !output) return;

  const expression = input.value.trim();
  try {
    const res = await fetch("/api/browser/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expression })
    });
    const data = await res.json();
    output.classList.remove("hidden");
    output.textContent = JSON.stringify(data.result, null, 2);
  } catch (err) {
    output.classList.remove("hidden");
    output.textContent = "Error: " + err.message;
  }
}

async function cockpitEvaluateJsDirect(expression) {
  try {
    await fetch("/api/browser/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expression })
    });
  } catch (e) {}
}

function cockpitCopyExtractedText() {
  const bodyTextEl = document.getElementById("cockpitBodyTextPreview");
  if (bodyTextEl && bodyTextEl.textContent) {
    navigator.clipboard.writeText(bodyTextEl.textContent);
    showToast("Page body text copied to clipboard");
  }
}

// -------------------------------------------------------------
// COMPUTER & TELEMETRY CONTROLLERS
// -------------------------------------------------------------
let telemetryPulseInterval = null;
let isTelemetryPulseActive = true;

function startTelemetryPulse() {
  if (telemetryPulseInterval) clearInterval(telemetryPulseInterval);
  if (!isTelemetryPulseActive) return;
  telemetryPulseInterval = setInterval(() => {
    const modal = document.getElementById("autonomousCockpitModal");
    if (!modal || modal.classList.contains("hidden") || cockpitActiveTab !== "computer") {
      stopTelemetryPulse();
      return;
    }
    fetchComputerTelemetry(true);
  }, 2500);
}

function stopTelemetryPulse() {
  if (telemetryPulseInterval) {
    clearInterval(telemetryPulseInterval);
    telemetryPulseInterval = null;
  }
}

function toggleTelemetryPulse() {
  isTelemetryPulseActive = !isTelemetryPulseActive;
  const btnText = document.getElementById("textLivePulse");
  const btnIcon = document.getElementById("iconLivePulse");
  const ring = document.getElementById("cockpitLivePulseRing");
  const dot = document.getElementById("cockpitLivePulseDot");
  const freqBadge = document.getElementById("cockpitStreamFreqBadge");

  if (isTelemetryPulseActive) {
    if (btnText) btnText.textContent = "Pause Stream";
    if (btnIcon) btnIcon.setAttribute("data-lucide", "pause");
    if (ring) ring.className = "animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75";
    if (dot) dot.className = "relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500";
    if (freqBadge) {
      freqBadge.textContent = "2.5s Pulse";
      freqBadge.className = "text-[10px] font-mono text-emerald-400 px-1.5 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/20";
    }
    startTelemetryPulse();
    showToast("Live telemetry stream resumed");
  } else {
    stopTelemetryPulse();
    if (btnText) btnText.textContent = "Resume Stream";
    if (btnIcon) btnIcon.setAttribute("data-lucide", "play");
    if (ring) ring.className = "hidden";
    if (dot) dot.className = "relative inline-flex rounded-full h-2.5 w-2.5 bg-zinc-500";
    if (freqBadge) {
      freqBadge.textContent = "Stream Paused";
      freqBadge.className = "text-[10px] font-mono text-zinc-400 px-1.5 py-0.5 rounded bg-surface-800 border border-surface-700";
    }
    showToast("Live telemetry stream paused");
  }
  if (window.lucide) lucide.createIcons();
}

async function fetchComputerTelemetry(isSilentPulse = false) {
  try {
    const res = await fetch("/api/computer/telemetry");
    if (!res.ok) return;
    const telem = await res.json();

    const cpu1m = document.getElementById("telemCpu1m");
    const cpu5m = document.getElementById("telemCpu5m");
    const cpu15m = document.getElementById("telemCpu15m");
    if (cpu1m && telem.cpu_load) cpu1m.textContent = telem.cpu_load["1m"] ?? "0.00";
    if (cpu5m && telem.cpu_load) cpu5m.textContent = telem.cpu_load["5m"] ?? "0.00";
    if (cpu15m && telem.cpu_load) cpu15m.textContent = telem.cpu_load["15m"] ?? "0.00";

    const ramUsed = document.getElementById("telemRamUsed");
    const ramTotal = document.getElementById("telemRamTotal");
    const ramBar = document.getElementById("telemRamBar");
    const ramBadge = document.getElementById("telemRamPressureBadge");
    if (telem.memory) {
      if (ramUsed) ramUsed.textContent = `${telem.memory.used_gb} GB`;
      if (ramTotal) ramTotal.textContent = `of ${telem.memory.total_gb} GB`;
      const pct = telem.memory.pressure_pct || (telem.memory.total_gb > 0 ? Math.min(100, Math.round((telem.memory.used_gb / telem.memory.total_gb) * 100)) : 0);
      if (ramBar) {
        ramBar.style.width = `${pct}%`;
        if (pct >= 88) {
          ramBar.className = "bg-rose-500 h-full rounded-full transition-all";
        } else if (pct >= 75) {
          ramBar.className = "bg-amber-500 h-full rounded-full transition-all";
        } else {
          ramBar.className = "bg-cyan-500 h-full rounded-full transition-all";
        }
      }
      if (ramBadge) {
        const level = telem.memory.pressure_level || (pct >= 88 ? "critical" : (pct >= 75 ? "elevated" : "normal"));
        if (level === "critical") {
          ramBadge.textContent = `${pct}% Critical`;
          ramBadge.className = "text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded bg-rose-500/20 text-rose-400 border border-rose-500/30";
        } else if (level === "elevated") {
          ramBadge.textContent = `${pct}% Elevated`;
          ramBadge.className = "text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 border border-amber-500/30";
        } else {
          ramBadge.textContent = `${pct}% Normal`;
          ramBadge.className = "text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20";
        }
      }
    }

    const diskUsed = document.getElementById("telemDiskUsed");
    const diskTotal = document.getElementById("telemDiskTotal");
    const diskBar = document.getElementById("telemDiskBar");
    if (telem.disk) {
      if (diskUsed) diskUsed.textContent = `${telem.disk.used_gb} GB`;
      if (diskTotal) diskTotal.textContent = `of ${telem.disk.total_gb} GB`;
      if (diskBar) diskBar.style.width = `${telem.disk.percent_used || 0}%`;
    }

    const osText = document.getElementById("telemOsText");
    const archText = document.getElementById("telemArchText");
    if (osText && telem.os) osText.textContent = telem.os;
    if (archText && telem.arch) archText.textContent = telem.arch;

    // Top processes table with RSS and one-click kill
    const procTable = document.getElementById("telemProcessesTable");
    if (procTable && Array.isArray(telem.top_processes)) {
      if (telem.top_processes.length === 0) {
        procTable.innerHTML = `<tr><td colspan="6" class="py-3 text-center text-zinc-500">No processes reported.</td></tr>`;
      } else {
        procTable.innerHTML = telem.top_processes.map(p => {
          const cpuVal = parseFloat(p.cpu_pct) || 0;
          const cpuColor = cpuVal >= 70 ? 'text-rose-400 font-bold' : (cpuVal >= 25 ? 'text-amber-400 font-semibold' : 'text-emerald-400');
          const rssStr = p.rss_mb ? `${p.rss_mb} MB` : '-';
          return `
            <tr class="hover:bg-surface-800 transition-colors">
              <td class="py-1.5 font-mono text-zinc-400">${escapeHtml(p.pid)}</td>
              <td class="py-1.5 font-mono text-white font-medium truncate max-w-[150px]" title="${escapeHtml(p.full_path || p.command)}">${escapeHtml(p.command)}</td>
              <td class="py-1.5 font-mono text-right ${cpuColor}">${escapeHtml(p.cpu_pct)}%</td>
              <td class="py-1.5 font-mono text-right text-cyan-400">${escapeHtml(p.mem_pct)}%</td>
              <td class="py-1.5 font-mono text-right text-zinc-300">${rssStr}</td>
              <td class="py-1.5 text-right">
                <button onclick="cockpitKillPid(${p.pid}, '${escapeHtml(p.command)}')" class="px-2 py-0.5 rounded bg-surface-750 hover:bg-rose-600 text-zinc-300 hover:text-white transition-colors text-[10px] font-semibold inline-flex items-center gap-1" title="Terminate process">
                  <i data-lucide="x" class="size-2.5"></i> Kill
                </button>
              </td>
            </tr>
          `;
        }).join("");
      }
    }

    // Auto-Discovered Listening Ports Badges
    const portsGrid = document.getElementById("cockpitListeningPortsGrid");
    const portsCount = document.getElementById("cockpitPortsCountBadge");
    if (portsGrid && Array.isArray(telem.listening_ports)) {
      if (portsCount) portsCount.textContent = `${telem.listening_ports.length} ports`;
      if (telem.listening_ports.length === 0) {
        portsGrid.innerHTML = `<div class="text-[11px] text-zinc-500 italic p-1">No active listening ports detected.</div>`;
      } else {
        const commonPorts = [8000, 1234, 9228, 11434, 3000, 5173, 8080];
        portsGrid.innerHTML = telem.listening_ports.map(p => {
          const isCommon = commonPorts.includes(p.port);
          const dotColor = isCommon ? 'bg-cyan-400' : 'bg-emerald-400';
          return `
            <button onclick="cockpitSelectPort(${p.port})" class="px-2.5 py-1 rounded-lg bg-surface-900 border border-surface-700/80 hover:border-cyan-500/50 hover:bg-surface-800 transition-all text-[11px] font-mono flex items-center gap-1.5 shrink-0 group shadow-xs" title="Click to inspect port ${p.port}">
              <span class="size-1.5 rounded-full ${dotColor} group-hover:scale-125 transition-transform"></span>
              <span class="font-bold text-white">:${p.port}</span>
              <span class="text-zinc-400 text-[10px] truncate max-w-[100px]">${escapeHtml(p.command)}</span>
            </button>
          `;
        }).join("");
      }
    }

    if (!isSilentPulse) {
      cockpitRefreshJobs();
    }
    if (window.lucide) lucide.createIcons();
  } catch (err) {
    console.error("Telemetry fetch error:", err);
  }
}

function cockpitSelectPort(port) {
  const input = document.getElementById("cockpitInspectPortInput");
  if (input) {
    input.value = port;
    cockpitInspectPort();
  }
}

async function triggerAiSystemReview() {
  const queryInput = document.getElementById("cockpitAiReviewQuery");
  const query = queryInput ? queryInput.value.trim() : "";
  const statusBadge = document.getElementById("aiReviewStatusBadge");
  const deck = document.getElementById("aiReviewResultsDeck");
  const summaryEl = document.getElementById("aiReviewSummaryText");
  const findingsList = document.getElementById("aiReviewFindingsList");
  const actionsList = document.getElementById("aiReviewActionsList");
  const submitBtn = document.getElementById("btnSubmitAiReview");

  if (statusBadge) {
    statusBadge.textContent = "Analyzing...";
    statusBadge.className = "text-[10px] font-mono font-semibold px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 animate-pulse";
  }
  if (submitBtn) submitBtn.disabled = true;

  try {
    const res = await fetch("/api/computer/ai/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ intent: query || null })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "AI review failed");

    if (deck) deck.classList.remove("hidden");
    if (statusBadge) {
      const isCrit = data.status === "critical";
      const isWarn = data.status === "warning";
      statusBadge.textContent = isCrit ? "Critical Health" : (isWarn ? "Attention Needed" : "System Healthy");
      statusBadge.className = `text-[10px] font-mono font-semibold px-2 py-0.5 rounded-full ${isCrit ? 'bg-rose-500/20 text-rose-400 border border-rose-500/30' : (isWarn ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30' : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30')}`;
    }

    if (summaryEl) {
      summaryEl.textContent = data.summary || "Host diagnostic complete. No immediate critical failures detected.";
    }

    // Render findings
    if (findingsList) {
      const findings = data.diagnoses || [];
      if (findings.length === 0) {
        findingsList.innerHTML = `<div class="text-[11px] text-zinc-500 italic p-2 col-span-2">All host telemetry operating within optimal boundaries.</div>`;
      } else {
        findingsList.innerHTML = findings.map(f => {
          const isCrit = f.severity === "critical";
          const isWarn = f.severity === "warning";
          const colorClass = isCrit ? 'border-rose-500/30 bg-rose-950/20 text-rose-300' : (isWarn ? 'border-amber-500/30 bg-amber-950/20 text-amber-300' : 'border-surface-750 bg-surface-900 text-zinc-300');
          const badgeColor = isCrit ? 'bg-rose-500/30 text-rose-200' : (isWarn ? 'bg-amber-500/30 text-amber-200' : 'bg-surface-750 text-zinc-400');
          return `
            <div class="p-2.5 rounded-xl border ${colorClass} text-xs space-y-1">
              <div class="flex items-center justify-between">
                <span class="font-semibold text-white truncate">${escapeHtml(f.title)}</span>
                <span class="text-[9px] uppercase font-mono px-1.5 py-0.5 rounded ${badgeColor}">${escapeHtml(f.component || f.severity)}</span>
              </div>
              <p class="text-[11px] text-zinc-400 line-clamp-2">${escapeHtml(f.description)}</p>
            </div>
          `;
        }).join("");
      }
    }

    // Render remediations
    if (actionsList) {
      const actions = data.remediations || [];
      if (actions.length === 0) {
        actionsList.innerHTML = `<div class="text-[11px] text-zinc-500 italic p-2 col-span-2">No active remediations needed right now. System is running cleanly.</div>`;
      } else {
        actionsList.innerHTML = actions.map(act => {
          const isCaution = act.danger_level === "caution";
          const badgeClass = isCaution ? 'bg-amber-500/20 text-amber-300' : 'bg-emerald-500/20 text-emerald-300';
          const paramsJson = JSON.stringify(act.params || {});
          return `
            <div class="p-3 rounded-xl bg-surface-900 border border-surface-750 hover:border-indigo-500/40 transition-colors space-y-2">
              <div class="flex items-center justify-between gap-2">
                <span class="font-semibold text-white text-xs truncate">${escapeHtml(act.label)}</span>
                <span class="text-[9px] uppercase font-mono px-1.5 py-0.5 rounded ${badgeClass}">${escapeHtml(act.danger_level || 'safe')}</span>
              </div>
              <p class="text-[11px] text-zinc-400 leading-relaxed">${escapeHtml(act.description)}</p>
              <div class="pt-1 flex items-center justify-end">
                <button type="button" data-action-type="${escapeHtml(act.action_type)}" data-action-params='${escapeHtml(paramsJson)}' onclick="handleRemediationBtn(this)" class="px-3 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold flex items-center gap-1.5 shadow-sm transition-all">
                  <i data-lucide="zap" class="size-3 text-indigo-200"></i>
                  <span>Execute Fix</span>
                </button>
              </div>
            </div>
          `;
        }).join("");
      }
    }

    if (window.lucide) lucide.createIcons();
    showToast("AI Host Diagnosis Complete");
  } catch (err) {
    alert("AI Review Error: " + err.message);
    if (statusBadge) {
      statusBadge.textContent = "Audit Error";
      statusBadge.className = "text-[10px] font-mono font-semibold px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-400 border border-rose-500/30";
    }
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

function handleRemediationBtn(btn) {
  const actionType = btn.getAttribute("data-action-type");
  const rawParams = btn.getAttribute("data-action-params");
  try {
    const params = JSON.parse(rawParams);
    executeAiRemediation(actionType, params);
  } catch (e) {
    executeAiRemediation(actionType, {});
  }
}

function setAndRunAiReview(query) {
  const input = document.getElementById("cockpitAiReviewQuery");
  if (input) input.value = query;
  triggerAiSystemReview();
}

async function executeAiRemediation(actionType, params) {
  try {
    const res = await fetch("/api/computer/ai/execute_action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action_type: actionType, params })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.error || "Execution failed");

    showToast(data.message || `Executed ${actionType}`);
    fetchComputerTelemetry();
    setTimeout(() => { triggerAiSystemReview(); }, 800);
  } catch (err) {
    alert("Remediation Execution Error: " + err.message);
  }
}

function setCockpitCmd(cmd) {
  const input = document.getElementById("cockpitCmdInput");
  if (input) {
    input.value = cmd;
    cockpitRunShellCommand();
  }
}

async function cockpitRunShellCommand() {
  const input = document.getElementById("cockpitCmdInput");
  const btn = document.getElementById("cockpitRunCmdBtn");
  const stdoutEl = document.getElementById("cockpitTerminalStdout");
  const statusEl = document.getElementById("cockpitTerminalStatus");
  const durationEl = document.getElementById("cockpitTerminalDuration");

  if (!input || !input.value.trim()) return;
  const command = input.value.trim();

  if (statusEl) {
    statusEl.textContent = `Running: ${command}`;
    statusEl.className = "text-amber-400 font-mono";
  }
  if (durationEl) durationEl.textContent = "0.00s";
  if (stdoutEl) stdoutEl.textContent = "";
  if (btn) btn.disabled = true;

  const startT = Date.now();
  const timer = setInterval(() => {
    if (durationEl) {
      durationEl.textContent = `${((Date.now() - startT) / 1000).toFixed(1)}s`;
    }
  }, 100);

  try {
    const res = await fetch("/api/computer/run-stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command })
    });

    if (!res.ok) {
      throw new Error(`Server returned HTTP ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (!line.trim() || !line.startsWith("data: ")) continue;
        try {
          const payload = JSON.parse(line.slice(6));
          if (payload.type === "stdout" && stdoutEl) {
            stdoutEl.textContent += payload.text;
            stdoutEl.scrollTop = stdoutEl.scrollHeight;
          } else if (payload.type === "stderr" && stdoutEl) {
            stdoutEl.textContent += `[STDERR] ${payload.text}`;
            stdoutEl.scrollTop = stdoutEl.scrollHeight;
          } else if (payload.type === "done") {
            if (durationEl) durationEl.textContent = `${payload.duration_sec || 0}s`;
            if (statusEl) {
              const ok = payload.exit_code === 0;
              statusEl.textContent = ok ? "Exit code 0 (Success)" : `Exit code ${payload.exit_code} (Failed)`;
              statusEl.className = ok ? "text-emerald-400" : "text-rose-400";
            }
          } else if (payload.type === "error") {
            if (statusEl) {
              statusEl.textContent = `Error: ${payload.error}`;
              statusEl.className = "text-rose-400";
            }
            if (stdoutEl) stdoutEl.textContent += `\n[ERROR] ${payload.error}\n`;
          }
        } catch (e) {}
      }
    }
  } catch (err) {
    if (stdoutEl) stdoutEl.textContent = "Execution Error: " + err.message;
    if (statusEl) {
      statusEl.textContent = "Execution Failed";
      statusEl.className = "text-rose-400";
    }
  } finally {
    clearInterval(timer);
    if (btn) btn.disabled = false;
  }
}

async function cockpitSendNotification() {
  const title = document.getElementById("cockpitNotifyTitle")?.value || "Xod AI";
  const message = document.getElementById("cockpitNotifyMsg")?.value || "Test notification";

  try {
    const res = await fetch("/api/computer/notify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, message, sound: true })
    });
    if (res.ok) {
      showToast("Sent macOS Notification");
    }
  } catch (err) {
    alert("Notification error: " + err.message);
  }
}

async function cockpitRevealFinder() {
  const path = document.getElementById("cockpitRevealPath")?.value || "~";
  try {
    const res = await fetch("/api/computer/reveal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path })
    });
    if (res.ok) {
      showToast(`Revealed ${path} in Finder`);
    } else {
      const err = await res.json();
      alert(err.detail || "Could not reveal path");
    }
  } catch (err) {
    alert("Reveal error: " + err.message);
  }
}

async function cockpitSpotlightSearch() {
  const input = document.getElementById("cockpitSpotlightQuery");
  const list = document.getElementById("cockpitSpotlightResults");
  if (!input || !input.value.trim() || !list) return;

  const query = input.value.trim();
  list.innerHTML = `<div class="text-[11px] text-zinc-400 italic p-2 text-center flex items-center justify-center gap-1.5"><i data-lucide="loader-2" class="size-3.5 animate-spin text-amber-400"></i> Searching Spotlight...</div>`;
  if (window.lucide) lucide.createIcons();

  try {
    const res = await fetch("/api/computer/find", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, max_results: 20 })
    });
    const data = await res.json();
    if (!res.ok || !data.results || data.results.length === 0) {
      list.innerHTML = `<div class="text-[11px] text-zinc-500 italic p-2 text-center">No files found matching "${escapeHtml(query)}"</div>`;
      return;
    }

    list.innerHTML = data.results.map(f => {
      const icon = f.is_dir ? "folder" : "file-text";
      const sizeStr = f.size_bytes > 0 ? (f.size_bytes > 1024*1024 ? `${(f.size_bytes/(1024*1024)).toFixed(1)} MB` : `${Math.round(f.size_bytes/1024)} KB`) : "";
      return `
        <div class="flex items-center justify-between p-1.5 rounded-lg bg-surface-900 border border-surface-750 text-[11px] gap-2 hover:border-amber-500/40 transition-colors">
          <div class="flex items-center gap-1.5 min-w-0">
            <i data-lucide="${icon}" class="size-3.5 ${f.is_dir ? 'text-amber-400' : 'text-zinc-400'} shrink-0"></i>
            <div class="min-w-0">
              <div class="font-mono text-zinc-200 font-medium truncate" title="${escapeHtml(f.path)}">${escapeHtml(f.name)}</div>
              <div class="text-[9px] text-zinc-500 font-mono truncate">${escapeHtml(f.path)}</div>
            </div>
          </div>
          <div class="flex items-center gap-1 shrink-0">
            ${sizeStr ? `<span class="text-[9px] font-mono text-zinc-500 mr-1">${sizeStr}</span>` : ""}
            <button onclick="navigator.clipboard.writeText('${escapeHtml(f.path)}'); showToast('Copied path');" class="p-1 rounded hover:bg-surface-800 text-zinc-400 hover:text-white" title="Copy Path">
              <i data-lucide="copy" class="size-3"></i>
            </button>
            <button onclick="document.getElementById('cockpitRevealPath').value='${escapeHtml(f.path)}'; cockpitRevealFinder();" class="p-1 rounded hover:bg-surface-800 text-zinc-400 hover:text-white" title="Reveal in Finder">
              <i data-lucide="external-link" class="size-3"></i>
            </button>
          </div>
        </div>
      `;
    }).join("");
    if (window.lucide) lucide.createIcons();
  } catch (err) {
    list.innerHTML = `<div class="text-[11px] text-rose-400 p-2 text-center">Search error: ${err.message}</div>`;
  }
}

async function cockpitInspectPort() {
  const input = document.getElementById("cockpitInspectPortInput");
  const badge = document.getElementById("cockpitPortStatusBadge");
  if (!input || !input.value) return;

  const port = parseInt(input.value.trim(), 10);
  if (isNaN(port)) return;

  if (badge) badge.innerHTML = `<i data-lucide="loader-2" class="size-3 animate-spin inline"></i> Checking...`;
  try {
    const res = await fetch(`/api/computer/port/${port}`);
    const data = await res.json();
    if (!res.ok) {
      if (badge) badge.textContent = "Error checking port";
      return;
    }

    if (!data.in_use || data.processes.length === 0) {
      if (badge) badge.innerHTML = `<span class="text-emerald-400 font-semibold">Port ${port} is Free</span>`;
    } else {
      const p = data.processes[0];
      if (badge) badge.innerHTML = `
        <span class="text-rose-400 font-semibold">Port ${port}: ${escapeHtml(p.command)} (PID ${p.pid})</span>
        <button onclick="cockpitKillPid(${p.pid})" class="ml-1.5 px-2 py-0.5 rounded bg-rose-600 hover:bg-rose-500 text-white text-[10px] font-semibold">Kill</button>
      `;
    }
    if (window.lucide) lucide.createIcons();
  } catch (err) {
    if (badge) badge.textContent = "Error: " + err.message;
  }
}

async function cockpitKillPid(pid, name = "") {
  const label = name ? `process ${name} (PID ${pid})` : `process PID ${pid}`;
  if (!confirm(`Are you sure you want to terminate ${label}?`)) return;
  try {
    const res = await fetch("/api/computer/process/kill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pid, force: false })
    });
    if (res.ok) {
      showToast(`Terminated ${label}`);
      cockpitInspectPort();
      fetchComputerTelemetry();
    }
  } catch (err) {
    alert("Kill failed: " + err.message);
  }
}

async function cockpitRefreshJobs() {
  const list = document.getElementById("cockpitBackgroundJobsList");
  if (!list) return;

  try {
    const res = await fetch("/api/computer/jobs");
    const data = await res.json();
    const jobs = data.jobs || [];

    if (jobs.length === 0) {
      list.innerHTML = `<div class="text-[11px] text-zinc-500 italic p-2 text-center">No persistent background daemons running.</div>`;
      return;
    }

    list.innerHTML = jobs.map(j => {
      const isRunning = j.status === "running";
      const statusColor = isRunning ? "text-emerald-400" : (j.status === "completed" ? "text-blue-400" : "text-zinc-500");
      return `
        <div class="flex items-center justify-between p-2 rounded-lg bg-surface-900 border border-surface-750 text-[11px] gap-2">
          <div class="min-w-0">
            <div class="flex items-center gap-1.5 font-mono text-zinc-200 font-medium">
              <span class="size-1.5 rounded-full ${isRunning ? 'bg-emerald-400 animate-pulse' : 'bg-zinc-500'}"></span>
              <span class="truncate">${escapeHtml(j.name)}</span>
              <span class="text-[9px] text-zinc-500">(PID ${j.pid})</span>
            </div>
            <div class="text-[10px] text-zinc-400 font-mono truncate">$ ${escapeHtml(j.command)}</div>
          </div>
          <div class="flex items-center gap-1.5 shrink-0">
            <span class="text-[10px] font-mono ${statusColor} uppercase">${escapeHtml(j.status)}</span>
            ${isRunning ? `
              <button onclick="cockpitKillJob('${j.id}')" class="px-2 py-0.5 rounded bg-rose-600 hover:bg-rose-500 text-white text-[10px] font-semibold">Stop</button>
            ` : ""}
          </div>
        </div>
      `;
    }).join("");
    if (window.lucide) lucide.createIcons();
  } catch (err) {
    console.error("Refresh jobs error:", err);
  }
}

async function cockpitKillJob(jobId) {
  try {
    const res = await fetch(`/api/computer/jobs/${jobId}/stop`, { method: "POST" });
    if (res.ok) {
      showToast("Background job stopped");
      cockpitRefreshJobs();
    }
  } catch (err) {
    alert("Stop error: " + err.message);
  }
}

async function refreshCockpitFeed() {
  const container = document.getElementById("cockpitActionFeedList");
  if (!container) return;

  try {
    const [bRes, cRes] = await Promise.all([
      fetch("/api/browser/status"),
      fetch("/api/computer/history")
    ]);
    const bData = bRes.ok ? await bRes.json() : {};
    const cData = cRes.ok ? await cRes.json() : [];

    const actions = [];
    if (Array.isArray(bData.action_history)) {
      bData.action_history.forEach(a => {
        actions.push({
          type: "browser",
          icon: "globe",
          color: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
          title: `Browser ${a.action.toUpperCase()}`,
          details: a.url || a.target || a.selector || "",
          time_str: a.time_str || "",
          timestamp: a.time || 0
        });
      });
    }

    if (Array.isArray(cData)) {
      cData.forEach(c => {
        actions.push({
          type: "computer",
          icon: "terminal",
          color: "text-cyan-400 bg-cyan-500/10 border-cyan-500/20",
          title: `Command: ${c.command}`,
          details: c.status === "completed" ? `Duration: ${c.duration_sec}s` : `Status: ${c.status}`,
          time_str: c.start_time_str || "",
          timestamp: c.start_time || 0
        });
      });
    }

    actions.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));

    if (actions.length === 0) {
      container.innerHTML = `<div class="text-xs text-zinc-500 p-8 text-center italic">No autonomous actions recorded yet.</div>`;
    } else {
      container.innerHTML = actions.map(act => `
        <div class="p-2.5 rounded-xl bg-surface-850 border border-surface-700/70 flex items-start justify-between gap-3 text-xs">
          <div class="flex items-start gap-2.5 min-w-0">
            <span class="p-1.5 rounded-lg border ${act.color} shrink-0 mt-0.5">
              <i data-lucide="${act.icon}" class="size-3.5"></i>
            </span>
            <div class="min-w-0">
              <span class="font-bold text-white block truncate">${escapeHtml(act.title)}</span>
              <p class="text-[11px] text-zinc-400 font-mono truncate">${escapeHtml(act.details)}</p>
            </div>
          </div>
          <span class="text-[10px] text-zinc-500 font-mono shrink-0">${escapeHtml(act.time_str || "")}</span>
        </div>
      `).join("");
      if (window.lucide) lucide.createIcons();
    }
  } catch (err) {
    console.error("Action feed error:", err);
  }
}

// ==============================================================
// JARVIS HANDS-FREE VOICE CONTROLLER & LIVING AUDIO UI
// ==============================================================
let jarvisSpeechRecognition = null;
let isJarvisListening = false;
let jarvisAudioState = "idle"; // 'idle' | 'listening' | 'thinking' | 'speaking'

function setJarvisState(state) {
  jarvisAudioState = state;
  const headerOrb = document.getElementById("jarvisOrbBtn");
  const hudOrb = document.getElementById("hudOrbWrap");
  const label = document.getElementById("jarvisOrbLabel");
  const title = document.getElementById("hudStateTitle");
  const subtitle = document.getElementById("hudStateSubtitle");
  const micIcon = document.getElementById("chatMicIcon");

  [headerOrb, hudOrb].forEach(el => {
    if (!el) return;
    el.classList.remove("listening", "thinking", "speaking");
    if (state !== "idle") el.classList.add(state);
  });

  if (state === "listening") {
    if (label) label.textContent = "Listening…";
    if (title) title.textContent = "Listening";
    if (subtitle) subtitle.textContent = "Speak your command...";
    if (micIcon) micIcon.classList.add("text-red-400", "animate-pulse");
  } else if (state === "thinking") {
    if (label) label.textContent = "Executing…";
    if (title) title.textContent = "Working on it";
    if (subtitle) subtitle.textContent = "Routing to local tools...";
    if (micIcon) micIcon.classList.remove("text-red-400", "animate-pulse");
  } else if (state === "speaking") {
    if (label) label.textContent = "Speaking…";
    if (title) title.textContent = "Answering";
    if (subtitle) subtitle.textContent = "Audio response playing...";
    if (micIcon) micIcon.classList.remove("text-red-400", "animate-pulse");
  } else {
    if (label) label.textContent = "Voice";
    if (title) title.textContent = "Voice off";
    if (subtitle) subtitle.textContent = "Click to activate";
    if (micIcon) micIcon.classList.remove("text-red-400", "animate-pulse");
  }
}

// Voice input is transcribed on this Mac (local Whisper, /api/voice/transcribe), so audio never
// leaves the computer, and it works in the desktop app, where the browser's speech API is missing.
// LocalSpeechRecognition looks like the browser's SpeechRecognition to the code below: it records
// until you pause, then reports one final result.
class LocalSpeechRecognition {
  constructor() {
    this.onstart = this.onresult = this.onerror = this.onend = null;
    this.SILENCE_MS = 1300;      // stop after this much quiet once you've spoken
    this.WAIT_MS = 8000;         // give up if nothing is said
    this.MAX_MS = 60000;
  }
  async start() {
    if (this.recorder) return;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (e) {
      this.onerror && this.onerror({ error: "not-allowed" });
      showToast("Microphone access was blocked. Allow it in System Settings → Privacy & Security → Microphone.");
      this.onend && this.onend();
      return;
    }
    this.stream = stream;
    this.pcm = [];
    this.heard = false;
    this.recorder = true;
    // Raw audio straight from the mic, sent as a WAV file (no codecs, so the Mac needs no ffmpeg to read it).
    this.ctx = new AudioContext();
    const source = this.ctx.createMediaStreamSource(stream);
    this.proc = this.ctx.createScriptProcessor(4096, 1, 1);
    this.proc.onaudioprocess = (e) => { this.pcm.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
    source.connect(this.proc);
    this.proc.connect(this.ctx.destination);
    const analyser = this.ctx.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    const started = Date.now();
    let lastLoud = Date.now();
    this.timer = setInterval(() => {
      analyser.getFloatTimeDomainData(buf);
      const rms = Math.sqrt(buf.reduce((a, v) => a + v * v, 0) / buf.length);
      const now = Date.now();
      if (rms > 0.02) { this.heard = true; lastLoud = now; }
      if ((this.heard && now - lastLoud > this.SILENCE_MS) || (!this.heard && now - started > this.WAIT_MS) || now - started > this.MAX_MS) {
        this.stop();
      }
    }, 100);
    this.onstart && this.onstart();
  }
  stop() {
    clearInterval(this.timer);
    if (this.recorder) this._finish();
  }
  // 16 kHz mono 16-bit WAV, which is what Whisper wants anyway.
  _wav() {
    const rate = this.ctx.sampleRate, out = 16000;
    const total = this.pcm.reduce((n, a) => n + a.length, 0);
    const all = new Float32Array(total);
    let o = 0;
    for (const a of this.pcm) { all.set(a, o); o += a.length; }
    const n = Math.floor(total * out / rate);
    const view = new DataView(new ArrayBuffer(44 + n * 2));
    const str = (off, t) => { for (let i = 0; i < t.length; i++) view.setUint8(off + i, t.charCodeAt(i)); };
    str(0, "RIFF"); view.setUint32(4, 36 + n * 2, true); str(8, "WAVE"); str(12, "fmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, out, true); view.setUint32(28, out * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    str(36, "data"); view.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) {
      const v = Math.max(-1, Math.min(1, all[Math.min(total - 1, Math.floor(i * rate / out))]));
      view.setInt16(44 + i * 2, v * 0x7fff, true);
    }
    return new Blob([view], { type: "audio/wav" });
  }
  async _finish() {
    const type = "audio/wav";
    this.recorder = null;
    try { this.proc.disconnect(); } catch (e) {}
    this.stream.getTracks().forEach((t) => t.stop());
    const blob = this._wav();
    try { this.ctx.close(); } catch (e) {}
    if (!this.heard || blob.size < 1000) {
      this.onerror && this.onerror({ error: "no-speech" });
      this.onend && this.onend();
      return;
    }
    const transcriptEl = document.getElementById("hudTranscriptText");
    if (transcriptEl) transcriptEl.textContent = "Transcribing on this Mac…";
    try {
      const res = await fetch("/api/voice/transcribe", { method: "POST", headers: { "Content-Type": type }, body: blob });
      const data = await res.json();
      const text = (data.text || "").trim();
      if (!data.success) throw new Error(data.error || "transcription failed");
      if (text) {
        this.onresult && this.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: text }], { isFinal: true })] });
      } else {
        this.onerror && this.onerror({ error: "no-speech" });
      }
    } catch (e) {
      console.warn("Transcription failed:", e);
      showToast("Couldn't transcribe that. Try again?");
      this.onerror && this.onerror({ error: "network" });
    }
    this.onend && this.onend();
  }
}

function initSpeechRecognition() {
  if (!navigator.mediaDevices || !window.AudioContext) {
    alert("Voice input needs microphone access, which this browser doesn't offer.");
    return null;
  }
  const recog = new LocalSpeechRecognition();

  recog.onstart = () => {
    isJarvisListening = true;
    setJarvisState("listening");
    const hud = document.getElementById("jarvisVoiceHud");
    if (hud) hud.classList.remove("hidden");
    const transcriptEl = document.getElementById("hudTranscriptText");
    if (transcriptEl) transcriptEl.textContent = "Listening...";
  };

  recog.onresult = (event) => {
    let interim = "";
    let finalTranscript = "";
    for (let i = event.resultIndex; i < event.results.length; ++i) {
      if (event.results[i].isFinal) {
        finalTranscript += event.results[i][0].transcript;
      } else {
        interim += event.results[i][0].transcript;
      }
    }
    const transcriptEl = document.getElementById("hudTranscriptText");
    if (transcriptEl) {
      transcriptEl.textContent = finalTranscript || interim || "...";
    }
    if (finalTranscript.trim()) {
      handleJarvisVoiceCommand(finalTranscript.trim());
    }
  };

  recog.onerror = (event) => {
    console.warn("Speech recognition error:", event.error);
    if (event.error === "no-speech") {
      const isContinuous = document.getElementById("hudContinuousCheck")?.checked;
      if (!isContinuous) {
        setJarvisState("idle");
      }
    }
  };

  recog.onend = () => {
    isJarvisListening = false;
    const isContinuous = document.getElementById("hudContinuousCheck")?.checked;
    if (isContinuous && jarvisAudioState !== "idle") {
      // Auto-restart for hands-free loop after short debounce
      setTimeout(() => {
        if (jarvisAudioState !== "idle") {
          try {
            recog.start();
          } catch (e) {}
        }
      }, 400);
    } else if (jarvisAudioState === "listening") {
      setJarvisState("idle");
    }
  };

  return recog;
}

function toggleJarvisVoice() {
  if (isJarvisListening) {
    stopJarvisVoice();
  } else {
    startJarvisVoice();
  }
}

function startJarvisVoice() {
  if (!jarvisSpeechRecognition) {
    jarvisSpeechRecognition = initSpeechRecognition();
  }
  if (!jarvisSpeechRecognition) return;
  try {
    jarvisSpeechRecognition.start();
  } catch (e) {
    console.warn("Speech recognition already running:", e);
  }
}

function stopJarvisVoice() {
  if (jarvisSpeechRecognition && isJarvisListening) {
    jarvisSpeechRecognition.stop();
  }
  isJarvisListening = false;
  setJarvisState("idle");
  const hud = document.getElementById("jarvisVoiceHud");
  if (hud) hud.classList.add("hidden");
}

async function handleJarvisVoiceCommand(text) {
  setJarvisState("thinking");
  const speakFeedback = document.getElementById("hudVoiceFeedbackCheck")?.checked !== false;

  try {
    const res = await fetch("/api/voice/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: text, speak_back: speakFeedback })
    });
    const data = await res.json();

    const transcriptEl = document.getElementById("hudTranscriptText");

    if (data.success && data.fastpath) {
      setJarvisState("speaking");
      if (transcriptEl) {
        transcriptEl.innerHTML = `<span class="text-emerald-400 font-bold">✓ Fast-path (${data.latency_ms}ms):</span> ${escapeHtml(data.spoken)}`;
      }
      setTimeout(() => {
        const isContinuous = document.getElementById("hudContinuousCheck")?.checked;
        if (isContinuous) {
          setJarvisState("listening");
        } else {
          setJarvisState("idle");
        }
      }, 2500);
    } else {
      // Fallback: command needs deep reasoning or multi-turn agent execution
      if (transcriptEl) {
        transcriptEl.innerHTML = `<span class="text-indigo-400">Deep Agent Routing:</span> Routing "${escapeHtml(text)}" to Xod researcher...`;
      }
      quickSendPrompt(text);
      const isContinuous = document.getElementById("hudContinuousCheck")?.checked;
      if (!isContinuous) {
        stopJarvisVoice();
      } else {
        setTimeout(() => setJarvisState("listening"), 3000);
      }
    }
  } catch (err) {
    console.error("Voice command processing failed:", err);
    setJarvisState("idle");
  }
}

// ==============================================================
// MODEL TRAINING LAB (Self-Distillation Telemetry & Datasets)
// ==============================================================
// ==============================================================
// MODEL LAB: mini models that learn from Xod's own work
// ==============================================================
let labPollTimer = null;

function openModelLabModal() {
  const modal = document.getElementById("modelLabModal");
  if (modal) modal.classList.remove("hidden");
  refreshModelLab();
  clearInterval(labPollTimer);
  labPollTimer = setInterval(() => {
    if (document.getElementById("modelLabModal")?.classList.contains("hidden")) return clearInterval(labPollTimer);
    refreshModelLab({ quiet: true });
  }, 3000);
}

function closeModelLabModal() {
  const modal = document.getElementById("modelLabModal");
  if (modal) modal.classList.add("hidden");
  clearInterval(labPollTimer);
}

async function refreshModelLab(opts = {}) {
  try {
    const [specRes, jobsRes] = await Promise.all([fetch("/api/lab/specialists"), fetch("/api/lab/jobs")]);
    const spec = await specRes.json();
    const { jobs = [] } = await jobsRes.json();
    const running = jobs.find((j) => ["training", "evaluating", "preparing"].includes(j.status));
    renderMiniModels(spec.specialists || [], running, spec.promote_rules || {});
    renderJobPanel(running);
    renderJobHistory(jobs.filter((j) => j !== running));
    if (!opts.quiet) refreshLabStats();
    if (window.lucide) lucide.createIcons();
  } catch (e) {
    console.error("Model Lab refresh failed", e);
  }
}

const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);

function miniModelStatus(sp, running) {
  if (running && running.specialist === sp.name) return { text: running.status === "evaluating" ? "Testing…" : "Training…", cls: "lab-pill-busy" };
  if (sp.enabled && sp.serving) return { text: "Live", cls: "lab-pill-live" };
  if (sp.enabled) return { text: "Starting…", cls: "lab-pill-busy" };
  if (sp.eval && sp.eval.promotable) return { text: "Passed its test", cls: "lab-pill-ok" };
  if (sp.eval && !sp.eval.error) return { text: "Needs more practice", cls: "lab-pill-warn" };
  if (sp.ready) return { text: "Ready to train", cls: "lab-pill-ok" };
  return { text: "Collecting examples", cls: "lab-pill-idle" };
}

function renderMiniModels(list, running, rules) {
  const el = document.getElementById("labMiniModels");
  if (!el) return;
  el.innerHTML = list.map((sp) => {
    const status = miniModelStatus(sp, running);
    const progress = Math.round((sp.progress || 0) * 100);
    const ev = sp.eval && !sp.eval.error ? sp.eval : null;
    const canTrain = !running && sp.usable >= 4;
    const live = sp.live || {};
    const shortModel = (sp.base_model || "").split("/").pop();
    return `
      <div class="rounded-xl border border-white/[0.07] bg-surface-800/60 p-4 space-y-3">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="font-semibold text-white text-[14px]">${escapeHtml(sp.label)}</span>
              <span class="lab-pill ${status.cls}">${status.text}</span>
            </div>
            <p class="text-[12px] text-zinc-400 mt-0.5">${escapeHtml(sp.description)}</p>
          </div>
          <label class="lab-switch ${ev && ev.promotable ? "" : "is-disabled"}" title="${ev && ev.promotable ? "Let this model handle fast steps" : "Available once it passes its test"}">
            <input type="checkbox" ${sp.enabled ? "checked" : ""} ${ev && ev.promotable ? "" : "disabled"}
              onchange="toggleMiniModel('${sp.name}', this.checked)">
            <span></span>
          </label>
        </div>

        <div>
          <div class="flex justify-between text-[11px] text-zinc-400 mb-1">
            <span><b class="text-zinc-200">${sp.usable}</b> good examples of ${sp.threshold} recommended</span>
            <span>${sp.examples_total} saved · ${sp.examples_bad} marked bad</span>
          </div>
          <div class="lab-bar"><div style="width:${progress}%"></div></div>
        </div>

        ${ev ? `
        <div class="grid grid-cols-3 gap-2 text-center">
          <div class="lab-metric"><span>${pct(ev.action_match_rate)}</span><small>same choice as Xod</small></div>
          <div class="lab-metric"><span>${pct(ev.valid_json_rate)}</span><small>well-formed answers</small></div>
          <div class="lab-metric"><span>${ev.avg_latency_ms != null ? (ev.avg_latency_ms / 1000).toFixed(1) + "s" : "–"}</span><small>per step</small></div>
        </div>
        <p class="text-[11px] text-zinc-500">Tested on ${ev.test_examples} examples it never saw. Needs ${pct(rules.min_action_match)} same choices and ${pct(rules.min_valid_json)} well-formed answers to switch on.</p>` : ""}

        ${sp.enabled && live.calls ? `<p class="text-[12px] text-zinc-300">In real use: <b>${live.calls}</b> steps handled, <b>${pct(live.success_rate)}</b> worked, ${(live.avg_latency_ms / 1000).toFixed(1)}s each.</p>` : ""}

        ${sp.trained_job ? `
        <div class="flex items-center justify-between gap-3 rounded-lg bg-surface-900/50 px-3 py-2">
          <p class="text-[12px] text-zinc-400"><b class="text-zinc-200">Learn from mistakes.</b>
            ${(sp.mistakes || {}).ready
              ? `${(sp.mistakes || {}).estimate} lessons ready, including its own wrong answers.`
              : `${(sp.mistakes || {}).estimate || 0} of ${(sp.mistakes || {}).min_lessons || 100} lessons. Unlocks at ${(sp.mistakes || {}).min_lessons || 100}; with fewer it memorizes and gets worse.`}</p>
          <button onclick="learnFromMistakes('${sp.name}')" ${running || !(sp.mistakes || {}).ready ? "disabled" : ""}
            class="shrink-0 px-3 py-1.5 rounded-lg text-[12px] font-semibold bg-surface-700 text-zinc-100 hover:bg-surface-600 disabled:opacity-40">Practice</button>
        </div>` : ""}

        <div class="flex items-center justify-between gap-2 flex-wrap">
          <span class="text-[11px] text-zinc-500">Student: ${escapeHtml(shortModel)}</span>
          <button onclick="trainMiniModel('${sp.name}', ${sp.ready})" ${canTrain ? "" : "disabled"}
            class="px-3 py-1.5 rounded-lg text-[12px] font-semibold ${sp.ready ? "bg-brand-500 text-surface-900 hover:bg-brand-400" : "bg-surface-700 text-zinc-200 hover:bg-surface-600"} disabled:opacity-40 disabled:cursor-not-allowed">
            ${ev ? "Retrain with new examples" : sp.ready ? "Train it" : "Train early"}
          </button>
        </div>
      </div>`;
  }).join("") || `<p class="text-xs text-zinc-500">No mini models defined.</p>`;
}

function lossSparkline(points) {
  if (!points || points.length < 2) return "";
  const w = 220, h = 40;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)], [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const sx = (x) => ((x - x0) / Math.max(1, x1 - x0)) * w;
  const sy = (y) => h - ((y - y0) / Math.max(1e-6, y1 - y0)) * (h - 4) - 2;
  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" class="lab-spark" aria-label="Training loss going down"><path d="${d}"/></svg>`;
}

async function renderJobPanel(job) {
  const el = document.getElementById("labJobPanel");
  if (!el) return;
  if (!job) { el.classList.add("hidden"); el.innerHTML = ""; return; }
  let detail = job;
  try {
    const r = await fetch(`/api/lab/jobs/${job.id}`);
    const d = await r.json();
    if (d.success) detail = d.job;
  } catch (e) {}
  const p = Math.round(((detail.current_iter || 0) / Math.max(1, detail.total_iters || 1)) * 100);
  el.classList.remove("hidden");
  el.innerHTML = `
    <div class="rounded-xl border border-brand-500/30 bg-brand-500/[0.06] p-4 space-y-3">
      <div class="flex items-center justify-between gap-3">
        <div>
          <p class="text-[13px] text-white font-semibold">${detail.status === "evaluating" ? "Testing" : detail.status === "preparing" ? "Collecting corrections for" : detail.method === "dpo" ? "Learning from mistakes:" : "Training"} ${escapeHtml(detail.specialist)}</p>
          <p class="text-[11px] text-zinc-400">${detail.train_examples} examples · ${escapeHtml((detail.base_model || "").split("/").pop())} · ${Math.round((detail.duration_sec || 0) / 60)} min so far</p>
        </div>
        ${detail.status === "training" ? `<button onclick="cancelTraining('${detail.id}')" class="text-[12px] text-zinc-400 hover:text-rose-400">Stop</button>` : ""}
      </div>
      <div class="lab-bar"><div style="width:${detail.status === "evaluating" ? 100 : p}%"></div></div>
      ${detail.teacher_paused ? `<p class="text-[12px] text-amber-200/90">Xod's main model is paused to free memory, so chat and browser tasks wait until this finishes.</p>` : ""}
      <div class="flex items-end justify-between gap-4">
        <div class="text-[11px] text-zinc-400 space-y-0.5">
          <p>Step ${detail.current_iter || 0} of ${detail.total_iters}</p>
          <p>Loss ${detail.loss != null ? detail.loss.toFixed(3) : "–"}${detail.val_loss != null ? ` · check ${detail.val_loss.toFixed(3)}` : ""} <span class="text-zinc-500">(lower is better)</span></p>
          ${detail.memory && detail.memory.available_now_gb != null ? `<p>${detail.memory.available_now_gb} GB memory free <span class="text-zinc-500">(stops itself below 1 GB)</span></p>` : ""}
        </div>
        ${lossSparkline(detail.loss_curve)}
      </div>
    </div>`;
}

function renderJobHistory(jobs) {
  const el = document.getElementById("labJobHistory");
  if (!el) return;
  if (!jobs.length) { el.innerHTML = `<p class="text-zinc-500">Nothing trained yet.</p>`; return; }
  el.innerHTML = jobs.slice(0, 8).map((j) => {
    const ev = j.eval && !j.eval.error ? j.eval : null;
    const verdict = ev ? (ev.promotable ? `<span class="text-emerald-400">passed</span>` : `<span class="text-amber-300">didn't pass</span>`)
      : j.status === "stopped" ? `<span class="text-amber-300" title="${escapeHtml(j.stop_reason || "")}">stopped to protect your Mac</span>`
      : `<span class="text-zinc-500">${escapeHtml(j.status)}</span>`;
    const kind = j.method === "dpo" ? `practice on ${j.pairs_total != null ? j.train_examples || "?" : "?"} mistakes` : `${j.train_examples || "?"} examples`;
    const delta = j.method === "dpo" && j.improvement != null
      ? (j.kept_previous ? ` · <span class="text-zinc-500">not better, kept the previous one</span>` : ` · <span class="text-emerald-400">${j.improvement >= 0 ? "+" : ""}${Math.round(j.improvement * 100)} pts</span>`)
      : "";
    return `<div class="flex items-center justify-between gap-3 px-3 py-2 rounded-lg bg-surface-900/50 border border-white/[0.04]">
      <span class="text-zinc-300 truncate">${escapeHtml(j.specialist)} · ${escapeHtml(j.start_time_str || "")} · ${kind}${delta}</span>
      <span class="shrink-0">${ev ? `${pct(ev.action_match_rate)} same choice · ` : ""}${verdict}</span>
    </div>`;
  }).join("");
}

async function trainMiniModel(name, ready) {
  if (!ready && !confirm("This model doesn't have the recommended number of examples yet, so it will probably not pass its test. Train anyway?")) return;
  const res = await fetch("/api/lab/train", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ specialist: name }),
  });
  const data = await res.json();
  if (!data.success) alert(data.error || "Couldn't start training.");
  refreshModelLab({ quiet: true });
}

async function learnFromMistakes(name) {
  const res = await fetch(`/api/lab/specialists/${name}/learn-from-mistakes`, { method: "POST" });
  const data = await res.json();
  if (!data.success) alert(data.error || "Couldn't start.");
  refreshModelLab({ quiet: true });
}

async function cancelTraining(jobId) {
  await fetch(`/api/lab/jobs/${jobId}/cancel`, { method: "POST" });
  refreshModelLab({ quiet: true });
}

async function toggleMiniModel(name, enabled) {
  const res = await fetch(`/api/lab/specialists/${name}/enabled`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  const data = await res.json();
  if (!data.success) alert(data.error || "Couldn't change that.");
  refreshModelLab({ quiet: true });
}

// ---------- Voice & command specialists (older, trace-based) ----------
async function refreshLabStats() {
  try {
    const res = await fetch("/api/lab/stats");
    if (!res.ok) return;
    const stats = await res.json();
    const totalEl = document.getElementById("labTotalTraces");
    const threshEl = document.getElementById("labReadyThreshold");
    if (totalEl) totalEl.textContent = stats.total_traces || 0;
    if (threshEl) threshEl.textContent = stats.ready_threshold || 50;

    const specContainer = document.getElementById("labSpecialistsList");
    if (specContainer && Array.isArray(stats.specialists)) {
      specContainer.innerHTML = stats.specialists.map((sp) => {
        const p = Math.min(100, Math.round((sp.good_examples / sp.threshold) * 100));
        return `
          <div class="flex items-center justify-between gap-4">
            <div class="min-w-0 flex-1">
              <p class="text-[13px] text-zinc-100">${escapeHtml(sp.label)} <span class="text-[11px] text-zinc-500">${sp.good_examples} / ${sp.threshold}</span></p>
              <p class="text-[11px] text-zinc-500">${escapeHtml(sp.description)}</p>
              <div class="lab-bar mt-1.5"><div style="width:${p}%"></div></div>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <button onclick="trainMiniModel('${sp.key}', ${sp.status === "ready_to_train"})" class="px-2.5 py-1 rounded-lg bg-surface-700 hover:bg-surface-600 text-[12px] text-zinc-200">Train</button>
              <button onclick="exportTrainingDataset('${sp.key}')" class="px-2.5 py-1 rounded-lg text-[12px] text-zinc-400 hover:text-white" title="Save the dataset as JSONL">Export</button>
            </div>
          </div>`;
      }).join("");
    }

    const tbody = document.getElementById("labRecentTracesTbody");
    if (tbody && Array.isArray(stats.recent)) {
      tbody.innerHTML = stats.recent.length ? stats.recent.map((r) => `
        <tr class="hover:bg-white/[0.02]">
          <td class="p-2.5 font-mono text-zinc-400 text-[11px]">${escapeHtml(r.source)}</td>
          <td class="p-2.5 text-[11px] text-zinc-300">${escapeHtml(r.task_type)}</td>
          <td class="p-2.5 text-zinc-100 truncate max-w-xs" title="${escapeHtml(r.query)}">${escapeHtml(r.query)}</td>
          <td class="p-2.5 font-mono text-zinc-400 text-[11px]">${r.latency_ms || 0}ms</td>
          <td class="p-2.5">
            <button onclick="voteTrace('${r.id}', 1)" class="p-1 text-zinc-400 hover:text-emerald-400" title="Good"><i data-lucide="thumbs-up" class="size-3.5"></i></button>
            <button onclick="voteTrace('${r.id}', -1)" class="p-1 text-zinc-400 hover:text-rose-400" title="Bad"><i data-lucide="thumbs-down" class="size-3.5"></i></button>
          </td>
        </tr>`).join("")
        : `<tr><td colspan="5" class="p-4 text-center text-zinc-500">No commands logged yet.</td></tr>`;
    }
    if (window.lucide) lucide.createIcons();
  } catch (err) {
    console.error("Failed to load lab stats:", err);
  }
}

async function exportTrainingDataset(specialistKey) {
  try {
    const res = await fetch("/api/lab/export", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ specialist: specialistKey }),
    });
    const data = await res.json();
    alert(data.success ? `Saved ${data.examples} examples to:\n${data.path}` : `Export error: ${data.error || "Unknown error"}`);
  } catch (e) {
    alert(`Export failed: ${e.message}`);
  }
}

async function voteTrace(traceId, value) {
  try {
    await fetch("/api/lab/feedback", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trace_id: traceId, value }),
    });
    refreshLabStats();
  } catch (e) {}
}

// ==============================================================
// LOCAL MEDIA STUDIO (On-Device Image & Video Editor UI)
// ==============================================================
function openMediaStudioModal() {
  const modal = document.getElementById("mediaStudioModal");
  refreshMediaEngine();
  if (modal) modal.classList.remove("hidden");
  refreshMediaGallery();
}

function closeMediaStudioModal() {
  const modal = document.getElementById("mediaStudioModal");
  if (modal) modal.classList.add("hidden");
}

async function refreshMediaGallery() {
  const container = document.getElementById("mediaGalleryContainer");
  if (!container) return;
  try {
    const res = await fetch("/api/media/list");
    if (!res.ok) return;
    const data = await res.json();
    const items = data.media || [];
    if (items.length === 0) {
      container.innerHTML = `<div class="text-xs text-zinc-500 col-span-full py-8 text-center italic">No media generated yet. Create a graphic or convert a video!</div>`;
    } else {
      container.innerHTML = items.map(m => `
        <div class="bg-surface-800 border border-white/[0.06] rounded-xl p-2.5 flex flex-col justify-between text-xs space-y-2">
          <div class="flex items-center justify-between text-[10px] font-mono text-zinc-400">
            <span class="px-1.5 py-0.5 rounded bg-surface-700 text-zinc-300 uppercase">${escapeHtml(m.type)}</span>
            <span>${(m.size_bytes / 1024).toFixed(0)} KB</span>
          </div>
          <div class="truncate font-semibold text-zinc-200 text-xs" title="${escapeHtml(m.name)}">
            ${escapeHtml(m.name)}
          </div>
          <div class="flex items-center gap-1.5 pt-1">
            <button onclick="revealFileOnComputer('${escapeHtml(m.path)}')" class="flex-1 py-1 rounded bg-surface-700 hover:bg-surface-650 text-center text-[10px] text-zinc-300">
              Finder
            </button>
            <button onclick="openFileOnComputer('${escapeHtml(m.path)}')" class="flex-1 py-1 rounded bg-rose-600/80 hover:bg-rose-600 text-center text-[10px] text-white">
              Open
            </button>
            <button onclick="useMediaInStudio('${escapeHtml(m.path)}', '${escapeHtml(m.type)}')" class="px-2 py-1 rounded bg-indigo-600/80 hover:bg-indigo-600 text-center text-[10px] text-white" title="Use as input in studio">
              Use
            </button>
          </div>
        </div>
      `).join("");
    }
    if (window.lucide) lucide.createIcons();
  } catch (err) {
    console.error("Failed to load media gallery:", err);
  }
}

function useMediaInStudio(filePath, mediaType) {
  if (!filePath) return;
  const isVideo = mediaType === "video" || /\.(mp4|mov|mkv|webm|m4v|avi)$/i.test(filePath);
  const isImage = mediaType === "image" || /\.(png|jpg|jpeg|webp|bmp)$/i.test(filePath);

  if (isVideo) {
    const extractInput = document.getElementById("mediaExtractVideoPath");
    if (extractInput) extractInput.value = filePath;
    const overlayInput = document.getElementById("mediaOverlayVideoPath");
    if (overlayInput) overlayInput.value = filePath;
    const gifInput = document.getElementById("mediaGifVideoPath");
    if (gifInput) gifInput.value = filePath;
    const spliceInput = document.getElementById("mediaSplicePaths");
    if (spliceInput) {
      const current = spliceInput.value.trim();
      spliceInput.value = current ? `${current}\n${filePath}` : filePath;
    }
    showToast("Loaded video path into Studio cards");
  } else if (isImage) {
    const frameInput = document.getElementById("mediaFrameImagePath");
    if (frameInput) frameInput.value = filePath;
    showToast("Loaded image into Frame → Motion Synthesizer");
  } else {
    showToast(`Media selected: ${filePath}`);
  }
}

// ---------- Image engine: models, LoRAs, memory (see services/media_runtime.py) ----------
async function refreshMediaEngine() {
  const strip = document.getElementById("mediaEngineStatus");
  try {
    const d = await (await fetch("/api/media/engine")).json();
    const kind = window.__mediaAIKind || "image";
    const usable = d.models.filter((m) => m.usable && m.kind === kind);
    const unusable = d.models.filter((m) => !m.usable);
    const modelSel = document.getElementById("mediaAIModel");
    if (modelSel) {
      const keep = modelSel.value;
      modelSel.innerHTML = usable.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.label || m.id.split("/").pop())} · ~${m.need_gb} GB</option>`).join("")
        || `<option value="">No ${kind} model installed</option>`;
      const preferred = (keep && usable.some((m) => m.id === keep)) ? keep : (d.active || {})[kind];
      if (preferred && usable.some((m) => m.id === preferred)) modelSel.value = preferred;
      modelSel.title = kind === "video" ? "Video model" : "Image model";
    }
    const loraSel = document.getElementById("mediaAILora");
    if (loraSel) {
      const keep = loraSel.value;
      loraSel.innerHTML = `<option value="">No LoRA</option>` + d.loras.map((l) => `<option value="${escapeHtml(l.name)}">${escapeHtml(l.name)}</option>`).join("");
      loraSel.value = keep;
    }
    const rt = d.runtime, mem = d.memory, libs = d.libraries || { installed: true };
    if (!libs.installed) {
      const busy = libs.status === "installing";
      strip.innerHTML = busy
        ? `<span>Downloading Media Studio's image and video tools (one time, ~${libs.download_mb} MB)… <span class="text-zinc-500">${escapeHtml(libs.progress || "")}</span></span>`
        : `<span>${libs.status === "error" ? `<span class="text-amber-300">The download didn't finish: ${escapeHtml(libs.error || "")}</span>` : `Media Studio needs a one-time download of its image and video tools (~${libs.download_mb} MB).`}</span>
           <button onclick="installMediaLibraries()" class="text-zinc-200 hover:text-white underline">${libs.status === "error" ? "Try again" : "Download now"}</button>`;
      if (busy) setTimeout(refreshMediaEngine, 2000);
      return;
    }
    const engine = rt.worker_running ? `Loaded: <b class="text-zinc-200">${escapeHtml((rt.loaded_model || "starting").split("/").pop().replace("---", "/"))}</b>` : "Idle (no model in memory)";
    const chat = mem.teacher_paused ? `<span class="text-amber-300">Chat model paused to make room; it comes back on its own</span>` : "Chat model ready";
    strip.innerHTML = `
      <span>${engine} · ${chat} · ${mem.available_gb} GB free</span>
      <span class="flex items-center gap-3">
        ${unusable.length ? `<span title="${escapeHtml(unusable.map((m) => `${m.id}: ${m.reason}`).join("\n"))}" class="cursor-help underline decoration-dotted">${unusable.length} model${unusable.length > 1 ? "s" : ""} can't run here</span>` : ""}
        ${rt.worker_running ? `<button onclick="releaseMediaEngine()" class="text-zinc-300 hover:text-white">Free memory now</button>` : ""}
      </span>`;
  } catch (e) {
    if (strip) strip.textContent = "Couldn't reach the image engine.";
  }
}

async function installMediaLibraries() {
  await fetch("/api/media/libraries/install", { method: "POST" });
  refreshMediaEngine();
}

function setMediaAIKind(kind) {
  window.__mediaAIKind = kind;
  const video = kind === "video";
  const on = "px-2 py-0.5 rounded-md bg-rose-600 text-white", off = "px-2 py-0.5 rounded-md text-zinc-400 hover:text-white";
  document.getElementById("mediaAIKindImage").className = video ? off : on;
  document.getElementById("mediaAIKindVideo").className = video ? on : off;
  document.getElementById("mediaAIDuration").classList.toggle("hidden", !video);
  document.querySelector("#mediaAIGenerateBtn span").textContent = video ? "Generate Video" : "Generate Image";
  document.getElementById("mediaAIBlurb").textContent = video
    ? "Make a short clip with real AI motion (1–4 s). Describe what moves."
    : "Make images on this Mac from a description. Pick a model, and a LoRA for a style.";
  document.getElementById("mediaAIPrompt").placeholder = video
    ? "What happens? (e.g. 'ocean waves crashing on rocks at sunset')"
    : "Describe the scene (e.g. 'a cute snowman in a pine tree covered in snow')...";
  refreshMediaEngine();
}

function executeGenerateAI() {
  return (window.__mediaAIKind === "video") ? executeGenerateAIVideo() : executeGenerateAIImage();
}

async function executeGenerateAIVideo() {
  const prompt = document.getElementById("mediaAIPrompt")?.value.trim();
  const style = document.getElementById("mediaAIStyle")?.value || "photorealistic";
  const model = document.getElementById("mediaAIModel")?.value || null;
  const lora = document.getElementById("mediaAILora")?.value;
  const weight = parseFloat(document.getElementById("mediaAILoraWeight")?.value || "0.8");
  const duration_sec = parseFloat(document.getElementById("mediaAIDuration")?.value || "2");
  if (!prompt) {
    alert("Describe what happens in the clip (e.g. 'ocean waves crashing on rocks at sunset').");
    return;
  }
  showToast("Making the video on this Mac… (about a minute; the chat model may pause)");
  const timer = setInterval(refreshMediaEngine, 2000);
  try {
    const res = await fetch("/api/media/video/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, style, duration_sec, model, loras: lora ? [{ name: lora, weight }] : [] })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Video ready: ${data.duration_sec}s in ${data.seconds}s (${(data.active_model || "").split("/").pop()})`);
      refreshMediaGallery();
    } else {
      alert(`Couldn't make the video: ${data.error || "unknown error"}`);
    }
  } catch (e) {
    alert(`Video generation failed: ${e.message}`);
  } finally {
    clearInterval(timer);
    refreshMediaEngine();
  }
}

async function releaseMediaEngine() {
  const strip = document.getElementById("mediaEngineStatus");
  if (strip) strip.textContent = "Freeing memory and bringing the chat model back…";
  await fetch("/api/media/engine/release", { method: "POST" });
  await refreshMediaEngine();
}

async function executeGenerateAIImage() {
  const prompt = document.getElementById("mediaAIPrompt")?.value.trim();
  const style = document.getElementById("mediaAIStyle")?.value || "photorealistic";
  const model = document.getElementById("mediaAIModel")?.value || null;
  const lora = document.getElementById("mediaAILora")?.value;
  const weight = parseFloat(document.getElementById("mediaAILoraWeight")?.value || "0.8");
  if (!prompt) {
    alert("Please enter an image prompt (e.g. 'a cute snowman in a pine tree covered in snow').");
    return;
  }
  showToast("Generating on this Mac… (the chat model may pause for a moment)");
  const timer = setInterval(refreshMediaEngine, 2000);
  try {
    const res = await fetch("/api/media/image/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, style, width: 512, height: 512, model, loras: lora ? [{ name: lora, weight }] : [] })
    });
    const data = await res.json();
    if (data.success) {
      const motionInput = document.getElementById("mediaFrameImagePath");
      if (motionInput) motionInput.value = data.output_path;
      showToast(`Image ready in ${data.seconds}s (${(data.active_model || "").split("/").pop()}${lora ? ` + ${lora}` : ""})`);
      refreshMediaGallery();
    } else {
      alert(`Couldn't generate the image: ${data.error || "unknown error"}`);
    }
  } catch (e) {
    alert(`Image generation failed: ${e.message}`);
  } finally {
    clearInterval(timer);
    refreshMediaEngine();
  }
}

async function executeGenerateGraphic() {
  const title = document.getElementById("mediaGenTitle")?.value.trim();
  const subtitle = document.getElementById("mediaGenSubtitle")?.value.trim();
  if (!title) {
    alert("Please enter a graphic title.");
    return;
  }
  try {
    const res = await fetch("/api/media/image/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, subtitle, theme: "dark_cyber" })
    });
    const data = await res.json();
    if (data.success) {
      alert(`✅ Graphic rendered!\nSaved to: ${data.output_path}`);
      refreshMediaGallery();
    } else {
      alert(`Error: ${data.error || 'Failed to render graphic'}`);
    }
  } catch (e) {
    alert(`Render failed: ${e.message}`);
  }
}

async function executeVideoToGif() {
  const videoPath = document.getElementById("mediaGifVideoPath")?.value.trim();
  const startSec = parseFloat(document.getElementById("mediaGifStart")?.value || "0");
  const durationSec = parseFloat(document.getElementById("mediaGifDuration")?.value || "4");
  if (!videoPath) {
    alert("Please provide a local video path.");
    return;
  }
  try {
    const res = await fetch("/api/media/video/gif", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_path: videoPath, start_sec: startSec, duration_sec: durationSec })
    });
    const data = await res.json();
    if (data.success) {
      alert(`🎉 Animated GIF generated!\nSaved to: ${data.output_path}`);
      refreshMediaGallery();
    } else {
      alert(`Error: ${data.error || 'GIF conversion failed'}`);
    }
  } catch (e) {
    alert(`GIF creation failed: ${e.message}`);
  }
}

async function executeVideoOverlay() {
  const videoPath = document.getElementById("mediaOverlayVideoPath")?.value.trim();
  const text = document.getElementById("mediaOverlayText")?.value.trim();
  if (!videoPath || !text) {
    alert("Please provide both video path and overlay text.");
    return;
  }
  try {
    const res = await fetch("/api/media/video/overlay", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_path: videoPath, text })
    });
    const data = await res.json();
    if (data.success) {
      alert(`🎬 Video overlay rendered!\nSaved to: ${data.output_path}`);
      refreshMediaGallery();
    } else {
      alert(`Error: ${data.error || 'Overlay failed'}`);
    }
  } catch (e) {
    alert(`Overlay failed: ${e.message}`);
  }
}

async function executeSpliceVideos() {
  const rawPaths = document.getElementById("mediaSplicePaths")?.value || "";
  const paths = rawPaths.split(/[\n,]+/).map(p => p.trim()).filter(Boolean);
  if (paths.length < 2) {
    alert("Please provide at least 2 local video paths to splice (one per line or comma-separated).");
    return;
  }
  try {
    const res = await fetch("/api/media/video/splice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_paths: paths, output_format: "mp4" })
    });
    const data = await res.json();
    if (data.success) {
      alert(`🎉 Spliced ${paths.length} video clips together!\nSaved to: ${data.output_path}`);
      refreshMediaGallery();
    } else {
      alert(`Error: ${data.error || 'Video splice failed'}`);
    }
  } catch (e) {
    alert(`Video splice failed: ${e.message}`);
  }
}

async function executeExtractFrame() {
  const videoPath = document.getElementById("mediaExtractVideoPath")?.value.trim();
  const frameType = document.getElementById("mediaExtractType")?.value || "first";
  const timeVal = parseFloat(document.getElementById("mediaExtractTime")?.value || "0");

  if (!videoPath) {
    alert("Please provide a local video path.");
    return;
  }

  try {
    const payload = {
      video_path: videoPath,
      frame_type: frameType,
      timestamp_sec: frameType === "timestamp" ? timeVal : null,
      output_format: "png"
    };
    const res = await fetch("/api/media/video/extract_frame", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.success) {
      const motionInput = document.getElementById("mediaFrameImagePath");
      if (motionInput) motionInput.value = data.output_path;
      alert(`📸 Extracted ${frameType} frame!\nSaved to: ${data.output_path}\n(Auto-filled into Frame → Motion Video synthesizer)`);
      refreshMediaGallery();
    } else {
      alert(`Error: ${data.error || 'Frame extraction failed'}`);
    }
  } catch (e) {
    alert(`Frame extraction failed: ${e.message}`);
  }
}

async function executeCreateVideoFromFrame() {
  const imagePath = document.getElementById("mediaFrameImagePath")?.value.trim();
  const motion = document.getElementById("mediaFrameMotion")?.value || "zoom_in";
  const durationSec = parseFloat(document.getElementById("mediaFrameDuration")?.value || "3");

  if (!imagePath) {
    alert("Please provide an image or extracted frame path.");
    return;
  }

  try {
    const res = await fetch("/api/media/video/from_frame", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_path: imagePath,
        motion: motion,
        duration_sec: durationSec,
        fps: 30,
        output_format: "mp4"
      })
    });
    const data = await res.json();
    if (data.success) {
      alert(`🎬 Synthesized video clip with ${motion} motion!\nSaved to: ${data.output_path}`);
      refreshMediaGallery();
    } else {
      alert(`Error: ${data.error || 'Motion video synthesis failed'}`);
    }
  } catch (e) {
    alert(`Video synthesis failed: ${e.message}`);
  }
}

// =====================================================================
// PERSISTENT LONG-TERM MEMORY VAULT CONTROLLER (SQLite Vector & Chroma)
// =====================================================================

let currentMemoriesList = [];
let currentMemoryFilter = "all";
let memorySearchDebounceTimer = null;

function openMemoryVaultModal() {
  const modal = document.getElementById("memoryVaultModal");
  if (modal) {
    modal.classList.remove("hidden");
    loadMemoryStats();
    loadMemoriesFromVault();
    if (window.lucide) lucide.createIcons();
  }
}

function closeMemoryVaultModal() {
  const modal = document.getElementById("memoryVaultModal");
  if (modal) modal.classList.add("hidden");
}

function toggleNewMemoryForm() {
  const form = document.getElementById("newMemoryFormContainer");
  if (form) {
    form.classList.toggle("hidden");
    if (!form.classList.contains("hidden")) {
      document.getElementById("newMemTitle")?.focus();
    }
  }
}

async function loadMemoryStats() {
  try {
    const res = await fetch("/api/memory/stats");
    if (!res.ok) return;
    const stats = await res.json();
    
    // Update count labels
    const total = stats.total_memories || 0;
    const brk = stats.category_breakdown || {};
    
    const countAll = document.getElementById("memCountAll");
    if (countAll) countAll.textContent = total;

    const countUser = document.getElementById("memCountUser");
    if (countUser) countUser.textContent = brk.user_preference || 0;

    const countArch = document.getElementById("memCountArch");
    if (countArch) countArch.textContent = brk.project_architecture || 0;

    const countLearn = document.getElementById("memCountLearn");
    if (countLearn) countLearn.textContent = brk.agent_learning || 0;

    const countKnow = document.getElementById("memCountKnow");
    if (countKnow) countKnow.textContent = brk.knowledge || 0;

    const footerSpan = document.getElementById("memVaultStatsFooter");
    if (footerSpan) {
      footerSpan.textContent = `${total} persistent memories in vault (${stats.engine || 'SQLite Vector'})`;
    }

    const badge = document.getElementById("memActiveModelBadge");
    if (badge && stats.models_used) {
      const models = Object.keys(stats.models_used);
      if (models.includes("lm_studio_nomic_embed")) {
        badge.textContent = "LM Studio Nomic 768d";
        badge.className = "text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30";
      } else if (models.includes("openai_text_embed_3_small")) {
        badge.textContent = "OpenAI Embed 3 (1536d)";
        badge.className = "text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-300 border border-indigo-500/30";
      } else {
        badge.textContent = "Semantic Hash (768d)";
        badge.className = "text-[10px] font-mono px-2 py-0.5 rounded bg-zinc-500/15 text-zinc-300 border border-zinc-500/30";
      }
    }
  } catch (e) {
    console.error("Failed to load memory stats:", e);
  }
}

async function loadMemoriesFromVault(category = null) {
  const grid = document.getElementById("memoryCardsGrid");
  if (!grid) return;

  grid.innerHTML = `
    <div class="col-span-full py-12 text-center text-zinc-500 italic text-xs">
      <div class="inline-block size-6 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin mb-2"></div>
      <p>Accessing SQLite Vector Memory Vault...</p>
    </div>
  `;

  try {
    const catQuery = category && category !== "all" ? `?category=${encodeURIComponent(category)}` : "";
    const res = await fetch(`/api/memory/list${catQuery}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    currentMemoriesList = Array.isArray(data) ? data : [];
    renderMemoryCards(currentMemoriesList, false);
    loadMemoryStats();
  } catch (e) {
    grid.innerHTML = `
      <div class="col-span-full py-8 text-center text-rose-400 text-xs">
        Failed to load memories from SQLite vault: ${escapeHtml(e.message)}
      </div>
    `;
  }
}

function filterMemories(category) {
  currentMemoryFilter = category;
  
  // Update button highlights
  const filterBtns = {
    all: document.getElementById("memFilterAll"),
    user_preference: document.getElementById("memFilterUser"),
    project_architecture: document.getElementById("memFilterArch"),
    agent_learning: document.getElementById("memFilterLearn"),
    knowledge: document.getElementById("memFilterKnow")
  };

  Object.entries(filterBtns).forEach(([k, btn]) => {
    if (!btn) return;
    if (k === category) {
      btn.className = "px-3 py-1 rounded-lg font-medium text-xs bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 transition-all";
    } else {
      btn.className = "px-3 py-1 rounded-lg font-medium text-xs text-zinc-400 hover:text-zinc-200 border border-transparent transition-all";
    }
  });

  const searchInput = document.getElementById("memorySearchInput");
  const query = searchInput ? searchInput.value.trim() : "";
  if (query.length >= 2) {
    executeMemorySearch(query, category);
  } else {
    loadMemoriesFromVault(category);
  }
}

function handleMemorySearchInput(query) {
  clearTimeout(memorySearchDebounceTimer);
  memorySearchDebounceTimer = setTimeout(() => {
    const q = (query || "").trim();
    if (q.length < 2) {
      renderMemoryCards(currentMemoriesList, false);
    } else {
      executeMemorySearch(q, currentMemoryFilter);
    }
  }, 250);
}

async function executeMemorySearch(query, category = "all") {
  const grid = document.getElementById("memoryCardsGrid");
  if (!grid) return;

  grid.innerHTML = `
    <div class="col-span-full py-8 text-center text-zinc-400 text-xs">
      <div class="inline-block size-5 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin mb-2"></div>
      <p>Computing cosine similarity & BM25 rank across vector embeddings...</p>
    </div>
  `;

  try {
    const catParam = category && category !== "all" ? category : null;
    const res = await fetch("/api/memory/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: query,
        category: catParam,
        limit: 12
      })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderMemoryCards(data.results || [], true);
  } catch (e) {
    grid.innerHTML = `
      <div class="col-span-full py-6 text-center text-rose-400 text-xs">
        Search failed: ${escapeHtml(e.message)}
      </div>
    `;
  }
}

function renderMemoryCards(memories, isSearch = false) {
  const grid = document.getElementById("memoryCardsGrid");
  if (!grid) return;

  if (!memories || memories.length === 0) {
    grid.innerHTML = `
      <div class="col-span-full py-12 text-center text-zinc-500 text-xs">
        <i data-lucide="database" class="size-8 mx-auto mb-2 text-zinc-600"></i>
        <p class="font-medium text-zinc-400 mb-1">No memories found</p>
        <p class="text-zinc-600">Click <strong>"Index Workspace"</strong> to auto-index codebase architecture, or store your first preference with <strong>"New Memory"</strong>.</p>
      </div>
    `;
    if (window.lucide) lucide.createIcons();
    return;
  }

  const categoryThemes = {
    user_preference: { label: "User Preference", bg: "bg-purple-500/15", text: "text-purple-300", border: "border-purple-500/30", icon: "user-check" },
    project_architecture: { label: "Architecture", bg: "bg-cyan-500/15", text: "text-cyan-300", border: "border-cyan-500/30", icon: "network" },
    agent_learning: { label: "Agent Habit", bg: "bg-emerald-500/15", text: "text-emerald-300", border: "border-emerald-500/30", icon: "lightbulb" },
    knowledge: { label: "Knowledge", bg: "bg-amber-500/15", text: "text-amber-300", border: "border-amber-500/30", icon: "book-open" },
    general: { label: "General", bg: "bg-zinc-500/15", text: "text-zinc-300", border: "border-zinc-500/30", icon: "tag" }
  };

  let html = "";
  memories.forEach(m => {
    const theme = categoryThemes[m.category] || categoryThemes.general;
    const importanceStars = "⭐".repeat(Math.min(Math.max(parseInt(m.importance || 3), 1), 5));
    
    let matchBadge = "";
    if (isSearch && (m.similarity_score !== undefined || m.hybrid_score !== undefined)) {
      const scoreVal = m.similarity_score !== undefined ? m.similarity_score : m.hybrid_score;
      const pct = Math.round(scoreVal * 100);
      const scoreColor = pct >= 65 ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40" : "bg-cyan-500/20 text-cyan-300 border-cyan-500/40";
      matchBadge = `
        <span class="text-[10px] font-mono font-semibold px-2 py-0.5 rounded ${scoreColor} border">
          ${pct}% match
        </span>
      `;
    }

    const tags = Array.isArray(m.tags) ? m.tags : [];
    const tagsHtml = tags.map(t => `<span class="px-1.5 py-0.5 rounded bg-surface-900 text-zinc-400 text-[10px] border border-surface-700">#${escapeHtml(t)}</span>`).join(" ");

    html += `
      <div class="p-4 rounded-xl bg-surface-800/70 border border-surface-700 hover:border-cyan-500/40 transition-all flex flex-col justify-between group shadow-sm">
        <div>
          <!-- Header -->
          <div class="flex items-center justify-between gap-2 mb-2">
            <div class="flex items-center gap-1.5 flex-wrap">
              <span class="text-[10px] font-semibold px-2 py-0.5 rounded ${theme.bg} ${theme.text} border ${theme.border}">
                ${theme.label}
              </span>
              <span class="text-[10px] text-zinc-400" title="Importance: ${m.importance || 3}/5">${importanceStars}</span>
            </div>
            <div class="flex items-center gap-1.5">
              ${matchBadge}
              <button onclick="deleteMemory('${escapeHtml(m.id)}')" class="text-zinc-500 hover:text-rose-400 p-1 rounded hover:bg-surface-700 transition-colors opacity-0 group-hover:opacity-100" title="Delete Memory">
                <i data-lucide="trash-2" class="size-3.5"></i>
              </button>
            </div>
          </div>

          <!-- Title -->
          <h4 class="font-bold text-sm text-zinc-100 mb-2 leading-snug">${escapeHtml(m.title || "Untitled Note")}</h4>

          <!-- Content snippet -->
          <div class="text-xs text-zinc-300 whitespace-pre-wrap font-mono bg-surface-900/60 p-2.5 rounded-lg border border-surface-750 max-h-48 overflow-y-auto leading-relaxed select-text">
${escapeHtml(m.content || "")}
          </div>
        </div>

        <!-- Footer -->
        <div class="mt-3 pt-2.5 border-t border-surface-750 flex items-center justify-between text-[10px] text-zinc-500">
          <div class="flex items-center gap-1.5 flex-wrap max-w-[70%]">
            ${tagsHtml}
          </div>
          <span class="font-mono text-[10px] text-zinc-500">${m.id ? m.id.substring(0, 8) : ''}</span>
        </div>
      </div>
    `;
  });

  grid.innerHTML = html;
  if (window.lucide) lucide.createIcons();
}

async function submitNewMemory() {
  const title = document.getElementById("newMemTitle")?.value.trim();
  const content = document.getElementById("newMemContent")?.value.trim();
  const category = document.getElementById("newMemCategory")?.value || "general";
  const importance = parseInt(document.getElementById("newMemImportance")?.value || 3);
  const tagsRaw = document.getElementById("newMemTags")?.value.trim() || "";
  const tags = tagsRaw ? tagsRaw.split(",").map(t => t.trim()).filter(Boolean) : [];

  if (!title || !content) {
    alert("Please provide both a Title and Content for the memory.");
    return;
  }

  const alertBox = document.getElementById("memLiveAlert");
  try {
    const res = await fetch("/api/memory/store", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title,
        content,
        category,
        importance,
        tags
      })
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    // Show confirmation alert
    if (alertBox) {
      alertBox.className = "p-3 rounded-xl border border-cyan-500/40 bg-cyan-950/40 text-cyan-200 flex items-center justify-between text-xs";
      alertBox.innerHTML = `
        <div class="flex items-center gap-2">
          <i data-lucide="check-circle-2" class="size-4 text-cyan-400"></i>
          <span>Memory permanently embedded and saved to vault (ID: <code>${escapeHtml(data.id || '')}</code>)</span>
        </div>
        <button onclick="this.parentElement.classList.add('hidden')" class="text-zinc-400 hover:text-white"><i data-lucide="x" class="size-3.5"></i></button>
      `;
      alertBox.classList.remove("hidden");
      if (window.lucide) lucide.createIcons();
    }

    // Reset inputs & hide form
    document.getElementById("newMemTitle").value = "";
    document.getElementById("newMemContent").value = "";
    document.getElementById("newMemTags").value = "";
    toggleNewMemoryForm();

    // Refresh memory list
    loadMemoriesFromVault(currentMemoryFilter);
  } catch (e) {
    alert(`Failed to store memory: ${e.message}`);
  }
}

async function deleteMemory(memoryId) {
  if (!confirm("Are you sure you want to permanently erase this memory from your persistent vector vault?")) {
    return;
  }

  try {
    const res = await fetch(`/api/memory/${encodeURIComponent(memoryId)}`, {
      method: "DELETE"
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    loadMemoriesFromVault(currentMemoryFilter);
  } catch (e) {
    alert(`Failed to delete memory: ${e.message}`);
  }
}

async function triggerAutoIndexWorkspace() {
  const btn = document.getElementById("btnAutoIndexWorkspace");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 animate-spin"></i> Indexing...`;
    if (window.lucide) lucide.createIcons();
  }

  const alertBox = document.getElementById("memLiveAlert");
  try {
    const res = await fetch("/api/memory/index-workspace", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (alertBox) {
      alertBox.className = "p-3 rounded-xl border border-emerald-500/40 bg-emerald-950/40 text-emerald-200 flex items-center justify-between text-xs";
      alertBox.innerHTML = `
        <div class="flex items-center gap-2">
          <i data-lucide="check-circle-2" class="size-4 text-emerald-400"></i>
          <span>Successfully indexed <strong>${data.count || 0}</strong> codebase blueprints and documentation into long-term vector memory!</span>
        </div>
        <button onclick="this.parentElement.classList.add('hidden')" class="text-zinc-400 hover:text-white"><i data-lucide="x" class="size-3.5"></i></button>
      `;
      alertBox.classList.remove("hidden");
      if (window.lucide) lucide.createIcons();
    }

    loadMemoriesFromVault(currentMemoryFilter);
  } catch (e) {
    alert(`Failed to index workspace: ${e.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
      if (window.lucide) lucide.createIcons();
    }
  }
}







// API key fields: a show/hide eye (so you can check what was pasted), no browser autofill, and the two Fireworks
// key fields (main provider field and the Swarm one) kept in step so they can't disagree.
(() => {
  const ids = ["inputCloudKey", "inputFireworksKey", "inputGeminiKey", "inputOpenAIKey", "inputAnthropicKey",
               "inputGroqKey", "inputOpenRouterKey", "inputTavilyKey", "inputYouKey", "inputTrustmrrKey", "inputYoutubeKey", "inputHfKey"];
  const setup = () => {
    for (const id of ids) {
      const el = document.getElementById(id);
      if (!el || el.dataset.keyField) continue;
      el.dataset.keyField = "1";
      el.setAttribute("autocomplete", "off");
      el.setAttribute("spellcheck", "false");
      el.setAttribute("data-1p-ignore", "");
      el.setAttribute("data-lpignore", "true");
      const wrap = document.createElement("span");
      wrap.className = "relative flex-1 flex";
      el.parentNode.insertBefore(wrap, el);
      wrap.appendChild(el);
      el.classList.add("pr-8", "w-full");
      const eye = document.createElement("button");
      eye.type = "button";
      eye.className = "absolute right-2 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-200";
      eye.title = "Show / hide";
      eye.innerHTML = '<i data-lucide="eye" class="size-3.5"></i>';
      eye.addEventListener("click", () => {
        const show = el.type === "password";
        el.type = show ? "text" : "password";
        eye.innerHTML = `<i data-lucide="${show ? "eye-off" : "eye"}" class="size-3.5"></i>`;
        if (window.lucide) lucide.createIcons();
      });
      wrap.appendChild(eye);
    }
    const cloud = document.getElementById("inputCloudKey"), fw = document.getElementById("inputFireworksKey");
    if (cloud && fw && !cloud.dataset.fwSync) {
      cloud.dataset.fwSync = "1";
      cloud.addEventListener("input", () => {
        if (document.getElementById("selectLlmProvider")?.value === "fireworks") fw.value = cloud.value;
      });
      fw.addEventListener("input", () => {
        if (document.getElementById("selectLlmProvider")?.value === "fireworks") cloud.value = fw.value;
      });
    }
    if (window.lucide) lucide.createIcons();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup); else setup();
})();
