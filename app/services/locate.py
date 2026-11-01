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

Route hits are prioritized inside pool construction, before any route
cap or scan budget cuts: each route orders its hits by the identity part
of scoring (exact name tokens, kind match, an explicit year, cache scope
penalty) and only then truncates or spends its budget, so under heavy
hit volume a cap keeps the strongest hits instead of whichever files
the walk happened to reach first.

Scoring is additive and explainable, and target identity dominates: an
exact name-token match on the target's own terms and a kind match outweigh
any generic content-word overlap, instruction-frame words from the request
itself earn nothing, and files inside package or module caches or build
intermediates take a substantial scope penalty unless the clues name the
cache. Name tokens and an explicit year are the strongest signals; kind and
purpose expansions are next; season windows
and other rough dates only nudge the ranking, they never hard-filter. When
the top two candidates finish within ASK_MARGIN points of each other the
result is "ask" (both are returned with a question) instead of a guess,
but only when both clear ASK_FLOOR: an ask between two irrelevant files
is worse than no answer, so below the floor the result is "not_found"
with a tried list: which routes ran, with which terms, and how much each
one saw. A candidate whose own evidence states it is not the kind the
person asked for is likewise never presented as a match: it is recorded
as checked-and-rejected and the hunt falls to "not_found" (the delivery
layer in decisive_evidence.py enforces the same block on any payload).
The kind block also reads the candidate's structured kind field, not
only the evidence prose, and purpose credit in a name requires a
whole-token match, so a purpose term cannot earn name credit as a
substring of a longer token ("tax" inside "taxonomy").
"""
import os
import re
import stat
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

MAX_CANDIDATES = 8
FILENAME_ROUTE_CAP = 2000   # prioritized filename hits kept per hunt
SPOTLIGHT_ROUTE_CAP = 500   # prioritized Spotlight hits kept per root set
ASK_MARGIN = 12.0          # top two within this many points: ask, do not guess
FOUND_THRESHOLD = 30.0      # a candidate needs at least this to be named at all
# The ask/found floor, calibrated on the score_candidate scale. One bare
# name-token hit scores exactly 30 (FOUND_THRESHOLD), and one such token
# plus a 5-point content-scatter word scores 35: those are the shapes
# that drove the re-run 3 asks (a lone 'path' token in a filename, tied
# with another unrelated file). 40 is the weakest score a genuine match
# in the regression corpus reaches: a kind match plus its season window
# (25 + 15), or four strong content words (20 + 10 + 5 + 5). Real
# contenders clear it with room (two name tokens 80, a name token plus a
# kind match 55, a name-plus-year match 95 or more). Below the floor the
# honest answers are a single candidate at or above it, or not_found.
ASK_FLOOR = 40.0
MAX_WALK_ENTRIES = 30000    # hard bounds so a hunt can never wedge
MAX_WALK_DEPTH = 9
MAX_CONTENT_FILES = 6000
MAX_CONTENT_BYTES = 512 * 1024
MAX_CONTENT_ENTRIES = 30000   # entries the content route will look at, total
# Wall-clock budgets: a hunt must finish on its own, never by watchdog.
# The content route gets tens of seconds; the whole hunt stays under about
# a minute. Both are parameters on locate()/content_search() so tests can
# shrink them. Prevention, not thread-killing: only regular files are ever
# opened and every read is capped, so no single file can block past a check.
CONTENT_ROUTE_DEADLINE_S = 30.0
HUNT_DEADLINE_S = 55.0

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

# Instruction frame: the requester's own phrasing about how to answer or
# where to look, never part of the target's identity. "Reply with its full
# path.", "somewhere on this computer", and "in its name" describe the
# reply, the search space, and the matching mode; a file must never score
# for containing those words. Frame phrases are stripped from the text
# before name tokens are taken, and FRAME_TOKENS are skipped outright as
# a backstop for phrasings the patterns do not cover, so no frame word
# can land in name_tokens or content_words. Only name and content clues
# are filtered this way: kind, year, season, and purpose detection still
# read the full request text, exactly as before.
FRAME_TOKENS = {
    "reply", "full", "path", "its", "somewhere", "name", "computer",
}

FRAME_PHRASES = [
    r"\b(?:reply|respond|answer)\s+with\s+(?:its|the)\s+(?:full\s+)?path\b",
    r"\b(?:give|show|tell|provide|send|return)\s+(?:me\s+)?(?:its|the)\s+(?:full\s+)?path\b",
    r"\b(?:its\s+|the\s+)?full\s+path\b",
    r"\bsomewhere\s+on\s+this\s+computer\b",
    r"\bon\s+this\s+computer\b",
    r"\bin\s+(?:its|the)\s+name\b",
]


def strip_instruction_frame(text: str) -> str:
    """Remove instruction-frame phrases from request text before clue
    tokens are taken from it. Target words inside a frame phrase stay:
    "with Sourdough in its name" keeps "Sourdough", because only the
    trailing "in its name" is removed."""
    cleaned = text or ""
    for pattern in FRAME_PHRASES:
        cleaned = re.sub(pattern, " ", cleaned)
    return cleaned

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
    already parsed) into a Clues object. The request text is authoritative:
    a structured value may only fill a field the text parse left empty, it
    never overrides what the person actually said."""
    extra = extra or {}
    text = (request or "").strip()
    low = text.lower()
    clues = Clues()

    for match in re.finditer(r"\b(?:not|except|but not)\s+(?:the\s+)?(\d{4})\b", low):
        clues.excluded_years.append(int(match.group(1)))
    # A negated word names what to avoid, not what to find ("not the
    # draft"), the same way a negated year lands in excluded_years above.
    # It must never become a positive name clue: with the frame tokens
    # gone, a lone negated word would otherwise collect the all-name-clues
    # bonus in scoring and outrank the actual target.
    excluded_words = set()
    for match in re.finditer(r"\b(?:not|except|but not)\s+(?:the\s+|a\s+|an\s+)?([a-z][a-z0-9]*)\b", low):
        excluded_words.add(match.group(1))
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
    skip |= FRAME_TOKENS
    skip |= excluded_words
    for token in tokens_of(strip_instruction_frame(low)):
        if token not in skip and token not in clues.name_tokens:
            clues.name_tokens.append(token)

    if extra.get("name") and not clues.name_tokens:
        for token in tokens_of(str(extra["name"])):
            if token not in FRAME_TOKENS and token not in clues.name_tokens:
                clues.name_tokens.append(token)
    if extra.get("kind") and not clues.kind:
        clues.kind = str(extra["kind"]).lower()
        if clues.kind == "folder":
            clues.want_folder = True
    if extra.get("year") and clues.year is None:
        try:
            hint_year = int(extra["year"])
        except (TypeError, ValueError):
            hint_year = None
        if hint_year is not None and hint_year not in clues.excluded_years:
            clues.year = hint_year
    # content_words has no text-parse counterpart: the hint fills an
    # otherwise always-empty field, so it can never fight the text.
    if extra.get("content_words"):
        for word in extra["content_words"]:
            for token in tokens_of(str(word)):
                if token not in FRAME_TOKENS and token not in clues.content_words:
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
        # The index returns hits in its own order, which is volume order,
        # not relevance order: prioritize before the cap cuts, so the cap
        # keeps the strongest hits instead of the first ones.
        found.sort(key=lambda p: _pool_priority(p, clues))
        return found[:SPOTLIGHT_ROUTE_CAP]


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
    """Fallback 1: paths whose name or parent folders carry any clue term.

    Every matching path in the bounded walk is collected first, then the
    hits are prioritized with _pool_priority and only then does the route
    cap cut. The old shape returned the first FILENAME_ROUTE_CAP hits in
    walk order, so on a home tree with thousands of files matching a
    common term by folder name alone, the walk filled the cap on noise
    before it ever reached the target's tree and the target never entered
    the candidate pool at all."""
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
    found.sort(key=lambda p: _pool_priority(p, clues))
    return found[:FILENAME_ROUTE_CAP]


