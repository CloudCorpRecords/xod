"""
Xod -> Tree bridge (services/tree_inbox.py, POST /api/tree/message).

Covered here:
  - a message lands in the configured tree-inbox as one JSON file with
    exactly the bridge fields (id, ts, from_device, from_app, kind,
    text, read=false), written atomically (no .tmp left behind)
  - from_device is the family registry name for this device's id
  - an unknown kind and an empty text are refused, nothing written
  - when the configured inbox folder is absent the message queues in
    the local outbox and the result says "queued", never "inbox"
  - once the inbox is reachable, the next send drains the outbox into
    it and reports the drained count
  - the endpoint returns the same states and refuses a bad kind

Run: .venv/bin/python tests/smoke/run_tree_inbox_test.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-tree-inbox-"))
os.environ["HOME"] = str(TMP)
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
os.environ["XOD_SECRETS_SERVICE"] = "Xod test keys"
os.environ["PYTHON_KEYRING_BACKEND"] = "tests.support.filekeyring.FileKeyring"
(TMP / "data").mkdir()

results = []
skips = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""), flush=True)


def skip(name, reason):
    skips.append(name)
    print(f"SKIP  {name}  -- {reason}", flush=True)


def have(mod):
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


INBOX = TMP / "shared" / "tree-inbox"
REGISTRY = {
    "family": "test-family",
    "account": "cloudtheboy@gmail.com",
    "devices": [
        {"id": "razer-test", "name": "razer-test-box", "platform": "Windows",
         "endpoint": "100.80.212.74", "capabilities": ["Windows hands"],
         "public_key": "PLACEHOLDER-F0-no-key-enrolled", "signature": None,
         "last_seen": "2026-11-03T00:00:00-07:00", "status": "active"},
    ],
}


def main():
    from app.services import tree_inbox

    reg = TMP / "registry.json"
    reg.write_text(json.dumps(REGISTRY))
    os.environ["XOD_FAMILY_REGISTRY_PATH"] = str(reg)
    os.environ["XOD_FAMILY_DEVICE_ID"] = "razer-test"

    # ---- happy path: configured inbox exists, message lands atomically
    INBOX.mkdir(parents=True)
    os.environ["XOD_TREE_INBOX_DIR"] = str(INBOX)
    out = tree_inbox.send_message("note", "hello from the test")
    check("send reports state inbox", out["state"] == "inbox", str(out))
    files = list(INBOX.glob("*.json"))
    check("exactly one message file written", len(files) == 1, str(files))
    check("no .tmp left behind (atomic rename)", not list(INBOX.glob("*.tmp")))
    msg = json.loads(files[0].read_text())
    check("message carries exactly the bridge fields",
          set(msg) == {"id", "ts", "from_device", "from_app", "kind", "text", "read"},
          str(sorted(msg)))
    check("message fields are honest",
          msg["id"] == out["id"] and msg["from_app"] == "xod"
          and msg["kind"] == "note" and msg["text"] == "hello from the test"
          and msg["read"] is False and msg["ts"].endswith("Z"), str(msg))
    # The verified registry reader needs the crypto stack (services/
    # mail_crypto.py); without it from_device falls back to the device
    # id, which is the documented honest degradation, not a failure.
    if have("cryptography"):
        check("from_device is the registry name for this device id",
              msg["from_device"] == "razer-test-box", msg["from_device"])
    else:
        skip("from_device is the registry name for this device id",
             "cryptography not installed in this environment")
    check("file is named by message id", files[0].name == f"{msg['id']}.json")

    # ---- validation: bad kind and empty text write nothing
    for kind, text in (("memo", "x"), ("note", "   "), ("note", "")):
        try:
            tree_inbox.send_message(kind, text)
            check(f"refuses kind={kind!r} text={text!r}", False, "no error raised")
        except ValueError:
            check(f"refuses kind={kind!r} text={text!r}", True)
    check("refusals wrote nothing", len(list(INBOX.glob("*.json"))) == 1)

    # ---- outbox fallback: configured inbox folder absent -> queued
    missing = TMP / "gone" / "tree-inbox"
    os.environ["XOD_TREE_INBOX_DIR"] = str(missing)
    out2 = tree_inbox.send_message("task", "queued while the drive is away")
    check("absent inbox reports state queued", out2["state"] == "queued", str(out2))
    queued = list(tree_inbox.outbox_dir().glob("*.json"))
    check("queued message is in the local outbox", len(queued) == 1
          and json.loads(queued[0].read_text())["text"] == "queued while the drive is away",
          str(queued))

    # ---- drain: inbox reachable again, next send moves the queue over
    missing.mkdir(parents=True)
    out3 = tree_inbox.send_message("result", "back again")
    check("send after recovery reports inbox + drained 1",
          out3["state"] == "inbox" and out3["drained"] == 1, str(out3))
    check("outbox is empty after the drain", not list(tree_inbox.outbox_dir().glob("*.json")))
    texts = sorted(json.loads(f.read_text())["text"] for f in missing.glob("*.json"))
    check("both messages are in the inbox after the drain",
          texts == ["back again", "queued while the drive is away"], str(texts))

    # ---- endpoint: same states over HTTP, bad kind refused
    if have("fastapi"):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        r = client.post("/api/tree/message", json={"kind": "note", "text": "via the endpoint"})
        body = r.json()
        check("endpoint accepts a message", r.status_code == 200 and body.get("success") is True
              and body.get("state") == "inbox", str(body)[:200])
        r2 = client.post("/api/tree/message", json={"kind": "memo", "text": "nope"})
        body2 = r2.json()
        check("endpoint refuses a bad kind without writing",
              body2.get("success") is False and body2.get("state") == "invalid", str(body2)[:200])
        texts = sorted(json.loads(f.read_text())["text"] for f in missing.glob("*.json"))
        check("endpoint message landed, refusal did not",
              "via the endpoint" in texts and "nope" not in texts, str(texts))
    else:
        skip("endpoint checks (3)", "fastapi not installed in this environment")

    # ---- the Family screen carries the composer (static check)
    js = (ROOT / "app" / "static" / "js" / "family.js").read_text()
    check("family screen has the Send to Tree composer",
          'id="treeSend"' in js and "/api/tree/message" in js and "Send to Tree" in js)

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY: {len(results) - len(failed)}/{len(results)} checks passed"
          + (f"; SKIPPED: {skips}" if skips else "")
          + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
