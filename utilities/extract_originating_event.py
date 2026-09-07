"""Every scan event of the consignments that ORIGINATED at the five modelled sites.

    IN   inputs/originating_volume/<SITE>_ORIGINATING_VOL.csv   (five files, ~1.9 GB)
    OUT  inputs/originating_volume/originating_volume_scan_events.csv

`notebooks/analyse-pickup-volume.ipynb` asks how much volume each site ORIGINATES and answers it
by summing `Article_count` over the first event of the day. This script keeps that question's
answer — the same condition, site for site, including the two places where the notebook departs
from the general rule — but instead of reducing the matched consignments to a number it emits
EVERY event those consignments carry, from all five files, in one CSV with the source schema
untouched. That is what `plotting/sankey_facility_path_originating.py` reads, so the itinerary
page can be drawn for the whole catchment rather than for the single Bayswater extract:

    uv run python utilities/extract_originating_event.py
    uv run python plotting/sankey_facility_path_originating.py \
        --src inputs/originating_volume/originating_volume_scan_events.csv --rebuild

THE ORIGIN CONDITION, AND WHY IT IS NOT ONE RULE ────────────────────────────────────
A consignment originates here if its FIRST traced event (`Event_seq == 1`) on 11 May 2026 happened
in one of the site's own buildings. Two event types can carry that first scan and the notebook
treats them separately, because they are different physical acts:

  * ZPT_MACHINE_SORT — the parcel reached the sorter without a lodgement scan of its own.
  * ZPT_LODGE — the parcel was lodged, and WHERE it was lodged is read differently per site:
      - Melbourne North, Bayswater: the van-operations arm only. The PDC's own lodge scans are
        freight arriving from elsewhere, not freight this site collected.
      - Sunshine West: any of the site's three names, all of which are collection points.
      - Oakleigh South, Dandenong South: NO event-type filter at all. Their first-of-day scan is
        often neither LODGE nor SORT, and requiring one of the two loses the volume — so any first
        event in the site's own buildings counts. (This makes the sort condition a subset for
        those two: the notebook's article sum double-counts it, a consignment set cannot.)

Only Parcel Post and Express Post are in scope, as in the notebook.

TWO GATES THE NOTEBOOK DOES NOT HAVE, both of them removing freight that ARRIVED here rather than
originated here. See ARRIVAL_EVENTS and NIGHT_SORT_HOURS below for the measurements that forced
each one.

  1. The first traced event is an arrival-side scan (DELIVER / ACCEPT_FACILITY / UNLOAD_ITEMS /
     REMOVE_AGGR). Removes ~17%, essentially all of it at Oakleigh South, Dandenong South and
     Melbourne PF; a no-op wherever the notebook already demands a LODGE or a MACHINE_SORT.
  2. The first traced event is a MACHINE_SORT struck between midnight and 08:00 AND the parcel is
     delivered by one of the site's own buildings — the overnight linehaul, sorted in the morning,
     never having gone anywhere. Removes ~1.5%, all of it at the three sites with a night sort.

WHAT THIS SCRIPT ADDS TO THE SOURCE SCHEMA ─────────────────────────────────────────
Two columns, and nothing else moves: `Origin_site` (which of the five claimed it) and
`Origin_basis` (`lodge`, `sort` or `both`). The page ignores unknown columns, so the file stays a
drop-in for a raw extract, and the columns are there because the first question anyone asks of a
merged file is which site a row came from.

A consignment is claimed ONCE, by the first site in `SITES` order that matches it, and only that
site's file supplies its events — otherwise a parcel that appears in two extracts would have its
itinerary drawn twice. The console prints how often that happens.

The files total ~1.9 GB, so both passes are chunked: pass one reads six columns to find the
consignments, pass two re-reads and writes out their rows.
"""
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parents[1]
SRC = HERE / "inputs" / "originating_volume"
OUT = SRC / "originating_volume_scan_events.csv"

DAY = "2026-05-11"
PRODUCTS = ["Parcel Post", "Express Post"]
CHUNK = 500_000