def content_search(clues: Clues, roots: Sequence[str],
                   deadline_s: Optional[float] = None,
                   max_files: Optional[int] = None,
                   max_entries: Optional[int] = None,
                   stats: Optional[Dict[str, Any]] = None) -> Dict[str, Set[str]]:
    """Fallback 2: which content words appear inside which text files.
    Returns path -> set of matched words.

    Bounded four ways, so this route can never wedge a hunt: a wall-clock
    deadline (checked between files), a cap on files scanned, a cap on
    walk entries seen, and a per-file byte cap. Only regular files are
    ever opened (a FIFO or device with a text extension would block open()
    forever, and a blocked thread cannot be killed, so it must never be
    opened at all), and every read is capped at the byte cap plus one, so
    no read is ever unbounded. Unscannable entries (stat failures, files
    that vanish mid-walk, unreadable files) are skipped and counted.

    When any bound trips, the hits gathered so far are returned and, if a
    stats dict was passed, stats records partial=True and partial_reason
    as one of "deadline", "file_cap", "entry_cap". Nothing is silently
    truncated: locate() copies that labeling into its tried list."""
    if deadline_s is None:
        deadline_s = CONTENT_ROUTE_DEADLINE_S
    if max_files is None:
        max_files = MAX_CONTENT_FILES
    if max_entries is None:
        max_entries = MAX_CONTENT_ENTRIES
    started = time.monotonic()
    deadline_at = started + max(0.0, float(deadline_s))
    words = set(clues.content_words) | set(clues.name_tokens) | set(clues.purpose_terms)
    words = {w for w in words if len(w) > 2}
    hits: Dict[str, Set[str]] = {}
    scanned = 0
    skipped = 0
    skipped_nonregular = 0
    skipped_oversized = 0
    entries_seen = 0
    partial = False
    partial_reason = ""

    def _record_stats() -> None:
        if stats is not None:
            stats.update({
                "scanned": scanned,
                "skipped": skipped,
                "skipped_nonregular": skipped_nonregular,
                "skipped_oversized": skipped_oversized,
                "entries_seen": entries_seen,
                "partial": partial,
                "partial_reason": partial_reason,
                "elapsed_s": round(time.monotonic() - started, 3),
            })

    if not words:
        _record_stats()
        return hits
    if time.monotonic() >= deadline_at:
        partial = True
        partial_reason = "deadline"
        _record_stats()
        return hits
    # Phase 1: collect the scannable text files in the bounded walk,
    # applying the same entry, regularity, and size bounds as before.
    # Nothing is opened in this phase, so the pathological-entry guards
    # (no opens of non-regular files, no unbounded reads) are unchanged.
    collect: List[str] = []
    for path, is_dir in _walk(roots):
        if is_dir:
            continue
        entries_seen += 1
        if entries_seen > max_entries:
            partial = True
            partial_reason = "entry_cap"
            break
        if os.path.splitext(path)[1].lower() not in TEXT_EXTS:
            continue
        # Deadline is checked between files, before any stat or open.
        if time.monotonic() >= deadline_at:
            partial = True
            partial_reason = "deadline"
            break
        try:
            st = os.stat(path)
        except OSError:
            skipped += 1
            continue
        if not stat.S_ISREG(st.st_mode):
            skipped += 1
            skipped_nonregular += 1
            continue
        if st.st_size > MAX_CONTENT_BYTES:
            skipped_oversized += 1
            continue
        collect.append(path)
    # Phase 2: scan in pool-priority order, not walk order. The old shape
    # scanned in walk order until the file cap or the deadline cut, so a
    # tree with thousands of scannable files ahead of the target's tree
    # spent the whole budget on noise and the target was never even read.
    # Priority here is the identity part of scoring only (name, kind,
    # year, scope): contents are what the scan is about to learn.
    collect.sort(key=lambda p: _pool_priority(p, clues))
    for path in collect:
        if scanned >= max_files:
            if not partial:
                partial = True
                partial_reason = "file_cap"
            break
        # Deadline is checked between files, before any open.
        if time.monotonic() >= deadline_at:
            if not partial:
                partial = True
                partial_reason = "deadline"
            break
        scanned += 1
        try:
            with open(path, "rb") as f:
                raw = f.read(MAX_CONTENT_BYTES + 1)
        except OSError:
            skipped += 1
            continue
        body = raw.decode(errors="ignore").lower()
        body_tokens = set(tokens_of(body))
        matched = {w for w in words if w in body_tokens or ("-" in w and w in body)}
        if matched:
            hits[path] = matched
    _record_stats()
    return hits


