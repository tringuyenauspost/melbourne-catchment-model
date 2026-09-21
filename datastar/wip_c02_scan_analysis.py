"""PART 1 of 2 — analyse the cleaned scan table and publish the measurement tables.

Builds the scan reduction with `scan_reduction.py` (a standalone copy of the measurement
code — this folder imports nothing from pipeline/) and reports what it found. Writes one
DataStar table per breakdown, plus the per-consignment reduction itself, which PART 2 reads.

Needs `scan_reduction.py` beside it.

Input  : Wip_C02_ScanClean   (read from the sandbox)
Outputs: Wip_C02_ConsignmentPaths  + the Wip_C02_* analysis tables below

No reduction exists yet when this runs -- this script builds it.

THE REPORT, in the order it prints:
  * the reduction's steps, and which one of them removes volume (exactly one does);
  * a ledger from the clean table to the published reduction, step by step, with what
    each step removed -- 167,445 articles in, 166,159 out;
  * the depot ladder that does the removing, and what it cost each depot;
  * the steps that LABEL but never filter, so a band is not read as a loss;
  * ONE BUILDING, SEVERAL NAMES -- every site the scans spell more than one way, with
    each name's volume and why it folds; then the six original breakdowns.

The facility tables come in two shapes on purpose: Wip_C02_FacilityCensus is one row per
NAME, Wip_C02_FacilityByBuilding is one row per BUILDING. Bayswater is two middling rows
in the first and one large row in the second, and only the second is the site.
"""

import logging
import sys

import numpy as np
import pandas as pd

from datastar import Project

import scan_reduction as reduction

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

PROJECT_NAME = "Temp_FY26_Melbourne"
SOURCE_TABLE = "Wip_C02_ScanClean"

PATHS_TABLE = "Wip_C02_ConsignmentPaths"
# Origin bands, in the vocabulary asked for. KEPT_METRO is the reduction's stage band:
# lodged at the depot that delivered it, so it never entered the sort network.
ORIGIN_BAND = {"INT": "Interstate", "KEPT_METRO": "Kept at depot",
               "METRO": "Intrastate", "REGION": "Intrastate", "UNKNOWN": "Unknown"}
# 1 -> 100 in tens, 100 -> 1,000 in hundreds, 1,000 -> 2,000 in five-hundreds. 0 is kept as
# the baseline row: it is the volume a lane loses to the depth cap alone, before the fold takes
# anything, so every other row reads as the fold's own doing.
LANE_THRESHOLDS = ((0,) + tuple(range(10, 100, 10)) + tuple(range(100, 1000, 100))
                   + tuple(range(1000, 2001, 500)))


PCT_DP = 2      # published percentages are rounded; 46.286649799077246 is not a number
# tables logged as a top-N instead of in full — one row per facility NAME, thousands of them
LOG_HEAD = {"Wip_C02_FacilityCensus": 40, "Wip_C02_FacilityByBuilding": 40}


def publish(sandbox, df, name):
    """Write a table with LOWERCASE column names.

    Starburst folds unquoted identifiers to lowercase, so a mixed-case column is created as
    `lodged_int` and then the INSERT -- which SQLAlchemy quotes as "lodged_INT" because it
    needs quoting -- cannot find it. Lowercasing everything on the way out sidesteps the
    whole class of failure. scan_reduction._canon_columns() restores the reduction's own
    column names when PART 2 reads the table back.
    """
    out = df.reset_index()
    lower = [str(c).lower() for c in out.columns]
    dupes = {c for c in lower if lower.count(c) > 1}
    if dupes:
        raise SystemExit(f"{name}: columns collide once lowercased: {sorted(dupes)}")
    out.columns = lower

    if out.empty:
        raise ValueError(f"Refusing to replace {name} with an empty dataframe")

    # The old table is cleared by the macro's "run SQL" task, which runs BEFORE this script:
    # the sandbox object itself has no way to execute SQL (read_table / write_table only).
    sandbox.write_table(out, name)
    return out


