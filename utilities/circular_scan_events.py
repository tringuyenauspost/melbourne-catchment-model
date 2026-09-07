"""Every scan of every parcel whose journey goes round in a circle.

    IN   the eight event files in inputs/new_scan_events/
    OUT  outputs/path_census_sankey/circular_scan_events.csv — one row per SCAN

CIRCULAR MEANS ONE THING HERE (user, 2026-08-27): the parcel REACHED THE BUILDING THAT WOULD
DELIVER IT, LEFT, AND CAME BACK. Not any building revisited — a parcel that bounces between two
hubs and is then delivered normally is a different story, and `path_census`'s
`revisits_any_building` column is where that one lives. This is the loop that is always a mistake:
the depot had it, sent it away, and got it again.

That flag lives on a folded path, which is exactly what makes it readable and exactly what makes
it useless for asking WHY: the path says the depot got it back, and says nothing about what was
done to it in between. This script goes back to the raw scans for those parcels and hands over
everything the extract holds about them.

IT IS DELIBERATELY WIDER THAN THE PATH ─────────────────────────────────────────────
Two widenings, both on purpose:

  ALL EIGHT EVENT TYPES, not the five of the entry bar. The path is drawn on the events that mean
  a building INDUCTED the freight; the question "why did it come back" is usually answered by the
  ones that do not — an UNLOAD with no matching sort, a DEPART_CONTAINER to the wrong address.
  Dropping them here would hide the answer to the question the file exists to ask.

  EVERY SCAN OF THE PARCEL, including the ones outside Victoria and the ones after it reached the
  site that delivered it. The loop is a Victorian, pre-delivery event; its cause need not be.

Which means a row in this file is NOT a building on the path, and the two will not tally. The
`on_path` column says which rows are, so the tally can be recovered.

ONE CIRCLE IS ONE CONSIGNMENT ──────────────────────────────────────────────────────
The path is recorded per consignment, so the unit selected here is the consignment and the volume
attributed to it is its article count. A multi-article consignment whose articles were split up
contributes one path — the union of what its articles did — so it can be flagged circular when no
single article ever doubled back. `--singles` keeps only one-article consignments, where the
circle is unambiguously one article's.

Run:  uv run python utilities/circular_scan_events.py
      uv run python utilities/circular_scan_events.py --singles
      uv run python utilities/circular_scan_events.py --scope vic      # the un-truncated journey
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plotting"))   # the diagrams live there
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model_input_preparation"))  # the exporter lives there
import argparse
import pathlib

import pandas as pd

from analyse_new_scans import EVENT_FILES, SRC
import sankey_facility_path as PAGE
import sankey_facility_path_new as NEW
import path_census as PC

OUT = pathlib.Path("outputs/path_census_sankey/circular_scan_events.csv")
KEYS = ["Consignment_ID", "Event_date", "Event_local_time", "Event_facility_name"]


def circular_consignments(scope, touch, singles, rebuild):
    """Consignment ID -> (its folded path, articles), for the parcels whose path is circular.

    Built through `path_census.journeys` rather than beside it, so the flag here and the
    `circular` column in paths.csv can never drift apart: same fold, same scope, same test.
    """
    p = NEW.load_paths(rebuild)
    p = p[p.site.notna() & p.Product_type.isin(NEW.PRODUCT)].copy()
    ch = NEW.chain_by_time(touch, rebuild).reindex(p.Consignment_ID)
    chains = [c if isinstance(c, list) else [] for c in ch]
    _folds, state = PAGE.learn(zip(chains, p.articles), NEW.facility_point(rebuild))
    f = NEW.frame(p, chains)
    ids = p.Consignment_ID.tolist()
    if singles:
        keep = f.articles == 1
        f, ids = f[keep], [i for i, k in zip(ids, keep) if k]

    out = {}
    art = 0
    for cid, (path, dest, a, _c, _b) in zip(ids, PC.journeys(f, state, None, scope)):
        if PC.returns_to_site(path, dest, scope):
            out[str(cid)] = (" > ".join(path), dest, a)
            art += a
    print(f"  {len(out):,} consignments / {art:,} articles came back to the building that "
          f"delivered them ({scope} scope, {touch.upper()} bar)")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scope", choices=("drawn", "vic", "full"), default="drawn",
                    help="which journey the circle is tested on (default drawn — the Sankey's)")
    ap.add_argument("--touch", choices=tuple(PAGE.EVIDENCE), default="entry",
                    help="which events may name a building on that path (default entry). It does "
                         "NOT restrict the scans written out — those are always all eight types")
    ap.add_argument("--singles", action="store_true",
                    help="keep only one-article consignments, where the circle is unambiguous")
    ap.add_argument("--out", default=str(OUT), help=f"where to write (default {OUT})")
    ap.add_argument("--rebuild", action="store_true", help="re-read the scan files")
    args = ap.parse_args()

    want = circular_consignments(args.scope, args.touch, args.singles, args.rebuild)
    if not want:
        raise SystemExit("  no circular paths at this scope — nothing to write")

    # ── every scan of those parcels, out of all eight files ──────────────────────────
    frames = []
    for ev in EVENT_FILES:
        src = SRC / f"{ev}.csv"
        if not src.exists():
            continue
        d = pd.read_csv(src, dtype={"Consignment_ID": str}, low_memory=False)
        d = d[d.Consignment_ID.isin(want)]
        # EVERY FILE CARRIES A ROW PER CONSIGNMENT whether or not that event happened to it —
        # 602k of ZPT_LOAD_ITEM's 730k rows are one. A placeholder has no Event_type, no date and
        # no facility (checked in all eight files), so it is not a scan and does not belong in a
        # file of scans; keeping them would put 18% empty rows in front of the reader and break
        # the per-consignment ordering.
        blank = d.Event_type.isna()
        print(f"    {int((~blank).sum()):>8,} scans from {ev}"
              + (f"  ({int(blank.sum()):,} placeholder rows dropped)" if blank.any() else ""))
        frames.append(d[~blank])
    e = pd.concat(frames, ignore_index=True)

    # ── in the order they happened, which Event_seq does NOT give ────────────────────
    # Event_seq is the occurrence index WITHIN an event type in this extract, so sorting on it
    # reads "all the firsts, then all the seconds" and puts a delivery before a sort that
    # preceded it. The timestamp is the only ordering this file may use; `event_order` is written
    # out so the order survives a re-sort in Excel.
    e["ts"] = pd.to_datetime(e.Event_date + " " + e.Event_local_time.fillna("00:00:00"),
                             errors="coerce")
    lost = int(e.ts.isna().sum())
    assert not lost, f"{lost:,} real scans have no usable timestamp — investigate before trusting"
    e = e.sort_values(["Consignment_ID", "ts"], kind="stable", na_position="last")
    e["event_order"] = e.groupby("Consignment_ID").cumcount() + 1

    # ── the context that makes a row readable on its own ─────────────────────────────
    e["circular_path"] = e.Consignment_ID.map(lambda c: want[c][0])
    e["delivering_site"] = e.Consignment_ID.map(lambda c: want[c][1])
    e["path_articles"] = e.Consignment_ID.map(lambda c: want[c][2])
    e["building"] = e.Event_facility_name.map(PAGE.alias)     # the path's own vocabulary
    on = [set(v[0].split(" > ")) for v in want.values()]
    onmap = {c: s for c, s in zip(want, on)}
    e["on_path"] = [b in onmap[c] for b, c in zip(e.building, e.Consignment_ID)]

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    e.drop(columns=["ts"]).to_csv(out, index=False)
    print(f"\n  {len(e):,} scans for {e.Consignment_ID.nunique():,} consignments "
          f"({len(e) / e.Consignment_ID.nunique():.1f} each)")
    print(f"  {int(e.on_path.sum()):,} of them name a building that is ON the circular path")
    print("\n  scans by event type")
    for k, v in e.Event_type.value_counts().items():
        print(f"    {v:>9,}  {k}")
    print(f"\n  wrote {out}")


if __name__ == "__main__":
    main()
