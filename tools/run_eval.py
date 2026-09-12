"""Run the extraction evaluation and write the report.

    python tools/run_eval.py                 # rules baseline only, no API calls
    GEMINI_API_KEY=... python tools/run_eval.py --llm

Costs real tokens when --llm is set, so it is not part of CI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tracker.ai.evaluate import evaluate, failure_breakdown, render, to_json
from tracker.ai.extract import SpecExtractor

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--goldset", type=Path, default=ROOT / "evals" / "goldset.yaml")
    ap.add_argument("--outdir", type=Path, default=ROOT / "evals")
    ap.add_argument("--llm", action="store_true", help="also evaluate the model")
    ap.add_argument("--model", help="override the model name")
    ap.add_argument(
        "--interval",
        type=float,
        default=6.0,
        help="seconds between calls; raise it if the key is rate limited",
    )
    ap.add_argument(
        "--list-models",
        action="store_true",
        help="print the models this key can call, then exit",
    )
    args = ap.parse_args(argv)

    if args.list_models:
        from tracker.ai.client import GeminiClient

        client = GeminiClient()
        if not client.available:
            print("GEMINI_API_KEY is not set.", file=sys.stderr)
            return 2
        for name in client.list_models():
            print(name)
        return 0

    extractor = None
    if args.llm:
        from tracker.ai.client import GeminiClient

        # One call every few seconds keeps a free-tier key inside its
        # per-minute quota. Retrying a rejected call costs more than waiting.
        client = (
            GeminiClient(
                model=args.model or None,  # type: ignore[arg-type]
                min_interval_s=args.interval,
            )
            if args.model
            else GeminiClient(min_interval_s=args.interval)
        )
        extractor = SpecExtractor(client)
    if args.llm and not (extractor and extractor.available):
        print("GEMINI_API_KEY is not set; cannot evaluate the model.", file=sys.stderr)
        return 2

    reports = evaluate(args.goldset, extractor, include_llm=args.llm)

    table = render(reports)
    breakdown = failure_breakdown(reports)
    print(table)
    print()
    print(breakdown)

    args.outdir.mkdir(parents=True, exist_ok=True)
    (args.outdir / "results.md").write_text(
        f"# Extraction evaluation\n\n{table}\n\n## Failure breakdown\n\n{breakdown}\n",
        encoding="utf-8",
    )
    (args.outdir / "results.json").write_text(to_json(reports), encoding="utf-8")
    print(f"\nwrote {args.outdir / 'results.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
