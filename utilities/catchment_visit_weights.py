"""Per-postcode collection weights for the chain-1 first-mile catchments.

WHY. s2b_build_chain1.py divides each site's pickup volume EQUALLY over its catchment
postcodes — PERCAT = ceil(want / NCAT * 100) / 100, one number per site — and then pins it
there, because the PUD-level Min equality equals NCAT x PERCAT. So every postcode a site
touches is modelled as collecting the same volume. The CCP booking extract says that is wrong
by up to three orders of magnitude within a single site: Melbourne Transport's postcode 3029
takes 29% of the site's stops and seven of its postcodes carry one weekly stop each.

WHAT THE WEIGHT IS. Stop-events per (facility, postcode) over one clean Mon-Fri week. Not
route-visits and not distinct addresses: at the only two sites where CCP records collected
volume, stop-events predict it best (r = 0.998 at Dandenong Transport, 0.988 at Melbourne
Transport, against 0.975/0.976 for route-visits and 0.941/0.252 for addresses).

SHAPE ONLY, NEVER LEVEL. CCP's recorded volume totals 4,130 EA/day against the model's
452,634 — 0.9%, and five of the seven sites record none at all. This file carries shares. The
level stays with PEAK_2025_* x PEAK_FACTOR in dials_chain1.csv.

THE FOUR BASIS DECISIONS (confirmed with the modeller, 2026-09-22):
  window        Mon-Fri 18-22 May 2026. One full week, so Monday is not counted twice (the
                extract also holds 25 May) and the 895-job Saturday and 27-job 26 May are out.
  weight        stop-events, as above.
  own buildings a van's stop at its own PDC or another DC-Vans site is a return or transfer,
                not a collection from the public in that postcode, so it does not count. The
                test runs ONLY over Location Type == Network: over Customer rows the same
                name patterns hit real customers (Kings Transport, Harris Scarfe - Laverton
                DC, Chemist Warehouse (Chadstone DC)).
  delivery-only ten (facility, postcode) cells appear in the catchment purely because the
                notebook's red-van branch never filtered Booking Type. Eight of them are in
                the polygon set and collect nothing, ever. They are dropped, not floored.

A CONSEQUENCE OF THE OWN-BUILDING RULE, worth naming because it was not obvious up front.
Eight further cells turn out to hold own-building stops and NOTHING else: a red van's only
visit to that postcode all week is to another site's dock (Oakleigh South's only stop in 3175
is Dandenong DC, Sunshine West's only stop in 3122 is Hawthorn DC). Under the rule above they
collect nothing from the public, so DROP_OWN_ONLY_CELLS drops them on the same grounds as the
delivery-only cells. No volume leaves the model when they go — the weights are normalised
within a site and the site total stays pinned by PEAK_2025_* — it redistributes over the
site's remaining postcodes. Set the flag False to weight them on their own-building stops
instead, which is the "exclude own PDC only" reading.

THE PER-SITE FILTER IS ASYMMETRIC ON PURPOSE, but only over our OWN BUILDINGS. Both site types
keep customer pickups and keep lodgement points; the transports additionally drop their other
Network stops, because those are linehaul. The booking comments say so outright — "Collect all
for Mulgrave PDC", "Collect for TPF - Priority Western Forwards", "Collect for Darebin PDC" —
they name a destination building, and 583 of them are collecting EMPTY equipment rather than
freight. The red vans' Network stops are post offices (7,025), retail outlets, street posting
boxes ("Clear SPBs as listed: Red") and lockers, which are genuine first mile.

LODGEMENT AT THE TRANSPORTS (added 2026-09-22, at the user's direction). The transports clear
post offices on their country runs, and filtering them to Customer dropped that: 62 stops a
week at Abbotsford, Romsey, Lancefield, Woodend, Gisborne, Cockatoo and Gembrook. Customers
lodging at one of our points IS first-mile volume — it is the same thing the red vans collect
at an LPO — so it counts. This is +2.2% of the transport stop base and SEVEN new cells, which
utilities/add_transport_lodgement_catchments.py adds to the polygon file.

WHAT IS STILL EXCLUDED, and why it is not lodgement: 6,046 transport Network stops a week at
Melbourne Parcel Facility, Tullamarine, Dandenong Letters, Sunshine West PDC, Melbourne North
PDC, Bayswater PDC. Freight already inside our network, moving between our own buildings.

    python utilities/catchment_visit_weights.py
"""

import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CCP = ROOT / "inputs/red_vans_inputs/CCP-18-05_to_25-05.csv"
POLY = ROOT / "inputs/melbourne/first_mile_catchment_polygons.csv"
OUT = ROOT / "inputs/melbourne/first_mile_catchment_weights.csv"
REPORT = ROOT / "outputs/first_mile_catchment_visit_frequency.csv"

