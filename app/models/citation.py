"""Provenance primitives.

The central invariant of this tool: no financial figure reaches a memo without a
source page and the verbatim text it came from. Everything downstream is built
on `Cited[T]`, so an uncited number is unrepresentable rather than merely
discouraged.
"""

from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Citation(BaseModel):
    """A pointer back into the source document.

    Populated from the Claude API's native PDF citations, which return
    `page_location` (1-indexed) plus the `cited_text` span. We do not
    reconstruct these with regex — model-reported spans are what the model
    actually conditioned on, which is the thing worth showing a reviewer.
    """

    page: int = Field(ge=1, description="1-indexed source page")
    quote: str = Field(description="Verbatim text from the source")
    end_page: int | None = Field(default=None, description="Set when a span crosses pages")

    def render(self) -> str:
        if self.end_page and self.end_page != self.page:
            return f"[p.{self.page}-{self.end_page}]"
        return f"[p.{self.page}]"


class Cited(BaseModel, Generic[T]):
    """A value that knows where it came from and how sure we are.

    `value` is None when extraction could not find the field or could not cite
    it. That is a first-class outcome, not an error: unfound fields route to the
    review queue instead of being guessed at.
    """

    value: T | None = None
    citations: list[Citation] = Field(default_factory=list)
    confidence: Confidence = Confidence.LOW
    note: str | None = Field(default=None, description="Why confidence is low, if it is")

    @property
    def is_sourced(self) -> bool:
        return self.value is not None and len(self.citations) > 0

    @property
    def needs_review(self) -> bool:
        """Anything not confidently sourced goes in front of a human."""
        return not self.is_sourced or self.confidence != Confidence.HIGH

    def render(self) -> str:
        """Value plus inline citation markers, for memo rendering."""
        if self.value is None:
            return "_not found_"
        cites = "".join(c.render() for c in self.citations)
        return f"{self.value}{cites}"
