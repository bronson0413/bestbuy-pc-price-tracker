"""A bounded tool-calling loop over the tracker's own data.

The loop is short on purpose: the model may call tools, see their results, and
call again, up to a fixed limit. It is not an open-ended agent. Every step is
recorded, so the answer a user reads can be traced back to the exact queries
that produced it.

Three properties are enforced rather than requested:

  - The model cannot reach the database except through the registry, and every
    call is schema-validated before execution.
  - A rejected call is returned to the model as an error it must handle, not
    silently corrected. Repairing a malformed call would hide the failure.
  - The final answer must be grounded in tool output. The system prompt says so,
    and the transcript makes a violation visible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .client import GeminiClient, LLMError
from .tools import Tool, ToolError, execute

MAX_STEPS = 4

SYSTEM = """\
You answer questions about a Best Buy PC price tracker by calling tools.

Rules:
- Never state a price, a product or a date that did not come from a tool result.
- If the tools cannot answer the question, say so plainly. Do not fill the gap \
from general knowledge.
- Prices are in USD and come from a specific observation window; do not \
extrapolate beyond it.
- When you are done, reply with JSON: \
{"answer": "...", "grounded_in": ["tool_name", ...]}

To call a tool, reply with JSON only:
{"tool": "tool_name", "args": {...}}
"""


@dataclass
class Step:
    """One turn of the loop, kept for the transcript."""

    tool: str
    args: dict[str, Any]
    ok: bool
    result: Any = None
    error: str | None = None


@dataclass
class AgentAnswer:
    text: str
    steps: list[Step] = field(default_factory=list)
    grounded_in: list[str] = field(default_factory=list)
    raw_replies: list[str] = field(default_factory=list)

    @property
    def used_tools(self) -> list[str]:
        return [s.tool for s in self.steps if s.ok]

    @property
    def is_grounded(self) -> bool:
        """An answer with no successful tool call is not grounded in data."""
        return bool(self.used_tools)


class ToolCallingAgent:
    def __init__(
        self,
        registry: dict[str, Tool],
        client: GeminiClient | None = None,
        *,
        max_steps: int = MAX_STEPS,
    ) -> None:
        self.registry = registry
        self.client = client or GeminiClient()
        self.max_steps = max_steps

    @property
    def available(self) -> bool:
        return self.client.available

    def ask(self, question: str) -> AgentAnswer:
        catalogue = json.dumps([t.schema() for t in self.registry.values()], indent=2)
        transcript: list[str] = [
            f"AVAILABLE TOOLS:\n{catalogue}",
            f"QUESTION: {question}",
        ]
        answer = AgentAnswer(text="")

        for _ in range(self.max_steps):
            reply = self.client.generate("\n\n".join(transcript), system=SYSTEM)
            answer.raw_replies.append(reply.text)
            try:
                payload = reply.as_json()
            except LLMError:
                # Unparseable output ends the loop; the raw text is retained so
                # a reviewer can see what the model actually said.
                answer.text = reply.text.strip()
                return answer

            if not isinstance(payload, dict):
                answer.text = str(payload)
                return answer

            if "tool" in payload:
                step = self._run(payload)
                answer.steps.append(step)
                transcript.append(json.dumps(payload))
                transcript.append(
                    f"TOOL RESULT:\n{_truncate(json.dumps(step.result, default=str))}"
                    if step.ok
                    else f"TOOL ERROR: {step.error}\nFix the call or explain that "
                    "you cannot answer."
                )
                continue

            answer.text = str(payload.get("answer", "")).strip()
            grounded = payload.get("grounded_in") or []
            answer.grounded_in = (
                [str(g) for g in grounded] if isinstance(grounded, list) else []
            )
            return answer

        answer.text = (
            f"Stopped after {self.max_steps} tool calls without reaching an answer."
        )
        return answer

    def _run(self, payload: dict[str, Any]) -> Step:
        name = str(payload.get("tool", ""))
        args = payload.get("args") or {}
        if not isinstance(args, dict):
            return Step(
                tool=name,
                args={},
                ok=False,
                error=f"args must be an object, got {type(args).__name__}",
            )
        try:
            return Step(
                tool=name, args=args, ok=True, result=execute(self.registry, name, args)
            )
        except ToolError as exc:
            return Step(tool=name, args=args, ok=False, error=str(exc))
        except Exception as exc:  # a broken handler must not end the session
            return Step(
                tool=name, args=args, ok=False, error=f"{type(exc).__name__}: {exc}"
            )


def _truncate(text: str, limit: int = 4000) -> str:
    """Tool results can be large; the model does not need all of it."""
    return text if len(text) <= limit else text[:limit] + "\n...(truncated)"
