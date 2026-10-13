"""
What Xod did, in plain words, and how to undo it.

Every tool call goes through tools.execute_tool, which calls before() and after() here. Each
call becomes one activity entry ("Wrote notes.md", "Moved 12 files out of Downloads", "Ran a
command"). For the tools that change files, before() first saves what's needed to put things
back, and undo() does it:

  write / edit a file   the old file is copied aside (or noted as new, so undo removes it)
  move / organize       where each file came from (undo moves it back)
  trash                 a copy is kept (up to 1 GB; bigger items are still in the macOS Trash)
  new folder            removed by undo if it's still empty
  commands, AppleScript logged, but marked as not undoable

Undo refuses to overwrite a file that has changed since Xod touched it, unless forced.
Copies live in <data>/activity/ and are pruned after RETENTION_DAYS.
"""
import contextvars
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.activity")

ROOT = DATA_DIR / "activity"
SNAP_DIR = ROOT / "snapshots"
DB = DATA_DIR / "xod.db"
RETENTION_DAYS = 14
MAX_COPY_BYTES = 1024 ** 3

# Who asked: set by the chat / task runner around tool calls ("chat", "task:<id>", "routine:<id>").
origin = contextvars.ContextVar("xod_activity_origin", default="chat")

# Tools that only look at things: not worth a line in the log.
QUIET = {"computer_read_file", "computer_list_directory", "computer_directory_tree", "computer_find_files",
         "computer_check_job", "computer_list_jobs", "computer_hardware_overview", "computer_inspect_system",
         "computer_inspect_port", "fs_list_workspace_files", "fs_read_workspace_file", "fs_search_workspace",
         "xod_doctor", "computer_wait_for", "memory_search", "memory_list", "xod_tasks"}
