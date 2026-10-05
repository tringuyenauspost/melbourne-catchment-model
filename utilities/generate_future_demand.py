"""Future delivery demand: today's delivery points at every facility, grown to FY42.

THE STEPS, in the order the run reports them:

  1  GROWTH. The `Delivery Volume (No OOH)` sheet is grouped by (postcode, product) and each
     group's growth is its FY42 volume / FY26 volume (--from-fy / --to-fy). The forecast holds
     ABSOLUTE volumes; only the ratio is used, the level stays with today's delivery points.
     Its `Product type` maps to ours: eParcel Express -> Express Post, everything else ->
     Parcel Post; StarTrack is not ours and is dropped. A row with no FY-from volume, or a
     NEGATIVE volume in either year (Geelong 3213 and 3331 from FY27), gives no ratio.
     Written to outputs/future_demand/postcode_growth.csv.
  2  FACILITIES. Every facility in melbourne_catchment.csv is kept and grown (the default,
     --facilities all). To narrow it, name facilities (matched on Terminating_facility_name
     by substring, case-insensitive); --other-facilities then drops the rest, or keeps them
     at factor 1.0.
  3  ALIGNMENT + TODAY. How many of the delivery postcodes the growth sheet covers, per
     product group, and today's Product_type and parcel_count distributions.
  4  SCALE. Each growth cell (facility, postcode, product group) gets n x growth rows, kept
     EXACT, so Express Post and Parcel Post volumes per facility and in total are exactly
     what the growth sheet implies. Rows are SAMPLED from the cell's own delivery points: a
     growing cell keeps all of them and draws the extra with replacement (is_synthetic=True),
     a shrinking one keeps a random subset.
  5  VERIFY. Product group targets vs achieved per facility and in total, Product_type mix
     per facility and in total, parcel_count distribution, today vs future.

A CELL THE SHEET DOES NOT COVER falls back, and `level` in growth_factors.csv says how:
  postcode  the sheet's own (postcode, group) ratio
  facility  the sheet pooled over every postcode that facility serves in our data
  group     the whole sheet for that product group
Pooled ratios are sums of volumes over sums, not means of ratios.

SETTING THE FACTOR YOURSELF. growth_factors.csv holds one row per growth cell. Edit `growth`
(or keep only the rows you change) and pass it with --growth; listed cells override the sheet.

WHAT IS HELD (--bound). Express Post and Parcel Post grow at different rates, so their split
follows the sheet and cannot also stay at today's. Each --bound spec holds today's shares of
one attribute (or a `+`-joined set) over a scope:

  ATTR@total     over the whole file
  ATTR@group     within each product group
  ATTR@facility  within each facility's product group

Default: Product_type@facility parcel_count@total — the eParcel Standard / Returns / rParcel
Post Plus / Metro Next Day mix inside every facility's Parcel Post, and the parcel_count
distribution of the whole file. `--bound none` holds nothing. The specs and the cell totals
are reconciled by IPF (raking) over the cell x attribute table seeded with today's counts, so
no combination is invented that a cell does not have today. Product_type@total cannot hold
next to the sheet's growth (Product_type fixes the group) and the run says so.

    python utilities/generate_future_demand.py
    python utilities/generate_future_demand.py --to-fy FY35 --facilities all
    python utilities/generate_future_demand.py --growth outputs/future_demand/growth_factors.csv
    python utilities/generate_future_demand.py --bound Product_type+parcel_count@facility
"""

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEMAND = ROOT / "inputs/deilvery_events/melbourne_catchment.csv"
FORECAST = ROOT / "inputs/future_demand/growth_volume_sheet.xlsx"
FORECAST_SHEET = "Delivery Volume (No OOH)"
FC_POSTCODE, FC_PRODUCT = "Del post code", "Product type"
OUT_DIR = ROOT / "outputs/future_demand"

FAC, PC, GRP = "Terminating_facility_name", "POSTCODE", "product_group"
KEY = [FAC, PC, GRP]
GROUPS = ["Express Post", "Parcel Post"]
EXPRESS_PRODUCTS = {"eParcel Express"}
# our Terminating_facility_name values to keep and grow (substring match, case-insensitive)
FACILITIES = ["all"]
BOUND = ["Product_type@facility", "parcel_count@total"]
SCOPES = {"total": [], "group": [GRP], "facility": [FAC, GRP]}

IPF_TOL = 1e-9
IPF_MAX_ITER = 1000


def product_group(product_type):
    return np.where(product_type.isin(EXPRESS_PRODUCTS), GROUPS[0], GROUPS[1])


