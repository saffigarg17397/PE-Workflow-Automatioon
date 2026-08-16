"""Red flags and thesis scoring.

`DetectionSource` is the field that matters most to a reviewer: it separates
findings that are arithmetic (reproducible, never hallucinated) from findings
that are judgment (broader reach, needs verification). The memo renders them in
separate blocks for exactly that reason.
"""

from enum import Enum

from pydantic import BaseModel, Field

from app.models.citation import Citation


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return {"high": 0, "medium": 1, "low": 2}[self.value]


class DetectionSource(str, Enum):
    RULE = "rule"
    MODEL = "model"


class FlagCategory(str, Enum):
    CUSTOMER_CONCENTRATION = "customer_concentration"
    EARNINGS_QUALITY = "earnings_quality"
    GROWTH = "growth"
    MARGIN = "margin"
    WORKING_CAPITAL = "working_capital"
    KEY_PERSON = "key_person"
    CONTRACT_RISK = "contract_risk"
    COMPETITIVE = "competitive"
    DISCLOSURE = "disclosure"
    INTERNAL_INCONSISTENCY = "internal_inconsistency"


class RedFlag(BaseModel):
    category: FlagCategory
    severity: Severity
    title: str = Field(description="One line, specific and quantified where possible")
    detail: str
    source: DetectionSource
    citations: list[Citation] = Field(default_factory=list)
    rule_id: str | None = Field(default=None, description="Set when source is RULE")

    @property
    def unique_citations(self) -> list[Citation]:
        seen: set[tuple[int, int | None]] = set()
        out: list[Citation] = []
        for c in self.citations:
            key = (c.page, c.end_page)
            if key not in seen:
                seen.add(key)
                out.append(c)
        return out

    def render(self) -> str:
        cites = "".join(c.render() for c in self.unique_citations)
        return f"**{self.title}**{cites} — {self.detail}"


class CriterionResult(BaseModel):
    """One thesis criterion, evaluated."""

    name: str
    passed: bool | None = Field(default=None, description="None when data was unavailable")
    actual: str
    target: str
    weight: float = 1.0
    is_dealbreaker: bool = False


class ThesisFit(BaseModel):
    thesis_name: str
    criteria: list[CriterionResult] = Field(default_factory=list)
    dealbreakers_hit: list[str] = Field(default_factory=list)

    @property
    def score(self) -> float:
        """Weighted pass rate over criteria we could actually evaluate.

        Unevaluable criteria are excluded rather than counted as failures — a
        missing disclosure is a review item, not evidence against the company.
        """
        scored = [c for c in self.criteria if c.passed is not None]
        if not scored:
            return 0.0
        total_w = sum(c.weight for c in scored)
        if total_w == 0:
            return 0.0
        earned = sum(c.weight for c in scored if c.passed)
        return round(100 * earned / total_w, 1)

    @property
    def coverage(self) -> float:
        """Share of criteria we had data for. Low coverage = low trust in score."""
        if not self.criteria:
            return 0.0
        scored = sum(1 for c in self.criteria if c.passed is not None)
        return round(100 * scored / len(self.criteria), 1)
