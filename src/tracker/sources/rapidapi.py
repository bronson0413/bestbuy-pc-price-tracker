"""Third-party source: Best Buy pricing via a RapidAPI aggregator.

Used because the retailer's own channels are closed to this project: the
official Products API requires a non-free email domain to register, and direct
page collection is refused by the site's bot mitigation. This aggregator proxies
the same pricing data and is reachable from any IP, including CI runners.

The trade-off is recorded rather than hidden: prices arrive second-hand, so the
collector verifies that the SKU echoed back matches the SKU requested, and every
observation is tagged with this source so a reviewer can tell which numbers came
through an intermediary.
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

from .base import PriceSource, Quote, SourceError

HOST = "bestbuy-usa.p.rapidapi.com"
ENDPOINT = f"https://{HOST}/product/price"


class RapidApiSource(PriceSource):
    name = "rapidapi_bestbuy"

    def __init__(self, api_key: str | None = None, *, timeout: int = 20,
                 max_retries: int = 3, min_interval_s: float = 1.5) -> None:
        self.api_key = api_key or os.environ.get("RAPIDAPI_KEY", "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval_s = min_interval_s  # free tier is rate limited
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
            raise SourceError("RAPIDAPI_KEY is not set")

        headers = {
            "x-rapidapi-host": HOST,
            "x-rapidapi-key": self.api_key,
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = requests.get(ENDPOINT, params={"sku": sku},
                                    headers=headers, timeout=self.timeout)
                if resp.status_code == 429:
                    time.sleep(2 ** attempt * 3)
                    last_error = SourceError("rate limited (429)")
                    continue
                if resp.status_code in (401, 403):
                    raise SourceError(
                        f"rejected with HTTP {resp.status_code}; check the key "
                        "and that the plan is subscribed")
                resp.raise_for_status()
                payload: dict[str, Any] = resp.json()
                return self._to_quote(sku, payload, url)
            except SourceError:
                raise
            except Exception as exc:
                last_error = exc
                time.sleep(2 ** attempt)

        raise SourceError(f"fetch failed for sku {sku}: {last_error}")

    def _to_quote(self, sku: str, payload: dict[str, Any], url: str | None) -> Quote:
        if not payload.get("success"):
            raise SourceError(f"API reported failure for sku {sku}: "
                              f"{payload.get('message') or payload.get('error')}")
        data = payload.get("data") or {}
        if not data:
            raise SourceError(f"empty payload for sku {sku}")

        analytics = data.get("skuDataAnalytics") or {}
        returned_sku = str(analytics.get("skuId") or "")
        if returned_sku and returned_sku != str(sku):
            # The aggregator is an intermediary; never trust it to have answered
            # the question that was actually asked.
            raise SourceError(
                f"sku mismatch: requested {sku}, received {returned_sku}")

        price = _as_float(data.get("customerPrice"))
        if price is None:
            price = _as_float(analytics.get("customerPrice"))

        # Discounted items carry a numeric regularPrice in the analytics block;
        # everything else only has previousPrice as a formatted string.
        regular = _as_float(analytics.get("regularPrice"))
        if regular is None:
            regular = _as_float(data.get("previousPrice"))

        return Quote(
            sku=str(sku),
            price_usd=price,
            regular_price_usd=regular,
            on_sale=data.get("isOnSale"),
            availability=analytics.get("pricingType"),
            listing_title=None,  # this endpoint returns pricing only
            source_method=self.name,
            source_url=url,
            raw={k: data.get(k) for k in
                 ("customerPrice", "previousPrice", "isOnSale", "hasSavings",
                  "priceChangeTotalSavingsAmount", "skuDataAnalytics")},
        )


def _as_float(value: Any) -> float | None:
    """Parse a price that may arrive as a number or a formatted string."""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