# The five red van sites, then the two TRANSPORT facilities. The notebook keeps the transport
# pair commented out and they are a different animal: a hub does not run a red van fleet, so what
# originates there is freight injected into the network, not freight a driver collected. They are
# carried here because the user asked to see them, and kept in their own group everywhere after.
SITES = ["MELBOUNRE_NORTH", "BAYSWATER", "SUNSHINE_WEST", "OAKLEIGH_SOUTH", "DANDENONG_SOUTH",
         "MELBOURNE_TRANSPORT", "DANDENONG_TRANSPORT"]
TRANSPORT = {"MELBOURNE_TRANSPORT", "DANDENONG_TRANSPORT"}

# (the van-operations arm that a LODGE scan must name, the buildings a SORT scan may name)
OPERATIONS = {
    "MELBOUNRE_NORTH": ("MELBOURNE NORTH VAN OPERATIONS", ["MELBOURNE NTH PARCEL FACILITY"]),
    "BAYSWATER": ("BAYSWATER VAN OPERATIONS", ["BAYSWATER PDC"]),
    "SUNSHINE_WEST": ("SUNSHINE WEST VAN SERVICES",
                      ["SUNSHINE WEST VAN SERVICES", "SUNSHINE WEST PDC",
                       "SUNSHINE WEST PARCEL DELIVERY"]),
    "DANDENONG_SOUTH": ("DANDENONG SOUTH VAN OPERATIONS",
                        ["DANDENONG SOUTH PDC", "DANDENONG SOUTH VAN OPERATIONS"]),
    "OAKLEIGH_SOUTH": ("OAKLEIGH SOUTH VAN OPERATIONS",
                       ["OAKLEIGH SOUTH PDC", "OAKLEIGH SOUTH VAN OPERATIONS"]),
    # MPF is three buildings in the scans, and the notebook's own MPF branch names all three —
    # `operations_name` lists only the first, but the total it prints reads the wider set.
    "MELBOURNE_TRANSPORT": ("MELBOURNE PARCEL FACILITY",
                            ["MELBOURNE PARCEL FACILITY", "MELBOURNE PARCEL FACILITY DWS",
                             "MPF BULK PARCELS"]),
    "DANDENONG_TRANSPORT": ("DANDENONG LC", ["DANDENONG LC"]),
}

# ══ THE ARRIVAL GATE ═════════════════════════════════════════════════════════════════
# A consignment whose FIRST traced event is one of these did not originate here — it ARRIVED. The
# gate exists because `Lodge_*` in these extracts is NOT a lodgement: measured over the merged
# file, the lodge timestamp equals the first traced event's timestamp for 96.2% of articles and
# the lodge facility matches for 98.0%, and the "lodge" event's real type is ZPT_LODGE for only
# 239,058 of 412,826 — the rest are MACHINE_SORT, REMOVE_AGGR, ACCEPT_FACILITY and DELIVER. The
# extract seeds on ANY event at the work centre, so "the first traced event is at this site" is
# true by construction and `Event_seq == 1 & facility in site` selects almost the whole file.
#
# Left ungated it claimed 79.0% of the Oakleigh South file and 76.3% of Dandenong South, and
# 29,008 of the 31,223 articles the page drew as "started at its own depot" had no ZPT_LODGE
# anywhere — their whole event history reads ACCEPT_FACILITY -> UNLOAD_ITEMS -> DELIVER, or
# REMOVE_AGGR -> DELIVER, or a lone DELIVER. **3,074 articles were admitted on their own delivery
# scan**, counted as originating because the one thing they did at the site was get delivered.
#
# It is a NO-OP for the sites that already require a LODGE or a MACHINE_SORT — neither is in the
# set — which is the point: the rule is now uniform rather than a special case for two depots.
ARRIVAL_EVENTS = {"ZPT_DELIVER", "ZPT_ACCEPT_FACILITY", "ZPT_UNLOAD_ITEMS", "ZPT_REMOVE_AGGR"}

