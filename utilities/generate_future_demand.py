"""Future delivery demand: today's delivery points resampled to the FY42 volume per postcode and product.

INPUTS
  delivery_points.csv  one row per consignment delivered on 12/05/26 (Bendigo, Ballarat,
                       Geelong and Melbourne Metro): Consignment_ID, Del_Lat, Del_Long,
                       Terminating_facility_name (+ Lat/Long), Del_postcode, Product_type,
                       Article_count. --products keeps Parcel Post, Express Post, StarTrack
                       Premium and OOH by default; StarTrack Road Express is dropped.
  growth_volume_sheet.xlsx, sheet `volume_by_postcode`: one row per (Del post code, Revised
                       Product type) with its Region and a volume per FY (FY26 = extracted).

THE STEPS, in the order the run reports them:

  1  GROWTH. The sheet is grouped by (postcode, product): its --from-fy and --to-fy VOLUME and
     their ratio. `Revised Product type` matches our Product_type one to one (Parcel Post,
     Express Post, StarTrack Premium, StarTrack Road Express, OOH). A row with a NEGATIVE volume
     in either year is ignored. Written to outputs/future_demand/postcode_growth.csv.
  2  REGIONS. Each delivery point takes its postcode's Region from the sheet. Only Melbourne
     Metro is kept and grown by default (--regions; substring, case-insensitive, or `all`);
     --other-regions then drops the rest, or keeps them at today's volume.
  3  ALIGNMENT + TODAY. How many of the delivery postcodes the sheet covers, per product, and
     today's Product_type and Article_count distributions.
  4  SCALE. VOLUME = articles (the sum of Article_count). What is matched, in order:
       1. postcode x product VOLUME: each cell's future articles = the sheet's --to-fy volume
          (--level sheet, the default), rounded to whole articles so that each POSTCODE's total
          is its target rounded — EXACT, never traded;
          --level ratio uses today's articles x the sheet's growth ratio instead;
       2. Product_type: follows from (1), the sheet's own product mix;
       3. Article_count: today's mix of delivery points held within each product (--bound),
          by IPF in articles inside the cell totals. Rounding settles the multi-article rows
          against their product targets first and lands each cell exactly with single-article
          rows, so (3) gives way to (1), never the reverse.
     Rows are SAMPLED from the cell's own delivery points of each Article_count: a growing
     stratum keeps all of them and draws the extra with replacement (is_synthetic=True), a
     shrinking one keeps a random subset. A synthetic row keeps the Consignment_ID and the
     Terminating_facility_name of the row it was drawn from — the facility is carried, not
     constrained. The number of delivery points is whatever the volume needs.
     OOH has no delivery points today: its cells are seeded with a copy of the postcode's
     Parcel Post and Express Post rows (so OOH takes their Article_count mix), every OOH row is
     synthetic and gets Consignment_ID OOH_<postcode>_<n>, and step 5 places it (no locker
     locations: a random road point in the postcode). OOH is built only when in --products.
     A sheet cell with volume but no rows to draw from (OOH where the postcode has no Parcel or
     Express Post today) is reported and not placed.
  5  POINTS. A synthetic row keeps everything it drew except WHERE it is: it gets a new random
     point inside its postcode (vic_postcode_boundaries.csv), snapped to the nearest driveable
     road by OSRM /nearest. A candidate is redrawn when OSRM finds no road for it, when the
     road is more than --max-snap-m away (a lake, the bay, a park, a paddock interior), or
     when the snapped point lands outside the postcode. Rows still without a point after
     MAX_ROUNDS draws keep the copied coordinate and are counted. Real rows never move.
     --points copy skips this and keeps today's copied coordinates.
  6  VERIFY. Articles vs target per region x product and per cell, Product_type mix of
     articles, Article_count mix within each product, today vs future.
  7  COMPARISON. comparison_by_postcode_product.csv, per (postcode, product) plus an `All
     products` row per postcode: the sheet's --from-fy and --to-fy volume, today's articles, the
     target, the articles generated, the gap to the target and to the sheet, and the change and
     % change in articles and delivery points (blank where the base is 0, i.e. OOH).
  8  REPORT. future_demand_report.xlsx, volume = articles: `postcode` (sheet --from-fy volume,
     base, target, future; target and future vs base and future vs target, absolute and %),
     `product` (the same per product type, with shares), `parcel_count` (delivery points and
     articles by Article_count per product, base vs future shares; OOH against today's Parcel
     and Express Post), `postcode_product` (the cell table).

A CELL THE SHEET DOES NOT COVER (or --level ratio) uses today's articles x a ratio, and
`level` in growth_factors.csv says which (`sheet` = the sheet's own volume):
  postcode  the sheet's own (postcode, product) ratio
  region    the sheet pooled over the postcode's region
  product   the whole sheet for that product
Pooled ratios are sums of volumes over sums, not means of ratios.

SETTING THE FACTOR YOURSELF. growth_factors.csv holds one row per growth cell. Edit `growth`
(or keep only the rows you change) and pass it with --growth; a listed cell's target becomes
today's articles x that factor.

WHAT IS HELD (--bound). Products grow at different rates, so the Product_type mix follows the
sheet and cannot also stay at today's. Each --bound spec holds today's shares of one attribute
(or a `+`-joined set) over a scope:

  ATTR@total     over the whole file
  ATTR@product   within each product
  ATTR@region    within each region's product

Default: Article_count@product — each product's own Article_count distribution (shares of
articles by Article_count are shares of rows, so this holds the delivery-point mix). Not @total:
the product mix moves with the sheet, so holding the whole file's mix would bend every
product's own mix to compensate. `--bound none` holds nothing (each cell then keeps its own
mix, which is noisier in small cells). The specs and the cell totals are reconciled by IPF
(raking) over the cell x attribute table seeded with today's counts, so no combination is
invented that a cell does not have today. Postcode, Product_type and Region define the cell
and cannot be bound.

    python utilities/generate_future_demand.py
    python utilities/generate_future_demand.py --to-fy FY35 --regions all
    python utilities/generate_future_demand.py --level ratio
    python utilities/generate_future_demand.py --growth outputs/future_demand/growth_factors.csv
    python utilities/generate_future_demand.py --bound Article_count@total
    python utilities/generate_future_demand.py --osrm http://localhost:5001 --max-snap-m 150
"""

import argparse
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import shapely
from requests.adapters import HTTPAdapter

ROOT = Path(__file__).resolve().parents[1]
DEMAND = ROOT / "inputs/future_demand/delivery_points.csv"
FORECAST = ROOT / "inputs/future_demand/growth_volume_sheet.xlsx"
FORECAST_SHEET = "volume_by_postcode"
FC_POSTCODE, FC_PRODUCT, FC_REGION = "Del post code", "Revised Product type", "Region"
BOUNDARIES = ROOT / "inputs/deilvery_events/vic_postcode_boundaries.csv"
BOUNDARY_PC = "POSTCODE"
OUT_DIR = ROOT / "outputs/future_demand"

