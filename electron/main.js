// Xod desktop shell: starts the Python server on a free local port, shows a loading screen,
// then opens the app. The window can only show Xod itself; links to other sites open in the
// normal browser. The server is stopped when the app quits.
const { app, BrowserWindow, Menu, shell, session, dialog, systemPreferences, globalShortcut, ipcMain } = require("electron");
const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");
const updater = require("./updater");

// Electron's own cache goes in a subfolder; Xod's data (xod.db, projects, models…) is next to it.
app.setPath("userData", path.join(app.getPath("appData"), "Xod", "Electron"));
const DATA_DIR = process.env.XOD_DATA_DIR || path.join(app.getPath("appData"), "Xod");

// From source: the repository and its .venv. Installed (Xod.app): the Python code and a Python
// with every package already installed are both inside the app (electron/scripts/bundle-python.js),
// so nothing is installed on first launch.
const PACKAGED = app.isPackaged;
const ROOT = PACKAGED ? path.join(process.resourcesPath, "backend") : path.resolve(__dirname, "..");
const VENV_DIR = path.join(ROOT, ".venv");
const PY = PACKAGED ? path.join(process.resourcesPath, "python", "bin", "python3.12") : path.join(VENV_DIR, "bin", "python");
const PREFERRED_PORT = Number(process.env.XOD_PORT || 8000);

let server = null;          // the Python process we started (null if we attached to a running one)
let appUrl = "";
let splash = null;
let mainWindow = null;
let quitting = false;

if (!app.requestSingleInstanceLock()) app.quit();
app.on("second-instance", () => {
  if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.focus(); }
});

// ------------------------------------------------------------------ helpers

function status(text) {
  if (splash && !splash.isDestroyed()) {
    splash.webContents.executeJavaScript(`setStatus(${JSON.stringify(text)})`).catch(() => {});
  }
}

function get(url, timeoutMs = 1500) {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      let body = "";
      res.on("data", (c) => { if (body.length < 4096) body += c; });
      res.on("end", () => resolve({ status: res.statusCode, body }));
    });
    req.on("error", () => resolve(null));
    req.on("timeout", () => { req.destroy(); resolve(null); });
  });
}

function portFree(port) {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.once("error", () => resolve(false));
    s.once("listening", () => s.close(() => resolve(true)));
    s.listen(port, "127.0.0.1");
  });
}

async function pickPort() {
  if (await portFree(PREFERRED_PORT)) return PREFERRED_PORT;
  return new Promise((resolve) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => resolve(p)); });
  });
}

function findPython() {
  for (const cmd of ["python3.12", "python3.13", "python3.11", "python3.10", "python3"]) {
    const r = spawnSync(cmd, ["-c", "import sys; print(sys.version_info[:2] >= (3, 10))"], { encoding: "utf8" });
    if (r.status === 0 && r.stdout.trim() === "True") return cmd;
  }
  return null;
}

function run(cmd, args, label) {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, { cwd: ROOT, env: { ...process.env, PYTHONUNBUFFERED: "1" } });
    const onData = (d) => {
      const line = d.toString().trim().split("\n").pop();
      if (line) status(`${label}… ${line.slice(0, 90)}`);
    };
    p.stdout.on("data", onData);
    p.stderr.on("data", onData);
    p.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`${label} failed (exit ${code})`))));
  });
}

async function ensurePythonEnv() {
  if (PACKAGED) {
    if (!fs.existsSync(PY)) throw new Error("This copy of Xod is incomplete. Download it again from the Releases page.");
    // Earlier test builds installed packages into the data folder on first launch; that copy is no longer used.
    fs.rmSync(path.join(DATA_DIR, "python"), { recursive: true, force: true });
    return;
  }
  // From source: make .venv the first time (a few GB of packages).
  if (fs.existsSync(PY) && spawnSync(PY, ["-c", "import sys"]).status === 0) return;
  const py = findPython();
  if (!py) throw new Error("Xod needs Python 3.10 or newer (3.12 recommended). Install it from python.org, then open Xod again.");
  status("First run: creating Xod's Python environment…");
  fs.rmSync(VENV_DIR, { recursive: true, force: true });
  await run(py, ["-m", "venv", VENV_DIR], "Creating environment");
  status("Installing Xod's packages (a few GB, one time)…");
  await run(PY, ["-m", "pip", "install", "-r", path.join(ROOT, "requirements.txt")], "Installing packages");
}

