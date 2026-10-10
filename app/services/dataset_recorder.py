"""
Dataset recorder: the data flywheel for Xod's mini models.

Every tagged LLM call (a browser step, a research plan, a report...) is stored as a
(messages -> response) example. Outcomes label it later:
    +1  good   (the action worked, the run succeeded, the user approved it)
    -1  bad    (the action failed, did nothing, invented a value, the user rejected it)
     0  unlabeled / neutral
Good examples from the big "teacher" model are what small specialist students learn from.

Nothing here may break the agent: every public function swallows its own errors.
"""
import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.paths import DATA_DIR

logger = logging.getLogger("xod.dataset")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
# XOD_DATASET_DB lets tests keep their examples out of your real training data.
DB_PATH = Path(os.environ.get("XOD_DATASET_DB") or (DATA_DIR / "xod.db"))

# Prompts longer than this are trimmed before storing (keeps the DB and training sane).
MAX_MESSAGE_CHARS = 16000


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_tables() -> None:
    c = _conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS llm_examples (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            task TEXT NOT NULL,
            group_id TEXT,
            model TEXT,
            provider TEXT,
            think INTEGER DEFAULT 0,
            messages_json TEXT NOT NULL,
            response TEXT NOT NULL,
            prompt_hash TEXT,
            latency_ms INTEGER DEFAULT 0,
            label INTEGER DEFAULT 0,
            label_reason TEXT,
            meta_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_examples_task ON llm_examples(task);
        CREATE INDEX IF NOT EXISTS idx_examples_group ON llm_examples(group_id);
        CREATE TABLE IF NOT EXISTS example_groups (
            group_id TEXT PRIMARY KEY,
            task TEXT,
            outcome TEXT,
            updated_at REAL
        );
        """
    )
    c.commit()
    c.close()


def _text_only(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Students are text models: drop images (keep a marker) and trim very long content."""
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            parts = []
            for p in content:
                if p.get("type") == "text":
                    parts.append(p.get("text", ""))
                elif p.get("type") == "image_url":
                    parts.append("[screenshot attached]")
            content = "\n".join(parts)
        content = str(content or "")
        if len(content) > MAX_MESSAGE_CHARS:
            content = content[:MAX_MESSAGE_CHARS] + "\n[...trimmed]"
        out.append({"role": m.get("role", "user"), "content": content})
    return out


def capture(
    task: str,
    messages: List[Dict[str, Any]],
    response: str,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    think: bool = False,
    latency_ms: int = 0,
    group_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Stores one teacher example. Returns its id ('' on failure)."""
    try:
        if not task or not response or not response.strip():
            return ""
        msgs = _text_only(messages)
        user_text = "\n".join(m["content"] for m in msgs if m["role"] != "system")
        example_id = f"ex-{uuid.uuid4().hex[:12]}"
        c = _conn()
        c.execute(
            "INSERT INTO llm_examples (id, created_at, task, group_id, model, provider, think, messages_json, response, "
            "prompt_hash, latency_ms, meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                example_id, time.time(), task, group_id, model, provider, 1 if think else 0,
                json.dumps(msgs), response[:MAX_MESSAGE_CHARS],
                hashlib.sha1(user_text.encode("utf-8", "ignore")).hexdigest(),
                int(latency_ms or 0), json.dumps(meta) if meta else None,
            ),
        )
        c.commit()
        c.close()
        return example_id
    except Exception as e:
        logger.debug(f"capture skipped: {e}")
        return ""


def label(example_id: str, value: int, reason: str = "") -> bool:
    """Sets +1 / -1 / 0 on one example. A bad label is never overwritten by a good one."""
    if not example_id:
        return False
    try:
        c = _conn()
        cur = c.execute(
            "UPDATE llm_examples SET label = ?, label_reason = ? WHERE id = ? AND NOT (label = -1 AND ? > 0)",
            (max(-1, min(1, int(value))), (reason or "")[:200], example_id, int(value)),
        )
        c.commit()
        ok = cur.rowcount > 0
        c.close()
        return ok
    except Exception:
        return False


def set_group_outcome(group_id: str, task: str, outcome: str) -> None:
    """Records how a whole run ended (e.g. a browser mission 'completed' or 'failed')."""
    if not group_id:
        return
    try:
        c = _conn()
        c.execute(
            "INSERT INTO example_groups (group_id, task, outcome, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(group_id) DO UPDATE SET outcome = excluded.outcome, updated_at = excluded.updated_at",
            (group_id, task, outcome, time.time()),
        )
        c.commit()
        c.close()
    except Exception:
        pass


def label_group(group_id: str, value: int, reason: str = "") -> int:
    """User feedback on a whole run: applies to every example in it that isn't already bad."""
    try:
        c = _conn()
        cur = c.execute(
            "UPDATE llm_examples SET label = ?, label_reason = ? WHERE group_id = ? AND label != -1",
            (max(-1, min(1, int(value))), (reason or "")[:200], group_id),
        )
        c.commit()
        n = cur.rowcount
        c.close()
        return n
    except Exception:
        return 0


def task_stats() -> List[Dict[str, Any]]:
    """Per-task counts for the Model Lab."""
    try:
        c = _conn()
        rows = c.execute(
            """
            SELECT e.task AS task, COUNT(*) AS total,
                   SUM(CASE WHEN e.label = 1 THEN 1 ELSE 0 END) AS good,
                   SUM(CASE WHEN e.label = -1 THEN 1 ELSE 0 END) AS bad,
                   COUNT(DISTINCT e.prompt_hash) AS unique_prompts,
                   AVG(e.latency_ms) AS avg_latency,
                   MAX(e.created_at) AS last_at
            FROM llm_examples e GROUP BY e.task ORDER BY total DESC
            """
        ).fetchall()
        c.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def live_stats(task: str) -> Dict[str, Any]:
    """How a switched-on specialist is doing in real use (its own calls, labelled by outcome)."""
    try:
        c = _conn()
        r = c.execute(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN label = 1 THEN 1 ELSE 0 END) AS good, "
            "SUM(CASE WHEN label = -1 THEN 1 ELSE 0 END) AS bad, AVG(latency_ms) AS ms "
            "FROM llm_examples WHERE task = ? AND provider = 'specialist'", (task,)
        ).fetchone()
        c.close()
        n = r["n"] or 0
        return {"calls": n, "good": r["good"] or 0, "bad": r["bad"] or 0,
                "success_rate": round((r["good"] or 0) / n, 3) if n else None, "avg_latency_ms": int(r["ms"] or 0)}
    except Exception:
        return {"calls": 0}


def fetch_training_examples(
    task: str,
    min_label: int = 1,
    exclude_group_outcomes: tuple = ("failed",),
    teacher_only: bool = True,
) -> List[Dict[str, Any]]:
    """
    Good, de-duplicated examples for one task, oldest first.
    Excludes runs that ended badly and (by default) anything a student model produced,
    so students never learn from their own mistakes.
    """
    c = _conn()
    q = (
        "SELECT e.* FROM llm_examples e LEFT JOIN example_groups g ON g.group_id = e.group_id "
        "WHERE e.task = ? AND e.label >= ? "
    )
    params: List[Any] = [task, min_label]
    if exclude_group_outcomes:
        q += f"AND (g.outcome IS NULL OR g.outcome NOT IN ({','.join('?' for _ in exclude_group_outcomes)})) "
        params += list(exclude_group_outcomes)
    if teacher_only:
        q += "AND (e.provider IS NULL OR e.provider != 'specialist') "
    q += "ORDER BY e.created_at ASC"
    rows = c.execute(q, params).fetchall()
    c.close()
    seen, out = set(), []
    for r in rows:
        key = (r["prompt_hash"], r["response"].strip())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "id": r["id"],
            "messages": json.loads(r["messages_json"]),
            "response": r["response"],
            "group_id": r["group_id"],
        })
    return out


init_tables()