# ---------------------------------------------------------------- scoring

def _kind_of(path: str, is_dir: bool) -> str:
    if is_dir:
        return "folder"
    return EXT_KIND.get(os.path.splitext(path)[1].lower(), "file")


# Instruction-frame tokens: words that belong to the shape of the request
# itself ("Reply with its full path", "somewhere on this computer", "in its
# name", "the file that mentions ..."), not to the target. Until the clue
# parser strips them upstream, scoring gives them no weight at all: a file
# earns nothing for containing or being named with the request's own
# phrasing. This is scoring-side robustness only; parse_clues is untouched.
FRAME_TOKENS = {
    "reply", "full", "path", "its", "name", "somewhere", "computer",
    "mentions", "mentioned", "mention", "lives", "living",
}

# Cache and build-intermediate scopes: package and module caches and build
# output trees. Files there are library internals, not user content, so
# they take a substantial penalty and lose to user documents, project
# folders, and ordinary directories at equal textual overlap. The penalty
# is waived when the clue set itself names the scope (a name, purpose, or
# content token matching the cache path segment that triggered it).
# Each entry: (path parts that trigger it, penalty, label).
_SCOPE_RULES = [
    (("go", "pkg", "mod"), 90.0, "Go module cache"),
    (("pkg", "mod"), 90.0, "module cache"),
    (("node_modules",), 90.0, "node_modules package cache"),
    (("mod", "cache"), 70.0, "module download cache"),
    ((".gradle",), 70.0, "Gradle cache"),
    (("intermediates",), 70.0, "build intermediates"),
    (("deriveddata",), 70.0, "DerivedData build output"),
    (("site-packages",), 70.0, "site-packages"),
    ((".cache",), 70.0, "cache folder"),
]


