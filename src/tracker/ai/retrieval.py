"""Retrieval over product descriptions.

Why this exists: the tool-calling agent answers questions that decompose into
queries -- prices, spreads, history. It cannot answer "which of these has a
360-degree hinge", because that fact lives in prose, not in a column.

Why it is not a vector database: six products produce a few dozen passages.
Loading an embedding model and a vector store for that would be architecture as
decoration. BM25 over the same corpus is exact, has no dependencies, no cold
start, and is auditable -- you can see which term matched. The interface is the
part that matters; swapping the scorer for embeddings later touches one class.

What retrieval buys here is not recall. It is grounding: the generation step
receives passages and is told it may use nothing else, so a question the corpus
cannot answer produces "not stated" rather than a plausible invention.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# Words that appear in nearly every listing carry no discriminating signal.
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "your",
        "you",
        "our",
        "we",
        "they",
        "them",
        "their",
        "these",
        "those",
        "там",
    ]
)

_TOKEN = re.compile(r"[a-z0-9][a-z0-9.\-]*")

# BM25 parameters. k1 controls how quickly term frequency saturates; b controls
# how strongly long passages are penalised. These are the standard defaults and
# are not tuned -- with a corpus this small, tuning them would be fitting noise.
K1 = 1.5
B = 0.75


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, keeping model numbers like 226v and 1tb intact."""
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


@dataclass
class Passage:
    """One retrievable unit, with enough metadata to cite it."""

    doc_id: str
    text: str
    sku: str = ""
    brand: str = ""
    model_name: str = ""
    section: str = ""
    tokens: list[str] = field(default_factory=list)

    def citation(self) -> str:
        who = f"{self.brand} {self.model_name}".strip() or self.doc_id
        return f"{who} ({self.sku}) — {self.section}" if self.section else who


@dataclass
class Hit:
    passage: Passage
    score: float
    matched_terms: list[str]


class BM25Index:
    """A small, transparent lexical index.

    Transparent matters: every hit reports which query terms matched it, so a
    reviewer can tell whether a passage was retrieved for a good reason.
    """

    def __init__(self, passages: list[Passage]) -> None:
        self.passages = [
            Passage(**{**p.__dict__, "tokens": p.tokens or tokenize(p.text)})
            for p in passages
        ]
        self.doc_freq: Counter[str] = Counter()
        for p in self.passages:
            self.doc_freq.update(set(p.tokens))
        self.n = len(self.passages)
        self.avg_len = (
            sum(len(p.tokens) for p in self.passages) / self.n if self.n else 0.0
        )

    def _idf(self, term: str) -> float:
        df = self.doc_freq.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def search(
        self, query: str, *, top_k: int = 4, min_score: float = 0.1
    ) -> list[Hit]:
        """Return the best passages, or an empty list if none is relevant.

        An empty result is a valid answer. Returning the least-bad passage for a
        query the corpus cannot serve is how a retrieval layer starts producing
        confident nonsense downstream.
        """
        terms = tokenize(query)
        if not terms or not self.n:
            return []

        hits: list[Hit] = []
        for passage in self.passages:
            counts = Counter(passage.tokens)
            length = len(passage.tokens)
            score = 0.0
            matched: list[str] = []
            for term in set(terms):
                tf = counts.get(term, 0)
                if not tf:
                    continue
                matched.append(term)
                denominator = tf + K1 * (1 - B + B * length / (self.avg_len or 1))
                score += self._idf(term) * (tf * (K1 + 1)) / denominator
            if score > min_score:
                hits.append(
                    Hit(
                        passage=passage,
                        score=round(score, 4),
                        matched_terms=sorted(matched),
                    )
                )

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]


def chunk_product(product: dict[str, Any], *, max_words: int = 90) -> list[Passage]:
    """Split one product's text into passages small enough to cite precisely.

    Chunking is by sentence group rather than by character count: a passage cut
    mid-sentence loses the qualifier that makes it true ("...is *not* a
    touchscreen"), which is exactly the failure grounding is supposed to prevent.
    """
    sku = str(product.get("sku", ""))
    brand = str(product.get("brand", ""))
    model = str(product.get("model_name", ""))
    passages: list[Passage] = []

    sections = {
        "title": product.get("title", ""),
        "description": product.get("description", ""),
        "specifications": product.get("specifications", ""),
    }

    for section, text in sections.items():
        text = (text or "").strip()
        if not text:
            continue
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        current: list[str] = []
        count = 0
        for sentence in sentences:
            words = len(sentence.split())
            if current and count + words > max_words:
                passages.append(
                    _passage(
                        sku, brand, model, section, " ".join(current), len(passages)
                    )
                )
                current, count = [], 0
            current.append(sentence)
            count += words
        if current:
            passages.append(
                _passage(sku, brand, model, section, " ".join(current), len(passages))
            )
    return passages


def _passage(
    sku: str, brand: str, model: str, section: str, text: str, index: int
) -> Passage:
    return Passage(
        doc_id=f"{sku}-{section}-{index}",
        text=text,
        sku=sku,
        brand=brand,
        model_name=model,
        section=section,
    )


def build_index(products: list[dict[str, Any]]) -> BM25Index:
    passages: list[Passage] = []
    for product in products:
        passages.extend(chunk_product(product))
    return BM25Index(passages)
