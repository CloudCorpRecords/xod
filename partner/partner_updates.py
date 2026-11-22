#!/usr/bin/env python3
"""
partner_updates: the Partner's update guardian (v0.5 job: update swapping,
snapshot-and-guardian shape).

Xod's own self-updater (electron/updater.js) owns staging and the quit-time
swap, and this guardian never races or replaces it. What the self-updater
stages on a Mac, read from that code:

- WORK is ~/Library/Caches/Xod/updates. stage() wipes it, downloads the
  release .dmg and SHA256SUMS.txt into it, checks the .dmg against the sums,
  mounts the .dmg and copies Xod.app out of it to WORK/Xod.app, then deletes
  the .dmg. So a staged payload on disk is: WORK/Xod.app (a full app tree
  whose Contents/Info.plist carries CFBundleShortVersionString equal to the
  release version and CFBundleIdentifier dev.xod.desktop) sitting beside
  WORK/SHA256SUMS.txt.
- swap() then writes WORK/install.sh (a small script that waits for Xod's
  pid to exit, moves the installed bundle to Xod.app.previous, moves
  WORK/Xod.app into place, deletes the backup, and optionally reopens the
  app). The staged Xod.app is consumed by that move, so after a swap the
  updates dir holds only install.sh and SHA256SUMS.txt.
- The installed app's version stamp is the same plist key:
  /Applications/Xod.app/Contents/Info.plist, CFBundleShortVersionString
  (the updater reads it with plutil -extract; Electron's app.getVersion()
  reports the same value).

So a version change is observable two ways: the staged payload's plist
version in the updates dir, and the installed bundle's plist version
changing, paired with a new app process start (the pid and start time of
the bundle executable) to prove the new build actually restarted.

The guardian's job around that swap:

1. On a NEW staged payload (an identity not seen before): snapshot the
   currently installed app into the Partner state dir (exactly ONE
   snapshot retained), after a free-space check: the snapshot area must
   hold the incoming copy beside the retained one, so free space must be
   at least 2x the installed app's size plus a fixed margin
   (update_disk_margin_bytes, default 256 MiB). If it cannot fit, refuse
   with a clear log line and snapshot nothing.
2. When the installed version stamp changes AND the app has restarted
   since the snapshot (a new start token), run the health battery against
   the app API, in the proving suite's shapes: the health endpoint, one
   exact-reply chat (POST /api/chat streaming, verbatim compare), one
   file round trip through tasks (POST /api/tasks writes exact bytes,
   read back from disk and compared, the T5 shape), and a read of the
   auto-approve settings (Xod's own trust and the partner block are
   recorded; that check fails only if the endpoint itself fails).
3. On battery failure: roll back. A single failed battery never rolls
   anything back, on ANY path: the battery is re-run once as a
   confirmation, and only a second consecutive failure acts (2026-11-22:
   a healthy 1.1.14 whose health, chat and permission probes all passed
   was rolled back on one failed file-round-trip probe). The
   version-agreement failure takes the same road: it is a first failed
   verdict, and only a confirmation battery that also fails (or an
   agreement re-check that still disagrees) may act on it.
   A battery verdict is three-valued. A check FAILS only on evidence
   about the build itself (wrong chat reply, wrong file bytes, a dead
   health endpoint). The file round-trip probe separates that from the
   task system failing to produce a verdict at all: when the agent task
   ends in a failed or cancelled state, the task API errors, or no
   terminal state arrives in time, the probe is INCONCLUSIVE. Task-end
   failures retry in place with backoff before the probe reports; a
   content failure is conclusive for that battery and its retry is the
   confirmation battery, so a file failure is only counted toward a
   rollback when the retry also fails. A battery with no failed check
   but an inconclusive one is itself inconclusive: the update is
   neither accepted nor rolled back, the pending verdict waits, and
   the battery re-runs on a later tick. (2026-11-22, second incident:
   a healthy 1.1.16 was rolled back to 1.1.14 and blocked because the
   file task ended 'failed' while the backend was under a burst of
   mail-endpoint 429s; the task system was overloaded, which says
   nothing about the build.) Every block record carries the full
   battery history (which battery, its verdict, and the per-probe
   results) plus the guardian build stamp, so a field incident is
   diagnosable from the record alone. And a rollback may only restore the
   snapshot of the install the payload actually replaced: when no fresh
   verified snapshot of that pre-update install exists (the snapshot
   copy failed verification, or was refused, or is held), the snapshot
   on disk is STALE by definition, and restoring it moves the install
   backwards past the replaced version (2026-11-22: the 1.1.14 snapshot
   failed verification, the retained snapshot stayed at 1.1.4 from two
   weeks earlier, and the rollback landed the machine on 1.1.4, a build
   whose own self-repair is known-broken, in an endless repair loop).
   In that case the guardian leaves the new install in place, blocks
   the payload, and writes a guardian report instead. Write the
   planned-shutdown signal first (the v1.1 marker protocol, so the
   revival accounting stays honest), quit the app, restore the snapshot
   over the app path, relaunch, re-run the battery, and write a block
   marker naming the bad payload version, the time, and the failure.
   If the restored build then fails its own post-rollback battery, the
   pre-rollback build (kept aside during the restore) is put back: a
   build that passed most of its battery is a better known state than
   a restored build that cannot start at all. The restore is staged:
   the snapshot is copied aside and verified byte for byte before the
   app path is touched, then swapped in — a copy that dies halfway
   leaves a complete tree in place, never a half-copied husk. And after
   a failed restore the guardian holds snapshots: the suspect installed
   state is never copied over the retained last-good snapshot until a
   battery has passed for the installed version again. If that same version
   stages again, the guardian does NOT overwrite the retained good
   snapshot with the post-bad-update state, still rolls back if the
   swap happens and the battery fails, logs every cycle, and after a
   second rollback of the same version escalates with a prominent
   BLOCKED log entry that also surfaces in status.
4. On battery success: clear the pending state, keep the snapshot until
   the next payload appears, and log the update as accepted with both
   versions.

Everything external is injectable (HTTP is a small internal client, quit
and relaunch and the start token and the free-space probe are callables),
so the tests run the whole guardian against throwaway app copies, a fake
backend, and a fake updates dir. Stdlib only.
"""
import hashlib
import json
import os
import plistlib
import secrets
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

DEFAULT_UPDATES_DIR = Path.home() / "Library" / "Caches" / "Xod" / "updates"
DEFAULT_APP_PATH = Path("/Applications/Xod.app")

