"""
The footprint doctor (scripts/xod_doctor.py) proves its conservatism on fixture trees.

Xod had no component owning its machine footprint, and machines drifted into sprawl (a
2026-11-14 census of one Mac: about 70 GiB across roughly 170 paths). The doctor's cleanup
may only remove the class it can prove redundant. This test builds a fixture home with one
of each shape the census found and pins the verdicts: the installed app and its data are
current; a duplicate superseded bundle is safe while its kept twin is held; a clean checkout
whose commits all live in the canonical repo is safe, while unique commits, a dirty tree, a
linked worktree, and an agent-referenced checkout are all holds; app scratch and disposable
outputs are safe; a mystery folder is a hold because nothing proves another copy exists.

Run: python3 tests/smoke/run_xod_doctor_test.py
"""
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import xod_doctor as doctor  # noqa: E402

GIT_ENV = ["-c", "user.email=fixture@example.com", "-c", "user.name=Fixture"]


def git(path: Path, *args: str, check=True):
    out = subprocess.run(["git", *GIT_ENV, "-C", str(path), *args],
                         capture_output=True, text=True)
    if check and out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr}")
    return out


def make_app(parent: Path, name: str, version: str, bundle_id="dev.xod.desktop") -> Path:
    app = parent / name
    (app / "Contents" / "Resources").mkdir(parents=True)
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleShortVersionString": version, "CFBundleIdentifier": bundle_id}, f)
    (app / "Contents" / "Resources" / "app.asar").write_bytes(b"asar-bytes-" + version.encode())
    return app


def make_repo(path: Path, commits: int) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    for i in range(commits):
        (path / f"file{i}.txt").write_text(f"content {i}\n")
        git(path, "add", ".")
        git(path, "commit", "-m", f"commit {i}")
    return path


tmp = Path(tempfile.mkdtemp())
home = tmp / "home"
apps_dir = home / "Applications"
for d in (apps_dir, home / "Documents", home / "Downloads", home / ".Trash",
          home / "Library" / "Application Support", home / "Library" / "LaunchAgents"):
    d.mkdir(parents=True)

# The installed app (current) and a separate product.
installed = make_app(apps_dir, "Xod.app", "1.1.4")
partner = make_app(home / "Applications", "Xod Partner.app", "1.0", bundle_id="dev.xod.partner")

# Two copies of superseded 1.0.2: one is kept, the other is a provable duplicate.
kept_102 = make_app(home / "Xod-archive", "Xod-1.0.2.app-backup", "1.0.2")
dupe_102 = make_app(home / "xod-bundle-backup", "Xod.app", "1.0.2")

# Git shapes, against a canonical repo outside the scanned home.
canonical = make_repo(tmp / "canonical-xod", 2)
clean_clone = tmp / "clone-src"
subprocess.run(["git", "clone", str(canonical), str(home / "xod-clean-clone")],
               capture_output=True, text=True, check=True)
unique_clone = home / "xod-unique"
subprocess.run(["git", "clone", str(canonical), str(unique_clone)],
               capture_output=True, text=True, check=True)
(unique_clone / "only-here.txt").write_text("unmerged work\n")
git(unique_clone, "add", ".")
git(unique_clone, "commit", "-m", "unique commit")
dirty_clone = home / "xod-dirty"
subprocess.run(["git", "clone", str(canonical), str(dirty_clone)],
               capture_output=True, text=True, check=True)
(dirty_clone / "file0.txt").write_text("edited but never committed\n")
linked = home / "xod-linked"
git(canonical, "worktree", "add", str(linked))
devstack = home / "xod-devstack"
subprocess.run(["git", "clone", str(canonical), str(devstack)],
               capture_output=True, text=True, check=True)

# A launch agent that runs from the dev stack checkout (the census's port 8001 shape).
plist = {
    "Label": "com.xod.testserver",
    "ProgramArguments": ["/bin/sh", "-c", f"cd {devstack} && exec .venv/bin/python -m uvicorn app.main:app --port 8001"],
}
with open(home / "Library" / "LaunchAgents" / "com.xod.testserver.plist", "wb") as f:
    plistlib.dump(plist, f)

# The data folder with its itemized children.
data = home / "Library" / "Application Support" / "Xod"
for sub in ("logs", "test_runs", "models"):
    (data / sub).mkdir(parents=True)
    (data / sub / "blob.bin").write_bytes(b"x" * 64)
(data / "xod.db").write_bytes(b"db")
(data / "xod.db.bak-20261107").write_bytes(b"db-backup")

