# AI usage and human validation

Lenovo AI Application Development Intern assessment (WD00102927)
Bo-Sheng Chen

Repository: https://github.com/bronson0413/bestbuy-pc-price-tracker

---

## 1. Tools used

| Tool | Role |
| --- | --- |
| Claude (Anthropic) | Implementation: collection pipeline, specification parser, validation rules, storage layer, Streamlit application, test suite, CI configuration |
| ruff, mypy, pytest, pytest-cov | Verification of the generated code |
| GitHub Actions | Continuous verification on every change |

The working model was deliberate: **AI as the implementation engine, human
judgement for direction and trade-offs, and tooling for verification.** Each
of those three is described below with concrete evidence.

---

## 2. Where AI did the work

**The collection pipeline.** Four price sources behind one interface, with
ordered degradation, exponential backoff, self-throttling, and per-observation
provenance. Roughly 600 lines across twelve modules.

**The specification parser.** Regular expressions covering current Intel Core
Ultra, legacy Core, AMD Ryzen and Qualcomm Snapdragon naming, plus memory,
storage, operating system, device type and form factor normalisation.

**The validation layer.** Nine rules that flag suspicious observations for a
person rather than correcting them automatically.

**The test suite.** 61 tests at 81% line coverage with branch coverage enabled.

Building this by hand would have taken considerably longer than the roughly one
day the project occupied. That compression is the point of the exercise, and it
is only defensible because of Section 4.

---

## 3. Where human judgement was required

These were decisions AI did not and could not make, because each one is a
judgement about what the project is *for*.

**Refusing to abandon automated collection.** When the official Best Buy API
proved unobtainable, the proposed fallback was manual entry. That was rejected
as premature. The subsequent attempts — forcing HTTP/1.1 past the protocol
error, running headful, inspecting the site's own network traffic to locate its
GraphQL pricing endpoint, and finally identifying a third-party aggregator —
happened because the easier answer was not accepted. **The tracker collects
automatically today as a direct result of that call.**

**Identifying that the product contained no AI.** The first complete version
was a purely rule-based price tracker. It satisfied every deliverable in the
brief and contained no machine learning or language-model component at all —
for a role titled *AI Application Development Intern*. Noticing that gap
between the artefact and the position it was submitted for changed the
architecture's direction.

**Rejecting "sufficient".** The brief asks for a simple working prototype, and
an early version delivered exactly that: three products, one grouping rule, one
data source. The decision to go further — six products, two grouping levels, a
four-tier source chain, a full quality toolchain — was a judgement that meeting
the minimum bar is not a differentiator when the applicant pool is large.

**The investment judgement.** Roughly sixty applications had produced one
take-home assessment. The conclusion that this justified several days of
concentrated effort rather than a minimum-effort submission was a judgement
about return, not about engineering.

---

## 4. How the AI-generated output was verified

Line-by-line reading of 600 lines of generated code is a poor verification
strategy: it is slow, it is unreliable, and a human reader will not catch the
class of defect that matters most. **Verification was done by tooling**, and
the tooling found real defects.

### mypy found eleven type errors in the generated code

The most serious were in `normalize.py`, where `group_key()` could
construct a string from values typed as `str | None`:

```
normalize.py:175: error: List item 0 has incompatible type "str | None"; expected "str"
normalize.py:178: error: List item 3 has incompatible type "str | None"; expected "str"
...
```

At runtime these were guarded by a `complete` check, so the code worked. But
the guarantee was implicit — nothing in the type system enforced it, and a
future change to `complete` would have broken the key silently. The fix
extracts the narrowing into one place (`_parts()`) and makes the contract
explicit. mypy also found an unchecked `Optional` return from
`cursor.lastrowid` and a type mismatch in the browser source.

**This is the central finding of the exercise: code that passes its tests is
not the same as code that is correct.** Every one of these eleven defects was
invisible to the test suite.

### The tests found a hidden dependency in the parser

Writing an integration test with a realistic product configuration surfaced a
limitation nobody had stated: the parser cannot determine device type from a
title that does not contain a device word such as "Laptop". Real Best Buy
titles almost always carry one, so it had never failed in practice.

It was **not** silently fixed. The behaviour — exclude rather than assume — is
correct, so it was pinned with a test
(`test_a_title_without_a_device_word_is_excluded_not_guessed`) and recorded in
the limitations section, so that a future change cannot quietly start guessing.

### The grouping guard catches what unit tests cannot

`tools/check_grouping.py` runs in CI against the **real** product
configuration and asserts structural invariants: the largest tier must contain
at least two products spanning at least two brands, and no exact group may
escape the tier that contains it.

A parser change that quietly merged two different machines would pass every
unit test and break the comparison. This check exists because unit tests
verify the rules in isolation, not the outcome on live data.

### Independent price verification

The first snapshot was recorded manually from the live Best Buy pages, with
screenshots retained. The aggregator API later returned identical figures for
all three products:

| SKU | Manual reading | API response |
| --- | --- | --- |
| 6603654 | $1,199.99 | $1,199.99 |
| 12251856 | $1,678.49 | $1,678.49 |
| 12349089 | $849.99 | $849.99 |

Two independent methods agreeing is not proof, but it is the strongest
available check on a second-hand source. Both observations are retained in the
database rather than de-duplicated, precisely so that the agreement remains
visible in the record.

### A data error found by human inspection

While verifying processor models on the product pages, the structured
**Processor Model** field on two listings was found to read **"Intel Processor
N150"** — an entry-level chip — for machines whose descriptions specify Intel
Core Ultra 5 225U and 236V respectively.

**The retailer's own structured data is wrong for these products.** No
automated check would have caught this, because the field parses cleanly; it is
simply false. This finding validates the design decision to parse titles and
descriptions rather than trust structured attributes, and it came from reading
product pages, not from any tool.

---

## 5. Known risks in the AI-assisted portions

- **The parser encodes current processor naming.** A future naming scheme will
  not parse. The failure mode is exclusion, not mis-grouping — the safer
  direction, and the one the tests enforce.
- **Page-extraction selectors depend on current markup** and will break when
  the site changes. The collector reports this as a failure rather than
  recording a wrong number.
- **Validation thresholds are heuristics**, not values derived from an observed
  distribution. They reduce the chance of a wrong price reaching a chart; they
  do not eliminate it.
- **Coverage is 81%, not 100%.** The uncovered paths are mostly network error
  handling in the API clients, which would require extensive mocking for
  limited benefit.
- **Verification was by tooling, not by line-by-line review.** Static analysis,
  tests and CI catch type errors, regressions and structural breakage. They do
  not catch a design that is internally consistent but wrong for the problem.
  That remains a human responsibility, and it is the reason Section 3 exists.

---

## 6. Summary

AI wrote effectively all of the code in this project. It was not used as an
oracle. Direction, scope and trade-offs were human decisions; the generated
output was verified by static analysis, an executable test suite, and
continuous integration; and the verification found real defects that the code's
own tests did not.

The eleven type errors are the clearest illustration. They were invisible to 61
passing tests and would have remained invisible to a human reader skimming 600
lines. Finding them required running the right tool — **which is what using AI
responsibly at this speed actually consists of.**
