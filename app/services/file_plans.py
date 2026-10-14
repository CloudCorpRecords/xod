"""
File plans: Xod shows exactly what it will move, rename or trash, you press Apply, and one Undo
puts it all back.

A plan is a list of operations:

  {"op": "move",  "src": "~/Downloads/a.pdf", "dst": "~/Downloads/Documents/a.pdf"}   (also renames)
  {"op": "trash", "src": "~/Downloads/Setup.dmg"}

Plans come from the built-in planners below (tidy a folder by kind or month, screenshots, old
installers, old downloads, duplicates, leftover node_modules) or from the agent (custom), and every
plan goes through the same checks before anyone sees it:

  - only inside your home folder (or an external drive under /Volumes)
  - never ~/Library, hidden folders, Xod's own data or code, or the home/Desktop/Documents/... folders
    themselves; never into itself
  - name clashes get " 2", " 3" (nothing is ever overwritten)
  - iCloud files that aren't downloaded are left alone (reading them would download them)

Nothing changes until apply(). Applying records one Activity entry whose undo moves every file
back, asks Finder to put trashed items back, and removes the folders the plan made.
Set XOD_FILES_HOME to point all of this at another folder (the tests do).
"""
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.paths import APP_ROOT, DATA_DIR

logger = logging.getLogger("xod.file_plans")

PLANS_DIR = DATA_DIR / "plans"
PLAN_TTL = 6 * 3600
MAX_OPS = 5000
SF_DATALESS = 0x40000000          # an iCloud file that isn't on this Mac (st_flags)

# ------------------------------------------------------------------ where things are


def home() -> Path:
    return Path(os.environ.get("XOD_FILES_HOME") or Path.home()).expanduser().resolve()


def expand(p: str) -> Path:
    p = str(p or "").strip()
    if p == "~" or p.startswith("~/"):
        p = str(home()) + p[1:]
    q = Path(p).expanduser()
    if not q.is_absolute():
        q = home() / q
    return Path(os.path.normpath(str(q)))


def _inside(path: Any, folder: Any) -> bool:
    """path is folder or under it (Windows: \\ and any capitalisation, like the file system itself)."""
    s, f = os.path.normcase(str(path)), os.path.normcase(str(folder)).rstrip("\\/")
    return s == f or s.startswith(f + os.sep)


def tilde(p: Any) -> str:
    s, h = str(p), str(home())
    return "~" + s[len(h):].replace(os.sep, "/") if _inside(s, h) else s


STANDARD = ("Desktop", "Documents", "Downloads", "Pictures", "Movies", "Music", "Applications", "Library", "Public",
            "Sites", "Developer", "iCloud Drive (Archive)")


def check_path(p: Path, role: str = "src") -> Optional[str]:
    """Why Xod may not touch this path, or None if it may."""
    h = home()
    s = str(p)
    in_home = _inside(s, h)
    if not in_home and not s.startswith("/Volumes/"):
        return "it's outside your home folder"
    rel = Path(s[len(str(h)) + 1:]) if in_home and s != str(h) else None
    if rel is None and in_home:
        return "that's your home folder itself"
    parts = rel.parts if rel else Path(s).parts[3:]
    if rel and parts[0] == "Library":
        return "~/Library holds app data and settings"
    if any(x.startswith(".") for x in parts):
        return "it's hidden (hidden files belong to apps and tools)"
    for guarded in (DATA_DIR, APP_ROOT):
        if _inside(s, guarded) or _inside(guarded, s):
            return "that's Xod's own data or code"
    if role == "src" and rel and len(parts) == 1 and parts[0] in STANDARD:
        return f"~/{parts[0]} is one of your main folders"
    if role == "src" and any(x.endswith((".app", ".photoslibrary", ".musiclibrary", ".tvlibrary", ".fcpbundle")) for x in parts[:-1]):
        return "it's inside an app or library bundle"
    return None


