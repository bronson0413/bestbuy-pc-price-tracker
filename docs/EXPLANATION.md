# AI-Powered PC Pricing Tracker — methodology

Lenovo AI Application Development Intern assessment (WD00102927)
Author: Bo-Sheng Chen · Retailer: Best Buy (US, online) · Currency: USD

> **Before submitting, replace every `[FILL IN]` below with your real values.**

## 1. How comparable products were identified

Price comparison is only meaningful between machines a buyer would genuinely
cross-shop, so equivalence is defined as a rule in code rather than a judgement
made per product. `src/tracker/normalize.py` reduces each listing to six
normalised attributes and joins them into an equivalence key:

```
intel-ultra5-226v | 16gb | 512gb | windows-11-home | laptop | convertible
   CPU              RAM     SSD      OS               device    form factor
```

Normalisation is what makes the key stable. `Intel Core Ultra 5 226V`,
`Core Ultra 5 226V` and `Intel(R) Core(TM) Ultra 5 226V` all reduce to
`intel-ultra5-226v`; `1TB SSD` and `1024GB SSD` both become `1024gb`. Two
listings are comparable if and only if their keys match exactly, which means a
16GB and a 32GB machine can never end up on the same chart by accident.

The parser is deliberately conservative. If any attribute cannot be read with
confidence the product is assigned to the `unmatched` bucket and excluded from
comparison rather than grouped on a guess. Excluding a product is a visible,
recoverable error; silently comparing two different machines is not.

The tracked group is `[FILL IN: your group key]`, containing `[FILL IN: N]`
products from `[FILL IN: brands]`.

## 2. Data source and collection approach

The primary source is the official Best Buy Products API
(`api.bestbuy.com/v1/products`). It is used in preference to scraping because it
is the retailer's sanctioned interface, it responds consistently regardless of
where the request originates, and it separates the sale price from the regular
price — a distinction a page scrape tends to collapse into a single number.

Two fallbacks exist so that a single blocked path does not create a gap in the
series:

1. **Product page extraction** via Playwright, trying JSON-LD structured data,
   then embedded JSON, then the rendered price element. The strategy that
   succeeded is stored alongside the price.
2. **Manual entry**, where a price is read off the live page by a person and
   recorded in `data/manual_prices.csv` with the entrant's name and a screenshot
   filename.

Every observation records which method produced it, so the provenance of any
point on the chart is recoverable. Manually entered points are visibly tagged as
such in the app rather than blended into the automated series.

Storage is an append-only SQLite table. Observations are never updated or
deleted; a correction is a new row. This keeps the audit trail intact and makes
the chart reproducible from the raw record.

Snapshots are captured every six hours by a scheduled GitHub Actions workflow,
which commits the updated database and regenerated charts back to the
repository. The timestamp written by the collector is authoritative, not the
cron schedule, because scheduled runs can be delayed under load.

## 3. Update logic

Each run iterates the configured products and attempts the source chain in
order, stopping at the first success. A failure on one product is logged and
raised as a flag; it never aborts the run for the others. Retries use
exponential backoff, and the API client self-throttles to stay inside the
documented per-key quota, treating HTTP 429 as a retryable condition.

Duplicate protection is a uniqueness constraint on
`(sku, captured_at_utc, source_method)`, so re-running the collector after a
partial failure cannot double-count a snapshot.

## 4. Assumptions

- The Best Buy online price for a new-condition unit is the figure of interest.
  Open-box offers, member pricing, tax, shipping and financing are out of scope.
- A SKU identifies a stable configuration over the observation window. Section 5
  describes what happens when that assumption breaks.
- Prices are USD from the US Best Buy storefront and are not adjusted for
  regional pricing.
- The six tracked attributes are sufficient to establish equivalence for this
  exercise. Display panel quality, battery capacity, chassis material and
  warranty terms materially affect value and are **not** captured, so the groups
  are equivalent on paper specifications only.

## 5. Limitations

**The observation window is short.** Collection began on `[FILL IN: date]`,
which yields roughly `[FILL IN: N]` snapshots per product. That is enough to
demonstrate that the tracker records change over time, but not enough to
characterise a promotional cycle or to support any claim about pricing trends.
A flat line over four days means the prices did not move during those four days
and nothing more. The system is built to accumulate; the dataset is young.

**Retail listings are not stable identifiers.** A SKU page can change its bundle
contents or configuration. This is detected rather than assumed away: the
listing title is re-parsed on every run and compared against the registered
configuration, and any divergence raises a critical `spec_drift` flag.

**Specification text is not a specification.** Parsing is driven by patterns for
current Intel, AMD and Qualcomm naming. A processor family outside those
patterns will not parse, and the product will drop into `unmatched` rather than
being mis-grouped.

**Page extraction is unreliable by design of the site.** Best Buy applies bot
mitigation, which typically blocks requests from datacenter addresses including
CI runners. The scraper detects a challenge response and reports the failure
instead of recording whatever the challenge page happened to contain.

**The comparison is specification-based, not value-based.** Two machines with
identical keys may differ substantially in build quality and support.

## 6. Error handling and validation

The tracker never auto-corrects a suspicious price. It records the observation,
raises a flag, and leaves the decision to a person. The rules encode failure
modes that actually occur on retail listings:

| Rule | Severity | What it catches |
| --- | --- | --- |
| `price_missing` / `price_nonpositive` | critical | Source returned nothing usable |
| `price_below_floor` | critical | An accessory or placeholder price rendered in place of the PC |
| `price_jump_critical` (±40%) | critical | Open-box price substituted for new, or a different configuration |
| `price_jump_warning` (±15%) | warning | A promotion worth confirming |
| `spec_drift` | critical | The live listing no longer matches the registered configuration |
| `spec_incomplete` | warning | Attributes could not be parsed; product excluded from comparison |
| `group_too_small` | warning | Fewer than two products in a group, so no comparison exists |
| `data_stale` | warning | No successful capture in over 24 hours |
| `collection_failed` | critical | Every source in the chain failed for this product |

Open flags are surfaced in the Streamlit app and exported to
`reports/review_queue.csv`. Resolution is recorded against the flag with the
name of the person who adjudicated it.

The equivalence and validation rules are covered by 18 unit tests
(`pytest -q`), including the case that matters most: three listings from three
different brands, written in three different house styles, must produce one
identical group key — and a storage change must split them.
