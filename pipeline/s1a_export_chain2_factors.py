"""Export the measured CHAIN-2 factors from the scan-path reduction to CSV.

TWO HALVES, AND THE NAME ONLY DESCRIBES THE SECOND. Renamed from `export_model_factors.py`
(2026-08-31) because "model factors" never said which model, and everything written to
`inputs/factors_observed/` is chain 2's — chain 1 is a frozen build with no measured factor
in it. But PART 1 below is the SCAN REDUCTION, and that is not chain-2-specific:

    PART 1  the reduction — which scan name is which building (SORT_SITE / TERM_PDC /
            LODGE_PUD / alias), the itinerary rules (path_chain, cap_path, itinerary) and
            the cached per-parcel table (load_paths, CACHE). Imported by classify_entry.py,
            analyse_sort_residual.py, model_common.py, plotting/sankey_from_scans.py and
            plotting/sankey_facility_path.py — none of which builds chain 2. They want the
            measurement's vocabulary, and it lives here because it belongs beside its
            evidence, not because it belongs to chain 2.

    PART 2  the export — cohort / derive / filter_factors / derive_stages / filter_stages
            and main(), which write the six obs_*.csv the chain-2 notebook reads and its
            drift guard re-derives. This half is what the file is named for.

Splitting the two into `scan_reduction.py` + this file is the change that would make both
names true; it was considered and deferred, so this note stands in for it.

Full note: docs/export_chain2_factors.md#module-overview
"""
import argparse
import collections
import datetime as _dt
import re
from pathlib import Path

import json

import numpy as np
import pandas as pd

from _paths import DATA_ROOT, FASS, FOBS, RAW, SCAN_OUT as OUT   # noqa: E402  (see _paths.py)

HERE = DATA_ROOT                                    # kept: the docstrings and messages name it
SCAN = RAW / "all_scan_for_melbourne_pdc_20052026.csv"
CACHE = OUT / "consignment_paths.pkl"
COVJSON = OUT / "event_coverage.json"    # written beside the cache; needs the raw scan file
PDCJSON = OUT / "pdc_basis.json"         # what the delivering-depot basis cost, for the diagram
OUT_PATH = OUT / "full_path.csv"
DEMAND = RAW / "temp_clustered.csv"      # the model's own demand table, for the reconciliation check


# PART 1 — THE REDUCTION. 2.96 M scan events down to one row per…  → docs/export_chain2_factors.md#part-1-the-reduction-2-96
# ══════════════════════════════════════════════════════════════════════════════════════

# the facility dictionary, identical to…  → docs/export_chain2_factors.md#the-facility-dictionary-identical-to
SORT_SITE = {
    "MELBOURNE PARCEL FACILITY": "MPF",
    "TULLAMARINE PARCEL FACILITY": "TPF",
    "MELBOURNE GATEWAY FACILITY": "MGF",
    "MGF - AIRPORT FACILITY(HIRCHY)": "MGF",     # alias — 12 sorts
    "SUNSHINE WEST PDC": "SWP",
    "MELBOURNE NTH PARCEL FACILITY": "MNP",
    "BAYSWATER PDC": "BAY",
    # Bayswater PDC is on Holloway Drive and the scans use both names. LODGE_PUD has always
    # mapped this to PUD_Bayswater; leaving it out of SORT_SITE meant 909 machine sorts at the
    # depot's own machine read as "sorted somewhere outside Melbourne". Evidence it is one site:
    # 91.5% of Holloway consignments also carry a BAYSWATER PDC scan, Bayswater comes first on
    # 21,225 of those 21,254, 95.5% of Holloway-sorted parcels terminate at Bayswater, and 95 of
    # the 810 fold straight onto an adjacent Bayswater sort under the consecutive-repeat rule.
    "HOLLOWAY DR PARCEL OPERATIONS": "BAY",
    "DANDENONG LC": "DLC",
    "AVALON PARCEL FACILITY": "AVL",
    "AVALON PCL FACILITY TRANSPORT": "AVL",      # alias — 238 transfers, no sorts of its own
    "AVALON LETTERS FACILITY": "AVL",            # alias — 4 scans
}
# Deliberately NOT aliases, and each one was checked  → docs/export_chain2_factors.md#deliberately-not-aliases-and-each-one
# THE SOURCE TAG GRAMMAR (Change 37)  → docs/export_chain2_factors.md#the-source-tag-grammar-change-37
STAGE_TAG = "METRO_DEPOT"
SOURCE_FAMILIES = ("INTERSTATE", "METRO", "REGION", STAGE_TAG)


def tag_family(tag):
    """INTERSTATE_MPF -> INTERSTATE;  METRO_DEPOT -> METRO_DEPOT."""
    return STAGE_TAG if tag == STAGE_TAG else tag.split("_", 1)[0]


def tag_site(tag):
    """INTERSTATE_MPF -> MPF;  METRO_DEPOT -> None (the depot is the row)."""
    return None if tag == STAGE_TAG else tag.split("_", 1)[1]


MODEL_HUBS = ("MPF", "TPF", "MGF")
HUB_FACILITIES = {k for k, v in SORT_SITE.items() if v in MODEL_HUBS}
# Fixed row order for every table and for the diagram's sort columns: the three modelled hubs, then
# the sorting PDCs, then the two marginal sites. Deliberately not volume-ranked, so a class filter
# cannot reshuffle the rows under the reader.
SORT_ORDER = ["MPF", "TPF", "MGF", "SWP", "MNP", "BAY", "DLC", "AVL"]
# Node names follow the MODEL's convention (PUD_*), not display text, because
# melbourne-scan-path-analysis.ipynb shares this script's cached reduction and indexes on them.
# The diagram shortens them for labels at render time.
TERM_PDC = {
    "SUNSHINE WEST PARCEL DELIVERY": "PUD_Sunshine_West",
    "OAKLEIGH SOUTH PDC": "PUD_Oakleigh_South",
    "BAYSWATER PDC": "PUD_Bayswater",
    "DAREBIN PDC": "PUD_Darebin",
    "MELBOURNE NORTH PDC": "PUD_Melbourne_North",
    "DANDENONG SOUTH PDC": "PUD_Dandenong_South",
    "TULLAMARINE PDC": "PUD_Tullamarine",
    "PAKENHAM PARCEL DELIVERY": "PUD_Pakenham",
    "ABBOTSFORD PARCEL DELIVERY": "PUD_Abbotsford",
    "MOUNT WAVERLEY PARCEL DELIVERY": "PUD_Mount_Waverley",
    "MULGRAVE PDC": "PUD_Mulgrave",
}


def depot_label(pud):
    """PUD_Sunshine_West -> Sunshine West, for the diagram's right-hand column."""
    return pud.replace("PUD_", "").replace("_", " ")
# ── Lodgement facility -> the depot it belongs to ─────────────────────────────────────
# A depot appears in the scans under up to three names: its own, its sorting hall, and its
# first-mile van arm. Mapping all three is what lets us spot a parcel that was lodged at a depot
# and delivered by that same depot — the observable proxy for the model's overnight stage.
LODGE_PUD = {
    "SUNSHINE WEST PARCEL DELIVERY": "PUD_Sunshine_West", "SUNSHINE WEST PDC": "PUD_Sunshine_West",
    "SUNSHINE WEST VAN SERVICES": "PUD_Sunshine_West",
    "BAYSWATER PDC": "PUD_Bayswater", "HOLLOWAY DR PARCEL OPERATIONS": "PUD_Bayswater",
    "BAYSWATER VAN OPERATIONS": "PUD_Bayswater",
    "MELBOURNE NORTH PDC": "PUD_Melbourne_North", 
    "MELBOURNE NTH PARCEL FACILITY": "PUD_Melbourne_North",
    "MELBOURNE NORTH VAN OPERATIONS": "PUD_Melbourne_North",
    "OAKLEIGH SOUTH PDC": "PUD_Oakleigh_South", "OAKLEIGH SOUTH VAN OPERATIONS": "PUD_Oakleigh_South",
    "DANDENONG SOUTH PDC": "PUD_Dandenong_South",
    "DANDENONG SOUTH VAN OPERATIONS": "PUD_Dandenong_South",
    "DAREBIN PDC": "PUD_Darebin", "TULLAMARINE PDC": "PUD_Tullamarine",
    "PAKENHAM PARCEL DELIVERY": "PUD_Pakenham", "ABBOTSFORD PARCEL DELIVERY": "PUD_Abbotsford",
    "MOUNT WAVERLEY PARCEL DELIVERY": "PUD_Mount_Waverley", "MULGRAVE PDC": "PUD_Mulgrave",
}
# A DEPOT WHOSE ROUNDS ARE RUN FROM ANOTHER DEPOT'S ADDRESS  → docs/export_chain2_factors.md#a-depot-whose-rounds-are-run
CONTRACTOR_BASE = {"PUD_Mulgrave": "PUD_Oakleigh_South"}
# ── WHICH DEPOTS HAVE A SORTER ────────────────────────────────────────────────────────
# Derived, never typed: a depot has sort capacity if one of ITS OWN buildings is a machine-sort
# site. Sunshine West, Melbourne North and Bayswater (as itself and as Holloway Dr) are in both
# dictionaries; every other depot is in LODGE_PUD only. The membership is asserted against the
# scans at build time — see compute_kept_on_site — so a depot that starts running a machine, or
# an alias that gets added to SORT_SITE, cannot silently change the rule without failing.
SORT_CAPABLE_PUDS = frozenset(pud for fac, pud in LODGE_PUD.items() if fac in SORT_SITE)
# WHERE IN VICTORIA WAS IT LODGED — METRO OR REGIONAL  → docs/export_chain2_factors.md#where-in-victoria-was-it-lodged
# WHY REGION IS AN UPPER BOUND  → docs/export_chain2_factors.md#why-region-is-an-upper-bound
VIC_UNPLACED_BAND = "REGION"
assert VIC_UNPLACED_BAND in ("REGION", "METRO")
METRO_BOUNDARY = RAW / "first_mile_catchment_dissolved_all.geojson"
NODES_XLSX = RAW / "all-data.xlsx"                     # the `nodes` sheet — one point per building
ROUTE_STOPS = RAW / "first_mile_route_stops.geojson"   # 10,606 first-mile pickup points
SORT_ONLY_CSV = FASS / "sort_only_sites.csv"           # Avalon's point, declared there as ASSUMED
# A sort site's point. Every OTHER lodgement name resolves through LODGE_PUD, which already knows
# a depot's three names, so this dictionary is only the buildings that are not depots — plus the
# three depots that are also sort sites, which cost nothing to name here and make the map total.
SITE_NODE = {"MPF": "Melbourne Parcel Facility", "TPF": "Tullamarine Parcel Facility",
             "MGF": "Melbourne Gateway Facility", "DLC": "Dandenong Letter Center",
             "AVL": "Avalon Parcel Facility", "SWP": "Sunshine West PDC",
             "MNP": "Melbourne North PDC", "BAY": "Bayswater PDC"}
# Lodgement names that are a real building under a name neither dictionary carries. Kept tiny and
# explicit for the same reason SORT_SITE's aliases are: a name we do not map is not an error, it
# is silently unplaceable, and unplaceable now moves volume into REGION.
LODGE_NODE_ALIAS = {"MPF BULK PARCELS": "Melbourne Parcel Facility"}


