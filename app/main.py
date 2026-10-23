import asyncio
import sys
import os
import json
import logging
import re
import httpx
from pathlib import Path
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.paths import DATA_DIR
from app.config import settings, update_env_variable
from app.services.trustmrr import trustmrr_client, VALID_CATEGORIES, VALID_SORTS
from app.services.you_search import you_client
from app.services.tavily_search import tavily_client
from app.services.tech_auditor import tech_auditor
from app.services.omni_search import omni_search_engine
from app.services.llm import llm_service
from app.agent.researcher import AgenticResearcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")

app = FastAPI(title="Xod - Ultimate Researcher & TrustMRR Assistant", version="1.0.0")

# Only this machine's own UI may talk to the server. Xod can run commands, read files and edit its
# own code, so a web page open in any browser (including Xod's own) must not be able to send it
# requests: no CORS, a Host check (stops DNS rebinding) and an Origin / Sec-Fetch-Site check
# (stops cross-site requests). Tools like curl send neither header and still work.
from starlette.responses import JSONResponse  # noqa: E402

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def _host_only(value: str) -> str:
    v = (value or "").strip().lower()
    if "://" in v:
        v = v.split("://", 1)[1]
    v = v.split("/", 1)[0]
    if v.startswith("["):
        return v.split("]", 1)[0] + "]"
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


from app.services.trustmrr import TrustMRRError as _TrustMRRError


@app.exception_handler(_TrustMRRError)
async def trustmrr_error_handler(request: Request, exc: _TrustMRRError):
    """A missing or rejected TrustMRR key is a setup problem, not a crash: say what to do."""
    missing = "missing" in str(exc).lower()
    return JSONResponse(status_code=400 if missing else 502,
                        content={"success": False, "needs_key": missing, "error": str(exc)})


@app.middleware("http")
async def local_only_guard(request, call_next):
    if _host_only(request.headers.get("host", "")) not in _LOCAL_HOSTS:
        return JSONResponse({"detail": "Xod only answers on localhost."}, status_code=403)
    from app.services import previews as _previews
    if request.method in ("GET", "HEAD") and request.url.path.startswith(_previews.PREFIX):
        # A sandboxed preview page (opaque origin) loading its own files; nothing else is reachable here.
        return await call_next(request)
    _previews.SERVER_BASE = "http://" + request.headers.get("host", "127.0.0.1:8000")
    origin = request.headers.get("origin")
    own = request.headers.get("host", "").lower()
    if origin and origin != "null" and origin.lower().split("://", 1)[-1].rstrip("/") != own:
        return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)
    # One exception: a sign-in provider sends the browser back to /oauth/callback from its own site.
    # That page only completes a sign-in Xod itself started, matched by a random one-time state.
    oauth_return = request.method == "GET" and request.url.path == "/oauth/callback" and not origin
    if (origin == "null" or request.headers.get("sec-fetch-site") == "cross-site") and not oauth_return:
        return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)
    response = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response