def pct(part, whole):
    return round(100.0 * part / whole, PCT_DP) if whole else 0.0


def log_frame(df, title, floatfmt="{:,.1f}", limit=None):
    """Log a DataFrame as a fixed-width table. `limit` logs only the top rows.

    The facility tables carry one row per name in the extract -- 3,300 of them, which is
    6,600 log lines for two tables and buries everything else. They are capped in the log
    and published WHOLE; nothing is lost, it just has to be read out of the table.
    """
    logger.info("")
    logger.info("=== %s ===", title)
    # Every cell becomes a STRING before it meets a format spec. pandas 3 turns a list with
    # None into str dtype holding NaN (a float, which formats); pandas 2 keeps real None, and
    # f"{None:>12}" raises TypeError. FacilityCensus has None in three columns, so the table
    # printer has to be version-independent rather than lucky.
    def cell(v, fmt):
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        try:
            if pd.isna(v):
                return ""
        except (TypeError, ValueError):
            return str(v)
        return fmt.format(v) if fmt else str(v)

    more = 0
    if limit is not None and len(df) > limit:
        more, df = len(df) - limit, df.head(limit)
    show = pd.DataFrame(index=df.index)
    for c in df.columns:
        col = df[c]
        fmt = (floatfmt if pd.api.types.is_float_dtype(col)
               else "{:,}" if pd.api.types.is_integer_dtype(col) else None)
        show[c] = [cell(v, fmt) for v in col]
    if show.empty:
        logger.info("  (no rows)")
        return
    widths = {c: max(len(str(c)), int(show[c].str.len().max() or 0)) for c in show.columns}
    idxw = max(len(str(i)) for i in show.index)
    logger.info("  %s  %s", " " * idxw, "  ".join(f"{c:>{widths[c]}}" for c in show.columns))
    for i, row in show.iterrows():
        logger.info("  %-*s  %s", idxw, str(i),
                    "  ".join(f"{row[c]:>{widths[c]}}" for c in show.columns))
    if more:
        logger.info("  ... %s more rows — the published table holds all of them", f"{more:,}")


def share_table(df, key, weight, name):
    """Absolute + percentage breakdown of `weight` by `key`."""
    g = df.groupby(key, dropna=False)[weight].agg(["sum", "size"])
    out = pd.DataFrame({"articles": g["sum"].astype("int64"),
                        "consignments": g["size"].astype("int64")})
    out["articles_pct"] = out["articles"].map(lambda v: pct(v, out["articles"].sum()))
    out["consignments_pct"] = out["consignments"].map(
        lambda v: pct(v, out["consignments"].sum()))
    out.index.name = name
    return out.sort_values("articles", ascending=False)


# ------------------------------------------------- what the reduction does, step by step

