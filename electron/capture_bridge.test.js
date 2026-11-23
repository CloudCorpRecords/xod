// The capture bridge serves screen captures from the GUI main process (the
// identity macOS credits with the Screen Recording grant) to the Python
// backend over loopback, guarded by a per-launch token. These tests run the
// real HTTP server with fake Electron capture sources.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const { createCaptureBridge, bridgeFilePath } = require("./capture_bridge");

function fakeImage(png, w, h) {
  return { toPNG: () => png, getSize: () => ({ width: w, height: h }) };
}

function fakeDeps() {
  return {
    desktopCapturer: {
      getSources: async (opts) => {
        if (opts.types.includes("screen")) {
          return [{ display_id: "1", name: "main", thumbnail: fakeImage(Buffer.from("SCREENPNG"), 800, 600) }];
        }
        return [
          { display_id: "", name: "iPhone Mirroring", thumbnail: fakeImage(Buffer.from("MIRRORPNG"), 400, 800) },
          { display_id: "", name: "Something Else", thumbnail: fakeImage(Buffer.from("OTHERPNG"), 10, 10) },
        ];
      },
    },
    screen: {
      getPrimaryDisplay: () => ({ id: 1, scaleFactor: 2, size: { width: 400, height: 300 } }),
    },
  };
}

function call(port, method, urlPath, token, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : "";
    const req = http.request({ host: "127.0.0.1", port, path: urlPath, method,
      headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data),
                 ...(token ? { "x-xod-bridge-token": token } : {}) } }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
    });
    req.on("error", reject);
    req.end(data);
  });
}

test("bridge serves screen and window captures to the token holder only", async (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "xod-bridge-"));
  const bridge = createCaptureBridge({ dataDir, appVersion: "0.0.0-test", token: "test-token", deps: fakeDeps() });
  const port = await bridge.start();
  t.after(() => bridge.stop());

  const record = JSON.parse(fs.readFileSync(bridgeFilePath(dataDir), "utf8"));
  assert.equal(record.port, port);
  assert.equal(record.token, "test-token");
  assert.equal(record.app_version, "0.0.0-test");

  const noToken = await call(port, "POST", "/capture/screen", null, {});
  assert.equal(noToken.status, 401);
  const badToken = await call(port, "POST", "/capture/screen", "nope", {});
  assert.equal(badToken.status, 401);

  const screen = await call(port, "POST", "/capture/screen", "test-token", {});
  assert.equal(screen.status, 200);
  assert.equal(screen.headers["content-type"], "image/png");
  assert.equal(screen.body.toString(), "SCREENPNG");
  assert.equal(screen.headers["x-xod-width"], "800");

  const win = await call(port, "POST", "/capture/window", "test-token", { name: "iPhone Mirroring" });
  assert.equal(win.status, 200);
  assert.equal(win.body.toString(), "MIRRORPNG");

  const partial = await call(port, "POST", "/capture/window", "test-token", { name: "iphone mirr" });
  assert.equal(partial.status, 200);
  assert.equal(partial.body.toString(), "MIRRORPNG");

  const missing = await call(port, "POST", "/capture/window", "test-token", { name: "No Such Window" });
  assert.equal(missing.status, 502);

  const health = await call(port, "GET", "/health", null);
  assert.equal(health.status, 200);

  bridge.stop();
  assert.equal(fs.existsSync(bridgeFilePath(dataDir)), false);
});