def _distinctive_name_tokens(clues: Clues) -> List[str]:
    """Name clues with instruction-frame tokens removed (scoring view)."""
    return [t for t in clues.name_tokens if t not in FRAME_TOKENS]


def _scope_penalty(path: str, clues: Clues) -> Tuple[float, str]:
    """Penalty for cache or build-intermediate locations, else (0, '').

    Waived when a clue token names the triggering segment, so a hunt that
    is actually about the cache (its name is in the request) is unaffected.
    """
    parts = [p for p in path.lower().replace("\\", "/").split("/") if p]
    clue_terms = (set(_distinctive_name_tokens(clues)) | set(clues.purpose_terms)
                  | set(clues.content_words))
    best: Tuple[float, str] = (0.0, "")
    for trigger, penalty, label in _SCOPE_RULES:
        n = len(trigger)
        for i in range(len(parts) - n + 1):
            if tuple(parts[i:i + n]) != trigger:
                continue
            segment_tokens = set()
            for seg in trigger:
                segment_tokens |= set(tokens_of(seg)) | {seg}
            if clue_terms & segment_tokens:
                return (0.0, "")
            if penalty > best[0]:
                best = (penalty, label)
            break
    return best


def _is_cache_scoped(path: str, clues: Clues) -> bool:
    return _scope_penalty(path, clues)[0] > 0


