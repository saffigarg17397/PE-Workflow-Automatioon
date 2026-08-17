"""Web-layer tests.

Cover the wiring most likely to break silently: template rendering against real
model objects, the detached-ORM trap in the run list, and both export formats.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["CIM_DB_URL"] = f"sqlite:///{Path(tempfile.gettempdir()) / 'cim_test.db'}"


@pytest.fixture(scope="module")
def seeded():
    from unittest.mock import patch

    from app.llm.client import LLMClient
    from app.pipeline import orchestrator
    from app.storage import db
    from tests import fixtures as fx

    def fs(self, s, d, p, sc):
        self._record(s, "high", fx.FakeUsage(28000, 900, 0, 27000), 12000)
        return fx.brightpath_raw()

    def fc(self, s, d, p):
        self._record(s, "high", fx.FakeUsage(400, 1200, 27000, 0), 9000)
        return fx.brightpath_cited_text() if s == "extract_cited" else fx.brightpath_flags_text()

    def fk(self, s, d, p, max_tokens=16000):
        self._record(s, "high", fx.FakeUsage(3000, 800, 0, 0), 7000)
        return fx.draft_text()

    db.init_db()
    with (
        patch.object(
            LLMClient,
            "__init__",
            lambda s, model=None: (
                setattr(s, "model", "gemini-3.6-flash"),
                setattr(s, "telemetry", []),
                None,
            )[-1],
        ),
        patch.object(LLMClient, "extract_structured", fs),
        patch.object(LLMClient, "extract_cited", fc),
        patch.object(LLMClient, "complete", fk),
    ):
        run = orchestrator.run("data/sample_cims/brightpath_dental.pdf")
    db.save(run)
    return run


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_index_lists_runs_without_detached_orm_error(client, seeded):
    """The run list returns detached summaries; returning ORM rows raised
    DetachedInstanceError in the template."""
    r = client.get("/")
    assert r.status_code == 200
    assert "BrightPath Dental Partners" in r.text
    assert "Screen a CIM" in r.text


def test_memo_page_renders_citations_and_flag_split(client, seeded):
    r = client.get(f"/memo/{seeded.run_id}")
    assert r.status_code == 200
    assert "p.11" in r.text  # inline citation marker
    assert "Detected by rules" in r.text  # deterministic findings
    assert "Identified in review" in r.text  # qualitative findings
    assert "dealbreaker" in r.text


def test_review_queue_shows_reason_and_source(client, seeded):
    r = client.get(f"/review/{seeded.run_id}")
    assert r.status_code == 200
    assert "Passes disagree" in r.text
    assert "No source passage located" in r.text


def test_markdown_export(client, seeded):
    r = client.get(f"/export/{seeded.run_id}.md")
    assert r.status_code == 200
    assert "# Investment Committee Screening Memo" in r.text
    assert "## 7. Sources and Confidence" in r.text


def test_docx_export_is_a_real_document(client, seeded):
    r = client.get(f"/export/{seeded.run_id}.docx")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"  # zip magic — a valid .docx container
    assert len(r.content) > 10_000


def test_unknown_run_404s(client):
    assert client.get("/memo/deadbeef").status_code == 404
    assert client.get("/review/deadbeef").status_code == 404


def test_rejects_non_pdf_upload(client):
    r = client.post(
        "/run",
        files={"upload": ("notes.txt", b"hello", "text/plain")},
        data={"thesis": "services_rollup"},
    )
    assert r.status_code == 400


# --------------------------------------------------------------- input safety


@pytest.mark.parametrize(
    "sample",
    ["../../../etc/passwd", "../README.md", "/etc/passwd", "..%2F..%2Fetc%2Fpasswd", "nope.pdf"],
)
def test_sample_name_cannot_escape_the_corpus(client, sample):
    """`Path(base) / "/etc/passwd"` yields "/etc/passwd" — pathlib discards the
    base for an absolute right operand, so prefix filtering is not enough. The
    check is membership in the real listing."""
    r = client.post("/run", data={"sample": sample, "thesis": "services_rollup"})
    assert r.status_code == 400


@pytest.mark.parametrize("thesis", ["../../etc/passwd", "nonexistent", "../config"])
def test_unknown_thesis_rejected_up_front(client, thesis):
    """A bad thesis name is a 400 at submit time, not a background failure the
    user discovers after watching a progress spinner."""
    r = client.post("/run", data={"sample": "meridian_hvac.pdf", "thesis": thesis})
    assert r.status_code == 400


def test_valid_sample_is_accepted(client):
    r = client.post("/run", data={"sample": "meridian_hvac.pdf", "thesis": "services_rollup"})
    assert r.status_code == 200


def test_upload_over_the_cap_is_rejected(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "max_upload_mb", 1)
    big = b"%PDF-1.4\n" + b"x" * (2 * 1024 * 1024)
    r = client.post(
        "/run",
        files={"upload": ("big.pdf", big, "application/pdf")},
        data={"thesis": "services_rollup"},
    )
    assert r.status_code == 400
    assert "exceeds" in r.text


def test_job_store_is_bounded():
    """Finished jobs are evicted; running jobs are never dropped, since losing
    one would orphan a live progress poll."""
    from app.main import _JOBS, _MAX_JOBS, Job, _prune_jobs

    _JOBS.clear()
    for i in range(_MAX_JOBS + 50):
        _JOBS[f"j{i}"] = Job(job_id=f"j{i}", filename="x.pdf", done=True)
    _JOBS["live"] = Job(job_id="live", filename="running.pdf", done=False)
    _prune_jobs()
    assert len(_JOBS) <= _MAX_JOBS + 1
    assert "live" in _JOBS
    _JOBS.clear()