def log_logic():
    """The reduction's steps, in the order a parcel experiences them.

    Printed every run because the two questions this script is always asked -- "what did you
    do to the data" and "where did the missing parcels go" -- are answered by the same list.
    ONE step removes volume; the rest only label it.
    """
    r = reduction
    logger.info("")
    logger.info("=== WHAT THE REDUCTION DOES ===")
    logger.info("  Wip_C02_ScanClean is 2.96 M scan events. The reduction turns them into ONE")
    logger.info("  ROW PER CONSIGNMENT, in these steps:")
    logger.info("")
    logger.info("  0   read every scan, ordered by Event_seq (the dates are database dates,")
    logger.info("      not movement dates, so they are never used for ordering)")
    logger.info("  0b  IDENTITY -- product, articles, and WHICH DEPOT DELIVERED IT, off a")
    logger.info("      three-rung ladder (PDC_BASIS=%r):", r.PDC_BASIS)
    logger.info("        1. the last ZPT_DELIVER scan at one of our %d depots", len(r.MODEL_PDCS))
    logger.info("        2. the last %s scan at one of our depots", r.ACCEPT_EVENT)
    logger.info("        3. Terminating_facility_name -- the plan")
    logger.info("      *** THE ONLY STEP THAT REMOVES VOLUME *** a parcel that reaches rung 3")
    logger.info("      with no depot scan on either event, and no contractor base to explain")
    logger.info("      it, cannot be placed and leaves the reduction (DROP_UNSCANNED_DEPOT=%s).",
                r.DROP_UNSCANNED_DEPOT)
    logger.info("  0c  PRESENCE -- the scans that put the parcel in a building, consecutive")
    logger.info("      repeats collapsed, so the sequence lists BUILDINGS not scans")
    logger.info("  0d  ITINERARY -- the Victorian buildings it was in before its depot")
    logger.info("      (PATH_BASIS=%r, evidence bar %r, PATH_DEPTH=%d %s). Facility names are",
                r.PATH_BASIS, r.PATH_TOUCH_BAR, r.PATH_DEPTH, r.PATH_CAP_RULE)
    logger.info("      folded onto one name per building first -- see ONE BUILDING, SEVERAL NAMES.")
    logger.info("  1   SOURCE -- where it was lodged. State off the lodge scan, else off the")
    logger.info("      first presence scan that carries one; then Victorian lodgements are put")
    logger.info("      on the map and asked which side of the first-mile boundary they fall.")
    logger.info("      Bands INT / METRO / REGION / UNKNOWN (VIC_UNPLACED_BAND=%r).",
                r.VIC_UNPLACED_BAND)
    logger.info("  2   SORT -- how many times Melbourne machine-sorted it and where, which")
    logger.info("      site received it, and whether that is a cross-dock")
    logger.info("  3   DELIVERY -- the SINK: which depot delivered it, and whether it slept")
    logger.info("      there first (kept_on_site). Gives every parcel its source band.")
    logger.info("")
    logger.info("  Steps 0c-3 CLASSIFY, they never filter: a parcel with no lodge scan, no")
    logger.info("  sort and no path keeps its row and is labelled UNKNOWN / 0 sorts / no_scan.")
    logger.info("  PART 2 filters again when it builds the factors: cohort() drops")
    logger.info("  origin=UNKNOWN (DROP_UNPLACED_ENTRY=%s), and the lane fold drops small lanes",
                r.DROP_UNPLACED_ENTRY)
    logger.info("  -- Wip_C02_LaneThresholds prices that before it is chosen.")


def reduction_ledger(scans, attr, p):
    """Volume at each step, and what that step removed. Articles are the unit that matters.

    `attr` is the identity table BEFORE the depot ladder drops anything, which is why this
    script builds it itself rather than reading it out of build_paths().
    """
    ev_per_cons = scans.groupby("Consignment_ID").size()
    kept = attr[attr.pdc.notna()]

    def row(label, idx, articles):
        return {"step": label, "events": int(ev_per_cons.reindex(idx).fillna(0).sum()),
                "consignments": len(idx), "articles": int(articles)}

    rows = [
        row(f"0.  {SOURCE_TABLE} as read", attr.index, attr.articles.sum()),
        row("0b. collapsed to one row per consignment", attr.index, attr.articles.sum()),
        row("1.  + a delivering depot can be named", kept.index, kept.articles.sum()),
        row("2.  reduction published (FINAL)", p.Consignment_ID, p.articles.sum()),
    ]
    out = pd.DataFrame(rows).set_index("step")
    for c in ("events", "consignments", "articles"):
        out[f"{c}_removed"] = (out[c].shift() - out[c]).fillna(0).astype("int64")
    out["articles_pct_of_clean"] = out["articles"].map(lambda v: pct(v, rows[0]["articles"]))
    return out