def score_candidate(path: str, clues: Clues, content_hits: Set[str]) -> Tuple[float, List[str]]:
    is_dir = os.path.isdir(path)
    kind = _kind_of(path, is_dir)
    name = os.path.basename(path)
    stem = os.path.splitext(name)[0].lower()
    stem_tokens = set(tokens_of(stem))
    path_tokens = set(tokens_of(path.lower()))
    score = 0.0
    why: List[str] = []

    # Target identity dominates: an exact name-token match on the target's
    # own terms is worth far more than any amount of generic content-word
    # overlap. A substring-only resemblance ("path" inside "paths") earns
    # almost nothing, and frame tokens earn nothing at all.
    name_tokens = _distinctive_name_tokens(clues)
    exact_hits = [t for t in name_tokens if t in stem_tokens]
    for t in exact_hits:
        score += 60
        why.append(f"name matches '{t}'")
    sub_hits = [t for t in name_tokens if t not in exact_hits and len(t) >= 3 and t in stem]
    for t in sub_hits:
        score += 8
        why.append(f"name partly matches '{t}'")
    name_hits = exact_hits + sub_hits
    path_only = [t for t in name_tokens if t not in name_hits and t in path_tokens]
    for t in path_only:
        score += 15
        why.append(f"folder path matches '{t}'")
    path_sub = [t for t in name_tokens if t not in name_hits and t not in path_only
                and len(t) >= 3 and t in path.lower()]
    for t in path_sub:
        score += 3
        why.append(f"folder path partly matches '{t}'")
    # (There is deliberately no extra bonus for matching every name clue:
    # with frame tokens still in the clue set that bonus almost never
    # fires for the real target, and when it does fire it triple-counts a
    # single mentioned word, which can be a negated one like "draft".)
    # A name made only of clue terms (the name IS the thing described, like
    # a folder named exactly "xod" or a file named "Invoice 1043") is the
    # strongest identity signal there is.
    identity_terms = set(name_tokens) | set(clues.purpose_terms)
    if clues.year:
        identity_terms.add(str(clues.year))
    exact_bonus = bool(stem_tokens and stem_tokens <= identity_terms)
    if exact_bonus:
        score += 50
        why.append("the name is exactly the thing described")

    # A purpose term in the name earns the same per-term weight as a name
    # token: both are descriptive terms for the target, and which bucket
    # the parser filed a word in must not decide a close pair. (A signed
    # contract and its draft share two terms and split the third; equal
    # per-term weight keeps that pair inside the ask margin instead of
    # letting the negated word in the request outrank the signed file.)
    # Purpose-in-name credit requires a whole-token match: every token
    # of the term must appear as a whole token of the name. The old
    # substring test paid the purpose term 'tax' its full 60 points
    # inside the longer token 'taxonomy', which closed two re-run 4
    # hunts (a W2 wage statement, a tax return summary) on an unrelated
    # code file. Multi-token and hyphenated terms ('pay stub', 'w-2')
    # still match names that carry their tokens.
    stem_parts = set(re.findall(r"[a-z0-9]+", stem))

    def _purpose_in_name(term: str) -> bool:
        if term in stem_tokens:
            return True
        parts = [p for p in re.findall(r"[a-z0-9]+", term) if p]
        return bool(parts) and all(p in stem_parts for p in parts)

    purpose_hits = [t for t in clues.purpose_terms if _purpose_in_name(t)]
    for t in purpose_hits[:3]:
        score += 60
        why.append(f"name fits the purpose ('{t}')")
    purpose_path = [t for t in clues.purpose_terms if t not in purpose_hits and t in path_tokens]
    for t in purpose_path[:2]:
        score += 10
        why.append(f"folder path fits the purpose ('{t}')")

    kind_refuted = False
    if clues.kind:
        if kind == clues.kind or (clues.kind == "photo" and kind == "image"):
            score += 25
            why.append(f"it is a {kind}")
        elif not is_dir:
            # A stated kind mismatch is strong negative evidence: a hunt
            # for a pdf or a spreadsheet must not be won by a text file
            # that merely contains the right words.
            kind_refuted = True
            score -= 60
            why.append(f"but it is a {kind}, not a {clues.kind}")

    year_in_name = False
    if clues.year:
        year_s = str(clues.year)
        if year_s in stem or year_s in path.lower():
            year_in_name = True
            score += 50
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
            # An explicitly excluded year is near-disqualifying: the
            # penalty must outweigh the full identity stack above, or a
            # strongly named wrong-year file leaks back into the list.
            score -= 200
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

    # Content hits help, but with diminishing returns and a hard cap: a
    # file that merely talks about a thing must not outrank the file that
    # is named for it. Frame tokens in the contents earn nothing. Words
    # the person actually used (name/content clues) count most;
    # purpose-expansion words found only in contents count a little.
    strong_words = (set(name_tokens) | set(clues.content_words)) - FRAME_TOKENS
    strong = sorted(w for w in content_hits if w in strong_words)
    weak = sorted(w for w in content_hits if w not in strong_words and w not in FRAME_TOKENS)
    content_total = 0.0
    for i, word in enumerate(strong):
        pts = 25 if i == 0 else (15 if i == 1 else (10 if i == 2 else 3))
        if content_total + pts > 55:
            pts = max(0.0, 55 - content_total)
        if pts <= 0:
            break
        content_total += pts
        score += pts
        why.append(f"contents mention '{word}'")
    for word in weak[:3]:
        score += 4
        why.append(f"contents mention '{word}'")

    # Found-threshold kind discipline. Credit that is not name identity
    # (purpose terms in the name or path, content words) cannot carry a
    # candidate over the found threshold in either of these shapes:
    #   * The candidate's kind contradicts the requested kind. Such a
    #     candidate needs name identity as well; purpose and content
    #     credit alone is capped below FOUND_THRESHOLD here, on top of
    #     the -60 deduction above, so no stack of content hits can
    #     outvote the contradiction.
    #   * The hunt names no kind at all, so no kind line or deduction
    #     ever fired (the re-run 4 L4 and L9 prompts name none, which
    #     is exactly how a code file closed both). There the ceiling
    #     applies on the evidence that exists: a candidate with no
    #     name-based evidence at all (no exact name token, no
    #     exact-name shape, no whole-token purpose term in the name)
    #     and no other real support (the person's own words inside the
    #     file, a year in the name) is capped the same way: path-only
    #     purpose nudges, purpose-expansion content words, and date
    #     nudges cannot stack into a name. A whole-token purpose term
    #     in the name IS real support ("find my W2" is won exactly
    #     that way), so it is not capped.
    has_identity = bool(exact_hits) or exact_bonus
    if kind_refuted and not has_identity and score > FOUND_THRESHOLD - 1.0:
        score = FOUND_THRESHOLD - 1.0
        why.append("it is not the kind asked for, and no name match backs it up, "
                   "so purpose or content credit alone cannot name it")
    elif (not clues.kind and not has_identity and not purpose_hits and not strong
            and not year_in_name and score > FOUND_THRESHOLD - 1.0):
        score = FOUND_THRESHOLD - 1.0
        why.append("no name evidence supports it, only path and content-expansion "
                   "words, so that alone cannot name it")

    penalty, label = _scope_penalty(path, clues)
    if penalty:
        score -= penalty
        why.append(f"it sits in a {label}, so it ranks below your own files")
    return score, why


