"""
Repo Worker: the open-source contribution stack as a built-in Xod capability.

Phase 1 (bundle, do not rewrite): the fleet tools stay the fleet tools. This
module wraps their published CLIs behind the harness tool surface
(services/harness.py), shapes every answer as {claimed, verified, evidence},
and encodes the campaign's gate pass (services/repo_worker_policy.py is the
one policy file; the gates come from the maintainer profile's Stages 0 to 6).

  repo_wave, repo_check, repo_friction, repo_racecheck   Greenlight wrappers
  repo_reprocheck, repo_prosecheck, repo_commitcheck     the toolkit gates
  repo_ci_gate                                           the full-CI gate
  repo_find_work                                         Stages 0 to 2
  repo_claimcheck, repo_claim                            Stage 3, the claim
  repo_open_plan                                         Stages 3 to 5, dry-run
  repo_open_execute                                      the approved real open
  repo_watch, repo_draft_reply                           Stage 6, read + drafts

Phase 2: the open path is live but approval-locked. repo_open_plan still
only prepares and verifies, in dry-run, every time. A person approves one
stored plan through the approval flow (record_approval, fed by the app's
approval surface and logged in the agent approval record), and
repo_open_execute is the only path that pushes and opens, only for that
plan, only after re-running the gates fresh, and it verifies the opened
PR by read-back. Babysit replies are drafted locally and never posted.

Honesty rules, inherited from the harness: what a CLI said is claimed; what
this module's own checks established is verified; the two are never merged.
A tool that errors, times out, or cannot be read yields verified state
"unknown" with the failure named in evidence, and under the policy an
unknown gate means the candidate does not proceed.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.paths import DATA_DIR
from app.services.repo_worker_policy import POLICY

_CLI = POLICY["cli"]


# ------------------------------------------------------------------ seams
# Module-level so suites can stand in fakes, the way the harness replaces
# its own seams (harness._relay and friends).

def _resolve_argv(argv: List[str]) -> List[str]:
    """npx and npm are .cmd shims on Windows; route them through cmd /c."""
    if os.name == "nt" and argv and argv[0] in ("npx", "npm"):
        return ["cmd", "/c"] + list(argv)
    return list(argv)


def _run_cli(argv: List[str], timeout: Optional[int] = None, cwd: Optional[str] = None,
             input_text: Optional[str] = None) -> Tuple[int, str, str]:
    """Run one CLI. Returns (exit_code, stdout, stderr); a spawn failure or
    a timeout is exit -1 with the reason in stderr, never an exception."""
    try:
        r = subprocess.run(_resolve_argv(argv), capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout or _CLI["cli_timeout_sec"],
                           cwd=cwd, input=input_text)
        return r.returncode, r.stdout or "", r.stderr or ""
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", f"{type(e).__name__}: {e}"


def _github_token() -> str:
    """The GitHub access token this Xod signed in with (keychain only,
    services/github_device.py), or "" when there is no sign-in."""
    try:
        from app.services import github_device
        return github_device.stored_token() or ""
    except Exception:
        return ""


def _github_identity() -> Dict[str, Any]:
    """The signed-in GitHub identity, proven by GitHub (github_device.verify)."""
    try:
        from app.services import github_device
        return github_device.verify() or {}
    except Exception:
        return {}


def _http_get_json(url: str, token: str) -> Dict[str, Any]:
    import httpx
    with httpx.Client(timeout=30) as c:
        r = c.get(url, headers={"Authorization": f"Bearer {token}",
                                "Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return r.json()


def _http_post_json(url: str, token: str, payload: Dict[str, Any]) -> Tuple[int, Any]:
    """POST JSON to the GitHub API. Returns (status, parsed body); an HTTP
    error status comes back as data, never raised, so a caller can name a
    403 (a closed channel) exactly instead of catching it blind."""
    import httpx
    try:
        with httpx.Client(timeout=30) as c:
            r = c.post(url, headers={"Authorization": f"Bearer {token}",
                                     "Accept": "application/vnd.github+json"},
                       json=payload)
    except Exception as e:
        return -1, {"error": f"{type(e).__name__}: {e}"}
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"raw": (r.text or "")[:300]}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ shape

def _shaped(claimed: Any, verified: Any, evidence: List[str],
            data: Any = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"claimed": claimed, "verified": verified,
                           "evidence": evidence}
    if data is not None:
        out["data"] = data
    return out


def _unknown(tool: str, reason: str, evidence: List[str]) -> Dict[str, Any]:
    return _shaped(None, {"state": "unknown", "tool": tool, "reason": reason},
                   evidence + [f"{tool}: {reason}"])


def _parse_json(text: str) -> Any:
    """Fleet CLIs print one JSON document with --json; be liberal anyway."""
    t = (text or "").strip()
    if not t:
        return None
    try:
        return json.loads(t)
    except ValueError:
        pass
    for i, ch in enumerate(t):
        if ch in "{[":
            try:
                return json.loads(t[i:])
            except ValueError:
                continue
    return None


def _cli_json(tool: str, argv: List[str], ok_exits=(0,), cwd: Optional[str] = None,
              input_text: Optional[str] = None) -> Dict[str, Any]:
    """Run a fleet CLI with --json and shape the answer. A verdict exit
    (racecheck's 1 on RACE) is an ok exit the caller declares."""
    code, out, err = _run_cli(argv, cwd=cwd, input_text=input_text)
    cmdline = " ".join(argv)
    evidence = [f"ran: {cmdline}", f"exit: {code}"]
    if code not in ok_exits:
        tail = (err or out or "").strip()[-300:]
        return _unknown(tool, f"the CLI exited {code}" + (f": {tail}" if tail else ""),
                        evidence)
    data = _parse_json(out)
    if data is None:
        return _unknown(tool, "the CLI answered without parseable JSON",
                        evidence + [f"stdout tail: {out.strip()[-300:]}"])
    return _shaped({"cli": tool, "exit": code}, {"state": "ok", "exit": code},
                   evidence, data=data)


def _need(args: Dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if not str(args.get(k) or "").strip()]
    if missing:
        raise ValueError(f"missing required arg(s): {', '.join(missing)}")


def _pkg(key: str) -> str:
    return str(_CLI[key])


# ------------------------------------------------------------------ greenlight

def _issue_url(args: Dict[str, Any]) -> str:
    url = str(args.get("issue_url") or "").strip()
    if url:
        return url
    repo = str(args.get("repo") or "").strip()
    issue = str(args.get("issue") or "").strip()
    if repo and issue:
        return f"https://github.com/{repo}/issues/{issue}"
    raise ValueError("missing required arg(s): issue_url, or repo and issue")


def _norm_candidate(c: Dict[str, Any]) -> Dict[str, Any]:
    repo = str(c.get("repo") or c.get("repository") or "").strip()
    url = str(c.get("issue_url") or c.get("url") or "").strip()
    issue: Any = c.get("issue", c.get("issue_number"))
    if (not repo or issue is None) and url:
        m = re.match(r"https://github\.com/([^/]+/[^/]+)/issues/(\d+)", url)
        if m:
            repo = repo or m.group(1)
            issue = issue if issue is not None else int(m.group(2))
    labels = c.get("labels") or []
    if isinstance(labels, list):
        labels = [str(x.get("name") if isinstance(x, dict) else x) for x in labels]
    return {"repo": repo, "issue": issue, "issue_url": url,
            "title": str(c.get("title") or c.get("issue_title") or ""),
            "labels": labels,
            "reasons": c.get("reasons") or c.get("xray") or [],
            "already_open_pr": bool(c.get("already_open_pr") or c.get("own_open_pr")),
            "raw": c}


def greenlight_wave(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage 0: the build queue from the live Agent Policy Index."""
    argv = ["npx", "-y", _pkg("greenlight"), "wave",
            "--repos", str(int(args.get("repos") or 6)), "--json"]
    if args.get("min_share"):
        argv += ["--min-share", str(int(args["min_share"]))]
    if args.get("stars"):
        argv += ["--stars", str(args["stars"])]
    if args.get("issues_per_repo"):
        argv += ["--issues-per-repo", str(int(args["issues_per_repo"]))]
    res = _cli_json("greenlight wave", argv)
    if res["verified"].get("state") != "ok":
        return res
    data = res["data"]
    raw_list: List[Any] = []
    if isinstance(data, list):
        raw_list = data
    elif isinstance(data, dict):
        for key in ("queue", "candidates", "results", "items", "repos"):
            if isinstance(data.get(key), list):
                raw_list = data[key]
                break
    candidates = [_norm_candidate(c) for c in raw_list if isinstance(c, dict)]
    res["claimed"] = {"queue_size": len(candidates)}
    res["verified"] = {"state": "ok", "candidates": candidates}
    res["evidence"] = res["evidence"] + [
        f"greenlight wave returned {len(candidates)} candidate(s) from the live policy index"]
    return res


_CHECK_VERDICTS = {"GO", "RISKY", "TAKEN", "GATED", "STALE"}


def greenlight_check(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage 1: Greenlight's verdict on one issue, with its reasons."""
    url = _issue_url(args)
    res = _cli_json("greenlight check",
                    ["npx", "-y", _pkg("greenlight"), "check", url, "--json"])
    if res["verified"].get("state") != "ok":
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    verdict = str(data.get("verdict") or data.get("status") or "").upper()
    reasons = data.get("reasons") or data.get("reason") or []
    if verdict not in _CHECK_VERDICTS:
        return _unknown("greenlight check",
                        f"the CLI answered with an unreadable verdict {verdict!r}",
                        res["evidence"])
    res["claimed"] = {"verdict": verdict, "reasons": reasons}
    res["verified"] = {"state": "ok", "verdict": verdict, "reasons": reasons}
    res["evidence"] = res["evidence"] + [f"verdict on {url}: {verdict}"]
    return res


def greenlight_friction(args: Dict[str, Any]) -> Dict[str, Any]:
    """How hard an outside PR is to land in a repo, 0 to 100, with gates."""
    _need(args, "repo")
    argv = ["npx", "-y", _pkg("greenlight"), "friction",
            str(args["repo"]).strip(), "--json"]
    if args.get("lite"):
        argv.append("--lite")
    res = _cli_json("greenlight friction", argv)
    if res["verified"].get("state") != "ok":
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    score = data.get("score", data.get("friction"))
    if not isinstance(score, (int, float)):
        return _unknown("greenlight friction", "the CLI answered without a score",
                        res["evidence"])
    res["claimed"] = {"score": score}
    res["verified"] = {"state": "ok", "score": score,
                       "gates": data.get("gates") or data.get("policy_gates") or [],
                       "behavioral_gate": data.get("behavioral_gate")}
    res["evidence"] = res["evidence"] + [f"friction for {args['repo']}: {score}"]
    return res


_RACE_VERDICTS = {"CLEAR", "SUSPECT", "RACE"}


def racecheck(args: Dict[str, Any]) -> Dict[str, Any]:
    """Gate 2's race check: open PRs fixing the same problem, linked or not.
    The CLI exits 1 on RACE; that is a verdict, not a failure."""
    _need(args, "repo", "issue")
    argv = ["npx", "-y", _pkg("greenlight"), "racecheck",
            str(args["repo"]).strip(), str(args["issue"]).strip(), "--json"]
    res = _cli_json("greenlight racecheck", argv, ok_exits=(0, 1))
    if res["verified"].get("state") != "ok":
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    verdict = str(data.get("verdict") or data.get("status") or "").upper()
    if verdict not in _RACE_VERDICTS and res["data"] is not None:
        # Some shapes answer with a matches list only; derive conservatively.
        matches = data.get("matches") or data.get("prs") or []
        verdict = "SUSPECT" if matches else "CLEAR"
    if verdict not in _RACE_VERDICTS:
        return _unknown("greenlight racecheck",
                        f"the CLI answered with an unreadable verdict {verdict!r}",
                        res["evidence"])
    matches = data.get("matches") or data.get("prs") or data.get("matched_prs") or []
    res["claimed"] = {"verdict": verdict, "matches": matches}
    res["verified"] = {"state": "ok", "verdict": verdict, "matches": matches}
    res["evidence"] = res["evidence"] + [
        f"racecheck {args['repo']}#{args['issue']}: {verdict}"]
    return res


# ------------------------------------------------------------------ toolkit gates

def reprocheck(args: Dict[str, Any]) -> Dict[str, Any]:
    """The red-green proof: the repro fails on the base, passes on the head."""
    _need(args, "repo_dir", "base", "head")
    argv = ["npx", "-y", _pkg("reprocheck"), "run",
            "--repo", str(args["repo_dir"]), "--base", str(args["base"]),
            "--head", str(args["head"]), "--json"]
    if args.get("cmd"):
        argv += ["--cmd", str(args["cmd"])]
    elif args.get("runner"):
        argv += ["--runner", str(args["runner"])]
    if args.get("test_file"):
        argv += ["--test-file", str(args["test_file"])]
    if args.get("test_name"):
        argv += ["--test-name", str(args["test_name"])]
    res = _cli_json("reprocheck", argv, ok_exits=(0, 1, 3))
    if res["verified"].get("state") != "ok":
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    verdict = data.get("verdict") or data.get("status") or data.get("result")
    res["claimed"] = {"verdict": verdict, "receipt": data}
    res["verified"] = {"state": "ok", "verdict": verdict, "receipt": data}
    res["evidence"] = res["evidence"] + [
        f"reprocheck {args['base']}..{args['head']}: {verdict}"]
    return res


_PROSE_KINDS = {"pr-body", "commit", "comment"}


def _prosecheck_argv() -> Optional[List[str]]:
    """Resolve the prosecheck command: the policy file's configured command,
    then the XOD_PROSECHECK_CLI env (a path to a dist/cli.js, or a JSON argv
    list), then the npm package (not yet published; resolves when it is)."""
    configured = _CLI.get("prosecheck_command")
    if configured:
        return [str(x) for x in configured]
    env = os.environ.get("XOD_PROSECHECK_CLI", "").strip()
    if env:
        if env.startswith("["):
            try:
                return [str(x) for x in json.loads(env)]
            except ValueError:
                pass
        return ["node", env]
    return ["npx", "-y", str(_CLI["prosecheck_package"])]


def prosecheck(args: Dict[str, Any]) -> Dict[str, Any]:
    """The prose gate: PR bodies, commit messages, replies. Exit 0 is PASS.
    Exit 1 is a FAIL only when the tool actually ran and answered with a
    verdict document; a run that could not obtain or execute the tool (an
    npx fetch failure against an unpublished package, a missing CLI file)
    also exits 1, and that reads unknown with the tool error named, never
    FAIL. Unknown blocks like a FAIL."""
    kind = str(args.get("kind") or "").strip()
    if kind not in _PROSE_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(sorted(_PROSE_KINDS))}")
    _need(args, "text")
    base_argv = _prosecheck_argv()
    if base_argv is None:
        return _unknown("prosecheck", "no prosecheck command is configured", [])
    argv = base_argv + [kind, "-", "--json"]
    code, out, err = _run_cli(argv, input_text=str(args["text"]))
    evidence = [f"ran: {' '.join(argv)}", f"exit: {code}"]
    if code == 0:
        return _shaped({"verdict": "PASS"}, {"state": "ok", "verdict": "PASS"},
                       evidence + ["prosecheck: PASS"], data=_parse_json(out))
    if code == 1:
        data = _parse_json(out)
        if isinstance(data, dict) and (str(data.get("verdict") or "").upper() == "FAIL"
                                       or data.get("findings")):
            # The tool ran and scored the text: this is a genuine FAIL.
            return _shaped({"verdict": "FAIL", "findings": data},
                           {"state": "ok", "verdict": "FAIL"},
                           evidence + ["prosecheck: FAIL"], data=data)
        # Exit 1 with no verdict document means the tool never ran: npm
        # answering 404 for an unpublished package, node failing to find
        # the CLI, a loader error. An unobtained tool is an unknown gate,
        # and the tool's own error is the named reason.
        tail = (err or out or "").strip()[-300:]
        return _unknown("prosecheck",
                        "prosecheck did not run, so the text was not scored"
                        + (f": {tail}" if tail else ""),
                        evidence)
    tail = (err or out or "").strip()[-300:]
    return _unknown("prosecheck",
                    f"prosecheck could not run (exit {code})" + (f": {tail}" if tail else ""),
                    evidence)


def commitcheck(args: Dict[str, Any]) -> Dict[str, Any]:
    """Commit history hygiene: subjects, sign-off, convention."""
    argv = ["npx", "-y", _pkg("commitcheck"), "check"]
    if args.get("target"):
        argv.append(str(args["target"]))
    argv.append("--json")
    res = _cli_json("commitcheck", argv, ok_exits=(0, 1),
                    cwd=str(args["repo_dir"]) if args.get("repo_dir") else None)
    if res["verified"].get("state") != "ok":
        return res
    data = res["data"]
    res["claimed"] = {"report": data}
    res["verified"] = {"state": "ok", "report": data}
    return res


def claimcheck_issue(args: Dict[str, Any]) -> Dict[str, Any]:
    """Claim state for one issue (claimcheck): who has claimed it, how
    long ago, and whether a PR already exists for the strongest claim."""
    url = _issue_url(args)
    res = _cli_json("claimcheck issue",
                    ["npx", "-y", _pkg("claimcheck"), "issue", url, "--json"])
    if res["verified"].get("state") != "ok":
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    claims = data.get("claims") or []
    strongest = data.get("strongest") or (claims[0] if claims else None)
    res["claimed"] = {"summary": data.get("summary"), "claims": claims}
    res["verified"] = {"state": "ok", "summary": data.get("summary"),
                       "claims": claims, "strongest": strongest}
    res["evidence"] = res["evidence"] + [
        f"claimcheck on {url}: {data.get('summary') or 'no summary'}"]
    return res


# ------------------------------------------------------------------ the full-CI gate

def _detect_commands(repo_dir: str) -> List[str]:
    """The repo's own full checks, when they are discoverable: package.json
    scripts (test, lint, typecheck) or pytest for a Python project."""
    pkg = Path(repo_dir) / "package.json"
    if pkg.exists():
        try:
            scripts = (json.loads(pkg.read_text(encoding="utf-8")) or {}).get("scripts") or {}
        except ValueError:
            scripts = {}
        cmds = [f"npm run {name}" for name in ("test", "lint", "typecheck") if name in scripts]
        if cmds:
            return cmds
    if any((Path(repo_dir) / m).exists()
           for m in ("pyproject.toml", "setup.py", "pytest.ini", "requirements.txt")):
        return ["python -m pytest -q"]
    return []


def _run_commands(commands: List[str], cwd: str, timeout: int) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for cmd in commands:
        try:
            r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout)
            out[cmd] = {"exit": r.returncode,
                        "tail": ((r.stdout or "") + (r.stderr or "")).strip()[-300:]}
        except (OSError, subprocess.TimeoutExpired) as e:
            out[cmd] = {"exit": None, "tail": f"{type(e).__name__}: {e}"}
    return out


