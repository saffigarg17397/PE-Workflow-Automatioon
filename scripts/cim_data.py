"""Company definitions for the synthetic CIM corpus.

Each entry carries both the narrative content and the ground-truth labels. They
live in one structure on purpose: generator and eval labels that are defined
separately drift the moment someone edits a number, and silently-wrong ground
truth is worse than none.

The planted issues are the eval targets. Company 1 is the clean control; each
other company carries a specific, checkable defect.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Company:
    slug: str
    name: str
    tagline: str
    sector: str
    hq: str
    founded: int
    employees: int
    description: str
    end_markets: list[str]
    service_lines: list[str]
    revenue_model: str
    recurring_pct: float
    financials: list[dict[str, Any]]  # year, revenue, gross_profit, reported_ebitda, adj_ebitda
    addbacks: list[dict[str, Any]]
    customers: list[dict[str, Any]]
    customer_count: int
    top_customer_pct: float
    top_five_pct: float
    management: list[dict[str, Any]]
    asking_price: float
    asking_multiple: float
    dso: list[float]
    acquisitions: int
    growth_strategy: list[str]
    # Narrative knobs
    concentration_claim: str  # what the exec summary asserts (may contradict the table)
    seasonality_note: str
    contract_note: str
    expected_flags: list[str] = field(default_factory=list)
    notes: str = ""


COMPANIES: list[Company] = [
    # ---------------------------------------------------------------- 1. CLEAN
    Company(
        slug="meridian_hvac",
        name="Meridian Mechanical Services",
        tagline="Regional Commercial HVAC Services Platform",
        sector="Commercial HVAC services",
        hq="Columbus, Ohio",
        founded=2004,
        employees=142,
        description=(
            "Meridian Mechanical Services is a leading provider of commercial HVAC "
            "maintenance, repair and replacement services to institutional and light "
            "industrial customers across the Midwest. The Company operates a "
            "predominantly contracted service model, with maintenance agreements "
            "generating a substantial majority of gross profit."
        ),
        end_markets=["Healthcare", "Education (K-12 and higher ed)", "Light industrial", "Municipal"],
        service_lines=[
            "Preventive maintenance contracts",
            "Emergency repair and service calls",
            "Equipment replacement and retrofit",
            "Building automation and controls",
        ],
        revenue_model="contracted",
        recurring_pct=58.0,
        financials=[
            {"year": 2022, "revenue": 14.2, "gross_profit": 5.11, "reported_ebitda": 1.92, "adj_ebitda": 2.13},
            {"year": 2023, "revenue": 16.1, "gross_profit": 5.96, "reported_ebitda": 2.35, "adj_ebitda": 2.58},
            {"year": 2024, "revenue": 18.4, "gross_profit": 6.99, "reported_ebitda": 2.81, "adj_ebitda": 3.06},
        ],
        addbacks=[
            {"description": "Owner compensation above market", "amount": 0.14, "years_recurring": 3, "one_time": False},
            {"description": "Non-recurring legal settlement (2024)", "amount": 0.07, "years_recurring": 1, "one_time": True},
            {"description": "Transaction preparation costs", "amount": 0.04, "years_recurring": 1, "one_time": True},
        ],
        customers=[
            {"name": "Riverside Health System", "pct": 11.2, "tenure": 9},
            {"name": "Buckeye Regional School District", "pct": 8.4, "tenure": 12},
            {"name": "Central Ohio Manufacturing Co.", "pct": 6.9, "tenure": 6},
            {"name": "Franklin County Facilities", "pct": 5.8, "tenure": 7},
            {"name": "Tri-State Logistics Park", "pct": 4.6, "tenure": 4},
        ],
        customer_count=310,
        top_customer_pct=11.2,
        top_five_pct=36.9,
        management=[
            {"name": "David Kessler", "role": "Chief Executive Officer", "tenure": 20, "founder": True, "staying": True},
            {"name": "Angela Ruiz", "role": "Chief Operating Officer", "tenure": 8, "founder": False, "staying": True},
            {"name": "Mark Feeney", "role": "Chief Financial Officer", "tenure": 5, "founder": False, "staying": True},
            {"name": "Tom Alvarez", "role": "VP Service Operations", "tenure": 11, "founder": False, "staying": True},
        ],
        asking_price=21.4,
        asking_multiple=7.0,
        dso=[41.0, 39.5, 38.2],
        acquisitions=1,
        growth_strategy=[
            "Densify the existing Ohio footprint through targeted tuck-in acquisitions",
            "Expand building automation attach rate on the installed maintenance base",
            "Enter adjacent Indianapolis and Pittsburgh metro markets",
            "Convert time-and-materials accounts to multi-year maintenance agreements",
        ],
        concentration_claim=(
            "The Company maintains a well-diversified customer base, with no single "
            "customer representing more than 12% of revenue and the top five "
            "representing approximately 37%."
        ),
        seasonality_note=(
            "Revenue exhibits modest seasonality, with the second and third quarters "
            "typically representing approximately 55% of annual revenue driven by "
            "cooling season demand."
        ),
        contract_note=(
            "Maintenance agreements are typically three years in duration with automatic "
            "renewal provisions and contain customary assignment and change-of-control "
            "consent provisions."
        ),
        expected_flags=[],
        notes="Control case. Should score well against services_rollup and produce no high-severity flags.",
    ),
    # ------------------------------------------- 2. CONCENTRATION + FAKE GROWTH
    Company(
        slug="brightpath_dental",
        name="BrightPath Dental Partners",
        tagline="Multi-Site Dental Services Organization",
        sector="Dental services (DSO)",
        hq="Tampa, Florida",
        founded=2013,
        employees=88,
        description=(
            "BrightPath Dental Partners is a dental support organization operating "
            "eleven affiliated practices across central Florida. The Company provides "
            "centralized administrative, marketing and revenue cycle services to its "
            "affiliated practices under long-term management services agreements."
        ),
        end_markets=["General dentistry", "Pediatric dentistry", "Orthodontics"],
        service_lines=[
            "Practice management services",
            "Revenue cycle management",
            "Centralized marketing and patient acquisition",
            "Procurement and supply chain",
        ],
        revenue_model="contracted",
        recurring_pct=31.0,
        financials=[
            {"year": 2022, "revenue": 6.8, "gross_profit": 2.31, "reported_ebitda": 0.79, "adj_ebitda": 1.02},
            {"year": 2023, "revenue": 8.1, "gross_profit": 2.68, "reported_ebitda": 0.88, "adj_ebitda": 1.19},
            {"year": 2024, "revenue": 9.3, "gross_profit": 2.98, "reported_ebitda": 0.94, "adj_ebitda": 1.34},
        ],
        addbacks=[
            {"description": "De novo practice ramp losses", "amount": 0.18, "years_recurring": 3, "one_time": False},
            {"description": "Owner compensation normalization", "amount": 0.13, "years_recurring": 3, "one_time": False},
            {"description": "Acquisition integration costs", "amount": 0.09, "years_recurring": 3, "one_time": True},
        ],
        customers=[
            {"name": "Sunshine Health (Medicaid MCO)", "pct": 34.6, "tenure": 6},
            {"name": "Florida Blue", "pct": 19.8, "tenure": 8},
            {"name": "MCNA Dental", "pct": 12.1, "tenure": 5},
            {"name": "Cigna Dental", "pct": 7.4, "tenure": 4},
            {"name": "Self-pay / other", "pct": 6.2, "tenure": None},
        ],
        customer_count=5,
        top_customer_pct=34.6,
        top_five_pct=80.1,
        management=[
            {"name": "Dr. Priya Raman", "role": "Chief Executive Officer", "tenure": 11, "founder": True, "staying": True},
            {"name": "Curtis Hale", "role": "Chief Financial Officer", "tenure": 3, "founder": False, "staying": True},
            {"name": "Dana Whitfield", "role": "VP Operations", "tenure": 4, "founder": False, "staying": False},
        ],
        asking_price=12.1,
        asking_multiple=9.0,
        dso=[52.0, 55.5, 58.1],
        acquisitions=7,
        growth_strategy=[
            "Continue de novo and acquisition-led practice additions in Florida",
            "Expand specialty service lines (orthodontics, oral surgery) across the base",
            "Improve payor mix through targeted commercial patient acquisition",
            "Extend into the Georgia and Alabama markets",
        ],
        concentration_claim=(
            "The Company benefits from a diversified payor base and broad patient "
            "population, with no single payor relationship representing a "
            "disproportionate share of practice collections."
        ),
        seasonality_note=(
            "Revenue is modestly weighted toward the fourth quarter as patients "
            "utilize remaining annual benefit maximums."
        ),
        contract_note=(
            "Payor agreements are generally terminable on 90 days notice and are "
            "subject to periodic fee schedule revision at the payor's discretion."
        ),
        expected_flags=[
            "customer_concentration",
            "growth",
            "working_capital",
            "internal_inconsistency",
            "contract_risk",
        ],
        notes=(
            "Two planted defects. (1) Severe payor concentration: 34.6% single payor "
            "against a p.3 exec-summary claim of diversification — the adversarial "
            "cross-reference case. (2) 7 acquisitions across the period means reported "
            "16.9% CAGR is inorganic; organic growth is roughly flat."
        ),
    ),
    # --------------------------------------------------- 3. ADDBACK AGGRESSION
    Company(
        slug="northgate_msp",
        name="Northgate Technology Partners",
        tagline="Managed IT Services Provider",
        sector="Managed IT services",
        hq="Charlotte, North Carolina",
        founded=2009,
        employees=118,
        description=(
            "Northgate Technology Partners is a managed services provider delivering "
            "outsourced IT infrastructure management, cybersecurity monitoring and "
            "helpdesk services to mid-market commercial clients in the Southeast."
        ),
        end_markets=["Professional services", "Healthcare", "Financial services", "Manufacturing"],
        service_lines=[
            "Managed infrastructure and helpdesk",
            "Cybersecurity monitoring and response",
            "Cloud migration and management",
            "Project-based professional services",
        ],
        revenue_model="recurring",
        recurring_pct=71.0,
        financials=[
            {"year": 2022, "revenue": 19.8, "gross_profit": 8.32, "reported_ebitda": 2.18, "adj_ebitda": 3.41},
            {"year": 2023, "revenue": 22.1, "gross_profit": 9.28, "reported_ebitda": 2.44, "adj_ebitda": 3.79},
            {"year": 2024, "revenue": 24.3, "gross_profit": 10.21, "reported_ebitda": 2.61, "adj_ebitda": 4.18},
        ],
        addbacks=[
            {"description": "One-time platform migration costs", "amount": 0.62, "years_recurring": 3, "one_time": True},
            {"description": "Owner compensation above market", "amount": 0.41, "years_recurring": 3, "one_time": False},
            {"description": "One-time recruiting and onboarding", "amount": 0.28, "years_recurring": 3, "one_time": True},
            {"description": "Non-recurring rebranding initiative", "amount": 0.16, "years_recurring": 2, "one_time": True},
            {"description": "Transaction costs", "amount": 0.10, "years_recurring": 1, "one_time": True},
        ],
        customers=[
            {"name": "Carolina Physicians Network", "pct": 9.8, "tenure": 7},
            {"name": "Piedmont Capital Advisors", "pct": 7.2, "tenure": 5},
            {"name": "Queen City Legal Group", "pct": 6.1, "tenure": 9},
            {"name": "Southeast Precision Mfg.", "pct": 5.4, "tenure": 4},
            {"name": "Atlantic Insurance Partners", "pct": 4.9, "tenure": 6},
        ],
        customer_count=214,
        top_customer_pct=9.8,
        top_five_pct=33.4,
        management=[
            {"name": "Ryan Doherty", "role": "Chief Executive Officer", "tenure": 15, "founder": True, "staying": True},
            {"name": "Sofia Lindqvist", "role": "Chief Technology Officer", "tenure": 9, "founder": False, "staying": True},
            {"name": "Brett Okafor", "role": "Chief Financial Officer", "tenure": 2, "founder": False, "staying": True},
            {"name": "Hannah Cole", "role": "VP Client Success", "tenure": 6, "founder": False, "staying": True},
        ],
        asking_price=33.4,
        asking_multiple=8.0,
        dso=[44.0, 43.1, 42.6],
        acquisitions=2,
        growth_strategy=[
            "Increase security services attach rate across the managed base",
            "Migrate remaining on-premise clients to managed cloud",
            "Acquire sub-scale regional MSPs in adjacent metros",
            "Launch co-managed IT offering for larger enterprise accounts",
        ],
        concentration_claim=(
            "The Company serves over 200 clients with no meaningful concentration; "
            "the largest client represents under 10% of revenue."
        ),
        seasonality_note=(
            "Recurring managed services revenue is substantially level across quarters; "
            "project revenue is weighted toward the fourth quarter."
        ),
        contract_note=(
            "Managed services agreements are typically 36 months with automatic renewal "
            "and contain change-of-control consent requirements for approximately 40% "
            "of the contracted base."
        ),
        expected_flags=["earnings_quality"],
        notes=(
            "Planted defect: addbacks total $1.57M against $4.18M adjusted EBITDA "
            "(37.6%), and three of five are labelled 'one-time' or 'non-recurring' "
            "while recurring in all three historical years. Reported EBITDA is "
            "roughly flat at ~$2.2-2.6M; the adjusted growth story is manufactured."
        ),
    ),
    # ----------------------------------------------------- 4. WORKING CAPITAL
    Company(
        slug="verdant_landscaping",
        name="Verdant Grounds Management",
        tagline="Commercial Landscaping and Grounds Services",
        sector="Commercial landscaping services",
        hq="Raleigh, North Carolina",
        founded=2011,
        employees=96,
        description=(
            "Verdant Grounds Management provides commercial landscape maintenance, "
            "enhancement and snow removal services to property managers, HOAs and "
            "corporate campuses throughout the Carolinas."
        ),
        end_markets=["Commercial real estate", "HOA / residential communities", "Corporate campuses", "Retail centers"],
        service_lines=[
            "Recurring landscape maintenance contracts",
            "Landscape enhancement projects",
            "Irrigation installation and repair",
            "Seasonal services (snow, leaf removal)",
        ],
        revenue_model="contracted",
        recurring_pct=64.0,
        financials=[
            {"year": 2022, "revenue": 5.1, "gross_profit": 1.63, "reported_ebitda": 0.61, "adj_ebitda": 0.72},
            {"year": 2023, "revenue": 5.7, "gross_profit": 1.77, "reported_ebitda": 0.66, "adj_ebitda": 0.79},
            {"year": 2024, "revenue": 6.4, "gross_profit": 1.92, "reported_ebitda": 0.71, "adj_ebitda": 0.86},
        ],
        addbacks=[
            {"description": "Owner vehicle and personal expenses", "amount": 0.08, "years_recurring": 3, "one_time": False},
            {"description": "Equipment write-off (2023)", "amount": 0.04, "years_recurring": 1, "one_time": True},
            {"description": "Transaction preparation", "amount": 0.03, "years_recurring": 1, "one_time": True},
        ],
        customers=[
            {"name": "Triangle Property Group", "pct": 14.8, "tenure": 8},
            {"name": "Cardinal HOA Management", "pct": 11.3, "tenure": 6},
            {"name": "Research Park Campus Services", "pct": 9.7, "tenure": 5},
            {"name": "Piedmont Retail Trust", "pct": 7.2, "tenure": 4},
            {"name": "Carolina Corporate Centers", "pct": 5.9, "tenure": 3},
        ],
        customer_count=126,
        top_customer_pct=14.8,
        top_five_pct=48.9,
        management=[
            {"name": "Wes Tanner", "role": "Chief Executive Officer", "tenure": 13, "founder": True, "staying": True},
            {"name": "Marisol Vega", "role": "General Manager", "tenure": 7, "founder": False, "staying": True},
            {"name": "Kyle Brennan", "role": "Controller", "tenure": 3, "founder": False, "staying": True},
        ],
        asking_price=5.6,
        asking_multiple=6.5,
        dso=[38.0, 49.5, 67.3],
        acquisitions=0,
        growth_strategy=[
            "Expand enhancement project attach rate on the maintenance base",
            "Add irrigation and lighting service lines",
            "Extend into the Charlotte and Greenville markets",
            "Selectively acquire owner-operator landscape businesses",
        ],
        concentration_claim=(
            "The Company serves a diversified base of over 120 commercial accounts "
            "with the largest representing under 15% of revenue."
        ),
        seasonality_note=(
            "The business experiences limited seasonality given the year-round nature "
            "of maintenance contracts in the Carolinas market."
        ),
        contract_note=(
            "Maintenance agreements are typically twelve months with annual renewal "
            "and thirty-day termination for convenience provisions."
        ),
        expected_flags=["working_capital", "disclosure", "contract_risk"],
        notes=(
            "Planted defects. (1) DSO deteriorates 38 -> 49.5 -> 67.3 days across the "
            "period, a 77% increase, with no explanation offered — cash conversion is "
            "degrading while EBITDA grows. (2) Seasonality is understated: the CIM "
            "claims 'limited seasonality' while the quarterly table on p.14 shows Q1 "
            "at 14% of annual revenue. (3) 30-day termination for convenience on "
            "contracts described as recurring."
        ),
    ),
    # -------------------------------------------------------- 5. KEY PERSON
    Company(
        slug="atlas_bookkeeping",
        name="Atlas Financial Operations",
        tagline="Outsourced Accounting and Bookkeeping Services",
        sector="Outsourced accounting / BPO",
        hq="Denver, Colorado",
        founded=2015,
        employees=74,
        description=(
            "Atlas Financial Operations provides outsourced bookkeeping, controller "
            "and CFO advisory services to small and mid-sized businesses, with "
            "particular depth in the construction, restaurant and professional "
            "services verticals."
        ),
        end_markets=["Construction", "Restaurant and hospitality", "Professional services", "Nonprofit"],
        service_lines=[
            "Monthly bookkeeping and close",
            "Outsourced controller services",
            "Fractional CFO advisory",
            "Payroll administration",
        ],
        revenue_model="recurring",
        recurring_pct=83.0,
        financials=[
            {"year": 2022, "revenue": 8.9, "gross_profit": 3.83, "reported_ebitda": 1.42, "adj_ebitda": 1.61},
            {"year": 2023, "revenue": 10.6, "gross_profit": 4.56, "reported_ebitda": 1.71, "adj_ebitda": 1.94},
            {"year": 2024, "revenue": 12.2, "gross_profit": 5.25, "reported_ebitda": 1.98, "adj_ebitda": 2.23},
        ],
        addbacks=[
            {"description": "Owner compensation normalization", "amount": 0.15, "years_recurring": 3, "one_time": False},
            {"description": "Office relocation (2023)", "amount": 0.06, "years_recurring": 1, "one_time": True},
            {"description": "Transaction costs", "amount": 0.04, "years_recurring": 1, "one_time": True},
        ],
        customers=[
            {"name": "Summit Construction Group", "pct": 8.1, "tenure": 7},
            {"name": "Front Range Restaurant Holdings", "pct": 6.4, "tenure": 5},
            {"name": "Mile High Professional Services", "pct": 5.2, "tenure": 6},
            {"name": "Rocky Mountain Nonprofit Alliance", "pct": 4.1, "tenure": 4},
            {"name": "Cherry Creek Medical Partners", "pct": 3.8, "tenure": 3},
        ],
        customer_count=187,
        top_customer_pct=8.1,
        top_five_pct=27.6,
        management=[
            {"name": "Janet Okonkwo", "role": "Founder and Chief Executive Officer", "tenure": 9, "founder": True, "staying": False},
            {"name": "Derek Salas", "role": "Director of Operations", "tenure": 3, "founder": False, "staying": True},
            {"name": "Amy Chen", "role": "Controller", "tenure": 2, "founder": False, "staying": True},
        ],
        asking_price=16.7,
        asking_multiple=7.5,
        dso=[34.0, 33.2, 32.8],
        acquisitions=1,
        growth_strategy=[
            "Deepen vertical specialization in construction and restaurant segments",
            "Increase CFO advisory attach rate on the bookkeeping base",
            "Expand into the Phoenix and Salt Lake City markets",
            "Automate close workflows to improve staff leverage",
        ],
        concentration_claim=(
            "The Company serves 187 clients with a well-distributed revenue base; "
            "the largest client represents approximately 8% of revenue."
        ),
        seasonality_note=(
            "Revenue is weighted toward the first quarter reflecting year-end close "
            "and tax preparation support activity."
        ),
        contract_note=(
            "Client engagement letters are generally annual and do not contain "
            "assignment or change-of-control provisions; the Company has not "
            "historically sought client consent in connection with ownership changes."
        ),
        expected_flags=["key_person", "contract_risk"],
        notes=(
            "Planted defects. (1) Founder/CEO Janet Okonkwo is not staying post-close "
            "and personally holds the top client relationships (stated p.21); the "
            "remaining team averages under 3 years tenure. (2) Engagement letters lack "
            "change-of-control provisions, which the CIM presents as a positive."
        ),
    ),
]


def by_slug(slug: str) -> Company:
    for c in COMPANIES:
        if c.slug == slug:
            return c
    raise KeyError(slug)
