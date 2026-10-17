"""
Answers written for each job before Xod applies: why this company, why this role, a short cover letter, and the parts of
your experience that fit, from your résumé and that job's posting.

Why before and not during: the browser only types answers it can trace to you (browser_sandbox._value_is_grounded), so
it never makes one up on a form. These are written once per job, checked, saved where you can read them
(data/applications/<company>-<title>.json), and handed to the application as answers you approved.

Checked: a sentence that names a company, a number or a skill that's in neither your résumé nor the posting is dropped,
so an answer can't claim what you haven't done. No model, or no résumé text: no answers, and the form asks you.
"""
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xod.job_tailor")

KEYS = ("why_company", "why_role", "fit", "cover_letter")
PROMPT = """You're helping {name} apply for a job. Write their answers in first person, from their résumé and this posting only.
Never claim a job, skill, number or result that isn't in the résumé.
Sound like a person, not a template: open each answer with something concrete (a detail of the company's work, or
something they built), and never use "I am drawn to", "aligns perfectly", "passion", "I am excited", "leverage",
"I believe", or "your mission". Plain, confident, short sentences.

Return ONLY a JSON object:
  why_company   2-3 sentences: why {company}, naming something specific from the posting
  why_role      2-3 sentences: why this role, tied to what they've done
  fit           2-3 sentences: their most relevant experience for this role, with specifics from the résumé
  cover_letter  a short cover letter, 120-170 words, no "Dear Hiring Manager" filler, signed with their name

THE JOB: {title} at {company} ({location})
{posting}

THEIR RÉSUMÉ:
{resume}
"""


def resume_text(profile: Dict[str, Any]) -> str:
    path = os.path.expanduser(str(profile.get("resume_path") or ""))
    if not path or not os.path.isfile(path):
        return ""
    from app.services.document_parser import document_parser_service
    r = document_parser_service.parse_file(path, 12000)
    t = "" if r.get("error") else (r.get("content") or "")
    return "" if t.startswith("[Scanned") else t


_WORD = re.compile(r"[A-Za-z][\w+#.-]*[\w+#]|[A-Za-z]")
_NUMBER = re.compile(r"\b\d[\d,.]*\+?%?")
_COMMON = {"i", "i'm", "i've", "i'd", "dear", "thank", "thanks", "best", "sincerely", "regards", "hello", "hi", "ai", "us", "usa", "ok",
           "monday", "friday", "english"}


def _names(sent: str) -> List[str]:
    """What a sentence claims by name: capitalized words after its first word, acronyms, camelCase, and numbers."""
    words = _WORD.findall(sent)
    out = [w for i, w in enumerate(words) if w.lower() not in _COMMON and (
        (i > 0 and w[:1].isupper()) or (len(w) > 1 and w.isupper()) or re.search(r"[a-z][A-Z]", w))]
    return out + _NUMBER.findall(sent)


def grounded(text: str, sources: str) -> str:
    """Drops each sentence that names something (a company, a tool, an acronym, a number) found in neither source, and
    any with an email address or a link: an answer never sends anyone anywhere."""
    low = sources.lower()
    keep = [s for s in re.split(r"(?<=[.!?])\s+", text.strip())
            if all(n.lower().rstrip(".,%+") in low for n in _names(s)) and not re.search(r"@|https?://|www\.", s)]
    out = " ".join(keep).strip()
    # A dropped sentence can leave the next one starting mid-thought ("Additionally, …").
    out = re.sub(r"^(?:Additionally|Also|Furthermore|Moreover|In addition|Plus|And|That said),?\s+", "", out)
    return out[:1].upper() + out[1:]


def without_orders(posting: str) -> str:
    """A posting with what could steer the answers taken out: sentences that give orders to an AI (services/sentinel.py's
    patterns) or speak to one, and any with an email address or a link (an answer never needs one)."""
    from app.services.sentinel import _INJECTION_RE
    return " ".join(s for s in re.split(r"(?<=[.!?])\s+", posting or "")
                    if not (_INJECTION_RE.search(s) or re.search(r"@|https?://|www\.|\b(?:AI|LLM|automated)\s+(?:assistants?|agents?|models?|tools?)\b.{0,60}\b(?:you|must|should|please|before)\b", s, re.I)))


