"""Do Italian pump prices rise faster than they fall ("rockets and feathers")?

Uses the marts built by the pipeline (mart_daily_national, etl_loaded_file).
Method: daily change in the national average self-service price regressed on the RISES and
FALLS of crude oil (Brent, in euro per litre) over the same day and the previous 14 days:

    Δpump_t = a + Σ_k β⁺_k · max(Δbrent_{t-k}, 0) + Σ_k β⁻_k · min(Δbrent_{t-k}, 0) + e_t

Σβ⁺ = share of a crude price RISE passed to the pump within 15 days; Σβ⁻ = the same for a FALL.
Standard errors: Newey–West (HAC, 7 lags), because daily changes are autocorrelated.
Days with a pump change above 6 cents (one-off steps, likely excise duty changes) are left out
of the main model; the results with all days are reported as a robustness check.

Usage:  python analysis/rockets_and_feathers.py      (DATABASE_URL as for the pipeline)
Output: figures/*.png, analysis/results.md
"""
import os
import pathlib

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import psycopg2
import statsmodels.api as sm

LAGS = 14
EARLY = 5     # also compare what is passed on within the first 5 days (speed)
STEP = 0.06   # a one-day pump change above 6 cents is a one-off step (likely a tax change), not a market move
ROOT = pathlib.Path(__file__).resolve().parent.parent
FIG = ROOT / "figures"
FIG.mkdir(exist_ok=True)
ACCENT, BLUE, GREY = "#C44E52", "#4C72B0", "#9A9A9A"
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "font.size": 10})

conn = psycopg2.connect(os.environ.get("DATABASE_URL", "postgresql:///fuel_dw"))
daily = pd.read_sql("""SELECT full_date, fuel_group, avg_price::float AS price, brent_eur_litre::float AS brent
                       FROM fuel_dw.mart_daily_national WHERE service = 'Self-service'
                       ORDER BY full_date""", conn, parse_dates=["full_date"])
files = pd.read_sql("""SELECT price_date, rows_loaded, rows_quarantined
                       FROM fuel_dw.etl_loaded_file ORDER BY price_date""", conn, parse_dates=["price_date"])
wide = daily.pivot(index="full_date", columns="fuel_group", values="price")
brent = daily.groupby("full_date")["brent"].first()
calendar = pd.date_range(wide.index.min(), wide.index.max(), freq="D")
wide, brent = wide.reindex(calendar), brent.reindex(calendar)   # missing day stays NaN -> its change is dropped

