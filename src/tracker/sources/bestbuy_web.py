"""Fallback source: render the product page and read its structured data.

Used only when the official API is unavailable. Three extraction strategies are
tried in order and the one that succeeded is recorded on the quote, so a price
can always be traced back to how it was obtained. Bot mitigation on the site
means this path is expected to fail from datacenter IPs (including CI runners);
that failure is reported, never silently substituted.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from .base import PriceSource, Quote, SourceError

_PRICE_ATTR = re.compile(
    r'data-testid="customer-price"[^>]*>.*?\$([\d,]+\.\d{2})', re.S)


class BestBuyWebSource(PriceSource):
    name = "bestbuy_web"

    # Best Buy's edge terminates HTTP/2 connections from headless Chromium,
    # surfacing as ERR_HTTP2_PROTOCOL_ERROR before any page content loads.
    # Forcing HTTP/1.1 and removing the automation fingerprint gets past it.
    LAUNCH_ARGS = [
        "--disable-http2",
        "--disable-quic",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--no-sandbox",
    ]

    STEALTH_JS = """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        window.chrome = {runtime: {}};
    """

    def __init__(self, *, headless: bool | None = None, timeout_ms: int = 60_000,
                 locale: str = "en-US", attempts: int = 2) -> None:
        env = os.environ.get("TRACKER_HEADFUL", "").strip().lower()
        self.headless = (env not in ("1", "true", "yes")) if headless is None else headless
        self.timeout_ms = timeout_ms
        self.locale = locale
        self.attempts = attempts

    def fetch(self, sku: str, url: str | None = None) -> Quote:
        if not url:
            raise SourceError(f"sku {sku} has no product URL for web fallback")
        try:
            import playwright  # noqa: F401
        except ImportError as exc:
            raise SourceError("playwright is not installed") from exc

        html = title = None
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            try:
                html, title = self._render(url)
                break
            except Exception as exc:
                last_error = exc
                time.sleep(3 * (attempt + 1))
        if html is None:
            raise SourceError(f"could not load page for sku {sku}: {last_error}")

        if _looks_blocked(html, title):
            raise SourceError("blocked by bot mitigation (challenge page returned)")

        for strategy, extractor in (
            ("jsonld", _from_jsonld),
            ("embedded_json", _from_embedded_json),
            ("dom_price", _from_dom),
        ):
            result = extractor(html)
            if result and result.get("price") is not None:
                return Quote(
                    sku=sku,
                    price_usd=result["price"],
                    regular_price_usd=result.get("regular_price"),
                    on_sale=None,
                    availability=result.get("availability"),
                    listing_title=result.get("title") or title,
                    source_method=f"{self.name}:{strategy}",
                    source_url=url,
                    raw={"extraction_strategy": strategy, "page_title": title},
                )
        raise SourceError(f"no price found on page for sku {sku}")


    def _render(self, url: str) -> tuple[str, str]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless, args=self.LAUNCH_ARGS)
            ctx = browser.new_context(
                locale=self.locale,
                timezone_id="America/Chicago",
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/151.0.0.0 Safari/537.36"),
                viewport={"width": 1440, "height": 900},
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                               "image/avif,image/webp,*/*;q=0.8"),
                    "Upgrade-Insecure-Requests": "1",
                },
            )
            ctx.add_init_script(self.STEALTH_JS)
            page = ctx.new_page()
            try:
                page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                return page.content(), page.title()
            finally:
                ctx.close()
                browser.close()


def _looks_blocked(html: str, title: str) -> bool:
    needles = ("access denied", "are you a robot", "unusual traffic",
               "reference #", "pardon the interruption")
    haystack = f"{title} {html[:4000]}".lower()
    return any(n in haystack for n in needles)


def _from_jsonld(html: str) -> dict[str, Any] | None:
    for block in re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in _iter_nodes(data):
            if not isinstance(node, dict):
                continue
            if node.get("@type") not in ("Product", ["Product"]):
                continue
            offers = node.get("offers")
            offer = offers[0] if isinstance(offers, list) and offers else offers
            if not isinstance(offer, dict):
                continue
            price = _to_float(offer.get("price") or offer.get("lowPrice"))
            if price is None:
                continue
            return {"price": price, "title": node.get("name"),
                    "availability": offer.get("availability")}
    return None


def _from_embedded_json(html: str) -> dict[str, Any] | None:
    for m in re.finditer(r'"currentPrice"\s*:\s*([\d.]+)', html):
        price = _to_float(m.group(1))
        if price:
            regular = None
            if rm := re.search(r'"regularPrice"\s*:\s*([\d.]+)', html):
                regular = _to_float(rm.group(1))
            return {"price": price, "regular_price": regular}
    return None


def _from_dom(html: str) -> dict[str, Any] | None:
    if m := _PRICE_ATTR.search(html):
        return {"price": _to_float(m.group(1).replace(",", ""))}
    return None


def _iter_nodes(data: Any):
    if isinstance(data, list):
        for item in data:
            yield from _iter_nodes(item)
    elif isinstance(data, dict):
        yield data
        for value in data.values():
            if isinstance(value, (list, dict)):
                yield from _iter_nodes(value)


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None
