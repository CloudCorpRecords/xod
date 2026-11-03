"""
Family Link: inference borrowing — a keyless family member routes a
model call to a member that has the key (V3).

Design: FAMILY-KEYS-AND-INFERENCE-SHARING-DESIGN-2026-11-01.md
Mechanism 2, with Tree's reconciliation (MEMORY, 2026-11-01): the
call bills the SERVING member's ledger, and borrowing is an explicit
state, never a silent fallback.

Wire: POST /v1/family/inference on the serving device's beacon
(services/xod_network.py, tailnet-only listener, port 8791 — the same
listener as /v1/tasks and /v1/memory/*). Two locks, like the harness:
the caller must be on the same Tailscale account (checked by the
beacon route via tailscale whois), AND the request body is signed
with the calling device's registry key:
  {from, to, provider, model, messages, task, ts, nonce, signature}
where signature covers the canonical form of every other field. The
server accepts only a caller whose record is in the registry's
trusted_members (active, signature-verified, approval-covered, not
revoked). Forged, unsigned, stale (>10 min), or revoked callers are
refused with a plain reason.

Serving: the serving device calls the provider with ITS OWN key —
keys never move in this mode. The completion returns to the caller,
and the call is written to the SERVING device's usage ledger
(services/usage_ledger.py) with context
"family-borrow:<requester>:<task>", so the serving member's ledger
carries the cost with the requesting device and task named.

Failure is explicit, never a silent wait: an unreachable or sleeping
server raises BorrowBlocked on the caller (short timeout, no retry
loop); a device pinned local-only (setting
family_inference_local_only="on") refuses to borrow at all; a server
with family_inference_serve="off" refuses to serve. The caller then
falls back to its local model or reports blocked — in the open,
never by silently waiting or silently substituting.
"""
import os
import time

from app.services import family_registry, mail_crypto as mc

BEACON_PORT = 8791
CLOCK_SKEW = 600
SERVE_SETTING = "family_inference_serve"
LOCAL_ONLY_SETTING = "family_inference_local_only"
MAX_MESSAGES = 64
MAX_MESSAGE_CHARS = 32000


class BorrowBlocked(Exception):
    """Borrowing could not happen, stated plainly. Explicit by design."""


class BorrowRefused(PermissionError):
    """The server refused this caller. Maps to HTTP 403 at the beacon."""


def _setting(name, default=""):
    try:
        from app.services.storage import get_setting
        return get_setting(name, default) or default
    except Exception:
        return default


def local_only() -> bool:
    return _setting(LOCAL_ONLY_SETTING, "off") == "on"


def serve_enabled() -> bool:
    return _setting(SERVE_SETTING, "on") != "off"


# ---------------------------------------------------------------- signing

def signed_request(identity, my_id: str, server_id: str, provider: str,
                   model: str, messages, task: str = "") -> dict:
    body = {"from": my_id, "to": server_id, "provider": provider, "model": model,
            "messages": messages, "task": task or "",
            "ts": int(time.time()), "nonce": mc.b64e(os.urandom(12))}
    body["signature"] = identity.sign(mc.canonical(body))
    return body


def _verify_request(body: dict, view: dict) -> dict:
    if not isinstance(body, dict):
        raise BorrowRefused("request is not an object")
    caller_id = body.get("from")
    record = family_registry.trusted_member(view, caller_id or "")
    if record is None:
        raise BorrowRefused(
            "the calling device is not a trusted family member "
            "(not active, not signature-verified, or revoked)")
    sig = body.get("signature")
    if not sig:
        raise BorrowRefused("the request is unsigned")
    core = {k: v for k, v in body.items() if k != "signature"}
    if not mc.verify(record["public_key"], mc.canonical(core), sig):
        raise BorrowRefused("the request signature does not verify")
    ts = body.get("ts") or 0
    if abs(time.time() - float(ts)) > CLOCK_SKEW:
        raise BorrowRefused("the request is stale")
    messages = body.get("messages")
    if (not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES
            or any(not isinstance(m, dict) or not isinstance(m.get("content"), str)
                   or len(m["content"]) > MAX_MESSAGE_CHARS for m in messages)):
        raise BorrowRefused("messages are missing or over the size limit")
    return record


# ---------------------------------------------------------------- serving

