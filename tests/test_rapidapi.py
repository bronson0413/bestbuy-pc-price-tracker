import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from tracker.sources import RapidApiSource, SourceError

OK = {
    "success": True, "message": "Success", "error": None,
    "data": {
        "customerPrice": 1199.99, "previousPrice": "1,199.99",
        "isOnSale": False, "hasSavings": False,
        "priceChangeTotalSavingsAmount": 0,
        "skuDataAnalytics": {"customerPrice": 1199.99,
                             "pricingType": "regular", "skuId": "6603654"},
    },
}


@pytest.fixture
def source():
    return RapidApiSource(api_key="test-key")


def test_parses_price_and_provenance(source):
    q = source._to_quote("6603654", OK, "https://example.invalid")
    assert q.price_usd == 1199.99
    assert q.regular_price_usd == 1199.99      # comma-formatted string parsed
    assert q.on_sale is False
    assert q.source_method == "rapidapi_bestbuy"


def test_rejects_a_response_for_a_different_sku(source):
    with pytest.raises(SourceError, match="sku mismatch"):
        source._to_quote("9999999", OK, None)


def test_rejects_failure_and_empty_payloads(source):
    with pytest.raises(SourceError):
        source._to_quote("6603654", {"success": False, "message": "nope"}, None)
    with pytest.raises(SourceError):
        source._to_quote("6603654", {"success": True, "data": {}}, None)


def test_falls_back_to_the_analytics_price(source):
    payload = {**OK, "data": {**OK["data"], "customerPrice": None}}
    assert source._to_quote("6603654", payload, None).price_usd == 1199.99


def test_missing_key_is_reported_not_silently_skipped():
    bare = RapidApiSource(api_key="")
    assert not bare.available
    with pytest.raises(SourceError, match="RAPIDAPI_KEY"):
        bare.fetch("6603654")
