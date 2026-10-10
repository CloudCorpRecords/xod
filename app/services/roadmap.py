from typing import Dict, Any, List

def get_project_roadmap() -> Dict[str, Any]:
    """Returns the backend project capability roadmap, statuses, and zero-key architectural details."""
    return {
        "title": "Xod Intelligence Engine — Capability Roadmap",
        "version": "2.5.0",
        "modules": [
            {
                "id": "email_finder",
                "number": 1,
                "name": "Verified Work-Email Finder",
                "tool": "find_work_email",
                "status": "active",
                "description": "Permutation engine (first.last@, first@, etc.) + MX host alignment + GitHub commit author mining + public digital exhaust.",
                "bottleneck_solved": "Replaces paywalled Hunter/RocketReach with zero-key discovery for early-stage founders."
            },
            {
                "id": "job_firehose",
                "number": 2,
                "name": "Live Job Firehose & Batch Cohort Hunter",
                "tool": "scan_live_job_firehose / scan_batch_job_firehose",
                "status": "active",
                "description": "Aggregates public Ashby, Greenhouse, and Lever JSON feeds with recency filtering, dead-posting liveness check, and multi-company cohort batch scanning (YC AI, Modern DevTools, AI Infra).",
                "bottleneck_solved": "Eliminates stale job boards and ghost jobs with real-time HTTP liveness checks and multi-company talent sweeps."
            },
            {
                "id": "funding_org_data",
                "number": 3,
                "name": "Funding & Org Intelligence",
                "tool": "audit_funding_and_org",
                "status": "active",
                "description": "Y Combinator public directory (batch W24/S23, founders, team size) + SEC EDGAR Form D venture capital filings + scam risk screening.",
                "bottleneck_solved": "Fast corporate due diligence and scam screening for stealth/early startups."
            },
            {
                "id": "email_verifier",
                "number": 4,
                "name": "Email Bounce-Risk Verifier",
                "tool": "verify_email_bounce_risk",
                "status": "active",
                "description": "Cloudflare DNS-over-HTTPS MX lookup + SPF/DMARC policy extraction + catch-all detection + disposable domain blocking.",
                "bottleneck_solved": "Prevents cold email domain reputation burns with zero-key pre-send deliverability checks."
            },
            {
                "id": "tech_detector",
                "number": 5,
                "name": "Live Tech-Stack Detector",
                "tool": "audit_tech_stack",
                "status": "active",
                "description": "In-situ DOM scripts, HTTP response headers, CDN signatures, DNS telemetry, and SDK fingerprints without external APIs.",
                "bottleneck_solved": "Instant tech stack personalization lines (e.g. 'I see you run Next.js and Supabase')."
            },
            {
                "id": "trigger_radar",
                "number": 6,
                "name": "Trigger-Event Radar",
                "tool": "scan_trigger_events",
                "status": "active",
                "description": "Algolia Hacker News (Show HN / launches) + SEC EDGAR Form D (funding rounds) + Google News RSS corporate announcements.",
                "bottleneck_solved": "Generates warm conversational openers ('saw your Series A', 'saw your Show HN launch')."
            }
        ]
    }
