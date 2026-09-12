"""The store's job is to never lose or silently alter an observation."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from tracker import storage as st


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "prices.sqlite"


def _product(conn, sku="6603654", group="g1", tier="t1"):
    st.upsert_product(
        conn,
        sku=sku,
        brand="ASUS",
        model_name="ExpertBook P5",
        listing_title="ASUS ExpertBook P5",
        url=None,
        declared={},
        normalized={},
        group_key=group,
        tier_key=tier,
    )


def _observe(conn, sku="6603654", when="2026-09-11T10:00:00+00:00", price=1199.99):
    return st.insert_observation(
        conn,
        sku=sku,
        captured_at_utc=when,
        price_usd=price,
        regular_price_usd=None,
        on_sale=None,
        availability="regular",
        source_method="test",
        source_url=None,
        collector_version="test",
        raw=None,
    )


def test_a_repeated_snapshot_is_not_counted_twice(db: Path) -> None:
    with st.connect(db) as conn:
        _product(conn)
        first = _observe(conn)
        second = _observe(conn)  # identical sku/time/source
        assert first is not None
        assert second is None  # ignored, not duplicated
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1


def test_the_same_sku_from_two_sources_is_two_observations(db: Path) -> None:
    with st.connect(db) as conn:
        _product(conn)
        _observe(conn)
        st.insert_observation(
            conn,
            sku="6603654",
            captured_at_utc="2026-09-11T10:00:00+00:00",
            price_usd=1199.99,
            regular_price_usd=None,
            on_sale=None,
            availability=None,
            source_method="other-source",
            source_url=None,
            collector_version="test",
            raw=None,
        )
        # Two independent readings agreeing is evidence, so both are kept.
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2


def test_last_observation_ignores_rows_without_a_price(db: Path) -> None:
    with st.connect(db) as conn:
        _product(conn)
        _observe(conn, when="2026-09-11T10:00:00+00:00", price=1199.99)
        st.insert_observation(
            conn,
            sku="6603654",
            captured_at_utc="2026-09-11T16:00:00+00:00",
            price_usd=None,
            regular_price_usd=None,
            on_sale=None,
            availability=None,
            source_method="failed",
            source_url=None,
            collector_version="test",
            raw=None,
        )
        last = st.last_observation(conn, "6603654")
        assert last is not None
        assert last["price_usd"] == 1199.99  # the failed run is not "the latest price"


def test_updating_a_product_never_drops_its_price_history(db: Path) -> None:
    with st.connect(db) as conn:
        _product(conn, group="old-group", tier="old-tier")
        _observe(conn)
    with st.connect(db) as conn:
        _product(conn, group="new-group", tier="new-tier")
        row = conn.execute("SELECT group_key, tier_key FROM products").fetchone()
        assert row["group_key"] == "new-group"
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1


def test_a_database_predating_tier_keys_is_migrated_in_place(db: Path) -> None:
    # Simulate the schema as it shipped before tier grouping existed.
    conn = sqlite3.connect(db)
    conn.executescript(
        """CREATE TABLE products (
               sku TEXT PRIMARY KEY, brand TEXT NOT NULL, model_name TEXT NOT NULL,
               listing_title TEXT, url TEXT, declared_json TEXT NOT NULL,
               normalized_json TEXT NOT NULL, group_key TEXT NOT NULL,
               first_seen_utc TEXT NOT NULL);
           INSERT INTO products VALUES
               ('6603654','ASUS','P5','t',NULL,'{}','{}','g1','2026-09-11T00:00:00+00:00');"""
    )
    conn.commit()
    conn.close()

    with st.connect(db) as conn:
        _product(conn)  # triggers the migration
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(products)")}
        assert "tier_key" in cols
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1


def test_flags_and_runs_are_recorded_for_audit(db: Path) -> None:
    with st.connect(db) as conn:
        _product(conn)
        obs_id = _observe(conn)
        st.raise_flag(
            conn,
            observation_id=obs_id,
            sku="6603654",
            rule="price_jump_warning",
            severity="warning",
            detail="+18%",
        )
        run = st.start_run(conn, "test-chain")
        st.finish_run(conn, run, attempted=1, succeeded=1, failed=0, notes="ok")

        flag = conn.execute("SELECT * FROM review_flags").fetchone()
        assert flag["status"] == "open"  # raised, not resolved
        assert flag["observation_id"] == obs_id
        assert (
            conn.execute(
                "SELECT succeeded FROM collection_runs WHERE id=?", (run,)
            ).fetchone()[0]
            == 1
        )
