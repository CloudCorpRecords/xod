"""
"This, not that" pairs for preference training (see mlx_dpo.py).

Sources, from cheapest to most expensive:
  rule_fix           a failure whose correct answer is known by rule (e.g. an invented form value
                     should have been a question to the user). No model needed.
  teacher_correction the teacher (thinking mode) is shown a failed action and what went wrong,
                     and gives the right one. Covers failures by the teacher itself and by a
                     switched-on student in real use, the most valuable mistakes to learn from.
  student_mistake    mined at training time by mlx_dpo (the student's answers to practice prompts
                     that disagree with a teacher answer that worked).
"""
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services import dataset_recorder

logger = logging.getLogger("xod.preferences")

# Preference training memorizes small sets and gets worse (measured: 18 pairs took a student from
# 50% to 25%). Below this many saved lessons, "Learn from mistakes" stays locked.
MIN_LESSONS = 100

# Failure reasons a correction can fix. "no usable action" / "asked to think" aren't real decisions.
CORRECTABLE = ("action failed", "no effect", "submitted an incomplete form", "tried to repeat",
               "repeated a finished", "invented a value")


def init_table() -> None:
    c = dataset_recorder._conn()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS preference_pairs (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            task TEXT NOT NULL,
            example_id TEXT UNIQUE,
            prompt_json TEXT NOT NULL,
            chosen TEXT NOT NULL,
            rejected TEXT NOT NULL,
            source TEXT NOT NULL,
            note TEXT
        )
        """
    )
    c.commit()
    c.close()


def _bad_examples(task: str, limit: int) -> List[Dict[str, Any]]:
    """Failed decisions not yet turned into a pair, newest first (student failures first)."""
    c = dataset_recorder._conn()
    rows = c.execute(
        "SELECT e.* FROM llm_examples e LEFT JOIN preference_pairs p ON p.example_id = e.id "
        "WHERE e.task = ? AND e.label = -1 AND p.id IS NULL "
        "ORDER BY (e.provider = 'specialist') DESC, e.created_at DESC LIMIT ?",
        (task, limit * 3),
    ).fetchall()
    c.close()
    out = []
    for r in rows:
        reason = r["label_reason"] or ""
        if any(reason.startswith(k) for k in CORRECTABLE):
            out.append(dict(r))
        if len(out) >= limit:
            break
    return out


def _insert(task: str, example: Dict[str, Any], chosen: str, source: str, note: str = "") -> None:
    msgs = json.loads(example["messages_json"])
    c = dataset_recorder._conn()
    c.execute(
        "INSERT OR IGNORE INTO preference_pairs (id, created_at, task, example_id, prompt_json, chosen, rejected, source, note) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (f"pp-{uuid.uuid4().hex[:12]}", time.time(), task, example["id"], json.dumps(msgs),
         chosen, example["response"], source, note[:200]),
    )
    c.commit()
    c.close()


def _same_action(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    from app.services.specialists import clean_action
    strip = lambda d: {k: v for k, v in clean_action(d).items() if k not in ("reasoning", "plan", "remember")}
    return strip(a) == strip(b)


async def build_pairs(task: str = "browser_step", limit: int = 25) -> Dict[str, Any]:
    """Turns recent failures into pairs. Uses the teacher, so run it before pausing the teacher."""
    from app.services.llm import llm_service, parse_llm_json
    made = {"rule_fix": 0, "teacher_correction": 0, "skipped": 0}
    for ex in _bad_examples(task, limit):
        reason = ex["label_reason"] or ""
        bad = parse_llm_json(ex["response"])

        # Rule: an answer nobody gave should have been a question to the user.
        if reason.startswith("invented a value"):
            question = reason.split("'", 1)[-1].rstrip("'") or "this field"
            chosen = {"reasoning": "The form asks something the user hasn't told me, so I must ask instead of guessing.",
                      "action": "ask_user", "question": f"The form asks: \"{question}\". What should I answer?"}
            _insert(task, ex, json.dumps(chosen, ensure_ascii=False), "rule_fix", reason)
            made["rule_fix"] += 1
            continue

        # Teacher correction: show the failed choice and what happened, ask for the right one.
        msgs = json.loads(ex["messages_json"])
        feedback = (f"That action was wrong: {reason}. Look at the page again and reply with ONLY the JSON for the "
                    "correct next action instead. Don't repeat the failed action.")
        try:
            reply = await llm_service.chat_completion(
                messages=msgs + [{"role": "assistant", "content": ex["response"]}, {"role": "user", "content": feedback}],
                temperature=0.2, max_tokens=3000,  # thinking on: corrections should be careful
            )
            fixed = parse_llm_json(reply.get("content", ""))
        except Exception as e:
            logger.warning(f"Correction failed for {ex['id']}: {e}")
            fixed = {}
        if not fixed.get("action") or (bad and _same_action(fixed, bad)) or fixed.get("think"):
            made["skipped"] += 1
            continue
        fixed.pop("think", None)
        _insert(task, ex, json.dumps(fixed, ensure_ascii=False), "teacher_correction", reason)
        made["teacher_correction"] += 1
    return made


def export_pairs(task: str, out_path: Path) -> int:
    c = dataset_recorder._conn()
    rows = c.execute("SELECT prompt_json, chosen, rejected, source FROM preference_pairs WHERE task = ?", (task,)).fetchall()
    c.close()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"prompt": json.loads(r["prompt_json"]), "chosen": r["chosen"],
                                "rejected": r["rejected"], "source": r["source"]}, ensure_ascii=False) + "\n")
    return len(rows)


def pair_stats(task: str) -> Dict[str, Any]:
    try:
        c = dataset_recorder._conn()
        rows = c.execute("SELECT source, COUNT(*) AS n FROM preference_pairs WHERE task = ? GROUP BY source", (task,)).fetchall()
        c.close()
        by_source = {r["source"]: r["n"] for r in rows}
        total = sum(by_source.values())
        return {"total": total, "by_source": by_source, "min_lessons": MIN_LESSONS,
                "ready": total >= MIN_LESSONS, "uncorrected_failures": len(_bad_examples(task, 500))}
    except Exception:
        return {"total": 0, "by_source": {}, "uncorrected_failures": 0}


init_table()
