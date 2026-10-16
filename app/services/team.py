"""
The team: more than one brain on a job.

Brains     every model Xod can use that calls tools (models on this computer, model servers you added, cloud models
           you have a key for) and outside AI agents connected over MCP (another Xod, a coding agent…) that you mark
           as teammates.
Tiers      small (quick lookups, mechanical edits), standard (the chat model) and large (hard reasoning). Picked by
           themselves from what's here (small: the lightest local model that fits; large: a cloud model with a key,
           else the best-scoring local one), or set by hand in Tools → Agent → Team.
Subagents  the worker can hand a self-contained piece of a job to a subagent on a tier (team_delegate). Each runs its
           own short loop with the computer tools and reports back a summary. Several handed off in one go run at the
           same time: cloud ones truly in parallel; local ones share the GPU (Ollama queues them). Their steps are
           logged under SUBAGENT 01, 02… for the Agent page, and the safety check still looks at every action.
Outside    a teammate agent gets the piece through its own ask/run/research tool and its answer comes back the same way.

Ideas from Replit Agent 4's harness (the core loop picks tier and effort per dispatch; delegation is optional, never
forced), shrunk to what a local model can handle.
"""
import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.team")
FILE = DATA_DIR / "team.json"
TIERS = ("small", "standard", "large")
SUB_STEPS = 12
CLOUD_ORDER = ("anthropic", "openai", "gemini", "openrouter", "fireworks", "groq", "custom")
AGENT_TOOL_RE = re.compile(r"(?:^|_)(?:ask|chat|query|research|run_task|start_task|delegate|prompt|agent|task|message)(?:_|$)", re.I)


# ------------------------------------------------------------------ settings
def load() -> Dict[str, Any]:
    try:
        s = json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    return {"enabled": s.get("enabled", True), "tiers": s.get("tiers") or {}, "agents": s.get("agents") or [],
            "max_parallel": int(s.get("max_parallel") or 3)}


def save(**changes: Any) -> Dict[str, Any]:
    s = load()
    if "enabled" in changes:
        s["enabled"] = bool(changes["enabled"])
    if isinstance(changes.get("tiers"), dict):
        for t, v in changes["tiers"].items():
            if t in TIERS:
                s["tiers"][t] = v if (v is None or (isinstance(v, dict) and v.get("model"))) else None
    if isinstance(changes.get("agents"), list):
        s["agents"] = [str(a) for a in changes["agents"]][:10]
    if changes.get("max_parallel"):
        s["max_parallel"] = max(1, min(6, int(changes["max_parallel"])))
    FILE.write_text(json.dumps(s, indent=1), encoding="utf-8")
    return s


# ------------------------------------------------------------------ brains
def _bid(provider: str, model: str) -> str:
    return f"{provider}:{model}"


async def brains() -> List[Dict[str, Any]]:
    """Every model that can work as a teammate (calls tools, and fits if it's on this computer)."""
    from app.services import model_hub
    try:
        rows = (await model_hub.all_models())["models"]
    except Exception as e:
        logger.warning(f"brains: {e}")
        rows = []
    out = []
    for r in rows:
        if r.get("tools") is False or r.get("fits") is False or not r.get("online", True):
            continue
        out.append({"id": _bid(r["provider"], r["model"]), "kind": "model", "provider": r["provider"], "model": r["model"],
                    "base_url": r.get("base_url") or "", "where": r.get("where"), "size_gb": r.get("size_gb"),
                    "score": (r.get("score") or {}).get("score"), "in_use": r.get("in_use") or [],
                    "thinking": bool(r.get("thinking")), "vision": bool(r.get("vision"))})
    return out


def outside_agents() -> List[Dict[str, Any]]:
    """Connected MCP servers with a tool an agent would have (ask, run_task, research…): candidates to be teammates."""
    try:
        from app.services.mcp_client import mcp_hub
    except Exception:
        return []
    chosen = set(load()["agents"])
    out = []
    for name, c in mcp_hub.conns.items():
        if not c.client:
            continue
        tool = next((t for t in c.tools if AGENT_TOOL_RE.search(t.name or "")), None)
        if tool:
            out.append({"id": f"agent:{name}", "kind": "agent", "server": name, "tool": tool.name,
                        "description": (tool.description or "")[:160], "teammate": name in chosen})
    return out


def _local(b: Dict[str, Any]) -> bool:
    return b.get("where") == "this computer"


