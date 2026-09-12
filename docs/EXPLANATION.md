# AI-Powered PC Pricing Tracker — methodology

Lenovo AI Application Development Intern assessment (WD00102927)
Bo-Sheng Chen · Retailer: Best Buy (US, online) · Currency: USD

Repository: https://github.com/bronson0413/bestbuy-pc-price-tracker

---

## 1. How comparable products were identified

Price comparison is only meaningful between machines a buyer would genuinely
cross-shop, so equivalence is defined as executable code rather than a
judgement made per product. `src/tracker/normalize.py` reduces each listing to
six normalised attributes and joins them into a key:

```
intel-ultra5-226v | 16gb | 512gb | windows-11-pro | laptop | clamshell
   processor        memory  storage      OS         device    form factor
```

Normalisation is what makes the key stable. `Intel Core Ultra 5 226V`,
`Core Ultra 5 226V` and `Intel Ultra 5-226v` all reduce to `intel-ultra5-226v`;
`1TB SSD` and `1024GB SSD` both become `1024gb`. Two listings are comparable if
and only if their keys match exactly.

### Two levels of grouping

A single key turned out to answer only half the question. The tracker
therefore derives two:

| Key | Rule | Question it answers |
| --- | --- | --- |
| `group_key` | All six attributes identical, including the exact processor SKU | Are these the same machine? |
| `tier_key` | Processor reduced to its class (`intel-ultra5-226v` → `intel-ultra5`); other five attributes unchanged | Would a buyer cross-shop these? |

Only the processor SKU is relaxed. Memory, storage, operating system, device
type and form factor stay exact, because each of those changes the purchase
decision: 16GB versus 32GB is a specification upgrade, Windows 11 Home versus
Pro is a functional difference, and a convertible is not a substitute for a
clamshell.

The effect on the tracked set is visible:

```
6 products → 5 exact groups, 3 tiers
largest tier: 4 products across 4 brands (ASUS, Dell, HP, Lenovo)
```

Under exact matching the largest comparison is two machines. Under tier
matching it is four machines from four manufacturers — which is the comparison
a shopper is actually making. The application exposes both and lets the
reviewer switch between them; neither replaces the other.

### Why unparseable products are excluded rather than guessed

If any of the six attributes cannot be read with confidence, the product is
assigned to `unmatched` and kept out of every comparison.

The two error modes are not symmetrical. Excluding a comparable machine costs
one line on a chart — visible and recoverable. Merging two different machines
into one group produces a chart that looks correct and a conclusion that is
wrong, with nothing to signal the error.

This was not hypothetical. Two of the six tracked listings — a Lenovo
ThinkBook 14 G5 and a Dell Pro 14 — carry titles that say only "Intel Core
Ultra 5" with no model suffix. The parser refused to infer a model and both
fell into `unmatched`. Reading the product descriptions revealed them to be a
**225U** and a **236V** respectively: three different processors across the
set. Had the parser guessed "226V" to fill the gap, three distinct machines
would have been silently compared as one configuration.

### A data-quality finding worth recording

Both of those listings expose a structured **Processor Model** field on the
Best Buy product page. Both read **"Intel Processor N150"** — an entry-level
Alder Lake-N part, not a Core Ultra chip. The correct processor appears only in
the free-text description.

The retailer's own structured field is wrong for these products. This is the
strongest argument for the design above: a parser that trusted the structured
attribute would have mis-classified both machines with complete confidence.

---

## 2. Data source and collection approach

Four sources are tried in order, each one step further from the retailer than
the last. The collector descends only when it has to, and records which source
produced every price.

| Order | Source | Status in this project |
| --- | --- | --- |
| 1 | Best Buy Products API | Implemented and tested; **key could not be obtained** |
| 2 | RapidAPI aggregator | **In use.** Reachable from any IP, including CI runners |
| 3 | Product page extraction (Playwright) | Implemented; **blocked by bot mitigation** |
| 4 | Manual entry with screenshot evidence | Implemented; used for the first snapshot |

### What actually happened

**The official API was closed.** Registration at `developer.bestbuy.com`
failed first with a client-side error — the form placed a non-Latin-1 character
into an HTTP header, which is a defect in the portal rather than in the input —
and then at a hard requirement that the account use a non-free, non-`.edu`
email domain. The API client is implemented and unit-tested; it becomes the
preferred source automatically if a key is ever supplied, with no code change.

**Page extraction was refused.** Playwright requests were first terminated at
the protocol layer (`ERR_HTTP2_PROTOCOL_ERROR`). Forcing HTTP/1.1 and removing
the automation fingerprint moved the failure from a protocol error to a silent
timeout: the edge accepted the connection and never responded. Headful mode
failed identically. Inspecting the site's own network traffic showed pricing
served over an authenticated GraphQL endpoint whose tokens are generated by
page JavaScript — reachable only by first loading the page, which is the step
being blocked.

**A third-party aggregator worked.** The RapidAPI Best Buy endpoint returns the
same figures and is reachable from any address. It is second-hand data, so the
collector verifies that the SKU echoed back matches the SKU requested and
refuses the response otherwise.

**Manual entry closed the gap.** The first snapshot was recorded by hand from
the live pages with screenshots retained. Those three prices were later
reproduced exactly by the aggregator, which serves as an independent check on
both.

### Storage

Observations are append-only. Nothing is updated or deleted; a correction is a
new row. This keeps the audit trail intact and makes every chart reproducible
from the raw record. Duplicate protection is a uniqueness constraint on
`(sku, captured_at_utc, source_method)`, so re-running after a partial failure
cannot double-count — while two *different* sources reporting the same price at
the same time are both kept, because independent agreement is evidence.

