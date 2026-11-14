#!/usr/bin/env python3
"""
Owns the Xod footprint on one machine: what is installed, what is history, what is safe to remove.

  python3 scripts/xod_doctor.py                      # inventory and classify (read only)
  python3 scripts/xod_doctor.py --json               # the same, machine readable
  python3 scripts/xod_doctor.py clean                # dry run: what cleanup would remove
  python3 scripts/xod_doctor.py clean --yes          # remove the stale-safe class only
  python3 scripts/xod_doctor.py --canonical ~/xod    # canonical repo for the equivalence check

Why this exists: the product had no component that owns its machine footprint. A long-term
user's machine drifts into sprawl: one repo clone per experiment, bundle backups stacked on
bundle backups, stale dev stacks still answering ports (a 2026-11-14 census of the founder's
own Mac found about 70 GiB of Xod across roughly 170 paths, of which a handful of items were
load bearing). This doctor productizes that census for any user.

The classification is conservative on purpose. An item is only ever "stale-safe" when there
is positive evidence it is redundant or regenerable:

  - an app copy whose version is still represented by another copy that is kept;
  - a git checkout that is clean AND whose every commit exists in the canonical repo
    (without a canonical repo, equivalence is unproven and the checkout is a hold);
  - the app's own scratch folders and its disposable outputs (logs, test runs);
  - an exact byte duplicate of a file that is kept;
  - anything already in the Trash.

Everything else is a hold, with the reason stated: the only copy of a version, unique commits,
uncommitted changes, a linked worktree, a folder a launch agent runs from, the canonical repo
itself, a database backup, model weights whose removal forces a re-download, a separate
product (Xod Partner), or simply no redundancy evidence. Cleanup removes the stale-safe
class and nothing else, and never follows or removes a symlink. The running install and its
data folder are "current" and untouchable.

macOS is the proven platform (fixture-tested, and the rules are the ones the 2026-11-14
census applied by hand). The Windows roots follow the updater's own facts (per-user NSIS
install under %LOCALAPPDATA%\\Programs, data in %APPDATA%\\Xod) and are design-level until
a Windows machine proves them. Uses only the standard library.
"""
import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

XOD_BUNDLE_ID = "dev.xod.desktop"
SCRATCH_PREFIX = "xod-manage-"
DATA_PRUNE_DIRS = ("logs", "test_runs")

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def dir_size(path: Path) -> int:
    total = 0
    for base, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(base, name)).st_size
            except OSError:
                pass
    return total


