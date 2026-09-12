"""Tool definitions the model may call, and the code that executes them.

The pattern is deliberate. The model does not answer questions about prices; it
chooses a tool and fills in its arguments. Execution is ordinary Python against
the database, so the numbers a user sees are never generated text.

That split is what makes the feature auditable. Every answer decomposes into
"which tool was chosen, with which arguments" -- both of which are shown to the
user -- followed by a deterministic query. A model that picks the wrong tool
produces a visibly wrong query rather than a plausible wrong number.

Arguments are validated against each tool's schema before execution. A model
that invents a parameter, omits a required one, or supplies the wrong type gets
its call rejected, not repaired.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import storage as st


class ToolError(RuntimeError):
    """A tool call could not be validated or executed."""


@dataclass(frozen=True)
class Param:
    name: str
    type: str  # "string" | "number" | "integer" | "boolean"
    description: str
    required: bool = False
    enum: tuple[str, ...] | None = None


@dataclass
class Tool:
    name: str
    description: str
    params: tuple[Param, ...]
    handler: Callable[..., Any] = field(repr=False)

    def schema(self) -> dict[str, Any]:
        """The declaration sent to the model."""
        properties: dict[str, Any] = {}
        for p in self.params:
            spec: dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                spec["enum"] = list(p.enum)
            properties[p.name] = spec
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": [p.name for p in self.params if p.required],
            },
        }

    def validate(self, args: dict[str, Any]) -> dict[str, Any]:
        """Check the model's arguments before anything touches the database."""
        known = {p.name: p for p in self.params}
        unknown = set(args) - set(known)
        if unknown:
            raise ToolError(f"{self.name}: unknown argument(s) {sorted(unknown)}")

        missing = [
            p.name for p in self.params if p.required and args.get(p.name) is None
        ]
        if missing:
            raise ToolError(f"{self.name}: missing required argument(s) {missing}")

        cleaned: dict[str, Any] = {}
        for name, value in args.items():
            if value is None:
                continue
            param = known[name]
            cleaned[name] = _coerce(param, value, self.name)
        return cleaned


def _coerce(param: Param, value: Any, tool_name: str) -> Any:
    """Accept what a model plausibly returns; reject what it invents."""
    if param.type in ("number", "integer"):
        try:
            number = float(str(value).replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError) as exc:
            raise ToolError(
                f"{tool_name}.{param.name}: expected a number, got {value!r}"
            ) from exc
        return int(number) if param.type == "integer" else number

    if param.type == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "yes", "1"):
            return True
        if text in ("false", "no", "0"):
            return False
        raise ToolError(f"{tool_name}.{param.name}: expected a boolean, got {value!r}")

    text = str(value).strip()
    if param.enum and text not in param.enum:
        raise ToolError(
            f"{tool_name}.{param.name}: {text!r} is not one of {list(param.enum)}"
        )
    return text


# --- handlers ---------------------------------------------------------------
# Each returns plain data. None of them format prose; that is the model's job,
# and it may only use what these return.


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, args)]


def list_products(db: Path, *, brand: str | None = None) -> dict[str, Any]:
    """Every tracked product with its latest price."""
    sql = """
        SELECT p.sku, p.brand, p.model_name, p.group_key, p.tier_key,
               o.price_usd, o.captured_at_utc, o.source_method
        FROM products p
        LEFT JOIN observations o ON o.id = (
            SELECT id FROM observations
            WHERE sku = p.sku AND price_usd IS NOT NULL
            ORDER BY captured_at_utc DESC LIMIT 1)
    """
    args: tuple = ()
    if brand:
        sql += " WHERE LOWER(p.brand) = LOWER(?)"
        args = (brand,)
    with st.connect(db) as conn:
        products = _rows(conn, sql + " ORDER BY o.price_usd", args)
    return {"count": len(products), "products": products}


def compare_group(db: Path, *, group: str, basis: str = "tier") -> dict[str, Any]:
    """Latest price for every product in one equivalence group."""
    column = "tier_key" if basis == "tier" else "group_key"
    with st.connect(db) as conn:
        members = _rows(
            conn,
            f"""SELECT p.sku, p.brand, p.model_name, o.price_usd, o.captured_at_utc
                FROM products p
                LEFT JOIN observations o ON o.id = (
                    SELECT id FROM observations
                    WHERE sku = p.sku AND price_usd IS NOT NULL
                    ORDER BY captured_at_utc DESC LIMIT 1)
                WHERE p.{column} = ?
                ORDER BY o.price_usd""",
            (group,),
        )
    priced = [m for m in members if m["price_usd"] is not None]
    if not priced:
        return {"group": group, "basis": basis, "members": members, "note": "no prices"}

    cheapest, dearest = priced[0], priced[-1]
    spread = dearest["price_usd"] - cheapest["price_usd"]
    return {
        "group": group,
        "basis": basis,
        "members": members,
        "cheapest": cheapest,
        "dearest": dearest,
        "spread_usd": round(spread, 2),
        "premium_pct": round(spread / cheapest["price_usd"] * 100, 1),
    }


