"""Health reporting reads the audit trail back. It must not invent a diagnosis."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from tracker import storage as st
from tracker.health import _summarise, report

# Timestamps are relative to now, not hard-coded. A fixed date silently becomes
# "stale" as the calendar moves past it, which makes the test fail for a reason
# that has nothing to do with the code.
RECENT = (datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds")
OLD = (datetime.now(UTC) - timedelta(days=3)).isoformat(timespec="seconds")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "prices.sqlite"
    with st.connect(path) as conn:
        st.upsert_product(
            conn,
            sku="6603654",
            brand="ASUS",
            model_name="P5",
            listing_title="P5",
            url=None,
            declared={},
            normalized={},
            group_key="g",
            tier_key="t",
        )
        st.insert_observation(
            conn,
            sku="6603654",
            captured_at_utc=RECENT,
            price_usd=1199.99,
            regular_price_usd=None,
            on_sale=None,
            availability="regular",
            source_method="rapidapi_bestbuy",
            source_url=None,
            collector_version="test",
            raw=None,
        )
        st.insert_observation(
            conn,
            sku="6603654",
            captured_at_utc=RECENT,
            price_usd=1199.99,
            regular_price_usd=None,
            on_sale=None,
            availability="regular",
            source_method="manual_entry",
            source_url=None,
            collector_version="test",
            raw=None,
        )
        st.raise_flag(
            conn,
            observation_id=None,
            sku="6603654",
            rule="collection_failed",
            severity="critical",
            detail="bestbuy_web: unexpected Error: net::ERR_HTTP2_PROTOCOL_ERROR "
            "| manual_entry: no manual entry for sku 6603654",
        )
        run = st.start_run(conn, "rapidapi_bestbuy")
        st.finish_run(conn, run, attempted=3, succeeded=2, failed=1)
    return path


def test_an_empty_database_reports_no_data(tmp_path: Path) -> None:
    r = report(tmp_path / "empty.sqlite")
    assert r.status == "no data"
    assert r.total_observations == 0
    assert r.overall_success_rate == 0.0


def test_each_source_is_counted_separately(db: Path) -> None:
    r = report(db)
    methods = {s.source_method: s.observations for s in r.sources}
    # Two sources agreeing on one price is two observations, not one.
    assert methods == {"rapidapi_bestbuy": 1, "manual_entry": 1}
    assert r.total_observations == 2


def test_run_success_rate_comes_from_the_run_log(db: Path) -> None:
    r = report(db)
    assert len(r.runs) == 1
    assert r.runs[0].success_rate == pytest.approx(2 / 3)
    assert r.overall_success_rate == pytest.approx(2 / 3)


def test_failures_are_grouped_by_cause_not_by_product(db: Path) -> None:
    r = report(db)
    reasons = dict(r.failure_reasons)
    # One flag carried two causes; both are counted, and both are readable.
    assert "bestbuy_web: http2 connection terminated" in reasons
    assert "manual_entry: no manual entry recorded" in reasons


def test_open_flags_are_reported_by_severity(db: Path) -> None:
    assert report(db).open_flags == {"critical": 1}


def test_a_critical_flag_makes_the_status_needs_review(db: Path) -> None:
    assert report(db).status == "needs review"


def test_recent_data_is_not_stale(db: Path) -> None:
    r = report(db)
    assert r.hours_since_capture is not None
    assert r.hours_since_capture < 24
    assert not r.is_stale


def test_data_older_than_a_day_is_reported_as_stale(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    with st.connect(path) as conn:
        st.upsert_product(
            conn,
            sku="1",
            brand="B",
            model_name="M",
            listing_title="M",
            url=None,
            declared={},
            normalized={},
            group_key="g",
            tier_key="t",
        )
        st.insert_observation(
            conn,
            sku="1",
            captured_at_utc=OLD,
            price_usd=100.0,
            regular_price_usd=None,
            on_sale=None,
            availability=None,
            source_method="test",
            source_url=None,
            collector_version="t",
            raw=None,
        )

    r = report(path)
    assert r.is_stale
    # Staleness outranks a clean flag table: data nobody refreshed is the more
    # urgent problem.
    assert r.status == "stale"


@pytest.mark.parametrize(
    "detail,expected",
    [
        (
            "bestbuy_web: net::ERR_HTTP2_PROTOCOL_ERROR at https://...",
            "bestbuy_web: http2 connection terminated",
        ),
        (
            "bestbuy_web: could not load page: Page.goto: Timeout 60000ms exceeded",
            "bestbuy_web: page load timed out",
        ),
        (
            "bestbuy_web: blocked by bot mitigation (challenge page returned)",
            "bestbuy_web: blocked by bot mitigation",
        ),
        (
            "bestbuy_products_api: BESTBUY_API_KEY is not set",
            "bestbuy_products_api: no credentials configured",
        ),
        (
            "rapidapi_bestbuy: sku mismatch: requested 1, received 2",
            "rapidapi_bestbuy: source answered about a different sku",
        ),
    ],
)
def test_error_strings_collapse_to_their_cause(detail: str, expected: str) -> None:
    # Raw messages carry SKUs and URLs that differ every time; grouping by cause
    # is what turns a list of failures into a diagnosis.
    assert _summarise(detail) == expected


def test_an_unrecognised_error_is_kept_rather_than_discarded() -> None:
    out = _summarise("some_source: a failure nobody anticipated")
    assert out.startswith("some_source:")
    assert "anticipated" in out


def test_an_empty_detail_is_ignored() -> None:
    assert _summarise("   ") == ""
