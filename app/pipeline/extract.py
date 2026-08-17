"""Two-pass extraction, the join between them, and local citation verification.

Pass 1 (`extract_structured`) fills a typed schema from the PDF — accurate
values, no provenance. Pass 2 (`extract_cited`) reads page-numbered text and
reports, per field, a page number and a verbatim quote — an *assertion* of
provenance.

The assertion is not trusted. Every quote is matched against the actual text of
the page it names before it is allowed to become a `Citation`; a quote that
cannot be found is discarded and the field is reported as unverified. This is
the core of the tool: provenance is something this code checks, not something a
model promises or a vendor annotates. It also means a `Citation` anywhere in the
system is one that was verified, so downstream code never has to ask.

The join is by field name, which is why `CITED_FIELDS` and the prompt in
`extract_cited.md` must agree. That coupling is the cost of the split, and it's
mechanical enough to be safe as long as both are edited together.

Where the two passes disagree on a value, the structured pass wins (it was
schema-validated) but confidence is downgraded and the disagreement is recorded
in `note` — that field lands in the review queue with the conflict visible.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import load_prompt
from app.llm.client import CachedDocument, LLMClient
from app.models.citation import Citation, Cited, Confidence
from app.models.deal import DealProfile, DealProfileRaw
from app.models.memo import ExtractionHealth

# Join keys. Left side is the name used in the cited pass's output lines; right
# side is the path into the structured profile. Must stay in sync with
# app/llm/prompts/extract_cited.md.
CITED_FIELDS: dict[str, str] = {
    "company_name": "company_name",
    "headquarters": "headquarters",
    "year_founded": "year_founded",
    "employees": "employees",
    "recurring_revenue_pct": "recurring_revenue_pct",
    "latest_revenue": "financials",
    "latest_adjusted_ebitda": "financials",
    "latest_reported_ebitda": "financials",
    "top_customer_pct": "top_customer_pct",
    "top_five_customer_pct": "top_five_customer_pct",
    "customer_count": "customer_count",
    "asking_price": "asking_price",
    "asking_multiple": "asking_multiple",
    "acquisitions_completed": "acquisitions_completed",
    "latest_dso": "days_sales_outstanding",
}

# Markdown emphasis around the labels is tolerated: models very often bold or
# italicise `FIELD:` / `VALUE:` even when the prompt shows them plain, and a
# parser that misses those lines silently drops every citation — which would
# degrade the whole document to LOW confidence with no visible error.
# `_E` absorbs emphasis wherever it lands — models write `**FIELD:**`,
# `**FIELD**:`, and `- FIELD:` interchangeably, and the colon may sit inside or
# outside the emphasis.
_E = r"[*_`]*"


def _label(name: str) -> str:
    return rf"{_E}{name}{_E}:{_E}\s*"


# PAGE and QUOTE are optional so that a NOT_FOUND line still parses as a
# deliberate "the document is silent" answer rather than as unrecognised noise —
# the two are very different signals for ExtractionHealth.
_FIELD_LINE = re.compile(
    _label("FIELD") + r"(\w+)\s*" + _E + r"\|" + _E + r"\s*"
    r"" + _label("VALUE") + r"([^|\n]*)"
    r"(?:" + _E + r"\|" + _E + r"\s*" + _label("PAGE") + r"(\d+)\s*"
    r"(?:" + _E + r"\|" + _E + r"\s*" + _label("QUOTE") + r"([^\n]*))?"
    r")?",
    re.IGNORECASE,
)

# Values the model uses to mean "absent". Treating these as found would attach a
# real citation to a non-answer, which is worse than reporting nothing.
_NULL_VALUES = {
    "not_found",
    "notfound",
    "n/a",
    "na",
    "none",
    "unknown",
    "not disclosed",
    "not stated",
    "not specified",
    "-",
    "—",
}

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")

# A quote shorter than this carries no evidential weight — "revenue" appears on
# every page of a CIM, so matching it proves nothing. Rejecting short quotes is
# what stops the verifier from rubber-stamping a citation that happens to hit.
_MIN_QUOTE_TOKENS = 4

# Progressive truncations tried when the full quote does not match. Models
# reliably copy the start of a passage and then drift — paraphrasing the tail,
# merging a following clause, or running past a line break. Matching a prefix
# still proves the passage exists on that page, which is the claim being
# checked; anything shorter than _MIN_QUOTE_TOKENS is never tried.
_PREFIX_LENGTHS = (16, 10, 6)


def _norm(s: str) -> str:
    """Collapse to comparable text.

    PDF extraction inserts spurious line breaks, splits ligatures and drops
    punctuation inconsistently, so a literal comparison fails on quotes that are
    in fact verbatim. Reducing both sides to lowercase alphanumeric words
    removes exactly the noise the extractor introduces while preserving the word
    sequence, which is the part that would have to be invented to fake a quote.
    """
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


class QuoteVerifier:
    """Checks quoted passages against the document they claim to come from.

    Normalised page text is computed once per document: verification runs for
    every field, and re-normalising a 21-page CIM each time would dominate the
    cost of the whole join.
    """

    def __init__(self, page_text: list[str]) -> None:
        self._pages = [_norm(t) for t in page_text]

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def locate(self, quote: str, claimed_page: int | None) -> int | None:
        """Return the 1-indexed page the quote really appears on, or None.

        The page the model named is tried first, but every other page is tried
        too. A correct quote reported against the wrong page is a citation with
        a typo, not a fabrication — repairing it silently is right, because the
        reviewer clicking through lands on the passage either way. A quote found
        nowhere is the case that matters, and it returns None.
        """
        tokens = _norm(quote).split()
        if len(tokens) < _MIN_QUOTE_TOKENS:
            return None

        order = list(range(1, len(self._pages) + 1))
        if claimed_page and 1 <= claimed_page <= len(self._pages):
            order.remove(claimed_page)
            order.insert(0, claimed_page)

        for length in (len(tokens), *_PREFIX_LENGTHS):
            if length < _MIN_QUOTE_TOKENS or length > len(tokens):
                continue
            probe = " ".join(tokens[:length])
            for page in order:
                if probe in self._pages[page - 1]:
                    return page
        return None


class CitedFact:
    """One field the cited pass reported, and what verification made of it."""

    def __init__(
        self,
        field: str,
        value: str,
        *,
        claimed_page: int | None = None,
        claimed_quote: str = "",
        verifier: QuoteVerifier | None = None,
    ) -> None:
        self.field = field
        self.raw_value = value.strip().strip("*_`").strip()
        self.claimed_page = claimed_page
        self.claimed_quote = claimed_quote.strip().strip("*_`").strip()
        self.citations: list[Citation] = []
        self.rejected_quote = False

        if not self.found or not self.claimed_quote or verifier is None:
            return

        page = verifier.locate(self.claimed_quote, claimed_page)
        if page is None:
            # The evidence does not exist in the document. The value survives —
            # the structured pass may well be right — but it is now an uncited
            # value, and the rejection is counted so a document full of them
            # trips the degradation warning.
            self.rejected_quote = True
            return
        self.citations = [Citation(page=page, quote=self.claimed_quote[:600])]

    @property
    def found(self) -> bool:
        return bool(self.raw_value) and self.raw_value.lower() not in _NULL_VALUES

    @property
    def page_corrected(self) -> bool:
        """The quote checked out, but not on the page the model named."""
        return bool(self.citations) and self.claimed_page != self.citations[0].page

    @property
    def is_ambiguous(self) -> bool:
        """True when the value names more than one number — a range, or a figure
        quoted alongside a comparison. Taking the first number silently would
        put an arbitrary pick into a memo, so the caller downgrades instead."""
        if not self.found:
            return False
        return len(_NUMBER.findall(self.raw_value.replace("$", ""))) > 1

    def as_float(self) -> float | None:
        if not self.found:
            return None
        m = _NUMBER.search(self.raw_value.replace("$", ""))
        if not m:
            return None
        try:
            return float(m.group().replace(",", ""))
        except ValueError:
            return None


def parse_cited_response(text: str, page_text: list[str]) -> dict[str, CitedFact]:
    """Parse the cited pass's lines and verify every quote as we go."""
    verifier = QuoteVerifier(page_text)
    facts: dict[str, CitedFact] = {}

    for m in _FIELD_LINE.finditer(text):
        name = m.group(1).strip().lower()
        if name not in CITED_FIELDS:
            continue
        page_raw = m.group(3)
        facts[name] = CitedFact(
            name,
            m.group(2).strip(),
            claimed_page=int(page_raw) if page_raw else None,
            claimed_quote=m.group(4) or "",
            verifier=verifier,
        )

    return facts


