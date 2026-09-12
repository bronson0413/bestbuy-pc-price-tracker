"""Source interface. Every quote carries its own provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class SourceError(RuntimeError):
    """Raised when a source cannot produce a quote for a SKU."""


@dataclass
class Quote:
    sku: str
    price_usd: float | None
    regular_price_usd: float | None = None
    on_sale: bool | None = None
    availability: str | None = None
    listing_title: str | None = None
    source_method: str = "unknown"
    source_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PriceSource(Protocol):
    name: str

    def fetch(self, sku: str, url: str | None = None) -> Quote: ...