def _pool_priority(path: str, clues: Clues) -> Tuple[float, int, str]:
    """Sort key for prioritizing route hits INSIDE pool construction,
    before any route cap or scan budget cuts. The primary signal is the
    identity part of the hunt's own scoring: score_candidate with no
    content hits, so exact name tokens, kind match, an explicit year,
    and the cache scope penalty all count, and generic content overlap
    does not (contents are unknown at route time anyway). Ties break
    toward shallower paths (top level user folders before deep archive
    or cache nests), then by path, so the order is fully deterministic
    and never depends on which route or walk position produced a hit
    first. Higher identity wins: the key sorts ascending on the negated
    score."""
    try:
        score = score_candidate(path, clues, set())[0]
    except Exception:
        score = 0.0
    return (-score, path.count(os.sep), path)


def _content_hits_for(path: str, words: Set[str]) -> Set[str]:
    """Content words inside one file, matched with the same rule
    content_search uses. Lets an index-only candidate (a path outside the
    walked roots, so the content route never scanned it) be scored on the
    same content evidence as if the content route had found it: route
    provenance must not change a candidate's score."""
    if os.path.splitext(path)[1].lower() not in TEXT_EXTS:
        return set()
    try:
        if os.path.getsize(path) > MAX_CONTENT_BYTES:
            return set()
        with open(path, "r", errors="ignore") as f:
            body = f.read().lower()
    except OSError:
        return set()
    body_tokens = set(tokens_of(body))
    return {w for w in words if w in body_tokens or ("-" in w and w in body)}


def kind_mismatch(cand: Dict[str, Any], kind: str) -> bool:
    """True when the candidate's own evidence lines state it is NOT the
    kind the hunt asked for. score_candidate phrases that evidence as
    "but it is a {actual}, not a {wanted}" (see the kind branch there);
    a candidate that refutes itself this way can never be presented as
    a match, however high it scored on other clues."""
    if not kind:
        return False
    pat = re.compile(r"not an? " + re.escape(kind) + r"\b")
    return any(pat.search(str(w)) for w in (cand.get("why") or []))


def kind_contradicts(cand: Dict[str, Any], kind: str) -> bool:
    """Structured kind check: True when the candidate's own kind FIELD
    contradicts the requested kind, whatever its why lines say. The
    prose check above only fires when the scorer itself ran with a kind
    clue and emitted the line; a candidate whose evidence never carried
    that line (the re-run 4 L4 and L9 deliveries printed kind "code"
    beside PDF hunts and still closed done) needs the field read
    directly. Folders are exempt, mirroring score_candidate, which
    never deducts for a folder."""
    if not kind or cand.get("is_folder"):
        return False
    actual = str(cand.get("kind") or "")
    if not actual or actual == kind:
        return False
    return not (kind == "photo" and actual == "image")


