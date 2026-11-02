"""
AppleScript permission guard (Fix Round 3 rider): before computer_run_applescript fires a script that
tells a known app, the existing no-prompt automation check (services/abilities.py automation(), the
AEDeterminePermissionToAutomateTarget lookup) runs first, in app/agent/tools.py execute_tool, after the
placeholder guard and before the bounded, reaped osascript spawn in system_control.

Sealed the way the Mac abilities suite seals its seams: abilities.automation is replaced with a stub
(the seam run_mac_abilities_test.py already uses), system_control_service.run_applescript is replaced
with a recorder, and abilities._open is replaced, so no real prompt, no real osascript, no System
Settings page and no real Keychain is ever touched. Storage is a temp XOD_DATA_DIR with the file
keyring, like the other suites.

Cases: (a) granted target: the script executes, result unchanged; (b) ask state: the script does NOT
execute, the tool returns the named graceful result, and the Allow surface is surfaced exactly once;
(c) denied, and a remembered dismissal: a second targeted call does not surface again and does not
execute; (d) after a remembered hold, a granted pre-check lets the next call execute; (e) an
untargeted script, and one telling an unknown app, execute exactly as before; (f) Music is a covered
target on the Abilities surface, its status readable without prompting, and a held-back target
surfaces there with the same Allow flow.

  PYTHON_KEYRING_BACKEND=tests.support.filekeyring.FileKeyring .venv/bin/python tests/smoke/run_applescript_permguard_test.py   (Mac only)
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-permguard-"))
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


async def main():
    if sys.platform != "darwin":
        print("Mac only")
        return
    from app.agent import tools
    from app.services import abilities as ab

    # ---- the seams: no prompt, no real script, no Settings page
    states = {}          # bundle id -> automation state the stub reports
    looked_up = []

    def fake_automation(bundle):
        looked_up.append(bundle)
        return states.get(bundle, "unknown")
    ab.automation = fake_automation
    opened = []
    ab._open = lambda url: opened.append(url)

    ran = []

    async def fake_run_applescript(script):
        ran.append(script)
        return {"success": True, "exit_code": 0, "output": "ok", "error": None, "script": script}
    tools.system_control_service.run_applescript = fake_run_applescript

    MUSIC = 'tell application "Music" to play'
    SAFARI = 'tell application "Safari" to count windows'
    NOTES = 'tell application "Notes" to count folders'
    FINDER = 'tell application "Finder" to get name of every disk'

    async def call(script):
        return await tools.execute_tool("computer_run_applescript", {"script": script}, reviewed=True)

    print("P1 granted target runs unchanged")
    states["com.apple.Music"] = "allowed"
    r = await call(MUSIC)
    check("granted: the script executed", ran == [MUSIC], str(ran))
    check("granted: the result is the script's own result, unchanged",
          r.get("success") is True and r.get("output") == "ok" and r.get("error_code") is None, str(r)[:200])
    check("granted: the pre-check looked Music up by its bundle id", looked_up == ["com.apple.Music"], str(looked_up))

    print("P2 ask state: held back, named result, surfaced exactly once")
    states["com.apple.Safari"] = "ask"
    n = len(ran)
    r = await call(SAFARI)
    check("ask: the script did NOT execute", len(ran) == n, str(ran))
    check("ask: the named graceful result (not a prompt, not a raw failure)",
          r.get("success") is False and r.get("error_code") == "applescript_permission_not_granted"
          and "AppleScriptPermissionNotGranted" in str(r.get("error") or "") and "Safari" in str(r.get("error") or "")
          and "was not run" in str(r.get("error") or ""), str(r)[:300])
    check("ask: surfaced this once", r.get("surfaced") is True and r.get("state") == "ask", str(r)[:200])
    r = await call(SAFARI)
    check("ask again: still not executed, and NOT surfaced a second time",
          len(ran) == n and r.get("surfaced") is False, str(r)[:200])
    check("ask: nothing opened System Settings and no prompt was fired from the task",
          not opened, str(opened))

    print("P3 denied, remembered: no re-surface, no execution")
    states["com.apple.Notes"] = "denied"
    r = await call(NOTES)
    check("denied: the script did NOT execute, named result, surfaced once",
          len(ran) == n and r.get("error_code") == "applescript_permission_not_granted"
          and r.get("state") == "denied" and r.get("surfaced") is True, str(r)[:200])
    r = await call(NOTES)
    check("denied again (the dismissal is remembered): no second surface, still no execution",
          len(ran) == n and r.get("surfaced") is False, str(r)[:200])
    guard_mem = getattr(ab, "_guard_remembered", lambda: {})()
    check("the holds are remembered per target, persisted the abilities way",
          guard_mem.get("safari") == "ask" and guard_mem.get("notes") == "denied",
          str(guard_mem))

    print("P4 a later grant clears the way (the pre-check runs every time)")
    states["com.apple.finder"] = "denied"
    r = await call(FINDER)
    check("held while denied", len(ran) == n and r.get("surfaced") is True, str(r)[:200])
    states["com.apple.finder"] = "allowed"
    r = await call(FINDER)
    check("granted on the next pre-check: the script executes despite the remembered hold",
          len(ran) == n + 1 and ran[-1] == FINDER and r.get("success") is True, str(r)[:200])

    print("P5 untargeted and unknown-target scripts run exactly as before")
    before = len(looked_up)
    r = await call("set volume output volume 50")
    check("no tell target: executed, and no automation lookup was even made",
          r.get("success") is True and ran[-1] == "set volume output volume 50" and len(looked_up) == before, str(r)[:200])
    r = await call('tell application "Obscure Player" to play')
    check("an app Xod has no bundle id for: executed as before (no check is possible for it)",
          r.get("success") is True and ran[-1] == 'tell application "Obscure Player" to play', str(r)[:200])
    r = await call('tell app "Music" to pause')
    check("the tell app short form is still a Music target (held while ask, here granted so it runs)",
          r.get("success") is True, str(r)[:200])
    states["com.apple.Music"] = "ask"
    r = await call('tell application id "com.apple.Music" to play')
    check("the tell application id (bundle id) form is a Music target too: held while ask",
          r.get("error_code") == "applescript_permission_not_granted" and r.get("target_id") == "music", str(r)[:200])

    print("P6 Music (and held-back targets) on the Abilities surface")
    ab.messages_readable = lambda: True
    ab.location_status = lambda: "ask"
    ab.shortcut_names = lambda: []
    ab._connector_on = lambda: True
    st = {a["id"]: a for a in ab.status()["abilities"]}
    check("Music is listed, its status read from the no-prompt check (ask here)",
          "music" in st and st["music"]["state"] == "ask", str(list(st)))
    check("the held-back Safari and Notes targets surfaced on the same screen, once each",
          st.get("safari", {}).get("state") == "ask" and st.get("notes", {}).get("state") == "denied"
          and "held back" in st.get("safari", {}).get("note", ""), str({k: v.get("state") for k, v in st.items()}))
    check("reading status opened nothing and prompted nothing", not opened, str(opened))

    class Run:
        def __init__(self, code, err=""):
            self.returncode, self.stderr, self.stdout = code, err, "3"

    class FakeSub:
        probed = []

        @staticmethod
        def run(argv, **kw):
            FakeSub.probed.append(argv)
            return Run(0)
        Popen = None
    ab.subprocess = FakeSub
    r = await ab.allow("music")
    last_probe = FakeSub.probed[-1][-1] if FakeSub.probed else ""
    check("Music Allow: the same one-time Allow flow (a harmless probe, remembered as allowed)",
          r.get("success") is True and last_probe == 'tell application "Music" to count playlists'
          and ab._remembered().get("music") == "allowed", str(r)[:200] + str(FakeSub.probed[-1:]))
    r = await ab.allow("safari")
    last_probe = FakeSub.probed[-1][-1] if FakeSub.probed else ""
    check("a surfaced extra target (Safari) Allows through the same flow, no second surface built",
          r.get("success") is True and last_probe == 'tell application "Safari" to count windows', str(r)[:200])


if __name__ == "__main__":
    asyncio.run(main())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)
