# Tool Documentation: `omni_deep_search`

## Overview
Next-generation multi-vector research fusion engine. Fans out targeted sub-queries across TrustMRR financial telemetry, You.com deep web snippets, and direct domain audits, fusing and de-duplicating all findings via Reciprocal Rank Fusion (RRF).

## When to Use
- Best starting tool for any complex startup teardown, competitor analysis, or market research where a 360-degree synthesis is needed.
- Uncovering contradictions between marketing claims and actual ground truth.

## When NOT to Use
- Simple conversational greetings or when a targeted single-purpose tool (e.g. `audit_sec_filings` or `audit_tech_stack`) is more direct and context-efficient.

## Parameters
- `goal` (string, required): Research objective (e.g. `'Teardown on Cursor'`).
- `target_startup` (string, optional): Specific startup name.
- `domain` (string, optional): Root domain.

## Key Returns
- `top_ranked_intelligence`: Best multi-source facts ranked by RRF score.
- `contradictions_detected`: Discrepancies between marketing and reality.
