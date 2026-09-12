"""Primary source: official Best Buy Products API (developer.bestbuy.com).

Preferred over scraping because it is the retailer's own sanctioned interface,
it is stable from any IP (including CI runners), and it returns the regular /
sale price split that a naive page scrape collapses into one number.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from .base import PriceSource, Quote, SourceError

ENDPOINT = "https://api.bestbuy.com/v1/products(sku={sku})"
FIELDS = ",".join(
    [
        "sku",
        "name",
        "salePrice",
        "regularPrice",
        "onSale",
        "orderable",
        "url",
        "manufacturer",
        "modelNumber",
    ]
)


class BestBuyApiSource(PriceSource):
    name = "bestbuy_products_api"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: int = 20,
        max_retries: int = 3,
        min_interval_s: float = 1.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("BESTBUY_API_KEY", "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval_s = min_interval_s  # respect the documented QPS cap
        self._last_call = 0.0

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _throttle(self) -> None:
        delta = time.monotonic() - self._last_call
        if delta < self.min_interval_s:
            time.sleep(self.min_interval_s - delta)
        self._last_call = time.monotonic()

    def fetch(self, sku: str, url: str | None = None) -> Quote:
        if not self.available:
            raise SourceError("BESTBUY_API_KEY is not set")
        params = {"apiKey": self.api_key, "format": "json", "show": FIELDS}
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = requests.get(
                    ENDPOINT.format(sku=sku), params=params, timeout=self.timeout
                )
                if resp.status_code == 429:  # documented per-key quota
                    time.sleep(2**attempt * 2)
                    last_error = SourceError("rate limited (429)")
                    continue
                resp.raise_for_status()
                payload: dict[str, Any] = resp.json()
                products = payload.get("products") or []
                if not products:
                    raise SourceError(f"sku {sku} not found in API response")
                p = products[0]
                return Quote(
                    sku=str(p.get("sku", sku)),
                    price_usd=_as_float(p.get("salePrice")),
                    regular_price_usd=_as_float(p.get("regularPrice")),
                    on_sale=p.get("onSale"),
                    availability="orderable"
                    if p.get("orderable") in ("Available", True)
                    else str(p.get("orderable")),
                    listing_title=p.get("name"),
                    source_method=self.name,
                    source_url=p.get("url") or url,
                    raw=p,
                )
            except SourceError:
                raise
            except Exception as exc:  # network, JSON, HTTP
                last_error = exc
                time.sleep(2**attempt)
        raise SourceError(f"API fetch failed for sku {sku}: {last_error}")


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
