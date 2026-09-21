"""Rebuild the cleaned Melbourne scan table and write it to DataStar.

Reproduces `all_scan_for_melbourne_pdc_20052026.csv` byte-for-byte from the raw scan
extract. Reports, in this order:

  * the cleaning rules themselves, in the order they are applied;
  * a stage ledger -- events / consignments / articles surviving after each rule, and
    what that rule removed on its own;
  * an in/out headline for the run as a whole;
  * why volume was removed, cross-tabbed by product type and by modelled PDC (the
    columns are the rules, so the tables read step by step);
  * before/after volume by product type and by modelled PDC.

Every count is given three ways: scan events, consignments, and articles.

Order matters: the EXCLUDE_FACILITY_SET filter MUST run before the facility remaps.
"MULGRAVE PDC" in that set means the natively-Mulgrave consignments (all of which have a
null Contract_ID and none of which belong in the clean table). The Mulgrave volume that
DOES belong arrives later, via the V03974 remap of Oakleigh South -- remap first and this
exclusion deletes all of it.
"""

import logging
import sys

import numpy as np
import pandas as pd
import shapely

from datastar import Project

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

PROJECT_NAME = "Temp_FY26_Melbourne"
OUTPUT_TABLE = "Wip_C02_ScanClean"

RAW_PATH = "/projects/My Files/DataStar/Raw Inputs/melbourne/melbourne-delivery-volume-all-scan-events.csv"
AUS_STATE_BOUNDARY = "/projects/My Files/DataStar/Raw Inputs/melbourne/aus_state_boundaries.csv"

EXCLUDE_FACILITY_SET = {"WESTERN DC", "MULGRAVE PDC", "STARTRACK MULGRAVE"}
CLEAN_PRODUCT_SET = {
    "eParcel Express", "eParcel Standard", "eParcel Returns", "rParcel Post Plus", "Metro Next Day",
}
CLEAN_FACILITY_SET = {
    "ABBOTSFORD PARCEL DELIVERY", "BAYSWATER PDC", "DANDENONG SOUTH PDC", "DAREBIN PDC",
    "MELBOURNE NORTH PDC", "MOUNT WAVERLEY PARCEL DELIVERY", "MULGRAVE PDC",
    "OAKLEIGH SOUTH PDC", "PAKENHAM PARCEL DELIVERY", "SUNSHINE WEST PARCEL DELIVERY",
    "TULLAMARINE PDC",
}
CONSIDER_COLS = [
    "Consignment_ID", "Event_seq", "Event_type", "Event_sub_type", "Event_stage",
    "Event_date", "Event_local_time", "Event_facility_name", "Terminating_facility_name",
    "Product_type", "Article_count", "STE_NAME21",
]
OTHER = "(other, not modelled)"
BLANK = "(no value)"
KEPT = "kept"
# numbered to match the step labels in the stage ledger
R_NODEL = "1a no delivery coord"
R_EXCL = "1b excluded facility"
R_PROD = "1c out-of-scope prod"
R_PDC = "3 not a modelled PDC"
R_GEO = "4 event not placed"
# consignment-level reasons first, then the one event-level reason
REASON_ORDER = (KEPT, R_NODEL, R_EXCL, R_PROD, R_PDC, R_GEO)
CONS_REASONS = (R_NODEL, R_EXCL, R_PROD, R_PDC)
METRICS = ("events", "consignments", "articles")


# ----------------------------------------------------------------- volume reporting

def volumes(df):
    """(events, consignments, articles) for a frame of scan events.

    Article_count repeats on EVERY scan of a consignment, so summing it down the rows
    overcounts by ~20x on this extract. It is constant within a consignment, so count it
    once per consignment.
    """
    per_cons = df.drop_duplicates("Consignment_ID")
    return len(df), len(per_cons), int(per_cons["Article_count"].sum())


