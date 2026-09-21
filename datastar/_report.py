"""What each phase of the build actually produced — one function per step, one call per step.

WHY THIS IS A SEPARATE FILE. The seven build scripts already say a great deal as they run, but
what they say is a running commentary: a number at the moment it was decided, in the order the
code happened to decide it. That is the wrong shape for the question people actually ask a build
— "what came out of this step?" — and answering it inside those files would mean threading
statistics through code whose job is to build tables. So the commentary stays where it is, and
the SUMMARY lives here: every phase ends with one line, `_report.s1a(...)` or `_report.s2a()`,
and the block it prints is the phase's own report card.

WHERE THE NUMBERS COME FROM. Only two of these functions are handed anything. `s1a` takes the
reduction in memory, because 161,976 consignment itineraries cannot be recovered from the seven
small CSVs it writes. Everything else READS THE FOLDER THE PHASE JUST WROTE — which is the right
source anyway, since the question is what the phase produced, not what it believed it was
producing. A report that re-reads the output cannot drift from it.

    s1a(p, prov, meta, demand)   the measurement: scans, itineraries, origin, destination
    s2a() / s2b() / s2c()        the entity a build step wrote — one ledger, three times
    s3a() / s3b() / s3c()        a patch: what it changed in the folder it edits

The shape of the tables is the scan Sankeys' — volume, share, running cumulative — because that
is the table the manager reads and there should be one of it. It lives in `_log.dist`.
"""
import collections
import json

import numpy as np
import pandas as pd

from _log import banner, dist, get_logger, kv, pct, table, wrap
from _paths import CHAIN1_OUT, CHAIN2_OUT, FINAL_OUT, FOBS, SCAN_OUT

TOUCH_CAP = 5           # the last touch bucket is "5+" — past it the tail is 0.3% and unreadable
TOP_N = 8               # how many rows of a "biggest first" list are worth printing


def _by(series, weights):
    """Volume per value of `series`, biggest first. The one groupby every report below wants."""
    return weights.groupby(series).sum().sort_values(ascending=False)


def _read(folder, name):
    """One Anura table, or None. A phase may legitimately not write the optional ones.

    `low_memory=False` because the report reads whole columns anyway, and the chunked default
    emits a DtypeWarning on TransportationPolicies — a warning from the REPORT is pure noise
    next to the same warning from the build, which is about a table the build actually uses.
    """
    path = folder / f"{name}.csv"
    return (pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
            if path.exists() else None)


# ══════════════════════════════════════════════════════════════════════════════════════
#  S1A — THE MEASUREMENT
# ══════════════════════════════════════════════════════════════════════════════════════

def s1a(p, prov, meta, demand, log=None, raw=None):
    """The scan reduction's report card: what was read, what it says, what was written.

    `p` is the per-consignment table indexed by Consignment_ID, `prov` the provenance rows as
    written to _provenance.csv, `meta` the cohort metadata, `demand` the obs_demand rows.

    `raw` is the UNFILTERED measurement — the dict s1a's main() hands over — and it is optional
    because everything above the fold section reads only `p`. With it, the fold section can
    re-run both filters at every threshold the dial could be set to, which is the only way to
    say what the dial COSTS rather than merely what it did.
    """
    log = log or get_logger("s1a")
    prov = dict(prov)
    w = p.articles
    tot = int(w.sum())

    _scans(log)
    _reduction(log, p, w, tot, meta)
    _touches(log, p, w, tot, prov)
    _origin(log, p, w, tot)
    _destination(log, p, w, tot, prov)
    _sorts(log, p, w, tot)
    _fold(log, raw, meta)
    _exported(log, meta, demand, tot)


