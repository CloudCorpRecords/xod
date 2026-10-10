# Tool Documentation: `explore_site_surface`

## Overview
Probes standard SaaS product surfaces, public API documentation pages, changelogs, status pages, enterprise tier offerings, and security disclosure policies (`security.txt`).

## When to Use
- Determining if a startup has a developer API, enterprise custom contract tier, or public roadmap.
- Evaluating operational maturity score (0-100).
- Checking product release velocity via public changelog detection.

## When NOT to Use
- When domain is unknown.
- Casual greetings or general market benchmarks.

## Parameters
- `domain` (string, required): Startup domain (e.g. `'cal.com'`, `'supabase.com'`).

## Key Returns
- `operational_signals`: Booleans for `has_public_api_or_docs`, `has_enterprise_tier`, `has_changelog_or_roadmap`, `has_dedicated_pricing`.
- `maturity_score`: 0-100 operational surface score.
- `surfaces_found`: Exact URLs located for docs, changelog, pricing, and security.
