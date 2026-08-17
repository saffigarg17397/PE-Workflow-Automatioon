"""The extracted deal profile.

Two schema layers here, deliberately:

  * `*Raw` models are what the extraction call fills in — plain values, no
    provenance. Structured extraction reads the PDF for its layout; the citation
    pass reads page-numbered text so it can name pages and quote them. Two
    views of the same document, so two passes.
  * The `Cited[...]` models are the joined result the rest of the pipeline sees.

The join happens in `pipeline/extract.py`. Keeping the raw layer flat and
field-named makes that join mechanical rather than fuzzy.
"""

from enum import Enum

from pydantic import BaseModel, Field

from app.models.citation import Cited


class RevenueModel(str, Enum):
    RECURRING = "recurring"
    CONTRACTED = "contracted"
    PROJECT = "project"
    TRANSACTIONAL = "transactional"
    MIXED = "mixed"


class FiscalYear(BaseModel):
    """One year of the P&L. Amounts in USD millions."""

    year: int
    revenue: float | None = None
    gross_profit: float | None = None
    reported_ebitda: float | None = None
    adjusted_ebitda: float | None = None

    @property
    def ebitda_margin(self) -> float | None:
        base = self.adjusted_ebitda if self.adjusted_ebitda is not None else self.reported_ebitda
        if base is None or not self.revenue:
            return None
        return round(100 * base / self.revenue, 1)


class EbitdaAddback(BaseModel):
    """A single line in the adjusted-EBITDA bridge.

    `years_recurring` is the tell: an addback labelled "one-time" that appears in
    all three historical years is the classic quality-of-earnings red flag, and
    it is detectable arithmetically rather than by judgment.
    """

    description: str
    amount: float
    years_recurring: int = Field(default=1, ge=1)
    is_labelled_one_time: bool = False


class Customer(BaseModel):
    name: str
    revenue_pct: float | None = Field(default=None, ge=0, le=100)
    tenure_years: int | None = None


class Manager(BaseModel):
    name: str
    role: str
    tenure_years: int | None = None
    is_founder: bool = False
    staying_post_close: bool | None = None


class DealProfileRaw(BaseModel):
    """Flat structured-extraction target. One call fills this in.

    Field names here are the join keys against the cited pass — do not rename
    without updating `CITED_FIELDS` in pipeline/extract.py.
    """

    company_name: str | None = None
    description: str | None = None
    headquarters: str | None = None
    year_founded: int | None = None
    employees: int | None = None

    end_markets: list[str] = Field(default_factory=list)
    service_lines: list[str] = Field(default_factory=list)
    revenue_model: RevenueModel | None = None
    recurring_revenue_pct: float | None = None

    financials: list[FiscalYear] = Field(default_factory=list)
    addbacks: list[EbitdaAddback] = Field(default_factory=list)

    top_customers: list[Customer] = Field(default_factory=list)
    customer_count: int | None = None
    top_customer_pct: float | None = None
    top_five_customer_pct: float | None = None

    management: list[Manager] = Field(default_factory=list)

    asking_price: float | None = None
    asking_multiple: float | None = None

    days_sales_outstanding: list[float] = Field(default_factory=list)
    acquisitions_completed: int | None = None


class DealProfile(BaseModel):
    """The joined, cited profile. This is what analysis and drafting consume."""

    company_name: Cited[str] = Field(default_factory=lambda: Cited[str]())
    description: Cited[str] = Field(default_factory=lambda: Cited[str]())
    headquarters: Cited[str] = Field(default_factory=lambda: Cited[str]())
    year_founded: Cited[int] = Field(default_factory=lambda: Cited[int]())
    employees: Cited[int] = Field(default_factory=lambda: Cited[int]())

    end_markets: Cited[list[str]] = Field(default_factory=lambda: Cited[list[str]]())
    service_lines: Cited[list[str]] = Field(default_factory=lambda: Cited[list[str]]())
    revenue_model: Cited[str] = Field(default_factory=lambda: Cited[str]())
    recurring_revenue_pct: Cited[float] = Field(default_factory=lambda: Cited[float]())

    financials: Cited[list[FiscalYear]] = Field(default_factory=lambda: Cited[list[FiscalYear]]())
    addbacks: Cited[list[EbitdaAddback]] = Field(
        default_factory=lambda: Cited[list[EbitdaAddback]]()
    )

    top_customers: Cited[list[Customer]] = Field(default_factory=lambda: Cited[list[Customer]]())
    customer_count: Cited[int] = Field(default_factory=lambda: Cited[int]())
    top_customer_pct: Cited[float] = Field(default_factory=lambda: Cited[float]())
    top_five_customer_pct: Cited[float] = Field(default_factory=lambda: Cited[float]())

    management: Cited[list[Manager]] = Field(default_factory=lambda: Cited[list[Manager]]())

    asking_price: Cited[float] = Field(default_factory=lambda: Cited[float]())
    asking_multiple: Cited[float] = Field(default_factory=lambda: Cited[float]())

    days_sales_outstanding: Cited[list[float]] = Field(default_factory=lambda: Cited[list[float]]())
    acquisitions_completed: Cited[int] = Field(default_factory=lambda: Cited[int]())

    # --- derived metrics (computed, never extracted) ---

    @property
    def latest_year(self) -> FiscalYear | None:
        years = self.financials.value or []
        return max(years, key=lambda y: y.year) if years else None

    @property
    def revenue_cagr(self) -> float | None:
        years = sorted(self.financials.value or [], key=lambda y: y.year)
        if len(years) < 2:
            return None
        first, last = years[0], years[-1]
        if not first.revenue or not last.revenue or first.revenue <= 0:
            return None
        periods = last.year - first.year
        if periods <= 0:
            return None
        return round(100 * ((last.revenue / first.revenue) ** (1 / periods) - 1), 1)

    @property
    def addback_pct_of_ebitda(self) -> float | None:
        """Addbacks as a share of adjusted EBITDA — the QoE aggressiveness proxy.

        Undefined when adjusted EBITDA is not positive. Dividing by a negative
        base yields a negative ratio, which would sail under a `lte 20%`
        earnings-quality threshold and score a loss-making company as having
        clean earnings — the exact inversion this metric exists to catch.
        """
        latest = self.latest_year
        if not latest or not latest.adjusted_ebitda or latest.adjusted_ebitda <= 0:
            return None
        total = sum(a.amount for a in (self.addbacks.value or []))
        if total <= 0:
            return None
        return round(100 * total / latest.adjusted_ebitda, 1)

    @property
    def margin_trend(self) -> list[tuple[int, float]]:
        out = []
        for y in sorted(self.financials.value or [], key=lambda y: y.year):
            m = y.ebitda_margin
            if m is not None:
                out.append((y.year, m))
        return out

    def fields_needing_review(self) -> list[str]:
        """Every field a human should look at before the memo is trusted."""
        out = []
        for name, _ in type(self).model_fields.items():
            val = getattr(self, name)
            if isinstance(val, Cited) and val.needs_review:
                out.append(name)
        return out
