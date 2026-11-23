// The screen capture bridge: the GUI main process is the only part of Xod that
// macOS credits with the app's Screen Recording grant, so it is the only part
// that may capture the screen. The Python backend used to capture on its own
// (PIL ImageGrab, /usr/sbin/screencapture), and macOS attributed those captures
// to the backend's own identity, which holds no grant: the app read "denied"
// while its own Settings switch stood on (AGE-98, the capture half of AGE-57).
//
// This module serves captures from the main process over a loopback-only HTTP
// endpoint and leaves the coordinates in capture_bridge.json in the data dir,
// the same pattern as the GUI trust verdicts (ax_trust.json, screen_trust.json).
// The backend (app/services/capture_bridge.py) reads that file and asks here.
// Every request needs the per-launch token from the file, so another local
// program cannot help itself to the screen through Xod.
const http = require("http");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const BRIDGE_FILE = "capture_bridge.json";
const MAX_BODY = 16384;

function bridgeFilePath(dataDir) {
  return path.join(dataDir, BRIDGE_FILE);
}

function writeBridgeFile(dataDir, record) {
  fs.mkdirSync(dataDir, { recursive: true });
  const file = bridgeFilePath(dataDir);
  const tmp = file + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(record));
  fs.renameSync(tmp, file);
}

function removeBridgeFile(dataDir, token) {
  try {
    const cur = JSON.parse(fs.readFileSync(bridgeFilePath(dataDir), "utf8"));
    if (cur && cur.token === token) fs.unlinkSync(bridgeFilePath(dataDir));
  } catch (e) { /* already gone or someone else's: leave it */ }
}

// deps is injectable for tests: { desktopCapturer, screen } default to Electron's.
function createCaptureBridge({ dataDir, appVersion = "", token = null, deps = null } = {}) {
  const dc = (deps && deps.desktopCapturer) || require("electron").desktopCapturer;
  const scr = (deps && deps.screen) || require("electron").screen;
  const bridgeToken = token || crypto.randomBytes(24).toString("hex");
  let server = null;

  function sendJson(res, status, obj) {
    const body = JSON.stringify(obj);
    res.writeHead(status, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) });
    res.end(body);
  }

  function sendPng(res, source) {
    const png = source.thumbnail.toPNG();
    const size = source.thumbnail.getSize ? source.thumbnail.getSize() : { width: 0, height: 0 };
    res.writeHead(200, { "Content-Type": "image/png", "Content-Length": png.length,
                         "X-Xod-Width": String(size.width || ""), "X-Xod-Height": String(size.height || "") });
    res.end(png);
  }

  async function captureScreen() {
    const primary = scr.getPrimaryDisplay();
    const scale = primary.scaleFactor || 1;
    const sources = await dc.getSources({ types: ["screen"], thumbnailSize: {
      width: Math.round(primary.size.width * scale), height: Math.round(primary.size.height * scale) } });
    if (!sources.length) throw new Error("no screen source");
    return sources.find((s) => String(s.display_id) === String(primary.id)) || sources[0];
  }

  async function captureWindow(name) {
    const want = String(name || "").trim().toLowerCase();
    if (!want) throw new Error("no window name given");
    const sources = await dc.getSources({ types: ["window"], thumbnailSize: { width: 3840, height: 2400 } });
    const exact = sources.find((s) => (s.name || "").toLowerCase() === want);
    if (exact) return exact;
    const partial = sources.find((s) => (s.name || "").toLowerCase().includes(want));
    if (partial) return partial;
    throw new Error("no window named " + JSON.stringify(name));
  }

  function readBody(req) {
    return new Promise((resolve, reject) => {
      let body = "";
      req.on("data", (c) => { body += c; if (body.length > MAX_BODY) { reject(new Error("body too large")); req.destroy(); } });
      req.on("end", () => resolve(body));
      req.on("error", reject);
    });
  }

  async function handle(req, res) {
    if (req.method === "GET" && req.url === "/health") return sendJson(res, 200, { ok: true });
    if (req.headers["x-xod-bridge-token"] !== bridgeToken) return sendJson(res, 401, { error: "bad token" });
    let body = {};
    try { body = JSON.parse((await readBody(req)) || "{}"); } catch (e) { return sendJson(res, 400, { error: "bad json" }); }
    try {
      if (req.method === "POST" && req.url === "/capture/screen") return sendPng(res, await captureScreen());
      if (req.method === "POST" && req.url === "/capture/window") return sendPng(res, await captureWindow(body.name));
      return sendJson(res, 404, { error: "no such route" });
    } catch (e) {
      return sendJson(res, 502, { error: String((e && e.message) || e) });
    }
  }

  return {
    token: bridgeToken,
    start() {
      return new Promise((resolve, reject) => {
        server = http.createServer((req, res) => { handle(req, res).catch(() => { try { res.destroy(); } catch (e) {} }); });
        server.once("error", reject);
        server.listen(0, "127.0.0.1", () => {
          const port = server.address().port;
          try {
            writeBridgeFile(dataDir, { port, token: bridgeToken, pid: process.pid,
                                       app_version: appVersion, started_at: Date.now() / 1000 });
          } catch (e) { /* the backend will report the bridge as missing, which is diagnosable */ }
          resolve(port);
        });
      });
    },
    stop() {
      if (server) { try { server.close(); } catch (e) {} server = null; }
      removeBridgeFile(dataDir, bridgeToken);
    },
  };
}

function startCaptureBridge(opts) {
  const bridge = createCaptureBridge(opts);
  bridge.start().catch(() => { /* a missing bridge reads as unavailable in the backend, never a crash */ });
  return bridge;
}

module.exports = { createCaptureBridge, startCaptureBridge, bridgeFilePath, BRIDGE_FILE };
