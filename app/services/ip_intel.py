import os
import re
import asyncio
import logging
import httpx
from typing import Dict, Any, List, Optional
from app.services.you_search import you_client

logger = logging.getLogger("ip_intel")

class IPIntelService:
    """
    Audits intellectual property, federal USPTO trademarks, proprietary patents,
    and technological moats across USPTO/Justia Trademarks, Google Patents, and Wikidata.
    """

    def __init__(self):
        self.headers = {
            "User-Agent": f"XodResearch/1.0 ({os.environ.get('XOD_CONTACT_EMAIL', 'xod@localhost')})"
        }

    async def audit_ip_landscape(self, target: str, entity_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Executes a multi-vector intellectual property and defensibility audit.
        """
        clean_target = self._clean_target_name(entity_name or target)
        if not clean_target:
            return self._empty_result(target, "Invalid target for IP landscape audit")

        # Run trademark search, patent search, and Wikidata probing concurrently
        tm_task = self._audit_trademarks(clean_target)
        pat_task = self._audit_patents(clean_target)
        wiki_task = self._probe_wikidata_products(clean_target)

        tm_results, pat_results, wiki_products = await asyncio.gather(
            tm_task, pat_task, wiki_task, return_exceptions=True
        )

        trademarks = tm_results if isinstance(tm_results, list) else []
        patents = pat_results if isinstance(pat_results, list) else []
        products = wiki_products if isinstance(wiki_products, list) else []

        # Deduplicate trademarks by mark name
        unique_trademarks = []
        seen_tms = set()
        for tm in trademarks:
            mark_key = tm.get("mark", "").strip().upper()
            if mark_key and mark_key not in seen_tms:
                seen_tms.add(mark_key)
                unique_trademarks.append(tm)

        # Deduplicate patents by patent publication number
        unique_patents = []
        seen_pats = set()
        for pt in patents:
            p_num = pt.get("patent_number", "").strip().upper()
            if p_num and p_num not in seen_pats:
                seen_pats.add(p_num)
                unique_patents.append(pt)

        # Compute IP Defensibility Score (0-100)
        tm_score = min(40, len(unique_trademarks) * 15)
        pat_score = min(50, len(unique_patents) * 25)
        prod_score = min(10, len(products) * 5)
        ip_score = tm_score + pat_score + prod_score

        # Determine Verdict & Brand Protection
        has_patents = len(unique_patents) > 0
        has_tms = len(unique_trademarks) > 0

        if has_patents and has_tms:
            verdict = f"Institutional IP Moat ({len(unique_patents)} Patents, {len(unique_trademarks)} Federal Trademarks)"
            color = "emerald"
        elif has_tms:
            verdict = f"Brand Defense Moat ({len(unique_trademarks)} Federal Trademarks Registered)"
            color = "brand"
        elif has_patents:
            verdict = f"Proprietary Technology Moat ({len(unique_patents)} Patents Filed/Assigned)"
            color = "blue"
        else:
            verdict = "Open Source Brand Commons / Unregistered Mark (No USPTO Records Found)"
            color = "zinc"

        brand_protection = (
            "Protected Brand (Federal USPTO Trademark Claims on Record)"
            if has_tms else
            "Unregistered Brand Commons (Potential Squatting / Infringement Risk)"
        )

        # Build clean summary
        summary_items = []
        if unique_trademarks:
            tm_names = [t["mark"] for t in unique_trademarks[:3]]
            summary_items.append(f"Trademarks: {', '.join(tm_names)}")
        if unique_patents:
            p_nums = [p["patent_number"] for p in unique_patents[:3]]
            summary_items.append(f"Patents: {', '.join(p_nums)}")
        if products:
            summary_items.append(f"Core Products: {', '.join(products[:3])}")

        ip_summary = " | ".join(summary_items) if summary_items else "No formal patents or trademarks detected"

        return {
            "target": target,
            "audited_name": clean_target,
            "has_ip_moat": has_patents or has_tms,
            "ip_moat_score": ip_score,
            "ip_defensibility_verdict": verdict,
            "ip_moat_color": color,
            "brand_protection_status": brand_protection,
            "ip_summary": ip_summary,
            "total_trademarks": len(unique_trademarks),
            "total_patents": len(unique_patents),
            "trademarks": unique_trademarks[:6],
            "patents": unique_patents[:5],
            "registered_products": products[:5]
        }

    def _clean_target_name(self, target: str) -> str:
        """Strips URL protocols, web extensions, and corporate noise for precise IP queries."""
        cleaned = re.sub(r"^https?://", "", target.strip().lower())
        cleaned = re.sub(r"^www\.", "", cleaned)
        cleaned = re.sub(r"\.(com|io|co|ai|app|org|net|dev|so|me)(/.*)?$", "", cleaned)
        cleaned = re.sub(r"[^\w\s]", " ", cleaned).strip()
        return cleaned.capitalize() if cleaned else target.strip()

    async def _audit_trademarks(self, target: str) -> List[Dict[str, Any]]:
        """Queries You.com / web for USPTO and Justia registered trademark entries."""
        query = f"\"{target}\" site:trademarks.justia.com OR site:uspto.report/TM"
        try:
            res = await you_client.search(query, count=6)
            hits = res.get("results", [])
        except Exception as e:
            logger.warning(f"Error querying trademarks for {target}: {e}")
            return []

        trademarks = []
        target_lower = target.lower()

        for h in hits:
            title = h.get("title", "")
            snippet = h.get("snippet", "")
            url = h.get("url", "")
            full_text = f"{title} {snippet}".lower()

            # Filter out generic directories or search indices
            if target_lower not in full_text:
                continue
            if any(title.lower().startswith(b) for b in ["browse ", "category ", "trademark resource", "search "]):
                continue

            # Parse Mark Name
            mark_match = re.search(r"^([A-Z0-9\.\-\s]+?)\s+Trademark", title, re.IGNORECASE)
            reg_match = re.search(r"Registration Number\s*[:\.]?\s*(\d+)", title + " " + snippet, re.IGNORECASE)
            serial_match = re.search(r"Serial Number\s*[:\.]?\s*(\d+)", title + " " + snippet, re.IGNORECASE)
            filed_match = re.search(r"Filed\s+(?:in|on)?\s*([A-Za-z0-9\s,]+?\d{4})", snippet, re.IGNORECASE)

            mark_name = mark_match.group(1).strip() if mark_match else title.split("-")[0].strip()

            # Ensure mark name is not a generic header
            if len(mark_name) > 40 or mark_name.lower().startswith("browse"):
                continue

            # Scope / goods and services excerpt
            goods_match = re.search(r"covers\s+([^.]+)", snippet, re.IGNORECASE)
            goods_desc = goods_match.group(1).strip() if goods_match else snippet[:150]

            trademarks.append({
                "mark": mark_name,
                "status": "Registered" if reg_match else "Filed / Published",
                "reg_number": reg_match.group(1) if reg_match else None,
                "serial_number": serial_match.group(1) if serial_match else None,
                "filing_date": filed_match.group(1).strip() if filed_match else None,
                "goods_and_services": goods_desc,
                "url": url
            })

        return trademarks

    async def _audit_patents(self, target: str) -> List[Dict[str, Any]]:
        """Queries Google Patents and reassignment logs for proprietary technology filings."""
        query = f"site:patents.google.com/patent/ \"{target}\" (\"assignee\" OR \"reassignment\" OR \"current assignee\" OR \"inventor\")"
        try:
            res = await you_client.search(query, count=6)
            hits = res.get("results", [])
        except Exception as e:
            logger.warning(f"Error querying patents for {target}: {e}")
            return []

        patents = []
        target_lower = target.lower()

        for h in hits:
            title = h.get("title", "")
            snippet = h.get("snippet", "")
            url = h.get("url", "")
            full_text = f"{title} {snippet}".lower()

            if target_lower not in full_text:
                continue

            # Extract patent / publication number format (e.g., US10325030B2, WO2026072776A1)
            p_match = re.search(r"\b(US[0-9A-Z]{7,}|WO[0-9A-Z]{7,}|EP[0-9A-Z]{7,})\b", title + " " + url)
            if not p_match:
                continue

            p_num = p_match.group(1)
            clean_title = re.sub(r"^(US[0-9A-Z]+|WO[0-9A-Z]+|EP[0-9A-Z]+)\s*[-:]*\s*", "", title)
            clean_title = clean_title.replace("- Google Patents", "").strip()

            patents.append({
                "patent_number": p_num,
                "title": clean_title,
                "abstract": snippet[:200],
                "url": url
            })

        return patents

    async def _probe_wikidata_products(self, target: str) -> List[str]:
        """Queries Wikidata for officially cataloged software products, platforms, or tools."""
        try:
            async with httpx.AsyncClient(timeout=5.0, headers=self.headers) as client:
                search_url = f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={target}&language=en&format=json"
                r = await client.get(search_url)
                if r.status_code != 200:
                    return []

                hits = r.json().get("search", [])
                comp_id = None
                for h in hits:
                    desc = (h.get("description") or "").lower()
                    if any(w in desc for w in ["company", "software", "startup", "cloud", "technology", "platform", "business"]):
                        comp_id = h["id"]
                        break

                if not comp_id and hits:
                    comp_id = hits[0]["id"]

                if not comp_id:
                    return []

                # Fetch claims for entity
                entity_url = f"https://www.wikidata.org/wiki/Special:EntityData/{comp_id}.json"
                e_res = await client.get(entity_url)
                if e_res.status_code != 200:
                    return []

                entity = e_res.json().get("entities", {}).get(comp_id, {})
                claims = entity.get("claims", {})

                # P1056: product or material produced
                products = []
                prod_claims = claims.get("P1056", [])
                for p in prod_claims[:4]:
                    p_val = p.get("mainsnak", {}).get("datavalue", {}).get("value", {})
                    if isinstance(p_val, dict) and "id" in p_val:
                        p_qid = p_val["id"]
                        p_r = await client.get(f"https://www.wikidata.org/wiki/Special:EntityData/{p_qid}.json")
                        if p_r.status_code == 200:
                            label = p_r.json().get("entities", {}).get(p_qid, {}).get("labels", {}).get("en", {}).get("value")
                            if label and label not in products:
                                products.append(label)

                return products
        except Exception as e:
            logger.debug(f"Wikidata probing skipped for {target}: {e}")
            return []

    def _empty_result(self, target: str, message: str = "No IP records located") -> Dict[str, Any]:
        return {
            "target": target,
            "audited_name": target,
            "has_ip_moat": False,
            "ip_moat_score": 0,
            "ip_defensibility_verdict": "Open Source Brand Commons / Unregistered Mark (No USPTO Records Found)",
            "ip_moat_color": "zinc",
            "brand_protection_status": "Unregistered Brand Commons",
            "ip_summary": "No federal trademarks or patents detected",
            "total_trademarks": 0,
            "total_patents": 0,
            "trademarks": [],
            "patents": [],
            "registered_products": [],
            "message": message
        }

ip_intel_service = IPIntelService()
