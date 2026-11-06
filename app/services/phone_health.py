"""Phone health classification and recovery ladder decision logic.

Ported 2026-11-06 from the VM-side Poco healer daemon
(social/poco-phone/healer/poco_healer.py, 437 lines) and its plan
(social/poco-phone/SELF-HEALER-V2-PLAN.md).

Only the LOGIC is ported here: probe-output parsing, failure
classification, and the capped laddered recovery decision. The VM
daemon's SSH and adb transport is deliberately NOT a dependency of
this module. A caller that already owns a transport (adb, mesh, or
an on-device agent) builds a snapshot dict, or feeds raw batched
probe text to parse_probe_output, and gets back a classification
plus the next ladder step. Nothing in this module touches a device,
a network, a file, or a clock unless the caller passes the time in.

Snapshot dict shape (all keys optional except reachable):
    reachable     bool   transport to the phone or its host worked
    device_state  str    adb get-state value, "device" when attached
    wifi_on       str    "1" or "0" (an int 1 or 0 is also accepted)
    ip            str    wlan0 IPv4 address, or None
    ping_ip_ok    bool   ping by IP (for example 8.8.8.8) succeeded
    ping_dns_ok   bool   ping by name (for example dns.google) worked
    captive       bool   HTTP probe was redirected (plan class D)
    http_status   int    optional HTTP probe status code
    fg_app        str    foreground package name, or None
    awake         bool   screen is awake
    lane_busy     bool   a foreground lane app is driving the phone
    lane_apps     tuple  package names that count as a live lane

Classification states and classes (names match the source daemon,
with the plan letters kept):
    ok / None                 healthy
    razer_unreachable / None  transport failed (state only)
    adb_absent / None         host reachable, phone not in adb
    down / wifi_off           radio itself is off
    down / A_disconnected     no wlan0 IP, supplicant not COMPLETED
    down / B_no_internet      has IP, ping by IP failing
    down / C_dns              IP reachable, DNS resolution failing
    down / D_captive          HTTP probe redirected, captive or blocked
    down / unreachable        alias class used by callers that only
                              know the phone did not answer at all
    down / unknown            unclassified probe pattern

Ladder rules (identical caps to the source daemon):
    - Ladder passes are due at episode ages LADDER_AGES seconds,
      then once per LADDER_TAIL_INTERVAL seconds.
    - Pass 0 is a non-radio nudge. Pass 2 is an airplane cycle for
      classes A, B, wifi_off and unknown (subject to the airplane
      minimum gap, otherwise it falls back to a wifi toggle). Every
      other pass is a wifi toggle.
    - At most MAX_RADIO_PER_HOUR radio actions per rolling hour and
      at most MAX_RADIO_PER_EPISODE radio actions per episode.
    - A radio step is deferred while a lane app is foreground and
      the screen is awake, for up to LANE_DEFER_MAX seconds of
      episode age. Past that the focus is treated as stale.
    - wifi_off exception: if the radio itself is off, the next step
      is force_wifi_on immediately, on every probe. It bypasses the
      ladder schedule, the radio caps, and the lane defer rule, and
      it is not counted as a radio action.

Never actions: the ladder can never emit forget_network,
change_dns, install, wipe, root, or kill_adb_server. ALLOWED_STEPS
is the complete allowlist and is_never_action guards it.
"""
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MAX_RADIO_PER_HOUR = 4
MAX_RADIO_PER_EPISODE = 10
AIRPLANE_MIN_GAP = 1800
LANE_DEFER_MAX = 1800
LADDER_AGES = [0, 180, 480, 1080, 1980, 3780]
LADDER_TAIL_INTERVAL = 3600

PROBE_INTERVAL_OK = 60
PROBE_INTERVAL_DOWN = 30
PROBE_INTERVAL_UNREACHABLE = 120

DEFAULT_LANE_APPS = ("com.twitter.android",)

ALLOWED_STEPS = frozenset({
    "none",
    "probe_only",
    "nudge",
    "force_wifi_on",
    "toggle",
    "airplane",
})

NEVER_ACTIONS = frozenset({
    "forget_network",
    "forget-network",
    "change_dns",
    "change-dns",
    "install",
    "wipe",
    "root",
    "kill_adb_server",
    "kill-adb-server",
})

# Classes for which ladder pass 2 escalates to an airplane cycle.
AIRPLANE_CLASSES = frozenset({"A_disconnected", "B_no_internet", "wifi_off", "unknown"})


def is_never_action(step: Optional[str]) -> bool:
    """True when a step name is on the never list."""
    return bool(step) and step in NEVER_ACTIONS


def is_allowed_step(step: Optional[str]) -> bool:
    """True only for steps the ladder is allowed to emit."""
    return bool(step) and step in ALLOWED_STEPS and not is_never_action(step)


def _wifi_on_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if text:
        return text[-1:]
    # An explicitly empty read-back matches the source daemon, where
    # anything other than "1" means the radio is treated as off.
    return "" if value == "" else None


