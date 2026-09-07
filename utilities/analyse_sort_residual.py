"""Two questions the Sankey raises but cannot answer: the 3+ sorts, and the NONE node.

Reads the cached reduction from export_chain2_factors.py — the FIRST extract, the one the
sankey-from-scans page draws — and goes back to the raw scan file for the residual, because the
reduction deliberately throws away what happens outside the eight modelled sites and that is
exactly what these two questions are about.

    uv run python utilities/analyse_sort_residual.py            # both reports, cached event pull
    uv run python utilities/analyse_sort_residual.py --rebuild  # re-reads the 394 MB scan file first
    uv run python utilities/analyse_sort_residual.py --csv      # also writes the three tables
    uv run python utilities/analyse_sort_residual.py --all      # the unfiltered 166,159 instead

THE DENOMINATOR IS THE ELIGIBLE COHORT, 165,567 ────────────────────────────────────
Every share below is quoted over the parcels the model actually measures, which is what
export_chain2_factors.cohort() keeps: `origin != "UNKNOWN"`. That drops 592 articles (0.36%) whose
lodgement state could not be established — no ZPT_LODGE scan, and none of the four fallback
events either. Step 1 refuses to guess an origin off a delivery scan, so they abstain rather than
being called VIC, and a parcel with no origin cannot be banded, cannot be sourced, and has no
place in a factor table.

That filter is NOT neutral for this file, and the reason is mechanical: three of the four origin
fallbacks are physical events at a working building, and ZPT_MACHINE_SORT is the strongest of
them. A parcel that never crossed a machine has one fewer chance to be placed. So all 592 land in
the residual, and every one of them is NONE — 592 of 592 have nrounds == 0 and not one was sorted
elsewhere. The NONE group is therefore the one number that MOVES under this filter (5,515 ->
4,923); the 3+ bucket and ELSE keep their article counts and only their percentages shift.

`--all` restores the unfiltered 166,159 so the two can be read against each other.

WHY THE RESIDUAL NEEDS THE RAW FILE ─────────────────────────────────────────────────
`nrounds` counts machine sorts AT THE EIGHT SITES in SORT_SITE, consecutive repeats collapsed.
A parcel with nrounds == 0 has not necessarily gone unsorted, and the reduction keeps the two
cases apart with one flag:

    nrounds == 0 and sorted_elsewhere        ELSE — machine-sorted, at a site we do not model
    nrounds == 0 and not sorted_elsewhere    NONE — no machine-sort scan anywhere in the file

`sorted_elsewhere` is a BOOLEAN. It says a sort happened somewhere else; it does not say where,
because the reduction drops every event whose facility is not in the dictionary. So "which
non-modelled sites?" cannot be answered from the pickle at all — it has to come from the scans,
which is what `residual_events` is for. The pull is cached beside the reduction so the 394 MB
read happens once.

ONE THING TO WATCH in the numbers below: an ELSE parcel sorted at TWO non-modelled sites appears
under both, so the per-facility article column double-counts by design — it answers "how much
volume did this site touch", not "how does the residual split". On this extract that is 64
consignments and therefore 64 articles: the facility column sums to 5,010 against an ELSE total
of 4,946, and `sum(articles * (sites - 1)) == 64` reconciles it exactly.

The STATE line is deduplicated separately and does NOT inherit that overcount, because most of it
never crosses a border: Sydney Parcel Facility + Sydney West LF is two facilities and one state.
Only 12 consignments were sorted in two states, so the state column sums to 4,958. Grouping the
facility-level frame by state instead — the obvious shortcut — silently reports 5,010 there too
and overstates New South Wales by 40 articles it never handled twice.

The residual total is the only figure that is a clean partition.
"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model_input_preparation"))  # the exporter lives there

import pandas as pd

from export_chain2_factors import CACHE, OUT, PHYSICAL, SCAN, SORT_SITE

# The raw events behind ELSE + NONE, cached — ONE FILE PER COHORT, deliberately.
# The first version cached the full residual once and filtered it at load, so that --all and the
# default could share a single read. It was correct and it read wrong: the file on disk held
# 137,777 events while every figure on screen was computed off 134,012 of them, and the rebuild
# line announced the number that matched neither the report above it nor the one below. A cache
# whose size has to be explained is a cache that will be misread. Now each cohort owns its file
# and what is written is exactly what is measured; --all pays for a second read of the scan file,
# which is a fair price for never having to explain the gap again.
RESID = {True:  OUT / "residual_scan_events.pkl",
         False: OUT / "residual_scan_events_all.pkl"}


def load_reduction(eligible=True):
    """The per-parcel reduction, keyed the way the scan file keys it.

    `eligible` applies export_chain2_factors.cohort()'s first test and only that one — the origin
    filter — so the denominator here is the 165,567 the model measures. It deliberately does NOT
    apply DROP_UNPLACED_ENTRY, cohort()'s second cut: that one drops parcels with no sort AND no
    handling scan at a modelled site, which is the NONE group being partly deleted by the very
    property this script exists to describe. Measuring the residual on a cohort that has already
    thrown some of it away would answer a different question quietly.
    """
    p = pd.read_pickle(CACHE)
    p = p.set_index("Consignment_ID") if "Consignment_ID" in p.columns else p
    if not eligible:
        return p
    m = p.origin != "UNKNOWN"
    out = p[~m]
    print(f"  eligible cohort: dropping {out.articles.sum():,} articles "
          f"({out.articles.sum() / p.articles.sum():.2%}) with origin=UNKNOWN — "
          f"{out.articles[out.nrounds == 0].sum() / max(out.articles.sum(), 1):.0%} of them sit "
          f"in the residual, which is what the origin fallback needs a machine sort to place")
    return p[m]


# ══════════════════════════════════════════════════════════════════════════════════════
#  QUESTION 1 — the 3+ bucket
# ══════════════════════════════════════════════════════════════════════════════════════
def report_three_plus(p):
    """How big is "sorted three or more times", and what is it made of?

    Reported by ARTICLE, which is the Sankey's unit, with the consignment count beside it
    because the two disagree: a 3+ parcel is slightly more likely to be part of a multi-article
    consignment, so the article share sits above the consignment share.
    """
    w, tot = p.articles, p.articles.sum()
    m = p.nrounds >= 3
    print(f"\n  THREE OR MORE SORTS — {w[m].sum():,} of {tot:,} articles "
          f"({w[m].sum() / tot:.2%}); {m.sum():,} of {len(p):,} consignments ({m.mean():.2%})")
    dist = w.groupby(p.nrounds.clip(upper=4)).sum()
    print("    the whole distribution: " + "  ".join(
        f"{int(k)}{'+' if k == 4 else ''} {v / tot:5.2%}" for k, v in dist.items())
        + f"   mean {(p.nrounds * w).sum() / tot:.3f} sorts per article")
    deep = w[p.nrounds >= 4].sum()
    print(f"    exactly 3 carries {w[p.nrounds == 3].sum():,} ({w[p.nrounds == 3].sum() / tot:.2%}); "
          f"4 or more is {deep:,} ({deep / tot:.2%}), longest chain {int(p.nrounds.max())}")

    # WITHIN-CLASS RATES, not shares of the bucket. "95.6% of 3+ is Parcel Post" mostly says
    # Parcel Post is 85% of the day; the rate says whether the class is actually more re-sorted.
    print("    rate within class: " + ", ".join(
        f"{c} {w[(p.cls == c) & m].sum() / w[p.cls == c].sum():.2%}"
        for c in w.groupby(p.cls).sum().sort_values(ascending=False).index))
    print("    top paths: " + ", ".join(
        f"{k} {v:,} ({v / w[m].sum():.0%})"
        for k, v in w[m].groupby(p.path[m]).sum().sort_values(ascending=False).head(4).items()))
    print("    rate by delivering depot: " + ", ".join(
        f"{i.replace('PUD_', '')} {r.three / r.day:.1%}"
        for i, r in pd.DataFrame({"three": w[m].groupby(p.pdc[m]).sum(),
                                  "day": w.groupby(p.pdc).sum()}).fillna(0)
                     .sort_values("three", ascending=False).head(5).iterrows()))
    for col, lab in (("first", "first sort"), ("last", "last sort")):
        v = w[m].groupby(p[col][m]).sum().sort_values(ascending=False)
        print(f"    {lab} site: " + ", ".join(f"{k} {x / v.sum():.0%}" for k, x in v.head(4).items()))
    # how the bucket differs from the day — the three flags the model has lanes for
    print("    against the whole day: " + ", ".join(
        f"{lab} {w[m & p[c]].sum() / w[m].sum():.1%} vs {w[p[c]].sum() / tot:.1%}"
        for c, lab in (("crossdocked", "cross-docked"), ("kept_on_site", "kept at the depot"),
                       ("sorted_elsewhere", "also sorted elsewhere"))))
    band = w[m].groupby(p.lodge_band[m]).sum().sort_values(ascending=False)
    print("    lodged: " + ", ".join(f"{k} {v / band.sum():.0%}" for k, v in band.items())
          + "  (the day is " + ", ".join(
              f"{k} {v / tot:.0%}" for k, v in
              w.groupby(p.lodge_band).sum().sort_values(ascending=False).items()) + ")")
    return pd.DataFrame({"articles": w[m].groupby(p.path[m]).sum(),
                         "consignments": p.path[m].value_counts()}).sort_values(
        "articles", ascending=False)


# ══════════════════════════════════════════════════════════════════════════════════════
#  QUESTION 2 — the residual: every scan event behind ELSE and NONE
# ══════════════════════════════════════════════════════════════════════════════════════
def residual_events(p, rebuild=False, eligible=True):
    """Every scan row for every parcel with no sort at the eight sites.

    THIS is the code that answers "give me the scan events for the NONE group". It reads the
    whole scan file once — there is no index on it — keeps the residual consignments, and caches
    the result, which is about 138k rows against the file's 2.96 M.

    Note the column list is WIDER than the reduction's: `Event_type` and `STE_NAME21` are what
    make the answer, since the question is which building in which state did the sorting.
    """
    path = RESID[eligible]
    if not path.exists() or rebuild:
        # ids come from the CALLER'S cohort, so the file written is the file measured
        ids = set(p.index[p.nrounds == 0])
        print(f"  reading {SCAN.name} for {len(ids):,} residual consignments ...")
        df = pd.read_csv(SCAN, dtype=str,
                         usecols=["Consignment_ID", "Event_seq", "Event_type",
                                  "Event_facility_name", "STE_NAME21", "Event_date",
                                  "Event_local_time"])
        ev = df[df.Consignment_ID.isin(ids)].copy()
        ev["Event_seq"] = pd.to_numeric(ev.Event_seq)
        ev = ev.sort_values(["Consignment_ID", "Event_seq"])
        path.parent.mkdir(parents=True, exist_ok=True)
        ev.to_pickle(path)
        print(f"  cached {len(ev):,} events -> {path.name}")
    return scope(p, pd.read_pickle(path))


def scope(p, ev):
    """Prove the cached frame IS this cohort's residual, and nothing else.

    Since each cohort now owns its cache file this is a no-op on a fresh build, which is the
    point: it is the guard that catches a stale file from the other cohort, or a CACHE that has
    been rebuilt under it. It stays a filter as well as an assert so a superset left over from
    an older version degrades to correct-and-slow rather than to wrong.

    The assert is on ARTICLES, because that is the unit every share on screen is quoted in and
    the one that has to reconcile against the residual line. Note that the two counts are NOT
    interchangeable and this is where people trip: the eligible residual is 9,869 ARTICLES
    carried by 9,681 CONSIGNMENTS. A consignment can hold more than one article, so any figure
    matched against the wrong one will be out by about 2%.
    """
    res = p.nrounds == 0
    ids = set(p.index[res])
    ev = ev[ev.Consignment_ID.isin(ids)].copy()
    seen = set(ev.Consignment_ID.unique())
    art = p.articles[p.index.isin(seen)].sum()
    assert art == p.articles[res].sum(), (
        f"residual events cover {art:,} articles, residual is {p.articles[res].sum():,}")
    missing = len(ids - seen)
    print(f"  residual events: {len(ev):,} scans covering {len(seen):,} consignments / "
          f"{art:,} articles — exactly the residual"
          + (f"; {missing:,} residual consignments carry no scan row at all" if missing else ""))
    return ev


def report_elsewhere(p, ev):
    """ELSE — sorted, just not at one of the eight. WHICH sites, and where are they?

    The article column is per FACILITY and double-counts a parcel sorted at two of them; see the
    module docstring. The partition figure is the residual total printed first.
    """
    w, tot = p.articles, p.articles.sum()
    res = p.nrounds == 0
    els, non = res & p.sorted_elsewhere, res & ~p.sorted_elsewhere
    print(f"\n  THE RESIDUAL — no machine sort at the {len(set(SORT_SITE.values()))} modelled "
          f"sites: {w[res].sum():,} articles ({w[res].sum() / tot:.2%})")
    print(f"    ELSE  sorted at a site the model does not carry   {w[els].sum():>7,}  "
          f"{w[els].sum() / tot:6.2%}")
    print(f"    NONE  no machine-sort scan anywhere in the file   {w[non].sum():>7,}  "
          f"{w[non].sum() / tot:6.2%}")

    ms = ev[(ev.Event_type == "ZPT_MACHINE_SORT") & ev.Consignment_ID.isin(set(p.index[els]))].copy()
    ms["articles"] = ms.Consignment_ID.map(w)
    u = ms.drop_duplicates(["Event_facility_name", "Consignment_ID"])
    t = (u.groupby(["Event_facility_name", "STE_NAME21"])
           .agg(consignments=("Consignment_ID", "nunique"), articles=("articles", "sum"))
           .sort_values("articles", ascending=False))
    dup = int(u.articles.sum() - w[els].sum())
    print(f"\n    {len(t)} non-modelled facilities ran those sorts, {len(ms):,} sort events. "
          f"The column below sums to {int(u.articles.sum()):,}, not {int(w[els].sum()):,}: "
          f"{int((ms.groupby('Consignment_ID').Event_facility_name.nunique() > 1).sum())} parcels "
          f"were sorted at two of these sites and are counted under both (+{dup}).")
    for (fac, st), r in t.head(12).iterrows():
        print(f"      {fac[:33]:<34}{st[:22]:<23}{int(r.articles):>7,}  "
              f"{r.articles / w[els].sum():>6.1%}")
    if len(t) > 12:
        print(f"      + {len(t) - 12} more, {int(t.articles[12:].sum()):,} articles between them")
    # deduplicated at STATE level, not carried over from the facility frame — see the docstring
    us = ms.drop_duplicates(["STE_NAME21", "Consignment_ID"])
    st = us.groupby("STE_NAME21").articles.sum().sort_values(ascending=False)
    ns = ms.groupby("Consignment_ID").STE_NAME21.nunique()
    print("    by state: " + ", ".join(f"{k} {int(v):,}" for k, v in st.items())
          + f"   (sums to {int(st.sum()):,}, {int(st.sum()) - int(w[els].sum()):,} over the ELSE "
            f"total — {int((ns > 1).sum())} parcels were sorted in two states)")
    vic = u[u.STE_NAME21 == "Victoria"]
    print(f"    VICTORIAN non-modelled sorts — the only ones the model could plausibly carry: "
          f"{int(vic.articles.sum()):,} articles ({vic.articles.sum() / tot:.2%} of the day) "
          f"across {vic.Event_facility_name.nunique()} sites — "
          + ", ".join(f"{k} {int(v):,}" for k, v in
                      vic.groupby("Event_facility_name").articles.sum()
                         .sort_values(ascending=False).items()))
    return t


def report_none(p, ev):
    """NONE — no machine sort anywhere. So what DID happen to it?

    A parcel has to get from a lodgement to a doorstep somehow, and the answer here is that it
    was worked by hand: the events are loads, departs, unloads and transfers at depots, with the
    sorting machines never touching it. `full_path` shows the shape — a lodgement and a depot,
    often nothing in between.
    """
    w, tot = p.articles, p.articles.sum()
    non = p.nrounds == 0
    non = non & ~p.sorted_elsewhere
    ids = set(p.index[non])
    e = ev[ev.Consignment_ID.isin(ids)].copy()
    e["articles"] = e.Consignment_ID.map(w)
    ph = e[e.Event_type.isin(PHYSICAL)]
    print(f"\n  THE NONE GROUP — {w[non].sum():,} articles ({w[non].sum() / tot:.2%}), "
          f"{non.sum():,} consignments, {len(e):,} scan events "
          f"({len(e) / non.sum():.1f} each), of which {len(ph):,} are physical")
    print("    physical event mix: " + ", ".join(
        f"{k.replace('ZPT_', '')} {v:,}" for k, v in ph.Event_type.value_counts().items()))
    nf = ph.groupby("Consignment_ID").Event_facility_name.nunique()
    print(f"    buildings touched per parcel: median {int(nf.median())}, "
          + ", ".join(f"{int(k)} bldg {v / len(nf):.0%}" for k, v in
                      nf.value_counts().sort_index().head(4).items()))
    u = ph.drop_duplicates(["Event_facility_name", "Consignment_ID"])
    t = (u.groupby("Event_facility_name")
           .agg(consignments=("Consignment_ID", "nunique"), articles=("articles", "sum")))
    t["modelled_sort_site"] = [i in SORT_SITE for i in t.index]
    t = t.sort_values("articles", ascending=False)
    print(f"    it passed through {len(t)} distinct buildings; the busiest:")
    for fac, r in t.head(10).iterrows():
        print(f"      {fac[:38]:<40}{int(r.articles):>7,}"
              f"   {'modelled sort site' if r.modelled_sort_site else ''}")
    m = t.groupby("modelled_sort_site").articles.sum()
    print(f"    article-touches at a modelled sort site {int(m.get(True, 0)):,}, "
          f"elsewhere {int(m.get(False, 0)):,} — so most of this freight IS in our buildings, "
          f"it just never went across a machine there")
    # the profile: what makes a parcel skip the machines
    print("    lodged: " + ", ".join(
        f"{k} {v / w[non].sum():.0%}" for k, v in
        w[non].groupby(p.lodge_band[non]).sum().sort_values(ascending=False).items())
        + "  (the day is " + ", ".join(
            f"{k} {v / tot:.0%}" for k, v in
            w.groupby(p.lodge_band).sum().sort_values(ascending=False).items()) + ")")
    print(f"    kept at the depot {w[non & p.kept_on_site].sum() / w[non].sum():.1%} "
          f"vs {w[p.kept_on_site].sum() / tot:.1%} for the day")
    print("    commonest whole journeys:")
    for k, v in w[non].groupby(p.full_path[non]).sum().sort_values(ascending=False).head(6).items():
        print(f"      {str(k)[:74]:<76}{int(v):>6,}")
    return t


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rebuild", action="store_true", help="re-read the scan file")
    ap.add_argument("--csv", action="store_true", help="write the three tables beside the cache")
    ap.add_argument("--all", action="store_true",
                    help="the unfiltered extract, origin=UNKNOWN included")
    a = ap.parse_args()

    p = load_reduction(eligible=not a.all)
    print(f"  reduction: {len(p):,} consignments, {p.articles.sum():,} articles "
          f"({CACHE.name})")
    paths = report_three_plus(p)
    ev = residual_events(p, a.rebuild, eligible=not a.all)
    elsew = report_elsewhere(p, ev)
    none = report_none(p, ev)

    if a.csv:
        for name, t in (("three_plus_paths", paths), ("residual_sorted_elsewhere", elsew),
                        ("residual_no_sort_facilities", none)):
            t.to_csv(OUT / f"{name}.csv")
            print(f"  wrote {(OUT / f'{name}.csv').name}")


if __name__ == "__main__":
    main()
