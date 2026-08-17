"""Anthropic SDK wrapper.

Everything that touches the API goes through here so that caching, telemetry and
error handling are implemented once rather than per-stage.

Two things worth knowing about the API shape, both of which drove the design:

  * `citations` and `output_config.format` cannot be used on the same request.
    So structured extraction and cited extraction are separate calls, joined
    downstream. `extract_structured` and `extract_cited` below are that split.
  * The CIM document block is marked `cache_control: ephemeral`. The same PDF is
    read by four stages, so this is where most of the cost saving lives —
    `cache_read_tokens` in the telemetry is the proof it's working.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any, TypeVar, cast

import anthropic
from pydantic import BaseModel

from app.config import PRICING, STAGE_EFFORT, price_call, settings
from app.models.memo import StageTelemetry

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when a call fails in a way the pipeline should surface, not retry.

    `terminal` marks a condition that cannot resolve by trying another
    document — an exhausted credit balance, a bad key, a revoked permission.
    Batch callers abort on these instead of failing identically N more times,
    which turns one clear message into a wall of noise and delays the fix.
    """

    def __init__(self, message: str, *, terminal: bool = False) -> None:
        super().__init__(message)
        self.terminal = terminal


class RefusalError(LLMError):
    """The model declined. Rare on this content, but the pipeline degrades to a
    flagged field rather than crashing on an index error."""


# Account-level conditions surface as a 400 rather than a dedicated exception
# type, so they have to be recognised from the message. Matching on substrings
# is brittle by nature — hence the fallback to non-terminal, which merely
# forfeits the fast-abort rather than misclassifying a recoverable failure.
_TERMINAL_MESSAGES = (
    "credit balance is too low",
    "billing",
    "quota",
)


def _status_error(stage: str, e: anthropic.APIStatusError) -> LLMError:
    msg = (e.message or "").lower()
    terminal = any(m in msg for m in _TERMINAL_MESSAGES)
    if terminal:
        return LLMError(
            f"{e.message}\n\n"
            "  This is an account-level problem, not a problem with this document —\n"
            "  every remaining document would fail identically, so stopping here.",
            terminal=True,
        )
    return LLMError(f"API error {e.status_code} in stage '{stage}': {e.message}")


