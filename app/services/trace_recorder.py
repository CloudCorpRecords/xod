"""
Trace Recorder — the data foundation for on-device specialist training.

Every agent run (chat or voice) is logged as a structured trace:
    utterance -> routed intent -> tool calls (name + args) -> outcome -> user feedback

Traces are clustered by `task_type`. Once a cluster has enough *good* examples,
it can be exported as chat-format JSONL and distilled into a small local
specialist model (see Phase 4: MLX LoRA pipeline).
"""
import json
import time
import uuid
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.paths import DATA_DIR

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = DATA_DIR / "xod.db"
TRAINING_DIR = DATA_DIR / "training"

# How many good examples a task cluster needs before it is "ready to distill"
READY_THRESHOLD = 50

# Specialists we plan to train, and which task types feed each one
SPECIALIST_TARGETS = {
    "intent_router": {
        "label": "Intent Router",
        "description": "Utterance → {tool, args} JSON. Powers instant hands-free commands.",
        "sources": ["voice_fastpath", "voice_llm_route"],
    },
    "file_operator": {
        "label": "File & Shell Operator",
        "description": "Natural language → exact computer_* file / shell calls.",
        "sources": ["files", "shell", "organize"],
    },
    "summarizer": {
        "label": "Spoken Summarizer",
        "description": "Tool output → 1–2 sentence spoken answer.",
        "sources": ["voice_summary"],
    },
    "media_director": {
        "label": "Media Studio Director",
        "description": "Video/image prompt → exact slice, frame extract, splice, and overlay pipelines.",
        "sources": ["media"],
    },
}

ROUTER_SYSTEM_PROMPT = (
    "You are Xod's intent router. Convert the user's spoken command into a single JSON "
    "object: {\"tool\": <tool name or \"agent\">, \"args\": {...}}. Output JSON only."
)


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_traces_table() -> None:
    c = _conn()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS traces (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            source TEXT NOT NULL,
            task_type TEXT NOT NULL,
            query TEXT NOT NULL,
            route_json TEXT,
            tool_calls_json TEXT,
            outcome TEXT,
            success INTEGER DEFAULT 1,
            feedback INTEGER DEFAULT 0,
            latency_ms INTEGER DEFAULT 0,
            model TEXT
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_traces_task ON traces(task_type)")
    c.commit()
    c.close()


def classify_task_type(tool_names: List[str], source: str = "chat") -> str:
    """Buckets a run into a task cluster based on the tools it used."""
    names = set(tool_names or [])
    if any(n in names for n in ("computer_edit_file", "computer_write_file", "computer_read_file",
                                 "computer_directory_tree", "computer_list_directory",
                                 "fs_read_workspace_file", "fs_write_workspace_file", "fs_list_workspace_files")):
        return "files"
    if any(n in names for n in ("computer_batch_organize", "fs_organize_files", "fs_move_file", "fs_create_folder")):
        return "organize"
    if any(n in names for n in ("computer_run_command", "computer_start_job", "computer_kill_process")):
        return "shell"
    if any(n.startswith("browser_") for n in names):
        return "browser"
    if any(n.startswith("computer_") for n in names):
        return "system"
    if any(n.startswith("media_") for n in names):
        return "media"
    if names:
        return "research"
    return "conversation"


def record_trace(
    source: str,
    query: str,
    task_type: Optional[str] = None,
    route: Optional[Dict[str, Any]] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    outcome: str = "",
    success: bool = True,
    latency_ms: int = 0,
    model: Optional[str] = None,
) -> str:
    """Persists a single trace. Never raises — tracing must not break the agent."""
    try:
        tool_calls = tool_calls or []
        if not task_type:
            task_type = classify_task_type([t.get("name", "") for t in tool_calls], source)
        trace_id = f"tr-{uuid.uuid4().hex[:12]}"
        c = _conn()
        c.execute(
            "INSERT INTO traces (id, created_at, source, task_type, query, route_json, tool_calls_json, "
            "outcome, success, latency_ms, model) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                trace_id, time.time(), source, task_type, (query or "")[:4000],
                json.dumps(route) if route else None,
                json.dumps(tool_calls)[:20000],
                (outcome or "")[:4000],
                1 if success else 0,
                int(latency_ms or 0),
                model,
            ),
        )
        c.commit()
        c.close()
        return trace_id
    except Exception:
        return ""