def _scans(log):
    """The extract itself — how many events, of what type, and how much freight each one sees."""
    path = SCAN_OUT / "event_coverage.json"
    if not path.exists():
        log.info("    (no event_coverage.json — run s1a with --rebuild to measure the extract)")
        return
    cov = json.loads(path.read_text())
    banner(log, "s1a · the scan extract", "what was read, before any rule was applied")
    kv(log, "scan events in the file", cov["scans"])
    kv(log, "distinct event types", cov["types"],
       f"{cov['physical_scans']:,} of the events are PHYSICAL (a building saw the parcel)")
    kv(log, "consignments", cov["consignments"])
    kv(log, "articles", cov["articles"],
       f"{cov['articles'] / max(cov['consignments'], 1):.2f} articles per consignment")
    log.info("")
    log.info("    coverage by event type — a scan is only usable as a stage marker if nearly")
    log.info("    every consignment has one, and if it sits where its name implies:")
    table(log, ["event", "scans", "consignments", "articles", "% of art", "per cons", "% named"],
          [[r["event"], f"{r['scans']:,}", f"{r['cons']:,}", f"{r['art']:,}",
            f"{r['art_pct']:.1f}%", f"{r['per_cons']:.2f}", f"{r['fac_pct']:.1f}%"]
           for r in sorted(cov["rows"], key=lambda r: -r["art"])],
          widths=[16, 10, 13, 10, 8, 8, 8])
    wrap(log, f"the chain OPENS on a LODGE scan for {cov['lodge_first']:.1f}% of articles and "
              f"CLOSES on a DELIVER for {cov['deliver_last']:.1f}%, so the sequence mostly means "
              f"what it looks like. What else opens it: "
              + ", ".join(f"{e} {v:.1f}%" for e, v in cov["opens"][1:4]) + ".")


def _reduction(log, p, w, tot, meta):
    """What survived into the reduction: one row per consignment, and which days it spans."""
    banner(log, "s1a · what the reduction carries",
           "one row per consignment that can be placed at a delivering depot")
    kv(log, "consignments in the reduction", len(p))
    kv(log, "articles (EA)", tot, "every delivery date in the extract")
    for cls, v in _by(p.cls, w).items():
        kv(log, f"  class {cls}", int(v), pct(v, tot) + " of the freight")
    days = _by(p.delivery_date, w)
    peak = meta.get("peak_day")
    kv(log, "delivery dates spanned", int(p.delivery_date.nunique()),
       f"modal day {peak} carries {pct(days.max(), tot)} of the volume")
    kv(log, "cohort exported", str(meta.get("cohort")),
       f"{meta.get('cohort_articles', 0):,} EA — the basis every factor below is measured on")


def _touches(log, p, w, tot, prov):
    """BUILDINGS TOUCHED. The question the itinerary exists to answer, and the model's cap."""
    banner(log, "s1a · buildings touched",
           f"{prov.get('path_touch_bar', '?').upper()} bar — "
           + str(prov.get("path_touch_events", "")))
    wrap(log, "a parcel's path is the buildings OF OURS it was inside before it reached the "
              "depot that delivered it. The delivering depot is NOT counted here — it is the "
              "column the path stops at — so add one to every bucket for the whole journey.")
    n = p.path_n.clip(upper=TOUCH_CAP)
    reasons = {"depot_first": "went straight to its own depot",
               "interstate_only": "every building before the depot was interstate",
               "outside_only": "only touched buildings the model does not carry",
               "no_scan": "no qualifying scan at all on this bar"}

    def why(lab):
        if str(lab) != "0":
            return []
        z = p.path_why[p.path_why != "path"]
        return [f"{int(w[p.path_why == r].sum()):>11,} {pct(w[p.path_why == r].sum(), tot):>6}"
                f"   {reasons.get(r, r)}" for r in z.value_counts().index]

    dist(log, [(f"{k}+" if k == TOUCH_CAP else str(k), int(w[n == k].sum()))
               for k in range(TOUCH_CAP + 1)], tot, head="buildings", note=why)
    mean = float((p.path_n * w).sum() / tot)
    wrap(log, f"mean {mean:.2f} buildings before the depot, so {mean + 1:.2f} for the whole "
              f"journey once the delivering depot is counted. The model carries "
              f"PATH_DEPTH={prov.get('path_depth')} of them ({prov.get('path_cap_rule')}).")
    folded = int(prov.get("path_interior_folded_ea", 0) or 0)
    if folded:
        wrap(log, f"{folded:,} interior touches are FOLDED by that cap: the first building and "
                  f"the last are kept as measured, and what is invented is the directness of "
                  f"the middle.")
    wrap(log, f"cut from the journey — "
              f"{int(prov.get('path_before_vic_ea', 0) or 0):,} EA arrived through another "
              f"state, {int(prov.get('path_after_depot_ea', 0) or 0):,} EA were seen somewhere "
              f"after their depot, and "
              f"{int(prov.get('path_fell_outside_ea', 0) or 0):,} EA passed through a building "
              f"the model does not carry.")
    _vs_sankey(log, p, w, tot, mean)