REG, FAC, PC, GRP = "Region", "Terminating_facility_name", "Del_postcode", "Product_type"
CNT = "Article_count"
KEY = [REG, PC, GRP]                      # one growth cell; the facility is carried, not held
PRODUCTS = ["Parcel Post", "Express Post", "StarTrack Premium", "OOH"]
REGIONS = ["Melbourne Metro"]
NO_REGION = "(not in sheet)"
OOH = "OOH"                                # no delivery points today: built from the sheet
OOH_LIKE = ["Parcel Post", "Express Post"]  # the rows an OOH row draws its Article_count from
BOUND = ["Article_count@product"]
SCOPES = {"total": [], "product": [GRP], "region": [REG, GRP]}

IPF_TOL = 1e-9
IPF_MAX_ITER = 1000

OSRM_URL = "http://localhost:5001"   # the local osrm-server container
MAX_SNAP_M = 250          # a candidate further than this from a road is redrawn
EDGE_TOL_DEG = 1e-4       # ~10 m: a point snapped onto the postcode's boundary road is inside
MAX_ROUNDS = 30           # draw-and-snap rounds before a row keeps its copied coordinate
LAT_COLS, LON_COLS = ["Del_Lat"], ["Del_Long"]


def section(title):
    print(f"\n{'=' * 4} {title} {'=' * max(4, 86 - len(title))}")


def group_sum(values, frame, cols):
    """`values` summed within each `cols` group of `frame`, broadcast back to every row."""
    return pd.Series(values).groupby([frame[c] for c in cols]).transform("sum").to_numpy()


def largest_remainder(w, by):
    """Whole numbers summing to round(sum w) within each `by` group."""
    w = pd.Series(np.asarray(w, dtype=float))
    base = np.floor(w)
    short = w.groupby(by).transform("sum").round() - base.groupby(by).transform("sum")
    rank = (w - base).groupby(by).rank(method="first", ascending=False)
    return (base + (rank <= short)).astype(int).to_numpy()


# ---------------------------------------------------------------- 1 growth

def load_forecast(path, sheet, fy_from, fy_to):
    """(postcode, region, product, v_from, v_to, growth) — the sheet by postcode and product."""
    sheets = pd.ExcelFile(path).sheet_names
    if sheet not in sheets:
        raise SystemExit(f"no sheet '{sheet}' in {path.name}; it has {sheets}")
    grid = pd.read_excel(path, sheet_name=sheet, header=None)
    # the header row is wherever the postcode column title sits (a totals row sits above it)
    is_head = grid.apply(lambda r: r.astype(str).str.strip().eq(FC_POSTCODE).any(), axis=1)
    if not is_head.any():
        raise SystemExit(f"no '{FC_POSTCODE}' header cell in sheet '{sheet}' of {path.name}")
    head = is_head.idxmax()
    raw = grid.iloc[head + 1:].set_axis(grid.iloc[head].astype(str).str.strip(), axis=1)
    missing = [c for c in (FC_PRODUCT, FC_REGION) if c not in raw.columns]
    if missing:
        raise SystemExit(f"no {missing} column in sheet '{sheet}' of {path.name}")
    fy = {m.group(1): c for c in raw.columns if (m := re.match(r"\s*(FY\d{2})", str(c)))}
    for y in (fy_from, fy_to):
        if y not in fy:
            raise SystemExit(f"{y} not in the forecast; it has {sorted(fy)}")

    f = pd.DataFrame({
        PC: pd.to_numeric(raw[FC_POSTCODE], errors="coerce"),
        REG: raw[FC_REGION].astype(str).str.strip(),
        GRP: raw[FC_PRODUCT].astype(str).str.strip(),
        "v_from": pd.to_numeric(raw[fy[fy_from]], errors="coerce").fillna(0.0),
        "v_to": pd.to_numeric(raw[fy[fy_to]], errors="coerce").fillna(0.0),
    })
    f = f[f[PC].notna()].astype({PC: int})
    regions = f.groupby(PC)[REG].nunique()
    if (regions > 1).any():
        raise SystemExit(f"postcodes in more than one Region: {regions[regions > 1].index.tolist()}")
    f = f.groupby([PC, REG, GRP], as_index=False)[["v_from", "v_to"]].sum()
    bad = (f["v_from"] < 0) | (f["v_to"] < 0)
    if bad.any():
        print(f"  {bad.sum()} (postcode, product) rows have a negative {fy_from}/{fy_to} volume "
              f"and are ignored: postcodes {sorted(f.loc[bad, PC].unique().tolist())}")
    f = f[~bad]
    f["growth"] = f["v_to"] / f["v_from"].where(f["v_from"] > 0)    # NaN when no FY-from volume
    return f


def growth_step(a, products):
    """(fc, ooh): the sheet's rows for the products we have delivery points for, and OOH's."""
    section(f"1 GROWTH  {a.to_fy} / {a.from_fy} per (postcode, product)")
    fc = load_forecast(a.forecast, a.sheet, a.from_fy, a.to_fy)
    fc.to_csv(a.out.parent / "postcode_growth.csv", index=False)
    print(fc.groupby(GRP).agg(postcodes=(PC, "size"), v_from=("v_from", "sum"),
                              v_to=("v_to", "sum"), median=("growth", "median"),
                              min=("growth", "min"), max=("growth", "max"))
          .assign(pooled=lambda t: t["v_to"] / t["v_from"]).round(3).to_string())
    other = sorted(set(fc[GRP]) - set(products) - {OOH})
    if other:
        print(f"not in --products or no delivery points, not grown: {other}")
    absent = sorted(set(products) - set(fc[GRP]))
    if absent:
        raise SystemExit(f"Product_type {absent} has no '{FC_PRODUCT}' rows in the sheet")
    return (fc[fc[GRP].isin(products)].reset_index(drop=True),
            fc[fc[GRP] == OOH].reset_index(drop=True))


# ---------------------------------------------------------------- 2 regions

def select_regions(names, terms):
    """Our region names matching any of `terms`; every name for `all`."""
    names = sorted(names)
    if [t.lower() for t in terms] == ["all"]:
        return set(names)
    picked = set()
    for t in terms:
        hit = [n for n in names if t.strip().upper() in n.upper()]
        if not hit:
            raise SystemExit(f"--regions '{t}' matches no region; we have {names}")
        picked.update(hit)
    return picked


