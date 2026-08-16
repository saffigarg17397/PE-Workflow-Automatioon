"""Render a MemoRun to Markdown.

Section 7 (sources and confidence) is emitted unconditionally, including when
everything extracted cleanly — its absence would be the more interesting signal.
"""

from __future__ import annotations

from app.models.flags import DetectionSource
from app.models.memo import MemoRun

_REC_LABEL = {
    "advance": "ADVANCE",
    "more_info": "MORE INFORMATION REQUIRED",
    "pass": "PASS",
}


def render(run: MemoRun) -> str:
    m = run.memo
    p = run.profile
    L: list[str] = []

    L.append(f"# Investment Committee Screening Memo — {m.company_name}")
    L.append("")
    L.append(
        f"*Screened against `{m.thesis_name}` · source: {run.source_file} "
        f"({run.page_count}pp) · generated {m.generated_at:%Y-%m-%d %H:%M} UTC · "
        f"run `{run.run_id}`*"
    )
    L.append("")
    L.append(
        "> Machine-generated first draft. Every figure below is cited to a source "
        "page. Uncited prose is model-generated and unverified. This is not an "
        "investment recommendation."
    )
    L.append("")

    # 1
    L.append(f"## 1. Recommendation: {_REC_LABEL[m.recommendation.value]}")
    L.append("")
    L.append(m.recommendation_rationale)
    L.append("")

    # 2
    L.append("## 2. Business Overview")
    L.append("")
    L.append(m.business_overview or "_Not generated._")
    L.append("")

    # 3
    L.append("## 3. Financial Summary")
    L.append("")
    years = p.financials.value or []
    if years:
        cite = "".join(c.render() for c in p.financials.unique_citations[:2])
        L.append(f"**Historical performance** ($M){cite}")
        L.append("")
        hdr = ["Metric"] + [str(y.year) for y in sorted(years, key=lambda y: y.year)]
        L.append("| " + " | ".join(hdr) + " |")
        L.append("|" + "---|" * len(hdr))
        srt = sorted(years, key=lambda y: y.year)
        for label, attr in (
            ("Revenue", "revenue"),
            ("Gross profit", "gross_profit"),
            ("Reported EBITDA", "reported_ebitda"),
            ("Adjusted EBITDA", "adjusted_ebitda"),
        ):
            vals = [getattr(y, attr) for y in srt]
            if all(v is None for v in vals):
                continue
            L.append(
                "| "
                + label
                + " | "
                + " | ".join(f"{v:,.2f}" if v is not None else "n/a" for v in vals)
                + " |"
            )
        margins = [y.ebitda_margin for y in srt]
        if any(x is not None for x in margins):
            L.append(
                "| Adj. EBITDA margin | "
                + " | ".join(f"{v:.1f}%" if v is not None else "n/a" for v in margins)
                + " |"
            )
        L.append("")

    derived = []
    if p.revenue_cagr is not None:
        derived.append(f"Revenue CAGR **{p.revenue_cagr:.1f}%**")
    if p.addback_pct_of_ebitda is not None:
        derived.append(f"add-backs **{p.addback_pct_of_ebitda:.1f}%** of adjusted EBITDA")
    if p.asking_multiple.value is not None:
        am = p.asking_multiple
        cites = "".join(c.render() for c in am.unique_citations)
        derived.append(f"asking multiple **{am.value:.1f}x**{cites}")
    if derived:
        L.append("Derived: " + " · ".join(derived) + ".")
        L.append("")
    L.append(m.financial_summary or "_Not generated._")
    L.append("")

    # 4
    fit = m.thesis_fit
    L.append(f"## 4. Thesis Fit — {fit.score:.0f}% ({fit.thesis_name})")
    L.append("")
    L.append(f"*Coverage: {fit.coverage:.0f}% of criteria evaluable from this document.*")
    L.append("")
    L.append("| Criterion | Result | Actual | Target |")
    L.append("|---|---|---|---|")
    for c in fit.criteria:
        mark = {True: "pass", False: "**fail**", None: "n/a"}[c.passed]
        if c.is_dealbreaker and c.passed is False:
            mark = "**FAIL — dealbreaker**"
        L.append(f"| {c.name} | {mark} | {c.actual} | {c.target} |")
    L.append("")
    L.append(m.thesis_commentary or "")
    L.append("")

    # 5
    L.append("## 5. Key Risks")
    L.append("")
    if not m.red_flags:
        L.append("No red flags identified by either the rules engine or the review pass.")
        L.append("")
    else:
        rule_flags = [f for f in m.flags_by_severity if f.source == DetectionSource.RULE]
        model_flags = [f for f in m.flags_by_severity if f.source == DetectionSource.MODEL]
        if rule_flags:
            L.append("**Detected by rules** (deterministic, reproducible)")
            L.append("")
            for f in rule_flags:
                L.append(f"- `{f.severity.value.upper()}` {f.render()}")
            L.append("")
        if model_flags:
            L.append("**Identified in review** (qualitative — verify before relying on)")
            L.append("")
            for f in model_flags:
                L.append(f"- `{f.severity.value.upper()}` {f.render()}")
            L.append("")

    # 6
    L.append("## 6. Diligence Priorities")
    L.append("")
    if m.diligence_priorities:
        for i, d in enumerate(m.diligence_priorities, 1):
            L.append(f"{i}. {d}")
    else:
        L.append("_None generated._")
    L.append("")

    # 7
    L.append("## 7. Sources and Confidence")
    L.append("")
    L.append(m.confidence_note)
    L.append("")
    if m.unreviewed_fields:
        L.append("**Pending human review** — extracted but below high confidence, or uncited:")
        L.append("")
        for field_name in m.unreviewed_fields:
            cited = getattr(p, field_name)
            reason = cited.note or f"confidence: {cited.confidence.value}"
            L.append(f"- `{field_name}` — {reason}")
        L.append("")
    if m.not_found:
        L.append("**Not found in source document:** " + ", ".join(f"`{f}`" for f in m.not_found))
        L.append("")

    L.append("---")
    L.append("")
    L.append(
        f"*Pipeline cost ${run.total_cost_usd:.4f} · {run.total_latency_ms / 1000:.1f}s · "
        f"{len(run.telemetry)} model calls · {run.cache_savings_tokens:,} tokens served from cache*"
    )
    L.append("")
    L.append("| Stage | Effort | In | Out | Cached | Latency | Cost |")
    L.append("|---|---|---|---|---|---|---|")
    for t in run.telemetry:
        L.append(
            f"| {t.stage} | {t.effort} | {t.input_tokens:,} | {t.output_tokens:,} | "
            f"{t.cache_read_tokens:,} | {t.latency_ms / 1000:.1f}s | ${t.cost_usd:.4f} |"
        )
    L.append("")
    return "\n".join(L)
