"""Combine the eight new_scan_events files into one table per depot, sorted by time, + its paths.

    uv run python utilities/combine_depot_events.py --site sunshine_west --filter both --paths
    uv run python utilities/combine_depot_events.py --site tullamarine  --filter terminating
    uv run python utilities/combine_depot_events.py --site sunshine_west --filter event

The eight files are one file per event type, same 29 columns, so the combine is a concat.
`Event_seq` is NOT chronological in this extract (it is a per-event-type index), so the sort key
is a real timestamp built from Event_date + Event_local_time, never the sequence number. Events
sharing a timestamp tie-break on EVENT_ORDER, the order they happen inside one building --
sorted FILENAMES are alphabetical, which is a different and wrong order.

Five readings of "only this depot", because the name sits in two columns:
  terminating  (default) every event of the consignments whose Terminating_facility_name is the
               depot -- its freight, whole journey, wherever it was scanned
  event        only the events whose Event_facility_name is the depot -- scans in the building
  touched      every event of the consignments with at least one event at the depot
  delivered    every event of the consignments whose ZPT_DELIVER scan fired at the depot -- the
               delivery as it happened, not as it was planned
  both         the intersection: planned for AND delivered at the depot

ALIASES, and they are not cosmetic. One building carries several names -- a PDC, its delivery
arm, its van arm -- and `Terminating_facility_name` may use a name that NEVER appears as an
event (Sunshine West is planned as SUNSHINE WEST PARCEL DELIVERY and worked as SUNSHINE WEST
PDC). Leaving them unmerged draws a real building as two and pins its kept rate at ~0. The
test is the distance between the median coordinates of the IN-BUILDING events only
(ZPT_UNLOAD_ITEMS is best: docked at the dock, SD ~1 m) -- never DELIVER, which sits at the
customer's address, nor LODGE at a van site, which sits out on the pickup round. Measured:
SUNSHINE WEST PARCEL DELIVERY is 55 m from SUNSHINE WEST PDC and VAN SERVICES 193 m, so all
three are one site; TULLAMARINE PARCEL FACILITY is 12.2 km from Tullamarine PDC and is NOT an
alias of anything here. Aliases are folded to the canonical name in the PATH, so the journey
does not draw SWP > SWPD as two buildings.

Every run prints an ALIGNMENT report: the planned depot against the delivered depot. At
Tullamarine the two NEST rather than disagree (delivered is a 99.94% subset), so the planned
column is the safe basis and the DELIVER scan alone undercounts the depot.

The grain of these files is (consignment, terminating depot), NOT the consignment: a SPLIT
consignment whose articles go to two depots appears once per depot, with its physical scan rows
repeated verbatim under each `Terminating_facility_name` (0.16% of the file). So the id-based
filters keep this depot's rows and drop the other depot's copy -- a foreign flavour survives
only for a consignment with no row at this depot at all.

Each file also carries one PLACEHOLDER row per consignment with no event of that type
(`Events_traced == 0`, every Event_* column blank, delivery and terminating columns filled).
Dropped by default -- they are not events, and no consignment is lost. `--keep-empty` keeps
them, sorted last.
"""
import argparse
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]      # repo root, one up from utilities/
SRC = REPO / "inputs" / "new_scan_events"
OUTDIR = REPO / "outputs" / "new_scan_analysis"

# canonical name -> every name that IS that building. See the alias note in the docstring:
# add a name here only on the in-building coordinate test, never on the name looking similar.
SITES = {
    "tullamarine": ("TULLAMARINE PDC", ["TULLAMARINE PDC"]),
    "sunshine_west": ("SUNSHINE WEST PDC", ["SUNSHINE WEST PDC",
                                            "SUNSHINE WEST PARCEL DELIVERY",
                                            "SUNSHINE WEST VAN SERVICES"]),
}
UNNAMED = "<unnamed>"                   # a real event whose Event_facility_name is blank
# the order these events happen in INSIDE one building -- the tiebreak when timestamps tie
EVENT_ORDER = ["ZPT_LODGE", "ZPT_ACCEPT_FACILITY", "ZPT_UNLOAD_ITEMS", "ZPT_MACHINE_SORT",
               "ZPT_TRANSFER", "ZPT_LOAD_ITEM", "ZPT_DEPART_CONTAINER", "ZPT_DELIVER"]
CHUNK = 500_000
# the id is numeric in the file but is an identifier, so it stays a string throughout
STR_COLS = {"Consignment_ID": str, "Contract_ID": str, "Terminating_facility_ID": str,
            "Del_postcode": str, "Del_SA2_code": str}