# ---------------------------------------------------------------- 1 growth

def load_forecast(path, sheet, fy_from, fy_to):
    """(postcode, group, v_from, v_to, growth) — the sheet grouped by postcode and product."""
    sheets = pd.ExcelFile(path).sheet_names
    if sheet not in sheets:
        raise SystemExit(f"no sheet '{sheet}' in {path.name}; it has {sheets}")
    grid = pd.read_excel(path, sheet_name=sheet, header=None)
    head = grid.index[grid.apply(lambda r: r.astype(str).str.strip().eq(FC_POSTCODE).any(),
                                 axis=1)]
    if head.empty:
        raise SystemExit(f"no '{FC_POSTCODE}' header cell in sheet '{sheet}' of {path.name}")
    raw = grid.iloc[head[0] + 1:].set_axis(grid.iloc[head[0]].astype(str).str.strip(), axis=1)
    fy = {}
    for c in raw.columns:
        m = re.match(r"\s*(FY\d{2})", str(c))
        if m:
            fy[m.group(1)] = c
    for y in (fy_from, fy_to):
        if y not in fy:
            raise SystemExit(f"{y} not in the forecast; it has {sorted(fy)}")
    f = pd.DataFrame({
        PC: pd.to_numeric(raw[FC_POSTCODE], errors="coerce"),
        GRP: raw[FC_PRODUCT],
        "v_from": pd.to_numeric(raw[fy[fy_from]], errors="coerce").fillna(0.0),
        "v_to": pd.to_numeric(raw[fy[fy_to]], errors="coerce").fillna(0.0),
    })
    f = f[f[GRP].isin(GROUPS) & f[PC].notna()].astype({PC: int})
    f = f.groupby([PC, GRP], as_index=False)[["v_from", "v_to"]].sum()
    bad = (f["v_from"] < 0) | (f["v_to"] < 0)
    if bad.any():
        print(f"  {bad.sum()} (postcode, product) rows have a negative {fy_from}/{fy_to} volume "
              f"and are ignored: postcodes {sorted(f.loc[bad, PC].unique().tolist())}")
    f = f[~bad]
    f["growth"] = np.where(f["v_from"] > 0, f["v_to"] / f["v_from"].where(f["v_from"] > 0),
                           np.nan)
    return f


def forecast_factors(cells, fc):
    """One factor per growth cell, with the fallback level it was matched at.

    The sheet has no facility, so the `facility` level gives each of OUR facilities the sheet
    pooled over the postcodes it serves in `cells` (a shared postcode counts at both).
    """
    look = cells[KEY].copy()
    served = cells[KEY].drop_duplicates().merge(fc, on=[PC, GRP])
    pools = [("postcode", [PC, GRP], fc), ("facility", [FAC, GRP], served),
             ("group", [GRP], fc)]
    look["growth"], look["level"] = np.nan, ""
    for name, on, src in pools:
        agg = src.groupby(on, as_index=False)[["v_from", "v_to"]].sum()
        agg = agg[agg["v_from"] > 0]
        agg["ratio"] = agg["v_to"] / agg["v_from"]
        hit = look[on].merge(agg[on + ["ratio"]], on=on, how="left")["ratio"].to_numpy()
        fill = look["growth"].isna().to_numpy() & ~np.isnan(hit)
        look.loc[fill, "growth"] = hit[fill]
        look.loc[fill, "level"] = name
    return look


def apply_overrides(factors, path):
    ov = pd.read_csv(path, usecols=KEY + ["growth"]).drop_duplicates(KEY, keep="last")
    if (ov["growth"] < 0).any():
        raise SystemExit("growth factors must be >= 0")
    m = factors.merge(ov, on=KEY, how="left", suffixes=("", "_user"))
    hit = m["growth_user"].notna() & ~np.isclose(m["growth_user"], m["growth"])
    m.loc[hit, "growth"] = m.loc[hit, "growth_user"]
    m.loc[hit, "level"] = "user"
    unused = len(ov) - m["growth_user"].notna().sum()
    print(f"  --growth: {hit.sum()} cells overridden"
          + (f", {unused} rows match no growth cell" if unused else ""))
    return m.drop(columns="growth_user")


# ---------------------------------------------------------------- 2 filter

