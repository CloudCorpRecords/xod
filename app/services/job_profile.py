"""
job_profile: structured Job-Application Profile + learned Q&A memory.

Real ATS portals (Greenhouse, Lever, Workday, Ashby, iCIMS) ask the same ~30
structured fields and screening questions over and over. This module stores a
clean structured profile in DATA_DIR/job_profile.json plus a normalized
question -> answer cache (learned_qa) so Xod fills applications in one shot
and remembers any new screening question you answer once.
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_SCHEMA: Dict[str, Any] = {
    "first_name": "",
    "last_name": "",
    "full_name": "",
    "email": "",
    "phone": "",
    "location": "",
    "city": "",
    "state": "",
    "country": "",
    "zip_code": "",
    "linkedin": "",
    "github": "",
    "portfolio": "",
    "website": "",
    "resume_path": "",
    "current_title": "",
    "current_company": "",
    "years_experience": "",
    "work_authorization": "",
    "requires_sponsorship": "",
    "salary_expectation": "",
    "start_date": "",
    "pronouns": "",
    "learned_qa": {},
}

_FIELD_SYNONYMS = [
    ("first_name", ("first name", "given name", "fname")),
    ("last_name", ("last name", "family name", "surname", "lname")),
    ("full_name", ("full name", "your name", "legal name", "name")),
    ("email", ("email", "e-mail")),
    ("phone", ("phone", "mobile", "telephone", "cell")),
    ("linkedin", ("linkedin",)),
    ("github", ("github", "git hub")),
    ("portfolio", ("portfolio", "personal site", "personal website")),
    ("website", ("website", "url")),
    ("location", ("location", "address", "where are you based")),
    ("city", ("city",)),
    ("state", ("state", "province", "region")),
    ("country", ("country",)),
    ("zip_code", ("zip", "postal")),
    ("resume_path", ("resume", "cv")),
    ("current_company", ("current company", "current employer", "company")),
    ("current_title", ("current title", "job title", "headline")),
    ("years_experience", ("years of experience", "years experience", "how many years")),
    ("work_authorization", ("authorized to work", "work authorization", "legally authorized", "eligible to work")),
    ("requires_sponsorship", ("sponsorship", "visa sponsor", "require sponsor")),
    ("salary_expectation", ("salary", "compensation", "desired pay", "expected pay")),
    ("start_date", ("start date", "when can you start", "earliest start", "availability")),
]


def _path() -> Path:
    from app.paths import DATA_DIR
    return DATA_DIR / "job_profile.json"


def _norm_q(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def load_job_profile() -> Dict[str, Any]:
    base = dict(DEFAULT_SCHEMA)
    base["learned_qa"] = {}
    p = _path()
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k == "learned_qa" and isinstance(v, dict):
                        base["learned_qa"] = {str(qk): str(qv) for qk, qv in v.items()}
                    else:
                        base[k] = v
        except Exception:
            pass
    if not base["full_name"] and (base["first_name"] or base["last_name"]):
        base["full_name"] = f"{base['first_name']} {base['last_name']}".strip()
    return base


def save_job_profile(updates: Dict[str, Any]) -> Dict[str, Any]:
    prof = load_job_profile()
    for k, v in (updates or {}).items():
        if k == "learned_qa" and isinstance(v, dict):
            prof["learned_qa"].update({str(qk): str(qv) for qk, qv in v.items()})
        elif k in prof or isinstance(v, (str, int, float, bool)):
            prof[k] = v
    if not prof.get("full_name") and (prof.get("first_name") or prof.get("last_name")):
        prof["full_name"] = f"{prof.get('first_name','')} {prof.get('last_name','')}".strip()
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prof, indent=2), encoding="utf-8")
    return prof


def learn_qa(question: str, answer: str) -> Dict[str, Any]:
    """Remember an answer to a screening question for future applications."""
    prof = load_job_profile()
    key = _norm_q(question)
    if key:
        prof["learned_qa"][key] = str(answer)
        save_job_profile({"learned_qa": prof["learned_qa"]})
    return prof


def match_field_value(label_or_question: str, profile: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Resolve an ATS field label or screening question to a stored profile value or learned Q&A."""
    prof = profile if profile is not None else load_job_profile()
    norm = _norm_q(label_or_question)
    if not norm:
        return None

    # 1. Exact or high-overlap learned Q&A match first (handles custom screening questions)
    qa = prof.get("learned_qa") or {}
    if norm in qa and str(qa[norm]).strip():
        return str(qa[norm]).strip()
    q_words = set(norm.split())
    best_qa, best_score = None, 0.0
    for qk, qv in qa.items():
        if not str(qv).strip():
            continue
        k_words = set(_norm_q(qk).split())
        if not k_words:
            continue
        overlap = len(q_words & k_words) / max(len(k_words), 1)
        if overlap >= 0.75 and overlap > best_score:
            best_qa, best_score = str(qv).strip(), overlap
    if best_qa:
        return best_qa

    # 2. Structured profile fields by synonym table
    for field_key, needles in _FIELD_SYNONYMS:
        if any(n in norm for n in needles):
            val = str(prof.get(field_key) or "").strip()
            if val:
                return val
    return None
