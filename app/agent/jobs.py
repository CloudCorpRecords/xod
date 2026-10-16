"""
Job hunting in chat: "find me FDE jobs", then "only remote ones", "apply to 2 and 5".

  find     services/job_hunt.py finds real, open postings on companies' own job boards and ranks
           them; the chat gets a jobs card (Apply / Open per row, filters) and a short answer
  narrow   follow-ups filter the last results without searching again (remote, a city, a company,
           seniority, "show more")
  apply    each application becomes a task in Tasks: Xod's browser opens the posting, fills the form
           from your job profile and résumé, and stops for your OK before it submits. If your
           profile is missing the basics, the job profile form opens first.
"""
import json
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.services import job_hunt

logger = logging.getLogger("xod.jobs")

MARK = "<!-- xod-jobs -->"
JOB_RE = re.compile(r"\b(jobs?|roles?|openings?|positions?|hiring|internships?|vacanc(y|ies)|gigs?)\b", re.I)
INTENT_RE = re.compile(r"\b(find|search|research|look|looking|lookign|any|show|get|hunt|list|who'?s hiring|open|apply|need|want|recommend|help me)\b", re.I)
NOT_JOBS_RE = re.compile(r"\b(background|cron|print|batch|build|ci|queued?|running|scheduled|steve|blow|odd|inside|nose|side)\s+jobs?\b|"
                         r"\bjobs? (queue|scheduler|runner|to be done)\b|\brole[- ]?play|\b(what|which) roles? (does|do|did|is|are)\b", re.I)
APPLY_RE = re.compile(r"\bapply\b", re.I)
NARROW_RE = re.compile(r"^\s*(and |ok(ay)? |now |just |please )*(only|just|show( me)?|filter|remote|hybrid|on-?site|in |near |at |not |except|excluding|without|no |more|all|reset|clear|"
                       r"hide|exclude|newest|latest|recent|senior|junior|entry|staff|top \d+|the best)\b", re.I)
REQUIRED = ("full_name", "email", "resume_path")
PROFILE_FIELDS = ("full_name", "email", "phone", "location", "linkedin", "github", "portfolio", "resume_path", "work_authorization",
                  "needs_sponsorship", "years_of_experience", "desired_salary", "willing_to_relocate", "short_bio", "common_answers")
# "just do all" / "fill it in for me": Xod fills the profile from your résumé and applies, instead of opening the form.
DO_ALL_RE = re.compile(r"\b(do (it |them |this |that )?all|do (it )?(all|everything) for me|do everything|just do it|you do it|go ahead|"
                       r"fill (it|them|this|everything|my (job )?profile|it all|the (form|profile))( (in|out))?( for me)?|"
                       r"(handle|take care of) (it|everything|the rest)|use my (resume|résumé|cv))\b", re.I)
_pending: List[str] = []           # the jobs waiting for a profile ("apply to 2 and 5" before it was filled in)


def _last_jobs_turn(messages: List[Dict[str, Any]]) -> bool:
    for m in reversed(messages[:-1][-4:]):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            return MARK in m["content"]
    return False