def ci_gate(args: Dict[str, Any]) -> Dict[str, Any]:
    """pr_full_ci_gate: the repo's full root checks run on the head and on
    the base, and every failure labelled ours, base red, or unknown from
    the split. Green or base-red-explained passes; ours or unknown does not."""
    _need(args, "repo_dir")
    repo_dir = str(args["repo_dir"])
    base_ref = str(args.get("base_ref") or "origin/main")
    commands = [str(c) for c in args.get("commands") or []] or _detect_commands(repo_dir)
    timeout = int(args.get("timeout_per_command") or 600)
    if not commands:
        return _unknown("repo ci gate",
                        "no check commands given and none could be detected in the repo",
                        [f"repo_dir: {repo_dir}"])
    evidence = [f"base ref: {base_ref}", f"commands: {commands}"]
    ours = _run_commands(commands, repo_dir, timeout)
    tmp = tempfile.mkdtemp(prefix="xod-cigate-base-")
    base_dir = str(Path(tmp) / "base")
    code, _, err = _run_cli(["git", "-C", repo_dir, "worktree", "add", "--detach",
                             base_dir, base_ref])
    if code != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return _unknown("repo ci gate", f"could not create a base worktree: {err.strip()[-200:]}",
                        evidence)
    try:
        base = _run_commands(commands, base_dir, timeout)
    finally:
        _run_cli(["git", "-C", repo_dir, "worktree", "remove", "--force", base_dir])
        shutil.rmtree(tmp, ignore_errors=True)
    split: Dict[str, str] = {}
    for cmd in commands:
        o, b = ours[cmd]["exit"], base[cmd]["exit"]
        if o is None or b is None:
            split[cmd] = "unknown"
        elif o == 0 and b == 0:
            split[cmd] = "pass"
        elif o != 0 and b != 0:
            split[cmd] = "base_red"
        else:
            split[cmd] = "ours"
    classes = set(split.values())
    if "ours" in classes:
        state = "fail_ours"
    elif "unknown" in classes:
        state = "unknown"
    elif "base_red" in classes:
        state = "base_red_only"
    else:
        state = "pass"
    for cmd in commands:
        evidence.append(
            f"{cmd}: ours exit {ours[cmd]['exit']}, base exit {base[cmd]['exit']} -> {split[cmd]}")
    return _shaped({"ours": ours, "base": base},
                   {"state": state, "split": split}, evidence)


