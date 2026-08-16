"""Fake API responses.

These mimic the shape of real SDK response objects closely enough to exercise
the parsing and join logic — which is where the interesting bugs live. Tests
never make a network call and never need a key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.deal import (
    Customer,
    DealProfileRaw,
    EbitdaAddback,
    FiscalYear,
    Manager,
    RevenueModel,
)


@dataclass
class FakeCitation:
    type: str = "page_location"
    start_page_number: int = 1
    end_page_number: int | None = None
    cited_text: str = ""


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"
    citations: list[FakeCitation] = field(default_factory=list)


@dataclass
class FakeUsage:
    input_tokens: int = 1000
    output_tokens: int = 500
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeResponse:
    content: list[Any]
    stop_reason: str = "end_turn"
    usage: FakeUsage = field(default_factory=FakeUsage)
    stop_details: Any = None


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
            FiscalYear(year=2022, revenue=6.8, gross_profit=2.31, reported_ebitda=0.79, adjusted_ebitda=1.02),
            FiscalYear(year=2023, revenue=8.1, gross_profit=2.68, reported_ebitda=0.88, adjusted_ebitda=1.19),
            FiscalYear(year=2024, revenue=9.3, gross_profit=2.98, reported_ebitda=0.94, adjusted_ebitda=1.34),
        ],
        addbacks=[
            EbitdaAddback(description="De novo practice ramp losses", amount=0.18, years_recurring=3),
            EbitdaAddback(description="Owner compensation normalization", amount=0.13, years_recurring=3),
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
            Manager(name="Dr. Priya Raman", role="Chief Executive Officer", tenure_years=11, is_founder=True, staying_post_close=True),
            Manager(name="Curtis Hale", role="Chief Financial Officer", tenure_years=3, staying_post_close=True),
        ],
        asking_price=12.1,
        asking_multiple=9.0,
        days_sales_outstanding=[52.0, 55.5, 58.1],
        acquisitions_completed=7,
    )


def brightpath_cited_response() -> FakeResponse:
    """Cited-pass output. `top_customer_pct` deliberately disagrees with the
    structured pass so the confidence-downgrade path gets exercised."""
    return FakeResponse(
        content=[
            FakeTextBlock(
                text="FIELD: company_name | VALUE: BrightPath Dental Partners",
                citations=[FakeCitation(start_page_number=3, cited_text="BrightPath Dental Partners is a dental support organization")],
            ),
            FakeTextBlock(
                text="FIELD: headquarters | VALUE: Tampa, Florida",
                citations=[FakeCitation(start_page_number=3, cited_text="headquartered in Tampa, Florida")],
            ),
            FakeTextBlock(
                text="FIELD: year_founded | VALUE: 2013",
                citations=[FakeCitation(start_page_number=3, cited_text="Founded in 2013")],
            ),
            FakeTextBlock(
                text="FIELD: employees | VALUE: 88",
                citations=[FakeCitation(start_page_number=3, cited_text="employs approximately 88 people")],
            ),
            FakeTextBlock(
                text="FIELD: latest_revenue | VALUE: 9.3",
                citations=[FakeCitation(start_page_number=9, cited_text="Revenue $9.3")],
            ),
            FakeTextBlock(
                text="FIELD: latest_adjusted_ebitda | VALUE: 1.34",
                citations=[FakeCitation(start_page_number=9, cited_text="Adjusted EBITDA $1.34")],
            ),
            FakeTextBlock(
                text="FIELD: latest_reported_ebitda | VALUE: 0.94",
                citations=[FakeCitation(start_page_number=9, cited_text="Reported EBITDA $0.94")],
            ),
            # Disagreement: cited says 33.0, structured says 34.6 -> MEDIUM
            FakeTextBlock(
                text="FIELD: top_customer_pct | VALUE: 33.0",
                citations=[FakeCitation(start_page_number=11, cited_text="Sunshine Health (Medicaid MCO) 34.6%")],
            ),
            FakeTextBlock(
                text="FIELD: top_five_customer_pct | VALUE: 80.1",
                citations=[FakeCitation(start_page_number=11, cited_text="Top 5 Total 80.1%")],
            ),
            FakeTextBlock(
                text="FIELD: asking_multiple | VALUE: 9.0",
                citations=[FakeCitation(start_page_number=18, cited_text="Implied Multiple 9.0x")],
            ),
            # Uncited -> LOW confidence
            FakeTextBlock(text="FIELD: acquisitions_completed | VALUE: 7", citations=[]),
            FakeTextBlock(text="FIELD: customer_count | VALUE: NOT_FOUND", citations=[]),
        ]
    )


def brightpath_flags_response() -> FakeResponse:
    return FakeResponse(
        content=[
            FakeTextBlock(
                text=(
                    "FINDING: Executive summary claims payor diversification contradicted by customer table "
                    "| CATEGORY: internal_inconsistency | SEVERITY: high\n"
                    "The executive summary states the Company 'benefits from a diversified payor base' "
                    "while the customer table shows Sunshine Health at 34.6% of revenue."
                ),
                citations=[
                    FakeCitation(start_page_number=3, cited_text="benefits from a diversified payor base"),
                    FakeCitation(start_page_number=11, cited_text="Sunshine Health (Medicaid MCO) 34.6%"),
                ],
            ),
            FakeTextBlock(
                text=(
                    "FINDING: Payor agreements terminable on 90 days notice | CATEGORY: contract_risk | SEVERITY: medium\n"
                    "Revenue described as contracted rests on agreements terminable on short notice."
                ),
                citations=[FakeCitation(start_page_number=11, cited_text="terminable on 90 days notice")],
            ),
        ]
    )


def draft_response() -> FakeResponse:
    return FakeResponse(
        content=[
            FakeTextBlock(
                text=(
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
            )
        ]
    )