def regions_step(a, today, fc):
    """(today, selected): today with its Region, without the unselected regions when dropped."""
    section("2 REGIONS  delivery points kept and grown")
    region = fc.drop_duplicates(PC).set_index(PC)[REG]
    today[REG] = today[PC].map(region).fillna(NO_REGION)
    selected = select_regions(today[REG].unique(), a.regions)
    other = ~today[REG].isin(selected)
    print(today[~other].groupby(REG).size().rename("rows").to_string())
    print(f"delivery points {len(today):,} -> {(~other).sum():,} in {len(selected)} regions"
          + (f" ({other.sum():,} elsewhere: {a.other_regions})" if other.any() else ""))
    if a.other_regions == "drop":
        today = today[~other].reset_index(drop=True)
    return today, selected


# ---------------------------------------------------------------- 3 alignment + today

def distribution(df, col, by=None, weight=None):
    """Counts (rows, or the sum of `weight`) and % of `col`, overall or within each `by` value."""
    keys = ([by] if by else []) + [col]
    n = (df.groupby(keys)[weight].sum() if weight else df.groupby(keys).size()).rename("rows")
    pct = n / (n.groupby(level=by).transform("sum") if by else n.sum()) * 100
    return pd.DataFrame({"rows": n, "%": pct.round(3)})


def alignment_step(today, selected, fc):
    section("3 ALIGNMENT + TODAY'S DISTRIBUTIONS")
    served = today[today[REG].isin(selected)]
    pcs, sheet = set(served[PC]), set(fc[PC])
    usable = set(fc.loc[fc["growth"].notna(), PC])
    print(f"delivery postcodes {len(pcs)} | in growth sheet {len(pcs & sheet)} "
          f"| with a usable ratio {len(pcs & usable)} | missing {len(pcs - sheet)}")
    print(f"growth sheet postcodes {len(sheet)} ({len(sheet - pcs)} with no delivery points "
          f"in the selected regions)")
    cells = served.groupby([PC, GRP]).size().rename("rows").reset_index()
    cells = cells.merge(fc[[PC, GRP, "growth"]], on=[PC, GRP], how="left")
    cells["aligned"] = cells["growth"].notna()
    cells["rows_aligned"] = cells["rows"].where(cells["aligned"], 0)
    print(cells.groupby(GRP).agg(postcodes=(PC, "size"), aligned=("aligned", "sum"),
                                 rows=("rows", "sum"), rows_aligned=("rows_aligned", "sum"))
          .to_string())
    miss = cells[~cells["aligned"]]
    if len(miss):
        by_reg = served.merge(miss[[PC, GRP]], on=[PC, GRP]).groupby([REG, PC, GRP]).size()
        print("not aligned (falls back, see `level`):")
        print(by_reg.rename("rows").to_string())

    print("\nProduct_type, today:")
    print(distribution(today, GRP).sort_values("rows", ascending=False).to_string())
    print(f"\n{CNT}, today:")
    print(distribution(today, CNT).to_string())


# ---------------------------------------------------------------- 4 scale

def seed_rows(today, sheet, selected, products):
    """The rows each cell is drawn from: today's, plus an OOH copy of each postcode's Parcel Post
    and Express Post rows wherever the sheet has OOH volume (flagged `_ooh`: never real)."""
    seed = today.assign(_ooh=False)
    if OOH not in products:
        return seed
    pcs = sheet.loc[(sheet[GRP] == OOH) & sheet[REG].isin(selected) & (sheet["v_to"] > 0), PC]
    ooh = today[today[GRP].isin(OOH_LIKE) & today[PC].isin(set(pcs))]
    return pd.concat([seed, ooh.assign(**{GRP: OOH}, _ooh=True)], ignore_index=True)


def cell_targets(a, today, seed, selected, sheet):
    """One row per growth cell (region, postcode, product): today's rows and articles, the ratio
    growth with its `level`, and `target` — the --to-fy volume in ARTICLES — then `future_n`,
    the whole-article target (largest remainder within each postcode, so a postcode's total is
    its target rounded; product totals may move by a few articles).

    --level sheet: target = the sheet's own --to-fy volume for the cell (level `sheet`); a cell
    the sheet does not cover takes today's articles x the fallback ratio. --level ratio: target =
    today's articles x the ratio; OOH (no articles today) = --to-fy volume x the postcode's
    articles today per sheet --from-fy volume. Unselected regions stay at today's articles;
    --growth overrides set target = today's articles x the given factor.
    """
    f = seed.groupby(KEY).size().rename("seed_rows").reset_index()
    base = today.groupby(KEY)[CNT].agg(n="size", articles="sum").reset_index()
    f = f.merge(base, on=KEY, how="left").fillna({"n": 0, "articles": 0})
    f = f.merge(sheet[[PC, GRP, "v_from", "v_to"]], on=[PC, GRP], how="left")
    f["growth"], f["level"] = np.nan, ""
    for level, on in [("postcode", [PC, GRP]), ("region", [REG, GRP]), ("product", [GRP])]:
        pool = sheet.groupby(on, as_index=False)[["v_from", "v_to"]].sum()
        pool = pool[pool["v_from"] > 0]
        pool["ratio"] = pool["v_to"] / pool["v_from"]
        ratio = f[on].merge(pool[on + ["ratio"]], on=on, how="left")["ratio"].to_numpy()
        fill = f["growth"].isna().to_numpy() & ~np.isnan(ratio)
        f.loc[fill, "growth"] = ratio[fill]
        f.loc[fill, "level"] = level

    f["target"] = f["articles"] * f["growth"]
    if a.level == "sheet":
        has = f["v_to"].notna()
        f.loc[has, "target"] = f.loc[has, "v_to"]
        f.loc[has, "level"] = "sheet"
    else:
        art_pc = today.groupby(PC)[CNT].sum()
        vol_pc = sheet[sheet[GRP] != OOH].groupby(PC)["v_from"].sum()
        o = f[GRP].eq(OOH)
        f.loc[o, "target"] = f.loc[o, "v_to"] * f.loc[o, PC].map(art_pc / vol_pc.where(vol_pc > 0))
        f.loc[o, "level"] = "ooh calibrated"

    off = ~f[REG].isin(selected)
    f.loc[off, "target"] = f.loc[off, "articles"]
    f.loc[off, "level"] = "not selected"

    if a.growth:
        ov = pd.read_csv(a.growth, usecols=KEY + ["growth"]).drop_duplicates(KEY, keep="last")
        if (ov["growth"] < 0).any():
            raise SystemExit("growth factors must be >= 0")
        f = f.merge(ov, on=KEY, how="left", suffixes=("", "_user"))
        hit = f["growth_user"].notna()
        f.loc[hit, "growth"] = f.loc[hit, "growth_user"]
        f.loc[hit, "target"] = f.loc[hit, "articles"] * f.loc[hit, "growth_user"]
        f.loc[hit, "level"] = "user"
        print(f"  --growth: {hit.sum()} cells overridden"
              + (f", {len(ov) - hit.sum()} rows match no growth cell" if len(ov) > hit.sum() else ""))
        f = f.drop(columns="growth_user")

    f["target"] = f["target"].fillna(0.0)
    f["future_n"] = largest_remainder(f["target"], [f[REG], f[PC]])   # postcode totals first
    return f[KEY + ["n", "articles", "v_from", "v_to", "growth", "level", "target", "future_n"]]


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


