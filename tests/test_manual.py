"""Manual entries are a first-class source, so they get first-class parsing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from tracker.sources import ManualSource, SourceError

HEADER = "sku,captured_at_utc,price_usd,availability,entered_by,screenshot\n"
ROW = "6603654,2026-09-11T10:00:00+00:00,1199.99,orderable,Bo-Sheng Chen,a.png\n"


def _csv(tmp_path: Path, text: str, *, bom: bool = False) -> ManualSource:
    path = tmp_path / "manual.csv"
    path.write_text(text, encoding="utf-8-sig" if bom else "utf-8")
    return ManualSource(path)


def test_reads_a_plain_utf8_file(tmp_path: Path) -> None:
    q = _csv(tmp_path, HEADER + ROW).fetch("6603654")
    assert q.price_usd == 1199.99
    assert q.source_method == "manual_entry"
    assert q.raw["entered_by"] == "Bo-Sheng Chen"
    assert q.raw["screenshot"] == "a.png"  # the evidence trail survives


def test_reads_a_file_written_with_a_byte_order_mark(tmp_path: Path) -> None:
    # PowerShell and Excel both write UTF-8 with a BOM; without handling it the
    # first column name becomes "\ufeffsku" and every row silently disappears.
    q = _csv(tmp_path, HEADER + ROW, bom=True).fetch("6603654")
    assert q.price_usd == 1199.99


def test_tolerates_stray_whitespace_around_values(tmp_path: Path) -> None:
    padded = "6603654 , 2026-09-11T10:00:00+00:00 , 1199.99 ,orderable,Me,a.png\n"
    assert _csv(tmp_path, HEADER + padded).fetch("6603654").price_usd == 1199.99


def test_uses_the_most_recent_entry_for_a_sku(tmp_path: Path) -> None:
    later = "6603654,2026-09-12T10:00:00+00:00,1099.99,orderable,Me,b.png\n"
    q = _csv(tmp_path, HEADER + ROW + later).fetch("6603654")
    assert q.price_usd == 1099.99
    assert q.raw["captured_at_utc"] == "2026-09-12T10:00:00+00:00"


def test_a_missing_file_is_reported_not_treated_as_no_price(tmp_path: Path) -> None:
    source = ManualSource(tmp_path / "absent.csv")
    assert source.load() == []
    with pytest.raises(SourceError, match="no manual entry"):
        source.fetch("6603654")


def test_a_malformed_price_raises_rather_than_guessing(tmp_path: Path) -> None:
    bad = "6603654,2026-09-11T10:00:00+00:00,about a thousand,,Me,a.png\n"
    with pytest.raises(SourceError, match="malformed"):
        _csv(tmp_path, HEADER + bad).fetch("6603654")


def test_rows_for_other_skus_are_ignored(tmp_path: Path) -> None:
    other = "9999999,2026-09-11T10:00:00+00:00,1.00,,Me,x.png\n"
    with pytest.raises(SourceError):
        _csv(tmp_path, HEADER + other).fetch("6603654")
