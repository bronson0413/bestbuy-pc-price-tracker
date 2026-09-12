"""The collector's job is to degrade in order and to never invent a price."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from tracker.collect import build_sources, collect_one, run
from tracker.sources import Quote, SourceError


class Stub:
    """A source that either answers or fails, and records that it was asked."""

    def __init__(self, name: str, price: float | None = None) -> None:
        self.name = name
        self.price = price
        self.calls = 0

    def fetch(self, sku: str, url: str | None = None) -> Quote:
        self.calls += 1
        if self.price is None:
            raise SourceError(f"{self.name} has nothing")
        return Quote(sku=sku, price_usd=self.price, source_method=self.name)


def test_the_first_working_source_wins_and_the_rest_are_not_called() -> None:
    first, second = Stub("first", 999.0), Stub("second", 111.0)
    quote, errors = collect_one([first, second], "6603654", None)
    assert quote is not None
    assert quote.price_usd == 999.0
    assert second.calls == 0  # no needless second-guessing
    assert errors == []


def test_a_failing_source_falls_through_and_the_failure_is_reported() -> None:
    broken, backup = Stub("broken"), Stub("backup", 849.99)
    quote, errors = collect_one([broken, backup], "6603654", None)
    assert quote is not None
    assert quote.source_method == "backup"
    assert len(errors) == 1  # the failure is recorded, not swallowed
    assert "broken" in errors[0]


def test_when_every_source_fails_no_price_is_produced() -> None:
    quote, errors = collect_one([Stub("a"), Stub("b")], "6603654", None)
    assert quote is None  # silence, never a guess
    assert len(errors) == 2


def test_an_unexpected_exception_is_contained_not_propagated() -> None:
    class Exploding:
        name = "exploding"

        def fetch(self, sku: str, url: str | None = None) -> Quote:
            raise ValueError("boom")

    quote, errors = collect_one([Exploding(), Stub("backup", 1.0)], "x", None)
    assert quote is not None  # one bad source must not end the run
    assert "ValueError" in errors[0]


def test_the_auto_chain_is_ordered_from_official_to_manual(monkeypatch) -> None:
    monkeypatch.setenv("BESTBUY_API_KEY", "key")
    monkeypatch.setenv("RAPIDAPI_KEY", "key")
    names = [s.name for s in build_sources("auto")]
    assert names == [
        "bestbuy_products_api",
        "rapidapi_bestbuy",
        "bestbuy_web",
        "manual_entry",
    ]


def test_sources_without_credentials_are_left_out_of_the_chain(monkeypatch) -> None:
    monkeypatch.delenv("BESTBUY_API_KEY", raising=False)
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)
    names = [s.name for s in build_sources("auto")]
    assert "bestbuy_products_api" not in names
    assert names == ["bestbuy_web", "manual_entry"]


def test_an_unknown_source_name_is_rejected() -> None:
    with pytest.raises(SystemExit):
        build_sources("telepathy")


def test_a_run_with_no_configured_products_exits_nonzero(tmp_path: Path) -> None:
    cfg = tmp_path / "empty.yaml"
    cfg.write_text("products: []\n", encoding="utf-8")
    assert run(cfg, tmp_path / "db.sqlite", "manual") == 2
