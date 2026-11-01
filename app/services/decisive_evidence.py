"""Decisive evidence: the delivery contract for structured tool results.

Some tool results ARE the answer: locate_files returns ranked candidates
with full paths and the evidence for each match. In the baseline hunts the
locate service was right every time and the delivered answer still dropped
all of it (0 of 13 answers carried a ranked candidate or an evidence field),
because the only copy the writing model ever saw was a 500-char truncated
JSON line in the evidence summary, cut before the first candidate's path
even completed. Prompt wording asking for verbatim delivery did not move
that number, because the model cannot deliver what it never received.

So delivery is a contract here, not a prompt:

  * A tool in DECISIVE_TOOLS can mark its result as decisive evidence.
  * The complete structured payload is captured at the tool boundary and
    stored out of band (on the task record, as evidence_json), never
    truncated. The in-conversation copy may stay bounded; this copy is not.
  * At completion, a canonical block is rendered from the stored payload
    deterministically and appended to the delivered answer. The model's
    own prose may accompany the block; it cannot replace it.
  * Output discipline wins: when the user dictated the reply's exact form,
    no block is appended (the 0.9.12 rule, same detector as the source
    link garnish guard).

Rendering for locate_files:
  found     ranked candidates in order, each with full path, kind,
            modified date, size, and its why lines.
  ask       both top candidates with paths, plus the tool's question.
  not_found the tried list (routes and terms), stated plainly, plus any
            checked-and-rejected candidates with their evidence.

Kind-mismatch block: a candidate whose own why lines state it is not
the kind the hunt asked for ("but it is a document, not a pdf", the
phrasing app/services/locate.py score_candidate produces) can never be
delivered as a found match. capture() downgrades such a payload before
any consumer (the task layer's done close included) can act on it, and
render_locate() applies the same partition again, so the block holds
at this layer even for a payload that never passed through locate().
"""
import re
from typing import Any, Dict, List, Optional

# The user dictated the reply's exact form: an appended block would break
# it. Same pattern the researcher uses to keep source links off exact
# replies (app/agent/researcher.py, _source_link_events).
EXACT_OUTPUT_RE = re.compile(
    r"reply with exactly|respond with exactly|answer with exactly|exactly this text|"
    r"exactly the following|nothing else|no other text|just the number|only the number|"
    r"reply with only|respond with only|answer with only", re.I)

DECISIVE_TOOLS = {"locate_files"}

# The scorer's kind-mismatch phrasing: "but it is a {actual}, not a
# {wanted}". The wanted kind named in the line is the refutation; the
# candidate is refuted whatever kind the hunt's clues recorded.
_KIND_MISMATCH_RE = re.compile(r"but it is an? .+?, not an? [a-z]+\b")


def _kind_refuted(cand: Dict[str, Any]) -> bool:
    return any(_KIND_MISMATCH_RE.search(str(w)) for w in (cand.get("why") or []))


