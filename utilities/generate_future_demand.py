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
  5  POINTS. A synthetic row keeps everything it drew except WHERE it is: it gets a new random
     point inside its postcode (vic_postcode_boundaries.csv), snapped to the nearest driveable
     road by OSRM /nearest. A candidate is redrawn when OSRM finds no road for it, when the
     road is more than --max-snap-m away (a lake, the bay, a park, a paddock interior), or
     when the snapped point lands outside the postcode. Rows still without a point after
     MAX_ROUNDS draws keep the copied coordinate and are counted. Real rows never move.
     --points copy skips this and keeps today's copied coordinates (the old output).
  6  VERIFY. Product group targets vs achieved per facility and in total, Product_type mix
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
DEMAND = ROOT / "inputs/deilvery_events/melbourne_catchment.csv"
FORECAST = ROOT / "inputs/future_demand/growth_volume_sheet.xlsx"
FORECAST_SHEET = "Delivery Volume (No OOH)"
FC_POSTCODE, FC_PRODUCT = "Del post code", "Product type"
BOUNDARIES = ROOT / "inputs/deilvery_events/vic_postcode_boundaries.csv"
OUT_DIR = ROOT / "outputs/future_demand"

FAC, PC, GRP = "Terminating_facility_name", "POSTCODE", "product_group"
KEY = [FAC, PC, GRP]                      # one growth cell
EXPRESS, PARCEL = GROUPS = ["Express Post", "Parcel Post"]
EXPRESS_PRODUCTS = {"eParcel Express"}
BOUND = ["Product_type@facility", "parcel_count@total"]
SCOPES = {"total": [], "group": [GRP], "facility": [FAC, GRP]}

IPF_TOL = 1e-9
IPF_MAX_ITER = 1000

OSRM_URL = "http://localhost:5001"   # the local osrm-server container
MAX_SNAP_M = 250          # a candidate further than this from a road is redrawn
EDGE_TOL_DEG = 1e-4       # ~10 m: a point snapped onto the postcode's boundary road is inside
MAX_ROUNDS = 30           # draw-and-snap rounds before a row keeps its copied coordinate
LAT_COLS, LON_COLS = ["Del_Lat", "delivery_lat"], ["Del_Long", "delivery_lon"]


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
    """(postcode, group, v_from, v_to, growth) — the sheet grouped by postcode and product."""
    sheets = pd.ExcelFile(path).sheet_names
    if sheet not in sheets:
        raise SystemExit(f"no sheet '{sheet}' in {path.name}; it has {sheets}")
    grid = pd.read_excel(path, sheet_name=sheet, header=None)
    # the header row is wherever the postcode column title sits
    is_head = grid.apply(lambda r: r.astype(str).str.strip().eq(FC_POSTCODE).any(), axis=1)
    if not is_head.any():
        raise SystemExit(f"no '{FC_POSTCODE}' header cell in sheet '{sheet}' of {path.name}")
    head = is_head.idxmax()
    raw = grid.iloc[head + 1:].set_axis(grid.iloc[head].astype(str).str.strip(), axis=1)
    fy = {m.group(1): c for c in raw.columns if (m := re.match(r"\s*(FY\d{2})", str(c)))}
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
    f["growth"] = f["v_to"] / f["v_from"].where(f["v_from"] > 0)    # NaN when no FY-from volume
    return f


def growth_step(a):
    section(f"1 GROWTH  {a.to_fy} / {a.from_fy} per (postcode, product)")
    fc = load_forecast(a.forecast, a.sheet, a.from_fy, a.to_fy)
    fc.to_csv(a.out.parent / "postcode_growth.csv", index=False)
    print(fc.groupby(GRP).agg(postcodes=(PC, "size"), v_from=("v_from", "sum"),
                              v_to=("v_to", "sum"), median=("growth", "median"),
                              min=("growth", "min"), max=("growth", "max"))
          .assign(pooled=lambda t: t["v_to"] / t["v_from"]).round(3).to_string())
    return fc


# ---------------------------------------------------------------- 2 facilities

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


