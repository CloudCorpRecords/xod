// Xod desktop shell: starts the Python server on a free local port, shows a loading screen,
// then opens the app. The window can only show Xod itself; links to other sites open in the
// normal browser. The server is stopped when the app quits.
const { app, BrowserWindow, shell, session, dialog, systemPreferences } = require("electron");
const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");

// Electron's own cache goes in a subfolder; Xod's data (xod.db, projects, models…) is next to it.
app.setPath("userData", path.join(app.getPath("appData"), "Xod", "Electron"));
const DATA_DIR = process.env.XOD_DATA_DIR || path.join(app.getPath("appData"), "Xod");

// From source: the repository and its .venv. Installed (Xod.app): the Python code is bundled
// read-only in Resources/backend, and the Python environment is made once in the data folder.
const PACKAGED = app.isPackaged;
const ROOT = PACKAGED ? path.join(process.resourcesPath, "backend") : path.resolve(__dirname, "..");
const VENV_DIR = PACKAGED ? path.join(DATA_DIR, "python") : path.join(ROOT, ".venv");
const VENV_PY = path.join(VENV_DIR, "bin", "python");
const STAMP = path.join(VENV_DIR, ".xod-requirements");
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

// First run: make the Python environment Xod needs (a few GB of packages, one time).
// Installs again when requirements.txt changes (e.g. after an update of Xod.app).
async function ensurePythonEnv() {
  const reqs = fs.readFileSync(path.join(ROOT, "requirements.txt"), "utf8");
  const ready = fs.existsSync(VENV_PY) && (!PACKAGED || (fs.existsSync(STAMP) && fs.readFileSync(STAMP, "utf8") === reqs));
  if (ready) return;
  if (!fs.existsSync(VENV_PY)) {
    const py = findPython();
    if (!py) throw new Error("Xod needs Python 3.10 or newer (3.12 recommended). Install it from python.org, then open Xod again.");
    status("First run: creating Xod's Python environment…");
    fs.mkdirSync(path.dirname(VENV_DIR), { recursive: true });
    await run(py, ["-m", "venv", VENV_DIR], "Creating environment");
  }
  status("Installing Xod's packages (a few GB, one time)…");
  await run(VENV_PY, ["-m", "pip", "install", "--upgrade", "pip"], "Updating pip");
  await run(VENV_PY, ["-m", "pip", "install", "-r", path.join(ROOT, "requirements.txt")], "Installing packages");
  if (PACKAGED) fs.writeFileSync(STAMP, reqs);
}

function serverEnv() {
  const env = { ...process.env, PYTHONUNBUFFERED: "1", XOD_DATA_DIR: DATA_DIR };
  if (PACKAGED) Object.assign(env, { XOD_PACKAGED: "1", PYTHONDONTWRITEBYTECODE: "1" });
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
  server = spawn(VENV_PY, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port)], {
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

function lockDown() {
  // Only the microphone (voice input), clipboard writes and notifications, and only for Xod itself.
  const allowed = new Set(["media", "clipboard-sanitized-write", "notifications", "fullscreen"]);
  session.defaultSession.setPermissionRequestHandler(async (wc, permission, cb) => {
    if (!allowed.has(permission) || !isAppUrl(wc.getURL())) return cb(false);
    // Voice input: macOS asks the user once, then remembers (System Settings → Privacy → Microphone).
    if (permission === "media" && process.platform === "darwin") {
      try { return cb(await systemPreferences.askForMediaAccess("microphone")); } catch (e) { return cb(false); }
    }
    cb(true);
  });
  session.defaultSession.setPermissionCheckHandler((wc, permission) => allowed.has(permission) && !!wc && isAppUrl(wc.getURL()));

  app.on("web-contents-created", (_e, contents) => {
    contents.setWindowOpenHandler(({ url }) => {
      if (/^https?:\/\//.test(url) && !isAppUrl(url)) shell.openExternal(url);
      return { action: isAppUrl(url) ? "allow" : "deny" };
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
    title: "Xod", backgroundColor: "#0f1222", webPreferences: SECURE_PREFS,
  });
  mainWindow.loadURL(appUrl);
  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    if (splash && !splash.isDestroyed()) splash.close();
  });
  mainWindow.on("closed", () => { mainWindow = null; });
}

app.whenReady().then(async () => {
  lockDown();
  showSplash();
  try {
    appUrl = await startServer();
    showMain();
  } catch (err) {
    dialog.showErrorBox("Xod couldn't start", String(err.message || err));
    app.quit();
  }
});

app.on("activate", () => { if (!mainWindow && appUrl) showMain(); });
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => { quitting = true; stopServer(); });