def _latest_financial(raw: DealProfileRaw, attr: str) -> float | None:
    if not raw.financials:
        return None
    latest = max(raw.financials, key=lambda y: y.year)
    return getattr(latest, attr, None)


def _resolve(
    field: str, raw: DealProfileRaw, facts: dict[str, CitedFact]
) -> tuple[Any, list[Citation], Confidence, str | None]:
    """Join one field across the two passes.

    Returns (value, citations, confidence, note). Confidence policy:
      HIGH   — structured value present, quote verified, and the two agree
      MEDIUM — verified but the values disagree, or the passage is ambiguous
      LOW    — value present but unverified, or missing entirely
    """
    # Structured-side value
    if field == "latest_revenue":
        value: Any = _latest_financial(raw, "revenue")
    elif field == "latest_adjusted_ebitda":
        value = _latest_financial(raw, "adjusted_ebitda")
    elif field == "latest_reported_ebitda":
        value = _latest_financial(raw, "reported_ebitda")
    elif field == "latest_dso":
        value = raw.days_sales_outstanding[-1] if raw.days_sales_outstanding else None
    else:
        value = getattr(raw, CITED_FIELDS[field], None)

    fact = facts.get(field)
    if value is None:
        return None, [], Confidence.LOW, "Not found by structured extraction"
    if fact is None or not fact.found or not fact.citations:
        # Either the cited pass couldn't find the field, or it offered a quote
        # that verification rejected. Keep the value (the structured pass is
        # schema-valid) but never let it reach a memo as an established figure —
        # and say which of the two happened, because they mean different things
        # to whoever picks this up in review.
        if fact is not None and fact.rejected_quote:
            reason = (
                f"Citation rejected: the supporting quote was not found in the document "
                f"(model cited p.{fact.claimed_page})"
            )
        else:
            reason = "Extracted but no supporting citation located"
        return value, [], Confidence.LOW, reason

    # Both passes produced something, and the evidence checks out — do they agree?
    note: str | None = None
    conf = Confidence.HIGH
    if fact.is_ambiguous:
        conf = Confidence.MEDIUM
        note = f"Cited passage names more than one figure: {fact.raw_value!r}"
    elif isinstance(value, int | float):
        cited_val = fact.as_float()
        if cited_val is None:
            conf = Confidence.MEDIUM
            note = f"Cited passage has no readable figure: {fact.raw_value!r}"
        elif value:
            drift = abs(cited_val - float(value)) / max(abs(float(value)), 1e-9)
            if drift > 0.02:
                conf = Confidence.MEDIUM
                note = f"Passes disagree: structured={value}, cited={cited_val}"

    if fact.page_corrected and note is None:
        note = f"Quote verified on p.{fact.citations[0].page}; model reported p.{fact.claimed_page}"
    return value, fact.citations, conf, note


