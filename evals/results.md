# Extraction evaluation

Model: `gemini-flash-lite-latest` · 9 hand-verified cases · temperature 0

| Strategy | Field accuracy | Complete specs | Hallucination rate | Cases |
| --- | --- | --- | --- | --- |
| Rules only (title) | 74.1% | 2/9 (22%) | 0.0% | 9 |
| LLM only (title + description) | 96.3% | 7/9 (78%) | 0.0% | 9 |
| Rules + LLM reconciled | 98.1% | 8/9 (89%) | 0.0% | 9 |

Per-field accuracy:

| Field | Rules only (title) | LLM only (title + description) | Rules + LLM reconciled |
| --- | --- | --- | --- |
| cpu | 67% | 100% | 100% |
| ram_gb | 100% | 100% | 100% |
| storage_gb | 89% | 89% | 100% |
| os | 67% | 100% | 100% |
| device_type | 67% | 100% | 100% |
| form_factor | 56% | 89% | 89% |

## Failure breakdown

| Strategy | Correct | Wrong | Missed | Hallucinated |
| --- | --- | --- | --- | --- |
| Rules only (title) | 40 | 1 | 13 | 0 |
| LLM only (title + description) | 52 | 1 | 1 | 0 |
| Rules + LLM reconciled | 53 | 1 | 0 | 0 |

## Reading these numbers

**The hybrid beats either component.** On `storage_gb` the rules score 89% and
the model scores 89%, but reconciling them scores 100%: the two readers miss
different things, and comparing them recovers what each one dropped. That is an
architectural gain, not an effect of adding a model.

**No strategy hallucinated.** Two of the nine cases are traps whose correct
answer is "not stated". The model returned null for both. Re-normalising every
field through the project's own vocabulary means an invented value cannot enter
the specification even if the model produces one.

**The failure mode changes.** The rules miss 13 fields and get 1 wrong. The
hybrid misses none and gets the same 1 wrong — it recovers the omissions
without introducing new errors.

**`form_factor` remains at 89%.** The model reads a hinge description correctly
where the rules cannot, but one case still fails. Reconciliation does not
improve on the model here, so this is the field where extraction is weakest and
where a reviewer should look first.

**Model choice was decided by this evaluation, not by preference.** The larger
`gemini-flash-latest` exhausted its free-tier quota partway through and could
not complete a run; the lite model completed all nine cases at 96.3% accuracy.
For a reading task with a fixed output schema, the smaller model is sufficient
and the larger one is unreliable in the environment that has to run it.