def facilities_step(a, today):
    """(today, selected): today without the unselected facilities when they are dropped."""
    section("2 FACILITIES  delivery points kept and grown")
    selected = select_facilities(today[FAC].unique(), a.facilities)
    other = ~today[FAC].isin(selected)
    print(today[~other].groupby(FAC).size().rename("rows").to_string())
    print(f"delivery points {len(today):,} -> {(~other).sum():,} at {len(selected)} facilities"
          + (f" ({other.sum():,} elsewhere: {a.other_facilities})" if other.any() else ""))
    if a.other_facilities == "drop":
        today = today[~other].reset_index(drop=True)
    return today, selected


# ---------------------------------------------------------------- 3 alignment + today

def distribution(df, col, by=None):
    """Counts and % of `col`, overall or within each `by` value."""
    keys = ([by] if by else []) + [col]
    n = df.groupby(keys).size().rename("rows")
    pct = n / (n.groupby(level=by).transform("sum") if by else n.sum()) * 100
    return pd.DataFrame({"rows": n, "%": pct.round(3)})


def alignment_step(today, selected, fc):
    section("3 ALIGNMENT + TODAY'S DISTRIBUTIONS")
    served = today[today[FAC].isin(selected)]
    pcs, sheet = set(served[PC]), set(fc[PC])
    usable = set(fc.loc[fc["growth"].notna(), PC])
    print(f"delivery postcodes {len(pcs)} | in growth sheet {len(pcs & sheet)} "
          f"| with a usable ratio {len(pcs & usable)} | missing {len(pcs - sheet)}")
    print(f"growth sheet postcodes {len(sheet)} ({len(sheet - pcs)} not served by the "
          f"selected facilities)")
    cells = served.groupby([PC, GRP]).size().rename("rows").reset_index()
    cells = cells.merge(fc[[PC, GRP, "growth"]], on=[PC, GRP], how="left")
    cells["aligned"] = cells["growth"].notna()
    cells["rows_aligned"] = cells["rows"].where(cells["aligned"], 0)
    print(cells.groupby(GRP).agg(postcodes=(PC, "size"), aligned=("aligned", "sum"),
                                 rows=("rows", "sum"), rows_aligned=("rows_aligned", "sum"))
          .to_string())
    miss = cells[~cells["aligned"]]
    if len(miss):
        by_fac = served.merge(miss[[PC, GRP]], on=[PC, GRP]).groupby([FAC, PC, GRP]).size()
        print("not aligned (falls back, see `level`):")
        print(by_fac.rename("rows").to_string())

    print("\nProduct_type, today:")
    print(distribution(today, "Product_type").sort_values("rows", ascending=False).to_string())
    print("\nparcel_count, today:")
    print(distribution(today, "parcel_count").to_string())


# ---------------------------------------------------------------- 4 scale

