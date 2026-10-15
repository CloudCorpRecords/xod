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
import sys
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

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
    # The other one-click connectors (services/connectors.py): words that mean them. Only connected ones have tools.
    *[(re.compile(rf"\b({words})\b", re.I), (server, "")) for words, server in (
        (r"jira|confluence|atlassian", "atlassian"), (r"todoist|tasks? list", "todoist"), (r"clickup", "clickup"),
        (r"monday(\.com)?|boards?", "monday"), (r"airtable|bases?", "airtable"), (r"zapier|zaps?", "zapier"),
        (r"dropbox", "dropbox"), (r"canva|designs?", "canva"), (r"webflow", "webflow"), (r"wix", "wix"),
        (r"stripe|payments?|invoices?|subscriptions?|customers?", "stripe"), (r"paypal", "paypal"),
        (r"intercom|support conversations?", "intercom"), (r"sentry|errors?|exceptions?|crash(es)?", "sentry"),
        (r"vercel|deploy(ment)?s?", "vercel"), (r"netlify", "netlify"), (r"supabase", "supabase"), (r"neon", "neon"),
        (r"prisma", "prisma"), (r"cloudflare|dns|workers?", "cloudflare"), (r"hugging ?face|datasets?|spaces?", "huggingface"),
        (r"microsoft (learn|docs)|powershell docs|azure docs", "mslearn"), (r"context7|library docs|api docs", "context7"),
        (r"deepwiki", "deepwiki"), (r"cloudflare docs", "cloudflare-docs"))],
]
VERBS_RE = re.compile(r"\b(add|create|make|new|put|schedule|book|set up|update|change|edit|rename|move|reschedule|push|postpone|"
                      r"delete|remove|cancel|clear|complete|finish|check off|tick off|mark|archive|close|reopen|flag|list|show|find|"
                      r"search|look up|read|open|what'?s|what is|what are|do i have|when is|any|how many)\b", re.I)
NOT_MINE_RE = re.compile(r"\b(how (do|does|to)|explain|what is a|history of|write (me )?(a|an) (essay|poem|story))\b", re.I)
CHANGE_RE = re.compile(r"(update|edit|change|move|delete|remove|complete|archive|close|set_)", re.I)   # tools that change an item
# A request to change something, and a reply that talks as if it's done or waiting for an OK (see the nudge in stream()).
ASKS_CHANGE_RE = re.compile(r"\b(add|create|make|put|schedule|book|delete|remove|cancel|check off|tick off|complete|finish|mark|move|"
                            r"reschedule|push|change|rename|update|edit|set)\b", re.I)
SOUNDS_DONE_RE = re.compile(r"\b(ready to|press \*?\*?approve|waiting for your ok|i'?ve (added|deleted|removed|moved|created|updated|checked|marked|"
                            r"scheduled|changed)|i have (added|deleted|removed|moved|created|updated|checked|marked|scheduled|changed)|"
                            r"(has|have) been (added|deleted|removed|moved|created|updated|checked off|marked|scheduled|changed)|done\b)", re.I)
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


_win_apps_cache: Dict[str, Any] = {"at": 0.0, "apps": {}}


def _windows_apps() -> Dict[str, Tuple[str, str]]:
    """Start menu apps (Store apps too): lowercase name -> (name, AppID for `shell:AppsFolder\\<id>`). Cached 10 min."""
    if time.time() - _win_apps_cache["at"] < 600:
        return _win_apps_cache["apps"]
    found: Dict[str, Tuple[str, str]] = {}
    try:
        from app.services.system_control_win import _ps_sync
        r = _ps_sync("[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-StartApps | ForEach-Object { $_.Name + \"`t\" + $_.AppID }", timeout=20)
        for line in r["out"].splitlines():
            name, _, app_id = line.strip().partition("\t")
            if name and app_id:
                found.setdefault(name.lower(), (name, app_id))
    except Exception as e:
        logger.debug(f"Start menu apps: {e}")
    _win_apps_cache.update(at=time.time(), apps=found)
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
    if sys.platform == "win32":
        apps = _windows_apps()
        key = name if name in apps else next((n for n in apps if n.startswith(name) and len(name) >= 4), None)
        if key:
            title, app_id = apps[key]
            return {"kind": "windows", "title": title, "path": app_id}
        return None
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
    # The app's words (DOMAINS) or its name: only connected apps have tools, so "boards" alone does nothing without monday.com.
    tools = tools_for(q)
    if not tools:
        return None
    return {"mode": "apps", "tools": tools}