def _vs_sankey(log, p, w, tot, mean):
    """Why this table and sankey-facility-path.html disagree about the same day.

    They disagree because they answer different questions, and someone reading the two side by
    side deserves the bridge rather than a doubt. THREE differences, and all three are on
    purpose:

      the DENOMINATOR — this table is the whole reduction; the page draws the cohort, which
      drops the articles no rule could name an origin for;
      the NODE SET — `path_n` counts buildings the MODEL carries, because a lane the model has
      no node for is a lane it cannot route; the page draws every Victorian building, because
      a picture that silently deletes a building the parcel really was in is a lie about the day;
      the REVISIT COLLAPSE, worth tens of articles between adjacent buckets and nothing to the
      total — the page collapses a lane from a building to itself after the node filter, so the
      two orderings shuffle a little volume between buckets 2 and up.

    The numbers are computed here rather than quoted, so this cannot go stale when either side
    moves. The page's own mean is `path_n + path_fell` over the cohort — the same arithmetic it
    does, off the same columns.
    """
    coh = p[p.origin != "UNKNOWN"]
    if not len(coh) or coh.articles.sum() == tot and not p.path_fell.any():
        return
    cw = coh.articles
    drawn = float(((coh.path_n + coh.path_fell) * cw).sum() / cw.sum())
    log.info("")
    wrap(log, f"READ AGAINST THE SANKEY. sankey-facility-path.html prints {drawn:.2f} buildings "
              f"for this same day, not {mean:.2f}, and both are right — they count different "
              f"things. The bridge, in full:")
    kv(log, "  this table", tot, "every row of the reduction; buildings the MODEL carries")
    kv(log, "  the page", int(cw.sum()),
       f"the cohort ({int(w.sum() - cw.sum()):,} EA have no nameable origin); EVERY Vic building")
    kv(log, "  the gap in the mean", f"{drawn - mean:+.2f}",
       f"{int(w[p.path_fell > 0].sum()):,} EA pass through a building we have no node for")


def _origin(log, p, w, tot):
    """WHERE IT WAS LODGED — and how much of that is ZPT_LODGE rather than an inference."""
    banner(log, "s1a · origin", "which rule named the state the parcel was lodged in")
    rules = {"lodge": "ZPT_LODGE scan — MEASURED, the parcel's own lodgement event",
             "first_scan": "first presence scan carrying a state — INFERRED",
             "none": "neither; tagged UNKNOWN and dropped by the cohort"}
    for k in ("lodge", "first_scan", "none"):
        v = int(w[p.origin_from == k].sum())
        kv(log, f"  {k}", v, f"{pct(v, tot):>6}  {rules[k]}")
    log.info("")
    log.info("    the band each parcel is sourced from, which is what the model's families are:")
    bands = {"INT": "interstate — lodged in another state",
             "METRO": "metro Melbourne — lodged inside the catchment polygon",
             "REGION": "regional Victoria, OR Victorian and unplaceable (an upper bound)",
             "UNKNOWN": "no state on any scan"}
    dist(log, [(b, int(w[p.lodge_band == b].sum())) for b in ("INT", "METRO", "REGION", "UNKNOWN")
               if (p.lodge_band == b).any()], tot, head="band", tail=bands)
    log.info("    METRO vs REGION is decided on the lodgement point, placed by:")
    for k, v in _by(p.lodge_geo_from, w).items():
        kv(log, f"  {k}", int(v), pct(v, tot))