def growth_factors(today, selected, fc, overrides):
    """One row per growth cell: n today, growth, the `level` it came from, future_n.

    The sheet has no facility, so the `facility` level gives each of OUR facilities the sheet
    pooled over the postcodes it serves (a shared postcode counts at both). Unselected
    facilities are held at 1.0; `overrides` (--growth) replaces the factor of the cells it lists.
    """
    f = today.groupby(KEY).size().rename("n").reset_index()
    served = f[KEY].merge(fc, on=[PC, GRP])
    f["growth"], f["level"] = np.nan, ""
    for level, on, src in [("postcode", [PC, GRP], fc), ("facility", [FAC, GRP], served),
                           ("group", [GRP], fc)]:
        pool = src.groupby(on, as_index=False)[["v_from", "v_to"]].sum()
        pool = pool[pool["v_from"] > 0]
        pool["ratio"] = pool["v_to"] / pool["v_from"]
        ratio = f[on].merge(pool[on + ["ratio"]], on=on, how="left")["ratio"].to_numpy()
        fill = f["growth"].isna().to_numpy() & ~np.isnan(ratio)
        f.loc[fill, "growth"] = ratio[fill]
        f.loc[fill, "level"] = level

    off = ~f[FAC].isin(selected)
    f.loc[off, "growth"] = 1.0
    f.loc[off, "level"] = "not selected"

    if overrides:
        ov = pd.read_csv(overrides, usecols=KEY + ["growth"]).drop_duplicates(KEY, keep="last")
        if (ov["growth"] < 0).any():
            raise SystemExit("growth factors must be >= 0")
        f = f.merge(ov, on=KEY, how="left", suffixes=("", "_user"))
        hit = f["growth_user"].notna() & ~np.isclose(f["growth_user"], f["growth"])
        f.loc[hit, "growth"] = f.loc[hit, "growth_user"]
        f.loc[hit, "level"] = "user"
        unused = len(ov) - f["growth_user"].notna().sum()
        print(f"  --growth: {hit.sum()} cells overridden"
              + (f", {unused} rows match no growth cell" if unused else ""))
        f = f.drop(columns="growth_user")

    f["future_n"] = largest_remainder(f["n"] * f["growth"], [f[FAC], f[GRP]])
    return f[KEY + ["growth", "level", "n", "future_n"]]


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
    """IPF weights over the strata (growth cell x bound attributes).

    The seed is today's count `n` per stratum, so the zero pattern survives. Margins: every
    cell's target, and for each bound spec today's share of the attributes within its scope
    times the scope's future total.
    """
    margins = [(KEY, cell_target)]
    for scope, attrs in bounds:
        n = strata["n"]
        share = n.groupby([strata[c] for c in scope + attrs]).transform("sum") / (
            n.groupby([strata[c] for c in scope]).transform("sum") if scope else n.sum())
        if scope:
            total = factors.groupby(scope)["future_n"].sum().rename("t").reset_index()
            future = strata[scope].merge(total, on=scope, how="left")["t"].to_numpy(dtype=float)
        else:
            future = float(factors["future_n"].sum())
        margins.append((scope + attrs, share.to_numpy() * future))

    w = strata["n"].to_numpy(dtype=float)
    tol = IPF_TOL * max(factors["future_n"].sum(), 1.0)
    for it in range(IPF_MAX_ITER):
        worst = 0.0
        for cols, target in margins:
            cur = group_sum(w, strata, cols)
            worst = max(worst, np.abs(cur - target).max())
            w = w * np.divide(target, cur, out=np.zeros_like(target), where=cur > 0)
        if worst < tol:
            print(f"IPF converged in {it + 1} iterations")
            return w
    raise SystemExit(
        f"IPF did not converge ({worst:,.1f} rows off after {IPF_MAX_ITER} iterations): "
        f"the --bound shares cannot all hold next to the growth. Usual cause: an attribute "
        f"that fixes the product group (Product_type@total) or two specs that overlap "
        f"(Product_type@facility with Product_type@group).")


def sample_cell(rows, k, rng):
    """k rows from one stratum: keep all and add draws when growing, a subset when shrinking."""
    n = len(rows)
    if k <= n:
        pick = rng.choice(n, size=k, replace=False)
        return rows.iloc[np.sort(pick)].assign(is_synthetic=False)
    extra = rows.iloc[rng.choice(n, size=k - n, replace=True)].assign(is_synthetic=True)
    return pd.concat([rows.assign(is_synthetic=False), extra])


def scale_step(a, today, selected, fc, bounds, attrs):
    """(factors, future): the growth cells and the sampled future delivery points."""
    section("4 SCALE")
    factors = growth_factors(today, selected, fc, a.growth)
    factors.to_csv(a.out.parent / "growth_factors.csv", index=False)
    lv = factors.groupby("level", sort=False)["n"].agg(["size", "sum"])
    lv.columns = ["cells", "rows today"]
    print(lv.to_string())

    # whole rows per stratum, summing exactly to each cell's target
    by_stratum = today.groupby(KEY + attrs)                   # sorted; drives the sampling order
    strata = by_stratum.size().rename("n").reset_index()
    cell_target = strata.merge(factors, on=KEY, how="left")["future_n"].to_numpy(dtype=float)
    w = rake(strata, cell_target, factors, bounds) if bounds else strata["n"].to_numpy(float)
    cur = group_sum(w, strata, KEY)
    w = np.divide(w * cell_target, cur, out=np.zeros_like(w), where=cur > 0)
    k = largest_remainder(w, [strata[c] for c in KEY])

    rng = np.random.default_rng(a.seed)
    future = pd.concat([sample_cell(rows, kk, rng) for (_, rows), kk in zip(by_stratum, k)],
                       ignore_index=True)
    got = future.groupby(KEY).size().reindex(pd.MultiIndex.from_frame(factors[KEY]),
                                              fill_value=0)
    assert (got.to_numpy() == factors["future_n"].to_numpy()).all(), "cells drifted"
    print(f"future: {len(future):,} rows ({len(future) / len(today) - 1:+.1%}), "
          f"{future['parcel_count'].sum():,} parcels, "
          f"{future['is_synthetic'].sum():,} synthetic rows")
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
    polys = dict(zip(polys[PC].astype(int), shapely.from_wkt(polys["geometry"])))
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

