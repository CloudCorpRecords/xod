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


def make_app(parent: Path, name: str, version: str, bundle_id="dev.xod.desktop",
             build: str | None = None, extra: dict | None = None) -> Path:
    app = parent / name
    (app / "Contents" / "Resources").mkdir(parents=True)
    info = {"CFBundleShortVersionString": version, "CFBundleIdentifier": bundle_id}
    if build is not None:
        info["CFBundleVersion"] = build
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump(info, f)
    (app / "Contents" / "Resources" / "app.asar").write_bytes(b"asar-bytes-" + version.encode())
    for rel, data in (extra or {}).items():
        target = app / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
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

# --- Round 6 review regressions: removal only on positive evidence. ---------
# Each fixture below is a shape an independent review showed the doctor calling
# stale-safe without proof. Every one of them must now be a hold.

def scaffold(base: Path):
    h = base / "home"
    ad = h / "Applications"
    for d in (ad, h / "Documents", h / "Downloads", h / ".Trash",
              h / "Library" / "Application Support", h / "Library" / "LaunchAgents"):
        d.mkdir(parents=True)
    return h, ad


def classify_at(h: Path, ad: Path, canon):
    its, sp = doctor.inventory("darwin", h, ad)
    doctor.classify(its, sp, canon)
    return {str(i.path): i for i in its}, its


def clone_from(canon: Path, dest: Path):
    subprocess.run(["git", "clone", str(canon), str(dest)],
                   capture_output=True, text=True, check=True)
    return dest


r6 = tmp / "r6"

# C2: the canonical path is a plain directory, so git cannot read it at all.
h6, ad6 = scaffold(r6 / "c2")
canon6 = make_repo(r6 / "c2" / "canonical-xod", 2)
clone_from(canon6, h6 / "xod-clean-clone")
not_a_repo = r6 / "c2" / "not-a-repo"
not_a_repo.mkdir()
by6, _ = classify_at(h6, ad6, not_a_repo)
it6 = by6[str(h6 / "xod-clean-clone")]
cases.append(("unreadable canonical repo: checkout held, nothing presumed proven",
              it6.cls == "hold" and any("could not be read" in r for r in it6.reasons)))

# C3: a commit made on a detached HEAD, then abandoned when HEAD moved back.
h6, ad6 = scaffold(r6 / "c3-reflog")
canon6 = make_repo(r6 / "c3-reflog" / "canonical-xod", 2)
reflog_clone = clone_from(canon6, h6 / "xod-reflog")
git(reflog_clone, "checkout", "--detach", "HEAD")
(reflog_clone / "reflog-only.txt").write_text("work from a detached head\n")
git(reflog_clone, "add", ".")
git(reflog_clone, "commit", "-m", "detached work, later abandoned")
git(reflog_clone, "checkout", "main")
by6, _ = classify_at(h6, ad6, canon6)
it6 = by6[str(reflog_clone)]
cases.append(("reflog-only commit: checkout held",
              it6.cls == "hold" and any("canonical repo" in r for r in it6.reasons)))

# C3: gitignored files inside an otherwise clean, fully proven checkout.
h6, ad6 = scaffold(r6 / "c3-ignored")
canon6 = make_repo(r6 / "c3-ignored" / "canonical-xod", 1)
(canon6 / ".gitignore").write_text("local-data/\n")
git(canon6, "add", ".")
git(canon6, "commit", "-m", "ignore local data")
ignored_clone = clone_from(canon6, h6 / "xod-ignored")
(ignored_clone / "local-data").mkdir()
(ignored_clone / "local-data" / "secrets.env").write_text("TOKEN=abc\n")
by6, _ = classify_at(h6, ad6, canon6)
it6 = by6[str(ignored_clone)]
cases.append(("gitignored files: checkout held",
              it6.cls == "hold" and any("ignores" in r for r in it6.reasons)))

# C4: a bundle whose Info.plist is corrupt, and one with no bundle identifier.
h6, ad6 = scaffold(r6 / "c4-identity")
for name in ("Xod-a.app", "Xod-b.app"):
    bundle = h6 / name
    (bundle / "Contents" / "Resources").mkdir(parents=True)
    (bundle / "Contents" / "Info.plist").write_bytes(b"\x00\x01 not a plist {{{")
    (bundle / "Contents" / "Resources" / "app.asar").write_bytes(b"payload")
