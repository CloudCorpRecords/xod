# Tool Documentation: `audit_tech_stack`

## Overview
Direct live domain auditor. Probes any target startup's live URL (e.g. `'stan.store'`, `'chatbase.co'`, `'postiz.com'`) via HTTP/DOM inspection to fingerprint frontend frameworks, CDN providers, payment gateways, analytics tools, and pricing signals without external API keys.

## When to Use
- User asks what tech stack, framework, database, payment provider, or hosting a startup uses.
- Deep teardown of any startup with a known domain name.
- Detecting live billing providers (Stripe, LemonSqueezy, Paddle, Whop) and pricing structures (free tier, annual discounts).

## When NOT to Use
- When domain is unknown or when target is not a website.
- Casual greetings or general high-level market questions.

## Parameters
- `target` (string, required): Domain name or URL of the startup (e.g. `'postiz.com'`, `'stan.store'`).

## Key Returns
- `technologies` (list): Badges of detected technologies grouped by category (Frontend, CDN, Payments, Analytics).
- `pricing_signals` (dict): Detected price points, annual discount presence, and free tier status.
- `meta` (dict): Title, description, OG image, and response latency.