def chunks(path):
    return pd.read_csv(path, dtype=STR_COLS, chunksize=CHUNK, low_memory=False)


def consignments_at(files, column, aliases):
    """Ids of the consignments with at least one row whose `column` names the depot."""
    ids = set()
    for path in files:
        for chunk in chunks(path):
            ids.update(chunk.loc[chunk[column].isin(aliases), "Consignment_ID"])
    return ids


def delivered_at(files, aliases):
    """Ids whose ZPT_DELIVER scan fired at the depot -- the delivery as it happened."""
    deliver = [f for f in files if f.stem == "ZPT_DELIVER"]
    if not deliver:
        raise SystemExit("ZPT_DELIVER.csv is missing -- the delivered basis needs it")
    ids = set()
    for path in deliver:
        for chunk in chunks(path):
            hit = chunk.Event_type.notna() & chunk.Event_facility_name.isin(aliases)
            ids.update(chunk.loc[hit, "Consignment_ID"])
    return ids


def alignment(planned, delivered):
    both = planned & delivered
    print(f"\nALIGNMENT  planned {len(planned):,} vs delivered {len(delivered):,}")
    print(f"  both                                       {len(both):>8,}")
    print(f"  planned only (no DELIVER scan here)        {len(planned - delivered):>8,}")
    print(f"  delivered only (planned for another depot) {len(delivered - planned):>8,}")
    if planned | delivered:
        print(f"  jaccard {len(both) / len(planned | delivered):.3f} · "
              f"delivered inside planned {100 * len(both) / max(len(delivered), 1):.2f}%")


