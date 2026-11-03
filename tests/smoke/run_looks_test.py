"""
Living Look packs (services/looks.py): a pack is data, never code. It lives in the data
folder's looks/<pack>/ directory as a pack.json plus one keyframe image and one animation
clip per state. Verification is claimed versus verified: every asset exists on disk, the
keyframe hashes match the manifest, the theme parses, and every quirk the pack declares is
a behavior the app actually has, bound to the state it belongs to.

Also covers the API surface: /api/looks lists packs with their verification, pack assets
serve over /looks, and /api/companion accepts an installed pack as the look while still
rejecting unknown ids (the built-in looks are untouched).

Uses a throwaway data folder, never yours:
  .venv/bin/python tests/smoke/run_looks_test.py
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
sys.path.insert(0, str(ROOT))

DATA = tempfile.mkdtemp(prefix="xod-looks-")
os.environ["XOD_DATA_DIR"] = DATA
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"

from app.paths import DATA_DIR  # noqa: E402
from app.services import looks  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write_pack(pid, mutate=None, states=None):
    """A minimal valid pack; mutate(manifest, dir) may break it on purpose."""
    d = DATA_DIR / "looks" / pid
    d.mkdir(parents=True, exist_ok=True)
    man = {"pack": pid, "version": "0.1.0-test", "status": "test fixture",
           "theme": {"flavor": "test flavor", "palette": {"primary": "black"}},
           "states": {}, "quirks": [{"id": "wand-tap-finish", "bound_state": "celebrating",
                                     "description": "test quirk"}]}
    for st in (states or looks.PACK_STATES):
        key, clip = f"{st}.webp", f"{st}.mp4"
        (d / key).write_bytes(f"key-{st}".encode())
        (d / clip).write_bytes(f"clip-{st}".encode())
        man["states"][st] = {"file": key, "animation_file": clip, "sha256": sha(f"key-{st}".encode())}
    if mutate:
        mutate(man, d)
    (d / "pack.json").write_text(json.dumps(man))
    return d


# ---------------------------------------------------------------- unit: verification
good = write_pack("testwiz")
rep = looks.verify_pack("testwiz")
check("good pack verifies", rep["ok"], json.dumps(rep.get("errors")))
check("good pack reports every state", set(rep["states"]) == set(looks.PACK_STATES))
check("good pack keyframe hash checked", rep["states"]["idle"]["sha256_ok"] is True)
check("list_packs finds the good pack verified",
      any(p["id"] == "testwiz" and p["ok"] for p in looks.list_packs()))

write_pack("gap", mutate=lambda m, d: (d / "upgrading.mp4").unlink())
rep = looks.verify_pack("gap")
check("missing animation file fails", not rep["ok"] and any("upgrading" in e for e in rep["errors"]),
      json.dumps(rep.get("errors")))

write_pack("badhash", mutate=lambda m, d: m["states"]["idle"].__setitem__("sha256", "0" * 64))
rep = looks.verify_pack("badhash")
check("wrong keyframe hash fails", not rep["ok"] and any("idle" in e for e in rep["errors"]),
      json.dumps(rep.get("errors")))

write_pack("badquirk", mutate=lambda m, d: m["quirks"].__setitem__(0, {"id": "fly-to-moon", "bound_state": "idle"}))
rep = looks.verify_pack("badquirk")
check("unknown quirk fails", not rep["ok"] and any("quirk" in e for e in rep["errors"]),
      json.dumps(rep.get("errors")))

write_pack("wrongbind", mutate=lambda m, d: m["quirks"][0].__setitem__("bound_state", "idle"))
rep = looks.verify_pack("wrongbind")
check("quirk bound to the wrong state fails", not rep["ok"],
      json.dumps(rep.get("errors")))

write_pack("escape", mutate=lambda m, d: m["states"]["idle"].__setitem__("file", "../outside.webp"))
rep = looks.verify_pack("escape")
check("asset path escaping the pack fails", not rep["ok"], json.dumps(rep.get("errors")))

broken = DATA_DIR / "looks" / "broken"
broken.mkdir(parents=True, exist_ok=True)
(broken / "pack.json").write_text("{not json")
rep = looks.verify_pack("broken")
check("unparseable manifest fails cleanly", not rep["ok"] and bool(rep["errors"]))

rep = looks.verify_pack("nosuchpack")
check("unknown pack id fails cleanly", not rep["ok"] and bool(rep["errors"]))

check("mood map covers the app moods",
      set(looks.MOOD_TO_STATE) >= {"idle", "thinking", "searching", "working", "waiting",
                                   "listening", "error", "sleepy", "happy"})
check("mood map lands on pack states", set(looks.MOOD_TO_STATE.values()) <= set(looks.PACK_STATES))
check("thinking maps to working", looks.MOOD_TO_STATE["thinking"] == "working")
check("searching maps to looking", looks.MOOD_TO_STATE["searching"] == "looking")
check("happy maps to celebrating", looks.MOOD_TO_STATE["happy"] == "celebrating")
check("is_pack knows installed packs", looks.is_pack("testwiz") and not looks.is_pack("moon")
      and not looks.is_pack("../testwiz"))


# ---------------------------------------------------------------- API
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def req(method, url, body=None):
    r = urllib.request.Request(url, method=method,
                               data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


port = free_port()
env = {**os.environ, "XOD_DATA_DIR": DATA, "XOD_NO_UPDATE_CHECK": "1",
       "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring"}
server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app",
                           "--host", "127.0.0.1", "--port", str(port)],
                          cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
base = f"http://127.0.0.1:{port}"
try:
    up = False
    for _ in range(120):
        try:
            urllib.request.urlopen(base + "/api/companion", timeout=2)
            up = True
            break
        except Exception:
            time.sleep(0.25)
    check("server boots", up)

    code, body = req("GET", base + "/api/looks")
    packs = json.loads(body).get("packs", []) if code == 200 else []
    wiz = next((p for p in packs if p["id"] == "testwiz"), None)
    check("GET /api/looks lists the pack verified", bool(wiz and wiz["ok"]), body[:200].decode("utf8", "replace"))

    code, body = req("GET", base + "/looks/testwiz/idle.webp")
    check("pack keyframe serves over /looks", code == 200 and body == b"key-idle", f"code={code}")

    code, body = req("POST", base + "/api/looks/testwiz/verify")
    check("POST /api/looks/<id>/verify verifies", code == 200 and json.loads(body)["ok"], f"code={code}")

    code, _ = req("POST", base + "/api/looks/nosuchpack/verify")
    check("verify of an unknown pack is 404", code == 404, f"code={code}")

    code, body = req("POST", base + "/api/companion", {"character": "testwiz"})
    check("companion accepts an installed pack", code == 200 and json.loads(body)["character"] == "testwiz",
          f"code={code}")
    code, body = req("GET", base + "/api/companion")
    check("companion reads back the pack", json.loads(body)["character"] == "testwiz")

    code, _ = req("POST", base + "/api/companion", {"character": "nosuchlook"})
    check("companion still rejects unknown looks", code == 400, f"code={code}")

    code, body = req("POST", base + "/api/companion", {"character": "moon"})
    check("companion still accepts the built-in default", code == 200
          and json.loads(body)["character"] == "moon", f"code={code}")
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("FAILED:", "; ".join(failed))
    sys.exit(1)
print("ALL PASS")