def rake(strata, cell_target, factors, bounds):
    """IPF over the strata (growth cell x bound attributes x Article_count), in ARTICLES.

    The seed `u` is today's articles per stratum, so the zero pattern survives. Margins: for each
    bound spec today's share of the attributes' ARTICLES within its scope times the scope's
    future articles, and every cell's target articles. With Article_count among the attributes
    a share of articles is the same as a share of rows (each group has one Article_count), so
    @product holds each product's parcel-count mix. The cells come LAST in each sweep, so the
    weights returned always sit on the cell targets and only the bound margins carry residual.
    """
    margins = []
    u = strata["u"]
    for scope, attrs in bounds:
        share = u.groupby([strata[c] for c in scope + attrs]).transform("sum") / (
            u.groupby([strata[c] for c in scope]).transform("sum") if scope else u.sum())
        if scope:
            total = factors.groupby(scope)["future_n"].sum().rename("t").reset_index()
            future = strata[scope].merge(total, on=scope, how="left")["t"].to_numpy(dtype=float)
        else:
            future = float(factors["future_n"].sum())
        margins.append((scope + attrs, share.to_numpy() * future))
    soft = margins[:1]                     # the first bound spec: what rounding protects next
    margins.append((KEY, cell_target))

    w = u.to_numpy(dtype=float)
    tol = IPF_TOL * max(factors["future_n"].sum(), 1.0)
    for it in range(IPF_MAX_ITER):
        worst = 0.0
        for cols, target in margins:
            cur = group_sum(w, strata, cols)
            worst = max(worst, np.abs(cur - target).max())
            w = w * np.divide(target, cur, out=np.zeros_like(target), where=cur > 0)
        if worst < tol:
            print(f"IPF converged in {it + 1} iterations")
            return w, soft
    raise SystemExit(
        f"IPF did not converge ({worst:,.1f} articles off after {IPF_MAX_ITER} iterations): "
        f"the --bound shares cannot all hold next to the cell targets. Usual cause: two specs "
        f"that overlap (Article_count@region with Article_count@product).")


def round_to_volume(w, strata, want, soft):
    """Whole ROWS per stratum whose articles (rows x Article_count) hit every cell's target EXACTLY.

    `w` is articles per stratum. Single-article rows are the slack that lands each cell exactly,
    so the multi-article rows are settled first: floor them, then add rows by largest fraction
    where the cell still has room and the stratum's bound group (`soft`: whole-row targets by
    largest remainder within the scope) is still short; the cell's remaining articles become
    single-article rows. A cell with no single-article stratum is filled by largest fraction
    wherever a row fits, and a gap it still cannot close is returned in `patch` (filled at
    sampling with 1-article copies). Cells win; only the bound margin carries what is left.
    """
    a = strata[CNT].to_numpy(dtype=int)
    cell = strata.groupby(KEY, sort=False).ngroup().to_numpy()
    ncell = cell.max() + 1
    x = w / a
    k = np.floor(x + 1e-9).astype(int)
    one = np.full(ncell, -1)
    for i in np.flatnonzero((a == 1) & (w > 0)):
        if one[cell[i]] < 0:
            one[cell[i]] = i
    slack = one[cell] >= 0                                          # the cell has 1-article rows
    k[slack & (a == 1)] = 0                                         # set last, as the slack
    left = want - np.bincount(cell, k * a, minlength=ncell).astype(int)

    by_rows = bool(soft) and CNT in soft[0][0][0]
    if by_rows:
        (cols, target), scope = soft[0]
        att = strata.groupby(cols, sort=False).ngroup().to_numpy()
        groups = pd.DataFrame({"att": att, "t": target / a}).groupby("att").first()
        group_scope = (strata.groupby(cols, sort=False)[scope].first().reset_index(drop=True)
                       if scope else pd.DataFrame({"_": np.zeros(len(groups))}))
        whole = largest_remainder(groups["t"], [group_scope[c] for c in group_scope.columns])
        short = whole - np.bincount(att, np.where(slack & (a == 1), 0, k), minlength=len(groups))
    order = np.argsort(-(x - np.floor(x + 1e-9)), kind="stable")
    order = order[w[order] > 0]                                     # never invent a combination
    for i in order:                                                 # multi-article rows first
        c = cell[i]
        if a[i] == 1 and slack[i]:
            continue
        if a[i] <= left[c] and (not by_rows or short[att[i]] > 0 or not slack[i]):
            if slack[i] or x[i] - k[i] > 0:
                k[i] += 1
                left[c] -= a[i]
                if by_rows:
                    short[att[i]] -= 1
    for c in np.flatnonzero(one >= 0):                              # the slack lands the cell
        k[one[c]] += left[c]
        if by_rows:
            short[att[one[c]]] -= left[c]
        left[c] = 0
    patch = {}
    for i in order:                                                 # cells with no 1-article rows
        c = cell[i]
        while left[c] >= a[i] > 0 and not slack[i]:
            k[i] += 1
            left[c] -= a[i]
    for c in np.flatnonzero(left > 0):
        patch[int(c)] = int(left[c])
    if by_rows:
        print(f"rounding: every cell's articles exact; {'+'.join(c for c in cols if c not in scope)}"
              f" groups off {(short != 0).sum()} of {len(short)} ({np.abs(short).sum():,} rows)"
              + (f"; {sum(patch.values())} articles in {len(patch)} cells with no single-article"
                 f" row added as 1-article copies" if patch else ""))
    return k, cell, patch


def sample_cell(rows, k, rng):
    """k rows from one stratum: keep all and add draws when growing, a subset when shrinking.
    An OOH seed stratum (`_ooh`) is never real: all k rows are draws."""
    n = len(rows)
    if rows["_ooh"].iat[0]:
        return rows.iloc[rng.choice(n, size=k, replace=True)].assign(is_synthetic=True)
    if k <= n:
        pick = rng.choice(n, size=k, replace=False)
        return rows.iloc[np.sort(pick)].assign(is_synthetic=False)
    extra = rows.iloc[rng.choice(n, size=k - n, replace=True)].assign(is_synthetic=True)
    return pd.concat([rows.assign(is_synthetic=False), extra])


