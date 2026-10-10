import time
import httpx
import logging
from typing import Optional, Dict, Any, List
from app.config import settings

logger = logging.getLogger("trustmrr")

VALID_CATEGORIES = [
    "ai", "saas", "developer-tools", "fintech", "marketing", "ecommerce",
    "productivity", "design-tools", "no-code", "analytics", "crypto-web3",
    "education", "health-fitness", "social-media", "content-creation", "sales",
    "customer-support", "recruiting", "real-estate", "travel", "legal",
    "security", "iot-hardware", "green-tech", "entertainment", "games",
    "community", "news-magazines", "utilities", "marketplace", "mobile-apps"
]

VALID_SORTS = [
    "revenue-desc", "revenue-asc", "price-desc", "price-asc",
    "multiple-asc", "multiple-desc", "growth-desc", "growth-asc",
    "listed-desc", "listed-asc", "best-deal"
]

class TrustMRRError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Any] = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details

class TrustMRRClient:
    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://trustmrr.com/api/v1"):
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.last_rate_limit: Dict[str, Any] = {
            "limit": 10,
            "remaining": 10,
            "reset": 0
        }

    @property
    def api_key(self) -> str:
        return self._api_key or settings.trustmrr_api_key

    def set_api_key(self, key: str):
        self._api_key = key

    def _get_headers(self) -> Dict[str, str]:
        key = self.api_key
        if not key:
            raise TrustMRRError(
                "TrustMRR API key is missing. Please set your key in Settings or in .env",
                status_code=401
            )
        return {
            "Authorization": f"Bearer {key.strip()}",
            "Accept": "application/json",
            "User-Agent": "XodResearcher/1.0"
        }

    def _get_cache(self, key: str) -> Optional[Any]:
        if key in self._cache:
            entry = self._cache[key]
            if time.time() < entry["expires"]:
                return entry["data"]
            del self._cache[key]
        return None

    def _set_cache(self, key: str, data: Any, ttl: Optional[int] = None):
        ttl = ttl or settings.cache_ttl_seconds
        self._cache[key] = {
            "expires": time.time() + ttl,
            "data": data
        }

    def format_money(self, cents: Optional[int]) -> str:
        if cents is None:
            return "N/A"
        dollars = cents / 100
        if dollars >= 1_000_000:
            return f"${dollars / 1_000_000:.2f}M"
        if dollars >= 1_000:
            return f"${dollars / 1_000:.1f}k"
        return f"${dollars:.2f}"

    def format_percent(self, val: Optional[float]) -> str:
        if val is None:
            return "N/A"
        # If val is decimal (e.g. 0.15), format as 15%
        if abs(val) < 2.0:
            return f"{val * 100:.1f}%"
        return f"{val:.1f}%"

    def enrich_startup(self, s: Dict[str, Any]) -> Dict[str, Any]:
        """Adds formatted monetary metrics and human-friendly fields."""
        if not isinstance(s, dict):
            return s
        rev = s.get("revenue") or {}
        last30 = rev.get("last30Days")
        mrr = rev.get("mrr")
        total = rev.get("total")
        asking_price = s.get("askingPrice")

        s["formatted"] = {
            "mrr": self.format_money(mrr),
            "last30Days": self.format_money(last30),
            "totalRevenue": self.format_money(total),
            "askingPrice": self.format_money(asking_price) if asking_price else "Not for sale",
            "multiple": f"{s.get('multiple'):.1f}x" if s.get("multiple") else "N/A",
            "growth30d": self.format_percent(s.get("growth30d")),
            "growthMRR30d": self.format_percent(s.get("growthMRR30d")),
            "profitMargin": f"{s.get('profitMarginLast30Days')}%" if s.get("profitMarginLast30Days") is not None else "N/A",
        }
        return s

    async def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cache_key = f"{endpoint}?{str(sorted(params.items())) if params else ''}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached

        # Check rate limit reset if remaining == 0
        if self.last_rate_limit["remaining"] <= 0 and time.time() < self.last_rate_limit["reset"]:
            wait_sec = max(1.0, self.last_rate_limit["reset"] - time.time())
            logger.warning(f"TrustMRR rate limit hit. Pausing for {wait_sec:.1f}s")
            time.sleep(min(wait_sec, 5.0))

        headers = self._get_headers()
        url = f"{self.base_url}{endpoint}"

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.get(url, params=params, headers=headers)
            except httpx.RequestError as exc:
                raise TrustMRRError(f"Network error connecting to TrustMRR: {exc}")

            # Capture rate limits
            if "X-RateLimit-Limit" in res.headers:
                self.last_rate_limit["limit"] = int(res.headers.get("X-RateLimit-Limit", 10))
            if "X-RateLimit-Remaining" in res.headers:
                self.last_rate_limit["remaining"] = int(res.headers.get("X-RateLimit-Remaining", 10))
            if "X-RateLimit-Reset" in res.headers:
                self.last_rate_limit["reset"] = float(res.headers.get("X-RateLimit-Reset", time.time() + 60))

            if res.status_code == 401:
                raise TrustMRRError("Invalid or expired TrustMRR API key.", status_code=401)
            elif res.status_code == 403:
                raise TrustMRRError("Access forbidden under TrustMRR Acceptable Use Policy.", status_code=403)
            elif res.status_code == 404:
                raise TrustMRRError(f"Resource not found at {endpoint}", status_code=404)
            elif res.status_code == 429:
                raise TrustMRRError("TrustMRR rate limit exceeded. Please try again shortly.", status_code=429)
            elif res.status_code >= 400:
                raise TrustMRRError(f"TrustMRR API error {res.status_code}: {res.text}", status_code=res.status_code)

            data = res.json()
            self._set_cache(cache_key, data)
            return data

    async def verify_key(self) -> Dict[str, Any]:
        """Checks if the configured API key is valid."""
        if not self.api_key:
            return {"valid": False, "message": "No API key configured."}
        try:
            res = await self._request("/startups", params={"limit": 1})
            total = res.get("meta", {}).get("total", 0)
            return {
                "valid": True,
                "message": f"API Key verified! Connected to TrustMRR ({total} verified startups indexed).",
                "totalStartups": total,
                "rateLimit": self.last_rate_limit
            }
        except TrustMRRError as e:
            return {"valid": False, "message": str(e), "status_code": e.status_code}
        except Exception as e:
            return {"valid": False, "message": str(e)}

    async def list_startups(
        self,
        page: int = 1,
        limit: int = 10,
        sort: str = "revenue-desc",
        category: Optional[str] = None,
        on_sale: Optional[bool] = None,
        min_revenue: Optional[int] = None,
        max_revenue: Optional[int] = None,
        min_mrr: Optional[int] = None,
        max_mrr: Optional[int] = None,
        min_growth: Optional[float] = None,
        max_growth: Optional[float] = None,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        team_size: Optional[str] = None,
        funding_status: Optional[str] = None,
        x_handle: Optional[str] = None
    ) -> Dict[str, Any]:
        """Query startups with all available TrustMRR filters."""
        params: Dict[str, Any] = {
            "page": max(1, page),
            "limit": min(10, max(1, limit)),
        }
        if sort in VALID_SORTS:
            params["sort"] = sort
        if category and category.lower() in VALID_CATEGORIES:
            params["category"] = category.lower()
        if on_sale is not None:
            params["onSale"] = "true" if on_sale else "false"
        if min_revenue is not None:
            params["minRevenue"] = int(min_revenue)
        if max_revenue is not None:
            params["maxRevenue"] = int(max_revenue)
        if min_mrr is not None:
            params["minMrr"] = int(min_mrr)
        if max_mrr is not None:
            params["maxMrr"] = int(max_mrr)
        if min_growth is not None:
            params["minGrowth"] = float(min_growth)
        if max_growth is not None:
            params["maxGrowth"] = float(max_growth)
        if min_price is not None:
            params["minPrice"] = int(min_price)
        if max_price is not None:
            params["maxPrice"] = int(max_price)
        if team_size:
            params["teamSize"] = team_size
        if funding_status in ["bootstrapped", "vc-funded"]:
            params["fundingStatus"] = funding_status
        if x_handle:
            params["xHandle"] = x_handle.replace("@", "")

        res = await self._request("/startups", params=params)
        startups = res.get("data", [])
        enriched = [self.enrich_startup(s) for s in startups]
        return {
            "startups": enriched,
            "meta": res.get("meta", {}),
            "rateLimit": self.last_rate_limit
        }

    async def get_startup(self, slug: str) -> Dict[str, Any]:
        """Fetch complete verified profile for a single startup."""
        slug_clean = slug.strip().lower().replace("https://trustmrr.com/startups/", "").replace("https://trustmrr.com/", "").strip("/")
        res = await self._request(f"/startups/{slug_clean}")
        data = res.get("data", {})
        enriched = self.enrich_startup(data)
        return enriched

    async def search_startups(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """Fuzzy search across category, name, or keywords."""
        q_lower = query.lower().strip()
        matched_category = None
        for cat in VALID_CATEGORIES:
            if cat in q_lower or q_lower in cat:
                matched_category = cat
                break

        # First try listing
        res = await self.list_startups(
            limit=10,
            category=matched_category,
            sort="revenue-desc"
        )
        all_results = res.get("startups", [])

        # Filter in-memory if query has specific keywords
        if q_lower and matched_category != q_lower:
            filtered = [
                s for s in all_results
                if q_lower in (s.get("name") or "").lower()
                or q_lower in (s.get("slug") or "").lower()
                or q_lower in (s.get("description") or "").lower()
            ]
            if filtered:
                all_results = filtered

        return {
            "query": query,
            "matched_category": matched_category,
            "startups": all_results[:limit],
            "total_found": len(all_results)
        }

    async def compare_startups(self, slugs: List[str]) -> Dict[str, Any]:
        """Compare multiple startups side-by-side."""
        results = []
        for slug in slugs[:5]: # Cap to 5 to protect rate limits
            try:
                s = await self.get_startup(slug)
                results.append(s)
            except Exception as e:
                results.append({"slug": slug, "error": str(e)})

        return {
            "compared_count": len(results),
            "startups": results
        }

    async def get_acquisition_deals(
        self,
        category: Optional[str] = None,
        max_multiple: Optional[float] = None,
        max_price_usd: Optional[float] = None
    ) -> Dict[str, Any]:
        """Screen businesses listed for sale."""
        max_price_cents = int(max_price_usd * 100) if max_price_usd else None
        res = await self.list_startups(
            on_sale=True,
            sort="best-deal",
            category=category,
            max_price=max_price_cents,
            limit=10
        )
        startups = res.get("startups", [])
        if max_multiple:
            startups = [s for s in startups if s.get("multiple") and s.get("multiple") <= max_multiple]

        return {
            "deals": startups,
            "meta": res.get("meta", {})
        }

# Global singleton client
trustmrr_client = TrustMRRClient()
