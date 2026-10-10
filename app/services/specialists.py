"""
Specialist mini models: small students trained on Xod's own good examples.

A specialist covers one task (e.g. browser steps). Once trained and evaluated, it can be
switched on: Xod then starts it as a local OpenAI-compatible server (mlx_lm.server)
and llm_service sends that task's *fast* calls to it. Thinking/escalated calls still go
to the big teacher model, so a student's mistakes fall back to the teacher.
"""
import asyncio
import json
import logging
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from app.paths import DATA_DIR

logger = logging.getLogger("xod.specialists")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = DATA_DIR / "models"
STATE_PATH = MODELS_DIR / "specialists.json"
VENV_PYTHON = Path(sys.executable)   # same Python as the server

# Specialists learned from tagged LLM calls (dataset_recorder). The older trace-based
# ones (intent router etc.) live in trace_recorder.SPECIALIST_TARGETS.
SPECIALISTS: Dict[str, Dict[str, Any]] = {
    "browser_navigator": {
        "label": "Browser Navigator",
        "description": "Picks the next click/type/scroll on a web page. Handles the fast browser steps.",
        "task": "browser_step",
        "base_model": "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
        "threshold": 150,
        "max_seq_length": 6144,   # upper bound; the trainer sizes to the data
        "validator": "json_action",
    },
    "research_planner": {
        "label": "Research Planner",
        "description": "Decides between a direct answer and a tool plan, and picks the tools.",
        "task": "planner",
        "base_model": "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
        "threshold": 60,
        "max_seq_length": 4096,
        "validator": "json_decision",
        "min_label": 0,
    },
}

# Fields that actually define each browser action. Teachers often fill in the rest with
# noise (a url on a click, press_enter on a select); training on that teaches students noise.
ACTION_FIELDS: Dict[str, tuple] = {
    "click": ("index",), "hover": ("index",), "check": ("index",), "uncheck": ("index",),
    "upload_resume": ("index",), "create_password": ("index", "username"),
    "type": ("index", "text", "press_enter"), "select": ("index", "text"), "choose": ("index", "text"),
    "log_in": ("username_index", "password_index"),
    "navigate": ("url",), "switch_tab": ("tab",), "close_tab": ("tab",),
    "scroll": ("direction",), "press_key": ("text",),
    "read_more": (), "back": (), "wait": (),
    "ask_user": ("question",), "finish": ("answer",),
}
ALWAYS_KEEP = ("reasoning", "plan", "remember", "action")


