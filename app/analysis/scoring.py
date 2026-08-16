"""Thesis fit scoring.

The criteria come from YAML, not code, so the same engine screens a services
rollup and a software buyout without modification. `_FIELD_RESOLVERS` is the only
coupling between a thesis file and the deal schema — adding a criterion means
adding a resolver, not editing scoring logic.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.config import Thesis, ThesisCriterion
from app.models.deal import DealProfile
from app.models.flags import CriterionResult, ThesisFit


def _latest_revenue(p: DealProfile) -> float | None:
    y = p.latest_year
    return y.revenue if y else None


def _latest_margin(p: DealProfile) -> float | None:
    y = p.latest_year
    return y.ebitda_margin if y else None


def _management_staying(p: DealProfile) -> Any:
    mgmt = p.management.value or []
    if not mgmt:
        return None
    # "exists" semantics: true when no key principal is departing.
    leaving = [m for m in mgmt if m.staying_post_close is False]
    return None if all(m.staying_post_close is None for m in mgmt) else (not leaving)


_FIELD_RESOLVERS: dict[str, Callable[[DealProfile], Any]] = {
    "latest_revenue": _latest_revenue,
    "latest_ebitda_margin": _latest_margin,
    "revenue_cagr": lambda p: p.revenue_cagr,
    "top_customer_pct": lambda p: p.top_customer_pct.value,
    "top_five_customer_pct": lambda p: p.top_five_customer_pct.value,
    "recurring_revenue_pct": lambda p: p.recurring_revenue_pct.value,
    "addback_pct_of_ebitda": lambda p: p.addback_pct_of_ebitda,
    "asking_multiple": lambda p: p.asking_multiple.value,
    "management_staying": _management_staying,
}


def _evaluate(c: ThesisCriterion, actual: Any) -> bool | None:
    """None means 'could not evaluate' — excluded from the score rather than
    counted as a failure. A missing disclosure is a review item, not evidence
    against the company."""
    if actual is None:
        return None
    try:
        if c.op == "gte":
            return float(actual) >= float(c.value)  # type: ignore[arg-type]
        if c.op == "lte":
            return float(actual) <= float(c.value)  # type: ignore[arg-type]
        if c.op == "between":
            lo, hi = c.value  # type: ignore[misc]
            return float(lo) <= float(actual) <= float(hi)
        if c.op == "in":
            return str(actual) in (c.value or [])  # type: ignore[operator]
        if c.op == "exists":
            return bool(actual)
    except (TypeError, ValueError):
        return None
    return None


def _fmt(actual: Any) -> str:
    if actual is None:
        return "not disclosed"
    if isinstance(actual, bool):
        return "yes" if actual else "no"
    if isinstance(actual, float):
        return f"{actual:,.1f}"
    return str(actual)


def run(profile: DealProfile, thesis: Thesis) -> ThesisFit:
    results: list[CriterionResult] = []
    dealbreakers: list[str] = []

    for c in thesis.criteria:
        resolver = _FIELD_RESOLVERS.get(c.field)
        actual = resolver(profile) if resolver else None
        passed = _evaluate(c, actual)

        results.append(
            CriterionResult(
                name=c.name,
                passed=passed,
                actual=_fmt(actual),
                target=c.target_label or f"{c.op} {c.value}",
                weight=c.weight,
                is_dealbreaker=c.dealbreaker,
            )
        )
        if c.dealbreaker and passed is False:
            dealbreakers.append(c.name)

    return ThesisFit(thesis_name=thesis.name, criteria=results, dealbreakers_hit=dealbreakers)
