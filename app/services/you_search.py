import os
import httpx
import logging
from typing import Optional, Dict, Any, List
from app.config import settings
from app.services.web_search import search_web

logger = logging.getLogger("you_search")

class YouSearchClient:
    """
    You.com Search & Web Intelligence client.
    Uses official developer endpoints at https://ydc-index.io/v1/search.
    Authenticates via the X-API-Key header.
    Falls back gracefully to DuckDuckGo search if unconfigured or unreachable.
    """
    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://ydc-index.io/v1"):
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def api_key(self) -> str:
        return (
            self._api_key
            or settings.you_api_key
            or os.getenv("YOU_API_KEY", "")
            or os.getenv("YDC_API_KEY", "")
        )

    def set_api_key(self, key: str):
        self._api_key = key

    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key.strip()) > 5)

    async def verify_key(self) -> Dict[str, Any]:
        """Tests whether the configured You.com API key is active and authorized."""
        key = self.api_key
        if not key:
            return {"valid": False, "message": "No You.com API key configured."}

        headers = {
            "X-API-Key": key.strip(),
            "Accept": "application/json"
        }
        params = {"query": "SaaS industry trends", "count": 1}

        # Candidate endpoints to verify against
        endpoints = [
            f"{self.base_url}/search",
            "https://ydc-index.io/v1/search",
            "https://api.you.com/v1/answer"
        ]

        async with httpx.AsyncClient(timeout=10.0) as client:
            for url in endpoints:
                try:
                    if "answer" in url:
                        res = await client.post(url, json={"query": "test"}, headers=headers)
                    else:
                        res = await client.get(url, params=params, headers=headers)

                    if res.status_code == 200:
                        data = res.json()
                        web_items = data.get("results", {}).get("web", []) or data.get("hits", [])
                        count = len(web_items) or 1
                        return {
                            "valid": True,
                            "message": f"You.com API Key verified! Search index active ({count} test result received).",
                            "hits_count": count
                        }
                    elif res.status_code in (401, 403):
                        continue  # Try next candidate endpoint before declaring 403
                    else:
                        logger.warning(f"You.com test on {url} returned HTTP {res.status_code}")
                except Exception as e:
                    logger.warning(f"Error testing candidate endpoint {url}: {e}")

        return {
            "valid": False,
            "message": "Invalid or unauthorized You.com API key (HTTP 403). Please verify your key on you.com/platform."
        }

    async def search(
        self,
        query: str,
        count: int = 5,
        freshness: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes a real-time web search via You.com Web Search API.
        Falls back seamlessly to DuckDuckGo search if no key is configured.
        """
        if not self.is_configured():
            logger.info("You.com key not configured. Evaluating secondary web search engines.")
            return await self._fallback_to_tavily_or_ddg(query, count, "You.com key not configured")

        headers = {
            "X-API-Key": self.api_key.strip(),
            "Accept": "application/json"
        }
        params: Dict[str, Any] = {
            "query": query,
            "count": min(10, max(1, count))
        }
        if freshness:
            params["freshness"] = freshness

        url = f"{self.base_url}/search"

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                res = await client.get(url, params=params, headers=headers)

                # Fallback to secondary URL if needed
                if res.status_code != 200:
                    res = await client.get("https://ydc-index.io/v1/search", params=params, headers=headers)

                if res.status_code == 200:
                    data = res.json()
                    # Parse web results from ydc-index format or hits format
                    raw_items = data.get("results", {}).get("web", []) or data.get("hits", [])
                    formatted_results = []

                    for hit in raw_items[:count]:
                        snippets = hit.get("snippets", [])
                        if isinstance(snippets, list) and snippets:
                            snippet_text = "\n".join(snippets)
                        else:
                            snippet_text = hit.get("description", "") or (snippets if isinstance(snippets, str) else "")

                        # Truncate snippet to 600 chars to respect local LM Studio context limits
                        formatted_results.append({
                            "title": hit.get("title", ""),
                            "url": hit.get("url", ""),
                            "snippet": snippet_text[:600],
                            "age": hit.get("age") or hit.get("published_date")
                        })

                    # If You.com returned 0 results, fall back to check if Tavily has answers
                    if not formatted_results:
                        logger.warning(f"You.com returned 0 results for '{query}'. Trying fallback search.")
                        return await self._fallback_to_tavily_or_ddg(query, count, "You.com returned 0 results")

                    return {
                        "provider": "you.com",
                        "query": query,
                        "total_results": len(formatted_results),
                        "results": formatted_results
                    }
                else:
                    logger.warning(f"You.com API returned {res.status_code}. Falling back to secondary search engine.")
                    return await self._fallback_to_tavily_or_ddg(query, count, f"You.com returned HTTP {res.status_code}")

        except Exception as e:
            logger.error(f"You.com search exception: {e}. Falling back to secondary search engine.")
            return await self._fallback_to_tavily_or_ddg(query, count, f"You.com search failed ({str(e)})")

    async def _fallback_to_tavily_or_ddg(self, query: str, count: int, reason: str) -> Dict[str, Any]:
        """Cascades to Tavily AI (if configured) or DuckDuckGo HTML parser."""
        try:
            from app.services.tavily_search import tavily_client
            if tavily_client.is_configured():
                logger.info(f"Cascading search to Tavily AI due to: {reason}")
                tav_res = await tavily_client.search(query=query, count=count)
                if tav_res.get("results") and tav_res.get("provider") == "tavily":
                    tav_res["notice"] = f"{reason}. Seamlessly retrieved via Tavily AI."
                    return tav_res
        except Exception as e:
            logger.debug(f"Tavily cascade failed: {e}")

        ddg_results = await search_web(query, max_results=count)
        return {
            "provider": "web_fallback",
            "query": query,
            "notice": f"{reason}. Used standard web intelligence.",
            "results": ddg_results,
            "total_results": len(ddg_results)
        }

    async def news_search(self, query: str, count: int = 5) -> Dict[str, Any]:
        """
        Queries You.com for recent news, funding rounds, press, or product updates.
        """
        if not self.is_configured():
            ddg_results = await search_web(f"{query} news", max_results=count)
            return {
                "provider": "web_fallback",
                "query": query,
                "notice": "You.com key not configured. Using standard web news.",
                "results": ddg_results
            }

        # Query You.com search with news freshness / query
        return await self.search(f"{query} news", count=count, freshness="month")

# Global singleton
you_client = YouSearchClient()
