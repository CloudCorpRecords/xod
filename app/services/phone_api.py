"""
The phone app's window into this Xod: chat, tasks, routines and goals, memory, reports and project files.

The phone asks over Agent Mail (end-to-end encrypted, like everything else between them): a message with subject
REQUEST_SUBJECT whose text is {"op": ..., "args": {...}, "request_id": ...}. This computer answers on the same thread
with REPLY_SUBJECT and {"request_id", "ok", "data" | "error"}. Neither shows up in Agent Mail's inbox (status 'api').

Who may ask: only a phone paired in phone_mcp's registry whose mail checked out (signature and pinned keys), i.e.
a phone that paired on its own screen and that you can see under Your phone. Anyone else is ignored, not answered.

What it can do is the same as the desktop app (including the Muse approvals switch, muse.*): a chat turn runs the same agent (which still asks before sending,
paying, posting or deleting), a task is a normal task. Files are limited to your projects' own files and Xod's
reports; there is no way to name an arbitrary path on this computer.
"""
import asyncio
import inspect
import json
import logging
import re
import time
from typing import Any, Callable, Dict

logger = logging.getLogger("xod.phone_api")

REQUEST_SUBJECT = "xod api"
REPLY_SUBJECT = "xod api reply"
MAX_REPLY = 18000          # characters of JSON in one answer (Agent Mail carries up to 20000)
CHAT_TIMEOUT = 600.0


class ApiError(Exception):
    pass


def _clip(text: Any, n: int) -> str:
    s = str(text or "")
    return s if len(s) <= n else s[:n] + "…"


def allowed(sender: str) -> bool:
    """A phone paired on its own screen, still pinned as a contact you trust."""
    from app.services import agent_mail, phone_mcp
    sender = (sender or "").lower()
    if not any(p.get("address") == sender for p in phone_mcp.paired_phones()):
        return False
    c = agent_mail.contact(sender)
    return bool(c and c.get("trust") in ("known", "mine"))


# ---------------------------------------------------------------- chat