def _destination(log, p, w, tot, prov):
    """WHERE IT WAS DELIVERED — the three-rung ladder, and what naming a depot cost."""
    banner(log, "s1a · destination",
           f"delivering depot from {prov.get('delivering_depot_basis', '?')}")
    rungs = {"deliver_scan": "the DELIVER scan names a depot — MEASURED",
             "accept_scan": "the last ACCEPT_FACILITY names one — MEASURED, one rung down",
             "plan": "neither did; the planned terminating facility — ASSUMED"}
    for k in ("deliver_scan", "accept_scan", "plan"):
        if (p.pdc_from == k).any():
            v = int(w[p.pdc_from == k].sum())
            kv(log, f"  {k}", v, f"{pct(v, tot):>6}  {rungs.get(k, '')}")
    path = SCAN_OUT / "pdc_basis.json"
    if path.exists():
        b = json.loads(path.read_text())
        log.info("")
        kv(log, "articles in the raw extract", b["extract"])
        kv(log, "kept — a depot could be named", b["kept"], pct(b["kept"], b["extract"]))
        kv(log, "dropped — no rung named one", b["extract"] - b["kept"],
           pct(b["extract"] - b["kept"], b["extract"]) + "  out of the reduction entirely")
        if b.get("disagree"):
            kv(log, "scan disagrees with the plan", b["disagree"],
               f"net {b.get('moved', 0):,} EA actually change depot")
        # `lost` in pdc_basis.json is a COUNTERFACTUAL, not this basis's loss: it is what the
        # deliver scan ALONE would have cost, measured every rebuild so the question can be
        # re-asked without one. Reporting it as "lost" made a 12.1% loss out of a 0.8% one.
        log.info("")
        wrap(log, f"what rungs 2 and 3 are worth: on the DELIVER SCAN ALONE "
                  f"{b['lost']:,} EA ({pct(b['lost'], b['extract'])}) could not be placed — "
                  f"{b['lost_no_scan']:,} EA have no delivery scan at all, and "
                  f"{b['lost_off_network']:,} EA were delivered by one of "
                  f"{b.get('off_network_names', 0)} facilities the model has no node for. The "
                  f"ladder recovers all but the {b['extract'] - b['kept']:,} EA above.")
    log.info("")
    log.info("    the delivering depots, by volume:")
    top = _by(p.pdc, w)
    table(log, ["depot", "articles", "share"],
          [[d, f"{int(v):,}", pct(v, tot)] for d, v in top.items()],
          widths=[28, 12, 8])


def _sorts(log, p, w, tot):
    """SORTS AND THE STAGE — how often the freight was sorted, carried sideways, or slept."""
    banner(log, "s1a · sorts, cross-dock and the overnight stage",
           "what happened between lodgement and delivery")
    n = p.nrounds.clip(upper=TOUCH_CAP)
    dist(log, [(f"{k}+" if k == TOUCH_CAP else str(k), int(w[n == k].sum()))
               for k in range(TOUCH_CAP + 1)], tot, head="sorts")
    log.info(f"    mean {(p.nrounds * w).sum() / tot:.2f} machine sorts per article at a "
             f"building the model carries")
    xd = int(w[p.crossdocked].sum())
    kv(log, "cross-docked", xd,
       f"{pct(xd, tot):>6}  handled at one building, first SORTED at another")
    kept = int(w[p.kept_on_site].sum())
    kv(log, "kept on site overnight", kept,
       f"{pct(kept, tot):>6}  the measured stage — standing at the delivering depot")
    if kept:
        by = _by(p.lodge_band[p.kept_on_site], w[p.kept_on_site])
        log.info("    the stage is not local stock: it is lodged, by band — "
                 + ", ".join(f"{b} {pct(v, kept)}" for b, v in by.items()))


