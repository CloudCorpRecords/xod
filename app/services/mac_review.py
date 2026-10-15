"""
Looking around your Mac: a full review (what's using the space, what's cluttered, what could go),
a look at one folder, Spotlight search with filters, and a peek inside files.

Everything here only reads. Each finding in the review carries a ready-made plan (file_plans.py)
that you see in full and apply with one click, and undo with another.

Reads are careful: iCloud files that aren't downloaded are never opened (that would download them),
hidden folders and ~/Library are left alone, and every scan has a time budget.
"""
import asyncio
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services import file_plans as fp
from app.services.file_plans import expand, fmt_bytes, tilde

DAY = 86400


# ------------------------------------------------------------------ small helpers

def _du_kb(path: Path, timeout: float) -> Optional[int]:
    """Folder size in KB from du (fast, in C). None if it took too long or wasn't allowed."""
    if sys.platform == "win32":     # no du: add it up ourselves, within the same time budget
        total, t0, stack = 0, time.time(), [str(path)]
        while stack:
            if time.time() - t0 > timeout:
                return None
            try:
                with os.scandir(stack.pop()) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                            elif e.is_file(follow_symlinks=False):
                                total += e.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
            except OSError:
                continue
        return total // 1024
    try:
        r = subprocess.run(["du", "-skx", str(path)], capture_output=True, text=True, timeout=timeout)
        m = re.match(r"(\d+)", r.stdout)
        return int(m.group(1)) if m else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _mdls(paths: List[str], names: List[str]) -> List[Dict[str, Any]]:
    """Spotlight metadata for several files in one call."""
    if not paths:
        return []
    if sys.platform == "win32":     # no Spotlight: callers fall back to what the file system says
        return [{} for _ in paths]
    argv = ["mdls"] + [x for n in names for x in ("-name", n)] + paths
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout
    except (subprocess.TimeoutExpired, OSError):
        return [{} for _ in paths]
    records, cur = [], {}
    lines = out.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(kMD\w+)\s*=\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if key in cur:
                records.append(cur); cur = {}
            if val == "(":
                items = []
                i += 1
                while i < len(lines) and lines[i].strip() != ")":
                    items.append(lines[i].strip().rstrip(",").strip('"'))
                    i += 1
                cur[key] = items
            else:
                cur[key] = None if val == "(null)" else val.strip('"')
        i += 1
    if cur:
        records.append(cur)
    while len(records) < len(paths):
        records.append({})
    return records[:len(paths)]


def _mdfind(query: str, onlyin: Optional[Path] = None, limit: int = 200, timeout: float = 8) -> List[str]:
    if sys.platform == "win32":     # no Spotlight: callers walk the folders instead
        return []
    argv = ["mdfind"] + (["-onlyin", str(onlyin)] if onlyin else []) + [query]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    return [l for l in out.splitlines() if l.strip()][: limit * 3]


def _ago(ts: float) -> str:
    d = (time.time() - ts) / DAY
    if d < 1:
        return "today"
    if d < 2:
        return "yesterday"
    if d < 60:
        return f"{int(d)} days ago"
    if d < 730:
        return f"{int(d / 30)} months ago"
    return f"{int(d / 365)} years ago"


def _visible_ok(p: str) -> bool:
    return fp.check_path(Path(p), "dst") is None


# ------------------------------------------------------------------ the full review

def _disk() -> Dict[str, Any]:
    u = shutil.disk_usage(str(fp.home()))
    return {"total": u.total, "used": u.used, "free": u.free, "used_pct": round(100 * u.used / u.total, 1)}


def _folder_sizes(budget: float) -> List[Dict[str, Any]]:
    h = fp.home()
    try:
        children = [Path(e.path) for e in os.scandir(h) if not e.name.startswith(".") and e.is_dir(follow_symlinks=False)]
    except OSError:
        children = []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as ex:
        sizes = list(ex.map(lambda p: _du_kb(p, budget), children))
    out = [{"path": tilde(p), "name": p.name, "bytes": (kb or 0) * 1024, "complete": kb is not None}
           for p, kb in zip(children, sizes)]
    return sorted(out, key=lambda x: -x["bytes"])


