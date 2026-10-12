"""
Doing things in the user's apps: "move my dentist appointment to Friday", "check off milk",
"delete the note about the old wifi password", "what's on my calendar tomorrow?", "launch Spotify".

The general planner writes every tool call before seeing any result, so it can't do the two-step
work most changes need (find the item to get its id, then change it). The operator is a small
tool-calling loop over just the connected-app tools that fit the request:

  1. the model picks a tool (native function calling), Xod runs it, the result goes back
  2. repeat until it answers (6 steps at most)
  3. anything that changes or deletes waits for the user's OK on a card in the chat (MCP approvals;
     deletes always ask), and the loop stops there and says so

Also here: launching Mac apps by name, and apps Xod built ("launch Neon Dodge").
"""
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.services.llm import llm_service

logger = logging.getLogger("xod.operator")

MAX_STEPS = 6
MARK = "<!-- xod-apps:"
FOLLOW_RE = re.compile(r"^\s*(and |also |now |ok(ay)? |then |actually |no,? |yes,? |yeah,? |please )*(add|create|make|put|update|change|edit|"
                       r"rename|move|delete|remove|cancel|complete|finish|check|tick|mark|do|same|the other|that one|this one|both|all|"
                       r"it|them|those|undo|instead)\b", re.I)

# words in a request -> which tools fit (by connector server and tool-name prefix)
DOMAINS = [
    (re.compile(r"\b(calendar|event|events|meeting|meetings|appointment|appointments|schedule|scheduled|agenda|busy|free (on|at|tomorrow|today)|reschedul\w*)\b", re.I), ("mac", "calendar_")),
    (re.compile(r"\b(reminders?|to-?dos?|todo list|checklist|check (it )?off|remind me)\b", re.I), ("mac", "reminders_")),
    (re.compile(r"\b(notes?|notebook)\b", re.I), ("mac", "notes_")),
    (re.compile(r"\b(e-?mails?|mail|inbox|unread|messages? from)\b", re.I), ("mac", "mail_")),
    (re.compile(r"\bnotion\b", re.I), ("notion", "")),
    (re.compile(r"\b(linear|issues?|tickets?|backlog|sprint)\b", re.I), ("linear", "")),
    (re.compile(r"\b(github|repo|repos|repository|pull requests?|PRs?)\b", re.I), ("github", "")),
]
VERBS_RE = re.compile(r"\b(add|create|make|new|put|schedule|book|set up|update|change|edit|rename|move|reschedule|push|postpone|"
                      r"delete|remove|cancel|clear|complete|finish|check off|tick off|mark|archive|close|reopen|flag|list|show|find|"
                      r"search|look up|read|open|what'?s|what is|what are|do i have|when is|any|how many)\b", re.I)
NOT_MINE_RE = re.compile(r"\b(how (do|does|to)|explain|what is a|history of|write (me )?(a|an) (essay|poem|story))\b", re.I)
LAUNCH_RE = re.compile(r"^\s*(?:please |can you |could you |now |ok(?:ay)? )*(?:launch|open|start|fire up|boot up|run)\s+"
                       r"(?:up\s+)?(?:the\s+|my\s+)?(.{2,40}?)(?:\s+app)?\s*[.!?]*$", re.I)

SYSTEM = """You are Xod, working in the user's own apps for them, on their Mac. Now: {now}.
Use the tools to do what they ask, step by step:
- To change, complete or delete something, first list or search to find it and get its id. Never guess an id.
- Do exactly what was asked. Don't create duplicates. If several items could match, ask which one (list them briefly).
- Creating, changing and deleting may wait for the user's OK in a card in the chat. That's normal: if a tool says
  it's waiting for their OK, stop and tell them it's ready to approve.
- Dates and times for tools look like 2026-10-14T15:00 (local time).
- When finished, reply in one or two plain sentences: what you did or found. Use friendly times ("Tue 3 PM")."""


# ---------------------------------------------------------------- launching apps

def _mac_apps() -> Dict[str, str]:
    found: Dict[str, str] = {}
    for root in ("/Applications", "/System/Applications", "/System/Applications/Utilities", os.path.expanduser("~/Applications"),
                 "/Applications/Utilities"):
        try:
            for name in os.listdir(root):
                if name.endswith(".app"):
                    found.setdefault(name[:-4].lower(), os.path.join(root, name))
        except OSError:
            pass
    return found