# The guardian build stamp. The Partner imports this module once at
# process start, so the file on disk and the code a long-running
# Partner actually executes can differ after a deploy; the stamp is
# exposed in status and written into every block record so a field
# incident names the code that acted, not just the code on disk.
GUARDIAN_BUILD = "2026-11-22-probe-fix-1"

DEFAULTS = {
    "update_updates_dir": str(DEFAULT_UPDATES_DIR),
    "update_app_path": str(DEFAULT_APP_PATH),
    "update_api_base": None,  # derived from probe_url when unset
    "update_disk_margin_bytes": 256 * 1024 * 1024,
    "update_health_timeout_s": 90,
    "update_health_poll_s": 3,
    "update_chat_timeout_s": 120,
    "update_task_timeout_s": 180,
    "update_task_poll_s": 2,
    "update_file_probe_retries": 2,
    "update_file_probe_backoff_s": 2,
    "update_battery_retry_min_s": 120,
    "update_http_timeout_s": 15,
    "update_quit_wait_s": 30,
}


def _iso(ts):
    from datetime import datetime
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _norm_version(v):
    """A version reduced to its numeric core for comparison: a leading v,
    build metadata and a prerelease suffix never make two stamps of the
    same release disagree."""
    if v is None:
        return None
    core = str(v).strip().lstrip("vV").split("+")[0].split("-")[0]
    return core or None


def read_version(app_path):
    """The version stamp of an app tree: CFBundleShortVersionString in
    Contents/Info.plist, the same stamp the self-updater checks. None
    when the tree or the stamp is absent or unreadable."""
    try:
        with open(Path(app_path) / "Contents" / "Info.plist", "rb") as fh:
            data = plistlib.load(fh)
        v = data.get("CFBundleShortVersionString")
        return str(v) if v else None
    except Exception:
        return None


VOLATILE_DIR_NAMES = {"__pycache__"}
VOLATILE_SUFFIXES = (".pyc", ".pyo", ".log")


def _is_volatile(rel):
    """A path whose bytes a running app legitimately rewrites under a
    byte-for-byte copy: bytecode caches and logs inside the bundle.
    Copying a LIVE app tree and then demanding every one of these bytes
    match is what failed the 1.1.14 snapshot verification on 2026-11-22
    (the copy raced the running backend's own writes), which silently
    left a two-week-old snapshot as the rollback target. Volatile paths
    are still copied; copy VERIFICATION compares the load-bearing tree
    and ignores them."""
    parts = str(rel).split("/")
    if any(p in VOLATILE_DIR_NAMES for p in parts):
        return True
    return str(rel).endswith(VOLATILE_SUFFIXES)


def tree_manifest(root, ignore_volatile=False):
    """{relative path: (kind, size, sha256 or link target)} for a tree,
    used to verify copies byte for byte. Symlinks are recorded, not
    followed. With ignore_volatile, paths a running app rewrites on its
    own (see _is_volatile) are left out of the comparison."""
    root = Path(root)
    out = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if ignore_volatile and _is_volatile(rel):
            continue
        if path.is_symlink():
            out[rel] = ("link", 0, str(path.readlink()))
        elif path.is_file():
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
            out[rel] = ("file", path.stat().st_size, h.hexdigest())
    return out


def tree_size(root):
    total = 0
    for kind, size, _ in tree_manifest(root).values():
        if kind == "file":
            total += size
    return total


