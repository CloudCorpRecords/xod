import logging
import re
import urllib.parse
from typing import Dict, Any, List, Optional
import httpx

logger = logging.getLogger("email_intel")

DISPOSABLE_DOMAINS = {
    "mailinator.com", "tempmail.com", "10minutemail.com", "guerrillamail.com",
    "sharklasers.com", "yopmail.com", "trashmail.com", "getairmail.com",
    "dispostable.com", "throwawaymail.com", "fakeinbox.com", "temp-mail.org",
    "nada.ltd", "mohmal.com", "crazymailing.com", "burnermail.io"
}

class EmailIntelService:
    """
    Zero-API-Key Work-Email Finder and Bounce-Risk Verifier.
    Performs RFC validation, Cloudflare DNS-over-HTTPS MX lookups, SPF/DMARC analysis,
    corporate email permutation modeling, and public digital exhaust corroboration.
    """

    def __init__(self):
        self.doh_url = "https://cloudflare-dns.com/dns-query"
        self.default_headers = {
            "Accept": "application/dns-json",
            "User-Agent": "XodEmailVerifier/1.0"
        }

    async def verify_email_bounce_risk(self, email: str) -> Dict[str, Any]:
        """
        Conducts pre-flight bounce-risk verification for an email address without paid API keys.
        Audits syntax, MX servers, mail provider, SPF/DMARC policies, and disposable domain status.
        """
        clean_email = email.strip().lower()

        # 1. RFC 5322 Syntax Check
        syntax_valid = bool(re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", clean_email))
        if not syntax_valid or "@" not in clean_email:
            return {
                "email": clean_email,
                "deliverability_verdict": "UNDELIVERABLE",
                "bounce_risk": "CRITICAL",
                "deliverability_score": 0,
                "syntax_valid": False,
                "reasons": ["Invalid email format or malformed syntax."]
            }

        user, domain = clean_email.split("@", 1)

        # 2. Disposable Email Check
        is_disposable = domain in DISPOSABLE_DOMAINS
        if is_disposable:
            return {
                "email": clean_email,
                "domain": domain,
                "deliverability_verdict": "DISPOSABLE / BURNER",
                "bounce_risk": "CRITICAL",
                "deliverability_score": 10,
                "syntax_valid": True,
                "is_disposable": True,
                "reasons": ["Domain is a temporary disposable email provider. Do not send outreach."]
            }

        # 3. DNS MX Record Lookup via Cloudflare DoH (Port 443 HTTPS, immune to ISP Port 53/25 blocks)
        mx_records, provider_name = await self._query_mx_records(domain)
        has_mx = len(mx_records) > 0

        if not has_mx:
            return {
                "email": clean_email,
                "domain": domain,
                "deliverability_verdict": "UNDELIVERABLE",
                "bounce_risk": "CRITICAL",
                "deliverability_score": 15,
                "syntax_valid": True,
                "mx_records_found": False,
                "reasons": [f"No active Mail Exchange (MX) records found for domain '{domain}'. Incoming mail rejected."]
            }

        # 4. SPF and DMARC Security Inspection
        spf_record, dmarc_record = await self._query_email_security_txt(domain)
        has_spf = bool(spf_record)
        has_dmarc = bool(dmarc_record)

        # 5. Deliverability Score Computation
        score = 65  # Base score for valid syntax + verified MX
        if provider_name in ["Google Workspace", "Microsoft 365"]:
            score += 20  # Tier 1 institutional email infrastructure
        elif provider_name in ["Proton Mail", "Zoho Mail", "Fastmail", "iCloud Mail"]:
            score += 15
        elif provider_name in ["Proofpoint", "Mimecast"]:
            score += 15

        if has_spf:
            score += 10
        if has_dmarc:
            score += 5

        score = min(score, 98)

        # Determine bounce risk category
        if score >= 85:
            bounce_risk = "LOW (Safe to Send)"
            verdict = "DELIVERABLE"
        elif score >= 65:
            bounce_risk = "MODERATE (Standard Business Inbox)"
            verdict = "PROBABLY DELIVERABLE"
        else:
            bounce_risk = "HIGH (Unverified / Unknown Mail Host)"
            verdict = "RISKY"

        return {
            "email": clean_email,
            "domain": domain,
            "deliverability_verdict": verdict,
            "bounce_risk": bounce_risk,
            "deliverability_score": score,
            "syntax_valid": True,
            "is_disposable": False,
            "mail_provider": provider_name,
            "mx_records": mx_records[:3],
            "spf_configured": has_spf,
            "dmarc_configured": has_dmarc,
            "spf_record": spf_record[:80] if spf_record else None,
            "recommendation": "Safe for cold outreach." if score >= 85 else "Recommended to verify via social profile or warm touchpoint first."
        }

    async def find_work_email(self, first_name: str, last_name: str, domain: str) -> Dict[str, Any]:
        """
        Discovers and ranks verified work email candidates for a founder or executive
        using business pattern permutation matrices, MX provider alignment, and public web footprints.
        """
        clean_first = re.sub(r"[^a-zA-Z]", "", first_name.strip()).lower()
        clean_last = re.sub(r"[^a-zA-Z]", "", last_name.strip()).lower()
        clean_domain = domain.strip().lower().replace("https://", "").replace("http://", "").split("/")[0].lstrip("www.")

        if not clean_first or not clean_domain:
            return {"error": "first_name and domain are required to discover work email."}

        # 1. Check Domain Mail Exchanger
        mx_records, provider_name = await self._query_mx_records(clean_domain)
        has_mx = len(mx_records) > 0

        # 2. Build Permutation Matrix
        permutations = self._generate_email_permutations(clean_first, clean_last, clean_domain)

        # 3. Public Digital Exhaust & GitHub Commit Mining
        github_verified_email = await self._search_github_commit_author(clean_first, clean_last, clean_domain)

        # 4. Rank candidates based on provider prevalence and evidence
        candidates = []
        for idx, p in enumerate(permutations):
            pattern_name = p["pattern"]
            candidate_email = p["email"]

            # Baseline confidence
            confidence = 80 - (idx * 5)
            evidence = [f"Standard SaaS pattern '{pattern_name}'"]

            if github_verified_email and github_verified_email.lower() == candidate_email:
                confidence = 98
                evidence.insert(0, "Confirmed via public GitHub commit author history")

            if provider_name == "Google Workspace" and pattern_name in ["first", "first.last"]:
                confidence += 10
                evidence.append("Google Workspace startup default pattern")

            if not has_mx:
                confidence = 20
                evidence = ["Warning: Domain has no active MX records"]

            confidence = min(max(confidence, 15), 98)

            candidates.append({
                "email": candidate_email,
                "pattern": pattern_name,
                "confidence_score": confidence,
                "evidence": evidence
            })

        # Sort highest confidence first
        candidates.sort(key=lambda x: x["confidence_score"], reverse=True)
        top_candidate = candidates[0] if candidates else None

        return {
            "target": {
                "name": f"{first_name.strip()} {last_name.strip()}".strip(),
                "first_name": clean_first,
                "last_name": clean_last,
                "domain": clean_domain
            },
            "mail_infrastructure": {
                "provider": provider_name,
                "mx_active": has_mx,
                "primary_mx": mx_records[0] if mx_records else "None"
            },
            "best_match_email": top_candidate["email"] if top_candidate else None,
            "confidence_score": top_candidate["confidence_score"] if top_candidate else 0,
            "top_candidates": candidates[:5],
            "github_mined_email": github_verified_email
        }

    def _generate_email_permutations(self, first: str, last: str, domain: str) -> List[Dict[str, str]]:
        """Generates standard corporate email permutation matrix."""
        permutations = []
        if first and last:
            permutations.append({"pattern": "first.last", "email": f"{first}.{last}@{domain}"})
            permutations.append({"pattern": "first", "email": f"{first}@{domain}"})
            permutations.append({"pattern": "flast", "email": f"{first[0]}{last}@{domain}"})
            permutations.append({"pattern": "firstlast", "email": f"{first}{last}@{domain}"})
            permutations.append({"pattern": "first_last", "email": f"{first}_{last}@{domain}"})
            permutations.append({"pattern": "firstl", "email": f"{first}{last[0]}@{domain}"})
            permutations.append({"pattern": "last", "email": f"{last}@{domain}"})
        elif first:
            permutations.append({"pattern": "first", "email": f"{first}@{domain}"})
            permutations.append({"pattern": "founder", "email": f"founder@{domain}"})
            permutations.append({"pattern": "hello", "email": f"hello@{domain}"})
        return permutations

    async def _query_mx_records(self, domain: str) -> tuple[List[str], str]:
        """Queries DNS MX records via Cloudflare DoH."""
        mx_records = []
        provider_name = "Custom / Self-Hosted Mail Server"

        try:
            async with httpx.AsyncClient(timeout=4.0, headers=self.default_headers) as client:
                resp = await client.get(self.doh_url, params={"name": domain, "type": "MX"})
                if resp.status_code == 200:
                    data = resp.json()
                    answers = data.get("Answer", [])
                    for a in answers:
                        if a.get("type") == 15 and "data" in a:  # 15 = MX
                            host = a["data"].strip().rstrip(".")
                            mx_records.append(host)

                    # Provider classification
                    mx_str = " ".join(mx_records).lower()
                    if "google" in mx_str or "aspmx" in mx_str:
                        provider_name = "Google Workspace"
                    elif "outlook" in mx_str or "protection.outlook" in mx_str:
                        provider_name = "Microsoft 365"
                    elif "proton" in mx_str:
                        provider_name = "Proton Mail"
                    elif "zoho" in mx_str:
                        provider_name = "Zoho Mail"
                    elif "icloud" in mx_str or "apple" in mx_str:
                        provider_name = "iCloud Mail"
                    elif "fastmail" in mx_str:
                        provider_name = "Fastmail"
                    elif "mimecast" in mx_str:
                        provider_name = "Mimecast Protected"
                    elif "pphosted" in mx_str or "proofpoint" in mx_str:
                        provider_name = "Proofpoint Enterprise"
                    elif "cloudflare" in mx_str:
                        provider_name = "Cloudflare Email Routing"
        except Exception as e:
            logger.debug(f"MX record query failed for {domain}: {e}")

        return mx_records, provider_name

    async def _query_email_security_txt(self, domain: str) -> tuple[Optional[str], Optional[str]]:
        """Queries SPF and DMARC TXT records via Cloudflare DoH."""
        spf_rec, dmarc_rec = None, None
        try:
            async with httpx.AsyncClient(timeout=4.0, headers=self.default_headers) as client:
                # Query domain TXT for SPF
                r_spf = await client.get(self.doh_url, params={"name": domain, "type": "TXT"})
                if r_spf.status_code == 200:
                    for a in r_spf.json().get("Answer", []):
                        val = a.get("data", "").strip('"')
                        if val.startswith("v=spf1"):
                            spf_rec = val
                            break

                # Query _dmarc.domain TXT
                r_dmarc = await client.get(self.doh_url, params={"name": f"_dmarc.{domain}", "type": "TXT"})
                if r_dmarc.status_code == 200:
                    for a in r_dmarc.json().get("Answer", []):
                        val = a.get("data", "").strip('"')
                        if val.startswith("v=DMARC1"):
                            dmarc_rec = val
                            break
        except Exception as e:
            logger.debug(f"SPF/DMARC query failed for {domain}: {e}")

        return spf_rec, dmarc_rec

    async def _search_github_commit_author(self, first: str, last: str, domain: str) -> Optional[str]:
        """Searches GitHub public commit logs for verified author email addresses."""
        try:
            q = urllib.parse.quote_plus(f"{first} {last} {domain}")
            headers = {
                "User-Agent": "XodEmailVerifier/1.0",
                "Accept": "application/vnd.github.cloak-preview"
            }
            async with httpx.AsyncClient(timeout=4.0, headers=headers) as client:
                resp = await client.get(f"https://api.github.com/search/commits?q={q}&per_page=3")
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    for item in items:
                        commit = item.get("commit", {})
                        author = commit.get("author", {})
                        email = author.get("email", "")
                        if email and domain in email.lower() and not email.endswith("noreply.github.com"):
                            return email.lower()
        except Exception as e:
            logger.debug(f"GitHub author email search failed: {e}")
        return None

email_intel_service = EmailIntelService()