# ── THE FOLD, AND WHAT EVERY OTHER SETTING OF IT WOULD COST ──────────────────────────
# The steps are the scan Sankey's, and for its reason: fine where the fold actually changes the
# model, coarse where it has stopped changing it. 0 is the baseline row — nothing folded — and it
# is what makes the rest of the column readable, because the volume the OTHER rules move is
# already sitting in it before the dial has done anything at all.
FOLD_STEPS = [0, 25, 50, 100, 150, 200, 300, 400, 500, 750, 1000, 1500, 2000]


def _fold(log, raw, meta):
    """What FOLD_MIN_ARTICLES costs — at its current setting, and at every other one.

    One dial drives TWO folds and they are not the same operation, so both are reported:

      the CELL fold (filter_factors) drops a family/class/site FLAVOUR whose whole volume is
      under the dial, and renormalises what is left within its own origin family;
      the LANE fold (filter_stages) drops a physical (from, to) BUILDING PAIR under it, and the
      freight rides the lanes that survive.

    The sweep re-runs both filters from the unfiltered measurement at every row — never
    interpolated, never cached — so a row says exactly what the build would write at that dial.
    """
    if not raw:
        return
    # Imported HERE, not at module scope: s1a imports this module, so a top-level import back
    # into it is a cycle. The filters are pure functions of their arguments, which is the whole
    # reason a sweep is possible at all.
    from s1a_export_chain2_factors import (filter_factors, filter_stages, lane_ledger,
                                           round2_sites)

    joint, single, second = raw["joint"], raw["single"], raw["second"]
    recv_entry, legs, deliver_rows = raw["recv_entry"], raw["legs"], raw["deliver_rows"]
    dials = raw["dials"]
    live = dials["fold"]
    cohort = meta.get("cohort_articles", 0) or 1
    r2_allowed = {s for s, _, _ in round2_sites(second, dials["r2_min"])}
    ledger = lane_ledger(recv_entry, legs, deliver_rows)
    steps = int(sum(ledger.values()))

    banner(log, "s1a · the chain-2 fold", f"FOLD_MIN_ARTICLES = {live:,} EA")
    wrap(log, "one dial drives two folds. The CELL fold drops a family/class/site flavour whose "
              "whole volume is under it; the LANE fold drops a physical (from, to) building pair "
              "under it and the freight rides the lanes that survive. A lane is tested ACROSS "
              "classes, which is the unit the dial is written in.")
    log.info("")
    kv(log, "physical lanes before any fold", len(ledger),
       "  ".join(f"{f or 'despatch'} {sum(1 for k in ledger if k[0] == f)}"
                 for f in sorted({k[0] for k in ledger}, key=lambda x: (x is None, x))))
    kv(log, "article-steps on them", steps,
       f"{steps / cohort:.2f} lanes per article — a parcel counts on every lane it rides")

    def run(t):
        """Both filters at one threshold, or None where the fold makes the model impossible."""
        try:
            _, _, dead, cell_ea = filter_factors(joint, single, t)
            _, r2, dl, moved = filter_stages(recv_entry, legs, deliver_rows, r2_allowed, dead, t)
        except AssertionError:
            return None                 # the fold removed every same-day cell at some depot
        thin = [v for v in ledger.values() if v < t]
        # the kept lanes, split the way the two output tables split them: a lane whose family is
        # None is a despatch run to a depot (obs_delivery), anything else is a sort leg (obs_legs)
        fat = [k for k, v in ledger.items() if v >= t]
        return dict(kept=len(fat), folded=len(thin),
                    kept_legs=sum(1 for k in fat if k[0] is not None),
                    kept_desp=sum(1 for k in fat if k[0] is None),
                    # stage 4 keeps a despatch site's BIGGEST lane even when nothing it runs
                    # clears the bar — emptying a building is not a decision the fold may make.
                    # Without this line the report looks self-contradictory: more obs_delivery
                    # rows than there are kept lanes to put them on.
                    exempt=sum(1 for _c, x, pud, _a, _s in dl if ledger[(None, x, pud)] < t),
                    lane_ea=int(sum(moved.values())), cell_ea=int(cell_ea), dead=len(dead),
                    legs=len([r for r in r2 if r[3] != "ONCE"]), deliver=len(dl))

    now = run(live)
    if now:
        log.info("")
        log.info(f"    at the live setting, {live:,} EA:")
        kv(log, "  lanes kept", now["kept"], pct(now["kept"], len(ledger))
           + f"  — {now['kept_legs']} sort legs + {now['kept_desp']} despatch runs")
        kv(log, "  lanes folded", now["folded"], pct(now["folded"], len(ledger))
           + "  their freight rides the lanes that survive")
        kv(log, "  flavours dropped", now["dead"],
           f"{now['cell_ea']:,} EA ({pct(now['cell_ea'], cohort)}) reassigned within its family")
        kv(log, "  volume refolded onto other lanes", now["lane_ea"],
           f"{pct(now['lane_ea'], cohort)} of the cohort")
        kv(log, "  obs_legs rows written", now["legs"],
           f"obs_delivery {now['deliver']:,} — ROWS, one per class, so more than the lanes above")
        if now["exempt"]:
            wrap(log, f"{now['exempt']} of those obs_delivery rows ride a lane UNDER the dial: "
                      f"stage 4 keeps a despatch site's biggest lane even when nothing it runs "
                      f"clears the bar, because emptying a building is not a decision the fold "
                      f"is allowed to make. They are the reason the row count can exceed the "
                      f"kept-lane count.", indent="      ")

    log.info("")
    wrap(log, "what the dial would cost at every other setting. Both filters are re-run from the "
              "unfiltered measurement at each row, so a row is what the build WOULD write, not "
              "an interpolation. Watch the refolded column against row 0: that baseline is volume "
              "the OTHER rules move — an unreachable round-2 site, a dropped flavour — and only "
              "the excess above it is the fold's own doing.")
    rows = []
    for t in sorted(set(FOLD_STEPS) | {live}):
        r = run(t)
        mark = "<- live" if t == live else ""
        if r is None:
            rows.append([f"{t:,}", "—", "—", "INFEASIBLE", "—", "—", "—", mark])
            continue
        rows.append([f"{t:,}" + (" none" if t == 0 else ""), f"{r['kept']:,}",
                     f"{r['folded']:,}", f"{r['lane_ea']:,}", pct(r["lane_ea"], cohort),
                     f"{r['legs']:,}", f"{r['deliver']:,}", mark])
    table(log, ["fold under", "lanes kept", "folded", "EA refolded", "% of day",
                "obs_legs", "obs_deliv", ""],
          rows, widths=[10, 10, 7, 11, 8, 8, 9, 7], align="lrrrrrrl")
    if any(r[3] == "INFEASIBLE" for r in rows):
        wrap(log, "INFEASIBLE means filter_factors asserted: at that threshold the fold removes "
                  "every same-day cell from some depot x class, leaving a row that is nothing but "
                  "overnight stage. That is the ceiling on this dial, not a bug.")


