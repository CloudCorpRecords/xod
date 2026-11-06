"""
Phone health classification and ladder decisions (services/phone_health.py),
ported from the VM Poco healer daemon, with no device, SSH, or adb touched:
  - every failure class is read from a fixture snapshot: healthy, wifi_off,
    A disconnected, B no internet, C DNS broken, D captive, unreachable,
    adb absent, and unknown
  - the batched probe text format of the source daemon parses into the
    same snapshot the classifier expects
  - the ladder: pass 0 nudges, pass 2 flies airplane mode for A and B,
    the rest toggle wifi; passes are only due at the ladder ages, then hourly
  - caps bite: at most 4 radio actions per rolling hour, 10 per episode
  - wifi_off is the exception: forced on at once, caps and defer ignored
  - a live lane defers radio steps, and only for up to 1800s of episode age
  - the ladder can never emit a never action (forget network, change DNS,
    install, wipe, root, kill the shared adb server)
  .venv/bin/python tests/smoke/run_phone_health_test.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-phone-health-"))
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


NOW = 1_800_000_000.0

HEALTHY = {"reachable": True, "device_state": "device", "wifi_on": "1",
           "ip": "10.0.0.233", "ping_ip_ok": True, "ping_dns_ok": True,
           "fg_app": None, "awake": True}
WIFI_OFF = dict(HEALTHY, wifi_on="0", ip=None, ping_ip_ok=False, ping_dns_ok=False)
CLASS_A = dict(HEALTHY, ip=None, ping_ip_ok=False, ping_dns_ok=False)
CLASS_B = dict(HEALTHY, ping_ip_ok=False, ping_dns_ok=False)
CLASS_C = dict(HEALTHY, ping_dns_ok=False)
CLASS_D = dict(HEALTHY, captive=True)

PROBE_TEXT = "\n".join([
    "@@GETSTATE@@", "device",
    "@@WIFION@@", "1",
    "@@IPADDR@@", "3: wlan0: <BROADCAST> mtu 1500",
    "    inet 10.0.0.233/24 brd 10.0.0.255 scope global wlan0",
    "@@PINGIP@@", "2 packets transmitted, 2 received, 0% packet loss, time 1001ms",
    "@@PINGDNS@@", "1 packets transmitted, 1 received, 0% packet loss, time 0ms",
    "@@FOCUS@@", "  mCurrentFocus=Window{abc u0 com.twitter.android/com.twitter.android.MainActivity}",
    "@@WAKE@@", "  mWakefulness=Awake",
])


def main():
    from app.services import phone_health as ph

    print("H1 classification from fixture snapshots")
    state, cls, _ = ph.classify(HEALTHY)
    check("healthy snapshot is ok with no class", state == "ok" and cls is None, f"{state} {cls}")
    state, cls, _ = ph.classify(WIFI_OFF)
    check("radio off is down / wifi_off", state == "down" and cls == "wifi_off", f"{state} {cls}")
    state, cls, _ = ph.classify(CLASS_A)
    check("no wlan0 IP is A_disconnected", state == "down" and cls == "A_disconnected", f"{state} {cls}")
    state, cls, _ = ph.classify(CLASS_B)
    check("IP but ping by IP failing is B_no_internet", state == "down" and cls == "B_no_internet", f"{state} {cls}")
    state, cls, _ = ph.classify(CLASS_C)
    check("IP ok, DNS failing is C_dns", state == "down" and cls == "C_dns", f"{state} {cls}")
    state, cls, _ = ph.classify(CLASS_D)
    check("redirected HTTP probe is D_captive", state == "down" and cls == "D_captive", f"{state} {cls}")
    state, cls, _ = ph.classify({"reachable": False})
    check("unreachable transport is its own state", state == "razer_unreachable" and cls == "unreachable", f"{state} {cls}")
    state, cls, _ = ph.classify(dict(HEALTHY, device_state="offline"))
    check("host up, phone not in adb is adb_absent", state == "adb_absent" and cls is None, f"{state} {cls}")
    state, cls, _ = ph.classify(dict(HEALTHY, ping_ip_ok=True, ping_dns_ok=True, ip=None, wifi_on="1"))
    check("no IP wins over good pings (still A)", cls == "A_disconnected", f"{state} {cls}")
    info = ph.classify_snapshot(HEALTHY)
    check("dict form carries the status.json shape", info["healthy"] is True and info["ip"] == "10.0.0.233"
          and info["wifi_on"] == "1" and info["lane_busy"] is False, str(info))
    status = ph.build_status(CLASS_A, ladder_passes=2, radio_epochs=[NOW - 10], now=NOW, down_for_sec=480)
    check("build_status matches the healer status shape", status["class"] == "A_disconnected"
          and status["ladder_passes"] == 2 and status["radio_last_hour"] == 1 and status["down_for_sec"] == 480, str(status))

    print("H2 batched probe text parsing")
    snap = ph.parse_probe_output(PROBE_TEXT)
    check("probe text parses to the healthy snapshot", snap["device_state"] == "device" and snap["wifi_on"] == "1"
          and snap["ip"] == "10.0.0.233" and snap["ping_ip_ok"] and snap["ping_dns_ok"], str(snap))
    check("foreground app and wakefulness are read", snap["fg_app"] == "com.twitter.android" and snap["awake"] is True, str(snap))
    check("a foreground X lane with the screen awake is lane_busy", snap["lane_busy"] is True, str(snap))
    state, cls, _ = ph.classify(snap)
    check("the parsed snapshot classifies healthy", state == "ok" and cls is None, f"{state} {cls}")
    down_text = PROBE_TEXT.replace("inet 10.0.0.233/24", "inet6 fe80::1/64")
    state, cls, _ = ph.classify(ph.parse_probe_output(down_text))
    check("probe text with no IPv4 address classifies A", cls == "A_disconnected", f"{state} {cls}")

    print("H3 ladder shape and schedule")
    check("ladder ages match the source daemon", ph.LADDER_AGES == [0, 180, 480, 1080, 1980, 3780], str(ph.LADDER_AGES))
    check("pass 0 is due at age 0", ph.ladder_due(0, 0) is True)
    check("pass 1 is not due before 180s", ph.ladder_due(1, 179) is False and ph.ladder_due(1, 180) is True)
    check("after the ladder, passes are hourly", ph.ladder_due(6, 99999, last_pass_epoch=NOW - 3599, now=NOW) is False
          and ph.ladder_due(6, 99999, last_pass_epoch=NOW - 3600, now=NOW) is True)
    d = ph.decide_next_step(CLASS_A, ladder_passes=0, episode_age=0, now=NOW)
    check("pass 0 nudges and is not a radio action", d["step"] == "nudge" and d["is_radio"] is False, str(d))
    d = ph.decide_next_step(CLASS_A, ladder_passes=1, episode_age=180, now=NOW)
    check("pass 1 toggles wifi", d["step"] == "toggle" and d["is_radio"] is True, str(d))
    d = ph.decide_next_step(CLASS_A, ladder_passes=2, episode_age=480, now=NOW)
    check("pass 2 for class A flies airplane mode", d["step"] == "airplane" and d["is_radio"] is True, str(d))
    d = ph.decide_next_step(CLASS_C, ladder_passes=2, episode_age=480, now=NOW)
    check("pass 2 for class C toggles instead (no airplane)", d["step"] == "toggle", str(d))
    d = ph.decide_next_step(CLASS_A, ladder_passes=2, episode_age=480, now=NOW, last_airplane_epoch=NOW - 60)
    check("airplane inside its 1800s minimum gap falls back to toggle", d["step"] == "toggle", str(d))
    d = ph.decide_next_step(CLASS_A, ladder_passes=1, episode_age=60, now=NOW)
    check("no pass due yet means probe only", d["step"] == "probe_only" and d["deferred"] is False and d["capped"] is False, str(d))
    d = ph.decide_next_step(HEALTHY, now=NOW)
    check("healthy means no step", d["step"] == "none", str(d))
    d = ph.decide_next_step({"reachable": False}, now=NOW)
    check("unreachable means probe only", d["step"] == "probe_only", str(d))

    print("H4 caps bite")
    four_recent = [NOW - 10, NOW - 20, NOW - 30, NOW - 40]
    check("rolling hour count only counts the last hour", ph.recent_radio_count(four_recent + [NOW - 7200], NOW) == 4)
    d = ph.decide_next_step(CLASS_A, ladder_passes=1, episode_age=180, radio_epochs=four_recent, now=NOW)
    check("4 radio actions in the hour caps the next radio step", d["step"] == "probe_only" and d["capped"] is True, str(d))
    d = ph.decide_next_step(CLASS_A, ladder_passes=1, episode_age=180, episode_radio=10, now=NOW)
    check("10 radio actions in the episode caps the next radio step", d["step"] == "probe_only" and d["capped"] is True, str(d))
    d = ph.decide_next_step(CLASS_A, ladder_passes=0, episode_age=0, radio_epochs=four_recent, now=NOW)
    check("caps never block the non-radio nudge", d["step"] == "nudge", str(d))

    print("H5 wifi_off exception and lane defer")
    d = ph.decide_next_step(WIFI_OFF, ladder_passes=0, episode_age=0, radio_epochs=four_recent, episode_radio=10, now=NOW)
    check("wifi_off forces the radio on at once, caps ignored", d["step"] == "force_wifi_on" and d["bypasses_caps"] is True
          and d["is_radio"] is False, str(d))
    busy_a = dict(CLASS_A, fg_app="com.twitter.android", awake=True, lane_busy=True)
    d = ph.decide_next_step(busy_a, ladder_passes=1, episode_age=180, now=NOW)
    check("a live lane defers a radio step", d["step"] == "probe_only" and d["deferred"] is True, str(d))
    d = ph.decide_next_step(busy_a, ladder_passes=1, episode_age=1800, now=NOW)
    check("the defer ends at 1800s of episode age", d["step"] == "toggle" and d["deferred"] is False, str(d))
    d = ph.decide_next_step(WIFI_OFF, ladder_passes=0, episode_age=0, now=NOW)
    check("wifi_off is never deferred by a lane either", d["step"] == "force_wifi_on", str(d))

    print("H6 never actions can never be emitted")
    never = ["forget_network", "change_dns", "install", "wipe", "root", "kill_adb_server"]
    check("every never action is recognised", all(ph.is_never_action(n) for n in never))
    check("no never action is an allowed step", all(not ph.is_allowed_step(n) for n in never))
    emitted = set()
    for snap in (HEALTHY, WIFI_OFF, CLASS_A, CLASS_B, CLASS_C, CLASS_D, {"reachable": False}):
        for passes in range(0, 9):
            for age in (0, 180, 480, 1080, 1980, 3780, 7200):
                emitted.add(ph.decide_next_step(snap, ladder_passes=passes, episode_age=age, now=NOW)["step"])
    check("a sweep of classes, passes and ages only emits allowed steps",
          emitted <= ph.ALLOWED_STEPS and not (emitted & ph.NEVER_ACTIONS), str(sorted(emitted)))
    check("probe intervals match the source daemon", ph.next_probe_interval("ok") == 60
          and ph.next_probe_interval("down") == 30 and ph.next_probe_interval("razer_unreachable") == 120)


if __name__ == "__main__":
    main()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)
