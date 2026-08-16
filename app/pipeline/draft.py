"""Memo drafting.

The model writes prose sections only. The recommendation itself, the thesis
table, and the flag list are computed — a screening call that depends on a
model's mood is not one an investment team can rely on, and the deterministic
path is the one you can defend in an IC meeting.

Everything handed to the model is pre-cited: the context block carries page
markers per field, so the model's job is to place citations it was given rather
than to generate them.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.config import load_prompt
from app.llm.client import CachedDocument, LLMClient
from app.models.deal import DealProfile
from app.models.flags import RedFlag, Severity, ThesisFit
from app.models.memo import Memo, Recommendation

_SECTION = re.compile(r"^##\s*(\w+)\s*$", re.MULTILINE)

_FORMAT_INSTRUCTION = """

Return the sections as markdown headings with these exact names, in this order:

## recommendation_rationale
## business_overview
## financial_summary
## thesis_commentary
## diligence_priorities

Under `diligence_priorities`, write one item per line beginning with "- ".
Write nothing outside these five sections.
"""


def decide(fit: ThesisFit, flags: list[RedFlag]) -> tuple[Recommendation, str]:
    """Compute the recommendation from evidence, not from model judgment.

    Ordering matters: a dealbreaker outranks a good score, and a high-severity
    flag outranks a passing grade. Coverage gates the whole thing — a high score
    computed from three of nine criteria is not a recommendation, it's noise.
    """
    highs = [f for f in flags if f.severity == Severity.HIGH]

    if fit.dealbreakers_hit:
        return Recommendation.PASS, (
            f"Fails dealbreaker criteria: {', '.join(fit.dealbreakers_hit)}."
        )
    if fit.coverage < 50:
        return Recommendation.MORE_INFO, (
            f"Only {fit.coverage:.0f}% of thesis criteria could be evaluated from the "
            f"CIM; the profile is too incomplete to screen."
        )
    if len(highs) >= 2:
        return Recommendation.PASS, (
            f"{len(highs)} high-severity issues identified: "
            f"{'; '.join(f.title for f in highs[:3])}."
        )
    if highs:
        return Recommendation.MORE_INFO, (
            f"One high-severity issue requires resolution before advancing: {highs[0].title}."
        )
    if fit.score >= 70:
        return Recommendation.ADVANCE, (
            f"Scores {fit.score:.0f}% against the {fit.thesis_name} thesis with no "
            f"high-severity issues identified."
        )
    if fit.score >= 50:
        return Recommendation.MORE_INFO, (
            f"Scores {fit.score:.0f}% against the {fit.thesis_name} thesis — below the "
            f"advance threshold but without disqualifying issues."
        )
    return Recommendation.PASS, (
        f"Scores {fit.score:.0f}% against the {fit.thesis_name} thesis."
    )


def build_context(profile: DealProfile, fit: ThesisFit, flags: list[RedFlag]) -> str:
    """Render the model's input: every fact with its page marker attached.

    The model never sees the raw PDF here — only extracted, cited values. That
    is what makes "never introduce a number you weren't given" enforceable
    rather than aspirational.
    """
    lines: list[str] = ["## EXTRACTED PROFILE (value followed by source page)"]

    for name in type(profile).model_fields:
        cited = getattr(profile, name)
        if cited.value is None:
            continue
        val = cited.value
        if isinstance(val, list):
            if not val:
                continue
            rendered = json.dumps(
                [v.model_dump() if hasattr(v, "model_dump") else v for v in val],
                default=str,
            )[:1400]
        else:
            rendered = str(val)
        cites = "".join(c.render() for c in cited.citations) or "[uncited]"
        lines.append(f"- {name}: {rendered} {cites} (confidence: {cited.confidence.value})")

    lines.append("")
    lines.append("## DERIVED METRICS")
    for label, val in (
        ("revenue_cagr_pct", profile.revenue_cagr),
        ("addback_pct_of_ebitda", profile.addback_pct_of_ebitda),
        ("margin_trend", profile.margin_trend),
    ):
        if val is not None:
            lines.append(f"- {label}: {val}")

    lines.append("")
    lines.append(f"## THESIS FIT — {fit.thesis_name} (score {fit.score:.0f}%, coverage {fit.coverage:.0f}%)")
    for c in fit.criteria:
        mark = {True: "PASS", False: "FAIL", None: "NOT EVALUABLE"}[c.passed]
        db = " [DEALBREAKER]" if c.is_dealbreaker else ""
        lines.append(f"- {c.name}: {mark}{db} — actual {c.actual}, target {c.target}")

    lines.append("")
    lines.append("## RED FLAGS")
    if not flags:
        lines.append("- None identified.")
    for f in sorted(flags, key=lambda f: f.severity.rank):
        cites = "".join(c.render() for c in f.citations)
        lines.append(f"- [{f.severity.value.upper()}][{f.source.value}] {f.title}{cites}: {f.detail}")

    lines.append("")
    lines.append("## FIELDS PENDING REVIEW (do not present these as established)")
    pending = profile.fields_needing_review()
    lines.append("- " + (", ".join(pending) if pending else "none"))

    return "\n".join(lines)


def parse_sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    parts = _SECTION.split(text)
    # split yields [preamble, name, body, name, body, ...]
    for i in range(1, len(parts) - 1, 2):
        out[parts[i].strip().lower()] = parts[i + 1].strip()
    return out


def run(
    client: LLMClient,
    doc: CachedDocument,
    profile: DealProfile,
    fit: ThesisFit,
    flags: list[RedFlag],
) -> Memo:
    rec, rationale = decide(fit, flags)

    prompt = (
        load_prompt("draft_memo")
        + _FORMAT_INSTRUCTION
        + "\n\n---\n\n"
        + build_context(profile, fit, flags)
        + f"\n\n---\n\nThe computed recommendation is: {rec.value.upper()} — {rationale}\n"
        "Write `recommendation_rationale` consistent with that determination; do not "
        "substitute your own conclusion."
    )

    # No document block: the model works from the cited context only, which is
    # what keeps it from sourcing numbers the extraction pass never validated.
    text = client.complete("draft", None, prompt, max_tokens=20000)
    sections = parse_sections(text)

    priorities = [
        line.lstrip("-• ").strip()
        for line in sections.get("diligence_priorities", "").splitlines()
        if line.strip().startswith(("-", "•"))
    ]

    pending = profile.fields_needing_review()
    not_found = [
        n
        for n in type(profile).model_fields
        if getattr(profile, n).value is None
    ]

    return Memo(
        company_name=profile.company_name.value or doc.name,
        thesis_name=fit.thesis_name,
        recommendation=rec,
        recommendation_rationale=sections.get("recommendation_rationale", rationale),
        business_overview=sections.get("business_overview", ""),
        financial_summary=sections.get("financial_summary", ""),
        thesis_fit=fit,
        thesis_commentary=sections.get("thesis_commentary", ""),
        red_flags=flags,
        diligence_priorities=priorities,
        unreviewed_fields=pending,
        not_found=not_found,
        confidence_note=(
            f"{len(pending)} field(s) below high confidence or uncited; "
            f"{len(not_found)} field(s) not found in the source document. "
            f"Figures in this memo are cited to source pages; uncited claims are "
            f"model-generated prose and should be verified."
        ),
    )