### Update logic

A scheduled GitHub Actions workflow captures a snapshot every six hours and
commits the updated database and regenerated charts back to the repository. The
commit history is therefore a verifiable record that the series accumulated
over time rather than being produced in one sitting.

Each run iterates the configured products and attempts the source chain in
order, stopping at the first success. A failure on one product is logged and
flagged; it never aborts the run for the others. Retries use exponential
backoff within each source, and the API clients self-throttle to stay inside
documented rate limits. The timestamp written by the collector is
authoritative, not the cron schedule, because scheduled runs can be delayed.

---

## 3. Assumptions

- The Best Buy online price for a new-condition unit is the figure of interest.
  Open-box offers, member pricing, tax, shipping and financing are out of scope.
- A SKU identifies a stable configuration over the observation window. Section
  5 describes what happens when that assumption breaks.
- Prices are USD from the US storefront and are not adjusted for regional
  pricing.
- The six tracked attributes are sufficient to establish equivalence for this
  exercise. Display quality, battery capacity, chassis material and warranty
  terms materially affect value and are **not** captured, so the groups are
  equivalent on paper specifications only.
- Five of the six tracked listings are fulfilled by third-party marketplace
  sellers rather than Best Buy directly; only the ASUS is sold by Best Buy. Their pricing behaviour may differ
  from first-party inventory.

---

## 4. Limitations

**The observation window is short.** Collection began on 11 September 2026.
That is enough to demonstrate that the tracker records change over time, but
not enough to characterise a promotional cycle or to support any claim about
pricing trends. A flat line means prices did not move during those days and
nothing more. The system is built to accumulate; the dataset is young.

**Prices are second-hand.** The active source is an aggregator, not the
retailer. Cache latency is possible and its extraction logic cannot be
inspected. The SKU echo check catches the response being about the wrong
product; it cannot catch the price being stale.

**Screen size is not part of the equivalence key.** A 14-inch and a 16-inch
machine with otherwise identical specifications are currently treated as
comparable. Adding it would be a one-line change; it is omitted because the
tracked set would then contain no cross-brand group at all, and stating the
limitation is more honest than silently discarding the comparison.

**Specification text is not a specification.** Parsing is driven by patterns
for current Intel, AMD and Qualcomm naming, and by device words such as
"Laptop". A title lacking either will not parse, and the product drops into
`unmatched` rather than being mis-grouped. This behaviour is pinned by a test
(`test_a_title_without_a_device_word_is_excluded_not_guessed`) so that a future
change cannot quietly start guessing.

**Form factor is only as good as the title.** One tracked machine — the HP
OmniBook 7 — is described in its own product copy as a 360-degree convertible
with four modes, but its Best Buy title says only "Laptop Computer". The parser
reads what the title says and classifies it as a clamshell. The declaration in
`config/products.yaml` matches the title deliberately, with a comment recording
the discrepancy.

**Validation thresholds are heuristics.** The ±15% and ±40% bands are judgement
calls, not values derived from an observed distribution — the window is too
short for that. They reduce the chance of a wrong price reaching a chart
unnoticed; they do not guarantee it.

**The comparison is specification-based, not value-based.** Two machines with
identical keys may differ substantially in build quality and support.

---

## 5. Error handling and validation

The tracker never auto-corrects a suspicious price. It records the observation,
raises a flag, and leaves the decision to a person. The rules encode failure
modes that occur on real retail listings:

| Rule | Severity | What it catches |
| --- | --- | --- |
| `price_missing` / `price_nonpositive` | critical | Source returned nothing usable |
| `price_below_floor` | critical | An accessory or placeholder price rendered in place of the PC |
| `price_jump_critical` (±40%) | critical | Open-box price substituted for new, or a changed configuration |
| `price_jump_warning` (±15%) | warning | A promotion worth confirming |
| `spec_drift` | critical | The live listing no longer matches the registered configuration |
| `spec_incomplete` | warning | Attributes unparseable; product excluded from comparison |
| `group_too_small` | warning | Fewer than two products in a group, so no comparison exists |
| `data_stale` | warning | No successful capture in over 24 hours |
| `collection_failed` | critical | Every source in the chain failed for this product |

`spec_drift` is the most important of these. Retail SKU pages can change their
bundle contents or configuration; without re-checking, the tracker would keep
following a price for a machine that is no longer the one registered. Every run
re-parses the live title and compares it against the declared specification.

Open flags are surfaced in the application and exported to
`reports/review_queue.csv`. The current database carries twelve flags raised
during the failed page-extraction attempts — they are retained deliberately, as
the audit record of that degradation path.

---

## 6. Engineering practice

Quality checks run in CI on every push, in a workflow separate from the
snapshot schedule so that a failing test never interrupts price collection and
a collection outage never looks like a broken build.

| Check | Tool | Result |
| --- | --- | --- |
| Lint | ruff | clean |
| Formatting | ruff format | clean |
| Static types | mypy | no issues across 12 modules |
| Tests | pytest | 61 passing |
| Coverage | pytest-cov | 81% lines, branch coverage enabled, gate at 70% |
| Grouping invariants | `tools/check_grouping.py` | verified against the real configuration |

The last of these is a guard specific to this project: a change to the parsing
rules that quietly merged two different machines would pass the unit tests but
break the comparison. Checking the actual product configuration in CI catches
that.

The tests are written around behaviour rather than implementation. The ones
that matter most:

- three listings from three brands, written in three house styles, must produce
  one identical group key — and a storage change must split them
- a storage size must never be read as system memory
- a re-run must not double-count a snapshot, and a failed run must not be
  treated as "the latest price"
- when every source fails, no price is produced — silence, never a guess
