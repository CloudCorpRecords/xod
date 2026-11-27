"""Unified Control Center snapshot for Xod Desktop.

Aggregates AI Brain, MCP & App Connections (including Smart MCP Routing telemetry),
Mac Computer Control permissions, and Saved Browser / Live Chrome sessions into a
single fast payload with actionable one-click fixes.
"""
import subprocess
import sys
from typing import Any, Dict, List


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def open_mac_permission_pane(pane: str) -> Dict[str, Any]:
    """Open macOS System Settings directly to Screen Recording or Accessibility."""
    if sys.platform != "darwin":
        return {"success": False, "error": "macOS permission panes are only available on Mac."}
    urls = {
        "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        "automation": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
        "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    }
    target = urls.get((pane or "").strip().lower())
    if not target:
        return {"success": False, "error": f"Unknown permission pane: {pane}"}
    try:
        subprocess.Popen(["open", target])
        return {"success": True, "opened": pane}
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}


def snapshot() -> Dict[str, Any]:
    from app.services.storage import get_setting
    from app.services import desktop, credential_vault, agora_voice, secret_store
    from app.services.mcp_client import mcp_hub
    from app import config as cfg

    # 1. AI Brain & Keychain summary
    key_names: List[str] = []
    for k in ("openai", "anthropic", "fireworks", "gemini", "groq", "openrouter", "tavily", "you", "huggingface", "trustmrr"):
        if _safe(lambda k=k: bool(secret_store.get_secret(k)), False):
            key_names.append(k)
    agora_configured = _safe(agora_voice.is_configured, False)
    agora_convoai = bool(_safe(lambda: secret_store.get_secret("agora_customer_id"), "") and
                         _safe(lambda: secret_store.get_secret("agora_customer_secret"), ""))
    from app.services import usage_ledger, sentinel, harness_turbo
    usage_today = _safe(lambda: usage_ledger.summary("today").get("totals") or {}, {}) or {}
    active_provider = getattr(cfg, "LLM_PROVIDER", "") or get_setting("llm_provider", "")
    brain = {
        "provider": active_provider,
        "model": getattr(cfg, "LLM_MODEL", "") or get_setting("llm_model", ""),
        "keychain_keys": key_names,
        "agora_configured": agora_configured,
        "agora_convoai_ready": agora_convoai,
        "cache_hit_rate": usage_today.get("cache_hit_rate", 0.0),
        "cached_tokens": usage_today.get("cached_tokens", 0),
        "prompt_tokens": usage_today.get("prompt_tokens", 0),
        "permissionless_mode": _safe(sentinel.permissionless_mode, False),
        "local_turbo_active": harness_turbo.is_local_provider(active_provider),
        "local_mcp_tool_cap": harness_turbo.LOCAL_MCP_TOOL_CAP,
    }

    # 2. MCP servers + Smart Tool Routing
    servers_raw = _safe(mcp_hub.status, []) or []
    servers = []
    needs_attention = []
    total_tools = 0
    connected_count = 0
    for s in servers_raw:
        tcount = len(s.get("tools") or [])
        total_tools += tcount
        if s.get("connected"):
            connected_count += 1
        item = {
            "name": s.get("name") or "",
            "connected": bool(s.get("connected")),
            "enabled": bool(s.get("enabled", True)),
            "trust": s.get("trust") or "ask",
            "tool_count": tcount,
            "needs_sign_in": bool(s.get("needs_sign_in")),
            "error": s.get("error") or "",
            "connector": bool(s.get("connector")),
        }
        servers.append(item)
        if item["enabled"] and (item["needs_sign_in"] or not item["connected"]):
            needs_attention.append(item)

    smart_routing = get_setting("smart_mcp_routing", "1") != "0"
    mcp_block = {
        "total_servers": len(servers),
        "connected_count": connected_count,
        "total_tools": total_tools,
        "smart_routing": smart_routing,
        "last_route": getattr(mcp_hub, "last_route", None) or {},
        "needs_attention": needs_attention,
        "servers": servers,
    }

    # 3. Mac Computer Control permissions
    perms = _safe(desktop.permissions, {}) or {}
    screen_ok = bool(perms.get("screen"))
    ax_ok = bool(perms.get("accessibility"))
    mic_ok = bool(perms.get("microphone", True))
    supported = bool(perms.get("supported", sys.platform == "darwin"))
    missing_perms: List[str] = []
    if supported:
        if not screen_ok:
            missing_perms.append("Screen Recording")
        if not ax_ok:
            missing_perms.append("Accessibility")
    mac_control = {
        "supported": supported,
        "ready": bool(screen_ok and ax_ok) if supported else False,
        "screen": screen_ok,
        "accessibility": ax_ok,
        "microphone": mic_ok,
        "app_path": perms.get("app_path") or "",
        "missing": missing_perms,
    }

    # 4. Saved Browser Logins & Live Signed-in Chrome
    vault_idx = _safe(credential_vault._read_index, {}) or {}
    saved_logins = [
        {"site": site, "username": (meta.get("username") or "")[:64]}
        for site, meta in sorted(vault_idx.items())
        if isinstance(meta, dict)
    ][:30]
    chrome_res = _safe(desktop.chrome_tabs, {"success": False, "tabs": []}) or {}
    chrome_tabs = (chrome_res.get("tabs") or [])[:15]
    prefer_live_chrome = get_setting("prefer_live_chrome", "1") != "0"
    browser_sessions = {
        "saved_logins": saved_logins,
        "chrome_running": bool(chrome_res.get("success") and len(chrome_tabs) > 0),
        "chrome_tabs_count": len(chrome_res.get("tabs") or []),
        "chrome_tabs": chrome_tabs,
        "prefer_live_chrome": prefer_live_chrome,
    }

    # 5. Unified actionable issues list (for top-bar badge + Control Center banners)
    issues: List[Dict[str, Any]] = []
    if supported and not screen_ok:
        issues.append({
            "id": "mac_screen",
            "severity": "warn",
            "title": "Screen Recording is off for Xod",
            "detail": "Enable Screen Recording in macOS System Settings so Xod can see your screen during computer tasks.",
            "action_type": "mac_permission",
            "action_target": "screen",
            "action_label": "Open Screen Recording",
        })
    if supported and not ax_ok:
        issues.append({
            "id": "mac_accessibility",
            "severity": "warn",
            "title": "Accessibility is off for Xod",
            "detail": "Enable Accessibility in macOS System Settings so Xod can click and type in Mac apps.",
            "action_type": "mac_permission",
            "action_target": "accessibility",
            "action_label": "Open Accessibility",
        })
    for bad in needs_attention:
        issues.append({
            "id": f"mcp_{bad['name']}",
            "severity": "warn",
            "title": f"{bad['name']} connection needs sign-in" if bad["needs_sign_in"] else f"{bad['name']} MCP is disconnected",
            "detail": bad["error"] or "Reconnect to restore its tools.",
            "action_type": "mcp_reconnect",
            "action_target": bad["name"],
            "action_label": "Reconnect",
        })

    from app.services import fleet_control, autonomy_modes, training_vault, checkpoints, terminal_pro, superpowers, remote_devices
    fleet_block = _safe(fleet_control.status_snapshot, {}) or {}
    autonomy_block = _safe(autonomy_modes.status, {}) or {}
    vault_block = _safe(training_vault.status, {}) or {}
    checkpoints_list = _safe(lambda: checkpoints.list_checkpoints(limit=12), []) or []
    toolchain_block = _safe(terminal_pro.toolchain_status, {}) or {}
    superpowers_block = _safe(superpowers.status, {}) or {}
    remote_block = _safe(remote_devices.list_devices, {}) or {}

    return {
        "ok": len(issues) == 0,
        "issue_count": len(issues),
        "issues": issues,
        "brain": brain,
        "mcp": mcp_block,
        "mac_control": mac_control,
        "browser_sessions": browser_sessions,
        "fleet": fleet_block,
        "autonomy": autonomy_block,
        "training_vault": vault_block,
        "checkpoints": checkpoints_list,
        "terminal_toolchain": toolchain_block,
        "superpowers": superpowers_block,
        "remote_devices": remote_block,
    }
