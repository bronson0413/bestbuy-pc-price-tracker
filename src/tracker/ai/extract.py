"""Extract a specification from unstructured listing text.

Why this exists: the rule-based parser reads titles, and titles are often
incomplete. Two of the six tracked listings say only "Intel Core Ultra 5" with
no model suffix; the actual processors (225U and 236V) appear only in the
free-text description. A third describes a 360-degree convertible in its
description while its title says "Laptop Computer".

Those gaps were closed by a person reading product pages. That does not scale.
A language model reads unstructured prose well, which is precisely the task the
regular expressions cannot do.

What the model is NOT allowed to do:
  - override a value the rules already read with confidence
  - emit a value that does not conform to the parser's own vocabulary
  - decide grouping

Every field it returns is re-normalised through `normalize.py`, so the model
cannot introduce a token the deterministic layer would not have produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..normalize import (
    NormalizedSpec,
    normalize_cpu,
    normalize_device_type,
    normalize_form_factor,
    normalize_os,
    normalize_ram_gb,
    normalize_storage_gb,
)
from .client import GeminiClient, LLMError

SYSTEM = """\
You extract PC specifications from retail listing text.

Rules:
- Report only what the text states. Never infer from brand, price, or typical \
configurations.
- If the text does not state a field, return null for it. A null is correct; \
a guess is not.
- Prefer the product description over the title when they disagree, and say so \
in `notes`.
- Return JSON only.
"""

PROMPT = """\
Extract the specification from this Best Buy listing.

TITLE:
{title}

DESCRIPTION:
{description}

Return this exact JSON shape:
{{
  "cpu": "full processor name including model suffix, or null",
  "ram_gb": integer or null,
  "storage_gb": integer or null,
  "os": "operating system edition as stated, or null",
  "device_type": "laptop, desktop, or aio, or null",
  "form_factor": "clamshell, convertible, detachable, aio, desktop-sff, or \
desktop-tower, or null",
  "confidence": "high, medium, or low",
  "notes": "one sentence on anything ambiguous, or an empty string"
}}

Guidance:
- "cpu" must include the model suffix when the text provides one, e.g. \
"Intel Core Ultra 5 226V", not "Intel Core Ultra 5".
- "form_factor" is "convertible" when the text mentions a 360-degree hinge, \
2-in-1, flip, or tablet mode, even if the title calls it a laptop.
- "storage_gb" is the SSD capacity in gigabytes; convert TB to GB.
- "ram_gb" is system memory, never storage.
"""


@dataclass
class ExtractionResult:
    """What the model proposed, after the deterministic layer has vetted it.

    `spec` holds only values that survived re-normalisation. `rejected` records
    what the model said that the vocabulary would not accept -- kept because a
    pattern of rejections is a signal about the prompt, not noise.
    """

    spec: NormalizedSpec
    confidence: str
    notes: str
    rejected: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    latency_s: float = 0.0

    @property
    def usable(self) -> bool:
        """Low-confidence output is recorded but never acted on."""
        return self.confidence in ("high", "medium")


class SpecExtractor:
    """Turns listing prose into a NormalizedSpec, or into nothing."""

    def __init__(self, client: GeminiClient | None = None) -> None:
        self.client = client or GeminiClient()

    @property
    def available(self) -> bool:
        return self.client.available

    def extract(self, *, title: str, description: str = "") -> ExtractionResult:
        response = self.client.generate(
            PROMPT.format(title=title or "(none)", description=description or "(none)"),
            system=SYSTEM,
        )
        payload = response.as_json()
        if not isinstance(payload, dict):
            raise LLMError(f"expected a JSON object, got {type(payload).__name__}")
        result = _vet(payload)
        result.latency_s = response.latency_s
        return result


def _vet(payload: dict[str, Any]) -> ExtractionResult:
    """Re-normalise every field the model returned.

    This is the control that matters. The model may write "Core Ultra 5 226V",
    "intel ultra5 226v", or something invented; only strings that the project's
    own normaliser recognises become part of the spec. Anything else is
    recorded as rejected and does not reach the grouping logic.
    """
    rejected: dict[str, Any] = {}

    def vet(field_name: str, value: Any, normaliser) -> Any:
        if value in (None, "", "null"):
            return None
        normalised = normaliser(str(value))
        if normalised is None:
            rejected[field_name] = value
        return normalised

    def vet_int(field_name: str, value: Any, normaliser) -> int | None:
        if value in (None, "", "null"):
            return None
        # The model may return a bare integer or a phrase; normalise both.
        normalised = normaliser(
            f"{value}GB" if isinstance(value, int | float) else str(value)
        )
        if normalised is None:
            rejected[field_name] = value
        return normalised

    spec = NormalizedSpec(
        cpu=vet("cpu", payload.get("cpu"), normalize_cpu),
        ram_gb=vet_int("ram_gb", payload.get("ram_gb"), _ram_of),
        storage_gb=vet_int("storage_gb", payload.get("storage_gb"), _storage_of),
        os=vet("os", payload.get("os"), normalize_os),
        device_type=vet(
            "device_type", payload.get("device_type"), normalize_device_type
        ),
        form_factor=vet(
            "form_factor", payload.get("form_factor"), normalize_form_factor
        ),
    )

    confidence = str(payload.get("confidence", "low")).lower()
    if confidence not in ("high", "medium", "low"):
        rejected["confidence"] = payload.get("confidence")
        confidence = "low"

    return ExtractionResult(
        spec=spec,
        confidence=confidence,
        notes=str(payload.get("notes", "")),
        rejected=rejected,
        raw=payload,
    )


def _ram_of(text: str) -> int | None:
    """Memory phrased however the model chose to phrase it."""
    return normalize_ram_gb(text if "gb" in text.lower() else f"{text}GB RAM")


def _storage_of(text: str) -> int | None:
    return normalize_storage_gb(text if "ssd" in text.lower() else f"{text} SSD")