def normalize_snapshot(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a snapshot dict with the derived fields filled in."""
    snap = dict(snapshot or {})
    # A snapshot built on-device has no transport leg to fail.
    snap["reachable"] = bool(snap.get("reachable", True))
    snap["wifi_on"] = _wifi_on_str(snap.get("wifi_on"))
    snap.setdefault("ip", None)
    snap["ping_ip_ok"] = bool(snap.get("ping_ip_ok"))
    snap["ping_dns_ok"] = bool(snap.get("ping_dns_ok"))
    snap["awake"] = bool(snap.get("awake"))
    if "lane_busy" not in snap:
        lane_apps = tuple(snap.get("lane_apps") or DEFAULT_LANE_APPS)
        snap["lane_busy"] = bool(snap.get("fg_app") in lane_apps and snap["awake"])
    snap["lane_busy"] = bool(snap.get("lane_busy"))
    return snap


def parse_probe_output(text: str) -> Dict[str, Any]:
    """Parse the source daemon's batched probe text into a snapshot.

    The batch format is sections headed by @@NAME@@ lines, produced
    by one adb call per section (get-state, wifi_on setting, wlan0
    address, ping by IP, ping by name, window focus, wakefulness).
    Pure text in, snapshot dict out. No transport happens here.
    """
    sections: Dict[str, List[str]] = {}
    current = None
    for line in (text or "").splitlines():
        match = re.match(r"@@(\w+)@@", line.strip())
        if match:
            current = match.group(1)
            sections[current] = []
        elif current:
            sections[current].append(line)
    sec = {key: "\n".join(value) for key, value in sections.items()}

    getstate = sec.get("GETSTATE", "").strip()
    ip_match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", sec.get("IPADDR", ""))
    focus_match = re.search(r"u0 ([a-zA-Z0-9_.]+)/", sec.get("FOCUS", ""))
    wifi_text = sec.get("WIFION", "").strip()
    snap = {
        "reachable": True,
        "device_state": getstate.splitlines()[-1].strip() if getstate else "",
        "wifi_on": wifi_text[-1:] if wifi_text else "",
        "ip": ip_match.group(1) if ip_match else None,
        "ping_ip_ok": " 0% packet loss" in sec.get("PINGIP", ""),
        "ping_dns_ok": " 0% packet loss" in sec.get("PINGDNS", ""),
        "fg_app": focus_match.group(1) if focus_match else None,
        "awake": "mWakefulness=Awake" in sec.get("WAKE", ""),
    }
    return normalize_snapshot(snap)


def classify(snapshot: Optional[Dict[str, Any]]) -> Tuple[str, Optional[str], str]:
    """Classify a snapshot. Returns (state, failure class, detail)."""
    snap = normalize_snapshot(snapshot)
    if not snap.get("reachable"):
        return "razer_unreachable", "unreachable", "transport to phone or host failed"
    device_state = snap.get("device_state")
    # On-device snapshots carry no adb leg; only judge it when present.
    if device_state is not None and device_state != "device":
        return "adb_absent", None, f"adb get-state: {device_state!r}"
    if snap.get("wifi_on") is not None and snap.get("wifi_on") != "1":
        return "down", "wifi_off", "wifi_on != 1"
    if snap.get("captive"):
        return "down", "D_captive", "HTTP probe redirected (captive portal or blocked)"
    if snap.get("ip") and snap.get("ping_ip_ok") and snap.get("ping_dns_ok"):
        return "ok", None, f"IP {snap['ip']}, ping IP+DNS ok"
    if not snap.get("ip"):
        return "down", "A_disconnected", "no wlan0 IP (supplicant not COMPLETED)"
    if snap.get("ip") and snap.get("ping_ip_ok") and not snap.get("ping_dns_ok"):
        return "down", "C_dns", "IP reachable, DNS resolution failing"
    if snap.get("ip") and not snap.get("ping_ip_ok"):
        return "down", "B_no_internet", "has IP, gateway/internet ping failing"
    return "down", "unknown", "unclassified probe pattern"


def classify_snapshot(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Dict form of classify, in the health status.json shape."""
    state, failure_class, detail = classify(snapshot)
    snap = normalize_snapshot(snapshot)
    return {
        "state": state,
        "class": failure_class,
        "detail": detail,
        "healthy": state == "ok",
        "ip": snap.get("ip"),
        "wifi_on": snap.get("wifi_on"),
        "ping_ip_ok": snap.get("ping_ip_ok"),
        "ping_dns_ok": snap.get("ping_dns_ok"),
        "fg_app": snap.get("fg_app"),
        "awake": snap.get("awake"),
        "lane_busy": snap.get("lane_busy", False),
    }


def recent_radio_count(radio_epochs: Optional[Iterable[float]], now: float,
                       within: float = 3600) -> int:
    """Count radio actions inside the rolling window ending at now."""
    return sum(1 for stamp in (radio_epochs or []) if now - stamp < within)


def ladder_due(ladder_passes: int, episode_age: float,
               last_pass_epoch: Optional[float] = None, now: Optional[float] = None) -> bool:
    """True when the next ladder pass is due at this episode age."""
    if ladder_passes < len(LADDER_AGES):
        return episode_age >= LADDER_AGES[ladder_passes]
    if last_pass_epoch is None or now is None:
        return False
    return now - last_pass_epoch >= LADDER_TAIL_INTERVAL


def next_probe_interval(state: str) -> int:
    """Seconds to wait before the next probe, by classified state."""
    if state == "ok":
        return PROBE_INTERVAL_OK
    if state in ("razer_unreachable", "adb_absent"):
        return PROBE_INTERVAL_UNREACHABLE
    return PROBE_INTERVAL_DOWN


def decide_next_step(snapshot: Optional[Dict[str, Any]],
                     ladder_passes: int = 0,
                     episode_age: float = 0,
                     radio_epochs: Optional[Sequence[float]] = None,
                     episode_radio: int = 0,
                     last_pass_epoch: Optional[float] = None,
                     last_airplane_epoch: Optional[float] = None,
                     now: Optional[float] = None) -> Dict[str, Any]:
    """Decide the next ladder step for a snapshot. Pure decision only.

    The caller owns executing the step and owns recording it. The
    returned dict always names a step in ALLOWED_STEPS, says whether
    it is a radio action, and explains the decision in plain text.
    """
    snap = normalize_snapshot(snapshot)
    state, failure_class, detail = classify(snap)
    radio_used = recent_radio_count(radio_epochs, now) if now is not None else len(list(radio_epochs or []))

    def decision(step: str, reason: str, is_radio: bool = False, **extra: Any) -> Dict[str, Any]:
        assert is_allowed_step(step), f"ladder tried to emit a non-allowed step: {step}"
        out = {
            "step": step,
            "state": state,
            "class": failure_class,
            "detail": detail,
            "reason": reason,
            "is_radio": is_radio,
            "deferred": False,
            "capped": False,
            "bypasses_caps": False,
            "ladder_pass": ladder_passes + 1,
            "radio_last_hour": radio_used,
            "episode_radio": episode_radio,
            "next_probe_interval": next_probe_interval(state),
        }
        out.update(extra)
        return out

    if state == "ok":
        return decision("none", "healthy, no action")
    if state in ("razer_unreachable", "adb_absent"):
        return decision("probe_only", f"{state}: probe only until the hardware path returns")

    if failure_class == "wifi_off":
        return decision(
            "force_wifi_on",
            "wifi_off exception: radio is off, force it on immediately, "
            "bypassing the ladder schedule, the radio caps, and the lane defer rule",
            is_radio=False,
            bypasses_caps=True,
        )

    if not ladder_due(ladder_passes, episode_age, last_pass_epoch, now):
        return decision("probe_only", "no ladder pass due at this episode age yet")

    if ladder_passes == 0:
        step, is_radio = "nudge", False
    elif ladder_passes == 2 and failure_class in AIRPLANE_CLASSES:
        step, is_radio = "airplane", True
    else:
        step, is_radio = "toggle", True

    if is_radio and snap.get("lane_busy") and episode_age < LANE_DEFER_MAX:
        return decision(
            "probe_only",
            f"lane defer: foreground lane app {snap.get('fg_app')} is awake, "
            f"radio steps defer for up to {LANE_DEFER_MAX}s of episode age",
            deferred=True,
        )

    if is_radio and (radio_used >= MAX_RADIO_PER_HOUR or episode_radio >= MAX_RADIO_PER_EPISODE):
        return decision(
            "probe_only",
            f"ladder capped: {radio_used} radio actions in the last hour "
            f"(cap {MAX_RADIO_PER_HOUR}), {episode_radio} this episode "
            f"(cap {MAX_RADIO_PER_EPISODE})",
            capped=True,
        )

    if step == "airplane" and now is not None and last_airplane_epoch is not None:
        if now - last_airplane_epoch < AIRPLANE_MIN_GAP:
            step = "toggle"
            return decision(step, "airplane minimum gap not met, falling back to wifi toggle",
                            is_radio=True)

    return decision(step, f"ladder pass {ladder_passes + 1} for class {failure_class}",
                    is_radio=is_radio)


def build_status(snapshot: Optional[Dict[str, Any]],
                 ladder_passes: int = 0,
                 radio_epochs: Optional[Sequence[float]] = None,
                 episode_started: Optional[str] = None,
                 down_for_sec: int = 0,
                 now: Optional[float] = None) -> Dict[str, Any]:
    """Build a status dict in the poco-phone health/status.json shape."""
    info = classify_snapshot(snapshot)
    radio_used = recent_radio_count(radio_epochs, now) if now is not None else len(list(radio_epochs or []))
    return {
        "state": info["state"],
        "class": info["class"],
        "detail": info["detail"],
        "ip": info["ip"],
        "wifi_on": info["wifi_on"],
        "ping_ip_ok": info["ping_ip_ok"],
        "ping_dns_ok": info["ping_dns_ok"],
        "fg_app": info["fg_app"],
        "awake": info["awake"],
        "lane_busy": info["lane_busy"],
        "episode_started_pt": episode_started,
        "down_for_sec": int(down_for_sec),
        "ladder_passes": ladder_passes,
        "radio_last_hour": radio_used,
    }
