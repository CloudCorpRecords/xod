# Tool Documentation: `calculate_saas_metrics`

## Overview
Quantitative SaaS math engine. Calculates ARR, ARR valuation multiples, Rule of 40 score, Revenue Per Visitor (RPV), Average Revenue Per User (ARPU), and 12-month compound growth projections.

## When to Use
- Evaluating deal valuation multiples (e.g. asking price vs ARR).
- Calculating Rule of 40 (growth rate + profit margin).
- Modeling prospective M&A valuations.

## When NOT to Use
- When numbers/metrics are not available.
- Casual greetings or qualitative questions.

## Parameters
- `mrr_usd` (number, optional): Monthly recurring revenue.
- `revenue_30d_usd` (number, optional): Last 30-day gross revenue.
- `asking_price_usd` (number, optional): Listed acquisition price.
- `growth_30d_pct` (number, optional): Month-over-month growth rate percentage.
- `profit_margin_pct` (number, optional): Net profit margin percentage.