def write_paths(df, out, canon, aliases):
    """One row per consignment: the buildings it touched, in time order, repeats collapsed."""
    ev = df[df.Event_type.notna()].copy()           # placeholders carry no building
    # ~1% of real events name no building (mostly on-road TRANSFER, some with coordinates).
    # They stay in the path as UNNAMED rather than being dropped -- dropping them would stitch
    # the buildings either side together and invent a lane that never existed.
    nameless = int(ev.Event_facility_name.isna().sum())
    if nameless:
        print(f"\n  {nameless:,} real events name no building -- kept in the path as {UNNAMED}")
    building = ev.Event_facility_name.fillna(UNNAMED)
    # fold the aliases BEFORE collapsing repeats, or one building draws as two
    folded = int(building.isin([a for a in aliases if a != canon]).sum())
    if folded:
        print(f"  {folded:,} events folded to {canon} from its alias names")
    ev["building"] = building.where(~building.isin(aliases), canon)

    rows = []
    for cid, sub in ev.groupby("Consignment_ID", sort=False):
        seen = sub.building.tolist()
        # collapse only CONSECUTIVE repeats -- a genuine A > B > A return must survive
        path = [b for i, b in enumerate(seen) if i == 0 or b != seen[i - 1]]
        first = sub.iloc[0]
        rows.append({"Consignment_ID": cid, "n_buildings": len(path), "n_events": len(sub),
                     "path": " > ".join(path), "first_building": path[0],
                     "last_building": path[-1], "Article_count": first.Article_count,
                     "Product_type": first.Product_type,
                     "Terminating_facility_name": first.Terminating_facility_name,
                     "first_event": sub.Event_datetime.iloc[0],
                     "last_event": sub.Event_datetime.iloc[-1]})
    paths = pd.DataFrame(rows).sort_values("Consignment_ID").reset_index(drop=True)
    pf = out.with_name(out.stem + "_paths.csv")
    paths.to_csv(pf, index=False)

    freq = (paths.groupby("path")
            .agg(consignments=("Consignment_ID", "size"), articles=("Article_count", "sum"),
                 n_buildings=("n_buildings", "first"))
            .sort_values("consignments", ascending=False).reset_index())
    freq["share_pct"] = (100 * freq.consignments / len(paths)).round(3)
    ff = out.with_name(out.stem + "_path_frequency.csv")
    freq.to_csv(ff, index=False)

    print(f"\nPATHS  {len(paths):,} consignments · {len(freq):,} distinct paths")
    print(f"  buildings per consignment: mean {paths.n_buildings.mean():.2f} · "
          f"median {int(paths.n_buildings.median())} · max {paths.n_buildings.max()}")
    print(f"  path ends at {canon}: {paths.last_building.eq(canon).sum():,} "
          f"({100 * paths.last_building.eq(canon).mean():.1f}%)")
    print("\n  top 10 paths")
    for _, r in freq.head(10).iterrows():
        print(f"  {r.consignments:>6,} {r.share_pct:>6.2f}%  {r.path}")
    print(f"\n-> {pf}\n-> {ff}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", choices=sorted(SITES), default="tullamarine",
                    help="which depot to extract")
    ap.add_argument("--filter", choices=["terminating", "event", "touched", "delivered", "both"],
                    default="terminating", help="which reading of the depot to keep")
    ap.add_argument("--paths", action="store_true",
                    help="also write the building path per consignment + a frequency ranking")
    ap.add_argument("--out", type=Path, default=None, help="output csv")
    ap.add_argument("--keep-empty", action="store_true",
                    help="keep the Events_traced==0 placeholder rows (no event happened)")
    args = ap.parse_args()
    canon, aliases = SITES[args.site]
    if args.out is None:
        args.out = OUTDIR / f"{args.site}_pdc_events_{args.filter}.csv"

    files = sorted(SRC.glob("ZPT_*.csv"))
    if not files:
        raise SystemExit(f"no ZPT_*.csv under {SRC}")
    print(f"{len(files)} event files under {SRC}")
    print(f"site {canon}" + (f" · aliases folded: {', '.join(a for a in aliases if a != canon)}"
                             if len(aliases) > 1 else " · no aliases"))

    planned = consignments_at(files, "Terminating_facility_name", aliases)
    delivered = delivered_at(files, aliases)
    alignment(planned, delivered)

    keep_ids = None
    if args.filter == "touched":
        keep_ids = consignments_at(files, "Event_facility_name", aliases)
        print(f"\n{len(keep_ids):,} consignments touched {canon}")
    elif args.filter == "delivered":
        keep_ids = delivered
    elif args.filter == "both":
        keep_ids = planned & delivered
    print()

    parts, scanned = [], 0
    for path in files:
        rows = 0
        for chunk in chunks(path):
            scanned += len(chunk)
            if args.filter == "terminating":
                chunk = chunk[chunk.Terminating_facility_name.isin(aliases)]
            elif args.filter == "event":
                chunk = chunk[chunk.Event_facility_name.isin(aliases)]
            else:                                   # touched, delivered, both
                chunk = chunk[chunk.Consignment_ID.isin(keep_ids)]
                # a split consignment repeats its scans under each terminating depot -- keep
                # this depot's copy, and a foreign one only where there is no copy here
                chunk = chunk[chunk.Terminating_facility_name.isin(aliases)
                              | ~chunk.Consignment_ID.isin(planned)]
            if len(chunk):
                parts.append(chunk)
                rows += len(chunk)
        print(f"  {path.name:<28} {rows:>9,} kept")

    if not parts:
        raise SystemExit(f"nothing matched {canon} under filter={args.filter}")
    df = pd.concat(parts, ignore_index=True)

    if not args.keep_empty:
        empty = df.Event_type.isna()
        print(f"  dropping {int(empty.sum()):,} placeholder rows with no event")
        df = df[~empty].copy()

    # one sortable timestamp; a bad or missing date sorts last rather than dropping the row
    df["Event_datetime"] = pd.to_datetime(
        df.Event_date.astype(str) + " " + df.Event_local_time.astype(str),
        format="%Y-%m-%d %H:%M:%S", errors="coerce")
    bad = int(df.Event_datetime.isna().sum())
    if bad:
        print(f"  {bad:,} rows have no parseable Event_date/Event_local_time -- sorted last")
    df["_ev"] = pd.Categorical(df.Event_type, categories=EVENT_ORDER, ordered=True)
    unknown = df._ev.isna() & df.Event_type.notna()
    if unknown.any():
        raise SystemExit(f"event types missing from EVENT_ORDER: "
                         f"{sorted(df.loc[unknown, 'Event_type'].unique())}")
    df = df.sort_values(["Consignment_ID", "Event_datetime", "_ev"],
                        na_position="last", kind="stable").reset_index(drop=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.drop(columns="_ev").to_csv(args.out, index=False)
    if args.paths:
        write_paths(df, args.out, canon, aliases)

    art = df.groupby("Consignment_ID", observed=True).Article_count.first().sum()
    print(f"\nsite={args.site} filter={args.filter} · {scanned:,} rows read · {len(df):,} kept")
    print(f"{df.Consignment_ID.nunique():,} consignments · {art:,} articles · "
          f"{df.Event_type.nunique()} event types")
    print(f"{df.Event_datetime.min()} .. {df.Event_datetime.max()}")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
