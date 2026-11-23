"""
Shared password vault broker (services/muse_vault.py): when Xod's own
Keychain vault has no login for a site, credential_vault.get_login asks
Tree for it -- through an injected provider (the Xod Connect seam) or the
tree-inbox route with a response file Tree writes back. A brokered login
is used once and never persisted by Xod; the audit records site + outcome
only, never a value.

Covered here:
  V1  provider happy path returns the login; audit holds site + outcome
      and neither the username nor the password
  V2  provider unknown site returns None and the miss is negative-cached
      (the provider is not asked twice)
  V3  provider failure returns None, never raises, audits "failed"
  V4  tree-inbox happy path: request text + structured payload, Tree's
      response file is read, deleted, and returned; audit is value-free
  V5  tree answers found:false -> None, negative-cached (no second send)
  V6  no transport and queued transport both return None without waiting
  V7  a response for a different request id is refused (fail closed)
  V8  credential_vault wiring: local hit wins without touching the
      broker; local miss falls through to the broker; a brokered login
      is NOT saved into Xod's own vault
  V9  status() shape

All credentials below are obvious fakes.

  .venv/bin/python tests/smoke/run_muse_vault_test.py
"""
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-musevault-"))
os.environ.setdefault("XOD_SPEECH", "off")
RESP = TMP / "responses"
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_NO_UPDATE_CHECK": "1",
                   "XOD_VAULT_INDEX": str(TMP / "vault_index.json"),
                   "XOD_TREE_VAULT_DIR": str(RESP)})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []

