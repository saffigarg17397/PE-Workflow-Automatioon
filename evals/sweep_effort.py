"""Effort sweep — measure the cost/quality tradeoff instead of asserting it.

Effort, not model choice, is this pipeline's cost lever (see `config.STAGE_EFFORT`).
This runs the same documents at several effort levels and reports accuracy,
citation rate, cost and latency for each, so the setting in `config.py` is a
measured default rather than a guess.

The extraction stages are the ones swept: drafting effort changes prose quality,
which this harness cannot score, while extraction effort changes accuracy, which
it can.

    python -m evals.sweep_effort --levels low,medium,high --only brightpath_dental
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import SAMPLE_CIMS, STAGE_EFFORT, load_thesis  # noqa: E402
from app.llm.client import LLMError  # noqa: E402
from app.pipeline import orchestrator  # noqa: E402
from evals.run_evals import GT_DIR, RESULTS_DIR, score_document  # noqa: E402

SWEPT_STAGES = ("extract_structured", "extract_cited", "llm_flags")


@dataclass
class LevelResult:
    level: str
    accuracy: float
    coverage: float
    citation_rate: float
    flag_recall: float | None
    cost_usd: float
    latency_ms: int
    docs: int
    errors: int


def run_level(level: str, slugs: list[str], thesis_name: str | None) -> LevelResult:
    """Run the corpus at one effort level.

    STAGE_EFFORT is mutated and restored rather than threaded through the
    orchestrator: effort is deliberately a deployment setting, not a per-call
    argument, and adding a parameter just for this harness would put a knob in
    the production path that only the sweep uses.
    """
    original = dict(STAGE_EFFORT)
    for stage in SWEPT_STAGES:
        STAGE_EFFORT[stage] = level

    thesis = load_thesis(thesis_name)
    accs, covs, cites, recalls = [], [], [], []
    cost = 0.0
    latency = 0
    errors = 0

    try:
        for slug in slugs:
            pdf = SAMPLE_CIMS / f"{slug}.pdf"
            gt = yaml.safe_load((GT_DIR / f"{slug}.yaml").read_text())
            print(f"    {slug} ...", end="", flush=True)
            try:
                run = orchestrator.run(pdf, thesis=thesis)
            except (LLMError, ValueError) as e:
                print(f" ERROR: {e}")
                errors += 1
                if isinstance(e, LLMError) and e.terminal:
                    break
                continue
            s = score_document(gt, run)
            accs.append(s.accuracy)
            covs.append(s.coverage)
            cites.append(s.citation_rate)
            if s.flag_recall is not None:
                recalls.append(s.flag_recall)
            cost += s.cost_usd
            latency += s.latency_ms
            print(f" acc {s.accuracy:.0f}%  ${s.cost_usd:.4f}")
    finally:
        STAGE_EFFORT.clear()
        STAGE_EFFORT.update(original)

    n = max(len(accs), 1)
    return LevelResult(
        level=level,
        accuracy=round(sum(accs) / n, 1),
        coverage=round(sum(covs) / n, 1),
        citation_rate=round(sum(cites) / n, 1),
        flag_recall=round(sum(recalls) / len(recalls), 1) if recalls else None,
        cost_usd=round(cost, 4),
        latency_ms=latency,
        docs=len(accs),
        errors=errors,
    )


def print_table(results: list[LevelResult]) -> None:
    print("\n" + "=" * 84)
    print("EFFORT SWEEP — extraction stages")
    print("=" * 84)
    print(
        f"{'Effort':<10} {'Accuracy':>9} {'Coverage':>9} {'Cited':>8} "
        f"{'FlagRecall':>11} {'Cost/memo':>11} {'Sec/memo':>9}"
    )
    print("-" * 84)
    for r in results:
        per_doc = max(r.docs, 1)
        print(
            f"{r.level:<10} {r.accuracy:>8.1f}% {r.coverage:>8.1f}% {r.citation_rate:>7.1f}% "
            f"{(f'{r.flag_recall:.0f}%' if r.flag_recall is not None else 'n/a'):>11} "
            f"{'$' + format(r.cost_usd / per_doc, '.4f'):>11} "
            f"{r.latency_ms / per_doc / 1000:>8.0f}s"
        )
    print("-" * 84)

    scored = [r for r in results if r.docs]
    if len(scored) > 1:
        best = max(scored, key=lambda r: r.accuracy)
        cheapest = min(scored, key=lambda r: r.cost_usd / max(r.docs, 1))
        print(f"\nHighest accuracy : {best.level} ({best.accuracy:.1f}%)")
        print(
            f"Lowest cost      : {cheapest.level} "
            f"(${cheapest.cost_usd / max(cheapest.docs, 1):.4f}/memo)"
        )
        if best.level != cheapest.level:
            d_acc = best.accuracy - cheapest.accuracy
            d_cost = (best.cost_usd - cheapest.cost_usd) / max(best.docs, 1)
            print(
                f"\nTradeoff         : {d_acc:+.1f}pts accuracy for {d_cost:+.4f} USD/memo "
                f"moving {cheapest.level} -> {best.level}."
            )
            print(
                "If the accuracy delta is inside noise for your corpus, the cheaper "
                "level is the better default."
            )
        else:
            print("\nThe cheapest level is also the most accurate — use it.")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Sweep extraction effort levels.")
    ap.add_argument("--levels", default="low,medium,high", help="Comma-separated effort levels")
    ap.add_argument("--only", default=None, help="Single slug instead of the full corpus")
    ap.add_argument("--thesis", default=None)
    args = ap.parse_args()

    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        print("GEMINI_API_KEY is not set — the sweep makes real API calls.")
        return 1

    slugs = [args.only] if args.only else sorted(p.stem for p in GT_DIR.glob("*.yaml"))
    if not slugs:
        print("No ground truth found. Run `make cims` first.")
        return 1

    levels = [x.strip() for x in args.levels.split(",") if x.strip()]
    valid = {"low", "medium", "high", "xhigh", "max"}
    if bad := set(levels) - valid:
        print(
            f"Unknown effort level(s): {', '.join(sorted(bad))}. Valid: {', '.join(sorted(valid))}"
        )
        return 1

    print(f"Sweeping {levels} across {len(slugs)} document(s).")
    print(f"That is {len(levels) * len(slugs)} full pipeline runs — this costs real money.\n")

    results = []
    for level in levels:
        print(f"  effort={level}")
        results.append(run_level(level, slugs, args.thesis))

    print_table(results)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS_DIR / f"sweep_{stamp}.json"
    out.write_text(json.dumps([r.__dict__ for r in results], indent=2))
    print(f"results -> {out.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
