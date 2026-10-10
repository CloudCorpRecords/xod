// Everything the AI writes can contain text from web pages, files and emails, so rendered
// Markdown goes through DOMPurify before it reaches the page: no scripts, event handlers
// (onerror=…), iframes, forms or javascript: links. Loaded right after marked and DOMPurify,
// so every marked.parse() call in the app is covered.
(function () {
  if (!window.marked || !window.DOMPurify) {
    console.error("safe-html: marked or DOMPurify missing");
    return;
  }
  const OPTIONS = {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["style", "form", "input", "button", "textarea", "select", "iframe", "object", "embed", "base", "meta", "link"],
    FORBID_ATTR: ["style", "srcset", "formaction", "action"],
    ALLOW_DATA_ATTR: false,
    ADD_ATTR: ["data-reveal", "role", "tabindex", "controls", "playsinline", "preload", "loading"],
  };
  // Links from AI output open outside the app and never pass the opener along.
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A" && node.getAttribute("href")) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
  // A marked hook, not a wrapper around marked.parse: later marked.use()/setOptions() calls in
  // app.js replace marked.parse, but hooks stay in place.
  marked.use({ hooks: { postprocess: (html) => DOMPurify.sanitize(html, OPTIONS) } });
  window.safeHtml = (html) => DOMPurify.sanitize(html || "", OPTIONS);
})();
