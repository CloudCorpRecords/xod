"""
Which API search provider answers when Xod just needs "search the web".

One place decides, so every surface agrees: a saved You.com key wins (You.com is
Xod's preferred search & research provider), then Tavily, then the keyless
cascade the clients already fall back to (DuckDuckGo via services/web_search.py).

The explicit tools keep their own routing — the you_search tool still goes
straight to You.com and the tavily_search tool straight to Tavily. This helper
serves the places with no provider opinion of their own: the web_search tool's
API fallback and OmniSearch's web vector. Each client already cascades to the
other provider and then to DuckDuckGo on failure, so calling the preferred one
preserves every existing fallback; this only fixes who goes first.
"""
import logging
from typing import Any, Dict

logger = logging.getLogger("search_providers")


def preferred_provider() -> str:
    """The provider preferred_search tries first: "you.com", "tavily", or "web_fallback"."""
    from app.services.you_search import you_client
    from app.services.tavily_search import tavily_client
    if you_client.is_configured():
        return "you.com"
    if tavily_client.is_configured():
        return "tavily"
    return "web_fallback"


async def preferred_search(query: str, count: int = 5) -> Dict[str, Any]:
    """Searches with the preferred API provider (see module docstring)."""
    from app.services.you_search import you_client
    from app.services.tavily_search import tavily_client
    if you_client.is_configured():
        return await you_client.search(query=query, count=count)
    if tavily_client.is_configured():
        return await tavily_client.search(query=query, count=count)
    # Neither key saved: the unconfigured client still runs the keyless cascade.
    return await you_client.search(query=query, count=count)


async def search_for_chat(query: str, count: int = 8):
    """The chat fast lane's search: the keyed API provider when one is saved,
    in the same shape services/web_reader.search returns ({results, provider}),
    or None when no search key is saved or the API came back empty, so the
    caller falls back to the browser search.

    Chat used to go straight to the browser search and only touch the API
    tools when the browser found nothing, which silently bypassed a saved
    You.com or Tavily key. The result's "provider" names who actually served
    it (the clients cascade and report the real provider), so answers and
    traces can say so."""
    import urllib.parse
    if preferred_provider() == "web_fallback":
        return None
    try:
        res = await preferred_search(query, count=count)
    except Exception as e:
        logger.info(f"chat search via API failed, browser fallback next: {e}")
        return None
    hits = []
    for h in (res or {}).get("results") or []:
        if not isinstance(h, dict):
            continue
        url = str(h.get("url") or "")
        if not url.startswith("http"):
            continue
        hits.append({"title": str(h.get("title") or "")[:200], "url": url,
                     "snippet": str(h.get("snippet") or h.get("content") or "")[:600],
                     "host": urllib.parse.urlparse(url).hostname or ""})
    if not hits:
        return None
    return {"results": hits, "provider": res.get("provider") or preferred_provider()}