def select_facilities(names, terms):
    """Our facility names matching any of `terms`; every name for `all`."""
    names = sorted(names)
    if [t.lower() for t in terms] == ["all"]:
        return set(names)
    picked = set()
    for t in terms:
        hit = [n for n in names if t.strip().upper() in n.upper()]
        if not hit:
            raise SystemExit(f"--facilities '{t}' matches no facility; we have {names}")
        picked.update(hit)
    return picked


# ---------------------------------------------------------------- 3 / 5 reports

def section(title):
    print(f"\n{'=' * 4} {title} {'=' * max(4, 86 - len(title))}")


def distribution(df, col, by=None):
    """Counts and % of `col`, overall or within each `by` value."""
    keys = ([by] if by else []) + [col]
    n = df.groupby(keys).size().rename("rows")
    pct = n / (n.groupby(level=by).transform("sum") if by else n.sum()) * 100
    return pd.DataFrame({"rows": n, "%": pct.round(3)})


def compare(today, future, col, by=None):
    """today % vs future % of `col` (overall or within `by`), with the difference in points."""
    t = distribution(today, col, by)["%"].rename("today %")
    f = distribution(future, col, by)["%"].rename("future %")
    tab = pd.concat([t, f], axis=1).fillna(0)
    tab["diff pp"] = (tab["future %"] - tab["today %"]).round(3)
    return tab


def alignment_report(today, fc):
    pcs = set(today[PC])
    sheet = set(fc[PC])
    usable = set(fc.loc[fc["growth"].notna(), PC])
    print(f"delivery postcodes {len(pcs)} | in growth sheet {len(pcs & sheet)} "
          f"| with a usable ratio {len(pcs & usable)} | missing {len(pcs - sheet)}")
    print(f"growth sheet postcodes {len(sheet)} ({len(sheet - pcs)} not served by the "
          f"selected facilities)")
    cells = today.groupby([PC, GRP]).size().rename("rows").reset_index()
    cells = cells.merge(fc[[PC, GRP, "growth"]], on=[PC, GRP], how="left")
    cells["aligned"] = cells["growth"].notna()
    cells["rows_aligned"] = cells["rows"].where(cells["aligned"], 0)
    tab = cells.groupby(GRP).agg(postcodes=(PC, "size"), aligned=("aligned", "sum"),
                                 rows=("rows", "sum"), rows_aligned=("rows_aligned", "sum"))
    print(tab.to_string())
    miss = cells[~cells["aligned"]]
    if len(miss):
        by_fac = today.merge(miss[[PC, GRP]], on=[PC, GRP]).groupby([FAC, PC, GRP]).size()
        print("not aligned (falls back, see `level`):")
        print(by_fac.rename("rows").to_string())


# ---------------------------------------------------------------- 4 scale

def parse_bound(specs):
    """--bound specs -> [(scope columns, attribute columns)]."""
    if specs == ["none"]:
        return []
    out = []
    for s in specs:
        attrs, _, scope = s.partition("@")
        scope = scope or "total"
        if scope not in SCOPES:
            raise SystemExit(f"--bound '{s}': scope must be one of {sorted(SCOPES)}")
        out.append((SCOPES[scope], attrs.split("+")))
    return out


def largest_remainder(w, by):
    """Whole numbers summing to round(sum w) within each `by` group."""
    w = pd.Series(np.asarray(w, dtype=float))
    base = np.floor(w)
    short = w.groupby(by).transform("sum").round() - base.groupby(by).transform("sum")
    rank = (w - base).groupby(by).rank(method="first", ascending=False)
    return (base + (rank <= short)).astype(int).to_numpy()


def rake(cells, targets_by_cell, bounds):
    """IPF over the growth-cell x bound-attribute table.

    The seed is today's count `n` per (cell, *attributes), so the zero pattern survives.
    Margins: every cell's target, and for each bound spec today's share of the attributes
    within its scope times the scope's future total.
    """
    w = cells["n"].astype(float).to_numpy()
    tgt = cells.merge(targets_by_cell, on=KEY, how="left")["future_n"].to_numpy(dtype=float)
    margins = [(KEY, tgt)]
    for scope, attrs in bounds:
        share = cells.groupby(scope + attrs)["n"].transform("sum") / (
            cells.groupby(scope)["n"].transform("sum") if scope else cells["n"].sum())
        # the scope's future total, summed over unique cells (a cell target repeats per row)
        if scope:
            uniq = targets_by_cell.groupby(scope)["future_n"].sum().rename("t").reset_index()
            future = cells[scope].merge(uniq, on=scope, how="left")["t"]
        else:
            future = pd.Series(targets_by_cell["future_n"].sum(), index=cells.index)
        margins.append((scope + attrs, share.to_numpy() * future.to_numpy(dtype=float)))

    total = targets_by_cell["future_n"].sum()
    for it in range(IPF_MAX_ITER):
        worst = 0.0
        for cols, target in margins:
            cur = pd.Series(w).groupby([cells[c] for c in cols]).transform("sum").to_numpy()
            ratio = np.divide(target, cur, out=np.zeros_like(target), where=cur > 0)
            worst = max(worst, np.abs(cur - target).max())
            w = w * ratio
        if worst < IPF_TOL * max(total, 1.0):
            break
    else:
        raise SystemExit(
            f"IPF did not converge ({worst:,.1f} rows off after {IPF_MAX_ITER} iterations): "
            f"the --bound shares cannot all hold next to the growth. Usual cause: an attribute "
            f"that fixes the product group (Product_type@total) or two specs that overlap "
            f"(Product_type@facility with Product_type@group).")
    print(f"IPF converged in {it + 1} iterations")
    return w