# ---------------------------------------------------------------- running

def _items(res: Any) -> Optional[List[Any]]:
    """The list a list/search tool returned, or None if it isn't one. MCP tools hand it back as structured data
    ({"result": [...]}), as one JSON array, or as one JSON object per line (one text block per item)."""
    if not isinstance(res, dict) or res.get("error"):
        return None
    data = res.get("data")
    if isinstance(data, dict) and isinstance(data.get("result"), list):
        return data["result"]
    if isinstance(data, list):
        return data
    text = str(res.get("content") or "").strip()
    if not text:
        return None
    try:
        v = json.loads(text)
        return v if isinstance(v, list) else [v] if isinstance(v, dict) else None
    except ValueError:
        pass
    # Several JSON objects one after another (each may span lines: they're pretty-printed).
    dec, out, i = json.JSONDecoder(), [], 0
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        try:
            v, i = dec.raw_decode(text, i)
        except ValueError:
            return None
        if not isinstance(v, dict):
            return None
        out.append(v)
    return out


def _remember_items(res: Any, seen: Dict[str, Dict[str, Any]]) -> None:
    """Items a list or search returned this turn, by id (to fix ids a model retypes wrong, see _fix_ids)."""
    items = _items(res)
    for i in items or []:
        if isinstance(i, dict) and isinstance(i.get("id"), str) and i["id"]:
            seen[i["id"]] = i


