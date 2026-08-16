"""Eval-harness scoring logic. Verifies the scorer itself is correct before it
gets used to judge the pipeline."""

from __future__ import annotations

import yaml

from evals.run_evals import GT_DIR, DocScore, FieldScore, _compare, score_document


def test_numeric_tolerance_accepts_rounding():
    assert _compare(9.3, 9.31) == "correct"  # within 2%
    assert _compare(9.3, 9.9) == "wrong"  # outside
    assert _compare(9.3, None) == "missed"


def test_string_compare_is_case_insensitive():
    assert _compare("Tampa, Florida", "tampa, florida") == "correct"
    assert _compare("Tampa, Florida", "Miami, Florida") == "wrong"


def test_missed_excluded_from_accuracy_but_shown_in_coverage():
    """A tool that declines to answer must not be scored the same as one that
    invents a number — that distinction is the whole point for diligence."""
    s = DocScore(
        slug="x",
        fields=[
            FieldScore("a", 1, 1, "correct"),
            FieldScore("b", 2, 9, "wrong"),
            FieldScore("c", 3, None, "missed"),
        ],
    )
    assert s.accuracy == 50.0  # 1 of 2 attempted
    assert round(s.coverage, 1) == 66.7  # 2 of 3 fields attempted
    assert s.missed == 1


def test_flag_recall_and_precision():
    s = DocScore(
        slug="x",
        expected_flags={"customer_concentration", "growth"},
        detected_flags={"customer_concentration", "margin"},
    )
    assert s.flag_recall == 50.0
    assert s.flag_precision == 50.0


def test_ground_truth_files_are_wellformed():
    """Ground truth is generated, but a malformed label file would silently
    corrupt every score computed from it."""
    files = sorted(GT_DIR.glob("*.yaml"))
    assert len(files) == 5
    for f in files:
        gt = yaml.safe_load(f.read_text())
        assert gt["slug"] == f.stem
        assert gt["fields"]["latest_revenue"] > 0
        assert isinstance(gt["expected_flags"], list)
        assert len(gt["financials"]) == 3


def test_scorer_runs_against_a_mocked_run():
    from unittest.mock import patch

    from app.export import markdown  # noqa: F401  (import smoke)
    from app.llm.client import LLMClient
    from app.pipeline import orchestrator
    from tests import fixtures as fx

    def fake_structured(self, stage, doc, prompt, schema):
        self._record(stage, "high", fx.FakeUsage(), 100)
        return fx.brightpath_raw()

    def fake_cited(self, stage, doc, prompt):
        self._record(stage, "high", fx.FakeUsage(), 100)
        return (
            fx.brightpath_cited_response()
            if stage == "extract_cited"
            else fx.brightpath_flags_response()
        )

    def fake_complete(self, stage, doc, prompt, max_tokens=16000):
        self._record(stage, "high", fx.FakeUsage(), 100)
        return fx.draft_response().content[0].text

    with (
        patch.object(
            LLMClient,
            "__init__",
            lambda s, model=None: (
                setattr(s, "model", "claude-opus-5"),
                setattr(s, "telemetry", []),
                None,
            )[-1],
        ),
        patch.object(LLMClient, "extract_structured", fake_structured),
        patch.object(LLMClient, "extract_cited", fake_cited),
        patch.object(LLMClient, "complete", fake_complete),
    ):
        run = orchestrator.run("data/sample_cims/brightpath_dental.pdf")

    gt = yaml.safe_load((GT_DIR / "brightpath_dental.yaml").read_text())
    score = score_document(gt, run)
    assert score.attempted > 10
    assert score.accuracy > 80  # fixture mirrors ground truth
    assert "customer_concentration" in score.detected_flags
