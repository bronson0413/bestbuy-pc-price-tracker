"""Assemble the retrieval corpus from the tracked product configuration.

The corpus is built from the same YAML the collector reads, so a product cannot
be answerable by the assistant without also being tracked. Keeping one source
of truth avoids the failure where the assistant describes a machine the price
history knows nothing about.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .retrieval import BM25Index, build_index


def load_products(
    config: Path, descriptions: Path | None = None
) -> list[dict[str, Any]]:
    """Merge the tracked configuration with any stored long-form descriptions."""
    cfg = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    products = cfg.get("products", []) or []

    extra: dict[str, Any] = {}
    if descriptions and descriptions.exists():
        loaded = yaml.safe_load(descriptions.read_text(encoding="utf-8")) or {}
        extra = loaded.get("descriptions", {}) or {}

    merged: list[dict[str, Any]] = []
    for product in products:
        sku = str(product.get("sku", ""))
        declared = product.get("declared", {}) or {}
        merged.append(
            {
                "sku": sku,
                "brand": product.get("brand", ""),
                "model_name": product.get("model_name", ""),
                "title": product.get("title", ""),
                "description": extra.get(sku, ""),
                # The declared specification is indexed as prose so that
                # "which ones run Windows 11 Pro" is answerable from retrieval.
                "specifications": _spec_sentence(product, declared),
            }
        )
    return merged


def _spec_sentence(product: dict[str, Any], declared: dict[str, Any]) -> str:
    parts = [
        f"{product.get('brand', '')} {product.get('model_name', '')}".strip(),
        f"has a {declared['cpu']} processor." if declared.get("cpu") else "",
        f"It has {declared['ram']} of memory." if declared.get("ram") else "",
        f"Storage is {declared['storage']}." if declared.get("storage") else "",
        f"It runs {declared['os']}." if declared.get("os") else "",
        f"The form factor is {declared['form_factor']}."
        if declared.get("form_factor")
        else "",
        f"The screen is {product['screen_inches']} inches."
        if product.get("screen_inches")
        else "",
        f"Sold by {product['seller']}." if product.get("seller") else "",
    ]
    return " ".join(p for p in parts if p)


def build_corpus(config: Path, descriptions: Path | None = None) -> BM25Index:
    return build_index(load_products(config, descriptions))
