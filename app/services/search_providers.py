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
