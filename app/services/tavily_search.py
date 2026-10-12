import os
import httpx
import logging
from typing import Optional, Dict, Any, List
from app.config import settings
from app.services.web_search import search_web

logger = logging.getLogger("tavily_search")

class TavilySearchClient:
    """
    Tavily AI Search & Web Intelligence Client.
    Connects to official developer API at https://api.tavily.com/search.
    Features:
    - AI-synthesized direct answer generation with cited sources
    - Basic & Advanced search depth
    - Context-efficient snippet extraction
    - Mutual cascading fallback to You.com and DuckDuckGo when unconfigured or exhausted
    """

    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://api.tavily.com"):
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def api_key(self) -> str:
        return (
            self._api_key
            or settings.tavily_api_key
            or os.getenv("TAVILY_API_KEY", "")
        )

    def set_api_key(self, key: str):
        self._api_key = key

    def is_configured(self) -> bool:
        k = self.api_key
        return bool(k and len(k.strip()) >= 8)

    async def verify_key(self, test_key: Optional[str] = None) -> Dict[str, Any]:
        """Tests whether the provided or configured Tavily API key is valid and authorized."""
        key = (test_key or self.api_key or "").strip()
        if not key:
            return {"valid": False, "message": "No Tavily API key provided."}

        payload = {
            "api_key": key,
            "query": "artificial intelligence technological trends",
            "search_depth": "basic",
            "max_results": 1,
            "include_answer": False
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    f"{self.base_url}/search",
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )

                if res.status_code == 200:
                    data = res.json()
                    results = data.get("results", [])
                    return {
                        "valid": True,
                        "message": f"Tavily API key verified! Connected to Tavily AI search ({len(results)} sample result).",
                        "results_count": len(results)
                    }
                elif res.status_code in (401, 403):
                    err_msg = ""
                    try:
                        err_msg = res.json().get("detail", {}).get("error") or res.json().get("error", "")
                    except Exception:
                        pass
                    msg = f"Authentication failed (HTTP {res.status_code}). Invalid Tavily API key."
                    if err_msg:
                        msg += f" ({err_msg})"
                    return {"valid": False, "message": msg}
                elif res.status_code == 429:
                    return {
                        "valid": False,
                        "message": "Tavily rate limit or monthly credit quota exceeded (HTTP 429). Check your usage on app.tavily.com."
                    }
                else:
                    detail = res.text[:120]
                    try:
                        detail = res.json().get("detail") or detail
                    except Exception:
                        pass
                    return {"valid": False, "message": f"Tavily returned HTTP {res.status_code}: {detail}"}

        except Exception as e:
            return {"valid": False, "message": f"Connection error: {str(e)}"}

    async def search(
        self,
        query: str,
        count: int = 5,
        search_depth: str = "basic",
        include_answer: bool = True,
        max_results: Optional[int] = None,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        topic: Optional[str] = None,
        days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Executes a real-time web search via Tavily API.
        If unconfigured, rate-limited, or if an error occurs, seamlessly falls back
        to alternative configured providers (You.com or DuckDuckGo web search).
        """
        actual_count = min(10, max(1, max_results or count))
        depth = "advanced" if search_depth == "advanced" else "basic"

        # 1. Fallback if not configured
        if not self.is_configured():
            logger.info("Tavily API key not configured. Evaluating secondary web search engines.")
            return await self._fallback_search(query, actual_count, reason="Tavily key not configured")

        key = self.api_key.strip()
        payload: Dict[str, Any] = {
            "api_key": key,
            "query": query,
            "search_depth": depth,
            "max_results": actual_count,
            "include_answer": include_answer,
            "include_raw_content": False
        }
        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains
        if topic == "news":          # recent articles only ("latest", "who won", "this week")
            payload["topic"] = "news"
            payload["days"] = max(1, min(60, int(days or 14)))

        try:
            async with httpx.AsyncClient(timeout=14.0) as client:
                res = await client.post(
                    f"{self.base_url}/search",
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )

                if res.status_code == 200:
                    data = res.json()
                    raw_items = data.get("results", [])
                    formatted_results = []

                    for hit in raw_items[:actual_count]:
                        content_str = hit.get("content", "") or hit.get("snippet", "")
                        # Truncate long content to respect local LLM context limits
                        formatted_results.append({
                            "title": hit.get("title", ""),
                            "url": hit.get("url", ""),
                            "snippet": content_str[:700],
                            "score": hit.get("score"),
                            "published_date": hit.get("published_date")
                        })

                    # If Tavily returned 0 results, fall back to check if alternatives find answers
                    if not formatted_results:
                        logger.warning(f"Tavily returned 0 results for '{query}'. Trying fallback search.")
                        return await self._fallback_search(query, actual_count, reason="Tavily returned 0 results")

                    return {
                        "provider": "tavily",
                        "query": query,
                        "answer": data.get("answer"),
                        "search_depth": depth,
                        "total_results": len(formatted_results),
                        "results": formatted_results,
                        "response_time": data.get("response_time")
                    }

                elif res.status_code == 429:
                    logger.warning(f"Tavily credit limit exceeded (HTTP 429). Seamlessly falling back.")
                    return await self._fallback_search(
                        query, actual_count,
                        reason="Tavily credit quota exceeded (HTTP 429)"
                    )
                else:
                    logger.warning(f"Tavily returned HTTP {res.status_code}. Falling back to secondary engine.")
                    return await self._fallback_search(
                        query, actual_count,
                        reason=f"Tavily returned HTTP {res.status_code}"
                    )

        except Exception as e:
            logger.error(f"Tavily search exception: {e}. Falling back to secondary engine.")
            return await self._fallback_search(
                query, actual_count,
                reason=f"Tavily connection failed ({str(e)})"
            )

    async def _fallback_search(self, query: str, count: int, reason: str) -> Dict[str, Any]:
        """
        Cascades search to You.com (if configured) or DuckDuckGo HTML web search.
        """
        # Try You.com if configured
        try:
            from app.services.you_search import you_client
            if you_client.is_configured():
                logger.info(f"Cascading search to You.com due to: {reason}")
                you_res = await you_client.search(query=query, count=count)
                if you_res.get("results") and you_res.get("provider") == "you.com":
                    you_res["notice"] = f"{reason}. Used You.com Search."
                    return you_res
        except Exception as e:
            logger.debug(f"You.com cascade failed: {e}")

        # Final resilient fallback: DuckDuckGo HTML parser
        logger.info(f"Cascading search to standard web search due to: {reason}")
        ddg_results = await search_web(query, max_results=count)
        return {
            "provider": "web_fallback",
            "query": query,
            "notice": f"{reason}. Used standard web intelligence.",
            "results": ddg_results,
            "total_results": len(ddg_results)
        }

# Global singleton client
tavily_client = TavilySearchClient()
