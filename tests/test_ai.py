"""The model is a proposer. These tests verify it cannot become a decider."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from tracker.ai.client import GeminiClient, LLMError, LLMResponse
from tracker.ai.ensemble import Verdict, reconcile
from tracker.ai.evaluate import StrategyReport, rules_only, score_case
from tracker.ai.extract import _vet
from tracker.normalize import NormalizedSpec, parse_spec

GOOD = {
    "cpu": "Intel Core Ultra 5 226V",
    "ram_gb": 16,
    "storage_gb": 512,
    "os": "Windows 11 Home",
    "device_type": "laptop",
    "form_factor": "convertible",
    "confidence": "high",
    "notes": "",
}


# --- the vetting layer ------------------------------------------------------


def test_a_well_formed_extraction_becomes_a_usable_spec() -> None:
    r = _vet(GOOD)
    assert r.spec.group_key() == (
        "intel-ultra5-226v|16gb|512gb|windows-11-home|laptop|convertible"
    )
    assert r.usable and r.rejected == {}


def test_invented_values_are_rejected_not_stored() -> None:
    r = _vet({**GOOD, "cpu": "Quantum Brain 9000", "form_factor": "hovercraft"})
    assert r.spec.cpu is None and r.spec.form_factor is None
    assert set(r.rejected) == {"cpu", "form_factor"}
    # An incomplete spec cannot group, so the invention cannot reach a chart.
    assert r.spec.group_key() == "unmatched"


def test_an_unrecognised_confidence_downgrades_to_low() -> None:
    r = _vet({**GOOD, "confidence": "absolutely certain"})
    assert r.confidence == "low"
    assert not r.usable  # and low-confidence output is never acted on


def test_nulls_are_preserved_rather_than_filled() -> None:
    r = _vet({**GOOD, "ram_gb": None, "cpu": "null"})
    assert r.spec.ram_gb is None and r.spec.cpu is None
    assert r.rejected == {}  # declining to answer is not an error


def test_numeric_and_prose_capacities_both_normalise() -> None:
    assert _vet({**GOOD, "storage_gb": 1024}).spec.storage_gb == 1024
    assert _vet({**GOOD, "storage_gb": "1TB"}).spec.storage_gb == 1024
    assert _vet({**GOOD, "ram_gb": "16 GB"}).spec.ram_gb == 16


# --- reconciliation ---------------------------------------------------------

RULES = parse_spec(
    title="ASUS - ExpertBook P5 Laptop - Intel Core Ultra 5 226V with 16GB "
    "Memory - 512GB SSD - Windows 11 Pro"
)


def test_agreement_raises_nothing() -> None:
    r = reconcile(RULES, RULES)
    assert r.flags == []
    assert len(r.agreements) == 6


def test_the_model_may_fill_a_gap_but_it_is_flagged() -> None:
    thin = parse_spec(
        title="Lenovo - ThinkBook 14 - Intel Core Ultra 5 - 16GB "
        "- 512GB SSD - Win 11 Pro - Laptop"
    )
    assert thin.cpu is None  # the title has no suffix
    model = NormalizedSpec(
        cpu="intel-ultra5-225u",
        ram_gb=16,
        storage_gb=512,
        os="windows-11-pro",
        device_type="laptop",
        form_factor="clamshell",
    )
    r = reconcile(thin, model)
    assert r.spec.cpu == "intel-ultra5-225u"  # the gap is filled
    assert "cpu" in r.filled_by_llm
    assert any(f.rule == "llm_filled_gap" for f in r.flags)


def test_a_conflict_keeps_the_rules_value_and_escalates() -> None:
    model = NormalizedSpec(
        cpu="intel-ultra7-258v",
        ram_gb=16,
        storage_gb=512,
        os="windows-11-pro",
        device_type="laptop",
        form_factor="clamshell",
    )
    r = reconcile(RULES, model)
    assert r.spec.cpu == "intel-ultra5-226v"  # deterministic value wins
    assert [o.field for o in r.conflicts] == ["cpu"]
    flag = next(f for f in r.flags if f.rule == "parser_disagreement")
    assert flag.severity == "critical"  # never silently settled


def test_low_confidence_output_is_discarded_before_comparison() -> None:
    thin = parse_spec(title="Acme - Notebook - 16GB - 512GB SSD - Windows 11 Home")
    model = NormalizedSpec(
        cpu="intel-ultra5-226v",
        ram_gb=16,
        storage_gb=512,
        os="windows-11-home",
        device_type="laptop",
        form_factor="clamshell",
    )
    r = reconcile(thin, model, llm_usable=False)
    assert r.spec.cpu is None  # not filled from a guess
    assert r.filled_by_llm == []
    assert r.flags == []  # and not a conflict either


def test_when_neither_reader_sees_a_field_it_stays_empty() -> None:
    blank = NormalizedSpec(None, None, None, None, None, None)
    r = reconcile(blank, blank)
    assert r.spec.group_key() == "unmatched"
    assert all(o.verdict is Verdict.NEITHER for o in r.outcomes)


# --- scoring ----------------------------------------------------------------


def test_a_guess_where_the_gold_set_says_null_counts_as_hallucination() -> None:
    report = StrategyReport("t")
    predicted = NormalizedSpec(
        "intel-ultra5-226v", 16, 512, "windows-11-home", "laptop", "clamshell"
    )
    expected = {
        "cpu": None,
        "ram_gb": 16,
        "storage_gb": 512,
        "os": "windows-11-home",
        "device_type": "laptop",
        "form_factor": "clamshell",
    }
    assert score_case(predicted, expected, report) is False
    assert report.per_field["cpu"].hallucinated == 1
    assert report.per_field["cpu"].missed == 0  # inventing is not the same as missing


def test_declining_to_answer_counts_as_a_miss_not_an_error() -> None:
    report = StrategyReport("t")
    predicted = NormalizedSpec(None, 16, 512, "windows-11-home", "laptop", "clamshell")
    expected = {
        "cpu": "intel-ultra5-226v",
        "ram_gb": 16,
        "storage_gb": 512,
        "os": "windows-11-home",
        "device_type": "laptop",
        "form_factor": "clamshell",
    }
    score_case(predicted, expected, report)
    assert report.per_field["cpu"].missed == 1
    assert report.per_field["cpu"].wrong == 0


def test_the_baseline_reads_titles_only() -> None:
    case = {
        "title": "ASUS - ExpertBook P5 Laptop - Intel Core Ultra 5 226V "
        "with 16GB Memory - 512GB SSD - Windows 11 Pro",
        "description": "This machine has a 360-degree hinge.",
    }
    # The description would change form_factor; the baseline must not see it.
    assert rules_only(case).form_factor == "clamshell"


# --- client plumbing --------------------------------------------------------


def test_json_wrapped_in_a_code_fence_is_still_parsed() -> None:
    r = LLMResponse(text='```json\n{"cpu": "x"}\n```', model="m", latency_s=0.1)
    assert r.as_json() == {"cpu": "x"}


def test_non_json_output_raises_rather_than_returning_something() -> None:
    with pytest.raises(LLMError, match="did not return JSON"):
        LLMResponse(text="I think it is an ASUS", model="m", latency_s=0.1).as_json()


def test_a_missing_key_is_reported_not_silently_skipped() -> None:
    client = GeminiClient(api_key="")
    assert not client.available
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        client.generate("hello")


def test_extraction_is_deterministic_by_default() -> None:
    # Temperature zero: the same listing must yield the same specification, or
    # the stored provenance is not reproducible.
    assert GeminiClient(api_key="k").temperature == 0.0


def test_the_model_name_is_an_alias_not_a_pinned_version() -> None:
    # A pinned name is a dated assumption: the first evaluation run failed with
    # a 404 because the version it named had been retired.
    assert "latest" in GeminiClient(api_key="k").model
    assert "lite" in GeminiClient(api_key="k").model  # chosen on evaluation evidence


def test_the_api_key_never_appears_in_an_error_message() -> None:
    from tracker.ai.client import _redact

    key = "AQ.SuperSecretKeyValue"
    message = f"404 Not Found for url: https://example/models?key={key}"
    redacted = _redact(message, key)
    assert key not in redacted
    assert "redacted" in redacted


def test_redaction_is_a_no_op_without_a_key() -> None:
    from tracker.ai.client import _redact

    assert _redact("some message", "") == "some message"


def test_a_transport_error_is_redacted_before_it_becomes_an_exception() -> None:
    """The key travels as a query parameter, so it lands in requests' own
    exception text. Redaction has to happen at every boundary, not once at the
    end -- the first evaluation run leaked the key through a 503."""
    import requests

    from tracker.ai.client import GeminiClient, LLMError

    key = "AQ.SecretKeyThatMustNotLeak"
    client = GeminiClient(api_key=key, max_retries=1)

    def exploding(*args, **kwargs):
        raise requests.HTTPError(
            f"503 Server Error for url: https://x/models:generateContent?key={key}"
        )

    original = requests.post
    requests.post = exploding
    try:
        with pytest.raises(LLMError) as caught:
            client.generate("hello")
    finally:
        requests.post = original

    assert key not in str(caught.value)
    assert "redacted" in str(caught.value)


def test_rate_limit_backoff_outlasts_a_per_minute_window() -> None:
    from tracker.ai.client import _RATE_LIMIT_BACKOFF

    # Free-tier quotas reset per minute; backing off for seconds just burns
    # the remaining attempts.
    assert sum(_RATE_LIMIT_BACKOFF) >= 60


def test_scoring_uses_only_the_cases_every_strategy_completed(tmp_path) -> None:
    """A nine-case baseline against a six-case model run is not a comparison."""
    import yaml

    from tracker.ai.evaluate import evaluate
    from tracker.ai.extract import ExtractionResult
    from tracker.normalize import parse_spec

    goldset = tmp_path / "gold.yaml"
    goldset.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "ok",
                        "title": "ASUS Laptop - Intel Core Ultra 5 226V with "
                        "16GB Memory - 512GB SSD - Windows 11 Pro",
                        "expected": {
                            "cpu": "intel-ultra5-226v",
                            "ram_gb": 16,
                            "storage_gb": 512,
                            "os": "windows-11-pro",
                            "device_type": "laptop",
                            "form_factor": "clamshell",
                        },
                    },
                    {
                        "id": "unreachable",
                        "title": "HP Laptop - Intel Core Ultra 5 226V "
                        "with 16GB Memory - 512GB SSD - Windows 11 Pro",
                        "expected": {
                            "cpu": "intel-ultra5-226v",
                            "ram_gb": 16,
                            "storage_gb": 512,
                            "os": "windows-11-pro",
                            "device_type": "laptop",
                            "form_factor": "clamshell",
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    class HalfBrokenExtractor:
        available = True

        def extract(self, *, title: str, description: str = "") -> ExtractionResult:
            if title.startswith("HP"):
                raise RuntimeError("rate limited (429)")
            return ExtractionResult(
                spec=parse_spec(title=title), confidence="high", notes=""
            )

    reports = evaluate(goldset, HalfBrokenExtractor())  # type: ignore[arg-type]

    # The baseline is scored on one case, not two, because that is the only
    # case the model also completed.
    assert reports["rules"].cases == 1
    assert reports["llm"].cases == 1
    assert any("excluded from scoring" in e for e in reports["rules"].errors)
