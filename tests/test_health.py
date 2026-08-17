"""Extraction-health signalling.

The failure mode under test: the cited pass drifts format, the parser matches
nothing, and the memo renders normally with zero provenance. That is
indistinguishable from success at a glance, so it must be loud.
"""

from __future__ import annotations

from unittest.mock import patch

from app.export import markdown
from app.llm.client import LLMClient
from app.models.memo import ExtractionHealth
from app.pipeline import extract, orchestrator
from tests import fixtures as fx


def _run_with(cited_response):
    def fs(self, s, d, p, sc):
        self._record(s, "high", fx.FakeUsage(), 100)
        return fx.brightpath_raw()

    def fc(self, s, d, p):
        self._record(s, "high", fx.FakeUsage(), 100)
        return cited_response if s == "extract_cited" else fx.brightpath_flags_text()

    def fk(self, s, d, p, max_tokens=16000):
        self._record(s, "high", fx.FakeUsage(), 100)
        return fx.draft_text()

    with (
        patch.object(
            LLMClient,
            "__init__",
            lambda s, model=None: (
                setattr(s, "model", "gemini-3.6-flash"),
                setattr(s, "telemetry", []),
                None,
            )[-1],
        ),
        patch.object(LLMClient, "extract_structured", fs),
        patch.object(LLMClient, "extract_cited", fc),
        patch.object(LLMClient, "complete", fk),
    ):
        return orchestrator.run("data/sample_cims/brightpath_dental.pdf")


def test_healthy_run_is_not_degraded():
    run = _run_with(fx.brightpath_cited_text())
    h = run.memo.health
    assert not h.degraded
    assert h.warning is None
    # Comfortably clear of the 40% floor, not one point from it — the metric
    # measures citable fields, not every field.
    assert h.citation_rate > 55
    assert h.facts_located > 5
    assert h.fields_citable < h.fields_extracted


def test_total_parser_failure_is_flagged_degraded():
    """The cited pass returns prose instead of the requested format — the exact
    silent-degradation case."""
    prose = "I reviewed the document but could not format a response."
    run = _run_with(prose)
    h = run.memo.health

    assert h.facts_located == 0
    assert h.degraded
    assert "returned nothing the parser recognised" in h.warning
    # And it must be visible in the rendered memo, not just the object.
    assert "EXTRACTION DEGRADED" in markdown.render(run)


def test_partial_citation_collapse_is_flagged():
    """Values extract fine but almost none carry a source page."""
    thin = "FIELD: company_name | VALUE: BrightPath Dental Partners"
    run = _run_with(thin)
    assert run.memo.health.degraded
    assert "carry a source page" in run.memo.health.warning


def test_health_counts_are_consistent():
    run = _run_with(fx.brightpath_cited_text())
    h = run.memo.health
    assert h.fields_cited <= h.fields_citable <= h.fields_extracted <= h.fields_total
    assert h.flags_parsed == 2  # from the fixture


def test_uncitable_fields_excluded_from_the_denominator():
    """Narrative fields are filled by the structured pass only and are never
    asked of the citation pass. Counting them would peg a healthy run at the
    degradation threshold and make the signal useless."""
    from app.pipeline.extract import CITABLE_ATTRS

    assert "description" not in CITABLE_ATTRS
    assert "management" not in CITABLE_ATTRS
    assert "top_customer_pct" in CITABLE_ATTRS


def test_empty_health_reports_degraded_not_crash():
    h = ExtractionHealth()
    assert h.citation_rate == 0.0
    assert h.extraction_rate == 0.0
    assert h.degraded


def test_health_appears_in_section_seven():
    run = _run_with(fx.brightpath_cited_text())
    md = markdown.render(run)
    assert "Extraction health:" in md
    assert "located by the citation pass" in md


def test_health_of_is_pure_and_matches_profile():
    facts = extract.parse_cited_response(fx.brightpath_cited_text(), fx.brightpath_pages())
    profile = extract.build_profile(fx.brightpath_raw(), facts)
    h = extract.health_of(profile, facts)
    cited = sum(
        1
        for n in type(profile).model_fields
        if getattr(profile, n).value is not None and getattr(profile, n).citations
    )
    assert h.fields_cited == cited
