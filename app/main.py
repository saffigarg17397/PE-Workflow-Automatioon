"""FastAPI app.

Server-rendered Jinja + HTMX. No build step and no SPA — a reviewer should be
able to clone this and run it, and a frontend toolchain is not the thing being
demonstrated.

Pipeline runs happen in a background thread with progress polled over HTMX,
because a full run takes tens of seconds and blocking the request would time out
in front of the user.
"""

from __future__ import annotations

import tempfile
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import SAMPLE_CIMS, available_theses, settings
from app.export import docx as docx_export
from app.export import markdown
from app.llm.client import LLMError
from app.pipeline import orchestrator
from app.storage import db

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "web" / "templates"))


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    yield


app = FastAPI(title="CIM → IC Memo", docs_url="/api/docs", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "web" / "static")), name="static")


@dataclass
class Job:
    job_id: str
    filename: str
    stages: list[tuple[str, str]] = field(default_factory=list)
    run_id: str | None = None
    error: str | None = None
    done: bool = False


_JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()

# In-flight jobs are held in memory only — they exist to drive the progress
# poll, and the durable record is the saved MemoRun. Without a bound this dict
# grows for the life of the process.
_MAX_JOBS = 200


def _prune_jobs() -> None:
    """Drop the oldest finished jobs once the store is over its bound.

    Caller must hold _LOCK. Running jobs are never evicted — losing one would
    orphan a live progress poll.
    """
    if len(_JOBS) <= _MAX_JOBS:
        return
    finished = [jid for jid, j in _JOBS.items() if j.done]
    for jid in finished[: len(_JOBS) - _MAX_JOBS]:
        del _JOBS[jid]


def _run_job(job: Job, path: Path, thesis: str | None, cleanup: bool) -> None:
    def progress(stage: str, status: str) -> None:
        with _LOCK:
            job.stages = [s for s in job.stages if s[0] != stage] + [(stage, status)]

    try:
        run = orchestrator.run(path, thesis=thesis, on_progress=progress)
        db.save(run)
        with _LOCK:
            job.run_id = run.run_id
    except (LLMError, ValueError, FileNotFoundError) as e:
        with _LOCK:
            job.error = str(e)
    except Exception as e:  # noqa: BLE001 - surface anything else rather than hang the UI
        with _LOCK:
            job.error = f"Unexpected error: {e}"
    finally:
        with _LOCK:
            job.done = True
        if cleanup:
            path.unlink(missing_ok=True)


# ------------------------------------------------------------------- pages


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> Any:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "runs": db.list_runs(),
            "samples": sorted(p.name for p in SAMPLE_CIMS.glob("*.pdf")),
            "theses": available_theses(),
            "default_thesis": settings.default_thesis,
        },
    )


@app.get("/memo/{run_id}", response_class=HTMLResponse)
def memo(request: Request, run_id: str) -> Any:
    run = db.get(run_id)
    if not run:
        raise HTTPException(404, "No such run")
    return templates.TemplateResponse(
        request, "memo.html", {"run": run, "memo": run.memo, "profile": run.profile}
    )


@app.get("/review/{run_id}", response_class=HTMLResponse)
def review(request: Request, run_id: str) -> Any:
    """Human-in-the-loop queue: every field below high confidence, with its
    source page and the reason it was flagged."""
    run = db.get(run_id)
    if not run:
        raise HTTPException(404, "No such run")
    items = []
    for name in run.memo.unreviewed_fields:
        cited = getattr(run.profile, name)
        items.append(
            {
                "field": name,
                "value": cited.value,
                "confidence": cited.confidence.value,
                "note": cited.note or "",
                "citations": cited.unique_citations,
            }
        )
    return templates.TemplateResponse(request, "review.html", {"run": run, "items": items})


# ------------------------------------------------------------------ actions


@app.post("/run", response_class=HTMLResponse)
async def start_run(
    request: Request,
    thesis: str = Form(default=""),
    sample: str = Form(default=""),
    upload: UploadFile | None = None,
) -> Any:
    # Validate the thesis against the real listing before starting work, so a
    # bad name is a 400 here rather than a background failure the user only
    # sees after the progress spinner. `load_thesis` interpolates the name into
    # a path, so an unvalidated value is also a traversal vector.
    if thesis and thesis not in available_theses():
        raise HTTPException(400, "Unknown thesis")

    if sample:
        # Membership check against the actual listing, not a string test on the
        # input. `SAMPLE_CIMS / "/etc/passwd"` yields "/etc/passwd" — pathlib
        # discards the base when the right operand is absolute — so prefix or
        # ".." filtering here would not be sufficient.
        if sample not in {p.name for p in SAMPLE_CIMS.glob("*.pdf")}:
            raise HTTPException(400, "Unknown sample")
        path = SAMPLE_CIMS / sample
        cleanup = False
        name = sample
    elif upload is not None and upload.filename:
        if not upload.filename.lower().endswith(".pdf"):
            raise HTTPException(400, "Only PDF uploads are supported")
        limit = settings.max_upload_mb * 1024 * 1024
        tmp = Path(tempfile.gettempdir()) / f"cim_{uuid.uuid4().hex}.pdf"
        # Enforce the cap while streaming rather than after: copying first means
        # an oversized upload is fully written to disk before being rejected.
        written = 0
        try:
            with tmp.open("wb") as fh:
                while chunk := await upload.read(1024 * 1024):
                    written += len(chunk)
                    if written > limit:
                        raise HTTPException(400, f"File exceeds {settings.max_upload_mb}MB")
                    fh.write(chunk)
        except HTTPException:
            tmp.unlink(missing_ok=True)
            raise
        path, cleanup, name = tmp, True, Path(upload.filename).name
    else:
        raise HTTPException(400, "Choose a sample or upload a PDF")

    job = Job(job_id=uuid.uuid4().hex[:12], filename=name)
    with _LOCK:
        _prune_jobs()
        _JOBS[job.job_id] = job
    threading.Thread(
        target=_run_job, args=(job, path, thesis or None, cleanup), daemon=True
    ).start()

    return templates.TemplateResponse(request, "_progress.html", {"job": job})


@app.get("/progress/{job_id}", response_class=HTMLResponse)
def progress(request: Request, job_id: str) -> Any:
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "No such job")
    return templates.TemplateResponse(request, "_progress.html", {"job": job})


@app.get("/export/{run_id}.md", response_class=PlainTextResponse)
def export_md(run_id: str) -> Any:
    run = db.get(run_id)
    if not run:
        raise HTTPException(404, "No such run")
    return PlainTextResponse(
        markdown.render(run),
        headers={"Content-Disposition": f'attachment; filename="{run_id}.md"'},
    )


@app.get("/export/{run_id}.docx")
def export_docx(run_id: str) -> Any:
    run = db.get(run_id)
    if not run:
        raise HTTPException(404, "No such run")
    return Response(
        docx_export.render(run),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{run_id}.docx"'},
    )


@app.post("/delete/{run_id}")
def delete_run(run_id: str) -> Any:
    db.delete(run_id)
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"
