"""
Xod on your phone, through Telegram.

You make a Telegram bot of your own (BotFather, one minute), paste its token into Xod, and send the bot the pairing
code Xod shows. From then on:

  you write to the bot        → it becomes a task in Xod's task inbox (services/task_queue.py) and runs on this computer
  a task needs your OK        → the bot asks, with Approve / Cancel buttons
  a task has a question       → the bot asks; reply to that message to answer
  a task finishes             → the bot sends the result
  a connected app needs an OK → Approve / Decline buttons too (services/mcp_client.py)

Nothing is opened to the internet: Xod asks Telegram for new messages (long polling), so no port, router or
account change is needed. Only the paired Telegram account is listened to; anyone else gets nothing. The token is in
the Keychain / Credential Manager. Your messages do pass through Telegram's servers (as any Telegram chat does):
the settings say so before you connect.
"""
import asyncio
import json
import logging
import os
import re
import secrets
import time
from typing import Any, Dict, List, Optional

import httpx

from app.paths import DATA_DIR

logger = logging.getLogger("xod.phone")

STATE_PATH = DATA_DIR / "phone.json"
TOKEN_KEY = "telegram_bot_token"
API = os.environ.get("XOD_TELEGRAM_API", "https://api.telegram.org") + "/bot{token}/{method}"   # tests use a stand-in
MAX_TEXT = 3800          # Telegram's limit is 4096 characters a message

_state: Dict[str, Any] = {}
_loop_task: Optional[asyncio.Task] = None
_main_loop: Optional[asyncio.AbstractEventLoop] = None


# ---------------------------------------------------------------- state

def _load() -> Dict[str, Any]:
    global _state
    if not _state:
        try:
            _state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _state = {}
    _state.setdefault("asked", {})        # message id we sent → task id (a reply to it answers that task)
    _state.setdefault("from_phone", [])   # tasks started from the phone
    return _state


def _save() -> None:
    try:
        st = dict(_state)
        st["asked"] = dict(list(st.get("asked", {}).items())[-200:])
        st["from_phone"] = st.get("from_phone", [])[-200:]
        STATE_PATH.write_text(json.dumps(st, indent=1), encoding="utf-8")
    except Exception as e:
        logger.warning(f"phone state: {e}")


def _token() -> str:
    from app.services import secret_store
    return secret_store.get(TOKEN_KEY)


def status() -> Dict[str, Any]:
    st = _load()
    tok = bool(_token())
    return {"connected": tok and bool(st.get("bot")), "bot": st.get("bot"), "paired": bool(st.get("chat_id")),
            "who": st.get("who"), "pair_code": None if st.get("chat_id") else st.get("pair_code"),
            "pair_link": f"https://t.me/{st['bot']}?start={st['pair_code']}" if tok and st.get("bot") and not st.get("chat_id") and st.get("pair_code") else None,
            "notify_all": st.get("notify_all", True), "listening": bool(_loop_task and not _loop_task.done()),
            "last_error": st.get("last_error")}


# ---------------------------------------------------------------- Telegram

async def _api(method: str, token: Optional[str] = None, http_timeout: float = 20, **params: Any) -> Dict[str, Any]:
    token = token or _token()
    if not token:
        raise RuntimeError("No Telegram bot connected.")
    async with httpx.AsyncClient(timeout=http_timeout) as c:
        r = await c.post(API.format(token=token, method=method), json={k: v for k, v in params.items() if v is not None})
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or f"Telegram said no ({r.status_code})")
    return data.get("result")


def _plain(text: str) -> str:
    """Markdown and the page's hidden notes → readable plain text for a chat bubble."""
    text = re.sub(r"<!--.*?-->", "", text or "", flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 40].rsplit("\n", 1)[0] + "\n\n… (the rest is in Xod's Tasks)"


async def send(text: str, buttons: Optional[List[List[Dict[str, str]]]] = None, reply_to: Optional[int] = None) -> Optional[int]:
    """Sends to the paired phone. Returns the message id (None if not paired or it failed)."""
    st = _load()
    if not st.get("chat_id") or not _token():
        return None
    try:
        msg = await _api("sendMessage", chat_id=st["chat_id"], text=_plain(text) or "…", reply_to_message_id=reply_to,
                         reply_markup={"inline_keyboard": buttons} if buttons else None)
        return msg.get("message_id")
    except Exception as e:
        logger.warning(f"phone: couldn't send: {e}")
        return None


# ---------------------------------------------------------------- connecting

