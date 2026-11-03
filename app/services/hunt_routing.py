"""Deterministic hunt routing: find/locate requests dispatch locate_files.

Three measurement runs (baseline, re-run 1, re-run 2) showed the same shape:
production file hunts never called locate_files. Left to choose its own
tools, the model rode the worker's computer_find_files / computer_read_file
(whose delivered answers dropped the full path; one hunt passed the literal
placeholder string "<matching file path from search results>" as a path),
and one hunt (a tax summary asked for by name plus year) answered from the
web research path with source links and never ran a local search at all.
Round 2's decisive-evidence contract (services/decisive_evidence.py) renders
locate_files results into the delivered answer, but it can only fire when
locate_files is actually called, so the routing decision cannot be left to
prompt wording or model tool choice. It is made here, deterministically,
from the request text alone:

  * A find/locate intent (a file or folder described by any clue type:
    name fragment, kind plus date, content words, purpose, a folder known
    by its contents, a document kind plus a year) is dispatched straight
    to the locate_files tool under the round 2 contract. The model does
    not get to pick a different tool for the hunt itself.
  * A local hunt is never answered with web sources: this lane runs
    instead of the planner, the fast lane, and the web research path, and
    it has no web fallback. A failed locate is reported as a failed
    locate, plainly.
  * Non-locate file work is untouched: reading a file the person named
    outright, organizing, editing, and every other task keep their
    existing lanes, and computer_find_files stays available to them.

Exact-format requests (the user dictated the reply's exact form) are not
intercepted: output discipline wins, as in round 2, and those keep the
planner path where the contract already suppresses appended blocks.
"""
import re
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

# The find phrasing itself: a find/locate/where-is verb aimed at the
# person's own files. "find out" is excluded: that is a research idiom,
# not a hunt.
_FIND_RE = re.compile(
    r"\bfind\b(?!\s+out\b)|\blocate\b|\bsearch\s+(?:my\s+|the\s+|this\s+)?"
    r"(?:computer|mac|files?|folders?|documents|desktop|downloads)\b|"
    r"\blook\s+for\b[^.?!]{0,80}\b(?:file|folder|my|the)\b|"
    r"\bwhere\s+(?:is|are)\s+(?:my|the|that|this)\b", re.I)

# A folder known by what is inside it: "the folder that has my Japan trip
# stuff", with or without a find verb in front.
_FOLDER_CONTENTS_RE = re.compile(
    r"\b(?:folder|directory)\b[^.?!]{0,60}\b(?:has|with|containing|contains|full\s+of)\b", re.I)

# A document kind plus a year, asked as a bare query ("tax return summary
# for 2025"): the L9 shape. Name-plus-year hunts failed three runs straight
# because this shape reads like a web question; it is a local hunt.
_DOC_KIND_RE = re.compile(
    r"\b(?:tax\s+return|summary|statement|invoice|contract|lease|agreement|"
    r"wage\s+statement|w-?2|budget|spreadsheet|photo|picture|resume|receipt|"
    r"deck|slides|presentation|document|file|folder)\b", re.I)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

# The person asked for the web, or for news/facts about the world: not a
# local hunt, whatever find wording it carries.
_WEB_ASK_RE = re.compile(
    r"\b(?:on\s+the\s+web|online|on\s+the\s+internet|web\s+search|"
    r"search\s+the\s+web|look\s+(?:it\s+|this\s+|that\s+)?up\s+online|"
    r"google\s+(?:for|it|this|that)\b|latest\s+news|news\s+about)\b", re.I)

# A change to carry out (move, delete, write, organize, send...), as
# opposed to a hunt: those keep their existing lanes. Read/open are not
# here: "find it and read it" is still a hunt first.
_CHANGE_RE = re.compile(
    r"\b(?:move|copy|rename|delete|remove|trash|organise|organize|tidy|"
    r"clean\s+up|edit|write|create|make|zip|unzip|convert|send|email|"
    r"upload|download|install|uninstall)\b", re.I)