def breakdown(df, key):
    """events / consignments / articles per category of `key`."""
    per_cons = df.drop_duplicates("Consignment_ID")
    grp, grp_cons = df.groupby(key, dropna=False), per_cons.groupby(key, dropna=False)
    out = pd.DataFrame({
        "events": grp.size(),
        "consignments": grp_cons.size(),
        "articles": grp_cons["Article_count"].sum(),
    })
    return out.fillna(0).astype("int64")


def log_table(title, before, after, metric):
    """Before/after table for one metric, with share-of-total and retention."""
    idx = before.index.union(after.index)
    b = before[metric].reindex(idx, fill_value=0)
    a = after[metric].reindex(idx, fill_value=0)
    # surviving categories first (largest after-volume), then wholly-dropped ones
    order = pd.DataFrame({"live": (a > 0).astype(int), "a": a, "b": b}).sort_values(
        ["live", "a", "b"], ascending=False
    ).index

    logger.info("")
    logger.info("%s -- %s", title, metric)
    logger.info("  %-32s %12s %8s %12s %8s %8s", "", "before", "share", "after", "share", "kept")
    logger.info("  %s", "-" * 84)
    for k in order:
        label = BLANK if pd.isna(k) else str(k)
        kept = f"{100 * a[k] / b[k]:7.1f}%" if b[k] else "      -"
        logger.info(
            "  %-32s %12s %7.1f%% %12s %7.1f%% %s",
            label[:32], f"{b[k]:,}", 100 * b[k] / b.sum() if b.sum() else 0,
            f"{a[k]:,}", 100 * a[k] / a.sum() if a.sum() else 0, kept,
        )
    logger.info(
        "  %-32s %12s %7.1f%% %12s %7.1f%% %7.1f%%",
        "TOTAL", f"{b.sum():,}", 100.0, f"{a.sum():,}", 100.0,
        100 * a.sum() / b.sum() if b.sum() else 0,
    )


def removal_table(title, metric, labels, reason, weight):
    """One cross-tab: rows are `labels`, columns are why those rows were removed."""
    tab = weight.groupby([labels, reason], dropna=False).sum().unstack(fill_value=0)
    cols = [r for r in REASON_ORDER if r in tab.columns and r != KEPT]
    tab = tab.reindex(columns=[KEPT] + cols, fill_value=0)
    total = tab.sum(axis=1)
    removed = tab[cols].sum(axis=1)

    logger.info("")
    logger.info("%s -- %s", title, metric)
    logger.info("  %-32s %12s %9s   %s", "", "total", "removed",
                "  ".join(f"{c[:20]:>20}" for c in cols))
    logger.info("  %s", "-" * (58 + 22 * len(cols)))
    for k in total.sort_values(ascending=False).index:
        label = BLANK if pd.isna(k) else str(k)
        cells = "  ".join(
            f"{int(tab.loc[k, c]):>12,}{tab.loc[k, c] / total[k]:7.1%}" if total[k]
            else f"{int(tab.loc[k, c]):>12,}      -" for c in cols)
        logger.info("  %-32s %12s %8.1f%%   %s", label[:32], f"{int(total[k]):,}",
                    100 * removed[k] / total[k] if total[k] else 0, cells)
    cells = "  ".join(f"{int(tab[c].sum()):>12,}{tab[c].sum() / total.sum():7.1%}" for c in cols)
    logger.info("  %-32s %12s %8.1f%%   %s", "TOTAL", f"{int(total.sum()):,}",
                100 * removed.sum() / total.sum() if total.sum() else 0, cells)


