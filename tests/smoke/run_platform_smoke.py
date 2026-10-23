"""
Xod from scratch on this computer (any system: CI runs it on Windows and macOS): a brand-new data folder, the
server started the way the desktop app starts it, and a stand-in model server so no LM Studio, key or GPU is
needed. Checks the page loads, settings, a real chat end to end (routing, the answer streaming back, the chat
saved), memory search, the companion, the update pill's version, and that the secrets vault round-trips.

  python tests/smoke/run_platform_smoke.py            (uses the file keyring unless XOD_REAL_KEYRING=1)
  python tests/smoke/run_platform_smoke.py --python path/to/python   (e.g. the Python bundled in the app)
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


ANSWER = "Hello from the stand-in model. Paris is the capital of France."


class FakeModel(BaseHTTPRequestHandler):
    """An OpenAI-compatible server that always answers, streaming or not."""
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "stand-in", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path.endswith("/embeddings"):
            n = len(req.get("input") or [])
            body = json.dumps({"data": [{"index": i, "embedding": [((i + j) % 7) / 7.0 for j in range(64)]} for i in range(n)]}).encode()
        elif req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in (ANSWER[:20], ANSWER[20:]):
                self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        else:
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(method, url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:           # a failing endpoint is a failed check, not a crash of the test
        return e.code, e.read().decode("utf-8", "replace")


def main():
    py = sys.executable
    if "--python" in sys.argv:
        py = sys.argv[sys.argv.index("--python") + 1]
    fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    model_url = f"http://127.0.0.1:{fake.server_address[1]}/v1"

    data = tempfile.mkdtemp(prefix="xod-fresh-")
    port = free_port()
    env = {**os.environ, "XOD_DATA_DIR": data, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "LLM_PROVIDER": "lm_studio", "LLM_BASE_URL": model_url,
           "LLM_MODEL": "stand-in", "XOD_SECRETS_SERVICE": "Xod smoke test keys"}
    if not os.environ.get("XOD_REAL_KEYRING"):
        kr = Path(data) / "test-keyring.json"
        kr.write_text("{}")
        env.update(PYTHON_KEYRING_BACKEND="tests.support.filekeyring.FileKeyring", XOD_TEST_KEYRING_FILE=str(kr),
                   PYTHONPATH=str(ROOT))
    log = open(Path(data) / "server.log", "w")
    t0 = time.time()
    server = subprocess.Popen([py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=os.environ.get("XOD_BACKEND_DIR") or str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        up = False
        for _ in range(240):
            try:
                st, body = http("GET", base + "/", timeout=2)
                if st == 200 and "<title>" in body:
                    up = True
                    break
            except Exception:
                pass
            if server.poll() is not None:
                break
            time.sleep(0.5)
        print(f"      (server up in {time.time() - t0:.1f}s, data in {data})")
        check("the server starts from scratch and serves the page", up, (Path(data) / "server.log").read_text()[-1500:] if not up else "")
        if not up:
            return
        check("its data folder is the one it was given", (Path(data) / "xod.db").exists())

        st, body = http("GET", base + "/api/config")
        cfg = json.loads(body)
        check("settings load", st == 200 and cfg.get("llm_provider") == "lm_studio", body[:200])

        sys.path.insert(0, str(ROOT))
        from app.version import VERSION
        st, body = http("GET", base + "/api/update")
        check("it knows its version (the update pill)", st == 200 and VERSION in body, body[:200])

        # A real chat turn, end to end.
        req = urllib.request.Request(base + "/api/chat", data=json.dumps({"messages": [{"role": "user", "content": "say hello and tell me the capital of France"}]}).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        text, session, types = "", None, set()
        with urllib.request.urlopen(req, timeout=180) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except ValueError:
                    continue
                types.add(ev.get("type"))
                if ev.get("type") == "content":
                    text += ev.get("content", "")
                if ev.get("type") == "session_info":
                    session = ev.get("session_id")
        check("a chat gets an answer from the model", "Paris" in text, f"{text[:200]!r} {sorted(t for t in types if t)}")
        if session:
            st, body = http("GET", f"{base}/api/sessions/{session}")
            check("…and the chat is saved", st == 200 and "capital of France" in body, body[:200])

        st, body = http("GET", base + "/api/memory/search?q=capital", timeout=60)
        check("memory search works", st == 200, body[:200])
        st, body = http("GET", base + "/api/soul")
        s = json.loads(body)
        check("the companion has a state, a plan for today and a notebook", st == 200 and "stage_name" in s and "today" in (s.get("inner") or {}), body[:200])
        st, body = http("GET", base + "/api/lmstudio/status")
        check("the model status answers", st == 200, body[:200])
        st, body = http("GET", base + "/api/about-you")
        check("'what Xod knows about you' answers", st == 200, body[:200])

        # The secrets vault: save a key, read it back (Credential Manager on Windows with XOD_REAL_KEYRING=1).
        code = ("from app.services import keychain as k; k.set_password('smoke', 'key', 'v' * 3000); k.reset_cache(); "
                "ok = k.get_password('smoke', 'key') == 'v' * 3000; k.delete_password('smoke', 'key'); print('VAULT', ok)")
        out = subprocess.run([py, "-c", code], cwd=os.environ.get("XOD_BACKEND_DIR") or str(ROOT), env=env, capture_output=True, text=True, timeout=60)
        check("the secrets vault saves and reads back a long value", "VAULT True" in out.stdout, (out.stdout + out.stderr)[-500:])
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        fake.shutdown()
        log.close()
        logtext = (Path(data) / "server.log").read_text(errors="replace")
        if "Traceback" in logtext:          # show the first one whole
            i = logtext.index("Traceback")
            print("      server log, first error:\n        " + "\n        ".join(logtext[i:i + 3000].splitlines()[:40]))

    # The installer packs only the files electron/package.json lists: one the app's code requires but isn't listed
    # would make the installed app fail to start, and nothing else here would notice.
    import re
    edir = Path(__file__).resolve().parents[2] / "electron"
    listed = set(json.loads((edir / "package.json").read_text())["build"]["files"])
    needed = {m + ("" if m.endswith(".js") else ".js") for f in listed if f.endswith(".js")
              for m in re.findall(r"""require\(["']\./([^"']+)["']\)""", (edir / f).read_text())}
    check("every file the desktop app requires is packed into the installer", needed <= listed, str(sorted(needed - listed)))

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
