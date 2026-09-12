"""Static deliverables: trend chart PNG, tidy CSV export, review queue."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from . import storage as st

PALETTE = ["#1B3A6B", "#C2410C", "#0F766E", "#7E22CE", "#A16207"]


def load_frame(db_path: Path | str = st.DEFAULT_DB) -> pd.DataFrame:
    with st.connect(db_path) as conn:
        df = pd.read_sql_query(
            """SELECT o.id, o.sku, o.captured_at_utc, o.price_usd, o.regular_price_usd,
                      o.source_method, p.brand, p.model_name, p.group_key, p.tier_key, p.url
               FROM observations o JOIN products p ON p.sku = o.sku
               WHERE o.price_usd IS NOT NULL
               ORDER BY o.captured_at_utc""",
            conn,
        )
    if df.empty:
        return df
    df["captured_at_utc"] = pd.to_datetime(
        df["captured_at_utc"], utc=True, format="mixed"
    )
    df["label"] = df["brand"] + " " + df["model_name"]
    return df


def load_flags(db_path: Path | str = st.DEFAULT_DB) -> pd.DataFrame:
    with st.connect(db_path) as conn:
        return pd.read_sql_query(
            "SELECT * FROM review_flags ORDER BY raised_at_utc DESC", conn
        )


def price_summary(df: pd.DataFrame, key_col: str = "group_key") -> pd.DataFrame:
    """Per-product first/last/min/max and the change over the observed window."""
    if df.empty:
        return df
    rows = []
    for (sku, label), g in df.groupby(["sku", "label"]):
        g = g.sort_values("captured_at_utc")
        first, last = g["price_usd"].iloc[0], g["price_usd"].iloc[-1]
        rows.append(
            {
                "sku": sku,
                "product": label,
                "group_key": g[key_col].iloc[-1],
                "snapshots": len(g),
                "first_usd": first,
                "latest_usd": last,
                "min_usd": g["price_usd"].min(),
                "max_usd": g["price_usd"].max(),
                "change_usd": last - first,
                "change_pct": (last - first) / first * 100 if first else float("nan"),
            }
        )
    return pd.DataFrame(rows).sort_values(["group_key", "latest_usd"])


def humanize_group(group_key: str) -> str:
    parts = group_key.split("|")
    if len(parts) != 6:
        return group_key
    cpu, ram, storage, os_, device, form = parts
    return (
        f"{cpu.replace('-', ' ').title()} · {ram.upper()} RAM · "
        f"{storage.upper()} SSD · {os_.replace('-', ' ').title()} · {form}"
    )


def plot_group(
    df: pd.DataFrame,
    group_key: str,
    out_path: Path,
    watermark: str | None = None,
    col: str = "group_key",
) -> Path:
    """One chart per equivalence group: price over time, one line per product."""
    sub = df[df[col] == group_key]
    if sub.empty:
        raise ValueError(f"no observations for group {group_key}")

    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=160)
    for i, (label, g) in enumerate(sub.groupby("label")):
        g = g.sort_values("captured_at_utc")
        colour = PALETTE[i % len(PALETTE)]
        ax.plot(
            g["captured_at_utc"],
            g["price_usd"],
            marker="o",
            markersize=4.5,
            linewidth=1.9,
            color=colour,
            label=label,
        )
        ax.annotate(
            f"${g['price_usd'].iloc[-1]:,.0f}",
            (g["captured_at_utc"].iloc[-1], g["price_usd"].iloc[-1]),
            textcoords="offset points",
            xytext=(7, 0),
            fontsize=9,
            color=colour,
            va="center",
        )

    ax.set_title(
        "Best Buy price movement, comparable configurations",
        fontsize=13,
        pad=22,
        loc="left",
    )
    ax.text(
        0,
        1.03,
        humanize_group(group_key),
        transform=ax.transAxes,
        fontsize=9,
        color="#555555",
    )
    ax.set_ylabel("Price (USD)")
    ax.yaxis.set_major_formatter(lambda v, _: f"${v:,.0f}")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d\n%H:%M"))
    ax.grid(axis="y", alpha=0.25, linewidth=0.7)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    # Below the axes: with four or more lines there is no free space inside the
    # plot that does not sit on top of a series.
    ncol = min(len(sub["label"].unique()), 3)
    ax.legend(
        frameon=False,
        fontsize=9,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.13),
        ncol=ncol,
    )
    span = sub["captured_at_utc"]
    fig.text(
        0.01,
        -0.02,
        f"Observation window {span.min():%Y-%m-%d %H:%M} to {span.max():%Y-%m-%d %H:%M} UTC"
        f" · {len(sub)} snapshots · source: Best Buy",
        fontsize=7.5,
        color="#555555",
    )
    if watermark:
        ax.text(
            0.5,
            0.5,
            watermark,
            transform=ax.transAxes,
            fontsize=44,
            color="#C2410C",
            alpha=0.16,
            ha="center",
            va="center",
            rotation=22,
            weight="bold",
        )
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return out_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render charts and CSV exports.")
    ap.add_argument("--db", type=Path, default=st.DEFAULT_DB)
    ap.add_argument("--outdir", type=Path, default=Path("reports"))
    args = ap.parse_args(argv)

    df = load_frame(args.db)
    if df.empty:
        print("No priced observations yet. Run the collector first.")
        return 1
    args.outdir.mkdir(parents=True, exist_ok=True)

    df.to_csv(args.outdir / "observations.csv", index=False)
    summary = price_summary(df)
    summary.to_csv(args.outdir / "summary.csv", index=False)
    flags = load_flags(args.db)
    if not flags.empty:
        flags.to_csv(args.outdir / "review_queue.csv", index=False)

    for col, tag in (("tier_key", "tier"), ("group_key", "exact")):
        for key in sorted(k for k in df[col].unique() if k != "unmatched"):
            if df[df[col] == key]["sku"].nunique() < 2:
                continue  # a group of one is not a comparison
            safe = key.replace("|", "_")
            path = plot_group(df, key, args.outdir / f"trend_{tag}_{safe}.png", col=col)
            print(f"wrote {path}")
    print(f"wrote {args.outdir / 'observations.csv'}, {args.outdir / 'summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
