"""
The backend's side of the screen capture bridge.

Screen capture on a Mac belongs to the Xod app's GUI main process: it is the
identity macOS credits with the app's Screen Recording grant. This backend is
a child the GUI spawns, and when it captured on its own (PIL ImageGrab,
/usr/sbin/screencapture) macOS attributed the capture to the child's identity,
which holds no grant, so the app read "denied" while its own Settings switch
stood on (AGE-98, the capture half of the AGE-57 family).

The GUI serves captures from electron/capture_bridge.js over a loopback-only
HTTP endpoint and leaves the coordinates in DATA_DIR/capture_bridge.json, the
same pattern as its trust verdicts (ax_trust.json, screen_trust.json). This
module reads that file and asks the GUI. Every failure raises
CaptureBridgeError with the reason, so a missing bridge is a clear sentence,
never a silent black frame.
"""
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

BRIDGE_FILE = "capture_bridge.json"
TIMEOUT = 20.0


class CaptureBridgeError(Exception):
    """The GUI capture bridge could not give us the screen, and says why."""


def _record() -> Optional[Dict[str, Any]]:
    from app.paths import DATA_DIR
    try:
        rec = json.loads((Path(DATA_DIR) / BRIDGE_FILE).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(rec, dict) or not rec.get("port") or not rec.get("token"):
        return None
    return rec


def available() -> bool:
    return _record() is not None


def _ask(route: str, payload: Dict[str, Any]) -> bytes:
    rec = _record()
    if rec is None:
        raise CaptureBridgeError(
            "the Xod app's capture bridge isn't running (no capture_bridge.json in the data folder). "
            "Screen capture now runs inside the Xod app itself, so macOS credits it to Xod; "
            "a backend running outside the app cannot see the screen.")
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(rec['port'])}{route}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-xod-bridge-token": str(rec["token"])},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8", "replace")).get("error", "")
        except Exception:
            pass
        raise CaptureBridgeError(f"the Xod app's capture bridge answered {e.code}" + (f": {detail}" if detail else ""))
    except Exception as e:
        raise CaptureBridgeError(f"the Xod app's capture bridge didn't answer: {e}")


def capture_screen_png() -> bytes:
    """The main display as PNG bytes, captured by the GUI main process."""
    return _ask("/capture/screen", {})


def capture_window_png(name: str) -> bytes:
    """One window as PNG bytes, captured by the GUI main process."""
    return _ask("/capture/window", {"name": name})
