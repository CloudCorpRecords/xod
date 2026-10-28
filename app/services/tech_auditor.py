import re
import json
import logging
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("tech_auditor")

# Known technology signature patterns
TECH_SIGNATURES: Dict[str, Dict[str, Any]] = {
    # --- Frontend Frameworks ---
    "Next.js": {
        "category": "Frontend Framework",
        "html": [r"__NEXT_DATA__", r"/_next/static/"],
        "headers": {"x-powered-by": r"Next\.js"},
        "scripts": [r"/_next/"]
    },
    "React": {
        "category": "Frontend Library",
        "html": [r"data-reactroot", r"_reactRootContainer", r"react\.production\.min\.js"],
        "scripts": [r"react(-dom)?\.(production|development)"]
    },
    "Vue.js": {
        "category": "Frontend Framework",
        "html": [r"v-cloak", r"data-v-[a-f0-9]+", r"__vue__"],
        "scripts": [r"vue(\.runtime)?\.min\.js"]
    },
    "Nuxt.js": {
        "category": "Frontend Framework",
        "html": [r"__NUXT__", r"/_nuxt/"],
        "scripts": [r"/_nuxt/"]
    },
    "Svelte / SvelteKit": {
        "category": "Frontend Framework",
        "html": [r"__svelte", r"svelte-[a-z0-9]+", r"/_app/immutable/"],
        "scripts": [r"/_app/immutable/"]
    },
    "Remix": {
        "category": "Frontend Framework",
        "html": [r"window\.__remixContext"],
        "scripts": [r"/__remix/"]
    },
    "Astro": {
        "category": "Frontend Framework",
        "html": [r"astro-island", r"data-astro-"]
    },
    "Tailwind CSS": {
        "category": "CSS Framework",
        "html": [r"tailwind", r"class=[\"'][^\"']*(?:grid-cols|items-center|justify-between|rounded-[2xl|xl|lg])[^\"']*[\"']"]
    },

    # --- Payment & Billing Gateways ---
    "Stripe": {
        "category": "Payment Gateway",
        "html": [r"js\.stripe\.com", r"stripe-buy-button", r"pk_live_[0-9a-zA-Z]+", r"pk_test_[0-9a-zA-Z]+"],
        "scripts": [r"js\.stripe\.com/v3"]
    },
    "LemonSqueezy": {
        "category": "Payment Gateway",
        "html": [r"lemonsqueezy\.com", r"assets\.lemonsqueezy\.com", r"data-ls-"],
        "scripts": [r"assets\.lemonsqueezy\.com/lemon\.js"]
    },
    "Paddle": {
        "category": "Payment Gateway",
        "html": [r"cdn\.paddle\.com", r"Paddle\.Setup", r"paddle\.js"],
        "scripts": [r"cdn\.paddle\.com/paddle/paddle\.js"]
    },
    "Whop": {
        "category": "Payment Gateway",
        "html": [r"whop\.com/checkout", r"whop\.com/embedded"]
    },
    "Shopify": {
        "category": "Ecommerce / Payments",
        "html": [r"cdn\.shopify\.com", r"myshopify\.com"],
        "scripts": [r"cdn\.shopify\.com"]
    },
    "PayPal": {
        "category": "Payment Gateway",
        "html": [r"paypal\.com/sdk/js"],
        "scripts": [r"paypal\.com/sdk"]
    },
    "Chargebee": {
        "category": "Subscription Billing",
        "html": [r"js\.chargebee\.com"],
        "scripts": [r"chargebee\.js"]
    },

    # --- Authentication & Backend ---
    "Supabase": {
        "category": "Database & Auth",
        "html": [r"supabase\.co", r"@supabase/supabase-js"]
    },
    "Firebase": {
        "category": "Backend / Database",
        "html": [r"firebase\.googleapis\.com", r"firebasejs", r"__FIREBASE_DEFAULTS__"],
        "scripts": [r"firebase.*\.js"]
    },
    "Clerk": {
        "category": "Authentication",
        "html": [r"clerk\.com", r"frontend-api\.clerk\.dev", r"clerk-telemetry", r"pk_live_[A-Za-z0-9]+clerk"],
        "scripts": [r"clerk\.browser\.js", r"/npm/@clerk/"]
    },
    "Auth0": {
        "category": "Authentication",
        "html": [r"cdn\.auth0\.com", r"auth0-js"],
        "scripts": [r"cdn\.auth0\.com"]
    },
    "Kinde": {
        "category": "Authentication",
        "html": [r"kinde\.com", r"kinde-auth"]
    },

    # --- Analytics & Product Intelligence ---
    "PostHog": {
        "category": "Product Analytics",
        "html": [r"posthog\.com", r"us\.i\.posthog\.com", r"eu\.i\.posthog\.com"],
        "scripts": [r"posthog.*\.js", r"us-assets\.i\.posthog\.com"]
    },
    "Mixpanel": {
        "category": "Product Analytics",
        "html": [r"cdn\.mxpnl\.com", r"mixpanel\.init"],
        "scripts": [r"cdn\.mxpnl\.com/libs/mixpanel"]
    },
    "Google Analytics / GTM": {
        "category": "Web Analytics",
        "html": [r"googletagmanager\.com/gtm\.js", r"google-analytics\.com/analytics\.js", r"googletagmanager\.com/gtag/js"],
        "scripts": [r"googletagmanager\.com", r"google-analytics\.com"]
    },
    "Plausible Analytics": {
        "category": "Privacy Analytics",
        "html": [r"plausible\.io/js/script\.js", r"plausible\.io"],
        "scripts": [r"plausible\.io"]
    },
    "Fathom Analytics": {
        "category": "Privacy Analytics",
        "html": [r"usefathom\.com/script\.js"],
        "scripts": [r"usefathom\.com"]
    },
    "Segment": {
        "category": "Customer Data Platform",
        "html": [r"cdn\.segment\.com/analytics\.js"],
        "scripts": [r"cdn\.segment\.com"]
    },
    "Hotjar": {
        "category": "Behavior Analytics",
        "html": [r"static\.hotjar\.com"],
        "scripts": [r"hotjar-"]
    },
    "Microsoft Clarity": {
        "category": "Session Recording",
        "html": [r"www\.clarity\.ms"],
        "scripts": [r"clarity\.ms/tag"]
    },

    # --- AI & LLM Ecosystem ---
    "OpenAI API Integration": {
        "category": "AI / LLM",
        "html": [r"api\.openai\.com", r"gpt-4o", r"text-embedding-3"]
    },
    "Vercel AI SDK": {
        "category": "AI Framework",
        "html": [r"@ai-sdk", r"useChat", r"useCompletion"]
    },
    "ElevenLabs": {
        "category": "Voice AI",
        "html": [r"elevenlabs\.io"]
    },
    "Replicate": {
        "category": "AI Inference",
        "html": [r"replicate\.com", r"replicate\.delivery"]
    },
    "Chatbase Widget": {
        "category": "AI Customer Support",
        "html": [r"chatbase\.co/embed\.min\.js"],
        "scripts": [r"chatbase\.co"]
    },

    # --- Customer Support & Chat ---
    "Intercom": {
        "category": "Customer Support",
        "html": [r"widget\.intercom\.io", r"Intercom\("],
        "scripts": [r"widget\.intercom\.io/widget"]
    },
    "Crisp Chat": {
        "category": "Customer Support",
        "html": [r"client\.crisp\.chat"],
        "scripts": [r"client\.crisp\.chat/l\.js"]
    },
    "Tidio": {
        "category": "Customer Support",
        "html": [r"code\.tidio\.co"],
        "scripts": [r"code\.tidio\.co"]
    },
    "Zendesk": {
        "category": "Customer Support",
        "html": [r"static\.zdassets\.com", r"assets\.zendesk\.com"],
        "scripts": [r"zdassets\.com"]
    },

    # --- Hosting, CDN & Cloud Infrastructure ---
    "Cloudflare": {
        "category": "CDN & Security",
        "headers": {"server": r"cloudflare", "cf-ray": r".+"}
    },
    "Vercel": {
        "category": "Cloud Hosting",
        "headers": {"x-vercel-id": r".+", "x-vercel-cache": r".+", "server": r"Vercel"}
    },
    "AWS CloudFront": {
        "category": "CDN",
        "headers": {"x-amz-cf-id": r".+", "via": r"CloudFront"}
    },
    "Netlify": {
        "category": "Cloud Hosting",
        "headers": {"x-nf-request-id": r".+", "server": r"Netlify"}
    }
}


