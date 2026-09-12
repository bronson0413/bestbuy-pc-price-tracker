import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from tracker.normalize import (
    normalize_cpu,
    normalize_os,
    normalize_ram_gb,
    normalize_storage_gb,
    parse_spec,
)

HP = 'HP OmniBook X Flip 14" 2-in-1, Intel Core Ultra 5 226V, 16GB RAM, 512GB SSD, Windows 11 Home'
LENOVO = (
    'Lenovo Yoga 7 2-in-1 14" Intel Core Ultra 5 226V 16GB Memory 512GB SSD Win 11 Home'
)
DELL = "Dell Inspiron 14 2-in-1 Laptop, Intel Core Ultra 5 226V, 16GB, 512GB SSD, Windows 11 Home"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Intel Core Ultra 5 226V", "intel-ultra5-226v"),
        ("Intel Core Ultra 7 258V", "intel-ultra7-258v"),
        ("Intel Core i7-1355U", "intel-i7-1355u"),
        ("AMD Ryzen AI 9 365", "amd-ryzenai9-365"),
        ("AMD Ryzen 7 8840HS", "amd-ryzen7-8840hs"),
        ("Snapdragon X Elite X1E-78-100", "qc-snapdragon-x-elite-x1e-78-100"),
        ("Some Unlisted Processor", None),
    ],
)
def test_cpu_tokens(text, expected):
    assert normalize_cpu(text) == expected


def test_storage_is_not_mistaken_for_memory():
    assert normalize_ram_gb("512GB SSD") is None
    assert normalize_ram_gb("16GB RAM, 512GB SSD") == 16
    assert normalize_storage_gb("1TB PCIe NVMe SSD") == 1024


def test_os_variants():
    assert normalize_os("Windows 11 Home") == "windows-11-home"
    assert normalize_os("Windows 11 Pro") == "windows-11-pro"
    assert normalize_os("Win 11 Home") == "windows-11-home"


def test_same_config_across_brands_shares_one_group():
    keys = {parse_spec(title=t).group_key() for t in (HP, LENOVO, DELL)}
    assert len(keys) == 1
    assert (
        keys.pop() == "intel-ultra5-226v|16gb|512gb|windows-11-home|laptop|convertible"
    )


def test_different_storage_splits_the_group():
    a = parse_spec(title=HP).group_key()
    b = parse_spec(title=HP.replace("512GB SSD", "1TB SSD")).group_key()
    assert a != b


def test_incomplete_spec_never_groups():
    spec = parse_spec(title="Some Laptop With No Useful Details")
    assert not spec.complete
    assert spec.group_key() == "unmatched"
    assert "cpu" in spec.missing


def test_declared_fields_win_over_title():
    spec = parse_spec(title=HP, storage="1TB SSD")
    assert spec.storage_gb == 1024


def test_tier_key_groups_processor_classes_but_not_configurations():
    ultra5_226v = parse_spec(title=HP.replace("2-in-1", "Laptop"))
    ultra5_236v = parse_spec(
        title=HP.replace("2-in-1", "Laptop").replace("226V", "236V")
    )
    # Different SKUs, same tier: strict grouping separates them, tier does not.
    assert ultra5_226v.group_key() != ultra5_236v.group_key()
    assert ultra5_226v.tier_key() == ultra5_236v.tier_key()


def test_tier_key_still_separates_different_processor_classes():
    ultra5 = parse_spec(title=HP.replace("2-in-1", "Laptop"))
    ultra7 = parse_spec(
        title=HP.replace("2-in-1", "Laptop").replace("Ultra 5 226V", "Ultra 7 258V")
    )
    assert ultra5.tier_key() != ultra7.tier_key()


def test_tier_key_still_respects_memory_and_os():
    base = parse_spec(title=HP.replace("2-in-1", "Laptop"))
    more_ram = parse_spec(
        title=HP.replace("2-in-1", "Laptop").replace("16GB RAM", "32GB RAM")
    )
    pro = parse_spec(
        title=HP.replace("2-in-1", "Laptop").replace(
            "Windows 11 Home", "Windows 11 Pro"
        )
    )
    assert base.tier_key() != more_ram.tier_key()
    assert base.tier_key() != pro.tier_key()