WINDOW = ("2026-05-18", "2026-05-22")          # Mon-Fri
TRANSPORTS = {"Melbourne Transport", "Dandenong Transport Facility"}
COLLECTING = ["Pickup", "Pickup & Delivery"]
# our own buildings, tested over Location Type == Network ONLY (see docstring)
OWN = (r"PDC|PARCEL FACILITY|LETTERS CENTRE|VAN OP|VAN SERV|TRANSPORT|GATEWAY"
       r"|\bDC\b|\bMDC\b|STARTRACK|DELIVERY CENTRE")
# places the public lodges: post offices (LPO / RP, a retail outlet), lockers, posting boxes.
# RP is Retail Post, NOT one of our operational buildings — "Collins St West RP (Melbourne
# GPO)", "Malvern RP", "Coburg RP" — and the red-van sites already collect 2,144 RP stops a
# week, so treating it as an own-building name would delete real lodgement.
LODGE = r"\bLPO\b|POST OFFICE|POSTSHOP|\bRP\b|LOCKER|POST BOX|POSTING BOX|\bSPB\b"
# the level, for the report's EA columns only — mirrors dials_chain1.csv
PEAK = {"Melbourne Transport": 351535, "Sunshine West Van Services": 96849,
        "Oakleigh South Van Operations": 57493, "Melbourne North Van Operations": 43515,
        "Bayswater Van Operations": 37721, "Dandenong South Van Operations": 35427,
        "Dandenong Transport Facility": 24081}
PEAK_FACTOR = 0.70
DROP_OWN_ONLY_CELLS = True     # see docstring; False weights them on their own-building stops

MEASURES = ["Volumes Delivered", "Volumes Collected",
            "Expected Volumes Delivered", "Expected Volumes Collected"]


def load_ccp():
    """CCP arrives long: one job repeated over four measures, in blocks of four. Reshape to
    one row per job rather than pivoting — the natural key is not unique (364 collisions in
    84,218) and pivot_table on the full column set blows the cartesian product up."""
    df = pd.read_csv(CCP, low_memory=False)
    key = [c for c in df.columns if c not in ("Measure Names", "Measure Values")]
    w = df.iloc[::4][key].reset_index(drop=True)
    for i, m in enumerate(MEASURES):
        block = df.iloc[i::4]
        assert (block["Measure Names"] == m).all(), f"CCP is not in blocks of four at {m}"
        w[m] = pd.to_numeric(block["Measure Values"].values, errors="coerce")
    w = w.rename(columns={"Job planned dwell date": "job_date", "Location Address": "addr",
                          "Facility Wcc Name": "facility", "Route Name": "route",
                          "Location Type": "loc_type", "Booking Type": "booking_type",
                          "Location Name": "loc_name"})
    w["d"] = pd.to_datetime(w.job_date, format="%d/%m/%Y", errors="coerce")
    w["day"] = w.d.dt.date
    w["facility"] = w.facility.str.title()
    # the postcode is the second-last token of the address, as the catchment notebook reads it
    w["post_code"] = pd.to_numeric(w.addr.astype(str).str.split(" ").str[-2], errors="coerce")
    dropped = w.post_code.isna().sum()
    w = w[w.post_code.notna()].copy()
    w["post_code"] = w.post_code.astype(int)
    print(f"CCP: {len(w):,} jobs ({dropped} with no parsable postcode)")
    return w


def collection_slice(w):
    """The jobs that are a collection from the public, on each site's own filter."""
    wk = w[w.day.astype(str).between(*WINDOW)].copy()
    _name = wk.loc_name.astype(str).str.upper()
    _own = wk.loc_type.eq("Network") & _name.str.contains(OWN, regex=True, na=False)
    _lodge = wk.loc_type.eq("Network") & _name.str.contains(LODGE, regex=True, na=False)
    is_tr = wk.facility.isin(TRANSPORTS)
    sel = ((is_tr & wk.booking_type.isin(COLLECTING) & (wk.loc_type.eq("Customer") | (_lodge & ~_own)))
           | (~is_tr & wk.booking_type.isin(COLLECTING)))
    col = wk[sel].copy()
    col["own"] = _own[sel]
    print(f"Mon-Fri {WINDOW[0]}..{WINDOW[1]}: {len(wk):,} jobs -> {len(col):,} collection jobs, "
          f"of which {col.own.sum():,} at our own buildings (excluded)")
    return col


def summarise(t):
    g = t.groupby(["facility", "post_code"]).agg(
        stops=("addr", "size"), uniq_addr=("addr", "nunique"),
        uniq_routes=("route", "nunique"), days=("day", "nunique"),
        vol_coll=("Volumes Collected", "sum"),
        vol_exp=("Expected Volumes Collected", "sum")).reset_index()
    visits = t.groupby(["facility", "post_code"])[["route", "day"]].apply(
        lambda x: len(x.drop_duplicates()))
    g["route_visits"] = g.set_index(["facility", "post_code"]).index.map(visits)
    return g


