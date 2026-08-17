"""Qualitative red-flag detection.

Scoped deliberately to what the rules engine cannot reach: cross-section
inconsistencies, contract structure, disclosure gaps, positioning claims. The
prompt tells the model the arithmetic checks are already covered, so it spends
its effort on reading rather than recomputing ratios it would do worse than
Python at.

Findings are parsed from a delimited text format rather than structured outputs
because this pass has to quote the document, and every quote it offers is
verified against the page it names before becoming a citation — the same
treatment field extraction gets. A finding whose evidence cannot be found is
kept but marked, because the claim may still be worth a reviewer's attention
even when the supporting quote was garbled.
"""

from __future__ import annotations

import re

from app.config import load_prompt
from app.llm.client import CachedDocument, LLMClient
from app.models.citation import Citation
from app.models.flags import DetectionSource, FlagCategory, RedFlag, Severity
from app.pipeline.extract import QuoteVerifier

_VALID_CATEGORIES = {
    "internal_inconsistency": FlagCategory.INTERNAL_INCONSISTENCY,
    "contract_risk": FlagCategory.CONTRACT_RISK,
    "key_person": FlagCategory.KEY_PERSON,
    "disclosure": FlagCategory.DISCLOSURE,
    "competitive": FlagCategory.COMPETITIVE,
    "growth": FlagCategory.GROWTH,
}

# Same emphasis tolerance as the cited-extraction parser — a missed FINDING line
# silently drops a real risk from the memo.
_E = r"[*_`]*"
_FINDING = re.compile(
    rf"{_E}FINDING{_E}:{_E}\s*(?P<title>[^\n|]+?)\s*{_E}\|{_E}\s*"
    rf"{_E}CATEGORY{_E}:{_E}\s*(?P<cat>\w+)\s*{_E}\|{_E}\s*"
    rf"{_E}SEVERITY{_E}:{_E}\s*(?P<sev>\w+)",
    re.IGNORECASE,
)

# One or more evidence lines sit beneath each finding. A contradiction needs two
# (the claim and the fact it contradicts), so this repeats rather than being a
# single field on the FINDING line.
_EVIDENCE = re.compile(
    rf"{_E}PAGE{_E}:{_E}\s*(?P<page>\d+)\s*{_E}\|{_E}\s*{_E}QUOTE{_E}:{_E}\s*(?P<quote>[^\n]+)",
    re.IGNORECASE,
)

_FORMAT_INSTRUCTION = """

Format each finding as a line beginning with FINDING:, then one or more evidence lines,
then a short explanation. Use exactly this shape:

FINDING: <short specific title> | CATEGORY: <category> | SEVERITY: <high|medium|low>
PAGE: <n> | QUOTE: <verbatim text copied from that page>
<one or two sentences explaining why this matters>

Give one PAGE/QUOTE line per passage you are relying on — a contradiction needs two, one
for each side of it. Copy quotes exactly as they appear in the page text; they are checked
in code against the page you name, and a quote that cannot be found there is discarded.

Leave a blank line between findings. If you find nothing worth reporting, write NO FINDINGS.
"""


def parse_findings(text: str, page_text: list[str]) -> list[RedFlag]:
    """Parse findings and verify each piece of evidence against the document.

    A finding whose quotes all fail verification still reaches the memo — the
    observation may be sound even if the model mangled the supporting text — but
    it arrives with no citations, which is what marks it for review. Dropping it
    silently would hide a real risk; presenting it as sourced would be a lie.
    """
    verifier = QuoteVerifier(page_text)
    flags: list[RedFlag] = []
    matches = list(_FINDING.finditer(text))

    for i, m in enumerate(matches):
        cat = _VALID_CATEGORIES.get(m.group("cat").strip().lower())
        if cat is None:
            continue
        try:
            sev = Severity(m.group("sev").strip().lower())
        except ValueError:
            sev = Severity.LOW

        # Everything between this finding line and the next belongs to it.
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]

        citations: list[Citation] = []
        for ev in _EVIDENCE.finditer(body):
            quote = ev.group("quote").strip().strip("*_`\"'").strip()
            page = verifier.locate(quote, int(ev.group("page")))
            if page is not None:
                citations.append(Citation(page=page, quote=quote[:600]))

        detail = _EVIDENCE.sub("", body).strip()
        flags.append(
            RedFlag(
                category=cat,
                severity=sev,
                title=m.group("title").strip()[:200],
                detail=detail[:1200] or m.group("title").strip(),
                source=DetectionSource.MODEL,
                citations=citations,
            )
        )

    return flags


def run(client: LLMClient, doc: CachedDocument) -> list[RedFlag]:
    prompt = load_prompt("llm_flags") + _FORMAT_INSTRUCTION
    text = client.extract_cited("llm_flags", doc, prompt)
    return parse_findings(text, doc.page_text)


def dedupe(rule_flags: list[RedFlag], model_flags: list[RedFlag]) -> list[RedFlag]:
    """Drop model findings that restate a rule finding.

    The rules engine wins on overlap: its finding is reproducible and carries a
    threshold a reviewer can inspect. Matching is by category plus numeric
    overlap in the title, which is crude but errs toward keeping the model
    finding — a duplicate is cheaper than a dropped signal.
    """

    def nums(s: str) -> set[str]:
        return set(re.findall(r"\d+\.?\d*", s))

    kept: list[RedFlag] = []
    for mf in model_flags:
        dup = False
        for rf in rule_flags:
            if mf.category != rf.category:
                continue
            shared = nums(mf.title) & nums(rf.title)
            if shared:
                dup = True
                break
        if not dup:
            kept.append(mf)
    return rule_flags + kept
