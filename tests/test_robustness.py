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
from app.models.citation import Cited, Confidence
from app.models.deal import DealProfile, EbitdaAddback, FiscalYear
from app.pipeline import draft, extract, ingest
from app.pipeline.extract import CitedFact, QuoteVerifier, parse_cited_response

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
    facts = parse_cited_response(line, [])
    assert facts["latest_revenue"].as_float() == 9.3


def test_unknown_field_names_are_ignored_not_crashed():
    facts = parse_cited_response("FIELD: invented_field | VALUE: 5", [])
    assert facts == {}


def test_prose_only_response_yields_no_facts():
    facts = parse_cited_response("I was unable to locate these fields.", [])
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
    assert CitedFact("f", raw).as_float() == expected


@pytest.mark.parametrize(
    "raw", ["NOT_FOUND", "n/a", "N/A", "none", "unknown", "not disclosed", "not stated", "-", ""]
)
def test_null_sentinels_are_not_treated_as_found(raw):
    """A model that answers 'n/a' has found nothing. Treating that as a value
    would attach a real citation to a non-answer."""
    f = CitedFact("f", raw)
    assert not f.found
    assert f.as_float() is None


def test_range_values_are_flagged_ambiguous():
    """Silently taking the first number of a range would put an arbitrary pick
    into a memo presented as sourced fact."""
    assert CitedFact("f", "1.2 to 1.5").is_ambiguous
    assert CitedFact("f", "between $9.3 and $9.8 million").is_ambiguous
    assert not CitedFact("f", "$9.3 million").is_ambiguous


def _fact(field: str, value: str, quote: str, pages: list[str], page: int = 1) -> CitedFact:
    """Build a verified CitedFact the way the pipeline does — through the verifier."""
    return CitedFact(
        field, value, claimed_page=page, claimed_quote=quote, verifier=QuoteVerifier(pages)
    )


def test_ambiguous_cited_value_downgrades_confidence():
    raw = extract.DealProfileRaw(employees=88)
    pages = ["the Company employs 88 to 92 employees across all sites"]
    facts = {"employees": _fact("employees", "88 to 92", "employs 88 to 92 employees", pages)}
    p = extract.build_profile(raw, facts)
    assert p.employees.confidence == Confidence.MEDIUM
    assert "more than one figure" in (p.employees.note or "")


def test_unreadable_cited_value_downgrades_confidence():
    raw = extract.DealProfileRaw(employees=88)
    pages = ["the Company employs several dozen people at its head office"]
    facts = {
        "employees": _fact("employees", "several dozen", "employs several dozen people", pages)
    }
    p = extract.build_profile(raw, facts)
    assert p.employees.confidence == Confidence.MEDIUM
    assert "no readable figure" in (p.employees.note or "")


# ---------------------------------------------------- citation verification
#
# The provenance guarantee lives here. Everything downstream treats a Citation
# as proof the quote is really in the document, so these are the tests that make
# that true rather than aspirational.


def test_fabricated_quote_is_rejected():
    """The failure this exists to prevent: a plausible sentence the document
    never contained, rendered to a reviewer as a sourced fact."""
    pages = ["Revenue grew to $9.3 million in fiscal 2024."]
    f = _fact("latest_revenue", "9.3", "Revenue reached $14.2 million on record demand", pages)
    assert f.citations == []
    assert f.rejected_quote


def test_rejected_quote_leaves_the_value_uncited_and_says_why():
    raw = extract.DealProfileRaw(employees=88)
    pages = ["the Company employs approximately 88 people"]
    facts = {"employees": _fact("employees", "88", "a workforce of 88 full-time staff", pages)}
    p = extract.build_profile(raw, facts)
    assert p.employees.value == 88, "the structured value survives"
    assert p.employees.citations == [], "but it is not presented as sourced"
    assert p.employees.confidence == Confidence.LOW
    assert "Citation rejected" in (p.employees.note or "")
    assert "_[uncited — pending review]_" in p.employees.render()


def test_wrong_page_number_is_repaired_not_discarded():
    """A correct quote reported against the wrong page is a typo, not a
    fabrication — the reviewer still lands on the passage."""
    pages = ["cover", "table of contents", "Adjusted EBITDA of $1.34 million in fiscal 2024"]
    f = _fact("latest_adjusted_ebitda", "1.34", "Adjusted EBITDA of $1.34 million", pages, page=1)
    assert f.citations[0].page == 3
    assert f.page_corrected


def test_pdf_whitespace_artifacts_do_not_break_verification():
    """pypdf splits table cells across lines and drops punctuation. A verifier
    that demanded literal equality would reject genuinely verbatim quotes and
    report a healthy document as degraded."""
    pages = ["Revenue\n$6.8\n$8.1\n$9.3\nGross  Profit\n$2.3"]
    f = _fact("latest_revenue", "9.3", "Revenue $6.8 $8.1 $9.3", pages)
    assert f.citations, "normalised matching should absorb layout noise"


def test_trivially_short_quotes_are_refused():
    """'Revenue' appears on every page of a CIM. Matching it proves nothing, so
    it must not be allowed to stand as evidence."""
    pages = ["Revenue for the period was strong across all service lines."]
    assert _fact("latest_revenue", "9.3", "Revenue", pages).citations == []
    assert _fact("latest_revenue", "9.3", "Revenue for", pages).citations == []


def test_quote_matching_is_not_fooled_by_word_reordering():
    """Normalisation strips punctuation, so it must still preserve word order —
    otherwise a shuffled paraphrase would verify."""
    pages = ["The Company employs approximately 88 people across eleven locations."]
    f = _fact("employees", "88", "approximately people employs 88 the Company", pages)
    assert f.citations == []


def test_health_counts_rejected_quotes():
    raw = extract.DealProfileRaw(employees=88, year_founded=2013)
    pages = ["Founded in 2013 and employing approximately 88 people"]
    facts = {
        "employees": _fact("employees", "88", "employing approximately 88 people", pages),
        "year_founded": _fact("year_founded", "2013", "incorporated in Delaware in 2013", pages),
    }
    h = extract.health_of(extract.build_profile(raw, facts), facts)
    assert h.quotes_rejected == 1
    assert h.facts_located == 2, "both were reported; only one was verifiable"


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
    flags = parse_findings(line + "\nevidence", [])
    assert len(flags) == 1
    assert flags[0].title == "t"


def test_invalid_category_is_dropped_not_crashed():
    assert parse_findings("FINDING: x | CATEGORY: bogus | SEVERITY: high", []) == []


def test_invalid_severity_defaults_to_low():
    flags = parse_findings("FINDING: x | CATEGORY: growth | SEVERITY: critical", [])
    assert flags[0].severity.value == "low"


def test_no_findings_sentinel():
    assert parse_findings("NO FINDINGS", []) == []


def test_citations_attach_to_the_finding_they_follow():
    pages = ["", "", "", "margins compressed sharply during the period"]
    flags = parse_findings(
        "FINDING: a | CATEGORY: growth | SEVERITY: high\n"
        "PAGE: 4 | QUOTE: margins compressed sharply during the period\n"
        "body",
        pages,
    )
    assert flags[0].citations[0].page == 4
    assert "PAGE:" not in flags[0].detail, "evidence lines belong in citations, not prose"


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
