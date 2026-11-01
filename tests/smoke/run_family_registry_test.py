"""
Family Link F0: the family registry reader and the read-only Family
endpoint (services/family_registry.py, GET /api/family).

The registry is a plain JSON file in the synced bus space: one record
per device (name, platform, tailnet endpoint, capabilities, device
public key, last seen, status). F0 only reads it. Records are UNSIGNED
in F0 (signatures arrive with F1/F4), so everything the reader returns
is flagged interim and must never be treated as authenticated identity.

Covered here:
  - a seeded registry parses, and the endpoint returns exactly the
    seeded active members with their capabilities
  - a malformed record is skipped and reported, never a crash, and the
    good records around it still load
  - a missing registry file is an honest empty state, not an error
  - a corrupt registry file is an honest invalid state, not an error
  - joining and revoked devices are valid records but not members
  - the registry path is configurable (env var wins)
  - the Family screen script renders from the same fields the API
    returns (static check; the live render is proven on the dev
    instance in the F0 proof leg)

Run: .venv/bin/python tests/smoke/run_family_registry_test.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-family-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
(TMP / "data").mkdir()

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


SEED = {
    "family": "test-family",
    "version": 1,
    "updated": "2026-10-31T20:00:00-07:00",
    "devices": [
        {
            "id": "macbook-pro",
            "name": "MacBook Pro (test)",
            "platform": "macOS",
            "endpoint": "100.119.42.98",
            "capabilities": ["cloud model: openai gpt-6.1-terra", "macOS hands"],
            "public_key": "PLACEHOLDER-F0-no-key-enrolled",
            "signature": None,
            "last_seen": "2026-10-31T20:00:00-07:00",
            "status": "active",
        },
        {
            "id": "razer",
            "name": "razer-test-box",
            "platform": "Windows 11",
            "endpoint": "100.80.212.74",
            "capabilities": ["local model: lm_studio prism-ml/bonsai-27b", "Windows hands"],
            "public_key": "PLACEHOLDER-F0-no-key-enrolled",
            "signature": None,
            "last_seen": "2026-10-31T20:00:00-07:00",
            "status": "active",
        },
        {
            "id": "phone",
            "name": "S25 Ultra",
            "platform": "Android",
            "endpoint": None,
            "capabilities": ["approvals", "camera"],
            "public_key": "PLACEHOLDER-F0-no-key-enrolled",
            "signature": None,
            "last_seen": "2026-10-31T20:00:00-07:00",
            "status": "active",
        },
    ],
}


def write_registry(payload):
    path = TMP / "registry.json"
    if isinstance(payload, str):
        path.write_text(payload)
    else:
        path.write_text(json.dumps(payload))
    os.environ["XOD_FAMILY_REGISTRY_PATH"] = str(path)
    return path


def main():
    from app.services import family_registry

    # ---- path is configurable, env var wins
    path = write_registry(SEED)
    check("registry path comes from the env override", family_registry.registry_path() == path)

    # ---- a seeded registry parses; members are exactly the active devices
    view = family_registry.load_registry()
    check("seeded registry loads with state ok", view["state"] == "ok", str(view)[:200])
    check("no problems on a clean seed", view["problems"] == [], str(view["problems"]))
    check("records are flagged interim and unsigned", view["interim_unsigned"] is True)
    names = [m["name"] for m in view["members"]]
    check("members are exactly the seeded active devices",
          names == ["MacBook Pro (test)", "razer-test-box", "S25 Ultra"], str(names))
    mac = view["members"][0]
    check("member carries platform, endpoint, capabilities, last seen",
          mac["platform"] == "macOS" and mac["endpoint"] == "100.119.42.98"
          and "cloud model: openai gpt-6.1-terra" in mac["capabilities"]
          and mac["last_seen"] == "2026-10-31T20:00:00-07:00", str(mac))
    check("public key placeholder is explicit, never invented",
          mac["public_key"].startswith("PLACEHOLDER"), str(mac["public_key"]))

    # ---- the endpoint returns the same view
    from fastapi.testclient import TestClient
    from app.main import app
    # Xod's guard checks the Host header; TestClient's default host is not local.
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    r = client.get("/api/family")
    check("GET /api/family answers 200", r.status_code == 200, r.text[:200])
    body = r.json()
    check("endpoint returns exactly the seeded members",
          [m["id"] for m in body["members"]] == ["macbook-pro", "razer", "phone"], r.text[:300])
    check("endpoint payload carries capabilities for rendering",
          body["members"][1]["capabilities"] == ["local model: lm_studio prism-ml/bonsai-27b", "Windows hands"],
          r.text[:300])
    check("endpoint is read-only (POST refused)",
          client.post("/api/family", json={}).status_code in (404, 405))

    # ---- malformed records are skipped and reported, never a crash
    broken = json.loads(json.dumps(SEED))
    broken["devices"].append({"id": "no-name", "platform": "macOS", "status": "active"})
    broken["devices"].append({"id": "bad-status", "name": "Nope", "platform": "macOS", "status": "vanished"})
    broken["devices"].append("not-a-record")
    broken["devices"].append({"id": "macbook-pro", "name": "Dupe", "platform": "macOS", "status": "active"})
    write_registry(broken)
    view = family_registry.load_registry()
    check("malformed records do not crash the reader", view["state"] == "ok", str(view)[:200])
    check("good records still load around the bad ones",
          [m["id"] for m in view["members"]] == ["macbook-pro", "razer", "phone"], str(view["members"])[:200])
    check("every skipped record is reported", len(view["problems"]) == 4, str(view["problems"]))

    # ---- joining and revoked are valid records, but not members
    mixed = json.loads(json.dumps(SEED))
    mixed["devices"].append({"id": "new-box", "name": "New Box", "platform": "Linux", "status": "joining"})
    mixed["devices"].append({"id": "old-box", "name": "Old Box", "platform": "Linux", "status": "revoked"})
    write_registry(mixed)
    view = family_registry.load_registry()
    check("joining and revoked records load as devices",
          [d["id"] for d in view["devices"]] == ["macbook-pro", "razer", "phone", "new-box", "old-box"],
          str(view["devices"])[:200])
    check("only active devices are members",
          [m["id"] for m in view["members"]] == ["macbook-pro", "razer", "phone"], str(view["members"])[:200])

    # ---- missing file: an honest empty state
    os.environ["XOD_FAMILY_REGISTRY_PATH"] = str(TMP / "no-such-registry.json")
    view = family_registry.load_registry()
    check("missing registry is state missing, not an error", view["state"] == "missing", str(view)[:200])
    check("missing registry has no members and says why",
          view["members"] == [] and "No family registry" in view["message"], str(view)[:200])
    r = client.get("/api/family")
    check("endpoint stays 200 with the honest empty state",
          r.status_code == 200 and r.json()["state"] == "missing", r.text[:200])

    # ---- corrupt file: an honest invalid state
    write_registry("{ this is not json")
    view = family_registry.load_registry()
    check("corrupt registry is state invalid, not an error", view["state"] == "invalid", str(view)[:200])
    check("corrupt registry has no members and reports the problem",
          view["members"] == [] and len(view["problems"]) == 1, str(view)[:200])

    # ---- the Family screen renders from the fields the API returns
    screen = (ROOT / "app" / "static" / "js" / "family.js").read_text()
    check("family screen fetches the family endpoint", "/api/family" in screen)
    check("family screen renders name, platform, endpoint, capabilities, last seen",
          all(field in screen for field in ("name", "platform", "endpoint", "capabilities", "last_seen")))
    index = (ROOT / "app" / "static" / "index.html").read_text()
    check("family screen is reachable from the tools menu", "openFamilyPanel" in index and "family.js" in index)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main()
