"""Pipeline sequencing.

One place that knows the stage order, so telemetry, error handling and progress
reporting are uniform across every entry point (CLI, API, eval harness).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from app.analysis import llm_flags, rules, scoring
from app.config import Thesis, load_thesis
from app.llm.client import LLMClient, LLMError
from app.models.memo import MemoRun
from app.pipeline import draft, extract, ingest

ProgressFn = Callable[[str, str], None]


def _noop(stage: str, status: str) -> None:
    pass


def run(
    path: str | Path,
    thesis: Thesis | str | None = None,
    client: LLMClient | None = None,
    on_progress: ProgressFn | None = None,
) -> MemoRun:
    """Run the full pipeline against one CIM.

    Raises LLMError on an unrecoverable API failure; callers decide whether that
    is fatal (CLI) or a per-document skip (batch eval).
    """
    progress = on_progress or _noop
    client = client or LLMClient()
    if not isinstance(thesis, Thesis):
        thesis = load_thesis(thesis)

    started = datetime.utcnow()
    run_id = uuid.uuid4().hex[:12]

    progress("ingest", "running")
    doc = ingest.run(path)
    progress("ingest", f"done ({doc.page_count}pp)")

    progress("extract", "running")
    profile, health = extract.run(client, doc.doc)
    if health.degraded:
        progress("extract", f"DEGRADED — citation rate {health.citation_rate:.0f}%")
    else:
        progress("extract", f"done ({len(profile.fields_needing_review())} field(s) for review)")

    progress("analyze", "running")
    rule_flags = rules.run(profile)
    try:
        model_flags = llm_flags.run(client, doc.doc)
    except LLMError:
        # A failed qualitative pass degrades the run rather than sinking it —
        # the deterministic findings still stand on their own.
        model_flags = []
    flags = llm_flags.dedupe(rule_flags, model_flags)
    health.flags_parsed = len(model_flags)
    fit = scoring.run(profile, thesis)
    progress("analyze", f"done ({len(flags)} flag(s), fit {fit.score:.0f}%)")

    progress("draft", "running")
    memo = draft.run(client, doc.doc, profile, fit, flags, health)
    progress("draft", f"done ({memo.recommendation.value})")

    return MemoRun(
        run_id=run_id,
        source_file=Path(path).name,
        page_count=doc.page_count,
        profile=profile,
        memo=memo,
        telemetry=list(client.telemetry),
        started_at=started,
        completed_at=datetime.utcnow(),
    )