def compare(today, future, col, by=None):
    """today % vs future % of `col` (overall or within `by`), with the difference in points."""
    t = distribution(today, col, by)["%"].rename("today %")
    f = distribution(future, col, by)["%"].rename("future %")
    tab = pd.concat([t, f], axis=1).fillna(0)
    tab["diff pp"] = (tab["future %"] - tab["today %"]).round(3)
    return tab


def max_diff(tab):
    return f"max |diff| {tab['diff pp'].abs().max():.3f} pp"


def verify_step(a, today, future, factors):
    section("6 VERIFY  today vs future")
    tab = factors.groupby([FAC, GRP])[["n", "future_n"]].sum()
    tab["future"] = future.groupby([FAC, GRP]).size()
    tab["growth"] = (tab["future"] / tab["n"]).round(3)
    tab.loc[("TOTAL", ""), :] = [*tab[["n", "future_n", "future"]].sum(),
                                 round(len(future) / len(today), 3)]
    tab["match"] = np.where(tab["future"] == tab["future_n"], "yes", "NO")
    print("Product group rows vs growth target, per facility:")
    print(tab.rename(columns={"n": "today", "future_n": "target"}).to_string())

    def within(df, g):
        return df[df[GRP] == g]

    pf = compare(within(today, PARCEL), within(future, PARCEL), "Product_type", by=FAC)
    print(f"\nProduct_type % within each facility's {PARCEL} — held ({max_diff(pf)}):")
    print(pf.to_string())
    print(f"\n{EXPRESS} share of each facility — follows the growth sheet:")
    print(compare(today, future, GRP, by=FAC).xs(EXPRESS, level=GRP).to_string())
    pt = compare(today, future, "Product_type")
    print(f"\nProduct_type % total ({max_diff(pt)}; Express vs "
          f"Parcel Post follows the growth sheet):")
    print(pt.to_string())
    for g in GROUPS:
        pg = compare(within(today, g), within(future, g), "Product_type")
        print(f"  within {g}: {max_diff(pg)}")
    pc = compare(today, future, "parcel_count")
    print(f"\nparcel_count % total ({max_diff(pc)}):")
    print(pc.to_string())
    print(f"\nheld: {a.bound}")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--demand", type=Path, default=DEMAND)
    ap.add_argument("--forecast", type=Path, default=FORECAST)
    ap.add_argument("--sheet", default=FORECAST_SHEET)
    ap.add_argument("--from-fy", default="FY26")
    ap.add_argument("--to-fy", default="FY42")
    ap.add_argument("--facilities", nargs="+", default=["all"],
                    help="our Terminating_facility_name values to keep and grow (substring, "
                         "case-insensitive), or 'all'")
    ap.add_argument("--other-facilities", choices=["drop", "keep"], default="drop",
                    help="delivery points at other facilities: drop, or keep at factor 1.0")
    ap.add_argument("--growth", type=Path,
                    help="CSV with Terminating_facility_name,POSTCODE,product_group,growth; "
                         "overrides the sheet's factor for the cells it lists")
    ap.add_argument("--bound", nargs="+", default=BOUND,
                    help="ATTR[+ATTR]@total|group|facility specs to hold, or 'none'")
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
    today[GRP] = np.where(today["Product_type"].isin(EXPRESS_PRODUCTS), EXPRESS, PARCEL)
    bounds = parse_bound(a.bound)
    attrs = list(dict.fromkeys(c for _, cols in bounds for c in cols))
    unknown = [c for c in attrs if c not in today.columns]
    if unknown:
        raise SystemExit(f"--bound columns not in {a.demand.name}: {unknown}")
    if set(attrs) & set(KEY):
        raise SystemExit(f"{KEY} define the growth cell, they cannot also be bound")
    a.out.parent.mkdir(parents=True, exist_ok=True)

    fc = growth_step(a)
    today, selected = facilities_step(a, today)
    alignment_step(today, selected, fc)
    factors, future = scale_step(a, today, selected, fc, bounds, attrs)
    future = points_step(a, future)
    future.to_csv(a.out, index=False)
    print(f"-> {a.out}")
    verify_step(a, today, future, factors)


if __name__ == "__main__":
    main()
