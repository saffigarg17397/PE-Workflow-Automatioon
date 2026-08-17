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


def check_api_key() -> str | None:
    """Return an actionable message if the key is missing or unusable.

    The placeholder check matters more than the missing check: copying
    .env.example and forgetting to edit it leaves a syntactically fine key that
    loads cleanly and then fails as a 401 partway through a paid run. Catching
    it here costs nothing and saves a confusing mid-flight failure.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    env_file = ROOT / ".env"

    if key and "your-key-here" not in key:
        if not key.startswith("sk-ant-"):
            return (
                f"ANTHROPIC_API_KEY does not look like an Anthropic key "
                f"(expected it to start with 'sk-ant-', got '{key[:8]}...').\n"
                "  Check you copied the whole key from console.anthropic.com."
            )
        return None

    if key:
        return (
            f"ANTHROPIC_API_KEY is still the placeholder from .env.example.\n\n"
            f"  Open {env_file.name} and replace 'sk-ant-your-key-here' with your real key\n"
            "  from console.anthropic.com -> API Keys."
        )

    if not env_file.exists():
        return (
            "ANTHROPIC_API_KEY is not set.\n\n"
            "  cp .env.example .env      # then put your key in it\n"
            "  ...or: export ANTHROPIC_API_KEY=sk-ant-..."
        )

    return (
        f"ANTHROPIC_API_KEY is not set, though {env_file.name} exists.\n\n"
        "  Check the file contains a line reading:\n"
        "    ANTHROPIC_API_KEY=sk-ant-...\n"
        "  with no leading '#'.\n\n"
        "  Or set it for this shell only:\n"
        "    export ANTHROPIC_API_KEY=sk-ant-..."
    )


def progress(stage: str, status: str) -> None:
    icon = "..." if status == "running" else " ok"
    print(f"  [{icon}] {stage:<10} {status if status != 'running' else ''}".rstrip())


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate an IC screening memo from a CIM.")
    ap.add_argument("cim", nargs="?", help="Path to a CIM PDF (default: the first sample)")
    ap.add_argument("--thesis", default=None, help=f"One of: {', '.join(available_theses())}")
    ap.add_argument("--all", action="store_true", help="Run every sample CIM")
    ap.add_argument(
        "--seed",
        action="store_true",
        help="Also export each run to data/seed_runs/ for a hosted read-only demo",
    )
    args = ap.parse_args()

    if err := check_api_key():
        print(err)
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
            if e.terminal:
                print("\nStopped — remaining documents skipped.")
                break
            continue
        except (FileNotFoundError, ValueError) as e:
            print(f"  FAILED: {e}")
            failures += 1
            continue

        out_path = OUT / f"{path.stem}.md"
        out_path.write_text(markdown.render(run))

        if args.seed:
            from app.storage import seed

            seed_path = seed.export_run(run)
            print(f"  -> {seed_path.relative_to(ROOT)} (seed)")

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