def price_history(db: Path, *, sku: str, limit: int = 50) -> dict[str, Any]:
    """Every recorded observation for one product, newest last."""
    with st.connect(db) as conn:
        history = _rows(
            conn,
            """SELECT captured_at_utc, price_usd, source_method
               FROM observations
               WHERE sku = ? AND price_usd IS NOT NULL
               ORDER BY captured_at_utc DESC LIMIT ?""",
            (sku, limit),
        )
    history.reverse()
    if not history:
        return {"sku": sku, "observations": [], "note": "no observations"}
    first, last = history[0]["price_usd"], history[-1]["price_usd"]
    return {
        "sku": sku,
        "observations": history,
        "snapshots": len(history),
        "first_usd": first,
        "latest_usd": last,
        "change_usd": round(last - first, 2),
        "change_pct": round((last - first) / first * 100, 2) if first else 0.0,
    }


def find_movers(db: Path, *, min_change_pct: float = 1.0) -> dict[str, Any]:
    """Products whose price moved by at least the given percentage."""
    with st.connect(db) as conn:
        skus = [r["sku"] for r in conn.execute("SELECT sku FROM products")]
    movers = []
    for sku in skus:
        h = price_history(db, sku=sku)
        if h.get("snapshots", 0) < 2:
            continue
        if abs(h["change_pct"]) >= min_change_pct:
            movers.append(
                {
                    k: h[k]
                    for k in (
                        "sku",
                        "first_usd",
                        "latest_usd",
                        "change_usd",
                        "change_pct",
                    )
                }
            )
    movers.sort(key=lambda m: abs(m["change_pct"]), reverse=True)
    return {"threshold_pct": min_change_pct, "count": len(movers), "movers": movers}


def open_flags(db: Path, *, severity: str | None = None) -> dict[str, Any]:
    """Validation flags awaiting human review."""
    sql = "SELECT sku, rule, severity, detail, raised_at_utc FROM review_flags WHERE status='open'"
    args: tuple = ()
    if severity:
        sql += " AND severity = ?"
        args = (severity,)
    with st.connect(db) as conn:
        flags = _rows(conn, sql + " ORDER BY raised_at_utc DESC LIMIT 50", args)
    return {"count": len(flags), "flags": flags}


def build_registry(db: Path) -> dict[str, Tool]:
    """The tools the model is allowed to call, bound to one database."""
    tools = [
        Tool(
            name="list_products",
            description="List every tracked product with its most recent price. "
            "Use when the user asks what is being tracked.",
            params=(
                Param("brand", "string", "Restrict to one manufacturer, e.g. ASUS."),
            ),
            handler=lambda **kw: list_products(db, **kw),
        ),
        Tool(
            name="compare_group",
            description="Compare every product in one equivalence group and report "
            "the cheapest, the dearest and the spread between them.",
            params=(
                Param(
                    "group",
                    "string",
                    "The group key, e.g. intel-ultra5|16gb|512gb|windows-11-pro|laptop|clamshell",
                    required=True,
                ),
                Param(
                    "basis", "string", "Which grouping to use.", enum=("tier", "exact")
                ),
            ),
            handler=lambda **kw: compare_group(db, **kw),
        ),
        Tool(
            name="price_history",
            description="Return every recorded price for one product, with the "
            "change over the observed window.",
            params=(
                Param("sku", "string", "The Best Buy SKU.", required=True),
                Param("limit", "integer", "Maximum observations to return."),
            ),
            handler=lambda **kw: price_history(db, **kw),
        ),
        Tool(
            name="find_movers",
            description="Find products whose price changed by at least a given "
            "percentage over the observed window.",
            params=(
                Param(
                    "min_change_pct", "number", "Minimum absolute change, in percent."
                ),
            ),
            handler=lambda **kw: find_movers(db, **kw),
        ),
        Tool(
            name="open_flags",
            description="List validation flags awaiting human review.",
            params=(
                Param(
                    "severity",
                    "string",
                    "Restrict to one severity.",
                    enum=("critical", "warning", "info"),
                ),
            ),
            handler=lambda **kw: open_flags(db, **kw),
        ),
    ]
    return {t.name: t for t in tools}


def execute(registry: dict[str, Tool], name: str, args: dict[str, Any]) -> Any:
    """Validate and run one call. Unknown tools are refused, never guessed at."""
    tool = registry.get(name)
    if tool is None:
        raise ToolError(f"no such tool: {name!r}; available: {sorted(registry)}")
    return tool.handler(**tool.validate(args))