async def kit(job: Dict[str, Any], profile: Dict[str, Any], resume: str, timeout: float = 60) -> Dict[str, str]:
    """The answers for one job, checked; {} if there's no model or no résumé to write them from."""
    if not resume.strip():
        return {}
    from app.services import usage
    from app.services.llm import llm_service, parse_llm_json
    prompt = PROMPT.format(name=profile.get("full_name") or "the candidate", company=job.get("company") or "the company",
                           title=job.get("title") or "this role", location=job.get("location") or "",
                           posting=without_orders(job.get("summary") or "")[:3500] or "(no description)", resume=resume[:7000])
    tag = usage.purpose.set("job_answers")     # Usage tab: "Job answers" (no task tag: a résumé isn't training data)
    try:
        msg = await asyncio.wait_for(llm_service.chat_completion([{"role": "user", "content": prompt}], temperature=0.4,
                                                                 max_tokens=900, think=False), timeout)
        d = parse_llm_json(msg.get("content") or "")
    except Exception as e:
        logger.info(f"no answers for {job.get('company')}: {type(e).__name__}: {e}")
        return {}
    finally:
        usage.purpose.reset(tag)
    sources = resume + "\n" + without_orders(job.get("summary") or "") + "\n" + " ".join(str(job.get(k) or "") for k in ("company", "title", "location"))
    sources += "\n" + str(profile.get("full_name") or "")
    out = {}
    for k in KEYS:
        v = grounded(str(d.get(k) or ""), sources)
        if len(v) > 40:
            out[k] = v[:1500]
    return out


def save(job: Dict[str, Any], answers: Dict[str, str]) -> Optional[str]:
    from app.paths import DATA_DIR
    if not answers:
        return None
    folder = Path(DATA_DIR) / "applications"
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", f"{job.get('company', '')}-{job.get('title', '')}".lower()).strip("-")[:80] or "job"
    path = folder / f"{slug}.json"
    path.write_text(json.dumps({"company": job.get("company"), "title": job.get("title"), "url": job.get("apply_url") or job.get("url"),
                                "written_at": time.time(), **answers}, indent=2))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return str(path)


def for_request(job: Dict[str, Any], answers: Dict[str, str]) -> str:
    """The answers, as they go into the application task (and so count as given by you)."""
    if not answers:
        return ""
    co = job.get("company") or "the company"
    lines = ["\n\nAnswers I approved for this application (use them as written; shorten one only if the box has a limit):"]
    if answers.get("why_company"):
        lines.append(f"- Why {co} / why do you want to work here: {answers['why_company']}")
    if answers.get("why_role"):
        lines.append(f"- Why this role / what interests you about it: {answers['why_role']}")
    if answers.get("fit"):
        lines.append(f"- Relevant experience / why you're a fit / tell us about yourself: {answers['fit']}")
    if answers.get("cover_letter"):
        lines.append(f"- Cover letter / anything else you'd like to share: {answers['cover_letter']}")
    return "\n".join(lines)


async def kits(jobs: List[Dict[str, Any]], profile: Dict[str, Any], budget: float = 90) -> List[Dict[str, str]]:
    """Answers for several jobs at once (a few at a time), within one time budget; a job that runs out gets none."""
    resume = await asyncio.to_thread(resume_text, profile)
    if not resume:
        return [{} for _ in jobs]
    sem = asyncio.Semaphore(3)

    async def one(j):
        async with sem:
            return await kit(j, profile, resume)
    tasks = [asyncio.create_task(one(j)) for j in jobs]
    done, pending = await asyncio.wait(tasks, timeout=budget)
    for t in pending:
        t.cancel()
    return [t.result() if t in done and not t.exception() else {} for t in tasks]