# Second line of defence if something slips past the sanitizer: the page may only run its own
# scripts and talk to this server. Images and video may come from anywhere (research results).
_SECURITY_HEADERS = {
    "Content-Security-Policy": "; ".join([
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",        # the UI uses inline onclick handlers
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https: http:",
        "media-src 'self' data: blob: https:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "frame-src 'self'",                          # the preview pane (sandboxed, see services/previews.py)
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Static files mount
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

MEDIA_DIR = DATA_DIR / "media_studio"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")
REPORTS_DIR = DATA_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports")

from app.services.storage import (
    init_db,
    create_session,
    list_sessions,
    get_session,
    update_session,
    delete_session,
    add_message,
    get_message,
    get_session_messages,
    list_entities,
    get_entity,
    search_entities,
    update_entity_enrichment,
    get_entity_relationships,
    add_or_update_relationship
)
from app.services.community_intel import community_intel_service
from app.services.github_auditor import github_auditor_service
from app.services.dns_inspector import dns_inspector_service
from app.services.surface_explorer import surface_explorer_service
from app.services.subdomain_recon import subdomain_recon_service
from app.services.talent_intel import talent_intel_service
from app.services.package_auditor import package_auditor_service
from app.services.sec_edgar import sec_edgar_service
from app.services.ip_intel import ip_intel_service
from app.services.traffic_intel import traffic_intel_service
from app.services.trend_intel import trend_intel_service
from app.services.roadmap import get_project_roadmap
from app.services.email_intel import email_intel_service
from app.services.funding_intel import funding_intel_service
from app.services.project_storage import project_storage_service
from app.services.filesystem import filesystem_service
from app.services.browser_sandbox import browser_sandbox_service
from app.services.system_control import system_control_service
from app.services.creator_intel import creator_intel_service
from app.services.youtube_client import youtube_client
from app.services.media_model_manager import media_model_manager
from app.services.memory_service import memory_service


class ChatRequest(BaseModel):
    messages: List[Dict[str, Any]]
    route: Optional[str] = None   # "cloud": run this turn on the stronger cloud model (llm_routing)
    persona: Optional[str] = "ultimate_researcher"
    session_id: Optional[str] = None
    active_workspace_path: Optional[str] = None
    attached_document: Optional[Dict[str, Any]] = None
    voice: Optional[bool] = None   # asked out loud in a conversation: answer in a spoken style

class CreateSessionRequest(BaseModel):
    title: Optional[str] = "New Research Session"
    persona: Optional[str] = "ultimate_researcher"

class UpdateSessionRequest(BaseModel):
    title: Optional[str] = None
    persona: Optional[str] = None

class ConfigUpdateRequest(BaseModel):
    trustmrr_api_key: Optional[str] = None
    you_api_key: Optional[str] = None
    youtube_api_key: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    fireworks_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    huggingface_token: Optional[str] = None

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found")

    # Each script and stylesheet gets ?v=<its file's change time>, so a changed file is always fetched fresh
    # (after an update, or an edit) and an unchanged one stays cached. No version to bump by hand.
    def stamp(m: "re.Match") -> str:
        try:
            return f"{m.group(1)}?v={int((STATIC_DIR / m.group(2)).stat().st_mtime)}"
        except OSError:
            return m.group(0)
    return re.sub(r'(/static/([^"?\s]+\.(?:js|css)))\?v=[\w.-]+', stamp, index_file.read_text(encoding="utf-8"))

@app.get("/api/sessions")
async def get_sessions():
    """Returns list of all saved research sessions."""
    return {"sessions": list_sessions()}

@app.post("/api/sessions")
async def create_new_session(req: CreateSessionRequest):
    """Creates a new persistent research session."""
    session = create_session(title=req.title or "New Research Session", persona=req.persona or "ultimate_researcher")
    return session

@app.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str):
    """Returns session info and its full message history."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = get_session_messages(session_id)
    return {"session": session, "messages": messages}

@app.patch("/api/sessions/{session_id}")
async def update_session_endpoint(session_id: str, req: UpdateSessionRequest):
    """Updates session title or persona."""
    update_session(session_id, title=req.title, persona=req.persona)
    return {"status": "success"}

@app.delete("/api/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    """Deletes a session and its saved messages."""
    delete_session(session_id)
    return {"status": "success"}

@app.get("/api/messages/{message_id}/trace")
async def get_message_trace(message_id: str):
    """Returns deep observability trace and tool payloads for a specific assistant message."""
    msg = get_message(message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning") or ""
    tools = msg.get("tool_events") or []
    
    char_count = len(content) + len(reasoning)
    approx_tokens = int(char_count / 3.8) if char_count > 0 else 0
    
    # Extract swarm intelligence if present
    is_swarm = False
    swarm_plan = None
    swarm_results = []
    subagent_tool_calls = []
    all_tools = []

    if isinstance(tools, list):
        for ev in tools:
            ev_type = ev.get("type")
            if ev_type == "swarm_plan":
                is_swarm = True
                swarm_plan = ev
            elif ev_type == "swarm_complete":
                is_swarm = True
                swarm_results = ev.get("results", [])
            elif ev_type in ("tool_call", "subagent_tool_call"):
                t_name = ev.get("name") or ev.get("tool") or "unknown"
                all_tools.append(t_name)
                if ev_type == "subagent_tool_call":
                    subagent_tool_calls.append(ev)
            elif ev_type == "tool_result":
                t_name = ev.get("name") or ev.get("tool") or "unknown"
                if t_name not in all_tools:
                    all_tools.append(t_name)

    return {
        "message": msg,
        "metrics": {
            "model": settings.llm_model,
            "provider": settings.llm_provider,
            "reasoning_chars": len(reasoning),
            "content_chars": len(content),
            "estimated_tokens": approx_tokens,
            "tool_count": len(all_tools),
            "tools_used": all_tools,
            "is_swarm": is_swarm,
            "subagents_count": len(swarm_plan.get("subagents", [])) if swarm_plan else len(swarm_results),
            "subagent_tools_count": len(subagent_tool_calls),
            "swarm_plan": swarm_plan,
            "swarm_results": swarm_results
        }
    }

@app.get("/api/sessions/{session_id}/trace")
async def get_session_trace(session_id: str):
    """Returns the full execution trace and telemetry breakdown across all messages in a session."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = get_session_messages(session_id)
    
    total_tools = 0
    all_tools_used = []
    total_reasoning_chars = 0
    total_content_chars = 0
    turn_traces = []
    
    for m in messages:
        t_events = m.get("tool_events") or []
        reasoning = m.get("reasoning") or ""
        content = m.get("content") or ""
        
        t_count = len(t_events) if isinstance(t_events, list) else 0
        total_tools += t_count
        total_reasoning_chars += len(reasoning)
        total_content_chars += len(content)
        
        if isinstance(t_events, list):
            for t in t_events:
                t_name = t.get("tool") or t.get("name") or "unknown"
                all_tools_used.append(t_name)
        
        turn_traces.append({
            "id": m.get("id"),
            "role": m.get("role"),
            "created_at": m.get("created_at"),
            "has_reasoning": bool(reasoning),
            "reasoning_chars": len(reasoning),
            "content_chars": len(content),
            "tool_count": t_count,
            "tools": t_events
        })
    
    approx_tokens = int((total_reasoning_chars + total_content_chars) / 3.8)
    
    return {
        "session": session,
        "metrics": {
            "model": settings.llm_model,
            "provider": settings.llm_provider,
            "total_messages": len(messages),
            "total_tools": total_tools,
            "tools_breakdown": {t: all_tools_used.count(t) for t in set(all_tools_used)},
            "estimated_tokens": approx_tokens,
            "total_reasoning_chars": total_reasoning_chars,
            "total_content_chars": total_content_chars
        },
        "turns": turn_traces
    }

# --------------------------------------------------------------
# Saved website logins (passwords stay in the macOS Keychain)
# --------------------------------------------------------------
class SaveLoginRequest(BaseModel):
    site: str
    username: str
    password: str


@app.get("/api/vault/logins")
async def list_saved_logins():
    from app.services import credential_vault
    return {"logins": credential_vault.list_logins()}


@app.post("/api/vault/logins")
async def save_saved_login(req: SaveLoginRequest):
    from app.services import credential_vault
    try:
        return {"success": True, **credential_vault.save_login(req.site, req.username, req.password, source="added by you")}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/vault/logins/{site:path}")
async def delete_saved_login(site: str):
    from app.services import credential_vault
    return {"success": credential_vault.delete_login(site)}


# 1Password (services/onepassword.py): API keys as op:// references, and website logins from 1Password.
class OnePasswordLink(BaseModel):
    name: str
    reference: str


class OnePasswordLogins(BaseModel):
    on: bool


def _op_call(fn, *args):
    from app.services import onepassword
    try:
        return fn(*args)
    except onepassword.OnePasswordError as e:
        return {"success": False, "error": str(e)}


@app.get("/api/1password")
async def onepassword_status():
    from app.services import onepassword
    return await asyncio.to_thread(onepassword.status)


@app.post("/api/1password/connect")
async def onepassword_connect():
    from app.services import onepassword
    return await asyncio.to_thread(_op_call, onepassword.connect)


@app.post("/api/1password/disconnect")
async def onepassword_disconnect():
    from app.services import onepassword
    return onepassword.disconnect()


@app.post("/api/1password/logins")
async def onepassword_logins(req: OnePasswordLogins):
    from app.services import onepassword
    return onepassword.set_logins(req.on)


@app.get("/api/1password/items")
async def onepassword_items(q: str = ""):
    from app.services import onepassword
    res = await asyncio.to_thread(_op_call, onepassword.items, q)
    return res if isinstance(res, dict) else {"success": True, "items": res}


@app.get("/api/1password/items/{item_id}/fields")
async def onepassword_fields(item_id: str):
    from app.services import onepassword
    res = await asyncio.to_thread(_op_call, onepassword.fields, item_id)
    return res if isinstance(res, dict) else {"success": True, "fields": res}


@app.post("/api/1password/link")
async def onepassword_link(req: OnePasswordLink):
    """Uses a 1Password field for one of Xod's API keys: the vault keeps the op:// reference, not the key."""
    from app.services import onepassword, secret_store
    from app.config import update_env_variable, settings
    if not secret_store.is_secret(req.name) or not onepassword.is_reference(req.reference):
        raise HTTPException(status_code=400, detail="Pick one of Xod's keys and a 1Password field.")
    value = await asyncio.to_thread(onepassword.read, req.reference)
    if not value:
        return {"success": False, "error": "1Password didn't hand over that field. Is 1Password unlocked?"}
    field = secret_store.canonical(req.name)
    update_env_variable(field, req.reference)
    setattr(settings, field, value)        # in use now; the vault keeps only the reference
    return {"success": True, "name": field, "masked": secret_store.mask(req.reference)}


@app.get("/api/secrets")
async def secrets_list():
    """Which keys are saved (masked), and which come from 1Password."""
    from app.services import secret_store
    return {"keys": await asyncio.to_thread(secret_store.status), **secret_store.health()}


# --------------------------------------------------------------
# Task inbox: jobs that run in the background (see services/task_queue.py)
# --------------------------------------------------------------
class NewTaskRequest(BaseModel):
    request: str


class TaskAnswerRequest(BaseModel):
    text: str


@app.get("/api/tasks")
async def list_tasks():
    from app.services.task_queue import task_queue
    return {"tasks": task_queue.list(), "counts": task_queue.counts()}


@app.get("/api/tasks/counts")
async def task_counts():
    """How many tasks are waiting, working, queued… (the desktop app's tray shows it)."""
    from app.services.task_queue import task_queue
    return task_queue.counts()


@app.post("/api/tasks")
async def create_task(req: NewTaskRequest):
    from app.services.task_queue import task_queue
    try:
        return {"success": True, "task": task_queue.create(req.request)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/tasks/{task_id}/approve")
async def approve_task(task_id: str):
    from app.services.task_queue import task_queue
    return task_queue.approve(task_id)


@app.post("/api/tasks/{task_id}/answer")
async def answer_task(task_id: str, req: TaskAnswerRequest):
    from app.services.task_queue import task_queue
    return task_queue.answer(task_id, req.text)


@app.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: str):
    from app.services.task_queue import task_queue
    return task_queue.retry(task_id)


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    from app.services.task_queue import task_queue
    return await task_queue.cancel(task_id)


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    from app.services.task_queue import task_queue
    return {"success": task_queue.delete(task_id)}


@app.get("/api/tasks/{task_id}/screenshot")
async def task_screenshot(task_id: str):
    from fastapi.responses import FileResponse
    from app.services.task_queue import task_queue
    path = task_queue.screenshot_path(task_id)
    if not path:
        raise HTTPException(status_code=404, detail="No screenshot")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/tasks/{task_id}/show")
async def show_task_page(task_id: str):
    """Brings a task's own tab to the front (e.g. to clear a bot check with Take Over)."""
    from app.services.task_queue import task_queue
    from app.services.browser_sandbox import browser_sandbox_service as sb
    t = task_queue.get(task_id)
    if not t or not t.get("tab_id"):
        return {"success": False, "error": "This task has no open page."}
    return await sb.manage_tabs("switch", target_id=t["tab_id"])


@app.on_event("shutdown")
async def _stop_crew_host():
    try:
        from app.services import crew
        await crew.shutdown()
    except Exception:
        pass


@app.on_event("shutdown")
async def _stop_terminal_sessions():
    """The agents and programs Xod started in terminals stop with it (they run in their own process groups, so
    nothing else would stop them)."""
    try:
        from app.services import agent_clis
        agent_clis.stop_all()
    except Exception:
        pass


def _announce_server() -> None:
    """Writes where this server listens (DATA_DIR/server.json), so `xod` in a terminal finds the app's server even when
    it isn't on port 8000 (the app picks another port when 8000 is taken)."""
    import json as _json
    import sys as _sys
    port = None
    argv = _sys.argv
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            port = argv[i + 1]
        elif a.startswith("--port="):
            port = a.split("=", 1)[1]
    port = port or os.environ.get("XOD_PORT") or "8000"
    try:
        (DATA_DIR / "server.json").write_text(_json.dumps({"url": f"http://127.0.0.1:{int(port)}", "pid": os.getpid()}))
    except (OSError, ValueError):
        pass


@app.on_event("startup")
async def _start_task_worker():
    _announce_server()
    from app.services.task_queue import task_queue
    from app.services.doctor import doctor
    task_queue.start()
    doctor.start()
    from app.services import activity, routines
    activity.prune()
    routines.start()
    from app.services.mcp_client import mcp_hub
    asyncio.create_task(mcp_hub.start())   # connect outside tool servers in the background
    from app.services import agent_mail
    agent_mail.start()                      # picks up Agent Mail every 30 s once an address is set up
    from app.services import soul
    asyncio.create_task(soul.life_loop())   # when you're away, the companion does things for itself
    from app.services import recall
    recall.schedule_index(20)               # catch Xod's memory up on chats and tasks since it last ran
    from app.services import autoconnect
    asyncio.create_task(autoconnect.loop())  # keeps finding the AI on this computer and keeps it working
    from app.services import local_servers
    asyncio.create_task(local_servers.idle_loop())   # stops the built-in engine after a while unused
    from app.services import phone
    phone.start()                           # the linked phone (Telegram), if there is one


class ModelRef(BaseModel):
    provider: str
    model: str
    base_url: Optional[str] = ""
    sees: Optional[bool] = None


class ModelUse(BaseModel):
    job: str
    provider: str
    model: str
    base_url: Optional[str] = ""


class ModelSourceAdd(BaseModel):
    url: str
    name: Optional[str] = ""
    key: Optional[str] = ""


@app.get("/api/capabilities")
async def capabilities_endpoint():
    """What works on this computer with this model, what's limited and why, and what's off (services/capabilities.py)."""
    from app.services import capabilities
    return await capabilities.report()


@app.get("/api/models")
async def models_endpoint():
    """Every model Xod can use, on every server, with what it can do, its score and its jobs (services/model_hub.py)."""
    from app.services import model_hub
    return await model_hub.all_models()


@app.post("/api/models/use")
async def models_use_endpoint(req: ModelUse):
    from app.services import model_hub
    return model_hub.use_for(req.job, req.provider, req.model, req.base_url or "")


@app.get("/api/models/catalog")
async def models_catalog_endpoint():
    from app.services import model_hub
    return await model_hub.catalog()


@app.get("/api/models/search")
async def models_search_endpoint(q: str):
    from app.services import model_hub
    return await model_hub.search_hf(q)


@app.post("/api/models/test")
async def models_test_endpoint(req: ModelRef):
    """Scores a model on this computer, in the background (services/model_scores.py)."""
    from app.services import model_scores
    return model_scores.start(req.provider, req.model, req.base_url or "", req.sees)


@app.get("/api/models/test")
async def models_test_status_endpoint(key: str):
    from app.services import model_scores
    return model_scores.status(key)


@app.post("/api/models/sources")
async def models_source_add_endpoint(req: ModelSourceAdd):
    from app.services import model_sources
    return await model_sources.add(req.url, req.name or "", req.key or "")


@app.delete("/api/models/sources/{sid}")
async def models_source_remove_endpoint(sid: str):
    from app.services import model_sources
    return {"success": model_sources.remove(sid)}


class PhoneConnectRequest(BaseModel):
    token: str


class PhoneNotifyRequest(BaseModel):
    all_tasks: bool


@app.get("/api/phone")
async def phone_status():
    from app.services import phone
    return phone.status()


@app.post("/api/phone/connect")
async def phone_connect(req: PhoneConnectRequest):
    """A Telegram bot token from BotFather: checked, kept in the Keychain, and a pairing code made."""
    from app.services import phone
    return await phone.connect(req.token)


@app.post("/api/phone/disconnect")
async def phone_disconnect():
    from app.services import phone
    return phone.disconnect()


@app.post("/api/phone/notify")
async def phone_notify(req: PhoneNotifyRequest):
    """Tell the phone about every task (on) or only the ones started from it (off)."""
    from app.services import phone
    return phone.set_notify_all(req.all_tasks)


@app.post("/api/phone/test")
async def phone_test():
    from app.services import phone
    ok = await phone.send("Hello from Xod on your computer. 👋")
    return {"success": bool(ok), "error": None if ok else "Not linked yet, or Telegram couldn't be reached."}


@app.get("/api/mcp")
async def mcp_status_endpoint():
    from app.services.mcp_client import mcp_hub
    return {"servers": mcp_hub.status(), "pending": list(mcp_hub.pending.values())}


@app.get("/api/mcp/self-config")
async def mcp_self_config_endpoint(request: Request):
    """The snippet other MCP apps need to use Xod (paths of this install, this server's port)."""
    import sys as _sys
    from app.paths import APP_ROOT
    port = request.url.port or 8000
    return {"config": {"mcpServers": {"xod": {
        "command": _sys.executable, "args": ["-m", "app.mcp_server"], "cwd": str(APP_ROOT),
        "env": {"XOD_URL": f"http://127.0.0.1:{port}"}}}}}


@app.post("/api/mcp/servers")
async def mcp_add_endpoint(request: Request):
    """Body: a Claude-Desktop-style block {"mcpServers": {...}}. Secret env values go to the Keychain."""
    from app.services import mcp_client
    try:
        block = await request.json()
    except Exception:
        return {"success": False, "error": "That isn't valid JSON."}
    res = mcp_client.add_servers(block)
    if res["success"]:
        await mcp_client.mcp_hub.start()
    return res


class MCPImport(BaseModel):
    text: str
    values: Dict[str, Dict[str, str]] = {}
    only: Optional[List[str]] = None


@app.get("/api/agent")
async def agent_page_head():
    """The Agent page's header: name and what it's doing now (services/agent_page.py)."""
    from app.services import agent_page
    return {"name": agent_page.identity().get("name") or "Xod", "status": agent_page.status_line()}


@app.get("/api/agent/{tab}")
async def agent_page_tab(tab: str, origin: Optional[str] = None):
    """One tab of the Agent page: activity (or one job's steps with ?origin=), approvals, browser, schedules,
    identity, team."""
    from app.services import agent_page
    if tab == "activity":
        return {"detail": agent_page.detail(origin)} if origin else {"items": await asyncio.to_thread(agent_page.activity)}
    if tab == "approvals":
        return {"items": agent_page.approvals()}
    if tab == "browser":
        return {"items": agent_page.browser()}
    if tab == "schedules":
        return agent_page.schedules()
    if tab == "identity":
        return await asyncio.to_thread(agent_page.identity)
    if tab == "team":
        return await agent_page.team_view()
    if tab == "usage":
        from app.services import usage
        return await asyncio.to_thread(usage.overview, origin if origin in usage.RANGES else "7d")
    return {"error": "No such tab."}


@app.post("/api/agent/reminders")
async def agent_add_reminder(request: Request):
    """{"text": "Follow up with Saphira", "at": "2026-10-28T09:00"}: a reminder here and on the phone."""
    from app.services import agent_page
    body = await request.json()
    return agent_page.add_reminder(body.get("text", ""), body.get("at", ""))


@app.post("/api/agent/team")
async def agent_team_save(request: Request):
    """{"tiers": {"large": {"provider": "anthropic", "model": "…"} | null}, "agents": ["xod2"], "enabled": true}"""
    from app.services import team, agent_page
    team.save(**(await request.json()))
    return await agent_page.team_view()


@app.get("/api/goals")
async def goals_list():
    from app.services import goals
    return {"goals": goals.list_all(), "brief": goals.brief_settings()}


@app.post("/api/goals")
async def goals_create(request: Request):
    """{"text": "find a flat under $2k by November"}: a plan, and the first check-in starts."""
    from app.services import goals
    body = await request.json()
    text = str(body.get("text") or "").strip()
    if len(text) < 4:
        return {"success": False, "error": "Say what the goal is."}
    return {"success": True, "goal": await goals.create(text, start_now=body.get("start_now", True))}


@app.patch("/api/goals/{gid}")
async def goals_update(gid: str, request: Request):
    from app.services import goals
    g = goals.update(gid, **(await request.json()))
    return {"success": bool(g), "goal": g}


@app.post("/api/goals/{gid}/check")
async def goals_check(gid: str):
    from app.services import goals
    return goals.check_in(gid)


@app.delete("/api/goals/{gid}")
async def goals_delete(gid: str):
    from app.services import goals
    return {"success": goals.delete(gid)}


@app.post("/api/brief")
async def brief_set(request: Request):
    """{"enabled": true, "time": "07:30"} or {"run": true} for one now."""
    from app.services import goals
    body = await request.json()
    if body.get("run"):
        return goals.run_brief()
    return {"success": True, "brief": goals.set_brief(body.get("enabled"), body.get("time"))}


@app.get("/api/sentinel")
async def sentinel_log():
    """What the safety check held lately, and why (services/sentinel.py)."""
    from app.services import sentinel
    return {"held": list(reversed(sentinel.LOG[-50:]))}


@app.get("/api/cli/hello")
async def cli_hello():
    """Who this server is, for the terminal (app/cli.py): it only talks to a Xod using the same data folder."""
    from app.version import VERSION
    return {"app": "xod", "version": VERSION, "data_dir": str(DATA_DIR), "pid": os.getpid()}


@app.post("/api/mcp/import/check")
async def mcp_import_check(req: MCPImport):
    """Anything a server's page says to paste (JSON, a command, an install link, an address): what's in it, what's
    missing, and, for addresses, how to connect (asked from the server). Nothing is saved."""
    from app.services import mcp_import
    return await mcp_import.check(req.text)


@app.post("/api/mcp/import/add")
async def mcp_import_add(req: MCPImport, request: Request):
    """Saves and connects what was pasted, with the answers filled in. A sign-in opens in the browser."""
    from app.services import mcp_import
    redirect_uri = f"http://{request.headers.get('host', '127.0.0.1:8000')}/oauth/callback"
    return await mcp_import.add(req.text, req.values, redirect_uri, req.only)


class MCPServerUpdate(BaseModel):
    enabled: Optional[bool] = None
    trust: Optional[str] = None


@app.patch("/api/mcp/servers/{name}")
async def mcp_update_endpoint(name: str, req: MCPServerUpdate):
    from app.services import mcp_client
    ok = mcp_client.update_server(name, req.enabled, req.trust)
    if ok and req.enabled is not None:
        await mcp_client.mcp_hub.start()
    return {"success": ok}


@app.delete("/api/mcp/servers/{name}")
async def mcp_remove_endpoint(name: str):
    from app.services import mcp_client
    ok = mcp_client.remove_server(name)
    await mcp_client.mcp_hub.start()
    return {"success": ok}


@app.post("/api/mcp/servers/{name}/connect")
async def mcp_connect_one(name: str, request: Request):
    """Tries one server again; {"interactive": true} may open its sign-in page (it finishes in the background)."""
    import asyncio as _asyncio
    from app.services.mcp_client import mcp_hub
    try:
        body = await request.json()
    except Exception:
        body = {}
    if body.get("interactive"):
        redirect_uri = f"http://{request.headers.get('host', '127.0.0.1:8000')}/oauth/callback"
        from app.services import mcp_import
        mcp_import._tasks[name] = _asyncio.create_task(mcp_hub.connect(name, interactive=True, redirect_uri=redirect_uri))
        return {"success": True, "started": True}
    return await mcp_hub.connect(name)


@app.post("/api/mcp/reconnect")
async def mcp_reconnect_endpoint():
    from app.services.mcp_client import mcp_hub
    await mcp_hub.start()
    return {"servers": mcp_hub.status()}


class MCPCall(BaseModel):
    tool: str
    args: Dict[str, Any] = {}


@app.post("/api/mcp/call")
async def mcp_call_endpoint(req: MCPCall):
    """Try an MCP tool from the UI. Same rules as the agent: non-read-only tools wait for approval."""
    if not req.tool.startswith("mcp__"):
        return {"success": False, "error": "Only MCP tools can be called here."}
    from app.agent.tools import execute_tool
    return await execute_tool(req.tool, req.args or {})


# ---- "A new version is out" (services/updates.py)

class UpdateSettings(BaseModel):
    enabled: bool


@app.get("/api/update")
async def update_status(force: bool = False):
    from app.services import updates
    return await updates.status(force=force)


@app.post("/api/update/settings")
async def update_settings(req: UpdateSettings):
    from app.services import updates
    updates.set_enabled(req.enabled)
    return {"success": True, "enabled": updates.enabled()}


# ---- What Xod knows about you (services/user_memory.py) ------------------------------------------------
class AboutFact(BaseModel):
    text: str
    topic: Optional[str] = "other"


class AboutGather(BaseModel):
    sources: List[str]


class AboutKeep(BaseModel):
    proposals: List[Dict[str, Any]]


@app.get("/api/about-you")
async def about_you():
    from app.services import user_memory
    d = user_memory.load()
    return {"facts": d["facts"], "settings": d["settings"], "sources": user_memory.SOURCES, "topics": user_memory.TOPICS,
            "gathered_at": d.get("gathered_at", {})}


@app.get("/api/memory/search")
async def memory_search_api(q: str = "", when: str = ""):
    """Search Xod's memory of past chats and tasks (Settings → What Xod knows about you)."""
    from app.services import recall
    if not q.strip():
        return {"results": [], "stats": recall.stats()}
    found = await recall.search(q, k=8, when=when or None)
    return {**found, "stats": recall.stats()}


@app.get("/api/about-you/turn/{turn_id}")
async def about_you_turn(turn_id: str):
    """What Xod picked up from one chat message (it may still be working on it: done=false)."""
    from app.services import user_memory
    return user_memory.turn_result(turn_id)


@app.post("/api/about-you/fact")
async def about_you_add(req: AboutFact):
    from app.services import user_memory
    f = user_memory.add(req.text, req.topic or "other", source="you added", pinned=True)
    if not f:
        raise HTTPException(400, "That's empty, or looks like a password or number Xod doesn't keep.")
    return f


@app.put("/api/about-you/fact/{fact_id}")
async def about_you_edit(fact_id: str, req: AboutFact):
    from app.services import user_memory
    f = user_memory.edit(fact_id, req.text)
    if not f:
        raise HTTPException(400, "Couldn't change that fact.")
    return f


@app.delete("/api/about-you/fact/{fact_id}")
async def about_you_forget(fact_id: str):
    from app.services import user_memory
    return {"forgot": user_memory.forget(fact_id)}


@app.delete("/api/about-you")
async def about_you_forget_all():
    from app.services import user_memory
    return {"forgot": user_memory.forget_all()}


@app.post("/api/about-you/settings")
async def about_you_settings(changes: Dict[str, Any]):
    from app.services import user_memory
    return user_memory.settings(changes)


@app.post("/api/about-you/gather")
async def about_you_gather(req: AboutGather):
    """Reads the sources the person ticked and proposes facts. Nothing is kept until /keep."""
    from app.services import user_memory
    allowed = {s["id"] for s in user_memory.SOURCES}
    return await user_memory.gather([s for s in req.sources if s in allowed])


@app.post("/api/about-you/keep")
async def about_you_keep(req: AboutKeep):
    from app.services import user_memory
    return {"kept": user_memory.keep(req.proposals)}


@app.post("/api/llm/warm")
async def llm_warm(voice: bool = False):
    """Called when the user starts typing or talking: loads the local model ahead of the question (services/warmup.py)."""
    from app.services import warmup
    return warmup.warm(spoken=voice)


# ---- Xod's look (static/js/mascot.js): which character the companion is drawn as

class CompanionSettings(BaseModel):
    character: str


@app.get("/api/companion")
async def companion_get():
    from app.services.storage import get_setting
    return {"character": get_setting("companion", "moon")}


@app.post("/api/companion")
async def companion_set(req: CompanionSettings):
    from app.services.storage import set_setting
    if not re.fullmatch(r"[a-z]{2,16}", req.character or ""):
        raise HTTPException(status_code=400, detail="Unknown look.")
    set_setting("companion", req.character)
    return {"success": True, "character": req.character}


# ---- Agent Mail (services/agent_mail.py): encrypted messages between Xods

class MailSetup(BaseModel):
    name: str
    relay: Optional[str] = None
    network: Optional[str] = None            # this computer's own post office: "network" or "tailscale"


class MailSend(BaseModel):
    to: Any
    text: str
    subject: Optional[str] = ""
    kind: Optional[str] = "message"
    thread: Optional[str] = None
    in_reply_to: Optional[str] = None


class MailDraftEdit(BaseModel):
    text: str
    subject: Optional[str] = None


class MailContactEdit(BaseModel):
    trust: Optional[str] = None
    nickname: Optional[str] = None
    verified: Optional[bool] = None
    auto_reply: Optional[bool] = None


def _mail_call(fn):
    from app.services.agent_mail import MailError
    async def run(*a, **kw):
        try:
            res = fn(*a, **kw)
            return (await res) if asyncio.iscoroutine(res) else res
        except MailError as e:
            return {"success": False, "error": str(e)}
    return run


@app.get("/api/mail/status")
async def mail_status():
    from app.services import agent_mail
    return agent_mail.status()


@app.post("/api/mail/setup")
async def mail_setup(req: MailSetup):
    from app.services import agent_mail
    return await agent_mail.setup(req.name, req.relay or "", req.network or "")


@app.get("/api/mail/networks")
async def mail_networks():
    """How other agents could reach this computer, for hosting your own post office."""
    from app.services import home_post
    return {"networks": await asyncio.to_thread(home_post.networks), "port": home_post.PORT}


@app.get("/api/mail/outbox")
async def mail_outbox():
    from app.services import agent_mail
    return {"outbox": agent_mail.outbox()}


@app.post("/api/mail/outbox/retry")
async def mail_outbox_retry():
    from app.services import agent_mail
    return await agent_mail.retry_outbox(force=True)


@app.post("/api/mail/check")
async def mail_check():
    from app.services import agent_mail
    return await agent_mail.check(notify=False)


@app.get("/api/mail/threads")
async def mail_threads(box: str = "inbox"):
    from app.services import agent_mail
    return {"threads": agent_mail.threads(box)}


@app.get("/api/mail/thread/{thread_id}")
async def mail_thread(thread_id: str):
    from app.services import agent_mail
    return {"messages": agent_mail.thread(thread_id)}


@app.post("/api/mail/send")
async def mail_send(req: MailSend):
    from app.services import agent_mail
    return await _mail_call(agent_mail.send)(req.to, req.text, req.subject or "", req.kind or "message",
                                              req.thread, req.in_reply_to, origin="you")


@app.post("/api/mail/drafts/{draft_id}/approve")
async def mail_draft_approve(draft_id: str):
    from app.services import agent_mail
    return await agent_mail.approve_draft(draft_id)


@app.post("/api/mail/drafts/{draft_id}/discard")
async def mail_draft_discard(draft_id: str):
    from app.services import agent_mail
    return {"success": agent_mail.discard_draft(draft_id)}


@app.patch("/api/mail/drafts/{draft_id}")
async def mail_draft_edit(draft_id: str, req: MailDraftEdit):
    from app.services import agent_mail
    return {"success": agent_mail.edit_draft(draft_id, req.text, req.subject)}


@app.post("/api/mail/messages/{mid}/run")
async def mail_run_request(mid: str):
    from app.services import agent_mail
    return await _mail_call(agent_mail.run_request)(mid)


@app.post("/api/mail/messages/{mid}/draft-reply")
async def mail_draft_reply(mid: str):
    from app.services import agent_mail
    return await _mail_call(agent_mail.draft_reply)(mid)


@app.get("/api/mail/contacts")
async def mail_contacts():
    from app.services import agent_mail
    return {"contacts": agent_mail.contacts()}


@app.get("/api/mail/lookup")
async def mail_lookup(address: str):
    from app.services import agent_mail, mail_crypto
    async def go():
        d = await agent_mail.lookup(address)
        return {"success": True, "address": d["address"], "safety_number": mail_crypto.safety_number(d["sign_pub"], d["box_pub"]),
                "contact": agent_mail.contact(d["address"])}
    return await _mail_call(go)()


@app.patch("/api/mail/contacts/{address}")
async def mail_contact_edit(address: str, req: MailContactEdit):
    from app.services import agent_mail
    return {"success": agent_mail.set_contact(address, req.trust, req.nickname, req.verified, req.auto_reply)}


@app.post("/api/mail/contacts/{address}/accept-keys")
async def mail_contact_accept_keys(address: str):
    from app.services import agent_mail
    return await _mail_call(agent_mail.accept_new_keys)(address)


# ---- One-click connected apps (services/connectors.py) and OAuth sign-in returns

class ConnectorConnect(BaseModel):
    use_cli: Optional[bool] = False
    token: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None


@app.get("/api/connectors")
async def connectors_list():
    from app.services import connectors
    return {"connectors": connectors.catalog()}


@app.post("/api/connectors/{cid}/connect")
async def connectors_connect(cid: str, req: ConnectorConnect, request: Request):
    from app.services import connectors
    redirect_uri = f"http://{request.headers.get('host', '127.0.0.1:8000')}/oauth/callback"
    return await connectors.connect(cid, redirect_uri, token=req.token, client_id=req.client_id,
                                    client_secret=req.client_secret, use_cli=bool(req.use_cli))


class ConnectorShare(BaseModel):
    on: bool


@app.post("/api/connectors/share-with-agents")
async def connectors_share(req: ConnectorShare):
    """Whether Crew jobs (Claude Code, Muse) get Xod's GitHub connection too."""
    from app.services import connectors
    return {"success": True, "on": connectors.share_with_agents(req.on)}


@app.delete("/api/connectors/{cid}")
async def connectors_disconnect(cid: str):
    from app.services import connectors
    from app.services.mcp_client import mcp_hub
    if cid in mcp_hub.conns:
        await mcp_hub.conns.pop(cid).stop()
    return {"success": connectors.disconnect(cid)}


@app.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(code: str = "", state: str = "", error: str = "", iss: Optional[str] = None):
    from app.services import mcp_oauth
    import html as _html
    ok = mcp_oauth.fail(state, error) if error else mcp_oauth.finish(code, state, iss) if code else False
    if ok and not error:
        title, text = "Connected", "You're signed in. You can close this tab and go back to Xod."
    elif error:
        title, text = "Not connected", f"The sign-in was cancelled or refused ({_html.escape(error[:80])}). You can close this tab."
    else:
        title, text = "Nothing to finish", "This sign-in link has expired or was already used. Start again from Xod."
    return HTMLResponse(f"""<!doctype html><meta charset="utf-8"><title>Xod · {title}</title>
<style>body{{margin:0;height:100vh;display:grid;place-items:center;background:#0f1222;color:#fffaf0;font:16px system-ui}}
div{{text-align:center;max-width:420px}}h1{{font:600 40px Georgia,serif;margin:.2em 0}}p{{color:#a39e97}}</style>
<div><div style="font-size:48px">🌙</div><h1>{title}</h1><p>{text}</p></div>""")


@app.post("/api/mcp/approve/{pid}")
async def mcp_approve_endpoint(pid: str):
    from app.services.mcp_client import mcp_hub
    return await mcp_hub.approve(pid)


@app.post("/api/mcp/decline/{pid}")
async def mcp_decline_endpoint(pid: str):
    from app.services.mcp_client import mcp_hub
    return {"success": mcp_hub.decline(pid)}


class RoutineCreate(BaseModel):
    name: Optional[str] = None
    instructions: str
    schedule: Optional[Dict[str, Any]] = None
    source: Optional[str] = None      # "chat:<session id>" or "task:<task id>": learn the steps from it


class RoutineUpdate(BaseModel):
    name: Optional[str] = None
    instructions: Optional[str] = None
    schedule: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    steps: Optional[List[str]] = None


class RoutineFromTemplate(BaseModel):
    template_id: str


@app.get("/api/routines")
async def routines_list_endpoint():
    from app.services import routines
    return {"routines": routines.list_all()}


@app.post("/api/routines")
async def routines_create_endpoint(req: RoutineCreate):
    from app.services import routines
    try:
        return {"success": True, "routine": routines.create(req.name or "", req.instructions, req.schedule, req.source)}
    except ValueError as e:
        return {"success": False, "error": str(e)}


@app.patch("/api/routines/{rid}")
async def routines_update_endpoint(rid: str, req: RoutineUpdate):
    from app.services import routines
    r = routines.update(rid, **req.model_dump())
    return {"success": bool(r), "routine": r}


@app.post("/api/routines/{rid}/run")
async def routines_run_endpoint(rid: str):
    from app.services import routines
    return routines.run_now(rid)


@app.delete("/api/routines/{rid}")
async def routines_delete_endpoint(rid: str):
    from app.services import routines
    return {"success": routines.delete(rid)}


@app.get("/api/routines/templates")
async def routines_templates_endpoint():
    """Ready-made routines to start from (services/routines.py TEMPLATES)."""
    from app.services import routines
    return {"templates": routines.list_templates()}


@app.post("/api/routines/from-template")
async def routines_from_template_endpoint(req: RoutineFromTemplate):
    from app.services import routines
    try:
        return {"success": True, "routine": routines.create_from_template(req.template_id)}
    except ValueError as e:
        return {"success": False, "error": str(e)}


@app.get("/api/routines/suggest")
async def routines_suggest_endpoint(source: str):
    """Prefill for "Save as routine": the request and the steps that worked."""
    from app.services import routines
    from app.services.task_queue import task_queue
    instructions, name = "", ""
    if source.startswith("task:"):
        t = task_queue.get(source.split(":", 1)[1]) or {}
        instructions, name = t.get("request", ""), t.get("title", "")
    elif source.startswith("chat:"):
        msgs = get_session_messages(source.split(":", 1)[1])
        first = next((m for m in msgs if m.get("role") == "user"), None)
        instructions = (first or {}).get("content", "")
        name = (get_session(source.split(":", 1)[1]) or {}).get("title", "")
    return {"name": name[:80], "instructions": instructions, "steps": routines.steps_from(source)}


@app.get("/api/activity")
async def activity_list_endpoint(since: Optional[float] = None, limit: int = 200):
    """What Xod did (newest first), with which entries can be undone."""
    from app.services import activity
    return {"items": activity.recent(limit=min(limit, 1000), since=since)}


class UndoRequest(BaseModel):
    force: bool = False


@app.post("/api/activity/{entry_id}/undo")
async def activity_undo_endpoint(entry_id: str, req: UndoRequest):
    from app.services import activity
    return await asyncio.to_thread(activity.undo, entry_id, req.force)


@app.get("/api/doctor")
async def doctor_check():
    """Health of Xod's machinery (chat model, memory, browser, media, tasks, keys, recent errors)."""
    from app.services.doctor import doctor
    return await doctor.check()


class DoctorFixRequest(BaseModel):
    fix: str


@app.get("/api/self-edit")
async def self_edit_status():
    """Self-editing gate: tiers of the chat/editor model, settings, protected code, and the change history."""
    from app.services import self_edit
    return self_edit.status()


class SelfEditSettingsRequest(BaseModel):
    enabled: Optional[bool] = None
    auto_apply_green: Optional[bool] = None
    editor: Optional[Dict[str, Any]] = None
    clear_editor: Optional[bool] = None
    tier_overrides: Optional[Dict[str, str]] = None


@app.post("/api/self-edit/settings")
async def self_edit_settings(req: SelfEditSettingsRequest):
    from app.services import self_edit
    upd = {k: v for k, v in req.dict().items() if v is not None and k != "clear_editor"}
    if req.clear_editor:
        upd["editor"] = None
    self_edit.save_settings(upd)
    return self_edit.status()


class SelfEditRequest(BaseModel):
    goal: str
    files: Optional[List[str]] = None


@app.post("/api/self-edit/request")
async def self_edit_request(req: SelfEditRequest):
    from app.services import self_edit
    return await self_edit.request_change(req.goal, req.files, requested_by="you")


@app.post("/api/self-edit/{change_id}/{action}")
async def self_edit_action(change_id: str, action: str):
    from app.services import self_edit
    fn = {"approve": self_edit.approve, "reject": self_edit.reject, "revert": self_edit.revert}.get(action)
    if not fn:
        return {"ok": False, "detail": f"Unknown action {action}"}
    return {**fn(change_id), "status": self_edit.status()}


@app.get("/api/self-edit/{change_id}")
async def self_edit_change(change_id: str):
    from app.services import self_edit
    c = self_edit.get_change(change_id)
    return self_edit.public_change(c) if c else {"error": "not found"}


@app.post("/api/doctor/fix")
async def doctor_fix(req: DoctorFixRequest):
    from app.services.doctor import doctor
    res = await doctor.fix(req.fix)
    return {**res, "report": await doctor.check()}


async def _counted(gen):
    """Counts replies in flight, so self-edits wait for quiet before the dev server reloads."""
    from app.services import self_edit
    self_edit.active_streams += 1
    try:
        async for chunk in gen:
            yield chunk
    finally:
        self_edit.active_streams -= 1


@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    """Streaming chat endpoint using Server-Sent Events (SSE) with persistent DB storage."""
    # Ensure session exists or create new
    session_id = request.session_id
    is_new_session = False
    
    last_msg = request.messages[-1].get("content", "Research Session") if request.messages else "Research Session"
    if isinstance(last_msg, list):
        text_parts = [p.get("text", "") for p in last_msg if isinstance(p, dict) and p.get("type") == "text"]
        prompt_preview = " ".join(text_parts) if text_parts else "Visual Intelligence Audit"
    else:
        prompt_preview = str(last_msg)

    if not session_id or not get_session(session_id):
        title = prompt_preview[:35] + ("..." if len(prompt_preview) > 35 else "")
        session = create_session(title=title, persona=request.persona or "ultimate_researcher")
        session_id = session["id"]
        is_new_session = True
    else:
        session = get_session(session_id)

    # Save user message to persistent storage
    if request.messages and request.messages[-1].get("role") == "user":
        user_text = prompt_preview if isinstance(last_msg, str) else json.dumps(last_msg)
        add_message(session_id=session_id, role="user", content=user_text)

    researcher = AgenticResearcher(persona=request.persona or "ultimate_researcher")

    async def event_generator():
        from app.services import activity
        activity.origin.set(f"chat:{session_id}")   # tool calls in this reply are logged against this chat
        from app.services import llm_routing
        cloud_ep = llm_routing.cloud_endpoint() if request.route == "cloud" else None
        if request.route == "cloud" and not cloud_ep:
            yield f"data: {json.dumps({'type': 'error', 'content': 'No cloud model is set up. Add an API key in Settings first.'})}\n\n"
            return
        route = llm_routing.start_turn(cloud_ep)
        yield f"data: {json.dumps({'type': 'session_info', 'session_id': session_id, 'is_new': is_new_session})}\n\n"
        from app.services import soul
        soul.note_busy(+1)
        try:   # what you said is how the companion learns (thanks, corrections, your interests, your name)
            felt = soul.learn_from_message(prompt_preview if isinstance(last_msg, str) else "")
            if request.voice:
                felt = soul.event("spoke") or felt
            if felt:
                yield f"data: {json.dumps({'type': 'soul', 'kind': felt.get('kind'), 'state': {k: felt[k] for k in ('stage', 'stage_name', 'bond', 'feeling', 'mood', 'stardust')}, 'evolved': felt.get('evolved')})}\n\n"
        except Exception as e:
            logger.debug(f"soul: {e}")

        # What you say about yourself is how Xod gets to know you (services/user_memory.py). On a cloud model the
        # small fact-finding call runs alongside the answer; on a local one it waits until the answer is done,
        # so it doesn't slow it down.
        from app.services import user_memory
        said = last_msg if isinstance(last_msg, str) else ""
        learn_wanted, learn_turn, local_model = False, None, True
        try:
            learn_wanted = user_memory.worth_a_look(said) or bool(user_memory.FORGET_ALL_RE.match(said))
            local_model = llm_service.get_endpoint_info().get("provider") in ("lm_studio", "ollama", "xod", "local") and not route.endpoint
            if learn_wanted and not local_model:
                learn_turn = user_memory.start_learning(said)
        except Exception as e:
            logger.debug(f"memory: {e}")

        assistant_content = ""
        assistant_reasoning = ""
        tool_events = []
        # This turn's model calls get labelled by how it goes (services/turn_quality.py): your next message
        # (a correction, thanks, the same question again) and Xod's own check of the answer.
        from app.services import turn_quality
        turn = None
        try:
            turn = turn_quality.begin(session_id, said)
        except Exception as e:
            logger.debug(f"turn: {e}")

        try:
            async for event in researcher.run_research_stream(
                messages=request.messages,
                active_workspace_path=request.active_workspace_path,
                attached_document=request.attached_document,
                spoken=bool(request.voice),
            ):
                event_type = event.get("type")
                if event_type == "content":
                    assistant_content += event.get("content", "")
                elif event_type == "reasoning":
                    assistant_reasoning += event.get("content", "")
                elif event_type in (
                    "tool_call", "tool_result", "scorecard",
                    "swarm_plan", "subagent_status", "subagent_tool_call",
                    "subagent_tool_result", "swarm_complete", "build_done", "jobs"
                ):
                    tool_events.append(event)
                if event_type == "build_done" and (event.get("check") or {}).get("ok"):
                    try:
                        grown = soul.event("built", event.get("title") or "")
                        if grown.get("evolved"):
                            yield f"data: {json.dumps({'type': 'soul', 'state': {k: grown[k] for k in ('stage', 'stage_name', 'bond', 'feeling', 'mood')}, 'evolved': grown['evolved']})}\n\n"
                    except Exception:
                        pass
                if event_type == "build_done" and (event.get("project") or {}).get("slug"):
                    try:
                        from app.services.project_storage import project_storage_service as project_storage
                        project_storage.assign_session_to_project(session_id, event["project"]["slug"])
                    except Exception as e:
                        logger.warning(f"couldn't file the chat under the new project: {e}")

                yield f"data: {json.dumps(event)}\n\n"

            # A finished answer leaves a little stardust (js/joy.js: give it back to Xod as a treat).
            if assistant_content.strip():
                try:
                    done = soul.event("success")
                    if done.get("earned") or done.get("evolved"):
                        yield f"data: {json.dumps({'type': 'soul', 'state': {k: done[k] for k in ('stage', 'stage_name', 'bond', 'feeling', 'mood', 'stardust')}, 'earned': done.get('earned', 0), 'evolved': done.get('evolved')})}\n\n"
                except Exception:
                    pass

            if learn_wanted:
                # Never holds up the chat: a cloud model has usually finished by now (it ran alongside the answer);
                # a local one starts now. Either way the page asks for the result and shows it when it's in.
                try:
                    if learn_turn is None:
                        learn_turn = user_memory.start_learning(said)
                    else:
                        task = user_memory._turns.get(learn_turn, {}).get("task")
                        if task is not None:
                            await asyncio.wait([task], timeout=2)
                    got = user_memory.turn_result(learn_turn)
                    if got.get("done"):
                        if got.get("learned") or got.get("forgot"):
                            yield f"data: {json.dumps({'type': 'memory', **got})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'memory_pending', 'turn': learn_turn})}\n\n"
                except Exception as e:
                    logger.debug(f"memory: {e}")

            # Smart routing: say which model answered and what it cost, or offer a stronger one.
            if route.endpoint:
                yield f"data: {json.dumps({'type': 'route_info', 'model': route.endpoint.get('label') or route.endpoint['model'], 'reason': route.escalated_reason, 'cost_usd': round(route.cost_usd, 4), 'calls': route.cloud_calls})}\n\n"
            elif llm_routing.should_offer():
                ep = llm_routing.cloud_endpoint()
                yield f"data: {json.dumps({'type': 'escalation_offer', 'model': ep['label'], 'est_cost_usd': llm_routing.estimate_turn_cost(request.messages, ep), 'failures': route.failures})}\n\n"

        except Exception as e:
            logger.exception(f"Chat stream exception: {e}")
            err_event = {"type": "error", "content": f"Server error: {str(e)}"}
            yield f"data: {json.dumps(err_event)}\n\n"
        finally:
            soul.note_busy(-1)
            try:
                turn_quality.finish(turn, assistant_content, [e.get("name") for e in tool_events if e.get("type") == "tool_call" and e.get("name")])
            except Exception as e:
                logger.debug(f"turn: {e}")
            try:   # this turn becomes part of Xod's memory of your chats (services/recall.py)
                from app.services import recall
                recall.schedule_index(4)
            except Exception:
                pass
            # Get ready for the next question while this answer is read: re-read the router's prompt if a long
            # prompt pushed it out of the model server's cache, and keep the browser up (services/warmup.py).
            try:
                from app.services import warmup
                warmup.warm(spoken=bool(request.voice), after_answer=True)
            except Exception:
                pass
            # Persist assistant response to SQLite database
            if assistant_content or assistant_reasoning or tool_events:
                add_message(
                    session_id=session_id,
                    role="assistant",
                    content=assistant_content,
                    reasoning=assistant_reasoning,
                    tool_events=tool_events
                )
                try:
                    from app.services.trace_recorder import record_trace
                    last_user_query = ""
                    for m in reversed(request.messages):
                        if m.get("role") == "user":
                            last_user_query = m.get("content", "")
                            break
                    record_trace(
                        source="chat",
                        query=last_user_query,
                        tool_calls=[{"name": e.get("name") or e.get("tool", "unknown"), "event": e} for e in tool_events if e.get("type") in ("tool_call", "subagent_tool_call")],
                        outcome=assistant_content[:1000],
                        success=True,
                        model=settings.llm_model
                    )
                except Exception:
                    pass

    return StreamingResponse(
        _counted(event_generator()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@app.get("/api/config")
async def get_config():
    """Returns safe masked configuration and status."""
    key = settings.trustmrr_api_key
    masked_key = ""
    if key:
        masked_key = key[:7] + "..." + key[-4:] if len(key) > 11 else "******"

    you_key = settings.you_api_key
    masked_you_key = ""
    if you_key:
        masked_you_key = you_key[:7] + "..." + you_key[-4:] if len(you_key) > 11 else "******"

    yt_key = settings.youtube_api_key
    masked_yt_key = ""
    if yt_key:
        masked_yt_key = yt_key[:7] + "..." + yt_key[-4:] if len(yt_key) > 11 else "******"

    tav_key = settings.tavily_api_key
    masked_tav_key = ""
    if tav_key:
        masked_tav_key = tav_key[:7] + "..." + tav_key[-4:] if len(tav_key) > 11 else "******"

    return {
        "trustmrr_configured": bool(key),
        "trustmrr_masked_key": masked_key,
        "you_configured": bool(you_key),
        "you_masked_key": masked_you_key,
        "tavily_configured": bool(tav_key),
        "tavily_masked_key": masked_tav_key,
        "youtube_configured": bool(yt_key),
        "youtube_masked_key": masked_yt_key,
        "llm_provider": settings.llm_provider,
        "llm_base_url": settings.llm_base_url,
        "llm_model": settings.llm_model,
        "has_openai_key": bool(settings.openai_api_key),
        "has_anthropic_key": bool(settings.anthropic_api_key),
        "has_gemini_key": bool(settings.gemini_api_key),
        "has_groq_key": bool(settings.groq_api_key),
        "has_openrouter_key": bool(settings.openrouter_api_key),
        "has_fireworks_key": bool(settings.fireworks_api_key),
        "has_tavily_key": bool(tav_key),
        "has_huggingface_token": bool(settings.huggingface_token),
        "huggingface_masked_token": (settings.huggingface_token[:4] + "..." + settings.huggingface_token[-4:]) if len(settings.huggingface_token) > 8 else ("Configured" if settings.huggingface_token else "")
    }

@app.post("/api/config")
async def update_config(req: ConfigUpdateRequest):
    """Updates settings and persists to .env."""
    if req.trustmrr_api_key is not None:
        key_val = req.trustmrr_api_key.strip()
        update_env_variable("TRUSTMRR_API_KEY", key_val)
        trustmrr_client.set_api_key(key_val)

    if req.you_api_key is not None:
        you_val = req.you_api_key.strip()
        update_env_variable("YOU_API_KEY", you_val)
        you_client.set_api_key(you_val)

    if req.tavily_api_key is not None:
        tav_val = req.tavily_api_key.strip()
        update_env_variable("TAVILY_API_KEY", tav_val)
        tavily_client.set_api_key(tav_val)

    if req.youtube_api_key is not None:
        yt_val = req.youtube_api_key.strip()
        update_env_variable("YOUTUBE_API_KEY", yt_val)
        youtube_client.set_api_key(yt_val)

    picked = [(new, getattr(settings, field)) for new, field in ((req.llm_provider, "llm_provider"), (req.llm_base_url, "llm_base_url"),
                                                                 (req.llm_model, "llm_model")) if new is not None]
    if any(new != old for new, old in picked):    # the person changed the model here: it's theirs, Xod won't swap it
        from app.services.storage import set_setting
        from app.services.setup_wizard import CHOSEN_BY_KEY
        set_setting(CHOSEN_BY_KEY, "you")

    if req.llm_provider is not None:
        update_env_variable("LLM_PROVIDER", req.llm_provider)

    if req.llm_base_url is not None:
        update_env_variable("LLM_BASE_URL", req.llm_base_url)

    if req.llm_model is not None:
        update_env_variable("LLM_MODEL", req.llm_model)

    if req.openai_api_key is not None:
        update_env_variable("OPENAI_API_KEY", req.openai_api_key.strip())

    if req.gemini_api_key is not None:
        update_env_variable("GEMINI_API_KEY", req.gemini_api_key.strip())

    if req.groq_api_key is not None:
        update_env_variable("GROQ_API_KEY", req.groq_api_key.strip())

    if req.anthropic_api_key is not None:
        update_env_variable("ANTHROPIC_API_KEY", req.anthropic_api_key.strip())

    if req.openrouter_api_key is not None:
        update_env_variable("OPENROUTER_API_KEY", req.openrouter_api_key.strip())

    if req.fireworks_api_key is not None:
        update_env_variable("FIREWORKS_API_KEY", _clean_api_key(req.fireworks_api_key))

    if req.huggingface_token is not None:
        hf_val = req.huggingface_token.strip()
        update_env_variable("HUGGINGFACE_TOKEN", hf_val)
        update_env_variable("HF_TOKEN", hf_val)
        media_model_manager.set_hf_token(hf_val)

    from app.services import local_servers, setup_wizard
    if req.llm_provider is not None:
        local_servers.after_switch(req.llm_provider)     # not the built-in engine any more: free its memory now
    switched = setup_wizard.prefer_key()                  # a key just saved wins while the model is still Xod's pick
    msg = f"Settings updated. Xod now answers with {switched['label']} (your API key)." if switched["switched"] else "Settings updated successfully."
    return {"status": "success", "message": msg, "switched_to": switched.get("provider") if switched["switched"] else None}

class TavilyTestRequest(BaseModel):
    tavily_api_key: Optional[str] = None

@app.post("/api/tavily/test")
async def test_tavily_connection(payload: Optional[TavilyTestRequest] = None):
    """Tests Tavily AI API key and connectivity."""
    k = payload.tavily_api_key if payload else None
    res = await tavily_client.verify_key(k)
    if res.get("valid") and (k or tavily_client.api_key):
        clean_key = (k or tavily_client.api_key).strip()
        settings.tavily_api_key = clean_key
        update_env_variable("TAVILY_API_KEY", clean_key)
        tavily_client.set_api_key(clean_key)
    return res

class TavilySearchRequest(BaseModel):
    query: str
    count: int = 5
    search_depth: str = "basic"
    include_answer: bool = True

@app.post("/api/tavily/search")
async def search_tavily_endpoint(req: TavilySearchRequest):
    """Direct search endpoint for Tavily AI."""
    return await tavily_client.search(
        query=req.query,
        count=req.count,
        search_depth=req.search_depth,
        include_answer=req.include_answer
    )

class YouTubeTestRequest(BaseModel):
    youtube_api_key: Optional[str] = None

@app.post("/api/youtube/test")
async def test_youtube_key_endpoint(req: Optional[YouTubeTestRequest] = None):
    """Tests YouTube Data API v3 key validity."""
    k = req.youtube_api_key if req else None
    return await youtube_client.test_key(k)

@app.get("/api/youtube/channel/{identifier}")
async def youtube_channel_endpoint(identifier: str):
    """Inspects YouTube channel statistics and recent uploads."""
    return await youtube_client.inspect_channel(identifier)

class MediaDownloadRequest(BaseModel):
    url_or_query: str
    format_type: str = "auto"
    output_dir: Optional[str] = None

@app.post("/api/media/download")
async def media_download_endpoint(req: MediaDownloadRequest):
    """Downloads YouTube video or extracts audio song directly to host ~/Downloads."""
    from app.services.media_downloader import media_downloader_service
    return await media_downloader_service.download(
        url_or_query=req.url_or_query,
        format_type=req.format_type,
        output_dir=req.output_dir
    )

@app.post("/api/media/info")
async def media_info_endpoint(req: MediaDownloadRequest):
    """Fetches media metadata (title, uploader, duration, thumbnail) without downloading."""
    from app.services.media_downloader import media_downloader_service
    return await media_downloader_service.get_info(req.url_or_query)

@app.get("/api/swarm/status")
async def get_swarm_status_endpoint():
    """Returns available model tiers and subagent specialist profiles."""
    from app.agent.swarm import SPECIALIST_ARCHETYPES
    tiers = llm_service.get_available_tiers()
    return {
        "conductor": "Xod",
        "tiers": tiers,
        "specialists": SPECIALIST_ARCHETYPES
    }

def _clean_api_key(key: str) -> str:
    """A pasted key, minus what copying tends to add: spaces and line breaks anywhere, invisible characters,
    quotes (straight or curly), and a leading "Bearer " or "NAME=" from a config line."""
    k = re.sub(r"[\s\u200b-\u200f\u2028\u2029\u2060\ufeff\u00a0]", "", key or "")
    k = k.strip("'\"\u2018\u2019\u201c\u201d`")
    if k.lower().startswith("bearer"):
        k = k[6:]
    m = re.match(r"^[A-Z_]+=(.+)$", k)
    if m:
        k = m.group(1).strip("'\"")
    return k


@app.post("/api/fireworks/test")
async def test_fireworks_connection(payload: Optional[Dict[str, str]] = None):
    """Tests the Fireworks AI API key and model connectivity."""
    key = (payload.get("fireworks_api_key") if payload else None) or settings.fireworks_api_key
    if not key or not key.strip():
        return {"valid": False, "message": "No Fireworks API key provided."}

    clean_key = _clean_api_key(key)
    if not clean_key:
        return {"valid": False, "message": "No Fireworks API key provided."}
    tested_saved = not (payload and (payload.get("fireworks_api_key") or "").strip())
    # What was tested, without showing the key: its first characters and its length.
    shape = f"the {'saved' if tested_saved else 'pasted'} key starts with \"{clean_key[:3]}\" and is {len(clean_key)} characters long"
    if not clean_key.startswith("fw_"):
        return {"valid": False, "message": (
            f"That doesn't look like a Fireworks API key: {shape}, but Fireworks keys start with \"fw_\" and are about 25-50 "
            "characters. You may have copied the key's name or ID instead of the key itself. On fireworks.ai open "
            "Settings → API Keys, create a key and copy the secret it shows (it's only shown once).")}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                "https://api.fireworks.ai/inference/v1/models",
                headers={"Authorization": f"Bearer {clean_key}"}
            )
            if res.status_code == 200:
                data = res.json()
                models = data.get("data", [])
                if not tested_saved:     # the saved one stays as it is (it may be a 1Password reference)
                    settings.fireworks_api_key = clean_key
                    update_env_variable("FIREWORKS_API_KEY", clean_key)
                return {
                    "valid": True,
                    "message": f"Connected to Fireworks AI! ({len(models)} models available)",
                    "models_count": len(models)
                }
            elif res.status_code in (401, 403):
                err_detail = ""
                try:
                    err_json = res.json()
                    err_detail = err_json.get("error", {}).get("message") or err_json.get("message", "")
                except Exception:
                    pass
                msg = f"Fireworks turned this key down (HTTP {res.status_code}"
                msg += f": {err_detail.rstrip('.')}). " if err_detail else "). "
                msg += (f"For reference, {shape}. If that matches your key, it may have been deleted or not finished "
                        "creating on fireworks.ai; create a new one under Settings → API Keys and paste the whole secret.")
                return {"valid": False, "message": msg}
            else:
                err_detail = ""
                try:
                    err_json = res.json()
                    err_detail = err_json.get("error", {}).get("message") or err_json.get("message", "")
                except Exception:
                    err_detail = res.text[:120]
                return {"valid": False, "message": f"Fireworks returned HTTP {res.status_code}: {err_detail}"}
    except Exception as e:
        return {"valid": False, "message": f"Connection error: {str(e)}"}

@app.post("/api/trustmrr/test")
async def test_trustmrr_connection():
    """Tests the TrustMRR API key."""
    res = await trustmrr_client.verify_key()
    return res

@app.post("/api/you/test")
async def test_you_connection():
    """Tests the You.com API key."""
    res = await you_client.verify_key()
    return res

@app.get("/api/trustmrr/categories")
async def list_categories():
    return {"categories": VALID_CATEGORIES, "sorts": VALID_SORTS}

@app.get("/api/trustmrr/deals")
async def get_deals(category: Optional[str] = None, max_multiple: Optional[float] = None):
    """Direct deal screening endpoint."""
    res = await trustmrr_client.get_acquisition_deals(category=category, max_multiple=max_multiple)
    return res

@app.get("/api/llm/models")
async def get_llm_models(provider: Optional[str] = None):
    """Lists models available from the currently configured or requested provider."""
    models = await llm_service.list_models(provider_override=provider)
    loaded_model = None
    for m in models:
        if m.get("is_loaded"):
            loaded_model = m.get("id")
            break
    return {
        "models": models,
        "current_model": settings.llm_model,
        "loaded_model": loaded_model
    }

@app.get("/api/lmstudio/status")
async def get_lmstudio_status():
    """Fetches real-time status and loaded instances from LM Studio native v1 API. "active" says which model answers
    chats: with a cloud provider set up, that one (the header shows it as ready even when LM Studio is off)."""
    info = llm_service.get_endpoint_info()
    # LM Studio's details only matter when it's the provider (asking a port nobody listens on takes ~2 s on Windows).
    status = await llm_service.get_lmstudio_status() if info.get("provider") in ("lm_studio", "xod") else {
        "online": False, "server_url": llm_service.get_lmstudio_native_base(), "loaded_model": None, "models": [], "total_models": 0}
    from app.services.llm import CLOUD_PROVIDERS
    if info.get("provider") in CLOUD_PROVIDERS:
        from app.services import keychain
        status = {**status, "active": {"cloud": True, "provider": info["provider"], "ready": info.get("api_key") not in ("", "missing", None),
                                       "model": str(info.get("model") or "").split("/")[-1], "keys_locked": bool(keychain.problem())}}
    elif info.get("provider") == "ollama":
        from app.services.ollama_context import status as ollama_status
        status = {**status, "active": await ollama_status(info["base_url"], info["model"])}
    elif info.get("provider") == "local":
        from app.services.autoconnect import local_status
        status = {**status, "active": await local_status(info["base_url"], info["model"])}
    elif info.get("provider") == "xod":
        # The built-in engine: ready when its model is on this computer (it starts on the first chat). LM Studio's state
        # isn't this one's, so it doesn't decide what the header says.
        from app.services import local_engine as le
        repo = str(info.get("model") or "")
        entry = le.catalog_entry(repo) or {}
        ready = le.is_downloaded(repo)
        status = {**status, "active": {"provider": "local", "server": "Built-in", "model": entry.get("label") or repo.split("/")[-1],
                                       "ready": ready, "problem": None if ready else "The built-in model isn't downloaded yet (Settings → Models)."}}
    return status

@app.get("/api/crew")
async def crew_list():
    """The Crew (services/crew.py): jobs Xod hands to coding agents (Muse, natively), their steps and what they ask."""
    from app.services import crew
    return {"jobs": crew.jobs()}


class CrewStart(BaseModel):
    task: str
    folder: Optional[str] = ""
    title: Optional[str] = ""
    agent: Optional[str] = ""
    isolate: Optional[bool] = None


@app.post("/api/crew")
async def crew_start(req: CrewStart):
    from app.services import crew
    try:
        return {"success": True, "job": await crew.start(req.task, req.folder or "", req.title or "", agent=req.agent or "", isolate=req.isolate)}
    except crew.CrewError as e:
        return {"success": False, "error": str(e)}


@app.get("/api/crew/settings/muse-tools")
async def crew_muse_tools():
    """Muse's own tools (MCP servers in its settings) and which ones Crew jobs get (each slows Muse's start)."""
    from app.services import crew
    return crew.muse_tools()


class CrewTools(BaseModel):
    names: List[str]


@app.post("/api/crew/settings/muse-tools")
async def crew_set_muse_tools(req: CrewTools):
    from app.services import crew
    return crew.set_muse_tools(req.names)


@app.get("/api/crew/{jid}")
async def crew_job(jid: str):
    from app.services import crew
    try:
        return {"success": True, "job": crew._view(crew.job(jid), steps=400)}
    except crew.CrewError as e:
        return {"success": False, "error": str(e)}


class CrewAct(BaseModel):
    text: Optional[str] = ""
    steer: Optional[bool] = False
    approval: Optional[str] = ""
    decision: Optional[str] = ""
    question: Optional[str] = ""
    answers: Optional[List[Dict[str, Any]]] = None


@app.post("/api/crew/{jid}/{action}")
async def crew_act(jid: str, action: str, req: CrewAct):
    """message, decide, answer, interrupt, stop, and for a job's own copy review, merge, pr, discard: what you do on the
    Crew board (it's you, so it goes straight through)."""
    from app.services import crew
    try:
        if action == "message":
            return {"success": True, "job": await crew.message(jid, req.text or "", bool(req.steer))}
        if action == "decide":
            return {"success": True, "job": await crew.decide(jid, req.approval or "", req.decision or "once")}
        if action == "answer":
            return {"success": True, "job": await crew.answer(jid, req.question or "", req.answers or [])}
        if action == "interrupt":
            return {"success": True, "job": await crew.interrupt(jid)}
        if action == "stop":
            return {"success": True, "job": await crew.stop(jid)}
        if action == "review":
            return await crew.review(jid)
        if action == "merge":
            return await crew.merge(jid)
        if action == "pr":
            return await crew.pull_request(jid)
        if action == "discard":
            return await crew.discard(jid)
        return {"success": False, "error": f"Unknown action {action}."}
    except crew.CrewError as e:
        return {"success": False, "error": str(e)}


@app.get("/api/abilities")
async def abilities_status():
    """Everyday abilities on this Mac (services/abilities.py): Messages, Location, Calendar, Home, Reminders, Contacts."""
    from app.services import abilities
    return await asyncio.to_thread(abilities.status)


@app.post("/api/abilities/{aid}/allow")
async def abilities_allow(aid: str):
    """The person's Allow: switches This Mac on and has macOS ask (or opens the right page of System Settings)."""
    from app.services import abilities
    return await abilities.allow(aid)


@app.get("/api/scorecard")
async def scorecard_get(days: int = 7):
    """How Xod is doing (services/scorecard.py): chat turns, model calls, Crew jobs and the routing check."""
    from app.services import scorecard
    return await asyncio.to_thread(scorecard.card, max(1, min(days, 90)))


@app.post("/api/evals/run")
async def evals_run():
    """Replays the routing cases through the router and planner with the model Xod uses (services/evals.py)."""
    from app.services import evals
    if evals.status():
        return {"success": False, "error": "A check is already running."}
    async def all_suites():
        for name in evals.suites():
            await evals.run(name, label="from the Scorecard")
    asyncio.create_task(all_suites())
    return {"success": True, "started": True}


@app.get("/api/agents/{agent}/setup")
async def agent_setup_status(agent: str):
    """Setting up a terminal agent (services/agent_clis.py): installed, signed in, answering, and what's next."""
    from app.services import agent_clis
    try:
        return await asyncio.to_thread(agent_clis.setup_status, agent)
    except agent_clis.CliError as e:
        return {"success": False, "error": str(e)}


class AgentSetupAction(BaseModel):
    code: Optional[str] = ""
    method: Optional[str] = ""


@app.post("/api/agents/{agent}/setup/{action}")
async def agent_setup_action(agent: str, action: str, req: AgentSetupAction):
    """The person's own click in Agent setup: install and update run here because the click is their OK."""
    from app.services import agent_clis as ac
    try:
        if action == "install":
            return await ac.install(agent)
        if action == "login":
            return await ac.login(agent, "console" if req.method == "console" else "")
        if action == "code":
            return await asyncio.to_thread(ac.login_code, agent, req.code or "")
        if action == "test":
            return await ac.test(agent)
        if action == "update":
            return await ac.update(agent)
        if action == "doctor":
            return await ac.doctor(agent)
    except ac.CliError as e:
        return {"success": False, "error": str(e)}
    return {"success": False, "error": f"Unknown step {action!r}."}


@app.get("/api/terminals")
async def terminals_list():
    """Xod's terminal sessions (services/agent_clis.py): agents and programs, with their state, and the agents installed."""
    from app.services import agent_clis
    found = await asyncio.to_thread(agent_clis.find)
    return {"sessions": agent_clis.sessions(), "agents": [{k: a[k] for k in ("id", "name", "installed", "signed_in")} for a in found["agents"]]}


class TerminalStart(BaseModel):
    agent: Optional[str] = ""
    command: Optional[str] = ""
    folder: Optional[str] = ""
    name: Optional[str] = ""
    new: Optional[bool] = False


@app.post("/api/terminals")
async def terminals_start(req: TerminalStart):
    from app.services import agent_clis
    try:
        return await agent_clis.session_start(agent=req.agent or "", command=req.command or "", folder=req.folder or "",
                                              name=req.name or "", new=bool(req.new))
    except agent_clis.CliError as e:
        return {"success": False, "error": str(e)}


@app.get("/api/terminals/{sid}")
async def terminals_screen(sid: str):
    from app.services import agent_clis
    try:
        s = agent_clis._session(sid)
    except agent_clis.CliError as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "session": s.id, "name": s.name, "agent": s.label, "state": s.state(), "running": s.running(),
            "folder": s.cwd, "screen": s.screen()[-20000:]}


class TerminalInput(BaseModel):
    text: Optional[str] = ""
    enter: Optional[bool] = True
    key: Optional[str] = ""


@app.post("/api/terminals/{sid}/input")
async def terminals_input(sid: str, req: TerminalInput):
    """What you type in the Terminals panel goes straight in (it's you typing, so no question is held back)."""
    from app.services import agent_clis
    try:
        s = agent_clis._session(sid)
        if req.key:
            return agent_clis.session_key(s.id, req.key)
        s.write(req.text or "")
        if req.enter:
            await asyncio.sleep(agent_clis.RETURN_PAUSE)
            s.write("\r")
        return {"success": True}
    except agent_clis.CliError as e:
        return {"success": False, "error": str(e)}


@app.delete("/api/terminals/{sid}")
async def terminals_stop(sid: str):
    from app.services import agent_clis
    try:
        return agent_clis.session_stop(sid)
    except agent_clis.CliError as e:
        return {"success": False, "error": str(e)}


@app.get("/api/terminal-command")
async def terminal_command_status():
    """The `xod` command for the terminal (services/terminal_command.py): installed or not, and where."""
    from app.services import terminal_command
    return terminal_command.status()


@app.post("/api/terminal-command")
async def terminal_command_install():
    from app.services import terminal_command
    return terminal_command.install()


@app.delete("/api/terminal-command")
async def terminal_command_uninstall():
    from app.services import terminal_command
    return terminal_command.uninstall()


@app.get("/api/desktop/status")
async def desktop_status():
    """The real screen, mouse and keyboard (services/desktop.py): macOS permissions, the apps Xod may use, the task running."""
    from app.services import desktop
    return desktop.status()


@app.post("/api/desktop/permissions")
async def desktop_permissions():
    from app.services import desktop
    return desktop.request_permissions()


@app.post("/api/desktop/stop")
async def desktop_stop():
    from app.services import desktop
    return desktop.stop()


class DesktopApp(BaseModel):
    bundle: str


@app.post("/api/desktop/apps/forget")
async def desktop_forget(req: DesktopApp):
    from app.services import desktop
    return desktop.forget_app(req.bundle)


@app.get("/api/local-servers")
async def local_servers_endpoint():
    """What answers chats, and which model servers on this computer are running and hold memory (services/local_servers.py)."""
    from app.services import local_servers
    return await local_servers.report()


class LocalServerStop(BaseModel):
    server: str


@app.post("/api/local-servers/stop")
async def local_servers_stop(req: LocalServerStop):
    from app.services import local_servers
    return await local_servers.stop(req.server)


class EngineIdle(BaseModel):
    minutes: int


@app.post("/api/local-servers/idle")
async def local_servers_idle(req: EngineIdle):
    """Minutes unused before the built-in engine stops itself (0 = never)."""
    from app.services.storage import set_setting
    set_setting("engine_idle_minutes", str(max(0, min(req.minutes, 1440))))
    from app.services import local_servers
    return {"success": True, "idle_minutes": local_servers.idle_minutes()}


class ModelActionRequest(BaseModel):
    model_key: Optional[str] = None
    instance_id: Optional[str] = None

@app.post("/api/lmstudio/load")
async def load_lmstudio_model(req: ModelActionRequest):
    """Loads a model into LM Studio memory."""
    if not req.model_key:
        raise HTTPException(status_code=400, detail="model_key is required")
    res = await llm_service.load_lmstudio_model(req.model_key)
    return res

@app.post("/api/lmstudio/unload")
async def unload_lmstudio_model(req: ModelActionRequest):
    """Unloads a model from LM Studio memory to free RAM/VRAM."""
    res = await llm_service.unload_lmstudio_model(req.instance_id)
    return res

class AuditRequest(BaseModel):
    target: str

@app.post("/api/audit")
async def audit_endpoint(req: AuditRequest):
    """Direct live domain tech audit endpoint."""
    return await tech_auditor.audit(req.target)

class OmniSearchRequest(BaseModel):
    goal: str
    target_startup: Optional[str] = None
    domain: Optional[str] = None

@app.post("/api/omni-search")
async def omni_search_endpoint(req: OmniSearchRequest):
    """Proprietary multi-vector query fanout and RRF intelligence search."""
    return await omni_search_engine.search(
        goal=req.goal,
        target_name=req.target_startup,
        target_domain=req.domain
    )

@app.get("/api/knowledge-graph")
async def get_knowledge_graph_entities(limit: int = 50):
    """Returns all researched startups stored in the persistent Knowledge Graph."""
    return {"entities": list_entities(limit=limit)}

@app.get("/api/knowledge-graph/search")
async def search_knowledge_graph_endpoint(q: str):
    """Searches local Knowledge Graph by keyword."""
    return {"results": search_entities(q)}

@app.get("/api/knowledge-graph/{slug}")
async def get_knowledge_graph_entity_detail(slug: str):
    """Returns single startup profile from Knowledge Graph."""
    entity = get_entity(slug)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found in Knowledge Graph")
    return {"entity": entity}

class CommunityIntelRequest(BaseModel):
    target: str
    query: Optional[str] = None

@app.post("/api/community-intel")
async def community_intel_endpoint(req: CommunityIntelRequest):
    """Mines developer and user sentiment from Hacker News & forums."""
    return await community_intel_service.mine(target=req.target, query=req.query)

class GitHubAuditRequest(BaseModel):
    target: str
    repo: Optional[str] = None

@app.post("/api/github-audit")
async def github_audit_endpoint(req: GitHubAuditRequest):
    """Audits open-source repository telemetry, stars, forks, and velocity."""
    return await github_auditor_service.audit(target=req.target, repo=req.repo)

class DNSInspectRequest(BaseModel):
    domain: str

@app.post("/api/dns-inspect")
async def dns_inspect_endpoint(req: DNSInspectRequest):
    """Inspects corporate mail host, SPF, DMARC, and SaaS domain verification tokens."""
    return await dns_inspector_service.inspect(domain=req.domain)

class SurfaceExploreRequest(BaseModel):
    domain: str

@app.post("/api/surface-explore")
async def surface_explore_endpoint(req: SurfaceExploreRequest):
    """Probes SaaS product surfaces, APIs, changelogs, and enterprise pages."""
    return await surface_explorer_service.explore(domain=req.domain)

class EntityEnrichRequest(BaseModel):
    slug: str
    tags: Optional[List[str]] = None
    notes: Optional[str] = None
    competitor_slugs: Optional[List[str]] = None
    due_diligence_score: Optional[int] = None

@app.post("/api/knowledge-graph/enrich")
async def enrich_entity_endpoint(req: EntityEnrichRequest):
    """Enriches an entity in the Knowledge Graph with tags, notes, and competitor links."""
    res = update_entity_enrichment(
        slug=req.slug,
        tags=req.tags,
        notes=req.notes,
        competitor_slugs=req.competitor_slugs,
        due_diligence_score=req.due_diligence_score
    )
    if "error" in res:
        raise HTTPException(status_code=404, detail=res["error"])
    return res

class SubdomainReconRequest(BaseModel):
    domain: str

@app.post("/api/subdomain-recon")
async def subdomain_recon_endpoint(req: SubdomainReconRequest):
    """Mines Certificate Transparency logs and passive DNS for shadow infrastructure."""
    return await subdomain_recon_service.recon(domain=req.domain)

class TalentAuditRequest(BaseModel):
    target: str
    domain: Optional[str] = None

@app.post("/api/talent-audit")
async def talent_audit_endpoint(req: TalentAuditRequest):
    """Mines public ATS job boards (Ashby, Greenhouse, Lever) for hiring and headcount velocity."""
    return await talent_intel_service.audit_talent_and_hiring(target=req.target, domain=req.domain)

class PackageAuditRequest(BaseModel):
    target: str
    package_name: Optional[str] = None

@app.post("/api/package-audit")
async def package_audit_endpoint(req: PackageAuditRequest):
    """Audits ground-truth developer adoption and downloads across NPM, PyPI, and Docker Hub."""
    return await package_auditor_service.audit_package_ecosystem(target=req.target, package_name=req.package_name)

class SECFilingsRequest(BaseModel):
    target: str
    entity_name: Optional[str] = None

@app.post("/api/sec-filings")
async def sec_filings_endpoint(req: SECFilingsRequest):
    """Audits SEC EDGAR Form D private placement filings, venture capital, and board directors."""
    return await sec_edgar_service.audit_sec_filings(target=req.target, entity_name=req.entity_name)

class IPAuditRequest(BaseModel):
    target: str
    entity_name: Optional[str] = None

@app.post("/api/ip-audit")
async def ip_audit_endpoint(req: IPAuditRequest):
    """Audits USPTO trademarks, patent moats, and intellectual property."""
    return await ip_intel_service.audit_ip_landscape(target=req.target, entity_name=req.entity_name)

class DomainTrafficRequest(BaseModel):
    domain: str

@app.post("/api/traffic/domain")
async def domain_traffic_endpoint(req: DomainTrafficRequest):
    """Audits ground-truth Tranco Top 1M popularity, monthly visitor estimates, and edge CDN speed."""
    return await traffic_intel_service.audit_domain_traffic(domain=req.domain)

class PathActivityRequest(BaseModel):
    target: str
    path: Optional[str] = None

@app.post("/api/traffic/path")
async def path_activity_endpoint(req: PathActivityRequest):
    """Inspects path-level liveness (e.g. /checkoutaf1), redirects, latency, and Wayback capture frequency."""
    return await traffic_intel_service.audit_path_activity(target=req.target, path=req.path)

class TrafficCompareRequest(BaseModel):
    target_a: str
    target_b: str
    path_a: Optional[str] = None
    path_b: Optional[str] = None

@app.post("/api/traffic/compare")
async def traffic_compare_endpoint(req: TrafficCompareRequest):
    """Executes head-to-head digital traffic, global rank, and path activity comparison."""
    return await traffic_intel_service.compare_traffic_and_paths(
        target_a=req.target_a,
        target_b=req.target_b,
        path_a=req.path_a,
        path_b=req.path_b
    )

class SubdomainActivityRequest(BaseModel):
    domain: str
    max_sample: Optional[int] = 10

@app.post("/api/traffic/subdomains")
async def subdomain_activity_endpoint(req: SubdomainActivityRequest):
    """Tests whether discovered subdomains are actively being used in production vs dead zombies."""
    clean_dom = req.domain.lower().replace("https://", "").replace("http://", "").strip().split("/")[0].lstrip("www.")
    recon_res = await subdomain_recon_service.recon(domain=clean_dom)
    subs = recon_res.get("sample_subdomains", [])
    if not subs:
        subs = [f"api.{clean_dom}", f"app.{clean_dom}", f"shop.{clean_dom}", f"auth.{clean_dom}", f"staging.{clean_dom}"]
    return await traffic_intel_service.probe_subdomains_activity(
        domain=clean_dom,
        subdomains=subs,
        max_sample=req.max_sample or 10
    )

@app.get("/api/trends")
async def get_trends_endpoint(
    platform: Optional[str] = "all",
    query: Optional[str] = None,
    geo: Optional[str] = "US",
    force_refresh: Optional[bool] = False
):
    """Fetches real-time social & internet trends (X/Twitter, TikTok, Google Trends, Hacker News, GitHub) with zero API keys."""
    return await trend_intel_service.get_all_trends(
        platform=platform or "all",
        query=query,
        geo=geo or "US",
        force_refresh=force_refresh or False
    )

class TopicBuzzRequest(BaseModel):
    topic: str
    limit: Optional[int] = 8

@app.post("/api/trends/search")
async def topic_buzz_endpoint(req: TopicBuzzRequest):
    """Searches real-time multi-platform community buzz and discussion across X, Reddit, and Hacker News."""
    return await trend_intel_service.search_topic_buzz(topic=req.topic, limit=req.limit or 8)

@app.get("/api/roadmap")
async def roadmap_endpoint():
    """Returns the backend project capability roadmap, statuses, and zero-key architectural details."""
    return get_project_roadmap()

class FindEmailRequest(BaseModel):
    first_name: str
    last_name: str
    domain: str

@app.post("/api/email/find")
async def find_email_endpoint(req: FindEmailRequest):
    """Discovers and models verified work email candidates without paid Hunter/RocketReach accounts."""
    return await email_intel_service.find_work_email(
        first_name=req.first_name,
        last_name=req.last_name,
        domain=req.domain
    )

class VerifyEmailRequest(BaseModel):
    email: str

@app.post("/api/email/verify")
async def verify_email_endpoint(req: VerifyEmailRequest):
    """Conducts pre-flight bounce-risk verification for an email address."""
    return await email_intel_service.verify_email_bounce_risk(email=req.email)

class JobFirehoseRequest(BaseModel):
    target: str
    domain: Optional[str] = None
    department: Optional[str] = None
    max_age_days: Optional[int] = 45

@app.post("/api/jobs/firehose")
async def job_firehose_endpoint(req: JobFirehoseRequest):
    """Aggregates real-time job requisitions from Ashby, Greenhouse, and Lever with liveness checks."""
    return await talent_intel_service.scan_live_job_firehose(
        target=req.target,
        domain=req.domain,
        max_age_days=req.max_age_days or 45,
        department=req.department
    )

class BatchJobFirehoseRequest(BaseModel):
    targets: Optional[List[str]] = None
    cohort: Optional[str] = None
    role_keyword: Optional[str] = None
    max_age_days: Optional[int] = 45

@app.post("/api/jobs/firehose/batch")
async def batch_job_firehose_endpoint(req: BatchJobFirehoseRequest):
    """Scans real-time job requisitions across multiple startups or curated cohorts concurrently."""
    return await talent_intel_service.scan_batch_job_firehose(
        targets=req.targets,
        cohort=req.cohort,
        role_keyword=req.role_keyword,
        max_age_days=req.max_age_days or 45
    )


class FundingOrgRequest(BaseModel):
    target: str
    domain: Optional[str] = None

@app.post("/api/funding/org")
async def funding_org_endpoint(req: FundingOrgRequest):
    """Audits Y Combinator accelerator batch, verified founders, and federal SEC Form D capital."""
    return await funding_intel_service.audit_funding_and_org(
        target=req.target,
        domain=req.domain
    )

class TriggerEventsRequest(BaseModel):
    target: str
    domain: Optional[str] = None

@app.post("/api/trigger/events")
async def trigger_events_endpoint(req: TriggerEventsRequest):
    """Scans corporate trigger events (funding, launches, hires) to generate warm outreach openers."""
    return await trend_intel_service.scan_trigger_events(
        target=req.target,
        domain=req.domain
    )

# -------------------------------------------------------------
# CREATOR DISCOVERY & ATTENTION ARBITRAGE API
# -------------------------------------------------------------

class CreatorDiscoverRequest(BaseModel):
    niche: str
    platform: Optional[str] = "all"
    follower_min: Optional[int] = 5000
    follower_max: Optional[int] = 500000
    limit: Optional[int] = 10

class CreatorPredictRequest(BaseModel):
    handle: str
    platform: str
    followers: Optional[int] = 50000
    recent_views: Optional[List[int]] = None
    median_views: Optional[int] = None
    avg_likes: Optional[int] = None
    avg_comments: Optional[int] = None

@app.post("/api/creators/discover")
async def creator_discover_endpoint(req: CreatorDiscoverRequest):
    """Autonomous multi-platform creator discovery, Attention Prediction ($V_50$), and CPM Arbitrage scoring."""
    return await creator_intel_service.discover_creators(
        niche=req.niche,
        platform=req.platform or "all",
        follower_min=req.follower_min or 5000,
        follower_max=req.follower_max or 500000,
        limit=req.limit or 10
    )

@app.post("/api/creators/predict")
async def creator_predict_endpoint(req: CreatorPredictRequest):
    """Calculates Expected View Floor ($V_50$), Algorithmic Velocity Ratio ($AVR$), Predicted CPM, and Arbitrage Multiple vs $15 market baseline."""
    return creator_intel_service.model_attention_arbitrage(
        handle=req.handle,
        platform=req.platform,
        followers=req.followers or 50000,
        recent_views=req.recent_views,
        median_views=req.median_views,
        avg_likes=req.avg_likes,
        avg_comments=req.avg_comments
    )

# -------------------------------------------------------------
# LOCAL PROJECT WORKSPACE & OBSERVABILITY API
# -------------------------------------------------------------

class CreateProjectRequest(BaseModel):
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = "#6366f1"
    icon: Optional[str] = "folder"
    tags: Optional[List[str]] = None
    custom_path: Optional[str] = None

@app.get("/api/projects")
async def list_projects_endpoint():
    """Lists all physical project folders on disk with files, sessions, and telemetry."""
    projects = project_storage_service.list_projects()
    return {"projects": projects, "unfiled": project_storage_service.list_unfiled_sessions([p["slug"] for p in projects])}

@app.post("/api/projects")
async def create_project_endpoint(req: CreateProjectRequest):
    """Creates a new physical project directory and workspace on the computer."""
    return project_storage_service.create_project(
        name=req.name,
        slug=req.slug,
        description=req.description or "",
        color=req.color or "#6366f1",
        icon=req.icon or "folder",
        tags=req.tags,
        custom_path=req.custom_path
    )

class SaveFileRequest(BaseModel):
    filename: str
    content: str
    subfolder: Optional[str] = "dossiers"

@app.post("/api/projects/{slug}/files")
async def save_project_file_endpoint(slug: str, req: SaveFileRequest):
    """Saves or updates a physical file inside a project folder on disk."""
    return project_storage_service.save_project_file(
        project_slug=slug,
        filename=req.filename,
        content=req.content,
        subfolder=req.subfolder or "dossiers"
    )

@app.get("/api/projects/{slug}/files")
async def read_project_file_endpoint(slug: str, filename: str, subfolder: Optional[str] = "dossiers"):
    """Reads a physical file from a project folder."""
    return project_storage_service.read_project_file(
        project_slug=slug,
        subfolder=subfolder or "dossiers",
        filename=filename
    )

@app.delete("/api/projects/{slug}/files")
async def delete_project_file_endpoint(slug: str, filename: str, subfolder: Optional[str] = "dossiers"):
    """Deletes a physical file from a project folder."""
    return project_storage_service.delete_project_file(
        project_slug=slug,
        subfolder=subfolder or "dossiers",
        filename=filename
    )

class AssignSessionRequest(BaseModel):
    session_id: str
    project_slug: Optional[str] = ""   # empty = take it out of its project

@app.post("/api/projects/assign-session")
async def assign_session_endpoint(req: AssignSessionRequest):
    """Assigns an active chat session to a physical project folder."""
    success = project_storage_service.assign_session_to_project(
        session_id=req.session_id,
        project_slug=req.project_slug
    )
    return {"success": success}

class MoveFileRequest(BaseModel):
    from_slug: str
    subfolder: str
    filename: str
    to_slug: str

@app.post("/api/projects/move-file")
async def move_project_file_endpoint(req: MoveFileRequest):
    """Moves a saved file from one project to another (sidebar drag and drop)."""
    return project_storage_service.move_project_file(req.from_slug, req.subfolder, req.filename, req.to_slug)

@app.get("/api/projects/observability")
async def get_observability_endpoint():
    """Returns the full knowledge graph, entity links, and project observability telemetry."""
    return project_storage_service.get_observability_data()

@app.get("/api/projects/search")
async def search_projects_endpoint(q: str):
    """Performs deep global search across all local projects, dossiers, and entities."""
    return project_storage_service.search_all_projects(query=q)

# -------------------------------------------------------------
# COMPUTER FILESYSTEM & FULL MACHINE EXPLORER API
# -------------------------------------------------------------

class FSWriteRequest(BaseModel):
    path: str
    content: str

class FSCreateFileRequest(BaseModel):
    dir_path: str
    filename: str
    content: Optional[str] = ""

class FSCreateFolderRequest(BaseModel):
    parent_path: str
    folder_name: str

class FSRenameRequest(BaseModel):
    source_path: str
    new_name: str

class FSDuplicateRequest(BaseModel):
    source_path: str

class FSDeleteRequest(BaseModel):
    path: str

class FSLinkProjectRequest(BaseModel):
    path: str
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = "#10b981"
    icon: Optional[str] = "folder"
    tags: Optional[List[str]] = None

@app.get("/api/fs/browse")
async def fs_browse_endpoint(path: Optional[str] = None, show_hidden: bool = False, sort_by: str = "name"):
    """Lists directories and files anywhere on the user's computer with metadata and shortcuts."""
    return filesystem_service.list_directory(raw_path=path, show_hidden=show_hidden, sort_by=sort_by)

@app.get("/api/fs/read")
async def fs_read_endpoint(path: str):
    """Reads a computer file with safe text/image/size handling."""
    res = filesystem_service.read_file(raw_path=path)
    if "error" in res and not res.get("is_too_large"):
        raise HTTPException(status_code=400, detail=res["error"])
    return res

@app.post("/api/fs/write")
async def fs_write_endpoint(req: FSWriteRequest):
    """Saves edited content to a file on the computer."""
    res = filesystem_service.write_file(raw_path=req.path, content=req.content)
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])
    return res