def _exported(log, meta, demand, tot):
    """WHAT WAS WRITTEN — the factor CSVs the rest of the build reads."""
    banner(log, "s1a · exported factors", f"inputs/{FOBS.name}/ — never hand-edit")
    rows = []
    for f in sorted(FOBS.glob("*.csv")):
        rows.append([f.name, f"{sum(1 for _ in f.open(encoding='utf-8-sig')) - 1:,}",
                     f"{f.stat().st_size / 1024:.1f} KB"])
    table(log, ["table", "rows", "size"], rows, widths=[26, 8, 10])
    d = sum(a for _, _, a in demand)
    kv(log, "measured delivery exported", d,
       f"{pct(d, tot)} of the reduction — what chain 2 is built to carry")


# ══════════════════════════════════════════════════════════════════════════════════════
#  S2 — THE ENTITIES.  One ledger, read off the folder the step just wrote.
# ══════════════════════════════════════════════════════════════════════════════════════

def _family(names, depth=2):
    """`SUP_STAGE_MPF` -> `SUP_STAGE`; `CZ_PUD_Abbotsford_1` -> `CZ_PUD`. The ledger's rows."""
    return names.str.split("_").str[:depth].str.join("_")


def model(folder, title, sub="", log=None):
    """The entity a build step wrote: its tables, its products, and both sides of its ledger."""
    log = log or get_logger("s2")
    if not folder.exists():
        log.info(f"    (nothing to report — {folder} does not exist)")
        return
    banner(log, title, sub or str(folder.name))

    files = sorted(folder.glob("*.csv"))
    rows = {f.stem: sum(1 for _ in f.open(encoding="utf-8-sig")) - 1 for f in files}
    kv(log, "tables written", len(files), f"{sum(rows.values()):,} rows in total")
    names = sorted(rows)
    for i in range(0, len(names), 3):     # three to a line — twenty tables is five lines, not twenty
        log.info(("      " + "".join(f"{t:<22}{rows[t]:>9,}   " for t in names[i:i + 3])).rstrip())

    _products(log, folder)
    _supply(log, folder)
    _demand(log, folder)
    _network(log, folder)


