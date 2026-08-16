"""Render the synthetic CIM corpus to PDF, plus matching eval ground truth.

Both artifacts come out of one pass over `cim_data.COMPANIES` so they cannot
drift. Prose is written to read like a sell-side banker wrote it: promotional in
the summary, hedged in the risk sections, and burying the inconvenient numbers
in tables and footnotes rather than in the narrative.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.cim_data import COMPANIES, Company  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_PDF = ROOT / "data" / "sample_cims"
OUT_GT = ROOT / "evals" / "ground_truth"

NAVY = colors.HexColor("#1a2f4b")
GREY = colors.HexColor("#6b7280")
LIGHT = colors.HexColor("#eef2f7")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "t", parent=base["Title"], fontSize=22, textColor=NAVY, spaceAfter=10
        ),
        "subtitle": ParagraphStyle(
            "st", parent=base["Normal"], fontSize=13, textColor=GREY,
            alignment=TA_CENTER, spaceAfter=28,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"], fontSize=15, textColor=NAVY,
            spaceBefore=16, spaceAfter=9,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontSize=12, textColor=NAVY,
            spaceBefore=12, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "b", parent=base["Normal"], fontSize=10, leading=15,
            alignment=TA_JUSTIFY, spaceAfter=9,
        ),
        "bullet": ParagraphStyle(
            "bu", parent=base["Normal"], fontSize=10, leading=15,
            leftIndent=16, bulletIndent=6, spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "s", parent=base["Normal"], fontSize=8, textColor=GREY, leading=11, spaceAfter=6
        ),
    }


def _table(data: list[list[str]], widths: list[float], align_right_from: int = 1) -> Table:
    t = Table(data, colWidths=widths, hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("ALIGN", (align_right_from, 1), (-1, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c7d2de")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return t


def _money(x: float | None) -> str:
    return f"${x:,.1f}" if x is not None else "n/a"


def _pct(x: float | None) -> str:
    return f"{x:.1f}%" if x is not None else "n/a"


def build_story(c: Company, S: dict[str, ParagraphStyle]) -> list[Any]:
    """Assemble the flowables. Page targets are approximate; 25-40pp is the aim."""
    st: list[Any] = []
    P = lambda txt, s="body": Paragraph(txt, S[s])  # noqa: E731
    B = lambda txt: Paragraph(txt, S["bullet"], bulletText="•")  # noqa: E731

    years = c.financials
    latest = years[-1]

    # ---------------------------------------------------------------- cover
    st += [
        Spacer(1, 2.2 * inch),
        P(c.name, "title"),
        P(c.tagline, "subtitle"),
        Spacer(1, 0.4 * inch),
        P("CONFIDENTIAL INFORMATION MEMORANDUM", "subtitle"),
        Spacer(1, 1.6 * inch),
        P(
            "This Confidential Information Memorandum has been prepared solely for "
            "the use of prospective purchasers in evaluating a possible transaction "
            "involving the Company. It does not purport to be all-inclusive or to "
            "contain all information that a prospective purchaser may require. "
            "Recipients should conduct their own independent investigation and "
            "analysis. Financial information herein is unaudited and has been "
            "prepared by management.",
            "small",
        ),
        PageBreak(),
    ]

    # ------------------------------------------------------- table of contents
    st += [P("Table of Contents", "h1")]
    toc = [
        ("1.", "Executive Summary"),
        ("2.", "Company Overview"),
        ("3.", "Market Overview"),
        ("4.", "Historical Financial Performance"),
        ("5.", "Customer Base"),
        ("6.", "Management and Organization"),
        ("7.", "Operations and Infrastructure"),
        ("8.", "Growth Strategy"),
        ("9.", "Transaction Overview"),
        ("A.", "Appendix — Supplemental Financial Detail"),
    ]
    st += [_table([["Section", "Title"]] + [[n, t] for n, t in toc],
                  [1.0 * inch, 4.6 * inch], align_right_from=99)]
    st += [
        Spacer(1, 18),
        P(
            "All financial information presented herein is unaudited and derived from "
            "management-prepared statements. Certain information has been rounded for "
            "presentation. No representation or warranty, express or implied, is made "
            "as to the accuracy or completeness of the information contained herein.",
            "small",
        ),
        PageBreak(),
    ]

    # ------------------------------------------------------ 1. exec summary
    st += [P("1. Executive Summary", "h1"), P(c.description)]
    st += [
        P(
            f"Founded in {c.founded} and headquartered in {c.hq}, the Company employs "
            f"approximately {c.employees} people and generated revenue of "
            f"${latest['revenue']:.1f} million and Adjusted EBITDA of "
            f"${latest['adj_ebitda']:.2f} million in fiscal {latest['year']}, "
            f"representing an Adjusted EBITDA margin of "
            f"{100 * latest['adj_ebitda'] / latest['revenue']:.1f}%."
        )
    ]
    st += [P("Investment Highlights", "h2")]
    cagr = 100 * ((years[-1]["revenue"] / years[0]["revenue"]) ** (1 / (len(years) - 1)) - 1)
    st += [
        B(
            f"<b>Consistent growth.</b> Revenue has grown at a "
            f"{cagr:.1f}% compound annual rate over the last three fiscal years, from "
            f"${years[0]['revenue']:.1f} million in {years[0]['year']} to "
            f"${years[-1]['revenue']:.1f} million in {years[-1]['year']}."
        ),
        B(f"<b>Attractive revenue mix.</b> Approximately {c.recurring_pct:.0f}% of revenue is "
          f"recurring or contracted in nature, providing visibility into forward periods."),
        B(f"<b>Diversified customer base.</b> {c.concentration_claim}"),
        B("<b>Experienced management team.</b> The senior leadership team averages "
          f"{sum(m['tenure'] for m in c.management) / len(c.management):.0f} years with the Company "
          "and is committed to supporting a transition to new ownership."),
        B("<b>Fragmented market.</b> The Company operates in a highly fragmented market "
          "with meaningful opportunity for consolidation-led growth."),
    ]
    st += [PageBreak()]

    # -------------------------------------------------- 2. company overview
    st += [P("2. Company Overview", "h1"), P("History and Background", "h2")]
    st += [
        P(
            f"{c.name} was established in {c.founded} and has grown from a "
            f"single-location operator to its current position as a leading regional "
            f"provider of {c.sector.lower()}. The Company has completed "
            f"{c.acquisitions} acquisition{'s' if c.acquisitions != 1 else ''} "
            f"since inception and today serves customers from its {c.hq} headquarters."
        ),
        P(
            "Management has invested consistently in systems, training and service "
            "delivery infrastructure, positioning the Company to support continued "
            "organic growth as well as integration of acquired operations."
        ),
    ]
    st += [PageBreak(), P("Service Lines", "h2")]
    st += [
        P(
            "The Company delivers its offering through the service lines described "
            "below. Management views the breadth of the offering as a competitive "
            "differentiator, enabling the Company to serve as a single-source provider "
            "and to expand wallet share within existing accounts over time."
        )
    ]
    for i, s in enumerate(c.service_lines):
        share = [42, 27, 19, 12][i % 4]
        st += [P(f"{s}", "h2")]
        st += [
            P(
                f"Representing approximately {share}% of fiscal {latest['year']} revenue, "
                f"this service line is delivered by dedicated teams operating under "
                f"standardized procedures and quality controls. Management believes the "
                f"offering is well positioned relative to local competitors given the "
                f"Company's technical depth, response-time commitments and established "
                f"customer relationships. Pricing is generally established on a "
                f"{'contracted annual' if i % 2 == 0 else 'time-and-materials'} basis, "
                f"with periodic escalation provisions."
            )
        ]
    st += [PageBreak()]
    st += [P("End Markets", "h2")]
    st += [
        P(
            "The Company serves a diversified set of end markets, which management "
            "believes reduces exposure to any single sector's cyclicality:"
        )
    ]
    for m in c.end_markets:
        st += [B(f"<b>{m}.</b> Customers in this segment value reliability, "
                 f"responsiveness and compliance documentation. Management believes the "
                 f"Company's positioning in this segment is durable and supports "
                 f"above-market retention.")]
    st += [P("Facilities", "h2")]
    st += [
        P(
            f"The Company operates from its headquarters facility in {c.hq}, together "
            f"with satellite operating locations supporting field service delivery. "
            f"All facilities are leased. Management believes existing facilities are "
            f"adequate to support the business plan without material incremental "
            f"investment over the forecast period."
        )
    ]
    st += [P("Employees", "h2")]
    st += [
        P(
            f"As of the most recent period, the Company employed {c.employees} full-time "
            f"equivalents. Voluntary turnover has averaged in the low-to-mid teens "
            f"annually, which management believes compares favorably to industry norms. "
            f"The Company is not party to any collective bargaining agreement and has "
            f"not experienced any work stoppage."
        ),
        P(
            "Compensation is structured with a base salary component and a variable "
            "component tied to utilization, quality and customer satisfaction metrics. "
            "Management believes its compensation structure is competitive within its "
            "operating geographies and supports continued recruitment."
        ),
    ]
    st += [PageBreak()]

    # ------------------------------------------------------ 3. market
    st += [P("3. Market Overview", "h1")]
    st += [
        P(
            f"The {c.sector} market is characterized by a large installed base "
            "of small, owner-operated providers and limited national scale players. "
            "Management estimates the addressable market in the Company's core "
            "geography exceeds $2 billion, of which the Company represents less than "
            "2% share, implying substantial runway for both organic and acquisition-led "
            "expansion."
        ),
        P(
            "Industry growth is supported by durable secular drivers including aging "
            "installed infrastructure, increasing regulatory and compliance "
            "requirements, and a continued shift among customers toward outsourced "
            "service delivery."
        ),
        P("Market Segmentation", "h2"),
        P(
            "Management segments the addressable market by customer size and service "
            "intensity. The Company's core focus is the mid-market segment, where "
            "customers are large enough to require professionalized service delivery "
            "but generally lack the scale to bring the function in-house."
        ),
    ]
    seg_rows = [["Segment", "Est. Market Size", "Company Position"]]
    seg_rows.append(["Enterprise / national accounts", "$780M", "Selective participation"])
    seg_rows.append(["Mid-market", "$1,050M", "Core focus"])
    seg_rows.append(["Small / owner-operated", "$310M", "Opportunistic"])
    st += [_table(seg_rows, [2.4 * inch, 1.5 * inch, 1.9 * inch], align_right_from=1), Spacer(1, 14)]
    st += [
        P("Competitive Positioning", "h2"),
        P(
            "The Company competes primarily against local and regional operators. "
            "Management believes the Company's scale, service breadth and systems "
            "investment provide meaningful competitive advantages in bidding "
            "situations, particularly for larger multi-site accounts."
        ),
        P(
            "The competitive set is fragmented, with the majority of participants "
            "operating from a single location and generating under $5 million of "
            "annual revenue. Management believes limited succession planning across "
            "this cohort creates a durable pipeline of acquisition opportunities at "
            "attractive entry valuations relative to the Company's own multiple."
        ),
        P("Industry Trends", "h2"),
    ]
    for trend, detail in [
        ("Consolidation", "Private-capital-backed platforms have become increasingly "
                          "active acquirers of sub-scale operators, though the market "
                          "remains substantially unconsolidated."),
        ("Labor availability", "Skilled technical labor remains constrained across the "
                               "sector; operators with structured training and career "
                               "pathing have a recruiting advantage."),
        ("Customer sophistication", "Customers increasingly require documented service "
                                    "levels, compliance reporting and digital access to "
                                    "service history, favoring operators with systems "
                                    "investment."),
        ("Pricing", "Input cost inflation has generally been passable to customers "
                    "through contractual escalation provisions, supporting stable "
                    "gross margin over the periods presented."),
    ]:
        st += [B(f"<b>{trend}.</b> {detail}")]
    st += [PageBreak()]

    # -------------------------------------------------- 4. financials
    st += [P("4. Historical Financial Performance", "h1")]
    st += [
        P(
            "The following table summarizes the Company's historical financial "
            "performance for the three fiscal years presented. Financial information "
            "is unaudited and has been prepared by management on a basis consistent "
            "with prior periods."
        )
    ]
    fin_rows = [["($ in millions)"] + [str(y["year"]) for y in years]]
    fin_rows.append(["Revenue"] + [_money(y["revenue"]) for y in years])
    fin_rows.append(["Gross Profit"] + [_money(y["gross_profit"]) for y in years])
    fin_rows.append(
        ["Gross Margin"] + [_pct(100 * y["gross_profit"] / y["revenue"]) for y in years]
    )
    fin_rows.append(["Reported EBITDA"] + [_money(y["reported_ebitda"]) for y in years])
    fin_rows.append(["Adjusted EBITDA"] + [_money(y["adj_ebitda"]) for y in years])
    fin_rows.append(
        ["Adj. EBITDA Margin"] + [_pct(100 * y["adj_ebitda"] / y["revenue"]) for y in years]
    )
    st += [_table(fin_rows, [2.3 * inch] + [1.15 * inch] * len(years)), Spacer(1, 14)]

    st += [P("Adjusted EBITDA Bridge", "h2")]
    st += [
        P(
            f"Management has presented Adjusted EBITDA for fiscal {latest['year']} "
            "reflecting the add-backs set forth below. Management believes these "
            "adjustments present a more representative view of the ongoing earnings "
            "power of the business under new ownership."
        )
    ]
    ab_rows = [["Adjustment", "Amount ($M)", "Periods Presented"]]
    ab_rows.append(["Reported EBITDA", f"${latest['reported_ebitda']:.2f}", ""])
    for a in c.addbacks:
        label = a["description"]
        ab_rows.append([label, f"${a['amount']:.2f}", f"FY{years[-1]['year'] - a['years_recurring'] + 1}-FY{years[-1]['year']}" if a["years_recurring"] > 1 else f"FY{years[-1]['year']}"])
    ab_rows.append(["Adjusted EBITDA", f"${latest['adj_ebitda']:.2f}", ""])
    st += [_table(ab_rows, [3.3 * inch, 1.1 * inch, 1.6 * inch]), Spacer(1, 10)]
    st += [
        P(
            "Add-backs are presented on a pre-tax basis. Prospective purchasers should "
            "conduct their own quality of earnings analysis.",
            "small",
        )
    ]
    st += [PageBreak()]

    # quarterly + working capital
    st += [P("Quarterly Revenue Distribution", "h2"), P(c.seasonality_note)]
    q_split = (
        [0.14, 0.29, 0.33, 0.24]
        if c.slug == "verdant_landscaping"
        else [0.23, 0.26, 0.26, 0.25]
    )
    q_rows = [["($ in millions)", "Q1", "Q2", "Q3", "Q4"]]
    for y in years:
        q_rows.append([str(y["year"])] + [f"${y['revenue'] * s:.1f}" for s in q_split])
    st += [_table(q_rows, [1.6 * inch, 1.0 * inch, 1.0 * inch, 1.0 * inch, 1.0 * inch]), Spacer(1, 14)]

    st += [P("Working Capital", "h2")]
    st += [
        P(
            "The following table presents days sales outstanding for the periods "
            "shown. The Company bills monthly in arrears for recurring services and "
            "on milestone terms for project work."
        )
    ]
    dso_rows = [["Metric"] + [str(y["year"]) for y in years]]
    dso_rows.append(["Days Sales Outstanding"] + [f"{d:.1f}" for d in c.dso])
    st += [_table(dso_rows, [2.3 * inch] + [1.15 * inch] * len(years)), Spacer(1, 10)]
    if c.slug == "verdant_landscaping":
        st += [
            P(
                "Management attributes recent receivables trends to timing of "
                "enhancement project billings and expects normalization in the "
                "forward period.",
                "small",
            )
        ]
    st += [PageBreak()]

    # -------------------------------------------------- 5. customers
    st += [P("5. Customer Base", "h1")]
    st += [
        P(
            f"The Company serves approximately {c.customer_count} "
            f"{'active customer relationships' if c.customer_count > 20 else 'principal payor and customer relationships'}. "
            "The table below sets out the largest relationships by share of revenue "
            f"for fiscal {latest['year']}."
        )
    ]
    cust_rows = [["Customer", "% of Revenue", "Tenure (yrs)"]]
    for cu in c.customers:
        cust_rows.append(
            [cu["name"], _pct(cu["pct"]), str(cu["tenure"]) if cu["tenure"] else "n/a"]
        )
    cust_rows.append(["Top 5 Total", _pct(c.top_five_pct), ""])
    st += [_table(cust_rows, [3.1 * inch, 1.4 * inch, 1.5 * inch]), Spacer(1, 12)]
    st += [P("Contractual Arrangements", "h2"), P(c.contract_note)]
    st += [
        P(
            "Customer relationships have historically demonstrated high retention, "
            "with annual revenue retention in excess of 90% across the maintenance "
            "base over the periods presented."
        )
    ]
    st += [PageBreak()]

    # customer case studies — banker filler, and extraction noise
    st += [P("Selected Customer Case Studies", "h2")]
    for cu in c.customers[:3]:
        st += [P(f"{cu['name']}", "h2")]
        st += [
            P(
                f"A customer of the Company for "
                f"{cu['tenure'] if cu['tenure'] else 'several'} years, this "
                f"relationship began with a single engagement and has expanded to "
                f"encompass multiple service lines. The account illustrates the "
                f"Company's land-and-expand motion: initial scope was limited, with "
                f"subsequent expansion driven by service quality and responsiveness "
                f"rather than competitive re-bid. Management believes the relationship "
                f"is stable and expects continued expansion under new ownership."
            )
        ]
    st += [P("Retention and Churn", "h2")]
    ret_rows = [["Metric"] + [str(y["year"]) for y in years]]
    ret_rows.append(["Gross revenue retention", "91%", "92%", "93%"])
    ret_rows.append(["Net revenue retention", "104%", "106%", "108%"])
    ret_rows.append(["Logo churn", "7%", "6%", "6%"])
    st += [_table(ret_rows, [2.3 * inch] + [1.15 * inch] * len(years)), Spacer(1, 10)]
    st += [
        P(
            "Retention statistics are management estimates and have not been "
            "independently verified.",
            "small",
        )
    ]
    st += [PageBreak()]

    # -------------------------------------------------- 6. management
    st += [P("6. Management and Organization", "h1")]
    mgmt_rows = [["Name", "Role", "Tenure (yrs)"]]
    for m in c.management:
        mgmt_rows.append([m["name"], m["role"], str(m["tenure"])])
    st += [_table(mgmt_rows, [1.9 * inch, 2.6 * inch, 1.3 * inch], align_right_from=2), Spacer(1, 14)]

    for m in c.management:
        bio = (
            f"<b>{m['name']}, {m['role']}.</b> "
            f"{'Founded the Company in ' + str(c.founded) + ' and has' if m['founder'] else 'Joined the Company and has'} "
            f"served in the current role for {m['tenure']} years. "
        )
        if m["staying"] is False:
            bio += (
                "Has indicated an intention to transition out of the business "
                "following a transaction, and will make herself available for a "
                "customary transition period."
                if m["name"].split()[0] in {"Janet", "Dana"}
                else "Intends to transition out of the business following a transaction."
            )
        else:
            bio += "Is expected to continue with the business following a transaction."
        st += [P(bio)]

    if c.slug == "atlas_bookkeeping":
        st += [
            P("Client Relationship Ownership", "h2"),
            P(
                "The Company's founder has historically maintained primary "
                "relationship ownership for the majority of the Company's largest "
                "client accounts, including all of the top five relationships. "
                "Management is in the process of transitioning relationship coverage "
                "to the broader delivery team. Excluding the founder, the senior "
                "team averages under three years of tenure with the Company."
            ),
        ]
    st += [PageBreak()]

    # ------------------------------------------------- 7. operations
    st += [P("7. Operations and Infrastructure", "h1")]
    st += [
        P(
            "The Company delivers services through a field organization supported by "
            "centralized scheduling, dispatch and quality functions. Management has "
            "invested in standardizing service delivery procedures across the "
            "organization, which it believes supports both consistent customer "
            "outcomes and the efficient integration of acquired operations."
        ),
        P("Systems and Technology", "h2"),
        P(
            "The Company operates an integrated field service management platform "
            "covering work order management, scheduling, mobile field data capture, "
            "inventory and invoicing. Financial reporting is maintained on a "
            "commercial accounting package with monthly close completed within "
            "fifteen business days. Management believes the current systems "
            "environment is adequate to support the business through the forecast "
            "period."
        ),
        P("Quality and Compliance", "h2"),
        P(
            "The Company maintains documented quality procedures and carries "
            "customary general liability, workers compensation, auto and umbrella "
            "coverage. There is no pending or threatened litigation that management "
            "believes would be material to the Company's operations or financial "
            "condition."
        ),
        P("Supplier Relationships", "h2"),
        P(
            "The Company sources materials and equipment through national "
            "distribution relationships as well as regional suppliers. No single "
            "supplier represents a concentration that management believes would be "
            "material, and the Company does not have long-term purchase commitments "
            "outside the ordinary course."
        ),
    ]
    st += [PageBreak()]

    # -------------------------------------------------- 8. growth
    st += [P("8. Growth Strategy", "h1")]
    st += [
        P(
            "Management has identified the following initiatives, which it believes "
            "represent actionable near-term opportunities for a new owner:"
        )
    ]
    for i, g in enumerate(c.growth_strategy, 1):
        st += [P(f"{i}. {g}", "h2")]
        st += [
            P(
                "Management believes this initiative is supported by the Company's "
                "existing customer relationships, operational infrastructure and "
                "market position, and can be executed without material incremental "
                "capital investment."
            ),
            P(
                f"Management has identified an initial set of target accounts and "
                f"estimates this initiative could contribute incremental revenue "
                f"equivalent to {[6, 9, 5, 7][i % 4]}% of the fiscal {latest['year']} "
                f"base over a three-year horizon, at contribution margins consistent "
                f"with or above the Company's current blended margin profile. "
                f"Execution risk is primarily related to hiring and onboarding "
                f"qualified delivery staff at the required pace."
            ),
        ]
        if i == len(c.growth_strategy):
            st += [PageBreak()]

    st += [P("Financial Outlook", "h2")]
    st += [
        P(
            "Management has prepared the illustrative projections below. These "
            "projections reflect management's current expectations and are subject to "
            "significant business, economic and competitive uncertainties, many of "
            "which are beyond the Company's control. Actual results may differ "
            "materially."
        )
    ]
    proj_rows = [["($ in millions)", f"FY{latest['year'] + 1}E", f"FY{latest['year'] + 2}E", f"FY{latest['year'] + 3}E"]]
    proj_rows.append(
        ["Revenue"] + [f"${latest['revenue'] * (1.12 ** n):.1f}" for n in (1, 2, 3)]
    )
    proj_rows.append(
        ["Adjusted EBITDA"] + [f"${latest['adj_ebitda'] * (1.18 ** n):.2f}" for n in (1, 2, 3)]
    )
    proj_rows.append(
        ["Adj. EBITDA Margin"]
        + [
            f"{100 * latest['adj_ebitda'] * (1.18 ** n) / (latest['revenue'] * (1.12 ** n)):.1f}%"
            for n in (1, 2, 3)
        ]
    )
    st += [_table(proj_rows, [2.3 * inch, 1.15 * inch, 1.15 * inch, 1.15 * inch]), Spacer(1, 10)]
    st += [
        P(
            "Projections assume continued execution of the initiatives described "
            "above, no material change in competitive or macroeconomic conditions, "
            "and retention of key personnel and customer relationships.",
            "small",
        )
    ]
    st += [PageBreak()]

    # -------------------------------------------------- 9. transaction
    st += [P("9. Transaction Overview", "h1")]
    st += [
        P(
            "The shareholders of the Company are seeking a transaction with a partner "
            "capable of supporting the Company's next phase of growth. The Company is "
            "being offered on a debt-free, cash-free basis with a normalized level of "
            "working capital."
        )
    ]
    tx_rows = [["Item", "Value"]]
    tx_rows.append(["Enterprise Value (asking)", f"${c.asking_price:.1f}M"])
    tx_rows.append([f"Implied Multiple (FY{latest['year']} Adj. EBITDA)", f"{c.asking_multiple:.1f}x"])
    tx_rows.append([f"FY{latest['year']} Adjusted EBITDA", f"${latest['adj_ebitda']:.2f}M"])
    tx_rows.append([f"FY{latest['year']} Revenue", f"${latest['revenue']:.1f}M"])
    st += [_table(tx_rows, [3.6 * inch, 2.4 * inch]), Spacer(1, 14)]
    st += [
        P("Process", "h2"),
        P(
            "Indications of interest are requested by the date set forth in the "
            "process letter. Management presentations will be scheduled with selected "
            "parties, followed by a confirmatory diligence period and access to a "
            "virtual data room."
        ),
        PageBreak(),
        P("Risk Factors", "h2"),
        P(
            "Prospective purchasers should consider, among other factors, the "
            "following. This list is not exhaustive and is not presented in order of "
            "significance."
        ),
    ]
    for rf, detail in [
        ("Economic conditions", "The Company's customers may reduce discretionary "
                                "spending in a downturn, which could affect project "
                                "and enhancement revenue in particular."),
        ("Key personnel", "The Company's performance depends on its ability to retain "
                          "senior management and skilled delivery staff. The loss of "
                          "key personnel could adversely affect operations and "
                          "customer relationships."),
        ("Customer relationships", "Customer agreements may generally be terminated on "
                                   "notice. The loss of one or more significant "
                                   "relationships could have a material effect on "
                                   "results."),
        ("Competition", "The Company operates in a competitive market and may face "
                        "pricing pressure from existing or new entrants."),
        ("Labor costs", "Wage inflation and constrained availability of skilled labor "
                        "could compress margins if cost increases cannot be passed "
                        "through."),
        ("Acquisition integration", "The Company's growth plan contemplates "
                                    "acquisitions. Integration may prove more "
                                    "difficult or costly than anticipated."),
        ("Financial information", "Financial information presented herein is unaudited "
                                  "and has been prepared by management. Prospective "
                                  "purchasers should conduct independent verification."),
    ]:
        st += [B(f"<b>{rf}.</b> {detail}")]
    st += [PageBreak()]

    # -------------------------------------------------- appendix
    st += [P("Appendix A — Supplemental Financial Detail", "h1")]
    st += [P("Monthly Revenue Detail — Fiscal " + str(latest["year"]), "h2")]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    monthly = [q_split[i // 3] / 3 for i in range(12)]
    m_rows = [["Month", "Revenue ($M)", "Month", "Revenue ($M)"]]
    for i in range(6):
        m_rows.append(
            [
                months[i],
                f"${latest['revenue'] * monthly[i]:.2f}",
                months[i + 6],
                f"${latest['revenue'] * monthly[i + 6]:.2f}",
            ]
        )
    st += [_table(m_rows, [1.3 * inch, 1.5 * inch, 1.3 * inch, 1.5 * inch]), Spacer(1, 14)]

    st += [P("Summary Balance Sheet", "h2")]
    ar = latest["revenue"] * c.dso[-1] / 365
    bs_rows = [["($ in millions)", f"FY{latest['year']}"]]
    bs_rows.append(["Cash and equivalents", f"${latest['revenue'] * 0.04:.2f}"])
    bs_rows.append(["Accounts receivable", f"${ar:.2f}"])
    bs_rows.append(["Inventory and other current", f"${latest['revenue'] * 0.03:.2f}"])
    bs_rows.append(["Property and equipment, net", f"${latest['revenue'] * 0.11:.2f}"])
    bs_rows.append(["Total assets", f"${latest['revenue'] * 0.18 + ar:.2f}"])
    bs_rows.append(["Accounts payable and accrued", f"${latest['revenue'] * 0.07:.2f}"])
    bs_rows.append(["Total liabilities", f"${latest['revenue'] * 0.09:.2f}"])
    st += [_table(bs_rows, [3.4 * inch, 1.8 * inch]), Spacer(1, 14)]

    st += [P("Capital Expenditures", "h2")]
    capex_rows = [["($ in millions)"] + [str(y["year"]) for y in years]]
    capex_rows.append(["Maintenance capex"] + [f"${y['revenue'] * 0.012:.2f}" for y in years])
    capex_rows.append(["Growth capex"] + [f"${y['revenue'] * 0.008:.2f}" for y in years])
    capex_rows.append(["Total capex"] + [f"${y['revenue'] * 0.020:.2f}" for y in years])
    capex_rows.append(
        ["% of revenue"] + ["2.0%" for _ in years]
    )
    st += [_table(capex_rows, [2.3 * inch] + [1.15 * inch] * len(years)), Spacer(1, 12)]
    st += [
        P(
            "Capital expenditure requirements are modest and primarily relate to "
            "vehicle and equipment replacement on a normal replacement cycle.",
            "small",
        )
    ]
    return st


def ground_truth(c: Company) -> dict[str, Any]:
    """Eval labels, derived from the same source as the PDF."""
    years = c.financials
    latest = years[-1]
    total_addbacks = sum(a["amount"] for a in c.addbacks)
    cagr = round(100 * ((years[-1]["revenue"] / years[0]["revenue"]) ** (1 / (len(years) - 1)) - 1), 1)
    return {
        "slug": c.slug,
        "company_name": c.name,
        "notes": c.notes,
        "fields": {
            "company_name": c.name,
            "headquarters": c.hq,
            "year_founded": c.founded,
            "employees": c.employees,
            "recurring_revenue_pct": c.recurring_pct,
            "latest_revenue": latest["revenue"],
            "latest_reported_ebitda": latest["reported_ebitda"],
            "latest_adjusted_ebitda": latest["adj_ebitda"],
            "revenue_cagr": cagr,
            "top_customer_pct": c.top_customer_pct,
            "top_five_customer_pct": c.top_five_pct,
            "customer_count": c.customer_count,
            "asking_price": c.asking_price,
            "asking_multiple": c.asking_multiple,
            "total_addbacks": round(total_addbacks, 2),
            "addback_pct_of_ebitda": round(100 * total_addbacks / latest["adj_ebitda"], 1),
            "acquisitions_completed": c.acquisitions,
            "latest_dso": c.dso[-1],
            "end_markets_count": len(c.end_markets),
            "management_count": len(c.management),
        },
        "expected_flags": sorted(c.expected_flags),
        "financials": [
            {
                "year": y["year"],
                "revenue": y["revenue"],
                "reported_ebitda": y["reported_ebitda"],
                "adjusted_ebitda": y["adj_ebitda"],
            }
            for y in years
        ],
    }


def main() -> None:
    OUT_PDF.mkdir(parents=True, exist_ok=True)
    OUT_GT.mkdir(parents=True, exist_ok=True)
    S = _styles()

    for c in COMPANIES:
        pdf_path = OUT_PDF / f"{c.slug}.pdf"
        doc = SimpleDocTemplate(
            str(pdf_path),
            pagesize=letter,
            leftMargin=0.9 * inch,
            rightMargin=0.9 * inch,
            topMargin=0.9 * inch,
            bottomMargin=0.9 * inch,
            title=f"{c.name} — Confidential Information Memorandum",
        )
        doc.build(build_story(c, S))

        gt_path = OUT_GT / f"{c.slug}.yaml"
        gt_path.write_text(yaml.safe_dump(ground_truth(c), sort_keys=False, width=100))

        size_kb = pdf_path.stat().st_size / 1024
        print(f"  {c.slug:24s} -> {pdf_path.name} ({size_kb:.0f} KB) + ground truth")

    print(f"\n{len(COMPANIES)} CIMs written to {OUT_PDF.relative_to(ROOT)}")
    print(f"{len(COMPANIES)} ground-truth files written to {OUT_GT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
