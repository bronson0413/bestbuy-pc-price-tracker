import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tracker.normalize import parse_spec
from tracker.validate import (
    check_completeness,
    check_group_size,
    check_price,
    check_spec_drift,
    check_staleness,
)

HP = 'HP OmniBook X Flip 14" 2-in-1, Intel Core Ultra 5 226V, 16GB RAM, 512GB SSD, Windows 11 Home'


def rules(flags):
    return {f.rule for f in flags}


def test_missing_and_implausible_prices_are_critical():
    assert rules(check_price(None)) == {"price_missing"}
    assert rules(check_price(0)) == {"price_nonpositive"}
    assert "price_below_floor" in rules(check_price(19.99))


def test_small_moves_pass_and_large_moves_escalate():
    assert check_price(999.0, previous=989.0) == []
    assert "price_jump_warning" in rules(check_price(1150.0, previous=999.0))
    assert "price_jump_critical" in rules(check_price(499.0, previous=999.0))


def test_spec_drift_detected_when_listing_changes_configuration():
    declared = parse_spec(title=HP)
    same = check_spec_drift(declared, HP)
    drifted = check_spec_drift(declared, HP.replace("512GB SSD", "1TB SSD"))
    assert same == []
    assert rules(drifted) == {"spec_drift"}


def test_incomplete_spec_is_flagged():
    spec = parse_spec(title="Mystery PC")
    assert rules(check_completeness(spec)) == {"spec_incomplete"}


def test_single_member_group_is_not_a_comparison():
    assert rules(check_group_size("a|b", 1)) == {"group_too_small"}
    assert check_group_size("a|b", 2) == []
    assert check_staleness(30.0)[0].rule == "data_stale"
    assert check_staleness(3.0) == []