def log_logic():
    """The rules, in the order the pipeline applies them."""
    logger.info("")
    logger.info("=== CLEANING LOGIC ===")
    logger.info("  Six rules, applied in this order. The order is load-bearing: the excluded-")
    logger.info("  facility filter (1b) MUST run before the remaps (2) -- see module docstring.")
    logger.info("")
    logger.info("  1a  drop events with no delivery coordinate (Del_Lat or Del_Long null)")
    logger.info("  1b  drop events terminating at an excluded facility: %s",
                ", ".join(sorted(EXCLUDE_FACILITY_SET)))
    logger.info("  1c  keep only in-scope products (%d): %s",
                len(CLEAN_PRODUCT_SET), ", ".join(sorted(CLEAN_PRODUCT_SET)))
    logger.info("  2   remap facility aliases:")
    logger.info("        HOLLOWAY DR PARCEL OPERATIONS               -> BAYSWATER PDC")
    logger.info("        OAKLEIGH SOUTH PDC with Contract_ID V03974  -> MULGRAVE PDC")
    logger.info("  3   keep only the %d modelled PDCs: %s",
                len(CLEAN_FACILITY_SET), ", ".join(sorted(CLEAN_FACILITY_SET)))
    logger.info("  4   drop events whose scan coordinate falls outside every Australian state")
    logger.info("")
    logger.info("  1a-3 are CONSIGNMENT-level: the whole consignment goes or stays, because the")
    logger.info("       columns they test are constant within a consignment.")
    logger.info("  4    is EVENT-level: a consignment can lose some scans and still survive, so")
    logger.info("       it only counts as removed by rule 4 if it lost ALL of its events.")


def stage(stages, label, df):
    """Record one point in the ledger. Returns the frame so it can be chained."""
    stages.append((label,) + volumes(df))
    return df


def log_ledger(stages, notes=()):
    """Running volume after each rule, and what that rule removed on its own."""
    logger.info("")
    logger.info("=== STAGE LEDGER (what each rule removes, step by step) ===")
    logger.info("  %-42s %11s %13s %11s   %11s %13s %11s %8s",
                "", "events", "consignments", "articles",
                "-events", "-consignments", "-articles", "of raw")
    logger.info("  %s", "-" * 125)
    raw_art = stages[0][3] if stages else 0
    prev = None
    for label, ev, cons, art in stages:
        d = ("", "", "") if prev is None else tuple(
            f"-{p - c:,}" if p != c else "0" for p, c in zip(prev, (ev, cons, art)))
        logger.info("  %-42s %11s %13s %11s   %11s %13s %11s %7.1f%%",
                    label, f"{ev:,}", f"{cons:,}", f"{art:,}", d[0], d[1], d[2],
                    100 * art / raw_art if raw_art else 0)
        prev = (ev, cons, art)
    for note in notes:
        logger.info("  %s", note)


def log_inout(stages):
    """Headline in/out: what went into the clean, what came out, what was removed."""
    _, *first = stages[0]
    _, *last = stages[-1]
    logger.info("")
    logger.info("=== IN / OUT ===")
    logger.info("  %-14s %13s %13s %13s %9s", "", "in (raw)", "out (clean)", "removed", "kept")
    logger.info("  %s", "-" * 68)
    for metric, b, a in zip(METRICS, first, last):
        logger.info("  %-14s %13s %13s %13s %8.1f%%", metric, f"{b:,}", f"{a:,}",
                    f"{b - a:,}", 100 * a / b if b else 0)


def modelled_label(df):
    """The PDC label a row WOULD carry after remapping, so the before and after tables
    share one label space and 'kept' is a real retention rate.

    Facilities in EXCLUDE_FACILITY_SET are tagged instead of remapped: the natively-
    Mulgrave volume (dropped) must never be pooled with the Oakleigh South volume that is
    remapped INTO Mulgrave (kept). Everything unmodelled collapses to a single row.
    """
    name = df["Terminating_facility_name"]
    excluded = name.isin(EXCLUDE_FACILITY_SET)
    label = name.mask(name == "HOLLOWAY DR PARCEL OPERATIONS", "BAYSWATER PDC")
    v03974 = (df["Contract_ID"] == "V03974") & (label == "OAKLEIGH SOUTH PDC")
    label = label.mask(v03974, "MULGRAVE PDC")
    label = label.where(label.isin(CLEAN_FACILITY_SET), OTHER)
    return label.mask(excluded, name.astype("object") + " (excluded)")


# ------------------------------------------------------------------------- pipeline

