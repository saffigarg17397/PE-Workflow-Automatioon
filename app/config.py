"""Settings and per-stage model configuration.

Effort — not model choice — is the cost lever here. Every stage runs on the same
model; what varies is how hard it thinks. Extraction and drafting are the two
stages where a mistake is expensive, so they run high.
"""

from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env into the process environment before anything reads it.
#
# pydantic-settings' own `env_file` only populates this Settings model's
# CIM_-prefixed fields — it does not export anything to os.environ. But
# ANTHROPIC_API_KEY is read from os.environ by the Anthropic SDK, not by
# Settings, so without this a key sitting in .env is silently ignored and the
# pipeline reports "ANTHROPIC_API_KEY is not set" while the file plainly
# contains it. `override=False` keeps an explicitly exported key winning over
# the file, which is what someone switching keys expects.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_FILE, override=False)
# Also try the usual cwd-upward search, so a key still resolves when the app is
# invoked from outside the repo (e.g. an installed console script).
load_dotenv(override=False)

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = Path(__file__).resolve().parent / "llm" / "prompts"
THESIS_DIR = ROOT / "config" / "thesis"
SAMPLE_CIMS = ROOT / "data" / "sample_cims"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CIM_", env_file=".env", extra="ignore")

    model: str = "claude-opus-5"
    db_url: str = "sqlite:///./cim_memo.db"
    default_thesis: str = "services_rollup"
    max_upload_mb: int = 32

    # --- public hosting ---
    # A hosted instance holds the API key server-side, so every visitor who
    # starts a run spends the owner's money. Demo mode serves pre-generated
    # memos read-only: full UI, real output, zero marginal cost, no abuse
    # vector. Set a run password to re-enable live runs for a live audience.
    demo_mode: bool = False
    run_password: str = ""
    demo_banner: str = ""


settings = Settings()

# Per-stage effort. Tuning these is a legitimate finding to report — the README
# records a sweep. `low` on classification is where most of the saving lives.
STAGE_EFFORT: dict[str, str] = {
    "ingest": "low",
    "extract_structured": "high",
    "extract_cited": "high",
    "llm_flags": "high",
    "draft": "high",
}

# USD per million tokens. Cache reads bill at ~0.1x input, writes at ~1.25x.
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}


def price_call(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_write: int = 0,
) -> float:
    p = PRICING.get(model, PRICING["claude-opus-5"])
    return round(
        (
            input_tokens * p["input"]
            + output_tokens * p["output"]
            + cache_read * p["input"] * 0.1
            + cache_write * p["input"] * 1.25
        )
        / 1_000_000,
        6,
    )


class ThesisCriterion(BaseModel):
    name: str
    field: str
    op: str  # gte | lte | between | in | exists
    value: float | list[float] | list[str] | None = None
    weight: float = 1.0
    dealbreaker: bool = False
    target_label: str = ""


class Thesis(BaseModel):
    name: str
    description: str
    criteria: list[ThesisCriterion]


def load_thesis(name: str | None = None) -> Thesis:
    name = name or settings.default_thesis
    path = THESIS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No thesis config at {path}")
    return Thesis(**yaml.safe_load(path.read_text()))


def available_theses() -> list[str]:
    return sorted(p.stem for p in THESIS_DIR.glob("*.yaml"))


def load_prompt(name: str) -> str:
    """Prompts live as files, not inline strings — their diffs are the most
    important thing in this repo's history."""
    return (PROMPTS_DIR / f"{name}.md").read_text()
