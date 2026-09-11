# Best Buy PC Pricing Tracker

Tracks the online price of directly comparable Windows PCs on Best Buy, stores a
dated snapshot on every run, and charts how the prices move against each other.

Built for the Lenovo AI Application Development Intern assessment (WD00102927).

- `docs/EXPLANATION.md` — methodology, assumptions, limitations
- `docs/AI_USAGE.md` — AI tool usage and human validation record

## What it does

Products are only compared when a deterministic rule says they are equivalent.
`src/tracker/normalize.py` reduces a listing to a six-part key:

```
intel-ultra5-226v | 16gb | 512gb | windows-11-home | laptop | convertible
```

Two listings with the same key are comparable; anything the parser cannot read
with confidence falls into `unmatched` and is excluded rather than guessed at.

Each run writes one append-only row per product. Observations are never
overwritten, so the chart can always be traced back to what was actually seen
and when, and by which collection method.

Suspicious values are flagged for a person, never silently fixed. See the review
queue in the app and `reports/review_queue.csv`.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH=src
```

Get a Best Buy Products API key at <https://developer.bestbuy.com/> and set it:

```bash
export BESTBUY_API_KEY=your_key_here
```

Or use the RapidAPI aggregator, which needs no retailer approval:

```bash
export RAPIDAPI_KEY=your_key_here
```

Then fill in `config/products.yaml` with real SKUs and listing titles. The file
has instructions at the top.

## Run

```bash
python -m tracker.collect              # capture one snapshot per product
python -m tracker.collect --dry-run    # fetch and print, write nothing
python -m tracker.collect --source rapidapi  # force one source
python -m tracker.report               # write charts + CSV into reports/
streamlit run app.py                   # interactive tracker
```

Verify the pipeline before real data exists:

```bash
pytest -q            # 18 tests over the equivalence and validation rules
python tools/selftest.py   # end-to-end run on synthetic, watermarked data
```

## Collection sources

The collector tries three sources in order and records which one produced each
price.

| Order | Source | When it is used |
| --- | --- | --- |
| 1 | Best Buy Products API | Preferred: the retailer's own interface. Needs a key from developer.bestbuy.com. |
| 2 | RapidAPI aggregator | Reachable from any IP including CI. Second-hand data, so the SKU echoed back is verified against the SKU requested. |
| 3 | Product page extraction (Playwright) | Tries JSON-LD, then embedded JSON, then the rendered price node. Blocked by bot mitigation in practice. |
| 4 | Manual entry (`data/manual_prices.csv`) | Everything automated is blocked. Human reads the price off the page and records a screenshot filename. |

Scheduled capture runs every 6 hours through `.github/workflows/track.yml` and
commits the updated database and charts back to the repository.

## Layout

```
config/products.yaml          tracked listings and their declared configuration
src/tracker/normalize.py      spec parsing and equivalence keys
src/tracker/sources/          API, web-scrape and manual price sources
src/tracker/validate.py       the checks that decide what a human must review
src/tracker/storage.py        append-only SQLite schema
src/tracker/collect.py        collection CLI
src/tracker/report.py         chart and CSV generation
app.py                        Streamlit tracker
tools/selftest.py             synthetic end-to-end check
```