def find_launch(query: str) -> Optional[Dict[str, Any]]:
    """'launch Spotify' / 'open Neon Dodge' -> what to open, or None."""
    m = LAUNCH_RE.match(query or "")
    if not m:
        return None
    name = m.group(1).strip().strip("\"'").lower()
    if name in ("it", "this", "that", "the game", "the app", "a new chat", "settings", "tools"):
        return None
    try:   # an app Xod built
        from app.services.project_storage import project_storage_service as ps
        for p in ps.list_projects():
            for a in p.get("apps") or []:
                if name in ((a.get("title") or "").lower(), Path(a["path"]).name.lower()):
                    return {"kind": "built", "title": a["title"], "path": a["path"]}
    except Exception:
        pass
    apps = _mac_apps()
    hit = apps.get(name) or next((p for n, p in apps.items() if n.startswith(name) and len(name) >= 4), None)
    if hit:
        return {"kind": "mac", "title": Path(hit).stem, "path": hit}
    return None


# ---------------------------------------------------------------- routing

def tools_for(query: str) -> List[Dict[str, Any]]:
    from app.services.mcp_client import mcp_hub
    defs = mcp_hub.tool_definitions()
    picked: List[Dict[str, Any]] = []
    for rx, (server, prefix) in DOMAINS:
        if rx.search(query):
            for d in defs:
                n = d["function"]["name"]
                parts = n.split("__", 2)
                if len(parts) == 3 and (parts[1] == server or parts[1].startswith(server)) and parts[2].startswith(prefix) and d not in picked:
                    picked.append(d)
    # A connected server named in the request ("in Notion", "on Todoist") brings its tools too.
    q = (query or "").lower()
    for d in defs:
        server = d["function"]["name"].split("__")[1] if d["function"]["name"].count("__") >= 2 else ""
        if server and len(server) > 3 and re.search(rf"\b{re.escape(server.lower())}\b", q) and d not in picked:
            picked.append(d)
    return picked[:24]


def _last_apps_turn(messages: List[Dict[str, Any]]) -> List[str]:
    """The tool names the previous answer used, if it was the operator's (for follow-ups like "the other one too")."""
    for m in reversed(messages[:-1][-4:]):
        c = m.get("content")
        if m.get("role") == "assistant" and isinstance(c, str):
            if MARK in c:
                try:
                    return json.loads(c.split(MARK, 1)[1].split("-->", 1)[0])
                except ValueError:
                    return []
            return []
    return []


