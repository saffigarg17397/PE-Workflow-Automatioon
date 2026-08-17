"""Robustness against real model output and degenerate data.

Every test here corresponds to a defect found by adversarially probing the
pipeline rather than by running the happy path. The parser cases matter most:
the model is asked for a delimited format, will drift from it, and a parser that
silently misses a line degrades the whole document to LOW confidence with no
error anyone would notice.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.analysis import rules, scoring
from app.analysis.llm_flags import parse_findings
from app.config import load_thesis
from app.models.citation import Citation, Cited, Confidence
from app.models.deal import DealProfile, EbitdaAddback, FiscalYear
from app.pipeline import draft, extract, ingest
from app.pipeline.extract import CitedFact, parse_cited_response
from tests.fixtures import FakeCitation, FakeResponse, FakeTextBlock

# ------------------------------------------------------- format drift: FIELD


@pytest.mark.parametrize(
    "line",
    [
        "FIELD: latest_revenue | VALUE: 9.3",
        "**FIELD:** latest_revenue **| VALUE:** 9.3",
        "**FIELD**: latest_revenue | **VALUE**: 9.3",
        "- FIELD: latest_revenue | VALUE: 9.3",
        "*FIELD:* latest_revenue *|* *VALUE:* 9.3",
        "`FIELD:` latest_revenue | `VALUE:` 9.3",
        "FIELD:latest_revenue|VALUE:9.3",
        "  FIELD:  latest_revenue  |  VALUE:  9.3  ",
        "field: latest_revenue | value: 9.3",
    ],
)
def test_field_line_survives_markdown_emphasis(line):
    facts = parse_cited_response(FakeResponse(content=[FakeTextBlock(text=line)]))
    assert facts["latest_revenue"].as_float() == 9.3


def test_unknown_field_names_are_ignored_not_crashed():
    facts = parse_cited_response(
        FakeResponse(content=[FakeTextBlock(text="FIELD: invented_field | VALUE: 5")])
    )
    assert facts == {}


def test_prose_only_response_yields_no_facts():
    facts = parse_cited_response(
        FakeResponse(content=[FakeTextBlock(text="I was unable to locate these fields.")])
    )
    assert facts == {}


# ------------------------------------------------------------- value parsing


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9.3", 9.3),
        ("$9.3", 9.3),
        ("$9.3 million", 9.3),
        ("9,300", 9300.0),
        ("34.6%", 34.6),
        ("approximately 88", 88.0),
        ("9.3x", 9.3),
        ("-2.5", -2.5),
    ],
)
def test_currency_and_unit_formats_parse(raw, expected):
    assert CitedFact("f", raw, []).as_float() == expected


@pytest.mark.parametrize(
    "raw", ["NOT_FOUND", "n/a", "N/A", "none", "unknown", "not disclosed", "not stated", "-", ""]
)
def test_null_sentinels_are_not_treated_as_found(raw):
    """A model that answers 'n/a' has found nothing. Treating that as a value
    would attach a real citation to a non-answer."""
    f = CitedFact("f", raw, [])
    assert not f.found
    assert f.as_float() is None


def test_range_values_are_flagged_ambiguous():
    """Silently taking the first number of a range would put an arbitrary pick
    into a memo presented as sourced fact."""
    assert CitedFact("f", "1.2 to 1.5", []).is_ambiguous
    assert CitedFact("f", "between $9.3 and $9.8 million", []).is_ambiguous
    assert not CitedFact("f", "$9.3 million", []).is_ambiguous


def test_ambiguous_cited_value_downgrades_confidence():
    raw = extract.DealProfileRaw(employees=88)
    facts = {
        "employees": CitedFact(
            "employees", "88 to 92", [Citation(page=3, quote="88 to 92 employees")]
        )
    }
    p = extract.build_profile(raw, facts)
    assert p.employees.confidence == Confidence.MEDIUM
    assert "more than one figure" in (p.employees.note or "")


def test_unreadable_cited_value_downgrades_confidence():
    raw = extract.DealProfileRaw(employees=88)
    facts = {"employees": CitedFact("employees", "several dozen", [Citation(page=3, quote="x")])}
    p = extract.build_profile(raw, facts)
    assert p.employees.confidence == Confidence.MEDIUM
    assert "no readable figure" in (p.employees.note or "")


# ----------------------------------------------------- format drift: FINDING


@pytest.mark.parametrize(
    "line",
    [
        "FINDING: t | CATEGORY: growth | SEVERITY: high",
        "**FINDING:** t **| CATEGORY:** growth **| SEVERITY:** high",
        "- **FINDING**: t | **CATEGORY**: growth | **SEVERITY**: high",
    ],
)
def test_finding_line_survives_emphasis(line):
    flags = parse_findings(FakeResponse(content=[FakeTextBlock(text=line + "\nevidence")]))
    assert len(flags) == 1
    assert flags[0].title == "t"


def test_invalid_category_is_dropped_not_crashed():
    assert (
        parse_findings(
            FakeResponse(
                content=[FakeTextBlock(text="FINDING: x | CATEGORY: bogus | SEVERITY: high")]
            )
        )
        == []
    )


def test_invalid_severity_defaults_to_low():
    flags = parse_findings(
        FakeResponse(
            content=[FakeTextBlock(text="FINDING: x | CATEGORY: growth | SEVERITY: critical")]
        )
    )
    assert flags[0].severity.value == "low"


def test_no_findings_sentinel():
    assert parse_findings(FakeResponse(content=[FakeTextBlock(text="NO FINDINGS")])) == []


def test_citations_attach_to_the_finding_they_follow():
    flags = parse_findings(
        FakeResponse(
            content=[
                FakeTextBlock(
                    text="FINDING: a | CATEGORY: growth | SEVERITY: high\nbody",
                    citations=[FakeCitation(start_page_number=4, cited_text="q")],
                )
            ]
        )
    )
    assert flags[0].citations[0].page == 4


# ------------------------------------------------------ format drift: memo


@pytest.mark.parametrize(
    "heading",
    [
        "## business_overview",
        "## **business_overview**",
        "###   business_overview   ",
        "## business_overview:",
        "**business_overview**",
        "## Business_Overview",
    ],
)
def test_section_heading_variants(heading):
    assert "business_overview" in draft.parse_sections(heading + "\nbody text")


def test_bare_bold_line_is_not_treated_as_a_section():
    """Accepting any bolded word as a heading would silently truncate the
    section above it."""
    assert draft.parse_sections("## business_overview\na\n\n**Some emphasis**\nb") == {
        "business_overview": "a\n\n**Some emphasis**\nb"
    }


@pytest.mark.parametrize(
    "body",
    ["- one\n- two", "1. one\n2. two", "* one\n* two", "1) one\n2) two", "• one\n• two"],
)
def test_diligence_list_markers(body):
    from app.pipeline.draft import _LIST_ITEM

    lines = draft.parse_sections(f"## diligence_priorities\n{body}")["diligence_priorities"]
    items = [_LIST_ITEM.sub("", ln).strip() for ln in lines.splitlines() if _LIST_ITEM.match(ln)]
    assert items == ["one", "two"]


# --------------------------------------------------------- degenerate numbers


def test_negative_adjusted_ebitda_yields_no_addback_ratio():
    """A negative denominator produced a negative ratio, which sailed under the
    `<= 20%` earnings-quality threshold and scored a loss-making company as
    having clean earnings."""
    p = DealProfile()
    p.financials = Cited(value=[FiscalYear(year=2024, revenue=10.0, adjusted_ebitda=-2.0)])
    p.addbacks = Cited(value=[EbitdaAddback(description="x", amount=1.0)])
    assert p.addback_pct_of_ebitda is None

    fit = scoring.run(p, load_thesis("services_rollup"))
    eq = next(c for c in fit.criteria if c.name == "Earnings quality")
    assert eq.passed is None, "must be unevaluable, not a pass"


@pytest.mark.parametrize(
    "years",
    [
        [FiscalYear(year=2022, revenue=0.0), FiscalYear(year=2024, revenue=5.0)],
        [FiscalYear(year=2022, revenue=-1.0), FiscalYear(year=2024, revenue=5.0)],
        [FiscalYear(year=2024, revenue=5.0)],
        [FiscalYear(year=2024, revenue=5.0), FiscalYear(year=2024, revenue=6.0)],
    ],
)
def test_cagr_is_none_on_degenerate_series(years):
    p = DealProfile()
    p.financials = Cited(value=years)
    assert p.revenue_cagr is None


def test_zero_revenue_yields_no_margin():
    p = DealProfile()
    p.financials = Cited(value=[FiscalYear(year=2024, revenue=0.0, adjusted_ebitda=1.0)])
    assert p.latest_year.ebitda_margin is None


def test_rules_never_raise_on_an_empty_profile():
    assert rules.run(DealProfile()) == []


# --------------------------------------------------------------- ingest


def test_corrupt_pdf_raises_value_error_not_library_error():
    """Callers all handle ValueError; a raw PdfStreamError surfaced to the web
    UI as 'Unexpected error'."""
    p = Path(tempfile.mkdtemp()) / "broken.pdf"
    p.write_bytes(b"this is not a pdf")
    with pytest.raises(ValueError, match="not a readable PDF"):
        ingest.run(p)


def test_empty_file_raises_value_error():
    p = Path(tempfile.mkdtemp()) / "empty.pdf"
    p.write_bytes(b"")
    with pytest.raises(ValueError):
        ingest.run(p)


def test_non_pdf_rejected_by_suffix():
    p = Path(tempfile.mkdtemp()) / "notes.txt"
    p.write_text("hello")
    with pytest.raises(ValueError, match="Expected a PDF"):
        ingest.run(p)


def test_missing_file_raises_filenotfound():
    with pytest.raises(FileNotFoundError):
        ingest.run("/definitely/not/here.pdf")


# ------------------------------------------------------------ context size


def test_build_context_caps_oversized_scalars():
    p = DealProfile()
    p.description = Cited(value="x" * 50_000)
    ctx = draft.build_context(p, scoring.run(p, load_thesis("services_rollup")), [])
    assert len(ctx) < 5_000
