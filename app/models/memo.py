"""The IC screening memo and the run record that produced it."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.models.deal import DealProfile
from app.models.flags import RedFlag, ThesisFit


class Recommendation(str, Enum):
    ADVANCE = "advance"
    MORE_INFO = "more_info"
    PASS = "pass"


class StageTelemetry(BaseModel):
    """Per-stage cost accounting. PE is a unit-economics business; so is this."""

    stage: str
    model: str
    effort: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0

    @property
    def cache_hit(self) -> bool:
        return self.cache_read_tokens > 0


class Memo(BaseModel):
    """Sections 1-7 of the house format. Section 7 is not optional.

    A diligence tool that is explicit about what it could not determine is more
    trustworthy than one that isn't — so `unreviewed_fields` and `not_found` are
    structural, not an appendix afterthought.
    """

    company_name: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    thesis_name: str

    # 1
    recommendation: Recommendation
    recommendation_rationale: str
    # 2
    business_overview: str
    # 3
    financial_summary: str
    # 4
    thesis_fit: ThesisFit
    thesis_commentary: str
    # 5
    red_flags: list[RedFlag] = Field(default_factory=list)
    # 6
    diligence_priorities: list[str] = Field(default_factory=list)
    # 7
    unreviewed_fields: list[str] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)
    confidence_note: str = ""

    @property
    def flags_by_severity(self) -> list[RedFlag]:
        return sorted(self.red_flags, key=lambda f: (f.severity.rank, f.category.value))


class MemoRun(BaseModel):
    """Everything one pipeline execution produced, including what it cost."""

    run_id: str
    source_file: str
    page_count: int
    profile: DealProfile
    memo: Memo
    telemetry: list[StageTelemetry] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None

    @property
    def total_cost_usd(self) -> float:
        return round(sum(t.cost_usd for t in self.telemetry), 4)

    @property
    def total_latency_ms(self) -> int:
        return sum(t.latency_ms for t in self.telemetry)

    @property
    def cache_savings_tokens(self) -> int:
        return sum(t.cache_read_tokens for t in self.telemetry)
