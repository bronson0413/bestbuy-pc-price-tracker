"""Assert that the configured products still fall into the expected groups.

This runs in CI as a guard against silent regressions: a change to the parsing
rules that quietly merges two different machines would pass the unit tests but
break the comparison. Checking the real configuration catches that.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml

from tracker.normalize import parse_spec

CONFIG = Path(__file__).resolve().parents[1] / "config" / "products.yaml"

# The invariants that matter, expressed as properties rather than exact keys so
# that adding a product does not force an edit here.
MIN_TIER_GROUP_SIZE = 2  # at least one tier group must be a real comparison
MIN_BRANDS_IN_LARGEST = 2  # and must span more than one brand


def main() -> int:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    products = cfg.get("products", [])
    if not products:
        print("FAIL: no products configured")
        return 1

    tiers: dict[str, list[dict]] = defaultdict(list)
    strict: dict[str, list[dict]] = defaultdict(list)
    unparsed: list[str] = []

    for p in products:
        d = p.get("declared", {})
        spec = parse_spec(
            title=p.get("title"),
            cpu=d.get("cpu"),
            ram=d.get("ram"),
            storage=d.get("storage"),
            os_=d.get("os"),
            device_type=d.get("device_type"),
            form_factor=d.get("form_factor"),
        )
        if not spec.complete:
            unparsed.append(
                f"{p['brand']} {p['model_name']} ({p['sku']}): "
                f"missing {', '.join(spec.missing)}"
            )
            continue
        tiers[spec.tier_key()].append(p)
        strict[spec.group_key()].append(p)

    largest = max(tiers.values(), key=len, default=[])
    brands = {p["brand"] for p in largest}

    print(f"{len(products)} products, {len(strict)} exact groups, {len(tiers)} tiers")
    print(
        f"largest tier: {len(largest)} products across {len(brands)} brands "
        f"({', '.join(sorted(brands))})"
    )
    if unparsed:
        print("excluded (specs not parseable):")
        for line in unparsed:
            print(f"  {line}")

    ok = True
    if len(largest) < MIN_TIER_GROUP_SIZE:
        print(
            f"FAIL: largest tier has {len(largest)} product(s); "
            f"a comparison needs {MIN_TIER_GROUP_SIZE}"
        )
        ok = False
    if len(brands) < MIN_BRANDS_IN_LARGEST:
        print(
            f"FAIL: largest tier spans {len(brands)} brand(s); "
            f"cross-brand comparison needs {MIN_BRANDS_IN_LARGEST}"
        )
        ok = False

    # A strict group must never be larger than the tier that contains it.
    for key, members in strict.items():
        skus = {p["sku"] for p in members}
        parent = next(
            (v for v in tiers.values() if skus <= {p["sku"] for p in v}), None
        )
        if parent is None:
            print(f"FAIL: exact group {key} is not contained in any tier")
            ok = False

    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