def _products(log, folder):
    pr = _read(folder, "Products")
    if pr is None:
        return
    log.info("")
    # the stage is the tail of the name — _Pickup, _Sorted, _Despatch1_MPF — and it is the
    # parcel's STATE, which is what tells you how many times the model handles one parcel.
    stage = pr.productname.str.rsplit("_", n=1).str[-1]
    stage = np.where(pr.productname.str.contains("Despatch1_"), "Despatch1",
             np.where(pr.productname.str.contains("Despatch2"), "Despatch2", stage))
    c = collections.Counter(stage)
    kv(log, "products (parcel states)", len(pr),
       "  ".join(f"{k} {v}" for k, v in c.most_common(TOP_N)))


def _supply(log, folder):
    """The IN side: what the entity is allowed to source, by family, in EA."""
    sc = _read(folder, "SupplierCapabilities")
    if sc is None or sc.empty:
        return
    cap = pd.to_numeric(sc.supplycapacity, errors="coerce").fillna(0)
    tot = int(cap.sum())
    log.info("")
    log.info(f"    SUPPLY — {tot:,} EA over {sc.suppliername.nunique():,} suppliers, by family:")
    g = cap.groupby(_family(sc.suppliername)).sum().sort_values(ascending=False)
    dist(log, [(k, int(v)) for k, v in g.items()], tot, head="family", unit="EA")


def _demand(log, folder):
    """The OUT side: where the entity is required to put the freight."""
    cd = _read(folder, "CustomerDemand")
    if cd is None or cd.empty:
        return
    q = pd.to_numeric(cd.quantity, errors="coerce").fillna(0)
    tot = int(q.sum())
    log.info("")
    log.info(f"    DEMAND — {tot:,} EA over {cd.customername.nunique():,} sinks, "
             f"{len(cd):,} rows, by sink family:")
    g = q.groupby(_family(cd.customername)).sum().sort_values(ascending=False)
    dist(log, [(k, int(v)) for k, v in g.items()], tot, head="sink", unit="EA")


def _network(log, folder):
    """The machinery: lanes, work centres, processes, and the constraints that bound them."""
    log.info("")
    tp = _read(folder, "TransportationPolicies")
    if tp is not None and not tp.empty:
        modes = collections.Counter(tp.modename.fillna("—"))
        kv(log, "lanes", len(tp),
           f"{tp.originname.nunique():,} origins x {tp.destinationname.nunique():,} "
           f"destinations;  "
           + "  ".join(f"{m} {c:,}" for m, c in modes.most_common(4)))
    wc = _read(folder, "WorkCenters")
    if wc is not None and not wc.empty:
        cap = pd.to_numeric(wc.throughputcapacity, errors="coerce")
        kv(log, "work centres", len(wc),
           f"over {wc.facilityname.nunique()} facilities;  "
           f"{int(cap.notna().sum())} carry a throughput cap, {int(cap.sum()):,} EA in total")
    pc = _read(folder, "Processes")
    if pc is not None and not pc.empty:
        kv(log, "processes", pc.processname.nunique(), f"{len(pc):,} process steps")
    fc = _read(folder, "FlowConstraints")
    if fc is not None and not fc.empty:
        c = collections.Counter(fc.constrainttype.fillna("—"))
        kv(log, "flow constraints", len(fc), "  ".join(f"{k} {v:,}" for k, v in c.most_common()))
    fa = _read(folder, "Facilities")
    if fa is not None and not fa.empty:
        cap = pd.to_numeric(fa.throughputcapacity, errors="coerce")
        kv(log, "facilities", len(fa), f"{int(cap.notna().sum())} with a throughput cap")


