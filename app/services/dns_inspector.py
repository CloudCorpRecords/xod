import logging
import httpx
import re
from typing import Dict, Any, List, Optional

logger = logging.getLogger("dns_inspector")

class DNSInspectorService:
    """
    Performs zero-dependency DNS over HTTPS (DoH) inspection to evaluate:
    1. Mail server host & enterprise infrastructure (Google Workspace, Microsoft 365).
    2. Email security & anti-phishing posture (SPF, DMARC).
    3. SaaS vendor domain verifications (Stripe, Vercel, Atlassian, Google).
    4. Primary nameserver provider (Cloudflare, AWS Route 53).
    """

    def __init__(self):
        self.doh_url = "https://cloudflare-dns.com/dns-query"
        self.headers = {"Accept": "application/dns-json"}

    async def inspect(self, domain: str) -> Dict[str, Any]:
        """
        Executes DoH queries for MX, TXT, DMARC, and NS records.
        """
        clean_domain = self._clean_domain(domain)
        if not clean_domain:
            return {"error": "Invalid domain supplied"}

        results: Dict[str, Any] = {
            "domain": clean_domain,
            "email_provider": "Unknown / Self-hosted",
            "nameservers": [],
            "spf_configured": False,
            "spf_record": None,
            "dmarc_configured": False,
            "dmarc_policy": "none",
            "saas_verifications": [],
            "enterprise_email_security_score": 0,
            "readiness_verdict": "Low / Unconfigured"
        }

        async with httpx.AsyncClient(timeout=6.0, headers=self.headers) as client:
            # 1. Query MX records
            mx_records = await self._query_doh(client, clean_domain, "MX")
            email_host = self._detect_email_host(mx_records)
            results["email_provider"] = email_host

            # 2. Query NS records
            ns_records = await self._query_doh(client, clean_domain, "NS")
            results["nameservers"] = [r.get("data", "").rstrip(".") for r in ns_records if r.get("data")]

            # 3. Query TXT records (SPF and SaaS Verifications)
            txt_records = await self._query_doh(client, clean_domain, "TXT")
            for t in txt_records:
                data_str = t.get("data", "").strip('"')
                if data_str.startswith("v=spf1"):
                    results["spf_configured"] = True
                    results["spf_record"] = data_str
                
                # Check for SaaS vendor verification signatures
                verif = self._detect_saas_verification(data_str)
                if verif and verif not in results["saas_verifications"]:
                    results["saas_verifications"].append(verif)

            # 4. Query _dmarc record
            dmarc_records = await self._query_doh(client, f"_dmarc.{clean_domain}", "TXT")
            for t in dmarc_records:
                data_str = t.get("data", "").strip('"')
                if "v=DMARC1" in data_str:
                    results["dmarc_configured"] = True
                    policy_match = re.search(r"p=([a-zA-Z]+)", data_str)
                    if policy_match:
                        results["dmarc_policy"] = policy_match.group(1).lower()

        # 5. Compute Enterprise Security & Infrastructure Score (0-100)
        score = 0
        if results["spf_configured"]:
            score += 30
        if results["dmarc_configured"]:
            score += 30
            if results["dmarc_policy"] in ("quarantine", "reject"):
                score += 10  # Enforced anti-spoofing policy
        if results["email_provider"] in ("Google Workspace", "Microsoft 365 / Exchange"):
            score += 20
        elif results["email_provider"] not in ("Unknown / Self-hosted", "No MX Records Found"):
            score += 10
        if results["saas_verifications"]:
            score += 10

        results["enterprise_email_security_score"] = min(100, score)

        if score >= 80:
            results["readiness_verdict"] = "Enterprise Ready (Hardened SPF/DMARC & Corporate Mail)"
        elif score >= 50:
            results["readiness_verdict"] = "Moderate Posture (Basic Mail Auth Configured)"
        else:
            results["readiness_verdict"] = "Vulnerable / Unhardened (Missing DMARC or SPF)"

        return results

    async def _query_doh(self, client: httpx.AsyncClient, name: str, record_type: str) -> List[Dict[str, Any]]:
        try:
            resp = await client.get(self.doh_url, params={"name": name, "type": record_type})
            if resp.status_code == 200:
                data = resp.json()
                return data.get("Answer", [])
        except Exception as e:
            logger.debug(f"DoH query for {name} ({record_type}) failed: {e}")
        return []

    def _detect_email_host(self, mx_answers: List[Dict[str, Any]]) -> str:
        if not mx_answers:
            return "No MX Records Found"
        all_hosts = " ".join([a.get("data", "").lower() for a in mx_answers])

        if "google" in all_hosts or "aspmx" in all_hosts or "googlemail" in all_hosts:
            return "Google Workspace"
        elif "outlook" in all_hosts or "microsoft" in all_hosts:
            return "Microsoft 365 / Exchange"
        elif "fastmail" in all_hosts:
            return "Fastmail"
        elif "protonmail" in all_hosts:
            return "ProtonMail"
        elif "zoho" in all_hosts:
            return "Zoho Mail"
        elif "improvmx" in all_hosts:
            return "ImprovMX (Forwarding)"
        elif "migadu" in all_hosts:
            return "Migadu"
        return "Custom / Self-Hosted Mail Server"

    def _detect_saas_verification(self, txt: str) -> Optional[str]:
        txt_lower = txt.lower()
        if "stripe-verification" in txt_lower:
            return "Stripe Domain Verification"
        if "google-site-verification" in txt_lower:
            return "Google Search Console"
        if "vercel" in txt_lower:
            return "Vercel Cloud Verification"
        if "atlassian-domain-verification" in txt_lower:
            return "Atlassian SSO"
        if "postmark" in txt_lower:
            return "Postmark Transactional Mail"
        if "sendgrid" in txt_lower:
            return "SendGrid Email Deliverability"
        if "apple-domain-verification" in txt_lower:
            return "Apple Pay / Domain Auth"
        if "facebook-domain-verification" in txt_lower:
            return "Meta / Facebook Business Auth"
        return None

    def _clean_domain(self, target: str) -> str:
        target = target.strip()
        target = re.sub(r"^https?://", "", target, flags=re.I)
        target = target.split("/")[0]
        target = target.split(":")[0]
        return target.lower()

dns_inspector_service = DNSInspectorService()
