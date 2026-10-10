import logging
import httpx
import re
import asyncio
from typing import Dict, Any, List, Set, Optional

logger = logging.getLogger("subdomain_recon")

class SubdomainReconService:
    """
    Performs Certificate Transparency (CT) log mining and passive DNS reconnaissance
    to discover unlisted subdomains, shadow infrastructure, internal staging environments,
    private APIs, and operational sprawl.
    """

    def __init__(self):
        self.certspotter_url = "https://api.certspotter.com/v1/issuances"
        self.hackertarget_url = "https://api.hackertarget.com/hostsearch/"
        self.crtsh_url = "https://crt.sh/"

    async def recon(self, domain: str) -> Dict[str, Any]:
        """
        Executes multi-source reconnaissance across CT logs and passive DNS.
        """
        clean_domain = self._clean_domain(domain)
        if not clean_domain:
            return {"error": "Invalid domain supplied"}

        discovered_subdomains: Set[str] = set()
        ip_mappings: Dict[str, str] = {}

        # Concurrently query Certspotter CT and HackerTarget Passive DNS
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "XodRecon/1.0"}) as client:
            tasks = [
                self._query_certspotter(client, clean_domain),
                self._query_hackertarget(client, clean_domain)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, tuple):
                sub_list, ips = res
                discovered_subdomains.update(sub_list)
                ip_mappings.update(ips)
            elif isinstance(res, list):
                discovered_subdomains.update(res)

        # Remove base domain and wildcards
        clean_subs = set()
        for sub in discovered_subdomains:
            s = sub.strip().lower()
            if s.startswith("*."):
                s = s[2:]
            if s and s != clean_domain and s.endswith(f".{clean_domain}"):
                clean_subs.add(s)

        sorted_subs = sorted(list(clean_subs))

        # Classify subdomains by operational role
        classified = self._classify_subdomains(sorted_subs, clean_domain)

        def _match_tokens(sub_str: str, target_tokens: set) -> bool:
            prefix_tokens = set(re.split(r"[-._]", sub_str.replace(f".{clean_domain}", "").lower()))
            return bool(prefix_tokens & target_tokens)

        # Extract architecture signals
        signals = {
            "is_microservice_architecture": len(classified["microservices_and_apis"]) >= 3,
            "has_enterprise_subdomains": len(classified["enterprise_and_docs"]) > 0 and any("enterprise" in s for s in classified["enterprise_and_docs"]),
            "has_realtime_websockets": any(_match_tokens(s, {"ws", "socket", "realtime", "live"}) for s in sorted_subs),
            "has_exposed_staging_or_dev": len(classified["staging_and_dev"]) > 0,
            "has_admin_panels_exposed": len(classified["admin_and_auth"]) > 0 and any("admin" in s for s in classified["admin_and_auth"]),
            "has_ai_or_mcp_endpoints": any(_match_tokens(s, {"mcp", "ai", "agent", "llm", "copilot"}) for s in sorted_subs)
        }

        # Notable discoveries
        notable = []
        if signals["has_enterprise_subdomains"]:
            ent_subs = [s for s in classified["enterprise_and_docs"] if "enterprise" in s]
            notable.append(f"Enterprise Infrastructure: {', '.join(ent_subs[:2])}")
        if signals["has_ai_or_mcp_endpoints"]:
            ai_subs = [s for s in sorted_subs if _match_tokens(s, {"mcp", "ai", "agent", "llm", "copilot"})]
            notable.append(f"AI/Agent Protocol Endpoints: {', '.join(ai_subs[:2])}")
        if signals["has_realtime_websockets"]:
            ws_subs = [s for s in sorted_subs if _match_tokens(s, {"ws", "socket", "realtime", "live"})]
            notable.append(f"Realtime WebSocket Clusters: {', '.join(ws_subs[:2])}")
        if signals["has_exposed_staging_or_dev"]:
            stg_subs = classified["staging_and_dev"][:2]
            notable.append(f"Publicly Observable Pre-prod/Staging: {', '.join(stg_subs)}")
        if signals["has_admin_panels_exposed"]:
            adm_subs = [s for s in classified["admin_and_auth"] if "admin" in s][:2]
            notable.append(f"Dedicated Admin/Console Surfaces: {', '.join(adm_subs)}")

        # Score & verdict calculation
        maturity_score = min(100, 20 + len(sorted_subs) * 3 + (15 if signals["is_microservice_architecture"] else 0) + (15 if signals["has_enterprise_subdomains"] else 0))

        if len(sorted_subs) >= 20:
            scale_verdict = "Complex Multi-Tier Cloud Footprint (20+ Active Subdomains)"
        elif len(sorted_subs) >= 5:
            scale_verdict = "Multi-Service Architecture (Modular APIs & Environments)"
        elif len(sorted_subs) >= 1:
            scale_verdict = "Monolithic / Early-Stage Footprint (Minimal Subdomains)"
        else:
            scale_verdict = "Single Origin / Protected Ingress (No Public CT Records)"

        return {
            "domain": clean_domain,
            "total_subdomains_discovered": len(sorted_subs),
            "scale_verdict": scale_verdict,
            "architecture_signals": signals,
            "infrastructure_maturity_score": maturity_score,
            "notable_discoveries": notable,
            "classified_subdomains": classified,
            "sample_subdomains": sorted_subs[:30],
            "ip_mappings": {k: ip_mappings[k] for k in sorted(ip_mappings.keys())[:15] if k in ip_mappings}
        }

    async def _query_certspotter(self, client: httpx.AsyncClient, domain: str) -> List[str]:
        """Queries Certspotter Certificate Transparency API."""
        try:
            url = f"{self.certspotter_url}?domain={domain}&include_subdomains=true&expand=dns_names"
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                subdomains = []
                for item in data:
                    for name in item.get("dns_names", []):
                        subdomains.append(name)
                return subdomains
        except Exception as e:
            logger.debug(f"Certspotter query failed for {domain}: {e}")
        return []

    async def _query_hackertarget(self, client: httpx.AsyncClient, domain: str) -> tuple:
        """Queries HackerTarget Hostsearch API for DNS and IP mappings."""
        subs = []
        ips = {}
        try:
            url = f"{self.hackertarget_url}?q={domain}"
            resp = await client.get(url)
            if resp.status_code == 200 and "error" not in resp.text.lower():
                lines = resp.text.strip().split("\n")
                for line in lines:
                    parts = line.split(",")
                    if len(parts) >= 2:
                        host = parts[0].strip().lower()
                        ip = parts[1].strip()
                        subs.append(host)
                        ips[host] = ip
                    elif len(parts) == 1 and parts[0].strip():
                        subs.append(parts[0].strip().lower())
        except Exception as e:
            logger.debug(f"HackerTarget query failed for {domain}: {e}")
        return subs, ips

    def _classify_subdomains(self, subdomains: List[str], domain: str) -> Dict[str, List[str]]:
        classified: Dict[str, List[str]] = {
            "admin_and_auth": [],
            "staging_and_dev": [],
            "microservices_and_apis": [],
            "enterprise_and_docs": [],
            "observability_and_internal": [],
            "integrations_and_tools": [],
            "other": []
        }

        for sub in subdomains:
            prefix = sub.replace(f".{domain}", "").lower()
            tokens = set(re.split(r"[-._]", prefix))

            if tokens & {"admin", "auth", "clerk", "accounts", "sso", "login", "identity", "portal", "iam"}:
                classified["admin_and_auth"].append(sub)
            elif tokens & {"staging", "dev", "stage", "uat", "test", "sandbox", "beta", "preview", "qa"}:
                classified["staging_and_dev"].append(sub)
            elif tokens & {"api", "v1", "v2", "v3", "ws", "websocket", "grpc", "graphql", "gateway", "image", "upload", "cdn", "assets", "media", "imageservices"}:
                classified["microservices_and_apis"].append(sub)
            elif tokens & {"grafana", "prometheus", "kibana", "sentry", "datadog", "metabase", "jaeger", "elk", "logs", "monitor", "uptime", "metric"}:
                classified["observability_and_internal"].append(sub)
            elif tokens & {"enterprise", "corp", "partner", "docs", "documentation", "help", "status", "learn", "support", "roadmap", "cms"}:
                classified["enterprise_and_docs"].append(sub)
            elif tokens & {"mcp", "n8n", "zapier", "cli", "bot", "webhook", "hook"}:
                classified["integrations_and_tools"].append(sub)
            else:
                classified["other"].append(sub)

        return classified

    def _clean_domain(self, target: str) -> str:
        target = target.strip()
        target = re.sub(r"^https?://", "", target, flags=re.I)
        target = target.split("/")[0]
        target = target.split(":")[0]
        return target.lower()

subdomain_recon_service = SubdomainReconService()
