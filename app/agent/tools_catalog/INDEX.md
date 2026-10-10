# Xod Autonomous Agent Tool Catalog & Decision Matrix

This catalog serves as the ground-truth reference manual for Xod's autonomous reasoning layer. It outlines the purpose, input requirements, ideal use cases, anti-patterns, and multi-hop sequencing for every specialized tool in the arsenal.

---

## 🧠 Core Deliberation Protocol: "Think First, Decide, Then Act"

Before calling any tool, the agent MUST perform internal deliberation:
1. **Intent Classification**:
   - Is the user asking a conversational greeting (`hi`, `hello`), asking about your capabilities, or requesting general guidance?
     -> **DECISION: NO TOOLS**. Respond immediately with a helpful, conversational message. Do not display research plans or scorecards.
   - Is the user asking for facts that require external verification (e.g. startup financials, tech stack, hiring velocity, funding, competitors, trademarks)?
     -> **DECISION: SELECT TOOLS**. Formulate a focused hypothesis and select only the tools strictly required to prove or disprove it.

2. **Relevance & Precision**:
   - Do NOT call all tools blindly. Choose the minimal, highest-yield tools for the specific query.
   - If looking up a company domain (e.g. `supabase.com`), use `audit_tech_stack` or `recon_shadow_subdomains`.
   - If looking up funding or legal entity, use `audit_sec_filings`.
   - If evaluating developer traction, use `audit_package_ecosystem` or `audit_github_repo`.

---

## 🗂️ Tool Directory by Intelligence Vector

| Vector | Tools | Primary Ground Truth |
| :--- | :--- | :--- |
| **Financial & M&A** | `trustmrr_get_startup`<br>`trustmrr_list_startups`<br>`trustmrr_compare_startups`<br>`trustmrr_screen_deals` | Stripe-verified SaaS MRR, ARR multiples, churn, growth, and vetted acquisition listings. |
| **Direct Architecture** | `audit_tech_stack` | Direct DOM fingerprinting: frontend frameworks, payment gateways, analytics, and pricing signals. |
| **Shadow Infrastructure** | `recon_shadow_subdomains` | Certificate Transparency (CT) logs & passive DNS for hidden staging, private APIs, and WebSocket clusters. |
| **Talent & Burn Rate** | `audit_talent_and_hiring` | Public ATS mining (Ashby, Greenhouse, Lever) for hiring velocity, Eng vs GTM ratios, and burn signals. |
| **Package Consumption** | `audit_package_ecosystem` | Monthly NPM / PyPI downloads and Docker Hub pulls to separate real SDK adoption from vanity GitHub stars. |
| **Federal Regulatory** | `audit_sec_filings` | SEC EDGAR Form D/Form D/A filings: total venture capital sold, investor counts, and institutional board directors. |
| **Intellectual Property** | `audit_ip_landscape` | USPTO federal registered trademarks, Google Patents proprietary algorithmic inventions, and Wikidata core products. |
| **Open Source** | `audit_github_repo` | GitHub repository stars, forks, commit cadence, license compliance, and maintenance health. |
| **DNS & Security** | `inspect_domain_dns_health` | Enterprise email security (SPF, DKIM, DMARC), mail providers, and SaaS verification TXT records. |
| **Operational Surface** | `explore_site_surface` | Probing public API docs, status pages, enterprise tier offerings, and changelogs. |
| **Market Perception** | `mine_community_sentiment`<br>`tavily_search`<br>`you_search`<br>`you_news_search`<br>`omni_deep_search` | Tavily AI cited research & direct answers, Hacker News sentiment, developer critiques, news articles, and multi-query Reciprocal Rank Fusion. |
| **Persistent Memory** | `query_knowledge_graph`<br>`update_knowledge_graph_entity` | Querying and updating the local SQLite SaaS Knowledge Graph and linking competitors. |
| **Financial Modeling** | `calculate_saas_metrics` | Quantitative SaaS metrics (ARR, Rule of 40, ARPU, LTV/CAC, ARR multiple valuation). |
