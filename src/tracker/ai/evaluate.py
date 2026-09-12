"""Measure whether the language model is actually better than the rules.

The claim "we added AI" is worth nothing without a number attached. This module
produces the number: three strategies run against the same hand-verified cases,
scored per field, reported side by side.

Scoring is deliberately strict about one thing. A field the gold set marks as
null must be predicted null. Inventing a plausible value where the source text
states nothing is counted as an error, not as a miss -- because in this system
a confident wrong answer is worse than no answer.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..normalize import NormalizedSpec, parse_spec
from .ensemble import reconcile
from .extract import ExtractionResult, SpecExtractor

FIELDS = ("cpu", "ram_gb", "storage_gb", "os", "device_type", "form_factor")


@dataclass
class FieldScore:
    correct: int = 0
    wrong: int = 0
    missed: int = 0  # gold has a value, prediction is null
    hallucinated: int = 0  # gold is null, prediction has a value

    @property
    def total(self) -> int:
        return self.correct + self.wrong + self.missed + self.hallucinated

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass
class StrategyReport:
    name: str
    per_field: dict[str, FieldScore] = field(default_factory=dict)
    complete_specs: int = 0  # all six fields correct
    cases: int = 0
    latency_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        c = sum(s.correct for s in self.per_field.values())
        t = sum(s.total for s in self.per_field.values())
        return c / t if t else 0.0

    @property
    def hallucination_rate(self) -> float:
        """Share of predictions that asserted a value where none was stated."""
        h = sum(s.hallucinated for s in self.per_field.values())
        t = sum(s.total for s in self.per_field.values())
        return h / t if t else 0.0

    @property
    def exact_match_rate(self) -> float:
        return self.complete_specs / self.cases if self.cases else 0.0


def score_case(
    predicted: NormalizedSpec, expected: dict[str, Any], report: StrategyReport
) -> bool:
    """Score one case, returning whether every field was correct."""
    all_right = True
    for name in FIELDS:
        bucket = report.per_field.setdefault(name, FieldScore())
        got = getattr(predicted, name)
        want = expected.get(name)
        if want == "":
            want = None

        if got == want:
            bucket.correct += 1
        elif want is None:
            bucket.hallucinated += 1
            all_right = False
        elif got is None:
            bucket.missed += 1
            all_right = False
        else:
            bucket.wrong += 1
            all_right = False
    return all_right


def rules_only(case: dict[str, Any]) -> NormalizedSpec:
    """The baseline: what the deterministic parser reads from the title alone.

    Title only, because that is what the collector actually receives on every
    run. Giving the baseline the description too would flatter it beyond what
    the production path can do.
    """
    return parse_spec(title=case.get("title", ""))


def evaluate(
    goldset: Path,
    extractor: SpecExtractor | None = None,
    *,
    include_llm: bool = True,
) -> dict[str, StrategyReport]:
    """Run every strategy and return one report each.

    Scoring happens in a second pass over only the cases every strategy
    completed. Comparing a nine-case baseline against a six-case model run --
    because three calls hit a quota -- would make the columns look comparable
    when they are not.
    """
    data = yaml.safe_load(goldset.read_text(encoding="utf-8"))
    cases = data["cases"]

    reports = {
        "rules": StrategyReport("Rules only (title)"),
        "llm": StrategyReport("LLM only (title + description)"),
        "hybrid": StrategyReport("Rules + LLM reconciled"),
    }
    use_llm = include_llm and extractor is not None and extractor.available

    extracted: dict[str, ExtractionResult] = {}
    if use_llm:
        assert extractor is not None
        for case in cases:
            try:
                extracted[case["id"]] = extractor.extract(
                    title=case.get("title", ""),
                    description=case.get("description", ""),
                )
            except Exception as exc:
                reports["llm"].errors.append(f"{case['id']}: {exc}")
                reports["hybrid"].errors.append(f"{case['id']}: {exc}")

    scored = [c for c in cases if not use_llm or c["id"] in extracted]
    for case in scored:
        expected = case["expected"]
        rules_spec = rules_only(case)

        reports["rules"].cases += 1
        if score_case(rules_spec, expected, reports["rules"]):
            reports["rules"].complete_specs += 1

        if not use_llm:
            continue

        result = extracted[case["id"]]
        reports["llm"].cases += 1
        reports["llm"].latency_s += result.latency_s
        if score_case(result.spec, expected, reports["llm"]):
            reports["llm"].complete_specs += 1

        merged = reconcile(rules_spec, result.spec, llm_usable=result.usable)
        reports["hybrid"].cases += 1
        reports["hybrid"].latency_s += result.latency_s
        if score_case(merged.spec, expected, reports["hybrid"]):
            reports["hybrid"].complete_specs += 1

    skipped = len(cases) - len(scored)
    if skipped:
        for report_ in reports.values():
            report_.errors.append(
                f"{skipped} case(s) excluded from scoring: the model could not "
                "be reached for them, so every strategy is scored on the "
                f"{len(scored)} cases all of them completed."
            )
    return reports


def render(reports: dict[str, StrategyReport]) -> str:
    """A markdown table, so the result can go straight into the report."""
    lines = [
        "| Strategy | Field accuracy | Complete specs | Hallucination rate | Cases |",
        "| --- | --- | --- | --- | --- |",
    ]
    for report in reports.values():
        if not report.cases:
            continue
        lines.append(
            f"| {report.name} | {report.accuracy:.1%} | "
            f"{report.complete_specs}/{report.cases} ({report.exact_match_rate:.0%}) | "
            f"{report.hallucination_rate:.1%} | {report.cases} |"
        )

    lines.append("")
    lines.append("Per-field accuracy:")
    lines.append("")
    header = (
        "| Field | " + " | ".join(r.name for r in reports.values() if r.cases) + " |"
    )
    lines.append(header)
    lines.append("| --- " * (1 + sum(1 for r in reports.values() if r.cases)) + "|")
    for name in FIELDS:
        row = [name]
        for report in reports.values():
            if not report.cases:
                continue
            s = report.per_field.get(name)
            row.append(f"{s.accuracy:.0%}" if s else "-")
        lines.append("| " + " | ".join(row) + " |")

    for report in reports.values():
        if report.errors:
            lines.append("")
            lines.append(f"{report.name} errors: {len(report.errors)}")
            for e in report.errors[:5]:
                lines.append(f"  - {e}")
    return "\n".join(lines)


def to_json(reports: dict[str, StrategyReport]) -> str:
    return json.dumps({k: asdict(v) for k, v in reports.items()}, indent=2, default=str)


def failure_breakdown(reports: dict[str, StrategyReport]) -> str:
    """Where each strategy loses, which matters more than the headline number."""
    lines = [
        "| Strategy | Correct | Wrong | Missed | Hallucinated |",
        "| --- | --- | --- | --- | --- |",
    ]
    for report in reports.values():
        if not report.cases:
            continue
        c: Counter[str] = Counter()
        for s in report.per_field.values():
            c["correct"] += s.correct
            c["wrong"] += s.wrong
            c["missed"] += s.missed
            c["hallucinated"] += s.hallucinated
        lines.append(
            f"| {report.name} | {c['correct']} | {c['wrong']} | "
            f"{c['missed']} | {c['hallucinated']} |"
        )
    return "\n".join(lines)