class TechAuditor:
    """
    Direct zero-dependency domain and tech stack auditor.
    Connects directly to the live domain, parses assets, scripts, headers,
    metadata, and pricing structures.
    """

    def _normalize_url(self, target: str) -> str:
        target = target.strip()
        if not target.startswith("http://") and not target.startswith("https://"):
            target = f"https://{target}"
        return target

    def _extract_domain(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed.netloc.lower()

    async def audit(self, target: str) -> Dict[str, Any]:
        """
        Audits a target domain or URL. Returns detected tech stack,
        SEO metadata, security posture, and detected pricing indicators.
        """
        url = self._normalize_url(target)
        domain = self._extract_domain(url)

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 (XOD Researcher/2.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            from app.services import tls_fetch
            async with tls_fetch.client(timeout=10.0, follow_redirects=True) as client:
                res = await client.get(url, headers=headers)
                tls_certificate = client.tls_note
                final_url = str(res.url)
                status_code = res.status_code
                resp_headers = {k.lower(): v for k, v in res.headers.items()}
                html = res.text
        except Exception as e:
            return {
                "success": False,
                "domain": domain,
                "url": url,
                "error": f"Failed to connect to {url}: {str(e)}"
            }

        soup = BeautifulSoup(html, "html.parser")

        # 1. Detect Technologies
        detected_tech = self._detect_technologies(html, resp_headers, soup)

        # 2. Extract SEO & Social Metadata
        meta_info = self._extract_metadata(soup, final_url)

        # 3. Detect Pricing Indicators
        pricing_signals = self._extract_pricing_signals(html, soup)

        # 4. Security & Architecture Signals
        security = {
            "https": final_url.startswith("https://"),
            "hsts": "strict-transport-security" in resp_headers,
            "csp": "content-security-policy" in resp_headers,
            "x_frame_options": resp_headers.get("x-frame-options", "None"),
            "server_header": resp_headers.get("server", "Hidden")
        }

        # Categorize detected technologies
        tech_by_category: Dict[str, List[str]] = {}
        for item in detected_tech:
            cat = item["category"]
            tech_by_category.setdefault(cat, []).append(item["name"])

        return {
            "success": True,
            "domain": domain,
            "final_url": final_url,
            "status_code": status_code,
            "tls_certificate": tls_certificate,
            "technologies": detected_tech,
            "technologies_by_category": tech_by_category,
            "total_technologies_detected": len(detected_tech),
            "metadata": meta_info,
            "pricing_signals": pricing_signals,
            "security": security
        }

    def _detect_technologies(self, html: str, headers: Dict[str, str], soup: BeautifulSoup) -> List[Dict[str, str]]:
        detected = []
        script_srcs = [s.get("src", "") for s in soup.find_all("script") if s.get("src")]
        script_srcs_str = " ".join(script_srcs)

        for name, sig in TECH_SIGNATURES.items():
            found = False

            # Check HTML regex
            if "html" in sig:
                for pattern in sig["html"]:
                    if re.search(pattern, html, re.IGNORECASE):
                        found = True
                        break

            # Check Headers
            if not found and "headers" in sig:
                for header_name, pattern in sig["headers"].items():
                    val = headers.get(header_name.lower(), "")
                    if val and re.search(pattern, val, re.IGNORECASE):
                        found = True
                        break

            # Check Script Tags
            if not found and "scripts" in sig:
                for pattern in sig["scripts"]:
                    if re.search(pattern, script_srcs_str, re.IGNORECASE):
                        found = True
                        break

            if found:
                detected.append({
                    "name": name,
                    "category": sig["category"]
                })

        return detected

    def _extract_metadata(self, soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        
        def get_meta(attr: str, val: str) -> str:
            tag = soup.find("meta", {attr: val})
            return tag.get("content", "").strip() if tag and tag.get("content") else ""

        description = get_meta("name", "description") or get_meta("property", "og:description")
        og_title = get_meta("property", "og:title")
        og_image = get_meta("property", "og:image")
        og_site_name = get_meta("property", "og:site_name")
        twitter_creator = get_meta("name", "twitter:creator") or get_meta("name", "twitter:site")

        # JSON-LD schemas
        json_ld_schemas = []
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                if tag.string:
                    data = json.loads(tag.string)
                    schema_type = data.get("@type") if isinstance(data, dict) else [d.get("@type") for d in data if isinstance(d, dict)]
                    if schema_type:
                        json_ld_schemas.append(schema_type)
            except Exception:
                pass

        return {
            "title": title or og_title,
            "description": description[:300] if description else "",
            "og_image": og_image,
            "site_name": og_site_name,
            "twitter_handle": twitter_creator,
            "json_ld_schemas": json_ld_schemas[:4]
        }

    def _extract_pricing_signals(self, html: str, soup: BeautifulSoup) -> Dict[str, Any]:
        """
        Extracts pricing mentions, price points, and billing intervals ($X/mo, free tier).
        """
        # Look for dollar amounts ($9, $19, $99, etc.)
        price_patterns = re.findall(r"\$([0-9]{1,4}(?:\.[0-9]{2})?)\s*(?:/\s*(?:mo|month|yr|year|user|seat))?", html)
        unique_prices = sorted(list(set([float(p) for p in price_patterns if float(p) > 0 and float(p) < 5000])))[:8]

        has_free_tier = bool(re.search(r"\b(free plan|free tier|start for free|try for free|100% free|free trial)\b", html, re.IGNORECASE))
        has_enterprise = bool(re.search(r"\b(enterprise plan|contact sales|custom pricing|talk to sales)\b", html, re.IGNORECASE))
        has_annual_discount = bool(re.search(r"\b(save [0-9]+%|billed annually|yearly discount)\b", html, re.IGNORECASE))

        return {
            "detected_price_points": [f"${p:g}" for p in unique_prices],
            "has_free_tier": has_free_tier,
            "has_enterprise": has_enterprise,
            "has_annual_discount": has_annual_discount
        }

# Global singleton
tech_auditor = TechAuditor()
