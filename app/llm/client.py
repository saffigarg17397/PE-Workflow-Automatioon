"""Google Gemini SDK wrapper.

Everything that touches the API goes through here so that error handling,
telemetry and the effort→thinking-budget translation are implemented once rather
than per-stage.

Two things worth knowing about the design, both of which survived the move off
Anthropic:

  * **Provenance is computed locally, not supplied by the API.** No provider
    returns verified page citations for an uploaded PDF. So the cited pass
    receives page-numbered text and must return a page number and a verbatim
    quote per field, and `pipeline/extract.py` then checks that quote against
    the real page text before it is allowed to become a `Citation`. A citation
    in this system is one that was verified against the document, not one the
    model asserted.
  * **The two passes stay separate.** The structured pass gets the PDF itself,
    because table layout is where most of a CIM's financial content lives and
    flattening it to text loses column alignment. The cited pass gets text,
    because it has to name page numbers. Same document, two views, joined
    downstream.

Caching is implicit on the 2.5 models — repeated prefixes are discounted
automatically, and `cache_read_tokens` in the telemetry reports what was hit.
There is no explicit cache resource to manage.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, TypeVar, cast

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel
from pypdf import PdfReader

from app.config import PRICING, STAGE_EFFORT, THINKING_BUDGET, price_call, settings
from app.models.memo import StageTelemetry

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when a call fails in a way the pipeline should surface, not retry.

    `terminal` marks a condition that cannot resolve by trying another
    document — an exhausted daily quota, a bad key, a revoked permission.
    Batch callers abort on these instead of failing identically N more times,
    which turns one clear message into a wall of noise and delays the fix.
    """

    def __init__(self, message: str, *, terminal: bool = False) -> None:
        super().__init__(message)
        self.terminal = terminal


class RefusalError(LLMError):
    """The model declined. Rare on this content, but the pipeline degrades to a
    flagged field rather than crashing on an index error."""


# Conditions that make every subsequent document fail identically. Free-tier
# daily quota is the one that will actually bite here: it exhausts mid-corpus
# and reads like a transient rate limit, so it is called out by name.
_TERMINAL_CODES = {400, 401, 403}
_TERMINAL_MESSAGES = (
    "api key not valid",
    "api_key_invalid",
    "permission denied",
    "quota",
    "resource_exhausted",
    "billing",
)


def _api_error(stage: str, e: genai_errors.APIError) -> LLMError:
    msg = (getattr(e, "message", "") or str(e)).lower()
    code = getattr(e, "code", None)

    if "api key not valid" in msg or "api_key_invalid" in msg:
        return LLMError(
            "GEMINI_API_KEY is not valid.\n\n"
            "  Get one at aistudio.google.com -> Get API key, and put it in .env as\n"
            "    GEMINI_API_KEY=AIza...",
            terminal=True,
        )

    if "quota" in msg or "resource_exhausted" in msg or code == 429:
        return LLMError(
            f"{getattr(e, 'message', e)}\n\n"
            "  The free tier allows 1,500 requests/day and 15/minute on Flash.\n"
            "  If this is the per-minute limit, wait sixty seconds and re-run;\n"
            "  the daily limit resets at midnight Pacific.",
            terminal=True,
        )

    terminal = code in _TERMINAL_CODES and any(m in msg for m in _TERMINAL_MESSAGES)
    if terminal:
        return LLMError(
            f"{getattr(e, 'message', e)}\n\n"
            "  This is an account-level problem, not a problem with this document —\n"
            "  every remaining document would fail identically, so stopping here.",
            terminal=True,
        )
    return LLMError(f"API error {code} in stage '{stage}': {getattr(e, 'message', e)}")


