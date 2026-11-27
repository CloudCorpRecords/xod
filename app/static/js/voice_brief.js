/**
 * Xod Voice Brief UI (Agora ConvoAI, local-first BYOK).
 *
 * - Lazy-loads vendored AgoraRTC SDK (/static/vendor/agora-rtc-sdk-ng.min.js)
 * - Starts/stops a live `xodbrief` ConvoAI voice session via /api/voice-brief/*
 * - Renders captured 5-section briefs and lets the user confirm & launch as a Xod mission
 * - Manages Agora credentials in Settings -> Connections (Keychain-backed via /api/config)
 */
(function () {
  const SECTION_ORDER = [
    "Mission",
    "Done looks like",
    "Guardrails",
    "My calls versus yours",
    "The window",
  ];

  let rtcClient = null;
  let localAudioTrack = null;
  let activeAgentId = null;
  let isLive = false;
  let isMuted = false;
  let timerHandle = null;
  let startedAtMs = 0;
  let sdkPromise = null;

  function loadAgoraSdk() {
    if (window.AgoraRTC) return Promise.resolve(window.AgoraRTC);
    if (sdkPromise) return sdkPromise;
    sdkPromise = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "/static/vendor/agora-rtc-sdk-ng.min.js";
      s.async = true;
      s.onload = () => {
        if (window.AgoraRTC) resolve(window.AgoraRTC);
        else reject(new Error("AgoraRTC SDK loaded but window.AgoraRTC is missing"));
      };
      s.onerror = () => reject(new Error("Could not load AgoraRTC SDK"));
      document.head.appendChild(s);
    });
    return sdkPromise;
  }

  function esc(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setError(msg) {
    const el = document.getElementById("vbError");
    if (!el) return;
    if (msg) {
      el.textContent = msg;
      el.classList.remove("hidden");
    } else {
      el.textContent = "";
      el.classList.add("hidden");
    }
  }

  function formatDuration(ms) {
    const sec = Math.max(0, Math.floor(ms / 1000));
    const m = String(Math.floor(sec / 60)).padStart(2, "0");
    const s = String(sec % 60).padStart(2, "0");
    return `${m}:${s}`;
  }

  function updateUiState(state, label) {
    const dot = document.getElementById("vbStatusDot");
    const text = document.getElementById("vbStatusText");
    const navDot = document.getElementById("voiceBriefNavDot");
    const startBtn = document.getElementById("vbStartBtn");
    const stopBtn = document.getElementById("vbStopBtn");
    const muteBtn = document.getElementById("vbMuteBtn");
    const timer = document.getElementById("vbTimer");

    if (text && label) text.textContent = label;
    if (dot) {
      dot.className = "size-2.5 rounded-full " + (
        state === "live" ? "bg-emerald-400 animate-pulse" :
        state === "connecting" || state === "stopping" ? "bg-brand-400 animate-pulse" :
        "bg-zinc-500"
      );
    }
    if (navDot) navDot.classList.toggle("hidden", state !== "live");
    if (startBtn) {
      startBtn.classList.toggle("hidden", state === "live" || state === "stopping");
      startBtn.disabled = state === "connecting";
    }
    if (stopBtn) {
      stopBtn.classList.toggle("hidden", state !== "live" && state !== "stopping");
      stopBtn.disabled = state === "stopping";
    }
    if (muteBtn) muteBtn.classList.toggle("hidden", state !== "live");
    if (timer) timer.classList.toggle("hidden", state !== "live");
  }

  function startTimer() {
    stopTimer();
    startedAtMs = Date.now();
    const el = document.getElementById("vbTimer");
    if (el) el.textContent = "00:00";
    timerHandle = setInterval(() => {
      if (el) el.textContent = formatDuration(Date.now() - startedAtMs);
    }, 1000);
  }

  function stopTimer() {
    if (timerHandle) {
      clearInterval(timerHandle);
      timerHandle = null;
    }
  }

  async function cleanupRtc() {
    window.__xodAgoraActive = false;
    stopTimer();
    if (localAudioTrack) {
      try { localAudioTrack.stop(); localAudioTrack.close(); } catch (_) {}
      localAudioTrack = null;
    }
    if (rtcClient) {
      try { await rtcClient.leave(); } catch (_) {}
      rtcClient = null;
    }
    isLive = false;
    isMuted = false;
    const lbl = document.getElementById("vbMuteLabel");
    if (lbl) lbl.textContent = "Mute";
  }

  function renderBriefs(briefs) {
    const list = document.getElementById("vbBriefsList");
    if (!list) return;
    if (!Array.isArray(briefs) || briefs.length === 0) {
      list.innerHTML = `<div class="rounded-xl border border-white/[0.06] bg-surface-900/50 p-4 text-[12px] text-zinc-500 text-center">No captured briefs yet. Start a Voice Brief, let Xod read back the 5 sections, then click Finish &amp; Capture.</div>`;
      return;
    }
    list.innerHTML = briefs.map((b) => {
      const secs = b.sections || {};
      const delivery = b.delivery || {};
      const launched = b.status === "launched" && b.mission_id;
      const rows = SECTION_ORDER.map((label) => {
        const val = secs[label] || "not specified";
        const dim = val === "not specified" ? "text-zinc-500 italic" : "text-zinc-200";
        return `<div class="grid grid-cols-[150px_1fr] gap-2 text-[12px] py-1 border-b border-white/[0.04] last:border-0">
          <span class="text-zinc-400 font-medium">${esc(label)}</span>
          <span class="${dim}">${esc(val)}</span>
        </div>`;
      }).join("");
      const badges = [
        delivery.local_path ? `<span class="px-2 py-0.5 rounded-full bg-surface-700 text-[10px] text-zinc-300">Saved locally</span>` : "",
        delivery.tree_inbox_delivered ? `<span class="px-2 py-0.5 rounded-full bg-emerald-500/15 text-[10px] text-emerald-300">Delivered to Tree</span>` : "",
        launched ? `<span class="px-2 py-0.5 rounded-full bg-brand-500/20 text-[10px] text-brand-300">Mission running</span>` : "",
      ].filter(Boolean).join("");
      const actions = launched
        ? `<button type="button" onclick="openWorkForBrief()" class="px-3 py-1.5 rounded-lg bg-surface-700 hover:bg-surface-600 text-zinc-100 text-[12px] font-medium">View in Work</button>`
        : `<button type="button" onclick="launchVoiceBrief('${esc(b.id)}')" class="px-3 py-1.5 rounded-lg bg-brand-500 hover:bg-brand-400 text-surface-900 text-[12px] font-semibold">Confirm &amp; Run Mission</button>
           <button type="button" onclick="runBriefInChat('${esc(b.id)}')" class="px-3 py-1.5 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/30 text-emerald-200 text-[12px] font-medium">Run in Chat (All Tools)</button>
           <button type="button" onclick="dismissVoiceBrief('${esc(b.id)}')" class="px-2.5 py-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-surface-700 text-[12px]">Dismiss</button>`;
      return `<div class="rounded-xl bg-surface-900/90 border border-white/[0.08] p-4 space-y-2.5">
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <div class="text-[13px] font-semibold text-white truncate">${esc(b.mission_title || "Voice brief")}</div>
            <div class="text-[11px] text-zinc-500">${esc(b.ts || "")}</div>
          </div>
          <div class="flex items-center gap-1.5 shrink-0">${badges}</div>
        </div>
        <div class="rounded-lg bg-surface-950/60 px-3 py-1.5">${rows}</div>
        <div class="flex items-center gap-2 pt-1">${actions}</div>
      </div>`;
    }).join("");
  }

  async function refreshVoiceBriefs() {
    try {
      const res = await fetch("/api/voice-brief/status");
      if (!res.ok) return;
      const data = await res.json();
      const notCfg = document.getElementById("vbNotConfigured");
      const startBtn = document.getElementById("vbStartBtn");
      if (notCfg) notCfg.classList.toggle("hidden", !!data.configured);
      if (startBtn && !isLive) startBtn.disabled = !data.configured;
      renderBriefs(data.briefs || []);
    } catch (_) {}
  }

  async function openVoiceBriefModal() {
    const modal = document.getElementById("voiceBriefModal");
    if (!modal) return;
    modal.classList.remove("hidden");
    setError("");
    if (window.lucide) lucide.createIcons();
    await refreshVoiceBriefs();
  }

  function closeVoiceBriefModal() {
    const modal = document.getElementById("voiceBriefModal");
    if (modal) modal.classList.add("hidden");
  }

  function openAgoraSettingsFromBrief() {
    closeVoiceBriefModal();
    if (typeof openSettingsModal === "function") {
      openSettingsModal();
      if (typeof showSettingsTab === "function") showSettingsTab("connections");
    }
  }

  async function startVoiceBrief() {
    if (isLive) return;
    // Prevent double-voices: stop local TTS / local conversation immediately when Agora starts
    window.__xodAgoraActive = true;
    try { if (window.XodSpeech) XodSpeech.stop(); } catch (_) {}
    try { if (window.XodConversation && XodConversation.active()) XodConversation.end("user"); } catch (_) {}
    setError("");
    updateUiState("connecting", "Connecting to Agora…");
    try {
      const AgoraRTC = await loadAgoraSdk();
      AgoraRTC.setLogLevel(3);

      const cfgRes = await fetch("/api/voice-brief/config");
      const cfgJson = await cfgRes.json();
      if (!cfgRes.ok) throw new Error(cfgJson.detail || "Could not mint Agora token");
      const cfg = cfgJson.data;

      rtcClient = AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });
      rtcClient.on("user-published", async (user, mediaType) => {
        try {
          await rtcClient.subscribe(user, mediaType);
          if (mediaType === "audio" && user.audioTrack) {
            user.audioTrack.play();
          }
        } catch (err) {
          setError("Remote audio error: " + (err.message || err));
        }
      });

      await Promise.race([
        rtcClient.join(cfg.app_id, cfg.channel_name, cfg.token, Number(cfg.uid)),
        new Promise((_, reject) => setTimeout(() => reject(new Error("Timed out connecting to Agora RTC (20s). Check network/firewall.")), 20000)),
      ]);
      localAudioTrack = await AgoraRTC.createMicrophoneAudioTrack();
      await rtcClient.publish([localAudioTrack]);

      const startRes = await fetch("/api/voice-brief/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          channel_name: cfg.channel_name,
          agent_uid: Number(cfg.agent_uid),
          user_uid: Number(cfg.uid),
          preset: "xodbrief",
        }),
      });
      const startJson = await startRes.json();
      if (!startRes.ok) throw new Error(startJson.detail || "Could not start voice agent");

      activeAgentId = startJson.data.agent_id;
      isLive = true;
      startTimer();
      updateUiState("live", "Live — speak your brief to Xod");
      if (window.lucide) lucide.createIcons();
    } catch (err) {
      await cleanupRtc();
      activeAgentId = null;
      updateUiState("idle", "Ready");
      setError(err.message || String(err));
    }
  }

  async function toggleVoiceBriefMute() {
    if (!localAudioTrack) return;
    isMuted = !isMuted;
    await localAudioTrack.setMuted(isMuted);
    const lbl = document.getElementById("vbMuteLabel");
    if (lbl) lbl.textContent = isMuted ? "Unmute" : "Mute";
  }

  async function stopVoiceBrief() {
    const aid = activeAgentId;
    activeAgentId = null;
    updateUiState("stopping", "Capturing brief & stopping…");
    setError("");
    try {
      if (aid) {
        const res = await fetch("/api/voice-brief/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ agent_id: aid }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          setError(data.detail || "Stop returned an error");
        } else if (data.data && data.data.captured) {
          if (typeof showToast === "function") showToast("Voice brief captured!");
        } else {
          setError("Session ended before Xod read back a 5-section brief, so nothing was captured.");
        }
      }
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      await cleanupRtc();
      updateUiState("idle", "Ready");
      await refreshVoiceBriefs();
    }
  }

  async function launchVoiceBrief(briefId) {
    setError("");
    try {
      const res = await fetch(`/api/voice-brief/briefs/${encodeURIComponent(briefId)}/launch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ continue_on_failure: true }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Could not launch mission");
      if (typeof showToast === "function") showToast("Mission started from Voice Brief!");
      await refreshVoiceBriefs();
      closeVoiceBriefModal();
      if (typeof openTasksPanel === "function") openTasksPanel();
    } catch (err) {
      setError(err.message || String(err));
    }
  }

  async function dismissVoiceBrief(briefId) {
    await fetch(`/api/voice-brief/briefs/${encodeURIComponent(briefId)}/dismiss`, { method: "POST" });
    await refreshVoiceBriefs();
  }

  function openWorkForBrief() {
    closeVoiceBriefModal();
    if (typeof openTasksPanel === "function") openTasksPanel();
  }

  async function runBriefInChat(briefId) {
    try {
      const res = await fetch("/api/voice-brief/briefs");
      const data = await res.json().catch(() => ({}));
      const b = (data.briefs || []).find((x) => x.id === briefId);
      if (!b) return;
      const secs = b.sections || {};
      const lines = SECTION_ORDER
        .map((k) => (secs[k] && secs[k] !== "not specified" ? `${k}: ${secs[k]}` : ""))
        .filter(Boolean);
      const prompt = lines.length ? `Execute this voice brief using any Xod tools needed:\n${lines.join("\n")}` : (b.mission_title || "");
      closeVoiceBriefModal();
      if (prompt && typeof window.quickSendPrompt === "function") window.quickSendPrompt(prompt);
    } catch (_) {}
  }
  window.runBriefInChat = runBriefInChat;

  // ---- Settings -> Connections: Agora BYOK credentials ----
  async function loadAgoraSettings() {
    try {
      const res = await fetch("/api/config");
      if (!res.ok) return;
      const cfg = await res.json();
      const status = document.getElementById("agoraCredStatus");
      if (status) {
        status.textContent = cfg.agora_configured ? "Configured (Keychain)" : "Not configured";
        status.className = "text-[11px] " + (cfg.agora_configured ? "text-emerald-400" : "text-zinc-400");
      }
      const appIdEl = document.getElementById("inputAgoraAppId");
      const certEl = document.getElementById("inputAgoraAppCert");
      const cidEl = document.getElementById("inputAgoraCustomerId");
      const csecEl = document.getElementById("inputAgoraCustomerSecret");
      const sshEl = document.getElementById("inputAgoraTreeSsh");
      const dirEl = document.getElementById("inputAgoraTreeDir");
      if (appIdEl) appIdEl.placeholder = cfg.agora_app_id_masked || "Agora App ID";
      if (certEl) certEl.placeholder = cfg.agora_app_certificate_configured ? "Configured (••••••••)" : "Agora App Certificate";
      if (cidEl) cidEl.placeholder = cfg.agora_customer_id_masked || "Optional Customer ID";
      if (csecEl) csecEl.placeholder = cfg.agora_customer_secret_configured ? "Configured (••••••••)" : "Optional Customer Secret";
      if (sshEl && !sshEl.value) sshEl.value = cfg.agora_tree_inbox_ssh || "";
      if (dirEl && !dirEl.value) dirEl.value = cfg.agora_tree_inbox_dir || "";
    } catch (_) {}
  }

  async function saveAgoraSettings() {
    const fb = document.getElementById("agoraSaveFeedback");
    const payload = {};
    const appId = document.getElementById("inputAgoraAppId")?.value.trim();
    const cert = document.getElementById("inputAgoraAppCert")?.value.trim();
    const cid = document.getElementById("inputAgoraCustomerId")?.value.trim();
    const csec = document.getElementById("inputAgoraCustomerSecret")?.value.trim();
    const ssh = document.getElementById("inputAgoraTreeSsh")?.value.trim();
    const dir = document.getElementById("inputAgoraTreeDir")?.value.trim();

    if (appId) payload.agora_app_id = appId;
    if (cert) payload.agora_app_certificate = cert;
    if (cid) payload.agora_customer_id = cid;
    if (csec) payload.agora_customer_secret = csec;
    if (ssh !== undefined) payload.agora_tree_inbox_ssh = ssh;
    if (dir !== undefined) payload.agora_tree_inbox_dir = dir;

    if (fb) { fb.textContent = "Saving…"; fb.className = "text-[12px] text-zinc-400"; }
    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Save failed");
      }
      ["inputAgoraAppId", "inputAgoraAppCert", "inputAgoraCustomerId", "inputAgoraCustomerSecret"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = "";
      });
      await loadAgoraSettings();
      if (fb) { fb.textContent = "Saved to Keychain."; fb.className = "text-[12px] text-emerald-400"; }
    } catch (err) {
      if (fb) { fb.textContent = err.message || "Error saving"; fb.className = "text-[12px] text-red-400"; }
    }
  }

  async function clearAgoraSettings() {
    const fb = document.getElementById("agoraSaveFeedback");
    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agora_app_id: "",
          agora_app_certificate: "",
          agora_customer_id: "",
          agora_customer_secret: "",
        }),
      });
      if (res.ok) {
        await loadAgoraSettings();
        if (fb) { fb.textContent = "Cleared."; fb.className = "text-[12px] text-zinc-400"; }
      }
    } catch (_) {}
  }

  // Hook into Settings modal load
  document.addEventListener("DOMContentLoaded", () => {
    setTimeout(loadAgoraSettings, 600);
  });

  window.openVoiceBriefModal = openVoiceBriefModal;
  window.closeVoiceBriefModal = closeVoiceBriefModal;
  window.openAgoraSettingsFromBrief = openAgoraSettingsFromBrief;
  window.startVoiceBrief = startVoiceBrief;
  window.stopVoiceBrief = stopVoiceBrief;
  window.toggleVoiceBriefMute = toggleVoiceBriefMute;
  window.refreshVoiceBriefs = refreshVoiceBriefs;
  window.launchVoiceBrief = launchVoiceBrief;
  window.dismissVoiceBrief = dismissVoiceBrief;
  window.openWorkForBrief = openWorkForBrief;
  window.loadAgoraSettings = loadAgoraSettings;
  window.saveAgoraSettings = saveAgoraSettings;
  window.clearAgoraSettings = clearAgoraSettings;
})();
