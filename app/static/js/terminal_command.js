// The `xod` command for the terminal (backend: services/terminal_command.py): Tools → Terminal command.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function render(root, note) {
    let s;
    try { s = await (await fetch("/api/terminal-command")).json(); } catch (e) { s = null; }
    const body = root.querySelector("[data-tc-body]");
    if (!s) { body.innerHTML = `<p class="text-red-300">Couldn't check.</p>`; return; }
    body.innerHTML = `
      <p class="text-zinc-300">Type <code class="px-1 rounded bg-surface-700 text-white">xod</code> in any terminal to chat with Xod there:
        the same chats, models and keys as this app. <span class="text-zinc-500">xod "a question" · xod status · xod task "…"</span></p>
      <p class="mt-2 ${s.installed ? "text-emerald-300" : "text-zinc-400"}">${s.installed ? `Installed at <span class="font-mono">${esc(s.path)}</span>.` + (s.current ? "" : " (an older version: Install again to update it)") : "Not installed yet."}</p>
      ${s.other ? `<p class="mt-1 text-amber-300">Another xod at <span class="font-mono">${esc(s.other)}</span> comes first on your PATH.</p>` : ""}
      ${note ? `<p class="mt-2 text-zinc-200">${esc(note)}</p>` : ""}
      <div class="mt-3 flex gap-2">
        <button data-tc="install" class="px-3 py-1.5 rounded-lg bg-brand-500 text-surface-900 font-semibold hover:bg-brand-400">${s.installed ? "Install again" : "Install"}</button>
        ${s.installed ? `<button data-tc="remove" class="px-3 py-1.5 rounded-lg bg-surface-700 text-zinc-200 hover:bg-surface-600">Remove</button>` : ""}
      </div>`;
  }

  window.openTerminalCommand = () => {
    let root = document.getElementById("terminalCommandPanel");
    if (!root) {
      root = document.createElement("div");
      root.id = "terminalCommandPanel";
      root.className = "fixed inset-0 z-[85] flex items-center justify-center p-4 bg-black/60";
      root.innerHTML = `<div class="w-full max-w-md p-5 rounded-2xl bg-surface-850 border border-white/[0.08] shadow-2xl text-[13px]">
        <div class="flex items-center justify-between mb-2"><h3 class="text-white font-semibold text-[15px]">Terminal command</h3>
          <button data-tc="close" class="p-1 rounded-lg text-zinc-500 hover:text-white" title="Close">✕</button></div>
        <div data-tc-body></div></div>`;
      document.body.appendChild(root);
      root.addEventListener("click", async (e) => {
        const b = e.target.closest("[data-tc]");
        if (e.target === root || (b && b.dataset.tc === "close")) { root.classList.add("hidden"); return; }
        if (!b) return;
        const r = await (await fetch("/api/terminal-command", { method: b.dataset.tc === "install" ? "POST" : "DELETE" })).json();
        render(root, r.message || r.error);
      });
    }
    root.classList.remove("hidden");
    render(root);
  };
})();