# ══ THE OVERNIGHT SORT ════════════════════════════════════════════════════════════════
# A second class of arrival that `ARRIVAL_EVENTS` cannot see, because the site's first scan on the
# parcel is a ZPT_MACHINE_SORT and a sort is not an arrival. The tell is the CLOCK, and it is not
# subtle — first-scan hour at Bayswater / Sunshine West / Melbourne North:
#
#     population                                    n        median   00-07h   12-19h
#     first scan at a VAN arm (known collection)    42,988      14h     0.1%    83.8%
#     the ones that never leave the depot            8,050       6h    65.5%    24.5%
#
# A red van collection cannot be first scanned at 3am — the driver is out in the afternoon, and
# the known collections say so at 0.1%. Freight struck by the morning sort at 03:00-06:00 came in
# on the overnight linehaul and its arrival scan was never recorded.
#
# BUT THE CLOCK ALONE IS NOT ENOUGH, and this is why the rule has a second half. 37,836 EA are
# struck 00:00-07:59 on a seq-1 sort, and only 5,261 of them never leave the site. The other
# 32,575 CONTINUE — to Melbourne PF (10,676), Tullamarine PF (2,790), Sydney PF (2,592) — which is
# the OUTBOUND flow, exactly what originating volume does. A parcel that arrived overnight from a
# hub does not then set off for that hub. Those are originations whose collection scan is missing,
# not arrivals, and cutting them on the clock alone would have deleted half of Bayswater.
#
# So the gate needs BOTH: struck overnight AND delivered by one of the site's own buildings, i.e.
# it never went anywhere. That is 5,261 EA and it is the population the evidence actually covers.
NIGHT_SORT_HOURS = range(0, 8)          # 00:00-07:59; hour 8 is empty in this extract

# The site's own buildings — every name the scans use for one physical place, which is also what
# the page's alias fold merges. It has to be spelled out here because the extract has no fold: the
# delivering building reads HOLLOWAY DR PARCEL OPERATIONS, not BAYSWATER PDC, on most of Bayswater's
# own deliveries, and a rule that missed that would catch none of them.
OWN_BUILDINGS = {
    "MELBOUNRE_NORTH": {"MELBOURNE NTH PARCEL FACILITY", "MELBOURNE NORTH VAN OPERATIONS",
                        "MELBOURNE NORTH PDC"},
    "BAYSWATER": {"BAYSWATER PDC", "BAYSWATER VAN OPERATIONS",
                  "HOLLOWAY DR PARCEL OPERATIONS"},
    "SUNSHINE_WEST": {"SUNSHINE WEST PDC", "SUNSHINE WEST VAN SERVICES",
                      "SUNSHINE WEST PARCEL DELIVERY"},
    "OAKLEIGH_SOUTH": {"OAKLEIGH SOUTH PDC", "OAKLEIGH SOUTH VAN OPERATIONS"},
    "DANDENONG_SOUTH": {"DANDENONG SOUTH PDC", "DANDENONG SOUTH VAN OPERATIONS"},
    "MELBOURNE_TRANSPORT": {"MELBOURNE PARCEL FACILITY", "MELBOURNE PARCEL FACILITY DWS",
                            "MPF BULK PARCELS"},
    "DANDENONG_TRANSPORT": {"DANDENONG LC"},
}

# the sites whose lodge condition drops the event-type test — see the header. MPF joins them
# because the notebook's MPF branch has no event-type filter either: a hub's first scan on a
# consignment is a sort or an unload far more often than a lodgement.
ANY_EVENT = {"OAKLEIGH_SOUTH", "DANDENONG_SOUTH", "MELBOURNE_TRANSPORT"}

# ONE DEPARTURE FROM THE NOTEBOOK, AND IT IS DELIBERATE. The notebook's MPF branch drops the
# `Event_date == DAY` test as well, so its MPF total spans both lodgement days while every other
# site's spans one. That is fine for a number printed on its own and wrong for this file, which
# feeds a diagram whose every share is a fraction of ONE day: a two-day MPF next to five one-day
# sites would overstate the hub and make the page's dateline a lie. The date test is therefore
# applied uniformly. Run the notebook if you want its MPF figure.

SCAN_COLS = ["Consignment_ID", "Event_seq", "Event_type", "Event_date", "Event_local_time",
             "Event_facility_name", "Terminating_facility_name", "Product_type", "Article_count"]


# the extract for a site is not always named after it — Dandenong LC arrived as DLC_*
FILE_STEM = {"DANDENONG_TRANSPORT": "DLC"}


def path(site):
    return SRC / f"{FILE_STEM.get(site, site)}_ORIGINATING_VOL.csv"


