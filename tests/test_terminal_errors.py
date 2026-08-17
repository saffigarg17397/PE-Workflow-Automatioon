"""Fail-fast on account-level errors.

An exhausted daily quota cannot resolve by trying the next document. The batch
runners must stop on the first one rather than repeating an identical failure
per document — five copies of the same message buries the one line that tells
you what to do.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from google.genai import errors as genai_errors

from app.llm.client import LLMError, _api_error


def _error(message: str, status: int = 400) -> genai_errors.APIError:
    """Build an APIError the way the SDK does, from a response body."""
    return genai_errors.APIError(
        status, {"error": {"code": status, "message": message, "status": "INVALID_ARGUMENT"}}
    )


@pytest.mark.parametrize(
    "message,status",
    [
        ("API key not valid. Please pass a valid API key.", 400),
        ("Quota exceeded for quota metric 'Generate Content API requests'", 429),
        ("RESOURCE_EXHAUSTED: you have run out of daily requests", 429),
        ("Permission denied on resource project.", 403),
    ],
)
def test_account_level_errors_are_terminal(message, status):
    assert _api_error("extract_structured", _error(message, status)).terminal


def test_invalid_key_says_where_to_get_one():
    """The most common first-run failure deserves the fix in the message rather
    than a status code the reader has to go and look up."""
    err = _api_error("extract_structured", _error("API key not valid. Pass a valid API key."))
    assert "aistudio.google.com" in str(err)
    assert "GEMINI_API_KEY" in str(err)


def test_rate_limit_message_distinguishes_per_minute_from_per_day():
    """Both arrive as the same error, and the response to each is different:
    one is a sixty-second wait, the other is tomorrow."""
    err = _api_error("draft", _error("Quota exceeded for requests per minute", 429))
    assert "wait sixty seconds" in str(err)
    assert "daily limit resets" in str(err)


@pytest.mark.parametrize(
    "message,status",
    [
        ("Request contains an invalid argument.", 400),
        ("The model is overloaded. Please try again later.", 503),
        ("Internal error encountered.", 500),
    ],
)
def test_request_level_errors_are_not_terminal(message, status):
    """Misclassifying a recoverable failure as terminal would abandon a run that
    would have succeeded on the next document."""
    assert not _api_error("extract_structured", _error(message, status)).terminal


def test_llm_error_defaults_to_non_terminal():
    """The safe default: forfeit the fast-abort rather than stop a run that
    could have continued."""
    assert not LLMError("something went wrong").terminal


def test_run_demo_stops_the_batch_on_a_terminal_error(monkeypatch, capsys, tmp_path):
    """The reported behaviour: an exhausted account produced five identical failures."""
    from unittest.mock import patch

    import scripts.run_demo as rd

    calls: list[str] = []

    def boom(path, thesis=None, on_progress=None):
        calls.append(str(path))
        raise LLMError("Quota exceeded", terminal=True)

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaFAKE")
    monkeypatch.setattr(rd, "OUT", tmp_path)
    with (
        patch.object(rd.orchestrator, "run", boom),
        patch.object(rd.sys, "argv", ["run_demo", "--all"]),
    ):
        rc = rd.main()

    assert rc == 1
    assert len(calls) == 1, f"should stop after the first failure, tried {len(calls)}"
    assert "Stopped" in capsys.readouterr().out


def test_run_demo_continues_past_a_per_document_error(monkeypatch, capsys, tmp_path):
    """A document-specific failure must not abandon the rest of the corpus."""
    from unittest.mock import patch

    import scripts.run_demo as rd

    calls: list[str] = []

    def boom(path, thesis=None, on_progress=None):
        calls.append(str(path))
        raise LLMError("API error 503 in stage 'draft': overloaded")

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaFAKE")
    monkeypatch.setattr(rd, "OUT", tmp_path)
    with (
        patch.object(rd.orchestrator, "run", boom),
        patch.object(rd.sys, "argv", ["run_demo", "--all"]),
    ):
        rd.main()

    assert len(calls) == 5, "all five documents should be attempted"


def test_model_not_found_is_terminal_and_names_the_setting():
    """The reported failure: a stale CIM_MODEL from a previous provider 404'd
    once per document, five times, and the API's own message pointed at
    ListModels rather than at the .env line that needed changing."""
    err = _api_error(
        "extract_structured",
        _error("models/some-other-vendor-model is not found for API version v1beta", 404),
    )
    assert err.terminal
    assert "CIM_MODEL" in str(err)


def test_non_gemini_model_is_caught_before_any_request(monkeypatch):
    from app.config import Settings, settings
    from scripts.run_demo import check_model

    monkeypatch.setattr(settings, "model", "claude-opus-5")
    msg = check_model()
    assert msg and "not a Gemini model" in msg
    assert "CIM_MODEL" in msg

    monkeypatch.setattr(settings, "model", Settings.model_fields["model"].default)
    assert check_model() is None


def test_model_closed_to_new_keys_is_caught_before_any_request(monkeypatch):
    """Google still prices and documents 2.5 Flash, and it still serves keys
    that used it before the cutoff — so it fails only for new keys, and only at
    request time. Naming it up front beats a 404 that says 'call ListModels'.
    """
    from app.config import settings
    from scripts.run_demo import check_model

    monkeypatch.setattr(settings, "model", "gemini-2.5-flash")
    msg = check_model()
    assert msg and "closed to new API keys" in msg
    assert "CIM_MODEL=gemini-3.6-flash" in msg


def test_the_default_model_is_not_one_we_know_is_closed():
    """A default that cannot serve a new key makes first-run failure the norm."""
    from app.config import RETIRED_FOR_NEW_KEYS, Settings

    assert Settings.model_fields["model"].default not in RETIRED_FOR_NEW_KEYS


def test_resume_skips_documents_that_already_have_seeds(monkeypatch, capsys, tmp_path):
    """A transient 503 three minutes into a document killed the batch mid-corpus.
    Re-running everything costs minutes and quota to rebuild artifacts that are
    already on disk."""
    from unittest.mock import patch

    import scripts.run_demo as rd
    from app.storage import seed as seed_store

    seeded = tmp_path / "seeds"
    seeded.mkdir()
    for name in ("atlas_bookkeeping", "brightpath_dental", "meridian_hvac"):
        (seeded / f"{name}.json").write_text("{}")

    calls: list[str] = []

    def record(path, thesis=None, on_progress=None):
        calls.append(Path(path).stem)
        raise LLMError("stop here")

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaFAKE")
    monkeypatch.setattr(seed_store, "SEED_DIR", seeded)
    monkeypatch.setattr(rd, "OUT", tmp_path / "memos")
    with (
        patch.object(rd.orchestrator, "run", record),
        patch.object(rd.sys, "argv", ["run_demo", "--all", "--seed", "--resume"]),
    ):
        rd.main()

    assert set(calls) == {"northgate_msp", "verdant_landscaping"}
    assert "skipping 3 already seeded" in capsys.readouterr().out