@app.post("/api/fs/create-file")
async def fs_create_file_endpoint(req: FSCreateFileRequest):
    """Creates a new file in any writable folder on the computer."""
    res = filesystem_service.create_file(raw_dir=req.dir_path, filename=req.filename, content=req.content or "")
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])
    return res

@app.post("/api/fs/create-folder")
async def fs_create_folder_endpoint(req: FSCreateFolderRequest):
    """Creates a new folder on the computer."""
    res = filesystem_service.create_folder(raw_parent=req.parent_path, folder_name=req.folder_name)
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])
    return res

@app.post("/api/fs/rename")
async def fs_rename_endpoint(req: FSRenameRequest):
    """Renames a computer file or folder."""
    res = filesystem_service.rename_item(raw_source=req.source_path, new_name=req.new_name)
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])
    return res

@app.post("/api/fs/duplicate")
async def fs_duplicate_endpoint(req: FSDuplicateRequest):
    """Duplicates a computer file or folder."""
    res = filesystem_service.duplicate_item(raw_source=req.source_path)
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])
    return res

@app.post("/api/fs/delete")
async def fs_delete_endpoint(req: FSDeleteRequest):
    """Deletes a file or directory with system safety protection."""
    res = filesystem_service.delete_item(raw_path=req.path)
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])
    return res

