// "Share report": turns an answer into a polished page + PDF with sources (services/reports.py).
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function questionBefore(wrapper) {
    for (let el = wrapper?.previousElementSibling; el; el = el.previousElementSibling) {
      const u = el.querySelector?.(".user-bubble");
      if (u) return u.innerText.trim();
    }
    return "";
  }

  function chatTitle() {
    const all = [...(typeof unfiledChats !== "undefined" ? unfiledChats : []), ...((typeof localProjects !== "undefined" ? localProjects : []).flatMap((p) => p.sessions || []))];
    return (all.find((c) => c.id === currentSessionId) || {}).title || "";
  }

  async function share(btn) {
    const wrapper = btn.closest(".message-bubble-animate");
    const prose = wrapper?.querySelector(".prose-custom");
    if (!prose || !prose.innerText.trim()) return showToast("Nothing to share yet.");
    const question = questionBefore(wrapper);
    btn.disabled = true;
    const label = btn.innerHTML;
    btn.innerHTML = `<i data-lucide="loader-2" class="size-3.5 animate-spin"></i> Making report…`;
    if (window.lucide) lucide.createIcons();
    try {
      const r = await (await fetch("/api/reports", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: chatTitle() || question.slice(0, 100), question, html: safeHtml(prose.innerHTML) }) })).json();
      if (!r.success) throw new Error(r.error || "failed");
      showResult(r);
    } catch (e) {
      showToast("Couldn't make the report.");
    } finally {
      btn.disabled = false;
      btn.innerHTML = label;
      if (window.lucide) lucide.createIcons();
    }
  }

  function showResult(r) {
    const m = document.createElement("div");
    m.className = "fixed inset-0 z-[95] flex items-center justify-center p-4 bg-black/60";
    m.innerHTML = `
      <div class="w-full max-w-md rounded-3xl bg-surface-850 border border-white/[0.08] shadow-2xl p-6 space-y-4">
        <h3 class="text-white text-lg font-semibold">Report ready</h3>
        <p class="text-[13px] text-zinc-300">“${esc(r.title)}” with ${r.sources} source${r.sources === 1 ? "" : "s"}. It's a single file you can email, AirDrop or post; nothing in it runs code.</p>
        <div class="flex flex-wrap gap-2">
          <a href="${esc(r.html_url)}" target="_blank" class="px-3 py-2 rounded-lg bg-brand-500 text-surface-900 text-[13px] font-semibold">Open page</a>
          ${r.pdf_url ? `<a href="${esc(r.pdf_url)}" target="_blank" class="px-3 py-2 rounded-lg bg-surface-700 text-white text-[13px]">Open PDF</a>` : ""}
          <button data-reveal="${esc(r.pdf_path || r.html_path)}" class="px-3 py-2 rounded-lg bg-surface-700 text-white text-[13px]">Show in Finder</button>
          <button data-close class="px-3 py-2 rounded-lg text-[13px] text-zinc-400 hover:text-white ml-auto">Close</button>
        </div>
      </div>`;
    m.addEventListener("click", (e) => { if (e.target === m || e.target.closest("[data-close]")) m.remove(); });
    document.body.appendChild(m);
  }

  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-share-report]");
    if (b) share(b);
  });
})();