async def connect(token: str) -> Dict[str, Any]:
    """Checks the token with Telegram, keeps it in the Keychain, and makes a pairing code."""
    token = (token or "").strip()
    if not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_-]{30,}", token):
        return {"success": False, "error": "That doesn't look like a bot token. It's what BotFather sends, like 123456789:AAH…"}
    try:
        me = await _api("getMe", token=token)
    except Exception as e:
        return {"success": False, "error": f"Telegram didn't accept that token: {e}"}
    from app.services import secret_store
    secret_store.save(TOKEN_KEY, token)
    st = _load()
    st.update(bot=me.get("username"), chat_id=None, who=None, pair_code=f"{secrets.randbelow(10 ** 6):06d}", offset=0, last_error=None)
    _save()
    start()
    return {"success": True, **status()}


def disconnect() -> Dict[str, Any]:
    global _loop_task, _state
    from app.services import secret_store
    secret_store.save(TOKEN_KEY, "")
    if _loop_task and not _loop_task.done():
        _loop_task.cancel()
    _loop_task = None
    _state = {}
    try:
        STATE_PATH.unlink()
    except OSError:
        pass
    return {"success": True, **status()}


def set_notify_all(on: bool) -> Dict[str, Any]:
    _load()["notify_all"] = bool(on)
    _save()
    return status()


def start() -> None:
    """Starts listening (at startup, and after connecting). Safe to call again."""
    global _loop_task, _main_loop
    if not _token():
        return
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _loop_task and not _loop_task.done():
        return
    _loop_task = _main_loop.create_task(_listen())


async def _listen() -> None:
    backoff = 5
    while _token():
        st = _load()
        try:
            updates = await _api("getUpdates", http_timeout=65, timeout=50, offset=st.get("offset") or None,
                                 allowed_updates=["message", "callback_query"])     # waits up to 50 s for a message
            backoff = 5
            if st.get("last_error"):
                st["last_error"] = None
                _save()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            st["last_error"] = str(e)[:200]
            _save()
            if "Unauthorized" in str(e) or "Not Found" in str(e):
                logger.warning("phone: the bot token stopped working; disconnected")
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)
            continue
        for u in updates or []:
            st["offset"] = u["update_id"] + 1
            _save()
            try:
                if "callback_query" in u:
                    await _on_button(u["callback_query"])
                elif "message" in u:
                    await _on_message(u["message"])
            except Exception as e:
                logger.exception(f"phone: handling an update failed: {e}")


# ---------------------------------------------------------------- incoming

HELP = ("Write what you want done and I'll do it on your computer, like in Xod's chat. I'll message you when it's done, "
        "and ask here when I need your OK or an answer (reply to my message to answer).\n\n/tasks: what I'm working on")


async def _on_message(m: Dict[str, Any]) -> None:
    st = _load()
    chat = m.get("chat") or {}
    if chat.get("type") != "private":
        return
    text = (m.get("text") or "").strip()
    who = (m.get("from") or {}).get("first_name") or "you"
    # Not paired yet: only the code (or the /start link carrying it) pairs, and only once.
    if not st.get("chat_id"):
        code = st.get("pair_code") or ""
        if code and (text == code or text == f"/start {code}"):
            st.update(chat_id=chat["id"], who=who, pair_code=None)
            _save()
            await send(f"Linked. Hi {who}! This is your Xod.\n\n{HELP}")
            _event(f"Your phone is linked ({who} on Telegram).")
        else:
            await _api("sendMessage", chat_id=chat["id"], text="To link this bot to your Xod, send it the code shown in Xod (Settings → Your phone).")
        return
    if chat["id"] != st["chat_id"]:
        await _api("sendMessage", chat_id=chat["id"], text="This is someone's private Xod.")
        return
    if not text:
        await send("I can read text messages for now.")
        return
    if text in ("/start", "/help"):
        await send(HELP)
        return
    from app.services.task_queue import task_queue
    if text == "/tasks":
        active = [t for t in task_queue.list() if t["status"] in ("queued", "working", "waiting")][:10]
        await send("Nothing going on." if not active else "\n".join(
            f"• {t['title']}: {'waiting for you' if t['status'] == 'waiting' else t.get('now') or t['status']}" for t in active))
        return
    # A reply to a question about a task answers that task.
    replied = str(((m.get("reply_to_message") or {}).get("message_id")) or "")
    task_id = st["asked"].get(replied)
    if task_id:
        r = task_queue.answer(task_id, text)
        await send("Thanks, carrying on." if r.get("success") else r.get("error", "That task isn't waiting any more."), reply_to=m.get("message_id"))
        return
    t = task_queue.create(text)
    st["from_phone"].append(t["id"])
    _save()
    try:
        await _api("sendChatAction", chat_id=st["chat_id"], action="typing")
    except Exception:
        pass
    await send("On it. I'll message you when it's done.", reply_to=m.get("message_id"))


