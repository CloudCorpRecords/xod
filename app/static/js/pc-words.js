// Xod's words were written on a Mac ("runs on your Mac", "your Mac's Keychain"). On Windows this rewrites them
// as the page is shown, including text added later (panels, toasts), so a PC user reads about their PC,
// Windows Credential Manager and File Explorer. Chat answers are left alone, and so are mentions of the Mac as
// a Mac ("Download for Mac", "Mac only for now"), which don't match these phrases.
(() => {
  if (!/Windows/i.test(navigator.userAgent)) return;
  const RULES = [
    [/your Mac's Keychain/g, "Windows Credential Manager"],
    [/in your Keychain/g, "in Windows Credential Manager"],
    [/the macOS Keychain/g, "Windows Credential Manager"],
    [/macOS Finder/g, "File Explorer"],
    [/\bin Finder\b/g, "in File Explorer"],
    [/your Mac's settings/g, "Windows settings"],
    [/\bthis Mac\b/g, "this PC"],
    [/\bthe Mac is\b/g, "the PC is"],
    [/\bYour Mac\b/g, "Your PC"],
    [/\byour Mac\b/g, "your PC"],
    [/macOS shows the orange mic dot/g, "Windows shows the mic icon"],
  ];
  const SKIP = "script, style, textarea, input, .prose-custom, [data-no-pc-words]";
  const fix = (s) => RULES.reduce((t, [re, to]) => t.replace(re, to), s);
  const touches = (s) => s && /Mac|Keychain|Finder|macOS/.test(s);

  function walk(root) {
    if (root.nodeType === 3) {
      if (touches(root.nodeValue) && !root.parentElement?.closest(SKIP)) {
        const v = fix(root.nodeValue);
        if (v !== root.nodeValue) root.nodeValue = v;
      }
      return;
    }
    if (root.nodeType !== 1 || root.closest?.(SKIP)) return;
    for (const a of ["title", "placeholder", "aria-label"]) {
      const v = root.getAttribute?.(a);
      if (touches(v)) root.setAttribute(a, fix(v));
    }
    const it = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = it.nextNode(); n; n = it.nextNode()) {
      if (n.nodeType === 1) {
        for (const a of ["title", "placeholder", "aria-label"]) {
          const v = n.getAttribute(a);
          if (touches(v)) n.setAttribute(a, fix(v));
        }
      } else if (touches(n.nodeValue) && !n.parentElement?.closest(SKIP)) {
        const v = fix(n.nodeValue);
        if (v !== n.nodeValue) n.nodeValue = v;
      }
    }
  }

  const start = () => {
    walk(document.body);
    new MutationObserver((muts) => {
      for (const m of muts) {
        if (m.type === "characterData") walk(m.target);
        else m.addedNodes.forEach(walk);
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
  };
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
})();