class Http:
    """The small slice of Xod's API the battery uses. get/post return
    (status, parsed json or text); failures raise."""

    def __init__(self, base, timeout):
        self.base = str(base).rstrip("/")
        self.timeout = timeout

    def get_json(self, path):
        with urllib.request.urlopen(self.base + path, timeout=self.timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))

    def post_json(self, path, body):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))

    def health(self):
        try:
            with urllib.request.urlopen(self.base + "/", timeout=self.timeout) as resp:
                return 200 <= resp.status < 400
        except Exception:
            return False

    def chat_exact(self, prompt, expected, timeout_s):
        """POST /api/chat in the proving harness's shape: SSE data lines,
        content events concatenated, compared verbatim (after a strip,
        exactly as the harness compares)."""
        req = urllib.request.Request(
            self.base + "/api/chat",
            data=json.dumps({"messages": [{"role": "user", "content": prompt}]}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        parts, error = [], None
        deadline = time.time() + timeout_s
        with urllib.request.urlopen(req, timeout=max(10, int(timeout_s))) as resp:
            for raw in resp:
                if time.time() > deadline:
                    return False, "chat timed out after %ss" % timeout_s
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if ev.get("type") == "content":
                    parts.append(ev.get("content") or "")
                elif ev.get("type") == "error":
                    error = str(ev.get("content") or ev.get("error") or "unknown error")
                    break
        observed = "".join(parts).strip()
        if error:
            return False, "chat error event: %s" % error
        if observed != expected:
            return False, "chat reply mismatch: expected %r, observed %r" % (expected, observed[:120])
        return True, "exact reply matched"

    def hello_version(self):
        """The backend-reported version from GET /api/cli/hello, or None
        when the endpoint cannot be read. The bundle version and this
        version are stamped by the same release (the backend's version
        module rides inside the bundle), so a disagreement between them
        is a mixed install (AGE-72)."""
        try:
            _, data = self.get_json("/api/cli/hello")
        except Exception:
            return None
        if isinstance(data, dict) and data.get("version"):
            return str(data["version"])
        return None


def _default_start_token(app_path):
    """A token that changes when the app restarts: the pid and start time
    of the bundle executable process, or None when the app is not running."""
    pat = str(Path(app_path) / "Contents" / "MacOS") + "/"
    try:
        out = subprocess.run(["pgrep", "-f", pat], capture_output=True, timeout=10)
        pids = out.stdout.decode().split()
        if out.returncode != 0 or not pids:
            return None
        pid = sorted(pids)[0]
        started = subprocess.run(["ps", "-o", "lstart=", "-p", pid],
                                 capture_output=True, timeout=10)
        return "%s:%s" % (pid, started.stdout.decode().strip())
    except Exception:
        return None


class UpdateGuardian:
    def __init__(self, cfg, state_dir, log, http=None, quit_app=None,
                 relaunch_app=None, app_start_token=None, free_bytes=None,
                 now_fn=None):
        self.cfg = dict(DEFAULTS)
        self.cfg.update(cfg or {})
        if not self.cfg.get("update_api_base"):
            probe = str(self.cfg.get("probe_url") or "http://127.0.0.1:8000/")
            self.cfg["update_api_base"] = probe.rstrip("/")
        self.state_dir = Path(state_dir)
        self.log = log
        self.updates_dir = Path(self.cfg["update_updates_dir"]).expanduser()
        self.app_path = Path(self.cfg["update_app_path"]).expanduser()
        self.http = http or Http(self.cfg["update_api_base"],
                                 self.cfg["update_http_timeout_s"])
        self.quit_app = quit_app or self._default_quit_app
        self.relaunch_app = relaunch_app or self._default_relaunch_app
        self.app_start_token = app_start_token or (lambda: _default_start_token(self.app_path))
        self.free_bytes = free_bytes or (lambda: shutil.disk_usage(self.state_dir).free)
        self.now_fn = now_fn or time.time
        self.state = self._load_state()

    # -- paths and state ----------------------------------------------
    def _state_path(self):
        return self.state_dir / "updates-guardian.json"

    def _snap_dir(self):
        return self.state_dir / "snapshots"

    def _snapshot_path(self):
        return self._snap_dir() / "Xod.app"

    def _block_path(self, version):
        safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(version))
        return self.state_dir / ("blocked-update-%s.json" % safe)

    def _load_state(self):
        try:
            data = json.loads(self._state_path().read_text())
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _save_state(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True))
        tmp.replace(self._state_path())

    def status(self):
        blocked = self.state.get("blocked") or {}
        battery = self.state.get("last_battery") or {}
        return {
            "guardian_build": GUARDIAN_BUILD,
            "last_payload_version": self.state.get("last_payload_version"),
            "pending_version": (self.state.get("pending") or {}).get("payload_version"),
            "pending_snapshot_ok": (self.state.get("pending") or {}).get("snapshot_ok"),
            "last_guardian_report": self.state.get("last_guardian_report"),
            "snapshot_version": self.state.get("snapshot_version"),
            "snapshot_present": self._snapshot_path().exists(),
            "last_battery_ok": battery.get("ok"),
            "last_battery_verdict": battery.get("verdict"),
            "last_battery_at": battery.get("at"),
            "last_battery_for": battery.get("for_version"),
            "blocked_versions": sorted(blocked.keys()),
            "blocked": {v: (blocked[v] or {}).get("rollbacks", 0) for v in sorted(blocked)},
            "blocked_batteries": {v: (blocked[v] or {}).get("batteries")
                                  for v in sorted(blocked)},
            "last_action": self.state.get("last_action"),
            "battery_in_progress": bool(self.state.get("battery_in_progress")),
            "superseded": self.state.get("superseded"),
            "mixed_install": self.state.get("mixed_install"),
        }

    # -- payload observation -------------------------------------------
    def _staged_payload(self):
        """(version, identity) of the staged payload, or (None, None).
        The identity binds the version to the sums file and the staged
        plist bytes, so a restage of the same version with different
        contents is a new payload, and the same payload sitting in place
        is seen exactly once."""
        staged = self.updates_dir / "Xod.app"
        version = read_version(staged)
        if version is None:
            return None, None
        h = hashlib.sha256()
        h.update(version.encode())
        sums = self.updates_dir / "SHA256SUMS.txt"
        try:
            h.update(sums.read_bytes())
        except Exception:
            pass
        try:
            h.update((staged / "Contents" / "Info.plist").read_bytes())
        except Exception:
            pass
        return version, h.hexdigest()

    # -- snapshot --------------------------------------------------------
    def _take_snapshot(self, pre_version, payload_version):
        app = self.app_path
        if not app.exists():
            self.log("updates guardian: snapshot refused for payload %s: the installed app "
                     "is not at %s" % (payload_version, app))
            return False
        size = tree_size(app)
        need = size * 2 + int(self.cfg["update_disk_margin_bytes"])
        try:
            free = int(self.free_bytes())
        except Exception as exc:
            free = -1
            self.log("updates guardian: free-space probe failed: %s" % exc)
        if free < need:
            self.log("updates guardian: snapshot REFUSED for payload %s: the copy cannot fit "
                     "with margin (app is %d bytes; the rule needs 2x the app plus %d bytes "
                     "of margin = %d bytes free; only %d bytes free)"
                     % (payload_version, size, int(self.cfg["update_disk_margin_bytes"]),
                        need, free))
            self.state["last_action"] = "snapshot refused (disk space)"
            return False
        snap_dir = self._snap_dir()
        snap_dir.mkdir(parents=True, exist_ok=True)
        incoming = snap_dir / ".incoming"
        shutil.rmtree(incoming, ignore_errors=True)
        shutil.copytree(app, incoming, symlinks=True)
        if (tree_manifest(incoming, ignore_volatile=True) != tree_manifest(app, ignore_volatile=True)
                or read_version(incoming) != pre_version):
            shutil.rmtree(incoming, ignore_errors=True)
            self.log("updates guardian: snapshot for payload %s FAILED verification; "
                     "the retained snapshot was not touched" % payload_version)
            return False
        final = self._snapshot_path()
        shutil.rmtree(final, ignore_errors=True)
        incoming.rename(final)
        self.state["snapshot_version"] = pre_version
        self.log("updates guardian: snapshot taken: installed Xod %s copied to the snapshots "
                 "area ahead of staged payload %s (verified byte for byte)"
                 % (pre_version, payload_version))
        return True

    # -- the health battery ----------------------------------------------
    def _battery_health(self):
        deadline = time.time() + float(self.cfg["update_health_timeout_s"])
        poll = float(self.cfg["update_health_poll_s"])
        while True:
            if self.http.health():
                return True, "health endpoint answered"
            if time.time() >= deadline:
                return False, "health endpoint did not answer within %ss" % self.cfg["update_health_timeout_s"]
            time.sleep(min(poll, max(0.1, deadline - time.time())))

    def _battery_file_round_trip_once(self, nonce):
        """One file round-trip attempt, as a three-valued verdict:
        "pass" (the task completed and the bytes matched), "fail" (the
        task completed but the bytes are wrong or the file is absent:
        evidence about the build), or "inconclusive" (the task system
        produced no verdict on the file at all: the task ended failed
        or cancelled, the task API errored, or no terminal state
        arrived in time). The probe rides the same agent task system
        the update is judging, so a task that dies because the backend
        is overloaded (the 2026-11-22 1.1.16 incident: the file task
        ended 'failed' during a burst of mail-endpoint 429s) is a
        statement about load, not about the build, and must never be
        counted as a build failure."""
        sandbox = self.state_dir / "update-battery-sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        target = sandbox / "proof.txt"
        try:
            target.unlink()
        except Exception:
            pass
        content = "XOD UPDATE PROOF %s 42" % nonce
        text = ("Automated update check task. Create a plain text file at %s whose entire "
                "contents are exactly this, with no extra words and no extra lines: %s"
                % (target, content))
        try:
            _, created = self.http.post_json("/api/tasks", {"request": text})
        except Exception as exc:
            return "inconclusive", "task create raised %s: %s" % (type(exc).__name__, exc)
        task = created.get("task") or {} if isinstance(created, dict) else {}
        tid = task.get("id") or (created.get("id") if isinstance(created, dict) else None)
        if not tid:
            return "inconclusive", "task create returned no id"
        deadline = time.time() + float(self.cfg["update_task_timeout_s"])
        approved = False
        status = None
        while time.time() < deadline:
            try:
                _, listing = self.http.get_json("/api/tasks")
            except Exception as exc:
                return "inconclusive", "task list raised %s: %s" % (type(exc).__name__, exc)
            tasks = listing.get("tasks", listing if isinstance(listing, list) else [])
            mine = next((t for t in tasks if t.get("id") == tid), None)
            if mine is None:
                return "inconclusive", "task %s vanished from the task list" % tid
            status = mine.get("status")
            if status in ("done", "failed", "cancelled"):
                break
            if status == "waiting" and mine.get("waiting_kind") == "approval" and not approved:
                try:
                    self.http.post_json("/api/tasks/%s/approve" % tid, {})
                except Exception as exc:
                    return "inconclusive", "task approve raised %s: %s" % (type(exc).__name__, exc)
                approved = True
            time.sleep(float(self.cfg["update_task_poll_s"]))
        if status in ("failed", "cancelled"):
            return "inconclusive", ("file task ended with status %r: the task system "
                                    "produced no verdict on the file contents" % status)
        if status != "done":
            return "inconclusive", ("file task %s reached no terminal state within %ss "
                                    "(last status %r)" % (tid, self.cfg["update_task_timeout_s"],
                                                          status))
        try:
            observed = target.read_bytes()
        except Exception:
            return "fail", "the task reported done but the file it was told to write is not on disk"
        if observed != content.encode():
            return "fail", "file bytes mismatch: expected %r, observed %r" % (content, observed[:120])
        try:
            target.unlink()
        except Exception:
            pass
        return "pass", "task wrote the exact bytes and the read-back matched (task %s)" % tid

    def _battery_file_round_trip(self, nonce):
        """The file round-trip probe. Inconclusive attempts (the task
        system produced no verdict) retry in place with backoff: a
        transient overload gets its chance to clear before the probe
        reports, and the probe can only ever report inconclusive for
        that class, never a counted failure. A content failure is a
        verdict about the build and is conclusive for this battery;
        its retry is the confirmation battery, so a file failure counts
        toward a rollback only when the retry also fails."""
        retries = max(0, int(self.cfg["update_file_probe_retries"]))
        backoff = float(self.cfg["update_file_probe_backoff_s"])
        verdict, detail = self._battery_file_round_trip_once(nonce)
        attempts = 1
        while verdict == "inconclusive" and attempts <= retries:
            wait = backoff * attempts
            self.log("updates guardian: file round trip attempt %d inconclusive (%s); "
                     "retrying in %ss" % (attempts, detail, wait))
            if wait > 0:
                time.sleep(wait)
            verdict, detail = self._battery_file_round_trip_once(nonce)
            attempts += 1
        if attempts > 1:
            detail = "%s (after %d attempts)" % (detail, attempts)
        return verdict, detail

    def _battery_permissions(self):
        status, data = self.http.get_json("/api/crew/settings/auto-approve")
        if status != 200 or not isinstance(data, dict):
            return False, "auto-approve settings endpoint answered %s" % status
        perms = data.get("permissions") or {}
        partner = data.get("partner") or {}
        detail = ("recorded, no assertion: Xod own trusted=%r, partner block "
                  "running=%r trusted=%r" % (perms.get("trusted"),
                                             partner.get("running"), partner.get("trusted")))
        return True, detail

    # -- battery marker (AGE-72) ------------------------------------------
    # While a verdict runs, the guardian publishes battery-in-progress.json
    # in its state dir. The Electron swap paths (the in-app updater and the
    # bootstrap repair) read that marker and defer their own swap until
    # the verdict lands, so two update paths can never interleave on the
    # same install again. The marker is written before the first probe and
    # removed in a finally, so even a battery that raises cannot leave a
    # stale marker behind; the readers also age markers out.
    def _battery_marker_path(self):
        return self.state_dir / "battery-in-progress.json"

    def _mark_battery_start(self, for_version):
        at = _iso(self.now_fn())
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.state_dir / "battery-in-progress.json.tmp"
            tmp.write_text(json.dumps({"for_version": for_version, "at": at,
                                       "pid": os.getpid()}))
            tmp.replace(self._battery_marker_path())
        except Exception as exc:
            self.log("updates guardian: battery marker write failed: %s" % exc)
        self.state["battery_in_progress"] = {"for_version": for_version, "at": at}
        self._save_state()

    def _mark_battery_end(self):
        try:
            self._battery_marker_path().unlink()
        except Exception:
            pass
        self.state.pop("battery_in_progress", None)
        self._save_state()

    def run_battery(self, for_version):
        self._mark_battery_start(for_version)
        try:
            return self._run_battery_checks(for_version)
        finally:
            self._mark_battery_end()

    @staticmethod
    def _verdict_of(ok, checks):
        """The battery verdict behind the (ok, checks) pair: "pass"
        when ok, "fail" when any check failed (a conclusive failure is
        evidence about the build and dominates: an inconclusive probe
        elsewhere in the same battery does not dilute it), otherwise
        "inconclusive" when nothing failed but at least one check
        produced no verdict."""
        if ok:
            return "pass"
        if any(c.get("verdict") == "fail" for c in checks):
            return "fail"
        if any(c.get("verdict") == "inconclusive" for c in checks):
            return "inconclusive"
        return "fail"

    def _run_battery_checks(self, for_version):
        nonce = secrets.token_hex(3).upper()
        checks = []

        def run(name, fn):
            try:
                first, detail = fn()
            except Exception as exc:
                first, detail = False, "%s: %s" % (type(exc).__name__, exc)
            if isinstance(first, bool):
                verdict = "pass" if first else "fail"
            else:
                verdict = str(first)
            ok = verdict == "pass"
            checks.append({"name": name, "ok": ok, "verdict": verdict,
                           "detail": str(detail)})
            label = {"pass": "PASS", "fail": "FAIL: ", "inconclusive": "INCONCLUSIVE: "}
            self.log("updates guardian: battery [%s] %s: %s"
                     % (for_version, name,
                        "PASS" if ok else label.get(verdict, "FAIL: ") + str(detail)))
            return ok

        results = [
            run("health", self._battery_health),
            run("chat exact reply", lambda: self.http.chat_exact(
                "Automated update check. Do not search the web and do not use any tools. "
                "Reply with exactly this text and nothing else: XOD-UPDATE-CHECK-%s" % nonce,
                "XOD-UPDATE-CHECK-%s" % nonce, float(self.cfg["update_chat_timeout_s"]))),
            run("file round trip", lambda: self._battery_file_round_trip(nonce)),
            run("permission reads", self._battery_permissions),
        ]
        ok = all(results)
        verdict = self._verdict_of(ok, checks)
        self.state["last_battery"] = {"at": _iso(self.now_fn()), "ok": ok,
                                      "verdict": verdict,
                                      "for_version": for_version, "checks": checks}
        return ok, checks

    # -- rollback ----------------------------------------------------------
    def _default_quit_app(self):
        name = self.app_path.stem
        try:
            subprocess.run(["osascript", "-e", 'quit app "%s"' % name],
                           capture_output=True, timeout=30)
        except Exception:
            pass
        pat = str(self.app_path / "Contents" / "MacOS") + "/"
        deadline = time.time() + float(self.cfg["update_quit_wait_s"])
        while time.time() < deadline and self.app_start_token() is not None:
            time.sleep(1)
        if self.app_start_token() is not None:
            subprocess.run(["pkill", "-f", pat], capture_output=True, timeout=15)
            time.sleep(2)

    def _default_relaunch_app(self):
        subprocess.run(["open", str(self.app_path)], capture_output=True, timeout=30)

    def _write_planned_signal(self, reason):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_dir / "planned-shutdown.json.tmp"
        tmp.write_text(json.dumps({"at": self.now_fn(), "reason": reason}))
        tmp.replace(self.state_dir / "planned-shutdown.json")

    def _restore_snapshot(self, snapshot, pending):
        """Restore the retained snapshot over the app path, staged: the
        snapshot is copied to a staging tree and verified byte for byte
        BEFORE anything at the app path moves, then swapped in and
        verified again. A copy that dies halfway (the 0.9.24 incident:
        Errno 1 across the tree) therefore leaves the app path holding
        a complete tree — the verified restored build, or, if the swap
        itself fails, the pre-rollback build moved back — never a
        half-copied husk. The snapshot itself is only ever read."""
        want = pending.get("snapshot_version")
        staging = self._snap_dir() / ".restore-staging"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            shutil.copytree(snapshot, staging, symlinks=True)
            staged_ok = (tree_manifest(staging, ignore_volatile=True)
                         == tree_manifest(snapshot, ignore_volatile=True)
                         and read_version(staging) == want)
        except Exception as exc:
            self.log("updates guardian: restore copy raised: %s" % exc)
            staged_ok = False
        if not staged_ok:
            shutil.rmtree(staging, ignore_errors=True)
            self.log("updates guardian: RESTORE FAILED before the swap: the staged copy "
                     "did not verify; the app path was not touched")
            return False
        # AGE-72, second belt: the installed version may have moved while
        # the restore copy was staging (a newer swap landing mid-rollback).
        # Restoring now would move the install backwards over that swap,
        # so abort with the app path untouched.
        moved = read_version(self.app_path)
        if moved != pending.get("payload_version"):
            shutil.rmtree(staging, ignore_errors=True)
            self.log("updates guardian: RESTORE ABORTED before the swap: the installed "
                     "version moved to %s while the restore was staging, not the %s "
                     "that failed the battery; the app path was not touched"
                     % (moved, pending.get("payload_version")))
            return False
        aside = self._snap_dir() / ".replaced-bad"
        shutil.rmtree(aside, ignore_errors=True)
        moved_aside = False
        try:
            if self.app_path.exists():
                shutil.move(str(self.app_path), str(aside))
                moved_aside = True
            shutil.move(str(staging), str(self.app_path))
            restored = (tree_manifest(self.app_path, ignore_volatile=True)
                        == tree_manifest(snapshot, ignore_volatile=True)
                        and read_version(self.app_path) == want)
        except Exception as exc:
            self.log("updates guardian: restore swap raised: %s" % exc)
            restored = False
        if restored:
            # The replaced (battery-failed) build stays in the aside tree
            # until the restored build passes its post-rollback battery:
            # if the restored build turns out to be the one that cannot
            # start, the guardian puts the replaced build back rather
            # than leaving a dead app installed (2026-11-22: the restored
            # 1.1.4 could not start at all and was simply left there).
            self.state["replaced_kept"] = str(aside)
            return True
        self.log("updates guardian: RESTORE FAILED verification; the replaced app "
                 "is kept at %s" % aside)
        if moved_aside and aside.exists():
            shutil.rmtree(self.app_path, ignore_errors=True)
            try:
                shutil.move(str(aside), str(self.app_path))
                self.log("updates guardian: the pre-rollback build was put back in place")
            except Exception as exc:
                self.log("updates guardian: putting the pre-rollback build back raised: %s" % exc)
        return False

    def _record_block(self, bad_version, failure, restored_version, batteries=None):
        blocked = self.state.setdefault("blocked", {})
        entry = blocked.get(bad_version) or {"rollbacks": 0}
        entry["rollbacks"] = int(entry.get("rollbacks", 0)) + 1
        entry["at"] = _iso(self.now_fn())
        entry["failure"] = failure
        # The full battery history rides in the record (2026-11-22,
        # second incident): which battery failed, its verdict, and the
        # per-probe results, plus the guardian build that acted. The
        # old record carried only the final failure string, in a shape
        # identical across guardian builds, so the field record could
        # not say which battery or which code produced it.
        entry["batteries"] = batteries or []
        entry["failed_battery"] = (batteries[-1].get("which") if batteries else None)
        entry["guardian_build"] = GUARDIAN_BUILD
        blocked[bad_version] = entry
        marker = {"version": bad_version, "at": entry["at"], "failure": failure,
                  "rollbacks": entry["rollbacks"],
                  "restored_version": restored_version,
                  "failed_battery": entry["failed_battery"],
                  "batteries": entry["batteries"],
                  "guardian_build": GUARDIAN_BUILD}
        self._block_path(bad_version).write_text(json.dumps(marker, indent=1, sort_keys=True))
        self.log("updates guardian: block marker written for payload %s (rollback %d): %s"
                 % (bad_version, entry["rollbacks"], self._block_path(bad_version)))
        if entry["rollbacks"] >= 2:
            self.log("updates guardian: BLOCKED: payload %s has now failed the update battery "
                     "%d times; it is flagged in status and will never be silently accepted"
                     % (bad_version, entry["rollbacks"]))
        return entry

    def _write_guardian_report(self, kind, version, detail):
        """One clear report file per guardian incident that left the app
        in a state other than a clean accept or a clean rollback, so a
        human finds the story in one place instead of a looping dialog."""
        report = {"kind": kind, "version": version, "at": _iso(self.now_fn()),
                  "detail": detail}
        safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(version))
        path = self.state_dir / ("guardian-report-%s.json" % safe)
        try:
            path.write_text(json.dumps(report, indent=1, sort_keys=True))
        except Exception as exc:
            self.log("updates guardian: guardian report write failed: %s" % exc)
        self.state["last_guardian_report"] = {"kind": kind, "version": version,
                                              "at": report["at"]}
        return path

    def _swap_back_replaced(self, pending):
        """Put the pre-rollback build (kept aside by _restore_snapshot)
        back at the app path. Used when the restored build fails its own
        post-rollback battery: the pre-rollback build, which passed most
        of its battery, is the better known state."""
        kept = self.state.pop("replaced_kept", None)
        if not kept or not Path(kept).exists():
            return False
        kept = Path(kept)
        if read_version(kept) != pending.get("payload_version"):
            return False
        try:
            self.quit_app()
        except Exception as exc:
            self.log("updates guardian: quit before put-back raised: %s" % exc)
        aside = self._snap_dir() / ".failed-restore"
        shutil.rmtree(aside, ignore_errors=True)
        try:
            if self.app_path.exists():
                shutil.move(str(self.app_path), str(aside))
            shutil.move(str(kept), str(self.app_path))
            ok = read_version(self.app_path) == pending.get("payload_version")
        except Exception as exc:
            self.log("updates guardian: put-back swap raised: %s" % exc)
            ok = False
        if ok:
            shutil.rmtree(aside, ignore_errors=True)
        else:
            if aside.exists() and not self.app_path.exists():
                try:
                    shutil.move(str(aside), str(self.app_path))
                except Exception:
                    pass
            self.log("updates guardian: PUT-BACK FAILED; what sits at the app path "
                     "needs a human: %s" % self.app_path)
        try:
            self.relaunch_app()
        except Exception as exc:
            self.log("updates guardian: relaunch after put-back raised: %s" % exc)
        return ok

    def _rollback(self, pending, checks, batteries=None):
        bad_version = pending.get("payload_version")
        # AGE-72: never restore over a completed newer swap. The battery
        # ran against bad_version, but the installed version is re-read
        # NOW: if it moved while the verdict ran (a repair swap, a newer
        # update), this rollback's premise is gone. Abort without
        # quitting, restoring, or blocking anything.
        installed_now = read_version(self.app_path)
        if installed_now != bad_version:
            self.log("updates guardian: rollback ABORTED for %s: the installed version "
                     "is now %s, not the version that failed the battery; a completed "
                     "newer swap is never restored over"
                     % (bad_version, installed_now))
            self.state["last_rollback_aborted"] = {
                "at": _iso(self.now_fn()), "bad_version": bad_version,
                "installed_version": installed_now}
            self.state["last_action"] = "rollback aborted: installed moved to %s" % installed_now
            return False
        failure = "; ".join("%s: %s" % (c["name"], c["detail"]) for c in checks if not c["ok"])
        # Freshness gate (2026-11-22): a rollback may only restore the
        # snapshot of the install this payload actually replaced. If the
        # retained snapshot is anything else (an older cycle's copy, left
        # in place because this cycle's snapshot failed verification or
        # was refused or held), restoring it moves the install backwards
        # past the replaced version. That is how a healthy 1.1.14 became
        # a stranded 1.1.4: the 1.1.13 snapshot failed verification, the
        # retained 1.1.4 snapshot was restored instead, and 1.1.4's own
        # self-repair is known-broken, so the machine looped on a repair
        # dialog. Refuse: leave the new install running, block the
        # payload, write one clear report.
        snapshot = self._snapshot_path()
        snap_version = pending.get("snapshot_version")
        if not snapshot.exists() or snap_version != pending.get("pre_version"):
            self.log("updates guardian: ROLLBACK REFUSED for %s: the retained snapshot is "
                     "%s, not the %s this payload replaced; restoring it would move the "
                     "install backwards past the replaced version. The new install is left "
                     "in place, the payload is blocked, and a guardian report was written"
                     % (bad_version, snap_version, pending.get("pre_version")))
            self._record_block(bad_version, failure, None, batteries)
            self._write_guardian_report(
                "stale-snapshot-rollback-refused", bad_version,
                "battery failure: %s. The retained snapshot is %s but the install this "
                "payload replaced was %s, so no restore was attempted and %s stays "
                "installed. A human should check the app; the payload stays blocked."
                % (failure, snap_version, pending.get("pre_version"), bad_version))
            self.state["last_action"] = ("rollback refused (stale snapshot): %s left installed"
                                         % bad_version)
            return False
        self.log("updates guardian: update to %s FAILED the battery (%s); rolling back to %s"
                 % (bad_version, failure, snap_version))
        self._write_planned_signal("update rollback: %s failed the health battery" % bad_version)
        self.log("updates guardian: planned-shutdown signal written ahead of the rollback quit")
        try:
            self.quit_app()
        except Exception as exc:
            self.log("updates guardian: quit before rollback raised: %s" % exc)
        restored = self._restore_snapshot(snapshot, pending)
        if not restored:
            # The installed state is suspect now: whatever sits at the app
            # path must never be snapshotted over the retained last-good
            # copy (the 0.9.24 incident: a failed restore left a husk in
            # place and the NEXT payload's snapshot overwrote the good
            # 0.9.23 snapshot with it). Snapshots hold until a battery
            # passes for the installed version again.
            self.state["snapshot_hold"] = True
            self.log("updates guardian: snapshot hold set: after the failed restore, "
                     "later payloads will not overwrite the retained snapshot of %s"
                     % snap_version)
        try:
            self.relaunch_app()
        except Exception as exc:
            self.log("updates guardian: relaunch after rollback raised: %s" % exc)
        if restored:
            ok2, checks2 = self.run_battery(snap_version)
            self.log("updates guardian: post-rollback battery for the restored %s: %s"
                     % (snap_version, "PASS" if ok2 else "FAIL"))
            if ok2:
                kept = self.state.pop("replaced_kept", None)
                if kept:
                    shutil.rmtree(kept, ignore_errors=True)
                # AGE-72 property (c): a restored bundle whose backend still
                # reports a different version is a mixed install even after
                # the rollback. Surface it as a defect state in status; do
                # not loop another rollback over it.
                agree, detail = self._version_agreement(snap_version)
                if not agree:
                    self.state["mixed_install"] = {
                        "at": _iso(self.now_fn()), "detail": detail,
                        "after": "rollback to %s" % snap_version}
                    self.log("updates guardian: MIXED INSTALL after rollback: %s; "
                             "recorded as a defect state in status" % detail)
            else:
                # The restored build failed its own battery. That alone is
                # not a reason to undo the rollback: a restored build that
                # ANSWERS health but fails agent probes is a working app
                # with an unhappy backend, and it stays. The put-back is
                # only for the 2026-11-22 shape: the restored build cannot
                # start at all (its health probe fails) while the build it
                # replaced had passed health — a dead install is strictly
                # worse than the better-known state that was rolled back.
                fail2 = "; ".join("%s: %s" % (c["name"], c["detail"])
                                  for c in checks2 if not c["ok"])
                restored_starts = any(c["name"] == "health" and c["ok"] for c in checks2)
                replaced_started = any(c["name"] == "health" and c["ok"] for c in checks)
                if restored_starts or not replaced_started:
                    self.log("updates guardian: the restored %s failed its post-rollback "
                             "battery (%s) but %s, so the rollback stands and the failure "
                             "is on record" % (snap_version, fail2,
                                                "it answers health, so it is a working app"
                                                if restored_starts else
                                                "the replaced build had not passed health either, "
                                                "so there is no better known state to put back"))
                elif self._swap_back_replaced(pending):
                    self.log("updates guardian: the restored %s FAILED its post-rollback "
                             "battery (%s), so the pre-rollback build %s was put back: it "
                             "is the better known state. The payload is blocked and a "
                             "guardian report was written" % (snap_version, fail2, bad_version))
                    self._write_guardian_report(
                        "restored-build-failed-put-back", bad_version,
                        "battery failure: %s. The rollback restored %s, which then failed "
                        "its own battery (%s), so %s was put back in place. A human should "
                        "check the app; the payload stays blocked."
                        % (failure, snap_version, fail2, bad_version))
                    restored = False
                else:
                    self.log("updates guardian: the restored %s FAILED its post-rollback "
                             "battery (%s) and the pre-rollback build could not be put "
                             "back; what sits at the app path needs a human"
                             % (snap_version, fail2))
                    self._write_guardian_report(
                        "restored-build-failed-put-back-failed", bad_version,
                        "battery failure: %s. The rollback restored %s, which failed its "
                        "own battery (%s), and putting %s back failed too."
                        % (failure, snap_version, fail2, bad_version))
        self._record_block(bad_version, failure,
                           snap_version if restored else None, batteries)
        self.state["last_action"] = "rolled back %s" % bad_version
        return restored

    # -- one tick ------------------------------------------------------------
    def tick(self, now=None):
        now = self.now_fn() if now is None else now
        version, identity = self._staged_payload()
        if version is None:
            if self.state.get("seen_identity") is not None:
                self.state["seen_identity"] = None
                self._save_state()
        elif identity != self.state.get("seen_identity"):
            self._on_new_payload(version, identity, now)
        pending = self.state.get("pending")
        if pending:
            self._maybe_verify_swap(pending, now)
        self._save_state()
        return self.status()

    def _on_new_payload(self, version, identity, now):
        installed = read_version(self.app_path)
        self.log("updates guardian: new staged payload seen: Xod %s (installed is %s); "
                 "snapshotting before any swap" % (version, installed))
        blocked = (self.state.get("blocked") or {}).get(version)
        pending = {"payload_version": version, "pre_version": installed,
                   "snapshot_version": self.state.get("snapshot_version"),
                   "snapshot_ok": False,
                   "staged_at": _iso(now), "pre_start_token": self.app_start_token(),
                   "blocked_repeat": bool(blocked)}
        if blocked:
            self.log("updates guardian: payload %s is a BLOCKED version (rolled back %d "
                     "time(s), last at %s); the retained snapshot of %s is NOT overwritten "
                     "with the current app state"
                     % (version, blocked.get("rollbacks", 0), blocked.get("at"),
                        pending["snapshot_version"]))
        elif self.state.get("snapshot_hold") and self._snapshot_path().exists():
            self.log("updates guardian: snapshot HELD for payload %s: the last restore "
                     "failed, so the current installed state is not trusted as a new "
                     "last-good; the retained snapshot of %s is kept untouched"
                     % (version, pending["snapshot_version"]))
        else:
            if self._take_snapshot(installed, version):
                pending["snapshot_version"] = self.state.get("snapshot_version")
                pending["snapshot_ok"] = True
        if not pending["snapshot_ok"]:
            self.log("updates guardian: ROLLBACK PROTECTION UNAVAILABLE for payload %s: "
                     "no verified snapshot of the installed %s exists for this cycle "
                     "(see the snapshot lines above), so if %s fails its battery it will "
                     "be left in place and blocked, never restored to a stale snapshot"
                     % (version, installed, version))
        self.state["seen_identity"] = identity
        self.state["last_payload_version"] = version
        self.state["pending"] = pending
        self.state["last_action"] = "snapshot ahead of %s" % version
        self._save_state()

    def _version_agreement(self, bundle_version):
        """AGE-72 property (c): after any swap, the installed bundle
        version and the backend-reported version must agree. Both are
        stamped by the same release, so a disagreement is a mixed
        install, the field state behind the 2026-11-14 failure (bundle
        at the rolled-back version, backend reporting the newer one).
        An unreadable backend version proves nothing either way; the
        battery's health check owns that failure."""
        hello = getattr(self.http, "hello_version", None)
        backend = hello() if callable(hello) else None
        if backend is None or bundle_version is None:
            return True, "backend version unreadable; the health check owns that failure"
        if _norm_version(backend) == _norm_version(bundle_version):
            return True, "bundle and backend agree on %s" % bundle_version
        return False, ("mixed install: the installed bundle reports %s but the backend "
                       "reports %s" % (bundle_version, backend))

    def _maybe_verify_swap(self, pending, now):
        current = read_version(self.app_path)
        if current is None or current == pending.get("pre_version"):
            return
        token = self.app_start_token()
        if token is None or token == pending.get("pre_start_token"):
            self.log("updates guardian: installed version stamp changed to %s but the app "
                     "has not restarted yet; the battery waits for the new process" % current)
            return
        if current != pending.get("payload_version"):
            # AGE-72: the install moved to a version that was never this
            # cycle's staged payload (a bootstrap repair swap, or an even
            # newer update landed first). The pending verdict is
            # superseded: no battery against a version this cycle did not
            # stage, and above all no rollback, which would restore the
            # pre-update snapshot over a completed newer swap. The
            # intervening install is left exactly as it landed and held
            # as unverified: the retained snapshot is not overwritten by
            # it until some battery passes for the installed state.
            self.log("updates guardian: the installed version is %s, not the staged payload "
                     "%s this guardian cycle was watching; another swap landed first. No "
                     "battery and no rollback for the superseded payload; the installed "
                     "state is held as unverified"
                     % (current, pending.get("payload_version")))
            self.state["superseded"] = {"at": _iso(now),
                                        "expected": pending.get("payload_version"),
                                        "found": current,
                                        "pre_version": pending.get("pre_version")}
            self.state["snapshot_hold"] = True
            self.state["pending"] = None
            self.state["last_action"] = "superseded swap: installed %s" % current
            self._save_state()
            return
        # Inconclusive retry gate: after an inconclusive battery the
        # verdict waits for a later tick, but not every tick: the task
        # system that produced no verdict needs time to shed its load
        # before the battery leans on it again.
        attempt = self.state.get("last_battery_attempt") or {}
        if (attempt.get("for_version") == current
                and attempt.get("verdict") == "inconclusive"
                and now - float(attempt.get("at_epoch") or 0)
                < float(self.cfg["update_battery_retry_min_s"])):
            self.log("updates guardian: the battery for %s was inconclusive at %s; "
                     "the next attempt waits for the retry interval"
                     % (current, attempt.get("at")))
            return
        self.log("updates guardian: swap detected: installed Xod went %s to %s and restarted; "
                 "running the health battery" % (pending.get("pre_version"), current))
        batteries = []

        def run_and_record(which):
            ok_, checks_ = self.run_battery(current)
            verdict_ = self._verdict_of(ok_, checks_)
            batteries.append({"which": which, "at": _iso(now), "verdict": verdict_,
                              "ok": ok_, "checks": checks_})
            self.state["last_battery_attempt"] = {
                "for_version": current, "verdict": verdict_,
                "at": _iso(now), "at_epoch": now}
            return verdict_, checks_

        def accept():
            self.state.pop("snapshot_hold", None)   # the installed build just proved itself
            self.log("updates guardian: update accepted: Xod %s to %s passed the health "
                     "battery; the snapshot of %s is retained until the next payload"
                     % (pending.get("pre_version"), current, pending.get("snapshot_version")))
            self.state["pending"] = None
            self.state["last_action"] = "update accepted: %s" % current

        def inconclusive():
            # No verdict either way: the update is NOT accepted (the
            # build has not proved itself) and NEVER rolled back (there
            # is no evidence against it). The pending cycle stays open
            # and the battery re-runs on a later tick.
            self.log("updates guardian: battery for %s is INCONCLUSIVE: a probe produced "
                     "no verdict (the task system did not answer for the build), so the "
                     "update is neither accepted nor rolled back; the battery re-runs "
                     "on a later tick" % current)
            self.state["last_action"] = "battery inconclusive for %s; verdict pending" % current

        agree, detail = self._version_agreement(current)
        if not agree:
            # AGE-72 property (c): the swap landed a bundle whose backend
            # reports a different version. That is a defect state, not a
            # pass. It is a FIRST failed verdict, not a verdict: the
            # confirmation rule applies here exactly as it does to a
            # failed battery, so the cycle runs a confirmation battery
            # and re-checks agreement before anything rolls back. A
            # single agreement read never rolls an install back.
            checks = [{"name": "version agreement", "ok": False, "verdict": "fail",
                       "detail": detail}]
            self.state["last_battery"] = {"at": _iso(now), "ok": False,
                                          "verdict": "fail",
                                          "for_version": current, "checks": checks}
            self.state["mixed_install"] = {"at": _iso(now), "detail": detail,
                                           "bundle_version": current}
            batteries.append({"which": "first", "at": _iso(now), "verdict": "fail",
                              "ok": False, "checks": checks})
            self.state["last_battery_attempt"] = {
                "for_version": current, "verdict": "fail",
                "at": _iso(now), "at_epoch": now}
            self.log("updates guardian: MIXED INSTALL detected: %s; running a confirmation "
                     "battery before any rollback (a single failed verdict never rolls "
                     "an install back)" % detail)
            verdict2, checks2 = run_and_record("confirmation")
            agree2, detail2 = self._version_agreement(current)
            if verdict2 == "pass" and agree2:
                self.log("updates guardian: confirmation for %s passed and the versions "
                         "agree on re-check; the first agreement read is treated as "
                         "transient and no rollback happens" % current)
                accept()
            elif verdict2 == "inconclusive":
                inconclusive()
            else:
                final_checks = list(checks2)
                if not agree2:
                    final_checks = final_checks + [
                        {"name": "version agreement", "ok": False, "verdict": "fail",
                         "detail": detail2}]
                self._rollback(pending, final_checks, batteries)
                self.state["pending"] = None
            self._save_state()
            return
        verdict, checks = run_and_record("first")
        if verdict == "inconclusive":
            inconclusive()
            self._save_state()
            return
        if verdict == "fail":
            # Confirmation (2026-11-22): one failed battery never rolls an
            # install back. The battery's probes lean on the agent stack
            # itself (a chat reply, a file task the model must perform),
            # so a single transient failure is weak evidence; a healthy
            # 1.1.14 was rolled back on exactly one failed file-round-trip
            # probe while health, chat and permissions all passed. Re-run
            # the whole battery once; only a second consecutive failure
            # acts. A confirmation battery that comes back inconclusive
            # does not act either: the verdict waits for a later tick.
            self.log("updates guardian: battery for %s failed; running a confirmation "
                     "battery before any rollback (a single failed battery never rolls "
                     "an install back)" % current)
            verdict, checks = run_and_record("confirmation")
            if verdict == "pass":
                self.log("updates guardian: confirmation battery for %s PASSED; the first "
                         "failure is treated as transient and no rollback happens" % current)
            elif verdict == "inconclusive":
                inconclusive()
                self._save_state()
                return
        if verdict == "pass":
            accept()
        else:
            self._rollback(pending, checks, batteries)
            self.state["pending"] = None
        self._save_state()
