# Best Buy PC Pricing Tracker

[![CI](https://github.com/bronson0413/bestbuy-pc-price-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/bronson0413/bestbuy-pc-price-tracker/actions/workflows/ci.yml)

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

## Language-model components

The model reads what the rules cannot: two tracked listings state their
processor only in the description, and one describes a 360-degree hinge in prose
while its title says "Laptop Computer". Regular expressions cannot reach that
text; a model can.

It is constrained rather than trusted:

- every field it returns is re-normalised through `normalize.py`, so it cannot
  introduce a token the deterministic layer would not have produced
- where the rules already read a value, a conflicting model value never wins --
  the disagreement is raised as a critical flag instead
- low-confidence output is discarded before comparison

### Tool calling

The application includes an assistant that answers questions about the tracked
data. It does not generate numbers: it selects from five query functions
(`list_products`, `compare_group`, `price_history`, `find_movers`,
`open_flags`), and every argument is schema-validated before anything touches
the database. A malformed call is returned to the model as an error rather than
repaired, and the tool calls are displayed alongside the answer so a reader can
check the query that produced it. An answer reached without a successful query
is labelled unverified.

### Retrieval-augmented answering

Price questions decompose into queries; product questions do not. "Which of
these has a 360-degree hinge" is answered from prose, so those questions go
through retrieval instead: passages are selected from
`config/descriptions.yaml`, and the model is told it may use nothing else.

Two properties are enforced in code rather than asked for in the prompt:

- when retrieval finds no relevant passage, **the model is not called at all** —
  the cheapest place to refuse is before generation
- citations in the answer are checked against the passage ids actually
  supplied; an invented id is reported rather than shown as a source

Retrieval is BM25, not embeddings. Six products yield twenty-three passages; a
vector store for that would be architecture as decoration, and lexical matching
has the advantage of showing *which term* matched. The trade-off is real and is
pinned by a test: BM25 cannot distinguish "Wi-Fi 7" from "Wi-Fi 6E", because
both share the token `wi-fi`. Swapping the scorer for embeddings touches one
class.

### Measure it before believing it

```bash
export GEMINI_API_KEY=your_key_here
python tools/run_eval.py --llm     # scores rules, model and hybrid on evals/goldset.yaml
python tools/run_eval.py           # rules baseline only, no API calls
```

`evals/goldset.yaml` is hand-verified against live product pages and includes
trap cases where the correct answer is "not stated" — inventing a plausible
value there is scored as a hallucination, not as a miss.

### Flag explanation

A flag reading `price_jump_critical: -42%` is accurate and nearly useless — a
reviewer still has to work out what happened. The explainer drafts the first
five minutes of that investigation: it is given the flag, the registered
configuration, the current listing title and the price history, and proposes
what may have occurred together with what would confirm or refute each
possibility.

It resolves nothing. The flag's status is untouched, the output is labelled as
model-generated, and malformed hypotheses are dropped rather than repaired.

## Collection health

`src/tracker/health.py` reports what the tracker knows about its own
reliability: which sources produced which observations, the success rate of
each run, and — grouped by cause rather than by product — why collection
failed. Error strings carry SKUs and timings that differ every time, so they
are collapsed to their cause; that is what turns a list of failures into a
diagnosis.

The failure record from this project's own degradation path is retained rather
than cleared, because it is the evidence for the four-tier source design.

## Quality checks

```bash
pip install -r requirements-dev.txt
pre-commit install          # lint, format and type-check before every commit

ruff check src tests tools  # lint
ruff format src tests tools # format
mypy src                    # type check
pytest --cov                # 138 tests, 79% line coverage
python tools/check_grouping.py   # the configured products still group correctly
python tools/selftest.py    # end-to-end run on synthetic, watermarked data
```

CI runs all of the above on every push. The snapshot schedule is a separate
workflow so that a failing test never interrupts price collection, and a
collection outage never looks like a broken build.

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
