import logging
from typing import Dict, Any, List

logger = logging.getLogger("scoring")

def compute_due_diligence_score(evidence: Any) -> Dict[str, Any]:
    """
    Computes an institutional-grade Quantitative Due Diligence & Health Score (0-100)
    evaluating:
    1. Financial Velocity & Scale (0-35 points)
    2. Tech Stack Scalability & Modernness (0-30 points)
    3. Monetization Moat & Pricing Model (0-25 points)
    4. Operational & Platform Risk Penalties (-15 to 0 points)
    """
    financials = getattr(evidence, "verified_financials", {}) or {}
    tech_stack = getattr(evidence, "tech_stack", []) or []
    pricing = getattr(evidence, "pricing_signals", {}) or {}
    contradictions = getattr(evidence, "contradictions", []) or []

    score = 0
    breakdown = {}

    # -------------------------------------------------------------
    # 1. Financial Velocity (0-35 pts)
    # -------------------------------------------------------------
    fin_pts = 0
    mrr_str = str(financials.get("verified_mrr", "")).lower()
    total_rev_str = str(financials.get("total_revenue", "")).lower()
    growth_str = str(financials.get("growth_30d", "")).replace("%", "").strip()

    # MRR Scale
    if "k" in mrr_str:
        try:
            val = float(mrr_str.replace("$", "").replace("k", "").strip())
            if val >= 25.0:
                fin_pts += 15
            elif val >= 5.0:
                fin_pts += 12
            else:
                fin_pts += 8
        except ValueError:
            fin_pts += 5
    elif "m" in mrr_str:
        fin_pts += 15
    elif financials.get("verified_mrr"):
        fin_pts += 4

    # 30-Day Growth Velocity
    try:
        growth_val = float(growth_str)
        if growth_val >= 20.0:
            fin_pts += 12
        elif growth_val >= 5.0:
            fin_pts += 9
        elif growth_val >= 0.0:
            fin_pts += 5
        else:
            fin_pts += 1  # negative growth
    except ValueError:
        if financials.get("verified_mrr"):
            fin_pts += 5

    # ARR Multiple or Historical Volume
    mult_str = str(financials.get("arr_multiple", "")).lower().replace("x", "").strip()
    try:
        mult_val = float(mult_str)
        if mult_val <= 2.5:
            fin_pts += 8  # Bargain multiple
        elif mult_val <= 4.5:
            fin_pts += 5  # Fair value
        else:
            fin_pts += 2  # Expensive
    except ValueError:
        if "k" in total_rev_str or "m" in total_rev_str:
            fin_pts += 6

    github = getattr(evidence, "github_telemetry", None) or {}
    dns = getattr(evidence, "dns_infrastructure", None) or {}
    surfaces = getattr(evidence, "operational_surfaces", None) or {}
    sentiment = getattr(evidence, "community_sentiment", None) or ""
    subdomain_recon = getattr(evidence, "subdomain_recon", None) or {}
    sub_signals = subdomain_recon.get("architecture_signals", {})
    talent = getattr(evidence, "talent_telemetry", None) or {}
    total_roles = talent.get("total_open_roles", 0)
    packages = getattr(evidence, "package_telemetry", None) or {}
    pkg_downloads = packages.get("total_monthly_downloads", 0)
    docker_pulls = packages.get("total_docker_pulls", 0)
    sec = getattr(evidence, "sec_filings", None) or {}

    # SEC Regulatory Capital Sold (Alternative Ground-Truth Scale)
    if sec.get("is_sec_registered"):
        sec_sold = sec.get("total_capital_sold_usd", 0.0)
        if sec_sold >= 50_000_000:
            fin_pts = max(fin_pts, 32)
        elif sec_sold >= 10_000_000:
            fin_pts = max(fin_pts, 26)
        elif sec_sold >= 1_000_000:
            fin_pts = max(fin_pts, 20)
        elif sec_sold > 0:
            fin_pts = max(fin_pts, 14)

    fin_pts = min(35, fin_pts)
    breakdown["financial_velocity"] = {
        "score": fin_pts,
        "max": 35,
        "label": "Financial Velocity & Scale"
    }
    score += fin_pts

    # -------------------------------------------------------------
    # 2. Tech Stack & Architecture (0-30 pts)
    # -------------------------------------------------------------
    tech_pts = 0
    tech_names = [t.get("name", "") for t in tech_stack if isinstance(t, dict)]

    # Modern Frontend Framework
    if any(k in tech_names for k in ["Next.js", "React", "Nuxt.js", "Vue.js", "Svelte / SvelteKit", "Remix", "Astro"]):
        tech_pts += 10
    elif tech_stack:
        tech_pts += 4

    # Scalable Cloud / CDN
    if any(k in tech_names for k in ["Vercel", "Cloudflare", "AWS CloudFront", "Netlify"]):
        tech_pts += 8

    # Reliable Payment Gateway
    if any(k in tech_names for k in ["Stripe", "LemonSqueezy", "Paddle", "Whop"]):
        tech_pts += 6

    # Analytics / Product Telemetry
    if any(k in tech_names for k in ["PostHog", "Mixpanel", "Google Analytics / GTM", "Plausible Analytics", "Segment"]):
        tech_pts += 6

    # Open Source & Developer Velocity Moat
    gh_stars = github.get("stars", 0)
    if gh_stars >= 10000:
        tech_pts += 10  # Institutional OSS adoption
    elif gh_stars >= 1000:
        tech_pts += 6
    elif gh_stars > 0:
        tech_pts += 3

    # Verified Package Consumption (NPM / PyPI / Docker Hub)
    if pkg_downloads >= 100_000 or docker_pulls >= 1_000_000:
        tech_pts += 8  # Proven high-scale production utilization
    elif pkg_downloads >= 10_000 or docker_pulls >= 10_000:
        tech_pts += 4

    # Enterprise DNS & Mail Infrastructure
    dns_score = dns.get("enterprise_email_security_score", 0)
    if dns_score >= 80:
        tech_pts += 5
    elif dns_score >= 50:
        tech_pts += 3

    # Public API & Developer Docs
    if surfaces.get("has_public_api_or_docs"):
        tech_pts += 4

    # Subdomain Architecture Maturity (Microservices, Realtime WebSockets, AI/MCP)
    if sub_signals.get("is_microservice_architecture"):
        tech_pts += 5
    if sub_signals.get("has_realtime_websockets"):
        tech_pts += 4
    if sub_signals.get("has_ai_or_mcp_endpoints"):
        tech_pts += 4

    # Active Engineering Talent Velocity
    eng_roles = talent.get("department_breakdown", {}).get("Engineering & Product", {}).get("count", 0)
    if eng_roles >= 5:
        tech_pts += 4

    # Proprietary Patent Portfolio
    ip = getattr(evidence, "ip_intel", None) or {}
    if ip.get("total_patents", 0) > 0:
        tech_pts += 4

    tech_pts = min(30, tech_pts)
    breakdown["tech_architecture"] = {
        "score": tech_pts,
        "max": 30,
        "label": "Architecture Scalability"
    }
    score += tech_pts

    # -------------------------------------------------------------
    # 3. Monetization Moat & Pricing (0-25 pts)
    # -------------------------------------------------------------
    pricing_pts = 0
    if pricing.get("has_free_tier"):
        pricing_pts += 8  # Effective PLG lead magnet
    if pricing.get("has_annual_discount"):
        pricing_pts += 7  # Upfront annual cash flow lock-in
    if pricing.get("detected_price_points"):
        pricing_pts += 6  # Transparent self-serve tiers
    if str(financials.get("funding", "")).lower() == "bootstrapped":
        pricing_pts += 4  # Bootstrapped discipline
    if surfaces.get("has_enterprise_tier") or sub_signals.get("has_enterprise_subdomains"):
        pricing_pts += 6  # Enterprise contract expansion
    if surfaces.get("has_dedicated_pricing"):
        pricing_pts += 4
    if "Positive" in sentiment:
        pricing_pts += 4  # Strong organic developer brand equity
    elif "Mixed" in sentiment:
        pricing_pts += 2

    # Active Go-to-Market Expansion
    gtm_roles = talent.get("department_breakdown", {}).get("Sales & GTM", {}).get("count", 0)
    if gtm_roles >= 3:
        pricing_pts += 4

    # SEC Institutional Governance Oversight
    if sec.get("is_sec_registered"):
        if sec.get("board_directors"):
            pricing_pts += 4  # Independent / institutional board oversight
        if sec.get("jurisdiction") == "DELAWARE":
            pricing_pts += 2  # Delaware C-Corp governance standard

    # Intellectual Property & Federal Trademark Moat
    if ip.get("total_trademarks", 0) > 0:
        pricing_pts += 4  # Federally registered brand defense
    if ip.get("total_patents", 0) > 0:
        pricing_pts += 3  # Proprietary algorithmic lock-in

    pricing_pts = min(25, pricing_pts)
    breakdown["monetization_moat"] = {
        "score": pricing_pts,
        "max": 25,
        "label": "Monetization & Pricing Moat"
    }
    score += pricing_pts

    # -------------------------------------------------------------
    # 4. Risk & Platform Penalties (-15 to 0 pts)
    # -------------------------------------------------------------
    risk_deduction = 0
    risk_flags = []

    if contradictions:
        risk_deduction += 6
        risk_flags.append("Marketing Discrepancy Flagged")

    # Only penalize unverified payment gateway if NO financials AND no verified OSS repo AND no SEC registration
    if not financials.get("verified_mrr") and not github.get("success") and not sec.get("is_sec_registered"):
        risk_deduction += 4
        risk_flags.append("Unverified Payment Gateways")

    # Flag unprotected brand risk for growth companies with 0 registered marks
    if (fin_pts >= 12 or total_roles >= 8) and ip and ip.get("total_trademarks", 0) == 0:
        risk_flags.append("Unprotected Brand (No Federal Trademarks)")

    score = max(15, min(100, score - risk_deduction))

    # Rating Tier
    if score >= 85:
        rating = "Institutional Grade (High Conviction)"
        color = "emerald"
    elif score >= 70:
        rating = "Strong Fundamentals (Growth Phase)"
        color = "brand"
    elif score >= 55:
        rating = "Moderate Traction (Watchlist)"
        color = "amber"
    else:
        rating = "High Risk / Early Validation"
        color = "red"

    return {
        "overall_score": score,
        "rating": rating,
        "color": color,
        "breakdown": breakdown,
        "risk_flags": risk_flags
    }
