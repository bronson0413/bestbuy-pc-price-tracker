"""The agent may query the data. It may not invent it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from tracker import storage as st
from tracker.ai.agent import ToolCallingAgent
from tracker.ai.client import LLMResponse
from tracker.ai.tools import ToolError, build_registry, execute


class ScriptedClient:
    """Replays a fixed list of model replies, recording what it was asked."""

    available = True

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def generate(
        self, prompt: str, *, system: str | None = None, json_only: bool = True
    ) -> LLMResponse:
        self.prompts.append(prompt)
        text = self.replies.pop(0) if self.replies else '{"answer": "done"}'
        return LLMResponse(text=text, model="scripted", latency_s=0.0)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "prices.sqlite"
    fixtures = [
        ("6603654", "ASUS", "ExpertBook P5", [1199.99, 1149.99]),
        ("12251856", "HP", "EliteBook 16", [1678.49, 1678.49]),
    ]
    tier = "intel-ultra5|16gb|512gb|windows-11-pro|laptop|clamshell"
    with st.connect(path) as conn:
        for sku, brand, model, prices in fixtures:
            st.upsert_product(
                conn,
                sku=sku,
                brand=brand,
                model_name=model,
                listing_title=model,
                url=None,
                declared={},
                normalized={},
                group_key=tier,
                tier_key=tier,
            )
            for i, price in enumerate(prices):
                st.insert_observation(
                    conn,
                    sku=sku,
                    captured_at_utc=f"2026-09-1{i + 1}T10:00:00+00:00",
                    price_usd=price,
                    regular_price_usd=None,
                    on_sale=None,
                    availability="regular",
                    source_method="test",
                    source_url=None,
                    collector_version="test",
                    raw=None,
                )
        st.raise_flag(
            conn,
            observation_id=None,
            sku="6603654",
            rule="price_jump_warning",
            severity="warning",
            detail="-4%",
        )
    return path


TIER = "intel-ultra5|16gb|512gb|windows-11-pro|laptop|clamshell"


# --- the handlers return data, never prose ----------------------------------


def test_compare_group_reports_the_spread(db: Path) -> None:
    out = execute(build_registry(db), "compare_group", {"group": TIER})
    assert out["cheapest"]["brand"] == "ASUS"
    assert out["dearest"]["brand"] == "HP"
    assert out["spread_usd"] == pytest.approx(528.50)
    assert out["premium_pct"] == pytest.approx(46.0, abs=0.1)


def test_price_history_reports_direction(db: Path) -> None:
    out = execute(build_registry(db), "price_history", {"sku": "6603654"})
    assert out["snapshots"] == 2
    assert out["change_usd"] == pytest.approx(-50.0)
    assert out["change_pct"] < 0


def test_find_movers_respects_its_threshold(db: Path) -> None:
    reg = build_registry(db)
    assert execute(reg, "find_movers", {"min_change_pct": 1})["count"] == 1
    assert execute(reg, "find_movers", {"min_change_pct": 90})["count"] == 0


def test_list_products_can_filter_by_brand(db: Path) -> None:
    reg = build_registry(db)
    assert execute(reg, "list_products", {})["count"] == 2
    assert execute(reg, "list_products", {"brand": "asus"})["count"] == 1


def test_open_flags_are_queryable(db: Path) -> None:
    out = execute(build_registry(db), "open_flags", {"severity": "warning"})
    assert out["count"] == 1
    assert out["flags"][0]["rule"] == "price_jump_warning"


def test_a_query_for_a_missing_product_says_so_rather_than_failing(db: Path) -> None:
    out = execute(build_registry(db), "price_history", {"sku": "0000000"})
    assert out["observations"] == [] and "note" in out


# --- argument validation ----------------------------------------------------


@pytest.mark.parametrize(
    "name,args,expected",
    [
        ("compare_group", {"group": "x", "basis": "galaxy"}, "not one of"),
        ("compare_group", {"grp": "x"}, "unknown argument"),
        ("price_history", {}, "missing required"),
        ("find_movers", {"min_change_pct": "a lot"}, "expected a number"),
        ("teleport", {}, "no such tool"),
    ],
)
def test_malformed_calls_are_refused(db: Path, name, args, expected) -> None:
    with pytest.raises(ToolError, match=expected):
        execute(build_registry(db), name, args)


def test_numbers_written_as_prices_are_accepted(db: Path) -> None:
    # A model may return "$1,100" where a number is wanted; that is recoverable.
    out = execute(build_registry(db), "find_movers", {"min_change_pct": "1"})
    assert out["threshold_pct"] == 1.0


# --- the loop ---------------------------------------------------------------


def test_a_tool_result_reaches_the_model_before_it_answers(db: Path) -> None:
    client = ScriptedClient(
        [
            json.dumps({"tool": "compare_group", "args": {"group": TIER}}),
            json.dumps(
                {
                    "answer": "ASUS is cheapest at $1,149.99.",
                    "grounded_in": ["compare_group"],
                }
            ),
        ]
    )
    answer = ToolCallingAgent(build_registry(db), client).ask("which is cheapest?")

    assert answer.used_tools == ["compare_group"]
    assert answer.is_grounded
    assert "1,149.99" in answer.text
    # The second prompt must contain the tool output the answer relies on.
    assert "TOOL RESULT" in client.prompts[1]
    assert "1149.99" in client.prompts[1]


def test_a_rejected_call_is_returned_to_the_model_not_repaired(db: Path) -> None:
    client = ScriptedClient(
        [
            json.dumps(
                {"tool": "compare_group", "args": {"group": TIER, "basis": "galaxy"}}
            ),
            json.dumps(
                {"tool": "compare_group", "args": {"group": TIER, "basis": "tier"}}
            ),
            json.dumps({"answer": "Recovered.", "grounded_in": ["compare_group"]}),
        ]
    )
    answer = ToolCallingAgent(build_registry(db), client).ask("compare them")

    assert [s.ok for s in answer.steps] == [False, True]
    assert "not one of" in (answer.steps[0].error or "")
    assert "TOOL ERROR" in client.prompts[1]  # the model saw its own mistake


def test_an_answer_with_no_successful_call_is_not_marked_grounded(db: Path) -> None:
    client = ScriptedClient(
        [
            json.dumps(
                {
                    "answer": "Laptops usually cost about $1,000.",
                    "grounded_in": ["general knowledge"],
                }
            )
        ]
    )
    answer = ToolCallingAgent(build_registry(db), client).ask("what do laptops cost?")

    assert answer.used_tools == []
    assert not answer.is_grounded  # the caller can refuse to display this


def test_the_loop_stops_rather_than_calling_forever(db: Path) -> None:
    looping = [json.dumps({"tool": "list_products", "args": {}})] * 10
    agent = ToolCallingAgent(build_registry(db), ScriptedClient(looping), max_steps=3)
    answer = agent.ask("tell me everything")

    assert len(answer.steps) == 3
    assert "Stopped after 3" in answer.text


def test_non_json_output_ends_the_loop_and_keeps_the_raw_text(db: Path) -> None:
    agent = ToolCallingAgent(build_registry(db), ScriptedClient(["I am not JSON"]))
    answer = agent.ask("hello")
    assert answer.text == "I am not JSON"
    assert answer.steps == []


def test_a_handler_that_raises_does_not_end_the_session(db: Path) -> None:
    registry = build_registry(db)
    registry["list_products"].handler = lambda **kw: 1 / 0
    client = ScriptedClient(
        [
            json.dumps({"tool": "list_products", "args": {}}),
            json.dumps(
                {"answer": "Could not read the product list.", "grounded_in": []}
            ),
        ]
    )
    answer = ToolCallingAgent(registry, client).ask("list them")
    assert answer.steps[0].ok is False
    assert "ZeroDivisionError" in (answer.steps[0].error or "")
    assert answer.text.startswith("Could not read")
