"""Automated checks that decide what a human must look at.

The tracker never auto-corrects a suspicious price. It records the observation,
raises a flag, and waits for a person to adjudicate it in the review queue. The
flag rules below encode the failure modes actually seen on retail listings:
open-box prices substituted for new, a bundle page replacing the base config,
a rendered "$0.00" placeholder, and a listing quietly changing configuration.
"""
from __future__ import annotations

from dataclasses import dataclass

from .normalize import NormalizedSpec, parse_spec

PRICE_FLOOR_USD = 50.0
PRICE_CEILING_USD = 20_000.0
JUMP_WARN_PCT = 15.0
JUMP_CRITICAL_PCT = 40.0


@dataclass
class Flag:
    rule: str
    severity: str  # "info" | "warning" | "critical"
    detail: str


def check_price(price: float | None, *, previous: float | None = None) -> list[Flag]:
    flags: list[Flag] = []
    if price is None:
        return [Flag("price_missing", "critical", "No price returned by any source")]
    if price <= 0:
        return [Flag("price_nonpositive", "critical", f"Price {price} is not positive")]
    if price < PRICE_FLOOR_USD:
        flags.append(Flag("price_below_floor", "critical",
                          f"${price:.2f} is below the ${PRICE_FLOOR_USD:.0f} plausibility "
                          "floor for a Windows PC; likely an accessory or placeholder"))
    if price > PRICE_CEILING_USD:
        flags.append(Flag("price_above_ceiling", "warning",
                          f"${price:.2f} exceeds the ${PRICE_CEILING_USD:.0f} ceiling"))
    if previous:
        pct = (price - previous) / previous * 100
        if abs(pct) >= JUMP_CRITICAL_PCT:
            flags.append(Flag("price_jump_critical", "critical",
                              f"{pct:+.1f}% vs previous ${previous:.2f}; verify the "
                              "listing is still the same new-condition configuration"))
        elif abs(pct) >= JUMP_WARN_PCT:
            flags.append(Flag("price_jump_warning", "warning",
                              f"{pct:+.1f}% vs previous ${previous:.2f}"))
    return flags


def check_spec_drift(declared: NormalizedSpec, listing_title: str | None) -> list[Flag]:
    """Compare the configuration we registered against the live listing title."""
    if not listing_title:
        return []
    observed = parse_spec(title=listing_title)
    flags: list[Flag] = []
    for field in ("cpu", "ram_gb", "storage_gb", "os", "form_factor"):
        want, got = getattr(declared, field), getattr(observed, field)
        if want is not None and got is not None and want != got:
            flags.append(Flag("spec_drift", "critical",
                              f"{field}: registered {want!r} but listing now reads {got!r}"))
    return flags


def check_completeness(spec: NormalizedSpec) -> list[Flag]:
    if spec.complete:
        return []
    return [Flag("spec_incomplete", "warning",
                 "Cannot derive an equivalence key; missing " + ", ".join(spec.missing))]


def check_group_size(group_key: str, member_count: int) -> list[Flag]:
    if group_key == "unmatched":
        return []
    if member_count < 2:
        return [Flag("group_too_small", "warning",
                     f"Group {group_key} has {member_count} member; a comparison "
                     "needs at least two products")]
    return []


def check_staleness(hours_since_last: float | None, *, max_hours: float = 24.0) -> list[Flag]:
    if hours_since_last is None:
        return []
    if hours_since_last > max_hours:
        return [Flag("data_stale", "warning",
                     f"Last successful capture was {hours_since_last:.1f}h ago")]
    return []