@app.get("/api/fs/search")
async def fs_search_endpoint(path: Optional[str] = None, q: str = ""):
    """Searches files and contents recursively in a computer folder."""
    return filesystem_service.search_filesystem(raw_base=path or "", query=q)

@app.post("/api/fs/link-project")
async def fs_link_project_endpoint(req: FSLinkProjectRequest):
    """Links any existing directory on the computer as a Xod project workspace."""
    try:
        return project_storage_service.link_folder(
            folder_path=req.path,
            name=req.name,
            description=req.description or "",
            color=req.color or "#10b981",
            icon=req.icon or "folder",
            tags=req.tags
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/projects/{slug}/unlink")
async def fs_unlink_project_endpoint(slug: str):
    """Unlinks a project workspace without deleting files on the computer."""
    res = project_storage_service.unlink_project(slug=slug)
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])
    return res


# -------------------------------------------------------------
# Autonomous Browser Sandbox & Computer Control Endpoints
# -------------------------------------------------------------

class BrowserNavigateRequest(BaseModel):
    url: str

class BrowserClickRequest(BaseModel):
    target: str

class BrowserTypeRequest(BaseModel):
    selector: str
    text: str
    press_enter: Optional[bool] = False

class BrowserClickCoordsRequest(BaseModel):
    x: float
    y: float
    button: Optional[str] = "left"