class CachedDocument:
    """A PDF prepared once and reused across every stage.

    Holds both views the pipeline needs: the raw bytes for the passes that
    benefit from seeing the real layout, and pypdf's per-page text for the
    cited pass and for local citation verification. Extracting the text once
    here — rather than per stage — is what lets `extract.py` check a quote
    against exactly the same text the model was shown.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.name = path.name
        self._bytes = path.read_bytes()
        reader = PdfReader(str(path))
        self.page_text: list[str] = [(pg.extract_text() or "") for pg in reader.pages]

    @property
    def page_count(self) -> int:
        return len(self.page_text)

    def part(self) -> types.Part:
        """The PDF itself, for stages that should see the layout."""
        return types.Part.from_bytes(data=self._bytes, mime_type="application/pdf")

    def numbered_text(self) -> str:
        """Page-delimited plain text, for the pass that must cite page numbers.

        The delimiter is deliberately unmistakable and repeated on every page:
        the model's only way to report a correct page number is to read it off
        the marker immediately above the text it is quoting.
        """
        return "\n\n".join(
            f"=== PAGE {i} ===\n{t}" for i, t in enumerate(self.page_text, 1) if t.strip()
        )


class LLMClient:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.model
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self._client = genai.Client(api_key=key) if key else genai.Client()
        self.telemetry: list[StageTelemetry] = []

    # ------------------------------------------------------------ internals

    def _record(self, stage: str, effort: str, usage: Any, latency_ms: int) -> StageTelemetry:
        inp = getattr(usage, "prompt_token_count", 0) or 0
        out = getattr(usage, "candidates_token_count", 0) or 0
        # Thinking tokens bill as output but are reported separately.
        out += getattr(usage, "thoughts_token_count", 0) or 0
        cr = getattr(usage, "cached_content_token_count", 0) or 0
        # Gemini reports cached tokens inside the prompt count; the pricing
        # helper expects them separated, so net them out to avoid double billing.
        inp = max(inp - cr, 0)
        t = StageTelemetry(
            stage=stage,
            model=self.model,
            effort=effort,
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=cr,
            cache_write_tokens=0,
            latency_ms=latency_ms,
            cost_usd=price_call(self.model, inp, out, cr, 0),
        )
        self.telemetry.append(t)
        return t

    def _effort(self, stage: str) -> str:
        return STAGE_EFFORT.get(stage, "high")

    def _config(self, effort: str, **extra: Any) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_budget=THINKING_BUDGET.get(effort, THINKING_BUDGET["high"])
            ),
            **extra,
        )

    def _generate(self, stage: str, effort: str, **kwargs: Any) -> Any:
        """Single choke point for error handling and telemetry.

        The SDK already retries 429/5xx with backoff — deliberately not adding a
        second retry layer on top of it.
        """
        started = time.monotonic()
        try:
            resp = self._client.models.generate_content(**kwargs)
        except genai_errors.APIError as e:
            raise _api_error(stage, e) from e
        except (ConnectionError, TimeoutError) as e:
            raise LLMError(f"Could not reach the API in stage '{stage}': {e}") from e

        latency = int((time.monotonic() - started) * 1000)
        self._record(stage, effort, getattr(resp, "usage_metadata", None), latency)
        _check_refusal(stage, resp)
        return resp

    # ------------------------------------------------------------- public API

    def extract_structured(
        self, stage: str, doc: CachedDocument, prompt: str, schema: type[T]
    ) -> T:
        """Typed extraction via structured outputs.

        Passes the Pydantic model as the response schema so the SDK validates
        and instantiates it — we never hand-parse JSON out of a text response.
        Sees the PDF rather than the flattened text, because the numbers this
        pass is after mostly live in tables.
        """
        effort = self._effort(stage)
        resp = self._generate(
            stage,
            effort,
            model=self.model,
            contents=[doc.part(), prompt],
            config=self._config(
                effort,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        parsed = getattr(resp, "parsed", None)
        if parsed is None:
            raise LLMError(f"Stage '{stage}' returned no parseable output")
        return cast(T, parsed)

    def extract_cited(self, stage: str, doc: CachedDocument, prompt: str) -> str:
        """Provenance pass. Returns raw text for `extract.py` to parse and verify.

        Receives page-numbered text, not the PDF: the model has to be able to
        read a page number off the document to report one, and a PDF part
        carries no page markers the model can quote back. What comes out of here
        is an *assertion* of provenance — it is not trusted until the quote has
        been matched against the page it names.
        """
        effort = self._effort(stage)
        resp = self._generate(
            stage,
            effort,
            model=self.model,
            contents=[doc.numbered_text(), prompt],
            config=self._config(effort),
        )
        return _text_of(resp)

    def complete(
        self, stage: str, doc: CachedDocument | None, prompt: str, max_tokens: int = 16000
    ) -> str:
        """Plain text completion."""
        effort = self._effort(stage)
        contents: list[Any] = []
        if doc is not None:
            contents.append(doc.part())
        contents.append(prompt)

        resp = self._generate(
            stage,
            effort,
            model=self.model,
            contents=contents,
            config=self._config(effort, max_output_tokens=max_tokens),
        )
        return _text_of(resp)

    def count_tokens(self, doc: CachedDocument, prompt: str) -> int:
        """Pre-flight estimate. Never approximate this with a generic tokenizer —
        wrong vocabulary, and materially wrong on this content."""
        r = self._client.models.count_tokens(
            model=self.model,
            # The SDK types this union structurally; a heterogeneous list of
            # Part and str is accepted at runtime but not statically inferable.
            contents=cast(Any, [doc.part(), prompt]),
        )
        return int(getattr(r, "total_tokens", 0) or 0)

    # ---------------------------------------------------------------- summary

    def cost_summary(self) -> dict[str, Any]:
        return {
            "total_usd": round(sum(t.cost_usd for t in self.telemetry), 4),
            "total_ms": sum(t.latency_ms for t in self.telemetry),
            "cache_read_tokens": sum(t.cache_read_tokens for t in self.telemetry),
            "calls": len(self.telemetry),
            "by_stage": [t.model_dump() for t in self.telemetry],
        }


# ------------------------------------------------------------------ helpers

# finish_reason values that mean the model stopped for policy reasons rather
# than because it was done. MAX_TOKENS is deliberately absent: a truncated
# response is still partially usable, and the parsers downstream degrade to
# missing fields, which routes to review rather than crashing.
_REFUSAL_REASONS = {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"}


def _check_refusal(stage: str, resp: Any) -> None:
    """Raise before any caller touches the content.

    A blocked response comes back as a normal 200 with no usable parts, so
    indexing into it would raise an opaque IndexError three layers away from
    the cause.
    """
    feedback = getattr(resp, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        raise RefusalError(f"Prompt blocked in stage '{stage}' (reason: {feedback.block_reason})")
    for cand in getattr(resp, "candidates", None) or []:
        reason = str(getattr(cand, "finish_reason", "") or "").upper()
        if any(r in reason for r in _REFUSAL_REASONS):
            raise RefusalError(f"Model declined in stage '{stage}' (reason: {reason})")


def _text_of(resp: Any) -> str:
    """Concatenate the text parts of a response.

    `resp.text` is the SDK's convenience accessor but returns None when the
    candidate carries non-text parts, so the parts are walked directly.
    """
    if getattr(resp, "text", None):
        return str(resp.text)
    out: list[str] = []
    for cand in getattr(resp, "candidates", None) or []:
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "text", None):
                out.append(part.text)
    return "".join(out)


def model_is_priced(model: str) -> bool:
    return model in PRICING
