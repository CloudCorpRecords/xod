from typing import Dict, Any, Optional

def calculate_saas_metrics(
    mrr_cents: Optional[int] = None,
    revenue_30d_cents: Optional[int] = None,
    asking_price_cents: Optional[int] = None,
    visitors_30d: Optional[int] = None,
    customers: Optional[int] = None,
    growth_30d_pct: Optional[float] = None,
    profit_margin_pct: Optional[float] = None
) -> Dict[str, Any]:
    """Calculates ARR, implied multiples, Rule of 40, ARPU, and 12-month projections."""
    mrr_dollars = (mrr_cents / 100) if mrr_cents else ((revenue_30d_cents / 100) if revenue_30d_cents else 0.0)
    arr_dollars = mrr_dollars * 12.0
    asking_price_dollars = (asking_price_cents / 100) if asking_price_cents else None

    # Multiple on ARR
    arr_multiple = None
    if asking_price_dollars and arr_dollars > 0:
        arr_multiple = round(asking_price_dollars / arr_dollars, 2)

    # ARPU (Average Revenue Per User/Customer)
    arpu_dollars = None
    if customers and customers > 0 and mrr_dollars > 0:
        arpu_dollars = round(mrr_dollars / customers, 2)

    # Revenue per visitor
    rpv_dollars = None
    if visitors_30d and visitors_30d > 0 and revenue_30d_cents:
        rpv_dollars = round((revenue_30d_cents / 100) / visitors_30d, 3)

    # Rule of 40 (Growth rate % + Profit margin %)
    rule_of_40 = None
    if growth_30d_pct is not None and profit_margin_pct is not None:
        # Annualized growth approx
        annual_growth = ((1 + (growth_30d_pct / 100 if growth_30d_pct > 1 else growth_30d_pct)) ** 12 - 1) * 100
        rule_of_40 = round(annual_growth + profit_margin_pct, 1)

    # 12-Month Projections based on 30d growth
    growth_decimal = (growth_30d_pct / 100) if (growth_30d_pct and growth_30d_pct > 1) else (growth_30d_pct or 0.0)
    # Cap compounding projection to reasonable bound
    safe_growth = max(-0.5, min(growth_decimal, 0.4))
    projected_mrr_12m = mrr_dollars * ((1 + safe_growth) ** 12) if mrr_dollars > 0 else 0

    return {
        "mrr_usd": round(mrr_dollars, 2),
        "arr_usd": round(arr_dollars, 2),
        "arr_formatted": f"${arr_dollars:,.0f}",
        "asking_price_usd": round(asking_price_dollars, 2) if asking_price_dollars else None,
        "arr_multiple": arr_multiple,
        "arpu_usd": arpu_dollars,
        "revenue_per_visitor_usd": rpv_dollars,
        "rule_of_40_score": rule_of_40,
        "projected_arr_12m_usd": round(projected_mrr_12m * 12, 2) if mrr_dollars > 0 else None,
        "benchmarks": {
            "multiple_verdict": (
                "Attractively priced (< 2.5x ARR)" if arr_multiple and arr_multiple < 2.5
                else "Fair market standard (2.5x - 5.0x ARR)" if arr_multiple and arr_multiple <= 5.0
                else "Premium valuation (> 5.0x ARR)" if arr_multiple
                else "N/A"
            ),
            "efficiency_verdict": (
                "Elite Rule of 40 performance" if rule_of_40 and rule_of_40 >= 40
                else "Healthy trajectory" if rule_of_40 and rule_of_40 >= 20
                else "Underperforming benchmarks" if rule_of_40
                else "N/A"
            )
        }
    }