class BrowserTypeDirectRequest(BaseModel):
    text: str
    press_enter: Optional[bool] = False

class BrowserEvaluateRequest(BaseModel):
    expression: str

class BrowserScrollRequest(BaseModel):
    direction: Optional[str] = "down"
    amount: Optional[int] = 500
    to_selector: Optional[str] = None

class BrowserHoverRequest(BaseModel):
    target: str

class BrowserKeyRequest(BaseModel):
    key: str

class BrowserWaitRequest(BaseModel):
    target: Optional[str] = None
    condition: Optional[str] = "selector"
    value: Optional[str] = None
    timeout_sec: Optional[float] = 10.0

class BrowserPdfRequest(BaseModel):
    filename: Optional[str] = None
    landscape: Optional[bool] = False
    print_background: Optional[bool] = True

class BrowserUploadRequest(BaseModel):
    file_paths: List[str]
    selector_or_index: Optional[Any] = None

class BrowserAxTreeRequest(BaseModel):
    max_nodes: Optional[int] = 60

class BrowserMissionRequest(BaseModel):
    goal: str
    start_url: Optional[str] = None
    max_steps: Optional[int] = 6

class BrowserTabActionRequest(BaseModel):
    action: str = "list"
    target_id: Optional[str] = None
    url: Optional[str] = "about:blank"

