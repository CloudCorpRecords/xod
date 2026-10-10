import os
import logging
import httpx
import re
import asyncio
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional

logger = logging.getLogger("sec_edgar")

class SECEDGARService:
    """
    Zero-Auth SEC EDGAR Form D & Corporate Diligence Service.
    Queries the SEC EFTS public search API and parses Form D XML submissions
    to uncover legal entity structures, private equity / venture funding rounds,
    total capital sold, investor counts, executive officers, and board directors.
    """

    def __init__(self):
        self.efts_search_url = "https://efts.sec.gov/LATEST/search-index"
        self.archives_base_url = "https://www.sec.gov/Archives/edgar/data"
        # The SEC requires a custom, contact-identified User-Agent header (otherwise 403 Forbidden)
        self.headers = {
            "User-Agent": f"XodResearch {os.environ.get('XOD_CONTACT_EMAIL', 'xod@localhost')}",
            "Accept": "application/json"
        }

    async def audit_sec_filings(self, target: str, entity_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Searches SEC EDGAR for Form D / Form D/A private placement filings for a target startup.
        Extracts verified legal entity details, funding rounds, and board leadership.
        """
        clean_target = self._clean_target_name(entity_name or target)
        if not clean_target:
            return {
                "target": target,
                "is_sec_registered": False,
                "error": "Invalid target query for SEC search"
            }

        async with httpx.AsyncClient(timeout=10.0, headers=self.headers, follow_redirects=True) as client:
            try:
                # Query SEC EFTS endpoint for Form D filings
                search_url = f"{self.efts_search_url}?q={clean_target}&forms=D"
                resp = await client.get(search_url)

                if resp.status_code != 200:
                    logger.warning(f"SEC EFTS returned status {resp.status_code} for {clean_target}")
                    return self._empty_result(clean_target, f"SEC API returned status {resp.status_code}")

                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])

                if not hits:
                    return self._empty_result(clean_target)

                # Filter and prioritize hits that match target entity name
                matched_hits = self._filter_relevant_hits(hits, clean_target)
                if not matched_hits:
                    # Fallback to the top hits if no strict match
                    matched_hits = hits[:5]

                # Sort matched hits by file_date descending to inspect latest rounds first
                matched_hits.sort(key=lambda h: h.get("_source", {}).get("file_date", ""), reverse=True)

                # Fetch and parse primary XML documents for top up to 4 filings
                filing_tasks = []
                for hit in matched_hits[:4]:
                    filing_tasks.append(self._parse_filing_hit(client, hit))

                parsed_filings = await asyncio.gather(*filing_tasks, return_exceptions=True)
                valid_filings = [f for f in parsed_filings if isinstance(f, dict) and f.get("success")]

                if not valid_filings:
                    return self._empty_result(clean_target, "Filings located but XML parsing produced no data")

                return self._aggregate_sec_intelligence(clean_target, valid_filings, len(hits))

            except httpx.RequestError as e:
                logger.error(f"HTTP error querying SEC EDGAR for {clean_target}: {e}")
                return self._empty_result(clean_target, f"SEC EDGAR connection error: {str(e)}")
            except Exception as e:
                logger.exception(f"Unexpected error in SEC EDGAR audit for {clean_target}: {e}")
                return self._empty_result(clean_target, f"Audit error: {str(e)}")

    def _clean_target_name(self, target: str) -> str:
        """Strips domain extensions, protocols, and standard corporate suffixes for cleaner search."""
        cleaned = re.sub(r"^https?://", "", target.strip().lower())
        cleaned = re.sub(r"^www\.", "", cleaned)
        # Strip common TLDs if user passed a domain
        cleaned = re.sub(r"\.(com|io|co|ai|app|org|net|dev|so|me)(/.*)?$", "", cleaned)
        # Remove special characters
        cleaned = re.sub(r"[^\w\s]", " ", cleaned).strip()
        # Remove common trailing terms for search query if too specific
        return cleaned.capitalize() if cleaned else target.strip()

    def _filter_relevant_hits(self, hits: List[Dict[str, Any]], target: str) -> List[Dict[str, Any]]:
        """Prioritizes hits where target name is part of the entity display name."""
        target_lower = target.lower()
        exact_or_substr = []
        for hit in hits:
            display_names = hit.get("_source", {}).get("display_names", [])
            for dn in display_names:
                if target_lower in dn.lower():
                    exact_or_substr.append(hit)
                    break
        return exact_or_substr

    async def _parse_filing_hit(self, client: httpx.AsyncClient, hit: Dict[str, Any]) -> Dict[str, Any]:
        """Fetches and parses the primary_doc.xml file for a single Form D hit."""
        hit_id = hit.get("_id", "")
        if ":" not in hit_id:
            return {"success": False, "error": "Invalid hit ID format"}

        accession_with_hyphens, filename = hit_id.split(":", 1)
        accession_clean = accession_with_hyphens.replace("-", "")

        src = hit.get("_source", {})
        ciks = src.get("ciks", [])
        if not ciks:
            return {"success": False, "error": "No CIK in hit source"}

        try:
            cik_int = int(ciks[0])
        except (ValueError, TypeError):
            return {"success": False, "error": "Malformed CIK"}

        xml_url = f"{self.archives_base_url}/{cik_int}/{accession_clean}/{filename}"

        try:
            r = await client.get(xml_url)
            if r.status_code != 200:
                return {"success": False, "status": r.status_code, "url": xml_url}

            root = ET.fromstring(r.text)
            return self._extract_form_d_fields(root, hit, xml_url)
        except Exception as e:
            logger.warning(f"Failed parsing Form D XML at {xml_url}: {e}")
            return {"success": False, "error": str(e), "url": xml_url}

    def _extract_form_d_fields(self, root: ET.Element, hit: Dict[str, Any], url: str) -> Dict[str, Any]:
        """Extracts structured fields from Form D XML schema."""
        def _get_text(path: str) -> Optional[str]:
            el = root.find(path)
            return el.text.strip() if el is not None and el.text else None

        src = hit.get("_source", {})
        file_date = src.get("file_date", "")
        form_type = src.get("form", "D")

        # Primary Issuer Info
        entity_name = _get_text(".//primaryIssuer/entityName")
        cik = _get_text(".//primaryIssuer/cik")
        jurisdiction = _get_text(".//primaryIssuer/jurisdictionOfInc")
        entity_type = _get_text(".//primaryIssuer/entityType")
        prev_name = _get_text(".//primaryIssuer/issuerPreviousNameList/previousName")

        # Address
        street = _get_text(".//primaryIssuer/issuerAddress/street1")
        city = _get_text(".//primaryIssuer/issuerAddress/city")
        state = _get_text(".//primaryIssuer/issuerAddress/stateOrCountry")
        zip_code = _get_text(".//primaryIssuer/issuerAddress/zipCode")
        full_address = ", ".join(filter(None, [street, city, state, zip_code]))

        # Financial Offering Details
        offering_raw = _get_text(".//offeringData/offeringSalesAmounts/totalOfferingAmount")
        sold_raw = _get_text(".//offeringData/offeringSalesAmounts/totalAmountSold")
        remaining_raw = _get_text(".//offeringData/offeringSalesAmounts/totalRemaining")
        investors_raw = _get_text(".//offeringData/investors/totalNumberAlreadyInvested")
        first_sale_date = _get_text(".//offeringData/typeOfFiling/dateOfFirstSale/value")
        industry = _get_text(".//offeringData/industryGroup/industryGroupType")

        # Parse numeric amounts
        offering_amount = float(offering_raw) if offering_raw and offering_raw.replace(".", "", 1).isdigit() else 0.0
        amount_sold = float(sold_raw) if sold_raw and sold_raw.replace(".", "", 1).isdigit() else 0.0
        remaining_amount = float(remaining_raw) if remaining_raw and remaining_raw.replace(".", "", 1).isdigit() else 0.0
        investors_count = int(investors_raw) if investors_raw and investors_raw.isdigit() else 0

        # Signer Block
        signer_name = _get_text(".//offeringData/signatureBlock/signature/nameOfSigner")
        signer_title = _get_text(".//offeringData/signatureBlock/signature/signatureTitle")
        signer_date = _get_text(".//offeringData/signatureBlock/signature/signatureDate")

        # Related Persons (Executive Officers & Directors)
        related_persons = []
        for p in root.findall(".//relatedPersonInfo"):
            fname = p.find(".//firstName")
            lname = p.find(".//lastName")
            first = fname.text.strip() if fname is not None and fname.text else ""
            last = lname.text.strip() if lname is not None and lname.text else ""
            roles = [r.text.strip() for r in p.findall(".//relationship") if r.text]
            full_pname = f"{first} {last}".strip()
            if full_pname:
                related_persons.append({
                    "name": full_pname,
                    "roles": roles
                })

        return {
            "success": True,
            "url": url,
            "file_date": file_date,
            "form_type": form_type,
            "entity_name": entity_name,
            "cik": cik,
            "jurisdiction": jurisdiction,
            "entity_type": entity_type,
            "previous_name": prev_name,
            "full_address": full_address,
            "offering_amount": offering_amount,
            "amount_sold": amount_sold,
            "remaining_amount": remaining_amount,
            "investors_count": investors_count,
            "first_sale_date": first_sale_date,
            "industry": industry,
            "signer_name": signer_name,
            "signer_title": signer_title,
            "signer_date": signer_date,
            "related_persons": related_persons
        }

    def _aggregate_sec_intelligence(self, target: str, filings: List[Dict[str, Any]], total_hits: int) -> Dict[str, Any]:
        """Aggregates multiple Form D filings into corporate diligence intelligence."""
        latest = filings[0]

        # Deduplicate and calculate totals
        total_sold = sum(f.get("amount_sold", 0.0) for f in filings)
        total_offered = sum(f.get("offering_amount", 0.0) for f in filings)
        max_investors = max(f.get("investors_count", 0) for f in filings)

        # Collect unique officers and directors
        directors = []
        executives = []
        seen_directors = set()
        seen_execs = set()

        for f in filings:
            for p in f.get("related_persons", []):
                name = p.get("name", "")
                roles = p.get("roles", [])
                if any("Director" in r for r in roles) and name not in seen_directors:
                    seen_directors.add(name)
                    directors.append({"name": name, "roles": roles})
                if any("Executive Officer" in r for r in roles) and name not in seen_execs:
                    seen_execs.add(name)
                    executives.append({"name": name, "roles": roles})

        # Governance summary string
        gov_parts = []
        if executives:
            exec_names = [e["name"] for e in executives[:3]]
            gov_parts.append(f"Execs: {', '.join(exec_names)}")
        if directors:
            dir_names = [d["name"] for d in directors[:3]]
            gov_parts.append(f"Board: {', '.join(dir_names)}")

        governance_summary = " | ".join(gov_parts) if gov_parts else "No officers listed in primary filing"

        # Diligence verdict classification
        if total_sold >= 50_000_000:
            verdict = f"Institutional Venture-Backed Scale (${self._format_currency(total_sold)} total sold across {len(filings)} filings)"
            color = "emerald"
        elif total_sold >= 10_000_000:
            verdict = f"Substantial Growth Equity (${self._format_currency(total_sold)} capital sold)"
            color = "brand"
        elif total_sold >= 1_000_000:
            verdict = f"Verified Venture Capital (${self._format_currency(total_sold)} capital sold)"
            color = "blue"
        elif total_sold > 0:
            verdict = f"Early-Stage Private Placement (${self._format_currency(total_sold)} capital sold)"
            color = "amber"
        else:
            verdict = "Form D Notice Filed (Capital Undisclosed or Zero Sold)"
            color = "zinc"

        # Build clean filing history records
        filing_history = []
        for f in filings:
            filing_history.append({
                "date": f.get("file_date"),
                "form": f.get("form_type"),
                "offering_usd": f.get("offering_amount"),
                "offering_formatted": f"${self._format_currency(f.get('offering_amount'))}",
                "sold_usd": f.get("amount_sold"),
                "sold_formatted": f"${self._format_currency(f.get('amount_sold'))}",
                "investors": f.get("investors_count"),
                "industry": f.get("industry"),
                "url": f.get("url")
            })

        return {
            "target": target,
            "is_sec_registered": True,
            "legal_entity_name": latest.get("entity_name"),
            "cik": latest.get("cik"),
            "jurisdiction": latest.get("jurisdiction"),
            "entity_type": latest.get("entity_type"),
            "previous_names": [latest.get("previous_name")] if latest.get("previous_name") else [],
            "registered_office": latest.get("full_address"),
            "total_filings_found": total_hits,
            "filings_audited": len(filings),
            "total_capital_sold_usd": total_sold,
            "total_capital_sold_formatted": f"${self._format_currency(total_sold)}",
            "total_offering_usd": total_offered,
            "total_investors_count": max_investors,
            "diligence_verdict": verdict,
            "diligence_color": color,
            "governance_summary": governance_summary,
            "executive_officers": executives,
            "board_directors": directors,
            "latest_filing": {
                "date": latest.get("file_date"),
                "form": latest.get("form_type"),
                "amount_sold": latest.get("amount_sold"),
                "amount_sold_formatted": f"${self._format_currency(latest.get('amount_sold'))}",
                "total_offering": latest.get("offering_amount"),
                "total_offering_formatted": f"${self._format_currency(latest.get('offering_amount'))}",
                "investors": latest.get("investors_count"),
                "signer_name": latest.get("signer_name"),
                "signer_title": latest.get("signer_title")
            },
            "filing_history": filing_history
        }

    def _empty_result(self, target: str, message: str = "No Form D filings located in SEC EDGAR") -> Dict[str, Any]:
        """Standardized empty response when company has no Regulation D private placement filings."""
        return {
            "target": target,
            "is_sec_registered": False,
            "legal_entity_name": None,
            "cik": None,
            "jurisdiction": None,
            "entity_type": None,
            "total_filings_found": 0,
            "total_capital_sold_usd": 0.0,
            "total_capital_sold_formatted": "$0",
            "diligence_verdict": "No Public SEC Form D Filings Detected (Bootstrapped or Foreign Entity)",
            "diligence_color": "zinc",
            "governance_summary": "No SEC filings on record",
            "executive_officers": [],
            "board_directors": [],
            "filing_history": [],
            "message": message
        }

    def _format_currency(self, amount: Optional[float]) -> str:
        """Formats dollar amount into human-readable compact notation ($149.9M, $5.2B, $500K)."""
        if not amount:
            return "0"
        if amount >= 1_000_000_000:
            return f"{amount / 1_000_000_000:.1f}B"
        if amount >= 1_000_000:
            return f"{amount / 1_000_000:.1f}M"
        if amount >= 1_000:
            return f"{amount / 1_000:.1f}K"
        return f"{int(amount):,}"

sec_edgar_service = SECEDGARService()