def origin_ids(site):
    """The consignments this site originated, as {sort ids}, {lodge ids} — the notebook's condition."""
    lodge_arm, sort_sites = OPERATIONS[site]
    sort_ids, lodge_ids = set(), set()
    for ch in pd.read_csv(path(site), usecols=SCAN_COLS, dtype={"Consignment_ID": str},
                          chunksize=CHUNK, low_memory=False):
        first = ch[ch.Product_type.isin(PRODUCTS)
                   & (ch.Event_seq == 1) & (ch.Event_date == DAY)
                   & ~ch.Event_type.isin(ARRIVAL_EVENTS)]
        # …and the overnight sort that never left the building — see NIGHT_SORT_HOURS
        hour = pd.to_datetime(first.Event_local_time, format="%H:%M:%S",
                              errors="coerce").dt.hour
        first = first[~((first.Event_type == "ZPT_MACHINE_SORT")
                        & hour.isin(NIGHT_SORT_HOURS)
                        & first.Terminating_facility_name.isin(OWN_BUILDINGS[site]))]
        sort_ids |= set(first.Consignment_ID[
            (first.Event_type == "ZPT_MACHINE_SORT")
            & first.Event_facility_name.isin(sort_sites)])
        if site in ANY_EVENT:
            hit = first.Event_facility_name.isin(sort_sites)
        elif site == "SUNSHINE_WEST":
            hit = (first.Event_type == "ZPT_LODGE") & first.Event_facility_name.isin(sort_sites)
        else:
            hit = (first.Event_type == "ZPT_LODGE") & (first.Event_facility_name == lodge_arm)
        lodge_ids |= set(first.Consignment_ID[hit])
    return sort_ids, lodge_ids


def basis_map(sort_ids, lodge_ids):
    """Which of the two conditions matched, per consignment."""
    b = {c: "sort" for c in sort_ids}
    for c in lodge_ids:
        b[c] = "both" if c in b else "lodge"
    return b


def main():
    sites = [s for s in SITES if path(s).exists()]
    missing = [s for s in SITES if not path(s).exists()]
    if not sites:
        sys.exit("no originating extracts found in inputs/originating_volume/")
    if missing:
        print("  NOT BUILT — no extract for: " + ", ".join(missing))
        print("  (drop <SITE>_ORIGINATING_VOL.csv in inputs/originating_volume/ and re-run; "
              "everything downstream picks them up)\n")

    print(f"origin condition: Event_seq == 1 on {DAY}, {' / '.join(PRODUCTS)}\n")
    claimed, basis, taken = {}, {}, set()
    for site in sites:
        sort_ids, lodge_ids = origin_ids(site)
        ids = (sort_ids | lodge_ids) - taken
        overlap = len(sort_ids | lodge_ids) - len(ids)
        claimed[site] = ids
        basis.update({c: b for c, b in basis_map(sort_ids, lodge_ids).items() if c in ids})
        taken |= ids
        print(f"  {site:<16} sort {len(sort_ids):>7,}  lodge {len(lodge_ids):>7,}  "
              f"claimed {len(ids):>7,}" + (f"  (already claimed elsewhere: {overlap:,})"
                                           if overlap else ""))

    print(f"\n  {'TOTAL':<16} {len(taken):,} consignments\n")

    OUT.unlink(missing_ok=True)
    rows = arts = 0
    per_site = {}
    for site in sites:
        ids = claimed[site]
        kept = n = 0
        for ch in pd.read_csv(path(site), dtype={"Consignment_ID": str},
                              chunksize=CHUNK, low_memory=False):
            ch = ch[ch.Consignment_ID.isin(ids)]
            if ch.empty:
                continue
            ch = ch.assign(Origin_site=site,
                           Origin_basis=ch.Consignment_ID.map(basis))
            ch.to_csv(OUT, mode="a", header=not OUT.exists(), index=False)
            kept += len(ch)
            n += ch.groupby("Consignment_ID").Article_count.first().sum()
        per_site[site] = (kept, n)
        rows += kept
        arts += n
        print(f"  {site:<16} {kept:>9,} events   {n:>8,} articles")

    print(f"\n  {'TOTAL':<16} {rows:>9,} events   {arts:>8,} articles")
    print(f"  wrote {OUT.relative_to(HERE)} ({OUT.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
