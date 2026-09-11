# AI usage and human validation

Lenovo AI Application Development Intern assessment (WD00102927)

> **Before submitting, replace every `[FILL IN]` with what you actually did.**
> This document is only worth anything if it is accurate.

## Tools used

| Tool | Used for |
| --- | --- |
| Claude (Anthropic) | Architecture discussion, implementation of the collector, normalisation rules, validation checks, Streamlit app and this documentation |
| `[FILL IN: any others, e.g. GitHub Copilot, ChatGPT]` | `[FILL IN]` |

## Where AI helped, and where it did not

**Design.** The three-tier source chain (official API → page extraction →
manual entry) came out of a discussion about what happens when a retailer blocks
automated collection. The AI's first instinct was a single scraper; the
requirement that every price carry its own provenance, and that a blocked
request fail loudly rather than record a challenge page, came from working
through the failure cases explicitly.

**Normalisation rules.** The regular expressions for Intel Core Ultra, legacy
Core, Ryzen and Snapdragon naming were drafted by AI and then tested against
real listing titles. One bug found in review: an early version matched `512GB`
in `512GB SSD` as system memory. The fix — strip storage tokens before looking
for a loose memory figure — is now covered by a regression test
(`test_storage_is_not_mistaken_for_memory`).

**Validation thresholds.** The ±15% / ±40% price-jump bands are judgement
calls, not derived values. They were set to catch the specific case of an
open-box price being substituted for a new-condition one, which is a large
discrete drop, while tolerating ordinary promotional movement.

**What AI could not do.** The sandbox used for development had no network access
to bestbuy.com, so no AI-generated price data exists in this repository. Every
price in `data/prices.sqlite` was collected from Best Buy by the tracker itself.
The only synthetic data in the project is in `tools/selftest.py`, which exists
solely to exercise the pipeline before real snapshots accumulated; its output is
written to a separate database and its chart is watermarked `SYNTHETIC DATA`.

## Human validation performed

- Verified that each configured SKU resolves to the product page it claims, and
  that the title in `config/products.yaml` matches the live listing exactly.
  `[FILL IN: date you checked]`
- Cross-checked `[FILL IN: N]` API-reported prices against the prices displayed
  on the product pages in a browser. `[FILL IN: result — did they match?]`
- Confirmed the three tracked products genuinely share a configuration by
  reading the specification tables on their product pages, not by trusting the
  parsed title. `[FILL IN: any discrepancy found]`
- Reviewed every flag raised during the observation window.
  `[FILL IN: how many, and how each was resolved]`
- Read the generated code rather than accepting it. `[FILL IN: anything you
  changed or rejected — this is the most useful line in the document]`

## Known risks in the AI-assisted portions

- The specification parser encodes current processor naming conventions. A
  future naming scheme will not parse. The failure mode is exclusion, not
  mis-grouping, which is the safer direction.
- The page-extraction selectors depend on Best Buy's current markup and will
  break when the site changes. The collector reports this as a failure rather
  than recording a wrong number.
- Validation thresholds are heuristics. They reduce the chance that a wrong
  price reaches the chart unnoticed; they do not guarantee it.