class BrowserObserveRequest(BaseModel):
    max_elements: Optional[int] = 60
    filter_type: Optional[str] = "all"

class BrowserInteractRequest(BaseModel):
    index: Optional[Any] = None
    selector: Optional[str] = None
    text_target: Optional[str] = None
    action: Optional[str] = "click"
    text: Optional[str] = None
    press_enter: Optional[bool] = False
    clear_first: Optional[bool] = True

class BrowserFormFillRequest(BaseModel):
    fields: List[Dict[str, Any]]
    submit_index: Optional[Any] = None

class BrowserVisibilityRequest(BaseModel):
    visible: bool = True

class BrowserExtractDataRequest(BaseModel):
    instruction: str
    schema_description: Optional[str] = None

class BrowserCookiesRequest(BaseModel):
    action: Optional[str] = "get"
    cookies: Optional[List[Dict[str, Any]]] = None

class BrowserRestartRequest(BaseModel):
    clear_profile: Optional[bool] = False
    visible: Optional[bool] = None

class BrowserUnstuckRequest(BaseModel):
    reload_if_blank: Optional[bool] = True

class BrowserCaptchaSolveRequest(BaseModel):
    auto_click: Optional[bool] = True
    wait_sec: Optional[float] = 12.0

class ComputerRunRequest(BaseModel):
    command: str
    cwd: Optional[str] = None
    timeout_sec: Optional[int] = 45

class ComputerNotifyRequest(BaseModel):
    title: str
    message: str
    sound: Optional[bool] = False

class ComputerRevealRequest(BaseModel):
    path: Optional[str] = None
    target: Optional[str] = None
    folder: Optional[str] = None

class ComputerFindRequest(BaseModel):
    query: str
    root_dir: Optional[str] = None
    max_results: Optional[int] = 30

class ComputerManageFileRequest(BaseModel):
    action: str
    src: str
    dst: Optional[str] = None

class ComputerClipboardRequest(BaseModel):
    text: str

class ComputerProcessKillRequest(BaseModel):
    pid: int
    force: Optional[bool] = False

class ComputerStartJobRequest(BaseModel):
    name: str
    command: str
    cwd: Optional[str] = None

class ComputerAIReviewRequest(BaseModel):
    intent: Optional[str] = None

class ComputerAIExecuteActionRequest(BaseModel):
    action_type: str
    params: Dict[str, Any]

class ComputerFileReadRequest(BaseModel):
    filepath: str
    max_bytes: Optional[int] = 50000

class ComputerFileWriteRequest(BaseModel):
    filepath: str
    content: str
    create_dirs: Optional[bool] = True

class ComputerFilePatchRequest(BaseModel):
    filepath: str
    search_text: str
    replace_text: str


@app.get("/api/browser/status")
async def get_browser_status():
    """Returns current state of the isolated background AI browser."""
    return browser_sandbox_service.get_state()

