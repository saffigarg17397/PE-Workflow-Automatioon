"""Run persistence.

The MemoRun is stored as JSON rather than shredded into relational tables. The
schema is Pydantic-owned and still moving; a normalized store would mean a
migration every time a field is added, for no query benefit at demo scale.
Columns exist only for what the run list needs to filter and sort on.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, desc
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import settings
from app.models.memo import MemoRun


class Base(DeclarativeBase):
    pass


class RunRecord(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    company_name: Mapped[str] = mapped_column(String(256))
    source_file: Mapped[str] = mapped_column(String(256))
    thesis_name: Mapped[str] = mapped_column(String(64))
    recommendation: Mapped[str] = mapped_column(String(16))
    fit_score: Mapped[float] = mapped_column(Float)
    flag_count: Mapped[int] = mapped_column(Integer)
    pending_count: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    payload: Mapped[str] = mapped_column(Text)

    def to_run(self) -> MemoRun:
        return MemoRun(**json.loads(self.payload))


_engine = create_engine(settings.db_url, future=True)
_Session = sessionmaker(bind=_engine, future=True)


def init_db() -> None:
    Base.metadata.create_all(_engine)


@contextmanager
def session() -> Iterator[Session]:
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def save(run: MemoRun) -> None:
    rec = RunRecord(
        run_id=run.run_id,
        company_name=run.memo.company_name,
        source_file=run.source_file,
        thesis_name=run.memo.thesis_name,
        recommendation=run.memo.recommendation.value,
        fit_score=run.memo.thesis_fit.score,
        flag_count=len(run.memo.red_flags),
        pending_count=len(run.memo.unreviewed_fields),
        cost_usd=run.total_cost_usd,
        latency_ms=run.total_latency_ms,
        created_at=run.started_at,
        payload=run.model_dump_json(),
    )
    with session() as s:
        s.merge(rec)


def get(run_id: str) -> MemoRun | None:
    with session() as s:
        rec = s.get(RunRecord, run_id)
        return rec.to_run() if rec else None


@dataclass
class RunSummary:
    """Detached view for the run list.

    Returning ORM instances would hand the template objects whose session has
    already closed, and every attribute access raises DetachedInstanceError.
    """

    run_id: str
    company_name: str
    source_file: str
    thesis_name: str
    recommendation: str
    fit_score: float
    flag_count: int
    pending_count: int
    cost_usd: float
    created_at: datetime


def list_runs(limit: int = 50) -> list[RunSummary]:
    with session() as s:
        rows = s.query(RunRecord).order_by(desc(RunRecord.created_at)).limit(limit).all()
        return [
            RunSummary(
                run_id=r.run_id,
                company_name=r.company_name,
                source_file=r.source_file,
                thesis_name=r.thesis_name,
                recommendation=r.recommendation,
                fit_score=r.fit_score,
                flag_count=r.flag_count,
                pending_count=r.pending_count,
                cost_usd=r.cost_usd,
                created_at=r.created_at,
            )
            for r in rows
        ]


def delete(run_id: str) -> None:
    with session() as s:
        rec = s.get(RunRecord, run_id)
        if rec:
            s.delete(rec)