async def _on_button(q: Dict[str, Any]) -> None:
    st = _load()
    chat_id = ((q.get("message") or {}).get("chat") or {}).get("id")
    if not st.get("chat_id") or chat_id != st["chat_id"]:
        await _api("answerCallbackQuery", callback_query_id=q["id"], text="Not yours.")
        return
    kind, _, ref = str(q.get("data") or "").partition(":")
    from app.services.task_queue import task_queue
    from app.services.mcp_client import mcp_hub
    if kind == "ok":
        r = task_queue.approve(ref)
        note = "Approved: going ahead." if r.get("success") else r.get("error", "")
    elif kind == "no":
        r = await task_queue.cancel(ref)
        note = "Cancelled." if r.get("success") else r.get("error", "")
    elif kind == "mok":
        r = await mcp_hub.approve(ref)
        note = "Done." if r.get("success") else f"That didn't work: {str(r.get('error'))[:200]}"
    elif kind == "mno":
        note = "Declined: nothing was changed." if mcp_hub.decline(ref) else "That request had already expired."
    else:
        note = ""
    await _api("answerCallbackQuery", callback_query_id=q["id"], text=note[:180] or "OK")
    msg = q.get("message") or {}
    if note and msg.get("message_id"):
        try:     # the buttons go, so the same OK can't be pressed twice
            await _api("editMessageText", chat_id=chat_id, message_id=msg["message_id"], text=_plain((msg.get("text") or "") + f"\n\n→ {note}"))
        except Exception:
            pass


# ---------------------------------------------------------------- outgoing (from the task inbox)

def _fire(coro) -> None:
    """Runs a send from sync code (the task queue), on Xod's event loop."""
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        if _main_loop and _main_loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, _main_loop)
        else:
            coro.close()


def on_task(task: Dict[str, Any], event: str) -> None:
    """Called by the task queue: event is "approval", "question", "blocked", "done" or "failed"."""
    st = _load()
    if not st.get("chat_id") or not _token():
        return
    mine = task.get("id") in st.get("from_phone", [])
    if not mine and not st.get("notify_all", True):
        return
    _fire(_tell(task, event, mine))


async def _tell(task: Dict[str, Any], event: str, mine: bool) -> None:
    title = task.get("title") or "A task"
    detail = task.get("waiting_detail") or {}
    if event == "approval":
        label = detail.get("label") or "Submit"
        await send(f"✋ {title}\n\nReady to press \"{label}\" on {detail.get('page') or 'the page'}. "
                   + (f"\n\n{detail['message']}" if detail.get("message") else "") + "\n\nGo ahead?",
                   buttons=[[{"text": "✅ Approve", "callback_data": f"ok:{task['id']}"},
                             {"text": "✖ Cancel", "callback_data": f"no:{task['id']}"}]])
    elif event in ("question", "blocked"):
        mid = await send(f"❓ {title}\n\n{detail.get('message') or 'I need something from you.'}"
                         + ("\n\nReply to this message to answer." if event == "question" else
                            "\n\nIt's stuck at a verification check: finish it on the computer, then reply \"continue\"."))
        if mid:
            st = _load()
            st["asked"][str(mid)] = task["id"]
            _save()
    elif event in ("done", "failed"):
        report = task.get("result") or ("Done." if event == "done" else "It didn't finish.")
        await send(("✓ " if event == "done" else "✗ ") + f"{title}\n\n{report}")
        await _apps_waiting(since=task.get("created_at") or 0)


async def _apps_waiting(since: float) -> None:
    """Changes in connected apps (Reminders, Calendar…) that a task left waiting for an OK: ask here too."""
    from app.services.mcp_client import mcp_hub
    for p in list(mcp_hub.pending.values()):
        if p.get("created_at", 0) >= since - 1 and not p.get("_phoned"):
            p["_phoned"] = True
            args = ", ".join(f"{k}: {v}" for k, v in (p.get("args") or {}).items())[:300]
            await send(f"✋ {p['tool_name'].replace('_', ' ')} in {p['server']}\n{args}\n\nGo ahead?",
                       buttons=[[{"text": "✅ Approve", "callback_data": f"mok:{p['id']}"},
                                 {"text": "✖ Decline", "callback_data": f"mno:{p['id']}"}]])


def _event(text: str) -> None:
    try:
        from app.services import autoconnect
        autoconnect._event("fixed", text)
    except Exception:
        pass