def set_feedback(trace_id: str, value: int) -> bool:
    """value: +1 (good / thumbs up), -1 (bad), 0 (neutral)."""
    try:
        c = _conn()
        cur = c.execute("UPDATE traces SET feedback = ? WHERE id = ?", (max(-1, min(1, int(value))), trace_id))
        c.commit()
        ok = cur.rowcount > 0
        c.close()
        return ok
    except Exception:
        return False


def get_lab_stats() -> Dict[str, Any]:
    """Aggregated Model Lab telemetry: clusters, readiness per specialist, recent traces."""
    c = _conn()
    rows = c.execute(
        "SELECT task_type, COUNT(*) AS n, "
        "SUM(CASE WHEN success = 1 AND feedback >= 0 THEN 1 ELSE 0 END) AS good, "
        "AVG(latency_ms) AS avg_latency "
        "FROM traces GROUP BY task_type ORDER BY n DESC"
    ).fetchall()
    clusters = [
        {
            "task_type": r["task_type"],
            "count": r["n"],
            "good": r["good"] or 0,
            "avg_latency_ms": int(r["avg_latency"] or 0),
        }
        for r in rows
    ]
    by_type = {cl["task_type"]: cl for cl in clusters}

    specialists = []
    for key, spec in SPECIALIST_TARGETS.items():
        good = sum(by_type.get(s, {}).get("good", 0) for s in spec["sources"])
        specialists.append({
            "key": key,
            "label": spec["label"],
            "description": spec["description"],
            "good_examples": good,
            "threshold": READY_THRESHOLD,
            "progress": min(1.0, good / READY_THRESHOLD),
            "status": "ready_to_train" if good >= READY_THRESHOLD else "collecting",
        })

    recent = c.execute(
        "SELECT id, created_at, source, task_type, query, success, feedback, latency_ms "
        "FROM traces ORDER BY created_at DESC LIMIT 12"
    ).fetchall()
    total = c.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
    c.close()

    return {
        "total_traces": total,
        "clusters": clusters,
        "specialists": specialists,
        "recent": [dict(r) for r in recent],
        "ready_threshold": READY_THRESHOLD,
    }


def export_training_data(specialist: str = "intent_router") -> Dict[str, Any]:
    """
    Exports good traces as chat-format JSONL (mlx-lm / OpenAI fine-tune compatible):
        {"messages": [{"role": "system"...}, {"role": "user"...}, {"role": "assistant"...}]}
    """
    spec = SPECIALIST_TARGETS.get(specialist)
    if not spec:
        return {"success": False, "error": f"Unknown specialist: {specialist}"}

    TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    c = _conn()
    placeholders = ",".join("?" for _ in spec["sources"])
    rows = c.execute(
        f"SELECT query, route_json, tool_calls_json, outcome FROM traces "
        f"WHERE (task_type IN ({placeholders}) OR source IN ({placeholders})) "
        f"AND success = 1 AND feedback >= 0 "
        f"ORDER BY created_at ASC",
        spec["sources"] + spec["sources"],
    ).fetchall()
    c.close()

    out_path = TRAINING_DIR / f"{specialist}.jsonl"
    written = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            if specialist == "intent_router":
                route = json.loads(r["route_json"]) if r["route_json"] else None
                if not route or not route.get("tool"):
                    continue
                target = json.dumps({"tool": route["tool"], "args": route.get("args", {})})
                messages = [
                    {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                    {"role": "user", "content": r["query"]},
                    {"role": "assistant", "content": target},
                ]
            elif specialist == "summarizer":
                if not r["outcome"]:
                    continue
                messages = [
                    {"role": "system", "content": "Summarize the tool result for speech in 1-2 short sentences."},
                    {"role": "user", "content": r["query"]},
                    {"role": "assistant", "content": r["outcome"]},
                ]
            elif specialist == "media_director":
                calls = json.loads(r["tool_calls_json"] or "[]")
                if not calls:
                    continue
                messages = [
                    {"role": "system", "content": "You are Xod's on-device Media Director. Output JSON tool calls for video splicing, frame extraction, or motion synthesis."},
                    {"role": "user", "content": r["query"]},
                    {"role": "assistant", "content": json.dumps(calls)},
                ]
            else:
                calls = json.loads(r["tool_calls_json"] or "[]")
                if not calls:
                    continue
                messages = [
                    {"role": "system", "content": "Translate the request into Xod computer tool calls as JSON."},
                    {"role": "user", "content": r["query"]},
                    {"role": "assistant", "content": json.dumps(calls[:4])},
                ]
            f.write(json.dumps({"messages": messages}) + "\n")
            written += 1

    return {"success": True, "specialist": specialist, "path": str(out_path), "examples": written}


init_traces_table()