def scale_step(a, today, selected, sheet, bounds, attrs):
    """(factors, future): the growth cells and the sampled future delivery points."""
    section(f"4 SCALE  each postcode x product to its {a.to_fy} volume in articles "
            f"(--level {a.level})")
    seed = seed_rows(today, sheet, selected, a.products)
    factors = cell_targets(a, today, seed, selected, sheet)
    factors.to_csv(a.out.parent / "growth_factors.csv", index=False)
    lv = factors.groupby("level", sort=False)[["n", "articles", "target"]].agg(["size", "sum"])
    lv = lv[[("n", "size"), ("n", "sum"), ("articles", "sum"), ("target", "sum")]].round(1)
    lv.columns = ["cells", "rows today", "articles today", "target articles"]
    print(lv.to_string())
    lost = sheet[sheet[REG].isin(selected) & sheet[GRP].isin(a.products) & (sheet["v_to"] > 0)]
    lost = lost.merge(factors[[PC, GRP]], on=[PC, GRP], how="left", indicator=True)
    lost = lost[lost["_merge"] == "left_only"]
    if len(lost):
        print(f"  {len(lost)} sheet cells have {a.to_fy} volume but no delivery points to draw from"
              f" (OOH: no Parcel/Express Post rows in the postcode): {lost['v_to'].sum():,.1f} "
              f"volume not placed")

    cols = KEY + list(dict.fromkeys(attrs + [CNT]))
    by_stratum = seed.groupby(cols)                           # sorted; drives the sampling order
    strata = by_stratum.size().rename("rows").reset_index()
    strata["u"] = strata["rows"] * strata[CNT]
    cell_target = strata.merge(factors, on=KEY, how="left")["future_n"].to_numpy(dtype=float)
    if bounds:
        w, margins = rake(strata, cell_target, factors, bounds)
        soft = [(margins[0], bounds[0][0])]
    else:
        cur = group_sum(strata["u"].to_numpy(float), strata, KEY)
        w = np.divide(strata["u"] * cell_target, cur, out=np.zeros(len(strata)), where=cur > 0)
        soft = []
    want = strata.assign(t=cell_target).groupby(KEY, sort=False)["t"].first().to_numpy(int)
    k, cell, patch = round_to_volume(w, strata, want, soft)

    rng = np.random.default_rng(a.seed)
    parts = [sample_cell(rows, kk, rng) for (_, rows), kk in zip(by_stratum, k)]
    if patch:
        first = pd.Series(range(len(strata))).groupby(cell).first()
        for c, m in patch.items():
            rows = by_stratum.get_group(tuple(strata.loc[first[c], cols]))
            parts.append(rows.iloc[rng.choice(len(rows), size=m)].assign(**{CNT: 1},
                                                                         is_synthetic=True))
    future = pd.concat(parts, ignore_index=True)
    o = future["_ooh"].to_numpy()
    future.loc[o, "Consignment_ID"] = [f"{OOH}_{pc}_{i + 1}" for i, pc in
                                       enumerate(future.loc[o, PC])]
    future = future.drop(columns="_ooh")
    got = future.groupby(KEY)[CNT].sum().reindex(pd.MultiIndex.from_frame(factors[KEY]),
                                                 fill_value=0)
    assert (got.to_numpy() == factors["future_n"].to_numpy()).all(), "cell articles drifted"
    print(f"future: {len(future):,} rows ({len(future) / len(today) - 1:+.1%}), "
          f"{future[CNT].sum():,} articles ({future[CNT].sum() / today[CNT].sum() - 1:+.1%}), "
          f"{future['is_synthetic'].sum():,} synthetic rows, {o.sum():,} of them {OOH}")
    return factors, future


# ---------------------------------------------------------------- 5 points

def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres, elementwise."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    h = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6_371_000.0 * np.arcsin(np.sqrt(h))


def snap_one(session, url, lon, lat):
    """OSRM /nearest for one point: (lon, lat) on the nearest driveable edge, or None."""
    for attempt in range(3):
        try:
            resp = session.get(f"{url}/nearest/v1/driving/{lon:.6f},{lat:.6f}", timeout=10)
            break
        except requests.RequestException:
            if attempt == 2:
                return None
            time.sleep(0.1 * (attempt + 1))
    if resp.status_code != 200 or (data := resp.json()).get("code") != "Ok":
        return None
    loc = (data.get("waypoints") or [{}])[0].get("location") or []
    return (float(loc[0]), float(loc[1])) if len(loc) == 2 else None


def snap_points(lon, lat, url, workers):
    """Snapped (lon, lat) arrays for every point, NaN where OSRM gave no road."""
    session = requests.Session()
    session.mount("http://", HTTPAdapter(pool_maxsize=workers))
    session.mount("https://", HTTPAdapter(pool_maxsize=workers))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        got = list(ex.map(lambda p: snap_one(session, url, *p), zip(lon, lat)))
    session.close()
    out = np.array([g if g else (np.nan, np.nan) for g in got], dtype=float).reshape(-1, 2)
    return out[:, 0], out[:, 1]


def check_osrm(url):
    s = requests.Session()
    if snap_one(s, url, 144.9631, -37.8136) is None:              # Melbourne GPO
        raise SystemExit(f"OSRM /nearest does not answer at {url}: start the server "
                         f"(docker start <osrm container>), pass --osrm, or --points copy")


def draw_in(poly, m, rng):
    """m uniform points inside `poly` (lon/lat), by rejection from its bounding box."""
    x0, y0, x1, y1 = poly.bounds
    xs, ys = [], []
    while sum(map(len, xs)) < m:
        x, y = rng.uniform(x0, x1, 2 * m), rng.uniform(y0, y1, 2 * m)
        ok = shapely.contains_xy(poly, x, y)
        xs.append(x[ok]), ys.append(y[ok])
    return np.concatenate(xs)[:m], np.concatenate(ys)[:m]