def route(query: str, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    q = (query or "").strip()
    if not q or len(q) > 600 or re.search(r"https?://", q):
        return None                   # "apply to … <url>" is a browser task, not a search
    follow = _last_jobs_turn(messages) and job_hunt.last()
    if follow and DO_ALL_RE.search(q) and not re.search(r"\b\d{1,3}\b", q):
        return {"mode": "do_all"}
    if follow and APPLY_RE.search(q):
        return {"mode": "apply"}
    if follow and NARROW_RE.search(q) and not (JOB_RE.search(q) and INTENT_RE.search(q) and job_hunt.understand(q)["role"]):
        return {"mode": "narrow"}
    if JOB_RE.search(q) and INTENT_RE.search(q) and not NOT_JOBS_RE.search(q):
        if job_hunt.understand(q)["role"]:
            return {"mode": "find"}
    return None


def profile_status() -> Dict[str, Any]:
    from app.services.browser_sandbox import load_user_profile
    import os
    p = load_user_profile()
    missing = [k for k in REQUIRED if not p.get(k)]
    if p.get("resume_path") and not os.path.isfile(os.path.expanduser(str(p["resume_path"]))):
        missing.append("resume_path")
    return {"ready": not missing, "missing": sorted(set(missing))}


def save_profile(changes: Dict[str, Any]) -> None:
    """Writes the job profile (a private file in Xod's data folder) with these fields changed."""
    import os
    from pathlib import Path
    from app.services.browser_sandbox import PROFILE_PATH
    p = Path(PROFILE_PATH)
    try:
        data = json.loads(p.read_text())
    except Exception:
        data = {"_note": "Your details for filling forms (job applications). Stays on this computer."}
    for k in PROFILE_FIELDS:
        if k in changes:
            v = changes[k]
            data[k] = v.strip()[:4000] if isinstance(v, str) else v
    p.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


async def fill_profile(save: bool) -> Dict[str, Any]:
    """Your profile filled in from your résumé and what Xod knows (services/job_autofill.py); saved when save=True."""
    from app.services import job_autofill
    from app.services.browser_sandbox import load_user_profile
    r = await job_autofill.autofill(load_user_profile())
    if save and r["fields"]:
        save_profile(r["fields"])
    return r


NEED = {"full_name": "your name", "email": "your email", "resume_path": "your résumé"}


# ---------------------------------------------------------------- narrowing

def narrow(result: Dict[str, Any], q: str) -> Dict[str, Any]:
    jobs = list(result.get("jobs") or [])
    low = q.lower()
    notes = []
    if re.search(r"\bremote\b", low):
        jobs = [j for j in jobs if j.get("remote") or "remote" in (j.get("location") or "").lower()]
        notes.append("remote")
    if re.search(r"\bhybrid\b", low):
        jobs = [j for j in jobs if "hybrid" in (j.get("location") or "").lower() or "hybrid" in (j.get("summary") or "").lower()[:300]]
        notes.append("hybrid")
    m = re.search(r"\b(?:in|near|around)\s+([A-Za-z][A-Za-z .'-]{1,30})", q)
    if m and m.group(1).strip().lower() not in ("the", "a", "an"):
        place = m.group(1).strip().lower()
        jobs = [j for j in jobs if place in (j.get("location") or "").lower()]
        notes.append(f"in {m.group(1).strip()}")
    m = re.search(r"\b(?:not at|except(?: at)?|excluding|but not)\s+([A-Za-z][\w.&' -]{1,30})", q, re.I)
    if m:
        co = m.group(1).strip().lower()
        jobs = [j for j in jobs if co not in j["company"].lower()]
        notes.append(f"not at {m.group(1).strip()}")
    m = None if m else re.search(r"\bat\s+([A-Z][\w.&' -]{1,30})", q)
    if m:
        co = m.group(1).strip().lower()
        jobs = [j for j in jobs if co in j["company"].lower()]
        notes.append(f"at {m.group(1).strip()}")
    for word in ("senior", "staff", "junior", "entry", "lead", "principal"):
        if re.search(rf"\b(only |just )?{word}\b", low) and not re.search(rf"\b(no|without|hide|exclude)\s+{word}", low):
            jobs = [j for j in jobs if word in j["title"].lower()]
            notes.append(word)
    m = re.search(r"\b(?:no|without|hide|exclude)\s+(senior|staff|manager|managers|lead|head|director|principal)s?\b", low)
    if m:
        w = m.group(1).rstrip("s")
        jobs = [j for j in jobs if w not in j["title"].lower()]
        notes.append(f"no {w} roles")
    if re.search(r"\b(newest|latest|recent|new ones)\b", low):
        jobs = sorted(jobs, key=lambda j: j.get("age_days") if j.get("age_days") is not None else 999)
        notes.append("newest first")
    return {**result, "jobs": jobs, "narrowed": notes}


def pick(result: Dict[str, Any], q: str) -> List[Dict[str, Any]]:
    """'apply to 2 and 5' / 'the OpenAI Paris one' / 'the top 3' / 'all the remote ones'."""
    jobs = result.get("jobs") or []
    low = q.lower()
    m = re.search(r"\btop\s+(\d+)\b", low)
    if m:
        return jobs[: int(m.group(1))]
    if re.search(r"\b(all|every(one| one)?|them all)\b", low) and not re.search(r"\ball (the )?\w+ ones\b", low):
        return jobs[:10]
    nums = [int(n) for n in re.findall(r"(?:#|\b)(\d{1,3})\b", low)]
    if nums:
        return [jobs[n - 1] for n in nums if 0 < n <= len(jobs)]
    hits = []
    for j in jobs:
        co = j["company"].lower()
        if co and re.search(rf"\b{re.escape(co)}\b", low):
            words = [w for w in re.findall(r"[a-z]{3,}", low) if w not in co and w not in ("apply", "the", "one", "role", "job", "for", "and", "please", "that")]
            place = (j.get("location") or "").lower() + " " + j["title"].lower()
            if not words or any(w in place for w in words):
                hits.append(j)
    return hits[:10]


# ---------------------------------------------------------------- answers

def _age(d: Optional[float]) -> str:
    if d is None:
        return ""
    return "today" if d < 1 else "yesterday" if d < 2 else f"{int(d)}d ago" if d < 60 else f"{int(d / 30)}mo ago"


def _table(jobs: List[Dict[str, Any]], n: int = 12) -> str:
    rows = ["| # | Company | Role | Where | Posted | Pay |", "|---|---|---|---|---|---|"]
    for i, j in enumerate(jobs[:n], 1):
        wp = j.get("workplace") or ("Remote" if j.get("remote") else "")
        where = (j.get("location") or "").replace("|", "/")[:40] + (f" · {wp.lower()}" if wp and wp.lower() not in (j.get("location") or "").lower() else "")
        rows.append(f"| {i} | {j['company']} | [{j['title'][:70].replace('|', '/')}]({j['url']}) | {where} | {_age(j.get('age_days'))} | {(j.get('pay') or '')[:40].replace('|', '/')} |")
    return "\n".join(rows)


def _card(result: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("id", "company", "title", "location", "remote", "workplace", "age_days", "pay", "url", "apply_url", "department", "provider", "fit", "score")
    return {"type": "jobs", "role": result["spec"]["role"], "acronym": result["spec"].get("acronym"), "spec": result["spec"],
            "boards_checked": result.get("boards_checked"), "companies": result.get("companies"), "narrowed": result.get("narrowed") or [],
            "jobs": [{k: j.get(k) for k in keep} for j in (result.get("jobs") or [])[:60]], "others": result.get("others") or [],
            "profile": profile_status()}


async def stream(query: str, messages: List[Dict[str, Any]], decision: Dict[str, Any], execute_tool,
                 spoken: bool = False) -> AsyncGenerator[Dict[str, Any], None]:
    mode = decision["mode"]
    if mode == "find":
        spec = job_hunt.understand(query)
        expanded = f" ({spec['acronym']})" if spec.get("acronym") else ""
        yield {"type": "plan", "title": f"Finding {spec['role']}{expanded} jobs", "steps": [
            {"id": 1, "title": "Find companies hiring for it", "tools": ["web_search"], "status": "in_progress"},
            {"id": 2, "title": "Check their live job boards", "tools": ["job_boards"], "status": "pending"},
            {"id": 3, "title": "Rank them for you", "tools": [], "status": "pending"}]}
        progress: List[str] = []

        async def on_progress(text: str):
            progress.append(text)

        async def search(q: str):
            return await execute_tool("web_search", {"query": q, "count": 10})

        yield {"type": "status", "content": f"Looking for companies hiring {spec['role']}s…"}
        yield {"type": "tool_call", "name": "job_boards", "args": {"role": spec["role"], "location": spec.get("location") or None,
                                                                 "remote": spec.get("remote") or None}, "id": "jobs_1"}
        result = await job_hunt.hunt(query, search, on_progress)
        yield {"type": "plan_step", "step_id": 1, "status": "completed"}
        yield {"type": "plan_step", "step_id": 2, "status": "completed"}
        yield {"type": "tool_result", "name": "job_boards", "id": "jobs_1",
               "summary": f"{len(result['jobs'])} open roles at {result.get('companies', 0)} companies ({result.get('boards_checked', 0)} boards checked)",
               "data": {"success": True, "count": len(result["jobs"])}}
        yield {"type": "plan_step", "step_id": 3, "status": "completed"}
        async for ev in _present(result, spoken, first=True):
            yield ev
        return

    result = job_hunt.last() or {}
    if mode == "narrow":
        if re.search(r"\b(all|everything|show all|reset|clear (the )?filters?)\b", query, re.I) and result.get("all_jobs"):
            result = {**result, "jobs": result["all_jobs"], "narrowed": []}
        narrowed = narrow(result, query)
        narrowed["all_jobs"] = result.get("all_jobs") or result.get("jobs")
        if narrowed["jobs"]:
            job_hunt.save_view(narrowed)
        async for ev in _present(narrowed, spoken):
            yield ev
        return

    if mode == "do_all":
        async for ev in _do_all(result):
            yield ev
        return

    # apply
    chosen = pick(result, query)
    if not chosen:
        yield {"type": "content", "content": "Which ones? Say the numbers from the list (\"apply to 1 and 4\"), a company (\"the OpenAI Paris one\"), "
                                             "or \"the top 3\". Or press **Apply** on a row in the card.\n\n" + MARK}
        yield {"type": "done"}
        return
    status = profile_status()
    if not status["ready"]:
        # Look for the résumé first, so the form opens filled in for a check instead of empty.
        yield {"type": "status", "content": "Looking for your résumé to fill in your job profile…"}
        r = await fill_profile(save=False)
        _pending[:] = [j["id"] for j in chosen]
        yield {"type": "job_profile_needed", "missing": status["missing"], "ids": _pending[:], "suggested": r["fields"],
               "from": r["from"], "choices": r["choices"], "note": r.get("note")}
        s = "s" if len(chosen) != 1 else ""
        if r["fields"]:
            msg = (f"{r['note']} I opened **your job profile** with it filled in: check it and press **Save and apply**, "
                   f"or just say **\"do it all\"** and I'll save it and start the {len(chosen)} application{s}.")
        else:
            msg = (f"Before I apply I need {', '.join(NEED.get(m, m) for m in status['missing'])}. {r.get('note') or ''} I opened **your job profile**: "
                   f"fill it in (it stays on this computer) and I'll start the {len(chosen)} application{s} right after.")
        yield {"type": "content", "content": msg + "\n\n" + MARK}
        yield {"type": "done"}
        return
    tasks = apply(chosen)
    lines = [f"**Started {len(tasks)} application{'s' if len(tasks) != 1 else ''}** in Tasks:"]
    lines += [f"- {t['company']}: {t['title']}" for t in tasks]
    lines.append("\nFor each one, my browser opens the posting, fills the form from your profile and résumé, and **stops for your OK before submitting**. "
                 "Questions it can't answer from your profile come to you there too.")
    yield {"type": "jobs_applied", "tasks": tasks}
    yield {"type": "content", "content": "\n".join(lines) + "\n\n" + MARK}
    yield {"type": "done"}


async def _do_all(result: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
    """"Just do all": fill in the profile from the résumé, save it, and apply to the jobs waiting for it (or the list shown)."""
    jobs = result.get("jobs") or []
    waiting = [j for j in jobs if j.get("id") in set(_pending)]
    chosen = waiting or jobs[:10]
    yield {"type": "plan", "title": "Doing it all", "steps": [
        {"id": 1, "title": "Find your résumé and fill in your profile", "tools": ["job_profile"], "status": "in_progress"},
        {"id": 2, "title": f"Start {len(chosen)} application{'s' if len(chosen) != 1 else ''}", "tools": [], "status": "pending"}]}
    status = profile_status()
    r: Dict[str, Any] = {"fields": {}, "from": {}, "choices": []}
    if not status["ready"] or not waiting:
        yield {"type": "tool_call", "name": "job_profile", "args": {"from": "your résumé"}, "id": "jobs_fill"}
        r = await fill_profile(save=True)
        yield {"type": "tool_result", "name": "job_profile", "id": "jobs_fill", "summary": r.get("note") or "",
               "data": {"success": bool(r["fields"]), "filled": sorted(r["fields"])}}
        status = profile_status()
    yield {"type": "plan_step", "step_id": 1, "status": "completed" if status["ready"] else "failed"}
    if not status["ready"]:
        yield {"type": "job_profile_needed", "missing": status["missing"], "ids": [j["id"] for j in chosen], "suggested": {},
               "from": r["from"], "choices": r["choices"], "note": r.get("note")}
        yield {"type": "content", "content": f"{r.get('note') or ''} I still need {', '.join(NEED.get(m, m) for m in status['missing'])}, so I opened "
                                             "**your job profile** for that part. Everything else is filled in.\n\n" + MARK}
        yield {"type": "done"}
        return
    if not chosen:
        yield {"type": "content", "content": f"{r.get('note') or 'Your job profile is ready.'} Search for jobs and I'll apply.\n\n" + MARK}
        yield {"type": "done"}
        return
    _pending.clear()
    tasks = apply(chosen)
    yield {"type": "plan_step", "step_id": 2, "status": "completed"}
    filled = ", ".join(NEED.get(k, k.replace("_", " ")) for k in r["fields"]) if r["fields"] else ""
    lines = [(f"{r['note']} ({filled})." if filled else "Your job profile was ready.") + " You can change it any time in **your job profile**.",
             f"\n**Started {len(tasks)} application{'s' if len(tasks) != 1 else ''}** in Tasks" + ("" if waiting else " (the top of the list)") + ":"]
    lines += [f"- {t['company']}: {t['title']}" for t in tasks]
    lines.append("\nEach one fills the form and **stops for your OK before submitting**. Questions your profile doesn't answer "
                 "(like work authorization) come to you there.")
    yield {"type": "jobs_applied", "tasks": tasks}
    yield {"type": "content", "content": "\n".join(lines) + "\n\n" + MARK}
    yield {"type": "done"}


def apply(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from app.services.task_queue import task_queue
    out = []
    for j in jobs[:10]:
        url = j.get("apply_url") or j.get("url")
        t = task_queue.create(f"Apply to the {j['title']} role at {j['company']}: {url} . Fill the application with my profile and upload my "
                              f"résumé, answer questions from my profile, and stop for my OK before submitting.",
                              title=f"Apply: {j['title'][:50]} at {j['company']}")
        out.append({"task_id": t["id"], "title": j["title"], "company": j["company"], "url": url})
    return out


async def _present(result: Dict[str, Any], spoken: bool, first: bool = False) -> AsyncGenerator[Dict[str, Any], None]:
    spec = result.get("spec") or {}
    jobs = result.get("jobs") or []
    yield _card(result)
    role = spec.get("role") or "matching"
    exp = f" ({spec['acronym']})" if spec.get("acronym") and first else ""
    if not jobs:
        msg = (f"I couldn't find open {role}{exp} roles on the job boards I checked." if first else
               f"None of the {role} roles match that ({', '.join(result.get('narrowed') or [])}).")
        if result.get("others"):
            msg += "\n\nThese pages might list some:\n" + "\n".join(f"- [{o['title'][:80]}]({o['url']})" for o in result["others"][:5])
        yield {"type": "content", "content": msg + "\n\n" + MARK}
        yield {"type": "done"}
        return
    cos = sorted({j["company"] for j in jobs}, key=lambda c: -sum(1 for j in jobs if j["company"] == c))
    if spoken:
        yield {"type": "content", "content": f"I found {len(jobs)} open {role} roles at {len(cos)} companies, including {', '.join(cos[:3])}. "
                                             "They're on screen; tell me which to apply to.\n\n" + MARK}
        yield {"type": "done"}
        return
    head = (f"**{len(jobs)} open {role}{exp} roles at {len(cos)} companies**, checked live on their own job boards just now"
            + (f" ({', '.join(result['narrowed'])})" if result.get("narrowed") else "") + ".")
    remote = sum(1 for j in jobs if j.get("remote") or "remote" in (j.get("location") or "").lower())
    fresh = sum(1 for j in jobs if (j.get("age_days") or 99) <= 7)
    facts = []
    if fresh:
        facts.append(f"{fresh} posted in the last week")
    if remote:
        facts.append(f"{remote} remote")
    paid = [j for j in jobs if j.get("pay")]
    if paid:
        facts.append(f"{len(paid)} list pay")
    body = [head, ("Hiring most: " + ", ".join(cos[:6]) + ".") if len(cos) > 1 else "", ("; ".join(facts).capitalize() + ".") if facts else "",
            _table(jobs), "",
            "Say **\"apply to 1 and 3\"** (or press **Apply** in the card) and I'll fill in the applications in the background, stopping for your OK "
            "before each submit. Narrow it down with \"only remote\", \"in New York\", or \"no senior roles\"."]
    if first and not profile_status()["ready"]:
        body.append("_Tip: add your details and résumé in **your job profile** (button in the card) so I can apply for you._")
    yield {"type": "content", "content": "\n\n".join(b for b in body if b is not None) + "\n\n" + MARK}
    yield {"type": "done"}