# Scratch, empty, mystery, duplicate media, trash.
scratch = home / "xod-manage-zz9"
scratch.mkdir()
(scratch / "tmp.txt").write_text("scratch")
empty_dir = home / "xod-eval-sandbox"
empty_dir.mkdir()
mystery = home / "xod-mystery"
mystery.mkdir()
(mystery / "who-knows.txt").write_text("?")
(home / "xod_launch.mp4").write_bytes(b"VIDEOBYTES")
dupe_media = home / "Downloads" / "xod_launch.mp4"
dupe_media.write_bytes(b"VIDEOBYTES")
trashed = home / ".Trash" / "Xod-0.9.24-arm64.dmg"
trashed.write_bytes(b"old installer")

items, spec = doctor.inventory("darwin", home, apps_dir)
doctor.classify(items, spec, canonical)
by_path = {str(i.path): i for i in items}


def cls(p: Path):
    item = by_path.get(str(p))
    return item.cls if item else None


def reason_has(p: Path, needle: str):
    item = by_path.get(str(p))
    return bool(item) and any(needle in r for r in item.reasons)


cases = []
cases.append(("installed app is current", cls(installed) == "current"))
cases.append(("separate product is a hold", cls(partner) == "hold" and reason_has(partner, "separate product")))
cases.append(("one 1.0.2 copy is kept as a hold", cls(kept_102) == "hold" and reason_has(kept_102, "keeps")))
cases.append(("the duplicate 1.0.2 copy is stale-safe", cls(dupe_102) == "stale-safe"))
cases.append(("clean equivalent checkout is stale-safe", cls(home / "xod-clean-clone") == "stale-safe"))
clean_item = by_path[str(home / "xod-clean-clone")]
cases.append(("equivalence evidence is recorded",
              clean_item.detail.get("commits_checked") == 2 and clean_item.detail.get("head_tree_entries") == 2))
cases.append(("unique commits are a hold", cls(unique_clone) == "hold" and reason_has(unique_clone, "canonical repo")))
cases.append(("dirty checkout is a hold", cls(dirty_clone) == "hold" and reason_has(dirty_clone, "uncommitted")))
cases.append(("linked worktree is a hold", cls(linked) == "hold" and reason_has(linked, "linked git worktree")))
cases.append(("agent-referenced checkout is a hold", cls(devstack) == "hold" and reason_has(devstack, "launch agent")))
cases.append(("launch agent itself is a hold",
              cls(home / "Library" / "LaunchAgents" / "com.xod.testserver.plist") == "hold"))
cases.append(("data folder is current", cls(data) == "current"))
cases.append(("logs are stale-safe", cls(data / "logs") == "stale-safe"))
cases.append(("test runs are stale-safe", cls(data / "test_runs") == "stale-safe"))
cases.append(("models are a hold (re-download cost)", cls(data / "models") == "hold" and reason_has(data / "models", "re-download")))
cases.append(("database backup is a hold", cls(data / "xod.db.bak-20261107") == "hold"))
cases.append(("app scratch is stale-safe", cls(scratch) == "stale-safe"))
cases.append(("empty folder is stale-safe", cls(empty_dir) == "stale-safe"))
cases.append(("mystery folder is a hold", cls(mystery) == "hold"))
cases.append(("original media is kept", cls(home / "xod_launch.mp4") == "hold"))
cases.append(("duplicate media is stale-safe", cls(dupe_media) == "stale-safe"))
cases.append(("trashed installer is stale-safe", cls(trashed) == "stale-safe"))

# Without a canonical repo, even the clean checkout must become a hold.
items2, spec2 = doctor.inventory("darwin", home, apps_dir)
doctor.classify(items2, spec2, None)
by2 = {str(i.path): i for i in items2}
cases.append(("no canonical repo: clean checkout held",
              by2[str(home / "xod-clean-clone")].cls == "hold"))

# Cleanup: dry run removes nothing; --yes removes exactly the stale-safe set.
safe_paths = {i.path for i in items if i.cls == "stale-safe"}
expected_reclaim = sum(i.size for i in items if i.cls == "stale-safe")
dry = doctor.clean(items, yes=False)
cases.append(("dry run removes nothing on disk",
              all(p.exists() for p in safe_paths) and len(dry["removed"]) == len(safe_paths)))
done = doctor.clean(items, yes=True)
cases.append(("cleanup reclaims exactly the stale-safe bytes",
              done["reclaimed_bytes"] == expected_reclaim and not done.get("errors")))
cases.append(("cleanup removed the stale-safe set", all(not p.exists() for p in safe_paths)))
cases.append(("cleanup left every current and held item in place",
              all(i.path.exists() for i in items if i.cls in ("current", "hold"))))

failed = [name for name, ok in cases if not ok]
for name, ok in cases:
    print(f"  {'✓' if ok else '✗'} {name}")
print(f"{len(cases) - len(failed)}/{len(cases)} passed")
sys.exit(1 if failed else 0)
