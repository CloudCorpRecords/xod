"""
What Xod can do right now, on this computer with this model, and why not when it can't.

report() looks at the chat model (does it answer, call tools, see, think, how much it reads at once, its score),
the helpers (the vision model, memory search), the computer (browser, shell, voice, images and video) and what's
connected (apps, phone, cloud keys). Each ability is:

  ok        works
  limited   works, but less well, and how (e.g. "a small vision model describes images for the chat model")
  off       doesn't work here, why, and what would fix it

The models panel shows it; the agent answers "what can't you do?" / "why can't you see images?" from it (as_text()),
so the answer is about this setup, not a guess. tool_blocks() also tells the agent which tools to leave out because
they can't work here (no browser → no browser tools), instead of trying them and failing.
"""
import asyncio
import os
import sys
from typing import Any, Dict, List, Optional


def _item(cid: str, name: str, status: str, why: str, fix: Optional[Dict[str, str]] = None, tools: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"id": cid, "name": name, "status": status, "why": why, "fix": fix, "tools": tools or []}


def _browser() -> Optional[str]:
    from app.services.browser_sandbox import CHROME_BINARIES
    return next((p for p in CHROME_BINARIES if p and os.path.exists(p)), None)


async def _chat_model() -> Dict[str, Any]:
    """What the chat model is and can do (None where Xod can't tell)."""
    from app.config import settings
    from app.services.llm import CLOUD_PROVIDERS, PROVIDER_NAMES
    from app.services import model_hub, model_scores
    p, m = settings.llm_provider, settings.llm_model
    info = {"provider": p, "model": m, "server": PROVIDER_NAMES.get(p, p), "tools": None, "vision": None, "thinking": None,
            "context": None, "problem": None, "score": model_scores.score_of(p, m)}
    if p in CLOUD_PROVIDERS:
        info.update(tools=True, vision=p in model_hub.CLOUD_SEES)
        key_field = f"{p}_api_key"
        if hasattr(settings, key_field) and not getattr(settings, key_field, ""):
            info["problem"] = f"there's no {PROVIDER_NAMES.get(p, p)} key saved"
        return info
    from app.services import autoconnect, model_sources
    servers = await autoconnect.discover()
    try:
        servers += [s for s in await model_sources.discover() if s["online"]]
    except Exception:
        pass
    info["problem"] = await autoconnect._usable(p, m, servers)
    if p == "ollama":
        from app.services import ollama_hub
        try:
            for x in await ollama_hub.installed():
                if model_hub._same(x["name"], m):
                    info.update(tools=x["tools"], vision=x["vision"], thinking=x["thinking"], context=x.get("context"))
        except Exception:
            pass
    elif p == "lm_studio":
        from app.services.llm import llm_service
        try:
            for x in (await llm_service.get_lmstudio_status()).get("models", []):
                if x.get("key") == m:
                    info.update(tools=bool((x.get("capabilities") or {}).get("trained_for_tool_use")), vision=x.get("type") == "vlm",
                                context=x.get("max_context_length"))
        except Exception:
            pass
    elif p == "xod":
        info.update(tools=True, vision=False)
    return info