def log_depot_ladder(attr):
    """How each surviving parcel got its depot, and what the one drop cost, by depot.

    The ladder is a partition of what was KEPT, so it is printed beside the ledger rather
    than inside it: rungs do not remove each other's volume, they answer for different parcels.
    """
    w, tot = attr.articles, attr.articles.sum()
    logger.info("")
    logger.info("=== STEP 1 — THE ONE FILTER: WHICH DEPOT DELIVERED IT ===")
    logger.info("  %-44s %11s %9s", "", "articles", "share")
    logger.info("  %s", "-" * 68)
    for rung, lab in (("deliver_scan", "rung 1  last ZPT_DELIVER at one of our depots"),
                      ("accept_scan", f"rung 2  last {reduction.ACCEPT_EVENT} at one of ours"),
                      ("plan", "rung 3  the plan (contractor base explains it)"),
                      ("unscanned", "REMOVED  no depot scan on either event")):
        m = attr.pdc_from == rung
        logger.info("  %-44s %11s %8.2f%%", lab, f"{int(w[m].sum()):,}", pct(w[m].sum(), tot))
    drop = attr.unscanned
    if drop.any():
        logger.info("  the removed volume, by the depot it was PLANNED for:")
        by = w[drop].groupby(attr.pdc_term[drop]).sum().sort_values(ascending=False)
        for k, v in by.items():
            logger.info("      %-28s %9s  %6.2f%% of that depot's planned volume",
                        reduction.depot_label(k), f"{int(v):,}",
                        pct(v, w[attr.pdc_term == k].sum()))


def log_labelling(p):
    """The steps that label but never remove -- where the volume WENT, not what was lost.

    Read this instead of assuming a step dropped freight: every number below is still in the
    published reduction.
    """
    w, tot = p.articles, p.articles.sum()

    def block(title, series, order=None, note=""):
        logger.info("")
        logger.info("  %s%s", title, note)
        v = w.groupby(series).sum()
        for k in (order or v.sort_values(ascending=False).index):
            if k in v.index:
                logger.info("      %-34s %10s  %6.2f%%", str(k), f"{int(v[k]):,}", pct(v[k], tot))

    logger.info("")
    logger.info("=== STEPS THAT LABEL, NOT FILTER (all of this volume is IN the final table) ===")
    block("step 0d  itinerary — why a parcel has no path", p.path_why,
          note="  (path = it has one)")
    block("step 1   source — where it was lodged", p.lodge_band,
          ["INT", "METRO", "REGION", "UNKNOWN"],
          note="   UNKNOWN is what PART 2's cohort() drops")
    block("step 2   sort — Melbourne machine sorts", p.nrounds.clip(upper=3).map(
        {0: "0 sorts", 1: "1 sort", 2: "2 sorts", 3: "3+ sorts"}),
        ["0 sorts", "1 sort", "2 sorts", "3+ sorts"])
    block("step 3   delivery (the sink) — did it sleep at the delivering depot",
          p.kept_on_site.map({True: "kept at depot overnight", False: "moved through"}))


# ------------------------------------------------------- one building, several names

def alias_reason(name, winner):
    """WHY two scan names were folded onto one building. Reconstructed from the fork's own
    rules, so a new rule shows up here as `folded transitively` rather than as a wrong claim."""
    if reduction.VAN_HOME.get(name) == winner:
        return "van arm of this depot"
    if (name, winner) in reduction.MERGE or (winner, name) in reduction.MERGE:
        return "told, not derived (MERGE)"
    if reduction.fold_key(name) == reduction.fold_key(winner):
        return "PARCEL DELIVERY / PDC suffix"
    if reduction.sort_key(name) and reduction.sort_key(name) == reduction.sort_key(winner):
        return "same sort-site code"
    if name in reduction.VAN_HOME:
        return "van arm, via the depot's other name"
    return "folded transitively"


def facility_aliases(scans):
    """One row per scan name that is NOT the name its building ended up carrying.

    ALIAS is learned at build time off article volume -- the busiest name wins -- so this
    table is the audit trail for a name change that would otherwise be invisible.
    """
    ev = scans.groupby("Event_facility_name").size()
    cons = scans.groupby("Event_facility_name").Consignment_ID.nunique()
    members = {}
    for name, winner in reduction.ALIAS.items():
        members.setdefault(winner, []).append(name)

    rows = []
    for name, winner in sorted(reduction.ALIAS.items()):
        whole = int(ev.get(winner, 0) + sum(ev.get(n, 0) for n in members[winner]))
        rows.append({"facility_name": name, "folds_onto": winner,
                     "model_node": reduction.model_node(winner),
                     "why": alias_reason(name, winner),
                     "events": int(ev.get(name, 0)),
                     "consignments": int(cons.get(name, 0)),
                     "building_events": whole,
                     "share_of_building_pct": pct(ev.get(name, 0), whole)})
    out = pd.DataFrame(rows)
    if out.empty:
        return out.set_index(pd.Index([], name="facility_name"))
    return out.set_index("facility_name").sort_values("events", ascending=False)


