# 🗺️ Xod Intelligence Engine — Strategic Roadmap & Capabilities Architecture

This roadmap tracks the development of institutional-grade, zero-API-key intelligence modules for Xod.

---

## 📌 The 6 Core Hunter & Diligence Modules

Derived from real-world startup research and founder outreach bottlenecks:

| ID | Module Name | Status | Zero-Key Architecture | Primary Agent Tool |
| :--- | :--- | :---: | :--- | :--- |
| **#1** | **Verified Work-Email Finder** | 🚀 **Armed** | Permutation engine (`first.last@`, `first@`, etc.) + MX host alignment + GitHub commit author mining + public digital exhaust | `find_work_email` |
| **#2** | **Live Job Firehose & Batch Cohort Hunter** | 🚀 **Armed** | Direct Ashby, Greenhouse & Lever public JSON APIs + recency filtering + HTTP liveness probes + concurrent multi-startup cohort sweeping | `scan_live_job_firehose` / `scan_batch_job_firehose` |
| **#3** | **Funding & Org Intelligence** | 🚀 **Armed** | Y Combinator public directory (batch `W24`/`S23`, founders, team size) + SEC EDGAR Form D venture capital filings + scam/zombie risk screening | `audit_funding_and_org` |
| **#4** | **Email Bounce-Risk Verifier** | 🚀 **Armed** | Cloudflare DNS-over-HTTPS MX lookup + SPF/DMARC policy extraction + catch-all detection + disposable domain blocking | `verify_email_bounce_risk` |
| **#5** | **Live Tech-Stack Detector** | ✅ **Active** | In-situ DOM scripts, HTTP response headers, CDN signatures, DNS telemetry, and SDK fingerprints | `audit_tech_stack` |
| **#6** | **Trigger-Event Radar** | 🚀 **Armed** | Algolia Hacker News (`Show HN` / launches) + SEC EDGAR Form D (Series A/equity rounds) + Google News RSS real-time corporate pulse | `scan_trigger_events` |

---

## 🛠️ Implementation Details

### Module #1: Verified Work-Email Finder (`find_work_email`)
- **Bottleneck Solved**: Hunter.io / RocketReach paywalls and poor coverage on early-stage founders.
- **Workflow**:
  1. Accepts `First Name`, `Last Name`, and company domain.
  2. Generates standard business permutation matrix (`first@`, `first.last@`, `flast@`, `firstlast@`, `firstl@`, `last@`).
  3. Audits domain mail exchanger (MX) provider (Google Workspace, Microsoft 365, etc.).
  4. Queries GitHub public commit history for author email addresses matching founder names.
  5. Computes deliverability confidence score (0-100%) and provides clear verification evidence.

### Module #2: Live Job Firehose & Batch Cohort Hunter (`scan_live_job_firehose` & `scan_batch_job_firehose`)
- **Bottleneck Solved**: Dead postings on stale job boards, ghost jobs, and single-target lookup limits.
- **Workflow**:
  1. Aggregates live requisition feeds from public Ashby, Greenhouse, and Lever JSON endpoints.
  2. Filters postings by recency timestamp (`max_age_days`) and role keyword filters ('founding', 'engineer', 'sales').
  3. Executes HTTP liveness check on individual requisition URLs to confirm listings haven't been archived or redirected.
  4. Supports **Batch Cohort Mode** to scan entire startup batches (YC AI, Modern DevTools, AI Infra, FinTech) concurrently with bounded semaphores.

### Module #3: Funding & Org Data (`audit_funding_and_org`)
- **Bottleneck Solved**: Scam screening, zombie projects, and lack of accelerator cohort transparency.
- **Workflow**:
  1. Scrapes Y Combinator's verified directory (`ycombinator.com/companies/{slug}`) for accelerator batch (e.g. `Winter 2009`, `W24`), active founder names, team headcount, company status, and primary partner.
  2. Cross-references federal SEC EDGAR Form D filings for legally verified venture capital rounds, date of first sale, and institutional investors.
  3. Synthesizes a composite organizational legitimacy score and scam risk assessment.

### Module #4: Email Bounce-Risk Verifier (`verify_email_bounce_risk`)
- **Bottleneck Solved**: Preventing domain reputation burns when sending cold outreach.
- **Workflow**:
  1. RFC 5322 syntax validation.
  2. DNS MX lookup via Cloudflare DNS-over-HTTPS (`cloudflare-dns.com/dns-query`).
  3. SPF and DMARC enforcement policy analysis.
  4. Catch-all domain detection.
  5. Disposable/temporary mailbox detection.

### Module #5: Live Tech-Stack Detector (`audit_tech_stack`)
- **Workflow**: Directly inspects target domain for Next.js, React, Tailwind, Supabase, Clerk, Stripe, PostHog, etc.

### Module #6: Trigger-Event Radar (`scan_trigger_events`)
- **Workflow**: Synthesizes fresh funding announcements, Product Hunt / Show HN launches, and executive hiring surges for high-converting warm openers.

---

## 📅 Version Milestones
- **v1.0**: Core TrustMRR & SaaS financial intelligence engine.
- **v1.5**: Direct DOM Tech Stack Auditor & TrafficPulse (Tranco Top 1M, Path Activity).
- **v2.0**: Zero-Key TrendPulse (X/Twitter, TikTok, Google Trends, Hacker News, GitHub).
- **v2.5**: Talent & Founder Hunter Suite (Email Finder, Email Verifier, Job Firehose, YC & SEC Funding).
- **v2.6**: Batch Multi-Company Cohort Firehose & Dedicated Hunter Suite UI Workspaces.