# ------------------------------------------------------------------ policy scan (Gate 3)

_AI_TERMS = re.compile(
    r"\b(a\.?i\.?|llm|agents?|chatgpt|copilot|claude|gpt|ai-generated|"
    r"machine-generated|ai tools?|automated tools?)\b", re.I)
_BAN_TERMS = re.compile(
    r"\b(ban|banned|not allowed|not permitted|do not|don't|never|"
    r"prohibit\w*|forbidden|disallow\w*|reject\w*|no ai\b)", re.I)
_HUMAN_PROSE = re.compile(
    r"(written by a human|human-written|your own words|write (it|them|the .{0,20}) "
    r"yourself|personally writ\w+|own voice)", re.I)
_CONTRIB_CONTEXT = re.compile(
    r"\b(contribut\w+|pull requests?|\bprs?\b|code|issues?|submi\w+|work)\b", re.I)
_PROSE_CONTEXT = re.compile(
    r"\b(description|comment|prose|summar\w+|write|writing|written|text)\b", re.I)
_GFI_TERMS = re.compile(r"good[ -]first[ -]issue|\bgfi\b", re.I)


def _sentences(text: str) -> List[str]:
    parts = re.split(r"[\n\r]+|(?<=[.!?])\s+", text or "")
    return [p.strip() for p in parts if p and p.strip()]


def scan_policy(texts: List[str], labels: Optional[List[str]] = None) -> Dict[str, Any]:
    """Read policy text (a repo's CONTRIBUTING or AGENTS file, or the policy
    evidence inside a Greenlight payload) for the rules Gate 3 enforces:
    label-scoped AI bans, blanket AI bans, and human-written-prose rules.
    Every finding carries the sentence it came from as its evidence."""
    norm_labels = {re.sub(r"[^a-z0-9]+", "-", str(x).lower()).strip("-")
                   for x in (labels or [])}
    bans: List[Dict[str, Any]] = []
    prose_rules: List[Dict[str, Any]] = []
    for text in texts:
        for sent in _sentences(str(text)):
            if not _AI_TERMS.search(sent) or len(sent) > 400:
                continue
            quote = sent.strip()[:300]
            if _HUMAN_PROSE.search(sent):
                prose_rules.append({"scope": "prose", "quote": quote,
                                    "requires_human_prose": True})
                continue
            if not _BAN_TERMS.search(sent):
                continue
            if _GFI_TERMS.search(sent):
                applies = bool(norm_labels & {"good-first-issue", "gfi"})
                bans.append({"scope": "good-first-issue", "quote": quote,
                             "applies": applies})
            elif _PROSE_CONTEXT.search(sent) and not _CONTRIB_CONTEXT.search(sent):
                prose_rules.append({"scope": "prose", "quote": quote,
                                    "requires_human_prose": True})
            elif _CONTRIB_CONTEXT.search(sent):
                bans.append({"scope": "all", "quote": quote, "applies": True})
    return {"bans": bans, "prose_rules": prose_rules}


# ------------------------------------------------------------------ evidence clearing
# A clearing is a person's recorded firsthand proof that a gate's on-paper
# verdict does not hold for this candidate: a maintainer invitation thread
# behind a STALE, a full firsthand read of the AI policy behind a GATED, a
# file-level inspection of every matched PR behind a SUSPECT. It is how
# the hand-run waves clear those verdicts, and find_work and open_plan
# accept the same records so the built-in worker can too. A record names
# its gate, the evidence text, its source, who recorded it, and when.
# Empty or placeholder evidence is not a record: it clears nothing.
# racecheck RACE is never clearable here, exactly as in the hand waves.

_CLEARING_GATES = ("greenlight", "racecheck")
_CLEARING_PLACEHOLDERS = {"", "n/a", "na", "none", "tbd", "todo", "unknown",
                          "placeholder", "cleared", "ok", "yes", "see notes",
                          "see above", "-", "--", "..."}


def _clearing_record(raw: Any) -> Optional[Dict[str, Any]]:
    """One clearing record, normalized, or None when it carries no usable
    evidence (not a dict, an unnamed gate, or empty or placeholder text)."""
    if not isinstance(raw, dict):
        return None
    gate = str(raw.get("gate") or "").strip().lower()
    text = str(raw.get("evidence") or "").strip()
    squashed = re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()
    if gate not in _CLEARING_GATES:
        return None
    if len(text) < 10 or squashed in _CLEARING_PLACEHOLDERS:
        return None
    return {"gate": gate, "evidence": text,
            "source": str(raw.get("source") or "").strip(),
            "recorded_by": str(raw.get("recorded_by") or "").strip(),
            "recorded_at": str(raw.get("recorded_at") or "").strip()}


def _normalize_clearings(clearings: Optional[List[Dict[str, Any]]],
                         legacy_cleared: bool = False,
                         legacy_evidence: Optional[List[str]] = None
                         ) -> Dict[str, List[Dict[str, Any]]]:
    """All usable clearing records, grouped by gate. The legacy inputs
    (evidence_cleared plus clearing_evidence, the phase 1 shape) become
    records for both gates, so callers written against them keep working."""
    by_gate: Dict[str, List[Dict[str, Any]]] = {g: [] for g in _CLEARING_GATES}
    for raw in clearings or []:
        rec = _clearing_record(raw)
        if rec:
            by_gate[rec["gate"]].append(rec)
    if legacy_cleared:
        for text in legacy_evidence or []:
            for gate in _CLEARING_GATES:
                rec = _clearing_record(
                    {"gate": gate, "evidence": text,
                     "source": "clearing evidence recorded by the caller"})
                if rec:
                    by_gate[gate].append(rec)
    return by_gate


def _clearings_for(clearings: Optional[List[Dict[str, Any]]], repo: str,
                   issue: Any, require_target: bool) -> List[Dict[str, Any]]:
    """The clearing records that belong to one candidate. A record that
    names a repo or an issue must match this candidate; find_work also
    requires the target to be named, since one run gates many candidates."""
    out: List[Dict[str, Any]] = []
    for raw in clearings or []:
        if not isinstance(raw, dict):
            continue
        r = str(raw.get("repo") or "").strip()
        i = raw.get("issue")
        if require_target and (not r or i is None):
            continue
        if r and r.lower() != (repo or "").lower():
            continue
        if i is not None and str(i) != str(issue):
            continue
        out.append(raw)
    return out


def _cleared_evidence(gate: str, verdict: str,
                      records: List[Dict[str, Any]]) -> str:
    parts = []
    for r in records:
        src = r["source"] or "an unnamed source"
        who = r["recorded_by"] or "an unnamed recorder"
        when = r["recorded_at"] or "an unrecorded time"
        parts.append(f"{r['evidence']} (source: {src}; "
                     f"recorded by {who} at {when})")
    return (f"{gate} verdict {verdict} cleared with recorded evidence: "
            + "; ".join(parts))


