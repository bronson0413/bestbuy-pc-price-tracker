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
                      o.source_method, o.on_sale, p.brand, p.model_name,
                      p.group_key, p.tier_key, p.url
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

    # The band between the cheapest and dearest machine at each moment. It is
    # the quantity a shopper actually cares about -- how much the same
    # configuration varies by brand -- and a line chart alone hides it.
    band = sub.pivot_table(
        index="captured_at_utc", columns="label", values="price_usd", aggfunc="last"
    ).sort_index()
    if band.shape[1] > 1:
        ax.fill_between(
            band.index,
            band.min(axis=1),
            band.max(axis=1),
            color="#1B3A6B",
            alpha=0.06,
            zorder=0,
            label="_spread",
        )

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
            zorder=3,
        )
        # Observations the retailer flagged as discounted, marked so a dip can
        # be read as a promotion rather than mistaken for a listing change.
        if "on_sale" in g.columns:
            promo = g[g["on_sale"] == 1]
            if not promo.empty:
                ax.scatter(
                    promo["captured_at_utc"],
                    promo["price_usd"],
                    s=110,
                    facecolors="none",
                    edgecolors=colour,
                    linewidths=1.6,
                    zorder=4,
                    label="_promo",
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
    latest = sub.sort_values("captured_at_utc").groupby("label")["price_usd"].last()
    subtitle = humanize_group(group_key)
    if len(latest) > 1:
        spread = latest.max() - latest.min()
        premium = spread / latest.min() * 100
        subtitle += (
            f"    ·    spread ${spread:,.0f} "
            f"({premium:.0f}% premium, {latest.idxmax()} over {latest.idxmin()})"
        )
    ax.text(
        0,
        1.03,
        subtitle,
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
    notes = []
    if band.shape[1] > 1:
        notes.append("shaded band: spread across the group")
    if "on_sale" in sub.columns and (sub["on_sale"] == 1).any():
        notes.append("ringed points: retailer-flagged promotion")
    if notes:
        fig.text(0.01, -0.055, "   ·   ".join(notes), fontsize=7.5, color="#777777")

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


def group_comparison(df: pd.DataFrame, key_col: str = "tier_key") -> pd.DataFrame:
    """Latest price per product, grouped, for cross-group comparison.

    A price tracker that only says what things cost stops one question short of
    useful. This frames the next one: given two configurations, what does the
    difference between them actually cost on the shelf?
    """
    if df.empty:
        return df
    latest = (
        df.sort_values("captured_at_utc")
        .groupby(["sku", "label", key_col], as_index=False)
        .last()
    )
    return latest[latest[key_col] != "unmatched"]


def plot_group_premium(
    df: pd.DataFrame, out_path: Path, key_col: str = "tier_key"
) -> Path:
    """Price distribution per equivalence group, one row per group.

    Deliberately a strip plot rather than a box plot: with two to four machines
    per group, a box plot draws quartiles from a sample too small to have them,
    which is a chart that implies more than the data supports.
    """
    latest = group_comparison(df, key_col)
    if latest.empty or key_col not in latest.columns:
        raise ValueError("no groups with a price to compare")
    groups = [g for g, members in latest.groupby(key_col) if len(members) >= 1]
    if not groups:
        raise ValueError("no groups with a price to compare")

    groups = sorted(
        groups, key=lambda g: latest[latest[key_col] == g]["price_usd"].min()
    )
    height = max(2.6, 0.9 * len(groups) + 1.6)
    fig, ax = plt.subplots(figsize=(10, height), dpi=160)

    for row, group in enumerate(groups):
        members = latest[latest[key_col] == group].sort_values("price_usd")
        prices = members["price_usd"].tolist()
        if len(prices) > 1:
            ax.plot(
                [min(prices), max(prices)],
                [row, row],
                color="#CBD5E1",
                linewidth=6,
                solid_capstyle="round",
                zorder=1,
            )
        for i, (_, product) in enumerate(members.iterrows()):
            colour = PALETTE[i % len(PALETTE)]
            ax.scatter(product["price_usd"], row, s=70, color=colour, zorder=3)
            ax.annotate(
                product["brand"],
                (product["price_usd"], row),
                textcoords="offset points",
                xytext=(0, 9),
                ha="center",
                fontsize=8,
                color=colour,
            )
        if len(prices) > 1:
            ax.annotate(
                f"${max(prices) - min(prices):,.0f} spread",
                (max(prices), row),
                textcoords="offset points",
                xytext=(12, -3),
                fontsize=8,
                color="#555555",
            )

    ax.set_yticks(range(len(groups)))
    ax.set_yticklabels([humanize_group(g) for g in groups], fontsize=8)
    ax.set_xlabel("Latest price (USD)")
    ax.xaxis.set_major_formatter(lambda v, _: f"${v:,.0f}")
    ax.set_title(
        "What the same configuration costs, by equivalence group",
        fontsize=13,
        pad=16,
        loc="left",
    )
    ax.grid(axis="x", alpha=0.25, linewidth=0.7)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.margins(x=0.12, y=0.25)
    fig.text(
        0.01,
        0.01,
        "One point per product; the bar spans the group. Groups with a "
        "single member show no spread because there is nothing to compare.",
        fontsize=7.5,
        color="#777777",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return out_path


def plot_trend(
    df: pd.DataFrame, group_key: str, out_path: Path, col: str = "tier_key"
) -> Path:
    """Observed price over time, with the retailer's stated regular price.

    Two series per product, drawn differently on purpose. The solid line is what
    was observed at each capture; the dashed line is the "regular price" the
    retailer displays alongside it. The second is a claim, not an observation --
    it was never watched changing -- so it is styled as a reference rather than
    as data.

    Without it a short window of unchanged prices is a chart of flat lines and
    nothing else. With it, the same chart shows which machines are discounted
    and by how much, which is information the observations genuinely contain.
    """
    sub = df[df[col] == group_key]
    if sub.empty:
        raise ValueError(f"no observations for group {group_key}")

    fig, ax = plt.subplots(figsize=(10, 5.4), dpi=160)
    discounts: list[tuple[str, float]] = []

    for i, (label, g) in enumerate(sub.groupby("label")):
        g = g.sort_values("captured_at_utc")
        colour = PALETTE[i % len(PALETTE)]
        single = len(g) == 1
        ax.plot(
            g["captured_at_utc"],
            g["price_usd"],
            marker="o" if not single else "D",
            markersize=5 if not single else 6,
            linewidth=2 if not single else 0,
            color=colour,
            label=label + ("  (one observation)" if single else ""),
            zorder=3,
        )
        if single:
            # Extend a faint guide so the reader can place the point against the
            # others without implying a series that was never observed.
            ax.plot(
                [sub["captured_at_utc"].min(), sub["captured_at_utc"].max()],
                [g["price_usd"].iloc[0]] * 2,
                linestyle=":",
                linewidth=0.9,
                color=colour,
                alpha=0.35,
                zorder=1,
            )

        regular = g["regular_price_usd"].dropna()
        latest_price = g["price_usd"].iloc[-1]
        if not regular.empty and regular.iloc[-1] > latest_price:
            ref = float(regular.iloc[-1])
            ax.plot(
                g["captured_at_utc"],
                [ref] * len(g),
                linestyle=(0, (4, 3)),
                linewidth=1.3,
                color=colour,
                alpha=0.55,
                zorder=2,
            )
            cut = (ref - latest_price) / ref * 100
            discounts.append((label, cut))
            ax.annotate(
                f"−{cut:.0f}%",
                (g["captured_at_utc"].iloc[0], (ref + latest_price) / 2),
                textcoords="offset points",
                xytext=(6, 0),
                fontsize=8.5,
                color=colour,
                va="center",
            )

        ax.annotate(
            f"${latest_price:,.0f}",
            (g["captured_at_utc"].iloc[-1], latest_price),
            textcoords="offset points",
            xytext=(8, 0),
            fontsize=9,
            color=colour,
            va="center",
            fontweight="bold",
        )

    ax.set_title(
        "Observed price over time, against the retailer's regular price",
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

    ncol = min(len(sub["label"].unique()), 3)
    ax.legend(
        frameon=False,
        fontsize=9,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.14),
        ncol=ncol,
    )

    span = sub["captured_at_utc"]
    singles = sum(1 for _, g in sub.groupby("label") if len(g) == 1)
    note = (
        f"Solid: observed price, {len(sub)} snapshots between "
        f"{span.min():%d %b %H:%M} and {span.max():%d %b %H:%M} UTC. "
        "Dashed: regular price as stated by the retailer — a claim, not an "
        "observed history."
    )
    if singles:
        note += (
            f"  {singles} product(s) were added late and carry a single "
            "observation, drawn as a diamond on a dotted guide."
        )
    if discounts:
        note += (
            "  Prices did not move during the window; the gap between the "
            "lines is the discount already in effect when observation began."
        )
    fig.text(0.01, -0.055, note, fontsize=7.5, color="#777777", wrap=True)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
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
            path = plot_group(
                df, key, args.outdir / f"movement_{tag}_{safe}.png", col=col
            )
            print(f"wrote {path}")
            path = plot_trend(df, key, args.outdir / f"trend_{tag}_{safe}.png", col=col)
            print(f"wrote {path}")
    try:
        path = plot_group_premium(df, args.outdir / "group_premium.png")
        print(f"wrote {path}")
    except ValueError as exc:
        print(f"skipped group comparison: {exc}")

    print(f"wrote {args.outdir / 'observations.csv'}, {args.outdir / 'summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
