"""Locate files and folders on this computer from loose, human clues.

The old find path only worked when the person already knew an exact name
fragment. Real requests sound like "find the folder that has my Japan trip
stuff", "my tax return summary for 2025", or "the W2 from my old job": a mix
of name fragments, a kind, a rough date, words inside the file, and a purpose
("wage statement" means a W2). This service turns those clues into ranked
candidates, each with the evidence for why it matched, and it never invents
a path: every candidate is existence-checked and returned in full.

Routes, in order:
  1. The Spotlight index (mdfind), behind a seam so tests can inject a fake.
  2. A bounded filename walk of the home tree (noise folders skipped).
  3. A content search over plausible text files for content-word clues.

Scoring is additive and explainable. Name tokens and an explicit year are
the strongest signals; kind and purpose expansions are next; season windows
and other rough dates only nudge the ranking, they never hard-filter. When
the top two candidates finish within ASK_MARGIN points of each other the
result is "ask" (both are returned with a question) instead of a guess.
When nothing clears the bar the result is "not_found" with a tried list:
which routes ran, with which terms, and how much each one saw.
"""
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

MAX_CANDIDATES = 8
ASK_MARGIN = 12.0          # top two within this many points: ask, do not guess
FOUND_THRESHOLD = 30.0      # a candidate needs at least this to be named at all
MAX_WALK_ENTRIES = 30000    # hard bounds so a hunt can never wedge
MAX_WALK_DEPTH = 9
MAX_CONTENT_FILES = 6000
MAX_CONTENT_BYTES = 512 * 1024

NOISE_DIRS = {
    "Library", "Caches", "node_modules", ".git", ".venv", "venv", "__pycache__",
    ".Trash", "Applications", ".cache", ".npm", ".cargo", ".rustup", ".local",
    "DerivedData", ".gradle", ".m2", "site-packages", ".tox", ".mypy_cache",
}

TEXT_EXTS = {
    ".txt", ".md", ".rst", ".csv", ".tsv", ".json", ".log", ".html", ".htm",
    ".xml", ".yaml", ".yml", ".ini", ".cfg", ".py", ".js", ".ts", ".sh",
    ".rtf", ".tex", ".org",
}

