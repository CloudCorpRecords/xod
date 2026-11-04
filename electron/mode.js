// How this launch of the desktop shell runs, decided in one place so it can be tested:
//
//   full      the window opens and Xod is used as an app (the default, unchanged).
//   headless  no window at all: the server, the tray, the talk shortcut and the update
//             checks run, but nothing renders. Opening Xod again (a second launch, the
//             dock, or "Open Xod" in the tray) attaches the window to the same server;
//             nothing restarts, nothing is re-paired, no setting moves.
//
// Headless is the person's choice, never a state the app drifts into on its own: the saved
// switch (prefs.headless in desktop.json, shared with Settings and `xod mode headless on|off`),
// overridden for one launch by --headless / --window or XOD_HEADLESS.
const TRUE = ["1", "true", "yes", "on"];
const FALSE = ["0", "false", "no", "off"];
function resolveMode(argv, env, prefs) {
  const args = Array.isArray(argv) ? argv : [];
  if (args.includes("--headless")) return { headless: true, why: "--headless" };
  if (args.includes("--window")) return { headless: false, why: "--window" };
  const raw = String((env && env.XOD_HEADLESS) || "").trim().toLowerCase();
  if (TRUE.includes(raw)) return { headless: true, why: "XOD_HEADLESS" };
  if (FALSE.includes(raw)) return { headless: false, why: "XOD_HEADLESS" };
  return { headless: !!(prefs && prefs.headless), why: "saved switch" };
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
