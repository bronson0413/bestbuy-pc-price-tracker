"""Reconcile the rule-based parser with the language model.

The model is not a better parser. It is a second reader with different failure
modes: the rules fail by reading nothing, the model fails by reading something
that is not there. Running both and comparing turns those two weaknesses into
one signal.

Four outcomes, and what each means:

  agreement      both read the same value            -- highest confidence
  rules_only     the model saw nothing               -- trust the rules
  llm_only       the rules saw nothing               -- a candidate, needs review
  disagreement   both read a value, and they differ  -- neither is trusted

The last case is the point of the exercise. A disagreement means either the
rules have a gap or the model invented something, and both are worth a human's
attention. Nothing is auto-resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..normalize import NormalizedSpec
from ..validate import Flag

FIELDS = ("cpu", "ram_gb", "storage_gb", "os", "device_type", "form_factor")


class Verdict(StrEnum):
    AGREEMENT = "agreement"
    RULES_ONLY = "rules_only"
    LLM_ONLY = "llm_only"
    DISAGREEMENT = "disagreement"
    NEITHER = "neither"


@dataclass(frozen=True)
class FieldOutcome:
    field: str
    rules_value: object
    llm_value: object
    verdict: Verdict

    @property
    def resolved(self) -> object:
        """What the field becomes.

        A disagreement resolves to the rules' value, not the model's: when two
        readers conflict, the deterministic one is the one that can be audited.
        The conflict is flagged regardless, so nothing is quietly settled.
        """
        if self.verdict in (
            Verdict.AGREEMENT,
            Verdict.RULES_ONLY,
            Verdict.DISAGREEMENT,
        ):
            return self.rules_value
        if self.verdict is Verdict.LLM_ONLY:
            return self.llm_value
        return None


@dataclass
class Reconciliation:
    outcomes: list[FieldOutcome]
    spec: NormalizedSpec
    flags: list[Flag]

    @property
    def filled_by_llm(self) -> list[str]:
        return [o.field for o in self.outcomes if o.verdict is Verdict.LLM_ONLY]

    @property
    def conflicts(self) -> list[FieldOutcome]:
        return [o for o in self.outcomes if o.verdict is Verdict.DISAGREEMENT]

    @property
    def agreements(self) -> list[str]:
        return [o.field for o in self.outcomes if o.verdict is Verdict.AGREEMENT]


def reconcile(
    rules: NormalizedSpec,
    llm: NormalizedSpec | None,
    *,
    llm_usable: bool = True,
) -> Reconciliation:
    """Combine two readings of the same listing into one spec plus flags.

    When the model's own confidence is low, its values are discarded before
    comparison -- a low-confidence reading is not evidence of a conflict.
    """
    outcomes: list[FieldOutcome] = []
    flags: list[Flag] = []
    resolved: dict[str, object] = {}

    for field in FIELDS:
        r = getattr(rules, field)
        m = getattr(llm, field) if (llm is not None and llm_usable) else None

        if r is not None and m is not None:
            verdict = Verdict.AGREEMENT if r == m else Verdict.DISAGREEMENT
        elif r is not None:
            verdict = Verdict.RULES_ONLY
        elif m is not None:
            verdict = Verdict.LLM_ONLY
        else:
            verdict = Verdict.NEITHER

        outcome = FieldOutcome(field=field, rules_value=r, llm_value=m, verdict=verdict)
        outcomes.append(outcome)
        resolved[field] = outcome.resolved

        if verdict is Verdict.DISAGREEMENT:
            flags.append(
                Flag(
                    "parser_disagreement",
                    "critical",
                    f"{field}: rules read {r!r}, model read {m!r}. "
                    "Kept the rules' value; verify against the product page.",
                )
            )
        elif verdict is Verdict.LLM_ONLY:
            flags.append(
                Flag(
                    "llm_filled_gap",
                    "warning",
                    f"{field}: rules read nothing, model proposed {m!r}. "
                    "Confirm before relying on the grouping.",
                )
            )

    return Reconciliation(
        outcomes=outcomes,
        spec=NormalizedSpec(**resolved),  # type: ignore[arg-type]
        flags=flags,
    )