KIND_EXTS = {
    "pdf": {".pdf"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".heic", ".webp", ".tif", ".tiff", ".bmp", ".svg"},
    "photo": {".png", ".jpg", ".jpeg", ".gif", ".heic", ".webp", ".tif", ".tiff", ".bmp", ".svg"},
    "spreadsheet": {".xlsx", ".xls", ".csv", ".tsv", ".numbers", ".ods"},
    "document": {".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".pages"},
    "presentation": {".ppt", ".pptx", ".key", ".odp"},
    "video": {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"},
    "audio": {".mp3", ".m4a", ".wav", ".aiff", ".flac", ".ogg"},
    "archive": {".zip", ".tar", ".gz", ".dmg", ".pkg"},
    "code": {".py", ".js", ".ts", ".swift", ".go", ".rs", ".java", ".c", ".h", ".sh"},
}
EXT_KIND = {}
for _kind, _exts in KIND_EXTS.items():
    for _ext in _exts:
        EXT_KIND.setdefault(_ext, _kind)

KIND_WORDS = {
    "pdf": "pdf", "spreadsheet": "spreadsheet", "sheet": "spreadsheet",
    "excel": "spreadsheet", "photo": "image", "picture": "image",
    "image": "image", "screenshot": "image", "folder": "folder",
    "directory": "folder", "presentation": "presentation", "slides": "presentation",
    "video": "video", "movie": "video", "song": "audio", "audio": "audio",
    "document": "document", "doc": "document", "archive": "archive", "zip": "archive",
}

# Purpose: the word the person uses, and the words that actually appear in
# file names, paths, and contents. Triggers are matched against the request.
PURPOSE_MAP = {
    "w2": ["w2", "w-2", "wage", "wages", "salary", "earnings", "paystub", "pay stub"],
    "wage statement": ["w2", "w-2", "wage", "wages", "salary", "earnings"],
    "paystub": ["w2", "wage", "paystub", "salary", "earnings"],
    "pay stub": ["w2", "wage", "paystub", "salary", "earnings"],
    "tax": ["tax", "taxes", "1040", "return", "filing", "irs"],
    "lease": ["lease", "rental", "landlord", "tenant", "rent"],
    "contract": ["contract", "agreement", "signed", "consulting"],
    "agreement": ["agreement", "contract", "signed"],
    "trip": ["trip", "itinerary", "travel", "flight", "hotel", "vacation", "booking"],
    "itinerary": ["itinerary", "trip", "travel", "flight", "hotel", "booking"],
    "vacation": ["vacation", "trip", "travel", "itinerary", "hotel"],
    "invoice": ["invoice", "bill", "receipt", "payment"],
    "receipt": ["receipt", "invoice", "payment"],
    "recipe": ["recipe", "ingredients", "cooking", "bake", "dish"],
    "budget": ["budget", "expenses", "spending", "finance"],
    "reunion": ["reunion", "family", "gathering"],
    "resume": ["resume", "cv", "curriculum"],
    "insurance": ["insurance", "policy", "coverage", "claim"],
    "passport": ["passport", "visa", "travel"],
    "mortgage": ["mortgage", "loan", "house", "home"],
}

STOPWORDS = {
    "find", "the", "my", "a", "an", "that", "has", "have", "with", "stuff",
    "thing", "things", "file", "files", "for", "from", "of", "in", "on",
    "please", "me", "where", "is", "it", "i", "need", "want", "looking",
    "look", "search", "show", "get", "can", "you", "about", "containing",
    "contains", "inside", "there", "was", "were", "this", "last", "not",
}

SEASONS = {"spring": (3, 5), "summer": (6, 8), "fall": (9, 11), "autumn": (9, 11), "winter": (12, 2)}
MONTHS = {name: i + 1 for i, name in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}


def season_window(text: str, now: Optional[datetime] = None) -> Optional[Tuple[float, float]]:
    """The most recent window for a season phrase ('last spring', 'spring'),
    as (start, end) timestamps. Rough dates rank; they never filter."""
    now = now or datetime.now()
    low = (text or "").lower()
    for season, (m1, m2) in SEASONS.items():
        if season not in low:
            continue
        years = [now.year, now.year - 1]
        for year in years:
            start = datetime(year, m1, 1)
            end_month = m2 if m2 >= m1 else m2 + 12
            end_year = year if m2 >= m1 else year + 1
            end = datetime(end_year, end_month % 12 + 1, 1) if end_month % 12 else datetime(end_year + 1, 1, 1)
            if m2 < m1:  # winter wraps the year end
                end = datetime(year + 1, m2 % 12 + 1, 1)
            if start <= now or "last" not in low:
                return (start.timestamp(), end.timestamp())
        return None
    for name, month in MONTHS.items():
        if name in low:
            year = now.year if month <= now.month else now.year - 1
            start = datetime(year, month, 1)
            end = datetime(year + (month // 12), month % 12 + 1, 1)
            return (start.timestamp(), end.timestamp())
    return None


def tokens_of(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 1]


@dataclass
class Clues:
    name_tokens: List[str] = field(default_factory=list)
    kind: str = ""
    year: Optional[int] = None
    excluded_years: List[int] = field(default_factory=list)
    content_words: List[str] = field(default_factory=list)
    purpose_terms: List[str] = field(default_factory=list)
    want_folder: bool = False
    folder_by_contents: bool = False
    date_window: Optional[Tuple[float, float]] = None
    date_label: str = ""


def parse_clues(request: str, extra: Optional[Dict[str, Any]] = None,
                now: Optional[datetime] = None) -> Clues:
    """Turn a natural find request (plus any structured clues the caller
    already parsed) into a Clues object. Structured values win."""
    extra = extra or {}
    text = (request or "").strip()
    low = text.lower()
    clues = Clues()

    for match in re.finditer(r"\b(?:not|except|but not)\s+(?:the\s+)?(\d{4})\b", low):
        clues.excluded_years.append(int(match.group(1)))
    years = [int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", low)]
    years = [y for y in years if y not in clues.excluded_years]
    if years:
        clues.year = years[0]

    window = season_window(low, now)
    if window:
        clues.date_window = window
        clues.date_label = next((w for w in list(SEASONS) + list(MONTHS) if w in low), "that time")

    for word, kind in KIND_WORDS.items():
        if re.search(r"\b" + re.escape(word) + r"\b", low):
            clues.kind = "folder" if kind == "folder" else kind
            if kind == "folder":
                clues.want_folder = True
            break
    if re.search(r"\b(folder|directory)\b[^.]*\b(has|with|containing|contains|full of)\b", low):
        clues.folder_by_contents = True
        clues.want_folder = True

    for trigger, terms in PURPOSE_MAP.items():
        if trigger in low:
            for term in terms:
                if term not in clues.purpose_terms:
                    clues.purpose_terms.append(term)

    skip = set(STOPWORDS) | set(KIND_WORDS) | set(SEASONS) | set(MONTHS)
    skip |= {str(y) for y in ([clues.year] if clues.year else []) + clues.excluded_years}
    skip |= set(clues.purpose_terms)
    for token in tokens_of(low):
        if token not in skip and token not in clues.name_tokens:
            clues.name_tokens.append(token)

    if extra.get("name"):
        for token in tokens_of(str(extra["name"])):
            if token not in clues.name_tokens:
                clues.name_tokens.insert(0, token)
    if extra.get("kind"):
        clues.kind = str(extra["kind"]).lower()
        if clues.kind == "folder":
            clues.want_folder = True
    if extra.get("year"):
        try:
            clues.year = int(extra["year"])
        except (TypeError, ValueError):
            pass
    if extra.get("content_words"):
        for word in extra["content_words"]:
            for token in tokens_of(str(word)):
                if token not in clues.content_words:
                    clues.content_words.append(token)
    if extra.get("folder_by_contents"):
        clues.folder_by_contents = True
        clues.want_folder = True
    return clues


# ---------------------------------------------------------------- backends

class SpotlightBackend:
    """The macOS Spotlight index via mdfind. The seam: locate() accepts any
    object with search(clues, roots) -> list of full path strings."""

    def search(self, clues: Clues, roots: Sequence[str]) -> List[str]:
        terms = clues.name_tokens + clues.purpose_terms + clues.content_words
        if clues.year:
            terms = terms + [str(clues.year)]
        if not terms:
            return []
        query = " ".join(terms[:6])
        found: List[str] = []
        for root in roots:
            try:
                out = subprocess.run(["/usr/bin/mdfind", "-onlyin", root, query],
                                     capture_output=True, text=True, timeout=20)
                for line in (out.stdout or "").splitlines():
                    line = line.strip()
                    if line and line not in found:
                        found.append(line)
            except Exception:
                continue
        return found[:500]


def _walk(roots: Sequence[str], want_dirs: bool = False):
    """Bounded walk yielding (path, is_dir). Noise folders are never entered,
    but a folder is yielded before its own noise children are skipped, so a
    repo checkout full of .git and node_modules is still findable itself."""
    seen = 0
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath[len(root):].count(os.sep)
            dirnames[:] = [d for d in dirnames if d not in NOISE_DIRS and not d.startswith(".")]
            if depth >= MAX_WALK_DEPTH:
                dirnames[:] = []
            if want_dirs:
                for d in list(dirnames):
                    seen += 1
                    yield (os.path.join(dirpath, d), True)
            for name in filenames:
                seen += 1
                if seen > MAX_WALK_ENTRIES:
                    return
                yield (os.path.join(dirpath, name), False)
            if seen > MAX_WALK_ENTRIES:
                return


def filename_search(clues: Clues, roots: Sequence[str]) -> List[str]:
    """Fallback 1: paths whose name or parent folders carry any clue term."""
    terms = set(clues.name_tokens) | set(clues.purpose_terms) | set(clues.content_words)
    if clues.year:
        terms.add(str(clues.year))
    found: List[str] = []
    for path, is_dir in _walk(roots, want_dirs=True):
        if is_dir and not clues.want_folder:
            continue
        low = path.lower()
        name = os.path.basename(low)
        if not terms or any(t in name or t in low for t in terms):
            found.append(path)
        if len(found) >= 2000:
            break
    return found


def content_search(clues: Clues, roots: Sequence[str]) -> Dict[str, Set[str]]:
    """Fallback 2: which content words appear inside which text files.
    Returns path -> set of matched words. Bounded in files and bytes."""
    words = set(clues.content_words) | set(clues.name_tokens) | set(clues.purpose_terms)
    words = {w for w in words if len(w) > 2}
    hits: Dict[str, Set[str]] = {}
    if not words:
        return hits
    scanned = 0
    for path, is_dir in _walk(roots):
        if is_dir or scanned >= MAX_CONTENT_FILES:
            continue
        if os.path.splitext(path)[1].lower() not in TEXT_EXTS:
            continue
        try:
            if os.path.getsize(path) > MAX_CONTENT_BYTES:
                continue
            scanned += 1
            with open(path, "r", errors="ignore") as f:
                body = f.read().lower()
        except OSError:
            continue
        body_tokens = set(tokens_of(body))
        matched = {w for w in words if w in body_tokens or ("-" in w and w in body)}
        if matched:
            hits[path] = matched
    return hits


# ---------------------------------------------------------------- scoring

def _kind_of(path: str, is_dir: bool) -> str:
    if is_dir:
        return "folder"
    return EXT_KIND.get(os.path.splitext(path)[1].lower(), "file")


def score_candidate(path: str, clues: Clues, content_hits: Set[str]) -> Tuple[float, List[str]]:
    is_dir = os.path.isdir(path)
    kind = _kind_of(path, is_dir)
    name = os.path.basename(path)
    stem = os.path.splitext(name)[0].lower()
    stem_tokens = set(tokens_of(stem))
    path_tokens = set(tokens_of(path.lower()))
    score = 0.0
    why: List[str] = []

    name_hits = [t for t in clues.name_tokens if t in stem_tokens or t in stem]
    for t in name_hits:
        score += 30
        why.append(f"name matches '{t}'")
    path_only = [t for t in clues.name_tokens if t not in name_hits and t in path_tokens]
    for t in path_only:
        score += 12
        why.append(f"folder path matches '{t}'")
    if clues.name_tokens and len(name_hits) == len(clues.name_tokens):
        score += 20
        why.append("every name clue is in the name")

    purpose_hits = [t for t in clues.purpose_terms if t in stem or t in stem_tokens]
    for t in purpose_hits[:3]:
        score += 25
        why.append(f"name fits the purpose ('{t}')")
    purpose_path = [t for t in clues.purpose_terms if t not in purpose_hits and t in path_tokens]
    for t in purpose_path[:2]:
        score += 10
        why.append(f"folder path fits the purpose ('{t}')")

    if clues.kind:
        if kind == clues.kind or (clues.kind == "photo" and kind == "image"):
            score += 25
            why.append(f"it is a {kind}")
        elif not is_dir:
            score -= 30
            why.append(f"but it is a {kind}, not a {clues.kind}")

    if clues.year:
        year_s = str(clues.year)
        if year_s in stem or year_s in path.lower():
            score += 40
            why.append(f"year {clues.year} in the name or path")
        try:
            if datetime.fromtimestamp(os.path.getmtime(path)).year == clues.year:
                score += 25
                why.append(f"modified in {clues.year}")
            else:
                score -= 10
        except OSError:
            pass
    for bad in clues.excluded_years:
        if str(bad) in stem or str(bad) in path.lower():
            score -= 80
            why.append(f"excluded year {bad}")
    if clues.date_window:
        try:
            mtime = os.path.getmtime(path)
            if clues.date_window[0] <= mtime <= clues.date_window[1]:
                score += 15
                why.append(f"modified in {clues.date_label or 'the time you mentioned'}")
            else:
                score -= 5
        except OSError:
            pass

    # Content hits help, but with diminishing returns: a file that merely
    # talks about a thing must not outrank the file that is named for it.
    # Words the person actually used (name/content clues) count fully;
    # purpose-expansion words found only in contents count a little.
    strong_words = set(clues.name_tokens) | set(clues.content_words)
    strong = sorted(w for w in content_hits if w in strong_words)
    weak = sorted(w for w in content_hits if w not in strong_words)
    for i, word in enumerate(strong):
        score += 20 if i == 0 else (10 if i == 1 else 5)
        why.append(f"contents mention '{word}'")
    for word in weak[:3]:
        score += 5
        why.append(f"contents mention '{word}'")
    return score, why


def _candidate_dict(path: str, score: float, why: List[str]) -> Optional[Dict[str, Any]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    is_dir = os.path.isdir(path)
    return {
        "path": path,
        "name": os.path.basename(path),
        "kind": _kind_of(path, is_dir),
        "is_folder": is_dir,
        "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
        "modified_ts": st.st_mtime,
        "size": st.st_size,
        "score": round(score, 1),
        "why": why,
    }


def default_roots() -> List[str]:
    home = str(Path.home())
    roots = [os.path.join(home, d) for d in ("Documents", "Desktop", "Downloads", "Pictures", "Movies", "Music")]
    roots = [r for r in roots if os.path.isdir(r)]
    return roots or [home]


def locate(request: str, extra: Optional[Dict[str, Any]] = None,
           roots: Optional[Sequence[str]] = None,
           index_backend: Any = None, use_index: bool = True,
           now: Optional[datetime] = None) -> Dict[str, Any]:
    """Run a full hunt. Returns a dict with status found | ask | not_found,
    ranked candidates (each existence-verified, full path, with evidence),
    and the tried list. Never raises on a missing route; a route that fails
    is recorded in the tried list and the fallbacks still run."""
    clues = parse_clues(request, extra, now=now)
    roots = list(roots) if roots else default_roots()
    tried: List[Dict[str, Any]] = []
    pool: Dict[str, Set[str]] = {}

    def add(paths: Sequence[str], route: str):
        for p in paths:
            if p:
                pool.setdefault(str(p), set())

    if use_index:
        backend = index_backend if index_backend is not None else SpotlightBackend()
        terms = clues.name_tokens + clues.purpose_terms + clues.content_words
        try:
            hits = backend.search(clues, roots)
            tried.append({"route": "spotlight", "terms": terms, "hits": len(hits)})
            add(hits, "spotlight")
        except Exception as e:
            tried.append({"route": "spotlight", "terms": terms, "hits": 0, "error": str(e)[:120]})

    fn_hits = filename_search(clues, roots)
    tried.append({"route": "filename", "terms": clues.name_tokens + clues.purpose_terms, "hits": len(fn_hits)})
    add(fn_hits, "filename")

    content_hits: Dict[str, Set[str]] = {}
    if clues.content_words or clues.name_tokens or clues.purpose_terms:
        content_hits = content_search(clues, roots)
        tried.append({"route": "content", "terms": sorted(set(clues.content_words) | set(clues.name_tokens)),
                      "hits": len(content_hits)})
        for p, words in content_hits.items():
            pool.setdefault(p, set()).update(words)

    scored: List[Dict[str, Any]] = []
    folder_children: Dict[str, List[Tuple[float, str]]] = {}
    for path, hits_words in pool.items():
        if not os.path.exists(path):
            continue
        is_dir = os.path.isdir(path)
        if is_dir and not clues.want_folder:
            continue
        if not is_dir and clues.want_folder and not clues.folder_by_contents:
            continue
        score, why = score_candidate(path, clues, hits_words if not is_dir else set())
        cand = _candidate_dict(path, score, why)
        if cand:
            scored.append(cand)
        if clues.folder_by_contents and not is_dir and score >= 20:
            parent = os.path.dirname(path)
            folder_children.setdefault(parent, []).append((score, os.path.basename(path)))

    if clues.folder_by_contents:
        for parent, children in folder_children.items():
            if not os.path.isdir(parent):
                continue
            best = max(s for s, _ in children)
            bonus = min(30.0, 8.0 * len(children))
            score = best * 0.9 + bonus
            why = [f"holds {len(children)} matching file(s), best: {max(children)[1]}",
                   "promoted from the files inside it"]
            name_hits = [t for t in clues.name_tokens if t in os.path.basename(parent).lower()]
            for t in name_hits:
                score += 30
                why.append(f"folder name matches '{t}'")
            existing = next((c for c in scored if c["path"] == parent), None)
            cand = _candidate_dict(parent, score, why)
            if cand:
                if existing:
                    existing.update(cand)
                else:
                    scored.append(cand)

    scored = [c for c in scored if c["score"] >= FOUND_THRESHOLD]
    scored.sort(key=lambda c: (-c["score"], c["name"].lower()))
    top = scored[:MAX_CANDIDATES]

    result: Dict[str, Any] = {
        "request": request,
        "clues": {
            "name_tokens": clues.name_tokens, "kind": clues.kind, "year": clues.year,
            "excluded_years": clues.excluded_years, "content_words": clues.content_words,
            "purpose_terms": clues.purpose_terms, "want_folder": clues.want_folder,
            "folder_by_contents": clues.folder_by_contents, "date_label": clues.date_label,
        },
        "tried": tried,
        "candidates": top,
    }
    if not top:
        result["status"] = "not_found"
        result["message"] = ("Nothing matched well enough to name. Tried: " +
                             "; ".join(f"{t['route']} ({t['hits']} hits)" for t in tried) + ".")
        return result
    if len(top) >= 2 and (top[0]["score"] - top[1]["score"]) <= ASK_MARGIN:
        result["status"] = "ask"
        result["question"] = (f"Two close matches: {top[0]['name']} and {top[1]['name']}. "
                              "Which one did you mean?")
        return result
    result["status"] = "found"
    return result