def _lodge_key(s):
    """Facility name -> a comparison key. Brackets, punctuation and the obvious synonyms out.

    The scans, the nodes sheet and the route-stop file were written by three different systems
    and none of them spells a building the same way: `BAYSWATER PDC (BWPDC) - VANS` and
    `BAYSWATER VAN OPERATIONS` are one address. This is deliberately blunt — it only has to make
    two spellings of the SAME name meet, never to guess that two different names are one place.
    """
    s = re.sub(r"\(.*?\)", " ", str(s).upper())
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    for a, b in (("PARCEL COLLECTION", "PC"), ("PCL COLLECTION", "PC"), ("POST SHOP", "PS"),
                 ("POST OFFICE", "PO"), ("DELIVERY CENTRE", "DC"), ("PARCEL FACILITY", "PF"),
                 ("VAN OPERATIONS", "VO"), ("VAN SERVICES", "VO"), ("VAN OPS", "VO"),
                 ("VANS", "VO")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def _node_points():
    """Every modelled building's coordinate, keyed by the name the nodes sheet uses.

    all-data.xlsx is the model's own node table and is authoritative for the 15 buildings it
    carries. Avalon is not one of them — `sort_only_sites.csv` supplies it and flags the point as
    an ASSUMPTION there, which matters here more than it does anywhere else in the repo: Avalon
    is the one network facility that falls OUTSIDE the boundary, so 1,921 EA of the region band
    rests on a coordinate nobody has confirmed.
    """
    n = pd.read_excel(NODES_XLSX, sheet_name="nodes")[["Facility", "lat", "long"]]
    extra = pd.read_csv(SORT_ONLY_CSV)
    extra = (extra[extra.lat.notna()][["node_label", "lat", "long"]]
             .rename(columns={"node_label": "Facility"}))
    n = pd.concat([n[~n.Facility.isin(extra.Facility)], extra], ignore_index=True)
    return {r.Facility.upper(): (r.long, r.lat) for r in n.itertuples()}


def _pud_node(points):
    """PUD_Sunshine_West -> the nodes-sheet row that is that depot. Derived, never typed.

    Asserted rather than defaulted: a depot with no point would place none of its lodgements and
    quietly move the lot into REGION, which is exactly the kind of silence this file avoids.
    """
    out = {}
    for pud in set(LODGE_PUD.values()):
        lab = depot_label(pud).upper()
        hit = [k for k in points if k.startswith(lab)]
        assert hit, f"no all-data.xlsx node for {pud} — add it, or the depot cannot be placed"
        out[pud] = sorted(hit, key=len)[0]
    return out


def lodgement_geography(lodge_fac):
    """Put every lodgement facility on the map, then ask the boundary which side it is on.

    Full note: docs/export_chain2_factors.md#lodgement-geography
    """
    import geopandas as gpd
    from shapely.geometry import Point

    names = pd.Index(lodge_fac.dropna().unique())
    points = _node_points()
    pud_node = _pud_node(points)

    # layer 1 — the model's own nodes, via every dictionary that names a building
    def node_of(name):
        for cand in (LODGE_NODE_ALIAS.get(name),
                     SITE_NODE.get(SORT_SITE.get(name)),
                     pud_node.get(LODGE_PUD.get(name)),
                     name):
            if cand and cand.upper() in points:
                return points[cand.upper()]
        return None

    # layer 2 — the first-mile pickup points, by name
    stops = gpd.read_file(ROUTE_STOPS, engine="pyogrio")
    stops["key"] = stops.location_name.map(_lodge_key)
    by_stop = (stops.drop_duplicates("key").set_index("key")[["google_longitude",
                                                             "google_latitude"]])
    # layer 3 — the suburb on a pickup point's address, as a fallback point for a retail name
    stops["suburb"] = (stops.location_address
                       .str.extract(r"([A-Z][A-Z ]+?)\s+\d{4}\s+VIC", expand=False).str.strip())
    by_sub = (stops.dropna(subset=["suburb"])
              .groupby("suburb")[["google_longitude", "google_latitude"]].median())
    NOISE = {"LPO", "PS", "PO", "DC", "PC", "BC", "MDC", "PDC", "SC", "VO", "PF", "STARTRACK",
             "STC", "AP", "BUSINESS", "CENTRE", "CNTRE", "CTR", "MAIL", "RETAIL", "TRANSPORT",
             "BULK", "PARCELS", "FULFILMENT", "PLAZA", "STREET", "ST", "RD", "ROAD", "THE",
             "VIC", "OPERATIONS", "OPS", "SERVICES", "DELIVERY", "FACILITY", "PARCEL"}

    def suburb_of(name):
        toks = [t for t in _lodge_key(name).split() if t not in NOISE]
        for n in range(len(toks), 0, -1):          # longest run first: HOPPERS CROSSING > HOPPERS
            for i in range(len(toks) - n + 1):
                k = " ".join(toks[i:i + n])
                if k in by_sub.index:
                    r = by_sub.loc[k]
                    return (r.google_longitude, r.google_latitude)
        return None

    rows = []
    for name in names:
        xy, lay = node_of(name), "node"
        if xy is None:
            k = _lodge_key(name)
            if k in by_stop.index:
                r = by_stop.loc[k]
                xy, lay = (r.google_longitude, r.google_latitude), "stop"
        if xy is None:
            xy, lay = suburb_of(name), "suburb"
        rows.append((name, xy[0] if xy else np.nan, xy[1] if xy else np.nan,
                     lay if xy else "unplaced"))
    place = pd.DataFrame(rows, columns=["lodge_fac", "lon", "lat", "geo_from"]).set_index("lodge_fac")

    boundary = gpd.read_file(METRO_BOUNDARY, engine="pyogrio").geometry.union_all()
    ok = place.lon.notna()
    place["metro"] = pd.Series(pd.NA, index=place.index, dtype="object")
    place.loc[ok, "metro"] = [boundary.contains(Point(x, y))
                              for x, y in zip(place.lon[ok], place.lat[ok])]

    out = pd.DataFrame(index=lodge_fac.index)
    out["lodge_lon"] = lodge_fac.map(place.lon)
    out["lodge_lat"] = lodge_fac.map(place.lat)
    out["lodge_geo_from"] = lodge_fac.map(place.geo_from).fillna("no_lodge_scan")
    out["lodge_metro"] = lodge_fac.map(place.metro)
    return out

def print_lodge_geography(s, w, unit):
    """What the boundary could and could not answer — printed apart, every run.

    The REGION band is only as good as the geocoding under it, so the three ways a Victorian
    parcel can end up there are never pooled into one number. `placed outside` is measurement;
    the other two are the assumption VIC_UNPLACED_BAND makes, and their size is the size of the
    doubt. If the last two ever grow past the first, the band is describing the geocoder rather
    than the network.
    """
    vic = s.origin == "VIC"
    tot = w[vic].sum()
    print(f"  lodgement geography: {tot:,} {unit} lodged in Victoria, placed by")
    for lay in ("node", "stop", "suburb", "unplaced", "no_lodge_scan"):
        m = vic & (s.lodge_geo_from == lay)
        if m.any():
            print(f"      {lay:<14} {w[m].sum():>9,}  {w[m].sum() / max(tot, 1):>6.1%}"
                  f"  ({s.lodge_fac[m].nunique():,} facilities)")
    inside = vic & (s.lodge_metro.fillna(False).astype(bool))
    outside = vic & s.lodge_metro.notna() & ~s.lodge_metro.fillna(True).astype(bool)
    unknown = vic & s.lodge_metro.isna()
    print(f"    METRO  {w[inside].sum():>9,}  {w[inside].sum() / max(tot, 1):>6.1%}"
          f"   inside the first-mile boundary — measured")
    print(f"    REGION {w[outside | unknown].sum():>9,}  "
          f"{w[outside | unknown].sum() / max(tot, 1):>6.1%}   of which:")
    print(f"        placed outside {w[outside].sum():>9,}  measured — a point outside the polygon")
    print(f"        UNPLACEABLE    {w[unknown].sum():>9,}  ASSUMED region "
          f"(VIC_UNPLACED_BAND={VIC_UNPLACED_BAND!r}); every geocoding layer is metro-only, so "
          f"this is an upper bound")
    top = (w[unknown].groupby(s.lodge_fac[unknown]).sum().sort_values(ascending=False).head(6))
    if len(top):
        print("          biggest unplaced: "
              + ", ".join(f"{k} {v:,}" for k, v in top.items()))


PRODUCT_MAP = {"eParcel Express": "EP", "Metro Next Day": "PP", "eParcel Standard": "PP",
               "eParcel Returns": "PP", "rParcel Post Plus": "PP"}
CLASSES = ("EP", "PP")
MODEL_PDCS = sorted(set(TERM_PDC.values()))
# WHICH DEPOT DELIVERED IT — THE PLAN, OR THE VAN?  → docs/export_chain2_factors.md#which-depot-delivered-it-the-plan
# CHANGE 39 — DROP THE PARCELS NO DEPOT SCAN CAN PLACE  → docs/export_chain2_factors.md#change-39-drop-the-parcels-no
DROP_UNSCANNED_DEPOT = True
ACCEPT_EVENT = "ZPT_ACCEPT"       # the depot taking it on, NOT ZPT_ACCEPT_FACILITY — see above
PDC_BASIS = "deliver_then_accept_then_plan"
# deliver_then_accept_then_plan | deliver_then_plan | deliver_scan | terminating_facility


# THE REDUCTION — 2.96 M scan events down to one row per parcel  → docs/export_chain2_factors.md#the-reduction-2-96-m-scan
# ══════════════════════════════════════════════════════════════════════════════════════

# THREE EVIDENCE BARS, NOT ONE  → docs/export_chain2_factors.md#three-evidence-bars-not-one
HANDLED = ["ZPT_UNLOAD_ITEMS", "ZPT_DEPART_CONTAINER", "ZPT_LOAD_ITEM", "ZPT_TRANSFER",
           "ZPT_MACHINE_SORT"]
# ── WHAT COUNTS AS "THE PARCEL WAS IN THIS BUILDING AT ALL" ───────────────────────────
# The eight events ops supplied as evidence of presence — a wider net than HANDLED, used for the
# whole-journey chain and for the coverage table, never for deciding a cross-dock. The remaining
# 54 event types are administrative or notification types attributed to facilities that never
# held the freight; letting those in is the same mistake that overstated the cross-dock.
PHYSICAL = ["ZPT_LOAD_ITEM", "ZPT_LODGE", "ZPT_DEPART_CONTAINER", "ZPT_ACCEPT_FACILITY",
            "ZPT_UNLOAD_ITEMS", "ZPT_MACHINE_SORT", "ZPT_TRANSFER", "ZPT_DELIVER"]

# WHICH OF THOSE EVENTS MAY ANSWER "WHERE DID IT COME FROM"  → docs/export_chain2_factors.md#which-of-those-events-may-answer
ORIGIN_FALLBACK_EVENTS = ["ZPT_MACHINE_SORT", "ZPT_LOAD_ITEM", "ZPT_ACCEPT_FACILITY",
                          "ZPT_TRANSFER"]

# The column order of the emitted row, grouped the way the steps run.
PATH_COLS = ["Consignment_ID",
             # 0b identity
             "Product_type", "Article_count", "Terminating_facility_name", "articles", "cls",
             "deliver_fac", "pdc", "pdc_term", "pdc_deliver",
             # 0c presence
             "full_chain", "full_path", "n_facilities", "states_touched",
             # 0d itinerary — the buildings it was IN, capped to what the model can carry
             "path_sites", "path_n", "path_interior", "path_before", "path_after",
             "path_merged", "path_fell", "path_why",
             # 1 source
             "lodge_fac", "lodge_state", "first_state", "origin", "origin_from", "lodge_pud",
             # 2 sort
             "chain", "path", "nrounds", "first", "second", "last", "sorted_elsewhere",
             "recv_site", "recv_seq", "sort_site", "sort_site_seq", "received", "crossdocked",
             "hub_seq", "sort_seq", "hub_first",
             # 3 delivery
             "same_depot_end_to_end", "kept_on_site", "sorted_before_delivery", "delivery_date",
             "hours_at_depot", "delivered_on_peak_day", "source_band",
             "lodge_band", "lodge_metro", "lodge_geo_from", "pdc_accept", "pdc_from"]
# `unscanned` is not carried: the rows it marks are dropped in build_paths, so it would be a
# column of False in every surviving row. It is reported instead, by report_pdc_basis.


def read_scans():
    """STEP 0 — every scan, ordered.

    `Event_seq` is the only ordering we trust. The dates are when the row reached the database,
    not when the parcel moved: they spread about 20 days either side of the delivery. `dt` is
    built anyway because step 3 needs the DATE a parcel first reached its delivering depot, which
    is a different question from the order of events.
    """
    df = pd.read_csv(SCAN, dtype=str,
                     usecols=["Consignment_ID", "Event_seq", "Event_type", "Event_facility_name",
                              "Terminating_facility_name", "Product_type", "Article_count",
                              "STE_NAME21", "Event_date", "Event_local_time"])
    df["dt"] = pd.to_datetime(df.Event_date + " " + df.Event_local_time.fillna("00:00:00"),
                              errors="coerce")
    df["Event_seq"] = pd.to_numeric(df["Event_seq"])
    return df.sort_values(["Consignment_ID", "Event_seq"])


def consignment_identity(df):
    """STEP 0b — what the parcel IS, and which depot delivered it. One value per consignment.

    Full note: docs/export_chain2_factors.md#consignment-identity
    """
    g = df.groupby("Consignment_ID")
    for col in ("Product_type", "Article_count", "Terminating_facility_name"):
        assert (g[col].nunique(dropna=False) <= 1).all(), f"{col} varies within a consignment"
    attr = g[["Product_type", "Article_count", "Terminating_facility_name"]].first()
    attr["articles"] = pd.to_numeric(attr.Article_count)
    attr["cls"] = attr.Product_type.map(PRODUCT_MAP)
    # the LAST delivery scan, not the first: an attempted-then-redelivered parcel is the work of
    # the depot that finally got rid of it
    attr["deliver_fac"] = (df[df.Event_type == "ZPT_DELIVER"].groupby("Consignment_ID")
                             .Event_facility_name.last())
    # the LAST acceptance at one of OUR depots — mapped first, so an accept at a post office
    # cannot outrank an earlier accept at a real depot just by being later
    _acc = df[df.Event_type == ACCEPT_EVENT].copy()
    _acc["pud"] = _acc.Event_facility_name.map(LODGE_PUD)
    attr["accept_fac"] = (_acc[_acc.pud.notna()].groupby("Consignment_ID")
                            .Event_facility_name.last())
    attr["pdc_term"] = attr.Terminating_facility_name.map(TERM_PDC)
    attr["pdc_deliver"] = attr.deliver_fac.map(LODGE_PUD)
    attr["pdc_accept"] = attr.accept_fac.map(LODGE_PUD)
    assert attr.cls.notna().all(), "unmapped Product_type"
    assert attr.pdc_term.notna().all(), (
        "Terminating_facility_name holds a site that is not one of the 11 modelled depots: "
        f"{set(attr.Terminating_facility_name[attr.pdc_term.isna()])}")
    assert PDC_BASIS in ("deliver_then_accept_then_plan", "deliver_then_plan", "deliver_scan",
                         "terminating_facility"), f"PDC_BASIS={PDC_BASIS!r}"
    # a scan at the depot's own contractor base is not a reassignment — see CONTRACTOR_BASE.
    # Checked as a PAIR so only the known arrangement is overridden, and applied to the accept
    # rung too: the contractor accepts the freight at its own address on the depot's behalf.
    registered = attr.pdc_term.map(CONTRACTOR_BASE)
    scan = attr.pdc_deliver.mask(registered.notna() & (attr.pdc_deliver == registered))
    acc = attr.pdc_accept.mask(registered.notna() & (attr.pdc_accept == registered))
    attr["pdc"] = {"deliver_scan": scan,
                   "terminating_facility": attr.pdc_term,
                   "deliver_then_plan": scan.fillna(attr.pdc_term),
                   "deliver_then_accept_then_plan":
                       scan.fillna(acc).fillna(attr.pdc_term)}[PDC_BASIS]
    attr["pdc_from"] = np.where(scan.notna(), "deliver_scan",
                        np.where(acc.notna(), "accept_scan", "plan"))
    # Change 39 — the plan rung, minus the part the contractor base explains
    by_contractor = ((attr.pdc_deliver.notna() & (attr.pdc_deliver == registered))
                     | (attr.pdc_accept.notna() & (attr.pdc_accept == registered)))
    attr["unscanned"] = (attr.pdc_from == "plan") & ~by_contractor
    if DROP_UNSCANNED_DEPOT:
        attr.loc[attr.unscanned, "pdc"] = np.nan     # build_paths drops these, once, and reports
        attr.loc[attr.unscanned, "pdc_from"] = "unscanned"
    # the ladder can only ever answer with a modelled depot — LODGE_PUD and TERM_PDC both map
    # into the same eleven, so this is structural, not a filter. Asserted so it stays that way.
    assert set(attr.pdc.dropna()) <= set(TERM_PDC.values()), \
        f"the delivering-depot ladder produced a site outside the terminating list: " \
        f"{set(attr.pdc.dropna()) - set(TERM_PDC.values())}"
    return attr


def report_pdc_basis(attr):
    """What the delivering-depot basis costs — measured every rebuild whichever basis is active.

    The deliver-scan arithmetic is computed even when the plan is the basis, because "what would
    the delivery scan give us" is a question that gets re-asked and should not need a rebuild to
    answer. Two ways to lose a parcel on that basis and they are not the same loss: no delivery
    scan at all is a gap in the extract, a delivery scan at an LPO is a real parcel that a real
    depot really did not put out. On the deliver basis both are dropped; both are named.
    """
    w, tot = attr.articles, attr.articles.sum()
    keep = attr.pdc.notna()
    live = PDC_BASIS == "deliver_scan"
    print(f"  delivering depot from {PDC_BASIS}: {w[keep].sum():,} of {tot:,} articles kept "
          f"({w[keep].sum() / tot:.2%})")
    no_scan = attr.deliver_fac.isna()
    off = attr.deliver_fac.notna() & attr.pdc_deliver.isna()
    lost = no_scan | off
    if PDC_BASIS == "deliver_then_plan":
        print(f"    {w[~lost].sum():,} articles ({w[~lost].sum() / tot:.2%}) take the depot from "
              f"their delivery scan; {w[lost].sum():,} ({w[lost].sum() / tot:.2%}) fall back to "
              f"the plan because the scan names no depot")
    if PDC_BASIS == "deliver_then_accept_then_plan":
        print("    the three-rung ladder (Change 38):")
        for rung, lab in (("deliver_scan", "1. last ZPT_DELIVER at one of our depots"),
                          ("accept_scan", f"2. last {ACCEPT_EVENT} at one of our depots"),
                          ("plan", "3. Terminating_facility_name (the plan)")):
            m = attr.pdc_from == rung
            print(f"      {lab:<48} {w[m].sum():>9,}  {w[m].sum() / tot:>6.2%}")
        _u = attr.unscanned
        if _u.any():
            _verb = "DROPPED" if DROP_UNSCANNED_DEPOT else "kept on the plan"
            print(f"      of rung 3, {w[_u].sum():,} EA ({w[_u].sum() / tot:.2%}) have NO depot "
                  f"scan on either event and no contractor base to explain it — {_verb} "
                  f"(DROP_UNSCANNED_DEPOT={DROP_UNSCANNED_DEPOT})")
            print("        by planned depot: " + ", ".join(
                f"{depot_label(k)} {v:,}" for k, v in
                w[_u].groupby(attr.pdc_term[_u]).sum().sort_values(ascending=False).items()))
            _c = attr.pdc_term.isin(CONTRACTOR_BASE) & (attr.pdc_from == "plan") & ~_u
            print(f"        the other {w[_c].sum():,} EA on rung 3 ARE explained by a contractor "
                  f"base and are kept: " + ", ".join(
                      f"{depot_label(a)} via {depot_label(b)}" for a, b in CONTRACTOR_BASE.items()))
        _a = attr.pdc_from == "accept_scan"
        if _a.any():
            _ag = _a & (attr.pdc_accept == attr.pdc_term)
            print(f"      the accept rung AGREES with the plan on {w[_ag].sum():,} of its "
                  f"{w[_a].sum():,} EA ({w[_ag].sum() / max(w[_a].sum(), 1):.1%}) — it only "
                  f"changes the answer for {w[_a & ~_ag].sum():,}")
            _mv = w[_a & ~_ag].groupby([attr.pdc_term[_a & ~_ag],
                                        attr.pdc_accept[_a & ~_ag]]).sum().sort_values(
                                            ascending=False).head(3)
            if len(_mv):
                print("      top moves it makes: " + ", ".join(
                    f"{depot_label(a)}->{depot_label(b)} {v:,}" for (a, b), v in _mv.items()))
    print(f"    the deliver-scan basis {'drops' if live else 'WOULD drop'} "
          f"{w[lost].sum():,} articles ({w[lost].sum() / tot:.2%}): "
          f"{w[no_scan].sum():,} with no ZPT_DELIVER scan anywhere, "
          f"{w[off].sum():,} delivered from a site that is not one of the {len(MODEL_PDCS)} "
          f"depots ({attr.deliver_fac[off].nunique()} distinct names)")
    print(f"    that volume, by the depot it is planned for: " + ", ".join(
        f"{depot_label(k)} {v:,}" for k, v in
        w[lost].groupby(attr.pdc_term[lost]).sum().sort_values(ascending=False).items()))
    disagree = ~lost & (attr.pdc_deliver != attr.pdc_term)
    contractor = disagree & (attr.pdc_deliver == attr.pdc_term.map(CONTRACTOR_BASE))
    moved = disagree & ~contractor
    print(f"    the scan disagrees with the plan on {w[disagree].sum():,} articles "
          f"({w[disagree].sum() / tot:.2%}); {w[contractor].sum():,} of that is a depot's own "
          f"contractor base and is NOT a reassignment ("
          + ", ".join(f"{depot_label(a)} via {depot_label(b)}"
                      for a, b in CONTRACTOR_BASE.items()) + ")")
    print(f"    net {w[moved].sum():,} articles ({w[moved].sum() / tot:.2%}) actually change "
          f"depot; top moves: " + (", ".join(
        f"{depot_label(a)}->{depot_label(b)} {v:,}" for (a, b), v in
        w[moved].groupby([attr.pdc_term[moved], attr.pdc_deliver[moved]]).sum()
                .sort_values(ascending=False).head(3).items()) or "none"))
    gone = sorted(set(MODEL_PDCS) - set(attr.pdc_deliver.dropna()))
    for g in gone:
        held = CONTRACTOR_BASE.get(g)
        print(f"    ⚠ no delivery scan EVER names {depot_label(g)}" + (
            f" — its rounds are closed under {depot_label(held)}, where its contractor is "
            f"registered, so CONTRACTOR_BASE keeps its {w[attr.pdc == g].sum():,} articles "
            f"with it and counts that building as its own"
            if held else
            " — on the deliver basis that depot runs no round at all"
            + (", and the model's demand table still gives it volume" if live else "")))
    return dict(
        basis=PDC_BASIS, extract=int(tot), kept=int(w[keep].sum()),
        deliver_kept=int(w[~lost].sum()), lost=int(w[lost].sum()),
        lost_no_scan=int(w[no_scan].sum()), lost_off_network=int(w[off].sum()),
        off_network_names=int(attr.deliver_fac[off].nunique()),
        moved=int(w[moved].sum()), disagree=int(w[disagree].sum()),
        contractor_held=int(w[contractor].sum()),
        contractor_pairs=[[depot_label(a), depot_label(b)] for a, b in CONTRACTOR_BASE.items()],
        depots_lost=[depot_label(g) for g in gone],
        lost_by_planned_depot={depot_label(k): int(v) for k, v in
                               w[lost].groupby(attr.pdc_term[lost]).sum()
                                      .sort_values(ascending=False).items()},
        top_moves=[[depot_label(a), depot_label(b), int(v)] for (a, b), v in
                   w[moved].groupby([attr.pdc_term[moved], attr.pdc_deliver[moved]]).sum()
                           .sort_values(ascending=False).head(4).items()])


def presence_scans(df):
    """STEP 0c — the scans that place the parcel in a building, consecutive repeats collapsed.

    Row-level, not one row per consignment: steps 1 and 2 both slice it. Collapsing consecutive
    repeats means the sequence lists BUILDINGS in order, not scans.
    """
    fc = df[df.Event_type.isin(PHYSICAL) & df.Event_facility_name.notna()].copy()
    return fc[fc.Event_facility_name != fc.groupby("Consignment_ID").Event_facility_name.shift()]


def journey(fc, index):
    """STEP 0c (cont.) — the whole journey, end to end, wherever it went.

    The sort chain in step 2 is deliberately narrow — Melbourne machine sorts only, because that
    is what the model's nodes are — so it cannot show the lodgement point, the interstate leg,
    the air leg or the delivering depot. This can.
    """
    j = pd.DataFrame(index=index).join(
        fc.groupby("Consignment_ID").Event_facility_name.apply(list).rename("full_chain")).join(
        fc.groupby("Consignment_ID").STE_NAME21
          .apply(lambda x: list(dict.fromkeys(x.dropna()))).rename("states_touched"))
    for col in ("full_chain", "states_touched"):
        j[col] = j[col].apply(lambda x: x if isinstance(x, list) else [])
    j["full_path"] = j.full_chain.apply(lambda x: " > ".join(x) if x else "(no physical scan)")
    j["n_facilities"] = j.full_chain.str.len()
    return j


# STEP 0d — THE ITINERARY. Which buildings the parcel was IN, in…  → docs/export_chain2_factors.md#step-0d-the-itinerary-which-buildings
# WHY THE MODEL READS A PATH AND NOT A ROLE  → docs/export_chain2_factors.md#why-the-model-reads-a-path
# ONE BUILDING, SEVERAL NAMES  → docs/export_chain2_factors.md#one-building-several-names
# ══════════════════════════════════════════════════════════════════════════════════════
VAN_SUFFIX = (" VAN OPERATIONS", " VAN SERVICES")
PUD_BUILDING = {pud: name for name, pud in TERM_PDC.items()}
VAN_HOME = {n: PUD_BUILDING[pud] for n, pud in LODGE_PUD.items() if n.endswith(VAN_SUFFIX)}
assert VAN_HOME and all(VAN_HOME.values()), "a van arm has no depot building to fold onto"
FOLD = [((" PARCEL DELIVERY", " PDC"), "PDC")]
MERGE = [("MELBOURNE NTH PARCEL FACILITY", "MELBOURNE NORTH PDC")]   # told, not derived
ALIAS = {}                                          # raw scan name -> the name its node carries
ALIASED_SORT, ALIASED_PUD = dict(SORT_SITE), dict(LODGE_PUD)
STATE_JSON = OUT / "facility_state.json"            # facility -> state, built once off the scans


def alias(name):
    """The name of the building this scan facility IS — one of the scans' own names."""
    return ALIAS.get(name, name)


def fold_key(name):
    """The building a scan name belongs to. Two names with one key are one building."""
    for suffixes, kind in FOLD:
        for suffix in suffixes:
            if name.endswith(suffix):
                return name[:-len(suffix)] + "\x00" + kind
    return name


def sort_key(name):
    """The model's OWN alias set: two names with one SORT_SITE code are one building."""
    return "\x00SORT:" + SORT_SITE[name] if name in SORT_SITE else None


def learn_aliases(volume):
    """Fold names onto one name per building; the busiest name wins. Returns what it did.

    Takes the article volume per raw name rather than a bare list, because which of two names a
    building should carry is not a matter of taste: it is the one the scans mostly use.
    """
    ALIAS.clear()
    parent = {n: n for n in volume}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

    for key in (fold_key, sort_key, VAN_HOME.get):
        seen = {}
        for n in volume:
            k = key(n)
            if k is None:
                continue
            union(n, seen.setdefault(k, n))
    for n in volume:                      # a van arm whose depot is named differently
        if n in VAN_HOME:
            union(n, VAN_HOME[n])
    for a, b in MERGE:
        union(a, b)
    comp = {}
    for n in volume:
        comp.setdefault(find(n), []).append(n)
    for members in comp.values():
        w = max(members, key=lambda n: (volume[n], n))
        for n in members:
            if n != w:
                ALIAS[n] = w
    # the reduction's own dictionaries, re-keyed on the surviving name, so a modelled site is
    # still recognised under whichever of its names won. A name that folds onto another must
    # agree with it about which depot it is — asserted, because a silent clash moves volume.
    for d, src in ((ALIASED_SORT, SORT_SITE), (ALIASED_PUD, LODGE_PUD)):
        d.clear()
        for k, v in src.items():
            assert d.setdefault(alias(k), v) == v, f"alias clash on {alias(k)}"
    return {c: sorted(v) for c, v in
            {w: [n for n in ALIAS if ALIAS[n] == w] for w in set(ALIAS.values())}.items()}


def canon_state(state):
    """The facility -> state map, re-keyed on canonical names.

    Names that fold together are one building, so they must be in one state; a clash means the
    fold is wrong and is worth failing on rather than resolving quietly.
    """
    out = {}
    for fac, st in state.items():
        got = out.setdefault(alias(fac), st)
        assert got == st, f"{alias(fac)} is in two states: {got} and {st}"
    return out


def facility_state(df=None, rebuild=False):
    """Facility name -> state, cached beside the reduction.

    The state comes off the SCAN (`STE_NAME21`), never off the building's name, and every facility
    in the extract is placed — which is what lets the path start at the state border rather than
    at a guess about which names sound Victorian.
    """
    if STATE_JSON.exists() and not rebuild:
        return json.loads(STATE_JSON.read_text())
    if df is None:
        print(f"  reading states off {SCAN.name} (once; cached to {STATE_JSON.name})")
        df = pd.read_csv(SCAN, usecols=["Event_facility_name", "STE_NAME21"], dtype=str)
    g = (df[df.Event_facility_name.notna()].groupby("Event_facility_name").STE_NAME21
          .agg(lambda s: s.dropna().mode().iat[0] if s.notna().any() else None).dropna())
    STATE_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATE_JSON.write_text(json.dumps(g.to_dict(), sort_keys=True))
    return g.to_dict()


# WHICH EVENTS MAY NAME A BUILDING ON THE PATH  → docs/export_chain2_factors.md#which-events-may-name-a-building
EVIDENCE = {
    "entry": ["ZPT_LODGE", "ZPT_MACHINE_SORT", "ZPT_TRANSFER", "ZPT_LOAD_ITEM",
              "ZPT_ACCEPT_FACILITY"],
    "physical": PHYSICAL,
    "dock": ["ZPT_UNLOAD_ITEMS", "ZPT_DEPART_CONTAINER", "ZPT_LOAD_ITEM", "ZPT_TRANSFER",
             "ZPT_MACHINE_SORT"],
    "sort": ["ZPT_MACHINE_SORT"],
}


def collapse_chain(d, order):
    """Event rows -> one ordered building list per consignment, consecutive repeats collapsed."""
    d = d.sort_values(["Consignment_ID", order], kind="stable")
    keep = ((d.Consignment_ID != d.Consignment_ID.shift())
            | (d.Event_facility_name != d.Event_facility_name.shift()))
    return d[keep].groupby("Consignment_ID").Event_facility_name.apply(list)


def bar_chain(df, bar):
    """The raw (unaliased) building chain on one evidence bar. Names stay raw: `path_chain`
    folds them itself and needs the raw form to measure what the fold was worth."""
    d = df[df.Event_type.isin(EVIDENCE[bar]) & df.Event_facility_name.notna()]
    return collapse_chain(d, "Event_seq")


def model_names():
    """The buildings the model has a node for — the eight sort sites and the eleven depots,
    under whichever of their names the fold left standing."""
    return ({alias(n) for n in SORT_SITE} | {alias(n) for n in TERM_PDC}
            | {alias(n) for n in LODGE_PUD})


def model_node(name):
    """The MODEL's name for one of our buildings: a sort-site code, or the depot's PUD.

    SORT_SITE is asked first, so BAYSWATER PDC — which is both a sorting site and a delivering
    depot — resolves to BAY on the path and stays PUD_Bayswater as a destination, exactly as the
    model carries the two as separate nodes.
    """
    for raw, code in SORT_SITE.items():
        if alias(raw) == name:
            return code
    for raw, pud in {**TERM_PDC, **LODGE_PUD}.items():
        if alias(raw) == name:
            return pud
    return None


def depot_buildings():
    """PUD -> every canonical facility name that IS that depot. The set a journey ends at."""
    own = {}
    for fac, pud in ALIASED_PUD.items():
        own.setdefault(pud, set()).add(fac)
    return own


# ══ WHY THERE IS NOTHING ON THE PATH ══════════════════════════════════════════════════
# An empty itinerary is not one population, and pooling the four hides the only interesting thing
# about them: whether the parcel skipped our network or the extract simply never saw it anywhere
# else. Tested in the order the chain is cut down, so a parcel lands in exactly one — and they
# replace the single 4,665 EA "assumed" blob the role basis reported, which is what Change 39
# turned into the manual-sort measurement.
Z_DEPOT, Z_INTER, Z_OUTSIDE, Z_NOSCAN = -4, -5, -6, -7
ZERO_REASON = {0: "path", Z_DEPOT: "depot_first", Z_INTER: "interstate_only",
               Z_OUTSIDE: "outside_only", Z_NOSCAN: "no_scan"}


def path_chain(raw, own, state, group=None):
    """One parcel's itinerary: the VICTORIAN buildings it was in before it reached its depot.

    Full note: docs/export_chain2_factors.md#path-chain
    """
    chain = []
    for name in raw:                      # fold the names FIRST, then re-collapse: a parcel
        name = alias(name)                # scanned at SUNSHINE WEST PARCEL DELIVERY and then at
        if not chain or chain[-1] != name:  # SUNSHINE WEST PDC never left the building
            chain.append(name)
    idx = [i for i, f in enumerate(chain) if f in own]
    ch = chain[:idx[-1]] if idx else list(chain)      # cut at the LAST arrival at its own depot
    vic = [x for x in ch if state.get(x) == "Victoria"]
    seen = len(vic)
    if group is not None:
        vic = [x for x in vic if x in group]
    fell = seen - len(vic)
    cut = len(vic)
    vic = [x for i, x in enumerate(vic) if not i or vic[i - 1] != x]
    revisit = cut - len(vic)
    oidx = [i for i, x in enumerate(raw) if alias(x) in own]
    old = [x for x in (raw[:oidx[-1]] if oidx else raw) if state.get(alias(x)) == "Victoria"]
    why = (0 if vic else
           Z_OUTSIDE if seen else         # it had Victorian buildings; none were ours
           Z_INTER if ch else             # everything before the depot was interstate
           Z_DEPOT if idx else            # its own depot is the FIRST thing on the chain
           Z_NOSCAN)                      # no qualifying scan at all on this bar
    return (vic, len(ch) - seen, (len(chain) - 1 - idx[-1] if idx else 0),
            len(old) - seen, bool(old) and old[0].endswith(VAN_SUFFIX), fell, why, revisit)


# THE MODEL HAS TWO SORT ROUNDS, SO THE PATH IS CAPPED AT TWO  → docs/export_chain2_factors.md#the-model-has-two-sort-rounds
PATH_BASIS = "facility_path"     # facility_path | machine_sort (the role basis, unchanged)
PATH_TOUCH_BAR = "entry"         # which EVIDENCE bar may name a building
PATH_DEPTH = 2                   # buildings kept before the delivering depot
PATH_CAP_RULE = "first_last"     # first_last | first_n


def cap_path(sites):
    """The path the model can carry, and how many buildings that cost. See the note above."""
    if len(sites) <= PATH_DEPTH:
        return list(sites), 0
    if PATH_CAP_RULE == "first_n":
        return list(sites[:PATH_DEPTH]), len(sites) - PATH_DEPTH
    return list(sites[:PATH_DEPTH - 1]) + [sites[-1]], len(sites) - PATH_DEPTH


def itinerary(df, identity, index, articles=None):
    """STEP 0d (cont.) — the whole extract's itineraries, as model nodes.

    A NON-SORTING DEPOT IS A LEGITIMATE STOP. `group` is all nineteen buildings the model carries,
    not the eight sort sites, so a parcel first seen at Oakleigh South enters there rather than at
    a hub the assumed rung would have named. That is 5,236 EA (3.2%), two-fifths of it Oakleigh
    South at position 1, and `model_node` gives those stops their PUD name — so a leg may run from
    a depot, and the notebook has to carry a node for it rather than the exporter inventing a hub.
    """
    state = canon_state(facility_state(df))
    raw = bar_chain(df, PATH_TOUCH_BAR).reindex(index)
    raw = [c if isinstance(c, list) else [] for c in raw]
    w = pd.Series(1, index=index) if articles is None else articles.reindex(index)
    unit = "consignments" if articles is None else "articles"

    vol = collections.Counter()
    for chain, a in zip(raw, w):
        for name in set(chain):
            vol[name] += a
    for name in state:
        vol.setdefault(name, 0)
    folds = learn_aliases(vol)
    state = canon_state(facility_state(df))          # re-key on the names the fold left standing
    own_by_pud, group = depot_buildings(), model_names()
    node = {n: model_node(n) for n in group}      # resolved once, not once per touch
    assert all(node.values()), "a building in the model group has no model node"

    rows = []
    for chain, pud in zip(raw, identity.pdc.reindex(index)):
        vic, before, after, merged, _van, fell, why, _rev = path_chain(
            chain, own_by_pud.get(pud, frozenset()), state, group)
        sites, interior = cap_path([node[x] for x in vic])
        rows.append((sites, len(vic), interior, before, after, merged, fell, ZERO_REASON[why]))
    p = pd.DataFrame(rows, index=index, columns=[
        "path_sites", "path_n", "path_interior", "path_before", "path_after", "path_merged",
        "path_fell", "path_why"])

    tot = w.sum()
    print(f"  path 0d: {PATH_TOUCH_BAR.upper()} bar — "
          + ", ".join(e.replace("ZPT_", "") for e in EVIDENCE[PATH_TOUCH_BAR]))
    print(f"    {len(folds)} buildings answer to more than one scan name; "
          f"{w[p.path_merged > 0].sum():,} {unit} were scanned under two names of one building")
    _n = p.path_n.clip(upper=PATH_DEPTH + 1)
    print("    buildings of ours touched before the depot — " + "  ".join(
        f"{k}{'+' if k == PATH_DEPTH + 1 else ''} {w[_n == k].sum() / tot:5.1%}"
        for k in range(PATH_DEPTH + 2))
        + f"  (mean {(p.path_n * w).sum() / tot:.2f})")
    _z = p.path_why != "path"
    if _z.any():
        print(f"    {w[_z].sum():,} {unit} ({w[_z].sum() / tot:.1%}) have no path at all: "
              + ", ".join(f"{r} {w[p.path_why == r].sum():,}"
                          for r in p.path_why[_z].value_counts().index))
    print(f"    cut from the journey: {w[p.path_before > 0].sum():,} {unit} arrived through "
          f"another state, {w[p.path_after > 0].sum():,} were seen somewhere after their depot, "
          f"{w[p.path_fell > 0].sum():,} passed a building the model does not carry")
    if (p.path_interior > 0).any():
        print(f"    PATH_DEPTH={PATH_DEPTH} ({PATH_CAP_RULE}) folds "
              f"{(p.path_interior * w).sum():,} interior touches out of "
              f"{w[p.path_interior > 0].sum():,} {unit}")
    return p


def source(df, fc, index, articles=None):
    """STEP 1 — SOURCE. Where was this parcel lodged?  VIC / INTERSTATE / UNKNOWN.

    Full note: docs/export_chain2_factors.md#source
    """
    lg = (df[df.Event_type == "ZPT_LODGE"].groupby("Consignment_ID").first()
            [["Event_facility_name", "STE_NAME21"]]
            .set_axis(["lodge_fac", "lodge_state"], axis=1))
    nl = fc[fc.Event_type.isin(ORIGIN_FALLBACK_EVENTS)]      # LODGE is absent by construction
    fallback = nl.groupby("Consignment_ID").STE_NAME21.first().rename("first_state")
    # WHICH event handed rule 2 its answer. `.first()` skips nulls, so the row that supplied the
    # state is the first CANDIDATE presence scan that carries one — not simply the first scan.
    supplier = (nl[nl.STE_NAME21.notna()].groupby("Consignment_ID")
                  .Event_type.first().rename("first_state_from"))

    s = pd.DataFrame(index=index).join(lg).join(fallback)
    state = s.lodge_state.fillna(s.first_state)                  # rule 1, then rule 2
    s["origin"] = np.where(state == "Victoria", "VIC",
                           np.where(state.isna(), "UNKNOWN", "INTERSTATE"))
    s["origin_from"] = np.where(s.lodge_state.notna(), "lodge",
                                np.where(s.first_state.notna(), "first_scan", "none"))
    s["lodge_pud"] = s.lodge_fac.map(LODGE_PUD)                  # which depot's catchment

    # ── what rule 2 had to work with, and what neither rule could answer ──────────────
    w = pd.Series(1, index=index) if articles is None else articles.reindex(index)
    unit = "consignments" if articles is None else "articles"
    gap = s.index[s.lodge_state.isna()]                          # rule 1 had nothing to say
    print(f"  origin: rule 1 (lodge scan) answers {w[s.lodge_state.notna()].sum():,} {unit} "
          f"({w[s.lodge_state.notna()].sum() / w.sum():.2%}); {len(gap):,} consignments fall to "
          f"rule 2 and it reads the state off:")
    by_ev = (w.reindex(gap).groupby(supplier.reindex(gap)).sum()
              .sort_values(ascending=False))
    for ev, v in by_ev.items():
        print(f"      {ev.replace('ZPT_', ''):18s} {v:8,} {unit}  {v / w[gap].sum():6.2%} "
              f"of the gap")
    dead = s.index[s.origin_from == "none"]
    print(f"    BOTH RULES FAIL on {len(dead):,} consignments ({w.reindex(dead).sum():,} {unit}, "
          f"{w.reindex(dead).sum() / w.sum():.4%}) — no lodge scan, and no state on any of "
          + "/".join(e.replace("ZPT_", "") for e in ORIGIN_FALLBACK_EVENTS)
          + ". Tagged origin=UNKNOWN; export_chain2_factors.cohort() drops them.")

    # ── METRO or REGIONAL — asked only of Victorian freight, and only after the state ──
    # Order matters and is the whole rule: interstate is decided above, off the STATE, exactly as
    # it always was. The boundary is never asked about an interstate parcel, so a Sydney lodgement
    # that happens to sit outside a Melbourne polygon cannot arrive in the region band.
    s = s.join(lodgement_geography(s.lodge_fac))
    vic = s.origin == "VIC"
    placed = s.lodge_metro.notna()
    s["lodge_band"] = np.where(
        ~vic, np.where(s.origin == "UNKNOWN", "UNKNOWN", "INT"),
        np.where(placed & s.lodge_metro.fillna(False).astype(bool), "METRO",
                 np.where(placed, "REGION", VIC_UNPLACED_BAND)))
    print_lodge_geography(s, w, unit)
    return s


def sortation(df, index, articles=None):
    """STEP 2 — SORT. What did Melbourne do to this parcel, and where?

    Full note: docs/export_chain2_factors.md#sortation
    """
    # 2a — the sort chain
    ms = df[(df.Event_type == "ZPT_MACHINE_SORT")
            & (df.Event_facility_name.isin(SORT_SITE))].copy()
    ms["code"] = ms.Event_facility_name.map(SORT_SITE)
    ms = ms[ms.code != ms.groupby("Consignment_ID").code.shift()]
    chain = ms.groupby("Consignment_ID").code.apply(list)
    elsewhere = set(df.loc[(df.Event_type == "ZPT_MACHINE_SORT")
                           & ~df.Event_facility_name.isin(SORT_SITE), "Consignment_ID"])

    # 2b — received at, and sorted at, with the sequence numbers the comparison needs
    handled = df[df.Event_type.isin(HANDLED)]
    site_ev = handled[handled.Event_facility_name.isin(SORT_SITE)].copy()
    site_ev["code"] = site_ev.Event_facility_name.map(SORT_SITE)
    recv = (site_ev.sort_values("Event_seq").groupby("Consignment_ID")
                   .agg(recv_site=("code", "first"), recv_seq=("Event_seq", "first")))
    srt1 = (site_ev[site_ev.Event_type == "ZPT_MACHINE_SORT"].sort_values("Event_seq")
                   .groupby("Consignment_ID")
                   .agg(sort_site=("code", "first"), sort_site_seq=("Event_seq", "first")))

    # 2c — first hub touch that involved physical handling, and first Victorian sort
    fh = (handled[handled.Event_facility_name.isin(HUB_FACILITIES)].sort_values("Event_seq")
            .groupby("Consignment_ID").Event_seq.first().rename("hub_seq"))
    fs = (df[(df.Event_type == "ZPT_MACHINE_SORT") & (df.STE_NAME21 == "Victoria")]
            .sort_values("Event_seq").groupby("Consignment_ID")
            .Event_seq.first().rename("sort_seq"))

    s = (pd.DataFrame(index=index).join(pd.DataFrame({"chain": chain}))
           .join(recv).join(srt1).join(fh).join(fs))

    w = pd.Series(1, index=index) if articles is None else articles.reindex(index)
    unit = "consignments" if articles is None else "articles"
    tot = w.sum()

    def vol(mask):
        return w[mask].sum()

    # ── 2a — the sort chain ───────────────────────────────────────────────────────────
    s["chain"] = s.chain.apply(lambda x: x if isinstance(x, list) else [])
    s["nrounds"] = s.chain.str.len()
    s["first"] = s.chain.apply(lambda x: x[0] if x else None)
    # The SECOND sort site, so the diagram can draw where a repeat sort actually went. A parcel
    # sorted once repeats its own site here, which draws flat — the same convention the entry and
    # exit columns already use, so "no second sort" reads as a horizontal ribbon.
    s["second"] = s.chain.apply(lambda x: x[1] if len(x) > 1 else (x[0] if x else None))
    s["last"] = s.chain.apply(lambda x: x[-1] if x else None)
    s["path"] = s.chain.apply(lambda x: " > ".join(x) if x else "(no Melbourne sort)")
    s["sorted_elsewhere"] = s.index.isin(elsewhere)

    _n = s.nrounds.clip(upper=3)
    print("  sort 2a: Melbourne sorts per parcel — " + "  ".join(
        f"{k}{'+' if k == 3 else ''} sorts {vol(_n == k) / tot:5.1%}" for k in range(4)))
    _none = s.nrounds == 0
    # sites, not names: SORT_SITE holds several aliases per building (Holloway Dr is Bayswater)
    print(f"    of the {vol(_none):,} {unit} with no sort at our "
          f"{len(set(SORT_SITE.values()))} sites, "
          f"{vol(_none & s.sorted_elsewhere):,} were machine-sorted SOMEWHERE ELSE "
          f"(mostly interstate); the rest carry no machine sort anywhere")
    _sorted = s.nrounds > 0
    print("    first sort site: " + ", ".join(
        f"{k} {v / vol(_sorted):.1%}" for k, v in
        w[_sorted].groupby(s["first"][_sorted]).sum().sort_values(ascending=False).items()))

    # ── 2b — received at, and therefore the cross-dock ────────────────────────────────
    earlier = s.recv_seq.isna() | s.sort_site_seq.notna() & (s.recv_seq >= s.sort_site_seq)
    s["received"] = np.where(earlier, s.sort_site, s.recv_site)
    s["received"] = s.received.fillna(s.recv_site)
    s["crossdocked"] = s.received.notna() & s.sort_site.notna() & (s.received != s.sort_site)

    _folded = earlier & s.recv_site.notna() & s.sort_site.notna() & (s.recv_site != s.sort_site)
    print(f"  sort 2b: cross-dock {vol(s.crossdocked):,} {unit} "
          f"({vol(s.crossdocked) / tot:.1%}) — handled at one site, sorted at another")
    print("    top lanes: " + ", ".join(
        f"{a}->{b} {v:,}" for (a, b), v in
        w[s.crossdocked].groupby([s.received[s.crossdocked], s.sort_site[s.crossdocked]])
         .sum().sort_values(ascending=False).head(4).items()))
    print(f"    receipt-before-sort guard folded {vol(_folded):,} {unit} onto the diagonal"
          + ("  (it never fires on this extract: `recv` is the FIRST handled scan at any sort "
             "site, so it cannot post-date the sort — the guard is defensive, not load-bearing)"
             if vol(_folded) == 0 else "  — touched by another building, but not before the sort"))

    # ── 2c — was a hub involved before any sort ───────────────────────────────────────
    s["hub_first"] = s.hub_seq.notna() & s.sort_seq.notna() & (s.hub_seq < s.sort_seq)
    print(f"  sort 2c: a hub {list(MODEL_HUBS)} handled it before any sort for "
          f"{vol(s.hub_first):,} {unit} ({vol(s.hub_first) / tot:.1%})")
    return s


def delivery(df, identity, src, index):
    """STEP 3 — DELIVERY. Which depot delivered it, and had it slept there first?

    Full note: docs/export_chain2_factors.md#delivery
    """
    d = pd.DataFrame(index=index)
    d["same_depot_end_to_end"] = ((src.origin == "VIC") & src.lodge_pud.notna()
                                  & (src.lodge_pud == identity.pdc))
    (d["kept_on_site"], d["sorted_before_delivery"], d["delivery_date"], d["hours_at_depot"],
     d["delivered_on_peak_day"]) = compute_kept_on_site(df, d, identity.articles, identity.pdc)

    # the stage is a source band for METRO only; INT and REGION carry their own kept volume
    d["source_band"] = np.where(
        src.lodge_band == "METRO",
        np.where(d.kept_on_site, "KEPT_METRO", "METRO"), src.lodge_band)
    w, tot = identity.articles, identity.articles.sum()
    print("  source bands (articles, every delivery date in the file):")
    for b in ("INT", "METRO", "KEPT_METRO", "REGION", "UNKNOWN"):
        m = d.source_band == b
        if m.any():
            print(f"    {b:<11} {w[m].sum():>9,}  {w[m].sum() / tot:>6.1%}")
    k = d.kept_on_site
    if w[k].sum():
        # the whole stage, not the band — the two are no longer the same pile, and the gap is
        # exactly the interstate and regional stock that now bands with its lodgement instead
        print(f"    kept at depot in total {w[k].sum():,} ({w[k].sum() / tot:.1%}), of which "
              + ", ".join(f"{b} {w[k & (src.lodge_band == b)].sum() / w[k].sum():.1%}"
                          for b in ("INT", "METRO", "REGION"))
              + f" — only the METRO slice is drawn as a source band")
        print_kept_duration(d, identity.articles, src.lodge_band)
    return d


def build_paths():
    """Reduce 2.96 M scan events to one row per consignment. Takes about a minute.

    The assembly. Every column comes from exactly one step above, and the steps run in the order
    a parcel experiences them: it is lodged, Melbourne sorts it, a depot delivers it.
    """
    df = read_scans()

    identity = consignment_identity(df)          # step 0b — and `pdc`, the delivering depot
    PDCJSON.parent.mkdir(parents=True, exist_ok=True)
    PDCJSON.write_text(json.dumps(report_pdc_basis(identity), indent=1))
    # A parcel whose delivering depot cannot be named is out of the reduction entirely, not
    # carried with a blank: every factor below groups by `pdc`, and a NaN group would quietly
    # leave the shares while still counting in the totals. Dropped here, once, and reported above.
    identity = identity[identity.pdc.notna()]
    index = identity.index                       # every consignment we can place
    df = df[df.Consignment_ID.isin(index)]
    fc = presence_scans(df)                      # step 0c

    itin = itinerary(df, identity, index, identity.articles)      # step 0d
    src = source(df, fc, index, identity.articles)               # step 1
    srt = sortation(df, index, identity.articles)                # step 2
    dlv = delivery(df, identity, src, index)     # step 3 — the one cross-step dependency

    p = pd.concat([identity, journey(fc, index), itin, src, srt, dlv], axis=1)

    COVJSON.write_text(json.dumps(event_coverage(df, identity, PHYSICAL), indent=1))
    return p.reset_index().rename(columns={"index": "Consignment_ID"})[PATH_COLS]


def event_coverage(df, attr, physical):
    """How completely does each scan type cover the freight? The question behind the table.

    A scan type is only usable as a stage marker if nearly every consignment has one. This
    measures that directly, per event type, and separately measures whether the event actually
    sits where its name implies — a LODGE that is not the first scan, or a DELIVER that is not
    the last, is a warning that the chain cannot simply be read off the sequence.
    """
    art = attr.articles
    na, nc = int(art.sum()), len(art)
    rows = []
    for ev in physical:
        d = df[df.Event_type == ev]
        cons = d.Consignment_ID.unique()
        a = int(art.reindex(cons).sum())
        rows.append(dict(event=ev.replace("ZPT_", ""), scans=int(len(d)), cons=int(len(cons)),
                         art=a, art_pct=round(100 * a / na, 2),
                         per_cons=round(len(d) / max(len(cons), 1), 2),
                         multi_pct=round(100 * (d.groupby("Consignment_ID").size() > 1).mean(), 1),
                         fac_pct=round(100 * d.Event_facility_name.notna().mean(), 1)))

    # position: does the event sit where its name implies, judged against the physical chain only
    ph = df[df.Event_type.isin(physical)]
    first = ph.groupby("Consignment_ID").Event_type.first()
    last = ph.groupby("Consignment_ID").Event_type.last()
    share = lambda idx: round(100 * float(art.reindex(idx).sum()) / na, 2)
    opens = (first.groupby(first).apply(lambda s: share(s.index))
                  .sort_values(ascending=False).head(5))
    closes = (last.groupby(last).apply(lambda s: share(s.index))
                  .sort_values(ascending=False).head(5))
    return dict(
        articles=na, consignments=nc, scans=int(len(df)),
        types=int(df.Event_type.nunique()), physical_scans=int(len(ph)),
        rows=rows,
        lodge_first=share(first[first == "ZPT_LODGE"].index),
        deliver_last=share(last[last == "ZPT_DELIVER"].index),
        opens=[[k.replace("ZPT_", ""), v] for k, v in opens.items()],
        closes=[[k.replace("ZPT_", ""), v] for k, v in closes.items()],
    )


def read_pdc_basis():
    """What the delivering-depot basis cost, written by the last rebuild. {} if never rebuilt."""
    return json.loads(PDCJSON.read_text()) if PDCJSON.exists() else {}


# The events that mean "this depot took the parcel in". The third…  → docs/export_chain2_factors.md#the-events-that-mean-this-depot
ARRIVAL_EVENTS = ["ZPT_ACCEPT_FACILITY", "ZPT_UNLOAD_ITEMS", "ZPT_TRANSFER",
                  "ZPT_DEPART_CONTAINER", "ZPT_LOAD_ITEM"]

# AND THE DEPOT MUST HAVE WORKED IT BEFORE THE DELIVERY DATE  → docs/export_chain2_factors.md#and-the-depot-must-have-worked
DEPOT_SORT_EVENTS = ["ZPT_MACHINE_SORT"]
KEPT_REQUIRE_SORT = True                     # Change 34; False restores the arrival-only rule
KEPT_SORT_RULE = "last"                      # last | any — see above, worth 2,240 EA


def compute_kept_on_site(df, p, articles=None, pdc=None):
    """Ops' definition: of the parcels delivered on a day, which SAT at the delivering depot?

    Full note: docs/export_chain2_factors.md#compute-kept-on-site
    """
    ev_pud = df.Event_facility_name.map(LODGE_PUD)
    # the delivering depot on the ACTIVE basis (Change 34), not the planned destination — plus
    # its contractor's base where it has one, which is the same operation at another address
    own_pud = df.Consignment_ID.map(pdc)
    base_pud = df.Consignment_ID.map(pdc.map(CONTRACTOR_BASE))
    own = ev_pud.notna() & ((ev_pud == own_pud)          # a scan at ITS OWN delivering depot
                            | (ev_pud == base_pud))

    qualifying = df[own & df.Event_type.isin(ARRIVAL_EVENTS + ["ZPT_MACHINE_SORT"])]
    dl = df[df.Event_type == "ZPT_DELIVER"]
    delivered = dl.groupby("Consignment_ID").dt.max().reindex(p.index)
    deliver_seq = dl.groupby("Consignment_ID").Event_seq.max()

    # The last time it was anywhere but its own depot, before it went out for delivery.
    # PHYSICAL only, and that restriction is load-bearing: an ADMIN-ER40 or LINK_VIRTUALCONT is
    # attributed to a facility that never held the freight, and letting one count as "the parcel
    # left the building" breaks a genuine overnight stay on paperwork. It costs 12,566 EA — a
    # third of the measure — and it is the same mistake as counting an acceptance scan as
    # presence. Using HANDLED instead of PHYSICAL would give 23.7% rather than 22.1%.
    away = df[df.Event_facility_name.notna() & ~own & df.Event_type.isin(PHYSICAL)]
    last_away = (away.assign(_ds=away.Consignment_ID.map(deliver_seq))
                     .query("Event_seq < _ds").groupby("Consignment_ID").Event_seq.max())
    q = qualifying.assign(_la=qualifying.Consignment_ID.map(last_away).fillna(-1),
                          _ds=qualifying.Consignment_ID.map(deliver_seq))
    stay = q[(q.Event_seq > q._la) & (q.Event_seq <= q._ds)]     # the final, unbroken stay
    arrived = stay.groupby("Consignment_ID").dt.min().reindex(p.index)

    # ── the second condition: the depot SORTED it before the delivery date ────────────
    assert KEPT_SORT_RULE in ("last", "any"), f"KEPT_SORT_RULE={KEPT_SORT_RULE!r}"
    ms = stay[stay.Event_type.isin(DEPOT_SORT_EVENTS)]
    worked = ms.groupby("Consignment_ID").dt
    sorted_at = (worked.max() if KEPT_SORT_RULE == "last" else worked.min()).reindex(p.index)
    sorted_before = (sorted_at.dt.date < delivered.dt.date).fillna(False)

    # The condition only binds at a depot that has a sorter to be asked about; elsewhere the
    # arrival test stands alone. The dictionary decides, and the scans have to agree with it.
    asked = pdc.reindex(p.index).isin(SORT_CAPABLE_PUDS)
    seen = set(pdc.reindex(ms.Consignment_ID.unique()).dropna())
    assert seen <= set(SORT_CAPABLE_PUDS), (
        "a depot outside SORT_CAPABLE_PUDS ran a machine sort on its own freight — the facility "
        f"dictionary is behind the scans: {sorted(seen - set(SORT_CAPABLE_PUDS))}")

    stood = (arrived.dt.date < delivered.dt.date).fillna(False)
    kept = (stood & (sorted_before | ~asked)) if KEPT_REQUIRE_SORT else stood
    hours = ((delivered - arrived).dt.total_seconds() / 3600).round(2)
    peak = delivered.dt.date.mode()
    peak = peak.iloc[0] if len(peak) else None
    on_peak = delivered.dt.date == peak

    # the superseded rule, kept so the older 25.6% stays traceable rather than just disappearing
    was = ((qualifying.groupby("Consignment_ID").dt.min().reindex(p.index).dt.date
            < delivered.dt.date).fillna(False))
    w = pd.Series(1, index=p.index) if articles is None else articles.reindex(p.index)
    unit = "consignments" if articles is None else "articles"
    share = lambda m, sub=None: w[m & (True if sub is None else sub)].sum() / w[
        slice(None) if sub is None else sub].sum()
    print(f"  kept on site: {w[kept].sum():,} {unit} were STANDING at the delivering depot "
          f"before the delivery date (final-stay rule"
          + (", and sorted there before it at the depots that have a sorter)"
             if KEPT_REQUIRE_SORT else ")"))
    _lag = (delivered.dt.date - sorted_at.dt.date).map(
        lambda x: x.days if pd.notna(x) else np.nan)
    print(f"    the arrival test alone passes {w[stood].sum():,} {unit} ({share(stood):.1%}); "
          f"the sort condition is asked of {', '.join(sorted(depot_label(x) for x in SORT_CAPABLE_PUDS))} "
          f"and removes {w[stood & asked & ~sorted_before].sum():,} of it — "
          f"{w[stood & asked & sorted_at.notna() & ~sorted_before].sum():,} sorted at the depot "
          f"only on the delivery day itself, {w[stood & asked & sorted_at.isna()].sum():,} carry "
          f"no own-depot machine sort inside the final stay at all")
    print(f"    the other {len(MODEL_PDCS) - len(SORT_CAPABLE_PUDS)} depots have no sorter, so "
          f"the condition is not asked of them and their {w[stood & ~asked].sum():,} {unit} "
          f"stand on the arrival test alone")
    print("    days from the depot's own sort to the delivery, where there is a sorter: "
          + ", ".join(f"{int(d)}d {w[stood & asked & (_lag == d)].sum():,}"
                      for d in sorted(_lag[stood & asked].dropna().unique())[:5])
          + f", no sort {w[stood & asked & _lag.isna()].sum():,}")
    print(f"    on the extract's delivery day ({peak}): {share(kept, on_peak):.1%}"
          f"   |   pooled over all delivery dates: {share(kept):.1%}")
    print(f"    the superseded 'first touch at the depot' rule gave {share(was, on_peak):.1%} / "
          f"{share(was):.1%}; the difference is {w[was & ~kept].sum():,} {unit} that touched the "
          f"depot, left for a hub or interstate, and came back on delivery day")
    return kept, sorted_before, delivered.dt.date, hours, on_peak


# The bins the duration table below is cut into. Deliberately fine around one night — 12-24h is
# where two thirds of the stage sits and is the only band an ops planner can act on — and coarse
# after two days, where the volume is a redelivery tail rather than a staging decision.
KEPT_BINS = [(0, 6), (6, 12), (12, 18), (18, 24), (24, 36), (36, 48), (48, 72), (72, None)]


def print_kept_duration(d, articles, lodge_band):
    """How long the stage actually stood there, split by where it was lodged.

    Full note: docs/export_chain2_factors.md#print-kept-duration
    """
    k = d[d.kept_on_site]
    w = articles.reindex(k.index)
    h = k.hours_at_depot
    lb = lodge_band.reindex(k.index)
    cols = [("interstate", lb == "INT"), ("metro", lb == "METRO"), ("region", lb == "REGION"),
            ("all", pd.Series(True, index=k.index))]

    def cell(m, col):
        """Volume in this bin, and its share OF THAT COLUMN — so the columns each read to 100%."""
        v = w[m].sum()
        return f"{v:>7,} {v / max(w[col].sum(), 1):>6.1%}" if v else f"{'—':>7}{'':>7}"

    print("  kept at depot — hours from the last arrival scan to the delivery scan:")
    print(f"    {'hours':<12}" + "".join(f"{lab:^16}" for lab, _ in cols))
    for lo, hi in KEPT_BINS:
        band = (h >= lo) & (h < hi) if hi else (h >= lo)
        if not any((band & c).any() for _, c in cols):
            continue
        label = f"{lo} – {hi}" if hi else f"{lo}+"
        print(f"    {label:<12}" + "".join(f"  {cell(band & c, c)}" for _, c in cols))
    print(f"    {'median':<12}"
          + "".join(f"  {h[c].median():>7.1f}h{'':>6}" for _, c in cols))
    print(f"    {'under 24h':<12}" + "".join(f"  {cell((h < 24) & c, c)}" for _, c in cols))


STATE_ABBR = {"Victoria": "VIC", "New South Wales": "NSW", "Queensland": "QLD",
              "South Australia": "SA", "Western Australia": "WA", "Tasmania": "TAS",
              "Australian Capital Territory": "ACT", "Northern Territory": "NT",
              "unknown": "?"}


def print_state_summary(p):
    """The day, by the STATE it was lodged in — one row per state, then the two model bands.

    Full note: docs/export_chain2_factors.md#print-state-summary
    """
    q = p.assign(state=p.lodge_state.fillna(p.first_state).fillna("unknown"))
    w, tot = q.articles, q.articles.sum()
    kept_tot = max(q.articles[q.kept_on_site].sum(), 1)

    def row(label, d):
        k, s = d[d.kept_on_site], d[d.nrounds > 0]
        a = max(d.articles.sum(), 1)
        med = k.hours_at_depot.median()
        top = s.groupby("first").articles.sum().idxmax() if len(s) else "—"
        return (f"  {label:<7}{d.articles.sum():>10,}{d.articles.sum() / tot:>8.1%}"
                f"{k.articles.sum():>10,}{k.articles.sum() / a:>8.1%}"
                f"{k.articles.sum() / kept_tot:>9.1%}"
                f"{(med if pd.notna(med) else 0):>7.1f}"
                f"{(d.nrounds * d.articles).sum() / a:>9.2f}"
                f"{s[s['first'].isin(MODEL_HUBS)].articles.sum() / max(s.articles.sum(), 1):>7.0%}"
                f"{d[d.crossdocked].articles.sum() / a:>8.1%}   {top}")

    print("  the day by LODGEMENT STATE  (kept % is of that state; % stage is of the whole stage)")
    print(f"  {'lodged':<7}{'articles':>10}{'% day':>8}{'kept EA':>10}{'kept %':>8}{'% stage':>9}"
          f"{'med h':>7}{'sorts':>9}{'hub%':>7}{'xdock%':>8}   first sort")
    for st in sorted(set(q.state), key=lambda s: -w[q.state == s].sum()):
        print(row(STATE_ABBR.get(st, st), q[q.state == st]))
    print("  " + "─" * 84)
    # the two bands the model actually carries, then the whole file — VIC + REST is 167,443,
    # two short of ALL, and those two are the `?` row above
    for lab, m in (("VIC", q.origin == "VIC"), ("REST", q.origin == "INTERSTATE"),
                   ("ALL", pd.Series(True, index=q.index))):
        print(row(lab, q[m]))


def load_paths(rebuild=False):
    if CACHE.exists() and COVJSON.exists() and not rebuild:
        p = pd.read_pickle(CACHE)
        # the notebook writes this cache too; rebuild if it is an older, narrower version
        if {"hub_first", "deliver_fac", "path", "received", "crossdocked",
             "full_chain", "full_path", "n_facilities", "same_depot_end_to_end",
             "kept_on_site", "hours_at_depot", "delivery_date", "delivered_on_peak_day",
             "second", "first_state", "origin_from", "sorted_elsewhere",
             "source_band", "pdc_term", "pdc_deliver", "sorted_before_delivery",
             "lodge_band", "lodge_metro", "lodge_geo_from",
             "pdc_accept", "pdc_from",
             "path_sites", "path_n", "path_interior", "path_why"} <= set(p.columns):
            return p
        print("  cache is missing columns this script needs — rebuilding")
    OUT.mkdir(parents=True, exist_ok=True)
    p = build_paths()
    p.to_csv(OUT_PATH, index=False)
    p.to_pickle(CACHE)
    return p


# ══════════════════════════════════════════════════════════════════════════════════════
#  PART 2 — THE FACTORS.  Per-parcel rows -> the CSVs the model reads.
# ══════════════════════════════════════════════════════════════════════════════════════

# Change 30 (2026-08-10): all EIGHT buildings the scans show running a first machine sort. AVL and
# DLC are small — 3,290 EA between them, 2.0% of the day — but they are real sort sites, and while
# they were absent every parcel they sorted was handed to MPF or TPF by the entry() fallback
# below. Avalon is 50 km from either. Both need OBS_MIN_CLASS_SITE at 1,000: their largest
# flavours are 2,078 (VIC/PP/AVL) and 1,023 EA (VIC/PP/DLC), and nothing else sits in the
# 1,000-2,000 band, so the lower threshold admits exactly those two and changes nothing else.
# CHANGE 41 — DROP THE VOLUME NO SCAN CAN PLACE AT A SORT SITE  → docs/export_chain2_factors.md#change-41-drop-the-volume-no
# CHANGE 39 : REVERSED. The assumed rung is now KEPT  → docs/export_chain2_factors.md#change-39-reversed-the-assumed-rung
DROP_UNPLACED_ENTRY = False
ARR_CODES = ["MPF", "TPF", "MGF", "SWP", "MNP", "BAY", "AVL", "DLC"]
# ── WHICH NODES A LANE MAY TOUCH ─────────────────────────────────────────────────────
# The role basis only ever named a SORT SITE, because a role is what it measured. The path basis
# also names the eight depots that do not sort: 5,236 EA (3.2%) are first seen at one of them and
# the model has a PUD node for every one, so a lane may start or end there. Folding those onto a
# hub to keep the old node set would invent an entry site for freight the scans place exactly —
# the error DROP_UNPLACED_ENTRY exists to argue about, made silently.
def lane_nodes():
    return set(ARR_CODES) | (set(TERM_PDC.values()) if PATH_BASIS == "facility_path" else set())

DEPOT_CODE = {"PUD_Sunshine_West": "SWP", "PUD_Melbourne_North": "MNP", "PUD_Bayswater": "BAY"}
BIG_HUB = {"PP": "MPF", "EP": "TPF"}                     # entry-site fallback of last resort


def read_dials():
    d = pd.read_csv(FASS / "dials.csv").set_index("parameter")
    def g(name, cast):
        return cast(d.loc[name, "value"])
    return dict(fold=g("FOLD_MIN_ARTICLES", int),
                r2_min=g("ROUND2_MIN_SHARE", float),
                cohort=g("OBS_COHORT", str))


def cohort(p, basis=None):
    """The measurement cohort, with every Sankey stage column mapped onto the model's six sites.

    Full note: docs/export_chain2_factors.md#cohort
    """
    basis = basis or read_dials()["cohort"]
    assert basis in ("all_dates", "peak_day"), f"OBS_COHORT={basis!r}"
    m = p.origin != "UNKNOWN"
    if basis == "peak_day":
        m &= p.delivered_on_peak_day
    q = p[m].copy()

    def entry(r):
        """The entry site, and HOW it was decided — see DROP_UNPLACED_ENTRY."""
        if r["first"] in ARR_CODES:
            return r["first"], "sorted"
        if pd.notna(r["received"]) and r["received"] in ARR_CODES:
            return r["received"], "handled"
        return DEPOT_CODE.get(r["pdc"], BIG_HUB[r["cls"]]), "assumed"

    if PATH_BASIS == "facility_path":
        # THE PATH IS THE ANSWER, and the fallback ladder shrinks to its last rung. `first` and
        # `received` asked which building SORTED it and which HANDLED it; the path asks which
        # building it was IN, and a parcel with a path needs no rule to place it. What is left is
        # the population with no path at all — step 0d's four empty reasons — which keeps the
        # assumed rung and, with it, Change 39's manual-sort measurement. The reason is carried
        # through as `entry_from` rather than flattened to "assumed", so the residual can be read
        # apart without a rebuild.
        _first = [s[0] if s else None for s in q.path_sites]
        q["site"] = [s if s is not None else DEPOT_CODE.get(pud, BIG_HUB[cls])
                     for s, pud, cls in zip(_first, q.pdc, q.cls)]
        q["entry_from"] = np.where(q.path_n > 0, "path", "assumed")
        q["path_from"] = q.path_why
    else:
        _e = [entry(r) for _, r in q[["first", "received", "pdc", "cls"]].iterrows()]
        q["site"] = [x[0] for x in _e]
        q["entry_from"] = [x[1] for x in _e]
        q["path_from"] = q.entry_from
    if DROP_UNPLACED_ENTRY:
        _a = q.entry_from == "assumed"
        # cohort() is called by derive() and derive_stages(); say this once.
        # The MESSAGE is what is deduplicated — never the drop. Guarding the drop with the same
        # condition silently gave obs_joint a 160,902 cohort and obs_demand a 165,567 one, which
        # balances inside each table and is wrong between them.
        if _a.any() and not getattr(cohort, "_said", False):
            cohort._said = True
            print(f"  cohort: dropping {q.articles[_a].sum():,} articles "
                  f"({q.articles[_a].sum() / q.articles.sum():.2%}) whose entry site is ASSUMED "
                  f"— no sort and no handling scan at any modelled site "
                  f"(DROP_UNPLACED_ENTRY=True); the {q.articles[q.entry_from == 'handled'].sum():,} "
                  f"placed by a handling scan are KEPT")
        q = q[~_a].copy()
    # Change 37: the family IS the source band. Four of them, and the one that is not a
    # lodgement place — METRO_DEPOT, yesterday's metro stock — is the only one that bypasses the
    # sort lanes. Interstate and regional freight that also slept at the depot is NOT a stage
    # supplier any more: it travelled the network and was sorted on the way, so it rides its own
    # family's lanes and only its overnight wait is unmodelled.
    q["fam"] = np.where(q.kept_on_site & (q.lodge_band == "METRO"), "METRO_DEPOT",
                np.where(q.lodge_band == "INT", "INTERSTATE",
                 np.where(q.lodge_band == "REGION", "REGION", "METRO")))
    if PATH_BASIS == "facility_path":
        # ONE LEG, NOT TWO STAGES. The path is already capped at PATH_DEPTH buildings, so the
        # second of them IS the destination — whether the truck that carried it there was a
        # cross-dock leg or a round-2 leg, which is a distinction the path basis does not make and
        # does not need. `recv_site` is kept, equal to the entry, so the interstate cross-dock
        # matrix degenerates to its diagonal rather than disappearing from code that still asks.
        q["touches"] = q.path_n
        q["recv_site"] = q["site"]
        q["dest2"] = [s[1] if len(s) > 1 else "ONCE" for s in q.path_sites]
        q["exit_site"] = pd.Series([s[-1] if s else None for s in q.path_sites],
                                   index=q.index).fillna(q["site"])
        q["flavour_site"], q["repeat_site"] = q["site"], q.dest2
    else:
        q["touches"] = q.nrounds
        # handled where it was sorted unless a MODELLED other site handled it first
        q["recv_site"] = np.where(q.received.isin(ARR_CODES), q.received, q["site"])
        # a second sort at an unmodelled site (DLC, AVL, interstate) cannot be drawn as a second
        # sort — the model has no node for it, so that volume reads as sorted once at its entry
        q["dest2"] = np.where(q.nrounds.values >= 2,
                              np.where(q["second"].isin(ARR_CODES), q["second"], "ONCE"), "ONCE")
        q["exit_site"] = np.where(q["last"].isin(ARR_CODES), q["last"], q["site"])
        q["flavour_site"], q["repeat_site"] = q["first"], q["second"]
    return q


def derive(p):
    """The RAW factor derivation — shared with the notebook's drift guard."""
    q = cohort(p)
    # METRO_DEPOT carries no site: like STG before it, the depot is the row, not the tag.
    q["band"] = np.where(q.fam == "METRO_DEPOT", "METRO_DEPOT", q.fam + "_" + q["site"])

    joint = []
    for (pud, cls), d in q.groupby(["pdc", "cls"]):
        tot = d.articles.sum()
        for t, v in d.groupby("band").articles.sum().items():
            joint.append((pud, cls, t, v / tot, int(v)))

    # `touches` counts SORTS on the role basis and BUILDINGS on the path basis, and
    # `flavour_site` / `repeat_site` are the entry and the repeat under whichever basis is
    # running — so everything below is written once and means the same thing either way.
    nodes = lane_nodes()
    s = q[(q.touches > 0) & (q.fam != "METRO_DEPOT")]
    single = []
    for (fam, cls, site), d in s.groupby(["fam", "cls", "flavour_site"]):
        if site in nodes and d.articles.sum() >= 50:
            single.append((fam, cls, site,
                           round(d[d.touches == 1].articles.sum() / d.articles.sum(), 4),
                           int(d.articles.sum())))

    # `second` is not exported any more, but it is still DERIVED: `round2_sites` reads it to
    # decide which sites may take a second sort, and that answer IS exported.
    rep = q[q.touches >= 2]
    second = [(site, round(v / rep.articles.sum(), 4), int(v))
              for site, v in rep.groupby("repeat_site").articles.sum().items()]

    meta = dict(cohort=read_dials()["cohort"],
                peak_day=str(q.delivery_date.mode().iloc[0]),
                cohort_articles=int(q.articles.sum()),
                cohort_consignments=int(len(q)),
                # the volume whose entry site is a RULE rather than a measurement — the parcels
                # `entry()` had to place on the role basis, and step 0d's four empty populations
                # on the path basis. Same question, asked of whichever basis is running.
                entry_fallback_articles=int(q.articles[q.entry_from == "assumed"].sum()
                                            if PATH_BASIS == "facility_path" else
                                            q[(q["first"].isna())
                                              | (~q["first"].isin(ARR_CODES))].articles.sum()),
                # THE SECOND-SORT VOLUME THE SCANS MEASURE (Change 48), both cohorts, so the build
                # can charge round-2 handling for the work the network really does. The model
                # routes fewer parcels through a second sort than the scans record — most of the
                # difference is staged freight, sorted the day it ARRIVED, which a one-day model
                # has nowhere to book — and the handling cost of those sorts is real either way.
                # Exported rather than computed downstream because it is a MEASUREMENT: it belongs
                # on the observed side of the folder, and a number the notebook derived for itself
                # would drift the moment a filter dial moved.
                sort_2plus_cohort=int(q.articles[q.nrounds >= 2].sum()),
                sort_2plus_same_day=int(q.articles[(q.nrounds >= 2)
                                                   & (q.fam != STAGE_TAG)].sum()))
    return joint, single, second, meta


def filter_factors(joint, single, fold):
    """Fold everything smaller than FOLD_MIN_ARTICLES, renormalise, and report what moved.

    Full note: docs/export_chain2_factors.md#filter-factors
    """
    fam_tot = {}
    for pud, cls, tag, share, art in joint:
        if tag != STAGE_TAG:
            fam, site = tag_family(tag), tag_site(tag)
            fam_tot[(fam, cls, site)] = fam_tot.get((fam, cls, site), 0) + art
    dead = {k for k, v in fam_tot.items() if v < fold}

    # Change 30, and it survives the dial collapse unchanged in…  → docs/export_chain2_factors.md#change-30-and-it-survives-the
    live, seen = set(), set()
    for pud, cls, tag, share, art in joint:
        if tag == STAGE_TAG:
            continue
        fam, site = tag_family(tag), tag_site(tag)
        k = (fam, cls, site)
        if k in dead:
            continue
        seen.add(k)
        if art >= fold:
            live.add(k)
    exempt = seen - live

    # WHERE A FAMILY GOES WHEN IT LOSES EVERY CELL IN A ROW  → docs/export_chain2_factors.md#where-a-family-goes-when-it
    fallback = {}
    for pud, cls, tag, share, art in joint:
        if tag == STAGE_TAG:
            continue
        fam, site = tag_family(tag), tag_site(tag)
        if (fam, cls, site) in dead:
            continue
        k = (fam, cls)
        if art > fallback.get(k, (None, -1))[1]:
            fallback[k] = (site, art)

    out, moved = [], 0
    rows = {}
    for pud, cls, tag, share, art in joint:
        rows.setdefault((pud, cls), []).append((tag, share, art))
    for (pud, cls), cells in rows.items():
        keep = []
        for tag, share, art in cells:
            if tag == STAGE_TAG:
                keep.append((tag, share, art)); continue
            fam, site = tag_family(tag), tag_site(tag)
            if (fam, cls, site) in dead or (art < fold
                                            and (fam, cls, site) not in exempt):
                moved += art
            else:
                keep.append((tag, share, art))
        # Renormalise WITHIN each origin family. Dropping a thin site is a statement about where
        # freight was sorted, never about where it came from — so interstate volume that loses a
        # site folds onto interstate's other sites, and the depot's FOUR-way split (metro depot /
        # interstate / metro / region) comes out of the filter exactly as it went in. Renormalising
        # across families instead moved 2,000 EA of interstate into Victoria and pulled the
        # model's interstate total 4% below the measurement.
        want = {}
        for tag, share, art in cells:
            want[tag_family(tag)] = want.get(tag_family(tag), 0.0) + share
        have = {}
        for tag, share, art in keep:
            have[tag_family(tag)] = have.get(tag_family(tag), 0.0) + share
        # A family with no surviving site has nowhere of its own to go, so it joins the other
        # SAME-DAY families. METRO_DEPOT never takes any of it: the stage share is the measured
        # overnight holding and is the one number in this table that must not move.
        for fam in [f for f in want if f not in have and f != STAGE_TAG]:
            hit = fallback.get((fam, cls))
            if hit:
                keep.append((f"{fam}_{hit[0]}", want[fam], 0))
                have[fam] = want[fam]
        orphan = sum(v for f, v in want.items() if f not in have)
        sameday = sum(v for f, v in have.items() if f != STAGE_TAG)
        assert sameday > 0, f"{pud}/{cls}: the fold removed every same-day cell — lower it"
        for tag, share, art in keep:
            fam = tag_family(tag)
            take = 0.0 if fam == STAGE_TAG else orphan * have[fam] / sameday
            out.append((pud, cls, tag, round(share / have[fam] * (want[fam] + take), 6), art))

    single_f = [r for r in single if (r[0], r[1], r[2]) not in dead]
    return out, single_f, sorted(dead), moved


def round2_sites(second, r2_min):
    """The sites allowed to take a second sort: observed share >= the dial."""
    return [(site, share, art) for site, share, art in second if share >= r2_min]


def derive_stages(p):
    """The Sankey's link stages as model factors, RAW. Shared with the notebook's drift guard.

    Stage 1 (origin band -> handling site) is not returned separately: it is `demand` crossed
    with obs_joint, which already carries the band split per depot.
    """
    q = cohort(p)
    demand = [(pud, cls, int(v))
              for (pud, cls), v in q.groupby(["pdc", "cls"]).articles.sum().items()]
    # Stages 2-4 route SAME-DAY freight only. Staged freight is already standing at the depot it
    # will be delivered from — it rides no sort lane and no despatch lane in this model, and
    # letting it into these matrices would dilute every routing share with yesterday's parcels.
    s = q[q.fam != "METRO_DEPOT"]
    if PATH_BASIS == "facility_path":
        # STAGES 2 AND 3 ARE ONE TABLE NOW  → docs/export_chain2_factors.md#stages-2-and-3-are-one
        recv_entry, vic_xd = [], 0
    else:
        # Cross-dock is modelled on the interstate family only, which is where most of it sits;
        # the Victorian off-diagonals fold onto the diagonal and are counted in provenance.
        i = s[s.fam == "INTERSTATE"]
        recv_entry = [(cls, r, e, int(v)) for (cls, r, e), v
                      in i.groupby(["cls", "recv_site", "site"]).articles.sum().items()]
        vic_xd = int(s[s.fam.isin(("METRO", "REGION")) & (s.recv_site != s.site)].articles.sum())
    legs = [(fam, cls, e, d, int(v)) for (fam, cls, e, d), v
            in s.groupby(["fam", "cls", "site", "dest2"]).articles.sum().items()]
    deliver_rows = [(cls, x, pud, int(v)) for (cls, x, pud), v
                in s.groupby(["cls", "exit_site", "pdc"]).articles.sum().items()]
    return demand, recv_entry, legs, deliver_rows, vic_xd


# Stage 4 despatch runs are the same kind of movement as stages 2…  → docs/export_chain2_factors.md#stage-4-despatch-runs-are-the
ALL_ORIGINS = None


def lane_ledger(recv_entry, legs, deliver_rows):
    """(family, from, to) -> the articles OF THAT ORIGIN measured moving between two buildings.

    Full note: docs/export_chain2_factors.md#lane-ledger
    """
    lane = {}

    def add(k, a):
        lane[k] = lane.get(k, 0) + a

    for cls, r, e, a in recv_entry:      # cross-dock is measured on the interstate family only
        if r != e:
            add(("INTERSTATE", r, e), a)
    for fam, cls, e, d, a in legs:
        if d not in ("ONCE", e):
            add((fam, e, d), a)
    for cls, x, pud, a in deliver_rows:
        add((ALL_ORIGINS, x, pud), a)
    return lane


def filter_stages(recv_entry, legs, deliver_rows, r2_allowed, dead, fold):
    """Fold every lane under FOLD_MIN_ARTICLES into a larger one, per stage.

    Full note: docs/export_chain2_factors.md#filter-stages
    """
    lane = lane_ledger(recv_entry, legs, deliver_rows)
    moved = {}
    xd = []
    for cls in sorted({c for c, _, _, _ in recv_entry}):
        for r in sorted({x[1] for x in recv_entry if x[0] == cls}):
            cells = [(e, a) for c, rr, e, a in recv_entry if c == cls and rr == r]
            tot = sum(a for _, a in cells)
            # an entry site whose interstate flavour did not survive the class-site minimum has
            # no sort node for this class at all, so it cannot be a cross-dock destination
            live = [(e, a) for e, a in cells if ("INTERSTATE", cls, e) not in dead]
            if not live:
                moved["stage2"] = moved.get("stage2", 0) + tot
                continue
            keep = [(e, a) for e, a in live
                    if e != r and lane[("INTERSTATE", r, e)] >= fold]
            off = sum(a for e, a in cells if e != r)
            moved["stage2"] = moved.get("stage2", 0) + off - sum(a for _, a in keep)
            diag = tot - sum(a for _, a in keep)          # everything else is sorted where it landed
            if ("INTERSTATE", cls, r) not in dead:
                keep = [(r, diag)] + keep
            else:                                          # the diagonal itself is dead: spread it
                w = sum(a for _, a in keep)
                keep = [(e, a + diag * a / w) for e, a in keep]
            for e, a in keep:
                xd.append((cls, r, e, int(round(a)), round(a / tot, 6)))

    r2 = []
    for fam, cls, e in sorted({(f, c, s) for f, c, s, _, _ in legs} - set(dead)):
        cells = [(d, a) for f, c, s, d, a in legs if (f, c, s) == (fam, cls, e)]
        tot = sum(a for _, a in cells)
        # d == e is a second pass on the same site's own machines; the model has one sort node
        # per site and no self-lane, so that volume reads as sorted once there.
        keep = [(d, a) for d, a in cells
                if d not in ("ONCE", e) and d in r2_allowed and lane[(fam, e, d)] >= fold]
        once = sum(a for d, a in cells if d == "ONCE")
        moved["stage3"] = moved.get("stage3", 0) + (tot - once - sum(a for _, a in keep))
        r2.append((fam, cls, e, "ONCE", tot - sum(a for _, a in keep),
                   round(1 - sum(a for _, a in keep) / tot, 6)))
        for d, a in keep:
            r2.append((fam, cls, e, d, a, round(a / tot, 6)))

    dl = []
    for cls, x in sorted({(c, s) for c, s, _, _ in deliver_rows}):
        cells = [(pud, a) for c, s, pud, a in deliver_rows if (c, s) == (cls, x)]
        tot = sum(a for _, a in cells)
        keep = [(pud, a) for pud, a in cells if lane[(ALL_ORIGINS, x, pud)] >= fold]
        if not keep and cells:
            keep = [max(cells, key=lambda kv: kv[1])]
        moved["stage4"] = moved.get("stage4", 0) + tot - sum(a for _, a in keep)
        kept = sum(a for _, a in keep)
        for pud, a in keep:
            dl.append((cls, x, pud, a, round(a / kept, 6)))
    return xd, r2, dl, moved


def main(argv=None):
    # argv is a parameter so `src.export_factors` can call this as step 0 of the build
    # pipeline instead of shelling out; None still means "read sys.argv" for the CLI.
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rebuild", action="store_true",
                    help="re-run the scan reduction from the raw CSV before exporting")
    args = ap.parse_args(argv)

    dials = read_dials()
    p = load_paths(rebuild=args.rebuild).set_index("Consignment_ID")
    # also printed by delivery() during a rebuild; repeated here so a cached run still shows it
    print_kept_duration(p, p.articles, p.lodge_band)
    print_state_summary(p)
    joint, single, second, meta = derive(p)
    joint_f, single_f, dead, moved = filter_factors(joint, single, dials["fold"])
    r2 = round2_sites(second, dials["r2_min"])
    demand, recv_entry, legs, deliver_rows, vic_xd = derive_stages(p)
    xd_f, r2_f, dl_f, lane_moved = filter_stages(recv_entry, legs, deliver_rows,
                                                 {s for s, _, _ in r2}, dead, dials["fold"])
    # obs_single_sort is the ONCE slice of the lane matrix, so the two cannot disagree about
    # how much freight finishes in one building. It keeps its old columns — the share, and the
    # DENOMINATOR the share was measured on, which is what BAND_MIN_ARTICLES gates.
    _tot = {}
    for f, c, e, d, a in legs:
        _tot[(f, c, e)] = _tot.get((f, c, e), 0) + a
    _kept_flavours = {(f, c, e) for f, c, e, _, _ in single_f}
    single_f = [(f, c, e, sh, _tot[(f, c, e)]) for f, c, e, d, a, sh in r2_f
                if d == "ONCE" and (f, c, e) in _kept_flavours]

    FOBS.mkdir(parents=True, exist_ok=True)

    def w(rows, name, cols):
        df = pd.DataFrame(rows, columns=cols)
        df.to_csv(FOBS / f"{name}.csv", index=False)
        print(f"  ✓ {name + '.csv':<24} {len(df):>4} rows")

    # the joint's articles and the demand table are the SAME cohort counted two ways; if they
    # ever disagree a filter has run on one and not the other (Change 41 did exactly that)
    _joint_ea = sum(a for _, _, _, _, a in joint)
    _demand_ea = sum(a for _, _, a in demand)
    assert _joint_ea == _demand_ea, (
        f"obs_joint counts {_joint_ea:,} articles but obs_demand counts {_demand_ea:,} — "
        f"the two are built from different cohorts")
    w(joint_f, "obs_joint", ["pud", "cls", "tag", "share", "articles"])
    w(demand, "obs_demand", ["pud", "cls", "articles"])
    if PATH_BASIS == "facility_path":
        # ONE lane table. The columns are obs_round2's, unchanged, because the ONCE convention and
        # the share denominator mean exactly what they meant — what has changed is that a row can
        # now be a cross-dock leg as well as a second-sort leg, so the notebook reads one matrix
        # where it read two. The superseded pair is REMOVED rather than left beside it: they were
        # written on a cohort that no longer exists, and a stale factor CSV that still parses is
        # the failure this file's drift guard was built for. Flip PATH_BASIS to restore them.
        w(r2_f, "obs_legs", ["family", "cls", "entry", "dest", "articles", "share_of_entry"])
        for _old in ("obs_recv_entry", "obs_round2"):
            if (FOBS / f"{_old}.csv").exists():
                (FOBS / f"{_old}.csv").unlink()
                print(f"  ✗ {_old + '.csv':<24} removed — merged into obs_legs.csv")
    else:
        w(xd_f, "obs_recv_entry", ["cls", "recv", "entry", "articles", "share_of_recv"])
        w(r2_f, "obs_round2", ["family", "cls", "entry", "dest", "articles", "share_of_entry"])
    w(dl_f, "obs_delivery", ["cls", "exit", "pud", "articles", "share_of_exit"])
    w(single_f, "obs_single_sort", ["family", "cls", "site", "single_share", "articles"])
    w(r2, "obs_round2_sites", ["site", "share", "articles"])
    _pn = p.reindex(cohort(p).index)
    prov = [("source_scan_csv", SCAN.name),
            ("path_basis", PATH_BASIS),
            ("path_touch_bar", PATH_TOUCH_BAR),
            ("path_touch_events", "|".join(e.replace("ZPT_", "")
                                           for e in EVIDENCE[PATH_TOUCH_BAR])),
            ("path_depth", PATH_DEPTH),
            ("path_cap_rule", PATH_CAP_RULE),
            ("path_mean_buildings", round(float((_pn.path_n * _pn.articles).sum()
                                                / _pn.articles.sum()), 3)),
            ("path_interior_folded_ea", int((_pn.path_interior * _pn.articles).sum())),
            ("path_after_depot_ea", int(_pn.articles[_pn.path_after > 0].sum())),
            ("path_before_vic_ea", int(_pn.articles[_pn.path_before > 0].sum())),
            ("path_fell_outside_ea", int(_pn.articles[_pn.path_fell > 0].sum())),
            ("path_no_path_ea", "; ".join(
                f"{r}={int(_pn.articles[_pn.path_why == r].sum())}"
                for r in sorted(set(_pn.path_why)) if r != "path") or "none"),
            ("delivering_depot_basis", PDC_BASIS),
            ("kept_requires_depot_sort", KEPT_REQUIRE_SORT),
            ("depot_sort_events", "|".join(e.replace("ZPT_", "") for e in DEPOT_SORT_EVENTS)),
            ("reduction_articles", int(p.articles.sum())),
            *(("delivering_depot_" + k, v) for k, v in read_pdc_basis().items()
              if k in ("extract", "kept", "lost", "lost_no_scan", "lost_off_network")),
            ("reduction_cache", str(CACHE.relative_to(HERE))),
            ("generated_utc", _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")),
            ("generator", Path(__file__).name),
            ("filter_fold_min_articles", dials["fold"]),
            ("drop_unplaced_entry", DROP_UNPLACED_ENTRY),
            ("drop_unscanned_depot", DROP_UNSCANNED_DEPOT),
            ("filter_round2_min_share", dials["r2_min"]),
            ("source_families", "|".join(SOURCE_FAMILIES)),
            ("filtered_volume_reassigned_ea", moved),
            ("lane_volume_refolded_stage2_ea", lane_moved.get("stage2", 0)),
            ("lane_volume_refolded_stage3_ea", lane_moved.get("stage3", 0)),
            ("lane_volume_refolded_stage4_ea", lane_moved.get("stage4", 0)),
            ("measured_delivery_ea", sum(a for _, _, a in demand)),
            ("vic_crossdock_folded_ea", vic_xd),
            ("dropped_family_class_sites", "; ".join("/".join(k) for k in dead) or "none"),
            *meta.items()]
    w(prov, "_provenance", ["key", "value"])

    print(f"  fold at {dials['fold']:,} EA: {moved:,} EA "
          f"({100 * moved / meta['cohort_articles']:.2f}% of the cohort) folded into "
          f"surviving cells")
    if dead:
        print(f"  dropped family/class/site flavours: {', '.join('/'.join(k) for k in dead)}")
    print(f"  round-2 sites (share >= {dials['r2_min']:.0%}): "
          + ", ".join(f"{s} {sh:.0%}" for s, sh, _ in r2)
          + f"  — the other {len(second) - len(r2)} observed sites carry "
          f"{100 * (1 - sum(sh for _, sh, _ in r2)):.0f}% of second sorts and are not offered")
    _lm = sum(lane_moved.values())
    _path = PATH_BASIS == "facility_path"
    print(f"  lanes after the {dials['fold']:,} EA fold: "
          + (f"legs {len(r2_f)}, despatch {len(dl_f)}" if _path
             else f"stage2 {len(xd_f)}, stage3 {len(r2_f)}, stage4 {len(dl_f)}")
          + f" — {_lm:,} EA ({100 * _lm / meta['cohort_articles']:.2f}%) refolded "
          + ("(a dropped leg onto ONE BUILDING, a dropped despatch onto the surviving depots)"
             if _path else "(stage 2 onto the no-cross-dock diagonal, stage 3 onto sorted-once, "
                           "stage 4 onto the surviving depots)"))
    _rep_kept = sum(a for _f, _c, _e, d, a, _s in r2_f if d != "ONCE")
    _rep_all = sum(a for _f, _c, _e, d, a in legs if d != "ONCE")
    _word = "a second building" if _path else "a second sort"
    print(f"  {_word}: {_rep_kept:,} EA of the measured {_rep_all:,} EA keeps it; "
          f"the rest is modelled as finishing at the building it entered")
    print(f"  cohort '{meta['cohort']}' (modal delivery date {meta['peak_day']}), "
          f"{meta['cohort_articles']:,} articles; "
          f"measured delivery {sum(a for _, _, a in demand):,} EA is what the model now carries")


if __name__ == "__main__":
    main()
