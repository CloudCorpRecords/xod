"""
Partner v1.2 brain test: the judgment layer over the deterministic floor.

The floor must behave byte-for-byte as v1.1 with the brain off, unkeyed,
malformed, or the API down; the brain is consulted at exactly four
triggers; shadow mode never changes a floor outcome; active mode's
bounded effects (one deferral per episode, cap-hold release, escalation)
work and the hard hourly ceiling binds past all of them; the
provisioning route stores the key in the brain's key store and the key
string appears in no log line and no state file; every call is metered
to Xod's usage endpoint or, when Xod is down, to a brain_usage log line.

The key store is a fake in memory and the Keychain store is driven
through a fake runner: no test here touches a real Keychain, a real
Partner, the real Xod, or any real port or state dir.

Run: python3 tests/partner/run_partner_brain_test.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Standing discipline (0.9.14 integration rider): every suite runner pins
# the file-keyring backend explicitly, so a test process can never reach
# for the real macOS Keychain. This suite stubs its key stores anyway;
# the pin is belt and braces, set before anything app-adjacent loads.
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
os.environ.setdefault("XOD_TEST_KEYRING_FILE",
                      os.path.join(tempfile.mkdtemp(prefix="xod-brain-keys-"), "keys.json"))

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "partner"))
import xod_partner as XP
import partner_brain as PB


# -- fakes -------------------------------------------------------------------


class _FakeProbe:
    def __init__(self, ok=True):
        self.ok = ok

    def __call__(self):
        return self.ok


class _FakeRestart:
    def __init__(self, ok=False):
        self.ok = ok
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.ok


class _FakeWalk:
    def scan(self, front=False):
        return {"running": True, "cards": []}

    def press(self, card_id, front=False, away=False):
        return {"pressed": False, "reason": "fake walk"}

    def request_access(self):
        return False


class _StubLLM:
    """A tiny OpenAI-compatible stub. responder(trigger) returns the raw
    content string for the completion; every request is recorded."""

    def __init__(self, responder=None):
        self.requests = []
        self.responder = responder or (lambda trigger: json.dumps(
            {"decision": "restart", "reason": "stub concurs"}))
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                stub.requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                      "body": body})
                if self.path == "/api/partner/brain/usage":
                    out = json.dumps({"recorded": True}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return
                content = ""
                try:
                    user = body["messages"][1]["content"]
                    trigger = user.split("Trigger: ")[1].split("\n")[0]
                except Exception:
                    trigger = ""
                payload = {"choices": [{"message": {"content": stub.responder(trigger)}}],
                           "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
                out = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.port

    def triggers(self):
        out = []
        for req in self.requests:
            if req["path"].endswith("/chat/completions"):
                user = req["body"]["messages"][1]["content"]
                out.append(user.split("Trigger: ")[1].split("\n")[0])
        return out

    def usage_posts(self):
        return [r["body"] for r in self.requests if r["path"] == "/api/partner/brain/usage"]


def _dead_url():
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return "http://127.0.0.1:%d" % port


def _brain(lines, stub, mode="shadow", store=None, usage_url=None):
    cfg = dict(XP.DEFAULTS)
    cfg.update({"partner_brain_mode": mode,
                "brain_base_url": (stub.url + "/v1") if stub else _dead_url() + "/v1",
                "brain_model": "stub-model", "brain_provider": "stub",
                "brain_usage_url": usage_url if usage_url is not None else _dead_url() + "/usage"})
    if store is None:
        store = PB.MemoryBrainKeyStore()
        store.save("sk-test-key")
    return PB.Brain(cfg, lines.append, store), cfg


def _core(tmp, probe, restart, lines, brain=None, cfg=None, **kw):
    return XP.Core(cfg or dict(XP.DEFAULTS), Path(tmp), probe, restart, lines.append,
                   brain=brain, **kw)


def _drive_gate(core, base=1000.0):
    core.cycle(base)
    core.cycle(base + 15.0)
    core.cycle(base + 30.0)


# -- the suite -----------------------------------------------------------------


def main():
    # decision validation: the strict enum and shape, nothing else passes
    assert PB.parse_decision_text('{"decision": "restart", "reason": "it is dead"}') == ("restart", "it is dead")
    assert PB.parse_decision_text('```json\n{"decision": "escalate", "reason": "loop"}\n```') == ("escalate", "loop")
    assert PB.parse_decision_text('{"decision": "explode", "reason": "x"}') is None
    assert PB.parse_decision_text('{"decision": "restart"}') is None
    assert PB.parse_decision_text('{"decision": "restart", "reason": "  "}') is None
    assert PB.parse_decision_text("not json") is None
    assert PB.validate_decision({"decision": "wait_and_reprobe", "reason": "slow"}) == ("wait_and_reprobe", "slow")
    print("PASS: decision validation accepts only the enum with a one-line reason", flush=True)

    # the Keychain store seam: exact service and account, update in place,
    # proven against a fake runner (no real Keychain is touched)
    calls = []

    def fake_runner(argv, timeout=15):
        calls.append(argv)
        if "find-generic-password" in argv:
            return 0, "sk-from-keychain\n", ""
        return 0, "", ""

    store = PB.KeychainBrainKeyStore(runner=fake_runner)
    assert PB.KEYCHAIN_SERVICE == "dev.xod.partner brain"
    assert store.save("sk-new") is True
    save_argv = calls[-1]
    assert save_argv[0] == "security" and "add-generic-password" in save_argv
    assert save_argv[save_argv.index("-s") + 1] == "dev.xod.partner brain"
    assert save_argv[save_argv.index("-a") + 1] == "brain-api-key"
    assert "-U" in save_argv
    assert store.get() == "sk-from-keychain"
    missing = PB.KeychainBrainKeyStore(runner=lambda argv, timeout=15: (44, "", "not found"))
    assert missing.get() is None and missing.save("") is False
    print("PASS: the Keychain store targets its own item and updates in place (fake runner)", flush=True)

    # a healthy Xod never consults the brain at all
    with tempfile.TemporaryDirectory() as tmp:
        stub = _StubLLM()
        lines = []
        brain, cfg = _brain(lines, stub, usage_url=stub.url + "/api/partner/brain/usage")
        probe, restart = _FakeProbe(True), _FakeRestart()
        core = _core(tmp, probe, restart, lines, brain=brain, cfg=cfg,
                     process_alive=lambda: True)
        for t in (0.0, 15.0, 30.0, 45.0):
            core.cycle(t)
        assert stub.triggers() == [] and restart.calls == 0, (stub.triggers(), restart.calls)
        print("PASS: a healthy Xod never consults the brain", flush=True)

    # off mode: never consulted, floor unchanged
    with tempfile.TemporaryDirectory() as tmp:
        stub = _StubLLM()
        lines = []
        brain, cfg = _brain(lines, stub, mode="off")
        probe, restart = _FakeProbe(False), _FakeRestart(ok=True)
        core = _core(tmp, probe, restart, lines, brain=brain, cfg=cfg,
                     process_alive=lambda: True)
        _drive_gate(core)
        assert stub.triggers() == [] and restart.calls == 1, (stub.triggers(), restart.calls)
        print("PASS: off mode is never consulted and the floor restarts as today", flush=True)

    # no key: never consulted, floor unchanged
    with tempfile.TemporaryDirectory() as tmp:
        stub = _StubLLM()
        lines = []
        brain, cfg = _brain(lines, stub, store=PB.MemoryBrainKeyStore())
        probe, restart = _FakeProbe(False), _FakeRestart(ok=True)
        core = _core(tmp, probe, restart, lines, brain=brain, cfg=cfg,
                     process_alive=lambda: True)
        _drive_gate(core)
        assert stub.triggers() == [] and restart.calls == 1, (stub.triggers(), restart.calls)
        assert any("no brain key" in line for line in lines), lines[-3:]
        print("PASS: an unkeyed brain is never consulted and the floor restarts as today", flush=True)

    # trigger (a) wedge suspicion, shadow: consulted and logged, floor acts as today
    with tempfile.TemporaryDirectory() as tmp:
        stub = _StubLLM(lambda trigger: json.dumps({"decision": "wait_and_reprobe", "reason": "looks like load"}))
        lines = []
        brain, cfg = _brain(lines, stub, mode="shadow", usage_url=stub.url + "/api/partner/brain/usage")
        probe, restart = _FakeProbe(False), _FakeRestart(ok=True)
        core = _core(tmp, probe, restart, lines, brain=brain, cfg=cfg,
                     process_alive=lambda: True)
        _drive_gate(core)
        assert "wedge_suspicion" in stub.triggers(), stub.triggers()
        assert restart.calls == 1, restart.calls  # shadow never defers
        assert any("brain consult" in line and "shadow" in line for line in lines), lines[-4:]
        print("PASS: wedge suspicion consults in shadow and the floor still restarts", flush=True)

    # no wedge without a live process: a plain death is the floor's alone
    with tempfile.TemporaryDirectory() as tmp:
        stub = _StubLLM()
        lines = []
        brain, cfg = _brain(lines, stub, mode="active")
        probe, restart = _FakeProbe(False), _FakeRestart(ok=True)
        core = _core(tmp, probe, restart, lines, brain=brain, cfg=cfg,
                     process_alive=lambda: False)
        _drive_gate(core)
        assert "wedge_suspicion" not in stub.triggers(), stub.triggers()
        assert restart.calls == 1, restart.calls
        print("PASS: a dead process with a silent port is not a wedge; no consult", flush=True)

    # trigger (a) active: wait defers one restart by one cycle, never twice
    with tempfile.TemporaryDirectory() as tmp:
        stub = _StubLLM(lambda trigger: json.dumps({"decision": "wait_and_reprobe", "reason": "slow under load"}))
        lines = []
        brain, cfg = _brain(lines, stub, mode="active", usage_url=stub.url + "/api/partner/brain/usage")
        probe, restart = _FakeProbe(False), _FakeRestart(ok=True)
        core = _core(tmp, probe, restart, lines, brain=brain, cfg=cfg,
                     process_alive=lambda: True)
        _drive_gate(core)  # the gate cycle: deferred
        assert restart.calls == 0, restart.calls
        assert any("deferred by one probe cycle" in line for line in lines), lines[-4:]
        core.cycle(1045.0)  # next cycle: the same episode may not defer again
        assert restart.calls == 1, restart.calls
        assert stub.triggers().count("wedge_suspicion") == 2, stub.triggers()
        assert any("already used its one deferral" in line for line in lines), lines[-4:]
        print("PASS: active wait_and_reprobe defers once per episode and never twice", flush=True)

    # trigger (b) cap event, shadow: the hold lands exactly as today
    with tempfile.TemporaryDirectory() as tmp:
        stub = _StubLLM(lambda trigger: json.dumps({"decision": "release_hold", "reason": "promotion work"}))
        lines = []
        brain, cfg = _brain(lines, stub, mode="shadow")
        probe, restart = _FakeProbe(False), _FakeRestart(ok=False)
        core = _core(tmp, probe, restart, lines, brain=brain, cfg=cfg)
        core.cycle(0.0); core.cycle(15.0); core.cycle(30.0)
        core.cycle(61.0); core.cycle(200.0)
        assert restart.calls == 3, restart.calls
        core.cycle(600.0)  # cap event: shadow brain would release; the floor holds
        assert restart.calls == 3 and core.cap_hold, (restart.calls, core.cap_hold)
        assert "cap_event" in stub.triggers(), stub.triggers()
        print("PASS: a cap event consults in shadow and the HOLD lands as today", flush=True)

    # trigger (b) cap event, active: release_hold clears the hold and the revival proceeds
    with tempfile.TemporaryDirectory() as tmp:
        stub = _StubLLM(lambda trigger: json.dumps({"decision": "release_hold", "reason": "promotion work"}))
        lines = []
        brain, cfg = _brain(lines, stub, mode="active")
        probe, restart = _FakeProbe(False), _FakeRestart(ok=False)
        core = _core(tmp, probe, restart, lines, brain=brain, cfg=cfg)
        core.cycle(0.0); core.cycle(15.0); core.cycle(30.0)
        core.cycle(61.0); core.cycle(200.0)
        assert restart.calls == 3, restart.calls
        core.cycle(600.0)
        assert not core.cap_hold and restart.calls == 4, (core.cap_hold, restart.calls)
        assert any("brain release_hold" in line for line in lines), lines[-4:]
        print("PASS: active release_hold clears the cap hold and the revival proceeds", flush=True)

    # the hard ceiling binds past a brain release_hold: 6 total in the hour, stop
    with tempfile.TemporaryDirectory() as tmp:
        stub = _StubLLM(lambda trigger: json.dumps({"decision": "release_hold", "reason": "let it run"}))
        lines = []
        brain, cfg = _brain(lines, stub, mode="active")
        probe, restart = _FakeProbe(False), _FakeRestart(ok=False)
        core = _core(tmp, probe, restart, lines, brain=brain, cfg=cfg)
        core.restarts = [(9700.0 + 10.0 * i, "healthy" if i % 2 else "still down", i % 2 == 0)
                         for i in range(6)]
        _drive_gate(core, base=10000.0)
        assert restart.calls == 0 and core.cap_hold, (restart.calls, core.cap_hold)
        assert any("hard restart ceiling" in line for line in lines), lines[-4:]
        assert stub.triggers() == [], stub.triggers()  # the ceiling is never the brain's to argue
        print("PASS: the hard hourly ceiling blocks a restart no brain decision can override", flush=True)

    # triggers (c) and (d): triage after each revival; the third identical
    # failure consults repeated_failure instead, and an active escalate lands
    with tempfile.TemporaryDirectory() as tmp:
        def responder(trigger):
            if trigger == "repeated_failure":
                return json.dumps({"decision": "escalate", "reason": "same failure three times"})
            return json.dumps({"decision": "restart", "reason": "triage concurs"})
        stub = _StubLLM(responder)
        lines = []
        brain, cfg = _brain(lines, stub, mode="active")
        probe, restart = _FakeProbe(False), _FakeRestart(ok=False)
        core = _core(tmp, probe, restart, lines, brain=brain, cfg=cfg,
                     process_alive=lambda: False)
        core.cycle(0.0); core.cycle(15.0); core.cycle(30.0)   # revival 1: triage
        core.cycle(61.0)                                     # revival 2: triage
        core.cycle(200.0)                                    # revival 3: repeated failure
        trig = stub.triggers()
        assert trig.count("post_revival_triage") == 2, trig
        assert trig.count("repeated_failure") == 1, trig
        st = core.status(200.0)
        assert st["brain"]["escalation"] is not None, st["brain"]
        assert st["brain"]["escalation"]["trigger"] == "repeated_failure", st["brain"]
        assert any("BRAIN ESCALATION" in line for line in lines), lines[-4:]
        print("PASS: triage follows revivals; the third identical failure escalates", flush=True)

    # malformed outputs: non-JSON, wrong enum, missing reason; floor as today
    for label, content in (("non-JSON", "the app looks sad"),
                           ("wrong enum", json.dumps({"decision": "explode", "reason": "boom"})),
                           ("missing reason", json.dumps({"decision": "restart"}))):
        with tempfile.TemporaryDirectory() as tmp:
            stub = _StubLLM(lambda trigger, c=content: c)
            lines = []
            brain, cfg = _brain(lines, stub, mode="active")
            probe, restart = _FakeProbe(False), _FakeRestart(ok=True)
            core = _core(tmp, probe, restart, lines, brain=brain, cfg=cfg,
                         process_alive=lambda: True)
            _drive_gate(core)
            assert restart.calls == 1, (label, restart.calls)
            assert any("brain malfunction" in line for line in lines), (label, lines[-4:])
        print("PASS: malformed output (%s) falls back to the floor and logs" % label, flush=True)

    # API down: the consult fails soft, the floor restarts, nothing raises
    with tempfile.TemporaryDirectory() as tmp:
        lines = []
        brain, cfg = _brain(lines, None, mode="active")
        probe, restart = _FakeProbe(False), _FakeRestart(ok=True)
        core = _core(tmp, probe, restart, lines, brain=brain, cfg=cfg,
                     process_alive=lambda: True)
        _drive_gate(core)
        assert restart.calls == 1, restart.calls
        assert any("brain malfunction" in line for line in lines), lines[-4:]
        print("PASS: an unreachable brain API degrades silently to the floor", flush=True)

    # metering: Xod answers, so the call lands on its usage endpoint;
    # Xod down, so the same fields land in partner.log as brain_usage
    with tempfile.TemporaryDirectory() as tmp:
        stub = _StubLLM()
        lines = []
        brain, cfg = _brain(lines, stub, mode="shadow",
                            usage_url=stub.url + "/api/partner/brain/usage")
        probe, restart = _FakeProbe(False), _FakeRestart(ok=True)
        core = _core(tmp, probe, restart, lines, brain=brain, cfg=cfg,
                     process_alive=lambda: True)
        _drive_gate(core)
        posts = stub.usage_posts()
        assert posts and posts[0]["trigger"] == "wedge_suspicion", posts
        assert posts[0]["model"] == "stub-model" and posts[0]["prompt_tokens"] == 11, posts
        assert posts[0]["completion_tokens"] == 7 and posts[0]["decision"] == "restart", posts
        print("PASS: a brain call is metered to Xod's usage endpoint when Xod answers", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        stub = _StubLLM()
        lines = []
        brain, cfg = _brain(lines, stub, mode="shadow")  # dead usage URL by default
        probe, restart = _FakeProbe(False), _FakeRestart(ok=True)
        core = _core(tmp, probe, restart, lines, brain=brain, cfg=cfg,
                     process_alive=lambda: True)
        _drive_gate(core)
        assert any(line.startswith("") and "brain_usage" in line and "prompt_tokens=11" in line
                   for line in lines), lines[-5:]
        print("PASS: with Xod down the call is metered as a brain_usage log line", flush=True)

    # status carries the brain block: mode, key presence, the hard ceiling
    with tempfile.TemporaryDirectory() as tmp:
        stub = _StubLLM()
        lines = []
        brain, cfg = _brain(lines, stub, mode="shadow")
        core = _core(tmp, _FakeProbe(True), _FakeRestart(), lines, brain=brain, cfg=cfg)
        st = core.status(0.0)
        assert st["version"] == "1.2", st["version"]
        assert st["brain"]["mode"] == "shadow" and st["brain"]["has_key"] is True, st["brain"]
        assert st["brain"]["hard_ceiling_per_hour"] == XP.HARD_RESTART_CEILING_PER_HOUR == 6, st["brain"]
        print("PASS: status reports version 1.2 and the brain block", flush=True)

    # provisioning route: wrong token refused; right token stores; the key
    # string appears in no answer, no log line, and no state file; the
    # brain path reads it back afterwards
    with tempfile.TemporaryDirectory() as tmp:
        stub = _StubLLM()
        lines = []
        store = PB.MemoryBrainKeyStore()
        brain, cfg = _brain(lines, stub, mode="shadow", store=store,
                            usage_url=stub.url + "/api/partner/brain/usage")
        cfg["api_port"] = 0
        core = _core(tmp, _FakeProbe(True), _FakeRestart(), lines, brain=brain, cfg=cfg)
        server = XP.make_server(cfg, core, _FakeWalk(), lines.append)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        secret = "sk-provisioned-SECRET-12345"

        def post(path, body, token=None):
            req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                         data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json",
                                                  **({"Authorization": "Bearer " + token} if token else {})})
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.status, resp.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode()

        code, _ = post("/brain/key", {"key": secret})
        assert code == 401 and store.get() is None, code
        code, _ = post("/brain/key", {"key": secret}, token="wrong")
        assert code == 401 and store.get() is None, code
        code, text = post("/brain/key", {"key": secret}, token=core.token)
        assert code == 200 and json.loads(text) == {"stored": True, "has_key": True}, (code, text)
        assert secret not in text
        assert store.get() == secret
        assert all(secret not in line for line in lines), [l for l in lines if secret in l]
        for path in Path(tmp).rglob("*"):
            if path.is_file():
                assert secret not in path.read_bytes().decode("utf-8", "ignore"), path
        result = core._consult_brain("wedge_suspicion", {"process_alive": True})
        assert result is not None and result["decision"] == "restart", result
        chat = [r for r in stub.requests if r["path"].endswith("/chat/completions")]
        assert chat and chat[-1]["auth"] == "Bearer " + secret, chat
        server.shutdown()
        print("PASS: provisioning stores the key, refuses bad tokens, and never leaks it", flush=True)

    # Xod side: partner_client provisions through the real Partner server,
    # and record_brain_usage ledgers under the partner context
    with tempfile.TemporaryDirectory() as tmp:
        stub = _StubLLM()
        lines = []
        store = PB.MemoryBrainKeyStore()
        brain, cfg = _brain(lines, stub, mode="shadow", store=store)
        cfg["api_port"] = 0
        core = _core(tmp, _FakeProbe(True), _FakeRestart(), lines, brain=brain, cfg=cfg)
        server = XP.make_server(cfg, core, _FakeWalk(), lines.append)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()

        import os
        os.environ["XOD_PARTNER_URL"] = "http://127.0.0.1:%d" % port
        os.environ["XOD_PARTNER_TOKEN"] = core.token
        ledger_calls = []

        class _FakeLedger:
            @staticmethod
            def append_record(provider, model, key_profile=None, usage=None,
                              prompt_chars=0, completion_chars=0, context=""):
                ledger_calls.append({"provider": provider, "model": model,
                                     "usage": usage, "context": context})

        import types
        app_mod = types.ModuleType("app")
        services_mod = types.ModuleType("app.services")
        ledger_mod = types.ModuleType("app.services.usage_ledger")
        ledger_mod.append_record = _FakeLedger.append_record
        services_mod.usage_ledger = ledger_mod
        app_mod.services = services_mod
        saved = {k: sys.modules.get(k) for k in ("app", "app.services", "app.services.usage_ledger")}
        sys.modules.update({"app": app_mod, "app.services": services_mod,
                            "app.services.usage_ledger": ledger_mod})
        try:
            spec = importlib.util.spec_from_file_location(
                "partner_client_under_test", ROOT / "app" / "services" / "partner_client.py")
            pc = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(pc)
            out = pc.provision_brain_key("sk-xod-side-key", profile="openai:default")
            assert out == {"stored": True, "has_key": True}, out
            assert store.get() == "sk-xod-side-key"
            rec = pc.record_brain_usage("openai", "gpt-5-nano", 11, 7,
                                        trigger="cap_event", decision="release_hold")
            assert rec["recorded"] is True and rec["context"] == "partner", rec
            assert ledger_calls and ledger_calls[0]["context"] == "partner", ledger_calls
            assert ledger_calls[0]["usage"] == {"prompt_tokens": 11, "completion_tokens": 7}, ledger_calls
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
            os.environ.pop("XOD_PARTNER_URL", None)
            os.environ.pop("XOD_PARTNER_TOKEN", None)
        server.shutdown()
        print("PASS: Xod side provisions the brain key and ledgers its usage as partner", flush=True)

    print("ALL PARTNER BRAIN TESTS PASSED", flush=True)


if __name__ == "__main__":
    main()