FAKE_USER = "fake-user@example.com"
FAKE_PASS = "fake-password-123"


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def main():
    from app.services import credential_vault, muse_vault

    def reset_state():
        muse_vault.set_provider(None)
        muse_vault._send_fn = None
        muse_vault._negative.clear()
        muse_vault._stats.update({"supplied": 0, "not_found": 0, "unreachable": 0,
                                  "failed": 0, "no_transport": 0})
        muse_vault._last_outcome = ""

    def audit_text():
        p = muse_vault.audit_path()
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    print("V1 provider happy path")
    reset_state()
    calls = []

    def provider_hit(site):
        calls.append(site)
        return (FAKE_USER, FAKE_PASS) if site == "example.com" else None

    muse_vault.set_provider(provider_hit)
    got = muse_vault.get_login("https://app.example.com/login?next=/home")
    check("provider login returned for the registrable site key",
          got == (FAKE_USER, FAKE_PASS) and calls == ["example.com"], f"got={got} calls={calls}")
    text = audit_text()
    check("audit records the site and the supplied outcome",
          '"site": "example.com"' in text and '"outcome": "supplied"' in text
          and '"source": "provider"' in text)
    check("audit contains neither the username nor the password",
          FAKE_USER not in text and FAKE_PASS not in text)

    print("V2 provider unknown site + negative cache")
    reset_state()
    calls2 = []

    def provider_miss(site):
        calls2.append(site)
        return None

    muse_vault.set_provider(provider_miss)
    check("unknown site returns None", muse_vault.get_login("missing-site.org") is None)
    check("second ask is served by the negative cache (provider not re-asked)",
          muse_vault.get_login("missing-site.org") is None and len(calls2) == 1,
          f"calls={calls2}")

    print("V3 provider failure fails closed")
    reset_state()

    def provider_boom(site):
        raise RuntimeError("vault is locked")

    muse_vault.set_provider(provider_boom)
    try:
        got3 = muse_vault.get_login("boom-site.net")
        raised = False
    except Exception:
        got3, raised = "raised", True
    check("provider exception returns None and never raises",
          got3 is None and not raised, f"got={got3}")
    check("the failure is audited as failed",
          '"site": "boom-site.net"' in audit_text() and '"outcome": "failed"' in audit_text())

    print("V4 tree-inbox happy path")
    reset_state()
    sent = []

    def fake_send(kind, body, payload):
        sent.append((kind, body, payload))
        RESP.mkdir(parents=True, exist_ok=True)
        (RESP / f"{payload['request_id']}.json").write_text(
            '{"request_id": "%s", "site": "%s", "found": true, '
            '"username": "%s", "password": "%s"}'
            % (payload["request_id"], payload["site"], FAKE_USER, FAKE_PASS),
            encoding="utf-8")
        return {"state": "inbox", "id": "msg-v4"}

    muse_vault._send_fn = fake_send
    got4 = muse_vault.get_login("https://handshake-app.com/login", timeout=5)
    check("tree-supplied login returned", got4 == (FAKE_USER, FAKE_PASS), f"got={got4}")
    check("the request went out as a task with the structured credential_request payload",
          len(sent) == 1 and sent[0][0] == "task"
          and sent[0][2].get("site") == "handshake-app.com"
          and sent[0][2].get("request_id", "").startswith("vaultreq-")
          and "handshake-app.com" in sent[0][1], f"sent={sent}")
    check("the request text itself carries no credential",
          FAKE_PASS not in sent[0][1] and FAKE_USER not in sent[0][1])
    check("the response file was deleted after the read",
          not list(RESP.glob("*.json")), f"left={list(RESP.glob('*'))}")
    text4 = audit_text()
    check("tree supply is audited by site only, value-free",
          '"site": "handshake-app.com"' in text4 and '"source": "tree-inbox"' in text4
          and FAKE_USER not in text4 and FAKE_PASS not in text4)

    print("V5 tree answers found:false")
    reset_state()
    sent5 = []

    def fake_send_miss(kind, body, payload):
        sent5.append(payload)
        RESP.mkdir(parents=True, exist_ok=True)
        (RESP / f"{payload['request_id']}.json").write_text(
            '{"request_id": "%s", "site": "%s", "found": false}'
            % (payload["request_id"], payload["site"]), encoding="utf-8")
        return {"state": "inbox", "id": "msg-v5"}

    muse_vault._send_fn = fake_send_miss
    check("found:false returns None",
          muse_vault.get_login("nothere-site.com", timeout=5) is None)
    check("the not_found answer is negative-cached (no second send)",
          muse_vault.get_login("nothere-site.com", timeout=5) is None and len(sent5) == 1,
          f"sends={len(sent5)}")

    print("V6 no transport / queued transport")
    reset_state()
    orig_responses_dir = muse_vault.responses_dir
    muse_vault.responses_dir = lambda: None
    try:
        t0 = time.time()
        got6 = muse_vault.get_login("anywhere-site.com", timeout=30)
        check("no transport returns None at once (no 30s wait)",
              got6 is None and time.time() - t0 < 5, f"elapsed={time.time() - t0:.1f}")
    finally:
        muse_vault.responses_dir = orig_responses_dir
    check("no_transport is audited", '"outcome": "no_transport"' in audit_text())
    reset_state()
    muse_vault._send_fn = lambda kind, body, payload: {"state": "queued", "id": "msg-v6"}
    t0 = time.time()
    got6b = muse_vault.get_login("queued-site.com", timeout=30)
    check("a queued (outbox) request is not waited on",
          got6b is None and time.time() - t0 < 5, f"elapsed={time.time() - t0:.1f}")
    check("the queued outcome is audited as unreachable",
          '"site": "queued-site.com"' in audit_text()
          and '"outcome": "unreachable"' in audit_text())

    print("V7 mismatched response is refused")
    reset_state()

    def fake_send_wrong(kind, body, payload):
        RESP.mkdir(parents=True, exist_ok=True)
        (RESP / f"{payload['request_id']}.json").write_text(
            '{"request_id": "vaultreq-someoneelse", "site": "%s", "found": true, '
            '"username": "%s", "password": "%s"}'
            % (payload["site"], FAKE_USER, FAKE_PASS), encoding="utf-8")
        return {"state": "inbox", "id": "msg-v7"}

    muse_vault._send_fn = fake_send_wrong
    check("a response naming a different request id returns None (fail closed)",
          muse_vault.get_login("victim-site.com", timeout=5) is None)
    check("the refusal is audited as failed",
          '"site": "victim-site.com"' in audit_text()
          and '"outcome": "failed"' in audit_text())

    print("V8 credential_vault wiring")
    reset_state()
    provider_calls = []

    def provider_v8(site):
        provider_calls.append(site)
        return (FAKE_USER, FAKE_PASS)

    muse_vault.set_provider(provider_v8)
    credential_vault.save_login("local-site.com", "local-user", "local-fake-pass",
                                source="test")
    got8a = credential_vault.get_login("https://local-site.com/login")
    check("a local vault hit wins and the broker is never consulted",
          got8a == ("local-user", "local-fake-pass") and provider_calls == [],
          f"got={got8a} calls={provider_calls}")
    got8b = credential_vault.get_login("https://brokered-site.com/login")
    check("a local miss falls through to the broker",
          got8b == (FAKE_USER, FAKE_PASS) and provider_calls == ["brokered-site.com"],
          f"got={got8b} calls={provider_calls}")
    check("the brokered login was NOT saved into Xod's own vault",
          all(e["site"] != "brokered-site.com" for e in credential_vault.list_logins())
          and "brokered-site.com" not in Path(os.environ["XOD_VAULT_INDEX"]).read_text(),
          f"logins={credential_vault.list_logins()}")

    print("V9 status shape")
    reset_state()
    st = muse_vault.status()
    check("status reports provider/transport/stats/negative cache",
          st["provider"] is False and isinstance(st["transport"], bool)
          and set(st["stats"]) == {"supplied", "not_found", "unreachable", "failed",
                                   "no_transport"}
          and st["negative_cached"] == [], f"status={st}")

    reset_state()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
