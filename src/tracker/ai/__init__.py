"""Language-model components.

Three rules govern everything in this package:

1. A model never decides a price and never decides whether two machines are
   comparable. It proposes structure; the deterministic layer disposes.
2. Every model output is validated against the same schema the rule-based
   parser produces. Output that does not conform is rejected, not repaired.
3. Where the model and the rules disagree, neither wins. The disagreement is
   raised for a human.
"""

from .agent import AgentAnswer, Step, ToolCallingAgent
from .client import GeminiClient, LLMError, LLMResponse
from .corpus import build_corpus, load_products
from .ensemble import Reconciliation, Verdict, reconcile
from .explain import Explanation, FlagExplainer, context_for
from .extract import ExtractionResult, SpecExtractor
from .rag import GroundedAnswer, GroundedAnswerer
from .retrieval import BM25Index, Hit, Passage, build_index
from .tools import Tool, ToolError, build_registry, execute

__all__ = [
    "AgentAnswer",
    "BM25Index",
    "Explanation",
    "ExtractionResult",
    "FlagExplainer",
    "GeminiClient",
    "GroundedAnswer",
    "GroundedAnswerer",
    "Hit",
    "LLMError",
    "LLMResponse",
    "Passage",
    "Reconciliation",
    "SpecExtractor",
    "Step",
    "Tool",
    "ToolCallingAgent",
    "ToolError",
    "Verdict",
    "build_corpus",
    "context_for",
    "build_index",
    "build_registry",
    "load_products",
    "execute",
    "reconcile",
]
