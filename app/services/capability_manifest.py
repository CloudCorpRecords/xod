"""
What this Xod has, and what it does not have, in one checked list.

services/capabilities.py answers "can the model see / call tools / browse" for the
models panel. This module is the wider inventory the chat and mission sessions
answer from: which providers have a key saved (presence only, never a value),
which search provider actually serves, every registered tool grouped by kind,
the family members and paired phones with their online state, the family
vault state, and the family's shared rules that scope to this Xod
(services/family_rules.py: one rulebook the owner sets once, followed by
both of his agents).

It is built from the live sources, not from a hand-kept list, so a key saved in
Settings or a tool registered in app/agent/tools.py shows up here by itself:

  build_manifest()   the full inventory, for GET /api/capabilities/manifest
  summary_text()     a short plain-language version injected into the system
                     context of chat and mission sessions, with the standing
                     rule: answer questions about your own abilities from this
                     list, never from a guess
  compact_line()     one line of the same facts for the fast-lane router, which
                     answers most short questions and used to invent limits
                     (it once told the owner there was no You.com tool while a
                     verified You.com key sat in this computer's vault)
  keyed_search_tool_names()
                     the API search tools whose provider has a key saved, so
                     session assembly can offer exactly the search tools that
                     can work here

Presence only: a provider entry is a boolean and a field name. No key value,
length or mask ever enters the manifest.
"""
import logging
import time
import urllib.parse
from typing import Any, Dict, List, Optional

logger = logging.getLogger("capability_manifest")

# (id, display name, settings field, what the key is for)
PROVIDERS = [
    ("you", "You.com", "you_api_key", "web search and research"),
    ("tavily", "Tavily", "tavily_api_key", "web search"),
    ("openai", "OpenAI", "openai_api_key", "cloud models"),
    ("fireworks", "Fireworks", "fireworks_api_key", "cloud models"),
    ("anthropic", "Anthropic", "anthropic_api_key", "cloud models"),
    ("groq", "Groq", "groq_api_key", "cloud models"),
    ("gemini", "Google Gemini", "gemini_api_key", "cloud models"),
    ("openrouter", "OpenRouter", "openrouter_api_key", "cloud models"),
    ("trustmrr", "TrustMRR", "trustmrr_api_key", "startup financial data"),
    ("youtube", "YouTube", "youtube_api_key", "video data"),
    ("huggingface", "Hugging Face", "huggingface_token", "model downloads"),
]

SEARCH_TOOLS = ("you_search", "tavily_search", "web_search", "omni_deep_search", "web_scrape")

_ONLINE_WINDOW = 1800        # seconds; the same window the phones roster uses


def provider_states() -> List[Dict[str, Any]]:
    """One entry per provider: is a key saved here? Presence only."""
    from app.config import settings
    return [{"id": pid, "name": name, "field": field, "purpose": purpose,
             "keyed": bool(getattr(settings, field, ""))}
            for pid, name, field, purpose in PROVIDERS]


def keyed_search_tool_names() -> List[str]:
    """The API search tools that can work right now, in preference order."""
    keyed = {p["id"]: p["keyed"] for p in provider_states()}
    out = []
    if keyed.get("you"):
        out.append("you_search")
    if keyed.get("tavily"):
        out.append("tavily_search")
    return out


def _tool_group(name: str) -> str:
    if name in SEARCH_TOOLS:
        return "Web search and reading"
    if name.startswith("computer_"):
        return "Computer"
    if name.startswith("browser_"):
        return "Browser"
    if name.startswith("phone"):
        return "Phone"
    if name.startswith("fs_") or name == "locate_files":
        return "Files and workspace"
    if name.startswith("media_"):
        return "Media"
    if name.startswith("repo_"):
        return "Repositories"
    if name.startswith("agent_mail"):
        return "Agent Mail"
    if name.startswith("xod_"):
        return "Xod itself"
    return "Other tools"


def tool_catalog() -> Dict[str, Any]:
    """Every registered tool, grouped. Read from the live registry, so new
    tools appear here without this file changing."""
    try:
        from app.agent.tools import TOOL_DEFINITIONS
        names = [t["function"]["name"] for t in TOOL_DEFINITIONS]
    except Exception as e:
        logger.debug(f"tool catalog unavailable: {e}")
        return {"total": 0, "groups": {}}
    groups: Dict[str, List[str]] = {}
    for n in names:
        groups.setdefault(_tool_group(n), []).append(n)
    return {"total": len(names), "groups": {k: sorted(v) for k, v in sorted(groups.items())}}


