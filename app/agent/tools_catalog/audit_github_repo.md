# Tool Documentation: `audit_github_repo`

## Overview
Audits open source telemetry and repository health directly from GitHub's public API. Examines real stars, forks, primary programming language, open issues, commit cadence, license type, and topics.

## When to Use
- Startup or product is open-source (e.g. Supabase, Postiz, Cal.com, LangChain).
- User asks about open source traction, star counts, repo health, or community adoption.

## When NOT to Use
- Closed-source proprietary SaaS with no public GitHub codebase.
- Casual greetings or general Q&A.

## Parameters
- `target` (string, required): Company or project name (e.g. `'postiz'`, `'supabase'`).
- `repo` (string, optional): Specific `owner/repo` string if known (e.g. `'gitroomhq/postiz-app'`).

## Key Returns
- `stars` / `forks` / `open_issues`: Quantitative community traction.
- `health_rating`: Star velocity and maintenance rating.
- `language`: Primary codebase programming language.
- `license`: Open source software license (MIT, Apache 2.0, AGPL, etc.).
