"""
Add-on packs: optional groups of tools and screens, so Xod's core stays simple.

Core (always on): research and web search, the browser, files and the computer, memory, tasks,
routines, reports, connected apps (MCP), voice. Packs add specialist abilities. A pack that's
off has its tools hidden from the model (and refused if called) and its menu items hidden.

New installs start with the general packs on and the specialist ones off; an install that
already has chats keeps everything on (that's how it was used before packs existed).
"""
import json
from typing import Any, Dict, List, Optional, Set

PACKS: List[Dict[str, Any]] = [
    {"id": "media-studio", "name": "Media Studio", "icon": "film", "default": True,
     "description": "Make and edit images, GIFs and short videos on this Mac.",
     "tools": ["media_generate_image", "media_generate_video", "media_image_create", "media_image_edit", "media_list_models",
               "media_video_extract_frame", "media_video_from_frame", "media_video_gif", "media_video_overlay",
               "media_video_splice", "media_video_trim", "media_download_youtube"]},
    {"id": "model-lab", "name": "Model Lab", "icon": "cpu", "default": True,
     "description": "Train small, fast models on Xod's own work so routine steps get quicker.",
     "tools": []},
    {"id": "startup-intel", "name": "Startup & market intel", "icon": "trending-up", "default": False,
     "description": "Revenue and deals (TrustMRR), funding and SEC filings, traffic, hiring, and a library of companies you've researched.",
     "tools": ["trustmrr_compare_startups", "trustmrr_get_startup", "trustmrr_list_startups", "trustmrr_screen_deals",
               "calculate_saas_metrics", "audit_funding_and_org", "audit_sec_filings", "audit_traffic_and_popularity",
               "compare_traffic_head_to_head", "audit_talent_and_hiring", "scan_live_job_firehose", "scan_batch_job_firehose",
               "scan_trigger_events", "query_knowledge_graph", "update_knowledge_graph_entity", "mine_community_sentiment"]},
    {"id": "people-finder", "name": "People & emails", "icon": "at-sign", "default": False,
     "description": "Find and check work email addresses for outreach.",
     "tools": ["find_work_email", "verify_email_bounce_risk"]},
    {"id": "web-recon", "name": "Website forensics", "icon": "scan-search", "default": False,
     "description": "What a site is built with, its DNS, subdomains, code repos, IP ranges and packages.",
     "tools": ["audit_tech_stack", "audit_github_repo", "audit_ip_landscape", "audit_package_ecosystem", "audit_subdomains_activity",
               "recon_shadow_subdomains", "explore_site_surface", "inspect_domain_dns_health", "inspect_url_path_activity"]},
    {"id": "agent-mail", "name": "Agent Mail (preview)", "icon": "send", "default": True, "preview": True,
     "description": "Give this Xod an address and trade end-to-end encrypted messages and tasks with other AI agents. Needs a relay (post office).",
     "tools": ["agent_mail_send", "agent_mail_inbox"]},
    {"id": "creator-trends", "name": "Creators & trends", "icon": "flame", "default": False,
     "description": "Spot rising creators and what people are talking about across social sites and YouTube.",
     "tools": ["creator_discover_candidates", "creator_predict_attention", "scan_live_social_trends", "search_topic_buzz",
               "youtube_inspect_channel"]},
]
_BY_ID = {p["id"]: p for p in PACKS}
TOOL_PACK = {t: p["id"] for p in PACKS for t in p["tools"]}


def _stored() -> Optional[Dict[str, bool]]:
    from app.services.storage import get_setting
    raw = get_setting("packs", "")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def enabled() -> Set[str]:
    stored = _stored()
    if stored is None:
        # Decided once, then saved: installs from before add-ons existed keep everything on; new
        # installs (which only have the sample "Getting started" chat) start with the defaults.
        try:
            from app.services.storage import list_sessions, set_setting
            existing = len(list_sessions()) > 1
            set_setting("packs", json.dumps({p["id"]: bool((existing and not p.get("preview")) or p["default"]) for p in PACKS}))
        except Exception:
            existing = False
        return {p["id"] for p in PACKS if (existing and not p.get("preview")) or p["default"]}
    return {p["id"] for p in PACKS if stored.get(p["id"], p["default"])}


def set_enabled(pack_id: str, on: bool) -> Dict[str, Any]:
    from app.services.storage import set_setting
    if pack_id not in _BY_ID:
        return {"success": False, "error": "Unknown add-on."}
    current = {p["id"]: (p["id"] in enabled()) for p in PACKS}
    current[pack_id] = bool(on)
    set_setting("packs", json.dumps(current))
    return {"success": True, "enabled": sorted(enabled())}


def tool_allowed(name: str) -> bool:
    pack = TOOL_PACK.get(name)
    return pack is None or pack in enabled()


def status() -> List[Dict[str, Any]]:
    on = enabled()
    return [{"id": p["id"], "name": p["name"], "icon": p["icon"], "description": p["description"],
             "tools": len(p["tools"]), "enabled": p["id"] in on} for p in PACKS]