def main():
    w = load_ccp()
    col = collection_slice(w)
    kept = summarise(col[~col.own])                       # the weight basis
    allstops = summarise(col)                             # for the report's comparison columns

    poly = pd.read_csv(POLY, usecols=["facility_name", "post_code"])
    m = poly.merge(kept, left_on=["facility_name", "post_code"],
                   right_on=["facility", "post_code"], how="left").drop(columns=["facility"])
    m = m.merge(allstops[["facility", "post_code", "stops", "route_visits"]].rename(
        columns={"stops": "stops_incl_own", "route_visits": "route_visits_incl_own"}),
        left_on=["facility_name", "post_code"], right_on=["facility", "post_code"],
        how="left").drop(columns=["facility"])
    num = [c for c in m.columns if c not in ("facility_name", "post_code")]
    m[num] = m[num].fillna(0)

    # ── drop the cells that collect nothing from the public ──────────────────────────
    start = len(m)
    dead = m[m.stops_incl_own == 0]
    print(f"\npolygon cells {start}")
    print(f"  dropping {len(dead)} delivery-only (never a collection booking, any basis):")
    for r in dead.itertuples():
        print(f"    {r.facility_name:32s} {r.post_code}")
    m = m[m.stops_incl_own > 0].copy()

    # a cell whose ONLY stops are at our own buildings — the own-building rule empties it
    orphan = m[m.stops == 0]
    if DROP_OWN_ONLY_CELLS:
        print(f"  dropping {len(orphan)} own-building-only (every stop is another site's dock):")
        for r in orphan.itertuples():
            print(f"    {r.facility_name:32s} {r.post_code}  "
                  f"{int(r.stops_incl_own):3d} stops, all own-building")
        m = m[m.stops > 0].copy()
    else:
        print(f"  keeping {len(orphan)} own-building-only cells on their own-building stops")
        m.loc[m.stops == 0, "stops"] = m.loc[m.stops == 0, "stops_incl_own"]
    print(f"  -> {len(m)} cells carry a weight ({start - len(m)} dropped)")

    # ── the weight ───────────────────────────────────────────────────────────────────
    site_stops = m.groupby("facility_name").stops.transform("sum")
    m["weight"] = (m.stops / site_stops).round(8)
    m["ncat"] = m.groupby("facility_name").post_code.transform("size")
    m["share_equal"] = (1 / m.ncat).round(8)
    for c in ["route_visits", "uniq_addr", "vol_coll"]:
        tot = m.groupby("facility_name")[c].transform("sum").replace(0, float("nan"))
        m[f"share_{c}"] = (m[c] / tot).round(8)

    bad = m.groupby("facility_name").weight.sum().sub(1).abs().max()
    assert bad < 1e-6, f"weights do not sum to 1 within a site (off by {bad})"

    m["site_EA"] = (m.facility_name.map(PEAK) * PEAK_FACTOR).apply(math.floor)
    m["equal_EA"] = (m.site_EA / m.ncat).round(1)
    m["weighted_EA"] = (m.site_EA * m.weight).round(1)
    m["delta_EA"] = (m.weighted_EA - m.equal_EA).round(1)

    m = m.sort_values(["facility_name", "weight"], ascending=[True, False])
    m[["facility_name", "post_code", "weight"]].to_csv(OUT, index=False)
    REPORT.parent.mkdir(exist_ok=True)
    m.to_csv(REPORT, index=False)
    print(f"\nwrote {OUT.relative_to(ROOT)}  ({len(m)} cells, 3 columns — what the build reads)")
    print(f"wrote {REPORT.relative_to(ROOT)}  (same cells, every measure and the EA impact)")

    print("\n=== weight spread per site ===")
    rep = m.groupby("facility_name").apply(lambda t: pd.Series({
        "cells": len(t), "site_EA": int(t.site_EA.iloc[0]),
        "equal_EA": round(t.equal_EA.iloc[0]),
        "min_EA": round(t.weighted_EA.min()), "median_EA": round(t.weighted_EA.median()),
        "max_EA": round(t.weighted_EA.max()),
        "max/min": round(t.weighted_EA.max() / max(t.weighted_EA.min(), 1), 1),
        "top5_pct": round(100 * t.weight.nlargest(5).sum(), 1),
        "equal_top5_pct": round(100 * 5 / len(t), 1)}), include_groups=False)
    print(rep.to_string())

    print("\n=== validation: stop-share vs recorded-volume share, the two sites that record it ===")
    for site in sorted(TRANSPORTS):
        t = m[(m.facility_name == site) & (m.vol_coll > 0)]
        print(f"  {site}: {len(t)} cells with volume, "
              f"weight~volume r = {t.weight.corr(t.share_vol_coll):.3f}")
    return m


if __name__ == "__main__":
    main()