def _fix_ids(args: Dict[str, Any], seen: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """A small model copies ids badly: "1dcccf5b" for "x-apple-reminder://1dcccf5b", a typo, or the title instead of the
    id. When an id argument isn't one the lists just returned but plainly means one of them, use the real one."""
    if not seen:
        return args
    import difflib
    out = dict(args)
    for k, v in args.items():
        if not (k == "id" or k.endswith("_id")) or not isinstance(v, str) or not v or v in seen:
            continue
        val = v.strip().casefold()
        hits = [i for i in seen if i.casefold().endswith(val) or val.endswith(i.casefold())] if len(val) >= 4 else []
        if not hits:
            hits = [i for i, item in seen.items() if str(item.get("title") or item.get("name") or "").strip().casefold() == val]
        if not hits:
            hits = difflib.get_close_matches(v, list(seen), n=2, cutoff=0.85)
        if len(hits) == 1:
            out[k] = hits[0]
    return out


def _lister_for(name: str, allowed: set) -> Optional[str]:
    """The tool that lists what a change tool works on: mcp__mac__reminders_update → mcp__mac__reminders_list,
    mcp__mac__calendar_update_event → mcp__mac__calendar_events."""
    prefix, _, tool = name.rpartition("__")
    noun = tool.split("_", 1)[0]
    for cand in (f"{prefix}__{noun}_list", f"{prefix}__{noun}_events", f"{prefix}__{noun}_search", f"{prefix}__list_{noun}"):
        if cand in allowed:
            return cand
    return next((t for t in sorted(allowed) if t.startswith(f"{prefix}__{noun}_") and re.search(r"_(list|search|events)$", t)), None)


_STOP = {"the", "and", "for", "with", "from", "this", "that", "my", "your", "list", "reminder", "reminders", "event", "events",
         "appointment", "meeting", "personal", "default", "today", "tomorrow"}


def _named_in(query: str, seen: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """The one item whose title shares a real word with the request ("move my dentist appointment" → "Dentist")."""
    words = {w for w in re.findall(r"[a-z0-9']{3,}", (query or "").lower()) if w not in _STOP}
    hits = [i for i, it in seen.items()
            if words & {w for w in re.findall(r"[a-z0-9']{3,}", str(it.get("title") or it.get("name") or "").lower()) if w not in _STOP}]
    return hits[0] if len(hits) == 1 else None


def _id_keys(name: str, args: Dict[str, Any]) -> List[str]:
    return [k for k, v in args.items() if (k == "id" or k.endswith("_id")) and isinstance(v, str) and v] if CHANGE_RE.search(name) else []


def _needs_lookup(name: str, args: Dict[str, Any], allowed: set, seen: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """The list tool to run first when a change names an id this turn's lists didn't return (a small model made up
    "1234567890" instead of looking)."""
    keys = _id_keys(name, args)
    return _lister_for(name, allowed) if keys and not all(args[k] in seen for k in keys) else None


def _check_ids(name: str, args: Dict[str, Any], seen: Dict[str, Dict[str, Any]], query: str = "") -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """After looking: the real id for a made-up or mistyped one, from the item the request plainly names. Returns
    (args with the real id, or an error listing the real ones so the model can pick)."""
    keys = _id_keys(name, args)
    if not keys:
        return args, None
    args = _fix_ids(args, seen)
    bad = [k for k in keys if args.get(k) not in seen]
    if not bad or not seen:
        return args, None
    named = _named_in(query, seen)
    if named and len(bad) == 1:
        return {**args, bad[0]: named}, None
    choices = [{"id": i, "title": it.get("title") or it.get("name"), **({"start": it["start"]} if it.get("start") else {})}
               for i, it in list(seen.items())[:15]]
    return args, {"success": False, "error": f"There's no item with id {args[bad[0]]!r}. Nothing was changed. The real ones are listed "
                                               "here: pick the one the person means and call this again with its exact id.", "items": choices}


async def _already_there(name: str, args: Dict[str, Any], allowed: set, execute_tool) -> Optional[Dict[str, Any]]:
    """Adding something that's already there, open, with the same title ("add the other one too" re-adding the same
    reminder): say so instead of making a duplicate. Uses the app's own list tool (reminders_add → reminders_list)."""
    if not re.search(r"_(add|create)\w*$", name):
        return None
    title = str(args.get("title") or args.get("name") or args.get("summary") or "").strip().casefold()
    lister = re.sub(r"_(add|create)\w*$", "_list", name)
    if not title or lister not in allowed:
        return None
    try:
        items = _items(await execute_tool(lister, {})) or []
    except Exception:
        return None
    same = [i for i in items if isinstance(i, dict) and str(i.get("title") or i.get("name") or "").strip().casefold() == title
            and not i.get("completed")]
    if not same:
        return None
    return {"success": False, "duplicate": True, "existing": same[:3],
            "error": f"There's already an open one called “{same[0].get('title') or same[0].get('name')}”, so nothing was added. "
                     "If the person meant something different, add that instead; if they really want two, ask them first."}


def _found_nothing(res: Any) -> bool:
    """A list or search that came back empty."""
    if not isinstance(res, dict) or res.get("error"):
        return False
    data = res.get("data")
    if isinstance(data, dict) and data.get("result") == [] or data == []:
        return True
    text = str(res.get("content") or "").strip()
    return text in ("", "[]") and ("data" in res or "content" in res)


def _summary(name: str, res: Any) -> str:
    if isinstance(res, dict):
        if res.get("needs_approval"):
            return "Waiting for your OK"
        if res.get("error"):
            return f"✗ {str(res['error'])[:120]}"
        text = str(res.get("content") or "")
        items = _items(res)
        if items is not None and (text.lstrip().startswith(("[", "{")) or res.get("data") is not None):
            return f"{len(items)} found"
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
    from app import platform_info
    convo: List[Dict[str, Any]] = [{"role": "system", "content": platform_info.localize(SYSTEM.format(now=now)) +
                                    ("\nYour reply will be read aloud: one short sentence." if spoken else "")}]
    for m in messages[-6:-1]:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip():
            text = re.sub(r"<!--.*?-->", "", m["content"], flags=re.S)
            if m["role"] == "assistant":
                # "Ready to …: press Approve on the card above" from an earlier turn got copied word for word, with no
                # tool called: the card it promised never existed. Keep what happened, not the wording.
                text = re.sub(r"Ready to [^.:]*?:? press \*\*Approve\*\* on the card above[^.]*\.?", "(That change waited for the person's OK.)", text)
            convo.append({"role": m["role"], "content": text[:1500]})
    convo.append({"role": "user", "content": query})
    allowed = {t["function"]["name"] for t in tools}
    schemas = {t["function"]["name"]: (t["function"].get("parameters") or {}).get("properties") or {} for t in tools}
    items_seen: Dict[str, Dict[str, Any]] = {}
    nudged = False
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
            if not used and not nudged and ASKS_CHANGE_RE.search(query) and SOUNDS_DONE_RE.search(content or ""):
                # It says the change is done or waiting for an OK, but called nothing: nothing happened. Once, say so.
                nudged = True
                convo.append({"role": "assistant", "content": content or ""})
                convo.append({"role": "user", "content": "You didn't call any tool, so nothing has been done and no approval card exists. "
                                                         "Call the tools now: find the item first if you need its id, then make the change."})
                continue
            final = content.strip()
            break
        convo.append({"role": "assistant", "content": content or "", "tool_calls": calls})
        seen = set()
        for i, tc in enumerate(calls[:4]):
            name = tc.get("function", {}).get("name", "")
            raw = tc.get("function", {}).get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except ValueError:
                args = {}
            cid = tc.get("id") or f"op_{step}_{i}"
            same = (name, json.dumps(args, sort_keys=True, default=str))
            if same in seen:
                # The same call twice in one reply (small models do this) would add the reminder twice.
                res: Any = {"success": True, "note": "Same as the call just above: done once, not again."}
            elif name not in allowed:
                res = {"success": False, "error": f"{name} isn't one of the tools for this."}
            else:
                seen.add(same)
                yield {"type": "tool_call", "name": name, "args": args, "id": cid}
                looked = any(CHANGE_RE.search(u) is None for u in used)
                used.append(name)
                args = _fix_ids(args, items_seen)
                lister = _needs_lookup(name, args, allowed, items_seen)
                if lister:
                    # It changes an item it never looked up (a made-up id): look first, in plain view.
                    largs = {"include_completed": True} if "include_completed" in schemas.get(lister, {}) else {}
                    yield {"type": "tool_call", "name": lister, "args": largs, "id": f"{cid}_find"}
                    lres = await execute_tool(lister, largs)
                    yield {"type": "tool_result", "name": lister, "id": f"{cid}_find", "summary": _summary(lister, lres), "data": lres}
                    _remember_items(lres, items_seen)
                    used.append(lister)
                args, unknown = _check_ids(name, args, items_seen, query)
                dup = unknown or await _already_there(name, args, allowed, execute_tool)
                res = dup or await execute_tool(name, args)
                if _found_nothing(res) and "include_completed" in schemas.get(name, {}) and not args.get("include_completed"):
                    # Lists show open items only: "delete the eggs reminder" after checking it off found nothing.
                    # Look again with the finished ones before telling the person it isn't there.
                    again = await execute_tool(name, {**args, "include_completed": True})
                    if isinstance(again, dict) and not again.get("error"):
                        res = {**again, "note": "Nothing open matched, so this includes finished (checked-off) items too."}
                _remember_items(res, items_seen)
                if isinstance(res, dict) and res.get("error") and CHANGE_RE.search(name) and not looked:
                    # Changed something it hadn't looked up: point it at the way that works, so it tries again.
                    res = {**res, "hint": "Find the item first with a list or search tool to get its exact id, then call this again with that id."}
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
        if target["kind"] == "windows":
            # explorer.exe returns 1 even when it worked, so success is "it started"
            try:
                if os.path.isfile(target["path"]):      # some desktop apps' AppID is just their .exe
                    os.startfile(target["path"])
                else:
                    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target['path']}"])
                r = subprocess.CompletedProcess([], 0, "", "")
            except Exception as e:
                r = subprocess.CompletedProcess([], 1, "", str(e))
        else:
            r = subprocess.run(["open", "-a", target["path"]], capture_output=True, text=True, timeout=20)
        ok = r.returncode == 0
        yield {"type": "tool_result", "name": "computer_open_item", "id": "launch_1", "summary": "Opened" if ok else "Couldn't open it",
               "data": {"success": ok, "error": r.stderr.strip()[:200] if not ok else None}}
        yield {"type": "content", "content": f"Opened **{target['title']}**." if ok else f"I couldn't open {target['title']}: {r.stderr.strip()[:200]}"}
    yield {"type": "done"}