def _serve_local(provider: str, model: str, messages) -> dict:
    """One chat completion with THIS device's own provider key (or its
    local engine). OpenAI-shaped providers only; the provider key
    comes from this device's keystore and never leaves it."""
    import httpx
    from app.config import settings
    from app.services import secret_store
    provider = (provider or settings.llm_provider or "").lower()
    model = model or settings.llm_model
    base = (settings.llm_base_url or "").rstrip("/")
    key = ""
    if provider == "openai":
        base = base or "https://api.openai.com/v1"
        key = secret_store.get("openai_api_key")
        if not key:
            raise BorrowRefused("this device holds no OpenAI key to serve with")
    elif provider in ("lm_studio", "ollama", "local"):
        base = base or "http://localhost:1234/v1"
    else:
        # Other cloud providers ride their OpenAI-compatible endpoints
        # only when this device already points at one.
        field = {"fireworks": "fireworks_api_key", "groq": "groq_api_key",
                 "openrouter": "openrouter_api_key"}.get(provider)
        if not field or not base:
            raise BorrowRefused(f"this device cannot serve provider {provider!r}")
        key = secret_store.get(field)
        if not key:
            raise BorrowRefused(f"this device holds no {provider} key to serve with")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    payload = {"model": model, "messages": messages}
    with httpx.Client(timeout=120) as client:
        r = client.post(f"{base}/chat/completions", json=payload, headers=headers)
    if r.status_code != 200:
        raise BorrowRefused(f"the provider answered {r.status_code} on the serving device")
    data = r.json()
    text = ""
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        pass
    return {"text": text, "usage": data.get("usage") or {},
            "provider": provider, "model": data.get("model") or model}


def handle_request(body: dict, view=None, serve_fn=None, my_id=None) -> dict:
    """The serving half, transport-free so it is fully testable. The
    beacon route wraps this; ledger attribution happens here, on the
    serving device, before the answer returns."""
    if not serve_enabled():
        raise BorrowRefused("this device does not serve borrowed inference")
    view = view or family_registry.load_registry()
    record = _verify_request(body, view)
    if my_id and body.get("to") not in (None, "", my_id):
        raise BorrowRefused("the request is addressed to a different device")
    started = time.time()
    out = (serve_fn or _serve_local)(body.get("provider") or "", body.get("model") or "",
                                     body["messages"])
    try:
        from app.services import usage_ledger
        usage_ledger.append_record(
            out.get("provider") or body.get("provider") or "",
            out.get("model") or body.get("model") or "",
            usage=out.get("usage") or None,
            prompt_chars=sum(len(m["content"]) for m in body["messages"]),
            completion_chars=len(out.get("text") or ""),
            context=f"family-borrow:{record['id']}:{body.get('task') or 'inference'}")
    except Exception:
        pass  # the ledger never blocks a call (usage_ledger's own rule)
    return {"text": out.get("text") or "", "usage": out.get("usage") or {},
            "provider": out.get("provider") or body.get("provider") or "",
            "model": out.get("model") or body.get("model") or "",
            "served_by": my_id or "", "borrowed": True,
            "duration_ms": round((time.time() - started) * 1000)}


# ---------------------------------------------------------------- borrowing

def borrow(server_ip: str, server_id: str, provider: str, model: str, messages,
           task: str = "", identity=None, my_id=None, port: int = BEACON_PORT,
           timeout: float = 25.0) -> dict:
    """The borrowing half. Raises BorrowBlocked — explicitly, with the
    reason — whenever the call cannot be borrowed: pinned local-only,
    server asleep/offline/unreachable, or server refusal. Never waits
    silently and never falls back silently."""
    if local_only():
        raise BorrowBlocked("this device is pinned to local-only inference; borrowing is off")
    if not my_id:
        raise BorrowBlocked("this device has no family id to borrow under")
    identity = identity or family_registry.device_identity()
    body = signed_request(identity, my_id, server_id, provider, model, messages, task)
    import httpx
    url = f"http://{server_ip}:{port}/v1/family/inference"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=body)
    except httpx.HTTPError as e:
        raise BorrowBlocked(
            f"the serving device at {server_ip}:{port} is unreachable "
            f"({type(e).__name__}); it may be asleep or offline. "
            "Borrowing is blocked, not queued.") from e
    if r.status_code != 200:
        detail = ""
        try:
            detail = str(r.json().get("detail") or "")
        except ValueError:
            pass
        raise BorrowBlocked(f"the serving device refused the borrowed call "
                            f"({r.status_code}){': ' + detail if detail else ''}")
    out = r.json()
    out["borrowed"] = True
    return out