def facility_by_building(scans):
    """The census rolled up onto BUILDINGS, so a site with three names is counted once.

    Wip_C02_FacilityCensus counts names; this counts buildings. Bayswater reads as two
    middling sites in the census and as one large one here, and only the second is true.
    """
    names = scans["Event_facility_name"].dropna().unique()
    canon = {n: reduction.alias(n) for n in names}
    b = scans.assign(building=scans["Event_facility_name"].map(canon))
    g = b.groupby("building")
    out = pd.DataFrame({"events": g.size(), "consignments": g["Consignment_ID"].nunique()})
    per = g["Event_facility_name"].unique()
    out["n_names"] = [len(v) for v in per]
    out["names"] = [" | ".join(sorted(v)) for v in per]
    out["model_node"] = [reduction.model_node(n) for n in out.index]
    out["modelled"] = out["model_node"].notna()
    out["events_pct"] = out["events"].map(lambda v: pct(v, out["events"].sum()))
    out.index.name = "building"
    return out.sort_values("events", ascending=False)


def log_alias_summary(buildings, aliases, p):
    """Every multi-named building, with each of its names and what that name is.

    This is the answer to "the same site appears under several names": not a warning, a list.
    A name absent from it is a name the reduction treats as its own building.
    """
    multi = buildings[buildings.n_names > 1].sort_values("events", ascending=False)
    logger.info("")
    logger.info("=== ONE BUILDING, SEVERAL NAMES ===")
    logger.info("  %d of the %s facility names in the scans are a second (or third) name for",
                len(reduction.ALIAS), f"{len(buildings) + len(reduction.ALIAS):,}")
    logger.info("  a building already listed. %d buildings answer to more than one name.",
                len(multi))
    logger.info("  The name carrying the most ARTICLES ON THE PATH wins and the others fold onto")
    logger.info("  it; the reduction, the model nodes and every table below use the winner. The")
    logger.info("  column below is EVENTS, a different count, so the winner is not always the")
    logger.info("  largest row here — Sunshine West keeps the PDC name on fewer scan events.")
    for bld, r in multi.iterrows():
        # `model_node` is None for a building the model does not carry, and pandas 3 stores
        # that as NaN in a str column -- which is TRUTHY, so `or` would print "nan".
        node = r.model_node if isinstance(r.model_node, str) else "(not modelled)"
        logger.info("")
        logger.info("  %-34s %-20s %d names  %11s events  %s", bld,
                    node, int(r.n_names), f"{int(r.events):,}",
                    "MODELLED" if r.modelled else "")
        kids = aliases[aliases.folds_onto == bld]
        own = int(r.events - kids.events.sum())
        logger.info("      %-34s %11s  %6.1f%%  %s", bld, f"{own:,}",
                    pct(own, r.events), "(the name kept)")
        for nm, k in kids.sort_values("events", ascending=False).iterrows():
            logger.info("      %-34s %11s  %6.1f%%  %s", nm, f"{int(k.events):,}",
                        pct(k.events, r.events), k.why)
    merged = p.articles[p.path_merged > 0].sum()
    logger.info("")
    logger.info("  %s articles (%.2f%%) were scanned under two names of ONE building on their",
                f"{int(merged):,}", pct(merged, p.articles.sum()))
    logger.info("  own journey — without the fold each of those reads as a second building, and")
    logger.info("  the model would book a sort and a linehaul that never happened.")


# ------------------------------------------------------------------ the six report blocks

