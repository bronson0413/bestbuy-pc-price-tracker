"""Append-only SQLite store.

Design rule: observations are never updated or deleted. A price correction is a
new row, so the audit trail of what was seen and when survives intact -- which
is what makes the trend chart defensible.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("data/prices.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    sku            TEXT PRIMARY KEY,
    brand          TEXT NOT NULL,
    model_name     TEXT NOT NULL,
    listing_title  TEXT,
    url            TEXT,
    declared_json  TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    group_key      TEXT NOT NULL,
    tier_key       TEXT NOT NULL DEFAULT 'unmatched',
    first_seen_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    sku               TEXT NOT NULL REFERENCES products(sku),
    captured_at_utc   TEXT NOT NULL,
    price_usd         REAL,
    regular_price_usd REAL,
    on_sale           INTEGER,
    availability      TEXT,
    source_method     TEXT NOT NULL,
    source_url        TEXT,
    collector_version TEXT NOT NULL,
    raw_json          TEXT,
    UNIQUE (sku, captured_at_utc, source_method)
);
CREATE INDEX IF NOT EXISTS idx_obs_sku_time ON observations (sku, captured_at_utc);

CREATE TABLE IF NOT EXISTS review_flags (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id INTEGER REFERENCES observations(id),
    sku            TEXT,
    rule           TEXT NOT NULL,
    severity       TEXT NOT NULL,
    detail         TEXT NOT NULL,
    raised_at_utc  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'open',
    resolved_by    TEXT,
    resolved_at_utc TEXT,
    note           TEXT
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_utc   TEXT NOT NULL,
    finished_utc  TEXT,
    source_method TEXT,
    attempted     INTEGER DEFAULT 0,
    succeeded     INTEGER DEFAULT 0,
    failed        INTEGER DEFAULT 0,
    notes         TEXT
);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@contextmanager
def connect(db_path: Path | str = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_product(
    conn: sqlite3.Connection,
    *,
    sku: str,
    brand: str,
    model_name: str,
    listing_title: str | None,
    url: str | None,
    declared: dict[str, Any],
    normalized: dict[str, Any],
    group_key: str,
    tier_key: str = "unmatched",
) -> None:
    _ensure_tier_column(conn)
    conn.execute(
        """INSERT INTO products (sku, brand, model_name, listing_title, url,
                declared_json, normalized_json, group_key, tier_key, first_seen_utc)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(sku) DO UPDATE SET
                brand=excluded.brand, model_name=excluded.model_name,
                listing_title=COALESCE(excluded.listing_title, products.listing_title),
                url=COALESCE(excluded.url, products.url),
                declared_json=excluded.declared_json,
                normalized_json=excluded.normalized_json,
                group_key=excluded.group_key, tier_key=excluded.tier_key""",
        (
            sku,
            brand,
            model_name,
            listing_title,
            url,
            json.dumps(declared),
            json.dumps(normalized),
            group_key,
            tier_key,
            utcnow(),
        ),
    )


def _ensure_tier_column(conn: sqlite3.Connection) -> None:
    """Add tier_key to databases created before it existed.

    Migrating in place keeps the price history from earlier runs, which is the
    whole point of an append-only store.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(products)")}
    if "tier_key" not in cols:
        conn.execute(
            "ALTER TABLE products ADD COLUMN tier_key TEXT NOT NULL DEFAULT 'unmatched'"
        )


def insert_observation(
    conn: sqlite3.Connection,
    *,
    sku: str,
    captured_at_utc: str,
    price_usd: float | None,
    regular_price_usd: float | None,
    on_sale: bool | None,
    availability: str | None,
    source_method: str,
    source_url: str | None,
    collector_version: str,
    raw: dict[str, Any] | None,
) -> int | None:
    cur = conn.execute(
        """INSERT OR IGNORE INTO observations
           (sku, captured_at_utc, price_usd, regular_price_usd, on_sale, availability,
            source_method, source_url, collector_version, raw_json)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            sku,
            captured_at_utc,
            price_usd,
            regular_price_usd,
            None if on_sale is None else int(on_sale),
            availability,
            source_method,
            source_url,
            collector_version,
            json.dumps(raw) if raw is not None else None,
        ),
    )
    return cur.lastrowid if cur.rowcount else None


def last_observation(conn: sqlite3.Connection, sku: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM observations WHERE sku=? AND price_usd IS NOT NULL
           ORDER BY captured_at_utc DESC LIMIT 1""",
        (sku,),
    ).fetchone()


def raise_flag(
    conn: sqlite3.Connection,
    *,
    observation_id: int | None,
    sku: str | None,
    rule: str,
    severity: str,
    detail: str,
) -> None:
    conn.execute(
        """INSERT INTO review_flags (observation_id, sku, rule, severity, detail, raised_at_utc)
           VALUES (?,?,?,?,?,?)""",
        (observation_id, sku, rule, severity, detail, utcnow()),
    )


def start_run(conn: sqlite3.Connection, source_method: str) -> int:
    cur = conn.execute(
        "INSERT INTO collection_runs (started_utc, source_method) VALUES (?,?)",
        (utcnow(), source_method),
    )
    if cur.lastrowid is None:  # pragma: no cover - sqlite always sets this
        raise RuntimeError("could not open a collection run")
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    attempted: int,
    succeeded: int,
    failed: int,
    notes: str = "",
) -> None:
    conn.execute(
        """UPDATE collection_runs SET finished_utc=?, attempted=?, succeeded=?,
           failed=?, notes=? WHERE id=?""",
        (utcnow(), attempted, succeeded, failed, notes, run_id),
    )