def s2a(log=None):
    model(CHAIN2_OUT, "s2a · chain 2 — the delivery entity",
          "entirely measured: sources -> sort -> delivery", log=log)


def s2b(log=None):
    model(CHAIN1_OUT, "s2b · chain 1 — the collection entity",
          "assumed: pickup -> sortation -> terminate", log=log)


def s2c(log=None):
    model(FINAL_OUT, "s2c · the combined model",
          "both entities, the folder Cosmic Frog takes", log=log)


# ══════════════════════════════════════════════════════════════════════════════════════
#  S3 — THE PATCHES.  They EDIT the final folder, so the report is what moved.
# ══════════════════════════════════════════════════════════════════════════════════════

def patch(title, sub, tables, log=None):
    """A post-process step's report: the state of the model AFTER the patch went in.

    A patch edits `outputs/melbourne_optilogic_final/` in place, so there is no before-folder to
    diff against — the step's own commentary above says what it moved. What this block answers
    is the question the commentary cannot: what does the model look like NOW, and in particular
    what is the shape of the rule set the three patches are collectively building.
    """
    log = log or get_logger("s3")
    banner(log, title, sub)
    rows = [[t, "—" if (df := _read(FINAL_OUT, t)) is None else f"{len(df):,}"] for t in tables]
    table(log, ["table", "rows now"], rows, widths=[32, 10])
    _constraints(log)


def _constraints(log):
    """The rule set, by what a row DOES. A Max of 0 blocks a lane; a Max above it is a ceiling.

    Counting by `constrainttype` alone hides the whole point of s3b: 947 Max rows sounds like
    947 ceilings, and 889 of them are flat prohibitions. The value is what separates them.
    """
    fc = _read(FINAL_OUT, "FlowConstraints")
    if fc is None or fc.empty:
        return
    v = pd.to_numeric(fc.constraintvalue, errors="coerce").fillna(0)
    kind = fc.constrainttype.fillna("—")
    buckets = [("Max at 0", (kind == "Max") & (v == 0), "a lane the site may not use at all"),
               ("Max above 0", (kind == "Max") & (v > 0), "a ceiling on a lane it may use"),
               ("Min above 0", (kind == "Min") & (v > 0), "a floor — volume that MUST move"),
               ("Min at 0", (kind == "Min") & (v == 0), "inert")]
    log.info("")
    log.info(f"    the rule set now in FlowConstraints — {len(fc):,} rows, by what a row does:")
    rows = [[lab, f"{int(m.sum()):,}", f"{int(v[m].sum()):,}", why]
            for lab, m, why in buckets if m.any()]
    table(log, ["constraint", "rows", "EA bounded", "what it means"], rows,
          widths=[14, 8, 12, 3], align="lrrl")


def s3a(log=None):
    patch("s3a · a Despatch2 per route",
          "one product per despatch flavour, so a site cannot relay what it did not make",
          ["Products", "BillOfMaterials", "ProductionPolicies",
           "ReplenishmentPolicies", "TransportationPolicies"], log)


def s3b(log=None):
    patch("s3b · a site despatches only what it makes",
          "Max-0 rows on every lane a site has no recipe for",
          ["FlowConstraints"], log)


def s3c(log=None):
    patch("s3c · round-2 pinned on the measured share",
          "the sort band, narrowed onto the scans",
          ["FlowConstraints"], log)