def is_dataless(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_flags", 0) & SF_DATALESS)


def item_size(p: Path, budget: float = 2.0) -> int:
    try:
        st = p.lstat()
    except OSError:
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size
    total, end = 0, time.time() + budget
    for root, dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
        if time.time() > end:
            break
    return total


def fmt_bytes(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("bytes", "KB") else f"{n:.1f} {unit}".replace(".0 ", " ")
        n /= 1024
    return f"{n:.1f} TB"


# ------------------------------------------------------------------ kinds

KINDS: List[Tuple[str, Tuple[str, ...]]] = [
    ("Installers", (".dmg", ".pkg", ".mpkg", ".exe", ".msi", ".msix")),
    ("Archives", (".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar")),
    ("Documents", (".pdf", ".doc", ".docx", ".pages", ".txt", ".rtf", ".md", ".odt", ".epub")),
    ("Spreadsheets", (".xlsx", ".xls", ".csv", ".numbers", ".tsv", ".ods")),
    ("Presentations", (".pptx", ".ppt", ".key", ".odp")),
    ("Images", (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".tiff", ".tif", ".bmp", ".svg", ".ico", ".raw", ".cr2", ".nef", ".dng")),
    ("Videos", (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")),
    ("Audio", (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aiff", ".aif")),
    ("Design", (".fig", ".sketch", ".psd", ".ai", ".xd", ".indd", ".afdesign", ".afphoto")),
    ("Code", (".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".html", ".css", ".sh", ".swift", ".go", ".rs", ".java", ".rb", ".php", ".ipynb", ".yaml", ".yml", ".sql")),
    ("Fonts", (".ttf", ".otf", ".woff", ".woff2")),
    ("Calendar and contacts", (".ics", ".vcf")),
]
_EXT_KIND = {ext: kind for kind, exts in KINDS for ext in exts}
SCREENSHOT_RE = re.compile(r"^(Screen ?Shot|Screenshot|Screen Recording|CleanShot|Capture d.écran|Bildschirmfoto)\b", re.I)
PARTIAL = (".crdownload", ".download", ".part", ".partial", ".tmp", ".opdownload")


def kind_of(p: Path) -> Optional[str]:
    if SCREENSHOT_RE.match(p.name):
        return "Screenshots"
    return _EXT_KIND.get(p.suffix.lower())


def _loose_files(folder: Path, min_age_sec: int = 120) -> List[Tuple[Path, os.stat_result]]:
    out, now = [], time.time()
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return out
    for e in entries:
        if e.name.startswith(".") or e.name.lower().endswith(PARTIAL):
            continue
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode) or now - st.st_mtime < min_age_sec:
            continue
        out.append((Path(e.path), st))
    return out


# ------------------------------------------------------------------ planners (nothing changes here)

def plan_tidy(folder: str = "~/Downloads", by: str = "kind") -> Dict[str, Any]:
    root = expand(folder)
    why = check_path(root / "x", "dst")
    if why or not root.is_dir():
        return _empty(f"Tidy {tilde(root)}", why or f"{tilde(root)} isn't a folder")
    ops = []
    for p, st in _loose_files(root):
        if by == "date":
            sub = time.strftime("%Y-%m", time.localtime(st.st_mtime))
        else:
            sub = kind_of(p)
            if not sub:
                continue        # unknown kinds stay where they are
        ops.append({"op": "move", "src": str(p), "dst": str(root / sub / p.name), "bytes": st.st_size})
    how = "by month" if by == "date" else "by kind"
    return build(f"Tidy {tilde(root)}", ops, f"Sort the loose files in {tilde(root)} into folders {how}.", source="tidy",
                 args={"folder": tilde(root), "by": by})


def plan_screenshots(sources: Optional[List[str]] = None, dest: str = "~/Pictures/Screenshots") -> Dict[str, Any]:
    target = expand(dest)
    ops = []
    for src in sources or ["~/Desktop", "~/Downloads"]:
        for p, st in _loose_files(expand(src)):
            if SCREENSHOT_RE.match(p.name):
                month = time.strftime("%Y-%m", time.localtime(st.st_mtime))
                ops.append({"op": "move", "src": str(p), "dst": str(target / month / p.name), "bytes": st.st_size})
    return build("File away screenshots", ops, f"Move screenshots into {tilde(target)}, one folder per month.",
                 source="screenshots", args={"sources": sources, "dest": dest})


def plan_installers(folder: str = "~/Downloads", min_age_days: float = 1) -> Dict[str, Any]:
    root = expand(folder)
    ops = []
    for p, st in _loose_files(root, min_age_sec=int(min_age_days * 86400)):
        ext = p.suffix.lower()
        # Windows: .msi/.msix are always installers; an .exe only when its name says so (others may be portable apps).
        if ext in (".dmg", ".pkg", ".mpkg", ".msi", ".msix") or (ext == ".exe" and re.search(r"setup|install", p.name, re.I)):
            ops.append({"op": "trash", "src": str(p), "bytes": st.st_size})
    return build("Clear out old installers", ops,
                 f"Move app installers (.dmg, .pkg, .msi, setup .exe) in {tilde(root)} to the Trash. Apps you installed stay installed.",
                 source="installers", args={"folder": folder})


def _last_touched(p: Path, st: os.stat_result) -> float:
    return max(st.st_mtime, getattr(st, "st_birthtime", 0) or 0, st.st_atime if p.is_dir() else 0)


def plan_old_downloads(days: int = 90, action: str = "archive", folder: str = "~/Downloads") -> Dict[str, Any]:
    root = expand(folder)
    cutoff = time.time() - days * 86400
    archive = root / "Old downloads"
    ops = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        entries = []
    tidy_names = {k for k, _ in KINDS} | {"Screenshots", "Old downloads"}
    for e in entries:
        if e.name.startswith(".") or e.name in tidy_names or e.name.lower().endswith(PARTIAL):
            continue
        p = Path(e.path)
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        if _last_touched(p, st) > cutoff:
            continue
        size = item_size(p, 0.5)
        if action == "trash":
            ops.append({"op": "trash", "src": str(p), "bytes": size})
        else:
            ops.append({"op": "move", "src": str(p), "dst": str(archive / p.name), "bytes": size})
    verb = "Move to the Trash" if action == "trash" else f"Move into {tilde(archive)}"
    return build(f"Old downloads (over {days} days)", ops, f"{verb}: everything in {tilde(root)} untouched for {days}+ days.",
                 source="old_downloads", args={"days": days, "action": action, "folder": folder})


def _quick_hash(p: Path, size: int) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(p, "rb") as f:
        h.update(f.read(65536))
        if size > 131072:
            f.seek(-65536, os.SEEK_END)
            h.update(f.read(65536))
    return h.hexdigest()


def _full_hash(p: Path) -> str:
    h = hashlib.blake2b(digest_size=20)
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_files(roots: Iterable[Path], min_size: int = 0, budget: float = 6.0, max_files: int = 60000):
    """Files under these folders, skipping hidden ones, bundles, node_modules and iCloud-only files."""
    end, n = time.time() + budget, 0
    for root in roots:
        if not root.is_dir():
            continue
        stack = [root]
        while stack:
            d = stack.pop()
            try:
                it = list(os.scandir(d))
            except OSError:
                continue
            for e in it:
                if e.name.startswith(".") or e.name == "node_modules":
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    if not e.name.endswith((".app", ".photoslibrary", ".musiclibrary", ".tvlibrary", ".fcpbundle", ".bundle", ".framework")):
                        stack.append(Path(e.path))
                elif stat.S_ISREG(st.st_mode) and st.st_size >= min_size and not is_dataless(st):
                    yield Path(e.path), st
                    n += 1
                if n >= max_files or time.time() > end:
                    return


def find_duplicates(folders: Optional[List[str]] = None, min_size_mb: float = 1, budget: float = 10.0) -> List[Dict[str, Any]]:
    roots = [expand(f) for f in (folders or ["~/Downloads", "~/Desktop", "~/Documents", "~/Pictures", "~/Movies", "~/Music"])]
    by_size: Dict[int, List[Path]] = defaultdict(list)
    for p, st in walk_files(roots, min_size=int(min_size_mb * 1024 * 1024), budget=budget * 0.5):
        by_size[st.st_size].append(p)
    end = time.time() + budget * 0.5
    groups = []
    for size, paths in sorted(by_size.items(), reverse=True):
        if len(paths) < 2:
            continue
        quick: Dict[str, List[Path]] = defaultdict(list)
        for p in paths:
            try:
                quick[_quick_hash(p, size)].append(p)
            except OSError:
                pass
        for same in quick.values():
            if len(same) < 2:
                continue
            full: Dict[str, List[Path]] = defaultdict(list)
            for p in same:
                try:
                    full[_full_hash(p)].append(p)
                except OSError:
                    pass
            for copies in full.values():
                if len(copies) > 1:
                    keep = _keeper(copies)
                    groups.append({"size": size, "keep": str(keep), "copies": [str(c) for c in copies if c != keep]})
        if time.time() > end:
            break
    return groups


def _keeper(copies: List[Path]) -> Path:
    """Keep the copy that's been filed somewhere (not Downloads/Desktop), then the one without '(1)'/' copy', then the oldest."""
    def score(p: Path):
        s = str(p)
        loose = any(f"/{d}/" in s for d in ("Downloads", "Desktop"))
        copyish = bool(re.search(r"(\(\d+\)| copy( \d+)?| \d+)$", p.stem))
        try:
            born = p.stat().st_birthtime
        except (OSError, AttributeError):
            born = 0
        return (loose, copyish, born, len(s))
    return min(copies, key=score)


def plan_duplicates(folders: Optional[List[str]] = None, min_size_mb: float = 1) -> Dict[str, Any]:
    groups = find_duplicates(folders, min_size_mb)
    ops = [{"op": "trash", "src": c, "bytes": g["size"], "note": f"same as {tilde(g['keep'])}"} for g in groups for c in g["copies"]]
    return build("Remove duplicate files", ops, "Move exact duplicates to the Trash, keeping one copy of each (the one that's filed away, or the oldest).",
                 source="duplicates", args={"folders": folders, "min_size_mb": min_size_mb})


def find_node_modules(roots: Optional[List[str]] = None, idle_days: int = 30, budget: float = 6.0) -> List[Dict[str, Any]]:
    """node_modules folders in projects nobody has touched for a while (npm install brings them back)."""
    found, end = [], time.time() + budget
    cutoff = time.time() - idle_days * 86400
    for r in roots or ["~"]:
        stack = [(expand(r), 0)]
        while stack and time.time() < end:
            d, depth = stack.pop()
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                if not e.is_dir(follow_symlinks=False) or (e.name.startswith(".")) or (depth == 0 and e.name == "Library"):
                    continue
                p = Path(e.path)
                if e.name == "node_modules":
                    if check_path(p) is None and (p.parent / "package.json").exists():
                        try:
                            touched = max((p.parent / n).stat().st_mtime for n in os.listdir(p.parent) if n != "node_modules")
                        except (OSError, ValueError):
                            touched = time.time()
                        if touched < cutoff:
                            found.append({"path": str(p), "project": str(p.parent), "idle_days": int((time.time() - touched) / 86400)})
                    continue
                if depth < 6 and not e.name.endswith((".app", ".photoslibrary", ".bundle")):
                    stack.append((p, depth + 1))
    return found


def plan_dev_leftovers(roots: Optional[List[str]] = None, idle_days: int = 30) -> Dict[str, Any]:
    ops = [{"op": "trash", "src": f["path"], "bytes": item_size(Path(f["path"]), 1.0),
            "note": f"project untouched for {f['idle_days']} days; `npm install` brings it back"} for f in find_node_modules(roots, idle_days)]
    return build("Clear old node_modules", ops, f"Move the node_modules folders of projects untouched for {idle_days}+ days to the Trash. "
                 "Your code isn't touched; `npm install` brings them back.", source="dev_leftovers", args={"roots": roots, "idle_days": idle_days})


def plan_custom(ops: List[Dict[str, Any]], title: str = "", why: str = "") -> Dict[str, Any]:
    clean = []
    for o in ops or []:
        if not isinstance(o, dict):
            continue
        kind = str(o.get("op") or o.get("action") or "move").lower()
        kind = {"rename": "move", "delete": "trash", "remove": "trash"}.get(kind, kind)
        src = o.get("src") or o.get("source") or o.get("from") or o.get("path")
        dst = o.get("dst") or o.get("destination") or o.get("to")
        if kind == "move" and dst and src and (str(dst).endswith("/") or expand(dst).is_dir()):
            dst = str(expand(dst) / os.path.basename(str(src).rstrip("/")))   # "into this folder"
        clean.append({"op": kind, "src": src, "dst": dst, "note": o.get("note") or o.get("why")})
    return build(title or "Organize files", clean, why or "", source="custom")


PLANNERS = {"tidy": plan_tidy, "screenshots": plan_screenshots, "installers": plan_installers,
            "old_downloads": plan_old_downloads, "duplicates": plan_duplicates, "dev_leftovers": plan_dev_leftovers}


# ------------------------------------------------------------------ building and checking a plan

def _empty(title: str, why: str) -> Dict[str, Any]:
    return {"id": None, "title": title, "ops": [], "skipped": [{"src": "", "why": why}], "summary": _summary([]), "why": why}


def build(title: str, raw_ops: List[Dict[str, Any]], why: str = "", source: str = "custom",
          args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ops, skipped, taken = [], [], set()
    for o in raw_ops[:MAX_OPS]:
        kind = o.get("op")
        src = expand(o.get("src") or "")
        if kind not in ("move", "trash"):
            skipped.append({"src": tilde(src), "why": f"unknown action {kind!r}"}); continue
        reason = check_path(src, "src")
        if not reason and not os.path.lexists(src):
            reason = "it isn't there"
        if not reason:
            try:
                if is_dataless(src.lstat()):
                    reason = "it's in iCloud and not downloaded"
            except OSError:
                reason = "it can't be read"
        dst = None
        if not reason and kind == "move":
            if not o.get("dst"):
                reason = "no destination"
            else:
                dst = expand(o["dst"])
                reason = check_path(dst, "dst")
                if not reason and (dst == src):
                    reason = "it's already there"
                if not reason and dst != src and _inside(dst, src):
                    reason = "can't move a folder into itself"
                if not reason:
                    dst = _free_name(dst, taken)
        if reason:
            skipped.append({"src": tilde(src), "why": reason}); continue
        size = o.get("bytes")
        if size is None:
            size = item_size(src, 0.3)
        op = {"op": kind, "src": str(src), "bytes": int(size or 0)}
        if dst:
            op["dst"] = str(dst)
            taken.add(str(dst))
        if o.get("note"):
            op["note"] = str(o["note"])[:160]
        ops.append(op)
    plan = {"id": uuid.uuid4().hex[:12] if ops else None, "title": title, "why": why, "source": source, "args": args or {},
            "created": time.time(), "ops": ops, "skipped": skipped[:50], "summary": _summary(ops)}
    if ops:
        _save(plan)
    return plan


def _free_name(dst: Path, taken: set) -> Path:
    if not os.path.lexists(dst) and str(dst) not in taken:
        return dst
    stem, suf = (dst.stem, dst.suffix) if dst.suffix and len(dst.suffix) <= 8 else (dst.name, "")
    for i in range(2, 1000):
        cand = dst.with_name(f"{stem} {i}{suf}")
        if not os.path.lexists(cand) and str(cand) not in taken:
            return cand
    return dst.with_name(f"{stem} {uuid.uuid4().hex[:6]}{suf}")


def group_key(o: Dict[str, Any]) -> str:
    """Which group of the plan card an operation is in: "trash", "rename", or the destination folder."""
    if o["op"] == "trash":
        return "trash"
    return "rename" if Path(o["dst"]).parent == Path(o["src"]).parent else str(Path(o["dst"]).parent)


def _summary(ops: List[Dict[str, Any]]) -> Dict[str, Any]:
    moves = [o for o in ops if o["op"] == "move"]
    trash = [o for o in ops if o["op"] == "trash"]
    renames = [o for o in moves if group_key(o) == "rename"]
    groups: Dict[str, Dict[str, Any]] = {}
    for o in ops:
        if o["op"] == "trash":
            key, label = "trash", "To the Trash"
        elif group_key(o) == "rename":
            key, label = "rename", "Renamed"
        else:
            key = str(Path(o["dst"]).parent)
            label = tilde(key)
        g = groups.setdefault(key, {"key": key, "label": label, "count": 0, "bytes": 0, "items": []})
        g["count"] += 1
        g["bytes"] += o["bytes"]
        if len(g["items"]) < 40:
            g["items"].append({"name": Path(o["src"]).name, "to": Path(o["dst"]).name if o.get("dst") else None,
                               "from": tilde(Path(o["src"]).parent), "bytes": o["bytes"], "note": o.get("note")})
    new_folders = sorted({str(Path(o["dst"]).parent) for o in moves if not Path(o["dst"]).parent.exists()})
    return {"moves": len(moves) - len(renames), "renames": len(renames), "trash": len(trash),
            "trash_bytes": sum(o["bytes"] for o in trash), "move_bytes": sum(o["bytes"] for o in moves),
            "new_folders": [tilde(f) for f in new_folders][:30], "groups": sorted(groups.values(), key=lambda g: (g["key"] == "trash", -g["count"]))}


def describe(plan: Dict[str, Any]) -> str:
    s = plan["summary"]
    bits = []
    if s["moves"]:
        bits.append(f"move {s['moves']} item{'s' if s['moves'] != 1 else ''}"
                    + (f" into {len([g for g in s['groups'] if g['key'] not in ('trash', 'rename')])} folders" if s["moves"] > 1 else ""))
    if s["renames"]:
        bits.append(f"rename {s['renames']}")
    if s["trash"]:
        bits.append(f"put {s['trash']} in the Trash ({fmt_bytes(s['trash_bytes'])})")
    return (", ".join(bits) or "nothing to do").capitalize()


def for_model(plan: Dict[str, Any]) -> Dict[str, Any]:
    """What the agent sees: the plan exists, nothing has changed yet, the user decides."""
    if not plan.get("ops"):
        why = "; ".join(f"{s['src']}: {s['why']}" for s in plan.get("skipped", [])[:8]) or "nothing matched"
        return {"success": True, "kind": "file_plan", "empty": True, "title": plan["title"],
                "message": f"Nothing to do for '{plan['title']}' ({why})."}
    groups = [f"{g['label']}: {g['count']} ({', '.join(i['name'] + (' → ' + i['to'] if i['to'] and i['to'] != i['name'] else '') for i in g['items'][:4])}"
              + (", …" if g["count"] > 4 else "") + ")" for g in plan["summary"]["groups"][:10]]
    return {"success": True, "needs_approval": True, "kind": "file_plan", "approval_id": plan["id"], "plan_id": plan["id"],
            "title": plan["title"], "why": plan.get("why", ""), "summary": plan["summary"], "skipped": plan["skipped"][:12],
            "message": f"Plan ready, waiting for the user: {describe(plan)}. NOTHING has moved yet. The user sees the full list "
                       f"with an Apply button (and one-click Undo after). Tell them what the plan does in a sentence or two and that "
                       f"they can press Apply; don't claim it's done.",
            "groups": groups}


# ------------------------------------------------------------------ storing

def _path(pid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", pid or ""):
        raise ValueError("bad plan id")
    return PLANS_DIR / f"{pid}.json"


def _save(plan: Dict[str, Any]) -> None:
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    _path(plan["id"]).write_text(json.dumps(plan))
    for old in PLANS_DIR.glob("*.json"):
        try:
            if time.time() - old.stat().st_mtime > PLAN_TTL * 4:
                old.unlink()
        except OSError:
            pass


def get(pid: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(_path(pid).read_text())
    except (OSError, ValueError):
        return None


def public(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {k: plan.get(k) for k in ("id", "title", "why", "source", "summary", "skipped", "created", "status", "result")}


def cancel(pid: str) -> Dict[str, Any]:
    plan = get(pid)
    if not plan or plan.get("status"):
        return {"ok": False, "detail": "That plan was already handled."}
    plan["status"] = "cancelled"
    _path(pid).write_text(json.dumps(plan))
    return {"ok": True}


# ------------------------------------------------------------------ applying

def _trash(p: Path) -> None:
    from app.services.activity import _trash as trash
    trash(p)


def apply(pid: str, exclude_groups: Optional[List[str]] = None) -> Dict[str, Any]:
    """Runs the plan (minus any groups the user unticked) and records one undoable Activity entry."""
    plan = get(pid)
    if not plan:
        return {"ok": False, "detail": "That plan has expired. Ask Xod to make it again."}
    if plan.get("status"):
        return {"ok": False, "detail": f"That plan was already {plan['status']}."}
    if time.time() - plan["created"] > PLAN_TTL:
        return {"ok": False, "detail": "That plan is a few hours old and things may have changed. Ask Xod to make it again."}
    plan["status"] = "applying"
    _path(pid).write_text(json.dumps(plan))
    skip = set(exclude_groups or [])
    undo_ops: List[Dict[str, Any]] = []
    made_dirs: List[str] = []
    done = {"moved": 0, "renamed": 0, "trashed": 0, "freed": 0}
    failed = []
    for o in plan["ops"]:
        src = Path(o["src"])
        key = group_key(o)
        if key in skip:
            continue
        try:
            # Checked again now: the plan may be a while old.
            why = check_path(src, "src")
            if why:
                raise RuntimeError(why)
            if not os.path.lexists(src):
                raise RuntimeError("it isn't there any more")
            if o["op"] == "move":
                dst = Path(o["dst"])
                why = check_path(dst, "dst")
                if why:
                    raise RuntimeError(why)
                if os.path.lexists(dst):
                    dst = _free_name(dst, set())
                missing = []
                d = dst.parent
                while not d.exists():
                    missing.append(d)
                    d = d.parent
                dst.parent.mkdir(parents=True, exist_ok=True)
                made_dirs += [str(m) for m in missing]
                shutil.move(str(src), str(dst))
                undo_ops.append({"kind": "move_back", "from": str(src), "now_at": str(dst)})
                done["renamed" if key == "rename" else "moved"] += 1
            else:
                _trash(src)
                undo_ops.append({"kind": "put_back", "from": str(src), "name": src.name})
                done["trashed"] += 1
                done["freed"] += o["bytes"]
        except Exception as e:
            failed.append({"name": src.name, "why": str(e)[:200]})
    undo_ops += [{"kind": "remove_empty_dir", "path": d} for d in made_dirs]
    title = _done_title(plan, done)
    entry_id = _record(plan, title, undo_ops, failed)
    plan["status"] = "applied"
    plan["result"] = {**done, "failed": failed, "activity_id": entry_id}
    _path(pid).write_text(json.dumps(plan))
    return {"ok": not failed or bool(undo_ops), "title": title, **done, "failed": failed[:20], "activity_id": entry_id,
            "detail": title + (f". {len(failed)} couldn't be done." if failed else ".")}


def _done_title(plan: Dict[str, Any], done: Dict[str, int]) -> str:
    bits = []
    if done["moved"]:
        bits.append(f"moved {done['moved']} item{'s' if done['moved'] != 1 else ''}")
    if done["renamed"]:
        bits.append(f"renamed {done['renamed']}")
    if done["trashed"]:
        bits.append(f"put {done['trashed']} in the Trash ({fmt_bytes(done['freed'])})")
    return f"{plan['title']}: {', '.join(bits) or 'nothing changed'}"


def _record(plan: Dict[str, Any], title: str, ops: List[Dict[str, Any]], failed: List[Dict[str, Any]]) -> Optional[str]:
    from app.services import activity
    entry = uuid.uuid4().hex[:12]
    note = f"{len(failed)} item(s) couldn't be done: " + "; ".join(f"{f['name']} ({f['why']})" for f in failed[:5]) if failed else ""
    if any(o["kind"] == "put_back" for o in ops):
        note = (note + " " if note else "") + "Undo asks Finder to put trashed items back (or use Put Back in the Trash)."
    try:
        with activity._conn() as c:
            c.execute("INSERT INTO activity VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (entry, time.time(), activity.origin.get(), "files_plan", title[:200], plan.get("why", "")[:300],
                       1, json.dumps(ops), int(bool(ops)), None, note))
        return entry
    except Exception as e:
        logger.warning(f"couldn't record the plan in Activity: {e}")
        return None
