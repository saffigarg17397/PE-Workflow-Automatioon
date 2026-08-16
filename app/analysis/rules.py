"""Deterministic red-flag detection.

These run on extracted structured data. They are cheap, reproducible, and cannot
hallucinate — which is why they own every finding that is fundamentally
arithmetic. The LLM pass (llm_flags.py) is scoped to what these cannot see.

Each rule carries an id so a finding can be traced to the exact threshold that
produced it, and so thresholds can be tuned without touching detection logic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.models.citation import Citation
from app.models.deal import DealProfile
from app.models.flags import DetectionSource, FlagCategory, RedFlag, Severity

# Thresholds live here rather than inline so they're reviewable in one place.
THRESHOLDS = {
    "top_customer_high": 30.0,
    "top_customer_medium": 20.0,
    "top_five_high": 70.0,
    "top_five_medium": 50.0,
    "addback_pct_high": 30.0,
    "addback_pct_medium": 20.0,
    "cagr_low": 5.0,
    "margin_compression_pts": 1.0,
    "dso_increase_pct": 25.0,
    "dso_absolute_high": 60.0,
}


@dataclass
class Rule:
    id: str
    fn: Callable[[DealProfile], RedFlag | None]


def _cites(*fields: object) -> list[Citation]:
    """Reuse the citations already attached to the fields a rule fired on, so a
    rule-based finding is as traceable as a model-based one."""
    out: list[Citation] = []
    for f in fields:
        cits = getattr(f, "citations", None)
        if cits:
            out.extend(cits[:2])
    return out[:3]


def r_customer_concentration(p: DealProfile) -> RedFlag | None:
    pct = p.top_customer_pct.value
    if pct is None:
        return None
    if pct >= THRESHOLDS["top_customer_high"]:
        sev = Severity.HIGH
    elif pct >= THRESHOLDS["top_customer_medium"]:
        sev = Severity.MEDIUM
    else:
        return None
    name = ""
    customers = p.top_customers.value or []
    if customers:
        name = f" ({customers[0].name})"
    return RedFlag(
        category=FlagCategory.CUSTOMER_CONCENTRATION,
        severity=sev,
        title=f"Single-customer concentration at {pct:.1f}% of revenue{name}",
        detail=(
            f"The largest customer represents {pct:.1f}% of revenue, above the "
            f"{THRESHOLDS['top_customer_medium']:.0f}% screening threshold. Loss or "
            f"repricing of this relationship would materially affect earnings."
        ),
        source=DetectionSource.RULE,
        rule_id="customer_concentration",
        citations=_cites(p.top_customer_pct),
    )


def r_top_five_concentration(p: DealProfile) -> RedFlag | None:
    pct = p.top_five_customer_pct.value
    if pct is None:
        return None
    if pct >= THRESHOLDS["top_five_high"]:
        sev = Severity.HIGH
    elif pct >= THRESHOLDS["top_five_medium"]:
        sev = Severity.MEDIUM
    else:
        return None
    return RedFlag(
        category=FlagCategory.CUSTOMER_CONCENTRATION,
        severity=sev,
        title=f"Top-five customer concentration at {pct:.1f}% of revenue",
        detail=(
            f"The five largest relationships account for {pct:.1f}% of revenue. "
            f"Revenue durability depends on a narrow set of counterparties."
        ),
        source=DetectionSource.RULE,
        rule_id="top_five_concentration",
        citations=_cites(p.top_five_customer_pct),
    )


def r_addback_aggression(p: DealProfile) -> RedFlag | None:
    pct = p.addback_pct_of_ebitda
    if pct is None:
        return None
    if pct >= THRESHOLDS["addback_pct_high"]:
        sev = Severity.HIGH
    elif pct >= THRESHOLDS["addback_pct_medium"]:
        sev = Severity.MEDIUM
    else:
        return None
    total = sum(a.amount for a in (p.addbacks.value or []))
    return RedFlag(
        category=FlagCategory.EARNINGS_QUALITY,
        severity=sev,
        title=f"Add-backs represent {pct:.1f}% of adjusted EBITDA",
        detail=(
            f"${total:.2f}M of adjustments bridge reported to adjusted EBITDA. "
            f"The adjusted figure is the basis for the asking multiple, so the "
            f"quality of these adjustments drives the effective entry price."
        ),
        source=DetectionSource.RULE,
        rule_id="addback_aggression",
        citations=_cites(p.addbacks, p.financials),
    )


def r_recurring_one_time_addbacks(p: DealProfile) -> RedFlag | None:
    """The classic QoE tell: 'one-time' costs that recur every year.

    Detectable arithmetically because the CIM's own bridge table states which
    periods each adjustment covers.
    """
    offenders = [
        a
        for a in (p.addbacks.value or [])
        if a.is_labelled_one_time and a.years_recurring >= 2
    ]
    if not offenders:
        return None
    total = sum(a.amount for a in offenders)
    names = "; ".join(f"{a.description} (${a.amount:.2f}M, {a.years_recurring} yrs)" for a in offenders[:3])
    return RedFlag(
        category=FlagCategory.EARNINGS_QUALITY,
        severity=Severity.HIGH,
        title=(
            f"{len(offenders)} add-back{'s' if len(offenders) != 1 else ''} labelled "
            f"one-time recur{'' if len(offenders) != 1 else 's'} across multiple years (${total:.2f}M)"
        ),
        detail=(
            f"The following adjustments are described as one-time or non-recurring "
            f"but appear in more than one historical period: {names}. If these are "
            f"in fact operating costs, adjusted EBITDA is overstated by up to "
            f"${total:.2f}M and the effective entry multiple is correspondingly higher."
        ),
        source=DetectionSource.RULE,
        rule_id="recurring_one_time_addbacks",
        citations=_cites(p.addbacks),
    )


def r_weak_growth(p: DealProfile) -> RedFlag | None:
    cagr = p.revenue_cagr
    if cagr is None or cagr >= THRESHOLDS["cagr_low"]:
        return None
    return RedFlag(
        category=FlagCategory.GROWTH,
        severity=Severity.MEDIUM,
        title=f"Revenue CAGR of {cagr:.1f}% below screening floor",
        detail=(
            f"Revenue has compounded at {cagr:.1f}% over the periods presented, "
            f"below the {THRESHOLDS['cagr_low']:.0f}% threshold."
        ),
        source=DetectionSource.RULE,
        rule_id="weak_growth",
        citations=_cites(p.financials),
    )


def r_inorganic_growth(p: DealProfile) -> RedFlag | None:
    """Growth that may be bought rather than earned.

    Not a judgment about whether roll-up growth is bad — it's a flag that the
    headline CAGR needs decomposing before it can be underwritten.
    """
    n = p.acquisitions_completed.value
    cagr = p.revenue_cagr
    if not n or n < 3 or cagr is None:
        return None
    years = p.financials.value or []
    if len(years) < 2:
        return None
    return RedFlag(
        category=FlagCategory.GROWTH,
        severity=Severity.HIGH if n >= 5 else Severity.MEDIUM,
        title=f"Headline {cagr:.1f}% CAGR spans {n} completed acquisitions",
        detail=(
            f"The Company has completed {n} acquisitions. Reported growth of "
            f"{cagr:.1f}% is therefore a blend of organic and acquired revenue, and "
            f"the organic rate is not disclosed. Same-store or pro-forma organic "
            f"growth must be established before the growth story can be underwritten."
        ),
        source=DetectionSource.RULE,
        rule_id="inorganic_growth",
        citations=_cites(p.acquisitions_completed, p.financials),
    )


def r_margin_compression(p: DealProfile) -> RedFlag | None:
    trend = p.margin_trend
    if len(trend) < 2:
        return None
    first, last = trend[0][1], trend[-1][1]
    delta = last - first
    if delta > -THRESHOLDS["margin_compression_pts"]:
        return None
    return RedFlag(
        category=FlagCategory.MARGIN,
        severity=Severity.MEDIUM,
        title=f"EBITDA margin compressed {abs(delta):.1f}pts ({first:.1f}% to {last:.1f}%)",
        detail=(
            f"Adjusted EBITDA margin declined from {first:.1f}% in {trend[0][0]} to "
            f"{last:.1f}% in {trend[-1][0]} despite revenue growth, suggesting cost "
            f"growth outpacing pricing."
        ),
        source=DetectionSource.RULE,
        rule_id="margin_compression",
        citations=_cites(p.financials),
    )


def r_dso_deterioration(p: DealProfile) -> RedFlag | None:
    dso = p.days_sales_outstanding.value or []
    if len(dso) < 2:
        return None
    first, last = dso[0], dso[-1]
    if first <= 0:
        return None
    increase = 100 * (last - first) / first
    if increase < THRESHOLDS["dso_increase_pct"]:
        return None
    sev = (
        Severity.HIGH
        if last >= THRESHOLDS["dso_absolute_high"] or increase >= 50
        else Severity.MEDIUM
    )
    return RedFlag(
        category=FlagCategory.WORKING_CAPITAL,
        severity=sev,
        title=f"DSO deteriorated {increase:.0f}% ({first:.0f} to {last:.0f} days)",
        detail=(
            f"Days sales outstanding rose from {first:.0f} to {last:.0f} days across "
            f"the periods presented. Cash conversion is degrading while reported "
            f"EBITDA grows — a gap that typically indicates either collection "
            f"problems or revenue recognition that leads cash."
        ),
        source=DetectionSource.RULE,
        rule_id="dso_deterioration",
        citations=_cites(p.days_sales_outstanding),
    )


def r_founder_departing(p: DealProfile) -> RedFlag | None:
    leaving = [
        m
        for m in (p.management.value or [])
        if m.staying_post_close is False and (m.is_founder or "chief executive" in m.role.lower())
    ]
    if not leaving:
        return None
    who = "; ".join(f"{m.name} ({m.role})" for m in leaving)
    return RedFlag(
        category=FlagCategory.KEY_PERSON,
        severity=Severity.HIGH,
        title=f"Founder/CEO not staying post-close: {who}",
        detail=(
            f"{who} is not continuing with the business after a transaction. "
            f"Where relationship ownership and operating knowledge sit with a "
            f"departing principal, continuity risk is a first-order diligence item."
        ),
        source=DetectionSource.RULE,
        rule_id="founder_departing",
        citations=_cites(p.management),
    )


def r_thin_bench(p: DealProfile) -> RedFlag | None:
    mgmt = p.management.value or []
    if len(mgmt) < 2:
        return None
    staying = [m for m in mgmt if m.staying_post_close is not False]
    tenures = [m.tenure_years for m in staying if m.tenure_years is not None]
    if not tenures or len(staying) == len(mgmt):
        return None
    avg = sum(tenures) / len(tenures)
    if avg >= 4:
        return None
    return RedFlag(
        category=FlagCategory.KEY_PERSON,
        severity=Severity.MEDIUM,
        title=f"Remaining management averages {avg:.1f} years tenure",
        detail=(
            f"Excluding departing principals, the continuing management team averages "
            f"{avg:.1f} years with the Company. Institutional knowledge may be "
            f"concentrated in the departing party."
        ),
        source=DetectionSource.RULE,
        rule_id="thin_bench",
        citations=_cites(p.management),
    )


RULES: list[Rule] = [
    Rule("customer_concentration", r_customer_concentration),
    Rule("top_five_concentration", r_top_five_concentration),
    Rule("addback_aggression", r_addback_aggression),
    Rule("recurring_one_time_addbacks", r_recurring_one_time_addbacks),
    Rule("weak_growth", r_weak_growth),
    Rule("inorganic_growth", r_inorganic_growth),
    Rule("margin_compression", r_margin_compression),
    Rule("dso_deterioration", r_dso_deterioration),
    Rule("founder_departing", r_founder_departing),
    Rule("thin_bench", r_thin_bench),
]


def run(profile: DealProfile) -> list[RedFlag]:
    """Fire every rule. A rule that cannot evaluate returns None and is skipped —
    missing data is a review item, not a finding."""
    out: list[RedFlag] = []
    for rule in RULES:
        try:
            flag = rule.fn(profile)
        except Exception:  # noqa: BLE001 - one bad rule must not sink the run
            continue
        if flag is not None:
            out.append(flag)
    return out