def _big_files(limit: int = 15, min_mb: int = 200) -> List[Dict[str, Any]]:
    h = fp.home()
    paths = [p for p in _mdfind(f"kMDItemFSSize > {min_mb * 1024 * 1024}", h) if _visible_ok(p)]
    if not paths:     # Spotlight off, or not indexed (e.g. a test folder): walk instead
        paths = [str(p) for p, _ in fp.walk_files([h], min_size=min_mb * 1024 * 1024, budget=4)]
    rows = []
    for p in paths:
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and not fp.is_dataless(st):
            rows.append((p, st))
    rows.sort(key=lambda r: -r[1].st_size)
    rows = rows[:limit]
    meta = _mdls([p for p, _ in rows], ["kMDItemLastUsedDate"])
    out = []
    for (p, st), m in zip(rows, meta):
        used = m.get("kMDItemLastUsedDate")
        out.append({"path": tilde(p), "name": os.path.basename(p), "bytes": st.st_size,
                    "modified": _ago(st.st_mtime), "last_opened": used[:10] if used else None})
    return out


def _scan_folder(folder: Path) -> Dict[str, Any]:
    files = fp._loose_files(folder, min_age_sec=0)
    try:
        subdirs = [e for e in os.scandir(folder) if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")]
    except OSError:
        subdirs = []
    kinds = Counter((fp.kind_of(p) or "Other") for p, _ in files)
    return {"path": tilde(folder), "loose_files": len(files), "folders": len(subdirs),
            "bytes": sum(st.st_size for _, st in files), "kinds": dict(kinds.most_common()),
            "oldest": _ago(min((st.st_mtime for _, st in files), default=time.time()))}


def review(budget: float = 25.0) -> Dict[str, Any]:
    """The whole-Mac review. Reads only; every finding comes with a plan id you can open and apply."""
    t0 = time.time()
    disk = _disk()
    folders = _folder_sizes(timeout_for(budget, 0.45))
    big = _big_files()
    downloads, desktop = expand("~/Downloads"), expand("~/Desktop")
    dl = _scan_folder(downloads) if downloads.is_dir() else None
    dt = _scan_folder(desktop) if desktop.is_dir() else None

    findings: List[Dict[str, Any]] = []

    def add(fid: str, title: str, detail: str, plan: Dict[str, Any], icon: str, min_count: int = 1):
        s = plan["summary"]
        n = s["moves"] + s["renames"] + s["trash"]
        if n >= min_count and plan.get("id"):
            findings.append({"id": fid, "title": title, "detail": detail, "icon": icon, "plan_id": plan["id"],
                             "count": n, "bytes": s["trash_bytes"] or s["move_bytes"], "frees": s["trash_bytes"],
                             "action": fp.describe(plan)})

    inst = fp.plan_installers()
    add("installers", f"{inst['summary']['trash']} old installer{'s' if inst['summary']['trash'] != 1 else ''} in Downloads",
        f"{fmt_bytes(inst['summary']['trash_bytes'])} of .dmg and .pkg files. The apps stay installed.", inst, "package")

    old = fp.plan_old_downloads(days=90, action="trash")
    add("old_downloads", f"{old['summary']['trash']} downloads untouched for 3+ months",
        f"{fmt_bytes(old['summary']['trash_bytes'])}. You can also just archive them inside Downloads.", old, "archive", min_count=3)

    dups = fp.plan_duplicates(min_size_mb=1)
    add("duplicates", f"{dups['summary']['trash']} duplicate file{'s' if dups['summary']['trash'] != 1 else ''}",
        f"Exact copies taking {fmt_bytes(dups['summary']['trash_bytes'])}. One copy of each is kept.", dups, "copy")

    shots = fp.plan_screenshots()
    add("screenshots", f"{shots['summary']['moves']} screenshots lying around",
        "On the Desktop and in Downloads. File them in Pictures/Screenshots by month.", shots, "image", min_count=3)

    if dt and dt["loose_files"] >= 12:
        tidy = fp.plan_tidy("~/Desktop")
        add("desktop", f"{dt['loose_files']} loose files on the Desktop", "Sort them into folders by kind.", tidy, "monitor", min_count=5)
    if dl and dl["loose_files"] >= 20:
        tidy = fp.plan_tidy("~/Downloads")
        add("downloads", f"{dl['loose_files']} loose files in Downloads", "Sort them into folders by kind.", tidy, "download", min_count=5)

    if time.time() - t0 < budget:
        dev = fp.plan_dev_leftovers(idle_days=30)
        add("node_modules", f"{dev['summary']['trash']} node_modules in old projects",
            f"{fmt_bytes(dev['summary']['trash_bytes'])} that `npm install` can bring back.", dev, "code")

    findings.sort(key=lambda f: -f["frees"])
    can_free = sum(f["frees"] for f in findings if f["id"] in ("installers", "duplicates", "node_modules"))
    return {"success": True, "kind": "mac_review", "disk": disk, "folders": folders[:12], "big_files": big,
            "downloads": dl, "desktop": dt, "findings": findings, "can_free": can_free,
            "took_sec": round(time.time() - t0, 1),
            "note": "Nothing has been changed. Each finding has a plan the user can open, check and apply (with Undo)."}


def timeout_for(budget: float, share: float) -> float:
    return max(3.0, budget * share)


def review_for_model(r: Dict[str, Any]) -> str:
    """The review as short markdown, written from what was measured."""
    d = r["disk"]
    lines = [f"**{fmt_bytes(d['free'])} free of {fmt_bytes(d['total'])}** ({d['used_pct']}% used)."]
    top = [f for f in r["folders"] if f["bytes"] > 0][:6]
    if top:
        lines.append("\n**Where the space is:** " + ", ".join(f"{f['name']} {fmt_bytes(f['bytes'])}" + ("" if f["complete"] else "+") for f in top) + ".")
    if r["findings"]:
        lines.append("\n**What I'd tidy:**")
        for f in r["findings"]:
            lines.append(f"- **{f['title']}.** {f['detail']}")
        if r["can_free"] > 0:
            lines.append(f"\nClearing the installers, duplicates and old `node_modules` would free about **{fmt_bytes(r['can_free'])}**.")
        lines.append("\nNothing has changed yet. Open any of these to see every file first; each one can be undone.")
    else:
        lines.append("\nNothing obvious to tidy. Your Mac is in good shape.")
    if r["big_files"]:
        lines.append("\n**Biggest files:** " + ", ".join(f"{b['name']} ({fmt_bytes(b['bytes'])})" for b in r["big_files"][:5]) + ".")
    return "\n".join(lines)


# ------------------------------------------------------------------ one folder

def summarize_folder(path: str, depth: int = 3) -> Dict[str, Any]:
    root = expand(path)
    if not root.is_dir():
        return {"success": False, "error": f"{tilde(root)} isn't a folder."}
    kinds: Counter = Counter()
    kind_bytes: Dict[str, int] = defaultdict(int)
    files, total = [], 0
    newest, oldest = 0.0, time.time()
    for p, st in fp.walk_files([root], budget=5, max_files=40000):
        if len(p.relative_to(root).parts) > depth + 1:
            continue
        k = fp.kind_of(p) or (p.suffix.lower().lstrip(".") or "no extension")
        kinds[k] += 1
        kind_bytes[k] += st.st_size
        total += st.st_size
        newest, oldest = max(newest, st.st_mtime), min(oldest, st.st_mtime)
        files.append((p, st))
    biggest = sorted(files, key=lambda f: -f[1].st_size)[:10]
    recent = sorted(files, key=lambda f: -f[1].st_mtime)[:8]
    loose = fp._loose_files(root, 0)
    try:
        subfolders = sorted(e.name for e in os.scandir(root) if e.is_dir(follow_symlinks=False) and not e.name.startswith("."))
    except OSError:
        subfolders = []
    return {"success": True, "path": tilde(root), "files": len(files), "bytes": total, "size": fmt_bytes(total),
            "subfolders": subfolders[:40], "loose_files": len(loose),
            "kinds": [{"kind": k, "count": n, "size": fmt_bytes(kind_bytes[k])} for k, n in kinds.most_common(12)],
            "biggest": [{"path": tilde(p), "size": fmt_bytes(st.st_size)} for p, st in biggest],
            "recent": [{"path": tilde(p), "modified": _ago(st.st_mtime)} for p, st in recent],
            "newest": _ago(newest) if files else None, "oldest": _ago(oldest) if files else None,
            "screenshots": sum(1 for p, _ in loose if fp.SCREENSHOT_RE.match(p.name)),
            "installers": sum(1 for p, _ in loose if p.suffix.lower() in (".dmg", ".pkg"))}


# ------------------------------------------------------------------ search

KIND_UTI = {"pdf": "com.adobe.pdf", "image": "public.image", "photo": "public.image", "video": "public.movie",
            "movie": "public.movie", "audio": "public.audio", "music": "public.audio", "text": "public.text",
            "document": "public.content", "spreadsheet": "public.spreadsheet", "presentation": "public.presentation",
            "archive": "public.archive", "app": "com.apple.application-bundle", "folder": "public.folder",
            "code": "public.source-code", "email": "com.apple.mail.emlx"}
EXTS = {"pdf": (".pdf",), "image": fp.KINDS[5][1], "photo": fp.KINDS[5][1], "video": fp.KINDS[6][1], "movie": fp.KINDS[6][1],
        "audio": fp.KINDS[7][1], "music": fp.KINDS[7][1], "spreadsheet": fp.KINDS[3][1], "presentation": fp.KINDS[4][1],
        "archive": fp.KINDS[1][1], "code": fp.KINDS[9][1], "document": fp.KINDS[2][1] + fp.KINDS[3][1] + fp.KINDS[4][1]}


def _q(s: str) -> str:
    return re.sub(r'["\\*]', " ", s).strip()


def search(text: str = "", kind: str = "", folder: str = "", modified_within_days: Optional[float] = None,
           older_than_days: Optional[float] = None, min_size_mb: Optional[float] = None, content: bool = True,
           limit: int = 30) -> Dict[str, Any]:
    """Spotlight search by name and (optionally) what's inside, with kind/date/size filters."""
    root = expand(folder) if folder else fp.home()
    limit = max(1, min(int(limit or 30), 200))
    parts = []
    t = _q(text)
    if t:
        words = [w for w in t.split() if w][:6]
        name = " && ".join(f'kMDItemFSName == "*{w}*"cd' for w in words)
        body = " && ".join(f'kMDItemTextContent == "*{w}*"cd' for w in words)
        parts.append(f"(({name}) || ({body}))" if content else f"({name})")
    k = (kind or "").lower().rstrip("s")
    if k in KIND_UTI:
        parts.append(f'kMDItemContentTypeTree == "{KIND_UTI[k]}"')
    if modified_within_days:
        parts.append(f"kMDItemFSContentChangeDate >= $time.today(-{int(modified_within_days)})")
    if older_than_days:
        parts.append(f"kMDItemFSContentChangeDate < $time.today(-{int(older_than_days)})")
    if min_size_mb:
        parts.append(f"kMDItemFSSize > {int(float(min_size_mb) * 1024 * 1024)}")
    query = " && ".join(parts) or "kMDItemFSName == \"*\""
    hits = [p for p in _mdfind(query, root, limit) if _visible_ok(p)]
    how = "spotlight"
    if not hits:       # not indexed: look by name ourselves
        how = "walk"
        words = [w.lower() for w in t.split()] if t else []
        cut_new = time.time() - modified_within_days * DAY if modified_within_days else None
        cut_old = time.time() - older_than_days * DAY if older_than_days else None
        for p, st in fp.walk_files([root], budget=5):
            n = p.name.lower()
            if words and not all(w in n for w in words):
                continue
            if k in EXTS and p.suffix.lower() not in EXTS[k]:
                continue
            if cut_new and st.st_mtime < cut_new or cut_old and st.st_mtime > cut_old:
                continue
            if min_size_mb and st.st_size < float(min_size_mb) * 1024 * 1024:
                continue
            hits.append(str(p))
            if len(hits) >= limit * 3:
                break
    rows = []
    for p in hits:
        try:
            st = os.lstat(p)
        except OSError:
            continue
        rows.append((p, st))
    rows.sort(key=lambda r: -r[1].st_mtime)
    rows = rows[:limit]
    meta = _mdls([p for p, _ in rows], ["kMDItemKind", "kMDItemLastUsedDate"])
    results = [{"path": tilde(p), "name": os.path.basename(p), "kind": m.get("kMDItemKind") or fp.kind_of(Path(p)),
                "size": fmt_bytes(st.st_size) if stat.S_ISREG(st.st_mode) else "folder", "bytes": st.st_size,
                "modified": _ago(st.st_mtime), "last_opened": (m.get("kMDItemLastUsedDate") or "")[:10] or None,
                "in_icloud_only": fp.is_dataless(st)}
               for (p, st), m in zip(rows, meta)]
    return {"success": True, "count": len(results), "results": results, "searched": tilde(root), "how": how}


# ------------------------------------------------------------------ peek inside

def peek(paths: List[str], chars: int = 700) -> Dict[str, Any]:
    """What files are: kind, size, dates, where they were downloaded from, page/pixel/duration info and
    the start of their text. For naming and sorting by content."""
    items = []
    targets = [expand(p) for p in (paths or [])][:25]
    meta = _mdls([str(p) for p in targets], ["kMDItemKind", "kMDItemWhereFroms", "kMDItemNumberOfPages", "kMDItemPixelWidth",
                                             "kMDItemPixelHeight", "kMDItemDurationSeconds", "kMDItemAuthors", "kMDItemTitle",
                                             "kMDItemContentCreationDate", "kMDItemLastUsedDate"])
    from app.services.document_parser import DocumentParserService
    parser = DocumentParserService()
    for p, m in zip(targets, meta):
        row: Dict[str, Any] = {"path": tilde(p), "name": p.name}
        why = fp.check_path(p, "dst")
        if why:
            row["error"] = f"Not looking: {why}."
            items.append(row); continue
        try:
            st = p.lstat()
        except OSError:
            row["error"] = "Not found."
            items.append(row); continue
        row.update({"size": fmt_bytes(st.st_size), "modified": _ago(st.st_mtime), "kind": m.get("kMDItemKind")})
        for key, label in (("kMDItemWhereFroms", "downloaded_from"), ("kMDItemNumberOfPages", "pages"), ("kMDItemPixelWidth", "width"),
                           ("kMDItemPixelHeight", "height"), ("kMDItemDurationSeconds", "seconds"), ("kMDItemAuthors", "authors"),
                           ("kMDItemTitle", "title"), ("kMDItemContentCreationDate", "created"), ("kMDItemLastUsedDate", "last_opened")):
            if m.get(key):
                row[label] = m[key] if not isinstance(m[key], list) else [x for x in m[key] if x][:3]
        if fp.is_dataless(st):
            row["note"] = "In iCloud, not downloaded; not opened."
        elif stat.S_ISDIR(st.st_mode):
            try:
                row["contains"] = sorted(os.listdir(p))[:30]
            except OSError:
                pass
        elif st.st_size < 60 * 1024 * 1024:
            try:
                parsed = parser.parse_file(p, max_chars=chars)
                txt = (parsed.get("content") or parsed.get("text") or "").strip() if isinstance(parsed, dict) else ""
                if txt:
                    row["text"] = re.sub(r"\s+", " ", txt)[:chars]
            except Exception:
                pass
        items.append(row)
    return {"success": True, "files": items}


async def review_async(budget: float = 25.0) -> Dict[str, Any]:
    return await asyncio.to_thread(review, budget)
