"""Extraction eval harness.

Scores the pipeline against the ground truth emitted alongside each synthetic
CIM. This is the only thing in the repo that makes real API calls, and it is the
number worth reporting: extraction accuracy field-by-field, plus red-flag
detection recall and precision.

Scoring policy, stated because it changes what the numbers mean:

  * Numeric fields use a relative tolerance band (default 2%). A CIM states
    figures to one or two decimals; demanding exact float equality would measure
    rounding, not extraction.
  * A field the model declined to extract scores as a miss, not as an error —
    but misses and wrong-values are reported separately, because a tool that
    says "not found" is behaving correctly where one that invents a number is
    not. Collapsing them into one accuracy figure would hide the distinction
    that matters most for a diligence tool.
  * Flag detection is scored as set precision/recall over categories, not
    individual findings, since wording varies between runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import SAMPLE_CIMS, load_thesis  # noqa: E402
from app.llm.client import LLMError  # noqa: E402
from app.models.deal import DealProfile  # noqa: E402
from app.models.memo import MemoRun  # noqa: E402
from app.pipeline import orchestrator  # noqa: E402
from app.storage import seed  # noqa: E402

GT_DIR = Path(__file__).resolve().parent / "ground_truth"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

TOLERANCE = 0.02  # 2% relative, for numeric fields


@dataclass
class FieldScore:
    field: str
    expected: Any
    actual: Any
    status: str  # correct | wrong | missed
    cited: bool = False


@dataclass
class DocScore:
    slug: str
    fields: list[FieldScore] = field(default_factory=list)
    expected_flags: set[str] = field(default_factory=set)
    detected_flags: set[str] = field(default_factory=set)
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: str | None = None

    @property
    def correct(self) -> int:
        return sum(1 for f in self.fields if f.status == "correct")

    @property
    def wrong(self) -> int:
        return sum(1 for f in self.fields if f.status == "wrong")

    @property
    def missed(self) -> int:
        return sum(1 for f in self.fields if f.status == "missed")

    @property
    def attempted(self) -> int:
        return self.correct + self.wrong

    @property
    def accuracy(self) -> float:
        """Correct / attempted. Excludes fields the tool declined to extract —
        those are reported separately as coverage."""
        return 100 * self.correct / self.attempted if self.attempted else 0.0

    @property
    def coverage(self) -> float:
        return 100 * self.attempted / len(self.fields) if self.fields else 0.0

    @property
    def citation_rate(self) -> float:
        """Share of correctly-extracted fields that carry a source page."""
        ok = [f for f in self.fields if f.status == "correct"]
        return 100 * sum(1 for f in ok if f.cited) / len(ok) if ok else 0.0

    @property
    def flag_recall(self) -> float | None:
        if not self.expected_flags:
            return None
        hit = len(self.expected_flags & self.detected_flags)
        return 100 * hit / len(self.expected_flags)

    @property
    def flag_precision(self) -> float | None:
        if not self.detected_flags:
            return None
        hit = len(self.expected_flags & self.detected_flags)
        return 100 * hit / len(self.detected_flags)


# Ground-truth key -> how to read it off the extracted profile.
_GETTERS: dict[str, Any] = {
    "company_name": lambda p: p.company_name,
    "headquarters": lambda p: p.headquarters,
    "year_founded": lambda p: p.year_founded,
    "employees": lambda p: p.employees,
    "recurring_revenue_pct": lambda p: p.recurring_revenue_pct,
    "top_customer_pct": lambda p: p.top_customer_pct,
    "top_five_customer_pct": lambda p: p.top_five_customer_pct,
    "customer_count": lambda p: p.customer_count,
    "asking_price": lambda p: p.asking_price,
    "asking_multiple": lambda p: p.asking_multiple,
    "acquisitions_completed": lambda p: p.acquisitions_completed,
}


def _latest(p: DealProfile, attr: str) -> Any:
    y = p.latest_year
    return getattr(y, attr, None) if y else None


def _compare(expected: Any, actual: Any) -> str:
    if actual is None:
        return "missed"
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        if expected == 0:
            return "correct" if actual == 0 else "wrong"
        return "correct" if abs(actual - expected) / abs(expected) <= TOLERANCE else "wrong"
    return "correct" if str(actual).strip().lower() == str(expected).strip().lower() else "wrong"


def score_document(gt: dict[str, Any], run: MemoRun) -> DocScore:
    p = run.profile
    score = DocScore(slug=gt["slug"])
    exp = gt["fields"]

    for key, getter in _GETTERS.items():
        if key not in exp:
            continue
        cited = getter(p)
        status = _compare(exp[key], cited.value)
        score.fields.append(
            FieldScore(key, exp[key], cited.value, status, cited=bool(cited.citations))
        )

    # Derived and latest-year fields
    for key, actual in (
        ("latest_revenue", _latest(p, "revenue")),
        ("latest_reported_ebitda", _latest(p, "reported_ebitda")),
        ("latest_adjusted_ebitda", _latest(p, "adjusted_ebitda")),
        ("revenue_cagr", p.revenue_cagr),
        ("addback_pct_of_ebitda", p.addback_pct_of_ebitda),
        ("latest_dso", (p.days_sales_outstanding.value or [None])[-1]),
    ):
        if key not in exp:
            continue
        score.fields.append(
            FieldScore(
                key,
                exp[key],
                actual,
                _compare(exp[key], actual),
                cited=bool(p.financials.citations),
            )
        )

    score.expected_flags = set(gt.get("expected_flags", []))
    score.detected_flags = {f.category.value for f in run.memo.red_flags}
    score.cost_usd = run.total_cost_usd
    score.latency_ms = run.total_latency_ms
    return score


def print_scorecard(scores: list[DocScore]) -> None:
    ok = [s for s in scores if s.error is None]
    w = 24
    print("\n" + "=" * 92)
    print("EXTRACTION SCORECARD")
    print("=" * 92)
    print(
        f"{'Document':<{w}} {'Acc':>7} {'Cov':>7} {'Cited':>7} {'Correct':>9} {'Wrong':>7} {'Missed':>7} {'Cost':>9}"
    )
    print("-" * 92)
    for s in scores:
        if s.error:
            print(f"{s.slug:<{w}} {'ERROR':>7}  {s.error[:52]}")
            continue
        print(
            f"{s.slug:<{w}} {s.accuracy:>6.1f}% {s.coverage:>6.1f}% {s.citation_rate:>6.1f}% "
            f"{s.correct:>9} {s.wrong:>7} {s.missed:>7} {'$' + format(s.cost_usd, '.4f'):>9}"
        )
    if ok:
        print("-" * 92)
        tc = sum(s.correct for s in ok)
        ta = sum(s.attempted for s in ok)
        tf = sum(len(s.fields) for s in ok)
        tw = sum(s.wrong for s in ok)
        tm = sum(s.missed for s in ok)
        cited_ok = sum(1 for s in ok for f in s.fields if f.status == "correct" and f.cited)
        print(
            f"{'TOTAL':<{w}} {100 * tc / ta if ta else 0:>6.1f}% {100 * ta / tf if tf else 0:>6.1f}% "
            f"{100 * cited_ok / tc if tc else 0:>6.1f}% {tc:>9} {tw:>7} {tm:>7} "
            f"{'$' + format(sum(s.cost_usd for s in ok), '.4f'):>9}"
        )

    print("\n" + "=" * 92)
    print("RED FLAG DETECTION (by category)")
    print("=" * 92)
    print(f"{'Document':<{w}} {'Recall':>8} {'Prec':>8}  {'Missed categories':<40}")
    print("-" * 92)
    for s in ok:
        r = s.flag_recall
        pr = s.flag_precision
        missed = sorted(s.expected_flags - s.detected_flags)
        print(
            f"{s.slug:<{w}} {(f'{r:.0f}%' if r is not None else 'n/a'):>8} "
            f"{(f'{pr:.0f}%' if pr is not None else 'n/a'):>8}  "
            f"{', '.join(missed) if missed else '-':<40}"
        )

    if ok:
        total_cost = sum(s.cost_usd for s in ok)
        total_ms = sum(s.latency_ms for s in ok)
        print("\n" + "-" * 92)
        print(
            f"{len(ok)} document(s) · ${total_cost:.4f} total · "
            f"${total_cost / len(ok):.4f} per memo · {total_ms / len(ok) / 1000:.0f}s per memo"
        )
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Score extraction against ground truth.")
    ap.add_argument("--thesis", default=None)
    ap.add_argument("--only", default=None, help="Run a single slug")
    ap.add_argument("--label", default="", help="Tag this run in the results file")
    ap.add_argument(
        "--from-seeds",
        action="store_true",
        help="Score the runs already in data/seed_runs/ instead of regenerating them (free)",
    )
    args = ap.parse_args()

    # Scoring a stored run needs no model call. Regenerating a memo purely to
    # score it doubles the cost of the corpus for no extra signal, so
    # `make seed && make eval --from-seeds` is the cheap path: generate once,
    # score the same artifacts you are going to ship.
    if not args.from_seeds and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set — the eval harness makes real API calls.")
        print("Set it, or run `make eval-seeds` to score already-generated runs for free.")
        return 1

    gts = sorted(GT_DIR.glob("*.yaml"))
    if args.only:
        gts = [g for g in gts if g.stem == args.only]
    if not gts:
        print(f"No ground truth in {GT_DIR}. Run `make cims` first.")
        return 1

    thesis = load_thesis(args.thesis)
    scores: list[DocScore] = []

    for gt_path in gts:
        gt = yaml.safe_load(gt_path.read_text())
        print(f"scoring {gt['slug']} ...", flush=True)

        if args.from_seeds:
            seed_path = seed.SEED_DIR / f"{gt['slug']}.json"
            if not seed_path.exists():
                scores.append(
                    DocScore(slug=gt["slug"], error="no seed run — run `make seed` first")
                )
                continue
            try:
                run = MemoRun(**json.loads(seed_path.read_text()))
            except Exception as e:  # noqa: BLE001 - report, don't abort the corpus
                scores.append(DocScore(slug=gt["slug"], error=f"unreadable seed: {e}"))
                continue
            # The seed was scored against the thesis it was generated with. Say
            # so rather than silently reporting it under the requested one.
            if run.memo.thesis_name != thesis.name:
                print(f"  (seeded under '{run.memo.thesis_name}', not '{thesis.name}')")
        else:
            pdf = SAMPLE_CIMS / f"{gt['slug']}.pdf"
            if not pdf.exists():
                scores.append(DocScore(slug=gt["slug"], error=f"missing {pdf.name}"))
                continue
            try:
                run = orchestrator.run(pdf, thesis=thesis)
            except (LLMError, ValueError) as e:
                scores.append(DocScore(slug=gt["slug"], error=str(e)))
                continue

        scores.append(score_document(gt, run))

    print_scorecard(scores)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS_DIR / f"eval_{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "timestamp": stamp,
                "label": args.label,
                "thesis": thesis.name,
                "source": "seed_runs" if args.from_seeds else "live",
                "tolerance": TOLERANCE,
                "documents": [
                    {
                        "slug": s.slug,
                        "error": s.error,
                        "accuracy": round(s.accuracy, 1),
                        "coverage": round(s.coverage, 1),
                        "citation_rate": round(s.citation_rate, 1),
                        "flag_recall": s.flag_recall,
                        "flag_precision": s.flag_precision,
                        "cost_usd": s.cost_usd,
                        "latency_ms": s.latency_ms,
                        "fields": [
                            {
                                "field": f.field,
                                "expected": f.expected,
                                "actual": f.actual,
                                "status": f.status,
                                "cited": f.cited,
                            }
                            for f in s.fields
                        ],
                    }
                    for s in scores
                ],
            },
            indent=2,
            default=str,
        )
    )
    print(f"results -> {out.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