def sanitize_locate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Partition a locate payload's candidates by the kind-mismatch
    block. Returns the payload unchanged when nothing is refuted;
    otherwise returns a copy with refuted candidates moved to
    "rejected", downgrading found/ask to not_found when no presentable
    candidate remains (and an ask with one survivor to found)."""
    if payload.get("status") not in ("found", "ask"):
        return payload
    cands = payload.get("candidates") or []
    refuted = [c for c in cands if _kind_refuted(c)]
    if not refuted:
        return payload
    kept = [c for c in cands if not _kind_refuted(c)]
    out = dict(payload)
    out["rejected"] = list(payload.get("rejected") or []) + refuted
    out["candidates"] = kept
    if not kept:
        out["status"] = "not_found"
        out.pop("question", None)
    elif payload.get("status") == "ask" and len(kept) == 1:
        out["status"] = "found"
        out.pop("question", None)
    return out


def exact_format_requested(text: str) -> bool:
    return bool(EXACT_OUTPUT_RE.search(text or ""))


def capture(tool_name: str, result: Any) -> Optional[Dict[str, Any]]:
    """Mark a tool result as decisive evidence, or None. The payload is the
    result itself, complete: nothing here trims, summarizes, or reorders."""
    if tool_name not in DECISIVE_TOOLS or not isinstance(result, dict):
        return None
    if tool_name == "locate_files":
        if result.get("status") not in ("found", "ask", "not_found"):
            return None
        if not isinstance(result.get("candidates"), list) or not isinstance(result.get("tried"), list):
            return None
        return {"tool": tool_name, "payload": sanitize_locate(result)}
    return None


def collect(pairs) -> List[Dict[str, Any]]:
    """Capture from (tool_name, result) pairs, keeping the LAST payload per
    tool: a retried hunt supersedes its first attempt, it does not stack."""
    by_tool: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for name, result in pairs:
        entry = capture(name, result)
        if entry:
            if entry["tool"] not in by_tool:
                order.append(entry["tool"])
            by_tool[entry["tool"]] = entry
    return [by_tool[t] for t in order]


def _size(n: Any) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n < 1024:
        return f"{n} bytes"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _candidate_lines(cand: Dict[str, Any], rank: int) -> List[str]:
    head = f"{rank}. **{cand.get('name') or 'file'}**"
    bits = [str(cand.get("kind") or "file")]
    if cand.get("modified"):
        bits.append(f"modified {cand['modified']}")
    size = _size(cand.get("size"))
    if size and not cand.get("is_folder"):
        bits.append(size)
    head += " - " + ", ".join(bits)
    lines = [head, f"   Path: {cand.get('path') or ''}"]
    why = cand.get("why") or []
    if why:
        lines.append("   Why: " + "; ".join(str(w) for w in why))
    return lines


def render_locate(payload: Dict[str, Any]) -> str:
    payload = sanitize_locate(payload)
    status = payload.get("status")
    cands = payload.get("candidates") or []
    if status == "found" and cands:
        lines = ["**What the search found** (ranked, from the locate search):"]
        for i, cand in enumerate(cands, 1):
            lines.extend(_candidate_lines(cand, i))
        return "\n".join(lines)
    if status == "ask" and len(cands) >= 2:
        lines = ["**Two close matches, too close to call:**"]
        for i, cand in enumerate(cands[:2], 1):
            lines.extend(_candidate_lines(cand, i))
        if payload.get("question"):
            lines.append(f"Question: {payload['question']}")
        return "\n".join(lines)
    if status == "not_found":
        lines = ["**Not found.** The search ran and nothing matched well enough to name."]
        tried = payload.get("tried") or []
        if tried:
            lines.append("Tried:")
            for t in tried:
                terms = ", ".join(str(x) for x in (t.get("terms") or []))
                line = f"- {t.get('route') or 'route'} route"
                if terms:
                    line += f", terms: {terms}"
                line += f" ({t.get('hits', 0)} hits)"
                if t.get("error"):
                    line += f" - error: {t['error']}"
                lines.append(line)
        rejected = payload.get("rejected") or []
        if rejected:
            lines.append("Checked and rejected:")
            for i, cand in enumerate(rejected, 1):
                lines.extend(_candidate_lines(cand, i))
        return "\n".join(lines)
    return ""


RENDERERS = {"locate_files": render_locate}


def render_block(entry: Dict[str, Any]) -> str:
    renderer = RENDERERS.get(entry.get("tool") or "")
    if not renderer:
        return ""
    return renderer(entry.get("payload") or {})


def block_for(entries: List[Dict[str, Any]], user_text: str) -> str:
    """The canonical block for a finished answer, or "" when there is no
    decisive evidence or the user dictated the reply's exact form."""
    if not entries or exact_format_requested(user_text):
        return ""
    blocks = [b for b in (render_block(e) for e in entries) if b]
    return "\n\n".join(blocks)


def apply_to_answer(answer: str, entries: List[Dict[str, Any]], user_text: str) -> str:
    """The delivered answer: the model's prose plus the canonical block.
    Prose that already carries the block verbatim is left alone."""
    block = block_for(entries, user_text)
    if not block or block in (answer or ""):
        return answer
    text = (answer or "").strip()
    return (text + "\n\n" if text else "") + block
