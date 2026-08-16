"""Two-pass extraction and the join between them.

Pass 1 (`extract_structured`) fills a typed schema — accurate values, no
provenance. Pass 2 (`extract_cited`) locates source passages with the API's
native citations — provenance, but free text. Neither alone is sufficient: the
API rejects `citations` and `output_config.format` on the same request.

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

_FIELD_LINE = re.compile(r"FIELD:\s*(\w+)\s*\|\s*VALUE:\s*([^|\n]*)", re.IGNORECASE)


class CitedFact:
    """One field located by the cited pass."""

    def __init__(self, field: str, value: str, citations: list[Citation]) -> None:
        self.field = field
        self.raw_value = value.strip()
        self.citations = citations

    @property
    def found(self) -> bool:
        return self.raw_value.upper() != "NOT_FOUND" and bool(self.raw_value)

    def as_float(self) -> float | None:
        m = re.search(r"-?[\d,]+\.?\d*", self.raw_value.replace("$", ""))
        if not m:
            return None
        try:
            return float(m.group().replace(",", ""))
        except ValueError:
            return None


def _citations_from_block(block: Any) -> list[Citation]:
    """Pull page-located citations off one response content block.

    The API returns `page_location` for PDF sources with 1-indexed
    start/end page numbers plus the cited span. We take it as-is rather than
    reconstructing spans — the model-reported span is what it actually
    conditioned on, which is the thing worth showing a reviewer.
    """
    out: list[Citation] = []
    for c in getattr(block, "citations", None) or []:
        if getattr(c, "type", None) != "page_location":
            continue
        start = getattr(c, "start_page_number", None)
        if start is None:
            continue
        end = getattr(c, "end_page_number", None)
        out.append(
            Citation(
                page=int(start),
                quote=(getattr(c, "cited_text", "") or "").strip()[:600],
                end_page=int(end) if end and int(end) != int(start) else None,
            )
        )
    return out


def parse_cited_response(resp: Any) -> dict[str, CitedFact]:
    """Walk the response, attaching each block's citations to the field named on
    the most recent FIELD: line.

    The model emits one line per field; citations arrive attached to the text
    blocks covering those lines. Blocks do not align 1:1 with lines, so we track
    the current field as we scan and accumulate citations onto it.
    """
    facts: dict[str, CitedFact] = {}
    current: str | None = None

    for block in resp.content:
        if getattr(block, "type", None) != "text":
            continue
        text = block.text
        block_cites = _citations_from_block(block)

        matches = list(_FIELD_LINE.finditer(text))
        if matches:
            for m in matches:
                name = m.group(1).strip().lower()
                value = m.group(2).strip()
                if name in CITED_FIELDS:
                    facts[name] = CitedFact(name, value, list(block_cites))
                    current = name
        elif current and block_cites:
            # Continuation block — the quote for the field named earlier.
            facts[current].citations.extend(block_cites)

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
      HIGH   — structured value present, cited, and the two agree
      MEDIUM — structured value present and cited, but values disagree
      LOW    — value present but uncited, or missing entirely
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
        # The cited pass either couldn't find the field or named a value without
        # attaching a source span. Either way the value is unverified: keep it
        # (the structured pass is schema-valid) but never let it reach a memo
        # as an established figure.
        return value, [], Confidence.LOW, "Extracted but no supporting citation located"

    # Both passes produced something — do they agree?
    note: str | None = None
    conf = Confidence.HIGH
    if isinstance(value, int | float):
        cited_val = fact.as_float()
        if cited_val is not None and value:
            drift = abs(cited_val - float(value)) / max(abs(float(value)), 1e-9)
            if drift > 0.02:
                conf = Confidence.MEDIUM
                note = f"Passes disagree: structured={value}, cited={cited_val}"
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


def run(client: LLMClient, doc: CachedDocument) -> DealProfile:
    """Both passes, joined."""
    raw = client.extract_structured(
        "extract_structured", doc, load_prompt("extract_structured"), DealProfileRaw
    )
    cited_resp = client.extract_cited("extract_cited", doc, load_prompt("extract_cited"))
    facts = parse_cited_response(cited_resp)
    return build_profile(raw, facts)
