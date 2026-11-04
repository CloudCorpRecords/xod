// How this launch of the desktop shell runs, decided in one place so it can be tested:
//
//   full      the window opens and Xod is used as an app (the default, unchanged).
//   headless  no window at all: the server, the tray, the talk shortcut and the update
//             checks run, but nothing renders. Opening Xod again (a second launch, the
//             dock, or "Open Xod" in the tray) attaches the window to the same server;
//             nothing restarts, nothing is re-paired, no setting moves.
//
// Headless is a launch choice (--headless, or XOD_HEADLESS=1), never a state the app
// drifts into on its own.
function resolveMode(argv, env) {
  const flag = Array.isArray(argv) && argv.includes("--headless");
  const raw = String((env && env.XOD_HEADLESS) || "").trim().toLowerCase();
  return { headless: flag || ["1", "true", "yes", "on"].includes(raw) };
}

// Closing the window normally only hides it, and a hidden window keeps its renderer
// alive: the page is Xod's live dashboard and polls the server several times a second,
// which is the single biggest idle cost of the whole app. With the person's lightweight
// background choice on (and background running itself on), closing destroys the window
// instead, so the renderer goes away; "Open Xod" builds a fresh one against the same
// server. Everything real (tasks, chats, settings) lives in the server, not the page.
function shouldShedOnClose(prefs) {
  return !!(prefs && prefs.background && prefs.lightBackground);
}

module.exports = { resolveMode, shouldShedOnClose };
