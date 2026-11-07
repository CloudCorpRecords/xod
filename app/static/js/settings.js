// Settings pages: the modal used to be one long scroll (run mode, memory,
// four search keys, logins, the LLM picker, usage, media models and the
// swarm keys all stacked). It is five pages now, one visible at a time,
// picked from the rail on the left. Nothing moved underneath: same ids,
// same endpoints, same save/test flows - this only decides what shows.
(() => {
  const TABS = ["general", "models", "search", "connections", "advanced"];

  window.showSettingsTab = (name) => {
    if (!TABS.includes(name)) name = "general";
    document.querySelectorAll("#settingsModal [data-settings-page]").forEach((p) =>
      p.classList.toggle("is-active", p.dataset.settingsPage === name));
    document.querySelectorAll("#settingsModal [data-settings-tab]").forEach((b) =>
      b.classList.toggle("is-active", b.dataset.settingsTab === name));
    const pages = document.getElementById("settingsPages");
    if (pages) pages.scrollTop = 0;
    if (window.lucide) lucide.createIcons();
  };

  // Open the modal straight at a page (the drawer's key rows use this).
  window.openSettingsTab = (name) => {
    if (typeof window.openSettingsModal === "function") window.openSettingsModal();
    window.showSettingsTab(name);
  };

  // Deep links: #settings opens the modal, #settings-models a page.
  function fromHash() {
    const h = (location.hash || "").replace(/^#/, "");
    if (h === "settings") {
      if (typeof window.openSettingsModal === "function") window.openSettingsModal();
    } else if (h.startsWith("settings-")) {
      window.openSettingsTab(h.slice("settings-".length));
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fromHash);
  else fromHash();
})();