def item_size(path: Path) -> int:
    if path.is_symlink() or path.is_file():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    return dir_size(path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(path: Path, *args: str) -> tuple[int, str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True, text=True, timeout=120,
        )
        return out.returncode, out.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def read_plist(path: Path):
    try:
        with open(path, "rb") as f:
            return plistlib.load(f)
    except Exception:
        return None


def version_key(v):
    parts = []
    for seg in str(v or "0").replace("-", ".").split("."):
        parts.append(int(seg) if seg.isdigit() else 0)
    return tuple(parts[:3]) + (0,) * (3 - len(parts[:3]))


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


class Item:
    def __init__(self, path: Path, kind: str):
        self.path = path
        self.kind = kind                # app | data | data-child | checkout | folder | file | agent
        self.size = item_size(path)
        self.version = None
        self.bundle_id = None
        self.detail = {}
        self.cls = "hold"               # current | hold | stale-safe
        self.reasons: list[str] = []

    def hold(self, reason: str):
        self.cls = "hold"
        self.reasons.append(reason)

    def safe(self, reason: str):
        self.cls = "stale-safe"
        self.reasons.append(reason)

    def to_dict(self):
        return {
            "path": str(self.path),
            "kind": self.kind,
            "size_bytes": self.size,
            "version": self.version,
            "class": self.cls,
            "reasons": self.reasons,
            "detail": self.detail,
        }


def platform_roots(platform: str, home: Path, apps_dir: Path | None):
    """Scan roots, in a fixed order (duplicate-keeping depends on it)."""
    if platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        apps = apps_dir or (local / "Programs")
        return {
            "apps": apps,
            "roots": [apps, home, home / "Documents", home / "Downloads", home / "Desktop"],
            "data_parent": roaming,
            "agents": None,
        }
    apps = apps_dir or Path("/Applications")
    return {
        "apps": apps,
        "roots": [
            apps,
            home / "Applications",
            home,
            home / "Documents",
            home / "Downloads",
            home / "Desktop",
            home / ".Trash",
            home / "Library" / "Application Support",
            home / "Library" / "LaunchAgents",
        ],
        "data_parent": home / "Library" / "Application Support",
        "agents": home / "Library" / "LaunchAgents",
    }


def is_app_bundle(path: Path) -> bool:
    return path.is_dir() and (path / "Contents" / "Info.plist").is_file()


def looks_xod(name: str) -> bool:
    return "xod" in name.lower()


def inventory(platform: str, home: Path, apps_dir: Path | None) -> tuple[list[Item], dict]:
    spec = platform_roots(platform, home, apps_dir)
    items: list[Item] = []
    seen: set[Path] = set()

    def add(item: Item):
        key = item.path.resolve() if not item.path.is_symlink() else item.path
        if key in seen:
            return
        seen.add(key)
        items.append(item)

    for root in spec["roots"]:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda p: p.name):
            name = child.name
            if root == spec["agents"]:
                if looks_xod(name):
                    add(Item(child, "agent"))
                continue
            if root == spec["data_parent"]:
                if name in ("Xod", "XodPartner"):
                    add(Item(child, "data"))
                continue
            if not looks_xod(name):
                continue
            if child.is_symlink():
                add(Item(child, "folder"))
            elif is_app_bundle(child):
                add(Item(child, "app"))
            elif child.is_dir() and (child / ".git").exists():
                add(Item(child, "checkout"))
            elif child.is_dir():
                # One level deeper: archive and backup folders hold bundles
                # (the census's Xod-archive shape). A folder that holds only
                # bundles is a container, not an item of its own.
                try:
                    grandchildren = list(child.iterdir())
                except OSError:
                    grandchildren = []
                bundles = [g for g in grandchildren if looks_xod(g.name) and is_app_bundle(g)]
                for g in bundles:
                    add(Item(g, "app"))
                if not (bundles and len(bundles) == len(grandchildren)):
                    add(Item(child, "folder"))
            else:
                add(Item(child, "file"))
    return items, spec