def _sessions(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.storage import list_sessions
    return {"sessions": [{"id": s["id"], "title": s.get("title") or "Chat", "updated_at": s.get("updated_at"),
                          "message_count": s.get("message_count") or 0, "source": s.get("source") or "desktop"}
                         for s in list_sessions()[:40]]}


def _history(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.storage import get_session, get_session_messages
    sid = str(args.get("session_id") or "")
    if not get_session(sid):
        raise ApiError("That chat isn't on this computer any more.")
    msgs = [m for m in get_session_messages(sid) if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()]
    return {"session_id": sid, "messages": [{"role": m["role"], "content": _clip(m["content"], 4000),
                                             "at": m.get("created_at")} for m in msgs[-30:]]}


async def _chat(args: Dict[str, Any]) -> Dict[str, Any]:
    """One chat turn, exactly as the desktop chat runs it (same agent, same history, saved in the same chat)."""
    from app import main
    from app.services.storage import get_session, get_session_messages
    text = str(args.get("text") or "").strip()
    if not text:
        raise ApiError("Say something first.")
    sid = str(args.get("session_id") or "") or None
    history = []
    if sid and get_session(sid):
        history = [{"role": m["role"], "content": m["content"]} for m in get_session_messages(sid)
                   if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()][-20:]
    resp = await main.chat_endpoint(main.ChatRequest(messages=history + [{"role": "user", "content": text}], session_id=sid,
                                                     source="phone"))
    reply, tools, error = "", [], None
    async for chunk in resp.body_iterator:
        for line in (chunk.decode() if isinstance(chunk, bytes) else chunk).splitlines():
            if not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "session_info":
                sid = ev.get("session_id") or sid
            elif kind == "content":
                reply += ev.get("content") or ""
            elif kind == "tool_call":
                tool = ev.get("tool")
                name = ev.get("name") or (tool.get("name") if isinstance(tool, dict) else tool)
                if name and name not in tools:
                    tools.append(str(name))
            elif kind == "error":
                error = ev.get("content") or "Something went wrong."
    if error and not reply.strip():
        raise ApiError(error)
    from app.services import agent_mail
    return {"session_id": sid, "reply": _clip(agent_mail.re_clean(reply), 12000), "tools": tools[:12]}


# ---------------------------------------------------------------- tasks

def _task(t: Dict[str, Any], full: bool = False) -> Dict[str, Any]:
    detail = t.get("waiting_detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            pass
    out = {k: t.get(k) for k in ("id", "title", "status", "now", "waiting_kind", "created_at", "updated_at", "finished_at", "steps")}
    out["waiting_detail"] = detail if full else (_clip(json.dumps(detail), 300) if detail else None)
    out["result"] = _clip(t.get("result"), 6000 if full else 240)
    if full:
        out["request"] = _clip(t.get("request"), 2000)
    return out


def _tasks(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.task_queue import task_queue
    return {"tasks": [_task(t) for t in task_queue.list(40)], "counts": task_queue.counts()}


def _task_get(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.task_queue import task_queue
    t = task_queue.get(str(args.get("id") or ""))
    if not t:
        raise ApiError("No such task.")
    return {"task": _task(t, full=True)}


def _task_create(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.task_queue import task_queue
    try:
        t = task_queue.create(str(args.get("text") or "").strip())
    except ValueError as e:
        raise ApiError(str(e))
    task_queue._update(t["id"], note="Started from your phone")
    return {"task": _task(t)}


async def _task_act(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.task_queue import task_queue
    tid, action = str(args.get("id") or ""), str(args.get("action") or "")
    if not task_queue.get(tid):
        raise ApiError("No such task.")
    if action == "approve":
        r = task_queue.approve(tid)
    elif action == "answer":
        r = task_queue.answer(tid, str(args.get("text") or ""))
    elif action == "retry":
        r = task_queue.retry(tid)
    elif action == "cancel":
        r = await task_queue.cancel(tid)
    else:
        raise ApiError("Unknown task action.")
    return {"result": r, "task": _task(task_queue.get(tid) or {}, full=True)}


# ---------------------------------------------------------------- routines and goals

def _routines(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import goals, routines
    return {"routines": [{k: r.get(k) for k in ("id", "name", "schedule", "enabled", "next_run_at", "last_run_at",
                                                 "last_status", "runs")} | {"instructions": _clip(r.get("instructions"), 300)}
                         for r in routines.list_all()],
            "goals": [{k: g.get(k) for k in ("id", "title", "outcome", "deadline", "status", "next_check_at")}
                      | {"log": _clip(g.get("log") if isinstance(g.get("log"), str) else json.dumps(g.get("log")), 600)}
                      for g in goals.list_all()]}


def _routine_act(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import routines
    rid, action = str(args.get("id") or ""), str(args.get("action") or "")
    if action == "run":
        return routines.run_now(rid, reason="you asked from your phone")
    if action in ("pause", "resume"):
        r = routines.update(rid, enabled=action == "resume")
        if not r:
            raise ApiError("No such routine.")
        return {"success": True}
    raise ApiError("Unknown routine action.")


def _goal_act(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import goals
    gid, action = str(args.get("id") or ""), str(args.get("action") or "")
    if action == "check":
        return goals.check_in(gid)
    if action in ("active", "paused", "done"):
        g = goals.update(gid, status=action)
        if not g:
            raise ApiError("No such goal.")
        return {"success": True}
    raise ApiError("Unknown goal action.")


# ---------------------------------------------------------------- memory, reports, files

async def _memory(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import recall
    q = str(args.get("q") or "").strip()
    if not q:
        return {"results": []}
    found = await recall.search(q, k=8)
    return {"results": [{k: (_clip(v, 600) if isinstance(v, str) else v) for k, v in r.items()
                         if isinstance(v, (str, int, float)) or v is None} for r in (found.get("results") or [])[:8]]}


def _report_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|h\d|li|tr)>", "\n", html)
    text = re.sub(r"<[^>]+>", "", html)
    import html as h
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", h.unescape(text)).strip()


def _reports(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.reports import REPORTS_DIR
    items = []
    for p in sorted(REPORTS_DIR.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)[:40] if REPORTS_DIR.exists() else []:
        title = re.search(r"(?is)<title>(.*?)</title>", p.read_text(encoding="utf-8", errors="replace")[:4000])
        items.append({"name": p.name, "title": (title.group(1).strip() if title else p.stem), "updated_at": p.stat().st_mtime})
    return {"reports": items}


def _report_read(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.reports import REPORTS_DIR
    name = str(args.get("name") or "")
    p = REPORTS_DIR / name
    if "/" in name or "\\" in name or not name.endswith(".html") or not p.is_file():
        raise ApiError("No such report.")
    return {"name": name, "text": _clip(_report_text(p.read_text(encoding="utf-8", errors="replace")), 15000)}


def _projects(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.project_storage import project_storage_service as ps
    return {"projects": [{"slug": p["slug"], "name": p.get("name") or p["slug"], "description": _clip(p.get("description"), 200),
                          "files": [{k: f.get(k) for k in ("filename", "subfolder", "size_formatted", "updated_at")}
                                    for f in (p.get("files") or [])[:100]]} for p in ps.list_projects()]}


def _file_read(args: Dict[str, Any]) -> Dict[str, Any]:
    """Only a file the project's own listing shows: no paths, no '..', nothing outside your projects."""
    from app.services.project_storage import project_storage_service as ps
    slug, sub, name = str(args.get("slug") or ""), str(args.get("subfolder") or ""), str(args.get("filename") or "")
    project = next((p for p in ps.list_projects() if p["slug"] == slug), None)
    listed = project and any(f.get("filename") == name and (f.get("subfolder") or "") == sub for f in project.get("files") or [])
    if not listed:
        raise ApiError("That file isn't in this project.")
    r = ps.read_project_file(slug, sub or "root", name)
    if r.get("error"):
        raise ApiError(r["error"])
    return {"slug": slug, "subfolder": sub, "filename": name, "content": _clip(r.get("content"), 15000),
            "size_bytes": r.get("size_bytes")}


def _summary(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import goals, routines
    from app.services.task_queue import task_queue
    return {"counts": task_queue.counts(), "routines": len(routines.list_all()),
            "goals": len([g for g in goals.list_all() if g.get("status") == "active"])}


async def _routes(args: Dict[str, Any]) -> Dict[str, Any]:
    """How the phone can reach this computer (phone_pair.routes()): it keeps them for when it leaves home. Sent here,
    sealed and signed, rather than read from the post office, which anyone on the way could answer for."""
    from app.services import phone_pair
    return await asyncio.to_thread(phone_pair.reach)


def _muse(args: Dict[str, Any]) -> Dict[str, Any]:
    """The Muse approvals switch (muse_app): status, and set {mode: off|always|away|pause|resume, minutes, away_minutes}."""
    from app.services import muse_app
    st = muse_app.status()
    return {"summary": muse_app.describe(st), **{k: st.get(k) for k in
            ("mode", "paused_until", "away_minutes", "pressing_now", "approved", "accessibility_error", "backend")}}


def _muse_set(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import muse_app
    if muse_app._backends.backend() is None:
        raise ApiError("This computer can't approve the Muse app's cards (Mac and Windows only).")
    try:
        if args.get("away_minutes"):
            muse_app.set_away_minutes(int(args["away_minutes"]))
        if args.get("mode"):
            muse_app.set_mode(str(args["mode"]), args.get("minutes"), by="your phone")
    except (TypeError, ValueError) as e:
        raise ApiError(str(e))
    return _muse({})


async def _models(args: Dict[str, Any]) -> Dict[str, Any]:
    """This computer's model choices (remote_models), or another of your Xods' when args has "machine"."""
    from app.services import remote_models, xod_network
    if args.get("machine"):
        try:
            return await xod_network.machine_models(str(args["machine"]))
        except xod_network.NetworkError as e:
            raise ApiError(str(e))
    return await remote_models.choices()


async def _models_use(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import remote_models, xod_network
    provider, model = str(args.get("provider") or ""), str(args.get("model") or "")
    if not provider or not model:
        raise ApiError("Which model? Pick one from the list.")
    if args.get("machine"):
        try:
            return await xod_network.machine_use_model(str(args["machine"]), provider, model, "your phone")
        except xod_network.NetworkError as e:
            raise ApiError(str(e))
    r = await remote_models.use(provider, model, "your phone")
    if not r.get("success"):
        raise ApiError(r.get("error") or "Couldn't switch.")
    return {**r, **await remote_models.choices()}


async def _machines(args: Dict[str, Any]) -> Dict[str, Any]:
    """Your other Xods this computer can see (for the phone's model picker)."""
    from app.services import xod_network
    st = await asyncio.to_thread(xod_network.status)
    if not st.get("signed_in"):
        return {"signed_in": False, "me": st.get("name"), "xods": []}
    f = await xod_network.find()
    return {"signed_in": True, "me": f.get("me"), "xods": [{k: x.get(k) for k in ("name", "host", "os", "online")}
                                                            for x in f.get("xods") or [] if x.get("same_account")]}


OPS: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "summary": _summary, "link.routes": _routes,
    "chat.sessions": _sessions, "chat.history": _history, "chat.send": _chat,
    "tasks.list": _tasks, "tasks.get": _task_get, "tasks.create": _task_create, "tasks.act": _task_act,
    "routines.list": _routines, "routines.act": _routine_act, "goals.act": _goal_act,
    "memory.search": _memory, "reports.list": _reports, "reports.read": _report_read,
    "files.projects": _projects, "files.read": _file_read,
    "muse.status": _muse, "muse.set": _muse_set,
    "models.list": _models, "models.use": _models_use, "machines.list": _machines,
}


async def run(op: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """{ok, data} or {ok: False, error}. Never raises."""
    fn = OPS.get(op)
    if not fn:
        return {"ok": False, "error": f"This computer's Xod doesn't know '{op}' yet. Update Xod on the computer."}
    try:
        res = fn(args if isinstance(args, dict) else {})
        if inspect.isawaitable(res):
            res = await asyncio.wait_for(res, CHAT_TIMEOUT)
        return {"ok": True, "data": res}
    except ApiError as e:
        return {"ok": False, "error": str(e)}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "That took too long on the computer."}
    except Exception as e:
        logger.warning(f"phone_api {op}: {e}")
        return {"ok": False, "error": f"Xod on the computer hit a problem: {type(e).__name__}"}


def encode(request_id: str, out: Dict[str, Any]) -> str:
    """The answer as text, trimmed to fit one message (lists are shortened before anything is cut mid-way)."""
    body = {"request_id": request_id, **out}
    text = json.dumps(body, default=str, separators=(",", ":"))
    data = out.get("data")
    while len(text) > MAX_REPLY and isinstance(data, dict):
        lists = [k for k, v in data.items() if isinstance(v, list) and len(v) > 1]
        if not lists:
            break
        biggest = max(lists, key=lambda k: len(json.dumps(data[k], default=str)))
        data[biggest] = data[biggest][: max(1, len(data[biggest]) // 2)]
        text = json.dumps(body, default=str, separators=(",", ":"))
    if len(text) > MAX_REPLY:
        text = json.dumps({"request_id": request_id, "ok": False, "error": "The answer was too big to send to the phone."})
    return text


async def handle(sender: str, msg: Dict[str, Any], row_id: str) -> None:
    """One request from a phone: run it, answer on the same thread, and keep both out of the Agent Mail inbox."""
    from app.services import agent_mail
    with agent_mail._db() as c:
        c.execute("UPDATE mail_messages SET status='api', read=1 WHERE id=?", (row_id,))
    if not allowed(sender):
        logger.info(f"phone_api: ignored a request from {sender} (not a paired phone)")
        return
    try:
        req = json.loads(msg.get("text") or "{}")
    except ValueError:
        req = {}
    rid = str(req.get("request_id") or "")[:64]
    started = time.time()
    out = await run(str(req.get("op") or ""), req.get("args") or {})
    logger.info(f"phone_api: {req.get('op')} from {sender} -> {'ok' if out.get('ok') else out.get('error')} in {time.time() - started:.1f}s")
    try:
        res = await agent_mail.send(sender, encode(rid, out), REPLY_SUBJECT, "reply", msg.get("thread"), msg.get("id"),
                                    origin="phone-api")
        with agent_mail._db() as c:
            c.execute("UPDATE mail_messages SET status='api' WHERE direction='out' AND thread=? AND subject=? AND origin='phone-api'",
                      (res.get("thread"), REPLY_SUBJECT))
    except Exception as e:
        logger.warning(f"phone_api: couldn't answer {sender}: {e}")
