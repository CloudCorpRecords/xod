import asyncio
import logging
from typing import Dict, Any, List, Optional
from app.services.trustmrr import trustmrr_client
from app.services.you_search import you_client
from app.services.tech_auditor import tech_auditor

logger = logging.getLogger("omni_search")

class OmniSearchEngine:
    """
    Proprietary Multi-Source OmniSearch Fusion Engine.
    Executes Query Fanout across:
    1. TrustMRR Verified SaaS Financials
    2. You.com High-Precision Web & AI Snippets
    3. Direct Live Domain & Tech Stack Auditor
    4. Reciprocal Rank Fusion (RRF) deduplication & scoring
    """

    def __init__(self, rrf_k: int = 60):
        self.rrf_k = rrf_k

    async def search(
        self,
        goal: str,
        target_name: Optional[str] = None,
        target_domain: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes parallel multi-vector query fanout, audits the target domain,
        retrieves verified financial data, and performs Reciprocal Rank Fusion.
        """
        logger.info(f"Running OmniSearch Fusion for: goal='{goal}', target='{target_name}'")

        # 1. Deduce target name / domain if not explicitly provided
        entity_name = target_name or self._guess_entity_name(goal)
        domain = target_domain or (f"{entity_name.lower().replace(' ', '')}.com" if entity_name and "." not in entity_name else entity_name)

        tasks = []

        # Vector 1: TrustMRR Financial Lookup (if entity name available)
        if entity_name:
            tasks.append(self._fetch_trustmrr_financials(entity_name))
        else:
            tasks.append(asyncio.sleep(0, result=None))

        # Vector 2: AI Web Intelligence (Tavily AI or You.com)
        from app.services.tavily_search import tavily_client
        if tavily_client.is_configured():
            tasks.append(tavily_client.search(goal, count=5, search_depth="basic", include_answer=True))
        else:
            tasks.append(you_client.search(goal, count=5))

        # Vector 3: Sentiment / Competitors / Complaints
        sentiment_query = f"{entity_name or goal} competitors pricing alternatives Reddit reviews"
        if you_client.is_configured():
            tasks.append(you_client.search(sentiment_query, count=4))
        elif tavily_client.is_configured():
            tasks.append(tavily_client.search(sentiment_query, count=4, search_depth="advanced"))
        else:
            tasks.append(you_client.search(sentiment_query, count=4))

        # Vector 4: Live Domain & Tech Stack Audit
        if domain:
            tasks.append(tech_auditor.audit(domain))
        else:
            tasks.append(asyncio.sleep(0, result=None))

        # Execute all vectors concurrently in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        trustmrr_data = results[0] if not isinstance(results[0], Exception) else None
        web_results_raw = results[1] if not isinstance(results[1], Exception) else {}
        sentiment_results_raw = results[2] if not isinstance(results[2], Exception) else {}
        tech_audit = results[3] if not isinstance(results[3], Exception) else None

        # 2. Extract web snippets from Vector 2 & 3
        all_web_items: List[Dict[str, Any]] = []
        if isinstance(web_results_raw, dict):
            all_web_items.extend(web_results_raw.get("results", []))
        if isinstance(sentiment_results_raw, dict):
            all_web_items.extend(sentiment_results_raw.get("results", []))

        # 3. Reciprocal Rank Fusion (RRF) Ranking
        fused_snippets = self._reciprocal_rank_fusion(all_web_items)

        # 4. Check for Discrepancies / Contradictions
        contradictions = self._detect_contradictions(trustmrr_data, fused_snippets)

        # 5. Structure the OmniSearch Intelligence Dossier
        dossier = {
            "entity": entity_name or "Market Topic",
            "search_goal": goal,
            "engine": "OmniSearch Fusion Engine (SOTA Multi-Vector RRF)",
            "verified_financials": trustmrr_data,
            "tech_stack_audit": tech_audit if (tech_audit and tech_audit.get("success")) else None,
            "top_ranked_intelligence": fused_snippets[:6],
            "contradictions_detected": contradictions,
            "summary_stats": {
                "sources_analyzed": len(all_web_items),
                "top_fused_sources": min(6, len(fused_snippets)),
                "financial_truth_verified": bool(trustmrr_data and not trustmrr_data.get("not_found")),
                "tech_fingerprinted": bool(tech_audit and tech_audit.get("success"))
            }
        }

        return dossier

    async def _fetch_trustmrr_financials(self, name: str) -> Optional[Dict[str, Any]]:
        """Queries TrustMRR for verified financial telemetry."""
        try:
            slug = name.lower().replace(" ", "-").replace(".", "")
            res = await trustmrr_client.get_startup(slug)
            if res and not res.get("error") and res.get("name"):
                return {
                    "name": res.get("name"),
                    "slug": res.get("slug"),
                    "verified_mrr": res.get("formatted", {}).get("mrr"),
                    "last_30d_revenue": res.get("formatted", {}).get("last30Days"),
                    "total_revenue": res.get("formatted", {}).get("totalRevenue"),
                    "asking_price": res.get("formatted", {}).get("askingPrice"),
                    "arr_multiple": res.get("formatted", {}).get("multiple"),
                    "growth_30d": res.get("formatted", {}).get("growth30d"),
                    "active_subscriptions": res.get("activeSubscriptions"),
                    "paying_customers": res.get("payingCustomers"),
                    "category": res.get("category"),
                    "funding": res.get("startupInsights", {}).get("fundingStatus")
                }
            # Fallback: search via list_startups
            list_res = await trustmrr_client.list_startups(limit=20)
            for s in list_res.get("startups", []):
                if name.lower() in s.get("name", "").lower() or name.lower() in s.get("slug", "").lower():
                    f = s.get("formatted", {})
                    return {
                        "name": s.get("name"),
                        "slug": s.get("slug"),
                        "verified_mrr": f.get("mrr"),
                        "last_30d_revenue": f.get("last30Days"),
                        "total_revenue": f.get("totalRevenue"),
                        "asking_price": f.get("askingPrice"),
                        "arr_multiple": f.get("multiple"),
                        "growth_30d": f.get("growth30d"),
                        "category": s.get("category")
                    }
            return {"not_found": True, "note": f"No verified financials found on TrustMRR for '{name}'"}
        except Exception as e:
            logger.warning(f"TrustMRR lookup in OmniSearch failed: {e}")
            return None

    def _reciprocal_rank_fusion(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Merges items and scores them using RRF: score = 1 / (k + rank).
        Deduplicates by URL and normalizes text.
        """
        seen_urls = set()
        scores: Dict[str, float] = {}
        item_map: Dict[str, Dict[str, Any]] = {}

        for rank, item in enumerate(items):
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # RRF scoring
            score = 1.0 / (self.rrf_k + rank + 1)
            # Boost reputable domains (docs, github, news, trusted sources)
            if any(dom in url for dom in ["trustmrr.com", "github.com", "ycombinator.com", "techcrunch.com"]):
                score *= 1.3

            scores[url] = score
            item_map[url] = {
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("snippet", "")[:400],
                "score": round(score, 4)
            }

        # Sort by RRF score descending
        sorted_urls = sorted(scores.keys(), key=lambda u: scores[u], reverse=True)
        return [item_map[u] for u in sorted_urls]

    def _detect_contradictions(
        self,
        financials: Optional[Dict[str, Any]],
        web_snippets: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Cross-checks verified Stripe data vs marketing claims in web snippets.
        """
        alerts = []
        if not financials or financials.get("not_found"):
            return alerts

        verified_mrr = financials.get("verified_mrr")
        if verified_mrr:
            # Check if web snippets claim vastly different numbers (e.g. $1M ARR vs $10k MRR)
            for s in web_snippets:
                snippet = s.get("snippet", "")
                if "$1M" in snippet or "$10M" in snippet:
                    if "k" in str(verified_mrr).lower():
                        alerts.append(f"Potential discrepancy: Web sources mention million-dollar revenues, but TrustMRR verified MRR is {verified_mrr}.")
                        break
        return alerts

    def _guess_entity_name(self, goal: str) -> Optional[str]:
        """Guesses the startup/company name from the research query."""
        words = goal.split()
        for w in words:
            cleaned = w.strip(".,;:?!'\"()[]")
            if "." in cleaned and len(cleaned) > 4:
                return cleaned
            if cleaned.lower() in ["stan", "chatbase", "shipfast", "postiz", "pdfai", "rezi", "cometly", "stripe"]:
                return cleaned.capitalize()
        return None

# Global singleton
omni_search_engine = OmniSearchEngine()