def event_distribution(scans):
    """Events by type, and how many scans of that type an average consignment collects."""
    g = scans.groupby("Event_type")
    out = pd.DataFrame({"events": g.size(),
                        "consignments": g["Consignment_ID"].nunique()})
    out["scans_per_consignment"] = out["events"] / out["consignments"]
    out["events_pct"] = out["events"].map(lambda v: pct(v, out["events"].sum()))
    # share of ALL consignments that ever see this event type, not just those that do
    total_cons = scans["Consignment_ID"].nunique()
    out["consignment_coverage_pct"] = out["consignments"].map(lambda v: pct(v, total_cons))
    out.index.name = "event_type"
    return out.sort_values("events", ascending=False)


def product_breakdowns(p):
    p = p.assign(origin_band=p["source_band"].map(ORIGIN_BAND).fillna("Unknown"))
    return {
        "Wip_C02_ProductByOrigin": share_table(p, "origin_band", "articles", "origin_band"),
        "Wip_C02_ProductByType": share_table(p, "Product_type", "articles", "product_type"),
        "Wip_C02_ProductByPdc": share_table(p, "pdc", "articles", "pdc"),
    }


def facility_census(scans):
    """Every facility the scans name, with whether the model carries it."""
    g = scans.groupby("Event_facility_name")
    out = pd.DataFrame({"events": g.size(),
                        "consignments": g["Consignment_ID"].nunique()})
    out["sort_site"] = [reduction.SORT_SITE.get(n) for n in out.index]
    out["term_pdc"] = [reduction.TERM_PDC.get(n) for n in out.index]
    out["lodge_pud"] = [reduction.LODGE_PUD.get(n) for n in out.index]
    out["modelled"] = out[["sort_site", "term_pdc", "lodge_pud"]].notna().any(axis=1)
    # which BUILDING this name is. A census row is a NAME, and a building can own several --
    # so a row's volume is not the site's volume unless `is_alias` is False and no other name
    # folds onto it. Wip_C02_FacilityByBuilding is the same census counted per building.
    out["canonical_name"] = [reduction.alias(n) for n in out.index]
    out["model_node"] = [reduction.model_node(n) for n in out["canonical_name"]]
    out["is_alias"] = out["canonical_name"] != out.index
    out["events_pct"] = out["events"].map(lambda v: pct(v, out["events"].sum()))
    out.index.name = "facility_name"
    return out.sort_values("events", ascending=False)


def sortation_counts(p):
    out = share_table(p, "nrounds", "articles", "sortations")
    return out


def kept_on_depot(p, key, name):
    """The overnight stage, split by where the freight was LODGED.

    `kept_on_site` means the parcel SLEPT at the depot that delivered it -- it is the final
    stay, not the first touch. It does NOT mean the depot both collected and delivered it:
    most of it arrived from a hub or interstate and merely stopped overnight. Sunshine West
    is 46% of the whole stage, and only 1,406 of its 12,440 articles were lodged there.

    So the band split is published beside the total, and `same_depot_end_to_end` -- lodged
    AND delivered by the one depot -- gets its own column, because that is the number people
    mean when they say "kept at the depot".
    """
    kept = p[p["kept_on_site"]]
    out = share_table(kept, key, "articles", name)
    base = p.groupby(key)["articles"].sum()
    out["kept_share_of_category_pct"] = [pct(out.loc[i, "articles"], base.get(i, 0))
                                         for i in out.index]
    # where that kept volume was lodged -- INT/METRO/REGION
    bands = kept.groupby([key, "lodge_band"])["articles"].sum().unstack(fill_value=0)
    for b in ("INT", "METRO", "REGION"):
        out[f"lodged_{b}"] = bands[b].reindex(out.index, fill_value=0) if b in bands else 0
    # NOT a subset of the row: this counts ALL volume the depot both collected and delivered,
    # whereas the row counts what SLEPT there. A parcel lodged and delivered the same day is
    # in this column and not in the row, so the column can exceed it -- Melbourne North does.
    same = p[p["same_depot_end_to_end"]].groupby(key)["articles"].sum()
    out["same_depot_e2e_all_volume"] = same.reindex(out.index, fill_value=0).astype("int64")
    return out


