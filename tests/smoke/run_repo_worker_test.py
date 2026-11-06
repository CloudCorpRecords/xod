"""
Repo Worker (services/repo_worker.py) behind the harness surface (surface v3):
  - the manifest lists the eleven repo_* tools, each versioned and kinded
  - every wrapper answers in the {claimed, verified, evidence} shape, and a
    tool that cannot run reads verified "unknown", never a borrowed pass
  - the gate pass (repo_worker.evaluate_candidate): all gates green passes;
    an unknown gate defers and never passes; a label-scoped AI ban found in
    fixture policy text kills the matching candidate; RACE, GATED, the
    never-build list, an existing open PR, and another assignee all kill;
    RISKY and SUSPECT pass only with logged clearing evidence
  - find work runs the real flow over faked CLIs: one pass, and kills for
    TAKEN, never-build, and the label-scoped policy ban
  - the prose gate: PASS, FAIL, and the cannot-run unknown
  - evidence clearing: a recorded firsthand-evidence record clears a STALE,
    an on-paper GATED, and a SUSPECT through the pure gate, find work, and
    the open plan, and is logged with its original verdict and result;
    empty or placeholder records clear nothing, and a RACE never clears
  - the prose wrapper: an npx fetch failure or a missing CLI (exit 1 with
    no verdict document) reads unknown with the tool error named, not FAIL
  - the full-CI gate over a real tiny git repo: ours, base_red, and pass
  - the open plan is a dry run even when every gate passes: it says what
    would happen, what blocks it, and that nothing was pushed or opened
  - the approval-gated real open: a persisted plan opens only with a
    recorded approval for that exact plan, fresh gates, and the approved
    head, verified by read-back; no approval, a decline, or the policy
    kill switch each refuse, and an opened plan never opens twice
  - the claim flow: a required claim is posted once and verified by
    read-back, an existing claim is reported not duplicated, a repo with
    no claim requirement is left alone, and a refused post reads blocked
  - babysit reply drafts: a prose PASS stores a local draft that is never
    posted; a FAIL or an unknown prose gate stores nothing
  - watch with no GitHub sign-in reads unknown and names why; with faked
    API answers it classifies attention (changes requested needs attention)
The CLI runner and the GitHub seams are faked at the module seams, the way
the harness suite fakes harness._relay. No network, no real fleet CLIs.
  .venv/bin/python tests/smoke/run_repo_worker_test.py
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-repoworker-"))
os.environ.setdefault("XOD_SPEECH", "off")
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir()
sys.path.insert(0, str(ROOT))
results = []

REGISTRY = TMP / "registry.json"
REGISTRY.write_text(json.dumps({
    "family": "Test Family", "version": 0, "updated": "2026-11-05T00:00:00Z",
    "devices": [
        {"id": "macbook-pro", "name": "Test Mac", "platform": "macOS", "endpoint": "127.0.0.1",
         "capabilities": [], "public_key": "PLACEHOLDER-F0-no-key-enrolled",
         "signature": None, "last_seen": "2026-11-05T00:00:00Z", "status": "active", "note": None},
    ]}))
os.environ["XOD_FAMILY_REGISTRY_PATH"] = str(REGISTRY)
IP = "127.0.0.1"
TOKEN = "test-harness-token"
HDRS = {"X-Harness-Token": TOKEN}


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


# ------------------------------------------------------------------ fakes

WAVE = {"queue": [
    {"repo": "acme/widgets", "issue": 12, "issue_url": "https://github.com/acme/widgets/issues/12",
     "title": "Fix the widget frobnicator", "labels": ["bug"]},
    {"repo": "acme/taken", "issue": 5, "issue_url": "https://github.com/acme/taken/issues/5",
     "title": "Taken work", "labels": []},
    {"repo": "astral-sh/ruff", "issue": 9, "issue_url": "https://github.com/astral-sh/ruff/issues/9",
     "title": "Never build here", "labels": []},
    {"repo": "acme/scoped", "issue": 7, "issue_url": "https://github.com/acme/scoped/issues/7",
     "title": "Scoped starter issue", "labels": ["good-first-issue"]},
]}
CHECKS = {
    "acme/widgets": {"verdict": "GO", "reasons": ["starter issue, tests exist, no competing work"]},
    "acme/taken": {"verdict": "TAKEN", "reasons": ["an open PR already fixes this"]},
    "astral-sh/ruff": {"verdict": "GO", "reasons": ["starter issue"]},
    "acme/scoped": {"verdict": "GO", "reasons": [
        "starter issue", "AI agents are banned from good first issue work in this repo."]},
}
FAIL_PROSE_SPAWN = {"on": False}
PROSE_FETCH_FAIL = {"on": False}
PROSE_MODULE_FAIL = {"on": False}


def fake_run_cli(argv, timeout=None, cwd=None, input_text=None):
    joined = " ".join(str(a) for a in argv)
    if "prosecheck" in joined:
        if FAIL_PROSE_SPAWN["on"]:
            return -1, "", "FileNotFoundError: npx not found"
        if PROSE_FETCH_FAIL["on"]:
            # npm answering 404 for an unpublished package: exit 1, the
            # error on stderr, and no verdict document on stdout.
            return 1, "", ("npm error code E404\nnpm error 404 Not Found - GET "
                           "https://registry.npmjs.org/@owner%2fprosecheck - Not found")
        if PROSE_MODULE_FAIL["on"]:
            return 1, "", "Error: Cannot find module 'C:/tools/prosecheck/dist/cli.js'"
        words = len((input_text or "").split())
        if words > 120:
            return 1, json.dumps({"verdict": "FAIL", "findings": ["over the word limit"]}), ""
        return 0, json.dumps({"verdict": "PASS"}), ""
    if "commitcheck" in joined:
        return 0, json.dumps({"ok": True, "problems": []}), ""
    if "claimcheck" in joined:
        return 0, json.dumps({"issue": "acme/widgets#12", "number": 12, "title": "t",
                              "claims": [], "strongest": None,
                              "summary": "no claims on this issue"}), ""
    if "greenlight" in joined:
        if "wave" in argv:
            return 0, json.dumps(WAVE), ""
        if "check" in argv:
            url = argv[argv.index("check") + 1]
            repo = "/".join(url.split("/")[3:5])
            return 0, json.dumps(CHECKS[repo]), ""
        if "racecheck" in argv:
            return 0, json.dumps({"verdict": "CLEAR", "matches": []}), ""
        if "friction" in argv:
            return 0, json.dumps({"score": 82, "gates": []}), ""
    if argv and argv[0] == "git":
        return REAL_RUN_CLI(argv, timeout=timeout, cwd=cwd, input_text=input_text)
    return 2, "", f"unfaked argv: {joined}"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    *args], check=True, capture_output=True, text=True)


# ------------------------------------------------------------------ main

async def main():
    from app.services import harness, repo_worker
    from app.services.storage import set_setting
    harness._caller = lambda ip: {"host": "laptop", "same_account": True}
    set_setting(harness.TOKEN_SETTING, TOKEN)
    globals()["REAL_RUN_CLI"] = repo_worker._run_cli
    repo_worker._run_cli = fake_run_cli

    print("R1 the surface")
    _, man = harness.manifest(IP, HDRS)
    names = [t["name"] for t in man["tools"]]
    repo_names = [n for n in names if n.startswith("repo_")]
    check("the manifest is surface v3", man["version"] == 3, str(man.get("version")))
    check("the fifteen repo tools are listed, versioned and kinded",
          repo_names == ["repo_find_work", "repo_wave", "repo_check", "repo_friction",
                         "repo_racecheck", "repo_reprocheck", "repo_prosecheck",
                         "repo_commitcheck", "repo_ci_gate", "repo_claimcheck",
                         "repo_claim", "repo_open_plan", "repo_open_execute",
                         "repo_watch", "repo_draft_reply"]
          and all(t["version"] == 1 and t["kind"] in ("read", "write")
                  for t in man["tools"] if t["name"].startswith("repo_")), str(repo_names))

    print("R2 wrapper shapes through invoke")
    code, out = await harness.invoke(IP, HDRS, {"tool": "repo_check",
                                                "args": {"issue_url": "https://github.com/acme/widgets/issues/12"}})
    res = out.get("result") or {}
    check("repo_check answers ok with the verdict kept in claimed and verified",
          code == 200 and out["state"] == "ok" and res["claimed"]["verdict"] == "GO"
          and res["verified"]["verdict"] == "GO" and bool(res["evidence"]), str(out)[:300])
    try:
        await harness.invoke(IP, HDRS, {"tool": "repo_check", "args": {}})
        bad_shape = None
    except harness.HarnessError as e:
        bad_shape = e.status
    check("a repo tool with missing args is a 400", bad_shape == 400, str(bad_shape))
    code, out = await harness.invoke(IP, HDRS, {"tool": "repo_watch", "args": {}})
    check("repo_watch with no GitHub sign-in is a gate_unknown error envelope, not a pass",
          code == 200 and out["state"] == "error"
          and (out.get("error") or {}).get("code") == "gate_unknown"
          and out["verified"]["state"] == "unknown", str(out)[:300])

    print("R3 the gate pass, pure")
    ev = repo_worker.evaluate_candidate
    base = dict(repo="acme/widgets", labels=["bug"], check_verdict="GO",
                race_verdict="CLEAR", policy_texts=["Contributions welcome."])
    check("all gates green passes", ev(**base)["verdict"] == "pass", str(ev(**base)))
    g = ev(**{**base, "check_verdict": None})
    check("an unknown gate defers and never passes",
          g["verdict"] == "defer" and g["gates"]["greenlight"]["state"] == "unknown", str(g))
    check("an unread policy defers too",
          ev(**{**base, "policy_texts": []})["verdict"] == "defer")
    check("RACE kills", ev(**{**base, "race_verdict": "RACE"})["verdict"] == "kill")
    check("GATED kills", ev(**{**base, "check_verdict": "GATED"})["verdict"] == "kill")
    check("the never-build list kills",
          ev(**{**base, "repo": "astral-sh/ruff"})["verdict"] == "kill")
    check("an existing open PR in the repo kills",
          ev(**{**base, "already_open_in_repo": True})["verdict"] == "kill")
    check("another assignee kills",
          ev(**{**base, "issue_assignee": "someoneelse", "own_login": "me"})["verdict"] == "kill")
    check("RISKY without clearing evidence defers, with it passes",
          ev(**{**base, "check_verdict": "RISKY"})["verdict"] == "defer"
          and ev(**{**base, "check_verdict": "RISKY", "evidence_cleared": True,
                    "clearing_evidence": ["PR #44 touches docs only, file-level read"]})["verdict"] == "pass")
    check("SUSPECT without clearing evidence kills, with it passes",
          ev(**{**base, "race_verdict": "SUSPECT"})["verdict"] == "kill"
          and ev(**{**base, "race_verdict": "SUSPECT", "evidence_cleared": True,
                    "clearing_evidence": ["PR #9 is a different subsystem"]})["verdict"] == "pass")
    check("a low budget defers an open",
          ev(**base, for_open=True, budget_remaining=100)["verdict"] == "defer"
          and ev(**base, for_open=True, budget_remaining=5000)["verdict"] == "pass")

    print("R4 the policy scan and the label-scoped ban")
    ban_text = ["AI agents are banned from good first issue work in this repo."]
    scan = repo_worker.scan_policy(ban_text, ["good-first-issue"])
    check("the label-scoped ban is found and applies to the matching label",
          scan["bans"] and scan["bans"][0]["scope"] == "good-first-issue"
          and scan["bans"][0]["applies"] is True, str(scan))
    scan2 = repo_worker.scan_policy(ban_text, ["bug"])
    check("the same ban does not apply to an unscoped label",
          scan2["bans"] and scan2["bans"][0]["applies"] is False, str(scan2))
    g = ev(**{**base, "labels": ["good-first-issue"], "policy_texts": ban_text})
    check("the gate kills a candidate the label-scoped ban covers",
          g["verdict"] == "kill" and g["gates"]["policy"]["state"] == "kill", str(g))
    check("a blanket ban kills at any label",
          ev(**{**base, "policy_texts": ["AI-generated contributions are not allowed here."]})["verdict"] == "kill")
    prose_scan = repo_worker.scan_policy(
        ["Please write the PR description in your own words; AI may help with code."], [])
    check("a human-written-prose rule is a prose flag, not a ban",
          prose_scan["prose_rules"] and not prose_scan["bans"], str(prose_scan))

    print("R5 find work over faked CLIs")
    out = repo_worker.find_work({"repos": 4, "limit": 8})
    v = out["verified"]
    by_repo = {e["repo"]: e for grp in ("passed", "killed", "deferred") for e in v[grp]}
    check("find work considered the wave queue",
          v["considered"] == 4 and out["claimed"]["wave_queue"] == 4, str(v.get("counts")))
    check("the clean candidate passes with gate evidence",
          by_repo["acme/widgets"]["gate"]["verdict"] == "pass"
          and bool(by_repo["acme/widgets"]["gate"]["evidence"]), str(by_repo.get("acme/widgets"))[:300])
    check("TAKEN is killed by the greenlight gate",
          by_repo["acme/taken"]["gate"]["verdict"] == "kill"
          and by_repo["acme/taken"]["gate"]["gates"]["greenlight"]["state"] == "kill")
    check("the never-build repo is killed",
          by_repo["astral-sh/ruff"]["gate"]["gates"]["never_build"]["state"] == "kill")
    check("the label-scoped candidate is killed by the policy gate",
          by_repo["acme/scoped"]["gate"]["gates"]["policy"]["state"] == "kill",
          str(by_repo.get("acme/scoped"))[:300])

    print("R6 the prose gate")
    p = repo_worker.prosecheck({"kind": "pr-body", "text": "Fixes #12. Short and plain."})
    check("short prose passes", p["verified"].get("verdict") == "PASS", str(p)[:200])
    p = repo_worker.prosecheck({"kind": "pr-body", "text": "word " * 200})
    check("long prose fails", p["verified"].get("verdict") == "FAIL", str(p)[:200])
    FAIL_PROSE_SPAWN["on"] = True
    p = repo_worker.prosecheck({"kind": "commit", "text": "fix: anything"})
    check("a prosecheck that cannot run reads unknown",
          p["verified"].get("state") == "unknown", str(p)[:200])
    FAIL_PROSE_SPAWN["on"] = False

    print("R7 the full-CI gate over a real git repo")
    repo = TMP / "cigate"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "marker.txt").write_text("good", encoding="utf-8")
    (repo / "check_marker.py").write_text(
        "import sys\nsys.exit(0 if open('marker.txt').read().strip() == 'good' else 1)\n",
        encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    (repo / "marker.txt").write_text("bad", encoding="utf-8")
    git(repo, "commit", "-qam", "head breaks the marker")
    py = f'"{sys.executable}"'
    g = repo_worker.ci_gate({"repo_dir": str(repo), "base_ref": "HEAD~1",
                             "commands": [f"{py} check_marker.py",
                                          f"{py} -c \"import sys; sys.exit(3)\""]})
    split = g["verified"].get("split") or {}
    check("a head-only failure is labelled ours and fails the gate",
          g["verified"].get("state") == "fail_ours"
          and split.get(f"{py} check_marker.py") == "ours"
          and split.get(f"{py} -c \"import sys; sys.exit(3)\"") == "base_red", str(g["verified"])[:300])
    g2 = repo_worker.ci_gate({"repo_dir": str(repo), "base_ref": "HEAD~1",
                              "commands": [f"{py} -c \"import sys; sys.exit(3)\""]})
    check("a failure on both sides is base_red_only, which passes explained",
          g2["verified"].get("state") == "base_red_only", str(g2["verified"])[:200])
    g3 = repo_worker.ci_gate({"repo_dir": str(repo), "base_ref": "HEAD~1",
                              "commands": [f"{py} -c \"import sys; sys.exit(0)\""]})
    check("green on both sides passes", g3["verified"].get("state") == "pass",
          str(g3["verified"])[:200])

    print("R8 the open plan is a dry run")
    plan = repo_worker.open_plan({"repo": "acme/widgets", "issue": 12,
                                  "pr_body": "Fixes #12. Short and plain.",
                                  "budget_remaining": 5000})
    pv = plan["verified"]
    check("with every gate green the plan would open, but stays a dry run",
          pv["state"] == "dry_run" and pv["would_open"] is True
          and any("Nothing was pushed" in e for e in plan["evidence"]), str(pv)[:300])
    plan2 = repo_worker.open_plan({"repo": "acme/widgets", "issue": 12,
                                   "pr_body": "word " * 200, "budget_remaining": 5000})
    check("a prose FAIL blocks the plan",
          plan2["verified"]["would_open"] is False
          and any("prosecheck" in b for b in plan2["verified"]["blocked_by"]),
          str(plan2["verified"])[:300])

    print("R10 evidence clearing, through the gate and the flows")
    gl_rec = {"gate": "greenlight",
              "evidence": "The maintainer invited this fix on the issue thread; "
                          "the stale read rests on a month-old index snapshot.",
              "source": "the issue thread, maintainer comment",
              "recorded_by": "tree", "recorded_at": "2026-11-05"}
    g = ev(**{**base, "check_verdict": "STALE", "clearings": [gl_rec]})
    log = g.get("clearings") or []
    check("STALE clears with a recorded clearing, logged with verdict, evidence, result",
          g["verdict"] == "pass" and len(log) == 1
          and log[0]["original_verdict"] == "STALE" and log[0]["applied"] is True
          and log[0]["result"] == "pass" and "maintainer invited" in log[0]["evidence"].lower()
          and g["gates"]["greenlight"].get("cleared_from") == "STALE", str(g)[:300])
    check("STALE without a clearing still kills",
          ev(**{**base, "check_verdict": "STALE"})["verdict"] == "kill")
    gated_rec = {"gate": "greenlight",
                 "evidence": "Read AI_POLICY.md in full firsthand: it asks for a "
                             "discussion before large changes, and this fix is four lines.",
                 "source": "AI_POLICY.md in the repo, read firsthand",
                 "recorded_by": "tree", "recorded_at": "2026-11-05"}
    check("an on-paper GATED clears with a recorded clearing",
          ev(**{**base, "check_verdict": "GATED", "clearings": [gated_rec]})["verdict"] == "pass")
    check("a placeholder clearing changes nothing",
          ev(**{**base, "check_verdict": "GATED",
                "clearings": [{"gate": "greenlight", "evidence": "TBD"}]})["verdict"] == "kill")
    race_rec = {"gate": "racecheck",
                "evidence": "Matched PR read at file level: it touches docs only, "
                            "no overlap with the files this issue names.",
                "source": "the matched PR's diff",
                "recorded_by": "tree", "recorded_at": "2026-11-05"}
    check("an empty-evidence clearing changes nothing",
          ev(**{**base, "race_verdict": "SUSPECT",
                "clearings": [{"gate": "racecheck", "evidence": "   "}]})["verdict"] == "kill")
    check("SUSPECT clears with a recorded clearing and logs it",
          ev(**{**base, "race_verdict": "SUSPECT", "clearings": [race_rec]})["verdict"] == "pass"
          and (ev(**{**base, "race_verdict": "SUSPECT",
                     "clearings": [race_rec]}).get("clearings") or [{}])[0].get("applied") is True)
    g = ev(**{**base, "race_verdict": "RACE", "clearings": [race_rec]})
    log = g.get("clearings") or []
    check("RACE never clears, even with a recorded clearing, and the refusal is logged",
          g["verdict"] == "kill" and len(log) == 1 and log[0]["applied"] is False
          and log[0]["result"] == "kill" and log[0]["original_verdict"] == "RACE",
          str(g)[:300])
    check("a greenlight clearing does not clear the racecheck gate",
          ev(**{**base, "check_verdict": "RISKY", "race_verdict": "SUSPECT",
                "clearings": [gl_rec]})["verdict"] == "kill")
    saved_taken = CHECKS["acme/taken"]
    CHECKS["acme/taken"] = {"verdict": "STALE",
                             "reasons": ["the index snapshot is a month old"]}
    out = repo_worker.find_work({"repos": 4, "limit": 8})
    by_repo = {e["repo"]: e for grp in ("passed", "killed", "deferred")
               for e in out["verified"][grp]}
    check("find work kills a STALE candidate when no clearing is supplied",
          by_repo["acme/taken"]["gate"]["verdict"] == "kill",
          str(by_repo.get("acme/taken"))[:300])
    out = repo_worker.find_work({"repos": 4, "limit": 8,
                                 "clearings": [{"repo": "acme/taken", "issue": 5, **gl_rec}]})
    by_repo = {e["repo"]: e for grp in ("passed", "killed", "deferred")
               for e in out["verified"][grp]}
    ent = by_repo["acme/taken"]
    check("find work passes the same candidate with a recorded clearing and logs it",
          ent["gate"]["verdict"] == "pass" and (ent.get("clearings") or [])
          and ent["clearings"][0]["original_verdict"] == "STALE"
          and ent["clearings"][0]["applied"] is True, str(ent)[:300])
    CHECKS["acme/taken"] = saved_taken
    saved_widgets = CHECKS["acme/widgets"]
    CHECKS["acme/widgets"] = {"verdict": "GATED", "reasons": ["on paper gated"]}
    plan = repo_worker.open_plan({"repo": "acme/widgets", "issue": 12,
                                  "pr_body": "Fixes #12. Short and plain.",
                                  "budget_remaining": 5000})
    check("an open plan for a GATED candidate does not open by default",
          plan["verified"]["would_open"] is False
          and plan["verified"]["gates"]["candidate"]["verdict"] == "kill",
          str(plan["verified"])[:300])
    plan = repo_worker.open_plan({"repo": "acme/widgets", "issue": 12,
                                  "pr_body": "Fixes #12. Short and plain.",
                                  "budget_remaining": 5000,
                                  "clearings": [gated_rec]})
    pv = plan["verified"]
    check("the same plan would open with a recorded clearing, stays a dry run, logs it",
          pv["would_open"] is True and pv["state"] == "dry_run"
          and (pv.get("clearings") or [])
          and pv["clearings"][0]["original_verdict"] == "GATED"
          and pv["clearings"][0]["applied"] is True, str(pv)[:300])
    CHECKS["acme/widgets"] = saved_widgets

    print("R11 the prose wrapper never calls a fetch failure a FAIL")
    PROSE_FETCH_FAIL["on"] = True
    p = repo_worker.prosecheck({"kind": "pr-body", "text": "Fixes #12. Short and plain."})
    check("an npx fetch failure reads unknown with the tool error named, not FAIL",
          p["verified"].get("state") == "unknown"
          and p["verified"].get("verdict") is None and "404" in json.dumps(p),
          str(p)[:300])
    PROSE_FETCH_FAIL["on"] = False
    PROSE_MODULE_FAIL["on"] = True
    p = repo_worker.prosecheck({"kind": "commit", "text": "fix: anything"})
    check("a missing CLI reads unknown, not FAIL",
          p["verified"].get("state") == "unknown"
          and p["verified"].get("verdict") is None, str(p)[:300])
    PROSE_MODULE_FAIL["on"] = False

    print("R9 watch attention with faked API answers")
    real_token, real_ident, real_http = (repo_worker._github_token,
                                         repo_worker._github_identity,
                                         repo_worker._http_get_json)
    repo_worker._github_token = lambda: "fake-token"
    repo_worker._github_identity = lambda: {"login": "cloudcorp"}

    def fake_http(url, token):
        if "search/issues" in url:
            return {"items": [
                {"number": 31, "title": "Needs changes", "html_url": "https://github.com/acme/widgets/pull/31",
                 "repository_url": "https://api.github.com/repos/acme/widgets",
                 "labels": [], "updated_at": "2026-11-05T00:00:00Z"},
                {"number": 32, "title": "All green", "html_url": "https://github.com/acme/widgets/pull/32",
                 "repository_url": "https://api.github.com/repos/acme/widgets",
                 "labels": [], "updated_at": "2026-11-05T00:00:00Z"}]}
        if url.endswith("/pulls/31"):
            return {"mergeable": True, "head": {"sha": "aaa"}}
        if url.endswith("/pulls/32"):
            return {"mergeable": True, "head": {"sha": "bbb"}}
        if "/commits/aaa/status" in url:
            return {"state": "success"}
        if "/commits/bbb/status" in url:
            return {"state": "success"}
        if url.endswith("/pulls/31/reviews?per_page=100"):
            return [{"user": {"login": "maintainer"}, "state": "CHANGES_REQUESTED"}]
        if url.endswith("/pulls/32/reviews?per_page=100"):
            return [{"user": {"login": "maintainer"}, "state": "APPROVED"}]
        raise AssertionError(f"unfaked url {url}")

    repo_worker._http_get_json = fake_http
    w = repo_worker.watch({})
    wv = w["verified"]
    check("changes requested lands in needs_attention, approved and green waits",
          [p["number"] for p in wv["needs_attention"]] == [31]
          and [p["number"] for p in wv["waiting_on_maintainer"]] == [32], str(wv)[:300])
    repo_worker._github_token, repo_worker._github_identity, repo_worker._http_get_json = (
        real_token, real_ident, real_http)

    print("R12 the approval-gated real open")
    saved_seams = (repo_worker._github_token, repo_worker._github_identity,
                   repo_worker._http_get_json, repo_worker._http_post_json,
                   repo_worker._run_cli)
    repo_worker._github_token = lambda: "fake-token"
    repo_worker._github_identity = lambda: {"login": "cloudcorp"}
    oclone = TMP / "openclone"
    oclone.mkdir()
    git(oclone, "init", "-q")
    (oclone / "widget.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(oclone, "add", "-A")
    git(oclone, "commit", "-qm", "base")
    git(oclone, "checkout", "-qb", "fix-widget")
    (oclone / "widget.py").write_text("VALUE = 2\n", encoding="utf-8")
    git(oclone, "commit", "-qam", "fix: the widget frobnicator")
    head_sha = subprocess.run(["git", "-C", str(oclone), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    PUSHES = []
    POSTS = []

    def exec_run_cli(argv, timeout=None, cwd=None, input_text=None):
        if argv and argv[0] == "git" and "push" in argv:
            PUSHES.append(list(argv))
            return 0, "", ""
        return fake_run_cli(argv, timeout=timeout, cwd=cwd, input_text=input_text)

    def exec_http_get(url, token):
        if "rate_limit" in url:
            return {"resources": {"core": {"remaining": 5000}}}
        if url.endswith("/repos/acme/widgets"):
            return {"default_branch": "main"}
        if url.endswith("/pulls/77"):
            return {"number": 77, "html_url": "https://github.com/acme/widgets/pull/77",
                    "state": "open", "head": {"sha": head_sha, "ref": "fix-widget"},
                    "base": {"ref": "main"}}
        raise AssertionError(f"unfaked url {url}")

    def exec_http_post(url, token, payload):
        POSTS.append((url, payload))
        if url.endswith("/pulls"):
            return 201, {"number": 77,
                         "html_url": "https://github.com/acme/widgets/pull/77",
                         "state": "open"}
        raise AssertionError(f"unfaked url {url}")

    repo_worker._run_cli = exec_run_cli
    repo_worker._http_get_json = exec_http_get
    repo_worker._http_post_json = exec_http_post
    ok_cmd = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
    plan_args = {"repo": "acme/widgets", "issue": 12,
                 "pr_body": "Fixes #12. Short and plain.",
                 "commit_messages": ["fix: the widget frobnicator"],
                 "clone_dir": str(oclone), "base_ref": "HEAD~1",
                 "commands": [ok_cmd], "title": "Fix the widget frobnicator",
                 "budget_remaining": 5000, "persist": True}
    plan = repo_worker.open_plan(plan_args)
    pv = plan["verified"]
    plan_id = pv.get("plan_id")
    check("a persisted plan carries a plan id and the clone head it recorded",
          bool(plan_id) and pv.get("head_sha") == head_sha
          and pv.get("branch") == "fix-widget"
          and pv["state"] == "dry_run" and pv["would_open"] is True, str(pv)[:300])
    ex = repo_worker.open_execute({"plan_id": plan_id})
    check("execute without an approval refuses and touches nothing",
          ex["verified"].get("state") == "refused" and not PUSHES and not POSTS,
          str(ex["verified"])[:300])
    rec = repo_worker.record_approval(plan_id, "approved",
                                      decided_by="rene", via="test")
    check("the approval is recorded on the exact plan",
          rec["verified"].get("state") == "approved", str(rec["verified"])[:200])
    ex = repo_worker.open_execute({"plan_id": plan_id})
    ev2 = ex["verified"]
    check("an approved plan opens for real, verified by read-back",
          ev2.get("state") == "opened" and ev2.get("number") == 77
          and ev2.get("head_sha") == head_sha and len(PUSHES) == 1
          and len(POSTS) == 1 and POSTS[0][1].get("head") == "cloudcorp:fix-widget"
          and POSTS[0][1].get("base") == "main", str(ev2)[:300])
    ex = repo_worker.open_execute({"plan_id": plan_id})
    check("executing an opened plan opens nothing twice",
          ex["verified"].get("state") == "opened" and len(POSTS) == 1,
          str(ex["verified"])[:200])
    plan2 = repo_worker.open_plan(plan_args)["verified"]
    repo_worker.record_approval(plan2["plan_id"], "declined",
                                decided_by="rene", via="test")
    ex = repo_worker.open_execute({"plan_id": plan2["plan_id"]})
    check("a declined plan never opens",
          ex["verified"].get("state") == "refused" and len(POSTS) == 1,
          str(ex["verified"])[:200])
    plan3 = repo_worker.open_plan(plan_args)["verified"]
    repo_worker.record_approval(plan3["plan_id"], "approved",
                                decided_by="rene", via="test")
    saved_flag = repo_worker.POLICY["open"]["allow_real_open"]
    repo_worker.POLICY["open"]["allow_real_open"] = False
    ex = repo_worker.open_execute({"plan_id": plan3["plan_id"]})
    repo_worker.POLICY["open"]["allow_real_open"] = saved_flag
    check("the policy kill switch refuses even an approved plan",
          ex["verified"].get("state") == "refused" and len(POSTS) == 1,
          str(ex["verified"])[:200])
    saved_widgets = CHECKS["acme/widgets"]
    CHECKS["acme/widgets"] = {"verdict": "GO", "reasons": [
        "starter issue, tests exist, no competing work",
        "Please write the PR description in your own words; AI may help with code."]}
    plan4 = repo_worker.open_plan(plan_args)["verified"]
    repo_worker.record_approval(plan4["plan_id"], "approved",
                                decided_by="rene", via="test")
    posts_before = len(POSTS)
    ex = repo_worker.open_execute({"plan_id": plan4["plan_id"]})
    check("a human-written-prose rule blocks the open until the approval "
          "records a person wrote the body",
          ex["verified"].get("state") == "blocked"
          and len(POSTS) == posts_before, str(ex["verified"])[:300])
    repo_worker.record_approval(plan4["plan_id"], "approved",
                                decided_by="rene", via="test", human_prose=True)
    ex = repo_worker.open_execute({"plan_id": plan4["plan_id"]})
    check("with human prose confirmed on the approval, the same plan opens",
          ex["verified"].get("state") == "opened"
          and len(POSTS) == posts_before + 1, str(ex["verified"])[:300])
    CHECKS["acme/widgets"] = saved_widgets
    (repo_worker._github_token, repo_worker._github_identity,
     repo_worker._http_get_json, repo_worker._http_post_json,
     repo_worker._run_cli) = saved_seams

    print("R13 the claim flow")
    repo_worker._github_token = lambda: "fake-token"
    repo_worker._github_identity = lambda: {"login": "cloudcorp"}
    COMMENTS = {"12": [], "13": []}
    CLAIM_POSTS = []
    CLAIM_MODE = {"blocked": False}

    def claim_http_get(url, token):
        for num in ("12", "13"):
            if f"/issues/{num}/comments" in url:
                return list(COMMENTS[num])
        raise AssertionError(f"unfaked url {url}")

    def claim_http_post(url, token, payload):
        CLAIM_POSTS.append((url, payload))
        if CLAIM_MODE["blocked"]:
            return 403, {"message": "Forbidden"}
        num = "12" if "/issues/12/comments" in url else "13"
        comment = {"id": 900 + len(CLAIM_POSTS),
                   "html_url": f"https://github.com/acme/widgets/issues/{num}#issuecomment-900",
                   "user": {"login": "cloudcorp"}, "body": payload["body"],
                   "created_at": "2026-11-06T00:00:00Z"}
        COMMENTS[num].append(comment)
        return 201, comment

    repo_worker._http_get_json = claim_http_get
    repo_worker._http_post_json = claim_http_post
    c = repo_worker.claim({"repo": "acme/widgets", "issue": 12,
                           "requires_claim": True})
    check("a required claim is posted and verified by read-back",
          c["verified"].get("state") == "claimed"
          and (c["verified"].get("own_claim") or {}).get("comment_id") is not None
          and len(CLAIM_POSTS) == 1, str(c["verified"])[:300])
    c = repo_worker.claim({"repo": "acme/widgets", "issue": 12,
                           "requires_claim": True})
    check("an existing claim is reported, not posted twice",
          c["verified"].get("state") == "claimed" and len(CLAIM_POSTS) == 1,
          str(c["verified"])[:200])
    c = repo_worker.claim({"repo": "acme/widgets", "issue": 12})
    check("with no claim requirement in the gate evidence, nothing is posted",
          c["verified"].get("state") == "not_required" and len(CLAIM_POSTS) == 1,
          str(c["verified"])[:300])
    CLAIM_MODE["blocked"] = True
    c = repo_worker.claim({"repo": "acme/widgets", "issue": 13,
                           "requires_claim": True})
    check("a refused claim post reads blocked with the status named",
          c["verified"].get("state") == "blocked"
          and c["verified"].get("http_status") == 403, str(c["verified"])[:300])
    CLAIM_MODE["blocked"] = False

    print("R14 babysit reply drafts")
    d = repo_worker.draft_reply(
        {"repo": "acme/widgets", "number": 31,
         "text": "Thanks for the review. I will push the fix today.",
         "in_reply_to": "changes requested on the frobnicate path"})
    check("a passing reply is drafted locally, never posted",
          d["verified"].get("state") == "drafted"
          and d["verified"].get("posted") is False, str(d["verified"])[:300])
    before = repo_worker.draft_reply({"list": True})["verified"]["drafts"]
    d2 = repo_worker.draft_reply({"repo": "acme/widgets", "number": 31,
                                  "text": "word " * 200})
    after = repo_worker.draft_reply({"list": True})["verified"]["drafts"]
    check("a prose FAIL drafts nothing",
          d2["verified"].get("state") == "not_stored"
          and len(after) == len(before) == 1, str(d2["verified"])[:200])
    FAIL_PROSE_SPAWN["on"] = True
    d3 = repo_worker.draft_reply({"repo": "acme/widgets", "number": 31,
                                  "text": "short"})
    FAIL_PROSE_SPAWN["on"] = False
    after = repo_worker.draft_reply({"list": True})["verified"]["drafts"]
    check("an unknown prose gate drafts nothing either",
          d3["verified"].get("state") == "unknown" and len(after) == 1,
          str(d3["verified"])[:200])
    (repo_worker._github_token, repo_worker._github_identity,
     repo_worker._http_get_json, repo_worker._http_post_json) = (
        saved_seams[0], saved_seams[1], saved_seams[2], saved_seams[3])


if __name__ == "__main__":
    asyncio.run(main())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
