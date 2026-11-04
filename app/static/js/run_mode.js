// How Xod runs: the headless / lightweight switch (services/run_mode.py). The same switch as the
// desktop tray and `xod mode` in the terminal: all three read and write desktop.json.
(() => {
  const el = () => document.getElementById("runModeSection");
  async function load() {
    const box = el();
    if (!box) return;
    let m;
    try { m = await (await fetch("/api/mode")).json(); } catch (e) { return; }
    const row = (key, title, sub, on) => `
      <label class="flex items-start gap-3 cursor-pointer">
        <input type="checkbox" data-mode="${key}" ${on ? "checked" : ""} class="mt-0.5 accent-brand-500">
        <span><span class="text-[13px] text-zinc-200">${title}</span><br><span class="text-[11px] text-zinc-500">${sub}</span></span>
      </label>`;
    box.innerHTML = `
      <div class="space-y-3 rounded-xl bg-surface-750 p-4 border border-surface-700">
        <h4 class="text-xs font-semibold uppercase tracking-wider text-zinc-300">How Xod runs</h4>
        ${row("headless", "Headless", "Start with no window next time. Use the terminal (<code>xod</code>), the tray, or Open Xod.", m.headless)}
        ${row("lightweight", "Lightweight", "A lean server: optional background work off, an idle browser closes after 3 minutes. Tasks, routines and approvals keep working.", m.lightweight)}
        ${m.pending_restart ? `<p class="text-[11px] text-amber-300">Lightweight applies when Xod restarts.</p>` : ""}
        ${m.overridden_by_env && m.overridden_by_env.length ? `<p class="text-[11px] text-amber-300">Overridden for this run by ${m.overridden_by_env.join(", ")}.</p>` : ""}
        <p class="text-[11px] text-zinc-500">Same switch in the terminal: <code>xod mode headless on|off</code>, <code>xod mode light on|off</code>.</p>
      </div>`;
    box.querySelectorAll("input[data-mode]").forEach((cb) => cb.addEventListener("change", async () => {
      await fetch("/api/mode", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [cb.dataset.mode]: cb.checked }) });
      load();
    }));
  }
  // Refresh whenever Settings opens (wrapped once every script has loaded, whatever their order).
  function hook() {
    const open = window.openSettingsModal;
    if (typeof open === "function" && !open.__runMode) {
      window.openSettingsModal = function (...args) { const r = open.apply(this, args); load(); return r; };
      window.openSettingsModal.__runMode = true;
    }
    load();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", hook);
  else hook();
  window.loadRunMode = load;
})();
