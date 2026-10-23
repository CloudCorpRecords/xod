"""
A Crew job's own copy of a git repository: a worktree on a branch of its own (xod/<job>), made from where your
checkout is now. Jobs never edit the same files at once, and nothing reaches your checkout until you say so:

  create         the copy, under DATA_DIR/worktrees (not inside your repository, so `git status` stays clean)
  changes        what the job changed: each file with lines added and removed, and the diff
  merge          commits the job's changes on its branch, merges that into the branch it started from, removes the copy
  pull_request   commits, pushes the branch and opens a pull request (GitHub CLI); the copy goes, the branch stays
  remove         throws the copy and its branch away

Not a git repository (or one with no commits yet): there's no copy, and the job works in the folder itself. Files git
doesn't track (node_modules, .env …) aren't in a copy; the job can install what it needs there.
"""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.paths import DATA_DIR

ROOT = DATA_DIR / "worktrees"
MAX_PATCH = 300_000


class WorktreeError(Exception):
    pass


def _env() -> Dict[str, str]:
    try:
        from app.services.agent_clis import _env as agent_env
        env = agent_env()
    except Exception:
        env = dict(os.environ)
    env.update(GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true")     # never stop to ask for a password or a message
    return env


def _git(args: List[str], cwd: str, timeout: float = 60) -> Tuple[int, str]:
    git = shutil.which("git", path=_env().get("PATH")) or "git"
    try:
        r = subprocess.run([git, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=_env(),
                           stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _out(args: List[str], cwd: str) -> str:
    code, out = _git(args, cwd)
    return out.splitlines()[0].strip() if code == 0 and out else ""


def create(folder: str, name: str, jid: str) -> Optional[Dict[str, Any]]:
    """The job's own copy of the repository the folder is in, or None when it isn't in one (the job then works there)."""
    if not folder or not os.path.isdir(folder):
        return None
    top = _out(["rev-parse", "--show-toplevel"], folder)
    if not top or not _out(["rev-parse", "--verify", "HEAD"], top):
        return None                                   # not a repository, or no commits yet: nothing to branch from
    base_sha = _out(["rev-parse", "HEAD"], top)
    base = _out(["rev-parse", "--abbrev-ref", "HEAD"], top)
    if base == "HEAD":
        base = base_sha[:10]                          # a detached checkout: from that commit
    code, status = _git(["status", "--porcelain"], top)
    branch = f"xod/{name}"
    if _out(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], top):
        branch = f"xod/{name}-{jid}"
    path = ROOT / f"{Path(top).name}-{name}-{jid}"
    ROOT.mkdir(parents=True, exist_ok=True)
    code, out = _git(["worktree", "add", "-b", branch, str(path), base_sha], top, timeout=180)
    if code != 0:
        raise WorktreeError(f"Couldn't make the job's own copy of {Path(top).name}: {out[-400:]}")
    rel = os.path.relpath(os.path.realpath(folder), os.path.realpath(top))
    cwd = str(path / rel) if rel != "." else str(path)
    os.makedirs(cwd, exist_ok=True)
    return {"repo": top, "repo_name": Path(top).name, "path": str(path), "cwd": cwd, "branch": branch, "base": base,
            "base_sha": base_sha, "dirty": bool(status.strip())}


def _stage(wt: Dict[str, Any]) -> None:
    if not os.path.isdir(wt["path"]):
        raise WorktreeError("The job's copy isn't there any more.")
    code, out = _git(["add", "-A"], wt["path"])
    if code != 0:
        raise WorktreeError(f"Couldn't gather the job's changes: {out[-300:]}")


def changes(wt: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the job changed since it started (its commits and what it left uncommitted)."""
    _stage(wt)
    code, numstat = _git(["diff", "--cached", "--numstat", wt["base_sha"]], wt["path"])
    files = []
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            a, r, f = parts
            files.append({"file": f, "added": int(a) if a.isdigit() else None, "removed": int(r) if r.isdigit() else None})
    code, patch = _git(["diff", "--cached", wt["base_sha"]], wt["path"])
    return {"branch": wt["branch"], "base": wt["base"], "files": files, "patch": patch[:MAX_PATCH],
            "truncated": len(patch) > MAX_PATCH,
            "added": sum(f["added"] or 0 for f in files), "removed": sum(f["removed"] or 0 for f in files)}


def _commit(wt: Dict[str, Any], message: str) -> bool:
    """Commits whatever the job left uncommitted on its branch. True when the branch has anything new at all."""
    _stage(wt)
    if _git(["diff", "--cached", "--quiet", "HEAD"], wt["path"])[0] != 0:
        ident = [] if _out(["config", "user.email"], wt["path"]) else ["-c", "user.name=Xod", "-c", "user.email=xod@localhost"]
        code, out = _git([*ident, "commit", "-q", "--no-verify", "-m", message], wt["path"])
        if code != 0:
            raise WorktreeError(f"Couldn't commit the job's changes: {out[-300:]}")
    return _out(["rev-list", "--count", f"{wt['base_sha']}..HEAD"], wt["path"]) not in ("", "0")


def merge(wt: Dict[str, Any], message: str) -> Dict[str, Any]:
    if not _commit(wt, message):
        remove(wt, True)
        return {"success": True, "merged": False, "message": "It didn't change anything, so there was nothing to merge (its copy is gone)."}
    top = wt["repo"]
    here = _out(["rev-parse", "--abbrev-ref", "HEAD"], top)
    if here != wt["base"]:
        raise WorktreeError(f"Your checkout of {wt['repo_name']} is on {here} now, and the job started from {wt['base']}. "
                            f"Switch back to {wt['base']} to merge it, or open a pull request instead.")
    n = len(changes_files(wt))
    code, out = _git(["merge", "--no-ff", "--no-edit", "-m", f"Merge {wt['branch']}: {message}", wt["branch"]], top, timeout=120)
    if code != 0:
        clash = _git(["diff", "--name-only", "--diff-filter=U"], top)[1].split()
        _git(["merge", "--abort"], top)
        if clash:
            raise WorktreeError(f"It clashes with your checkout in {', '.join(clash[:6])}: nothing was changed. "
                                "Open a pull request instead, or ask the agent to bring its branch up to date.")
        raise WorktreeError(f"Git didn't merge it, so nothing was changed: {out[-400:]}")
    remove(wt, False)
    _git(["branch", "-d", wt["branch"]], top)
    return {"success": True, "merged": True, "files": n,
            "message": f"Merged into {wt['base']}: {n} file{'s' if n != 1 else ''} changed. Its copy is gone."}


def changes_files(wt: Dict[str, Any]) -> List[str]:
    code, out = _git(["diff", "--name-only", wt["base_sha"], wt["branch"]], wt["repo"])
    return [x for x in out.splitlines() if x.strip()] if code == 0 else []


def pull_request(wt: Dict[str, Any], title: str, body: str) -> Dict[str, Any]:
    if not _commit(wt, title):
        raise WorktreeError("It didn't change anything, so there's nothing to open a pull request for.")
    top = wt["repo"]
    if "origin" not in _git(["remote"], top)[1].split():
        raise WorktreeError(f"{wt['repo_name']} has no remote called origin to push to.")
    base = wt["base"] if _out(["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{wt['base']}"], top) else ""
    code, out = _git(["push", "-u", "origin", wt["branch"]], wt["path"], timeout=180)
    if code != 0:
        raise WorktreeError(f"Couldn't push its branch: {out[-400:]}")
    gh = shutil.which("gh", path=_env().get("PATH"))
    if not gh:
        remove(wt, False)
        return {"success": True, "url": None, "message": f"Pushed {wt['branch']}. The GitHub CLI (gh) isn't installed, so open the pull request on GitHub."}
    args = [gh, "pr", "create", "--head", wt["branch"], "--title", title[:200], "--body", body[:4000]] + (["--base", base] if base else [])
    try:
        r = subprocess.run(args, cwd=wt["path"], capture_output=True, text=True, timeout=120, env=_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise WorktreeError(f"Pushed {wt['branch']}, but opening the pull request didn't work: {e}")
    if r.returncode != 0:
        raise WorktreeError(f"Pushed {wt['branch']}, but opening the pull request didn't work: {(r.stderr or r.stdout)[-400:]}")
    url = (r.stdout.strip().splitlines() or [""])[-1]
    remove(wt, False)
    return {"success": True, "url": url, "message": f"Opened a pull request: {url}"}


def remove(wt: Dict[str, Any], delete_branch: bool) -> None:
    top = wt["repo"]
    if os.path.isdir(wt["path"]):
        code, out = _git(["worktree", "remove", "--force", wt["path"]], top)
        if code != 0 and os.path.isdir(wt["path"]):
            raise WorktreeError(f"Couldn't remove the job's copy: {out[-300:]}")
    _git(["worktree", "prune"], top)
    if delete_branch:
        _git(["branch", "-D", wt["branch"]], top)