# ------------------------------------------------------------------ the gate pass

def evaluate_candidate(*, repo: str, labels: Optional[List[str]] = None,
                       check_verdict: Optional[str] = None,
                       race_verdict: Optional[str] = None,
                       policy_texts: Optional[List[str]] = None,
                       already_open_in_repo: bool = False,
                       issue_open: bool = True,
                       issue_assignee: Optional[str] = None,
                       own_login: Optional[str] = None,
                       evidence_cleared: bool = False,
                       clearing_evidence: Optional[List[str]] = None,
                       budget_remaining: Optional[int] = None,
                       for_open: bool = False,
                       policy: Optional[Dict[str, Any]] = None,
                       clearings: Optional[List[Dict[str, Any]]] = None
                       ) -> Dict[str, Any]:
    """The maintainer profile's gate pass, as one pure function. Verdicts
    are pass, kill, or defer; reasons and evidence say which gate decided.
    A gate nobody ran is passed in as None and reads as unknown: a defer,
    never a pass. That is the whole rule. Clearable verdicts (a Greenlight
    RISKY, STALE, or on-paper GATED; a racecheck SUSPECT) pass only with a
    usable clearing record for that gate; the result's "clearings" list
    logs every record with its original verdict and how it landed."""
    policy = policy or POLICY
    gates_policy = policy["gates"]
    reasons: List[str] = []
    evidence: List[str] = []
    gate_states: Dict[str, Dict[str, Any]] = {}
    flags: Dict[str, Any] = {}
    outcome = {"kill": False, "defer": False}
    records = _normalize_clearings(clearings, evidence_cleared, clearing_evidence)
    clearing_applied = {"greenlight": False, "racecheck": False}

    def kill(gate: str, reason: str, ev: Optional[str] = None) -> None:
        outcome["kill"] = True
        gate_states[gate] = {"state": "kill", "detail": reason}
        reasons.append(reason)
        if ev:
            evidence.append(ev)

    def defer(gate: str, reason: str) -> None:
        outcome["defer"] = True
        gate_states[gate] = {"state": "unknown", "detail": reason}
        reasons.append(reason)

    def ok(gate: str, detail: str, ev: Optional[str] = None) -> None:
        gate_states[gate] = {"state": "pass", "detail": detail}
        evidence.append(ev if ev else f"{gate}: {detail}")

    repo_l = (repo or "").lower()
    # Gate 4a: never-build and report-only (the lists are policy data).
    scoped = {k.lower(): v for k, v in (policy.get("never_build_labels") or {}).items()}
    if repo_l in {r.lower() for r in policy.get("report_only") or []}:
        kill("never_build", f"{repo} is report-only (a dedicated effort owns it)")
    elif repo_l in scoped:
        hit = [x for x in (labels or [])
               if re.sub(r"[^a-z0-9]+", "-", str(x).lower()).strip("-")
               in {re.sub(r"[^a-z0-9]+", "-", str(y).lower()).strip("-")
                   for y in scoped[repo_l]}]
        if hit:
            kill("never_build",
                 f"{repo} is never-build on label(s) {hit} (label-scoped standing rule)",
                 f"standing rule scopes {repo} off label(s) {scoped[repo_l]}")
        else:
            ok("never_build", f"{repo} is label-scoped; this candidate's labels do not hit the scope")
    elif repo_l in {r.lower() for r in policy.get("never_build") or []}:
        kill("never_build", f"{repo} is on the never-build list")
    else:
        ok("never_build", f"{repo} is not on the never-build list")

    # Gate 1: Greenlight's verdict on the issue.
    clearable = {v.upper() for v in gates_policy["greenlight_verdicts_clearable"]}
    if check_verdict is None:
        defer("greenlight", "Greenlight's verdict was not read (unknown gate)")
    elif check_verdict.upper() in gates_policy["greenlight_verdicts_pass"]:
        ok("greenlight", f"Greenlight verdict {check_verdict}")
    elif check_verdict.upper() in clearable and records["greenlight"]:
        clearing_applied["greenlight"] = True
        gate_states["greenlight"] = {
            "state": "pass",
            "detail": f"Greenlight verdict {check_verdict}, cleared with "
                      "recorded firsthand evidence",
            "cleared_from": check_verdict.upper()}
        evidence.append(_cleared_evidence("greenlight", check_verdict.upper(),
                                          records["greenlight"]))
    elif check_verdict.upper() == "RISKY":
        defer("greenlight",
              "Greenlight verdict RISKY and not evidence-cleared; a risky candidate defers")
    else:
        kill("greenlight", f"Greenlight verdict {check_verdict}")

    # Gate 2: the fresh racecheck. RACE is never clearable: the branches
    # below consult clearing records only for a clearable verdict, so a
    # record offered against a RACE changes nothing and is logged refused.
    if race_verdict is None:
        defer("racecheck", "racecheck was not run (unknown gate)")
    elif race_verdict.upper() in gates_policy["racecheck_pass"]:
        ok("racecheck", "racecheck CLEAR")
    elif (race_verdict.upper() in gates_policy["racecheck_clearable"]
          and records["racecheck"]):
        clearing_applied["racecheck"] = True
        gate_states["racecheck"] = {
            "state": "pass",
            "detail": f"racecheck {race_verdict}, cleared with recorded "
                      "evidence: every matched PR inspected and proven unrelated",
            "cleared_from": race_verdict.upper()}
        evidence.append(_cleared_evidence("racecheck", race_verdict.upper(),
                                          records["racecheck"]))
    elif race_verdict.upper() == "SUSPECT":
        kill("racecheck", "racecheck SUSPECT and not evidence-cleared")
    else:
        kill("racecheck", f"racecheck {race_verdict}")

    # Gate 3: the repo's own policy, read directly.
    scan = scan_policy(policy_texts or [], labels)
    applied = [b for b in scan["bans"] if b.get("applies")]
    if not policy_texts:
        defer("policy", "the repo's policy text was not read (unknown gate)")
    elif applied:
        for b in applied:
            kill("policy",
                 f"the repo's policy bans this work (scope: {b['scope']})",
                 f"policy quote: {b['quote']}")
    else:
        ok("policy", "no applicable AI scope ban in the policy text read",
           "; ".join(f"scoped but not applicable: {b['quote']}"
                     for b in scan["bans"] if not b.get("applies")) or None)
    if scan["prose_rules"]:
        flags["requires_human_prose"] = True
        evidence.append("policy requires human-written prose: "
                        + scan["prose_rules"][0]["quote"])

    # Gate 4b: one open PR per repo, the issue still open and unassigned.
    if already_open_in_repo and gates_policy["one_open_pr_per_repo"]:
        kill("availability", f"an open PR by this account already exists in {repo}")
    if not issue_open:
        kill("availability", "the issue is no longer open")
    if (issue_assignee and own_login
            and issue_assignee.lower() != own_login.lower()):
        kill("availability", f"the issue is assigned to {issue_assignee}")
    if "availability" not in gate_states:
        ok("availability", "no open PR in the repo; the issue is open and unassigned")

    # Gate 5: budget, only where a push phase is actually proposed.
    if for_open:
        floor = int(gates_policy["min_core_remaining_before_push"])
        if budget_remaining is None:
            defer("budget", "the API budget was not read (unknown gate)")
        elif budget_remaining < floor:
            defer("budget",
                  f"core remaining {budget_remaining} is under the {floor} floor")
        else:
            ok("budget", f"core remaining {budget_remaining} >= {floor}")

    # The clearing log: every usable record, the verdict it was offered
    # against, whether it was applied, and the gate's resulting state. A
    # record against a verdict that needed no clearing, or one that can
    # never clear (a racecheck RACE), is logged with applied False.
    clearing_log: List[Dict[str, Any]] = []
    for gate_name, original in (("greenlight", check_verdict),
                                ("racecheck", race_verdict)):
        for rec in records[gate_name]:
            clearing_log.append({
                "gate": gate_name,
                "original_verdict": str(original).upper() if original else None,
                "evidence": rec["evidence"], "source": rec["source"],
                "recorded_by": rec["recorded_by"],
                "recorded_at": rec["recorded_at"],
                "applied": clearing_applied[gate_name],
                "result": gate_states.get(gate_name, {}).get("state")})

    verdict = "kill" if outcome["kill"] else ("defer" if outcome["defer"] else "pass")
    return {"verdict": verdict, "reasons": reasons, "evidence": evidence,
            "gates": gate_states, "flags": flags, "clearings": clearing_log}


# ------------------------------------------------------------------ find work (Stages 0 to 2)

def _own_open_repos() -> Tuple[set, List[str]]:
    """Repos where the signed-in account already has an open PR, from the
    API directly. No sign-in means an empty set and the gap named."""
    token = _github_token()
    if not token:
        return set(), ["own open PRs not re-read: no GitHub sign-in stored on this Xod; "
                       "the wave queue's own exclusion is the only cover"]
    ident = _github_identity()
    login = str(ident.get("login") or "")
    if not login:
        return set(), ["own open PRs not re-read: the stored GitHub sign-in did not verify"]
    try:
        data = _http_get_json(
            f"https://api.github.com/search/issues?q=author:{login}+type:pr+state:open"
            f"&per_page=100", token)
    except Exception as e:
        return set(), [f"own open PRs not re-read: the search failed ({type(e).__name__})"]
    repos = set()
    for item in data.get("items") or []:
        m = re.match(r"https://api\.github\.com/repos/([^/]+/[^/]+)/", 
                     str(item.get("repository_url") or ""))
        if m:
            repos.add(m.group(1).lower())
    return repos, [f"own open PRs re-read from the API: {len(repos)} repo(s) covered"]


