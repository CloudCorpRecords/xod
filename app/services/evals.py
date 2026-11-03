"""
Measuring whether Xod does what was asked, replayable: a set of real requests (app/evals/*.json) put through the same two
decisions a chat turn makes, with the model Xod is set to use, and nothing run:

  route   the quick router (when a request may take the fast lane): answer it, search, or hand it to the planner
  plan    the planner's steps: which tools, with which arguments

Each case says what it wants as an ability (a Crew job, a quick question, setting an agent up …), not a tool name, so
the same cases score any version of Xod's tools, and runs can be compared (before and after a change). Results are
kept in DATA_DIR/evals; the Scorecard shows the latest and how it moved.

Nothing a case asks for happens: only the router and the planner are called. Their answers aren't kept as training
examples (they'd teach the small models from test requests), and the computer each case sees is a fixed one (Claude
Code and Muse installed and signed in), so results don't depend on this machine.

  .venv/bin/python -m app.services.evals [--suite agent_routing] [--only id,id] [--repeat 1]
"""
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR

ROOT = Path(__file__).resolve().parent.parent.parent
SUITES = Path(__file__).resolve().parent.parent / "evals"      # app/evals: it ships with the app
RESULTS = DATA_DIR / "evals"

AGENT_RE = re.compile(r"\b(muse|claude?\s*code|claude|codex|gemini(?:\s*cli)?|aider|opencode|goose|cursor[- ]?agent|amp)\b", re.I)
WEB_TOOLS = {"web_search", "tavily_search", "you_search", "web_scrape"}
SHELL_TOOLS = {"computer_run_command", "computer_start_job", "terminal_send"}


def _agent_key(name: Any) -> str:
    n = str(name or "").strip().lower()
    n = re.sub(r"[\s_-]+", " ", n)
    if n.startswith("clau"):
        return "claude"
    for k in ("muse", "codex", "gemini", "aider", "opencode", "goose", "cursor", "amp"):
        if n.startswith(k):
            return k
    return n


def ability(tool: str, args: Dict[str, Any]) -> str:
    """What a planned call does, in the cases' words. Knows the tool names of every version."""
    args = args if isinstance(args, dict) else {}
    act = str(args.get("action") or "").lower()
    if tool.startswith("mcp__"):
        return tool.split("__", 2)[-1]          # a connected app's tool: its own name (messages_send, places_nearby …)
    if tool in ("crew_start",):
        return "job"
    if tool in ("crew_status",):
        return "job_status"
    if tool in ("crew_message",):
        return "job_message"
    if tool in ("crew_stop",):
        return "job_status"
    if tool == "agent_job":
        return {"": "job", "start": "job", "status": "job_status", "list": "job_status", "wait": "job_status",
                "message": "job_message", "steer": "job_message", "interrupt": "job_status", "stop": "job_status",
                "review": "job_status", "merge": "job_status", "discard": "job_status", "pr": "job_status"}.get(act, "job")
    if tool in ("cli_agent_chat", "cli_agent_run", "agent_ask"):
        return "ask"
    if tool in ("cli_agent_session", "agent_terminal"):
        return "terminal"
    if tool in ("cli_agent_install", "cli_agent_setup"):
        return "list" if tool == "cli_agent_setup" and act == "status" else "setup"
    if tool == "cli_agent_login":
        return "login"
    if tool == "cli_agents":
        return "list"
    if tool == "agent_setup":
        return {"": "list", "list": "list", "status": "list", "login": "login", "code": "login"}.get(act, "setup")
    if tool in WEB_TOOLS:
        return "web"
    if tool in SHELL_TOOLS and AGENT_RE.search(json.dumps(args)):
        return "shell_agent"          # an agent driven through the plain shell: what the agent tools are for
    return "other"