def points_step(a, future):
    """Synthetic rows moved to a random road point inside their postcode."""
    section("5 POINTS  synthetic rows: random point in the postcode, snapped to a road")
    syn = future.index[future["is_synthetic"]]
    if a.points == "copy" or not len(syn):
        print(f"--points {a.points}: {len(syn):,} synthetic rows keep the copied coordinate")
        return future
    check_osrm(a.osrm)

    polys = pd.read_csv(a.boundaries)
    polys = dict(zip(polys[BOUNDARY_PC].astype(int), shapely.from_wkt(polys["geometry"])))
    for g in polys.values():
        shapely.prepare(g)
    need = future.loc[syn].groupby(PC).groups                    # postcode -> row labels
    nopoly = [pc for pc in need if pc not in polys]
    if nopoly:
        print(f"  {sum(len(need[pc]) for pc in nopoly):,} rows in {len(nopoly)} postcodes "
              f"with no boundary keep the copied coordinate: {sorted(nopoly)}")
    want = {pc: len(rows) for pc, rows in need.items() if pc in polys}
    got = {pc: [] for pc in want}                                  # accepted (lat, lon, metres)
    rate = {pc: 0.8 for pc in want}                                # acceptance, to oversize draws
    rng = np.random.default_rng([a.seed, 5])                     # own stream: rows unchanged
    tally = dict(drawn=0, no_road=0, too_far=0, outside=0)

    for rnd in range(MAX_ROUNDS):
        short = {pc: want[pc] - len(got[pc]) for pc in want if len(got[pc]) < want[pc]}
        if not short:
            break
        batch = []
        for pc, k in short.items():
            m = int(np.ceil(k / max(rate[pc], 0.05) * 1.2)) + 2
            x, y = draw_in(polys[pc], m, rng)
            batch.append(pd.DataFrame({PC: pc, "lon": x, "lat": y}))
        c = pd.concat(batch, ignore_index=True)
        c["s_lon"], c["s_lat"] = snap_points(c["lon"], c["lat"], a.osrm, a.workers)
        c["m"] = haversine_m(c["lat"], c["lon"], c["s_lat"], c["s_lon"])
        no_road = c["s_lon"].isna()
        too_far = ~no_road & (c["m"] > a.max_snap_m)
        inside = np.zeros(len(c), bool)
        for pc, ix in c.groupby(PC).groups.items():
            ix = np.asarray(ix)
            inside[ix] = shapely.dwithin(polys[pc], shapely.points(
                c.loc[ix, "s_lon"].fillna(0), c.loc[ix, "s_lat"].fillna(0)), EDGE_TOL_DEG)
        ok = ~no_road & ~too_far & inside
        tally["drawn"] += len(c)
        tally["no_road"] += no_road.sum()
        tally["too_far"] += too_far.sum()
        tally["outside"] += (~no_road & ~too_far & ~inside).sum()
        for pc, g in c[ok].groupby(PC):
            got[pc].extend(zip(g["s_lat"], g["s_lon"], g["m"]))
        rate.update(ok.groupby(c[PC]).mean())
        print(f"  round {rnd + 1}: {len(c):,} candidates for {sum(short.values()):,} rows "
              f"in {len(short)} postcodes, {ok.sum():,} accepted")

    moved, metres, left = 0, [], 0
    for pc, rows in need.items():
        pts = got.get(pc, [])[:len(rows)]
        left += len(rows) - len(pts)
        if not pts:
            continue
        lat, lon, m = map(np.array, zip(*pts))
        idx = rows[:len(pts)]
        for col in LAT_COLS:
            future.loc[idx, col] = lat.round(6)
        for col in LON_COLS:
            future.loc[idx, col] = lon.round(6)
        moved += len(pts)
        metres.extend(m)

    print(f"candidates {tally['drawn']:,} | rejected: no road {tally['no_road']:,}, "
          f"> {a.max_snap_m:g} m from a road {tally['too_far']:,}, snapped outside the "
          f"postcode {tally['outside']:,}")
    print(f"synthetic rows {len(syn):,}: moved {moved:,}, kept the copied coordinate "
          f"{len(syn) - moved:,} ({left:,} unfilled after {MAX_ROUNDS} rounds)")
    if metres:
        q = np.percentile(metres, [50, 90, 99])
        print(f"snap distance m: median {q[0]:.0f}, p90 {q[1]:.0f}, p99 {q[2]:.0f}")
    return future


# ---------------------------------------------------------------- 6 verify

def compare(today, future, col, by=None, weight=None):
    """today % vs future % of `col` (overall or within `by`), with the difference in points."""
    t = distribution(today, col, by, weight)["%"].rename("today %")
    f = distribution(future, col, by, weight)["%"].rename("future %")
    tab = pd.concat([t, f], axis=1).fillna(0)
    tab["diff pp"] = (tab["future %"] - tab["today %"]).round(3)
    return tab


def max_diff(tab):
    return f"max |diff| {tab['diff pp'].abs().max():.3f} pp"


def verify_step(a, today, future, factors):
    section("6 VERIFY  today vs future")
    tab = factors.groupby([REG, GRP])[["articles", "target", "future_n"]].sum()
    tab["future"] = future.groupby([REG, GRP])[CNT].sum()
    tab.loc[("TOTAL", ""), :] = tab.sum()
    tab["growth"] = (tab["future"] / tab["articles"].where(tab["articles"] > 0)).round(3)
    tab["match"] = np.where(tab["future"] == tab["future_n"], "yes", "NO")
    print(f"Articles vs {a.to_fy} target, per region x product (target = the sheet's volume, "
          f"before rounding):")
    print(tab.rename(columns={"articles": "today", "future_n": "target (whole)"})
          .round(1).to_string())
    got = future.groupby(KEY)[CNT].sum().reindex(pd.MultiIndex.from_frame(factors[KEY]),
                                                 fill_value=0).to_numpy()
    err = np.abs(got - factors["target"].to_numpy())
    print(f"\npostcode x product ({len(factors):,} cells): articles = whole target in every cell: "
          f"{(got == factors['future_n'].to_numpy()).all()}; max |articles - target| "
          f"{err.max():.3f} (rounding to whole articles only)")

    print("\nProduct_type % of articles within each region — follows the sheet:")
    print(compare(today, future, GRP, by=REG, weight=CNT).to_string())
    cp = compare(today, future[future[GRP] != OOH], CNT, by=GRP)
    print(f"\n{CNT} % of delivery points within each product ({max_diff(cp)}), the 10 largest:")
    print(cp.sort_values("today %", ascending=False).head(10).to_string())
    if (future[GRP] == OOH).any():
        oc = compare(today[today[GRP].isin(OOH_LIKE)], future[future[GRP] == OOH], CNT)
        print(f"  {OOH} vs today's {'/'.join(OOH_LIKE)}: {max_diff(oc)}")
    print(f"  total (moves with the product mix): {max_diff(compare(today, future, CNT))}")
    print(f"\nheld: {a.bound}")


