# Tool Documentation: TrustMRR Financial Suite (`trustmrr_*`)

## Tools Included
1. `trustmrr_get_startup`: Retrieve full verified financial dossier on a single startup.
2. `trustmrr_list_startups`: Search and filter startups by verified revenue, MRR, growth rate, asking price, multiple, category, and sale status.
3. `trustmrr_compare_startups`: Compare 2 to 5 startups side-by-side on verified unit economics.
4. `trustmrr_screen_deals`: Filter startups currently listed for sale by valuation multiple and asking price.

## When to Use
- User asks for verified revenue, MRR, all-time revenue, churn, customer counts, or ARR multiples.
- Screening businesses for acquisition (e.g. "Find profitable SaaS deals for sale under 3x multiple").
- Benchmarking two SaaS products or ranking startups in a category (e.g. AI, developer tools).

## When NOT to Use
- General conversations, casual greetings, or companies that are not on TrustMRR (e.g. public Fortune 500s).
- Technical code audits or DNS queries.

## Parameters
- `slug` (string, for `trustmrr_get_startup`): Startup slug on TrustMRR (e.g. `'shipfast'`, `'chatbase'`).
- `category` (string, optional): SaaS category (e.g. `'ai'`, `'developer-tools'`, `'marketing'`).
- `min_mrr_usd` / `max_multiple` / `is_for_sale`: Filtering criteria.

## Key Returns
- `mrr` / `last_30d_revenue` / `total_revenue`: Verified Stripe/payment numbers.
- `arr_multiple`: Asking price / ARR ratio.
- `growth_30d`: Month-over-month growth percentage.
- `active_subscriptions` / `paying_customers`: Paying user base count.