def lane_thresholds(p, thresholds):
    """What FOLD_MIN_ARTICLES buys: lanes kept vs volume they carry."""
    demand, recv_entry, legs, deliver_rows, vic_xd = reduction.derive_stages(
        p.set_index("Consignment_ID"))
    total = sum(a for *_, a in ((f, c, e, d, a) for f, c, e, d, a in legs))
    rows = []
    for t in thresholds:
        kept = [(f, c, e, d, a) for f, c, e, d, a in legs if a >= t]
        vol = sum(a for *_, a in kept)
        rows.append({"threshold": t, "lanes": len(kept),
                     "lanes_pct": pct(len(kept), len(legs)),
                     "articles": vol, "articles_pct": pct(vol, total),
                     "lanes_dropped": len(legs) - len(kept),
                     "articles_dropped": total - vol})
    out = pd.DataFrame(rows).set_index("threshold")
    logger.info("  lane ledger: %s raw lanes carrying %s articles", f"{len(legs):,}", f"{total:,}")
    return out


# ------------------------------------------------------------------------------- pipeline

def main():
    logger.info("Starting scan analysis (PART 1)")
    project = Project.connect_to(PROJECT_NAME)
    sandbox = project.get_sandbox()

    # Double quotes preserve the table's exact case in the Starburst sandbox.
    raw = sandbox.read_sql(f'SELECT * FROM "{SOURCE_TABLE}"')
    logger.info("Read %s rows from %s", f"{len(raw):,}", SOURCE_TABLE)

    scans = reduction.set_scans(raw)
    log_logic()

    # The identity table BEFORE the depot ladder drops anything -- build_paths() computes it
    # again internally and keeps only what it can place, so the population that was removed is
    # not recoverable from its output. A few groupbys over the scans; seconds, not minutes.
    identity = reduction.consignment_identity(scans)

    # build_paths(), not load_paths(): there is no cache on the first run and we do not want
    # one -- Wip_C02_ConsignmentPaths is the artefact PART 2 reads.
    p = reduction.build_paths()
    logger.info("Reduction: %s consignments, %s articles",
                f"{len(p):,}", f"{int(p.articles.sum()):,}")
    tables = {}

    # 0 — where the volume went, step by step
    ledger = reduction_ledger(scans, identity, p)
    tables["Wip_C02_ReductionLedger"] = ledger
    log_frame(ledger, "REDUCTION LEDGER (step by step, from the clean table)")
    shown = {"Wip_C02_ReductionLedger"}       # logged here, in context; not again in the dump
    log_depot_ladder(identity)
    log_labelling(p)

    # 1 — event distribution
    tables["Wip_C02_EventDistribution"] = event_distribution(scans)

    # 2 — product breakdowns
    tables.update(product_breakdowns(p))

    # 3 — facility touches
    modelled = share_table(p, "path_n", "articles", "modelled_facilities_touched")
    tables["Wip_C02_FacilityTouches"] = modelled
    by_prod = p.groupby(["Product_type", "path_n"])["articles"].sum().unstack(fill_value=0)
    tables["Wip_C02_FacilityTouchesByProduct"] = by_prod
    tables["Wip_C02_FacilityTouchesByProductPct"] = (
        by_prod.div(by_prod.sum(axis=1), axis=0) * 100)
    tables["Wip_C02_FacilityCensus"] = facility_census(scans)
    aliases = facility_aliases(scans)
    buildings = facility_by_building(scans)
    tables["Wip_C02_FacilityAliases"] = aliases
    tables["Wip_C02_FacilityByBuilding"] = buildings
    log_alias_summary(buildings, aliases, p)

    # 4 — sortation
    tables["Wip_C02_SortationCounts"] = sortation_counts(p)

    # 5 — kept on depot
    tables["Wip_C02_KeptByProduct"] = kept_on_depot(p, "Product_type", "product_type")
    tables["Wip_C02_KeptByPdc"] = kept_on_depot(p, "pdc", "pdc")
    logger.info("")
    logger.info("  NOTE on the Kept tables: rows count what SLEPT at the delivering depot.")
    logger.info("       `same_depot_e2e_all_volume` is a different measure over ALL volume —")
    logger.info("       lodged AND delivered by that depot — so it can exceed the row.")

    # 6 — lanes vs threshold
    tables["Wip_C02_LaneThresholds"] = lane_thresholds(p, LANE_THRESHOLDS)

    # ---- headline numbers that the model reads directly ----
    art = p["articles"].sum()
    no_sort = p.loc[p["nrounds"] == 0, "articles"].sum()
    logger.info("")
    logger.info("=== HEADLINES ===")
    _in_c, _in_a = len(identity), int(identity.articles.sum())
    logger.info("  in  (%-22s) %12s consignments  %12s articles",
                SOURCE_TABLE, f"{_in_c:,}", f"{_in_a:,}")
    logger.info("  out (reduction)              %12s consignments  %12s articles",
                f"{len(p):,}", f"{int(art):,}")
    logger.info("  removed (no depot scan)      %12s consignments  %12s articles  %5.2f%%",
                f"{_in_c - len(p):,}", f"{_in_a - int(art):,}", pct(_in_a - art, _in_a))
    _unk = p.loc[p.origin == "UNKNOWN", "articles"].sum()
    logger.info("  PART 2 will drop origin=UNKNOWN %9s articles  %5.2f%% (still in this table)",
                f"{int(_unk):,}", pct(_unk, art))
    logger.info("")
    logger.info("  articles                       %12s", f"{int(art):,}")
    logger.info("  no sortation (manual ceiling)  %12s  %5.2f%%", f"{int(no_sort):,}",
                pct(no_sort, art))
    logger.info("  kept at depot                  %12s  %5.2f%%",
                f"{int(p.loc[p.kept_on_site, 'articles'].sum()):,}",
                pct(p.loc[p.kept_on_site, "articles"].sum(), art))
    logger.info("  sorted more than once          %12s  %5.2f%%",
                f"{int(p.loc[p.nrounds > 1, 'articles'].sum()):,}",
                pct(p.loc[p.nrounds > 1, "articles"].sum(), art))

    # THREE different numbers get called "kept". They are printed together so the Kept tables
    # and the Origin table cannot be read as disagreeing when they are answering different
    # questions. Wip_C02_KeptBy* use the first; Wip_C02_ProductByOrigin uses the third.
    logger.info("")
    logger.info("  'kept' means three different things — all three, reconciled:")
    stage = p.loc[p.kept_on_site, "articles"].sum()
    logger.info("    slept at the delivering depot (kept_on_site)   %10s  %5.2f%%",
                f"{int(stage):,}", pct(stage, art))
    for b in ("METRO", "INT", "REGION"):
        v = p.loc[p.kept_on_site & (p.lodge_band == b), "articles"].sum()
        logger.info("        of which lodged %-6s                    %10s  %5.1f%% of the stage",
                    b, f"{int(v):,}", pct(v, stage))
    same = p.loc[p.same_depot_end_to_end, "articles"].sum()
    logger.info("    lodged AND delivered by one depot              %10s  %5.2f%%",
                f"{int(same):,}", pct(same, art))
    band = p.loc[p.source_band == "KEPT_METRO", "articles"].sum()
    logger.info("    KEPT_METRO source band (the model's stage)     %10s  %5.2f%%",
                f"{int(band):,}", pct(band, art))

    for name, df in tables.items():
        if name not in shown:
            log_frame(df, name, limit=LOG_HEAD.get(name))

    logger.info("")
    for name, df in tables.items():
        publish(sandbox, df, name)
        logger.info("  wrote %-38s %5d rows", name, len(df))
    publish(sandbox, reduction.pack_paths(p).set_index("Consignment_ID"), PATHS_TABLE)
    logger.info("  wrote %-38s %5d rows", PATHS_TABLE, len(p))
    logger.info("Done")


if __name__ == "__main__":
    main()
