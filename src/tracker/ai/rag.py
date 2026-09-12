"""Grounded question answering over product descriptions.

The retrieval step decides what the model is allowed to know. The generation
step is told, in the system prompt and by construction, that the passages are
the only admissible evidence.

Two behaviours are enforced outside the prompt, because a prompt is a request
and not a guarantee:

  - If retrieval returns nothing, the model is never called. An empty corpus
    match produces "not stated in the product information", not an answer.
  - Every answer carries the passages it was given. A reader can check the
    claim against the source text without leaving the page.

The prompt asks the model to cite passage ids inline. Those citations are
verified against the ids actually supplied; an id the model invented is
reported rather than displayed as a source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .client import GeminiClient, LLMError
from .retrieval import BM25Index, Hit

SYSTEM = """\
You answer questions about PCs using only the passages provided.

Rules:
- Use nothing except the passages. You have no other knowledge of these products.
- If the passages do not answer the question, say exactly what is missing. Do \
not substitute general knowledge about laptops.
- Cite the passage id in square brackets after each claim, e.g. [12349089-description-0].
- Be brief. Two or three sentences unless the question needs more.
- Do not compare prices; you cannot see prices.
"""

PROMPT = """\
PASSAGES:
{passages}

QUESTION: {question}

Answer using only the passages above, citing ids in square brackets."""

_CITATION = re.compile(r"\[([a-z0-9\-]+)\]", re.I)

NO_EVIDENCE = (
    "The product information does not cover that. Nothing in the indexed "
    "descriptions addresses this question."
)


@dataclass
class GroundedAnswer:
    text: str
    hits: list[Hit] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)
    invented_citations: list[str] = field(default_factory=list)
    called_model: bool = True

    @property
    def has_evidence(self) -> bool:
        return bool(self.hits)

    @property
    def is_clean(self) -> bool:
        """No citation that was not supplied to the model."""
        return not self.invented_citations

    def sources(self) -> list[str]:
        return [h.passage.citation() for h in self.hits]


class GroundedAnswerer:
    def __init__(
        self, index: BM25Index, client: GeminiClient | None = None, *, top_k: int = 4
    ) -> None:
        self.index = index
        self.client = client or GeminiClient()
        self.top_k = top_k

    @property
    def available(self) -> bool:
        return self.client.available

    def ask(self, question: str) -> GroundedAnswer:
        hits = self.index.search(question, top_k=self.top_k)
        if not hits:
            # Refusing here, before any generation, is the cheapest and most
            # reliable place to prevent an invented answer.
            return GroundedAnswer(text=NO_EVIDENCE, hits=[], called_model=False)

        block = "\n\n".join(
            f"[{h.passage.doc_id}] ({h.passage.citation()})\n{h.passage.text}"
            for h in hits
        )
        response = self.client.generate(
            PROMPT.format(passages=block, question=question),
            system=SYSTEM,
            json_only=False,
        )
        text = response.text.strip()
        if not text:
            raise LLMError("model returned an empty answer")

        supplied = {h.passage.doc_id for h in hits}
        cited = _CITATION.findall(text)
        return GroundedAnswer(
            text=text,
            hits=hits,
            cited=[c for c in cited if c in supplied],
            invented_citations=[c for c in cited if c not in supplied],
        )