def comparison_report(a, today, future, sheet, factors, path):
    """Sheet volume vs generated volume, per (postcode, product).

    For every (Region, postcode, product) in the selected regions and products, either side:

      vol_<from>, vol_<to>, vol_growth   the sheet: --from-fy and --to-fy volume and their ratio
      base_articles                      today's articles (sum of Article_count)
      target_articles                    the --to-fy target before rounding (the sheet's volume
                                         under --level sheet; see `level` in growth_factors.csv)
      future_articles                    what was generated; gap_articles = future - target,
                                         vs_sheet_articles = future - vol_<to>
      articles_growth, change_articles(_pct), and the same for delivery points

    then an `All products` row per postcode. A blank ratio means a zero base (OOH today).
    """
    keys = [REG, PC, GRP]
    vf, vt = f"vol_{a.from_fy}", f"vol_{a.to_fy}"

    def side(df, tag):
        return df.groupby(keys).agg(**{f"{tag}_points": (CNT, "size"),
                                       f"{tag}_articles": (CNT, "sum")})

    vol = sheet.set_index(keys)[["v_from", "v_to"]].set_axis([vf, vt], axis=1)
    t = pd.concat([vol, side(today, "base"), factors.set_index(keys)["target"]
                   .rename("target_articles"), side(future, "future")], axis=1)
    num = [vf, vt, "base_articles", "target_articles", "future_articles", "base_points",
           "future_points"]
    if a.level == "sheet":                 # a sheet cell with nothing to draw from still has a target
        miss = t["target_articles"].isna()
        t.loc[miss, "target_articles"] = t.loc[miss, vt]
    t[num] = t[num].fillna(0)
    t = t.reset_index()
    every = t.groupby([REG, PC], as_index=False)[num].sum().assign(**{GRP: "All products"})
    t = pd.concat([t, every], ignore_index=True)
    t["_o"] = t[GRP].eq("All products")
    t = t.sort_values([REG, PC, "_o", GRP]).drop(columns="_o").reset_index(drop=True)

    def ratio(x, y):
        return (x / y.where(y > 0)).round(4)

    def add_measures(d):
        d["vol_growth"] = ratio(d[vt], d[vf])
        d["gap_articles"] = (d["future_articles"] - d["target_articles"]).round(3)
        d["vs_sheet_articles"] = (d["future_articles"] - d[vt]).round(3)
        d["articles_growth"] = ratio(d["future_articles"], d["base_articles"])
        for m in ("articles", "points"):
            b, f = d[f"base_{m}"], d[f"future_{m}"]
            d[f"change_{m}"] = f - b
            d[f"change_{m}_pct"] = (ratio(f - b, b) * 100).round(2)
        d[[vf, vt, "target_articles"]] = d[[vf, vt, "target_articles"]].round(3)
        return d

    t = add_measures(t)
    cols = [REG, PC, GRP, vf, vt, "vol_growth", "base_articles", "target_articles",
            "future_articles", "gap_articles", "vs_sheet_articles", "articles_growth",
            "change_articles", "change_articles_pct", "base_points", "future_points",
            "change_points", "change_points_pct"]
    t = t[cols]
    ints = ["base_articles", "future_articles", "change_articles", "base_points",
            "future_points", "change_points"]
    t[ints] = t[ints].astype(int)
    t.to_csv(path, index=False)
    cells = t[t[GRP] != "All products"].reset_index(drop=True)

    section("7 COMPARISON  sheet volume vs generated articles, per postcode and product")
    s = t[t[GRP] != "All products"].groupby(GRP)[num].sum()
    s.loc["All products"] = s.sum()
    s = add_measures(s)
    print(s[[vf, vt, "base_articles", "future_articles", "vs_sheet_articles", "articles_growth",
             "base_points", "future_points"]].round(2).to_string())
    cell = t[t[GRP] != "All products"]
    print(f"postcode x product: max |future - target| {cell['gap_articles'].abs().max():.3f} "
          f"articles; max |future - {vt}| {cell['vs_sheet_articles'].abs().max():.3f}")
    lost = cell[(cell["future_articles"] == 0) & (cell[vt] > 0)]
    if len(lost):
        print(f"  {len(lost)} sheet cells with {a.to_fy} volume but nothing generated (no rows to "
              f"draw from): {lost[vt].sum():,.1f} volume ({lost[vt].sum() / cell[vt].sum():.3%})")
    print(f"-> {path} ({t[PC].nunique()} postcodes, {len(t):,} rows)")
    return cells


def diff_cols(d, pairs):
    """For each (new, old, name): name = new - old and name_pct = (new - old) / old x 100."""
    for new, old, name in pairs:
        d[name] = d[new] - d[old]
        d[f"{name}_pct"] = (d[name] / d[old].where(d[old] > 0) * 100).round(2)
    return d