# ------------------------------------------------------------------ chart 1: pump vs crude
fig, ax = plt.subplots(figsize=(10, 4.5))
ax.plot(wide.index, wide["Petrol"], color=ACCENT, label="Petrol, self-service (national average)")
ax.plot(wide.index, wide["Diesel"], color=BLUE, label="Diesel, self-service (national average)")
ax.plot(brent.index, brent, color=GREY, label="Crude oil (Brent) per litre")
ax.fill_between(wide.index, brent, wide["Petrol"], color=ACCENT, alpha=0.06)
mid = wide.index[len(wide) // 2]
ax.annotate("taxes + refining + distribution + margin", xy=(mid, 1.05), ha="center", color="#555555", fontsize=9)
ax.set_ylabel("€ per litre")
ax.set_ylim(0, 2.5)
ax.legend(loc="lower left", frameon=False, fontsize=9)
steps = [d for d in wide.index if (wide.loc[:, ["Petrol", "Diesel"]].diff().abs().loc[d] > STEP).any()]
for d in steps:
    ax.axvline(d, color="#555555", linestyle=":", linewidth=1)
if steps:
    ax.annotate("dotted lines: one-day steps > 6 cents (not explained by crude oil)", xy=(wide.index[0], 2.42),
                fontsize=8, color="#555555")
ax.set_title("Crude oil is about a quarter of the pump price; sudden steps come from elsewhere",
             loc="left", fontweight="bold")
fig.tight_layout()
fig.savefig(FIG / "pump_vs_crude.png", dpi=150)

# ------------------------------------------------------------------ regression
def asymmetry(fuel: str, exclude_steps: bool = True):
    d = pd.DataFrame({"dp": wide[fuel].diff(), "db": brent.diff()})
    if exclude_steps:
        d.loc[d["dp"].abs() > STEP, "dp"] = np.nan
    X = {}
    for k in range(LAGS + 1):
        X[f"up_{k}"] = d["db"].clip(lower=0).shift(k)
        X[f"down_{k}"] = d["db"].clip(upper=0).shift(k)
    X = pd.DataFrame(X)
    data = pd.concat([d["dp"], X], axis=1).dropna()
    model = sm.OLS(data["dp"], sm.add_constant(data[X.columns])).fit(cov_type="HAC", cov_kwds={"maxlags": 7})
    up = [f"up_{k}" for k in range(LAGS + 1)]
    down = [f"down_{k}" for k in range(LAGS + 1)]
    r = np.zeros(len(model.params))
    names = list(model.params.index)
    for c in up:
        r[names.index(c)] = 1
    for c in down:
        r[names.index(c)] = -1
    test = model.t_test(r)
    r_early = np.zeros(len(model.params))
    for k in range(EARLY + 1):
        r_early[names.index(f"up_{k}")] = 1
        r_early[names.index(f"down_{k}")] = -1
    test_early = model.t_test(r_early)
    return {
        "early_rise": model.params[up[:EARLY + 1]].sum(), "early_fall": model.params[down[:EARLY + 1]].sum(),
        "early_difference": float(test_early.effect[0]), "early_p_value": float(test_early.pvalue),
        "fuel": fuel, "days": int(model.nobs), "steps_excluded": exclude_steps,
        "pass_through_rise": model.params[up].sum(), "pass_through_fall": model.params[down].sum(),
        "difference": float(test.effect[0]), "p_value": float(test.pvalue),
        "cum_rise": model.params[up].cumsum().values, "cum_fall": model.params[down].cumsum().values,
    }


results = [asymmetry("Petrol"), asymmetry("Diesel")]
robust = [asymmetry("Petrol", False), asymmetry("Diesel", False)]

# ------------------------------------------------------------------ chart 2: response curves
fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
for ax, r in zip(axes, results):
    days = np.arange(LAGS + 1)
    ax.plot(days, 100 * r["cum_rise"], color=ACCENT, marker="o", ms=3, label="Crude oil rises")
    ax.plot(days, 100 * r["cum_fall"], color=BLUE, marker="o", ms=3, label="Crude oil falls")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title(f"{r['fuel']}: {100 * r['pass_through_rise']:.0f}% of rises vs "
                 f"{100 * r['pass_through_fall']:.0f}% of falls passed on", loc="left", fontsize=10)
    ax.set_xlabel("Days after the crude oil change")
axes[0].set_ylabel("Share of the crude change passed to the pump (%)")
axes[0].legend(frameon=False)
fig.suptitle("How fast crude oil changes reach the pump (cumulative, 15 days)", x=0.01, ha="left", fontweight="bold")
fig.tight_layout()
fig.savefig(FIG / "pass_through_curves.png", dpi=150)

# ------------------------------------------------------------------ chart 3: pipeline monitoring
fig, ax1 = plt.subplots(figsize=(10, 3.6))
ax1.bar(files["price_date"], files["rows_loaded"] / 1000, width=1.0, color=GREY)
ax1.set_ylabel("Rows loaded per day (thousand)")
ax1.set_ylim(80, files["rows_loaded"].max() / 1000 * 1.03)
ax2 = ax1.twinx()
ax2.plot(files["price_date"], files["rows_quarantined"], color=ACCENT, linewidth=1)
ax2.set_ylabel("Rows quarantined", color=ACCENT)
ax2.spines["right"].set_visible(True)
full = pd.date_range(files["price_date"].min(), files["price_date"].max())
for d in full.difference(files["price_date"]):
    ax1.axvline(d, color=ACCENT, linestyle="--", linewidth=1)
    ax1.annotate(f"missing file {d:%d %b %Y}", xy=(d, ax1.get_ylim()[1]), xytext=(-5, -12),
                 textcoords="offset points", ha="right", color=ACCENT, fontsize=9)
ax1.set_title("Pipeline monitoring: rows loaded and quarantined per day", loc="left", fontweight="bold")
fig.tight_layout()
fig.savefig(FIG / "pipeline_monitoring.png", dpi=150)

# ------------------------------------------------------------------ results file
lines = ["| Fuel | Days used | Within 6 days: RISE | FALL | p-value | Within 15 days: RISE | FALL | p-value |",
         "|---|---|---|---|---|---|---|---|"]
for r in results + robust:
    label = r["fuel"] if r["steps_excluded"] else r["fuel"] + " (all days, incl. steps)"
    lines.append(f"| {label} | {r['days']} | {100 * r['early_rise']:.0f}% | {100 * r['early_fall']:.0f}% "
                 f"| {r['early_p_value']:.3f} | {100 * r['pass_through_rise']:.0f}% | {100 * r['pass_through_fall']:.0f}% "
                 f"| {r['p_value']:.3f} |")
summary = wide.agg(["mean", "min", "max"]).round(3)
lines += ["", "Average self-service price over the period (€/litre):", "",
          summary.to_markdown() if hasattr(summary, "to_markdown") else summary.to_string()]
share = (brent / wide["Petrol"]).mean()
lines += ["", "One-day steps above 6 cents (excluded from the main model):"]
chg = wide[["Petrol", "Diesel"]].diff()
for d in steps:
    lines.append(f"- {d:%Y-%m-%d}: petrol {100 * chg.loc[d, 'Petrol']:+.1f} c, diesel {100 * chg.loc[d, 'Diesel']:+.1f} c, "
                 f"crude {100 * brent.diff().loc[d]:+.1f} c per litre")
lines += ["", f"Crude oil as share of the petrol price (average): {100 * share:.0f}%",
          f"Brent per litre: min €{brent.min():.3f}, max €{brent.max():.3f}"]
(ROOT / "analysis" / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
