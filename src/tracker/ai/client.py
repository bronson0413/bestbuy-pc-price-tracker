"""A thin, testable Gemini client.

Deliberately not using the vendor SDK. The REST surface needed here is one
endpoint, and depending on the SDK would make the call path harder to stub in
tests and harder to swap for a different provider. Everything the rest of the
package sees is `LLMResponse`, so the provider is one class away from being
replaceable.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

# An alias rather than a pinned version. A hard-coded model name is a dated
# assumption: the first run of this evaluation failed with a 404 because the
# pinned model had been retired.
#
# The lite model rather than the full one, on evidence. Specification
# extraction is a reading task with a fixed output schema, and the evaluation
# scored the lite model at 96.3% field accuracy with no hallucinations -- while
# the larger model exhausted its free-tier quota partway through and could not
# complete the run at all. A model that cannot finish is not more capable.
DEFAULT_MODEL = "gemini-flash-lite-latest"

# Free-tier quotas recover on a per-minute window, so the first retry needs to
# outlast that window rather than back off by a few seconds.
_RATE_LIMIT_BACKOFF = (20, 45, 70)
_SERVER_BACKOFF = (5, 15, 30)
ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# Models are asked for JSON and still sometimes wrap it in a code fence.
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


class LLMError(RuntimeError):
    """The model could not be reached, or returned something unusable."""


def _redact(text: str, secret: str) -> str:
    """Keep the key out of error messages, logs and screenshots.

    Google passes the key as a query parameter, so it appears verbatim in the
    URL that requests includes in its exception text. Any message that might be
    pasted into a bug report or a terminal transcript goes through here first.
    """
    if not secret:
        return text
    return text.replace(secret, f"{secret[:6]}...redacted")


@dataclass
class LLMResponse:
    """What came back, plus what it cost to get it.

    `raw_text` is kept even when parsing succeeds: when a reviewer disputes an
    extraction, the exact model output is the evidence.
    """

    text: str
    model: str
    latency_s: float
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> Any:
        """Parse the response as JSON, tolerating a code fence around it."""
        candidate = self.text.strip()
        if m := _FENCE.match(candidate):
            candidate = m.group(1)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise LLMError(f"model did not return JSON: {self.text[:200]!r}") from exc


class GeminiClient:
    """Calls Gemini and returns text. No domain knowledge lives here."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DEFAULT_MODEL,
        timeout: int = 45,
        max_retries: int = 4,
        temperature: float = 0.0,
        min_interval_s: float = 0.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        # Zero temperature: extraction is a reading task, not a writing task.
        # The same listing must produce the same specification every time, or
        # the audit trail means nothing.
        self.temperature = temperature
        # Spacing calls is cheaper than retrying them: the free tier rejects
        # bursts, and a rejected call still costs a round trip.
        self.min_interval_s = min_interval_s
        self._last_call = 0.0

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def generate(
        self, prompt: str, *, system: str | None = None, json_only: bool = True
    ) -> LLMResponse:
        if not self.available:
            raise LLMError("GEMINI_API_KEY is not set")

        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "candidateCount": 1,
            },
        }
        if json_only:
            body["generationConfig"]["responseMimeType"] = "application/json"
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        url = ENDPOINT.format(model=self.model)
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            if self.min_interval_s:
                delta = time.monotonic() - self._last_call
                if delta < self.min_interval_s:
                    time.sleep(self.min_interval_s - delta)
            self._last_call = time.monotonic()
            started = time.monotonic()
            resp = None
            try:
                resp = requests.post(
                    url,
                    params={"key": self.api_key},
                    json=body,
                    timeout=self.timeout,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 429:
                    time.sleep(
                        _RATE_LIMIT_BACKOFF[min(attempt, len(_RATE_LIMIT_BACKOFF) - 1)]
                    )
                    last_error = LLMError("rate limited (429)")
                    continue
                if resp.status_code in (401, 403):
                    raise LLMError(
                        f"rejected with HTTP {resp.status_code}; check GEMINI_API_KEY"
                    )
                if resp.status_code == 404:
                    raise LLMError(
                        f"model {self.model!r} is not available to this key. "
                        "List the models the key can reach and set `model=` "
                        "accordingly."
                    )
                resp.raise_for_status()
                return _to_response(resp.json(), self.model, time.monotonic() - started)
            except LLMError:
                raise
            except Exception as exc:
                # requests embeds the full URL -- and therefore the key -- in
                # its exception text, so redact at the boundary rather than
                # once at the end.
                last_error = LLMError(_redact(str(exc), self.api_key))
                if resp is not None and resp.status_code >= 500:
                    # Free-tier capacity errors are transient and worth waiting
                    # out; a short backoff just burns the remaining attempts.
                    time.sleep(_SERVER_BACKOFF[min(attempt, len(_SERVER_BACKOFF) - 1)])
                else:
                    time.sleep(2**attempt)

        raise LLMError(
            f"generation failed after {self.max_retries} attempts: {last_error}"
        )


def _to_response(payload: dict[str, Any], model: str, latency: float) -> LLMResponse:
    candidates = payload.get("candidates") or []
    if not candidates:
        # A blocked prompt returns no candidates and a reason. Surfacing it is
        # more useful than an empty string.
        feedback = payload.get("promptFeedback", {})
        raise LLMError(f"no candidate returned; feedback={feedback}")

    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    if not text.strip():
        raise LLMError(
            f"empty response; finishReason={candidates[0].get('finishReason')}"
        )

    usage = payload.get("usageMetadata", {})
    return LLMResponse(
        text=text,
        model=model,
        latency_s=latency,
        prompt_tokens=usage.get("promptTokenCount"),
        output_tokens=usage.get("candidatesTokenCount"),
        raw=payload,
    )