def has_name_identity(cand: Dict[str, Any], clues: Clues) -> bool:
    """Name identity, recomputed from fields: an exact name-token match
    on one of the hunt's name tokens, or the exact-name shape (the
    name's tokens are all clue terms, the shape score_candidate pays
    the exact-name bonus for). Purpose and content credit are not
    identity: a candidate carried only by those needs this check to
    fail before the kind discipline can name it."""
    stem = os.path.splitext(str(cand.get("name") or ""))[0].lower()
    stem_tokens = set(tokens_of(stem))
    if not stem_tokens:
        return False
    name_tokens = _distinctive_name_tokens(clues)
    if any(t in stem_tokens for t in name_tokens):
        return True
    identity_terms = set(name_tokens) | set(clues.purpose_terms)
    if clues.year:
        identity_terms.add(str(clues.year))
    return stem_tokens <= identity_terms


_NAME_EXACT_PREFIXES = ("name matches '", "folder name matches '",
                        "folder path matches '")
_SUPPORT_PREFIXES = ("contents mention", "year ", "modified in",
                     "it is a ")


def partial_name_only(cand: Dict[str, Any], clues: "Clues") -> bool:
    """True when a candidate's only positive evidence is an exact match
    on ONE of several name tokens, with nothing else supporting it: no
    exact-name bonus, no kind match, no year or season match, no content
    or purpose evidence. Lane 2 pays 60 for a single exact name token,
    so a file named board.dat clears the floor on a zebrafish deck hunt
    even though it covers a quarter of the target's identity. The
    exact-name bonus does not count as support here: it derives from
    that same single token (a one-word stem is trivially made only of
    clue terms), so it cannot rescue its own partial coverage. One-token
    coverage of a multi-token description, alone, is not a candidate:
    it is the re-run 3 noise shape in miniature. When the hunt has a
    single name token, matching it is full coverage and this is False."""
    tokens = clues.name_tokens or []
    if len(tokens) < 2:
        return False
    why = [str(w) for w in (cand.get("why") or [])]
    matched = set()
    for w in why:
        for prefix in _NAME_EXACT_PREFIXES:
            if w.startswith(prefix) and w.endswith("'"):
                matched.add(w[len(prefix):-1])
    if len(matched) != 1 or not matched.issubset(set(tokens)):
        return False
    for w in why:
        if w.startswith("but it is a "):
            continue
        if "fits the purpose" in w:
            return False
        if any(w.startswith(p) for p in _SUPPORT_PREFIXES):
            return False
    return True


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
           now: Optional[datetime] = None,
           content_deadline_s: float = CONTENT_ROUTE_DEADLINE_S,
           hunt_deadline_s: float = HUNT_DEADLINE_S) -> Dict[str, Any]:
    """Run a full hunt. Returns a dict with status found | ask | not_found,
    ranked candidates (each existence-verified, full path, with evidence),
    the tried list, and a diagnostics block (per-candidate route provenance
    and index visibility, plus how many pooled candidates the index missed).
    Never raises on a missing route; a route that fails
    is recorded in the tried list and the fallbacks still run."""
    clues = parse_clues(request, extra, now=now)
    roots = list(roots) if roots else default_roots()
    hunt_started = time.monotonic()
    hunt_deadline_at = hunt_started + max(0.0, float(hunt_deadline_s))
    tried: List[Dict[str, Any]] = []
    pool: Dict[str, Set[str]] = {}
    routes_by_path: Dict[str, Set[str]] = {}
    index_paths: Set[str] = set()

    def add(paths: Sequence[str], route: str):
        for p in paths:
            if p:
                pool.setdefault(str(p), set())
                routes_by_path.setdefault(str(p), set()).add(route)
                if route == "spotlight":
                    index_paths.add(str(p))

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
        # The content route gets whichever budget runs out first: its own
        # route deadline, or the time left under the total hunt deadline.
        remaining_s = hunt_deadline_at - time.monotonic()
        route_budget_s = max(0.0, min(float(content_deadline_s), remaining_s))
        content_stats: Dict[str, Any] = {}
        content_hits = content_search(clues, roots, deadline_s=route_budget_s,
                                       stats=content_stats)
        content_tried: Dict[str, Any] = {
            "route": "content",
            "terms": sorted(set(clues.content_words) | set(clues.name_tokens)),
            "hits": len(content_hits),
            "partial": bool(content_stats.get("partial")),
            "scanned": content_stats.get("scanned", 0),
            "skipped": content_stats.get("skipped", 0),
        }
        if content_stats.get("partial"):
            content_tried["partial_reason"] = content_stats.get("partial_reason", "")
        tried.append(content_tried)
        for p, words in content_hits.items():
            pool.setdefault(p, set()).update(words)
            routes_by_path.setdefault(p, set()).add("content")

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
        words_here = hits_words
        if not is_dir and path not in content_hits and routes_by_path.get(path) == {"spotlight"}:
            probe = {w for w in (set(clues.content_words) | set(clues.name_tokens) | set(clues.purpose_terms)) if len(w) > 2}
            words_here = _content_hits_for(path, probe)
        score, why = score_candidate(path, clues, words_here if not is_dir else set())
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
            parent_tokens = set(tokens_of(os.path.basename(parent).lower()))
            for t in _distinctive_name_tokens(clues):
                if t in parent_tokens:
                    score += 60
                    why.append(f"folder name matches '{t}'")
                elif len(t) >= 3 and t in os.path.basename(parent).lower():
                    score += 8
                    why.append(f"folder name partly matches '{t}'")
            penalty, label = _scope_penalty(parent, clues)
            if penalty:
                score -= penalty
                why.append(f"it sits in a {label}, so it ranks below your own files")
            existing = next((c for c in scored if c["path"] == parent), None)
            cand = _candidate_dict(parent, score, why)
            if cand:
                if existing:
                    existing.update(cand)
                else:
                    scored.append(cand)

    scored = [c for c in scored if c["score"] >= FOUND_THRESHOLD]
    # Fully deterministic order: score, then scope (user content before
    # cache or build trees, so among same-named files the one inside a
    # real project tree outranks ones inside module caches), then name,
    # then the full path, so equal candidates never keep pool insertion
    # order (which depended on which route produced a path first).
    scored.sort(key=lambda c: (-c["score"], 1 if _is_cache_scoped(c["path"], clues) else 0,
                               c["name"].lower(), c["path"]))
    top = scored[:MAX_CANDIDATES]

    missed = sorted(p for p in routes_by_path
                    if "spotlight" not in routes_by_path[p]
                    and routes_by_path[p] & {"filename", "content"})
    diagnostics: Dict[str, Any] = {
        "candidates": [
            {"path": c["path"],
             "routes": sorted(routes_by_path.get(c["path"], set())),
             "in_index": c["path"] in index_paths}
            for c in top
        ],
        "index_used": use_index,
        "index_missed_count": len(missed),
        "index_missed_examples": missed[:5],
    }

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
        "diagnostics": diagnostics,
    }
    def _not_found(rejected: List[Dict[str, Any]]) -> Dict[str, Any]:
        result["status"] = "not_found"
        result["candidates"] = []
        if rejected:
            result["rejected"] = rejected
        result["message"] = ("Nothing matched well enough to name. Tried: " +
                             "; ".join(f"{t['route']} ({t['hits']} hits)" for t in tried) + ".")
        for t in tried:
            if t.get("route") == "content" and t.get("partial"):
                result["message"] += (f" The content search was partial ({t.get('partial_reason', '')}), "
                                       "so more matches may exist than were seen.")
        return result

    if not top:
        return _not_found([])

    # The floor, the kind-mismatch block, and the partial-name block,
    # applied before the ask gate: a candidate is presentable only if it
    # clears ASK_FLOOR, its own evidence does not refute the requested
    # kind, its structured kind field does not contradict the requested
    # kind without name identity behind it, and its evidence is not one
    # lone name token out of several with nothing else behind it.
    # Everything else is recorded as checked-and-rejected, never
    # presented.
    eligible: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for cand in top:
        if (kind_mismatch(cand, clues.kind)
                or (kind_contradicts(cand, clues.kind)
                    and not has_name_identity(cand, clues))
                or cand["score"] < ASK_FLOOR
                or partial_name_only(cand, clues)):
            rejected.append(cand)
        else:
            eligible.append(cand)
    if not eligible:
        return _not_found(rejected)
    if rejected:
        result["rejected"] = rejected
    if len(eligible) == 1:
        result["status"] = "found"
        result["candidates"] = eligible
        return result
    result["candidates"] = eligible
    if (eligible[0]["score"] - eligible[1]["score"]) <= ASK_MARGIN:
        result["status"] = "ask"
        result["question"] = (f"Two close matches: {eligible[0]['name']} and {eligible[1]['name']}. "
                              "Which one did you mean?")
        return result
    result["status"] = "found"
    return result