class CachedDocument:
    """A PDF prepared once and reused across every stage.

    Holding the base64 payload on one object (rather than re-encoding per call)
    is what keeps the cache prefix byte-identical between stages. Re-encoding
    would be harmless in principle, but any difference in the document block
    invalidates the prefix, so it's worth being deliberate.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.name = path.name
        self._b64 = base64.standard_b64encode(path.read_bytes()).decode("utf-8")

    def block(self, *, citations: bool, cache: bool = True) -> dict[str, Any]:
        blk: dict[str, Any] = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": self._b64,
            },
            "title": self.name,
        }
        if citations:
            blk["citations"] = {"enabled": True}
        if cache:
            blk["cache_control"] = {"type": "ephemeral"}
        return blk


class LLMClient:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.model
        self._client = anthropic.Anthropic()
        self.telemetry: list[StageTelemetry] = []

    # ------------------------------------------------------------ internals

    def _record(self, stage: str, effort: str, usage: Any, latency_ms: int) -> StageTelemetry:
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        t = StageTelemetry(
            stage=stage,
            model=self.model,
            effort=effort,
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=cr,
            cache_write_tokens=cw,
            latency_ms=latency_ms,
            cost_usd=price_call(self.model, inp, out, cr, cw),
        )
        self.telemetry.append(t)
        return t

    def _effort(self, stage: str) -> str:
        return STAGE_EFFORT.get(stage, "high")

    def _call(self, stage: str, **kwargs: Any) -> Any:
        """Single choke point for error handling.

        The SDK already retries 429/5xx with backoff — deliberately not adding a
        second retry layer on top of it.
        """
        started = time.monotonic()
        try:
            resp = self._client.messages.create(**kwargs)
        except anthropic.NotFoundError as e:
            raise LLMError(
                f"Model '{self.model}' not found — check the model id", terminal=True
            ) from e
        except anthropic.AuthenticationError as e:
            raise LLMError("ANTHROPIC_API_KEY missing or invalid", terminal=True) from e
        except anthropic.PermissionDeniedError as e:
            raise LLMError(f"API key lacks permission: {e.message}", terminal=True) from e
        except anthropic.RateLimitError as e:
            raise LLMError(f"Rate limited after SDK retries: {e}") from e
        except anthropic.APIStatusError as e:
            raise _status_error(stage, e) from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Could not reach the API in stage '{stage}': {e}") from e

        latency = int((time.monotonic() - started) * 1000)
        self._record(
            stage, kwargs.get("output_config", {}).get("effort", "high"), resp.usage, latency
        )

        # Check stop_reason before touching content — a refusal returns HTTP 200
        # with empty content, and indexing content[0] would raise IndexError.
        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            raise RefusalError(f"Model declined in stage '{stage}' (category: {cat})")
        return resp

    # ------------------------------------------------------------- public API

    def extract_structured(
        self, stage: str, doc: CachedDocument, prompt: str, schema: type[T]
    ) -> T:
        """Typed extraction via structured outputs.

        Uses `messages.parse()` so the SDK validates against the Pydantic model —
        we never hand-parse JSON out of a text block.
        """
        effort = self._effort(stage)
        started = time.monotonic()
        try:
            resp = self._client.messages.parse(
                model=self.model,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                # The SDK models these as TypedDicts; the dicts we build are
                # structurally correct but not statically inferable as such.
                output_config=cast(Any, {"effort": effort}),
                messages=cast(
                    Any,
                    [
                        {
                            "role": "user",
                            "content": [
                                doc.block(citations=False),
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                ),
                output_format=schema,
            )
        except anthropic.APIStatusError as e:
            raise _status_error(stage, e) from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Could not reach the API in stage '{stage}': {e}") from e

        self._record(stage, effort, resp.usage, int((time.monotonic() - started) * 1000))
        if resp.stop_reason == "refusal":
            raise RefusalError(f"Model declined in stage '{stage}'")
        parsed = resp.parsed_output
        if parsed is None:
            raise LLMError(f"Stage '{stage}' returned no parseable output")
        return cast(T, parsed)

    def extract_cited(self, stage: str, doc: CachedDocument, prompt: str) -> Any:
        """Free-text pass with citations enabled.

        Returns the raw response; the caller walks `content` for `citations` on
        each text block. Cannot be combined with structured outputs (400) —
        hence the separate pass.
        """
        effort = self._effort(stage)
        return self._call(
            stage,
            model=self.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            messages=[
                {
                    "role": "user",
                    "content": [doc.block(citations=True), {"type": "text", "text": prompt}],
                }
            ],
        )

    def complete(
        self, stage: str, doc: CachedDocument | None, prompt: str, max_tokens: int = 16000
    ) -> str:
        """Plain text completion. Streams when max_tokens is large enough to risk
        an HTTP timeout on a non-streaming request."""
        effort = self._effort(stage)
        content: list[dict[str, Any]] = []
        if doc is not None:
            content.append(doc.block(citations=False))
        content.append({"type": "text", "text": prompt})

        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            messages=[{"role": "user", "content": content}],
        )

        if max_tokens > 16000:
            started = time.monotonic()
            with self._client.messages.stream(**kwargs) as stream:
                resp = stream.get_final_message()
            self._record(stage, effort, resp.usage, int((time.monotonic() - started) * 1000))
            if resp.stop_reason == "refusal":
                raise RefusalError(f"Model declined in stage '{stage}'")
        else:
            resp = self._call(stage, **kwargs)

        return "".join(b.text for b in resp.content if b.type == "text")

    def count_tokens(self, doc: CachedDocument, prompt: str) -> int:
        """Pre-flight estimate. Never use tiktoken here — wrong tokenizer, and
        materially wrong on this content."""
        r = self._client.messages.count_tokens(
            model=self.model,
            messages=cast(
                Any,
                [
                    {
                        "role": "user",
                        "content": [
                            doc.block(citations=False, cache=False),
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            ),
        )
        return int(r.input_tokens)

    # ---------------------------------------------------------------- summary

    def cost_summary(self) -> dict[str, Any]:
        return {
            "total_usd": round(sum(t.cost_usd for t in self.telemetry), 4),
            "total_ms": sum(t.latency_ms for t in self.telemetry),
            "cache_read_tokens": sum(t.cache_read_tokens for t in self.telemetry),
            "calls": len(self.telemetry),
            "by_stage": [t.model_dump() for t in self.telemetry],
        }


def model_is_priced(model: str) -> bool:
    return model in PRICING