def _cited(value: Any, cites: list[Citation], conf: Confidence, note: str | None) -> Cited[Any]:
    return Cited[Any](value=value, citations=cites, confidence=conf, note=note)


def build_profile(raw: DealProfileRaw, facts: dict[str, CitedFact]) -> DealProfile:
    """Merge the two passes into the cited profile the pipeline consumes."""
    p = DealProfile()

    for field in CITED_FIELDS:
        value, cites, conf, note = _resolve(field, raw, facts)
        target = {
            "latest_revenue": None,
            "latest_adjusted_ebitda": None,
            "latest_reported_ebitda": None,
            "latest_dso": None,
        }.get(field, field)
        if target is None:
            continue  # folded into financials / dso below
        if hasattr(p, target):
            setattr(p, target, _cited(value, cites, conf, note))

    # Composite fields: carry the citations located for their latest-year proxies.
    fin_cites: list[Citation] = []
    fin_conf = Confidence.LOW
    for proxy in ("latest_revenue", "latest_adjusted_ebitda", "latest_reported_ebitda"):
        _, cites, conf, _ = _resolve(proxy, raw, facts)
        fin_cites.extend(cites)
        if conf == Confidence.HIGH:
            fin_conf = Confidence.HIGH
        elif conf == Confidence.MEDIUM and fin_conf != Confidence.HIGH:
            fin_conf = Confidence.MEDIUM
    p.financials = Cited[Any](
        value=raw.financials or None,
        citations=fin_cites[:6],
        confidence=fin_conf if raw.financials else Confidence.LOW,
        note=None if raw.financials else "No historical financials extracted",
    )

    _, dso_cites, dso_conf, _ = _resolve("latest_dso", raw, facts)
    p.days_sales_outstanding = Cited[Any](
        value=raw.days_sales_outstanding or None,
        citations=dso_cites,
        confidence=dso_conf if raw.days_sales_outstanding else Confidence.LOW,
    )

    # Fields the cited pass doesn't cover. They carry the structured value with
    # MEDIUM confidence — present and schema-valid, but unverified against a
    # source passage, which is exactly what a reviewer should know.
    for name, val in (
        ("description", raw.description),
        ("end_markets", raw.end_markets or None),
        ("service_lines", raw.service_lines or None),
        ("revenue_model", raw.revenue_model.value if raw.revenue_model else None),
        ("addbacks", raw.addbacks or None),
        ("top_customers", raw.top_customers or None),
        ("management", raw.management or None),
    ):
        setattr(
            p,
            name,
            Cited[Any](
                value=val,
                citations=[],
                confidence=Confidence.MEDIUM if val else Confidence.LOW,
                note="Extracted without citation pass" if val else "Not extracted",
            ),
        )

    return p


