"""The IC screening memo and the run record that produced it."""

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.models.deal import DealProfile
from app.models.flags import RedFlag, ThesisFit

# Sentence boundary, approximated. Good enough for deciding where a citation
# stops being a repeat: over-splitting only keeps a marker that could have been
# dropped, which is the harmless direction to be wrong in.
_SENTENCE = re.compile(r"(?<=[.:;])\s+")
_MARKER = re.compile(r"\s*\[p\.(\d+(?:-\d+)?)\]")


def tidy_citations(text: str) -> str:
    """Drop repeated citations to the same page within a sentence.

    The model cites every clause, which is the right instinct — it is showing
    its work. But "38.0 days [p.10] in 2022 [p.10] to 49.5 days [p.10] in 2023
    [p.10]" makes six identical provenance claims about one table and reads like
    debug output rather than a memo. Keeping the first occurrence per page per
    sentence loses nothing a reader needs: they still know which page the series
    came from, and can still click it.

    Scoped to *within* a sentence deliberately — a page cited again in the next
    sentence supports a new assertion and keeps its marker. Table rows are left
    alone entirely, since a cell carries no sentence context.
    """
    out = []
    for line in text.split("\n"):
        if line.lstrip().startswith("|"):
            out.append(line)
            continue
        sentences = []
        for sentence in _SENTENCE.split(line):
            seen: set[str] = set()

            def keep(m: re.Match[str], seen: set[str] = seen) -> str:
                page = m.group(1)
                if page in seen:
                    return ""
                seen.add(page)
                return m.group(0)

            sentences.append(_MARKER.sub(keep, sentence))
        out.append(" ".join(s for s in sentences if s))
    return "\n".join(out)


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


class ExtractionHealth(BaseModel):
    """Did extraction actually work, or did it merely finish?

    The failure this exists to catch: the cited pass drifts from the requested
    format, the parser matches nothing, every field falls back to LOW
    confidence — and the memo still renders, looking normal, with no provenance
    behind any number. That is the most dangerous outcome for a diligence tool,
    because it is indistinguishable from success at a glance.
    """

    fields_total: int = 0
    fields_extracted: int = 0
    fields_cited: int = 0
    fields_citable: int = Field(
        default=0,
        description="Extracted fields the citation pass is actually asked to locate",
    )
    facts_located: int = Field(default=0, description="Fields the cited pass returned")
    quotes_rejected: int = Field(
        default=0,
        description="Quotes the model supplied that could not be found in the document",
    )
    flags_parsed: int = 0

    @property
    def citation_rate(self) -> float:
        """Share of *citable* extracted values carrying a source page.

        The denominator is deliberately not every extracted field. Several
        fields (narrative description, service lines, management roster) are
        filled by the structured pass only and are never asked of the citation
        pass, so counting them would peg a perfectly healthy run near the
        degradation threshold and make the signal useless.
        """
        base = self.fields_citable or self.fields_extracted
        if not base:
            return 0.0
        return round(100 * self.fields_cited / base, 1)

    @property
    def extraction_rate(self) -> float:
        if not self.fields_total:
            return 0.0
        return round(100 * self.fields_extracted / self.fields_total, 1)

    @property
    def degraded(self) -> bool:
        """True when provenance has collapsed — treat the run as unreliable.

        Zero located facts means the cited pass produced nothing the parser
        recognised, which is a format-drift bug rather than a document that
        happens to be sparse.
        """
        return self.facts_located == 0 or (self.fields_citable > 0 and self.citation_rate < 40)

    @property
    def warning(self) -> str | None:
        if not self.degraded:
            return None
        if self.facts_located == 0:
            return (
                "The citation pass returned nothing the parser recognised. Every figure "
                "in this memo is unverified. This indicates a format or API failure, not "
                "a sparse document — do not rely on this memo."
            )
        if self.quotes_rejected and self.quotes_rejected >= self.fields_cited:
            return (
                f"{self.quotes_rejected} of the quotes offered as evidence could not be "
                f"found anywhere in the document, against only {self.fields_cited} that "
                f"could. The model is inventing sources rather than locating them — this "
                f"memo is unsafe to rely on at any level."
            )
        return (
            f"Only {self.citation_rate:.0f}% of extracted fields carry a source page "
            f"(expected >40%). Provenance is substantially incomplete; treat figures as "
            f"unverified pending review."
        )


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
    health: ExtractionHealth = Field(default_factory=ExtractionHealth)

    # Applied here rather than in each renderer: markdown, docx and the web view
    # all display this prose, and three call sites would eventually disagree.
    # Running on validation also means stored runs are tidied when they are
    # loaded back, so seeds generated before this existed render correctly
    # without being regenerated.
    @field_validator(
        "recommendation_rationale",
        "business_overview",
        "financial_summary",
        "thesis_commentary",
        mode="after",
    )
    @classmethod
    def _tidy(cls, v: str) -> str:
        return tidy_citations(v)

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