async def tiers() -> Dict[str, Dict[str, Any]]:
    """Which brain each tier uses now: the one set by hand if it's still here, else the automatic pick."""
    from app.config import settings
    bs = await brains()
    by_id = {b["id"]: b for b in bs}
    chat = next((b for b in bs if "chat" in b["in_use"]), None) or {
        "id": _bid(settings.llm_provider, settings.llm_model), "kind": "model", "provider": settings.llm_provider,
        "model": settings.llm_model, "base_url": "", "where": "this computer" if settings.llm_provider in ("ollama", "lm_studio", "xod", "local") else "online",
        "size_gb": None, "score": None, "in_use": ["chat"]}
    locals_ = [b for b in bs if _local(b) and b["size_gb"]]
    # Small: a lighter local model that answers straight away. A thinking model spent a subagent's whole budget thinking
    # and handed back nothing (qwen3-vl:4b), and the seeing model has its own job.
    small = min((b for b in locals_ if (b["size_gb"] or 99) < (chat.get("size_gb") or 99) and not b.get("thinking")
                 and "seeing" not in (b.get("in_use") or [])), key=lambda b: b["size_gb"], default=None)
    clouds = sorted((b for b in bs if b["where"] == "online"), key=lambda b: CLOUD_ORDER.index(b["provider"]) if b["provider"] in CLOUD_ORDER else 99)
    large = clouds[0] if clouds else max(locals_, key=lambda b: ((b["score"] or 0), b["size_gb"] or 0), default=None)
    auto = {"small": small or chat, "standard": chat, "large": large or chat}
    out = {}
    manual = load()["tiers"]
    for t in TIERS:
        m = manual.get(t)
        pick = by_id.get(_bid(m["provider"], m["model"])) if isinstance(m, dict) else None
        out[t] = {**(pick or auto[t]), "auto": pick is None}
    return out