def agent_references(agents_dir: Path | None) -> list[str]:
    """Every program path any launch agent points at; used to protect live dependencies."""
    refs: list[str] = []
    if not agents_dir or not agents_dir.is_dir():
        return refs
    for plist in agents_dir.iterdir():
        data = read_plist(plist)
        if not isinstance(data, dict):
            continue
        prog = data.get("Program")
        if isinstance(prog, str):
            refs.append(prog)
        args = data.get("ProgramArguments")
        if isinstance(args, list):
            refs.extend(a for a in args if isinstance(a, str))
    return refs


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(items: list[Item], spec: dict, canonical: Path | None):
    refs = agent_references(spec["agents"])

    def referenced(path: Path) -> bool:
        # Agents point at a folder directly (an interpreter inside it) or name
        # it inside a shell command string (the dev stack's -c line), so match
        # the path as a whole token, not only as a prefix.
        s = str(path)
        return any(r == s or r.startswith(s + "/") or (s + " ") in r or r.endswith(s) for r in refs)

    # App bundles first: identity, then the version groups.
    apps = [i for i in items if i.kind == "app"]
    for it in apps:
        info = read_plist(it.path / "Contents" / "Info.plist") or {}
        it.version = info.get("CFBundleShortVersionString")
        it.bundle_id = info.get("CFBundleIdentifier")
        if it.bundle_id and it.bundle_id != XOD_BUNDLE_ID:
            it.hold(f"separate product (bundle id {it.bundle_id}); the doctor only manages Xod itself")
    desktop = [i for i in apps if i.bundle_id == XOD_BUNDLE_ID or not i.bundle_id]
    current = None
    in_apps = [i for i in desktop if i.path.parent == spec["apps"]]
    if in_apps:
        current = max(in_apps, key=lambda i: version_key(i.version))
        current.cls = "current"
        current.reasons.append(f"the installed Xod {current.version} in the applications folder")
    by_version: dict[str, list[Item]] = {}
    for it in desktop:
        if it is not current:
            by_version.setdefault(str(it.version), []).append(it)
    for ver, group in by_version.items():
        group.sort(key=lambda i: str(i.path))
        if current is not None and ver == str(current.version):
            for it in group:
                it.safe(f"duplicate copy of the installed version {ver}; the applications copy is kept")
        else:
            keeper, rest = group[0], group[1:]
            keeper.hold(f"kept: the copy of Xod {ver} this machine keeps" if rest else
                        f"the only copy of Xod {ver} on this machine")
            for it in rest:
                it.safe(f"duplicate copy of Xod {ver}; one copy is kept at {keeper.path}")

    # Data folders and their itemized children.
    data_children: list[Item] = []
    for it in [i for i in items if i.kind == "data"]:
        if it.path.name == "XodPartner":
            it.hold("data folder of Xod Partner, a separate product")
            continue
        it.cls = "current"
        it.reasons.append("the installed app's data folder; an update or repair never touches it")
        for child in sorted(it.path.iterdir(), key=lambda p: p.name):
            if child.is_dir() and child.name in DATA_PRUNE_DIRS:
                c = Item(child, "data-child")
                c.safe(f"disposable app output ({child.name}); the app regenerates it")
                data_children.append(c)
            elif child.is_dir() and child.name == "models":
                c = Item(child, "data-child")
                c.hold("downloaded model weights; removing them forces a re-download before the next use")
                data_children.append(c)
            elif child.is_file() and ".bak" in child.name:
                c = Item(child, "data-child")
                c.hold("database backup; it is the recovery copy if the live database fails")
                data_children.append(c)
    items.extend(data_children)

    canonical_resolved = canonical.resolve() if canonical else None
    for it in items:
        if it.kind in ("app", "data", "data-child", "agent"):
            if it.kind == "agent":
                it.hold("launch agent; it is the app's own switch, managed by the app, not the doctor")
            continue
        p = it.path
        if p.is_symlink():
            it.hold("symlink; the doctor never follows or removes a link")
            continue
        if ".Trash" in p.parts:
            trash_note = "already in the Trash; cleanup only finishes what the user started"
            if it.kind != "checkout":
                it.safe(trash_note)
                continue
        if it.kind == "checkout":
            classify_checkout(it, canonical, canonical_resolved, referenced)
            continue
        if referenced(p):
            it.hold("a launch agent runs from here; it is a live dependency")
            continue
        if it.kind == "folder":
            if it.size == 0 and not any(p.iterdir()):
                it.safe("empty folder")
            elif p.name.startswith(SCRATCH_PREFIX):
                it.safe("app scratch folder; the app recreates these as it works")
            else:
                it.hold("no redundancy evidence; the doctor cannot prove another copy exists")
        # kind == file: decided by the duplicate pass below; default hold.
        if it.kind == "file":
            it.hold("no redundancy evidence; the doctor cannot prove another copy exists")

    # Exact-duplicate loose files: keep the first in scan order, the rest are safe.
    by_hash: dict[str, Item] = {}
    for it in [i for i in items if i.kind == "file"]:
        if it.path.is_symlink() or it.size == 0:
            continue
        try:
            digest = sha256_file(it.path)
        except OSError:
            continue
        if digest in by_hash:
            keeper = by_hash[digest]
            it.cls = "stale-safe"
            it.reasons = [f"exact duplicate of {keeper.path}, which is kept"]
        else:
            by_hash[digest] = it


