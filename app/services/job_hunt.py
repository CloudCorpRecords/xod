"""
Finding real job openings: "find me FDE jobs", "remote ML engineer roles in Europe".

General web search returns job-board landing pages and articles about a role, not openings. This
goes to the source: most startups and many big companies post on Ashby, Greenhouse or Lever, whose
job boards have public JSON APIs. So:

  1. understand   the role (acronyms expanded: FDE = Forward Deployed Engineer, SWE, SRE, MLE…),
                  other names for it, location, remote, seniority
  2. discover     which companies hire for it: site-restricted searches of the ATS domains
                  ("forward deployed engineer" site:jobs.ashbyhq.com …) give company board slugs;
                  boards that had matches before are remembered and checked again
  3. confirm      read each company's live board through its API and keep postings whose title
                  matches: every result is open today and links to its real application page
  4. rank         title match, freshness, location/remote fit, seniority, and (when a résumé is on
                  file) skills overlap

Nothing is applied for here; applying is a task that fills the form from your profile and stops for
your OK before submitting (see agent/jobs.py).
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import httpx

from app.paths import DATA_DIR

logger = logging.getLogger("xod.job_hunt")

BOARDS_FILE = DATA_DIR / "jobs" / "boards.json"
LAST_FILE = DATA_DIR / "jobs" / "last.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Xod/0.2 job search"}

ACRONYMS = {
    "fde": ("Forward Deployed Engineer", ["Forward Deployed Software Engineer", "Forward-Deployed Engineer", "Forward Deployed AI Engineer"]),
    "swe": ("Software Engineer", ["Software Developer", "Software Development Engineer"]),
    "sde": ("Software Development Engineer", ["Software Engineer"]),
    "sre": ("Site Reliability Engineer", ["SRE", "Reliability Engineer"]),
    "mle": ("Machine Learning Engineer", ["ML Engineer", "AI Engineer"]),
    "ml engineer": ("Machine Learning Engineer", ["ML Engineer", "AI Engineer"]),
    "ai engineer": ("AI Engineer", ["Machine Learning Engineer", "Applied AI Engineer", "LLM Engineer"]),
    "pm": ("Product Manager", ["Product Lead"]),
    "tpm": ("Technical Program Manager", ["Program Manager"]),
    "se": ("Solutions Engineer", ["Sales Engineer", "Solutions Architect"]),
    "sa": ("Solutions Architect", ["Solutions Engineer"]),
    "ae": ("Account Executive", ["Account Manager"]),
    "sdr": ("Sales Development Representative", ["Business Development Representative", "BDR"]),
    "bdr": ("Business Development Representative", ["Sales Development Representative", "SDR"]),
    "devrel": ("Developer Relations", ["Developer Advocate", "Developer Relations Engineer"]),
    "ds": ("Data Scientist", ["Data Science"]),
    "de": ("Data Engineer", ["Analytics Engineer"]),
    "qa": ("QA Engineer", ["Quality Assurance", "Test Engineer", "SDET"]),
    "ux": ("UX Designer", ["Product Designer", "UX/UI Designer"]),
    "fe": ("Frontend Engineer", ["Front End Engineer", "Front-End Developer"]),
    "be": ("Backend Engineer", ["Back End Engineer", "Backend Developer"]),
    "full stack": ("Full Stack Engineer", ["Fullstack Engineer", "Full-Stack Developer", "Full Stack Developer"]),
    "devops": ("DevOps Engineer", ["Platform Engineer", "Infrastructure Engineer"]),
    "cse": ("Customer Success Engineer", ["Customer Engineer", "Support Engineer"]),
}
SENIORITY = ["intern", "junior", "new grad", "entry", "mid", "senior", "staff", "principal", "lead", "head", "director", "manager"]
REMOTE_RE = re.compile(r"\bremote\b|work from home|wfh|anywhere", re.I)
ATS_RE = [
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.\-]+)", re.I)),
    ("greenhouse", re.compile(r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_\-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.\-]+)", re.I)),
]
NOT_SLUGS = {"jobs", "embed", "v1", "boards", "careers", "job_board", "search"}


# ------------------------------------------------------------------ 1. the request

def understand(query: str) -> Dict[str, Any]:
    """Role, its other names, where, remote, seniority. Deterministic: acronyms first, then the words."""
    q = re.sub(r"\s+", " ", query or "").strip()
    low = q.lower()
    role, aliases, acronym = "", [], ""
    for k, (full, al) in sorted(ACRONYMS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(k)}s?\b", low):
            role, aliases, acronym = full, list(al), (k.upper() if len(k) <= 4 and " " not in k else "")
            break
    if not role:
        m = re.search(r"(?:for|as an?|as|find|search|research|look(?:ing)? for|any|some|me)\s+(?:an?\s+)?(?:new\s+)?"
                      r"([a-z][a-z0-9+#/ .-]{2,60}?)\s+(?:jobs?|roles?|positions?|openings?|internships?|gigs?)\b", low)
        if m:
            role = m.group(1)
        else:
            m = re.search(r"([a-z][a-z0-9+#/ .-]{2,60}?)\s+(?:jobs?|roles?|positions?|openings?)\b", low)
            role = m.group(1) if m else ""
        role = re.sub(r"^(for|me|some|any|good|great|open|remote|new|the|a|an|im|i'm|i am|looking|lookign|research|find|search)\s+", "", role).strip()
        role = re.sub(r"\b(remote|hybrid|onsite)\b", "", role).strip()
        role = role.title() if role else ""
    loc = ""
    m = re.search(r"\b(?:in|near|around|based in)\s+([A-Z][A-Za-z .,'-]{2,40}?)(?:$|[.?!,]| and | or | for | with | that | who | remote)", q)
    if m and not re.match(r"(?i)(the|a|an|my|tech|ai|startups?)\b", m.group(1)):
        loc = m.group(1).strip(" ,.")
    seniority = next((s for s in SENIORITY if re.search(rf"\b{s}\b", low)), "")
    return {"role": role, "aliases": aliases, "acronym": acronym, "location": loc,
            "remote": bool(REMOTE_RE.search(q)), "seniority": seniority}


def title_matcher(role: str, aliases: Iterable[str]):
    names = [n.lower() for n in [role, *aliases] if n]
    stop = {"engineer", "developer", "manager", "the", "and", "of", "a"}
    wordsets = [set(re.findall(r"[a-z0-9+#]+", n)) for n in names]

    def score(title: str) -> float:
        t = (title or "").lower().replace("-", " ")
        tw = set(re.findall(r"[a-z0-9+#]+", t))
        best = 0.0
        for n, ws in zip(names, wordsets):
            if n.replace("-", " ") in t:
                best = max(best, 1.0)
                continue
            core = ws - stop or ws
            if core and core <= tw and (ws & stop <= tw or not ws & stop):
                best = max(best, 0.85)
            elif core and len(core & tw) / len(core) >= 0.67 and (ws & stop) <= tw:
                best = max(best, 0.6)
        return best
    return score


# ------------------------------------------------------------------ 2. which companies

NAMES: Dict[str, str] = {}      # board slug -> company name, learned from search result titles


def _name_from_title(title: str) -> str:
    """'Forward Deployed Engineer @ HappyRobot', 'Job Application for X at Databricks', 'OpenAI - FDE'."""
    t = (title or "").strip()
    for rx in (r"\s@\s*([^|\-–—]+)", r"\bat\s+([A-Z][\w&.' ]{1,40})\s*(?:$|[|\-–—])", r"^([A-Z][\w&.' ]{1,30})\s*[|\-–—]\s"):
        m = re.search(rx, t)
        if m:
            name = m.group(1).strip(" .|-")
            if 1 < len(name) <= 40 and not re.search(r"(?i)jobs?|careers?|apply|hiring|engineer|developer", name):
                return name
    return ""


def _boards_from_urls(urls: Iterable[str], titles: Optional[Dict[str, str]] = None) -> Set[Tuple[str, str]]:
    out = set()
    for u in urls:
        for provider, rx in ATS_RE:
            m = rx.search(u or "")
            if m and m.group(1).lower() not in NOT_SLUGS:
                out.add((provider, m.group(1)))
                name = _name_from_title((titles or {}).get(u, ""))
                if name and m.group(1).lower() not in NAMES:
                    NAMES[m.group(1).lower()] = name
    return out


def remembered(role: str) -> Set[Tuple[str, str]]:
    try:
        data = json.loads(BOARDS_FILE.read_text())
    except (OSError, ValueError):
        return set()
    key = role.lower()
    return {(b["provider"], b["slug"]) for b in data.get(key, [])}


def remember(role: str, boards: Iterable[Tuple[str, str]]) -> None:
    try:
        data = json.loads(BOARDS_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    key = role.lower()
    have = {(b["provider"], b["slug"]) for b in data.get(key, [])}
    data[key] = [{"provider": p, "slug": s} for p, s in sorted(have | set(boards))][:120]
    BOARDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    BOARDS_FILE.write_text(json.dumps(data, indent=1))


async def discover(spec: Dict[str, Any], search) -> Tuple[Set[Tuple[str, str]], List[Dict[str, Any]]]:
    """Company boards to check, plus other listings found on the way (aggregators, careers pages)."""
    names = [spec["role"], *spec["aliases"][:2]]
    where = f" {spec['location']}" if spec.get("location") else (" remote" if spec.get("remote") else "")
    queries = []
    for n in names[:2]:
        queries += [f'"{n}" site:jobs.ashbyhq.com{where}', f'"{n}" site:job-boards.greenhouse.io OR site:boards.greenhouse.io{where}',
                    f'"{n}" site:jobs.lever.co{where}']
    queries.append(f"{names[0]} jobs hiring now{where}")
    results = await asyncio.gather(*[search(q) for q in queries], return_exceptions=True)
    urls, others, titles = [], [], {}
    for r in results:
        if isinstance(r, dict):
            for it in r.get("results") or []:
                u = it.get("url") or it.get("href") or ""
                urls.append(u)
                titles[u] = it.get("title", "")
                if u and not any(rx.search(u) for _, rx in ATS_RE):
                    others.append({"title": it.get("title", ""), "url": u, "snippet": str(it.get("content") or it.get("snippet") or "")[:240]})
    return _boards_from_urls(urls, titles) | remembered(spec["role"]), others


# ------------------------------------------------------------------ 3. live boards

def _iso_days(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
    except (ValueError, OSError, OverflowError):
        return None


def _clean(html: str, n: int = 700) -> str:
    t = re.sub(r"<[^>]+>", " ", html or "")
    t = re.sub(r"&nbsp;|&#160;", " ", t)
    t = re.sub(r"&amp;", "&", t)
    return re.sub(r"\s+", " ", t).strip()[:n]


async def fetch_board(client: httpx.AsyncClient, provider: str, slug: str) -> List[Dict[str, Any]]:
    try:
        if provider == "ashby":
            r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
            if r.status_code != 200:
                return []
            data = r.json()
            org = slug
            out = []
            for j in data.get("jobs", []):
                if j.get("isListed") is False:
                    continue
                comp = (j.get("compensation") or {}).get("compensationTierSummary") or (j.get("compensation") or {}).get("scrapeableCompensationSalarySummary") or ""
                wt = str(j.get("workplaceType") or "").lower()       # Remote / Hybrid / OnSite: isRemote alone is unreliable
                out.append({"provider": "Ashby", "company_slug": org, "title": j.get("title", ""), "location": j.get("location") or "",
                            "remote": wt == "remote" if wt else bool(j.get("isRemote")),
                            "workplace": "Remote" if wt == "remote" else "Hybrid" if wt == "hybrid" else "On-site" if wt in ("onsite", "on-site") else "",
                            "department": j.get("department") or j.get("team") or "", "url": j.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{j.get('id')}",
                            "apply_url": j.get("applyUrl") or j.get("jobUrl") or "", "age_days": _iso_days(j.get("publishedAt")), "pay": comp,
                            "summary": _clean(j.get("descriptionPlain") or j.get("descriptionHtml") or ""), "employment": j.get("employmentType") or ""})
            return out
        if provider == "greenhouse":
            r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
            if r.status_code != 200:
                return []
            out = []
            company = None
            for j in r.json().get("jobs", []):
                company = company or j.get("company_name")
                loc = (j.get("location") or {}).get("name", "") if isinstance(j.get("location"), dict) else str(j.get("location") or "")
                import html as _h
                out.append({"provider": "Greenhouse", "company_slug": slug, "company": j.get("company_name") or "", "title": j.get("title", ""),
                            "location": loc, "remote": bool(REMOTE_RE.search(loc)),
                            "department": ((j.get("departments") or [{}])[0] or {}).get("name", ""), "url": j.get("absolute_url", ""),
                            "apply_url": j.get("absolute_url", ""), "age_days": _iso_days(j.get("first_published") or j.get("updated_at")),
                            "pay": "", "summary": _clean(_h.unescape(j.get("content") or ""))})
            return out
        if provider == "lever":
            r = await client.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
            if r.status_code != 200:
                return []
            out = []
            for j in r.json() if isinstance(r.json(), list) else []:
                cats = j.get("categories") or {}
                sr = j.get("salaryRange") or {}
                pay = f"{sr.get('currency', '')} {sr.get('min', ''):,}–{sr.get('max', ''):,}".strip() if sr.get("min") else ""
                wt = str(j.get("workplaceType") or "").lower()
                out.append({"provider": "Lever", "company_slug": slug, "title": j.get("text", ""), "location": cats.get("location") or "",
                            "remote": wt == "remote" or (not wt and bool(REMOTE_RE.search(cats.get("location") or ""))),
                            "workplace": "Remote" if wt == "remote" else "Hybrid" if wt == "hybrid" else "On-site" if wt == "onsite" else "",
                            "department": cats.get("team") or cats.get("department") or "", "url": j.get("hostedUrl", ""),
                            "apply_url": j.get("applyUrl") or j.get("hostedUrl", ""), "age_days": _iso_days(j.get("createdAt")), "pay": pay,
                            "summary": _clean(j.get("descriptionPlain") or j.get("description") or ""), "employment": cats.get("commitment") or ""})
            return out
    except Exception as e:
        logger.debug(f"board {provider}/{slug}: {e}")
    return []


KNOWN = {"openai": "OpenAI", "happyrobot.ai": "HappyRobot", "gitlab": "GitLab", "beaconsoftware": "Beacon Software", "reflectionai": "Reflection AI",
         "normalcomputing": "Normal Computing", "tenexlabs": "TenEx Labs", "shieldai": "Shield AI", "thinkahead": "AHEAD", "wmg": "Warner Music Group"}


def _company_name(job: Dict[str, Any]) -> str:
    if job.get("company"):
        return job["company"]
    slug = (job.get("company_slug") or "").lower()
    if slug in KNOWN:
        return KNOWN[slug]
    if slug in NAMES:
        return NAMES[slug]
    s = re.sub(r"[-_.]+", " ", job.get("company_slug", "")).strip()
    s = re.sub(r"\b(ai|io|hq|inc|labs?)\b$", lambda m: m.group(0).upper() if m.group(0) in ("ai", "io", "hq") else m.group(0).title(), s)
    return " ".join(w if w.isupper() else w.capitalize() for w in s.split()) or job.get("company_slug", "")


# ------------------------------------------------------------------ 4. ranking

def rank(jobs: List[Dict[str, Any]], spec: Dict[str, Any], resume_words: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
    loc = (spec.get("location") or "").lower()
    sen = spec.get("seniority") or ""
    for j in jobs:
        s = j["match"] * 60
        age = j.get("age_days")
        s += 15 if age is not None and age <= 7 else 10 if age is not None and age <= 30 else 4 if age is None else 0
        text = f"{j['location']} {'remote' if j['remote'] else ''}".lower()
        if spec.get("remote"):
            s += 15 if j["remote"] or "remote" in text else -10
        if loc:
            s += 12 if loc.split(",")[0] in text else (4 if j["remote"] else -6)
        t = j["title"].lower()
        if sen:
            s += 8 if sen in t else (-8 if any(x in t for x in ("senior", "staff", "principal", "lead", "director")) and sen in ("junior", "entry", "new grad", "intern") else 0)
        if resume_words:
            words = set(re.findall(r"[a-z][a-z0-9+#.]{2,}", (j.get("summary") or "").lower()))
            overlap = len(words & resume_words)
            j["fit"] = min(100, overlap * 4)
            s += min(15, overlap)
        j["score"] = round(s, 1)
    return sorted(jobs, key=lambda j: -j["score"])


# ------------------------------------------------------------------ all together

async def hunt(query: str, search, on_progress=None, max_boards: int = 40) -> Dict[str, Any]:
    spec = understand(query)
    if not spec["role"]:
        return {"spec": spec, "jobs": [], "others": [], "boards_checked": 0, "error": "no_role"}
    if on_progress:
        await on_progress(f"Looking for companies hiring {spec['role']}s…")
    boards, others = await discover(spec, search)
    boards = sorted(boards)[:max_boards]
    if on_progress:
        await on_progress(f"Checking {len(boards)} live job boards…")
    score = title_matcher(spec["role"], spec["aliases"])
    async with httpx.AsyncClient(timeout=12, headers=UA, follow_redirects=True) as client:
        sem = asyncio.Semaphore(10)

        async def one(b):
            async with sem:
                return b, await fetch_board(client, *b)
        pairs = await asyncio.gather(*[one(b) for b in boards])
    jobs, hit_boards = [], set()
    for b, posts in pairs:
        for p in posts:
            m = score(p["title"])
            if m >= 0.6:
                p["match"] = m
                p["company"] = _company_name(p)
                jobs.append(p)
                hit_boards.add(b)
    remember(spec["role"], hit_boards)
    seen, unique = set(), []
    for j in jobs:
        k = (j["company"].lower(), j["title"].lower(), j["location"].lower())
        if k not in seen:
            seen.add(k)
            unique.append(j)
    resume_words = _resume_words()
    ranked = rank(unique, spec, resume_words)
    for i, j in enumerate(ranked):
        j["id"] = f"j{i + 1}"
    result = {"spec": spec, "jobs": ranked, "others": others[:8], "boards_checked": len(boards),
              "companies": len({j['company'] for j in ranked}), "checked_at": time.time(), "used_resume": bool(resume_words)}
    LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_FILE.write_text(json.dumps(result))
    return result


def save_view(result: Dict[str, Any]) -> None:
    """The list as you last saw it (after "only remote" etc.), so "apply to 2" means the 2 on screen."""
    LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_FILE.write_text(json.dumps(result))


def last() -> Optional[Dict[str, Any]]:
    try:
        return json.loads(LAST_FILE.read_text())
    except (OSError, ValueError):
        return None


def _resume_words() -> Set[str]:
    """Skills words from the résumé on file (read locally), for a rough fit score."""
    try:
        from app.services.browser_sandbox import load_user_profile
        import os
        path = os.path.expanduser(str(load_user_profile().get("resume_path") or ""))
        if not path or not os.path.isfile(path):
            return set()
        from app.services.document_parser import DocumentParserService
        parsed = DocumentParserService().parse_file(path, max_chars=20000)
        text = (parsed.get("content") or parsed.get("text") or "") if isinstance(parsed, dict) else ""
        words = set(re.findall(r"[a-z][a-z0-9+#.]{2,}", text.lower()))
        common = {"the", "and", "with", "for", "from", "that", "this", "have", "has", "was", "were", "are", "you", "your", "our", "work",
                  "experience", "team", "teams", "years", "using", "used", "build", "built"}
        return words - common
    except Exception:
        return set()
