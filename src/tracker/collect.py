"""Collection entry point: one run = one dated snapshot per tracked product."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from . import COLLECTOR_VERSION
from . import storage as st
from .normalize import parse_spec
from .sources import (BestBuyApiSource, BestBuyWebSource, ManualSource,
                      Quote, RapidApiSource, SourceError)
from .validate import check_completeness, check_price, check_spec_drift


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def build_sources(mode: str) -> list:
    """Order the chain by how directly each source speaks for the retailer.

    Official API first when a key exists, then the RapidAPI aggregator, then
    page extraction, then human entry. Each step is further from the source of
    truth than the last, so the collector only descends when it has to.
    """
    api = BestBuyApiSource()
    rapid = RapidApiSource()
    web, manual = BestBuyWebSource(), ManualSource()
    preferred = [s for s in (api, rapid) if s.available] + [web, manual]
    chains = {
        "auto": preferred,
        "api": [api],
        "rapidapi": [rapid],
        "web": [web],
        "manual": [manual],
    }
    if mode not in chains:
        raise SystemExit(f"unknown --source {mode}")
    return chains[mode]


def collect_one(sources: list, sku: str, url: str | None) -> tuple[Quote | None, list[str]]:
    """Try each source in order. Returns the first quote plus the failure log."""
    errors: list[str] = []
    for source in sources:
        try:
            return source.fetch(sku, url), errors
        except SourceError as exc:
            errors.append(f"{source.name}: {exc}")
        except Exception as exc:  # never let one product kill the run
            errors.append(f"{source.name}: unexpected {type(exc).__name__}: {exc}")
    return None, errors


def run(config_path: Path, db_path: Path, mode: str, dry_run: bool = False) -> int:
    cfg = load_config(config_path)
    products = cfg.get("products", [])
    if not products:
        print("No products configured. Fill in config/products.yaml first.", file=sys.stderr)
        return 2

    sources = build_sources(mode)
    print(f"Source chain: {' -> '.join(s.name for s in sources)}")
    attempted = succeeded = failed = 0

    with st.connect(db_path) as conn:
        run_id = st.start_run(conn, "->".join(s.name for s in sources))
        for entry in products:
            attempted += 1
            sku = str(entry["sku"])
            declared = entry.get("declared", {})
            spec = parse_spec(
                title=entry.get("title"), cpu=declared.get("cpu"),
                ram=declared.get("ram"), storage=declared.get("storage"),
                os_=declared.get("os"), device_type=declared.get("device_type"),
                form_factor=declared.get("form_factor"))
            group_key = spec.group_key()
            tier_key = spec.tier_key()

            quote, errors = collect_one(sources, sku, entry.get("url"))
            st.upsert_product(
                conn, sku=sku, brand=entry.get("brand", "?"),
                model_name=entry.get("model_name", entry.get("title", sku)),
                listing_title=(quote.listing_title if quote else entry.get("title")),
                url=entry.get("url"), declared=declared,
                normalized=spec.__dict__, group_key=group_key,
                tier_key=tier_key)

            for flag in check_completeness(spec):
                st.raise_flag(conn, observation_id=None, sku=sku, rule=flag.rule,
                              severity=flag.severity, detail=flag.detail)

            if quote is None:
                failed += 1
                print(f"  [FAIL] {sku}: " + " | ".join(errors))
                st.raise_flag(conn, observation_id=None, sku=sku,
                              rule="collection_failed", severity="critical",
                              detail=" | ".join(errors) or "all sources failed")
                continue

            previous = st.last_observation(conn, sku)
            prev_price = previous["price_usd"] if previous else None
            captured = quote.raw.get("captured_at_utc") or st.utcnow()

            if dry_run:
                print(f"  [DRY] {sku} ${quote.price_usd} via {quote.source_method}")
                succeeded += 1
                continue

            obs_id = st.insert_observation(
                conn, sku=sku, captured_at_utc=captured, price_usd=quote.price_usd,
                regular_price_usd=quote.regular_price_usd, on_sale=quote.on_sale,
                availability=quote.availability, source_method=quote.source_method,
                source_url=quote.source_url, collector_version=COLLECTOR_VERSION,
                raw=quote.raw)

            flags = check_price(quote.price_usd, previous=prev_price)
            flags += check_spec_drift(spec, quote.listing_title)
            for flag in flags:
                st.raise_flag(conn, observation_id=obs_id, sku=sku, rule=flag.rule,
                              severity=flag.severity, detail=flag.detail)

            succeeded += 1
            marks = "".join("!" for f in flags if f.severity == "critical")
            print(f"  [ OK ] {sku} ${quote.price_usd:.2f} via {quote.source_method} "
                  f"[{group_key}] {marks}")

        st.finish_run(conn, run_id, attempted=attempted, succeeded=succeeded,
                      failed=failed)

    print(f"\nRun complete: {succeeded}/{attempted} captured, {failed} failed.")
    return 0 if succeeded else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Capture one Best Buy price snapshot per product.")
    ap.add_argument("--config", type=Path, default=Path("config/products.yaml"))
    ap.add_argument("--db", type=Path, default=st.DEFAULT_DB)
    ap.add_argument("--source", default="auto", choices=["auto", "api", "rapidapi", "web", "manual"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    return run(args.config, args.db, args.source, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
