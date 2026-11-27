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


def family_allowed(sender: str) -> bool:
    """Family ops (Phase 1) are answered for any pinned contact. By the time a
    request reaches handle(), Agent Mail has verified the envelope's signature
    against the sender's pinned keys; the family payloads then carry their own
    signatures (the advertisement, the approval), and enrollment still needs
    the owner's tap. Pairing status is exactly what the exchange establishes,
    so it cannot gate the exchange itself."""
    from app.services import agent_mail
    c = agent_mail.contact((sender or "").lower())
    return bool(c and c.get("trust") in ("known", "mine"))


def allowed(sender: str) -> bool:
    """A phone paired on its own screen, still pinned as a contact you trust."""
    from app.services import agent_mail, phone_mcp
    sender = (sender or "").lower()
    if not any(p.get("address") == sender for p in phone_mcp.paired_phones()):
        return False
    c = agent_mail.contact(sender)
    if not (c and c.get("trust") in ("known", "mine")):
        return False
    try:  # a phone revoked from the family loses control even though it stays paired
        from app.services import phone_enroll
        return not phone_enroll.control_refused(sender)
    except Exception:
        return True


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
    started = time.time()
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
    from app.services import agent_mail, browser_remote
    out = {"session_id": sid, "reply": _clip(agent_mail.re_clean(reply), 12000), "tools": tools[:12]}
    signin = browser_remote.signin_since(started)
    if signin:
        out["browser_signin"] = signin       # the phone shows "Sign in to <site>", which opens Take Over
    if any("browser" in t.lower() for t in tools):
        try:
            proof = await asyncio.wait_for(browser_remote.proof_thumbnail(max_b64=8500), timeout=4.0)
            if proof:
                out["proof_b64"] = proof
        except Exception:
            pass
    return out


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
            ("mode", "paused_until", "away_minutes", "pressing_now", "approved", "accessibility_error",
             "backend", "watcher_running", "watcher_notice", "approval_notice")}}


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


# ---------------------------------------------------------------- Take Over from the phone (services/browser_remote.py)

async def _browser(args: Dict[str, Any], what: str) -> Dict[str, Any]:
    from app.services import browser_remote as br
    try:
        if what == "screen":
            if "phone" in args:          # the phone's Take Over opened (true) or closed (false)
                await br.phone_layout(bool(args["phone"]))
            return await br.screen()
        if what == "tap":
            return await br.tap(args.get("x", 0.5), args.get("y", 0.5))
        if what == "type":
            return await br.type_text(str(args.get("text") or ""), bool(args.get("enter")))
        if what == "key":
            return await br.key(str(args.get("key") or ""))
        if what == "scroll":
            return await br.scroll(str(args.get("direction") or "down"))
        if what == "go":
            return await br.go(str(args.get("url") or ""))
        if what == "back":
            return await br.back()
        return await br.done()
    except ValueError as e:
        raise ApiError(str(e))
    except RuntimeError as e:
        raise ApiError(f"Xod's browser isn't answering ({e}). Try again in a moment.")


BROWSER_OPS = {f"browser.{w}": (lambda w: lambda a: _browser(a, w))(w)
               for w in ("screen", "tap", "type", "key", "scroll", "go", "back", "done")}
# Requests whose words aren't kept on the computer (a password typed into a sign-in page).
SECRET_OPS = {"browser.type"}
# Answers that are pictures of the screen: bigger than a chat answer, and not kept either.
SCREEN_OPS = set(BROWSER_OPS) - {"browser.done"}
MAX_SCREEN_REPLY = 150_000


# ---------------------------------------------------------------- family enrollment (Phase 1)

def _family_advertise(args: Dict[str, Any]) -> Dict[str, Any]:
    """The phone's signed join ask (services/phone_enroll.py). Submission is
    not trust: it pends for the owner's tap, or for a family.approve.
    The mail channel evidences no local presence, so the owner's
    local-presence auto-approve rule never fires on this path."""
    from app.services import phone_enroll
    r = phone_enroll.intake(args.get("advertisement") or {}, mail_sender=args.get("_sender"))
    if not r.get("ok"):
        raise ApiError("; ".join(r.get("problems") or []) or r.get("error") or "The advertisement was refused.")
    return r