def find_work(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stages 0 to 2: discover from the live index, then gate every
    candidate with fresh tool evidence. Returns only shaped verdicts:
    passed candidates carry their gate evidence; kills and defers carry
    the gate that decided and why. args["clearings"] may carry recorded
    evidence clearings, one record per candidate and gate (repo, issue,
    gate, evidence, source, recorded_by, recorded_at); each candidate's
    entry logs the records that were applied to it."""
    limit = int(args.get("limit") or 8)
    wave = greenlight_wave(args)
    if wave["verified"].get("state") != "ok":
        return _shaped({"stage": "discover"}, wave["verified"],
                        wave["evidence"] + ["find work stopped at discovery"])
    candidates = wave["verified"]["candidates"]
    own_repos, own_evidence = _own_open_repos()
    evidence: List[str] = list(wave["evidence"]) + own_evidence
    passed: List[Dict[str, Any]] = []
    killed: List[Dict[str, Any]] = []
    deferred: List[Dict[str, Any]] = []
    seen_repos = set()
    considered = 0
    for cand in candidates:
        if considered >= limit:
            break
        repo = cand["repo"]
        if not repo or cand["issue"] is None:
            continue
        if repo.lower() in seen_repos:
            continue     # one candidate per repo per run, like the waves
        seen_repos.add(repo.lower())
        considered += 1
        entry: Dict[str, Any] = {"repo": repo, "issue": cand["issue"],
                                 "issue_url": cand["issue_url"],
                                 "title": cand["title"], "labels": cand["labels"]}
        cand_clearings = _clearings_for(args.get("clearings"), repo,
                                         cand["issue"], require_target=True)
        entry["clearings_supplied"] = len(cand_clearings)
        chk = greenlight_check({"issue_url": cand["issue_url"]})
        check_verdict = (chk["verified"].get("verdict")
                         if chk["verified"].get("state") == "ok" else None)
        entry["check"] = chk["claimed"] or chk["verified"]
        evidence += chk["evidence"]
        race_verdict = None
        # A GATED or STALE check normally ends the candidate here, so the
        # race gate is honestly recorded as not run. A usable greenlight
        # clearing record reopens the candidate, and then the race gate
        # runs fresh like any other candidate's.
        gl_cleared = any(rec and rec["gate"] == "greenlight"
                         for rec in (_clearing_record(r) for r in cand_clearings))
        if check_verdict not in ("GATED", "STALE") or gl_cleared:
            race = racecheck({"repo": repo, "issue": cand["issue"]})
            race_verdict = (race["verified"].get("verdict")
                            if race["verified"].get("state") == "ok" else None)
            entry["racecheck"] = race["claimed"] or race["verified"]
            evidence += race["evidence"]
        fric = greenlight_friction({"repo": repo, "lite": True})
        entry["friction"] = fric["verified"] if fric["verified"].get("state") == "ok" \
            else fric["verified"]
        evidence += fric["evidence"]
        policy_texts = [json.dumps(chk.get("data") or {}),
                        json.dumps(fric.get("data") or {}),
                        " ".join(str(r) for r in cand["reasons"])]
        gate = evaluate_candidate(
            repo=repo, labels=cand["labels"], check_verdict=check_verdict,
            race_verdict=race_verdict, policy_texts=policy_texts,
            already_open_in_repo=(repo.lower() in own_repos
                                  or bool(cand["already_open_pr"])),
            clearings=cand_clearings)
        entry["gate"] = gate
        entry["clearings"] = gate.get("clearings") or []
        evidence += [f"{repo}#{cand['issue']}: {e}" for e in gate["evidence"]]
        {"pass": passed, "kill": killed, "defer": deferred}[gate["verdict"]].append(entry)
    verified = {"state": "ok", "considered": considered,
                "passed": passed, "killed": killed, "deferred": deferred,
                "counts": {"passed": len(passed), "killed": len(killed),
                           "deferred": len(deferred)}}
    return _shaped({"stage": "find_work", "wave_queue": len(candidates)},
                   verified, evidence)


# ------------------------------------------------------------------ open plan (Stages 3 to 5, dry-run)

def open_plan(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stages 3 to 5 behind the approval model, exercised in dry-run only:
    re-run the gates fresh, run the prose, commit, and CI gates against the
    prepared work when it is provided, and return the plan with its
    evidence. The plan itself never pushes and never opens. With
    args["persist"] the plan is stored under a plan id so a person can
    approve that exact plan through the approval flow; repo_open_execute
    is the only path that turns an approved stored plan into a real open."""
    _need(args, "repo", "issue")
    repo, issue = str(args["repo"]), args["issue"]
    url = _issue_url(args)
    chk = greenlight_check({"issue_url": url})
    check_verdict = (chk["verified"].get("verdict")
                     if chk["verified"].get("state") == "ok" else None)
    race = racecheck({"repo": repo, "issue": issue})
    race_verdict = (race["verified"].get("verdict")
                    if race["verified"].get("state") == "ok" else None)
    plan_clearings = _clearings_for(args.get("clearings"), repo, issue,
                                    require_target=False)
    gate = evaluate_candidate(
        repo=repo, labels=args.get("labels") or [], check_verdict=check_verdict,
        race_verdict=race_verdict,
        policy_texts=[json.dumps(chk.get("data") or {})],
        already_open_in_repo=bool(args.get("already_open_in_repo")),
        budget_remaining=args.get("budget_remaining"), for_open=True,
        clearings=plan_clearings)
    evidence: List[str] = chk["evidence"] + race["evidence"] + gate["evidence"]
    blocked_by = [r for r in gate["reasons"]]
    gate_results: Dict[str, Any] = {"candidate": gate}

    if args.get("pr_body"):
        prose = prosecheck({"kind": "pr-body", "text": args["pr_body"]})
        gate_results["prose"] = prose["verified"]
        evidence += prose["evidence"]
        if prose["verified"].get("verdict") != "PASS":
            blocked_by.append("prosecheck did not PASS the draft PR body")
    else:
        gate_results["prose"] = {"state": "not_run",
                                 "reason": "no draft PR body was provided"}
        blocked_by.append("no draft PR body provided for the prose gate")

    if args.get("commit_messages"):
        fails = []
        for i, msg in enumerate(args["commit_messages"]):
            prose = prosecheck({"kind": "commit", "text": msg})
            evidence += prose["evidence"]
            if prose["verified"].get("verdict") != "PASS":
                fails.append(i)
        gate_results["commit_prose"] = {"state": "ok", "failed_indexes": fails}
        if fails:
            blocked_by.append(f"prosecheck did not PASS commit message(s) {fails}")

    if args.get("clone_dir"):
        cc = commitcheck({"repo_dir": args["clone_dir"],
                          "target": args.get("commit_target")})
        gate_results["commitcheck"] = cc["verified"]
        evidence += cc["evidence"]
        cig = ci_gate({"repo_dir": args["clone_dir"],
                       "base_ref": args.get("base_ref") or "origin/main",
                       "commands": args.get("commands") or []})
        gate_results["ci_gate"] = cig["verified"]
        evidence += cig["evidence"]
        if cig["verified"].get("state") not in ("pass", "base_red_only"):
            blocked_by.append(
                f"the full CI gate reads {cig['verified'].get('state')}, not pass")

    would_open = gate["verdict"] == "pass" and not blocked_by
    clone_branch = clone_head = None
    if args.get("clone_dir"):
        code_b, out_b, _ = _run_cli(["git", "-C", str(args["clone_dir"]),
                                     "branch", "--show-current"])
        code_h, out_h, _ = _run_cli(["git", "-C", str(args["clone_dir"]),
                                     "rev-parse", "HEAD"])
        if code_b == 0 and out_b.strip():
            clone_branch = out_b.strip()
        if code_h == 0 and out_h.strip():
            clone_head = out_h.strip()
        evidence.append(
            f"prepared clone: branch {clone_branch or 'unread'}, head "
            f"{(clone_head or 'unread')[:12]}")
    steps = [
        "fresh clone of the repo, smallest fix in the repo's own style",
        "repo tests run; new tests red on unfixed code when practical",
        "reprocheck: the repro fails on the base and passes with the fix",
        "prosecheck on the PR body and every commit message; commitcheck on the history",
        "the full CI gate: the repo's root checks, ours vs base split",
        "push the branch (never force), open the PR, verify it by read-back",
    ]
    evidence.append(
        "open policy: a real open runs only through repo_open_execute, "
        "behind a recorded approval for the exact stored plan; this plan "
        "call is a dry run. Nothing was pushed and no PR was opened.")
    verified = {"state": "dry_run", "would_open": would_open,
                "blocked_by": blocked_by, "gates": gate_results,
                "clearings": gate.get("clearings") or [],
                "requires_approval": bool(POLICY["open"]["requires_approval"]),
                "requires_human_prose": bool(gate["flags"].get("requires_human_prose"))}
    if clone_branch or clone_head:
        verified["branch"] = clone_branch
        verified["head_sha"] = clone_head
    if args.get("persist"):
        plan_id = "rw-" + uuid.uuid4().hex[:12]
        record = {
            "id": plan_id, "created_at": _now_iso(), "state": "awaiting_approval",
            "repo": repo, "issue": issue, "issue_url": url,
            "labels": args.get("labels") or [],
            "title": str(args.get("title") or ""),
            "pr_body": str(args.get("pr_body") or ""),
            "commit_messages": [str(m) for m in args.get("commit_messages") or []],
            "clone_dir": str(args.get("clone_dir") or ""),
            "base_ref": str(args.get("base_ref") or ""),
            "base_branch": str(args.get("base_branch") or ""),
            "commands": [str(c) for c in args.get("commands") or []],
            "branch": clone_branch, "head_sha": clone_head,
            "would_open": would_open, "blocked_by": list(blocked_by),
            "approval": None, "open_result": None}
        _save_plan(record)
        verified["plan_id"] = plan_id
        verified["plan_state"] = "awaiting_approval"
        try:
            from app.services import agent_log
            agent_log.approval_opened(
                f"repo-plan-{plan_id}", "repo_open", "xod",
                f"Open a PR in {repo} for issue #{issue}",
                f"plan {plan_id}: would_open {would_open}; "
                f"blocked_by {blocked_by}")
        except Exception:
            pass
        evidence.append(
            f"plan stored for approval as {plan_id}; nothing opens until a "
            "person approves this exact plan and repo_open_execute runs it")
    return _shaped({"plan_for": f"{repo}#{issue}", "steps": steps}, verified, evidence)


# ------------------------------------------------------------------ stored plans and the approval flow

def _store_dir() -> Path:
    d = DATA_DIR / "repo_worker"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _plan_path(plan_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "", str(plan_id or ""))
    return _store_dir() / "plans" / f"{safe}.json"


def _save_plan(plan: Dict[str, Any]) -> None:
    path = _plan_path(str(plan.get("id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=1), encoding="utf-8")


def _load_plan(plan_id: str) -> Optional[Dict[str, Any]]:
    path = _plan_path(plan_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def record_approval(plan_id: str, decision: str, decided_by: str = "",
                    via: str = "", human_prose: bool = False) -> Dict[str, Any]:
    """The person-side half of the approval flow: record a person's
    decision on one stored open plan. The app's approval surface calls
    this (the decision is also written into the agent approval record);
    the worker never calls it for itself, so an agent cannot approve its
    own plan. An approval covers this exact plan only: its repo, issue,
    PR body, and recorded head. human_prose records the approver's
    confirmation that a person wrote the PR body, which is what a repo's
    human-written-prose rule asks for; an approval alone does not
    confirm authorship."""
    plan = _load_plan(plan_id)
    if plan is None:
        return _unknown("repo plan approval",
                        f"no stored plan with id {plan_id!r}", [])
    d = str(decision or "").strip().lower()
    if d in ("approved", "approve", "allowed", "allow"):
        d = "approved"
    elif d in ("declined", "decline", "denied", "deny"):
        d = "declined"
    else:
        raise ValueError("decision must be approved or declined")
    if plan.get("state") == "opened":
        return _shaped({"plan_id": plan_id},
                       {"state": "opened", "note": "the plan already opened; "
                        "a later decision changes nothing"},
                       [f"plan {plan_id} already opened"])
    plan["approval"] = {"decision": d,
                        "decided_by": str(decided_by or ""),
                        "decided_at": _now_iso(), "via": str(via or ""),
                        "human_prose": bool(human_prose)}
    plan["state"] = "approved" if d == "approved" else "declined"
    _save_plan(plan)
    try:
        from app.services import agent_log
        agent_log.approval_decided(f"repo-plan-{plan_id}",
                                   "allowed" if d == "approved" else "declined")
    except Exception:
        pass
    return _shaped({"plan_id": plan_id, "decision": d},
                   {"state": plan["state"], "approval": plan["approval"]},
                   [f"approval recorded for plan {plan_id}: {d} "
                    f"by {decided_by or 'an unnamed approver'}"])


def open_execute(args: Dict[str, Any]) -> Dict[str, Any]:
    """The one real open. Runs only for a stored plan a person approved,
    and only when every lock holds: the policy kill switch is on
    (open.allow_real_open), the plan carries a recorded approval, the
    gates re-run fresh right now still pass with a live budget read, and
    the prepared clone still sits on the branch and head the plan
    recorded. It pushes the branch (never force), opens the PR through
    the API, and verifies the result by read-back: the stored plan, not
    the creation response, is what gets trusted. Any step that fails
    stops the open and names where, including the partial state where
    the branch pushed but the PR did not open."""
    _need(args, "plan_id")
    plan_id = str(args["plan_id"])
    plan = _load_plan(plan_id)
    if plan is None:
        return _unknown("repo open execute",
                        f"no stored plan with id {plan_id!r}; run repo_open_plan "
                        "with persist first", [])
    evidence = [f"plan {plan_id} for {plan.get('repo')}#{plan.get('issue')}, "
                f"stored {plan.get('created_at')}"]
    if plan.get("state") == "opened" and plan.get("open_result"):
        return _shaped({"plan_id": plan_id, "already_opened": True},
                       {"state": "opened", **plan["open_result"]},
                       evidence + ["this plan already opened; the stored "
                                   "read-back is returned and nothing was "
                                   "opened a second time"])
    if not POLICY["open"].get("allow_real_open"):
        return _shaped(None, {"state": "refused",
                              "reason": "repo_worker_policy.open.allow_real_open "
                                        "is False on this copy; real opens are "
                                        "hard-disabled here"},
                       evidence + ["refused: the policy kill switch is off"])
    approval = plan.get("approval") or {}
    if POLICY["open"].get("requires_approval") and approval.get("decision") != "approved":
        return _shaped(None, {"state": "refused",
                              "reason": "no recorded approval for this exact "
                                        "plan; an unapproved plan stays a dry run"},
                       evidence + ["refused: no approval record on this plan"])
    evidence.append(f"approval on record: {approval.get('decision')} by "
                    f"{approval.get('decided_by') or 'an unnamed approver'} "
                    f"at {approval.get('decided_at')}")
    token = _github_token()
    ident = _github_identity()
    login = str(ident.get("login") or "")
    if not token or not login:
        return _unknown("repo open execute",
                        "no verified GitHub sign-in is stored on this Xod", evidence)
    repo = str(plan.get("repo") or "")
    issue = plan.get("issue")
    # Fresh gates, right now, with a live budget read: the approval was
    # for the plan's evidence, and the world may have moved since.
    budget: Optional[int] = None
    try:
        rl = _http_get_json("https://api.github.com/rate_limit", token)
        budget = int(((rl.get("resources") or {}).get("core") or {}).get("remaining"))
    except Exception as e:
        evidence.append(f"budget read failed ({type(e).__name__}); the budget "
                        "gate will read unknown and block")
    fresh = open_plan({"repo": repo, "issue": issue,
                       "issue_url": plan.get("issue_url") or "",
                       "labels": plan.get("labels") or [],
                       "pr_body": plan.get("pr_body") or "",
                       "commit_messages": plan.get("commit_messages") or [],
                       "clone_dir": plan.get("clone_dir") or "",
                       "base_ref": plan.get("base_ref") or "",
                       "commands": plan.get("commands") or [],
                       "budget_remaining": budget})
    evidence += [f"fresh gate re-run: {e}" for e in fresh["evidence"]]
    if not fresh["verified"].get("would_open"):
        plan["state"] = "approved"
        plan["last_execute_block"] = {"at": _now_iso(),
                                      "blocked_by": fresh["verified"].get("blocked_by")}
        _save_plan(plan)
        return _shaped({"plan_id": plan_id},
                       {"state": "blocked",
                        "blocked_by": fresh["verified"].get("blocked_by")},
                       evidence + ["the fresh gates no longer pass, so nothing "
                                   "was pushed and no PR was opened; the "
                                   "approval stays on record for a fixed plan"])
    if fresh["verified"].get("requires_human_prose") \
            and not approval.get("human_prose"):
        plan["last_execute_block"] = {
            "at": _now_iso(),
            "blocked_by": ["the repo requires human-written PR prose and the "
                           "approval on record does not confirm a person "
                           "wrote this PR body"]}
        _save_plan(plan)
        return _shaped({"plan_id": plan_id},
                       {"state": "blocked",
                        "blocked_by": plan["last_execute_block"]["blocked_by"]},
                       evidence + ["the repo's human-written-prose rule blocks "
                                   "this open: an approval confirms intent, "
                                   "not authorship, so the approval must "
                                   "record that a person wrote the body"])
    clone_dir = str(plan.get("clone_dir") or "")
    if not clone_dir:
        return _shaped({"plan_id": plan_id},
                       {"state": "blocked",
                        "blocked_by": ["the plan recorded no prepared clone"]},
                       evidence)
    code_b, out_b, _ = _run_cli(["git", "-C", clone_dir, "branch", "--show-current"])
    code_h, out_h, _ = _run_cli(["git", "-C", clone_dir, "rev-parse", "HEAD"])
    branch_now = out_b.strip() if code_b == 0 else ""
    head_now = out_h.strip() if code_h == 0 else ""
    if not branch_now or not head_now:
        return _shaped({"plan_id": plan_id},
                       {"state": "blocked",
                        "blocked_by": ["the prepared clone's branch or head "
                                        "could not be read"]},
                       evidence)
    if plan.get("branch") and branch_now != plan["branch"]:
        return _shaped({"plan_id": plan_id},
                       {"state": "blocked",
                        "blocked_by": [f"the clone is on branch {branch_now}, "
                                        f"not the plan's {plan['branch']}"]},
                       evidence + ["refused: what was approved is not what "
                                   "would be pushed"])
    if plan.get("head_sha") and head_now != plan["head_sha"]:
        return _shaped({"plan_id": plan_id},
                       {"state": "blocked",
                        "blocked_by": ["the clone's head moved since the plan "
                                        "was approved"]},
                       evidence + ["refused: the approved head is "
                                   f"{plan['head_sha'][:12]}, the clone is at "
                                   f"{head_now[:12]}; re-plan and re-approve"])
    base_branch = str(plan.get("base_branch") or "")
    if not base_branch:
        try:
            repo_info = _http_get_json(f"https://api.github.com/repos/{repo}", token)
            base_branch = str(repo_info.get("default_branch") or "")
        except Exception as e:
            return _unknown("repo open execute",
                            f"the repo's default branch could not be read "
                            f"({type(e).__name__})", evidence)
    if not base_branch:
        return _unknown("repo open execute",
                        "the repo's default branch read back empty", evidence)
    code, out, err = _run_cli(["git", "-C", clone_dir, "push", "origin", branch_now])
    if code != 0:
        plan["state"] = "push_failed"
        plan["last_execute_block"] = {"at": _now_iso(),
                                      "blocked_by": [f"git push exited {code}"]}
        _save_plan(plan)
        return _shaped({"plan_id": plan_id},
                       {"state": "push_failed",
                        "reason": (err or out or "").strip()[-300:]},
                       evidence + [f"git push of {branch_now} failed with exit "
                                   f"{code}; nothing was opened"])
    evidence.append(f"pushed branch {branch_now} at {head_now[:12]} to origin "
                    "(no force, per policy)")
    title = (str(args.get("title") or "") or str(plan.get("title") or "")
             or next((ln.strip() for ln in str(plan.get("pr_body") or "").splitlines()
                      if ln.strip()), "") or f"Fix #{issue}")
    status, data = _http_post_json(
        f"https://api.github.com/repos/{repo}/pulls", token,
        {"title": title, "head": f"{login}:{branch_now}", "base": base_branch,
         "body": str(plan.get("pr_body") or "")})
    if status != 201 or not isinstance(data, dict) or not data.get("number"):
        plan["state"] = "pushed_unopened"
        plan["last_execute_block"] = {
            "at": _now_iso(),
            "blocked_by": [f"PR creation answered HTTP {status}"]}
        _save_plan(plan)
        return _shaped({"plan_id": plan_id, "branch": branch_now},
                       {"state": "pushed_unopened", "http_status": status,
                        "reason": str((data or {}).get("message")
                                      or (data or {}).get("error") or data)[:300]},
                       evidence + [f"the branch {branch_now} is pushed, but PR "
                                   "creation did not succeed; the open is "
                                   "recoverable from the pushed branch and "
                                   "was not retried blind"])
    number = int(data["number"])
    evidence.append(f"PR creation answered 201: #{number} "
                    f"({data.get('html_url')})")
    # Read-back: the creation response is a claim; the GET is the proof.
    try:
        pr = _http_get_json(f"https://api.github.com/repos/{repo}/pulls/{number}",
                            token)
    except Exception as e:
        plan["state"] = "opened_unverified"
        plan["open_result"] = {"number": number, "url": data.get("html_url")}
        _save_plan(plan)
        return _shaped({"plan_id": plan_id},
                       {"state": "opened_unverified", "number": number,
                        "url": data.get("html_url")},
                       evidence + [f"the PR was created but its read-back "
                                   f"failed ({type(e).__name__}); the open is "
                                   "claimed, not verified"])
    readback = {"number": number, "url": pr.get("html_url"),
                "pr_state": pr.get("state"),
                "head_sha": ((pr.get("head") or {}).get("sha")) or "",
                "head_ref": ((pr.get("head") or {}).get("ref")) or "",
                "base_ref": ((pr.get("base") or {}).get("ref")) or ""}
    ok = (readback["pr_state"] == "open" and readback["head_sha"] == head_now
          and readback["base_ref"] == base_branch)
    plan["open_result"] = readback
    plan["state"] = "opened" if ok else "opened_unverified"
    _save_plan(plan)
    evidence.append(
        f"read-back: PR state {readback['pr_state']}, head "
        f"{readback['head_sha'][:12]}, base {readback['base_ref']} "
        f"({'matches' if ok else 'DOES NOT MATCH'} the pushed head and base)")
    return _shaped({"plan_id": plan_id, "created": {"number": number,
                                                    "url": data.get("html_url")}},
                   {"state": plan["state"], **readback}, evidence)


# ------------------------------------------------------------------ watch (Stages 6 and 7, read side)

def _attention(pr: Dict[str, Any]) -> str:
    if pr.get("changes_requested") or pr.get("stale_label"):
        return "needs_attention"
    if pr.get("checks") in ("failure", "error") or pr.get("mergeable") is False:
        return "needs_attention"
    if pr.get("checks") in ("success",) and not pr.get("changes_requested"):
        return "waiting_on_maintainer"
    return "quiet"


def watch(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage 6's read side: the signed-in account's open PRs with their
    attention state (reviews, checks, conflicts, stale marks), prwatch
    style. Read-only. No sign-in on this Xod reads as an honest unknown."""
    token = _github_token()
    if not token:
        return _unknown("repo watch",
                        "no GitHub sign-in is stored on this Xod, so there is "
                        "no account whose open PRs could be read", [])
    ident = _github_identity()
    login = str(ident.get("login") or "")
    if not login:
        return _unknown("repo watch",
                        "the stored GitHub sign-in did not verify against GitHub", [])
    limit = min(int(args.get("limit") or 25), 50)
    try:
        data = _http_get_json(
            f"https://api.github.com/search/issues?q=author:{login}+type:pr+state:open"
            f"&per_page={limit}", token)
    except Exception as e:
        return _unknown("repo watch", f"the open-PR search failed ({type(e).__name__})", [])
    evidence = [f"identity verified as {login}",
                f"open PRs found: {len(data.get('items') or [])}"]
    prs: List[Dict[str, Any]] = []
    for item in (data.get("items") or [])[:limit]:
        m = re.match(r"https://api\.github\.com/repos/([^/]+/[^/]+)/",
                     str(item.get("repository_url") or ""))
        repo = m.group(1) if m else ""
        number = item.get("number")
        entry: Dict[str, Any] = {
            "repo": repo, "number": number, "title": item.get("title"),
            "url": item.get("html_url"),
            "labels": [str(x.get("name")) for x in item.get("labels") or []],
            "updated_at": item.get("updated_at")}
        entry["stale_label"] = any("stale" in x.lower() for x in entry["labels"])
        try:
            pr = _http_get_json(
                f"https://api.github.com/repos/{repo}/pulls/{number}", token)
            entry["mergeable"] = pr.get("mergeable")
            head = ((pr.get("head") or {}).get("sha")) or ""
            status = _http_get_json(
                f"https://api.github.com/repos/{repo}/commits/{head}/status", token)
            entry["checks"] = status.get("state")
            reviews = _http_get_json(
                f"https://api.github.com/repos/{repo}/pulls/{number}/reviews"
                f"?per_page=100", token)
            latest: Dict[str, str] = {}
            for r in reviews if isinstance(reviews, list) else []:
                who = str((r.get("user") or {}).get("login") or "")
                if who and r.get("state") in ("APPROVED", "CHANGES_REQUESTED"):
                    latest[who] = str(r["state"])
            entry["changes_requested"] = "CHANGES_REQUESTED" in latest.values()
            entry["approved"] = "APPROVED" in latest.values()
        except Exception as e:
            entry["detail_error"] = f"{type(e).__name__}"
            evidence.append(f"{repo}#{number}: detail read failed ({type(e).__name__})")
        entry["attention"] = _attention(entry)
        prs.append(entry)
    verified = {"state": "ok", "login": login,
                "needs_attention": [p for p in prs if p["attention"] == "needs_attention"],
                "waiting_on_maintainer": [p for p in prs
                                          if p["attention"] == "waiting_on_maintainer"],
                "quiet": [p for p in prs if p["attention"] == "quiet"]}
    return _shaped({"open_prs": len(prs)}, verified, evidence)


# ------------------------------------------------------------------ the claim (Stage 3)

_CLAIM_PHRASES = re.compile(
    r"(i would like to (work on|take|handle) this|i'?d like to (work on|take) "
    r"this|i'?ll take this|i will take this|i'?m working on this|i am working "
    r"on this|working on this one|claiming this)", re.I)


def _own_claim(comments: Any, login: str) -> Optional[Dict[str, Any]]:
    """The signed-in account's own claim comment on an issue, if one is
    there: author match plus a claim phrase, the claimcheck standard."""
    if not isinstance(comments, list):
        return None
    for c in comments:
        if not isinstance(c, dict):
            continue
        who = str(((c.get("user") or {}).get("login")) or "")
        body = str(c.get("body") or "")
        if who.lower() == login.lower() and _CLAIM_PHRASES.search(body):
            return {"comment_id": c.get("id"), "url": c.get("html_url"),
                    "created_at": c.get("created_at"),
                    "excerpt": body.strip()[:160]}
    return None


def _issue_comments(repo: str, issue: Any, token: str) -> Any:
    return _http_get_json(
        f"https://api.github.com/repos/{repo}/issues/{issue}/comments"
        f"?per_page=100", token)


def claim(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage 3: claim an issue where the repo requires it, and only there.
    Reads the claim state first (claimcheck plus the issue's own comments):
    a repo that does not require a claim is left alone, a claim already on
    the issue by this account is reported as claimed, and only a missing
    required claim is posted, as a short plain comment that must pass the
    prose gate, then verified by read-back. A closed comment channel (the
    API refusing the post) is reported as blocked with the status named;
    it is never routed around."""
    _need(args, "repo", "issue")
    repo, issue = str(args["repo"]), args["issue"]
    token = _github_token()
    ident = _github_identity()
    login = str(ident.get("login") or "")
    if not token or not login:
        return _unknown("repo claim",
                        "no verified GitHub sign-in is stored on this Xod", [])
    evidence = [f"identity verified as {login}"]
    try:
        comments = _issue_comments(repo, issue, token)
    except Exception as e:
        return _unknown("repo claim",
                        f"the issue's comments could not be read "
                        f"({type(e).__name__})", evidence)
    own = _own_claim(comments, login)
    cc = claimcheck_issue({"repo": repo, "issue": issue})
    evidence += cc["evidence"]
    others = None
    if cc["verified"].get("state") == "ok":
        strongest = cc["verified"].get("strongest") or {}
        if strongest and str(strongest.get("author") or "").lower() != login.lower():
            others = {"author": strongest.get("author"),
                      "bucket": strongest.get("bucket"),
                      "age_days": strongest.get("ageDays")}
    requires = args.get("requires_claim")
    if requires is None:
        chk = greenlight_check({"repo": repo, "issue": issue})
        fric = greenlight_friction({"repo": repo, "lite": True})
        evidence += chk["evidence"] + fric["evidence"]
        if chk["verified"].get("state") != "ok" \
                and fric["verified"].get("state") != "ok":
            return _unknown("repo claim",
                            "the claim requirement could not be read: the "
                            "check and friction reads both failed, so whether "
                            "this repo requires a claim is unknown", evidence)
        texts = [str(r) for r in (chk["verified"].get("reasons") or [])]
        texts += [json.dumps(g) for g in (fric["verified"].get("gates") or [])]
        hits = [t for t in texts if "claim" in t.lower()]
        requires = bool(hits)
        evidence.append("claim requirement read from the gate evidence: "
                        + (hits[0][:200] if hits else
                           "no claim requirement found in the check or friction evidence"))
    if not requires:
        return _shaped({"repo": repo, "issue": issue},
                       {"state": "not_required", "own_claim": own,
                        "other_claim": others},
                       evidence + ["the repo does not require a claim for this "
                                   "issue; nothing was posted"])
    if own:
        return _shaped({"repo": repo, "issue": issue},
                       {"state": "claimed", "own_claim": own,
                        "other_claim": others},
                       evidence + ["this account's claim is already on the "
                                   "issue; nothing new was posted"])
    text = str(args.get("text") or "").strip()
    if not text:
        text = str(POLICY["claim"]["text_template"])
        plan_line = str(args.get("plan") or "").strip()
        if plan_line:
            text = f"{text} Plan: {plan_line}"
    prose = prosecheck({"kind": "comment", "text": text})
    evidence += prose["evidence"]
    if prose["verified"].get("state") != "ok":
        return _shaped({"repo": repo, "issue": issue},
                       {"state": "unknown", "own_claim": None,
                        "reason": "the claim text could not be prose-checked, "
                                  "and an unchecked claim is not posted"},
                       evidence)
    if prose["verified"].get("verdict") != "PASS":
        return _shaped({"repo": repo, "issue": issue},
                       {"state": "blocked", "own_claim": None,
                        "reason": "the claim text did not PASS the prose gate"},
                       evidence)
    status, data = _http_post_json(
        f"https://api.github.com/repos/{repo}/issues/{issue}/comments",
        token, {"body": text})
    if status != 201:
        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("message") or data.get("error") or "")
        return _shaped({"repo": repo, "issue": issue},
                       {"state": "blocked", "http_status": status,
                        "own_claim": None,
                        "reason": f"the claim post was refused (HTTP {status})"
                                  + (f": {msg}" if msg else "")},
                       evidence + ["the comment channel is closed for this "
                                   "account on this issue; the claim was not "
                                   "posted and was not routed around"])
    evidence.append(f"claim posted (HTTP 201, comment "
                    f"{(data or {}).get('id') if isinstance(data, dict) else '?'})")
    try:
        comments2 = _issue_comments(repo, issue, token)
    except Exception as e:
        return _shaped({"repo": repo, "issue": issue},
                       {"state": "posted_unverified", "own_claim": None},
                       evidence + [f"the claim posted but its read-back failed "
                                   f"({type(e).__name__}); the claim is "
                                   "claimed, not verified"])
    own2 = _own_claim(comments2, login)
    if own2:
        return _shaped({"repo": repo, "issue": issue},
                       {"state": "claimed", "own_claim": own2,
                        "other_claim": others},
                       evidence + ["read-back confirms this account's claim "
                                   "comment is on the issue"])
    return _shaped({"repo": repo, "issue": issue},
                   {"state": "posted_unverified", "own_claim": None},
                   evidence + ["the claim posted but no matching claim comment "
                               "by this account reads back; the claim is "
                               "claimed, not verified"])


# ------------------------------------------------------------------ babysit reply drafts (Stage 6)

def _drafts_path() -> Path:
    return _store_dir() / "reply_drafts.json"


def _load_drafts() -> List[Dict[str, Any]]:
    path = _drafts_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save_drafts(drafts: List[Dict[str, Any]]) -> None:
    _drafts_path().write_text(json.dumps(drafts, indent=1), encoding="utf-8")


def draft_reply(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage 6's write side, drafting only: a reply to a maintainer is
    composed by the caller, must PASS the prose gate as a comment (the
    policy's reply limits), and is then stored in this Xod's local draft
    store, verified by read-back. Repo Worker never posts a reply: the
    draft waits in the store for the campaign's posting step, and a FAIL
    or an unknown prose gate stores nothing. With args["list"], returns
    the stored drafts instead."""
    if args.get("list"):
        drafts = _load_drafts()
        return _shaped({"drafts": len(drafts)},
                       {"state": "ok", "drafts": drafts},
                       [f"{len(drafts)} drafted reply(ies) in the local store; "
                        "none of them has been posted"])
    _need(args, "repo", "number", "text")
    repo, number = str(args["repo"]), int(args["number"])
    prose = prosecheck({"kind": "comment", "text": str(args["text"])})
    evidence = list(prose["evidence"])
    if prose["verified"].get("state") != "ok":
        return _shaped({"repo": repo, "number": number},
                       {"state": "unknown", "stored": False,
                        "reason": "the reply could not be prose-checked, and "
                                  "an unchecked reply is not drafted"},
                       evidence)
    if prose["verified"].get("verdict") != "PASS":
        return _shaped({"repo": repo, "number": number},
                       {"state": "not_stored", "stored": False,
                        "findings": (prose.get("data") or {}),
                        "reason": "prosecheck FAIL: rewrite the reply shorter "
                                  "and plainer, then draft again; a FAIL is "
                                  "never waived"},
                       evidence)
    draft = {"id": "rwd-" + uuid.uuid4().hex[:12],
             "repo": repo, "number": number,
             "text": str(args["text"]),
             "in_reply_to": str(args.get("in_reply_to") or ""),
             "created_at": _now_iso(), "prose": "PASS",
             "status": "drafted_not_posted"}
    drafts = _load_drafts()
    drafts.append(draft)
    _save_drafts(drafts)
    found = any(d.get("id") == draft["id"] for d in _load_drafts())
    confirm = "confirmed by read-back" if found else "NOT confirmed by read-back"
    return _shaped({"repo": repo, "number": number, "draft_id": draft["id"]},
                   {"state": "drafted" if found else "store_unverified",
                    "draft_id": draft["id"], "posted": False},
                   evidence + [f"draft {draft['id']} stored locally and "
                               f"{confirm}; Repo Worker drafts replies, "
                               "it never posts them"])


# ------------------------------------------------------------------ dispatch

TOOLS = {
    "repo_wave": greenlight_wave,
    "repo_check": greenlight_check,
    "repo_friction": greenlight_friction,
    "repo_racecheck": racecheck,
    "repo_reprocheck": reprocheck,
    "repo_prosecheck": prosecheck,
    "repo_commitcheck": commitcheck,
    "repo_claimcheck": claimcheck_issue,
    "repo_ci_gate": ci_gate,
    "repo_find_work": find_work,
    "repo_open_plan": open_plan,
    "repo_open_execute": open_execute,
    "repo_claim": claim,
    "repo_watch": watch,
    "repo_draft_reply": draft_reply,
}


def run_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """One Repo Worker tool by its surface name. Raises ValueError for a
    bad request shape; tool failures come back as shaped unknowns."""
    fn = TOOLS.get(name)
    if fn is None:
        raise ValueError(f"unknown repo worker tool {name!r}")
    return fn(args or {})
