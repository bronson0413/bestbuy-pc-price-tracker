"""An explanation is a hypothesis. It must never read as a verdict."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


from tracker import storage as st
from tracker.ai.client import LLMResponse
from tracker.ai.explain import FlagExplainer, _vet, context_for

GOOD = {
    "hypotheses": [
        {
            "claim": "The listing switched to an open-box unit.",
            "check": "Open the product page and look for an Open-Box badge.",
        },
        {
            "claim": "A promotion started.",
            "check": "Check whether the retailer reports the item as on sale.",
        },
    ],
    "confidence": "medium",
    "recommended_action": "Open the product page before accepting the new price.",
}


class StubClient:
    available = True

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.last_prompt = ""

    def generate(
        self, prompt: str, *, system: str | None = None, json_only: bool = True
    ) -> LLMResponse:
        self.last_prompt = prompt
        return LLMResponse(text=json.dumps(self.payload), model="stub", latency_s=0.0)


def test_a_well_formed_explanation_is_kept_whole() -> None:
    e = _vet(GOOD)
    assert len(e.hypotheses) == 2
    assert e.is_confident
    assert e.recommended_action.startswith("Open the product page")


def test_every_hypothesis_carries_a_way_to_check_it() -> None:
    e = _vet(GOOD)
    assert all(h["check"] for h in e.hypotheses)


def test_malformed_hypotheses_are_dropped_not_repaired() -> None:
    e = _vet(
        {
            **GOOD,
            "hypotheses": [
                {"claim": "Valid one.", "check": "Look."},
                {"check": "no claim at all"},
                "not an object",
                {"claim": "   "},
            ],
        }
    )
    assert [h["claim"] for h in e.hypotheses] == ["Valid one."]


def test_an_unrecognised_confidence_downgrades_to_low() -> None:
    e = _vet({**GOOD, "confidence": "absolutely sure"})
    assert e.confidence == "low"
    assert not e.is_confident  # callers can decline to show this


def test_an_empty_response_yields_no_hypotheses() -> None:
    e = _vet({})
    assert e.hypotheses == []
    assert e.confidence == "low"


def test_the_prompt_carries_the_evidence_the_reviewer_would_have() -> None:
    client = StubClient(GOOD)
    FlagExplainer(client).explain(
        rule="price_jump_critical",
        severity="critical",
        detail="-42% vs previous",
        sku="6603654",
        brand="ASUS",
        model="ExpertBook P5",
        title="ASUS ExpertBook P5 Open-Box",
        configuration="cpu=Intel Core Ultra 5 226V",
        history=[
            {
                "captured_at_utc": "2026-09-11T10:00:00+00:00",
                "price_usd": 1199.99,
                "source_method": "rapidapi_bestbuy",
            }
        ],
    )
    prompt = client.last_prompt
    assert "price_jump_critical" in prompt
    assert "Open-Box" in prompt  # the title that may explain the drop
    assert "1199.99" in prompt  # and the history to compare against


def test_history_is_truncated_rather_than_sent_whole() -> None:
    client = StubClient(GOOD)
    long_history = [
        {
            "captured_at_utc": f"2026-09-{d:02d}T10:00:00+00:00",
            "price_usd": 1000 + d,
            "source_method": "test",
        }
        for d in range(1, 30)
    ]
    FlagExplainer(client).explain(
        rule="r", severity="warning", detail="d", sku="x", history=long_history
    )
    # Only the recent window is relevant to a jump, and prompts are not free.
    assert "2026-09-01" not in client.last_prompt
    assert "2026-09-29" in client.last_prompt


def test_missing_context_is_labelled_not_left_blank() -> None:
    client = StubClient(GOOD)
    FlagExplainer(client).explain(rule="r", severity="warning", detail="d", sku="x")
    assert "(not recorded)" in client.last_prompt
    assert "(no prior observations)" in client.last_prompt


def test_context_is_gathered_from_the_database(tmp_path: Path) -> None:
    db = tmp_path / "prices.sqlite"
    with st.connect(db) as conn:
        st.upsert_product(
            conn,
            sku="6603654",
            brand="ASUS",
            model_name="P5",
            listing_title="ASUS ExpertBook P5",
            url=None,
            declared={"cpu": "Intel Core Ultra 5 226V"},
            normalized={},
            group_key="g",
            tier_key="t",
        )
        st.insert_observation(
            conn,
            sku="6603654",
            captured_at_utc="2026-09-11T10:00:00+00:00",
            price_usd=1199.99,
            regular_price_usd=None,
            on_sale=None,
            availability=None,
            source_method="test",
            source_url=None,
            collector_version="t",
            raw=None,
        )

    ctx = context_for(db, "6603654")
    assert ctx["brand"] == "ASUS"
    assert "Intel Core Ultra 5 226V" in ctx["configuration"]
    assert len(ctx["history"]) == 1


def test_context_for_an_unknown_sku_returns_what_it_can(tmp_path: Path) -> None:
    db = tmp_path / "prices.sqlite"
    with st.connect(db):
        pass
    ctx = context_for(db, "0000000")
    assert ctx == {"sku": "0000000", "history": []}
