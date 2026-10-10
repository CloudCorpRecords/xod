import logging
import httpx
import re
import asyncio
from typing import Dict, Any, List

logger = logging.getLogger("surface_explorer")

STANDARD_ENDPOINTS = [
    {"name": "Pricing Page", "path": "/pricing", "category": "monetization"},
    {"name": "Documentation", "path": "/docs", "category": "developer"},
    {"name": "API Reference", "path": "/api", "category": "developer"},
    {"name": "Changelog / Release Updates", "path": "/changelog", "category": "velocity"},
    {"name": "Enterprise Offering", "path": "/enterprise", "category": "sales"},
    {"name": "System Status", "path": "/status", "category": "infrastructure"},
    {"name": "Security & Compliance", "path": "/security", "category": "compliance"},
    {"name": "Terms of Service", "path": "/terms", "category": "legal"},
    {"name": "Privacy Policy", "path": "/privacy", "category": "legal"}
]

class SurfaceExplorerService:
    """
    Discovers key SaaS architectural and operational surfaces by probing
    standard endpoints and parsing robots.txt for hidden APIs and sitemaps.
    """

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }

    async def explore(self, domain: str) -> Dict[str, Any]:
        base_url = self._format_base_url(domain)
        discovered_surfaces: List[Dict[str, Any]] = []
        robots_sitemaps: List[str] = []
        robots_disallowed: List[str] = []

        async with httpx.AsyncClient(timeout=4.0, headers=self.headers, follow_redirects=True) as client:
            # 1. Fetch robots.txt
            try:
                robots_resp = await client.get(f"{base_url}/robots.txt")
                if robots_resp.status_code == 200:
                    text = robots_resp.text
                    for line in text.splitlines():
                        line = line.strip()
                        if line.lower().startswith("sitemap:"):
                            robots_sitemaps.append(line.split(":", 1)[1].strip())
                        elif line.lower().startswith("disallow:"):
                            dis = line.split(":", 1)[1].strip()
                            if dis and dis != "/":
                                robots_disallowed.append(dis)
            except Exception as e:
                logger.debug(f"Could not fetch robots.txt for {domain}: {e}")

            # 2. Concurrently probe standard SaaS endpoints
            async def probe_endpoint(item):
                url = f"{base_url}{item['path']}"
                try:
                    resp = await client.get(url)
                    # 200 or 300 range that isn't a 404 redirected to home
                    if resp.status_code in (200, 301, 302, 307, 308):
                        final_url = str(resp.url).rstrip("/")
                        # If redirected back to root domain, it doesn't exist
                        if final_url != base_url.rstrip("/"):
                            return {
                                "name": item["name"],
                                "path": item["path"],
                                "category": item["category"],
                                "status_code": resp.status_code,
                                "url": final_url,
                                "accessible": True
                            }
                except Exception:
                    pass
                return None

            tasks = [probe_endpoint(item) for item in STANDARD_ENDPOINTS]
            results = await asyncio.gather(*tasks)
            discovered_surfaces = [r for r in results if r is not None]

        # 3. Analyze operational maturity signals
        paths_found = [s["path"] for s in discovered_surfaces]
        has_api = "/api" in paths_found or "/docs" in paths_found or any("/api" in d for d in robots_disallowed)
        has_enterprise = "/enterprise" in paths_found
        has_changelog = "/changelog" in paths_found
        has_status = "/status" in paths_found
        has_pricing = "/pricing" in paths_found

        maturity_score = 0
        if has_pricing: maturity_score += 20
        if has_api: maturity_score += 25
        if has_enterprise: maturity_score += 20
        if has_changelog: maturity_score += 20
        if has_status: maturity_score += 15

        return {
            "domain": domain,
            "base_url": base_url,
            "discovered_surfaces": discovered_surfaces,
            "total_surfaces_detected": len(discovered_surfaces),
            "operational_signals": {
                "has_public_api_or_docs": has_api,
                "has_enterprise_tier": has_enterprise,
                "has_active_changelog": has_changelog,
                "has_status_page": has_status,
                "has_dedicated_pricing": has_pricing
            },
            "maturity_score": min(100, maturity_score),
            "sitemaps": robots_sitemaps[:3],
            "sample_hidden_endpoints": robots_disallowed[:6]
        }

    def _format_base_url(self, target: str) -> str:
        target = target.strip()
        if not target.startswith("http"):
            target = f"https://{target}"
        return target.rstrip("/")

surface_explorer_service = SurfaceExplorerService()