@app.post("/api/browser/navigate")
async def browser_navigate_endpoint(req: BrowserNavigateRequest):
    """Navigates the background browser to a URL without moving user cursor."""
    try:
        return await browser_sandbox_service.navigate(url=req.url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/click")
async def browser_click_endpoint(req: BrowserClickRequest):
    """Clicks an element by selector or visible text inside the background browser."""
    try:
        return await browser_sandbox_service.click(selector_or_text=req.target)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/type")
async def browser_type_endpoint(req: BrowserTypeRequest):
    """Types text into an input field inside the background browser."""
    try:
        return await browser_sandbox_service.type_text(
            selector=req.selector,
            text=req.text,
            press_enter=bool(req.press_enter)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/click-coords")
async def browser_click_coords_endpoint(req: BrowserClickCoordsRequest):
    """Dispatches a direct hardware/CDP click at exact (x, y) coordinates for human-in-the-loop intervention."""
    try:
        return await browser_sandbox_service.click_coordinates(x=req.x, y=req.y, button=req.button or "left")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/type-direct")
async def browser_type_direct_endpoint(req: BrowserTypeDirectRequest):
    """Sends keystrokes directly into whatever DOM element has focus."""
    try:
        return await browser_sandbox_service.type_direct(text=req.text, press_enter=bool(req.press_enter))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/browser/content")
async def browser_content_endpoint(max_length: int = 10000):
    """Extracts text, links, and structure from the currently active browser page."""
    try:
        return await browser_sandbox_service.extract_content(max_length=max_length)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/screenshot")
async def browser_screenshot_endpoint():
    """Captures an immediate screenshot of the AI's current browser page."""
    try:
        return await browser_sandbox_service.take_screenshot()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/evaluate")
async def browser_evaluate_endpoint(req: BrowserEvaluateRequest):
    """Evaluates JavaScript inside the background browser."""
    try:
        return await browser_sandbox_service.evaluate_js(expression=req.expression)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/scroll")
async def browser_scroll_endpoint(req: BrowserScrollRequest):
    """Scrolls the page or scrolls to an element."""
    try:
        return await browser_sandbox_service.scroll(
            direction=req.direction or "down",
            amount=req.amount or 500,
            to_selector=req.to_selector
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/hover")
async def browser_hover_endpoint(req: BrowserHoverRequest):
    """Hovers over an element to trigger interactive dropdowns or CSS hover effects."""
    try:
        return await browser_sandbox_service.hover(selector_or_text=req.target)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/key")
async def browser_key_endpoint(req: BrowserKeyRequest):
    """Dispatches a keyboard event (Enter, Escape, Tab, ArrowDown, Backspace) to page."""
    try:
        return await browser_sandbox_service.press_key(key=req.key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/wait")
async def browser_wait_endpoint(req: BrowserWaitRequest):
    """Waits for an element or condition to appear in the DOM."""
    try:
        cond = req.condition or "selector"
        val = req.value or req.target or ""
        return await browser_sandbox_service.wait_for_condition(
            condition=cond,
            value=val,
            timeout_sec=req.timeout_sec or 10.0
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/browser/markdown")
async def browser_markdown_endpoint(max_length: int = 15000):
    """Extracts clean, structured GitHub-flavored Markdown from current page."""
    try:
        return await browser_sandbox_service.extract_clean_markdown(max_length=max_length)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/screenshot/full")
async def browser_full_screenshot_endpoint():
    """Captures a full scroll-height screenshot of the current page."""
    try:
        return await browser_sandbox_service.capture_full_page_screenshot()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/browser/tabs")
async def browser_list_tabs_endpoint():
    """Lists open browser tabs."""
    return await browser_sandbox_service.manage_tabs(action="list")

@app.post("/api/browser/tabs")
async def browser_tabs_action_endpoint(req: BrowserTabActionRequest):
    """Performs tab management (list, new, switch, close)."""
    return await browser_sandbox_service.manage_tabs(
        action=req.action,
        target_id=req.target_id,
        url=req.url
    )

@app.post("/api/browser/mission")
async def browser_mission_endpoint(req: BrowserMissionRequest):
    """Runs an autonomous multi-step perception-action browser mission with live LLM deliberation."""
    try:
        return await browser_sandbox_service.run_browser_mission(
            goal=req.goal,
            start_url=req.start_url,
            max_steps=req.max_steps or 6
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/observe")
async def browser_observe_endpoint(req: Optional[BrowserObserveRequest] = None):
    """Inspects visible interactive elements with Set-of-Marks indexing and returns grounded text map."""
    try:
        max_el = req.max_elements if req else 60
        filt = req.filter_type if req else "all"
        return await browser_sandbox_service.observe(max_count=max_el or 60, filter=filt or "all")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/interact")
async def browser_interact_endpoint(req: BrowserInteractRequest):
    """Executes deterministic action directly by element numeric index (Set-of-Marks), selector, or text."""
    try:
        return await browser_sandbox_service.interact_by_index(
            index=req.index,
            action=req.action or "click",
            text=req.text,
            press_enter=bool(req.press_enter),
            clear_first=bool(req.clear_first if req.clear_first is not None else True),
            selector=req.selector,
            text_target=req.text_target
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/form")
async def browser_form_endpoint(req: BrowserFormFillRequest):
    """Batch fills multiple form fields and optionally submits."""
    try:
        return await browser_sandbox_service.fill_form(
            fields=req.fields,
            submit_index=req.submit_index
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/visibility")
async def browser_visibility_endpoint(req: BrowserVisibilityRequest):
    """Toggles Chrome between silent background headless mode and visible macOS desktop window."""
    try:
        # req.visible=True means headless=False
        return await browser_sandbox_service.set_headless_mode(not bool(req.visible))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/dismiss-overlays")
async def browser_dismiss_overlays_endpoint():
    """Auto-detects and dismisses cookie consent popups, GDPR banners, and overlay backdrops."""
    try:
        return await browser_sandbox_service.dismiss_overlays()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/restart")
async def browser_restart_endpoint(req: Optional[BrowserRestartRequest] = None):
    """Force-restarts browser, clears dead locks, and optionally wipes cookies/cache for fresh profile."""
    try:
        clear_prof = req.clear_profile if req else False
        headless = not req.visible if (req and req.visible is not None) else None
        return await browser_sandbox_service.restart_browser(clear_profile=clear_prof, headless=headless)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/unstuck")
async def browser_unstuck_endpoint(req: Optional[BrowserUnstuckRequest] = None):
    """Autonomous self-healing: halts spinners, closes modal dialogs & backdrops, unfreezes scroll."""
    try:
        reload_blank = req.reload_if_blank if req else True
        return await browser_sandbox_service.unstuck(reload_if_blank=reload_blank)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/browser/captcha/detect")
async def browser_captcha_detect_endpoint():
    """Detects whether active page contains Cloudflare Turnstile, 'Just a moment...', or reCAPTCHA."""
    try:
        return await browser_sandbox_service.detect_captcha()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/captcha/solve")
async def browser_captcha_solve_endpoint(req: Optional[BrowserCaptchaSolveRequest] = None):
    """Autonomously attempts solving or bypassing detected CAPTCHA barrier."""
    try:
        auto_click = req.auto_click if req else True
        wait_sec = req.wait_sec if req else 12.0
        return await browser_sandbox_service.handle_captcha(auto_click=auto_click, wait_sec=wait_sec)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/browser/smart-inspect")
async def browser_smart_inspect_endpoint():
    """Gathers single-shot perceptual grounding (Set-of-Marks tags, headings, forms, markdown outline)."""
    try:
        return await browser_sandbox_service.smart_inspect()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/browser/network/snoop")
async def browser_network_snoop_endpoint(filter_url: Optional[str] = None, limit: Optional[int] = 20):
    """Retrieves intercepted background API/XHR/fetch requests and JSON payloads."""
    try:
        return await browser_sandbox_service.snoop_network(filter_url=filter_url, limit=limit or 20)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/som-snapshot")
async def browser_som_snapshot_endpoint():
    """Captures a screenshot with high-contrast Set-of-Marks badges overlaid on interactive elements."""
    try:
        return await browser_sandbox_service.take_screenshot(overlay_som=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/extract-data")
async def browser_extract_data_endpoint(req: BrowserExtractDataRequest):
    """Synthesizes clean structured JSON data from current page based on instruction and schema."""
    try:
        return await browser_sandbox_service.extract_structured_data(
            instruction=req.instruction,
            schema_description=req.schema_description
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/cookies")
async def browser_cookies_endpoint(req: BrowserCookiesRequest):
    """Manages browser cookies (get, set, clear)."""
    try:
        return await browser_sandbox_service.manage_cookies(
            action=req.action or "get",
            cookies=req.cookies
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/browser/cookies")
async def browser_get_cookies_endpoint():
    """Gets cookies for the current browser page."""
    try:
        return await browser_sandbox_service.manage_cookies(action="get")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/pdf")
async def browser_export_pdf_endpoint(req: Optional[BrowserPdfRequest] = None):
    """Exports the current page to a high-resolution PDF file."""
    try:
        r = req or BrowserPdfRequest()
        return await browser_sandbox_service.export_pdf(
            filename=r.filename,
            landscape=r.landscape or False,
            print_background=True if r.print_background is None else r.print_background
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/browser/upload")
async def browser_upload_file_endpoint(req: BrowserUploadRequest):
    """Attaches local files to an input[type='file'] element via CDP."""
    try:
        return await browser_sandbox_service.upload_file(
            file_paths=req.file_paths,
            selector_or_index=req.selector_or_index
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/browser/downloads")
async def browser_downloads_endpoint():
    """Lists files downloaded into sandbox downloads folder."""
    try:
        return await browser_sandbox_service.get_downloads()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/documents/upload")
async def upload_document_endpoint(file: UploadFile = File(...)):
    """Parses uploaded documents (PDF, DOCX, CSV, TXT, JSON, code, etc.) into clean text and metadata."""
    try:
        content_bytes = await file.read()
        from app.services.document_parser import document_parser_service
        parsed = document_parser_service.parse_bytes(file.filename, content_bytes)
        return parsed
    except Exception as e:
        logger.error(f"Failed to parse uploaded document {file.filename}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/browser/ax-tree")
async def browser_ax_tree_endpoint(max_nodes: int = 60):
    """Extracts semantic Accessibility (AX) Tree snapshot via CDP."""
    try:
        return await browser_sandbox_service.extract_accessibility_tree(max_nodes=max_nodes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/browser/mission/live")
async def browser_mission_live_endpoint():
    """Returns the latest active or completed autonomous browser mission trace."""
    return browser_sandbox_service.latest_mission or {"status": "idle", "steps": []}

@app.get("/api/computer/profile")
async def computer_profile_endpoint():
    """Returns persistent host machine profile, cwd, and specs."""
    return system_control_service.get_computer_profile()

@app.get("/api/computer/telemetry")
async def computer_telemetry_endpoint():
    """Returns background system telemetry (CPU load, memory, disk, top processes)."""
    return system_control_service.get_system_telemetry()

@app.post("/api/computer/run")
async def computer_run_endpoint(req: ComputerRunRequest):
    """Runs a background shell command safely without stealing active window focus."""
    return await system_control_service.run_command(
        command=req.command,
        cwd=req.cwd,
        timeout_sec=req.timeout_sec or 45
    )

@app.post("/api/computer/run-stream")
async def computer_run_stream_endpoint(req: ComputerRunRequest):
    """Streams live stdout and stderr chunks in real time via Server-Sent Events (SSE)."""
    async def event_generator():
        async for event in system_control_service.run_command_stream(
            command=req.command,
            cwd=req.cwd,
            timeout_sec=req.timeout_sec or 60
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@app.get("/api/computer/history")
async def computer_history_endpoint():
    """Returns recent background command executions."""
    return system_control_service.command_history

@app.post("/api/computer/notify")
async def computer_notify_endpoint(req: ComputerNotifyRequest):
    """Sends a native macOS notification banner to the user."""
    return await system_control_service.notify_user(
        title=req.title,
        message=req.message,
        sound=bool(req.sound)
    )

@app.post("/api/computer/reveal")
@app.post("/api/computer/open-finder")
async def computer_reveal_endpoint(req: ComputerRevealRequest):
    """Reveals a file or directory in macOS Finder."""
    target = req.path or req.target or req.folder or ""
    return await system_control_service.reveal_in_finder(path=target)

@app.post("/api/computer/find")
async def computer_find_endpoint(req: ComputerFindRequest):
    """Instant Spotlight search for files across the computer."""
    return await system_control_service.find_files(
        query=req.query,
        root_dir=req.root_dir,
        max_results=req.max_results or 30
    )

@app.post("/api/computer/file/manage")
async def computer_file_manage_endpoint(req: ComputerManageFileRequest):
    """Safely manages files (trash to macOS Trash, copy, move, mkdir)."""
    return await system_control_service.manage_file(
        action=req.action,
        src=req.src,
        dst=req.dst
    )

@app.get("/api/computer/clipboard")
async def computer_clipboard_get_endpoint():
    """Reads current text from macOS clipboard."""
    return await system_control_service.read_clipboard()

@app.post("/api/computer/clipboard")
async def computer_clipboard_set_endpoint(req: ComputerClipboardRequest):
    """Sets text into macOS clipboard."""
    return await system_control_service.write_clipboard(text=req.text)

@app.get("/api/computer/port/{port}")
async def computer_port_inspect_endpoint(port: int):
    """Inspects processes listening on a port."""
    return await system_control_service.inspect_port(port=port)

@app.post("/api/computer/process/kill")
async def computer_kill_process_endpoint(req: ComputerProcessKillRequest):
    """Safely terminates a process by PID."""
    return await system_control_service.kill_process(pid=req.pid, force=bool(req.force))

@app.get("/api/computer/jobs")
async def computer_jobs_list_endpoint():
    """Lists running background daemon jobs."""
    return {"jobs": system_control_service.list_jobs()}

@app.post("/api/computer/jobs")
async def computer_jobs_start_endpoint(req: ComputerStartJobRequest):
    """Starts a persistent background daemon job."""
    return await system_control_service.start_background_job(
        name=req.name,
        command=req.command,
        cwd=req.cwd
    )

@app.post("/api/computer/jobs/{job_id}/stop")
async def computer_jobs_stop_endpoint(job_id: str, force: Optional[bool] = False):
    """Stops a background daemon job."""
    return await system_control_service.stop_job(job_id=job_id, force=bool(force))

@app.get("/api/computer/jobs/{job_id}/logs")
async def computer_jobs_logs_endpoint(job_id: str, tail: Optional[int] = 100):
    """Retrieves logs from a background daemon job."""
    return system_control_service.get_job_logs(job_id=job_id, tail=tail or 100)

@app.get("/api/computer/ports")
async def computer_ports_endpoint():
    """Returns all active listening TCP ports discovered dynamically on the machine."""
    ports = system_control_service.get_listening_ports()
    return {"ports": ports, "count": len(ports)}

@app.post("/api/computer/ai/review")
async def computer_ai_review_endpoint(req: ComputerAIReviewRequest):
    """Triggers an AI host diagnostic audit synthesizing live telemetry, ports, and memory."""
    return await system_control_service.ai_review_system(user_intent=req.intent)

@app.post("/api/computer/ai/execute_action")
async def computer_ai_execute_action_endpoint(req: ComputerAIExecuteActionRequest):
    """Executes an AI-recommended or user-selected remediation action."""
    return await system_control_service.execute_system_action(action_type=req.action_type, params=req.params)

@app.post("/api/computer/file/read")
async def computer_file_read_endpoint(req: ComputerFileReadRequest):
    """Reads a file from the host filesystem."""
    return system_control_service.read_file(filepath=req.filepath, max_bytes=req.max_bytes or 50000)

@app.post("/api/computer/file/write")
async def computer_file_write_endpoint(req: ComputerFileWriteRequest):
    """Writes content to a file on the host filesystem."""
    return system_control_service.write_file(filepath=req.filepath, content=req.content, create_dirs=bool(req.create_dirs))

@app.post("/api/computer/file/patch")
async def computer_file_patch_endpoint(req: ComputerFilePatchRequest):
    """Replaces text in a file on the host filesystem."""
    return system_control_service.patch_file(filepath=req.filepath, search_text=req.search_text, replace_text=req.replace_text)


# ==============================================================================
# Hands-Free Voice Engine & Model Lab Endpoints
# ==============================================================================
from app.services.voice import voice_engine
from app.services.trace_recorder import get_lab_stats, export_training_data, set_feedback

class VoiceCommandRequest(BaseModel):
    query: str
    speak_back: Optional[bool] = True
    safe_only: Optional[bool] = False   # hands-free conversation: nothing that changes files

class VoiceTTSRequest(BaseModel):
    text: str
    voice: Optional[str] = None

class SpeechRequest(BaseModel):
    text: str
    voice: Optional[str] = None
    rate: Optional[int] = None

class SpeechSettings(BaseModel):
    mode: Optional[str] = None
    voice: Optional[str] = None
    rate: Optional[int] = None
    wake: Optional[bool] = None

class TraceFeedbackRequest(BaseModel):
    trace_id: str
    value: int  # +1 (good) or -1 (bad)

class LabExportRequest(BaseModel):
    specialist: Optional[str] = "intent_router"

# ---------------------------------------------------------------- first-run setup (services/setup_wizard.py)
class SetupChoice(BaseModel):
    provider: str
    model: str
    base_url: Optional[str] = ""

class SetupDownload(BaseModel):
    repo: str

class RoutingSettings(BaseModel):
    mode: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    monthly_budget_usd: Optional[float] = None
    price_overrides: Optional[Dict[str, List[float]]] = None


@app.get("/api/routing")
async def routing_status_endpoint():
    from app.services import llm_routing
    return llm_routing.summary()


@app.post("/api/routing")
async def routing_save_endpoint(req: RoutingSettings):
    from app.services import llm_routing
    llm_routing.save_settings(**req.model_dump())
    return llm_routing.summary()


class ReportRequest(BaseModel):
    title: Optional[str] = None
    question: Optional[str] = None
    html: str
    sources: Optional[List[Dict[str, str]]] = None
    pdf: bool = True


@app.post("/api/reports")
async def report_create_endpoint(req: ReportRequest):
    """Turns an answer into a shareable page (+ PDF) in the data folder's reports/."""
    from app.services import reports
    title = await reports.better_title(req.title or "", req.question or "")
    return await asyncio.to_thread(reports.make_report, title, req.question or "", req.html, req.sources, req.pdf)


class PackToggle(BaseModel):
    enabled: bool


@app.get("/api/packs")
async def packs_list_endpoint():
    from app.services import packs
    return {"packs": packs.status()}


@app.post("/api/packs/{pack_id}")
async def packs_toggle_endpoint(pack_id: str, req: PackToggle):
    from app.services import packs
    return packs.set_enabled(pack_id, req.enabled)


@app.get("/api/setup")
async def setup_status_endpoint():
    from app.services import setup_wizard
    return await setup_wizard.status()

@app.post("/api/setup/download")
async def setup_download_endpoint(req: SetupDownload):
    from app.services.local_engine import start_download
    return start_download(req.repo)

@app.get("/api/setup/download")
async def setup_download_status_endpoint(repo: str):
    from app.services.local_engine import download_status
    return download_status(repo)

@app.get("/api/models/quick")
async def models_quick_endpoint():
    """The model switcher at the top of the page (setup_wizard.quick)."""
    from app.services import setup_wizard
    return await setup_wizard.quick()

@app.get("/api/secrets/health")
async def secrets_health_endpoint():
    """Whether Xod can read its saved API keys from the Keychain right now (services/keychain.py)."""
    from app.services import secret_store
    return secret_store.health()

@app.post("/api/secrets/retry")
async def secrets_retry_endpoint():
    """Reads the Keychain again; macOS asks whether Xod may (Always Allow keeps it)."""
    from app.config import reload_secrets
    return await asyncio.to_thread(reload_secrets)

@app.post("/api/setup/choose")
async def setup_choose_endpoint(req: SetupChoice):
    from app.services import setup_wizard
    return setup_wizard.choose(req.provider, req.model, req.base_url or "")

@app.post("/api/setup/test")
async def setup_test_endpoint():
    """Says hello to the chosen model, starting the built-in engine if that's the one."""
    import time as _t
    t = _t.time()
    try:
        msg = await llm_service.chat_completion([{"role": "user", "content": "Say hi in five words or fewer."}], max_tokens=40, think=False)
        return {"success": True, "reply": (msg.get("content") or "").strip()[:200], "seconds": round(_t.time() - t, 1)}
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}

@app.post("/api/setup/complete")
async def setup_complete_endpoint():
    from app.services import setup_wizard
    return setup_wizard.complete()

# This computer and the AI on it (services/machine.py, services/autoconnect.py).
@app.get("/api/machine")
async def machine_endpoint(fresh: bool = False):
    from app.services import machine
    return await asyncio.to_thread(machine.profile, fresh)

@app.get("/api/autoconnect")
async def autoconnect_state_endpoint(since: int = 0):
    """What Xod found and did; `events` holds only those after `since` (an event id) for the page's notices."""
    from app.services import autoconnect
    s = autoconnect.state()
    return {**s, "events": autoconnect.events(since)}

@app.post("/api/autoconnect/check")
async def autoconnect_check_endpoint():
    from app.services import autoconnect
    return await autoconnect.check()

# Local models through Ollama: install it, recommended models for this computer, downloads (services/ollama_hub.py).
class OllamaModel(BaseModel):
    name: str

class OllamaQuery(BaseModel):
    query: str

@app.get("/api/ollama")
async def ollama_overview_endpoint():
    from app.services import ollama_hub
    return await ollama_hub.overview()

@app.post("/api/ollama/install")
async def ollama_install_endpoint():
    from app.services import ollama_hub
    return await ollama_hub.install_ollama()

@app.post("/api/ollama/start")
async def ollama_start_endpoint():
    from app.services import ollama_hub
    return await ollama_hub.start_ollama()

@app.post("/api/ollama/resolve")
async def ollama_resolve_endpoint(req: OllamaQuery):
    from app.services import ollama_hub
    return await ollama_hub.resolve(req.query)

@app.post("/api/ollama/pull")
async def ollama_pull_endpoint(req: OllamaModel):
    from app.services import ollama_hub
    return await ollama_hub.start_pull(req.name)

@app.get("/api/ollama/pull")
async def ollama_pull_status_endpoint(name: str):
    from app.services import ollama_hub
    return ollama_hub.pull_status(name)

@app.post("/api/ollama/pull/cancel")
async def ollama_pull_cancel_endpoint(req: OllamaModel):
    from app.services import ollama_hub
    return ollama_hub.cancel_pull(req.name)

@app.post("/api/ollama/delete")
async def ollama_delete_endpoint(req: OllamaModel):
    from app.services import ollama_hub
    return await ollama_hub.delete(req.name)

VOICE_MAC_ONLY = {"success": False, "mac_only": True,
                  "error": "Talking to Xod uses speech recognition that runs on a Mac (Apple silicon) for now. On this PC, type instead."}


@app.post("/api/voice/transcribe")
async def voice_transcribe_endpoint(request: Request):
    """Speech to text on this Mac (local Whisper). Body: the raw recording (webm/ogg/mp4/wav)."""
    if sys.platform != "darwin":
        return VOICE_MAC_ONLY
    from app.services import speech_to_text
    # ?partial=1: a live caption while you're still talking; skipped if Whisper is busy, so the
    # real transcription of what you said never waits behind a caption.
    return await speech_to_text.transcribe(await request.body(), partial=request.query_params.get("partial") == "1")


@app.post("/api/voice/prepare")
async def voice_prepare_endpoint():
    """Loads speech recognition ahead of time (a conversation or "Hey Xod" listening is starting)."""
    if sys.platform != "darwin":
        return {**VOICE_MAC_ONLY, "ready": False}
    from app.services import speech_to_text
    return await speech_to_text.prepare()


@app.post("/api/voice/wake")
async def voice_wake_endpoint(request: Request):
    """Hands-free: was this short clip "Hey Xod…"? Returns the transcript, wake and the rest of the sentence."""
    if sys.platform != "darwin":
        return VOICE_MAC_ONLY
    from app.services import speech_to_text
    return await speech_to_text.wake(await request.body())

@app.post("/api/voice/command")
async def voice_command_endpoint(req: VoiceCommandRequest):
    """Executes spoken computer command with sub-400ms regex routing and trace capture."""
    return await voice_engine.process_voice_command(query=req.query, speak_back=bool(req.speak_back), safe_only=bool(req.safe_only))

@app.post("/api/voice/tts")
async def voice_tts_endpoint(req: VoiceTTSRequest):
    """Speaks text using macOS native speech synthesizer without blocking."""
    success = await voice_engine.speak_native(text=req.text, voice=req.voice)
    return {"success": success, "text": req.text}


# ---- Spoken replies (services/speech.py): the reply as audio, a sentence or so at a time

@app.get("/api/speech")
async def speech_status():
    from app.services import speech
    return await speech.status()


@app.post("/api/speech/say")
async def speech_say(req: SpeechRequest):
    from fastapi.responses import Response
    from app.services import speech
    try:
        audio = await speech.synthesize(req.text, voice=req.voice, rate=req.rate)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    if not audio:
        return Response(status_code=204)
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})


class SpeechLines(BaseModel):
    texts: List[str]


@app.post("/api/speech/prepare")
async def speech_prepare(req: SpeechLines):
    from app.services import speech
    return {"preparing": speech.prepare_lines(req.texts)}


@app.post("/api/speech/settings")
async def speech_settings(req: SpeechSettings):
    from app.services import speech
    try:
        return {"success": True, "settings": await speech.save_settings(req.mode, req.voice, req.rate, req.wake)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/speech/open-voice-settings")
async def speech_open_voice_settings():
    """Opens System Settings at Spoken Content, where Premium voices are downloaded."""
    from app.services import speech
    return {"success": await speech.open_voice_settings()}

@app.get("/api/lab/stats")
async def lab_stats_endpoint():
    """Returns Model Lab telemetry: clusters, trace counts, readiness for on-device distillation."""
    return get_lab_stats()

@app.post("/api/lab/export")
async def lab_export_endpoint(req: LabExportRequest):
    """Exports trace dataset in chat JSONL format for MLX LoRA distillation."""
    return export_training_data(specialist=req.specialist or "intent_router")

@app.post("/api/lab/feedback")
async def lab_feedback_endpoint(req: TraceFeedbackRequest):
    """Marks trace as high quality (+1) or low quality (-1)."""
    ok = set_feedback(trace_id=req.trace_id, value=req.value)
    return {"success": ok, "trace_id": req.trace_id, "value": req.value}

from app.services.finetuning import finetuning_service

class StartTrainingRequest(BaseModel):
    specialist: Optional[str] = "browser_navigator"
    base_model: Optional[str] = None   # None: the specialist's recommended student
    iters: Optional[int] = None        # None: sized to the dataset
    batch_size: Optional[int] = 1
    learning_rate: Optional[float] = 1e-4


class SpecialistToggleRequest(BaseModel):
    enabled: bool


class GroupFeedbackRequest(BaseModel):
    group_id: str
    value: int


@app.post("/api/lab/train")
async def lab_train_start_endpoint(req: StartTrainingRequest):
    """Builds the dataset from good examples and starts on-device MLX LoRA training."""
    return await finetuning_service.start_training_job(
        specialist=req.specialist or "browser_navigator",
        base_model=req.base_model,
        iters=req.iters,
        batch_size=req.batch_size or 1,
        learning_rate=req.learning_rate or 1e-4,
    )


@app.get("/api/lab/jobs")
async def lab_jobs_list_endpoint():
    """All training runs (survives restarts), newest first."""
    return {"jobs": finetuning_service.list_jobs()}


@app.get("/api/lab/jobs/{job_id}")
async def lab_job_status_endpoint(job_id: str):
    """Live loss curve, logs, and test results of a training run."""
    return finetuning_service.get_job_status(job_id=job_id)


@app.post("/api/lab/jobs/{job_id}/cancel")
async def lab_job_cancel_endpoint(job_id: str):
    return await finetuning_service.cancel(job_id)


@app.post("/api/lab/jobs/{job_id}/evaluate")
async def lab_job_evaluate_endpoint(job_id: str):
    """Re-runs the held-out test for a trained student."""
    return await finetuning_service.evaluate(job_id)


@app.get("/api/lab/specialists")
async def lab_specialists_endpoint():
    """Mini models Xod can learn from its own work: data collected, readiness, test results, live status."""
    from app.services import specialists, dataset_recorder, turn_quality
    from app.services.finetuning import STUDENT_CHOICES
    return {
        "specialists": specialists.overview(),
        "tasks": dataset_recorder.task_stats(),
        "turns": turn_quality.stats(),      # how chat turns went: corrections, thanks, self-checks (labels the examples)
        "student_choices": STUDENT_CHOICES,
        "promote_rules": {"min_valid_json": specialists.PROMOTE_MIN_VALID_JSON,
                          "min_action_match": specialists.PROMOTE_MIN_ACTION_MATCH},
    }


@app.post("/api/lab/specialists/{name}/learn-from-mistakes")
async def lab_learn_from_mistakes_endpoint(name: str):
    """Preference-trains the current student on corrected failures and its own mistakes (DPO)."""
    return await finetuning_service.start_preference_job(name)


@app.post("/api/lab/specialists/{name}/enabled")
async def lab_specialist_toggle_endpoint(name: str, req: SpecialistToggleRequest):
    """Switches a tested specialist on (it takes over that task's fast steps) or off."""
    from app.services import specialists
    return await specialists.set_enabled(name, req.enabled)


@app.post("/api/lab/feedback/group")
async def lab_group_feedback_endpoint(req: GroupFeedbackRequest):
    """Thumbs up/down on a whole run (e.g. a browser task) labels all of its examples."""
    from app.services import dataset_recorder
    return {"success": True, "labelled": dataset_recorder.label_group(req.group_id, req.value, "user feedback")}


@app.on_event("startup")
async def _restart_enabled_specialists():
    from app.services import specialists
    asyncio.create_task(specialists.start_enabled_servers())


# ==============================================================
# LOCAL MEDIA STUDIO (On-Device Image & Video Editor)
# ==============================================================
from app.services.media_studio import local_media_service

class ImageGenerateRequest(BaseModel):
    prompt: str
    style: Optional[str] = "photorealistic"
    width: Optional[int] = 512
    height: Optional[int] = 512
    seed: Optional[int] = None
    model: Optional[str] = None
    loras: Optional[List[Dict[str, Any]]] = None
    negative_prompt: Optional[str] = None

@app.post("/api/media/image/generate")
async def media_image_generate_endpoint(req: ImageGenerateRequest):
    """Generates an image on this Mac (memory-managed media worker)."""
    return await local_media_service.generate_ai_image(
        prompt=req.prompt,
        style=req.style or "photorealistic",
        width=req.width or 512,
        height=req.height or 512,
        seed=req.seed,
        model=req.model,
        loras=req.loras,
        negative_prompt=req.negative_prompt,
    )


@app.get("/api/media/engine")
async def media_engine_status():
    """Installed image/video models and LoRAs (usable or not, and why), plus live memory state."""
    from app.services.media_runtime import list_models, list_loras, media_runtime
    from app.services.memory_broker import memory_broker
    from app.services.media_model_manager import media_model_manager
    cfg = media_model_manager.config
    from app.services import media_packages
    return {"models": list_models(), "loras": list_loras(), "runtime": media_runtime.status(),
            "libraries": media_packages.status(),
            "memory": memory_broker.status(),
            "active": {"image": cfg.get("active_image_model", ""), "video": cfg.get("active_video_model", "")}}


@app.post("/api/media/libraries/install")
async def media_libraries_install():
    """Starts the one-time download of Media Studio's libraries (the Mac app ships without them)."""
    from app.services import media_packages
    return media_packages.start_install()


@app.post("/api/media/engine/release")
async def media_engine_release():
    """Stops the media worker now, giving its memory back (the chat model returns on its next use)."""
    from app.services.media_runtime import media_runtime
    from app.services.memory_broker import memory_broker
    stopped = await media_runtime.release(full=True)
    await memory_broker.ensure_teacher()
    return {"success": True, "stopped": stopped}

class ImageEditRequest(BaseModel):
    image_path: str
    operations: List[Dict[str, Any]]
    output_format: Optional[str] = "png"

class ImageCreateRequest(BaseModel):
    title: str
    subtitle: Optional[str] = ""
    theme: Optional[str] = "dark_cyber"
    width: Optional[int] = 1200
    height: Optional[int] = 675

class VideoTrimRequest(BaseModel):
    video_path: str
    start_sec: float
    duration_sec: float
    output_format: Optional[str] = "mp4"

class VideoGifRequest(BaseModel):
    video_path: str
    start_sec: Optional[float] = 0.0
    duration_sec: Optional[float] = 5.0
    fps: Optional[int] = 12
    scale_width: Optional[int] = 480

class VideoOverlayRequest(BaseModel):
    video_path: str
    text: str
    position: Optional[str] = "bottom"
    font_size: Optional[int] = 32

@app.post("/api/media/image/edit")
async def media_image_edit_endpoint(req: ImageEditRequest):
    """Edits, crops, adjusts, filters, or watermarks an image on-device."""
    return local_media_service.edit_image(
        image_path=req.image_path,
        operations=req.operations,
        output_format=req.output_format
    )

@app.post("/api/media/image/create")
async def media_image_create_endpoint(req: ImageCreateRequest):
    """Generates high-res branded graphics on-device."""
    return local_media_service.create_image_from_template(
        title=req.title,
        subtitle=req.subtitle or "",
        theme=req.theme or "dark_cyber",
        width=req.width or 1200,
        height=req.height or 675
    )

@app.post("/api/media/video/trim")
async def media_video_trim_endpoint(req: VideoTrimRequest):
    """Trims a video segment on-device using local Apple Silicon ffmpeg."""
    return await local_media_service.trim_video(
        video_path=req.video_path,
        start_sec=req.start_sec,
        duration_sec=req.duration_sec,
        output_format=req.output_format or "mp4"
    )

@app.post("/api/media/video/gif")
async def media_video_gif_endpoint(req: VideoGifRequest):
    """Converts a video segment into an animated GIF on-device."""
    return await local_media_service.convert_to_gif(
        video_path=req.video_path,
        start_sec=req.start_sec or 0.0,
        duration_sec=req.duration_sec or 5.0,
        fps=req.fps or 12,
        scale_width=req.scale_width or 480
    )

@app.post("/api/media/video/overlay")
async def media_video_overlay_endpoint(req: VideoOverlayRequest):
    """Adds styled text overlay / lower thirds to a video on-device."""
    return await local_media_service.add_text_overlay(
        video_path=req.video_path,
        text=req.text,
        position=req.position or "bottom",
        font_size=req.font_size or 32
    )

@app.get("/api/media/list")
async def media_list_endpoint():
    """Lists all on-device generated and edited media artifacts."""
    return {"media": local_media_service.list_created_media()}

class VideoSpliceRequest(BaseModel):
    video_paths: List[str]
    transition: Optional[str] = None
    output_format: Optional[str] = "mp4"

class VideoExtractFrameRequest(BaseModel):
    video_path: str
    frame_type: Optional[str] = "first"  # "first" | "last" | "timestamp"
    timestamp_sec: Optional[float] = None
    output_format: Optional[str] = "png"

class VideoFromFrameRequest(BaseModel):
    image_path: str
    duration_sec: Optional[float] = 3.0
    fps: Optional[int] = 30
    motion: Optional[str] = "zoom_in"
    audio_path: Optional[str] = None
    output_format: Optional[str] = "mp4"

@app.post("/api/media/video/splice")
async def media_video_splice_endpoint(req: VideoSpliceRequest):
    """Splices multiple local video clips together on-device."""
    return await local_media_service.splice_videos(
        video_paths=req.video_paths,
        transition=req.transition,
        output_format=req.output_format or "mp4"
    )

@app.post("/api/media/video/extract_frame")
async def media_video_extract_frame_endpoint(req: VideoExtractFrameRequest):
    """Extracts first, last, or specific timestamp frame from a video on-device."""
    return await local_media_service.extract_frame(
        video_path=req.video_path,
        frame_type=req.frame_type or "first",
        timestamp_sec=req.timestamp_sec,
        output_format=req.output_format or "png"
    )

@app.post("/api/media/video/from_frame")
async def media_video_from_frame_endpoint(req: VideoFromFrameRequest):
    """Creates a motion video from an extracted frame or image on-device."""
    return await local_media_service.create_video_from_frame(
        image_path=req.image_path,
        duration_sec=req.duration_sec or 3.0,
        fps=req.fps or 30,
        motion=req.motion or "zoom_in",
        audio_path=req.audio_path,
        output_format=req.output_format or "mp4"
    )


class VideoGenerateRequest(BaseModel):
    prompt: str
    duration_sec: Optional[float] = 2.0
    fps: Optional[int] = 24
    motion: Optional[str] = None
    style: Optional[str] = "photorealistic"
    model: Optional[str] = None
    loras: Optional[List[Dict[str, Any]]] = None
    seed: Optional[int] = None
    negative_prompt: Optional[str] = None

@app.post("/api/media/video/generate")
async def media_video_generate_endpoint(req: VideoGenerateRequest):
    """Synthesizes an AI motion video directly from a descriptive text prompt on-device."""
    return await local_media_service.generate_ai_video(
        prompt=req.prompt,
        duration_sec=req.duration_sec or 2.0,
        fps=req.fps or 24,
        motion=req.motion,
        style=req.style or "photorealistic",
        model=req.model,
        loras=req.loras,
        seed=req.seed,
        negative_prompt=req.negative_prompt,
    )


class MediaModelActionRequest(BaseModel):
    model_id: str
    model_type: Optional[str] = "image"

class MediaModelRevealRequest(BaseModel):
    model_id: str


# -------------------------------------------------------------
# ON-DEVICE MEDIA MODEL STUDIO & DISCOVERY ENDPOINTS
# -------------------------------------------------------------
@app.get("/api/media/models/status")
async def media_models_status_endpoint():
    """Returns active models, downloaded on-device models inventory, and hardware acceleration status."""
    return media_model_manager.get_status()

@app.get("/api/media/models/curated")
async def media_models_curated_endpoint():
    """Returns curated image and video models optimized for Apple Silicon."""
    return {"models": media_model_manager.list_curated_models()}

@app.get("/api/media/models/search")
async def media_models_search_endpoint(q: str = "", type: Optional[str] = None, limit: int = 15):
    """Queries Hugging Face Hub live for text-to-image or text-to-video models."""
    return {"models": media_model_manager.search_huggingface_models(query=q, model_type=type, limit=limit)}

@app.post("/api/media/models/activate")
async def media_models_activate_endpoint(req: MediaModelActionRequest):
    """Sets a model as active default for image or video generation."""
    return media_model_manager.set_active_model(model_id=req.model_id, model_type=req.model_type or "image")

@app.post("/api/media/models/download")
async def media_models_download_endpoint(req: MediaModelActionRequest):
    """Starts 1-click background download of a model to the local device."""
    return media_model_manager.start_download_job(model_id=req.model_id, model_type=req.model_type or "image")

@app.get("/api/media/models/download/{job_id}")
async def media_models_download_status_endpoint(job_id: str):
    """Checks the status of an ongoing model download."""
    return media_model_manager.get_download_status(job_id)

@app.delete("/api/media/models/{model_id:path}")
async def media_models_delete_endpoint(model_id: str):
    """Deletes a local model folder from disk to reclaim storage."""
    return media_model_manager.delete_model(model_id)

@app.post("/api/media/models/reveal")
async def media_models_reveal_endpoint(req: MediaModelRevealRequest):
    """Reveals the model directory directly in macOS Finder."""
    return media_model_manager.reveal_in_finder(req.model_id)

class MediaHfTokenRequest(BaseModel):
    token: str

@app.post("/api/media/models/token")
async def media_models_token_endpoint(req: MediaHfTokenRequest):
    """Saves and validates Hugging Face user access token."""
    return media_model_manager.set_hf_token(req.token)

@app.get("/api/media/models/token/test")
async def media_models_token_test_endpoint():
    """Validates the currently configured Hugging Face user token."""
    return media_model_manager.test_hf_token()


# =====================================================================
# PERSISTENT LONG-TERM MEMORY (SQLite Vector Vault) ENDPOINTS
# =====================================================================

class MemorySearchRequest(BaseModel):
    query: str
    category: Optional[str] = None
    limit: Optional[int] = 10

class MemoryStoreRequest(BaseModel):
    title: str
    content: str
    category: Optional[str] = "general"
    tags: Optional[List[str]] = None
    importance: Optional[int] = 3
    source_session_id: Optional[str] = None

class MemoryIndexWorkspaceRequest(BaseModel):
    workspace_dir: Optional[str] = None

@app.get("/api/memory/list")
async def memory_list_endpoint(category: Optional[str] = None, limit: int = 50):
    """Lists memories from persistent vector vault."""
    return memory_service.list_memories(category=category, limit=limit)

@app.post("/api/memory/search")
async def memory_search_endpoint(req: MemorySearchRequest):
    """Performs hybrid vector cosine + FTS5 semantic search."""
    results = await memory_service.search_memories_async(
        query=req.query,
        category=req.category,
        limit=req.limit or 10
    )
    return {"query": req.query, "count": len(results), "results": results}

@app.post("/api/memory/store")
async def memory_store_endpoint(req: MemoryStoreRequest):
    """Stores a new permanent memory with dense vector embedding."""
    mem = await memory_service.store_memory_async(
        title=req.title,
        content=req.content,
        category=req.category or "general",
        tags=req.tags,
        importance=req.importance or 3,
        source_session_id=req.source_session_id
    )
    return mem

@app.delete("/api/memory/{memory_id}")
async def memory_delete_endpoint(memory_id: str):
    """Permanently deletes a memory by ID."""
    success = memory_service.delete_memory(memory_id)
    return {"success": success, "id": memory_id}

@app.get("/api/memory/stats")
async def memory_stats_endpoint():
    """Returns memory vault capacity, embedding model, and category distribution."""
    return memory_service.get_stats()


@app.get("/api/memory/store")
async def memory_store_endpoint():
    """Unified memory store: backend (chroma embedded / chroma server / sqlite fallback) and counts."""
    from app.services import memory_store
    return memory_store.stats()


@app.post("/api/memory/store/migrate")
async def memory_store_migrate_endpoint():
    """Imports about_you facts, legacy memories and recall episodes into the unified store (idempotent)."""
    from app.services import memory_store
    return memory_store.migrate()


@app.post("/api/memory/store/search")
async def memory_store_search_endpoint(req: MemorySearchRequest):
    """Searches facts + memories + episodes together in the unified store."""
    kinds = [k for k in (req.category or "").split(",") if k in ("fact", "memory", "episode")] or None
    results = await memory_service.search_unified_async(query=req.query, kinds=kinds, limit=req.limit or 10)
    return {"query": req.query, "count": len(results), "results": results}

@app.post("/api/memory/index-workspace")
async def memory_index_workspace_endpoint(req: Optional[MemoryIndexWorkspaceRequest] = None):
    """Scans and indexes workspace repository into persistent memory vault."""
    ws_path = req.workspace_dir if req else None
    res = await memory_service.auto_index_workspace(workspace_path=ws_path)
    return {"status": "indexed", "count": res.get("indexed_memories", 0), "result": res}










# ---------------------------------------------------------------------------------------------
# Previews of apps the builder makes (services/previews.py). The pages are untrusted code, served
# sandboxed under a per-start secret path; the API below is for Xod's own UI.

@app.get("/preview/{token}/{rid}/{path:path}")
async def preview_file(token: str, rid: str, path: str = ""):
    from fastapi.responses import Response
    from app.services import previews
    if token != previews.TOKEN:
        raise HTTPException(status_code=404, detail="This preview link has expired. Open the preview again.")
    p = previews.lib_file(path) if rid == "__lib" else previews.resolve(rid, path)
    if not p:   # with the preview headers, so the page's console says "404" rather than a CORS error
        return Response(content=b"Not found", status_code=404, media_type="text/plain", headers=dict(previews.PREVIEW_HEADERS))
    body = p.read_bytes()
    if p.suffix.lower() in (".html", ".htm") and rid != "__lib":
        body = previews.inject(body.decode("utf-8", errors="replace"), rid).encode()
    return Response(content=body, media_type=previews.content_type(p), headers=dict(previews.PREVIEW_HEADERS))


@app.get("/api/preview/apps")
async def preview_apps(folder: Optional[str] = None):
    from app.services import previews
    return {"apps": previews.find_apps(folder)}


@app.get("/api/preview/files")
async def preview_files(root: str):
    from app.services import previews
    r = previews.root_of(root)
    if not r:
        raise HTTPException(status_code=404, detail="Unknown preview")
    return {"root": root, "path": str(r), "files": [f for f in previews.list_files(root) if f["path"] != previews.APP_MARK]}


@app.get("/api/preview/file")
async def preview_file_text(root: str, path: str):
    from app.services import previews
    p = previews.resolve(root, path)
    if not p or p.suffix.lower() not in previews.TEXT_EXT:
        raise HTTPException(status_code=404, detail="Not found")
    return {"path": path, "text": p.read_text(errors="replace")[:400000]}


class PreviewStorageRequest(BaseModel):
    root: str
    data: Dict[str, Any]


@app.post("/api/preview/storage")
async def preview_storage(req: PreviewStorageRequest):
    """What a preview saved in localStorage (its high scores, settings), kept in the app's folder."""
    from app.services import previews
    return {"ok": previews.save_storage(req.root, req.data)}


class PreviewLaunchRequest(BaseModel):
    url: str


@app.post("/api/preview/launch")
async def preview_launch(req: PreviewLaunchRequest):
    """Opens a built app in the default browser (when the page couldn't open a tab itself)."""
    import subprocess as _sp
    from app.services import previews
    if not req.url.startswith(previews.PREFIX):
        raise HTTPException(status_code=400, detail="Not a preview")
    base = previews.SERVER_BASE or "http://127.0.0.1:8000"
    if sys.platform == "win32":
        import webbrowser
        return {"ok": webbrowser.open(base + req.url)}
    _sp.Popen(["open", base + req.url], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
    return {"ok": True}


class PreviewCheckRequest(BaseModel):
    url: str


@app.post("/api/preview/check")
async def preview_check(req: PreviewCheckRequest):
    from app.services import previews
    if not req.url.startswith(previews.PREFIX):
        raise HTTPException(status_code=400, detail="Not a preview")
    return await previews.check(req.url)


# ---------------------------------------------------------------------------------------------
# Xod's inner life (services/soul.py): mood, bond, growth, what it learned, and its journal.

@app.get("/api/soul")
async def soul_state():
    from app.services import soul
    return soul.public()


class SoulEvent(BaseModel):
    kind: str
    detail: str = ""


@app.post("/api/soul/event")
async def soul_event(req: SoulEvent):
    """From the page: petting, talking, and reactions to its journal."""
    from app.services import soul
    # Treats spend stardust it earned on the server; play and wishes are capped per day there too.
    if req.kind not in ("pet", "spoke", "liked", "dismissed", "treat", "play", "wish"):
        raise HTTPException(status_code=400, detail="Unknown event")
    return soul.event(req.kind, req.detail[:40])


@app.post("/api/soul/presence")
async def soul_presence():
    from app.services import soul
    soul.presence()
    return {"ok": True}


@app.post("/api/soul/settings")
async def soul_settings(request: Request):
    from app.services import soul
    return soul.settings(await request.json())


class SoulForget(BaseModel):
    what: str
    value: str = ""


@app.post("/api/soul/forget")
async def soul_forget(req: SoulForget):
    from app.services import soul
    if req.what not in ("interest", "name", "style", "journal", "everything"):
        raise HTTPException(status_code=400, detail="Unknown")
    return soul.forget(req.what, req.value)


class SoulReaction(BaseModel):
    reaction: str


@app.post("/api/soul/journal/{entry_id}")
async def soul_react(entry_id: str, req: SoulReaction):
    from app.services import soul
    if req.reaction not in ("liked", "dismissed", "seen", "seen-all"):
        raise HTTPException(status_code=400, detail="Unknown reaction")
    return soul.react(entry_id, req.reaction)


@app.post("/api/soul/muse")
async def soul_muse(request: Request):
    """'What are you thinking about?': it takes a moment for itself now instead of waiting until you're away."""
    from app.services import soul, activity
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    activity.origin.set("xod (on its own)")
    entry = await soul.muse(body.get("activity") if body.get("activity") in soul.ACTIVITIES else None, asked=True)
    return {"entry": entry, "state": soul.public()}


# ---------------------------------------------------------------------------------------------
# Job hunting (services/job_hunt.py, agent/jobs.py): results, your job profile, applying.

from app.agent.jobs import PROFILE_FIELDS  # noqa: E402


@app.get("/api/jobs/last")
async def jobs_last():
    from app.services import job_hunt
    return job_hunt.last() or {"jobs": []}


@app.get("/api/jobs/profile")
async def jobs_profile():
    """Your details for job applications (on this Mac only; the form shows them back to you)."""
    from app.services.browser_sandbox import PROFILE_PATH
    from app.agent.jobs import profile_status
    try:
        data = json.loads(Path(PROFILE_PATH).read_text())
    except Exception:
        data = {}
    return {"profile": {k: data.get(k, "") for k in PROFILE_FIELDS}, "status": profile_status()}


@app.post("/api/jobs/profile")
async def jobs_profile_save(request: Request):
    from app.agent.jobs import profile_status, save_profile
    save_profile(await request.json())
    return {"ok": True, "status": profile_status()}


@app.get("/api/jobs/resumes")
async def jobs_resumes():
    """Résumé / CV files on this computer and in the Google Drive for desktop folder, so you can pick yours."""
    from app.services import job_autofill
    return {"files": await asyncio.to_thread(job_autofill.local_files)}


class JobsAutofillRequest(BaseModel):
    current: Dict[str, Any] = {}            # what's in the form now (only empty boxes are filled)
    pick: Optional[Dict[str, Any]] = None   # a résumé the person picked from "choices"
    save: bool = False                      # "Do it all": save it too


@app.post("/api/jobs/profile/autofill")
async def jobs_profile_autofill(req: JobsAutofillRequest):
    """Fills your job profile from your résumé (this computer, or Google Drive when connected) and what Xod knows about you."""
    from app.services import job_autofill
    from app.services.browser_sandbox import load_user_profile
    from app.agent.jobs import profile_status, save_profile
    current = {**load_user_profile(), **{k: v for k, v in (req.current or {}).items() if isinstance(v, str) and v.strip()}}
    r = await job_autofill.autofill(current, files=[req.pick] if req.pick else None)
    if req.save and r["fields"]:
        save_profile({**{k: v for k, v in (req.current or {}).items() if isinstance(v, str)}, **r["fields"]})
    return {**r, "status": profile_status()}


class JobsApplyRequest(BaseModel):
    ids: List[str]


@app.post("/api/jobs/apply")
async def jobs_apply(req: JobsApplyRequest):
    """Starts application tasks for jobs from the last search. Each stops for your OK before submitting."""
    from app.services import job_hunt
    from app.agent import jobs as job_lane
    status = job_lane.profile_status()
    if not status["ready"]:
        job_lane._pending[:] = list(req.ids)      # "do it all" in the chat afterwards applies to these
        return {"ok": False, "needs_profile": True, "missing": status["missing"]}
    last = job_hunt.last() or {}
    chosen = [j for j in last.get("jobs", []) if j.get("id") in set(req.ids)]
    if not chosen:
        return {"ok": False, "error": "Those jobs aren't in the last search any more. Search again."}
    return {"ok": True, "tasks": await job_lane.apply(chosen)}