async def report() -> Dict[str, Any]:
    from app.config import settings
    from app.services import vision
    chat = await _chat_model()
    items: List[Dict[str, Any]] = []
    name = f"{chat['model']} ({chat['server']})"

    # The model itself.
    if chat["problem"]:
        items.append(_item("chat", "Answering", "off", f"{name} can't answer now: {chat['problem']}.", {"label": "Pick a model", "action": "models"}))
    else:
        items.append(_item("chat", "Answering", "ok", f"{name} answers." + (f" Its score on this computer: {chat['score']}/100." if chat["score"] is not None else "")))
    if chat["tools"] is False:
        items.append(_item("tools", "Using tools (files, browser, apps, commands)", "limited",
                           f"{chat['model']} wasn't made to call tools, so Xod writes out each step as text for it to follow: slower and "
                           "it misses more often. Agent tasks, connected apps and building apps all depend on this.",
                           {"label": "Pick a model that calls tools", "action": "models"}))
    elif chat["tools"] is None:
        items.append(_item("tools", "Using tools (files, browser, apps, commands)", "limited",
                           f"Xod can't tell whether {chat['model']} calls tools well; it will try. Testing the model checks it.",
                           {"label": "Test the model", "action": "test"}))
    else:
        items.append(_item("tools", "Using tools (files, browser, apps, commands)", "ok", f"{chat['model']} calls tools."))
    if chat["context"] and chat["context"] < 8192:
        items.append(_item("context", "Reading long pages and files", "limited",
                           f"{chat['model']} reads about {chat['context'] // 1000}k tokens at once, so long pages and files are cut short.",
                           {"label": "Pick a model with a longer context", "action": "models"}))

    # Seeing.
    sees = chat["vision"] if chat["vision"] is not None else await vision.main_sees()
    helper = await vision.model() if not sees else None
    if sees:
        items.append(_item("vision", "Seeing (pictures, web pages)", "ok", f"{chat['model']} sees images itself."))
    elif helper:
        items.append(_item("vision", "Seeing (pictures, web pages)", "limited",
                           f"{chat['model']} reads text only; {helper} looks instead and describes pictures and browser screenshots to it. "
                           "Fine detail can be lost in the description, and it's a little slower."))
    else:
        items.append(_item("vision", "Seeing (pictures, web pages)", "off",
                           f"{chat['model']} reads text only and there's no vision model, so attached pictures can't be read and the browser works "
                           "from the page's text alone.", {"label": "Get a vision model", "action": "get_vision"}))

    # The computer.
    browser = _browser()
    items.append(_item("browser", "Browsing the web and filling in forms", "ok" if browser else "off",
                       "Xod's own browser runs in Chrome or Edge." if browser else "No Chrome or Edge on this computer, so Xod can't open web pages.",
                       None if browser else {"label": "Install Chrome", "action": "link:https://www.google.com/chrome/"},
                       tools=[] if browser else ["browser_*", "web_scrape"]))
    keys = [n for n, k in (("Tavily", "tavily_api_key"), ("You.com", "you_api_key")) if getattr(settings, k, "")]
    items.append(_item("web", "Searching the web", "ok" if browser or keys else "off",
                       ("Searches with " + " and ".join(keys) if keys else "Searches in its own browser and reads the top pages (no key needed).")
                       if browser or keys else "Needs a browser or a search key.", None))
    shell = "Windows PowerShell" if sys.platform == "win32" else "the Terminal (zsh)" if sys.platform == "darwin" else "the shell"
    items.append(_item("computer", "Files, folders and commands", "ok", f"Your files, and commands in {shell}. Deleting goes to the "
                       + ("Recycle Bin" if sys.platform == "win32" else "Trash") + ", and changes can be undone."))
    try:
        from app.services import embeddings
        mem_ok = bool(await embeddings.nomic(["search_query: test"], timeout=3))
    except Exception:
        mem_ok = False
    items.append(_item("memory", "Searching its memory by meaning", "ok" if mem_ok else "limited",
                       "Finds past chats and notes by what they mean." if mem_ok else
                       "No embedding model answering (nomic-embed-text in Ollama or LM Studio), so memory search matches words only.",
                       None if mem_ok else {"label": "Get it", "action": "pull:nomic-embed-text"}))
    if sys.platform == "darwin":
        items.append(_item("voice", "Talking to it (voice)", "ok", "Speech recognition and voices run on this Mac."))
    else:
        items.append(_item("voice", "Talking to it (voice)", "limited",
                           "Spoken replies work with Windows' own voices, but talking to Xod (speech recognition) runs on a Mac only for now: type instead."))
    try:
        from app.services import media_packages
        media = media_packages.installed("media")
    except Exception:
        media = False
    items.append(_item("media", "Making images and video", "ok" if media else "limited",
                       "The image and video engine is installed." if media else
                       "Needs a one-time download of the image engine (about 600 MB); it starts by itself the first time you ask.",
                       None if media else {"label": "Download it now", "action": "install_media"}))

    # What's connected.
    try:
        from app.services.mcp_client import mcp_hub
        connected = [s for s in mcp_hub.status() if s.get("connected")]
    except Exception:
        connected = []
    if connected:
        names = ", ".join(f"{s['name']} ({len(s.get('tools') or [])} tools)" for s in connected)
        items.append(_item("apps", "Connected apps", "limited" if chat["tools"] is False else "ok",
                           f"Connected: {names}." + (" They need a model that calls tools to be used well." if chat["tools"] is False else "")))
    else:
        items.append(_item("apps", "Connected apps", "off",
                           "Nothing connected yet (calendars, notes, Notion, GitHub…)." + (" This Mac's Calendar, Reminders and Mail are Mac only."
                                                                                            if sys.platform != "darwin" else ""),
                           {"label": "Connect an app", "action": "connectors"}))
    try:
        from app.services import phone
        linked = phone.status()["paired"]
    except Exception:
        linked = False
    items.append(_item("phone", "From your phone", "ok" if linked else "off",
                       "Linked through Telegram." if linked else "Not linked: you can message Xod from your phone with a Telegram bot of your own.",
                       None if linked else {"label": "Link your phone", "action": "phone"}))
    from app.services.llm import CLOUD_PROVIDERS
    cloud = [p for p in CLOUD_PROVIDERS if p != "custom" and getattr(settings, f"{p}_api_key", "")]
    if settings.llm_provider not in CLOUD_PROVIDERS:
        items.append(_item("cloud", "Help from a cloud model on hard steps", "ok" if cloud else "off",
                           f"With your permission, hard steps can go to {', '.join(c.title() for c in cloud)}." if cloud else
                           "No cloud key saved: everything stays on this computer.", None if cloud else {"label": "Add a key", "action": "keys"}))
    if chat["tools"] is not False and browser:
        items.append(_item("build", "Building apps and games", "ok", "Writes, runs and tests them here, with a live preview."))
    else:
        items.append(_item("build", "Building apps and games", "limited",
                           "Works best with a model that calls tools and a browser to test in; " +
                           ("this model can't call tools." if chat["tools"] is False else "there's no browser.")))
    counts = {s: sum(1 for i in items if i["status"] == s) for s in ("ok", "limited", "off")}
    return {"model": chat, "items": items, "counts": counts}


def as_text(rep: Dict[str, Any]) -> str:
    """The report in words, for the agent to answer from."""
    mark = {"ok": "WORKS", "limited": "LIMITED", "off": "NOT AVAILABLE"}
    lines = [f"- {i['name']}: {mark[i['status']]}. {i['why']}" + (f" Fix: {i['fix']['label']}." if i.get("fix") else "") for i in rep["items"]]
    return "WHAT XOD CAN DO RIGHT NOW (checked just now on this computer):\n" + "\n".join(lines)


def tool_blocks(rep: Dict[str, Any]) -> List[str]:
    """Tool name patterns that can't work here (leave them out rather than fail)."""
    return [t for i in rep["items"] if i["status"] == "off" for t in i.get("tools") or []]


_cache: Dict[str, Any] = {"at": 0.0, "rep": None}


async def cached(max_age: float = 60) -> Dict[str, Any]:
    import time
    if not _cache["rep"] or time.time() - _cache["at"] > max_age:
        _cache.update(rep=await report(), at=time.time())
    return _cache["rep"]
