"""
Permissions regression test: Xod can tell whether its Accessibility access is
really in effect, and its guided repair can only ever touch its own grant.

The 2026-10-30 incident: the installed app's files changed, the Accessibility
switch in System Settings still showed ON, and the grant underneath was stale,
so the Muse auto-approver silently pressed nothing. The checks here cover the
pure logic with the macOS calls faked behind the permissions seam. They must
NEVER run a real tccutil reset or trust prompt on this machine: the runner and
the AX backend are fakes throughout.

Run: .venv/bin/python tests/self_heal/run_permissions_test.py
"""
import plistlib, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import os
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
import app.services.permissions as P


class _FakeAX:
    def __init__(self, value, prompt_value=None):
        self.value = value
        self.prompt_value = value if prompt_value is None else prompt_value
        self.prompt_calls = 0

    def trusted(self):
        return self.value

    def trusted_with_prompt(self):
        self.prompt_calls += 1
        return self.prompt_value


class _FakeRunner:
    def __init__(self, returncode=0, stderr=""):
        self.calls, self.returncode, self.stderr = [], returncode, stderr

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))

        class R:
            pass
        r = R()
        r.returncode, r.stderr, r.stdout = self.returncode, self.stderr, ""
        return r


def _seam_trusted_with_prompt():
    """The real _CTypesAX trusted_with_prompt with only its output half
    faked: a stand-in prompt function receives the options dictionary the
    seam built and inspects it with real CoreFoundation calls, so the
    prompt option is proven to reach the framework call, not assumed. The
    dictionary the callback receives is released when trusted_with_prompt
    returns, so every observation is captured inside the callback."""
    import ctypes
    real = P._CTypesAX()
    assert real._cf is not None and real._prompt is not None, "the real AX seam must load on this Mac"
    cf = real._cf
    cf.CFDictionaryGetCount.restype = ctypes.c_long
    cf.CFDictionaryGetCount.argtypes = [ctypes.c_void_p]
    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf.CFDictionaryGetKeysAndValues.restype = None
    cf.CFDictionaryGetKeysAndValues.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                                ctypes.POINTER(ctypes.c_void_p)]

    asv = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    real_key = ctypes.c_void_p.in_dll(asv, "kAXTrustedCheckOptionPrompt")
    assert real_key, "the framework must export the prompt key constant"

    for answer in (False, True):
        ax = P._CTypesAX.__new__(P._CTypesAX)
        ax._cf = cf
        ax._prompt_key = real_key
        seen = {}

        def fake_prompt(options, _answer=answer):
            # The framework looks the option up under its own exported
            # constant and the dictionary compares keys by pointer, so
            # look it up under that very constant: finding the value here
            # is exactly what the real call does inside
            # AXIsProcessTrustedWithOptions. (Before the fix the seam
            # keyed the dictionary with a same-text CFString; this lookup
            # then found nothing, which is the 2026-10-30 crash.)
            seen["count"] = cf.CFDictionaryGetCount(options)
            seen["value"] = cf.CFDictionaryGetValue(options, real_key)
            keys = (ctypes.c_void_p * 1)()
            values = (ctypes.c_void_p * 1)()
            cf.CFDictionaryGetKeysAndValues(options, keys, values)
            seen["key"] = keys[0]
            return _answer

        ax._prompt = fake_prompt
        result = ax.trusted_with_prompt()
        assert result is answer, (result, answer)
        assert seen["count"] == 1, seen
        assert seen["key"] == real_key.value, seen
        assert seen["value"] == ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue").value, seen
    print("PASS: the re-ask passes the prompt option into the framework call, keyed by the framework constant itself", flush=True)

    # Without the real key constant the seam must refuse to ask at all:
    # asking with a same-text lookalike key is the 2026-10-30 crash.
    ax = P._CTypesAX.__new__(P._CTypesAX)
    ax._cf = cf
    calls = []
    ax._prompt = lambda options: calls.append(options) or True
    ax._prompt_key = None
    assert ax.trusted_with_prompt() is None
    assert calls == [], calls
    print("PASS: no real key constant, no ask: the re-ask answers None instead of risking the framework", flush=True)

    ax = P._CTypesAX.__new__(P._CTypesAX)
    ax._cf, ax._prompt, ax._prompt_key = cf, None, real_key
    assert ax.trusted_with_prompt() is None
    print("PASS: an unavailable framework answers None from the re-ask, never a crash", flush=True)


