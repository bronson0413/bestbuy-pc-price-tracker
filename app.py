"""Streamlit front end for the Best Buy PC pricing tracker.

Run: streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd
import streamlit as st_ui

from tracker import storage as st
from tracker.ai import (
    FlagExplainer,
    GeminiClient,
    GroundedAnswerer,
    ToolCallingAgent,
    build_corpus,
    build_registry,
    context_for,
)
from tracker.health import report as health_report
from tracker.report import load_flags, load_frame, price_summary
from tracker.validate import check_group_size, check_staleness

st_ui.set_page_config(
    page_title="Best Buy PC Pricing Tracker", page_icon="◧", layout="wide"
)

SEVERITY_ICON = {"critical": "✕", "warning": "!", "info": "·"}


@st_ui.cache_data(ttl=60)
def _load(db: str):
    return load_frame(db), load_flags(db)


@st_ui.cache_resource
def _corpus():
    """Index the product descriptions once per session."""
    root = Path(__file__).parent
    return build_corpus(
        root / "config" / "products.yaml", root / "config" / "descriptions.yaml"
    )


db_path = st_ui.sidebar.text_input("Database", value=str(st.DEFAULT_DB))
if st_ui.sidebar.button("Reload data"):
    st_ui.cache_data.clear()

df, flags = _load(db_path)

st_ui.title("Best Buy PC pricing tracker")

if df.empty:
    st_ui.info(
        "No price snapshots yet. Fill in `config/products.yaml`, then run "
        "`python -m tracker.collect` to capture the first snapshot."
    )
    st_ui.stop()

window_start, window_end = df["captured_at_utc"].min(), df["captured_at_utc"].max()
hours = (window_end - window_start).total_seconds() / 3600

c1, c2, c3, c4 = st_ui.columns(4)
c1.metric("Products tracked", df["sku"].nunique())
c2.metric("Snapshots", len(df))
c3.metric("Observation window", f"{hours:.0f} h")
c4.metric(
    "Open review flags",
    int((flags["status"] == "open").sum()) if not flags.empty else 0,
)

mode = st_ui.radio(
    "Comparison basis",
    ["Same tier", "Exact match"],
    horizontal=True,
    help="Exact match requires the identical processor SKU. Same tier accepts "
    "any processor of the same class, which is how a buyer actually "
    "cross-shops. Everything else -- memory, storage, OS, device type, "
    "form factor -- must match either way.",
)
key_col = "tier_key" if mode == "Same tier" else "group_key"

groups = sorted(g for g in df[key_col].unique() if g != "unmatched")
unmatched = df[df[key_col] == "unmatched"]["sku"].nunique()

if not groups:
    st_ui.warning(
        "No product has a complete specification, so no equivalence "
        "group could be formed. Check the review queue below."
    )
    st_ui.stop()

# Default to the group with the most products, since a group of one is not a
# comparison and makes the app look empty on first open.
_sizes = df[df[key_col] != "unmatched"].groupby(key_col)["sku"].nunique()
_default = groups.index(_sizes.idxmax()) if len(_sizes) else 0

group = st_ui.selectbox(
    "Equivalence group",
    groups,
    index=_default,
    help="Products are comparable only when CPU, memory, storage, "
    "OS, device type and form factor all normalise identically.",
)
sub = df[df[key_col] == group]

st_ui.caption("Group definition: " + " · ".join(group.split("|")))
if unmatched:
    st_ui.caption(
        f"{unmatched} product(s) excluded: specifications could not be "
        "parsed with confidence, so they are not grouped."
    )

pivot = sub.pivot_table(
    index="captured_at_utc", columns="label", values="price_usd", aggfunc="last"
).sort_index()
if len(pivot) < 2:
    st_ui.info(
        "One snapshot so far, so there is no line to draw yet. "
        "Capture another to start the trend."
    )
    st_ui.bar_chart(pivot.iloc[-1], height=300, y_label="Price (USD)")
else:
    st_ui.line_chart(pivot, height=380, y_label="Price (USD)")

left, right = st_ui.columns([3, 2])

with left:
    st_ui.subheader("Price movement")
    summary = price_summary(sub, key_col)
    st_ui.dataframe(
        summary[
            [
                "product",
                "snapshots",
                "first_usd",
                "latest_usd",
                "min_usd",
                "max_usd",
                "change_usd",
                "change_pct",
            ]
        ],
        hide_index=True,
        use_container_width=True,
        column_config={
            "first_usd": st_ui.column_config.NumberColumn("First", format="$%.2f"),
            "latest_usd": st_ui.column_config.NumberColumn("Latest", format="$%.2f"),
            "min_usd": st_ui.column_config.NumberColumn("Min", format="$%.2f"),
            "max_usd": st_ui.column_config.NumberColumn("Max", format="$%.2f"),
            "change_usd": st_ui.column_config.NumberColumn("Change", format="$%+.2f"),
            "change_pct": st_ui.column_config.NumberColumn(
                "Change %", format="%+.1f%%"
            ),
        },
    )
    cheapest = summary.sort_values("latest_usd").iloc[0]
    dearest = summary.sort_values("latest_usd").iloc[-1]
    spread = dearest["latest_usd"] - cheapest["latest_usd"]
    premium = spread / cheapest["latest_usd"] * 100 if cheapest["latest_usd"] else 0
    st_ui.write(
        f"Cheapest right now: **{cheapest['product']}** at ${cheapest['latest_usd']:,.2f}. "
        f"Dearest: **{dearest['product']}** at ${dearest['latest_usd']:,.2f} — "
        f"a **{premium:.0f}% premium** for the same configuration, a spread of "
        f"${spread:,.2f}."
    )

with right:
    st_ui.subheader("Where each price came from")
    prov = sub.groupby("source_method")["id"].count().rename("snapshots").reset_index()
    st_ui.dataframe(prov, hide_index=True, use_container_width=True)
    st_ui.caption(
        "Every observation records the method that produced it. "
        "Manual entries are human-read prices with a screenshot on file."
    )

st_ui.divider()
st_ui.subheader("Review queue")
st_ui.caption(
    "Automated checks flag observations for a person to adjudicate. "
    "Nothing here is auto-corrected."
)

health = check_staleness(
    (pd.Timestamp.now(tz="UTC") - window_end).total_seconds() / 3600
)
health += check_group_size(group, sub["sku"].nunique())
for f in health:
    st_ui.warning(f"{SEVERITY_ICON.get(f.severity, '·')} **{f.rule}** — {f.detail}")

if flags.empty:
    st_ui.success("No flags raised.")
else:
    show = flags[["raised_at_utc", "sku", "rule", "severity", "detail", "status"]]
    only_open = st_ui.checkbox("Show open flags only", value=True)
    if only_open:
        show = show[flags["status"] == "open"]
    st_ui.dataframe(show, hide_index=True, use_container_width=True)

    # An explanation drafts the first five minutes of an investigation. It
    # decides nothing: the flag's status is untouched and the output is
    # labelled as model-generated.
    _explainer = FlagExplainer()
    if _explainer.available and not show.empty:
        options = {
            f"{r.sku or '—'} · {r.rule} · {r.raised_at_utc[:16]}": i
            for i, r in show.iterrows()
        }
        chosen = st_ui.selectbox(
            "Draft a hypothesis for a flag",
            ["—", *options],
            help="A language model proposes what may have happened and how to "
            "check it. It does not resolve the flag.",
        )
        if chosen != "—":
            row = show.loc[options[chosen]]
            with st_ui.spinner("Drafting..."):
                try:
                    ctx = context_for(db_path, str(row["sku"] or ""))
                    drafted = _explainer.explain(
                        rule=str(row["rule"]),
                        severity=str(row["severity"]),
                        detail=str(row["detail"]),
                        sku=str(row["sku"] or ""),
                        brand=ctx.get("brand", ""),
                        model=ctx.get("model", ""),
                        title=ctx.get("title", ""),
                        configuration=ctx.get("configuration", ""),
                        history=ctx.get("history", []),
                    )
                except Exception as exc:
                    st_ui.error(f"Could not draft an explanation: {exc}")
                    drafted = None

            if drafted is not None:
                st_ui.caption(
                    f"Model-generated hypothesis · confidence {drafted.confidence} · "
                    "not a resolution"
                )
                if not drafted.hypotheses:
                    st_ui.info("The model did not propose an explanation.")
                for n, hypothesis in enumerate(drafted.hypotheses, start=1):
                    st_ui.markdown(f"**{n}. {hypothesis['claim']}**")
                    if hypothesis["check"]:
                        st_ui.caption(f"To check: {hypothesis['check']}")
                if drafted.recommended_action:
                    st_ui.markdown(
                        f"_Suggested next step:_ {drafted.recommended_action}"
                    )

st_ui.divider()
st_ui.subheader("Collection health")
st_ui.caption(
    "A tracker that only works when everything works is not finished. This is "
    "the other half: which sources were tried, how often each succeeded, and "
    "why the failures happened."
)

_health = health_report(db_path)
h1, h2, h3, h4 = st_ui.columns(4)
h1.metric("Status", _health.status)
h2.metric("Run success rate", f"{_health.overall_success_rate:.0%}")
h3.metric(
    "Since last capture",
    f"{_health.hours_since_capture:.1f} h" if _health.hours_since_capture else "—",
)
h4.metric("Sources in use", len(_health.sources))

hl, hr = st_ui.columns(2)
with hl:
    st_ui.markdown("**Where the prices came from**")
    if _health.sources:
        st_ui.dataframe(
            pd.DataFrame(
                [
                    {
                        "source": s.source_method,
                        "observations": s.observations,
                        "products": s.products,
                        "latest": s.last_seen,
                    }
                    for s in _health.sources
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
    else:
        st_ui.caption("No priced observations yet.")

with hr:
    st_ui.markdown("**Why collection failed**")
    if _health.failure_reasons:
        st_ui.dataframe(
            pd.DataFrame(_health.failure_reasons, columns=["cause", "occurrences"]),
            hide_index=True,
            use_container_width=True,
        )
        st_ui.caption(
            "Retained deliberately: these are the audit record of the "
            "degradation path, not noise to be cleared."
        )
    else:
        st_ui.caption("No collection failures recorded.")

st_ui.divider()
st_ui.subheader("Ask the tracker")
st_ui.caption(
    "A language model answers by calling the tracker's own query functions. "
    "Every tool call and its result are shown below the answer, so the numbers "
    "can be traced to a query rather than taken on trust."
)

_client = GeminiClient()
if not _client.available:
    st_ui.info(
        "Set GEMINI_API_KEY to enable this. Without it the rest of the "
        "application is unaffected."
    )
else:
    kind = st_ui.radio(
        "Question type",
        ["About prices", "About the products"],
        horizontal=True,
        help="Price questions are answered by querying the database. Product "
        "questions are answered from the indexed descriptions, and only "
        "from those.",
    )

    placeholder = (
        "Which machine in this group is cheapest, and by how much?"
        if kind == "About prices"
        else "Which of these has a 360-degree hinge?"
    )
    question = st_ui.text_input("Question", placeholder=placeholder)

    if question and kind == "About prices":
        with st_ui.spinner("Querying..."):
            agent = ToolCallingAgent(build_registry(Path(db_path)), _client)
            try:
                reply = agent.ask(question)
            except Exception as exc:
                st_ui.error(f"The assistant could not answer: {exc}")
                reply = None

        if reply is not None:
            if reply.is_grounded:
                st_ui.write(reply.text)
            else:
                # An answer produced without a successful query is not evidence.
                st_ui.warning(
                    "The assistant answered without querying any data. Treat the "
                    "following as unverified:"
                )
                st_ui.write(reply.text)

            with st_ui.expander(f"Tool calls ({len(reply.steps)})"):
                for i, step in enumerate(reply.steps, start=1):
                    icon = "OK" if step.ok else "REJECTED"
                    st_ui.markdown(f"**{i}. `{step.tool}` — {icon}**")
                    st_ui.json(step.args)
                    if step.ok:
                        st_ui.json(step.result)
                    else:
                        st_ui.error(step.error)

    elif question:
        with st_ui.spinner("Searching product descriptions..."):
            try:
                index = _corpus()
                answered = GroundedAnswerer(index, _client).ask(question)
            except Exception as exc:
                st_ui.error(f"The assistant could not answer: {exc}")
                answered = None

        if answered is not None:
            st_ui.write(answered.text)
            if not answered.has_evidence:
                st_ui.caption(
                    "No passage in the indexed descriptions matched, so the "
                    "model was not asked."
                )
            if not answered.is_clean:
                st_ui.warning(
                    "The answer cites passages that were not supplied: "
                    + ", ".join(answered.invented_citations)
                )
            if answered.hits:
                with st_ui.expander(f"Passages used ({len(answered.hits)})"):
                    for hit in answered.hits:
                        st_ui.markdown(
                            f"**`{hit.passage.doc_id}`** — {hit.passage.citation()} "
                            f"· score {hit.score} · matched "
                            f"`{', '.join(hit.matched_terms[:6])}`"
                        )
                        st_ui.caption(hit.passage.text)

st_ui.divider()
with st_ui.expander("All observations"):
    st_ui.dataframe(df, hide_index=True, use_container_width=True)
st_ui.download_button(
    "Download observations as CSV",
    df.to_csv(index=False).encode("utf-8"),
    file_name="observations.csv",
    mime="text/csv",
)
