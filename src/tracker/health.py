"""Collection health: what the tracker knows about its own reliability.

A tracker that only works when everything works is not finished. This module
reports the other half -- which sources were tried, how often each succeeded,
why the failures happened, and how stale the data is now.

The numbers come from the run log and the flag table, both of which the
collector writes as it goes. Nothing here is computed specially for display;
it is the audit trail read back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import storage as st


@dataclass
class SourceHealth:
    source_method: str
    observations: int
    products: int
    first_seen: str
    last_seen: str


@dataclass
class RunHealth:
    started_utc: str
    source_method: str
    attempted: int
    succeeded: int
    failed: int

    @property
    def success_rate(self) -> float:
        return self.succeeded / self.attempted if self.attempted else 0.0


@dataclass
class HealthReport:
    sources: list[SourceHealth] = field(default_factory=list)
    runs: list[RunHealth] = field(default_factory=list)
    failure_reasons: list[tuple[str, int]] = field(default_factory=list)
    open_flags: dict[str, int] = field(default_factory=dict)
    hours_since_capture: float | None = None
    total_observations: int = 0
    tracked_products: int = 0

    @property
    def overall_success_rate(self) -> float:
        attempted = sum(r.attempted for r in self.runs)
        succeeded = sum(r.succeeded for r in self.runs)
        return succeeded / attempted if attempted else 0.0

    @property
    def is_stale(self) -> bool:
        return self.hours_since_capture is not None and self.hours_since_capture > 24

    @property
    def status(self) -> str:
        """A single word for the top of the page."""
        if not self.total_observations:
            return "no data"
        if self.is_stale:
            return "stale"
        if self.open_flags.get("critical"):
            return "needs review"
        return "healthy"


def report(db: Path | str) -> HealthReport:
    with st.connect(db) as conn:
        sources = [
            SourceHealth(
                source_method=r["source_method"],
                observations=r["observations"],
                products=r["products"],
                first_seen=r["first_seen"],
                last_seen=r["last_seen"],
            )
            for r in conn.execute(
                """SELECT source_method,
                          COUNT(*)              AS observations,
                          COUNT(DISTINCT sku)   AS products,
                          MIN(captured_at_utc)  AS first_seen,
                          MAX(captured_at_utc)  AS last_seen
                   FROM observations
                   WHERE price_usd IS NOT NULL
                   GROUP BY source_method
                   ORDER BY observations DESC"""
            )
        ]

        runs = [
            RunHealth(
                started_utc=r["started_utc"],
                source_method=r["source_method"] or "",
                attempted=r["attempted"] or 0,
                succeeded=r["succeeded"] or 0,
                failed=r["failed"] or 0,
            )
            for r in conn.execute(
                "SELECT * FROM collection_runs ORDER BY started_utc DESC LIMIT 40"
            )
        ]

        # Failures are stored as one flag per product per run; the useful view
        # is which reason recurs, not which product hit it.
        reasons: dict[str, int] = {}
        for row in conn.execute(
            """SELECT detail FROM review_flags
               WHERE rule = 'collection_failed'"""
        ):
            for part in str(row["detail"]).split(" | "):
                key = _summarise(part)
                if key:
                    reasons[key] = reasons.get(key, 0) + 1

        open_flags = {
            r["severity"]: r["n"]
            for r in conn.execute(
                """SELECT severity, COUNT(*) AS n FROM review_flags
                   WHERE status = 'open' GROUP BY severity"""
            )
        }

        totals = conn.execute(
            """SELECT COUNT(*) AS n, MAX(captured_at_utc) AS latest
               FROM observations WHERE price_usd IS NOT NULL"""
        ).fetchone()
        products = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]

    hours = _hours_since(totals["latest"]) if totals["latest"] else None
    return HealthReport(
        sources=sources,
        runs=runs,
        failure_reasons=sorted(reasons.items(), key=lambda kv: -kv[1]),
        open_flags=open_flags,
        hours_since_capture=hours,
        total_observations=totals["n"] or 0,
        tracked_products=products,
    )


def _summarise(detail: str) -> str:
    """Collapse a failure message to its cause.

    Error strings carry SKUs, URLs and timings that differ every time. Grouping
    by the underlying cause is what turns a list of failures into a diagnosis.
    """
    text = detail.strip()
    if not text:
        return ""
    source, _, message = text.partition(":")
    message = message.strip().lower()

    known = [
        ("ERR_HTTP2_PROTOCOL_ERROR", "http2 connection terminated"),
        ("timeout", "page load timed out"),
        ("bot mitigation", "blocked by bot mitigation"),
        ("no price found", "no price in page markup"),
        ("api_key is not set", "no credentials configured"),
        ("key is not set", "no credentials configured"),
        ("not found in api response", "sku unknown to the source"),
        ("no manual entry", "no manual entry recorded"),
        ("rate limited", "rate limited"),
        ("sku mismatch", "source answered about a different sku"),
    ]
    for needle, label in known:
        if needle.lower() in message:
            return f"{source.strip()}: {label}"
    return f"{source.strip()}: {message[:60]}" if message else source.strip()


def _hours_since(timestamp: str) -> float | None:
    try:
        when = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (datetime.now(UTC) - when).total_seconds() / 3600