def device_states() -> Dict[str, Any]:
    """Family members (from the signed family registry) and paired phones
    (from the phones roster), each with an online flag where one is known."""
    family: List[Dict[str, Any]] = []
    try:
        from app.services import family_registry
        view = family_registry.load_registry()
        trusted = {d.get("id") for d in view.get("trusted_members") or []}
        mine = family_registry.my_device_id()
        now = time.time()
        for d in view.get("members") or []:
            seen = d.get("last_seen") or 0
            family.append({"name": d.get("name") or d.get("id") or "device",
                           "platform": d.get("platform") or "",
                           "this_device": d.get("id") == mine,
                           "trusted": d.get("id") in trusted,
                           "online": bool(seen and now - seen < _ONLINE_WINDOW) or d.get("id") == mine})
    except Exception as e:
        logger.debug(f"family state unavailable: {e}")
    phones: List[Dict[str, Any]] = []
    try:
        from app.services import phone_mcp
        for ph in phone_mcp.phone_list().get("phones") or []:
            name = ph.get("nickname") or ph.get("model") or ph.get("address") or "phone"
            phones.append({"name": name, "model": ph.get("model") or "",
                           "online": bool(ph.get("online"))})
    except Exception as e:
        logger.debug(f"phone state unavailable: {e}")
    return {"family": family, "phones": phones}


def vault_state() -> Dict[str, Any]:
    """Family vault presence state (services/family_vault.py reports presence
    level state only; there are no key values in it to leak)."""
    try:
        from app.services import family_vault
        st = family_vault.status()
        return {"state": st.get("state"), "version": st.get("version"),
                "applied_version": st.get("applied_version"),
                "recipients": len(st.get("recipients") or []),
                "local_only": bool(st.get("local_only"))}
    except Exception as e:
        logger.debug(f"vault state unavailable: {e}")
        return {"state": "unknown", "version": None, "applied_version": None,
                "recipients": 0, "local_only": False}


