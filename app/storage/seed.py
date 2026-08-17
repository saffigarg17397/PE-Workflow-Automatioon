"""Seed runs — pre-generated memos committed as fixtures.

A hosted demo should not run the pipeline for visitors: it holds the API key
server-side, so every click costs the owner money and a crawler could cost a
lot of it. Instead the owner generates memos once locally, exports them here,
and the deployed instance serves them read-only.

The exported JSON is a full `MemoRun`, so a seeded memo is byte-identical to a
live one — same citations, same review queue, same telemetry. Nothing is
simulated for display.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.models.memo import MemoRun
from app.storage import db

SEED_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "seed_runs"


def export_run(run: MemoRun, out_dir: Path | None = None) -> Path:
    """Write one run to the seed directory, named by source document.

    Named by source rather than run id so re-running a document replaces its
    seed instead of accumulating near-duplicates in the committed corpus.
    """
    out_dir = out_dir or SEED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{Path(run.source_file).stem}.json"
    path.write_text(run.model_dump_json(indent=2))
    return path


def available() -> list[Path]:
    return sorted(SEED_DIR.glob("*.json")) if SEED_DIR.exists() else []


def load_all(replace: bool = False) -> int:
    """Import every seed run into the database. Returns the number loaded.

    Idempotent by default: a run already present is skipped, so restarting a
    deployed instance does not duplicate rows. Malformed seeds are skipped with
    a warning rather than crashing startup — a bad fixture should not take the
    whole site down.
    """
    loaded = 0
    for path in available():
        try:
            run = MemoRun(**json.loads(path.read_text()))
        except Exception as e:  # noqa: BLE001 - a bad seed must not break boot
            print(f"[seed] skipping {path.name}: {e}")
            continue
        if not replace and db.get(run.run_id) is not None:
            continue
        db.save(run)
        loaded += 1
    return loaded