def report_workbook(a, today, future, cells, path):
    """future_demand_report.xlsx — volume = articles (sum of Article_count):

      postcode          per postcode: the sheet's --from-fy volume, base (today), target
                        (--to-fy), future (generated), and target/future vs base and future vs
                        target, absolute and %
      product           the same per product type, plus each product's share of the volume
      parcel_count      per product (and all products): delivery points and articles by
                        Article_count, base vs future shares and the difference in points.
                        OOH has no base: its base is today's Parcel Post + Express Post, the
                        rows it is drawn from (`base_from`)
      postcode_product  the cell-level table (= comparison_by_postcode_product.csv)
    """
    vf, vt = f"vol_{a.from_fy}", f"vol_{a.to_fy}"
    vol = ["base", "target", "future"]
    pairs = [("target", "base", "target_vs_base"), ("future", "base", "future_vs_base"),
             ("future", "target", "future_vs_target")]
    c = cells.rename(columns={vf: f"sheet_{a.from_fy}", "base_articles": "base",
                              "target_articles": "target", "future_articles": "future"})
    num = [f"sheet_{a.from_fy}"] + vol + ["base_points", "future_points"]

    pc = diff_cols(c.groupby([REG, PC], as_index=False)[num].sum(), pairs)
    pc[[f"sheet_{a.from_fy}", "target"]] = pc[[f"sheet_{a.from_fy}", "target"]].round(2)
    pc["future_vs_target"] = pc["future_vs_target"].round(2)

    pr = c.groupby(GRP)[num].sum()
    pr.loc["All products"] = pr.sum()
    pr = diff_cols(pr, pairs)
    for v in vol:
        pr[f"{v}_share_pct"] = (pr[v] / pr.loc["All products", v] * 100).round(3)
    pr["future_vs_base_share_pp"] = (pr["future_share_pct"] - pr["base_share_pct"]).round(3)
    pr = pr.round(2).reset_index()

    bins = [0, 1, 2, 3, 4, 5, 10, 20, np.inf]
    labels = ["1", "2", "3", "4", "5", "6-10", "11-20", "21+"]

    def mix(df, tag):
        b = pd.cut(df[CNT], bins, labels=labels)
        g = df.groupby(b, observed=False)[CNT].agg(["size", "sum"])
        g.columns = [f"{tag}_points", f"{tag}_articles"]
        for m in ("points", "articles"):
            g[f"{tag}_{m}_pct"] = (g[f"{tag}_{m}"] / g[f"{tag}_{m}"].sum() * 100).round(3)
        return g

    parts = []
    for prod in sorted(future[GRP].unique()) + ["All products"]:
        if prod == "All products":
            bt, ft, src = today, future, "today"
        elif prod == OOH:
            bt, ft, src = today[today[GRP].isin(OOH_LIKE)], future[future[GRP] == OOH], \
                " + ".join(OOH_LIKE)
        else:
            bt, ft, src = today[today[GRP] == prod], future[future[GRP] == prod], "today"
        m = pd.concat([mix(bt, "base"), mix(ft, "future")], axis=1)
        m["points_diff_pp"] = (m["future_points_pct"] - m["base_points_pct"]).round(3)
        m["articles_diff_pp"] = (m["future_articles_pct"] - m["base_articles_pct"]).round(3)
        parts.append(m.reset_index().rename(columns={CNT: "Article_count"})
                     .assign(**{GRP: prod, "base_from": src}))
    pcnt = pd.concat(parts, ignore_index=True)
    pcnt = pcnt[[GRP, "Article_count", "base_from", "base_points", "future_points",
                 "base_points_pct", "future_points_pct", "points_diff_pp", "base_articles",
                 "future_articles", "base_articles_pct", "future_articles_pct",
                 "articles_diff_pp"]]

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in [("postcode", pc), ("product", pr), ("parcel_count", pcnt),
                         ("postcode_product", cells)]:
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = "B2" if name == "product" else "C2"
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = max(
                    10, min(28, len(str(col[0].value)) + 2))

    section("8 REPORT  volume (articles): base, target, future")
    print("per product:")
    print(pr.set_index(GRP)[["base", "target", "future", "future_vs_base", "future_vs_base_pct",
                             "future_vs_target", "base_share_pct", "future_share_pct"]]
          .to_string())
    print(f"\nper postcode ({len(pc)}): max |future - target| "
          f"{pc['future_vs_target'].abs().max():.2f} articles; future vs base % — median "
          f"{pc['future_vs_base_pct'].median():.1f}, min {pc['future_vs_base_pct'].min():.1f}, "
          f"max {pc['future_vs_base_pct'].max():.1f}")
    print("\nparcel count (share of delivery points), max |diff| per product:")
    print(pcnt.groupby(GRP, sort=False)["points_diff_pp"].apply(lambda s: s.abs().max())
          .rename("max |diff| pp").to_string())
    print(f"-> {path}")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--demand", type=Path, default=DEMAND)
    ap.add_argument("--forecast", type=Path, default=FORECAST)
    ap.add_argument("--sheet", default=FORECAST_SHEET)
    ap.add_argument("--from-fy", default="FY26")
    ap.add_argument("--to-fy", default="FY42")
    ap.add_argument("--regions", nargs="+", default=REGIONS,
                    help="the sheet's Region values to keep and grow (substring, "
                         "case-insensitive), or 'all'")
    ap.add_argument("--other-regions", choices=["drop", "keep"], default="drop",
                    help="delivery points in other regions: drop, or keep at factor 1.0")
    ap.add_argument("--growth", type=Path,
                    help=f"CSV with {REG},{PC},{GRP},growth; "
                         "overrides the sheet's factor for the cells it lists")
    ap.add_argument("--bound", nargs="+", default=BOUND,
                    help="ATTR[+ATTR]@total|product|region specs to hold, or 'none'")
    ap.add_argument("--products", nargs="+", default=PRODUCTS,
                    help="Product_type values to keep and grow; the rest are dropped. "
                         f"'{OOH}' in the list builds OOH delivery points from the sheet")
    ap.add_argument("--level", choices=["sheet", "ratio"], default="sheet",
                    help="each postcode x product's future articles = the sheet's --to-fy "
                         "volume, or today's articles x the sheet's growth ratio")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--points", choices=["road", "copy"], default="road",
                    help="synthetic rows: a random road point inside the postcode (OSRM), "
                         "or the copied coordinate of the row they were drawn from")
    ap.add_argument("--boundaries", type=Path, default=BOUNDARIES)
    ap.add_argument("--osrm", default=OSRM_URL, help="OSRM server for /nearest")
    ap.add_argument("--max-snap-m", type=float, default=MAX_SNAP_M,
                    help="redraw a candidate further than this from a road")
    ap.add_argument("--workers", type=int, default=20, help="concurrent OSRM requests")
    ap.add_argument("--out", type=Path, default=OUT_DIR / "future_demand.csv")
    a = ap.parse_args()

    today = pd.read_csv(a.demand)
    today = today.drop(columns=[c for c in today.columns if c.startswith("Unnamed:")])
    missing = [c for c in [PC, GRP, CNT, *LAT_COLS, *LON_COLS] if c not in today.columns]
    if missing:
        raise SystemExit(f"{a.demand.name} has no {missing} column")
    out_products = today.loc[~today[GRP].isin(a.products), GRP].value_counts()
    if len(out_products):
        print(f"--products drops {out_products.sum():,} delivery points: "
              f"{out_products.to_dict()}")
    today = today[today[GRP].isin(a.products)].reset_index(drop=True)
    absent = sorted(set(a.products) - set(today[GRP]) - {OOH})
    if absent:
        print(f"--products {absent} have no delivery points and are not grown")
    bounds = parse_bound(a.bound)
    attrs = list(dict.fromkeys(c for _, cols in bounds for c in cols))
    unknown = [c for c in attrs if c not in today.columns]
    if unknown:
        raise SystemExit(f"--bound columns not in {a.demand.name}: {unknown}")
    if set(attrs) & set(KEY):
        raise SystemExit(f"{KEY} define the growth cell, they cannot also be bound")
    a.out.parent.mkdir(parents=True, exist_ok=True)

    fc, ooh = growth_step(a, today[GRP].unique())
    today, selected = regions_step(a, today, fc)
    alignment_step(today, selected, fc)
    sheet = pd.concat([fc, ooh] if OOH in a.products else [fc], ignore_index=True)
    factors, future = scale_step(a, today, selected, sheet, bounds, attrs)
    future = points_step(a, future)
    future.to_csv(a.out, index=False)
    print(f"-> {a.out}")
    verify_step(a, today, future, factors)
    sheet = sheet[sheet[REG].isin(selected) & sheet[GRP].isin(a.products)]
    cells = comparison_report(a, today, future, sheet, factors,
                              a.out.parent / "comparison_by_postcode_product.csv")
    report_workbook(a, today, future, cells, a.out.parent / "future_demand_report.xlsx")


if __name__ == "__main__":
    main()
