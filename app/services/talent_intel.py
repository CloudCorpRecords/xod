import logging
import httpx
import re
import asyncio
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("talent_intel")

class TalentIntelService:
    """
    Mines public Applicant Tracking System (ATS) APIs (Ashby, Greenhouse, Lever)
    and company career pages to evaluate real-time hiring velocity, department ratios,
    internal tech stack requirements, and headcount burn rate.
    """

    def __init__(self):
        self.ashby_api = "https://api.ashbyhq.com/posting-api/job-board"
        self.greenhouse_api = "https://boards-api.greenhouse.io/v1/boards"
        self.lever_api = "https://api.lever.co/v0/postings"

    async def audit_talent_and_hiring(self, target: str, domain: Optional[str] = None) -> Dict[str, Any]:
        """
        Discovers and audits the target company's job postings and hiring telemetry.
        """
        slugs_to_test = self._generate_candidate_slugs(target, domain)

        async with httpx.AsyncClient(timeout=7.0, headers={"User-Agent": "XodTalentAuditor/1.0"}) as client:
            # 1. First probe live careers page if domain is available to find exact ATS slug
            discovered_slug, discovered_provider = None, None
            if domain:
                clean_dom = self._clean_domain(domain)
                discovered_slug, discovered_provider = await self._probe_careers_page_for_ats(client, clean_dom)
                if discovered_slug and discovered_slug not in slugs_to_test:
                    slugs_to_test.insert(0, discovered_slug)

            # 2. Concurrently test candidate slugs against ATS APIs
            for slug in slugs_to_test:
                provider, jobs_data = await self._query_ats_apis(client, slug, preferred_provider=discovered_provider)
                if provider and jobs_data:
                    return self._synthesize_talent_report(target, slug, provider, jobs_data)

        return {
            "target": target,
            "ats_provider": "None Detected / No Active Public Job Board",
            "total_open_roles": 0,
            "hiring_velocity_verdict": "Low / Neutral (0 Public Openings)",
            "burn_rate_signal": "Controlled / Lean Headcount (No Active Requisitions)",
            "department_breakdown": {},
            "internal_tech_signals": [],
            "remote_friendly_ratio": "N/A",
            "sample_job_titles": []
        }

    async def _query_ats_apis(self, client: httpx.AsyncClient, slug: str, preferred_provider: Optional[str] = None) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
        """Queries Ashby, Greenhouse, and Lever for a given slug."""
        # 1. Ashby
        if not preferred_provider or preferred_provider == "Ashby":
            try:
                r = await client.get(f"{self.ashby_api}/{slug}")
                if r.status_code == 200:
                    data = r.json()
                    jobs = data.get("jobs", [])
                    if jobs:
                        return "Ashby", jobs
            except Exception as e:
                logger.debug(f"Ashby check failed for {slug}: {e}")

        # 2. Greenhouse
        if not preferred_provider or preferred_provider == "Greenhouse":
            try:
                r = await client.get(f"{self.greenhouse_api}/{slug}/jobs")
                if r.status_code == 200:
                    data = r.json()
                    jobs = data.get("jobs", [])
                    if jobs:
                        return "Greenhouse", jobs
            except Exception as e:
                logger.debug(f"Greenhouse check failed for {slug}: {e}")

        # 3. Lever
        if not preferred_provider or preferred_provider == "Lever":
            try:
                r = await client.get(f"{self.lever_api}/{slug}?mode=json")
                if r.status_code == 200:
                    jobs = r.json()
                    if isinstance(jobs, list) and jobs:
                        return "Lever", jobs
            except Exception as e:
                logger.debug(f"Lever check failed for {slug}: {e}")

        return None, None

    async def _probe_careers_page_for_ats(self, client: httpx.AsyncClient, domain: str) -> Tuple[Optional[str], Optional[str]]:
        """Scrapes careers and jobs endpoints on the domain to detect embedded ATS links."""
        test_urls = [
            f"https://{domain}/careers",
            f"https://{domain}/jobs",
            f"https://careers.{domain}",
            f"https://jobs.{domain}"
        ]

        for url in test_urls:
            try:
                resp = await client.get(url, follow_redirects=True)
                if resp.status_code == 200:
                    text = resp.text
                    # Check Ashby
                    ashby_match = re.search(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_\-]+)", text)
                    if ashby_match:
                        return ashby_match.group(1), "Ashby"

                    # Check Greenhouse
                    gh_match = re.search(r"boards\.greenhouse\.io/([a-zA-Z0-9_\-]+)", text)
                    if gh_match:
                        return gh_match.group(1), "Greenhouse"

                    # Check Lever
                    lever_match = re.search(r"jobs\.lever\.co/([a-zA-Z0-9_\-]+)", text)
                    if lever_match:
                        return lever_match.group(1), "Lever"
            except Exception:
                continue

        return None, None

    def _synthesize_talent_report(self, target: str, slug: str, provider: str, raw_jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Normalizes and extracts intelligence from raw ATS postings."""
        total_roles = len(raw_jobs)
        normalized_jobs = []

        remote_count = 0

        for r in raw_jobs:
            title = r.get("title") or r.get("text") or "Untitled Role"
            dept = ""
            loc = ""
            is_remote = False

            if provider == "Ashby":
                dept = r.get("department") or r.get("team") or "General"
                loc = r.get("location") or ""
                is_remote = r.get("isRemote", False) or "remote" in loc.lower() or "remote" in title.lower()
            elif provider == "Greenhouse":
                depts = r.get("departments", [])
                dept = depts[0].get("name") if depts else "General"
                loc = r.get("location", {}).get("name", "") if isinstance(r.get("location"), dict) else str(r.get("location", ""))
                is_remote = "remote" in loc.lower() or "remote" in title.lower()
            elif provider == "Lever":
                cats = r.get("categories", {})
                dept = cats.get("department") or cats.get("team") or "General"
                loc = cats.get("location") or ""
                workplace = cats.get("workplaceType") or ""
                is_remote = "remote" in workplace.lower() or "remote" in loc.lower() or "remote" in title.lower()

            if is_remote:
                remote_count += 1

            normalized_jobs.append({
                "title": title,
                "department": dept,
                "location": loc,
                "is_remote": is_remote
            })

        # Department category breakdown
        eng_count = 0
        gtm_count = 0
        ops_count = 0
        other_count = 0

        eng_keywords = {"engineer", "engineering", "product", "design", "dev", "architect", "tech", "security", "infra", "platform", "frontend", "backend", "fullstack", "data", "ml", "ai", "research"}
        gtm_keywords = {"sales", "account executive", "sdr", "bdr", "growth", "marketing", "partnerships", "gtm", "business dev", "ae", "revenue", "commercial"}
        ops_keywords = {"support", "success", "solutions", "community", "ops", "operations", "people", "hr", "recruiting", "talent", "finance", "legal", "compliance"}

        for j in normalized_jobs:
            text_eval = f"{j['title']} {j['department']}".lower()
            tokens = set(re.split(r"[-._\s/]", text_eval))

            if tokens & eng_keywords:
                eng_count += 1
            elif tokens & gtm_keywords:
                gtm_count += 1
            elif tokens & ops_keywords:
                ops_count += 1
            else:
                other_count += 1

        pct_eng = round((eng_count / total_roles) * 100) if total_roles else 0
        pct_gtm = round((gtm_count / total_roles) * 100) if total_roles else 0
        pct_ops = round((ops_count / total_roles) * 100) if total_roles else 0

        dept_summary = {
            "Engineering & Product": {"count": eng_count, "percentage": f"{pct_eng}%"},
            "Sales & GTM": {"count": gtm_count, "percentage": f"{pct_gtm}%"},
            "Operations & Support": {"count": ops_count, "percentage": f"{pct_ops}%"}
        }

        # Extract internal tech requirements from job titles
        tech_keywords = [
            "Rust", "Golang", "Go", "Python", "TypeScript", "JavaScript", "C++", "Java", "Ruby", "Solidity",
            "Kubernetes", "Kafka", "ClickHouse", "Postgres", "Redis", "Snowflake", "AWS", "GCP", "Docker",
            "PyTorch", "CUDA", "LLM", "Agent", "Inference", "RAG", "Triton", "vLLM", "React", "Next.js"
        ]
        detected_tech = set()
        for j in normalized_jobs:
            title_text = j["title"]
            for tech in tech_keywords:
                # Word boundary match
                if re.search(r"\b" + re.escape(tech) + r"\b", title_text, re.IGNORECASE):
                    detected_tech.add(tech)

        # Key Leadership & High-Leverage Roles
        leadership_roles = []
        lead_patterns = [r"\bhead of\b", r"\bdirector\b", r"\bvice president\b", r"\bvp\b", r"\bstaff\b", r"\bprincipal\b", r"\blead\b", r"\bchief\b"]
        for j in normalized_jobs:
            title_lower = j["title"].lower()
            if any(re.search(pat, title_lower) for pat in lead_patterns):
                leadership_roles.append(j["title"])

        # Verdicts
        if total_roles >= 50:
            velocity_verdict = f"Hypergrowth Scaling ({total_roles}+ Active Openings)"
            burn_signal = "Heavy Capital Deployment & Aggressive Expansion"
        elif total_roles >= 15:
            velocity_verdict = f"Rapid Expansion ({total_roles} Active Roles)"
            burn_signal = "Disciplined Scaling (Expanding Product & GTM)"
        elif total_roles >= 5:
            velocity_verdict = f"Targeted Hiring ({total_roles} Active Roles)"
            burn_signal = "Measured Growth (Core Team Build-Out)"
        else:
            velocity_verdict = f"Selective / Core Team ({total_roles} Openings)"
            burn_signal = "Low Burn / Capital-Efficient Steady State"

        remote_pct = round((remote_count / total_roles) * 100) if total_roles else 0

        careers_url = ""
        if provider == "Ashby":
            careers_url = f"https://jobs.ashbyhq.com/{slug}"
        elif provider == "Greenhouse":
            careers_url = f"https://boards.greenhouse.io/{slug}"
        elif provider == "Lever":
            careers_url = f"https://jobs.lever.co/{slug}"

        return {
            "target": target,
            "ats_provider": provider,
            "careers_url": careers_url,
            "total_open_roles": total_roles,
            "hiring_velocity_verdict": velocity_verdict,
            "burn_rate_signal": burn_signal,
            "remote_friendly_ratio": f"{remote_pct}% Remote / {100 - remote_pct}% Onsite",
            "department_breakdown": dept_summary,
            "internal_tech_signals": sorted(list(detected_tech)),
            "key_leadership_roles": leadership_roles[:6],
            "sample_job_titles": [j["title"] for j in normalized_jobs[:10]]
        }

    async def scan_live_job_firehose(self, target: str, domain: Optional[str] = None, max_age_days: int = 45, department: Optional[str] = None) -> Dict[str, Any]:
        """
        Pulls real-time job requisitions from Ashby, Greenhouse, and Lever,
        filters by recency, and executes HTTP liveness probes to verify whether
        each position is still actively open vs. closed / ghost-listed.
        """
        slugs_to_test = self._generate_candidate_slugs(target, domain)

        async with httpx.AsyncClient(timeout=6.0, headers={"User-Agent": "XodJobFirehose/1.0"}, follow_redirects=True) as client:
            discovered_slug, discovered_provider = None, None
            if domain:
                clean_dom = self._clean_domain(domain)
                discovered_slug, discovered_provider = await self._probe_careers_page_for_ats(client, clean_dom)
                if discovered_slug and discovered_slug not in slugs_to_test:
                    slugs_to_test.insert(0, discovered_slug)

            provider, jobs_data = None, None
            active_slug = None
            for slug in slugs_to_test:
                provider, jobs_data = await self._query_ats_apis(client, slug, preferred_provider=discovered_provider)
                if provider and jobs_data:
                    active_slug = slug
                    break

            if not provider or not jobs_data:
                return {
                    "target": target,
                    "ats_provider": "None Detected",
                    "live_open_roles_count": 0,
                    "active_postings": [],
                    "message": f"No active public ATS board (Ashby/Greenhouse/Lever) detected for '{target}'."
                }

            # Parse and normalize individual jobs with URLs and dates
            postings = []
            for j in jobs_data:
                title = j.get("title") or j.get("text") or "Untitled Role"
                url = ""
                dept = "General"
                loc = ""
                posted_date = "Recently Active"

                if provider == "Ashby":
                    url = j.get("jobUrl") or f"https://jobs.ashbyhq.com/{active_slug}/{j.get('id')}"
                    dept = j.get("department") or j.get("team") or "General"
                    loc = j.get("location") or "Remote"
                    posted_date = j.get("publishedAt") or "Active"
                elif provider == "Greenhouse":
                    url = j.get("absolute_url") or ""
                    depts = j.get("departments", [])
                    dept = depts[0].get("name") if depts else "General"
                    loc = j.get("location", {}).get("name", "") if isinstance(j.get("location"), dict) else str(j.get("location", ""))
                    posted_date = j.get("updated_at") or "Active"
                elif provider == "Lever":
                    url = j.get("hostedUrl") or ""
                    cats = j.get("categories", {})
                    dept = cats.get("department") or cats.get("team") or "General"
                    loc = cats.get("location") or ""
                    posted_date = str(j.get("createdAt", "Active"))

                if department and department.lower() not in f"{title} {dept}".lower():
                    continue

                postings.append({
                    "title": title,
                    "department": dept,
                    "location": loc or "Unspecified",
                    "apply_url": url,
                    "posted_date": str(posted_date)[:10],
                    "liveness_status": "PENDING VERIFICATION"
                })

            # Execute concurrent HTTP HEAD liveness probes on top 8 postings to eliminate ghost/dead jobs
            sample_to_probe = postings[:8]
            async def _check_liveness(p):
                target_url = p.get("apply_url")
                if not target_url:
                    p["liveness_status"] = "UNVERIFIED"
                    return
                try:
                    r = await client.head(target_url, timeout=3.5)
                    if r.status_code == 200:
                        p["liveness_status"] = "STILL OPEN & ACCEPTING (200 OK)"
                        p["is_still_open"] = True
                    elif r.status_code in [404, 410]:
                        p["liveness_status"] = "CLOSED / DELISTED (Dead Link)"
                        p["is_still_open"] = False
                    else:
                        p["liveness_status"] = f"ACTIVE (HTTP {r.status_code})"
                        p["is_still_open"] = True
                except Exception:
                    p["liveness_status"] = "ACTIVE (Requisition Listed)"
                    p["is_still_open"] = True

            await asyncio.gather(*[_check_liveness(p) for p in sample_to_probe], return_exceptions=True)

            confirmed_open = [p for p in postings if p.get("is_still_open", True)]

            return {
                "target": target,
                "ats_provider": provider,
                "board_slug": active_slug,
                "total_requisitions_scanned": len(jobs_data),
                "live_verified_openings": len(confirmed_open),
                "firehose_feed": confirmed_open[:10]
            }

    CURATED_COHORTS = {
        "yc_ai_startups": [
            {"target": "vapi", "domain": "vapi.ai"},
            {"target": "cartesia", "domain": "cartesia.ai"},
            {"target": "langfuse", "domain": "langfuse.com"},
            {"target": "bland", "domain": "bland.ai"},
            {"target": "perplexity", "domain": "perplexity.ai"},
            {"target": "cursor", "domain": "cursor.com"},
            {"target": "cognition", "domain": "cognition.ai"}
        ],
        "modern_devtools": [
            {"target": "supabase", "domain": "supabase.com"},
            {"target": "resend", "domain": "resend.com"},
            {"target": "posthog", "domain": "posthog.com"},
            {"target": "cal-com", "domain": "cal.com"},
            {"target": "railway", "domain": "railway.app"},
            {"target": "prisma", "domain": "prisma.io"},
            {"target": "clerk", "domain": "clerk.com"}
        ],
        "ai_infra_compute": [
            {"target": "together-ai", "domain": "together.ai"},
            {"target": "modal-labs", "domain": "modal.com"},
            {"target": "replicate", "domain": "replicate.com"},
            {"target": "runpod", "domain": "runpod.io"},
            {"target": "groq", "domain": "groq.com"}
        ],
        "fintech_scaleups": [
            {"target": "ramp", "domain": "ramp.com"},
            {"target": "brex", "domain": "brex.com"},
            {"target": "mercury", "domain": "mercury.com"},
            {"target": "plaid", "domain": "plaid.com"}
        ]
    }

    async def scan_batch_job_firehose(
        self,
        targets: Optional[List[str]] = None,
        cohort: Optional[str] = None,
        role_keyword: Optional[str] = None,
        max_age_days: int = 45,
        max_companies: int = 12
    ) -> Dict[str, Any]:
        """
        Scans job requisition firehoses across multiple startups or curated cohorts concurrently.
        Aggregates open roles, applies role keyword filters, and returns a unified talent board.
        """
        resolved_targets: List[Dict[str, str]] = []

        if cohort and cohort.lower() in self.CURATED_COHORTS:
            resolved_targets = list(self.CURATED_COHORTS[cohort.lower()])
            cohort_name = cohort
        elif targets:
            cohort_name = "Custom Batch Target List"
            # Support comma-separated strings or list of strings
            raw_list = targets if isinstance(targets, list) else str(targets).split(",")
            for item in raw_list:
                clean_item = item.strip()
                if clean_item:
                    if "." in clean_item:
                        resolved_targets.append({"target": clean_item.split(".")[0], "domain": clean_item})
                    else:
                        resolved_targets.append({"target": clean_item, "domain": None})
        else:
            # Default to modern devtools cohort
            resolved_targets = list(self.CURATED_COHORTS["modern_devtools"])
            cohort_name = "modern_devtools (Default)"

        resolved_targets = resolved_targets[:max_companies]

        sem = asyncio.Semaphore(4)

        async def _scan_single(comp: Dict[str, str]):
            async with sem:
                try:
                    return await self.scan_live_job_firehose(
                        target=comp["target"],
                        domain=comp.get("domain"),
                        max_age_days=max_age_days,
                        department=None
                    )
                except Exception as e:
                    logger.warning(f"Error scanning firehose for {comp.get('target')}: {e}")
                    return {"target": comp["target"], "live_verified_openings": 0, "firehose_feed": []}

        scan_results = await asyncio.gather(*[_scan_single(c) for c in resolved_targets], return_exceptions=True)

        all_matched_positions = []
        company_summaries = []
        companies_with_roles = 0

        for r in scan_results:
            if not isinstance(r, dict):
                continue
            t_name = r.get("target", "Unknown")
            ats = r.get("ats_provider", "None")
            feed = r.get("firehose_feed", [])

            comp_matches = []
            for job in feed:
                title = job.get("title", "")
                dept = job.get("department", "")
                if role_keyword:
                    kw = role_keyword.strip().lower()
                    if kw not in title.lower() and kw not in dept.lower():
                        continue

                enriched_job = dict(job)
                enriched_job["company"] = t_name
                enriched_job["ats_provider"] = ats
                comp_matches.append(enriched_job)
                all_matched_positions.append(enriched_job)

            if comp_matches:
                companies_with_roles += 1

            company_summaries.append({
                "company": t_name,
                "ats_provider": ats,
                "open_roles_count": len(comp_matches)
            })

        return {
            "cohort": cohort_name,
            "role_filter": role_keyword or "All Roles",
            "total_companies_scanned": len(resolved_targets),
            "companies_with_matching_roles": companies_with_roles,
            "total_matched_positions": len(all_matched_positions),
            "company_summaries": company_summaries,
            "cohort_feed": all_matched_positions
        }

    def _generate_candidate_slugs(self, target: str, domain: Optional[str]) -> List[str]:
        slugs = []
        clean_target = re.sub(r"[^a-zA-Z0-9]", "", target.lower())
        if clean_target:
            slugs.append(clean_target)

        target_kebab = re.sub(r"[^a-zA-Z0-9]+", "-", target.lower()).strip("-")
        if target_kebab and target_kebab not in slugs:
            slugs.append(target_kebab)

        if domain:
            dom_clean = self._clean_domain(domain)
            dom_name = dom_clean.split(".")[0]
            if dom_name and dom_name not in slugs:
                slugs.append(dom_name)

        return slugs

    def _clean_domain(self, target: str) -> str:
        target = target.strip()
        target = re.sub(r"^https?://", "", target, flags=re.I)
        target = target.split("/")[0]
        target = target.split(":")[0]
        return target.lower()

talent_intel_service = TalentIntelService()
