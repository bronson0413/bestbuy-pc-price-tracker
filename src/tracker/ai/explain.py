"""Turn a raised flag into a hypothesis a reviewer can check.

A flag says `price_jump_critical: -42%`. That is accurate and nearly useless:
the reviewer still has to open the product page and work out what happened.

This produces the first draft of that reasoning -- "a 42% drop with the title
now reading Open-Box suggests the listing switched condition" -- so the reviewer
is confirming or rejecting a specific claim rather than starting cold.

It is explicitly a hypothesis, not a verdict. The output never changes a flag's
status, never touches a price, and is labelled as model-generated wherever it
is shown. Its value is in saving the first five minutes of an investigation,
not in replacing it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .client import GeminiClient, LLMError

SYSTEM = """\
You help a reviewer investigate a flagged price observation on a retail tracker.

Rules:
- Propose the most likely explanations for what the data shows. Be specific \
about what would confirm or rule out each one.
- Use only the evidence given. Do not assume facts about the product that are \
not stated.
- If the evidence does not support any confident explanation, say so.
- You are drafting a hypothesis for a human to check, not deciding anything.

Return JSON:
{"hypotheses": [{"claim": "...", "check": "what would confirm or refute it"}],
 "confidence": "high|medium|low",
 "recommended_action": "one sentence"}
"""

PROMPT = """\
FLAG: {rule} ({severity})
DETAIL: {detail}

PRODUCT: {brand} {model} (SKU {sku})
REGISTERED CONFIGURATION: {configuration}
CURRENT LISTING TITLE: {title}

PRICE HISTORY (oldest first):
{history}

What most likely happened?"""


@dataclass
class Explanation:
    hypotheses: list[dict[str, str]]
    confidence: str
    recommended_action: str
    raw: dict[str, Any]

    @property
    def is_confident(self) -> bool:
        return self.confidence in ("high", "medium")


class FlagExplainer:
    def __init__(self, client: GeminiClient | None = None) -> None:
        self.client = client or GeminiClient()

    @property
    def available(self) -> bool:
        return self.client.available

    def explain(
        self,
        *,
        rule: str,
        severity: str,
        detail: str,
        sku: str,
        brand: str = "",
        model: str = "",
        title: str = "",
        configuration: str = "",
        history: list[dict[str, Any]] | None = None,
    ) -> Explanation:
        response = self.client.generate(
            PROMPT.format(
                rule=rule,
                severity=severity,
                detail=detail,
                sku=sku,
                brand=brand or "(unknown)",
                model=model or "(unknown)",
                title=title or "(not recorded)",
                configuration=configuration or "(not recorded)",
                history=_format_history(history or []),
            ),
            system=SYSTEM,
        )
        payload = response.as_json()
        if not isinstance(payload, dict):
            raise LLMError(f"expected a JSON object, got {type(payload).__name__}")
        return _vet(payload)


def _vet(payload: dict[str, Any]) -> Explanation:
    """Keep only well-formed hypotheses; a malformed one is dropped, not guessed at."""
    hypotheses: list[dict[str, str]] = []
    for item in payload.get("hypotheses") or []:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim", "")).strip()
        if not claim:
            continue
        hypotheses.append({"claim": claim, "check": str(item.get("check", "")).strip()})

    confidence = str(payload.get("confidence", "low")).lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "low"

    return Explanation(
        hypotheses=hypotheses,
        confidence=confidence,
        recommended_action=str(payload.get("recommended_action", "")).strip(),
        raw=payload,
    )


def _format_history(history: list[dict[str, Any]]) -> str:
    if not history:
        return "(no prior observations)"
    return "\n".join(
        f"  {row.get('captured_at_utc', '?')}  ${row.get('price_usd', '?')}"
        f"  via {row.get('source_method', '?')}"
        for row in history[-12:]
    )


def context_for(db, sku: str) -> dict[str, Any]:
    """Gather what a reviewer would have open in front of them."""
    from .. import storage as st

    with st.connect(db) as conn:
        product = conn.execute(
            "SELECT brand, model_name, listing_title, declared_json FROM products "
            "WHERE sku = ?",
            (sku,),
        ).fetchone()
        history = [
            dict(r)
            for r in conn.execute(
                """SELECT captured_at_utc, price_usd, source_method
                   FROM observations WHERE sku = ? AND price_usd IS NOT NULL
                   ORDER BY captured_at_utc""",
                (sku,),
            )
        ]

    if product is None:
        return {"sku": sku, "history": history}

    try:
        declared = json.loads(product["declared_json"] or "{}")
    except json.JSONDecodeError:
        declared = {}

    return {
        "sku": sku,
        "brand": product["brand"],
        "model": product["model_name"],
        "title": product["listing_title"] or "",
        "configuration": ", ".join(f"{k}={v}" for k, v in declared.items()) or "",
        "history": history,
    }