def clean_action(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Keeps only the fields that matter for this action (plus reasoning/plan/notes)."""
    keep = set(ALWAYS_KEEP) | set(ACTION_FIELDS.get(str(obj.get("action")), ()))
    return {k: v for k, v in obj.items() if k in keep and v not in (None, "")}


# A student must score at least this on held-out teacher examples to be switched on.
PROMOTE_MIN_ACTION_MATCH = 0.70
PROMOTE_MIN_VALID_JSON = 0.95

_servers: Dict[str, Dict[str, Any]] = {}   # name -> {"proc", "port", "ready"}


def load_state() -> Dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def save_state(state: Dict[str, Any]) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_PATH)


def specialist_for_task(task: str) -> Optional[str]:
    for name, spec in SPECIALISTS.items():
        if spec["task"] == task:
            return name
    return None


def active_endpoint_for(task: str) -> Optional[Dict[str, Any]]:
    """Endpoint of a running, switched-on specialist for this task, else None (use the teacher)."""
    name = specialist_for_task(task)
    if not name:
        return None
    entry = load_state().get(name) or {}
    srv = _servers.get(name)
    if not entry.get("enabled") or not srv or not srv.get("ready") or srv["proc"].poll() is not None:
        return None
    return {
        "provider": "specialist",
        "base_url": f"http://127.0.0.1:{srv['port']}/v1",
        "model": entry["base_model"],
        "api_key": "local",
    }


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start_server(name: str) -> Dict[str, Any]:
    """Starts mlx_lm.server for a specialist's active adapter and waits until it answers."""
    entry = load_state().get(name) or {}
    adapter = entry.get("adapter_dir")
    if not adapter or not Path(adapter, "adapters.safetensors").exists():
        return {"success": False, "error": f"{name} has no trained adapter yet"}
    await stop_server(name)
    port = _free_port()
    proc = subprocess.Popen(
        [str(VENV_PYTHON), "-m", "mlx_lm.server", "--model", entry["base_model"],
         "--adapter-path", adapter, "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _servers[name] = {"proc": proc, "port": port, "ready": False}
    deadline = time.time() + 180
    async with httpx.AsyncClient(timeout=3) as client:
        while time.time() < deadline:
            if proc.poll() is not None:
                return {"success": False, "error": f"specialist server exited (code {proc.returncode})"}
            try:
                r = await client.get(f"http://127.0.0.1:{port}/v1/models")
                if r.status_code == 200:
                    _servers[name]["ready"] = True
                    logger.info(f"Specialist {name} serving on port {port}")
                    return {"success": True, "port": port}
            except Exception:
                pass
            await asyncio.sleep(1.5)
    await stop_server(name)
    return {"success": False, "error": "specialist server did not start in time"}


async def stop_server(name: str) -> None:
    srv = _servers.pop(name, None)
    if srv and srv["proc"].poll() is None:
        srv["proc"].terminate()
        try:
            srv["proc"].wait(timeout=5)
        except Exception:
            srv["proc"].kill()


async def set_enabled(name: str, enabled: bool) -> Dict[str, Any]:
    if name not in SPECIALISTS:
        return {"success": False, "error": f"unknown specialist {name}"}
    state = load_state()
    entry = state.get(name) or {}
    if enabled:
        ev = entry.get("eval") or {}
        if not ev.get("promotable"):
            return {"success": False, "error": "This model hasn't passed its test yet. Train and evaluate it first."}
        res = await start_server(name)
        if not res.get("success"):
            return res
    else:
        await stop_server(name)
    entry["enabled"] = enabled
    state[name] = entry
    save_state(state)
    return {"success": True, "name": name, "enabled": enabled}


async def start_enabled_servers() -> None:
    """Called at app startup: bring back specialists that were switched on."""
    for name, entry in load_state().items():
        if entry.get("enabled") and name in SPECIALISTS:
            res = await start_server(name)
            if not res.get("success"):
                logger.warning(f"Could not start specialist {name}: {res.get('error')}")


def _lesson_estimate(name: str, stats: Dict[str, Any]) -> Dict[str, Any]:
    """Saved lessons + failures still to correct + mistakes the student will make on its practice prompts (~1 in 3)."""
    train = DATA_DIR / "training" / name / "train.jsonl"
    practice = len(train.read_text().splitlines()) // 3 if train.exists() else 0
    estimate = stats.get("total", 0) + stats.get("uncorrected_failures", 0) + practice
    return {**stats, "estimate": estimate, "ready": estimate >= stats.get("min_lessons", 100)}


def overview() -> List[Dict[str, Any]]:
    """Model Lab view of every specialist: data collected, readiness, model, test results, live status."""
    from app.services import dataset_recorder, preference_data
    stats = {s["task"]: s for s in dataset_recorder.task_stats()}
    state = load_state()
    out = []
    for name, spec in SPECIALISTS.items():
        st = stats.get(spec["task"], {})
        usable = len(dataset_recorder.fetch_training_examples(spec["task"], min_label=spec.get("min_label", 1)))
        entry = state.get(name) or {}
        srv = _servers.get(name)
        out.append({
            "name": name,
            "label": spec["label"],
            "description": spec["description"],
            "task": spec["task"],
            "examples_total": st.get("total", 0),
            "examples_good": st.get("good", 0),
            "examples_bad": st.get("bad", 0),
            "usable": usable,
            "threshold": spec["threshold"],
            "progress": min(1.0, usable / spec["threshold"]),
            "ready": usable >= spec["threshold"],
            "base_model": entry.get("base_model") or spec["base_model"],
            "trained_job": entry.get("job_id"),
            "eval": entry.get("eval"),
            "enabled": bool(entry.get("enabled")),
            "serving": bool(srv and srv.get("ready") and srv["proc"].poll() is None),
            "live": dataset_recorder.live_stats(spec["task"]),
            "mistakes": _lesson_estimate(name, preference_data.pair_stats(spec["task"])),
        })
    return out
