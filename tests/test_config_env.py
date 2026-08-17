"""`.env` loading.

The defect this guards: `.env.example` documents ANTHROPIC_API_KEY, but
pydantic-settings' `env_file` only populates this Settings model's CIM_-prefixed
fields — it exports nothing to os.environ, which is where the Anthropic SDK
reads the key. So a correctly-filled .env produced "ANTHROPIC_API_KEY is not
set", with the file plainly containing it. Documented behaviour that silently
does nothing is worse than undocumented behaviour.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PROBE = textwrap.dedent(
    """
    import os, sys
    sys.path.insert(0, %r)
    from app.config import settings
    print("KEY=" + str(os.environ.get("ANTHROPIC_API_KEY")))
    print("THESIS=" + settings.default_thesis)
    """
    % str(REPO)
)


def _probe(env: dict[str, str], cwd: Path) -> dict[str, str]:
    """Run in a subprocess: .env loads at import time, so an already-imported
    app.config would mask the behaviour under test."""
    out = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, env=env, cwd=cwd
    )
    assert out.returncode == 0, out.stderr
    return dict(line.split("=", 1) for line in out.stdout.strip().splitlines())


def _clean_env() -> dict[str, str]:
    import os

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["CIM_DEFAULT_THESIS"] = ""
    env.pop("CIM_DEFAULT_THESIS")
    return env


def test_api_key_in_dotenv_reaches_os_environ(tmp_path, monkeypatch):
    """The whole point: a key in .env must be visible to the SDK."""
    env_file = REPO / ".env"
    existed = env_file.exists()
    backup = env_file.read_text() if existed else None
    try:
        env_file.write_text("ANTHROPIC_API_KEY=sk-ant-TEST-FROM-DOTENV\n")
        result = _probe(_clean_env(), REPO)
        assert result["KEY"] == "sk-ant-TEST-FROM-DOTENV"
    finally:
        if backup is not None:
            env_file.write_text(backup)
        else:
            env_file.unlink(missing_ok=True)


def test_exported_key_wins_over_dotenv():
    """Someone switching keys expects the exported one to take effect without
    editing the file."""
    env_file = REPO / ".env"
    existed = env_file.exists()
    backup = env_file.read_text() if existed else None
    try:
        env_file.write_text("ANTHROPIC_API_KEY=sk-ant-FROM-FILE\n")
        env = _clean_env()
        env["ANTHROPIC_API_KEY"] = "sk-ant-EXPORTED"
        assert _probe(env, REPO)["KEY"] == "sk-ant-EXPORTED"
    finally:
        if backup is not None:
            env_file.write_text(backup)
        else:
            env_file.unlink(missing_ok=True)


def test_missing_dotenv_is_not_an_error():
    """No .env is the normal state in CI and in the container image."""
    env_file = REPO / ".env"
    existed = env_file.exists()
    backup = env_file.read_text() if existed else None
    try:
        env_file.unlink(missing_ok=True)
        assert _probe(_clean_env(), REPO)["KEY"] == "None"
    finally:
        if backup is not None:
            env_file.write_text(backup)


def test_env_example_documents_the_key():
    """If the example stops mentioning it, the loading above is dead code."""
    assert "ANTHROPIC_API_KEY" in (REPO / ".env.example").read_text()


# ------------------------------------------------------- key validation


def _check(monkeypatch, key: str | None):
    from scripts.run_demo import check_api_key

    if key is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    return check_api_key()


def test_valid_key_passes(monkeypatch):
    assert _check(monkeypatch, "sk-ant-api03-realkey") is None


def test_unedited_placeholder_is_caught(monkeypatch):
    """Copying .env.example and forgetting to edit it leaves a syntactically
    fine key that loads cleanly, then 401s partway through a paid run."""
    msg = _check(monkeypatch, "sk-ant-your-key-here")
    assert msg and "placeholder" in msg


def test_key_with_wrong_prefix_is_caught(monkeypatch):
    msg = _check(monkeypatch, "oops-wrong-thing")
    assert msg and "does not look like an Anthropic key" in msg


def test_missing_key_is_caught(monkeypatch):
    msg = _check(monkeypatch, None)
    assert msg and "not set" in msg


def test_whitespace_only_key_is_treated_as_missing(monkeypatch):
    msg = _check(monkeypatch, "   ")
    assert msg and "not set" in msg