function serverEnv() {
  const env = { ...process.env, PYTHONUNBUFFERED: "1", XOD_DATA_DIR: DATA_DIR };
  if (PACKAGED) Object.assign(env, { XOD_PACKAGED: "1", PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1" });
  return env;
}

async function startServer() {
  // A Xod server already running on the usual port (e.g. ./run.sh): use it instead of starting another.
  const existing = await get(`http://127.0.0.1:${PREFERRED_PORT}/`);
  if (existing && existing.status === 200 && /<title>\s*Xod/i.test(existing.body)) {
    return `http://127.0.0.1:${PREFERRED_PORT}`;
  }
  await ensurePythonEnv();
  const port = await pickPort();
  status("Starting Xod…");
  server = spawn(PY, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port)], {
    cwd: ROOT,
    env: serverEnv(),
  });
  let log = "";
  const keep = (d) => { log = (log + d.toString()).slice(-4000); };
  server.stdout.on("data", keep);
  server.stderr.on("data", keep);
  server.on("exit", (code) => {
    server = null;
    if (!quitting) {
      dialog.showErrorBox("Xod stopped", `The Xod server exited (code ${code}).\n\n${log.slice(-1500)}`);
      app.quit();
    }
  });
  const url = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 240; i++) {               // up to 2 minutes (first start loads a lot)
    const r = await get(`${url}/`);
    if (r && r.status === 200) return url;
    if (!server) throw new Error(`The server didn't start.\n\n${log.slice(-1500)}`);
    await new Promise((r2) => setTimeout(r2, 500));
  }
  throw new Error(`The server didn't answer in time.\n\n${log.slice(-1500)}`);
}

function stopServer() {
  if (!server) return;
  const p = server;
  p.kill("SIGTERM");
  setTimeout(() => { try { p.kill("SIGKILL"); } catch (e) {} }, 5000).unref();
}

// ------------------------------------------------------------------ security

function isAppUrl(u) {
  try { return appUrl && new URL(u).origin === new URL(appUrl).origin; } catch (e) { return false; }
}
// Apps Xod builds are served from Xod's address too, under /preview/. They're untrusted pages:
// no permissions, no preload, never treated as Xod itself.
function isPreviewUrl(u) {
  try { return isAppUrl(u) && new URL(u).pathname.startsWith("/preview/"); } catch (e) { return false; }
}
const isXodPage = (u) => isAppUrl(u) && !isPreviewUrl(u);

function lockDown() {
  // Only the microphone (voice input), clipboard writes and notifications, and only for Xod itself.
  const allowed = new Set(["media", "clipboard-sanitized-write", "notifications", "fullscreen"]);
  session.defaultSession.setPermissionRequestHandler(async (wc, permission, cb) => {
    if (!allowed.has(permission) || !isXodPage(wc.getURL())) return cb(false);
    // Voice input: macOS asks the user once, then remembers (System Settings → Privacy → Microphone).
    if (permission === "media" && process.platform === "darwin") {
      try { return cb(await systemPreferences.askForMediaAccess("microphone")); } catch (e) { return cb(false); }
    }
    cb(true);
  });
  session.defaultSession.setPermissionCheckHandler((wc, permission) => allowed.has(permission) && !!wc && isXodPage(wc.getURL()));

  app.on("web-contents-created", (_e, contents) => {
    contents.setWindowOpenHandler(({ url }) => {
      // Previews of apps Xod built are untrusted pages: they open in the user's browser, not in an app window.
      const preview = isAppUrl(url) && new URL(url).pathname.startsWith("/preview/");
      if (/^https?:\/\//.test(url) && (!isAppUrl(url) || preview)) shell.openExternal(url);
      return { action: isAppUrl(url) && !preview ? "allow" : "deny" };
    });
    contents.on("will-navigate", (e, url) => {
      if (url.startsWith("file://") && contents === (splash && splash.webContents)) return;
      if (!isAppUrl(url)) {
        e.preventDefault();
        if (/^https?:\/\//.test(url)) shell.openExternal(url);
      }
    });
    contents.on("will-attach-webview", (e) => e.preventDefault());
  });
}

const SECURE_PREFS = { contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true };

// ------------------------------------------------------------------ windows

function showSplash() {
  splash = new BrowserWindow({
    width: 420, height: 300, frame: false, resizable: false, show: false,
    backgroundColor: "#0f1222", webPreferences: SECURE_PREFS,
  });
  splash.loadFile(path.join(__dirname, "splash.html"));
  splash.once("ready-to-show", () => splash.show());
}

function showMain() {
  mainWindow = new BrowserWindow({
    width: 1440, height: 920, minWidth: 900, minHeight: 600, show: false,
    title: "Xod", backgroundColor: "#0f1222",
    webPreferences: { ...SECURE_PREFS, preload: path.join(__dirname, "preload.js") },
  });
  mainWindow.loadURL(appUrl);
  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    if (splash && !splash.isDestroyed()) splash.close();
  });
  mainWindow.on("closed", () => { mainWindow = null; });
}

// ------------------------------------------------------------------ talking to Xod

