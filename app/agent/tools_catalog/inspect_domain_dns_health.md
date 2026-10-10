# Tool Documentation: `inspect_domain_dns_health`

## Overview
Inspects enterprise email security posture, MX records, SPF configuration, DKIM selectors, DMARC policies, and SaaS third-party verification records (Google, Stripe, Microsoft, Postmark, SendGrid) using standard DNS queries.

## When to Use
- User asks about enterprise infrastructure maturity, security posture, or email deliverability.
- Detecting third-party enterprise vendor verifications in TXT records.
- Evaluating whether a startup takes email authentication and domain security seriously.

## When NOT to Use
- When domain is unknown or not provided.
- Casual greetings or financial deal screening.

## Parameters
- `domain` (string, required): Clean domain of the startup (e.g. `'linear.app'`, `'postiz.com'`).

## Key Returns
- `enterprise_email_security_score`: 0-100 quantitative DNS security score.
- `email_provider`: Primary mail infrastructure (Google Workspace, Microsoft 365, etc.).
- `dmarc_configured` / `dmarc_policy`: DMARC protection level (reject, quarantine, none).
- `saas_verifications`: Verified cloud vendors detected in DNS TXT records.