def main():
    real_backend, real_bid_fn = P._backend, P.own_bundle_id
    try:
        # status shaping: the notice appears only when approvals are on and trust is really off
        P._backend = _FakeAX(False)
        st = P.accessibility_status(auto_approve_on=True)
        assert st["trusted"] is False and st["notice"] == P.STALE_NOTICE, st
        assert st["settings_url"] == P.SETTINGS_URL, st
        st = P.accessibility_status(auto_approve_on=False)
        assert st["notice"] is None, st
        P._backend = _FakeAX(True)
        assert P.accessibility_status(auto_approve_on=True)["notice"] is None
        P._backend = _FakeAX(None)
        assert P.accessibility_status(auto_approve_on=True)["notice"] is None
        print("PASS: status shows the stale-grant notice only when approvals are on and trust is off", flush=True)

        # bundle id resolution from a real (fake) app bundle on disk
        with tempfile.TemporaryDirectory() as td:
            app = Path(td) / "Xod.app"
            exe = app / "Contents" / "Resources" / "python" / "bin" / "python3.12"
            exe.parent.mkdir(parents=True)
            exe.write_text("")
            with open(app / "Contents" / "Info.plist", "wb") as f:
                plistlib.dump({"CFBundleIdentifier": "com.example.xod"}, f)
            assert P.bundle_id_for_executable(str(exe)) == "com.example.xod"
            with open(app / "Contents" / "Info.plist", "wb") as f:
                plistlib.dump({"CFBundleIdentifier": "not a bundle id!!"}, f)
            assert P.bundle_id_for_executable(str(exe)) is None
            plain = Path(td) / "python3"
            plain.write_text("")
            assert P.bundle_id_for_executable(str(plain)) is None
        print("PASS: bundle id comes from the running app's own Info.plist, never a guess", flush=True)

        # command construction: fixed service, validated id, argv form
        assert P.reset_command("com.example.xod") == ["tccutil", "reset", "Accessibility", "com.example.xod"]
        for bad in ("", "foo bar", "a/b", "com.example; rm -rf /", None):
            try:
                P.reset_command(bad)
                raise AssertionError(f"reset_command accepted {bad!r}")
            except ValueError:
                pass
        print("PASS: the reset command is fixed-shape and refuses unrecognized bundle ids", flush=True)

        # reset refuses, and never runs anything, when the bundle id is unknown
        runner = _FakeRunner()
        P.own_bundle_id = lambda: None
        out = P.reset_own_grant(runner=runner)
        assert out["ok"] is False and runner.calls == [], out
        print("PASS: reset refuses without touching tccutil when the bundle id is unknown", flush=True)

        # reset with a known id: exactly one tccutil call for our own id, then re-ask
        P.own_bundle_id = lambda: "com.example.xod"
        P._backend = _FakeAX(False, prompt_value=False)
        runner = _FakeRunner()
        out = P.reset_own_grant(runner=runner)
        assert runner.calls == [["tccutil", "reset", "Accessibility", "com.example.xod"]], runner.calls
        assert out["ok"] is True and out["trusted"] is False and P._backend.prompt_calls == 1, out
        print("PASS: reset runs tccutil once for Xod's own id, then re-asks with the prompt", flush=True)

        # a failed tccutil is reported, not hidden
        runner = _FakeRunner(returncode=1, stderr="tccutil: no such bundle")
        out = P.reset_own_grant(runner=runner)
        assert out["ok"] is False and "refused" in out["message"], out
        print("PASS: a failed reset is surfaced with macOS's own words", flush=True)

        # open-settings builds exactly the documented deep link
        runner = _FakeRunner()
        out = P.open_accessibility_settings(runner=runner)
        assert runner.calls == [["open", P.SETTINGS_URL]] and out["ok"] is True, runner.calls
        print("PASS: open-settings opens the Accessibility pane deep link", flush=True)

        # Partner v1: resetting the PARTNER's grant targets dev.xod.partner exactly,
        # never Xod's own id, and never re-asks from this process (the prompt would
        # be about Xod, the wrong app)
        runner = _FakeRunner()
        P._backend.prompt_calls = 0
        out = P.reset_partner_grant(runner=runner)
        assert runner.calls == [["tccutil", "reset", "Accessibility", "dev.xod.partner"]], runner.calls
        assert out["ok"] is True and P._backend.prompt_calls == 0, out
        print("PASS: the partner reset targets dev.xod.partner once and never re-asks here", flush=True)

        _seam_trusted_with_prompt()
    finally:
        P._backend, P.own_bundle_id = real_backend, real_bid_fn


if __name__ == "__main__":
    main()
