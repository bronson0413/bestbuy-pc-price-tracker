"""Retrieval decides what may be said. These tests pin that boundary."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from tracker.ai.client import LLMResponse
from tracker.ai.rag import NO_EVIDENCE, GroundedAnswerer
from tracker.ai.retrieval import BM25Index, build_index, chunk_product, tokenize

PRODUCTS = [
    {
        "sku": "12349089",
        "brand": "HP",
        "model_name": "OmniBook 7 Flip 16",
        "title": 'HP OmniBook 7 Laptop Computer 16" Touch Screen Intel Core Ultra 5 226V',
        "description": (
            "It has a versatile 360-degree hinge offering four intuitive modes: "
            "Laptop mode, Yoga mode, Flip convertible mode and Tablet spin mode. "
            "The battery is 4-cell, 68 Wh. It runs Windows 11 Home."
        ),
    },
    {
        "sku": "6603654",
        "brand": "ASUS",
        "model_name": "ExpertBook P5",
        "title": "ASUS ExpertBook P5 2.5K Laptop Intel Core Ultra 5 226V",
        "description": (
            "Engineered to meet the MIL-STD 810H US Military standard for "
            "durability. It carries a three-year warranty. It runs Windows 11 Pro."
        ),
    },
]


class StubClient:
    available = True

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.calls = 0
        self.last_prompt = ""

    def citing(self, doc_id: str) -> StubClient:
        """Reply with a citation to a specific passage."""
        self.text = f"An answer. [{doc_id}]"
        return self

    def generate(
        self, prompt: str, *, system: str | None = None, json_only: bool = True
    ) -> LLMResponse:
        self.calls += 1
        self.last_prompt = prompt
        return LLMResponse(text=self.text, model="stub", latency_s=0.0)


@pytest.fixture
def index() -> BM25Index:
    return build_index(PRODUCTS)


# --- tokenisation and chunking ---------------------------------------------


def test_model_numbers_survive_tokenisation() -> None:
    tokens = tokenize("Intel Core Ultra 5 226V with a 1TB SSD")
    assert "226v" in tokens and "1tb" in tokens
    assert "the" not in tokens and "with" not in tokens


def test_passages_carry_enough_metadata_to_cite() -> None:
    passages = chunk_product(PRODUCTS[0])
    assert passages
    first = passages[0]
    assert first.sku == "12349089"
    assert "OmniBook" in first.citation()
    assert first.doc_id.startswith("12349089-")


def test_long_text_is_split_without_cutting_a_sentence(index: BM25Index) -> None:
    long_product = {
        "sku": "x",
        "brand": "B",
        "model_name": "M",
        "description": " ".join(f"Sentence number {i} is complete." for i in range(40)),
    }
    passages = chunk_product(long_product, max_words=20)
    assert len(passages) > 1
    # Every passage ends on a sentence boundary, so no qualifier is severed.
    assert all(p.text.rstrip().endswith(".") for p in passages)


# --- retrieval behaviour ----------------------------------------------------


def test_a_distinctive_term_retrieves_the_right_product(index: BM25Index) -> None:
    hits = index.search("which one has a 360 degree hinge")
    assert hits and hits[0].passage.sku == "12349089"
    assert "hinge" in hits[0].matched_terms


def test_a_query_the_corpus_cannot_serve_returns_nothing(index: BM25Index) -> None:
    # No passage mentions thunderbolt, so returning the least-bad match would be
    # the start of a confident wrong answer.
    assert index.search("thunderbolt docking station bandwidth") == []


def test_hits_report_why_they_matched(index: BM25Index) -> None:
    hits = index.search("military durability standard")
    assert hits
    assert set(hits[0].matched_terms) & {"military", "durability", "standard"}


def test_an_empty_query_retrieves_nothing(index: BM25Index) -> None:
    assert index.search("") == []
    assert index.search("the and of") == []  # stopwords only


def test_lexical_matching_cannot_distinguish_adjacent_versions() -> None:
    """A known weakness, pinned so it is not mistaken for correct behaviour.

    BM25 matches terms, not meaning. "Wi-Fi 7" and "Wi-Fi 6E" share the token
    "wi-fi", so a query for one can retrieve the other. Embeddings would narrow
    this; with a corpus this small the trade-off was judged not worth the
    dependency, and the limitation is documented instead.
    """
    products = [
        {
            "sku": "a",
            "brand": "A",
            "model_name": "One",
            "description": "Wireless options include Wi-Fi 7 and Bluetooth.",
        },
        {
            "sku": "b",
            "brand": "B",
            "model_name": "Two",
            "description": "Connectivity includes Wi-Fi 6E and Bluetooth 5.3.",
        },
    ]
    hits = build_index(products).search("which one has Wi-Fi 7")
    assert len(hits) == 2  # both match on "wi-fi"
    # The correct product does rank first, but the wrong one is still returned.
    assert hits[0].passage.sku == "a"


# --- grounding --------------------------------------------------------------


def test_no_retrieval_means_the_model_is_never_called(index: BM25Index) -> None:
    client = StubClient("An answer.")
    answer = GroundedAnswerer(index, client).ask("what is the resale value")
    assert answer.text == NO_EVIDENCE
    assert client.calls == 0  # cheapest place to refuse
    assert not answer.has_evidence
    assert answer.called_model is False


def test_the_model_receives_the_passages_and_nothing_else(index: BM25Index) -> None:
    client = StubClient("An answer.")
    GroundedAnswerer(index, client).ask("tell me about the hinge")
    assert "360-degree hinge" in client.last_prompt
    assert "PASSAGES:" in client.last_prompt


def test_a_valid_citation_is_recorded(index: BM25Index) -> None:
    retrieved = index.search("the hinge")[0].passage.doc_id
    answer = GroundedAnswerer(index, StubClient().citing(retrieved)).ask("the hinge")
    assert answer.cited == [retrieved]
    assert answer.is_clean


def test_an_invented_citation_is_reported_not_displayed_as_a_source(
    index: BM25Index,
) -> None:
    client = StubClient("It has a hinge. [99999999-description-0]")
    answer = GroundedAnswerer(index, client).ask("the hinge")
    assert answer.invented_citations == ["99999999-description-0"]
    assert not answer.is_clean  # the caller can warn on this
    assert answer.cited == []


def test_sources_are_human_readable(index: BM25Index) -> None:
    answer = GroundedAnswerer(index, StubClient("An answer.")).ask("the hinge")
    assert any("OmniBook" in s for s in answer.sources())