# Profile attributes the citation pass is asked to locate. Derived from
# CITED_FIELDS so the two can't drift; composite targets collapse to one entry.
CITABLE_ATTRS: frozenset[str] = frozenset(CITED_FIELDS.values())


def health_of(profile: DealProfile, facts: dict[str, CitedFact]) -> ExtractionHealth:
    """Summarise whether the join produced provenance or just values."""
    names = [n for n in type(profile).model_fields if isinstance(getattr(profile, n), Cited)]
    extracted = [n for n in names if getattr(profile, n).value is not None]
    citable = [n for n in extracted if n in CITABLE_ATTRS]
    return ExtractionHealth(
        fields_total=len(names),
        fields_extracted=len(extracted),
        fields_citable=len(citable),
        fields_cited=sum(1 for n in citable if getattr(profile, n).citations),
        facts_located=sum(1 for f in facts.values() if f.found),
        quotes_rejected=sum(1 for f in facts.values() if f.rejected_quote),
    )


def run(client: LLMClient, doc: CachedDocument) -> tuple[DealProfile, ExtractionHealth]:
    """Both passes, joined and verified, plus a health signal for the join itself."""
    raw = client.extract_structured(
        "extract_structured", doc, load_prompt("extract_structured"), DealProfileRaw
    )
    cited_text = client.extract_cited("extract_cited", doc, load_prompt("extract_cited"))
    facts = parse_cited_response(cited_text, doc.page_text)
    profile = build_profile(raw, facts)
    return profile, health_of(profile, facts)
