"""Reporting must summarise what was observed without reinterpreting it."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from tracker import COLLECTOR_VERSION
from tracker import storage as st
from tracker.report import (
    humanize_group,
    load_flags,
    load_frame,
    plot_group,
    price_summary,
)

TIER = "intel-ultra5|16gb|512gb|windows-11-pro|laptop|clamshell"
EXACT = "intel-ultra5-226v|16gb|512gb|windows-11-pro|laptop|clamshell"


@pytest.fixture
def populated(tmp_path: Path) -> Path:
    db = tmp_path / "prices.sqlite"
    fixtures = [
        ("6603654", "ASUS", "ExpertBook P5", EXACT, [1199.99, 1249.99]),
        ("12251856", "HP", "EliteBook 16", EXACT, [1678.49, 1599.00]),
        (
            "12190191",
            "Dell",
            "Pro 14",
            "intel-ultra5-236v|16gb|512gb|windows-11-pro|laptop|clamshell",
            [1914.24, 1914.24],
        ),
    ]
    with st.connect(db) as conn:
        for sku, brand, model, group, prices in fixtures:
            st.upsert_product(
                conn,
                sku=sku,
                brand=brand,
                model_name=model,
                listing_title=model,
                url=None,
                declared={},
                normalized={},
                group_key=group,
                tier_key=TIER,
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
                    collector_version=COLLECTOR_VERSION,
                    raw=None,
                )
        st.raise_flag(
            conn,
            observation_id=None,
            sku="6603654",
            rule="spec_drift",
            severity="critical",
            detail="test",
        )
    return db


def test_an_empty_database_returns_an_empty_frame(tmp_path: Path) -> None:
    assert load_frame(tmp_path / "none.sqlite").empty


def test_loads_both_grouping_keys(populated: Path) -> None:
    df = load_frame(populated)
    assert len(df) == 6
    assert set(df["tier_key"]) == {TIER}  # all three share a tier
    assert df["group_key"].nunique() == 2  # but not an exact group
    assert "ASUS ExpertBook P5" in set(df["label"])


def test_summary_reports_direction_of_change_per_product(populated: Path) -> None:
    df = load_frame(populated)
    s = price_summary(df, "tier_key").set_index("sku")
    assert s.loc["6603654", "change_usd"] == pytest.approx(50.0)
    assert s.loc["12251856", "change_pct"] < 0  # HP fell
    assert s.loc["12190191", "change_usd"] == 0  # Dell did not move
    assert s.loc["6603654", "snapshots"] == 2


def test_summary_of_nothing_is_nothing(populated: Path) -> None:
    df = load_frame(populated)
    assert price_summary(df.iloc[0:0]).empty


def test_chart_is_written_for_a_real_group(populated: Path, tmp_path: Path) -> None:
    out = plot_group(load_frame(populated), TIER, tmp_path / "t.png", col="tier_key")
    assert out.exists() and out.stat().st_size > 5_000


def test_charting_an_unknown_group_fails_loudly(
    populated: Path, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="no observations"):
        plot_group(load_frame(populated), "no-such-group", tmp_path / "x.png")


def test_flags_are_readable_for_review(populated: Path) -> None:
    flags = load_flags(populated)
    assert len(flags) == 1
    assert flags.iloc[0]["rule"] == "spec_drift"
    assert flags.iloc[0]["status"] == "open"


def test_group_keys_are_rendered_for_humans() -> None:
    assert "16GB RAM" in humanize_group(TIER)
    assert humanize_group("malformed") == "malformed"  # unparseable, shown as-is


# --- the enhanced charts ----------------------------------------------------


def test_the_trend_chart_reports_the_spread_in_its_subtitle(
    populated: Path, tmp_path: Path
) -> None:
    # The finding belongs on the chart, not only in the surrounding prose.
    out = plot_group(load_frame(populated), TIER, tmp_path / "t.png", col="tier_key")
    assert out.exists() and out.stat().st_size > 5_000


def test_promotion_markers_do_not_break_a_chart_without_them(
    populated: Path, tmp_path: Path
) -> None:
    df = load_frame(populated)
    assert "on_sale" in df.columns  # loaded even when every value is null
    assert plot_group(df, TIER, tmp_path / "no-promo.png", col="tier_key").exists()


def test_group_comparison_drops_unmatched_products(populated: Path) -> None:
    from tracker.report import group_comparison

    df = load_frame(populated)
    latest = group_comparison(df, "tier_key")
    assert len(latest) == 3  # one row per product, newest observation
    assert "unmatched" not in set(latest["tier_key"])


def test_the_premium_chart_renders_for_real_groups(
    populated: Path, tmp_path: Path
) -> None:
    from tracker.report import plot_group_premium

    out = plot_group_premium(load_frame(populated), tmp_path / "premium.png")
    assert out.exists() and out.stat().st_size > 5_000


def test_the_premium_chart_refuses_an_empty_frame(tmp_path: Path) -> None:
    import pandas as pd

    from tracker.report import plot_group_premium

    with pytest.raises(ValueError, match="no groups"):
        plot_group_premium(pd.DataFrame(), tmp_path / "x.png")