def tool_ability(tool: str, args: Dict[str, Any]) -> str:
    """For a suite scored by tool (by_tool): the tool itself, with its action (tailscale_files.send), so a case can
    name exactly what should come first. Driving an agent through the shell still shows as shell_agent."""
    args = args if isinstance(args, dict) else {}
    if tool.startswith("mcp__"):
        return tool.split("__", 2)[-1]
    if tool in SHELL_TOOLS and AGENT_RE.search(json.dumps(args)):
        return "shell_agent"
    act = str(args.get("action") or "").lower()
    return f"{tool}.{act}" if act else tool


def _agents_named(tool: str, args: Dict[str, Any], query: str) -> List[str]:
    args = args if isinstance(args, dict) else {}
    named = [args.get("agent")] + [j.get("agent") for j in args.get("jobs") or [] if isinstance(j, dict)]
    named = [_agent_key(a) for a in named if a]
    if named:
        return named
    if ability(tool, args) == "job":
        from_q = AGENT_RE.search(query or "")
        return [_agent_key(from_q.group(1)) if from_q else "claude"]   # a Crew job's default: Claude Code
    m = AGENT_RE.search(query or "")
    return [_agent_key(m.group(1))] if m else []


def score(case: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    """Whether a decision is what the case wants. decision: {"route": answer|web|plan, "calls": [(tool, args)…]}."""
    want, never = set(case.get("want") or []), set(case.get("never") or [])
    calls = decision.get("calls") or []
    got = [(tool_ability if case.get("by_tool") else ability)(t, a) for t, a in calls]
    if decision.get("route") in ("answer", "web") or not calls:
        got = [decision.get("route") if decision.get("route") in ("answer", "web") else "answer"]
    why = []
    if "shell_agent" in got:
        why.append("drove an agent through the plain shell instead of its tools")
    agentish = [g for g in got if g not in ("other", "web", "answer")] or got
    if not (set(agentish[:1]) & want or (case.get("control") and set(got) & want)):
        why.append(f"wanted {' or '.join(sorted(want))}, got {', '.join(got) or 'nothing'}")
    if never & set(got):
        why.append(f"did what it shouldn't: {', '.join(sorted(never & set(got)))}")
    if case.get("agent") and not why:
        named = [x for (t, a) in calls if ability(t, a) in want for x in _agents_named(t, a, case["q"])]
        if named and case["agent"] not in named:
            why.append(f"named {', '.join(sorted(set(named)))}, not {case['agent']}")
    if case.get("all_agents") and not why:
        named = {x for (t, a) in calls if ability(t, a) == "job" for x in _agents_named(t, a, case["q"])}
        missing = set(case["all_agents"]) - named
        if missing:
            why.append(f"left out {', '.join(sorted(missing))}")
    return {"pass": not why, "why": "; ".join(why), "got": got}


# ------------------------------------------------------------------ the fixed computer each case sees
FIXED_AGENTS = [
    {"id": "claude", "name": "Claude Code", "installed": True, "path": "/Users/you/.local/bin/claude", "signed_in": True},
    {"id": "muse", "name": "Muse (Meta)", "installed": True, "path": "/Users/you/.local/bin/muse", "signed_in": True},
]


def _mac_tools() -> List[Dict[str, Any]]:
    """The This Mac connector's tools as the chat sees them when it's connected, read from the connector itself
    (nothing is started or connected)."""
    try:
        from app.connectors import mac
        tools = asyncio.run(mac.server.list_tools()) if not _loop_running() else []
    except Exception:
        return []
    return [{"type": "function", "function": {"name": f"mcp__mac__{t.name}", "description": f"[mac] {(t.description or t.name)[:400]}",
                                              "parameters": getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or
                                              {"type": "object", "properties": {}}}} for t in tools]


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


class _Fixed:
    """Swaps in the fixed computer (Claude Code and Muse installed, This Mac connected), and keeps the router's and
    planner's answers out of the training data."""

    def __init__(self, mac_tools: Optional[List[Dict[str, Any]]] = None):
        self.mac_tools = mac_tools or []

    def __enter__(self):
        from app.services import agent_clis, dataset_recorder
        from app.services.mcp_client import mcp_hub
        self._saved = (agent_clis.find, agent_clis.sessions, agent_clis.claude_app, dataset_recorder.capture)
        self._defs = mcp_hub.tool_definitions
        if self.mac_tools:
            mcp_hub.tool_definitions = lambda: list(self.mac_tools)
        others = [{"id": k, "name": a["name"], "installed": False, "path": None, "signed_in": None}
                  for k, a in agent_clis.AGENTS.items() if k not in ("claude", "muse")]
        agent_clis.find = lambda: {"success": True, "agents": FIXED_AGENTS + others, "installed": ["claude", "muse"]}
        agent_clis.sessions = lambda: []
        agent_clis.claude_app = lambda: {"installed": True, "account": None}
        dataset_recorder.capture = lambda *a, **k: ""
        return self

    def __exit__(self, *exc):
        from app.services import agent_clis, dataset_recorder
        from app.services.mcp_client import mcp_hub
        agent_clis.find, agent_clis.sessions, agent_clis.claude_app, dataset_recorder.capture = self._saved
        mcp_hub.tool_definitions = self._defs


# ------------------------------------------------------------------ one case
async def _route(agent, query: str) -> str:
    """The fast lane's router, as a chat turn would call it: answer, web or plan (the planner takes it)."""
    from app.services.llm import llm_service
    if not agent._fast_lane_ok(query, False, False, None):
        return "plan"
    if agent._FRESH_RE.search(query) and not agent._PERSONAL_RE.search(query):
        return "web"
    msgs = agent.router_messages(query, [])
    head = ""
    thinks = agent._router_thinks()
    stream = llm_service.stream_chat_completion(messages=msgs, temperature=0.2, max_tokens=2400 if thinks else 200,
                                                think=None if thinks else False, task="router")
    try:
        async for ch in stream:
            if ch.get("type") == "error":
                return "plan"
            if ch.get("type") == "content":
                head += ch.get("content", "")
                if len(head) > 160:
                    break
    finally:
        await stream.aclose()
    norm = re.sub(r"^[\s*`#>_]+", "", head).strip()
    up = norm.upper()
    if up.startswith("SEARCH"):
        return "web"
    if not norm or up.startswith("TOOLS") or norm.startswith(("{", "[")) or agent._CLAIMS_DONE_RE.search(norm[:200]):
        return "plan"
    return "answer"


async def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
    from app.agent.researcher import AgenticResearcher
    from app.services.agent_clis import context_note
    agent = AgenticResearcher()
    q = case["q"]
    t0 = time.time()
    route = await _route(agent, q)
    calls: List[Any] = []
    plan: Dict[str, Any] = {}
    if route == "plan":
        note = context_note(q)
        async for ev in agent.deliberate_and_plan(query=q + (f"\n\n{note}" if note else "")):
            if ev.get("type") == "deliberation_complete":
                plan = ev.get("deliberation") or {}
        if plan.get("decision") == "direct_answer":
            route = "answer"
        for st in plan.get("plan_steps") or []:
            args = st.get("tool_args") or []
            for i, t in enumerate(st.get("tools") or []):
                calls.append((agent._canonicalize_tool_name(t), args[i] if i < len(args) and isinstance(args[i], dict) else {}))
    s = score(case, {"route": route, "calls": calls})
    return {"id": case["id"], "q": q, **s, "route": route, "seconds": round(time.time() - t0, 1),
            "calls": [{"tool": t, "args": a} for t, a in calls][:6],
            "said": (plan.get("direct_response") or "")[:300]}


# ------------------------------------------------------------------ a run
def suites() -> List[str]:
    return sorted(p.stem for p in SUITES.glob("*.json"))


def suite(name: str = "agent_routing") -> Dict[str, Any]:
    return json.loads((SUITES / f"{name}.json").read_text())


def _tools_version() -> str:
    """Which agent tools the chat had for this run, so runs before and after a change are told apart."""
    import hashlib
    from app.agent import tools
    names = sorted(t["function"]["name"] for t in tools.TOOL_DEFINITIONS if t["function"]["name"] in tools.CLI_TOOLS)
    return f"{len(names)} agent tools ({', '.join(names)}) #{hashlib.sha1(','.join(names).encode()).hexdigest()[:6]}"


_running: Dict[str, Any] = {"run": None}


async def run(name: str = "agent_routing", only: Optional[List[str]] = None, repeat: int = 1, label: str = "",
              progress=None) -> Dict[str, Any]:
    """Runs a suite with the model Xod is set to use. repeat > 1 runs each case again (models aren't deterministic)."""
    from app.config import settings
    by_tool = bool(suite(name).get("by_tool"))
    cases = [{**c, "by_tool": by_tool} for c in suite(name)["cases"] if not only or c["id"] in only]
    out: List[Dict[str, Any]] = []
    started = time.time()
    _running["run"] = {"suite": name, "done": 0, "total": len(cases) * repeat, "started": started}
    mac_tools = await asyncio.to_thread(_mac_tools) if suite(name).get("mac_connected") else []
    with _Fixed(mac_tools):
        for c in cases:
            for _ in range(max(1, repeat)):
                try:
                    r = await run_case(c)
                except Exception as e:
                    r = {"id": c["id"], "q": c["q"], "pass": False, "why": f"error: {e}", "got": [], "route": "", "calls": []}
                r["control"] = bool(c.get("control"))
                out.append(r)
                _running["run"]["done"] += 1
                if progress:
                    progress(r)
    _running["run"] = None
    agent_rows = [r for r in out if not r["control"]]
    res = {"suite": name, "label": label, "at": started, "seconds": round(time.time() - started, 1),
           "model": f"{settings.llm_provider} / {settings.llm_model}", "tools": _tools_version(),
           "passed": sum(r["pass"] for r in out), "total": len(out),
           "agent_passed": sum(r["pass"] for r in agent_rows), "agent_total": len(agent_rows), "partial": bool(only),
           "repeat": max(1, repeat), "results": out}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{name}-{int(started)}.json").write_text(json.dumps(res, indent=1))
    return res


def history(name: str = "agent_routing", n: int = 10) -> List[Dict[str, Any]]:
    """The latest runs, newest first, without their per-case rows."""
    out = []
    for p in sorted(RESULTS.glob(f"{name}-*.json"), reverse=True)[:n]:
        try:
            r = json.loads(p.read_text())
            r.setdefault("partial", r.get("total", 0) < len(suite(name)["cases"]))
            out.append({k: v for k, v in r.items() if k != "results"} | {"file": p.name})
        except (OSError, ValueError):
            pass
    return out


def latest(name: str = "agent_routing") -> Optional[Dict[str, Any]]:
    """The latest full run (one of only some cases doesn't stand for the whole suite)."""
    for p in sorted(RESULTS.glob(f"{name}-*.json"), reverse=True):
        try:
            r = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        size = len(suite(name)["cases"]) * max(1, r.get("repeat") or 1)
        if not r.get("partial", r.get("total", 0) < size):
            return r
    return None


def status() -> Optional[Dict[str, Any]]:
    return _running["run"]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="agent_routing")
    ap.add_argument("--only", default="")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--label", default="")
    o = ap.parse_args()

    def show(r):
        print(f"  {'✓' if r['pass'] else '✗'} {r['id']:<18} {r['route']:<6} {','.join(r.get('got') or []):<22} {r.get('why') or ''}", flush=True)
    res = asyncio.run(run(o.suite, [x for x in o.only.split(",") if x] or None, o.repeat, o.label, show))
    print(f"\n{res['passed']}/{res['total']} passed (agent requests {res['agent_passed']}/{res['agent_total']}) "
          f"with {res['model']} in {res['seconds']} s\n{res['tools']}")
