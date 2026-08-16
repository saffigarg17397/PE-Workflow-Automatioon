"""Run the pipeline against one CIM and write the memo. `make demo` entry point."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import SAMPLE_CIMS, available_theses  # noqa: E402
from app.export import markdown  # noqa: E402
from app.llm.client import LLMError  # noqa: E402
from app.pipeline import orchestrator  # noqa: E402

OUT = ROOT / "data" / "memos"


def progress(stage: str, status: str) -> None:
    icon = "..." if status == "running" else " ok"
    print(f"  [{icon}] {stage:<10} {status if status != 'running' else ''}".rstrip())


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate an IC screening memo from a CIM.")
    ap.add_argument("cim", nargs="?", help="Path to a CIM PDF (default: the first sample)")
    ap.add_argument("--thesis", default=None, help=f"One of: {', '.join(available_theses())}")
    ap.add_argument("--all", action="store_true", help="Run every sample CIM")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set.\n")
        print("  cp .env.example .env   # then add your key")
        print("  export ANTHROPIC_API_KEY=sk-ant-...")
        return 1

    samples = sorted(SAMPLE_CIMS.glob("*.pdf"))
    if not samples:
        print("No sample CIMs found. Run `make cims` first.")
        return 1

    targets = samples if args.all else [Path(args.cim) if args.cim else samples[0]]
    OUT.mkdir(parents=True, exist_ok=True)
    failures = 0

    for path in targets:
        print(f"\n{path.name}")
        try:
            run = orchestrator.run(path, thesis=args.thesis, on_progress=progress)
        except LLMError as e:
            print(f"  FAILED: {e}")
            failures += 1
            continue
        except (FileNotFoundError, ValueError) as e:
            print(f"  FAILED: {e}")
            failures += 1
            continue

        out_path = OUT / f"{path.stem}.md"
        out_path.write_text(markdown.render(run))

        m = run.memo
        print(
            f"  -> {m.recommendation.value.upper():<10} "
            f"fit {m.thesis_fit.score:.0f}% · {len(m.red_flags)} flags · "
            f"${run.total_cost_usd:.4f} · {run.total_latency_ms / 1000:.0f}s"
        )
        print(f"  -> {out_path.relative_to(ROOT)}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
