"""End-to-end pipeline tests against a mocked API.

These cover the parts most likely to break silently: the two-pass join, the
confidence policy, rule firing, and the fact that no uncited number reaches a
memo. No network, no API key.
"""

from __future__ import annotations

import pytest

from app.analysis import llm_flags, rules, scoring
from app.config import load_thesis
from app.models.citation import Confidence
from app.models.flags import DetectionSource, FlagCategory, Severity
from app.models.memo import Recommendation
from app.pipeline import draft, extract
from tests import fixtures as fx


@pytest.fixture
def profile():
    facts = extract.parse_cited_response(fx.brightpath_cited_response())
    return extract.build_profile(fx.brightpath_raw(), facts)


# --------------------------------------------------------------- extraction


def test_cited_pass_parses_fields_and_pages():
    facts = extract.parse_cited_response(fx.brightpath_cited_response())
    assert facts["latest_revenue"].as_float() == 9.3
    assert facts["latest_revenue"].citations[0].page == 9
    assert not facts["customer_count"].found


def test_agreeing_passes_yield_high_confidence(profile):
    assert profile.company_name.value == "BrightPath Dental Partners"
    assert profile.company_name.confidence == Confidence.HIGH
    assert profile.company_name.citations[0].page == 3


def test_disagreeing_passes_downgrade_and_record_conflict(profile):
    """Structured value wins, but the field is flagged for review with the
    disagreement visible — not silently resolved."""
    assert profile.top_customer_pct.value == 34.6
    assert profile.top_customer_pct.confidence == Confidence.MEDIUM
    assert "disagree" in (profile.top_customer_pct.note or "")
    assert profile.top_customer_pct.needs_review


def test_uncited_value_is_low_confidence_and_needs_review(profile):
    assert profile.acquisitions_completed.value == 7
    assert profile.acquisitions_completed.confidence == Confidence.LOW
    assert profile.acquisitions_completed.needs_review


def test_value_the_cited_pass_could_not_find_is_kept_but_unverified(profile):
    """The structured pass found customer_count; the cited pass returned
    NOT_FOUND. The value is retained (it is schema-valid and may well be right)
    but is marked unverified, routed to review, and rendered so a reader cannot
    mistake it for a sourced figure."""
    assert profile.customer_count.value == 5
    assert profile.customer_count.confidence == Confidence.LOW
    assert profile.customer_count.needs_review
    assert not profile.customer_count.is_sourced
    assert "uncited" in profile.customer_count.render()


def test_sourced_value_renders_with_page_marker(profile):
    assert profile.company_name.is_sourced
    assert "[p.3]" in profile.company_name.render()


def test_derived_metrics_are_computed_not_extracted(profile):
    assert profile.revenue_cagr == pytest.approx(16.9, abs=0.2)
    assert profile.addback_pct_of_ebitda == pytest.approx(29.9, abs=0.5)


# -------------------------------------------------------------------- rules


def test_rules_fire_on_concentration_and_inorganic_growth(profile):
    flags = rules.run(profile)
    ids = {f.rule_id for f in flags}
    assert "customer_concentration" in ids
    assert "top_five_concentration" in ids
    assert "inorganic_growth" in ids
    conc = next(f for f in flags if f.rule_id == "customer_concentration")
    assert conc.severity == Severity.HIGH
    assert conc.source == DetectionSource.RULE


def test_rule_flags_carry_citations(profile):
    """A rule finding must be as traceable as a model finding."""
    flags = rules.run(profile)
    conc = next(f for f in flags if f.rule_id == "customer_concentration")
    assert conc.citations and conc.citations[0].page == 11


def test_recurring_one_time_addback_rule():
    from app.models.citation import Cited
    from app.models.deal import DealProfile, EbitdaAddback, FiscalYear

    p = DealProfile()
    p.financials = Cited(value=[FiscalYear(year=2024, revenue=24.3, adjusted_ebitda=4.18)])
    p.addbacks = Cited(
        value=[
            EbitdaAddback(
                description="One-time platform migration",
                amount=0.62,
                years_recurring=3,
                is_labelled_one_time=True,
            ),
            EbitdaAddback(
                description="One-time recruiting",
                amount=0.28,
                years_recurring=3,
                is_labelled_one_time=True,
            ),
        ]
    )
    flags = rules.run(p)
    hit = next(f for f in flags if f.rule_id == "recurring_one_time_addbacks")
    assert hit.severity == Severity.HIGH
    assert "0.90" in hit.title or "0.9" in hit.title