def route(query: str, has_attachment: bool = False, messages: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    q = (query or "").strip()
    if not q or has_attachment or len(q) > 800:
        return None
    launch = find_launch(q)
    if launch:
        return {"mode": "launch", "target": launch}
    prev = _last_apps_turn(messages or [])
    if prev and FOLLOW_RE.search(q) and len(q) < 200:
        from app.services.mcp_client import mcp_hub
        defs = [d for d in mcp_hub.tool_definitions() if d["function"]["name"] in prev or
                any(d["function"]["name"].rsplit("_", 1)[0] == n.rsplit("_", 1)[0] for n in prev)]
        tools = (defs + [d for d in tools_for(q) if d not in defs])[:24]
        if tools:
            return {"mode": "apps", "tools": tools}
    if NOT_MINE_RE.search(q) or not VERBS_RE.search(q):
        return None
    if not any(rx.search(q) for rx, _ in DOMAINS):
        return None
    tools = tools_for(q)
    if not tools:
        return None
    return {"mode": "apps", "tools": tools}


# ---------------------------------------------------------------- running

def _summary(name: str, res: Any) -> str:
    if isinstance(res, dict):
        if res.get("needs_approval"):
            return "Waiting for your OK"
        if res.get("error"):
            return f"✗ {str(res['error'])[:120]}"
        text = str(res.get("content") or "")
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return f"{len(data)} found"
        except ValueError:
            pass
        return text[:120] or "Done"
    return str(res)[:120]


async def stream(query: str, messages: List[Dict[str, Any]], decision: Dict[str, Any], execute_tool,
                 spoken: bool = False) -> AsyncGenerator[Dict[str, Any], None]:
    if decision["mode"] == "launch":
        async for ev in _launch(decision["target"]):
            yield ev
        return
    tools = decision["tools"]
    now = time.strftime("%A %B %d, %Y, %I:%M %p")
    convo: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM.format(now=now) +
                                    ("\nYour reply will be read aloud: one short sentence." if spoken else "")}]
    for m in messages[-6:-1]:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip():
            convo.append({"role": m["role"], "content": re.sub(r"<!--.*?-->", "", m["content"], flags=re.S)[:1500]})
    convo.append({"role": "user", "content": query})
    allowed = {t["function"]["name"] for t in tools}
    yield {"type": "status", "content": "Working in your apps…"}
    waiting = None
    pendings: List[Dict[str, Any]] = []
    used: List[str] = []
    final = ""
    for step in range(MAX_STEPS):
        calls, content = [], ""
        async for ev in llm_service.deliberate_stream(messages=convo, tools=tools, tool_choice="auto", temperature=0.1, max_tokens=1500):
            if ev.get("type") == "deliberation_complete":
                calls, content = ev.get("tool_calls") or [], ev.get("content") or ""
            elif ev.get("type") == "error":
                yield {"type": "content", "content": ev.get("content") or "The model stopped."}
                yield {"type": "done"}
                return
        if not calls:
            final = content.strip()
            break
        convo.append({"role": "assistant", "content": content or "", "tool_calls": calls})
        for i, tc in enumerate(calls[:4]):
            name = tc.get("function", {}).get("name", "")
            raw = tc.get("function", {}).get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except ValueError:
                args = {}
            cid = tc.get("id") or f"op_{step}_{i}"
            if name not in allowed:
                res: Any = {"success": False, "error": f"{name} isn't one of the tools for this."}
            else:
                yield {"type": "tool_call", "name": name, "args": args, "id": cid}
                used.append(name)
                res = await execute_tool(name, args)
                yield {"type": "tool_result", "name": name, "id": cid, "summary": _summary(name, res), "data": res}
            if isinstance(res, dict) and res.get("needs_approval"):
                waiting = res
                pendings.append(res)
            body = json.dumps(res, default=str)[:4000] if not isinstance(res, str) else res[:4000]
            convo.append({"role": "tool", "tool_call_id": cid, "content": body})
        if waiting:
            break
    if waiting:
        pending = [w for w in pendings]
        if len(pending) > 1:
            final = f"Ready: {len(pending)} changes are waiting for your OK on the cards above ({'; '.join(_say(w) for w in pending[:4])})."
        else:
            final = f"Ready to {_say(waiting)}: press **Approve** on the card above and I'll do it."
        if any(w.get("destructive") for w in pending):
            final += " Deleting can't always be undone, so it waits for you."
    if not final:
        final = "I got partway but couldn't finish that. Could you say it another way?"
    yield {"type": "content", "content": final + ("\n\n" + MARK + json.dumps(sorted(set(used))[:12]) + " -->" if used else "")}
    yield {"type": "done"}


NOUNS = {"reminders": "reminder", "calendar": "event", "notes": "note", "mail": "email"}


def _say(w: Dict[str, Any]) -> str:
    """'mcp__mac__reminders_add' + {"title": "Buy eggs"} -> 'add the reminder “Buy eggs”'."""
    tool = (w.get("tool") or "").split("__")[-1]
    area, _, action = tool.partition("_")
    noun = NOUNS.get(area)
    args = w.get("args") or {}
    label = args.get("title") or args.get("name") or args.get("subject") or ""
    if noun:
        verb = {"add": "add", "create": "create", "create_event": "create", "update": "update", "update_event": "change",
                "delete": "delete", "delete_event": "delete", "draft": "write", "mark": "mark"}.get(action, action.replace("_", " "))
        text = f"{verb} the {noun}"
        if args.get("completed") in ("true", True) and action.startswith("update"):
            text = f"check off the {noun}"
    else:
        text = tool.replace("_", " ")
    return text + (f" “{str(label)[:60]}”" if label else "")


async def _launch(target: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
    if target["kind"] == "built":
        from app.services import previews
        d = Path(target["path"])
        yield {"type": "launch_app", "title": target["title"], "url": previews.url_for(d), "root": previews.register(d), "path": str(d)}
        yield {"type": "content", "content": f"**Launched {target['title']}** in its own window."}
    else:
        yield {"type": "tool_call", "name": "computer_open_item", "args": {"target": target["path"]}, "id": "launch_1"}
        r = subprocess.run(["open", "-a", target["path"]], capture_output=True, text=True, timeout=20)
        ok = r.returncode == 0
        yield {"type": "tool_result", "name": "computer_open_item", "id": "launch_1", "summary": "Opened" if ok else "Couldn't open it",
               "data": {"success": ok, "error": r.stderr.strip()[:200] if not ok else None}}
        yield {"type": "content", "content": f"Opened **{target['title']}**." if ok else f"I couldn't open {target['title']}: {r.stderr.strip()[:200]}"}
    yield {"type": "done"}