NOT_UNDOABLE = {"computer_run_command": "Ran a command", "computer_run_applescript": "Ran an AppleScript",
                "computer_kill_process": "Stopped a process", "computer_start_job": "Started a background job",
                "computer_stop_job": "Stopped a background job", "computer_clipboard": "Used the clipboard",
                "computer_open_item": "Opened something", "computer_notify_user": "Showed a notification"}


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS activity (
        id TEXT PRIMARY KEY, ts REAL, origin TEXT, tool TEXT, title TEXT, detail TEXT,
        ok INTEGER, undo TEXT, undoable INTEGER, undone_at REAL, note TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS activity_ts ON activity(ts)")
    return c


def _sha(p: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
            if total > MAX_COPY_BYTES:
                return total
    return total


def _resolve(p: str, workspace: Optional[str]) -> Path:
    q = Path(os.path.expanduser(p or ""))
    return q if q.is_absolute() else Path(workspace or Path.home() / "Documents") / q


def _arg(a: Dict[str, Any], *names: str) -> str:
    return next((a[n] for n in names if a.get(n)), "")


# ---------------------------------------------------------------- before / after a tool call

def before(name: str, args: Dict[str, Any], workspace: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Saves what undo will need. Returns a context for after(), or None for quiet tools."""
    if name in QUIET:
        return None
    ctx: Dict[str, Any] = {"id": uuid.uuid4().hex[:12], "tool": name, "args": args, "ts": time.time(),
                           "origin": origin.get(), "ops": [], "workspace": workspace}
    snap = SNAP_DIR / ctx["id"]
    try:
        if name in ("computer_write_file", "computer_edit_file", "fs_write_workspace_file"):
            raw = _arg(args, "filepath", "file_path", "path")
            p = _resolve(raw, workspace) if name == "fs_write_workspace_file" else Path(os.path.expanduser(raw))
            if p.is_file():
                snap.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, snap / "before")
                ctx["ops"].append({"kind": "restore_file", "path": str(p), "copy": str(snap / "before")})
            elif not p.exists():
                ctx["ops"].append({"kind": "remove_created", "path": str(p)})
        elif name == "computer_manage_file":
            action, src, dst = args.get("action"), Path(os.path.expanduser(args.get("src") or "")), args.get("dst")
            if action == "trash" and src.exists():
                if _size(src) <= MAX_COPY_BYTES:
                    snap.mkdir(parents=True, exist_ok=True)
                    (shutil.copytree if src.is_dir() else shutil.copy2)(src, snap / src.name)
                    ctx["ops"].append({"kind": "untrash", "path": str(src), "copy": str(snap / src.name)})
                else:
                    ctx["note"] = "Too big to keep a copy; it's in the macOS Trash."
            elif action == "move" and dst:
                ctx["ops"].append({"kind": "move_back", "from": str(src), "to_hint": os.path.expanduser(dst)})
            elif action == "copy" and dst:
                ctx["ops"].append({"kind": "remove_copy", "src": str(src), "to_hint": os.path.expanduser(dst)})
            elif action == "mkdir" and not src.exists():
                ctx["ops"].append({"kind": "remove_empty_dir", "path": str(src)})
        elif name == "fs_move_file":
            src = _resolve(_arg(args, "source_path", "src", "source"), workspace)
            dst = _resolve(_arg(args, "destination_path", "dst", "destination"), workspace)
            ctx["ops"].append({"kind": "move_back", "from": str(src), "to_hint": str(dst)})
        elif name == "fs_organize_files":
            target = _resolve(_arg(args, "target_folder", "folder", "destination"), workspace)
            for f in args.get("file_paths") or args.get("files") or []:
                src = _resolve(f, workspace)
                ctx["ops"].append({"kind": "move_back", "from": str(src), "to_hint": str(target / src.name)})
            if not target.exists():
                ctx["ops"].append({"kind": "remove_empty_dir", "path": str(target)})
        elif name == "fs_create_folder":
            p = _resolve(_arg(args, "folder_path", "path", "name"), workspace)
            if not p.exists():
                ctx["ops"].append({"kind": "remove_empty_dir", "path": str(p)})
        elif name == "computer_batch_organize":
            d = Path(os.path.expanduser(_arg(args, "source_dir", "directory", "path") or "~/Downloads"))
            ctx["dir"] = str(d)
            ctx["before_names"] = sorted(e.name for e in d.iterdir()) if d.is_dir() else []
    except Exception as e:
        logger.warning(f"Couldn't prepare undo for {name}: {e}")
        ctx["note"] = f"Undo isn't available: {e}"
        ctx["ops"] = []
    return ctx


def after(ctx: Optional[Dict[str, Any]], result: Any) -> None:
    if not ctx:
        return
    ok = isinstance(result, dict) and not result.get("error") and result.get("success", True) is not False
    name, args = ctx["tool"], ctx["args"]
    ops: List[Dict[str, Any]] = ctx["ops"] if ok else []
    try:
        # Record what the files look like now, so undo can tell if someone changed them since.
        for op in ops:
            if op["kind"] in ("restore_file", "remove_created"):
                op["after_sha"] = _sha(Path(op["path"]))
            if op["kind"] in ("move_back", "remove_copy"):
                hint = Path(op["to_hint"])
                src_name = Path(op.get("from") or op.get("src")).name
                op["now_at"] = str(hint / src_name) if hint.is_dir() and (hint / src_name).exists() else str(hint)
        if name == "computer_batch_organize" and ok:
            d = Path(ctx["dir"])
            gone = [n for n in ctx["before_names"] if not (d / n).exists()]
            for n in gone:
                hit = next((sub / n for sub in d.iterdir() if sub.is_dir() and (sub / n).exists()), None)
                if hit:
                    ops.append({"kind": "move_back", "from": str(d / n), "now_at": str(hit)})
            for sub in d.iterdir():
                if sub.is_dir() and sub.name not in ctx["before_names"]:
                    ops.append({"kind": "remove_empty_dir", "path": str(sub)})
        if not ops:
            shutil.rmtree(SNAP_DIR / ctx["id"], ignore_errors=True)
        title, detail = describe(name, args, result, ok, ops)
        note = ctx.get("note") or ("" if ok else "Didn't work, nothing changed.")
        if (name in NOT_UNDOABLE or name.startswith("mcp__")) and ok:
            note = note or "Can't be undone automatically."
        with _conn() as c:
            c.execute("INSERT INTO activity VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (ctx["id"], ctx["ts"], ctx["origin"], name, title, detail, int(ok), json.dumps(ops),
                       int(bool(ops)), None, note))
    except Exception as e:
        logger.warning(f"Couldn't record activity for {name}: {e}")


def describe(name: str, args: Dict[str, Any], result: Any, ok: bool, ops: List[Dict[str, Any]]):
    base = lambda p: os.path.basename(str(p).rstrip("/")) or str(p)
    path = _arg(args, "filepath", "file_path", "path", "src", "source_path", "folder_path")
    moves = sum(1 for o in ops if o["kind"] == "move_back")
    titles = {
        "computer_write_file": f"Wrote {base(path)}", "fs_write_workspace_file": f"Wrote {base(path)}",
        "computer_edit_file": f"Edited {base(path)}",
        "computer_manage_file": {"trash": f"Moved {base(path)} to the Trash", "move": f"Moved {base(path)}",
                                 "copy": f"Copied {base(path)}", "mkdir": f"Made folder {base(path)}"}.get(args.get("action"), "Managed a file"),
        "fs_move_file": f"Moved {base(path)}", "fs_create_folder": f"Made folder {base(path)}",
        "fs_organize_files": f"Moved {moves} file{'s' if moves != 1 else ''} into {base(_arg(args, 'target_folder', 'folder'))}",
        "xod_make_report": f"Made a report: {str(args.get('title', ''))[:60]}",
        "agent_mail_send": f"Agent Mail to {str(args.get('to', ''))[:60]}",
        "computer_batch_organize": f"Sorted {moves} file{'s' if moves != 1 else ''} in {base(_arg(args, 'source_dir', 'directory') or '~/Downloads')}",
    }
    title = titles.get(name) or NOT_UNDOABLE.get(name) or name.replace("_", " ").capitalize()
    if name.startswith("mcp__"):
        _, server, tool = (name.split("__", 2) + ["", ""])[:3]
        title = f"Used {tool.replace('_', ' ')} ({server})"
        if isinstance(result, dict) and result.get("needs_approval"):
            title += ", waiting for your OK"
    if "search" in name and isinstance(args, dict) and args.get("query") and name not in titles:
        title = f"Searched for “{str(args['query'])[:70]}”"
    if name.startswith("browser_"):
        title = {"browser_navigate": f"Opened {args.get('url', 'a page')}", "browser_click": "Clicked on a page",
                 "browser_type": "Typed into a page"}.get(name, title)
    detail = ""
    if name in ("computer_run_command",):
        detail = str(args.get("command", ""))[:300]
    elif path:
        detail = str(path)[:300]
    elif isinstance(args, dict):
        detail = ", ".join(f"{k}: {str(v)[:60]}" for k, v in list(args.items())[:3] if k not in ("content", "password"))
    if not ok:
        err = result.get("error") if isinstance(result, dict) else str(result)
        detail = f"{detail}  ✗ {str(err)[:200]}".strip()
    return title, detail


# ---------------------------------------------------------------- reading and undoing

def recent(limit: int = 200, since: Optional[float] = None) -> List[Dict[str, Any]]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM activity WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
                         (since or 0, limit)).fetchall()
    return [{"id": r["id"], "ts": r["ts"], "origin": r["origin"], "tool": r["tool"], "title": r["title"],
             "detail": r["detail"], "ok": bool(r["ok"]), "undoable": bool(r["undoable"]) and not r["undone_at"],
             "undone_at": r["undone_at"], "note": r["note"]} for r in rows]


def _trash(p: Path) -> None:
    """Into the macOS Trash (recoverable), not deleted. macOS 14+ has /usr/bin/trash (no Finder permission needed)."""
    import subprocess
    if Path("/usr/bin/trash").exists():
        r = subprocess.run(["/usr/bin/trash", str(p)], capture_output=True, text=True, timeout=30)
    else:
        r = subprocess.run(["osascript", "-e", "on run argv", "-e", 'tell application "Finder" to delete (POSIX file (item 1 of argv) as alias)',
                            "-e", "end run", str(p)], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "couldn't move it to the Trash")


def _put_back(name: str, original: Path) -> None:
    """Out of the Trash and back where it was. Xod can't read the Trash itself (macOS keeps it private),
    so Finder does it; the first time, macOS asks whether Xod may control Finder."""
    import subprocess
    original.parent.mkdir(parents=True, exist_ok=True)
    script = ["on run argv", 'tell application "Finder"',
              "set hits to (every item of trash whose name is (item 1 of argv))",
              'if (count of hits) is 0 then error "It isn\'t in the Trash any more."',
              "move (item 1 of hits) to (POSIX file (item 2 of argv) as alias)",
              "end tell", "end run"]
    argv = ["osascript"] + [x for line in script for x in ("-e", line)] + [name, str(original.parent)]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr.strip() or "Finder couldn't put it back") + " You can use Put Back in the Trash.")


def undo(entry_id: str, force: bool = False) -> Dict[str, Any]:
    with _conn() as c:
        r = c.execute("SELECT * FROM activity WHERE id = ?", (entry_id,)).fetchone()
    if not r:
        return {"ok": False, "detail": "No such activity."}
    if r["undone_at"]:
        return {"ok": False, "detail": "Already undone."}
    ops = json.loads(r["undo"] or "[]")
    if not ops:
        return {"ok": False, "detail": r["note"] or "This one can't be undone automatically."}
    # Check first: never clobber something that changed after Xod's action.
    if not force:
        for op in ops:
            if op["kind"] in ("restore_file", "remove_created") and op.get("after_sha") and _sha(Path(op["path"])) not in (op["after_sha"], None):
                return {"ok": False, "conflict": True, "detail": f"{Path(op['path']).name} changed after Xod edited it. Undo anyway?"}
            if op["kind"] in ("move_back", "put_back") and Path(op["from"]).exists():
                return {"ok": False, "conflict": True, "detail": f"Something is already at {op['from']}. Undo anyway?"}
    done, problems = [], []
    # Newest change first; empty folders last, once the files are out of them.
    ordered = [o for o in reversed(ops) if o["kind"] != "remove_empty_dir"] + \
        sorted((o for o in ops if o["kind"] == "remove_empty_dir"), key=lambda o: -len(o["path"]))   # deepest first
    for op in ordered:
        try:
            k = op["kind"]
            if k == "restore_file":
                shutil.copy2(op["copy"], op["path"]); done.append(f"restored {Path(op['path']).name}")
            elif k == "remove_created":
                p = Path(op["path"])
                if p.exists():
                    _trash(p); done.append(f"moved new file {p.name} to the Trash")
            elif k == "untrash":
                dst = Path(op["path"])
                if dst.exists():
                    raise RuntimeError(f"{dst} exists again")
                (shutil.copytree if Path(op["copy"]).is_dir() else shutil.copy2)(op["copy"], dst)
                done.append(f"brought back {dst.name}")
            elif k == "move_back":
                src = Path(op.get("now_at") or op.get("to_hint"))
                if src.exists():
                    Path(op["from"]).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), op["from"]); done.append(f"moved {Path(op['from']).name} back")
            elif k == "put_back":
                _put_back(op["name"], Path(op["from"])); done.append(f"put {op['name']} back")
            elif k == "remove_copy":
                p = Path(op.get("now_at") or op["to_hint"])
                if p.exists():
                    _trash(p); done.append(f"moved the copy {p.name} to the Trash")
            elif k == "remove_empty_dir":
                p = Path(op["path"])
                if p.is_dir() and not any(p.iterdir()):
                    p.rmdir(); done.append(f"removed empty folder {p.name}")
        except Exception as e:
            problems.append(f"{op['kind']}: {e}")
    with _conn() as c:
        c.execute("UPDATE activity SET undone_at = ? WHERE id = ?", (time.time(), entry_id))
    return {"ok": not problems, "detail": "; ".join(done + problems) or "Nothing left to undo."}


def prune() -> None:
    cutoff = time.time() - RETENTION_DAYS * 86400
    try:
        with _conn() as c:
            old = [r["id"] for r in c.execute("SELECT id FROM activity WHERE ts < ?", (cutoff,))]
            c.execute("DELETE FROM activity WHERE ts < ?", (cutoff,))
        for i in old:
            shutil.rmtree(SNAP_DIR / i, ignore_errors=True)
    except Exception as e:
        logger.warning(f"activity prune failed: {e}")