for name in ("Xod-c.app", "Xod-d.app"):
    bundle = h6 / name
    (bundle / "Contents" / "Resources").mkdir(parents=True)
    with open(bundle / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleShortVersionString": "1.0.2"}, f)
    (bundle / "Contents" / "Resources" / "app.asar").write_bytes(b"payload")
by6, _ = classify_at(h6, ad6, None)
cases.append(("corrupt Info.plist: both copies held, no 'None' version group",
              by6[str(h6 / "Xod-a.app")].cls == "hold" and by6[str(h6 / "Xod-b.app")].cls == "hold"))
cases.append(("missing bundle identifier: both copies held",
              by6[str(h6 / "Xod-c.app")].cls == "hold" and by6[str(h6 / "Xod-d.app")].cls == "hold"))

# C4: same version string, different build numbers are different builds.
h6, ad6 = scaffold(r6 / "c4-build")
make_app(h6, "Xod-old-a.app", "1.0.2", build="100")
make_app(h6, "Xod-old-b.app", "1.0.2", build="101")
by6, _ = classify_at(h6, ad6, None)
cases.append(("same version, different build: both copies held",
              by6[str(h6 / "Xod-old-a.app")].cls == "hold"
              and by6[str(h6 / "Xod-old-b.app")].cls == "hold"))

# C4: same version and build, but the bundle contents differ.
h6, ad6 = scaffold(r6 / "c4-contents")
make_app(h6, "Xod-old-a.app", "1.0.2", build="100")
make_app(h6, "Xod-old-b.app", "1.0.2", build="100",
         extra={"Contents/Resources/extra.dat": b"only in b"})
by6, _ = classify_at(h6, ad6, None)
it6 = by6[str(h6 / "Xod-old-b.app")]
cases.append(("same version and build, different contents: differing copy held",
              it6.cls == "hold" and any("contents differ" in r for r in it6.reasons)))

# C4: a duplicate a launch agent points at is a live dependency, not a duplicate.
h6, ad6 = scaffold(r6 / "c4-agent")
make_app(h6, "Xod-keep.app", "1.0.2", build="100")
agent_app = make_app(h6, "xod-agent-run.app", "1.0.2", build="100")
with open(h6 / "Library" / "LaunchAgents" / "com.xod.runner.plist", "wb") as f:
    plistlib.dump({"Label": "com.xod.runner",
                   "ProgramArguments": ["/usr/bin/open", str(agent_app)]}, f)
by6, _ = classify_at(h6, ad6, None)
it6 = by6[str(agent_app)]
cases.append(("agent-referenced app copy: held",
              it6.cls == "hold" and any("launch agent" in r for r in it6.reasons)))

# M2: the data folder's logs as a symlink to a directory outside the data folder.
h6, ad6 = scaffold(r6 / "m2-data")
outside_logs = r6 / "m2-data" / "outside-logs"
outside_logs.mkdir(parents=True)
(outside_logs / "keep.log").write_text("precious\n")
data6 = h6 / "Library" / "Application Support" / "Xod"
data6.mkdir(parents=True)
(data6 / "logs").symlink_to(outside_logs, target_is_directory=True)
by6, items6 = classify_at(h6, ad6, None)
cases.append(("symlinked logs folder: held as a symlink",
              by6[str(data6 / "logs")].cls == "hold"
              and any("symlink" in r for r in by6[str(data6 / "logs")].reasons)))
doctor.clean(items6, yes=True)
cases.append(("cleanup leaves the symlinked logs and its target in place",
              (data6 / "logs").is_symlink() and (outside_logs / "keep.log").exists()))

# M2: a symlinked bundle inside a container folder is not a bundle at all.
h6, ad6 = scaffold(r6 / "m2-bundle")
real_bundle = make_app(h6, "Xod-real.app", "1.0.2", build="100")
container6 = h6 / "xod-links"
container6.mkdir()
bundle_link = container6 / "Xod.app"
bundle_link.symlink_to(real_bundle, target_is_directory=True)
by6, items6 = classify_at(h6, ad6, None)
cases.append(("symlinked bundle in a container: held, never stale-safe",
              by6[str(bundle_link)].cls == "hold"))
doctor.clean(items6, yes=True)
cases.append(("cleanup leaves the bundle symlink and its target in place",
              bundle_link.is_symlink() and real_bundle.exists()))

# M3: checkouts referenced only inside agent command strings, in the shapes
# real command lines take: an env assignment, a quoted path, a ;-separated one.
h6, ad6 = scaffold(r6 / "m3")
canon6 = make_repo(r6 / "m3" / "canonical-xod", 2)
m3_shapes = {
    "xod-envstack": lambda p: f"PYTHONPATH={p}/src exec python3 -m app",
    "xod-quoted": lambda p: f'"{p}"',
    "xod-semicolon": lambda p: f"cd /tmp;{p}/bin/run",
}
for dirname, shape in m3_shapes.items():
    target = clone_from(canon6, h6 / dirname)
    with open(h6 / "Library" / "LaunchAgents" / f"com.xod.{dirname}.plist", "wb") as f:
        plistlib.dump({"Label": f"com.xod.{dirname}",
                       "ProgramArguments": ["/bin/sh", "-c", shape(target)]}, f)
by6, _ = classify_at(h6, ad6, canon6)
for dirname in m3_shapes:
    it6 = by6[str(h6 / dirname)]
    cases.append((f"agent reference ({dirname}): checkout held",
                  it6.cls == "hold" and any("launch agent" in r for r in it6.reasons)))

# Doctor minor: an unreadable folder is a hold, not a crash.
import os as _os
if _os.geteuid() != 0:
    h6, ad6 = scaffold(r6 / "unreadable")
    sealed = h6 / "xod-sealed"
    sealed.mkdir()
    (sealed / "hidden.txt").write_text("cannot see me\n")
    sealed.chmod(0)
    try:
        by6, _ = classify_at(h6, ad6, None)
        cases.append(("unreadable folder: held, classify does not raise",
                      by6[str(sealed)].cls == "hold"))
    finally:
        sealed.chmod(0o755)

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
