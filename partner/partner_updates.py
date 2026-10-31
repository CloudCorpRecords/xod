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
3. On battery failure: roll back. Write the planned-shutdown signal
   first (the v1.1 marker protocol, so the revival accounting stays
   honest), quit the app, restore the snapshot over the app path,
   relaunch, re-run the battery, and write a block marker naming the bad
   payload version, the time, and the failure. If that same version
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
import plistlib
import secrets
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

DEFAULT_UPDATES_DIR = Path.home() / "Library" / "Caches" / "Xod" / "updates"
DEFAULT_APP_PATH = Path("/Applications/Xod.app")

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
    "update_http_timeout_s": 15,
    "update_quit_wait_s": 30,
}


def _iso(ts):
    from datetime import datetime
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


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


def tree_manifest(root):
    """{relative path: (kind, size, sha256 or link target)} for a tree,
    used to verify copies byte for byte. Symlinks are recorded, not
    followed."""
    root = Path(root)
    out = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
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
            "last_payload_version": self.state.get("last_payload_version"),
            "pending_version": (self.state.get("pending") or {}).get("payload_version"),
            "snapshot_version": self.state.get("snapshot_version"),
            "snapshot_present": self._snapshot_path().exists(),
            "last_battery_ok": battery.get("ok"),
            "last_battery_at": battery.get("at"),
            "last_battery_for": battery.get("for_version"),
            "blocked_versions": sorted(blocked.keys()),
            "blocked": {v: (blocked[v] or {}).get("rollbacks", 0) for v in sorted(blocked)},
            "last_action": self.state.get("last_action"),
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
        if tree_manifest(incoming) != tree_manifest(app) or read_version(incoming) != pre_version:
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

    def _battery_file_round_trip(self, nonce):
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
        _, created = self.http.post_json("/api/tasks", {"request": text})
        task = created.get("task") or {}
        tid = task.get("id") or created.get("id")
        if not tid:
            return False, "task create returned no id"
        deadline = time.time() + float(self.cfg["update_task_timeout_s"])
        approved = False
        status = None
        while time.time() < deadline:
            _, listing = self.http.get_json("/api/tasks")
            tasks = listing.get("tasks", listing if isinstance(listing, list) else [])
            mine = next((t for t in tasks if t.get("id") == tid), None)
            if mine is None:
                return False, "task %s vanished from the task list" % tid
            status = mine.get("status")
            if status in ("done", "failed", "cancelled"):
                break
            if status == "waiting" and mine.get("waiting_kind") == "approval" and not approved:
                self.http.post_json("/api/tasks/%s/approve" % tid, {})
                approved = True
            time.sleep(float(self.cfg["update_task_poll_s"]))
        if status != "done":
            return False, "file task ended with status %r" % status
        try:
            observed = target.read_bytes()
        except Exception:
            return False, "the file the task was told to write is not on disk"
        if observed != content.encode():
            return False, "file bytes mismatch: expected %r, observed %r" % (content, observed[:120])
        try:
            target.unlink()
        except Exception:
            pass
        return True, "task wrote the exact bytes and the read-back matched (task %s)" % tid

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

    def run_battery(self, for_version):
        nonce = secrets.token_hex(3).upper()
        checks = []

        def run(name, fn):
            try:
                ok, detail = fn()
            except Exception as exc:
                ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
            checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})
            self.log("updates guardian: battery [%s] %s: %s"
                     % (for_version, name, "PASS" if ok else "FAIL: " + str(detail)))
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
        self.state["last_battery"] = {"at": _iso(self.now_fn()), "ok": ok,
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

    def _rollback(self, pending, checks):
        bad_version = pending.get("payload_version")
        failure = "; ".join("%s: %s" % (c["name"], c["detail"]) for c in checks if not c["ok"])
        self.log("updates guardian: update to %s FAILED the battery (%s); rolling back to %s"
                 % (bad_version, failure, pending.get("snapshot_version")))
        self._write_planned_signal("update rollback: %s failed the health battery" % bad_version)
        self.log("updates guardian: planned-shutdown signal written ahead of the rollback quit")
        try:
            self.quit_app()
        except Exception as exc:
            self.log("updates guardian: quit before rollback raised: %s" % exc)
        snapshot = self._snapshot_path()
        restored = False
        if snapshot.exists():
            aside = self._snap_dir() / ".replaced-bad"
            shutil.rmtree(aside, ignore_errors=True)
            try:
                if self.app_path.exists():
                    shutil.move(str(self.app_path), str(aside))
                shutil.copytree(snapshot, self.app_path, symlinks=True)
                restored = (tree_manifest(self.app_path) == tree_manifest(snapshot)
                            and read_version(self.app_path) == pending.get("snapshot_version"))
            except Exception as exc:
                self.log("updates guardian: restore raised: %s" % exc)
            if restored:
                shutil.rmtree(aside, ignore_errors=True)
            else:
                self.log("updates guardian: RESTORE FAILED verification; the replaced app "
                         "is kept at %s" % aside)
        else:
            self.log("updates guardian: no snapshot is retained, so there is nothing to "
                     "restore; the bad version stays installed and the block marker still lands")
        try:
            self.relaunch_app()
        except Exception as exc:
            self.log("updates guardian: relaunch after rollback raised: %s" % exc)
        if restored:
            ok2, checks2 = self.run_battery(pending.get("snapshot_version"))
            self.log("updates guardian: post-rollback battery for the restored %s: %s"
                     % (pending.get("snapshot_version"), "PASS" if ok2 else "FAIL"))
        blocked = self.state.setdefault("blocked", {})
        entry = blocked.get(bad_version) or {"rollbacks": 0}
        entry["rollbacks"] = int(entry.get("rollbacks", 0)) + 1
        entry["at"] = _iso(self.now_fn())
        entry["failure"] = failure
        blocked[bad_version] = entry
        marker = {"version": bad_version, "at": entry["at"], "failure": failure,
                  "rollbacks": entry["rollbacks"],
                  "restored_version": pending.get("snapshot_version") if restored else None}
        self._block_path(bad_version).write_text(json.dumps(marker, indent=1, sort_keys=True))
        self.log("updates guardian: block marker written for payload %s (rollback %d): %s"
                 % (bad_version, entry["rollbacks"], self._block_path(bad_version)))
        if entry["rollbacks"] >= 2:
            self.log("updates guardian: BLOCKED: payload %s has now failed the update battery "
                     "and been rolled back %d times; it is flagged in status and will keep "
                     "being rolled back, never silently accepted"
                     % (bad_version, entry["rollbacks"]))
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
                   "staged_at": _iso(now), "pre_start_token": self.app_start_token(),
                   "blocked_repeat": bool(blocked)}
        if blocked:
            self.log("updates guardian: payload %s is a BLOCKED version (rolled back %d "
                     "time(s), last at %s); the retained snapshot of %s is NOT overwritten "
                     "with the current app state"
                     % (version, blocked.get("rollbacks", 0), blocked.get("at"),
                        pending["snapshot_version"]))
        else:
            if self._take_snapshot(installed, version):
                pending["snapshot_version"] = self.state.get("snapshot_version")
        self.state["seen_identity"] = identity
        self.state["last_payload_version"] = version
        self.state["pending"] = pending
        self.state["last_action"] = "snapshot ahead of %s" % version
        self._save_state()

    def _maybe_verify_swap(self, pending, now):
        current = read_version(self.app_path)
        if current is None or current == pending.get("pre_version"):
            return
        token = self.app_start_token()
        if token is None or token == pending.get("pre_start_token"):
            self.log("updates guardian: installed version stamp changed to %s but the app "
                     "has not restarted yet; the battery waits for the new process" % current)
            return
        self.log("updates guardian: swap detected: installed Xod went %s to %s and restarted; "
                 "running the health battery" % (pending.get("pre_version"), current))
        ok, checks = self.run_battery(current)
        if ok:
            self.log("updates guardian: update accepted: Xod %s to %s passed the health "
                     "battery; the snapshot of %s is retained until the next payload"
                     % (pending.get("pre_version"), current, pending.get("snapshot_version")))
            self.state["pending"] = None
            self.state["last_action"] = "update accepted: %s" % current
        else:
            self._rollback(pending, checks)
            self.state["pending"] = None
        self._save_state()