// While Xod is listening (a conversation, or waiting for "Hey Xod"), keep the page running at full
// speed in the background: macOS would otherwise slow its timers once the window isn't in front.
ipcMain.on("xod:listening", (e, on) => {
  if (!mainWindow || e.sender !== mainWindow.webContents) return;
  e.sender.setBackgroundThrottling(!on);
});

// "Launch it": an app Xod built opens in its own window, sandboxed, with no bridge to Xod.
const appWindows = new Map();
ipcMain.on("xod:open-app", (e, url, title) => {
  if (!mainWindow || e.sender !== mainWindow.webContents || typeof url !== "string") return;
  const full = new URL(url, appUrl).toString();
  if (!isPreviewUrl(full)) return;
  const key = new URL(full).pathname.split("/").slice(0, 4).join("/");      // one window per app
  const existing = appWindows.get(key);
  if (existing && !existing.isDestroyed()) { existing.loadURL(full); existing.focus(); return; }
  const w = new BrowserWindow({
    width: 1100, height: 760, minWidth: 360, minHeight: 300, title: String(title || "App").slice(0, 80),
    backgroundColor: "#000000", webPreferences: { ...SECURE_PREFS },
  });
  w.on("page-title-updated", (ev) => ev.preventDefault());
  w.loadURL(full);
  appWindows.set(key, w);
  w.on("closed", () => appWindows.delete(key));
});

// ⌃⌥Space from any app starts (or ends) a conversation. Xod answers out loud, so the window
// doesn't need to come forward; it's only shown (without taking focus) if it was minimized.
const TALK_SHORTCUT = "Control+Alt+Space";
function registerTalkShortcut() {
  try {
    globalShortcut.register(TALK_SHORTCUT, () => {
      if (!mainWindow) return;
      if (mainWindow.isMinimized()) mainWindow.showInactive();
      mainWindow.webContents.send("xod:talk");
    });
  } catch (e) { /* taken by another app: the in-app button and "Hey Xod" still work */ }
}

// Opened straight from the download (the disk image or Downloads)? Offer to install it properly.
function offerMoveToApplications() {
  if (!PACKAGED || process.platform !== "darwin" || app.isInApplicationsFolder()) return false;
  const choice = dialog.showMessageBoxSync({
    type: "question",
    buttons: ["Move to Applications", "Not now"],
    defaultId: 0, cancelId: 1,
    message: "Install Xod in your Applications folder?",
    detail: "Then you can open it from Launchpad, Spotlight or the Dock like any other app.",
  });
  if (choice !== 0) return false;
  try {
    return app.moveToApplicationsFolder({
      conflictHandler: (type) => type === "exists"
        ? dialog.showMessageBoxSync({ type: "question", buttons: ["Replace", "Cancel"], defaultId: 0, cancelId: 1,
            message: "An older Xod is already in Applications. Replace it?" }) === 0
        : true,
    });
  } catch (e) {
    dialog.showErrorBox("Couldn't move Xod", String(e.message || e));
    return false;
  }
}

// The standard Mac menus, plus "Check for Updates…" (electron/updater.js).
function setMenu(checkForUpdates) {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: app.name, submenu: [
      { role: "about" },
      { label: "Check for Updates…", click: checkForUpdates, enabled: app.isPackaged },
      { type: "separator" }, { role: "services" }, { type: "separator" },
      { role: "hide" }, { role: "hideOthers" }, { role: "unhide" }, { type: "separator" }, { role: "quit" },
    ] },
    { role: "editMenu" },
    { role: "viewMenu" },
    { role: "windowMenu" },
  ]));
}

app.whenReady().then(async () => {
  if (offerMoveToApplications()) return;    // macOS relaunches it from Applications
  lockDown();
  showSplash();
  try {
    appUrl = await startServer();
    showMain();
    registerTalkShortcut();
    const updates = updater.start(() => mainWindow, () => appUrl);
    setMenu(() => updates.menuCheck());
    // The page's version pill (app/static/js/updates.js): status, "Check now", "Update". Only Xod's own page.
    const fromXod = (ev) => isXodPage(ev.senderFrame ? ev.senderFrame.url : ev.sender.getURL());
    ipcMain.handle("xod:update-status", (ev) => (fromXod(ev) ? updates.status() : null));
    ipcMain.handle("xod:update-check", (ev) => (fromXod(ev) ? updates.check() : null));
    ipcMain.handle("xod:update-install", (ev) => (fromXod(ev) ? updates.install() : null));
    updates.onState((st) => { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send("xod:update-state", st); });
  } catch (err) {
    dialog.showErrorBox("Xod couldn't start", String(err.message || err));
    app.quit();
  }
});

app.on("activate", () => { if (!mainWindow && appUrl) showMain(); });
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => { quitting = true; stopServer(); });
app.on("will-quit", () => globalShortcut.unregisterAll());
