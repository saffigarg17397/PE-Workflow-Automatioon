"""Fail-fast on account-level errors.

An exhausted credit balance cannot resolve by trying the next document. The
batch runners must stop on the first one rather than repeating an identical
failure per document — five copies of the same message buries the one line that
tells you what to do.
"""

from __future__ import annotations

import anthropic
import httpx
import pytest

from app.llm.client import LLMError, _status_error


def _status_error_from(message: str, status: int = 400) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    return anthropic.APIStatusError(
        message, response=response, body={"error": {"message": message}}
    )


@pytest.mark.parametrize(
    "message",
    [
        "Your credit balance is too low to access the Anthropic API.",
        "Please go to Plans & Billing to upgrade or purchase credits.",
        "You have exceeded your quota.",
    ],
)
def test_account_level_errors_are_terminal(message):
    err = _status_error("extract_structured", _status_error_from(message))
    assert err.terminal
    assert "account-level problem" in str(err)


@pytest.mark.parametrize(
    "message",
    [
        "messages.0.content.0: invalid document",
        "max_tokens must be less than 128000",
        "Overloaded",
    ],
)
def test_request_level_errors_are_not_terminal(message):
    """Misclassifying a recoverable failure as terminal would abandon a run that
    would have succeeded on the next document."""
    err = _status_error("extract_structured", _status_error_from(message))
    assert not err.terminal


def test_llm_error_defaults_to_non_terminal():
    """The safe default: forfeit the fast-abort rather than stop a run that
    could have continued."""
    assert not LLMError("something went wrong").terminal


def test_run_demo_stops_the_batch_on_a_terminal_error(monkeypatch, capsys, tmp_path):
    """The reported behaviour: a zero balance produced five identical failures."""
    from unittest.mock import patch

    import scripts.run_demo as rd

    calls: list[str] = []

    def boom(path, thesis=None, on_progress=None):
        calls.append(str(path))
        raise LLMError("Your credit balance is too low", terminal=True)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
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
        raise LLMError("API error 500 in stage 'draft': Overloaded")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setattr(rd, "OUT", tmp_path)
    with (
        patch.object(rd.orchestrator, "run", boom),
        patch.object(rd.sys, "argv", ["run_demo", "--all"]),
    ):
        rd.main()

    assert len(calls) == 5, "all five documents should be attempted"
