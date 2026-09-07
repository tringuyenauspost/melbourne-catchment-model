"""The itinerary page, drawn from the NEW extract — inputs/new_scan_events/.

    IN   the reduction in analyse_new_scans.py (cached per-consignment rows)
    OUT  outputs/new_scan_analysis/sankey-facility-path-new.html

Same page, same rules, different day: `sankey_facility_path.py` holds every rule about what the
diagram MEANS — what a touch is, where the journey starts, how two names for one building fold —
and this module only says what the source IS. Six answers and it is done: the classes, the source
bands, how many delivering sites to name, what to call that column, how to tell a Victorian
building from an interstate one, and where the numbers came from.

WHAT IS DIFFERENT ABOUT THIS EXTRACT ────────────────────────────────────────────────
  * 768,086 articles over 11-12 May 2026, against 165,567 for one day in May — and it is the
    WHOLE terminating network, so StarTrack rides on the page beside Australia Post rather than
    being folded into "sorted elsewhere". The product buttons keep the two networks separable.
  * 1,422 delivering sites, not eleven depots. The last column names its biggest and folds the
    rest into one residual, the same bargain the touch columns make.
  * IT DRAWS EVERY BUILDING, not just ours (user, 2026-08-26). The 20 May page defaults to the
    MODEL basis because it exists to check a model that carries nineteen buildings; this extract
    is the whole terminating network, and the buildings the model does NOT carry are the reason
    to read it. On the model basis a quarter of the day's articles pass through one and it falls
    out unseen. So `--group` defaults to SCAN here and `--group model` is the opt-in.
  * VICTORIA IS DECIDED ON A COORDINATE, not on a state column: every event in this extract
    carries a point, so a building is Victorian if its own position falls inside VIC_BOX — the
    same rectangle `analyse_new_scans` bands lodgements with, and the same caveat applies (it is
    a rectangle, so it claims a strip of south-eastern SA and southern NSW).

Run:  uv run python plotting/sankey_facility_path_new.py
      uv run python plotting/sankey_facility_path_new.py --rebuild     # re-read the event CSVs
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))                # the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utilities"))               # ad-hoc tools
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model_input_preparation"))  # the reduction lives there
import argparse
import collections
import json

import pandas as pd

from analyse_new_scans import EVENT_FILES, SRC, VIC_BOX
from analyse_new_scans import chain_by_time as chain_in_time_order
from analyse_new_scans import OUT as NEW_OUT
from analyse_new_scans import load_paths
from sankey_from_new_scans import ORG_LABEL, ORG_ORDER   # the band vocabulary, said once
import sankey_facility_path as PAGE                # the page module, for the dials it owns
from sankey_facility_path import (  # the page — every rule about what it means lives there
    KEEP, alias, build, learn, report, top_after, write_page,
)

TARGET = NEW_OUT / "sankey-facility-path-new.html"
POINT_JSON = NEW_OUT / "facility_point.json"     # facility -> lat, lon, in Victoria
DEST_KEEP = 40                # delivering sites named before the fold — 82% of the day
PRODUCT = {"Parcel Post": "PP", "Express Post": "EP",
           "StarTrack Road Express": "STR", "StarTrack Premium": "STP"}
PRODUCT_LABEL = [("PP", "Parcel Post"), ("EP", "Express Post"),
                 ("STR", "ST Road"), ("STP", "ST Premium")]


def facility_point(rebuild=False):
    """Facility name -> "Victoria" or "Interstate", from the event coordinates, cached.

    The old extract carries a state column on every scan; this one carries a POINT, which is
    better evidence and needs one step of work. Every facility in the eight files is placed —
    4,096 of them, none missing — so a building is never dropped for want of a position, and the
    page's Victoria test is a measurement rather than a name match.
    """
    if POINT_JSON.exists() and not rebuild:
        cached = json.loads(POINT_JSON.read_text())
        # a cache from an older shape is not an error, it is a rebuild — the alternative is an
        # assertion five functions away that says nothing about which file is stale
        if all(isinstance(v, str) for v in cached.values()):
            return cached
        print("  the facility cache is an older shape — rebuilding it")
    print(f"  placing facilities from the event files (once; cached to {POINT_JSON.name})")
    frames = [pd.read_csv(SRC / f"{ev}.csv", low_memory=False,
                          usecols=["Event_facility_name", "Event_Lat", "Event_Long"])
              for ev in EVENT_FILES if (SRC / f"{ev}.csv").exists()]
    d = pd.concat(frames, ignore_index=True).dropna(subset=["Event_facility_name"])
    g = d.groupby("Event_facility_name")[["Event_Lat", "Event_Long"]].median()
    vic = (g.Event_Lat.between(*VIC_BOX["lat"]) & g.Event_Long.between(*VIC_BOX["lon"])).fillna(False)
    out = {name: ("Victoria" if v else "Interstate") for name, v in vic.items()}
    POINT_JSON.parent.mkdir(parents=True, exist_ok=True)
    POINT_JSON.write_text(json.dumps(out, sort_keys=True))
    print(f"  ✓ {len(out):,} facilities placed, {sum(v == 'Victoria' for v in out.values()):,} "
          f"of them in Victoria")
    return out


CATCH_JSON = NEW_OUT / "facility_catchment.json"   # facility -> is it inside the catchment


def facility_catchment(rebuild=False):
    """Facility name -> True if its own point falls inside the first-mile catchment, cached.

    `facility_point` asks Victoria-or-not off a rectangle; this asks the narrower question the
    model is actually built around — is this building inside the footprint we collect from — and
    it asks it of the same polygon `analyse_new_scans.metro_flag` bands lodgements with, so a
    building and a lodgement cannot disagree about where the catchment ends.

    THE CAVEAT TRAVELS WITH THE POLYGON. It is the first-mile ROUTE footprint, not an
    administrative boundary: it reaches Geelong, Bacchus Marsh, Kyneton and Woodend, and it is
    not the same thing as "inside Melbourne". It answers "inside the catchment we collect from",
    which is the question the model asks — so a page built on it should say catchment, not metro.

    The position is the MEDIAN of the building's own events, which for a van-pickup arm is the
    median lodgement point rather than the depot's address and can sit well away from it. That is
    why `path_census` unions the answer across a fold rather than asserting agreement: a van arm
    that folds onto a depot inside the catchment is inside it too, whatever its own point says.
    """
    if CATCH_JSON.exists() and not rebuild:
        return {k: bool(v) for k, v in json.loads(CATCH_JSON.read_text()).items()}
    from analyse_new_scans import metro_flag
    print(f"  placing facilities against the catchment (once; cached to {CATCH_JSON.name})")
    frames = [pd.read_csv(SRC / f"{ev}.csv", low_memory=False,
                          usecols=["Event_facility_name", "Event_Lat", "Event_Long"])
              for ev in EVENT_FILES if (SRC / f"{ev}.csv").exists()]
    d = pd.concat(frames, ignore_index=True).dropna(subset=["Event_facility_name"])
    g = d.groupby("Event_facility_name")[["Event_Lat", "Event_Long"]].median()
    inside = metro_flag(g.Event_Long, g.Event_Lat)
    out = {name: bool(v) for name, v in inside.fillna(False).items()}
    CATCH_JSON.parent.mkdir(parents=True, exist_ok=True)
    CATCH_JSON.write_text(json.dumps(out, sort_keys=True))
    print(f"  ✓ {len(out):,} facilities placed, {sum(out.values()):,} inside the catchment")
    return out


# ══ THE CHAIN IS BUILT HERE, AND NOT TAKEN FROM THE REDUCTION ════════════════════════
# `analyse_new_scans` orders every chain by `Event_seq`, which is right for the old extract and
# WRONG for this one. This extract arrives as eight files, one per event type, and Event_seq is
# the occurrence index WITHIN a type — the first sort is seq 1, the second sort is seq 2, and so
# is the second acceptance. Sorted by it, a consignment reads "all the firsts, then all the
# seconds": measured on a 250k-row sample of each file, 96.4% of consignments repeat a seq value,
# 86.3% come out in the wrong time order, and only 18.1% of chains survive the reordering
# unchanged. One real example, and it is typical:
#
#   seq 1  11 May 15:11  LODGE          HARTWELL LPO
#   seq 1  11 May 22:43  MACHINE_SORT   DANDENONG LC
#   seq 1  12 May 00:02  DEPART         DANDENONG LC
#   seq 1  12 May 14:32  DELIVER        EPPING DC          <- the delivery
#   seq 2  12 May 02:06  MACHINE_SORT   MELBOURNE GATEWAY FACILITY   <- twelve hours EARLIER
#
# which `full_chain` renders as "... > EPPING DC > MELBOURNE GATEWAY FACILITY": a building the
# parcel visited before it was delivered, drawn after the delivery. An itinerary page cannot use
# that, because ORDER IS THE WHOLE CONTENT here. So the chain is rebuilt from the event files on
# the timestamp, cached, and the reduction is left alone — whether `sort_chain`, `first_sort` and
# `crossdocked` should be rebuilt the same way is a bigger decision than this page.
def chain_by_time(bar="physical", rebuild=False):
    """Consignment -> the buildings it was in, in TIME order, consecutive repeats collapsed.

    The mechanics moved to `analyse_new_scans.chain_by_time` (2026-08-31) so `sankey_from_new_scans`
    can share them without an import cycle. This keeps the page's vocabulary: `bar` names an
    evidence set in `sankey_facility_path.EVIDENCE`, and the cache file is unchanged.
    """
    return chain_in_time_order(PAGE.EVIDENCE[bar], bar, rebuild)


def frame(p, chains):
    """The new extract, normalised to the six columns the page reads.

    The delivering site is `site` — the reduction's own three-way basis (the DELIVER scan where it
    names a building, the plan where it does not). It is aliased like every other name, so a site
    that folds onto another is the same node in the last column as it is in the touch columns —
    which means this runs AFTER the fold is learnt, never before.
    """
    dest = [alias(x) for x in p.site]
    return pd.DataFrame({
        "articles": p.articles.values,
        "cls": p.Product_type.map(PRODUCT).values,
        "band": p.source_band.values,
        "chain": chains,
        "dest": dest,
        "own": [frozenset([d]) for d in dest],
    })


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rebuild", action="store_true",
                    help="ignore the caches and re-read the event CSVs")
    ap.add_argument("--keep", type=int, default=KEEP, metavar="N",
                    help=f"facilities named per column before the fold (default {KEEP})")
    ap.add_argument("--dest-keep", type=int, default=DEST_KEEP, metavar="N",
                    help=f"delivering sites named in the last column (default {DEST_KEEP}); "
                         "the rest fold into one residual")
    ap.add_argument("--depth", type=int, default=PAGE.DEPTH, metavar="N",
                    help=f"touch columns before the delivering site (default {PAGE.DEPTH})")
    ap.add_argument("--touch", choices=tuple(PAGE.EVIDENCE), default="entry",
                    help="which events may name a building (default entry: LODGE / MACHINE_SORT / "
                         "TRANSFER / LOAD_ITEM / ACCEPT_FACILITY, in time order)")
    ap.add_argument("--group", choices=("model", "keep", "scan"), default="scan",
                    help="scan (default here, unlike the 20 May page): every building a scan "
                         "names, under its own name — which is the point of this extract. keep: "
                         "the same stops, but only the buildings the model carries are NAMED; the "
                         "rest share one node per column. model: only ours are stops at all, and "
                         "anything else falls out of the journey")
    args = ap.parse_args()
    PAGE.DEPTH = args.depth

    p = load_paths(args.rebuild)
    p = p[p.site.notna() & p.Product_type.isin(PRODUCT)].copy()
    ch = chain_by_time(args.touch, args.rebuild).reindex(p.Consignment_ID)
    chains = [c if isinstance(c, list) else [] for c in ch]
    folds, state = learn(zip(chains, p.articles), facility_point(args.rebuild))
    f = frame(p, chains)
    # the bands this extract actually has, in the reduction's own order — a button for a band
    # with no volume is a button that lies about the day
    present = set(f.band)
    cfg = {
        "classes": [c for c in PRODUCT_LABEL if c[0] in set(f.cls)],
        "bands": [(b, ORG_LABEL[b]) for b in ORG_ORDER if b in present],
        "dest_keep": args.dest_keep,
        "destcol": "Delivering site", "destword": "site",
        "group": None if args.group == "scan" else PAGE.model_names(),
        "outside": args.group == "keep",
        "bar": args.touch,
    }
    data, summary = build(f, state, args.keep, cfg)
    summary["folds"] = folds
    summary["top_after"] = top_after(f, summary)
    report(summary, data, folds, cfg["destword"])
    print(f"    evidence bar: {args.touch.upper()} — "
          + ", ".join(e.replace("ZPT_", "") for e in PAGE.EVIDENCE[args.touch]))
    write_page(data, summary, {
        "{{DATELINE}}": "11-12 May 2026",
        "{{PROVENANCE}}": "Built from the eight event files in <code>inputs/new_scan_events/</code>"
                          " by <code>sankey_facility_path_new.py</code>, over the reduction in "
                          "<code>analyse_new_scans.py</code> &mdash; the same page as the 20 May "
                          "extract's, drawn from the newer and wider one",
        "{{VICRULE}}": "tested on the building's OWN COORDINATE against <code>VIC_BOX</code>, the "
                       "rectangle the reduction bands lodgements with. Every facility in the "
                       "extract carries a point, so nothing falls through the test &mdash; but it "
                       "is a rectangle, so it claims a strip of south-eastern SA and southern NSW",
    }, TARGET)


if __name__ == "__main__":
    main()
