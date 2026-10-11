// "A new version is out" on the home screen (services/updates.py). Dismissed per version.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  async function check() {
    let u;
    try { u = await (await fetch("/api/update")).json(); } catch (e) { return; }
    if (!u || !u.available) return;
    try { if (localStorage.getItem("xod.updateDismissed") === u.latest) return; } catch (e) {}
    const anchor = document.getElementById("missingKeyBanner");
    if (!anchor || document.getElementById("updateBanner")) return;
    const el = document.createElement("div");
    el.id = "updateBanner";
    el.className = "max-w-md mx-auto px-4 py-3 rounded-xl bg-brand-500/10 border border-brand-500/30 text-left flex items-center gap-3";
    el.innerHTML = `
      <i data-lucide="sparkles" class="size-4 text-brand-300 shrink-0"></i>
      <p class="text-[13px] text-zinc-200 flex-1">Xod ${esc(u.latest)} is out. <a href="${esc(u.url)}" target="_blank" rel="noopener" class="text-brand-300 hover:underline">See what's new and download</a></p>
      <button data-upd="later" class="text-[12px] text-zinc-400 hover:text-white" title="Hide until the next version">Later</button>
      <button data-upd="off" class="text-[12px] text-zinc-500 hover:text-white" title="Xod asks GitHub once a day for the latest version; nothing else is sent">Stop checking</button>`;
    anchor.parentNode.insertBefore(el, anchor);
    if (window.lucide) lucide.createIcons();
    el.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-upd]");
      if (!b) return;
      try { localStorage.setItem("xod.updateDismissed", u.latest); } catch (err) {}
      if (b.dataset.upd === "off") {
        await fetch("/api/update/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: false }) });
        if (window.showToast) showToast("Xod won't check for new versions. Turn it back on in Tools → What's coming next.");
      }
      el.remove();
    });
  }
  setTimeout(check, 2500);
})();