# A find verb alone is not a hunt ("find a good restaurant", "where is
# the nearest station"): the request must also carry a local signal, a
# file-ish noun, a possessive, or the computer itself.
_LOCAL_SIGNAL_RE = re.compile(
    r"\b(?:files?|folders?|directory|pdf|photos?|pictures?|images?|"
    r"spreadsheet|sheet|documents?|contracts?|lease|invoice|receipt|"
    r"budget|deck|slides|presentation|summary|statement|resume|my|"
    r"this\s+computer|my\s+computer|my\s+mac|somewhere\s+on)\b", re.I)

# Finding a physical thing, not a file: not a hunt unless a file noun is
# also present ("find the file with my phone bill" still is one).
_PHYSICAL_RE = re.compile(r"\b(?:phone|iphone|android|airpods|keys|wallet)\b", re.I)
_FILE_NOUN_RE = re.compile(r"\b(?:files?|folders?|directory|pdf|documents?)\b", re.I)


def is_find_intent(text: str) -> bool:
    """True when the request is a hunt for the person's own file or folder.

    Deterministic, text-only, no model call. Any one of: find/locate
    phrasing, a folder described by its contents, or a document kind
    disambiguated by a year with a possessive or purpose framing. Web
    asks, change requests, and exact-format dictates are not hunts here.
    """
    q = (text or "").strip()
    if not q or len(q) > 2000:
        return False
    from app.services import decisive_evidence
    if decisive_evidence.exact_format_requested(q):
        return False
    if _WEB_ASK_RE.search(q):
        return False
    if _CHANGE_RE.search(q):
        return False
    if _PHYSICAL_RE.search(q) and not _FILE_NOUN_RE.search(q):
        return False
    if _FIND_RE.search(q) and _LOCAL_SIGNAL_RE.search(q):
        return True
    if _FOLDER_CONTENTS_RE.search(q):
        return True
    if _YEAR_RE.search(q) and _DOC_KIND_RE.search(q) and re.search(r"\b(?:my|for|from)\b", q, re.I):
        return True
    return False


def locate_root() -> str:
    """The root the deterministic dispatch searches: the home folder.

    computer_find_files, the tool hunts used to ride, searches from the
    home folder (Spotlight over the whole Mac); locate's own default
    roots cover only the six standard folders, which misses anything
    kept elsewhere in the home tree (the graded forest lives directly
    in it). One home root covers those folders too. The locate service
    bounds its walks, and its Spotlight route is scoped per root, so a
    home-wide hunt stays bounded the same way the service's hunts are.
    """
    return str(Path.home())


def answer_for(query: str, result: Any) -> str:
    """The delivered answer for a dispatched hunt: the round 2 canonical
    block rendered from the complete locate payload, or a plain failure
    line when the hunt itself could not run. Never a web answer, never
    a guessed path."""
    from app.services import decisive_evidence
    entries = decisive_evidence.collect([("locate_files", result)])
    block = decisive_evidence.block_for(entries, query)
    if block:
        return block
    if isinstance(result, dict) and result.get("error"):
        return f"The locate search could not run: {result['error']}"
    return "The locate search ran but returned no usable result, so I do not have a verified path to share."


async def stream_hunt(query: str, execute_tool) -> AsyncGenerator[Dict[str, Any], None]:
    """Run one hunt deterministically: dispatch locate_files once, deliver
    its decisive evidence. Yields the same event shapes the other lanes
    yield, so the task layer records the tool result (and with it the
    decisive payload, the ask state, and the verification flow) exactly
    as it does for a planned hunt."""
    args = {"request": query, "root_dir": locate_root()}
    yield {"type": "status", "content": "Searching this computer for it..."}
    yield {"type": "tool_call", "name": "locate_files", "args": args, "id": "hunt_locate_1"}
    try:
        result = await execute_tool("locate_files", args)
    except Exception as e:
        result = {"success": False, "error": str(e)}
    if isinstance(result, dict) and result.get("status") in ("found", "ask", "not_found"):
        top = (result.get("candidates") or [{}])[0]
        summary = (f"{result['status']}: {top.get('path')}" if top.get("path")
                   else f"{result['status']}: nothing matched well enough to name")
    elif isinstance(result, dict) and result.get("error"):
        summary = f"locate failed: {result['error']}"
    else:
        summary = "locate finished"
    yield {"type": "tool_result", "name": "locate_files", "id": "hunt_locate_1",
           "summary": summary[:200], "data": result}
    yield {"type": "content", "content": answer_for(query, result)}
    yield {"type": "done"}
