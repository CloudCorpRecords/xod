#!/usr/bin/env python3
"""
Capture bridge and voice lifecycle logging (AGE-98).

Covered here:
  - the backend's capture client (services/capture_bridge.py) reads the GUI's
    capture_bridge.json and returns the GUI's PNG bytes for the screen and
    for a named window, sending the per-launch token
  - with no bridge file (a backend running outside the app) the client raises
    CaptureBridgeError with a reason, never a silent empty frame
  - a wrong token from the GUI side surfaces as CaptureBridgeError too
  - the Mac capture paths are gone from the backend: neither desktop.py nor
    iphone_mirror.py shells to screencapture any more
  - POST /api/voice/log lands allowlisted voice events in event_log.jsonl
    under kind voice.<event>, and refuses unknown event names
  - the voice UI no longer silences a blocked wake listener: conversation.js
    keeps wakeOn, shows the banner and logs wake_blocked

Run: .venv/bin/python tests/smoke/run_capture_bridge_test.py
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-bridge-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
(TMP / "data").mkdir(parents=True)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


SCREEN_PNG = b"\x89PNG\r\n\x1a\nSCREEN"
WINDOW_PNG = b"\x89PNG\r\n\x1a\nWINDOW"
TOKEN = "test-token-123"


class FakeBridge(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.headers.get("x-xod-bridge-token") != TOKEN:
            body = json.dumps({"error": "bad token"}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/capture/screen":
            body = SCREEN_PNG
        elif self.path == "/capture/window" and payload.get("name") == "iPhone Mirroring":
            body = WINDOW_PNG
        else:
            body = json.dumps({"error": "no such window"}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    from app.services import capture_bridge

    # No bridge file yet: a clear error, not a black frame.
    try:
        capture_bridge.capture_screen_png()
        check("missing bridge raises", False, "no exception")
    except capture_bridge.CaptureBridgeError as e:
        check("missing bridge raises", "capture bridge isn't running" in str(e), str(e))
    check("available() false without bridge", capture_bridge.available() is False)

    server = HTTPServer(("127.0.0.1", 0), FakeBridge)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    (TMP / "data" / "capture_bridge.json").write_text(json.dumps(
        {"port": port, "token": TOKEN, "pid": os.getpid(), "app_version": "test", "started_at": 0}))

    check("available() true with bridge", capture_bridge.available() is True)
    check("screen capture returns GUI bytes", capture_bridge.capture_screen_png() == SCREEN_PNG)
    check("window capture returns GUI bytes", capture_bridge.capture_window_png("iPhone Mirroring") == WINDOW_PNG)
    try:
        capture_bridge.capture_window_png("No Such Window")
        check("unknown window raises", False, "no exception")
    except capture_bridge.CaptureBridgeError as e:
        check("unknown window raises", "502" in str(e), str(e))

    # Wrong token in the file: the GUI's 401 surfaces as a bridge error.
    (TMP / "data" / "capture_bridge.json").write_text(json.dumps(
        {"port": port, "token": "wrong", "pid": os.getpid(), "app_version": "test", "started_at": 0}))
    try:
        capture_bridge.capture_screen_png()
        check("bad token raises", False, "no exception")
    except capture_bridge.CaptureBridgeError as e:
        check("bad token raises", "401" in str(e), str(e))
    server.shutdown()

    # The backend no longer captures on its own on a Mac.
    desktop_src = (ROOT / "app" / "services" / "desktop.py").read_text()
    mirror_src = (ROOT / "app" / "services" / "iphone_mirror.py").read_text()
    check("desktop.py never invokes screencapture", '"/usr/sbin/screencapture"' not in desktop_src)
    check("iphone_mirror.py never invokes screencapture", '"/usr/sbin/screencapture"' not in mirror_src)
    check("desktop.py captures via the bridge", "capture_bridge.capture_screen_png()" in desktop_src)
    check("iphone_mirror.py captures via the bridge", "capture_bridge.capture_window_png(APP_NAME)" in mirror_src)

    # The GUI side: main.js runs the bridge and writes the mic verdict.
    main_js = (ROOT / "electron" / "main.js").read_text()
    check("main.js starts the capture bridge", "startCaptureBridge" in main_js)
    check("main.js writes mic_trust.json", "mic_trust.json" in main_js and 'getMediaAccessStatus("microphone")' in main_js)

    # The voice UI: a blocked wake listener is visible and logged, not silenced.
    conv = (ROOT / "app" / "static" / "js" / "conversation.js").read_text()
    check("conversation.js logs wake_blocked", 'vlog("wake_blocked"' in conv)
    check("conversation.js logs mic_open_fail", 'vlog("mic_open_fail"' in conv)
    check("conversation.js logs wake_start", 'vlog("wake_start"' in conv)
    check("conversation.js shows the blocked banner", "xodWakeBlocked" in conv)
    check("conversation.js no longer silences wake", 'C.wakeOn = false; document.body.classList.remove("xod-wake-on")' not in conv)

    # The voice log endpoint lands events in event_log.jsonl.
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    r = client.post("/api/voice/log", json={"event": "wake_blocked", "payload": {"error": "NotAllowedError: blocked"}})
    check("voice/log accepts an allowlisted event", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
    r = client.post("/api/voice/log", json={"event": "mic_open_ok", "payload": {"rate": 48000}})
    check("voice/log accepts mic_open_ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
    r = client.post("/api/voice/log", json={"event": "rm_rf", "payload": {}})
    check("voice/log refuses unknown events", r.status_code == 200 and r.json().get("ok") is False, r.text[:200])
    log_lines = (TMP / "data" / "event_log.jsonl").read_text().strip().splitlines()
    kinds = [json.loads(line).get("kind") for line in log_lines]
    check("event log holds voice.wake_blocked", "voice.wake_blocked" in kinds, str(kinds))
    check("event log holds voice.mic_open_ok", "voice.mic_open_ok" in kinds, str(kinds))
    check("event log holds no refused event", "voice.rm_rf" not in kinds, str(kinds))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
