import logging
import re
import asyncio
from typing import Dict, Any, List, Optional
import httpx
from bs4 import BeautifulSoup
from app.services.sec_edgar import sec_edgar_service

logger = logging.getLogger("funding_intel")

class FundingIntelService:
    """
    Zero-API-Key Startup Funding, Org Structure, and Accelerator Batch Intelligence.
    Integrates Y Combinator verified directory (batch, founders, headcount) with
    federal SEC EDGAR Form D venture capital registrations and scam risk screening.
    """

    def __init__(self):
        self.default_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

    async def audit_funding_and_org(self, target: str, domain: Optional[str] = None) -> Dict[str, Any]:
        """
        Audits startup organization structure, accelerator batches, and verified venture capital rounds.
        """
        clean_target = target.strip()
        slug = self._clean_slug(clean_target, domain)

        # Concurrently gather YC directory profile and federal SEC EDGAR filings
        t_yc = self._fetch_yc_profile(slug)
        t_sec = sec_edgar_service.audit_sec_filings(target=clean_target)

        results = await asyncio.gather(t_yc, t_sec, return_exceptions=True)

        yc_data = results[0] if isinstance(results[0], dict) else {}
        sec_data = results[1] if isinstance(results[1], dict) else {}

        # Synthesize organizational telemetry
        is_yc = yc_data.get("is_yc_backed", False)
        batch = yc_data.get("batch") or "Non-YC / Direct Venture"
        team_size = yc_data.get("team_size") or "Unspecified"
        yc_founders = yc_data.get("founders", [])

        # SEC capital metrics
        verified_sec_filings = sec_data.get("form_d_filings", [])
        total_vc_raised = sec_data.get("total_capital_sold_usd_formatted") or "No SEC Form D On File"
        delaware_entity = sec_data.get("delaware_entity_name")
        sec_officers = sec_data.get("executive_officers", [])

        # Consolidate founders list
        consolidated_founders = list(yc_founders)
        for off in sec_officers:
            name = off.get("name")
            if name and name not in consolidated_founders:
                consolidated_founders.append(name)

        # Scam / Legitimacy Score (0-100)
        legitimacy_score = 40  # Base
        reasons = []

        if is_yc:
            legitimacy_score += 35
            reasons.append(f"Verified Y Combinator Alumni ({batch})")

        if verified_sec_filings:
            legitimacy_score += 25
            reasons.append(f"Legally registered with federal SEC ({len(verified_sec_filings)} Form D filings)")

        if delaware_entity:
            legitimacy_score += 10
            reasons.append("Registered Delaware Corporate Governance")

        legitimacy_score = min(legitimacy_score, 99)

        if legitimacy_score >= 80:
            scam_risk = "LOW (Verified Institutional Entity)"
        elif legitimacy_score >= 50:
            scam_risk = "MODERATE (Standard Early-Stage Startup)"
        else:
            scam_risk = "ELEVATED (Unverified Corporate Footprint)"

        return {
            "target": clean_target,
            "accelerator": {
                "backed": is_yc,
                "name": "Y Combinator" if is_yc else "None Detected",
                "batch": batch,
                "status": yc_data.get("status") or "Active",
                "location": yc_data.get("location"),
                "founded_year": yc_data.get("founded_year"),
                "primary_partner": yc_data.get("primary_partner")
            },
            "org_structure": {
                "headcount_estimate": team_size,
                "founders": consolidated_founders[:6],
                "delaware_entity": delaware_entity
            },
            "verified_capital": {
                "total_raised_sec": total_vc_raised,
                "sec_filings_count": len(verified_sec_filings),
                "recent_filing_date": verified_sec_filings[0].get("filing_date") if verified_sec_filings else None
            },
            "scam_screening": {
                "legitimacy_score": legitimacy_score,
                "risk_assessment": scam_risk,
                "evidence_signals": reasons
            }
        }

    async def _fetch_yc_profile(self, slug: str) -> Dict[str, Any]:
        """Scrapes Y Combinator's verified public company directory."""
        candidates = [slug, slug.replace("-", ""), slug.replace("_", "")]
        for s in candidates:
            url = f"https://www.ycombinator.com/companies/{s}"
            try:
                async with httpx.AsyncClient(timeout=4.0, headers=self.default_headers, follow_redirects=True) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")

                        # Extract Batch
                        batch = None
                        batch_pill = soup.select_one('a[href*="/companies?batch="]')
                        if batch_pill:
                            batch = re.sub(r"Y Combinator Logo", "", batch_pill.get_text(strip=True)).strip()

                        # Extract metadata fields
                        team_size = None
                        founded = None
                        status = None
                        location = None
                        partner = None

                        for div in soup.select("div.flex.flex-row.justify-between"):
                            txt = div.get_text(" : ", strip=True)
                            if "Batch:" in txt and not batch:
                                batch = txt.split(":")[-1].strip()
                            elif "Team Size:" in txt:
                                team_size = txt.split(":")[-1].strip()
                            elif "Founded:" in txt:
                                founded = txt.split(":")[-1].strip()
                            elif "Status:" in txt:
                                status = txt.split(":")[-1].strip()
                            elif "Location:" in txt:
                                location = txt.split(":")[-1].strip()
                            elif "Primary Partner:" in txt:
                                partner = txt.split(":")[-1].strip()

                        # Extract Founders
                        founders = []
                        for d in soup.select("div.text-xl.font-bold"):
                            name = d.get_text(strip=True)
                            if name and len(name) > 2 and name not in founders:
                                founders.append(name)

                        return {
                            "is_yc_backed": True,
                            "slug": s,
                            "batch": batch,
                            "team_size": team_size,
                            "founded_year": founded,
                            "status": status,
                            "location": location,
                            "primary_partner": partner,
                            "founders": founders
                        }
            except Exception as e:
                logger.debug(f"YC scrape failed for {s}: {e}")

        return {"is_yc_backed": False}

    def _clean_slug(self, target: str, domain: Optional[str] = None) -> str:
        t = target.lower().strip()
        if domain:
            dom = domain.lower().replace("https://", "").replace("http://", "").split("/")[0].lstrip("www.")
            t = dom.split(".")[0]
        t = re.sub(r"[^a-z0-9-]", "-", t).strip("-")
        return t

funding_intel_service = FundingIntelService()