def endpoint(b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What llm_service needs to call this brain (None: the chat model, as configured)."""
    from app.config import settings
    from app.services.llm import llm_service
    if b.get("provider") == settings.llm_provider and b.get("model") == settings.llm_model:
        return None
    if b.get("where") == "online" and not b.get("base_url"):
        return llm_service._build_endpoint(b["provider"], b["model"])
    from app.services import model_sources
    base = (b.get("base_url") or "").rstrip("/")
    if b["provider"] == "ollama" and base and not base.endswith("/v1"):
        base += "/v1"
    return {"provider": b["provider"], "base_url": base or llm_service._build_endpoint(b["provider"], b["model"])["base_url"],
            "model": b["model"], "api_key": model_sources.key_for(base) or ("ollama" if b["provider"] == "ollama" else "local")}


# ------------------------------------------------------------------ handing off
DELEGATE_TOOL = {"type": "function", "function": {
    "name": "team_delegate",
    "description": "Hand a self-contained piece of this job to a subagent, who works on it with the computer tools and reports "
                   "back. Use tier small for quick lookups or mechanical edits, large for hard reasoning or a stubborn bug. "
                   "Several team_delegate calls in one reply run at the same time. The subagent can't see this conversation: "
                   "give it everything it needs (paths, names, what done looks like). Use agent to ask an outside teammate.",
    "parameters": {"type": "object", "properties": {
        "task": {"type": "string", "description": "The complete instruction for the subagent."},
        "tier": {"type": "string", "enum": list(TIERS), "description": "small, standard or large (default standard)."},
        "agent": {"type": "string", "description": "An outside teammate's name instead of a model (optional)."}},
        "required": ["task"]}}}

SUB_SYSTEM = """You are a subagent of Xod, working on one piece of a bigger job on this person's computer. Do it yourself
with the tools, one step at a time, reading each result. Don't send, buy, sign up or delete anything that the piece
doesn't clearly ask for. When it's done (or you're truly stuck), reply with a short report: what you did, what you
found, and the proof it works (an output you saw). It's {now}.
{shell}"""


async def team_on() -> bool:
    """Worth offering team_delegate: more than one brain, or a teammate agent."""
    if not load()["enabled"]:
        return False
    t = await tiers()
    return len({v["id"] for v in t.values()}) > 1 or any(a["teammate"] for a in outside_agents())


async def run_subagent(label: str, task: str, tier: str, execute_tool: Callable, emit: Callable[[Dict[str, Any]], None],
                       agent_name: Optional[str] = None) -> Dict[str, Any]:
    """One subagent's whole run. emit() gets its live events (tool calls and results) for the chat."""
    from app.services import agent_log
    agent_log.agent.set(label)               # this task's own context: its steps are logged as this subagent's
    from app.services import usage as usage_log
    usage_log.purpose.set("subagent")
    started = time.time()
    if agent_name:
        return await _ask_outside(label, agent_name, task, execute_tool, emit, started)
    t = (await tiers()).get(tier if tier in TIERS else "standard")
    ep = endpoint(t)
    brain = f"{t['model']} ({t['provider']})"
    emit({"type": "subagent_status", "agent_id": label, "status": "running", "message": f"{tier} · {brain}", "tier": "local" if _local(t) else "cloud",
          "model": t["model"]})
    from app.agent import worker
    from app.agent.tools import TOOL_DEFINITIONS
    from app import platform_info
    tools = [d for d in TOOL_DEFINITIONS if d["function"]["name"] in worker.TOOLS]
    allowed = {d["function"]["name"] for d in tools}
    convo: List[Dict[str, Any]] = [
        {"role": "system", "content": SUB_SYSTEM.format(now=time.strftime("%A %B %d, %Y, %I:%M %p"),
                                                         shell=(worker.SHELL_WIN + "\n" + platform_info.SHELL_NOTE) if worker.WIN else worker.SHELL_MAC)},
        {"role": "user", "content": task}]
    log: List[Dict[str, Any]] = []
    told: Dict[str, int] = {}
    report = ""
    nudged = swapped = False
    from app.services.llm import llm_service
    for step in range(SUB_STEPS):
        hints = worker.guidance(log, "", told)
        for e in log:
            e["new"] = False
        calls, content, err = [], "", ""
        async for ev in llm_service.deliberate_stream(messages=convo + ([{"role": "user", "content": "(Note from Xod) " + " ".join(hints)}] if hints else []),
                                                      tools=tools, tool_choice="auto", temperature=0.1, max_tokens=1500, endpoint_override=ep):
            if ev.get("type") == "deliberation_complete":
                calls, content = ev.get("tool_calls") or [], ev.get("content") or ""
            elif ev.get("type") == "error":
                err = str(ev.get("content") or "the model stopped")
        if err:
            report = f"Stopped: {err}"
            break
        calls = calls or worker.calls_in_text(content, allowed)
        if not calls and not content.strip() and not log:
            # Nothing at all: nudge once, then let the standard brain take this piece over.
            if not nudged:
                nudged = True
                convo.append({"role": "user", "content": "(Note from Xod) You haven't done anything yet. Use the tools now."})
                continue
            if not swapped and tier != "standard":
                swapped, nudged = True, False
                t = (await tiers())["standard"]
                ep, brain = endpoint(t), f"{t['model']} ({t['provider']}, took over)"
                emit({"type": "subagent_status", "agent_id": label, "status": "running", "message": brain})
                continue
        if not calls:
            report = content.strip() or "It gave no answer."
            break
        convo.append({"role": "assistant", "content": content or "", "tool_calls": calls})
        for i, tc in enumerate(calls[:3]):
            name = tc.get("function", {}).get("name", "")
            try:
                args = json.loads(tc.get("function", {}).get("arguments") or "{}")
            except ValueError:
                args = {}
            cid = f"{label}_{step}_{i}"
            if name not in allowed:
                res: Any = {"success": False, "error": f"{name} isn't one of your tools."}
            else:
                emit({"type": "subagent_tool_call", "agent_id": label, "name": name, "args": args, "id": cid})
                res = worker.refused(name, args) or await execute_tool(name, args)
                emit({"type": "subagent_tool_result", "agent_id": label, "name": name, "id": cid, "summary": worker._summary(name, res), "data": res})
            log.append({"name": name, "args": args, "fp": worker._fp(name, args), "failed": worker._failed(res), "out": worker._out(res)[-2000:],
                        "exit": res.get("exit_code") if isinstance(res, dict) else None, "new": True})
            convo.append({"role": "tool", "tool_call_id": tc.get("id") or cid, "name": name, "content": json.dumps(res, default=str)[:3000]})
            if isinstance(res, dict) and res.get("needs_approval"):
                report = f"Stopped at a step that needs the person's OK: {res.get('message') or name}"
                break
        if report:
            break
        worker._shrink(convo)
    else:
        report = report or f"Ran out of steps after {len(log)} actions; last: {log[-1]['name'] if log else 'none'}."
    ok = bool(report) and not report.startswith(("Stopped", "Ran out"))
    emit({"type": "subagent_status", "agent_id": label, "status": "completed" if ok else "partial", "message": brain,
          "findings": report[:1500], "duration_ms": int((time.time() - started) * 1000)})
    return {"success": ok, "agent": label, "brain": brain, "tier": tier, "summary": report[:3000], "steps": len(log)}


async def _ask_outside(label, name, task, execute_tool, emit, started) -> Dict[str, Any]:
    agent = next((a for a in outside_agents() if a["server"] == name or a["id"] == name), None)
    if not agent:
        return {"success": False, "agent": label, "summary": "", "error": f"No connected teammate called {name}."}
    emit({"type": "subagent_status", "agent_id": label, "status": "running", "message": f"asking {name}", "tier": "cloud", "model": name})
    from app.services.mcp_client import mcp_hub
    _, t = mcp_hub._find(f"mcp__{name}__{agent['tool']}")
    schema = (getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}) if t else {}
    props = schema.get("properties") or {}
    key = next((k for k in (schema.get("required") or []) if (props.get(k) or {}).get("type") == "string"), None) or \
        next((k for k in ("prompt", "task", "query", "question", "message", "request", "input", "text") if k in props), "prompt")
    tool = f"mcp__{name}__{agent['tool']}"
    emit({"type": "subagent_tool_call", "agent_id": label, "name": tool, "args": {key: task[:200]}, "id": f"{label}_0"})
    res = await execute_tool(tool, {key: task})
    emit({"type": "subagent_tool_result", "agent_id": label, "name": tool, "id": f"{label}_0", "summary": str((res or {}).get("content") or "")[:120], "data": res})
    text = str((res or {}).get("content") or (res or {}).get("error") or "")
    ok = bool(res and res.get("success"))
    emit({"type": "subagent_status", "agent_id": label, "status": "completed" if ok else "partial", "message": name,
          "findings": text[:1500], "duration_ms": int((time.time() - started) * 1000)})
    return {"success": ok, "agent": label, "brain": name, "summary": text[:3000], "needs_approval": bool(res and res.get("needs_approval"))}


async def delegate_many(jobs: List[Dict[str, Any]], execute_tool: Callable, start_index: int = 1):
    """Runs handed-off pieces at the same time (at most max_parallel at once) and streams their events.
    Yields chat events, then {"type": "_results", "results": [...]} in the order given."""
    queue: asyncio.Queue = asyncio.Queue()
    sem = asyncio.Semaphore(load()["max_parallel"])
    labels = [f"sub{start_index + i}" for i in range(len(jobs))]
    t = await tiers()
    yield {"type": "swarm_plan", "title": f"Team: {len(jobs)} subagent{'s' if len(jobs) != 1 else ''} at work", "conductor": "Xod",
           "subagents": [{"id": lb, "name": f"Subagent {lb[3:].zfill(2)}", "role": str(j.get("task"))[:90],
                          "tier": "cloud" if j.get("agent") or t.get(j.get("tier") or "standard", {}).get("where") == "online" else "local",
                          "tools": [j.get("agent") or (t.get(j.get("tier") or "standard") or {}).get("model", "")], "icon": "bot"}
                         for lb, j in zip(labels, jobs)]}

    async def one(lb, j):
        async with sem:
            try:
                return await run_subagent(lb, str(j.get("task") or ""), str(j.get("tier") or "standard"), execute_tool,
                                          queue.put_nowait, j.get("agent"))
            except Exception as e:
                logger.exception("subagent failed")
                return {"success": False, "agent": lb, "summary": "", "error": f"{type(e).__name__}: {e}"}

    tasks = [asyncio.create_task(one(lb, j)) for lb, j in zip(labels, jobs)]
    pending = set(tasks)
    while pending:
        getter = asyncio.create_task(queue.get())
        done, _ = await asyncio.wait(pending | {getter}, return_when=asyncio.FIRST_COMPLETED)
        if getter in done:
            yield getter.result()
        else:
            getter.cancel()
        pending = {x for x in pending if not x.done()}
    while not queue.empty():
        yield queue.get_nowait()
    results = [x.result() for x in tasks]
    yield {"type": "swarm_complete", "total_subagents": len(results), "results": [
        {"agent_id": r["agent"], "status": "completed" if r.get("success") else "partial", "findings": (r.get("summary") or r.get("error") or "")[:600]}
        for r in results]}
    yield {"type": "_results", "results": results}
