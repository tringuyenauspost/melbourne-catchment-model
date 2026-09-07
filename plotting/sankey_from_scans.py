"""Sankey of what the SCANS say happened.  ── the picture, and nothing else ──

    IN   the reduction in export_chain2_factors.py (cached per-parcel rows)
    OUT  outputs/melbourne_scan_path_analysis/sankey-from-scans.html

The reduction itself — raw scans to one row per parcel, and every rule about what an event
MEANS — lives in `export_chain2_factors.py`, which is also what the model is built from. This
module only draws it, so the diagram and the model can never disagree about the numbers.

The other one is `sankey_from_optilogic.py`, which draws what the SOLVER decided.

Every parcel delivered by a Melbourne PDC on one day, traced from lodgement through each machine
sort to the depot that delivered it. Volumes are ARTICLES (``Article_count``), the same unit as
the model's demand table — not consignments, and over EVERY DELIVERY DATE the extract covers,
which is the basis every model factor is measured on (OBS_COHORT=all_dates).

Run:  uv run python plotting/sankey_from_scans.py
      uv run python plotting/sankey_from_scans.py --rebuild     # ignore the cache, re-read the CSV"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))                # the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utilities"))               # ad-hoc tools
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model_input_preparation"))  # the reduction lives there
import argparse
import json
from pathlib import Path

import numpy as np

from export_chain2_factors import (  # the reduction — see the note above
    CLASSES, CONTRACTOR_BASE, DEPOT_SORT_EVENTS, HANDLED, HERE, KEPT_REQUIRE_SORT, LODGE_PUD,
    METRO_BOUNDARY, MODEL_HUBS, OUT, PDC_BASIS, SORT_CAPABLE_PUDS, SORT_ORDER,
    VIC_UNPLACED_BAND, depot_label, load_paths, read_pdc_basis,
)

TARGET = OUT / "sankey-from-scans.html"
MATRIX_DIR = OUT / "stage_matrices"
# ── THE UNPLACED FREIGHT — ONE PAGE NOW (Change 40, retired 2026-08-21) ──────────────
# There used to be a second rendering, "cleaned", which dropped every parcel whose ENTRY SITE
# could not be evidenced, on the grounds that it was the model's view of the day against the
# extract's. It is gone. Change 39 set DROP_UNPLACED_ENTRY=False in the exporter, so the model
# now KEEPS that freight and places it by rule; the cleaned cut's 160,902 stopped being the
# model's cohort, and a second page claiming to be the model while disagreeing with it is worse
# than no second page. This one draws 165,567 — the exporter's cohort exactly.
#
# The reduction still decides an entry site three ways (see export_chain2_factors.cohort):
#   1. it was SORTED at one of the eight modelled sites          — measured
#   2. it was not, but was HANDLED at one of them                — measured, weaker but real
#   3. neither, so the site is ASSUMED from the delivering depot or the class's big hub
# Rung 3 is 4,665 EA that no scan places anywhere we model, and `unplaced` still identifies it.
# Here they are VISIBLE — they arrive at NONE or ELSE, and the reader can see how much of the
# picture is not evidence. In the model they are invisible, because obs_joint has no such node
# and they land in an ordinary cell. That asymmetry is the thing to remember when reading a
# stage matrix against factors_observed; see the note in write_matrices.
#
# `first` and `received` both come from SORT_SITE, so "neither is present" IS rung 3.
def unplaced(q):
    return q["first"].isna() & q.received.isna()
PATHS_CSV = OUT / "consignment_full_paths.csv"     # one row per consignment, --csv
FREQ_CSV  = OUT / "full_path_frequency.csv"        # journeys ranked by volume, --csv


def flows(sub):
    """The four link stages. Entry and exit share an axis, so a single sort draws flat.

    THE HANDLED COLUMN IS GONE (2026-08-21). It used to sit between the source band and the first
    sort, and it was the single biggest source of lane double-counting: a truck running MPF ->
    Bayswater was drawn once as "handled at MPF, first sorted at Bayswater" and again as "first
    sorted at Bayswater, sorted again at MPF". 140 diagonal ribbons over the sort columns were 55
    real lanes; dropping this column takes that to 103. The remaining 48 duplicates are the
    despatch column redrawing the sort-1 -> sort-2 pairs, which is a separate decision.

    WHAT IT COST, kept honest: the CROSS-DOCK — handled at one building, first sorted at another
    — is 17,519 articles, 10.9% of the day, and the model builds it (BOM_SORT_XD). It is no
    longer a step you can see, so it stays as a headline STAT, fed from `headline()` rather than
    measured off the ribbons. See the note beside the stats tile."""
    # STAGE 0 IS SPLIT BY CROSS-DOCK, which is how the handled column comes back as a COLOUR
    # rather than as a step. Each (source band -> first sort) pair emits up to two ribbons: the
    # freight first sorted in the building that handled it, and the freight that was worked at
    # one building and sorted at another. They stack in the same slot, so the picture keeps its
    # shape and gains a band. `x` is the flag the page colours on.
    f0 = sub.groupby(["org", "entry", "xdock"]).articles.sum()
    # ...and so does the SECOND-SORT stage, on the same flag, so the cross-docked cohort can
    # be followed past its first sort. Only 1,748 of the 51,496 second sorts are cross-docked
    # freight — 3.4% — which is invisible while the whole stage is one colour.
    f2 = sub.groupby(["entry", "mid", "xdock"]).articles.sum()
    f3 = sub.groupby(["mid", "exit"]).articles.sum()
    f4 = sub.groupby(["exit", "pdc_label"]).articles.sum()
    return ([{"s": "ORG_" + a, "t": "E_" + b, "v": int(v), "x": int(x)}
             for (a, b, x), v in f0.items() if v > 0]
            + [{"s": "E_" + a, "t": "M_" + b, "v": int(v), "x": int(x)}
               for (a, b, x), v in f2.items() if v > 0]
            + [{"s": "M_" + a, "t": "X_" + b, "v": int(v)} for (a, b), v in f3.items() if v > 0]
            + [{"s": "X_" + a, "t": "P_" + b, "v": int(v)} for (a, b), v in f4.items() if v > 0])


# How many times the parcel was sorted in Melbourne, as a filter. "0" is not an error case — it is
# freight with no machine-sort scan at any of the eight sites, and it is 5.7% of the day.
SORT_BUCKETS = ("all", "0", "1", "2", "3+")


def sort_bucket(sub, b):
    """The sort-count filter, on `nrounds` — sorts at the eight modelled sites.

    The zero bucket is real and is kept: 3.2% of the day carries no machine sort at any of them.
    (The retired "cleaned" cut used to clip this to 1 and drop the button, because that page
    redrew those parcels as sorted once at the building that handled them. There is one page
    now and it draws what the scans say, so the measurement and the picture agree again.)
    """
    if b == "all":
        return sub
    return sub[sub.nrounds >= 3] if b == "3+" else sub[sub.nrounds == int(b)]


def sortmix(sub):
    """The sort-count distribution, and the actual routes behind the 2- and 3-sort volume.

    The diagram can only draw the FIRST and LAST sort, so a three-sort parcel hides its middle
    site. These tables carry the whole chain, which is the only place the middle is visible.
    """
    tot = max(int(sub.articles.sum()), 1)

    def top(m, n=8):
        s = sub[m]
        t = s.groupby("path").articles.sum().sort_values(ascending=False).head(n)
        st = max(int(s.articles.sum()), 1)
        return [[k, int(v), round(100 * v / st, 1)] for k, v in t.items()]

    return dict(
        dist=[[lab, int(sub[m].articles.sum()), round(100 * sub[m].articles.sum() / tot, 2),
               int(m.sum())]
              for lab, m in (("0", sub.nrounds == 0), ("1", sub.nrounds == 1),
                             ("2", sub.nrounds == 2), ("3", sub.nrounds == 3),
                             ("4+", sub.nrounds >= 4))],
        mean=round(float((sub.nrounds * sub.articles).sum() / tot), 2),
        two=top(sub.nrounds == 2), three=top(sub.nrounds >= 3),
        two_art=int(sub[sub.nrounds == 2].articles.sum()),
        three_art=int(sub[sub.nrounds >= 3].articles.sum()),
        second=second_sites(sub),
    )


def second_sites(sub):
    """Where the second sort happens, split by whether a third one follows.

    Sites are listed in the fixed SORT_ORDER so the table does not reshuffle under a filter.
    """
    rep = sub[sub.nrounds >= 2]
    two, three = rep[rep.nrounds == 2], rep[rep.nrounds >= 3]
    tot = max(int(rep.articles.sum()), 1)
    out = []
    for site in SORT_ORDER:
        a2 = int(two[two.second == site].articles.sum())
        a3 = int(three[three.second == site].articles.sum())
        if a2 or a3:
            out.append([site, a2, a3, a2 + a3, round(100 * (a2 + a3) / tot, 1)])
    return out


def headline(sub, isub):
    """The stat row. `isub` is interstate volume that was sorted in Melbourne at all.

    Both denominators are floored at 1: the sort-count filter can empty a slice (interstate
    freight with no Melbourne sort has no hub statistics to report), and a zero-division there
    would take out the whole build for a bucket nobody is looking at.
    """
    tot = max(int(isub.articles.sum()), 1)
    all_art = max(int(sub.articles.sum()), 1)
    hub_sorted = isub[isub["first"].isin(MODEL_HUBS)].articles.sum()
    pdc_sorted = isub[isub["first"].isin(["SWP", "MNP", "BAY"])]
    crossdock = pdc_sorted[pdc_sorted.hub_first].articles.sum()   # hub_first is handling-based
    return dict(
        articles=int(sub.articles.sum()),
        # off `origin`, not the band: this is a lodgement fact and must not move when kept volume
        # is split out into its own source band
        vic=round(100 * sub[sub.origin == "VIC"].articles.sum() / all_art, 1),
        # the size of the KEPT band, on the one basis the page shows: every delivery date
        keptband=round(100 * sub[sub.kept_on_site].articles.sum() / all_art, 1),
        # where in Victoria it was lodged — the two bands the diagram draws apart. Off
        # `lodge_band`, which is pure geography, so these do not move when kept volume is split
        # out into its own source band.
        metro=round(100 * sub[sub.lodge_band == "METRO"].articles.sum() / all_art, 1),
        region=round(100 * sub[sub.lodge_band == "REGION"].articles.sum() / all_art, 1),
        # the KEPT PILE split by lodgement. Note this is the whole pile, not the kept source
        # BAND: only metro-lodged stock is drawn as a source band, so kept_int and kept_region
        # are stock that slept at the depot and is nonetheless drawn under INT and REGION.
        # Shares are of the kept pile, so they read as "of what slept here".
        kept_int=round(100 * sub[sub.kept_on_site & (sub.lodge_band == "INT")].articles.sum()
                       / max(sub[sub.kept_on_site].articles.sum(), 1), 1),
        kept_metro=round(100 * sub[sub.kept_on_site & (sub.lodge_band == "METRO")].articles.sum()
                         / max(sub[sub.kept_on_site].articles.sum(), 1), 1),
        kept_region=round(100 * sub[sub.kept_on_site & (sub.lodge_band == "REGION")].articles.sum()
                          / max(sub[sub.kept_on_site].articles.sum(), 1), 1),
        same_depot=round(100 * sub[sub.same_depot_end_to_end].articles.sum() / all_art, 2),
        one=round(100 * isub[isub.nrounds == 1].articles.sum() / tot, 1),
        moved=round(100 * isub[isub.crossdocked].articles.sum() / tot, 1),
        mean=round(float((isub.nrounds * isub.articles).sum() / tot), 2),
        hubsort=round(100 * hub_sorted / tot, 1),
        xdock=round(100 * crossdock / tot, 1),
    )


def _depot_names():
    """PUD -> every facility name that IS that depot, for reading `full_path`."""
    own = {}
    for fac, pud in LODGE_PUD.items():
        own.setdefault(pud, set()).add(fac)
    return own


def _contractor_presence(q):
    """For a depot whose rounds run from a contractor's base: where is its freight, physically?

    The claim the CONTRACTOR_BASE pairing rests on is that the base is where the work happens and
    the depot's own building is incidental to it. Read off `full_path` rather than asserted, and
    per pairing, so a second arrangement would report itself rather than inherit these numbers.
    """
    own, out = _depot_names(), {}
    for pud, base in CONTRACTOR_BASE.items():
        d = q[q.pdc == pud]
        if not len(d):
            continue
        paths = [set(str(x).split(" > ")) for x in d.full_path]
        at = lambda names: int(d.articles[[bool(p & names) for p in paths]].sum())
        out[pud] = (at(own.get(base, set())), at(own.get(pud, set())), int(d.articles.sum()))
    return out


def _through_depot(q):
    """Of the volume handed over at a NON-depot site, how much passed through its depot first?

    The fallback in the hybrid basis rests on this: if a parcel collected from an LPO never went
    near the depot the plan names, charging that depot for it would be an assumption. It did.
    Read off `full_path`, which is every building the parcel physically touched, so a depot counts
    only if one of ITS names appears — the same dictionary the reduction maps arrivals with.
    """
    own = _depot_names()
    off = q[q.deliver_fac.notna() & q.pdc_deliver.isna()]
    hit = [any(f in own.get(pud, ()) for f in str(path).split(" > "))
           for pud, path in zip(off.pdc, off.full_path)]
    return int(off.articles[hit].sum()), int(off.articles.sum())


def prose(q):
    """Every figure quoted in the page's own words, computed rather than typed.

    The commentary under the diagram used to carry its numbers as literals, which is fine until
    a rule changes and the picture and the paragraph beside it stop agreeing. Change 34 changed
    two rules at once, so the paragraphs now read their numbers from the same frame the ribbons
    are drawn from and there is nothing left to update by hand.
    """
    b = read_pdc_basis()
    tot = int(q.articles.sum())
    kept = q[q.kept_on_site]
    ka = int(kept.articles.sum())
    n = lambda v: f"{int(v):,}"
    pct = lambda v, d: f"{100 * v / max(d, 1):.1f}%"

    per = (q.groupby("pdc").articles.sum().to_frame("tot")
            .join(kept.groupby("pdc").articles.sum().to_frame("kept")).fillna(0))
    per["share"] = 100 * per.kept / per.tot
    # split by whether the depot HAS a sorter, not by whether it ended up with volume — the
    # two groups are measured under different rules and the page has to say which is which
    has = per.index.isin(SORT_CAPABLE_PUDS)
    live = per[has].sort_values("share", ascending=False)
    dead = per[~has].sort_values("share", ascending=False)

    _cp = _contractor_presence(q)
    band = lambda t: int(kept[kept.lodge_band == t].articles.sum())
    ki, km, kr = band("INT"), band("METRO"), band("REGION")
    # only depots carrying a material share of the stage — a depot with a handful of staged
    # parcels produces a 0% or 100% mix off a denominator of nothing, and quoting that as the
    # bottom of the range says something about sample size, not about the depot
    mix = (kept.groupby("pdc")
               .apply(lambda g: 100 * g[g.lodge_band == "INT"].articles.sum()
                      / max(g.articles.sum(), 1), include_groups=False)
               .sort_values(ascending=False))
    mix = mix[per.kept.reindex(mix.index).fillna(0) >= 0.01 * ka]

    return {
        "{{TRACED}}": n(tot),
        "{{EXTRACT}}": n(b.get("extract", tot)),
        "{{LOST}}": n(b.get("lost", 0)),
        "{{FROMSCAN}}": n(b.get("deliver_kept", 0)),
        "{{FROMSCANPCT}}": pct(b.get("deliver_kept", 0), b.get("extract", tot)),
        "{{LOSTPCT}}": pct(b.get("lost", 0), b.get("extract", tot)),
        "{{LOSTNOSCAN}}": n(b.get("lost_no_scan", 0)),
        "{{LOSTOFF}}": n(b.get("lost_off_network", 0)),
        "{{OFFNAMES}}": n(b.get("off_network_names", 0)),
        "{{MOVED}}": n(b.get("moved", 0)),
        "{{MOVEDPCT}}": pct(b.get("moved", 0), b.get("extract", tot)),
        "{{DISAGREE}}": n(b.get("disagree", 0)),
        "{{HELD}}": n(b.get("contractor_held", 0)),
        "{{PAIRS}}": ", ".join(f"<strong>{a}</strong> under <strong>{c}</strong>"
                               for a, c in b.get("contractor_pairs", [])),
        "{{CONTRACTOR}}": ", ".join(a for a, _ in b.get("contractor_pairs", [])) or "none",
        "{{CONTRACTOREA}}": n(sum(per.tot.get("PUD_" + a.replace(" ", "_"), 0)
                                  for a, _ in b.get("contractor_pairs", []))),
        "{{ATBASEPCT}}": ", ".join(pct(v[0], v[2]) for v in _cp.values()),
        "{{ATOWNPCT}}": ", ".join(pct(v[1], v[2]) for v in _cp.values()),
        "{{TOPMOVE}}": " &middot; ".join(f"{a} &rarr; {c} {n(v)}"
                                         for a, c, v in b.get("top_moves", [])[:3]),
        "{{DEPOTSLOST}}": ", ".join(b.get("depots_lost", [])) or "none",
        "{{KEPTEA}}": n(ka),
        "{{KEPTPCT}}": pct(ka, tot),
        "{{KEPTLIVE}}": ", ".join(f"<strong>{depot_label(i)} {r.share:.1f}%</strong>"
                                  for i, r in live.iterrows()),
        "{{KEPTDEAD}}": ", ".join(f"<strong>{depot_label(i)} {r.share:.1f}%</strong>"
                                  for i, r in dead.iterrows()),
        "{{NLIVE}}": str(len(live)), "{{NDEAD}}": str(len(dead)),
        "{{KEPTLIVEEA}}": n(live.kept.sum()), "{{KEPTDEADEA}}": n(dead.kept.sum()),
        "{{KEPTINTPCT}}": pct(ki, ka), "{{KEPTINTEA}}": n(ki),
        "{{KEPTMETROPCT}}": pct(km, ka), "{{KEPTMETROEA}}": n(km),
        "{{KEPTREGIONPCT}}": pct(kr, ka), "{{KEPTREGIONEA}}": n(kr),
        "{{BOUNDARY}}": METRO_BOUNDARY.name, "{{UNPLACED}}": VIC_UNPLACED_BAND,
        "{{KEPTMIX}}": ", ".join(f"<strong>{depot_label(i)} {v:.1f}%</strong>"
                                 for i, v in mix.head(3).items())
                       + (f", down to <strong>{depot_label(mix.index[-1])} "
                          f"{mix.iloc[-1]:.1f}%</strong>" if len(mix) > 4 else ""),
        "{{SORTEVENTS}}": ", ".join(e.replace("ZPT_", "") for e in DEPOT_SORT_EVENTS),
        # the load-bearing number under the fallback: freight handed over at a post office had
        # already been through the depot we are charging it to, so the plan is not a guess there
        "{{FELLTHROUGH}}": pct(*_through_depot(q)),
    }


def write_path_tables(q, min_articles=1):
    """The full journey as CSV — per consignment, and aggregated by journey.

    `full_path` is every building the parcel physically touched, in order, consecutive repeats
    collapsed. `chain` is the narrow Melbourne-machine-sort view the model's nodes correspond to;
    both are written so the two can be compared row by row.
    """
    cols = ["Consignment_ID", "cls", "articles", "origin", "lodge_fac", "pdc",
            "n_facilities", "full_path", "path", "received", "first", "last",
            "nrounds", "crossdocked", "same_depot_end_to_end", "kept_on_site", "hours_at_depot",
            "delivery_date", "delivered_on_peak_day", "source_band"]
    per = q[cols].rename(columns={"cls": "product_class", "pdc": "terminating_depot",
                                  "path": "melbourne_sort_chain", "first": "first_sorted_at",
                                  "last": "despatched_from", "received": "handled_at",
                                  "nrounds": "melbourne_sorts"})
    per.to_csv(PATHS_CSV, index=False, encoding="utf-8-sig")
    print(f"  ✓ {PATHS_CSV.name:<32} {len(per):>7,} rows  "
          f"({PATHS_CSV.stat().st_size / 1e6:.0f} MB)")

    freq = (q.groupby("full_path")
              .agg(articles=("articles", "sum"), consignments=("articles", "size"),
                   facilities=("n_facilities", "first"))
              .sort_values("articles", ascending=False).reset_index())
    freq["share_pct"] = (100 * freq.articles / q.articles.sum()).round(4)
    kept = freq[freq.articles >= min_articles]
    kept.to_csv(FREQ_CSV, index=False, encoding="utf-8-sig")
    print(f"  ✓ {FREQ_CSV.name:<32} {len(kept):>7,} rows"
          + (f"  (of {len(freq):,}, filtered to >= {min_articles} articles)"
             if min_articles > 1 else "")
          + f"  covering {kept.articles.sum() / freq.articles.sum():.1%} of volume")
    print(f"    the tail is long: {len(freq):,} distinct journeys, "
          f"top 12 are only {freq.head(12).share_pct.sum():.0f}% of volume")


# The diagram's stages, and the only definition of them — the handled column left on 2026-08-21
# and the matrices followed it, because a matrix the picture does not draw is a matrix nothing
# checks.
STAGES = [("stage1_source_to_firstsort", "org", "entry"),
          ("stage2_firstsort_to_secondsort", "entry", "mid"),
          ("stage3_secondsort_to_despatch", "mid", "exit"),
          ("stage4_despatch_to_depot", "exit", "pdc_label")]


def write_matrices(q, stats):
    """The diagram's four stages, as matrices, with a share beside every count.

    The Sankey draws these and the model consumes the same joins, so writing them out is what
    makes the picture checkable against `inputs/factors_observed/` rather than merely plausible.
    One CSV per stage, long form (from, to, articles, share_of_from), plus a class-split copy so
    a per-class share can be read without re-deriving it.

    THE BASIS MOVED when the "cleaned" page was retired (2026-08-21). These used to be written
    off that cut — 160,902 articles, every one of them entering at a building a scan could put it
    in. They are now written off the only page there is, which is 165,567 and lets NONE and ELSE
    stand as entry nodes. Two consequences worth knowing before comparing a cell against
    factors_observed:

      * the volume matches the exporter's cohort exactly, which the cleaned cut no longer did
        once Change 39 set DROP_UNPLACED_ENTRY=False;
      * but NONE and ELSE now appear as rows, where `cohort()` places that same freight at a
        real building by rule. So the entry column agrees on TOTAL and disagrees on 5.96% of
        its placement, and that 5.96% is exactly the residual the manual-sort floor is measured
        from. It is a difference of convention, not of measurement.
    """
    MATRIX_DIR.mkdir(parents=True, exist_ok=True)
    # THE FOLDER IS OWNED, NOT APPENDED TO. Renaming a stage (the handled column left in 2026-08-21)
    # otherwise leaves the old CSV sitting beside the new one, and stage2_handled_to_firstsort.csv
    # reads exactly like a current measurement — same shape, same columns, just describing a
    # diagram nobody draws any more. Sweep anything this run did not write.
    _keep = {f"{n}{sfx}.csv" for n, _, _ in STAGES for sfx in ("", "_by_class")}
    _stale = sorted(p for p in MATRIX_DIR.glob("stage*.csv") if p.name not in _keep)
    for p in _stale:
        p.unlink()
    print("\n  === STAGE MATRICES ===")
    if _stale:
        print(f"    removed {len(_stale)} stale CSV(s) from an earlier column set: "
              + ", ".join(p.name for p in _stale[:3])
              + (f", +{len(_stale) - 3} more" if len(_stale) > 3 else ""))
    print(f"    {'stage':<34}{'from':>6}{'to':>5}{'lanes':>7}{'articles':>11}   biggest lane")
    for name, a, b in STAGES:
        g = q.groupby([a, b]).articles.sum().reset_index()
        g.columns = ["from", "to", "articles"]
        tot_from = g.groupby("from").articles.transform("sum")
        g["share_of_from"] = (g.articles / tot_from).round(6)
        g = g.sort_values("articles", ascending=False)
        g.to_csv(MATRIX_DIR / f"{name}.csv", index=False)
        byc = (q.groupby([a, b, "cls"]).articles.sum().reset_index()
                 .rename(columns={a: "from", b: "to"}))
        byc.to_csv(MATRIX_DIR / f"{name}_by_class.csv", index=False)
        top = g.iloc[0]
        print(f"    {name:<34}{g['from'].nunique():>6}{g['to'].nunique():>5}{len(g):>7}"
              f"{int(g.articles.sum()):>11,}   {top['from']} -> {top['to']} "
              f"{int(top.articles):,} ({top.share_of_from:.0%})")
    st = stats["ALL"]["all"]
    print(f"    every stage totals {int(q.articles.sum()):,} articles — conservation asserted above")
    print(f"    wrote {len(STAGES) * 2} CSVs to {MATRIX_DIR.relative_to(HERE)}/")
    # the numbers the model is most sensitive to, so a regression is visible here. hubsort,
    # xdock and one are INTERSTATE-scoped in headline() — they are denominated on interstate
    # volume that was sorted in Melbourne at all — so they are labelled as such rather than
    # read as whole-of-day figures.
    w = q.articles
    print(f"    whole day : sorts/parcel {(q.nrounds * w).sum() / w.sum():.2f} | "
          f"sorted once {w[q.nrounds == 1].sum() / w.sum():.1%} | "
          f"never sorted at a modelled site {w[q.nrounds == 0].sum() / w.sum():.1%} "
          f"(drawn as NONE or ELSE — the cleaned cut used to redraw them as ONCE at the "
          f"building that handled them; there is no cleaned cut now)")
    print(f"    interstate: first sort at a hub {st['hubsort']:.1f}% | cross-dock "
          f"{st['xdock']:.1f}% | sorted once {st['one']:.1f}%  (denominator = interstate "
          f"volume sorted in Melbourne)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rebuild", action="store_true",
                    help="ignore the cached reduction and re-read the scan CSV")
    ap.add_argument("--csv", action="store_true",
                    help="also write the full-path tables: one row per consignment, and the "
                         "same journeys aggregated and ranked by volume")
    ap.add_argument("--csv-min-articles", type=int, default=1, metavar="N",
                    help="with --csv, keep only journeys carrying at least N articles "
                         "(default 1 = every journey; the tail is very long)")
    args = ap.parse_args()

    paths = load_paths(args.rebuild)
    # one exclusion only: no lodgement scan means we cannot say where it started, and assigning
    # it would bias the very local-vs-interstate number this exists to test
    q_all = paths[paths.origin != "UNKNOWN"].copy()
    _u = unplaced(q_all)
    print(f"  {q_all.articles[_u].sum():,} articles "
          f"({q_all.articles[_u].sum() / q_all.articles.sum():.2%}) have no sort AND no handling at "
          f"any modelled site; they are DRAWN, as NONE, rather than dropped — export_chain2_factors "
          f"keeps them too now (DROP_UNPLACED_ENTRY=False)")
    stats = render(q_all, paths, args)
    write_matrices(q_all, stats)


def render(q, paths, args):
    target = TARGET
    # "NONE" in the sort columns splits two ways: ELSE was machine-sorted, just not at one of the
    # eight sites (85% of it interstate); NONE has no machine-sort scan anywhere in the file.
    unsorted = np.where(q.sorted_elsewhere, "ELSE", "NONE")
    q["recv"] = q.received.fillna("NONE")
    # ONE PAGE, and it draws what the scans say: NONE and ELSE stand as their own nodes so the
    # reader can see how much of the picture is not a modelled sort. There used to be a second,
    # "cleaned" rendering that applied the reduction's rescue rule and dropped what the rescue
    # could not place, on the grounds that it was the MODEL's view of the day. It no longer is:
    # export_chain2_factors now keeps that freight (DROP_UNPLACED_ENTRY=False, Change 39) and
    # places it by rule, so the cleaned cut's 160,902 stopped matching the model's cohort. Two
    # pages that disagree about which is the model is worse than one page that says what it is.
    q["entry"] = np.where(q["first"].notna(), q["first"], unsorted)
    # A parcel sorted once must NOT be drawn passing through a second-sort site — that is
    # indistinguishable from a genuine second sort there. It gets its own node instead, so the
    # column reads: this much was sorted again here, this much never was.
    # the cross-dock, as a column so `flows` can band on it: handled at one modelled site and
    # first sorted at another. False wherever there is no first sort at all — those articles
    # arrive at NONE/ELSE and are drawn grey, because "not cross-docked" and "no evidence either
    # way" are different claims and only one of them is measured.
    q["xdock"] = q.crossdocked.fillna(False).astype(bool)
    q["mid"] = np.where(q.nrounds >= 2, q["second"].fillna("NONE"),
                        np.where(q.nrounds == 1, "ONCE", unsorted))
    q["exit"] = np.where(q["last"].notna(), q["last"], unsorted)
    # The source bands, which is the question the model actually asks: where does a day's delivery
    # volume come from? Four of them since Change 36, and they are deliberately not symmetric:
    # interstate is ONE band whether or not it slept at the depot, Victorian freight splits
    # METRO/REGION on whether its lodgement point falls inside the dissolved first-mile catchment,
    # and only the metro side carries a kept-at-depot band. Kept-at-depot is not a lodgement
    # origin — the parcel was lodged somewhere and travelled here on an earlier day — but for
    # metro freight it is the model's third source (the overnight stage), so it is drawn as one.
    # The column comes from the reduction (`source_band`) so the diagram and the factors band the
    # day identically. Anything measured off true lodgement uses `origin` or `lodge_band`.
    q["org"] = q.source_band
    q["pdc_label"] = q.pdc.map(depot_label)

    # ── ONE BASIS: every delivery date in the extract ─────────────────────────────────
    # The page used to offer a second cohort — only the parcels whose delivery scan is dated on
    # the extract's own delivery day — and let the reader switch. It no longer does. The whole
    # file IS the day: `all_dates` is what OBS_COHORT is set to and what every model factor is
    # measured on, so the diagram now shows exactly that and nothing else. One number per box,
    # no toggle to get wrong.
    data, stats = {}, {}
    for key in ("ALL",) + CLASSES:
        base = q if key == "ALL" else q[q.cls == key]
        data[key], stats[key] = {}, {}
        for b in SORT_BUCKETS:
            sub = sort_bucket(base, b)
            # interstate by LODGEMENT, not by band — a kept parcel is still interstate freight,
            # and every hub/sort statistic below would shift if the KEPT band carved volume
            # out of it
            isub = sub[(sub.origin == "INTERSTATE") & (sub.nrounds > 0)]
            data[key][b] = flows(sub)
            stats[key][b] = headline(sub, isub)

    # conservation: every stage must carry the same article total, or a stage lost volume
    for key, buckets in data.items():
        for b, links in buckets.items():
            by_stage = {}
            for lk in links:
                by_stage.setdefault(lk["s"].split("_")[0], 0)
                by_stage[lk["s"].split("_")[0]] += lk["v"]
            want = stats[key][b]["articles"]
            assert set(by_stage.values()) == {want}, \
                f"{key}/{b}: stages do not balance — {by_stage}, expected {want}"
    # the cross-dock band must partition stage 0, not overlap it
    for key, buckets in data.items():
        for b, links in buckets.items():
            s0 = [lk for lk in links if lk["s"].startswith("ORG_")]
            assert sum(lk["v"] for lk in s0) == stats[key][b]["articles"], \
                f"{key}/{b}: the source stage no longer totals the day"
    # the buckets must also partition the whole: every article sits in exactly one of them
    for key in data:
        parts = sum(stats[key][b]["articles"] for b in SORT_BUCKETS if b != "all")
        assert parts == stats[key]["all"]["articles"], \
            f"{key}: sort buckets sum to {parts:,}, not {stats[key]['all']['articles']:,}"

    if args.csv:
        write_path_tables(q, args.csv_min_articles)

    payload = json.dumps({"flows": data, "stats": stats}, separators=(",", ":"))
    body = BODY
    for token, value in prose(q).items():
        body = body.replace(token, value)
    assert "{{" not in body, f"unfilled prose token: {body[body.index('{{'):][:40]}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(CSS + body + "<script>const DATA=" + payload + ";</script>\n"
                      + "<script>" + JS + "</script>\n")
    print()
    print(f"  full chain: {paths.n_facilities.mean():.1f} facilities per consignment "
          f"(median {int(paths.n_facilities.median())}, p95 {int(paths.n_facilities.quantile(.95))})")
    total = int(q.articles.sum())
    print(f"  every stage balances at {total:,} articles "
          f"({100 * total / paths.articles.sum():.1f}% of the file)")
    for key in ("ALL",) + CLASSES:
        st = stats[key]["all"]
        print(f"    {key:<4} {st['articles']:>8,} articles | {st['vic']:>5.1f}% lodged in Vic "
              f"| metro {st['metro']:>5.1f}% | region {st['region']:>4.1f}% "
              f"| kept {st['keptband']:>4.1f}% ({st['kept_int']:.0f}% int / "
              f"{st['kept_metro']:.0f}% metro / {st['kept_region']:.0f}% region) "
              f"| hub-sorted {st['hubsort']:>5.1f}% | cross-docked {st['xdock']:>5.1f}%")
    # not drawn on the page any more, but the console is where these get checked against a run
    mix = sortmix(q)
    print("  sorts per article: "
          + "  ".join(f"{lab}={pct}%" for lab, _, pct, _ in mix["dist"])
          + f"  mean {mix['mean']}")
    print(f"  wrote {target.relative_to(HERE)}  ({target.stat().st_size / 1024:.0f} KB)")
    return stats


CSS = '<title>Melbourne parcel paths — 20 May 2026</title>\n<style>\n:root{\n  color-scheme:light;\n  --bg:#f6f7f9; --panel:#ffffff; --panel-2:#eef1f5; --line:#dde2e9; --line-soft:#e8ecf1;\n  --ink:#0e1116; --ink-2:#4d5766; --ink-3:#7b8593;\n  --hub:#2a78d6; --pdc:#eb6834; --other:#b0b9c4; --xd:#1baf7a;\n  --accent:#2a78d6; --shadow:0 1px 2px rgba(14,17,22,.06),0 8px 24px -12px rgba(14,17,22,.14);\n}\n@media (prefers-color-scheme:dark){\n  :root:where(:not([data-theme="light"])){\n    color-scheme:dark;\n    --bg:#14171c; --panel:#191d24; --panel-2:#202631; --line:#2b323d; --line-soft:#232935;\n    --ink:#eef1f5; --ink-2:#9aa5b4; --ink-3:#6f7988;\n    --hub:#3987e5; --pdc:#d95926; --other:#5c6675; --xd:#199e70;\n    --accent:#3987e5; --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6);\n  }\n}\n:root[data-theme="dark"]{\n  color-scheme:dark;\n  --bg:#14171c; --panel:#191d24; --panel-2:#202631; --line:#2b323d; --line-soft:#232935;\n  --ink:#eef1f5; --ink-2:#9aa5b4; --ink-3:#6f7988;\n  --hub:#3987e5; --pdc:#d95926; --other:#5c6675; --xd:#199e70;\n  --accent:#3987e5; --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6);\n}\n:root[data-theme="light"]{\n  color-scheme:light;\n  --bg:#f6f7f9; --panel:#ffffff; --panel-2:#eef1f5; --line:#dde2e9; --line-soft:#e8ecf1;\n  --ink:#0e1116; --ink-2:#4d5766; --ink-3:#7b8593;\n  --hub:#2a78d6; --pdc:#eb6834; --other:#b0b9c4; --xd:#1baf7a;\n  --accent:#2a78d6; --shadow:0 1px 2px rgba(14,17,22,.06),0 8px 24px -12px rgba(14,17,22,.14);\n}\n*{box-sizing:border-box}\nbody{\n  margin:0; background:var(--bg); color:var(--ink);\n  font-family:"Segoe UI",Roboto,-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;\n  font-size:15px; line-height:1.6; -webkit-font-smoothing:antialiased;\n}\n.mono{font-family:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;font-variant-numeric:tabular-nums}\n.wrap{max-width:1780px;margin:0 auto;padding:40px 24px 72px}\nheader{border-bottom:1px solid var(--line);padding-bottom:28px;margin-bottom:28px}\n.eyebrow{\n  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:11px;letter-spacing:.14em;\n  text-transform:uppercase;color:var(--ink-3);margin:0 0 12px\n}\nh1{font-size:clamp(26px,3.6vw,38px);line-height:1.15;margin:0 0 14px;letter-spacing:-.02em;text-wrap:balance;font-weight:650}\n.lede{margin:0;max-width:66ch;color:var(--ink-2);font-size:16.5px}\n.lede strong{color:var(--ink);font-weight:620}\n.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:1px;background:var(--line);\n  border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:26px 0 0}\n.stat{background:var(--panel);padding:14px 16px}\n.stat .k{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:10.5px;letter-spacing:.1em;\n  text-transform:uppercase;color:var(--ink-3);margin-bottom:6px}\n.stat .v{font-size:25px;font-weight:640;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.1}\n.stat .s{font-size:12.5px;color:var(--ink-3);margin-top:3px}\n.stat .v em{font-style:normal;font-size:15px;color:var(--ink-2);font-weight:500}\n.controls{display:flex;flex-wrap:wrap;gap:10px 22px;align-items:center;margin:30px 0 6px}\n.grp{display:flex;align-items:center;gap:9px}\n.grp>span{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:10.5px;letter-spacing:.1em;\n  text-transform:uppercase;color:var(--ink-3)}\n.seg{display:inline-flex;background:var(--panel-2);border:1px solid var(--line);border-radius:8px;padding:2px;gap:2px}\n.seg button{\n  font:inherit;font-size:13px;padding:5px 13px;border:0;border-radius:6px;background:transparent;\n  color:var(--ink-2);cursor:pointer;transition:background .13s,color .13s\n}\n.seg button:hover{color:var(--ink)}\n.seg button[aria-pressed="true"]{background:var(--panel);color:var(--ink);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.09)}\n.seg button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}\n.legend{display:flex;flex-wrap:wrap;gap:8px 20px;align-items:center;margin:14px 0 0;font-size:13px;color:var(--ink-2)}\n.legend i{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:7px;vertical-align:-1px}\n.figure{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);\n  margin:18px 0 0;overflow:hidden}\n.figscroll{overflow-x:auto;padding:10px 6px 4px}\nsvg{display:block}\n.colhead{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:10.5px;letter-spacing:.1em;\n  text-transform:uppercase;fill:var(--ink-3)}\n.coltot{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:12px;\n  font-variant-numeric:tabular-nums;fill:var(--ink);font-weight:600}\n.nlabel,.nval{paint-order:stroke fill;stroke:var(--panel);stroke-width:3.5px;stroke-linejoin:round}\n.nlabel{font-size:12.5px;fill:var(--ink)}\n.nval{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:11px;fill:var(--ink-3);font-variant-numeric:tabular-nums}\n.node{cursor:pointer}\n.node rect{stroke:var(--panel);stroke-width:1}\n.node.sel rect{fill:var(--accent);stroke-width:2}\n.node.pale{opacity:.28}\n.link{fill:none;transition:stroke-opacity .13s}\n.dim .link{stroke-opacity:.07!important}\n.dim .link.on{stroke-opacity:.62!important}\n.dim .node{opacity:.34}\n.dim .node.on{opacity:1}\nbutton.lg{display:inline-flex;align-items:center;gap:7px;cursor:pointer;font:inherit;\n  font-size:12.5px;color:var(--ink-2);background:var(--panel);border:1px solid var(--line);\n  border-radius:999px;padding:5px 12px 5px 8px}\nbutton.lg:hover{border-color:var(--ink-3)}\nbutton.lg[aria-pressed="true"]{border-color:var(--accent);color:var(--ink);\n  box-shadow:inset 0 0 0 1px var(--accent)}\nbutton.lg .lgv{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink)}\nbutton.lg .lgp{color:var(--ink-3)}\n.lgnote{font-size:12px;color:var(--ink-3);align-self:center}\n.figfoot{border-top:1px solid var(--line-soft);padding:11px 18px;font-size:12.5px;color:var(--ink-3);\n  display:flex;flex-wrap:wrap;gap:6px 18px;justify-content:space-between}\n#tip{\n  position:fixed;pointer-events:none;z-index:9;opacity:0;transition:opacity .1s;\n  background:var(--panel);border:1px solid var(--line);border-radius:9px;box-shadow:var(--shadow);\n  padding:9px 12px;font-size:13px;max-width:280px;line-height:1.45\n}\n#tip .tt{font-weight:640;margin-bottom:3px;display:block}\n#tip .tv{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-variant-numeric:tabular-nums;color:var(--ink-2)}\n#tip .tn{color:var(--ink-3);font-size:12px;margin-top:4px;display:block}\n.notes{display:grid;grid-template-columns:repeat(auto-fit,minmax(268px,1fr));gap:16px;margin:34px 0 0}\n.note{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--nc,var(--accent));\n  border-radius:8px;padding:15px 17px}\n.note h3{margin:0 0 7px;font-size:14px;font-weight:640;letter-spacing:-.01em}\n.note p{margin:0;font-size:13.5px;color:var(--ink-2);line-height:1.55}\n.note p+p{margin-top:8px}\n.note b{color:var(--ink);font-weight:620}\nh2{font-size:19px;margin:44px 0 10px;letter-spacing:-.015em;font-weight:640;max-width:70ch}\nh2+p{margin:0 0 16px;color:var(--ink-2);max-width:70ch;font-size:14.5px}\ntable{border-collapse:collapse;width:100%;font-size:13px}\n.tablewrap{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}\nth,td{padding:8px 13px;text-align:left;border-bottom:1px solid var(--line-soft);white-space:nowrap}\nth{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:10.5px;letter-spacing:.09em;\n  text-transform:uppercase;color:var(--ink-3);font-weight:500;background:var(--panel-2);position:sticky;top:0}\ntd.n{text-align:right;font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}\ntbody tr:hover{background:var(--panel-2)}\ntbody tr:last-child td{border-bottom:0}\n.tag{display:inline-block;font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:10px;\n  letter-spacing:.06em;padding:1px 6px;border-radius:4px;background:var(--panel-2);color:var(--ink-2);\n  border:1px solid var(--line)}\nfooter{margin-top:52px;padding-top:20px;border-top:1px solid var(--line);color:var(--ink-3);font-size:12.5px}\nfooter code{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:12px;color:var(--ink-2)}\n.xlab{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:10.5px;letter-spacing:.11em;fill:var(--ink-3)}\n.xlab.xreal{fill:var(--pdc)}\n.xbox{fill:none;stroke:var(--ink-3);stroke-width:1.2}\n.xbox.xhub{stroke:var(--hub);stroke-width:1.6}\n.xbox.xreal-box{stroke:var(--pdc);stroke-width:1.6}\n.xt{font-size:12.5px;fill:var(--ink)}\n.xt.xhubt{fill:var(--hub)}\n.xt.xrealt,.xs.xrealt{fill:var(--pdc)}\n.xs{font-size:11px;fill:var(--ink-3)}\n.xstrike{text-decoration:line-through;fill:var(--pdc)}\n.xarr{stroke:var(--ink-3);stroke-width:1.3}\n.xrule{stroke:var(--line);stroke-width:1}\n.hrlab{font-size:12.5px;fill:var(--ink)}\n.hrsub{font-family:ui-monospace,Menlo,monospace;font-size:10.5px;fill:var(--ink-3)}\n.hrval{font-size:12px;font-weight:600;fill:#fff}\n.hrleg{font-size:11.5px;fill:var(--ink-2)}\n.sub{color:var(--ink-3);font-size:12px;line-height:1.4}\n.thr{display:flex;align-items:center;gap:10px;background:var(--panel-2);border:1px solid var(--line);border-radius:8px;padding:3px 10px}\n.thr input[type=range]{width:200px;accent-color:var(--accent);cursor:pointer}\n.thr input[type=number]{width:70px;font:inherit;font-size:13px;font-variant-numeric:tabular-nums;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:3px 6px}\n.thr .unit{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;color:var(--ink-3)}\n#foldnote strong{color:var(--ink-2);font-weight:600}\ntd.n.warn{color:var(--pdc);font-weight:600}\n#covtable td:last-child{white-space:normal;min-width:320px}\n#covtable tr.field td{background:var(--panel-2)}\n#covtable tr.field .mono{color:var(--accent)}\ntbody tr.hit{background:color-mix(in srgb,var(--pdc) 13%,transparent)}\ntbody tr.hit td:first-child{font-weight:700}\n.minibar{width:120px;height:8px;border-radius:4px;background:var(--panel-2);overflow:hidden}\n.minibar span{display:block;height:100%;border-radius:4px;background:var(--hub)}\n.arr{color:var(--ink-3)}\n.duo{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin:16px 0 0}\n.duo h3{margin:0 0 8px;font-size:13.5px;font-weight:640}\n.duo h3 span{font-weight:400;color:var(--ink-3)}\n.stagelist{list-style:none;margin:16px 0 0;padding:0;display:grid;gap:1px;background:var(--line);\n  border:1px solid var(--line);border-radius:10px;overflow:hidden}\n.stagelist li{background:var(--panel);padding:13px 16px;display:grid;grid-template-columns:190px 1fr;gap:16px}\n.stagelist b{font-size:13.5px}\n.stagelist .rule{color:var(--ink-2);font-size:13.5px}\n.stagelist .rule code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;\n  background:var(--panel-2);padding:1px 5px;border-radius:4px;color:var(--ink)}\n@media (max-width:720px){.stagelist li{grid-template-columns:1fr;gap:4px}}\n@media (prefers-reduced-motion:reduce){*{transition:none!important}}\n</style>\n'

BODY = '<div class="wrap">\n<header>\n  <p class="eyebrow">Scan-event path analysis &middot; 20 May 2026 &middot; {{TRACED}} articles</p>\n  <h1>Parcel paths, source to sink</h1>\n  <p class="lede">Where parcels actually go, lodgement to delivering depot &mdash; with a dial that\n  folds small lanes into bigger ones, so you can see what a simpler model would cost.</p>\n  <div class="stats" id="stats"></div>\n</header>\n\n<div class="controls">\n  <div class="grp"><span>Product class</span>\n    <div class="seg" role="group" aria-label="Product class">\n      <button data-cls="ALL" aria-pressed="true">All</button>\n      <button data-cls="PP" aria-pressed="false">PP &middot; 85%</button>\n      <button data-cls="EP" aria-pressed="false">EP &middot; 15%</button>\n    </div>\n  </div>\n  <div class="grp"><span>Melbourne sorts</span>\n    <div class="seg" role="group" aria-label="Number of Melbourne sorts">\n      <button data-sorts="all" aria-pressed="true">All</button>\n      <button data-sorts="0" aria-pressed="false">None</button>\n      <button data-sorts="1" aria-pressed="false">1</button>\n      <button data-sorts="2" aria-pressed="false">2</button>\n      <button data-sorts="3+" aria-pressed="false">3+</button>\n    </div>\n  </div>\n  <div class="grp"><span>Fold lanes under</span>\n    <div class="thr">\n      <input id="thr" type="range" min="1" max="5000" step="1" value="1"\n             aria-label="Fold every lane carrying fewer articles than this">\n      <input id="thrn" type="number" min="1" max="5000" step="1" value="1"\n             aria-label="Fold threshold, articles">\n      <span class="unit">articles</span>\n    </div>\n  </div>\n  <div class="grp"><span>View</span>\n    <div class="seg" role="group" aria-label="View">\n      <button data-view="flow" aria-pressed="true">Diagram</button>\n      <button data-view="table" aria-pressed="false">Table</button>\n    </div>\n  </div>\n  <span class="sub" id="sortnote"></span>\n</div>\n<p class="sub" id="foldnote" style="margin:8px 0 0"></p>\n\n<div class="legend" id="legend"></div>\n\n<div class="figure" id="fig">\n  <div class="figscroll"><svg id="sankey" role="img" aria-label="Sankey diagram of parcel flow from four source bands &mdash; interstate, metro Victorian lodgement delivered the same day, metro stock already kept at the delivering depot, and regional Victorian lodgement &mdash; through the first and second Melbourne sort to the delivering depot"></svg></div>\n  <div class="figfoot">\n    <span class="mono" id="figtot"></span>\n  </div>\n</div>\n\n<h2>What each threshold costs</h2>\n<div class="tablewrap"><table id="sweeptable"><thead><tr>\n  <th>Fold under</th><th style="text-align:right">Lanes</th><th style="text-align:right">Nodes</th><th style="text-align:right">Sorts per parcel</th><th style="text-align:right">First sort at a hub</th><th style="text-align:right">Hub handles, depot sorts</th><th style="text-align:right">Sorted once only</th><th style="text-align:right">Volume re-routed</th><th>Sites that go dark</th>\n</tr></thead><tbody></tbody></table></div>\n\n<div id="tablewrap" hidden>\n  <h2>Every flow, as numbers</h2>\n  <p>The same data the diagram draws, for the current filters. Sorted by volume.</p>\n  <div class="tablewrap"><table id="flowtable"><thead><tr>\n    <th>Stage</th><th>From</th><th>To</th><th style="text-align:right">Articles</th><th style="text-align:right">Share of stage</th>\n  </tr></thead><tbody></tbody></table></div>\n</div>\n\n<footer>\n  <p>Built from <code>inputs/melbourne/all_scan_for_melbourne_pdc_20052026.csv</code> by\n  <code>sankey_from_scans.py</code>, over the reduction in <code>export_chain2_factors.py</code>.\n  Volumes are <b>articles</b> (<code>Article_count</code>), the same unit as the model\'s demand\n  table. <b>Every delivery date in the extract</b> &mdash; {{TRACED}} of {{EXTRACT}} articles;\n  the ones left out have no state on any scan, so their origin cannot be read. Delivering depot\n  from the last <code>ZPT_DELIVER</code> scan where it names a depot, otherwise from\n  <code>Terminating_facility_name</code>. <b>Source bands:</b> interstate is decided on the\n  STATE of the lodgement scan and is one band whether or not the parcel slept at the depot;\n  Victorian freight is then split by testing its lodgement point against\n  <code>{{BOUNDARY}}</code>, the dissolved first-mile catchment. The extract carries no\n  coordinates, only a facility name, so a lodgement that cannot be placed bands as\n  <code>{{UNPLACED}}</code> &mdash; regional is therefore an <b>upper bound</b> and metro a lower\n  one. Of the {{KEPTEA}} articles that slept at their depot, {{KEPTMETROPCT}} were metro-lodged\n  and are drawn as their own source band; the {{KEPTINTPCT}} interstate and {{KEPTREGIONPCT}}\n  regional remainder is drawn under its lodgement band instead.</p>\n</footer>\n</div>\n<div id="tip" role="status" aria-live="polite"></div>\n'

JS = 'const SITE = {\n  MPF:{n:"Melbourne Parcel Facility", k:"hub"},\n  TPF:{n:"Tullamarine Parcel Facility", k:"hub"},\n  MGF:{n:"Melbourne Gateway Facility", k:"hub"},\n  SWP:{n:"Sunshine West PDC", k:"pdc"},\n  MNP:{n:"Melbourne Nth Parcel Facility", k:"pdc"},\n  BAY:{n:"Bayswater PDC", k:"pdc"},\n  DLC:{n:"Dandenong LC", k:"other"},\n  AVL:{n:"Avalon Parcel Facility", k:"other"},\n  ONCE:{n:"Sorted once and sent on", k:"other"},\n  ELSE:{n:"Sorted elsewhere", k:"other"},\n  NONE:{n:"No sort scan at all", k:"other"}\n};\nconst SITE_ORDER = ["MPF","TPF","MGF","SWP","MNP","BAY","DLC","AVL","ONCE","ELSE","NONE"];\n// The source bands, and they are deliberately NOT symmetric. Interstate is one band whether or\n// not it slept at the depot: it reaches Melbourne one way, on a linehaul into a gateway, and\n// whether it then waited a night is a fact about the depot\'s timetable rather than about where\n// the volume came from. Victorian freight splits on GEOGRAPHY — the lodgement point tested\n// against the dissolved first-mile catchment — and only the metro side carries a kept-at-depot\n// band, because that is the only stage slice the model has a lane for. Regional volume is one\n// band for the same reason interstate is.\nconst ORG = {INT:{n:"Interstate", k:"other"},\n             KEPT_METRO:{n:"Kept at the depot · metro", k:"pdc"},\n             METRO:{n:"Metro Victoria", k:"other"},\n             REGION:{n:"Regional Victoria", k:"other"}};\nconst ORG_SUB = {INT:"lodged outside Victoria — one band, whether or not it slept at the depot",\n  METRO:"lodged inside the first-mile catchment, delivered the same day",\n  REGION:"lodged in Victoria, outside the first-mile catchment — one band, kept volume included",\n  KEPT_METRO:"already in the building — arrived before the delivery date, and sorted here first where the depot has a sorter, lodged inside the catchment"};\nconst COLS = ["Where it came from", "First sorted at", "Sorted again at", "Despatched from", "Delivered by"];\nconst KCOL = {hub:"var(--hub)", pdc:"var(--pdc)", other:"var(--other)", xd:"var(--xd)"};\n\nconst HASH = (location.hash || "").replace("#","").toUpperCase();\nlet CLS = ["ALL","EP","PP"].includes(HASH) ? HASH : "ALL", VIEW = "flow", SORTS = "all";\n// every lookup goes through here, so the class filter and the sort filter can never drift apart\nconst cur = () => ({flows: DATA.flows[CLS][SORTS], stats: DATA.stats[CLS][SORTS]});\nconst $ = s => document.querySelector(s);\nconst svg = $("#sankey"), tip = $("#tip");\nconst fmt = n => Math.round(n).toLocaleString("en-AU");\n\nconst COL_OF = {ORG:0, E:1, M:2, X:3, P:4};\nconst COL_ROLE = {1:"ran the first sort", 2:"ran the second sort", 3:"despatched it"};\nfunction nodeMeta(id){\n  const [p, ...r] = id.split("_"), key = r.join("_");\n  const col = COL_OF[p];\n  if (p === "ORG") return {label:ORG[key].n, kind:ORG[key].k, sub:ORG_SUB[key], col};\n  if (p === "P")   return {label:key, kind:"other", sub:"ran the delivery round — from the delivery scan, or the plan where that names no depot", col};\n  const named = key === "ONCE" || key === "NONE" || key === "ELSE";\n  // column 1 asks who HANDLED it, so its residual means something different from the sort columns\n  const label = named ? SITE[key].n : key;\n  const NOTE = {\n    ONCE: "one sort only — no second round",\n    ELSE: "machine-sorted, just not at one of the eight sites — 85% of it interstate",\n    NONE: "no machine-sort scan anywhere in the file, Melbourne or otherwise"\n  };\n  return {label, key, kind:SITE[key].k, col,\n          sub: named ? NOTE[key] : `${SITE[key].n} — ${COL_ROLE[col]}`};\n}\n\n// ── layout ────────────────────────────────────────────────────────────────\n// PADL fits the longest left-column label. Since the stage band was split by lodgement that\n// is "Kept at the depot · interstate", not "Interstate", which is half the width.\nconst W = 1680, PADL = 205, PADR = 148, TOP = 60, BOT = 18, NW = 13, GAP = 11;\n\nfunction build(links){\n  const cols = [[],[],[],[],[]], nodes = new Map();\n  const get = id => {\n    if (!nodes.has(id)) {\n      const m = nodeMeta(id);\n      const n = {id, ...m, in:0, out:0, inL:[], outL:[]};\n      nodes.set(id, n); cols[m.col].push(n);\n    }\n    return nodes.get(id);\n  };\n  links.forEach(l => {\n    const s = get(l.s), t = get(l.t);\n    s.out += l.v; t.in += l.v;\n    const L = {...l, sn:s, tn:t};\n    s.outL.push(L); t.inL.push(L);\n  });\n  cols.forEach(c => c.forEach(n => n.val = Math.max(n.in, n.out)));\n\n  // fixed, meaningful order: hubs, then sorting PDCs, then the residual;\n  // depots by volume so the busiest sit together. Order does not change with the filter.\n  const ORG_RANK = {ORG_INT:0, ORG_KEPT_METRO:1, ORG_METRO:2, ORG_REGION:3};\n  const rank = n => n.col === 0 ? ORG_RANK[n.id]\n                  : n.col === 4 ? -n.val\n                  : SITE_ORDER.indexOf(n.key);\n  cols.forEach(c => c.sort((a,b) => rank(a) - rank(b)));\n\n  const H = 640, inner = H - TOP - BOT;\n  let scale = Infinity;\n  cols.forEach(c => {\n    const tot = c.reduce((a,n) => a + n.val, 0);\n    if (tot > 0) scale = Math.min(scale, (inner - GAP * (c.length - 1)) / tot);\n  });\n  const xOf = i => PADL + i * ((W - PADL - PADR) / 4);\n  cols.forEach((c, i) => {\n    let y = TOP;\n    c.forEach(n => { n.x = xOf(i); n.y = y; n.h = Math.max(n.val * scale, 2.5); y += n.h + GAP; });\n  });\n  // ribbon offsets: source side ordered by target position, target side by source position\n  cols.forEach(c => c.forEach(n => {\n    let o = 0;\n    n.outL.sort((a,b) => a.tn.y - b.tn.y).forEach(l => { l.sy = n.y + o + l.v * scale / 2; l.w = l.v * scale; o += l.v * scale; });\n    o = 0;\n    n.inL.sort((a,b) => a.sn.y - b.sn.y).forEach(l => { l.ty = n.y + o + l.v * scale / 2; o += l.v * scale; });\n  }));\n  return {cols, nodes, links: links.map(l => l), H};\n}\n\nfunction ribbon(l){\n  const x0 = l.sn.x + NW, x1 = l.tn.x, cx = (x0 + x1) / 2;\n  return `M${x0},${l.sy}C${cx},${l.sy} ${cx},${l.ty} ${x1},${l.ty}`;\n}\n\n// ── lane folding ── the simplification dial ──────────────────────\n// A WHAT-IF on the picture, never a re-measurement: DATA is copied, not touched. A ribbon under\n// THRESH articles is deleted and its volume folds onto what survives at the same source node, so\n// the node keeps every article it had.\n// MERGE THE BANDS, THEN ASK ONCE (2026-08-24). Stages 0 and 2 draw TWO ribbons per node pair:\n// the freight first sorted where it was handled, and the cross-docked freight beside it. MPF ->\n// TPF carries both, 2,439 and 23. The dial used to ask about each of those separately, so one\n// movement was judged twice and a truck could lose half of itself — and the losing half was then\n// scattered across every OTHER place MPF sends to, which says the freight went somewhere else\n// entirely while the truck it was actually on is still on the page beside it.\n// So the bands on a pair are MERGED FIRST and the threshold is put to the lane: is this movement,\n// all of it, worth 1,300 articles? A lane that clears it keeps everything measured on it. A lane\n// that does not folds WHOLE, and its volume is divided EQUALLY between the lanes still leaving\n// that node — which is what this dial has always done, and remains the honest answer, because\n// with the lane gone there is no evidence left about where that freight would have gone instead.\n// The node keeps every article either way: that is the invariant the page is checked on.\n// DIVIDED BAND BY BAND, THOUGH (2026-08-24). The lane is still judged ONCE, on its merged total\n// — that rule stands — but what comes off it is split by band first, and each band is divided\n// only between the surviving lanes that ALREADY CARRY THAT BAND. The previous rule divided the\n// merged total and then split each arriving share in the ratio the destination lane happened to\n// carry, which RECOLOURED freight: cross-dock volume folded off one lane arrived at the next as\n// 93% ordinary first sort. It did not show up in the column totals, because those are right\n// either way, but the cross-dock cohort drifted with the dial — 17,519 articles at rest, 15,922\n// at a threshold of 1,000, and UP to 18,017 in the source stage, where grey volume folded into\n// green-heavy lanes and invented cross-dock that was never measured. A parcel that was worked at\n// one building and sorted at another does not stop having been, because a thin lane left the\n// picture. Volume moves; the cohort does not. Only the two named last resorts below recolour\n// anything, and both report themselves.\n// TWO THINGS FOLD, and they are different questions asked with the same number:\n//   the LANE  — is this movement worth drawing at all? Asked of the merged total.\n//   the BAND  — is this SPLIT of a surviving lane worth drawing? Asked of the band.\n// A band under the threshold on a lane that clears it moves SIDEWAYS — onto the same band on the\n// other surviving lanes out of that node, equally. That is the case the first three attempts all\n// missed: MPF -> TPF carried 22 and 1 in the express view, the lane cleared a threshold of 6 on\n// the strength of the 22, and the 1 stayed on the page — a hairline the dial was explicitly asked\n// to remove. Every ribbon left under the threshold was one of these, and every one of them was a\n// cross-dock band. Sideways rather than into its own lane-mate, because a thin cross-dock band is\n// still cross-dock: merging it into the ordinary ribbon beside it is the same recolouring the\n// lane fold used to do, one lane further down. The page is checked on the NODE total, not the\n// lane total, so this keeps the invariant it actually has. Two last resorts remain and they do\n// recolour: a band with no same-coloured lane left to go to merges into its own lane, and a lane\n// where NO band clears alone collapses onto its biggest — sending them all away would empty a\n// lane the threshold just said to keep. Both are counted separately in the caption.\n// Then the cascade, left to right: a node that lost inbound volume has its outbound lanes\n// rescaled to what actually reached it. Without that the folded volume would leak — a site whose\n// every inbound lane was thin would still despatch freight nobody sent it. With it, a marginal\n// site goes dark all the way across, which is the answer the dial exists to give.\n// A source keeps at least its LARGEST lane: a threshold above a whole node has nowhere else to\n// put the volume, and destroying it would break the column totals the page is checked on.\n// ── what the picture says, measured off the picture ──────────────────────────────\n// Every tile is derived from the ribbons actually on screen instead of read from a table\n// computed per parcel, which is what lets the fold dial move them. The denominator is THE DAY\n// throughout. Three of these used to be quoted over interstate volume, and that cannot be\n// recovered here: past column 0 a ribbon carries no lodgement state. So the resting values are\n// on a wider base than they were and read lower.\n// `mean` counts a parcel once for reaching a first sort, again for landing on a NAMED second-sort\n// node, again for a despatch site that differs from the second. The diagram has three sort\n// columns, so a parcel sorted four times counts three — 0.59% of the day, worth 0.01 on the mean.\n// Checked against the sort-count filter, which is the same quantity measured per parcel: the\n// buckets return 0.000, 1.000, 2.000 and 2.937.\n// CROSS-DOCK is no longer measured here: it was diag("R","E") and column R is gone. The tile\n// reads the per-parcel measurement out of DATA.stats instead, so the number survives the\n// column that used to show it — and, unlike its neighbours, it does NOT move with the fold\n// dial. The tile says so rather than letting a still number look like a stable one.\nconst NAMED = new Set(SITE_ORDER.filter(k => k !== "ONCE" && k !== "ELSE" && k !== "NONE"));\nconst nkey = id => id.split("_").slice(1).join("_");\n// ONE definition of a ribbon category, used by the drawing, the legend and the filter, so\n// the swatch, the count and the highlight can never disagree. Reads a PLAIN link, so it\n// works before build() as well as after.\nfunction kindOf(l){\n  const tc = COL_OF[l.t.split("_")[0]], tk = nkey(l.t);\n  return (tc === 1 && NAMED.has(tk)) ? (l.x ? "xd" : "hub")\n       : (tc === 2 && NAMED.has(tk)) ? (l.x ? "xd" : "pdc") : "other";\n}\nlet CAT = null;      // the isolated category, or null for all four\n// ── PINNING A CELL ────────────────────────────────────────────────────────────────────\n// Click a node and the picture keeps only what enters and leaves it. Hovering already did this\n// for as long as the pointer stayed put, which is no use at all when the thing you want to read\n// is the tooltip on one of the ribbons you just revealed. So the same isolation, made to stay.\n// It STACKS with the legend rather than replacing it: pinning TPF while Cross-dock is isolated\n// asks "what does TPF cross-dock", which is a question neither filter answers on its own. Both\n// are filters ON THE INK AND ON THE POINTER only — no width, no total and no tile moves, so a\n// number read under a pin is the same number as under none.\nlet SEL = null;      // the pinned node id, or null\nfunction measure(links){\n  const inn = new Map(), out = new Map(), seen = new Set();\n  links.forEach(l => { out.set(l.s, (out.get(l.s) || 0) + l.v); inn.set(l.t, (inn.get(l.t) || 0) + l.v);\n                       seen.add(l.s); seen.add(l.t); });\n  const I = id => inn.get(id) || 0, O = id => out.get(id) || 0;\n  // every article leaves the first column exactly once, whichever first column it is\n  const T = Math.max([...out.keys()].filter(id => COL_OF[id.split("_")[0]] === 0)\n                                    .reduce((a, id) => a + O(id), 0), 1);\n  // a diagonal between two NAMED buildings — with the handled column gone this is the THIRD\n  // sort in the despatch stage, and nothing else\n  const diag = (a, b) => links.filter(l => l.s[0] === a && l.t[0] === b && NAMED.has(nkey(l.s))\n                                      && NAMED.has(nkey(l.t)) && nkey(l.s) !== nkey(l.t))\n                              .reduce((x, l) => x + l.v, 0);\n  const sorted1 = T - I("E_NONE") - I("E_ELSE");\n  const sorted2 = [...NAMED].reduce((a, k) => a + I("M_" + k), 0);\n  return {T, lanes:links.length, nodes:seen.size,\n          mean:(sorted1 + sorted2 + diag("M", "X")) / T,\n          hub:100 * (I("E_MPF") + I("E_TPF") + I("E_MGF")) / T,\n          // the green band, straight off the ribbons — so the fold dial moves it again,\n          // which it could not while the number came from DATA.stats\n          xdock:100 * links.filter(l => l.x && COL_OF[l.s.split("_")[0]] === 0)\n                            .reduce((a, l) => a + l.v, 0) / T,\n          single:100 * I("M_ONCE") / T,\n          vic:100 * (O("ORG_METRO") + O("ORG_KEPT_METRO") + O("ORG_REGION")) / T,\n          region:100 * O("ORG_REGION") / T,\n          kept:100 * O("ORG_KEPT_METRO") / T};\n}\n\nlet THRESH = 1, FOLDSTAT = {cut:0, of:0, moved:0};\nconst lanesIn = links => new Set(links.filter(l => l.v > 0).map(l => l.s + ">" + l.t)).size;\n// THERE IS NO SECOND LINE STYLE (2026-08-24, and it was tried). A dashed ribbon marked a lane\n// carrying under FOLD_MIN_ARTICLES — one the model folds away before it builds anything. The\n// encoding did not survive contact with the picture: the question only applies to a movement\n// between two buildings, so 68% of the ink was plain for a reason that had nothing to do with\n// the model (a source band, a ONCE/NONE/ELSE end, a parcel staying put), and a plain line\n// therefore meant two different things at once. The fold dial already shows that boundary, and\n// shows it honestly, by actually folding at the threshold you set.\n// which band a ribbon belongs to — the one thing the fold must never change about a parcel\nconst BK = l => l.x ? 1 : 0;\nfunction fold(links){\n  const out = links.map(l => ({...l}));\n  // `cut` and `dark` count LANES leaving the picture. `shifted` counts thin BANDS that moved\n  // onto the same band elsewhere at that node, `bands` the ones with nowhere same-coloured left\n  // to go, which collapsed into their own lane. Only the second kind recolours freight, so the\n  // caption names them apart rather than reporting one number for both.\n  FOLDSTAT = {cut:0, bands:0, merged:0, shifted:0, shiftv:0, of:lanesIn(out), moved:0,\n              left:lanesIn(out), dark:0};\n  if (THRESH <= 1) return out;\n  const stages = [[],[],[],[]];\n  out.forEach(l => stages[COL_OF[l.s.split("_")[0]]].push(l));\n  const arrived = new Map();\n  stages.forEach((stage, col) => {\n    if (col > 0) {\n      const had = new Map();\n      stage.forEach(l => had.set(l.s, (had.get(l.s) || 0) + l.v));\n      stage.forEach(l => { const h = had.get(l.s);\n                           l.v = h > 0 ? l.v * (arrived.get(l.s) || 0) / h : 0; });\n    }\n    const bySrc = new Map();\n    stage.forEach(l => { if (!bySrc.has(l.s)) bySrc.set(l.s, []); bySrc.get(l.s).push(l); });\n    bySrc.forEach(ls => {\n      // ONE LANE PER DESTINATION, bands merged, before the threshold sees anything\n      const lane = new Map();\n      ls.forEach(l => { if (!lane.has(l.t)) lane.set(l.t, []); lane.get(l.t).push(l); });\n      const tot = t => lane.get(t).reduce((a, l) => a + l.v, 0);\n      let keep = [...lane.keys()].filter(t => tot(t) >= THRESH);\n      // a threshold above the whole node has nowhere to put the volume, and destroying the node\n      // is a different decision from folding a lane — one this dial is not allowed to make\n      if (!keep.length) keep = [[...lane.keys()].reduce((a, b) => tot(b) > tot(a) ? b : a)];\n      const drop = [...lane.keys()].filter(t => !keep.includes(t) && tot(t) > 0);\n      // ── the LANE fold: judged once on the merged total, DIVIDED band by band ──────────\n      if (drop.length) {\n        const pot = new Map();\n        drop.forEach(t => lane.get(t).forEach(l => {\n          if (l.v <= 0) return;\n          pot.set(BK(l), (pot.get(BK(l)) || 0) + l.v);\n          FOLDSTAT.moved += l.v; l.v = 0;\n        }));\n        FOLDSTAT.cut += drop.length;\n        pot.forEach((vol, k) => {\n          // only the surviving lanes that already run this band, so cross-dock volume lands as\n          // cross-dock instead of being diluted into whatever the destination mostly carries\n          const hosts = keep.flatMap(t => lane.get(t).filter(l => BK(l) === k && l.v > 0));\n          if (hosts.length) { const s = vol / hosts.length; hosts.forEach(l => l.v += s); }\n          else {\n            // nothing left at this node carries it — the only case where the band has to give\n            // way, and then it goes equally between the lanes, in each lane by its own ratio\n            const s = vol / keep.length;\n            keep.forEach(t => { const rs = lane.get(t), w = tot(t);\n                                rs.forEach(l => l.v += w > 0 ? s * l.v / w : s / rs.length); });\n          }\n        });\n      }\n      // ── the BAND fold, on what each surviving lane ACTUALLY ended up with ─────────────\n      // After the step above, not before it: a band judged on its resting volume can be folded\n      // away and then handed the volume of the lane that just folded into it, which is how the\n      // old order made TPF -> DLC cross-dock vanish while TPF -> BAY cross-dock never moved.\n      keep.forEach(t => {\n        const rs = lane.get(t).filter(l => l.v > 0);\n        if (rs.length < 2) return;\n        const fat = rs.filter(l => l.v >= THRESH);\n        if (!fat.length) {\n          // the lane clears but no band does; they collapse in place onto the biggest, because\n          // sending them all away would empty a lane the threshold just said to keep\n          const into = rs.reduce((a, b) => b.v > a.v ? b : a);\n          rs.forEach(l => { if (l !== into) { into.v += l.v; FOLDSTAT.bands++;\n                                              FOLDSTAT.merged += l.v; l.v = 0; } });\n          return;\n        }\n        rs.filter(l => l.v < THRESH).forEach(l => {\n          const hosts = keep.filter(t2 => t2 !== t)\n                            .flatMap(t2 => lane.get(t2).filter(x => BK(x) === BK(l)\n                                                                    && x.v >= THRESH));\n          const vol = l.v; l.v = 0;\n          if (hosts.length) { const s = vol / hosts.length; hosts.forEach(x => x.v += s);\n                              FOLDSTAT.shifted++; FOLDSTAT.shiftv += vol; }\n          else { fat.reduce((a, b) => b.v > a.v ? b : a).v += vol;\n                 FOLDSTAT.bands++; FOLDSTAT.merged += vol; }\n        });\n      });\n    });\n    stage.forEach(l => { if (l.v > 0) arrived.set(l.t, (arrived.get(l.t) || 0) + l.v); });\n  });\n  const live = out.filter(l => l.v > 0);\n  // two ways a lane leaves the picture and they mean different things: `cut` was folded\n  // for being thin, `dark` emptied because the node upstream of it received nothing once\n  // ITS inbound lanes were folded. Reporting only the first overstates what survives.\n  FOLDSTAT.left = lanesIn(live);\n  FOLDSTAT.dark = FOLDSTAT.of - FOLDSTAT.left - FOLDSTAT.cut;\n  return live;\n}\n\n// The legend doubles as the filter: click a category to isolate it, click again for all\n// four. It is a HIGHLIGHT and not a re-measurement — the ribbons keep their widths and\n// every stage still totals the day — so the counts beside each swatch stay true whichever\n// category is selected. That is the point: you can read the split without losing the whole.\nconst LG = [["hub", "First sort", "first sorted in the building that handled it"],\n            ["xd", "Cross-dock", "handled at one building, first sorted at another — and kept green where that same freight comes back for a second sort"],\n            ["pdc", "Second sort", "round-2 work that was NOT cross-docked"],\n            ["other", "Not a sort", "sorted once and sent on, never sorted, or the run to the depot"]];\nfunction legend(raw){\n  const vol = {hub:0, xd:0, pdc:0, other:0};\n  raw.forEach(l => { vol[kindOf(l)] += l.v; });\n  // THE DENOMINATOR IS THE DAY, not the ribbon total. Summing all four and dividing gives\n  // "First sort 20.9%" and "Not a sort 68.7%", which is arithmetic about the drawing\n  // rather than about the freight: there are four stages, so the day is counted four\n  // times, and grey collects every downstream ribbon because past the second sort no\n  // ribbon is a sort. Against the day each share is a fact about parcels — 83.5% were\n  // first sorted where they were handled, 31.1% came back for a second sort. They do NOT\n  // sum to 100 and should not: a parcel can be in the first and the third.\n  const day = Math.max(raw.filter(l => l.s[0] === "O").reduce((a, l) => a + l.v, 0), 1);\n  // GREEN IS A COHORT, not a stage, so it is the one category that would double-count: the\n  // same parcel is drawn green entering its first sort and green again leaving it. The\n  // headline counts it ONCE, in the stage that defines it, and the onward volume is named\n  // separately rather than folded in.\n  const xd0 = raw.filter(l => l.s[0] === "O" && kindOf(l) === "xd")\n                 .reduce((a, l) => a + l.v, 0);\n  const xdOn = vol.xd - xd0;\n  vol.xd = xd0;\n  // grey is the exception and is left without a share. It is not one thing: 9,869 of it\n  // is freight with no modelled sort, the rest is simply every ribbon past the second\n  // sort column. A percentage would imply a category that can be compared with the\n  // others, and it cannot be.\n  $("#legend").innerHTML = LG.map(([k, name, note]) =>\n    `<button class="lg" data-cat="${k}" aria-pressed="${CAT === k}" title="${note}">\n       <i style="background:${KCOL[k]}"></i><b>${name}</b>\n       <span class="lgv">${fmt(vol[k])}</span>\n       <span class="lgp">${k === "other" ? "every stage"\n         : (100 * vol[k] / day).toFixed(1) + "% of the day"}</span>\n       ${k === "xd" && xdOn ? `<span class="lgp">+${fmt(xdOn)} again at the 2nd sort</span>` : ""}\n       </button>`).join("")\n    + `<span class="lgnote">${[\n        CAT ? "Showing <b>" + LG.find(x => x[0] === CAT)[1] + "</b> only &mdash; click it again"\n              + " for all four" : "Click a category to isolate it",\n        SEL ? "pinned to <b>" + nodeMeta(SEL).label + "</b> in <b>"\n              + COLS[COL_OF[SEL.split("_")[0]]] + "</b> &mdash; click it again to unpin"\n            : "click a cell to keep only what enters and leaves it",\n      ].join(" &middot; ")}. ${\n        raw.some(l => (!CAT || kindOf(l) === CAT) && (!SEL || l.s === SEL || l.t === SEL))\n          ? "Widths and totals are unchanged."\n          : "<b>Nothing matches both</b> &mdash; this cell runs no "\n            + LG.find(x => x[0] === CAT)[1].toLowerCase() + " ribbon."}</span>`;\n  $("#legend").querySelectorAll("[data-cat]").forEach(b =>\n    b.addEventListener("click", () => { CAT = CAT === b.dataset.cat ? null : b.dataset.cat;\n                                        render(); }));\n}\nfunction render(){\n  const raw = fold(cur().flows);\n  // a pin on a node the fold has since emptied would blank the page, so it does not survive one.\n  // Cleared here rather than at the paint, because the legend below has to describe what is\n  // actually pinned and it runs first.\n  if (SEL && !raw.some(l => l.s === SEL || l.t === SEL)) SEL = null;\n  legend(raw);\n  const {cols, H} = build(raw);\n  const stageTot = [0,0,0,0].map((_, i) => raw.filter(l => nodeMeta(l.s).col === i).reduce((a,b) => a + b.v, 0));\n  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);\n  svg.setAttribute("width", W); svg.setAttribute("height", H);\n\n  // Every column carries the same total — a parcel is lodged once, sorted (or not) once, and\n  // delivered once. Printing all four is the conservation check: if they ever differ, a stage\n  // has dropped or duplicated volume.\n  const colTot = cols.map(c => c.reduce((a, n) => a + n.val, 0));\n  let s = "";\n  COLS.forEach((c, i) => {\n    const x = PADL + i * ((W - PADL - PADR) / 4);\n    const tx = i === 4 ? x + NW + 9 : x;\n    s += `<text class="colhead" x="${tx}" y="18">${c}</text>`\n       + `<text class="coltot" x="${tx}" y="36">${fmt(colTot[i])} articles</text>`;\n  });\n\n  const all = [];\n  cols.forEach(c => c.forEach(n => n.outL.forEach(l => all.push(l))));\n  all.sort((a,b) => b.v - a.v);\n  const touches = l => !SEL || l.s === SEL || l.t === SEL;\n  const lit = new Set(SEL ? all.filter(touches).flatMap(l => [l.s, l.t]) : []);\n  s += `<g id="links">` + all.map((l, i) => {\n    // COLOUR IS THE SORT ROUND, not the kind of building. A ribbon is BLUE where it feeds the\n    // FIRST sort and ORANGE where it feeds the SECOND, which is the distinction the model is\n    // built on and the one a reader could not otherwise get off the picture. Building class\n    // used to own these two colours; it is readable from the labels and from SITE_ORDER, so\n    // it gave up the channel. Volume arriving at ONCE, NONE or ELSE is not a sort and stays\n    // grey, so the second column reads as "this much came back for another round, this much\n    // did not". Nodes are neutral for the same reason: one meaning per colour.\n    // COLOUR IS THE SORT ROUND, with the FIRST round split by where the work happened.\n    // The handled column used to carry that and it cost a whole stage of duplicated lanes;\n    // it is a band on the source ribbon now. BLUE = first sorted in the building that\n    // handled it. GREEN = CROSS-DOCK, worked at one building and first sorted at another.\n    // ORANGE = feeding a second sort. Green is a refinement of blue — both are round-1 work.\n    // Grey wherever the first sort is NONE or ELSE: "not cross-docked" and "no evidence\n    // either way" are different claims, and only one of them is measured.\n    const kind = kindOf(l);\n    // dashes at the WIDTH of the ribbon, so a hairline lane still reads as dashed and a fat one\n    // does not turn into a row of blocks\n    const w = Math.max(l.w, .7);\n    // ISOLATING A CATEGORY TAKES THE RIBBON OUT OF THE POINTER REACH, not merely out of the ink.\n    // Dimming alone left every hidden lane still answering the hover: you asked the page to show\n    // one layer and it handed you a tooltip for another, and a fat dimmed ribbon lying across a\n    // thin selected one swallowed the pointer before it ever arrived. `data-k` carries the\n    // category onto the element so the node handler below can ask the same question of the DOM.\n    const off = (CAT && kind !== CAT) || !touches(l);\n    return `<path class="link" data-i="${i}" data-k="${kind}" data-s="${l.sn.id}" data-t="${l.tn.id}" d="${ribbon(l)}"\n      stroke="${KCOL[kind]}" stroke-width="${w}"${\n        off ? ` pointer-events="none"` : ""} stroke-opacity="${\n        off ? .04\n        : l.sn.col >= 1 && l.sn.col <= 2 && l.sn.label === l.tn.label ? .5 : .34}"></path>`;\n  }).join("") + `</g>`;\n\n  s += `<g id="nodes">` + cols.flat().map(n => {\n    const right = n.col === 4;\n    const lx = right ? n.x + NW + 9 : n.x - 9;\n    const anc = right ? "start" : "end";\n    const mid = n.y + n.h / 2;\n    const two = n.h >= 22;\n    return `<g class="node${n.id === SEL ? " sel" : ""}${\n      SEL && !lit.has(n.id) ? " pale" : ""}" data-id="${n.id}">\n      <rect x="${n.x}" y="${n.y}" width="${NW}" height="${n.h}" rx="2.5" fill="var(--ink-3)"></rect>\n      <text class="nlabel" x="${lx}" y="${mid + (two ? -2 : 4)}" text-anchor="${anc}">${n.label}</text>\n      ${two ? `<text class="nval" x="${lx}" y="${mid + 12}" text-anchor="${anc}">${fmt(n.val)}</text>` : ""}\n    </g>`;\n  }).join("") + `</g>`;\n  svg.innerHTML = s;\n\n  const total = cols[2].reduce((a, n) => a + n.val, 0);\n  $("#figtot").textContent = fmt(total) + " articles in every column";\n  // the ribbon volume is the five stages summed, which is what a fold is a share OF —\n  // a parcel can be re-routed once per stage, so this is article-hops, not articles\n  const ribbonTot = colTot.slice(0, 4).reduce((a, b) => a + b, 0);\n  $("#foldnote").innerHTML = (THRESH <= 1\n    ? "<strong>Fold dial off.</strong> Every measured lane is drawn — "\n      + FOLDSTAT.of + " of them. Raise the threshold to see how few the picture needs."\n    : "<strong>Folding lanes under " + fmt(THRESH) + " articles</strong>, the bands on a pair"\n      + " merged first so each movement is asked once. " + FOLDSTAT.of + " lanes down to <strong>"\n      + FOLDSTAT.left + "</strong> — " + FOLDSTAT.cut + " folded for being thin"\n      + (FOLDSTAT.dark ? ", " + FOLDSTAT.dark + " emptied because the site feeding them went dark" : "")\n      + " · " + fmt(FOLDSTAT.moved) + " article-hops ("\n      + (100 * FOLDSTAT.moved / Math.max(ribbonTot, 1)).toFixed(2)\n      + "% of all ribbon volume) divided equally between the lanes still leaving each source"\n      + (FOLDSTAT.shifted ? " · " + FOLDSTAT.shifted + " thin bands (" + fmt(FOLDSTAT.shiftv)\n         + " articles) moved onto the same band on a lane that survived" : "")\n      + (FOLDSTAT.bands ? " · " + FOLDSTAT.bands + " (" + fmt(FOLDSTAT.merged) + " articles) had"\n         + " no same-coloured lane left and merged into their own, the only step that recolours"\n         : "")\n      + " · every column still totals " + fmt(colTot[0]) + ".");\n  STAGE_TOTAL = colTot;\n  wire(all, stageTot);\n  stats(measure(raw), measure(cur().flows));\n  table(raw, stageTot);\n  sweep();\n}\n\nlet STAGE_TOTAL = [];\nfunction wire(all, stageTot){\n  const fig = $("#fig");\n  svg.querySelectorAll(".link").forEach(p => {\n    const l = all[+p.dataset.i];\n    p.addEventListener("pointerenter", e => {\n      fig.classList.add("dim"); p.classList.add("on");\n      svg.querySelector(`.node[data-id="${l.sn.id}"]`).classList.add("on");\n      svg.querySelector(`.node[data-id="${l.tn.id}"]`).classList.add("on");\n      const st = STAGE_TOTAL[l.sn.col] || 1;\n      const same = l.sn.label === l.tn.label;\n      let note = "";\n      if (l.sn.col === 0 && NAMED.has(l.tn.key)) note = l.x\n        ? "CROSS-DOCK &mdash; worked at one building, then carried to this one for its first "\n          + "sort. The model builds this step (BOM_SORT_XD)."\n        : "First sorted in the same building that handled it &mdash; no cross-dock leg.";\n      else if (l.sn.id.startsWith("ORG_KEPT"))\n        note = "Stock that slept at the delivering depot — and was sorted there first, at the depots that have a sorter — lodged inside the first-mile catchment. This is where it was worked before that. Interstate and regional stock that also slept here is drawn under its own band, not this one.";\n      else if (l.sn.col === 1) note = l.tn.key === "ONCE"\n        ? "One sort only &mdash; this volume was never sorted again."\n        : l.tn.key === "ELSE" ? "Sorted somewhere else &mdash; mostly interstate, then railed straight to the depot."\n        : l.tn.key === "NONE" ? "No machine-sort scan anywhere in the file."\n        : "SORTED AGAIN &mdash; a second sort, and this is the site that ran it.";\n      else if (l.sn.col === 2) note = l.sn.key === "ONCE"\n        ? "Despatched from the one site that sorted it."\n        : same ? "The second sort was the last one."\n               : "A THIRD sort &mdash; despatched from a site later still.";\n      show(e, `${l.sn.label} &rarr; ${l.tn.label}`,\n        `${fmt(l.v)} articles &middot; ${(100 * l.v / st).toFixed(1)}% of this column`, note);\n    });\n    p.addEventListener("pointermove", e => pos(e));\n    p.addEventListener("pointerleave", clear);\n  });\n  svg.querySelectorAll(".node").forEach(g => {\n    const id = g.dataset.id;\n    g.addEventListener("pointerenter", e => {\n      fig.classList.add("dim"); g.classList.add("on");\n      // and the same question at a node: lighting its hidden ribbons would undo the isolation\n      // the reader just asked for, since `.dim .link.on` takes any lit ribbon back to full ink\n      const only = CAT ? `[data-k="${CAT}"]` : "";\n      svg.querySelectorAll(`.link[data-s="${id}"]${only},.link[data-t="${id}"]${only}`).forEach(p => {\n        p.classList.add("on");\n        svg.querySelector(`.node[data-id="${p.dataset.s}"]`).classList.add("on");\n        svg.querySelector(`.node[data-id="${p.dataset.t}"]`).classList.add("on");\n      });\n      const n = [...document.querySelectorAll(".node")], m = nodeMeta(id);\n      const v = all.filter(l => l.sn.id === id).reduce((a,b) => a + b.v, 0)\n             || all.filter(l => l.tn.id === id).reduce((a,b) => a + b.v, 0);\n      const stage = STAGE_TOTAL[m.col] || 0;\n      const note = m.kind === "hub" ? "A hub in the model."\n                 : m.kind === "pdc" ? "A sorting PDC &mdash; a depot that also runs a machine." : "";\n      show(e, m.label, `${m.sub} &middot; ${fmt(v)} articles`\n           + (stage ? ` &middot; ${(100 * v / stage).toFixed(1)}% of this column` : ""), note);\n    });\n    g.addEventListener("pointermove", e => pos(e));\n    g.addEventListener("pointerleave", clear);\n    g.addEventListener("click", () => { SEL = SEL === id ? null : id; clear(); render(); });\n  });\n  function clear(){\n    fig.classList.remove("dim");\n    svg.querySelectorAll(".on").forEach(x => x.classList.remove("on"));\n    tip.style.opacity = 0;\n  }\n}\nfunction show(e, t, v, n){\n  tip.innerHTML = `<span class="tt">${t}</span><span class="tv">${v}</span>` + (n ? `<span class="tn">${n}</span>` : "");\n  tip.style.opacity = 1; pos(e);\n}\nfunction pos(e){\n  const r = tip.getBoundingClientRect();\n  tip.style.left = Math.min(e.clientX + 14, innerWidth - r.width - 10) + "px";\n  tip.style.top  = Math.min(e.clientY + 14, innerHeight - r.height - 10) + "px";\n}\n\nconst SORTLAB = {"0":"none", "1":"one", "2":"two", "3+":"three or more"};\n// `rest` is the same measurement on the UNFOLDED picture, so every tile can say what the dial\n// has done to it. Without that the numbers move and there is nothing to move them against.\nfunction stats(now, rest){\n  const filt = SORTS !== "all", whole = DATA.stats[CLS].all, folded = THRESH > 1;\n  const was = (a, b, d) => folded && Math.abs(a - b) >= (d === 2 ? .005 : .05)\n                         ? " · was " + b.toFixed(d) + (d === 1 ? "%" : "") : "";\n  const pct = (v, r, note) => [v.toFixed(1) + "<em>%</em>", "of the day" + was(v, r, 1) + " · " + note];\n  $("#stats").innerHTML = [\n    ["Articles traced", fmt(now.T), filt\n      ? (100 * now.T / whole.articles).toFixed(1) + "% of the " + fmt(whole.articles) + " traced"\n      : "every delivery date in the extract · the fold cannot move this"],\n    ["Sorts per parcel", now.mean.toFixed(2),\n      "as drawn" + was(now.mean, rest.mean, 2) + " · the model gives every parcel two"],\n    ["First sorted at a hub", ...pct(now.hub, rest.hub, "the model assumes all of it")],\n    ["Handled here, sorted there", ...pct(now.xdock, rest.xdock,\n      "the green band in the first stage &mdash; the model builds it")],\n    ["Sorted once only", ...pct(now.single, rest.single, "the model gives every parcel two")],\n    ["Lodged in Victoria", ...pct(now.vic, rest.vic, "the model represents 3%")],\n    ["Outside the catchment", ...pct(now.region, rest.region, "regional Victoria — an upper bound, see the footer")],\n    ["Kept at the depot", ...pct(now.kept, rest.kept, "metro-lodged only — interstate and regional stock bands with its lodgement")],\n    filt ? ["Melbourne sorts", SORTLAB[SORTS], "this filter only"] : null\n  ].filter(Boolean).map(([k, v, sub]) =>\n    `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${sub}</div></div>`\n  ).join("");\n}\n\n// ── the ladder ── the same fold run at a set of thresholds, so the cost of each is visible\n// at once rather than one slider position at a time. The live threshold is always a row, even\n// when it is not one of the stops, and clicking a row moves the dial to it.\nconst LADDER = [1, 10, 25, 50, 100, 200, 350, 500, 750, 1000, 1500, 2000, 3000, 5000];\nconst DARKCOL = {1:"1st sort", 2:"2nd sort", 3:"despatch", 4:"depot"};\nfunction sweep(){\n  const base = cur().flows, live = THRESH;\n  const rest = measure(base);\n  const restNodes = new Set(base.flatMap(l => [l.s, l.t]));\n  const rows = [...new Set(LADDER.concat(live))].sort((a, b) => a - b).map(t => {\n    THRESH = t;\n    const f = fold(base), m = measure(f), moved = FOLDSTAT.moved;\n    const gone = [...restNodes].filter(id => !f.some(l => l.s === id || l.t === id));\n    return {t, m, moved, gone};\n  });\n  THRESH = live; fold(base);   // leave FOLDSTAT describing the threshold actually on screen\n  const d = (v, r, n, u) => v.toFixed(n) + (u || "")\n          + (Math.abs(v - r) < (n === 2 ? .005 : .05) ? ""\n          : ` <span style="color:var(--pdc)">${v > r ? "+" : ""}${(v - r).toFixed(n)}</span>`);\n  $("#sweeptable tbody").innerHTML = rows.map(r => {\n    // tagged with the column it went dark IN, which is the whole meaning: MPF losing its\n    // second-sort node is not MPF the building closing, and the two must not read alike\n    const names = [...new Set(r.gone.map(id => nodeMeta(id).label\n      + ` <span style="color:var(--ink-3)">${DARKCOL[COL_OF[id.split("_")[0]]]}</span>`))];\n    const lab = names.slice(0, 6).join(", ")\n              + (names.length > 6 ? ` <span style="color:var(--ink-3)">+${names.length - 6} more</span>` : "");\n    return `<tr class="${r.t === live ? "hit" : ""}" data-thr="${r.t}" style="cursor:pointer">\n      <td>${r.t === 1 ? "nothing folded" : fmt(r.t) + " articles"}</td>\n      <td class="n">${r.m.lanes}</td><td class="n">${r.m.nodes}</td>\n      <td class="n">${d(r.m.mean, rest.mean, 2)}</td>\n      <td class="n">${d(r.m.hub, rest.hub, 1, "%")}</td>\n      <td class="n">${d(r.m.xdock, rest.xdock, 1, "%")}</td>\n      <td class="n">${d(r.m.single, rest.single, 1, "%")}</td>\n      <td class="n">${r.moved ? fmt(r.moved) + " (" + (100 * r.moved / (5 * rest.T)).toFixed(1) + "%)" : "—"}</td>\n      <td style="white-space:normal">${lab || "—"}</td></tr>`;\n  }).join("");\n}\n$("#sweeptable").addEventListener("click", e => {\n  const tr = e.target.closest("tr[data-thr]");\n  if (tr) setThresh(tr.dataset.thr);\n});\n\nfunction table(raw, stageTot){\n  const rows = raw.map(l => {\n    const a = nodeMeta(l.s), b = nodeMeta(l.t);\n    return {stage: COLS[a.col] + " → " + COLS[b.col].toLowerCase(), col:a.col,\n            f:a.label, fs:a.sub, t:b.label, ts:b.sub, v:l.v, p:100 * l.v / stageTot[a.col]};\n  }).sort((x,y) => y.v - x.v);\n  $("#flowtable tbody").innerHTML = rows.map(r =>\n    `<tr><td><span class="tag">${["source&rarr;sort 1", "sort 1&rarr;2", "sort 2&rarr;out", "despatch&rarr;depot"][r.col]}</span></td>\n     <td>${r.f}${r.fs && r.f !== r.fs ? ` <span style="color:var(--ink-3)">${r.fs}</span>` : ""}</td>\n     <td>${r.t}${r.ts && r.t !== r.ts ? ` <span style="color:var(--ink-3)">${r.ts}</span>` : ""}</td>\n     <td class="n">${fmt(r.v)}</td><td class="n">${r.p.toFixed(2)}%</td></tr>`).join("");\n}\n\nconst syncCls = () => document.querySelectorAll("[data-cls]")\n  .forEach(x => x.setAttribute("aria-pressed", String(x.dataset.cls === CLS)));\n\ndocument.querySelectorAll("[data-cls]").forEach(b => b.addEventListener("click", () => {\n  CLS = b.dataset.cls;\n  history.replaceState(null, "", CLS === "ALL" ? location.pathname : "#" + CLS);\n  syncCls();\n  render();\n}));\ndocument.querySelectorAll("[data-sorts]").forEach(b => b.addEventListener("click", () => {\n  SORTS = b.dataset.sorts;\n  document.querySelectorAll("[data-sorts]").forEach(x => x.setAttribute("aria-pressed", String(x.dataset.sorts === SORTS)));\n  $("#sortnote").textContent = SORTS === "all" ? ""\n    : "Showing only parcels sorted " + (SORTS === "0" ? "nowhere in Melbourne"\n      : SORTS === "3+" ? "three or more times" : SORTS + (SORTS === "1" ? " time" : " times")) + ".";\n  render();\n}));\ndocument.querySelectorAll("[data-view]").forEach(b => b.addEventListener("click", () => {\n  VIEW = b.dataset.view;\n  document.querySelectorAll("[data-view]").forEach(x => x.setAttribute("aria-pressed", String(x.dataset.view === VIEW)));\n  $("#fig").hidden = VIEW !== "flow";\n  $("#tablewrap").hidden = VIEW !== "table";\n}));\nconst thrRange = $("#thr"), thrNum = $("#thrn");\nfunction setThresh(v){\n  const n = Math.round(Number(v));\n  THRESH = Math.max(1, Math.min(5000, Number.isFinite(n) ? n : 1));\n  thrRange.value = THRESH; thrNum.value = THRESH;\n  render();\n}\nthrRange.addEventListener("input", e => setThresh(e.target.value));\nthrNum.addEventListener("change", e => setThresh(e.target.value));\nsyncCls();\nrender();\n'


if __name__ == "__main__":
    main()
