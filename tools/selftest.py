"""End-to-end self-test on synthetic data. Not part of the submission data.

Builds a throwaway database with invented prices, runs the storage, validation
and charting path over it, and writes a chart marked SYNTHETIC. Its only job is
to prove the pipeline works before the real collector has enough snapshots.

    python tools/selftest.py

The synthetic database lives at data/selftest.sqlite and is never read by the
app or the report CLI unless you point them at it explicitly.
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tracker import COLLECTOR_VERSION
from tracker import storage as st
from tracker.normalize import parse_spec
from tracker.report import load_frame, plot_group, price_summary
from tracker.validate import check_price

DB = Path("data/selftest.sqlite")
FIXTURES = [
    ("9990001", "HP", "OmniBook X Flip 14",
     'HP OmniBook X Flip 14" 2-in-1, Intel Core Ultra 5 226V, 16GB RAM, 512GB SSD, Windows 11 Home', 949.99),
    ("9990002", "Lenovo", "Yoga 7 2-in-1 14",
     'Lenovo Yoga 7 2-in-1 14" Intel Core Ultra 5 226V 16GB Memory 512GB SSD Win 11 Home', 899.99),
    ("9990003", "Dell", "Inspiron 14 2-in-1",
     'Dell Inspiron 14 2-in-1 Laptop, Intel Core Ultra 5 226V, 16GB, 512GB SSD, Windows 11 Home', 979.99),
]


def main() -> int:
    random.seed(7)
    DB.unlink(missing_ok=True)
    start = datetime.now(timezone.utc) - timedelta(days=4)

    with st.connect(DB) as conn:
        for sku, brand, model, title, base in FIXTURES:
            spec = parse_spec(title=title)
            st.upsert_product(conn, sku=sku, brand=brand, model_name=model,
                              listing_title=title, url=f"https://example.invalid/{sku}",
                              declared={}, normalized=spec.__dict__,
                              group_key=spec.group_key())
            price = base
            for step in range(16):  # 4 days x 4 snapshots
                ts = (start + timedelta(hours=6 * step)).isoformat(timespec="seconds")
                if step == 9:
                    price = round(base * 0.85, 2)      # a promo
                elif step == 13:
                    price = base
                else:
                    price = round(price + random.choice([0, 0, 0, -10, 10]), 2)
                prev = st.last_observation(conn, sku)
                obs_id = st.insert_observation(
                    conn, sku=sku, captured_at_utc=ts, price_usd=price,
                    regular_price_usd=base, on_sale=price < base, availability="orderable",
                    source_method="synthetic_selftest", source_url=None,
                    collector_version=COLLECTOR_VERSION, raw={"synthetic": True})
                for flag in check_price(price, previous=prev["price_usd"] if prev else None):
                    st.raise_flag(conn, observation_id=obs_id, sku=sku, rule=flag.rule,
                                  severity=flag.severity, detail=flag.detail)

    df = load_frame(DB)
    assert not df.empty, "self-test produced no rows"
    group = [g for g in df["group_key"].unique() if g != "unmatched"][0]
    assert df[df["group_key"] == group]["sku"].nunique() == 3, "grouping failed"

    out = Path("reports/SYNTHETIC_selftest_trend.png")
    plot_group(df, group, out, watermark="SYNTHETIC DATA")

    print(price_summary(df).to_string(index=False))
    print(f"\nGroup formed: {group}")
    print(f"Chart written to {out} (synthetic data, do not submit)")
    with st.connect(DB) as conn:
        n = conn.execute("SELECT COUNT(*) FROM review_flags").fetchone()[0]
    print(f"Review flags raised on synthetic data: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
