"""One pass through the whole pipeline: config in, stored observations out."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from tracker import storage as st
from tracker.collect import main, run
from tracker.report import load_frame, price_summary

CONFIG = """
meta: {retailer: Best Buy, currency: USD}
products:
  - sku: "6603654"
    brand: ASUS
    model_name: ExpertBook P5
    title: "ASUS - ExpertBook P5 Laptop - Intel Core Ultra 5 226V with 16GB Memory - 512GB SSD"
    declared: {os: Windows 11 Pro, form_factor: clamshell}
  - sku: "12251856"
    brand: HP
    model_name: EliteBook 16
    title: "HP - EliteBook 16\\" Laptop - Intel Core Ultra 5 226V with 16GB Memory - 512GB SSD"
    declared: {os: Windows 11 Pro, form_factor: clamshell}
  - sku: "99999999"
    brand: Mystery
    model_name: Unknown Machine
    title: "Mystery - A Laptop"
    declared: {}
"""

PRICES = (
    "sku,captured_at_utc,price_usd,availability,entered_by,screenshot\n"
    "6603654,2026-09-11T10:00:00+00:00,1199.99,orderable,Tester,a.png\n"
    "12251856,2026-09-11T10:00:00+00:00,1678.49,orderable,Tester,b.png\n"
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    (data / "manual_prices.csv").write_text(PRICES, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_a_full_run_stores_what_it_found_and_flags_what_it_did_not(
    workspace: Path,
) -> None:
    db = workspace / "prices.sqlite"
    assert run(workspace / "config.yaml", db, "manual") == 0

    df = load_frame(db)
    assert len(df) == 2  # two priced, one unavailable
    assert set(df["sku"]) == {"6603654", "12251856"}

    with st.connect(db) as conn:
        flags = {
            r["rule"]
            for r in conn.execute("SELECT rule FROM review_flags WHERE sku='99999999'")
        }
        # The unparseable product is excluded AND the exclusion is visible.
        assert "spec_incomplete" in flags
        assert "collection_failed" in flags

        run_row = conn.execute("SELECT * FROM collection_runs").fetchone()
        assert (run_row["attempted"], run_row["succeeded"], run_row["failed"]) == (
            3,
            2,
            1,
        )


def test_the_two_comparable_products_land_in_one_group(workspace: Path) -> None:
    db = workspace / "prices.sqlite"
    run(workspace / "config.yaml", db, "manual")
    df = load_frame(db)

    groups = set(df["group_key"])
    assert len(groups) == 1  # ASUS and HP are comparable
    summary = price_summary(df)
    spread = summary["latest_usd"].max() - summary["latest_usd"].min()
    assert spread == pytest.approx(478.50)  # the finding the tracker exists for


def test_a_dry_run_reports_without_writing(workspace: Path) -> None:
    db = workspace / "prices.sqlite"
    assert run(workspace / "config.yaml", db, "manual", dry_run=True) == 0
    assert load_frame(db).empty  # nothing persisted


def test_rerunning_does_not_duplicate_the_same_snapshot(workspace: Path) -> None:
    db = workspace / "prices.sqlite"
    run(workspace / "config.yaml", db, "manual")
    run(workspace / "config.yaml", db, "manual")
    assert len(load_frame(db)) == 2  # same timestamps, ignored


def test_a_title_without_a_device_word_is_excluded_not_guessed(workspace: Path) -> None:
    """A known limitation, pinned so a future change cannot hide it.

    Device type is read from words like "Laptop" or "Desktop". Retail titles
    almost always carry one, but when none is present the parser declines to
    infer it and the product is excluded rather than assumed to be a laptop.
    """
    cfg = workspace / "no-device-word.yaml"
    cfg.write_text(
        "products:\n"
        '  - sku: "6603654"\n'
        "    brand: ASUS\n"
        "    model_name: ExpertBook P5\n"
        '    title: "ASUS ExpertBook P5 - Intel Core Ultra 5 226V - 16GB - 512GB SSD"\n'
        "    declared: {os: Windows 11 Pro, form_factor: clamshell}\n",
        encoding="utf-8",
    )
    db = workspace / "limit.sqlite"
    run(cfg, db, "manual")
    assert set(load_frame(db)["group_key"]) == {"unmatched"}


def test_the_cli_exits_nonzero_when_nothing_could_be_collected(workspace: Path) -> None:
    empty = workspace / "empty.yaml"
    empty.write_text("products: []\n", encoding="utf-8")
    assert main(["--config", str(empty), "--db", str(workspace / "x.sqlite")]) == 2
