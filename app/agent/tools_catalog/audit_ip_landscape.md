# Tool Documentation: `audit_ip_landscape`

## Overview
Audits intellectual property, federal USPTO registered trademarks, patent moats, and proprietary algorithmic inventions across USPTO/Justia Trademarks, Google Patents, and global IP databases.

## When to Use
- User asks about a startup's defensibility, patents, proprietary algorithms, or trademarks.
- M&A due diligence to verify if the company legally owns its brand name or is vulnerable to trademark squatting.
- Investigating whether technical innovations (e.g. database engines, AI models, security proxies) have patent protection.
- Checking cataloged products on Wikidata.

## When NOT to Use
- Casual greetings or general financial questions where IP is irrelevant.
- Looking up pure operational metrics like MRR, churn, or DNS records.

## Parameters
- `target` (string, required): Startup or brand name (e.g. `'Vercel'`, `'Supabase'`, `'Cloudflare'`).
- `entity_name` (string, optional): Specific corporate entity name.

## Key Returns
- `has_ip_moat` (bool): True if federal trademarks or patents were located.
- `ip_moat_score` (int): 0-100 defensibility score.
- `ip_defensibility_verdict` (str): Qualitative evaluation of the IP moat.
- `brand_protection_status` (str): `'Protected Brand'` vs `'Unregistered Brand Commons (Squatting Risk)'`.
- `trademarks` (list): Registered word marks, registration numbers, filing dates, and goods/services coverage.
- `patents` (list): Patent publication numbers, titles, abstracts, and direct URLs.
- `registered_products` (list): Core software platforms and products cataloged in global databases.