def _family_state(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import phone_enroll
    return phone_enroll.state()


def _family_approve(args: Dict[str, Any]) -> Dict[str, Any]:
    """An enrolled phone approves another device's join: the signature must
    cover the current member digest, recomputed on this computer."""
    from app.services import phone_enroll
    r = phone_enroll.apply_phone_approval(str(args.get("device_id") or ""),
                                          str(args.get("signature") or ""),
                                          str(args.get("approver_id") or ""))
    if not r.get("ok"):
        raise ApiError(r.get("error") or "The approval was refused.")
    return r


def _voice_briefs_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import agora_voice
    return {"configured": agora_voice.is_configured(), "briefs": agora_voice.list_briefs(limit=15)}


def _voice_brief_config(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import agora_voice
    if not agora_voice.is_configured():
        raise ApiError("Agora is not configured on this Mac yet. Add your Agora keys in Settings -> Connections.")
    channel = str(args.get("channel") or "").strip() or None
    uid = args.get("uid")
    return agora_voice.agora_voice_service.generate_client_config(channel=channel, uid=int(uid) if uid is not None else None)


async def _voice_brief_start(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import agora_voice
    channel_name = str(args.get("channel_name") or "").strip()
    if not channel_name:
        raise ApiError("Missing channel_name.")
    agent_uid = int(args.get("agent_uid") or 1000)
    user_uid = int(args.get("user_uid") or 1001)
    preset = str(args.get("preset") or "xodbrief")
    return await agora_voice.agora_voice_service.start_session(
        channel_name=channel_name,
        agent_uid=agent_uid,
        user_uid=user_uid,
        preset=preset,
    )


async def _voice_brief_stop(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import agora_voice
    agent_id = str(args.get("agent_id") or "").strip()
    if not agent_id:
        raise ApiError("Missing agent_id.")
    return await agora_voice.agora_voice_service.stop_session(agent_id)


def _voice_brief_launch(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import agora_voice
    bid = str(args.get("id") or "").strip()
    if not bid:
        raise ApiError("Missing brief id.")
    try:
        return agora_voice.launch_brief_as_mission(bid)
    except ValueError as e:
        raise ApiError(str(e))


def _voice_brief_dismiss(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import agora_voice
    bid = str(args.get("id") or "").strip()
    return {"dismissed": agora_voice.dismiss_brief(bid)}


def _looks_get(args: Dict[str, Any]) -> Dict[str, Any]:
    """Return the computer's active companion look (built-in id or Living Look pack + idle keyframe)."""
    import base64
    from app.services.storage import get_setting
    from app.services import looks
    look_id = (get_setting("xod_look", "") or get_setting("mascot_look", "") or "moon").strip()
    out: Dict[str, Any] = {"look": look_id, "name": look_id.title(), "image_b64": ""}
    if looks.is_pack(look_id):
        rep = looks.verify_pack(look_id)
        if rep.get("ok"):
            out["name"] = (rep.get("manifest") or {}).get("name") or look_id
            idle = (rep.get("states") or {}).get("idle") or {}
            fname = idle.get("file") or ""
            d = looks.pack_dir(look_id)
            if d and fname and (d / fname).is_file():
                raw = (d / fname).read_bytes()
                if len(raw) <= 12000:
                    out["image_b64"] = base64.b64encode(raw).decode("ascii")
    return out


def _desktop_chrome_tabs(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import desktop
    res = desktop.chrome_tabs()
    tabs = res.get("tabs") or []
    return {
        "success": bool(res.get("success", True)),
        "tabs": [
            {
                "window": t.get("window_index", 1),
                "tab": t.get("tab_index", 1),
                "title": _clip(t.get("title") or "", 140),
                "url": _clip(t.get("url") or "", 300),
            }
            for t in tabs[:40]
        ],
        "error": res.get("error") or "",
    }


def _desktop_chrome_focus(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import desktop
    query = str(args.get("query") or "").strip()
    win = args.get("window")
    tab = args.get("tab")
    if query:
        return desktop.chrome_focus_tab(query)
    if win is not None and tab is not None:
        return desktop.chrome_focus_tab(f"{win}:{tab}")
    raise ApiError("Specify a tab to focus.")


def _desktop_open_url(args: Dict[str, Any]) -> Dict[str, Any]:
    import subprocess, sys
    url = str(args.get("url") or "").strip()
    if not url:
        raise ApiError("Provide a URL to open.")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Google Chrome", url])
    else:
        import webbrowser
        webbrowser.open(url)
    return {"opened": url}


def _desktop_launch_app(args: Dict[str, Any]) -> Dict[str, Any]:
    import subprocess, sys
    app_name = str(args.get("app") or "").strip()
    if not app_name or len(app_name) > 80 or any(c in app_name for c in ";&|`$\n\r"):
        raise ApiError("Invalid app name.")
    if sys.platform == "darwin":
        r = subprocess.run(["open", "-a", app_name], capture_output=True, text=True, timeout=8)
        if r.returncode != 0:
            raise ApiError((r.stderr or f"Could not open {app_name}").strip()[:200])
        return {"launched": app_name}
    raise ApiError("App launch from phone is currently supported on macOS.")


def _control_center_status(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import control_center
    snap = control_center.snapshot()
    brain = snap.get("brain") or {}
    mcp = snap.get("mcp") or {}
    mac = snap.get("mac_control") or {}
    fleet = snap.get("fleet") or {}
    autonomy = snap.get("autonomy") or {}
    return {
        "ok": bool(snap.get("ok")),
        "issue_count": int(snap.get("issue_count") or 0),
        "brain": {
            "provider": brain.get("provider") or "",
            "model": _clip(brain.get("model") or "", 80),
            "cache_hit_rate": brain.get("cache_hit_rate") or 0.0,
        },
        "mac_ready": bool(mac.get("ready")),
        "mac_missing": mac.get("missing") or [],
        "mcp_connected": int(mcp.get("connected_count") or 0),
        "mcp_total": int(mcp.get("total_servers") or 0),
        "fleet_mode": fleet.get("auto_approve_mode") or "act",
        "blackboard": [
            {"topic": _clip(b.get("topic") or "", 40), "summary": _clip(b.get("summary") or "", 140), "actor": b.get("actor") or ""}
            for b in (fleet.get("blackboard") or [])[:6]
        ],
        "autonomy": {
            "mode": autonomy.get("mode") or "standard",
            "locked": bool(autonomy.get("locked")),
            "lock_reasons": autonomy.get("lock_reasons") or [],
            "disclaimer": autonomy.get("disclaimer") or "",
        },
    }


def _autonomy_get(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import autonomy_modes
    return autonomy_modes.status()


def _autonomy_set(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import autonomy_modes
    try:
        return autonomy_modes.set_mode(str(args.get("mode") or ""), bool(args.get("confirmed_disclaimer")))
    except (autonomy_modes.HarnessLockedError, autonomy_modes.DisclaimerRequiredError, ValueError) as e:
        raise ApiError(str(e))


def _fleet_blackboard(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import fleet_control
    entries = fleet_control.blackboard_read(str(args.get("topic") or ""), int(args.get("limit") or 12))
    return {"entries": entries}


OPS: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "summary": _summary, "link.routes": _routes,
    "looks.get": _looks_get,
    "control_center.status": _control_center_status,
    "autonomy.get": _autonomy_get,
    "autonomy.set": _autonomy_set,
    "fleet.blackboard": _fleet_blackboard,
    "desktop.chrome_tabs": _desktop_chrome_tabs,
    "desktop.chrome_focus": _desktop_chrome_focus,
    "desktop.open_url": _desktop_open_url,
    "desktop.launch_app": _desktop_launch_app,
    "chat.sessions": _sessions, "chat.history": _history, "chat.send": _chat,
    "tasks.list": _tasks, "tasks.get": _task_get, "tasks.create": _task_create, "tasks.act": _task_act,
    "voice_brief.list": _voice_briefs_list, "voice_brief.config": _voice_brief_config,
    "voice_brief.start": _voice_brief_start, "voice_brief.stop": _voice_brief_stop,
    "voice_brief.launch": _voice_brief_launch, "voice_brief.dismiss": _voice_brief_dismiss,
    "routines.list": _routines, "routines.act": _routine_act, "goals.act": _goal_act,
    "memory.search": _memory, "reports.list": _reports, "reports.read": _report_read,
    "files.projects": _projects, "files.read": _file_read,
    "muse.status": _muse, "muse.set": _muse_set,
    "models.list": _models, "models.use": _models_use, "machines.list": _machines,
    "family.advertise": _family_advertise, "family.state": _family_state, "family.approve": _family_approve,
    **BROWSER_OPS,
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


def encode(request_id: str, out: Dict[str, Any], limit: int = MAX_REPLY) -> str:
    """The answer as text, trimmed to fit one message (lists are shortened before anything is cut mid-way)."""
    body = {"request_id": request_id, **out}
    text = json.dumps(body, default=str, separators=(",", ":"))
    data = out.get("data")
    if len(text) > limit and isinstance(data, dict) and data.get("proof_b64"):
        data = {k: v for k, v in data.items() if k != "proof_b64"}
        body = {"request_id": request_id, **{**out, "data": data}}
        text = json.dumps(body, default=str, separators=(",", ":"))
    while len(text) > limit and isinstance(data, dict):
        lists = [k for k, v in data.items() if isinstance(v, list) and len(v) > 1]
        if not lists:
            break
        biggest = max(lists, key=lambda k: len(json.dumps(data[k], default=str)))
        data[biggest] = data[biggest][: max(1, len(data[biggest]) // 2)]
        text = json.dumps(body, default=str, separators=(",", ":"))
    if len(text) > limit:
        text = json.dumps({"request_id": request_id, "ok": False, "error": "The answer was too big to send to the phone."})
    return text


async def handle(sender: str, msg: Dict[str, Any], row_id: str) -> None:
    """One request from a phone: run it, answer on the same thread, and keep both out of the Agent Mail inbox."""
    from app.services import agent_mail
    with agent_mail._db() as c:
        c.execute("UPDATE mail_messages SET status='api', read=1 WHERE id=?", (row_id,))
    try:
        req = json.loads(msg.get("text") or "{}")
    except ValueError:
        req = {}
    if str(req.get("op") or "").startswith("family."):
        if not family_allowed(sender):
            logger.info(f"phone_api: ignored a family request from {sender} (not a pinned contact)")
            return
    elif not allowed(sender):
        logger.info(f"phone_api: ignored a request from {sender} (not a paired phone)")
        return
    rid = str(req.get("request_id") or "")[:64]
    op = str(req.get("op") or "")
    if op in SECRET_OPS:
        with agent_mail._db() as c:      # what was typed (often a password) isn't kept, only that something was
            c.execute("UPDATE mail_messages SET text=? WHERE id=?", (json.dumps({"op": op, "request_id": rid, "args": "(hidden)"}), row_id))
    started = time.time()
    args = req.get("args") or {}
    if op.startswith("family.") and isinstance(args, dict):
        args = {**args, "_sender": sender}  # family ops bind the ask to the channel it arrived on
    out = await run(op, args)
    logger.info(f"phone_api: {req.get('op')} from {sender} -> {'ok' if out.get('ok') else out.get('error')} in {time.time() - started:.1f}s")
    try:
        screen = op in SCREEN_OPS
        res = await agent_mail.send(sender, encode(rid, out, MAX_SCREEN_REPLY if screen else MAX_REPLY), REPLY_SUBJECT, "reply",
                                    msg.get("thread"), msg.get("id"), origin="phone-api",
                                    max_text=MAX_SCREEN_REPLY if screen else 20000)
        with agent_mail._db() as c:
            c.execute("UPDATE mail_messages SET status='api' WHERE direction='out' AND thread=? AND subject=? AND origin='phone-api'",
                      (res.get("thread"), REPLY_SUBJECT))
            if screen:      # a picture of the page isn't worth keeping
                c.execute("UPDATE mail_messages SET text='(a picture of Xod''s browser)' WHERE direction='out' AND thread=? "
                          "AND subject=? AND origin='phone-api'", (res.get("thread"), REPLY_SUBJECT))
    except Exception as e:
        logger.warning(f"phone_api: couldn't answer {sender}: {e}")
