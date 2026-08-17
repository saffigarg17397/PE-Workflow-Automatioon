"""Qualitative red-flag detection.

Scoped deliberately to what the rules engine cannot reach: cross-section
inconsistencies, contract structure, disclosure gaps, positioning claims. The
prompt tells the model the arithmetic checks are already covered, so it spends
its effort on reading rather than recomputing ratios it would do worse than
Python at.

Findings are parsed from a delimited text format rather than structured outputs
because this pass needs citations, and citations and structured outputs cannot
share a request.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import load_prompt
from app.llm.client import CachedDocument, LLMClient
from app.models.citation import Citation
from app.models.flags import DetectionSource, FlagCategory, RedFlag, Severity
from app.pipeline.extract import _citations_from_block

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

_FORMAT_INSTRUCTION = """

Format each finding as a single line beginning with FINDING:, followed by the evidence
in the lines beneath it. Use exactly this shape:

FINDING: <short specific title> | CATEGORY: <category> | SEVERITY: <high|medium|low>
<the evidence, quoting the relevant passage or table row>

Leave a blank line between findings. If you find nothing worth reporting, write NO FINDINGS.
"""


def parse_findings(resp: Any) -> list[RedFlag]:
    """Walk response blocks, attaching citations to the most recent FINDING line.

    Same accumulation pattern as the cited extraction pass: blocks don't align
    1:1 with findings, so we track the current finding as we scan.
    """
    flags: list[RedFlag] = []
    current: RedFlag | None = None
    detail_buf: list[str] = []

    def _flush() -> None:
        nonlocal current, detail_buf
        if current is not None:
            detail = " ".join(detail_buf).strip()
            current.detail = detail[:1200] or current.title
            flags.append(current)
        current = None
        detail_buf = []

    for block in resp.content:
        if getattr(block, "type", None) != "text":
            continue
        text: str = block.text
        cites: list[Citation] = _citations_from_block(block)

        matches = list(_FINDING.finditer(text))
        if not matches:
            if current is not None:
                detail_buf.append(text.strip())
                current.citations.extend(cites)
            continue

        for i, m in enumerate(matches):
            _flush()
            cat = _VALID_CATEGORIES.get(m.group("cat").strip().lower())
            if cat is None:
                continue
            try:
                sev = Severity(m.group("sev").strip().lower())
            except ValueError:
                sev = Severity.LOW

            current = RedFlag(
                category=cat,
                severity=sev,
                title=m.group("title").strip()[:200],
                detail="",
                source=DetectionSource.MODEL,
                citations=list(cites),
            )
            # Text between this finding line and the next is its evidence.
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            detail_buf.append(text[start:end].strip())

    _flush()
    return flags


def run(client: LLMClient, doc: CachedDocument) -> list[RedFlag]:
    prompt = load_prompt("llm_flags") + _FORMAT_INSTRUCTION
    resp = client.extract_cited("llm_flags", doc, prompt)
    return parse_findings(resp)


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
