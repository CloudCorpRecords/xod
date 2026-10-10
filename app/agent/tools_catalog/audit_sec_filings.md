# Tool Documentation: `audit_sec_filings`

## Overview
Queries the U.S. Securities and Exchange Commission (SEC) Electronic Data Gathering, Analysis, and Retrieval (EDGAR) EFTS system to parse Form D and Form D/A (Notice of Exempt Offering of Securities / Regulation D) XML archives.

## When to Use
- User asks about a startup's funding rounds, venture capital backing, or total capital raised.
- Checking official legal entity names, jurisdiction (e.g. Delaware C-Corp), and registered office address.
- Discovering executive leadership (CEOs, CFOs) and institutional board members (venture partner seats).
- M&A due diligence to verify whether private equity or venture capital claims are legally real.

## When NOT to Use
- Casual greetings, general SaaS Q&A, or non-corporate discussions.
- Non-US entities that have never issued private securities under U.S. Regulation D.
- Early micro-SaaS projects known to be bootstrapped solo operations with no institutional funding.

## Parameters
- `target` (string, required): Startup or company name (e.g. `'Vercel'`, `'Supabase'`, `'Anysphere'`).
- `entity_name` (string, optional): Specific registered corporate entity name if known (e.g. `'Vercel Inc.'`).

## Key Returns
- `is_sec_registered` (bool): True if official Form D filings exist.
- `legal_entity_name` (str): Full legal entity name (e.g. `'Supabase, Inc.'`).
- `jurisdiction` (str): State or country of incorporation (e.g. `'DELAWARE'`).
- `total_capital_sold_formatted` (str): Aggregated dollar volume sold across parsed filings (e.g. `'$1.3B'`).
- `executive_officers` (list): Names and roles of officers (e.g. CEO, CFO, Founder).
- `board_directors` (list): Board of directors (often revealing partner VCs from Accel, Coatue, CRV).
- `previous_names` (list): Former corporate identities (e.g. `'Zeit, Inc.'` for Vercel).