def main():
    logger.info("Starting ScanClean reconstruction")

    project = Project.connect_to(PROJECT_NAME)
    sandbox = project.get_sandbox()
    logger.info("Connected to sandbox for %s", PROJECT_NAME)

    raw_df = pd.read_csv(RAW_PATH, low_memory=False, dtype={"Consignment_ID": "string"})
    states = pd.read_csv(AUS_STATE_BOUNDARY)
    states["geometry"] = shapely.from_wkt(states["geometry"])
    logger.info("Read %s raw scan events and %d state polygons", f"{len(raw_df):,}", len(states))

    # Snapshot the raw picture before anything is filtered or remapped.
    before_product = breakdown(raw_df, "Product_type")
    before_pdc = breakdown(raw_df.assign(_pdc=modelled_label(raw_df)), "_pdc")

    # Per-event removal reason, filled in as each filter runs. The first four reasons are
    # consignment-level (every row of a consignment shares the outcome); only the geocode
    # test is per-event, so a consignment can lose some events and still survive.
    # `rep` keeps the ORIGINAL row index and full row set; raw_df shrinks under it.
    rep = pd.DataFrame({
        "prod": raw_df["Product_type"],
        "pdc": modelled_label(raw_df),
        "cons": raw_df["Consignment_ID"],
        "art": raw_df["Article_count"],
    })
    reason = pd.Series(KEPT, index=rep.index, dtype=object)

    no_del = raw_df["Del_Lat"].isna() | raw_df["Del_Long"].isna()
    excluded = ~no_del & raw_df["Terminating_facility_name"].isin(EXCLUDE_FACILITY_SET)
    bad_prod = ~no_del & ~excluded & ~raw_df["Product_type"].isin(CLEAN_PRODUCT_SET)
    reason[no_del] = R_NODEL
    reason[excluded] = R_EXCL
    reason[bad_prod] = R_PROD

    log_logic()

    # The ledger walks rule 1 in its three parts, so each filter gets its own line. The
    # masks are cumulative and mutually exclusive, exactly as `reason` above assigns them.
    stages = []
    stage(stages, "0.  raw extract", raw_df)
    stage(stages, "1a. + has a delivery coordinate", raw_df[~no_del])
    stage(stages, "1b. + not an excluded facility", raw_df[~no_del & ~excluded])
    stage(stages, "1c. + an in-scope product", raw_df[~no_del & ~excluded & ~bad_prod])

    # ---- row filter (must precede the remaps) ----
    raw_df = raw_df[
        raw_df["Del_Lat"].notna()
        & raw_df["Del_Long"].notna()
        & ~raw_df["Terminating_facility_name"].isin(EXCLUDE_FACILITY_SET)
        & raw_df["Product_type"].isin(CLEAN_PRODUCT_SET)
    ]  # index deliberately NOT reset -- removal_reason attributes drops by original row

    # ---- facility remaps ----
    holloway = raw_df["Terminating_facility_name"] == "HOLLOWAY DR PARCEL OPERATIONS"
    raw_df["Terminating_facility_name"] = raw_df["Terminating_facility_name"].mask(
        holloway, "BAYSWATER PDC"
    )
    v03974 = ((raw_df["Contract_ID"] == "V03974")
              & (raw_df["Terminating_facility_name"] == "OAKLEIGH SOUTH PDC"))
    raw_df["Terminating_facility_name"] = raw_df["Terminating_facility_name"].mask(v03974, "MULGRAVE PDC")
    # a ledger row of its own so the rules read 0, 1a-c, 2, 3, 4; it removes nothing
    stage(stages, "2.  remap facility aliases (renames only)", raw_df)
    remap_note = (
        f"rule 2 renamed Holloway Dr -> Bayswater on {int(holloway.sum()):,} events and "
        f"V03974 @ Oakleigh South -> Mulgrave on {int(v03974.sum()):,} events"
    )

    cons_ok = (raw_df["Terminating_facility_name"].isin(CLEAN_FACILITY_SET)
               & raw_df["Product_type"].isin(CLEAN_PRODUCT_SET))
    reason[raw_df.index[~cons_ok]] = R_PDC
    stage(stages, "3.  + terminates at a modelled PDC", raw_df[cons_ok])

    # ---- state of each scan event, on distinct coordinates only ----
    codes, uniq = pd.factorize(
        pd.MultiIndex.from_arrays([raw_df["Event_Lat"].values, raw_df["Event_Long"].values]),
        use_na_sentinel=False,
    )
    u_lat = np.fromiter((u[0] for u in uniq), float, len(uniq))
    u_lon = np.fromiter((u[1] for u in uniq), float, len(uniq))

    state_names = states["STE_NAME21"].to_numpy()
    tree = shapely.STRtree(states["geometry"].to_numpy())
    hit_pt, hit_poly = tree.query(shapely.points(u_lon, u_lat), predicate="intersects")
    # a coordinate exactly on a state border matches two polygons -- lowest index wins
    order = np.argsort(hit_poly, kind="mergesort")[::-1]
    u_state = np.full(len(uniq), None, dtype=object)
    u_state[hit_pt[order]] = state_names[hit_poly[order]]
    geo_note = (f"rule 4 tested {len(uniq):,} distinct coordinates; "
                f"{int(pd.isna(u_state).sum()):,} of them outside every state")

    joined = raw_df
    joined["STE_NAME21"] = u_state[codes]
    row_ok = joined["STE_NAME21"].notna()

    reconstructed = (
        joined.loc[row_ok & cons_ok, CONSIDER_COLS]
        .sort_values(["Consignment_ID", "Event_seq"], kind="mergesort")
        .reset_index(drop=True)
    )
    reason[raw_df.index[cons_ok & ~row_ok]] = R_GEO
    stage(stages, "4.  + event placed in a state (FINAL)", reconstructed)
    log_ledger(stages, [remap_note, geo_note])
    log_inout(stages)

    # ---- what was removed, and why ----
    # Events: every reason applies. Consignments/articles: a consignment carries one reason,
    # and it only counts as geocode-removed if it lost ALL of its events.
    ones = pd.Series(1, index=rep.index)
    logger.info("")
    logger.info("=== REMOVALS BY CATEGORY (columns are the rules, in the order they run) ===")
    removal_table("REMOVED BY PRODUCT TYPE", "events", rep["prod"], reason, ones)
    removal_table("REMOVED BY PDC", "events", rep["pdc"], reason, ones)

    survivors = set(reconstructed["Consignment_ID"].unique())
    first = ~rep["cons"].duplicated()
    cons_reason = reason.where(reason.isin(CONS_REASONS), KEPT)
    cons_reason = cons_reason.mask((cons_reason == KEPT) & ~rep["cons"].isin(survivors), R_GEO)
    for metric, weight in (("consignments", ones), ("articles", rep["art"])):
        removal_table("REMOVED BY PRODUCT TYPE", metric, rep["prod"][first],
                      cons_reason[first], weight[first])
        removal_table("REMOVED BY PDC", metric, rep["pdc"][first],
                      cons_reason[first], weight[first])

    # ---- before/after breakdowns ----
    after_product = breakdown(reconstructed, "Product_type")
    after_pdc = breakdown(reconstructed, "Terminating_facility_name")
    for metric in METRICS:
        log_table("BY PRODUCT TYPE", before_product, after_product, metric)
    logger.info("")
    logger.info("NOTE: both columns use the POST-remap label, so Bayswater's before-column")
    logger.info("      includes Holloway Dr and Mulgrave's is Oakleigh South's V03974 volume.")
    logger.info("      Rows tagged (excluded) are dropped wholesale by EXCLUDE_FACILITY_SET.")
    for metric in METRICS:
        log_table("BY MODELLED PDC", before_pdc, after_pdc, metric)

    logger.info("")
    logger.info("Writing %s rows to table %s", f"{len(reconstructed):,}", OUTPUT_TABLE)
    # The old table is cleared by the macro's "run SQL" task, which runs BEFORE this script:
    # the sandbox object itself has no way to execute SQL (read_table / write_table only).
    sandbox.write_table(reconstructed, OUTPUT_TABLE)
    logger.info("Done")


if __name__ == "__main__":
    main()
