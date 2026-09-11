"""Specification normalisation and equivalence-group derivation.

The equivalence rule is code, not prose: two listings are comparable only when
their normalised (cpu, ram, storage, os, device_type, form_factor) tuple is
identical. Every rule is deliberately conservative -- when a value cannot be
parsed with confidence we return None, which pushes the product into the
"unmatched" bucket and out of the comparison rather than guessing silently.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, asdict

_INTEL_ULTRA = re.compile(r"core\s*ultra\s*(?P<tier>\d)\s*(?P<sku>\d{3}[a-z]{0,2})", re.I)
_INTEL_CORE = re.compile(r"core\s*i(?P<tier>[3579])[\s-]*(?P<sku>\d{4,5}[a-z]{0,2})", re.I)
_AMD_RYZEN = re.compile(r"ryzen\s*(?P<ai>ai\s*)?(?P<tier>\d)\s*(?P<sku>\d{3,4}[a-z]{0,3})", re.I)
_SNAPDRAGON = re.compile(r"snapdragon\s*x\s*(?P<line>elite|plus)\s*(?P<sku>x\d[a-z]?[\d-]*)?", re.I)

_STORAGE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>gb|tb)\s*(?:pcie\s*)?(?:nvme\s*)?(?:ssd|solid state)", re.I)
_RAM = re.compile(r"(?P<num>\d+)\s*gb\s*(?:lpddr\d x?\s*|ddr\d x?\s*|unified\s*)?(?:memory|ram)\b", re.I)
_RAM_LOOSE = re.compile(r"\b(?P<num>4|8|12|16|24|32|48|64|96|128)\s*gb\b", re.I)

_FORM_FACTORS = {
    "convertible": ("2-in-1", "2 in 1", "convertible", "flip", "360-degree"),
    "detachable": ("detachable", "tablet with keyboard"),
    "desktop-sff": ("small form factor", " sff", "mini pc", "tiny desktop"),
    "desktop-tower": ("tower", "gaming desktop"),
    "aio": ("all-in-one", "all in one"),
    "clamshell": ("clamshell", "traditional laptop", "laptop", "notebook"),
}
_DEVICE_TYPES = {
    "aio": ("all-in-one", "all in one"),
    "desktop": ("desktop", "tower", "mini pc", " sff"),
    "laptop": ("laptop", "notebook", "2-in-1", "convertible", "chromebook"),
}


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    for dash in ("\u2011", "\u2013", "\u2014"):
        text = text.replace(dash, "-")
    return re.sub(r"\s+", " ", text).strip().lower()


def normalize_cpu(text: str | None) -> str | None:
    """Return a stable CPU token such as ``intel-ultra5-226v``."""
    t = _clean(text)
    if not t:
        return None
    if m := _INTEL_ULTRA.search(t):
        return f"intel-ultra{m.group('tier')}-{m.group('sku').lower()}"
    if m := _INTEL_CORE.search(t):
        return f"intel-i{m.group('tier')}-{m.group('sku').lower()}"
    if m := _AMD_RYZEN.search(t):
        prefix = "amd-ryzenai" if m.group("ai") else "amd-ryzen"
        return f"{prefix}{m.group('tier')}-{m.group('sku').lower()}"
    if m := _SNAPDRAGON.search(t):
        sku = (m.group("sku") or "").replace(" ", "").lower()
        return f"qc-snapdragon-x-{m.group('line').lower()}" + (f"-{sku}" if sku else "")
    return None


def normalize_ram_gb(text: str | None) -> int | None:
    t = _clean(text)
    if not t:
        return None
    if m := _RAM.search(t):
        return int(m.group("num"))
    # Strip storage tokens first so "512GB SSD" is never read as memory.
    stripped = _STORAGE.sub(" ", t)
    if m := _RAM_LOOSE.search(stripped):
        return int(m.group("num"))
    return None


def normalize_storage_gb(text: str | None) -> int | None:
    t = _clean(text)
    if not t:
        return None
    if m := _STORAGE.search(t):
        num = float(m.group("num"))
        return int(num * 1024) if m.group("unit").lower() == "tb" else int(num)
    return None


def normalize_os(text: str | None) -> str | None:
    t = _clean(text)
    if not t:
        return None
    if "windows 11 pro" in t or "win 11 pro" in t:
        return "windows-11-pro"
    if "windows 11" in t or re.search(r"\bwin\s*11\b", t):
        return "windows-11-home"
    if "windows" in t:
        return "windows"
    if "chrome os" in t or "chromeos" in t:
        return "chromeos"
    if "macos" in t or "mac os" in t:
        return "macos"
    return None


def _match_vocab(text: str, vocab: dict[str, tuple[str, ...]]) -> str | None:
    t = _clean(text)
    for key, needles in vocab.items():
        if any(n in t for n in needles):
            return key
    return None


def normalize_form_factor(text: str | None) -> str | None:
    return _match_vocab(text or "", _FORM_FACTORS)


def normalize_device_type(text: str | None) -> str | None:
    return _match_vocab(text or "", _DEVICE_TYPES)


# Processor tiers. Two machines in the same tier are not identical, but a buyer
# choosing between them is making a price decision rather than a spec decision.
_TIER = re.compile(r"^(intel-ultra\d|intel-i\d|amd-ryzenai\d|amd-ryzen\d|qc-snapdragon-x-\w+)")


def cpu_tier(cpu: str | None) -> str | None:
    """Reduce a CPU token to its performance tier: intel-ultra5-226v -> intel-ultra5."""
    if not cpu:
        return None
    m = _TIER.match(cpu)
    return m.group(1) if m else cpu


@dataclass(frozen=True)
class NormalizedSpec:
    cpu: str | None
    ram_gb: int | None
    storage_gb: int | None
    os: str | None
    device_type: str | None
    form_factor: str | None

    @property
    def complete(self) -> bool:
        return all(v is not None for v in asdict(self).values())

    @property
    def missing(self) -> list[str]:
        return [k for k, v in asdict(self).items() if v is None]

    def group_key(self) -> str:
        """Deterministic equivalence key. Incomplete specs never group."""
        if not self.complete:
            return "unmatched"
        return "|".join([
            self.cpu, f"{self.ram_gb}gb", f"{self.storage_gb}gb",
            self.os, self.device_type, self.form_factor,
        ])

    def tier_key(self) -> str:
        """A looser key: same processor tier and configuration, any SKU.

        The strict key answers "are these the same machine?". This one answers
        "would a buyer cross-shop these?" -- which is the question a price
        tracker actually exists to serve. Both are kept; neither replaces the
        other, and the app lets a reviewer switch between them.
        """
        if not self.complete:
            return "unmatched"
        return "|".join([
            cpu_tier(self.cpu), f"{self.ram_gb}gb", f"{self.storage_gb}gb",
            self.os, self.device_type, self.form_factor,
        ])

    def label(self) -> str:
        if not self.complete:
            return "Unmatched / incomplete specs"
        return (f"{self.cpu} · {self.ram_gb}GB RAM · {self.storage_gb}GB SSD · "
                f"{self.os} · {self.form_factor}")


def parse_spec(*, title: str | None = None, cpu: str | None = None,
               ram: str | None = None, storage: str | None = None,
               os_: str | None = None, device_type: str | None = None,
               form_factor: str | None = None) -> NormalizedSpec:
    """Parse declared fields, falling back to the listing title for any gap."""
    t = title or ""
    return NormalizedSpec(
        cpu=normalize_cpu(cpu) or normalize_cpu(t),
        ram_gb=normalize_ram_gb(ram) or normalize_ram_gb(t),
        storage_gb=normalize_storage_gb(storage) or normalize_storage_gb(t),
        os=normalize_os(os_) or normalize_os(t),
        device_type=normalize_device_type(device_type) or normalize_device_type(t),
        form_factor=normalize_form_factor(form_factor) or normalize_form_factor(t),
    )
