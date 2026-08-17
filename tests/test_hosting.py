"""Hosted-demo safety.

The property under test is financial, not functional: a public instance holds
the API key server-side, so an ungated run endpoint lets any visitor spend the
owner's money. Demo mode must make that impossible, and the seed path must
survive restarts without duplicating rows.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("CIM_DB_URL", f"sqlite:///{Path(tempfile.gettempdir()) / 'cim_host.db'}")


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def demo_mode(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "run_password", "")


def test_demo_mode_blocks_every_run_path(client, demo_mode):
    """Sample and upload alike — both would spend money."""
    r = client.post("/run", data={"sample": "meridian_hvac.pdf", "thesis": "services_rollup"})
    assert r.status_code == 403

    r = client.post(
        "/run",
        files={"upload": ("x.pdf", b"%PDF-1.4\n", "application/pdf")},
        data={"thesis": "services_rollup"},
    )
    assert r.status_code == 403


def test_demo_mode_still_serves_the_memos(client, demo_mode):
    """Read-only must not mean empty — the landing page is the whole demo."""
    r = client.get("/")
    assert r.status_code == 200
    assert "Read-only demo" in r.text
    assert "Live runs are disabled" in r.text


def test_password_reopens_live_runs(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "run_password", "s3cret")

    r = client.post("/run", data={"sample": "meridian_hvac.pdf", "password": "wrong"})
    assert r.status_code == 403

    r = client.post("/run", data={"sample": "meridian_hvac.pdf", "password": "s3cret"})
    assert r.status_code == 200


def test_empty_password_does_not_pass_a_gated_instance(client, monkeypatch):
    """A blank submission must not satisfy a configured password."""
    from app.config import settings

    monkeypatch.setattr(settings, "run_password", "s3cret")
    assert client.post("/run", data={"sample": "meridian_hvac.pdf"}).status_code == 403


def test_run_allowed_when_neither_flag_is_set(client):
    """Local development is unaffected."""
    r = client.post("/run", data={"sample": "meridian_hvac.pdf", "thesis": "services_rollup"})
    assert r.status_code == 200


# ------------------------------------------------------------------- seeding


def _sample_run():
    from unittest.mock import patch

    from app.llm.client import LLMClient
    from app.pipeline import orchestrator
    from tests import fixtures as fx

    def fs(self, s, d, p, sc):
        self._record(s, "high", fx.FakeUsage(), 100)
        return fx.brightpath_raw()

    def fc(self, s, d, p):
        self._record(s, "high", fx.FakeUsage(), 100)
        return fx.brightpath_cited_text() if s == "extract_cited" else fx.brightpath_flags_text()

    def fk(self, s, d, p, max_tokens=16000):
        self._record(s, "high", fx.FakeUsage(), 100)
        return fx.draft_text()

    with (
        patch.object(
            LLMClient,
            "__init__",
            lambda s, model=None: (
                setattr(s, "model", "gemini-2.5-flash"),
                setattr(s, "telemetry", []),
                None,
            )[-1],
        ),
        patch.object(LLMClient, "extract_structured", fs),
        patch.object(LLMClient, "extract_cited", fc),
        patch.object(LLMClient, "complete", fk),
    ):
        return orchestrator.run("data/sample_cims/brightpath_dental.pdf")


def test_seed_roundtrip_is_lossless():
    """A seeded memo must be identical to a live one — nothing simulated for
    display, or the demo would misrepresent the tool."""
    from app.models.memo import MemoRun
    from app.storage import seed

    run = _sample_run()
    out = Path(tempfile.mkdtemp())
    path = seed.export_run(run, out)
    restored = MemoRun(**json.loads(path.read_text()))

    assert restored.run_id == run.run_id
    assert restored.memo.recommendation == run.memo.recommendation
    assert len(restored.memo.red_flags) == len(run.memo.red_flags)
    assert restored.profile.top_customer_pct.citations[0].page == (
        run.profile.top_customer_pct.citations[0].page
    )
    assert restored.total_cost_usd == run.total_cost_usd
    assert restored.memo.health.citation_rate == run.memo.health.citation_rate


def test_seed_export_is_named_by_source_not_run_id():
    """Named by document so re-running replaces the seed instead of
    accumulating near-duplicates in the committed corpus."""
    from app.storage import seed

    out = Path(tempfile.mkdtemp())
    p1 = seed.export_run(_sample_run(), out)
    p2 = seed.export_run(_sample_run(), out)
    assert p1 == p2
    assert p1.name == "brightpath_dental.json"
    assert len(list(out.glob("*.json"))) == 1


def test_seed_load_is_idempotent(monkeypatch):
    """A redeploy must not duplicate rows."""
    from app.storage import db, seed

    out = Path(tempfile.mkdtemp())
    run = _sample_run()
    seed.export_run(run, out)
    monkeypatch.setattr(seed, "SEED_DIR", out)

    db.init_db()
    first = seed.load_all()
    second = seed.load_all()
    assert first == 1
    assert second == 0, "second load should skip the already-present run"
    db.delete(run.run_id)


def test_malformed_seed_does_not_break_startup(monkeypatch, capsys):
    """A bad fixture should skip with a warning, not take the site down."""
    from app.storage import seed

    out = Path(tempfile.mkdtemp())
    (out / "broken.json").write_text("{not valid json")
    monkeypatch.setattr(seed, "SEED_DIR", out)

    assert seed.load_all() == 0
    assert "skipping broken.json" in capsys.readouterr().out
