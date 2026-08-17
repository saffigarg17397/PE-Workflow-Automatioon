"""Fake documents and API responses.

These mimic the shape of real SDK response objects closely enough to exercise
the parsing, verification and join logic — which is where the interesting bugs
live. Tests never make a network call and never need a key.

`brightpath_pages()` is the important one. Citation verification matches quotes
against real page text, so the fixture responses quote passages that genuinely
appear in `data/sample_cims/brightpath_dental.pdf`. That coupling is deliberate:
a test that fed unverifiable quotes to the parser would pass while proving
nothing, since every citation would be correctly discarded. It also means these
fixtures break if the sample generator changes the document — which is the right
thing to happen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.models.deal import (
    Customer,
    DealProfileRaw,
    EbitdaAddback,
    FiscalYear,
    Manager,
    RevenueModel,
)

# --------------------------------------------------------------- fake document

_SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample_cims" / "brightpath_dental.pdf"


@lru_cache(maxsize=1)
def brightpath_pages() -> list[str]:
    """Page text of the real sample CIM.

    Verification fixtures read the actual document rather than a hand-written
    stand-in. A synthetic page list would drift from what pypdf really produces —
    the spacing, the split table cells, the runs of bare numbers — and those
    artifacts are precisely what the quote matcher has to survive. Testing
    against invented text would prove the matcher works on text no PDF produces.
    """
    from pypdf import PdfReader

    return [(pg.extract_text() or "") for pg in PdfReader(str(_SAMPLE)).pages]


# ------------------------------------------------------- fake SDK response shapes


@dataclass
class FakeUsage:
    prompt_token_count: int = 1000
    candidates_token_count: int = 500
    thoughts_token_count: int = 0
    cached_content_token_count: int = 0


@dataclass
class FakePart:
    text: str


@dataclass
class FakeContent:
    parts: list[FakePart]


@dataclass
class FakeCandidate:
    content: FakeContent
    finish_reason: str = "STOP"


@dataclass
class FakeResponse:
    """Mirrors `google.genai` response shape: candidates -> content -> parts."""

    text: str = ""
    usage_metadata: FakeUsage = field(default_factory=FakeUsage)
    prompt_feedback: Any = None
    parsed: Any = None
    candidates: list[FakeCandidate] = field(default_factory=list)

    @classmethod
    def of(cls, text: str, **kw: Any) -> FakeResponse:
        return cls(
            text=text,
            candidates=[FakeCandidate(content=FakeContent(parts=[FakePart(text=text)]))],
            **kw,
        )


# ------------------------------------------------------------- fixture content


def brightpath_raw() -> DealProfileRaw:
    """Structured-pass output for the concentration/inorganic-growth case."""
    return DealProfileRaw(
        company_name="BrightPath Dental Partners",
        description="A dental support organization operating eleven affiliated practices.",
        headquarters="Tampa, Florida",
        year_founded=2013,
        employees=88,
        end_markets=["General dentistry", "Pediatric dentistry", "Orthodontics"],
        service_lines=["Practice management services", "Revenue cycle management"],
        revenue_model=RevenueModel.CONTRACTED,
        recurring_revenue_pct=31.0,
        financials=[
            FiscalYear(
                year=2022,
                revenue=6.8,
                gross_profit=2.31,
                reported_ebitda=0.79,
                adjusted_ebitda=1.02,
            ),
            FiscalYear(
                year=2023,
                revenue=8.1,
                gross_profit=2.68,
                reported_ebitda=0.88,
                adjusted_ebitda=1.19,
            ),
            FiscalYear(
                year=2024,
                revenue=9.3,
                gross_profit=2.98,
                reported_ebitda=0.94,
                adjusted_ebitda=1.34,
            ),
        ],
        addbacks=[
            EbitdaAddback(
                description="De novo practice ramp losses", amount=0.18, years_recurring=3
            ),
            EbitdaAddback(
                description="Owner compensation normalization", amount=0.13, years_recurring=3
            ),
            EbitdaAddback(
                description="Acquisition integration costs",
                amount=0.09,
                years_recurring=3,
                is_labelled_one_time=True,
            ),
        ],
        top_customers=[
            Customer(name="Sunshine Health (Medicaid MCO)", revenue_pct=34.6, tenure_years=6),
            Customer(name="Florida Blue", revenue_pct=19.8, tenure_years=8),
        ],
        customer_count=5,
        top_customer_pct=34.6,
        top_five_customer_pct=80.1,
        management=[
            Manager(
                name="Dr. Priya Raman",
                role="Chief Executive Officer",
                tenure_years=11,
                is_founder=True,
                staying_post_close=True,
            ),
            Manager(
                name="Curtis Hale",
                role="Chief Financial Officer",
                tenure_years=3,
                staying_post_close=True,
            ),
        ],
        asking_price=12.1,
        asking_multiple=9.0,
        days_sales_outstanding=[52.0, 55.5, 58.1],
        acquisitions_completed=7,
    )


def brightpath_cited_text() -> str:
    """Cited-pass output.

    Three paths are exercised deliberately: `top_customer_pct` disagrees with the
    structured pass (-> MEDIUM), `acquisitions_completed` quotes a passage that
    is not in the document (-> citation rejected, LOW), and `customer_count` is
    a clean NOT_FOUND.
    """
    return "\n".join(
        [
            "FIELD: company_name | VALUE: BrightPath Dental Partners | PAGE: 3 | "
            "QUOTE: BrightPath Dental Partners is a dental support organization operating "
            "eleven affiliated practices",
            "FIELD: headquarters | VALUE: Tampa, Florida | PAGE: 3 | "
            "QUOTE: Founded in 2013 and headquartered in Tampa, Florida",
            "FIELD: year_founded | VALUE: 2013 | PAGE: 3 | "
            "QUOTE: Founded in 2013 and headquartered in Tampa",
            "FIELD: employees | VALUE: 88 | PAGE: 3 | "
            "QUOTE: the Company employs approximately 88 people",
            "FIELD: latest_revenue | VALUE: 9.3 | PAGE: 9 | QUOTE: Revenue $6.8 $8.1 $9.3",
            "FIELD: latest_adjusted_ebitda | VALUE: 1.34 | PAGE: 9 | "
            "QUOTE: Adjusted EBITDA $1.0 $1.2 $1.3",
            "FIELD: latest_reported_ebitda | VALUE: 0.94 | PAGE: 9 | "
            "QUOTE: Reported EBITDA $0.8 $0.9 $0.9",
            # Deliberate disagreement with the structured pass -> MEDIUM.
            "FIELD: top_customer_pct | VALUE: 33.0 | PAGE: 11 | "
            "QUOTE: Sunshine Health (Medicaid MCO) 34.6%",
            "FIELD: top_five_customer_pct | VALUE: 80.1 | PAGE: 11 | QUOTE: Top 5 Total 80.1%",
            "FIELD: latest_dso | VALUE: 58.1 | PAGE: 10 | "
            "QUOTE: Days Sales Outstanding 52.0 55.5 58.1",
            # The quote is right but the page is wrong (it is on 18, not 17) —
            # verification should repair it rather than discard it.
            "FIELD: asking_multiple | VALUE: 9.0 | PAGE: 17 | "
            "QUOTE: Implied Multiple (FY2024 Adj. EBITDA) 9.0x",
            # Fabricated evidence: this sentence is nowhere in the document, so
            # the citation must be rejected and the field dropped to LOW.
            "FIELD: acquisitions_completed | VALUE: 7 | PAGE: 4 | "
            "QUOTE: the Company closed seven bolt-on transactions during the period",
            "FIELD: customer_count | VALUE: NOT_FOUND",
        ]
    )


def brightpath_flags_text() -> str:
    return (
        "FINDING: Diversification claim contradicted by the payor table "
        "| CATEGORY: internal_inconsistency | SEVERITY: high\n"
        "PAGE: 6 | QUOTE: The Company serves a diversified set of end markets\n"
        "PAGE: 11 | QUOTE: Sunshine Health (Medicaid MCO) 34.6%\n"
        "The document claims diversification while the payor table shows a single plan "
        "at 34.6% of revenue.\n"
        "\n"
        "FINDING: Payor agreements terminable on 90 days notice | CATEGORY: contract_risk "
        "| SEVERITY: medium\n"
        "PAGE: 11 | QUOTE: Payor agreements are generally terminable on 90 days notice\n"
        "Revenue described as contracted rests on agreements terminable on short notice.\n"
    )


def draft_text() -> str:
    return (
        "## recommendation_rationale\n"
        "Single-payor concentration of 34.6% [p.11] exceeds the 20% screening ceiling "
        "and is a stated dealbreaker.\n\n"
        "## business_overview\n"
        "BrightPath Dental Partners operates eleven affiliated dental practices [p.3].\n\n"
        "## financial_summary\n"
        "Revenue grew to $9.3M [p.9] with adjusted EBITDA of $1.34M [p.9].\n\n"
        "## thesis_commentary\n"
        "Fails the concentration dealbreaker and the earnings-quality criterion.\n\n"
        "## diligence_priorities\n"
        "- Confirm Sunshine Health contract terms and renewal date\n"
        "- Decompose reported CAGR into organic and acquired revenue\n"
        "- Obtain a quality of earnings study covering the de novo ramp add-backs\n"
    )