def search_state(providers: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    providers = providers if providers is not None else provider_states()
    keyed = {p["id"]: p["keyed"] for p in providers}
    try:
        from app.services import search_providers
        preferred = search_providers.preferred_provider()
    except Exception:
        preferred = "you.com" if keyed.get("you") else "tavily" if keyed.get("tavily") else "web_fallback"
    label = {"you.com": "You.com API", "tavily": "Tavily API"}.get(
        preferred, "the browser search (no search key saved)")
    return {"preferred": preferred, "preferred_label": label,
            "you_keyed": bool(keyed.get("you")), "tavily_keyed": bool(keyed.get("tavily")),
            "fallback": "the other keyed provider, then the browser search, then keyless web search"}


def messaging_state() -> Dict[str, Any]:
    """WhatsApp per surface (services/whatsapp.py surface_state): which
    surfaces exist, whether each can read and can send, and which route a
    send would take. installed and signed_in are observed facts or null;
    the desktop WhatsApp Web route reports built False until it exists."""
    try:
        from app.services import whatsapp
        return whatsapp.surface_state()
    except Exception as e:
        logger.debug(f"messaging state unavailable: {e}")
        return {"whatsapp": {"route": "phone", "send_route": None, "can_read": False,
                             "can_send": False, "phones": []},
                "desktop": {"route": "whatsapp_web", "built": False,
                            "can_read": False, "can_send": False}}


def _messaging_line(messaging: Dict[str, Any]) -> str:
    wa = (messaging or {}).get("whatsapp") or {}
    phones = wa.get("phones") or []
    if not phones:
        return ("WhatsApp: no phone is paired, so Xod cannot read or send WhatsApp messages. "
                "The desktop WhatsApp Web route is not built.")
    parts = []
    for p in phones:
        if p.get("can_send"):
            state = "can read and send"
        elif p.get("can_read"):
            state = "can read, cannot send yet (the Tap switch is off on the phone)"
        elif p.get("installed") is False:
            state = "WhatsApp is not installed"
        elif p.get("signed_in") is False:
            state = "WhatsApp is signed out"
        elif not p.get("online"):
            state = "offline"
        else:
            state = "cannot read or send yet (phone switches are off)"
        parts.append(f"{p.get('name')} ({state})")
    line = "WhatsApp: " + "; ".join(parts) + "."
    if wa.get("can_send"):
        line += (" Sends go through the phone's WhatsApp app, one message at a time, and every send "
                 "waits for the owner's approval first.")
    line += " The desktop WhatsApp Web route is not built."
    return line


def build_manifest() -> Dict[str, Any]:
    from app.services import family_rules
    providers = provider_states()
    return {"providers": providers,
            "search": search_state(providers),
            "tools": tool_catalog(),
            "devices": device_states(),
            "vault": vault_state(),
            "family_rules": family_rules.manifest_section(),
            "messaging": messaging_state(),
            "generated_at": time.time()}


_cache: Dict[str, Any] = {"at": 0.0, "manifest": None}


def cached_manifest(max_age: float = 60) -> Dict[str, Any]:
    if not _cache["manifest"] or time.time() - _cache["at"] > max_age:
        _cache.update(manifest=build_manifest(), at=time.time())
    return _cache["manifest"]


def _device_line(devices: Dict[str, Any]) -> str:
    fam = devices.get("family") or []
    if fam:
        parts = []
        for d in fam:
            tag = "this computer" if d["this_device"] else ("online" if d["online"] else "offline")
            parts.append(f"{d['name']} ({tag})")
        fam_line = "Family computers: " + ", ".join(parts) + "."
    else:
        fam_line = "Family computers: only this computer is in the family registry."
    phones = devices.get("phones") or []
    if phones:
        ph = ", ".join(f"{p['name']} ({'online' if p['online'] else 'offline'})" for p in phones)
        fam_line += f" Paired phones: {ph}."
    else:
        fam_line += " No phones are paired."
    return fam_line


def summary_text(manifest: Optional[Dict[str, Any]] = None) -> str:
    """The manifest in plain words, for a session's system context. It ends
    with the standing rule, because the failure this fixes was a session
    answering a capability question from imagination."""
    m = manifest or cached_manifest()
    keyed = [p["name"] for p in m["providers"] if p["keyed"]]
    unkeyed = [p["name"] for p in m["providers"] if not p["keyed"]]
    s = m["search"]
    search_tools = [n for n in SEARCH_TOOLS if n in (m["tools"]["groups"].get("Web search and reading") or [])]
    groups = ", ".join(f"{k} {len(v)}" for k, v in m["tools"]["groups"].items())
    v = m["vault"]
    vault_line = ("Family vault: no bundle here yet." if v.get("state") in ("missing", "unknown")
                  else f"Family vault: bundle {v.get('state')}, version {v.get('version')}, "
                       f"applied version {v.get('applied_version')} on this computer.")
    lines = [
        "WHAT THIS XOD HAS (checked on this computer just now):",
        f"- Web search is served by {s['preferred_label']} first; fallback order: {s['fallback']}. "
        f"Search tools you can call: {', '.join(search_tools) if search_tools else 'none registered'}.",
        f"- Keys saved on this computer: {', '.join(keyed) if keyed else 'none'}. "
        f"No key saved: {', '.join(unkeyed) if unkeyed else 'none'}. A provider with no key saved cannot be called until the owner adds one in Settings.",
        f"- Tools: {m['tools']['total']} registered ({groups}). The full list is at GET /api/capabilities/manifest.",
        "- " + _device_line(m["devices"]),
        "- " + _messaging_line(m.get("messaging") or messaging_state()),
        "- " + vault_line,
        "RULE: answer any question about what you can or cannot do, which tools, keys, providers or devices "
        "you have, from this list only, never from a guess or from general knowledge about AI assistants. "
        "If something is not in this list, say it is not set up on this computer instead of claiming or denying it. "
        "The same rule covers what you report after doing work: never write that you could not run commands, "
        "use the browser, or reach some other capability this list shows as registered. If an attempt failed, "
        "name the attempt and what it said; a failed call is not a missing capability.",
    ]
    from app.services import family_rules
    section = m.get("family_rules")
    if section is None:
        section = family_rules.manifest_section()
    block = family_rules.summary_block(section)
    if block:
        lines.insert(len(lines) - 1, block)
    return "\n".join(lines)


def compact_line(manifest: Optional[Dict[str, Any]] = None) -> str:
    """One short line of the same facts for the fast-lane router prompt."""
    m = manifest or cached_manifest()
    keyed = [p["name"] for p in m["providers"] if p["keyed"]]
    unkeyed = [p["name"] for p in m["providers"] if not p["keyed"]]
    return (f"Your own setup, checked just now: web search is served by {m['search']['preferred_label']}; "
            f"keys saved here: {', '.join(keyed) if keyed else 'none'}; "
            f"no key saved: {', '.join(unkeyed) if unkeyed else 'none'}. "
            "If the person asks what you can do, or whether you have a tool, a key, a provider or a device, "
            "answer from these facts, never from a guess.")


def grounding_rules(manifest: Optional[Dict[str, Any]] = None) -> str:
    """Standing orders for a session that is about to DO work (a queued task or
    a worker job), built from the same live manifest. The failure this covers:
    a task fetched a service's setup instructions, then reported "I couldn't
    run commands... I'll need access to an environment with browser and shell
    execution enabled" on a backend whose registry has computer_run_command.
    The manifest summary alone did not stop it: its tool line is group counts,
    and its rule spoke to answering questions, not to claims made in a report.
    This block names the execution capabilities outright and forbids declaring
    one missing without checking here first."""
    m = manifest or cached_manifest()
    names = {n for group in (m["tools"]["groups"] or {}).values() for n in group}
    lines = ["GROUNDING RULES (checked on this computer just now):"]
    if "computer_run_command" in names:
        lines.append("- This Xod runs shell commands on this computer (computer_run_command) and reads "
                     "and writes files here. Saying you have no shell or command access, or that the "
                     "person must provide an environment with execution enabled, is false on this backend.")
    if "browser_navigate" in names:
        lines.append("- This Xod drives a real browser (browser_navigate and the other browser_ tools).")
    lines.append("- Before writing that a capability, tool or environment is unavailable or has to be "
                 "provided, check WHAT THIS XOD HAS. If it is registered, use it. If a call fails, report "
                 "that call and its error; never promote a failed call into a missing environment, and "
                 "never ask the person for access this backend already has.")
    lines.append("- Setup work (install, configure, 'set me up'): probe the current state first - what "
                 "is installed, whether you are already signed in or configured, which version. When a "
                 "probe shows a step is already done, report that with the probe's output and skip the "
                 "step instead of running a fresh setup or guessing the state.")
    return "\n".join(lines)