def sample_cell(rows, k, rng):
    """k rows from one cell: keep all and add draws when growing, a subset when shrinking."""
    n = len(rows)
    if k <= n:
        pick = rng.choice(n, size=k, replace=False)
        return rows.iloc[np.sort(pick)].assign(is_synthetic=False)
    extra = rows.iloc[rng.choice(n, size=k - n, replace=True)].assign(is_synthetic=True)
    return pd.concat([rows.assign(is_synthetic=False), extra])


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--demand", type=Path, default=DEMAND)
    ap.add_argument("--forecast", type=Path, default=FORECAST)
    ap.add_argument("--sheet", default=FORECAST_SHEET)
    ap.add_argument("--from-fy", default="FY26")
    ap.add_argument("--to-fy", default="FY42")
    ap.add_argument("--facilities", nargs="+", default=FACILITIES,
                    help="our Terminating_facility_name values to keep and grow, or 'all'")
    ap.add_argument("--other-facilities", choices=["drop", "keep"], default="drop",
                    help="delivery points at other facilities: drop, or keep at factor 1.0")
    ap.add_argument("--growth", type=Path,
                    help="CSV with Terminating_facility_name,POSTCODE,product_group,growth; "
                         "overrides the sheet's factor for the cells it lists")
    ap.add_argument("--bound", nargs="+", default=BOUND,
                    help="ATTR[+ATTR]@total|group|facility specs to hold, or 'none'")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=OUT_DIR / "future_demand.csv")
    a = ap.parse_args()

    today = pd.read_csv(a.demand)
    today[GRP] = product_group(today["Product_type"])
    bounds = parse_bound(a.bound)
    attrs = list(dict.fromkeys(c for _, cols in bounds for c in cols))
    unknown = [c for c in attrs if c not in today.columns]
    if unknown:
        raise SystemExit(f"--bound columns not in {a.demand.name}: {unknown}")
    if set(attrs) & set(KEY):
        raise SystemExit(f"{KEY} define the growth cell, they cannot also be bound")
    a.out.parent.mkdir(parents=True, exist_ok=True)

    section(f"1 GROWTH  {a.to_fy} / {a.from_fy} per (postcode, product)")
    fc = load_forecast(a.forecast, a.sheet, a.from_fy, a.to_fy)
    fc.to_csv(a.out.parent / "postcode_growth.csv", index=False)
    print(fc.groupby(GRP).agg(postcodes=(PC, "size"), v_from=("v_from", "sum"),
                              v_to=("v_to", "sum"), median=("growth", "median"),
                              min=("growth", "min"), max=("growth", "max"))
          .assign(pooled=lambda t: t["v_to"] / t["v_from"]).round(3).to_string())

    section("2 FACILITIES  delivery points kept and grown")
    selected = select_facilities(today[FAC].unique(), a.facilities)
    other = ~today[FAC].isin(selected)
    print(today[today[FAC].isin(selected)].groupby(FAC).size().rename("rows").to_string())
    print(f"delivery points {len(today):,} -> {(~other).sum():,} at {len(selected)} facilities"
          + (f" ({other.sum():,} elsewhere: {a.other_facilities})" if other.any() else ""))
    if a.other_facilities == "drop":
        today = today[~other].reset_index(drop=True)

    section("3 ALIGNMENT + TODAY'S DISTRIBUTIONS")
    alignment_report(today[today[FAC].isin(selected)], fc)
    print("\nProduct_type, today:")
    print(distribution(today, "Product_type").sort_values("rows", ascending=False).to_string())
    print("\nparcel_count, today:")
    print(distribution(today, "parcel_count").to_string())

    section("4 SCALE")
    n_cell = today.groupby(KEY).size().rename("n").reset_index()
    factors = forecast_factors(n_cell, fc)
    off = ~factors[FAC].isin(selected)
    factors.loc[off, "growth"] = 1.0
    factors.loc[off, "level"] = "not selected"
    if a.growth:
        factors = apply_overrides(factors, a.growth)
    factors = factors.merge(n_cell, on=KEY)
    factors["future_n"] = largest_remainder(factors["n"] * factors["growth"],
                                            [factors[FAC], factors[GRP]])
    factors.to_csv(a.out.parent / "growth_factors.csv", index=False)
    lv = factors.groupby("level", sort=False)["n"].agg(["size", "sum"])
    lv.columns = ["cells", "rows today"]
    print(lv.to_string())

    cells = today.groupby(KEY + attrs).size().rename("n").reset_index()
    targets = factors[KEY + ["future_n"]]
    if bounds:
        w = rake(cells, targets, bounds)
    else:
        w = cells["n"].to_numpy(dtype=float)
    # rescale so each cell sums to its whole-row target, then whole rows per (cell, *attrs)
    tgt = cells.merge(targets, on=KEY, how="left")["future_n"].to_numpy(dtype=float)
    cur = pd.Series(w).groupby([cells[c] for c in KEY]).transform("sum").to_numpy()
    w = np.divide(w * tgt, cur, out=np.zeros_like(w), where=cur > 0)
    cells["k"] = largest_remainder(w, [cells[c] for c in KEY])

    rng = np.random.default_rng(a.seed)
    k_of = {tuple(r[:-1]): r[-1] for r in cells[KEY + attrs + ["k"]].itertuples(index=False)}
    future = pd.concat([sample_cell(rows, k_of[key], rng)
                        for key, rows in today.groupby(KEY + attrs, sort=True)],
                       ignore_index=True)
    got = future.groupby(KEY).size().reindex(pd.MultiIndex.from_frame(targets[KEY]),
                                              fill_value=0)
    assert (got.to_numpy() == targets["future_n"].to_numpy()).all(), "cells drifted"
    future.to_csv(a.out, index=False)
    print(f"future: {len(future):,} rows ({len(future) / len(today) - 1:+.1%}), "
          f"{future['parcel_count'].sum():,} parcels, "
          f"{future['is_synthetic'].sum():,} synthetic rows -> {a.out}")

    section("5 VERIFY  today vs future")
    tgt_fg = factors.groupby([FAC, GRP])[["n", "future_n"]].sum()
    tgt_fg["future"] = future.groupby([FAC, GRP]).size()
    tgt_fg["growth"] = (tgt_fg["future"] / tgt_fg["n"]).round(3)
    tgt_fg.loc[("TOTAL", ""), :] = [*tgt_fg[["n", "future_n", "future"]].sum(),
                                    round(len(future) / len(today), 3)]
    tgt_fg["match"] = np.where(tgt_fg["future"] == tgt_fg["future_n"], "yes", "NO")
    print("Product group rows vs growth target, per facility:")
    print(tgt_fg.rename(columns={"n": "today", "future_n": "target"}).to_string())

    pp = GROUPS[1]
    pf = compare(today[today[GRP] == pp], future[future[GRP] == pp], "Product_type", by=FAC)
    print(f"\nProduct_type % within each facility's {pp} — held "
          f"(max |diff| {pf['diff pp'].abs().max():.3f} pp):")
    print(pf.to_string())
    ex = compare(today, future, GRP, by=FAC).xs(GROUPS[0], level=GRP)
    print(f"\n{GROUPS[0]} share of each facility — follows the growth sheet:")
    print(ex.to_string())
    pt = compare(today, future, "Product_type")
    print(f"\nProduct_type % total (max |diff| {pt['diff pp'].abs().max():.3f} pp; Express vs "
          f"Parcel Post follows the growth sheet):")
    print(pt.to_string())
    for g in GROUPS:
        pg = compare(today[today[GRP] == g], future[future[GRP] == g], "Product_type")
        print(f"  within {g}: max |diff| {pg['diff pp'].abs().max():.3f} pp")
    pc = compare(today, future, "parcel_count")
    print(f"\nparcel_count % total (max |diff| {pc['diff pp'].abs().max():.3f} pp):")
    print(pc.to_string())
    print(f"\nheld: {a.bound}")


if __name__ == "__main__":
    main()