def test_clean_profile_produces_no_high_severity_flags():
    from app.models.citation import Cited
    from app.models.deal import DealProfile, FiscalYear

    p = DealProfile()
    p.financials = Cited(
        value=[
            FiscalYear(year=2022, revenue=14.2, adjusted_ebitda=2.13),
            FiscalYear(year=2024, revenue=18.4, adjusted_ebitda=3.06),
        ]
    )
    p.top_customer_pct = Cited(value=11.2)
    p.top_five_customer_pct = Cited(value=36.9)
    p.days_sales_outstanding = Cited(value=[41.0, 38.2])
    assert [f for f in rules.run(p) if f.severity == Severity.HIGH] == []


def test_dso_deterioration_rule():
    from app.models.citation import Cited
    from app.models.deal import DealProfile

    p = DealProfile()
    p.days_sales_outstanding = Cited(value=[38.0, 49.5, 67.3])
    hit = next(f for f in rules.run(p) if f.rule_id == "dso_deterioration")
    assert hit.severity == Severity.HIGH
    assert hit.category == FlagCategory.WORKING_CAPITAL


# ------------------------------------------------------------------ scoring


def test_thesis_dealbreaker_is_recorded(profile):
    fit = scoring.run(profile, load_thesis("services_rollup"))
    assert "Customer concentration ceiling" in fit.dealbreakers_hit
    assert fit.score < 60


def test_unevaluable_criteria_excluded_not_failed():
    """A missing disclosure is a review item, not evidence against the company."""
    from app.models.deal import DealProfile

    fit = scoring.run(DealProfile(), load_thesis("services_rollup"))
    assert fit.coverage == 0.0
    assert all(c.passed is None for c in fit.criteria)


def test_same_profile_scores_differently_across_theses(profile):
    """The engine is thesis-driven, not hard-coded to one firm's box."""
    a = scoring.run(profile, load_thesis("services_rollup"))
    b = scoring.run(profile, load_thesis("software_buyout"))
    assert a.score != b.score


# ------------------------------------------------------------- model flags


def test_model_findings_parse_with_citations():
    flags = llm_flags.parse_findings(fx.brightpath_flags_response())
    assert len(flags) == 2
    top = flags[0]
    assert top.category == FlagCategory.INTERNAL_INCONSISTENCY
    assert top.severity == Severity.HIGH
    assert top.source == DetectionSource.MODEL
    assert {c.page for c in top.citations} == {3, 11}


def test_dedupe_drops_model_restatement_of_rule_finding(profile):
    rule_flags = rules.run(profile)
    model_flags = llm_flags.parse_findings(fx.brightpath_flags_response())
    merged = llm_flags.dedupe(rule_flags, model_flags)
    assert len(merged) <= len(rule_flags) + len(model_flags)
    assert all(f in merged for f in rule_flags)


# ------------------------------------------------------------------- draft


def test_recommendation_is_computed_not_model_generated(profile):
    fit = scoring.run(profile, load_thesis("services_rollup"))
    flags = rules.run(profile)
    rec, why = draft.decide(fit, flags)
    assert rec == Recommendation.PASS
    assert "dealbreaker" in why.lower()


def test_low_coverage_forces_more_info():
    from app.models.deal import DealProfile

    fit = scoring.run(DealProfile(), load_thesis("services_rollup"))
    rec, why = draft.decide(fit, [])
    assert rec == Recommendation.MORE_INFO
    assert "incomplete" in why.lower()


def test_context_marks_pending_fields_and_carries_pages(profile):
    fit = scoring.run(profile, load_thesis("services_rollup"))
    ctx = draft.build_context(profile, fit, rules.run(profile))
    assert "FIELDS PENDING REVIEW" in ctx
    assert "acquisitions_completed" in ctx
    assert "[p.9]" in ctx


def test_section_parsing():
    secs = draft.parse_sections(fx.draft_response().content[0].text)
    assert set(secs) >= {
        "recommendation_rationale",
        "business_overview",
        "financial_summary",
        "thesis_commentary",
        "diligence_priorities",
    }
    assert "34.6%" in secs["recommendation_rationale"]
