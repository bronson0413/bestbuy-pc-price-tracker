"""Last-resort source: human-entered prices read off the live page.

Kept as a first-class source rather than an ad-hoc spreadsheet so that manually
captured points sit in the same table as automated ones, tagged as manual and
carrying the name of the person who entered them.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .base import PriceSource, Quote, SourceError


class ManualSource(PriceSource):
    name = "manual_entry"

    def __init__(self, csv_path: Path | str = "data/manual_prices.csv") -> None:
        self.csv_path = Path(csv_path)

    def load(self) -> list[dict[str, str]]:
        """Read the manual log, tolerating the way desktop tools write CSV.

        Windows PowerShell and Excel both write UTF-8 with a byte-order mark,
        which would otherwise turn the first column name into "\ufeffsku" and
        silently hide every row. utf-8-sig strips it; keys and values are
        trimmed so a stray space does not lose a price either.
        """
        if not self.csv_path.exists():
            return []
        with self.csv_path.open(newline="", encoding="utf-8-sig") as fh:
            rows = []
            for raw in csv.DictReader(fh):
                row = {
                    (k or "").strip(): (v or "").strip()
                    for k, v in raw.items()
                    if k is not None
                }
                if row.get("sku"):
                    rows.append(row)
            return rows

    def history(self, sku):
        rows = sorted(
            (r for r in self.load() if r.get("sku") == sku),
            key=lambda r: r.get("captured_at_utc", ""),
        )
        quotes = []
        for row in rows:
            try:
                price = float(row["price_usd"])
            except (KeyError, ValueError):
                continue
            quotes.append(
                Quote(
                    sku=sku,
                    price_usd=price,
                    source_method=self.name,
                    availability=row.get("availability"),
                    raw={
                        "entered_by": row.get("entered_by", "unknown"),
                        "screenshot": row.get("screenshot", ""),
                        "captured_at_utc": row.get("captured_at_utc", ""),
                    },
                )
            )
        return quotes

    def fetch(self, sku: str, url: str | None = None) -> Quote:
        rows = [r for r in self.load() if r.get("sku") == sku]
        if not rows:
            raise SourceError(f"no manual entry for sku {sku}")
        row = sorted(rows, key=lambda r: r.get("captured_at_utc", ""))[-1]
        try:
            price = float(row["price_usd"])
        except (KeyError, ValueError) as exc:
            raise SourceError(f"malformed manual row for sku {sku}") from exc
        return Quote(
            sku=sku,
            price_usd=price,
            source_method=self.name,
            source_url=url,
            availability=row.get("availability"),
            raw={
                "entered_by": row.get("entered_by", "unknown"),
                "screenshot": row.get("screenshot", ""),
                "captured_at_utc": row.get("captured_at_utc", ""),
            },
        )