def classify_checkout(it: Item, canonical: Path | None, canonical_resolved, referenced):
    p = it.path
    if (p / ".git").is_file():
        it.hold("linked git worktree; resolve the worktree set before any removal")
        return
    if canonical_resolved and p.resolve() == canonical_resolved:
        it.hold("the canonical repo itself")
        return
    code, tree = git(p, "ls-tree", "-r", "HEAD")
    if code == 0:
        it.detail["head_tree_entries"] = len([ln for ln in tree.splitlines() if ln.strip()])
    code, status = git(p, "status", "--porcelain")
    if code != 0:
        it.hold("not a readable git checkout")
        return
    if status.strip():
        it.hold("uncommitted changes in the working tree")
        return
    if referenced(p):
        it.hold("a launch agent runs from here; it is a live dependency")
        return
    if canonical is None:
        it.hold("no canonical repo supplied; commit equivalence is unproven")
        return
    code, shas = git(p, "rev-list", "--all")
    if code != 0:
        it.hold("commit history unreadable")
        return
    sha_list = [s for s in shas.splitlines() if s.strip()]
    missing = []
    if sha_list:
        try:
            proc = subprocess.run(
                ["git", "-C", str(canonical), "cat-file", "--batch-check"],
                input="\n".join(sha_list), capture_output=True, text=True, timeout=300,
            )
            for line in proc.stdout.splitlines():
                parts = line.split(" ")
                if len(parts) >= 2 and parts[1] == "missing":
                    missing.append(parts[0])
        except (OSError, subprocess.TimeoutExpired):
            it.hold("the canonical repo could not be read for the equivalence check")
            return
    it.detail["commits_checked"] = len(sha_list)
    if missing:
        it.detail["unique_commits"] = len(missing)
        it.hold(f"{len(missing)} commit(s) exist nowhere in the canonical repo")
        return
    it.safe("working tree clean and every commit is in the canonical repo")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def clean(items: list[Item], yes: bool) -> dict:
    plan = [i for i in items if i.cls == "stale-safe"]
    result = {"dry_run": not yes, "removed": [], "reclaimed_bytes": 0}
    for it in plan:
        if yes:
            try:
                if it.path.is_dir() and not it.path.is_symlink():
                    shutil.rmtree(it.path)
                else:
                    it.path.unlink()
            except OSError as e:
                result.setdefault("errors", []).append(f"{it.path}: {e}")
                continue
        result["removed"].append({"path": str(it.path), "size_bytes": it.size, "reasons": it.reasons})
        result["reclaimed_bytes"] += it.size
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def fmt_size(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="xod_doctor", description="Inventory and clean the Xod footprint on this machine.")
    ap.add_argument("command", nargs="?", default="inventory", choices=["inventory", "clean"])
    ap.add_argument("--yes", action="store_true", help="with clean: actually remove (default is a dry run)")
    ap.add_argument("--json", action="store_true", help="machine readable output")
    ap.add_argument("--platform", default=sys.platform, choices=["darwin", "win32"])
    ap.add_argument("--home", type=Path, default=Path.home())
    ap.add_argument("--apps-dir", type=Path, default=None)
    ap.add_argument("--canonical", type=Path, default=None,
                    help="the canonical Xod repo; checkouts are only removable when every commit is in it")
    args = ap.parse_args(argv)

    items, spec = inventory(args.platform, args.home, args.apps_dir)
    classify(items, spec, args.canonical)

    if args.command == "clean":
        result = clean(items, args.yes)
        if args.json:
            print(json.dumps(result, indent=1))
        else:
            verb = "Removed" if args.yes else "Would remove"
            for r in result["removed"]:
                print(f"  {verb}: {r['path']} ({fmt_size(r['size_bytes'])})")
            for e in result.get("errors", []):
                print(f"✋ {e}")
            print(f"{'Reclaimed' if args.yes else 'Reclaimable'}: {fmt_size(result['reclaimed_bytes'])} "
                  f"across {len(result['removed'])} item(s)" + ("" if args.yes else " (dry run; pass --yes to remove)"))
        return 0

    if args.json:
        print(json.dumps({"items": [i.to_dict() for i in items]}, indent=1))
        return 0
    totals = {"current": 0, "hold": 0, "stale-safe": 0}
    for it in items:
        totals[it.cls] += it.size
        print(f"[{it.cls:10}] {fmt_size(it.size):>10}  {it.path}")
        for r in it.reasons:
            print(f"             {r}")
    print(f"\ncurrent {fmt_size(totals['current'])}, held {fmt_size(totals['hold'])}, "
          f"stale-safe {fmt_size(totals['stale-safe'])} across {len(items)} items")
    return 0


if __name__ == "__main__":
    sys.exit(main())
